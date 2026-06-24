"""spec/131 — anchor flows back up the dive stack.

* Picker / Editor ``_on_back`` snapshot the live cursor BEFORE
  ``_close_event`` clears state, so ``last_item_id`` / ``last_day_number``
  report the position the user was on at close.
* main_window's viewer-close handlers thread ``last_item_id`` into
  ``DaysGridPage.open_for_day(anchor_item_id=...)`` so the grid
  restores to the user's last position.
* main_window's grid-back handler threads ``current_day_number`` into
  ``DaysListsPage.setEventForPreview(anchor_day_number=...)`` so the
  list restores to the day the grid was last on (it may have used
  prev/next-day).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mira.gateway import Gateway
from mira.gateway.index import EventsIndex
from mira.settings.repo import SettingsRepo
from mira.store import models as m


# ── Picker: _on_back snapshots the cursor ──────────────────────────────


@pytest.fixture
def picker_page(qapp, tmp_path):
    from mira.ui.pages.picker_page import PickerPage
    settings = SettingsRepo(tmp_path / "settings.json")
    index = EventsIndex(tmp_path / "events_index.json")
    gw = Gateway(settings=settings, index=index)
    p = PickerPage(gw)
    yield p
    p.deleteLater()


def _stub_items(item_ids: list[str]) -> list:
    """Stand-ins for CullItem — only ``.item_id`` is read by the
    spec/131 accessors."""
    return [SimpleNamespace(item_id=iid) for iid in item_ids]


def test_picker_back_snapshots_current_item_id_and_day(picker_page):
    """``_on_back`` captures the live item id + day BEFORE
    ``_close_event`` clears them. Reads survive the close."""
    picker_page._items = _stub_items(["a", "b", "c", "d"])
    picker_page._index = 2
    picker_page._day_number = 4
    # current_* report the live cursor while loaded.
    assert picker_page.current_item_id() == "c"
    assert picker_page.current_day_number() == 4
    picker_page._on_back()
    # After back: live items list is cleared, but the snapshots
    # survive for the host's ``closed``-signal slot.
    assert picker_page._items == []
    assert picker_page.last_item_id() == "c"
    assert picker_page.last_day_number() == 4


def test_picker_back_snapshots_after_stepping_through_items(picker_page):
    """User pages from item B to item D and hits Back — last_item_id
    reports D, not the entry point B (the "last position" contract)."""
    picker_page._items = _stub_items(["a", "b", "c", "d"])
    picker_page._index = 1                              # B (entry)
    picker_page._day_number = 2
    # Step → C → D.
    picker_page._index = 2
    picker_page._index = 3
    picker_page._on_back()
    assert picker_page.last_item_id() == "d"
    assert picker_page.last_day_number() == 2


def test_picker_current_item_id_none_when_unloaded(picker_page):
    """Empty items list → current_item_id returns None (no crash)."""
    picker_page._items = []
    picker_page._index = 0
    assert picker_page.current_item_id() is None
    assert picker_page.current_day_number() is None


# ── Editor: _on_back snapshots the cursor ──────────────────────────────


@pytest.fixture
def editor_page(qapp, tmp_path):
    from mira.ui.pages.editor_page import EditorPage
    settings = SettingsRepo(tmp_path / "settings.json")
    index = EventsIndex(tmp_path / "events_index.json")
    gw = Gateway(settings=settings, index=index)
    p = EditorPage(gw)
    yield p
    p.deleteLater()


def test_editor_back_snapshots_current_item_id_and_day(editor_page):
    editor_page._items = _stub_items(["e1", "e2", "e3"])
    editor_page._index = 1
    editor_page._day_number = 7
    assert editor_page.current_item_id() == "e2"
    assert editor_page.current_day_number() == 7
    editor_page._on_back()
    assert editor_page._items == []
    assert editor_page.last_item_id() == "e2"
    assert editor_page.last_day_number() == 7


def test_editor_current_item_id_none_when_unloaded(editor_page):
    editor_page._items = []
    editor_page._index = 0
    assert editor_page.current_item_id() is None
    assert editor_page.current_day_number() is None


# ── main_window: thread anchors through the dive stack ─────────────────


@pytest.fixture
def main_window(qapp, tmp_path, monkeypatch):
    """A MainWindow against a tmp gateway; same pattern as the
    spec/126/127 menu tests so the EventsIndex doesn't cross-
    contaminate from the real user dir."""
    from mira.ui.shell.main_window import MainWindow
    user_data = tmp_path / "user_data"
    user_data.mkdir()
    base = tmp_path / "lib"
    base.mkdir()
    monkeypatch.setattr(
        "mira.paths.user_data_dir", lambda: user_data)
    monkeypatch.setattr(
        "core.settings.user_data_dir", lambda: user_data)
    monkeypatch.setattr(
        "mira.gateway.index.user_data_dir", lambda: user_data)
    monkeypatch.setattr(
        "mira.settings.repo.user_data_dir", lambda: user_data)
    gw = Gateway(
        settings=SettingsRepo(user_data / "settings.json"),
        index=EventsIndex(user_data / "events_index.json"),
        user_store_path=user_data / "mira.db",
    )
    _ = gw.user_store
    gw.settings.update(photos_base_path=str(base))
    w = MainWindow(gateway=gw)
    yield w
    w.deleteLater()


def test_picker_close_threads_anchor_into_grid_open_for_day(
    main_window, monkeypatch,
):
    """spec/131 host wiring — on viewer-close, the bridge calls
    ``days_grid_page.open_for_day(anchor_item_id=last_item_id)`` so the
    grid lands on the user's last position instead of the top."""
    w = main_window
    # Stage the bridge state the way ``_on_days_grid_item_activated``
    # would have left it before opening the Picker.
    w._days_grid_bridge_active = True
    # Stub the picker's last position (after the user paged through
    # items inside the Picker).
    w.picker_page._last_item_id = "p9"
    w.picker_page._last_day_number = 3

    # Capture how the host opens the grid.
    calls: list[dict] = []
    monkeypatch.setattr(
        w.days_grid_page, "open_for_day",
        lambda *a, **kw: (calls.append({"args": a, "kwargs": kw})
                          or True))
    monkeypatch.setattr(
        w.days_grid_page, "current_event_id", lambda: "evt-A")
    monkeypatch.setattr(
        w.days_grid_page, "current_day_number", lambda: 1)
    monkeypatch.setattr(
        w, "_lookup_day_meta", lambda _e, _d: ("Day 3", "2026-04-03"))
    # The page stack swap is the tail — stub so the grid doesn't try
    # to render against the empty gateway.
    monkeypatch.setattr(
        w.page_stack, "show_page", lambda *_: None)

    w._on_select_closed()

    assert len(calls) == 1
    call = calls[0]
    # Picker reported day=3, so the grid opens for day=3 (not the
    # grid's previous day=1).
    assert call["args"] == ("evt-A", 3)
    assert call["kwargs"].get("anchor_item_id") == "p9"
    # Bridge flag consumed.
    assert w._days_grid_bridge_active is False


