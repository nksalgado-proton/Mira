"""AUTO algorithm — infer LRC-style adjustment Params from an image.

Looks at the image's luminance histogram (percentile statistics +
clipping) and returns a :class:`Params` set that, when applied via
:func:`core.photo_render.apply_params`, lifts the photo toward the
calibration target: Nelson's LRC-pair ground truth (LRC AUTO as
corrected by his eye).

**Calibration landed 2026-06-10** (spec/54, the Looks redesign).
The single per-style constant set proved systematically wrong for
the brighter sub-populations of every style (it over-darkened toward
its median target while the ground truth mostly leaves bright photos
alone). It is replaced by the **A-router**: per style, 2-3 cluster
constant sets fitted from the 499-pair set at ``D:\\Photos\\Compare
LRC Auto correction`` via ``tools/calibrate_looks.py``; each photo
routes to its cluster by nearest centroid in z-scored feature space
(:data:`core.photo_looks_data.ROUTER`, generated — do not edit).
Styles without fitted data fall back to the legacy ``_TUNING`` /
``_TUNING_BY_STYLE`` constants below.

On top of the routed correction sit the **Looks** (spec/54 §3.2):
:func:`compute_look_params` = routed Natural + a designed mood bias
(:data:`core.photo_looks_data.LOOK_BIASES`) scaled by Intensity.

Six AUTO-affected sliders (matching what LRC AUTO actually touches):
``exposure``, ``contrast``, ``highlights``, ``shadows``, ``whites``,
``blacks``. ``sharpness`` and ``saturation`` are user-only. The Look
biases MAY set ``vibrance`` (mood character, spec/54 §3.4).

Pure-Python; no Qt; safe to call off the GUI thread.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final, Optional

import numpy as np

from core.photo_looks_data import (
    FEATURE_KEYS,
    FILTER_RECIPES,
    LOOK_BIASES,
    ROUTER,
)
from core.photo_render import Params

log = logging.getLogger(__name__)


# ── Tuning constants (the only knobs the calibration loop tunes) ─


@dataclass(frozen=True)
class _TuningConstants:
    """All the magic numbers in one place. Adjust → re-run the
    LRC-pair harness → eyeball the contact sheet → re-adjust. Nothing
    else in this module is meant to be touched during calibration."""

    # Exposure — target the luminance median at this value.
    # Tuning history:
    #   v0 (initial): 0.50 → ran the LRC-pair harness and saw a
    #   consistent +0.22 mean-luminance overshoot across all 7
    #   style folders. LRC's targets aren't centered on midgray
    #   — its AUTO leaves more of the photo's tonal character
    #   intact. Pulled the target down to 0.40 (closer to LRC's
    #   apparent preference; Selfies which already matched stay
    #   matched because the EV cap kicks in for them).
    exposure_target: float = 0.40
    # Cap exposure shift in EV stops (avoid extreme moves on the
    # very-dark / very-bright outliers — those need a different
    # algorithm, not a bigger gain). v0=1.5 → v1=1.0 to cut the
    # remaining systematic lift on dark RAWs.
    exposure_max_ev: float = 1.0
    # Highlight-protected exposure (Nelson 2026-05-28, LRC-pair run
    # ``2026-05-28_185021``): the median-targeting lift washed bright
    # skies sitting behind dark subjects (Wildlife-Action dp99 +0.21;
    # a systematic dp99 ≈ +0.09 across styles). Never lift the exposure
    # so far that the 99th-percentile luminance blows past this ceiling
    # — LRC keeps this much highlight headroom. Only clamps a POSITIVE
    # (brightening) lift; never forces darkening (the Highlights slider
    # handles already-bright scenes).
    exposure_highlight_ceiling: float = 0.94

    # Whites — target the 99th-percentile luminance at this value.
    # v0=0.95 → v1=0.90 (Nelson 2026-05-28, LRC-pair run): a systematic
    # dp99 ≈ +0.09 overshoot showed our whites ran hotter than LRC,
    # which leaves more highlight headroom (its JPEG p99 ≈ 0.86). 0.90
    # halves the gap without flattening the image into dullness; the
    # contact sheets are the final judge.
    whites_p99_target: float = 0.90
    # Scale: how aggressively to slide the whites slider for a given
    # gap. v0=200 → v1=140 (the post-exposure histogram already lifts
    # whites; this slider shouldn't double-count).
    whites_gain: float = 140.0

    # Blacks — target the 1st-percentile luminance at this value.
    blacks_p1_target: float = 0.04
    blacks_gain: float = 200.0

    # Shadows — lift if 25th percentile is below this threshold.
    shadows_p25_threshold: float = 0.25
    # Lift magnitude per unit gap. 400 means a 0.0625 gap → +25.
    shadows_gain: float = 400.0
    # Cap (avoid over-cooked shadows on already-flat-dark photos).
    # v0=60 → v1=35 — the +60 was visibly milky on the contact
    # sheets, LRC stays closer to +25..+35.
    shadows_max: float = 35.0

    # Highlights — pull if 75th percentile is above this threshold.
    highlights_p75_threshold: float = 0.75
    highlights_gain: float = 400.0
    highlights_max: float = 60.0

    # Contrast — boost if the histogram is compressed.
    # ``compression`` = (p95 - p5); below the threshold means flat.
    contrast_compression_threshold: float = 0.65
    contrast_gain: float = 120.0
    contrast_max: float = 50.0


# Default tuning — applied when no style is provided to
# :func:`compute_auto_params`, or when the style has no override.
_TUNING: Final[_TuningConstants] = _TuningConstants()


# ── Per-style tuning overrides ─────────────────────────────────

# Keyed by scenario string (matches the canonical ``Scenario`` enum
# values in ``core.vocabulary``: "macro", "portrait", "wildlife",
# "landscape", "selfie", "general", "night_long_exposure"). Sparse
# — missing styles fall back to :data:`_TUNING`. Use ``dataclasses
# .replace`` to override only the fields that need to differ;
# everything unspecified inherits the default. The genre helpers
# below pick the right tuning per call.
#
# Style-specific rationale (Nelson 2026-05-21 design conversation):
#
# * **Macro** — flash-lit subject against dark background is the
#   composition; a global-histogram AUTO sees the dark backdrop as
#   "underexposed" and lifts. The *subject* is already correctly
#   exposed by flash. Cap exposure hard so we don't pump the
#   background up. Also tighten shadows_max to preserve the
#   intentional drop-off. (A future Phase 3d will add subject-aware
#   AUTO using the AF point — this is the simpler interim guard.)
#
# * **Portrait** — face is the subject; over-lifted shadows turn
#   skin waxy. Modest exposure cap + reduced shadow lift.
#
# * **Wildlife** — animals are often in dappled shade; a touch more
#   shadow lift is usually welcome. Cap stays modest so we don't
#   nuke contrast on backlit subjects.
#
# * **Landscape / Selfie / General** — defaults track LRC well per
#   the first calibration run (Selfie was the best-matching folder).
#   No overrides.
import dataclasses as _dc                            # noqa: E402

_TUNING_BY_STYLE: Final[dict[str, _TuningConstants]] = {
    "macro": _dc.replace(
        _TUNING,
        exposure_max_ev=0.3,             # vs default 1.0 — don't lift bg
        shadows_max=15.0,                # vs default 35 — preserve drop-off
        highlights_max=80.0,             # allow stronger pull (recover flash hotspot)
    ),
    "portrait": _dc.replace(
        _TUNING,
        exposure_max_ev=0.6,             # gentler exposure handling on faces
        shadows_max=22.0,                # avoid waxy-skin shadow lift
    ),
    "wildlife": _dc.replace(
        _TUNING,
        # 2026-05-28 (LRC-pair run): Wildlife-Action over-brightened
        # (dp50 +0.15, dp99 +0.21) — a dark subject against a bright
        # sky pulled the median down so the old 45 shadow-lift +
        # uncapped exposure washed the sky. Cap exposure and bring
        # shadows back near default; highlight protection covers the
        # rest.
        shadows_max=30.0,                # was 45 — was washing skies
        exposure_max_ev=0.6,             # was 1.0 (default) — cap the lift
    ),
}


# ── Public API ─────────────────────────────────────────────────


def compute_auto_params(
    img: np.ndarray, *, style: Optional[str] = None,
) -> Params:
    """Return baseline AUTO :class:`Params` for ``img`` (a uint8
    ``(H, W, 3)`` RGB array). The Params lift the image toward
    "balanced exposure, no clipping, lifted shadows, recovered
    highlights" — the same direction LRC's AUTO takes a photo.

    ``style`` is the photo's classified scenario string (one of the
    ``Scenario`` enum values: "macro", "portrait", "wildlife", etc.).
    When provided, per-style tuning overrides in
    :data:`_TUNING_BY_STYLE` are applied; when omitted (or for a
    style with no override), the default :data:`_TUNING` is used.
    The Process page reads the cached scenario via
    :func:`core.genre.peek_auto_genre` and passes it here.

    Sharpness + Saturation are left at 0 (LRC AUTO doesn't touch
    them, neither does this).

    Calibration: the magic numbers live in ``_TUNING`` /
    ``_TUNING_BY_STYLE``. The LRC-pair harness
    (``tools/compare_auto.py``) reports a per-style score; tune
    until the visual contact sheets read right."""
    if img.dtype != np.uint8 or img.ndim != 3 or img.shape[2] != 3:
        raise ValueError(
            f"expected uint8 (H, W, 3) array, got dtype={img.dtype} "
            f"shape={img.shape}"
        )

    # Convert to a luminance array in [0, 1].
    lum = _luminance(img)

    # All percentile statistics in one numpy call — much faster than
    # six separate np.percentile calls.
    p1, p5, p25, p50, p75, p95, p99 = np.percentile(
        lum, [1, 5, 25, 50, 75, 95, 99]
    )

    # A-router (spec/54 §3.1): nearest fitted cluster in the style's
    # feature space; legacy constants when the style has no fitted
    # data (e.g. classifications outside the calibrated set).
    tuning, cluster = _route_tuning(
        {"p1": p1, "p25": p25, "p50": p50, "p99": p99,
         "spread": p95 - p5},
        style,
    )

    exposure = _compute_exposure(p50, tuning, p99=p99)
    whites = _compute_whites(p99, tuning)
    blacks = _compute_blacks(p1, tuning)
    shadows = _compute_shadows(p25, tuning)
    highlights = _compute_highlights(p75, tuning)
    contrast = _compute_contrast(p5, p95, tuning)

    params = Params(
        exposure=exposure,
        contrast=contrast,
        highlights=highlights,
        shadows=shadows,
        whites=whites,
        blacks=blacks,
    )
    # Calibration trim (spec/54 §4.1): the Natural-strength knob scales
    # the fitted correction globally. This is THE chokepoint — surface
    # previews, photo exports and video rep-frame compiles all flow
    # through here, so trim changes apply everywhere consistently.
    natural_mult = _trim_multiplier("look_scale_natural")
    if natural_mult != 1.0:
        params = params.scaled(natural_mult)
    log.debug(
        "auto[%s|c%s]: median=%.3f p1=%.3f p99=%.3f spread=%.3f → %s",
        style or "default", cluster if cluster is not None else "-",
        p50, p1, p99, p95 - p5, params,
    )
    return params


def compute_look_params(
    img: np.ndarray,
    *,
    style: Optional[str] = None,
    look: str = "natural",
    intensity: float = 1.0,
    strength: float = 1.0,
) -> Params:
    """The spec/54 §3.2 Look engine — one call answers every tile of
    the chooser:

    * ``look="original"`` → identity :class:`Params` (no adjustment;
      the pickable "leave it as shot" choice).
    * ``look="natural"``  → the A-routed fitted correction.
    * any key in :data:`~core.photo_looks_data.LOOK_BIASES`
      (``"brighter"``, ``"deeper"``) → Natural + the mood bias scaled
      by ``intensity`` (spec/54 §4.1 — Intensity scales the BIAS
      only, never the correction; direction = the Look, distance =
      Intensity).

    ``strength`` (Nelson 2026-06-13) scales the WHOLE Look's effect
    via :meth:`Params.scaled` — 1.0 = the Look exactly as it ships;
    0.0 = identity (effectively Original); 2.0 = exaggerated. This
    is distinct from ``intensity``: intensity scales the bias only
    (spec/54 §4.1); strength scales the bias AND the underlying
    correction. The Edit-surface Strength slider feeds this.

    Raises ``ValueError`` for an unknown ``look`` so a stale persisted
    choice surfaces instead of silently rendering as something else.
    """
    if look == "original":
        return Params()
    return look_params_from_natural(
        compute_auto_params(img, style=style), look, intensity,
        strength=strength)


def look_params_from_natural(
    natural: Params, look: str, intensity: float = 1.0,
    *, strength: float = 1.0,
) -> Params:
    """The Look algebra on a precomputed Natural — the ONE place the
    bias math lives. Surfaces that cache the routed Natural per loaded
    image (AdjustmentSurface) call this directly instead of paying the
    AUTO recompute; :func:`compute_look_params` delegates here.

    ``intensity`` scales the BIAS only (spec/54 §4.1) — fixed at 1.0
    everywhere user-facing under the zero-sliders lock; the degree of
    freedom stays for an evidence-gated per-filter return.

    ``strength`` scales the FINAL Look's Params via
    :meth:`Params.scaled` — 1.0 is a no-op, 0.0 returns identity. The
    Edit Strength slider (Nelson 2026-06-13) feeds this. Applied AFTER
    the natural+bias composition so strength=0.5 reduces correction
    AND bias by the same factor — the Look's identity is preserved at
    a uniform fraction of its full effect."""
    s = float(strength)
    if look == "original":
        return Params()
    if look == "natural":
        return natural.scaled(s) if s != 1.0 else natural
    bias = LOOK_BIASES.get(look)
    if bias is None:
        raise ValueError(f"unknown look {look!r}")
    # Calibration trim (spec/54 §4.1): the per-look knob scales the
    # BIAS only — never the correction underneath.
    effective = float(intensity) * _trim_multiplier(f"look_scale_{look}")
    scaled = Params(**bias).scaled(effective)
    composed = Params(**{
        f: getattr(natural, f) + getattr(scaled, f)
        for f in natural.__dataclass_fields__
    })
    return composed.scaled(s) if s != 1.0 else composed


def available_looks() -> tuple[str, ...]:
    """The chooser's option keys, in display order: Original first,
    Natural, then the fitted-data mood biases. INTERNAL identifiers —
    display names are the UI's concern (spec/54 §7)."""
    return ("original", "natural", *LOOK_BIASES)


