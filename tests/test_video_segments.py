"""Tests for ``core.video_segments`` — the marker-partition derivation (spec/56 §1).

Pure logic: markers + duration → per-index segment geometry. The locked model:
segments tile ``[0, duration]`` with no gaps and no overlaps; a video with zero
markers is ONE segment (whole-video export is not a special case); identity is
the index, geometry is derived here at read time.
"""
from __future__ import annotations

import pytest

from core.video_segments import containing_segment, segment_bounds


def test_zero_markers_is_one_whole_segment():
    assert segment_bounds([], 12_000) == [(0, 12_000)]


def test_markers_partition_with_no_gaps_no_overlaps():
    bounds = segment_bounds([4_000, 9_000], 12_000)
    assert bounds == [(0, 4_000), (4_000, 9_000), (9_000, 12_000)]
    # tiling invariant: each out is the next in; ends pin to 0/duration
    assert bounds[0][0] == 0 and bounds[-1][1] == 12_000
    assert all(a[1] == b[0] for a, b in zip(bounds, bounds[1:]))


def test_bounds_count_is_markers_plus_one():
    for markers in ([], [1], [1, 2], [10, 20, 30, 40]):
        assert len(segment_bounds(markers, 100)) == len(markers) + 1


def test_validation_rejects_bad_inputs():
    with pytest.raises(ValueError):
        segment_bounds([], 0)                    # no duration
    with pytest.raises(ValueError):
        segment_bounds([5_000, 5_000], 10_000)   # not strictly ascending
    with pytest.raises(ValueError):
        segment_bounds([9_000, 4_000], 10_000)   # descending
    with pytest.raises(ValueError):
        segment_bounds([0], 10_000)              # shadows the implicit start
    with pytest.raises(ValueError):
        segment_bounds([10_000], 10_000)         # shadows the implicit end


def test_containing_segment_half_open_with_closed_tail():
    markers = [4_000, 9_000]
    assert containing_segment(markers, 0, 12_000) == 0
    assert containing_segment(markers, 3_999, 12_000) == 0
    # a position ON a marker belongs to the segment STARTING there (the
    # split index for marker insertion)
    assert containing_segment(markers, 4_000, 12_000) == 1
    assert containing_segment(markers, 8_999, 12_000) == 1
    assert containing_segment(markers, 9_000, 12_000) == 2
    # the last segment is closed at the far end
    assert containing_segment(markers, 12_000, 12_000) == 2
    with pytest.raises(ValueError):
        containing_segment(markers, 12_001, 12_000)
    with pytest.raises(ValueError):
        containing_segment(markers, -1, 12_000)
