"""Per-camera clock-offset calibration for the Reconcile workflow.

Multi-camera trips taken before the user discovered "sync clocks before
the trip" produce photos whose ``DateTimeOriginal`` is wrong by a
constant offset (camera was set up months ago, drifted) and sometimes
also drifts further during the trip (cheap clocks lose seconds per day).
The Reconcile pre-processing pipeline corrects this so downstream
day-routing and chronological ordering work as if the user had synced
correctly.

Calibration model
-----------------

The user provides one or more **pairs** per camera: a photo from the
camera plus a photo from a designated reference (the user's phone, which
auto-syncs via NTP and is therefore the trip's "true clock"). Both
photos must depict the same moment — typically a quick double-shot of
the same scene. The offset for that pair is

    offset = reference_time - camera_time

If the camera were perfectly stable, one pair would suffice for the
whole trip. In practice cheap clocks drift, so:

* **1 pair** → constant offset (best we can do — assume no drift)
* **2 pairs** → linear interpolation between the two pairs by camera
  time. Photos before the first pair use the first pair's offset;
  photos after the last use the last pair's offset (no extrapolation
  to avoid amplifying noise).
* **3+ pairs** → same linear-segments shape, but with simple outlier
  rejection: any pair whose offset deviates from the median pair offset
  by more than a configurable threshold (default 5 minutes) is dropped
  with a warning. Rare in well-constructed trips, but protects against
  a paired photo where the user accidentally captured the wrong
  reference shot.

The class is Qt-free; the UI hands it parsed pairs and reads back the
correction function.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


_DEFAULT_OUTLIER_THRESHOLD = timedelta(minutes=5)


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
        """How far ahead the reference is from the camera. Positive
        means the camera's clock is BEHIND reference; subtract this
        from camera time? No — ADD this offset to camera time to get
        reference time:

            reference_time = camera_time + offset
        """
        return self.reference_time - self.camera_time


@dataclass
class CameraCalibration:
    """Calibration for one camera, derived from pair(s), a TZ
    declaration, or both.

    ``camera_id`` is the user-facing identifier (e.g. ``"Lumix G9 II"``
    or ``"celular_nelson"``) — typically the source subfolder name in
    the reconcile input layout.

    Three sources of offset, used in order of priority:

    1. ``pairs`` (1+) — measured from photo pairs. Most precise; can
       capture drift when 2+ pairs span the trip. ``rejected_pairs``
       holds outliers excluded by ``build_calibration``.
    2. ``tz_offset`` — derived from a TZ declaration
       (``trip_tz - camera_configured_tz``). Constant; used as a
       fallback when no pairs were provided.
    3. (no source) — caller must skip the camera or pass photos
       through uncorrected.

    ``warnings`` carries human-readable diagnostics from
    ``build_calibration`` — e.g. when the measured pair offset
    disagrees with the TZ-derived expectation by more than 5 minutes,
    suggesting either a mistaken TZ declaration or a poorly chosen
    pair. The pipeline surfaces these to the user.
    """
    camera_id: str
    pairs: list[CalibrationPair] = field(default_factory=list)
    rejected_pairs: list[CalibrationPair] = field(default_factory=list)
    tz_offset: Optional[timedelta] = None
    warnings: list[str] = field(default_factory=list)

    @property
    def has_drift_correction(self) -> bool:
        """True iff the calibration interpolates between two or more
        pairs in time. Single-pair and TZ-only calibrations apply a
        constant offset and don't model drift."""
        return len(self.pairs) >= 2

    @property
    def has_any_source(self) -> bool:
        """Whether the calibration has anything to compute an offset
        with — either pairs or a TZ declaration. Pipelines check this
        before calling ``offset_at`` / ``correct``."""
        return bool(self.pairs) or self.tz_offset is not None

    def offset_at(self, camera_time: datetime) -> timedelta:
        """Return the offset to apply to ``camera_time`` to bring it
        onto reference time.

        Resolution order:

        * If ``pairs`` are present, linearly interpolate between them
          (clamped to endpoints — no extrapolation).
        * Else if ``tz_offset`` is set, return it constant for any
          time.
        * Else raise ``ValueError`` — caller programming error.
        """
        if not self.pairs:
            if self.tz_offset is not None:
                return self.tz_offset
            raise ValueError(
                f"camera {self.camera_id!r} has no calibration source "
                f"(pairs nor TZ); cannot compute offset"
            )
        # Sort by camera time so before/after lookups are deterministic.
        # Cached on first call to avoid re-sorting on every photo.
        sorted_pairs = sorted(self.pairs, key=lambda p: p.camera_time)
        if camera_time <= sorted_pairs[0].camera_time:
            return sorted_pairs[0].offset
        if camera_time >= sorted_pairs[-1].camera_time:
            return sorted_pairs[-1].offset
        # Find the bracketing pair indices and interpolate linearly.
        for i in range(len(sorted_pairs) - 1):
            left = sorted_pairs[i]
            right = sorted_pairs[i + 1]
            if left.camera_time <= camera_time <= right.camera_time:
                span = (right.camera_time - left.camera_time).total_seconds()
                if span <= 0:
                    # Two pairs at exactly the same camera time —
                    # fall back to averaging their offsets to avoid
                    # divide-by-zero. Rare edge case (user drops two
                    # different reference photos against the same
                    # camera shot).
                    avg = (left.offset + right.offset) / 2
                    return avg
                t = (camera_time - left.camera_time).total_seconds() / span
                # Linear interpolation between two timedeltas.
                left_s = left.offset.total_seconds()
                right_s = right.offset.total_seconds()
                interp_s = left_s + t * (right_s - left_s)
                return timedelta(seconds=interp_s)
        # Defensive fallback — shouldn't reach here if logic above
        # is correct, but if it does, return the closest pair's offset.
        log.warning(
            "offset_at fell through for %s at %s; using nearest pair",
            self.camera_id, camera_time,
        )
        nearest = min(
            sorted_pairs,
            key=lambda p: abs((p.camera_time - camera_time).total_seconds()),
        )
        return nearest.offset

    def correct(self, camera_time: datetime) -> datetime:
        """Apply the calibration to a camera-recorded timestamp.
        Convenience wrapper around ``offset_at``."""
        return camera_time + self.offset_at(camera_time)


