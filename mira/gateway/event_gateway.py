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
from typing import Callable, Dict, Iterable, List, Mapping, NamedTuple, Optional, Sequence, Set, Tuple

from core import collection_resolver, cut_budget, cut_names, recipe_resolver
from core.video_segments import segment_bounds as derive_segment_bounds
from mira.store import models as m
from mira.store.repo import EventStore

log = logging.getLogger(__name__)

_PHASES = ("pick", "edit")  # decision phases with derived caches (spec/66; 'export' joins when its surface lands)


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


class LineageRatings(NamedTuple):
    """spec/159 — per-version ratings on the Exported Collection
    review surface. Returned by :meth:`EventGateway.lineage_ratings`
    as a flat bag so the caller doesn't juggle four separate reads.

    All fields are nullable / falsey when never set."""
    stars: Optional[int]
    color_label: Optional[str]
    flag: bool
    to_delete: bool
    #: spec/159 §6+ — at most one preferred per ``source_item_id``.
    is_preferred: bool = False


#: spec/159 — accepted colour labels (LRC convention).
_LINEAGE_COLOR_LABELS = frozenset(
    ("red", "yellow", "green", "blue", "purple"))


class EventGateway:
    """The query/mutator facade over one open event."""

    def __init__(
        self,
        store: EventStore,
        *,
        event_root: Optional[Path] = None,
        now: Callable[[], str] = _utc_now_iso,
        new_id: Callable[[], str] = _new_uuid,
        db_path: Optional[Path] = None,
        backups_dir: Optional[Path] = None,
        app_version: str = "",
        on_close: Optional[Callable[["EventGateway"], None]] = None,
        collections_library_factory: Optional[Callable[
            [], Tuple[Dict[str, dict], Dict[str, dict]]]] = None,
    ) -> None:
        self.store = store
        self.event_root = event_root
        self._now = now
        self._new_id = new_id
        # spec/94 Phase 2 — the file-based Collection library (spec/93 §4).
        # The factory builds ``(by_id, by_name)`` dicts of operand payloads
        # ``{"expr": [...], "filters": {...}}``. Called LAZILY on the first
        # operand lookup that misses the event.db DC table; the result is
        # CACHED on the EventGateway instance so a single ``open_event()``
        # lifetime touches the library tree once, not once per nested
        # operand. ``None`` falls through to event.db-only behaviour
        # (existing tests + ad-hoc opens without an umbrella gateway).
        self._collections_library_factory = collections_library_factory
        self._collections_library_cache: Optional[
            Tuple[Dict[str, dict], Dict[str, dict]]] = None
        # spec/79 §7.2 — close-if-dirty snapshot context. ``backups_dir``
        # is None for ad-hoc opens (tests, direct EventStore.open); the
        # real run gets one from Gateway.open_event.
        self._db_path = Path(db_path) if db_path is not None else None
        self._backups_dir = Path(backups_dir) if backups_dir is not None else None
        self._app_version = app_version
        # spec/81 Phase 2 Item 1 — close-time cross-event projection sync.
        # The umbrella :class:`Gateway` injects a callable that runs
        # :meth:`LibraryGateway.sync_event` against the open event store
        # before it closes, so cross-event reads off ``global_items`` are
        # always one event-close behind real state. None = no sync (tests,
        # ad-hoc opens).
        self._on_close = on_close
        # Baseline count of writes on this connection — close compares
        # against it to decide whether the session was dirty. The
        # baseline is captured AFTER ``EventStore.open`` so a pending
        # schema migration's writes are already folded in (a migrated
        # session is dirty in its own right; the snapshot saves the
        # post-migrate state).
        try:
            self._changes_at_open = self.store.conn.total_changes
        except AttributeError:
            self._changes_at_open = 0

    # ----- lifecycle ----------------------------------------------------- #

    @classmethod
    def open(
        cls,
        db_path: Path,
        *,
        event_root: Optional[Path] = None,
        now: Callable[[], str] = _utc_now_iso,
        new_id: Callable[[], str] = _new_uuid,
        backups_dir: Optional[Path] = None,
        app_version: str = "",
        on_close: Optional[Callable[["EventGateway"], None]] = None,
        collections_library_factory: Optional[Callable[
            [], Tuple[Dict[str, dict], Dict[str, dict]]]] = None,
    ) -> "EventGateway":
        return cls(
            EventStore.open(db_path),
            event_root=event_root, now=now, new_id=new_id,
            db_path=Path(db_path), backups_dir=backups_dir,
            app_version=app_version,
            on_close=on_close,
            collections_library_factory=collections_library_factory,
        )

    def close(self) -> None:
        # Did this session write to event.db? Both the spec/79 §7.2
        # backup snapshot AND the spec/81 Phase 2 cross-event sync hook
        # gate on this — a read-only open (e.g. the events dashboard
        # walking each card to read trip_days / day_tree) leaves the
        # event.db identical to its on-disk state, so neither a fresh
        # snapshot nor a re-projection of its slice into
        # ``mira.db.global_items`` adds any information. Skipping them
        # cuts a 10-event dashboard refresh from 10 sync log lines +
        # 10 re-projections to zero.
        #
        # The except catches both ``AttributeError`` (Python store
        # stubs in unit tests that don't carry a real ``conn``) and
        # ``sqlite3.ProgrammingError`` (a second ``close()`` after the
        # first already shut the connection — many tests close eg in
        # the test body and again in fixture teardown; close() must
        # stay idempotent).
        try:
            dirty = self.store.conn.total_changes > self._changes_at_open
        except (AttributeError, sqlite3.ProgrammingError):
            dirty = False
        # spec/79 §7.2 — snapshot before close-if-dirty. Runs while
        # the source connection is still open (the online backup API
        # needs both sides). Snapshot failure is logged but never
        # blocks close — a stuck close would be worse than a missed
        # snapshot.
        if (
            dirty
            and self._backups_dir is not None
            and self._db_path is not None
            and self._db_path.exists()
        ):
            try:
                from core import db_backup
                # spec/82 §A.1 — close-if-dirty is a milestone trigger
                # (the natural rollback point after a working session).
                db_backup.snapshot(
                    self._db_path,
                    self._backups_dir,
                    reason=db_backup.REASON_MILESTONE,
                    app_version=self._app_version,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "db_backup: snapshot on close failed for %s: %s",
                    self._db_path, exc,
                )
        # spec/81 Phase 2 Item 1 — cross-event projection sync hook.
        # Same dirty gate: a read-only open never changes what would
        # be projected, so re-running ``LibraryGateway.sync_event``
        # writes identical rows to ``mira.db.global_items``. Runs
        # BEFORE store.close() so the hook sees a live connection.
        # Failure is logged but never blocks close.
        #
        # Catchup for un-mutated events (spec/94 Phase 4b): app
        # startup now calls :meth:`Gateway.reconcile_global_items`
        # which forces a sync_event for every known event regardless
        # of whether it was opened-and-edited this session, so the
        # post-migration "freshly-migrated event stays NULL until the
        # user edits it" gap no longer applies.
        if dirty and self._on_close is not None:
            try:
                self._on_close(self)
            except Exception as exc:                       # noqa: BLE001
                log.warning(
                    "event_gateway: on_close sync failed for %s: %s",
                    self._db_path, exc,
                )
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

    def phase_decided_count(self, phase: str) -> int:
        """Number of items the user has *decided* at ``phase`` — any explicit
        state (picked / skipped / compare), visibility-filtered. This is the
        review-completeness numerator (spec/66: Pick% = decided / captured),
        distinct from :meth:`phase_picked_count` which counts only keepers."""
        row = self.store.conn.execute(
            "SELECT COUNT(*) AS n FROM phase_state ps "
            "JOIN visible_item v ON v.id = ps.item_id "
            "WHERE ps.phase = ?",
            (phase,),
        ).fetchone()
        return int(row["n"] or 0)

    def phase_day_progress(self) -> Dict[str, Dict[Optional[int], Dict[str, int]]]:
        """Per-phase, per-day decided/total/committed/kept counts for the events-list
        card's phase × day heatmap — two ``GROUP BY``s (no Python loops over every row).
        ``{phase: {day_number: {'total','decided','committed','picked'}}}``; ``total`` is the
        captured items on that day, ``decided`` the ones with an explicit mark. Both
        aggregates read ``visible_item`` so a hidden day contributes nothing (spec/14 §5C.1).

        **Buckets (spec/66 — Collect/Pick/Edit/Export):**
        - ``pick`` — decided / **captured** (review completeness), from
          ``phase_state`` rows with ``phase='pick'``.
        - ``edit`` — *developed* (items with an ``adjustment`` row) / **picked**.
        - ``export`` — *exported* files (``adjustment.edit_exported``) / **picked**.

        Edit and Export use the day's **picked** keepers as the denominator
        ("among picked"). There is **no ``share`` bucket** — Share is a
        closed-event state, not a phase (spec/66). Collect is derived by the
        caller from day totals.
        """
        conn = self.store.conn
        totals = {
            r["dn"]: r["n"] for r in conn.execute(
                "SELECT day_number AS dn, COUNT(*) AS n FROM visible_item "
                "WHERE provenance='captured' GROUP BY day_number"
            )
        }
        # Picked keepers per day — the denominator for Edit / Export %.
        picked_by_day = {
            r["dn"]: r["n"] for r in conn.execute(
                "SELECT item.day_number AS dn, COUNT(*) AS n "
                "FROM phase_state ps JOIN visible_item item ON item.id = ps.item_id "
                "WHERE ps.phase = 'pick' AND ps.state = 'picked' "
                "GROUP BY item.day_number"
            )
        }
        out: Dict[str, Dict[Optional[int], Dict[str, int]]] = {}

        # ---- Pick: decided / captured (review completeness) ----
        pick_map: Dict[Optional[int], Dict[str, int]] = {}
        for r in conn.execute(
            "SELECT item.day_number AS dn, COUNT(*) AS decided, "
            "SUM(CASE WHEN ps.committed_at IS NOT NULL THEN 1 ELSE 0 END) AS committed, "
            "SUM(CASE WHEN ps.state='picked' THEN 1 ELSE 0 END) AS picked "
            "FROM phase_state ps JOIN visible_item item ON item.id = ps.item_id "
            "WHERE ps.phase = 'pick' "
            "GROUP BY item.day_number"
        ):
            pick_map[r["dn"]] = {
                "total": totals.get(r["dn"], r["decided"]),
                "decided": r["decided"],
                "committed": r["committed"] or 0,
                "picked": r["picked"] or 0,
            }
        for dn, t in totals.items():
            pick_map.setdefault(
                dn, {"total": t, "decided": 0, "committed": 0, "picked": 0})
        out["pick"] = pick_map

        # ---- Edit: edited (off the unedited baseline) / picked ----
        # "Edited" is the non-default predicate (core.edit_status): a look
        # other than Original/Natural, a creative filter, or a crop — NOT
        # merely "an adjustment row exists" (Nelson 2026-06-18). The bare
        # row count lives on as ``developed_count`` for the closed tile.
        from core.edit_status import EDITED_SQL
        edited_by_day = {
            r["dn"]: r["n"] for r in conn.execute(
                "SELECT item.day_number AS dn, COUNT(DISTINCT a.item_id) AS n "
                "FROM adjustment a JOIN visible_item item ON item.id = a.item_id "
                f"WHERE {EDITED_SQL} "
                "GROUP BY item.day_number"
            )
        }
        out["edit"] = {
            dn: {
                "total": picked_by_day.get(dn, 0),
                "decided": edited_by_day.get(dn, 0),
                "committed": edited_by_day.get(dn, 0),
                "picked": edited_by_day.get(dn, 0),
            }
            for dn in set(picked_by_day) | set(edited_by_day)
        }

        # ---- Export: will-export / set-aside / undecided per source ----
        # spec/89 §4.1 + §11.3 (Nelson 2026-06-19 lock) — the three-
        # slice Days List bar is **per source**: each picked keeper
        # contributes exactly ONE tally to the bar. The state is the
        # source's overall cover reading per the user-locked default
        # rule:
        #
        # * **0 ship intents** → ``'skipped'`` (Set aside). Override
        #   with the user's explicit ``phase_state(edit)`` if present.
        # * **1 ship intent** (Mira-edit only OR one third-party
        #   return only) → default ``'picked'`` (Will export). A
        #   single-version cell reads green by default — the user
        #   expressed intent via the edit / return.
        # * **≥2 ship intents** (cluster) → default ``'compare'``
        #   (Undecided). The user has multiple versions to pick from;
        #   the cell defaults to "needs your attention." Member-level
        #   decisions roll up through the cover state machine: any
        #   member in compare → Undecided; all-same → that state;
        #   mix of picked + skipped → Undecided (Mixed is not a bar
        #   bucket so it folds in).
        #
        # The aggregation lives in Python — the SQL fetches the raw
        # per-source signals and the GROUP BY in
        # :func:`_export_source_state` is one branch per case.
        from core.edit_status import EDITED_SQL
        source_rows = conn.execute(
            "WITH picked_keepers AS ( "
            "    SELECT i.id, i.day_number FROM phase_state ps "
            "    JOIN visible_item i ON i.id = ps.item_id "
            "    WHERE ps.phase = 'pick' AND ps.state = 'picked' "
            ") "
            "SELECT pk.id, pk.day_number, "
            "    (SELECT GROUP_CONCAT(COALESCE(l.intent_state, 'picked'), '|') "
            "     FROM lineage l "
            "     WHERE l.source_item_id = pk.id "
            "       AND l.phase = 'edit' "
            "       AND l.export_relpath LIKE 'Exported Media/%') "
            "        AS lineage_states, "
            "    (SELECT 1 FROM adjustment a "
            f"    WHERE a.item_id = pk.id AND {EDITED_SQL}) "
            "        AS has_mira, "
            "    (SELECT state FROM phase_state ps2 "
            "     WHERE ps2.item_id = pk.id AND ps2.phase = 'edit') "
            "        AS ps_edit_state "
            "FROM picked_keepers pk"
        ).fetchall()

        export_by_day: Dict[Optional[int], Dict[str, int]] = {}
        for r in source_rows:
            dn = r["day_number"]
            cell = export_by_day.setdefault(
                dn, {"shipped": 0, "dropped": 0, "undecided": 0})
            state = self._export_source_state(
                lineage_states_raw=r["lineage_states"],
                has_mira=bool(r["has_mira"]),
                ps_edit_state=r["ps_edit_state"],
            )
            if state == "picked":
                cell["shipped"] += 1
            elif state == "skipped":
                cell["dropped"] += 1
            else:
                cell["undecided"] += 1

        out["export"] = {}
        for dn in set(picked_by_day) | set(export_by_day):
            cell = export_by_day.get(
                dn, {"shipped": 0, "dropped": 0, "undecided": 0})
            shipped = cell["shipped"]
            dropped = cell["dropped"]
            undecided = cell["undecided"]
            total = shipped + dropped + undecided
            out["export"][dn] = {
                "total": total,
                "shipped": shipped,
                "dropped": dropped,
                "undecided": undecided,
                # Backwards-compatible legacy fields read by the
                # event-card status heuristic: 'decided' = the count of
                # sources the user has implicitly or explicitly
                # committed one way or another.
                "decided": shipped + dropped,
                "committed": shipped,
                "picked": shipped,
            }
        return out

    @staticmethod
    def _export_source_state(
        *,
        lineage_states_raw: Optional[str],
        has_mira: bool,
        ps_edit_state: Optional[str],
    ) -> str:
        """spec/89 §11.3 (Nelson 2026-06-19 lock) — derive ONE bar
        state per source from its ship intents. Returns
        ``'picked'`` (Will export) / ``'skipped'`` (Set aside) /
        ``'compare'`` (Undecided).

        Rules:
        * 0 intents → ``ps_edit_state`` if set, else ``'skipped'``.
        * 1 intent → the intent's state if set; default ``'picked'``.
          (Lineage rows carry the Python-model default 'picked';
          Mira intents fall back to ``ps_edit_state`` or 'picked'.)
        * ≥2 intents (cluster) → cover-state machine:
            * Any member in 'compare'/'candidate' → Undecided.
            * All 'picked' → Will export.
            * All 'skipped' → Set aside.
            * Mixed picked + skipped → Undecided (Mixed isn't a bar
              bucket; it folds into Undecided per the user-locked
              cluster rule)."""
        lineage_states = (
            [s for s in (lineage_states_raw or "").split("|") if s]
            if lineage_states_raw else [])
        lineage_count = len(lineage_states)
        intent_count = lineage_count + (1 if has_mira else 0)
        if intent_count == 0:
            return ps_edit_state or "skipped"
        if intent_count == 1:
            if lineage_count:
                # Lineage row's intent_state IS the source's state.
                return lineage_states[0] or "picked"
            # Mira-only intent: phase_state(edit) overrides; default
            # picked per the user rule.
            return ps_edit_state or "picked"
        # ≥2 intents — cluster. Aggregate via the cover state machine.
        member_states = list(lineage_states)
        if has_mira:
            # The Mira member's compare default kicks in only when
            # the user hasn't expressed a decision via ps_edit.
            member_states.append(ps_edit_state or "compare")
        if any(s in ("compare", "candidate") for s in member_states):
            return "compare"
        if all(s == "picked" for s in member_states):
            return "picked"
        if all(s == "skipped" for s in member_states):
            return "skipped"
        # Mixed picked + skipped → Undecided (per the user-locked
        # rule; Mixed is not a bar bucket).
        return "compare"

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

    def items_with_mira_intent(self) -> set:
        """spec/89 Slice 5+ (Nelson 2026-06-19) — item ids whose
        ``adjustment`` row carries a non-default look / filter / crop /
        rotation AND whose Mira render has not yet been materialised.
        These count as **virtual Mira-render versions** so the versions
        cluster surface can present them alongside third-party returns
        BEFORE the JPEG lands. Once a ``mira_render`` lineage row
        exists for the source, the intent has been fulfilled by that
        file — it stops being a virtual member and only the on-disk
        row counts (Nelson 2026-07-02: pre-fix, every exported item
        with a non-default adjustment kept firing the intent forever,
        so the same item showed as a 2-version cluster {virtual Mira
        + on-disk Mira render} for identical pixels). Staleness of the
        on-disk render is handled by the "Adjustments changed" chip
        in the preview viewer, not by manufacturing a ghost cluster.
        """
        from core.edit_status import EDITED_SQL
        rows = self.store.conn.execute(
            "SELECT a.item_id FROM adjustment a "
            f"WHERE {EDITED_SQL} "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM lineage l "
            "  WHERE l.source_item_id = a.item_id "
            "    AND l.phase = 'edit' "
            "    AND l.provenance = 'mira_render' "
            "    AND l.export_relpath LIKE 'Exported Media/%'"
            ")"
        ).fetchall()
        return {r["item_id"] for r in rows}

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

    def edited_count(self) -> int:
        """Items whose adjustment is **off the unedited baseline** — a look
        other than Original/Natural, a creative filter, or a crop
        (``core.edit_status.EDITED_SQL``). This is the Edit-metric numerator
        the events-tile donut and the Days-Lists Edit rows read as
        *edited ÷ picked* (Nelson 2026-06-18) — strictly ``<=`` the bare
        ``len(adjustments())`` developed-row count."""
        from core.edit_status import EDITED_SQL
        row = self.store.conn.execute(
            f"SELECT COUNT(*) AS n FROM adjustment a WHERE {EDITED_SQL}"
        ).fetchone()
        return int(row["n"]) if row else 0

    def exported_item_ids(self) -> set:
        """Item ids with at least one **SHIPPED** lineage row — items
        that made it through the spec/66 §1.1 Export phase (the Export
        surface's ship set materialised under ``Exported Media/``).

        Under **spec/72 Model B** (spec/89 §1.5) this includes
        third-party returns: the scanner hardlinks each new
        ``Edited Media/`` file straight into ``Exported Media/`` on
        scan, so a return enters the ship set immediately rather than
        waiting for the Export run.

        Deliberately NOT ``Adjustment.edit_exported`` — that flag is
        freshness (reset on every adjustment change) and keeps its chip.
        ``source_kind='bracket'`` rows (stack exports) are out of scope:
        the watermark is per-photo (spec/59 §8)."""
        rows = self.store.conn.execute(
            "SELECT DISTINCT source_item_id FROM lineage "
            "WHERE phase = 'edit' AND source_item_id IS NOT NULL "
            "AND export_relpath LIKE 'Exported Media/%'"
        ).fetchall()
        return {r["source_item_id"] for r in rows}

    def exported_item_ids_with_video_parents(self) -> set:
        """:meth:`exported_item_ids` unioned with the parent source
        video id of every shipped clip / snapshot — spec/89 §4.1's
        "one source video = one keeper unit, shipped if at least one
        of its segments / snapshots shipped" rule projected onto the
        ``exported`` badge (Nelson 2026-07-02).

        Pre-fix, only child ids landed in the shipped set, so a video
        cell whose only ship was a clip render never lit up the
        Export watermark or the "Has file" destructive chip; photos
        got both, videos got neither, which read as inconsistent to
        the user. Videos also count as keeper units at the ship
        level, so their cells should carry the same visual signal.
        The extra ``item.parent_item_id`` JOIN scans only shipped
        rows (already tight), so the cost is negligible.
        """
        rows = self.store.conn.execute(
            "SELECT DISTINCT source_item_id, parent_item_id "
            "FROM lineage "
            "LEFT JOIN item ON item.id = lineage.source_item_id "
            "WHERE lineage.phase = 'edit' "
            "AND lineage.source_item_id IS NOT NULL "
            "AND lineage.export_relpath LIKE 'Exported Media/%'"
        ).fetchall()
        out: set = set()
        for r in rows:
            if r["source_item_id"]:
                out.add(r["source_item_id"])
            if r["parent_item_id"]:
                out.add(r["parent_item_id"])
        return out

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
        **shipped** lineage row in chronological show order. spec/66
        §1.2: only rows under ``Exported Media/`` count — third-party
        returns sitting in ``Edited Media/`` are inbox candidates, not
        the ship set. Never stored, never stale; computed from lineage
        on demand. Item-sourced rows read through ``visible_item`` (a
        hidden day's files drop out of the universe); bracket-sourced
        rows pass (their day rides the merged output item)."""
        sql = (
            "SELECT l.* FROM lineage l "
            + self._CUT_SOURCE_JOIN +
            "WHERE l.phase = 'edit' "
            "AND l.export_relpath LIKE 'Exported Media/%' "
            "AND (l.source_kind = 'bracket' OR si.id IS NOT NULL) "
            + self._CUT_SHOW_ORDER
        )
        return self.store.query_raw(m.Lineage, sql)

    def exported_edited_files(self) -> List[m.Lineage]:
        """Strict twin of :meth:`exported_files` restricted to exports
        whose source item carries a **creatively-edited** adjustment
        (per :data:`core.edit_status.EDITED_SQL` — off the unedited
        baseline: a non-Original look, a creative filter, a crop or
        rotation, a non-Original aspect).

        The ambient photo surfaces (startup splash, closed-event tile
        :class:`PhotoCycler`) prefer this over :meth:`exported_files`
        so the picked frame is one the user actually **developed** —
        not a straight-through export of the untouched baseline.
        Row shape and ordering match :meth:`exported_files` (item-
        sourced rows go through ``visible_item``; bracket-sourced rows
        join the adjustment via the merged output item).
        """
        from core.edit_status import EDITED_SQL
        sql = (
            "SELECT l.* FROM lineage l "
            + self._CUT_SOURCE_JOIN +
            "JOIN adjustment a "
            "  ON a.item_id = COALESCE(l.source_item_id, oi.id) "
            "WHERE l.phase = 'edit' "
            "AND l.export_relpath LIKE 'Exported Media/%' "
            "AND (l.source_kind = 'bracket' OR si.id IS NOT NULL) "
            f"AND {EDITED_SQL} "
            + self._CUT_SHOW_ORDER
        )
        return self.store.query_raw(m.Lineage, sql)

    def exported_files_all(self) -> List[m.Lineage]:
        """Lenient twin of :meth:`exported_files`: every shipped
        lineage row under ``Exported Media/``, in capture-time
        chronological order — but WITHOUT the ``visible_item`` filter
        that strips hidden-day sources.

        Used by the Pool detail surface so the on-disk reality of
        ``Exported Media/`` matches the file set the user sees here
        (and the "Exported" watermark in the Export grid, which also
        reads lineage directly via :meth:`exported_item_ids`). Cuts
        / pool algebra keep using the strict :meth:`exported_files`
        — those care about the visible universe.

        Nelson 2026-06-15: "I open the exported pool and there is
        nothing — but there are several items with the exported tag
        in the Export grid". Diagnosed: ``exported_files`` was
        dropping rows because the source items' day was hidden (or
        the item / day didn't pass the ``visible_item`` view); the
        watermark query never filtered those out, so the two views
        diverged. The Pool now mirrors the watermark.

        Nelson 2026-07-02: reordered from ``exported_at`` (ship time)
        to ``capture_time_corrected`` (shot time) so the DC detail
        grid reads strictly chronologically — a user who exported
        Day 5 before Day 3 was seeing Day 5 first. Item-sourced rows
        read ``item.capture_time_corrected`` (lenient JOIN — plain
        ``item``, not ``visible_item``); bracket-sourced rows read
        the merged output item's timestamp. Ship time stays as a
        tie-breaker for pre-timestamp / clock-corrected-at-import
        rows.
        """
        sql = (
            "SELECT l.* FROM lineage l "
            "LEFT JOIN item si ON si.id = l.source_item_id "
            "LEFT JOIN stack_bracket sb "
            "  ON sb.bracket_id = l.source_bracket_id "
            "LEFT JOIN item oi ON oi.id = sb.output_item_id "
            "WHERE l.phase = 'edit' "
            "AND l.export_relpath LIKE 'Exported Media/%' "
            "ORDER BY COALESCE("
            "si.capture_time_corrected, "
            "oi.capture_time_corrected, "
            "l.exported_at, ''), l.export_relpath"
        )
        return self.store.query_raw(m.Lineage, sql)

    def cuts(self) -> List[m.Cut]:
        """All user Cut definitions, oldest first (the list page's order).
        The built-in #exported is NOT here — it is :meth:`exported_files`."""
        return self.store.query_raw(
            m.Cut, "SELECT * FROM cut ORDER BY created_at, id")

    def cut(self, cut_id: str) -> Optional[m.Cut]:
        return self.store.get(m.Cut, cut_id)

    def cuts_containing(
        self, export_relpath: str,
    ) -> List[m.Cut]:
        """Every Cut whose ``cut_member`` set includes ``export_relpath``,
        in the same order :meth:`cuts` returns (oldest first).

        Powers the Pool's "Delete exported" cascade-aware confirm: the
        on-disk file is regenerable, but deleting it drops the file
        from every Cut that referenced it (spec/61 §1.4 — the
        ``cut_member.export_relpath`` FK carries ``ON DELETE
        CASCADE``). The confirm reads the cut count out loud so the
        user knows the blast radius BEFORE clicking Delete.

        Returns ``[]`` when the relpath isn't in any Cut.
        """
        rel = str(export_relpath).replace("\\", "/")
        rows = self.store.conn.execute(
            "SELECT DISTINCT cut_id FROM cut_member "
            "WHERE export_relpath = ?", (rel,),
        ).fetchall()
        if not rows:
            return []
        ids = {r["cut_id"] for r in rows}
        return [c for c in self.cuts() if c.id in ids]

    def cuts_containing_any(
        self, export_relpaths: Iterable[str],
    ) -> List[m.Cut]:
        """The :meth:`cuts_containing` variant for a batch: every Cut
        that references AT LEAST ONE of ``export_relpaths``. Used by
        the Pool's batch-delete confirm to read the unique Cut count.

        One ``IN (?, ?, ...)`` query — cheaper than N
        :meth:`cuts_containing` calls when the user has bulk-selected
        across many files. Empty input → empty list.
        """
        rels = [str(p).replace("\\", "/") for p in export_relpaths]
        if not rels:
            return []
        placeholders = ",".join("?" for _ in rels)
        rows = self.store.conn.execute(
            "SELECT DISTINCT cut_id FROM cut_member "
            f"WHERE export_relpath IN ({placeholders})",
            tuple(rels),
        ).fetchall()
        if not rows:
            return []
        ids = {r["cut_id"] for r in rows}
        return [c for c in self.cuts() if c.id in ids]

    def cut_by_tag(self, tag: str) -> Optional[m.Cut]:
        rows = self.store.query_by(m.Cut, tag=tag)
        return rows[0] if rows else None

    # ----- dynamic collections (spec/81 — the live-query DC noun) ---------- #

    def dynamic_collections(self) -> List[m.DynamicCollection]:
        """All Dynamic Collections, oldest first. The base #exported universe
        is NOT a row — it is :meth:`exported_files` (operand token "exported").

        spec/162 Round 3f — load-bearing after the user-saved Collection
        UI retired: the resolver's cycle-guard + the ShareCutsPage
        operand-picker inventory + the cut-binding placement classifier
        walk this list. Method survives; no user-facing writer creates
        rows any more."""
        return self.store.query_raw(
            m.DynamicCollection,
            "SELECT * FROM dynamic_collection ORDER BY created_at, id")

    def dynamic_collection(self, dc_id: str) -> Optional[m.DynamicCollection]:
        return self.store.get(m.DynamicCollection, dc_id)

    def dc_by_tag(self, tag: str) -> Optional[m.DynamicCollection]:
        rows = self.store.query_by(m.DynamicCollection, tag=tag)
        return rows[0] if rows else None

    @staticmethod
    def dc_expr(dc: m.DynamicCollection) -> List[list]:
        """A DC's formula as ``[[op, operand], …]`` — the operand is the base
        token ``"exported"`` or a typed ref ``{"kind","id","tag"}``.

        spec/162 Round 3f — load-bearing after the user-saved Collection
        UI retired: the resolver + :class:`CutSession` / :class:`Cross
        EventCutSession` read it when re-resolving a Cut's live DC."""
        return list(json.loads(dc.expr_json))

    @staticmethod
    def dc_filters(dc: m.DynamicCollection) -> dict:
        """A DC's filters mapping (``{"styles":[…],"media_type":…}``); readers
        tolerate missing keys.

        spec/162 Round 3f — load-bearing after the user-saved Collection
        UI retired: same callers as :meth:`dc_expr`."""
        try:
            data = json.loads(dc.filters_json)
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}

    def _check_dc_cycle(self, dc_id: str, expr: Sequence[Sequence]) -> None:
        """Cheap, non-resolving cycle guard at the write seam (spec/81 §2):
        reject a DC whose operand graph reaches its own id. Cut + base operands
        are terminal. Raises ``ValueError("cycle")`` for a ``tr()``-able UI."""
        by_id: Dict[str, list] = {}
        for d in self.dynamic_collections():
            if d.id == dc_id:
                continue
            by_id[d.id] = self.dc_expr(d)
        if collection_resolver.reaches(
                dc_id, [list(t) for t in expr],
                dc_expr_by_id=lambda i: by_id.get(i)):
            raise ValueError("cycle")

    def create_dc(
        self,
        name: str,
        *,
        expr: Sequence[Sequence] = (),
        styles: Sequence[str] = (),
        media_type: str = "both",
    ) -> m.DynamicCollection:
        """Create a DC from a user-typed name (slugified + validated against the
        DC namespace only — separate from Cut tags, Nelson 2026-06-16). Rejects
        a self-referential operand graph (cycle guard). Filters fold into
        ``filters_json``.

        spec/162 Round 3f — no user-facing UI writes DC rows any more
        (Round 2b retired the Save-as-Collection dialog). The write-side
        API stays exposed for the gateway's test coverage
        (:mod:`tests.test_gateway_cuts` +
        :mod:`tests.test_cuts_shell`) so a future re-integration can
        wire a new writer without reviving retired code."""
        slug = cut_names.slugify(name)
        err = cut_names.check_tag(slug, [d.tag for d in self.dynamic_collections()])
        if err:
            raise ValueError(err)
        dc_id = self._new_id()
        expr_list = [list(t) for t in expr]
        self._check_dc_cycle(dc_id, expr_list)
        now = self._now()
        dc = m.DynamicCollection(
            id=dc_id, tag=slug, created_at=now, updated_at=now,
            expr_json=json.dumps(expr_list),
            filters_json=json.dumps(
                {"styles": list(styles), "media_type": media_type}),
        )
        with self.store.transaction():
            self.store.upsert(dc)
            self._touch()
        return dc

    def update_dc(
        self,
        dc_id: str,
        *,
        expr: Optional[Sequence[Sequence]] = None,
        styles: Optional[Sequence[str]] = None,
        media_type: Optional[str] = None,
    ) -> None:
        """Edit a DC's formula / filters in place (the live recipe re-resolves
        next read; pinned Cuts are frozen and unaffected — spec/81 §5). The
        cycle guard runs against the NEW expr.

        spec/162 Round 3f — no user-facing UI writes to DC rows any
        more; test-coverage only."""
        dc = self.dynamic_collection(dc_id)
        if dc is None:
            raise KeyError(dc_id)
        sets: Dict[str, str] = {}
        if expr is not None:
            expr_list = [list(t) for t in expr]
            self._check_dc_cycle(dc_id, expr_list)
            sets["expr_json"] = json.dumps(expr_list)
        if styles is not None or media_type is not None:
            filters = self.dc_filters(dc)
            if styles is not None:
                filters["styles"] = list(styles)
            if media_type is not None:
                filters["media_type"] = media_type
            sets["filters_json"] = json.dumps(filters)
        if not sets:
            return
        cols = ", ".join(f"{k} = ?" for k in sets)
        with self.store.transaction() as conn:
            conn.execute(
                f"UPDATE dynamic_collection SET {cols}, updated_at = ? WHERE id = ?",
                (*sets.values(), self._now(), dc_id))
            self._touch()

    def rename_dc(self, dc_id: str, new_name: str) -> m.DynamicCollection:
        """Rename a DC (slugify + validate against the DC namespace, excluding
        itself). Pinned Cuts keep their frozen snapshot.

        spec/162 Round 3f — no user-facing UI writes to DC rows any
        more; test-coverage only."""
        dc = self.dynamic_collection(dc_id)
        if dc is None:
            raise KeyError(dc_id)
        slug = cut_names.slugify(new_name)
        err = cut_names.check_tag(
            slug, [d.tag for d in self.dynamic_collections() if d.id != dc_id])
        if err:
            raise ValueError(err)
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE dynamic_collection SET tag = ?, updated_at = ? WHERE id = ?",
                (slug, self._now(), dc_id))
            self._touch()
        return replace(dc, tag=slug)

    def delete_dc(self, dc_id: str) -> None:
        """Drop a DC. Pinned Cuts survive — the freeze invariant (spec/81
        §5) — but their ``source_dc_id`` is NULLed here at the gateway
        level. Schema v8 (spec/81 Phase 2) dropped the FK that used to
        carry ON DELETE SET NULL; the equivalent guarantee now lives in
        this method. Members are untouched.

        spec/162 Round 3f — no user-facing UI writes to DC rows any
        more; test-coverage only."""
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE cut SET source_dc_id = NULL, source_dc_kind = NULL "
                "WHERE source_dc_id = ? AND (source_dc_kind = 'event' "
                "OR source_dc_kind IS NULL)",
                (dc_id,))
            conn.execute("DELETE FROM dynamic_collection WHERE id = ?", (dc_id,))
            self._touch()

    def dc_operand_inventory(self) -> List[dict]:
        """The operands the New Cut dialog offers (spec/81 §2 + spec/93
        §6): the base universe ``#exported``, then every Collection
        offered in this event (GLOBAL ∪ BOUND-to-E), then every Cut.

        spec/94 Phase 2 — the Collections set includes:

        1. **Bound** DCs from ``event.db.dynamic_collection`` (oldest first).
        2. **Global** Collections from the file-based library (spec/93 §4)
           — every JSON file under ``<library_root>/Collections/`` whose
           id isn't already a bound row. Cross-bound Collections appear
           here too; the resolver handles the empty-resolution case when
           the current event isn't in their bound set.

        An id that appears in BOTH stores resolves to the bound row only
        (event.db wins) so the dialog never shows two chips for what the
        resolver treats as one definition.
        """
        inv: List[dict] = [{"kind": "base", "tag": cut_names.EXPORTED_TAG,
                            "operand": cut_names.EXPORTED_TAG}]
        seen_ids: set = set()
        for d in self.dynamic_collections():
            seen_ids.add(d.id)
            inv.append({"kind": "dc", "tag": d.tag,
                        "operand": {"kind": "dc", "id": d.id, "tag": d.tag}})
        # spec/93 §6 fallback to GLOBAL Collections. The cached library
        # snapshot is the same one the resolver uses (built lazily, one
        # scan per open_event lifetime).
        snapshot = self._collections_library_snapshot()
        if snapshot is not None:
            library_by_id, by_name = snapshot
            # Build a reverse map from payload-id to display name so we
            # can attach a human-readable tag to each library chip.
            name_by_payload_id = {
                id(payload): name for name, payload in by_name.items()
            }
            for lib_id, payload in library_by_id.items():
                if lib_id in seen_ids:
                    continue
                tag = name_by_payload_id.get(id(payload), lib_id)
                inv.append({"kind": "dc", "tag": tag,
                            "operand": {"kind": "dc", "id": lib_id,
                                        "tag": tag}})
        for c in self.cuts():
            inv.append({"kind": "cut", "tag": c.tag,
                        "operand": {"kind": "cut", "id": c.id, "tag": c.tag}})
        return inv

    # ----- DC resolution (spec/81 §2 — pure engine in core/) --------------- #

    def _operand_base_universe(self, token: str) -> set:
        """The base-universe member set for an operand token. ``"exported"`` =
        the live #exported relpaths; any other token = empty (event scope has
        one base universe — the ladder rungs are cross-event, Task D)."""
        if token == cut_names.EXPORTED_TAG:
            return {ln.export_relpath for ln in self.exported_files()}
        return set()

    def _operand_dc(self, ref) -> Optional["collection_resolver.DCExpr"]:
        """Resolve a ``{"kind":"dc","id"|"tag":…}`` operand to a
        :class:`~core.collection_resolver.DCExpr`, or None when it is gone.

        Resolution order (spec/93 §6 GLOBAL ∪ BOUND-to-E):

        1. ``event.db.dynamic_collection`` — bound-to-this-event Collections.
        2. The file-based library (spec/93 §4) via
           :meth:`_resolve_library_collection` when wired by the umbrella
           Gateway. The library tree is scanned at most once per
           ``open_event()`` lifetime — see
           :meth:`_collections_library_snapshot`.
        """
        dc = None
        if ref.get("id"):
            dc = self.dynamic_collection(ref["id"])
        if dc is None and ref.get("tag"):
            dc = self.dc_by_tag(ref["tag"])
        if dc is not None:
            return collection_resolver.DCExpr(
                id=dc.id, expr=self.dc_expr(dc), filters=self.dc_filters(dc))
        return self._resolve_library_collection(ref)

    def _collections_library_snapshot(
        self,
    ) -> Optional[Tuple[Dict[str, dict], Dict[str, dict]]]:
        """Cached ``(by_id, by_name)`` snapshot of the file-based Collection
        library. spec/94 Phase 2 — built once per ``open_event()`` lifetime
        so a single resolution pass (which may walk many nested operands)
        scans the JSON tree only once.

        Returns ``None`` when no factory was wired (legacy callers, ad-hoc
        :func:`EventGateway.open` from tests). The DefinitionLibrary's
        own reconcile-on-scan keeps the snapshot reasonably fresh per
        session; an event re-open rebuilds the cache.
        """
        if self._collections_library_factory is None:
            return None
        if self._collections_library_cache is None:
            self._collections_library_cache = (
                self._collections_library_factory())
        return self._collections_library_cache

    def _resolve_library_collection(
        self, ref: Mapping,
    ) -> Optional["collection_resolver.DCExpr"]:
        """Lookup a Collection operand against the file-based library.

        References are ``{id, name|tag}`` per spec/93 §4 — id first, name
        fallback (handles hand-authored files where the saver didn't write
        the id). The library snapshot is the cache built by
        :meth:`_collections_library_snapshot`.
        """
        snapshot = self._collections_library_snapshot()
        if snapshot is None:
            return None
        by_id, by_name = snapshot
        payload: Optional[dict] = None
        ref_id = ref.get("id") or ""
        ref_name = ref.get("tag") or ref.get("name") or ""
        if ref_id:
            payload = by_id.get(ref_id)
        if payload is None and ref_name:
            payload = by_name.get(ref_name)
        if payload is None:
            return None
        # Carry the file's id forward as the DCExpr id so the resolver's
        # memoisation key is stable. Hand-authored files without an id are
        # resolved by name; we fabricate a "library:<name>" stub so the
        # memo dict doesn't collide with other anonymous operands.
        return collection_resolver.DCExpr(
            id=ref_id or f"library:{ref_name}",
            expr=list(payload.get("expr") or []),
            filters=dict(payload.get("filters") or {}),
        )

    def _operand_cut_members(self, ref) -> set:
        """The frozen member set of a ``{"kind":"cut",…}`` operand (terminal —
        a Cut never re-queries its DC)."""
        cut = None
        if ref.get("id"):
            cut = self.cut(ref["id"])
        if cut is None and ref.get("tag"):
            cut = self.cut_by_tag(ref["tag"])
        if cut is None:
            return set()
        return {cm.export_relpath
                for cm in self.store.query_by(m.CutMember, cut_id=cut.id)}

    def _apply_dc_filters(self, keys, filters) -> List[str]:
        """Narrow + chronologically order a member-key set against a DC's
        filters (Style + media type at event scope). Returns export relpaths in
        show order — the resolver hands these back as the DC's resolution."""
        if not keys:
            return []
        styles = list(filters.get("styles") or [])
        media = filters.get("media_type", "both") or "both"
        rows = self._lineage_show_rows(
            keys, style_filter=styles, type_filter=media)
        return [ln.export_relpath for ln in rows]

    def resolve_dc(
        self,
        expr: Sequence[Sequence],
        filters: Optional[Mapping] = None,
    ) -> List[m.Lineage]:
        """Resolve a DC formula (spec/81 §2): left-to-right set algebra over
        operands (``+``/``-``/``&``), operands resolving recursively (nested
        DC) or terminally (base ``#exported`` / a frozen Cut), then the DC's
        filters (Style + media type). Returns lineage rows in chronological
        show order. Cycle-safe + memoised within the pass."""
        ordered_keys = collection_resolver.resolve(
            [list(t) for t in expr],
            dict(filters or {}),
            base_universe=self._operand_base_universe,
            dc_by_ref=self._operand_dc,
            cut_members=self._operand_cut_members,
            apply_filters=self._apply_dc_filters,
        )
        if not ordered_keys:
            return []
        # _apply_dc_filters already ordered + filtered the top-level set; map
        # the keys back to lineage rows in that exact order.
        by_rel = {ln.export_relpath: ln
                  for ln in self._lineage_show_rows(set(ordered_keys))}
        return [by_rel[k] for k in ordered_keys if k in by_rel]

    def dc_probe(self, expr: Sequence[Sequence],
                 filters: Optional[Mapping] = None) -> int:
        """The dialog's live count for a draft DC formula (spec/81 §2) — how
        many files this expr+filters resolves to right now.

        spec/162 Round 3f — load-bearing: :class:`ShareCutsPage`'s
        operand-picker inventory computes the live count for every DC
        chip via this call so the user sees the resolution size before
        pinning."""
        return len(self.resolve_dc(expr, filters))

    # ----- Recipe resolution (spec/90 §7 Phase 2) -------------------------- #

    def _operand_person_members(self, person_id: str) -> Optional[set]:
        """Resolve a Person id to the set of export relpaths where they
        appear (spec/90 §4.3 + §5.2). Joins ``face`` to ``lineage`` on the
        source item — the SQL ``SELECT lineage.export_relpath FROM face
        JOIN lineage ...`` shape.

        Returns ``None`` when the Person id doesn't appear in this event's
        ``face`` table AND no row exists in ``photo_person`` either — the
        Phase-1 substrate has no people management UI, so the only way
        to know "this id is real" is to see it on a row. ``None`` triggers
        the strict-reference miss path (spec/90 §1.4); an empty set means
        "exists, no detections" (lenient — Phase 1 ships with face empty).

        The ``photo_person`` fallback is the load-bearing seam to the
        user-level catalog: a Person known via ``photo_person`` (manual
        tagging — spec/53 §2.5) counts as "exists, no faces detected
        yet", which is the §4.3 correct behaviour pending the recognition
        pipeline (spec/90 §7 Phase 6).
        """
        # Items where this Person is detected via the face table.
        face_rows = self.store.conn.execute(
            "SELECT DISTINCT l.export_relpath "
            "FROM face f "
            "JOIN lineage l ON l.source_item_id = f.item_id "
            "WHERE f.person_id = ? "
            "AND l.phase = 'edit' "
            "AND l.export_relpath LIKE 'Exported Media/%'",
            (person_id,),
        ).fetchall()
        if face_rows:
            return {r["export_relpath"] for r in face_rows}
        # Existence fallback: a row in photo_person proves the id is real
        # (user-tagged, no auto detection yet → lenient empty set).
        exists = self.store.conn.execute(
            "SELECT 1 FROM photo_person WHERE person_id = ? LIMIT 1",
            (person_id,),
        ).fetchone()
        if exists is not None:
            return set()
        return None

    def _check_recipe_operand(self, operand: Mapping) -> None:
        """Strict-reference guard for one named operand in a Recipe expression
        (spec/90 §1.4). Raises :class:`recipe_resolver.RecipeResolutionError`
        when the operand's referent is gone. Event-scope ignores ``event`` /
        ``event_collection`` operands — those are scope-level, only meaningful
        on the cross-event face; for an event-Cut Recipe they shouldn't
        appear, but if they do we let them pass (the LibraryGateway face
        does the real check).

        DC operands resolve against event.db FIRST, then the file-based
        library (spec/93 §6 GLOBAL ∪ BOUND-to-E)."""
        kind = operand.get("kind")
        if kind == "dc":
            dc = None
            if operand.get("id"):
                dc = self.dynamic_collection(operand["id"])
            if dc is None and operand.get("tag"):
                dc = self.dc_by_tag(operand["tag"])
            if dc is None and self._resolve_library_collection(operand) is None:
                raise recipe_resolver.RecipeResolutionError(
                    operand.get("tag") or operand.get("id") or "",
                    kind="dc",
                )
        elif kind == "cut":
            cut = None
            if operand.get("id"):
                cut = self.cut(operand["id"])
            if cut is None and operand.get("tag"):
                cut = self.cut_by_tag(operand["tag"])
            if cut is None:
                raise recipe_resolver.RecipeResolutionError(
                    operand.get("tag") or operand.get("id") or "",
                    kind="cut",
                )
        elif kind == recipe_resolver.PERSON_KIND:
            pid = operand.get("id")
            if not pid or self._operand_person_members(pid) is None:
                raise recipe_resolver.RecipeResolutionError(
                    pid or "", kind=recipe_resolver.PERSON_KIND,
                )

    def _recipe_dc_expr_by_ref(
        self, operand: Mapping,
    ) -> Optional[list]:
        """Live DC expression lookup for the strict walk's transitive
        recursion. Returns ``None`` when the DC is gone (the operand
        validator already raised on it; this returning ``None`` just
        stops the walk for that branch).

        Phase 2 spec/94 — falls through to the file-based library when
        event.db doesn't carry the id/tag (spec/93 §6 load set)."""
        dc = None
        if operand.get("id"):
            dc = self.dynamic_collection(operand["id"])
        if dc is None and operand.get("tag"):
            dc = self.dc_by_tag(operand["tag"])
        if dc is not None:
            return self.dc_expr(dc)
        # File-library fallback. The DCExpr's ``expr`` is exactly the
        # formula stored in the JSON payload.
        library_dc = self._resolve_library_collection(operand)
        if library_dc is None:
            return None
        return list(library_dc.expr)

    def _operand_person_for_predicate(
        self, operand: Mapping,
    ) -> Optional[set]:
        """``extra_operand`` adapter for :func:`core.collection_resolver.resolve`
        so Person chips work inside rule predicates (spec/90 §4.3 advanced).
        The strict-walk has already validated the id before resolution
        starts; if for some reason it slipped through and the id is gone,
        we return ``set()`` (graceful — same shape as the dc / cut paths
        when a ref is missing). ``None`` from the underlying accessor maps
        to ``set()`` here, NOT to "unknown" — the resolver's empty-set
        contract is honoured."""
        kind = operand.get("kind")
        if kind != recipe_resolver.PERSON_KIND:
            return None                                    # not ours; resolver falls through
        pid = operand.get("id") or ""
        members = self._operand_person_members(pid)
        return set(members) if members is not None else set()

    def resolve_recipe(
        self,
        composition: Mapping,
        *,
        scope: Optional[Sequence[str]] = None,   # event uuids — Cut flavour ignores
    ) -> recipe_resolver.RecipeResolution:
        """Evaluate a Recipe ``composition`` against this event (spec/90 §7
        Phase 2). Returns the ordered pool of export relpaths plus a
        per-relpath ``initially_picked`` seed map.

        Event scope is Cut-flavoured (spec/90 §2.1) — the ``scope`` parameter
        is accepted for API parity with :meth:`LibraryGateway.resolve_recipe`
        but ignored here (the event is implicit). The composition's ``scope``
        section is walked for strict-reference checks regardless, so a
        cross-pollinated Recipe (a Collection Recipe applied to a Cut, spec/90
        §5.5) still surfaces missing-operand errors before any resolution.

        Raises :class:`recipe_resolver.RecipeResolutionError` if any named
        operand (DC / Cut / Person) is missing — the dialog catches and
        reports. Style / Media / Camera / Lens filters resolve leniently to
        empty; the empty pool is reported as ``RecipeResolution(pool=[],
        seed={})``."""
        del scope                                          # event scope is implicit

        def _resolve_pool(expr, filters):
            rows = self.resolve_dc(expr, filters)
            return [ln.export_relpath for ln in rows]

        def _resolve_predicate_keys(predicate_expr):
            keys = collection_resolver.resolve(
                [list(t) for t in predicate_expr],
                {},                                        # rule predicates: no top-level filters
                base_universe=self._operand_base_universe,
                dc_by_ref=self._operand_dc,
                cut_members=self._operand_cut_members,
                apply_filters=lambda ks, _f: list(ks),
                extra_operand=self._operand_person_for_predicate,
            )
            return set(keys)

        return recipe_resolver.resolve_recipe(
            composition,
            resolve_pool=_resolve_pool,
            resolve_predicate_keys=_resolve_predicate_keys,
            person_members=self._operand_person_members,
            validate_named_operand=self._check_recipe_operand,
            dc_expr_by_ref=self._recipe_dc_expr_by_ref,
        )

    @staticmethod
    def cut_expr_snapshot(cut: m.Cut) -> List[list]:
        """The Cut's FROZEN formula as ``[[op, operand], …]`` — the recipe
        resolved at pin time (spec/81 §5). A Cut never re-queries its DC; this
        is for display / reproducibility, not live resolution."""
        return list(json.loads(cut.expr_snapshot_json))

    @staticmethod
    def cut_overlay_fields(cut: m.Cut) -> List[str]:
        """The Cut's selected overlay provenance fields (spec/81 §3.1):
        a subset of ``when`` / ``where`` / ``how1`` / ``how2``; ``[]`` = off."""
        try:
            data = json.loads(cut.overlay_fields_json)
            return [f for f in data if isinstance(f, str)]
        except (ValueError, TypeError):
            return []

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

    def item_provenance(self, item_id: str):
        """spec/134 — resolve a viewer-overlay :class:`FrameProvenance`
        for a live ``item`` (the Picker / Editor source item, not a
        lineage export row).

        Thin wrapper over :func:`core.viewer_overlay.item_to_frame_provenance`
        that handles the day / camera lookups, so the UI doesn't have
        to. Returns an empty ``FrameProvenance`` when the item isn't
        found (the composer omits empty fields, the overlay then
        renders as hidden via ``set_html('')``)."""
        from core import viewer_overlay
        item = self.store.get(m.Item, item_id)
        if item is None:
            return viewer_overlay.FrameProvenance()
        camera_label: Optional[str] = None
        if item.camera_id:
            cam = self.store.get(m.Camera, item.camera_id)
            if cam is not None:
                camera_label = cam.camera_id
        day = None
        if item.day_number is not None:
            day = self.store.get(m.TripDay, item.day_number)
        return viewer_overlay.item_to_frame_provenance(
            item, camera_label=camera_label, day=day)

    def frame_provenance(self, export_relpath: str):
        """Resolve one Cut member's overlay provenance (spec/81 §3.1).

        Joins the lineage row to its source ``item`` (or the merged output
        of its source ``stack_bracket``) plus the trip-day for *where*
        context, and returns a :class:`core.cut_overlay.FrameProvenance`
        the export / Play pipelines consume. Missing source / missing
        facts → empty fields (the formatter omits them gracefully)."""
        from core import cut_overlay
        row = self.store.conn.execute(
            "SELECT l.source_kind, l.source_item_id, l.source_bracket_id, "
            "l.exported_at, b.output_item_id "
            "FROM lineage l "
            "LEFT JOIN stack_bracket b ON b.bracket_id = l.source_bracket_id "
            "WHERE l.export_relpath = ?",
            (export_relpath,),
        ).fetchone()
        if row is None:
            return cut_overlay.FrameProvenance()
        item_id = row["source_item_id"] or row["output_item_id"]
        if not item_id:
            return cut_overlay.FrameProvenance()
        item = self.store.get(m.Item, item_id)
        if item is None:
            return cut_overlay.FrameProvenance()
        when = item.capture_time_corrected or item.capture_time_raw or row["exported_at"]
        camera_label: Optional[str] = None
        if item.camera_id:
            cam = self.store.get(m.Camera, item.camera_id)
            if cam is not None:
                camera_label = cam.camera_id  # 'Make+Model' business key
        city: Optional[str] = None
        country: Optional[str] = None
        if item.day_number is not None:
            day = self.store.get(m.TripDay, item.day_number)
            if day is not None:
                city = day.location or None
                try:
                    country = json.loads(day.extras_json or "{}").get("country")
                except (ValueError, TypeError):
                    country = None
        return cut_overlay.FrameProvenance(
            when=when,
            city=city,
            country=country,
            camera=camera_label,
            lens_model=item.lens_model,
            flash_fired=(None if item.flash_fired is None
                         else bool(item.flash_fired)),
            aperture_f=item.aperture_f,
            shutter_speed_s=item.shutter_speed_s,
            iso=item.iso,
            focal_length_mm=item.focal_length_mm,
        )

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
            # Style chips narrow the PHOTO population only — Style is a
            # photo-shaped bucket (per spec/58 §2 the Style button lives
            # on the Edit photo surface). Videos pass through whatever
            # Style chips are active, otherwise the New Cut dialog's
            # "Videos" checkbox would silently drop every video whose
            # classification is NULL (the common case — classification
            # is unset for most videos). Reported by Nelson 2026-06-19.
            qs2 = ",".join("?" * len(style_filter))
            sql += (
                f"AND (COALESCE(si.classification, oi.classification) "
                f"IN ({qs2}) "
                "OR COALESCE(si.kind, oi.kind, 'photo') = 'video') "
            )
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

    def dc_show_totals(
        self,
        expr: Sequence[Sequence],
        filters: Optional[Mapping] = None,
    ) -> cut_budget.ShowTotals:
        """Budget composition of a DRAFT DC formula (spec/81 §2) — the dialog's
        live counts + budget hint read this before any Cut exists. Same
        semantics as :meth:`cut_show_totals` (separator_count = member days)."""
        rows = self.resolve_dc(expr, filters)
        if not rows:
            return cut_budget.ShowTotals()
        relpaths = [ln.export_relpath for ln in rows]
        qs = ",".join("?" * len(relpaths))
        # spec/144 — for video, prefer ``lineage.duration_ms`` (the clip
        # segment's on-disk length recorded at export) over the source
        # item's whole-video ``duration_ms``. Photos contribute 0; the
        # un-recorded fallback (legacy lineage row) reads as 0 here and
        # the surface readers (cut-play scrubber, recipe probe) ffprobe
        # the file when they need the live truth.
        row = self.store.conn.execute(
            "SELECT "
            "SUM(CASE WHEN COALESCE(si.kind, oi.kind, 'photo') = 'video' "
            "    THEN 0 ELSE 1 END) AS photos, "
            "SUM(CASE WHEN COALESCE(si.kind, oi.kind, 'photo') = 'video' "
            "    THEN 1 ELSE 0 END) AS videos, "
            "SUM(CASE WHEN COALESCE(si.kind, oi.kind, 'photo') = 'video' "
            "    THEN COALESCE(l.duration_ms, 0) ELSE 0 END) AS video_ms, "
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
        count a day.

        spec/144 — for video members the duration sums ``lineage.
        duration_ms`` (the clip-segment's TRUE on-disk length recorded at
        export). Falls back to ``0`` when the column is NULL (legacy
        pre-migration row, or a never-rendered placeholder); the surfaces
        that need the live truth ffprobe the file."""
        row = self.store.conn.execute(
            "SELECT "
            "SUM(CASE WHEN COALESCE(si.kind, oi.kind, 'photo') = 'video' "
            "    THEN 0 ELSE 1 END) AS photos, "
            "SUM(CASE WHEN COALESCE(si.kind, oi.kind, 'photo') = 'video' "
            "    THEN 1 ELSE 0 END) AS videos, "
            "SUM(CASE WHEN COALESCE(si.kind, oi.kind, 'photo') = 'video' "
            "    THEN COALESCE(l.duration_ms, 0) ELSE 0 END) AS video_ms, "
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

    def show_totals_for_export_relpaths(
        self, paths,
    ) -> cut_budget.ShowTotals:
        """spec/152 §3 — project ShowTotals for an ARBITRARY list of
        export relpaths. The New / Adjust Cut dialog uses this to
        compute the metric line BEFORE the membership is committed:
        same JOIN shape as :meth:`cut_show_totals`, but the
        membership comes from the caller (the dialog's resolver
        output) instead of ``cut_member``.

        Empty list returns zeros. Unknown relpaths (the resolver
        sometimes lists items whose lineage row is missing — defensive)
        contribute nothing rather than crashing the projection.
        """
        members = [str(p) for p in (paths or []) if p]
        if not members:
            return cut_budget.ShowTotals()
        placeholders = ",".join("?" * len(members))
        row = self.store.conn.execute(
            "SELECT "
            "SUM(CASE WHEN COALESCE(si.kind, oi.kind, 'photo') = 'video' "
            "    THEN 0 ELSE 1 END) AS photos, "
            "SUM(CASE WHEN COALESCE(si.kind, oi.kind, 'photo') = 'video' "
            "    THEN 1 ELSE 0 END) AS videos, "
            "SUM(CASE WHEN COALESCE(si.kind, oi.kind, 'photo') = 'video' "
            "    THEN COALESCE(l.duration_ms, 0) ELSE 0 END) AS video_ms, "
            "COUNT(DISTINCT COALESCE(si.day_number, oi.day_number)) AS days "
            "FROM lineage l "
            + self._CUT_SOURCE_JOIN +
            f"WHERE l.export_relpath IN ({placeholders})",
            tuple(members),
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

    def stack_producers_by_output(self) -> Dict[str, str]:
        """spec/109 §5 — ``output_item_id → producer`` for every
        adopted stack master. The Export-surface origin-wordmark
        resolver consults this to badge a stack output as ``Mira``
        (in-app Mertens fusion) vs ``ext`` (third-party stacker
        return). Skips rows whose output_item_id is ``NULL`` (the
        bracket lost its master)."""
        return {
            sb.output_item_id: sb.producer
            for sb in self.store.all(m.StackBracket)
            if sb.output_item_id
        }

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
        the WHERE is explicit for clarity.

        Also the **read-only defensive net** (spec/76 §B.1). Every
        gateway mutator funnels through ``_touch()`` while still
        inside the enclosing transaction, so raising here rolls the
        write back atomically — even when a UI surface forgot to gate
        upfront. UI is still expected to consult
        :func:`mira.session.is_read_only` and disable controls before
        the user can trigger the mutator; this guard only fires when
        a surface slipped through.
        """
        from mira.session import ReadOnlyLibraryError, is_read_only
        if is_read_only():
            raise ReadOnlyLibraryError(
                "Library is open read-only — mutation refused. The "
                "writer lock is held by another machine.")
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

    def bulk_set_video_speed(self, speed: float) -> int:
        """spec/146 — set :attr:`VideoAdjustment.speed` = ``speed`` for
        every video item in the event, in one transaction.

        Mirrors the per-clip workshop handler (editor_page.py:2636):
        when a video item has no ``VideoAdjustment`` row yet a default
        one is created with the chosen ``speed``. Non-video items are
        untouched (the writer filters on ``kind == 'video'``).

        The value bakes on the next Export — it flows into
        :func:`core.video_export.build_export_plan` (``speed`` is
        forwarded to ffmpeg's ``setpts`` filter via
        :data:`core.video_export_run`). Already-exported files don't
        change until the user re-exports.

        Returns the count of items whose ``speed`` was set. Raises
        ``ValueError`` for non-positive ``speed`` (the dropdown values
        are 0.5 / 0.75 / 1 / 1.25 / 1.5 / 2; a non-positive value would
        produce a degenerate ``setpts`` filter)."""
        if not isinstance(speed, (int, float)) or speed <= 0:
            raise ValueError(f"speed must be > 0, got {speed!r}")
        speed = float(speed)
        n = 0
        with self.store.transaction():
            for item in self.items(kind="video", include_hidden=True):
                vadj = (self.store.get(m.VideoAdjustment, item.id)
                        or m.VideoAdjustment(item_id=item.id))
                vadj.speed = speed
                self.store.upsert(vadj)
                n += 1
            if n:
                self._touch()
        return n

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

    @staticmethod
    def _iso_plus_ms(iso: "Optional[str]", ms: int) -> "Optional[str]":
        """Offset an ISO timestamp by ``ms`` milliseconds. None-safe; on a
        parse failure returns the input unchanged (never crashes a write)."""
        if not iso:
            return None
        try:
            from datetime import datetime, timedelta
            return (datetime.fromisoformat(iso)
                    + timedelta(milliseconds=int(ms))).isoformat()
        except Exception:                                      # noqa: BLE001
            return iso

    def _restamp_segment_times(self, video_item_id: str) -> None:
        """spec/56 / spec/61 — give every segment item the source video's
        ``day_number`` + a ``capture_time_corrected`` offset by the segment's
        START on the timeline, so exported clips land in their day in
        chronological show order in a Cut (not bunched under the undated
        separator). Re-runs after every marker op because segment starts are
        marker-derived. An undated source video leaves its clips undated too
        (correct). Caller holds the transaction."""
        video = self.item(video_item_id)
        if video is None:
            return
        day = video.day_number
        base = video.capture_time_corrected
        try:
            bounds = (derive_segment_bounds(
                [mk.at_ms for mk in self.video_markers(video_item_id)],
                int(video.duration_ms)) if video.duration_ms else [])
        except Exception:                                      # noqa: BLE001
            bounds = []
        for seg in self.video_segments(video_item_id):
            in_ms = (bounds[seg.seg_index][0]
                     if 0 <= seg.seg_index < len(bounds) else 0)
            item = self.item(seg.item_id)
            if item is None:
                continue
            self.store.upsert(replace(
                item, day_number=day,
                capture_time_corrected=self._iso_plus_ms(base, in_ms)))

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
                self._restamp_segment_times(video_item_id)
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
            self._restamp_segment_times(video_item_id)
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
            self._restamp_segment_times(mk.video_item_id)
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
            self._restamp_segment_times(mk.video_item_id)
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
                day_number=video.day_number,
                capture_time_corrected=self._iso_plus_ms(
                    video.capture_time_corrected, at_ms),
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
        producer: str = "external",
    ) -> str:
        """Adopt a merged stack master: move the source file from a scratch
        path (the ``Picked Media/`` root for the spec/57 external round
        trip, OR a working scratch file for the spec/109 in-app Mertens
        merge) into additive-only ``Original Media/Merged/`` (copy →
        sha-verify → delete source; the captured subtrees beside it stay
        untouchable) and record it as the bracket's FINAL result — a
        ``provenance='stack_output'`` item placed on the bracket's day so
        it sits beside its siblings, plus the
        ``stack_bracket``/``stack_member`` rows and an explicit
        ``phase_state('pick','picked')`` (merging it WAS the pick). The
        caller re-runs the links rebuild afterwards so the master appears
        at the projection root seamlessly (the locked spec/57 rider).

        ``bracket_kind`` is the cache kind (``focus_bracket`` /
        ``exposure_bracket``) or already the stack kind (``focus`` /
        ``exposure``). ``producer`` (spec/109 §5) records who fused
        the master — ``'external'`` (default — third-party stacker) or
        ``'mira'`` (the in-app Mertens job, exposure brackets only);
        drives the consolidation badge's ``Mira`` / ``ext`` wordmark.

        Raises on any verification failure — the source file is only
        removed after the copy proves byte-identical."""
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
        if producer not in ("mira", "external"):
            raise ValueError(f"unknown producer: {producer!r}")
        if producer == "mira" and kind != "exposure":
            # spec/109 §4: in-app Mertens is exposure-only — focus
            # brackets stay external-only (no built-in focus stacker).
            raise ValueError(
                "producer='mira' is only valid for exposure brackets")
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
                    producer=producer,
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
        source_dc_id: Optional[str] = None,
        source_dc_kind: Optional[str] = None,
        expr_snapshot: Sequence[Sequence] = (),
        target_s: Optional[int] = None,
        max_s: Optional[int] = None,
        photo_s: float = 6.0,
        # spec/152 §3 — per-Cut crossfade transition (ms); None means
        # "fall back to Settings.default_transition_ms" at read time.
        transition_ms: Optional[int] = None,
        default_state: str = "skipped",
        music_category: Optional[str] = None,
        separators: bool = True,
        overlay_fields: Sequence[str] = (),
        overlay_mode: Optional[str] = None,
        card_style: str = "black",
        aspect: str = "16:9",
    ) -> m.Cut:
        """Create a frozen Cut from a user-typed name (spec/81 §3). The dialog
        previews the transform live, but the gateway is the enforcement point:
        the name is slugified here and re-validated against the Cut namespace —
        raises ``ValueError`` carrying the :func:`core.cut_names.check_tag` code
        ('empty' / 'reserved' / 'taken'). ``source_dc_id`` is the source
        Collection's id (None = ad-hoc); ``source_dc_kind`` is the discriminator
        added in schema v8 (spec/81 Phase 2):

        * ``'event'`` — the source Collection lives in this event's
          ``event.db.dynamic_collection`` (bound DC, spec/93 §3 last paragraph).
        * ``'user'`` — the source Collection lives in the user-level recipe
          library. Pre-spec/94 Phase 1b this meant the legacy
          ``mira.db.saved_filter`` table; the dual-home migration emptied
          that table and moved every cross-event Collection into
          ``<library_root>/Collections/`` as a JSON file (spec/93 §4), so the
          value's live meaning is now **"the source is a file-library
          Collection"**. No DDL change — the CHECK still allows ``'event' |
          'user' | NULL``; only the semantic of ``'user'`` shifted.
        * ``None`` — legacy / unset.

        When ``source_dc_id`` is set and ``source_dc_kind`` is left ``None``,
        the gateway auto-infers the kind: id present in event.db → ``'event'``;
        id present in the file library → ``'user'``; neither → ``None``. This
        lets ``CutSession.commit()`` stay agnostic about where the source DC
        lives while still landing the right discriminator on the Cut row.

        ``expr_snapshot`` is the formula frozen at pin (style + media filters
        live on the DC, not the Cut). Membership is written separately via
        :meth:`set_cut_members`."""
        slug = cut_names.slugify(name)
        err = cut_names.check_tag(slug, [c.tag for c in self.cuts()])
        if err:
            raise ValueError(err)
        now = self._now()
        # spec/94 Phase 2 — auto-infer source_dc_kind so callers (the
        # pin session) don't have to know whether the source DC lives
        # in event.db or in the file library.
        if source_dc_id and source_dc_kind is None:
            source_dc_kind = self._infer_source_dc_kind(source_dc_id)
        from core.cut_aspect import normalise as _normalise_aspect
        cut = m.Cut(
            id=self._new_id(), tag=slug, created_at=now, updated_at=now,
            source_dc_id=source_dc_id,
            source_dc_kind=source_dc_kind,
            expr_snapshot_json=json.dumps([list(t) for t in expr_snapshot]),
            target_s=target_s, max_s=max_s, photo_s=photo_s,
            transition_ms=transition_ms,
            default_state=default_state,
            music_category=music_category,
            separators=separators,
            overlay_fields_json=json.dumps(list(overlay_fields)),
            overlay_mode=overlay_mode,
            # spec/111 — coerce through the canonical list so a UI bug
            # or stale call site can't park a bogus value on disk; the
            # DDL CHECK is belt-and-braces for fresh installs.
            aspect=_normalise_aspect(aspect),
            extras_json=json.dumps({"card_style": card_style}),
        )
        with self.store.transaction():
            self.store.upsert(cut)
            self._touch()
        return cut

    def _infer_source_dc_kind(self, dc_id: str) -> Optional[str]:
        """Discriminate where a Collection with id ``dc_id`` lives —
        ``'event'`` for an event.db DC, ``'user'`` for a file-library
        Collection, ``None`` when neither holds it.

        Used by :meth:`create_cut` when the caller didn't pass an
        explicit ``source_dc_kind`` (spec/94 Phase 2 — the pin session
        stays agnostic about storage; the gateway lands the right
        value)."""
        if not dc_id:
            return None
        if self.dynamic_collection(dc_id) is not None:
            return "event"
        snapshot = self._collections_library_snapshot()
        if snapshot is not None and dc_id in snapshot[0]:
            return "user"
        return None

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
        """Re-pinning may change the Cut's frozen fields (target_s / max_s /
        photo_s / source_dc_id / expr_snapshot_json / default_state /
        music_category / separators / overlay_fields_json / overlay_mode /
        card_style). Style + media filters live on the DC, not here. Tag changes
        go through :meth:`rename_cut`; membership through
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
        allowed = {"target_s", "max_s", "photo_s", "source_dc_id",
                   "source_dc_kind",
                   "expr_snapshot_json", "default_state", "music_category",
                   "separators", "overlay_fields_json", "overlay_mode",
                   "extras_json",
                   # spec/111 — slideshow canvas aspect.
                   "aspect",
                   # spec/152 §3 — per-Cut crossfade transition (ms).
                   "transition_ms"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown cut fields: {sorted(unknown)}")
        if "aspect" in fields:
            from core.cut_aspect import normalise as _normalise_aspect
            fields["aspect"] = _normalise_aspect(fields["aspect"])
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

    def set_cut_members(
        self,
        cut_id: str,
        members: Iterable,
    ) -> int:
        """The Create Cut commit (spec/61 §2 step 7): replace the Cut's
        membership with the session's picked files, one transaction, bulk
        (no per-row transactions — store.transaction() is not reentrant).
        Returns the new member count.

        ``members`` accepts three shapes:

        * ``Iterable[str]`` — legacy event-scope (kind='export', event_id=NULL,
          export_relpath=the string). For event-scope code that never crosses
          stores.
        * ``Iterable[Tuple[Optional[str], str]]`` — cross-event export
          (kind='export'). Each entry is ``(event_id, export_relpath)``.
        * ``Iterable[dict]`` — full shape. Keys:
          ``"kind"`` ('export'|'grab', default 'export'),
          ``"event_id"`` (optional source event UUID),
          ``"export_relpath"`` (set for kind='export'),
          ``"origin_relpath"`` (set for kind='grab', the source event's
          ``Original Media/<...>`` — spec/81 Phase 2 Item 6 grab-originals).
          ``member_id`` defaults to whichever relpath the kind requires.

        The first element's type discriminates; mixed shapes raise. Dedupes
        on ``member_id`` so the same item can't be added twice."""
        items = list(members)
        rows: List[Tuple[str, str, str, Optional[str], Optional[str], Optional[str]]] = []
        # (cut_id, member_id, kind, export_relpath, origin_relpath, event_id)
        if items and isinstance(items[0], dict):
            for d in items:
                kind = d.get("kind", "export")
                eid = d.get("event_id")
                if kind == "grab":
                    origin = d["origin_relpath"]
                    rows.append((cut_id, origin, "grab", None, origin, eid))
                else:
                    export = d["export_relpath"]
                    rows.append((cut_id, export, "export", export, None, eid))
        elif items and isinstance(items[0], str):
            for rp in items:
                rows.append((cut_id, rp, "export", rp, None, None))
        elif items:
            for (eid, rp) in items:
                rows.append((cut_id, rp, "export", rp, None, eid))
        # Dedupe on (cut_id, member_id) — same content-stable PK as the table.
        seen: set = set()
        unique: List[Tuple[str, str, str, Optional[str], Optional[str], Optional[str]]] = []
        for r in rows:
            key = (r[0], r[1])
            if key in seen:
                continue
            seen.add(key)
            unique.append(r)
        now = self._now()
        with self.store.transaction() as conn:
            conn.execute("DELETE FROM cut_member WHERE cut_id = ?", (cut_id,))
            conn.executemany(
                "INSERT INTO cut_member "
                "(cut_id, member_id, kind, export_relpath, origin_relpath, "
                "event_id, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(*r, now) for r in unique])
            conn.execute(
                "UPDATE cut SET updated_at = ? WHERE id = ?", (now, cut_id))
            self._touch()
        return len(unique)

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

    def set_lineage_intent(
        self, export_relpath: str, intent_state: str,
    ) -> None:
        """spec/89 §1.2 / Block 1 D2.B — per-version intent for a
        cluster member. Valid states: ``'compare'`` (undecided, the
        Compare orange initial reading), ``'picked'`` (will ship),
        ``'skipped'`` (will be dropped on the next Export run).

        Single-version flat cells ignore this column entirely — their
        intent rides ``phase_state(edit)`` on the source item — so a
        cluster forms only when ``versions_for_item()`` returns ≥2
        rows. The mutator is a thin UPDATE so the caller can stay
        branch-free at the click site (the days-grid verb path passes
        the click's export_relpath straight in)."""
        if intent_state not in ("compare", "picked", "skipped"):
            raise ValueError(
                f"set_lineage_intent: invalid state {intent_state!r}; "
                "must be 'compare' / 'picked' / 'skipped'")
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE lineage SET intent_state = ? "
                "WHERE export_relpath = ?",
                (intent_state, export_relpath))
            self._touch()

    # ── spec/159 — per-version ratings on Exported Collection review ──

    def set_lineage_stars(
        self, export_relpath: str, stars: Optional[int],
    ) -> None:
        """spec/159 — set the per-version star rating on a lineage row.
        ``stars`` is 1..5 or ``None`` (clear). Raises ``ValueError`` on
        out-of-range; mutates exactly one row by ``export_relpath`` and
        funnels through ``_touch`` so the read-only-library guard +
        backup snapshot trigger fire."""
        if stars is not None and not (1 <= int(stars) <= 5):
            raise ValueError(
                f"set_lineage_stars: stars must be 1..5 or None, got "
                f"{stars!r}")
        val = None if stars is None else int(stars)
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE lineage SET stars = ? WHERE export_relpath = ?",
                (val, export_relpath))
            self._touch()

    def set_lineage_color_label(
        self, export_relpath: str, label: Optional[str],
    ) -> None:
        """spec/159 — set the per-version LRC-style colour label. Valid
        labels: ``'red'`` / ``'yellow'`` / ``'green'`` / ``'blue'`` /
        ``'purple'``, or ``None`` to clear."""
        if label is not None and label not in _LINEAGE_COLOR_LABELS:
            raise ValueError(
                f"set_lineage_color_label: invalid label {label!r}; "
                f"must be one of {sorted(_LINEAGE_COLOR_LABELS)!r} or None")
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE lineage SET color_label = ? WHERE export_relpath = ?",
                (label, export_relpath))
            self._touch()

    def set_lineage_flag(
        self, export_relpath: str, flag: bool,
    ) -> None:
        """spec/159 — toggle the per-version portfolio flag."""
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE lineage SET flag = ? WHERE export_relpath = ?",
                (1 if flag else 0, export_relpath))
            self._touch()

    def set_lineage_to_delete(
        self, export_relpath: str, to_delete: bool,
    ) -> None:
        """spec/159 — mark / unmark a lineage row for batch deletion via
        the closed-event Cut page's "⌫ Delete N marked…" toolbar action.
        The unlink does NOT happen here — the column flip is reversible
        until the user confirms in :meth:`delete_marked_exported_files`."""
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE lineage SET to_delete = ? WHERE export_relpath = ?",
                (1 if to_delete else 0, export_relpath))
            self._touch()

    def lineage_ratings(self, export_relpath: str) -> LineageRatings:
        """spec/159 — read every review field for one lineage row in a
        single round-trip. Returns the zero-state ``LineageRatings``
        for rows that don't exist (the caller's surface typically
        renders nothing for absent rows anyway)."""
        row = self.store.conn.execute(
            "SELECT stars, color_label, flag, to_delete, is_preferred "
            "FROM lineage WHERE export_relpath = ?",
            (export_relpath,)).fetchone()
        if row is None:
            return LineageRatings(None, None, False, False, False)
        return LineageRatings(
            stars=row["stars"],
            color_label=row["color_label"],
            flag=bool(row["flag"]),
            to_delete=bool(row["to_delete"]),
            is_preferred=bool(row["is_preferred"]),
        )

    def set_lineage_preferred(
        self, export_relpath: str, preferred: bool,
    ) -> None:
        """spec/159 §6+ — mark / unmark a lineage row as the preferred
        version of its source item.

        Enforces "at most one preferred per ``source_item_id``" inside
        a single transaction:

        * when ``preferred=True``, every sibling lineage row sharing
          the same source has its ``is_preferred`` cleared, AND the
          item's ``preferred_virtual_mira`` is cleared too (mutually
          exclusive with the virtual flag);
        * when ``preferred=False`` simply clears this row's flag.

        Defensive: when the row's ``source_item_id`` is ``NULL`` (a
        legacy third-party return with no match) we only flip this
        row's own flag — there is nothing to dedupe against. The
        method is a no-op when the row doesn't exist (UPDATE matches
        zero rows; the gateway stays silent).
        """
        with self.store.transaction() as conn:
            if preferred:
                row = conn.execute(
                    "SELECT source_item_id FROM lineage "
                    "WHERE export_relpath = ?",
                    (export_relpath,)).fetchone()
                if row is not None and row["source_item_id"] is not None:
                    conn.execute(
                        "UPDATE lineage SET is_preferred = 0 "
                        "WHERE source_item_id = ? AND export_relpath != ?",
                        (row["source_item_id"], export_relpath))
                    # Clear the virtual-Mira flag on the source item;
                    # picking a real row supersedes the "Mira pending"
                    # intent.
                    conn.execute(
                        "UPDATE item SET preferred_virtual_mira = 0 "
                        "WHERE id = ?",
                        (row["source_item_id"],))
                conn.execute(
                    "UPDATE lineage SET is_preferred = 1 "
                    "WHERE export_relpath = ?",
                    (export_relpath,))
            else:
                conn.execute(
                    "UPDATE lineage SET is_preferred = 0 "
                    "WHERE export_relpath = ?",
                    (export_relpath,))
            self._touch()

    def set_item_preferred_virtual_mira(
        self, item_id: str, preferred: bool,
    ) -> None:
        """spec/159 §6+ — mark / unmark the virtual-Mira intent of a
        source item as preferred. Used when the cluster has only a
        third-party return on disk + a Mira intent (no Mira render
        file yet) and the user wants to say "use the Mira version
        once it ships."

        Mutually exclusive with any real ``lineage.is_preferred`` row
        for the same source: setting True clears every lineage
        preferred flag for this item; setting False just clears the
        column on the item row."""
        with self.store.transaction() as conn:
            if preferred:
                conn.execute(
                    "UPDATE lineage SET is_preferred = 0 "
                    "WHERE source_item_id = ?",
                    (item_id,))
                conn.execute(
                    "UPDATE item SET preferred_virtual_mira = 1 "
                    "WHERE id = ?",
                    (item_id,))
            else:
                conn.execute(
                    "UPDATE item SET preferred_virtual_mira = 0 "
                    "WHERE id = ?",
                    (item_id,))
            self._touch()

    def preferred_for_item(
        self, source_item_id: str,
    ) -> Optional[m.Lineage]:
        """spec/159 §6+ — the preferred version of a source item, or
        ``None`` when no preference has been set. Downstream surfaces
        (Cuts compose) call this to default the included version when
        adding a multi-version source to a Cut."""
        sql = (
            "SELECT l.* FROM lineage l "
            "WHERE l.source_item_id = ? AND l.is_preferred = 1 "
            "LIMIT 1"
        )
        rows = self.store.query_raw(m.Lineage, sql, (source_item_id,))
        return rows[0] if rows else None

    def exported_marked_for_deletion(self) -> List[m.Lineage]:
        """spec/159 — every lineage row under ``Exported Media/`` whose
        ``to_delete = 1``, in deterministic order (export_relpath).
        Drives the toolbar's "⌫ Delete N marked…" count + the confirm
        dialog's preview list."""
        sql = (
            "SELECT l.* FROM lineage l "
            "WHERE l.phase = 'edit' "
            "AND l.export_relpath LIKE 'Exported Media/%' "
            "AND l.to_delete = 1 "
            "ORDER BY l.export_relpath ASC"
        )
        return self.store.query_raw(m.Lineage, sql)

    def delete_marked_exported_files(self) -> int:
        """spec/159 — commit the batch delete: unlink every file under
        ``Exported Media/`` whose lineage row has ``to_delete = 1``,
        drop the row, clear ``edit_exported`` when the last shipped
        row for that item goes, and propagate the Cut-membership
        cascade. Returns the number of files actually deleted.

        Each row is delegated to :meth:`delete_exported_file_by_relpath`
        — same single-row unlink path the per-cell Skip-on-shipped
        verb (spec/89 §3) uses, so the gateway-side cascade is
        identical (cut_member cleanup, edit_exported flip,
        ``_unlink_with_retry`` for Windows lock retries). A row that
        fails to unlink (filesystem error) keeps its ``to_delete``
        flag for retry; rows that succeed clear naturally because
        the underlying row is dropped."""
        rows = self.exported_marked_for_deletion()
        deleted_count = 0
        for row in rows:
            result = self.delete_exported_file_by_relpath(
                row.export_relpath)
            if result.get("rows_deleted", 0) >= 1:
                deleted_count += 1
        return deleted_count

    def versions_for_item(self, item_id: str) -> List[m.Lineage]:
        """spec/89 Slice 5 — every ``Exported Media/`` lineage row for
        a source item, in newest-first export-time order (Block 1
        D4.A). Rows without an ``exported_at`` stamp sort last (no
        timestamp = legacy or external row). Returns ``[]`` if the
        item has no ship rows."""
        sql = (
            "SELECT l.* FROM lineage l "
            "WHERE l.phase = 'edit' AND l.source_item_id = ? "
            "  AND l.export_relpath LIKE 'Exported Media/%' "
            "ORDER BY COALESCE(l.exported_at, '') DESC, l.export_relpath ASC"
        )
        return self.store.query_raw(m.Lineage, sql, (item_id,))

    def clear_lineage(self, phase: str) -> None:
        """Drop a phase's lineage rows (rebuilt on re-export). The
        spec/81 Phase 2 v8 schema dropped the FK cascade on cut_member;
        sweep matching event-scope cut_member rows explicitly here so
        Cuts don't keep dangling references to now-gone lineage rows."""
        with self.store.transaction() as conn:
            conn.execute(
                "DELETE FROM cut_member WHERE event_id IS NULL AND "
                "export_relpath IN (SELECT export_relpath FROM lineage "
                "WHERE phase = ?)", (phase,))
            conn.execute("DELETE FROM lineage WHERE phase = ?", (phase,))
            self._touch()

    @staticmethod
    def _unlink_with_retry(path: "Path", *, attempts: int = 5,
                           delay: float = 0.1) -> None:
        """Unlink ``path``, retrying transient Windows file locks (WinError
        32 — a brief handle held by the thumbnailer / AV / Qt pixmap loader).
        A vanished file counts as success; the last error is re-raised if
        every attempt fails, so the caller's no-erase-the-record guard still
        fires."""
        import time
        for i in range(attempts):
            try:
                path.unlink()
                return
            except FileNotFoundError:
                return
            except OSError:
                if i == attempts - 1:
                    raise
                time.sleep(delay * (i + 1))

    def delete_exported_file(self, item_id: str) -> Dict:
        """Undo one item's Export ship: delete its on-disk JPEG(s) under
        ``Exported Media/``, drop its lineage row(s), and flip
        ``Adjustment.edit_exported`` back to False.

        Returns ``{"deleted_files": [Path…], "missing_files": [str…],
        "rows_deleted": N}``. Charter-safe: only files under
        ``event_root/Exported Media/`` get unlinked (the derived /
        regenerable tier — spec/66 §1.2); ``Original Media/`` is never
        touched. Multiple lineage rows for the same item (re-exports
        under the spec/54 §8 versions-as-exports policy each get their
        own row) are all dropped — undoing the ship undoes every
        registered ship file for that item.

        Cut membership cleanup (spec/61 §1.4): schema v8 (spec/81 Phase 2)
        DROPPED the FK CASCADE on ``cut_member.export_relpath`` so cross-
        event members can reference other events' lineage. This method now
        sweeps event-scope cut_member rows explicitly — same end state as
        the legacy cascade, just enforced at the gateway. Cross-event
        members from OTHER events that happened to share a relpath in
        THIS event survive (their bytes live elsewhere); they're cleaned
        on next read or by a future sweep.
        """
        if self.event_root is None:
            return {"deleted_files": [], "missing_files": [],
                    "rows_deleted": 0}
        rows = self.store.conn.execute(
            "SELECT export_relpath FROM lineage "
            "WHERE phase = 'edit' AND source_item_id = ? "
            "AND export_relpath LIKE 'Exported Media/%'",
            (item_id,),
        ).fetchall()
        if not rows:
            return {"deleted_files": [], "missing_files": [],
                    "rows_deleted": 0}

        event_root = Path(self.event_root)
        deleted: list = []
        missing: list = []
        for r in rows:
            rel = r["export_relpath"]
            abs_path = event_root / rel
            if abs_path.is_file():
                try:
                    self._unlink_with_retry(abs_path)
                    deleted.append(abs_path)
                except OSError:
                    log.exception(
                        "delete_exported_file: unlink failed for %s",
                        abs_path)
                    # Leave the row alone — the file is still on disk,
                    # the user should resolve manually before we erase
                    # the only record of where it landed.
                    continue
            else:
                # File already gone (manual deletion, archive move) —
                # drop the row anyway so the watermark / Share clear.
                missing.append(rel)

        # Drop the lineage rows we successfully handled (file gone OR
        # file deleted). The CASCADE on cut_member.export_relpath does
        # the spec/61 §1.4 Cut-membership cleanup for free.
        with self.store.transaction() as conn:
            kept_paths = {
                str((event_root / r["export_relpath"]))
                for r in rows
            } - {str(p) for p in deleted}
            kept_paths -= set()  # placeholder for clarity
            # Build the set of relpaths whose rows we actually want to
            # drop (deleted files + missing files).
            handled_rels = (
                {p.relative_to(event_root).as_posix() for p in deleted}
                | set(missing)
            )
            for rel in handled_rels:
                conn.execute(
                    "DELETE FROM lineage WHERE export_relpath = ?",
                    (rel,))
                # spec/81 Phase 2: the FK cascade is gone; sweep event-
                # scope cut_member rows explicitly. NULL event_id = legacy
                # event-scope; cross-event members (event_id non-NULL)
                # belong to other events and stay put.
                conn.execute(
                    "DELETE FROM cut_member WHERE export_relpath = ? "
                    "AND event_id IS NULL",
                    (rel,))
            self._touch()
        rows_deleted = len(deleted) + len(missing)

        # Clear the freshness flag — the item has no shipped file any
        # more. ``set_edit_exported`` re-saves the Adjustment via the
        # normal mutator so ``updated_at`` ticks.
        if rows_deleted:
            try:
                self.set_edit_exported(item_id, False)
            except Exception:                                       # noqa: BLE001
                log.exception(
                    "delete_exported_file: set_edit_exported(False) "
                    "failed for %s", item_id)

        return {
            "deleted_files": deleted,
            "missing_files": missing,
            "rows_deleted": rows_deleted,
        }

    def event_cut_usage_count(
        self, export_relpaths: Iterable[str],
    ) -> int:
        """spec/147 §4 — count the DISTINCT event-scope Cuts (event.db
        ``cut_member`` rows with ``event_id IS NULL``) that contain any
        of ``export_relpaths``.

        The Export-surface Delete-now confirm sums this with
        :meth:`LibraryGateway.cross_event_cut_usage_count` to warn the
        user before nuking files that are still in any Cut. Counts
        DISTINCT cuts (not member rows) so a single Cut with two
        doomed files counts once.

        Returns 0 on an empty input list."""
        relpaths = [r for r in (export_relpaths or ()) if r]
        if not relpaths:
            return 0
        placeholders = ",".join(["?"] * len(relpaths))
        row = self.store.conn.execute(
            "SELECT COUNT(DISTINCT cut_id) FROM cut_member "
            f"WHERE event_id IS NULL AND export_relpath IN ({placeholders})",
            tuple(relpaths),
        ).fetchone()
        return int(row[0] if row else 0)

    def delete_exported_files_by_relpaths(
        self, export_relpaths: Iterable[str],
    ) -> Dict:
        """spec/147 §2 — the "Delete now" batch worker.

        Per-file delegate to :meth:`delete_exported_file_by_relpath`
        (same cascade semantics: drop the lineage row, the on-disk
        file, the event-scope ``cut_member`` rows, and ``edit_exported``
        when the last shipped row for the source item is gone).
        Returns the aggregate ``{"deleted_files": [...],
        "missing_files": [...], "rows_deleted": int, "item_ids":
        [...]}`` summary across every relpath.

        Cross-event ``cut_member`` rows (the LIBRARY's user_store.db)
        live in a different store and are cleaned up by the caller via
        :meth:`LibraryGateway.delete_cross_event_cut_members` — kept
        out of here so this gateway stays event-scope (charter
        invariant #1)."""
        relpaths = [r for r in (export_relpaths or ()) if r]
        deleted_files: list = []
        missing_files: list = []
        rows_deleted = 0
        item_ids: list = []
        for rel in relpaths:
            res = self.delete_exported_file_by_relpath(rel)
            deleted_files.extend(res.get("deleted_files") or [])
            missing_files.extend(res.get("missing_files") or [])
            rows_deleted += int(res.get("rows_deleted") or 0)
            if res.get("item_id"):
                item_ids.append(res["item_id"])
        return {
            "deleted_files": deleted_files,
            "missing_files": missing_files,
            "rows_deleted": rows_deleted,
            "item_ids": item_ids,
        }

    def set_aside_export_relpaths(self) -> List[str]:
        """spec/147 §2 — every ``Exported Media/`` lineage row whose
        ``intent_state = 'skipped'`` (Set aside) and whose file is
        STILL on disk. The Days Grid / Days List "Delete now · M"
        button reads this list to drive the live ``M`` count + the
        actual delete pass.

        Returns the relpaths in deterministic (export_relpath) order
        so the count + the run agree."""
        if self.event_root is None:
            return []
        rows = self.store.conn.execute(
            "SELECT export_relpath FROM lineage "
            "WHERE phase = 'edit' AND intent_state = 'skipped' "
            "AND export_relpath LIKE 'Exported Media/%' "
            "ORDER BY export_relpath"
        ).fetchall()
        event_root = Path(self.event_root)
        out: List[str] = []
        for r in rows:
            rel = r["export_relpath"]
            if (event_root / rel).is_file():
                out.append(rel)
        return out

    def delete_exported_file_by_relpath(
        self, export_relpath: str,
    ) -> Dict:
        """File-level twin of :meth:`delete_exported_file` — drops the
        ONE lineage row matching ``export_relpath`` + its on-disk file
        + clears ``edit_exported`` IFF this was the last row for the
        source item.

        The Pool's "Delete exported" action (spec/61 §1.4 cascade-
        aware) needs file granularity: re-exports under spec/54 §8
        produce multiple rows for one item, and the user picks
        per-file in the #exported grid. ``delete_exported_file``
        unships the whole item — too wide a blast for this surface.

        Cut membership cleanup (spec/61 §1.4): schema v8 (spec/81 Phase 2)
        DROPPED the FK CASCADE on ``cut_member.export_relpath`` so cross-
        event members can reference other events' lineage. The legacy
        event-scope cleanup runs here explicitly. ``Original Media/`` stays
        untouchable — the relpath must match the ``Exported Media/``
        prefix or the call no-ops.

        Returns ``{"deleted_files": [Path…], "missing_files":
        [str…], "rows_deleted": 0|1, "item_id": str|None}``.
        """
        empty = {
            "deleted_files": [], "missing_files": [],
            "rows_deleted": 0, "item_id": None,
        }
        if self.event_root is None:
            return empty
        rel = str(export_relpath).replace("\\", "/")
        if not rel.startswith("Exported Media/"):
            # Charter pin — never touch other tiers.
            return empty
        row = self.store.conn.execute(
            "SELECT export_relpath, source_item_id FROM lineage "
            "WHERE phase = 'edit' AND export_relpath = ?", (rel,),
        ).fetchone()
        if row is None:
            return empty
        item_id = row["source_item_id"]
        event_root = Path(self.event_root)
        abs_path = event_root / rel
        deleted: list = []
        missing: list = []
        if abs_path.is_file():
            try:
                self._unlink_with_retry(abs_path)
                deleted.append(abs_path)
            except OSError:
                log.exception(
                    "delete_exported_file_by_relpath: unlink failed for %s",
                    abs_path)
                return {**empty, "item_id": item_id}
        else:
            missing.append(rel)
        with self.store.transaction() as conn:
            conn.execute(
                "DELETE FROM lineage WHERE export_relpath = ?", (rel,))
            # spec/81 Phase 2: gateway-enforced cascade — event-scope
            # cut_member rows for this relpath go too. Cross-event rows
            # (event_id non-NULL) reference another event's lineage and
            # stay put.
            conn.execute(
                "DELETE FROM cut_member WHERE export_relpath = ? "
                "AND event_id IS NULL",
                (rel,))
            self._touch()
        # Clear edit_exported only when no other shipped row survives
        # for the item — otherwise the watermark still belongs.
        if item_id:
            remaining = self.store.conn.execute(
                "SELECT 1 FROM lineage "
                "WHERE phase = 'edit' AND source_item_id = ? "
                "AND export_relpath LIKE 'Exported Media/%' "
                "LIMIT 1", (item_id,),
            ).fetchone()
            if remaining is None:
                try:
                    self.set_edit_exported(item_id, False)
                except Exception:                                  # noqa: BLE001
                    log.exception(
                        "delete_exported_file_by_relpath: "
                        "set_edit_exported(False) failed for %s",
                        item_id)
        return {
            "deleted_files": deleted,
            "missing_files": missing,
            "rows_deleted": 1,
            "item_id": item_id,
        }

    def rescan_exported_media(self) -> int:
        """Reconcile ``Exported Media/`` lineage to the bytes on disk:
        **prune** rows whose file is gone, then **backfill** rows for
        orphan files that exist on disk but lack one.

        Mirrors the spec/57 §3 ``Edited Media/`` returns scan: walk the
        on-disk tree, find files that have no lineage row pointing at
        them, match each back to a source ``Item`` by source-filename
        stem, and write the missing rows + ``Adjustment.edit_exported``.

        Self-heals **lost-commit** failures of the Export run — e.g.
        when the spec/60 worker process exits cleanly but echoes no ``ok``
        unit messages, the spec/68 §3 ``ExportPage._submit_batch.commit``
        closure short-circuits on empty ``ok_unit_ids`` and writes
        nothing, even though the engine could have rendered the JPEGs.
        The rescan finds those orphan files and writes the rows the
        Export run forgot, so the Exported watermark + Share's
        ``#exported`` pool catch up on the next surface entry.

        Match rule: stem of the on-disk file (without extension) against
        the stem of every Pick-kept photo's ``origin_relpath``. Ambiguous
        stems (more than one Pick-kept photo with the same filename, e.g.
        ``DSC0001.cr3`` on day 1 AND day 2) are SKIPPED — the rescan is
        conservative; the user re-runs Export to disambiguate. Returns
        the number of lineage rows reconciled (pruned + backfilled).

        Cheap: scans only the existing ``Exported Media/`` subtree, runs
        the stem map once. Suitable to call on every Share/Export entry
        as a no-op when nothing is orphaned.
        """
        if self.event_root is None:
            return 0
        event_root = Path(self.event_root)
        exported_root = event_root / "Exported Media"

        # ---- Prune pass (Nelson 2026-06-15): the bytes on disk are the
        # source of truth for the EXPORTED tier. Any 'edit' lineage row
        # under ``Exported Media/`` whose file no longer exists is stale
        # dirt — drop it. Runs even when the folder is empty or absent, so a
        # wiped ``Exported Media/`` reconciles ``#exported`` (and the
        # exported clusters that read off lineage) back to empty. The
        # ``cut_member.export_relpath`` FK CASCADE removes the file from
        # every Cut for free (regenerable tier — re-export rebuilds it).
        pruned = 0
        stale_rows = self.store.conn.execute(
            "SELECT export_relpath FROM lineage "
            "WHERE phase = 'edit' "
            "AND export_relpath LIKE 'Exported Media/%'"
        ).fetchall()
        for r in stale_rows:
            rel = r["export_relpath"]
            if (event_root / rel).is_file():
                continue
            try:
                if self.delete_exported_file_by_relpath(rel).get(
                        "rows_deleted"):
                    pruned += 1
            except Exception:                                       # noqa: BLE001
                log.exception(
                    "rescan_exported_media: prune failed for %s", rel)
        if pruned:
            log.info(
                "rescan_exported_media: pruned %d stale lineage row(s) — "
                "file missing under %s", pruned, exported_root)

        # Backfill needs the folder to exist; a missing folder means there
        # is nothing on disk to add (the prune above already reconciled the
        # rows downward to match).
        if not exported_root.is_dir():
            return pruned

        # Already-recorded ship rows — skip files that already have one.
        already_rows = self.store.conn.execute(
            "SELECT export_relpath FROM lineage "
            "WHERE phase = 'edit' "
            "AND export_relpath LIKE 'Exported Media/%'"
        ).fetchall()
        already_recorded: set = {r["export_relpath"] for r in already_rows}

        # Pick-kept photo items, keyed by source-filename stem. Ambiguous
        # stems (same filename appearing twice in the picked pool) are
        # marked None so the rescan refuses to guess.
        stem_to_item_id: dict = {}
        for it in self.items(phase="pick", state="picked", kind="photo",
                             provenance="captured"):
            if not it.origin_relpath:
                continue
            stem = Path(it.origin_relpath).stem
            if stem in stem_to_item_id:
                stem_to_item_id[stem] = None  # ambiguous — skip both
            else:
                stem_to_item_id[stem] = it.id

        # Walk Exported Media/ and write rows for the orphans.
        stamp = self._now()
        written = 0
        for f in exported_root.rglob("*"):
            if not f.is_file():
                continue
            try:
                rel = f.relative_to(self.event_root).as_posix()
            except ValueError:
                continue
            if rel in already_recorded:
                continue
            item_id = stem_to_item_id.get(f.stem)
            if not item_id:
                # Unknown stem (item not in picked pool) or ambiguous —
                # leave the file alone. Honest: a re-export from the
                # surface remains the authoritative path for these.
                log.info(
                    "rescan_exported_media: orphan %s — no unique source "
                    "stem match", rel)
                continue
            try:
                self.record_lineage(m.Lineage(
                    export_relpath=rel,
                    phase="edit",
                    source_kind="item",
                    source_item_id=item_id,
                    recipe_json=None,
                    exported_at=stamp,
                ))
                self.set_edit_exported(item_id, True)
                written += 1
            except Exception:                                       # noqa: BLE001
                log.exception(
                    "rescan_exported_media: backfill failed for %s", rel)
        if written:
            log.info(
                "rescan_exported_media: backfilled %d lineage row(s) "
                "under %s", written, exported_root)
        return written + pruned

    # ----- missing-originals enumeration + explicit-only prune ----------- #
    #
    # The originals tier is the inverse of the exported tier (above):
    # exports are regenerable, so ``rescan_exported_media`` PRUNES
    # missing bytes. Originals are not regenerable, so missing bytes are
    # only ever ENUMERATED here — never auto-pruned. The only path that
    # actually drops rows is :meth:`prune_missing_originals`, which the
    # UI must gate behind an explicit "these files are gone for good"
    # confirmation (charter §7).

    def list_missing_origin_items(self) -> List[str]:
        """Item ids whose ``origin_relpath`` points under ``Original Media/``
        but whose file no longer resolves on disk.

        Pure read — no writes, no cascade. Scoped to ``origin_relpath
        LIKE 'Original Media/%'`` so derived tiers (``Edited Media/``,
        ``Exported Media/``) and virtual rows (``origin_relpath IS
        NULL``) stay out of the count — those have their own reconciles
        and their own meaning of "missing". Returns ``[]`` when the
        event has no resolved root (called before the locate flow has
        re-anchored) so the dialog can fall back to a generic "all of
        them" prompt.
        """
        if self.event_root is None:
            return []
        event_root = Path(self.event_root)
        rows = self.store.conn.execute(
            "SELECT id, origin_relpath FROM item "
            "WHERE origin_relpath IS NOT NULL "
            "AND origin_relpath LIKE 'Original Media/%'"
        ).fetchall()
        missing: List[str] = []
        for r in rows:
            if not (event_root / r["origin_relpath"]).is_file():
                missing.append(r["id"])
        return missing

    def prune_missing_originals(self, item_ids: Iterable[str]) -> int:
        """Drop the named items in one transaction; FK cascades handle the rest.

        The destructive primitive behind the "These files are gone for
        good" branch of the missing-originals dialog. The caller has
        already confirmed; this method does not reprompt and does not
        re-verify that the files are actually missing — the verification
        belongs upstream so the prune itself stays a clean primitive
        (testable in isolation, callable from a future bulk tool).

        Per-item child rows ride along through the schema's ``ON DELETE
        CASCADE`` foreign keys (``phase_state``, ``adjustment``,
        ``video_adjustment``, ``video_marker``, ``video_segment``,
        ``video_snapshot``, ``stack_member``, ``bucket_member``,
        ``photo_person``, ``lineage.source_item_id`` and through it
        ``cut_member``). ``stack_bracket.output_item_id`` is
        ``ON DELETE SET NULL`` and survives — acceptable; the bracket
        stays without its output. Empty input is a no-op (no
        transaction, no ``_touch``). Returns the row count actually
        deleted (an id that's already gone counts as zero).
        """
        ids = [i for i in item_ids if i]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self.store.transaction() as conn:
            cur = conn.execute(
                f"DELETE FROM item WHERE id IN ({placeholders})",
                ids,
            )
            deleted = cur.rowcount
            self._touch()
        return int(deleted or 0)

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
        The bake's read path: fall back to ``camera.applied_offset_seconds``
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

    # ----- maps (spec/155) ----------------------------------------------- #

    def get_day_map_path(self, day_number: int) -> Optional[str]:
        """Return the per-day map's path relative to ``event_root``
        (e.g. ``Maps/day-02.jpg``), or ``None`` when no map is attached.
        Cheap PK lookup."""
        row = self.store.conn.execute(
            "SELECT map_image_path FROM trip_day WHERE day_number = ?",
            (day_number,),
        ).fetchone()
        if row is None:
            return None
        return row["map_image_path"]

    def get_event_map_path(self) -> Optional[str]:
        """Return the event-level map's path relative to ``event_root``
        (e.g. ``Maps/event.jpg``), or ``None`` when no map is attached."""
        row = self.store.conn.execute(
            "SELECT map_image_path FROM event WHERE id = 1"
        ).fetchone()
        if row is None:
            return None
        return row["map_image_path"]

    def attach_day_map(self, day_number: int, src_path: str | Path) -> str:
        """Copy ``src_path`` into ``Maps/day-NN.<ext>`` (atomic
        write-then-rename), null any stale sibling with a different
        accepted extension, write the relative path to
        ``trip_day.map_image_path``, and return the relative path.

        ``src_path`` must point at JPEG, PNG, or MP4 (extensions per
        :data:`core.path_builder.MAP_MEDIA_EXTENSIONS`). MP4 attach
        also extracts a first-frame sidecar (``…thumb.jpg``) so chip
        thumbnails + dialog previews don't re-run ffmpeg on every paint.
        Anything else raises ``ValueError``. The source file is left
        untouched."""
        from core.path_builder import (
            MAP_MEDIA_EXTENSIONS,
            day_map_slot_basename,
            maps_dir,
        )

        return self._attach_map_slot(
            slot_basename=day_map_slot_basename(day_number),
            src_path=src_path,
            update_sql="UPDATE trip_day SET map_image_path = ? WHERE day_number = ?",
            update_params_factory=lambda rel: (rel, day_number),
            accepted_exts=MAP_MEDIA_EXTENSIONS,
            maps_dir_factory=maps_dir,
        )

    def attach_event_map(self, src_path: str | Path) -> str:
        """Copy ``src_path`` into ``Maps/event.<ext>`` and write the
        relative path to ``event.map_image_path``. Accepts JPEG, PNG
        or MP4; on MP4, also writes the first-frame sidecar. Returns
        the relative path."""
        from core.path_builder import (
            MAP_MEDIA_EXTENSIONS,
            event_map_slot_basename,
            maps_dir,
        )

        return self._attach_map_slot(
            slot_basename=event_map_slot_basename(),
            src_path=src_path,
            update_sql="UPDATE event SET map_image_path = ? WHERE id = 1",
            update_params_factory=lambda rel: (rel,),
            accepted_exts=MAP_MEDIA_EXTENSIONS,
            maps_dir_factory=maps_dir,
        )

    def clear_day_map(self, day_number: int) -> None:
        """Delete the per-day map file (if any) and null
        ``trip_day.map_image_path``. Idempotent — calling on a day
        without a map is a no-op."""
        from core.path_builder import day_map_slot_basename, maps_dir

        self._clear_map_slot(
            slot_basename=day_map_slot_basename(day_number),
            current_path=self.get_day_map_path(day_number),
            update_sql="UPDATE trip_day SET map_image_path = NULL WHERE day_number = ?",
            update_params=(day_number,),
            maps_dir_factory=maps_dir,
        )

    def clear_event_map(self) -> None:
        """Delete the event-level map file (if any) and null
        ``event.map_image_path``. Idempotent."""
        from core.path_builder import event_map_slot_basename, maps_dir

        self._clear_map_slot(
            slot_basename=event_map_slot_basename(),
            current_path=self.get_event_map_path(),
            update_sql="UPDATE event SET map_image_path = NULL WHERE id = 1",
            update_params=(),
            maps_dir_factory=maps_dir,
        )

    def _attach_map_slot(
        self,
        *,
        slot_basename: str,
        src_path: str | Path,
        update_sql: str,
        update_params_factory: Callable[[str], Tuple],
        accepted_exts: Tuple[str, ...],
        maps_dir_factory: Callable[[Path], Path],
    ) -> str:
        """Shared core for attach_day_map / attach_event_map."""
        import shutil

        if self.event_root is None:
            raise RuntimeError("attach_*_map needs a resolvable event_root")
        src = Path(src_path)
        if not src.is_file():
            raise FileNotFoundError(src)
        ext = src.suffix.lower()
        if ext not in accepted_exts:
            raise ValueError(
                f"map image must be one of {accepted_exts}, got {ext!r}")
        # Normalize .jpeg -> .jpg on disk so slot files are consistently named.
        on_disk_ext = ".jpg" if ext == ".jpeg" else ext
        event_root = Path(self.event_root)
        dest_dir = maps_dir_factory(event_root)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{slot_basename}{on_disk_ext}"
        # Atomic write-then-rename: copy to a .tmp sibling then os.replace
        # so the slot is never observed half-written.
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        shutil.copy2(src, tmp)
        import os
        os.replace(tmp, dest)
        # Sweep any stale sibling with a different accepted extension
        # (e.g. an old day-02.png when the new attach is day-02.jpg).
        from core.path_builder import MAP_VIDEO_THUMB_SUFFIX
        for stale_ext in accepted_exts:
            stale_on_disk = ".jpg" if stale_ext == ".jpeg" else stale_ext
            if stale_on_disk == on_disk_ext:
                continue
            stale = dest_dir / f"{slot_basename}{stale_on_disk}"
            if stale.exists():
                stale.unlink()
            # spec/155 v2 — if the stale sibling was an MP4, its
            # first-frame sidecar also rides alongside; sweep it.
            if stale_on_disk == ".mp4":
                stale_sidecar = dest_dir / (
                    f"{slot_basename}{stale_on_disk}{MAP_VIDEO_THUMB_SUFFIX}")
                if stale_sidecar.exists():
                    stale_sidecar.unlink()
        rel = dest.relative_to(event_root).as_posix()
        # spec/155 v2 — MP4 maps get a first-frame sidecar so chip thumbs
        # and dialog previews can load a cheap QImage without ffmpeg.
        # Best-effort: if extraction fails (corrupt file, broken codec),
        # the attach still succeeds; the chip falls back to a generic
        # "video" placeholder.
        if on_disk_ext == ".mp4":
            try:
                self._write_video_map_thumb(dest)
            except Exception:                                       # noqa: BLE001
                log.warning(
                    "video map sidecar generation failed for %s",
                    dest, exc_info=True)
        with self.store.transaction() as conn:
            conn.execute(update_sql, update_params_factory(rel))
            self._touch()
        return rel

    @staticmethod
    def _video_map_thumb_path(map_abs_path: Path) -> Path:
        """The first-frame sidecar for a video map slot — same dir,
        same basename, ``.mp4.thumb.jpg`` suffix."""
        from core.path_builder import MAP_VIDEO_THUMB_SUFFIX
        return map_abs_path.with_suffix(
            map_abs_path.suffix + MAP_VIDEO_THUMB_SUFFIX)

    @classmethod
    def _write_video_map_thumb(cls, map_abs_path: Path) -> Optional[Path]:
        """Extract frame 0 of the MP4 to its sidecar. Returns the
        sidecar path on success, None on failure."""
        from core.video_extract import extract_frame
        sidecar = cls._video_map_thumb_path(map_abs_path)
        try:
            extract_frame(map_abs_path, 0, sidecar)
        except (FileNotFoundError, RuntimeError):
            return None
        return sidecar if sidecar.is_file() else None

    def _clear_map_slot(
        self,
        *,
        slot_basename: str,
        current_path: Optional[str],
        update_sql: str,
        update_params: Tuple,
        maps_dir_factory: Callable[[Path], Path],
    ) -> None:
        """Shared core for clear_day_map / clear_event_map."""
        if self.event_root is None:
            raise RuntimeError("clear_*_map needs a resolvable event_root")
        event_root = Path(self.event_root)
        # Delete by walking the accepted-extensions in case the DB and
        # disk fell out of sync — sweep anything sitting in the slot.
        from core.path_builder import (
            MAP_MEDIA_EXTENSIONS,
            MAP_VIDEO_THUMB_SUFFIX,
        )
        dest_dir = maps_dir_factory(event_root)
        for ext in MAP_MEDIA_EXTENSIONS:
            on_disk_ext = ".jpg" if ext == ".jpeg" else ext
            candidate = dest_dir / f"{slot_basename}{on_disk_ext}"
            if candidate.exists():
                candidate.unlink()
            # spec/155 v2 — sweep the MP4 sidecar that rode alongside.
            if on_disk_ext == ".mp4":
                sidecar = dest_dir / (
                    f"{slot_basename}{on_disk_ext}{MAP_VIDEO_THUMB_SUFFIX}")
                if sidecar.exists():
                    sidecar.unlink()
        # Belt-and-braces: if the DB pointed at a path outside the
        # standard slot (legacy / external edit), unlink that too.
        if current_path:
            legacy = event_root / current_path
            if legacy.exists() and legacy.is_file():
                try:
                    legacy.unlink()
                except OSError:
                    pass
        with self.store.transaction() as conn:
            conn.execute(update_sql, update_params)
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

    # ── spec/127 — per-(camera, trip-TZ-segment) correction store ────────

    def tz_segments(self) -> "List":
        """spec/127 §1.1 — derive trip-TZ segments from
        ``trip_day.tz_minutes`` and the per-(camera, day) captured-items
        presence. Returns a list of :class:`core.tz_segments.TzSegment`
        sorted ascending by ``trip_tz_seconds``. The unified correction
        dialog renders one section per segment; a single-segment trip
        shows no segment chrome."""
        from core.tz_segments import derive_segments

        trip_days_tz: Dict[int, Optional[int]] = {
            int(d.day_number): (None if d.tz_minutes is None else int(d.tz_minutes))
            for d in self.trip_days()
        }
        # One (camera_id, day_number) row per distinct captured pair.
        pairs = self.store.conn.execute(
            "SELECT DISTINCT camera_id, day_number FROM item "
            "WHERE provenance = 'captured' "
            "AND camera_id IS NOT NULL AND day_number IS NOT NULL"
        ).fetchall()
        return derive_segments(
            trip_days_tz,
            camera_day_pairs=[(str(r[0]), int(r[1])) for r in pairs],
        )

    def camera_tz_corrections(
        self, camera_id: Optional[str] = None,
    ) -> List[m.CameraTzCorrection]:
        """Read every persisted per-(camera, trip-TZ-segment) correction,
        or just the rows for one camera. Returns rows sorted by
        (camera_id, trip_tz_seconds) for deterministic ordering."""
        if camera_id is None:
            return self.store.all(m.CameraTzCorrection)
        return self.store.query_by(
            m.CameraTzCorrection, camera_id=camera_id)

    def camera_tz_correction(
        self, camera_id: str, trip_tz_seconds: int,
    ) -> Optional[m.CameraTzCorrection]:
        """Read the one correction row for ``(camera_id, trip_tz_seconds)``,
        or ``None`` when the user hasn't recorded one yet (the dialog
        renders that as the default "Clock was correct")."""
        return self.store.get(
            m.CameraTzCorrection, camera_id, int(trip_tz_seconds))

    def save_camera_tz_correction(
        self, correction: m.CameraTzCorrection,
        *, mirror_to_camera: bool = True,
    ) -> None:
        """Upsert one ``camera_tz_correction`` row — the unified Camera
        Clock Correction commit. Pair with
        :meth:`recompute_corrected_times` (scoped to the segment's days)
        so the items' corrected times follow.

        ``mirror_to_camera`` mirrors the row onto the legacy single
        per-camera ``camera.applied_offset_seconds`` /
        ``configured_tz_seconds`` summary columns so older read paths
        (the past-photos backfill, the v17 dialog still on the legacy
        seed) keep working. For a multi-segment trip the caller decides
        which segment "wins" the mirror — typically the segment with the
        most plan days; we let the dialog do that and call us once."""
        with self.store.transaction() as conn:
            self.store.upsert(correction)
            if mirror_to_camera:
                conn.execute(
                    "UPDATE camera SET applied_offset_seconds = ?, "
                    "configured_tz_seconds = ?, applied_at = ? "
                    "WHERE camera_id = ?",
                    (int(correction.applied_offset_seconds),
                     correction.configured_tz_seconds,
                     correction.applied_at,
                     correction.camera_id),
                )
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
        # spec/123 — trip_day still stores tz_minutes (zones are whole
        # minutes); convert the delta to seconds for item-level math.
        delta_seconds = (int(new_tz_minutes) - int(day.tz_minutes or 0)) * 60
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
                new_offset = int(it.tz_offset_seconds or 0) + delta_seconds
                corrected_dt = raw_dt + timedelta(seconds=new_offset)
                new_day = by_date.get(corrected_dt.date().isoformat(),
                                      it.day_number)
                if new_day != it.day_number:
                    moved += 1
                conn.execute(
                    "UPDATE item SET capture_time_corrected = ?, "
                    "tz_offset_seconds = ?, tz_source = ?, day_number = ? "
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

    def set_video_duration(
        self, video_item_id: str, duration_ms: int,
    ) -> None:
        """Stamp ``duration_ms`` onto a source video item — for callers
        who probed the duration after ingest (the workshop bar runs
        ffprobe + reads Qt's ``durationChanged`` when it lands on a
        video; ingest can leave the row NULL when ExifTool can't read
        ``duration_seconds`` for the format). No-op if the row already
        has a positive duration or the caller's value is non-positive.

        Without this backfill, every gateway mutator that REQUIRES
        ``video.duration_ms`` — ``add_video_marker``, ``move_video_marker``,
        ``segment_bounds`` — raises silently and the workshop's
        Marker / Snapshot / Remove / Toggle Status / Reset all feel
        dead to the user."""
        video = self._require_source_video(video_item_id)
        dur = int(duration_ms or 0)
        if dur <= 0:
            return
        if video.duration_ms and int(video.duration_ms) > 0:
            return
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE item SET duration_ms = ? WHERE id = ?",
                (dur, video_item_id))
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
        self, camera_id: str, *, offset_seconds: int,
        day_number: Optional[int] = None,
        day_numbers: Optional[Iterable[int]] = None,
    ) -> List[str]:
        """Re-derive ``capture_time_corrected`` for a camera's items from a new
        applied offset (spec/123 — integer SECONDS) — the virtual-EXIF
        replacement for the legacy in-place EXIF re-bake (G5). Shared by
        Camera-clocks (B1) and Adjust-TZ (B2).

        For EVERY captured item of ``camera_id`` (photos AND videos —
        optionally only those on ``day_number``, or restricted to a set of
        ``day_numbers``): ``corrected = raw + offset`` (raw never touched,
        G5); ``tz_offset_seconds`` ← the new offset; ``tz_source`` ←
        ``'user_declared'``; ``day_number`` reassigned from the corrected
        date against the plan (smallest-day-number-wins on a duplicate
        date). On a corrected date with NO planned day, the item's
        day_number is set to ``None`` (the natural / undated bucket) —
        never silently kept at the stale pre-correction day.

        spec/127 — pass ``day_numbers`` to scope a recompute to one
        trip-TZ segment's days in a single transaction (the unified
        correction dialog uses this so a camera spanning two segments
        gets its right offset in each). Mutually exclusive with
        ``day_number`` (a single int); passing both raises ``ValueError``.

        Items with no raw timestamp are skipped. Returns the affected item
        ids; downstream marks are flagged ``derived_dirty`` (G4)."""
        if day_number is not None and day_numbers is not None:
            raise ValueError(
                "recompute_corrected_times: pass day_number OR day_numbers, "
                "not both")
        day_filter: Optional[Set[int]] = None
        if day_numbers is not None:
            day_filter = {int(d) for d in day_numbers}
            if not day_filter:
                # An empty set is "scope to nothing" — return without
                # touching anything.
                return []
        offset = timedelta(seconds=offset_seconds)
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
                if day_filter is not None and it.day_number not in day_filter:
                    continue
                if not it.capture_time_raw:
                    continue
                try:
                    raw_dt = datetime.fromisoformat(it.capture_time_raw)
                except ValueError:
                    continue
                corrected_dt = raw_dt + offset
                new_corrected = corrected_dt.isoformat()
                # spec/123 — on a planned-date hit use that day; otherwise the
                # item lands in the natural/undated bucket. NEVER silently
                # keep the stale pre-correction day (was the GoPro
                # zero-correction bug: items missed reassignment).
                new_day = by_date.get(corrected_dt.date().isoformat())
                # tz_source enum aligned to camera_day_tz.source. A manual
                # recompute (the user dialed in an applied offset for one
                # camera) is a user-declared offset.
                self.store.conn.execute(
                    "UPDATE item SET capture_time_corrected = ?, tz_offset_seconds = ?, "
                    "tz_source = ?, day_number = ? WHERE id = ?",
                    (new_corrected, int(offset_seconds), "user_declared",
                     new_day, it.id),
                )
                affected.append(it.id)
            self._touch()

        if affected:
            for phase in _PHASES:
                self.mark_derived_dirty(phase, affected)
        return affected
