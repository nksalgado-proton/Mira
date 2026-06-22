"""spec/94 Phase 4a-ii — cross-event Cut migration tests.

Pin the three correctness rules:

1. **Discriminator (Nelson's call):** the migration uses the membership
   SHAPE — any member with a non-NULL ``event_id`` — not
   ``source_dc_kind = 'user'`` alone. An event-scope Cut pinned from a
   global Collection legitimately carries ``source_dc_kind='user'`` but
   all-local members, and MUST stay in its event.db.

2. **Copy → verify → delete:** if the verify gate fires (forced via a
   monkeypatched insert), the event.db rows survive untouched and the
   marker is NOT written. Re-running attempts the migration again.

3. **Idempotent + marker-gated:** a second invocation after success is
   a cheap no-op (skipped=True). A partial-recovery case (mira.db
   committed but event.db delete missed) converges on retry without
   double-writing.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import pytest

from core.cross_event_cut_migrate import (
    CrossEventCutMigrationError,
    MARKER_FILENAME,
    marker_path,
    migrate_cross_event_cuts,
)
from mira.store.repo import EventStore
from mira.user_store.repo import UserStore


NOW = "2026-06-21T00:00:00+00:00"


# --------------------------------------------------------------------------- #
# Fixtures — minimal event.db + mira.db scaffolding
# --------------------------------------------------------------------------- #


def _open_user_store(library_root: Path) -> UserStore:
    return UserStore.create(
        library_root / "mira.db",
        app_version="test", created_at=NOW,
    )


def _make_event(library_root: Path, *, eid: str) -> EventStore:
    """A minimal event.db at the current schema version, with a seed
    ``event`` row + an empty ``cut`` table. The store stashes its
    path on the instance so the test factory can re-open it."""
    path = library_root / f"{eid}.db"
    store = EventStore.create(
        path, event_id=eid, app_version="test", created_at=NOW)
    store._test_path = path                                   # type: ignore[attr-defined]
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO event (id, uuid, name, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?)", (eid, eid, NOW, NOW))
    return store


def _seed_cut(event_store: EventStore, cut_id: str, *,
              source_dc_kind: str = "user",
              source_dc_id: Optional[str] = "sf-1",
              members: Iterable[dict] = ()) -> None:
    """Insert one cut + the named members into an event.db."""
    with event_store.transaction() as conn:
        conn.execute(
            "INSERT INTO cut (id, tag, source_dc_id, source_dc_kind, "
            "                 created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cut_id, f"tag_{cut_id}", source_dc_id, source_dc_kind,
             NOW, NOW))
        for m in members:
            member_id = m.get("member_id") or m.get(
                "export_relpath") or m["origin_relpath"]
            conn.execute(
                "INSERT INTO cut_member (cut_id, member_id, kind, "
                "                        export_relpath, origin_relpath, "
                "                        event_id, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (cut_id, member_id, m.get("kind", "export"),
                 m.get("export_relpath"), m.get("origin_relpath"),
                 m.get("event_id"), m.get("added_at", NOW)))


def _list_events_factory(events: List[Tuple[str, str]]):
    def _list() -> List[Tuple[str, str]]:
        return list(events)
    return _list


def _open_event_store_factory(by_uuid):
    """The migration treats the returned store as it owns it (closes on
    exit). To match the production wiring, hand it a FRESH open on
    every call. Tests keep their own ``by_uuid`` references for the
    final assertions; this factory opens a NEW connection per call so
    the migration's close doesn't invalidate the test's handle."""
    paths = {uuid: store._test_path for uuid, store in by_uuid.items()}

    def _open(uuid):
        path = paths.get(uuid)
        if path is None:
            return None
        return EventStore.open(path)
    return _open


# --------------------------------------------------------------------------- #
# 1. Discriminator — cross-event vs event-scope
# --------------------------------------------------------------------------- #


