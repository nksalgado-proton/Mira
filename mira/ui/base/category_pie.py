"""CategoryPieWidget — generic pie chart for the dashboard.

Used on the per-event PhaseButton cards for the Capture phase
(``%photos per camera``) and the Select phase (``%style among
kept``). The shape mirrors :class:`_DonutWidget` from
:mod:`ui.pages.phase_button`: square, ``heightForWidth`` aspect,
centre text, but the segments are coloured by category instead of
by progress state.

The widget owns its own colour palette so callers don't have to
provide one — colours are picked deterministically from
:data:`_PALETTE` in dict-iteration order. For the small N we
typically render (≤6 cameras, ≤6 styles) the palette gives clearly
distinguishable hues.

The pie face also serves as a tooltip: hovering shows the per-
segment legend ``category — N (P%)`` so the user can read the
exact counts without a separate legend strip.
"""

from __future__ import annotations

import logging
import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

log = logging.getLogger(__name__)


# Gulf-livery + neutral palette. The first four hues are the
# brand's primary blue / orange variants from
# ``assets/themes/light.qss``; the rest cover larger N. Picked
# to be distinguishable at small donut size — same hue range
# Material's chart palette uses.
_PALETTE: tuple[QColor, ...] = (
    QColor("#3AA5D9"),     # Gulf blue (primary)
    QColor("#F37021"),     # Gulf orange (accent)
    QColor("#4ADE80"),     # green
    QColor("#A78BFA"),     # purple
    QColor("#FBBF24"),     # amber
    QColor("#F472B6"),     # pink
    QColor("#22D3EE"),     # cyan
    QColor("#FB7185"),     # rose
)

# Colour for the "empty / no data" state — a faint full ring with
# a dash in the centre. Matches the existing donut's empty look so
# the dashboard reads consistently when a phase has no data yet.
_EMPTY_COLOR = QColor("#E0E0E0")

# Slices smaller than this fraction get no callout label (the
# label would overlap with neighbours, and the user can read
# specifics from the tooltip). 3% of full circle ≈ 11° arc.
_LABEL_MIN_FRACTION = 0.03

# Callout-line colour — softer than the slice colours so the
# leader doesn't draw attention away from the slice itself.
_LEADER_LINE_COLOR = QColor(80, 80, 80, 180)


