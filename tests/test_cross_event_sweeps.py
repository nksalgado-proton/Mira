"""Cross-event integrity sweeps — spec/94 Phase 4a-ii repointed.

The pre-Phase 4a code walked event.db files for cross-event cut +
cut_member rows; spec/93 §3 moved cross-event Cuts into mira.db so the
sweeps follow. ``sweep_dangling_cross_event_members`` now reads
mira.db's ``cut_member``. ``sweep_dc_references`` covers BOTH stores —
mira.db cross-event Cuts AND event.db event-scope Cuts that
legitimately pinned a global Collection (the discriminator Nelson
flagged).
"""
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


def _seed_event(photos_base, eid, name) -> Path:
    """Build an empty event.db registered with the gateway."""
    root = photos_base / name
    root.mkdir(exist_ok=True)
    store = EventStore.create(
        root / "event.db", event_id=eid,
        app_version="test", created_at=NOW)
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO event (id, uuid, name, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?)", (eid, name, NOW, NOW))
    store.close()
    return root


def _seed_event_scope_cut(root: Path, cut_id: str, *,
                          source_dc_id: str = None,
                          source_dc_kind: str = "user") -> None:
    """Seed an event-scope cut in the event.db at ``root``. Used by
    sweep_dc_references tests — an event-scope Cut pinned from a
    global Collection legitimately carries ``source_dc_kind='user'``
    and lives in its event.db (spec/93 §3 discriminator)."""
    store = EventStore.open(root / "event.db")
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO cut (id, tag, source_dc_kind, source_dc_id, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cut_id, f"tag_{cut_id}", source_dc_kind, source_dc_id,
             NOW, NOW))
    store.close()


def _seed_cross_event_cut(gw, cut_id: str, members: list, *,
                          source_dc_id: str = None) -> None:
    """Seed a cross-event Cut in mira.db (the library store) with the
    given members. ``members`` is a list of dicts with the same shape
    :meth:`LibraryGateway.set_cross_event_cut_members` expects."""
    lg = gw.library_gateway()
    with lg.user_store.transaction() as conn:
        conn.execute(
            "INSERT INTO cut (id, tag, source_dc_kind, source_dc_id, "
            "created_at, updated_at) "
            "VALUES (?, ?, 'user', ?, ?, ?)",
            (cut_id, f"tag_{cut_id}", source_dc_id, NOW, NOW))
    if members:
        lg.set_cross_event_cut_members(cut_id, members)


def _register(gw, photos_base, root, *, eid, name):
    from mira.gateway.index import make_entry
    gw.index.upsert(make_entry(
        event_id=eid, name=name,
        start_date=None, end_date=None, is_closed=False,
        event_root=root, photos_base_path=photos_base))


# --------------------------------------------------------------------------- #
# sweep_dangling_cross_event_members — mira.db now
# --------------------------------------------------------------------------- #


def test_sweep_drops_members_whose_event_id_is_gone(tmp_path):
    """A cross-event member whose ``event_id`` is no longer in the
    events index is dropped from mira.db."""
    gw, photos_base = _make_umbrella(tmp_path)
    alive = _seed_event(photos_base, "alive", "Alive")
    _register(gw, photos_base, alive, eid="alive", name="Alive")
    _seed_cross_event_cut(gw, "cut-x", [
        {"event_id": "alive", "kind": "export",
         "export_relpath": "Exported Media/a.jpg"},
        {"event_id": "ghost", "kind": "export",
         "export_relpath": "Exported Media/b.jpg"},
    ])
    summary = sweep_dangling_cross_event_members(gw)
    assert summary["dropped"] == 1
    members = gw.library_gateway().cross_event_cut_members("cut-x")
    assert len(members) == 1
    assert members[0].event_id == "alive"
    gw.close()


