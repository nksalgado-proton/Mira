"""Tests for the bracket sequence detector."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.bracket_detector import (
    CONFIDENCE_EXIF_TAG,
    CONFIDENCE_INFERRED,
    DEFAULT_MIN_SEQUENCE_SIZE,
    DEFAULT_WINDOW_SECONDS,
    BracketCandidate,
    BracketDetectionResult,
    BracketSequence,
    DetectorConfig,
    _classify_window_as_exposure_bracket,
    _classify_window_as_focus_bracket,
    _is_constant,
    _is_monotonic,
    _same_context,
    _time_delta_seconds,
    _varies,
    _window_candidates,
    detect_brackets,
    load_detector_config,
)
from core.vocabulary import BracketType


# ---------------------------------------------------------------------------
# Helpers to build realistic candidates
# ---------------------------------------------------------------------------

def _base_ts() -> datetime:
    return datetime(2026, 4, 15, 10, 30, 0)


def _candidate(
    index: int,
    *,
    seconds_offset: float = 0.0,
    lens: str = "Leica DG 100-400",
    body: str = "panasonic_g9_ii",
    orientation: int = 1,
    focal: float = 400.0,
    aperture: float = 6.3,
    shutter: float = 1 / 500,
    iso: int = 400,
    focus_distance: float | None = None,
    ev_comp: float | None = None,
    focus_bracket_tag: bool = False,
    exposure_bracket_tag: bool = False,
    continuous_shooting: bool = False,
    sequence_number: int | None = None,
) -> BracketCandidate:
    return BracketCandidate(
        path=Path(f"P{index:04d}.RW2"),
        timestamp=_base_ts() + timedelta(seconds=seconds_offset),
        lens_name=lens,
        body_id=body,
        orientation=orientation,
        focal_length=focal,
        aperture=aperture,
        shutter_speed=shutter,
        iso=iso,
        focus_distance=focus_distance,
        exposure_compensation=ev_comp,
        focus_bracket_tag_active=focus_bracket_tag,
        exposure_bracket_tag_active=exposure_bracket_tag,
        continuous_shooting_active=continuous_shooting,
        sequence_number=sequence_number,
    )


def _make_focus_bracket_sequence(count: int = 5) -> list[BracketCandidate]:
    """Photos with constant aperture/shutter/ISO and monotonically
    increasing focus distance."""
    return [
        _candidate(
            i,
            seconds_offset=i * 0.5,
            focus_distance=0.5 + i * 0.1,
        )
        for i in range(count)
    ]


def _make_exposure_bracket_sequence(count: int = 3) -> list[BracketCandidate]:
    """Photos with constant aperture/ISO, varying shutter speed and EV comp."""
    shutters = [1 / 2000, 1 / 500, 1 / 125]
    ev_comps = [-2.0, 0.0, 2.0]
    return [
        _candidate(
            i,
            seconds_offset=i * 0.3,
            shutter=shutters[i % 3],
            ev_comp=ev_comps[i % 3],
        )
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# _same_context
# ---------------------------------------------------------------------------

def test_same_context_identical():
    a = _candidate(0)
    b = _candidate(1)
    assert _same_context(a, b) is True


def test_same_context_different_lens():
    a = _candidate(0, lens="Leica DG 100-400")
    b = _candidate(1, lens="Leica DG 12-60")
    assert _same_context(a, b) is False


def test_same_context_different_body():
    a = _candidate(0, body="g9_ii")
    b = _candidate(1, body="a6700")
    assert _same_context(a, b) is False


def test_same_context_different_orientation():
    a = _candidate(0, orientation=1)
    b = _candidate(1, orientation=6)
    assert _same_context(a, b) is False


def test_same_context_empty_lens():
    a = _candidate(0, lens="")
    b = _candidate(1, lens="")
    assert _same_context(a, b) is False


# ---------------------------------------------------------------------------
# _time_delta_seconds
# ---------------------------------------------------------------------------

def test_time_delta_basic():
    a = _candidate(0, seconds_offset=0)
    b = _candidate(1, seconds_offset=1.5)
    assert _time_delta_seconds(a, b) == pytest.approx(1.5)


def test_time_delta_none_timestamp():
    a = _candidate(0)
    a.timestamp = None
    b = _candidate(1)
    assert _time_delta_seconds(a, b) is None


# ---------------------------------------------------------------------------
# _is_constant / _is_monotonic / _varies
# ---------------------------------------------------------------------------

def test_is_constant_empty_list_is_constant():
    assert _is_constant([]) is True


def test_is_constant_single_value():
    assert _is_constant([5.0]) is True


def test_is_constant_all_equal():
    assert _is_constant([3.0, 3.0, 3.0]) is True


def test_is_constant_with_tolerance():
    assert _is_constant([3.0, 3.1, 3.0], tolerance=0.2) is True
    assert _is_constant([3.0, 3.5, 3.0], tolerance=0.2) is False


def test_is_monotonic_increasing():
    assert _is_monotonic([1.0, 2.0, 3.0]) is True


def test_is_monotonic_decreasing():
    assert _is_monotonic([3.0, 2.0, 1.0]) is True


def test_is_monotonic_non_monotonic():
    assert _is_monotonic([1.0, 3.0, 2.0]) is False


def test_is_monotonic_with_equal_values():
    # Strictly increasing/decreasing — equal values break monotonicity
    assert _is_monotonic([1.0, 1.0, 2.0]) is False


def test_is_monotonic_with_none_values():
    # Any None disqualifies monotonicity
    assert _is_monotonic([1.0, None, 3.0]) is False  # type: ignore[list-item]


def test_is_monotonic_too_short():
    assert _is_monotonic([1.0]) is False
    assert _is_monotonic([]) is False


def test_varies():
    assert _varies([1.0, 2.0, 3.0]) is True
    assert _varies([5.0, 5.0, 5.0]) is False
    assert _varies([1.0], min_range=0.0) is False  # too short


# ---------------------------------------------------------------------------
# Pass 1: windowing
# ---------------------------------------------------------------------------

def test_window_groups_close_in_time():
    candidates = [
        _candidate(i, seconds_offset=i * 0.5, focus_distance=i)
        for i in range(5)
    ]
    config = DetectorConfig()
    windows = _window_candidates(candidates, config)
    assert len(windows) == 1
    assert len(windows[0]) == 5


def test_window_splits_on_time_gap():
    # 3 photos close, then 10s gap, then 3 more close
    candidates = [
        _candidate(0, seconds_offset=0.0),
        _candidate(1, seconds_offset=0.5),
        _candidate(2, seconds_offset=1.0),
        _candidate(3, seconds_offset=15.0),  # gap
        _candidate(4, seconds_offset=15.5),
        _candidate(5, seconds_offset=16.0),
    ]
    windows = _window_candidates(candidates, DetectorConfig())
    assert len(windows) == 2
    assert len(windows[0]) == 3
    assert len(windows[1]) == 3


def test_window_splits_on_lens_change():
    candidates = [
        _candidate(0, seconds_offset=0.0, lens="Lens A"),
        _candidate(1, seconds_offset=0.5, lens="Lens A"),
        _candidate(2, seconds_offset=1.0, lens="Lens A"),
        _candidate(3, seconds_offset=1.5, lens="Lens B"),  # lens change
        _candidate(4, seconds_offset=2.0, lens="Lens B"),
        _candidate(5, seconds_offset=2.5, lens="Lens B"),
    ]
    windows = _window_candidates(candidates, DetectorConfig())
    assert len(windows) == 2


def test_window_discards_below_min_size():
    candidates = [
        _candidate(0, seconds_offset=0.0),
        _candidate(1, seconds_offset=0.5),
    ]
    windows = _window_candidates(candidates, DetectorConfig())
    # Only 2 photos — below MIN_SEQUENCE_SIZE=3
    assert windows == []


def test_window_respects_max_size():
    # Create 150 photos all in tight sequence
    candidates = [
        _candidate(i, seconds_offset=i * 0.1, focus_distance=i * 0.01)
        for i in range(150)
    ]
    config = DetectorConfig(max_sequence_size=50)
    windows = _window_candidates(candidates, config)
    # Should split into multiple windows at max size
    assert all(len(w) <= 50 for w in windows)


def test_window_skips_candidates_without_timestamps():
    good1 = _candidate(0, seconds_offset=0.0)
    no_ts = _candidate(1, seconds_offset=0.3)
    no_ts.timestamp = None
    good2 = _candidate(2, seconds_offset=0.6)
    good3 = _candidate(3, seconds_offset=0.9)

    windows = _window_candidates([good1, no_ts, good2, good3], DetectorConfig())
    # no_ts candidate is excluded; the remaining 3 form a window
    assert len(windows) == 1
    assert len(windows[0]) == 3


# ── Sequence-number reset (Nelson 2026-06-06) ────────────────────


def test_window_splits_on_sequence_number_reset():
    """Two back-to-back focus brackets shot in quick succession (gap
    well under window_seconds, same lens / body / orientation) must
    split on the camera's per-frame counter resetting from N to 1 —
    NOT get merged into one window and then arbitrarily cut at
    max_sequence_size. This is the actual fix for Nelson's 60+100
    misclustering."""
    # Bracket A: 5 frames numbered 1..5, focus near→far.
    a = [
        _candidate(i, seconds_offset=i * 0.3,
                   focus_distance=0.5 + i * 0.1,
                   sequence_number=i + 1)
        for i in range(5)
    ]
    # Bracket B: 5 more frames, starting just 0.3 s after A's last
    # frame (well within window_seconds=2), but counter resets to 1.
    b = [
        _candidate(10 + i, seconds_offset=5 * 0.3 + 0.3 + i * 0.3,
                   focus_distance=0.5 + i * 0.1,
                   sequence_number=i + 1)
        for i in range(5)
    ]
    windows = _window_candidates(a + b, DetectorConfig())
    assert len(windows) == 2, (
        f"expected two windows (sequence reset = bracket boundary), "
        f"got {len(windows)} of sizes {[len(w) for w in windows]}"
    )
    assert len(windows[0]) == 5 and len(windows[1]) == 5


def test_window_no_split_when_counter_keeps_climbing():
    """A single 8-frame bracket whose counter goes 1..8 stays as ONE
    window — the reset rule fires only on a real drop."""
    candidates = [
        _candidate(i, seconds_offset=i * 0.3,
                   focus_distance=0.5 + i * 0.1,
                   sequence_number=i + 1)
        for i in range(8)
    ]
    windows = _window_candidates(candidates, DetectorConfig())
    assert len(windows) == 1
    assert len(windows[0]) == 8


def test_window_no_split_when_counter_absent():
    """Cameras that don't write a sequence counter (both frames have
    ``sequence_number=None``) fall back to the time + size grouping
    unchanged. Regression guard: the new rule must not break unprofiled
    or non-Panasonic events."""
    candidates = [
        _candidate(i, seconds_offset=i * 0.3,
                   focus_distance=0.5 + i * 0.1)
        for i in range(5)
    ]
    windows = _window_candidates(candidates, DetectorConfig())
    assert len(windows) == 1
    assert len(windows[0]) == 5


def test_window_no_split_on_equal_counter_value():
    """An equal value (some cameras occasionally repeat under burst-
    buffer pressure) is NOT a reset — only a strict drop is."""
    candidates = [
        _candidate(0, seconds_offset=0.0, sequence_number=1),
        _candidate(1, seconds_offset=0.3, sequence_number=2),
        _candidate(2, seconds_offset=0.6, sequence_number=2),  # repeat
        _candidate(3, seconds_offset=0.9, sequence_number=3),
        _candidate(4, seconds_offset=1.2, sequence_number=4),
    ]
    windows = _window_candidates(candidates, DetectorConfig())
    assert len(windows) == 1
    assert len(windows[0]) == 5


# ── Tag-driven windowing (Nelson 2026-06-06) ─────────────────────


def test_window_tag_driven_spans_long_time_gap():
    """Frames carrying the SAME explicit focus-bracket tag stay in ONE
    window even when consecutive timestamps are far apart — the camera
    declared "these are bracket frames", we trust it (Nelson 2026-06-06:
    a slow 80-frame focus sweep at 5 fps lasts ~16 s, well above the
    2 s time-window, and was being arbitrarily cut)."""
    candidates = [
        _candidate(i, seconds_offset=i * 4.0,         # 4 s between frames
                   focus_distance=0.5 + i * 0.1,
                   focus_bracket_tag=True,
                   sequence_number=i + 1)
        for i in range(6)
    ]
    windows = _window_candidates(candidates, DetectorConfig())
    assert len(windows) == 1
    assert len(windows[0]) == 6


def test_window_tag_driven_splits_on_tag_flip():
    """A flip from focus-bracket → exposure-bracket (or vice versa)
    closes the window — different bracket kind = different cluster."""
    focus = [
        _candidate(i, seconds_offset=i * 0.5,
                   focus_bracket_tag=True,
                   sequence_number=i + 1)
        for i in range(4)
    ]
    exposure = [
        _candidate(10 + i, seconds_offset=4 * 0.5 + 0.5 + i * 0.5,
                   exposure_bracket_tag=True,
                   sequence_number=i + 1)
        for i in range(3)
    ]
    windows = _window_candidates(focus + exposure, DetectorConfig())
    assert len(windows) == 2
    assert len(windows[0]) == 4 and len(windows[1]) == 3


def test_window_tag_driven_splits_on_tag_off():
    """A tagged bracket followed by un-tagged frames closes the window
    (camera said the bracket ended). The un-tagged frames then take
    the inferred path (time-gap heuristic)."""
    tagged = [
        _candidate(i, seconds_offset=i * 0.5,
                   focus_bracket_tag=True,
                   sequence_number=i + 1)
        for i in range(4)
    ]
    untagged = [
        _candidate(10 + i, seconds_offset=4 * 0.5 + 0.3 + i * 0.5)
        for i in range(3)
    ]
    windows = _window_candidates(tagged + untagged, DetectorConfig())
    # The tagged window survives; the un-tagged trio is grouped by time
    # gap (≤ 2 s) but is just three plain frames — _classify_window
    # decides if it looks like an inferred bracket (no signal here → it
    # won't, but windowing still emits it).
    assert len(windows) == 2
    assert len(windows[0]) == 4
    assert len(windows[1]) == 3


def test_window_tag_driven_still_splits_on_sequence_reset():
    """Even with the SAME tag active, a sequence_number reset still
    closes the window — two back-to-back focus brackets fired in quick
    succession (both tagged focus, but counter resets between them)."""
    a = [
        _candidate(i, seconds_offset=i * 0.5,
                   focus_bracket_tag=True,
                   sequence_number=i + 1)
        for i in range(4)
    ]
    b = [
        _candidate(10 + i, seconds_offset=4 * 0.5 + 0.5 + i * 0.5,
                   focus_bracket_tag=True,
                   sequence_number=i + 1)
        for i in range(4)
    ]
    windows = _window_candidates(a + b, DetectorConfig())
    assert len(windows) == 2
    assert len(windows[0]) == 4 and len(windows[1]) == 4


def test_window_inferred_path_still_uses_time_gap():
    """Frames without any bracket tag still follow the classical
    time-window heuristic — backwards compatibility for cameras that
    don't write a bracket tag."""
    close = [
        _candidate(i, seconds_offset=i * 0.5,
                   focus_distance=0.5 + i * 0.1)
        for i in range(3)
    ]
    far = [
        _candidate(10 + i, seconds_offset=10.0 + i * 0.5,  # 10 s gap
                   focus_distance=0.5 + i * 0.1)
        for i in range(3)
    ]
    windows = _window_candidates(close + far, DetectorConfig())
    assert len(windows) == 2
    assert len(windows[0]) == 3 and len(windows[1]) == 3


