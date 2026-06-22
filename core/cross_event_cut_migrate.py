"""Cross-event Cut migration — spec/94 Phase 4a-ii.

Spec/93 §3 puts cross-event Cuts in **mira.db** (the library store).
Phase-2 + the Item-4 polish put them in an *anchor event's* event.db
with ``source_dc_kind = 'user'``. Phase 4a-ii closes the gap with a
one-shot migration that moves the right rows out of every event.db
into mira.db.

Two correctness rules (Nelson's call when approving the ii plan):

1. **The discriminator is the MEMBERSHIP SHAPE, not ``source_dc_kind``
   alone.** After Phase 2 an ordinary event-scope Cut that was pinned
   from a global Collection legitimately carries ``source_dc_kind = 'user'``
   too — but every one of its ``cut_member`` rows has ``event_id IS NULL``
   (local-to-this-event by convention). Only Cuts with AT LEAST ONE
   member whose ``event_id`` is non-NULL are cross-event by spec/93 §3
   and migrate; the rest stay put in their event.db.

2. **Copy → verify → delete** (no fallback path post-flip, so a
   crash mid-migration must not lose a Cut). For each eligible Cut:
   * Write the cut row + every cut_member row into mira.db.
   * **Re-read** mira.db and compare every field — column-by-column —
     against the source event.db rows. Mismatch (or any exception in
     the verify) → log + abort the whole migration; the marker is NOT
     set, both stores remain intact, and the next run retries from
     scratch.
   * Only after verify passes do we DELETE the source rows from the
     event.db. Members cascade via the event.db cut_id FK.

3. **Idempotent + marker-gated.** :data:`MARKER_FILENAME` short-circuits
   the next run. Discriminator + verify make a partial re-run safe
   too: a Cut already copied to mira.db won't double-migrate (the
   source rows are gone), and the verify gate is independent of
   marker state.

Pure logic + filesystem + SQLite. No Qt. The caller (Gateway init)
wires the gateways and invokes :func:`migrate_cross_event_cuts`.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)


#: Marker filename inside ``<library_root>/.mira/``. Its presence
#: short-circuits :func:`migrate_cross_event_cuts`; its absence is the
#: trigger for the one-shot migration.
MARKER_FILENAME = "cross_event_cut_migration.json"

#: Bumped together with a code change that warrants re-running the
#: migration (e.g. a schema reshape that needs a fresh pass).
MARKER_SCHEMA_VERSION = 1


class CrossEventCutMigrationError(RuntimeError):
    """Migration aborted mid-run — the verify gate fired or a per-row
    error surfaced. Both stores are left intact; the marker is NOT
    written so the next run retries."""


@dataclass(frozen=True)
class CrossEventCutMigrationReport:
    """Summary returned to the caller. ``skipped`` is True when the
    marker said the migration had already run."""
    skipped: bool
    migrated_cuts: int = 0
    migrated_members: int = 0
    inspected_cuts: int = 0
    events_visited: int = 0
    events_skipped: int = 0

    @classmethod
    def already_done(cls) -> "CrossEventCutMigrationReport":
        return cls(skipped=True)


def marker_path(library_root: Path) -> Path:
    """Resolve the marker file's location. Centralised so the wirer
    can probe it (e.g. for an "already migrated" log line) and so
    tests can override the directory."""
    return library_root / ".mira" / MARKER_FILENAME


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# The cut + cut_member shape we copy. Stable here so the verify gate's
# comparison is exact — adding a column on either side without updating
# this tuple is the gate firing.
# --------------------------------------------------------------------------- #


#: Columns copied from event.db ``cut`` → mira.db ``cut``. Order is
#: shared by the INSERT + the verify SELECT so the comparison reads as
#: a single tuple equality.
_CUT_COLUMNS: Tuple[str, ...] = (
    "id", "tag", "source_dc_id", "source_dc_kind",
    "expr_snapshot_json", "target_s", "max_s", "photo_s",
    "default_state", "music_category", "separators",
    "overlay_fields_json", "overlay_mode", "last_exported_at",
    "created_at", "updated_at", "extras_json",
)

#: Columns copied from event.db ``cut_member`` → mira.db ``cut_member``.
#: ``event_id`` is REQUIRED in the target (cross-event Cuts span events).
#: If the source row has ``event_id IS NULL`` we substitute the host
#: event's UUID (legacy "anchor event" convention) — but discrimination
#: above guarantees we never migrate a row where every member is NULL.
_MEMBER_COLUMNS: Tuple[str, ...] = (
    "cut_id", "event_id", "member_id", "kind",
    "export_relpath", "origin_relpath", "added_at",
)


# --------------------------------------------------------------------------- #
# Public entry
# --------------------------------------------------------------------------- #


def migrate_cross_event_cuts(
    *,
    library_root: Path,
    user_store,
    list_events,
    open_event_store,
) -> CrossEventCutMigrationReport:
    """Run the one-shot migration. Marker-gated; copy-verify-delete per
    Cut; partial failures abort cleanly (both stores intact).

    Parameters
    ----------
    library_root:
        Used to locate the marker file (``<root>/.mira/<MARKER>``).
        The lock + write atomicity are the caller's responsibility.
    user_store:
        Open :class:`mira.user_store.repo.UserStore` for mira.db. The
        migration runs the cut + cut_member inserts inside its
        ``transaction()`` so a verify failure rolls back the partial
        write before we touch the event.db.
    list_events:
        Callable returning an iterable of ``(event_uuid, name)`` for
        every event the library knows about. (Gateway.list_events()
        with a small projection.)
    open_event_store:
        ``open_event_store(uuid) -> EventStore | None``. The caller
        decides the open policy (lock-aware? read-only fallback?). A
        store that can't open counts as ``events_skipped``; never
        raises.
    """
    mp = marker_path(library_root)
    if mp.exists():
        return CrossEventCutMigrationReport.already_done()

    inspected = 0
    migrated_cuts = 0
    migrated_members = 0
    visited = 0
    skipped = 0
    for event_uuid, _event_name in list_events():
        store = None
        try:
            store = open_event_store(event_uuid)
        except Exception as exc:                              # noqa: BLE001
            log.warning(
                "cross_event_cut_migrate: open failed for %s — skipping: %s",
                event_uuid, exc)
            skipped += 1
            continue
        if store is None:
            skipped += 1
            continue
        try:
            visited += 1
            for cut_row in _candidates_in_event(store):
                inspected += 1
                members = _read_members(store, cut_row["id"])
                if not _is_cross_event(members):
                    continue
                _migrate_one(
                    user_store=user_store,
                    event_store=store,
                    host_event_uuid=event_uuid,
                    cut_row=cut_row,
                    members=members)
                migrated_cuts += 1
                migrated_members += len(members)
        finally:
            try:
                store.close()
            except Exception:                                  # noqa: BLE001
                pass

    _write_marker(mp, migrated_cuts=migrated_cuts,
                  migrated_members=migrated_members)
    return CrossEventCutMigrationReport(
        skipped=False,
        migrated_cuts=migrated_cuts,
        migrated_members=migrated_members,
        inspected_cuts=inspected,
        events_visited=visited,
        events_skipped=skipped,
    )


# --------------------------------------------------------------------------- #
# Per-Cut copy → verify → delete
# --------------------------------------------------------------------------- #


def _migrate_one(
    *,
    user_store,
    event_store,
    host_event_uuid: str,
    cut_row,
    members: Sequence,
) -> None:
    """Copy + verify + delete for one Cut.

    Failure path: the user_store write is wrapped in a transaction
    that ROLLS BACK on any exception (including the verify mismatch),
    leaving mira.db unchanged. The event.db delete only runs after
    the user_store commit; a crash between the commit and the
    delete leaves mira.db with the cut + event.db with the source,
    which the next run notices via the discriminator (cut id already
    exists in mira.db → skip duplicate insert) — see the
    :func:`_assert_target_unset` guard."""
    cut_id = cut_row["id"]
    cut_tuple = tuple(cut_row[c] for c in _CUT_COLUMNS)
    member_tuples = tuple(
        tuple(
            (host_event_uuid
             if c == "event_id" and m["event_id"] is None
             else m[c])
            for c in _MEMBER_COLUMNS)
        for m in members)

    # 1. Write into mira.db inside a transaction so a verify failure
    #    rolls back cleanly. The transaction context manager commits on
    #    a clean exit and rolls back on any exception.
    already_migrated = False
    try:
        with user_store.transaction() as conn:
            _assert_target_unset(conn, cut_id)
            _insert_cut(conn, cut_tuple)
            _insert_members(conn, member_tuples)
            # Verify inside the same transaction so a mismatch raises
            # → context manager rolls back → mira.db is unchanged.
            _verify_cut(conn, cut_id, cut_tuple)
            _verify_members(conn, cut_id, member_tuples)
    except _AlreadyMigrated:
        # Recovery: a previous run committed mira.db but didn't reach
        # the event.db delete (crashed between commit + delete, or
        # marker write failed). Skip the copy + verify; the delete
        # below converges the state.
        already_migrated = True
    except CrossEventCutMigrationError:
        raise
    except Exception as exc:                                  # noqa: BLE001
        raise CrossEventCutMigrationError(
            f"copy or verify failed for cut {cut_id}: {exc}") from exc

    # 2. The mira.db copy is committed + verified (or already there).
    #    NOW we can delete from event.db. cut_member rows cascade via
    #    the cut_id FK (event.db schema).
    with event_store.transaction() as conn:
        conn.execute("DELETE FROM cut WHERE id = ?", (cut_id,))
    log.info("migrate_cross_event_cuts: %s cut %s (%d members)",
             "recovered" if already_migrated else "moved",
             cut_id, len(members))


def _candidates_in_event(event_store) -> List:
    """Cuts in this event.db whose ``source_dc_kind = 'user'`` — the
    pre-filter. The membership-shape discriminator runs against each
    candidate's members separately, so this query is the cheap pass."""
    return event_store.conn.execute(
        "SELECT * FROM cut WHERE source_dc_kind = 'user' "
        "ORDER BY created_at, id"
    ).fetchall()


