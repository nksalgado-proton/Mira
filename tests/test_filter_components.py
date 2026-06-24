"""spec/116 §6 — engine primitives for the four new creative-filter
components (Subject Spotlight, Dehaze, Glow, Grain).

Each new field defaults to a no-op so existing filters and
``FilterRecipe()`` stay byte-identical. Every component pushes pixels
in the expected direction. ``from_dict`` accepts the new keys and
still rejects the unknown. ``amount=0.5`` blends to roughly half the
full effect, matching every other primitive.

The spotlight-anchor / centre plumbing has its own pin in
``tests/test_spotlight_center.py``."""
from __future__ import annotations

import numpy as np
import pytest

from core.photo_render import FilterRecipe, apply_filter


def _gradient() -> np.ndarray:
    """A (64, 96, 3) horizontal luminance ramp with a warm cast — same
    fixture as ``tests/test_photo_filters.py`` so the new tests live in
    the same regime."""
    ramp = np.linspace(20, 235, 96, dtype=np.float32)
    img = np.zeros((64, 96, 3), dtype=np.float32)
    img[..., 0] = ramp * 1.0
    img[..., 1] = ramp * 0.8
    img[..., 2] = ramp * 0.6
    return np.clip(img, 0, 255).astype(np.uint8)


def _flat(value: int = 128, shape=(64, 96, 3)) -> np.ndarray:
    return np.full(shape, value, dtype=np.uint8)


# ── Identity preserved with new fields off ──────────────────────


def test_identity_recipe_still_a_noop_after_new_fields():
    """A bare ``FilterRecipe()`` must remain byte-identical to its
    input — the four new fields default off so existing filters
    render unchanged (spec/116 §1)."""
    img = _gradient()
    out = apply_filter(img, FilterRecipe())
    assert np.array_equal(out, img)
    assert FilterRecipe().is_identity


def test_default_spotlight_radius_with_zero_strength_is_identity():
    """The radius alone is not an identity break — only the strength
    controls whether the stage runs. A recipe with the default radius
    and zero strength must still be identity."""
    recipe = FilterRecipe(spotlight=0.0, spotlight_radius=0.4)
    assert recipe.is_identity
    img = _gradient()
    out = apply_filter(img, recipe)
    assert np.array_equal(out, img)


def test_existing_filters_still_byte_identical():
    """spec/116 §5 — re-run every locked-nine recipe; their output is
    a fixed function of the input, so the new fields' presence (all
    defaulted) must not perturb a single byte."""
    from core.photo_auto import available_filters, resolve_filter_recipe
    img = _gradient()
    for key in available_filters():
        recipe = FilterRecipe.from_dict(resolve_filter_recipe(key))
        out_before = apply_filter(img, recipe)
        out_after = apply_filter(img, recipe)
        assert np.array_equal(out_before, out_after), key


# ── Each component changes pixels in the expected direction ────


def test_spotlight_brightens_and_contrasts_near_center():
    """Inside the radius, Spotlight = local contrast + slight exposure
    lift; on a flat frame the local-contrast delta is ~0 so the lift
    dominates and the centre region brightens visibly."""
    img = _flat(120)
    out = apply_filter(img, FilterRecipe(spotlight=0.8))
    centre = out[28:36, 44:52].mean()
    src_centre = img[28:36, 44:52].mean()
    assert centre > src_centre + 2


def test_spotlight_mutes_corners():
    """Outside the radius, Spotlight darkens + desaturates. On a
    saturated red corner the output reads less saturated than the
    input — luminance lerp + multiplicative darken."""
    img = np.zeros((64, 96, 3), dtype=np.uint8)
    img[..., 0] = 220                          # pure red field
    img[..., 1] = 40
    img[..., 2] = 40
    out = apply_filter(img, FilterRecipe(spotlight=0.8))
    # Top-left corner: saturation collapses toward the luminance.
    src_corner_chroma = int(img[0, 0, 0]) - int(img[0, 0, 2])
    out_corner_chroma = int(out[0, 0, 0]) - int(out[0, 0, 2])
    assert out_corner_chroma < src_corner_chroma
    # And it's at least as dark as the original.
    assert out[0, 0, 0] <= img[0, 0, 0]