def test_editor_close_threads_anchor_into_grid_open_for_day(
    main_window, monkeypatch,
):
    """Same host wiring on the Editor side."""
    w = main_window
    w._days_grid_bridge_active = True
    w.edit_page._last_item_id = "e7"
    w.edit_page._last_day_number = 2

    calls: list[dict] = []
    monkeypatch.setattr(
        w.days_grid_page, "open_for_day",
        lambda *a, **kw: (calls.append({"args": a, "kwargs": kw})
                          or True))
    monkeypatch.setattr(
        w.days_grid_page, "current_event_id", lambda: "evt-B")
    monkeypatch.setattr(
        w.days_grid_page, "current_day_number", lambda: 1)
    monkeypatch.setattr(
        w, "_lookup_day_meta", lambda _e, _d: ("Day 2", "2026-04-02"))
    monkeypatch.setattr(
        w.page_stack, "show_page", lambda *_: None)

    w._on_process_closed()

    assert len(calls) == 1
    call = calls[0]
    assert call["args"] == ("evt-B", 2)
    assert call["kwargs"].get("anchor_item_id") == "e7"
    assert w._days_grid_bridge_active is False


def test_grid_back_threads_day_anchor_into_days_lists(
    main_window, monkeypatch,
):
    """spec/131 host wiring — on grid-back, the days_list rebuild is
    given ``anchor_day_number=current_day_number`` so the list scrolls
    to + highlights the day the grid was last on (which may be
    different from the entry day thanks to prev/next-day)."""
    w = main_window
    w._current_event_id = "evt-D"
    # Grid moved to day 5 via prev/next-day before backing out.
    monkeypatch.setattr(
        w.days_grid_page, "current_day_number", lambda: 5)
    monkeypatch.setattr(
        w.days_grid_page, "close_event", lambda: None)

    captured: list[dict] = []
    monkeypatch.setattr(
        w, "_open_days_lists_for",
        lambda eid, *, anchor_day_number=None:
            captured.append({"event_id": eid,
                             "anchor": anchor_day_number}))

    w._on_days_grid_back()

    assert captured == [{"event_id": "evt-D", "anchor": 5}]
