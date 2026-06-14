"""Tests for core.clock_calibration — pair-based offset + drift interpolation.

Pure-math unit tests; no real EXIF / file I/O. The calibration math is
the highest-leverage place to invest in coverage because everything
downstream (EXIF rewriting, day routing, plan skeleton) trusts its
output.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.clock_calibration import (
    CalibrationPair,
    CameraCalibration,
    build_calibration,
    correct_camera_time,
    snap_disagreement,
    snap_to_tz_offset,
)


def _pair(
    cam_t: datetime,
    ref_t: datetime,
    name: str = "P0001.RW2",
) -> CalibrationPair:
    """Construct a pair with synthetic paths — file content doesn't
    matter for math-only tests."""
    return CalibrationPair(
        camera_path=Path(name),
        reference_path=Path(f"ref_{name}.jpg"),
        camera_time=cam_t,
        reference_time=ref_t,
    )


# ── Single-pair calibration ──────────────────────────────────────


def test_single_pair_produces_constant_offset():
    """One pair → constant offset applied to all camera times,
    regardless of when they were taken."""
    cam_t = datetime(2025, 5, 12, 10, 0, 0)
    ref_t = datetime(2025, 5, 12, 10, 5, 0)  # camera 5 min behind
    cal = build_calibration("G9", [_pair(cam_t, ref_t)])

    # Same time as the pair → exact offset
    assert cal.correct(cam_t) == ref_t
    # Earlier camera time → same offset (5 min added)
    earlier = datetime(2025, 5, 1, 9, 0, 0)
    assert cal.correct(earlier) == earlier + timedelta(minutes=5)
    # Later camera time → same offset
    later = datetime(2025, 5, 25, 18, 30, 0)
    assert cal.correct(later) == later + timedelta(minutes=5)
    assert not cal.has_drift_correction


def test_single_pair_with_negative_offset():
    """Camera AHEAD of reference produces negative offset; math
    must subtract correctly."""
    cam_t = datetime(2025, 5, 12, 10, 5, 0)
    ref_t = datetime(2025, 5, 12, 10, 0, 0)  # camera 5 min ahead
    cal = build_calibration("G9", [_pair(cam_t, ref_t)])
    assert cal.correct(cam_t) == ref_t


# ── Two-pair drift ────────────────────────────────────────────────


def test_two_pairs_interpolate_linearly_between():
    """Linear drift over the trip: pair 1 at +5min, pair 2 at +10min.
    A photo halfway through (in camera time) should get +7.5min."""
    p1 = _pair(
        datetime(2025, 5, 1, 10, 0, 0),
        datetime(2025, 5, 1, 10, 5, 0),  # +5 min
    )
    p2 = _pair(
        datetime(2025, 5, 11, 10, 0, 0),
        datetime(2025, 5, 11, 10, 10, 0),  # +10 min
    )
    cal = build_calibration("G9", [p1, p2])
    assert cal.has_drift_correction

    # Halfway through → +7.5 min
    midpoint = datetime(2025, 5, 6, 10, 0, 0)
    corrected = cal.correct(midpoint)
    expected = midpoint + timedelta(minutes=7, seconds=30)
    assert corrected == expected


def test_two_pairs_clamp_before_first():
    """Camera times BEFORE the earliest pair get the earliest pair's
    offset (no extrapolation backwards — keeps noise from amplifying)."""
    p1 = _pair(
        datetime(2025, 5, 5, 10, 0, 0),
        datetime(2025, 5, 5, 10, 5, 0),  # +5 min
    )
    p2 = _pair(
        datetime(2025, 5, 15, 10, 0, 0),
        datetime(2025, 5, 15, 10, 10, 0),  # +10 min
    )
    cal = build_calibration("G9", [p1, p2])
    very_early = datetime(2025, 5, 1, 8, 0, 0)
    assert cal.correct(very_early) == very_early + timedelta(minutes=5)


def test_two_pairs_clamp_after_last():
    """Camera times AFTER the latest pair get the latest pair's
    offset (no extrapolation forward)."""
    p1 = _pair(
        datetime(2025, 5, 5, 10, 0, 0),
        datetime(2025, 5, 5, 10, 5, 0),
    )
    p2 = _pair(
        datetime(2025, 5, 15, 10, 0, 0),
        datetime(2025, 5, 15, 10, 10, 0),
    )
    cal = build_calibration("G9", [p1, p2])
    very_late = datetime(2025, 5, 30, 22, 0, 0)
    assert cal.correct(very_late) == very_late + timedelta(minutes=10)


def test_two_pairs_unsorted_input_still_works():
    """build_calibration shouldn't depend on input ordering — the
    UI may hand pairs in arbitrary order."""
    p_late = _pair(
        datetime(2025, 5, 15, 10, 0, 0),
        datetime(2025, 5, 15, 10, 10, 0),
    )
    p_early = _pair(
        datetime(2025, 5, 5, 10, 0, 0),
        datetime(2025, 5, 5, 10, 5, 0),
    )
    cal = build_calibration("G9", [p_late, p_early])
    midpoint = datetime(2025, 5, 10, 10, 0, 0)
    corrected = cal.correct(midpoint)
    expected = midpoint + timedelta(minutes=7, seconds=30)
    assert corrected == expected


# ── Outlier rejection (3+ pairs) ──────────────────────────────────


def test_three_pairs_no_outlier_keeps_all():
    """Three pairs with offsets close to each other → all kept,
    no rejection."""
    pairs = [
        _pair(
            datetime(2025, 5, 5, 10, 0, 0),
            datetime(2025, 5, 5, 10, 5, 0),
            name="A",
        ),
        _pair(
            datetime(2025, 5, 10, 10, 0, 0),
            datetime(2025, 5, 10, 10, 5, 30),
            name="B",
        ),
        _pair(
            datetime(2025, 5, 15, 10, 0, 0),
            datetime(2025, 5, 15, 10, 6, 0),
            name="C",
        ),
    ]
    cal = build_calibration("G9", pairs)
    assert len(cal.pairs) == 3
    assert cal.rejected_pairs == []


def test_three_pairs_one_outlier_rejected():
    """Three pairs where pair B is wildly off the median → B
    gets rejected, A and C kept."""
    pairs = [
        _pair(
            datetime(2025, 5, 5, 10, 0, 0),
            datetime(2025, 5, 5, 10, 5, 0),
            name="A",
        ),
        _pair(
            # Outlier: 30 min off vs ~5 min for the others.
            datetime(2025, 5, 10, 10, 0, 0),
            datetime(2025, 5, 10, 10, 30, 0),
            name="B",
        ),
        _pair(
            datetime(2025, 5, 15, 10, 0, 0),
            datetime(2025, 5, 15, 10, 5, 0),
            name="C",
        ),
    ]
    cal = build_calibration("G9", pairs)
    accepted_names = {p.camera_path.name for p in cal.pairs}
    rejected_names = {p.camera_path.name for p in cal.rejected_pairs}
    assert "A" in accepted_names
    assert "C" in accepted_names
    assert "B" in rejected_names


def test_outlier_threshold_is_configurable():
    """A user with naturally noisy clocks can widen the threshold
    so 'real' drift isn't mistaken for outliers."""
    pairs = [
        _pair(
            datetime(2025, 5, 5, 10, 0, 0),
            datetime(2025, 5, 5, 10, 5, 0),
            name="A",
        ),
        _pair(
            # 8 min off vs 5 min — within 10-min threshold.
            datetime(2025, 5, 10, 10, 0, 0),
            datetime(2025, 5, 10, 10, 13, 0),
            name="B",
        ),
        _pair(
            datetime(2025, 5, 15, 10, 0, 0),
            datetime(2025, 5, 15, 10, 5, 0),
            name="C",
        ),
    ]
    cal = build_calibration(
        "G9", pairs, outlier_threshold=timedelta(minutes=10),
    )
    assert len(cal.pairs) == 3  # all kept


