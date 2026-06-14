"""Tests for core.sharpness — passive cull ranking (E3).

Pure-logic, Qt-free. Synthetic numpy images: a sharp checkerboard
vs a Gaussian-blurred copy must rank as sharper.
"""

from __future__ import annotations

import numpy as np
import pytest

from core.sharpness import (
    METRIC_WHOLE_FRAME,
    all_scored,
    cached_score,
    normalise_scores,
    peek_cached_score,
    sharpness_score,
)

cv2 = pytest.importorskip("cv2")


def _sharp_image(size: int = 256) -> np.ndarray:
    """High-frequency checkerboard — maximal Laplacian variance."""
    block = 8
    img = np.zeros((size, size), dtype=np.uint8)
    for y in range(0, size, block):
        for x in range(0, size, block):
            if ((x // block) + (y // block)) % 2 == 0:
                img[y:y + block, x:x + block] = 255
    return img


def _blurred_image(size: int = 256) -> np.ndarray:
    return cv2.GaussianBlur(_sharp_image(size), (21, 21), 0)


def _flat_image(size: int = 256) -> np.ndarray:
    return np.full((size, size), 128, dtype=np.uint8)


# ── sharpness_score ──────────────────────────────────────────────


def test_sharp_scores_higher_than_blurred():
    assert sharpness_score(_sharp_image()) > sharpness_score(_blurred_image())


def test_flat_image_scores_near_zero():
    assert sharpness_score(_flat_image()) < 1.0


def test_colour_image_uses_luminance_channel_order_irrelevant():
    gray = _sharp_image()
    bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    rgb = bgr[..., ::-1].copy()
    s_gray = sharpness_score(gray)
    s_bgr = sharpness_score(bgr)
    s_rgb = sharpness_score(rgb)
    # All within a tight tolerance — only luminance is used.
    assert abs(s_bgr - s_gray) / s_gray < 0.02
    assert abs(s_rgb - s_gray) / s_gray < 0.02


def test_empty_or_invalid_returns_zero_never_raises():
    assert sharpness_score(np.array([])) == 0.0
    assert sharpness_score(None) == 0.0  # type: ignore[arg-type]
    assert sharpness_score(np.zeros((0, 0), dtype=np.uint8)) == 0.0


def test_large_image_downscaled_still_discriminates():
    big_sharp = _sharp_image(4096)
    big_blur = cv2.GaussianBlur(big_sharp, (31, 31), 0)
    assert sharpness_score(big_sharp) > sharpness_score(big_blur)


# ── journal cache ────────────────────────────────────────────────


def test_cached_score_computes_once_then_reuses():
    journal: dict = {}
    calls = {"n": 0}

    def compute() -> float:
        calls["n"] += 1
        return 42.0

    v1 = cached_score(journal, "a.RW2", compute)
    v2 = cached_score(journal, "a.RW2", compute)
    assert v1 == v2 == 42.0
    assert calls["n"] == 1  # second call hit the cache
    # Persisted in the journal under the expected shape
    assert journal["sharpness"]["a.RW2"]["v"] == 42.0
    assert journal["sharpness"]["a.RW2"]["m"] == METRIC_WHOLE_FRAME


def test_cached_score_recomputes_on_metric_change():
    journal: dict = {}
    cached_score(journal, "a", lambda: 1.0, metric="old_metric")
    # Different metric tag → stale entry, recompute
    v = cached_score(journal, "a", lambda: 2.0, metric=METRIC_WHOLE_FRAME)
    assert v == 2.0
    assert journal["sharpness"]["a"]["m"] == METRIC_WHOLE_FRAME


def test_cached_score_tolerates_corrupt_cache():
    journal = {"sharpness": "not a dict"}
    v = cached_score(journal, "a", lambda: 7.0)
    assert v == 7.0
    assert isinstance(journal["sharpness"], dict)


def test_peek_cached_score_does_not_compute():
    journal: dict = {}
    assert peek_cached_score(journal, "a") is None
    cached_score(journal, "a", lambda: 3.0)
    assert peek_cached_score(journal, "a") == 3.0


def test_peek_handles_missing_and_corrupt():
    assert peek_cached_score({}, "a") is None
    assert peek_cached_score({"sharpness": {"a": "bad"}}, "a") is None


# ── normalise_scores ─────────────────────────────────────────────


def test_normalise_maps_min_zero_max_one():
    out = normalise_scores({"a": 10.0, "b": 20.0, "c": 30.0})
    assert out["a"] == 0.0
    assert out["c"] == 1.0
    assert out["b"] == pytest.approx(0.5)


def test_normalise_single_score_maps_to_one():
    """A lone frame isn't punished — nothing to discriminate."""
    assert normalise_scores({"a": 99.0}) == {"a": 1.0}


def test_normalise_all_equal_maps_to_one():
    assert normalise_scores({"a": 5.0, "b": 5.0}) == {"a": 1.0, "b": 1.0}


def test_normalise_empty():
    assert normalise_scores({}) == {}


# ── all_scored ───────────────────────────────────────────────────


def test_all_scored_false_until_every_name_cached():
    journal: dict = {}
    names = ["a", "b", "c"]
    assert all_scored(journal, names) is False
    cached_score(journal, "a", lambda: 1.0)
    cached_score(journal, "b", lambda: 1.0)
    assert all_scored(journal, names) is False
    cached_score(journal, "c", lambda: 1.0)
    assert all_scored(journal, names) is True


def test_all_scored_false_on_missing_cache_key():
    assert all_scored({}, ["a"]) is False


# ── sharpness_rating: bounded absolute 0..1000 bar value ─────────


def test_sharpness_rating_is_bounded_and_monotonic():
    from core.sharpness import (
        sharpness_rating, RATING_MAX,
        _RATING_FLOOR, _RATING_FULL,
    )
    assert sharpness_rating(0) == 0
    assert sharpness_rating(_RATING_FLOOR) == 0          # soft → empty
    assert sharpness_rating(_RATING_FLOOR - 5) == 0      # below floor
    assert sharpness_rating(_RATING_FULL) == RATING_MAX  # tack-sharp → full
    assert sharpness_rating(_RATING_FULL * 3) == RATING_MAX   # clamps
    mid = sharpness_rating((_RATING_FLOOR + _RATING_FULL) / 2)
    assert 0 < mid < RATING_MAX
    # Monotonic non-decreasing across the meaningful band.
    vals = [sharpness_rating(v) for v in (30, 100, 200, 400, 800, 1200)]
    assert vals == sorted(vals)
    assert vals[0] == 0 and vals[-1] == RATING_MAX


def test_sharpness_rating_garbage_safe():
    from core.sharpness import sharpness_rating
    assert sharpness_rating(None) == 0          # type: ignore[arg-type]
    assert sharpness_rating("nope") == 0        # type: ignore[arg-type]
