"""spec/81 Phase 2 Item 6 — grab-originals (spec/61 §6 + §8).

Cross-event Cuts can include items still on the ``#collected`` / ``#picked``
/ ``#edited`` rungs (no lineage row, no shipped JPEG). The export pipeline
grabs the ORIGINAL bytes from the source event's ``Original Media/<...>``
instead. These tests exercise the schema v9 data shape + the cross-event
session integration (build-time + commit-time).

Out of scope for Item 6: the bytes-on-disk export pipeline that actually
materializes grab member files into a Cut directory — that lands when the
cross-event export UI lands.
"""
from __future__ import annotations

import json

import pytest

from core import collection_resolver as cr
from mira.gateway.event_gateway import EventGateway
from mira.gateway.library_gateway import LibraryGateway
from mira.shared.cross_event_cut_session import (
    CrossEventCutSession,
    CrossEventSessionFile,
    session_files_from_global_items,
)
from mira.shared.cut_draft import CrossEventCutDraft, PIN_WEED_OUT
from mira.store import models as sm
from mira.store.repo import EventStore
from mira.user_store import models as um
from mira.user_store.repo import UserStore


NOW = "2026-06-16T00:00:00+00:00"


# --------------------------------------------------------------------------- #
# Schema — the kind discriminator + per-kind CHECK
# --------------------------------------------------------------------------- #


def _make_event(tmp_path, *, eid="evt") -> EventStore:
    store = EventStore.create(
        tmp_path / f"{eid}.db",
        event_id=eid, app_version="test", created_at=NOW,
    )
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO event (id, uuid, name, created_at, updated_at) "
            "VALUES (1, ?, 'anchor', ?, ?)", (eid, NOW, NOW))
        conn.execute(
            "INSERT INTO cut (id, tag, created_at, updated_at) "
            "VALUES ('c1', 'cut_one', ?, ?)", (NOW, NOW))
    return store


def test_grab_member_check_enforces_exclusivity(tmp_path):
    """A 'grab' member MUST have ``origin_relpath`` and MUST NOT have
    ``export_relpath`` — the CHECK constraint enforces it."""
    store = _make_event(tmp_path)
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO cut_member "
            "(cut_id, member_id, kind, export_relpath, origin_relpath, added_at) "
            "VALUES ('c1', 'm1', 'grab', 'Exported Media/x.jpg', NULL, 't')")
    store.close()


def test_export_member_check_enforces_exclusivity(tmp_path):
    """An 'export' member MUST have ``export_relpath`` and MUST NOT have
    ``origin_relpath``."""
    store = _make_event(tmp_path)
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO cut_member "
            "(cut_id, member_id, kind, export_relpath, origin_relpath, added_at) "
            "VALUES ('c1', 'm1', 'export', NULL, 'Original Media/x.jpg', 't')")
    store.close()


def test_kind_check_rejects_unknown_value(tmp_path):
    store = _make_event(tmp_path)
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO cut_member "
            "(cut_id, member_id, kind, export_relpath, added_at) "
            "VALUES ('c1', 'm1', 'maybe', 'x.jpg', 't')")
    store.close()


def test_member_id_pk_dedupes_same_path_under_one_cut(tmp_path):
    """Same ``member_id`` under the same cut is rejected by the PK — one
    cut, one row per content-stable path."""
    store = _make_event(tmp_path)
    import sqlite3
    store.conn.execute(
        "INSERT INTO cut_member "
        "(cut_id, member_id, kind, export_relpath, added_at) "
        "VALUES ('c1', 'Exported Media/x.jpg', 'export', "
        "'Exported Media/x.jpg', 't')")
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO cut_member "
            "(cut_id, member_id, kind, export_relpath, added_at) "
            "VALUES ('c1', 'Exported Media/x.jpg', 'export', "
            "'Exported Media/x.jpg', 't2')")
    store.close()


# --------------------------------------------------------------------------- #
# v8→v9 migration — existing v8 rows survive as 'export'
# --------------------------------------------------------------------------- #


