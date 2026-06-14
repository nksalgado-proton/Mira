"""Tests for core.photo_auto — baseline AUTO algorithm
(Nelson 2026-05-21 Phase 3a)."""

from __future__ import annotations

import numpy as np
import pytest

from core.photo_auto import (
    compute_auto_params,
    _compute_blacks,
    _compute_contrast,
    _compute_exposure,
    _compute_highlights,
    _compute_shadows,
    _compute_whites,
)
from core.photo_render import Params


# ── Per-slider helper tests (each is a pure function) ────────


def test_exposure_pushes_up_dark_image():
    """Dark median → positive EV shift."""
    from core.photo_auto import _TUNING
    # A median well below the target (whatever target the current
    # tuning uses) must produce a positive lift.
    ev = _compute_exposure(median=_TUNING.exposure_target * 0.4)
    assert ev > 0.0


def test_exposure_pushes_down_bright_image():
    from core.photo_auto import _TUNING
    ev = _compute_exposure(median=min(0.99, _TUNING.exposure_target * 2.0))
    assert ev < 0.0


def test_exposure_at_target_is_zero():
    """A median exactly at the configured target produces a 0 EV
    shift — survives ``_TUNING.exposure_target`` tuning without a
    test rewrite."""
    from core.photo_auto import _TUNING
    ev = _compute_exposure(median=_TUNING.exposure_target)
    assert ev == 0.0


def test_exposure_cap_at_extreme_dark():
    """A near-black median doesn't return an unbounded EV."""
    from core.photo_auto import _TUNING
    ev = _compute_exposure(median=0.01)
    assert -_TUNING.exposure_max_ev <= ev <= _TUNING.exposure_max_ev


def test_whites_brightens_when_p99_dim():
    """A scene whose 99th percentile is at 0.7 (no real highlights)
    gets a positive whites push."""
    w = _compute_whites(p99=0.7)
    assert w > 0.0


def test_whites_zero_when_p99_already_at_target():
    assert _compute_whites(p99=0.96) == 0.0


def test_blacks_negative_when_shadows_too_bright():
    """LRC AUTO direction: +blacks brightens shadows, -blacks
    deepens them. When p1 is above target (shadows too bright),
    we want NEGATIVE blacks to push them down."""
    b = _compute_blacks(p1=0.20)
    assert b < 0.0


def test_blacks_zero_when_already_clipped():
    assert _compute_blacks(p1=0.02) == 0.0


def test_shadows_positive_when_lower_quartile_dark():
    s = _compute_shadows(p25=0.15)
    assert s > 0.0


def test_shadows_zero_when_lower_quartile_above_threshold():
    assert _compute_shadows(p25=0.30) == 0.0


def test_shadows_capped():
    """A pitch-dark p25 should still cap the slider at the max."""
    from core.photo_auto import _TUNING
    s = _compute_shadows(p25=0.0)
    assert s <= _TUNING.shadows_max


def test_highlights_negative_when_upper_quartile_bright():
    """Highlights pull is NEGATIVE in LRC direction (pull DOWN)."""
    h = _compute_highlights(p75=0.85)
    assert h < 0.0


def test_highlights_zero_when_upper_quartile_below_threshold():
    assert _compute_highlights(p75=0.70) == 0.0


def test_contrast_boost_when_histogram_compressed():
    """p5-p95 spread of 0.4 (compressed) → positive contrast."""
    c = _compute_contrast(p5=0.30, p95=0.70)
    assert c > 0.0


def test_contrast_zero_when_spread_already_full():
    c = _compute_contrast(p5=0.05, p95=0.95)
    assert c == 0.0


# ── compute_auto_params (full pipeline) ────────────────────────


def test_auto_params_at_target_returns_near_identity():
    """A flat image at the configured exposure target produces ~0
    exposure shift. Pinned on the LEGACY path (an uncalibrated
    style) — calibrated styles route to fitted per-cluster targets
    (spec/54 §3.1) so the legacy ``_TUNING.exposure_target`` is no
    longer their fixed point."""
    from core.photo_auto import _TUNING
    # Build an image whose mean luminance exactly hits the target.
    level = int(round(_TUNING.exposure_target * 255))
    img = np.full((16, 16, 3), level, dtype=np.uint8)
    p = compute_auto_params(img, style="sports")
    assert abs(p.exposure) < 0.01


def test_auto_params_on_dark_image_lifts_exposure():
    """A dark image (median 30/255 ≈ 0.12) gets positive EV."""
    img = np.full((16, 16, 3), 30, dtype=np.uint8)
    p = compute_auto_params(img)
    assert p.exposure > 0.0


