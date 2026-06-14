"""BarChartWidget — horizontal bar chart for the Curate Overview.

Renders one row per bucket. Each row carries:

* A short text label on the left (the bucket name)
* A coloured bar in the middle whose length is proportional to
  the bucket's value
* A right-aligned value-text label (count + formatted duration)

Used by :class:`ShareOverviewPage` to show projected slideshow
durations across the canonical buckets (All-Time Best / preferred
genres / Short / Medium / Long / Compositions / Collage Only /
Discarded). Reusable for any other "one bar per category"
visualisation — pass any ``rows`` to :meth:`set_rows`.

Colours come from the same palette :class:`CategoryPieWidget`
uses so the dashboard reads with one visual language. Empty
state ("No data yet") renders as a single faint placeholder
line so the layout reserves its space without an awkward void.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

log = logging.getLogger(__name__)


# Borrow CategoryPieWidget's palette so the dashboard reads with
# one visual language. Re-imported as a tuple of QColors so widget
# code doesn't depend on the pie module's internal symbol.
_PALETTE: tuple[QColor, ...] = (
    QColor("#3AA5D9"),     # Gulf blue
    QColor("#F37021"),     # Gulf orange
    QColor("#4ADE80"),     # green
    QColor("#A78BFA"),     # purple
    QColor("#FBBF24"),     # amber
    QColor("#F472B6"),     # pink
    QColor("#22D3EE"),     # cyan
    QColor("#FB7185"),     # rose
    QColor("#94A3B8"),     # slate (for trailing rows / Discarded)
)
_EMPTY_COLOR = QColor("#E0E0E0")


@dataclass(frozen=True)
class BarRow:
    """One row in the chart.

    ``label`` — bucket name (rendered left-aligned).
    ``value`` — number used to compute the bar's width (must be
        ≥ 0; the chart normalises against the row with the
        largest ``value``).
    ``value_text`` — right-aligned annotation (e.g. ``"12 · 2m 0s"``).
    """

    label: str
    value: float
    value_text: str = ""


# Padding + sizing constants — tuned for an Overview surface
# sitting in a column ~480-720 px wide. Heights play with QSS
# row spacing; the QPainter draws inside a fixed row band.
_LABEL_WIDTH = 140        # left-aligned label column width
_VALUE_WIDTH = 120        # right-aligned value-text column width
_ROW_HEIGHT = 28
_ROW_GAP = 6
_BAR_INSET_Y = 6          # vertical inset inside each row
_TEXT_PAD = 4


class BarChartWidget(QWidget):
    """Vertical-stack horizontal bars.

    Public API:
      :meth:`set_rows` — replace the chart data (list of
        :class:`BarRow`).

    The chart resizes vertically to fit ``len(rows)``; horizontal
    width is determined by the parent layout (the bars stretch to
    fill).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[BarRow] = []
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.MinimumExpanding,
        )
        # Minimum width so labels + bars + value text all fit.
        self.setMinimumWidth(_LABEL_WIDTH + 80 + _VALUE_WIDTH)
        # Minimum height for ~9 canonical Curate buckets so the
        # widget can't be collapsed to zero pixels by a layout
        # squeeze (Nelson 2026-05-21: user reported the Overview
        # showing only the title + hint with no visible chart). The
        # floor is generous — sizeHint grows from here when more
        # rows are added.
        self.setMinimumHeight(
            9 * (_ROW_HEIGHT + _ROW_GAP) + _ROW_GAP)

    # ── Public API ────────────────────────────────────────────────

    def set_rows(self, rows: list[BarRow]) -> None:
        """Replace the chart data. Empty list shows an empty-state
        placeholder bar."""
        self._rows = list(rows)
        # Force the geometry to update — sizeHint changes with row
        # count.
        self.updateGeometry()
        self.update()

    # ── Qt overrides ──────────────────────────────────────────────

    def sizeHint(self):                          # noqa: N802
        n = max(1, len(self._rows))
        h = n * (_ROW_HEIGHT + _ROW_GAP) + _ROW_GAP
        return self.minimumSize().expandedTo(
            self.minimumSize().__class__(
                self.minimumWidth(), h,
            )
        )

    def minimumSizeHint(self):                   # noqa: N802
        n = max(1, len(self._rows))
        h = n * (_ROW_HEIGHT + _ROW_GAP) + _ROW_GAP
        return self.minimumSize().__class__(
            self.minimumWidth(), h,
        )

    def paintEvent(self, _event) -> None:        # noqa: N802
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if not self._rows:
                self._paint_empty(p)
                return
            max_value = max((r.value for r in self._rows), default=0)
            for i, row in enumerate(self._rows):
                y = i * (_ROW_HEIGHT + _ROW_GAP) + _ROW_GAP // 2
                self._paint_row(p, row, y, max_value, i)
        finally:
            p.end()

    # ── Painting helpers ──────────────────────────────────────────

    def _paint_row(
        self, p: QPainter, row: BarRow, y: int,
        max_value: float, idx: int,
    ) -> None:
        w = self.width()
        # Label column.
        label_rect = QRectF(_TEXT_PAD, y, _LABEL_WIDTH, _ROW_HEIGHT)
        p.setPen(self.palette().text().color())
        p.setFont(_label_font(p.font()))
        p.drawText(
            label_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            row.label,
        )
        # Bar column — between label and value-text.
        bar_left = _LABEL_WIDTH + _TEXT_PAD * 2
        bar_right = w - _VALUE_WIDTH - _TEXT_PAD
        bar_w = max(0.0, bar_right - bar_left)
        bar_y = y + _BAR_INSET_Y
        bar_h = _ROW_HEIGHT - 2 * _BAR_INSET_Y
        # Bar track (faint background).
        track = QColor(_EMPTY_COLOR)
        track.setAlpha(90)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(
            QRectF(bar_left, bar_y, bar_w, bar_h),
            bar_h / 2.0, bar_h / 2.0,
        )
        # Bar fill — proportional to value / max_value.
        if max_value > 0 and row.value > 0:
            frac = min(1.0, row.value / max_value)
            fill_w = bar_w * frac
            color = _PALETTE[idx % len(_PALETTE)]
            p.setBrush(color)
            p.drawRoundedRect(
                QRectF(bar_left, bar_y, fill_w, bar_h),
                bar_h / 2.0, bar_h / 2.0,
            )
        # Value-text column on the right.
        if row.value_text:
            text_rect = QRectF(
                bar_right + _TEXT_PAD, y,
                _VALUE_WIDTH - _TEXT_PAD, _ROW_HEIGHT,
            )
            p.setFont(_value_font(p.font()))
            sec_pen = QPen(self.palette().text().color())
            # palette() is a QWidget method, not QPainter — the legacy port
            # had ``p.palette()`` here which throws AttributeError on PyQt6
            # the moment the chart paints a row with value_text.
            sec_pen.setColor(_dim(self.palette().text().color()))
            p.setPen(sec_pen)
            p.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignRight
                | Qt.AlignmentFlag.AlignVCenter,
                row.value_text,
            )

    def _paint_empty(self, p: QPainter) -> None:
        """Empty-state placeholder — one faint bar + a hint
        message so the layout reads as "ready, waiting for data"
        rather than broken."""
        h = self.height()
        msg_rect = QRectF(0, h / 2 - 12, self.width(), 24)
        p.setPen(_dim(self.palette().text().color()))
        p.setFont(_value_font(p.font()))
        p.drawText(
            msg_rect,
            Qt.AlignmentFlag.AlignCenter,
            "No data yet — tag photos to see projections.",
        )


def _label_font(base: QFont) -> QFont:
    f = QFont(base)
    f.setBold(True)
    return f


def _value_font(base: QFont) -> QFont:
    f = QFont(base)
    return f


def _dim(c: QColor) -> QColor:
    out = QColor(c)
    out.setAlpha(160)
    return out


# ── Helpers for the Curate Overview ───────────────────────────


def format_duration_seconds(seconds: float) -> str:
    """Format seconds as a compact human-readable duration.

    Examples:
      45.0    → ``"45s"``
      90.0    → ``"1m 30s"``
      3725.0  → ``"1h 2m"`` (drop seconds at the hour boundary)
      0.0     → ``"0s"``

    Used by the Overview's bar-chart row value-text so a
    50-photo Long bucket reads as ``"50 · 8m 20s"`` rather than
    ``"50 · 500s"``."""
    s = max(0, int(round(seconds)))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        m, sec = divmod(s, 60)
        return f"{m}m {sec}s" if sec else f"{m}m"
    h, rem = divmod(s, 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h {m}m" if m else f"{h}h"