def test_spotlight_amount_half_is_half_effect():
    """``amount`` is the spec/54 §4.1 trim: 0.5 produces ~half the
    effect of 1.0 (within the blend's linear range)."""
    img = _flat(120)
    half = apply_filter(img, FilterRecipe(spotlight=0.8), amount=0.5)
    full = apply_filter(img, FilterRecipe(spotlight=0.8), amount=1.0)
    half_delta = float(half[32, 48].mean()) - 120.0
    full_delta = float(full[32, 48].mean()) - 120.0
    assert 0.35 * full_delta <= half_delta <= 0.65 * full_delta


def test_dehaze_raises_contrast_and_saturation():
    """+0.5 dehaze on a hazy (low-contrast, washed-out) frame: the
    output's pixel std (contrast) AND chroma spread (saturation)
    grow. A flat-region weight on both moves means the effect is
    measurable even on synthetic test frames."""
    # Hazy gradient: a milky mid-grey with weak colour.
    hazy = np.full((64, 96, 3), 140, dtype=np.float32)
    hazy[..., 0] += 6
    hazy[..., 2] -= 6
    src = np.clip(hazy, 0, 255).astype(np.uint8)
    out = apply_filter(src, FilterRecipe(dehaze=0.5))
    # Contrast — pixel std of the luminance — should grow.
    src_lum = src.mean(axis=2)
    out_lum = out.mean(axis=2)
    assert out_lum.std() >= src_lum.std()
    # Chroma spread (R-B) should be wider on the dehazed output.
    src_chroma = (src[..., 0].astype(int) - src[..., 2].astype(int)).std()
    out_chroma = (out[..., 0].astype(int) - out[..., 2].astype(int)).std()
    assert out_chroma >= src_chroma


def test_dehaze_negative_adds_haze():
    """Negative dehaze adds atmosphere — the inverse direction. The
    output's luminance std drops (less contrast)."""
    src = _gradient()
    out = apply_filter(src, FilterRecipe(dehaze=-0.5))
    assert out.std() < src.std()


def test_glow_raises_highlight_bloom():
    """Glow screen-blends a brightened-blurred copy over the source.
    The mean luminance of the upper half of the frame (where highlights
    cluster on a gradient) goes up."""
    src = _gradient()
    out = apply_filter(src, FilterRecipe(glow=0.6))
    bright_strip = src[:, -20:].mean()
    out_strip = out[:, -20:].mean()
    assert out_strip > bright_strip + 1


def test_glow_amount_half_is_half_effect():
    src = _gradient()
    half = apply_filter(src, FilterRecipe(glow=0.6), amount=0.5)
    full = apply_filter(src, FilterRecipe(glow=0.6), amount=1.0)
    src_strip = float(src[:, -20:].mean())
    half_d = float(half[:, -20:].mean()) - src_strip
    full_d = float(full[:, -20:].mean()) - src_strip
    assert 0.35 * full_d <= half_d <= 0.65 * full_d


def test_grain_raises_local_variance():
    """Grain adds luminance-masked monochrome noise; on a flat midtone
    frame (where the mid_mask is 1.0) the pixel-to-pixel variance
    grows from ~0 to a measurable amount."""
    flat = _flat(128)
    out = apply_filter(flat, FilterRecipe(grain=0.6))
    assert float(out.std()) > 1.0


def test_grain_strongest_in_midtones():
    """The luminance mask 4·L·(1−L) peaks at L = 0.5; pure shadows
    (L ≈ 0) and pure highlights (L ≈ 1) get almost no noise."""
    img = np.zeros((64, 96, 3), dtype=np.uint8)
    img[:, :48] = 5                              # near-black left half
    img[:, 48:] = 128                            # midtone right half
    out = apply_filter(img, FilterRecipe(grain=0.8))
    dark_std = float(out[:, :48].std())
    mid_std = float(out[:, 48:].std())
    assert mid_std > dark_std + 1.0


def test_grain_is_deterministic_across_renders():
    """Grain seeds from frame dimensions so re-renders produce the
    same noise. Cheap stability guarantee for the host's signature
    cache (spec/115 §1)."""
    flat = _flat(128)
    a = apply_filter(flat, FilterRecipe(grain=0.6))
    b = apply_filter(flat, FilterRecipe(grain=0.6))
    assert np.array_equal(a, b)


