"""Donut — ring chart for phase summary cards.

Used on Surface 03 (Phases) — one donut per phase card, ~140px. Two modes:

    Active     filled ring with proportional slices (e.g. per-camera
               contribution for Collect; picked/skipped for Pick) +
               optional center text (the total count or the dominant %).

    Empty      hollow track-colored ring with the state word centered
               ("Not started", "Skipped", etc). The track ring is kept
               neutral — red is reserved for the FIXED photo-skipped
               state per design-system §5a; the donut speaks about
               aggregate phase status, not per-photo state.

Painted via QPainter.drawArc directly; no QtCharts dependency. Colors
read from the active palette at paint time so theme toggles need no
QSS resync.
"""
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QSizePolicy, QWidget

from mira.ui.palette import PALETTE


@dataclass
class DonutSlice:
    """One wedge: label is for legends (not painted on the donut), value
    is the proportional weight, color is a hex string (often resolved
    from PALETTE at the caller's site so theme toggles can rebuild)."""

    label: str
    value: float
    color: str


class Donut(QWidget):
    """Phase-summary donut. Set ``slices`` for the active state or call
    :meth:`setEmptyState` with a state word ('Not started', 'Skipped',
    'Done', etc.) for the inactive look.

    Ring thickness defaults to 18px; tune via ``setRingThickness``. Center
    text auto-scales by the widget size."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        ring_thickness: int = 18,
    ) -> None:
        super().__init__(parent)
        self._slices: list[DonutSlice] = []
        self._empty_state: str | None = None
        self._center_text: str = ""
        self._center_sub: str = ""
        self._ring_thickness = ring_thickness
        self.setMinimumSize(QSize(120, 120))
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

    # ── public API ─────────────────────────────────────────────────────

    def setSlices(self, slices: list[DonutSlice]) -> None:
        self._slices = list(slices)
        self._empty_state = None
        self.update()

    def setEmptyState(self, word: str) -> None:
        """Display the hollow track ring with ``word`` centered.

        Word should be short (1–2 words). Common: 'Not started', 'Skipped',
        'Done'."""
        self._slices = []
        self._empty_state = word
        self.update()

    def setCenterText(self, text: str, sub: str = "") -> None:
        """Center label — usually a count ('1284') or percentage ('62%').
        Optional ``sub`` reads below in a smaller weight."""
        self._center_text = text
        self._center_sub = sub
        self.update()

    def setRingThickness(self, px: int) -> None:
        self._ring_thickness = max(4, int(px))
        self.update()

    # ── paint ──────────────────────────────────────────────────────────

    def paintEvent(self, _evt) -> None:  # noqa: N802
        app = QApplication.instance()
        mode = (app.property("theme") if app else None) or "dark"
        p = PALETTE[mode]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        side = min(self.width(), self.height()) - 6
        rect = QRectF(
            (self.width() - side) / 2,
            (self.height() - side) / 2,
            side, side,
        )
        ring_w = max(8, min(self._ring_thickness, int(side * 0.18)))

        if self._empty_state is not None:
            # Hollow track ring + centered state word
            pen = QPen(QColor(p["track"]), ring_w)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            inset = ring_w / 2
            painter.drawArc(
                rect.adjusted(inset, inset, -inset, -inset),
                0, 360 * 16,
            )
            self._paint_state_word(painter, rect, p)
            painter.end()
            return

        # Slices
        total = sum(s.value for s in self._slices) or 1.0
        # Qt drawArc angles are 16ths of a degree, anti-clockwise from 3 o'clock.
        # Start at 12 o'clock (90°), go clockwise (negative span).
        start_angle = 90 * 16
        inset = ring_w / 2
        arc_rect = rect.adjusted(inset, inset, -inset, -inset)
        for s in self._slices:
            if s.value <= 0:
                continue
            span = -int(360 * 16 * (s.value / total))
            pen = QPen(QColor(s.color), ring_w)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(pen)
            painter.drawArc(arc_rect, start_angle, span)
            start_angle += span

        self._paint_center_text(painter, rect, p)
        painter.end()

    def _paint_state_word(
        self, painter: QPainter, rect: QRectF, palette: dict[str, str]
    ) -> None:
        painter.setPen(QColor(palette["ink_soft"]))
        f = self._fit_font(int(rect.width() * 0.13), weight=700)
        painter.setFont(f)
        painter.drawText(
            rect, int(Qt.AlignmentFlag.AlignCenter), self._empty_state or ""
        )

    def _paint_center_text(
        self, painter: QPainter, rect: QRectF, palette: dict[str, str]
    ) -> None:
        if not self._center_text and not self._center_sub:
            return
        cx = rect.center().x()
        cy = rect.center().y()
        if self._center_text:
            painter.setPen(QColor(palette["ink"]))
            f = self._fit_font(int(rect.width() * 0.20), weight=800)
            painter.setFont(f)
            painter.drawText(
                QRectF(rect.x(), cy - rect.height() * 0.25,
                       rect.width(), rect.height() * 0.5),
                int(Qt.AlignmentFlag.AlignCenter),
                self._center_text,
            )
        if self._center_sub:
            painter.setPen(QColor(palette["ink_soft"]))
            f = self._fit_font(int(rect.width() * 0.10), weight=600)
            painter.setFont(f)
            painter.drawText(
                QRectF(rect.x(), cy + rect.height() * 0.10,
                       rect.width(), rect.height() * 0.25),
                int(Qt.AlignmentFlag.AlignCenter),
                self._center_sub,
            )

    @staticmethod
    def _fit_font(px: int, *, weight: int) -> QFont:
        f = QFont()
        f.setPixelSize(max(10, int(px)))
        f.setWeight(weight)
        return f
