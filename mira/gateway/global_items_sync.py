"""Cross-event ``global_items`` projection sync (spec/32 §3 + spec/81 Phase 2).

The cross-event surface needs to query item facts across every event in the
library. Fanning out across N ``event.db`` files per keystroke is too slow;
spec/32 §3's answer is a **denormalised projection** in the user-level
``mira.db`` that the cross-event resolver hits directly.

This module owns the **sync seam**: given an open per-event store, produce one
``GlobalItem`` row per item (current ladder state, key EXIF facets, day
location) and upsert into the user store inside one transaction. The
projection replaces the entire event's slice — synced rows for the event are
deleted first, then re-inserted, so a removed item disappears from the index
and a re-classified item resurfaces with the new value. Same-shape calls drive
both triggers (spec/81 Phase 2 handover recommendation): **on event close** +
**startup reconcile**.

Pure-shape logic — the gateway layer is the right home (it crosses both stores;
``core/`` stays Qt-free + store-shape-free). No UI imports, no network. The
write path uses ``UserStore.transaction`` so a half-failed sync rolls back
(charter invariant: atomic write-then-rename for persisted state).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Callable, Iterable, List, Optional

from mira.store.repo import EventStore
from mira.user_store import models as um
from mira.user_store.repo import UserStore

log = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Projection — one big SQL that joins everything the row needs
# --------------------------------------------------------------------------- #


# The projection query. Notes:
#   * ``LEFT JOIN phase_state ps_pick`` / ``ps_edit`` carry the per-phase state
#     independently; NULL = no row for that (item, phase) yet.
#   * ``has_export`` reads ``EXISTS`` over lineage on either the item directly
#     or the merged output of its source bracket (the lineage discriminator).
#   * ``day_city`` prefers the per-day ``trip_day.location`` free-text (the
#     legacy field the UI writes); ``extras_json.city`` is the structured
#     fallback (spec/32 §2c).
#   * The portfolio bit lives in ``item.extras_json.flag`` (the new locked
#     name — spec/32 §2a; ``pick`` retired with the locked verb pair).
#   * The query reads through the base ``item`` table (not ``visible_item``):
#     the projection includes hidden-day items too. The resolver / filter UI
#     decides whether to expose them; baking visibility in here would lose
#     the rung "items on hidden days are still collected".
_PROJECTION_SQL = """
SELECT
  i.id                          AS item_id,
  i.origin_relpath               AS origin_relpath,
  i.capture_time_corrected       AS capture_time,
  i.kind                         AS kind,
  i.provenance                   AS provenance,
  i.classification               AS classification,
  i.iso                          AS iso,
  i.aperture_f                   AS aperture_f,
  i.shutter_speed_s              AS shutter_speed_s,
  i.focal_length_mm              AS focal_length_mm,
  i.flash_fired                  AS flash_fired,
  i.lens_model                   AS lens_model,
  i.camera_id                    AS camera_id,
  i.duration_ms                  AS duration_ms,
  ps_pick.state                  AS pick_state,
  ps_edit.state                  AS edit_state,
  CASE WHEN EXISTS (
    SELECT 1 FROM lineage l
    WHERE  (l.source_kind = 'item'    AND l.source_item_id = i.id)
       OR  (l.source_kind = 'bracket'
            AND l.source_bracket_id IN (
              SELECT sb.bracket_id FROM stack_bracket sb
              WHERE sb.output_item_id = i.id
            ))
  ) THEN 1 ELSE 0 END             AS has_export,
  -- The latest exported relpath per item (NULL if not yet shipped).
  -- ``MAX(exported_at)`` picks the most recent ship; if multiple lineage
  -- rows share that timestamp the relpath order is the sub-tiebreaker.
  (SELECT l2.export_relpath FROM lineage l2
    WHERE (l2.source_kind = 'item'    AND l2.source_item_id = i.id)
       OR (l2.source_kind = 'bracket'
           AND l2.source_bracket_id IN (
             SELECT sb2.bracket_id FROM stack_bracket sb2
             WHERE sb2.output_item_id = i.id))
    ORDER BY COALESCE(l2.exported_at, '') DESC, l2.export_relpath DESC
    LIMIT 1)                      AS export_relpath,
  td.location                    AS td_location,
  td.extras_json                 AS td_extras,
  json_extract(i.extras_json, '$.stars')        AS stars,
  json_extract(i.extras_json, '$.color_label')  AS color_label,
  json_extract(i.extras_json, '$.flag')         AS flag
