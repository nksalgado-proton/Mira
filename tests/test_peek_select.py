"""Tests for ``core.peek_select`` — spec/52 §5.6 (slice D.1.a).

Pure-logic coverage of the curated-subset algorithm: filtering (videos,
huge files), RAW+JPEG sibling dedup, time-spread sampling, edge cases
(empty input, all-untimestamped, same-timestamp burst).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import List

from core import peek_select
from core.peek_select import (
    DEFAULT_MAX_BYTES,
    DEFAULT_TARGET,
    PeekCandidate,
    select_for_peek,
    stats_for_peek,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _c(
    name: str,
    *,
    at_minutes: int = 0,
    parent: str = "/scan",
    is_video: bool = False,
    byte_size: int = 1_000_000,
) -> PeekCandidate:
    """Compact builder. ``at_minutes`` is minutes-into-day (2026-04-01)."""
    ts = datetime(2026, 4, 1, 0, 0) + timedelta(minutes=at_minutes)
    return PeekCandidate(
        path=Path(parent) / name,
        timestamp=ts,
        is_video=is_video,
        byte_size=byte_size,
    )


def _evenly_spaced(count: int, span_minutes: int = 600) -> List[PeekCandidate]:
    """``count`` candidates evenly spread over ``span_minutes``."""
    step = span_minutes / max(count - 1, 1)
    return [
        _c(f"IMG_{i:04d}.JPG", at_minutes=int(i * step))
        for i in range(count)
    ]


# --------------------------------------------------------------------------- #
# Empty / trivial input
# --------------------------------------------------------------------------- #


def test_empty_input_returns_empty():
    assert select_for_peek([]) == []


def test_target_zero_returns_empty():
    assert select_for_peek(_evenly_spaced(10), target=0) == []


def test_only_videos_returns_empty():
    items = [
        _c("CLIP1.MP4", at_minutes=10, is_video=True),
        _c("CLIP2.MP4", at_minutes=120, is_video=True),
    ]
    assert select_for_peek(items) == []


def test_only_huge_files_returns_empty():
    items = [
        _c("BIG1.JPG", at_minutes=10, byte_size=DEFAULT_MAX_BYTES + 1),
        _c("BIG2.JPG", at_minutes=20, byte_size=DEFAULT_MAX_BYTES + 1),
    ]
    assert select_for_peek(items) == []


# --------------------------------------------------------------------------- #
# Small-set short-circuit
# --------------------------------------------------------------------------- #


def test_fewer_than_target_returns_all_in_chronological_order():
    items = [
        _c("C.JPG", at_minutes=300),
        _c("A.JPG", at_minutes=10),
        _c("B.JPG", at_minutes=120),
    ]
    out = select_for_peek(items, target=20)
    assert [c.path.name for c in out] == ["A.JPG", "B.JPG", "C.JPG"]


def test_exactly_target_returns_all():
    items = _evenly_spaced(20)
    out = select_for_peek(items, target=20)
    assert len(out) == 20
    # Order preserved (already sorted by _evenly_spaced).
    assert [c.path.name for c in out] == [c.path.name for c in items]


# --------------------------------------------------------------------------- #
# Video / huge filter
# --------------------------------------------------------------------------- #


def test_videos_are_filtered_out():
    items = [
        _c("A.JPG", at_minutes=10),
        _c("CLIP.MP4", at_minutes=60, is_video=True),
        _c("B.JPG", at_minutes=120),
    ]
    out = select_for_peek(items, target=20)
    assert [c.path.name for c in out] == ["A.JPG", "B.JPG"]


def test_huge_files_are_filtered_out():
    items = [
        _c("ok.JPG", at_minutes=10, byte_size=5_000_000),
        _c("huge.RAW", at_minutes=60, byte_size=DEFAULT_MAX_BYTES + 1),
    ]
    out = select_for_peek(items, target=20)
    assert [c.path.name for c in out] == ["ok.JPG"]


def test_unknown_size_zero_passes_filter():
    """``byte_size=0`` means "unknown" — don't reject. Lets callers skip the
    stat() call when they're confident the file's reasonable."""
    items = [_c("A.JPG", byte_size=0)]
    out = select_for_peek(items, target=20)
    assert out == items


# --------------------------------------------------------------------------- #
# RAW+JPEG sibling dedup
# --------------------------------------------------------------------------- #


def test_raw_jpeg_siblings_collapse_to_jpeg():
    items = [
        _c("IMG_0001.JPG", at_minutes=10),
        _c("IMG_0001.RW2", at_minutes=10),
    ]
    out = select_for_peek(items, target=20)
    assert len(out) == 1
    assert out[0].path.suffix.lower() == ".jpg"


def test_lone_raw_survives_dedup():
    """A RAW with no JPEG sibling is still shown — decoded via embedded
    preview at the dialog layer."""
    items = [_c("IMG_0001.RW2", at_minutes=10)]
    out = select_for_peek(items, target=20)
    assert len(out) == 1
    assert out[0].path.suffix.lower() == ".rw2"


def test_heic_beats_raw_in_dedup():
    """HEIC + RAW siblings → keep HEIC (non-RAW)."""
    items = [
        _c("IMG_0001.HEIC", at_minutes=10),
        _c("IMG_0001.DNG", at_minutes=10),
    ]
    out = select_for_peek(items)
    assert len(out) == 1
    assert out[0].path.suffix.lower() == ".heic"


def test_same_stem_in_different_dirs_does_not_dedup():
    items = [
        _c("IMG_0001.JPG", at_minutes=10, parent="/scan/dayA"),
        _c("IMG_0001.JPG", at_minutes=60, parent="/scan/dayB"),
    ]
    out = select_for_peek(items, target=20)
    assert len(out) == 2


def test_stem_dedup_is_case_insensitive():
    """Some cameras emit IMG_0001.JPG + img_0001.RW2 with mixed case stems."""
    items = [
        _c("IMG_0001.JPG", at_minutes=10),
        _c("img_0001.RW2", at_minutes=10),
    ]
    out = select_for_peek(items, target=20)
    assert len(out) == 1
    assert out[0].path.suffix.lower() == ".jpg"


# --------------------------------------------------------------------------- #
# Time-spread sampling — the headline behaviour
# --------------------------------------------------------------------------- #


def test_time_spread_picks_target_count_when_oversaturated():
    items = _evenly_spaced(100, span_minutes=600)
    out = select_for_peek(items, target=20)
    assert len(out) == 20


def test_time_spread_returns_chronological_order():
    items = _evenly_spaced(100, span_minutes=600)
    out = select_for_peek(items, target=20)
    timestamps = [c.timestamp for c in out]
    assert timestamps == sorted(timestamps)


def test_time_spread_covers_the_whole_day_range():
    """spec/52 §5.6 — the peek should show an arc (morning / midday /
    evening). The first picked photo's timestamp should be near the day
    start, the last near the day end."""
    items = _evenly_spaced(100, span_minutes=600)
    out = select_for_peek(items, target=20)
    span = (items[-1].timestamp - items[0].timestamp).total_seconds()
    # First selection within the first bucket (~5% of the span).
    first_offset = (out[0].timestamp - items[0].timestamp).total_seconds()
    assert first_offset < span * 0.1
    # Last selection within the last bucket.
    last_offset = (items[-1].timestamp - out[-1].timestamp).total_seconds()
    assert last_offset < span * 0.1


def test_time_spread_does_not_cluster_at_burst():
    """A breakfast burst of 80 photos in 10 minutes + 20 scattered photos
    spanning the rest of the day must NOT produce a peek that's 80% burst.
    Index-based sampling would; time-based sampling should not."""
    burst = [
        _c(f"BURST_{i:04d}.JPG", at_minutes=60 + i // 8)         # 80 in 10 min
        for i in range(80)
    ]
    scattered = [
        _c(f"DAY_{i:04d}.JPG", at_minutes=120 + i * 30)           # 20 over 10h
        for i in range(20)
    ]
    out = select_for_peek(burst + scattered, target=20)
    assert len(out) == 20
    # No more than half of the picks should be from the burst minute window.
    from_burst = sum(1 for c in out if c.path.name.startswith("BURST"))
    assert from_burst <= 10, (
        f"breakfast burst dominated the peek: {from_burst}/20"
    )


def test_time_spread_returns_distinct_candidates():
    """Every selected candidate must be a different file — no duplicates
    even when bucket centers fall in dense clusters."""
    items = _evenly_spaced(100, span_minutes=600)
    out = select_for_peek(items, target=20)
    paths = [str(c.path) for c in out]
    assert len(set(paths)) == len(paths)


# --------------------------------------------------------------------------- #
# Edge: all-same-timestamp burst
# --------------------------------------------------------------------------- #


def test_same_timestamp_burst_collapses_to_one_representative():
    """A pure 100-photo burst all timestamped to the same second is the
    canonical "tap many times for safety" extreme. Post Nelson 2026-06-08
    collapse, those collapse to a single representative — showing 20 frames
    from the same instant would waste the budget on duplicates."""
    items = [_c(f"BURST_{i:04d}.JPG", at_minutes=60) for i in range(100)]
    out = select_for_peek(items, target=20)
    assert len(out) == 1


def test_same_timestamp_burst_keeps_distinct_picks_when_collapse_off():
    """If the host opts out of the collapse (``min_seconds_between=0``),
    the previous index-spread fallback still produces ``target`` distinct
    picks for an all-same-timestamp card. This pins the opt-out path so a
    future regression in the spread sampler is caught."""
    items = [_c(f"BURST_{i:04d}.JPG", at_minutes=60) for i in range(100)]
    out = select_for_peek(items, target=20, min_seconds_between=0)
    assert len(out) == 20
    paths = [str(c.path) for c in out]
    assert len(set(paths)) == 20


# --------------------------------------------------------------------------- #
# Untimestamped backfill
# --------------------------------------------------------------------------- #


def test_untimestamped_candidates_pad_when_under_target():
    """Spec carve-out: a stripped-EXIF photo without DateTimeOriginal can
    still show up if there's room. Deterministic by path so reruns match."""
    items = [
        PeekCandidate(path=Path("/scan/B.JPG"), timestamp=None),
        PeekCandidate(path=Path("/scan/A.JPG"), timestamp=None),
        _c("IMG.JPG", at_minutes=60),
    ]
    out = select_for_peek(items, target=20)
    names = [c.path.name for c in out]
    # The timestamped one leads; untimestamped backfill is path-sorted.
    assert names[0] == "IMG.JPG"
    assert names[1:] == ["A.JPG", "B.JPG"]


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_select_is_deterministic():
    """Same input → same output. The peek must not shuffle between opens."""
    items = _evenly_spaced(100)
    a = select_for_peek(items, target=20)
    b = select_for_peek(items, target=20)
    assert [str(c.path) for c in a] == [str(c.path) for c in b]