def test_event_scope_cut_with_user_dc_stays_put(tmp_path):
    """A Cut with ``source_dc_kind='user'`` AND all-local members (every
    ``event_id`` NULL) is event-scope per spec/93 §3 — it stays in its
    event.db. Migration must NOT touch it."""
    user_store = _open_user_store(tmp_path)
    evt = _make_event(tmp_path, eid="A")
    _seed_cut(evt, "cut-local", source_dc_kind="user",
              source_dc_id="sf-global",
              members=[
                  {"event_id": None, "kind": "export",
                   "export_relpath": "Exported Media/a.jpg"},
                  {"event_id": None, "kind": "export",
                   "export_relpath": "Exported Media/b.jpg"},
              ])
    try:
        report = migrate_cross_event_cuts(
            library_root=tmp_path,
            user_store=user_store,
            list_events=_list_events_factory([("A", "A")]),
            open_event_store=_open_event_store_factory({"A": evt}),
        )
        # Inspected the candidate, migrated nothing.
        assert report.inspected_cuts == 1
        assert report.migrated_cuts == 0
        assert report.migrated_members == 0
        # The cut + members still live in event.db.
        cut_count = evt.conn.execute(
            "SELECT COUNT(*) FROM cut WHERE id = 'cut-local'").fetchone()[0]
        assert cut_count == 1
        member_count = evt.conn.execute(
            "SELECT COUNT(*) FROM cut_member WHERE cut_id = 'cut-local'"
        ).fetchone()[0]
        assert member_count == 2
        # Nothing landed in mira.db.
        assert user_store.conn.execute(
            "SELECT COUNT(*) FROM cut").fetchone()[0] == 0
    finally:
        evt.close()
        user_store.close()


def test_cross_event_cut_with_foreign_member_migrates(tmp_path):
    """A Cut with at least one foreign-event member is cross-event and
    moves to mira.db."""
    user_store = _open_user_store(tmp_path)
    evt = _make_event(tmp_path, eid="A")
    _seed_cut(evt, "cut-cross", source_dc_kind="user",
              source_dc_id="sf-1",
              members=[
                  {"event_id": "A", "kind": "export",
                   "export_relpath": "Exported Media/local.jpg"},
                  {"event_id": "B", "kind": "export",
                   "export_relpath": "Exported Media/foreign.jpg"},
              ])
    try:
        report = migrate_cross_event_cuts(
            library_root=tmp_path,
            user_store=user_store,
            list_events=_list_events_factory([("A", "A")]),
            open_event_store=_open_event_store_factory({"A": evt}),
        )
        assert report.migrated_cuts == 1
        assert report.migrated_members == 2
        # Source event.db row is gone (members cascade).
        assert evt.conn.execute(
            "SELECT COUNT(*) FROM cut WHERE id = 'cut-cross'").fetchone()[0] == 0
        assert evt.conn.execute(
            "SELECT COUNT(*) FROM cut_member WHERE cut_id = 'cut-cross'"
        ).fetchone()[0] == 0
        # Mira.db now holds the cut + every member.
        cut = user_store.conn.execute(
            "SELECT tag, source_dc_id, source_dc_kind FROM cut "
            "WHERE id = 'cut-cross'").fetchone()
        assert cut["tag"] == "tag_cut-cross"
        assert cut["source_dc_id"] == "sf-1"
        assert cut["source_dc_kind"] == "user"
        members = user_store.conn.execute(
            "SELECT event_id, member_id FROM cut_member "
            "WHERE cut_id = 'cut-cross' ORDER BY event_id"
        ).fetchall()
        assert [(m["event_id"], m["member_id"]) for m in members] == [
            ("A", "Exported Media/local.jpg"),
            ("B", "Exported Media/foreign.jpg"),
        ]
    finally:
        evt.close()
        user_store.close()


