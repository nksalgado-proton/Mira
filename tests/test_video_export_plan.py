"""Tests for core.video_export.build_export_plan (the pure resolver)."""

from core.photo_render import Params
from core.video_export import build_export_plan
from core.video_overrides import set_override, get_override


def _ov(**fields):
    j: dict = {}
    set_override(j, "c1", **fields)
    return get_override(j, "c1")


def test_defaults_when_no_override():
    p = build_export_plan(None, clip_start_ms=1000, clip_end_ms=5000,
                          src_fps=25.0)
    assert p.in_ms == 1000 and p.out_ms == 5000
    assert p.params == Params()
    assert p.crop_norm is None and p.box_angle == 0.0
    assert p.include_audio is True
    assert p.audio_volume == 1.0 and p.audio_fade_ms == 0
    assert p.speed == 1.0 and p.stabilise == 0.0
    assert p.has_colour is False and p.has_crop is False
    assert p.stabilise_on is False
    assert p.duration_ms == 4000


def test_trim_shaves_inward():
    ov = _ov(trim_start_delta_ms=300, trim_end_delta_ms=-500)
    p = build_export_plan(ov, clip_start_ms=1000, clip_end_ms=5000,
                          src_fps=30.0)
    assert p.in_ms == 1300            # 1000 + 300
    assert p.out_ms == 4500           # 5000 - 500


def test_trim_clamps_outward_attempts():
    # Negative start delta / positive end delta would EXTEND — clamped out.
    ov = _ov(trim_start_delta_ms=-400, trim_end_delta_ms=800)
    p = build_export_plan(ov, clip_start_ms=1000, clip_end_ms=5000,
                          src_fps=30.0)
    assert p.in_ms == 1000            # can't go before raw start
    assert p.out_ms == 5000           # can't go past raw end


def test_colour_crop_speed_audio_stabilise_resolve():
    look = Params(exposure=0.5, vibrance=20)
    ov = _ov(params=look, box_angle=18.0, crop_norm=(0.1, 0.1, 0.8, 0.8),
             include_audio=False, audio_volume=0.5, audio_fade_ms=300,
             speed=0.5, stabilise=40.0)
    p = build_export_plan(ov, clip_start_ms=0, clip_end_ms=2000, src_fps=60.0)
    assert p.params == look and p.has_colour is True
    assert p.crop_norm == (0.1, 0.1, 0.8, 0.8) and p.box_angle == 18.0
    assert p.has_crop is True
    assert p.include_audio is False
    assert p.audio_volume == 0.5 and p.audio_fade_ms == 300
    assert p.speed == 0.5
    assert p.stabilise == 40.0 and p.stabilise_on is True
    assert p.src_fps == 60.0


def test_bad_fps_falls_back():
    p = build_export_plan(None, clip_start_ms=0, clip_end_ms=1000, src_fps=0)
    assert p.src_fps == 30.0


def test_degenerate_trim_guarded():
    # A pathological trim that crosses itself still yields out > in.
    ov = _ov(trim_start_delta_ms=10000)
    p = build_export_plan(ov, clip_start_ms=0, clip_end_ms=1000, src_fps=30.0)
    assert p.out_ms > p.in_ms