def _read_members(event_store, cut_id: str) -> List:
    """Every cut_member row for a candidate. Order is insertion-stable
    (added_at then member_id) so the verify-side comparison is
    deterministic across rebuilds."""
    return event_store.conn.execute(
        "SELECT * FROM cut_member WHERE cut_id = ? "
        "ORDER BY added_at, member_id",
        (cut_id,),
    ).fetchall()


def _is_cross_event(members: Sequence) -> bool:
    """The discriminator (Nelson 2026-06-21): a Cut is cross-event iff
    AT LEAST ONE member has a non-NULL ``event_id``. An all-local-
    members Cut (every event_id NULL — the legacy event-scope
    convention) stays put even if its ``source_dc_kind = 'user'`` — it
    was pinned from a global Collection but its members live in this
    one event."""
    return any(m["event_id"] is not None for m in members)


# --------------------------------------------------------------------------- #
# The mira.db writes + the verify gate
# --------------------------------------------------------------------------- #


def _assert_target_unset(conn, cut_id: str) -> None:
    """A previous partial run could have committed the mira.db side
    without finishing the event.db delete. If the cut already exists
    in mira.db, we DELETE the event.db rows below without re-writing
    — the migration converges to the post-flip state either way."""
    row = conn.execute("SELECT 1 FROM cut WHERE id = ?", (cut_id,)).fetchone()
    if row is not None:
        raise _AlreadyMigrated()


