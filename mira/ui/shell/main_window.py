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
from mira.ui.picked.pick_page import PickPage


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
    PHASE_PICK,
    PHASE_SHARE,
)
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
    _SELECT_PAGE_KEY = "__select__"
    _PROCESS_PAGE_KEY = "__process__"
    _CURATE_PAGE_KEY = "__curate__"

    def __init__(self, gateway: Optional[Gateway] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.gateway = gateway or Gateway()
        self.setWindowTitle(tr("Mira"))
        self.resize(1180, 760)

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # spec/59 §8 — the app-level batch queue + its ONE progress line,
        # directly below the menubar, visible from every page; hidden
        # when idle. Jobs run strictly one at a time.
        from mira.ui.shell.batch_queue import (
            BatchExportQueue, BatchProgressLine,
        )
        self.batch_queue = BatchExportQueue(self)
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

        # Slice A (2026-06-06): the per-camera Cull picker is retired. The
        # Select tile opens the unified Select surface directly.
        self.pick_page = PickPage(self.gateway)
        self.page_stack.add_page(self._SELECT_PAGE_KEY, self.pick_page)

        from mira.ui.edited.edit_host_page import EditHostPage
        self.edit_page = EditHostPage(self.gateway)
        self.page_stack.add_page(self._PROCESS_PAGE_KEY, self.edit_page)

        # spec/61: the Cuts shell — the Share landing (#exported + user
        # Cuts list → New Cut dialog → picking session; the Cut detail
        # surface joins with the flat-grid slice).
        from mira.ui.shared.cuts_shell import CutsShellPage
        self.curate_page = CutsShellPage(self.gateway)
        self.page_stack.add_page(self._CURATE_PAGE_KEY, self.curate_page)

        self._current_event_id: Optional[str] = None
        # spec/64 §2.4 (Nelson 2026-06-13): the Cuts shell has two
        # entry doors — Share-phase tile on the activity dashboard
        # (returns to that dashboard on Back) and the closed-tile
        # body click (returns to the events list). The flag remembers
        # which door the user came in through; ``_on_curate_closed``
        # routes Back accordingly.
        self._cuts_entry_door: str = self._ACTIVITY_PAGE_KEY

        row.addWidget(self.page_stack, stretch=1)
        self.setCentralWidget(central)
        self._build_menu_bar()

        self.events_page.event_activated.connect(self._open_event)
        self.events_page.event_info_requested.connect(self._open_event_info_dialog)
        self.events_page.event_plan_requested.connect(self._open_event_plan_from_card)
        self.events_page.event_status_toggle_requested.connect(
            self._on_card_status_toggle_requested)
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
        self.pick_page.closed.connect(self._on_select_closed)
        self.pick_page.fullscreen_changed.connect(self._on_select_fullscreen)
        self.edit_page.closed.connect(self._on_process_closed)
        self.edit_page.fullscreen_changed.connect(self._on_process_fullscreen)
        self.curate_page.closed.connect(self._on_curate_closed)

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
            from mira.ui.picked.standalone_pick import run_standalone_fast_cull
            run_standalone_fast_cull(self)
            self.page_stack.show_page(ENTRY_DASHBOARD)
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
        event_menu.addSeparator()
        self._add_menu_action(
            event_menu, tr("&Restore from backup…"),
            lambda: self._coming_next(tr("Restore from backup")),
            surface=self._SURFACE_EVENTS_LIST)
        # Per-event entries.
        self._add_menu_action(
            event_menu, tr("Edit &info…"), self._open_edit_info,
            surface=self._SURFACE_PER_EVENT, modification=True)
        event_menu.addSeparator()
        self._add_menu_action(
            event_menu, tr("&Stats…"), self._open_stats,
            surface=self._SURFACE_PER_EVENT)
        event_menu.addSeparator()
        self._add_menu_action(
            event_menu, tr("&Back up event…"),
            lambda: self._coming_next(tr("Back up event")),
            surface=self._SURFACE_PER_EVENT)
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
            lambda: self._coming_next(tr("Quick Sweep this event")),
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

        # ── Share ──────────────────────────────────────────────────────────
        # Per-event only: Open Share phase · New Cut · Audio.
        # Hides on the events list (empty in cross-event context).
        share_menu = self.menuBar().addMenu(tr("&Share"))
        self._menus["share"] = share_menu
        self._add_menu_action(
            share_menu, tr("&Open Share phase"),
            lambda: self._on_phase_activated(PHASE_SHARE),
            surface=self._SURFACE_PER_EVENT)
        self._add_menu_action(
            share_menu, tr("&New Cut…"),
            self._menu_new_cut,
            surface=self._SURFACE_PER_EVENT)
        share_menu.addSeparator()
        self._add_menu_action(
            share_menu, tr("&Audio…"),
            lambda: self._on_entry(ENTRY_AUDIO),
            surface=self._SURFACE_PER_EVENT)

        # ── Help ───────────────────────────────────────────────────────────
        help_menu = self.menuBar().addMenu(tr("&Help"))
        self._menus["help"] = help_menu
        self._add_menu_action(
            help_menu, tr("&Third-party tool guides…"),
            lambda: self._on_entry(ENTRY_HELPERS))

        # Apply the initial surface (no event open → events_list).
        self._refresh_menu_state()

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

        # spec/64 §4.4 — the per-location-group prompt for consecutive
        # phone-GPS-less days. Fills the rows whose phone didn't supply
        # country / TZ; days the user Skips stay blank for fine-tuning
        # via the Days Table dialog below.
        self._prompt_phone_gps_stretches(
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

        start_date = (
            sorted_rows[0].date.isoformat() if sorted_rows else None
        )
        end_date = (
            sorted_rows[-1].date.isoformat() if sorted_rows else None
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
        if self._event_is_closed(event_id):
            return self._open_event_cuts_list(event_id)
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

    def _open_event_cuts_list(self, event_id: str) -> bool:
        """Closed event body click (spec/64 §2.4) → land on the Cuts list.
        Same shape as the ``"share"`` route in :meth:`_on_phase_activated`,
        promoted to a direct door for closed events. Back from here
        returns to the events list (the door the user came in through),
        not the activity dashboard (see :meth:`_on_curate_closed`)."""
        if not self.curate_page.open_event(event_id):
            return False
        self._current_event_id = event_id
        self._cuts_entry_door = ENTRY_DASHBOARD
        self.page_stack.show_page(self._CURATE_PAGE_KEY)
        self._refresh_menu_state()
        return True

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
        from mira.ui.picked.camera_clock_dialog import CameraClockDialog

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

        # spec/64 §4.4 — per-location-group prompt for consecutive days
        # in the merged set where neither phone GPS nor an existing
        # trip_day supplies country / TZ. Days the user Skips stay
        # blank; the Days Table dialog below lets them fine-tune.
        self._prompt_phone_gps_stretches(
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

        Returns True only when an ingest ran to completion (the copy
        engine's success tail navigated); False on every cancel/abort
        path. The backfill wizard (spec/57 §4.3.1) uses False to fall
        back to a dashboard landing; Collect ignores the return.
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
        """Host :class:`QuickSweepPage` modally over the about-to-be-
        imported set (Nelson 2026-06-08 — Collect ingest-mode gate).

        Builds :class:`SourceItem` instances from the scan's per-photo
        records, filtered to **checked days only** + the quarantine
        bucket (untimestamped files always travel through so the user
        can still triage them). Returns the kept paths set, or ``None``
        if the user backed out / pressed Esc.
        """
        from PyQt6.QtWidgets import QDialog, QVBoxLayout
        from core.fresh_source import SourceItem
        from mira.ui.picked.quick_sweep_page import QuickSweepPage

        checked_dates = {r.date for r in edited_rows if r.checked}
        items = []
        for rec in scan.per_photo_records:
            day_date = (
                scan.day_date_lookup.get(rec.day_number)
                if rec.day_number is not None else None
            )
            # Include checked-day photos + quarantine (day_number is None).
            if day_date is not None and day_date not in checked_dates:
                continue
            items.append(SourceItem(
                path=rec.source_path,
                timestamp=rec.capture_time_raw,
                camera_id=rec.camera_id or "",
            ))
        if not items:
            return set()

        host = QDialog(self)
        host.setWindowTitle(tr("Quick Sweep — pick what to import"))
        host.setModal(True)
        host.resize(1100, 740)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        page = QuickSweepPage()
        layout.addWidget(page)

        result = {"kept": None}
        page.saved.connect(
            lambda kept: (result.__setitem__("kept", set(kept)), host.accept())
        )
        page.cancelled.connect(
            lambda: (result.__setitem__("kept", None), host.reject())
        )
        if not page.load(items):
            return set()
        page.setFocus()
        host.exec()
        return result["kept"]

    def _run_collect_copy_all(
        self, *, event_id, event_root, scan,
        edited_rows, edited_info, existing_info, existing_days,
        keep_only_paths=None, calibration_decisions=None,
        post_record=None, land_phase=None,
    ) -> bool:
        """End-to-end Copy-all ingest:

        1. Persist event-info edits (if changed).
        2. Assign day_numbers (existing keep theirs; fresh get max+1, +2, …).
        3. Build IngestPhotoJob list — only checked dates' photos.
        4. Run ``ingest_pipeline.run_ingest`` off-thread with progress.
        5. Write cameras, trip_days, and items to event.db — then call
           ``post_record()`` (the backfill wizard's level state writes,
           spec/57 §4.3) while still inside the progress dialog, so every
           surface that opens afterwards already sees the written states.
        6. Refresh dashboards + navigate to the event — then straight to
           ``land_phase``'s surface when given (the backfill wizard's
           landing, spec/57 §4.3). Returns True only when this success
           tail ran; False on every earlier abort.

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
        from mira.ui.base.progress import run_with_progress

        calibration_decisions = dict(calibration_decisions or {})

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

        # 4. Run the copy AND the event.db writes inside one progress
        # dialog. The db writes need sha256 per file, which is heavy and
        # would freeze the UI if it ran after the progress dialog closed
        # (Nelson 2026-06-08 eyeball: "progress disappeared but cursor
        # still busy and the program became non-responsive").
        #
        # ``run_with_progress`` runs work on the GUI thread but pumps
        # ``processEvents`` whenever the callback is invoked; emitting
        # per-photo from both phases keeps the UI alive.
        #
        # Signature adapter — run_with_progress is ``(done, total, message)``;
        # ingest_pipeline emits ``(message, current, total)``.
        ingest_result_holder: list = []

        def _do_full_ingest(report):
            def _adapter(message, current=0, total=0):
                report(current, total, message)
            # ``bake_corrections=False`` — CLAUDE.md invariant #7 + spec/52
            # §8.1: the captured tree is NEVER mutated. TZ correction is
            # correction-on-read via item.capture_time_corrected in
            # event.db, NOT via EXIF rewrite in the copies. The legacy
            # bake step (still the default in ``run_ingest``) is a
            # pre-rebuild holdover. Skipping it also halves the import
            # wall-clock (Nelson 2026-06-09 eyeball: "the process was
            # very slow … two passes").
            ir = run_ingest(
                jobs, event_root,
                bake_corrections=False, progress=_adapter,
            )
            ingest_result_holder.append(ir)
            self._record_collect_in_event_db(
                event_id=event_id, event_root=event_root,
                jobs=jobs, edited_rows=edited_rows,
                date_to_day_num=date_to_day_num,
                existing_day_nums=existing_day_nums,
                per_job_info=ir.per_job_info,
                calibration_decisions=calibration_decisions,
                progress=_adapter,
            )
            if post_record is not None:
                _adapter(tr("Writing phase states…"))
                post_record()
            return ir

        # "media", not "photos" — videos are first-class collect content
        # (Nelson 2026-06-10).
        ok, result = run_with_progress(
            self, tr("Importing media files…"), _do_full_ingest,
            label=tr("Copying {n} file(s)…").replace("{n}", str(len(jobs))),
        )
        if not ok:
            QMessageBox.critical(
                self, tr("Import failed"),
                tr("The import crashed:\n\n{err}").replace("{err}", str(result)),
            )
            return False
        ingest_result = result

        # 6. Refresh + navigate. The classification pass rides every
        # ingest (spec/58 §1 — media sits in the system long before
        # Edit); quiet, off-thread, idempotent.
        self.gateway.refresh_index_entry(event_id)
        self._spawn_classify_pass(event_id)
        warnings_line = ""
        if ingest_result.warnings:
            warnings_line = tr(
                "\n\n{w} warning(s) — check the log."
            ).replace("{w}", str(len(ingest_result.warnings)))
        ok_msg = QMessageBox(self)
        ok_msg.setWindowTitle(tr("Import complete"))
        ok_msg.setIcon(QMessageBox.Icon.NoIcon)
        ok_msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        # Capture-time correction lives on item.capture_time_corrected
        # in event.db (spec/52 §8.1); the original EXIF is never mutated,
        # so the "EXIF time(s) corrected" line that used to live here was
        # misleading (Nelson 2026-06-09).
        dup_line = ""
        if ingest_result.photos_duplicates:
            # Backfill sources often carry the same file in several
            # subtrees (legacy captured + selected copies) — say so
            # plainly instead of burying it in the log (spec/57 §4.3).
            dup_line = tr(
                " · {d} duplicate(s) ingested once"
            ).replace("{d}", str(ingest_result.photos_duplicates))
        ok_msg.setText(tr(
            "{copied} photo(s) copied · {quar} quarantined{dups}{warns}"
        )
        .replace("{copied}", str(ingest_result.photos_copied))
        .replace("{quar}", str(ingest_result.photos_quarantined))
        .replace("{dups}", dup_line)
        .replace("{warns}", warnings_line))
        ok_msg.exec()
        self._on_event_created(event_id)
        if land_phase and self._current_event_id == event_id:
            # Backfill wizard landing (spec/57 §4.3) — straight to the
            # level's phase surface, the dashboard beneath for Back.
            self._on_phase_activated(land_phase)
        return True

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
            "duration_value": ev.duration_value,
            "duration_unit": ev.duration_unit,
            "participants": participants,
            "context": ev.context,
            "experience_type": ev.experience_type,
            "creative_focus": creative_focus,
        }

        dlg = self._exec_event_header_dialog(EventHeaderDialog(
            existing_info=existing, parent=self))
        if not dlg:
            return
        edited = dlg.header_info()
        try:
            self.gateway.set_classification(
                event_id,
                event_type=edited.get("event_type"),
                event_subtype=edited.get("event_subtype") or "",
                description=edited.get("description") or "",
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

        dlg = self._exec_event_days_table_dialog(self._build_days_table_dialog(rows))
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

    def _prompt_phone_gps_stretches(
        self, rows, *, home_country: Optional[str],
        home_tz_minutes: Optional[int],
    ) -> None:
        """spec/64 §4.4 — for each consecutive stretch of rows missing
        country OR TZ (the phone didn't supply usable location info),
        prompt the user once with the home defaults as suggestions and
        apply their answer across the stretch. Rows the user Skips stay
        blank for the Days Table dialog to handle.

        Mutates ``rows`` in place — the row list is the same object the
        caller hands to the Days Table dialog next."""
        stretches = self._collect_phone_gps_stretches(rows)
        if not stretches:
            return
        from mira.ui.pages.phone_gps_stretch_dialog import (
            PhoneGpsStretchDialog,
        )
        for stretch in stretches:
            dlg = self._exec_phone_gps_stretch_dialog(PhoneGpsStretchDialog(
                dates=[r.date for r in stretch],
                initial_country=home_country,
                initial_tz_minutes=home_tz_minutes,
                parent=self,
            ))
            if not dlg:
                continue                                        # Skip
            country, tz_minutes = dlg.result_values()
            for r in stretch:
                if country:
                    r.country_code = country
                if tz_minutes is not None:
                    r.tz_minutes = tz_minutes

    @staticmethod
    def _collect_phone_gps_stretches(rows):
        """Walk ``rows`` and return a list of consecutive-row runs where
        country OR TZ is blank. Pure logic — no Qt, no I/O."""
        stretches = []
        current = []
        for r in rows:
            blank = (not (r.country_code or "").strip()) or (r.tz_minutes is None)
            if blank:
                current.append(r)
            elif current:
                stretches.append(current)
                current = []
        if current:
            stretches.append(current)
        return stretches

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
            if self.pick_page.open_event(self._current_event_id):
                self.page_stack.show_page(self._SELECT_PAGE_KEY)
            return
        if phase == "edit" and self._current_event_id is not None:
            # spec/57: entering Edit runs the external seams — scan for
            # tool results (§3.3), then (re)build the links projection
            # (§2.2) so the doorway is current the moment the parallel
            # tracks begin. Quiet unless something user-relevant happened.
            self._run_edit_entry_seams()
            if self.edit_page.open_event(self._current_event_id):
                self.page_stack.show_page(self._PROCESS_PAGE_KEY)
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
        """Back from the Select surface → the per-event phase grid (refreshed)."""
        if self._current_event_id is not None:
            self.phases_page.set_event(self._current_event_id)
        self.page_stack.show_page(self._ACTIVITY_PAGE_KEY)

    def _on_select_fullscreen(self, on: bool) -> None:
        """Immersive select: mirror the cull fullscreen behaviour."""
        self.menuBar().setVisible(not on)

    def _on_process_closed(self) -> None:
        """Back from the Process surface → the per-event phase grid (refreshed)."""
        if self._current_event_id is not None:
            self.phases_page.set_event(self._current_event_id)
        self.page_stack.show_page(self._ACTIVITY_PAGE_KEY)

    def _on_process_fullscreen(self, on: bool) -> None:
        """Immersive process: mirror the cull/select fullscreen behaviour."""
        self.menuBar().setVisible(not on)

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
