"""Video clip + snapshot virtual marks (F-029 step 2, Nelson
2026-05-26).

Per-bucket cull/select journals store K/D state for files. For
videos, the user also wants to define **clips** (time ranges) and
**snapshots** (single timestamps) within a video, each with its
own K/D state, *without* materialising any bytes during cull or
select. Bytes only get created at Process Export.

This module owns the journal-side data model + the
mutation/query helpers. Pure-Python; no Qt; no I/O. The
ingest_journal.json file format gains two top-level arrays:

    journal["clips"] = [
        {"id": "c1", "source": "DSC_0042.mp4",
         "start_ms": 5000, "end_ms": 12000,
         "state": "kept", "label": "yak crossing",
         "created_at": "2026-05-26T19:00:00"},
        ...
    ]
    journal["snapshots"] = [
        {"id": "s1", "source": "DSC_0042.mp4",
         "time_ms": 8500, "state": "kept",
         "created_at": "2026-05-26T19:00:01"},
        ...
    ]

Both arrays are **additive** — old journals without these keys
keep working unchanged. The fields:

* ``id`` — monotonic per-source identifier (``c1``, ``c2``, …
  for clips; ``s1``, ``s2``, … for snapshots). Stable across
  edits — the user can re-trim a clip without changing its id.
  Used by the Process journal as the join key for transform
  overrides (crop_norm / rotation / mute) and by lineage
  records for materialised outputs.
* ``source`` — basename of the source video in the bucket
  journal. Redundant in the common case (one video per
  bucket) but kept for explicitness so the array is
  self-describing if the schema ever supports multi-source
  buckets.
* ``start_ms`` / ``end_ms`` / ``time_ms`` — integer
  milliseconds from the start of the source video.
* ``state`` — ``STATE_KEPT`` or ``STATE_DISCARDED``. Same
  vocabulary the per-file ``marks`` map uses, so consumers
  can branch identically.
* ``label`` (clips only, optional) — user-supplied free text
  for "yak crossing" / "sunrise pan" sort of names. Empty
  string when not set.
* ``created_at`` — ISO timestamp, for debugging + history
  ordering.

The silent-sync rule (F-029 step 4) consults
:func:`has_any_kept_derivative` to decide whether to hardlink
the source video into the next phase — a clip-kept-but-whole-
video-discarded video still needs its source on disk so Process
Export can extract from it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from core.cull_state import STATE_DISCARDED, STATE_KEPT

log = logging.getLogger(__name__)


# Top-level journal keys.
CLIPS_KEY = "clips"
SNAPSHOTS_KEY = "snapshots"

# Stable monotonic id prefixes — keeps clip ids and snapshot ids
# distinct so a future cross-mark lookup table can use the id
# alone as a key.
_CLIP_ID_PREFIX = "c"
_SNAPSHOT_ID_PREFIX = "s"


@dataclass(frozen=True)
class Clip:
    """Read-only view of one clip entry. Construct via
    :func:`list_clips` — callers shouldn't build these directly.

    ``source_duration_ms`` (F-034, Nelson 2026-05-28): the source
    video's total duration in milliseconds, stamped at clip-create
    time so downstream rollups can compute a time-weighted "Kept"
    contribution (5-second clip from a 60-second video = 1/12 of
    a unit, not 1 unit). ``0`` for legacy entries (created before
    the field existed) — rollup callers fall back to binary
    1-unit-per-derivative accounting when the duration is missing,
    so pre-F-034 journals keep their old display semantics."""
    id: str
    source: str
    start_ms: int
    end_ms: int
    state: str
    label: str = ""
    created_at: str = ""
    source_duration_ms: int = 0

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    @property
    def is_kept(self) -> bool:
        return self.state == STATE_KEPT


@dataclass(frozen=True)
class Snapshot:
    """Read-only view of one snapshot entry.

    ``source_duration_ms`` (F-034): same role as on :class:`Clip`
    — the source video's total duration for time-weighted rollup
    math. ``0`` for legacy entries; degrades to binary weighting."""
    id: str
    source: str
    time_ms: int
    state: str
    created_at: str = ""
    source_duration_ms: int = 0

    @property
    def is_kept(self) -> bool:
        return self.state == STATE_KEPT


# ── Schema parsing ────────────────────────────────────────────


def list_clips(journal: dict) -> list[Clip]:
    """Parse the journal's clip array, dropping malformed entries.
    Returns clips in their stored order (= creation order)."""
    raw = journal.get(CLIPS_KEY) or []
    if not isinstance(raw, list):
        return []
    out: list[Clip] = []
    for entry in raw:
        clip = _parse_clip(entry)
        if clip is not None:
            out.append(clip)
    return out


def list_snapshots(journal: dict) -> list[Snapshot]:
    """Parse the journal's snapshot array, dropping malformed
    entries. Returns snapshots in stored order."""
    raw = journal.get(SNAPSHOTS_KEY) or []
    if not isinstance(raw, list):
        return []
    out: list[Snapshot] = []
    for entry in raw:
        snap = _parse_snapshot(entry)
        if snap is not None:
            out.append(snap)
    return out


def _parse_clip(entry) -> Optional[Clip]:        # noqa: ANN001
    if not isinstance(entry, dict):
        return None
    try:
        clip_id = str(entry["id"])
        source = str(entry["source"])
        start_ms = int(entry["start_ms"])
        end_ms = int(entry["end_ms"])
        state = str(entry["state"])
    except (KeyError, TypeError, ValueError):
        return None
    if not clip_id or not source:
        return None
    if state not in (STATE_KEPT, STATE_DISCARDED):
        return None
    if end_ms < start_ms:
        # Defensive: a backwards range is nonsense; drop it
        # rather than expose a negative duration to callers.
        return None
    # source_duration_ms is optional (F-034); legacy entries lack
    # the field and read back as 0 → rollup fallback to binary.
    try:
        source_duration_ms = int(entry.get("source_duration_ms", 0) or 0)
        if source_duration_ms < 0:
            source_duration_ms = 0
    except (TypeError, ValueError):
        source_duration_ms = 0
    return Clip(
        id=clip_id, source=source,
        start_ms=start_ms, end_ms=end_ms,
        state=state,
        label=str(entry.get("label", "") or ""),
        created_at=str(entry.get("created_at", "") or ""),
        source_duration_ms=source_duration_ms,
    )


def _parse_snapshot(entry) -> Optional[Snapshot]:  # noqa: ANN001
    if not isinstance(entry, dict):
        return None
    try:
        snap_id = str(entry["id"])
        source = str(entry["source"])
        time_ms = int(entry["time_ms"])
        state = str(entry["state"])
    except (KeyError, TypeError, ValueError):
        return None
    if not snap_id or not source:
        return None
    if state not in (STATE_KEPT, STATE_DISCARDED):
        return None
    try:
        source_duration_ms = int(entry.get("source_duration_ms", 0) or 0)
        if source_duration_ms < 0:
            source_duration_ms = 0
    except (TypeError, ValueError):
        source_duration_ms = 0
    return Snapshot(
        id=snap_id, source=source, time_ms=time_ms,
        state=state,
        created_at=str(entry.get("created_at", "") or ""),
        source_duration_ms=source_duration_ms,
    )


# ── Id allocation ─────────────────────────────────────────────


def _next_clip_id(journal: dict, source: str) -> str:
    """Pick the next monotonic clip id for ``source``. Format:
    ``c<N>``. Counts existing clips (any source) to ensure the
    id is unique within the journal — multi-source buckets are
    rare but the count-across-all approach makes the id
    journal-scoped, not source-scoped, which is simpler."""
    return _next_id(journal, CLIPS_KEY, _CLIP_ID_PREFIX)


def _next_snapshot_id(journal: dict, source: str) -> str:
    return _next_id(journal, SNAPSHOTS_KEY, _SNAPSHOT_ID_PREFIX)


def _next_id(
    journal: dict, list_key: str, prefix: str,
) -> str:
    """Find the highest numeric suffix for ``prefix`` in the
    journal's ``list_key`` array, then return ``prefix<N+1>``.
    Falls back to ``prefix1`` for an empty / malformed array."""
    raw = journal.get(list_key) or []
    if not isinstance(raw, list):
        return f"{prefix}1"
    max_n = 0
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        raw_id = entry.get("id")
        if not isinstance(raw_id, str) or not raw_id.startswith(prefix):
            continue
        try:
            n = int(raw_id[len(prefix):])
        except ValueError:
            continue
        if n > max_n:
            max_n = n
    return f"{prefix}{max_n + 1}"


# ── Mutators ─────────────────────────────────────────────────


def add_clip(
    journal: dict,
    source: str,
    start_ms: int,
    end_ms: int,
    *,
    state: str = STATE_KEPT,
    label: str = "",
    source_duration_ms: int = 0,
    clip_id: Optional[str] = None,
) -> Clip:
    """Allocate a new clip id and append the clip to the
    journal's ``clips`` array. Returns the new :class:`Clip`.

    ``source_duration_ms`` (F-034, Nelson 2026-05-28): the source
    video's total duration in milliseconds. Callers that have it
    (the video player always does — duration is in the player
    state by the time a clip is being created) should pass it so
    downstream rollups can compute time-weighted Kept
    contributions. Default ``0`` is the legacy / unknown sentinel;
    rollup math falls back to binary 1-unit-per-derivative for
    such entries.

    ``clip_id`` (docs/24 follow-up, Nelson 2026-05-28): override
    the auto-allocated id with an explicit one. Used by the
    Select-journal upsert path so a clip seeded from a Cull
    definition keeps the Cull lineage id (``c1``, ``c2``, …) —
    same id Process consumes downstream. Default ``None`` keeps
    the legacy auto-allocate behaviour for create-time callers.

    Raises ``ValueError`` for a backwards range or an unknown
    state. Mutates the journal dict in place — caller is
    responsible for persisting via
    :func:`core.ingest_session.save_ingest_journal`.
    """
    if end_ms < start_ms:
        raise ValueError(
            f"clip end_ms ({end_ms}) < start_ms ({start_ms})")
    if state not in (STATE_KEPT, STATE_DISCARDED):
        raise ValueError(f"unknown clip state: {state!r}")
    clips = journal.setdefault(CLIPS_KEY, [])
    if not isinstance(clips, list):
        clips = []
        journal[CLIPS_KEY] = clips
    if clip_id is None:
        clip_id = _next_clip_id(journal, source)
    dur = max(0, int(source_duration_ms))
    entry = {
        "id": clip_id,
        "source": str(source),
        "start_ms": int(start_ms),
        "end_ms": int(end_ms),
        "state": str(state),
        "label": str(label or ""),
        "created_at": datetime.now().isoformat(
            timespec="microseconds"),
        "source_duration_ms": dur,
    }
    clips.append(entry)
    return Clip(
        id=clip_id, source=str(source),
        start_ms=int(start_ms), end_ms=int(end_ms),
        state=str(state), label=str(label or ""),
        created_at=entry["created_at"],
        source_duration_ms=dur,
    )


def add_snapshot(
    journal: dict,
    source: str,
    time_ms: int,
    *,
    state: str = STATE_KEPT,
    source_duration_ms: int = 0,
) -> Snapshot:
    """Allocate a new snapshot id and append to the journal's
    ``snapshots`` array. Defaults to KEPT per Nelson 2026-05-26
    ("auto-keep" — a snapshot the user took the trouble to
    create is implicitly worth keeping).

    ``source_duration_ms`` (F-034): the source video's total
    duration. Same role + same default as on :func:`add_clip` —
    stamped at create-time so the rollup math can compute the
    snapshot's time-weighted contribution. ``0`` = legacy /
    unknown; rollup falls back to binary."""
    if state not in (STATE_KEPT, STATE_DISCARDED):
        raise ValueError(f"unknown snapshot state: {state!r}")
    snaps = journal.setdefault(SNAPSHOTS_KEY, [])
    if not isinstance(snaps, list):
        snaps = []
        journal[SNAPSHOTS_KEY] = snaps
    snap_id = _next_snapshot_id(journal, source)
    dur = max(0, int(source_duration_ms))
    entry = {
        "id": snap_id,
        "source": str(source),
        "time_ms": int(time_ms),
        "state": str(state),
        "created_at": datetime.now().isoformat(
            timespec="microseconds"),
        "source_duration_ms": dur,
    }
    snaps.append(entry)
    return Snapshot(
        id=snap_id, source=str(source), time_ms=int(time_ms),
        state=str(state), created_at=entry["created_at"],
        source_duration_ms=dur,
    )


def update_clip_state(
    journal: dict, clip_id: str, state: str,
) -> bool:
    """Set ``state`` on the clip with ``clip_id``. Returns
    ``True`` iff a matching clip was found + updated. Raises
    ``ValueError`` for an unknown state value."""
    if state not in (STATE_KEPT, STATE_DISCARDED):
        raise ValueError(f"unknown clip state: {state!r}")
    return _set_field(journal, CLIPS_KEY, clip_id, "state", state)


def update_clip_bounds(
    journal: dict, clip_id: str, start_ms: int, end_ms: int,
) -> bool:
    """Re-trim a clip's time range without changing its id.
    Returns ``True`` iff updated."""
    if end_ms < start_ms:
        raise ValueError(
            f"clip end_ms ({end_ms}) < start_ms ({start_ms})")
    for entry in journal.get(CLIPS_KEY) or []:
        if isinstance(entry, dict) and entry.get("id") == clip_id:
            entry["start_ms"] = int(start_ms)
            entry["end_ms"] = int(end_ms)
            return True
    return False


def update_clip_label(
    journal: dict, clip_id: str, label: str,
) -> bool:
    """Rename a clip's user-visible label."""
    return _set_field(
        journal, CLIPS_KEY, clip_id, "label", str(label or ""))