FROM item i
LEFT JOIN phase_state ps_pick ON ps_pick.item_id = i.id AND ps_pick.phase = 'pick'
LEFT JOIN phase_state ps_edit ON ps_edit.item_id = i.id AND ps_edit.phase = 'edit'
LEFT JOIN trip_day    td      ON td.day_number  = i.day_number
ORDER BY i.id
"""


def _split_day_location(td_location: Optional[str],
                        td_extras: Optional[str]) -> tuple:
    """Pull (country, country_code, day_city, day_sublocation) out of a
    trip_day row. ``td.extras_json`` is the structured source (country /
    country_code per spec/32 §2c); ``td.location`` is the legacy free-text
    user-facing field — used as ``day_city`` when ``extras_json.city`` is
    absent. Tolerant of malformed JSON (logs a warning, treats as empty)."""
    country: Optional[str] = None
    country_code: Optional[str] = None
    day_city: Optional[str] = None
    day_sublocation: Optional[str] = None
    if td_extras:
        try:
            extras = json.loads(td_extras)
        except (ValueError, TypeError):
            extras = {}
        if isinstance(extras, dict):
            country = extras.get("country") or None
            country_code = extras.get("country_code") or None
            day_city = extras.get("city") or None
            day_sublocation = extras.get("sublocation") or None
    if not day_city and td_location:
        day_city = td_location
    return country, country_code, day_city, day_sublocation


def _read_event_qualifiers(event_store: EventStore) -> tuple:
    """Pull the spec/86 event-level qualifiers off the singleton ``event``
    row (the schema enforces ``id = 1``). Returns
    ``(event_type, event_subtype, experience_type, participants_json)``.

    ``participants`` round-trips as the raw JSON envelope — the resolver
    expands it via ``json_each`` and the inventory query treats it as a JSON
    array. Empty arrays survive intact so the user-store NULL semantics
    distinguish "no participants set" (NULL) from "explicitly empty" (``[]``).
    """
    row = event_store.conn.execute(
        "SELECT event_type, event_subtype, experience_type, participants "
        "FROM event WHERE id = 1"
    ).fetchone()
    if row is None:
        return (None, None, None, None)
    return (
        row["event_type"], row["event_subtype"],
        row["experience_type"], row["participants"],
    )


def _derive_event_span(event_store: EventStore) -> tuple:
    """Derive ``(event_start, event_end)`` from min/max of ``trip_day.date``
    (spec/86 §5). ``trip_day.date`` is nullable (undated days), so the MIN /
    MAX skip NULLs. An event with zero dated days lands ``(None, None)`` —
    the resolver's overlap then never fires for an event_date filter against
    that event, which is the right behaviour (no information to bound on)."""
    row = event_store.conn.execute(
        "SELECT MIN(date) AS start_date, MAX(date) AS end_date "
        "FROM trip_day WHERE date IS NOT NULL"
    ).fetchone()
    if row is None:
        return (None, None)
    return (row["start_date"], row["end_date"])


def project_event(
    *,
    event_store: EventStore,
    event_uuid: str,
    event_name: str,
    now: Callable[[], str] = _utc_now_iso,
) -> List[um.GlobalItem]:
    """Produce the projection rows for ONE open event store. Pure read — no
    write. Returns ``GlobalItem`` rows in item-id order (deterministic for
    diffing in tests). Used by :func:`sync_event` for the write step and by
    consistency-audit tooling that wants the projection without persisting it.
    """
    stamp = now()
    # spec/86 — event-level qualifiers + derived span read ONCE; every
    # projection row carries the same values (the projection is denormalised
    # on purpose: the resolver filters without joining trip_day or event).
    event_type, event_subtype, experience_type, participants = \
        _read_event_qualifiers(event_store)
    event_start, event_end = _derive_event_span(event_store)
    rows: List[um.GlobalItem] = []
    cur = event_store.conn.execute(_PROJECTION_SQL)
    for r in cur.fetchall():
        country, country_code, day_city, day_sublocation = _split_day_location(
            r["td_location"], r["td_extras"]
        )
        rows.append(um.GlobalItem(
            event_uuid=event_uuid,
            event_name=event_name,
            item_id=r["item_id"],
            synced_at=stamp,
            origin_relpath=r["origin_relpath"],
            export_relpath=r["export_relpath"],
            capture_time=r["capture_time"],
            kind=r["kind"],
            provenance=r["provenance"],
            classification=r["classification"],
            iso=r["iso"],
            aperture_f=r["aperture_f"],
            shutter_speed_s=r["shutter_speed_s"],
            focal_length_mm=r["focal_length_mm"],
            flash_fired=r["flash_fired"],
            lens_model=r["lens_model"],
            camera_id=r["camera_id"],
            duration_ms=r["duration_ms"],
            pick_state=r["pick_state"],
            edit_state=r["edit_state"],
            has_export=bool(r["has_export"]),
            country=country,
            country_code=country_code,
            day_city=day_city,
            day_sublocation=day_sublocation,
            stars=r["stars"],
            color_label=r["color_label"],
            flag=r["flag"],
            event_type=event_type,
            event_subtype=event_subtype,
            experience_type=experience_type,
            participants=participants,
            event_start=event_start,
            event_end=event_end,
        ))
    return rows


# --------------------------------------------------------------------------- #
# Write — replace-the-event's-slice in one transaction
# --------------------------------------------------------------------------- #


def sync_event(
    *,
    event_store: EventStore,
    user_store: UserStore,
    event_uuid: str,
    event_name: str,
    now: Callable[[], str] = _utc_now_iso,
) -> int:
    """Replace the user-store ``global_items`` slice for one event with a
    fresh projection. Returns the row count written. Atomic — the DELETE +
    INSERT pair runs inside one ``UserStore.transaction``, so a mid-flight
    failure leaves the prior slice intact.

    Triggers (spec/81 Phase 2 handover): **event close** (the EventGateway
    calls this from its close path) + **startup reconcile**
    (:func:`reconcile_all`). On-demand callers (a UI refresh button later)
    use the same entry point."""
    rows = project_event(
        event_store=event_store, event_uuid=event_uuid,
        event_name=event_name, now=now,
    )
    with user_store.transaction() as conn:
        conn.execute("DELETE FROM global_items WHERE event_uuid = ?",
                     (event_uuid,))
        for row in rows:
            user_store.upsert(row)
    log.info("global_items: synced %d rows for event %s", len(rows), event_uuid)
    return len(rows)


def drop_event(*, user_store: UserStore, event_uuid: str) -> int:
    """Remove the user-store ``global_items`` slice for one event. The
    reconcile pass uses this when an event has been deleted from the library
    since the last sync; a UI delete-event flow will also call it. Returns
    the row count removed."""
    cur = user_store.conn.execute(
        "SELECT COUNT(*) AS n FROM global_items WHERE event_uuid = ?",
        (event_uuid,),
    ).fetchone()
    deleted = int(cur["n"]) if cur else 0
    with user_store.transaction() as conn:
        conn.execute("DELETE FROM global_items WHERE event_uuid = ?",
                     (event_uuid,))
    if deleted:
        log.info("global_items: dropped %d rows for event %s",
                 deleted, event_uuid)
    return deleted


# --------------------------------------------------------------------------- #
# Reconcile — startup pass over the events index
# --------------------------------------------------------------------------- #


def reconcile_all(
    *,
    user_store: UserStore,
    open_event_store: Callable[[str], Optional[EventStore]],
    known_events: Iterable[tuple],
    now: Callable[[], str] = _utc_now_iso,
) -> dict:
    """Reconcile the entire ``global_items`` projection against the library's
    current event set. Called at startup so an event closed mid-crash, deleted
    while Mira was off, or added by another process gets caught up.

    The caller supplies ``known_events`` as ``(event_uuid, event_name)`` tuples
    (from the events index) and ``open_event_store(uuid) -> EventStore | None``
    so this module stays decoupled from the events-index format (JSON today,
    spec/53 §2.3 ``event_index`` table tomorrow). Events whose store can't be
    opened are skipped + logged, not raised — the reconcile is opportunistic.

    Returns a summary dict ``{synced, dropped, skipped}`` for the caller to
    log."""
    seen: set = set()
    synced = 0
    skipped = 0
    for uuid, name in known_events:
        seen.add(uuid)
        store = open_event_store(uuid)
        if store is None:
            skipped += 1
            continue
        try:
            sync_event(event_store=store, user_store=user_store,
                       event_uuid=uuid, event_name=name, now=now)
            synced += 1
        except sqlite3.Error as exc:                       # noqa: BLE001
            log.warning("global_items: reconcile failed for %s: %s", uuid, exc)
            skipped += 1
        finally:
            store.close()
    # Drop projections for events no longer in the library.
    existing = {r["event_uuid"] for r in user_store.conn.execute(
        "SELECT DISTINCT event_uuid FROM global_items").fetchall()}
    stale = existing - seen
    dropped = 0
    for uuid in stale:
        drop_event(user_store=user_store, event_uuid=uuid)
        dropped += 1
    return {"synced": synced, "dropped": dropped, "skipped": skipped}


__all__ = [
    "drop_event",
    "project_event",
    "reconcile_all",
    "sync_event",
]
