"""Surface 01 — Events list + Cross-Event Cuts entry (redesigned).

Sibling to the legacy :class:`~mira.ui.pages.events_dashboard_page.DashboardPage`
while the surface-by-surface migration is in flight. Built from the design-
system catalog (mira.ui.design); data layer is the unchanged ``Gateway`` —
same ``list_events`` / ``events_index_filtered`` / per-event ``EventCardData``
contract.

Top-to-bottom composition (design-system §Surface 01):
    TitleBar (host-owned)
    PageHeader   — "Events" + count sub-line + primary "+ New Event"
    CrossEventCutsBand   — accent-bordered entry (NEW; stub until backend)
    FilterRow    — SearchField + Status / Type / Year / Sort selects
                   (filters the events list only, NOT cross-event search)
    Events list  — vertical stack of EventCardRedesign tiles (open + closed)

Signals preserve the legacy DashboardPage shape so MainWindow routing doesn't
change:
    event_activated(str)             body click  -> activity dashboard / Cuts
    event_info_requested(str)        title click -> Event Header dialog
    event_status_toggle_requested    chip click  -> open <-> closed flip
    new_event_requested              + New Event primary button
    cross_event_query(str)           Cross-Event Cuts band submission

Once Nelson eyeballs this and approves, MainWindow swaps from DashboardPage
to EventsPage and the legacy DashboardPage retires.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mira.gateway import Gateway
from mira.ui.base.event_card import EventCardData
from mira.ui.design import (
    PageHeader,
    StatTile,
    primary_button,
    search_field,
    select,
)
from mira.ui.pages._cross_event_band import CrossEventCutsBand
from mira.ui.pages._event_card_data import card_data as _card_data
from mira.ui.pages._event_card_redesign import EventCardRedesign

log = logging.getLogger(__name__)


class EventsPage(QWidget):
    """Redesigned events surface (Surface 01).

    Public refresh API matches the legacy DashboardPage (``refresh()``) so the
    host can call it after a New Event lands / a closed/open toggle fires.
    """

    event_activated = pyqtSignal(str)
    event_info_requested = pyqtSignal(str)
    event_plan_requested = pyqtSignal(str)
    event_status_toggle_requested = pyqtSignal(str)
    # Stub for parity with the legacy DashboardPage so MainWindow's .connect()
    # lines work post-swap. The legacy 'Unclassified (N) [Classify all…]'
    # section header is dropped from Surface 01; a future commit re-surfaces
    # the entry (probably as an inline banner above the filter row when N>0)
    # and starts emitting this signal.
    classify_all_requested = pyqtSignal()
    new_event_requested = pyqtSignal()
    cross_event_query = pyqtSignal(str)

    def __init__(
        self, gateway: Gateway, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.gateway = gateway
        self._card_data_by_id: Dict[str, EventCardData] = {}
        self._build_ui()
        self.refresh()

    # ── layout ──────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 24, 32, 24)
        outer.setSpacing(16)

        # Header — title + count + primary New Event
        new_btn = primary_button("+ New Event")
        new_btn.clicked.connect(self.new_event_requested.emit)
        self._header = PageHeader("Events", "Loading…", action=new_btn)
        outer.addWidget(self._header)

        # Aggregate at-a-glance stat band — 3 quiet StatTile chips that
        # frame the events list as a dashboard view (spec/65 §2.4 / §3.1
        # "the dashboard wants this synthesis"). Sits between the header
        # and the CEC: the CEC's accent border + glow still dominate, and
        # the tiles are deliberately card2 (no accent shadow) so they
        # recede into the page chrome. setEventsForPreview / refresh
        # repopulate the values in _apply_filter.
        self._stat_row = QHBoxLayout()
        self._stat_row.setSpacing(10)
        self._stat_open = StatTile("Open", "0", value_color="#34d399")
        self._stat_closed = StatTile("Closed", "0", value_color="#ff5da2")
        self._stat_days = StatTile("Days", "0", value_color="#7c6cff")
        for w in (self._stat_open, self._stat_closed, self._stat_days):
            w.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            self._stat_row.addWidget(w, 1)
        outer.addLayout(self._stat_row)

        # Cross-Event Cuts entry
        self._cross_band = CrossEventCutsBand()
        self._cross_band.submitted.connect(self.cross_event_query.emit)
        outer.addWidget(self._cross_band)

        # Filters row
        filters = QHBoxLayout()
        filters.setSpacing(10)
        self._filter_search = search_field("Filter events…")
        self._filter_search.input.textChanged.connect(self._apply_filter)
        filters.addWidget(self._filter_search, 3)
        self._status_sel = select(["All status", "Open", "Closed"])
        self._status_sel.currentIndexChanged.connect(self._apply_filter)
        filters.addWidget(self._status_sel, 1)
        self._type_sel = select(
            ["All types", "Trip", "Session", "Wildlife", "Mountains", "Urban"]
        )
        self._type_sel.currentIndexChanged.connect(self._apply_filter)
        filters.addWidget(self._type_sel, 1)
        self._year_sel = select(
            ["Any year", "2026", "2025", "2024", "2023"]
        )
        self._year_sel.currentIndexChanged.connect(self._apply_filter)
        filters.addWidget(self._year_sel, 1)
        self._sort_sel = select(
            ["Newest first", "Oldest first", "Name (A→Z)", "Days (most→least)"]
        )
        self._sort_sel.currentIndexChanged.connect(self._apply_filter)
        filters.addWidget(self._sort_sel, 1)
        outer.addLayout(filters)

        # Empty state
        self._empty = QLabel(
            "No events yet. Use Event → New event to make one."
        )
        self._empty.setObjectName("Faint")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        outer.addWidget(self._empty)

        # Scrolling card list
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        host = QWidget()
        self._cards = QVBoxLayout(host)
        self._cards.setContentsMargins(0, 0, 0, 0)
        self._cards.setSpacing(14)
        self._cards.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(host)
        self._scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        outer.addWidget(self._scroll, 1)

    # ── data ────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Re-read the gateway and rebuild the card-data cache. Mirrors the
        legacy DashboardPage.refresh() so host callers don't change."""
        if self.gateway is None:
            # Preview path: tests / smoke use setEventsForPreview() instead.
            self._apply_filter()
            return
        try:
            rows = self.gateway.list_events()
            if not rows:
                recovered = self.gateway.recover_orphan_events()
                if recovered:
                    log.warning(
                        "Events list empty; recovered %d orphaned event(s)",
                        len(recovered),
                    )
                    rows = self.gateway.list_events()
            self._card_data_by_id = {
                str(row.get("id", "")): _card_data(self.gateway, row)
                for row in rows
            }
        except Exception:                                          # noqa: BLE001
            log.exception("EventsPage.refresh failed; keeping prior list")
        self._apply_filter()

    def setEventsForPreview(
        self,
        cards: List[EventCardData],
        sample_pixmaps_by_id: Dict[str, List] | None = None,
    ) -> None:
        """Inject card data directly (smoke-test / preview entrypoint).

        Skips the gateway. Useful for the surface mockup screenshot before
        the host's gateway is wired."""
        self._card_data_by_id = {c.event_id: c for c in cards}
        self._sample_pixmaps_by_id = sample_pixmaps_by_id or {}
        self._apply_filter()

    # ── filter / render ─────────────────────────────────────────────────

    def _apply_filter(self) -> None:
        if not self._card_data_by_id:
            # No events loaded (cold start with empty library, or
            # gateway list_events failed). Replace the constructor's
            # "Loading…" subtitle so the user doesn't get stranded on
            # an indeterminate state — the empty grid below already
            # shows the "no events yet" hint.
            sub = self._header.findChild(QLabel, "Sub")
            if sub is not None:
                sub.setText("0 events")
            self._update_stat_tile(self._stat_open, "0")
            self._update_stat_tile(self._stat_closed, "0")
            self._update_stat_tile(self._stat_days, "0")
            self._render([])
            return
        query = self._filter_search.input.text().strip().lower()
        status_idx = self._status_sel.currentIndex()
        type_idx = self._type_sel.currentIndex()
        year_idx = self._year_sel.currentIndex()
        # Year filter strings: 0=Any, 1=2026, 2=2025, ...
        year_text = (
            self._year_sel.currentText()
            if year_idx > 0 else None
        )
        # Type filter
        type_text = (
            self._type_sel.currentText().lower()
            if type_idx > 0 else None
        )
        filtered = []
        for cd in self._card_data_by_id.values():
            if query and query not in (cd.name or "").lower():
                continue
            if status_idx == 1 and cd.is_closed:
                continue
            if status_idx == 2 and not cd.is_closed:
                continue
            if type_text and (cd.event_type or "").lower() != type_text:
                continue
            if year_text and (
                not cd.start_date or str(cd.start_date.year) != year_text
            ):
                continue
            filtered.append(cd)
        # Sort
        sort_idx = self._sort_sel.currentIndex()
        if sort_idx == 0:
            filtered.sort(
                key=lambda c: c.start_date or __import__("datetime").date.min,
                reverse=True,
            )
        elif sort_idx == 1:
            filtered.sort(
                key=lambda c: c.start_date or __import__("datetime").date.max,
            )
        elif sort_idx == 2:
            filtered.sort(key=lambda c: (c.name or "").lower())
        elif sort_idx == 3:
            filtered.sort(key=lambda c: c.total_days, reverse=True)
        # Header sub-line
        total = len(self._card_data_by_id)
        open_n = sum(1 for c in self._card_data_by_id.values() if not c.is_closed)
        closed_n = total - open_n
        days_n = sum(
            c.total_days or 0 for c in self._card_data_by_id.values()
        )
        sub = self._header.findChild(QLabel, "Sub")
        if sub is not None:
            sub.setText(
                f"{total} event{'s' if total != 1 else ''} · "
                f"{open_n} open · {closed_n} closed"
            )
        # Push the at-a-glance trio. Static labels under a StatTile pick up
        # the bigger value glyph live; the value labels are the second child
        # in each tile (the Micro label is first).
        self._update_stat_tile(self._stat_open, str(open_n))
        self._update_stat_tile(self._stat_closed, str(closed_n))
        self._update_stat_tile(self._stat_days, str(days_n))
        self._render(filtered)

    @staticmethod
    def _update_stat_tile(tile: "StatTile", value: str) -> None:
        """StatTile's value label is the 2nd QLabel descendant (Micro label
        is first). Live updates avoid rebuilding the widget every refresh."""
        labels = tile.findChildren(QLabel)
        # labels: [Micro 'label', StatValue, optional Sub suffix]
        if len(labels) >= 2:
            labels[1].setText(value)

    def _render(self, cards: List[EventCardData]) -> None:
        while self._cards.count():
            it = self._cards.takeAt(0)
            w = it.widget() if it else None
            if w is not None:
                w.deleteLater()
        self._empty.setVisible(not cards)
        self._scroll.setVisible(bool(cards))
        for cd in cards:
            sample = self._sample_pixmaps_for(cd)
            tile = EventCardRedesign(cd, sample_pixmaps=sample)
            tile.activated.connect(self.event_activated.emit)
            tile.title_clicked.connect(self.event_info_requested.emit)
            tile.plan_requested.connect(self.event_plan_requested.emit)
            tile.status_toggled.connect(self.event_status_toggle_requested.emit)
            self._cards.addWidget(tile)

    def _sample_pixmaps_for(self, cd: EventCardData) -> list:
        """Resolve the closed-event Carousel pixmaps from either the
        preview injection (smoke tests) or the gateway-populated
        ``sample_pixmap_paths`` field.

        Loads each absolute path as a downscaled QPixmap (320x220 cap) so
        the events list doesn't choke on multi-megapixel finals. Skips
        paths that don't exist (post-export deletion, library move). For
        the events-list cadence (one closed event today, ~5 samples each)
        the synchronous load is acceptable; if Nelson's closed-event
        count grows enough to feel sluggish, the next step is routing
        through PhotoCache's worker.
        """
        preview = getattr(self, "_sample_pixmaps_by_id", {}).get(
            cd.event_id, []
        )
        if preview:
            return preview
        if not cd.sample_pixmap_paths:
            return []
        from PyQt6.QtCore import Qt as _Qt
        from PyQt6.QtGui import QPixmap as _QPixmap
        out = []
        for path in cd.sample_pixmap_paths:
            try:
                if not __import__("pathlib").Path(path).is_file():
                    continue
                pm = _QPixmap(str(path))
                if pm.isNull():
                    continue
                # Cap at ~card-carousel native size; rescale on render
                # handles the rest.
                pm = pm.scaled(
                    480, 320,
                    _Qt.AspectRatioMode.KeepAspectRatio,
                    _Qt.TransformationMode.SmoothTransformation,
                )
                out.append(pm)
            except Exception:                                  # noqa: BLE001
                log.exception(
                    "sample pixmap load failed for %s (%s)", cd.event_id, path
                )
        return out