def update_snapshot_state(
    journal: dict, snap_id: str, state: str,
) -> bool:
    if state not in (STATE_KEPT, STATE_DISCARDED):
        raise ValueError(f"unknown snapshot state: {state!r}")
    return _set_field(
        journal, SNAPSHOTS_KEY, snap_id, "state", state)


def update_snapshot_time(
    journal: dict, snap_id: str, time_ms: int,
) -> bool:
    """Move a snapshot's timestamp without changing its id."""
    return _set_field(
        journal, SNAPSHOTS_KEY, snap_id, "time_ms", int(time_ms))


def remove_clip(journal: dict, clip_id: str) -> bool:
    """Drop a clip from the journal. Returns ``True`` iff
    removed."""
    return _remove(journal, CLIPS_KEY, clip_id)


def remove_snapshot(journal: dict, snap_id: str) -> bool:
    return _remove(journal, SNAPSHOTS_KEY, snap_id)


def _set_field(
    journal: dict, list_key: str, item_id: str,
    field: str, value,                                # noqa: ANN001
) -> bool:
    for entry in journal.get(list_key) or []:
        if isinstance(entry, dict) and entry.get("id") == item_id:
            entry[field] = value
            return True
    return False


def _remove(journal: dict, list_key: str, item_id: str) -> bool:
    items = journal.get(list_key)
    if not isinstance(items, list):
        return False
    for i, entry in enumerate(items):
        if isinstance(entry, dict) and entry.get("id") == item_id:
            del items[i]
            return True
    return False