def test_sweep_visits_every_member_in_one_pass(tmp_path):
    """spec/94 Phase 4a-ii: cross-event members live in mira.db; the
    sweep is a single SELECT — no event.db walk."""
    gw, photos_base = _make_umbrella(tmp_path)
    r1 = _seed_event(photos_base, "e1", "E1")
    r2 = _seed_event(photos_base, "e2", "E2")
    _register(gw, photos_base, r1, eid="e1", name="E1")
    _register(gw, photos_base, r2, eid="e2", name="E2")
    _seed_cross_event_cut(gw, "c1", [
        {"event_id": "e2", "kind": "export",
         "export_relpath": "Exported Media/a.jpg"}])
    _seed_cross_event_cut(gw, "c2", [
        {"event_id": "e1", "kind": "export",
         "export_relpath": "Exported Media/b.jpg"}])
    summary = sweep_dangling_cross_event_members(gw)
    # Both rows reference known events → nothing drops.
    assert summary["visited"] == 2
    assert summary["dropped"] == 0
    gw.close()


def test_sweep_grab_kind_also_handled(tmp_path):
    """Grab-kind members whose source event vanished are dropped too."""
    gw, photos_base = _make_umbrella(tmp_path)
    _seed_cross_event_cut(gw, "cut-x", [
        {"event_id": "ghost", "kind": "grab",
         "origin_relpath": "Original Media/raw.raw"}])
    summary = sweep_dangling_cross_event_members(gw)
    assert summary["dropped"] == 1
    gw.close()


# --------------------------------------------------------------------------- #
# sweep_dc_references — covers BOTH stores (spec/94 Phase 4a-ii)
# --------------------------------------------------------------------------- #


def test_sweep_dc_refs_nulls_cross_event_cut_in_mira_db(tmp_path):
    """A cross-event Cut in mira.db with ``source_dc_id = 'sf-1'`` has
    its source_dc_id + source_dc_kind NULLed when sf-1 is deleted."""
    gw, photos_base = _make_umbrella(tmp_path)
    _seed_cross_event_cut(gw, "cut-x", [
        {"event_id": "src", "kind": "export",
         "export_relpath": "Exported Media/a.jpg"}],
        source_dc_id="sf-1")
    summary = sweep_dc_references(gw, "sf-1")
    assert summary["nulled"] == 1
    cut = gw.library_gateway().cross_event_cut("cut-x")
    assert cut.source_dc_id is None
    assert cut.source_dc_kind is None
    gw.close()


def test_sweep_dc_refs_nulls_event_scope_cut_in_event_db(tmp_path):
    """An event-scope Cut that pinned a global Collection legitimately
    lives in event.db (spec/93 §3 discriminator: all-local members).
    Its ``source_dc_id`` still references mira.db's saved_filter and
    needs cleaning up when the source DC is deleted."""
    gw, photos_base = _make_umbrella(tmp_path)
    r = _seed_event(photos_base, "anchor", "Anchor")
    _register(gw, photos_base, r, eid="anchor", name="Anchor")
    _seed_event_scope_cut(r, "cut-evt", source_dc_id="sf-1",
                          source_dc_kind="user")
    summary = sweep_dc_references(gw, "sf-1")
    assert summary["nulled"] == 1
    store = EventStore.open(r / "event.db")
    try:
        row = store.conn.execute(
            "SELECT source_dc_id, source_dc_kind FROM cut "
            "WHERE id = 'cut-evt'").fetchone()
        assert row["source_dc_id"] is None
        assert row["source_dc_kind"] is None
    finally:
        store.close()
    gw.close()


def test_sweep_dc_refs_does_not_touch_event_kind(tmp_path):
    """An event-scope Cut pinned from an EVENT-SCOPE DC (source_dc_kind
    = 'event') is untouched — that id lives in event.db's dynamic_collection,
    not mira.db. Only ``source_dc_kind='user'`` rows are swept."""
    gw, photos_base = _make_umbrella(tmp_path)
    r = _seed_event(photos_base, "anchor", "Anchor")
    _register(gw, photos_base, r, eid="anchor", name="Anchor")
    _seed_event_scope_cut(r, "cut-evt", source_dc_id="sf-1",
                          source_dc_kind="event")
    summary = sweep_dc_references(gw, "sf-1")
    assert summary["nulled"] == 0
    store = EventStore.open(r / "event.db")
    try:
        row = store.conn.execute(
            "SELECT source_dc_id, source_dc_kind FROM cut "
            "WHERE id = 'cut-evt'").fetchone()
        assert row["source_dc_id"] == "sf-1"
        assert row["source_dc_kind"] == "event"
    finally:
        store.close()
    gw.close()


