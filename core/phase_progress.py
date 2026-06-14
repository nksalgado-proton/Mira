"""Per-day per-phase progress cache (Nelson frozen 2026-05-20 v5).

The Event-Plan status table renders one cell per (day × phase). Each
cell shows three things at a glance:

* a **color** (gray / amber / green) — derived from the exported
  fraction;
* an optional **digit** ("X/Y") — exported vs total bucket counts;
* an optional **F overlay** — user-declared "this phase is done"
  (see :func:`is_frozen`, stored separately).

This module owns the per-day-per-phase progress cache that drives
the color + digit. Auto-derivation from the bucket journals or the
filesystem is expensive (per-day scan + per-bucket EXIF reads on a
Nepal-class trip = seconds per render). Instead we cache a tiny
rollup inside the Event JSON's ``event_settings`` block — written
by each Export commit at the natural moment, read by the dashboard
in O(1).

Schema::

    event.event_settings["phase_progress"] = {
        "cull":   {"1": {"total_buckets": 4, "exported_buckets": 4,
                         "kept_buckets": 3}, ...},
        "pick": {...},
        "process": {...},
        ...
    }

Each phase keys a sub-dict from ``str(day_number)`` (JSON keys
must be strings) to a 3-field summary. Days not yet touched are
absent — callers default to a zero-progress reading.

A separate event_settings entry stores **user freezes** (the F
overlay):

    event.event_settings["day_status_overrides"] = {
        "1:cull": "frozen",
        "1:select": "frozen",
        ...
    }

The freeze is independent of the auto-progress: the base color
still reflects what the cache says about reality; the F overlay
shows that the user has declared "this is done — leave the signal
alone even if the cache later disagrees". Reversible.

Qt-free. Pure dicts in / Event out.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

from core.models import Event, TripDay

log = logging.getLogger(__name__)


# Top-level event_settings keys.
PHASE_PROGRESS_KEY = "phase_progress"
DAY_STATUS_OVERRIDES_KEY = "day_status_overrides"

# Phase identifiers — match the dashboard column keys.
PHASE_CULL = "cull"
PHASE_PICK = "pick"
PHASE_PROCESS = "process"
PHASE_CURATE = "curate"
PHASE_DISTRIBUTE = "distribute"

# All phases the cache understands (Plan / Cap are derived
# differently — Plan from event.trip_days, Cap from the
# 00 - Captured filesystem walk; both stay out of the cache).
KNOWN_PHASES: frozenset[str] = frozenset({
    PHASE_CULL, PHASE_PICK, PHASE_PROCESS,
    PHASE_CURATE, PHASE_DISTRIBUTE,
})


@dataclass(frozen=True)
class PhaseProgress:
    """One day's progress for one phase. ``total_buckets`` = the
    number of buckets the user would need to act on for this day to
    be considered fully done; ``exported_buckets`` and
    ``kept_buckets`` are the per-bucket signals."""
    total_buckets: int = 0
    exported_buckets: int = 0
    kept_buckets: int = 0

    @property
    def is_empty(self) -> bool:
        return self.total_buckets == 0

    @property
    def is_complete(self) -> bool:
        return (
            self.total_buckets > 0
            and self.exported_buckets >= self.total_buckets
        )

    @property
    def exported_fraction(self) -> float:
        """0.0 when nothing exported; 1.0 when all buckets exported."""
        if self.total_buckets <= 0:
            return 0.0
        return self.exported_buckets / self.total_buckets


# ── Reading the cache ─────────────────────────────────────────


def read_phase_progress(
    event: Event, phase: str, day_number: int,
) -> PhaseProgress:
    """Look up a (phase, day) pair in the cache. Returns an empty
    PhaseProgress (zero totals, ``is_empty == True``) when no entry
    exists — callers can treat that as "not started yet"."""
    block = (event.event_settings or {}).get(PHASE_PROGRESS_KEY, {})
    phase_block = block.get(phase, {}) if isinstance(block, dict) else {}
    if not isinstance(phase_block, dict):
        return PhaseProgress()
    entry = phase_block.get(str(day_number))
    if not isinstance(entry, dict):
        return PhaseProgress()
    return PhaseProgress(
        total_buckets=int(entry.get("total_buckets", 0) or 0),
        exported_buckets=int(entry.get("exported_buckets", 0) or 0),
        kept_buckets=int(entry.get("kept_buckets", 0) or 0),
    )


def all_phase_progress(
    event: Event, phase: str,
) -> dict[int, PhaseProgress]:
    """Whole-phase view — every day this event has cached entries
    for. Returns ``{day_number: PhaseProgress}``."""
    block = (event.event_settings or {}).get(PHASE_PROGRESS_KEY, {})
    phase_block = block.get(phase, {}) if isinstance(block, dict) else {}
    if not isinstance(phase_block, dict):
        return {}
    out: dict[int, PhaseProgress] = {}
    for k, v in phase_block.items():
        try:
            day_number = int(k)
        except (TypeError, ValueError):
            continue
        if not isinstance(v, dict):
            continue
        out[day_number] = PhaseProgress(
            total_buckets=int(v.get("total_buckets", 0) or 0),
            exported_buckets=int(v.get("exported_buckets", 0) or 0),
            kept_buckets=int(v.get("kept_buckets", 0) or 0),
        )
    return out


# ── Phase status (Done / Ready / In Progress / Not Started) ───
#
# Centralises the rule the EventDashboardPage's `_update_progress_
# status` codified in B-018 (Nelson 2026-05-25): "A day with no
# phase_progress entry no longer counts as incomplete — it counts
# as no work to do for that day, a valid end-state."
#
# Promoted to a top-level helper here so every recap surface
# (EventPlanPage's 2×2 overview, EventCard's funnel hint, future
# notification surfaces) can read the same authoritative status
# without re-deriving the rule. Source of truth: phase_progress
# cache + F-overlay frozen-days, NOT the on-disk folder tree.


def phase_status_for(
    event: Event,
    phase_key: str,
    *,
    event_root: Optional[Path] = None,
) -> str:
    """Single-source-of-truth phase status. Returns one of
    ``STATUS_NOT_STARTED`` / ``STATUS_READY`` / ``STATUS_IN_PROGRESS``
    / ``STATUS_DONE`` (matching the constants in
    :mod:`core.event_card_grid`).

    Phase keys understood:

      * ``"plan"`` — DONE when the event has any trip_days, else
        NOT_STARTED. The plan is "done" the moment it exists.
      * ``"capture"`` — derived from the on-disk ``00 - Captured/``
        tree (the capture phase has no journal cache). Counts
        per-day camera contributions: every planned day has at
        least one camera → DONE; some days → IN_PROGRESS; none →
        READY. Plan with zero days → NOT_STARTED. Requires the
        ``event_root`` arg (Capture's only signal lives on disk);
        without it we return NOT_STARTED defensively.
      * ``"cull"`` / ``"pick"`` / ``"process"`` / ``"curate"`` /
        ``"distribute"`` — cache-driven. See the docstring on
        ``ui.pages.event_dashboard.EventDashboardPage._update_
        progress_status`` for the rule lineage; this function
        reproduces it verbatim so the badge on the PhaseButton and
        the recap surfaces stay in lockstep.

    Defensive on every read — never raises, even with malformed
    cache data. Unknown ``phase_key`` returns NOT_STARTED.
    """
    # Lazy import to avoid a circular import between phase_progress
    # ↔ event_card_grid (the latter imports STATUS_* into its own
    # public API).
    from core.event_card_grid import (
        STATUS_DONE, STATUS_IN_PROGRESS,
        STATUS_NOT_STARTED, STATUS_READY,
    )

    day_count = len(event.trip_days or [])
    if day_count == 0:
        return STATUS_NOT_STARTED

    if phase_key == "plan":
        # The plan exists → "done." There's no per-day completion
        # signal for Plan; the act of having trip_days IS the plan.
        return STATUS_DONE

    if phase_key == "capture":
        if event_root is None:
            return STATUS_NOT_STARTED
        # Mirror EventDashboardPage._render_capture_button: a day
        # counts as "captured" when at least one camera has a
        # photo file under 00 - Captured for that day.
        try:
            from ui.pages.day_status_table import DayStatusTable
            counts = DayStatusTable._captured_camera_counts_by_day(
                event_root, list(event.trip_days))
        except Exception:                          # noqa: BLE001
            log.exception(
                "phase_status_for(capture): camera-count lookup failed")
            return STATUS_NOT_STARTED
        days_with_files = sum(1 for c in counts.values() if c > 0)
        if days_with_files == 0:
            return STATUS_READY
        if days_with_files < day_count:
            return STATUS_IN_PROGRESS
        return STATUS_DONE

    if phase_key not in KNOWN_PHASES:
        return STATUS_NOT_STARTED

    # Cache-driven phases (cull / select / process / curate /
    # distribute). The B-018 rule:
    #   READY        — nothing has been touched
    #   IN_PROGRESS  — ≥1 partial day, no user-freeze rescue
    #   DONE         — touched days are all complete; no partials
    progress = all_phase_progress(event, phase_key)
    complete_days = sum(
        1 for day_n, p in progress.items()
        if p.is_complete or is_frozen(event, day_n, phase_key)
    )
    partial_days = sum(
        1 for day_n, p in progress.items()
        if (not p.is_complete and p.exported_buckets > 0)
        and not is_frozen(event, day_n, phase_key)
    )
    any_touched = complete_days + partial_days > 0
    if not any_touched:
        return STATUS_READY
    if partial_days > 0:
        return STATUS_IN_PROGRESS
    return STATUS_DONE


def is_phase_done(
    event: Event,
    phase_key: str,
    *,
    event_root: Optional[Path] = None,
) -> bool:
    """Convenience wrapper: ``phase_status_for(...) == STATUS_DONE``."""
    from core.event_card_grid import STATUS_DONE
    return phase_status_for(
        event, phase_key, event_root=event_root) == STATUS_DONE


# ── Writing the cache ─────────────────────────────────────────


def write_phase_progress(
    event: Event,
    phase: str,
    day_number: int,
    progress: PhaseProgress,
) -> None:
    """Stamp ``progress`` onto ``event.event_settings`` in-place.

    Caller is responsible for persisting the event via
    ``data.event_store.save_event(event)`` — this function MUTATES
    the Event but does not write to disk, so a single Export commit
    can write progress for multiple days and persist once at the end.
    Raises ``ValueError`` for an unknown phase.
    """
    if phase not in KNOWN_PHASES:
        raise ValueError(
            f"unknown phase {phase!r}; expected one of "
            f"{sorted(KNOWN_PHASES)}"
        )
    settings = event.event_settings or {}
    block = settings.get(PHASE_PROGRESS_KEY, {})
    if not isinstance(block, dict):
        block = {}
    phase_block = block.get(phase, {})
    if not isinstance(phase_block, dict):
        phase_block = {}
    phase_block[str(day_number)] = {
        "total_buckets": int(progress.total_buckets),
        "exported_buckets": int(progress.exported_buckets),
        "kept_buckets": int(progress.kept_buckets),
    }
    block[phase] = phase_block
    settings[PHASE_PROGRESS_KEY] = block
    event.event_settings = settings


def write_phase_progress_bulk(
    event: Event,
    phase: str,
    by_day: dict[int, PhaseProgress],
) -> None:
    """Batch-write progress for many days of one phase (Export
    commits typically cover multiple days at once)."""
    for day_number, progress in by_day.items():
        write_phase_progress(event, phase, day_number, progress)


def clear_phase_progress(event: Event, phase: str) -> None:
    """Drop the cached progress for ``phase`` entirely. Useful when
    recomputing from disk: clear, then bulk-write the fresh totals."""
    settings = event.event_settings or {}
    block = settings.get(PHASE_PROGRESS_KEY, {})
    if isinstance(block, dict) and phase in block:
        block.pop(phase, None)
        settings[PHASE_PROGRESS_KEY] = block
        event.event_settings = settings


# ── User-freeze (F overlay) ──────────────────────────────────


def _override_key(day_number: int, phase: str) -> str:
    return f"{day_number}:{phase}"


def is_frozen(event: Event, day_number: int, phase: str) -> bool:
    """True iff the user has explicitly declared (day, phase) done.
    Independent of auto-progress — the cell shows both signals."""
    overrides = (event.event_settings or {}).get(
        DAY_STATUS_OVERRIDES_KEY, {})
    if not isinstance(overrides, dict):
        return False
    return overrides.get(_override_key(day_number, phase)) == "frozen"


def freeze(event: Event, day_number: int, phase: str) -> None:
    """Mark (day, phase) as user-declared done. Mirrors the
    bucket-level ``reviewed`` flag (docs/18 §"Culling contexts" —
    frozen 2026-05-17 Nelson, "mark done / reopen" affordance,
    reversible). Caller saves the Event."""
    settings = event.event_settings or {}
    overrides = settings.get(DAY_STATUS_OVERRIDES_KEY, {})
    if not isinstance(overrides, dict):
        overrides = {}
    overrides[_override_key(day_number, phase)] = "frozen"
    settings[DAY_STATUS_OVERRIDES_KEY] = overrides
    event.event_settings = settings


def reopen(event: Event, day_number: int, phase: str) -> None:
    """Clear the user-frozen flag for (day, phase). Reversible —
    the user can freeze again later. Auto-detection takes over once
    this returns."""
    settings = event.event_settings or {}
    overrides = settings.get(DAY_STATUS_OVERRIDES_KEY, {})
    if not isinstance(overrides, dict):
        return
    overrides.pop(_override_key(day_number, phase), None)
    settings[DAY_STATUS_OVERRIDES_KEY] = overrides
    event.event_settings = settings


def summarize_export(
    buckets: Iterable["BucketExportInput"],   # type: ignore[name-defined]
    trip_days: Sequence[TripDay],
) -> dict[int, PhaseProgress]:
    """Per-day rollup from one Export run's bucket scope.

    For each day in the export scope, count:

    * ``total_buckets`` = number of buckets the user worked across
      for that day (the scope passed to gather);
    * ``kept_buckets`` = subset with at least one Kept mark in their
      journal — the "user has decided to keep something here" signal;
    * ``exported_buckets`` = same as ``kept_buckets`` in this run
      (Export sends every kept-bearing bucket in scope). Tracked
      separately so future incremental Exports can refine the
      semantics.

    The bucket's ``day_label`` is matched against the canonical
    ``day_folder_name`` for each TripDay — derived once via
    ``core.path_builder.day_folder_name`` so labels stay in sync
    with how the navigator builds them. ``buckets`` items whose
    label doesn't match any plan day are ignored (they'd be
    quarantine-equivalent at the day level).
    """
    from collections import defaultdict
    from core.cull_state import STATE_KEPT, state_counts
    from core.path_builder import day_folder_name

    label_to_num: dict[str, int] = {}
    for d in trip_days:
        label_to_num[day_folder_name(d)] = d.day_number

    by_day_total: dict[int, int] = defaultdict(int)
    by_day_kept: dict[int, int] = defaultdict(int)
    for b in buckets:
        day_num = label_to_num.get(getattr(b, "day_label", ""))
        if day_num is None:
            continue
        by_day_total[day_num] += 1
        # Count buckets with at least one Kept mark.
        try:
            counts = state_counts(
                b.journal, (p.name for p in b.files),
            )
        except Exception:                                # noqa: BLE001
            counts = {}
        if counts.get(STATE_KEPT, 0) > 0:
            by_day_kept[day_num] += 1

    return {
        day_num: PhaseProgress(
            total_buckets=by_day_total[day_num],
            exported_buckets=by_day_kept.get(day_num, 0),
            kept_buckets=by_day_kept.get(day_num, 0),
        )
        for day_num in by_day_total
    }


def reconcile_phase_progress(event: Event) -> bool:
    """Gap-fill version of :func:`recompute_from_disk` — for every
    (phase, day) pair that has files on disk but NO cache entry,
    write a coarse 1/1 entry. Days that already carry a precise
    cache entry (from a live Export commit) are left untouched.

    This is the right behaviour for auto-reconciliation on event
    load (Nelson 2026-05-21 v3 — kill the manual "Refresh status"
    button). Precise per-export data (Cull camera counts, Curate
    decided / total ratios) survives; "I have files but no cache
    record" gets a green light; "I had 8/12 decided in Curate but
    only have files for 4 buckets on disk" stays at 8/12 (the user
    explicitly committed that state via Export — disk shrinking
    later is a separate problem the F-overlay 'reopen' addresses).

    Returns ``True`` when any cache entry was written (so the
    caller knows to save the event).
    """
    from core.path_builder import (
        CAPTURED_CAMERAS_SUBDIR,
        CAPTURED_OTHER_SUBDIR,
        CAPTURED_PHONES_SUBDIR,
        culled_dir,
        curated_dir,
        day_folder_name,
        distributed_dir,
        event_root_path,
        processed_dir,
        selected_dir,
    )

    if not event.trip_days:
        return False
    photos_base = event.photos_base_path or ""
    if not photos_base:
        return False
    from pathlib import Path
    event_root = Path(event_root_path(photos_base, event))
    if not event_root.is_dir():
        return False

    wrote_any = False

    def _has_existing(phase: str, day_num: int) -> bool:
        existing = read_phase_progress(event, phase, day_num)
        return existing.total_buckets > 0

    # Cull — per-camera layout. Only writes when no precise entry
    # already exists for the day.
    cull_root = culled_dir(event_root)
    if cull_root.is_dir():
        buckets = (
            CAPTURED_CAMERAS_SUBDIR,
            CAPTURED_PHONES_SUBDIR,
            CAPTURED_OTHER_SUBDIR,
        )
        for d in event.trip_days:
            if _has_existing(PHASE_CULL, d.day_number):
                continue
            day_folder = day_folder_name(d)
            cameras: set[str] = set()
            for bucket in buckets:
                day_dir = cull_root / bucket / day_folder
                if not day_dir.is_dir():
                    continue
                for cam_dir in day_dir.iterdir():
                    if not cam_dir.is_dir():
                        continue
                    if any(cam_dir.rglob("*")):
                        cameras.add(cam_dir.name)
            if cameras:
                write_phase_progress(
                    event, PHASE_CULL, d.day_number,
                    PhaseProgress(
                        total_buckets=len(cameras),
                        exported_buckets=len(cameras),
                        kept_buckets=len(cameras),
                    ),
                )
                wrote_any = True

    # Consolidated phases (Select / Process / Distribute):
    # ``<day>/<style>/<file>`` directly under the phase root.
    for phase, dir_fn in (
        (PHASE_PICK, selected_dir),
        (PHASE_PROCESS, processed_dir),
        (PHASE_DISTRIBUTE, distributed_dir),
    ):
        root = dir_fn(event_root)
        if not root.is_dir():
            continue
        for d in event.trip_days:
            if _has_existing(phase, d.day_number):
                continue
            day_dir = root / day_folder_name(d)
            if not day_dir.is_dir():
                continue
            if any(p.is_file() for p in day_dir.rglob("*")):
                write_phase_progress(
                    event, phase, d.day_number,
                    PhaseProgress(
                        total_buckets=1,
                        exported_buckets=1,
                        kept_buckets=1,
                    ),
                )
                wrote_any = True

    # Curate — bucket-then-day layout: ``04 - Curated/<bucket>/<day>/``.
    curate_root = curated_dir(event_root)
    if curate_root.is_dir():
        for d in event.trip_days:
            if _has_existing(PHASE_CURATE, d.day_number):
                continue
            day_folder = day_folder_name(d)
            day_has_file = False
            for bucket_dir in curate_root.iterdir():
                if not bucket_dir.is_dir():
                    continue
                bucket_day_dir = bucket_dir / day_folder
                if not bucket_day_dir.is_dir():
                    continue
                if any(p.is_file() for p in bucket_day_dir.rglob("*")):
                    day_has_file = True
                    break
            if day_has_file:
                write_phase_progress(
                    event, PHASE_CURATE, d.day_number,
                    PhaseProgress(
                        total_buckets=1,
                        exported_buckets=1,
                        kept_buckets=1,
                    ),
                )
                wrote_any = True

    return wrote_any


def recompute_from_disk(event: Event) -> None:
    """Tier 1 fallback — walk the event's phase folders and rebuild
    the ``phase_progress`` cache from scratch. Used by the
    EventPlanPage's "Refresh status" affordance when the cache has
    drifted from disk reality (e.g. the user manually edited a
    phase folder).

    The recompute is **coarser** than the Export-commit writes — we
    have no access to the bucket journals from disk alone, so:

    * For **Cull** (per-camera layout
      ``01-Culled/<bucket>/<day>/<camera>/<style>/<file>``) we count
      distinct camera subdirs across all buckets as the bucket
      count. ``exported`` and ``kept`` are both equal to the camera
      count (every contributing camera has at least one kept file
      since the layout was Export-written). This matches what the
      live Export hook writes for the common single-Export-run case.
    * For the **consolidated** phases (Select / Process / Curate /
      Distribute, layout ``<day>/<style>/<file>``) we record 1/1
      whenever any file exists for the day, and skip the day
      otherwise. Coarser, but honest: from disk alone we can't tell
      how many buckets were involved.

    Caller saves the event after calling this.
    """
    from core.path_builder import (
        CAPTURED_CAMERAS_SUBDIR,
        CAPTURED_OTHER_SUBDIR,
        CAPTURED_PHONES_SUBDIR,
        culled_dir,
        curated_dir,
        day_folder_name,
        distributed_dir,
        event_root_path,
        processed_dir,
        selected_dir,
    )

    if not event.trip_days:
        return
    photos_base = event.photos_base_path or ""
    if not photos_base:
        log.info(
            "recompute_from_disk: event %s has no photos_base_path; "
            "nothing to scan", event.id,
        )
        return
    from pathlib import Path
    event_root = Path(event_root_path(photos_base, event))
    if not event_root.is_dir():
        return

    # Clear every known phase first so deletions show up.
    for ph in KNOWN_PHASES:
        clear_phase_progress(event, ph)

    # Cull — per-camera layout.
    cull_root = culled_dir(event_root)
    if cull_root.is_dir():
        buckets = (
            CAPTURED_CAMERAS_SUBDIR,
            CAPTURED_PHONES_SUBDIR,
            CAPTURED_OTHER_SUBDIR,
        )
        for d in event.trip_days:
            day_folder = day_folder_name(d)
            cameras: set[str] = set()
            for bucket in buckets:
                day_dir = cull_root / bucket / day_folder
                if not day_dir.is_dir():
                    continue
                for cam_dir in day_dir.iterdir():
                    if not cam_dir.is_dir():
                        continue
                    if any(cam_dir.rglob("*")):
                        cameras.add(cam_dir.name)
            if cameras:
                write_phase_progress(
                    event, PHASE_CULL, d.day_number,
                    PhaseProgress(
                        total_buckets=len(cameras),
                        exported_buckets=len(cameras),
                        kept_buckets=len(cameras),
                    ),
                )

    # Consolidated phases (Select / Process / Distribute) — coarser
    # 1/1 signal per day. Layout: ``<day>/<style>/<file>`` directly
    # under the phase root.
    for phase, dir_fn in (
        (PHASE_PICK, selected_dir),
        (PHASE_PROCESS, processed_dir),
        (PHASE_DISTRIBUTE, distributed_dir),
    ):
        root = dir_fn(event_root)
        if not root.is_dir():
            continue
        for d in event.trip_days:
            day_dir = root / day_folder_name(d)
            if not day_dir.is_dir():
                continue
            if any(p.is_file() for p in day_dir.rglob("*")):
                write_phase_progress(
                    event, phase, d.day_number,
                    PhaseProgress(
                        total_buckets=1,
                        exported_buckets=1,
                        kept_buckets=1,
                    ),
                )

    # Curate — bucket-then-day layout (Nelson 2026-05-21):
    # ``04 - Curated/<bucket>/<day>/<file>`` where ``<bucket>`` is
    # one of "All-Time Best", "Short", "Medium", "Long",
    # "Compositions", "Collage Only", or any configured theme name.
    # Walk the bucket subdirs and aggregate per-day file presence
    # across them; a day "has Curate output" when ANY bucket holds
    # a file for that day.
    curate_root = curated_dir(event_root)
    if curate_root.is_dir():
        for d in event.trip_days:
            day_folder = day_folder_name(d)
            day_has_file = False
            for bucket_dir in curate_root.iterdir():
                if not bucket_dir.is_dir():
                    continue
                bucket_day_dir = bucket_dir / day_folder
                if not bucket_day_dir.is_dir():
                    continue
                if any(p.is_file() for p in bucket_day_dir.rglob("*")):
                    day_has_file = True
                    break
            if day_has_file:
                write_phase_progress(
                    event, PHASE_CURATE, d.day_number,
                    PhaseProgress(
                        total_buckets=1,
                        exported_buckets=1,
                        kept_buckets=1,
                    ),
                )


def frozen_pairs(event: Event) -> set[tuple[int, str]]:
    """All currently-frozen (day_number, phase) pairs — handy for
    bulk rendering."""
    overrides = (event.event_settings or {}).get(
        DAY_STATUS_OVERRIDES_KEY, {})
    if not isinstance(overrides, dict):
        return set()
    out: set[tuple[int, str]] = set()
    for k, v in overrides.items():
        if v != "frozen":
            continue
        try:
            d, ph = k.split(":", 1)
            out.add((int(d), ph))
        except (TypeError, ValueError):
            continue
    return out
