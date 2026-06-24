"""Per-camera clock-offset calibration (spec/123).

The model collapsed into **one offset_seconds per camera** derived from
exactly three sources:

1. **Known TZ** — the user states which zone the camera's clock was on.
   ``offset_seconds = trip_tz_seconds − camera_tz_seconds``. Zones are
   whole minutes, so this is always a whole-minute integer-seconds value.
   Nepal GoPro: ``+5:45 − (−3:00) = +8:45 = +31 500 s``.

2. **Recognized "these two were the same moment, clock was right"** —
   the user confirms a pair is simultaneous AND the camera needed no
   shift. ``offset_seconds = 0``.

3. **Measured pair** — the user picks a pair (one camera shot + one
   reference shot) they know depicts the same moment. ``offset_seconds
   = round((reference_time − camera_time).total_seconds())`` — the raw
   measured delta, to the nearest second, with **no snapping**. The
   pair *is* the measurement; snapping (the spec/101 model) substitutes
   the assumption "the error must be a clean zone", which is false in
   general — the Nepal pair (5h00m02s measured) is in no zone at all
   from Kathmandu.

Sources 1 and 3 are two ways to derive the same kind of number; source
2 is the zero case. Nothing infers a zone from a measured delta;
nothing snaps. The result flows through one apply path —
``EventGateway.recompute_corrected_times(offset_seconds=…)`` — that
re-derives ``capture_time_corrected`` for every captured item of the
camera (photos AND videos).

Qt-free; the UI hands in either a TZ pair (source 1) or a measured
pair (source 3) and reads the offset_seconds back.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class CalibrationPair:
    """One (camera, reference) pair contributing to a camera's
    calibration. Both timestamps are *as recorded by each device's
    clock* — no correction yet applied. The pair's correction is the
    delta needed to bring the camera onto reference time."""
    camera_path: Path
    reference_path: Path
    camera_time: datetime
    reference_time: datetime

    @property
    def offset(self) -> timedelta:
        """How far ahead the reference is from the camera. ADD this
        offset to camera time to get reference time:

            reference_time = camera_time + offset
        """
        return self.reference_time - self.camera_time


@dataclass
class CameraCalibration:
    """One camera's resolved offset (spec/123 — single integer-seconds
    value, from one of three sources). ``offset_seconds`` is the
    canonical field; ``tz_offset`` is kept as a compatibility view for
    callers that still expect a :class:`timedelta`.

    ``pairs`` is kept for the engine's per-segment interpolation path
    (Reconcile multi-pair calibration); empty for the simple TZ /
    measured-pair source. ``rejected_pairs`` / ``warnings`` are
    preserved for diagnostics surfaces but no longer flow through the
    snap path."""
    camera_id: str
    offset_seconds: Optional[int] = None
    pairs: list[CalibrationPair] = field(default_factory=list)
    rejected_pairs: list[CalibrationPair] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def tz_offset(self) -> Optional[timedelta]:
        """Compatibility view — callers that read the resolved offset
        as a :class:`timedelta` keep working."""
        if self.offset_seconds is None:
            return None
        return timedelta(seconds=self.offset_seconds)

    @property
    def has_drift_correction(self) -> bool:
        """True iff the calibration interpolates between two or more
        pairs in time (the Reconcile multi-pair case). Constant-offset
        calibrations (sources 1/2/3) don't model drift."""
        return len(self.pairs) >= 2

    @property
    def has_any_source(self) -> bool:
        """Whether the calibration has anything to compute an offset
        with."""
        return self.offset_seconds is not None or bool(self.pairs)

    def offset_at(self, camera_time: datetime) -> timedelta:
        """Return the offset to apply to ``camera_time`` to bring it
        onto reference time. Constant ``offset_seconds`` wins; only
        falls through to per-pair interpolation if no constant is set
        but pairs exist (legacy Reconcile multi-pair path)."""
        if self.offset_seconds is not None and not self.pairs:
            return timedelta(seconds=self.offset_seconds)
        if not self.pairs:
            if self.offset_seconds is not None:
                return timedelta(seconds=self.offset_seconds)
            raise ValueError(
                f"camera {self.camera_id!r} has no calibration source"
            )
        # Multi-pair drift interpolation (Reconcile path) — kept for
        # callers that still construct calibrations from pair lists.
        sorted_pairs = sorted(self.pairs, key=lambda p: p.camera_time)
        if camera_time <= sorted_pairs[0].camera_time:
            return sorted_pairs[0].offset
        if camera_time >= sorted_pairs[-1].camera_time:
            return sorted_pairs[-1].offset
        for i in range(len(sorted_pairs) - 1):
            left = sorted_pairs[i]
            right = sorted_pairs[i + 1]
            if left.camera_time <= camera_time <= right.camera_time:
                span = (right.camera_time - left.camera_time).total_seconds()
                if span <= 0:
                    avg = (left.offset + right.offset) / 2
                    return avg
                t = (camera_time - left.camera_time).total_seconds() / span
                left_s = left.offset.total_seconds()
                right_s = right.offset.total_seconds()
                interp_s = left_s + t * (right_s - left_s)
                return timedelta(seconds=interp_s)
        nearest = min(
            sorted_pairs,
            key=lambda p: abs((p.camera_time - camera_time).total_seconds()),
        )
        return nearest.offset

    def correct(self, camera_time: datetime) -> datetime:
        """Apply the calibration to a camera-recorded timestamp."""
        return camera_time + self.offset_at(camera_time)


# ── Three explicit sources (spec/123 §1) ──────────────────────────────