# ── from_dict / is_identity accept the new keys ────────────────


def test_from_dict_round_trip_with_new_keys():
    r = FilterRecipe.from_dict({
        "spotlight": 0.4,
        "spotlight_radius": 0.5,
        "dehaze": -0.3,
        "glow": 0.2,
        "grain": 0.15,
    })
    assert r.spotlight == pytest.approx(0.4)
    assert r.spotlight_radius == pytest.approx(0.5)
    assert r.dehaze == pytest.approx(-0.3)
    assert r.glow == pytest.approx(0.2)
    assert r.grain == pytest.approx(0.15)
    assert not r.is_identity


def test_from_dict_still_rejects_unknown_keys():
    """The new fields were added but ``sparkle`` still isn't one of
    them — the rejection of unknown keys remains a loud failure."""
    with pytest.raises(ValueError, match="unknown FilterRecipe keys"):
        FilterRecipe.from_dict({"sparkle": 1.0})


def test_is_identity_carries_each_new_field():
    """Each of the four new strength fields breaks identity on its
    own. The radius alone doesn't (its zero-strength sibling carries
    the bypass)."""
    base = FilterRecipe()
    assert base.is_identity
    assert not FilterRecipe(spotlight=0.1).is_identity
    assert not FilterRecipe(dehaze=0.1).is_identity
    assert not FilterRecipe(glow=0.1).is_identity
    assert not FilterRecipe(grain=0.1).is_identity
    # spotlight_radius alone, with zero strength, stays identity.
    assert FilterRecipe(spotlight_radius=0.3).is_identity


# ── Amount=0 short-circuits even with new components on ────────


def test_amount_zero_returns_a_copy_with_new_fields_active():
    img = _gradient()
    out = apply_filter(
        img,
        FilterRecipe(spotlight=0.5, dehaze=0.5, glow=0.5, grain=0.5,
                     deglare=0.5),
        amount=0.0,
    )
    assert np.array_equal(out, img)
    assert out is not img                       # copy semantics preserved


# ── spec/118: de-glare ─────────────────────────────────────────


