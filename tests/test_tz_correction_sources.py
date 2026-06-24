"""spec/123 — the three explicit sources of a per-camera offset_seconds.

* Source 1 (known TZ): ``offset = trip_tz_seconds − camera_tz_seconds``.
  Nepal GoPro on São Paulo time → +31 500 s (+8:45).
* Source 2 (recognized simultaneous, clock was right): ``offset = 0``.
* Source 3 (measured pair): the RAW measured delta rounded to the
  nearest second — NO snapping. The Nepal pair (5h00m02s) yields
  18 002 s, not 18 000 (snapped 5:00) and not 17 100 (snapped 4:45).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from core.clock_calibration import (
    CalibrationPair,
    build_calibration_from_known_tz,
    build_calibration_from_pair,
    build_calibration_simultaneous,
    offset_from_known_tz,
    offset_from_measured_pair,
    offset_from_simultaneous,
)


# ── Source 1: known TZ ──────────────────────────────────────────────


def test_source1_nepal_gopro_eight_forty_five():
    """A camera configured to São Paulo (−3) shooting in Kathmandu
    (+5:45) needs +8:45 = +31 500 s."""
    trip = 5 * 3600 + 45 * 60     # +5:45
    cam = -3 * 3600               # −3:00
    assert offset_from_known_tz(
        trip_tz_seconds=trip, camera_tz_seconds=cam,
    ) == 31_500


def test_source1_clock_already_correct():
    """Same zone both sides → 0."""
    assert offset_from_known_tz(
        trip_tz_seconds=-3 * 3600, camera_tz_seconds=-3 * 3600,
    ) == 0


def test_source1_negative_direction():
    """Camera AHEAD of trip TZ → negative offset."""
    trip = 5 * 3600 + 45 * 60     # +5:45
    cam = 10 * 3600               # +10:00 (Sydney)
    # Camera 10−5:45 = 4:15 ahead → need to subtract 4:15.
    assert offset_from_known_tz(
        trip_tz_seconds=trip, camera_tz_seconds=cam,
    ) == -(4 * 3600 + 15 * 60)


def test_source1_builder_attaches_offset_to_calibration():
    cal = build_calibration_from_known_tz(
        "GoPro", trip_tz_seconds=20_700, camera_tz_seconds=-10_800)
    assert cal.offset_seconds == 31_500
    assert cal.has_any_source


# ── Source 2: recognized simultaneous ───────────────────────────────


def test_source2_is_zero():
    """The 'no correction necessary' outcome."""
    assert offset_from_simultaneous() == 0
    cal = build_calibration_simultaneous("G9")
    assert cal.offset_seconds == 0
    assert cal.has_any_source


# ── Source 3: measured pair (RAW DELTA, NO SNAPPING) ────────────────


def _pair(cam: datetime, ref: datetime) -> CalibrationPair:
    return CalibrationPair(
        camera_path=Path("c.jpg"),
        reference_path=Path("r.jpg"),
        camera_time=cam,
        reference_time=ref,
    )


def test_source3_nepal_5h00m02s_is_NOT_snapped():
    """The Nepal pair (5h00m02s measured) yields 18 002 s — the raw
    measured delta to the nearest second. NOT the snapped 5:00
    (18 000 s) or 4:45 (17 100 s) the spec/101 model would have
    invented."""
    cam = datetime(2026, 3, 10, 9, 0, 0)
    ref = datetime(2026, 3, 10, 14, 0, 2)          # +5h00m02s
    assert offset_from_measured_pair(_pair(cam, ref)) == 18_002


def test_source3_nepal_5h00m02s_via_builder():
    cam = datetime(2026, 3, 10, 9, 0, 0)
    ref = datetime(2026, 3, 10, 14, 0, 2)
    cal = build_calibration_from_pair("GoPro", _pair(cam, ref))
    # No snap — must be the raw 18 002 s, not 18 000 or 17 100.
    assert cal.offset_seconds == 18_002
    assert cal.offset_seconds not in (18_000, 17_100)


def test_source3_negative_measured_delta():
    """Camera was AHEAD of reference → offset is negative; still
    applied raw to the second."""
    cam = datetime(2026, 3, 10, 14, 5, 7)
    ref = datetime(2026, 3, 10, 9, 0, 0)        # −5h05m07s
    expected = -(5 * 3600 + 5 * 60 + 7)
    assert offset_from_measured_pair(_pair(cam, ref)) == expected


def test_source3_sub_second_rounds_to_nearest_second():
    """Sub-second precision rounds to the nearest second — clocks
    don't read finer than 1 s on dedicated cameras."""
    cam = datetime(2026, 3, 10, 9, 0, 0, microsecond=0)
    # +5:00:00.499 → rounds to 18 000 s (not up)
    ref = datetime(2026, 3, 10, 14, 0, 0, microsecond=499_000)
    assert offset_from_measured_pair(_pair(cam, ref)) == 18_000
    # +5:00:00.501 → rounds to 18 001 s (up)
    ref2 = datetime(2026, 3, 10, 14, 0, 0, microsecond=501_000)
    assert offset_from_measured_pair(_pair(cam, ref2)) == 18_001


# ── No code path snaps the APPLIED offset ──────────────────────────


def test_applied_path_never_snaps_measured_pair():
    """spec/123: the offset the calibration APPLIES from a measured
    pair is the raw delta — even if snap_to_tz_offset still exists as a
    recognition-UI clustering helper, the value
    ``build_calibration_from_pair`` produces must be the raw seconds."""
    cam = datetime(2026, 3, 10, 9, 0, 0)
    # 4h59m48s — a non-snapping pair (snap would push to 5:00 = 18 000;
    # we want the raw 17 988).
    ref = datetime(2026, 3, 10, 13, 59, 48)
    cal = build_calibration_from_pair("Cam", _pair(cam, ref))
    assert cal.offset_seconds == 17_988


def test_build_calibration_takes_no_timestamps_are_utc_kwarg():
    """spec/123 reverts spec/122 — the UTC kwarg is gone."""
    import inspect

    from core.clock_calibration import build_calibration
    sig = inspect.signature(build_calibration)
    assert "timestamps_are_utc" not in sig.parameters