# ── Tone-calibration trims (spec/54 §4.1 + spec/55, Nelson 2026-06-10) ─
#
# Settings-global -100..100 knobs, one per Look and per filter; 0 = the
# shipped recipe exactly. Read lazily from the Settings repo (the
# ``save_jpeg`` precedent for a pure-core module reaching the
# app-settings seam) and cached — the Settings dialog calls
# :func:`invalidate_tone_scaling_cache` on Apply.

_TONE_SCALING_KEYS: Final[tuple[str, ...]] = (
    "look_scale_natural", "look_scale_brighter", "look_scale_deeper",
    "filter_scale_vivid", "filter_scale_bw", "filter_scale_sepia",
    "filter_scale_faded", "filter_scale_golden", "filter_scale_cinema",
    "filter_scale_bleach", "filter_scale_dramatic", "filter_scale_crisp",
)

_tone_scaling_cache: Optional[dict[str, int]] = None


def active_tone_scaling() -> dict[str, int]:
    """The current trim values (only non-zero entries), straight from
    Settings — cached. Also what the export lineage snapshot records,
    so every version remembers the trims it was rendered under."""
    global _tone_scaling_cache
    if _tone_scaling_cache is None:
        vals: dict[str, int] = {}
        try:
            from mira.settings.repo import SettingsRepo
            s = SettingsRepo().load()
            for key in _TONE_SCALING_KEYS:
                v = int(getattr(s, key, 0) or 0)
                if v:
                    vals[key] = max(-100, min(100, v))
        except Exception:                              # noqa: BLE001
            vals = {}                                  # headless/tests: shipped recipe
        _tone_scaling_cache = vals
    return _tone_scaling_cache