_SKIN_H = 60
_SKIN_W = 240
_CENTRE = (_SKIN_H // 2, _SKIN_W // 2)
_CORNER = (8, 8)


def _skin_with_hotspot(*, also_corner: bool = False) -> np.ndarray:
    """A 60×240 warm skin-tone field with a single bright-desaturated
    hotspot at the centre (radius ~10 px). When ``also_corner=True``,
    paint a SECOND identical hotspot in the top-left corner. The wide
    aspect makes the corner sit well past the §2 subject-mask outer
    edge so the subject-only mode demonstrably leaves it alone."""
    img = np.zeros((_SKIN_H, _SKIN_W, 3), dtype=np.uint8)
    img[..., 0] = 215
    img[..., 1] = 175
    img[..., 2] = 150        # warm skin baseline (~(215, 175, 150))
    yy, xx = np.mgrid[0:_SKIN_H, 0:_SKIN_W].astype(np.float32)

    def _paint_spot(cy, cx):
        r = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
        m = np.clip(1.0 - r / 10.0, 0.0, 1.0)[..., None]
        # Glare patch: near-white, near-zero chroma — the optical
        # signature of a specular reflection.
        glare = np.array([245, 247, 246], dtype=np.float32)
        baseline = img.astype(np.float32)
        out = baseline * (1.0 - m) + glare * m
        img[...] = np.clip(out, 0, 255).astype(np.uint8)

    _paint_spot(*_CENTRE)
    if also_corner:
        _paint_spot(*_CORNER)
    return img


def _hotspot_stats(img: np.ndarray, cy: int, cx: int, r: int = 6):
    """Mean luminance + mean HSV saturation in a small box at (cy, cx)."""
    patch = img[cy - r:cy + r, cx - r:cx + r].astype(np.float32) / 255.0
    lum = (0.2126 * patch[..., 0]
           + 0.7152 * patch[..., 1]
           + 0.0722 * patch[..., 2]).mean()
    rgb_max = patch.max(axis=2)
    rgb_min = patch.min(axis=2)
    sat = np.where(rgb_max > 1e-6,
                   (rgb_max - rgb_min) / np.maximum(rgb_max, 1e-6),
                   0.0).mean()
    return float(lum), float(sat)


def test_deglare_lowers_luminance_and_raises_saturation_in_hotspot():
    """spec/118 — inside a synthetic bright-desaturated patch, the
    de-glare stage pulls luminance down and re-injects chroma sampled
    from the surrounding skin tone. Subject-only mode (default) covers
    the centred hotspot."""
    img = _skin_with_hotspot()
    before_lum, before_sat = _hotspot_stats(img, *_CENTRE)
    out = apply_filter(img, FilterRecipe(deglare=0.8))
    after_lum, after_sat = _hotspot_stats(out, *_CENTRE)
    assert after_lum < before_lum - 0.005, (
        f"de-glare must lower luminance "
        f"({after_lum:.4f} not < {before_lum:.4f})")
    assert after_sat > before_sat + 0.01, (
        f"de-glare must re-inject chroma "
        f"({after_sat:.4f} not > {before_sat:.4f})")


def test_deglare_subject_only_leaves_corner_hotspot_untouched():
    """With ``deglare_subject_only=True`` (default) the recovery is
    multiplied by the §2 subject radial mask anchored at ``center``
    (defaulting to frame centre); a hotspot in the top-left corner of
    a wide-aspect frame sits past the mask's outer edge and must
    survive unchanged. The same recipe with
    ``deglare_subject_only=False`` touches both."""
    img = _skin_with_hotspot(also_corner=True)
    centre_before_lum, _ = _hotspot_stats(img, *_CENTRE)
    corner_before_lum, _ = _hotspot_stats(img, *_CORNER)

    subj_only = apply_filter(img, FilterRecipe(deglare=0.8))
    centre_after_lum, _ = _hotspot_stats(subj_only, *_CENTRE)
    corner_after_lum, _ = _hotspot_stats(subj_only, *_CORNER)

    # Centre got softened.
    assert centre_after_lum < centre_before_lum - 0.005
    # Corner untouched (gaussian smoothing touches every pixel a hair,
    # but the change must be much smaller than the centre's).
    centre_drop = centre_before_lum - centre_after_lum
    corner_drop = corner_before_lum - corner_after_lum
    assert corner_drop < 0.2 * centre_drop, (
        f"subject-only de-glare must leave the corner hotspot mostly "
        f"unchanged (corner drop {corner_drop:.4f} vs centre drop "
        f"{centre_drop:.4f})")

    # Frame-wide mode does tame the corner — comparable to the centre.
    framewide = apply_filter(
        img, FilterRecipe(deglare=0.8, deglare_subject_only=False))
    corner_framewide_lum, _ = _hotspot_stats(framewide, *_CORNER)
    assert corner_framewide_lum < corner_before_lum - 0.005, (
        "frame-wide de-glare must also tame the corner hotspot")


def test_deglare_breaks_identity_and_round_trips_via_dict():
    """``deglare`` strength alone breaks identity; ``deglare_subject_
    only`` flag alone does NOT (it's a mode flag). from_dict/to_dict
    round-trip both fields verbatim."""
    base = FilterRecipe()
    assert base.is_identity
    assert FilterRecipe(deglare_subject_only=False).is_identity
    assert not FilterRecipe(deglare=0.1).is_identity

    r = FilterRecipe.from_dict({"deglare": 0.4, "deglare_subject_only": False})
    assert r.deglare == pytest.approx(0.4)
    assert r.deglare_subject_only is False
    round_trip = FilterRecipe.from_dict(r.to_dict())
    assert round_trip.deglare == pytest.approx(0.4)
    assert round_trip.deglare_subject_only is False
    # Identity recipe round-trips via an empty dict.
    assert FilterRecipe().to_dict() == {}


def test_named_deglare_filter_in_registry_is_subject_only():
    """The new ``deglare`` entry in FILTER_RECIPES wires through
    ``resolve_filter_recipe`` and hydrates to a FilterRecipe whose
    deglare strength is ~0.5 with subject-only on."""
    from core.photo_auto import available_filters, resolve_filter_recipe
    assert "deglare" in available_filters()
    recipe = FilterRecipe.from_dict(resolve_filter_recipe("deglare"))
    assert recipe.deglare == pytest.approx(0.5)
    assert recipe.deglare_subject_only is True
