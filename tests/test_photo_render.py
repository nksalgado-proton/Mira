"""Tests for core.photo_render — the Params dataclass and the
apply_params pipeline (Nelson 2026-05-21 Phase 3a)."""

from __future__ import annotations

import numpy as np
import pytest

from core.aspect_ratio import get_aspect_ratio
from core.photo_render import (
    Params,
    apply_crop_norm,
    apply_params,
    compute_default_crop,
)


# ── Test helpers ───────────────────────────────────────────────


def _mid_gray(h: int = 16, w: int = 16, level: int = 128) -> np.ndarray:
    """Solid-grey RGB image at the given level."""
    return np.full((h, w, 3), level, dtype=np.uint8)


def _gradient() -> np.ndarray:
    """16×256 horizontal grayscale gradient. Each column is a single
    luminance level from 0 to 255 — useful for testing tone curves."""
    row = np.arange(256, dtype=np.uint8)
    img = np.tile(row, (16, 1))
    return np.stack([img, img, img], axis=-1)


# ── Params dataclass ───────────────────────────────────────────


def test_params_default_is_identity():
    p = Params()
    assert p.is_identity is True


def test_params_any_nonzero_breaks_identity():
    assert not Params(exposure=0.1).is_identity
    assert not Params(contrast=1.0).is_identity
    assert not Params(highlights=-1.0).is_identity


def test_params_scaled_zero_is_identity():
    p = Params(exposure=1.0, shadows=50.0, contrast=20.0)
    scaled = p.scaled(0.0)
    assert scaled.is_identity is True


def test_params_scaled_one_is_unchanged():
    p = Params(exposure=1.0, shadows=50.0)
    scaled = p.scaled(1.0)
    assert scaled.exposure == p.exposure
    assert scaled.shadows == p.shadows


def test_params_scaled_doubles_at_two():
    p = Params(exposure=1.0, contrast=20.0)
    scaled = p.scaled(2.0)
    assert scaled.exposure == 2.0
    assert scaled.contrast == 40.0


# ── apply_params: identity case ────────────────────────────────


def test_apply_identity_returns_unchanged():
    img = _gradient()
    out = apply_params(img, Params())
    assert out.shape == img.shape
    assert out.dtype == np.uint8
    np.testing.assert_array_equal(out, img)


# ── apply_params: exposure ─────────────────────────────────────


def test_apply_exposure_one_stop_doubles_midgray():
    """+1 EV stop on a midgray (128) image should land near 255 (2×).
    Allow ±2 for the round-trip through float32 + uint8."""
    img = _mid_gray(level=64)        # 64 → 128 expected
    out = apply_params(img, Params(exposure=1.0))
    assert abs(int(out.mean()) - 128) <= 2


def test_apply_exposure_negative_one_stop_halves_midgray():
    img = _mid_gray(level=128)       # 128 → 64 expected
    out = apply_params(img, Params(exposure=-1.0))
    assert abs(int(out.mean()) - 64) <= 2


def test_apply_exposure_clips_at_white():
    """A bright image + extra exposure → saturates at 255, not overflow."""
    img = _mid_gray(level=200)
    out = apply_params(img, Params(exposure=2.0))
    assert out.max() == 255
    assert out.min() == 255


# ── apply_params: shadows ──────────────────────────────────────


def test_apply_shadows_lift_brightens_dark_pixels():
    """+50 shadows should brighten dark pixels but leave highlights
    nearly untouched."""
    img = _gradient()
    out = apply_params(img, Params(shadows=50.0))
    # Dark pixels (level 32) brightened.
    assert out[0, 32, 0] > img[0, 32, 0]
    # Bright pixels (level 220) ~unchanged.
    assert abs(int(out[0, 220, 0]) - int(img[0, 220, 0])) <= 3


def test_apply_shadows_zero_is_no_op():
    img = _gradient()
    out = apply_params(img, Params(shadows=0.0))
    np.testing.assert_array_equal(out, img)


# ── apply_params: highlights ───────────────────────────────────


def test_apply_highlights_pull_darkens_bright_pixels():
    """-50 highlights should darken bright pixels but leave shadows
    untouched."""
    img = _gradient()
    out = apply_params(img, Params(highlights=-50.0))
    # Bright pixel pulled down.
    assert out[0, 220, 0] < img[0, 220, 0]
    # Dark pixel ~unchanged.
    assert abs(int(out[0, 32, 0]) - int(img[0, 32, 0])) <= 3


