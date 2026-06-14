"""PickedRatioDonut — survival-rate donut for the dashboard.

Used on the Cull and Process PhaseButton cards:

* **Cull** — green slice = ``kept_in_cull_count`` / total captured;
  gray slice = the rest (discarded). Centre text: ``N/M (P%)``.
* **Process** — green slice = ``kept_in_process_count`` / total
  captured; gray slice = the rest.

Same shape and font sizing as the existing :class:`_DonutWidget` so
the dashboard reads consistently across all six PhaseButtons.

Pure Qt; no business logic beyond clamping. Callers compute the
counts via :mod:`core.event_metrics` and pass them in.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from mira.ui.i18n import tr


# Matches the existing donut + EventCard heatmap colours so the
# whole dashboard reads with one language.
_COLOR_KEPT = QColor("#4ADE80")          # green-400
_COLOR_DISCARDED = QColor("#C53030")     # red — same as _C_DISCARD in StatusBreakdown

# Callout-line colour — softer than the slice colours so the
# leader doesn't draw attention away from the slice itself.
_LEADER_LINE_COLOR = QColor(80, 80, 80, 180)

# Skip the callout for a slice smaller than this fraction (the
# label would land at the wrong end of the donut and look
# disconnected). At 0% / 100% only one label appears.
_LABEL_MIN_FRACTION = 0.01


class PickedRatioDonut(QWidget):
    """Donut showing "kept / total" survival.

    Public API:
      * :meth:`set_ratio` — ``(kept, total)``. Clamped so kept ≤ total.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kept = 0
        self._total = 0
        # Smaller minimum than legacy 80 so the donut shrinks WITH the card
        # when the window narrows (Nelson 2026-06-06 eyeball: at 80 floor the
        # donuts overflowed the activity card border when the window squeezed).
        self.setMinimumSize(40, 40)
        self.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding,
            QSizePolicy.Policy.MinimumExpanding,
        )

    def set_ratio(self, kept: int, total: int) -> None:
        """Update the donut. ``kept`` is clamped to ``[0, total]``."""
        self._total = max(0, int(total))
        self._kept = max(0, min(int(kept), self._total))
        self._refresh_tooltip()
        self.update()

    # ── Qt overrides ──────────────────────────────────────────────

    def heightForWidth(self, w: int) -> int:    # noqa: N802
        return w

    def hasHeightForWidth(self) -> bool:        # noqa: N802
        return True

    def paintEvent(self, _event) -> None:       # noqa: N802
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            # Same margin convention as CategoryPieWidget: donut at
            # 55% of the widget, the rest reserved for callout
            # labels around the perimeter.
            side = min(self.width(), self.height())
            donut_side = side * 0.55
            x = (self.width() - donut_side) / 2
            y = (self.height() - donut_side) / 2
            outer_rect = QRectF(x, y, donut_side, donut_side)
            ring_w = donut_side * 0.22
            half = ring_w / 2.0
            arc_rect = outer_rect.adjusted(half, half, -half, -half)

            pen = QPen()
            pen.setWidthF(ring_w)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)

            full_circle = 360 * 16

            if self._total == 0:
                pen.setColor(_COLOR_DISCARDED)
                p.setPen(pen)
                p.drawArc(arc_rect, 0, full_circle)
                self._draw_centre_dash(p, outer_rect)
                return

            # Background ring = discarded slice (covers the whole
            # circle then the kept slice paints OVER its share).
            pen.setColor(_COLOR_DISCARDED)
            p.setPen(pen)
            p.drawArc(arc_rect, 0, full_circle)

            # Kept slice from 12 o'clock clockwise.
            kept_frac = self._kept / self._total
            skipped_frac = 1.0 - kept_frac
            kept_span = -int(round(kept_frac * full_circle))
            if kept_span != 0:
                pen.setColor(_COLOR_KEPT)
                p.setPen(pen)
                p.drawArc(arc_rect, 90 * 16, kept_span)

            # Callout labels — only for slices that actually exist.
            # All-kept (100%) or all-discarded (0%) gets one label.
            kept_start = 90 * 16
            skip_start = kept_start + kept_span
            skip_span = -int(round(skipped_frac * full_circle))
            slices: list[tuple[str, float, int, int]] = []
            if kept_frac >= _LABEL_MIN_FRACTION:
                slices.append((tr("Picked"), kept_frac, kept_start, kept_span))
            if skipped_frac >= _LABEL_MIN_FRACTION:
                slices.append((
                    tr("Skipped"), skipped_frac, skip_start, skip_span,
                ))
            self._draw_callouts(p, outer_rect, slices)
        finally:
            p.end()

    # ── Helpers ───────────────────────────────────────────────────

    def _draw_centre_dash(
        self, painter: QPainter, outer_rect: QRectF,
    ) -> None:
        font = QFont(painter.font())
        font.setBold(True)
        side = min(outer_rect.width(), outer_rect.height())
        font.setPointSizeF(max(9.0, side * 0.16))
        painter.setFont(font)
        painter.setPen(self.palette().text().color())
        painter.drawText(
            outer_rect, Qt.AlignmentFlag.AlignCenter, "—",
        )

    def _draw_callouts(
        self,
        painter: QPainter,
        outer_rect: QRectF,
        slice_geom: list[tuple[str, float, int, int]],
    ) -> None:
        """Mirror CategoryPieWidget's callout pattern: a short
        leader line outward from each slice's midpoint angle plus a
        text label clamped inside the widget bounds. Smaller fonts
        than the old centre-text version (Nelson 2026-05-21)."""
        cx = outer_rect.center().x()
        cy = outer_rect.center().y()
        r_outer = outer_rect.width() / 2.0
        stub_len = max(6.0, r_outer * 0.16)

        side = min(self.width(), self.height())
        font = QFont(painter.font())
        font.setPointSizeF(max(8.0, side * 0.075))
        font.setBold(False)
        painter.setFont(font)
        fm = painter.fontMetrics()

        widget_left = 4.0
        widget_right = float(self.width() - 4)

        for label, _fraction, start_16, span_16 in slice_geom:
            mid_16 = start_16 + span_16 / 2.0
            mid_deg = mid_16 / 16.0
            rad = math.radians(mid_deg)
            cos_t = math.cos(rad)
            sin_t = math.sin(rad)
            x_outer = cx + r_outer * cos_t
            y_outer = cy - r_outer * sin_t
            x_stub = cx + (r_outer + stub_len) * cos_t
            y_stub = cy - (r_outer + stub_len) * sin_t

            pen = QPen(_LEADER_LINE_COLOR)
            pen.setWidth(1)
            painter.setPen(pen)
            painter.drawLine(
                QPointF(x_outer, y_outer),
                QPointF(x_stub, y_stub),
            )

            text_w = fm.horizontalAdvance(label)
            text_h = fm.height()
            gap = 3.0
            if cos_t >= 0:
                tx = x_stub + gap
                align = Qt.AlignmentFlag.AlignLeft
            else:
                tx = x_stub - gap - text_w
                align = Qt.AlignmentFlag.AlignRight
            ty = y_stub - text_h / 2.0

            if tx < widget_left:
                tx = widget_left
            if tx + text_w > widget_right:
                tx = widget_right - text_w

            painter.setPen(self.palette().text().color())
            painter.drawText(
                QRectF(tx, ty, text_w + 4, text_h),
                align | Qt.AlignmentFlag.AlignVCenter,
                label,
            )

    def _refresh_tooltip(self) -> None:
        if self._total == 0:
            self.setToolTip("")
            return
        pct = int(round(100 * self._kept / max(1, self._total)))
        self.setToolTip(
            f"{self._kept} kept out of {self._total} ({pct}%)"
        )