# ── Queries used by silent-sync (Step 4) ─────────────────────


def has_any_kept_derivative(journal: dict, source: str) -> bool:
    """True iff *anything* derived from ``source`` is kept:

      * ``marks[source] == STATE_KEPT`` (the whole video is
        kept), OR
      * any clip with ``source == source`` has ``state == KEPT``,
        OR
      * any snapshot with ``source == source`` has
        ``state == KEPT``.

    Drives the F-029 step 4 silent-sync rule: a video gets
    hardlinked into the next phase iff this returns ``True``.
    Even when the whole-video mark is DISCARDED, the source
    must be present on disk so Process Export can later extract
    the kept clips / snapshots from it.
    """
    marks = journal.get("marks") or {}
    if isinstance(marks, dict) and marks.get(source) == STATE_KEPT:
        return True
    for clip in list_clips(journal):
        if clip.source == source and clip.is_kept:
            return True
    for snap in list_snapshots(journal):
        if snap.source == source and snap.is_kept:
            return True
    return False


def kept_clips_for(journal: dict, source: str) -> list[Clip]:
    """All clips for ``source`` whose state is KEPT, in
    creation order."""
    return [
        c for c in list_clips(journal)
        if c.source == source and c.is_kept
    ]


def apply_state_to_all_derivatives(
    journal: dict, state: str,
) -> int:
    """Bulk-flip the ``state`` field on EVERY clip and EVERY
    snapshot in ``journal`` to ``state``. Returns the count of
    entries actually mutated (entries already at ``state`` aren't
    re-written; the count is a "what changed" metric, not a
    "what's in scope" metric).

    Definitions are **preserved** — only the state field flips.
    The definitions-deleting sibling for the user-facing
    Reset-all batch op is to wipe the journal_root entirely.

    Drives F-032 Spec A (Nelson 2026-05-27, frozen in
    docs/18 §"Video batch ops — asymmetric Keep / Discard /
    Reset"): Keep-all flips all derivatives to KEPT so the
    next silent-sync (after a Reopen) still hardlinks the raw
    video forward; Discard-all flips them to DISCARDED so the
    next silent-sync correctly drops the raw video. The
    asymmetry is intentional — Keep / Discard are reversible
    via Reopen + the preserved definitions; Reset is the
    nuclear option.

    Pure mutation — caller persists via ``save_ingest_journal``.
    Never raises; a malformed journal degrades to zero mutations.
    """
    if state not in (STATE_KEPT, STATE_DISCARDED):
        raise ValueError(f"unknown derivative state: {state!r}")
    mutated = 0
    clips = journal.get(CLIPS_KEY)
    if isinstance(clips, list):
        for entry in clips:
            if (isinstance(entry, dict)
                    and entry.get("state") != state):
                entry["state"] = state
                mutated += 1
    snaps = journal.get(SNAPSHOTS_KEY)
    if isinstance(snaps, list):
        for entry in snaps:
            if (isinstance(entry, dict)
                    and entry.get("state") != state):
                entry["state"] = state
                mutated += 1
    return mutated


def kept_snapshots_for(
    journal: dict, source: str,
) -> list[Snapshot]:
    return [
        s for s in list_snapshots(journal)
        if s.source == source and s.is_kept
    ]