class _AlreadyMigrated(Exception):
    """Internal sentinel: the target row exists, we just need to
    delete the source. Surfaces through :func:`_migrate_one` as a
    quiet skip (the outer migration loop counts it; the marker hits
    next run)."""


def _insert_cut(conn, cut_tuple: Tuple) -> None:
    placeholders = ", ".join(["?"] * len(_CUT_COLUMNS))
    cols = ", ".join(_CUT_COLUMNS)
    conn.execute(
        f"INSERT INTO cut ({cols}) VALUES ({placeholders})", cut_tuple)


def _insert_members(conn, member_tuples: Sequence[Tuple]) -> None:
    if not member_tuples:
        return
    placeholders = ", ".join(["?"] * len(_MEMBER_COLUMNS))
    cols = ", ".join(_MEMBER_COLUMNS)
    conn.executemany(
        f"INSERT INTO cut_member ({cols}) VALUES ({placeholders})",
        member_tuples)


def _verify_cut(conn, cut_id: str,
                source_tuple: Tuple) -> None:
    """Re-read the just-inserted cut row and compare field-for-field
    against the source. Any mismatch raises → the transaction rolls
    back → both stores intact."""
    cols = ", ".join(_CUT_COLUMNS)
    row = conn.execute(
        f"SELECT {cols} FROM cut WHERE id = ?", (cut_id,)).fetchone()
    if row is None:
        raise CrossEventCutMigrationError(
            f"verify: cut {cut_id} not found after insert")
    dest_tuple = tuple(row[c] for c in _CUT_COLUMNS)
    if dest_tuple != source_tuple:
        raise CrossEventCutMigrationError(
            f"verify: cut {cut_id} field mismatch — "
            f"src={source_tuple!r} dst={dest_tuple!r}")


