"""``PhotoViewport`` — THE photo display engine, one per surface
(spec/63 slice 1).

Surfaces own chrome and decisions; this widget owns pixels. It holds
the ordered item list and the current index, navigates (arrows /
wheel), and runs the display pipeline per move:

* **Placeholder instantly** — scaled-cache hit (sharp at once) →
  256-px thumb tier → keep the previous image. Never a blank frame.
* **Sharp within a beat** — a priority-0 scaled decode lands from the
  worker (decode-AT-display-size; the engine's generation drop from
  slice 0 means flown-past requests evaporate — skip-ahead for free).
* **Prefetch on settle** — 150 ms after the last move, neighbours
  N+1 / N+2 / N−1 queue at priority 1.

The locked keyboard map (spec/63 §4) is translated HERE, once, into
semantic signals — surfaces connect the verbs they support and never
read keycodes again: P→pick · X→skip · Space→toggle · C→cycle ·
Tab→transport (clips) · Enter→sweep · F10→truth · F/F11→fullscreen ·
Esc→back. Arrows/wheel navigate internally; stepping past either end
emits ``edge_reached`` so hosts can chain days/buckets.

Display targets are quantized to 512-px buckets (stable cache keys
across window resizes); the photo label carries Ignored size policy —
pixmaps must never drive the window's minimum size (the 2026-06-12
cut_play F11 lesson). True native dimensions arrive with every sharp
delivery via the scaled tier and are exposed through
:meth:`sharp_pixmap_info` for 1:1/zoom math.

Videos (slice 3, arm-on-landing — Nelson's design): a video shows its
poster like a photo placeholder while you fly past; the
QMediaPlayer/QVideoWidget ARMS only on the settle beat (you've
landed), and the poster→live flip happens only when real frames flow
for the item still on screen. The player is a STACKED SIBLING of the
photo label, never an overlay — QVideoWidget renders through the
Windows native compositor and child overlays paint underneath it (the
2026-06-09 finding the old PosterStack was built around). This keeps
the no-black-frame guarantee AND kills the per-keypress flicker (the
old PosterStack flipped on every move). A small timeline API
(position/duration/seek/play-pause) lets surfaces build scrubbers.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from PyQt6.QtCore import QPointF, QRect, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QPainter, QPalette, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QStackedLayout, QVBoxLayout, QWidget)

from core.brand_profile import AfPoint
from mira.ui.i18n import tr

log = logging.getLogger(__name__)

#: Settle delay before neighbour prefetch — matches the Picker's
#: proven skim-then-settle cadence.
_SETTLE_MS = 150

#: Display-target quantum: requests round the viewport size UP to this
#: step so small window resizes reuse cache entries.
_TARGET_STEP = 512

#: Wheel: one notch (120 units) = one item, exactly like MediaCanvas.
_WHEEL_STEP_UNITS = 120

#: Prefetch plan on settle — forward-biased, the Picker's proven set.
_PREFETCH_OFFSETS = (1, 2, -1)


def _clamp(v: float, lo: float, hi: float) -> float:
    if hi < lo:
        return lo
    return max(lo, min(hi, v))


class _InspectView(QWidget):
    """F10 — the INSPECTION LENS (Nelson 2026-06-12): the current photo
    at full resolution (RAW → honest half-res sensor demosaic) on a
    black canvas. **F** toggles HONEST focus peaking (the deliberate
    one-frame verdict, unlike the Sweep's fast peaking); the AF point
    shows if the host had one; box-zoom (Z) is true 1:1 + pan.

    Opens WINDOWED and MODAL (Nelson 2026-06-12 UI round — best
    resolution without taking the screen over, and the app waits: the
    lens must be closed before working on anything else): a resizable,
    aspect-locked window sized to the image, title = name + honest
    pixel dimensions. **F11** goes truly fullscreen; **Esc** steps
    down one level — zoom → fullscreen → close; F10 / a plain click
    closes outright."""

    def __init__(self, base: QPixmap, af_point: Optional[AfPoint] = None,
                 *, path: Optional[Path] = None, is_raw: bool = False,
                 with_tools: bool = True,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setObjectName("InspectView")
        # The zoom/peaking tool bar shows on the CULL surfaces (Picker,
        # Quick Sweep); Edit and the Cut views open the lens CLEAN
        # (Nelson 2026-06-12 standardisation — "not necessary there").
        self._with_tools = bool(with_tools)
        # MODAL (Nelson 2026-06-12): inspecting is a parenthesis — the
        # app waits until the lens closes. Must be set BEFORE show().
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        # House-themed like every other canvas surface (Nelson
        # 2026-06-12 UI round — the old inline-black exception is
        # REVOKED): the window is the photo bed and the bar a region
        # card, both via QSS roles in BOTH themes.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._aspect_guard = False
        self._bar_on = True     # explicit — isVisible() lies pre-show
        # Interactive resize (Nelson 2026-06-12): smooth-rescaling the
        # FULL-RES base (+ recompositing peaking) on every drag tick
        # made the photo flicker. During the drag we fit a mid-size
        # proxy with FAST transformation — the picture stays visible
        # (nicer than blanking) — and the smooth full-res render +
        # peaking run ONCE when the user stops.
        self._resizing = False
        self._drag_proxy: Optional[QPixmap] = None
        self._resize_settle = QTimer(self)
        self._resize_settle.setSingleShot(True)
        self._resize_settle.setInterval(180)
        self._resize_settle.timeout.connect(self._on_resize_settled)
        name = path.name if path is not None else tr("Inspect")
        self.setWindowTitle(
            f"{name} — {base.width()} × {base.height()}")
        self._base = base
        self._af = af_point
        self._path = path
        self._is_raw = is_raw
        self._peaking = False
        self._peak_cache = None        # display overlay: (w,h,colour,sens)
        # Full-resolution binary masks (round 5, Nelson 2026-06-12):
        # computed ONCE per source at ITS resolution — the fit view
        # derives by DENSITY downscale, 1:1 by a straight slice, so the
        # overview and the zoom agree. Keyed "base"/"zoom" → (sens, mask);
        # only the latest sensitivity is kept (a 24 MP mask is ~24 MB).
        self._peak_binary: dict = {}
        # Sensitivity drags settle before the full-res recompute fires
        # (one ~100-200 ms pass per chosen value, not per slider tick).
        self._peak_sens_timer = QTimer(self)
        self._peak_sens_timer.setSingleShot(True)
        self._peak_sens_timer.setInterval(180)
        self._peak_sens_timer.timeout.connect(self._apply_peak_sens)
        # Zoom (5c): fit ↔ 1:1. ``_zoom_source`` is the full-res pixels
        # (full JPEG = base; full RAW demosaic, lazy); ``_pan`` is the
        # view centre in source-pixel coords; drag/arrows move it.
        self._zoom = False
        self._zoom_source: Optional[QPixmap] = None
        self._pan = None               # QPointF, set on first zoom
        self._drag_anchor = None
        self._drag_pan_start = None
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                  QSizePolicy.Policy.Ignored)
        box.addWidget(self._label, 1)

        # ── The control bar (Nelson 2026-06-12 UI round): the zoom +
        # peaking options return as visible controls — the windowed
        # lens carries chrome; F11 fullscreen is the PURE look (bar
        # hidden, peaking + zoom off). Colour/sensitivity collapse
        # until Peaking is on (the old tools-row behaviour).
        from core.settings import load_settings
        _s = load_settings()
        self._peak_colour_name = str(_s.get("peaking_color", "magenta"))
        self._peak_sens_val = int(_s.get("peaking_sensitivity", 50))
        self._bar = QWidget(self)
        self._bar.setObjectName("InspectBar")
        self._bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar = QHBoxLayout(self._bar)
        bar.setContentsMargins(10, 6, 10, 6)
        bar.setSpacing(8)
        self._zoom_btn = QPushButton(tr("Zoom 1:1"))
        self._zoom_btn.setObjectName("FeatureToggle")
        self._zoom_btn.setCheckable(True)
        self._zoom_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._zoom_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._zoom_btn.setToolTip(tr(
            "True 1:1 pixels — drag or arrow keys pan  (Z)"))
        self._zoom_btn.clicked.connect(self._toggle_zoom)
        bar.addWidget(self._zoom_btn)
        bar.addStretch(1)
        self._peak_colour_btn = QPushButton(
            tr(self._peak_colour_name.capitalize()))
        self._peak_colour_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._peak_colour_btn.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        self._peak_colour_btn.setToolTip(tr("Cycle the peaking colour."))
        self._peak_colour_btn.clicked.connect(self._on_peak_colour)
        bar.addWidget(self._peak_colour_btn)
        from mira.ui.edited.adjustment_grid import (
            AdjustmentGrid, AdjustmentSpec,
        )
        self._peak_sens = AdjustmentGrid(
            [AdjustmentSpec(
                "sensitivity", "Sensitivity",
                0.0, 100.0, float(self._peak_sens_val), 5.0, 0,
            )],
            columns=1,
        )
        # Twice the default track (Nelson 2026-06-12) — sensitivity is
        # the lens's main tuning gesture; a longer track = finer feel.
        self._peak_sens.set_slider_minimum_width("sensitivity", 180)
        self._peak_sens.valueChanged.connect(self._on_peak_sens)
        bar.addWidget(self._peak_sens)
        self._peak_btn = QPushButton(tr("Peaking"))
        self._peak_btn.setObjectName("FeatureToggle")
        self._peak_btn.setCheckable(True)
        self._peak_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._peak_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._peak_btn.setToolTip(tr(
            "Honest focus peaking on the real pixels  (F)"))
        self._peak_btn.clicked.connect(self._on_peak_toggle)
        bar.addWidget(self._peak_btn)
        box.addWidget(self._bar)
        self._refresh_bar_collapse()
        if not self._with_tools:
            self._bar_on = False
            self._bar.setVisible(False)

        self._hint = QLabel(self)
        self._hint.setObjectName("InspectHint")
        self._hint.setText(tr(
            "F  peaking      Z  zoom 1:1      F11  fullscreen      "
            "Esc  back / close") if self._with_tools else tr(
            "F11  fullscreen      Esc  back / close"))
        self._hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    # ── the control bar handlers ──────────────────────────────────

    def _refresh_bar_collapse(self) -> None:
        """Collapse-until-active: colour + sensitivity show only while
        Peaking is on (the old Picker tools-row behaviour)."""
        on = self._peaking
        self._peak_colour_btn.setVisible(on)
        self._peak_sens.setVisible(on)

    def _on_peak_toggle(self) -> None:
        self._peaking = self._peak_btn.isChecked()
        self._peak_cache = None
        self._refresh_bar_collapse()
        self._fit()

    def _on_peak_colour(self) -> None:
        order = ("magenta", "yellow", "red", "cyan")
        try:
            idx = order.index(self._peak_colour_name)
        except ValueError:
            idx = -1
        self._peak_colour_name = order[(idx + 1) % len(order)]
        self._peak_colour_btn.setText(tr(self._peak_colour_name.capitalize()))
        # Colour only re-tints the overlay — the binary masks stand.
        self._peak_cache = None
        self._persist_peaking_pref("peaking_color", self._peak_colour_name)
        if self._peaking:
            self._fit()

    def _on_peak_sens(self, _key: str, value: float) -> None:
        self._peak_sens_val = int(value)
        # Debounce: the full-res mask recomputes once per settled value.
        self._peak_sens_timer.start()

    def _apply_peak_sens(self) -> None:
        self._peak_binary.clear()
        self._peak_cache = None
        self._persist_peaking_pref(
            "peaking_sensitivity", int(self._peak_sens_val))
        if self._peaking:
            self._fit()

    @staticmethod
    def _persist_peaking_pref(key: str, value) -> None:
        """The bar's tuning sticks across sessions (Nelson 2026-06-12
        round 5) — written through the canonical settings writer; a
        failed write must never break the lens."""
        try:
            from core.settings import update_setting
            update_setting(key, value)
        except Exception:                                       # noqa: BLE001
            log.exception("peaking preference write failed (%s)", key)

    def open_windowed(self) -> None:
        """Show the lens as a normal RESIZABLE window whose VIEW area
        is exactly the picture's aspect (the window adds only the bar's
        height — no letterbox bands, Nelson 2026-06-12): the image fit
        inside ~88% of the available screen, centred. Resizing keeps
        the lock (:meth:`_enforce_aspect`)."""
        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry()
        bar_h = self._bar.sizeHint().height() if self._bar_on else 0
        max_w = max(320, int(avail.width() * 0.88))
        max_h = max(240, int(avail.height() * 0.88) - bar_h)
        w = max(1, self._base.width())
        h = max(1, self._base.height())
        scale = min(max_w / w, max_h / h)
        view_w = max(1, int(round(w * scale)))
        view_h = max(1, int(round(h * scale)))
        win_h = view_h + bar_h
        self.resize(view_w, win_h)
        self.move(avail.center().x() - view_w // 2,
                  avail.center().y() - win_h // 2)
        self.show()
        self.raise_()
        self.activateWindow()

    def _enforce_aspect(self, ev) -> None:
        """The window is ASPECT-LOCKED to the picture (Nelson
        2026-06-12): the view area always matches the photo's ratio so
        the picture fills it edge to edge — no empty bands. The
        dominant drag axis wins, the other follows. Fullscreen /
        maximized are exempt (the OS owns those geometries; fullscreen
        letterboxes by design — the pure look)."""
        if self._aspect_guard or self._base.isNull():
            return
        if not self.isVisible():
            return       # pre-show geometry is open_windowed's job —
            # and isVisible() lies about the bar before the first show.
        if self.windowState() & (Qt.WindowState.WindowFullScreen
                                 | Qt.WindowState.WindowMaximized):
            return
        bar_h = self._bar.sizeHint().height() if self._bar_on else 0
        aspect = self._base.width() / max(1, self._base.height())
        old = ev.oldSize()
        dw = abs(self.width() - old.width()) if old.width() > 0 else 0
        dh = abs(self.height() - old.height()) if old.height() > 0 else 0
        if dh > dw:                       # vertical drag → width follows
            view_h = max(120, self.height() - bar_h)
            target_w = int(round(view_h * aspect))
            target_h = self.height()
        else:                             # horizontal/corner → height follows
            target_w = max(160, self.width())
            target_h = int(round(target_w / aspect)) + bar_h
        if (abs(target_w - self.width()) <= 2
                and abs(target_h - self.height()) <= 2):
            return
        self._aspect_guard = True
        try:
            self.resize(target_w, target_h)
        finally:
            self._aspect_guard = False

    def _toggle_fullscreen(self) -> None:
        """F11 — fullscreen is the PURE look (Nelson 2026-06-12 UI
        round): the bar hides and peaking + zoom switch OFF; coming
        back (Esc / F11) restores the bar, helpers stay off (clean)."""
        if self.isFullScreen():
            # Bar back BEFORE showNormal so the restored geometry's
            # aspect check counts the bar (no snap-flicker). Tool-less
            # lenses (Edit, Cut) stay bar-less either way.
            self._bar_on = self._with_tools
            self._bar.setVisible(self._with_tools)
            self.showNormal()
        else:
            if self._zoom:
                self._toggle_zoom()
            if self._peaking:
                self._peaking = False
                self._peak_btn.setChecked(False)
                self._peak_cache = None
                self._refresh_bar_collapse()
            self._bar_on = False
            self._bar.setVisible(False)
            self.showFullScreen()
        self._fit()

    def _view_size(self):
        """The pixel area the photo actually gets (the label — the bar
        eats window height when visible). True 1:1 must crop to THIS."""
        size = self._label.size()
        if size.width() < 2 or size.height() < 2:
            size = self.size()
        return size

    def _drag_base(self) -> QPixmap:
        """A mid-size proxy of the base for drag-time fits — fast-
        scaling THIS per tick is ~free; smooth-scaling 24 MP was the
        flicker. Built once, lazily."""
        if self._drag_proxy is None:
            if max(self._base.width(), self._base.height()) > 2560:
                self._drag_proxy = self._base.scaled(
                    2560, 2560, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
            else:
                self._drag_proxy = self._base
        return self._drag_proxy

    def _on_resize_settled(self) -> None:
        self._resizing = False
        self._fit()                  # the one smooth render + peaking

    def _fit(self) -> None:
        if self._base.isNull():
            return
        size = self._view_size()
        if size.width() < 2 or size.height() < 2:
            return
        if self._zoom and self._zoom_source is not None:
            shown = self._zoom_crop()
        elif self._resizing:
            # Mid-drag: fast proxy fit, no AF, no peaking — crisp
            # truth returns on settle.
            shown = self._drag_base().scaled(
                size, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation)
        else:
            shown = self._base.scaled(
                size, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            if self._af is not None:
                self._draw_af(shown)
        if self._peaking and not self._resizing:
            shown = self._composite_peaking(shown)
        self._label.setPixmap(shown)
        self._hint.adjustSize()
        bottom = self._label.geometry().bottom() if self._bar.isVisible() \
            else self.height()
        self._hint.move(16, max(0, bottom - self._hint.height() - 14))
        self._hint.raise_()

    # ── zoom (5c): fit ↔ 1:1 with pan ─────────────────────────────

    def _toggle_zoom(self) -> None:
        if self._zoom:
            self._zoom = False
            self._zoom_btn.setChecked(False)
            self._fit()
            return
        src = self._zoom_source
        if src is None:
            if self._is_raw and self._path is not None:
                from mira.ui.media.image_loader import load_raw_full_res
                QApplication.setOverrideCursor(
                    QCursor(Qt.CursorShape.WaitCursor))
                try:
                    img = load_raw_full_res(self._path)
                finally:
                    QApplication.restoreOverrideCursor()
                src = QPixmap.fromImage(img) if not img.isNull() else self._base
            else:
                src = self._base               # full-res JPEG already
            self._zoom_source = src
        # Centre on the AF point if we have one (zoom to where it
        # focused), else the image centre.
        if self._af is not None:
            self._pan = QPointF(self._af.cx * src.width(),
                                self._af.cy * src.height())
        else:
            self._pan = QPointF(src.width() / 2.0, src.height() / 2.0)
        self._peak_cache = None        # crop differs from the fit base
        self._zoom = True
        self._zoom_btn.setChecked(True)
        self._fit()

    def _zoom_rect(self) -> tuple[int, int, int, int]:
        """The 1:1 crop in SOURCE pixel coords — shared by the pixel
        crop and the peaking-mask slice so they always align."""
        src = self._zoom_source
        view = self._view_size()
        vw, vh = view.width(), view.height()
        cw, ch = min(vw, src.width()), min(vh, src.height())
        cx = _clamp(self._pan.x() - cw / 2.0, 0, src.width() - cw)
        cy = _clamp(self._pan.y() - ch / 2.0, 0, src.height() - ch)
        return int(cx), int(cy), int(cw), int(ch)

    def _zoom_crop(self) -> QPixmap:
        x, y, w, h = self._zoom_rect()
        return self._zoom_source.copy(x, y, w, h)

    def _pan_by(self, dx: float, dy: float) -> None:
        if not self._zoom or self._zoom_source is None:
            return
        src = self._zoom_source
        self._pan = QPointF(
            _clamp(self._pan.x() + dx, 0, src.width()),
            _clamp(self._pan.y() + dy, 0, src.height()))
        self._peak_cache = None
        self._fit()

    def _peak_binary_for(self, src: QPixmap, src_key: str):
        """The full-resolution binary mask for ``src``, computed once
        per (source, sensitivity) under a wait cursor (~100-200 ms on
        24 MP — the lens's deliberate one-frame budget). For JPEGs the
        zoom source IS the base, so one mask serves both views."""
        if src is self._base:
            src_key = "base"
        cached = self._peak_binary.get(src_key)
        if cached is not None and cached[0] == int(self._peak_sens_val):
            return cached[1]
        from core.focus_peaking import compute_peaking_binary
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            mask = compute_peaking_binary(
                src, sensitivity=self._peak_sens_val)
        finally:
            QApplication.restoreOverrideCursor()
        if mask is not None:
            self._peak_binary[src_key] = (int(self._peak_sens_val), mask)
        return mask

    def _composite_peaking(self, scaled: QPixmap) -> QPixmap:
        """Overlay peaking on the displayed pixmap — round 5 (Nelson
        2026-06-12): the mask comes from the FULL-RESOLUTION source; the
        fit overview is its DENSITY downscale (thin true edges survive,
        scattered noise stays dark, display smoothing can't invent
        edges) and the 1:1 zoom slices the very same mask."""
        from core.focus_peaking import (
            color_tuple_for_name, overlay_from_binary, scale_binary_mask)
        if scaled.isNull():
            return scaled
        if self._zoom and self._zoom_source is not None:
            full = self._peak_binary_for(self._zoom_source, "zoom")
            if full is None:
                return scaled
            x, y, w, h = self._zoom_rect()
            view_mask = full[y:y + h, x:x + w]
            if (view_mask.shape[0] != scaled.height()
                    or view_mask.shape[1] != scaled.width()):
                view_mask = scale_binary_mask(
                    view_mask, scaled.width(), scaled.height())
        else:
            full = self._peak_binary_for(self._base, "base")
            if full is None:
                return scaled
            view_mask = scale_binary_mask(
                full, scaled.width(), scaled.height())
        colour = self._peak_colour_name
        # The zoom flag matters: in the aspect-locked window the fit
        # view and the 1:1 crop are EXACTLY the same pixel size — the
        # key alone must keep their overlays apart (pan invalidates
        # explicitly at the pan call sites).
        key = (scaled.width(), scaled.height(), colour,
               int(self._peak_sens_val), self._zoom)
        if self._peak_cache is None or self._peak_cache[0] != key:
            overlay = overlay_from_binary(
                view_mask, color=color_tuple_for_name(colour))
            self._peak_cache = (key, overlay)
        else:
            overlay = self._peak_cache[1]
        if overlay is None or overlay.isNull():
            return scaled
        out = QPixmap(scaled.size())
        out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        try:
            painter.drawPixmap(0, 0, scaled)
            painter.drawPixmap(0, 0, overlay)
        finally:
            painter.end()
        return out

    def _draw_af(self, target: QPixmap) -> None:
        af = self._af
        dw, dh = target.width(), target.height()
        rw, rh = max(2.0, af.w * dw), max(2.0, af.h * dh)
        rx, ry = af.cx * dw - rw / 2.0, af.cy * dh - rh / 2.0
        painter = QPainter(target)
        try:
            pen = QPen(self.palette().color(QPalette.ColorRole.Link))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawRect(int(round(rx)), int(round(ry)),
                             int(round(rw)), int(round(rh)))
        finally:
            painter.end()

    def showEvent(self, ev) -> None:  # noqa: N802
        super().showEvent(ev)
        self._fit()

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self._enforce_aspect(ev)
        # A REAL size change while visible = an interactive drag (the
        # first show arrives with an invalid oldSize and stays on the
        # smooth path, so opening is crisp immediately).
        if (self.isVisible() and ev.oldSize().isValid()
                and ev.oldSize() != ev.size()):
            self._resizing = True
            self._resize_settle.start()
        self._fit()

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        key = ev.key()
        step = 120
        if key in (Qt.Key.Key_F1, Qt.Key.Key_Question):
            from mira.ui.base.shortcuts import show_shortcuts
            show_shortcuts(self, tr("Full-Resolution lens"), [
                (tr("F"),               tr("Focus peaking on / off")),
                (tr("Z"),               tr("Box-zoom 1:1 / fit")),
                (tr("◀ / ▶ · ▲ / ▼"),    tr("Pan when zoomed")),
                (tr("Drag"),            tr("Pan when zoomed")),
                (tr("F11"),             tr("Toggle fullscreen "
                                           "(pure look — no tools)")),
                (tr("Esc"),             tr("Step down one level — "
                                           "zoom → fullscreen → close")),
                (tr("F10 · Click"),     tr("Close")),
                (tr("F1 · ?"),          tr("This help")),
            ])
            ev.accept()
            return
        if key == Qt.Key.Key_F10:
            self.close()
        elif key == Qt.Key.Key_F11:
            self._toggle_fullscreen()
        elif key == Qt.Key.Key_Escape:
            # Step down one level: zoom → fullscreen → close.
            if self._zoom:
                self._toggle_zoom()
            elif self.isFullScreen():
                self._toggle_fullscreen()    # back to windowed, bar returns
            else:
                self.close()
        elif (key == Qt.Key.Key_F and self._with_tools
                and not self.isFullScreen()):
            # Through the bar button so chrome + state stay in sync.
            # (Fullscreen is the pure look — no peaking, no zoom; the
            # tool-less lens — Edit, Cut — has neither at all.)
            self._peak_btn.toggle()
            self._on_peak_toggle()
        elif (key == Qt.Key.Key_Z and self._with_tools
                and not self.isFullScreen()):
            self._toggle_zoom()
        elif self._zoom and key in (Qt.Key.Key_Left, Qt.Key.Key_Right,
                                    Qt.Key.Key_Up, Qt.Key.Key_Down):
            dx = (-step if key == Qt.Key.Key_Left else
                  step if key == Qt.Key.Key_Right else 0)
            dy = (-step if key == Qt.Key.Key_Up else
                  step if key == Qt.Key.Key_Down else 0)
            self._pan_by(dx, dy)
        else:
            super().keyPressEvent(ev)
            return
        ev.accept()

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if self._zoom:
            # Begin a pan drag (don't close).
            self._drag_anchor = ev.position()
            self._drag_pan_start = QPointF(self._pan)
            ev.accept()
            return
        self.close()                       # a plain click closes the fit view
        ev.accept()

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if self._zoom and self._drag_anchor is not None:
            delta = ev.position() - self._drag_anchor
            # Drag right → see leftward content: pan opposite the drag.
            src = self._zoom_source
            self._pan = QPointF(
                _clamp(self._drag_pan_start.x() - delta.x(), 0, src.width()),
                _clamp(self._drag_pan_start.y() - delta.y(), 0, src.height()))
            self._peak_cache = None
            self._fit()
            ev.accept()

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        self._drag_anchor = None
        self._drag_pan_start = None
        ev.accept()


@dataclass(frozen=True)
class ViewportItem:
    """One entry in the viewport's ordered list. ``payload`` is the
    host's opaque handle (a SessionFile, a CullItem, a card title…) —
    the viewport never looks inside it.

    Two species: **file items** carry ``path`` and run the full
    placeholder→sharp pipeline; **loose slides** carry ``pixmap``
    (separator/opener cards, any host-rendered image) and display it
    directly — already sharp, nothing to decode or prefetch."""
    path: Optional[Path] = None
    kind: str = "photo"          # "photo" | "video" | "card"
    payload: object = None
    pixmap: Optional[QPixmap] = None


#: Inner padding (px) between the media and the canvas edge.
#: Keeps a sliver of the blurred backdrop visible all around the photo
#: / video, so the media never touches the outer state-border (Nelson
#: 2026-06-15 — "make the photo/video a little bit smaller so it never
#: touches any border"). The hairline media frame painted in
#: :meth:`PhotoViewport.paintEvent` sits just outside the inset rect.
_MEDIA_INNER_PAD = 8

#: Hairline frame around the displayed media — white at 38 % alpha,
#: same FRAME_COLOR :class:`BlurredPhotoCanvas` carries on the Cut
#: surfaces. Works on both the darkened blurred backdrop and the live
#: video bed because both contexts read as dark.
_MEDIA_FRAME_COLOR = QColor(255, 255, 255, 96)


class PhotoViewport(QWidget):
    """The one way a current item gets on screen (spec/63 §1)."""

    # ── navigation / lifecycle ────────────────────────────────────
    current_changed = pyqtSignal(int)        # the new current index
    edge_reached = pyqtSignal(int)           # ±1 — stepped past an end
    sharp_changed = pyqtSignal()             # sharp pixels landed/changed

    # ── the locked key grammar, as verbs (spec/63 §4) ─────────────
    pick_requested = pyqtSignal()            # P
    skip_requested = pyqtSignal()            # X
    toggle_requested = pyqtSignal()          # Space
    cycle_requested = pyqtSignal()           # C
    transport_requested = pyqtSignal()       # Tab (clips)
    sweep_requested = pyqtSignal()           # Enter
    truth_requested = pyqtSignal()           # F10
    fullscreen_requested = pyqtSignal()      # F / F11
    back_requested = pyqtSignal()            # Esc

    #: See module-level :data:`_MEDIA_INNER_PAD` / :data:`_MEDIA_FRAME_COLOR`.
    _MEDIA_INNER_PAD = _MEDIA_INNER_PAD
    _MEDIA_FRAME_COLOR = _MEDIA_FRAME_COLOR

    # ── video timeline (for surface scrubbers) ────────────────────
    video_position_changed = pyqtSignal(int)   # ms
    video_duration_changed = pyqtSignal(int)   # ms
    video_playing_changed = pyqtSignal(bool)
    # Graceful-failure pass-through (the Pick surface's "Cannot play
    # this video — Pick/Skip still works" message; Nelson #4c): a
    # corrupt/unsupported clip must say so, never a silent blank.
    video_error = pyqtSignal(str)

    # Fires whenever the displayed image's rect inside the photo area
    # changes (new pixels, resize) — overlay widgets (the Edit crop
    # tool) re-sync their geometry on it. The MediaCanvas contract,
    # mirrored for the 6b migration (spec/63 §6.1).
    photo_geometry_changed = pyqtSignal()

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        cache=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PhotoViewport")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        if cache is None:
            from mira.ui.media.photo_cache import photo_cache
            cache = photo_cache()
        self._cache = cache
        self._items: List[ViewportItem] = []
        self._index = -1
        self._sharp: Optional[QPixmap] = None      # current item's pixels
        self._native: Optional[QSize] = None       # their TRUE dimensions
        self._displayed: QPixmap = QPixmap()       # whatever is on screen
        self._target_key: Tuple[int, int] = (0, 0)

        # Photo/poster label — only widget in the stack now. The video
        # widget rides as a raised sibling (managed manually so it sits
        # at the media's letterbox rect, not the full canvas — that
        # leaves the soft blurred backdrop showing in the bars instead
        # of QVideoWidget's opaque native black; Nelson 2026-06-15
        # canvas sweep).
        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.setStackingMode(QStackedLayout.StackingMode.StackOne)
        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Transparent bg so the viewport's paintEvent backdrop shows
        # through the letterbox area around the centered pixmap.
        self._label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._label.setAutoFillBackground(False)
        # Pixmaps must never drive layout minimums (cut_play lesson).
        self._label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                  QSizePolicy.Policy.Ignored)
        self._stack.addWidget(self._label)

        # Cached 48×48 darkened tiny for the blurred-cover backdrop
        # (mira/ui/design/blurred_backdrop helpers — same recipe
        # BlurredPhotoCanvas uses on the Cut detail grid + Cut player).
        # Invalidated on item change and when a sharper source lands;
        # never recomputed per paint (the viewport repaints during nav
        # + peaking, so the downscale must not ride the hot path).
        self._backdrop_tiny: Optional[QPixmap] = None
        self._backdrop_source_key: Optional[int] = None

        # Video (lazy — QtMultimedia stays untouched on photos-only
        # surfaces, tests, and odd machines).
        self._player = None
        self._audio = None
        self._video_widget = None
        self._video_armed: Optional[Path] = None   # path the player holds
        self._video_live = False                    # poster flipped to frames
        self._video_playing = False                 # authoritative play state
        self._video_autoplay = True
        # Cached audio / rate so the host can set them before the player
        # arms on the first video landing; applied in ``_ensure_player``.
        self._video_volume: float = 0.80
        self._video_rate: float = 1.0

        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(_SETTLE_MS)
        self._settle.timeout.connect(self._on_settle)

        self._wheel_units = 0
        self._truth_window = None
        self._truth_internal = True       # surfaces (Edit) may override F10
        # Host-rendered display override (spec/63 §6.1 — Edit's
        # developed working view / Toggle-Crop render; the video
        # workshop's developed frame). Shown INSTEAD of the browse
        # pixels until navigation clears it; peaking/AF never composite
        # onto it (it is not the sensor's pixels).
        self._rendered_override: Optional[QPixmap] = None
        # spec/59 §8 Exported watermark — mirrored from MediaCanvas for
        # the 6b migration. Child of the label; geometry tracks the
        # displayed image rect on the photo_geometry_changed pulse.
        from mira.ui.base.exported_watermark import ExportedWatermark
        self._exported_watermark = ExportedWatermark(self._label)
        self._exported_watermark_on = False
        # Rich overlay: AF point (full-absorb 5a) — drawn on the
        # displayed photo; the host feeds the brand-profile/EXIF point.
        self._af_point: Optional[AfPoint] = None
        self._af_overlay_enabled = False
        # Focus peaking (full-absorb 5b). On RAW the half-res sensor
        # decode is BOTH the displayed base and the peaking source, so
        # the mask registers by construction (the embedded thumb's
        # lens-correction would misregister it — Nelson 2026-05-16).
        self._peaking_enabled = False
        self._peaking_color_name = "magenta"
        self._peaking_sensitivity: Optional[int] = None     # None → setting
        self._stack_film_peaking = False
        self._peaking_mask_cache = None     # (key) → mask QPixmap
        self._halfres_cache = None          # (path_str, half QPixmap)
        self._cache.scaled_pixmap_ready.connect(self._on_scaled_ready)
        self._cache.decode_failed.connect(self._on_decode_failed)
        self.truth_requested.connect(self._on_truth_requested)

        # The viewport paints its own bg (the blurred-cover backdrop);
        # WA_StyledBackground stays for QSS rule compatibility but the
        # paintEvent below covers it.
        self.setAutoFillBackground(False)

        # The Full-Resolution affordance (Nelson 2026-06-12): a subtle
        # corner magnifier mirroring F10, an overlay child so it rides
        # ON the media and appears on EVERY surface for free. Hidden on
        # video (nothing full-res to inspect) and until an item shows.
        self._inspect_btn = QPushButton("\U0001F50D", self)   # 🔍
        self._inspect_btn.setObjectName("ViewportInspectButton")
        self._inspect_btn.setToolTip(tr("Full resolution — inspect this "
                                        "frame up close  (F10)"))
        self._inspect_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._inspect_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._inspect_btn.setFixedSize(34, 34)
        self._inspect_btn.clicked.connect(self.truth_requested.emit)
        self._inspect_btn.hide()
        self._corner_inspect_visible = True
        self._lens_tools = True

    def set_corner_inspect_visible(self, on: bool) -> None:
        """Hide the corner 🔍 magnifier when the SURFACE provides its
        own full-resolution affordance (the nav-row "Full Resolution"
        button — Nelson 2026-06-12 UI round). F10 and
        ``truth_requested`` are unaffected; this is chrome only."""
        self._corner_inspect_visible = bool(on)
        self._update_inspect_btn()

    def set_lens_tools_visible(self, on: bool) -> None:
        """Whether the F10 lens carries the zoom/peaking tool bar.
        Cull surfaces (Picker, Quick Sweep) keep it; Edit and the Cut
        views open the lens clean (Nelson 2026-06-12 standardisation)."""
        self._lens_tools = bool(on)

    # ── host-display contract (spec/63 §6.1 — the Edit migration) ─

    def set_truth_internal(self, on: bool) -> None:
        """``False`` → F10 only EMITS ``truth_requested``; the host
        owns the lens (Edit: the developed full-res preview, not the
        browse pixels). Default ``True`` (the cull surfaces' built-in
        honest lens)."""
        self._truth_internal = bool(on)

    def set_rendered_pixmap(self, pm: QPixmap) -> None:
        """Show a HOST-RENDERED pixmap for the current item — Edit's
        developed working view / Toggle-Crop render, the video
        workshop's developed frame. Replaces the browse pixels on
        screen until navigation clears it (:meth:`show_index` /
        :meth:`set_items`); ``sharp_pixmap_info`` keeps reporting the
        browse pixels (truth math never reads renders). Works with no
        items loaded (the video workshop's standalone bed)."""
        self._rendered_override = pm if pm is not None else None
        self._stack.setCurrentWidget(self._label)
        self._fit()

    def clear_rendered_pixmap(self) -> None:
        """Back to the browse pixels (host call; navigation also
        clears)."""
        if self._rendered_override is None:
            return
        self._rendered_override = None
        self._fit()

    def rendered_pixmap(self) -> Optional[QPixmap]:
        return self._rendered_override

    def photo_area_widget(self) -> QWidget:
        """The widget hosting the displayed photo — overlay widgets
        (the crop tool) parent here and re-sync geometry on
        :attr:`photo_geometry_changed` (the MediaCanvas contract)."""
        return self._label

    def image_rect_in_photo_area(self) -> QRect:
        """The displayed image's letterboxed rect inside
        :meth:`photo_area_widget` coordinates."""
        pm = self._label.pixmap()
        if pm is None or pm.isNull():
            return self._label.rect()
        x = (self._label.width() - pm.width()) // 2
        y = (self._label.height() - pm.height()) // 2
        return QRect(x, y, pm.width(), pm.height())

    def set_exported_watermark(self, on: bool) -> None:
        """spec/59 §8: the diagonal "Exported" overlay over the
        displayed image (lineage-driven; the host owns the decision)."""
        self._exported_watermark_on = bool(on)
        self._sync_exported_watermark_geometry()

    def _sync_exported_watermark_geometry(self) -> None:
        rect = self.image_rect_in_photo_area()
        self._exported_watermark.setGeometry(rect)
        self._exported_watermark.setVisible(
            self._exported_watermark_on and not rect.isEmpty())
        if self._exported_watermark_on:
            self._exported_watermark.raise_()

    # ── public API ────────────────────────────────────────────────

    def set_items(self, items: List[ViewportItem], current: int = 0) -> None:
        """Hand over the ordered list; shows ``current`` immediately."""
        self._items = list(items)
        self._index = -1
        self._sharp = None
        self._native = None
        if self._items:
            self.show_index(max(0, min(current, len(self._items) - 1)))
        else:
            self._rendered_override = None
            self._displayed = QPixmap()
            self._label.clear()

    def items(self) -> List[ViewportItem]:
        return list(self._items)

    def current_index(self) -> int:
        return self._index

    def current_item(self) -> Optional[ViewportItem]:
        if 0 <= self._index < len(self._items):
            return self._items[self._index]
        return None

    def show_index(self, index: int) -> None:
        """Programmatic navigation — same pipeline as the arrow keys."""
        if not (0 <= index < len(self._items)):
            return
        self._index = index
        self._show_current()
        self._settle.start()
        self.current_changed.emit(index)

    def sharp_pixmap_info(self) -> Optional[Tuple[QPixmap, QSize]]:
        """The current item's sharp pixels + TRUE native dimensions
        (None while only a placeholder is up). 1:1 / zoom math reads
        the native size, never the pixmap's own."""
        if self._sharp is None or self._native is None:
            return None
        return self._sharp, self._native

    def refresh_current(self) -> None:
        """Re-run the display pipeline for the current item (hosts call
        this after an external change touched the file)."""
        if 0 <= self._index < len(self._items):
            self._show_current()
            self._settle.start()

    # ── the display pipeline ──────────────────────────────────────

    def _go(self, delta: int) -> None:
        nxt = self._index + delta
        if not self._items:
            return
        if not (0 <= nxt < len(self._items)):
            self.edge_reached.emit(1 if delta > 0 else -1)
            return
        self.show_index(nxt)

    def _show_current(self) -> None:
        item = self.current_item()
        if item is None:
            return
        # Moving off a video (to anything) tears the player down — the
        # arm-on-landing rule: nothing decodes video while flying.
        if self._video_armed is not None and (
                item.path is None or Path(item.path) != self._video_armed):
            self._disarm_video()
        # Navigation invalidates any host-rendered override (Edit's
        # developed view belongs to the item it was rendered for; the
        # host re-pushes after its prep lands — spec/63 §6.1).
        self._rendered_override = None
        self._sharp = None
        self._native = None
        # Belt-and-suspenders: drop the cached tiny so the new item's
        # backdrop rebuilds on next paint, even if the displayed
        # pixmap is kept up (the spec/63 "never blank the canvas"
        # path on a miss).
        self._invalidate_backdrop()
        self._af_point = None        # host re-feeds per photo (5a)
        self._halfres_cache = None   # per-photo RAW peaking source (5b)
        self._peaking_mask_cache = None
        self._update_inspect_btn()   # show/hide the corner magnifier
        if item.pixmap is not None:
            # Loose slide (separator/opener card) — already sharp.
            self._adopt_sharp(item.pixmap, item.pixmap.size())
            return
        if item.path is None:
            return                       # nothing to show — keep previous
        if item.kind == "video":
            # Poster only — the player arms on the settle beat, never
            # on a fly-by. Poster = host-supplied pixmap or the thumb
            # tier; else keep the previous frame (never blank-flicker).
            self._show_video_poster(item)
            return
        target = self._target_size()
        self._target_key = (target.width(), target.height())
        hit = self._cache.get_scaled_pixmap_if_cached(item.path, target)
        if hit is not None:
            self._adopt_sharp(hit[0], hit[1])
            return
        thumb = self._cache.get_thumb_pixmap_sync(item.path)
        if thumb is not None and not thumb.isNull():
            self._display(thumb)
        # else: keep the previous image — never blank the canvas.
        self._cache.request_scaled_pixmap(item.path, target, priority=0)

    def _show_video_poster(self, item: ViewportItem) -> None:
        # The QLabel stays the current stack widget; the QVideoWidget
        # (when present) is a separate raised sibling — hide it until
        # the player flips live so the poster reads clean.
        self._stack.setCurrentWidget(self._label)
        if self._video_widget is not None:
            self._video_widget.hide()
        poster = self._cache.get_thumb_pixmap_sync(item.path)
        if poster is not None and not poster.isNull():
            self._display(poster)
        # else: keep the previous frame up until the player flips live
        # (export-video thumbs land with the slice-8 builder).

    def _on_settle(self) -> None:
        self._prefetch_neighbours()
        item = self.current_item()
        if item is not None and item.kind == "video" and item.path is not None:
            self._arm_video(item.path)

    def _prefetch_neighbours(self) -> None:
        if not self._items:
            return
        target = self._target_size()
        for offset in _PREFETCH_OFFSETS:
            idx = self._index + offset
            if 0 <= idx < len(self._items):
                neighbour = self._items[idx]
                if (neighbour.path is not None and neighbour.pixmap is None
                        and neighbour.kind != "video"):
                    self._cache.request_scaled_pixmap(
                        neighbour.path, target, priority=1)

    # ── video: arm-on-landing ─────────────────────────────────────

    def _ensure_player(self) -> None:
        if self._player is not None:
            return
        from PyQt6.QtMultimediaWidgets import QVideoWidget
        from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
        # The QVideoWidget rides as a RAISED SIBLING of the stack —
        # not inside it — so the host can size it to the media's
        # letterbox rect instead of the full canvas. That keeps the
        # blurred backdrop visible in the bars (the native HWND only
        # paints black inside its own geometry on Windows).
        self._video_widget = QVideoWidget(self)
        self._video_widget.setAspectRatioMode(
            Qt.AspectRatioMode.KeepAspectRatio)
        self._video_widget.hide()
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(self._video_widget)
        # Apply the host-set volume + playback rate from before the
        # player armed (so the first video respects the transport bar's
        # initial slider/select values).
        self._audio.setVolume(self._video_volume)
        self._player.setPlaybackRate(self._video_rate)
        self._player.positionChanged.connect(self.video_position_changed.emit)
        self._player.durationChanged.connect(self.video_duration_changed.emit)
        self._player.playbackStateChanged.connect(self._on_playback_state)
        self._player.errorOccurred.connect(self._on_video_error)
        # First real frame → flip the poster to live video (the
        # no-black-frame guarantee: the poster holds until pixels flow).
        sink = self._player.videoSink()
        if sink is not None:
            sink.videoFrameChanged.connect(self._on_video_frame)

    def _arm_video(self, path: Path) -> None:
        if self._video_armed == Path(path):
            return
        self._ensure_player()
        self._video_armed = Path(path)
        self._video_live = False
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        if self._video_autoplay:
            self._player.play()
        self._video_playing = self._video_autoplay

    def _disarm_video(self) -> None:
        if self._player is not None:
            self._player.stop()
            self._player.setSource(QUrl())
        self._video_armed = None
        self._video_live = False
        self._video_playing = False
        if self._video_widget is not None:
            self._video_widget.hide()

    def _on_video_frame(self, frame) -> None:
        if self._video_live or self._video_armed is None:
            return
        if frame is not None and frame.isValid():
            item = self.current_item()
            if (item is not None and item.path is not None
                    and Path(item.path) == self._video_armed):
                self._video_live = True
                # Size + show + raise. The widget sits ON TOP of the
                # QLabel (which still paints the poster centered with
                # the same aspect — so the seam is invisible) and the
                # blurred backdrop shows in the bars around it.
                self._sync_video_widget_geometry()
                self._video_widget.show()
                self._video_widget.raise_()

    def _sync_video_widget_geometry(self) -> None:
        """Pin the out-of-stack QVideoWidget to the media's letterbox
        rect so QVideoWidget's opaque native bars don't paint over the
        blurred backdrop. Aspect ratio comes from the poster (host-
        supplied pixmap on the current item) — it matches the live
        video aspect by construction (the poster IS an extracted
        frame). Falls back to filling the canvas if the aspect is
        unknown — internal KeepAspectRatio then letterboxes the player
        with its own opaque black (rare; the next poster lands ASAP)."""
        if self._video_widget is None:
            return
        rect = self.video_widget_rect()
        self._video_widget.setGeometry(rect)

    def video_widget_rect(self) -> QRect:
        """The QVideoWidget's target rect: the QLabel's centered-pixmap
        rect — i.e. wherever the poster (or, for an arming-still video,
        the previous frame held up by spec/63's "never blank the
        canvas" rule) is currently drawn. Public because a test pin
        checks "video widget geometry == media rect, not full canvas"
        — that's the canvas-bar guarantee.

        Falling back to the full canvas only when there is truly no
        displayed pixmap — rare; the next poster lands within
        milliseconds via the thumb cache and re-pins the geometry
        through :meth:`_display` → :meth:`_sync_video_widget_geometry`.

        Why not item.pixmap? The viewport host (PickerPage) doesn't
        supply a per-item pixmap for videos — it lets the viewport's
        poster path resolve one through the PhotoCache. The label's
        own pixmap is the authoritative "what the user sees" source."""
        rect = self.image_rect_in_photo_area()
        label_rect = self._label.rect()
        if rect.isEmpty() or rect == label_rect:
            # No displayed pixmap or the pixmap fills the label —
            # fall back to the full canvas (KeepAspectRatio inside the
            # video widget letterboxes the player; the next poster
            # arrival pins it tighter).
            return self.rect()
        return rect

    def _on_playback_state(self, state) -> None:
        from PyQt6.QtMultimedia import QMediaPlayer
        self._video_playing = (
            state == QMediaPlayer.PlaybackState.PlayingState)
        self.video_playing_changed.emit(self._video_playing)

    def _on_video_error(self, err, msg: str = "") -> None:
        from PyQt6.QtMultimedia import QMediaPlayer
        try:
            if err == QMediaPlayer.Error.NoError:
                return
        except Exception:  # noqa: BLE001 — defensive over backends
            pass
        self.video_error.emit(str(msg) or str(err))

    # ── video: public timeline API (for surface scrubbers) ────────

    def set_video_autoplay(self, on: bool) -> None:
        self._video_autoplay = bool(on)

    def video_is_playing(self) -> bool:
        # Tracked bool, kept current by the real player's state signal —
        # the hot path never imports QtMultimedia (offscreen-safe).
        return self._video_playing

    def video_toggle_play(self) -> None:
        if self._player is None or self._video_armed is None:
            return
        if self._video_playing:
            self._player.pause()
            self._video_playing = False
        else:
            self._player.play()
            self._video_playing = True

    def video_seek(self, ms: int) -> None:
        if self._player is not None and self._video_armed is not None:
            self._player.setPosition(int(ms))

    def video_set_volume(self, percent: int) -> None:
        """0..100 → 0.0..1.0 on the held QAudioOutput. Cached so a
        change made before the player arms applies on the next clip."""
        v = max(0.0, min(1.0, int(percent) / 100.0))
        self._video_volume = v
        if self._audio is not None:
            self._audio.setVolume(v)

    def video_set_playback_rate(self, rate: float) -> None:
        """Set the player's playback rate (1.0 = real time). Cached so a
        change made before the player arms applies on the next clip."""
        r = max(0.05, float(rate))
        self._video_rate = r
        if self._player is not None:
            self._player.setPlaybackRate(r)

    def shutdown_video(self) -> None:
        """Tear the player down (hosts call on close)."""
        self._disarm_video()

    # ── F10: the truth view ───────────────────────────────────────

    def set_truth_internal(self, on: bool) -> None:
        """Let a surface (Edit) handle F10 itself — it shows the
        developed preview, not the original's pixels (spec/63 §4).
        When off, the viewport's own corner magnifier hides (the
        surface provides its own affordance)."""
        self._truth_internal = bool(on)
        self._update_inspect_btn()

    def _on_truth_requested(self) -> None:
        """F10 — full-resolution, fit-to-screen, no chrome (Nelson
        2026-06-12). Cards show their own pixels; videos have none to
        show. A deliberate action, so the full-res decode runs inline
        under a wait cursor."""
        if not self._truth_internal:
            return
        item = self.current_item()
        if item is None:
            return
        if item.kind == "video":
            # No full-res frame to inspect — and a host-supplied POSTER
            # pixmap must not open the lens either (it's a placeholder,
            # not the video's pixels). F10 is a no-op on video (§4).
            return
        from mira.ui.media.image_loader import _RAW_EXTENSIONS
        path = None
        is_raw = False
        if item.pixmap is not None:
            base = item.pixmap                     # a card — already pixels
        elif item.path is not None and item.kind != "video":
            path = Path(item.path)
            is_raw = path.suffix.lower() in _RAW_EXTENSIONS
            base = self._honest_full_res(path)
        else:
            return
        if base.isNull():
            return
        self._truth_window = _InspectView(
            base, self._af_point, path=path, is_raw=is_raw,
            with_tools=self._lens_tools, parent=self)
        # Windowed by default (Nelson 2026-06-12 UI round): best
        # resolution without the fullscreen takeover; F11 inside the
        # lens goes fullscreen when wanted.
        self._truth_window.open_windowed()
        self._truth_window.setFocus()

    def _honest_full_res(self, path: Path) -> QPixmap:
        """The inspection source: full-res JPEG/HEIC pixels, or the
        honest half-res demosaic for RAW (the embedded thumb is
        lens-corrected/sharpened — not what to scrutinise). A deliberate
        action, so it decodes inline under a wait cursor."""
        from mira.ui.media.image_loader import (
            _RAW_EXTENSIONS, load_pixmap, load_raw_half_res)
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            if path.suffix.lower() in _RAW_EXTENSIONS:
                img = load_raw_half_res(path)
                if not img.isNull():
                    return QPixmap.fromImage(img)
            return load_pixmap(path)
        finally:
            QApplication.restoreOverrideCursor()

    def _on_scaled_ready(self, path: Path, pixmap: QPixmap,
                         native: QSize) -> None:
        item = self.current_item()
        if (item is None or item.path is None
                or Path(path) != Path(item.path)):
            return                       # stale delivery — not ours anymore
        self._adopt_sharp(pixmap, native)

    def _on_decode_failed(self, path: Path) -> None:
        item = self.current_item()
        if item is None or item.path is None or Path(path) != Path(item.path):
            return
        # Honest blank: the current item has no decodable pixels (e.g.
        # a video before slice 6) — clearing beats a stale neighbour.
        self._displayed = QPixmap()
        self._label.clear()

    def _adopt_sharp(self, pixmap: QPixmap, native: QSize) -> None:
        self._sharp = pixmap
        self._native = QSize(native)
        self._display(pixmap)
        self.sharp_changed.emit()

    def _display(self, pm: QPixmap) -> None:
        self._displayed = pm
        self._invalidate_backdrop()
        self._fit()
        # If the live video widget is up, re-pin its geometry to the
        # new poster's letterbox rect. The poster can arrive after the
        # widget shows (cache miss on first landing → async fetch).
        if (self._video_widget is not None
                and not self._video_widget.isHidden()):
            self._sync_video_widget_geometry()

    # ── Blurred backdrop (mira/ui/design/blurred_backdrop — same
    # recipe BlurredPhotoCanvas uses on Cut surfaces). Soft fill in
    # the letterbox bars; the sharp pixmap centers over it. ─────────

    def _backdrop_source(self) -> Optional[QPixmap]:
        """The pixmap the blurred tiny is computed from.

        Prefer the sharp decode when we have it (best blur source for
        photos); else the currently displayed pixmap (thumb / poster /
        previous frame held while the next sharp lands); else the
        host-supplied loose-slide pixmap on the current item."""
        if self._sharp is not None and not self._sharp.isNull():
            return self._sharp
        if not self._displayed.isNull():
            return self._displayed
        item = self.current_item()
        if item is not None and item.pixmap is not None \
                and not item.pixmap.isNull():
            return item.pixmap
        return None

    def _invalidate_backdrop(self) -> None:
        """Drop the cached tiny so the next paint rebuilds it from the
        fresher source. Called on item change AND when a sharper
        pixmap lands (item arms with a thumb, then proxy / sharp
        replaces it — the backdrop should follow)."""
        self._backdrop_tiny = None
        self._backdrop_source_key = None
        self.update()

    def _fit(self) -> None:
        # A host-rendered override (Edit's developed view) displays
        # verbatim — peaking/AF never composite onto rendered pixels.
        override = self._rendered_override
        if override is not None and not override.isNull():
            base = override
        else:
            override = None
            base = self._peaking_base()
        if base.isNull():
            return
        size = self._label.size()
        if size.width() < 2 or size.height() < 2:
            size = self.size()
        if size.width() < 2 or size.height() < 2:
            return
        # Inner padding so the media never touches the canvas edge —
        # the soft blurred backdrop wraps it. The frame painted in
        # ``paintEvent`` lives just outside this rect so it sits
        # against the backdrop, never against the canvas border.
        pad = self._MEDIA_INNER_PAD
        inner = QSize(
            max(2, size.width() - 2 * pad),
            max(2, size.height() - 2 * pad),
        )
        scaled = base.scaled(
            inner,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        if override is None:
            if self._peaking_enabled:
                scaled = self._composite_peaking(scaled)
            # KeepAspectRatio → the scaled pixmap IS the image at
            # display size (ox=oy=0), so AF coords map directly onto it.
            if self._should_draw_af():
                self._draw_af_overlay(scaled)
        self._label.setPixmap(scaled)
        # Overlay hosts (crop tool, watermark) re-sync on this pulse —
        # the MediaCanvas contract (spec/63 §6.1).
        self._sync_exported_watermark_geometry()
        self.photo_geometry_changed.emit()
        # The media rect just moved — pin the video widget to it.
        if (self._video_widget is not None
                and not self._video_widget.isHidden()):
            self._sync_video_widget_geometry()

    # ── AF-point overlay (full-absorb 5a) ─────────────────────────

    def set_af_point(self, af: Optional[AfPoint]) -> None:
        """Feed the current photo's normalized AF point (host resolves
        brand profile + EXIF; None = no AF data)."""
        self._af_point = af
        self._fit()

    def set_af_overlay_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._af_overlay_enabled:
            return
        self._af_overlay_enabled = enabled
        self._fit()

    def is_af_overlay_enabled(self) -> bool:
        return self._af_overlay_enabled

    def af_point(self) -> Optional[AfPoint]:
        return self._af_point

    def _should_draw_af(self) -> bool:
        if not self._af_overlay_enabled or self._af_point is None:
            return False
        item = self.current_item()
        # Only on real photos — never on posters/cards/video.
        return (item is not None and item.kind == "photo"
                and item.pixmap is None)

    def _draw_af_overlay(self, target: QPixmap) -> None:
        """Paint the AF rectangle onto the display pixmap. (Zoom
        suppression — Nelson 2026-05-16 — arrives with box-zoom in 5c.)"""
        af = self._af_point
        dw, dh = target.width(), target.height()
        rw = max(2.0, af.w * dw)
        rh = max(2.0, af.h * dh)
        rx = af.cx * dw - rw / 2.0
        ry = af.cy * dh - rh / 2.0
        painter = QPainter(target)
        try:
            pen = QPen(self.palette().color(QPalette.ColorRole.Link))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawRect(int(round(rx)), int(round(ry)),
                             int(round(rw)), int(round(rh)))
        finally:
            painter.end()

    # ── focus peaking (full-absorb 5b) ────────────────────────────

    def set_peaking_enabled(self, enabled: bool) -> None:
        if self._peaking_enabled == bool(enabled):
            return
        self._peaking_enabled = bool(enabled)
        self._fit()

    def set_peaking_color(self, color_name: str) -> None:
        new_name = (color_name or "").strip().lower() or "magenta"
        if new_name == self._peaking_color_name:
            return
        self._peaking_color_name = new_name
        self._peaking_mask_cache = None
        if self._peaking_enabled:
            self._fit()

    def set_peaking_sensitivity(self, value: Optional[int]) -> None:
        if value is not None:
            value = max(0, min(100, int(value)))
        if value == self._peaking_sensitivity:
            return
        self._peaking_sensitivity = value
        self._peaking_mask_cache = None
        if self._peaking_enabled:
            self._fit()

    def set_stack_film_peaking(self, enabled: bool) -> None:
        if self._stack_film_peaking == bool(enabled):
            return
        self._stack_film_peaking = bool(enabled)
        self._peaking_mask_cache = None
        if self._peaking_enabled:
            self._fit()

    def is_peaking_enabled(self) -> bool:
        return self._peaking_enabled

    def is_stack_film_peaking(self) -> bool:
        return self._stack_film_peaking

    def peaking_color_name(self) -> str:
        return self._peaking_color_name

    def peaking_sensitivity(self) -> int:
        return self._effective_peaking_sensitivity()

    def _effective_peaking_sensitivity(self) -> int:
        if self._peaking_sensitivity is not None:
            return self._peaking_sensitivity
        from core.settings import load_settings
        return int(load_settings().get("peaking_sensitivity", 50))

    def _peaking_base(self) -> QPixmap:
        """The pixmap to display + peak. Normally the current display
        pixmap; for a RAW with peaking on (and not stack-film) the
        half-res sensor decode, so the shown and peaked pixels are
        identical (registration exact by construction)."""
        if self._peaking_enabled and not self._stack_film_peaking:
            half = self._halfres_pixmap()
            if half is not None and not half.isNull():
                return half
        return self._displayed

    def _halfres_pixmap(self) -> Optional[QPixmap]:
        """Lazy, per-photo half-res RAW decode (None for non-RAW /
        failure). Only ever called when peaking is on, so the slow
        decode never hits the navigation hot path."""
        from mira.ui.media.image_loader import (
            _RAW_EXTENSIONS, load_raw_half_res)
        item = self.current_item()
        if item is None or item.path is None:
            return None
        path = Path(item.path)
        if path.suffix.lower() not in _RAW_EXTENSIONS:
            return None
        key = str(path)
        if self._halfres_cache is not None and self._halfres_cache[0] == key:
            return self._halfres_cache[1]
        image = load_raw_half_res(path)
        if image.isNull():
            return None
        half = QPixmap.fromImage(image)
        self._halfres_cache = (key, half)
        return half

    def _single_photo_peaking_mask(self, scaled: QPixmap) -> QPixmap:
        from core.focus_peaking import (
            color_tuple_for_name, compute_peaking_absolute)
        color = color_tuple_for_name(self._peaking_color_name)
        sens = self._effective_peaking_sensitivity()
        return compute_peaking_absolute(
            scaled, color=color, sensitivity=sens).mask

    def _composite_peaking(self, scaled: QPixmap) -> QPixmap:
        """Peak ``scaled`` (the fitted base), caching the mask by
        (size, colour, sensitivity, stack-film) so a toggle/resize that
        doesn't change those stays instant."""
        if scaled.isNull():
            return scaled
        key = (scaled.width(), scaled.height(), self._peaking_color_name,
               self._effective_peaking_sensitivity(), self._stack_film_peaking)
        cached = self._peaking_mask_cache
        if cached is None or cached[0] != key:
            mask = self._single_photo_peaking_mask(scaled)
            self._peaking_mask_cache = (key, mask)
        else:
            mask = cached[1]
        if mask is None or mask.isNull():
            return scaled
        out = QPixmap(scaled.size())
        out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        try:
            painter.drawPixmap(0, 0, scaled)
            painter.drawPixmap(0, 0, mask)
        finally:
            painter.end()
        return out

    def _target_size(self) -> QSize:
        """Viewport size rounded UP to the 512-px quantum (stable cache
        keys across small resizes), floored at one quantum."""
        w = max(self.width(), 1)
        h = max(self.height(), 1)
        q = _TARGET_STEP
        return QSize(((w + q - 1) // q) * q, ((h + q - 1) // q) * q)

    # ── events ────────────────────────────────────────────────────

    def _position_inspect_btn(self) -> None:
        m = 10
        self._inspect_btn.move(
            max(0, self.width() - self._inspect_btn.width() - m),
            max(0, self.height() - self._inspect_btn.height() - m))
        self._inspect_btn.raise_()

    def _update_inspect_btn(self) -> None:
        item = self.current_item()
        # Inspectable = a real photo or a card; video has nothing
        # full-res to show (F10 is a no-op there).
        show = (self._corner_inspect_visible and self._truth_internal
                and item is not None and item.kind != "video")
        self._inspect_btn.setVisible(show)
        if show:
            self._position_inspect_btn()

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self._fit()
        self._position_inspect_btn()
        # Keep the manually-managed QVideoWidget pinned to the media's
        # letterbox rect (out-of-stack child; spec/63 + Nelson
        # 2026-06-15 canvas sweep).
        self._sync_video_widget_geometry()
        # A bucket change means the cached sharp no longer matches the
        # display target — re-request (cheap on hit, coalesced on miss).
        item = self.current_item()
        if (item is not None and item.path is not None
                and item.pixmap is None and item.kind != "video"):
            target = self._target_size()
            key = (target.width(), target.height())
            if key != self._target_key:
                self._target_key = key
                self._cache.request_scaled_pixmap(
                    item.path, target, priority=0)
                self._settle.start()

    def paintEvent(self, _ev) -> None:  # noqa: N802
        """Paint the blurred-cover backdrop behind the QLabel + the
        out-of-stack QVideoWidget, then a hairline frame just outside
        the media rect.

        The sharp media (centered QLabel pixmap; centered QVideoWidget
        for live video) paints on top — the backdrop fills the
        letterbox bars instead of a flat black canvas, and the 1 px
        frame separates the media from the soft backdrop. Per-frame
        cost is one scale of the cached 48×48 tiny (cheap on
        ``SmoothTransformation``) + one rect outline; the tiny
        downscale rides ``_invalidate_backdrop`` on item / sharp
        change."""
        from mira.ui.design.blurred_backdrop import blurred_cover, blurred_tiny
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = self.rect()
        # Build the tiny lazily on first paint per source. The source
        # key dedupes — same QPixmap object → same tiny.
        source = self._backdrop_source()
        source_key = source.cacheKey() if source is not None else None
        if source_key != self._backdrop_source_key:
            self._backdrop_tiny = blurred_tiny(source)
            self._backdrop_source_key = source_key
        cover = blurred_cover(self._backdrop_tiny, self.size())
        if cover is not None:
            bx = (self.width() - cover.width()) // 2
            by = (self.height() - cover.height()) // 2
            painter.drawPixmap(bx, by, cover)
        else:
            # No source yet — fall back to a neutral dark so the
            # canvas reads as "loading" rather than "broken".
            painter.fillRect(rect, QColor(20, 22, 30))
        # Hairline frame around the displayed media (Nelson
        # 2026-06-15 — "perceptible but not too thick"). Sits 1 px
        # outside the inset media rect so the QVideoWidget / QLabel
        # pixmap doesn't paint over it on the next frame.
        media_rect = self.image_rect_in_photo_area()
        if (not media_rect.isEmpty()
                and media_rect != self._label.rect()):
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(self._MEDIA_FRAME_COLOR, 1))
            painter.drawRect(media_rect.adjusted(-1, -1, 0, 0))
        painter.end()

    def focusNextPrevChild(self, next: bool) -> bool:  # noqa: N802, A002
        # Tab is TRANSPORT on photo surfaces (spec/63 §4), never focus
        # traversal.
        return False

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        key = ev.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self._go(-1)
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self._go(+1)
        elif key == Qt.Key.Key_P:
            self.pick_requested.emit()
        elif key == Qt.Key.Key_X:
            self.skip_requested.emit()
        elif key == Qt.Key.Key_Space:
            self.toggle_requested.emit()
        elif key == Qt.Key.Key_C:
            self.cycle_requested.emit()
        elif key == Qt.Key.Key_Tab:
            # Transport: play/pause the armed clip (inert on stills).
            self.video_toggle_play()
            self.transport_requested.emit()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.sweep_requested.emit()
        elif key == Qt.Key.Key_F10:
            self.truth_requested.emit()
        elif key in (Qt.Key.Key_F, Qt.Key.Key_F11):
            self.fullscreen_requested.emit()
        elif key == Qt.Key.Key_Escape:
            self.back_requested.emit()
        else:
            super().keyPressEvent(ev)
            return
        ev.accept()

    def wheelEvent(self, ev) -> None:  # noqa: N802
        self._wheel_units += ev.angleDelta().y()
        while self._wheel_units >= _WHEEL_STEP_UNITS:
            self._wheel_units -= _WHEEL_STEP_UNITS
            self._go(-1)
        while self._wheel_units <= -_WHEEL_STEP_UNITS:
            self._wheel_units += _WHEEL_STEP_UNITS
            self._go(+1)
        ev.accept()


def open_inspect_lens(
    pixmap: QPixmap,
    *,
    parent: Optional[QWidget] = None,
    path: Optional[Path] = None,
    with_tools: bool = False,
) -> _InspectView:
    """Open the standard F10 inspection lens on an ARBITRARY pixmap —
    for hosts without a viewport (Edit's processed/cropped full-res
    render before export, Nelson 2026-06-12). Same window everywhere:
    modal, aspect-locked, house-themed; F11 = the pure fullscreen
    look; Esc steps down one level. ``path`` feeds the honest title
    (name + pixel dimensions). Returns the window (keep a reference)."""
    view = _InspectView(
        pixmap, None, path=path, is_raw=False,
        with_tools=with_tools, parent=parent)
    view.open_windowed()
    view.setFocus()
    return view
