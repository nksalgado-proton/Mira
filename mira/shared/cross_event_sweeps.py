"""Cross-event integrity sweeps (spec/81 Phase 2 polish — final;
spec/94 Phase 4a-ii — repointed at mira.db).

Two integrity gaps the per-store FKs can't enforce alone:

1. **Stale ``cut_member`` rows** — when a source event is deleted out
   of band, cross-event Cuts in mira.db keep dangling cut_member rows
   pointing at it. :func:`sweep_dangling_cross_event_members` walks
   mira.db and drops them.

2. **Dangling ``cut.source_dc_id``** — when a cross-event DC
   (``saved_filter`` row in mira.db) is deleted, cuts that pointed at
   it keep their stale id. :func:`sweep_dc_references` NULLs them in
   BOTH stores: mira.db cross-event Cuts AND event.db event-scope
   Cuts that legitimately pinned a global Collection (a per-event
   Cut with ``source_dc_kind='user'`` and all-local members — these
   stay in their event.db per spec/93 §3, but their source_dc_id
   reference into mira.db still needs cleaning up).

Pure logic — take the umbrella :class:`Gateway` (for index + event
store open + library gateway access). No Qt. Run on demand from a
maintenance trigger or alongside the cross-store delete-DC path.
"""
from __future__ import annotations

import logging
from typing import Iterable

log = logging.getLogger(__name__)


def sweep_dangling_cross_event_members(gateway) -> dict:
    """spec/94 Phase 4a-ii — walk mira.db's ``cut_member`` rows; for
    each whose ``event_id`` references an event no longer in the
    index, drop it. Returns ``{visited, dropped, events_skipped=0}``.

    Cross-event members live in mira.db only post-migration; the
    event.db sweep the pre-Phase 4a code did is no longer needed
    (event.db cut_member rows are all event-local by construction
    after the migration).
    """
    lg = gateway.library_gateway()
    known_event_ids: set = set()
    for entry in gateway.list_events():
        eid = entry.get("id") or entry.get("uuid")
        if eid:
            known_event_ids.add(eid)
    rows = lg.user_store.conn.execute(
        "SELECT cut_id, event_id, member_id FROM cut_member"
    ).fetchall()
    visited = len(rows)
    stale = [(r["cut_id"], r["event_id"], r["member_id"])
             for r in rows
             if r["event_id"] not in known_event_ids]
    if stale:
        with lg.user_store.transaction() as conn:
            conn.executemany(
                "DELETE FROM cut_member WHERE cut_id = ? "
                "AND event_id = ? AND member_id = ?",
                stale)
        log.info("sweep: dropped %d dangling cross-event members",
                 len(stale))
    return {
        "visited": visited,
        "dropped": len(stale),
        "events_skipped": 0,
    }


def sweep_dc_references(gateway, deleted_dc_id: str) -> dict:
    """NULL ``cut.source_dc_id`` + ``cut.source_dc_kind`` on every
    Cut that pointed at ``deleted_dc_id``. spec/81 §5 freeze invariant:
    the cut survives, its members + snapshot are untouched; only the
    reference goes.

    spec/94 Phase 4a-ii: the sweep covers BOTH stores. Cross-event
    Cuts live in mira.db (single UPDATE); event-scope Cuts that
    legitimately pinned a global Collection live in event.db (one
    UPDATE per event.db file).
    """
    from mira.store.repo import EventStore
    visited = 0
    nulled = 0
    skipped = 0

    # --- mira.db: cross-event Cuts (the bulk of the case) -----------
    lg = gateway.library_gateway()
    mira_count = lg.user_store.conn.execute(
        "SELECT COUNT(*) AS n FROM cut "
        "WHERE source_dc_kind = 'user' AND source_dc_id = ?",
        (deleted_dc_id,),
    ).fetchone()
    n_mira = int(mira_count["n"] or 0) if mira_count else 0
    visited += n_mira
    if n_mira:
        with lg.user_store.transaction() as conn:
            conn.execute(
                "UPDATE cut SET source_dc_id = NULL, "
                "source_dc_kind = NULL "
                "WHERE source_dc_kind = 'user' AND source_dc_id = ?",
                (deleted_dc_id,))
        nulled += n_mira

    # --- event.db: event-scope Cuts pinned from a global Collection -
    for entry in gateway.list_events():
        root = gateway.index.resolve_root(entry, gateway.photos_base_path())
        if root is None or not (root / "event.db").exists():
            skipped += 1
            continue
        try:
            store = EventStore.open(root / "event.db")
        except Exception:                                  # noqa: BLE001
            log.warning(
                "sweep_dc_references: could not open %s — skipping", root)
            skipped += 1
            continue
        try:
            count = store.conn.execute(
                "SELECT COUNT(*) AS n FROM cut "
                "WHERE source_dc_kind = 'user' AND source_dc_id = ?",
                (deleted_dc_id,),
            ).fetchone()
            n = int(count["n"] or 0) if count else 0
            visited += n
            if n:
                with store.transaction() as conn:
                    conn.execute(
                        "UPDATE cut SET source_dc_id = NULL, "
                        "source_dc_kind = NULL "
                        "WHERE source_dc_kind = 'user' AND source_dc_id = ?",
                        (deleted_dc_id,))
                nulled += n
        finally:
            store.close()
    return {
        "visited": visited,
        "nulled": nulled,
        "events_skipped": skipped,
    }


__all__ = [
    "sweep_dangling_cross_event_members",
    "sweep_dc_references",
]
