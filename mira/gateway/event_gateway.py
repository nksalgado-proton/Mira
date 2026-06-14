"""``EventGateway`` — the per-event facade (spec/08 §4; spec/30 §7).

Wraps one open :class:`~mira.store.repo.EventStore` and is the **only** place that
opens an ``event.db``. The UI holds one of these per event-editing session and never sees
``sqlite3`` or ``EventStore``. **Relational-first** (spec/31): reads push their predicates
into SQL (``WHERE`` / ``JOIN`` / ``GROUP BY``) via :meth:`~EventStore.query_by` /
:meth:`~EventStore.query_raw` and raw aggregate SQL — never load-all-then-filter-in-Python.
Mutators wrap writes in a transaction, stamp ``updated_at``, and enforce the model's
decision semantics (``decided_at`` / ``derived_dirty`` / ``committed_at``).

Timestamps come from an injected ``now`` callable so tests are deterministic; ids for
created clips/snapshots come from an injected ``new_id`` callable for the same reason.

**The video model is the marker-partition model (spec/56, schema v4):** markers are
first-class rows (the user's cut points; start/end stay implicit), and consecutive
markers define segments that tile the source timeline. A segment is its **own**
``item`` (kind='video', provenance='clip', child of the source via
``parent_item_id``) whose identity is its POSITION in the marker order
(``video_segment.seg_index``) — never milliseconds — so :meth:`move_video_marker`
re-times a segment without touching its state or adjustments,
:meth:`add_video_marker` splits the containing segment with both halves inheriting
the parent's state + adjustments, and :meth:`delete_video_marker` merges two
segments with the LEFT half surviving (it occupies the surviving order position).
Segment Pick/Skip rides ``phase_state`` (phase='edit', default Skip, written
explicitly at birth); snapshots (:meth:`create_video_snapshot`) auto-Pick.
Everything stays virtual until Export — :meth:`materialize` fills the file
identity (the single virtual→real transition); nothing commits bytes during
deciding.

**Buckets are transient grouping artifacts.** A bucket is a browsing convenience the
scanner *recomputes*; item→bucket membership lives only in the derived ``bucket_member``
cache. The store owns each bucket's durable **soft-state** (reviewed / browsed /
current_index / nudge_dismissed / default_state), keyed by ``bucket_key`` so it survives a
re-scan (spec/30 §5).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from bisect import bisect_right
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from core import cut_budget, cut_names
from core.video_segments import segment_bounds as derive_segment_bounds
from mira.store import models as m
from mira.store.repo import EventStore

log = logging.getLogger(__name__)

_PHASES = ("pick", "pick", "edit", "share")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_uuid() -> str:
    return uuid.uuid4().hex


def _parse_iso_or_epoch(s: Optional[str]) -> datetime:
    """ISO-8601 → ``datetime``; ``None`` / unparseable → epoch (deterministic
    sentinel for sort keys when an item has no usable timestamp)."""
    if not s:
        return datetime.fromtimestamp(0)
    try:
        return datetime.fromisoformat(s.rstrip("Z"))
    except ValueError:
        return datetime.fromtimestamp(0)


class EventGateway:
    """The query/mutator facade over one open event."""

    def __init__(
        self,
        store: EventStore,
        *,
        event_root: Optional[Path] = None,
        now: Callable[[], str] = _utc_now_iso,
        new_id: Callable[[], str] = _new_uuid,
    ) -> None:
        self.store = store
        self.event_root = event_root
        self._now = now
        self._new_id = new_id

    # ----- lifecycle ----------------------------------------------------- #

    @classmethod
    def open(
        cls,
        db_path: Path,
        *,
        event_root: Optional[Path] = None,
        now: Callable[[], str] = _utc_now_iso,
        new_id: Callable[[], str] = _new_uuid,
    ) -> "EventGateway":
        return cls(EventStore.open(db_path), event_root=event_root, now=now, new_id=new_id)

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> "EventGateway":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # =================================================================== #
    # Queries
    # =================================================================== #

    # ----- event-level ---------------------------------------------------- #

    def event(self) -> m.Event:
        ev = self.store.get(m.Event, 1)        # the enforced singleton (id=1)
        if ev is None:
            raise RuntimeError("event.db has no event row")
        return ev

    def trip_days(self) -> List[m.TripDay]:
        return self.store.all(m.TripDay)

    def cameras(self) -> List[m.Camera]:
        return self.store.all(m.Camera)

    def calibration_pairs(self, camera_id: Optional[str] = None) -> List[m.CameraCalibrationPair]:
        if camera_id is None:
            return self.store.all(m.CameraCalibrationPair)
        return self.store.query_by(m.CameraCalibrationPair, camera_id=camera_id)

    # spec/52 retired: participants/participant_devices (people moved to user-level
    # catalog + photo_person), checklist (per-camera-TZ checklist retired with
    # past_photos_cameras), distribution (share-event log no longer kept).

    # ----- item spine ----------------------------------------------------- #

    def item(self, item_id: str) -> Optional[m.Item]:
        return self.store.get(m.Item, item_id)

    def items(
        self,
        *,
        phase: Optional[str] = None,
        state: Optional[str] = None,
        day: Optional[int] = None,
        kind: Optional[str] = None,
        camera_id: Optional[str] = None,
        provenance: Optional[str] = None,
        include_hidden: bool = False,
    ) -> List[m.Item]:
        """Filtered, capture-time-ordered items — the query behind every phase list,
        pushed entirely into SQL (spec/30 §6).

        ``phase``/``state`` ``JOIN`` ``phase_state`` (explicit rows only — an item with no
        row for that phase is *undecided* and excluded; effective-state-with-bucket-default
        composition is a Cull-surface concern). ``day``/``kind``/``camera_id``/``provenance``
        filter ``item`` columns.

        By default items on a **hidden** day are excluded (the read goes through the
        ``visible_item`` view; spec/14 §5C.1). Pass ``include_hidden=True`` for the few
        callers that must touch every item regardless of visibility (TZ recompute, the
        plan editor's day viewer, hard-delete/move)."""
        if state is not None and phase is None:
            raise ValueError("items(state=...) requires a phase")
        source = "item" if include_hidden else "visible_item"
        params: list = []
        join = ""
        if phase is not None:
            join = " JOIN phase_state ps ON ps.item_id = item.id AND ps.phase = ?"
            params.append(phase)
        wh: list = []
        if camera_id is not None:
            wh.append("item.camera_id = ?"); params.append(camera_id)
        if kind is not None:
            wh.append("item.kind = ?"); params.append(kind)
        if day is not None:
            wh.append("item.day_number = ?"); params.append(day)
        if provenance is not None:
            wh.append("item.provenance = ?"); params.append(provenance)
        if state is not None:
            wh.append("ps.state = ?"); params.append(state)
        sql = f"SELECT item.* FROM {source} AS item" + join
        if wh:
            sql += " WHERE " + " AND ".join(wh)
        sql += " ORDER BY item.capture_time_corrected, item.origin_relpath"
        return self.store.query_raw(m.Item, sql, tuple(params))

    def children(self, item_id: str) -> List[m.Item]:
        """Child items (clips/snapshots) of a source video (``parent_item_id``)."""
        return self.store.query_by(m.Item, parent_item_id=item_id)

    # spec/56 slice 2 retired parent_ids_with_kept_children (the spec/32
    # §2.4 yellow-video-rule prefetch): Pick creates no children any more,
    # so a video cell shows its own whole-video P/D state.

    def day_tree(self) -> List[Dict]:
        """Day → {date, description, total, photos, videos} for the *captured* timeline.

        One ``GROUP BY`` over ``visible_item`` (``provenance='captured'`` — derivatives/
        virtual clips are not capture-day entries; hidden days are excluded). Items with no
        ``day_number`` group under ``None`` (undated)."""
        rows = self.store.conn.execute(
            "SELECT day_number AS dn, COUNT(*) AS total, "
            "SUM(CASE WHEN kind='photo' THEN 1 ELSE 0 END) AS photos, "
            "SUM(CASE WHEN kind='video' THEN 1 ELSE 0 END) AS videos "
            "FROM visible_item WHERE provenance='captured' GROUP BY day_number"
        ).fetchall()
        days = {d.day_number: d for d in self.trip_days()}
        groups: Dict[Optional[int], Dict] = {}
        for r in rows:
            dn = r["dn"]
            day = days.get(dn)
            groups[dn] = {
                "day_number": dn, "total": r["total"],
                "photos": r["photos"] or 0, "videos": r["videos"] or 0,
                "date": day.date if day else None,
                "description": day.description if day else "",
            }
        return [groups[k] for k in sorted(groups, key=lambda x: (x is None, x))]

    def day_summaries(self) -> List[Dict]:
        """Per-day rows for the **Manage days** surface (spec/14 §5D): EVERY trip day —
        **including hidden ones** — with its captured photo/video counts and the ``hidden``
        flag, ordered by ``day_number``. Unlike :meth:`day_tree` (which feeds phase surfaces
        through ``visible_item`` and so drops hidden days), this lists hidden days too, so the
        dialog can offer Unhide. Counts come from the base ``item`` table for the same reason.
        Undated items (no ``day_number``) are not trip days and don't appear here."""
        counts = {
            r["dn"]: (r["photos"] or 0, r["videos"] or 0)
            for r in self.store.conn.execute(
                "SELECT day_number AS dn, "
                "SUM(CASE WHEN kind='photo' THEN 1 ELSE 0 END) AS photos, "
                "SUM(CASE WHEN kind='video' THEN 1 ELSE 0 END) AS videos "
                "FROM item WHERE provenance='captured' GROUP BY day_number"
            )
        }
        out: List[Dict] = []
        for td in self.trip_days():
            photos, videos = counts.get(td.day_number, (0, 0))
            out.append({
                "day_number": td.day_number, "date": td.date,
                "description": td.description, "photos": photos, "videos": videos,
                "hidden": bool(td.hidden),
            })
        return out

    # ----- phase state & progress ----------------------------------------- #

    def phase_state(self, item_id: str, phase: str) -> Optional[m.PhaseState]:
        return self.store.get(m.PhaseState, item_id, phase)

    def phase_states(self, phase: str) -> Dict[str, m.PhaseState]:
        """All explicit ``phase_state`` rows for ``phase``, keyed by ``item_id`` (one
        indexed query). A row exists **iff** the user made an explicit decision, so
        ``item_id not in result`` is the first-class *undecided / untouched* state."""
        return {ps.item_id: ps for ps in self.store.query_by(m.PhaseState, phase=phase)}

    def phase_progress(self, phase: str) -> Dict:
        """``{counts, total, reviewed_buckets, dirty}`` — the dashboard/funnel summary.
        All SQL aggregates over ``phase_state`` + ``bucket`` (never a stored cache).
        ``phase_state`` is joined to ``visible_item`` so a hidden day's marks drop out of
        the counts (the metric a hidden day must not skew; spec/14 §5C.1). ``reviewed_buckets``
        is a global bucket count (buckets aren't day-keyed) — left unjoined for now."""
        conn = self.store.conn
        counts = {
            r["state"]: r["n"] for r in conn.execute(
                "SELECT ps.state AS state, COUNT(*) AS n FROM phase_state ps "
                "JOIN visible_item v ON v.id = ps.item_id "
                "WHERE ps.phase = ? GROUP BY ps.state", (phase,)
            )
        }
        dirty = conn.execute(
            "SELECT COUNT(*) FROM phase_state ps JOIN visible_item v ON v.id = ps.item_id "
            "WHERE ps.phase = ? AND ps.derived_dirty = 1", (phase,)
        ).fetchone()[0]
        reviewed = conn.execute(
            "SELECT COUNT(*) FROM bucket WHERE phase = ? AND reviewed = 1", (phase,)
        ).fetchone()[0]
        return {
            "counts": counts,
            "total": sum(counts.values()),
            "reviewed_buckets": reviewed,
            "dirty": dirty,
        }

    def phase_picked_count(self, phase: str) -> int:
        """Number of items kept by the user at ``phase``, visibility-filtered.

        Different phases store "picked" differently — the same special cases
        :meth:`phase_day_progress` applies per-day, applied here in aggregate:

        * ``cull`` / ``select`` → ``phase_state.state = 'picked'``
        * ``process``           → ``adjustment.edit_exported = 1`` (Process has no
                                  ``phase_state`` writes; Q3 locked 2026-06-08)
        * ``curate``            → ``share_tag.is_discarded = 0`` (the tag row IS the
                                  decision; spec/43 G6)

        Every query joins ``visible_item`` so hidden-day items drop out (spec/14
        §5C.1). Used by :func:`mira.overview_stats.phase_funnel_breakdown` —
        without this routing the Process/Curate funnel bars (and the per-event
        dashboard's PickedRatioDonut, which reads the funnel) would silently render
        as zero.
        """
        conn = self.store.conn
        if phase == "edit":
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM adjustment a "
                "JOIN visible_item v ON v.id = a.item_id "
                "WHERE a.edit_exported = 1"
            ).fetchone()
            return int(row["n"] or 0)
        # spec/52: 'share' branch retired with share_tag. The Cuts surfaces
        # (spec/61) read membership from cut_member when they land.
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM phase_state ps "
            "JOIN visible_item v ON v.id = ps.item_id "
            "WHERE ps.phase = ? AND ps.state = 'picked'",
            (phase,),
        ).fetchone()
        return int(row["n"] or 0)

    def phase_day_progress(self) -> Dict[str, Dict[Optional[int], Dict[str, int]]]:
        """Per-phase, per-day decided/total/committed/kept counts for the events-list
        card's phase × day heatmap — two ``GROUP BY``s (no Python loops over every row).
        ``{phase: {day_number: {'total','decided','committed','picked'}}}``; ``total`` is the
        captured items on that day, ``decided`` the ones with an explicit mark. Both
        aggregates read ``visible_item`` so a hidden day contributes nothing (spec/14 §5C.1).

        **Process special case (Q3 locked 2026-06-08):** Process has no phase_state
        writes — the per-item ``Adjustment.edit_exported`` flag is the signal.
        After the phase_state pass we OVERRIDE the ``process`` entry with a count
        from ``adjustment``, so the dashboard Process tile shows a real donut
        after exports (otherwise it would stay at 0%).
        """
        conn = self.store.conn
        totals = {
            r["dn"]: r["n"] for r in conn.execute(
                "SELECT day_number AS dn, COUNT(*) AS n FROM visible_item "
                "WHERE provenance='captured' GROUP BY day_number"
            )
        }
        out: Dict[str, Dict[Optional[int], Dict[str, int]]] = {}
        for r in conn.execute(
            "SELECT ps.phase AS phase, item.day_number AS dn, COUNT(*) AS decided, "
            "SUM(CASE WHEN ps.committed_at IS NOT NULL THEN 1 ELSE 0 END) AS committed, "
            "SUM(CASE WHEN ps.state='picked' THEN 1 ELSE 0 END) AS picked "
            "FROM phase_state ps JOIN visible_item item ON item.id = ps.item_id "
            "GROUP BY ps.phase, item.day_number"
        ):
            cell = out.setdefault(r["phase"], {}).setdefault(
                r["dn"], {"total": 0, "decided": 0, "committed": 0, "picked": 0})
            cell["decided"] = r["decided"]
            cell["committed"] = r["committed"] or 0
            cell["picked"] = r["picked"] or 0
        # Process override — count edit_exported per day from adjustment.
        process_map: Dict[Optional[int], Dict[str, int]] = {}
        for r in conn.execute(
            "SELECT item.day_number AS dn, COUNT(*) AS exp "
            "FROM adjustment a JOIN visible_item item ON item.id = a.item_id "
            "WHERE a.edit_exported = 1 "
            "GROUP BY item.day_number"
        ):
            process_map[r["dn"]] = {
                "total": totals.get(r["dn"], 0),
                "decided": r["exp"],
                "committed": r["exp"],
                "picked": r["exp"],
            }
        # Fill in zero rows for days with no exports so the tile shows
        # "0/N done" instead of being absent.
        for dn, t in totals.items():
            process_map.setdefault(
                dn, {"total": t, "decided": 0, "committed": 0, "picked": 0})
        out["edit"] = process_map

        # spec/52 + spec/61: the Share-phase share_tag override is retired.
        # Cuts replace the Curate concept and per-Cut membership lives in
        # cut_member (file-based, → lineage); the per-day Share progress
        # widget will be redesigned with the Cuts surfaces. Until then
        # phase_day_progress emits no 'share' bucket — callers that read
        # pdp['share'] should expect KeyError now.

        for phase, phase_map in out.items():
            if phase == "edit":
                continue
            for dn, cell in phase_map.items():
                cell["total"] = totals.get(dn, cell["decided"])
        return out

    # ----- buckets -------------------------------------------------------- #

    def buckets(self, phase: Optional[str] = None) -> List[m.Bucket]:
        if phase is None:
            return self.store.all(m.Bucket)
        return self.store.query_by(m.Bucket, phase=phase)

    def bucket(self, bucket_key: str, phase: str) -> Optional[m.Bucket]:
        return self.store.get(m.Bucket, bucket_key, phase)

    def bucket_status(self, bucket_key: str, phase: str) -> Dict[str, int]:
        """One bucket's K/D histogram — ``bucket_member ⋈ phase_state GROUP BY state``,
        over ``ix_phase_state_item`` (the honest-status projection, per bucket not per
        whole phase). Items with no mark are absent (counted by the caller as undecided)."""
        rows = self.store.conn.execute(
            "SELECT ps.state AS state, COUNT(*) AS n "
            "FROM bucket_member bm JOIN phase_state ps "
            "  ON ps.item_id = bm.item_id AND ps.phase = bm.phase "
            "WHERE bm.bucket_key = ? AND bm.phase = ? GROUP BY ps.state",
            (bucket_key, phase),
        ).fetchall()
        return {r["state"]: r["n"] for r in rows}

    # ----- bucket cache (spec/30 §3.18; day_number-keyed, NULL = undated) -- #

    def clustering_fingerprint(self, phase: str, day_number: Optional[int]) -> Optional[str]:
        """The stored per-(phase, day) clustering fingerprint, or ``None``. ``day_number``
        ``None`` is the undated day (``IS NULL``, not ``= NULL``)."""
        conn = self.store.conn
        if day_number is None:
            row = conn.execute(
                "SELECT fingerprint FROM clustering WHERE phase = ? AND day_number IS NULL",
                (phase,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT fingerprint FROM clustering WHERE phase = ? AND day_number = ?",
                (phase, day_number),
            ).fetchone()
        return row["fingerprint"] if row else None

    def cached_buckets(self, phase: str, day_number: Optional[int]) -> List[m.BucketCache]:
        """Cached bucket metadata for one day, in display order. ``day_number`` ``None``
        is the undated day (``IS NULL``)."""
        if day_number is None:
            return self.store.query_raw(
                m.BucketCache,
                "SELECT * FROM bucket_cache WHERE phase = ? AND day_number IS NULL ORDER BY ordinal",
                (phase,),
            )
        return self.store.query_raw(
            m.BucketCache,
            "SELECT * FROM bucket_cache WHERE phase = ? AND day_number = ? ORDER BY ordinal",
            (phase, day_number),
        )

    def bucket_members(self, bucket_key: str, phase: str) -> List[m.BucketMember]:
        """A cached bucket's members, in scanner order."""
        return self.store.query_by(m.BucketMember, bucket_key=bucket_key, phase=phase)

    # ----- Day Grid cell cursor (spec/32 §8.5) ---------------------------- #

    def get_day_resume_cell(self, phase: str, day_number: Optional[int]) -> int:
        """Return the persisted Day Grid cell cursor for ``(phase, day)``, or 0.

        The Day Grid replaces the per-bucket ``current_index`` resume with one
        cell-index per (phase, day). ``day_number`` ``None`` is the undated day
        (``IS NULL``, not ``= NULL``)."""
        if day_number is None:
            row = self.store.conn.execute(
                "SELECT cell_index FROM day_resume "
                "WHERE phase = ? AND day_number IS NULL", (phase,),
            ).fetchone()
        else:
            row = self.store.conn.execute(
                "SELECT cell_index FROM day_resume "
                "WHERE phase = ? AND day_number = ?", (phase, day_number),
            ).fetchone()
        return int(row["cell_index"]) if row else 0

    def set_day_resume_cell(
        self, phase: str, day_number: Optional[int], cell_index: int,
    ) -> None:
        """Persist the Day Grid cell cursor for ``(phase, day)`` (spec/32 §8.5).

        Upsert (``ON CONFLICT DO UPDATE`` — never delete-and-reinsert, lesson
        ``feedback_never_insert_or_replace_with_fks``). ``cell_index`` clamps
        negative input to 0 defensively."""
        idx = max(0, int(cell_index))
        with self.store.transaction() as conn:
            if day_number is None:
                # SQLite PRIMARY KEY treats two NULL day_number rows as distinct,
                # so we DELETE-then-INSERT for the undated-day case (still
                # within the transaction; no FK action fires because day_resume
                # has no child tables).
                conn.execute(
                    "DELETE FROM day_resume WHERE phase = ? AND day_number IS NULL",
                    (phase,),
                )
                conn.execute(
                    "INSERT INTO day_resume (phase, day_number, cell_index, updated_at) "
                    "VALUES (?, NULL, ?, ?)",
                    (phase, idx, self._now()),
                )
            else:
                conn.execute(
                    "INSERT INTO day_resume (phase, day_number, cell_index, updated_at) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(phase, day_number) DO UPDATE SET "
                    "  cell_index = excluded.cell_index, "
                    "  updated_at = excluded.updated_at",
                    (phase, day_number, idx, self._now()),
                )
            self._touch()

    # ----- Day Grid bulk operations (spec/32 §2.8) ------------------------- #

    def reset_compare_in_day(
        self, phase: str, day_number: Optional[int], to_state: str,
    ) -> int:
        """Bulk-reset every Compare item in ``day_number`` for ``phase`` to
        ``to_state`` (spec/32 §2.8 — the "Reset All Compare" button). Returns
        the count of items affected.

        Operates on visible items only (hidden days are excluded — they
        cannot reach the Day Grid). Each row's ``decided_at`` updates to now;
        ``committed_at`` is preserved per row."""
        if to_state not in ("picked", "skipped"):
            raise ValueError(f"reset_compare_in_day: bad target state {to_state!r}")
        if day_number is None:
            day_clause = "v.day_number IS NULL"
            params: tuple = (phase, "candidate")
        else:
            day_clause = "v.day_number = ?"
            params = (phase, "candidate", day_number)
        rows = self.store.conn.execute(
            "SELECT ps.item_id, ps.committed_at FROM phase_state ps "
            "JOIN visible_item v ON v.id = ps.item_id "
            f"WHERE ps.phase = ? AND ps.state = ? AND {day_clause}",
            params,
        ).fetchall()
        if not rows:
            return 0
        now = self._now()
        with self.store.transaction():
            for r in rows:
                self.store.upsert(m.PhaseState(
                    item_id=r["item_id"], phase=phase, state=to_state,
                    derived_dirty=False, decided_at=now,
                    committed_at=r["committed_at"],
                ))
            self._touch()
        return len(rows)

    def save_day_cache(
        self,
        phase: str,
        day_number: Optional[int],
        fingerprint: str,
        buckets: Iterable[Dict],
    ) -> None:
        """Replace one day's cached clustering (spec/30 §3.18). ``buckets`` is an ordered
        list of dicts ``{bucket_key, kind, title, detection_source, camera, item_ids:[...]}``.
        The durable ``bucket`` soft-state table is **untouched** — only the derived cache
        (``bucket_cache`` / ``bucket_member`` / ``clustering``) is rewritten, so a
        membership-preserving recompute keeps the user's reviewed/browsed/resume state.
        Idempotent per day. The composite FK cascades the prior members on cache delete."""
        conn = self.store.conn
        with self.store.transaction():
            if day_number is None:
                conn.execute(
                    "DELETE FROM bucket_cache WHERE phase = ? AND day_number IS NULL", (phase,))
                conn.execute(
                    "DELETE FROM clustering WHERE phase = ? AND day_number IS NULL", (phase,))
            else:
                conn.execute(
                    "DELETE FROM bucket_cache WHERE phase = ? AND day_number = ?", (phase, day_number))
                conn.execute(
                    "DELETE FROM clustering WHERE phase = ? AND day_number = ?", (phase, day_number))
            for ordinal, b in enumerate(buckets):
                self.store.upsert(
                    m.BucketCache(
                        bucket_key=b["bucket_key"], phase=phase, day_number=day_number,
                        kind=b["kind"], title=b.get("title", ""),
                        detection_source=b.get("detection_source", ""),
                        camera=b.get("camera", ""), ordinal=ordinal,
                    )
                )
                for m_ord, item_id in enumerate(b["item_ids"]):
                    self.store.upsert(
                        m.BucketMember(
                            bucket_key=b["bucket_key"], phase=phase,
                            item_id=item_id, ordinal=m_ord,
                        )
                    )
            self.store.upsert(
                m.Clustering(
                    phase=phase, day_number=day_number,
                    fingerprint=fingerprint, computed_at=self._now(),
                )
            )
            self._touch()

    # ----- video workshop: markers / segments / snapshots (spec/56) ------- #
    # spec/56 retired the clip_span readers (clip_span / clip_spans /
    # video_children / kept_video_children / next_lineage_id): freeform spans
    # are gone, segments derive from marker order, and stable per-source ids
    # are the segment items' own ids. The generic children() above survives.

    def video_markers(self, video_item_id: str) -> List[m.VideoMarker]:
        """The stored user cut points of a source video, in ``at_ms`` order.
        Zero rows = the video is one segment (start/end markers are implicit)."""
        return self.store.query_by(m.VideoMarker, video_item_id=video_item_id)

    def video_segments(self, video_item_id: str) -> List[m.VideoSegment]:
        """The segment satellite rows of a source video, in ``seg_index`` order.
        Empty until the first workshop touch (:meth:`ensure_video_segments` —
        segments materialise lazily); once present, the gateway maintains
        ``count == len(video_markers) + 1`` with dense indexes."""
        return self.store.query_by(m.VideoSegment, video_item_id=video_item_id)

    def video_snapshots(self, video_item_id: str) -> List[m.VideoSnapshot]:
        """The snapshot satellite rows of a source video, in ``at_ms`` order."""
        return self.store.query_by(m.VideoSnapshot, video_item_id=video_item_id)

    def segment_items(self, video_item_id: str) -> List[m.Item]:
        """The segment child ITEMS of a source video, in ``seg_index`` order —
        what the workshop timeline binds state/adjustment edits to."""
        return self.store.query_raw(
            m.Item,
            "SELECT item.* FROM item "
            "JOIN video_segment vs ON vs.item_id = item.id "
            "WHERE vs.video_item_id = ? "
            "ORDER BY vs.seg_index",
            (video_item_id,),
        )

    def segment_bounds(self, video_item_id: str) -> List[tuple]:
        """``[(in_ms, out_ms)]`` per segment in ``seg_index`` order — geometry
        DERIVED from marker order (``core.video_segments``), never stored.
        Requires a probed ``item.duration_ms`` (the right edge of the last
        segment)."""
        video = self._require_source_video(video_item_id)
        if not video.duration_ms:
            raise ValueError(
                f"segment_bounds({video_item_id}): video duration_ms not probed")
        return derive_segment_bounds(
            [mk.at_ms for mk in self.video_markers(video_item_id)], video.duration_ms)

    def video_adjustment(self, item_id: str) -> Optional[m.VideoAdjustment]:
        return self.store.get(m.VideoAdjustment, item_id)

    # ----- process -------------------------------------------------------- #

    def adjustment(self, item_id: str) -> Optional[m.Adjustment]:
        return self.store.get(m.Adjustment, item_id)

    def adjustments(self) -> List[m.Adjustment]:
        """Every row in ``adjustment`` — the bulk read Curate discovery needs to
        find Process-exported items (``edit_exported=True`` is the SoT)."""
        return self.store.all(m.Adjustment)

    def exported_item_ids(self) -> set:
        """Item ids with at least one **edit-phase lineage row** — the
        Exported-watermark driver (spec/59 §8): "an exported or
        externally-associated version of this photo exists." All four
        writers record it (as-you-go export, batch export, the return
        scan's third-party associations, the from-Edited backfill).

        Deliberately NOT ``Adjustment.edit_exported`` — that flag is
        freshness (reset on every adjustment change) and keeps its chip.
        ``source_kind='bracket'`` rows (stack exports) are out of scope:
        the watermark is per-photo (spec/59 §8)."""
        rows = self.store.conn.execute(
            "SELECT DISTINCT source_item_id FROM lineage "
            "WHERE phase = 'edit' AND source_item_id IS NOT NULL"
        ).fetchall()
        return {r["source_item_id"] for r in rows}

    # ----- share / cuts queries (spec/61) ---------------------------------- #
    # Membership is FILE-based: cut_member rows reference lineage (exported
    # finals), never items. The built-in #exported is a live query, not data.

    #: SELECT scaffold shared by every cuts read: a lineage row resolved to
    #: its source — `si` for item-sourced exports (through visible_item, so a
    #: hidden day's files drop out of Share like everywhere else), `oi` for
    #: the merged output item of bracket-sourced exports.
    _CUT_SOURCE_JOIN = (
        "LEFT JOIN visible_item si ON si.id = l.source_item_id "
        "LEFT JOIN stack_bracket sb ON sb.bracket_id = l.source_bracket_id "
        "LEFT JOIN item oi ON oi.id = sb.output_item_id "
    )
    #: Chronological show order: source capture time, exported_at as the
    #: tie-break/fallback, relpath as the deterministic last word.
    _CUT_SHOW_ORDER = (
        "ORDER BY COALESCE(si.capture_time_corrected, oi.capture_time_corrected, "
        "l.exported_at), l.export_relpath"
    )

    def exported_files(self) -> List[m.Lineage]:
        """#exported — the built-in live-query Cut (spec/61 §1.1): every
        edit-phase final in chronological show order. Never stored, never
        stale; computed from lineage on demand. Item-sourced rows read
        through ``visible_item`` (hidden day ⇒ its files leave the universe);
        bracket-sourced rows pass (their day rides the merged output item)."""
        sql = (
            "SELECT l.* FROM lineage l "
            + self._CUT_SOURCE_JOIN +
            "WHERE l.phase = 'edit' "
            "AND (l.source_kind = 'bracket' OR si.id IS NOT NULL) "
            + self._CUT_SHOW_ORDER
        )
        return self.store.query_raw(m.Lineage, sql)

    def cuts(self) -> List[m.Cut]:
        """All user Cut definitions, oldest first (the list page's order).
        The built-in #exported is NOT here — it is :meth:`exported_files`."""
        return self.store.query_raw(
            m.Cut, "SELECT * FROM cut ORDER BY created_at, id")

    def cut(self, cut_id: str) -> Optional[m.Cut]:
        return self.store.get(m.Cut, cut_id)

    def cut_by_tag(self, tag: str) -> Optional[m.Cut]:
        rows = self.store.query_by(m.Cut, tag=tag)
        return rows[0] if rows else None

    @staticmethod
    def cut_pool_expr(cut: m.Cut) -> List[Tuple[str, str]]:
        """The Cut's recipe expression as ``[(op, tag), ...]`` (op ∈ '+'/'-')."""
        return [(op, tag) for op, tag in json.loads(cut.pool_expr_json)]

    @staticmethod
    def cut_style_filter(cut: m.Cut) -> List[str]:
        """The Cut's style filter; empty list = All styles (spec/61 §10)."""
        return list(json.loads(cut.style_filter_json))

    @staticmethod
    def cut_card_style(cut: m.Cut) -> str:
        """The separator/opener colour style (Nelson 2026-06-12):
        'black' | 'single' | 'multi'. Lives in ``extras_json`` (the
        sanctioned escape hatch) — never queried, only rendered."""
        try:
            style = json.loads(cut.extras_json).get("card_style", "black")
        except (ValueError, TypeError):
            return "black"
        return style if style in ("black", "single", "multi") else "black"

    def _tag_file_set(self, tag: str) -> set:
        """The membership set (export relpaths) one pool term names. The
        built-in 'exported' resolves live; a user tag resolves through its
        cut row; an unknown/deleted tag contributes nothing (recipes are a
        record of intent — graceful shrink, spec/51 §6 H carried forward)."""
        if tag == cut_names.EXPORTED_TAG:
            return {ln.export_relpath for ln in self.exported_files()}
        cut = self.cut_by_tag(tag)
        if cut is None:
            return set()
        return {cm.export_relpath
                for cm in self.store.query_by(m.CutMember, cut_id=cut.id)}

    def resolve_pool(
        self,
        pool_expr: Sequence[Tuple[str, str]],
        *,
        style_filter: Sequence[str] = (),
        type_filter: str = "both",
    ) -> List[m.Lineage]:
        """Evaluate a Cut pool (spec/61 §2 step 2-3): left-to-right set
        algebra over membership sets (``+`` union, ``-`` difference), then
        the dialog filters — styles (empty = All; an active filter excludes
        unclassified sources) and media type ('both'/'photo'/'video').
        Returns lineage rows in chronological show order."""
        members: set = set()
        for op, tag in pool_expr:
            tag_set = self._tag_file_set(tag)
            if op == "+":
                members |= tag_set
            elif op == "-":
                members -= tag_set
            else:
                raise ValueError(f"unknown pool operator: {op!r}")
        if not members:
            return []
        return self._lineage_show_rows(
            members, style_filter=style_filter, type_filter=type_filter)

    def _lineage_show_rows(
        self,
        relpaths: Iterable[str],
        *,
        style_filter: Sequence[str] = (),
        type_filter: str = "both",
    ) -> List[m.Lineage]:
        """Lineage rows for a membership set, filtered + show-ordered."""
        relpaths = list(relpaths)
        if not relpaths:
            return []
        qs = ",".join("?" * len(relpaths))
        sql = (
            "SELECT l.* FROM lineage l "
            + self._CUT_SOURCE_JOIN +
            f"WHERE l.export_relpath IN ({qs}) "
        )
        params: list = list(relpaths)
        if type_filter in ("photo", "video"):
            sql += "AND COALESCE(si.kind, oi.kind, 'photo') = ? "
            params.append(type_filter)
        if style_filter:
            qs2 = ",".join("?" * len(style_filter))
            sql += f"AND COALESCE(si.classification, oi.classification) IN ({qs2}) "
            params.extend(style_filter)
        sql += self._CUT_SHOW_ORDER
        return self.store.query_raw(m.Lineage, sql, tuple(params))

    def cut_member_files(self, cut_id: str) -> List[m.Lineage]:
        """A Cut's committed membership in chronological show order — the
        flat grid / Play / Export read (spec/61 §5)."""
        members = {cm.export_relpath
                   for cm in self.store.query_by(m.CutMember, cut_id=cut_id)}
        return self._lineage_show_rows(members)

    def cut_style_options(self) -> List[str]:
        """Distinct classifications across the #exported universe — the New
        Cut dialog's style-chip vocabulary (spec/61 §2 step 3). Unclassified
        sources contribute nothing; alphabetical."""
        rows = self.store.conn.execute(
            "SELECT DISTINCT COALESCE(si.classification, oi.classification) AS c "
            "FROM lineage l "
            + self._CUT_SOURCE_JOIN +
            "WHERE l.phase = 'edit' "
            "AND (l.source_kind = 'bracket' OR si.id IS NOT NULL) "
            "ORDER BY c"
        ).fetchall()
        return [r["c"] for r in rows if r["c"]]

    def pool_show_totals(
        self,
        pool_expr: Sequence[Tuple[str, str]],
        *,
        style_filter: Sequence[str] = (),
        type_filter: str = "both",
    ) -> cut_budget.ShowTotals:
        """Budget composition of a DRAFT pool — the dialog's live counts +
        budget hint read this before any cut row exists. Same semantics as
        :meth:`cut_show_totals` (separator_count = member days)."""
        rows = self.resolve_pool(
            pool_expr, style_filter=style_filter, type_filter=type_filter)
        if not rows:
            return cut_budget.ShowTotals()
        relpaths = [ln.export_relpath for ln in rows]
        qs = ",".join("?" * len(relpaths))
        row = self.store.conn.execute(
            "SELECT "
            "SUM(CASE WHEN COALESCE(si.kind, oi.kind, 'photo') = 'video' "
            "    THEN 0 ELSE 1 END) AS photos, "
            "SUM(CASE WHEN COALESCE(si.kind, oi.kind, 'photo') = 'video' "
            "    THEN 1 ELSE 0 END) AS videos, "
            "SUM(CASE WHEN COALESCE(si.kind, oi.kind, 'photo') = 'video' "
            "    THEN COALESCE(si.duration_ms, oi.duration_ms, 0) ELSE 0 END) AS video_ms, "
            "COUNT(DISTINCT COALESCE(si.day_number, oi.day_number)) AS days "
            "FROM lineage l "
            + self._CUT_SOURCE_JOIN +
            f"WHERE l.export_relpath IN ({qs})",
            tuple(relpaths),
        ).fetchone()
        return cut_budget.ShowTotals(
            photo_count=int(row["photos"] or 0),
            video_count=int(row["videos"] or 0),
            separator_count=int(row["days"] or 0),
            video_ms_total=int(row["video_ms"] or 0),
        )

    def cut_show_totals(self, cut_id: str) -> cut_budget.ShowTotals:
        """Budget composition of a Cut's membership (spec/61 §2 step 5):
        photo/video counts, summed TRUE clip duration (un-probed clip
        duration reads 0 — honest minimum), and ``separator_count`` filled
        with the member days (one separator per day, spec/61 §4) — callers
        zero it when the separators setting is off. Undated sources don't
        count a day."""
        row = self.store.conn.execute(
            "SELECT "
            "SUM(CASE WHEN COALESCE(si.kind, oi.kind, 'photo') = 'video' "
            "    THEN 0 ELSE 1 END) AS photos, "
            "SUM(CASE WHEN COALESCE(si.kind, oi.kind, 'photo') = 'video' "
            "    THEN 1 ELSE 0 END) AS videos, "
            "SUM(CASE WHEN COALESCE(si.kind, oi.kind, 'photo') = 'video' "
            "    THEN COALESCE(si.duration_ms, oi.duration_ms, 0) ELSE 0 END) AS video_ms, "
            "COUNT(DISTINCT COALESCE(si.day_number, oi.day_number)) AS days "
            "FROM cut_member cm "
            "JOIN lineage l ON l.export_relpath = cm.export_relpath "
            + self._CUT_SOURCE_JOIN +
            "WHERE cm.cut_id = ?",
            (cut_id,),
        ).fetchone()
        return cut_budget.ShowTotals(
            photo_count=int(row["photos"] or 0),
            video_count=int(row["videos"] or 0),
            separator_count=int(row["days"] or 0),
            video_ms_total=int(row["video_ms"] or 0),
        )

    def photo_persons_for_item(self, item_id: str) -> List[m.PhotoPerson]:
        return self.store.query_by(m.PhotoPerson, item_id=item_id)

    def items_with_person(self, person_id: str) -> List[str]:
        return [pp.item_id for pp in self.store.query_by(m.PhotoPerson, person_id=person_id)]


    def stacks(self) -> List[m.StackBracket]:
        return self.store.all(m.StackBracket)

    def stack_members(self, bracket_id: str) -> List[m.StackMember]:
        return self.store.query_by(m.StackMember, bracket_id=bracket_id)

    def bracket_memberships(self, phase: str = "pick") -> Dict[str, tuple]:
        """``item_id → (bucket_key, kind)`` for every member of a CACHED
        focus/exposure bracket cluster (spec/57 §2.1 — the brackets the
        user actually saw in the day grid; ``item.bracket_group_id`` is
        the future ingest-detector override, still unpopulated). One
        indexed JOIN over the derived cache; days never computed at
        ``phase`` simply contribute nothing."""
        rows = self.store.conn.execute(
            "SELECT bm.item_id AS item_id, bm.bucket_key AS bucket_key, "
            "       bc.kind AS kind "
            "FROM bucket_member bm "
            "JOIN bucket_cache bc ON bc.bucket_key = bm.bucket_key "
            "                    AND bc.phase = bm.phase "
            "WHERE bm.phase = ? "
            "  AND bc.kind IN ('focus_bracket','exposure_bracket')",
            (phase,),
        ).fetchall()
        return {r["item_id"]: (r["bucket_key"], r["kind"]) for r in rows}

    def lineage(self) -> List[m.Lineage]:
        return self.store.all(m.Lineage)

    # =================================================================== #
    # Mutators
    # =================================================================== #

    def _touch(self) -> None:
        """Stamp the event's ``updated_at``. Caller is inside a transaction.
        ``id = 1`` is the enforced singleton, so a no-WHERE update would also be safe;
        the WHERE is explicit for clarity."""
        self.store.conn.execute("UPDATE event SET updated_at = ? WHERE id = 1", (self._now(),))

    # ----- phase decisions ------------------------------------------------ #

    def set_phase_state(self, item_id: str, phase: str, state: str) -> None:
        """The K/D/Candidate mark for any item (photo, video, clip, snapshot); stamps
        ``decided_at`` and clears ``derived_dirty``."""
        existing = self.store.get(m.PhaseState, item_id, phase)
        committed = existing.committed_at if existing else None
        row = m.PhaseState(
            item_id=item_id, phase=phase, state=state,
            derived_dirty=False, decided_at=self._now(), committed_at=committed,
        )
        with self.store.transaction():
            self.store.upsert(row)
            self._touch()

    def set_items_phase_state(
        self, item_ids: List[str], phase: str, state: str,
    ) -> int:
        """Bulk-set ``state`` for an explicit list of items in one
        transaction. The day-scope / cluster-scope / event-scope batch
        ops in the Picker UI use this; the per-item :meth:`set_phase_state`
        opens its own transaction, so the legacy "outer
        ``store.transaction()`` wrapping a loop of ``set_phase_state``"
        pattern nests BEGIN and raises ``cannot start a transaction
        within a transaction``. Returns the count written.
        ``committed_at`` is preserved per row.

        Mirrors :meth:`set_camera_phase_state` but with a caller-built
        item list — the UI gathers cells (and cluster members) and
        passes the ids explicitly."""
        if not item_ids:
            return 0
        now = self._now()
        with self.store.transaction():
            for item_id in item_ids:
                existing = self.store.get(m.PhaseState, item_id, phase)
                self.store.upsert(m.PhaseState(
                    item_id=item_id, phase=phase, state=state,
                    derived_dirty=False, decided_at=now,
                    committed_at=existing.committed_at if existing else None,
                ))
            self._touch()
        return len(item_ids)

    def set_camera_phase_state(self, camera_id: str, phase: str, state: str) -> int:
        """Bulk-set every captured item of ``camera_id`` to ``state`` for ``phase`` — the
        per-camera Keep-all / Discard-all on the Cull landing (Nelson 2026-06-01). One
        transaction; returns the count. ``committed_at`` is preserved per row."""
        items = self.items(camera_id=camera_id, provenance="captured")
        now = self._now()
        with self.store.transaction():
            for it in items:
                existing = self.store.get(m.PhaseState, it.id, phase)
                self.store.upsert(m.PhaseState(
                    item_id=it.id, phase=phase, state=state, derived_dirty=False,
                    decided_at=now,
                    committed_at=existing.committed_at if existing else None,
                ))
            self._touch()
        return len(items)

    def reset_camera_phase_state(self, camera_id: str, phase: str) -> int:
        """Clear every cull decision for ``camera_id`` (back to untouched) — Reset-all on the
        Cull landing. Deletes the ``phase_state`` rows; returns the count removed."""
        ids = [it.id for it in self.items(camera_id=camera_id, provenance="captured")]
        if not ids:
            return 0
        with self.store.transaction() as conn:
            conn.executemany(
                "DELETE FROM phase_state WHERE item_id = ? AND phase = ?",
                [(i, phase) for i in ids])
            self._touch()
        return len(ids)

    def commit_phase(self, phase: str) -> None:
        """Stamp ``committed_at`` on every decided row of a phase (phase-exit)."""
        now = self._now()
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE phase_state SET committed_at = ? WHERE phase = ?", (now, phase))
            self._touch()

    def mark_derived_dirty(self, phase: str, item_ids: Iterable[str]) -> None:
        """Flag downstream marks stale after an upstream change (the re-entry fix)."""
        ids = list(item_ids)
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self.store.transaction() as conn:
            conn.execute(
                f"UPDATE phase_state SET derived_dirty = 1 "
                f"WHERE phase = ? AND item_id IN ({placeholders})",
                [phase, *ids],
            )
            self._touch()

    # ----- buckets -------------------------------------------------------- #

    def _bucket_for_write(self, bucket_key: str, phase: str) -> m.Bucket:
        return self.store.get(m.Bucket, bucket_key, phase) or m.Bucket(
            bucket_key=bucket_key, phase=phase
        )

    def _save_bucket(self, bucket: m.Bucket) -> None:
        with self.store.transaction():
            self.store.upsert(bucket)
            self._touch()

    def set_bucket_reviewed(self, bucket_key: str, phase: str, value: bool = True) -> None:
        b = self._bucket_for_write(bucket_key, phase)
        b.reviewed = value
        self._save_bucket(b)

    def set_bucket_browsed(self, bucket_key: str, phase: str, value: bool = True) -> None:
        b = self._bucket_for_write(bucket_key, phase)
        b.browsed = value
        self._save_bucket(b)

    def set_bucket_current_index(self, bucket_key: str, phase: str, index: int) -> None:
        b = self._bucket_for_write(bucket_key, phase)
        b.current_index = index
        self._save_bucket(b)

    def dismiss_nudge(self, bucket_key: str, phase: str) -> None:
        b = self._bucket_for_write(bucket_key, phase)
        b.nudge_dismissed = True
        self._save_bucket(b)

    def set_bucket_default_state(self, bucket_key: str, phase: str, default_state: str) -> None:
        b = self._bucket_for_write(bucket_key, phase)
        b.default_state = default_state
        self._save_bucket(b)

    # ----- item visited (Day Grid tick, spec/32 §2.10 §8.6) -------------- #

    def set_item_visited(
        self, item_id: str, phase: str, value: bool = True,
    ) -> None:
        """Set/clear the Day Grid visited tick for one (item, phase) pair (spec/32 §2.10).

        Centre-click on a photo/video/clip cell drills into its surface — the host
        calls this to remember "the user looked at this one".  Sibling of
        :meth:`set_bucket_browsed` for cluster cells.

        Idempotent — repeated calls with the same value are no-ops at the user-visible
        level.  Upsert via ``ON CONFLICT DO UPDATE`` so the FK to ``item`` is never
        cascade-fired (per [[feedback_never_insert_or_replace_with_fks]]).
        """
        with self.store.transaction():
            self.store.upsert(m.ItemVisit(
                item_id=item_id, phase=phase, visited=bool(value),
                updated_at=datetime.now(timezone.utc).isoformat(),
            ))
            self._touch()

    def clear_visited_for_phase(self, phase: str) -> int:
        """Wipe every ✓ tick (spec/32 §2.10) for ``phase`` — item ticks AND
        cluster ticks, in one transaction.  Returns the count of item_visit
        rows deleted (the caller can show "Cleared N marks" if it wants).

        - Item ticks: every ``item_visit`` row with the given phase is DELETED.
        - Cluster ticks: every ``bucket`` row with the given phase has
          ``browsed`` reset to 0.  ``current_index`` / ``default_state`` /
          ``reviewed`` / ``nudge_dismissed`` are preserved — only the visited
          bit is reset.

        Used by the "Start a new pass…" button on the days panel of Cull /
        Process (and any future phase that opts in via
        ``BucketNavigatorConfig.show_clear_marks_button``).  No state about
        the user's actual decisions (phase_state / Adjustment) is touched.
        """
        with self.store.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM item_visit WHERE phase = ?", (phase,))
            deleted = cur.rowcount or 0
            conn.execute(
                "UPDATE bucket SET browsed = 0 WHERE phase = ?", (phase,))
            self._touch()
        return int(deleted)

    def items_visited_for_day(self, day_number: Optional[int], phase: str) -> set[str]:
        """Return the set of ``item_id``s with ``visited=1`` for items on ``day_number``
        in ``phase`` (spec/32 §8.6).

        One batched read per Day Grid open; the model layer stamps
        :pyattr:`CullCell.visited` from this set.  Returns an empty set when nothing
        in the day has been visited yet — the common case for fresh events.
        """
        if day_number is None:
            rows = self.store.conn.execute(
                "SELECT iv.item_id FROM item_visit iv "
                "JOIN item it ON it.id = iv.item_id "
                "WHERE iv.phase = ? AND iv.visited = 1 AND it.day_number IS NULL",
                (phase,),
            ).fetchall()
        else:
            rows = self.store.conn.execute(
                "SELECT iv.item_id FROM item_visit iv "
                "JOIN item it ON it.id = iv.item_id "
                "WHERE iv.phase = ? AND iv.visited = 1 AND it.day_number = ?",
                (phase, day_number),
            ).fetchall()
        return {r["item_id"] for r in rows}

    # ----- classification ------------------------------------------------- #

    def set_classification(
        self, item_id: str, value: Optional[str], source: str,
        rules_version: Optional[str] = None,
        needs_review: bool = False,
        confidence: Optional[float] = None,
    ) -> None:
        """Set the genre/scenario (FS→own — never folder names). ``source='user'`` is an
        override; the auto-classifier writes ``source='auto'`` + ``rules_version`` +
        ``confidence`` (spec/58 — the Edit Style button's ramp reads the score).
        ``needs_review=True`` marks the classification as uncertain."""
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE item SET classification = ?, classification_source = ?, "
                "classification_rules_version = ?, classification_needs_review = ?, "
                "classification_confidence = ? "
                "WHERE id = ?",
                (value, source, rules_version, int(needs_review), confidence,
                 item_id),
            )
            self._touch()

    def set_classifications_bulk(
        self,
        rows: List[tuple],
    ) -> int:
        """Bulk :meth:`set_classification` — ONE transaction, one short
        lock window (the spec/58 background pass writes its whole result
        set here instead of N per-row transactions racing the UI thread's
        connection). ``rows`` =
        ``(item_id, value, source, rules_version, needs_review, confidence)``."""
        if not rows:
            return 0
        with self.store.transaction() as conn:
            conn.executemany(
                "UPDATE item SET classification = ?, classification_source = ?, "
                "classification_rules_version = ?, classification_needs_review = ?, "
                "classification_confidence = ? "
                "WHERE id = ?",
                [(v, s, rv, int(nr), conf, iid)
                 for (iid, v, s, rv, nr, conf) in rows],
            )
            self._touch()
        return len(rows)

    def edit_touched_item_ids(self) -> set:
        """Items FROZEN against auto re-classification (spec/58 §3) — any
        Edit work the user produced: an adjustment row (photo or video —
        the item's own, or a child segment/snapshot's, which freezes the
        parent video), or an edit-phase lineage row (an export).
        Untouched means re-classifiable."""
        sql = (
            "SELECT item_id AS iid FROM adjustment "
            "UNION SELECT item_id FROM video_adjustment "
            "UNION SELECT source_item_id FROM lineage "
            "      WHERE phase = 'edit' AND source_item_id IS NOT NULL "
            "UNION SELECT i.parent_item_id FROM item i "
            "      WHERE i.parent_item_id IS NOT NULL AND ("
            "            i.id IN (SELECT item_id FROM adjustment) "
            "            OR i.id IN (SELECT item_id FROM video_adjustment))"
        )
        return {r[0] for r in self.store.conn.execute(sql) if r[0]}

    # ----- process: adjustments ------------------------------------------- #

    def save_adjustment(self, adjustment: m.Adjustment) -> None:
        with self.store.transaction():
            self.store.upsert(adjustment)
            self._touch()

    def set_edit_exported(self, item_id: str, value: bool = True) -> None:
        adj = self.store.get(m.Adjustment, item_id) or m.Adjustment(item_id=item_id)
        adj.edit_exported = value
        self.save_adjustment(adj)

    def adjustments_for_day(
        self, day_number: Optional[int],
    ) -> Dict[str, m.Adjustment]:
        """Per-day Adjustment lookup, batched (spec/32 §6.3 Process Day Grid).

        Returns a dict keyed by ``item_id`` for every captured / derived item on
        ``day_number`` that has an Adjustment row.  Items WITHOUT a row are
        absent from the dict — the caller treats absence as "no decisions yet"
        (edit_exported=False, every adjustment field at its dataclass default).
        Used by the Process Day Grid renderer to colour cells (green when
        ``edit_exported``, neutral otherwise).
        """
        from mira.store.repo import _BY_CLS
        info = _BY_CLS[m.Adjustment]
        if day_number is None:
            rows = self.store.conn.execute(
                "SELECT a.* FROM adjustment a "
                "JOIN item i ON i.id = a.item_id "
                "WHERE i.day_number IS NULL"
            ).fetchall()
        else:
            rows = self.store.conn.execute(
                "SELECT a.* FROM adjustment a "
                "JOIN item i ON i.id = a.item_id "
                "WHERE i.day_number = ?",
                (day_number,),
            ).fetchall()
        out: Dict[str, m.Adjustment] = {}
        for r in rows:
            adj = self.store._row_to_obj(r, info)
            out[adj.item_id] = adj
        return out

    def save_video_adjustment(self, adjustment: m.VideoAdjustment) -> None:
        with self.store.transaction():
            self.store.upsert(adjustment)
            self._touch()

    # ----- video workshop mutators: markers / segments / snapshots -------- #
    # spec/56 retired create_clip / create_snapshot / keep_whole_video /
    # _create_child (Pick-time clip authoring with freeform spans). Segments
    # are born from markers below; whole-video export is the original single
    # segment, picked — no special case.

    def _require_source_video(self, video_item_id: str) -> m.Item:
        """The workshop targets SOURCE videos only — a root item of kind
        'video'. Markers on a segment (itself kind='video') would be nonsense;
        the parent check rejects them."""
        video = self.item(video_item_id)
        if video is None:
            raise ValueError(f"no such item: {video_item_id}")
        if video.kind != "video" or video.parent_item_id is not None:
            raise ValueError(f"{video_item_id} is not a source video")
        return video

    def _ensure_segments_in_txn(
        self, video_item_id: str, now: str, default_state: str = "skipped",
    ):
        """Materialise the dense segment-item set for the CURRENT marker set —
        the lazy birth of segment rows (first workshop touch). Caller holds the
        transaction. Returns ``(segments, created)``.

        Each segment item gets an EXPLICIT ``phase_state`` row at
        ``default_state`` — spec/59 export-status (Nelson 2026-06-11):
        the configured edit default ("born green" out of the box)
        governs clip birth too, superseding spec/56's fixed default-Skip.
        ``decided_at`` stays NULL — created-by-default, not yet decided."""
        segs = self.video_segments(video_item_id)
        n_markers = len(self.video_markers(video_item_id))
        want = n_markers + 1
        if len(segs) == want and [s.seg_index for s in segs] == list(range(want)):
            return segs, False
        if segs:
            raise RuntimeError(
                f"video {video_item_id}: {len(segs)} segment rows out of step with "
                f"{n_markers} markers — marker ops must maintain the dense set")
        if default_state not in ("picked", "skipped"):
            default_state = "skipped"
        out: List[m.VideoSegment] = []
        for idx in range(want):
            seg_item_id = self._new_id()
            self.store.upsert(m.Item(
                id=seg_item_id, kind="video", provenance="clip",
                parent_item_id=video_item_id, created_at=now,
            ))
            seg = m.VideoSegment(
                item_id=seg_item_id, video_item_id=video_item_id,
                seg_index=idx, created_at=now,
            )
            self.store.upsert(seg)
            self.store.upsert(m.PhaseState(
                item_id=seg_item_id, phase="edit", state=default_state,
            ))
            out.append(seg)
        return out, True

    def ensure_video_segments(
        self, video_item_id: str, *, default_state: str = "skipped",
    ) -> List[m.VideoSegment]:
        """Public lazy-birth entry: make the segment-item set exist for the
        current markers (one segment per marker gap; one for a marker-less
        video) and return it in ``seg_index`` order. No-op when present.
        ``default_state`` is the configured edit default the birth rows
        carry (spec/59 — callers pass ``default_state_for(.., "edit")``)."""
        self._require_source_video(video_item_id)
        with self.store.transaction():
            segs, created = self._ensure_segments_in_txn(
                video_item_id, self._now(), default_state)
            if created:
                self._touch()
        return segs

    def add_video_marker(self, video_item_id: str, at_ms: int) -> str:
        """Insert a cut point — the spec/56 split rule. The marker lands inside
        segment ``k`` (by marker order); ``k`` keeps its row as the LEFT half
        and a new item becomes the RIGHT half at ``k + 1``, inheriting the
        parent segment's phase_state rows AND its video_adjustment verbatim
        (the user re-decides as needed). Later segments shift up by one —
        their rows, states and adjustments ride along untouched.

        Requires a probed ``duration_ms`` (markers must lie strictly inside
        ``(0, duration)``); rejects a duplicate position (zero-length
        segments are impossible by construction). Returns the marker id."""
        video = self._require_source_video(video_item_id)
        if not video.duration_ms or video.duration_ms <= 0:
            raise ValueError(
                f"add_video_marker({video_item_id}): video duration_ms not probed")
        at_ms = int(at_ms)
        if not (0 < at_ms < video.duration_ms):
            raise ValueError(
                f"marker at {at_ms} outside (0, {video.duration_ms})")
        markers = self.video_markers(video_item_id)
        positions = [mk.at_ms for mk in markers]
        if at_ms in positions:
            raise ValueError(f"marker already exists at {at_ms} ms")
        k = bisect_right(positions, at_ms)          # the segment being split
        marker_id = self._new_id()
        now = self._now()
        with self.store.transaction() as conn:
            segs, _ = self._ensure_segments_in_txn(video_item_id, now)
            # Shift the tail up FIRST, highest index first, so the UNIQUE
            # (video_item_id, seg_index) never collides mid-flight.
            for seg in sorted(segs[k + 1:], key=lambda s: s.seg_index, reverse=True):
                conn.execute(
                    "UPDATE video_segment SET seg_index = ? WHERE item_id = ?",
                    (seg.seg_index + 1, seg.item_id))
            left = segs[k]
            right_id = self._new_id()
            self.store.upsert(m.Item(
                id=right_id, kind="video", provenance="clip",
                parent_item_id=video_item_id, created_at=now,
            ))
            self.store.upsert(m.VideoSegment(
                item_id=right_id, video_item_id=video_item_id,
                seg_index=k + 1, created_at=now,
            ))
            for ps in self.store.query_by(m.PhaseState, item_id=left.item_id):
                self.store.upsert(replace(ps, item_id=right_id))
            vadj = self.store.get(m.VideoAdjustment, left.item_id)
            if vadj is not None:
                self.store.upsert(replace(vadj, item_id=right_id))
            self.store.upsert(m.VideoMarker(
                id=marker_id, video_item_id=video_item_id,
                at_ms=at_ms, created_at=now,
            ))
            self._touch()
        return marker_id

    def move_video_marker(self, marker_id: str, new_at_ms: int) -> None:
        """Re-time a cut point — the spec/56 move rule: the adjacent segments
        keep their Pick state + adjustments (identity is marker-order position,
        not milliseconds), so this updates ``at_ms`` and nothing else.

        A move may not cross — or land on — a neighbouring marker: that would
        reorder the markers and silently remap every later segment's identity.
        The workshop UI clamps drags; this guard is the data-layer backstop."""
        mk = self.store.get(m.VideoMarker, marker_id)
        if mk is None:
            raise ValueError(f"no such marker: {marker_id}")
        video = self._require_source_video(mk.video_item_id)
        if not video.duration_ms or video.duration_ms <= 0:
            raise ValueError(
                f"move_video_marker({marker_id}): video duration_ms not probed")
        new_at_ms = int(new_at_ms)
        if not (0 < new_at_ms < video.duration_ms):
            raise ValueError(
                f"marker at {new_at_ms} outside (0, {video.duration_ms})")
        others = [x.at_ms for x in self.video_markers(mk.video_item_id)
                  if x.id != marker_id]
        left = max((a for a in others if a < mk.at_ms), default=0)
        right = min((a for a in others if a > mk.at_ms), default=video.duration_ms)
        if not (left < new_at_ms < right):
            raise ValueError(
                f"marker move to {new_at_ms} would cross a neighbour "
                f"(allowed range ({left}, {right}))")
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE video_marker SET at_ms = ? WHERE id = ?",
                (new_at_ms, marker_id))
            self._touch()

    def delete_video_marker(self, marker_id: str) -> None:
        """Remove a cut point — the merge rule. The marker at order position
        ``p`` separates segments ``p`` and ``p + 1``; the merged segment
        occupies position ``p``, so the LEFT segment's row, state and
        adjustments survive and the right half's item is deleted (cascade
        clears its phase_state / video_adjustment / video_segment rows).
        Later segments shift down by one. Deterministic and predictable;
        re-inserting the marker re-splits with inheritance from the survivor.

        If the segment set was never materialised, only the marker row goes
        (the derived view shrinks by itself)."""
        mk = self.store.get(m.VideoMarker, marker_id)
        if mk is None:
            raise ValueError(f"no such marker: {marker_id}")
        markers = self.video_markers(mk.video_item_id)
        p = [x.id for x in markers].index(marker_id)
        segs = self.video_segments(mk.video_item_id)
        with self.store.transaction() as conn:
            if segs:
                if len(segs) != len(markers) + 1:
                    raise RuntimeError(
                        f"video {mk.video_item_id}: {len(segs)} segment rows out of "
                        f"step with {len(markers)} markers")
                right = segs[p + 1]
                conn.execute("DELETE FROM item WHERE id = ?", (right.item_id,))
                # Ascending shift-down never collides with the UNIQUE index.
                for seg in segs[p + 2:]:
                    conn.execute(
                        "UPDATE video_segment SET seg_index = ? WHERE item_id = ?",
                        (seg.seg_index - 1, seg.item_id))
            conn.execute("DELETE FROM video_marker WHERE id = ?", (marker_id,))
            self._touch()

    def create_video_snapshot(
        self, video_item_id: str, at_ms: int, *, item_id: Optional[str] = None,
    ) -> str:
        """Place a snapshot — a virtual ``kind='photo'`` child anchored at
        ``at_ms`` — and AUTO-PICK it (``phase_state`` edit/picked): placing a
        snapshot IS the intent (spec/56 §1). Its development state is a photo
        ``adjustment`` row, identical to any photo. Returns the new item id."""
        video = self._require_source_video(video_item_id)
        at_ms = int(at_ms)
        if at_ms < 0:
            raise ValueError(f"snapshot at_ms must be >= 0, got {at_ms}")
        if video.duration_ms and at_ms > video.duration_ms:
            raise ValueError(
                f"snapshot at {at_ms} beyond duration {video.duration_ms}")
        new_id = item_id or self._new_id()
        now = self._now()
        with self.store.transaction():
            # spec/58 (Nelson 2026-06-11): snapshots sit outside the
            # captured-only background pass — inherit the video's
            # classification at creation so Edit's Style badge is honest.
            self.store.upsert(m.Item(
                id=new_id, kind="photo", provenance="snapshot",
                parent_item_id=video_item_id, created_at=now,
                classification=video.classification,
                classification_source=video.classification_source,
                classification_rules_version=video.classification_rules_version,
                classification_needs_review=video.classification_needs_review,
                classification_confidence=video.classification_confidence,
            ))
            self.store.upsert(m.VideoSnapshot(
                item_id=new_id, video_item_id=video_item_id,
                at_ms=at_ms, created_at=now,
            ))
            self.store.upsert(m.PhaseState(
                item_id=new_id, phase="edit", state="picked", decided_at=now,
            ))
            self._touch()
        return new_id

    def delete_child(self, item_id: str) -> None:
        """Remove a snapshot child — the FK cascade drops its ``video_snapshot``
        / ``phase_state`` / ``adjustment`` rows. Segments are NOT deleted
        directly (they tile the timeline by construction) — remove the marker
        instead (:meth:`delete_video_marker`); Skip is the "drop this part"
        verb."""
        with self.store.transaction() as conn:
            conn.execute("DELETE FROM item WHERE id = ?", (item_id,))
            self._touch()

    # ----- external round trip: stack-output adoption (spec/57 §2.3) ------ #

    def adopt_stack_output(
        self,
        src_path: Path,
        *,
        bracket_key: str,
        bracket_kind: str,
        member_item_ids: List[str],
        item_id: Optional[str] = None,
    ) -> str:
        """Adopt an externally-merged stack master: move the tool's output
        from the ``Picked Media/`` root into additive-only
        ``Original Media/Merged/`` (copy → sha-verify → delete source; the
        captured subtrees beside it stay untouchable) and record it as the
        bracket's FINAL result — a ``provenance='stack_output'`` item
        placed on the bracket's day so it sits beside its siblings, plus
        the ``stack_bracket``/``stack_member`` rows and an explicit
        ``phase_state('pick','picked')`` (merging it WAS the pick). The
        caller re-runs the links rebuild afterwards so the master appears
        at the projection root seamlessly (the locked spec/57 rider).

        ``bracket_kind`` is the cache kind (``focus_bracket`` /
        ``exposure_bracket``) or already the stack kind (``focus`` /
        ``exposure``). Raises on any verification failure — the source
        file is only removed after the copy proves byte-identical."""
        from core.path_builder import merged_dir

        if self.event_root is None:
            raise RuntimeError("adopt_stack_output needs a resolvable event_root")
        src = Path(src_path)
        if not src.is_file():
            raise FileNotFoundError(src)
        kind = {"focus_bracket": "focus", "exposure_bracket": "exposure"}.get(
            bracket_kind, bracket_kind)
        if kind not in ("focus", "exposure"):
            raise ValueError(f"unknown bracket kind: {bracket_kind!r}")
        members = [it for iid in member_item_ids
                   if (it := self.item(iid)) is not None]
        if not members:
            raise ValueError(f"bracket {bracket_key}: no member items found")
        members.sort(key=lambda it: it.capture_time_corrected or "")
        anchor = members[0]

        event_root = Path(self.event_root)
        dest_dir = merged_dir(event_root)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        n = 2
        while dest.exists():
            dest = dest_dir / f"{src.stem} ({n}){src.suffix}"
            n += 1

        import hashlib
        import shutil

        def _sha(p: Path) -> str:
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            return h.hexdigest()

        src_sha = _sha(src)
        shutil.copy2(src, dest)
        if _sha(dest) != src_sha:
            dest.unlink(missing_ok=True)
            raise RuntimeError(f"adoption copy verification failed for {src.name}")

        new_id = item_id or self._new_id()
        now = self._now()
        ext = src.suffix.lower()
        kind_item = "video" if ext in (".mp4", ".mov", ".avi", ".mts", ".m4v") else "photo"
        try:
            with self.store.transaction():
                # spec/58 (Nelson 2026-06-11): merged masters sit outside
                # the captured-only background pass — inherit the anchor
                # member's classification so Edit's Style badge is honest.
                self.store.upsert(m.Item(
                    id=new_id, kind=kind_item, provenance="stack_output",
                    origin_relpath=dest.relative_to(event_root).as_posix(),
                    sha256=src_sha, byte_size=dest.stat().st_size,
                    materialized_at=now, materialized_phase="edit",
                    camera_id=anchor.camera_id, day_number=anchor.day_number,
                    capture_time_corrected=anchor.capture_time_corrected,
                    created_at=now,
                    classification=anchor.classification,
                    classification_source=anchor.classification_source,
                    classification_rules_version=anchor.classification_rules_version,
                    classification_needs_review=anchor.classification_needs_review,
                    classification_confidence=anchor.classification_confidence,
                ))
                self.store.upsert(m.StackBracket(
                    bracket_id=bracket_key, kind=kind, action="stacked",
                    output_item_id=new_id, day_number=anchor.day_number,
                ))
                for ordinal, it in enumerate(members):
                    self.store.upsert(m.StackMember(
                        bracket_id=bracket_key, item_id=it.id, ordinal=ordinal))
                self.store.upsert(m.PhaseState(
                    item_id=new_id, phase="pick", state="picked", decided_at=now))
                self._touch()
        except Exception:
            dest.unlink(missing_ok=True)   # roll the bytes back with the txn
            raise
        # Bytes are safe + recorded — only now does the root original go.
        src.unlink(missing_ok=True)
        return new_id

    def materialize(
        self, item_id: str, *, origin_relpath: str, sha256: str, byte_size: int,
        phase: str, materialized_at: Optional[str] = None,
        day_number: Optional[int] = None,
        capture_time_corrected: Optional[str] = None,
        duration_ms: Optional[int] = None,
    ) -> None:
        """The single virtual→real transition: fill a child item's file identity — the
        all-or-nothing CHECK columns (``origin_relpath``/``sha256``/``byte_size``/
        ``materialized_at``) plus ``materialized_phase``, set together in one UPDATE so the
        constraint is never momentarily violated.

        spec/56: nothing materialises before Export — the Export walker (slice 4)
        renders each picked segment/snapshot through its adjustments and calls this
        with ``phase='edit'`` (``'pick'`` left the ``materialized_phase`` enum).

        ``day_number`` / ``capture_time_corrected`` / ``duration_ms`` are the optional
        *placement* fields for the new physical item (the scanner groups by
        ``day_number`` and orders by ``capture_time_corrected``); leave them ``None``
        when placement is already set."""
        sets = ["origin_relpath = ?", "sha256 = ?", "byte_size = ?",
                "materialized_at = ?", "materialized_phase = ?"]
        vals: list = [origin_relpath, sha256, byte_size,
                      materialized_at or self._now(), phase]
        if day_number is not None:
            sets.append("day_number = ?"); vals.append(day_number)
        if capture_time_corrected is not None:
            sets.append("capture_time_corrected = ?"); vals.append(capture_time_corrected)
        if duration_ms is not None:
            sets.append("duration_ms = ?"); vals.append(duration_ms)
        vals.append(item_id)
        with self.store.transaction() as conn:
            conn.execute(
                f"UPDATE item SET {', '.join(sets)} WHERE id = ?", tuple(vals))
            self._touch()

    def unmaterialized_kept_children(self, phase: str) -> List[m.Item]:
        """Virtual segment/snapshot children picked at ``phase`` whose file identity
        is still NULL — the Export work-list (spec/56 slice 4 walks this with
        ``phase='edit'``). Pick no longer creates children at all, so
        ``phase='pick'`` legitimately returns empty (the cull-exit materialiser it
        used to feed retires with slice 2).

        Ordered by parent then ``seg_index`` (segments in timeline order — order
        IS geometry under the marker-partition model), snapshots after, then id."""
        sql = (
            "SELECT item.* FROM item "
            "JOIN phase_state ps ON ps.item_id = item.id "
            "                   AND ps.phase = ? AND ps.state = 'picked' "
            "LEFT JOIN video_segment vs ON vs.item_id = item.id "
            "WHERE item.provenance IN ('clip','snapshot') "
            "  AND item.origin_relpath IS NULL "
            "ORDER BY item.parent_item_id, vs.seg_index IS NULL, vs.seg_index, item.id"
        )
        return self.store.query_raw(m.Item, sql, (phase,))

    # ----- share / cuts mutators (spec/61) --------------------------------- #

    def create_cut(
        self,
        name: str,
        *,
        target_s: Optional[int] = None,
        max_s: Optional[int] = None,
        photo_s: float = 6.0,
        pool_expr: Sequence[Tuple[str, str]] = (),
        style_filter: Sequence[str] = (),
        type_filter: str = "both",
        default_state: str = "skipped",
        music_category: Optional[str] = None,
        card_style: str = "black",
    ) -> m.Cut:
        """Create a Cut from a user-typed name. The dialog previews the
        transform live, but the gateway is the enforcement point: the name is
        slugified here and re-validated against this event's tags — raises
        ``ValueError`` carrying the :func:`core.cut_names.check_tag` code
        ('empty' / 'reserved' / 'taken')."""
        slug = cut_names.slugify(name)
        err = cut_names.check_tag(slug, [c.tag for c in self.cuts()])
        if err:
            raise ValueError(err)
        now = self._now()
        cut = m.Cut(
            id=self._new_id(), tag=slug, created_at=now, updated_at=now,
            target_s=target_s, max_s=max_s, photo_s=photo_s,
            pool_expr_json=json.dumps([list(t) for t in pool_expr]),
            style_filter_json=json.dumps(list(style_filter)),
            type_filter=type_filter, default_state=default_state,
            music_category=music_category,
            extras_json=json.dumps({"card_style": card_style}),
        )
        with self.store.transaction():
            self.store.upsert(cut)
            self._touch()
        return cut

    def rename_cut(self, cut_id: str, new_name: str) -> m.Cut:
        """Rename = update one cell (spec/61 §1.4). Same transform +
        validation as creation, excluding the Cut itself from the taken
        check. Already-exported folders keep their old name (snapshot
        semantics, spec/61 §5.2)."""
        cut = self.cut(cut_id)
        if cut is None:
            raise KeyError(cut_id)
        slug = cut_names.slugify(new_name)
        err = cut_names.check_tag(
            slug, [c.tag for c in self.cuts() if c.id != cut_id])
        if err:
            raise ValueError(err)
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE cut SET tag = ?, updated_at = ? WHERE id = ?",
                (slug, self._now(), cut_id))
            self._touch()
        return replace(cut, tag=slug)

    def update_cut_settings(self, cut_id: str, **fields) -> None:
        """Re-entering the creation session may change the recipe fields
        (target_s / max_s / photo_s / pool_expr_json / style_filter_json /
        type_filter / default_state / music_category / card_style). Tag
        changes go through :meth:`rename_cut`; membership through
        :meth:`set_cut_members`."""
        card_style = fields.pop("card_style", None)
        if card_style is not None:
            cut = self.cut(cut_id)
            try:
                extras = json.loads(cut.extras_json) if cut else {}
            except (ValueError, TypeError):
                extras = {}
            extras["card_style"] = card_style
            fields["extras_json"] = json.dumps(extras)
        allowed = {"target_s", "max_s", "photo_s", "pool_expr_json",
                   "style_filter_json", "type_filter", "default_state",
                   "music_category", "extras_json"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown cut fields: {sorted(unknown)}")
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.store.transaction() as conn:
            conn.execute(
                f"UPDATE cut SET {sets}, updated_at = ? WHERE id = ?",
                (*fields.values(), self._now(), cut_id))
            self._touch()

    def delete_cut(self, cut_id: str) -> None:
        """Drop the definition; membership cascades away (FK). Zero bytes
        touched — any already-exported folder on disk is a snapshot the
        user owns (spec/61 §1.3 / §5.2)."""
        with self.store.transaction() as conn:
            conn.execute("DELETE FROM cut WHERE id = ?", (cut_id,))
            self._touch()

    def set_cut_members(self, cut_id: str, export_relpaths: Iterable[str]) -> int:
        """The Create Cut commit (spec/61 §2 step 7): replace the Cut's
        membership with the session's picked files, one transaction, bulk
        (no per-row transactions — store.transaction() is not reentrant).
        Returns the new member count."""
        relpaths = list(dict.fromkeys(export_relpaths))  # dedupe, keep order
        now = self._now()
        with self.store.transaction() as conn:
            conn.execute("DELETE FROM cut_member WHERE cut_id = ?", (cut_id,))
            conn.executemany(
                "INSERT INTO cut_member (cut_id, export_relpath, added_at) "
                "VALUES (?, ?, ?)",
                [(cut_id, rp, now) for rp in relpaths])
            conn.execute(
                "UPDATE cut SET updated_at = ? WHERE id = ?", (now, cut_id))
            self._touch()
        return len(relpaths)

    def mark_cut_exported(self, cut_id: str) -> None:
        """Stamp ``last_exported_at`` — the list row's exported status
        (spec/61 §10 #5). Called by the export slice after materializing."""
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE cut SET last_exported_at = ? WHERE id = ?",
                (self._now(), cut_id))
            self._touch()

    # ----- people links (spec/53 §2.5) ------------------------------------ #

    def add_photo_person(self, item_id: str, person_id: str, *,
                         source: str = "user", confidence: Optional[float] = None) -> None:
        """Link an item to a person from the user-level catalog (spec/53 §2.5)."""
        with self.store.transaction():
            self.store.upsert(m.PhotoPerson(
                item_id=item_id, person_id=person_id,
                source=source, confidence=confidence, tagged_at=self._now(),
            ))
            self._touch()

    def remove_photo_person(self, item_id: str, person_id: str) -> None:
        with self.store.transaction() as conn:
            conn.execute(
                "DELETE FROM photo_person WHERE item_id = ? AND person_id = ?",
                (item_id, person_id),
            )
            self._touch()

    def set_budget(
        self, *, short_target_s: Optional[int] = None, short_max_s: Optional[int] = None,
        long_target_s: Optional[int] = None, long_max_s: Optional[int] = None,
        video_share: Optional[float] = None,
    ) -> None:
        """The trip budget (folded into ``event``, 1:1) — Short/Long durations + video
        share."""
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE event SET budget_short_target_s = ?, budget_short_max_s = ?, "
                "budget_long_target_s = ?, budget_long_max_s = ?, budget_video_share = ? "
                "WHERE id = 1",
                (short_target_s, short_max_s, long_target_s, long_max_s, video_share),
            )
            self._touch()

    # spec/52 retired: record_distribution (distribution_action table gone).

    # ----- stacks / lineage ----------------------------------------------- #

    def save_stack(self, bracket: m.StackBracket, members: Iterable[m.StackMember]) -> None:
        with self.store.transaction():
            self.store.upsert(bracket)
            for sm in members:
                self.store.upsert(sm)
            self._touch()

    def set_stack_action(
        self, bracket_id: str, action: str, picked_index: int = -1,
        output_item_id: Optional[str] = None,
    ) -> None:
        brk = self.store.get(m.StackBracket, bracket_id)
        if brk is None:
            raise KeyError(f"no stack bracket {bracket_id}")
        brk.action = action
        brk.picked_index = picked_index
        if output_item_id is not None:
            brk.output_item_id = output_item_id
        with self.store.transaction():
            self.store.upsert(brk)
            self._touch()

    def record_lineage(self, entry: m.Lineage) -> None:
        with self.store.transaction():
            self.store.upsert(entry)
            self._touch()

    def clear_lineage(self, phase: str) -> None:
        """Drop a phase's lineage rows (rebuilt on re-export)."""
        with self.store.transaction() as conn:
            conn.execute("DELETE FROM lineage WHERE phase = ?", (phase,))
            self._touch()

    # ----- event ---------------------------------------------------------- #

    def set_closed(self, value: bool) -> None:
        """The Open/Closed bit — the only lifecycle bit (D6)."""
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE event SET is_closed = ?, updated_at = ? WHERE id = 1",
                (1 if value else 0, self._now()),
            )

    def save_trip_days(self, days: List[m.TripDay]) -> None:
        """Update the event's trip-day plan to ``days`` — a **diff**, not a delete-all.

        Kept/edited days are upserted **in place** (``ON CONFLICT DO UPDATE``), so their
        items' ``day_number`` links are preserved; a blind ``DELETE FROM trip_day`` would
        instead fire ``item.day_number … ON DELETE SET NULL`` and silently orphan every
        photo from its day.

        Removing a day is the genuinely destructive case, handled by dedicated operations
        (soft-hide / hard-delete / move-to-another-event — see spec/14). Until those land,
        a plan edit that would drop a day **with items** is rejected with a
        ``sqlite3.IntegrityError`` ('move the photos off that day first'); dropping an
        empty day (e.g. shrinking a plan-only event) is allowed."""
        incoming = {d.day_number for d in days}
        existing = {d.day_number for d in self.trip_days()}
        removed = existing - incoming
        with self.store.transaction() as conn:
            conn.execute("PRAGMA defer_foreign_keys = ON")
            if removed:
                qs = ", ".join("?" for _ in removed)
                row = conn.execute(
                    f"SELECT 1 FROM item WHERE day_number IN ({qs}) LIMIT 1",
                    tuple(removed),
                ).fetchone()
                if row is not None:
                    raise sqlite3.IntegrityError(
                        "cannot remove a trip day that still has items; move the photos "
                        "to another day (or hide/delete the day) first"
                    )
            for d in days:
                self.store.upsert(d)
            for day_number in removed:
                conn.execute("DELETE FROM trip_day WHERE day_number = ?", (day_number,))
            self._touch()

    # ── spec/45 Slice TZ-3 — camera_day_tz reads/writes ─────────────────

    def camera_day_tz(
        self, camera_id: str, day_number: int,
    ) -> Optional[m.CameraDayTz]:
        """The declared TZ for one ``(camera, day)``, or ``None`` if no row.
        The bake's read path: fall back to ``camera.applied_offset_minutes``
        on ``None``."""
        return self.store.get(m.CameraDayTz, camera_id, day_number)

    def camera_day_tz_all(self) -> List[m.CameraDayTz]:
        """Every row in the table — useful for debugging + the bulk reader
        the bake step will consume per-event in a future polish."""
        return self.store.all(m.CameraDayTz)

    def set_camera_day_tz(
        self,
        camera_id: str,
        day_number: int,
        *,
        tz_minutes: int,
        source: str,
    ) -> None:
        """Upsert one row. ``tz_minutes`` MUST be in
        :data:`core.discrete_tz.STANDARD_TZ_OFFSETS_MINUTES`; ``source`` MUST be
        one of ``'phone_auto'`` / ``'user_declared'`` / ``'pair_picker'``.
        Both validations live at the gateway boundary — silent coercion would
        mask dialog-side bugs that produced an invalid value (review finding
        #3 silent-no-op principle)."""
        from core.discrete_tz import is_valid_offset
        if not is_valid_offset(tz_minutes):
            raise ValueError(
                f"unknown TZ offset {tz_minutes!r}; not in the discrete TZ enum"
            )
        if source not in ("phone_auto", "user_declared", "pair_picker"):
            raise ValueError(
                f"unknown camera_day_tz source {source!r}"
            )
        self.store.upsert(m.CameraDayTz(
            camera_id=camera_id,
            day_number=day_number,
            declared_tz_minutes=tz_minutes,
            source=source,
            declared_at=self._now(),
        ))
        self._touch()

    def bulk_set_camera_day_tz_from_phone(
        self,
        camera_ids: List[str],
        day_offsets: Dict[int, int],
    ) -> None:
        """Write ``phone_auto`` rows for every (camera, day) combination —
        called by the capture flow after the DiscreteTzDialog runs so days
        the user didn't need to answer (phones present on every camera that
        day) get persistence too. Skips invalid offsets quietly (the dialog
        validates them at pick-time; this is the bulk fast-path)."""
        from core.discrete_tz import is_valid_offset
        stamp = self._now()
        rows: List[m.CameraDayTz] = []
        for camera_id in camera_ids:
            for day_number, tz_minutes in day_offsets.items():
                if not is_valid_offset(tz_minutes):
                    continue
                rows.append(m.CameraDayTz(
                    camera_id=camera_id,
                    day_number=day_number,
                    declared_tz_minutes=tz_minutes,
                    source="phone_auto",
                    declared_at=stamp,
                ))
        if not rows:
            return
        with self.store.transaction():
            for row in rows:
                self.store.upsert(row)
            self._touch()

    def set_trip_day_extras(
        self, day_number: int, updates: Dict[str, Any],
    ) -> None:
        """Shallow-merge ``updates`` into ``trip_day.extras_json`` for the
        given day.

        Used by Slice TZ-2 (spec/45) to write phone-derived ``country_code``
        per day; reusable for any future per-day extras (city, sublocation,
        custom IPTC keys). Matches the seam pattern
        :meth:`Gateway.set_classification`'s ``extras_updates`` uses for
        ``event.extras_json`` — IPTC location facets and classification
        namespaces stay merged rather than overwriting one another.

        ``updates={}`` is a fast no-op; ``day_number`` not in the DB is a
        warned no-op (don't silently create rows here)."""
        import json as _json
        if not updates:
            return
        with self.store.transaction() as conn:
            row = conn.execute(
                "SELECT extras_json FROM trip_day WHERE day_number = ?",
                (day_number,),
            ).fetchone()
            if row is None:
                log.warning(
                    "set_trip_day_extras: day %s not found — skipping", day_number,
                )
                return
            try:
                current = _json.loads(row["extras_json"] or "{}")
                if not isinstance(current, dict):
                    current = {}
            except (ValueError, TypeError):
                current = {}
            current.update(updates)
            conn.execute(
                "UPDATE trip_day SET extras_json = ? WHERE day_number = ?",
                (_json.dumps(current), day_number),
            )
            self._touch()

    def set_day_hidden(self, day_number: int, hidden: bool) -> None:
        """Soft-hide / unhide a whole trip day (spec/14 §5C.1). Items derive their
        visibility from this flag via the ``visible_item`` view — phase work + completion
        metrics disregard a hidden day's items — but ``phase_state`` is left untouched, so
        unhiding restores every prior cull/select/process decision intact. A targeted
        ``UPDATE`` (no row delete → no FK action fires)."""
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE trip_day SET hidden = ? WHERE day_number = ?",
                (1 if hidden else 0, day_number),
            )
            self._touch()

    # ----- ingest --------------------------------------------------------- #

    def save_item(self, item: m.Item) -> None:
        with self.store.transaction():
            self.store.upsert(item)
            self._touch()

    def add_items(self, items: Iterable[m.Item]) -> None:
        with self.store.transaction() as conn:
            conn.execute("PRAGMA defer_foreign_keys = ON")
            for it in items:
                self.store.upsert(it)
            self._touch()

    def add_cameras(self, cameras: Iterable[m.Camera]) -> None:
        """Insert camera rows that don't already exist, leaving existing ones untouched
        (the Capture append-ingest: a later card may surface a new camera, but an already-
        calibrated camera must keep its calibration)."""
        existing = {c.camera_id for c in self.cameras()}
        fresh = [c for c in cameras if c.camera_id not in existing]
        if not fresh:
            return
        with self.store.transaction():
            for c in fresh:
                self.store.upsert(c)
            self._touch()

    def save_camera(self, camera: m.Camera) -> None:
        """Replace-or-insert one camera row — the camera-clock / Adjust-TZ commit. Unlike
        :meth:`add_cameras` (insert-missing-only), this *does* clobber an existing row,
        because the user is deliberately re-calibrating it. Pair it with
        :meth:`recompute_corrected_times` so the items' corrected times follow."""
        with self.store.transaction():
            self.store.upsert(camera)
            self._touch()

    def retime_day(self, day_number: int, new_tz_minutes: int) -> Dict[str, int]:
        """spec/57 §4.2 — the single-day TZ fix-up. The day's declared TZ
        changes from its current value to ``new_tz_minutes``; every captured
        item ON that day shifts by the delta (``corrected = raw + offset +
        delta``; raw never touched), ``tz_source`` becomes ``'user_declared'``
        and ``day_number`` is reassigned from the new corrected date against
        the plan (smallest-day-number-wins) — the "may move some across days"
        the confirmation warns about. ``trip_day.tz_minutes`` is updated in
        the same transaction; downstream marks go ``derived_dirty``.

        A day whose TZ was never set re-times from a 0 baseline. Returns
        ``{'affected': n, 'moved': m}``."""
        day = self.store.get(m.TripDay, day_number)
        if day is None:
            raise ValueError(f"no trip day {day_number}")
        delta_min = int(new_tz_minutes) - int(day.tz_minutes or 0)
        by_date: Dict[str, int] = {}
        for d in sorted(self.trip_days(), key=lambda x: x.day_number):
            if d.date and d.date not in by_date:
                by_date[d.date] = d.day_number
        items = self.items(day=day_number, provenance="captured",
                           include_hidden=True)
        affected: List[str] = []
        moved = 0
        with self.store.transaction() as conn:
            for it in items:
                if not it.capture_time_raw:
                    continue
                try:
                    raw_dt = datetime.fromisoformat(it.capture_time_raw)
                except ValueError:
                    continue
                new_offset = int(it.tz_offset_minutes or 0) + delta_min
                corrected_dt = raw_dt + timedelta(minutes=new_offset)
                new_day = by_date.get(corrected_dt.date().isoformat(),
                                      it.day_number)
                if new_day != it.day_number:
                    moved += 1
                conn.execute(
                    "UPDATE item SET capture_time_corrected = ?, "
                    "tz_offset_minutes = ?, tz_source = ?, day_number = ? "
                    "WHERE id = ?",
                    (corrected_dt.isoformat(), new_offset, "user_declared",
                     new_day, it.id),
                )
                affected.append(it.id)
            conn.execute(
                "UPDATE trip_day SET tz_minutes = ? WHERE day_number = ?",
                (int(new_tz_minutes), day_number),
            )
            self._touch()
        if affected:
            for phase in ("pick", "edit"):
                self.mark_derived_dirty(phase, affected)
        return {"affected": len(affected), "moved": moved}

    def _downstream_refs(self, item_ids: set) -> Optional[str]:
        """Return a label ('lineage'/'stacks') if any of ``item_ids`` is referenced by
        downstream Process/Curate work, else ``None`` — the guard for delete/move (spec/14
        §5D Q4: never half-remove derived work)."""
        if not item_ids:
            return None
        conn = self.store.conn
        qs = ",".join("?" for _ in item_ids)
        params = tuple(item_ids)
        if conn.execute(
            f"SELECT 1 FROM lineage WHERE source_item_id IN ({qs}) LIMIT 1", params
        ).fetchone():
            return "lineage"
        if conn.execute(
            f"SELECT 1 FROM stack_member WHERE item_id IN ({qs}) LIMIT 1", params
        ).fetchone():
            return "stacks"
        if conn.execute(
            f"SELECT 1 FROM stack_bracket WHERE output_item_id IN ({qs}) LIMIT 1", params
        ).fetchone():
            return "stacks"
        return None

    def delete_day(self, day_number: int) -> Dict[str, int]:
        """Hard-delete a trip day (spec/14 §5C.2): its captured items' records — FK
        ``ON DELETE CASCADE`` removes each item's phase_state / adjustment / video_adjustment /
        video_marker / video_segment / video_snapshot / lineage (and its cut_member
        rows) / stack_member /
        bucket_member and its derived children (``parent_item_id``) — this event's
        **copied files** under the event root, then
        the ``trip_day`` row. The source card / backup is **never** touched (only this event's
        managed copies). Returns ``{'items_deleted', 'files_deleted'}``.

        **Blocks** (raises ``ValueError``) when any of the day's items or their derived children
        are referenced by downstream Process/Curate work (lineage / stacks): v1 scope is
        cull/select-level days, derived work is never half-deleted (spec/14 §5D Q4)."""
        captured = self.items(day=day_number, include_hidden=True)
        ids: set = set()
        for it in captured:
            ids.add(it.id)
            for ch in self.children(it.id):
                ids.add(ch.id)
        ref = self._downstream_refs(ids)
        if ref is not None:
            raise ValueError(
                f"day {day_number} has downstream {ref} work — remove the Process/Curate "
                "output for these photos before deleting the day"
            )
        files = [
            self.event_root / it.origin_relpath
            for it in captured
            if it.origin_relpath and self.event_root is not None
        ]
        with self.store.transaction() as conn:
            conn.execute("DELETE FROM item WHERE day_number = ?", (day_number,))
            conn.execute("DELETE FROM trip_day WHERE day_number = ?", (day_number,))
            self._touch()
        deleted = 0
        for f in files:
            try:
                if f.exists():
                    f.unlink()
                    deleted += 1
            except OSError:
                log.warning("delete_day: could not remove %s", f)
        log.info("delete_day: day %s — %d item record(s), %d file(s) removed",
                 day_number, len(captured), deleted)
        return {"items_deleted": len(captured), "files_deleted": deleted}

    def save_calibration_pair(self, pair: m.CameraCalibrationPair) -> None:
        with self.store.transaction():
            self.store.upsert(pair)
            self._touch()

    def backfill_video_durations(self) -> int:
        """Probe ``duration_ms`` for every captured video item that still has NULL.

        Reads the running time from the file via ExifTool (same ``duration_seconds``
        attribute the ingest engine uses).  Skips items whose origin file cannot be
        found on disk.  Returns the number of items updated."""
        from pathlib import Path as _Path
        from core.exif_reader import read_exif_batch

        if self.event_root is None:
            return 0
        root = _Path(self.event_root)
        rows = self.store.conn.execute(
            "SELECT id, origin_relpath FROM item "
            "WHERE kind='video' AND provenance='captured' AND duration_ms IS NULL"
        ).fetchall()
        if not rows:
            return 0

        path_to_id: dict = {}
        for item_id, relpath in rows:
            if relpath:
                p = root / relpath
                if p.exists():
                    path_to_id[p] = item_id

        if not path_to_id:
            return 0

        updated = 0
        exifs = read_exif_batch(list(path_to_id.keys()))
        with self.store.transaction():
            for pe in exifs:
                item_id = path_to_id.get(pe.path)
                if item_id is None:
                    continue
                dur_s = getattr(pe, "duration_seconds", None) or 0.0
                dur_ms = round(dur_s * 1000)
                if dur_ms > 0:
                    self.store.conn.execute(
                        "UPDATE item SET duration_ms = ? WHERE id = ?",
                        (dur_ms, item_id),
                    )
                    updated += 1
            if updated:
                self._touch()
        log.info("backfill_video_durations: updated %d items", updated)
        return updated

    def set_sharpness(
        self, item_id: str, score: float, metric: str = "lapvar_wf_v1",
    ) -> None:
        """Persist a lazy-computed sharpness score on an item (G10). Targeted ``UPDATE``
        (not ``upsert``) to avoid the FK cascade ``INSERT OR REPLACE`` on ``item`` would
        trigger on child tables."""
        with self.store.transaction():
            self.store.conn.execute(
                "UPDATE item SET sharpness_score = ?, sharpness_metric = ? WHERE id = ?",
                (score, metric, item_id),
            )
            self._touch()

    def recompute_corrected_times(
        self, camera_id: str, *, applied_offset_minutes: int,
        day_number: Optional[int] = None,
    ) -> List[str]:
        """Re-derive ``capture_time_corrected`` for a camera's items from a new applied
        offset — the virtual-EXIF replacement for the legacy in-place EXIF re-bake (G5).
        Shared by Camera-clocks (B1) and Adjust-TZ (B2).

        For each captured item of ``camera_id`` (optionally only those on ``day_number``):
        ``corrected = raw + offset`` (raw never touched, G5); ``tz_offset_minutes`` ← the
        new offset; ``tz_source`` ← ``'manual'``; ``day_number`` reassigned from the new
        corrected date against the plan (smallest-day-number-wins on a duplicate date).
        Items with no raw timestamp are skipped. Returns the affected item ids; downstream
        marks are flagged ``derived_dirty`` (G4)."""
        offset = timedelta(minutes=applied_offset_minutes)
        by_date: Dict[str, int] = {}
        for d in sorted(self.trip_days(), key=lambda x: x.day_number):
            if d.date and d.date not in by_date:
                by_date[d.date] = d.day_number

        # include_hidden: a hidden day's items still need consistent corrected times for
        # when the day is unhidden (the recompute is a model fix, not a phase read).
        items = self.items(camera_id=camera_id, provenance="captured", include_hidden=True)
        affected: List[str] = []
        with self.store.transaction():
            for it in items:
                if day_number is not None and it.day_number != day_number:
                    continue
                if not it.capture_time_raw:
                    continue
                try:
                    raw_dt = datetime.fromisoformat(it.capture_time_raw)
                except ValueError:
                    continue
                corrected_dt = raw_dt + offset
                new_corrected = corrected_dt.isoformat()
                new_day = by_date.get(corrected_dt.date().isoformat(), it.day_number)
                # spec/52: tz_source enum aligned to camera_day_tz.source.
                # A manual recompute (the user dialed in an applied offset
                # for one camera) is a user-declared offset.
                self.store.conn.execute(
                    "UPDATE item SET capture_time_corrected = ?, tz_offset_minutes = ?, "
                    "tz_source = ?, day_number = ? WHERE id = ?",
                    (new_corrected, applied_offset_minutes, "user_declared", new_day, it.id),
                )
                affected.append(it.id)
            self._touch()

        if affected:
            for phase in _PHASES:
                self.mark_derived_dirty(phase, affected)
        return affected
