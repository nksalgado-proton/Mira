"""Surface 01 — Events list (redesigned).

Sibling to the legacy :class:`~mira.ui.pages.events_dashboard_page.DashboardPage`
while the surface-by-surface migration is in flight. Built from the design-
system catalog (mira.ui.design); data layer is the unchanged ``Gateway`` —
same ``list_events`` / ``events_index_filtered`` / per-event ``EventCardData``
contract.

Top-to-bottom composition (spec/75 + spec/94 Phase 4a-iii):
    TitleBar (host-owned)
    Toolbar              — "Events" title + 3 stat chips + per-list
                           search + Filters popover + + New event
    Tile grid            — FlowLayout of uniform fixed-height EventTile
                           instances (open + closed), 3 columns × 3-4
                           rows visible at typical desktop width

The Cross-Event Cuts band retired in spec/94 Phase 4a-iii — cross-event
work (Cuts + Collections + Recipes) lives in the top-level
:class:`mira.ui.pages.library_page.LibraryPage` reachable from the
Share menu's "Cross-event Cuts and Collections…" entry. This page
focuses on the per-event list.

Signals preserve the legacy DashboardPage shape so MainWindow routing doesn't
change:
    event_activated(str)             body click  -> activity dashboard / Cuts
    event_info_requested(str)        title click -> Event Header dialog
    event_status_toggle_requested    chip click  -> open <-> closed flip
    new_event_requested              + New Event primary button
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
from mira.ui.pages._event_card_data import card_data as _card_data
from mira.ui.pages._event_tile import EventTile, TILE_WIDTH

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

    def __init__(
        self, gateway: Gateway, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.gateway = gateway
        self._card_data_by_id: Dict[str, EventCardData] = {}
        # spec/84 §5 — events with a background ingest still copying
        # don't surface a tile here; the record exists (the queue's
        # commit closure needs to write `item` rows against it) but
        # the user shouldn't see a half-imported tile. MainWindow
        # pushes the set in/out via :meth:`set_ingest_in_progress_ids`
        # when the IngestJob lands on / leaves the shared batch queue.
        self._ingest_in_progress_ids: set[str] = set()
        self._build_ui()
        self.refresh()

    # ── layout ──────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        """Surface 01 chrome (spec/75 §2): cross-event search first,
        then a one-line toolbar (title · stat chips · filter search ·
        Filters popover · + New event), then the uniform tile grid."""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Minimum width = just enough for 3 event tiles across (Nelson 2026).
        # Floor = 3 tiles + 2 grid gaps + vertical scrollbar + band side padding
        # + band border + page side margins, with a small safety buffer so the
        # third tile never clips. The toolbar fits inside this and absorbs any
        # extra width through the filter search field + the gap to its left.
        _GRID_GAP, _SCROLLBAR, _SAFETY = 14, 17, 8
        self.setMinimumWidth(
            3 * TILE_WIDTH + 2 * _GRID_GAP + _SCROLLBAR + 32 + 2 + 64 + _SAFETY
        )

        # 0. Full-width surface identity rail — the colored top line the
        # decision surfaces already carry (spec/71), so the chrome reads the
        # same at the top of every surface (Nelson 2026). `home` tints it the
        # brand accent since the events screen isn't a phase.
        rail = QFrame()
        rail.setObjectName("SurfaceHeaderRail")
        rail.setProperty("phase", "home")
        rail.setFixedHeight(2)
        root.addWidget(rail)

        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(32, 18, 32, 18)
        outer.setSpacing(12)
        root.addWidget(content, 1)

        # spec/94 Phase 4a-iii — the Cross-Event Cuts band retired
        # here. Cross-event work (Cuts, Collections, Recipes) moved
        # to the top-level :class:`LibraryPage` reachable via the
        # Share menu's "Cross-event Cuts and Collections…" entry. The
        # events page focuses on its one job: the per-event list.
        # spec/162 Round 3c — LibraryPage's "+ New Cut" button now
        # routes through :meth:`MainWindow._open_new_cross_event_cut_
        # from_library`; the events_page hook that used to sit here
        # retired (dead code from Round 2b/3e).

        # 2-4. The Events band — an unnamed bordered band (same #CrossEventBand
        # treatment) wrapping the toolbar, empty state, and the scrolling tile
        # grid so the whole events area reads as one enclosed section, exactly
        # like the Cross-Event Cuts band above (Nelson 2026).
        events_band = QFrame()
        events_band.setObjectName("EventsBand")
        band = QVBoxLayout(events_band)
        band.setContentsMargins(16, 14, 16, 14)
        band.setSpacing(12)

        # 2. One-line toolbar — title, three compact stat chips, the
        # per-list search field, the Filters popover button, and the
        # primary "+ New event". Replaces the tall PageHeader + the
        # always-visible 4-combo filter row from the prior layout.
        band.addWidget(self._build_toolbar())

        # 3. Empty state — hidden when the grid has any tiles.
        self._empty = QLabel(
            "No events yet. Use Event → New event to make one."
        )
        self._empty.setObjectName("Faint")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        band.addWidget(self._empty)

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
        band.addWidget(self._scroll, 1)

        outer.addWidget(events_band, 1)

    # ── toolbar / filters ──────────────────────────────────────────────

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)

        title = QLabel("Events")
        title.setStyleSheet("font-size: 22px; font-weight: 800;")  # pragma: no-qss — layout-only (font)
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
        self._showing_label.setStyleSheet("font-size: 12px;")  # pragma: no-qss — layout-only (font)
        self._showing_label.setVisible(False)
        h.addWidget(self._showing_label)

        # Per-list search field — distinct from the cross-event band
        # above it. Kept inline (rather than tucked into the popover) so
        # the most-frequent filter — type-to-find — stays one click away.
        self._filter_search = search_field("Filter events…")
        # The search field is the toolbar's flexible element — it grows and
        # shrinks with the window (along with the gap to its left), so the fixed
        # controls stay put and the 3-tile grid alone sets the floor (Nelson
        # 2026). min/max keep it usable but bounded; tune to taste.
        self._filter_search.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._filter_search.setMinimumWidth(160)
        self._filter_search.setMaximumWidth(360)
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
        self._clear_btn.setStyleSheet(  # pragma: no-qss — layout-only padding override
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
        menu.setStyleSheet(  # pragma: no-qss — menu chrome
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

    # ── cross-event DC entry (retired spec/162 Round 3e) ──────────────

    # spec/162 Round 2b (2026-07-01) retired the ``_open_new_cross_
    # event_dc`` entry point + the ``CrossEventDcsDialog`` (Manage
    # Collections). Round 3e (this commit) retires the dead
    # ``_pin_cross_event_dc`` method that used to drive the DC-to-Cut
    # Pin flow: its dependencies (``dc_creator`` / ``dc_replacer`` /
    # ``dc_loader`` kwargs on :class:`NewCutDialog`) all retired with
    # the Save/Load-Collection surface (Round 2d.C/D). Cross-event Cut
    # composition now lives entirely on :class:`LibraryPage` via
    # ``+ New Cut`` — spec/162 §3.2.

    def _open_cross_event_cuts(self) -> None:
        """Open the cross-event Cuts browser. The browser gathers cuts with
        ``source_dc_kind = 'user'`` across every event.db in the library and
        offers Open / Export / Delete actions per row."""
        if self.gateway is None:
            return
        from mira.ui.pages.cross_event_cuts_dialog import CrossEventCutsDialog
        dialog = CrossEventCutsDialog(self.gateway, parent=self)
        dialog.export_requested.connect(self._on_export_cross_event_cut)
        dialog.open_requested.connect(self._on_open_cross_event_cut)
        dialog.exec()

    def _on_open_cross_event_cut(self, row) -> None:
        """Open the Cut detail viewer for a cross-event Cut row."""
        from mira.ui.pages.cross_event_cut_detail_dialog import (
            CrossEventCutDetailDialog,
        )
        dialog = CrossEventCutDetailDialog(self.gateway, row, parent=self)
        dialog.exec()

    def _on_export_cross_event_cut(self, row) -> None:
        """Drive the cross-event Cut export pipeline (Task 10). Routes each
        member through its source event for bytes (export-kind → hardlink
        from Exported Media/; grab-kind → copy from Original Media/)."""
        from mira.shared.cross_event_cut_export import (
            export_cross_event_cut,
            CrossEventExportError,
        )
        from PyQt6.QtWidgets import QFileDialog, QMessageBox

        target = QFileDialog.getExistingDirectory(
            self, "Pick export target",
        )
        if not target:
            return
        try:
            audio_root = ""
            try:
                audio_root = self.gateway.settings.load().audio_library_path or ""
            except Exception:                                  # noqa: BLE001
                audio_root = ""
            summary = export_cross_event_cut(
                self.gateway, row.anchor_event_id, row.cut_id,
                target=target,
                audio_root=audio_root or None,
            )
        except CrossEventExportError as exc:
            QMessageBox.warning(
                self, "Export failed",
                f"Could not export: {exc}",
            )
            return
        QMessageBox.information(
            self, "Export complete",
            f"Exported {summary['member_count']} file(s) "
            f"({summary['linked']} linked, {summary['copied']} copied, "
            f"{summary['missing']} missing).",
        )

    def _cross_event_thumb_resolver(self, sess_file):
        """Cached export thumb for one cross-event candidate, or ``None``.

        Spans events: resolve the file's source event_uuid → its on-disk
        root (the same umbrella-index lookup the cross-event exporter uses),
        then read the ALREADY-CACHED export thumb. Never generates a thumb
        synchronously (the known first-open freeze, see
        [[project-no-wait-feedback-thumb-generation]]) and never raises — a
        miss returns ``None`` so the grid paints a neutral placeholder
        instead of stalling. Grab-kind members (Original Media, no export
        thumb) and not-yet-visited events fall to the placeholder."""
        from PyQt6.QtGui import QPixmap
        from core import photo_thumb_cache
        try:
            entry = self.gateway.index.get(sess_file.event_uuid)
            if entry is None:
                return None
            root = self.gateway.index.resolve_root(
                entry, self.gateway.photos_base_path())
            if root is None:
                return None
            rel = sess_file.export_relpath or sess_file.origin_relpath
            if not rel:
                return None
            thumb = photo_thumb_cache.resolve_export_thumb(root, root / rel)
            if thumb is None:
                return None
            pm = QPixmap(str(thumb))
            return pm if not pm.isNull() else None
        except Exception:                                      # noqa: BLE001
            log.exception("cross-event thumb resolve failed")
            return None

    def _direct_commit_cross_event_cut(self, session, library_gateway) -> None:
        """Drive ``session.commit`` against the library gateway.

        spec/94 Phase 4a-ii: cross-event Cuts now live in mira.db
        (spec/93 §3) — no anchor event.db is opened. The library
        gateway's lifecycle is owned by the umbrella Gateway / events
        page; we just hand it off.

        spec/98 — on a name collision ("taken"), offer **Replace**:
        adopt the existing cross-event cut's id onto the session and
        re-commit, which then takes the update branch. Cancel re-
        raises so the CrossEventPickerDialog's existing warning shows
        and the user can rename."""
        try:
            session.commit(library_gateway)
        except ValueError as exc:
            if str(exc) != "taken":
                raise
            from core import cut_names as _names
            from mira.ui.design import confirm
            slug = _names.slugify(session.name)
            existing = library_gateway.cross_event_cut_by_tag(slug)
            if existing is None:
                raise
            if not confirm(
                self,
                tr("Replace existing?"),
                tr("A Cut named '{name}' already exists. Replace it?")
                .format(name=session.name),
                primary_text=tr("Replace"),
            ):
                raise
            prior_id = session.cut_id
            session.cut_id = existing.id
            try:
                session.commit(library_gateway)
            except Exception:                                # noqa: BLE001
                session.cut_id = prior_id
                raise

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

    def set_ingest_in_progress_ids(self, ids) -> None:
        """spec/84 §5 — events with an active background ingest stay
        OFF the tile grid until the ingest finishes. MainWindow pushes
        the current set in whenever it changes; the page re-filters so
        the change is visible immediately."""
        self._ingest_in_progress_ids = set(ids or ())
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
            # spec/84 §5 — a background ingest is still copying into
            # this event; hide the tile until the queue's
            # ``finished_result`` fires and ``set_ingest_in_progress_ids``
            # removes the id.
            if cd.event_id in self._ingest_in_progress_ids:
                continue
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
        # Toolbar chips — totals across ALL visible events (not the
        # search/status-filtered subset). spec/84 §5 — events that are
        # mid-ingest aren't visible, so exclude them from the chips too
        # (else "5 open" while the grid shows 4 reads as wrong). The
        # "showing N of M" label carries the search/status-filter truth.
        visible_cards = [
            c for c in self._card_data_by_id.values()
            if c.event_id not in self._ingest_in_progress_ids
        ]
        total = len(visible_cards)
        open_n = sum(1 for c in visible_cards if not c.is_closed)
        closed_n = total - open_n
        days_n = sum(c.total_days or 0 for c in visible_cards)
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