# ---------------------------------------------------------------------------
# Pass 2 classification — focus bracket
# ---------------------------------------------------------------------------

def test_classify_focus_bracket_happy_path():
    window = _make_focus_bracket_sequence(5)
    assert _classify_window_as_focus_bracket(window, DetectorConfig()) is True


def test_classify_focus_bracket_rejects_non_monotonic_focus():
    window = _make_focus_bracket_sequence(5)
    window[2].focus_distance = 0.3  # breaks monotonicity
    assert _classify_window_as_focus_bracket(window, DetectorConfig()) is False


def test_classify_focus_bracket_rejects_varying_aperture():
    window = _make_focus_bracket_sequence(5)
    window[2].aperture = 8.0  # was 6.3
    assert _classify_window_as_focus_bracket(window, DetectorConfig()) is False


def test_classify_focus_bracket_tolerates_aperture_jitter_when_configured():
    window = _make_focus_bracket_sequence(5)
    window[2].aperture = 6.5  # slight jitter
    config = DetectorConfig(tolerate_aperture_jitter_stops=0.3)
    assert _classify_window_as_focus_bracket(window, config) is True


def test_classify_focus_bracket_requires_focus_distance():
    window = _make_focus_bracket_sequence(5)
    for c in window:
        c.focus_distance = None
    assert _classify_window_as_focus_bracket(window, DetectorConfig()) is False