def offset_from_known_tz(
    *, trip_tz_seconds: int, camera_tz_seconds: int,
) -> int:
    """Source 1 — the user states which zone the camera's clock was on.
    Both inputs are integer SECONDS east of UTC (zones are whole
    minutes, so always a multiple of 60). Returns the integer-seconds
    offset to ADD to a camera time to land on trip-local time.

    Nepal GoPro: ``trip_tz_seconds=+20700`` (+5:45),
    ``camera_tz_seconds=-10800`` (−3:00) → ``+31500`` s (+8:45)."""
    return int(trip_tz_seconds) - int(camera_tz_seconds)


def offset_from_simultaneous() -> int:
    """Source 2 — the user confirmed a pair is simultaneous and the
    camera needed no shift. Always 0."""
    return 0


def offset_from_measured_pair(pair: CalibrationPair) -> int:
    """Source 3 — the measured delta, applied raw to the nearest
    second. NO snapping. The Nepal pair (5h00m02s) yields 18 002 s,
    not 18 000 (snapped 5:00) and not 17 100 (snapped 4:45)."""
    return int(round(pair.offset.total_seconds()))


# ── Builders (UI seam) ────────────────────────────────────────────────


def build_calibration_from_known_tz(
    camera_id: str, *, trip_tz_seconds: int, camera_tz_seconds: int,
) -> CameraCalibration:
    """Source 1 → CameraCalibration."""
    return CameraCalibration(
        camera_id=camera_id,
        offset_seconds=offset_from_known_tz(
            trip_tz_seconds=trip_tz_seconds,
            camera_tz_seconds=camera_tz_seconds,
        ),
    )


def build_calibration_simultaneous(camera_id: str) -> CameraCalibration:
    """Source 2 → CameraCalibration (offset_seconds=0)."""
    return CameraCalibration(
        camera_id=camera_id, offset_seconds=0)


def build_calibration_from_pair(
    camera_id: str, pair: CalibrationPair,
) -> CameraCalibration:
    """Source 3 → CameraCalibration. Stores the raw measured delta as
    the single constant offset; the pair is also kept on
    ``pairs`` so the diagnostics surfaces (e.g. "which photos did the
    user pick?") still resolve."""
    return CameraCalibration(
        camera_id=camera_id,
        offset_seconds=offset_from_measured_pair(pair),
        pairs=[pair],
    )


def build_calibration(
    camera_id: str,
    pairs: list[CalibrationPair],
    *,
    configured_tz: Optional[float] = None,
    trip_tz: Optional[float] = None,
) -> CameraCalibration:
    """Legacy multi-source builder kept for the Reconcile pipeline.

    spec/123 collapsed the applied path to one of three sources; this
    function still exists so :mod:`mira.ingest.plan` /
    :mod:`core.reconcile_pipeline` can keep producing a calibration
    from "pairs + optional TZ declaration". The math:

    * If ``configured_tz`` and ``trip_tz`` are both given, the
      TZ-derived constant offset is ``(trip_tz − configured_tz) × 3600``
      seconds (hours → seconds, whole-minute precision preserved).
    * Pairs, when present, override the TZ offset with the raw
      measured delta of the median pair (no snapping — spec/123). The
      multi-pair drift interpolation path on ``CameraCalibration``
      stays available for callers that want it.
    """
    offset_seconds: Optional[int] = None
    if configured_tz is not None and trip_tz is not None:
        # Whole-minute zones → seconds. Tolerance: callers that still
        # pass float hours retain the same numbers (×3600 is exact for
        # whole-quarter-hour values).
        offset_seconds = int(round((float(trip_tz) - float(configured_tz)) * 3600))

    if pairs:
        # Source 3 — take the median pair's raw delta (the legacy
        # multi-pair outlier reject path is moot here; v1 keeps a
        # simple median that survives the spec/123 "no snap" rule).
        deltas = sorted(int(round(p.offset.total_seconds())) for p in pairs)
        offset_seconds = deltas[len(deltas) // 2]

    return CameraCalibration(
        camera_id=camera_id,
        offset_seconds=offset_seconds,
        pairs=list(pairs),
    )


def correct_camera_time(
    camera_time: datetime,
    calibration: Optional[CameraCalibration],
) -> datetime:
    """Top-level helper for callers that want a clean
    ``corrected = correct_camera_time(raw, calibration)`` line.

    ``calibration=None`` is a no-op."""
    if calibration is None:
        return camera_time
    return calibration.correct(camera_time)


# ── Recognition-only helpers (PRESENTATION, not applied path) ─────────
#
# spec/123 keeps the applied offset as the RAW measured delta. These
# helpers exist solely so :mod:`core.clock_recognition` can group
# candidate pairs by inferred zone in the recognition UI ("these all
# suggest +5:45"). They never touch the offset that flows into the
# calibration: :meth:`CandidatePair.to_calibration_pair` returns raw
# timestamps; ``build_calibration_from_pair`` rounds the raw delta to
# the second.


def snap_to_tz_offset(raw: timedelta) -> timedelta:
    """Snap ``raw`` to the nearest 15-minute multiple — the granularity
    of real-world UTC offsets. Used by the recognition front end ONLY,
    for clustering candidate pairs into "same suggested zone" piles
    (spec/123 §2 — presentation, not applied path). Pure; no clamping."""
    minutes = raw.total_seconds() / 60.0
    snapped = round(minutes / 15.0) * 15
    return timedelta(minutes=snapped)


def snap_disagreement(raw: timedelta, snapped: timedelta) -> timedelta:
    """Absolute distance between a raw pair-derived offset and its
    TZ-snap. Used by the recognition front end to filter pairs that
    aren't plausibly simultaneous before clustering. Never feeds the
    applied path (spec/123)."""
    return abs(raw - snapped)
