"""``FilterRail`` — the events dashboard filter rail (spec/44 §3.2).

A wrapped row of: Search · Status · Type · Subtype · Year · Sort. Reads + writes
its state through an :class:`~mira.gateway.gateway.EventsQuery` value; emits
``query_changed`` whenever the user adjusts anything (search debounced 200 ms).

The widget is **passive** — it doesn't know anything about the gateway or the
event list. The consuming page wires:

    rail.query_changed.connect(lambda: page.refresh())
    listing = gw.events_index_filtered(rail.query())
    rail.update_options(listing)   # chip-count refresh

Hoisted into ``ui.base`` rather than dropped into ``ui.pages.events_dashboard_page``
because a similar filter is likely useful for future surfaces (review finding #7 —
verbatim widget copies were the previous slice's smell; this widget is reuse-ready
from day one). Uses :class:`mira.ui.base.flow_layout.FlowLayout` so the rail
collapses gracefully when the window narrows ([[backlog_width_cannot_shrink]]).
"""
from __future__ import annotations

import logging
from typing import List, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from mira import event_classification
from mira.gateway.gateway import (
    SORT_NAME,
    SORT_NEWEST,
    SORT_OLDEST,
    SORT_TYPE,
    EventsListing,
    EventsQuery,
)
from mira.ui.base.flow_layout import FlowLayout
from mira.ui.i18n import tr

log = logging.getLogger(__name__)

# Sentinel chosen so it can never collide with a real subtype value (no real
# subtype is the empty string).
_ANY_SENTINEL = ""

_SEARCH_DEBOUNCE_MS = 200


def _user_data(combo: QComboBox):
    """The current `userData` value of a QComboBox, defaulting to None."""
    idx = combo.currentIndex()
    return combo.itemData(idx) if idx >= 0 else None