# ---------------------------------------------------------------------------
# Pass 2 classification — exposure bracket
# ---------------------------------------------------------------------------

def test_classify_exposure_bracket_happy_path_shutter_varies():
    window = _make_exposure_bracket_sequence(3)
    assert _classify_window_as_exposure_bracket(window, DetectorConfig()) is True


def test_classify_exposure_bracket_rejects_varying_aperture():
    window = _make_exposure_bracket_sequence(3)
    window[1].aperture = 4.0  # was 6.3
    assert _classify_window_as_exposure_bracket(window, DetectorConfig()) is False


def test_classify_exposure_bracket_rejects_varying_iso():
    window = _make_exposure_bracket_sequence(3)
    window[1].iso = 1600  # was 400
    assert _classify_window_as_exposure_bracket(window, DetectorConfig()) is False


def test_classify_exposure_bracket_rejects_when_focus_varies():
    # Varying shutter + varying focus distance → prefer focus bracket classification,
    # so exposure bracket should return False to let focus path take precedence
    window = _make_exposure_bracket_sequence(3)
    for i, c in enumerate(window):
        c.focus_distance = 0.5 + i * 0.1
    assert _classify_window_as_exposure_bracket(window, DetectorConfig()) is False


def test_classify_exposure_bracket_rejects_constant_exposure():
    # Constant everything → not a bracket
    window = [
        _candidate(i, seconds_offset=i * 0.3)
        for i in range(3)
    ]
    assert _classify_window_as_exposure_bracket(window, DetectorConfig()) is False