# --------------------------------------------------------------------------- #
# Stats — for the empty-peek hint
# --------------------------------------------------------------------------- #


def test_stats_counts_videos_and_huges():
    items = [
        _c("A.JPG", at_minutes=10),
        _c("CLIP.MP4", at_minutes=20, is_video=True),
        _c("BIG.RAW", at_minutes=30, byte_size=DEFAULT_MAX_BYTES + 1),
    ]
    stats = stats_for_peek(items)
    assert stats.total == 3
    assert stats.videos == 1
    assert stats.huge_files == 1
    assert stats.eligible == 1


def test_stats_counts_raws_skipped_by_jpeg_sibling():
    items = [
        _c("IMG_0001.JPG", at_minutes=10),
        _c("IMG_0001.RW2", at_minutes=10),
        _c("IMG_0002.RW2", at_minutes=20),                       # lone RAW survives
    ]
    stats = stats_for_peek(items)
    assert stats.raws_skipped == 1
    assert stats.eligible == 2


def test_stats_eligible_zero_when_all_filtered():
    items = [
        _c("CLIP.MP4", is_video=True),
        _c("BIG.JPG", byte_size=DEFAULT_MAX_BYTES + 1),
    ]
    stats = stats_for_peek(items)
    assert stats.eligible == 0


