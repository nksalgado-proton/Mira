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


# ── spec/150 §3 — ``-shortest`` keeps the muxed clip ending on the
#    last video frame. Without it, AAC priming/padding makes the audio
#    container run tens of ms longer than video, and every player
#    holds the frozen last frame until audio ends. Pin the flag in
#    BOTH encode-command builders (numpy-pipe + fast-path).

from pathlib import Path

from core.video_export_run import _run_ffmpeg_only, _start_encode


class _CapturingPopen:
    """Stand-in for ``subprocess.Popen`` — records the cmd list and
    presents the minimal surface ``_start_encode`` /
    ``_run_ffmpeg_only`` use (``stdin``, ``stderr``, ``wait``,
    ``poll``, ``kill``, ``returncode``). Lets the test assert flags
    without spinning up ffmpeg."""

    last_cmd: list = []

    def __init__(self, cmd, **kwargs):
        type(self).last_cmd = list(cmd)
        self.stdin = None
        self.stdout = None
        # ``_run_ffmpeg_only`` reads stderr on a non-zero exit; provide
        # a tiny stand-in so the no-op path doesn't AttributeError.
        self.stderr = type("Stderr", (), {"read": lambda self_: b""})()
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0

    def kill(self):
        pass


def _assert_shortest_before_output(cmd: list, output_path: Path) -> None:
    """``-shortest`` is an output option (per ffmpeg convention) and
    must appear after all input/filter flags and before the output
    path. Together with ``-map 0:v`` (where present), this keeps the
    video stream as the duration authority."""
    assert "-shortest" in cmd, (
        f"spec/150 §3: encode cmd must include -shortest; got {cmd!r}"
    )
    shortest_pos = cmd.index("-shortest")
    out_pos = cmd.index(str(output_path))
    assert shortest_pos < out_pos, (
        "spec/150 §3: -shortest must appear before the output path "
        f"(found at idx {shortest_pos}, output at {out_pos}); "
        f"cmd={cmd!r}"
    )


def test_start_encode_includes_shortest_with_audio(tmp_path, monkeypatch):
    """``_start_encode`` (numpy-pipe encode) emits ``-shortest`` when
    audio is mapped — this is the path the bug actually shows in."""
    out = tmp_path / "audio.mp4"
    monkeypatch.setattr(
        "core.video_export_run.subprocess.Popen", _CapturingPopen)
    _start_encode(
        Path("ignored.mp4"), out, _plan(include_audio=True),
        out_w=320, out_h=240, in_s=0.0, dur_s=1.0)
    _assert_shortest_before_output(_CapturingPopen.last_cmd, out)
    # Sanity-check the audio mapping is intact (video first, then
    # audio from the second input).
    cmd = _CapturingPopen.last_cmd
    assert ["-map", "0:v"] == cmd[cmd.index("-map"):cmd.index("-map") + 2], (
        f"spec/150 §3: -map 0:v must remain first; cmd={cmd!r}"
    )


def test_start_encode_includes_shortest_without_audio(tmp_path, monkeypatch):
    """The no-audio path (``-an``) — single video stream — must also
    pass ``-shortest`` so the command shape is uniform. With only one
    stream the flag is a no-op, which is what we want (no surprises
    on muted clips)."""
    out = tmp_path / "muted.mp4"
    monkeypatch.setattr(
        "core.video_export_run.subprocess.Popen", _CapturingPopen)
    _start_encode(
        Path("ignored.mp4"), out, _plan(include_audio=False),
        out_w=320, out_h=240, in_s=0.0, dur_s=1.0)
    _assert_shortest_before_output(_CapturingPopen.last_cmd, out)
    assert "-an" in _CapturingPopen.last_cmd


def test_run_ffmpeg_only_includes_shortest_with_audio(
        src, tmp_path, monkeypatch):
    """``_run_ffmpeg_only`` (single-pass fast path) emits ``-shortest``
    when audio is included. ``_make_test_video`` produces a silent
    clip, but the cmd builder is what we're pinning — the audio
    branch path is selected by ``plan.include_audio``, not by the
    source actually carrying audio."""
    out = tmp_path / "fast_audio.mp4"
    monkeypatch.setattr(
        "core.video_export_run.subprocess.Popen", _CapturingPopen)
    _run_ffmpeg_only(
        src, out, _plan(include_audio=True),
        in_s=0.0, dur_s=1.0, decode_vf=[],
        progress=None, on_file_fraction=None, timeout=60.0)
    _assert_shortest_before_output(_CapturingPopen.last_cmd, out)


def test_run_ffmpeg_only_includes_shortest_without_audio(
        src, tmp_path, monkeypatch):
    """Same uniformity guarantee for the muted fast path."""
    out = tmp_path / "fast_muted.mp4"
    monkeypatch.setattr(
        "core.video_export_run.subprocess.Popen", _CapturingPopen)
    _run_ffmpeg_only(
        src, out, _plan(include_audio=False),
        in_s=0.0, dur_s=1.0, decode_vf=[],
        progress=None, on_file_fraction=None, timeout=60.0)
    _assert_shortest_before_output(_CapturingPopen.last_cmd, out)
    assert "-an" in _CapturingPopen.last_cmd


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