def test_auto_params_on_bright_image_pulls_exposure():
    img = np.full((16, 16, 3), 220, dtype=np.uint8)
    p = compute_auto_params(img)
    assert p.exposure < 0.0


def test_auto_params_leaves_sharpness_and_saturation_untouched():
    """LRC AUTO never touches sharpness or saturation. Neither do we."""
    img = np.full((16, 16, 3), 128, dtype=np.uint8)
    p = compute_auto_params(img)
    assert p.sharpness == 0.0
    assert p.saturation == 0.0


def test_auto_params_input_validation():
    """Non-uint8 or non-3-channel images raise."""
    with pytest.raises(ValueError):
        compute_auto_params(np.zeros((4, 4, 3), dtype=np.float32))
    with pytest.raises(ValueError):
        compute_auto_params(np.zeros((4, 4), dtype=np.uint8))


# ── Integration: AUTO → apply_params lifts a dark image ────────


def test_auto_followed_by_apply_brightens_dark_image():
    """End-to-end smoke: a dark image, run AUTO, apply, mean
    brightness goes up."""
    from core.photo_render import apply_params

    img = np.full((32, 32, 3), 40, dtype=np.uint8)
    params = compute_auto_params(img)
    out = apply_params(img, params)
    assert out.mean() > img.mean()


# ── Per-style tuning overrides (Nelson 2026-05-21 Phase 3c) ───


def test_macro_caps_exposure_more_conservatively_than_default():
    """Macro AUTO must not pump exposure to illuminate the dark
    background (Nelson's empirical observation: flash-lit macro
    subject + dark backdrop is the composition; LRC's exposure
    lift on those is what he rejects). Verify on a dark image
    that the macro style returns a much smaller exposure shift
    than the default."""
    img = np.full((32, 32, 3), 30, dtype=np.uint8)  # dark
    default_params = compute_auto_params(img)
    macro_params = compute_auto_params(img, style="macro")
    assert 0 < macro_params.exposure < default_params.exposure


def test_macro_caps_shadows_lift_more_conservatively():
    """Same logic for the shadows slider — macro's intentional
    dark backdrop should not be lifted to milky-grey."""
    img = np.full((32, 32, 3), 30, dtype=np.uint8)
    default_params = compute_auto_params(img)
    macro_params = compute_auto_params(img, style="macro")
    assert 0 <= macro_params.shadows < default_params.shadows


def test_portrait_caps_exposure_more_conservatively_than_default():
    """Portrait is similar to macro for exposure — over-pumping
    the face washes out skin tone. Cap is gentler than macro's
    but still tighter than default."""
    img = np.full((32, 32, 3), 50, dtype=np.uint8)
    default_params = compute_auto_params(img)
    portrait_params = compute_auto_params(img, style="portrait")
    assert portrait_params.exposure <= default_params.exposure


def test_wildlife_caps_shadow_lift_and_exposure():
    """Wildlife is now RESTRAINED, not boosted (Nelson 2026-05-28, LRC
    -pair run): a dark subject against a bright sky was over-brightened
    (washed skies). The wildlife shadows cap (30) is below the default
    (35) and exposure is capped at 0.6 EV. So on a histogram that wants
    a big shadow lift, wildlife lifts NO MORE than the default."""
    # A dark-quarter histogram → shadows wants to fire hard.
    img = np.zeros((32, 32, 3), dtype=np.uint8)
    img[:, :8] = 20            # dark band; rest stays 0
    default_params = compute_auto_params(img)
    wildlife_params = compute_auto_params(img, style="wildlife")
    assert wildlife_params.shadows <= default_params.shadows


def test_unknown_style_falls_back_to_legacy_default():
    """A scenario string with no fitted router entry takes the
    LEGACY constants path (spec/54 §3.1 fallback): every such style
    behaves identically, and none of them crash."""
    img = np.full((32, 32, 3), 40, dtype=np.uint8)
    a = compute_auto_params(img, style="not_a_real_style")
    b = compute_auto_params(img, style="sports")
    assert a == b


def test_style_none_is_general():
    """``style=None`` rides the fitted *general* router entry — the
    calibration sweep's convention (spec/54). Before the 2026-06-10
    fit both meant the legacy ``_TUNING``; now both mean the routed
    general fit."""
    img = np.full((32, 32, 3), 40, dtype=np.uint8)
    assert compute_auto_params(img) \
        == compute_auto_params(img, style="general")
