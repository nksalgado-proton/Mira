"""The application main window — the persistent chrome: menu bar + page stack.

spec/46 Slice 1 retired the persistent global :class:`~mira.ui.shell.sidebar.Sidebar`
rail (2026-06-06). A first replacement put a contextual ``LibrarySidebar`` on the
events-list page; Nelson eyeball-revised that to "make it look like a Windows app" —
the sidebar was eliminated entirely and every former rail entry became a top-level
menu item on the **menu bar**:

  * **File**   → New event · Create from photos · Wizard · Settings (Ctrl+,) · Quit
  * **View**   → Library (Ctrl+L)  — return to the events list
  * **Events** → New event · Create from photos · Restore from backup · Back up event ·
                 Back up SD card
  * **Plan**   → Download plan template
  * **Cull**   → Quick Sweep · Picker
  * **Process** → Photo Processor
  * **Curate** → Audio
  * **Help**   → Third-party tool guides

Surfaces not yet reassembled get a :class:`~mira.ui.pages.placeholder.PlaceholderPage`
so the whole shell is navigable now; each real page (events list, ingest, …) replaces its
placeholder as the reassembly reaches it.

Holds the one :class:`~mira.gateway.Gateway` the UI talks to (charter §2 — UI →
gateway → store, one-way). Pages are handed the gateway as they are built.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QMainWindow, QVBoxLayout, QWidget,
)

log = logging.getLogger(__name__)

from mira.gateway import Gateway
from mira.ui.pages.picker_page import PickerPage


def _peek_target_from_settings(default: int = 20) -> int:
    """Read the ``peek_target_photos`` Setting (Nelson 2026-06-09
    audit promotion) with a defensive fallback to the prior hardcoded
    default if Settings can't be read."""
    try:
        from mira.settings.repo import SettingsRepo
        return int(SettingsRepo().load().peek_target_photos)
    except Exception:                                           # noqa: BLE001
        return default
from mira.ui.i18n import tr
from mira.event_classification import (
    PHASE_COLLECT,
    PHASE_EDIT,
    PHASE_EXPORT,
    PHASE_PICK,
    PHASE_SHARE,
)
from mira.ui.pages.days_grid_page import DaysGridPage
from mira.ui.pages.days_lists_page import DaySnapshot, DaysListsPage
from mira.ui.pages.events_page import EventsPage
from mira.ui.pages.phases_page import PhasesPage
from mira.ui.pages.new_event_page import NewEventPage
from mira.ui.pages.placeholder import PlaceholderPage
from mira.ui.shell.page_stack import PageStack
from mira.ui.shell.sidebar import (
    ENTRY_AUDIO,
    ENTRY_BACK_UP_CARD,
    ENTRY_BACK_UP_EVENT,
    ENTRY_CREATE_FROM_PAST,
    ENTRY_CULLER_STANDALONE,
    ENTRY_DASHBOARD,
    ENTRY_FAST_CULLER_STANDALONE,
    ENTRY_HELPERS,
    ENTRY_NEW_EVENT,
    ENTRY_PHOTO_PROCESSOR,
    ENTRY_PLAN_TEMPLATE,
    ENTRY_RESTORE_FROM_BACKUP,
    ENTRY_SETTINGS,
    ENTRY_WIZARD,
)


