"""Cross-event integrity sweeps (spec/81 Phase 2 polish — final).

The cross-event surface has TWO integrity gaps that the per-store FKs and
gateway methods can't enforce alone — both need a walk across every
event.db in the library:

1. **Stale ``cut_member`` rows** — when a source event is deleted out of
   band, cross-event Cuts in other event.db files keep dangling cut_member
   rows pointing at it. :func:`sweep_dangling_cross_event_members` walks
   the library and drops them.

2. **Dangling ``cut.source_dc_id``** — when a cross-event DC
   (``saved_filter`` row in mira.db) is deleted, cuts in event.db files
   that pointed at it keep their stale id. :func:`sweep_dc_references`
   NULLs them, plus their ``source_dc_kind``.

Pure logic — these functions take the umbrella :class:`Gateway` (for index
+ event store open) and operate on its surfaces. No Qt. Run on demand
from a maintenance trigger or alongside :meth:`LibraryGateway.delete_dc`.
"""
from __future__ import annotations

import logging
from typing import Iterable

log = logging.getLogger(__name__)


def sweep_dangling_cross_event_members(gateway) -> dict:
    """Walk every event.db; for each cross-event cut_member row whose
    ``event_id`` references an event no longer in the index, drop it.
    Returns a summary ``{visited, dropped, events_skipped}``."""
    from mira.store.repo import EventStore
    known_event_ids: set = set()
    for entry in gateway.list_events():
        eid = entry.get("id") or entry.get("uuid")
        if eid:
            known_event_ids.add(eid)
    visited = 0
    dropped = 0
    skipped = 0
    for entry in gateway.list_events():
        host_id = entry.get("id") or entry.get("uuid")
        root = gateway.index.resolve_root(entry, gateway.photos_base_path())
        if root is None or not (root / "event.db").exists():
            skipped += 1
            continue
        try:
            store = EventStore.open(root / "event.db")
        except Exception:                                  # noqa: BLE001
            log.warning(
                "sweep: could not open %s — skipping", root)
            skipped += 1
            continue
        try:
            rows = store.conn.execute(
                "SELECT cut_id, member_id, event_id FROM cut_member "
                "WHERE event_id IS NOT NULL"
            ).fetchall()
            visited += len(rows)
            stale = [(r["cut_id"], r["member_id"])
                     for r in rows
                     if r["event_id"] not in known_event_ids]
            if stale:
                with store.transaction() as conn:
                    conn.executemany(
                        "DELETE FROM cut_member WHERE cut_id = ? "
                        "AND member_id = ?",
                        stale)
                dropped += len(stale)
                log.info(
                    "sweep: dropped %d dangling members in %s",
                    len(stale), host_id)
        finally:
            store.close()
    return {
        "visited": visited,
        "dropped": dropped,
        "events_skipped": skipped,
    }


def sweep_dc_references(gateway, deleted_dc_id: str) -> dict:
    """NULL ``cut.source_dc_id`` + ``cut.source_dc_kind`` on every event.db
    cut that pointed at ``deleted_dc_id``. spec/81 §5 freeze invariant:
    the cut survives, its members + snapshot are untouched; only the
    reference goes."""
    from mira.store.repo import EventStore
    visited = 0
    nulled = 0
    skipped = 0
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
