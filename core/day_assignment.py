"""Brain-only day assignment (Stage B.1).

docs/18 §"Culling contexts" → "Stage B — brain-only day assignment"
(frozen 2026-05-17, Nelson): entering a Cull context must **never
copy or materialise** anything. Reconcile bundles *(brain)*
deciding which `Dia N` a file belongs to and *(hands)* physically
copying. This module is **only the brain** — a pure mapping
`file → (Dia N, label)` that drives the navigator's day grouping,
replacing Stage A's approximate raw-EXIF grouping. The single
file-write point stays Export (Stage C).

It re-uses the exact recipe `reconcile_commit` applies, lifted out
of its copy/event-creation side:

    corrected = camera_ts + calibration.offset_at(camera_ts)   # if any source
    day_num   = smallest day_number whose plan date == corrected.date()
    label     = day_folder_name(that TripDay)                   # "Dia N - desc"

Phones / cameras with no calibration **pass through uncorrected**
(phone EXIF is trip-local by NTP-sync convention — same as
reconcile). Pure / Qt-free / never raises (a bad timestamp must
never crash the navigator — it degrades to *Undated*).

The per-camera ``CameraCalibration`` is passed in **explicitly**
per item, so this core is decoupled and deterministically testable.
Binding a live ``Event``'s stored calibration to its cameras is the
Stage-B.3 wiring concern, not this module's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional, Sequence

from core.clock_calibration import CameraCalibration
from core.models import TripDay
from core.path_builder import day_folder_name

# Label for "no usable timestamp" / "date matches no Dia in the
# plan" — the navigator shows these in a trailing Undated day
# (same spirit as reconcile's _no_timestamp quarantine, minus any
# file move — brain-only).
UNDATED_LABEL = "Undated"


@dataclass(frozen=True)
class DayAssignment:
    """Where one file lands. ``day_number`` is ``None`` when the
    file has no usable timestamp or its corrected date matches no
    Dia in the plan; ``label`` is then :data:`UNDATED_LABEL`."""

    day_number: Optional[int]
    label: str

    @property
    def matched(self) -> bool:
        return self.day_number is not None


def build_day_index(
    trip_days: Sequence[TripDay],
) -> tuple[dict[date, int], dict[int, TripDay]]:
    """``(date → smallest day_number, day_number → TripDay)``.

    Smallest-day-number-wins on a duplicate calendar date is the
    documented reconcile behaviour (e.g. Nepal Dia 7 + Dia 8 share
    03/11 → a photo on that date routes to Dia 7)."""
    by_date: dict[date, int] = {}
    by_number: dict[int, TripDay] = {}
    for d in sorted(trip_days, key=lambda x: x.day_number):
        by_number[d.day_number] = d
        if d.date is not None and d.date not in by_date:
            by_date[d.date] = d.day_number
    return by_date, by_number


def corrected_timestamp(
    camera_ts: Optional[datetime],
    calibration: Optional[CameraCalibration],
) -> Optional[datetime]:
    """``camera_ts + calibration.offset_at(camera_ts)`` when there
    is a calibration source; otherwise pass through (phones / no
    calibration). ``None`` in → ``None`` out."""
    if camera_ts is None:
        return None
    try:
        if calibration is not None and calibration.has_any_source:
            return camera_ts + calibration.offset_at(camera_ts)
    except Exception:        # noqa: BLE001 — must never crash the nav
        return camera_ts
    return camera_ts


def assign_one(
    camera_ts: Optional[datetime],
    calibration: Optional[CameraCalibration],
    by_date: dict[date, int],
    by_number: dict[int, TripDay],
) -> DayAssignment:
    """Pure single-file assignment (the index is built once by the
    caller via :func:`build_day_index`)."""
    corrected = corrected_timestamp(camera_ts, calibration)
    if corrected is None:
        return DayAssignment(None, UNDATED_LABEL)
    day_num = by_date.get(corrected.date())
    if day_num is None:
        return DayAssignment(None, UNDATED_LABEL)
    day = by_number.get(day_num)
    if day is None:                       # defensive — index is paired
        return DayAssignment(None, UNDATED_LABEL)
    return DayAssignment(day_num, day_folder_name(day))


def assign_days(
    items: Iterable[tuple[Path, Optional[datetime], Optional[CameraCalibration]]],
    trip_days: Sequence[TripDay],
) -> dict[Path, DayAssignment]:
    """Map each ``(path, camera_ts, calibration)`` to its
    :class:`DayAssignment`. ``calibration`` is ``None`` for phones /
    uncalibrated cameras (pass-through). Builds the plan index once."""
    by_date, by_number = build_day_index(trip_days)
    out: dict[Path, DayAssignment] = {}
    for path, ts, cal in items:
        out[Path(path)] = assign_one(ts, cal, by_date, by_number)
    return out