def test_v8_rows_migrate_as_export_kind(tmp_path):
    """Existing v8 cut_member rows preserve verbatim into v9 — all
    ``kind='export'``, ``member_id = export_relpath``, ``origin_relpath`` NULL."""
    from mira.store import schema as sschema
    # Build a v8-shape file directly: connect raw + apply DDL up to v8 only.
    import sqlite3
    db_path = tmp_path / "legacy.db"
    conn = sschema.connect(db_path)
    sschema.initialize(conn, event_id="legacy", created_at=NOW)
    # Rewind to v8 (drop v9 table + restore the v8 shape).
    conn.execute("DROP TABLE cut_member")
    conn.execute("""
CREATE TABLE cut_member (
  cut_id         TEXT NOT NULL REFERENCES cut(id) ON DELETE CASCADE,
  export_relpath TEXT NOT NULL,
  event_id       TEXT,
  added_at       TEXT NOT NULL,
  PRIMARY KEY (cut_id, export_relpath)
)""")
    # Strip the post-v8 lineage columns so the ADD COLUMN steps on
    # the way back up don't collide (spec/89 added 'provenance' and
    # 'intent_state').
    conn.execute("ALTER TABLE lineage DROP COLUMN intent_state")
    conn.execute("ALTER TABLE lineage DROP COLUMN provenance")
    # Strip the v12 face table so the v11→v12 CREATE TABLE doesn't
    # collide on the way back up (spec/90 Phase 1). Also drop the v13
    # recipe table for the same reason (spec/94 Phase 1). spec/109
    # added v13→v14 stack_bracket.producer; strip the column so the
    # up-migration's ALTER ADD doesn't collide.
    conn.execute("DROP TABLE face")
    conn.execute("DROP TABLE IF EXISTS recipe")
    conn.execute("ALTER TABLE stack_bracket DROP COLUMN producer")
    conn.execute("UPDATE schema_info SET schema_version = 8 WHERE id = 1")
    conn.execute(
        "INSERT INTO cut (id, tag, created_at, updated_at) "
        "VALUES ('c1', 'legacy_cut', ?, ?)", (NOW, NOW))
    conn.execute(
        "INSERT INTO cut_member (cut_id, export_relpath, event_id, added_at) "
        "VALUES ('c1', 'Exported Media/p1.jpg', NULL, ?)", (NOW,))
    conn.execute(
        "INSERT INTO cut_member (cut_id, export_relpath, event_id, added_at) "
        "VALUES ('c1', 'Exported Media/p2.jpg', 'evt-B', ?)", (NOW,))

    sschema.migrate(conn)

    assert sschema.get_version(conn) == sschema.SCHEMA_VERSION
    rows = conn.execute(
        "SELECT * FROM cut_member ORDER BY export_relpath").fetchall()
    assert len(rows) == 2
    for r in rows:
        assert r["kind"] == "export"
        assert r["member_id"] == r["export_relpath"]
        assert r["origin_relpath"] is None
    # event_id preserved through the rebuild.
    assert {r["event_id"] for r in rows} == {None, "evt-B"}
    conn.close()


# --------------------------------------------------------------------------- #
# Session — un-exported items with origin_relpath become grab members
# --------------------------------------------------------------------------- #


def _seed_projection_with_grabs(user_store: UserStore) -> None:
    """3 exported + 2 picked-only (with origin_relpath) + 1 collected-only
    without origin_relpath. The session should include exports + grabs;
    drop the no-origin orphan."""
    rows = [
        # Exported member (Event A).
        um.GlobalItem(
            event_uuid="A", item_id="a1", synced_at=NOW,
            export_relpath="Exported Media/Day01/a1.jpg",
            origin_relpath="Original Media/Day01/a1.raw",
            capture_time="2026-04-01T10:00:00",
            kind="photo", has_export=True,
        ),
        # Grab candidate (picked but not yet exported, Event A).
        um.GlobalItem(
            event_uuid="A", item_id="a2", synced_at=NOW,
            export_relpath=None,
            origin_relpath="Original Media/Day01/a2.raw",
            capture_time="2026-04-01T11:00:00",
            kind="photo", pick_state="picked",
        ),
        # Edited but not yet exported (Event A, video).
        um.GlobalItem(
            event_uuid="A", item_id="a3", synced_at=NOW,
            export_relpath=None,
            origin_relpath="Original Media/Day02/a3.mp4",
            capture_time="2026-04-02T15:00:00",
            kind="video", duration_ms=45_000,
            pick_state="picked", edit_state="picked",
        ),
        # Exported member (Event B).
        um.GlobalItem(
            event_uuid="B", item_id="b1", synced_at=NOW,
            export_relpath="Exported Media/Day01/b1.jpg",
            origin_relpath="Original Media/Day01/b1.raw",
            capture_time="2025-10-15T07:00:00",
            kind="photo", has_export=True,
        ),
        # Orphan: no export, no origin — gets dropped by the session.
        um.GlobalItem(
            event_uuid="B", item_id="b2", synced_at=NOW,
            export_relpath=None, origin_relpath=None,
            capture_time="2025-10-16T17:00:00",
            kind="photo",
        ),
    ]
    for r in rows:
        user_store.upsert(r)


