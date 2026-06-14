"""Tests for core.video_export_run.export_processed_clip.

Uses the bundled ffmpeg to export tiny synthesized clips and asserts the
structural result (exists, probes as a valid video, expected dimensions
after crop, expected duration after speed). Pixel-exact colour parity is
covered by construction (the same apply_params the preview uses) — these
tests pin the pipeline plumbing.
"""

from __future__ import annotations

import pytest

from core.photo_render import Params
from core.video_export import ExportPlan
from core.video_export_run import _atempo_chain, export_processed_clip
from core.video_extract import _make_test_video, probe_video


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


def test_identity_export_preserves_dims_and_duration(src, tmp_path):
    out = tmp_path / "out.mp4"
    export_processed_clip(src, out, _plan())
    assert out.exists() and out.stat().st_size > 0
    meta = probe_video(out)
    assert (meta.width, meta.height) == (320, 240)
    assert 800 <= meta.duration_ms <= 1200


def test_crop_changes_output_dimensions(src, tmp_path):
    out = tmp_path / "cropped.mp4"
    export_processed_clip(src, out, _plan(crop_norm=(0.25, 0.25, 0.5, 0.5)))
    meta = probe_video(out)
    # 0.5 of 320x240 = 160x120 (both even — no extra trim).
    assert (meta.width, meta.height) == (160, 120)


def test_speed_halves_duration(src, tmp_path):
    out = tmp_path / "fast.mp4"
    export_processed_clip(src, out, _plan(speed=2.0))
    meta = probe_video(out)
    # 2x speed → ~0.5s.
    assert 350 <= meta.duration_ms <= 650


def test_colour_export_runs_and_stays_valid(src, tmp_path):
    out = tmp_path / "colour.mp4"
    export_processed_clip(src, out, _plan(params=Params(exposure=0.8, vibrance=20)))
    meta = probe_video(out)
    assert (meta.width, meta.height) == (320, 240)
    assert meta.duration_ms > 0


def test_box_rotation_export_runs(src, tmp_path):
    out = tmp_path / "rot.mp4"
    export_processed_clip(
        src, out, _plan(crop_norm=(0.2, 0.2, 0.6, 0.6), box_angle=15.0))
    assert out.exists()
    meta = probe_video(out)
    assert meta.width > 0 and meta.height > 0


def test_trim_subrange_duration(src, tmp_path):
    out = tmp_path / "trim.mp4"
    export_processed_clip(src, out, _plan(in_ms=200, out_ms=700))
    meta = probe_video(out)
    assert 300 <= meta.duration_ms <= 700        # ~0.5s window


def test_cancel_removes_partial_fast_path(src, tmp_path):
    from core.video_export_run import _Cancelled
    out = tmp_path / "cancel.mp4"
    # No colour/crop → fast path; cancel fires before the single pass.
    with pytest.raises(_Cancelled):
        export_processed_clip(src, out, _plan(), progress=lambda d, t: False)
    assert not out.exists()


def test_cancel_removes_partial_numpy_pipe(src, tmp_path):
    from core.video_export_run import _Cancelled
    out = tmp_path / "cancel_pipe.mp4"
    # Colour → the threaded decode→numpy→encode pipe; cancel mid-stream.
    with pytest.raises(_Cancelled):
        export_processed_clip(
            src, out, _plan(params=Params(exposure=0.5)),
            progress=lambda d, t: False)
    assert not out.exists()


def test_atempo_chain():
    assert _atempo_chain(2.0) == ["atempo=2"]
    assert _atempo_chain(1.0) == ["atempo=1"]
    assert _atempo_chain(4.0) == ["atempo=2", "atempo=2"]
    assert _atempo_chain(0.25) == ["atempo=0.5", "atempo=0.5"]


# ── Encoder detection — probe + cache live in core.encoder_ladder
#    (spec/60 §4); video_export_run delegates. Same contract, broader
#    coverage (NVENC → QSV → AMF → libx264) in test_encoder_ladder.

def test_detect_encoder_uses_nvenc_when_probe_succeeds(monkeypatch):
    from core import encoder_ladder
    encoder_ladder._reset_cache_for_tests()
    monkeypatch.setattr(
        encoder_ladder, "_run_hidden",
        lambda *a, **k: type("R", (), {"returncode": 0, "stderr": ""})())
    args = encoder_ladder.detect_encoder_args()
    assert "h264_nvenc" in args
    assert "-pix_fmt" in args and "yuv420p" in args


def test_detect_encoder_falls_back_to_libx264(monkeypatch):
    from core import encoder_ladder
    encoder_ladder._reset_cache_for_tests()
    monkeypatch.setattr(
        encoder_ladder, "_run_hidden",
        lambda *a, **k: type("R", (), {"returncode": 1, "stderr": "no gpu"})())
    args = encoder_ladder.detect_encoder_args()
    assert "libx264" in args
    assert "h264_nvenc" not in args


def test_detect_encoder_falls_back_when_probe_raises(monkeypatch):
    from core import encoder_ladder
    encoder_ladder._reset_cache_for_tests()

    def _boom(*a, **k):
        raise OSError("ffmpeg missing")

    monkeypatch.setattr(encoder_ladder, "_run_hidden", _boom)
    args = encoder_ladder.detect_encoder_args()
    assert "libx264" in args
