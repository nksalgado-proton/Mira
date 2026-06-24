"""Creative-filter engine stage (spec/55) — FilterRecipe + apply_filter.

Pins the identity contract, each primitive's direction of effect, and
the dict hydration the generated data module relies on.
"""

from __future__ import annotations

import numpy as np
import pytest

from core.photo_render import FilterRecipe, Params, apply_filter


def _gradient() -> np.ndarray:
    """A (64, 96, 3) horizontal luminance ramp with a color cast so
    mono/tint effects are observable."""
    ramp = np.linspace(20, 235, 96, dtype=np.float32)
    img = np.zeros((64, 96, 3), dtype=np.float32)
    img[..., 0] = ramp * 1.0
    img[..., 1] = ramp * 0.8
    img[..., 2] = ramp * 0.6
    return np.clip(img, 0, 255).astype(np.uint8)


def test_identity_recipe_is_noop():
    img = _gradient()
    out = apply_filter(img, FilterRecipe())
    assert np.array_equal(out, img)
    assert out is not img                      # copy, not the same buffer
    assert FilterRecipe().is_identity


def test_bw_mix_collapses_channels():
    out = apply_filter(_gradient(), FilterRecipe(bw_mix=(0.5, 0.35, 0.15)))
    assert np.array_equal(out[..., 0], out[..., 1])
    assert np.array_equal(out[..., 1], out[..., 2])


def test_bw_mix_red_heavy_darkens_blue_subjects():
    """The dramatic-mono trick: a red-heavy mix renders blue content
    darker than a blue-heavy mix would."""
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    img[..., 2] = 200                          # pure blue patch
    red_heavy = apply_filter(img, FilterRecipe(bw_mix=(0.7, 0.2, 0.1)))
    blue_heavy = apply_filter(img, FilterRecipe(bw_mix=(0.1, 0.2, 0.7)))
    assert red_heavy.mean() < blue_heavy.mean()


def test_tint_warms():
    out = apply_filter(_gradient(), FilterRecipe(tint=(1.1, 1.0, 0.85)))
    src = _gradient()
    assert out[..., 0].mean() > src[..., 0].mean() * 0.99
    assert out[..., 2].mean() < src[..., 2].mean()


def test_split_tone_cool_shadows_warm_highlights():
    src = _gradient()
    out = apply_filter(src, FilterRecipe(
        split_shadows=(0.9, 1.0, 1.15), split_highlights=(1.1, 1.0, 0.9)))
    dark = out[:, :10]                          # ramp start = shadows
    bright = out[:, -10:]
    src_dark = src[:, :10]
    src_bright = src[:, -10:]
    # Shadows pushed blue; highlights pushed warm.
    assert (dark[..., 2].mean() - src_dark[..., 2].mean()) > 0
    assert (bright[..., 0].astype(int) - bright[..., 2].astype(int)).mean() \
        > (src_bright[..., 0].astype(int) - src_bright[..., 2].astype(int)).mean()


def test_fade_lifts_blacks():
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    out = apply_filter(img, FilterRecipe(fade=0.12))
    assert out.min() >= int(0.12 * 255) - 1


def test_clarity_increases_local_contrast():
    src = _gradient()
    out = apply_filter(src, FilterRecipe(clarity=0.8))
    assert out.astype(int).std() > src.astype(int).std()


def test_vignette_darkens_corners_not_centre():
    img = np.full((64, 96, 3), 200, dtype=np.uint8)
    out = apply_filter(img, FilterRecipe(vignette=0.5))
    assert out[32, 48].mean() == pytest.approx(200, abs=1)   # centre intact
    assert out[0, 0].mean() < 200 - 20                       # corner darkened


def test_params_component_rides_existing_engine():
    out = apply_filter(_gradient(), FilterRecipe(
        params=Params(saturation=-100.0)))
    assert int(out[..., 0].astype(int).mean()) \
        == pytest.approx(int(out[..., 2].astype(int).mean()), abs=2)


