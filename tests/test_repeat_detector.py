"""Tests for the repeat sequence detector (spec/52 Quick Sweep)."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.repeat_detector import (
    DEFAULT_REPEAT_MIN_SEQUENCE_LENGTH,
    DEFAULT_REPEAT_WINDOW_SECONDS,
    RepeatCandidate,
    RepeatDetectorConfig,
    RepeatSequence,
    detect_repeats,
)


# ─── helpers ─────────────────────────────────────────────────────────────────


def _base_ts() -> datetime:
    return datetime(2026, 6, 9, 10, 30, 0)


def _candidate(index: int, *, seconds_offset: float = 0.0) -> RepeatCandidate:
    return RepeatCandidate(
        path=Path(f"IMG_{index:04d}.jpg"),
        timestamp=_base_ts() + timedelta(seconds=seconds_offset),
    )


def _candidate_no_ts(index: int) -> RepeatCandidate:
    return RepeatCandidate(path=Path(f"IMG_NOTS_{index:04d}.jpg"), timestamp=None)


# ─── defaults ────────────────────────────────────────────────────────────────


def test_defaults_match_spec():
    """Spec/52 (Nelson 2026-06-09): 2 s span window, ≥ 2 frames per run."""
    assert DEFAULT_REPEAT_WINDOW_SECONDS == 2.0
    assert DEFAULT_REPEAT_MIN_SEQUENCE_LENGTH == 2


def test_config_defaults():
    cfg = RepeatDetectorConfig()
    assert cfg.window_seconds == 2.0
    assert cfg.min_sequence_length == 2


# ─── empty / degenerate input ────────────────────────────────────────────────


def test_empty_input_returns_no_sequences():
    assert detect_repeats([]) == []


def test_single_candidate_returns_no_sequences():
    assert detect_repeats([_candidate(1)]) == []


def test_all_candidates_without_timestamp_returns_no_sequences():
    cs = [_candidate_no_ts(i) for i in range(5)]
    assert detect_repeats(cs) == []


# ─── doublet (the headline case) ─────────────────────────────────────────────


def test_doublet_within_window_detected():
    """Tap-twice: two photos one second apart → one repeat sequence."""
    cs = [_candidate(1, seconds_offset=0.0), _candidate(2, seconds_offset=1.0)]
    seqs = detect_repeats(cs)
    assert len(seqs) == 1
    seq = seqs[0]
    assert isinstance(seq, RepeatSequence)
    assert seq.photo_count == 2
    assert seq.photos == (Path("IMG_0001.jpg"), Path("IMG_0002.jpg"))
    assert seq.representative_timestamp == _base_ts()


def test_repeat_id_is_unique_per_sequence():
    cs = [_candidate(1, seconds_offset=0.0), _candidate(2, seconds_offset=1.0)]
    seqs1 = detect_repeats(cs)
    seqs2 = detect_repeats(cs)
    assert seqs1[0].repeat_id != seqs2[0].repeat_id


# ─── three-photo run ─────────────────────────────────────────────────────────


def test_three_consecutive_within_window():
    """Three photos with span 0→2 s → one repeat sequence of length 3."""
    cs = [
        _candidate(1, seconds_offset=0.0),
        _candidate(2, seconds_offset=1.0),
        _candidate(3, seconds_offset=2.0),
    ]
    seqs = detect_repeats(cs)
    assert len(seqs) == 1
    assert seqs[0].photo_count == 3


# ─── boundary at the window threshold ────────────────────────────────────────


def test_span_equal_to_window_is_inclusive():
    """Exactly window_seconds total span counts as same run (≤, not <)."""
    cs = [
        _candidate(1, seconds_offset=0.0),
        _candidate(2, seconds_offset=2.0),
    ]
    seqs = detect_repeats(cs)
    assert len(seqs) == 1
    assert seqs[0].photo_count == 2


def test_span_just_over_window_breaks_run():
    """2.001 s span breaks the run — two isolated frames, neither
    makes a repeat (each survives alone, dropped by min_length=2)."""
    cs = [
        _candidate(1, seconds_offset=0.0),
        _candidate(2, seconds_offset=2.001),
    ]
    seqs = detect_repeats(cs)
    assert seqs == []


# ─── two distinct runs in one input ──────────────────────────────────────────


def test_two_separate_runs_detected():
    """Two doublets separated by a 60 s gap → two repeat sequences."""
    cs = [
        _candidate(1, seconds_offset=0.0),
        _candidate(2, seconds_offset=1.0),
        # 60 s pause
        _candidate(3, seconds_offset=61.0),
        _candidate(4, seconds_offset=62.0),
    ]
    seqs = detect_repeats(cs)
    assert len(seqs) == 2
    assert seqs[0].photos == (Path("IMG_0001.jpg"), Path("IMG_0002.jpg"))
    assert seqs[1].photos == (Path("IMG_0003.jpg"), Path("IMG_0004.jpg"))
    assert seqs[0].repeat_id != seqs[1].repeat_id


def test_run_then_isolated_then_run():
    """Doublet, lone photo, doublet → two sequences; the lone photo is dropped."""
    cs = [
        _candidate(1, seconds_offset=0.0),
        _candidate(2, seconds_offset=1.0),
        _candidate(3, seconds_offset=30.0),         # alone
        _candidate(4, seconds_offset=60.0),
        _candidate(5, seconds_offset=61.5),
    ]
    seqs = detect_repeats(cs)
    assert len(seqs) == 2
    assert seqs[0].photos == (Path("IMG_0001.jpg"), Path("IMG_0002.jpg"))
    assert seqs[1].photos == (Path("IMG_0004.jpg"), Path("IMG_0005.jpg"))


# ─── isolation: photos without timestamps don't kill detection ───────────────


def test_candidates_without_timestamp_are_ignored():
    """Mixing in a no-timestamp photo should NOT prevent the rest from
    forming a repeat. The no-timestamp photo just isn't part of any
    sequence."""
    cs = [
        _candidate(1, seconds_offset=0.0),
        _candidate_no_ts(99),
        _candidate(2, seconds_offset=1.0),
    ]
    seqs = detect_repeats(cs)
    assert len(seqs) == 1
    assert seqs[0].photo_count == 2
    assert seqs[0].photos == (Path("IMG_0001.jpg"), Path("IMG_0002.jpg"))


# ─── ordering: the detector sorts internally ─────────────────────────────────


def test_input_order_does_not_matter():
    cs_ordered = [
        _candidate(1, seconds_offset=0.0),
        _candidate(2, seconds_offset=1.0),
        _candidate(3, seconds_offset=2.0),
    ]
    cs_shuffled = [cs_ordered[2], cs_ordered[0], cs_ordered[1]]
    seqs_ordered = detect_repeats(cs_ordered)
    seqs_shuffled = detect_repeats(cs_shuffled)
    assert len(seqs_ordered) == 1
    assert len(seqs_shuffled) == 1
    # Paths come back in chronological order regardless of input order
    assert seqs_shuffled[0].photos == seqs_ordered[0].photos


# ─── custom config ───────────────────────────────────────────────────────────


def test_custom_window_seconds():
    cfg = RepeatDetectorConfig(window_seconds=2.0)
    cs = [
        _candidate(1, seconds_offset=0.0),
        _candidate(2, seconds_offset=2.5),     # > 2.0 s
    ]
    assert detect_repeats(cs, cfg) == []


def test_custom_min_sequence_length():
    """Bump min to 3 — a doublet should not survive."""
    cfg = RepeatDetectorConfig(min_sequence_length=3)
    cs = [
        _candidate(1, seconds_offset=0.0),
        _candidate(2, seconds_offset=1.0),
    ]
    assert detect_repeats(cs, cfg) == []


def test_custom_min_sequence_length_allows_triple():
    cfg = RepeatDetectorConfig(min_sequence_length=3)
    cs = [
        _candidate(1, seconds_offset=0.0),
        _candidate(2, seconds_offset=1.0),
        _candidate(3, seconds_offset=2.0),
    ]
    seqs = detect_repeats(cs, cfg)
    assert len(seqs) == 1
    assert seqs[0].photo_count == 3


# ─── representative_timestamp is the earliest in the run ─────────────────────


def test_representative_timestamp_is_earliest_after_internal_sort():
    """Input order shuffled — representative_timestamp must still be the
    earliest timestamp in the run, not the first one the caller passed.
    Tight timestamps so the 3-photo span (0→2 = 2 s) fits the 2 s
    window."""
    cs = [
        _candidate(2, seconds_offset=1.0),
        _candidate(1, seconds_offset=0.0),
        _candidate(3, seconds_offset=2.0),
    ]
    seqs = detect_repeats(cs)
    assert len(seqs) == 1
    assert seqs[0].photo_count == 3
    assert seqs[0].representative_timestamp == _base_ts()


# ─── span-based grouping (NOT consecutive-gap) ───────────────────────────────


def test_span_not_consecutive_gap_breaks_chain_at_window():
    """Three photos at t = 0 / 1.5 / 3 — every consecutive gap is 1.5 s
    (well within 2 s), but the total span 0→3 = 3 s exceeds the window.
    Span-based grouping closes the run at the 2 s span boundary:
    {A, B} as a doublet (span 1.5); C as a lone photo (dropped,
    length 1 < 2)."""
    cs = [
        _candidate(1, seconds_offset=0.0),
        _candidate(2, seconds_offset=1.5),
        _candidate(3, seconds_offset=3.0),
    ]
    seqs = detect_repeats(cs)
    assert len(seqs) == 1
    assert seqs[0].photo_count == 2
    assert seqs[0].photos == (Path("IMG_0001.jpg"), Path("IMG_0002.jpg"))


def test_long_tight_chain_fragments_at_span_boundary():
    """Ten photos at 1 s intervals — every consecutive gap is 1 s but
    the total span 0→9 = 9 s. Span-based grouping splits at the 2 s
    boundary: {0,1,2} → run, {3,4,5} → run, {6,7,8} → run, {9} alone
    (dropped by min_length=2)."""
    cs = [_candidate(i, seconds_offset=float(i)) for i in range(10)]
    seqs = detect_repeats(cs)
    assert len(seqs) == 3
    assert seqs[0].photo_count == 3           # 0,1,2 — span 2 ≤ 2
    assert seqs[1].photo_count == 3           # 3,4,5 — span 2 ≤ 2
    assert seqs[2].photo_count == 3           # 6,7,8 — span 2 ≤ 2


def test_three_photos_within_2s_span_form_one_repeat():
    """Three photos at t = 0, 1, 2 — span exactly 2 s. All in one run
    (span boundary is inclusive)."""
    cs = [_candidate(i, seconds_offset=float(i)) for i in range(3)]
    seqs = detect_repeats(cs)
    assert len(seqs) == 1
    assert seqs[0].photo_count == 3