def test_classify_exposure_bracket_rejects_sub_stop_shutter_jitter():
    """Regression — real G9 Dia 9 (Manuel Antonio NP), Nelson eyeball
    2026-05-17. Handheld wildlife run: constant f/6.3 + ISO 3200, no
    EV-comp, no AEB tag, shutter auto-metering 1/125->1/100 across the
    frames = 0.32 stops. That is *not* an exposure bracket; the old
    code multiplied the stops threshold by 0.0 and promoted this
    metering jitter to a false AEB."""
    window = [
        _candidate(i, seconds_offset=i * 0.4, aperture=6.3, iso=3200,
                   shutter=(0.008 if i == 0 else 0.01), ev_comp=None)
        for i in range(4)
    ]
    assert _classify_window_as_exposure_bracket(
        window, DetectorConfig()) is False


def test_classify_exposure_bracket_threshold_is_enforced_in_stops():
    """The 1.0-stop gate is real now (was neutered by ``* 0.0``):
    below it rejects, at/above it accepts. Shutter span measured in
    stops via log2(max/min)."""
    cfg = DetectorConfig()  # min_exposure_range_stops == 1.0
    # 0.9-stop shutter span (constant aperture/ISO, no tag) → reject.
    near = [
        _candidate(i, seconds_offset=i * 0.4,
                   shutter=(1 / 500 if i == 0 else (1 / 500) * (2 ** 0.9)))
        for i in range(3)
    ]
    assert _classify_window_as_exposure_bracket(near, cfg) is False
    # Exactly 1.0-stop shutter span → accept (genuine inferred AEB).
    at = [
        _candidate(i, seconds_offset=i * 0.4,
                   shutter=(1 / 500 if i == 0 else (1 / 250)))
        for i in range(3)
    ]
    assert _classify_window_as_exposure_bracket(at, cfg) is True


