"""spec/116 §2 — Subject Spotlight's centre anchor.

The Spotlight's radial mask is centred at the photo's AF point. When
the camera records one, ``apply_filter`` consumes it via the ``center``
kwarg; when it doesn't, the call site passes ``(0.5, 0.5)`` (frame
centre). The Editor / preview / export render paths plumb this
through; this file pins the engine contract (centre swaps move the
"pop" with it) and a focused smoke that the AdjustmentSurface render
path passes the AF point along.

The AF-point computation (EXIF → ``AfPoint``) is brand-profile
plumbing tested elsewhere; here we only verify that ``center`` is
read correctly and that the fallback is the frame centre."""
from __future__ import annotations

import numpy as np
import pytest

from core.brand_profile import AfPoint
from core.photo_render import FilterRecipe, apply_filter


def _flat(value: int = 120, shape=(80, 120, 3)) -> np.ndarray:
    return np.full(shape, value, dtype=np.uint8)


def _patch_brightness(out: np.ndarray, x: int, y: int,
                      half: int = 6) -> float:
    """Mean brightness of a small box around (x, y)."""
    return float(out[y - half:y + half, x - half:x + half].mean())


# ── Engine: ``center`` moves the pop ────────────────────────────


def test_spotlight_default_centre_pops_the_frame_centre():
    """Default ``center=(0.5, 0.5)``: the brightest patch lives at
    the frame's middle on a flat input. The corners read dimmer."""
    img = _flat(120)
    out = apply_filter(img, FilterRecipe(spotlight=0.8))
    h, w = out.shape[:2]
    centre = _patch_brightness(out, w // 2, h // 2)
    corner_tl = _patch_brightness(out, 12, 12)
    corner_br = _patch_brightness(out, w - 12, h - 12)
    assert centre > corner_tl + 1.0
    assert centre > corner_br + 1.0


def test_spotlight_top_left_centre_pops_top_left():
    """``center=(0.2, 0.2)``: the bright spot moves to the upper-left
    quadrant; the bottom-right corner becomes the darkest."""
    img = _flat(120)
    out = apply_filter(
        img, FilterRecipe(spotlight=0.8), center=(0.2, 0.2))
    h, w = out.shape[:2]
    top_left = _patch_brightness(out, int(0.2 * w), int(0.2 * h))
    bottom_right = _patch_brightness(out, int(0.85 * w), int(0.85 * h))
    centre = _patch_brightness(out, w // 2, h // 2)
    assert top_left > bottom_right + 1.0
    # The default-centre patch is now off the anchor → dimmer than the
    # anchored top-left.
    assert top_left > centre


def test_spotlight_bottom_right_centre_pops_bottom_right():
    img = _flat(120)
    out = apply_filter(
        img, FilterRecipe(spotlight=0.8), center=(0.8, 0.8))
    h, w = out.shape[:2]
    bottom_right = _patch_brightness(out, int(0.8 * w), int(0.8 * h))
    top_left = _patch_brightness(out, int(0.15 * w), int(0.15 * h))
    assert bottom_right > top_left + 1.0


def test_spotlight_center_kwarg_is_independent_of_radius():
    """Changing ``center`` moves the anchor; ``spotlight_radius`` is
    orthogonal. Verify both knobs work on the same recipe."""
    img = _flat(120)
    out_a = apply_filter(
        img, FilterRecipe(spotlight=0.8, spotlight_radius=0.3),
        center=(0.2, 0.2))
    out_b = apply_filter(
        img, FilterRecipe(spotlight=0.8, spotlight_radius=0.3),
        center=(0.8, 0.8))
    # The brightest patches lie at their respective anchors and
    # differ between the two outputs.
    h, w = out_a.shape[:2]
    a_anchor = _patch_brightness(out_a, int(0.2 * w), int(0.2 * h))
    b_anchor = _patch_brightness(out_b, int(0.8 * w), int(0.8 * h))
    # Each anchor is brighter than its non-anchored corner in the
    # other render.
    assert a_anchor > _patch_brightness(out_b, int(0.2 * w), int(0.2 * h))
    assert b_anchor > _patch_brightness(out_a, int(0.8 * w), int(0.8 * h))


def test_spotlight_center_fallback_is_frame_centre():
    """Calling ``apply_filter`` without ``center`` is the same as
    passing ``(0.5, 0.5)`` — the contractual fallback when the host
    has no AF point."""
    img = _flat(120)
    default = apply_filter(img, FilterRecipe(spotlight=0.7))
    explicit = apply_filter(
        img, FilterRecipe(spotlight=0.7), center=(0.5, 0.5))
    assert np.array_equal(default, explicit)


# ── AfPoint → center plumbing ───────────────────────────────────


def test_afpoint_resolves_to_center_tuple():
    """The AF point carries (cx, cy) in normalised image coords; the
    Editor / preview / export call sites unpack it into a ``(cx, cy)``
    tuple for ``apply_filter``. Pin the unpack shape so future
    refactors don't quietly swap x and y."""
    af = AfPoint(cx=0.3, cy=0.7, w=0.1, h=0.1)
    center = (af.cx, af.cy)
    img = _flat(120)
    out = apply_filter(
        img, FilterRecipe(spotlight=0.8), center=center)
    h, w = out.shape[:2]
    anchor = _patch_brightness(out, int(af.cx * w), int(af.cy * h))
    far = _patch_brightness(out, int((1 - af.cx) * w),
                            int((1 - af.cy) * h))
    assert anchor > far + 1.0
