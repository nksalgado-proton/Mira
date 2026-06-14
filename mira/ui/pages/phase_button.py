"""Large card-style button for the per-event workflow view.

Each ``PhaseButton`` represents one of the workflow phases — Plan,
Capture, Cull, Select, Process, Curate — and shows a title, a
status badge, an at-a-glance **donut chart** of day progress, and a
one-line stats caption. The whole card is clickable; hover and
disabled states are driven by QSS.

Per Nelson's spec (2026-05-12, donut-chart amendment 2026-05-21):
the phase buttons take the full event view; the donut replaces the
prototype's text-only stats so completion-state reads in a glance —
green wedge = done days, amber = in-progress, gray = not started,
with the magnitude (e.g. ``5/14``) in the centre.

The widget is a QFrame (not a QPushButton) because the cards hold
multiple stacked widgets. QFrame supports QSS ``:hover`` once
``WA_Hover`` is enabled.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QMouseEvent,
    QPainter,
    QPen,
)
from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


# Status keys used by the per-phase status badge. They drive both
# the label text and the QSS-styled colour band on the badge.
STATUS_NOT_STARTED = "not_started"
STATUS_READY = "ready"
STATUS_IN_PROGRESS = "in_progress"
STATUS_DONE = "done"
STATUS_UNAVAILABLE = "unavailable"  # block not yet implemented


_STATUS_LABEL = {
    STATUS_NOT_STARTED: "Not started",
    STATUS_READY: "Ready",
    STATUS_IN_PROGRESS: "In progress",
    STATUS_DONE: "Done",
    STATUS_UNAVAILABLE: "Coming soon",
}


# Donut segment colours. Same scheme as the EventCard heatmap
# (core/event_card_grid.py) so the two surfaces read consistently.
_COLOR_DONE = QColor("#4ADE80")        # green-400 (kept/done)
_COLOR_IN_PROGRESS = QColor("#FBBF24") # amber-400 (in progress)
_COLOR_NOT_STARTED = QColor("#E0E0E0") # neutral grey (untouched)


class _DonutWidget(QWidget):
    """Paints a phase-progress donut. Three concentric wedges:
    done (green), in-progress (amber), not-started (gray). A bold
    "done/total" numeral sits in the centre. When ``total`` is 0,
    paints a full-gray donut with a single dash in the middle.

    Self-contained; takes its dimensions from the layout it's
    placed in (square aspect maintained via ``heightForWidth``)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._done = 0
        self._in_progress = 0
        self._total = 0
        self._unavailable = False
        # Square: size = min(width, height). 100×100 default; the
        # parent layout will resize it.
        self.setMinimumSize(80, 80)
        self.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding,
            QSizePolicy.Policy.MinimumExpanding,
        )

    def set_progress(
        self, done: int, in_progress: int, total: int,
    ) -> None:
        """Update the donut. ``done`` + ``in_progress`` may exceed
        ``total`` in callers' arithmetic mistakes — we clamp."""
        self._unavailable = False
        self._total = max(0, int(total))
        self._done = max(0, min(int(done), self._total))
        self._in_progress = max(
            0, min(int(in_progress), self._total - self._done)
        )
        self.update()

    def set_unavailable(self) -> None:
        """Special state for phases not yet built (Curate today).
        Paints a faint dashed circle with a "—" in the middle."""
        self._unavailable = True
        self.update()

    def heightForWidth(self, w: int) -> int:  # noqa: N802
        return w

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            side = min(self.width(), self.height())
            # Inset so the donut doesn't kiss the edge.
            inset = 4
            x = (self.width() - side) / 2 + inset
            y = (self.height() - side) / 2 + inset
            outer_rect = QRectF(x, y, side - 2 * inset, side - 2 * inset)
            # Pen width = donut thickness. ~22% of side reads as a
            # bold ring with room for the centre numeral.
            ring_w = (side - 2 * inset) * 0.22
            pen = QPen()
            pen.setWidthF(ring_w)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)

            # Shrink the rect so the stroke lands fully inside the
            # widget (QPainter strokes are centred on the path).
            half = ring_w / 2.0
            arc_rect = outer_rect.adjusted(half, half, -half, -half)

            if self._unavailable or self._total == 0:
                # Empty / unavailable state: faint full ring.
                pen.setColor(_COLOR_NOT_STARTED)
                p.setPen(pen)
                p.drawArc(arc_rect, 0, 360 * 16)
                self._draw_centre_text(p, outer_rect, "—" if self._unavailable else "0")
                return

            # Compute per-state arc angles. Qt uses 1/16th-of-a-degree
            # units; "12 o'clock start, clockwise" = start at 90°
            # going negative.
            full_circle = 360 * 16
            done_frac = self._done / self._total
            ip_frac = self._in_progress / self._total

            # Background ring (the untouched fraction = the remainder).
            pen.setColor(_COLOR_NOT_STARTED)
            p.setPen(pen)
            p.drawArc(arc_rect, 0, full_circle)

            # Done wedge: from 90° clockwise.
            start_angle = 90 * 16
            done_span = -int(round(done_frac * full_circle))
            if done_span != 0:
                pen.setColor(_COLOR_DONE)
                p.setPen(pen)
                p.drawArc(arc_rect, start_angle, done_span)

            # In-progress wedge: continues clockwise after done.
            ip_span = -int(round(ip_frac * full_circle))
            if ip_span != 0:
                pen.setColor(_COLOR_IN_PROGRESS)
                p.setPen(pen)
                p.drawArc(arc_rect, start_angle + done_span, ip_span)

            # Centre numeral: "done/total".
            self._draw_centre_text(
                p, outer_rect, f"{self._done}/{self._total}"
            )
        finally:
            p.end()

    def _draw_centre_text(
        self, p: QPainter, rect: QRectF, text: str,
    ) -> None:
        """Draw the centre numeral large + bold. Font size scales
        with the ring radius so the text fits the inner circle no
        matter the widget size."""
        side = min(rect.width(), rect.height())
        font = QFont(p.font())
        font.setBold(True)
        # ~26% of side reads well at the typical PhaseButton scale.
        font.setPointSizeF(max(10.0, side * 0.20))
        p.setFont(font)
        p.setPen(self.palette().text().color())
        p.drawText(
            rect, Qt.AlignmentFlag.AlignCenter, text,
        )


