"""Sharpness scoring for the passive cull ranking (E3).

Frozen design: `docs/18-culler-spec.md` §"Ranking (sharpness score)".

The culler shows a per-photo sharpness score **always** and
**passively** — the user leans on it or ignores it; it is never an
automatic cull decision (sharpest ≠ the keeper: eyes closed, wrong
subject). Two functional requirements drive the shape of this module:

* **Must not block bucket entry** ("Speed is King"). So the score is
  *lazy* and *cached in the journal*: computed once per photo on
  demand (the page schedules it off the navigation path), then
  persisted so resume / re-entry never recomputes.
* **Whole-frame standalone now; AF-region later.** Phase-1 metric is
  variance-of-Laplacian over the whole (downscaled) frame — the
  industry-standard focus measure. When AF-point extraction lands
  (E7), the same metric runs on just the AF region; the cache key
  carries a metric tag so a stale whole-frame score is recomputed.

Pure-logic and **Qt-free** — operates on numpy arrays. The page
converts its pixmap → ndarray and calls in; core never imports
PyQt (unlike the older ``focus_peaking`` module, whose Qt coupling
is a wart we deliberately do not propagate here). ``cv2`` is a hard
project dependency.
"""

from __future__ import annotations

import logging
from typing import Callable, Iterable, Optional

import numpy as np

try:  # pragma: no cover — cv2 is a hard dep in normal installs
    import cv2  # type: ignore
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore


log = logging.getLogger(__name__)


# Journal key holding the per-photo score cache:
#   journal["sharpness"] = {filename: {"v": <float>, "m": <metric tag>}}
# The metric tag lets a future AF-region score supersede a stale
# whole-frame one without a journal migration.
_CACHE_KEY = "sharpness"

# Metric tags. Bump / add when the scoring changes incompatibly.
METRIC_WHOLE_FRAME = "lapvar_wf_v1"

# Max long-edge the score is computed at. Downscaling first is both
# faster and *more* discriminating for whole-frame focus: it
# suppresses pixel noise (which would inflate Laplacian variance on
# high-ISO shots) while preserving the gross structure that separates
# a focused frame from a soft one. 1024 is a good speed/signal point.
_SCORE_LONG_EDGE = 1024


def sharpness_score(image: np.ndarray) -> float:
    """Variance-of-Laplacian sharpness of a numpy image.

    Accepts grayscale ``(H, W)`` or colour ``(H, W, 3)`` (BGR or RGB —
    only luminance is used so channel order is irrelevant). Higher =
    sharper. Returns ``0.0`` on an empty/invalid image or if cv2 is
    unavailable (never raises — the caller treats 0.0 as "unknown /
    lowest" and the badge degrades gracefully).

    The frame is downscaled to a max long edge before measuring (see
    ``_SCORE_LONG_EDGE``): faster and less noise-sensitive without
    losing the focus signal.
    """
    if cv2 is None or image is None:
        return 0.0
    arr = np.asarray(image)
    if arr.size == 0 or arr.ndim not in (2, 3):
        return 0.0
    try:
        if arr.ndim == 3:
            gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
        else:
            gray = arr
        gray = _downscale(gray)
        lap = cv2.Laplacian(gray, cv2.CV_64F)
        return float(lap.var())
    except Exception as exc:  # noqa: BLE001 — scoring must never crash UI
        log.warning("sharpness_score failed: %s", exc)
        return 0.0


def _downscale(gray: np.ndarray) -> np.ndarray:
    h, w = gray.shape[:2]
    long_edge = max(h, w)
    if long_edge <= _SCORE_LONG_EDGE:
        return gray
    scale = _SCORE_LONG_EDGE / float(long_edge)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    return cv2.resize(gray, (new_w, new_h), interpolation=cv2.INTER_AREA)


# ── Journal cache (lazy, persisted) ──────────────────────────────