class FilterRail(QWidget):
    """Wrap-friendly filter row for the events dashboard."""

    query_changed = pyqtSignal()

    def __init__(
        self,
        *,
        initial_sort: str = SORT_NEWEST,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("FilterRail")
        self._signals_blocked = False
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(_SEARCH_DEBOUNCE_MS)
        self._search_debounce.timeout.connect(self._emit_changed)
        self._build_ui(initial_sort)

    # ── Build ───────────────────────────────────────────────────────────────

    def _build_ui(self, initial_sort: str) -> None:
        # Each filter sits inside its own QGroupBox titled with the filter name
        # (Nelson 2026-06-06 — Slice 1 eyeball): the labels-beside-combo pattern
        # wrapped badly. Boxes flow horizontally via FlowLayout; the rail wraps
        # naturally as the panel narrows ([[backlog_width_cannot_shrink]]).
        layout = FlowLayout(self, spacing=10)
        layout.setContentsMargins(0, 0, 0, 0)

        search_box = self._titled_group(tr("Search"))
        self._search = QLineEdit()
        self._search.setPlaceholderText(tr("Name, description, tags…"))
        self._search.setToolTip(tr(
            "Match against name, description, and tags. Tokens combine with AND."))
        self._search.setMinimumWidth(220)
        self._search.textChanged.connect(self._on_search_changed)
        search_box.layout().addWidget(self._search)
        layout.addWidget(search_box)

        self._status = self._titled_combo(layout, tr("Status"))
        self._status.addItem(tr("All"), None)
        self._status.addItem(tr("Open"), False)
        self._status.addItem(tr("Closed"), True)
        self._status.currentIndexChanged.connect(lambda _i: self._emit_changed())

        self._type = self._titled_combo(layout, tr("Type"))
        # Type dropdown order: All first, then the closed enum in pipeline order
        # (trip/session/occasion/project/unclassified). Counts get appended in
        # update_options.
        self._type.addItem(tr("All"), None)
        for et in event_classification.ALL_EVENT_TYPES:
            self._type.addItem(
                event_classification.display_label_for_type(et), et,
            )
        self._type.currentIndexChanged.connect(self._on_type_changed)

        # Subtype lives in its own group box like the others; the whole box hides
        # until a Type is picked (spec/44 §3.2).
        self._subtype_box = self._titled_group(tr("Subtype"))
        self._subtype = QComboBox()
        self._subtype.setMinimumWidth(140)
        self._subtype.currentIndexChanged.connect(lambda _i: self._emit_changed())
        self._subtype_box.layout().addWidget(self._subtype)
        layout.addWidget(self._subtype_box)
        self._set_subtype_visible(False)

        self._year = self._titled_combo(layout, tr("Year"))
        self._year.addItem(tr("Any"), None)
        self._year.currentIndexChanged.connect(lambda _i: self._emit_changed())

        self._sort = self._titled_combo(layout, tr("Sort"))
        for key, label in (
            (SORT_NEWEST, tr("Newest first")),
            (SORT_OLDEST, tr("Oldest first")),
            (SORT_NAME,   tr("Name A→Z")),
            (SORT_TYPE,   tr("Type")),
        ):
            self._sort.addItem(label, key)
        # Restore last-used sort.
        idx = self._sort.findData(initial_sort)
        if idx >= 0:
            self._sort.setCurrentIndex(idx)
        self._sort.currentIndexChanged.connect(lambda _i: self._emit_changed())

    def _titled_group(self, title: str) -> QGroupBox:
        """Return a QGroupBox with a vertical layout, titled by ``title``.

        The QSS role ``FilterRailGroup`` lets the theme target these boxes
        independently from generic QGroupBox styling in the rest of the app.
        Padding is set inline because group-box vertical breathing room is the
        kind of pixel detail QSS handles poorly across platforms.
        """
        box = QGroupBox(title)
        box.setObjectName("FilterRailGroup")
        inner = QVBoxLayout(box)
        inner.setContentsMargins(8, 4, 8, 6)
        inner.setSpacing(2)
        return box

    def _titled_combo(self, layout: FlowLayout, title: str) -> QComboBox:
        """Build a QComboBox inside its own titled group box, added to ``layout``."""
        box = self._titled_group(title)
        combo = QComboBox()
        combo.setMinimumWidth(140)
        box.layout().addWidget(combo)
        layout.addWidget(box)
        return combo

    # ── Public API ──────────────────────────────────────────────────────────

    def query(self) -> EventsQuery:
        """Build an :class:`EventsQuery` from the current widget state."""
        subtype_data = _user_data(self._subtype)
        subtypes: Optional[List[str]] = None
        if subtype_data:
            subtypes = [subtype_data]
        year_data = _user_data(self._year)
        return EventsQuery(
            search=self._search.text().strip(),
            status=_user_data(self._status),
            type=_user_data(self._type),
            subtypes=subtypes,
            year=int(year_data) if year_data is not None else None,
            sort=_user_data(self._sort) or SORT_NEWEST,
        )

    def sort_key(self) -> str:
        """Current sort selection (for persistence)."""
        return _user_data(self._sort) or SORT_NEWEST

    def update_options(self, listing: EventsListing) -> None:
        """Refresh chip counts + Subtype + Year dropdown options from a listing.

        Counts are appended to Type labels ("Trip (4)") and Subtype labels.
        Year dropdown is rebuilt from ``listing.year_options`` (most recent
        first). The current selections are preserved across the rebuild
        whenever possible; if a selection disappears (e.g. last event of that
        year deleted), it silently resets to "Any" / "All".
        """
        self._signals_blocked = True
        try:
            self._update_type_labels(listing.type_counts)
            self._rebuild_subtype_combo(listing.subtype_counts, listing.custom_subtypes)
            self._rebuild_year_combo(listing.year_options)
        finally:
            self._signals_blocked = False

    # ── Internals ───────────────────────────────────────────────────────────

    def _on_search_changed(self, _text: str) -> None:
        if self._signals_blocked:
            return
        # Restart the debounce timer on every keystroke; emit once the user
        # stops typing for _SEARCH_DEBOUNCE_MS. Per-keystroke firing would
        # rebuild the events list on every key and tank responsiveness.
        self._search_debounce.start()

    def _on_type_changed(self, _idx: int) -> None:
        if self._signals_blocked:
            return
        et = _user_data(self._type)
        self._set_subtype_visible(et is not None)
        self._emit_changed()

    def _set_subtype_visible(self, visible: bool) -> None:
        # Toggle the whole group box (title + combo together).
        self._subtype_box.setVisible(visible)

    def _emit_changed(self) -> None:
        if self._signals_blocked:
            return
        self.query_changed.emit()

    def _update_type_labels(self, counts: dict) -> None:
        for i in range(self._type.count()):
            et = self._type.itemData(i)
            if et is None:
                # "All" — show grand total
                total = sum(counts.values())
                self._type.setItemText(i, f"{tr('All')} ({total})")
                continue
            n = counts.get(et, 0)
            label = event_classification.display_label_for_type(et)
            self._type.setItemText(i, f"{label} ({n})")

    def _rebuild_subtype_combo(
        self, counts: dict, custom_subtypes: list,
    ) -> None:
        previous = _user_data(self._subtype)
        self._subtype.clear()
        self._subtype.addItem(tr("Any"), None)
        et = _user_data(self._type)
        if et is None:
            return  # Subtype dropdown is hidden anyway when Type=All.
        presets = event_classification.subtype_presets_for(et)
        # First the presets (showing counts when non-zero so empty buckets
        # aren't visually noisy but stay discoverable).
        for s in presets:
            n = counts.get(s, 0)
            label = f"{s} ({n})" if n else s
            self._subtype.addItem(label, s)
        # Then the custom (user-typed) subtypes for this type.
        for s in custom_subtypes:
            n = counts.get(s, 0)
            label = f"{s} ({n})" if n else s
            self._subtype.addItem(label, s)
        # Restore prior selection if it still exists.
        if previous is not None:
            idx = self._subtype.findData(previous)
            if idx >= 0:
                self._subtype.setCurrentIndex(idx)

    def _rebuild_year_combo(self, years: list) -> None:
        previous = _user_data(self._year)
        self._year.clear()
        self._year.addItem(tr("Any"), None)
        for y in years:
            self._year.addItem(str(y), y)
        if previous is not None:
            idx = self._year.findData(previous)
            if idx >= 0:
                self._year.setCurrentIndex(idx)
