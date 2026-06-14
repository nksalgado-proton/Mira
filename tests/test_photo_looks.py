"""Layer-A router + Looks engine (spec/54 §3).

Covers the generated data tables' integrity, the router's
behavioral split (dark vs bright photos land on different fitted
clusters), the legacy fallback for uncalibrated styles, and the
Look algebra (Original identity, Natural = routed AUTO, bias ×
Intensity on top, unknown look surfaces loudly).
"""

from __future__ import annotations

import numpy as np
import pytest

from core.photo_auto import (
    _TuningConstants,
    _route_tuning,
    available_looks,
    compute_auto_params,
    compute_look_params,
)
from core.photo_looks_data import FEATURE_KEYS, LOOK_BIASES, ROUTER
from core.photo_render import Params


def _gradient(lo: float, hi: float) -> np.ndarray:
    """A (64, 64, 3) uint8 luminance ramp from ``lo`` to ``hi``."""
    ramp = np.linspace(lo, hi, 64 * 64, dtype=np.float32).reshape(64, 64)
    return np.clip(
        np.stack([ramp] * 3, axis=2), 0, 255).astype(np.uint8)


DARK = _gradient(5, 120)
BRIGHT = _gradient(90, 240)

# Hand stats well inside the dark-lift / bright-leave-alone modes the
# 2026-06-10 evidence run found in every scenario.
DARK_STATS = {"p50": 0.20, "p25": 0.10, "p99": 0.80, "p1": 0.02,
              "spread": 0.70}
BRIGHT_STATS = {"p50": 0.65, "p25": 0.50, "p99": 0.90, "p1": 0.15,
                "spread": 0.50}


# ── Generated tables ─────────────────────────────────────────────


def test_router_tables_complete():
    """Guards a broken regeneration of core/photo_looks_data.py."""
    assert sorted(ROUTER) == [
        "general", "landscape", "macro", "portrait", "selfie",
        "wildlife"]
    n_feat = len(FEATURE_KEYS)
    for scenario, entry in ROUTER.items():
        assert len(entry["zscore_mean"]) == n_feat
        assert len(entry["zscore_std"]) == n_feat
        assert len(entry["centroids"]) == len(entry["clusters"]) >= 2
        for cent in entry["centroids"]:
            assert len(cent) == n_feat
        for cluster in entry["clusters"]:
            # Every cluster dict must construct a full constant set.
            _TuningConstants(**cluster)


def test_look_biases_present():
    assert sorted(LOOK_BIASES) == ["brighter", "deeper"]
    for bias in LOOK_BIASES.values():
        Params(**bias)              # keys must be Params fields


# ── Router behavior ──────────────────────────────────────────────


def test_router_roundtrips_every_centroid():
    """Self-consistency of the z-transform + nearest-centroid
    assignment: stats reconstructed AT each fitted centroid must
    route back to that centroid's own cluster. (No assumption about
    what the clusters mean — e.g. general splits on highlight
    headroom, not on median brightness.)"""
    for scenario, entry in ROUTER.items():
        mu = np.array(entry["zscore_mean"])
        sd = np.array(entry["zscore_std"])
        for i, cent in enumerate(entry["centroids"]):
            feat = mu + sd * np.array(cent)
            stats = dict(zip(FEATURE_KEYS, feat))
            tuning, cluster = _route_tuning(stats, scenario)
            assert cluster == i, (scenario, i)
            assert tuning == _TuningConstants(**entry["clusters"][i])


def test_routed_auto_lifts_dark_more_than_bright():
    dark = compute_auto_params(DARK, style="wildlife")
    bright = compute_auto_params(BRIGHT, style="wildlife")
    assert dark != bright
    assert dark.exposure > bright.exposure


def test_legacy_fallback_for_uncalibrated_style():
    tuning, cluster = _route_tuning(DARK_STATS, "sports")
    assert cluster is None
    assert isinstance(tuning, _TuningConstants)
    # And the public API stays crash-free for such styles.
    compute_auto_params(DARK, style="sports")


def test_style_none_routes_as_general():
    """``style=None`` rides the fitted general entry, mirroring the
    calibration sweep's convention."""
    _, cluster = _route_tuning(DARK_STATS, None)
    assert cluster is not None


# ── Look algebra ─────────────────────────────────────────────────


def test_original_is_identity():
    assert compute_look_params(DARK, look="original").is_identity


def test_natural_equals_routed_auto():
    assert compute_look_params(DARK, style="macro", look="natural") \
        == compute_auto_params(DARK, style="macro")


def test_bias_rides_on_natural_and_intensity_scales_it():
    natural = compute_auto_params(DARK, style="macro")
    brighter = compute_look_params(
        DARK, style="macro", look="brighter", intensity=1.0)
    assert brighter.exposure == pytest.approx(
        natural.exposure + LOOK_BIASES["brighter"]["exposure"])
    # Vibrance is pure mood character — AUTO never sets it.
    assert brighter.vibrance == pytest.approx(
        LOOK_BIASES["brighter"]["vibrance"])
    # Intensity 0 → exactly Natural; 2.0 → double the bias delta.
    assert compute_look_params(
        DARK, style="macro", look="brighter", intensity=0.0) == natural
    double = compute_look_params(
        DARK, style="macro", look="deeper", intensity=2.0)
    assert double.exposure == pytest.approx(
        natural.exposure + 2.0 * LOOK_BIASES["deeper"]["exposure"])


def test_unknown_look_raises():
    with pytest.raises(ValueError, match="unknown look"):
        compute_look_params(DARK, look="cinematic")


def test_available_looks_order():
    assert available_looks() == (
        "original", "natural", "brighter", "deeper")