def test_sweep_dc_refs_skips_unopenable_events(tmp_path):
    """Unopenable events skip gracefully (event.db walk only)."""
    gw, photos_base = _make_umbrella(tmp_path)
    ghost = photos_base / "Ghost"
    ghost.mkdir()
    _register(gw, photos_base, ghost, eid="ghost", name="Ghost")
    summary = sweep_dc_references(gw, "sf-1")
    assert summary["events_skipped"] == 1
    gw.close()


def test_sweep_dc_refs_covers_both_stores_in_one_call(tmp_path):
    """One sweep call NULLs cuts in mira.db AND event.db that point at
    the deleted DC — the cross-store discipline (spec/94 Phase 4a-ii)."""
    gw, photos_base = _make_umbrella(tmp_path)
    r = _seed_event(photos_base, "anchor", "Anchor")
    _register(gw, photos_base, r, eid="anchor", name="Anchor")
    # Cross-event cut in mira.db.
    _seed_cross_event_cut(gw, "cut-cross", [
        {"event_id": "anchor", "kind": "export",
         "export_relpath": "Exported Media/a.jpg"}],
        source_dc_id="sf-shared")
    # Event-scope cut in event.db (also pinned from the same global DC).
    _seed_event_scope_cut(r, "cut-evt", source_dc_id="sf-shared",
                          source_dc_kind="user")
    summary = sweep_dc_references(gw, "sf-shared")
    assert summary["nulled"] == 2
    assert gw.library_gateway().cross_event_cut(
        "cut-cross").source_dc_id is None
    store = EventStore.open(r / "event.db")
    try:
        row = store.conn.execute(
            "SELECT source_dc_id FROM cut WHERE id = 'cut-evt'").fetchone()
        assert row["source_dc_id"] is None
    finally:
        store.close()
    gw.close()


# --------------------------------------------------------------------------- #
# Gateway-level wrappers
# --------------------------------------------------------------------------- #


def test_delete_cross_event_dc_combines_both(tmp_path):
    """``Gateway.delete_cross_event_dc`` deletes the cross-event DC AND
    NULLs every Cut's source_dc_id pointed at it (both stores)."""
    from mira.user_store import models as um
    gw, photos_base = _make_umbrella(tmp_path)
    lg = gw.library_gateway()
    sf = lg.create_dc("doomed", expr=[["+", "exported"]])
    # Cross-event Cut pointing at it.
    _seed_cross_event_cut(gw, "cut-cross", [
        {"event_id": "anchor", "kind": "export",
         "export_relpath": "Exported Media/a.jpg"}],
        source_dc_id=sf.id)
    summary = gw.delete_cross_event_dc(sf.id)
    assert summary["nulled"] == 1
    # JSON tree no longer holds the DC.
    assert lg.dynamic_collection(sf.id) is None
    # SQL was never written (the JSON tree is the single live source).
    assert gw.user_store.get(um.SavedFilter, sf.id) is None
    # Cross-event Cut survived, source_dc_id NULLed.
    cut = lg.cross_event_cut("cut-cross")
    assert cut is not None and cut.source_dc_id is None
    gw.close()


def test_gateway_sweep_dangling_members_wraps_function(tmp_path):
    gw, photos_base = _make_umbrella(tmp_path)
    _seed_cross_event_cut(gw, "cut-x", [
        {"event_id": "ghost", "kind": "export",
         "export_relpath": "Exported Media/a.jpg"}])
    summary = gw.sweep_dangling_cross_event_members()
    assert summary["dropped"] == 1
    gw.close()