def build_calibration(
    camera_id: str,
    pairs: list[CalibrationPair],
    *,
    configured_tz: Optional[float] = None,
    trip_tz: Optional[float] = None,
    outlier_threshold: timedelta = _DEFAULT_OUTLIER_THRESHOLD,
) -> CameraCalibration:
    """Construct a ``CameraCalibration`` from pairs and/or a TZ
    declaration. Both inputs are optional but at least one is needed
    for the calibration to produce offsets later.

    **TZ-derived offset** (when ``configured_tz`` and ``trip_tz`` are
    both given): the constant offset is ``trip_tz - configured_tz``
    in hours. Example: G9 set to São Paulo (-3) shooting in Nepal
    (+5.75) → expected offset of +8.75h. This is recorded as
    ``tz_offset`` on the calibration. When pairs are also provided,
    the TZ offset is used only as a sanity check (see below).

    **Pair-derived offset** (from ``pairs``): same as the previous
    behavior — linear interpolation between accepted pairs, with
    median-based outlier rejection when 3+ pairs are given.

    **Cross-check** when both sources are present: compute the median
    pair-measured offset, compare to the TZ-derived value. If they
    disagree by more than 5 minutes, emit a warning; the pair value
    still wins (it's empirical), but the user should double-check
    that either the TZ declaration or the pairs are correct.

    Pairs with non-positive offsets (the reference is BEHIND the
    camera) are accepted — that's the normal case when a camera was
    configured later than the phone, or the phone TZ was miscopied.
    """
    tz_offset: Optional[timedelta] = None
    warnings: list[str] = []
    if configured_tz is not None and trip_tz is not None:
        tz_offset = timedelta(hours=trip_tz - configured_tz)

    if not pairs:
        # TZ-only or fully-empty calibration.
        return CameraCalibration(
            camera_id=camera_id, pairs=[], rejected_pairs=[],
            tz_offset=tz_offset, warnings=warnings,
        )

    # Outlier rejection on pairs (unchanged from previous version).
    accepted: list[CalibrationPair] = list(pairs)
    rejected: list[CalibrationPair] = []
    if len(pairs) >= 3:
        offsets = sorted(p.offset.total_seconds() for p in pairs)
        median_s = offsets[len(offsets) // 2]
        threshold_s = outlier_threshold.total_seconds()
        accepted = []
        for p in pairs:
            if abs(p.offset.total_seconds() - median_s) > threshold_s:
                log.info(
                    "calibration outlier rejected for %s: pair %s "
                    "(offset %.1fs vs median %.1fs)",
                    camera_id, p.camera_path.name,
                    p.offset.total_seconds(), median_s,
                )
                rejected.append(p)
            else:
                accepted.append(p)

    if not accepted:
        log.warning(
            "all %d calibration pairs for %s rejected as outliers; "
            "keeping the one closest to median",
            len(pairs), camera_id,
        )
        offsets = sorted(p.offset.total_seconds() for p in pairs)
        median_s = offsets[len(offsets) // 2]
        closest = min(
            pairs,
            key=lambda p: abs(p.offset.total_seconds() - median_s),
        )
        accepted = [closest]
        rejected = [p for p in pairs if p is not closest]

    # Cross-check pair offset against TZ-derived expectation.
    if tz_offset is not None and accepted:
        accepted_secs = sorted(p.offset.total_seconds() for p in accepted)
        median_pair_s = accepted_secs[len(accepted_secs) // 2]
        tz_s = tz_offset.total_seconds()
        diff_s = abs(median_pair_s - tz_s)
        if diff_s > _DEFAULT_OUTLIER_THRESHOLD.total_seconds():
            warnings.append(
                f"camera {camera_id!r}: measured pair offset "
                f"({median_pair_s:+.0f}s) disagrees with TZ-derived "
                f"expectation ({tz_s:+.0f}s) by {diff_s:.0f}s. "
                f"Verify your TZ declaration or that the pair photos "
                f"are truly concurrent."
            )

    return CameraCalibration(
        camera_id=camera_id, pairs=accepted, rejected_pairs=rejected,
        tz_offset=tz_offset, warnings=warnings,
    )


def correct_camera_time(
    camera_time: datetime,
    calibration: Optional[CameraCalibration],
) -> datetime:
    """Top-level helper for callers that want a clean
    ``corrected = correct_camera_time(raw, calibration)`` line.

    ``calibration=None`` is a no-op — useful for the reference camera
    itself (which doesn't need correction) or for cameras the user
    didn't calibrate (left as-is, with a caller-side warning).
    """
    if calibration is None:
        return camera_time
    return calibration.correct(camera_time)


# ── Pair-picker snap heuristic (docs/03 §"Scope expansion #2") ───
#
# The Create-from-Past-Photos surface (the pair-picker UI) shows the
# raw offset a ``CalibrationPair`` derives, then proposes the obvious
# TZ-like value next to it. Real-world TZ offsets are quarter-hour
# multiples: every whole hour −12…+14 plus :30 (India +5:30,
# Newfoundland −3:30, etc.) and :45 (Nepal +5:45, Chatham +12:45).
# Snapping to the nearest 15-minute multiple matches all of them.


def snap_to_tz_offset(raw: timedelta) -> timedelta:
    """Snap ``raw`` to the nearest 15-minute multiple — the granularity
    of real-world UTC offsets. e.g. ``00:42:00 → 00:45:00`` (Nepal-ish);
    ``05:03:00 → 05:00:00`` (whole hour); ``-02:58:30 → -03:00:00``.
    Pure; no clamping (so a wildly-off pair returns a wildly-off snap,
    which is on purpose — the caller / UI surfaces both raw and snap
    so the user can see the snap was junk and reject it)."""
    minutes = raw.total_seconds() / 60.0
    snapped = round(minutes / 15.0) * 15
    return timedelta(minutes=snapped)


def snap_disagreement(raw: timedelta, snapped: timedelta) -> timedelta:
    """Absolute distance between a raw pair-derived offset and its
    TZ-snap. Useful for the UI to flag a suspicious pair: if the snap
    moved by more than a few minutes the pair photos probably weren't
    really simultaneous (someone clicked the wrong photo)."""
    return abs(raw - snapped)