# ---------------------------------------------------------------------------
# detect_brackets — end-to-end
# ---------------------------------------------------------------------------

def test_detect_brackets_explicit_focus_tag():
    # EXIF tag trumps inference — no variation needed
    candidates = [
        _candidate(i, seconds_offset=i * 0.3, focus_bracket_tag=True)
        for i in range(5)
    ]
    result = detect_brackets(candidates)
    assert len(result.sequences) == 1
    seq = result.sequences[0]
    assert seq.sequence_type == BracketType.FOCUS
    assert seq.confidence == CONFIDENCE_EXIF_TAG
    assert seq.detection_source == "exif_tag"
    assert seq.photo_count == 5
    assert result.orphans == []


def test_detect_brackets_explicit_exposure_tag():
    candidates = [
        _candidate(i, seconds_offset=i * 0.3, exposure_bracket_tag=True)
        for i in range(3)
    ]
    result = detect_brackets(candidates)
    assert len(result.sequences) == 1
    assert result.sequences[0].sequence_type == BracketType.EXPOSURE
    assert result.sequences[0].detection_source == "exif_tag"


def test_detect_brackets_inferred_focus():
    candidates = _make_focus_bracket_sequence(5)
    result = detect_brackets(candidates)
    assert len(result.sequences) == 1
    seq = result.sequences[0]
    assert seq.sequence_type == BracketType.FOCUS
    assert seq.confidence == CONFIDENCE_INFERRED
    assert seq.detection_source == "inferred_focus"