class CategoryPieWidget(QWidget):
    """Aspect-locked pie chart of arbitrary categories.

    Public API:
      * :meth:`set_data` — ``{label: count}``. Order preserved (use
        an ``OrderedDict`` or insertion order for repeatable colours).
      * :meth:`set_center_text` — override the centre numeral.
        Default = the dominant category's name + percent.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._categories: dict[str, int] = {}
        # Square aspect, like the existing donut.
        self.setMinimumSize(80, 80)
        self.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding,
            QSizePolicy.Policy.MinimumExpanding,
        )

    # ── Public API ────────────────────────────────────────────────

    def set_data(self, categories: dict[str, int]) -> None:
        """Replace the pie data. Categories with ``count == 0`` are
        silently dropped (they're not informative and would consume
        a palette colour slot for nothing)."""
        self._categories = {
            k: int(v) for k, v in categories.items() if int(v) > 0
        }
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
            side = min(self.width(), self.height())
            # Reserve a margin around the donut for the callout
            # labels. The donut occupies the inner 55% of the widget
            # diameter; the remaining 45% is shared between leader
            # lines (≈10%) and label text (≈35% of side, split L/R).
            donut_side = side * 0.55
            x = (self.width() - donut_side) / 2
            y = (self.height() - donut_side) / 2
            outer_rect = QRectF(x, y, donut_side, donut_side)
            ring_w = donut_side * 0.22
            half = ring_w / 2.0
            arc_rect = outer_rect.adjusted(half, half, -half, -half)

            total = sum(self._categories.values())
            if total <= 0:
                # Empty state — full grey ring + dash.
                self._draw_arc(p, arc_rect, _EMPTY_COLOR, 0, 360 * 16, ring_w)
                self._draw_centre_dash(p, outer_rect)
                return

            # Sweep the segments clockwise from the 12 o'clock
            # position. Qt uses 1/16°. Track the (start_deg, span_deg)
            # tuples per slice so the label pass below can compute
            # midpoints without redoing the math.
            start_angle = 90 * 16
            full_circle = 360 * 16
            slice_geom: list[tuple[str, float, int, int, QColor]] = []
            for i, (label, count) in enumerate(self._categories.items()):
                fraction = count / total
                span = -int(round(fraction * full_circle))
                if span == 0:
                    continue
                color = _PALETTE[i % len(_PALETTE)]
                self._draw_arc(
                    p, arc_rect, color, start_angle, span, ring_w,
                )
                slice_geom.append((label, fraction, start_angle, span, color))
                start_angle += span

            # Second pass: callout label per slice that's large
            # enough to warrant one. Done AFTER all arcs so the
            # leader lines paint on top of the ring (more readable).
            self._draw_callouts(p, outer_rect, ring_w, slice_geom)
        finally:
            p.end()

    # ── Helpers ───────────────────────────────────────────────────

    def _draw_arc(
        self,
        painter: QPainter,
        rect: QRectF,
        color: QColor,
        start_angle: int,
        span: int,
        ring_w: float,
    ) -> None:
        pen = QPen()
        pen.setWidthF(ring_w)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        pen.setColor(color)
        painter.setPen(pen)
        painter.drawArc(rect, start_angle, span)

    def _draw_centre_dash(
        self, painter: QPainter, outer_rect: QRectF,
    ) -> None:
        """Empty-state placeholder — a single dash in the centre of
        the donut, no label callouts to draw."""
        font = QFont(painter.font())
        font.setBold(True)
        side = min(outer_rect.width(), outer_rect.height())
        font.setPointSizeF(min(15.0, max(9.0, side * 0.16)))
        painter.setFont(font)
        painter.setPen(self.palette().text().color())
        painter.drawText(
            outer_rect, Qt.AlignmentFlag.AlignCenter, "—",
        )

    def _draw_callouts(
        self,
        painter: QPainter,
        outer_rect: QRectF,
        ring_w: float,
        slice_geom: list[tuple[str, float, int, int, QColor]],
    ) -> None:
        """Draw a leader-line + label outside each slice that's big
        enough to warrant one. Skips slices < :data:`_LABEL_MIN_FRACTION`
        of the full circle to avoid neighbour overlap (the tooltip
        carries the full breakdown for those).

        ``slice_geom`` is ``(label, fraction, start_angle_16, span_16,
        color)`` — the start angle and span are in Qt's 1/16°
        convention (already negative for clockwise sweeps).
        """
        cx = outer_rect.center().x()
        cy = outer_rect.center().y()
        r_outer = outer_rect.width() / 2.0      # outer edge of the ring
        # Stub line out from the ring's outer edge.
        stub_len = max(6.0, r_outer * 0.16)

        # Font picked once per paint — small relative to the widget
        # but with a sensible minimum so labels stay legible on a
        # ~150 px PhaseButton card, and an upper cap so the labels
        # don't balloon on the larger tiles of the rebuild dashboard
        # grid (Nelson 2026-06-01).
        side = min(self.width(), self.height())
        font = QFont(painter.font())
        font.setPointSizeF(min(10.0, max(8.0, side * 0.075)))
        font.setBold(False)
        painter.setFont(font)
        fm = painter.fontMetrics()

        # Widget bounds clamp where labels can land — never let them
        # paint off-widget.
        widget_left = 4.0
        widget_right = float(self.width() - 4)

        for label, fraction, start_16, span_16, color in slice_geom:
            if fraction < _LABEL_MIN_FRACTION:
                continue

            mid_16 = start_16 + span_16 / 2.0
            mid_deg = mid_16 / 16.0
            rad = math.radians(mid_deg)
            # Qt y-down: positive sin points UP in math, so flip sign
            # when computing pixel positions.
            cos_t = math.cos(rad)
            sin_t = math.sin(rad)
            x_outer = cx + r_outer * cos_t
            y_outer = cy - r_outer * sin_t
            x_stub = cx + (r_outer + stub_len) * cos_t
            y_stub = cy - (r_outer + stub_len) * sin_t

            # Leader line.
            pen = QPen(_LEADER_LINE_COLOR)
            pen.setWidth(1)
            painter.setPen(pen)
            painter.drawLine(
                QPointF(x_outer, y_outer),
                QPointF(x_stub, y_stub),
            )

            # Label placement. Right-half slices get text growing
            # right (left-aligned to the stub end); left-half slices
            # mirror. A tiny gap of 3 px separates the line from
            # the text.
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

            # Clamp horizontally so we don't paint off the widget.
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
        """Build a per-segment legend the user can read on hover.
        Skip the tooltip entirely when there's no data (the empty
        donut's "—" is self-explanatory)."""
        if not self._categories:
            self.setToolTip("")
            return
        total = sum(self._categories.values())
        lines = []
        for label, count in self._categories.items():
            pct = int(round(100 * count / max(1, total)))
            lines.append(f"{label} — {count} ({pct}%)")
        self.setToolTip("\n".join(lines))
