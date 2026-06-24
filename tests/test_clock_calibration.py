"""Tests for core.clock_calibration — pair-based offset + drift interpolation.

Pure-math unit tests; no real EXIF / file I/O. spec/123 collapsed the
applied path to one ``offset_seconds`` per camera derived from one of
three sources (see ``test_tz_correction_sources.py``); this file covers
the legacy multi-pair drift-interpolation path that the Reconcile
pipeline still consumes (and the constant-offset corrections that fall
out of pair-based + TZ-based builds).
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
    assert cal.correct(cam_t) == ref_t
    later = datetime(2025, 5, 25, 18, 30, 0)
    assert cal.correct(later) == later + timedelta(minutes=5)


def test_single_pair_with_negative_offset():
    """Camera AHEAD of reference produces negative offset."""
    cam_t = datetime(2025, 5, 12, 10, 5, 0)
    ref_t = datetime(2025, 5, 12, 10, 0, 0)  # camera 5 min ahead
    cal = build_calibration("G9", [_pair(cam_t, ref_t)])
    assert cal.correct(cam_t) == ref_t


# ── Multi-pair drift interpolation (legacy Reconcile path) ──────


def test_two_pairs_interpolate_linearly_between():
    """Multi-pair drift interpolation still available on the
    :class:`CameraCalibration` for callers that need it."""
    p1 = _pair(
        datetime(2025, 5, 1, 10, 0, 0),
        datetime(2025, 5, 1, 10, 5, 0),
    )
    p2 = _pair(
        datetime(2025, 5, 11, 10, 0, 0),
        datetime(2025, 5, 11, 10, 10, 0),
    )
    # Use a CameraCalibration directly so we exercise the
    # pair-interpolation path (build_calibration takes the median delta
    # as a constant for the single-source spec/123 model).
    cal = CameraCalibration(camera_id="G9", pairs=[p1, p2])
    assert cal.has_drift_correction

    midpoint = datetime(2025, 5, 6, 10, 0, 0)
    corrected = cal.correct(midpoint)
    expected = midpoint + timedelta(minutes=7, seconds=30)
    assert corrected == expected


def test_two_pairs_clamp_before_first():
    p1 = _pair(
        datetime(2025, 5, 5, 10, 0, 0),
        datetime(2025, 5, 5, 10, 5, 0),
    )
    p2 = _pair(
        datetime(2025, 5, 15, 10, 0, 0),
        datetime(2025, 5, 15, 10, 10, 0),
    )
    cal = CameraCalibration(camera_id="G9", pairs=[p1, p2])
    very_early = datetime(2025, 5, 1, 8, 0, 0)
    assert cal.correct(very_early) == very_early + timedelta(minutes=5)


def test_two_pairs_clamp_after_last():
    p1 = _pair(
        datetime(2025, 5, 5, 10, 0, 0),
        datetime(2025, 5, 5, 10, 5, 0),
    )
    p2 = _pair(
        datetime(2025, 5, 15, 10, 0, 0),
        datetime(2025, 5, 15, 10, 10, 0),
    )
    cal = CameraCalibration(camera_id="G9", pairs=[p1, p2])
    very_late = datetime(2025, 5, 30, 22, 0, 0)
    assert cal.correct(very_late) == very_late + timedelta(minutes=10)


def test_two_pairs_unsorted_input_still_works():
    p_late = _pair(
        datetime(2025, 5, 15, 10, 0, 0),
        datetime(2025, 5, 15, 10, 10, 0),
    )
    p_early = _pair(
        datetime(2025, 5, 5, 10, 0, 0),
        datetime(2025, 5, 5, 10, 5, 0),
    )
    cal = CameraCalibration(camera_id="G9", pairs=[p_late, p_early])
    midpoint = datetime(2025, 5, 10, 10, 0, 0)
    corrected = cal.correct(midpoint)
    expected = midpoint + timedelta(minutes=7, seconds=30)
    assert corrected == expected


# ── Edge cases ────────────────────────────────────────────────────


def test_no_pairs_no_offset_raises_on_offset_at():
    """An empty calibration is a programming error — surface loudly."""
    cal = CameraCalibration(camera_id="G9", pairs=[])
    with pytest.raises(ValueError):
        cal.offset_at(datetime(2025, 5, 1))


def test_correct_camera_time_with_none_calibration_is_no_op():
    """Helper for cameras the user chose not to calibrate."""
    t = datetime(2025, 5, 1, 10, 0, 0)
    assert correct_camera_time(t, None) == t


# ── TZ-based offset (legacy Reconcile entry point) ──────────────


def test_tz_only_calibration_produces_constant_offset():
    """Camera configured to a known TZ but no pairs given. Offset is
    ``trip_tz - configured_tz`` hours, applied constant for any time."""
    cal = build_calibration(
        "G9", [], configured_tz=-3.0, trip_tz=5.75,
    )
    assert cal.has_any_source
    assert not cal.has_drift_correction  # no pairs
    assert cal.tz_offset == timedelta(hours=8.75)
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


def test_two_pairs_with_same_camera_time_avoids_div_by_zero():
    """Edge case: user drops two reference photos against the same
    camera shot. Bracketing logic must not divide by zero."""
    cam_t = datetime(2025, 5, 5, 10, 0, 0)
    p1 = _pair(cam_t, datetime(2025, 5, 5, 10, 4, 0), name="A")
    p2 = _pair(cam_t, datetime(2025, 5, 5, 10, 6, 0), name="B")
    p3 = _pair(
        datetime(2025, 5, 10, 10, 0, 0),
        datetime(2025, 5, 10, 10, 5, 0),
        name="C",
    )
    cal = CameraCalibration(camera_id="G9", pairs=[p1, p2, p3])
    result = cal.correct(cam_t)
    assert isinstance(result, datetime)
