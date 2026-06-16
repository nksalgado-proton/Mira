"""Surface 01 — Events list + Cross-Event Cuts entry (redesigned).

Sibling to the legacy :class:`~mira.ui.pages.events_dashboard_page.DashboardPage`
while the surface-by-surface migration is in flight. Built from the design-
system catalog (mira.ui.design); data layer is the unchanged ``Gateway`` —
same ``list_events`` / ``events_index_filtered`` / per-event ``EventCardData``
contract.

Top-to-bottom composition (spec/75):
    TitleBar (host-owned)
    CrossEventCutsBand   — leads the screen; the app-level search door
    Toolbar              — "Events" title + 3 stat chips + per-list
                           search + Filters popover + + New event
    Tile grid            — FlowLayout of uniform fixed-height EventTile
                           instances (open + closed), 3 columns × 3-4
                           rows visible at typical desktop width

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
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from mira.gateway import Gateway
from mira.ui.base.event_card import EventCardData
from mira.ui.base.flow_layout import FlowLayout
from mira.ui.design import (
    chip_closed,
    chip_idle,
    chip_open,
    ghost_button,
    primary_button,
    search_field,
    select,
)
from mira.ui.pages._cross_event_band import CrossEventCutsBand
from mira.ui.pages._event_card_data import card_data as _card_data
from mira.ui.pages._event_tile import EventTile

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
    # spec/77 §6 — the v2 tile's ⋮ menu emits a Delete entry per-tile;
    # the host wires this signal to the existing delete-event flow with
    # the event id pre-selected (no menu-bar "Delete event" trip needed).
    event_delete_requested = pyqtSignal(str)
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
        """Surface 01 chrome (spec/75 §2): cross-event search first,
        then a one-line toolbar (title · stat chips · filter search ·
        Filters popover · + New event), then the uniform tile grid."""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 18, 32, 18)
        outer.setSpacing(12)

        # 1. Cross-Event Cuts band — the very first element on the screen
        # (Nelson 2026-06-16 / spec/75 §2). Reads as the app-level
        # entry point; per-events search/filter sit BELOW it.
        self._cross_band = CrossEventCutsBand()
        self._cross_band.submitted.connect(self.cross_event_query.emit)
        outer.addWidget(self._cross_band)

        # 2. One-line toolbar — title, three compact stat chips, the
        # per-list search field, the Filters popover button, and the
        # primary "+ New event". Replaces the tall PageHeader + the
        # always-visible 4-combo filter row from the prior layout.
        outer.addWidget(self._build_toolbar())

        # 3. Empty state — hidden when the grid has any tiles.
        self._empty = QLabel(
            "No events yet. Use Event → New event to make one."
        )
        self._empty.setObjectName("Faint")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        outer.addWidget(self._empty)

        # 4. Scrolling uniform tile grid (spec/75 §4). FlowLayout reflows
        # by width: tiles keep an identical fixed size and wrap into as
        # many columns as the viewport allows. The minimum-width-of-
        # widest-child invariant of FlowLayout means a single tile is the
        # only horizontal floor — the page never refuses to narrow.
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        host = QWidget()
        self._cards = FlowLayout(host, margin=0, spacing=14)
        self._scroll.setWidget(host)
        self._scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        outer.addWidget(self._scroll, 1)

    # ── toolbar / filters ──────────────────────────────────────────────

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)

        title = QLabel("Events")
        title.setStyleSheet("font-size: 22px; font-weight: 800;")
        h.addWidget(title)

        # Three small stat pills — open / closed / total days. The chip
        # factories already match the spec/66 phase identity (open green,
        # closed pink, days neutral). Live-updated in ``_apply_filter``.
        self._stat_open = chip_open("0 open")
        self._stat_closed = chip_closed("0 closed")
        self._stat_days = chip_idle("0 days")
        for chip in (self._stat_open, self._stat_closed, self._stat_days):
            h.addWidget(chip)

        h.addStretch(1)

        # "showing N of M" label — only visible when a filter trims the
        # list, so the user is never stranded on a hidden subset (spec/75
        # §3.2, also the original fix #1 root cause).
        self._showing_label = QLabel("")
        self._showing_label.setObjectName("Faint")
        self._showing_label.setStyleSheet("font-size: 12px;")
        self._showing_label.setVisible(False)
        h.addWidget(self._showing_label)

        # Per-list search field — distinct from the cross-event band
        # above it. Kept inline (rather than tucked into the popover) so
        # the most-frequent filter — type-to-find — stays one click away.
        self._filter_search = search_field("Filter events…")
        self._filter_search.setMaximumWidth(220)
        self._filter_search.input.textChanged.connect(self._apply_filter)
        h.addWidget(self._filter_search)

        # Filters button — popover holding Status / Type / Year / Sort.
        # The button text becomes "Filters · N" when any filter is off
        # its default; the QSS `[active="true"]` hook adds the accent
        # border in both themes.
        self._filter_btn = ghost_button("Filters")
        self._filter_btn.setObjectName("Ghost")
        self._filter_btn.setProperty("active", False)
        self._filter_btn.clicked.connect(self._open_filter_popover)
        h.addWidget(self._filter_btn)

        # Clear button — small "× clear" affordance pinned next to the
        # Filters button, only visible when any filter is off default.
        self._clear_btn = QPushButton("× Clear")
        self._clear_btn.setObjectName("Ghost")
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.setStyleSheet(
            "QPushButton#Ghost { padding: 8px 10px; }"
        )
        self._clear_btn.clicked.connect(self._on_clear_filters)
        self._clear_btn.setVisible(False)
        h.addWidget(self._clear_btn)

        new_btn = primary_button("+ New Event")
        new_btn.clicked.connect(self.new_event_requested.emit)
        h.addWidget(new_btn)

        # Build the popover-backing selects up front — they live inside
        # the QMenu shown from ``_open_filter_popover`` and the host reads
        # their currentIndex in ``_apply_filter``. Keeping them as attrs
        # rather than rebuilding on every popover open lets the active
        # state + count survive between presses.
        self._status_sel = select(["All status", "Open", "Closed"])
        self._status_sel.currentIndexChanged.connect(self._apply_filter)
        self._type_sel = select(
            ["All types", "Trip", "Session", "Wildlife", "Mountains", "Urban"]
        )
        self._type_sel.currentIndexChanged.connect(self._apply_filter)
        self._year_sel = select(
            ["Any year", "2026", "2025", "2024", "2023"]
        )
        self._year_sel.currentIndexChanged.connect(self._apply_filter)
        self._sort_sel = select(
            ["Newest first", "Oldest first", "Name (A→Z)", "Days (most→least)"]
        )
        self._sort_sel.currentIndexChanged.connect(self._apply_filter)

        # Build the popover once. Reusing a single QMenu (instead of
        # rebuilding on every open) keeps the combo widgets reparented to
        # one stable host — re-parenting on every press caused fragile
        # state where the combos' popups would close abruptly the second
        # time the popover opened.
        self._filter_menu = self._build_filter_menu()

        return bar

    def _build_filter_menu(self) -> QMenu:
        """Build the filter popover once. The combos live on a single
        host frame attached to a ``QWidgetAction`` so the menu inherits
        Esc-to-close + click-outside-to-close from Qt for free."""
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: transparent; border: none; }"
        )

        host = QFrame()
        host.setObjectName("Card")
        v = QVBoxLayout(host)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)

        for label, combo in (
            ("Status", self._status_sel),
            ("Type", self._type_sel),
            ("Year", self._year_sel),
            ("Sort", self._sort_sel),
        ):
            lab = QLabel(label)
            lab.setObjectName("Micro")
            v.addWidget(lab)
            v.addWidget(combo)

        # Clear-from-inside affordance — duplicates the toolbar Clear so
        # the user can drop everything without first closing the popover.
        clear_inside = QPushButton("Clear filters")
        clear_inside.setObjectName("Ghost")
        clear_inside.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_inside.clicked.connect(self._on_clear_filters)
        clear_inside.clicked.connect(menu.close)
        v.addWidget(clear_inside)

        action = QWidgetAction(menu)
        action.setDefaultWidget(host)
        menu.addAction(action)
        return menu

    def _open_filter_popover(self) -> None:
        """Pop the pre-built Filters menu beneath the button."""
        self._filter_menu.exec(
            self._filter_btn.mapToGlobal(
                self._filter_btn.rect().bottomLeft()
            )
        )

    def _on_clear_filters(self) -> None:
        """Reset every dropdown to its default index + clear the search.
        The dropdowns' currentIndexChanged signals fire ``_apply_filter``
        which then refreshes the count + active state in one pass."""
        for combo in (
            self._status_sel, self._type_sel,
            self._year_sel, self._sort_sel,
        ):
            combo.setCurrentIndex(0)
        self._filter_search.input.clear()

    def _filter_active_count(self) -> int:
        """How many filters are off their default. Drives the Filters
        button label, the active QSS hook, and the Clear button's
        visibility. The text-search counts as one filter when non-empty
        even though it lives inline."""
        n = 0
        if self._status_sel.currentIndex() != 0:
            n += 1
        if self._type_sel.currentIndex() != 0:
            n += 1
        if self._year_sel.currentIndex() != 0:
            n += 1
        if self._filter_search.input.text().strip():
            n += 1
        # Sort doesn't count: changing sort doesn't hide any event, so
        # the safety-net wording "filtered" never applies to it.
        return n

    def _refresh_filter_button(self) -> None:
        n = self._filter_active_count()
        active = n > 0
        self._filter_btn.setText(f"Filters · {n}" if active else "Filters")
        self._filter_btn.setProperty("active", active)
        # Qt only re-resolves QSS state on a style refresh — without
        # this, the property change doesn't repaint the border.
        self._filter_btn.style().unpolish(self._filter_btn)
        self._filter_btn.style().polish(self._filter_btn)
        self._clear_btn.setVisible(active)

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
            # gateway list_events failed). The empty grid below already
            # shows the "no events yet" hint; reset the toolbar chips
            # to their zero state so the surface reads as deliberately
            # empty rather than mid-load.
            self._stat_open.setText("0 open")
            self._stat_closed.setText("0 closed")
            self._stat_days.setText("0 days")
            self._showing_label.setVisible(False)
            self._refresh_filter_button()
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
        # Toolbar chips — totals across ALL events (not the filtered
        # subset). The "showing N of M" label carries the filtered-subset
        # truth, so the chips stay stable as the user toggles filters.
        total = len(self._card_data_by_id)
        open_n = sum(1 for c in self._card_data_by_id.values() if not c.is_closed)
        closed_n = total - open_n
        days_n = sum(
            c.total_days or 0 for c in self._card_data_by_id.values()
        )
        self._stat_open.setText(f"{open_n} open")
        self._stat_closed.setText(f"{closed_n} closed")
        self._stat_days.setText(f"{days_n} days")

        # "showing N of M" — visible only when the filtered subset is
        # smaller than the full set. Together with the active Filters
        # button, this is the safety net that closes the original bug #1
        # (the user stranded on a hidden list with no indication).
        shown_n = len(filtered)
        if shown_n < total:
            self._showing_label.setText(f"showing {shown_n} of {total}")
            self._showing_label.setVisible(True)
        else:
            self._showing_label.setVisible(False)
        self._refresh_filter_button()
        self._render(filtered)

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
            tile = EventTile(cd, sample_pixmaps=sample)
            tile.activated.connect(self.event_activated.emit)
            tile.title_clicked.connect(self.event_info_requested.emit)
            tile.plan_requested.connect(self.event_plan_requested.emit)
            tile.status_toggled.connect(self.event_status_toggle_requested.emit)
            tile.delete_requested.connect(self.event_delete_requested.emit)
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