def _verify_members(conn, cut_id: str,
                    source_tuples: Sequence[Tuple]) -> None:
    """Re-read every member of the just-inserted cut. Order is
    deterministic on both sides (added_at, event_id, member_id) so
    the comparison is exact."""
    cols = ", ".join(_MEMBER_COLUMNS)
    rows = conn.execute(
        f"SELECT {cols} FROM cut_member WHERE cut_id = ? "
        "ORDER BY added_at, event_id, member_id",
        (cut_id,)).fetchall()
    dest_tuples = tuple(
        tuple(r[c] for c in _MEMBER_COLUMNS) for r in rows)
    # Source side: re-sort by the same key as the destination so the
    # comparison is order-stable (the source query orders by
    # added_at + member_id; dest query orders by added_at + event_id
    # + member_id).
    src_sorted = tuple(sorted(
        source_tuples,
        key=lambda t: (t[_MEMBER_COLUMNS.index("added_at")],
                       t[_MEMBER_COLUMNS.index("event_id")] or "",
                       t[_MEMBER_COLUMNS.index("member_id")])))
    if dest_tuples != src_sorted:
        raise CrossEventCutMigrationError(
            f"verify: cut_member rows for {cut_id} mismatch — "
            f"src={src_sorted!r} dst={dest_tuples!r}")


# --------------------------------------------------------------------------- #
# Marker file
# --------------------------------------------------------------------------- #


def _write_marker(path: Path, *, migrated_cuts: int,
                  migrated_members: int) -> None:
    """Atomic write-then-rename (charter invariant #6). The marker
    contents are informational — the migration's gate is the file's
    presence."""
    payload = {
        "marker_schema_version": MARKER_SCHEMA_VERSION,
        "ran_at": _utc_now_iso(),
        "migrated_cuts": migrated_cuts,
        "migrated_members": migrated_members,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


__all__ = [
    "MARKER_FILENAME",
    "MARKER_SCHEMA_VERSION",
    "CrossEventCutMigrationError",
    "CrossEventCutMigrationReport",
    "marker_path",
    "migrate_cross_event_cuts",
]