def test_all_pairs_rejected_falls_back_to_closest_to_median():
    """Defensive: if every pair drifts wildly from each other we
    can't reject all of them and end up with zero coverage. Keep
    the median-closest one."""
    # Pairs with offsets 2, 30, 60 min — median is 30, both A and C
    # would be rejected with the default 5-min threshold. Logic
    # should keep B (closest to median = itself).
    pairs = [
        _pair(
            datetime(2025, 5, 5, 10, 0, 0),
            datetime(2025, 5, 5, 10, 2, 0),
            name="A",
        ),
        _pair(
            datetime(2025, 5, 10, 10, 0, 0),
            datetime(2025, 5, 10, 10, 30, 0),
            name="B",
        ),
        _pair(
            datetime(2025, 5, 15, 10, 0, 0),
            datetime(2025, 5, 15, 11, 0, 0),
            name="C",
        ),
    ]
    cal = build_calibration("G9", pairs)
    assert len(cal.pairs) == 1
    assert cal.pairs[0].camera_path.name == "B"


# ── Edge cases ────────────────────────────────────────────────────


def test_no_pairs_raises_on_offset_at():
    """Asking for an offset on an unpopulated calibration is a
    programming error — surface loudly rather than silently
    returning zero (which would be wrong)."""
    cal = CameraCalibration(camera_id="G9", pairs=[])
    with pytest.raises(ValueError):
        cal.offset_at(datetime(2025, 5, 1))