def test_from_dict_round_trip_and_unknown_keys():
    r = FilterRecipe.from_dict({
        "params": {"contrast": 12.0, "vibrance": 8.0},
        "bw_mix": [0.5, 0.35, 0.15],
        "tint": [1.05, 1.0, 0.9],
        "fade": 0.08,
        "clarity": 0.4,
        "vignette": 0.25,
    })
    assert r.params.contrast == 12.0
    assert r.bw_mix == (0.5, 0.35, 0.15)
    assert not r.is_identity
    with pytest.raises(ValueError, match="unknown FilterRecipe keys"):
        FilterRecipe.from_dict({"sparkle": 1.0})


# ── Resolution layer (photo_auto ⇄ generated data module) ─────────


def test_available_filters_is_the_locked_nine_plus_spec_116():
    """spec/55's nine + spec/116's four + spec/118's de-glare, in
    render order (the new entries sit at the tail). The order matters
    — the Editor's picker reads it; established filters keep their
    existing slots."""
    from core.photo_auto import available_filters
    assert available_filters() == (
        # spec/55 — the original nine.
        "vivid", "bw", "sepia", "faded", "golden", "cinema",
        "bleach", "dramatic", "crisp",
        # spec/116 — the four additions.
        "subject_pop", "dehaze", "dreamy_glow", "film_grain",
        # spec/118 — specular-hotspot tamer.
        "deglare")


def test_resolve_filter_recipe_none_and_unknown():
    from core.photo_auto import resolve_filter_recipe
    assert resolve_filter_recipe(None) is None
    assert resolve_filter_recipe("") is None
    with pytest.raises(ValueError, match="unknown creative filter"):
        resolve_filter_recipe("sparkle")


def test_crisp_style_overrides_differ():
    """spec/55: crisp is specimen-dark on macro, warm-feather on
    wildlife, base elsewhere — the _TUNING_BY_STYLE pattern."""
    from core.photo_auto import resolve_filter_recipe
    base = resolve_filter_recipe("crisp")
    macro = resolve_filter_recipe("crisp", "macro")
    wildlife = resolve_filter_recipe("crisp", "wildlife")
    landscape = resolve_filter_recipe("crisp", "landscape")
    assert macro != base and wildlife != base and wildlife != macro
    assert landscape == base                     # no override → base
    # Every variant hydrates.
    for d in (base, macro, wildlife):
        FilterRecipe.from_dict(d)


def test_every_locked_recipe_is_distinct_and_applies():
    """All nine (+ spec/116's four + spec/118's de-glare) produce a
    non-identity, mutually-renderable result on a real-ish gradient
    with a synthetic glare patch — the engine-level floor under the
    eyeball's 'identity' judgement. The glare patch (a small bright
    + low-saturation disc) gives the de-glare stage something to bite
    on; the gradient surround keeps the other filters' behaviour
    unchanged."""
    from core.photo_auto import available_filters, resolve_filter_recipe
    img = _gradient().copy()
    # Add a synthetic specular hotspot at the centre — high-luminance,
    # low-saturation, the exact pattern de-glare is designed to tame.
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.sqrt((yy - h / 2.0) ** 2 + (xx - w / 2.0) ** 2)
    m = np.clip(1.0 - r / 6.0, 0.0, 1.0)[..., None]
    glare = np.array([245, 247, 246], dtype=np.float32)
    img = np.clip(
        img.astype(np.float32) * (1.0 - m) + glare * m, 0, 255
    ).astype(np.uint8)
    outs = {}
    for key in available_filters():
        recipe = FilterRecipe.from_dict(resolve_filter_recipe(key))
        assert not recipe.is_identity, key
        outs[key] = apply_filter(img, recipe)
        assert not np.array_equal(outs[key], img), key
    # Pairwise distinct on this gradient (cheap sanity, not aesthetics).
    keys = list(outs)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            assert not np.array_equal(outs[a], outs[b]), (a, b)


