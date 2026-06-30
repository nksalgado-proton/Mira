"""Generated day-separator slides (spec/61 §4).

The plain card: the day's **date · location · description** from the
Collect-phase plan, rendered live — never stored, so a plan edit
propagates to every Cut automatically (the same derived-not-stored
pattern as #exported). One card per day boundary; it shows in the flat
grid, plays in the rehearsal, and exports as an image in sequence.

The look is deliberately slideshow-fixed (near-black ground, light
text) — these cards play on a TV, not inside the app theme. spec/155
landed the **map card** style parked here: when the trip_day has a
``map_image_path`` set, the separator renders that image letterboxed
(sharp inset over a blurred copy of itself, caption strip on top)
instead of the flat colour card; same for the event-level intro slide
when the event has a map attached.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap

from mira.ui.i18n import tr

#: Slideshow-fixed palette (not theme tokens — the card leaves the app).
_BG = QColor("#15171B")
_TITLE = QColor("#F2F3F5")
_SUB = QColor("#C9CDD4")
_DESC = QColor("#9AA0AA")

#: Card colour styles (a per-Cut choice, Nelson 2026-06-12): classic
#: black, ONE colour for the whole Cut, or a colour PER CARD.
CARD_STYLES = ("black", "single", "multi")


def card_colors(style: str, seed_key: str):
    """(bg, title, sub, desc) for one card.

    Colours are DETERMINISTIC from ``seed_key`` (the Cut id for
    'single', cut id + day for 'multi') so the grid tile, the
    rehearsal and the export always render the SAME card — random at
    creation, stable forever after. Hues spin the full wheel; the
    background stays medium-dark so the light text always reads."""
    if style not in ("single", "multi"):
        return _BG, _TITLE, _SUB, _DESC
    import zlib
    h = zlib.crc32(str(seed_key).encode("utf-8"))
    hue = h % 360
    sat = 140 + (h >> 9) % 80          # 140–219 of 255 — colourful, not neon
    val = 95 + (h >> 17) % 50          # 95–144 of 255 — deep enough for white
    bg = QColor.fromHsv(hue, sat, val)
    title = QColor("#F7F8FA")
    sub = QColor.fromHsv(hue, max(0, sat - 90), 235)
    desc = QColor.fromHsv(hue, max(0, sat - 110), 205)
    return bg, title, sub, desc


def parse_aspect(text: Optional[str]) -> float:
    """``"16:9"`` → 16/9. Junk or missing falls back to 16:9 — the card
    must always render."""
    try:
        w, h = str(text).replace("x", ":").split(":", 1)
        ratio = float(w) / float(h)
        if 0.2 <= ratio <= 5.0:
            return ratio
    except (ValueError, ZeroDivisionError, AttributeError):
        pass
    return 16.0 / 9.0


def _load_map_image(
    map_image_path: "Optional[Path | str]",
) -> Optional[QImage]:
    """Load the map slot file (JPEG/PNG) into a QImage, or return None
    when the path is missing/unreadable. Callers fall back to the flat
    colour card on None — the slide must always render."""
    if not map_image_path:
        return None
    img = QImage(str(map_image_path))
    if img.isNull():
        return None
    return img


def _composite_letterboxed_map(
    *,
    base: QImage,
    map_img: QImage,
    blur_passes: int = 3,
) -> None:
    """Paint the map's **sharp aspect-contain inset** onto ``base`` in
    place, with a solid white border. The flat card colour ``base`` was
    filled with stays visible around the inset (no blurred backdrop /
    matte). Mutates ``base``; no return value.

    spec/155 — Nelson 2026-06-30 dropped the blurred-cover matte step
    (the v1 design's "soft wash bezel") so the card looks flat behind
    the inset map: the play cut's BlurredPhotoCanvas already paints a
    flat-ish backdrop and the PTE export was bleeding the baked matte
    through around the 70 % video overlay. The ``blur_passes`` param is
    kept for API back-compat with callers that still pass it — it has
    no effect now."""
    cw, ch = base.width(), base.height()

    # Sharp inset (aspect-contain). Centred. The flat ``base`` colour
    # stays visible in the letterbox margins.
    inset = map_img.scaled(
        cw, ch,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    ix = (cw - inset.width()) // 2
    iy = (ch - inset.height()) // 2

    p = QPainter(base)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.drawImage(ix, iy, inset)
    # spec/155 — solid white 2 px border so image-map separators read
    # with the same crisp frame the video-map PTE overlay gets
    # (EnableBorder=1, BorderWidth=1.5, white in PTE). Consistency
    # across image / video separators + with the regular slide's
    # foreground photo border.
    p.setPen(QPen(QColor(255, 255, 255, 255), 2))
    p.drawRect(ix, iy, inset.width() - 1, inset.height() - 1)
    p.end()


def paint_video_thumb_overlay(
    *,
    base: QImage,
    thumb_path: "Path | str",
    scale: float = 0.70,
    center_y_frac: float = 0.575,
) -> None:
    """Composite the video's first-frame thumbnail on top of ``base``
    at PTE's :Video overlay geometry — 70 % canvas scale, centred
    horizontally, vertical centre at 57.5 % from the top. Solid white
    2 px border around the inset (matches PTE EnableBorder=1 +
    BorderWidth=1.5). Mutates ``base``; no-op if the thumb can't load.

    Used by the cut-grid renderer so a separator / opener with a video
    map still shows the slide's content at a glance (the bake itself
    stays flat — the live :Video overlay in PTE and the in-app video
    widget at play-time own the actual playback). spec/155 round 7
    follow-up — Nelson 2026-06-30."""
    if not thumb_path:
        return
    img = QImage(str(thumb_path))
    if img.isNull():
        return
    cw, ch = base.width(), base.height()
    bound_w = max(1, int(cw * scale))
    bound_h = max(1, int(ch * scale))
    inset = img.scaled(
        bound_w, bound_h,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    iw, ih = inset.width(), inset.height()
    ix = (cw - iw) // 2
    iy = int(ch * center_y_frac) - ih // 2
    p = QPainter(base)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.drawImage(ix, iy, inset)
    p.setPen(QPen(QColor(255, 255, 255, 255), 2))
    p.drawRect(ix, iy, iw - 1, ih - 1)
    p.end()


def paint_sep_caption_overlay(
    *,
    base: QImage,
    title: str,
    sub: str = "",
    title_y_frac: float = 0.09,
    sub_y_frac: float = 0.175,
    title_font_frac: float = 0.075,
    sub_font_frac: float = 0.030,
) -> None:
    """Paint the separator title + sub onto ``base`` at PTE-matching
    positions (top of canvas) — so a grid cell whose bake has its text
    at the vertical centre (covered by the 70 % video overlay) still
    shows the labels. spec/155 round 7c — Nelson 2026-06-30.

    Defaults mirror cut_play's _SEP_TITLE_CENTER_Y_FRAC + the PTE
    Position y values from trip_long.pte:

      title centre at 9 %  from top   (PTE y=-82)
      sub   centre at 17.5 % from top (PTE y=-65)
      title font at 7.5 % of canvas height (PTE ScaleX≈13-15)
      sub   font at 3.0 % of canvas height (PTE ScaleX=5)

    No background scrim — text rides over whatever's underneath
    (matches PTE's transparent-bg overlay)."""
    if not title and not sub:
        return
    w, h = base.width(), base.height()
    p = QPainter(base)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if title:
        title_px = max(10, int(h * title_font_frac))
        font = QFont("Segoe UI", title_px, QFont.Weight.Bold)
        p.setFont(font)
        fm = p.fontMetrics()
        title_h = fm.height()
        y = int(h * title_y_frac) - title_h // 2
        p.setPen(QColor(255, 255, 255, 255))
        p.drawText(QRect(0, y, w, title_h),
                   Qt.AlignmentFlag.AlignHCenter, title)
    if sub:
        sub_px = max(8, int(h * sub_font_frac))
        font = QFont("Segoe UI", sub_px)
        p.setFont(font)
        fm = p.fontMetrics()
        sub_h = fm.height()
        y = int(h * sub_y_frac) - sub_h // 2
        p.setPen(QColor(221, 221, 221, 255))  # #dddddd, matches cut_play
        p.drawText(QRect(0, y, w, sub_h),
                   Qt.AlignmentFlag.AlignHCenter, sub)
    p.end()


def _paint_caption_strip(
    *,
    base: QImage,
    title: str,
    sub: str = "",
    desc: str = "",
) -> None:
    """Bottom caption strip (~15% slide height) with title + optional
    sub + description, used by the map-card separator + opener variants
    so the day metadata stays visible over the map."""
    w, h = base.width(), base.height()
    strip_h = max(64, int(h * 0.16))
    p = QPainter(base)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.fillRect(0, h - strip_h, w, strip_h, QColor(0, 0, 0, 140))
    title_font = QFont("Segoe UI", max(10, int(strip_h * 0.32)), QFont.Weight.Bold)
    sub_font = QFont("Segoe UI", max(8, int(strip_h * 0.20)))
    p.setPen(QColor("#F2F3F5"))
    p.setFont(title_font)
    title_h = p.fontMetrics().height()
    body_h = title_h
    p.setFont(sub_font)
    sub_h = p.fontMetrics().height() if (sub or desc) else 0
    if sub or desc:
        body_h += sub_h
    y = h - strip_h + (strip_h - body_h) // 2
    p.setFont(title_font)
    p.drawText(QRect(0, y, w, title_h), Qt.AlignmentFlag.AlignHCenter, title)
    y += title_h
    if sub or desc:
        p.setPen(QColor(255, 255, 255, 200))
        p.setFont(sub_font)
        line = " · ".join(b for b in (sub, desc) if b)
        p.drawText(QRect(0, y, w, sub_h), Qt.AlignmentFlag.AlignHCenter, line)
    p.end()


def render_flat_background(
    *,
    aspect: str = "16:9",
    height: int = 720,
    card_style: str = "black",
    seed_key: str = "",
    map_image_path: "Optional[Path | str]" = None,
) -> QImage:
    """spec/153 — a text-less flat-colour slide background for the PTE
    export's opener / day-separator slides. The card's words ride as
    separate PTE ``:Text`` objects over this image, so the user can swap
    it for a map or a photo in PTE and keep the text. Uses the same
    deterministic background colour as :func:`render_separator_image` so
    the default look matches the in-app card.

    spec/155 — when ``map_image_path`` is set and the file loads, the
    background is the letterboxed map (blurred cover + sharp contain
    inset) instead of the flat colour. No caption text is painted —
    the PTE :Text objects still ride on top.
    """
    bg, _t, _s, _d = card_colors(card_style, seed_key)
    h = max(120, int(height))
    w = max(160, int(round(h * parse_aspect(aspect))))
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(bg)
    map_img = _load_map_image(map_image_path)
    if map_img is not None:
        _composite_letterboxed_map(base=img, map_img=map_img)
    return img


def render_separator_image(
    *,
    day_number: Optional[int],
    date: Optional[str] = None,
    location: Optional[str] = None,
    description: str = "",
    aspect: str = "16:9",
    height: int = 720,
    card_style: str = "black",
    seed_key: str = "",
    title: Optional[str] = None,
    map_image_path: "Optional[Path | str]" = None,
) -> QImage:
    """The day card as a QImage (export writes it; the grid and the
    rehearsal scale it). ``card_style`` + ``seed_key`` pick the colours
    (deterministic — see :func:`card_colors`). ``title`` overrides the
    headline (spec/154 — cross-event separators use the SOURCE EVENT name
    instead of "Day N"); when ``None`` the per-event "Day {n}" / "More
    moments" headline is used.

    spec/155 — when ``map_image_path`` is set and loads, the card
    becomes the letterboxed map (sharp inset + blurred bezels) with the
    same date / location / description appearing as a caption strip
    along the bottom. Falls back to the flat text card on missing /
    unreadable file."""
    bg, title_c, sub_c, desc_c = card_colors(card_style, seed_key)
    h = max(120, int(height))
    w = max(160, int(round(h * parse_aspect(aspect))))
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(bg)

    # spec/155 — letterboxed-map branch. The title text is composed
    # from the same metadata as the v1 text card, then drawn into a
    # bottom strip so the day metadata survives over the map.
    map_img = _load_map_image(map_image_path)
    if map_img is not None:
        _composite_letterboxed_map(base=img, map_img=map_img)
        if title:
            head = title
        elif isinstance(day_number, int):
            head = tr("Day {n}").replace("{n}", str(day_number))
        else:
            head = tr("More moments")
        # spec/155 — Nelson 2026-06-30 dropped the location field. Date
        # carries on its own as the sub line; description still rides
        # the third slot.
        sub_line = date or ""
        _paint_caption_strip(
            base=img, title=head, sub=sub_line,
            desc=(description or "").strip(),
        )
        return img

    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    if title:
        pass  # explicit override (spec/154 — cross-event source-event name)
    elif isinstance(day_number, int):
        title = tr("Day {n}").replace("{n}", str(day_number))
    else:
        # No override and no integer day (e.g. a cross-event (event, day)
        # token with an unresolved event name) → a neutral headline rather
        # than a garbled "Day (…)".
        title = tr("More moments")
    # spec/155 — Nelson 2026-06-30 dropped the location field.
    sub = date or ""
    desc = (description or "").strip()

    title_font = QFont("Segoe UI", max(10, int(h * 0.085)), QFont.Weight.Bold)
    sub_font = QFont("Segoe UI", max(8, int(h * 0.042)))
    desc_font = QFont("Segoe UI", max(8, int(h * 0.032)))

    # Vertical block centered as a whole: title / sub / description.
    p.setFont(title_font)
    title_h = p.fontMetrics().height()
    p.setFont(sub_font)
    sub_h = p.fontMetrics().height() if sub else 0
    p.setFont(desc_font)
    desc_line_h = p.fontMetrics().height()
    desc_h = min(3, max(0, len(desc) // 60 + 1)) * desc_line_h if desc else 0
    gap = int(h * 0.025)
    block_h = title_h + (gap + sub_h if sub else 0) + (gap + desc_h if desc else 0)
    y = (h - block_h) // 2

    p.setPen(title_c)
    p.setFont(title_font)
    p.drawText(QRect(0, y, w, title_h), Qt.AlignmentFlag.AlignHCenter, title)
    y += title_h
    if sub:
        y += gap
        p.setPen(sub_c)
        p.setFont(sub_font)
        p.drawText(QRect(0, y, w, sub_h), Qt.AlignmentFlag.AlignHCenter, sub)
        y += sub_h
    if desc:
        y += gap
        p.setPen(desc_c)
        p.setFont(desc_font)
        margin = int(w * 0.12)
        p.drawText(
            QRectF(margin, y, w - 2 * margin, desc_h + desc_line_h),
            Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignHCenter,
            desc)
    p.end()
    return img


def render_separator_pixmap(*, size: int, **kwargs) -> QPixmap:
    """Grid-thumb form: the same card rendered small (bounded by
    ``size`` on the long edge)."""
    img = render_separator_image(height=max(120, size), **kwargs)
    pm = QPixmap.fromImage(img)
    if pm.width() > size:
        pm = pm.scaledToWidth(size, Qt.TransformationMode.SmoothTransformation)
    return pm


def render_cut_opener_image(
    *,
    tag_text: str,
    lines: list,
    aspect: str = "16:9",
    height: int = 720,
    card_style: str = "black",
    seed_key: str = "",
    map_image_path: "Optional[Path | str]" = None,
) -> QImage:
    """The Cut OPENER (Nelson eyeball round 2): the show's first slide —
    the Cut's name big, then its facts (count · length, created date,
    target, music…). Same derived-not-stored posture and colour rules
    as the day separators.

    spec/155 — when ``map_image_path`` is set (the event-level map),
    the opener uses the letterboxed-map background with the title
    riding the caption strip."""
    bg, title_c, sub_c, desc_c = card_colors(card_style, seed_key)
    h = max(120, int(height))
    w = max(160, int(round(h * parse_aspect(aspect))))
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(bg)

    map_img = _load_map_image(map_image_path)
    if map_img is not None:
        _composite_letterboxed_map(base=img, map_img=map_img)
        first = str(lines[0]) if lines else ""
        rest = " · ".join(str(ln) for ln in lines[1:] if ln)
        _paint_caption_strip(
            base=img, title=str(tag_text), sub=first, desc=rest,
        )
        return img

    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    title_font = QFont("Segoe UI", max(10, int(h * 0.095)), QFont.Weight.Bold)
    line_font = QFont("Segoe UI", max(8, int(h * 0.038)))
    p.setFont(title_font)
    title_h = p.fontMetrics().height()
    p.setFont(line_font)
    line_h = p.fontMetrics().height()
    gap = int(h * 0.022)
    lines = [str(ln) for ln in lines if ln]
    block_h = title_h + sum(gap + line_h for _ in lines)
    y = (h - block_h) // 2

    p.setPen(title_c)
    p.setFont(title_font)
    p.drawText(QRect(0, y, w, title_h), Qt.AlignmentFlag.AlignHCenter, tag_text)
    y += title_h
    p.setFont(line_font)
    for i, ln in enumerate(lines):
        y += gap
        p.setPen(sub_c if i == 0 else desc_c)
        p.drawText(QRect(0, y, w, line_h), Qt.AlignmentFlag.AlignHCenter, ln)
        y += line_h
    p.end()
    return img


def cut_opener_lines(
    cut, totals, photo_s: float, transition_s: float = 0.0,
) -> list:
    """The opener's fact lines, composed in ONE place (grid tile, the
    rehearsal and the export all show the same card).

    Spec/81 reshape: a Cut no longer carries ``style_filter_json``
    (filters moved to the source DC). The opener now reads only the
    Cut's own self-describing facts — count·length, target, music,
    created date. Showing the source DC's styles is a later refinement
    (callers would need to thread the DC's filters through).

    spec/152 §3 — ``transition_s`` is added to every non-video slide's
    contribution to ``totals.seconds``. Defaults to 0 for back-compat
    with pre-152 callers; rehearsal / export call sites pass the
    per-Cut value (per-Cut wins over Settings default)."""

    def _mmss(seconds: float) -> str:
        s = max(0, int(round(seconds)))
        return f"{s // 60}:{s % 60:02d}"

    n = totals.photo_count + totals.video_count
    lines = [tr("{n} items · {len}").replace("{n}", str(n)).replace(
        "{len}", _mmss(totals.seconds(photo_s, transition_s)))]
    bits = []
    if cut.target_s:
        bits.append(tr("target {t}").replace("{t}", _mmss(cut.target_s)))
    if cut.music_category:
        bits.append(tr("music: {c}").replace("{c}", str(cut.music_category)))
    if bits:
        lines.append(" · ".join(bits))
    if cut.created_at:
        lines.append(tr("created {d}").replace(
            "{d}", str(cut.created_at)[:10]))
    return lines