def test_correct_camera_time_with_none_calibration_is_no_op():
    """Helper for cameras the user chose not to calibrate (or for
    the reference camera itself)."""
    t = datetime(2025, 5, 1, 10, 0, 0)
    assert correct_camera_time(t, None) == t


# ── TZ-based offset ──────────────────────────────────────────────


def test_tz_only_calibration_produces_constant_offset():
    """Camera configured to a known TZ but no pairs given. Offset is
    ``trip_tz - configured_tz`` hours, applied constant for any time."""
    cal = build_calibration(
        "G9", [], configured_tz=-3.0, trip_tz=5.75,
    )
    # +8.75h = 8h 45min = 31500s
    assert cal.has_any_source
    assert not cal.has_drift_correction  # no pairs
    assert cal.tz_offset == timedelta(hours=8.75)
    # Any camera time → +8.75h
    t = datetime(2025, 10, 26, 9, 0, 0)
    assert cal.correct(t) == t + timedelta(hours=8.75)


def test_tz_only_calibration_negative_offset():
    """Camera ahead of trip TZ → negative offset."""
    cal = build_calibration(
        "G9", [], configured_tz=10.0, trip_tz=5.75,
    )
    assert cal.tz_offset == timedelta(hours=-4.25)


def test_neither_pairs_nor_tz_marks_no_source():
    """Calibration with no pairs AND no TZ has nothing to compute
    with — caller must skip or pass through."""
    cal = build_calibration("G9", [])
    assert not cal.has_any_source
    assert cal.tz_offset is None
    with pytest.raises(ValueError):
        cal.offset_at(datetime(2025, 5, 1))


def test_both_pairs_and_tz_uses_pairs_for_offset():
    """When both inputs are given, pairs win for the actual offset
    computation (more precise / can capture drift). TZ is recorded
    for the sanity check + display, but doesn't drive offset_at."""
    pair_offset = timedelta(hours=8, minutes=45, seconds=20)  # +8:45:20
    pair = _pair(
        datetime(2025, 10, 26, 9, 0, 0),
        datetime(2025, 10, 26, 9, 0, 0) + pair_offset,
    )
    cal = build_calibration(
        "G9", [pair], configured_tz=-3.0, trip_tz=5.75,
    )
    assert cal.tz_offset == timedelta(hours=8.75)  # 8:45:00 = 8.75h
    # Pair-driven offset still computed
    assert cal.offset_at(pair.camera_time) == pair_offset


def test_both_pairs_and_tz_warns_on_disagreement():
    """Pair measures +5h but TZ declaration implies +8.75h → 3.75h
    disagreement → warning emitted."""
    pair = _pair(
        datetime(2025, 10, 26, 9, 0, 0),
        datetime(2025, 10, 26, 14, 0, 0),  # +5h
    )
    cal = build_calibration(
        "G9", [pair], configured_tz=-3.0, trip_tz=5.75,
    )
    assert any("disagrees with TZ-derived" in w for w in cal.warnings)


