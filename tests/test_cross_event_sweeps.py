"""Cross-event integrity sweeps."""
from __future__ import annotations

from pathlib import Path

import pytest

from mira.shared.cross_event_sweeps import (
    sweep_dangling_cross_event_members,
    sweep_dc_references,
)
from mira.store.repo import EventStore


NOW = "2026-06-16T00:00:00+00:00"


def _make_umbrella(tmp_path):
    from mira.gateway.gateway import Gateway
    from mira.gateway.index import EventsIndex
    from mira.settings.repo import SettingsRepo

    settings = SettingsRepo(tmp_path / "settings.json")
    index = EventsIndex(tmp_path / "events_index.json")
    photos_base = tmp_path / "photos"
    photos_base.mkdir()
    gw = Gateway(
        settings=settings, index=index,
        user_store_path=tmp_path / "mira.db",
        now=lambda: NOW, installation_profile="XMC")
    _ = gw.user_store
    settings.update(photos_base_path=str(photos_base))
    return gw, photos_base


def _seed_event(photos_base, eid, name, *, cut_members=()):
    """Build an event.db with cuts + members. ``cut_members`` is a list of
    dicts: cut_id, kind, export_relpath/origin_relpath, event_id."""
    root = photos_base / name
    root.mkdir(exist_ok=True)
    store = EventStore.create(
        root / "event.db", event_id=eid,
        app_version="test", created_at=NOW)
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO event (id, uuid, name, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?)", (eid, name, NOW, NOW))
        cut_ids = {m["cut_id"] for m in cut_members}
        for cid in cut_ids:
            conn.execute(
                "INSERT INTO cut (id, tag, source_dc_kind, source_dc_id, "
                "created_at, updated_at) "
                "VALUES (?, ?, 'user', ?, ?, ?)",
                (cid, f"tag_{cid}",
                 m.get("source_dc_id", "sf-1") if (m := next(
                     (m for m in cut_members if m["cut_id"] == cid), {}))
                 else None,
                 NOW, NOW))
        for m in cut_members:
            member_id = m.get("export_relpath") or m["origin_relpath"]
            conn.execute(
                "INSERT INTO cut_member (cut_id, member_id, kind, "
                "export_relpath, origin_relpath, event_id, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (m["cut_id"], member_id, m["kind"],
                 m.get("export_relpath"), m.get("origin_relpath"),
                 m.get("event_id"), NOW))
    store.close()
    return root


def _register(gw, photos_base, root, *, eid, name):
    from mira.gateway.index import make_entry
    gw.index.upsert(make_entry(
        event_id=eid, name=name,
        start_date=None, end_date=None, is_closed=False,
        event_root=root, photos_base_path=photos_base))


