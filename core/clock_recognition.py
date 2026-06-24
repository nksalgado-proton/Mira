"""Camera-clock calibration by recognition — candidate generator (spec/88).

Replaces the front end of the "I don't know my camera's TZ" flow. Instead of
asking the user to *construct* a sync pair (which mis-fires when two photos
only feel simultaneous — Nelson 2026-06-18: a pair that felt simultaneous was
~an hour apart, silently mis-dating a whole camera's photos), the app
*proposes* candidate pairs and the user *recognizes* one.

spec/123 update: the **applied offset is the raw measured delta** —
``find_candidate_pairs`` may still *present* near-simultaneous candidates
to help the user choose, but the value the calibration consumes is the
raw ``reference_time − camera_time`` (rounded to the second, no
snapping). Clustering on the (whole-minute-rounded) κ still helps the
user spot dominant set-TZ piles in the UI, but the value the engine
applies is never snapped to a 15-minute grid.

Algorithm. For every (camera item ``c``, phone item ``p``) within the trip:

1. Compute ``off = Tp − Tc`` (what a :class:`CalibrationPair` carries)
   and normalize to the camera's *constant* set-TZ::

       κ = phone_tz(p) − off   # the camera's set TZ, constant across the trip

   ``phone_tz(p)`` is the phone's UTC offset on ``p``'s day. The default
   source is ``p.tz_offset_minutes`` (the per-photo EXIF
   ``OffsetTimeOriginal`` value spec/45 already extracts); callers with
   a per-day map can override via the ``phone_tz_for`` callback.

2. Pairs more than :data:`MAX_PAIR_RAW_DELTA` apart by clock are
   filtered out — humans can't recognize "the same moment" across that
   gap regardless of the math (Nelson 2026-06-18 hard rule).

3. Cluster surviving pairs by κ rounded to the nearest minute — the
   dominant pile suggests the camera's set-TZ but does NOT constrain
   the applied offset. When the user picks a pair, the calibration
   consumes the raw ``reference_time − camera_time`` verbatim.

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


# Half of the 15-min snap step. Pairs whose implied κ is farther than
# this from any 15-min multiple are not plausibly simultaneous and are
# dropped (clustering-only filter; spec/123 keeps the snap for the
# recognition UI's "same suggested zone" piles — the offset the
# calibration applies is still the raw measured delta of whichever
# pair the user picks).
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
    """One (camera_item, phone_item) pair plausibly simultaneous —
    surfaced for the user to recognize. ``raw_kappa_minutes`` carries
    the implied set-TZ (for display); ``cluster_kappa_minutes`` is the
    nearest-minute bucket the pair fell into (for grouping in the UI).
    spec/123: no value here is ever snapped — the applied offset is the
    raw measured delta via :meth:`to_calibration_pair`."""

    camera_item: SourceItem
    phone_item: SourceItem
    phone_tz_minutes: int
    raw_kappa_minutes: float
    cluster_kappa_minutes: int
    tightness: timedelta

    @property
    def snapped_kappa_minutes(self) -> int:
        """Compatibility alias — the legacy name was ``snapped``;
        spec/123 renamed it to ``cluster`` because the value is no
        longer a 15-minute snap but a whole-minute group key."""
        return self.cluster_kappa_minutes

    def to_calibration_pair(self) -> CalibrationPair:
        """Build the :class:`CalibrationPair` the engine consumes from
        this pair's *raw* EXIF timestamps — the applied offset is the
        raw measured delta (spec/123: no snapping)."""
        return CalibrationPair(
            camera_path=self.camera_item.path,
            reference_path=self.phone_item.path,
            camera_time=self.camera_item.timestamp,
            reference_time=self.phone_item.timestamp,
        )


@dataclass(frozen=True)
class CandidateCluster:
    """Candidate pairs sharing a whole-minute κ bucket. The cluster is
    a UI grouping hint; the applied offset stays the raw measured
    delta of whichever pair the user picks."""

    cluster_kappa_minutes: int
    pairs: Tuple[CandidatePair, ...]

    @property
    def snapped_kappa_minutes(self) -> int:
        """Compat alias — see :attr:`CandidatePair.snapped_kappa_minutes`."""
        return self.cluster_kappa_minutes

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
    """Generate and cluster candidate sync pairs (spec/123 — no
    snapping anywhere; the cluster key is the κ rounded to the nearest
    minute, used only for grouping).

    ``phone_tz_for(p)`` returns the phone's UTC offset (minutes east)
    on ``p``'s day. Defaults to ``p.tz_offset_minutes`` (the per-photo
    EXIF ``OffsetTimeOriginal`` spec/45 already extracts).

    Items with missing timestamps are skipped on both sides. Pairs
    more than :data:`MAX_PAIR_RAW_DELTA` apart by clock are filtered.
    Clusters are sorted by size descending; ties break by tightest
    top-pair, then by κ closest to zero.
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
            if abs(off) > MAX_PAIR_RAW_DELTA:
                filtered_too_far += 1
                continue
            raw_kappa_minutes = ptz - (off.total_seconds() / 60.0)
            raw_kappa_td = timedelta(minutes=raw_kappa_minutes)
            # Cluster on the 15-min snap (presentation only — the
            # offset the calibration eventually applies is the raw
            # measured delta via ``to_calibration_pair``).
            snapped_kappa_td = snap_to_tz_offset(raw_kappa_td)
            tightness = snap_disagreement(raw_kappa_td, snapped_kappa_td)
            if tightness >= tolerance:
                continue
            cluster_kappa_minutes = int(round(
                snapped_kappa_td.total_seconds() / 60.0))
            buckets.setdefault(cluster_kappa_minutes, []).append(
                CandidatePair(
                    camera_item=c,
                    phone_item=p,
                    phone_tz_minutes=ptz,
                    raw_kappa_minutes=raw_kappa_minutes,
                    cluster_kappa_minutes=cluster_kappa_minutes,
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
            cluster_kappa_minutes=kappa,
            pairs=tuple(ranked),
        ))
    # Largest cluster first; ties → tightest top-pair, then κ closest
    # to 0 (the correctly-set-camera case wins ties).
    clusters.sort(key=lambda c: (
        -c.size,
        c.pairs[0].tightness,
        abs(c.cluster_kappa_minutes),
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