def invalidate_tone_scaling_cache() -> None:
    """Settings changed — next read reloads. Called by the Settings
    dialog host on Apply."""
    global _tone_scaling_cache
    _tone_scaling_cache = None


def _trim_multiplier(key: str) -> float:
    """Map a -100..100 trim to a 0..2 multiplier (0 = 1.0, shipped)."""
    return 1.0 + active_tone_scaling().get(key, 0) / 100.0


def creative_filter_amount(key: Optional[str]) -> float:
    """The blend amount for a filter under the user's trim — 1.0 =
    shipped recipe; passed to :func:`core.photo_render.apply_filter`."""
    if not key:
        return 1.0
    return _trim_multiplier(f"filter_scale_{key}")


def available_filters() -> tuple[str, ...]:
    """The creative-filter keys in display order (spec/55, the locked
    nine). ``None``/no-filter is the chooser's first option but not a
    key here. INTERNAL identifiers — display names are the UI's."""
    return tuple(FILTER_RECIPES)


def resolve_filter_recipe(
    key: Optional[str], style: Optional[str] = None,
) -> Optional[dict]:
    """The recipe dict for a filter key, honouring per-style overrides
    (spec/55 — ``crisp`` is specimen-dark on macro, warm-feather on
    wildlife). ``None``/empty key → ``None`` (no filter). Unknown keys
    raise so a stale persisted choice surfaces instead of silently
    rendering unfiltered."""
    if not key:
        return None
    entry = FILTER_RECIPES.get(key)
    if entry is None:
        raise ValueError(f"unknown creative filter {key!r}")
    by_style = entry.get("by_style", {})
    if style and style in by_style:
        return by_style[style]
    return entry["base"]