def test_detect_brackets_inferred_exposure():
    candidates = _make_exposure_bracket_sequence(3)
    result = detect_brackets(candidates)
    assert len(result.sequences) == 1
    assert result.sequences[0].sequence_type == BracketType.EXPOSURE
    assert result.sequences[0].detection_source == "inferred_exposure"


def test_detect_brackets_ambiguous_window_becomes_orphans():
    # 3 photos tight in time but no parameter variation — burst, not bracket
    candidates = [
        _candidate(i, seconds_offset=i * 0.3)
        for i in range(3)
    ]
    result = detect_brackets(candidates)
    assert result.sequences == []
    assert len(result.orphans) == 3


def test_detect_brackets_isolated_photos_are_orphans():
    # Photos spaced far apart → never form a window
    candidates = [
        _candidate(0, seconds_offset=0.0, focus_distance=0.5),
        _candidate(1, seconds_offset=60.0, focus_distance=0.6),
        _candidate(2, seconds_offset=120.0, focus_distance=0.7),
    ]
    result = detect_brackets(candidates)
    assert result.sequences == []
    assert len(result.orphans) == 3


def test_continuous_burst_with_shutter_jitter_is_not_a_bracket():
    """Costa Rica field test 2026-04-30: a Nikon D7100 burst where
    the photographer manually changed shutter mid-sequence (1/400 →
    1/1000) was being inferred as an exposure bracket because the
    detector saw shutter variation. With ``continuous_shooting_active``
    set on every frame and no explicit bracket tag, the inferred path
    must hard-veto and emit orphans instead."""
    shutters = [1 / 400, 1 / 400, 1 / 400, 1 / 1000, 1 / 1000]
    candidates = [
        _candidate(
            i,
            seconds_offset=i * 0.4,
            shutter=shutters[i],
            continuous_shooting=True,
        )
        for i in range(len(shutters))
    ]
    result = detect_brackets(candidates)
    assert result.sequences == []
    assert len(result.orphans) == len(shutters)