def test_event_scope_kind_event_not_inspected(tmp_path):
    """A Cut with ``source_dc_kind='event'`` is event-scope by source —
    not even inspected by the migration (the cheap pre-filter skips it)."""
    user_store = _open_user_store(tmp_path)
    evt = _make_event(tmp_path, eid="A")
    _seed_cut(evt, "cut-event", source_dc_kind="event",
              source_dc_id="event-dc-1",
              members=[
                  {"event_id": None, "kind": "export",
                   "export_relpath": "Exported Media/a.jpg"}])
    try:
        report = migrate_cross_event_cuts(
            library_root=tmp_path,
            user_store=user_store,
            list_events=_list_events_factory([("A", "A")]),
            open_event_store=_open_event_store_factory({"A": evt}),
        )
        assert report.inspected_cuts == 0
        assert report.migrated_cuts == 0
        # The event-scope cut sits intact in event.db.
        assert evt.conn.execute(
            "SELECT COUNT(*) FROM cut").fetchone()[0] == 1
    finally:
        evt.close()
        user_store.close()


def test_null_event_id_member_inherits_host_event_uuid(tmp_path):
    """A cross-event Cut may have a mix of local (event_id NULL by
    legacy convention) and foreign members. The migration substitutes
    the HOST event's UUID for the NULL — mira.db's cut_member.event_id
    is NOT NULL by schema."""
    user_store = _open_user_store(tmp_path)
    evt = _make_event(tmp_path, eid="host-event")
    _seed_cut(evt, "cut-mixed", source_dc_kind="user",
              source_dc_id="sf-1",
              members=[
                  {"event_id": None, "kind": "export",          # local legacy
                   "export_relpath": "Exported Media/local.jpg"},
                  {"event_id": "B", "kind": "export",
                   "export_relpath": "Exported Media/foreign.jpg"},
              ])
    try:
        migrate_cross_event_cuts(
            library_root=tmp_path,
            user_store=user_store,
            list_events=_list_events_factory([("host-event", "host")]),
            open_event_store=_open_event_store_factory(
                {"host-event": evt}),
        )
        rows = user_store.conn.execute(
            "SELECT event_id, member_id FROM cut_member "
            "WHERE cut_id = 'cut-mixed' ORDER BY event_id"
        ).fetchall()
        assert [(r["event_id"], r["member_id"]) for r in rows] == [
            ("B", "Exported Media/foreign.jpg"),
            ("host-event", "Exported Media/local.jpg"),
        ]
    finally:
        evt.close()
        user_store.close()


# --------------------------------------------------------------------------- #
# 2. Copy → verify → delete — verify failure leaves both stores intact
# --------------------------------------------------------------------------- #


def test_verify_failure_aborts_without_deleting_source(
        tmp_path, monkeypatch):
    """If the verify gate fires, the transaction rolls back, the
    event.db delete never runs, and the marker stays absent — the
    next migration retries from scratch.

    We force a mismatch by monkeypatching ``_verify_cut`` to raise."""
    user_store = _open_user_store(tmp_path)
    evt = _make_event(tmp_path, eid="A")
    _seed_cut(evt, "cut-cross", source_dc_kind="user",
              source_dc_id="sf-1",
              members=[
                  {"event_id": "B", "kind": "export",
                   "export_relpath": "Exported Media/x.jpg"}])

    from core import cross_event_cut_migrate as mod

    def _boom(*a, **kw):
        raise mod.CrossEventCutMigrationError("forced verify failure")

    monkeypatch.setattr(mod, "_verify_cut", _boom)
    try:
        with pytest.raises(mod.CrossEventCutMigrationError):
            mod.migrate_cross_event_cuts(
                library_root=tmp_path,
                user_store=user_store,
                list_events=_list_events_factory([("A", "A")]),
                open_event_store=_open_event_store_factory({"A": evt}),
            )
        # event.db still holds the cut + member.
        assert evt.conn.execute(
            "SELECT COUNT(*) FROM cut WHERE id = 'cut-cross'").fetchone()[0] == 1
        assert evt.conn.execute(
            "SELECT COUNT(*) FROM cut_member WHERE cut_id = 'cut-cross'"
        ).fetchone()[0] == 1
        # mira.db is empty — the rollback caught the INSERT before commit.
        assert user_store.conn.execute(
            "SELECT COUNT(*) FROM cut").fetchone()[0] == 0
        # Marker is NOT written.
        assert not marker_path(tmp_path).exists()
    finally:
        evt.close()
        user_store.close()


