"""Menu bar structure tests (Nelson 2026-06-09 design — App / Event /
Collect / Pick / Edit / Share / Help).

Covers:
* Top-level menu set is exactly the seven from the design.
* Old top-levels (File, View, Events, Plan, Process, Curate) are gone.
* Children differ correctly between surfaces (events list vs. per-event).
* Collect + Share hide on the events list (empty children → top-level
  hide rule).
* Library, Audit, phase ``Open …`` entries are per-event-only.
* Standalone Picker / Quick Sweep / Photo Processor / Restore / creation
  entries are events-list-only.
* Closed-event filter hides modification entries while leaving Stats /
  Back up / Close-toggle visible.

Tests do not exec the actions — they just instantiate ``MainWindow`` and
inspect the QAction tree. The ``qapp`` fixture (conftest) provides the
QApplication.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from mira.gateway import Gateway
from mira.ui.shell.main_window import MainWindow


# ─── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def main_window(qapp, tmp_path, monkeypatch):
    """Build a MainWindow against a tmp gateway. Stubs SettingsRepo's
    photos_base_path so the first-run prompt doesn't fire under the
    autouse modal-blocker neutraliser (conftest)."""
    # The Gateway needs a writable user-data dir; tmp_path keeps tests
    # isolated. Patch the user_data_dir resolver before Gateway init.
    base = tmp_path / "lib"
    base.mkdir()
    monkeypatch.setattr(
        "mira.paths.user_data_dir",
        lambda: tmp_path / "user_data",
    )
    monkeypatch.setattr(
        "core.settings.user_data_dir",
        lambda: tmp_path / "user_data",
    )
    gw = Gateway()
    # Ensure events_index resolves cleanly.
    try:
        gw.settings_repo.save(
            gw.settings_repo.load().__class__(
                **{**gw.settings_repo.load().to_dict(),
                   "photos_base_path": str(base)}
            )
        )
    except Exception:
        pass

    w = MainWindow(gateway=gw)
    yield w
    w.deleteLater()


# ─── helpers ─────────────────────────────────────────────────────────────────


def _top_level_titles(w: MainWindow) -> list[str]:
    """Titles of every visible top-level menu, in declared (left-to-right) order.

    Reads ``w._menus`` directly instead of ``w.menuBar().actions()``:
    once ``MainWindow`` installs the redesign TitleBar via
    ``setMenuWidget`` (see ``_install_title_bar``), the native menu bar
    no longer surfaces the menu actions, but the menus themselves stay
    alive on the dict and ``_refresh_menu_state`` continues to drive
    their ``menuAction().setVisible(...)`` per the empty-children rule.
    """
    return [
        menu.title().replace("&", "")
        for menu in w._menus.values()
        if menu.menuAction().isVisible()
    ]


def _action_labels(menu_name: str, w: MainWindow) -> list[str]:
    """Labels of visible non-separator children in the given top-level menu."""
    menu = w._menus[menu_name]
    return [
        a.text().replace("&", "")
        for a in menu.actions()
        if a.isVisible() and not a.isSeparator()
    ]


# ─── top-level structure ─────────────────────────────────────────────────────


def test_top_level_menus_are_the_designed_seven(main_window):
    """The menu bar shows exactly: App · Event · Pick · Edit · Help
    (Collect + Share hide on the events list per the empty-children rule)."""
    titles = _top_level_titles(main_window)
    assert "App" in titles
    assert "Event" in titles
    assert "Pick" in titles
    assert "Edit" in titles
    assert "Help" in titles
    # Collect + Share have no children on the events-list surface.
    assert "Collect" not in titles
    assert "Share" not in titles
    # Old top-levels must be gone.
    for old in ("File", "View", "Events", "Plan", "Process", "Curate"):
        assert old not in titles, f"old top-level still present: {old}"


def test_per_event_surface_unhides_collect(main_window):
    """When an event is open, the Collect top-level appears (it's
    empty / hidden on the events list).

    Share is NOT asserted here — spec/66 made it a closed-event STATE,
    so on an open event the empty-children rule keeps it hidden.
    ``test_share_menu_visible_on_closed_event`` covers the closed
    branch; ``test_share_menu_hidden_when_event_open`` covers the
    open branch."""
    main_window._current_event_id = "fake-evt-id"   # simulate event open
    # Stub the closed-state probe so we don't hit a real event.db.
    with patch.object(MainWindow, "_event_is_closed_now", return_value=False):
        main_window._refresh_menu_state()
        titles = _top_level_titles(main_window)
    assert "Collect" in titles
    assert "Share" not in titles


# ─── App menu surface-dependent children ────────────────────────────────────


def test_app_menu_hides_library_on_events_list(main_window):
    """Library is per-event-only (returns to the events list — nothing
    to return to when you're already there)."""
    labels = _action_labels("app", main_window)
    assert "Library" not in labels
    # Wizard / Settings / Quit are everywhere.
    assert "Wizard…" in labels
    assert "Settings…" in labels
    assert "Quit" in labels


def test_app_menu_shows_library_and_audit_per_event(main_window):
    main_window._current_event_id = "fake-evt-id"
    with patch.object(MainWindow, "_event_is_closed_now", return_value=False):
        main_window._refresh_menu_state()
        labels = _action_labels("app", main_window)
    assert "Library" in labels
    assert "Audit…" in labels


# ─── Event menu surface-dependent children ──────────────────────────────────


def test_event_menu_events_list_has_creation_entries(main_window):
    labels = _action_labels("event", main_window)
    assert "New event" in labels
    assert "New event from existing media…" in labels
    # Per-event entries hidden.
    assert "Delete event" not in labels
    assert "Stats…" not in labels
    # spec/82 §A.4 — Restore from backup is per-event (needs a current
    # event in context); not on the events list.
    assert "Restore from backup…" not in labels


def test_event_menu_per_event_has_lifecycle_entries(main_window):
    main_window._current_event_id = "fake-evt-id"
    with patch.object(MainWindow, "_event_is_closed_now", return_value=False):
        main_window._refresh_menu_state()
        labels = _action_labels("event", main_window)
    assert "Edit info…" in labels
    assert "Stats…" in labels
    assert "Back up event…" in labels
    # spec/82 §A.4 — manual restore lives on the per-event surface.
    assert "Restore from backup…" in labels
    assert "Close Event" in labels
    assert "Delete event" in labels
    # Cross-event creation hidden.
    assert "New event" not in labels


# ─── Pick + Edit surface-dependent children ─────────────────────────────────


def test_pick_menu_events_list_has_standalones(main_window):
    labels = _action_labels("pick", main_window)
    assert "Standalone Picker…" in labels
    assert "Standalone Quick Sweep…" in labels
    assert "Open Pick phase" not in labels
    assert "Quick Sweep this event…" not in labels


def test_pick_menu_per_event_has_open_and_in_event_quick_sweep(main_window):
    main_window._current_event_id = "fake-evt-id"
    with patch.object(MainWindow, "_event_is_closed_now", return_value=False):
        main_window._refresh_menu_state()
        labels = _action_labels("pick", main_window)
    assert "Open Pick phase" in labels
    assert "Quick Sweep this event…" in labels
    assert "Standalone Picker…" not in labels


def test_edit_menu_per_event_has_open_phase(main_window):
    main_window._current_event_id = "fake-evt-id"
    with patch.object(MainWindow, "_event_is_closed_now", return_value=False):
        main_window._refresh_menu_state()
        labels = _action_labels("edit", main_window)
    assert "Open Edit phase" in labels
    assert "Standalone Photo Processor…" not in labels


# ─── Collect + Share children (per-event only) ──────────────────────────────


def test_collect_menu_per_event_has_edit_event_and_edit_plan_and_tz(main_window):
    """Collect has two distinct editing entries (Nelson 2026-06-09):
    Edit Event opens the unified info+plan dialog (same as Event→Edit
    info); Edit plan opens the plan-only editor with Save/Load CSV +
    Delete-day."""
    main_window._current_event_id = "fake-evt-id"
    with patch.object(MainWindow, "_event_is_closed_now", return_value=False):
        main_window._refresh_menu_state()
        labels = _action_labels("collect", main_window)
    assert "Edit Event…" in labels
    assert "Edit plan…" in labels
    assert "Manage days…" in labels
    # spec/127 — the two old menu items ("Camera clocks…" + "Adjust
    # TZ…") merged into one unified handler / single menu item.
    assert "Camera Clock Correction…" in labels
    assert "Camera clocks…" not in labels
    assert "Adjust TZ…" not in labels
    assert "Re-import from LRC…" in labels


def test_collect_edit_event_and_edit_plan_are_separate_actions(main_window):
    """Verify that Edit Event and Edit plan are distinct QActions under
    Collect. The bound-method closures captured by ``_add_menu_action``
    can't be easily intercepted post-hoc, so we assert the two actions
    coexist (different handlers); a live eyeball confirms the dialogs
    they open are also distinct."""
    main_window._current_event_id = "fake-evt-id"
    with patch.object(MainWindow, "_event_is_closed_now", return_value=False):
        main_window._refresh_menu_state()
    collect_menu = main_window._menus["collect"]
    labels = [
        a.text().replace("&", "")
        for a in collect_menu.actions()
        if a.isVisible() and not a.isSeparator()
    ]
    assert "Edit Event…" in labels
    assert "Edit plan…" in labels
    # Two distinct actions — different object identity, distinct positions
    # in the menu.
    edit_event = next(
        a for a in collect_menu.actions()
        if a.text().replace("&", "") == "Edit Event…")
    edit_plan = next(
        a for a in collect_menu.actions()
        if a.text().replace("&", "") == "Edit plan…")
    assert edit_event is not edit_plan


def test_share_menu_hidden_when_event_open(main_window):
    """spec/66 §4 — Share is a closed-event STATE (Cuts assembly on
    closed events). When the open event is still active, the Share
    menu items hide; the empty-children rule then hides the whole
    top-level."""
    main_window._current_event_id = "fake-evt-id"
    with patch.object(MainWindow, "_event_is_closed_now", return_value=False):
        main_window._refresh_menu_state()
        labels = _action_labels("share", main_window)
    assert "Open Cuts" not in labels
    assert "New Cut…" not in labels
    assert "Audio…" not in labels


def test_share_menu_visible_on_closed_event(main_window):
    """spec/66 §4 — closed events unlock the Share menu so the user can
    assemble Cuts from the shipped finals."""
    main_window._current_event_id = "fake-evt-id"
    with patch.object(MainWindow, "_event_is_closed_now", return_value=True):
        main_window._refresh_menu_state()
        labels = _action_labels("share", main_window)
    assert "Open Cuts" in labels
    assert "New Cut…" in labels
    assert "Audio…" in labels


def test_export_menu_visible_per_event(main_window):
    """spec/66 §1.1 — Export is its own phase. The menu's keyboard door
    appears whenever an event is open."""
    main_window._current_event_id = "fake-evt-id"
    with patch.object(MainWindow, "_event_is_closed_now", return_value=False):
        main_window._refresh_menu_state()
        labels = _action_labels("export", main_window)
    assert "Open Export phase" in labels


# ─── Export route (spec/68 §3) ──────────────────────────────────────────────


def test_export_tile_routes_through_days_lists(main_window):
    """spec/68 §3 — Phases Export tile no longer opens a standalone
    flat-grid surface. It sets ``_export_phase_active`` and routes
    through ``_open_days_lists_for`` (same as Pick / Edit), so the
    user lands on the per-day spine the rest of the phases use."""
    main_window._current_event_id = "fake-evt-id"
    assert main_window._export_phase_active is False
    with patch.object(
            MainWindow, "_open_days_lists_for",
            autospec=True) as opener:
        main_window._on_phase_activated("export")
    assert main_window._export_phase_active is True
    opener.assert_called_once_with(main_window, "fake-evt-id")


def test_days_lists_identity_export_when_phase_active(main_window):
    """When ``_export_phase_active`` is set, opening Days Lists hands
    the page the ``"export"`` identity (green rail + EXPORT badge per
    spec/71)."""
    main_window._current_event_id = "fake-evt-id"
    main_window._export_phase_active = True
    captured: list[str] = []
    with patch.object(
            main_window.days_lists_page, "set_phase_identity",
            side_effect=captured.append):
        with patch.object(
                MainWindow, "_build_day_snapshots", return_value=[]):
            main_window._open_days_lists_for("fake-evt-id")
    assert captured == ["export"]


def test_all_days_export_now_does_not_close_eg_after_submit(
        main_window, tmp_path, monkeypatch):
    """spec/89 §11.3 lifecycle fix (Nelson 2026-06-19) —
    MainWindow._on_days_lists_export_now used to close the second-
    pass EventGateway in a finally block right after enqueueing the
    batch. The batch_queue's commit closure runs asynchronously and
    retains a reference to ``eg`` for set_edit_exported / lineage
    writes, so the close raced the closure and produced
    sqlite3.ProgrammingError("Cannot operate on a closed database")
    on every shipped unit. The fix: close eg only when NO batch was
    submitted (delete-only run, or every submit failed); otherwise
    let Python GC reap eg once the closures release their refs."""
    from unittest.mock import MagicMock
    from mira.ui.exported.batch import ExportCell

    main_window._current_event_id = "fake-evt-id"
    # Stub the trip-day scratch path — we only care about the eg
    # lifecycle around the batch submit.
    scratch_eg = MagicMock()
    scratch_eg.event.return_value = MagicMock(name="Alaska")
    scratch_eg.trip_days.return_value = [MagicMock(day_number=1)]
    # The second-pass eg — the one the bug closed too early.
    run_eg = MagicMock()
    open_calls: list = []

    def _open_event(_eid):
        open_calls.append(_eid)
        return scratch_eg if len(open_calls) == 1 else run_eg
    monkeypatch.setattr(
        main_window.gateway, "open_event", _open_event)

    scratch_plan = {
        "render_cells": [ExportCell(
            item_id="iid", path=tmp_path / "x.jpg", day_number=1)],
        "render_segments": [],
        "render_snapshots": [],
        "delete_relpaths": [],
    }
    with patch(
            "mira.ui.pages.days_grid_page.DaysGridPage."
            "_collect_export_run_plan",
            return_value=scratch_plan), \
            patch(
                "mira.ui.pages.days_grid_page.DaysGridPage."
                "open_for_day",
                return_value=True), \
            patch(
                "mira.ui.pages.days_grid_page.DaysGridPage."
                "close_event"), \
            patch("mira.ui.design.confirm",
                  return_value=True), \
            patch(
                "mira.ui.exported.batch.submit_export_batch",
                return_value=True) as submit, \
            patch.object(main_window, "batch_queue", MagicMock()), \
            patch.object(main_window, "_open_days_lists_for"):
        main_window._on_days_lists_export_now()
    assert submit.call_count == 1
    # The second-pass eg's close MUST NOT have been called — the
    # batch's commit closure needs it alive.
    assert run_eg.close.call_count == 0


def test_all_days_export_now_closes_eg_on_delete_only_run(
        main_window, tmp_path, monkeypatch):
    """Twin of the above: when the plan has ONLY deletes (no render
    submits), the eg is closed immediately — no closure to wait for."""
    from unittest.mock import MagicMock

    main_window._current_event_id = "fake-evt-id"
    scratch_eg = MagicMock()
    scratch_eg.event.return_value = MagicMock(name="Alaska")
    scratch_eg.trip_days.return_value = [MagicMock(day_number=1)]
    run_eg = MagicMock()
    open_calls: list = []

    def _open_event(_eid):
        open_calls.append(_eid)
        return scratch_eg if len(open_calls) == 1 else run_eg
    monkeypatch.setattr(
        main_window.gateway, "open_event", _open_event)

    # Delete-only plan: 1 relpath to unlink, no renders.
    scratch_plan = {
        "render_cells": [],
        "render_segments": [],
        "render_snapshots": [],
        "delete_relpaths": ["Exported Media/x-skip.jpg"],
    }
    with patch(
            "mira.ui.pages.days_grid_page.DaysGridPage."
            "_collect_export_run_plan",
            return_value=scratch_plan), \
            patch(
                "mira.ui.pages.days_grid_page.DaysGridPage."
                "open_for_day",
                return_value=True), \
            patch(
                "mira.ui.pages.days_grid_page.DaysGridPage."
                "close_event"), \
            patch("mira.ui.design.confirm",
                  return_value=True), \
            patch(
                "mira.ui.exported.batch.submit_export_batch",
                return_value=True) as submit, \
            patch.object(main_window, "batch_queue", MagicMock()), \
            patch.object(main_window, "_open_days_lists_for"):
        main_window._on_days_lists_export_now()
    assert submit.call_count == 0
    # Delete-only path closes eg immediately.
    assert run_eg.close.call_count == 1
    assert run_eg.delete_exported_file_by_relpath.call_count == 1


def test_days_grid_item_activated_opens_editor_in_export_mode(
        main_window):
    """spec/89 §3.2 D4.C (Nelson 2026-06-19) — in Export mode the
    ``item_activated`` signal only fires from the preview viewer's
    "Open in Editor" button (thumb clicks toggle in place via
    DaysGridPage._on_thumb_clicked → _apply_verb_at_index /
    _open_export_preview, NEVER emit the signal). So the host routes
    item_activated to the Editor, opening by item (NOT by cluster —
    versions-cluster buckets carry lineage relpaths / virtual Mira
    ids, and the dialog already resolved them back to the real
    source item_id). Pre-fix this short-circuited as a no-op, which
    left the dialog closing without opening the Editor."""
    main_window._current_event_id = "fake-evt-id"
    main_window._export_phase_active = True
    main_window._edit_phase_active = False
    # Stub current_event_id / current_day_number so the handler
    # short-circuits before reaching the gateway.
    with patch.object(
            main_window.days_grid_page, "current_event_id",
            return_value="fake-evt-id"), \
            patch.object(
                main_window.days_grid_page, "current_day_number",
                return_value=3), \
            patch.object(
                main_window.picker_page, "open_to_item",
                return_value=True) as picker_open, \
            patch.object(
                main_window.edit_page, "open_to_item",
                return_value=True) as edit_open, \
            patch(
                "mira.ui.base.progress.run_with_progress",
                return_value=(True, True)) as run_prog, \
            patch.object(
                main_window.page_stack, "show_page") as show_page:
        main_window._on_days_grid_item_activated("x42")
    # The Picker is never touched in Export mode.
    assert picker_open.call_count == 0
    # The Editor opens by item — single keeper, bypassing the
    # cluster route (versions-cluster members are lineage relpaths).
    assert run_prog.call_count == 1
    # The page stack switches to the Editor page.
    assert show_page.call_count == 1
    assert show_page.call_args[0][0] == main_window._PROCESS_PAGE_KEY
    # The bridge flag is set so Back from Editor returns to the
    # Days Grid.
    assert main_window._days_grid_bridge_active is True


def test_export_phase_active_clears_on_days_lists_back(main_window):
    """Back from Days Lists clears the Export flag — same lifecycle
    contract as ``_edit_phase_active``."""
    main_window._current_event_id = "fake-evt-id"
    main_window._export_phase_active = True
    main_window._on_days_lists_back()
    assert main_window._export_phase_active is False


# ─── F-024 closed-event filter ──────────────────────────────────────────────


def test_closed_event_hides_modification_entries_but_keeps_stats_backup(main_window):
    """Spec F-024: closed events hide modification entries
    (Edit info / Edit plan / Manage days / Camera Clock Correction /
    Re-import LRC / Delete event). Stats / Back up / Close-toggle stay.

    spec/127 — "Camera clocks…" + "Adjust TZ…" merged into the unified
    "Camera Clock Correction…" entry; the closed-event filter still
    hides it."""
    main_window._current_event_id = "fake-evt-id"
    with patch.object(MainWindow, "_event_is_closed_now", return_value=True):
        main_window._refresh_menu_state()
        event_labels = _action_labels("event", main_window)
        collect_labels = _action_labels("collect", main_window)

    # Modification entries — hidden.
    assert "Edit info…" not in event_labels
    assert "Delete event" not in event_labels
    assert "Edit plan…" not in collect_labels
    assert "Manage days…" not in collect_labels
    assert "Camera Clock Correction…" not in collect_labels
    assert "Re-import from LRC…" not in collect_labels

    # Survives the filter — Stats / Back up / Close-toggle.
    assert "Stats…" in event_labels
    assert "Back up event…" in event_labels
    assert "Re-open Event" in event_labels       # label-swap when closed


def test_close_toggle_label_swaps_on_open_event(main_window):
    main_window._current_event_id = "fake-evt-id"
    with patch.object(MainWindow, "_event_is_closed_now", return_value=False):
        main_window._refresh_menu_state()
        labels = _action_labels("event", main_window)
    assert "Close Event" in labels
    assert "Re-open Event" not in labels


def test_collect_hides_entirely_when_event_closed(main_window):
    """Every Collect entry is modification → on a closed event the
    Collect top-level becomes empty and hides per the rule."""
    main_window._current_event_id = "fake-evt-id"
    with patch.object(MainWindow, "_event_is_closed_now", return_value=True):
        main_window._refresh_menu_state()
        titles = _top_level_titles(main_window)
    assert "Collect" not in titles
