"""Trip-TZ segment derivation (spec/127 §1.1).

A **segment** = the set of plan days sharing one trip TZ
(``trip_day.tz_minutes``). A normal trip = one segment; a TZ-crossing
trip (e.g. Nepal +5:45 with a day at India +5:30) = two. Keyed by the
segment's ``trip_tz_seconds`` (×60 from ``tz_minutes``).

Mirrors Collect's ``tz_camera_groups`` shape — the unified correction
dialog uses the **same** per-segment model Collect already uses, instead
of a single ``Counter(...).most_common(1)`` predominant TZ. A camera
that captured items in two segments now gets the right offset in **each**.

Pure logic — no Qt, no SQLite — so the derivation is unit-testable on
plain dataclass-shaped inputs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Mapping, Optional, Set, Tuple


@dataclass
class TzSegment:
    """One trip-TZ segment.

    * ``trip_tz_seconds`` — the segment's trip TZ (``trip_day.tz_minutes
      × 60``). The PK that joins to ``camera_tz_correction``.
    * ``day_numbers`` — the plan days that share this TZ, sorted ascending.
    * ``cameras_present`` — the camera ids that captured at least one
      item on any of the segment's days (sorted ascending for stable UI
      ordering). Empty when no captured items intersect the segment.
    """

    trip_tz_seconds: int
    day_numbers: List[int] = field(default_factory=list)
    cameras_present: List[str] = field(default_factory=list)


def derive_segments(
    trip_days_tz: Mapping[int, Optional[int]],
    camera_day_pairs: Optional[Iterable[Tuple[str, int]]] = None,
) -> List[TzSegment]:
    """Group plan days by their declared trip TZ and assign cameras.

    ``trip_days_tz`` — ``{day_number: tz_minutes}`` for every plan day
    (``tz_minutes`` may be ``None`` for an un-set day). Days with a NULL
    TZ are dropped from segments — they aren't part of any TZ-grouping
    decision (the correction dialog has nothing to apply to them either).

    ``camera_day_pairs`` — iterable of ``(camera_id, day_number)`` pairs
    drawn from ``item.camera_id × item.day_number`` (one tuple per
    distinct pair; the caller dedupes — typically via
    ``eg.items(camera_id=..., day=...)`` per (camera, day)). When ``None``
    or empty, every segment lists no cameras.

    Returns segments sorted ascending by ``trip_tz_seconds`` for stable
    section ordering in the dialog.
    """
    by_tz_seconds: dict[int, Set[int]] = {}
    for day_num, tz_minutes in trip_days_tz.items():
        if tz_minutes is None:
            continue
        key = int(tz_minutes) * 60
        by_tz_seconds.setdefault(key, set()).add(int(day_num))

    day_to_tz: dict[int, int] = {
        day_num: tz_seconds
        for tz_seconds, days in by_tz_seconds.items()
        for day_num in days
    }
    cameras_by_tz: dict[int, Set[str]] = {k: set() for k in by_tz_seconds}
    if camera_day_pairs is not None:
        for cam_id, day_num in camera_day_pairs:
            tz_seconds = day_to_tz.get(int(day_num))
            if tz_seconds is None:
                continue
            cameras_by_tz[tz_seconds].add(str(cam_id))

    return [
        TzSegment(
            trip_tz_seconds=tz_seconds,
            day_numbers=sorted(by_tz_seconds[tz_seconds]),
            cameras_present=sorted(cameras_by_tz.get(tz_seconds, set())),
        )
        for tz_seconds in sorted(by_tz_seconds.keys())
    ]
