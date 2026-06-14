"""TZ-calibration trigger — spec/52 §8.2 + §8.4 (slice D.3.a).

Pure-logic decision: given a scan's per-day TZ + the user's home TZ +
the (camera, day) presence map + the already-calibrated offsets, which
(camera, day) pairs still need user calibration?

Per spec/52 §8.2 the calibration ask is conditional:

* The day's location-derived TZ ≠ the user's home TZ. Same-TZ days
  don't need calibration — the camera clock matches the trip clock.
* Camera photos (non-phone) are present on that day. If only phones
  contributed, no calibration is needed — phones carry TZ in EXIF
  (``OffsetTimeOriginal``).

Per spec/52 §8.4 different days of the same trip can need different
offsets (border crossings), so the candidate list is per-(camera, day),
not per-camera.

This module is the *trigger*. The actual calibration dialog
(:class:`mira.ui.pages.discrete_tz_dialog.DiscreteTzDialog` for
Path A; pair-pick for Path B) is wired by the host at slice E. The
entry "Calibrate now or Skip?" ask dialog
(:mod:`mira.ui.pages.tz_calibration_ask_dialog`) consumes this
module's output to render the candidate count + per-camera-day
breakdown.

Pure Python — no Qt — so the trigger is testable in isolation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Input shapes — the host assembles these from scan output + gateway reads
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CameraDayPresence:
    """One (camera, day) pair from the scan: does this camera have photos
    on this day, and is it a phone?

    The host produces these from the scan's per-camera item list, with
    ``is_phone`` resolved via :func:`core.phone_detector.is_phone`."""

    camera_id: str
    day_number: int
    is_phone: bool


@dataclass(frozen=True)
class CalibrationCandidate:
    """One (camera, day) pair that needs user calibration.

    ``day_tz_minutes`` is the day's location-derived TZ (from phone EXIF
    via the autofill engine, or from a user manual entry). Carried so
    the host can pre-seed the calibration dialog with a plausible
    starting value."""

    camera_id: str
    day_number: int
    date: date
    day_tz_minutes: int


# --------------------------------------------------------------------------- #
# The trigger
# --------------------------------------------------------------------------- #


def needs_calibration(
    *,
    home_tz_minutes: int,
    day_tz_lookup: Dict[int, Optional[int]],
    day_date_lookup: Dict[int, date],
    presences: Sequence[CameraDayPresence],
    existing_offsets: Dict[Tuple[str, int], int],
) -> List[CalibrationCandidate]:
    """The list of (camera, day) pairs that need user calibration.

    Args:
        home_tz_minutes: User's home TZ in minutes east-of-UTC (from
            ``Settings.home_timezone`` × 60).
        day_tz_lookup: Per-day TZ from the autofill engine. ``None`` for
            a day means "no autofill ran" (no phone photos contributed)
            — those days are skipped (we can't tell if calibration is
            needed without knowing the day's TZ).
        day_date_lookup: Per-day calendar date for display purposes.
        presences: One row per (camera, day) the scan found photos on.
        existing_offsets: Already-set per-(camera, day) declared TZ
            offsets (from ``gw.camera_day_tz(...)``). A presence that
            appears here is treated as already-calibrated and SKIPPED.

    Returns:
        A list of candidates in stable ``(day_number, camera_id)``
        order. Empty when no calibration is needed (every day matches
        home, or only phones contributed, or every non-phone (camera,
        day) is already calibrated).
    """
    candidates: List[CalibrationCandidate] = []

    for presence in presences:
        if presence.is_phone:
            continue                                                # phones carry TZ
        day_tz = day_tz_lookup.get(presence.day_number)
        if day_tz is None:
            continue                                                # day's TZ unknown
        if day_tz == home_tz_minutes:
            continue                                                # at home TZ
        if (presence.camera_id, presence.day_number) in existing_offsets:
            continue                                                # already calibrated
        day_date = day_date_lookup.get(presence.day_number)
        if day_date is None:
            continue                                                # missing date metadata
        candidates.append(CalibrationCandidate(
            camera_id=presence.camera_id,
            day_number=presence.day_number,
            date=day_date,
            day_tz_minutes=day_tz,
        ))

    # Stable order — day first, then camera_id. Makes the entry dialog's
    # "you'll be asked about N days" hint deterministic across runs.
    candidates.sort(key=lambda c: (c.day_number, c.camera_id))
    return candidates


# --------------------------------------------------------------------------- #
# Summary helpers — used by the entry dialog
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CalibrationSummary:
    """Aggregate counts for the entry "Calibrate now / Skip" dialog.

    ``distinct_days`` counts how many distinct day_numbers appear across
    the candidates; ``distinct_cameras`` counts how many distinct
    camera_ids. ``total_pairs`` is the candidate count.
    """

    total_pairs: int
    distinct_days: int
    distinct_cameras: int

    @property
    def is_empty(self) -> bool:
        return self.total_pairs == 0


def summarize(candidates: Sequence[CalibrationCandidate]) -> CalibrationSummary:
    """Aggregate counts for the entry dialog. Cheap O(n) walk — kept as a
    helper so the dialog doesn't reproduce the set arithmetic inline."""
    return CalibrationSummary(
        total_pairs=len(candidates),
        distinct_days=len({c.day_number for c in candidates}),
        distinct_cameras=len({c.camera_id for c in candidates}),
    )
