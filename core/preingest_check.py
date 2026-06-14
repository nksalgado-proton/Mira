"""Pre-ingest plan-confirm engine (F-019, frozen 2026-05-25).

Runs **before** any byte-copy from a source SD card or folder. For
every day the source carries photos for, produces a "verdict" the
UI dialog renders so the user can:

1. Edit the plan's day row (description / location / TZ) in place.
2. See the camera info and capture-time range pulled from EXIF.
3. See a set of TZ-sanity warnings (future-dated photos, photos
   suspiciously older than the trip, night-majority timestamps,
   stale gap to "now") + the existing TZ-mismatch detection
   (:mod:`core.tz_mismatch_detector`, the F-016 absorption point).
4. See per-brand "to fix this going forward, on your <Model>…"
   instructions from the brand profile.

The engine is **pure Python, Qt-free, off-thread safe**. It does
NOT execute the bake itself — it gathers offsets and the plan's
mutated trip-days for the dialog's Apply step to hand downstream.
The bake runs later, inside the existing offload pipeline, against
the destination files in ``00 - Captured/`` (see ``core.capture_bake.
bake_operations`` — the B-008 shared engine).

Spec: ``docs/18-culler-spec.md`` §"Pre-ingest plan-confirm dialog".
Scope confirmed Nelson 2026-05-25:

* Per-Model brand-tip lookup with a brand-wide ``_default``
  fallback (matches the EXIF Model string against
  :attr:`core.brand_profile.BrandProfile.tz_setting_instructions`).
* CameraClockDialog is suppressed downstream when the offsets this
  engine derives are written into ``event.event_settings
  ["camera_clocks"]`` — see the dialog's Apply handler.
* Wired into the **Capture phase only** for the first commit; the
  sidebar "Back up this card" and past-photos paths come later.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Sequence

from core.bucket_navigator_model import DayFolder
from core.day_assignment import UNDATED_LABEL
from core.fresh_source import SourceItem, group_items_to_days
from core.models import TripDay
from core.path_builder import day_folder_name

log = logging.getLogger(__name__)


# ── Tuning ─────────────────────────────────────────────────────────

# How far in the "future" (clock-of-this-machine) a timestamp must
# fall before we flag it. A 5-min slack absorbs the difference
# between camera and PC clocks set on different days.
_FUTURE_SLACK = timedelta(minutes=5)

# Older-than-trip threshold. If the median timestamp falls more
# than this many days before the trip's earliest planned day, the
# camera's clock is probably set years off (e.g. battery-flat reset
# to factory default 2018-01-01).
_TRIP_OLDER_DAYS = 365

# Night-majority fraction. If more than this fraction of the day's
# timestamps fall in [22:00, 06:00] local wall-clock, we suspect a
# TZ shift on a daylight-shooting trip.
_NIGHT_MAJORITY_THRESHOLD = 0.60

# "Stale gap" threshold — newest timestamp is more than this many
# days behind "now". Suggests the user is re-ingesting an already-
# culled card by mistake, or the camera's clock is years off.
_STALE_GAP_DAYS = 30


# ── Result shapes ──────────────────────────────────────────────────


@dataclass(frozen=True)
class TzWarning:
    """One TZ-sanity finding for a per-day verdict.

    ``kind`` is a stable identifier the dialog can switch on for
    colour / icon assignment; ``severity`` is the visual weight
    (high = orange/red banner, low = neutral yellow). ``message`` is
    the user-facing one-liner (pre-translated upstream by the dialog
    via ``tr()``; this engine emits English source strings).

    ``suggested_offset_hours`` is populated ONLY for the
    ``"tz_mismatch"`` kind — the offset the existing TZ-mismatch
    detector inferred from phone-pairing. The dialog renders a
    "Apply this offset?" chip on those warnings.
    """

    kind: str
    severity: str
    message: str
    suggested_offset_hours: Optional[float] = None


@dataclass(frozen=True)
class BrandTip:
    """The "how to fix on the camera body" instructions the dialog
    surfaces. ``camera_id`` is the EXIF Model string the engine
    looked up (passed through ``camera_id_for``); ``steps`` is the
    ordered list the dialog renders as bullets. ``source`` is
    informational — ``"model"`` when the user's body had its own
    entry, ``"_default"`` when the brand-wide fallback was used."""

    camera_id: str
    steps: tuple[str, ...]
    source: str  # "model" | "_default"


@dataclass(frozen=True)
class PerDayVerdict:
    """The engine's output for one day in the source.

    ``trip_day`` is the **plan row** the dialog renders editable —
    description / location / TZ all live here. ``file_paths`` is the
    set the user can correlate with the capture-time range. The
    warnings drive the inline banner; ``brand_tip`` is shown
    collapsed by default unless a TZ warning fires.
    """

    trip_day: TripDay
    file_paths: tuple[Path, ...]
    capture_time_range: Optional[tuple[datetime, datetime]]
    camera_make: str
    camera_model: str
    warnings: tuple[TzWarning, ...]
    brand_tip: Optional[BrandTip]


@dataclass(frozen=True)
class PreingestPlan:
    """The engine's full output for one source.

    ``days`` is in plan order (Dia 1, Dia 2, …). ``undated_files``
    is the trailing set the day-assignment couldn't pin to any
    planned day — surfaced by the dialog as a non-editable "these
    files have no known day" callout so the user can investigate
    before letting the bake run.
    """

    days: tuple[PerDayVerdict, ...] = ()
    undated_files: tuple[Path, ...] = ()


# ── Public API ─────────────────────────────────────────────────────


def build_preingest_plan(
    items: Sequence[SourceItem],
    trip_days: Sequence[TripDay],
    *,
    camera_make: str = "",
    camera_model: str = "",
    now: Optional[datetime] = None,
    brand_tip_resolver=None,
) -> PreingestPlan:
    """Build the dialog's input data from one source's items + the
    event plan.

    ``camera_make`` / ``camera_model`` are the dialog-level identity
    (Fast Culler is one-card-one-camera per docs/18 freeze
    2026-05-25 — so a single make/model applies to every per-day
    card). Pass empty strings if upstream didn't resolve them; the
    dialog will display "Camera: unknown".

    ``now`` is injectable for tests (frozen "today"). Defaults to
    :func:`datetime.now`.

    ``brand_tip_resolver`` is an injectable ``(make, model) ->
    Optional[BrandTip]`` so tests don't need to load the real
    asset JSONs. Defaults to :func:`load_brand_tip`.
    """
    if now is None:
        now = datetime.now()
    if brand_tip_resolver is None:
        brand_tip_resolver = load_brand_tip

    # Reuse fresh_source's existing day grouping — it already
    # handles None timestamps + Dia-N assignment + the trailing
    # Undated bucket. Pass an empty calibrations map so the items'
    # raw timestamps flow through unmodified (the engine is asking
    # "as captured, what days do these touch?" — TZ correction
    # comes *from* the dialog's user response, not before it).
    day_folders = group_items_to_days(items, trip_days, {})

    # Index trip-days by their canonical key (the folder name) so we
    # can find the plan row that matches each DayFolder.
    trip_by_key: dict[str, TripDay] = {
        day_folder_name(td): td for td in trip_days
    }

    # Build the per-day verdicts; collect undated files separately.
    plan_dates = [td.date for td in trip_days if td.date is not None]
    undated_paths: list[Path] = []
    verdicts: list[PerDayVerdict] = []
    brand_tip = (
        brand_tip_resolver(camera_make, camera_model)
        if (camera_make or camera_model) else None
    )

    for df in day_folders:
        if df.key == UNDATED_LABEL:
            undated_paths.extend(df.files)
            continue
        trip_day = trip_by_key.get(df.key)
        if trip_day is None:
            # Defensive — a day-folder key should match a TripDay
            # if it isn't UNDATED. Skip silently; the file count
            # mismatch will be obvious to the user.
            log.warning(
                "preingest: DayFolder %r has no matching TripDay",
                df.key,
            )
            continue
        day_files = tuple(df.files)
        day_timestamps = _timestamps_for(items, day_files)
        time_range = _range_of(day_timestamps)
        warnings = _build_warnings(
            day_timestamps, trip_day, plan_dates, now,
        )
        verdicts.append(PerDayVerdict(
            trip_day=trip_day,
            file_paths=day_files,
            capture_time_range=time_range,
            camera_make=camera_make,
            camera_model=camera_model,
            warnings=warnings,
            brand_tip=brand_tip,
        ))

    return PreingestPlan(
        days=tuple(verdicts),
        undated_files=tuple(undated_paths),
    )


def load_brand_tip(
    camera_make: str, camera_model: str,
) -> Optional[BrandTip]:
    """Resolve the brand tip for ``(make, model)`` using the existing
    brand-profile registry. Returns ``None`` for unknown brands or
    profiles that don't declare any tz instructions; the dialog
    hides the tip block in that case.

    Brand-method discipline: only the profile JSON knows what menu
    paths exist on a given body. Don't second-guess it here.
    """
    if not camera_make:
        return None
    from core.brand_profile import match_brand_profile_for_photo
    profile = match_brand_profile_for_photo({"Make": camera_make})
    if profile is None:
        return None
    steps = profile.tip_for_model(camera_model)
    if not steps:
        return None
    source = (
        "model" if camera_model in profile.tz_setting_instructions
        else "_default"
    )
    return BrandTip(
        camera_id=camera_model or camera_make,
        steps=tuple(steps),
        source=source,
    )


def operations_from_items(
    items: Sequence[SourceItem],
    verdict: PerDayVerdict,
    applied_offset_hours: float,
) -> list[tuple[Path, datetime]]:
    """Production helper — the dialog's Apply handler passes the
    full ``items`` list (which carries per-file timestamps) along
    with the verdict + chosen offset. Returns the
    ``bake_operations``-ready list.

    A separate function from :func:`compute_bake_operations` so the
    pure-engine arithmetic stays testable without dragging the full
    ``SourceItem`` shape into every unit test.
    """
    if not applied_offset_hours:
        return []
    delta = timedelta(hours=applied_offset_hours)
    by_path = {it.path: it.timestamp for it in items}
    out: list[tuple[Path, datetime]] = []
    for p in verdict.file_paths:
        ts = by_path.get(p)
        if ts is None:
            continue
        out.append((p, ts + delta))
    return out


# ── Sanity-check helpers (pure, easy to unit-test) ────────────────


def check_future_dated(
    ts_list: Sequence[datetime], now: datetime,
) -> Optional[TzWarning]:
    """Flag when ≥1 timestamp lands *after* ``now + _FUTURE_SLACK``.
    Future-dated photos almost always mean the camera's clock or TZ
    is misconfigured (e.g. set to 2099)."""
    if not ts_list:
        return None
    future = [t for t in ts_list if t > now + _FUTURE_SLACK]
    if not future:
        return None
    return TzWarning(
        kind="future_dated",
        severity="high",
        message=(
            f"{len(future)} photo(s) are timestamped in the future "
            f"(after {(now + _FUTURE_SLACK).strftime('%Y-%m-%d %H:%M')}). "
            f"The camera's clock or timezone is probably wrong."
        ),
    )


def check_older_than_trip(
    ts_list: Sequence[datetime],
    plan_dates: Sequence[date],
) -> Optional[TzWarning]:
    """Flag when the median timestamp falls more than
    :data:`_TRIP_OLDER_DAYS` before the trip's earliest planned
    date. Plan-relative beats now-relative: a 2024 trip imported
    today is fine; a 2024 timestamp imported into a 2026 trip is
    the symptom we want."""
    if not ts_list or not plan_dates:
        return None
    sorted_ts = sorted(ts_list)
    median = sorted_ts[len(sorted_ts) // 2]
    earliest_planned = min(plan_dates)
    gap_days = (earliest_planned - median.date()).days
    if gap_days <= _TRIP_OLDER_DAYS:
        return None
    return TzWarning(
        kind="older_than_trip",
        severity="high",
        message=(
            f"Photos are dated around {median.date().isoformat()}, "
            f"about {gap_days} days before this trip's earliest "
            f"planned day ({earliest_planned.isoformat()}). The "
            f"camera's clock is probably set to a previous year."
        ),
    )


def check_night_majority(
    ts_list: Sequence[datetime],
) -> Optional[TzWarning]:
    """Flag when more than :data:`_NIGHT_MAJORITY_THRESHOLD` of the
    timestamps fall in [22:00, 06:00] local wall-clock. Strong
    signal for a TZ shift on a daylight-shooting trip — the user
    didn't shoot 60% of the day at midnight."""
    if not ts_list:
        return None
    night_count = sum(1 for t in ts_list if (t.hour >= 22 or t.hour < 6))
    fraction = night_count / len(ts_list)
    if fraction <= _NIGHT_MAJORITY_THRESHOLD:
        return None
    return TzWarning(
        kind="night_majority",
        severity="high",
        message=(
            f"{int(fraction * 100)}% of timestamps fall between "
            f"22:00 and 06:00 — unusual for a daylight-shooting "
            f"day. The camera's timezone may be set to a region "
            f"hours away from where you're shooting."
        ),
    )