class PhaseButton(QFrame):
    """Clickable card displaying a phase title + status + donut + stats.

    Emits ``clicked()`` on left-click anywhere within the card.
    """

    clicked = pyqtSignal()

    def __init__(
        self,
        title: str,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PhaseButton")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # Nelson 2026-05-26: the previous (280, 220) minimum forced
        # the EventDashboardPage's 3-column PhaseButton grid to
        # demand ~870 px, pinning the whole MainWindow to that
        # minimum width. Floor to (160, 180) so the cards compress
        # with the window; the row text stays legible down to the
        # new floor and the donut chart inside auto-scales.
        self.setMinimumSize(160, 180)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(8)

        self._title = QLabel(title)
        self._title.setObjectName("PhaseButtonTitle")
        layout.addWidget(self._title)

        self._status = QLabel(_STATUS_LABEL[STATUS_NOT_STARTED])
        self._status.setObjectName("PhaseButtonStatus")
        # We keep a property so QSS can colour-shift the badge based
        # on status. Set the default property before any first paint.
        self._status.setProperty("status", STATUS_NOT_STARTED)
        layout.addWidget(self._status)

        # The chart widget takes the lion's share of the card's
        # vertical space — it's the visual headline of the phase's
        # state. Default is the 3-segment progress donut; callers
        # swap in a phase-specific widget via :meth:`set_chart_widget`
        # (e.g. CategoryPieWidget for Capture/Select, PickedRatioDonut
        # for Cull/Process, TimezoneMapWidget for Plan).
        self._chart_layout = QVBoxLayout()
        self._chart_layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._chart_layout, stretch=1)
        self._donut = _DonutWidget(self)
        self._chart_widget: QWidget = self._donut
        self._chart_layout.addWidget(self._donut)

        self._stats = QLabel("")
        self._stats.setObjectName("PhaseButtonStats")
        self._stats.setWordWrap(True)
        self._stats.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
        )
        layout.addWidget(self._stats)

    # ── Public API ──────────────────────────────────────────────────

    def set_title(self, title: str) -> None:
        """Update the card's title label. Nelson 2026-05-29 — the
        first dashboard tile flips between 'Plan' (no plan yet) and
        'Manage' (a plan exists) depending on event state."""
        self._title.setText(title)

    def set_status(self, status: str) -> None:
        """Update the status badge. ``status`` is one of the
        ``STATUS_*`` constants. When ``unavailable`` is set, the
        donut switches to its "not built yet" placeholder."""
        if status not in _STATUS_LABEL:
            raise ValueError(f"unknown status: {status!r}")
        self._status.setText(_STATUS_LABEL[status])
        self._status.setProperty("status", status)
        # Force a style refresh so the new property takes effect.
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)
        if status == STATUS_UNAVAILABLE:
            self._donut.set_unavailable()

    def set_stats(self, text: str) -> None:
        self._stats.setText(text)

    def set_progress(
        self, done: int, in_progress: int, total: int,
    ) -> None:
        """Drive the donut chart with per-day progress counts.

        ``done`` = days fully completed for this phase.
        ``in_progress`` = days partially exported / in-flight.
        ``total`` = total days in the trip plan.

        The donut paints green/amber/gray wedges in proportion and
        a "done/total" numeral in the centre. Pair with
        :meth:`set_stats` for an optional one-line caption below.
        For phases with no per-day decomposition (e.g. a fresh-Plan
        binary), pass ``(total, 0, total)`` for "done" or
        ``(0, total, total)`` for "in progress"."""
        self._donut.set_progress(done, in_progress, total)

    def set_chart_widget(self, widget: QWidget) -> None:
        """Replace the default donut with a phase-specific chart
        (Nelson 2026-05-21). The new widget lands in the same layout
        slot the donut occupies; size policy is the caller's
        concern, but every chart in :mod:`ui.base` is designed
        ``MinimumExpanding`` so a swap "just works"."""
        if widget is self._chart_widget:
            return
        # Remove + delete the previous widget so we don't leak Qt
        # objects on repeated swaps (theoretically only happens once
        # per PhaseButton lifetime, but defence in depth).
        old = self._chart_widget
        self._chart_layout.removeWidget(old)
        old.setParent(None)
        old.deleteLater()
        self._chart_widget = widget
        self._chart_layout.addWidget(widget)
        # Discourage the inherited set_progress / donut-specific
        # calls when a non-donut chart is in play — those methods
        # become no-ops by mutating the (now-orphaned) donut, which
        # would just confuse the next reader.
        if isinstance(widget, _DonutWidget):
            self._donut = widget                 # the new one IS a donut
        else:
            # Keep self._donut pointing at a live (but detached)
            # object so set_progress / set_status(UNAVAILABLE) don't
            # NPE on legacy paths; the widget is hidden so it doesn't
            # paint anywhere.
            self._donut = _DonutWidget()
            self._donut.setVisible(False)

    def chart_widget(self) -> QWidget:
        """Currently-installed chart widget. Useful for tests and
        future "rotate through info modes" toggles."""
        return self._chart_widget

    def set_phase_enabled(self, enabled: bool) -> None:
        """Disable the card visually (greyed-out) and stop click
        signals when the phase is not actionable.

        Distinct from the inherited ``setEnabled`` because we still
        want hover events on the disabled card for tooltip-style
        explanations later; for now they behave the same."""
        self.setEnabled(enabled)
        if enabled:
            self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        else:
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    @property
    def status(self) -> str:
        value = self._status.property("status")
        return str(value) if value else STATUS_NOT_STARTED

    @property
    def title(self) -> str:
        return self._title.text()

    @property
    def stats(self) -> str:
        return self._stats.text()

    # ── Qt overrides ────────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.isEnabled()
        ):
            self.clicked.emit()
        super().mousePressEvent(event)