def test_spec_116_named_filters_hydrate_and_carry_their_strength():
    """spec/116 §3 — Subject Pop / Dehaze / Dreamy Glow / Film Grain
    appear in the registry, hydrate cleanly, and carry a non-zero
    value on the headline component each one is built around."""
    from core.photo_auto import resolve_filter_recipe
    pop = FilterRecipe.from_dict(resolve_filter_recipe("subject_pop"))
    assert pop.spotlight > 0.0
    dh = FilterRecipe.from_dict(resolve_filter_recipe("dehaze"))
    assert dh.dehaze > 0.0
    glow = FilterRecipe.from_dict(resolve_filter_recipe("dreamy_glow"))
    assert glow.glow > 0.0
    grain = FilterRecipe.from_dict(resolve_filter_recipe("film_grain"))
    assert grain.grain > 0.0


def test_spec_116_named_filters_have_display_names():
    """The Editor's filter combo reads :func:`filter_display_name`;
    every new key has a tr-wrapped human label so the picker isn't
    showing internal identifiers."""
    from mira.ui.edited.look_grid import filter_display_name
    for key in ("subject_pop", "dehaze", "dreamy_glow", "film_grain"):
        label = filter_display_name(key)
        assert label != key, key
        assert label.strip() != "", key


# ── Calibration trims (spec/54 §4.1 — the Settings knobs) ─────────


@pytest.fixture
def _trims(monkeypatch):
    """Inject trim values directly into the photo_auto cache (the
    Settings repo is bypassed; invalidation restores reality)."""
    import core.photo_auto as pa

    def set_trims(**values):
        monkeypatch.setattr(pa, "_tone_scaling_cache", dict(values))
    yield set_trims
    pa.invalidate_tone_scaling_cache()


def test_filter_amount_blends_and_extrapolates():
    img = _gradient()
    recipe = FilterRecipe(params=Params(saturation=-100.0))
    full = apply_filter(img, recipe, 1.0)
    off = apply_filter(img, recipe, 0.0)
    half = apply_filter(img, recipe, 0.5)
    assert np.array_equal(off, img)
    # Half sits strictly between input and full (channel spread halves).
    spread = lambda a: float(  # noqa: E731
        np.abs(a[..., 0].astype(int) - a[..., 2].astype(int)).mean())
    assert spread(full) < spread(half) < spread(img)


def test_natural_trim_scales_the_correction(_trims):
    from core.photo_auto import compute_auto_params
    img = _gradient()
    base = compute_auto_params(img, style="macro")
    _trims(look_scale_natural=-100)
    off = compute_auto_params(img, style="macro")
    assert off.is_identity
    _trims(look_scale_natural=100)
    double = compute_auto_params(img, style="macro")
    assert double.exposure == pytest.approx(2 * base.exposure)


def test_look_trim_scales_bias_only(_trims):
    from core.photo_auto import look_params_from_natural
    natural = Params(exposure=0.5, contrast=10.0)
    base = look_params_from_natural(natural, "brighter")
    _trims(look_scale_brighter=-100)
    collapsed = look_params_from_natural(natural, "brighter")
    # Bias gone → identical to Natural; the correction is untouched.
    assert collapsed == natural
    _trims(look_scale_brighter=100)
    doubled = look_params_from_natural(natural, "brighter")
    assert (doubled.exposure - natural.exposure) == pytest.approx(
        2 * (base.exposure - natural.exposure))


def test_creative_filter_amount_reads_trims(_trims):
    from core.photo_auto import creative_filter_amount
    assert creative_filter_amount("vivid") == 1.0
    _trims(filter_scale_vivid=-50)
    assert creative_filter_amount("vivid") == pytest.approx(0.5)
    assert creative_filter_amount("bw") == 1.0
    assert creative_filter_amount(None) == 1.0