def check_stale_gap(
    ts_list: Sequence[datetime], now: datetime,
) -> Optional[TzWarning]:
    """Flag when the newest timestamp is more than
    :data:`_STALE_GAP_DAYS` behind ``now`` — the user is probably
    re-ingesting an already-processed card by mistake, or the
    camera's clock is set years off."""
    if not ts_list:
        return None
    newest = max(ts_list)
    gap_days = (now.date() - newest.date()).days
    if gap_days <= _STALE_GAP_DAYS:
        return None
    return TzWarning(
        kind="stale_gap",
        severity="low",
        message=(
            f"Newest photo here is from {newest.date().isoformat()}"
            f" — {gap_days} days ago. Make sure you're ingesting "
            f"the right card."
        ),
    )


# ── Internal helpers ───────────────────────────────────────────────


def _timestamps_for(
    items: Sequence[SourceItem], paths: Sequence[Path],
) -> list[datetime]:
    """Look up the raw EXIF timestamps for the given paths. Items
    with no readable timestamp are omitted."""
    wanted = set(paths)
    return [
        it.timestamp for it in items
        if it.path in wanted and it.timestamp is not None
    ]


def _range_of(
    timestamps: Sequence[datetime],
) -> Optional[tuple[datetime, datetime]]:
    if not timestamps:
        return None
    return (min(timestamps), max(timestamps))


def _build_warnings(
    day_timestamps: Sequence[datetime],
    trip_day: TripDay,
    plan_dates: Sequence[date],
    now: datetime,
) -> tuple[TzWarning, ...]:
    """Run every per-day sanity check + the TZ-mismatch detector
    fallback. Order is stable for testability.

    The TZ-mismatch detector (``core.tz_mismatch_detector``) needs
    a multi-camera ``SourceIndex`` to do its phone-pairwise
    inference, so it's invoked at the dialog level (caller-side,
    once across all items), not per-day here. This per-day routine
    only owns the four day-local heuristics; the dialog merges in
    the cross-camera ``TzSuggestion`` separately.
    """
    warnings: list[TzWarning] = []
    for check in (
        check_future_dated,
        check_older_than_trip,
        check_night_majority,
        check_stale_gap,
    ):
        if check is check_future_dated:
            w = check(day_timestamps, now)
        elif check is check_older_than_trip:
            w = check(day_timestamps, plan_dates)
        elif check is check_night_majority:
            w = check(day_timestamps)
        elif check is check_stale_gap:
            w = check(day_timestamps, now)
        else:
            w = None
        if w is not None:
            warnings.append(w)
    return tuple(warnings)
