"""``Event > Stats…`` modal (spec/46 Slice 2+3, 2026-06-06).

Shows the two surviving quadrants of the retired ``TwoByTwoOverview``:

* **Timezone band** — :class:`TimezoneMapWidget` showing the trip's
  dominant timezone.
* **Phase funnel** — vertical bar chart of kept-per-phase as % of
  Captured, via :class:`BarChartWidget` fed from
  :func:`overview_stats.phase_funnel_breakdown`.

The remaining two quadrants of the original 2×2 (style breakdown,
last-phase random photo) are deferred to Slice 4 (Compilation activity).
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from mira import overview_stats
from mira.gateway import Gateway
from mira.ui.base.bar_chart import BarChartWidget, BarRow
from mira.ui.base.timezone_map import TimezoneMapWidget
from mira.ui.i18n import tr

log = logging.getLogger(__name__)


def _dominant_tz_hours(trip_days) -> Optional[float]:
    """Most-common timezone (in hours) across the trip's days. ``None`` when
    no day carries a TZ. Verbatim from the legacy ``EventDashboardPage``
    helper (lifted because that module retires in this slice)."""
    from collections import Counter
    counts: Counter = Counter()
    for d in (trip_days or []):
        if getattr(d, "tz_minutes", None) is None:
            continue
        counts[d.tz_minutes / 60.0] += 1
    if not counts:
        return None
    return counts.most_common(1)[0][0]


class EventStatsDialog(QDialog):
    """Read-only stats viewer for one event. Built once per session, then
    re-populated each open via :meth:`populate`."""

    def __init__(self, gateway: Gateway, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.gateway = gateway
        self.setWindowTitle(tr("Stats"))
        self.setModal(True)
        self.resize(560, 480)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(12)

        self._heading = QLabel("")
        self._heading.setObjectName("PageHeading")
        layout.addWidget(self._heading)

        tz_label = QLabel(tr("Timezone"))
        tz_label.setObjectName("PageHint")
        layout.addWidget(tz_label)

        self._tz_map = TimezoneMapWidget()
        layout.addWidget(self._tz_map)

        funnel_label = QLabel(tr("Phase funnel — picked vs captured"))
        funnel_label.setObjectName("PageHint")
        layout.addWidget(funnel_label)

        self._bars = BarChartWidget()
        layout.addWidget(self._bars, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def populate(self, event_id: str) -> bool:
        """Load the event and refresh the two visuals. Returns False on
        gateway-open failure (event missing / db unreadable)."""
        try:
            eg = self.gateway.open_event(event_id)
        except Exception:  # noqa: BLE001
            log.exception("EventStatsDialog: cannot open event %s", event_id)
            self._heading.setText(tr("(event unavailable)"))
            self._tz_map.set_timezone(None)
            self._bars.set_rows([])
            return False
        try:
            event = eg.event()
            trip_days = eg.trip_days()
            funnel = overview_stats.phase_funnel_breakdown(eg)
        finally:
            eg.close()

        self._heading.setText(event.name or tr("(unnamed event)"))
        self._tz_map.set_timezone(_dominant_tz_hours(trip_days))

        if funnel:
            rows = [
                BarRow(
                    label=label,
                    value=float(count),
                    value_text=f"{count} ({pct:.0f}%)",
                )
                for label, count, pct in funnel
            ]
        else:
            rows = []
        self._bars.set_rows(rows)
        return True