def _route_tuning(
    stats: dict[str, float], style: Optional[str],
) -> tuple[_TuningConstants, Optional[int]]:
    """Resolve the constants for a photo: the fitted cluster of the
    style's router entry (nearest centroid in z-scored feature
    space), or the legacy per-style/default constants when no fitted
    data exists. Returns ``(tuning, cluster_index)`` — cluster is
    ``None`` on the legacy path."""
    entry = ROUTER.get(style or "general")
    if entry is None:
        return _resolve_tuning(style), None
    feat = np.array([stats[k] for k in FEATURE_KEYS], dtype=np.float64)
    mu = np.array(entry["zscore_mean"], dtype=np.float64)
    sd = np.array(entry["zscore_std"], dtype=np.float64)
    z = (feat - mu) / sd
    cents = np.array(entry["centroids"], dtype=np.float64)
    cluster = int(np.argmin(((cents - z) ** 2).sum(axis=1)))
    return _TuningConstants(**entry["clusters"][cluster]), cluster


def _resolve_tuning(style: Optional[str]) -> _TuningConstants:
    """The LEGACY constants: per-style override if one exists, else
    the default tuning. Still the fallback for styles without fitted
    router data, and the baseline the calibration workbench fits
    against."""
    if style is None:
        return _TUNING
    return _TUNING_BY_STYLE.get(style, _TUNING)


