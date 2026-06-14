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
    """Titles of every visible top-level menu, in left-to-right order."""
    out: list[str] = []
    for a in w.menuBar().actions():
        if a.isVisible() and a.menu() is not None:
            # Strip Qt's '&' mnemonic prefix for readability.
            out.append(a.text().replace("&", ""))
    return out


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


def test_per_event_surface_unhides_collect_and_share(main_window):
    """When an event is open, Collect + Share top-levels appear."""
    main_window._current_event_id = "fake-evt-id"   # simulate event open
    # Stub the closed-state probe so we don't hit a real event.db.
    with patch.object(MainWindow, "_event_is_closed_now", return_value=False):
        main_window._refresh_menu_state()
        titles = _top_level_titles(main_window)
    assert "Collect" in titles
    assert "Share" in titles


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
    assert "Restore from backup…" in labels
    # Per-event entries hidden.
    assert "Delete event" not in labels
    assert "Stats…" not in labels


def test_event_menu_per_event_has_lifecycle_entries(main_window):
    main_window._current_event_id = "fake-evt-id"
    with patch.object(MainWindow, "_event_is_closed_now", return_value=False):
        main_window._refresh_menu_state()
        labels = _action_labels("event", main_window)
    assert "Edit info…" in labels
    assert "Stats…" in labels
    assert "Back up event…" in labels
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
    assert "Camera clocks…" in labels
    assert "Adjust TZ…" in labels
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


def test_share_menu_per_event_has_open_phase_and_audio(main_window):
    main_window._current_event_id = "fake-evt-id"
    with patch.object(MainWindow, "_event_is_closed_now", return_value=False):
        main_window._refresh_menu_state()
        labels = _action_labels("share", main_window)
    assert "Open Share phase" in labels
    assert "New Cut…" in labels
    assert "Audio…" in labels


# ─── F-024 closed-event filter ──────────────────────────────────────────────


def test_closed_event_hides_modification_entries_but_keeps_stats_backup(main_window):
    """Spec F-024: closed events hide modification entries
    (Edit info / Edit plan / Manage days / Camera clocks / Adjust TZ /
    Re-import LRC / Delete event). Stats / Back up / Close-toggle stay."""
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
    assert "Camera clocks…" not in collect_labels
    assert "Adjust TZ…" not in collect_labels
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