def _read_cut_members(root: Path, cut_id: str) -> list:
    store = EventStore.open(root / "event.db")
    try:
        rows = store.conn.execute(
            "SELECT member_id, event_id FROM cut_member WHERE cut_id = ?",
            (cut_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# sweep_dangling_cross_event_members
# --------------------------------------------------------------------------- #


def test_sweep_drops_members_whose_event_id_is_gone(tmp_path):
    """A cross-event member whose ``event_id`` is no longer in the events
    index is dropped."""
    gw, photos_base = _make_umbrella(tmp_path)
    r = _seed_event(photos_base, "anchor", "Anchor", cut_members=[
        {"cut_id": "cut-x", "kind": "export",
         "export_relpath": "Exported Media/a.jpg", "event_id": "alive"},
        {"cut_id": "cut-x", "kind": "export",
         "export_relpath": "Exported Media/b.jpg", "event_id": "ghost"},
    ])
    _register(gw, photos_base, r, eid="anchor", name="Anchor")
    # "alive" is registered (the source), "ghost" is not.
    alive_root = _seed_event(photos_base, "alive", "Alive")
    _register(gw, photos_base, alive_root, eid="alive", name="Alive")

    summary = sweep_dangling_cross_event_members(gw)
    assert summary["dropped"] == 1
    members = _read_cut_members(r, "cut-x")
    assert len(members) == 1
    assert members[0]["event_id"] == "alive"
    gw.close()


def test_sweep_keeps_event_scope_members(tmp_path):
    """Members with ``event_id IS NULL`` (event-scope / anchor) are not
    touched by the cross-event sweep."""
    gw, photos_base = _make_umbrella(tmp_path)
    r = _seed_event(photos_base, "anchor", "Anchor", cut_members=[
        {"cut_id": "cut-x", "kind": "export",
         "export_relpath": "Exported Media/a.jpg"},  # event_id NULL
        {"cut_id": "cut-x", "kind": "export",
         "export_relpath": "Exported Media/b.jpg",
         "event_id": "ghost"},  # cross-event ghost
    ])
    _register(gw, photos_base, r, eid="anchor", name="Anchor")
    summary = sweep_dangling_cross_event_members(gw)
    assert summary["dropped"] == 1
    members = _read_cut_members(r, "cut-x")
    # The NULL-event_id member survives.
    assert any(m["event_id"] is None for m in members)
    gw.close()


def test_sweep_visits_every_event_in_index(tmp_path):
    gw, photos_base = _make_umbrella(tmp_path)
    r1 = _seed_event(photos_base, "e1", "E1", cut_members=[
        {"cut_id": "c1", "kind": "export",
         "export_relpath": "Exported Media/a.jpg", "event_id": "e2"},
    ])
    r2 = _seed_event(photos_base, "e2", "E2", cut_members=[
        {"cut_id": "c2", "kind": "export",
         "export_relpath": "Exported Media/b.jpg", "event_id": "e1"},
    ])
    _register(gw, photos_base, r1, eid="e1", name="E1")
    _register(gw, photos_base, r2, eid="e2", name="E2")
    summary = sweep_dangling_cross_event_members(gw)
    # Both rows are alive (each references the other), so nothing drops.
    assert summary["visited"] == 2
    assert summary["dropped"] == 0
    gw.close()


def test_sweep_grab_kind_also_handled(tmp_path):
    """Grab-kind members whose source event vanished are also dropped."""
    gw, photos_base = _make_umbrella(tmp_path)
    r = _seed_event(photos_base, "anchor", "Anchor", cut_members=[
        {"cut_id": "cut-x", "kind": "grab",
         "origin_relpath": "Original Media/raw.raw", "event_id": "ghost"},
    ])
    _register(gw, photos_base, r, eid="anchor", name="Anchor")
    summary = sweep_dangling_cross_event_members(gw)
    assert summary["dropped"] == 1
    gw.close()


# --------------------------------------------------------------------------- #
# sweep_dc_references
# --------------------------------------------------------------------------- #


def test_sweep_dc_refs_nulls_cuts_pointing_at_deleted_dc(tmp_path):
    """A cross-event Cut with ``source_dc_id = 'sf-1'`` has its source_dc_id
    + source_dc_kind NULLed when sf-1 is deleted."""
    gw, photos_base = _make_umbrella(tmp_path)
    r = _seed_event(photos_base, "anchor", "Anchor", cut_members=[
        {"cut_id": "cut-x", "kind": "export",
         "export_relpath": "Exported Media/a.jpg",
         "source_dc_id": "sf-1"},
    ])
    _register(gw, photos_base, r, eid="anchor", name="Anchor")
    summary = sweep_dc_references(gw, "sf-1")
    assert summary["nulled"] == 1
    # Verify the cut row was updated.
    store = EventStore.open(r / "event.db")
    try:
        row = store.conn.execute(
            "SELECT source_dc_id, source_dc_kind FROM cut WHERE id = 'cut-x'"
        ).fetchone()
        assert row["source_dc_id"] is None
        assert row["source_dc_kind"] is None
    finally:
        store.close()
    gw.close()


def test_sweep_dc_refs_does_not_touch_event_scope_cuts(tmp_path):
    """An event-scope Cut (source_dc_kind='event') with the same id is NOT
    touched."""
    gw, photos_base = _make_umbrella(tmp_path)
    r = photos_base / "Anchor"
    r.mkdir()
    store = EventStore.create(
        r / "event.db", event_id="anchor",
        app_version="test", created_at=NOW)
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO event (id, uuid, name, created_at, updated_at) "
            "VALUES (1, 'anchor', 'Anchor', ?, ?)", (NOW, NOW))
        # Event-scope cut with same source_dc_id as the deleted user DC.
        conn.execute(
            "INSERT INTO cut (id, tag, source_dc_kind, source_dc_id, "
            "created_at, updated_at) "
            "VALUES ('cut-event', 'evt_cut', 'event', 'sf-1', ?, ?)",
            (NOW, NOW))
    store.close()
    _register(gw, photos_base, r, eid="anchor", name="Anchor")
    summary = sweep_dc_references(gw, "sf-1")
    # Event-scope cut not affected.
    assert summary["nulled"] == 0
    store = EventStore.open(r / "event.db")
    try:
        row = store.conn.execute(
            "SELECT source_dc_id, source_dc_kind FROM cut "
            "WHERE id = 'cut-event'").fetchone()
        assert row["source_dc_id"] == "sf-1"
        assert row["source_dc_kind"] == "event"
    finally:
        store.close()
    gw.close()


def test_sweep_dc_refs_skips_unopenable_events(tmp_path):
    """Unopenable events skip gracefully."""
    gw, photos_base = _make_umbrella(tmp_path)
    # Empty root with no event.db.
    ghost = photos_base / "Ghost"
    ghost.mkdir()
    _register(gw, photos_base, ghost, eid="ghost", name="Ghost")
    summary = sweep_dc_references(gw, "sf-1")
    assert summary["events_skipped"] == 1
    gw.close()


# --------------------------------------------------------------------------- #
# Gateway-level wrappers
# --------------------------------------------------------------------------- #


def test_delete_cross_event_dc_combines_both(tmp_path):
    """``Gateway.delete_cross_event_dc`` deletes the cross-event DC AND
    NULLs every cross-event Cut's source_dc_id that pointed at it."""
    from mira.user_store import models as um
    gw, photos_base = _make_umbrella(tmp_path)
    # spec/94 Phase 1b — DC writes go through the JSON tree via the
    # Gateway's wired factory.
    lg = gw.library_gateway()
    sf = lg.create_dc("doomed", expr=[["+", "exported"]])
    # Create a cross-event Cut pointing at it.
    r = _seed_event(photos_base, "anchor", "Anchor", cut_members=[
        {"cut_id": "cut-x", "kind": "export",
         "export_relpath": "Exported Media/a.jpg",
         "source_dc_id": sf.id},
    ])
    _register(gw, photos_base, r, eid="anchor", name="Anchor")
    # Delete via the umbrella's combined method.
    summary = gw.delete_cross_event_dc(sf.id)
    assert summary["nulled"] == 1
    # JSON tree no longer holds it.
    assert lg.dynamic_collection(sf.id) is None
    # SQL was never written (the JSON tree is the single live source).
    assert gw.user_store.get(um.SavedFilter, sf.id) is None
    # Cut survived, source_dc_id NULLed.
    store = EventStore.open(r / "event.db")
    try:
        row = store.conn.execute(
            "SELECT source_dc_id, source_dc_kind FROM cut WHERE id = 'cut-x'"
        ).fetchone()
        assert row["source_dc_id"] is None
    finally:
        store.close()
    gw.close()


def test_gateway_sweep_dangling_members_wraps_function(tmp_path):
    gw, photos_base = _make_umbrella(tmp_path)
    r = _seed_event(photos_base, "anchor", "Anchor", cut_members=[
        {"cut_id": "cut-x", "kind": "export",
         "export_relpath": "Exported Media/a.jpg", "event_id": "ghost"},
    ])
    _register(gw, photos_base, r, eid="anchor", name="Anchor")
    summary = gw.sweep_dangling_cross_event_members()
    assert summary["dropped"] == 1
    gw.close()