# ── apply_params: whites + blacks (clipping point stretch) ─────


def test_apply_whites_positive_brightens_overall():
    img = _gradient()
    out = apply_params(img, Params(whites=50.0))
    # Mean brightness increased.
    assert out.mean() > img.mean()


def test_apply_blacks_positive_brightens_shadows():
    """LRC convention: +blacks brightens shadow tones (drag right →
    lift). -blacks deepens them. The render code follows that."""
    img = _gradient()
    out = apply_params(img, Params(blacks=50.0))
    # The dark end of the gradient is now BRIGHTER.
    assert out[0, 30, 0] > img[0, 30, 0]


def test_apply_blacks_negative_deepens_shadows():
    """The other half of the LRC convention: -blacks darkens
    shadows."""
    img = _gradient()
    out = apply_params(img, Params(blacks=-50.0))
    # The dark end gets crushed toward zero.
    assert out[0, 30, 0] < img[0, 30, 0]


# ── apply_params: contrast ─────────────────────────────────────


def test_apply_positive_contrast_increases_spread():
    """Positive contrast widens a *compressed* histogram (the
    typical AUTO target). A gradient already spanning 0..255 can't
    grow — use a midtone-only image so there's room to stretch."""
    # Midtones only: values 64..192 (256 cols, 128-wide span).
    row = np.linspace(64, 192, 256, dtype=np.uint8)
    img = np.stack([np.tile(row, (16, 1))] * 3, axis=-1)
    out = apply_params(img, Params(contrast=50.0))
    in_spread = float(np.percentile(img[..., 0], 95) - np.percentile(img[..., 0], 5))
    out_spread = float(np.percentile(out[..., 0], 95) - np.percentile(out[..., 0], 5))
    assert out_spread > in_spread


def test_apply_negative_contrast_compresses_spread():
    img = _gradient()
    out = apply_params(img, Params(contrast=-50.0))
    in_spread = float(np.percentile(img[..., 0], 95) - np.percentile(img[..., 0], 5))
    out_spread = float(np.percentile(out[..., 0], 95) - np.percentile(out[..., 0], 5))
    assert out_spread < in_spread


# ── apply_params: saturation ───────────────────────────────────


def test_apply_negative_saturation_collapses_to_gray():
    """-100 saturation = grayscale. All three channels should match."""
    # A colorful image: pure red on the left half, pure blue on right.
    img = np.zeros((16, 32, 3), dtype=np.uint8)
    img[:, :16, 0] = 255            # red
    img[:, 16:, 2] = 255            # blue
    out = apply_params(img, Params(saturation=-100.0))
    # R, G, B should be approximately equal across the image now.
    diff = out.astype(np.int16)
    assert (
        abs(diff[..., 0] - diff[..., 1]).max() <= 2
        and abs(diff[..., 1] - diff[..., 2]).max() <= 2
    )


# ── apply_params: sharpness ────────────────────────────────────


def test_apply_sharpness_preserves_solid_gray():
    """A flat-grey image has no edges → unsharp mask returns
    essentially the same image."""
    img = _mid_gray(level=128)
    out = apply_params(img, Params(sharpness=100.0))
    # Allow ±1 for float round-trip rounding.
    assert abs(int(out.mean()) - 128) <= 1


def test_apply_sharpness_brightens_edges():
    """An edge image → sharpening brightens the bright side and
    darkens the dark side of the transition."""
    img = np.zeros((16, 32, 3), dtype=np.uint8)
    img[:, 16:, :] = 200            # right half brighter
    out = apply_params(img, Params(sharpness=100.0))
    # Right side of the edge (column 16) brighter than input;
    # left side (column 15) darker than input.
    assert out[8, 16, 0] >= img[8, 16, 0]
    assert out[8, 15, 0] <= img[8, 15, 0]


# ── apply_params: input validation ─────────────────────────────


def test_apply_rejects_non_uint8():
    img = np.zeros((4, 4, 3), dtype=np.float32)
    with pytest.raises(ValueError, match="uint8"):
        apply_params(img, Params(exposure=0.5))


