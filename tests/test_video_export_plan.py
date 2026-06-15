"""Tests for core.video_export.build_export_plan — the pure resolver.

spec/56 fold (2026-06-15) — the marker-partition model deprecates the
``trim_*_delta_ms`` arithmetic: the segment's ``(in_ms, out_ms)`` bounds
ARE the trim (markers define them). The plan resolver now takes those
bounds verbatim and only resolves colour / crop / audio / speed /
stabilise from a duck-typed refinements object (a ``VideoAdjustment``
in production; a simple dataclass shim in tests).
"""

from dataclasses import dataclass
from typing import Optional

from core.photo_render import Params
from core.video_export import build_export_plan


@dataclass
class _ShimOverride:
    """Minimal shape :func:`build_export_plan` reads. Stand-in for the
    legacy ``VideoOverride`` in tests; production passes a
    ``VideoAdjustment`` (or a thin shim built from it)."""

    params: Optional[Params] = None
    crop_norm: Optional[tuple[float, float, float, float]] = None
    box_angle: float = 0.0
    include_audio: Optional[bool] = None
    audio_volume: Optional[float] = None
    audio_fade_ms: int = 0
    speed: float = 1.0
    stabilise: float = 0.0
    filter_recipe: Optional[dict] = None
    filter_amount: float = 1.0


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


def test_bounds_are_taken_verbatim_no_trim_arithmetic():
    """spec/56 §1: markers ARE the trim. The plan's range is the
    range the caller passes — there's no inward shave."""
    p = build_export_plan(
        _ShimOverride(), clip_start_ms=1300, clip_end_ms=4500, src_fps=30.0)
    assert p.in_ms == 1300
    assert p.out_ms == 4500


def test_degenerate_bounds_guarded():
    p = build_export_plan(
        _ShimOverride(), clip_start_ms=2000, clip_end_ms=2000, src_fps=30.0)
    assert p.out_ms > p.in_ms


def test_colour_crop_speed_audio_stabilise_resolve():
    look = Params(exposure=0.5, vibrance=20)
    ov = _ShimOverride(
        params=look, box_angle=18.0, crop_norm=(0.1, 0.1, 0.8, 0.8),
        include_audio=False, audio_volume=0.5, audio_fade_ms=300,
        speed=0.5, stabilise=40.0,
    )
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