def test_member_verify_failure_aborts_too(tmp_path, monkeypatch):
    """The member-side verify is independent of the cut-side; a
    mismatch there also rolls the whole copy back."""
    user_store = _open_user_store(tmp_path)
    evt = _make_event(tmp_path, eid="A")
    _seed_cut(evt, "cut-cross", source_dc_kind="user",
              source_dc_id="sf-1",
              members=[
                  {"event_id": "B", "kind": "export",
                   "export_relpath": "Exported Media/x.jpg"}])

    from core import cross_event_cut_migrate as mod

    def _boom(*a, **kw):
        raise mod.CrossEventCutMigrationError("forced member verify failure")

    monkeypatch.setattr(mod, "_verify_members", _boom)
    try:
        with pytest.raises(mod.CrossEventCutMigrationError):
            mod.migrate_cross_event_cuts(
                library_root=tmp_path,
                user_store=user_store,
                list_events=_list_events_factory([("A", "A")]),
                open_event_store=_open_event_store_factory({"A": evt}),
            )
        # Both stores intact + marker absent.
        assert evt.conn.execute(
            "SELECT COUNT(*) FROM cut").fetchone()[0] == 1
        assert user_store.conn.execute(
            "SELECT COUNT(*) FROM cut").fetchone()[0] == 0
        assert not marker_path(tmp_path).exists()
    finally:
        evt.close()
        user_store.close()


# --------------------------------------------------------------------------- #
# 3. Idempotency + partial-recovery
# --------------------------------------------------------------------------- #


def test_marker_short_circuits_second_run(tmp_path):
    """A second invocation after success is a cheap no-op (skipped)."""
    user_store = _open_user_store(tmp_path)
    evt = _make_event(tmp_path, eid="A")
    _seed_cut(evt, "cut-cross", source_dc_kind="user",
              source_dc_id="sf-1",
              members=[
                  {"event_id": "B", "kind": "export",
                   "export_relpath": "Exported Media/x.jpg"}])
    try:
        first = migrate_cross_event_cuts(
            library_root=tmp_path,
            user_store=user_store,
            list_events=_list_events_factory([("A", "A")]),
            open_event_store=_open_event_store_factory({"A": evt}),
        )
        assert first.skipped is False
        assert first.migrated_cuts == 1
        # Second run is a cheap no-op.
        second = migrate_cross_event_cuts(
            library_root=tmp_path,
            user_store=user_store,
            list_events=_list_events_factory([("A", "A")]),
            open_event_store=_open_event_store_factory({"A": evt}),
        )
        assert second.skipped is True
        assert second.migrated_cuts == 0
    finally:
        evt.close()
        user_store.close()


def test_partial_recovery_when_target_already_present(tmp_path):
    """Simulate a previous run that committed the mira.db side but
    crashed before deleting the event.db rows. The next run finds the
    target row already there, skips the re-insert via _AlreadyMigrated,
    and still deletes the source. Convergence is preserved."""
    user_store = _open_user_store(tmp_path)
    evt = _make_event(tmp_path, eid="A")
    _seed_cut(evt, "cut-cross", source_dc_kind="user",
              source_dc_id="sf-1",
              members=[
                  {"event_id": "B", "kind": "export",
                   "export_relpath": "Exported Media/x.jpg"}])
    # Pre-populate the target row in mira.db as if a previous run
    # committed it.
    with user_store.transaction() as conn:
        conn.execute(
            "INSERT INTO cut (id, tag, source_dc_id, source_dc_kind, "
            "                 created_at, updated_at) "
            "VALUES (?, ?, 'sf-1', 'user', ?, ?)",
            ("cut-cross", "tag_cut-cross", NOW, NOW))
        conn.execute(
            "INSERT INTO cut_member (cut_id, event_id, member_id, kind, "
            "                        export_relpath, added_at) "
            "VALUES (?, ?, ?, 'export', ?, ?)",
            ("cut-cross", "B", "Exported Media/x.jpg",
             "Exported Media/x.jpg", NOW))
    try:
        report = migrate_cross_event_cuts(
            library_root=tmp_path,
            user_store=user_store,
            list_events=_list_events_factory([("A", "A")]),
            open_event_store=_open_event_store_factory({"A": evt}),
        )
        # The recovery counts as a migrated cut (the source got deleted).
        assert report.migrated_cuts == 1
        # Source event.db is now clean.
        assert evt.conn.execute(
            "SELECT COUNT(*) FROM cut WHERE id = 'cut-cross'").fetchone()[0] == 0
        # Mira.db row is unchanged (no double-insert).
        cnt = user_store.conn.execute(
            "SELECT COUNT(*) FROM cut").fetchone()[0]
        assert cnt == 1
    finally:
        evt.close()
        user_store.close()