class MainWindow(QMainWindow):
    # spec/46 Slice 2+3 (2026-06-06): event-card click goes direct to the
    # activity dashboard. EventPlanPage + EventDashboardPage retired; their
    # keys folded into _ACTIVITY_PAGE_KEY.
    _ACTIVITY_PAGE_KEY = "__activity_dashboard__"
    # spec/65 §3.5 — Days Lists sits between Phases (03) and Pick (07).
    _DAYS_LISTS_PAGE_KEY = "__days_lists__"
    # spec/70 Phase 3 — Days Grid (Surface 06) sits between Days Lists
    # (05) and the legacy Pick surface. The redesigned DaysGridPage
    # absorbs the gateway/engine wiring the legacy DayGridView carried.
    _DAYS_GRID_PAGE_KEY = "__days_grid__"
    _SELECT_PAGE_KEY = "__select__"
    _PROCESS_PAGE_KEY = "__process__"
    # spec/70 Phase 3 §3 + Surface 12 fold (2026-06-15): the separate
    # Video Editor page is gone — every Edit-phase item, photo or video,
    # routes through :class:`EditorPage`. When the cursor lands on a
    # video the canvas becomes a video in place (spec/63 §3
    # arm-on-landing) and the spec/56 marker workshop reveals under it.
    # The constant survives as ``None`` so any straggling reference
    # surfaces as an AttributeError instead of a silent miss.
    _PROCESS_VIDEO_PAGE_KEY = None
    # spec/68 §3 — Export retired its own surface (the flat-grid MVP
    # at ``mira/ui/exported/export_page.py``); the phase now rides the
    # shared Phases → Days Lists → Days Grid spine like Pick/Edit. The
    # key constant survives as ``None`` so any straggling reference
    # surfaces as an AttributeError instead of a silent miss.
    _EXPORT_PAGE_KEY = None
    _CURATE_PAGE_KEY = "__curate__"
    # spec/70 Phase 3 — Quick Sweep (Collect-phase triage), redesigned.
    # Hosts both menu entries: "Standalone Quick Sweep…" (events list)
    # and "Quick Sweep this event…" (per-event).
    _QUICK_SWEEP_PAGE_KEY = "__quick_sweep__"

    def __init__(self, gateway: Optional[Gateway] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.gateway = gateway or Gateway()
        self.setWindowTitle(tr("Mira"))
        self.resize(1180, 760)

        central = QWidget()
        # Styled root — carries the redesign's radial background glow
        # (#RedesignRoot in redesign.qss). WA_StyledBackground makes a plain
        # QWidget actually paint its QSS background.
        from PyQt6.QtCore import Qt as _Qt
        central.setObjectName("RedesignRoot")
        central.setAttribute(_Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # spec/76 §B.1 — persistent read-only banner. Names the editing
        # machine when another Mira holds the writer lock; hidden + zero
        # height when this app owns the lock. Sits above the batch line
        # so a single glance from any page reveals "you can't write".
        from mira.ui.shell.read_only_banner import ReadOnlyBanner
        self.read_only_banner = ReadOnlyBanner()
        outer.addWidget(self.read_only_banner)

        # spec/59 §8 + spec/84 §2 — the app-level batch queue + its ONE
        # progress line, directly below the menubar, visible from every
        # page; hidden when idle. Jobs run strictly one at a time. Both
        # exports AND ingest copies ride this queue (spec/84 generalised
        # the queue from export-only to all batch jobs).
        from mira.ui.shell.batch_queue import (
            BatchJobQueue, BatchProgressLine,
        )
        self.batch_queue = BatchJobQueue(self)
        self.batch_line = BatchProgressLine()
        self.batch_line.bind(self.batch_queue)
        outer.addWidget(self.batch_line)

        body = QWidget()
        row = QHBoxLayout(body)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        outer.addWidget(body, stretch=1)

        # spec/46 Slice 1 (Nelson 2026-06-06 eyeball): the persistent left rail is
        # gone — every navigation entry is now a menu item on the menu bar. The
        # window holds only a PageStack.
        self.page_stack = PageStack()

        # Register a destination for every navigation key. Real pages replace these
        # placeholders as the reassembly reaches them. Modal openers (Wizard,
        # Create-from-Photos, Quick Sweep, Picker, Settings) still get a placeholder
        # registered but are intercepted in `_on_entry` and never actually displayed.
        self.events_page = EventsPage(self.gateway)
        self.new_event_page = NewEventPage(self.gateway)
        self.page_stack.add_page(ENTRY_DASHBOARD, self.events_page)
        self.page_stack.add_page(ENTRY_NEW_EVENT, self.new_event_page)
        # Every other ENTRY_* key gets a PlaceholderPage on the stack so the
        # dispatcher's show_page() resolves to *something* for the deferred
        # surfaces. Modal openers (Wizard / Create-from-Photos / Quick Sweep /
        # Picker / Settings) intercept in _on_entry before the page swap.
        for key, label in self._navigation_entries():
            if key in (ENTRY_DASHBOARD, ENTRY_NEW_EVENT):
                continue  # already registered with real pages
            self.page_stack.add_page(key, PlaceholderPage(label))

        # spec/46 Slice 2+3 (2026-06-06): the activity dashboard is the SINGLE
        # per-event landing. Click an event card → land here. Per-event chrome
        # (edit info, edit plan, manage days, camera clocks, adjust TZ, etc.)
        # moves to the Event menu on the menu bar; the 2×2 retires (its TZ map
        # + funnel land in the Event menu's Stats… modal).
        self.phases_page = PhasesPage(self.gateway)
        self.page_stack.add_page(self._ACTIVITY_PAGE_KEY, self.phases_page)

        # spec/65 §3.5 — Days Lists: the "pick where to start" surface that
        # lands between Phases and Pick. Built off gateway.phase_day_progress()
        # + cached_buckets(); a day-click opens the per-day Pick view.
        self.days_lists_page = DaysListsPage(self.gateway)
        self.page_stack.add_page(
            self._DAYS_LISTS_PAGE_KEY, self.days_lists_page)

        # spec/70 Phase 3 — Days Grid (Surface 06). The redesigned grid
        # absorbs the gateway/engine wiring the legacy DayGridView
        # carried; single-item clicks bridge to PickerPage (Surface 07,
        # also redesigned) via :meth:`_on_days_grid_item_activated`.
        self.days_grid_page = DaysGridPage(self.gateway)
        self.page_stack.add_page(
            self._DAYS_GRID_PAGE_KEY, self.days_grid_page)

        # spec/70 Phase 3 §2 — Surface 07 (Picker) is now the redesigned
        # PickerPage: PhotoViewport + the absorbed PickPhotoSurface wiring
        # (decision persistence, sharpness honesty, visited stamping,
        # cluster cover expansion, sweep-with-peaking, F10 lens). The
        # legacy ``mira/ui/picked/pick_page.py`` + ``pick_photo_surface.py``
        # stay in tree for the Quick Sweep session that comes next.
        self.picker_page = PickerPage(self.gateway)
        self.page_stack.add_page(self._SELECT_PAGE_KEY, self.picker_page)

        # spec/70 Phase 3 §3 + Surface 12 fold (2026-06-15) — Surface 08
        # (Editor) AND Surface 12 (Video Editor) are now both the SAME
        # redesigned EditorPage: PhotoViewport (embedded) + the absorbed
        # AdjustmentSurface engine + edit_prep working-copy worker +
        # the crop overlay's draggable handles + the spec/56 marker
        # workshop revealed in place when the cursor lands on a video.
        # The legacy ``mira/ui/edited/edit_host_page.py`` + ``edit_page.py``
        # + ``edit_video_page.py`` + ``mira/ui/pages/video_editor_page.py``
        # all retire with this surface; one page now owns every Edit-
        # phase item, photo or video, on one route.
        from mira.ui.pages.editor_page import EditorPage
        self.edit_page = EditorPage(self.gateway)
        self.page_stack.add_page(self._PROCESS_PAGE_KEY, self.edit_page)

        # spec/70 Phase 3 — Quick Sweep (Collect-phase triage). Redesigned
        # over the SAME DaysLists → DaysGrid → viewer route the Picker
        # uses (Nelson 2026-06-14). The QuickSweepPage is the leaf viewer;
        # DaysListsPage + DaysGridPage carry the nav levels (paths mode
        # for standalone via setEventForPreview / setDay; gateway mode
        # for per-event).
        from mira.ui.pages.quick_sweep_page import QuickSweepPage
        self.quick_sweep_page = QuickSweepPage()
        self.page_stack.add_page(
            self._QUICK_SWEEP_PAGE_KEY, self.quick_sweep_page)
        # Per-session Quick Sweep state — None when no QS session is
        # running, dict when active. Carries dest + event_id (mode), the
        # K/D ledger, and the per-day item lists so day cards can rebuild
        # the grid without re-scanning. ``_quick_sweep_current_day_items``
        # is the list the viewer walks.
        self._quick_sweep: Optional[dict] = None

        # spec/84 §5 — events currently being ingested via the shared
        # batch queue (the IngestJob hasn't fired ``finished_result``
        # yet). The event RECORD exists from OK time so the queue's
        # commit closure can write rows against the gateway, but the
        # tile is HIDDEN from the Events screen (and Pick rejects the
        # entry, and a second same-event enqueue is blocked) until the
        # ingest finishes. Cleared by :meth:`_mark_ingest_finished` from
        # :meth:`_finish_collect_ingest`.
        self._ingesting_event_ids: set[str] = set()

        # (Surface 08 + Surface 12 page-stack entries were registered
        # alongside the redesigned Picker above — spec/70 Phase 3 §3.)

        # spec/68 §3 — the Export phase is no longer a flat event-wide
        # surface (that pattern belongs to Share / Cuts, spec/61 §5.1).
        # Export now reuses the same Phases → Days Lists → Days Grid
        # spine as Pick / Edit (``_export_phase_active`` below + the
        # ``"export"`` phase argument on
        # :meth:`DaysGridPage.open_for_day`). The flat-grid MVP at
        # ``mira/ui/exported/export_page.py`` was retired with this
        # commit; the per-day batch trigger lives on the grid's
        # toolbar and submits through
        # :func:`mira.ui.exported.batch.submit_export_batch`.

        # spec/70 Phase 3 §5 — Surface 09 (Share / Cuts) is now the
        # redesigned ShareCutsPage: the spec/71 identity header + the
        # spec/65 §3.9 list visual (pool card + Cut rows with the kebab
        # for rare actions), wrapping the spec/61 list ↔ detail ↔
        # session stack. Gated to closed events only (spec/66/68) — the
        # Share menu uses _SURFACE_CLOSED_EVENT and the closed-tile body
        # click routes here directly via _open_event_cuts_list.
        from mira.ui.pages.share_cuts_page import ShareCutsPage
        self.curate_page = ShareCutsPage(self.gateway)
        self.page_stack.add_page(self._CURATE_PAGE_KEY, self.curate_page)

        self._current_event_id: Optional[str] = None
        # spec/64 §2.4 (Nelson 2026-06-13): the Cuts shell has two
        # entry doors — Share-phase tile on the activity dashboard
        # (returns to that dashboard on Back) and the closed-tile
        # body click (returns to the events list). The flag remembers
        # which door the user came in through; ``_on_curate_closed``
        # routes Back accordingly.
        self._cuts_entry_door: str = self._ACTIVITY_PAGE_KEY
        # spec/70 Phase 3 — when the user enters PickerPage via the
        # Days Grid (Surface 06) item-click bridge, Back from the Picker
        # returns to the Days Grid, not Phases. Cleared whenever the
        # user lands on Days Grid via the normal route.
        self._days_grid_bridge_active: bool = False
        # spec/70 Phase 3 §3 + Surface 12 fold (2026-06-15) — set while
        # the Edit-phase route is active (Phases → DaysLists → DaysGrid
        # → EditorPage). Tells the shared Days Grid item-click handler
        # to route to EditorPage instead of PickerPage; the EditorPage
        # itself sweeps photos AND videos in one bucket. Consumed when
        # the user leaves the Edit phase back to Phases.
        self._edit_phase_active: bool = False
        # spec/68 §3 — set while the Export-phase route is active
        # (Phases → DaysLists → DaysGrid in Export mode). The Days
        # Grid in Export mode handles clicks itself (toggle in place,
        # no drill-in), so the item-activated signal is short-circuited
        # for this branch — see :meth:`_on_days_grid_item_activated`.
        self._export_phase_active: bool = False

        # spec/82 §A.1 — periodic-while-open snapshot timer. Crash
        # insurance: take a ``reason="periodic"`` snapshot every
        # ``backup_periodic_minutes`` (settings, default 15) minutes
        # for the current event, but only when its db has changed
        # since the last snapshot. ``backup_snapshots_enabled=False``
        # short-circuits the timer entirely (the spec/82 §G master
        # toggle); ``backup_periodic_minutes=0`` is the per-class
        # off switch (milestones still fire through their own
        # triggers).
        from mira.ui.shell.periodic_snapshot import (
            DEFAULT_INTERVAL_MINUTES, PeriodicSnapshotter,
        )
        from mira.settings.repo import SettingsRepo as _SettingsRepo
        _snap_settings = _SettingsRepo().load()
        _interval = (
            int(getattr(
                _snap_settings, "backup_periodic_minutes",
                DEFAULT_INTERVAL_MINUTES))
            if bool(getattr(
                _snap_settings, "backup_snapshots_enabled", True))
            else 0
        )
        self._periodic_snapshotter = PeriodicSnapshotter(
            self.gateway,
            current_event_id=lambda: self._current_event_id,
            interval_minutes=_interval,
            parent=self,
        )
        self._periodic_snapshotter.start()

        # spec/82 §A.3 — snapshot the user-data store on quit if it
        # changed this session. mira.db carries settings, the library
        # index and templates; the same db_backup primitive that
        # protects event.db protects it. Ride the existing aboutToQuit
        # signal (already used by the writer-lock teardown) — the
        # gateway is still alive at this point so the dirty check has
        # a live connection.
        from PyQt6.QtWidgets import QApplication as _QApp
        _qapp = _QApp.instance()
        if _qapp is not None:
            _qapp.aboutToQuit.connect(self._snapshot_user_store_on_quit)
            # spec/82 §G — automatic backup-on-quit. When the user has
            # opted in via the Backups settings tab AND set an
            # event_backup_destination, the currently-open event is
            # exported as a Part-B bundle to that folder on quit. Same
            # primitive as the manual Back up event… action so both
            # paths ride one engine.
            _qapp.aboutToQuit.connect(self._backup_event_on_quit)

        row.addWidget(self.page_stack, stretch=1)
        self.setCentralWidget(central)
        self._build_menu_bar()
        self._install_title_bar()

        self.events_page.event_activated.connect(self._open_event)
        self.events_page.event_info_requested.connect(self._open_event_info_dialog)
        self.events_page.event_plan_requested.connect(self._open_event_plan_from_card)
        self.events_page.event_status_toggle_requested.connect(
            self._on_card_status_toggle_requested)
        # spec/77 §6 — the v2 tile's ⋮ Delete entry routes here with
        # the event id pre-selected so the existing
        # :meth:`_on_delete_event` flow runs without needing a menu-bar
        # round-trip.
        self.events_page.event_delete_requested.connect(
            self._on_card_delete_requested)
        self.events_page.classify_all_requested.connect(self._open_event_triage)
        # Redesign-new entry points (Surface 01):
        # - the PageHeader's primary "+ New Event" button routes to the
        #   NewEventPage via the existing navigation entry, so the menu
        #   item and the button share one code path.
        # - the Cross-Event Cuts band stub logs the query until the
        #   cross_event_search(query) endpoint lands.
        self.events_page.new_event_requested.connect(
            lambda: self._on_entry(ENTRY_NEW_EVENT)
        )
        self.events_page.cross_event_query.connect(
            lambda q: log.info("cross-event search (stub): %r", q)
        )
        self.new_event_page.event_created.connect(self._on_new_event_created)
        self.new_event_page.cancelled.connect(self._on_new_event_cancelled)
        self.phases_page.back_requested.connect(self._on_event_back)
        self.phases_page.phase_tile_activated.connect(
            self._on_phase_tile_activated)
        # spec/65 §3.5 — DaysListsPage signal wiring. Back returns to
        # PhasesPage; a day-card click opens the Pick surface anchored to
        # that day. The header's New-pass / Pick-all / Skip-all buttons
        # are still TBD (they belong to the Pick surface, out of scope
        # for the route-swap session); they log + flash a hint for now.
        self.days_lists_page.back_requested.connect(
            self._on_days_lists_back)
        self.days_lists_page.day_activated.connect(
            self._on_days_lists_day_activated)
        self.days_lists_page.new_pass_requested.connect(
            self._on_days_lists_new_pass_stub)
        self.days_lists_page.pick_all_days_requested.connect(
            self._on_days_lists_pick_all_stub)
        self.days_lists_page.skip_all_days_requested.connect(
            self._on_days_lists_skip_all_stub)
        self.days_lists_page.day_pick_all_requested.connect(
            self._on_days_lists_day_pick_all_stub)
        self.days_lists_page.day_skip_all_requested.connect(
            self._on_days_lists_day_skip_all_stub)
        # spec/70 Phase 3 — DaysGridPage signal wiring. Back returns to
        # Days Lists; an item click bridges to PickerPage (Surface 07,
        # redesigned + PhotoViewport-backed).
        self.days_grid_page.back_requested.connect(
            self._on_days_grid_back)
        self.days_grid_page.item_activated.connect(
            self._on_days_grid_item_activated)
        self.days_grid_page.prev_day_requested.connect(
            lambda: self._on_days_grid_step_day(-1))
        self.days_grid_page.next_day_requested.connect(
            lambda: self._on_days_grid_step_day(+1))
        self.days_grid_page.new_pass_requested.connect(
            self._on_days_lists_new_pass_stub)
        self.picker_page.closed.connect(self._on_select_closed)
        self.picker_page.fullscreen_changed.connect(self._on_select_fullscreen)
        self.edit_page.closed.connect(self._on_process_closed)
        self.edit_page.fullscreen_changed.connect(self._on_process_fullscreen)
        # (Surface 12 folded into EditorPage 2026-06-15 — no separate
        # video-edit page wiring; EditorPage's closed/fullscreen signals
        # cover both photo and video items.)
        # spec/68 §3 — Export now reuses the DaysGrid; its lifecycle
        # signals land on the existing Days Grid wiring (the back path
        # clears ``_export_phase_active``).
        self.curate_page.closed.connect(self._on_curate_closed)
        # Quick Sweep — page-stack hosting: saved → mode-aware finalize
        # (copy keepers for standalone, log + leave the gateway alone
        # for per-event); cancelled / closed → return to whichever door
        # the user came in through.
        self.quick_sweep_page.saved.connect(self._on_quick_sweep_saved)
        self.quick_sweep_page.cancelled.connect(
            self._on_quick_sweep_cancelled)
        self.quick_sweep_page.fullscreen_changed.connect(
            self._on_quick_sweep_fullscreen)

        # Land on Dashboard, shown explicitly (single source of truth for the start page).
        self.page_stack.show_page(ENTRY_DASHBOARD)

    def _on_entry(self, key: str) -> None:
        """Library sidebar Actions-band dispatch — switch pages, or fire an action
        for action-style entries (modals, standalone tools, settings)."""
        if key == ENTRY_WIZARD:
            self._open_wizard()
            return
        # Two real creation workflows (spec/52 reinstated them; spec/57
        # §4.3 turned the second into the backfill wizard):
        #   * ENTRY_NEW_EVENT — "New event" with NO media: fills event
        #     info only, lands on an empty event. Plan grows as Collect
        #     adds media later.
        #   * ENTRY_CREATE_FROM_PAST — "New event from existing media…":
        #     landing-level choice → source pick → scan → unified
        #     info+plan dialog → auto-Collect → land at the level's
        #     phase surface.
        if key == ENTRY_NEW_EVENT:
            self._open_new_event_info_only()
            return
        if key == ENTRY_CREATE_FROM_PAST:
            self._open_new_event_flow()
            return
        if key == ENTRY_SETTINGS:
            self._open_settings()
            return
        if key == ENTRY_FAST_CULLER_STANDALONE:
            # spec/70 Phase 3 — the standalone Quick Sweep now hosts
            # the redesigned page on the stack. The legacy modal-window
            # path was retired with the Surface 07 cadence.
            self._open_quick_sweep_standalone()
            return
        if key == ENTRY_CULLER_STANDALONE:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, tr("Picker"),
                tr("The full Picker runs inside an event for now — open an event and "
                   "choose Cull. Standalone folder culling is planned."))
            self.page_stack.show_page(ENTRY_DASHBOARD)
            return
        if key == ENTRY_DASHBOARD:
            self.events_page.refresh()
        self.page_stack.show_page(key)

    # ── Menu spec (Nelson 2026-06-09 design session — App/Event/phase model) ─
    #
    # Top-level set: App · Event · Collect · Pick · Edit · Share · Help.
    # Children context-dependent per (surface, event-state, feature-flag).
    # Surface: ``"events_list"`` (no event open) vs ``"per_event"`` (an
    # event is open). Some entries appear on ``"both"``. A top-level
    # hides when every child resolves hidden in the current context.

    # Surface tags for each menu action.
    _SURFACE_EVENTS_LIST = "events_list"
    _SURFACE_PER_EVENT = "per_event"
    _SURFACE_BOTH = "both"
    # spec/66 §4 + spec/68 — Share is a closed-event STATE (Cuts live
    # there). The Share menu items are visible only when the open event
    # is closed; the empty-children rule then hides the whole top-level.
    _SURFACE_CLOSED_EVENT = "closed_event"

    # ────────────────────────────────────────────────────────────────────────

    def _navigation_entries(self) -> list[tuple[str, str]]:
        """The flat ``(key, label)`` list of every navigable entry. Drives
        the PageStack placeholder-registration loop in ``__init__`` so
        every ``ENTRY_*`` key resolves to *something* on the stack — modal
        openers (Wizard / Settings / standalone tools) get a placeholder
        page registered but are intercepted by :meth:`_on_entry` before
        the page swap.

        Was previously the union of File + the six workflow-stage menus;
        post-2026-06-09 menu redesign (App / Event / Collect / Pick /
        Edit / Share / Help) the menu structure is built declaratively
        in :meth:`_build_menu_bar`. This list now just enumerates the
        ENTRY keys that need a stack placeholder."""
        return [
            (ENTRY_DASHBOARD, tr("Library")),
            (ENTRY_NEW_EVENT, tr("New event")),
            (ENTRY_CREATE_FROM_PAST, tr("New event from existing media")),
            (ENTRY_WIZARD, tr("Wizard")),
            (ENTRY_SETTINGS, tr("Settings")),
            (ENTRY_RESTORE_FROM_BACKUP, tr("Restore from backup")),
            (ENTRY_BACK_UP_EVENT, tr("Back up event")),
            (ENTRY_BACK_UP_CARD, tr("Back up SD card")),
            (ENTRY_PLAN_TEMPLATE, tr("Download plan template")),
            (ENTRY_FAST_CULLER_STANDALONE, tr("Quick Sweep")),
            (ENTRY_CULLER_STANDALONE, tr("Picker")),
            (ENTRY_PHOTO_PROCESSOR, tr("Photo Processor")),
            (ENTRY_AUDIO, tr("Audio")),
            (ENTRY_HELPERS, tr("Third-party tool guides")),
        ]

    def _build_menu_bar(self) -> None:
        """Build the redesigned menu bar (Nelson 2026-06-09):
        ``App · Event · Collect · Pick · Edit · Share · Help``.

        Children are surface-aware — each action carries one of
        ``_SURFACE_EVENTS_LIST`` / ``_SURFACE_PER_EVENT`` / ``_SURFACE_BOTH``
        and :meth:`_refresh_menu_state` toggles visibility accordingly.
        A top-level menu hides automatically when every child resolves
        hidden in the current context (e.g. Collect/Share on the events
        list).

        Modification actions (Edit info / Edit plan / Manage days / TZ
        adjustments / Delete) carry a flag and additionally hide when
        the open event is closed (F-024 rule unchanged from prior
        design).
        """
        from PyQt6.QtGui import QAction, QKeySequence

        # Per-action visibility registry — populated by :meth:`_add_menu_action`
        # and consumed by :meth:`_refresh_menu_state`. ``_modification_actions``
        # is the F-024 subset that also hides when the open event is closed.
        self._menus: dict[str, "QMenu"] = {}
        self._action_surfaces: dict["QAction", str] = {}
        self._modification_actions: list = []

        # ── App ────────────────────────────────────────────────────────────
        # Library (per-event only) · Wizard · Settings · Audit (per-event) · Quit.
        app_menu = self.menuBar().addMenu(tr("&App"))
        self._menus["app"] = app_menu
        self._add_menu_action(
            app_menu, tr("&Library"), self._go_to_library,
            surface=self._SURFACE_PER_EVENT, shortcut="Ctrl+L",
            tooltip=tr("Return to the events list."))
        app_menu.addSeparator()
        self._add_menu_action(
            app_menu, tr("&Wizard…"), self._open_wizard)
        app_menu.addSeparator()
        self._add_menu_action(
            app_menu, tr("&Settings…"), self._open_settings,
            shortcut="Ctrl+,",
            tooltip=tr("Open the app settings dialog."))
        # spec/82 §A.3 — restore the user-data store (settings,
        # library index, templates) from a snapshot. Lives next to
        # Settings because the two share a topic; the per-event
        # restore lives on the Event menu.
        self._add_menu_action(
            app_menu, tr("Restore &user data…"),
            self._open_restore_user_store,
            tooltip=tr(
                "Roll back settings, library index and templates "
                "to an earlier snapshot."))
        app_menu.addSeparator()
        self._add_menu_action(
            app_menu, tr("A&udit…"),
            lambda: self._coming_next(tr("Audit")),
            surface=self._SURFACE_PER_EVENT)
        app_menu.addSeparator()
        # Quit is the only action with a Qt StandardKey shortcut; built
        # inline because :meth:`_add_menu_action` takes a string shortcut.
        quit_action = QAction(tr("&Quit"), self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        app_menu.addAction(quit_action)
        self._action_surfaces[quit_action] = self._SURFACE_BOTH

        # ── Event ──────────────────────────────────────────────────────────
        # Events list: New event · New event from existing media · Restore from backup.
        # Per-event:   Edit info · Stats · Back up event · Close ↔ Re-open · Delete.
        event_menu = self.menuBar().addMenu(tr("&Event"))
        self._menus["event"] = event_menu
        self._add_menu_action(
            event_menu, tr("&New event"),
            lambda: self._on_entry(ENTRY_NEW_EVENT),
            surface=self._SURFACE_EVENTS_LIST, shortcut="Ctrl+N")
        self._add_menu_action(
            event_menu, tr("New event from existing &media…"),
            lambda: self._on_entry(ENTRY_CREATE_FROM_PAST),
            surface=self._SURFACE_EVENTS_LIST, shortcut="Ctrl+Shift+N")
        # Per-event entries.
        self._add_menu_action(
            event_menu, tr("Edit &info…"), self._open_edit_info,
            surface=self._SURFACE_PER_EVENT, modification=True)
        event_menu.addSeparator()
        self._add_menu_action(
            event_menu, tr("&Stats…"), self._open_stats,
            surface=self._SURFACE_PER_EVENT)
        event_menu.addSeparator()
        # spec/82 §B.2 — Back up event… (manual migration bundle
        # export). Writes a self-contained bundle to a user-chosen
        # destination folder. The automatic on-quit variant ships
        # in slice 8 from the Backups settings tab; both ride the
        # same core.event_bundle.export_event primitive.
        self._add_menu_action(
            event_menu, tr("&Back up event…"),
            self._open_back_up_event,
            surface=self._SURFACE_PER_EVENT)
        # spec/82 §B.3 — Restore event… (import a migration bundle
        # from another installation). Menu-only entry per Nelson's
        # 2026-06-17 decision; no auto-discovery on drive mount.
        self._add_menu_action(
            event_menu, tr("Re&store event…"),
            self._open_restore_event,
            surface=self._SURFACE_EVENTS_LIST, modification=True)
        # spec/82 §A.4 — manual restore (the trip-workflow "roll back
        # to before yesterday's ingest" case). The corruption-driven
        # auto-restore that already lives on event open (spec/79 §4)
        # always picks the latest good snapshot; this entry lets the
        # user pick an older one deliberately.
        self._add_menu_action(
            event_menu, tr("&Restore from backup…"),
            self._open_restore_backup,
            surface=self._SURFACE_PER_EVENT, modification=True)
        event_menu.addSeparator()
        # Close / Re-open is a label-swap on a single action.
        self._close_toggle_action = QAction(tr("&Close Event"), self)
        self._close_toggle_action.triggered.connect(self._on_close_toggled)
        event_menu.addAction(self._close_toggle_action)
        self._action_surfaces[self._close_toggle_action] = self._SURFACE_PER_EVENT
        event_menu.addSeparator()
        self._add_menu_action(
            event_menu, tr("&Delete event"), self._on_delete_event,
            surface=self._SURFACE_PER_EVENT, modification=True)

        # ── Collect ────────────────────────────────────────────────────────
        # Per-event only (Nelson 2026-06-09):
        #   * Edit Event — alias of Event→Edit info; opens the unified
        #     info+plan dialog (same surface, label reads naturally here).
        #   * Edit plan — plan-only editor with Save/Load CSV + Delete-day.
        #     Once photos are attached, only country/location/description
        #     are editable (TZ + date are frozen) so a re-imported plan
        #     can't shift photos across a TZ boundary.
        #   * Manage days · TZ adjustments · Re-import LRC.
        # Hides on the events list (empty in cross-event context).
        collect_menu = self.menuBar().addMenu(tr("&Collect"))
        self._menus["collect"] = collect_menu
        self._add_menu_action(
            collect_menu, tr("&Edit Event…"), self._open_edit_info,
            surface=self._SURFACE_PER_EVENT, modification=True)
        self._add_menu_action(
            collect_menu, tr("Edit &plan…"), self._open_edit_plan_for_event,
            surface=self._SURFACE_PER_EVENT, modification=True)
        collect_menu.addSeparator()
        self._add_menu_action(
            collect_menu, tr("Manage &days…"),
            self._open_manage_days_for_event,
            surface=self._SURFACE_PER_EVENT, modification=True)
        collect_menu.addSeparator()
        self._add_menu_action(
            collect_menu, tr("&Camera clocks…"),
            self._open_camera_clocks_for_event,
            surface=self._SURFACE_PER_EVENT, modification=True)
        self._add_menu_action(
            collect_menu, tr("&Adjust TZ…"),
            self._open_adjust_tz_for_event,
            surface=self._SURFACE_PER_EVENT, modification=True)
        collect_menu.addSeparator()
        self._add_menu_action(
            collect_menu, tr("Re-import from &LRC…"),
            lambda: self._coming_next(tr("Re-import from LRC")),
            surface=self._SURFACE_PER_EVENT, modification=True)

        # ── Pick ───────────────────────────────────────────────────────────
        # Events list: Standalone Picker · Standalone Quick Sweep.
        # Per-event:   Open Pick phase · Quick Sweep this event.
        pick_menu = self.menuBar().addMenu(tr("&Pick"))
        self._menus["pick"] = pick_menu
        self._add_menu_action(
            pick_menu, tr("Standalone &Picker…"),
            lambda: self._on_entry(ENTRY_CULLER_STANDALONE),
            surface=self._SURFACE_EVENTS_LIST)
        self._add_menu_action(
            pick_menu, tr("Standalone &Quick Sweep…"),
            lambda: self._on_entry(ENTRY_FAST_CULLER_STANDALONE),
            surface=self._SURFACE_EVENTS_LIST)
        self._add_menu_action(
            pick_menu, tr("&Open Pick phase"),
            lambda: self._on_phase_activated(PHASE_PICK),
            surface=self._SURFACE_PER_EVENT)
        self._add_menu_action(
            pick_menu, tr("&Quick Sweep this event…"),
            self._open_quick_sweep_for_event,
            surface=self._SURFACE_PER_EVENT)

        # ── Edit ───────────────────────────────────────────────────────────
        # Events list: Standalone Photo Processor.
        # Per-event:   Open Edit phase.
        edit_menu = self.menuBar().addMenu(tr("&Edit"))
        self._menus["edit"] = edit_menu
        self._add_menu_action(
            edit_menu, tr("Standalone &Photo Processor…"),
            lambda: self._on_entry(ENTRY_PHOTO_PROCESSOR),
            surface=self._SURFACE_EVENTS_LIST)
        self._add_menu_action(
            edit_menu, tr("&Open Edit phase"),
            lambda: self._on_phase_activated(PHASE_EDIT),
            surface=self._SURFACE_PER_EVENT)
        # spec/57 §2.2 — the manual refresh of the Picked Media links
        # projection (the external tools' doorway). Entering Edit also
        # rebuilds it automatically; this is the re-pick catch-up.
        self._add_menu_action(
            edit_menu, tr("&Refresh Picked Media links"),
            lambda: self._refresh_picked_media(quiet=False),
            surface=self._SURFACE_PER_EVENT)
        # spec/57 §3.3 — the explicit discovery action: adopt stacker
        # outputs from the Picked Media root + associate editor returns
        # under Edited Media. Entering Edit scans automatically too.
        self._add_menu_action(
            edit_menu, tr("&Scan for external results"),
            lambda: self._scan_external_returns(quiet=False),
            surface=self._SURFACE_PER_EVENT)

        # ── Export ─────────────────────────────────────────────────────────
        # Per-event only (spec/66 §1.1): the green/red ship decision over
        # all picked keepers. The trigger lives on the surface; this menu
        # entry is the keyboard-friendly door.
        export_menu = self.menuBar().addMenu(tr("E&xport"))
        self._menus["export"] = export_menu
        self._add_menu_action(
            export_menu, tr("&Open Export phase"),
            lambda: self._on_phase_activated(PHASE_EXPORT),
            surface=self._SURFACE_PER_EVENT)

        # ── Share ──────────────────────────────────────────────────────────
        # spec/66: Share is a closed-event STATE, not a phase. The menu
        # appears ONLY when the open event is closed (the empty-children
        # rule hides the whole top-level on open events + the events list).
        share_menu = self.menuBar().addMenu(tr("&Share"))
        self._menus["share"] = share_menu
        self._add_menu_action(
            share_menu, tr("&Open Cuts"),
            lambda: self._on_phase_activated(PHASE_SHARE),
            surface=self._SURFACE_CLOSED_EVENT,
            tooltip=tr(
                "Assemble Cuts from the exported files for hand-off."))
        self._add_menu_action(
            share_menu, tr("&New Cut…"),
            self._menu_new_cut,
            surface=self._SURFACE_CLOSED_EVENT)
        share_menu.addSeparator()
        self._add_menu_action(
            share_menu, tr("&Audio…"),
            lambda: self._on_entry(ENTRY_AUDIO),
            surface=self._SURFACE_CLOSED_EVENT)

        # ── Help ───────────────────────────────────────────────────────────
        help_menu = self.menuBar().addMenu(tr("&Help"))
        self._menus["help"] = help_menu
        self._add_menu_action(
            help_menu, tr("&Third-party tool guides…"),
            lambda: self._on_entry(ENTRY_HELPERS))
        help_menu.addSeparator()
        self._add_menu_action(
            help_menu, tr("&About Mira…"), self._open_about,
            tooltip=tr("Show app version and brand info."))

        # Apply the initial surface (no event open → events_list).
        self._refresh_menu_state()

    def _install_title_bar(self) -> None:
        """Wrap the native menu bar in the redesign TitleBar (Mira logo at
        left, the existing menu in the middle, ThemeToggle at right) — one
        strip, matching surface-01's titlebar — without disturbing the
        working QMenuBar (it keeps every action, shortcut, and surface-aware
        visibility rule). Installed via ``QMainWindow.setMenuWidget``."""
        from mira.ui.design.title_bar import TitleBar

        menu_bar = self.menuBar()
        self._title_bar = TitleBar(menu_bar)
        self.setMenuWidget(self._title_bar)
        self._title_bar.theme_toggle.themeChanged.connect(self._on_theme_toggled)

    def _on_theme_toggled(self, mode: str) -> None:
        """TitleBar ThemeToggle → apply the theme live + best-effort persist
        to settings so it sticks across restarts."""
        from PyQt6.QtWidgets import QApplication
        from mira.ui.theme import apply_theme

        app = QApplication.instance()
        if app is not None:
            apply_theme(app, mode)
        try:
            repo = self.gateway.settings
            settings = repo.load()
            if hasattr(settings, "theme"):
                settings.theme = mode
                repo.save(settings)
        except Exception:                                       # noqa: BLE001
            log.exception("Could not persist theme toggle")

    def _add_menu_action(
        self, menu, label: str, handler, *,
        surface: str = "both",
        modification: bool = False,
        shortcut: Optional[str] = None,
        tooltip: Optional[str] = None,
    ) -> "QAction":
        """Build a ``QAction`` and register its surface + modification flag.
        Used by :meth:`_build_menu_bar` for every entry except the special
        Quit + close-toggle actions that need ``QKeySequence`` / label-swap
        handling outside this helper."""
        from PyQt6.QtGui import QAction

        action = QAction(label, self)
        if shortcut:
            action.setShortcut(shortcut)
        action.setToolTip(tooltip or label)
        action.triggered.connect(lambda _checked=False, h=handler: h())
        menu.addAction(action)
        self._action_surfaces[action] = surface
        if modification:
            self._modification_actions.append(action)
        return action

    def _refresh_menu_state(self) -> None:
        """Set every menu action's visibility from the current surface +
        event-closed state, then hide top-level menus that have no
        visible children.

        Surface = ``per_event`` when ``self._current_event_id`` is set;
        otherwise ``events_list``. Closed-event filtering (F-024) hides
        modification actions when the open event is closed; Stats /
        Back up / Audit / Close-toggle stay visible.
        """
        in_event = self._current_event_id is not None
        is_closed = self._event_is_closed_now() if in_event else False

        # Step 1 — per-action surface visibility.
        for action, action_surface in self._action_surfaces.items():
            if action_surface == self._SURFACE_BOTH:
                action.setVisible(True)
            elif action_surface == self._SURFACE_EVENTS_LIST:
                action.setVisible(not in_event)
            elif action_surface == self._SURFACE_PER_EVENT:
                action.setVisible(in_event)
            elif action_surface == self._SURFACE_CLOSED_EVENT:
                # spec/66: Share is a closed-event STATE. Visible only
                # when the open event is closed; the empty-children rule
                # (Step 4) hides the whole top-level otherwise.
                action.setVisible(in_event and is_closed)

        # Step 2 — F-024 closed-event modification filter.
        if in_event and is_closed:
            for act in self._modification_actions:
                act.setVisible(False)

        # Step 3 — close-toggle label swap (Q4 = (a) label-swap pattern).
        if in_event:
            if is_closed:
                self._close_toggle_action.setText(tr("&Re-open Event"))
                self._close_toggle_action.setToolTip(tr(
                    "Re-open this event so the modification entries (Edit "
                    "info, Edit plan, etc.) come back. Browsing curated "
                    "slideshows works without reopening."))
            else:
                self._close_toggle_action.setText(tr("&Close Event"))
                self._close_toggle_action.setToolTip(tr(
                    "Close this event. The modification entries hide; "
                    "browsing curated slideshows still works. Click again "
                    "to reopen."))
            # The activity dashboard mirrors the same flag on its
            # modification-card CTAs.
            self.phases_page._apply_closed_card_state(is_closed)

        # Step 4 — hide top-level menus that have no visible non-separator
        # child. Qt auto-collapses adjacent separators inside menus, so
        # leftover separators don't keep a menu visible on their own.
        for menu in self._menus.values():
            any_visible = any(
                a.isVisible() and not a.isSeparator()
                for a in menu.actions()
            )
            menu.menuAction().setVisible(any_visible)

    def _open_edit_info(self) -> None:
        """Event menu "Edit info…" → spec/64 §3 Event Header dialog
        (identity). Same path as the tile title-zone click."""
        if self._current_event_id is None:
            return
        self._open_event_header_dialog(self._current_event_id)

    def _open_stats(self) -> None:
        """Event menu "Stats…" → the EventStatsDialog (TZ map + phase funnel)."""
        if self._current_event_id is None:
            return
        from mira.ui.pages.event_stats_dialog import EventStatsDialog

        dlg = EventStatsDialog(self.gateway, parent=self)
        dlg.populate(self._current_event_id)
        dlg.exec()
        dlg.deleteLater()

    def _on_close_toggled(self) -> None:
        """Event menu Close↔Re-open → flip ``event.is_closed`` via the gateway,
        refresh the activity dashboard + Event menu state + events list."""
        if self._current_event_id is None:
            return
        new_state = not self._event_is_closed_now()
        try:
            eg = self.gateway.open_event(self._current_event_id)
            try:
                eg.set_closed(new_state)
            finally:
                eg.close()
        except (KeyError, RuntimeError):
            log.exception("set_closed failed for %s", self._current_event_id)
            return
        self.phases_page.set_event(self._current_event_id)
        self._refresh_menu_state()
        self.events_page.refresh()

    def _coming_next(self, action: str) -> None:
        """Stub message for menu items whose handler lands in a later slice."""
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.information(
            self, tr("Coming next"),
            tr("“{action}” is being reassembled next.").replace(
                "{action}", action),
        )

    def _open_about(self) -> None:
        """Help → About Mira — show the brand lockup + version + tagline
        (spec/74 §3). The dialog is the one surface that surfaces
        ``MiraLogo(tagline=True)``; the title-bar logo is too small for the
        tagline to be legible."""
        from mira.ui.design.about_dialog import show_about
        show_about(self)

    def _go_to_library(self) -> None:
        """App → Library (Ctrl+L) — return to the events list from any
        per-event surface. Clears the cached event id + refreshes the menu
        state so the menu bar reverts to the events-list surface."""
        self.events_page.refresh()
        self.page_stack.show_page(ENTRY_DASHBOARD)
        self._current_event_id = None
        self._refresh_menu_state()

    def _open_settings(self) -> None:
        """Open the reused legacy SettingsDialog (charter §5.2 — data rewired to
        mira.settings). React to theme + photos_base_path changes via its
        changes_applied hook; the base change re-anchors the index + refreshes the list
        (charter §5.9 — relocate the whole library by editing one setting)."""
        from mira.ui.base.settings_dialog import SettingsDialog

        dlg = SettingsDialog(
            self,
            info_providers={"proxy_cache": self._proxy_cache_summary},
            info_actions={"clear_proxy_cache": self._clear_proxy_cache},
        )
        dlg.validate_changes = self._validate_settings_changes  # type: ignore[method-assign]
        dlg.changes_applied = self._on_settings_changed  # type: ignore[method-assign]
        dlg.exec()

    # ── Proxy-tier disk honesty (spec/63 slice 7) ────────────────────

    def _open_event_root(self) -> Optional[Path]:
        """The open event's root folder, or None (no event / unresolvable)."""
        if self._current_event_id is None:
            return None
        entry = self.gateway.index.get(self._current_event_id)
        if entry is None:
            return None
        return self.gateway.index.resolve_root(
            entry, self.gateway.photos_base_path())

    def _proxy_cache_summary(self) -> str:
        """Settings info row: the open event's screen-copy disk cost."""
        root = self._open_event_root()
        if root is None:
            return tr("Open an event to see its size")
        from core.photo_proxy_cache import proxy_cache_stats
        count, total = proxy_cache_stats(root)
        if count == 0:
            return tr("None yet for this event")
        mb = total / (1024 * 1024)
        size_text = (
            f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB")
        return tr("{n} copies · {size}").replace(
            "{n}", str(count)).replace("{size}", size_text)

    def _clear_proxy_cache(self) -> str:
        root = self._open_event_root()
        if root is None:
            return ""
        from core.photo_proxy_cache import clear_proxy_cache
        removed = clear_proxy_cache(root)
        log.info("proxy cache cleared for %s (%d files)", root, removed)
        return str(removed)

    def _validate_settings_changes(self, changed: dict) -> str | None:
        """Pre-commit veto (charter §5.9, Nelson 2026-06-01): refuse a
        ``photos_base_path`` change that would orphan events anchored relative to the
        current base. Verify-then-allow — the gateway checks whether each dependent
        event's ``event.db`` is actually present under the prospective new base; only
        the ones that would NOT be found block the change."""
        if "photos_base_path" not in changed:
            return None
        new_base = changed["photos_base_path"][1]
        blockers = self.gateway.base_change_blockers(new_base or "")
        if not blockers:
            return None
        names = "\n".join(
            f"  • {b['name'] or b['id']}  ({b['relpath']})" for b in blockers[:12]
        )
        more = ""
        if len(blockers) > 12:
            more = tr("\n  …and {n} more.").replace("{n}", str(len(blockers) - 12))
        return tr(
            "{n} event(s) are stored relative to the current photos folder, and their "
            "files were NOT found under the new location:\n\n{names}{more}\n\n"
            "Changing the photos folder would leave these events pointing at nothing. "
            "Move their folders to the new location first (or relocate those events), "
            "then change this setting."
        ).replace("{n}", str(len(blockers))).replace(
            "{names}", names).replace("{more}", more)

    def _on_settings_changed(self, changed: dict) -> None:
        if "photos_base_path" in changed:
            new_base = changed["photos_base_path"][1]
            self.gateway.set_photos_base_path(new_base or "")  # settings + index mirror
            self.events_page.refresh()
        if "theme" in changed:
            from PyQt6.QtWidgets import QApplication
            from mira.ui.theme import apply_theme
            app = QApplication.instance()
            if app is not None:
                apply_theme(app, changed["theme"][1])  # type: ignore[arg-type]
        if "font_scale" in changed:
            from PyQt6.QtWidgets import QApplication
            from mira.ui.app import apply_font_scale
            app = QApplication.instance()
            if app is not None:
                apply_font_scale(app, changed["font_scale"][1])

    def _home_defaults_from_settings(self):
        """Read ``home_country`` (ISO 3166-1 alpha-2) and ``home_timezone``
        (hours → minutes) from user settings for the autofill fallback
        (Nelson 2026-06-08). Returns ``(home_country_or_None,
        home_tz_minutes_or_None)`` — either / both may be ``None`` when
        the user hasn't set them."""
        try:
            settings = self.gateway.settings.load()
        except Exception:                                       # noqa: BLE001
            log.exception("Could not read settings for home defaults")
            return None, None
        cc = (getattr(settings, "home_country", "") or "").strip().upper()
        home_country = cc or None
        tz_hours = getattr(settings, "home_timezone", None)
        try:
            home_tz_minutes = (
                int(round(float(tz_hours) * 60))
                if tz_hours is not None else None
            )
        except (TypeError, ValueError):
            home_tz_minutes = None
        return home_country, home_tz_minutes

    @staticmethod
    def _format_phone_summary(summary) -> str:
        """Banner text above the Plan day-list (Nelson 2026-06-08) — explains
        which per-day fields will pre-fill from phone EXIF and which won't,
        so the user immediately understands why some rows came pre-filled
        and others are blank.

        Format optimised for "scan a number, scan a number" reading: aligned
        ratios for phone-photos / TZ / GPS across days. A trailing hint
        spells out the actionable consequence for days missing GPS."""
        from core.scan_source import PhoneScanSummary  # local — defer ui→core
        if not isinstance(summary, PhoneScanSummary):
            return ""
        total = summary.total_days
        if total <= 0:
            return ""
        ph = summary.days_with_phone_photos
        tz = summary.days_with_phone_tz
        gps = summary.days_with_phone_gps
        if ph == 0:
            # No phone photos at all — every day either falls back to
            # home defaults or stays blank.
            home_defaults_line = ""
            if summary.days_with_home_country_default or summary.days_with_home_tz_default:
                parts = []
                if summary.days_with_home_country_default:
                    parts.append(tr(
                        "country pre-filled with your home country on {n} day(s)"
                    ).replace("{n}", str(summary.days_with_home_country_default)))
                if summary.days_with_home_tz_default:
                    parts.append(tr(
                        "timezone pre-filled with your home timezone on {n} day(s)"
                    ).replace("{n}", str(summary.days_with_home_tz_default)))
                home_defaults_line = "\n\n" + tr(
                    "Home defaults applied — {parts}. "
                    "Verify per day in the next dialog."
                ).replace("{parts}", "; ".join(parts))
            return tr(
                "No phone photos in the scan."
            ) + home_defaults_line + (
                tr("\n\nLocation will need manual entry on every day.")
                if not home_defaults_line else
                tr("\n\nLocation still needs manual entry on every day.")
            )
        # Aligned ratios so the eye sweeps "X of N" thrice.
        lines = [
            tr("Phone photos: {a} of {b} day(s)")
            .replace("{a}", str(ph)).replace("{b}", str(total)),
            tr("Phone TZ:     {a} of {b} day(s)")
            .replace("{a}", str(tz)).replace("{b}", str(total)),
            tr("Phone GPS:    {a} of {b} day(s)")
            .replace("{a}", str(gps)).replace("{b}", str(total)),
        ]
        body = "\n".join(lines)
        missing_gps = total - gps
        missing_tz = total - tz
        notes = []
        home_country_days = summary.days_with_home_country_default
        home_tz_days = summary.days_with_home_tz_default
        if missing_gps > 0:
            if home_country_days > 0:
                notes.append(
                    tr(
                        "{n} day(s) without GPS — country pre-filled "
                        "with your home country (verify per day); "
                        "location still needs manual entry."
                    ).replace("{n}", str(missing_gps))
                )
            else:
                notes.append(
                    tr("{n} day(s) without GPS will need manual country + location.")
                    .replace("{n}", str(missing_gps))
                )
        if missing_tz > 0:
            if home_tz_days > 0:
                notes.append(
                    tr(
                        "{n} day(s) without phone TZ — pre-filled with "
                        "your home timezone (verify per day)."
                    ).replace("{n}", str(missing_tz))
                )
            else:
                notes.append(
                    tr("{n} day(s) without phone TZ will need manual timezone.")
                    .replace("{n}", str(missing_tz))
                )
        if notes:
            body += "\n\n" + " ".join(notes)
        return body

    def _open_new_event_info_only(self) -> None:
        """spec/64 §3.7 — Create Event (no photos yet) path. The Event
        Header dialog is the first moment of the event's birth; OK
        creates the event with zero ``trip_days`` (the Days Table
        fills later as Collect runs). Cancel = clean no-op."""
        from mira.ui.pages.event_header_dialog import (
            EventHeaderDialog,
        )

        dlg = self._exec_event_header_dialog(EventHeaderDialog(parent=self))
        if not dlg:
            return
        info = dlg.header_info()
        try:
            event_id = self._create_event_from_plan(info=info, edited_rows=[])
        except Exception as exc:                              # noqa: BLE001
            log.exception("New (info-only) event materialisation failed")
            err = QMessageBox(self)
            err.setWindowTitle(tr("Couldn't create the event"))
            err.setIcon(QMessageBox.Icon.NoIcon)
            err.setStandardButtons(QMessageBox.StandardButton.Ok)
            err.setText(tr(
                "The event row could not be written.\n\n{type}: {err}"
            ).replace("{type}", type(exc).__name__).replace("{err}", str(exc)))
            err.exec()
            return
        self._on_event_created(event_id)

    def _open_new_event_flow(self) -> None:
        """spec/57 §4.3 — "New event from existing media…", the backfill wizard.

        ONE flow, three landing levels: *collected* media lands the user at
        Pick; already-*picked* keepers land at Edit (slice 5b); already-
        *edited* finals land ready-to-Share (slice 5c). The common spine:
        landing-level choice → source pick → off-thread scan → multi-date
        split confirm → phone-EXIF coverage → ONE unified info+plan dialog
        → event creation → auto-Collect (the same TZ-calibration ask +
        ingest gate + copy engine Collect uses, Quick Sweep offer included)
        → the level's state writes → land at the level's surface.

        Cancel before event creation = clean no-op (spec/52 §2.4 — no
        orphaned event records). Cancel at the calibration ask or the
        ingest gate keeps the created event (plan baked, no media yet) and
        lands on its dashboard — Collect runs later from there (spec/57
        §4.3.1 cancel posture)."""
        from pathlib import Path
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        from core.scan_source import scan_source
        from mira.ui.base.progress import run_with_progress
        from mira.ui.pages.landing_level_dialog import (
            LEVEL_COLLECTED,
            LEVEL_EDITED,
            LEVEL_PICKED,
            LandingLevelDialog,
        )

        # ── 0. The landing-level question (spec/57 §4.3). ────────────────
        level_dlg = LandingLevelDialog(self)
        if level_dlg.exec() != QDialog.DialogCode.Accepted:
            return                                          # Cancel — clean no-op
        level = level_dlg.level()
        # The level's landing surface (spec/57 §4.3.1). The edited level
        # lands on Share now that the Cuts shell exists (spec/61) — the
        # backfilled finals are exactly the #exported universe.
        land_phase_by_level = {
            LEVEL_COLLECTED: "pick",
            LEVEL_PICKED: "edit",
            LEVEL_EDITED: "share",
        }
        land_phase = land_phase_by_level.get(level, "pick")

        base = self.gateway.photos_base_path()
        start_dir = str(base) if base else ""
        chosen = QFileDialog.getExistingDirectory(
            self, tr("Pick the photos source folder"), start_dir,
        )
        if not chosen:
            return                                          # Cancel — clean no-op

        home_country, home_tz_minutes = self._home_defaults_from_settings()

        # spec/64 §4.4 — the silent home-default autofill retires.
        # Scan with home=None so the GPS-less days come back with
        # blank country / TZ; the per-location-group prompt below
        # asks the user (with the home values as suggestions).
        def _do_scan(_progress):
            return scan_source(
                Path(chosen),
                home_country=None,
                home_tz_minutes=None,
            )

        ok, result = run_with_progress(
            self, tr("Scanning photos…"), _do_scan,
            label=tr("Reading EXIF from {path}").replace("{path}", chosen),
        )
        if not ok:
            QMessageBox.critical(
                self, tr("Scan failed"),
                tr("Could not scan the folder. Error:\n\n{err}")
                .replace("{err}", str(result)),
            )
            return

        scan = result
        n_days = len(scan.scan_rows)
        n_photos = scan.total_photos
        n_untimestamped = scan.untimestamped_count
        n_cameras = len({p.camera_id for p in scan.presences})

        if n_days == 0:
            QMessageBox.warning(
                self, tr("No photos found"),
                tr(
                    "The scan didn't find any photos with a readable capture "
                    "date in {path}. {scanned} file(s) examined; {undated} "
                    "had no EXIF DateTimeOriginal."
                )
                .replace("{path}", chosen)
                .replace("{scanned}", str(n_photos))
                .replace("{undated}", str(n_untimestamped)),
            )
            return

        # spec/64 §3.7 — the two-dialog split: Header first, Days Table
        # fills automatically from the scan. The Header dialog is the
        # first interactive moment of the event's birth; Cancel rolls
        # the whole flow back.

        # Explain the autofill state BEFORE opening the Header so the
        # user knows what came pre-filled vs. needs manual entry later.
        # Plain text box (no info icon — Nelson 2026-06-08 eyeball: the
        # circular ⓘ on a square reads ugly).
        summary_text = self._format_phone_summary(scan.phone_summary)
        if summary_text:
            msg = QMessageBox(self)
            msg.setWindowTitle(tr("Phone-EXIF coverage"))
            msg.setText(summary_text)
            msg.setIcon(QMessageBox.Icon.NoIcon)
            msg.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg.exec()

        # ── Multi-date split confirmation (spec/57 §4.1 — the same moment
        # Collect has). Every scanned date is new here, so any run spanning
        # >1 date shows the proposed split + the "day starts at" boundary
        # BEFORE the per-day metadata dialog.
        if len(scan.scan_rows) > 1:
            from core.scan_source import build_scan_result
            from mira.ui.pages.day_split_dialog import DaySplitDialog

            split = DaySplitDialog(
                [p.timestamp for p in scan.photos if p.timestamp is not None],
                initial_minutes=scan.day_start_minutes,
                parent=self,
            )
            if split.exec() != QDialog.DialogCode.Accepted:
                return                                      # Cancel — clean no-op
            boundary = split.day_start_minutes()
            if boundary != scan.day_start_minutes:
                # Pure regroup — same photos, new day boundary; every
                # derived structure (rows, presences, per-photo records)
                # stays consistent because the builder is the one seam.
                scan = build_scan_result(
                    scan.photos,
                    source_root=scan.source_root or Path(chosen),
                    home_country=home_country,
                    home_tz_minutes=home_tz_minutes,
                    day_start_minutes=boundary,
                )

        # spec/64 §3.7 — the Header dialog is the first interactive
        # moment of event birth. Cancel rolls the whole flow back: no
        # event is created and the scan results are discarded.
        from core.feature_flags import load_flags
        from core.peek_select import select_for_peek
        from mira.ui.pages.day_browse_dialog import DayBrowseDialog
        from mira.ui.pages.event_header_dialog import (
            EventHeaderDialog,
        )
        header_dlg = self._exec_event_header_dialog(EventHeaderDialog(
            parent=self))
        if not header_dlg:
            return                                          # roll back
        info = header_dlg.header_info()

        # spec/78 §A — ask once for the no-GPS days' Country / TZ and
        # apply it to all of them. Replaces the per-stretch loop
        # (spec/64 §4.4) which became one-prompt-per-gap on grab-bag
        # past-photos imports. Days the user Skips stay blank; the Days
        # Table dialog below lets them fine-tune any individual day.
        self._prompt_for_no_gps_days(
            scan.scan_rows,
            home_country=home_country,
            home_tz_minutes=home_tz_minutes,
        )

        # ── The Days Table preview: per-day editor between Header and
        # event creation. Includes the same browse_handler (peek per
        # day), Include checkbox (uncheck to leave a day out), Country /
        # TZ propagate-down, etc. Cancel = full roll back (no event).
        try:
            flags = load_flags(self.gateway.user_store)
            can_save_load_csv = flags.plan_save_load_csv
        except Exception:                                   # noqa: BLE001
            log.exception(
                "Could not read feature flags; defaulting CSV gate off")
            can_save_load_csv = False

        peek_target = _peek_target_from_settings()

        def _browse_day(day):
            candidates = scan.candidates_by_date.get(day, [])
            selected = select_for_peek(candidates, target=peek_target)
            paths = [c.path for c in selected]
            if not paths:
                noinfo = QMessageBox(self)
                noinfo.setWindowTitle(tr("Nothing to preview"))
                noinfo.setText(tr(
                    "No preview-able photos for {date} (videos and very "
                    "large files are skipped to keep the peek fast)."
                ).replace("{date}", day.isoformat()))
                noinfo.setIcon(QMessageBox.Icon.NoIcon)
                noinfo.setStandardButtons(QMessageBox.StandardButton.Ok)
                noinfo.exec()
                return
            dlg_browse = DayBrowseDialog(
                paths,
                title=tr("Browse — {date}").replace("{date}", day.isoformat()),
                parent=self,
            )
            dlg_browse.exec()

        days_dlg = self._exec_event_days_table_dialog(
            self._build_days_table_dialog(
                scan.scan_rows,
                can_save_load_csv=can_save_load_csv,
                browse_handler=_browse_day,
            ))
        if not days_dlg:
            return                                          # roll back
        edited = days_dlg.rows()
        included = [r for r in edited if r.checked]
        if not included:
            QMessageBox.warning(
                self, tr("Nothing to import"),
                tr("You unchecked every day. Pick at least one day to import."),
            )
            return

        # Materialise the event row + plan. Per spec/52 §2.4 this is the
        # commit point: a successful create_event is when the index entry
        # first exists. The auto-Collect below copies the media in.
        try:
            event_id = self._create_event_from_plan(
                info=info, edited_rows=edited,
            )
        except Exception as exc:                              # noqa: BLE001
            log.exception("New event materialisation failed")
            err = QMessageBox(self)
            err.setWindowTitle(tr("Couldn't create the event"))
            err.setIcon(QMessageBox.Icon.NoIcon)
            err.setStandardButtons(QMessageBox.StandardButton.Ok)
            err.setText(tr(
                "The event row could not be written. The plan is preserved "
                "in memory — try again, or fix the underlying error.\n\n"
                "{type}: {err}"
            ).replace("{type}", type(exc).__name__).replace("{err}", str(exc)))
            err.exec()
            return

        # ── Auto-Collect (spec/57 §4.3 — "what runs automatically"). The
        # created event gets its first ingest in the same flow: the same
        # TZ-calibration ask + ingest-mode gate + copy engine Collect
        # uses, then the landing at the level's surface. Fresh trip_days
        # are re-read from event.db so day numbering, calibration and the
        # copy walk all share the persisted plan.
        try:
            eg = self.gateway.open_event(event_id)
            try:
                fresh_days = list(eg.trip_days())
            finally:
                eg.close()
        except Exception:                                   # noqa: BLE001
            log.exception(
                "Backfill: could not re-read fresh event %s; landing on "
                "the dashboard (run Collect manually)", event_id)
            self._on_event_created(event_id)
            return

        entry = self.gateway.index.get(event_id)
        event_root = (
            self.gateway.index.resolve_root(entry, self.gateway.photos_base_path())
            if entry else None
        )
        if event_root is None:
            log.warning("Backfill: cannot resolve event_root for %s", event_id)
            self._on_event_created(event_id)
            return

        calibration_decisions = self._collect_run_tz_calibration(
            event_id=event_id, event_name=info["name"] or event_id,
            source_root=Path(chosen), scan=scan,
            edited_rows=edited, existing_days=fresh_days,
        )
        if calibration_decisions is None:
            # User aborted the calibration ask — the event exists with its
            # plan baked; land on the dashboard, Collect runs later.
            self._on_event_created(event_id)
            return

        # Level state writes (spec/57 §4.3 — rows "as if the phases had
        # run in order"), applied right after the items are recorded and
        # BEFORE any surface opens — so the Edit entry seams (Picked
        # Media projection + return scan) already see the picks when the
        # landing fires.
        post_record = None
        if level == LEVEL_PICKED:
            def post_record() -> None:
                eg2 = self.gateway.open_event(event_id)
                try:
                    ids = [it.id for it in eg2.items(provenance="captured")]
                    n = eg2.set_items_phase_state(ids, "pick", "picked")
                    log.info(
                        "Backfill (picked level): wrote picked for %d "
                        "ingested item(s)", n)
                finally:
                    eg2.close()
        elif level == LEVEL_EDITED:
            def post_record() -> None:
                from datetime import datetime, timezone
                from mira.ingest.backfill import apply_edited_level

                stamp = datetime.now(timezone.utc).isoformat(
                    timespec="seconds")
                eg2 = self.gateway.open_event(event_id)
                try:
                    rep = apply_edited_level(eg2, event_root, now=stamp)
                finally:
                    eg2.close()
                if rep.errors:
                    log.warning(
                        "Backfill (edited level): %d issue(s), e.g. %s",
                        len(rep.errors), "; ".join(rep.errors[:5]))

        ran = self._open_collect_ingest_gate(
            event_id=event_id, event_name=info["name"] or event_id,
            event_root=event_root, scan=scan,
            edited_rows=edited, edited_info=info,
            existing_info=info, existing_days=fresh_days,
            calibration_decisions=calibration_decisions,
            # Quick Sweep only makes sense on an unfiltered shoot — the
            # picked/edited levels arrive pre-filtered (spec/57 §4.3.1).
            offer_quick_sweep=(level == LEVEL_COLLECTED),
            post_record=post_record,
            land_phase=land_phase,
        )
        if not ran:
            # Gate cancelled / Quick Sweep backed out / ingest failed —
            # the event still exists with its plan. Land on its dashboard
            # so the user can run Collect from there (spec/57 §4.3.1).
            self._on_event_created(event_id)

    def _create_event_from_plan(
        self,
        *,
        info: dict,
        edited_rows,
    ) -> str:
        """Build an ``EventDocument`` from the dialog output + materialise it.

        Returns the new event's uuid on success. Days the user UNCHECKED are
        still saved (as ``TripDay(hidden=True)``) so the Collect pass can
        un-hide them later without re-deriving the plan. No cameras / items
        are inserted at this point — Collect populates them when the user
        actually points at a source and ingests."""
        import json as _json
        import uuid as _uuid
        from datetime import datetime, timezone
        from pathlib import Path
        from core.path_builder import sanitize_folder_name
        from mira.store import models as _m

        event_id = _uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        sorted_rows = sorted(edited_rows, key=lambda r: r.date)

        # spec/77 §5 — dialog dates win over the photo-inferred span.
        # The dialog now requires From / To; we trust them when set
        # (info-only creates always carry them, and edit-existing
        # flows post the dialog's updated range). Falls back to the
        # photo-inferred span only for the legacy plan-from-photos
        # path where the user never opened the new dialog.
        start_date = (
            info.get("start_date")
            or (sorted_rows[0].date.isoformat() if sorted_rows else None)
        )
        end_date = (
            info.get("end_date")
            or (sorted_rows[-1].date.isoformat() if sorted_rows else None)
        )

        event = _m.Event(
            uuid=event_id,
            name=info["name"],
            created_at=now,
            updated_at=now,
            start_date=start_date,
            end_date=end_date,
            event_type=info["event_type"] or "unclassified",
            event_subtype=(info["event_subtype"] or None),
            description=info["description"] or "",
            duration_value=info.get("duration_value"),
            duration_unit=info.get("duration_unit"),
            participants=_json.dumps(info.get("participants") or []),
            # spec/64: Context / Experience Type / Creative Focus carry
            # over from EventHeaderDialog when the user filled them at
            # create time; NULL / '[]' otherwise (the tile's Header
            # badge will nudge the user to fill later).
            context=info.get("context") or None,
            experience_type=info.get("experience_type") or None,
            creative_focus=_json.dumps(info.get("creative_focus") or []),
        )

        trip_days = []
        for i, row in enumerate(sorted_rows, start=1):
            extras = {}
            if row.country_code:
                extras["country_code"] = row.country_code
            trip_days.append(_m.TripDay(
                day_number=i,
                date=row.date.isoformat(),
                description=row.description or "",
                location=row.location or None,
                tz_minutes=row.tz_minutes,
                hidden=not row.checked,
                extras_json=_json.dumps(extras) if extras else "{}",
            ))

        doc = _m.EventDocument(event=event, trip_days=trip_days)

        base = Path(self.gateway.photos_base_path())
        safe = sanitize_folder_name(event.name).strip() or "Untitled event"
        event_root = base / safe

        # Refuse a name whose folder already exists — materialise_event
        # would DELETE the existing event.db at that root and leave the
        # old index card pointing at a hijacked folder (the events-index
        # clobber family). Cheap guard, clear message; the caller's
        # except block surfaces it.
        if event_root.exists():
            raise FileExistsError(tr(
                'A folder named "{name}" already exists under the photos '
                "base. Pick a different event name, or delete that event "
                "(or folder) first."
            ).replace("{name}", safe))

        eg = self.gateway.create_event(doc, event_root)
        eg.close()
        log.info("new-event flow created %s at %s", event_id, event_root)
        return event_id

    def _on_event_created(self, event_id: str) -> None:
        """A brand-new event (New Event or Create-from-Photos) → open it directly on its
        per-event dashboard, where the plan + phases live. A new event is always open, so
        it follows the same landing rule as activating a card (legacy
        MainWindow._on_event_created, Nelson 2026-05-29) — rather than dropping the user
        back on the events list to hunt for the empty card."""
        self.events_page.refresh()
        if not self._open_event(event_id):
            # Couldn't open (unexpected) — fall back to the refreshed list.
            self.page_stack.show_page(ENTRY_DASHBOARD)

    def _on_new_event_created(self, event_id: str) -> None:
        """New Event create → open the event AND drop straight into its editable plan
        table. New Event exists to author a plan, so after creation the user lands on the
        per-event dashboard and the plan editor opens on top of it; on Apply/Cancel they
        return to the dashboard with the plan reflected (Nelson 2026-05-31)."""
        self._on_event_created(event_id)
        self._open_plan_editor_for_event(event_id)

    def _on_new_event_cancelled(self) -> None:
        """Cancel on the New Event page → return to the (refreshed) events list."""
        self.events_page.refresh()
        self.page_stack.show_page(ENTRY_DASHBOARD)

    def _open_plan_editor_for_event(self, event_id: str) -> None:
        """Open the reused :class:`PlanEditorDialog` on the event's trip days; on Apply,
        persist the edited set through the gateway and refresh the surfaces that show the
        day count / date range (charter §5.2 — reused legacy dialog, data rewired to the
        gateway; mirrors legacy ``MainWindow._open_plan_editor_for_event``).

        The dialog works in the legacy ``core.models.TripDay`` shape, so we convert
        store ``TripDay`` → legacy on the way in and back on the way out (``tz_minutes`` ⇄
        ``tz_offset`` hours). ``event=None`` keeps the dialog's filesystem remove-day gate
        off; the gateway's ``save_trip_days`` is the real safety net — a removal that would
        orphan items is rejected at commit and surfaced here."""
        from datetime import date as _date
        import json as _json

        from core.models import TripDay as LegacyTripDay
        from mira.store import models as m
        from mira.ui.base.plan_editor_dialog import PlanEditorDialog

        eg = self.gateway.open_event(event_id)
        try:
            store_days = eg.trip_days()
            # Per-date photo counts (one pass) so each plan row shows "Browse…" only for days
            # that have photos, and a disabled "Empty" otherwise.
            date_by_num = {d.day_number: d.date for d in store_days if d.date}
            day_photo_counts: dict[str, int] = {}
            for it in eg.items():
                dt = date_by_num.get(it.day_number)
                if dt:
                    day_photo_counts[dt] = day_photo_counts.get(dt, 0) + 1
        finally:
            eg.close()

        def _country_from_extras(extras_json: str) -> Optional[str]:
            try:
                blob = _json.loads(extras_json or '{}')
            except _json.JSONDecodeError:
                return None
            cc = blob.get('country_code')
            return str(cc).upper() if cc else None

        legacy_days = [
            LegacyTripDay(
                day_number=d.day_number,
                date=_date.fromisoformat(d.date) if d.date else None,
                description=d.description or "",
                tz_offset=(d.tz_minutes / 60.0) if d.tz_minutes is not None else None,
                location=d.location,
                country_code=_country_from_extras(d.extras_json),
            )
            for d in store_days
        ]

        dlg = PlanEditorDialog(
            parent=self, trip_days=legacy_days, event=None,
            day_photos_provider=self._make_day_photos_provider(event_id),
            day_photo_counts=day_photo_counts,
        )
        dlg.exec()
        if not dlg.was_applied():
            return

        def _extras_with_country(country_code: Optional[str]) -> str:
            if not country_code:
                return '{}'
            return _json.dumps({'country_code': str(country_code).upper()})

        new_days = [
            m.TripDay(
                day_number=d.day_number,
                date=d.date.isoformat() if d.date else None,
                description=d.description or "",
                location=d.location,
                tz_minutes=(round(d.tz_offset * 60) if d.tz_offset is not None else None),
                extras_json=_extras_with_country(getattr(d, 'country_code', None)),
            )
            for d in dlg.get_trip_days()
        ]

        eg = self.gateway.open_event(event_id)
        try:
            eg.save_trip_days(new_days)
        except Exception as exc:  # noqa: BLE001 — sqlite IntegrityError + friends
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                tr("Couldn't save the plan"),
                tr(
                    "The plan couldn't be saved — a day you removed may still have photos "
                    "under it. Move or discard those photos first, then edit the plan "
                    "again.\n\n{err}"
                ).replace("{err}", str(exc)),
            )
            return
        finally:
            eg.close()

        # Refresh the surfaces that show day-count / date-range data.
        if self._current_event_id == event_id:
            self.phases_page.set_event(event_id)
        self.events_page.refresh()

    def _open_manage_days_for_event(self) -> None:
        """Plan page "Manage days…" → the per-day operations dialog (spec/14 §5D): hide/unhide,
        delete, move-to-another-event. Reuses the same per-day Browse provider as the plan
        editor; on any change, refreshes the surfaces that show day-count / progress."""
        event_id = self._current_event_id
        if event_id is None:
            return
        from mira.ui.pages.manage_days_dialog import ManageDaysDialog

        dlg = ManageDaysDialog(
            parent=self, gateway=self.gateway, event_id=event_id,
            day_photos_provider=self._make_day_photos_provider(event_id),
        )
        dlg.changed.connect(lambda eid=event_id: self._after_day_change(eid))
        dlg.exec()

    def _after_day_change(self, event_id: str) -> None:
        """Refresh the day-count / progress surfaces after a Manage-days operation."""
        if self._current_event_id == event_id:
            self.phases_page.set_event(event_id)
        self.events_page.refresh()

    def _make_day_photos_provider(self, event_id: str):
        """Return ``provider(row_date) -> list[SourceItem]`` for the plan editor's per-row
        Browse button: the event's items whose day matches ``row_date``, resolved to
        absolute paths under the event root (the gateway is the only data source)."""
        from datetime import date as _date, datetime as _dt

        from core.fresh_source import SourceItem

        def provider(row_date: _date):
            eg = self.gateway.open_event(event_id)
            try:
                root = eg.event_root
                target = row_date.isoformat()
                day_numbers = [
                    d.day_number for d in eg.trip_days()
                    if d.date == target and d.day_number is not None
                ]
                out: list = []
                for n in day_numbers:
                    for it in eg.items(day=n):
                        ts = None
                        if it.capture_time_corrected:
                            try:
                                ts = _dt.fromisoformat(it.capture_time_corrected)
                            except ValueError:
                                ts = None
                        out.append(SourceItem(root / it.origin_relpath, ts, it.camera_id))
                return out
            finally:
                eg.close()

        return provider

    def _open_event(self, event_id: str) -> bool:
        """Click an event card body → land where spec/64 §2.2 says.

        Open event: the activity dashboard (the "work the event" path, spec/46
        Slice 2+3). Closed event: the Cuts list (spec/64 §2.4 — the body
        becomes a Cuts door once the event is finished).

        Either way, sets the cached event id + refreshes the menu state so the
        per-event surface entries show. Returns True iff the page accepted the
        load.
        """
        # spec/79 §7.3 — quick_check the event.db before letting the
        # user touch it. On failure, offer to restore from the latest
        # backup; if the user declines, the door stays closed.
        if not self._check_event_integrity(event_id):
            return False
        if self._event_is_closed(event_id):
            return self._open_event_cuts_list(event_id)
        if not self._gate_missing_originals(event_id):
            return False
        if not self.phases_page.set_event(event_id):
            return False
        self._current_event_id = event_id
        self.page_stack.show_page(self._ACTIVITY_PAGE_KEY)
        self._refresh_menu_state()
        # spec/58 §1 — the quiet background classification pass. Opening
        # an event is the catch-all trigger: it covers backfilled events
        # (which never visit Pick), events ingested before spec/58, and
        # wizard re-runs (rules-version stamp change). No-ops fast when
        # everything is current.
        self._spawn_classify_pass(event_id)
        return True

    def _event_is_closed(self, event_id: str) -> bool:
        """Cheap is_closed lookup off the events index cache (no event.db open).
        The cache is the source of truth the dashboard already filters on."""
        entry = self.gateway.index.get(event_id)
        return bool(entry and entry.get("is_closed"))

    def _check_event_integrity(self, event_id: str) -> bool:
        """spec/79 §7.3 — ``PRAGMA quick_check`` the event.db before the
        user touches it. On a clean check, return True. On corruption,
        offer to restore from the latest snapshot (spec/79 §5); the user's
        choice gates the open.

        Returns True iff the door should proceed to open the event.
        """
        from core import db_backup
        entry = self.gateway.index.get(event_id)
        if entry is None:
            return True                                       # let the
                                                              # normal path
                                                              # raise
        root = self.gateway.index.resolve_root(
            entry, self.gateway.photos_base_path())
        if root is None:
            return True
        db_path = root / "event.db"
        if not db_path.exists():
            return True
        if db_backup.quick_check(db_path):
            return True
        backups_dir = self.gateway.event_backups_dir(event_id)
        snap = (
            db_backup.latest_snapshot(backups_dir)
            if backups_dir is not None else None
        )
        return self._offer_event_restore(event_id, db_path, snap)

    def _offer_event_restore(self, event_id, db_path, snap) -> bool:
        """Show the spec/79 §4 damage dialog. Returns True iff the
        user accepted Restore AND the restore succeeded — the only
        case where the open should proceed."""
        from mira.ui.design.dialogs import MessageDialog
        log.warning(
            "spec/79: event %s has a damaged db at %s", event_id, db_path)
        if snap is None:
            MessageDialog.error(
                tr("This event is damaged"),
                tr(
                    "Mira detected damage in this event's database, "
                    "but there are no backup snapshots to restore "
                    "from. The event can't be opened safely. Remove "
                    "it from Mira, or restore the file manually from "
                    "your NAS / system backups."
                ),
                parent=self,
            ).exec()
            return False
        dlg = MessageDialog(
            intent="warning",
            title=tr("This event is damaged"),
            message=tr(
                "Mira detected damage in this event's database. "
                "Restore from the most recent backup (taken {when})? "
                "The damaged file will be saved alongside the "
                "backups so it isn't lost."
            ).replace("{when}", snap.created_at),
            primary_text=tr("Restore from backup"),
            ghost_text=tr("Cancel"),
            parent=self,
        )
        dlg.exec()
        if dlg.result_kind() != "primary":
            return False
        from core import db_backup
        try:
            saved = db_backup.restore(snap, db_path)
            log.info(
                "spec/79 restore: %s restored from %s; corrupt "
                "original saved at %s", event_id, snap.db_path, saved)
        except ValueError as exc:
            log.warning("spec/79 restore failed: %s", exc)
            MessageDialog.error(
                tr("Restore failed"),
                tr(
                    "The backup snapshot itself failed integrity "
                    "checks. Mira can't safely restore from it."
                ),
                parent=self,
            ).exec()
            return False
        except OSError as exc:
            log.warning("spec/79 restore I/O failure: %s", exc)
            MessageDialog.error(
                tr("Restore failed"),
                tr(
                    "Mira couldn't swap the snapshot in. Check disk "
                    "space and permissions, then try again."
                ),
                parent=self,
            ).exec()
            return False
        return True

    def _backup_event_on_quit(self) -> None:
        """spec/82 §G — automatic backup-on-quit (Part-B bundle).

        Runs only when:
        * ``backup_on_quit_enabled`` is True (the user opted in via
          the Backups settings tab);
        * ``event_backup_destination`` is set (no hardcoded path,
          invariant #2);
        * an event is currently open (``_current_event_id`` set).

        Uses the same :func:`core.event_bundle.export_event`
        primitive the manual Back up event… action calls. Failures
        are logged + swallowed: a stuck quit would be worse than a
        missed backup. The user has the manual action as a fallback.
        """
        from mira.settings.repo import SettingsRepo
        from core import event_bundle
        try:
            settings = SettingsRepo().load()
        except Exception as exc:                            # noqa: BLE001
            log.warning(
                "backup-on-quit: settings load FAILED: %s", exc)
            return
        if not bool(getattr(settings, "backup_on_quit_enabled", False)):
            return
        dest = str(getattr(
            settings, "event_backup_destination", "") or "")
        if not dest:
            log.info(
                "backup-on-quit: enabled but event_backup_destination "
                "is blank; skipping")
            return
        event_id = self._current_event_id
        if not event_id:
            return
        entry = self.gateway.index.get(event_id)
        if entry is None:
            return
        root = self.gateway.index.resolve_root(
            entry, self.gateway.photos_base_path())
        if root is None:
            return
        try:
            from importlib import metadata
            app_version = metadata.version("mira")
        except Exception:                                   # noqa: BLE001
            app_version = "dev"
        try:
            result = event_bundle.export_event(
                root, root / "event.db", Path(dest),
                app_version=app_version,
                verify_after_copy=bool(getattr(
                    settings, "event_backup_verify", True)),
            )
            log.info(
                "spec/82 §G backup-on-quit: %s exported to %s",
                event_id, result.bundle_dir)
        except Exception as exc:                            # noqa: BLE001
            log.warning(
                "spec/82 §G backup-on-quit FAILED for %s: %s",
                event_id, exc)

    def _snapshot_user_store_on_quit(self) -> None:
        """spec/82 §A.3 close-if-dirty hook for the user-data store.

        Connected to :attr:`QApplication.aboutToQuit` — runs while
        the gateway is still alive so the dirty check has a live
        ``UserStore`` connection. Failures are logged + swallowed;
        a snapshot miss is far less bad than aborting shutdown.
        """
        try:
            snap = self.gateway.snapshot_user_store(only_if_dirty=True)
            if snap is not None:
                log.info(
                    "spec/82 §A.3 user-store snapshot on quit: %s",
                    snap)
        except Exception as exc:                           # noqa: BLE001
            log.warning(
                "spec/82 §A.3 user-store snapshot on quit FAILED: %s",
                exc)

    def _open_back_up_event(self) -> None:
        """spec/82 §B.2 — manual Back up event… for the current event.

        Asks the user for a destination folder, then runs
        :func:`core.event_bundle.export_event` with the standard
        progress dialog. The bundle lands as
        ``<dest>/<event-folder>/`` with a top-level
        ``mira-event.json``. Slice 8 will pre-fill the dialog with
        ``event_backup_destination`` from settings; for now the
        dialog opens at the user's home dir.

        Per-event surface only, so ``_current_event_id`` is set.
        """
        from PyQt6.QtWidgets import QFileDialog
        from core import event_bundle
        from mira.ui.base.progress import run_with_progress
        from mira.ui.design.dialogs import MessageDialog
        event_id = self._current_event_id
        if not event_id:
            return
        entry = self.gateway.index.get(event_id)
        if entry is None:
            return
        root = self.gateway.index.resolve_root(
            entry, self.gateway.photos_base_path())
        if root is None:
            MessageDialog.error(
                tr("Can't back up"),
                tr(
                    "Mira couldn't resolve this event's folder. "
                    "Check the library's photos path."
                ),
                parent=self,
            ).exec()
            return
        # spec/82 §G — pre-fill from event_backup_destination so the
        # user lands in their usual backup folder. Still a default,
        # not a frozen path — they can confirm a different one each
        # time (invariant #2).
        from mira.settings.repo import SettingsRepo
        default_dest = ""
        try:
            default_dest = str(
                getattr(SettingsRepo().load(),
                        "event_backup_destination", "") or "")
        except Exception:                                   # noqa: BLE001
            pass
        dest_root = QFileDialog.getExistingDirectory(
            self,
            tr("Pick a backup destination"),
            default_dest,
        )
        if not dest_root:
            return
        dest_path = Path(dest_root)
        event_name = str(entry.get("name") or event_id)

        # The live build version stamps the bundle's manifest so a
        # future Restore on a different installation can compare.
        try:
            from importlib import metadata
            app_version = metadata.version("mira")
        except Exception:                                   # noqa: BLE001
            app_version = "dev"
        verify_after = bool(getattr(
            SettingsRepo().load(), "event_backup_verify", True))

        def _do_export(report):
            def _adapter(message, current=0, total=0):
                report(current, total, message)
            return event_bundle.export_event(
                root, root / "event.db", dest_path,
                app_version=app_version,
                progress=_adapter,
                verify_after_copy=verify_after,
            )

        ok, result = run_with_progress(
            self, tr("Backing up event…"), _do_export,
            label=tr("Copying {name}…").replace("{name}", event_name),
        )
        if not ok:
            MessageDialog.error(
                tr("Backup failed"),
                tr(
                    "Mira couldn't finish the backup. The "
                    "destination folder still contains a "
                    "``.partial`` directory you can delete.\n\n"
                    "{err}"
                ).replace("{err}", str(result)),
                parent=self,
            ).exec()
            return
        MessageDialog.success(
            tr("Backup complete"),
            tr(
                "Backed up {name} to {dest}.\n\nKeep this folder "
                "as a backup; continue working on the original PC."
            )
            .replace("{name}", event_name)
            .replace("{dest}", str(result.bundle_dir)),
            parent=self,
        ).exec()

    def _open_restore_event(self) -> None:
        """spec/82 §B.3 — manual Restore event… (import a bundle).

        Asks the user for a bundle folder, runs the integrity +
        version gates, asks about Replace if the event_uuid is
        already in the library, then installs the bundle into the
        local library_base and registers it in the events index.
        The writer lock (spec/76 §A) gates this implicitly because
        the read-only mode set on the session would already have
        disabled the menu action via ``modification=True``.

        No automatic discovery on mount (Nelson 2026-06-17): the
        user explicitly invokes this and picks the folder.
        """
        from PyQt6.QtWidgets import QFileDialog
        from core import event_bundle
        from mira.ui.base.progress import run_with_progress
        from mira.ui.design.dialogs import MessageDialog
        bundle_root = QFileDialog.getExistingDirectory(
            self,
            tr("Pick a bundle folder to restore"),
            "",
        )
        if not bundle_root:
            return
        bundle_dir = Path(bundle_root)

        # 1. Inspect — integrity + version gates.
        from mira.user_store.schema import SCHEMA_VERSION as _TARGET
        try:
            plan = event_bundle.inspect_bundle(
                bundle_dir, target_schema_version=_TARGET)
        except FileNotFoundError as exc:
            MessageDialog.error(
                tr("Not a bundle"),
                tr(
                    "{path} is not a valid Mira event bundle "
                    "folder.\n\n{err}"
                ).replace("{path}", str(bundle_dir))
                 .replace("{err}", str(exc)),
                parent=self,
            ).exec()
            return
        if not plan.integrity.ok:
            MessageDialog.error(
                tr("Bundle is damaged"),
                tr(
                    "The bundle's integrity check failed; some files "
                    "are missing or their bytes don't match the "
                    "manifest. Mira won't import a damaged bundle "
                    "to avoid silently breaking your library.\n\n"
                    "{err}"
                ).replace("{err}", plan.integrity.error or tr(
                    "Files: {n} missing, {m} mismatched"
                ).replace("{n}", str(len(plan.integrity.missing)))
                 .replace("{m}", str(len(plan.integrity.mismatch)))),
                parent=self,
            ).exec()
            return
        if plan.version_status == event_bundle.VERSION_NEWER_THAN_LOCAL:
            MessageDialog.warning(
                tr("Bundle is newer than this Mira"),
                tr(
                    "This bundle was created with a newer Mira "
                    "(schema {b}). Update Mira on this PC first, "
                    "then try again."
                ).replace("{b}", str(plan.manifest.schema_version)),
                primary_text=tr("OK"),
                ghost_text=tr("Cancel"),
                parent=self,
            ).exec()
            return

        # 2. Identity gate — does this event_uuid already live here?
        library_base = self.gateway.photos_base_path()
        if library_base is None:
            MessageDialog.error(
                tr("Can't restore"),
                tr(
                    "Mira couldn't find a library folder yet. "
                    "Finish the wizard first."
                ),
                parent=self,
            ).exec()
            return
        existing = self.gateway.index.get(plan.manifest.event_uuid)
        replace_existing = False
        if existing is not None:
            dlg = MessageDialog(
                intent="warning",
                title=tr("Replace existing event?"),
                message=tr(
                    "An event with the same id is already in this "
                    "library — '{name}'. Restoring will replace it "
                    "wholesale.\n\nMira will take a safety snapshot "
                    "of the existing event.db first, so you can roll "
                    "back via Restore from backup… if needed."
                ).replace("{name}", str(existing.get("name") or "")),
                primary_text=tr("Replace"),
                ghost_text=tr("Cancel"),
                parent=self,
            )
            dlg.exec()
            if dlg.result_kind() != "primary":
                return
            replace_existing = True

        # 3. If Replace, take a Part-A snapshot of the existing event
        #    BEFORE swapping. The user's escape hatch.
        if replace_existing:
            saved = self.gateway.snapshot_event(
                plan.manifest.event_uuid, reason="milestone")
            log.info(
                "spec/82 §B.3 Replace: pre-replace snapshot of %s -> %s",
                plan.manifest.event_uuid, saved)

        # 4. Install + register.
        def _do_install(report):
            report(0, 1, tr("Copying bundle into the library…"))
            target_root = None
            if existing is not None:
                # Replace path: install over the existing event_root
                # so the index entry still resolves cleanly.
                target_root = self.gateway.index.resolve_root(
                    existing, library_base)
            new_root = event_bundle.install_bundle(
                plan, library_base, target_event_root=target_root)
            report(1, 1, tr("Registering the event…"))
            event_id = self.gateway.register_event_from_root(new_root)
            return (event_id, new_root)

        ok, result = run_with_progress(
            self, tr("Restoring event…"), _do_install,
            label=tr("Restoring {name}…").replace(
                "{name}", plan.manifest.event_name),
        )
        if not ok:
            MessageDialog.error(
                tr("Restore failed"),
                tr(
                    "Mira couldn't finish the restore. Check disk "
                    "space and permissions, then try again.\n\n"
                    "{err}"
                ).replace("{err}", str(result)),
                parent=self,
            ).exec()
            return
        event_id, new_root = result
        MessageDialog.success(
            tr("Restore complete"),
            tr(
                "{name} is now in your library at {path}."
            ).replace("{name}", plan.manifest.event_name)
             .replace("{path}", str(new_root)),
            parent=self,
        ).exec()
        # Refresh the events page so the new card appears.
        try:
            self.events_page.refresh()
        except Exception:                                  # noqa: BLE001
            log.exception("post-restore refresh failed")

    def _open_restore_user_store(self) -> None:
        """spec/82 §A.3 — Restore user data… handler.

        Opens the standard :class:`RestoreBackupDialog` but pointed
        at the user-store backups dir (one tier above per-event
        backups). The user-data store is the settings + library
        index + templates; restoring it rolls THE APP back, not a
        single event.

        The chosen snapshot is swapped in over the LIVE mira.db
        path (``Gateway._user_store_path``); the user is told the
        app must restart to re-open against the restored file —
        the live ``UserStore`` connection still points at the
        replaced bytes until then.
        """
        from mira.ui.design.dialogs import MessageDialog
        from mira.ui.pages.restore_backup_dialog import RestoreBackupDialog
        backups_dir = self.gateway.user_store_backups_dir()
        if backups_dir is None:
            MessageDialog.error(
                tr("Can't restore"),
                tr(
                    "Mira couldn't find a library folder yet. Finish "
                    "the wizard first so user-data backups have a "
                    "home."
                ),
                parent=self,
            ).exec()
            return
        # Reach the live mira.db path via the gateway (private but
        # stable since spec/53).
        db_path = self.gateway._user_store_path
        dlg = RestoreBackupDialog(
            event_name=tr("user data"),
            backups_dir=backups_dir,
            db_path=db_path,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            saved = dlg.restored_corrupt_path()
            saved_line = ""
            if saved is not None:
                saved_line = tr(
                    "\n\nThe pre-restore copy is saved at:\n{path}"
                ).replace("{path}", str(saved))
            MessageDialog.info(
                tr("User data restored"),
                tr(
                    "User data restored from the chosen snapshot. "
                    "Restart Mira so it re-opens against the "
                    "restored file.{saved}"
                ).replace("{saved}", saved_line),
                parent=self,
            ).exec()

    def _open_restore_backup(self) -> None:
        """spec/82 §A.4 — manual Restore from backup… for the current
        event.

        Shows the snapshot list with timestamp / reason / version (via
        :func:`db_backup.list_snapshots`); the user picks one and
        confirms. On accept, the dialog runs the restore + reports
        the corrupt-original save path. Visible only on the per-event
        surface, so ``_current_event_id`` is always set when this
        runs.
        """
        from mira.ui.design.dialogs import MessageDialog
        from mira.ui.pages.restore_backup_dialog import RestoreBackupDialog
        event_id = self._current_event_id
        if not event_id:
            return
        backups_dir = self.gateway.event_backups_dir(event_id)
        entry = self.gateway.index.get(event_id)
        if backups_dir is None or entry is None:
            MessageDialog.error(
                tr("Can't restore"),
                tr(
                    "Mira couldn't find the backups location for this "
                    "event. Set the library's photos folder first."
                ),
                parent=self,
            ).exec()
            return
        root = self.gateway.index.resolve_root(
            entry, self.gateway.photos_base_path())
        if root is None:
            MessageDialog.error(
                tr("Can't restore"),
                tr(
                    "Mira couldn't resolve this event's folder. "
                    "Check the library's photos path."
                ),
                parent=self,
            ).exec()
            return
        dlg = RestoreBackupDialog(
            event_name=str(entry.get("name") or event_id),
            backups_dir=backups_dir,
            db_path=root / "event.db",
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            saved = dlg.restored_corrupt_path()
            saved_line = ""
            if saved is not None:
                saved_line = tr(
                    "\n\nThe pre-restore copy is saved at:\n{path}"
                ).replace("{path}", str(saved))
            MessageDialog.success(
                tr("Restore complete"),
                tr(
                    "{event}'s database is restored from the chosen "
                    "snapshot.{saved}"
                ).replace("{event}", str(entry.get("name") or event_id))
                 .replace("{saved}", saved_line),
                parent=self,
            ).exec()
            # Force a refresh: the on-disk db is now the snapshot's
            # bytes; everything the UI has cached for this event is
            # potentially stale.
            self.gateway.refresh_index_entry(event_id)
            self._refresh_menu_state()

    def _open_event_cuts_list(self, event_id: str) -> bool:
        """Closed event body click (spec/64 §2.4) → land on the Cuts list.
        Same shape as the ``"share"`` route in :meth:`_on_phase_activated`,
        promoted to a direct door for closed events. Back from here
        returns to the events list (the door the user came in through),
        not the activity dashboard (see :meth:`_on_curate_closed`)."""
        if not self._gate_missing_originals(event_id):
            return False
        if not self.curate_page.open_event(event_id):
            return False
        self._current_event_id = event_id
        self._cuts_entry_door = ENTRY_DASHBOARD
        self.page_stack.show_page(self._CURATE_PAGE_KEY)
        self._refresh_menu_state()
        return True

    def _gate_missing_originals(self, event_id: str) -> bool:
        """Detection layer over the captured tree (charter §7).

        Runs :meth:`Gateway.check_originals` and — if the verdict isn't
        ``OK`` — pops :class:`MissingOriginalsDialog` with the right
        mode (alert for OFFLINE, locate for MOVED). Translates the
        user's choice into the appropriate gateway call:

        * ``relink`` ⇒ :meth:`Gateway.relink_event` + refresh the events
          list (the path columns moved).
        * ``prune`` ⇒ open the event, enumerate missing items, run
          :meth:`EventGateway.prune_missing_originals` (the explicit
          destructive branch — the dialog has already obtained the
          confirm).
        * ``kept`` ⇒ no data change.

        Returns ``True`` when the caller should continue and navigate
        into the event surface, ``False`` when the navigation should
        be suppressed (storage offline — the dashboard would render
        broken thumbnails; keep the user on the events list until they
        reconnect)."""
        from mira.gateway import OriginalsHealth
        from mira.ui.pages.missing_originals_dialog import (
            MissingOriginalsDialog,
            OUTCOME_PRUNE,
            OUTCOME_RELINK,
        )

        try:
            check = self.gateway.check_originals(event_id)
        except Exception:                                       # noqa: BLE001
            log.exception("check_originals failed for %s", event_id)
            return True   # don't block navigation on a detection bug
        if check.is_ok:
            return True

        entry = self.gateway.index.get(event_id) or {}
        event_name = entry.get("name", "") or ""

        # Pre-count missing items so the dialog body can be specific.
        # Requires event.db to be openable (the carved-out-Original-Media
        # subcase). When the whole event folder is gone, open_event
        # fails and we fall back to the generic body copy.
        missing_count: Optional[int] = None
        if (check.state == OriginalsHealth.ORIGINALS_MOVED
                and check.event_root is not None
                and check.event_root.exists()):
            try:
                eg = self.gateway.open_event(event_id)
                try:
                    missing_count = len(eg.list_missing_origin_items())
                finally:
                    eg.close()
            except Exception:                                   # noqa: BLE001
                missing_count = None

        dlg = MissingOriginalsDialog(
            check=check, event_name=event_name,
            missing_count=missing_count, parent=self,
        )
        dlg.exec()

        if dlg.outcome == OUTCOME_RELINK and dlg.chosen_path is not None:
            try:
                self.gateway.relink_event(event_id, dlg.chosen_path)
            except (FileNotFoundError, KeyError) as exc:
                from mira.ui.design.dialogs import show_error
                show_error(
                    self, tr("Couldn't relink"),
                    tr("The folder you picked doesn't contain this event's "
                       "event.db, so Mira can't be sure it's the right one.")
                    + f"\n\n{exc}",
                )
                return False
            self.events_page.refresh()
            return True

        if dlg.outcome == OUTCOME_PRUNE:
            try:
                eg = self.gateway.open_event(event_id)
                try:
                    ids = eg.list_missing_origin_items()
                    eg.prune_missing_originals(ids)
                finally:
                    eg.close()
            except Exception:                                   # noqa: BLE001
                log.exception("prune_missing_originals failed for %s", event_id)
                return False
            self.events_page.refresh()
            return True

        # Outcome == kept. Block navigation only on STORAGE_OFFLINE —
        # the dashboard wouldn't have any media to render.
        return check.state != OriginalsHealth.STORAGE_OFFLINE

    def _on_card_delete_requested(self, event_id: str) -> None:
        """spec/77 §6 — ⋮ menu Delete entry from a tile. Sets the
        current event id so the existing :meth:`_on_delete_event` flow
        (which reads ``self._current_event_id``) targets the right
        event without needing the menu-bar trip."""
        self._current_event_id = event_id
        self._on_delete_event()

    def _on_card_status_toggle_requested(self, event_id: str) -> None:
        """Status-badge click on an event tile (spec/64 §2.3) → flip
        ``event.is_closed`` instantly (no confirm) and refresh the events
        list so the tile picks up its new body content + badge state."""
        try:
            eg = self.gateway.open_event(event_id)
            try:
                new_state = not bool(eg.event().is_closed)
                eg.set_closed(new_state)
            finally:
                eg.close()
        except (KeyError, RuntimeError):
            log.exception("status-badge toggle failed for %s", event_id)
            return
        # Keep the dashboard's per-event index entry + filter-rail in sync.
        try:
            self.gateway.refresh_index_entry(event_id)
        except Exception:                                       # noqa: BLE001
            log.exception("refresh_index_entry failed for %s", event_id)
        self.events_page.refresh()
        # If the toggled event happens to be the currently-open one, the
        # menu state needs the same refresh the menu-driven toggle path
        # does (Event menu's Close↔Re-open label / closed-card chrome).
        if self._current_event_id == event_id:
            self.phases_page.set_event(event_id)
            self._refresh_menu_state()

    def _spawn_classify_pass(self, event_id: str) -> None:
        """Run ``classify_event_items`` on a daemon thread — quiet (log
        only), never touches Qt, and opens its OWN gateway inside the
        worker (SQLite connections are thread-bound). One pass per event
        at a time; the bulk write holds a single short lock window."""
        import threading

        running = getattr(self, "_classify_passes_running", None)
        if running is None:
            running = self._classify_passes_running = set()
        if event_id in running:
            return
        running.add(event_id)

        def _run() -> None:
            try:
                from pathlib import Path as _Path
                from mira.ingest.classify_pass import (
                    classify_event_items,
                )
                eg = self.gateway.open_event(event_id)
                try:
                    root = eg.event_root
                    if root is None:
                        return
                    classify_event_items(eg, _Path(root))
                finally:
                    eg.close()
            except Exception:                               # noqa: BLE001
                log.exception("classify pass failed for %s", event_id)
            finally:
                running.discard(event_id)

        threading.Thread(
            target=_run, name=f"classify-{event_id[:8]}", daemon=True,
        ).start()

    def _on_phase_tile_activated(self, phase: str) -> None:
        """Dashboard phase tile click → route to the corresponding surface.

        Slice A (2026-06-06): direct dispatch — the dashboard emits the same
        phase strings the routing handler accepts (``"collect"`` /
        ``"pick"`` / ``"edit"`` / ``"share"``)."""
        self._on_phase_activated(phase)

    # ── Surface 05: Days Lists ────────────────────────────────────────
    # spec/65 §3.5 wire-up. The page sits between Phases and Pick; gating
    # is cheap (two GROUP BYs + one bucket_cache read per day) so the page
    # is rebuilt at every entry instead of stale-cached.

    def _open_days_lists_for(self, event_id: str) -> None:
        """Build the per-day snapshots from the live gateway and route to
        the Days Lists dashboard. On a snapshot-build failure the user
        is left on Phases with a logged warning — the legacy fallback
        into the retired PickPage shell is gone (Surface 07 absorbed
        the engine into PickerPage and PickerPage opens per-item, not
        per-event)."""
        snapshots = self._build_day_snapshots(event_id)
        if snapshots is None:
            log.warning(
                "Days Lists snapshot build failed for %s; staying on Phases",
                event_id)
            return
        # When entering DaysLists during a Quick Sweep session, fold
        # undecided items into the QS-default side so the bars read the
        # all-green-on-entry contract the QS surface promises (per-event
        # QS borrows the pick-phase store; nothing's been written yet on
        # a fresh session, so without this every bar reads as 0/0).
        if self._quick_sweep is not None:
            snapshots = self._qs_apply_default_to_snapshots(snapshots)
        event_name = self._lookup_event_name(event_id) or tr("Event")
        self.days_lists_page.setEventForPreview(event_name, snapshots)
        # spec/71 — the shared Days Lists takes the host phase's chrome.
        # During a QS session it reads Collect/blue regardless of the
        # underlying Pick store; otherwise Edit when the Edit bridge is
        # active, else Pick (the default).
        if self._quick_sweep is not None:
            identity_phase = "collect"
        elif self._export_phase_active:
            identity_phase = "export"
        elif self._edit_phase_active:
            identity_phase = "edit"
        else:
            identity_phase = "pick"
        self.days_lists_page.set_phase_identity(identity_phase)
        self.page_stack.show_page(self._DAYS_LISTS_PAGE_KEY)

    def _qs_apply_default_to_snapshots(self, snapshots: list) -> list:
        """Fold each snapshot's undecided count (items - picked - skipped)
        into the QS default side. Picked / skipped stay non-negative;
        the total ``items`` is unchanged."""
        from mira.picked.status import STATE_PICKED
        default = self._qs_default_phase_state()
        for snap in snapshots:
            undecided = max(0, snap.items - snap.picked - snap.skipped)
            if undecided == 0:
                continue
            if default == STATE_PICKED:
                snap.picked = snap.picked + undecided
            else:
                snap.skipped = snap.skipped + undecided
        return snapshots

    def _build_day_snapshots(self, event_id: str) -> Optional[list]:
        """Compose ``DaySnapshot[]`` from the gateway for one event.

        Two GROUP BYs (via :meth:`phase_day_progress`) carry the per-day
        Pick totals; the bucket count for each day comes from the cached
        clustering. Returns ``None`` on a gateway failure so the caller
        can fall back."""
        try:
            eg = self.gateway.open_event(event_id)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "Days Lists: cannot open event %s", event_id)
            return None
        try:
            trip_days = eg.trip_days()
            progress = eg.phase_day_progress()
            pick_map = progress.get("pick", {})
            snapshots: list[DaySnapshot] = []
            for d in trip_days:
                cell = pick_map.get(d.day_number, {}) or {}
                total = int(cell.get("total", 0))
                decided = int(cell.get("decided", 0))
                picked = int(cell.get("picked", 0))
                skipped = max(0, decided - picked)
                try:
                    # The "Clusters · N" badge counts only real clusters
                    # (burst / focus_bracket / exposure_bracket / repeat)
                    # — matching the day grid where those kinds collapse
                    # to a single cluster cover. Individual / moment /
                    # video buckets flatten to per-item cells, so they
                    # are not "clusters" in the user-visible sense.
                    from mira.picked.model import REAL_CLUSTER_KINDS
                    buckets = sum(
                        1 for b in eg.cached_buckets("pick", d.day_number)
                        if b.kind in REAL_CLUSTER_KINDS
                    )
                except Exception:                                  # noqa: BLE001
                    log.exception(
                        "cached_buckets failed for day %s of %s",
                        d.day_number, event_id)
                    buckets = 0
                # Per-day capture-hour distribution — drives the small
                # spark on each DayRow. One SQL pass keyed by day_number
                # so the dashboard's analytic feel doesn't add round-
                # trips per day.
                snapshots.append(DaySnapshot(
                    day_number=d.day_number,
                    title=(d.description or f"Day {d.day_number}"),
                    # TripDay.date is already an ISO string in the store;
                    # the model carries Optional[str] not date.
                    date_iso=(str(d.date) if d.date else ""),
                    picked=picked,
                    skipped=skipped,
                    buckets=buckets,
                    items=total,
                    location=(getattr(d, "location", "") or ""),
                ))
            self._fill_capture_hours(eg, snapshots)
            return snapshots
        except Exception:                                          # noqa: BLE001
            log.exception(
                "Days Lists snapshot build failed for %s", event_id)
            return None
        finally:
            try:
                eg.close()
            except Exception:                                      # noqa: BLE001
                pass

    @staticmethod
    def _fill_capture_hours(eg, snapshots: list) -> None:
        """Project items per (day, capture-hour) into each snapshot's
        ``capture_hours`` 24-bucket array. Pure read-only — uses the same
        ``visible_item`` view ``phase_day_progress`` reads, so a hidden
        day contributes nothing."""
        try:
            rows = eg.store.conn.execute(
                "SELECT day_number AS dn, "
                "       CAST(substr(capture_time_corrected, 12, 2) AS INTEGER) AS hr, "
                "       COUNT(*) AS n "
                "FROM visible_item "
                "WHERE provenance='captured' "
                "  AND capture_time_corrected IS NOT NULL "
                "GROUP BY day_number, hr"
            ).fetchall()
        except Exception:                                          # noqa: BLE001
            log.exception("capture-hour rollup failed")
            return
        by_day: dict[int, list[int]] = {}
        for r in rows:
            dn = r["dn"]
            hr = r["hr"]
            if dn is None or hr is None or hr < 0 or hr >= 24:
                continue
            bucket = by_day.setdefault(int(dn), [0] * 24)
            bucket[int(hr)] = int(r["n"] or 0)
        for snap in snapshots:
            snap.capture_hours = by_day.get(snap.day_number, [0] * 24)

    def _lookup_event_name(self, event_id: str) -> Optional[str]:
        """Pull the event name from the index without opening event.db a
        second time (gateway already opens it for the snapshot build)."""
        try:
            for row in self.gateway.list_events():
                if str(row.get("id")) == event_id:
                    return str(row.get("name") or "")
        except Exception:                                          # noqa: BLE001
            log.exception("event-name lookup failed for %s", event_id)
        return None

    def _on_days_lists_back(self) -> None:
        """Back from Days Lists. During a QS session this is the
        outermost-back → confirm and finalize (copy_kept for standalone,
        log for per-event). Else returns to Phases (Surface 03). The
        Edit/Export phase route flags clear here — leaving Days Lists
        ends the visit."""
        if self._quick_sweep is not None:
            self._qs_finalize_via_back()
            return
        self._edit_phase_active = False
        self._export_phase_active = False
        self.page_stack.show_page(self._ACTIVITY_PAGE_KEY)

    def _on_days_lists_day_activated(self, day_number: int) -> None:
        """Day card clicked → open the redesigned Days Grid (Surface 06)
        for that day. During a QS session :meth:`_qs_open_day` handles
        both standalone (paths mode via setDay) and per-event (gateway
        via open_for_day) routing — same widgets, different source."""
        # Quick Sweep session in flight (standalone or per-event)
        # routes through the QS opener so the day items list is built
        # for the leaf QS viewer.
        if self._quick_sweep is not None:
            self._qs_open_day(day_number)
            return
        if self._current_event_id is None:
            return
        title, date_iso = self._lookup_day_meta(
            self._current_event_id, day_number)
        # spec/70 Phase 3 §3 / spec/68 §3 — phase-aware grid open:
        # Edit hides Pick-all/Skip-all (creative-only, spec/66 §1.1),
        # Export born-green + click-toggles + X-on-shipped cleanup +
        # Export-green trigger on the toolbar.
        if self._export_phase_active:
            grid_phase = "export"
        elif self._edit_phase_active:
            grid_phase = "edit"
        else:
            grid_phase = "pick"
        if not self.days_grid_page.open_for_day(
            self._current_event_id, day_number,
            title=title, date_iso=date_iso, phase=grid_phase,
        ):
            log.warning(
                "DaysGridPage.open_for_day(%s, %s, phase=%s) failed; "
                "staying on Days Lists. (The legacy PickPage fallback "
                "retired with spec/70 Phase 3 §2.)",
                self._current_event_id, day_number, grid_phase)
            return
        self.page_stack.show_page(self._DAYS_GRID_PAGE_KEY)
        self.days_grid_page.setFocus()

    def _lookup_day_meta(
        self, event_id: str, day_number: int,
    ) -> tuple[str, str]:
        """Day title + ISO date for the day-navigator pill on the
        redesigned grid. Empty strings on lookup failure — the pill
        still renders ("Day N · 0 items") so the user isn't stranded."""
        try:
            eg = self.gateway.open_event(event_id)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "Days Grid: cannot open event %s for meta", event_id)
            return "", ""
        try:
            for d in eg.trip_days():
                if d.day_number == day_number:
                    return (
                        d.description or f"Day {day_number}",
                        str(d.date) if d.date else "",
                    )
        except Exception:                                          # noqa: BLE001
            log.exception("trip_days lookup failed for %s", event_id)
        finally:
            try:
                eg.close()
            except Exception:                                      # noqa: BLE001
                pass
        return "", ""

    def _on_days_grid_back(self) -> None:
        """Back from the Days Grid returns to Days Lists. Releases the
        page's event gateway (no-op in paths mode) so the next day-card
        click opens fresh. During a QS session the DaysListsPage carries
        the QS chrome already (setEventForPreview / gateway-built
        snapshots) so we don't rebuild here."""
        self.days_grid_page.close_event()
        self.page_stack.show_page(self._DAYS_LISTS_PAGE_KEY)

    def _on_days_grid_step_day(self, delta: int) -> None:
        """Day navigator pill ‹ / ›. Walks to the previous/next day in
        the event's TripDay axis. No-op at the boundaries."""
        if self._current_event_id is None:
            return
        cur = self.days_grid_page.current_day_number()
        try:
            eg = self.gateway.open_event(self._current_event_id)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "Days Grid: cannot open event %s to step day",
                self._current_event_id)
            return
        try:
            days = sorted(
                d.day_number for d in eg.trip_days()
                if d.day_number is not None
            )
        finally:
            try:
                eg.close()
            except Exception:                                      # noqa: BLE001
                pass
        if cur not in days:
            return
        idx = days.index(cur) + delta
        if idx < 0 or idx >= len(days):
            return
        self._on_days_lists_day_activated(days[idx])

    def _on_days_grid_item_activated(self, item_id: str) -> None:
        """Single-photo / video click on the Days Grid. Routes by
        active mode:

        * **Quick Sweep session active** → open the redesigned QS
          viewer with the current day's items (DaysLists → DaysGrid →
          QS viewer flow, spec/70 Phase 3).
        * **Picker bridge active** → bridge into :class:`PickerPage`
          (Surface 07) as before.

        Three routes:

        * Day mode + photo → ``picker_page.open_to_item(...)`` opens a
          synthetic 1-item bucket so the Picker chrome + viewport
          navigate within just that item.
        * Cluster sub-grid mode + photo → ``picker_page.open_to_cluster(...)``
          opens the REAL cluster bucket so Enter sweep, intra-cluster
          ← →, and Combined preview (exposure brackets) all work.
        * Video → also ``picker_page.open_to_item(...)``. The unified
          PickerPage handles both kinds: ``PhotoViewport`` arms video
          on landing (poster→live in place) and the transport-bar
          reveal on ``compact_row`` shows the few buttons that decorate
          the video for as long as it is the current item. No separate
          page, no jump (spec/70 row 11 folded into 07, 2026-06-15).

        Back from the Picker emits :sig:`closed` which routes the user
        back to the Days Grid (refreshed) via the bridge flag in
        :meth:`_on_select_closed`."""
        # Quick Sweep session — route to the QS viewer instead of
        # Picker. The QS viewer carries the day's items + the K/D
        # ledger; Back from it refreshes this Days Grid via
        # :meth:`_qs_return_to_days_grid`.
        if self._quick_sweep is not None:
            self._qs_open_viewer_for_item(item_id)
            return
        # spec/68 §3 — Export mode handles the click itself (in-place
        # green↔red toggle + X-on-shipped cleanup). The grid still
        # fires item_activated for symmetry, but here the host does
        # nothing — there's no drill-in destination for Export.
        if self._export_phase_active:
            return
        event_id = self.days_grid_page.current_event_id()
        day_number = self.days_grid_page.current_day_number()
        if event_id is None:
            return
        # spec/70 Phase 3 §3 + Surface 12 fold (2026-06-15) — Edit-phase
        # route: every item, photo OR video, opens the unified
        # :class:`EditorPage`. The viewport sweeps both kinds and the
        # spec/56 marker workshop reveals in place under the canvas
        # when a video lands. No separate video page.
        if self._edit_phase_active:
            self._open_edit_surface_for_item(event_id, day_number, item_id)
            return
        # spec/70 row 11 folded into 07 (Nelson 2026-06-15): ONE
        # PickerPage handles photos AND videos. The viewport already
        # arms video on landing (poster→live in place) and the
        # compact_row transport bar reveals only when the landed item
        # is a video. No kind branch; no separate page.
        # If the user is inside a cluster sub-grid and clicked a member,
        # route to the cluster-bucket entry so in-cluster nav works.
        cluster = self.days_grid_page.current_cluster()
        if cluster is not None:
            entry_idx = next(
                (i for i, m in enumerate(cluster.members)
                 if m.item_id == item_id),
                0,
            )
            ok = self.picker_page.open_to_cluster(
                event_id, day_number, cluster, entry_idx=entry_idx)
        else:
            ok = self.picker_page.open_to_item(
                event_id, day_number, item_id)
        if not ok:
            log.warning(
                "PickerPage open from Days Grid failed (%s, %s, %s)",
                event_id, day_number, item_id)
            return
        # Mark the bridge as active so PickerPage.closed returns to the
        # Days Grid (refreshed). Cleared by _on_select_closed.
        self._days_grid_bridge_active = True
        self.page_stack.show_page(self._SELECT_PAGE_KEY)

    def _open_edit_surface_for_item(
        self, event_id: str, day_number: int, item_id: str,
    ) -> None:
        """spec/70 Phase 3 §3 + Surface 12 fold (2026-06-15) — Days Grid
        (Edit phase) → :class:`EditorPage`. Photos AND videos go through
        the SAME route now: a cluster bucket if the click was inside a
        sub-grid, a whole-day bucket otherwise. The viewport sweeps both
        kinds; on a video landing, EditorPage reveals the spec/56
        marker workshop in place."""
        cluster = self.days_grid_page.current_cluster()
        if cluster is not None:
            entry_idx = next(
                (i for i, m in enumerate(cluster.members)
                 if m.item_id == item_id),
                0,
            )
            ok = self.edit_page.open_to_cluster(
                event_id, day_number, cluster, entry_idx=entry_idx)
        else:
            ok = self.edit_page.open_to_item(
                event_id, day_number, item_id)
        if not ok:
            log.warning(
                "EditorPage open from Days Grid failed (%s, %s, %s)",
                event_id, day_number, item_id)
            return
        self._days_grid_bridge_active = True
        self.page_stack.show_page(self._PROCESS_PAGE_KEY)

    def _on_days_lists_new_pass_stub(self) -> None:
        """+ Start a new pass… — out of scope for the route-swap. Logs
        + leaves a hint until the Pick surface gains the new-pass flow."""
        log.info("Days Lists 'new pass' clicked (stub; not yet wired)")

    def _on_days_lists_pick_all_stub(self) -> None:
        self._apply_days_lists_bulk("picked")

    def _on_days_lists_skip_all_stub(self) -> None:
        self._apply_days_lists_bulk("skipped")

    def _on_days_lists_day_pick_all_stub(self, day_number: int) -> None:
        self._apply_days_lists_bulk("picked", day_number=day_number)

    def _on_days_lists_day_skip_all_stub(self, day_number: int) -> None:
        self._apply_days_lists_bulk("skipped", day_number=day_number)

    def _apply_days_lists_bulk(
        self, state: str, *, day_number: "Optional[int]" = None,
    ) -> None:
        """Bulk Pick/Skip from the Days Lists — set every CAPTURED item's
        decision (event-wide, or one day) for the active phase, then refresh
        the list. Pick/Skip are reversible per item; the event-wide sweep
        confirms first. Pick phase writes the 'pick' ledger; Edit/Export
        write 'edit' (the Export ship decision rides the edit phase_state)."""
        event_id = self._current_event_id
        if event_id is None:
            return
        phase = ("edit"
                 if (self._edit_phase_active or self._export_phase_active)
                 else "pick")
        if day_number is None:
            from PyQt6.QtWidgets import QMessageBox
            verb = tr("Pick") if state == "picked" else tr("Skip")
            if QMessageBox.question(
                self, tr("{verb} all days?").replace("{verb}", verb),
                tr("This marks every day in this event as {state}. You can "
                   "still change individual items afterwards.")
                .replace("{state}", state),
            ) != QMessageBox.StandardButton.Yes:
                return
        eg = self.gateway.open_event(event_id)
        try:
            items = (eg.items(provenance="captured", day=day_number)
                     if day_number is not None
                     else eg.items(provenance="captured"))
            eg.set_items_phase_state([it.id for it in items], phase, state)
        finally:
            eg.close()
        self._open_days_lists_for(event_id)

    def _on_delete_event(self) -> None:
        """Event menu "Delete event" → offer a choice (spec/14 §5D): remove from
        Mira only (index-only, files kept) OR also delete this event's whole
        folder from disk. The delete-files path removes user *originals* (invariant
        #9 gravity), so it takes a second blunt confirm. Returns to the (refreshed)
        events list."""
        from PyQt6.QtWidgets import QMessageBox

        event_id = self._current_event_id
        if event_id is None:
            return
        # Pull the name + item count from the gateway (the activity dashboard
        # doesn't expose a current_event property; one open of the event.db is
        # cheap and keeps the dialog text accurate).
        try:
            eg = self.gateway.open_event(event_id)
            try:
                name = eg.event().name or tr("(unnamed event)")
                n_items: Optional[int] = len(eg.items(include_hidden=True))
            finally:
                eg.close()
        except (KeyError, RuntimeError):
            name = tr("(unnamed event)")
            n_items = None

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(tr("Delete event"))
        box.setText(tr("Delete “{name}”?").replace("{name}", name))
        box.setInformativeText(tr(
            "“Remove from Mira” drops only the app's record — your photos and folders "
            "on disk stay, and you can re-add the event later with “Import plan from folder”.\n\n"
            "“Delete photos too” also permanently deletes this event's entire folder on disk "
            "(its photos + folders). Your camera card / original source is not touched."
        ))
        keep_btn = box.addButton(
            tr("Remove from Mira"), QMessageBox.ButtonRole.AcceptRole)
        delete_btn = box.addButton(
            tr("Delete photos too"), QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(keep_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is cancel_btn or clicked is None:
            return

        delete_files = clicked is delete_btn
        if delete_files:
            count = tr("{n} photo/video file(s)").replace("{n}", str(n_items)) \
                if n_items is not None else tr("all this event's files")
            confirm = QMessageBox.warning(
                self,
                tr("Permanently delete the photos?"),
                tr(
                    "This permanently deletes “{name}” and {count} from disk — including the "
                    "original photos under it. This CANNOT be undone.\n\nYour camera card / "
                    "original source is not touched, but if these are the only copies, they "
                    "are gone. Continue?"
                ).replace("{name}", name).replace("{count}", count),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

        self.gateway.delete_event(event_id, delete_files=delete_files)
        self._current_event_id = None
        self._on_event_back()

    def _open_camera_clocks_for_event(self) -> None:
        """Plan page "Camera clocks" → the reused CameraClockDialog (Slice B1). Reads the
        per-camera answers from the store's Camera rows, and on Save persists the new offset
        via ``save_camera`` + re-derives the affected items' corrected times via
        ``recompute_corrected_times`` (no EXIF bake — charter §3)."""
        from collections import Counter

        from PyQt6.QtWidgets import QDialog, QMessageBox

        from mira.store import models as m
        from mira.ui.pages.camera_clock_dialog import CameraClockDialog

        if self._current_event_id is None:
            return
        eg = self.gateway.open_event(self._current_event_id)
        try:
            cams = eg.cameras()
            store_days = eg.trip_days()
        finally:
            eg.close()
        cam_ids = sorted(c.camera_id for c in cams)
        if not cam_ids:
            QMessageBox.information(
                self, tr("No cameras recorded yet"),
                tr(
                    "No camera clocks have been recorded for this event yet. They are saved "
                    "the first time you cull a camera / phone / other source — then you can "
                    "correct a wrong one here."
                ),
            )
            return
        offsets = [d.tz_minutes for d in store_days if d.tz_minutes is not None]
        trip_tz = (Counter(offsets).most_common(1)[0][0] / 60.0) if offsets else 0.0
        # Reconstruct the human answer per camera from its applied offset (applied = trip_tz
        # − configured ⇒ configured = trip_tz − applied).
        initial: dict[str, dict] = {}
        for c in cams:
            ao = c.applied_offset_minutes
            if ao in (None, 0):
                initial[c.camera_id] = {"correct": True, "configured_tz": None}
            else:
                initial[c.camera_id] = {
                    "correct": False, "configured_tz": trip_tz - ao / 60.0}
        dlg = CameraClockDialog(
            cam_ids, default_trip_tz_hours=trip_tz, ask_trip_tz=False,
            initial=initial, edit_mode=True, parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        answers = dlg.result_answers()
        by_id = {c.camera_id: c for c in cams}
        eg = self.gateway.open_event(self._current_event_id)
        changed = False
        try:
            for cam_id, ans in answers.items():
                if ans.get("correct", True):
                    new_applied, new_cfg = 0, None
                else:
                    cfg = float(ans["configured_tz"])
                    new_applied = round((trip_tz - cfg) * 60)
                    new_cfg = round(cfg * 60)
                existing = by_id.get(cam_id)
                cur = (existing.applied_offset_minutes or 0) if existing else 0
                if new_applied == cur:
                    continue  # unchanged — skip the recompute
                changed = True
                cam_row = existing or m.Camera(camera_id=cam_id)
                cam_row.configured_tz_minutes = new_cfg
                cam_row.applied_offset_minutes = new_applied
                cam_row.applied_at = eg._now()
                eg.save_camera(cam_row)
                eg.recompute_corrected_times(cam_id, applied_offset_minutes=new_applied)
        finally:
            eg.close()
        if changed:
            QMessageBox.information(
                self, tr("Camera clocks saved"),
                tr("Saved — the affected photos have been re-grouped with the corrected "
                   "timezones."),
            )
        if self._current_event_id is not None:
            self.phases_page.set_event(self._current_event_id)

    def _open_adjust_tz_for_event(self) -> None:
        """Plan page "Adjust TZ" → the ported AdjustEventTzDialog (Slice B2, gateway-native,
        virtual-EXIF recompute). Refreshes the plan page on close."""
        from mira.ui.pages.adjust_event_tz_dialog import AdjustEventTzDialog

        if self._current_event_id is None:
            return
        dlg = AdjustEventTzDialog(self.gateway, self._current_event_id, self)
        dlg.exec()
        if self._current_event_id is not None:
            self.phases_page.set_event(self._current_event_id)

    def _open_capture(self, event_id: str) -> None:
        """**Retired (Nelson 2026-06-08).** The legacy Capture chain (spec/13)
        no longer runs from the Collect tile — :meth:`_open_collect` replaces
        it. Method kept temporarily as a thin redirect so any rogue caller
        gets routed to the new path until a cleanup sweep removes
        ``capture_flow.py`` + the legacy preingest/action dialogs."""
        self._open_collect(event_id)

    def _open_collect(self, event_id: str) -> None:
        """Collect — incremental ingest into an existing event.

        spec/64 §4 — the legacy unified PlanDialog (Info + per-day
        editor) retires here: identity edits route through the tile's
        title-zone door (`_open_event_header_dialog`); per-day edits
        route through the left-zone door
        (`_open_event_days_table_dialog`). Collect itself becomes a
        clean per-day flow: scan → coverage popup → multi-date split
        confirm (when applicable) → merged_rows → conditional
        TZ-calibration ask → ingest gate → ingest. The user can edit
        per-day metadata before or after Collect via the Days Table
        dialog; the spec/65-ish per-location-group prompt for phone-
        less days arrives in slice 5.

        Cancel anywhere = clean no-op (no event mutations)."""
        from pathlib import Path
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        from core.scan_source import scan_source
        from mira.ui.base.progress import run_with_progress

        # ── 1. Read the event + existing trip_days for the merge. ────────
        try:
            eg = self.gateway.open_event(event_id)
            try:
                ev = eg.event()
                existing_days = list(eg.trip_days())
            finally:
                eg.close()
        except Exception:                                       # noqa: BLE001
            log.exception("Collect: could not open event %s", event_id)
            return

        # ── 2. Source pick. Default to photos_base_path. ────────────────
        base = self.gateway.photos_base_path()
        start_dir = str(base) if base else ""
        chosen = QFileDialog.getExistingDirectory(
            self,
            tr("Collect — pick the photos source folder for {name}")
            .replace("{name}", ev.name or event_id),
            start_dir,
        )
        if not chosen:
            return                                              # clean no-op

        # ── 3. Scan off-thread with progress. ───────────────────────────
        home_country, home_tz_minutes = self._home_defaults_from_settings()

        # spec/64 §4.4 — scan with home=None so the GPS-less days come
        # back blank; the per-location-group prompt below asks the user
        # (with the home values as suggestions).
        def _do_scan(_progress):
            return scan_source(
                Path(chosen),
                home_country=None,
                home_tz_minutes=None,
            )

        ok, result = run_with_progress(
            self, tr("Scanning photos…"), _do_scan,
            label=tr("Reading EXIF from {path}").replace("{path}", chosen),
        )
        if not ok:
            QMessageBox.critical(
                self, tr("Scan failed"),
                tr("Could not scan the folder. Error:\n\n{err}")
                .replace("{err}", str(result)),
            )
            return
        scan = result
        if not scan.scan_rows:
            QMessageBox.warning(
                self, tr("No photos found"),
                tr("The scan didn't find any photos with a readable "
                   "capture date in {path}.")
                .replace("{path}", chosen),
            )
            return

        # ── 4. Coverage popup (no info icon — Nelson 2026-06-08). ───────
        summary_text = self._format_phone_summary(scan.phone_summary)
        if summary_text:
            msg = QMessageBox(self)
            msg.setWindowTitle(tr("Phone-EXIF coverage"))
            msg.setText(summary_text)
            msg.setIcon(QMessageBox.Icon.NoIcon)
            msg.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg.exec()

        # ── 4b. Multi-date split confirmation (spec/57 §4.1). A run whose
        # photos span >1 NEW date first shows the proposed day split —
        # the locked moment to pull post-midnight shots into the previous
        # evening via the "day starts at" boundary. Single-date runs
        # ingest straight through.
        existing_dates = {
            d for td in existing_days
            if (d := self._safe_date(td.date)) is not None
        }
        new_dates = [r.date for r in scan.scan_rows
                     if r.date not in existing_dates]
        if len(new_dates) > 1:
            from core.scan_source import build_scan_result
            from mira.ui.pages.day_split_dialog import DaySplitDialog

            split = DaySplitDialog(
                [p.timestamp for p in scan.photos if p.timestamp is not None],
                initial_minutes=scan.day_start_minutes,
                parent=self,
            )
            if split.exec() != QDialog.DialogCode.Accepted:
                return                                          # clean no-op
            boundary = split.day_start_minutes()
            if boundary != scan.day_start_minutes:
                # Pure regroup — same photos, new day boundary; every
                # derived structure (rows, presences, per-photo records)
                # stays consistent because the builder is the one seam.
                scan = build_scan_result(
                    scan.photos,
                    source_root=scan.source_root or Path(chosen),
                    # spec/64 §4.4 — keep home defaults out of the
                    # silent-fill path; the per-stretch prompt below
                    # collects the user's choice instead.
                    home_country=None,
                    home_tz_minutes=None,
                    day_start_minutes=boundary,
                )

        # ── 5. Merge existing trip_days + new scan days (date-keyed). ──
        merged_rows = self._merge_collect_rows(scan.scan_rows, existing_days)

        # ── 5b. Late-phone TZ reconciliation (spec/57 §4.1). When this
        # run's phone EXIF disagrees with a day the plan already carries
        # (built manually, day by day), PROMPT — the plan is never
        # silently overridden; matching days stay silent.
        self._reconcile_phone_tz(
            event_id=event_id, scan_rows=scan.scan_rows,
            existing_days=existing_days, merged_rows=merged_rows,
        )

        # spec/78 §A — single ask for all merged rows missing country
        # OR TZ (whether the gap came from phone-less days or
        # existing trip_days without that info). Days the user Skips
        # stay blank; the Days Table dialog below lets them fine-tune.
        self._prompt_for_no_gps_days(
            merged_rows,
            home_country=home_country,
            home_tz_minutes=home_tz_minutes,
        )

        # ── Per-day editor (Days Table dialog) on the merged_rows so
        # the user can preview / fix country / TZ / location /
        # description and Include checkboxes BEFORE the copy step. The
        # Header dialog isn't shown here — Collect is a per-day flow;
        # identity edits route through the tile's title-zone door.
        import json as _json
        from core.feature_flags import load_flags
        from core.peek_select import select_for_peek
        from mira.ui.pages.day_browse_dialog import DayBrowseDialog

        try:
            existing_participants = _json.loads(ev.participants or "[]")
            if not isinstance(existing_participants, list):
                existing_participants = []
        except (ValueError, TypeError):
            existing_participants = []
        existing_info = {
            "name": ev.name or "",
            "event_type": ev.event_type or "trip",
            "event_subtype": ev.event_subtype or "",
            "description": ev.description or "",
            "duration_value": ev.duration_value,
            "duration_unit": ev.duration_unit,
            "participants": existing_participants,
        }

        try:
            flags = load_flags(self.gateway.user_store)
            can_save_load_csv = flags.plan_save_load_csv
        except Exception:                                       # noqa: BLE001
            log.exception(
                "Could not read feature flags; defaulting CSV gate off")
            can_save_load_csv = False

        peek_target = _peek_target_from_settings()

        def _browse_day(day):
            candidates = scan.candidates_by_date.get(day, [])
            selected = select_for_peek(candidates, target=peek_target)
            paths = [c.path for c in selected]
            if not paths:
                noinfo = QMessageBox(self)
                noinfo.setWindowTitle(tr("Nothing to preview"))
                noinfo.setText(tr(
                    "No preview-able photos for {date} (videos and very "
                    "large files are skipped to keep the peek fast)."
                ).replace("{date}", day.isoformat()))
                noinfo.setIcon(QMessageBox.Icon.NoIcon)
                noinfo.setStandardButtons(QMessageBox.StandardButton.Ok)
                noinfo.exec()
                return
            dlg_browse = DayBrowseDialog(
                paths,
                title=tr("Browse — {date}").replace("{date}", day.isoformat()),
                parent=self,
            )
            dlg_browse.exec()

        days_dlg = self._exec_event_days_table_dialog(
            self._build_days_table_dialog(
                merged_rows,
                can_save_load_csv=can_save_load_csv,
                browse_handler=_browse_day,
            ))
        if not days_dlg:
            return                                              # clean no-op
        edited_rows = days_dlg.rows()
        included = [r for r in edited_rows if r.checked]
        if not included:
            QMessageBox.warning(
                self, tr("Nothing to import"),
                tr("You unchecked every day. Pick at least one day to import."),
            )
            return
        # Collect doesn't edit Header info — pass existing through both
        # slots so the ingest gate's change detector skips set_classification.
        edited_info = dict(existing_info)

        # ── Ingest-mode gate. ──────────────────────────────────────────
        # Event root resolved via the index entry (cross-volume fallback
        # handled by resolve_root, so a relocated library still works).
        entry = self.gateway.index.get(event_id)
        event_root = (
            self.gateway.index.resolve_root(entry, self.gateway.photos_base_path())
            if entry else None
        )
        if event_root is None:
            log.warning("Collect: cannot resolve event_root for %s", event_id)
            return

        # ── 9b. Conditional TZ-calibration ask (spec/52 §8.2). ──────────
        # Trigger fires only when a checked day's location-derived TZ
        # differs from the user's home TZ AND non-phone cameras are present.
        # Returns the per-(camera, event_day) declared offsets the user
        # accepted; an empty dict means "no calibration needed" or "user
        # skipped"; None means "user aborted the entire Collect".
        calibration_decisions = self._collect_run_tz_calibration(
            event_id=event_id, event_name=ev.name or event_id,
            source_root=Path(chosen), scan=scan,
            edited_rows=edited_rows, existing_days=existing_days,
        )
        if calibration_decisions is None:
            return                                              # user aborted

        self._open_collect_ingest_gate(
            event_id=event_id, event_name=ev.name or event_id,
            event_root=event_root, scan=scan,
            edited_rows=edited_rows, edited_info=edited_info,
            existing_info=existing_info, existing_days=existing_days,
            calibration_decisions=calibration_decisions,
        )

    # ── Collect: conditional TZ-calibration ask (spec/52 §8.2) ─────────────

    def _collect_run_tz_calibration(
        self, *, event_id, event_name, source_root, scan,
        edited_rows, existing_days,
    ):
        """spec/52 §8.2 — conditional TZ calibration before the ingest gate.

        Reuses the legacy per-TZ flow (the ported, tested
        :class:`PastPhotosCamerasDialog`): for each distinct trip TZ in
        the merged plan that's NOT the home TZ AND has non-phone cameras
        present AND isn't already calibrated, open the dialog once with
        the cameras and days that share that TZ. The user picks each
        camera's TZ (Path A — direct pick) or pair-picks against the
        phone (Path B — same-moment photo compare); that ONE answer per
        camera then applies to every day sharing that TZ.

        Returns:
            * ``dict`` of ``(camera_id, event_day_number) -> tz_minutes``
              (possibly empty) — pass straight into the ingest gate.
            * ``None`` — user cancelled a per-TZ dialog AND chose to abort
              the whole Collect from the follow-up prompt.

        Persistence to ``camera_day_tz`` is intentionally deferred to
        :meth:`_record_collect_in_event_db` where it runs inside the same
        transaction that upserts the FK-parent ``camera`` and ``trip_day``
        rows — writing here would FK-fail when the camera doesn't exist
        in event.db yet.
        """
        from PyQt6.QtWidgets import QMessageBox
        from core.discrete_tz import nearest_valid_offset
        from mira.ui.pages.past_photos_cameras import (
            PastPhotosCamerasDialog,
        )

        # 1. Home TZ from settings. If unset, can't compute the trigger.
        _, home_tz_minutes = self._home_defaults_from_settings()
        if home_tz_minutes is None:
            log.warning("Collect TZ-calibration: home_tz_minutes unset; skipping ask.")
            return {}

        # 2. Translate scan-space day_numbers to event-space (existing days
        # keep their day_numbers; new checked days get max+1, +2, …).
        # camera_day_tz FKs to trip_day(day_number), so persistence + lookup
        # must use event-space all the way through.
        existing_day_nums = {}
        for td in existing_days:
            d = self._safe_date(td.date)
            if d is not None:
                existing_day_nums[d] = td.day_number
        date_to_event_day_num = dict(existing_day_nums)
        max_n = max(date_to_event_day_num.values(), default=0)
        for row in sorted(edited_rows, key=lambda r: r.date):
            if row.date in date_to_event_day_num or not row.checked:
                continue
            max_n += 1
            date_to_event_day_num[row.date] = max_n

        # 3. Group event-day-numbers by their TZ (in minutes east-of-UTC).
        # Only checked days with a known TZ contribute. Order: TZ first-
        # seen in plan order so "Step 1 of N" starts with the earliest day.
        tz_to_event_days: Dict[int, List[int]] = {}
        for row in sorted(edited_rows, key=lambda r: r.date):
            if not row.checked or row.tz_minutes is None:
                continue
            ev_day = date_to_event_day_num.get(row.date)
            if ev_day is None:
                continue
            tz_to_event_days.setdefault(row.tz_minutes, []).append(ev_day)

        # 4. Per-TZ camera presence map. Non-phone cameras only — phones
        # carry TZ in EXIF, no calibration needed. Phone reference (for
        # the SyncPairPickerDialog) added per-TZ so PastPhotosCamerasDialog
        # auto-detects it via the ``_looks_like_phone`` substring match.
        phone_camera_ids: set[str] = set()
        for sp in scan.presences:
            if sp.is_phone:
                phone_camera_ids.add(sp.camera_id)
        # Pick one phone reference (alphabetical) — None means no phones
        # in the scan; calibration still works (Path A only).
        phone_reference: Optional[str] = (
            sorted(phone_camera_ids)[0] if phone_camera_ids else None
        )

        tz_to_non_phone_cameras: Dict[int, set] = {}
        for sp in scan.presences:
            if sp.is_phone:
                continue
            scan_date = scan.day_date_lookup.get(sp.day_number)
            if scan_date is None:
                continue
            ev_day = date_to_event_day_num.get(scan_date)
            if ev_day is None:
                continue
            for tz_min, days in tz_to_event_days.items():
                if ev_day in days:
                    tz_to_non_phone_cameras.setdefault(tz_min, set()).add(
                        sp.camera_id
                    )
                    break

        # 5. Candidate TZs: not the home TZ, has at least one non-phone
        # camera, and not every (camera, day_in_this_tz) is already
        # calibrated in event.db.
        try:
            eg = self.gateway.open_event(event_id)
            try:
                def _all_calibrated(cams: set, days: list) -> bool:
                    for cam in cams:
                        for d in days:
                            if eg.camera_day_tz(cam, d) is None:
                                return False
                    return True
                candidate_tzs: List[int] = []
                for tz_min, days in tz_to_event_days.items():
                    if tz_min == home_tz_minutes:
                        continue
                    cams = tz_to_non_phone_cameras.get(tz_min, set())
                    if not cams:
                        continue
                    if _all_calibrated(cams, days):
                        continue
                    candidate_tzs.append(tz_min)
            finally:
                eg.close()
        except Exception:                                       # noqa: BLE001
            log.exception("Collect TZ-calibration: existing offsets read failed.")
            candidate_tzs = [
                tz for tz, _days in tz_to_event_days.items()
                if tz != home_tz_minutes and tz_to_non_phone_cameras.get(tz)
            ]

        if not candidate_tzs:
            return {}

        # 6. "Calibrate / Skip" entry dialog.
        cam_count = len({
            cam
            for tz in candidate_tzs
            for cam in tz_to_non_phone_cameras.get(tz, set())
        })
        ask = QMessageBox(self)
        ask.setWindowTitle(tr("Calibrate camera timezones?"))
        ask.setIcon(QMessageBox.Icon.NoIcon)
        ask.setText(tr(
            "{name} has {n} trip timezone(s) different from your home "
            "timezone, and {c} camera(s) need a declared timezone for "
            "those days so capture times can be corrected.\n\n"
            "Calibrate now, or skip and continue?"
        )
        .replace("{name}", event_name)
        .replace("{n}", str(len(candidate_tzs)))
        .replace("{c}", str(cam_count)))
        cal_btn = ask.addButton(tr("Calibrate"), QMessageBox.ButtonRole.AcceptRole)
        ask.addButton(tr("Skip"), QMessageBox.ButtonRole.RejectRole)
        ask.setDefaultButton(cal_btn)
        ask.exec()
        if ask.clickedButton() is not cal_btn:
            return {}                                           # skipped — soft path

        # 7. Picker factory for Path B (sync pair). Replaces the legacy
        # QFileDialog with the dedicated :class:`CollectPhotoPicker`
        # filtered by camera_id (Nelson 2026-06-09 — the flat-scan source
        # layout has no per-camera subfolders for QFileDialog to scope
        # to, so the file-dialog approach is unusable in this flow).
        from mira.ui.pages.collect_photo_picker import (
            CollectPhotoPicker,
        )

        # Pre-build the per-date plan labels — same data the user sees on
        # the Days Table dialog (Day N · date · location · description) so
        # the picker's day list is immediately relatable. Reused across every
        # camera the user pair-picks; the lookup costs once for the whole
        # calibration session.
        day_labels: Dict[date, str] = {}
        for row in edited_rows:
            if not row.checked:
                continue
            ev_day = date_to_event_day_num.get(row.date)
            bits: List[str] = []
            if ev_day is not None:
                bits.append(
                    tr("Day {n}").replace("{n}", str(ev_day))
                )
            bits.append(row.date.isoformat())
            if row.location:
                bits.append(row.location)
            head = " · ".join(bits)
            if row.description:
                day_labels[row.date] = f"{head}\n{row.description}"
            else:
                day_labels[row.date] = head

        def _picker_factory(target_camera_id: str):
            # Group all scan records for ``target_camera_id`` by their
            # corrected day_date. Untimestamped records are skipped —
            # they can't anchor a pair-pick. Done lazily per camera so
            # we only pay the grouping cost when the user actually opens
            # the picker for that camera.
            photos_by_day: Dict[date, List[Path]] = {}
            for rec in scan.per_photo_records:
                if rec.camera_id != target_camera_id:
                    continue
                if rec.day_number is None:
                    continue
                d = scan.day_date_lookup.get(rec.day_number)
                if d is None:
                    continue
                photos_by_day.setdefault(d, []).append(rec.source_path)

            def _callback(parent_widget):
                dlg = CollectPhotoPicker(
                    camera_id=target_camera_id,
                    photos_by_day=photos_by_day,
                    day_labels=day_labels,
                    parent=parent_widget,
                )
                if dlg.exec() == QDialog.DialogCode.Accepted:
                    chosen = dlg.selected_path
                    dlg.deleteLater()
                    return chosen
                dlg.deleteLater()
                return None

            return _callback

        # 8. Per-TZ loop. PastPhotosCamerasDialog gets the cameras + days
        # for ONE TZ at a time; one answer per camera applies to every
        # day in that TZ. Phones are EXCLUDED from camera_ids — they
        # serve only as the pair-pick reference and never need
        # calibration themselves (Nelson 2026-06-09).
        calibration_decisions: Dict[Tuple[str, int], int] = {}
        total_steps = len(candidate_tzs)
        for step_i, tz_min in enumerate(candidate_tzs, start=1):
            cams_for_tz = sorted(tz_to_non_phone_cameras.get(tz_min, set()))
            days_for_tz = sorted(tz_to_event_days[tz_min])

            dlg = PastPhotosCamerasDialog(
                camera_ids=cams_for_tz,
                root_dir=str(source_root),
                trip_tz=tz_min / 60.0,
                ordinal=(step_i, total_steps),
                day_numbers=days_for_tz,
                parent=self,
                phone_reference_id=phone_reference,
                picker_factory=_picker_factory if phone_reference else None,
            )
            accepted = dlg.exec() == QDialog.DialogCode.Accepted
            per_camera = dlg.per_camera() if accepted else {}
            reference_id = dlg.reference_id
            dlg.deleteLater()

            if not accepted:
                # Cancel on a per-TZ dialog — ask Nelson how to proceed.
                prompt = QMessageBox(self)
                prompt.setWindowTitle(tr("Calibration cancelled"))
                prompt.setIcon(QMessageBox.Icon.NoIcon)
                prompt.setText(tr(
                    "You cancelled the camera-timezone calibration on "
                    "step {step} of {total}.\n\n"
                    "Abort the entire Collect, or continue copying "
                    "photos with the calibration done so far?"
                )
                .replace("{step}", str(step_i))
                .replace("{total}", str(total_steps)))
                abort_btn = prompt.addButton(
                    tr("Abort Collect"),
                    QMessageBox.ButtonRole.DestructiveRole,
                )
                continue_btn = prompt.addButton(
                    tr("Continue with partial calibration"),
                    QMessageBox.ButtonRole.AcceptRole,
                )
                prompt.setDefaultButton(continue_btn)
                prompt.exec()
                if prompt.clickedButton() is abort_btn:
                    return None
                break                                           # use decisions so far

            # Convert each per-camera answer into per-(camera, day) entries
            # for every day in this TZ. Reference camera (the phone) is
            # skipped — phones don't need camera_day_tz (their EXIF carries
            # OffsetTimeOriginal). Non-reference cams in "know" mode use
            # configured_tz; "unknown" mode uses the pair's offset.
            for cam_id, info in per_camera.items():
                if cam_id == reference_id:
                    continue                                    # phone — skip
                cam_tz_min: Optional[int] = None
                if info.get("mode") == "know":
                    cfg = info.get("configured_tz")
                    if cfg is not None:
                        cam_tz_min = int(round(float(cfg) * 60))
                else:
                    pair = info.get("pair")
                    if pair is not None:
                        delta_min = int(round(
                            (pair.reference_time
                             - pair.camera_time).total_seconds() / 60.0
                        ))
                        cam_tz_min = tz_min - delta_min
                if cam_tz_min is None:
                    continue
                snapped = nearest_valid_offset(cam_tz_min)
                if snapped is None:
                    continue
                for d in days_for_tz:
                    calibration_decisions[(cam_id, d)] = snapped

        return calibration_decisions

    # ── Collect: ingest-mode gate + Copy-all end-to-end ───────────────────

    def _open_collect_ingest_gate(
        self, *, event_id, event_name, event_root, scan,
        edited_rows, edited_info, existing_info, existing_days,
        calibration_decisions=None, offer_quick_sweep=True,
        post_record=None, land_phase=None,
    ) -> bool:
        """Small modal after Collect's plan capture with 3 buttons:
        Copy all / Quick Sweep first / Cancel (Nelson 2026-06-08).

        Returns True when an ingest was *enqueued* on the shared batch
        queue (spec/84 — the success tail runs later in
        :meth:`_finish_collect_ingest`); False on every cancel/abort
        path AND on the "nothing to import" pre-flight bail. The
        backfill wizard (spec/57 §4.3.1) uses False to fall back to a
        dashboard landing immediately; True callers leave the
        navigation to the queue's ``finished_result``.
        ``offer_quick_sweep=False`` drops the sweep button (the wizard's
        pre-filtered picked/edited levels). ``post_record`` and
        ``land_phase`` are forwarded to :meth:`_run_collect_copy_all`."""
        from PyQt6.QtWidgets import QMessageBox

        # Photo + day counters for the gate's headline.
        checked_dates = {r.date for r in edited_rows if r.checked}
        n_photos = sum(
            1 for rec in scan.per_photo_records
            if rec.day_number is not None
            and scan.day_date_lookup.get(rec.day_number) in checked_dates
        )
        n_days = len(checked_dates)
        n_quar = sum(
            1 for rec in scan.per_photo_records if rec.day_number is None
        )
        quar_line = ""
        if n_quar:
            quar_line = tr(
                "\n• {q} file(s) without EXIF date go to a quarantine subfolder."
            ).replace("{q}", str(n_quar))

        msg = QMessageBox(self)
        msg.setWindowTitle(tr("Ready to import"))
        msg.setIcon(QMessageBox.Icon.NoIcon)
        msg.setText(tr(
            "{n} photo(s) across {d} day(s) ready to copy into {name}.{quar}\n\n"
            "Pick how to proceed:"
        )
        .replace("{n}", str(n_photos))
        .replace("{d}", str(n_days))
        .replace("{name}", event_name)
        .replace("{quar}", quar_line))
        copy_btn = msg.addButton(tr("Copy all"), QMessageBox.ButtonRole.AcceptRole)
        sweep_btn = None
        if offer_quick_sweep:
            sweep_btn = msg.addButton(
                tr("Quick Sweep first…"), QMessageBox.ButtonRole.AcceptRole)
        cancel_btn = msg.addButton(QMessageBox.StandardButton.Cancel)
        msg.setDefaultButton(copy_btn)
        msg.exec()
        clicked = msg.clickedButton()

        if clicked is cancel_btn or clicked is None:
            return False
        if sweep_btn is not None and clicked is sweep_btn:
            kept = self._run_quick_sweep_first(scan=scan, edited_rows=edited_rows)
            if kept is None:
                return False                                    # Cancel — no-op
            if not kept:
                nothing = QMessageBox(self)
                nothing.setWindowTitle(tr("Nothing picked"))
                nothing.setIcon(QMessageBox.Icon.NoIcon)
                nothing.setStandardButtons(QMessageBox.StandardButton.Ok)
                nothing.setText(tr(
                    "You skipped every photo in Quick Sweep — nothing was copied."
                ))
                nothing.exec()
                return False
            return self._run_collect_copy_all(
                event_id=event_id, event_root=event_root, scan=scan,
                edited_rows=edited_rows, edited_info=edited_info,
                existing_info=existing_info, existing_days=existing_days,
                keep_only_paths=kept,
                calibration_decisions=calibration_decisions,
                post_record=post_record,
                land_phase=land_phase,
            )

        # Copy all → real ingest.
        return self._run_collect_copy_all(
            event_id=event_id, event_root=event_root, scan=scan,
            edited_rows=edited_rows, edited_info=edited_info,
            existing_info=existing_info, existing_days=existing_days,
            calibration_decisions=calibration_decisions,
            post_record=post_record,
            land_phase=land_phase,
        )

    def _run_quick_sweep_first(self, *, scan, edited_rows):
        """Host the redesigned Quick Sweep route — DaysListsPage →
        DaysGridPage → QuickSweepPage viewer — modally over the about-
        to-be-imported set (Nelson 2026-06-08 — Collect ingest-mode
        gate; Nelson 2026-06-14 — wizard QS now uses the same days-
        list + days-grid route the standalone path does, instead of
        the flat leaf-only modal).

        Builds :class:`SourceItem` instances from the scan's per-photo
        records, filtered to **checked days only** + the quarantine
        bucket (untimestamped files always travel through so the user
        can still triage them). Returns the kept paths set, or
        ``None`` if the user backed out without confirming the import.
        """
        from PyQt6.QtWidgets import (
            QDialog, QMessageBox, QStackedWidget, QVBoxLayout,
        )
        from core.cull_state import (
            STATE_CANDIDATE as _C,
            STATE_DISCARDED as _D,
            STATE_KEPT as _K,
        )
        from core.fresh_source import SourceItem
        from mira.picked.quick_sweep_buckets import build_fast_days
        from mira.ui.pages.days_grid_page import DaysGridPage
        from mira.ui.pages.days_lists_page import DaysListsPage
        from mira.ui.pages.quick_sweep_page import QuickSweepPage

        # ── Items: checked days only + quarantine ─────────────────
        checked_dates = {r.date for r in edited_rows if r.checked}
        items = []
        for rec in scan.per_photo_records:
            day_date = (
                scan.day_date_lookup.get(rec.day_number)
                if rec.day_number is not None else None
            )
            if day_date is not None and day_date not in checked_dates:
                continue
            items.append(SourceItem(
                path=rec.source_path,
                timestamp=rec.capture_time_raw,
                camera_id=rec.camera_id or "",
            ))
        if not items:
            return set()

        # ── Ledger pre-populated with the QS default ──────────────
        default_state = self._qs_default_legacy_state()
        state_ledger: dict[Path, str] = {
            it.path: default_state for it in items
        }
        days = build_fast_days(
            items, state_for=lambda p: state_ledger.get(p, default_state))
        if not days:
            return set()

        # Sort each day chronologically so the grid order matches the
        # viewer's own timestamp sort (see _open_quick_sweep_standalone
        # for the same rule).
        items_by_day: dict[int, list] = {}
        for day in days:
            wanted = {
                Path(ci.item_id)
                for b in day.buckets for ci in b.items
            }
            items_by_day[day.day_number] = sorted(
                (it for it in items if it.path in wanted),
                key=lambda it: (
                    it.timestamp is None,
                    it.timestamp.isoformat()
                    if it.timestamp is not None else "",
                    it.path.name,
                ),
            )

        # The QS session helpers (`_qs_build_day_snapshots`,
        # `_qs_build_grid_items`) read from ``self._quick_sweep``. The
        # modal borrows that contract for the duration of ``exec()``;
        # cleared in the ``finally`` even on an exception so a partial
        # wizard QS never leaves a phantom session behind.
        prior_session = self._quick_sweep
        self._quick_sweep = {
            "mode": "wizard",
            "dest": None,
            "event_id": None,
            "state": state_ledger,
            "default": default_state,
            "items_by_day": items_by_day,
            "days": days,
            "current_day": None,
            "current_day_items": [],
        }

        # ── Modal host with internal 3-page stack ────────────────
        host = QDialog(self)
        host.setWindowTitle(tr("Quick Sweep — pick what to import"))
        host.setModal(True)
        host.resize(1280, 800)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        stack = QStackedWidget()
        layout.addWidget(stack)

        # Paths-mode pages: no gateway, no main-window signal wiring —
        # the closures below own the navigation locally.
        lists_page = DaysListsPage()
        grid_page = DaysGridPage()
        viewer_page = QuickSweepPage()
        # spec/71 — the modal wizard route is a Collect-phase Quick
        # Sweep; the shared widgets read Collect/blue under it.
        lists_page.set_phase_identity("collect")
        grid_page.set_phase_identity("collect")
        stack.addWidget(lists_page)
        stack.addWidget(grid_page)
        stack.addWidget(viewer_page)

        result = {"kept": None}

        def render_lists() -> None:
            snapshots = self._qs_build_day_snapshots(days)
            lists_page.setEventForPreview(
                tr("Quick Sweep — {n} day(s)").replace(
                    "{n}", str(len(days))),
                snapshots,
            )
            stack.setCurrentWidget(lists_page)
            lists_page.setFocus()

        def open_day(day_number: int) -> None:
            day_items = items_by_day.get(day_number, [])
            self._quick_sweep["current_day"] = day_number
            self._quick_sweep["current_day_items"] = day_items
            snapshots = self._qs_build_day_snapshots(days)
            snap = next(
                (s for s in snapshots if s.day_number == day_number),
                None,
            )
            title = snap.title if snap is not None else f"Day {day_number}"
            date_iso = snap.date_iso if snap is not None else ""
            grid_items = self._qs_build_grid_items(day_number)
            grid_page.set_paths_mode_callbacks(
                state_lookup=self._qs_lookup_thumb_state,
                day_rebuild=lambda: self._qs_build_grid_items(day_number),
            )
            grid_page.setDay(day_number, title, date_iso, grid_items)
            stack.setCurrentWidget(grid_page)
            grid_page.setFocus()

        def open_viewer(item_id: str) -> None:
            day_items = self._quick_sweep["current_day_items"]
            if not day_items:
                return
            target = Path(item_id)
            start_idx = next(
                (i for i, it in enumerate(day_items)
                 if it.path == target),
                0,
            )
            viewer_page.load(
                day_items, start_index=start_idx, state=state_ledger)
            stack.setCurrentWidget(viewer_page)
            viewer_page.setFocus()

        def back_to_grid() -> None:
            day_number = self._quick_sweep.get("current_day")
            if day_number is not None:
                open_day(day_number)
            else:
                render_lists()

        def finalize() -> None:
            kept_set = {
                p for p, s in state_ledger.items() if s in (_K, _C)
            }
            skipped = sum(
                1 for s in state_ledger.values() if s == _D)
            total = len(state_ledger)
            box = QMessageBox(host)
            box.setIcon(QMessageBox.Icon.NoIcon)
            box.setWindowTitle(tr("Finish Quick Sweep"))
            box.setText(tr("Quick Sweep — ready to import."))
            bits = [
                tr("{n} of {total} item(s) will be imported.")
                .replace("{n}", str(len(kept_set)))
                .replace("{total}", str(total)),
            ]
            if skipped:
                bits.append(
                    tr("{n} discarded item(s) will not be imported.")
                    .replace("{n}", str(skipped)))
            bits.append("")
            bits.append(tr("Import and finish?"))
            box.setInformativeText("\n".join(bits))
            import_btn = box.addButton(
                tr("Import and finish"),
                QMessageBox.ButtonRole.AcceptRole)
            box.addButton(
                tr("Stay in Quick Sweep"),
                QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(import_btn)
            box.exec()
            if box.clickedButton() is import_btn:
                result["kept"] = kept_set
                host.accept()

        lists_page.back_requested.connect(finalize)
        lists_page.day_activated.connect(open_day)
        grid_page.back_requested.connect(render_lists)
        grid_page.item_activated.connect(open_viewer)
        viewer_page.saved.connect(lambda _kept: back_to_grid())
        viewer_page.cancelled.connect(back_to_grid)

        try:
            render_lists()
            host.exec()
        finally:
            self._quick_sweep = prior_session

        return result["kept"]

    def _run_collect_copy_all(
        self, *, event_id, event_root, scan,
        edited_rows, edited_info, existing_info, existing_days,
        keep_only_paths=None, calibration_decisions=None,
        post_record=None, land_phase=None,
    ) -> bool:
        """End-to-end Copy-all ingest — enqueue + return immediately
        (spec/84):

        1. Persist event-info edits (if changed).
        2. Assign day_numbers (existing keep theirs; fresh get max+1, +2, …).
        3. Build IngestPhotoJob list — only checked dates' photos.
        4. Wrap the copy as an :class:`IngestJob` and enqueue it on the
           shared :attr:`batch_queue`. OK returns IMMEDIATELY; the copy
           runs on the job's QThread, the progress line shows it, and
           Step 5 runs in :meth:`_finish_collect_ingest` on the UI
           thread once the queue's ``finished_result`` fires
           (spec/84 §3 — one SQLite connection per thread).
        5. (Deferred to ``_finish_collect_ingest`` on the UI thread.)
           Write cameras, trip_days, and items to event.db; call
           ``post_record()`` (the backfill wizard's level state writes,
           spec/57 §4.3); refresh dashboards + spec/82 day-add snapshot;
           navigate to the event — then straight to ``land_phase``'s
           surface when given (the backfill wizard's landing, spec/57
           §4.3).

        Returns True when the ingest was *enqueued* (the success tail
        will run when the queue finishes the job); False on every
        pre-flight abort — no jobs to ingest, info-persist failure.
        Callers that need to land the user somewhere on False (the
        backfill wizard) still do so immediately; True callers leave
        the navigation to the queue's ``finished_result``.

        ``calibration_decisions`` is the per-``(camera_id, event_day_number)``
        declared TZ map from :meth:`_collect_run_tz_calibration`. The bake
        formula (spec/45 TZ-3, DiscreteTzDialog docstring) is
        ``corrected = raw + (day_tz - declared_tz)``; absent decisions
        fall back to ``corrected = raw``.
        """
        from datetime import date as _date, timedelta
        from pathlib import Path
        from PyQt6.QtWidgets import QMessageBox
        from core.ingest_pipeline import IngestPhotoJob, destination_for, run_ingest
        from mira.ui.ingest.ingest_job import IngestJob
        from mira.ui.shell.batch_queue import JOB_TYPE_IMPORT

        calibration_decisions = dict(calibration_decisions or {})

        # 0. spec/84 §4 — block a second concurrent ingest for the same
        # event. The queue serialises across events; this gate protects
        # against the same user accidentally double-enqueuing one event
        # from two paths (Quick Sweep ⇆ Copy-all, dashboard ⇆ wizard).
        if self.is_ingesting(event_id):
            QMessageBox.warning(
                self, tr("Already importing"),
                tr("This event is still importing — wait for it to "
                   "finish before queuing another copy."),
            )
            return False

        # 1. Persist info edits if the user changed anything (skip the
        # 'name' key — folder rename is a separate, future operation).
        if {k: edited_info.get(k) for k in edited_info if k != "name"} \
                != {k: existing_info.get(k) for k in existing_info if k != "name"}:
            try:
                self.gateway.set_classification(
                    event_id,
                    event_type=edited_info.get("event_type"),
                    event_subtype=edited_info.get("event_subtype") or "",
                    description=edited_info.get("description") or "",
                    duration_value=edited_info.get("duration_value") or 0,
                    duration_unit=edited_info.get("duration_unit") or "",
                    participants=edited_info.get("participants") or [],
                    # spec/64: Context / Experience Type / Creative Focus
                    # arrive via the EventHeaderDialog (slice 2+).
                )
            except Exception:                                   # noqa: BLE001
                log.exception("Collect: set_classification failed for %s", event_id)
                return False

        # 2. Day-number assignment.
        existing_day_nums = {}
        for td in existing_days:
            d = self._safe_date(td.date)
            if d is not None:
                existing_day_nums[d] = td.day_number
        date_to_day_num = dict(existing_day_nums)
        max_n = max(date_to_day_num.values(), default=0)
        for row in sorted(edited_rows, key=lambda r: r.date):
            if row.date in date_to_day_num or not row.checked:
                continue
            max_n += 1
            date_to_day_num[row.date] = max_n

        checked_dates = {r.date for r in edited_rows if r.checked}
        date_to_row = {r.date: r for r in edited_rows}

        # 3. IngestPhotoJob list. Unchecked-day photos are skipped;
        # untimestamped photos go to the quarantine path regardless
        # (the user can't make a per-day decision on a file we couldn't
        # read). When ``keep_only_paths`` is set (Quick Sweep path), any
        # photo not in the kept set is dropped — applies to quarantine
        # too, so the user can prune stripped-EXIF junk.
        jobs: List[IngestPhotoJob] = []
        for rec in scan.per_photo_records:
            if keep_only_paths is not None and rec.source_path not in keep_only_paths:
                continue
            if rec.day_number is None or rec.capture_time_raw is None:
                jobs.append(IngestPhotoJob(
                    source_path=rec.source_path,
                    camera_id=rec.camera_id,
                    is_phone=rec.is_phone,
                    day_number=0,
                    day_date=None,
                    day_description="",
                    capture_time_raw=None,
                    capture_time_corrected=None,
                ))
                continue
            day_date = scan.day_date_lookup.get(rec.day_number)
            if day_date not in checked_dates:
                continue
            row = date_to_row[day_date]
            event_day_num = date_to_day_num[day_date]
            # Per-(camera, event_day) declared TZ → corrected time per spec/52
            # §8.1 + DiscreteTzDialog docstring. Phones are already correct
            # (their EXIF carries OffsetTimeOriginal); uncalibrated cameras
            # bake at raw (no shift).
            key = (rec.camera_id, event_day_num)
            if key in calibration_decisions and row.tz_minutes is not None:
                shift_min = row.tz_minutes - calibration_decisions[key]
                capture_time_corrected = (
                    rec.capture_time_raw + timedelta(minutes=shift_min)
                )
            else:
                capture_time_corrected = rec.capture_time_raw
            jobs.append(IngestPhotoJob(
                source_path=rec.source_path,
                camera_id=rec.camera_id,
                is_phone=rec.is_phone,
                day_number=event_day_num,
                day_date=day_date,
                day_description=row.description or "",
                capture_time_raw=rec.capture_time_raw,
                capture_time_corrected=capture_time_corrected,
            ))

        if not jobs:
            QMessageBox.information(
                self, tr("Nothing to import"),
                tr("All scanned days were unchecked — nothing was copied."),
            )
            return False

        # 4. Enqueue the copy as a background ingest job on the shared
        # batch queue (spec/84). OK returns IMMEDIATELY; the copy runs
        # on the IngestJob's QThread, per-file progress flows through
        # the under-menubar progress line, and the `item` rows are
        # written on the UI thread in ``_finish_collect_ingest`` once
        # the job emits ``finished_result`` (spec/84 §3 — one SQLite
        # connection per thread; the copy thread never writes event.db).
        # ``bake_corrections=False`` — CLAUDE.md invariant #7 + spec/52
        # §8.1: the captured tree is NEVER mutated. TZ correction is
        # correction-on-read via item.capture_time_corrected in event.db,
        # NOT via EXIF rewrite in the copies. The legacy bake step
        # (still the default in ``run_ingest``) is a pre-rebuild holdover.
        # Skipping it also halves the import wall-clock (Nelson 2026-06-09).
        def _copy_work(progress_cb, should_cancel):
            def _engine_progress(message, current=0, total=0):
                progress_cb(int(current), int(total), str(message))
            # spec/84 slice 5 will thread ``should_cancel`` into
            # ``run_ingest``; until then a Cancel mid-copy is observed
            # by the wrapper only AT the end (the copy still runs to
            # completion), and the result carries ``cancelled=True``.
            return run_ingest(
                jobs, event_root,
                bake_corrections=False, progress=_engine_progress,
            )

        def _on_done(result):
            self._finish_collect_ingest(
                result=result,
                event_id=event_id, event_root=event_root,
                jobs=jobs, edited_rows=edited_rows,
                date_to_day_num=date_to_day_num,
                existing_day_nums=existing_day_nums,
                calibration_decisions=calibration_decisions,
                post_record=post_record, land_phase=land_phase,
            )

        job = IngestJob(_copy_work)
        # The line owns the verb ("Importing"); the label is the
        # descriptive tail. Event-name + count is the same pattern the
        # export caller uses.
        label = tr("{name} — {n} file(s)") \
            .replace("{name}", event_root.name) \
            .replace("{n}", str(len(jobs)))
        job.finished.connect(job.deleteLater)
        # spec/84 §5 — flag this event as "ingest in progress" so the
        # Events screen hides its tile, Pick rejects entry, and the
        # second-enqueue gate fires for a duplicate attempt. The flag
        # clears in ``_mark_ingest_finished`` from
        # :meth:`_finish_collect_ingest`.
        self._mark_ingest_started(event_id)
        self.batch_queue.enqueue(
            job, label, _on_done, job_type=JOB_TYPE_IMPORT)
        log.info(
            "Collect: enqueued ingest job for %s (%d file(s)); UI returns",
            event_id, len(jobs))
        return True

    # ── spec/84 §5 — per-event ingest-in-progress bookkeeping ───────

    def is_ingesting(self, event_id: str) -> bool:
        """True while an ingest for ``event_id`` is sitting on the
        shared queue (queued or running). Drives the second-enqueue
        gate (:meth:`_run_collect_copy_all`), the Pick-entry guard
        (:meth:`_on_phase_activated`), and the Events-screen tile
        hide (:class:`EventsPage` filter)."""
        return event_id in self._ingesting_event_ids

    def _mark_ingest_started(self, event_id: str) -> None:
        self._ingesting_event_ids.add(event_id)
        self._notify_ingest_in_progress_changed()

    def _mark_ingest_finished(self, event_id: str) -> None:
        self._ingesting_event_ids.discard(event_id)
        self._notify_ingest_in_progress_changed()

    def _notify_ingest_in_progress_changed(self) -> None:
        """Push the current in-progress set to surfaces that filter on
        it. Today: the Events screen. Future: Days Lists / Phases tile
        badges could read the same set."""
        try:
            self.events_page.set_ingest_in_progress_ids(
                self._ingesting_event_ids)
        except AttributeError:
            # Older EventsPage build (placeholder, preview path) without
            # the setter; safe to ignore — only the live tile filter
            # is affected.
            pass

    def _finish_collect_ingest(
        self, *, result, event_id, event_root, jobs, edited_rows,
        date_to_day_num, existing_day_nums, calibration_decisions,
        post_record, land_phase,
    ) -> None:
        """spec/84 §3 — Collect-OK ingest's UI-thread tail.

        The batch queue runs this on the UI thread once the
        :class:`IngestJob` emits ``finished_result``. Writes ``item``
        rows + cameras + trip_days against the gateway (one SQLite
        connection per thread — the copy thread never touches event.db),
        fires the spec/82 day-add milestone snapshot, refreshes the
        Events index, and surfaces the result + navigation tail the
        user expects when the import finishes.

        Crash inside the worker → log + warn dialog, no DB writes. A
        partial cancel still writes whatever copied so far; spec/57's
        re-run-resumes rule picks up the remainder on the next attempt.
        """
        from PyQt6.QtWidgets import QMessageBox

        # Either path below MUST clear the "ingest in progress" flag
        # before returning — leaving it set would strand the tile and
        # keep blocking re-entry. Wrap the body so even an unexpected
        # raise inside it finally clears the flag.
        try:
            if result.error is not None:
                log.error("Collect ingest crashed: %s", result.error)
                QMessageBox.critical(
                    self, tr("Import failed"),
                    tr("The import crashed:\n\n{err}")
                    .replace("{err}", str(result.error)),
                )
                return

            ingest_result = result.payload
            if ingest_result is None:
                # No payload but no error either — defensive guard; the
                # job contract returns the engine's IngestResult unless
                # it crashed (which would have set ``error``).
                log.warning(
                    "Collect ingest finished with no payload (cancelled=%s)",
                    result.cancelled)
                return

            # spec/84 §5 + spec/57 §4.3.1 — zero-media cancel = clean
            # no-op. No file made it into Original Media/, so the event
            # never effectively existed; drop the record + the (empty)
            # folder so the user sees the same state they had before
            # OK.
            zero_media = (
                int(getattr(ingest_result, "photos_copied", 0)) == 0
                and int(getattr(ingest_result, "photos_quarantined", 0)) == 0
            )
            if result.cancelled and zero_media:
                log.info(
                    "Collect: zero-media cancel for %s → removing event "
                    "record + folder", event_id)
                try:
                    self.gateway.delete_event(
                        event_id, delete_files=True)
                except Exception:                            # noqa: BLE001
                    log.exception(
                        "zero-media cancel cleanup failed for %s",
                        event_id)
                # Even on cleanup failure refresh the screen — the index
                # may have removed the row.
                try:
                    self.events_page.refresh()
                except Exception:                            # noqa: BLE001
                    log.exception("events_page refresh failed")
                QMessageBox.information(
                    self, tr("Import cancelled"),
                    tr("No files were imported — the event was "
                       "removed."),
                )
                return

            self._record_collect_in_event_db(
                event_id=event_id, event_root=event_root,
                jobs=jobs, edited_rows=edited_rows,
                date_to_day_num=date_to_day_num,
                existing_day_nums=existing_day_nums,
                per_job_info=ingest_result.per_job_info,
                calibration_decisions=calibration_decisions,
                progress=None,
            )
            if post_record is not None:
                try:
                    post_record()
                except Exception:  # noqa: BLE001
                    log.exception(
                        "Collect: post_record failed for %s", event_id)

            # 6. Refresh + navigate. The classification pass rides every
            # ingest (spec/58 §1 — media sits in the system long before
            # Edit); quiet, off-thread, idempotent.
            self.gateway.refresh_index_entry(event_id)
            # spec/82 §A.1 — per-day-add milestone snapshot fires on the
            # *done* signal (spec/84 §5), not at OK. The trip workflow
            # is "add a day, decide on it; add the next day, decide on
            # it" — every added day is the natural rollback point.
            # Snapshot failures are logged + ignored (the gateway
            # helper never raises) so the import doesn't fail because
            # the backup volume hiccuped.
            snap = self.gateway.snapshot_event(
                event_id, reason="milestone")
            if snap is not None:
                log.info(
                    "spec/82 §A.1: per-day-add milestone snapshot "
                    "saved at %s", snap)
            self._spawn_classify_pass(event_id)
            warnings_line = ""
            if ingest_result.warnings:
                warnings_line = tr(
                    "\n\n{w} warning(s) — check the log."
                ).replace("{w}", str(len(ingest_result.warnings)))
            cancel_line = (
                tr(" · cancelled mid-run")
                if result.cancelled else "")
            ok_msg = QMessageBox(self)
            ok_msg.setWindowTitle(tr("Import complete"))
            ok_msg.setIcon(QMessageBox.Icon.NoIcon)
            ok_msg.setStandardButtons(QMessageBox.StandardButton.Ok)
            # Capture-time correction lives on
            # item.capture_time_corrected in event.db (spec/52 §8.1);
            # the original EXIF is never mutated, so the "EXIF time(s)
            # corrected" line that used to live here was misleading
            # (Nelson 2026-06-09).
            dup_line = ""
            if ingest_result.photos_duplicates:
                # Backfill sources often carry the same file in several
                # subtrees (legacy captured + selected copies) — say so
                # plainly instead of burying it in the log
                # (spec/57 §4.3).
                dup_line = tr(
                    " · {d} duplicate(s) ingested once"
                ).replace("{d}", str(ingest_result.photos_duplicates))
            ok_msg.setText(tr(
                "{copied} photo(s) copied · {quar} quarantined"
                "{dups}{cancel}{warns}"
            )
            .replace("{copied}", str(ingest_result.photos_copied))
            .replace("{quar}", str(ingest_result.photos_quarantined))
            .replace("{dups}", dup_line)
            .replace("{cancel}", cancel_line)
            .replace("{warns}", warnings_line))
            ok_msg.exec()
            self._on_event_created(event_id)
            if land_phase and self._current_event_id == event_id:
                # Backfill wizard landing (spec/57 §4.3) — straight to
                # the level's phase surface, the dashboard beneath for
                # Back.
                self._on_phase_activated(land_phase)
        finally:
            # spec/84 §5 — clear the per-event ingest-in-progress flag
            # in EVERY path (success / crash / partial-cancel / zero-
            # media-cleanup), so the tile reappears + Pick unlocks +
            # the second-enqueue gate releases.
            self._mark_ingest_finished(event_id)

    def _record_collect_in_event_db(
        self, *, event_id, event_root, jobs, edited_rows,
        date_to_day_num, existing_day_nums, per_job_info=None,
        calibration_decisions=None, progress=None,
    ):
        """Write cameras, trip_days, and items for a successful Copy-all
        Collect into ``event.db``. Idempotent on cameras + trip_days
        (upsert keyed on their primary keys); items get a fresh uuid per
        copied photo so re-running this would double-insert — but the
        whole flow is gated by the success of ``run_ingest`` so re-runs
        require a fresh scan.

        ``calibration_decisions`` is the per-(camera_id, event_day_number)
        TZ map from :meth:`_collect_run_tz_calibration`. Persisted into
        ``camera_day_tz`` rows AFTER the FK-parent ``camera`` and
        ``trip_day`` rows are upserted in this same transaction, and
        used to set per-item ``tz_offset_minutes`` / ``tz_source``
        (spec/52 §13 — item TZ columns aligned to camera_day_tz).

        ``progress`` follows ``ingest_pipeline.ProgressCallback`` shape
        (``message, current, total``) — emits per-item during the
        sha256 + insert loop so the host's progress dialog keeps
        repainting and Qt stays responsive (Nelson 2026-06-08)."""
        import hashlib
        import json as _json
        import uuid as _uuid
        from datetime import datetime, timezone
        from pathlib import Path
        from core.ingest_pipeline import destination_for
        from mira.store import models as m

        VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".mts"}
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        calibration_decisions = dict(calibration_decisions or {})

        eg = self.gateway.open_event(event_id)
        try:
            with eg.store.transaction():
                # Cameras — dedup against the existing set.
                existing_camera_ids = {c.camera_id for c in eg.cameras()}
                seen_jobs_per_camera = {}
                for job in jobs:
                    if job.camera_id and job.camera_id not in seen_jobs_per_camera:
                        seen_jobs_per_camera[job.camera_id] = job
                for cam_id, job in seen_jobs_per_camera.items():
                    if cam_id in existing_camera_ids:
                        continue
                    eg.store.upsert(m.Camera(
                        camera_id=cam_id,
                        is_phone=job.is_phone,
                    ))

                # Trip days — upsert all checked rows + any existing rows
                # the user edited (covered by the same upsert call since
                # day_number is the primary key).
                upserted_day_nums = set()
                for row in sorted(edited_rows, key=lambda r: r.date):
                    day_num = date_to_day_num.get(row.date)
                    if day_num is None:
                        continue
                    # Skip rows that are new AND unchecked — no point
                    # writing an empty placeholder day.
                    if row.date not in existing_day_nums and not row.checked:
                        continue
                    extras = (
                        _json.dumps({"country_code": row.country_code})
                        if row.country_code else "{}"
                    )
                    eg.store.upsert(m.TripDay(
                        day_number=day_num,
                        date=row.date.isoformat(),
                        description=row.description or "",
                        location=row.location or None,
                        tz_minutes=row.tz_minutes,
                        hidden=not row.checked,
                        extras_json=extras,
                    ))
                    upserted_day_nums.add(day_num)

                # camera_day_tz — persist each user-declared offset.
                # camera_day_tz FKs to camera + trip_day; the upserts above
                # guarantee the parent rows exist before this loop runs.
                # Filter defensively: a calibration decision whose camera or
                # day did NOT make it into the ingested set (e.g. user
                # skipped every photo for that camera/day in Quick Sweep)
                # would FK-fail. Drop it with a log rather than crash.
                upserted_camera_ids = (
                    existing_camera_ids | set(seen_jobs_per_camera.keys())
                )
                # Pre-read the full trip_day set ONCE — the previous
                # generator-in-a-loop pattern re-queried per iteration AND
                # exhausted on first hit. A pre-built set covers both
                # newly-upserted days and pre-existing ones (within the
                # current transaction's read view).
                known_day_nums = set(upserted_day_nums) | {
                    td.day_number for td in eg.trip_days()
                }
                for (cam_id, day_num), tz_min in calibration_decisions.items():
                    if cam_id not in upserted_camera_ids:
                        log.debug(
                            "Collect: dropping camera_day_tz for (%s, %s) — "
                            "camera not ingested.", cam_id, day_num,
                        )
                        continue
                    if day_num not in known_day_nums:
                        log.debug(
                            "Collect: dropping camera_day_tz for (%s, %s) — "
                            "day not ingested.", cam_id, day_num,
                        )
                        continue
                    # Critical: a stray exception here (FK violation, invalid
                    # offset, etc.) MUST NOT abort the whole transaction —
                    # that would drop every item.  Drop the row, keep going.
                    try:
                        eg.set_camera_day_tz(
                            cam_id, day_num,
                            tz_minutes=tz_min, source="user_declared",
                        )
                    except Exception:                       # noqa: BLE001
                        log.exception(
                            "Collect: camera_day_tz upsert failed for "
                            "(%s, %s, %s) — skipping that row.",
                            cam_id, day_num, tz_min,
                        )

                # Existing origin_relpaths — used to skip items already
                # recorded (re-running Collect on the same source folder,
                # or recovering from a partial run where the copy
                # succeeded but the DB write failed mid-transaction).
                existing_paths = {
                    r[0] for r in eg.store.conn.execute(
                        "SELECT origin_relpath FROM item "
                        "WHERE origin_relpath IS NOT NULL"
                    )
                }

                # Items — one row per successfully copied + not-yet-
                # recorded photo. sha256 + byte_size come from
                # ``ingest_pipeline``'s per-job cache (computed during
                # the copy stream) so this loop never re-reads files
                # from disk; the DB upserts run fast and the second
                # progress "pass" the user used to see (Nelson eyeball
                # 2026-06-08) collapses into a few milliseconds.
                per_job_info = per_job_info or {}
                planned_inserts = []
                planned_rels = set()
                for j in jobs:
                    outcome = per_job_info.get(j.source_path)
                    if outcome is None:
                        continue                                # copy failed
                    rel = str(outcome.destination.relative_to(event_root)).replace("\\", "/")
                    if rel in existing_paths:
                        continue
                    if rel in planned_rels:
                        # run_ingest dedups/diverts same-destination jobs
                        # now, so two jobs reporting one destination should
                        # be impossible — if it ever recurs, drop the row
                        # instead of aborting the whole recording
                        # transaction on item.origin_relpath UNIQUE.
                        log.warning(
                            "Collect: duplicate destination %s reported by "
                            "two jobs — recording once.", rel)
                        continue
                    planned_rels.add(rel)
                    planned_inserts.append((j, outcome, rel))
                items_total = len(planned_inserts)
                if progress is not None:
                    progress(
                        tr("Recording {n} item(s) in the event database…")
                        .replace("{n}", str(items_total)),
                        0, items_total,
                    )
                items_done = 0
                skipped_unrecordable: list[str] = []
                unknown_camera_ensured = False
                for job, outcome, origin_relpath in planned_inserts:
                    dest = outcome.destination
                    sha = outcome.sha256
                    byte_size = outcome.byte_size
                    items_done += 1
                    # Schema CHECK: a 'captured' item REQUIRES camera_id +
                    # capture_time_raw (spec/30). A job missing either —
                    # typically a damaged/EXIF-less file, e.g. a
                    # half-copied victim of an interrupted Collect — must
                    # be skipped with a local log, never crash the whole
                    # recording loop (Nelson 2026-06-10, interrupted-
                    # ingest resume). The file stays on disk; fixing or
                    # removing it and re-running Collect records it.
                    # EXIF-less files are FIRST-CLASS content (Nelson
                    # 2026-06-11 — the renamed bird clips): no camera →
                    # the sentinel "_unknown" camera (mirrors the
                    # ``_no_timestamp\_unknown`` folder the copy engine
                    # already gives them); no capture time → the file's
                    # modification date as an honest fallback
                    # (``tz_source='none'``, undated → no day). Only a
                    # file we cannot even stat() is skipped.
                    cam_id = job.camera_id
                    raw_dt = job.capture_time_raw
                    corr_dt = job.capture_time_corrected
                    fellback = False
                    if not cam_id:
                        cam_id = "_unknown"
                        fellback = True
                        if not unknown_camera_ensured:
                            eg.store.upsert(m.Camera(camera_id="_unknown"))
                            unknown_camera_ensured = True
                    if raw_dt is None:
                        try:
                            raw_dt = datetime.fromtimestamp(
                                dest.stat().st_mtime)
                        except OSError:
                            log.warning(
                                "Collect: NOT recording %s — unreadable "
                                "file (stat failed). Fix or remove it "
                                "under the captured tree and re-run "
                                "Collect.", dest)
                            skipped_unrecordable.append(dest.name)
                            continue
                        corr_dt = raw_dt
                        fellback = True
                    if fellback:
                        log.info(
                            "Collect: recording %s with fallback "
                            "identity (camera=%s, time=%s)",
                            dest.name, cam_id, raw_dt.isoformat())
                    if progress is not None and (
                        items_done == items_total or items_done % 25 == 0
                    ):
                        progress(
                            tr("Recording {i}/{n} — {name}")
                            .replace("{i}", str(items_done))
                            .replace("{n}", str(items_total))
                            .replace("{name}", dest.name),
                            items_done, items_total,
                        )
                    kind = "video" if dest.suffix.lower() in VIDEO_EXTS else "photo"
                    # tz_offset_minutes = corrected − raw (the shift baked
                    # into capture_time_corrected this Collect). tz_source
                    # follows spec/52 §13: 'user_declared' for cameras with
                    # a calibrated TZ, 'phone_auto' for phone photos (their
                    # EXIF carries OffsetTimeOriginal), 'none' for cameras
                    # we have no offset for.
                    if raw_dt is not None and corr_dt is not None:
                        tz_off = int(round(
                            (corr_dt - raw_dt).total_seconds() / 60.0
                        ))
                    else:
                        tz_off = 0
                    if fellback:
                        tz_src = "none"
                    elif job.is_phone:
                        tz_src = "phone_auto"
                    elif (job.camera_id, job.day_number) in calibration_decisions:
                        tz_src = "user_declared"
                    else:
                        tz_src = "none"
                    eg.store.upsert(m.Item(
                        id=_uuid.uuid4().hex,
                        kind=kind,
                        provenance="captured",
                        origin_relpath=origin_relpath,
                        sha256=sha,
                        byte_size=byte_size,
                        materialized_at=now,
                        materialized_phase="ingest",
                        camera_id=cam_id,
                        day_number=job.day_number or None,
                        capture_time_raw=raw_dt.isoformat(),
                        capture_time_corrected=(
                            corr_dt.isoformat() if corr_dt else None
                        ),
                        tz_offset_minutes=tz_off,
                        tz_source=tz_src,
                        created_at=now,
                    ))
                if skipped_unrecordable and progress is not None:
                    progress(
                        tr("Recorded {n} item(s) — {k} file(s) skipped "
                           "(unreadable camera/time; see the log)")
                        .replace("{n}", str(
                            items_total - len(skipped_unrecordable)))
                        .replace("{k}", str(len(skipped_unrecordable))),
                        items_total, items_total,
                    )
        finally:
            eg.close()

    @staticmethod
    def _sha256_of(path) -> str:
        """SHA-256 of one file, read in 256 KiB chunks (matches the
        existing legacy ingest convention)."""
        import hashlib
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(256 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _safe_date(s: Optional[str]):
        """ISO date string → date, or None on parse failure. Centralises
        the same defensive parse used in the Collect merge."""
        from datetime import date as _date
        if not s:
            return None
        try:
            return _date.fromisoformat(s)
        except ValueError:
            return None

    def _reconcile_phone_tz(
        self, *, event_id, scan_rows, existing_days, merged_rows,
    ) -> None:
        """spec/57 §4.1 — late-phone TZ reconciliation.

        For each EXISTING plan day whose saved TZ differs from what this
        run's phone EXIF says, prompt once (one summary, two buttons).
        "Use phone times" updates the merged rows AND re-times the days
        that already hold photos (:meth:`EventGateway.retime_day` — the
        same machinery as the plan editor's single-day unlock); "Keep
        plan" leaves everything exactly as the user built it. Days that
        agree are silent — the plan is never silently overridden."""
        from PyQt6.QtWidgets import QMessageBox

        scan_tz = {r.date: r.tz_minutes for r in scan_rows
                   if r.tz_minutes is not None}
        mismatches = []        # (date, day_number, plan_tz, phone_tz)
        for td in existing_days:
            d = self._safe_date(td.date)
            if d is None or td.tz_minutes is None:
                continue
            phone = scan_tz.get(d)
            if phone is not None and int(phone) != int(td.tz_minutes):
                mismatches.append((d, td.day_number, int(td.tz_minutes), int(phone)))
        if not mismatches:
            return

        def _fmt(minutes: int) -> str:
            sign = "+" if minutes >= 0 else "-"
            a = abs(minutes)
            return f"UTC{sign}{a // 60:02d}:{a % 60:02d}"

        lines = [tr("This run's phone data disagrees with the plan's "
                    "timezones:"), ""]
        for d, n, plan, phone in mismatches:
            lines.append(tr("Day {n} ({date}): plan {plan} · phone {phone}")
                         .replace("{n}", str(n))
                         .replace("{date}", d.isoformat())
                         .replace("{plan}", _fmt(plan))
                         .replace("{phone}", _fmt(phone)))
        lines += ["", tr("Use the phone times? Days that already hold "
                         "photos will be re-timed (originals are never "
                         "modified).")]
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setWindowTitle(tr("Timezone differences found"))
        box.setText("\n".join(lines))
        use_phone = box.addButton(tr("Use phone times"),
                                  QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("Keep my plan"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not use_phone:
            return

        by_date = {d: (n, phone) for d, n, _plan, phone in mismatches}
        for row in merged_rows:
            hit = by_date.get(row.date)
            if hit is not None:
                row.tz_minutes = hit[1]
        # Re-time the days that already hold photos so corrected times
        # follow the accepted phone TZ before any new photos join them.
        try:
            eg = self.gateway.open_event(event_id)
            try:
                day_nums_with_items = {
                    it.day_number for it in eg.items() if it.day_number is not None
                }
                for d, (n, phone) in by_date.items():
                    if n in day_nums_with_items:
                        out = eg.retime_day(n, phone)
                        log.info("phone-TZ reconcile: day %s re-timed (%s)", n, out)
            finally:
                eg.close()
        except Exception:                                       # noqa: BLE001
            log.exception("phone-TZ reconciliation re-time failed")

    def _merge_collect_rows(self, scan_rows, existing_days):
        """Date-keyed merge of the new scan with the event's existing
        trip_days.

        * Existing day + new scan match → one row using the event's saved
          values (country / TZ / location / description); the new photos
          will land in that day on ingest.
        * New scan day not in the event → autofilled row exactly as the
          scan produced it.
        * Existing day with NO new photos in this scan → appended at the
          end as an unchecked row so the user sees the full plan in
          context. Polish (muted / read-only styling) comes in a follow-up.

        Override-marker generation (when phone-EXIF differs from the saved
        values) is intentionally NOT here — it lands with the override-ask
        UI sub-slice.
        """
        import json as _json
        from mira.ui.pages.day_browse_dialog import _VIDEO_EXTS  # noqa: F401
        from core.scan_source import ScanDayRow

        def _country_from_extras(extras_json):
            try:
                blob = _json.loads(extras_json or "{}")
            except (ValueError, TypeError):
                blob = {}
            return blob.get("country_code") if isinstance(blob, dict) else None

        by_date_existing = {}
        for td in existing_days:
            d = self._safe_date(td.date)
            if d is not None:
                by_date_existing[d] = td

        merged = []
        scan_dates = set()
        for sr in scan_rows:
            scan_dates.add(sr.date)
            existing = by_date_existing.get(sr.date)
            if existing is None:
                merged.append(sr)                               # fresh day
                continue
            existing_country = _country_from_extras(existing.extras_json) or ""
            merged.append(ScanDayRow(
                date=sr.date,
                checked=True,
                country_code=existing_country or sr.country_code,
                tz_minutes=(
                    existing.tz_minutes
                    if existing.tz_minutes is not None else sr.tz_minutes
                ),
                location=existing.location or sr.location,
                description=existing.description or sr.description,
                override_marker=None,                           # follow-up
            ))

        # Existing days NOT in this scan — appended as unchecked context
        # rows. Sub-slice "muted styling" will mark these read-only.
        for d, td in sorted(by_date_existing.items()):
            if d in scan_dates:
                continue
            country = _country_from_extras(td.extras_json) or ""
            merged.append(ScanDayRow(
                date=d,
                checked=False,
                country_code=country,
                tz_minutes=td.tz_minutes,
                location=td.location or "",
                description=td.description or "",
                override_marker=None,
            ))

        merged.sort(key=lambda r: r.date)
        return merged

    def _on_event_back(self) -> None:
        """Back from the activity dashboard → the (refreshed) events list. Also
        refreshes the menu state so per-event entries hide and the events-list
        entries (New event / New event from photos / Restore / standalone tools)
        show."""
        self.events_page.refresh()
        self.page_stack.show_page(ENTRY_DASHBOARD)
        self._current_event_id = None
        self._refresh_menu_state()

    def _event_is_closed_now(self) -> bool:
        """Return the current event's ``is_closed`` flag, or False when no event
        is open or the gateway open fails."""
        if self._current_event_id is None:
            return False
        try:
            eg = self.gateway.open_event(self._current_event_id)
            try:
                return bool(eg.event().is_closed)
            finally:
                eg.close()
        except (KeyError, RuntimeError):
            return False

    def _open_event_info_dialog(self, event_id: str) -> None:
        """spec/64 §2.2 — Event tile **title** click opens the Event
        Header dialog for the existing event (identity, not schedule)."""
        self._open_event_header_dialog(event_id)

    def _open_event_plan_from_card(self, event_id: str) -> None:
        """spec/64 §2.2 — Event tile **left side** click opens the Event
        Days Table dialog for the existing event (schedule, not
        identity)."""
        self._open_event_days_table_dialog(event_id)

    def _open_event_header_dialog(self, event_id: str) -> None:
        """spec/64 §3 — open the EventHeaderDialog for an existing event.
        Reads the current values + opens the dialog pre-populated; on
        OK persists via :meth:`Gateway.set_classification`. Cancel = no
        change. Refreshes the dashboard tile so the new values land
        on-screen."""
        import json as _json
        from mira.ui.pages.event_header_dialog import (
            EventHeaderDialog,
        )

        self._current_event_id = event_id
        try:
            eg = self.gateway.open_event(event_id)
            try:
                ev = eg.event()
            finally:
                eg.close()
        except Exception:                                       # noqa: BLE001
            log.exception(
                "Could not read event %s for Header dialog", event_id)
            return

        try:
            participants = _json.loads(ev.participants or "[]")
            if not isinstance(participants, list):
                participants = []
        except (ValueError, TypeError):
            participants = []
        try:
            creative_focus = _json.loads(ev.creative_focus or "[]")
            if not isinstance(creative_focus, list):
                creative_focus = []
        except (ValueError, TypeError):
            creative_focus = []

        existing = {
            "name": ev.name or "",
            "event_type": ev.event_type or "trip",
            "event_subtype": ev.event_subtype or "",
            "description": ev.description or "",
            # spec/77 §5 — pre-populate the From / To dates so the user
            # edits the existing range instead of having to retype it.
            "start_date": ev.start_date,
            "end_date": ev.end_date,
            "duration_value": ev.duration_value,
            "duration_unit": ev.duration_unit,
            "participants": participants,
            "context": ev.context,
            "experience_type": ev.experience_type,
            "creative_focus": creative_focus,
        }

        dlg = self._exec_event_header_dialog(EventHeaderDialog(
            existing_info=existing,
            on_locate_originals=lambda: self._gate_missing_originals(event_id),
            parent=self,
        ))
        if not dlg:
            return
        edited = dlg.header_info()
        try:
            self.gateway.set_classification(
                event_id,
                event_type=edited.get("event_type"),
                event_subtype=edited.get("event_subtype") or "",
                description=edited.get("description") or "",
                # spec/77 §5 — persist the mandatory date range so the
                # next refresh reads the same span the user just set.
                start_date=edited.get("start_date") or "",
                end_date=edited.get("end_date") or "",
                duration_value=edited.get("duration_value") or 0,
                duration_unit=edited.get("duration_unit") or "",
                participants=edited.get("participants") or [],
                context=edited.get("context") or "",
                experience_type=edited.get("experience_type") or "",
                creative_focus=edited.get("creative_focus") or [],
            )
        except Exception:                                       # noqa: BLE001
            log.exception(
                "Could not persist Header for %s", event_id)
            return

        self.events_page.refresh()
        if (self._current_event_id == event_id
                and hasattr(self, "phases_page")):
            self.phases_page.set_event(event_id)

    @staticmethod
    def _exec_event_header_dialog(dlg):
        """Test seam (memory ``feedback_tests_never_exec_modals``):
        :meth:`_open_event_header_dialog` runs the dialog via this
        method so the suite can stub it without popping a real modal."""
        from PyQt6.QtWidgets import QDialog
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return dlg

    def _open_event_days_table_dialog(self, event_id: str) -> None:
        """spec/64 §4 — open the EventDaysTableDialog for an existing
        event. Reads the current trip_days + opens the dialog
        pre-populated; on OK persists via
        :meth:`EventGateway.save_trip_days`. Cancel = no change."""
        from core.scan_source import ScanDayRow

        self._current_event_id = event_id
        try:
            eg = self.gateway.open_event(event_id)
            try:
                existing_days = list(eg.trip_days())
            finally:
                eg.close()
        except Exception:                                       # noqa: BLE001
            log.exception(
                "Could not read event %s for Days Table dialog", event_id)
            return

        rows, day_number_by_date = self._build_scan_rows_from_trip_days(
            existing_days)
        if not rows:
            self._show_no_days_message()
            return

        dlg = self._exec_event_days_table_dialog(
            self._build_days_table_dialog(
                rows,
                browse_handler=self._make_days_table_browse_handler(event_id),
            ))
        if not dlg:
            return
        edited_rows = dlg.rows()
        if not self._save_trip_day_edits(
                event_id=event_id,
                edited_rows=edited_rows,
                day_number_by_date=day_number_by_date):
            return

        self.events_page.refresh()
        if (self._current_event_id == event_id
                and hasattr(self, "phases_page")):
            self.phases_page.set_event(event_id)

    def _prompt_for_no_gps_days(
        self, rows, *, home_country: Optional[str],
        home_tz_minutes: Optional[int],
    ) -> None:
        """spec/78 §A — when ANY day lacks GPS (no country OR no TZ),
        ask **once** for a default Country / time zone and apply it to
        every no-GPS day. GPS-bearing days are untouched. The user
        corrects individual days afterwards in the Event Days Table.

        Supersedes the per-stretch loop (spec/64 §4.4): non-consecutive
        days (the common case in a grab-bag past-photos import) would
        otherwise produce one prompt per gap, asking over and over.

        Mutates ``rows`` in place — the row list is the same object the
        caller hands to the Days Table dialog next."""
        blanks = self._collect_no_gps_rows(rows)
        if not blanks:
            return
        from mira.ui.pages.phone_gps_stretch_dialog import (
            PhoneGpsStretchDialog,
        )
        dlg = self._exec_phone_gps_stretch_dialog(PhoneGpsStretchDialog(
            dates=[r.date for r in blanks],
            initial_country=home_country,
            initial_tz_minutes=home_tz_minutes,
            parent=self,
        ))
        if not dlg:
            return                                              # Skip
        country, tz_minutes = dlg.result_values()
        for r in blanks:
            if country:
                r.country_code = country
            if tz_minutes is not None:
                r.tz_minutes = tz_minutes

    @staticmethod
    def _collect_no_gps_rows(rows):
        """Walk ``rows`` and return a flat list of those missing country
        OR TZ (spec/78 §A — non-consecutive days collected together so
        the caller can ask once for them all). Pure logic, no Qt."""
        return [
            r for r in rows
            if (not (r.country_code or "").strip()) or (r.tz_minutes is None)
        ]

    @staticmethod
    def _exec_phone_gps_stretch_dialog(dlg):
        """Test seam — see :meth:`_exec_event_header_dialog`."""
        from PyQt6.QtWidgets import QDialog
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return dlg

    def _build_days_table_dialog(self, rows, **kwargs):
        """Construct the EventDaysTableDialog — split out so subclasses
        and tests can override the construction without re-implementing
        the whole open-flow. ``kwargs`` forward straight to the
        dialog's constructor (browse_handler, can_save_load_csv,
        can_delete_days, frozen_after_ingest, tz_editable_when_frozen,
        override_handler)."""
        from mira.ui.pages.event_days_table_dialog import (
            EventDaysTableDialog,
        )
        return EventDaysTableDialog(rows=rows, parent=self, **kwargs)

    def _make_days_table_browse_handler(self, event_id: str):
        """Return a ``(date) -> None`` callable for the EventDaysTableDialog
        Browse… column on an existing event: opens DayBrowseDialog over the
        day's already-ingested files (paths resolved under the event root).
        Parity with the fresh-scan flows' inline ``_browse_day`` closure;
        the existing-event side just queries the gateway instead of a
        cached ``scan.candidates_by_date``."""
        from PyQt6.QtWidgets import QMessageBox
        from mira.ui.pages.day_browse_dialog import DayBrowseDialog

        def _browse_day(day):
            try:
                eg = self.gateway.open_event(event_id)
                try:
                    target = day.isoformat()
                    day_numbers = [
                        d.day_number for d in eg.trip_days()
                        if d.date == target and d.day_number is not None
                    ]
                    root = eg.event_root
                    paths = [
                        root / it.origin_relpath
                        for n in day_numbers
                        for it in eg.items(day=n)
                    ]
                finally:
                    eg.close()
            except Exception:                                   # noqa: BLE001
                log.exception(
                    "Days Table Browse: failed to gather files for "
                    "%s / %s", event_id, day,
                )
                return
            if not paths:
                noinfo = QMessageBox(self)
                noinfo.setWindowTitle(tr("Nothing to browse"))
                noinfo.setText(tr(
                    "No files filed under {date} yet."
                ).replace("{date}", day.isoformat()))
                noinfo.setIcon(QMessageBox.Icon.NoIcon)
                noinfo.setStandardButtons(QMessageBox.StandardButton.Ok)
                noinfo.exec()
                return
            DayBrowseDialog(
                paths,
                title=tr("Browse — {date}").replace(
                    "{date}", day.isoformat()),
                parent=self,
            ).exec()

        return _browse_day

    @staticmethod
    def _exec_event_days_table_dialog(dlg):
        """Test seam — see :meth:`_exec_event_header_dialog`."""
        from PyQt6.QtWidgets import QDialog
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return dlg

    def _build_scan_rows_from_trip_days(self, existing_days):
        """Convert a list of TripDay rows into ScanDayRow shapes for
        the EventDaysTableDialog. Returns ``(rows, day_number_by_date)``.

        Country is pulled out of ``extras_json``; date is sorted; the
        ``day_number_by_date`` mapping lets the save path match edits
        back to the right day_number identity."""
        import json as _json
        from core.scan_source import ScanDayRow

        def _country_from_extras(extras_json):
            try:
                blob = _json.loads(extras_json or "{}")
            except (ValueError, TypeError):
                blob = {}
            return (blob.get("country_code")
                    if isinstance(blob, dict) else None) or ""

        rows = []
        day_number_by_date = {}
        for td in existing_days:
            d = self._safe_date(td.date)
            if d is None:
                continue
            day_number_by_date[d] = td.day_number
            rows.append(ScanDayRow(
                date=d, checked=True,
                country_code=_country_from_extras(td.extras_json),
                tz_minutes=td.tz_minutes,
                location=td.location or "",
                description=td.description or "",
                override_marker=None,
            ))
        rows.sort(key=lambda r: r.date)
        return rows, day_number_by_date

    def _show_no_days_message(self) -> None:
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.NoIcon)
        msg.setWindowTitle(tr("No days yet"))
        msg.setText(tr(
            "This event has no days yet. Days arrive in the table when "
            "you Collect photos for them."))
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.exec()

    def _save_trip_day_edits(
        self, *, event_id, edited_rows, day_number_by_date,
    ) -> bool:
        """Persist edited trip_days. Returns True on success, False on
        error (the caller bails on False)."""
        import json as _json
        from mira.store import models as m

        def _extras_with_country(country_code):
            if not country_code:
                return ""
            return _json.dumps({"country_code": country_code})

        new_days = []
        next_day_number = (
            max(day_number_by_date.values()) + 1
            if day_number_by_date else 1
        )
        for sr in edited_rows:
            day_number = day_number_by_date.get(sr.date)
            if day_number is None:
                day_number = next_day_number
                next_day_number += 1
            new_days.append(m.TripDay(
                day_number=day_number,
                date=sr.date.isoformat(),
                description=sr.description or "",
                location=sr.location,
                tz_minutes=sr.tz_minutes,
                extras_json=_extras_with_country(sr.country_code),
            ))
        new_days.sort(key=lambda td: td.day_number)
        try:
            eg = self.gateway.open_event(event_id)
            try:
                eg.save_trip_days(new_days)
            finally:
                eg.close()
        except Exception:                                       # noqa: BLE001
            log.exception(
                "Could not persist trip_days for %s", event_id)
            return False
        return True

    def _open_edit_plan_for_event(self) -> None:
        """Collect → Edit plan. spec/64 §4 — the new EventDaysTableDialog
        replaces the legacy plan editor (focus stays put + country / TZ
        propagate-down + free-text loc / desc) while keeping every
        legacy capability: Save / Load CSV (premium-gated), Delete-day,
        and the spec/57 §4.2 single-day TZ unlock (TZ stays live so the
        explicit re-time confirmation below gates the actual write).

        Adding new days is intentionally NOT supported here — new days
        enter the event via Collect (scan + ingest). Date is the row
        identity and is never editable."""
        from core.feature_flags import load_flags

        event_id = self._current_event_id
        if event_id is None:
            return
        try:
            eg = self.gateway.open_event(event_id)
            try:
                existing_days = list(eg.trip_days())
                # Per-day item presence — drives the spec/57 §4.2
                # single-day TZ unlock (a changed TZ on a day that
                # holds photos re-times them after confirmation).
                days_with_items = {
                    it.day_number for it in eg.items()
                    if it.day_number is not None
                }
                has_photos = bool(days_with_items)
            finally:
                eg.close()
        except Exception:                                       # noqa: BLE001
            log.exception("Could not read event %s for plan editor", event_id)
            return

        try:
            flags = load_flags(self.gateway.user_store)
            can_save_load_csv = flags.plan_save_load_csv
        except Exception:                                       # noqa: BLE001
            log.exception(
                "Could not read feature flags; defaulting CSV gate off")
            can_save_load_csv = False

        rows, day_number_by_date = self._build_scan_rows_from_trip_days(
            existing_days)
        if not rows:
            self._show_no_days_message()
            return

        dlg = self._exec_event_days_table_dialog(
            self._build_days_table_dialog(
                rows,
                can_save_load_csv=can_save_load_csv,
                can_delete_days=True,
                frozen_after_ingest=has_photos,
                # spec/57 §4.2 — pickers stay live; the explicit re-time
                # confirmation in _handle_retime_and_save is the gate.
                tz_editable_when_frozen=True,
                browse_handler=self._make_days_table_browse_handler(event_id),
            ))
        if not dlg:
            return
        edited_rows = dlg.rows()

        if not self._handle_retime_and_save(
                event_id=event_id,
                edited_rows=edited_rows,
                existing_days=existing_days,
                day_number_by_date=day_number_by_date,
                days_with_items=days_with_items):
            return

        self.events_page.refresh()
        if (self._current_event_id == event_id
                and hasattr(self, "phases_page")):
            self.phases_page.set_event(event_id)

    def _handle_retime_and_save(
        self, *, event_id, edited_rows, existing_days,
        day_number_by_date, days_with_items,
    ) -> bool:
        """spec/57 §4.2 — detect TZ changes on days that already hold
        photos, ask the user to confirm re-timing, run the retime, then
        save the full plan. Returns True on success, False on
        cancel/error."""
        import json as _json
        from mira.store import models as m

        def _extras_with_country(country_code):
            if not country_code:
                return ""
            return _json.dumps({"country_code": country_code})

        # ── spec/57 §4.2 — detect TZ changes on days that hold photos. ──
        orig_tz_by_date = {}
        for td in existing_days:
            d = self._safe_date(td.date)
            if d is not None:
                orig_tz_by_date[d] = td.tz_minutes
        retimes = []           # (date, day_number, new_tz)
        for sr in edited_rows:
            day_num = day_number_by_date.get(sr.date)
            if day_num is None or day_num not in days_with_items:
                continue
            old_tz = orig_tz_by_date.get(sr.date)
            if sr.tz_minutes is not None and sr.tz_minutes != old_tz:
                retimes.append((sr.date, day_num, int(sr.tz_minutes)))
        if retimes:
            lines = [tr("You changed the timezone of day(s) that already "
                        "hold photos:"), ""]
            lines += [
                tr("Day {n} ({date})").replace("{n}", str(n))
                .replace("{date}", d.isoformat())
                for d, n, _tz in retimes
            ]
            lines += ["", tr("Their photos will be re-timed to the new "
                             "timezone and may move across days. The "
                             "original files are never modified. Continue?")]
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.NoIcon)
            box.setWindowTitle(tr("Re-time these days?"))
            box.setText("\n".join(lines))
            cont = box.addButton(tr("Re-time and save"),
                                 QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            if box.clickedButton() is not cont:
                return False                                    # nothing saved
        new_days = []
        next_day_number = (
            max(day_number_by_date.values()) + 1
            if day_number_by_date else 1
        )
        for sr in edited_rows:
            day_number = day_number_by_date.get(sr.date)
            if day_number is None:
                day_number = next_day_number
                next_day_number += 1
            new_days.append(m.TripDay(
                day_number=day_number,
                date=sr.date.isoformat(),
                description=sr.description or "",
                location=sr.location,
                tz_minutes=sr.tz_minutes,
                extras_json=_extras_with_country(sr.country_code),
            ))
        new_days.sort(key=lambda td: td.day_number)
        try:
            eg = self.gateway.open_event(event_id)
            try:
                # Re-time FIRST (retime_day also writes the day's new TZ),
                # then save the full plan — same values, plus any other
                # field edits and photo-less days' TZ changes.
                for d, day_num, new_tz in retimes:
                    out = eg.retime_day(day_num, new_tz)
                    log.info("plan-editor TZ unlock: day %s re-timed (%s)",
                             day_num, out)
                eg.save_trip_days(new_days)
            finally:
                eg.close()
        except Exception as exc:                                # noqa: BLE001
            # ``save_trip_days`` rejects removals that would orphan
            # photos — surface as a warning and leave the dialog state
            # so the user can fix it.
            QMessageBox.warning(
                self, tr("Couldn't save the plan"),
                tr(
                    "The plan couldn't be saved — a day you removed may "
                    "still have photos under it. Move or discard those "
                    "photos first, then edit the plan again.\n\n{err}"
                ).replace("{err}", str(exc)),
            )
            return False
        return True

    def _open_event_triage(self) -> None:
        """Open the spec/44 Slice E triage dialog from the dashboard's
        Unclassified section's "Classify all…" action. The dialog persists
        each row's type immediately; we refresh the events dashboard when it
        closes so the chip counts reflect what just landed."""
        from mira.ui.pages.event_triage_dialog import EventTriageDialog
        dlg = EventTriageDialog(self.gateway, parent=self)
        # Refresh after each pick so the dashboard's Unclassified chip count
        # shrinks live while the user is still in the dialog.
        dlg.event_classified.connect(lambda *_: self.events_page.refresh())
        try:
            dlg.exec()
        finally:
            dlg.deleteLater()
        self.events_page.refresh()

    def _on_phase_activated(self, phase: str) -> None:
        """A phase tile was clicked — route to the corresponding surface.

        Slice A (2026-06-06): per-camera Cull picker retired (the unified Select
        opens directly). The Collect tile routes to the existing capture flow;
        Edit and Share route to their hosts; Slice B will polish the unified
        Select pool.
        """
        if phase == "plan" and self._current_event_id is not None:
            self._open_plan_editor_for_event(self._current_event_id)
            return
        if phase == "collect" and self._current_event_id is not None:
            self._open_collect(self._current_event_id)
            return
        if phase == "pick" and self._current_event_id is not None:
            # spec/84 §5 — Pick is gated while a background ingest is
            # still copying files into this event: the `item` rows
            # aren't written until the queue's commit closure runs, so
            # Pick would show zero (or partial) decisions and confuse
            # the user. Warn + stay on Phases; the queue's progress
            # line above already shows the import.
            if self.is_ingesting(self._current_event_id):
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.information(
                    self, tr("Still importing"),
                    tr("This event is still importing — try Pick again "
                       "when the import finishes."),
                )
                return
            # spec/65 §3.5 — Pick lands on the Days Lists "pick where to
            # start" dashboard first. Building the per-day snapshots is
            # cheap (two GROUP BYs + one bucket_cache read per day).
            self._open_days_lists_for(self._current_event_id)
            return
        if phase == "edit" and self._current_event_id is not None:
            # spec/57: entering Edit runs the external seams — scan for
            # tool results (§3.3), then (re)build the links projection
            # (§2.2) so the doorway is current the moment the parallel
            # tracks begin. Quiet unless something user-relevant happened.
            self._run_edit_entry_seams()
            # spec/70 Phase 3 §3 — Edit phase now lands on the Days
            # Lists dashboard, same shape as Pick. The user picks a day
            # → Days Grid → EditorPage on a single click. The Edit
            # bridge flag tells :meth:`_on_days_grid_item_activated` to
            # route to the EditorPage instead of the Picker; it's
            # consumed by :meth:`_on_process_closed`.
            self._edit_phase_active = True
            self._open_days_lists_for(self._current_event_id)
            return
        if phase == "export" and self._current_event_id is not None:
            # spec/68 §3 — Export rides the Phases → Days Lists → Days
            # Grid spine (same as Pick/Edit). The flat-grid MVP retired
            # with this commit; the per-day batch trigger lives on the
            # grid's toolbar.
            self._export_phase_active = True
            self._open_days_lists_for(self._current_event_id)
            return
        if phase == "share" and self._current_event_id is not None:
            if self.curate_page.open_event(self._current_event_id):
                self._cuts_entry_door = self._ACTIVITY_PAGE_KEY
                self.page_stack.show_page(self._CURATE_PAGE_KEY)
            return
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.information(
            self, tr("Coming next"),
            tr("The {phase} surface is being reassembled next.").replace("{phase}", phase),
        )

    def _refresh_picked_media(self, *, quiet: bool) -> None:
        """(Re)build the ``Picked Media/`` links projection (spec/57 §2).

        Called automatically on entering Edit and manually from the Edit
        menu's Refresh action. The rebuild is manifest-guarded — it never
        touches real bytes (external-tool outputs awaiting ingest are
        preserved, always). ``quiet`` controls the summary dialog: entry
        rebuilds log only; the manual action reports its counts."""
        if self._current_event_id is None:
            return
        from pathlib import Path as _Path

        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QGuiApplication
        from PyQt6.QtWidgets import QMessageBox

        from core.picked_media import rebuild_picked_media
        from mira.picked.edit_model import picked_media_entries
        from mira.picked.status import default_state_for

        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            eg = self.gateway.open_event(self._current_event_id)
            try:
                if eg.event_root is None:
                    log.warning("picked-media refresh skipped: no event_root")
                    return
                entries = picked_media_entries(
                    eg, default_state_for(self.gateway.settings, "pick"))
                result = rebuild_picked_media(_Path(eg.event_root), entries)
            finally:
                eg.close()
        except Exception:  # noqa: BLE001 — the doorway must never block Edit
            log.exception("picked-media refresh failed")
            return
        finally:
            QGuiApplication.restoreOverrideCursor()
        if quiet and not result.errors:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setWindowTitle(tr("Picked Media links"))
        lines = [
            tr("{n} item(s) linked for external tools.")
            .replace("{n}", str(result.linked)),
        ]
        if result.bracket_dirs:
            lines.append(tr("{n} bracket folder(s) prepared for stacking.")
                         .replace("{n}", str(result.bracket_dirs)))
        if result.copied:
            lines.append(tr("{n} file(s) copied (event on a different "
                            "drive than its media — links not possible).")
                         .replace("{n}", str(result.copied)))
        if result.preserved:
            lines.append(tr("{n} real file(s) found and left untouched.")
                         .replace("{n}", str(result.preserved)))
        if result.errors:
            lines.append(tr("{n} problem(s) — see the log.")
                         .replace("{n}", str(len(result.errors))))
        box.setText("\n".join(lines))
        box.exec()

    def _run_edit_entry_seams(self) -> None:
        """Entering Edit (spec/57): scan for external results (§3.3), then
        rebuild the links projection (§2.2). Silent when nothing happened;
        the unmerged-brackets reminder (§3.4 — derived, dismissible, never
        a wall) shows at most once per event per app session."""
        report = self._scan_external_returns(quiet=True)
        self._refresh_picked_media(quiet=True)
        if report is None:
            return
        show_nudge = False
        if report.unmerged_bracket_count > 0:
            if not hasattr(self, "_stack_nudge_shown"):
                self._stack_nudge_shown: set = set()
            if self._current_event_id not in self._stack_nudge_shown:
                self._stack_nudge_shown.add(self._current_event_id)
                show_nudge = True
        if report.nothing_happened and not show_nudge:
            return
        self._show_returns_box(report)

    def _scan_external_returns(self, *, quiet: bool):
        """Run the spec/57 §3 return scan (stacker adoptions at the Picked
        Media root + editor-return association under Edited Media).
        Returns the report, or ``None`` when no event is open. The manual
        action (``quiet=False``) re-links and always reports."""
        if self._current_event_id is None:
            return None
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QGuiApplication

        from mira.picked.external_returns import scan_for_returns
        from mira.picked.status import default_state_for

        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            eg = self.gateway.open_event(self._current_event_id)
            try:
                report = scan_for_returns(
                    eg, default_state_for(self.gateway.settings, "pick"))
            finally:
                eg.close()
        except Exception:  # noqa: BLE001 — the seam must never block Edit
            log.exception("external-returns scan failed")
            return None
        finally:
            QGuiApplication.restoreOverrideCursor()
        if not quiet:
            # Seamless rider: an adopted master re-appears at the root as
            # a canonical link the moment the scan finishes.
            self._refresh_picked_media(quiet=True)
            self._show_returns_box(report, force=True)
        return report

    def _show_returns_box(self, report, *, force: bool = False) -> None:
        """The spec/57 §3 results summary — NoIcon, plain lines."""
        from PyQt6.QtWidgets import QMessageBox

        lines = []
        if report.adopted:
            lines.append(tr("{n} merged stack(s) adopted into Original "
                            "Media/Merged.").replace("{n}", str(len(report.adopted))))
        if report.associated:
            lines.append(tr("{n} edited file(s) from external software "
                            "linked to their originals.")
                         .replace("{n}", str(len(report.associated))))
        if report.unmatched:
            shown = ", ".join(report.unmatched[:5])
            more = len(report.unmatched) - 5
            if more > 0:
                shown += tr(" … and {n} more").replace("{n}", str(more))
            lines.append(tr("{n} file(s) could not be matched to any "
                            "original and were left untouched: {names}")
                         .replace("{n}", str(len(report.unmatched)))
                         .replace("{names}", shown))
        if report.unmerged_bracket_count > 0:
            lines.append(tr("{n} picked bracket(s) have no merged result "
                            "yet — best to run your stacker before "
                            "editing them.")
                         .replace("{n}", str(report.unmerged_bracket_count)))
        if report.errors:
            lines.append(tr("{n} problem(s) — see the log.")
                         .replace("{n}", str(len(report.errors))))
        if not lines:
            if not force:
                return
            lines = [tr("No new external results found.")]
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setWindowTitle(tr("External results"))
        box.setText("\n".join(lines))
        box.exec()

    def _on_select_closed(self) -> None:
        """Back from the Select surface → the per-event phase grid
        (refreshed). spec/70 Phase 3: when the bridge from the redesigned
        Days Grid is active, return to the Days Grid instead — the user
        entered the legacy Picker from there and expects to land back on
        the surface they came from. The bridge flag is consumed here."""
        if self._days_grid_bridge_active:
            self._days_grid_bridge_active = False
            event_id = self.days_grid_page.current_event_id()
            day_number = self.days_grid_page.current_day_number()
            if event_id is not None:
                title, date_iso = self._lookup_day_meta(event_id, day_number)
                # Re-open to pull fresh phase_state into the cells so
                # the user sees their Pick/Skip decisions reflected on
                # the borders right away.
                if self.days_grid_page.open_for_day(
                    event_id, day_number, title=title, date_iso=date_iso,
                ):
                    self.page_stack.show_page(self._DAYS_GRID_PAGE_KEY)
                    self.days_grid_page.setFocus()
                    return
            # Bridge return failed — fall through to the legacy path.
        if self._current_event_id is not None:
            self.phases_page.set_event(self._current_event_id)
        self.page_stack.show_page(self._ACTIVITY_PAGE_KEY)

    def _on_select_fullscreen(self, on: bool) -> None:
        """Immersive select: mirror the cull fullscreen behaviour."""
        self.menuBar().setVisible(not on)

    # ── Quick Sweep entry points (spec/70 Phase 3) ────────────────────
    #
    # Quick Sweep rides the SAME DaysLists → DaysGrid → viewer route the
    # Picker uses. ``self._quick_sweep`` is the per-session state dict
    # that flags the route into QS mode (so the shared DaysLists /
    # DaysGrid signal handlers know to open the QS viewer instead of
    # the Picker). Two modes:
    #
    # * **Standalone** — folder picker + scan; paths-driven nav (no
    #   gateway). DaysListsPage.setEventForPreview + DaysGridPage.setDay
    #   carry the smoke-mode API.
    # * **Per-event** — re-uses ``_open_days_lists_for(event_id)``; the
    #   gateway-driven path is identical to the Picker's, with the QS
    #   flag set so item clicks open the QS viewer instead of Picker.

    def _qs_default_legacy_state(self) -> str:
        """The resolved ``quick_sweep_default_state`` translated to the
        :mod:`core.cull_state` legacy values the Quick Sweep ledger uses
        (``"kept"`` / ``"discarded"``). Mirrors how Edit pulls its
        default via :func:`default_state_for(settings, "edit")` —
        same single-reader contract, different phase key."""
        from core.cull_state import (
            STATE_DISCARDED as STATE_SKIPPED_LEGACY,
            STATE_KEPT as STATE_PICKED_LEGACY,
        )
        from mira.picked.status import STATE_PICKED, default_state_for
        phase_default = default_state_for(
            self.gateway.settings, "quick_sweep")
        return (
            STATE_PICKED_LEGACY if phase_default == STATE_PICKED
            else STATE_SKIPPED_LEGACY
        )

    def _qs_default_phase_state(self) -> str:
        """The resolved Quick Sweep default in :mod:`mira.picked.status`
        wire values (``"picked"`` / ``"skipped"``) — what the gateway-
        backed grid/snapshot code expects."""
        from mira.picked.status import default_state_for
        return default_state_for(self.gateway.settings, "quick_sweep")

    def _open_quick_sweep_standalone(self) -> None:
        """Standalone Quick Sweep: pop the folder picker, scan the source,
        bucket into days, hand the day snapshots to DaysListsPage and let
        the user pick which day to sweep. The Picker's exact nav route,
        with QS chrome on the leaf viewer."""
        from PyQt6.QtWidgets import QMessageBox
        from mira.ui.pages.quick_sweep_page import StandaloneCullSetupDialog
        from mira.ui.base.progress import run_with_progress
        from core.fresh_source import read_source_items

        dlg = StandaloneCullSetupDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        source, dest = dlg.source_path(), dlg.dest_path()

        ok, items = run_with_progress(
            self, tr("Quick Sweep"),
            lambda report: read_source_items(source),
            label=tr("Reading {p}…").replace("{p}", source.name),
        )
        if not ok:
            QMessageBox.warning(
                self, tr("Quick Sweep"),
                tr("Could not read that folder (see log)."))
            return
        if not items:
            QMessageBox.information(
                self, tr("Quick Sweep"),
                tr("No photos or videos found in that folder."))
            return

        # Bucket the scanned items into PickDays so DaysListsPage can
        # show one card per source day. The ledger is pre-populated
        # with the QS default so every undecided item reads as that
        # default — the days-list bars and the grid cell borders both
        # render the all-green entry the QS contract promises.
        from mira.picked.quick_sweep_buckets import build_fast_days
        default_state = self._qs_default_legacy_state()
        state_ledger: dict[Path, str] = {
            it.path: default_state for it in items
        }
        days = build_fast_days(
            items, state_for=lambda p: state_ledger.get(p, default_state))
        if not days:
            QMessageBox.information(
                self, tr("Quick Sweep"),
                tr("No photos or videos found in that folder."))
            return

        # Group SourceItems by day so day-card clicks can rebuild the
        # grid without re-scanning. Sort each day chronologically so
        # the grid order matches the QuickSweepPage viewer's own
        # timestamp sort (otherwise clicking the first grid cell
        # would open the viewer at a different item — they'd disagree
        # on what "first" means).
        items_by_day: dict[int, list] = {}
        for day in days:
            wanted = {
                Path(ci.item_id)
                for b in day.buckets for ci in b.items
            }
            items_by_day[day.day_number] = sorted(
                (it for it in items if it.path in wanted),
                key=lambda it: (
                    it.timestamp is None,
                    it.timestamp.isoformat()
                    if it.timestamp is not None else "",
                    it.path.name,
                ),
            )

        self._quick_sweep = {
            "mode": "standalone",
            "dest": dest,
            "event_id": None,
            "state": state_ledger,
            "default": default_state,
            "items_by_day": items_by_day,
            "days": days,
            "current_day": None,
            "current_day_items": [],
        }

        # Days Lists in paths (smoke) mode — setEventForPreview takes the
        # event name + DaySnapshot list and renders without a gateway.
        snapshots = self._qs_build_day_snapshots(days)
        self.days_lists_page.setEventForPreview(
            tr("Quick Sweep — {p}").replace("{p}", source.name),
            snapshots,
        )
        self.page_stack.show_page(self._DAYS_LISTS_PAGE_KEY)

    def _open_quick_sweep_for_event(self) -> None:
        """Quick Sweep this event: route through the gateway-driven
        DaysLists / DaysGrid stack (the exact Picker route), with the
        QS flag set so item clicks open the QS viewer instead of the
        Picker. The per-event ``saved`` semantics (write-back to the
        gateway) live in a follow-up session — for now we log the kept
        set on Back."""
        event_id = self._current_event_id
        if event_id is None:
            return
        # Empty state ledger to start — per-event QS reads the current
        # decision from the gateway on viewer entry; the ledger only
        # captures what the user changes during this session.
        self._quick_sweep = {
            "mode": "per_event",
            "dest": None,
            "event_id": event_id,
            "state": {},
            "default": self._qs_default_legacy_state(),
            "items_by_day": {},
            "days": None,
            "current_day": None,
            "current_day_items": [],
        }
        # Re-use the Picker's DaysLists entry — gateway-driven snapshot
        # build + route. The flag we just set takes over routing in the
        # shared DaysLists / DaysGrid handlers.
        self._open_days_lists_for(event_id)

    def _qs_build_day_snapshots(self, days) -> list:
        """Build a list of :class:`DaySnapshot` from a paths-mode
        PickDay list (standalone QS only). Picked / skipped counts
        come from the session ledger — items not yet explicitly
        decided fall back to the resolved ``quick_sweep_default_state``
        so the bars render the "all-green by default" entry promise."""
        from core.cull_state import (
            STATE_CANDIDATE as STATE_CANDIDATE_LEGACY,
            STATE_DISCARDED as STATE_SKIPPED_LEGACY,
            STATE_KEPT as STATE_PICKED_LEGACY,
        )
        from mira.ui.pages.days_lists_page import DaySnapshot
        ledger: dict[Path, str] = (
            self._quick_sweep["state"]
            if self._quick_sweep is not None else {}
        )
        default = (
            self._quick_sweep.get("default", STATE_PICKED_LEGACY)
            if self._quick_sweep is not None else STATE_PICKED_LEGACY
        )
        from mira.picked.model import REAL_CLUSTER_KINDS
        out: list = []
        for d in days:
            items_count = sum(
                len(b.items) for b in d.buckets)
            # Match the gateway path: only real clusters (burst /
            # focus_bracket / exposure_bracket / repeat) count toward
            # the "Clusters · N" badge — individuals / moments /
            # videos flatten to per-item cells in the grid.
            bucket_count = sum(
                1 for b in d.buckets if b.kind in REAL_CLUSTER_KINDS
            )
            # Count picked / skipped per day from the ledger — Compare
            # counts as picked at Save time (QS contract); the bar
            # follows suit so the visible green tracks the kept pool.
            picked = 0
            skipped = 0
            for b in d.buckets:
                for ci in b.items:
                    s = ledger.get(Path(ci.item_id), default)
                    if s in (STATE_PICKED_LEGACY, STATE_CANDIDATE_LEGACY):
                        picked += 1
                    elif s == STATE_SKIPPED_LEGACY:
                        skipped += 1
            # Build a 24-hour capture-time histogram for the spark line.
            hours = [0] * 24
            for b in d.buckets:
                for ci in b.items:
                    ts = ci.capture_time_corrected
                    if not ts:
                        continue
                    try:
                        from datetime import datetime
                        h = datetime.fromisoformat(ts).hour
                        if 0 <= h < 24:
                            hours[h] += 1
                    except (ValueError, TypeError):
                        continue
            # Per-source-day "label" carries the date in the legacy
            # ``PickDay.label`` (e.g. "Day 1 — 2026-05-27"); split into
            # title + date for the snapshot.
            label = d.label or f"Day {d.day_number}"
            date_iso = ""
            title = label
            if " — " in label:
                title, date_iso = label.split(" — ", 1)
            out.append(DaySnapshot(
                day_number=d.day_number,
                title=title.strip(),
                date_iso=date_iso.strip(),
                picked=picked, skipped=skipped,
                buckets=bucket_count,
                items=items_count,
                capture_hours=hours,
            ))
        return out

    def _qs_open_day(self, day_number: int) -> None:
        """Open ``day_number`` in DaysGridPage from a QS session — both
        standalone (paths mode via setDay) and per-event (gateway mode
        via open_for_day) end up here."""
        if self._quick_sweep is None:
            return
        if self._quick_sweep["mode"] == "standalone":
            # Paths mode — synthesise GridItems for the day's items.
            day_items = self._quick_sweep["items_by_day"].get(day_number, [])
            self._quick_sweep["current_day"] = day_number
            self._quick_sweep["current_day_items"] = day_items
            snap = next(
                (s for s in self._qs_build_day_snapshots(
                    self._quick_sweep["days"])
                 if s.day_number == day_number),
                None,
            )
            title = snap.title if snap is not None else f"Day {day_number}"
            date_iso = snap.date_iso if snap is not None else ""
            grid_items = self._qs_build_grid_items(day_number)
            self.days_grid_page.set_paths_mode_callbacks(
                state_lookup=self._qs_lookup_thumb_state,
                day_rebuild=lambda: self._qs_build_grid_items(day_number),
            )
            self.days_grid_page.setDay(
                day_number, title, date_iso, grid_items)
            # spec/71 — Quick Sweep wears Collect chrome (blue).
            self.days_grid_page.set_phase_identity("collect")
            self.page_stack.show_page(self._DAYS_GRID_PAGE_KEY)
            self.days_grid_page.setFocus()
            return
        # Per-event mode — gateway-driven open_for_day (same as Picker).
        # The default-state override routes the day grid's cell borders
        # to ``quick_sweep_default_state`` instead of the pick-phase
        # default so the QS session reads as all-green on entry even
        # when nothing has been picked yet.
        event_id = self._quick_sweep["event_id"]
        if event_id is None:
            return
        title, date_iso = self._lookup_day_meta(event_id, day_number)
        if not self.days_grid_page.open_for_day(
            event_id, day_number, title=title, date_iso=date_iso,
            default_state=self._qs_default_phase_state(),
        ):
            log.warning(
                "QS per-event: open_for_day(%s, %s) failed",
                event_id, day_number)
            return
        # spec/71 — QS chrome even when reading from the Pick gateway.
        self.days_grid_page.set_phase_identity("collect")
        # Per-event mode tracks the day items list so the QS viewer
        # has a Sequence to walk. Build it from the gateway items.
        from datetime import datetime
        from core.fresh_source import SourceItem
        try:
            eg = self.gateway.open_event(event_id)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "QS per-event: gateway open failed for %s", event_id)
            return
        try:
            event_root = Path(eg.event_root)
            day_items = []
            for it in eg.items():
                if not it.origin_relpath:
                    continue
                if (getattr(it, "day_number", None) is not None
                        and it.day_number != day_number):
                    continue
                path = event_root / it.origin_relpath
                ts = None
                if it.capture_time_corrected:
                    try:
                        ts = datetime.fromisoformat(
                            it.capture_time_corrected)
                    except (ValueError, TypeError):
                        ts = None
                day_items.append(SourceItem(
                    path=path, timestamp=ts,
                    camera_id=it.camera_id or "",
                ))
        finally:
            try:
                eg.close()
            except Exception:                                      # noqa: BLE001
                pass
        self._quick_sweep["current_day"] = day_number
        self._quick_sweep["current_day_items"] = day_items
        self.page_stack.show_page(self._DAYS_GRID_PAGE_KEY)
        self.days_grid_page.setFocus()

    def _qs_lookup_thumb_state(self, path: Path):
        """Translate a path's QS ledger state into the Thumb's wire
        value (``"picked"`` / ``"skipped"`` / ``"compare"`` / ``None``).
        Registered with :meth:`DaysGridPage.set_paths_mode_callbacks`
        so the page can colour cluster-drill-in member cells without
        a gateway."""
        from core.cull_state import (
            STATE_CANDIDATE as _C,
            STATE_DISCARDED as _D,
            STATE_KEPT as _K,
        )
        if self._quick_sweep is None:
            return None
        ledger = self._quick_sweep["state"]
        default = self._quick_sweep["default"]
        s = ledger.get(path, default)
        if s == _K:
            return "picked"
        if s == _D:
            return "skipped"
        if s == _C:
            return "compare"
        return None

    def _qs_build_grid_items(self, day_number: int) -> list:
        """Build the day grid's GridItems from the session's PickDay
        structure for ``day_number``. Real clusters (burst /
        focus_bracket / exposure_bracket / repeat) collapse to ONE
        cluster-cover GridItem with the §5a cluster_type icon, count
        chip and (for mixed clusters) split chip; the cover carries
        ``_cull_cluster`` so DaysGridPage's drill-in walks the members.
        All other bucket kinds (individual / moment / video) flatten
        to per-item cells. Items without an explicit decision in the
        QS ledger fall back to the resolved
        ``quick_sweep_default_state`` so the grid renders the all-
        green-on-entry contract (or all-red when the user flipped the
        setting).

        Sort order: cluster covers + flat cells interleaved
        chronologically by the cell's anchor capture time — clusters
        anchor on their earliest member, flat cells anchor on their
        own capture time. The same ISO-string sort key the
        ``QuickSweepPage`` viewer uses, so the grid and the viewer
        walk the day in the same order (the user clicks the leftmost
        cell, the viewer opens with the leftmost cell selected — no
        sneaky re-shuffle on entry)."""
        from mira.picked.model import CullCluster, REAL_CLUSTER_KINDS
        from mira.picked.status import CellColor
        from mira.ui.pages.days_grid_page import GridItem
        from core.cull_state import (
            STATE_CANDIDATE as STATE_CANDIDATE_LEGACY,
            STATE_DISCARDED as STATE_SKIPPED_LEGACY,
            STATE_KEPT as STATE_PICKED_LEGACY,
        )
        state_to_thumb = {
            STATE_PICKED_LEGACY: "picked",
            STATE_SKIPPED_LEGACY: "skipped",
            STATE_CANDIDATE_LEGACY: "compare",
        }
        cluster_type_for_kind = {
            "burst": "burst",
            "focus_bracket": "focus",
            "exposure_bracket": "exposure",
            "repeat": "repeated",
        }
        is_video_ext = {
            ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".mts", ".mpg",
            ".mpeg",
        }
        if self._quick_sweep is None:
            return []
        days = self._quick_sweep.get("days") or []
        day = next(
            (d for d in days if d.day_number == day_number), None)
        if day is None:
            return []
        ledger = self._quick_sweep["state"]
        default = self._quick_sweep["default"]

        def _cluster_anchor(bucket) -> str:
            times = [
                ci.capture_time_corrected
                for ci in bucket.items
                if ci.capture_time_corrected
            ]
            return min(times) if times else ""

        raw: list[tuple[str, "GridItem"]] = []
        for bucket in day.buckets:
            is_real_cluster = (
                bucket.kind in REAL_CLUSTER_KINDS
                and len(bucket.items) > 1
            )
            if is_real_cluster:
                picked_n = 0
                skipped_n = 0
                for ci in bucket.items:
                    s = ledger.get(ci.path, default)
                    if s == STATE_PICKED_LEGACY:
                        picked_n += 1
                    elif s == STATE_SKIPPED_LEGACY:
                        skipped_n += 1
                # Aggregate cluster colour: mixed when both picked +
                # skipped members exist; else the dominant side.
                if picked_n > 0 and skipped_n > 0:
                    cover_state = "mixed"
                    split = (picked_n, skipped_n)
                elif skipped_n > 0:
                    cover_state = "skipped"
                    split = None
                elif picked_n > 0:
                    cover_state = "picked"
                    split = None
                else:
                    cover_state = None
                    split = None
                cover_path = (
                    bucket.items[0].path if bucket.items else None
                )
                cull_cluster = CullCluster(
                    bucket_key=bucket.bucket_key,
                    kind=bucket.kind,
                    title=bucket.title,
                    members=bucket.items,
                    color=CellColor.UNTOUCHED,  # painted via GridItem.state
                    detection_source=getattr(
                        bucket, "detection_source", "") or "",
                    camera=getattr(bucket, "camera", "") or "",
                )
                cover = GridItem(
                    item_id=f"cluster:{bucket.bucket_key}",
                    item_kind="cluster",
                    state=cover_state,
                    visited=False,
                    exported=False,
                    cluster_type=cluster_type_for_kind.get(bucket.kind),
                    cluster_count=len(bucket.items),
                    cluster_split=split,
                    _path=cover_path,
                    _cull_cluster=cull_cluster,
                )
                raw.append((_cluster_anchor(bucket), cover))
            else:
                for ci in bucket.items:
                    kind = (
                        "video"
                        if ci.path.suffix.lower() in is_video_ext
                        else "photo"
                    )
                    s = ledger.get(ci.path, default)
                    flat = GridItem(
                        item_id=str(ci.path),
                        item_kind=kind,
                        state=state_to_thumb.get(s),
                        visited=False,
                        exported=False,
                        _path=ci.path,
                    )
                    raw.append(
                        (ci.capture_time_corrected or "", flat))
        # Chronological sort: empty timestamps to the back (matches
        # the viewer's "None last" rule), then ISO ascending.
        raw.sort(key=lambda pair: (pair[0] == "", pair[0]))
        return [g for _, g in raw]

    def _qs_open_viewer_for_item(self, item_id: str) -> None:
        """Click on a Days Grid cell from a QS session → open the
        redesigned QS viewer, walking the current day's items."""
        if self._quick_sweep is None:
            return
        items = self._quick_sweep["current_day_items"]
        if not items:
            return
        if self._quick_sweep["mode"] == "standalone":
            # In paths mode, item_id == str(path).
            target = Path(item_id)
            start_idx = next(
                (i for i, it in enumerate(items) if it.path == target), 0,
            )
        else:
            # Gateway mode — item_id is the gateway item-id; map by
            # iterating eg.items()'s origin_relpath.
            event_id = self._quick_sweep["event_id"]
            target_path: Optional[Path] = None
            try:
                eg = self.gateway.open_event(event_id)
            except Exception:                                      # noqa: BLE001
                log.exception(
                    "QS per-event: gateway open failed during item open")
                return
            try:
                it = eg.item(item_id)
                if it is not None and it.origin_relpath:
                    target_path = Path(eg.event_root) / it.origin_relpath
            finally:
                try:
                    eg.close()
                except Exception:                                  # noqa: BLE001
                    pass
            if target_path is None:
                return
            start_idx = next(
                (i for i, s in enumerate(items) if s.path == target_path),
                0,
            )
        # Load the viewer with the day's items + the ledger (so prior
        # decisions persist across re-entries) and show.
        self.quick_sweep_page.load(
            items, start_index=start_idx,
            state=self._quick_sweep["state"],
        )
        self.page_stack.show_page(self._QUICK_SWEEP_PAGE_KEY)
        self.quick_sweep_page.setFocus()

    def _on_quick_sweep_saved(self, kept) -> None:
        """Save fired from the QS viewer. Updates the per-session ledger,
        then routes the save dialog only when the user later backs out
        of DaysLists. The viewer itself just emits saved + cancelled to
        flow back to the grid; the finalize gate is at the DaysLists
        Back."""
        if self._quick_sweep is None:
            return
        # The viewer already updated the shared ledger via _state.
        # Returning to the grid is enough; the user can keep sweeping
        # other days. The finalize dialog gates at the outermost Back.
        log.info(
            "QS viewer Save: %d kept in current day's ledger",
            len(kept) if kept else 0,
        )
        self._qs_return_to_days_grid()

    def _on_quick_sweep_cancelled(self) -> None:
        """Back / Esc from the QS viewer — return to the DaysGridPage
        (refreshed with the latest state from the ledger)."""
        if self._quick_sweep is None:
            return
        self._qs_return_to_days_grid()

    def _qs_return_to_days_grid(self) -> None:
        """Re-render the day grid with the latest K/D state from the
        ledger, then show it. Standalone mode rebuilds GridItems; per-
        event mode re-opens the gateway-driven day so the gateway-side
        state colours pick up (the QS write-back to phase_state is
        deferred, so the gateway state is unchanged — but re-opening
        is consistent)."""
        if self._quick_sweep is None:
            return
        if self._quick_sweep["mode"] == "standalone":
            day_number = self._quick_sweep.get("current_day")
            if day_number is None:
                self.page_stack.show_page(self._DAYS_LISTS_PAGE_KEY)
                return
            snap = next(
                (s for s in self._qs_build_day_snapshots(
                    self._quick_sweep["days"])
                 if s.day_number == day_number),
                None,
            )
            title = snap.title if snap is not None else f"Day {day_number}"
            date_iso = snap.date_iso if snap is not None else ""
            grid_items = self._qs_build_grid_items(day_number)
            self.days_grid_page.set_paths_mode_callbacks(
                state_lookup=self._qs_lookup_thumb_state,
                day_rebuild=lambda: self._qs_build_grid_items(day_number),
            )
            self.days_grid_page.setDay(
                day_number, title, date_iso, grid_items)
        else:
            # Per-event — re-open is consistent + cheap.
            event_id = self._quick_sweep.get("event_id")
            day_number = self._quick_sweep.get("current_day")
            if event_id is not None and day_number is not None:
                title, date_iso = self._lookup_day_meta(
                    event_id, day_number)
                self.days_grid_page.open_for_day(
                    event_id, day_number,
                    title=title, date_iso=date_iso,
                    default_state=self._qs_default_phase_state(),
                )
        # spec/71 — keep the Collect chrome on returning from the viewer.
        self.days_grid_page.set_phase_identity("collect")
        self.page_stack.show_page(self._DAYS_GRID_PAGE_KEY)
        self.days_grid_page.setFocus()

    def _qs_finalize_via_back(self) -> None:
        """Outermost-Back from DaysListsPage during a QS session. For
        standalone, pop the confirm dialog + copy_kept. For per-event,
        just log the ledger and return to Phases."""
        from PyQt6.QtWidgets import QMessageBox
        from core.standalone_cull_copy import CopyItem, copy_kept
        from mira.ui.base.progress import run_with_progress

        if self._quick_sweep is None:
            self.page_stack.show_page(ENTRY_DASHBOARD)
            return
        mode = self._quick_sweep["mode"]
        ledger = self._quick_sweep["state"]
        from core.cull_state import (
            STATE_CANDIDATE as _C, STATE_DISCARDED as _D,
            STATE_KEPT as _K,
        )
        kept_set = {p for p, s in ledger.items() if s in (_K, _C)}
        skipped = sum(1 for s in ledger.values() if s == _D)
        total = len(ledger)

        if mode == "standalone":
            dest = self._quick_sweep["dest"]
            self._quick_sweep = None
            if not ledger:
                self.page_stack.show_page(ENTRY_DASHBOARD)
                return
            # Confirm dialog.
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.NoIcon)
            box.setWindowTitle(tr("Finish Quick Sweep"))
            box.setText(tr("Quick Sweep — ready to copy."))
            bits = [
                tr("{n} of {total} item(s) will be copied.")
                .replace("{n}", str(len(kept_set)))
                .replace("{total}", str(total)),
            ]
            if skipped:
                bits.append(
                    tr("{n} discarded item(s) will not be copied.")
                    .replace("{n}", str(skipped)))
            bits.append("")
            bits.append(tr("Copy and finish?"))
            box.setInformativeText("\n".join(bits))
            copy_btn = box.addButton(
                tr("Copy and finish"),
                QMessageBox.ButtonRole.AcceptRole)
            box.addButton(
                tr("Stay in Quick Sweep"),
                QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(copy_btn)
            box.exec()
            if box.clickedButton() is not copy_btn:
                # User backed out — return to events list and drop the
                # session. The ledger is gone (no undo).
                self.page_stack.show_page(ENTRY_DASHBOARD)
                return
            if not kept_set:
                QMessageBox.information(
                    self, tr("Quick Sweep"),
                    tr("Nothing kept — nothing copied."))
                self.page_stack.show_page(ENTRY_DASHBOARD)
                return
            copy_items = [
                CopyItem(source=p, style="", rel_dest=Path(p.name))
                for p in kept_set
            ]
            ok, result = run_with_progress(
                self, tr("Quick Sweep"),
                lambda report: copy_kept(
                    copy_items, dest,
                    progress=lambda msg, cur, tot:
                        report(cur, tot, msg)),
                label=tr("Copying kept files…"),
            )
            if not ok:
                QMessageBox.warning(
                    self, tr("Quick Sweep"),
                    tr("Copy failed (see log)."))
                self.page_stack.show_page(ENTRY_DASHBOARD)
                return
            QMessageBox.information(
                self, tr("Quick Sweep — done"),
                tr("Copied {n} kept file(s) to:\n{dest}")
                .replace("{n}", str(result.ok_count))
                .replace("{dest}", str(dest)),
            )
            self.page_stack.show_page(ENTRY_DASHBOARD)
            return
        # Per-event — write-back deferred; log + return to Phases.
        event_id = self._quick_sweep.get("event_id")
        self._quick_sweep = None
        log.info(
            "QS per-event finish: ledger size=%d (write-back deferred)",
            total,
        )
        if event_id is not None:
            self.phases_page.set_event(event_id)
            self.page_stack.show_page(self._ACTIVITY_PAGE_KEY)
        else:
            self.page_stack.show_page(ENTRY_DASHBOARD)

    def _on_quick_sweep_fullscreen(self, on: bool) -> None:
        """Immersive Quick Sweep: mirror the cull/select fullscreen
        behaviour (hide the menu bar)."""
        self.menuBar().setVisible(not on)

    def _on_process_closed(self) -> None:
        """Back from the Process (Edit) surface. spec/70 Phase 3 §3 —
        when the Days Grid bridge is active the user entered EditorPage
        from the grid; return them there (refreshed). Otherwise fall
        back to the activity dashboard. The Edit-phase flag stays on
        until the user navigates back out to Phases (so a Back-and-
        re-open within the same day still routes to the Editor)."""
        if self._days_grid_bridge_active:
            self._days_grid_bridge_active = False
            event_id = self.days_grid_page.current_event_id()
            day_number = self.days_grid_page.current_day_number()
            if event_id is not None:
                title, date_iso = self._lookup_day_meta(event_id, day_number)
                grid_phase = "edit" if self._edit_phase_active else "pick"
                if self.days_grid_page.open_for_day(
                    event_id, day_number,
                    title=title, date_iso=date_iso, phase=grid_phase,
                ):
                    self.page_stack.show_page(self._DAYS_GRID_PAGE_KEY)
                    self.days_grid_page.setFocus()
                    return
        self._edit_phase_active = False
        if self._current_event_id is not None:
            self.phases_page.set_event(self._current_event_id)
        self.page_stack.show_page(self._ACTIVITY_PAGE_KEY)

    def _on_process_fullscreen(self, on: bool) -> None:
        """Immersive process: mirror the cull/select fullscreen behaviour."""
        self.menuBar().setVisible(not on)

    # (Surface 12 folded into EditorPage 2026-06-15 — the separate
    # _on_video_edit_back handler retired. EditorPage's own ``closed``
    # signal routes through _on_process_closed for both kinds.)

    # spec/68 §3 — the standalone _on_export_closed / _on_export_fullscreen
    # handlers retired with the flat-grid MVP. Export now rides the
    # shared Days Grid lifecycle; the Back path goes through
    # _on_days_grid_back / _on_days_lists_back which clear
    # _export_phase_active on the way out.

    def _on_curate_closed(self) -> None:
        """Back from the Cuts shell → the door the user came in through
        (spec/64 §2.4, Nelson 2026-06-13): events list when the user
        landed via a closed-tile body click, activity dashboard when
        they landed via the Share phase tile / menu."""
        if self._cuts_entry_door == ENTRY_DASHBOARD:
            self._current_event_id = None
            self.events_page.refresh()
            self.page_stack.show_page(ENTRY_DASHBOARD)
            self._refresh_menu_state()
            return
        if self._current_event_id is not None:
            self.phases_page.set_event(self._current_event_id)
        self.page_stack.show_page(self._ACTIVITY_PAGE_KEY)

    def _menu_new_cut(self) -> None:
        """Share menu → New Cut…: open the Share surface on the current
        event, then launch the composition dialog directly. Routed
        through the activity-dashboard door since the user was on a
        per-event surface when they fired the menu."""
        if self._current_event_id is None:
            return
        if self.curate_page.open_event(self._current_event_id):
            self._cuts_entry_door = self._ACTIVITY_PAGE_KEY
            self.page_stack.show_page(self._CURATE_PAGE_KEY)
            self.curate_page.start_new_cut()

    def _open_wizard(self) -> None:
        """Open the modal wizard. After it closes, invalidate the rules cache so
        the next classification uses the newly-generated scenarios (core/genre.py
        caches rules per-session; wizard completion is rare, so a full flush is
        cheap and correct). Returns to the Library on close."""
        from mira.ui.wizard.wizard_window import WizardWindow
        wizard = WizardWindow(self)
        wizard.exec()
        wizard.deleteLater()
        # Flush the per-session rules cache so the next classify picks up the
        # user scenarios the wizard just wrote.
        try:
            from core.genre import reset_rules_cache
            reset_rules_cache()
        except Exception:  # noqa: BLE001
            pass
        self.page_stack.show_page(ENTRY_DASHBOARD)