def _open_user(tmp_path) -> UserStore:
    return UserStore.create(
        tmp_path / "mira.db", app_version="test", created_at=NOW,
    )


def _make_lg(user_store: UserStore) -> LibraryGateway:
    return LibraryGateway(user_store, now=lambda: NOW)


def test_session_includes_grab_candidates_when_origin_available(tmp_path):
    """Un-exported items with ``origin_relpath`` become 'grab' members
    instead of being dropped (Item 4 behaviour). #collected→grab works
    for the whole ladder."""
    store = _open_user(tmp_path)
    _seed_projection_with_grabs(store)
    lg = _make_lg(store)
    keys = lg.resolve_dc_keys([["+", cr.BASE_COLLECTED]])
    rows = store.query_raw(um.GlobalItem, "SELECT * FROM global_items")
    files = session_files_from_global_items(rows, keys)
    by_id = {(f.event_uuid, f.item_id): f for f in files}
    # Exports stay 'export' members.
    assert by_id[("A", "a1")].member_kind == "export"
    assert by_id[("B", "b1")].member_kind == "export"
    # Un-exported with origin become 'grab' members.
    assert by_id[("A", "a2")].member_kind == "grab"
    assert by_id[("A", "a2")].origin_relpath == "Original Media/Day01/a2.raw"
    assert by_id[("A", "a3")].member_kind == "grab"
    # No-export-no-origin dropped.
    assert ("B", "b2") not in by_id
    store.close()


def test_session_skips_grabs_when_allow_grab_false(tmp_path):
    """``allow_grab=False`` falls back to pre-Item-6 behaviour — only
    pre-exported members survive."""
    store = _open_user(tmp_path)
    _seed_projection_with_grabs(store)
    lg = _make_lg(store)
    keys = lg.resolve_dc_keys([["+", cr.BASE_COLLECTED]])
    rows = store.query_raw(um.GlobalItem, "SELECT * FROM global_items")
    files = session_files_from_global_items(rows, keys, allow_grab=False)
    by_id = {(f.event_uuid, f.item_id) for f in files}
    assert by_id == {("A", "a1"), ("B", "b1")}
    store.close()


def test_picked_members_emits_grab_dict_for_grab_member(tmp_path):
    """A 'grab' picked member commits as a dict with ``origin_relpath`` +
    ``kind='grab'`` — set_cut_members consumes this shape verbatim."""
    store = _open_user(tmp_path)
    _seed_projection_with_grabs(store)
    lg = _make_lg(store)
    keys = lg.resolve_dc_keys([["+", cr.BASE_COLLECTED]])
    rows = store.query_raw(um.GlobalItem, "SELECT * FROM global_items")
    files = session_files_from_global_items(rows, keys)
    session = CrossEventCutSession(
        name="mixed_cut", expr=tuple([("+", "collected")]),
        filters={}, pin_mode=PIN_WEED_OUT,
        target_s=None, max_s=None, photo_s=6.0, music_category=None,
        files=tuple(files), anchor_event_id="A",
    )
    members = session.picked_members()
    grab = [m for m in members if m["kind"] == "grab"]
    export = [m for m in members if m["kind"] == "export"]
    assert len(grab) == 2 and len(export) == 2
    assert {m["origin_relpath"] for m in grab} == {
        "Original Media/Day01/a2.raw",
        "Original Media/Day02/a3.mp4",
    }
    assert {m["export_relpath"] for m in export} == {
        "Exported Media/Day01/a1.jpg",
        "Exported Media/Day01/b1.jpg",
    }
    store.close()


# --------------------------------------------------------------------------- #
# Commit — set_cut_members handles mixed export + grab members in one Cut
# --------------------------------------------------------------------------- #