def test_explicit_bracket_tag_wins_over_continuous_burst():
    """AEB sequences fire as a continuous burst from the camera's
    perspective — both signals are set. The explicit bracket tag
    must win so real AEB still gets detected."""
    candidates = _make_exposure_bracket_sequence(3)
    for c in candidates:
        c.continuous_shooting_active = True
        c.exposure_bracket_tag_active = True
    result = detect_brackets(candidates)
    assert len(result.sequences) == 1
    assert result.sequences[0].sequence_type == BracketType.EXPOSURE
    assert result.sequences[0].detection_source == "exif_tag"


def test_detect_brackets_mixed_sequence_and_orphans():
    focus = _make_focus_bracket_sequence(5)
    # Add an isolated photo far in the future
    lone = _candidate(99, seconds_offset=1000.0, focus_distance=5.0)
    result = detect_brackets(focus + [lone])
    assert len(result.sequences) == 1
    assert result.sequences[0].photo_count == 5
    assert result.orphans == [lone.path]


def test_detect_brackets_empty_input():
    result = detect_brackets([])
    assert result.sequences == []
    assert result.orphans == []


def test_detect_brackets_multiple_sequences():
    focus = _make_focus_bracket_sequence(5)
    # Shift the exposure bracket far in time to force a new window
    exposure = [
        _candidate(
            10 + i,
            seconds_offset=100.0 + i * 0.3,
            shutter=[1 / 2000, 1 / 500, 1 / 125][i],
            ev_comp=[-2.0, 0.0, 2.0][i],
        )
        for i in range(3)
    ]
    result = detect_brackets(focus + exposure)
    assert len(result.sequences) == 2
    types = {s.sequence_type for s in result.sequences}
    assert types == {BracketType.FOCUS, BracketType.EXPOSURE}


def test_detect_brackets_each_sequence_has_unique_id():
    candidates = _make_focus_bracket_sequence(5) + [
        _candidate(10 + i, seconds_offset=100.0 + i * 0.3, focus_distance=i * 0.2)
        for i in range(5)
    ]
    result = detect_brackets(candidates)
    assert len(result.sequences) == 2
    assert result.sequences[0].sequence_id != result.sequences[1].sequence_id


def test_detect_brackets_preserves_photo_order_by_timestamp():
    # Shuffle the input; the output sequence should be ordered by timestamp
    candidates = _make_focus_bracket_sequence(5)
    shuffled = [candidates[2], candidates[0], candidates[4], candidates[1], candidates[3]]
    result = detect_brackets(shuffled)
    assert len(result.sequences) == 1
    expected_order = [c.path for c in candidates]
    assert result.sequences[0].photos == expected_order


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def test_load_detector_config_built_in_defaults(tmp_path, monkeypatch):
    # Redirect user data dir to an empty temp dir so user override is absent
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    config = load_detector_config()
    assert isinstance(config, DetectorConfig)
    # Values from assets/bracket_detector.json should match
    assert config.window_seconds == DEFAULT_WINDOW_SECONDS
    assert config.min_sequence_size == DEFAULT_MIN_SEQUENCE_SIZE


def test_load_detector_config_user_override(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    custom = {
        "window_seconds": 5.0,
        "min_sequence_size": 4,
        "focus_bracket": {
            "tolerate_aperture_jitter_stops": 0.5,
        },
    }
    (tmp_path / "bracket_detector.json").write_text(
        json.dumps(custom), encoding="utf-8"
    )
    config = load_detector_config()
    assert config.window_seconds == 5.0
    assert config.min_sequence_size == 4
    assert config.tolerate_aperture_jitter_stops == 0.5


def test_load_detector_config_corrupted_falls_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    (tmp_path / "bracket_detector.json").write_text("{not valid", encoding="utf-8")
    config = load_detector_config()
    assert config.window_seconds == DEFAULT_WINDOW_SECONDS
