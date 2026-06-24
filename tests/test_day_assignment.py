"""Tests for core.day_assignment (Stage B.1 — brain-only).

Pure. Proves the reconcile day-recipe is correctly lifted: TZ
calibration shifts a midnight-spanning frame onto the right Dia
(the Nepal day-shift incident: a camera left on a prior trip's
timezone mis-files frames by a day), phones pass through,
duplicate-date days
pick the smallest day_number, no-timestamp / off-plan → Undated,
labels match `day_folder_name`, and it never raises.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from core.clock_calibration import CameraCalibration
from core.models import TripDay
from core.day_assignment import (
    UNDATED_LABEL,
    assign_days,
    assign_one,
    build_day_index,
)


def _days():
    return [
        TripDay(day_number=9, date=date(2026, 4, 20),
                description="Manuel Antonio National Park"),
        TripDay(day_number=10, date=date(2026, 4, 21),
                description="Departure"),
    ]


def _idx(days):
    return build_day_index(days)


def test_tz_calibration_pulls_midnight_frame_to_correct_dia():
    by_date, by_number = _idx(_days())
    # Camera left on a prior trip's TZ (the Nepal day-shift
    # incident) → a constant offset; representative -3h here.
    cal = CameraCalibration(camera_id="G9",
                            offset_seconds=-3 * 3600)
    # Camera clock reads 21 Apr 01:30 → corrected 20 Apr 22:30.
    a = assign_one(datetime(2026, 4, 21, 1, 30), cal,
                   by_date, by_number)
    assert a.day_number == 9
    assert a.label == "Dia 9 - 2026-04-20 - Manuel Antonio National Park"
    # Without correction it would have been Dia 10 — prove the brain
    # is doing the work, not raw EXIF.
    raw = assign_one(datetime(2026, 4, 21, 1, 30), None,
                     by_date, by_number)
    assert raw.day_number == 10


def test_phone_passes_through_uncorrected():
    by_date, by_number = _idx(_days())
    a = assign_one(datetime(2026, 4, 20, 10, 0), None,
                   by_date, by_number)        # calibration None = phone
    assert a.day_number == 9


def test_duplicate_date_picks_smallest_day_number():
    days = [
        TripDay(day_number=7, date=date(2026, 11, 3), description="A"),
        TripDay(day_number=8, date=date(2026, 11, 3), description="B"),
    ]
    by_date, by_number = _idx(days)
    a = assign_one(datetime(2026, 11, 3, 12, 0), None,
                   by_date, by_number)
    assert a.day_number == 7 and a.label == "Dia 7 - 2026-11-03 - A"


def test_no_timestamp_and_off_plan_are_undated():
    by_date, by_number = _idx(_days())
    assert assign_one(None, None, by_date, by_number).label == \
        UNDATED_LABEL
    off = assign_one(datetime(2026, 1, 1, 9, 0), None,
                     by_date, by_number)       # no Dia for Jan 1
    assert off.day_number is None and off.label == UNDATED_LABEL
    # No plan at all → everything Undated.
    bd, bn = _idx([])
    assert assign_one(datetime(2026, 4, 20, 9, 0), None,
                      bd, bn).matched is False


def test_assign_days_maps_each_path_and_never_raises():
    cal = CameraCalibration(camera_id="G9",
                            offset_seconds=-3 * 3600)
    items = [
        (Path("a.rw2"), datetime(2026, 4, 20, 9, 0), cal),
        (Path("b.rw2"), datetime(2026, 4, 21, 1, 30), cal),  # → Dia 9
        (Path("c.jpg"), None, None),                          # Undated
        (Path("d.jpg"), datetime(2026, 4, 21, 12, 0), None),  # phone→Dia10
    ]
    res = assign_days(items, _days())
    assert res[Path("a.rw2")].day_number == 9
    assert res[Path("b.rw2")].day_number == 9       # corrected back
    assert res[Path("c.jpg")].label == UNDATED_LABEL
    assert res[Path("d.jpg")].day_number == 10
    # Garbled calibration must not crash (degrades gracefully).
    class _Bad:
        has_any_source = True
        def offset_at(self, _t):  # noqa: D401
            raise RuntimeError("boom")
    bad = assign_days([(Path("x"), datetime(2026, 4, 20, 9, 0),
                        _Bad())], _days())            # type: ignore
    assert bad[Path("x")].day_number == 9             # fell back to raw