def cached_score(
    journal: dict,
    filename: str,
    compute: Callable[[], float],
    *,
    metric: str = METRIC_WHOLE_FRAME,
) -> float:
    """Return ``filename``'s cached score, computing + storing once.

    ``compute`` is a zero-arg callable the caller supplies (it owns
    loading the pixels — keeps this module Qt-free). It is invoked
    **only on a cache miss** or when the cached entry was produced by
    a different ``metric`` (e.g. a stale whole-frame score after the
    AF-region metric lands). The result is written back into
    ``journal[_CACHE_KEY]`` so it survives resume and is never
    recomputed on navigation.

    Page contract: call this off the hot navigation path (background /
    idle) so bucket entry is never blocked.
    """
    cache = journal.get(_CACHE_KEY)
    if not isinstance(cache, dict):
        cache = {}
        journal[_CACHE_KEY] = cache
    entry = cache.get(filename)
    if isinstance(entry, dict) and entry.get("m") == metric:
        v = entry.get("v")
        if isinstance(v, (int, float)):
            return float(v)
    value = float(compute())
    cache[filename] = {"v": value, "m": metric}
    return value


def peek_cached_score(journal: dict, filename: str) -> Optional[float]:
    """Return the cached score if present, else ``None`` — without
    computing. The page uses this to render the badge immediately for
    already-scored photos and show a pending state for the rest."""
    cache = journal.get(_CACHE_KEY)
    if not isinstance(cache, dict):
        return None
    entry = cache.get(filename)
    if isinstance(entry, dict) and isinstance(entry.get("v"), (int, float)):
        return float(entry["v"])
    return None


def normalise_scores(
    scores: dict[str, float],
) -> dict[str, float]:
    """Map raw scores to a 0.0–1.0 relative position **within the
    given set** (typically one bucket / one burst).

    Raw variance-of-Laplacian is unbounded and scene-dependent — the
    useful signal for the user is "which of *these* is sharpest", not
    the absolute number. Min→0.0, max→1.0, linear between. A single
    score, or all-equal scores, map to 1.0 (nothing to discriminate;
    don't punish a lone frame). Empty input → empty dict.
    """
    if not scores:
        return {}
    values = list(scores.values())
    lo, hi = min(values), max(values)
    if hi <= lo:
        return {k: 1.0 for k in scores}
    span = hi - lo
    return {k: (v - lo) / span for k, v in scores.items()}


# ── Bounded absolute rating (for the progress-bar display) ───────
#
# Variance-of-Laplacian is unbounded, so a bar needs an absolute
# mapping (Nelson 2026-05-16). NOT bucket-relative like
# normalise_scores — the same philosophy as the peaking reference:
# soft photo → near-empty, tack-sharp → near-full, every photo on
# its own. Linear floor→full, clamped 0..RATING_MAX. The two
# endpoints are the eyeball knobs (calibrated from 30 real RW2:
# softest ≈30–55, median ≈200, genuinely sharp ≈1200–2400).
RATING_MAX = 1000
_RATING_FLOOR = 30.0     # lapvar ≤ this → 0 (visibly "soft")
_RATING_FULL = 1200.0    # lapvar ≥ this → RATING_MAX (tack sharp)


def sharpness_rating(raw_score: float) -> int:
    """Map a raw :func:`sharpness_score` to a bounded ``0..RATING_MAX``
    integer for the progress-bar display. Absolute (not relative to
    a bucket): a soft frame reads low, a tack-sharp one reads near
    full, judged on its own. Endpoints are calibration constants.
    """
    try:
        v = float(raw_score)
    except (TypeError, ValueError):
        return 0
    if v <= _RATING_FLOOR:
        return 0
    if v >= _RATING_FULL:
        return RATING_MAX
    frac = (v - _RATING_FLOOR) / (_RATING_FULL - _RATING_FLOOR)
    return int(round(frac * RATING_MAX))


def all_scored(journal: dict, filenames: Iterable[str]) -> bool:
    """True iff every name has a cached score — the page uses this to
    know when the background scoring pass for a bucket is done."""
    cache = journal.get(_CACHE_KEY)
    if not isinstance(cache, dict):
        return False
    return all(
        isinstance(cache.get(n), dict)
        and isinstance(cache[n].get("v"), (int, float))
        for n in filenames
    )


__all__ = [
    "METRIC_WHOLE_FRAME",
    "sharpness_score",
    "cached_score",
    "peek_cached_score",
    "normalise_scores",
    "all_scored",
    "sharpness_rating",
    "RATING_MAX",
]