# --------------------------------------------------------------------------- #
# Module-vs-photo_decoder RAW extension sync
# --------------------------------------------------------------------------- #


def test_raw_extension_set_matches_photo_decoder():
    """peek_select duplicates the RAW extension set so it stays Qt-free
    + import-cheap. The test pins the duplication so the two can't drift
    silently — if photo_decoder gains a new RAW extension, this test
    fails until peek_select picks it up."""
    from core.photo_decoder import RAW_EXTENSIONS
    assert peek_select._RAW_EXTS == RAW_EXTENSIONS


# --------------------------------------------------------------------------- #
# Custom target
# --------------------------------------------------------------------------- #


def test_custom_target_overrides_default():
    items = _evenly_spaced(50)
    out = select_for_peek(items, target=5)
    assert len(out) == 5


def test_default_target_constant_is_twenty():
    """spec/52 §5.6 — ~20 photos per day."""
    assert DEFAULT_TARGET == 20


# --------------------------------------------------------------------------- #
# Near-duplicate collapse (Nelson 2026-06-08 — "tap twice for safety")
# --------------------------------------------------------------------------- #


def _c_seconds(name: str, *, at_seconds: int) -> PeekCandidate:
    ts = datetime(2026, 4, 1, 0, 0) + timedelta(seconds=at_seconds)
    return PeekCandidate(
        path=Path("/scan") / name,
        timestamp=ts,
        byte_size=1_000_000,
    )