def test_apply_rejects_non_3_channel():
    img = np.zeros((4, 4, 4), dtype=np.uint8)
    with pytest.raises(ValueError, match="H, W, 3"):
        apply_params(img, Params(exposure=0.5))


# ── compute_default_crop ───────────────────────────────────────


def test_compute_default_crop_original_returns_none():
    """Original ratio = "no crop". Returns None so callers can use the
    same falsy check for both "user picked Original" and "no choice
    yet"."""
    assert compute_default_crop(4000, 3000, get_aspect_ratio("Original")) is None


def test_compute_default_crop_4x3_on_3x2_image_matches():
    """A 3000×2000 image at 4:3 → 2667×2000 centered."""
    rect = compute_default_crop(3000, 2000, get_aspect_ratio("4:3"))
    assert rect is not None
    x, y, w, h = rect
    # Target ratio (4/3) is narrower than source (3/2=1.5) →
    # crop_w = target/src = 4/3 / (3/2) = 8/9 ≈ 0.889; crop_h = 1.
    assert h == pytest.approx(1.0)
    assert w == pytest.approx(8 / 9, abs=1e-6)
    # Centered: x slab on the left = (1 - 8/9) / 2 = 1/18
    assert x == pytest.approx(1 / 18, abs=1e-6)
    assert y == pytest.approx(0.0)


def test_compute_default_crop_16x9_on_4x3_image_pillars_top_bottom():
    """A 4:3 image (e.g. 4000×3000) at 16:9 → wider target than source
    → top/bottom slabs cropped; full width retained."""
    rect = compute_default_crop(4000, 3000, get_aspect_ratio("16:9"))
    assert rect is not None
    x, y, w, h = rect
    assert w == pytest.approx(1.0)
    # crop_h = src/target = (4/3) / (16/9) = 12/16 = 0.75
    assert h == pytest.approx(0.75)
    assert y == pytest.approx(0.125)
    assert x == pytest.approx(0.0)


def test_compute_default_crop_zero_dimensions_returns_none():
    assert compute_default_crop(0, 100, get_aspect_ratio("3:2")) is None
    assert compute_default_crop(100, 0, get_aspect_ratio("3:2")) is None


# ── apply_crop_norm ────────────────────────────────────────────


def test_apply_crop_norm_basic_quarter():
    """Crop the bottom-right quarter of an 8×8 gradient."""
    img = _gradient()[:8, :8]                  # 8×8 RGB
    out = apply_crop_norm(img, (0.5, 0.5, 0.5, 0.5))
    assert out.shape[:2] == (4, 4)


def test_apply_crop_norm_clamps_oob():
    """Out-of-bounds rect is clamped, not raised."""
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    # x=0.9 + w=0.5 → would go past 1.0 → clamped to w=0.1 → 1 px wide.
    out = apply_crop_norm(img, (0.9, 0.0, 0.5, 1.0))
    assert out.shape[1] == 1


def test_apply_crop_norm_degenerate_returns_input():
    """A zero-area rect returns the input unchanged."""
    img = np.zeros((5, 5, 3), dtype=np.uint8)
    out = apply_crop_norm(img, (0.5, 0.5, 0.0, 0.0))
    assert out is img


# ── Vibrance (docs/25 §3) ──────────────────────────────────────


def _saturation(arr: np.ndarray) -> float:
    """Mean HSV-style saturation of an RGB uint8 array, in [0, 1]."""
    f = arr.astype(np.float32) / 255.0
    mx = f.max(axis=2)
    mn = f.min(axis=2)
    return float((( mx - mn) / np.maximum(mx, 1e-6)).mean())


def test_vibrance_in_params_and_identity():
    assert Params().vibrance == 0.0
    assert Params(vibrance=10.0).is_identity is False
    assert Params(vibrance=0.0).is_identity is True


def test_vibrance_scaled():
    p = Params(vibrance=40.0).scaled(0.5)
    assert p.vibrance == 20.0


def test_vibrance_positive_boosts_muted_more_than_vivid():
    # A muted (low-saturation) colour and an already-vivid one.
    muted = np.full((8, 8, 3), 0, dtype=np.uint8)
    muted[..., 0] = 140
    muted[..., 1] = 120
    muted[..., 2] = 110
    vivid = np.full((8, 8, 3), 0, dtype=np.uint8)
    vivid[..., 0] = 230
    vivid[..., 1] = 20
    vivid[..., 2] = 20

    p = Params(vibrance=100.0)
    muted_gain = _saturation(apply_params(muted, p)) - _saturation(muted)
    vivid_gain = _saturation(apply_params(vivid, p)) - _saturation(vivid)

    assert muted_gain > 0.0                 # muted colour gets lifted
    assert muted_gain > vivid_gain          # ... more than the vivid one