# ── Per-slider helpers (testable in isolation) ─────────────────


def _luminance(img: np.ndarray) -> np.ndarray:
    """Rec. 709 luminance ∈ [0, 1] from a uint8 RGB array."""
    r = img[..., 0].astype(np.float32) / 255.0
    g = img[..., 1].astype(np.float32) / 255.0
    b = img[..., 2].astype(np.float32) / 255.0
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _compute_exposure(
    median: float, tuning: _TuningConstants = _TUNING,
    *, p99: Optional[float] = None,
) -> float:
    """EV shift that maps ``median`` to ``exposure_target``. Capped
    to ``±exposure_max_ev``. ``tuning`` defaults to the global
    constants — pass the per-style override to vary behaviour.

    ``p99`` (the 99th-percentile luminance) enables **highlight
    protection**: a positive (brightening) lift is reduced so the
    resulting p99 doesn't exceed ``exposure_highlight_ceiling`` —
    exposure scales luminance ~linearly (``work * 2**ev``), so the
    largest safe lift is ``log2(ceiling / p99)``. Never forces a
    darkening lift (a legitimately bright scene keeps its exposure;
    the Highlights slider pulls it down if needed). ``p99=None``
    disables protection (back-compat for direct callers / tests)."""
    if median < 0.02:
        ev = tuning.exposure_max_ev
    elif median > 0.98:
        ev = -tuning.exposure_max_ev
    else:
        ev = float(np.log2(tuning.exposure_target / median))
    ev = float(np.clip(ev, -tuning.exposure_max_ev, tuning.exposure_max_ev))
    if ev > 0.0 and p99 is not None and p99 > 1e-6:
        allowed = float(np.log2(tuning.exposure_highlight_ceiling / p99))
        ev = min(ev, max(0.0, allowed))
    return ev