def test_both_pairs_and_tz_no_warning_when_consistent():
    """Pair and TZ agree (within 5min threshold) → no warning."""
    pair = _pair(
        datetime(2025, 10, 26, 9, 0, 0),
        datetime(2025, 10, 26, 9, 0, 0) + timedelta(hours=8, minutes=45, seconds=10),
    )
    cal = build_calibration(
        "G9", [pair], configured_tz=-3.0, trip_tz=5.75,
    )
    assert cal.warnings == []


# ── Existing edge-case test (kept) ───────────────────────────────


def test_two_pairs_with_same_camera_time_avoids_div_by_zero():
    """Edge case: user drops two reference photos against the same
    camera shot. Bracketing logic must not divide by zero."""
    cam_t = datetime(2025, 5, 5, 10, 0, 0)
    p1 = _pair(cam_t, datetime(2025, 5, 5, 10, 4, 0), name="A")
    p2 = _pair(cam_t, datetime(2025, 5, 5, 10, 6, 0), name="B")
    # Add a third pair so the bracketing branch is hit (need
    # camera_time strictly between two pairs).
    p3 = _pair(
        datetime(2025, 5, 10, 10, 0, 0),
        datetime(2025, 5, 10, 10, 5, 0),
        name="C",
    )
    cal = build_calibration(
        "G9", [p1, p2, p3], outlier_threshold=timedelta(minutes=10),
    )
    # Query exactly at cam_t — gets the average of the two same-time
    # pairs at that point if our bracketing falls into that branch.
    # In practice the lookup goes to the start-clamp branch, but
    # the test ensures no exception is raised.
    result = cal.correct(cam_t)
    assert isinstance(result, datetime)


# ── Pair-picker TZ snap (docs/03 §"Scope expansion #2") ──────────


def _h(h: float) -> timedelta:
    return timedelta(seconds=int(round(h * 3600)))


def test_snap_to_tz_offset_whole_hour():
    # A pair-derived offset of 4:59:30 → snap to +5:00 (whole hour).
    assert snap_to_tz_offset(timedelta(hours=4, minutes=59, seconds=30)) == \
        timedelta(hours=5)
    assert snap_to_tz_offset(timedelta(hours=-3, seconds=12)) == \
        timedelta(hours=-3)


def test_snap_to_tz_offset_quarter_hour():
    # Nepal (+5:45) — within a few minutes either way should snap there.
    nepal = timedelta(hours=5, minutes=45)
    assert snap_to_tz_offset(nepal + timedelta(seconds=80)) == nepal
    assert snap_to_tz_offset(nepal - timedelta(seconds=80)) == nepal
    # India (+5:30) and Newfoundland (−3:30) — :30 zones.
    assert snap_to_tz_offset(timedelta(hours=5, minutes=29)) == \
        timedelta(hours=5, minutes=30)
    assert snap_to_tz_offset(timedelta(hours=-3, minutes=-32)) == \
        timedelta(hours=-3, minutes=-30)


def test_snap_to_tz_offset_negative_and_zero():
    assert snap_to_tz_offset(timedelta(0)) == timedelta(0)
    assert snap_to_tz_offset(timedelta(seconds=-30)) == timedelta(0)
    # Halfway between two 15-min multiples → Python's round() goes
    # to nearest even; both outcomes are sane real TZs, so just
    # assert the snap is a 15-min multiple.
    snap = snap_to_tz_offset(timedelta(minutes=7, seconds=30))
    assert (snap.total_seconds() % (15 * 60)) == 0


def test_snap_disagreement_flags_bad_pair():
    # A genuine pair: raw offset is 5:45:12 (Nepal) — disagreement
    # with the +5:45 snap is just 12 seconds → clearly a real pair.
    raw = timedelta(hours=5, minutes=45, seconds=12)
    snap = snap_to_tz_offset(raw)
    assert snap_disagreement(raw, snap) < timedelta(minutes=2)
    # A junk pair: raw is 4:00:00 — closest 15-min snap is 4:00
    # exactly (matches), but a 3:53:00 raw against a 4:00 snap is
    # 7 minutes off — useful UI signal "look again at this pair."
    raw_junk = timedelta(hours=3, minutes=53)
    snap_junk = snap_to_tz_offset(raw_junk)
    assert snap_disagreement(raw_junk, snap_junk) >= timedelta(minutes=5)