def test_unopenable_event_skipped_not_raised(tmp_path):
    """An event whose store can't open is counted as skipped — the
    migration walks the rest of the library."""
    user_store = _open_user_store(tmp_path)
    evt_a = _make_event(tmp_path, eid="A")
    _seed_cut(evt_a, "cut-cross", source_dc_kind="user",
              members=[
                  {"event_id": "B", "kind": "export",
                   "export_relpath": "Exported Media/x.jpg"}])
    try:
        report = migrate_cross_event_cuts(
            library_root=tmp_path,
            user_store=user_store,
            list_events=_list_events_factory(
                [("A", "A"), ("ghost", "Ghost")]),
            open_event_store=_open_event_store_factory({"A": evt_a}),
        )
        assert report.events_visited == 1
        assert report.events_skipped == 1
        assert report.migrated_cuts == 1
    finally:
        evt_a.close()
        user_store.close()


# --------------------------------------------------------------------------- #
# Multiple events / many cuts
# --------------------------------------------------------------------------- #


def test_migrate_walks_every_event(tmp_path):
    """The migration visits every event in the library and migrates
    cross-event Cuts wherever they live."""
    user_store = _open_user_store(tmp_path)
    e1 = _make_event(tmp_path, eid="E1")
    e2 = _make_event(tmp_path, eid="E2")
    _seed_cut(e1, "cross-1", source_dc_kind="user",
              source_dc_id="sf-1",
              members=[
                  {"event_id": "X", "kind": "export",
                   "export_relpath": "Exported Media/a.jpg"}])
    _seed_cut(e2, "cross-2", source_dc_kind="user",
              source_dc_id="sf-2",
              members=[
                  {"event_id": "Y", "kind": "export",
                   "export_relpath": "Exported Media/b.jpg"}])
    # Local-only cut in E1 (stays put).
    _seed_cut(e1, "stay-put", source_dc_kind="user",
              source_dc_id="sf-3",
              members=[
                  {"event_id": None, "kind": "export",
                   "export_relpath": "Exported Media/local.jpg"}])
    try:
        report = migrate_cross_event_cuts(
            library_root=tmp_path,
            user_store=user_store,
            list_events=_list_events_factory(
                [("E1", "E1"), ("E2", "E2")]),
            open_event_store=_open_event_store_factory(
                {"E1": e1, "E2": e2}),
        )
        assert report.migrated_cuts == 2
        assert report.migrated_members == 2
        assert report.inspected_cuts == 3   # the stay-put was inspected
        ids_in_mira = {
            r["id"] for r in user_store.conn.execute(
                "SELECT id FROM cut ORDER BY id").fetchall()}
        assert ids_in_mira == {"cross-1", "cross-2"}
        # The stay-put cut still lives in E1.
        assert e1.conn.execute(
            "SELECT COUNT(*) FROM cut WHERE id = 'stay-put'").fetchone()[0] == 1
    finally:
        e1.close()
        e2.close()
        user_store.close()
