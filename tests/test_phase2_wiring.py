"""spec/81 Phase 2 polish — lifecycle wiring tests.

The Item 1-6 builds shipped engines + UI; this file proves the wiring that
ties them together at runtime:

* :meth:`EventGateway.close` invokes the injected ``on_close`` callable
  (spec/81 Phase 2 Item 1 sync hook).
* :meth:`Gateway.open_event` installs an ``on_close`` that re-projects the
  event's items into ``global_items`` on close.
* :meth:`Gateway.reconcile_global_items` runs the startup catch-up.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.store import models as sm
from mira.store.repo import EventStore
from mira.user_store import models as um
from mira.user_store.repo import UserStore


NOW = "2026-06-16T00:00:00+00:00"


# --------------------------------------------------------------------------- #
# EventGateway.close hook
# --------------------------------------------------------------------------- #


def _bare_event_store(tmp_path, *, eid="evt") -> EventStore:
    store = EventStore.create(
        tmp_path / f"{eid}.db",
        event_id=eid, app_version="test", created_at=NOW,
    )
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO event (id, uuid, name, created_at, updated_at) "
            "VALUES (1, ?, 'Test', ?, ?)", (eid, NOW, NOW))
    return store


def _mark_dirty(eg: EventGateway) -> None:
    """Force the session's dirty bit by writing a no-op UPDATE that
    still advances ``connection.total_changes``. The on_close sync
    hook + the snapshot are both gated on ``total_changes >
    _changes_at_open``, so tests covering the hook firing need an
    in-session write."""
    eg.store.conn.execute(
        "UPDATE event SET updated_at = updated_at WHERE id = 1")


def test_event_gateway_close_invokes_on_close_when_dirty(tmp_path):
    """The on_close callable fires once on close, BEFORE the store is
    closed — the hook sees a live connection. Dirty-gated: a session
    that wrote something gets its hook called."""
    store = _bare_event_store(tmp_path)
    fired = []

    def _hook(eg):
        # Connection is still alive at hook time.
        rows = eg.store.conn.execute("SELECT name FROM event").fetchall()
        fired.append(rows[0]["name"])

    eg = EventGateway(store, now=lambda: NOW, on_close=_hook)
    _mark_dirty(eg)
    eg.close()
    assert fired == ["Test"]


def test_event_gateway_close_skips_on_close_when_clean(tmp_path):
    """A read-only open (no writes via the session) → on_close hook
    does NOT fire. The cross-event projection is already correct for
    the on-disk state; re-projecting would write identical rows. This
    is the fix that quietens the events-dashboard log noise — every
    card-data open + close was firing sync_event for no benefit."""
    store = _bare_event_store(tmp_path)
    fired = []

    def _hook(_eg):
        fired.append(True)

    eg = EventGateway(store, now=lambda: NOW, on_close=_hook)
    # No mutator called → total_changes unchanged → dirty=False.
    eg.close()
    assert fired == []


def test_event_gateway_close_hook_failure_does_not_block_close(tmp_path):
    """A hook that raises does not propagate — close still succeeds, the
    store still closes (charter: a stuck close is worse than a missed
    sync). Mark dirty so the hook actually runs."""
    store = _bare_event_store(tmp_path)

    def _angry(_eg):
        raise RuntimeError("nope")

    eg = EventGateway(store, now=lambda: NOW, on_close=_angry)
    _mark_dirty(eg)
    eg.close()                                         # should not raise
    # The store was actually closed.
    import sqlite3
    with pytest.raises(sqlite3.ProgrammingError):
        store.conn.execute("SELECT 1")


def test_event_gateway_close_without_hook_works_unchanged(tmp_path):
    """No hook → no behaviour change (legacy paths). Dirty or clean,
    same outcome — the store closes."""
    store = _bare_event_store(tmp_path)
    eg = EventGateway(store, now=lambda: NOW)
    eg.close()
    import sqlite3
    with pytest.raises(sqlite3.ProgrammingError):
        store.conn.execute("SELECT 1")


# --------------------------------------------------------------------------- #
# Gateway.open_event installs the projection-sync hook
# --------------------------------------------------------------------------- #


def _make_umbrella(tmp_path, *, with_events=True):
    """Build the umbrella Gateway pointing at tmp_path for everything."""
    from mira.gateway.gateway import Gateway
    from mira.gateway.index import EventsIndex
    from mira.settings.repo import SettingsRepo

    settings_path = tmp_path / "settings.json"
    index_path = tmp_path / "events_index.json"
    user_store_path = tmp_path / "mira.db"
    photos_base = tmp_path / "photos"
    photos_base.mkdir(exist_ok=True)
    settings = SettingsRepo(settings_path)
    index = EventsIndex(index_path)

    gw = Gateway(
        settings=settings,
        index=index,
        user_store_path=user_store_path,
        now=lambda: NOW,
        installation_profile="XMC",
    )
    # Prime the user_store FIRST — first-access triggers import_legacy_state
    # which retires the legacy JSON files (settings + events_index) by
    # renaming them with a ``.imported`` suffix. We then write settings +
    # index AFTER priming so they survive into the live run.
    _ = gw.user_store
    settings.update(photos_base_path=str(photos_base))
    return gw, photos_base


def _seed_event(photos_base: Path, *, name: str = "Test event",
                eid: str = "evt-1") -> Path:
    """Build an event.db with one item so the projection sync has rows
    to write."""
    root = photos_base / name
    root.mkdir(exist_ok=True)
    store = EventStore.create(
        root / "event.db",
        event_id=eid, app_version="test", created_at=NOW,
    )
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO event (id, uuid, name, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?)", (eid, name, NOW, NOW))
        conn.execute(
            "INSERT INTO trip_day (day_number, date) VALUES (1, '2026-04-01')")
        conn.execute(
            "INSERT INTO camera (camera_id) VALUES ('cam')")
        conn.execute(
            "INSERT INTO item (id, kind, provenance, origin_relpath, sha256, "
            "byte_size, materialized_at, materialized_phase, camera_id, "
            "day_number, capture_time_raw, capture_time_corrected, "
            "tz_offset_minutes, tz_source, extras_json, created_at) "
            "VALUES ('it-1', 'photo', 'captured', 'p.jpg', ?, 1, ?, 'ingest', "
            "'cam', 1, ?, ?, -360, 'pair_picker', '{}', ?)",
            ("a" * 64, NOW, "2026-04-01T10:00:00", "2026-04-01T04:00:00", NOW))
    store.close()
    return root


def _register_event(gw, photos_base: Path, root: Path,
                    *, eid: str, name: str) -> None:
    """Add the event to the index so open_event resolves it."""
    from mira.gateway.index import make_entry
    entry = make_entry(
        event_id=eid, name=name,
        start_date=None, end_date=None, is_closed=False,
        event_root=root,
        photos_base_path=photos_base,
    )
    gw.index.upsert(entry)


def test_open_event_installs_sync_hook(tmp_path):
    """``open_event`` builds an EventGateway whose close re-projects the
    event's items into ``global_items`` — when the session was dirty.
    A read-only open skips the projection (see the dirty-gate test
    below)."""
    gw, photos_base = _make_umbrella(tmp_path)
    root = _seed_event(photos_base, name="Test event", eid="evt-1")
    _register_event(gw, photos_base, root, eid="evt-1", name="Test event")

    # Open + write + close.
    eg = gw.open_event("evt-1")
    _mark_dirty(eg)
    eg.close()

    # global_items was populated by the close hook.
    rows = gw.user_store.query_by(um.GlobalItem, event_uuid="evt-1")
    assert len(rows) == 1
    assert rows[0].item_id == "it-1"
    assert rows[0].event_name == "Test event"
    gw.close()


def test_open_event_read_only_session_skips_sync(tmp_path):
    """A read-only open (events-dashboard card-data walk) does NOT
    fire sync_event on close — quietens the per-event sync log lines
    the dashboard was emitting on every refresh."""
    gw, photos_base = _make_umbrella(tmp_path)
    root = _seed_event(photos_base, name="Quiet", eid="evt-quiet")
    _register_event(gw, photos_base, root, eid="evt-quiet", name="Quiet")

    eg = gw.open_event("evt-quiet")
    # Pure reads — no _mark_dirty().
    _ = eg.trip_days()
    _ = eg.day_tree()
    eg.close()

    # Sync never ran — global_items has no rows for this event.
    rows = gw.user_store.query_by(um.GlobalItem, event_uuid="evt-quiet")
    assert rows == []
    gw.close()


def test_open_event_sync_hook_handles_missing_event_row(tmp_path):
    """An event.db without an ``event`` singleton row → sync hook returns
    silently (the close path never raises). Mark dirty so the hook
    actually runs and exercises the missing-row branch."""
    gw, photos_base = _make_umbrella(tmp_path)
    root = photos_base / "Empty"
    root.mkdir()
    store = EventStore.create(
        root / "event.db",
        event_id="evt-empty", app_version="test", created_at=NOW,
    )
    store.close()
    _register_event(gw, photos_base, root, eid="evt-empty", name="Empty")
    eg = gw.open_event("evt-empty")
    # Force the dirty gate so the hook actually runs and we test its
    # missing-event-row resilience (not just that dirty=False short-
    # circuited it).
    eg.store.conn.execute("UPDATE schema_info SET app_version = app_version")
    eg.close()                                              # must not raise
    # No global_items row was written (no event row → no projection).
    rows = gw.user_store.query_by(um.GlobalItem, event_uuid="evt-empty")
    assert rows == []
    gw.close()


# --------------------------------------------------------------------------- #
# Gateway.reconcile_global_items — startup catchup
# --------------------------------------------------------------------------- #


def test_reconcile_syncs_every_event_in_index(tmp_path):
    """``reconcile_global_items`` walks the events index, opens each event's
    store, and projects it into ``global_items``."""
    gw, photos_base = _make_umbrella(tmp_path)
    r1 = _seed_event(photos_base, name="E1", eid="e1")
    r2 = _seed_event(photos_base, name="E2", eid="e2")
    _register_event(gw, photos_base, r1, eid="e1", name="E1")
    _register_event(gw, photos_base, r2, eid="e2", name="E2")

    summary = gw.reconcile_global_items()

    assert summary["synced"] == 2
    assert summary["dropped"] == 0
    rows = gw.user_store.all(um.GlobalItem)
    assert {r.event_uuid for r in rows} == {"e1", "e2"}
    gw.close()


def test_reconcile_drops_stale_event_slices(tmp_path):
    """An event no longer in the index → its global_items slice is
    dropped during reconcile."""
    gw, photos_base = _make_umbrella(tmp_path)
    r1 = _seed_event(photos_base, name="Live", eid="live")
    _register_event(gw, photos_base, r1, eid="live", name="Live")

    # Seed a stale slice — an event_uuid not in the index.
    gw.user_store.upsert(um.GlobalItem(
        event_uuid="ghost", item_id="x", synced_at=NOW))

    summary = gw.reconcile_global_items()
    assert summary["synced"] == 1
    assert summary["dropped"] == 1
    assert gw.user_store.query_by(um.GlobalItem, event_uuid="ghost") == []
    assert gw.user_store.query_by(um.GlobalItem, event_uuid="live")
    gw.close()


def test_reconcile_skips_unresolvable_events_without_raising(tmp_path):
    """An event whose root can't be resolved (relocated, gone) is skipped
    + logged, never raised."""
    gw, photos_base = _make_umbrella(tmp_path)
    # Register an event whose root doesn't actually contain an event.db.
    ghost_root = photos_base / "Gone"
    ghost_root.mkdir()
    _register_event(gw, photos_base, ghost_root, eid="gone", name="Gone")
    summary = gw.reconcile_global_items()
    assert summary["synced"] == 0
    assert summary["skipped"] == 1
    gw.close()


# --------------------------------------------------------------------------- #
# Events page wires + Collection → dialog → create_dc
# --------------------------------------------------------------------------- #


def test_events_page_new_dc_signal_opens_list_dialog(qapp, tmp_path,
                                                    monkeypatch):
    """Clicking + Collection on the band opens
    :class:`CrossEventDcsDialog` (the BROWSE surface). The list dialog
    contains a + New collection button that opens
    :class:`NewCrossEventDcDialog` — that path is covered by
    :mod:`tests.test_cross_event_dcs_dialog`."""
    from mira.ui.pages.cross_event_dcs_dialog import CrossEventDcsDialog
    from mira.ui.pages.events_page import EventsPage

    gw, photos_base = _make_umbrella(tmp_path)

    captured: list = []
    orig_init = CrossEventDcsDialog.__init__

    def _fake_init(self, *a, **kw):
        orig_init(self, *a, **kw)
        captured.append(self)

    monkeypatch.setattr(CrossEventDcsDialog, "__init__", _fake_init)
    monkeypatch.setattr(CrossEventDcsDialog, "exec", lambda self: 0)

    page = EventsPage(gateway=gw)
    page._open_new_cross_event_dc()
    assert len(captured) == 1
    page.deleteLater()
    gw.close()


def test_events_page_pin_requested_opens_cut_dialog(qapp, tmp_path,
                                                    monkeypatch):
    """Clicking Pin → Cut on a DC row opens the New Collection face of
    :class:`NewRecipeDialog`, pre-seeded with that DC as the Source.

    spec/90 Phase 4f swapped the legacy
    :class:`NewCrossEventCutDialog` for the new dialog; this test
    pins the Collection-face configuration (show_scope=True,
    show_hardware=True, inventory_scope="library")."""
    from mira.gateway.library_gateway import LibraryGateway
    from mira.ui.pages.cross_event_dcs_dialog import CrossEventDcsDialog
    from mira.ui.pages.events_page import EventsPage
    from mira.ui.pages.new_recipe_dialog import (
        FLAVOUR_COLLECTION,
        INVENTORY_LIBRARY,
        NewRecipeDialog,
    )
    from mira.user_store import models as um

    gw, photos_base = _make_umbrella(tmp_path)
    lg = LibraryGateway(gw.user_store)
    sf = lg.create_dc("hero", expr=[["+", "exported"]])

    cut_dialogs: list = []
    orig_cut_init = NewRecipeDialog.__init__

    def _capture(self, *a, **kw):
        orig_cut_init(self, *a, **kw)
        cut_dialogs.append(self)
    monkeypatch.setattr(NewRecipeDialog, "__init__", _capture)
    monkeypatch.setattr(NewRecipeDialog, "exec", lambda self: 0)
    monkeypatch.setattr(CrossEventDcsDialog, "exec", lambda self: 0)

    page = EventsPage(gateway=gw)
    page._pin_cross_event_dc(lg, sf)
    assert len(cut_dialogs) == 1
    dlg = cut_dialogs[0]
    # The dialog opens in the Collection face — Scope + hardware visible.
    assert dlg._flavour == FLAVOUR_COLLECTION
    assert dlg._show_scope is True
    assert dlg._show_hardware is True
    assert dlg._inventory_scope == INVENTORY_LIBRARY
    # The clicked DC pre-seeded the Source.
    assert dlg._source_chips
    _, first_operand = dlg._source_chips[0]
    assert first_operand.tag == "hero"
    assert first_operand.kind == "dc"
    page.deleteLater()
    gw.close()
