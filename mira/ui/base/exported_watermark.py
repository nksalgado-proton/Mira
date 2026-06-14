"""``ExportedWatermark`` — the diagonal "Exported" overlay (spec/59 §8).

A translucent diagonal text painted over a photo wherever it displays
(Day Grid / cluster sub-grid tiles, the Edit individual view) when an
exported or externally-associated version of that photo exists — the
``lineage`` table is the driver, NOT ``Adjustment.edit_exported`` (that
flag is *freshness*: it resets on every adjustment change and keeps its
own chip).

System-set: there is no per-item toggle and the overlay never takes the
mouse. The only control is the app-wide ``show_exported_watermark``
setting (hosts simply never show the overlay when it's off).

Painted (no QSS role): the colours are image-relative — white text with
a dark soft shadow reads on any photo and is theme-independent, the same
reasoning as the Day Grid ▶ play overlay. The text rotates along the
widget's own diagonal so it reads bottom-left → top-right at every
aspect ratio, and the font scales with the widget so tiles and the full
canvas both stay legible.
"""
from __future__ import annotations

import math
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPaintEvent
from PyQt6.QtWidgets import QWidget

from mira.ui.i18n import tr

# Text alpha — translucent enough to never hide the image, opaque
# enough to read at a glance.
_TEXT_ALPHA = 150
_SHADOW_ALPHA = 120
# The text spans roughly this fraction of the widget diagonal.
_DIAGONAL_FILL = 0.66
# Font floor/cap so 40 px grid tiles and a 2000 px canvas both work.
_MIN_FONT_PX = 9
_MAX_FONT_PX = 96


def watermark_text() -> str:
    """The user-visible watermark string (one place, translated)."""
    return tr("Exported")


class ExportedWatermark(QWidget):
    """Transparent child widget that paints the diagonal "Exported"
    text across its own rect. The host owns geometry (full image rect)
    and visibility (lineage membership × the app-wide setting)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # Never a click target — the photo/cell underneath keeps every
        # interaction (border zones, centre clicks, crop drags).
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setVisible(False)

    def paintEvent(self, ev: QPaintEvent) -> None:  # noqa: N802
        w, h = self.width(), self.height()
        if w <= 1 or h <= 1:
            return
        text = watermark_text()
        if not text:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        # Rotate along the widget's own diagonal (bottom-left rising to
        # top-right). Qt's y-axis points down, so the rise is negative.
        angle_deg = math.degrees(math.atan2(h, w))
        diagonal = math.hypot(w, h)

        font = QFont(self.font())
        font.setBold(True)
        # Size the text to span ~_DIAGONAL_FILL of the diagonal: start
        # from a rough per-char width (~0.6 em for a bold latin face)
        # and clamp to the floor/cap.
        approx_px = (diagonal * _DIAGONAL_FILL) / max(1, len(text)) / 0.6
        font.setPixelSize(int(max(_MIN_FONT_PX, min(_MAX_FONT_PX, approx_px))))
        painter.setFont(font)

        painter.translate(w / 2.0, h / 2.0)
        painter.rotate(-angle_deg)

        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(text)
        # Baseline-centred: descent/ascent midpoint keeps the text on
        # the rotated centre line.
        ty = (fm.ascent() - fm.descent()) / 2.0
        shadow_off = max(1.0, font.pixelSize() / 24.0)

        painter.setPen(QColor(0, 0, 0, _SHADOW_ALPHA))
        painter.drawText(
            int(-tw / 2 + shadow_off), int(ty + shadow_off), text)
        painter.setPen(QColor(255, 255, 255, _TEXT_ALPHA))
        painter.drawText(int(-tw / 2), int(ty), text)
        painter.end()


__all__ = ["ExportedWatermark", "watermark_text"]