def test_tap_twice_duplicate_collapses_to_one():
    """The canonical case: two phone shots three seconds apart of the same
    scene. With the 20-photo budget there are 8 distinct moments + the
    duplicate; the budget should land on 8 representatives, not waste a
    slot on the dupe."""
    items = (
        [_c_seconds(f"A{i}.JPG", at_seconds=i * 600) for i in range(8)]
        + [_c_seconds("A0_dup.JPG", at_seconds=3)]            # 3 s after A0
    )
    out = select_for_peek(items, target=20)
    paths = {c.path.name for c in out}
    assert "A0.JPG" in paths
    assert "A0_dup.JPG" not in paths                          # collapsed
    assert len(out) == 8                                       # 9 input → 8 distinct moments


def test_burst_of_ten_frames_collapses_to_one():
    """Photo-burst case — 10 frames within 6 seconds at one moment. All ten
    collapse to one representative; the surrounding distinct moments stay."""
    items = (
        [_c_seconds("EARLY.JPG", at_seconds=0)]
        + [_c_seconds(f"BURST_{i}.JPG", at_seconds=600 + i)
           for i in range(10)]                                # 10 frames at minute 10
        + [_c_seconds("LATE.JPG", at_seconds=1800)]           # minute 30
    )
    out = select_for_peek(items, target=20)
    paths = sorted(c.path.name for c in out)
    assert "EARLY.JPG" in paths
    assert "LATE.JPG" in paths
    burst_kept = [n for n in paths if n.startswith("BURST_")]
    assert len(burst_kept) == 1
    assert len(out) == 3                                       # EARLY + 1 burst + LATE


def test_well_spaced_photos_not_collapsed():
    """Slow-walking case — adjacent photos are well above the 15 s window.
    Every photo survives the collapse step."""
    items = [_c_seconds(f"P{i}.JPG", at_seconds=i * 60)        # 1 minute apart
             for i in range(10)]
    out = select_for_peek(items, target=20)
    assert len(out) == 10                                      # nothing collapsed


def test_collapse_window_threshold_is_inclusive_of_gap():
    """A gap EQUAL to the window is treated as a new moment, not a duplicate
    (>= comparison). 15 s default → a 15 s gap survives."""
    items = [
        _c_seconds("A.JPG", at_seconds=0),
        _c_seconds("B.JPG", at_seconds=15),                   # exactly at window
        _c_seconds("C.JPG", at_seconds=30),
    ]
    out = select_for_peek(items, target=20)
    assert len(out) == 3


def test_collapse_can_be_disabled():
    """min_seconds_between=0 opts out of the collapse step entirely."""
    items = [_c_seconds(f"P{i}.JPG", at_seconds=i)            # 1 s apart
             for i in range(10)]
    out = select_for_peek(items, target=20, min_seconds_between=0)
    assert len(out) == 10                                      # no collapse


def test_untimestamped_photos_pass_through_collapse():
    """Photos with no timestamp can't be compared temporally — they pass
    through the collapse unchanged and stay countable in the budget."""
    items = [
        _c_seconds("A.JPG", at_seconds=0),
        PeekCandidate(path=Path("/scan/NO_TS.JPG"), timestamp=None,
                      byte_size=1_000_000),
        _c_seconds("B.JPG", at_seconds=600),
    ]
    out = select_for_peek(items, target=20)
    paths = {c.path.name for c in out}
    assert "NO_TS.JPG" in paths
    assert len(out) == 3
