"""spec/127 §1.1 — trip-TZ segment derivation.

A segment groups plan days by their shared ``trip_day.tz_minutes``
value. Cameras "present" in a segment are those with at least one
captured item on one of the segment's days. Pure-logic test against
:func:`core.tz_segments.derive_segments`.
"""
from __future__ import annotations

from core.tz_segments import derive_segments


# ── Day grouping ────────────────────────────────────────────────────────


def test_single_tz_yields_one_segment():
    """Normal trip — every day shares one TZ → one segment with every
    day in it."""
    days = {1: -180, 2: -180, 3: -180}             # -3:00 throughout
    segs = derive_segments(days)
    assert len(segs) == 1
    seg = segs[0]
    assert seg.trip_tz_seconds == -180 * 60
    assert seg.day_numbers == [1, 2, 3]
    assert seg.cameras_present == []


def test_two_tz_yields_two_segments_with_right_day_sets():
    """TZ-crossing trip (Nepal +5:45 with a Day 7 at India +5:30) →
    two segments, each carrying only its days, sorted ascending."""
    days = {1: 345, 2: 345, 3: 345, 4: 345, 5: 345, 6: 345, 7: 330}
    segs = derive_segments(days)
    assert len(segs) == 2
    # Sorted ascending by trip_tz_seconds — India first.
    assert segs[0].trip_tz_seconds == 330 * 60
    assert segs[0].day_numbers == [7]
    assert segs[1].trip_tz_seconds == 345 * 60
    assert segs[1].day_numbers == [1, 2, 3, 4, 5, 6]


def test_unset_tz_days_are_dropped_from_segments():
    """A plan day with ``tz_minutes=None`` is undecided — it shouldn't
    appear in any segment (the dialog has nothing to apply on it)."""
    days = {1: 0, 2: None, 3: 0}
    segs = derive_segments(days)
    assert len(segs) == 1
    assert segs[0].day_numbers == [1, 3]


def test_empty_plan_yields_no_segments():
    assert derive_segments({}) == []
    assert derive_segments({1: None, 2: None}) == []


# ── Camera presence per segment ─────────────────────────────────────────


def test_cameras_present_filtered_per_segment():
    """A camera that captured items on Day 1 (segment A) but not Day 7
    (segment B) shows up only in A — and vice versa."""
    days = {1: 345, 2: 345, 7: 330}
    pairs = [
        ("G9M2", 1), ("G9M2", 2),       # G9M2 in segment A (Days 1-2)
        ("GoPro", 7),                    # GoPro only in segment B (Day 7)
    ]
    segs = derive_segments(days, camera_day_pairs=pairs)
    by_tz = {s.trip_tz_seconds: s for s in segs}
    seg_a = by_tz[345 * 60]
    seg_b = by_tz[330 * 60]
    assert seg_a.cameras_present == ["G9M2"]
    assert seg_b.cameras_present == ["GoPro"]


def test_camera_in_both_segments_lists_in_both():
    """spec/127 acceptance — a camera spanning two segments must
    appear in EACH so the dialog shows two rows (so the user can set
    a correction per segment)."""
    days = {1: 345, 2: 345, 7: 330}
    pairs = [
        ("GoPro", 1), ("GoPro", 2),     # GoPro in segment A
        ("GoPro", 7),                    # AND in segment B
    ]
    segs = derive_segments(days, camera_day_pairs=pairs)
    by_tz = {s.trip_tz_seconds: s for s in segs}
    assert by_tz[345 * 60].cameras_present == ["GoPro"]
    assert by_tz[330 * 60].cameras_present == ["GoPro"]


def test_camera_on_unset_day_is_ignored():
    """A (camera, day) pair pointing at a day with ``tz_minutes=None``
    has no segment — the pair is dropped silently."""
    days = {1: 0, 2: None}
    pairs = [("G9M2", 1), ("G9M2", 2)]
    segs = derive_segments(days, camera_day_pairs=pairs)
    assert len(segs) == 1
    assert segs[0].cameras_present == ["G9M2"]


def test_cameras_present_sorted_ascending():
    """Stable UI ordering — ``cameras_present`` is sorted ascending
    regardless of insertion order in the pair iterable."""
    days = {1: 0}
    pairs = [("ZCam", 1), ("Apple", 1), ("GoPro", 1)]
    segs = derive_segments(days, camera_day_pairs=pairs)
    assert segs[0].cameras_present == ["Apple", "GoPro", "ZCam"]


def test_no_pairs_means_no_cameras():
    """``camera_day_pairs=None`` (the default) → every segment has an
    empty ``cameras_present`` list; the dialog will hide them since
    there's nothing to correct."""
    segs = derive_segments({1: 0, 2: 0})
    assert all(s.cameras_present == [] for s in segs)
