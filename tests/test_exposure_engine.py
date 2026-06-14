"""Unit tests for core.exposure_engine.

Tests use synthetic 64×64 PIL images so they run in milliseconds and
don't depend on real photos. Each test pins one property of the
algorithm — we don't assert exact pixel values, only the invariants
that make auto-exposure useful (stretches dark images, idempotent on
already-stretched ones, preserves alpha, etc.).
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from core.exposure_engine import auto_exposure, _build_lut


def _make_gradient(low: int, high: int, size: int = 64) -> Image.Image:
    """A horizontal gradient from ``low`` to ``high`` — simple stand-in
    for a real photo with known luminance bounds."""
    row = np.linspace(low, high, size, dtype=np.uint8)
    arr = np.tile(row, (size, 1))
    rgb = np.stack([arr, arr, arr], axis=-1)
    return Image.fromarray(rgb, mode="RGB")


def _luma_range(img: Image.Image) -> tuple[int, int]:
    arr = np.asarray(img, dtype=np.uint8)
    luma = (0.2126 * arr[..., 0] + 0.7152 * arr[..., 1]
            + 0.0722 * arr[..., 2]).astype(np.uint8)
    return int(luma.min()), int(luma.max())


def test_dark_image_gets_stretched():
    """A dim gradient (60–120) should expand toward 0..255."""
    img = _make_gradient(60, 120)
    out = auto_exposure(img, strength=1.0)
    lo, hi = _luma_range(out)
    assert lo < 30, f"expected darks pulled near 0, got {lo}"
    assert hi > 200, f"expected highlights pushed up, got {hi}"


def test_strength_zero_is_passthrough():
    img = _make_gradient(50, 200)
    out = auto_exposure(img, strength=0.0)
    assert np.array_equal(np.asarray(img), np.asarray(out))


def test_strength_clamped_to_one():
    img = _make_gradient(60, 120)
    full = auto_exposure(img, strength=1.0)
    over = auto_exposure(img, strength=5.0)
    assert np.array_equal(np.asarray(full), np.asarray(over)), (
        "strength > 1.0 should be clamped, not amplified"
    )


def test_flat_image_is_unchanged():
    """An image with no luminance variation has nothing to stretch.
    Algorithm should bail rather than produce divide-by-zero output."""
    arr = np.full((32, 32, 3), 128, dtype=np.uint8)
    img = Image.fromarray(arr, mode="RGB")
    out = auto_exposure(img, strength=1.0)
    assert np.array_equal(np.asarray(img), np.asarray(out))


def test_neutral_adjustments_are_passthrough():
    """Strength=0 with all four region/chroma sliders at 0 must
    return the source unchanged — confirms the new params don't
    leak any effect at their neutral default."""
    img = _make_gradient(50, 200)
    out = auto_exposure(
        img, strength=0.0,
        shadows=0.0, highlights=0.0, saturation=0.0, vibrance=0.0,
    )
    assert np.array_equal(np.asarray(img), np.asarray(out))


def test_shadows_lift_dark_pixels():
    """Positive shadows should pull dark pixels up significantly
    more than bright pixels — the response curve peaks at 0 and is
    zero past 128."""
    img = _make_gradient(0, 255)
    base = auto_exposure(
        img, strength=0.0, highlight_recovery=False,
    )
    lifted = auto_exposure(
        img, strength=0.0, shadows=1.0, highlight_recovery=False,
    )
    base_arr = np.asarray(base)
    lift_arr = np.asarray(lifted)
    dark_delta = int(lift_arr[0, 0, 0]) - int(base_arr[0, 0, 0])
    bright_delta = int(lift_arr[0, -1, 0]) - int(base_arr[0, -1, 0])
    assert dark_delta > 20, f"shadows should lift darks, got {dark_delta}"
    assert bright_delta < 5, (
        f"shadows should not move brights, got {bright_delta}"
    )


def test_highlights_boost_bright_pixels():
    img = _make_gradient(0, 255)
    base = auto_exposure(
        img, strength=0.0, highlight_recovery=False,
    )
    boosted = auto_exposure(
        img, strength=0.0, highlights=1.0, highlight_recovery=False,
    )
    base_arr = np.asarray(base)
    boost_arr = np.asarray(boosted)
    # Pick a bright-but-not-clipped column (mid-gradient at 80%).
    col = int(boost_arr.shape[1] * 0.8)
    bright_delta = int(boost_arr[0, col, 0]) - int(base_arr[0, col, 0])
    dark_delta = int(boost_arr[0, 0, 0]) - int(base_arr[0, 0, 0])
    assert bright_delta > 5, (
        f"highlights should push brights up, got {bright_delta}"
    )
    assert abs(dark_delta) < 5, (
        f"highlights should not move darks, got {dark_delta}"
    )


def test_saturation_minus_one_yields_grayscale():
    """saturation=-1 should collapse the image to gray — R == G == B."""
    arr = np.zeros((4, 4, 3), dtype=np.uint8)
    arr[..., 0] = 200  # pure red
    arr[..., 1] = 60
    arr[..., 2] = 60
    img = Image.fromarray(arr, mode="RGB")
    out = np.asarray(
        auto_exposure(img, strength=0.0, saturation=-1.0),
    )
    assert np.allclose(out[..., 0], out[..., 1], atol=2)
    assert np.allclose(out[..., 1], out[..., 2], atol=2)


def test_vibrance_boosts_low_sat_more_than_high():
    """A low-sat patch should gain more saturation from vibrance
    than a high-sat patch under the same slider value."""
    arr = np.zeros((4, 8, 3), dtype=np.uint8)
    # Left half: low sat (almost gray)
    arr[:, :4, 0] = 130
    arr[:, :4, 1] = 120
    arr[:, :4, 2] = 120
    # Right half: high sat (vivid red)
    arr[:, 4:, 0] = 230
    arr[:, 4:, 1] = 30
    arr[:, 4:, 2] = 30
    img = Image.fromarray(arr, mode="RGB")
    out = np.asarray(
        auto_exposure(img, strength=0.0, vibrance=1.0),
    )

    def _sat(pixel: np.ndarray) -> int:
        return int(pixel.max()) - int(pixel.min())

    low_gain = _sat(out[0, 0]) - _sat(arr[0, 0])
    high_gain = _sat(out[0, -1]) - _sat(arr[0, -1])
    assert low_gain > high_gain, (
        f"vibrance should boost low-sat more, got low={low_gain} high={high_gain}"
    )


def test_alpha_preserved():
    """RGBA input should keep its alpha channel untouched."""
    rgb = _make_gradient(60, 120)
    rgba = rgb.convert("RGBA")
    # Stamp a non-trivial alpha so we can verify it survived.
    alpha = np.linspace(64, 255, 64, dtype=np.uint8)
    alpha_arr = np.tile(alpha, (64, 1))
    rgba.putalpha(Image.fromarray(alpha_arr, mode="L"))

    out = auto_exposure(rgba, strength=1.0)
    assert out.mode == "RGBA"
    assert np.array_equal(
        np.asarray(out.getchannel("A")),
        np.asarray(rgba.getchannel("A")),
    )


def test_output_size_matches_input():
    img = _make_gradient(40, 200, size=128)
    out = auto_exposure(img, strength=0.85)
    assert out.size == img.size


def test_lut_anchors_dark_to_zero_and_light_to_255():
    """The LUT should map the dark percentile near 0 and the light
    percentile near (or just below) 255 at full strength."""
    lut = _build_lut(p_dark=60.0, p_light=200.0, strength=1.0,
                     highlight_recovery=False)
    assert lut[60] <= 5, f"dark anchor should be ~0, got {lut[60]}"
    assert lut[200] >= 250, f"light anchor should be ~255, got {lut[200]}"


def test_lut_is_monotonic():
    """Tone curve must never go backwards — that would invert local
    contrast and make the photo look broken."""
    lut = _build_lut(p_dark=40.0, p_light=210.0, strength=1.0,
                     highlight_recovery=True)
    diffs = np.diff(lut.astype(np.int16))
    assert np.all(diffs >= 0), (
        f"LUT not monotonic: {np.where(diffs < 0)[0][:5]} have negative diffs"
    )


def test_highlight_recovery_softens_top():
    """With recovery on, peak output should sit below 255 to leave
    headroom for specular highlights."""
    lut_with = _build_lut(p_dark=60.0, p_light=200.0, strength=1.0,
                          highlight_recovery=True)
    lut_without = _build_lut(p_dark=60.0, p_light=200.0, strength=1.0,
                             highlight_recovery=False)
    assert lut_with.max() < lut_without.max() or lut_with.max() <= 251


def test_partial_strength_blends():
    """Strength=0.5 should produce output between identity and full."""
    img = _make_gradient(50, 180)
    full = auto_exposure(img, strength=1.0)
    half = auto_exposure(img, strength=0.5)
    full_lo, full_hi = _luma_range(full)
    half_lo, half_hi = _luma_range(half)
    # At full strength darks are darker and highlights brighter than at
    # half — partial blends sit between original and full.
    assert full_lo <= half_lo
    assert full_hi >= half_hi


def test_contrast_s_curve_endpoints_unchanged():
    """The S-curve must keep 0 → 0 and 255 → 255 — only midtones bend.
    Otherwise we'd shift the white point and crush the black point."""
    lut_no_curve = _build_lut(0, 255, strength=1.0, highlight_recovery=False)
    lut_curve = _build_lut(
        0, 255, strength=1.0, highlight_recovery=False,
        contrast_strength=0.5,
    )
    # Both curves pass through 0 and 255 unchanged
    assert lut_no_curve[0] == lut_curve[0] == 0
    assert lut_no_curve[255] == lut_curve[255] == 255
    # Pivot at 128 should also be near-identity (S-curves are
    # symmetric around the midpoint).
    assert abs(int(lut_curve[128]) - 128) <= 1


def test_contrast_s_curve_darkens_below_pivot_and_lifts_above():
    """The whole point of the S-curve: enhance contrast by darkening
    midtones below 128 and lifting midtones above 128."""
    lut_no_curve = _build_lut(0, 255, strength=1.0, highlight_recovery=False)
    lut_curve = _build_lut(
        0, 255, strength=1.0, highlight_recovery=False,
        contrast_strength=0.5,
    )
    # Below pivot: curve output < linear output (darker)
    assert int(lut_curve[64]) < int(lut_no_curve[64])
    # Above pivot: curve output > linear output (lighter)
    assert int(lut_curve[192]) > int(lut_no_curve[192])


def test_contrast_zero_disables_s_curve():
    """contrast_strength=0 must yield exactly the legacy LUT — no
    behavior change for old journals that don't carry the field."""
    lut_legacy = _build_lut(10, 240, strength=0.85, highlight_recovery=True)
    lut_noop = _build_lut(
        10, 240, strength=0.85, highlight_recovery=True,
        contrast_strength=0.0,
    )
    assert np.array_equal(lut_legacy, lut_noop)