def test_set_cut_members_writes_mixed_kinds(tmp_path):
    """A single cross-event Cut can carry export-kind AND grab-kind
    members interleaved. spec/94 Phase 4a-ii: commit writes to mira.db
    via LibraryGateway (spec/93 §3) — no anchor event.db opens."""
    user_store = _open_user(tmp_path)
    _seed_projection_with_grabs(user_store)
    lg = _make_lg(user_store)
    keys = lg.resolve_dc_keys([["+", cr.BASE_COLLECTED]])
    rows = user_store.query_raw(um.GlobalItem, "SELECT * FROM global_items")
    files = session_files_from_global_items(rows, keys)

    session = CrossEventCutSession(
        name="mixed", expr=tuple([("+", "collected")]),
        filters={}, pin_mode=PIN_WEED_OUT,
        target_s=None, max_s=None, photo_s=6.0, music_category=None,
        files=tuple(files), anchor_event_id="anchor",
    )
    cut = session.commit(lg)
    members = lg.cross_event_cut_members(cut.id)
    assert len(members) == 4
    kinds = {m.kind for m in members}
    assert kinds == {"export", "grab"}
    # Each kind carries its own relpath column populated, the other NULL.
    for m in members:
        if m.kind == "export":
            assert m.export_relpath is not None
            assert m.origin_relpath is None
            assert m.member_id == m.export_relpath
        else:
            assert m.origin_relpath is not None
            assert m.export_relpath is None
            assert m.member_id == m.origin_relpath
        assert m.event_id           # cross-event always has it
    user_store.close()


def test_set_cut_members_dict_shape_works_for_grab_only(tmp_path):
    """The gateway's dict shape accepts pure-grab membership too."""
    store = _make_event(tmp_path)
    with store.transaction() as conn:
        conn.execute("DELETE FROM cut WHERE id = 'c1'")
    eg = EventGateway(store, now=lambda: NOW,
                      new_id=lambda: "cut-only")
    eg.create_cut("grabs_only", source_dc_kind="user")
    n = eg.set_cut_members("cut-only", [
        {"event_id": "B", "kind": "grab",
         "origin_relpath": "Original Media/x.raw"},
        {"event_id": "B", "kind": "grab",
         "origin_relpath": "Original Media/y.raw"},
    ])
    assert n == 2
    rows = store.conn.execute(
        "SELECT kind, origin_relpath, export_relpath FROM cut_member "
        "WHERE cut_id = 'cut-only'").fetchall()
    assert all(r["kind"] == "grab" for r in rows)
    assert all(r["export_relpath"] is None for r in rows)
    assert {r["origin_relpath"] for r in rows} == {
        "Original Media/x.raw", "Original Media/y.raw",
    }
    store.close()


def test_set_cut_members_legacy_string_shape_still_works(tmp_path):
    """Pre-Item-6 event-scope callers pass plain relpath strings — still
    works, all members become 'export' with NULL event_id."""
    store = _make_event(tmp_path)
    with store.transaction() as conn:
        conn.execute("DELETE FROM cut WHERE id = 'c1'")
    eg = EventGateway(store, now=lambda: NOW, new_id=lambda: "cut-leg")
    eg.create_cut("legacy")
    n = eg.set_cut_members("cut-leg", [
        "Exported Media/p1.jpg",
        "Exported Media/p2.jpg",
    ])
    assert n == 2
    rows = store.conn.execute(
        "SELECT kind, event_id FROM cut_member WHERE cut_id = 'cut-leg'"
    ).fetchall()
    assert all(r["kind"] == "export" for r in rows)
    assert all(r["event_id"] is None for r in rows)
    store.close()


def test_set_cut_members_dedupes_by_member_id(tmp_path):
    """Two entries with the same member_id collapse to one — the PK's
    content-stable identity."""
    store = _make_event(tmp_path)
    with store.transaction() as conn:
        conn.execute("DELETE FROM cut WHERE id = 'c1'")
    eg = EventGateway(store, now=lambda: NOW, new_id=lambda: "cut-dup")
    eg.create_cut("dedupe_test")
    n = eg.set_cut_members("cut-dup", [
        {"event_id": "A", "kind": "export",
         "export_relpath": "Exported Media/x.jpg"},
        {"event_id": "B", "kind": "export",     # different event, same path
         "export_relpath": "Exported Media/x.jpg"},
    ])
    # member_id = "Exported Media/x.jpg" matches both → the second wins,
    # final count = 1.
    assert n == 1
    store.close()