def test_vibrance_negative_desaturates():
    img = np.full((8, 8, 3), 0, dtype=np.uint8)
    img[..., 0] = 200
    img[..., 1] = 80
    img[..., 2] = 60
    out = apply_params(img, Params(vibrance=-100.0))
    assert _saturation(out) < _saturation(img)


# ── Rotation 90° (docs/25 §4) ──────────────────────────────────


def test_apply_rotation_zero_is_noop_same_object():
    from core.photo_render import apply_rotation
    img = _mid_gray()
    assert apply_rotation(img, 0) is img


def test_apply_rotation_90_swaps_dimensions():
    from core.photo_render import apply_rotation
    img = np.zeros((4, 6, 3), dtype=np.uint8)   # H=4, W=6
    out = apply_rotation(img, 90)
    assert out.shape[:2] == (6, 4)              # dims swap


def test_apply_rotation_is_clockwise():
    from core.photo_render import apply_rotation
    img = np.zeros((2, 3, 3), dtype=np.uint8)
    img[0, 0] = (255, 0, 0)                     # mark top-left red
    out = apply_rotation(img, 90)               # clockwise
    # Top-left of the original lands at the top-RIGHT after a CW turn.
    assert tuple(out[0, -1]) == (255, 0, 0)


def test_apply_rotation_wraps_and_normalises():
    from core.photo_render import apply_rotation
    img = np.zeros((4, 6, 3), dtype=np.uint8)
    # 450° → 90°; 360° → 0° (no-op, same object).
    assert apply_rotation(img, 450).shape[:2] == (6, 4)
    assert apply_rotation(img, 360) is img


# ── Box Rotation extraction (docs/25 §4) ───────────────────────


def test_extract_rotated_crop_zero_is_plain_crop():
    from core.photo_render import extract_rotated_crop
    img = np.full((100, 100, 3), (10, 20, 30), dtype=np.uint8)
    out = extract_rotated_crop(img, (0.25, 0.25, 0.5, 0.5), 0)
    assert out.shape[:2] == (50, 50)            # box size respected


def test_extract_rotated_crop_preserves_box_size():
    from core.photo_render import extract_rotated_crop
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    out = extract_rotated_crop(img, (0.2, 0.2, 0.6, 0.6), 18)
    assert out.shape[:2] == (60, 60)            # size preserved under rotation


def test_extract_rotated_crop_90_rotates_content():
    from core.photo_render import extract_rotated_crop
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    img[:50] = (255, 0, 0)                      # top half red
    img[50:] = (0, 0, 255)                      # bottom half blue
    out = extract_rotated_crop(img, (0.0, 0.0, 1.0, 1.0), 90)
    assert out.shape[:2] == (100, 100)
    left = out[50, 5]
    right = out[50, 95]
    # A 90° box rotation moves the top/bottom split to a left/right
    # split — the content genuinely rotated (not identity).
    assert left[0] != right[0] or left[2] != right[2]


def test_tone_lut_is_bit_identical_to_per_pixel():
    """apply_params collapses the per-channel tone stages into a 256-LUT for
    speed; that MUST be bit-identical to running the tone math per pixel.
    Pins the optimisation so a future tweak can't silently drift the look."""
    import numpy as np
    from core.photo_render import Params, _tone_curve
    rng = np.random.default_rng(7)
    img = (rng.random((64, 96, 3)) * 255).astype(np.uint8)
    for p in (
        Params(exposure=0.5, contrast=20),
        Params(contrast=-40, whites=15, blacks=10),
        Params(shadows=50, highlights=-30),
        Params(exposure=-1.2, contrast=80, whites=-20),
        Params(blacks=40),
    ):
        lut = _tone_curve(np.arange(256, dtype=np.float32) / 255.0, p)[img]
        per_pixel = _tone_curve(img.astype(np.float32) / 255.0, p)
        assert np.array_equal(lut, per_pixel), f"tone LUT drifted for {p}"
