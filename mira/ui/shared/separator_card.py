"""Generated day-separator slides (spec/61 §4).

The plain card: the day's **date · location · description** from the
Collect-phase plan, rendered live — never stored, so a plan edit
propagates to every Cut automatically (the same derived-not-stored
pattern as #exported). One card per day boundary; it shows in the flat
grid, plays in the rehearsal, and exports as an image in sequence.

The look is deliberately slideshow-fixed (near-black ground, light
text) — these cards play on a TV, not inside the app theme. Future
styles (first-photo-with-label, map card) join behind the same
setting per spec/61 §4.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPixmap

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
) -> QImage:
    """The day card as a QImage (export writes it; the grid and the
    rehearsal scale it). ``card_style`` + ``seed_key`` pick the colours
    (deterministic — see :func:`card_colors`)."""
    bg, title_c, sub_c, desc_c = card_colors(card_style, seed_key)
    h = max(120, int(height))
    w = max(160, int(round(h * parse_aspect(aspect))))
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(bg)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    title = (tr("Day {n}").replace("{n}", str(day_number))
             if day_number is not None else tr("More moments"))
    sub = " · ".join(b for b in (date, location) if b)
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
) -> QImage:
    """The Cut OPENER (Nelson eyeball round 2): the show's first slide —
    the Cut's name big, then its facts (count · length, created date,
    target, music…). Same derived-not-stored posture and colour rules
    as the day separators."""
    bg, title_c, sub_c, desc_c = card_colors(card_style, seed_key)
    h = max(120, int(height))
    w = max(160, int(round(h * parse_aspect(aspect))))
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(bg)
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
