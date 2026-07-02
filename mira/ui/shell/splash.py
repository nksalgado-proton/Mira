"""Startup splash — random exported photo with the bundled mark as fallback (spec/136).

Three halves:

* :func:`pick_random_exported_frame` — pure-ish picking logic over a
  gateway, time-boxed; returns a :class:`ChosenFrame` (with its
  :class:`~core.cut_overlay.FrameProvenance` pre-resolved) or ``None``.
* :func:`format_when_for_splash` / :func:`format_caption` — pure-logic
  composers that turn a ``FrameProvenance`` into the bottom caption
  ("October 2025 · Kathmandu, Nepal"). Strict on *when*: ``capture_
  time_corrected`` only — raw EXIF never leaks through (spec/136 §2a).
* :func:`decorate_splash_pixmap` / :func:`build_splash_pixmap` /
  :func:`show_startup_splash` / :func:`finish_startup_splash` — the
  Qt-side composition + lifecycle. The decoration is two text blocks
  baked into the pixmap under a soft scrim:

    - **Top-left, large:** the wordmark + loading cue (the §2a title).
    - **Bottom, small:** the photo's *when · where* caption.
      Omitted on the bundled-mark fallback (no source frame).

The splash covers the ~2 s ``MainWindow`` construction window so the
boot flicker (stray transient top-levels created during construction)
is no longer visible. The DISPLAY duration equals the build time — no
artificial minimum — we are *filling* existing dead time. The only
thing that must be fast is the image *sourcing*: time-boxed at
:data:`DEFAULT_DEADLINE_MS` so a missing proxy / slow disk never adds
perceptible startup latency. See spec/136.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, Sequence

log = logging.getLogger(__name__)

# Splash pixmap long-edge target — expressed in LOGICAL pixels so it
# stays visually consistent across DPRs. The runtime multiplies this by
# the active screen's devicePixelRatio to pick a physical-pixel decode
# target, then stamps the DPR on the pixmap so Qt paints it at the same
# logical size but with sharp physical pixels on HiDPI panels.
DEFAULT_SPLASH_EDGE = 720

# Wall-clock budget for the WHOLE sourcing step (pick + decode + scale).
# spec/136 §3 — the splash must never *add* perceptible startup latency.
DEFAULT_DEADLINE_MS = 250

# How many random closed events / exported frames to sample before
# giving up. The closed-event query is cheap (JSON index); the work
# is the per-event ``event.db`` open. 5 is enough to absorb a few
# empty / unreadable events without scanning the whole library.
MAX_EVENT_SAMPLES = 5
MAX_FRAME_SAMPLES = 5

# spec/136 §2a — the splash title text. The wordmark + loading cue
# painted top-left on every splash (photo path AND bundled fallback).
# Constant rather than computed so tests can pin it.
SPLASH_TITLE = "Mira by NKS starting…"

# Canvas size for the bundled-mark fallback (no source photo to paint
# on). Landscape-oriented dark surface that gives the title a sensible
# painting area. The photo path composes directly on the source pixmap
# and keeps its natural AR. (Since the mark also rides next to the
# title — see ``decorate_splash_pixmap`` — there is no separate
# centred mark to size here.)
_FALLBACK_CANVAS_W = 540
_FALLBACK_CANVAS_H = 360

# White frame painted around the splash perimeter — lifts the splash
# off the desktop and gives the photo a printed-photo feel. Pixel-
# aligned (no anti-aliased fractional stroke) so the frame reads as
# crisp at any pixmap size. Bumped from 4 → 10 logical px (Nelson
# 2026-07-01) so the frame reads as a real border rather than an
# easy-to-miss hairline.
_SPLASH_FRAME_PX = 10

_IMAGE_EXPORT_SUFFIXES = frozenset({
    ".jpg", ".jpeg", ".tif", ".tiff", ".png",
    ".bmp", ".webp", ".heic", ".heif",
})


def _screen_device_pixel_ratio() -> float:
    """Return the primary screen's devicePixelRatio, or ``1.0`` when Qt
    is not initialised (unit tests import this module without a
    ``QGuiApplication``). Callers use this to size the pixmap so it
    decodes at *physical* pixels on HiDPI panels — otherwise a 720 px
    splash renders from 720 real pixels stretched over 1440 physical
    pixels on a 200 % HiDPI display and reads soft."""
    try:
        from PyQt6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            return max(1.0, float(screen.devicePixelRatio()))
    except Exception:                                              # noqa: BLE001
        pass
    return 1.0


@dataclass(frozen=True)
class ChosenFrame:
    """A picked exported frame.

    ``proxy_path`` is the cached 2560-px JPEG when it exists (the fast
    path); ``None`` means the caller should draft-decode ``export_path``
    instead. ``provenance`` is the spec/134 resolver's
    :class:`~core.cut_overlay.FrameProvenance` for the source item —
    pre-resolved while the event gateway is open so
    :func:`build_splash_pixmap` doesn't need the gateway at decoration
    time. ``None`` when the provenance lookup failed (the caption is
    then omitted gracefully).
    """
    event_name: str
    export_path: Path
    proxy_path: Optional[Path]
    provenance: Optional[Any] = None         # core.cut_overlay.FrameProvenance


class _GatewayLike(Protocol):
    def list_events(self) -> Sequence[dict]: ...


def _default_open_event(event_root: Path):
    """Open an EventGateway directly — no on_close sync hook, no
    collections-library factory. The splash path is read-only; the
    umbrella's :meth:`Gateway.open_event` adds bookkeeping we don't
    want firing on every launch."""
    from mira.gateway.event_gateway import EventGateway
    return EventGateway.open(
        event_root / "event.db", event_root=event_root)


# ── Picker (Qt-free, deterministic with an rng) ──────────────────────


def pick_random_exported_frame(
    gateway: _GatewayLike,
    *,
    deadline_ms: int = DEFAULT_DEADLINE_MS,
    rng: Optional[random.Random] = None,
    monotonic: Callable[[], float] = time.monotonic,
    open_event: Callable[[Path], Any] = _default_open_event,
) -> Optional[ChosenFrame]:
    """Pick a random exported frame from a random closed event.

    Returns ``None`` for the empty/unavailable cases — no closed
    events, no exports anywhere we sampled, any error, or the deadline
    expiring. The caller falls back to the bundled mark on ``None``.

    Read-only: opens each candidate ``event.db`` for read and closes
    it promptly. Caps at :data:`MAX_EVENT_SAMPLES` closed events so a
    library with hundreds of empty closed events still returns in
    bounded time.
    """
    rng = rng or random.Random()
    start = monotonic()
    deadline_s = max(0.0, deadline_ms / 1000.0)

    def _budget_left() -> bool:
        return (monotonic() - start) < deadline_s

    if not _budget_left():
        return None
    try:
        rows = list(gateway.list_events())
    except Exception:                                              # noqa: BLE001
        log.exception("splash: list_events failed")
        return None

    closed = [
        r for r in rows
        if r.get("is_closed") and r.get("event_root") is not None
    ]
    if not closed:
        return None
    rng.shuffle(closed)
    for row in closed[:MAX_EVENT_SAMPLES]:
        if not _budget_left():
            return None
        chosen = _try_event(
            row, rng=rng, budget_left=_budget_left, open_event=open_event)
        if chosen is not None:
            return chosen
    return None


def _try_event(
    row: dict,
    *,
    rng: random.Random,
    budget_left: Callable[[], bool],
    open_event: Callable[[Path], Any],
) -> Optional[ChosenFrame]:
    event_root = row.get("event_root")
    if event_root is None:
        return None
    event_root = Path(event_root)
    db_path = event_root / "event.db"
    try:
        if not db_path.is_file():
            return None
    except OSError:
        return None
    try:
        eg = open_event(event_root)
    except Exception:                                              # noqa: BLE001
        log.exception("splash: open_event(%s) failed", event_root)
        return None
    try:
        if not budget_left():
            return None
        try:
            # spec/136 — only sample frames the user actually
            # DEVELOPED. A straight-through export of the untouched
            # baseline is technically in Exported Media/ but reads as
            # a non-choice on the splash; the strict twin
            # ``exported_edited_files`` narrows to rows off the
            # unedited baseline (look / crop / rotation / creative
            # filter set).
            exports = list(eg.exported_edited_files())
        except Exception:                                          # noqa: BLE001
            log.exception(
                "splash: exported_edited_files(%s) failed", event_root)
            return None
        if not exports:
            return None
        rng.shuffle(exports)
        for lineage in exports[:MAX_FRAME_SAMPLES]:
            if not budget_left():
                return None
            relpath = getattr(lineage, "export_relpath", None)
            if not relpath:
                continue
            export_path = event_root / relpath
            if export_path.suffix.lower() not in _IMAGE_EXPORT_SUFFIXES:
                continue
            try:
                if not export_path.is_file():
                    continue
            except OSError:
                continue
            proxy_path = _try_resolve_proxy(eg, event_root, lineage)
            provenance = _try_resolve_provenance(eg, relpath)
            return ChosenFrame(
                event_name=row.get("name") or "",
                export_path=export_path,
                proxy_path=proxy_path,
                provenance=provenance,
            )
        return None
    finally:
        try:
            eg.close()
        except Exception:                                          # noqa: BLE001
            pass


def _try_resolve_proxy(eg, event_root: Path, lineage) -> Optional[Path]:
    """Locate the cached 2560-px proxy of the lineage row's SOURCE item.

    ``None`` when the source item can't be resolved or no fresh proxy
    exists yet — the caller draft-decodes the export JPEG instead.
    """
    item = _lineage_source_item(eg, lineage)
    if item is None:
        return None
    sha256 = getattr(item, "sha256", None)
    origin_relpath = getattr(item, "origin_relpath", None)
    if not sha256 or not origin_relpath:
        return None
    source_path = event_root / origin_relpath
    try:
        if not source_path.is_file():
            return None
    except OSError:
        return None
    try:
        from core.photo_proxy_cache import resolve_proxy
        hit = resolve_proxy(event_root, sha256, source_path)
    except Exception:                                              # noqa: BLE001
        return None
    return hit.path if hit is not None else None


def _try_resolve_provenance(eg, export_relpath: str) -> Optional[Any]:
    """spec/134 — resolve the lineage row's
    :class:`~core.cut_overlay.FrameProvenance` while the event gateway
    is still open. Returns ``None`` on any failure; the caption then
    falls back to a date-only or empty form.
    """
    try:
        return eg.frame_provenance(export_relpath)
    except Exception:                                              # noqa: BLE001
        log.exception(
            "splash: frame_provenance(%s) failed", export_relpath)
        return None


def _lineage_source_item(eg, lineage):
    """The :class:`Item` behind ``lineage`` — direct for item-sourced
    exports, via the merged output item for bracket-sourced exports."""
    source_id = getattr(lineage, "source_item_id", None)
    if source_id:
        try:
            return eg.item(source_id)
        except Exception:                                          # noqa: BLE001
            return None
    bracket_id = getattr(lineage, "source_bracket_id", None)
    if not bracket_id:
        return None
    try:
        from mira.store import models as m
        rows = eg.store.query_raw(
            m.StackBracket,
            "SELECT * FROM stack_bracket WHERE bracket_id = ?",
            (bracket_id,),
        )
    except Exception:                                              # noqa: BLE001
        return None
    if not rows or not getattr(rows[0], "output_item_id", None):
        return None
    try:
        return eg.item(rows[0].output_item_id)
    except Exception:                                              # noqa: BLE001
        return None


# ── Caption composition (Qt-free) ────────────────────────────────────


def format_when_for_splash(when: Optional[str]) -> Optional[str]:
    """Format the *when* half of the splash caption as ``"Month YYYY"``.

    spec/136 §2a — uses ``capture_time_corrected`` (the TZ-/clock-
    corrected timestamp the rest of Mira shows). The caller MUST pass
    only the corrected timestamp; this helper never inspects raw EXIF.
    Returns ``None`` for missing or unparseable input so the caller
    can omit the *when* half and show *where* alone.
    """
    if not when:
        return None
    text = str(when).strip()
    if not text:
        return None
    # Strip a trailing 'Z' that older fromisoformat (pre-3.11) chokes on.
    if text.endswith("Z"):
        text = text[:-1]
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        # Pure date (no time part) or malformed.
        try:
            dt = datetime.fromisoformat(text + "T00:00:00")
        except ValueError:
            return None
    return dt.strftime("%B %Y")


def format_caption(provenance: Optional[Any]) -> str:
    """Compose the splash bottom caption from a
    :class:`~core.cut_overlay.FrameProvenance` — ``"Month YYYY · City,
    Country"`` joined by ``" · "``.

    Either half may be missing:

      * No *where* → ``"Month YYYY"`` (date only).
      * No *when* → the *where* text alone.
      * Neither → empty string.

    Uses :func:`core.cut_overlay._where_text` so the where vocabulary
    matches the cut overlay surfaces.
    """
    if provenance is None:
        return ""
    from core.cut_overlay import _where_text
    when_text = format_when_for_splash(getattr(provenance, "when", None))
    where_text = _where_text(provenance)
    parts = [t for t in (when_text, where_text) if t]
    return " · ".join(parts)


# ── Qt: decode → decorate → splash ───────────────────────────────────


def _decode_to_pixmap(path: Path, edge: int) -> Optional["object"]:
    """Decode ``path`` to a ``QPixmap`` whose long edge ≤ ``edge``.

    Prefers PIL's ``draft`` mode for JPEG (DCT-domain downscale — cheap)
    so even the export-JPEG fallback path lands near the splash size
    without a full-res decode. Returns ``None`` on any failure.
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QImage, QPixmap
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as im:
            try:
                im.draft("RGB", (edge, edge))
            except Exception:                                      # noqa: BLE001
                pass            # non-JPEG formats simply ignore draft
            im.load()
            im = ImageOps.exif_transpose(im)
            if im.mode != "RGB":
                im = im.convert("RGB")
            im.thumbnail((edge, edge), Image.Resampling.LANCZOS)
            w, h = im.size
            qimg = QImage(im.tobytes("raw", "RGB"), w, h,
                          w * 3, QImage.Format.Format_RGB888).copy()
    except Exception:                                              # noqa: BLE001
        log.exception("splash: decode failed for %s", path)
        return None
    pix = QPixmap.fromImage(qimg)
    if pix.isNull():
        return None
    if pix.width() > edge or pix.height() > edge:
        pix = pix.scaled(
            edge, edge,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return pix


def _bundled_canvas_pixmap(dpr: float = 1.0) -> "object":
    """Build the bundled-mark splash canvas: a fixed-size dark surface
    that :func:`decorate_splash_pixmap` paints the title (with the
    icon to its left) and the white frame onto. No caption — we have
    no source photo to read provenance from.

    ``dpr`` scales the physical pixel dimensions AND stamps the
    devicePixelRatio on the resulting pixmap so the canvas paints at
    the same *logical* size regardless of the display's HiDPI ratio."""
    from PyQt6.QtGui import QColor, QPixmap
    scale = max(1.0, float(dpr))
    canvas = QPixmap(int(_FALLBACK_CANVAS_W * scale),
                     int(_FALLBACK_CANVAS_H * scale))
    canvas.fill(QColor(18, 18, 22))
    canvas.setDevicePixelRatio(scale)
    return canvas


def _build_icon_renderer(
    bundled_fallback: Optional[Path],
) -> Optional[Callable[[int], "object"]]:
    """Return a ``(target_logical_height_px) -> QPixmap`` callable that
    produces a CRISP icon for the splash title row.

    Two paths:

      * **Vector** — prefers ``mira-mark.svg`` (sibling of the bundled
        PNG fallback). Rendered fresh at the exact target size via
        :func:`tinted_svg_pixmap`, which paints at PHYSICAL pixel
        resolution and stamps ``devicePixelRatio`` on the result so the
        glyph stays sharp on 125/150% HiDPI displays.
      * **Bitmap fallback** — when the SVG is absent, scales the
        bundled PNG to the target height. Will soften on HiDPI but
        keeps the splash usable.

    Returns ``None`` when neither asset is available (the splash then
    paints the title without an icon).
    """
    if not bundled_fallback:
        return None
    try:
        bundled_path = Path(bundled_fallback)
    except TypeError:
        return None
    svg_sibling = bundled_path.with_name("mira-mark.svg")
    try:
        svg_exists = svg_sibling.is_file()
    except OSError:
        svg_exists = False
    try:
        png_exists = bundled_path.is_file()
    except OSError:
        png_exists = False
    if not svg_exists and not png_exists:
        return None

    def _render(target_h: int) -> "object":
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QColor, QPixmap
        h = max(8, int(target_h))
        if svg_exists:
            try:
                from mira.ui.design.icons import tinted_svg_pixmap
                pix = tinted_svg_pixmap(
                    svg_sibling, h, QColor(255, 255, 255))
                if pix is not None and not pix.isNull():
                    return pix
            except Exception:                                      # noqa: BLE001
                log.exception(
                    "splash: vector icon render failed for %s", svg_sibling)
        if png_exists:
            try:
                pm = QPixmap(str(bundled_path))
                if not pm.isNull():
                    return pm.scaledToHeight(
                        h, Qt.TransformationMode.SmoothTransformation)
            except Exception:                                      # noqa: BLE001
                log.exception(
                    "splash: PNG icon load failed for %s", bundled_path)
        return QPixmap()

    return _render


def decorate_splash_pixmap(
    pixmap: "object", *,
    title: str = SPLASH_TITLE,
    caption: Optional[str] = None,
    icon: Optional["object"] = None,
    icon_render: Optional[Callable[[int], "object"]] = None,
) -> "object":
    """Composite the splash decoration onto a copy of ``pixmap``.

    Layers (in paint order):

      1. **Top-left title row** — optional icon (the bundled mark
         scaled to title height) + the title text, sharing one
         legibility scrim.
      2. **Bottom caption** — small *when · where* text under its own
         scrim; omitted when ``caption`` is ``None``/empty (the
         bundled-mark fallback path).
      3. **White perimeter frame** — a crisp 4-px white border around
         the whole splash so it lifts off the desktop.

    Non-destructive: the input pixmap is not mutated; a fresh copy
    carrying the decoration is returned. Returns ``pixmap`` unchanged
    when it is null or too small to paint on (defensive — the splash
    must never crash on an unusual pixmap).
    """
    from PyQt6.QtCore import Qt, QPointF, QRectF
    from PyQt6.QtGui import (
        QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPen, QPixmap,
    )
    if pixmap is None:
        return pixmap
    try:
        if pixmap.isNull() or pixmap.width() < 32 or pixmap.height() < 32:
            return pixmap
    except Exception:                                              # noqa: BLE001
        return pixmap

    out = QPixmap(pixmap)            # shallow copy; subsequent paint deep-copies
    painter = QPainter(out)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # Layout math runs in LOGICAL pixels so the font/pad caps stay
        # visually consistent across DPRs. ``QPixmap.width()`` returns
        # device pixels — on a DPR=2 splash that's 2× the logical
        # canvas and would push ``int(w * 0.045)`` past the 34-px title
        # cap, shrinking the title in half on HiDPI. ``QPainter`` on a
        # DPR-stamped pixmap uses a LOGICAL coord system, so drawing
        # at these logical dims paints at the right visual size.
        logical_size = out.deviceIndependentSize()
        w = float(logical_size.width())
        h = float(logical_size.height())

        # ── Title row — icon (optional) + large bold title ─────────
        title_font = QFont(painter.font())
        # Scale the title to the pixmap so a wider canvas reads as more
        # confident. Capped so we don't dominate small bundled fallbacks.
        title_px = max(20, min(34, int(w * 0.045)))
        title_font.setPixelSize(title_px)
        title_font.setBold(True)
        title_fm = QFontMetricsF(title_font)
        title_w = title_fm.horizontalAdvance(title) if title else 0.0
        title_h = title_fm.height() if title else 0.0
        # Top-left padding scales with the pixmap so the title sits
        # comfortably away from the edge.
        pad_x = max(16.0, w * 0.04)
        pad_y = max(16.0, h * 0.05)

        # Icon: scale to title-cap height (~1.15× the cap so it
        # visually balances the text). ``icon_render`` is preferred —
        # it lets the caller produce the icon at the EXACT target
        # logical pixels (with the right DPR), avoiding the
        # double-rescale blur that comes from down-scaling a 256-px
        # PNG and then up-scaling to physical pixels on HiDPI
        # displays. ``icon`` is the legacy direct-pixmap path.
        icon_pix = None
        icon_w = 0.0
        icon_h = 0.0
        icon_gap = 0.0
        if title:
            target_h = max(24, int(title_h * 1.15))
            chosen_pix = None
            if icon_render is not None:
                try:
                    chosen_pix = icon_render(target_h)
                except Exception:                                  # noqa: BLE001
                    log.exception("splash: icon_render raised")
                    chosen_pix = None
            elif icon is not None:
                try:
                    if not icon.isNull():
                        chosen_pix = icon.scaledToHeight(
                            target_h,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                except Exception:                                  # noqa: BLE001
                    chosen_pix = None
            if chosen_pix is not None:
                try:
                    if not chosen_pix.isNull():
                        # Logical size, not physical (`width` divides by
                        # devicePixelRatio); positioning math must use
                        # logical pixels so an HiDPI-stamped pixmap
                        # lays out at the right size.
                        icon_pix = chosen_pix
                        icon_w = float(chosen_pix.width()) / max(
                            1.0, chosen_pix.devicePixelRatio())
                        icon_h = float(chosen_pix.height()) / max(
                            1.0, chosen_pix.devicePixelRatio())
                        icon_gap = max(8.0, title_px * 0.4)
                except Exception:                                  # noqa: BLE001
                    icon_pix = None
                    icon_w = icon_h = icon_gap = 0.0

        # Legibility scrim under the title row — dark gradient that
        # fades to transparent on the right + bottom so it never reads
        # as a solid bar over the photo. Sized to cover icon + title.
        if title:
            row_w = icon_w + icon_gap + title_w
            row_h = max(title_h, icon_h)
            scrim_w = min(w, row_w + 2 * pad_x + 40)
            scrim_h = min(h, row_h + 2 * pad_y + 20)
            grad = QLinearGradient(0.0, 0.0, scrim_w, scrim_h)
            grad.setColorAt(0.0, QColor(0, 0, 0, 165))
            grad.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.fillRect(QRectF(0.0, 0.0, scrim_w, scrim_h), grad)

            # Vertically centre the icon against the title text.
            row_top = pad_y + max(0.0, (title_h - row_h) / 2)
            if icon_pix is not None:
                icon_y = row_top + (row_h - icon_h) / 2
                painter.drawPixmap(
                    QRectF(pad_x, icon_y, icon_w, icon_h),
                    icon_pix, QRectF(icon_pix.rect()))

            # Title text — soft drop shadow + crisp white.
            text_x = pad_x + (icon_w + icon_gap if icon_pix is not None else 0.0)
            text_baseline_y = row_top + (row_h - title_h) / 2 + title_fm.ascent()
            painter.setFont(title_font)
            painter.setPen(QColor(0, 0, 0, 200))
            painter.drawText(
                QPointF(text_x + 1.5, text_baseline_y + 1.5), title)
            painter.setPen(QColor(240, 240, 245, 255))
            painter.drawText(QPointF(text_x, text_baseline_y), title)

        # ── Caption block — bottom, small ───────────────────────────
        if caption:
            cap_font = QFont(painter.font())
            cap_px = max(11, min(16, int(w * 0.02)))
            cap_font.setPixelSize(cap_px)
            cap_font.setBold(False)
            cap_fm = QFontMetricsF(cap_font)
            cap_h = cap_fm.height()
            cap_pad_x = max(14.0, w * 0.035)
            cap_pad_y = max(10.0, h * 0.025)

            # Bottom scrim: dark band that fades up into transparent.
            band_h = min(h, cap_h + 2 * cap_pad_y + 16)
            grad = QLinearGradient(0.0, h - band_h, 0.0, h)
            grad.setColorAt(0.0, QColor(0, 0, 0, 0))
            grad.setColorAt(1.0, QColor(0, 0, 0, 170))
            painter.fillRect(QRectF(0.0, h - band_h, w, band_h), grad)

            painter.setFont(cap_font)
            baseline_y = h - cap_pad_y - cap_fm.descent()
            painter.setPen(QColor(0, 0, 0, 200))
            painter.drawText(
                QPointF(cap_pad_x + 1.0, baseline_y + 1.0), caption)
            painter.setPen(QColor(235, 235, 240, 240))
            painter.drawText(QPointF(cap_pad_x, baseline_y), caption)

        # ── White perimeter frame — last so it never gets occluded ──
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pen = QPen(QColor(255, 255, 255, 255))
        pen.setWidth(_SPLASH_FRAME_PX)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # Inset by half the pen width so the stroke lands ENTIRELY
        # inside the pixmap (the default would paint half outside,
        # which Qt clips and leaves a stroke that reads as 2 px).
        half = _SPLASH_FRAME_PX / 2
        painter.drawRect(
            QRectF(half, half, w - _SPLASH_FRAME_PX, h - _SPLASH_FRAME_PX))
    finally:
        painter.end()
    return out


def build_splash_pixmap(
    gateway: Optional[_GatewayLike],
    *,
    bundled_fallback: Path,
    photo_enabled: bool = True,
    max_edge: int = DEFAULT_SPLASH_EDGE,
    deadline_ms: int = DEFAULT_DEADLINE_MS,
    rng: Optional[random.Random] = None,
    title: str = SPLASH_TITLE,
) -> "object":
    """The QPixmap painted on the startup splash, decorated per spec/136 §2a.

    Tries the photo path when ``photo_enabled`` is True (the spec/136
    Settings toggle): pick a random exported frame, decode its cached
    proxy if present, otherwise draft-decode the export JPEG, finally
    fall back to the bundled mark on any failure / deadline overrun.

    The returned pixmap is ALWAYS decorated:

      * Photo path → title + when·where caption baked in with a scrim.
      * Bundled fallback → title only (no caption, no source photo).

    Always returns a ``QPixmap`` — never raises, never blocks beyond
    the deadline.
    """
    icon_render = _build_icon_renderer(bundled_fallback)
    # Decode at PHYSICAL pixels so HiDPI panels get a sharp source; the
    # DPR gets stamped on the pixmap so the decorator + display code
    # treat it as a ``max_edge``-logical-px canvas (same visual size,
    # 2× the pixel detail on a 200 % panel).
    dpr = _screen_device_pixel_ratio()
    physical_edge = int(max_edge * dpr)
    if photo_enabled and gateway is not None:
        try:
            chosen = pick_random_exported_frame(
                gateway, deadline_ms=deadline_ms, rng=rng)
        except Exception:                                          # noqa: BLE001
            log.exception("splash: picker raised")
            chosen = None
        if chosen is not None:
            candidate = chosen.proxy_path or chosen.export_path
            pix = _decode_to_pixmap(candidate, physical_edge)
            if pix is None and chosen.proxy_path is not None:
                # Proxy missing or unreadable → fall back to the export
                # JPEG before giving up on the photo path entirely.
                pix = _decode_to_pixmap(chosen.export_path, physical_edge)
            if pix is not None:
                pix.setDevicePixelRatio(dpr)
                caption = format_caption(chosen.provenance)
                return decorate_splash_pixmap(
                    pix, title=title, caption=caption or None,
                    icon_render=icon_render)
    return decorate_splash_pixmap(
        _bundled_canvas_pixmap(dpr), title=title, caption=None,
        icon_render=icon_render)


def show_startup_splash(
    app, pixmap, *, on_top: bool = True,
) -> Optional["object"]:
    """Construct + show a ``QSplashScreen`` over ``pixmap``.

    Returns the splash (or ``None`` on any failure) so the caller can
    pass it to :func:`finish_startup_splash` once the main window is
    visible. ``app.processEvents()`` is called so the splash actually
    paints before the heavyweight MainWindow construction starts.
    """
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QSplashScreen
    except Exception:                                              # noqa: BLE001
        return None
    flags = Qt.WindowType.SplashScreen
    if on_top:
        flags = flags | Qt.WindowType.WindowStaysOnTopHint
    try:
        splash = QSplashScreen(pixmap, flags)
        splash.show()
        if app is not None:
            try:
                app.processEvents()
            except Exception:                                      # noqa: BLE001
                pass
        return splash
    except Exception:                                              # noqa: BLE001
        log.exception("splash: show failed")
        return None


def finish_startup_splash(splash, window) -> None:
    """Hand the splash off to ``window`` and let Qt dismiss it.

    No-op when ``splash`` is ``None`` (the photo path failed silently
    and we never showed one) or when ``finish`` raises (defensive —
    we must never crash launch on a teardown corner).
    """
    if splash is None or window is None:
        return
    try:
        splash.finish(window)
    except Exception:                                              # noqa: BLE001
        log.exception("splash: finish failed")
