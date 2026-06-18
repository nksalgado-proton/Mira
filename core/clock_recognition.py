"""Camera-clock calibration by recognition — candidate generator (spec/88).

Replaces the front end of the "I don't know my camera's TZ" flow. Instead of
asking the user to *construct* a sync pair (which mis-fires when two photos
only feel simultaneous — Nelson 2026-06-18: a pair that felt simultaneous was
~an hour apart, silently mis-dating a whole camera's photos), the app
*proposes* candidate pairs and the user *recognizes* one.

The math reused: :func:`core.clock_calibration.snap_to_tz_offset` /
:func:`snap_disagreement` for the 15-minute grid, and ``CalibrationPair`` for
the result the engine consumes. The math added: per-pair κ normalization +
clustering on the snapped κ.

Algorithm (spec/88 §2). For every (camera item ``c``, phone item ``p``) within
the trip:

1. Compute ``off = Tp − Tc`` (the offset a :class:`CalibrationPair` would
   carry) and normalize to the camera's *constant* set-TZ::

       κ = phone_tz(p) − off   # the camera's set TZ, constant across the trip

   ``phone_tz(p)`` is the phone's UTC offset on ``p``'s day. The default
   source is ``p.tz_offset_minutes`` (the per-photo EXIF
   ``OffsetTimeOriginal`` value spec/45 already extracts); callers with a
   per-day map can override via the ``phone_tz_for`` callback. For
   single-zone trips ``phone_tz`` is constant and clustering on ``off``
   directly is equivalent — the normalization only matters for
   multi-zone trips, where it keeps the cluster intact across the day the
   phone crosses a border.

2. Snap ``κ`` to the 15-minute grid (:func:`snap_to_tz_offset`). The pair's
   *tightness* is the distance between raw and snapped κ. Pairs with
   tightness larger than half a 15-minute step (~7.5 min) are not
   plausibly simultaneous — snapping them either lands on an ambiguous
   boundary or pushes them into a zone they don't belong to — and are
   dropped.

3. Cluster surviving pairs by snapped κ. The dominant pile is the
   camera's proposed set-TZ. Per-day corrections fall out as
   ``trip_day.tz − κ*`` (spec/57 §4.2 applies them).

Output: candidate clusters sorted by size descending. The recognition UI
shows the strongest cluster first; the user confirms by recognizing one of
its pairs, and the confirmed pair is fed to
:func:`core.clock_calibration.build_calibration` verbatim — the existing
median-outlier rejection + pair-vs-TZ cross-check still apply.

Qt-free, store-free; tested in isolation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, List, Optional, Sequence, Tuple

from core.clock_calibration import (
    CalibrationPair,
    snap_disagreement,
    snap_to_tz_offset,
)
from core.fresh_source import SourceItem

log = logging.getLogger(__name__)


# Half of the 15-min snap step. Pairs whose implied κ is farther than this
# from any 15-min multiple are not plausibly simultaneous — snapping them
# would either be ambiguous (equidistant between two zones) or push them
# into a zone they don't actually belong to. spec/88 §2 step 3.
TIGHTNESS_TOLERANCE = timedelta(minutes=7, seconds=30)

# Maximum raw clock delta between cam_t and phone_t for a pair to even be
# considered (Nelson 2026-06-18 — the hard rule). For two photos to be
# RECOGNIZABLE as "the same moment", they must depict the same scene,
# and scene changes meaningfully over hours. The spec/88 cross-TZ
# normalization math is mathematically right but USELESS for human
# recognition: pairs in a cross-TZ "true" cluster are still hours apart
# by clock and depict different scenes (different light, different
# location), so the user can't recognize them anyway. We accept the
# trade-off: recognition only helps when the camera was set roughly
# in line with the phone clock; otherwise produces no clusters and the
# user falls back to the manual picker. 15 minutes is "a few minutes"
# in any reasonable interpretation and lines up with the 15-min snap
# step.
MAX_PAIR_RAW_DELTA = timedelta(minutes=15)

# Ranking inside a cluster: prefer pairs at least this far apart in camera
# time so the small set shown to the user spans the trip (spec/88 §2 ranking:
# "spread across the trip, not five frames from one minute").
_RANK_SPREAD_MIN_GAP = timedelta(minutes=30)

# Default number of cards the recognition UI shows per cluster. The cluster
# carries the full ranked list — anything past the first ``cards`` is the
# "show another" tail.
DEFAULT_CARDS_PER_CLUSTER = 6


@dataclass(frozen=True)
class CandidatePair:
    """One (camera_item, phone_item) pair plausibly simultaneous: its
    implied κ sits within :data:`TIGHTNESS_TOLERANCE` of a 15-minute
    multiple."""

    camera_item: SourceItem
    phone_item: SourceItem
    phone_tz_minutes: int
    raw_kappa_minutes: float
    snapped_kappa_minutes: int
    tightness: timedelta

    def to_calibration_pair(self) -> CalibrationPair:
        """Build the :class:`CalibrationPair` the engine consumes from
        this pair's *raw* EXIF timestamps. ``build_calibration`` sees
        unmodified numbers — its own snap + cross-check + outlier
        rejection still apply downstream, exactly as in the manual flow."""
        return CalibrationPair(
            camera_path=self.camera_item.path,
            reference_path=self.phone_item.path,
            camera_time=self.camera_item.timestamp,
            reference_time=self.phone_item.timestamp,
        )


@dataclass(frozen=True)
class CandidateCluster:
    """All :class:`CandidatePair`s sharing a snapped κ — i.e. all pairs that
    would confirm the same camera set-TZ. Pairs are ranked: tightest first,
    then thinned so the leading sample spans the trip in camera time."""

    snapped_kappa_minutes: int
    pairs: Tuple[CandidatePair, ...]

    @property
    def size(self) -> int:
        return len(self.pairs)


def find_candidate_pairs(
    camera_items: Sequence[SourceItem],
    phone_items: Sequence[SourceItem],
    *,
    phone_tz_for: Optional[Callable[[SourceItem], Optional[int]]] = None,
    default_phone_tz_minutes: Optional[int] = None,
    tolerance: timedelta = TIGHTNESS_TOLERANCE,
    cards_per_cluster: int = DEFAULT_CARDS_PER_CLUSTER,
) -> List[CandidateCluster]:
    """Generate and cluster candidate sync pairs.

    ``phone_tz_for(p)`` returns the phone's UTC offset (minutes east) on
    ``p``'s day. Defaults to ``p.tz_offset_minutes`` — the per-photo EXIF
    answer is the most precise source. When that EXIF tag is absent on a
    phone item (older iPhones, photos exported through a tool that
    strips OffsetTimeOriginal — Nelson 2026-06-18 iPhone 6s case), the
    per-photo result is ``None`` and ``default_phone_tz_minutes`` is used
    as a fallback. For single-zone trips this is exactly spec/88 §2's
    equivalence ("phone_tz is constant and clustering on off directly is
    equivalent"). When neither a per-photo answer nor a default is
    available, the phone item is skipped. Items with missing timestamps
    are skipped on both sides.

    Returned clusters are sorted by ``size`` descending; ties broken by
    smallest top-pair tightness, then by κ closest to zero (the
    correctly-set-camera case wins ties — spec/88 §3 point 2).
    """
    if phone_tz_for is None:
        def phone_tz_for(it: SourceItem) -> Optional[int]:
            if it.tz_offset_minutes is not None:
                return it.tz_offset_minutes
            return default_phone_tz_minutes

    cam_with_t = [c for c in camera_items if c.timestamp is not None]
    phone_with_tz: list[tuple[SourceItem, int]] = []
    for p in phone_items:
        if p.timestamp is None:
            continue
        tz = phone_tz_for(p)
        if tz is None:
            continue
        phone_with_tz.append((p, int(tz)))

    if not cam_with_t or not phone_with_tz:
        return []

    buckets: dict[int, list[CandidatePair]] = {}
    filtered_too_far = 0
    for c in cam_with_t:
        for p, ptz in phone_with_tz:
            off = p.timestamp - c.timestamp
            # Hours-apart pairs can't be "the same moment" — the user
            # can't recognize matching scenes across that gap, regardless
            # of what the cross-TZ math says. Filtered hard.
            if abs(off) > MAX_PAIR_RAW_DELTA:
                filtered_too_far += 1
                continue
            raw_kappa_minutes = ptz - (off.total_seconds() / 60.0)
            raw_kappa_td = timedelta(minutes=raw_kappa_minutes)
            snapped_kappa_td = snap_to_tz_offset(raw_kappa_td)
            tightness = snap_disagreement(raw_kappa_td, snapped_kappa_td)
            # spec/88 §2 step 3: tolerance is tightness ``< ~7.5 min`` so
            # adjacent zones never blur. Snap-to-nearest already bounds
            # tightness ≤ 7.5; the strict ``>=`` here only bites at the
            # exact midpoint between two zones (a pair that snap couldn't
            # disambiguate — drop it rather than route it arbitrarily).
            if tightness >= tolerance:
                continue
            snapped_kappa_minutes = int(round(
                snapped_kappa_td.total_seconds() / 60.0))
            buckets.setdefault(snapped_kappa_minutes, []).append(
                CandidatePair(
                    camera_item=c,
                    phone_item=p,
                    phone_tz_minutes=ptz,
                    raw_kappa_minutes=raw_kappa_minutes,
                    snapped_kappa_minutes=snapped_kappa_minutes,
                    tightness=tightness,
                )
            )

    if filtered_too_far:
        log.info(
            "clock_recognition: dropped %d pair(s) more than %s apart "
            "(unrecognizable as the same moment)",
            filtered_too_far, MAX_PAIR_RAW_DELTA,
        )
    clusters: list[CandidateCluster] = []
    for kappa, raw_pairs in buckets.items():
        ranked = _rank_pairs(raw_pairs, cards_per_cluster)
        clusters.append(CandidateCluster(
            snapped_kappa_minutes=kappa,
            pairs=tuple(ranked),
        ))
    # Largest cluster first; ties → tightest top-pair, then κ closest to 0
    # (so the 0-offset cluster wins all ties — Nelson's common case, spec/88
    # §3 point 2).
    clusters.sort(key=lambda c: (
        -c.size,
        c.pairs[0].tightness,
        abs(c.snapped_kappa_minutes),
    ))
    return clusters


def _rank_pairs(
    pairs: list[CandidatePair],
    cards: int,
) -> list[CandidatePair]:
    """Sort ``pairs`` by tightness ascending; pick a leading sample of
    ``cards`` pairs at least :data:`_RANK_SPREAD_MIN_GAP` apart in camera
    time (greedy, tightest-first); append the rest in tightness order so
    callers wanting "show more" can keep going.

    If the spread filter under-fills (small trip — every shot within an
    hour), top up the leading sample from the tail so the cluster still
    surfaces ``cards`` items when it has them."""
    if not pairs:
        return []
    by_tightness = sorted(pairs, key=lambda p: p.tightness)
    leading: list[CandidatePair] = []
    rest: list[CandidatePair] = []
    for p in by_tightness:
        if len(leading) >= cards:
            rest.append(p)
            continue
        too_close = any(
            abs(p.camera_item.timestamp - q.camera_item.timestamp)
            < _RANK_SPREAD_MIN_GAP
            for q in leading
        )
        if too_close:
            rest.append(p)
        else:
            leading.append(p)
    while len(leading) < cards and rest:
        leading.append(rest.pop(0))
    return leading + rest
