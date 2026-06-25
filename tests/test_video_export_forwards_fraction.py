"""spec/139 §2 — ``video_export_run`` forwards ``done_frames / total_frames``
as a per-file fraction (was discarded for cancel-only before).

These tests pin the new ``on_file_fraction`` channel on
:func:`core.video_export_run.export_processed_clip` end-to-end:

  * The numpy pipe (anything with colour / crop work) emits fraction
    ticks every ``_PROGRESS_EVERY`` frames, with the final tick at
    ``1.0`` once the encode lands.
  * The ffmpeg-only fast path (no per-frame numpy work) emits a single
    ``1.0`` on completion — videos that don't expose intermediate
    frame data still snap the per-file bar full at the end.
  * The signature change is backwards-compatible: omitting
    ``on_file_fraction`` keeps the existing cancel-only contract
    intact.
"""
from __future__ import annotations

import pytest

from core.photo_render import Params
from core.video_export import ExportPlan
from core.video_export_run import export_processed_clip
from core.video_extract import _make_test_video


def _plan(**over):
    base = dict(
        in_ms=0, out_ms=1000, params=Params(), crop_norm=None, box_angle=0.0,
        include_audio=True, audio_volume=1.0, audio_fade_ms=0, speed=1.0,
        stabilise=0.0, src_fps=30.0,
    )
    base.update(over)
    return ExportPlan(**base)


@pytest.fixture
def src(tmp_path):
    return _make_test_video(
        tmp_path / "src.mp4", duration_s=1.0, color="blue",
        size="320x240", fps=30)


def test_pipe_path_emits_increasing_fractions(src, tmp_path):
    """Numpy-pipe path (colour work forces it) — fractions advance
    monotonically through the encode and end at 1.0."""
    out = tmp_path / "exp.mp4"
    fractions: list[float] = []

    export_processed_clip(
        src, out,
        _plan(params=Params(exposure=0.5)),
        on_file_fraction=fractions.append,
    )
    assert out.exists()
    assert fractions, (
        "spec/139 §2: ``export_processed_clip`` must call "
        "``on_file_fraction`` while the numpy pipe encodes"
    )
    # Monotonic non-decreasing — never goes backwards.
    for prev, curr in zip(fractions, fractions[1:]):
        assert curr >= prev - 1e-9, (
            f"fractions must be monotonic; saw {prev} → {curr}")
    # All fractions are in [0, 1].
    for f in fractions:
        assert 0.0 <= f <= 1.0 + 1e-9, f"fraction out of range: {f}"
    # The terminal tick must be 1.0 — the bar snaps full at completion.
    assert fractions[-1] == pytest.approx(1.0), (
        f"final fraction must be 1.0 on success; got {fractions[-1]}"
    )


def test_fast_path_emits_one_complete_fraction(src, tmp_path):
    """ffmpeg-only fast path (no colour, no crop) — emits a single
    completion tick at 1.0 (the path is one-shot ffmpeg, so the most
    honest signal is 0→1 on success)."""
    out = tmp_path / "fast.mp4"
    fractions: list[float] = []

    export_processed_clip(
        src, out, _plan(), on_file_fraction=fractions.append)
    assert out.exists()
    assert 1.0 in fractions, (
        "spec/139 §2: the fast path must emit ``on_file_fraction(1.0)`` "
        f"on completion; got {fractions}"
    )
    # Last value must be 1.0 even if the fast-path cancel-poll emits
    # zeros along the way.
    assert fractions[-1] == pytest.approx(1.0)


def test_omitting_on_file_fraction_is_backwards_compatible(src, tmp_path):
    """Existing callers that don't pass ``on_file_fraction`` (cancel-
    only contract) still work — the parameter is optional."""
    out = tmp_path / "compat.mp4"
    export_processed_clip(src, out, _plan(params=Params(exposure=0.5)))
    assert out.exists()
