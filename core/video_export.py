"""Process-phase clip export — the engine that materialises a clip's
refinements into a new video file (docs/26 §6–7, Phase 4).

A clip's refinements live on its :class:`~mira.store.models.VideoAdjustment`
row (spec/56) — the colour/crop look made on a representative frame (tone
Params + Vibrance + crop + Box-Rotation + style) and the video-only temporal
tools (audio mute/volume/fade, speed, stabilisation). The marker-partition
model owns the timeline (spec/56 §1): a clip's ``(in_ms, out_ms)`` IS the
segment's derived bounds — the legacy trim-delta arithmetic retired with
the F-029 ``core.video_overrides`` module (2026-06-15 Surface 12 fold).

This module is split in two:

* :func:`build_export_plan` — **pure** resolution of a refinements object
  (a duck-typed shape exposing ``params`` / ``crop_norm`` / ``box_angle`` /
  ``include_audio`` / ``audio_volume`` / ``audio_fade_ms`` / ``speed`` /
  ``stabilise`` / optional ``filter_recipe`` / ``filter_amount``) into an
  :class:`ExportPlan`. No ffmpeg, no files — fully unit-testable.
* :func:`export_processed_clip` — runs the plan (in ``core/video_export_run``)
  through ffmpeg + our exact numpy colour/crop pipeline.

**Colour parity (docs/26 §7, RATIFIED exact).** Colour + crop + Box-Rotation are
applied **per frame in numpy** with the *same* :func:`core.photo_render.
apply_params` / :func:`~core.photo_render.extract_rotated_crop` the sub-surface
previews with — so the exported look is identical to the preview by
construction. ffmpeg handles decode, stabilisation, speed and audio; the video
frames stream decode → numpy → encode.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from core.photo_render import Params

__all__ = ["ExportPlan", "build_export_plan"]


@dataclass(frozen=True)
class ExportPlan:
    """A fully-resolved recipe for exporting one clip. All ambiguity
    (defaults, clamping, the trim arithmetic) is settled here so the
    runner is a straight translation to ffmpeg + numpy."""

    in_ms: int                                       # effective in-point
    out_ms: int                                      # effective out-point
    params: Params                                   # colour (tone + Vibrance)
    crop_norm: Optional[tuple[float, float, float, float]]
    box_angle: float
    include_audio: bool
    audio_volume: float                              # 1.0 = unchanged
    audio_fade_ms: int                               # in + out fade
    speed: float                                     # 1.0 = normal
    stabilise: float                                 # 0 = off; 1..100 strength
    src_fps: float
    # spec/55 creative filter — a FilterRecipe dict (or None), applied
    # per frame AFTER params, mirroring the photo pipeline. Defaulted
    # so legacy callers/plans are untouched. ``filter_amount`` is the
    # spec/54 §4.1 calibration trim, resolved at plan build.
    filter_recipe: Optional[dict] = None
    filter_amount: float = 1.0

    @property
    def duration_ms(self) -> int:
        return max(0, self.out_ms - self.in_ms)

    @property
    def has_colour(self) -> bool:
        return not self.params.is_identity

    @property
    def has_crop(self) -> bool:
        return self.crop_norm is not None or abs(self.box_angle) > 1e-3

    @property
    def stabilise_on(self) -> bool:
        return self.stabilise > 0.0


def build_export_plan(
    override: Optional[Any],
    *,
    clip_start_ms: int,
    clip_end_ms: int,
    src_fps: float,
) -> ExportPlan:
    """Resolve a refinements ``override`` (+ the clip's raw
    ``[start, end]`` range and the source fps) into an :class:`ExportPlan`.

    The range ``(clip_start_ms, clip_end_ms)`` IS the trim — markers
    define the segment's bounds (spec/56 §1, 2026-06-15 fold). The
    legacy ``trim_start_delta_ms`` / ``trim_end_delta_ms`` deltas are
    gone; callers slice the segment at the marker positions and pass
    those bounds in directly.

    Every other field falls back to its no-op default when the override
    is absent or leaves it unset (colour = identity, no crop, audio kept
    at full volume, speed 1×, no stabilisation)."""
    ov = override
    in_ms = int(clip_start_ms)
    out_ms = int(clip_end_ms)
    if out_ms <= in_ms:                              # degenerate bounds guard
        out_ms = in_ms + 1

    params = ov.params if (ov and getattr(ov, "params", None) is not None) \
        else Params()
    crop_norm = getattr(ov, "crop_norm", None) if ov else None
    box_angle = (float(ov.box_angle)
                 if (ov and getattr(ov, "box_angle", 0)) else 0.0)

    include_audio = True if (
        ov is None or getattr(ov, "include_audio", None) is None
    ) else bool(ov.include_audio)
    audio_volume = float(ov.audio_volume) if (
        ov and getattr(ov, "audio_volume", None) is not None) else 1.0
    audio_fade_ms = (int(ov.audio_fade_ms)
                     if (ov and getattr(ov, "audio_fade_ms", 0)) else 0)
    speed = float(ov.speed) if (
        ov and getattr(ov, "speed", 0) and ov.speed > 0) else 1.0
    stabilise = (float(ov.stabilise)
                 if (ov and getattr(ov, "stabilise", 0)) else 0.0)

    return ExportPlan(
        in_ms=in_ms,
        out_ms=out_ms,
        params=params,
        crop_norm=crop_norm,
        box_angle=box_angle,
        include_audio=include_audio,
        audio_volume=audio_volume,
        audio_fade_ms=audio_fade_ms,
        speed=speed,
        stabilise=stabilise,
        src_fps=float(src_fps) if src_fps and src_fps > 0 else 30.0,
        filter_recipe=getattr(ov, "filter_recipe", None) if ov else None,
        filter_amount=float(getattr(ov, "filter_amount", 1.0) or 1.0)
        if ov else 1.0,
    )