def _compute_whites(
    p99: float, tuning: _TuningConstants = _TUNING,
) -> float:
    """Push whites slider to land the 99th percentile at
    ``whites_p99_target``. Already-bright images (p99 ≥ target) get
    0. Capped to the LRC slider range ``[-100, +100]``."""
    if p99 >= tuning.whites_p99_target:
        return 0.0
    raw = (tuning.whites_p99_target - p99) * tuning.whites_gain
    return float(min(raw, 100.0))


def _compute_blacks(
    p1: float, tuning: _TuningConstants = _TUNING,
) -> float:
    """Move blacks slider to land the 1st percentile at the target.

    LRC convention: +blacks brightens shadows, -blacks deepens them.
    When ``p1 > target`` (shadows too bright), we return a NEGATIVE
    blacks value. Already-clipped images (``p1 ≤ target``) get 0."""
    if p1 <= tuning.blacks_p1_target:
        return 0.0
    raw = (p1 - tuning.blacks_p1_target) * tuning.blacks_gain
    return float(-min(raw, 100.0))


def _compute_shadows(
    p25: float, tuning: _TuningConstants = _TUNING,
) -> float:
    """Lift shadows when the lower quartile is dark. Capped to
    ``shadows_max``."""
    if p25 >= tuning.shadows_p25_threshold:
        return 0.0
    raw = (tuning.shadows_p25_threshold - p25) * tuning.shadows_gain
    return float(min(raw, tuning.shadows_max))


def _compute_highlights(
    p75: float, tuning: _TuningConstants = _TUNING,
) -> float:
    """Pull highlights (negative slider) when the upper quartile is
    bright. Capped to ``-highlights_max``."""
    if p75 <= tuning.highlights_p75_threshold:
        return 0.0
    raw = (p75 - tuning.highlights_p75_threshold) * tuning.highlights_gain
    return float(-min(raw, tuning.highlights_max))


def _compute_contrast(
    p5: float, p95: float, tuning: _TuningConstants = _TUNING,
) -> float:
    """Boost contrast when the histogram is compressed (small spread
    between 5th and 95th percentile)."""
    spread = p95 - p5
    if spread >= tuning.contrast_compression_threshold:
        return 0.0
    raw = (tuning.contrast_compression_threshold - spread) * tuning.contrast_gain
    return float(min(raw, tuning.contrast_max))
