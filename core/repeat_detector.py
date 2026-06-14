"""Repeat sequence detector — spec/52 Quick Sweep redesign.

A **repeat** is a chronological run of two or more photos where every
consecutive gap is small enough that the user almost certainly fired the
shutter twice (or more) at the same subject. It catches the cell-phone
"tap-twice-just-in-case" pattern and the camera "shutter doublet" — runs
that the bracket and burst detectors deliberately ignore because:

* brackets require explicit EXIF bracket tags or constant-parameter
  variation patterns;
* camera bursts require ``continuous_shooting_active`` (DriveMode /
  ShootingMode / ReleaseMode / ContinuousDrive set to a continuous value);
* phone bursts require an explicit ``BurstUUID`` from iOS.

A repeat is what's left over: tight temporal spacing with none of those
hard signals. The detector therefore operates on the *individuals* the
scanner produced after brackets and bursts have already been claimed —
the caller pre-filters and hands the leftover candidates here.

Pure logic, mirrors the shape of :mod:`core.bracket_detector` — same
``Candidate`` / ``Sequence`` / ``Config`` triad, same module-level
``detect_*`` entry point, no Qt, no I/O.

Spec/52 Quick Sweep redesign (Nelson 2026-06-09 design session):

* Default window — **2.0 s total span** between the first and last photo
  in the run (NOT consecutive-gap). Mirrors the scanner's
  :func:`core.bucket_scanner.annotate_clusters` grouping — a 10-frame
  chain whose consecutive gaps are all 1 s but whose span is 9 s does
  **not** form a single repeat; it fragments at the 2 s span boundary.
  Tightened from 5 s on Nelson eyeball 2026-06-09: most genuine "tap-
  twice" doublets fire within a second, and the looser 5 s window was
  pulling in unrelated frames that happened to be nearby.
* Default minimum sequence length — **2** frames. The whole point is to
  catch doublets the burst rule (min 3) drops on the floor.
* Both thresholds live as constants here with room to promote them to
  user-tunable Settings later (spec/52's "threshold … with room to
  promote to a user-tunable Setting").
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

log = logging.getLogger(__name__)


# Defaults — promotable to user-tunable Settings later (spec/52 §Quick Sweep).
DEFAULT_REPEAT_WINDOW_SECONDS = 2.0
DEFAULT_REPEAT_MIN_SEQUENCE_LENGTH = 2


@dataclass(frozen=True)
class RepeatCandidate:
    """A single photo expressed in the shape the repeat detector needs.

    Only ``path`` and ``timestamp`` participate in detection — the
    detector's whole signal is consecutive-time-gap. Candidates without a
    timestamp can't participate in a run; the detector skips them and
    the caller treats them as orphans (like the bracket detector does).
    """

    path: Path
    timestamp: Optional[datetime]


@dataclass(frozen=True)
class RepeatSequence:
    """A detected repeat — two or more photos with every consecutive gap
    within ``window_seconds``."""

    repeat_id: str
    photos: tuple[Path, ...]
    representative_timestamp: Optional[datetime]

    @property
    def photo_count(self) -> int:
        return len(self.photos)


@dataclass(frozen=True)
class RepeatDetectorConfig:
    """Detector thresholds. Defaults match spec/52's Quick Sweep design."""

    window_seconds: float = DEFAULT_REPEAT_WINDOW_SECONDS
    min_sequence_length: int = DEFAULT_REPEAT_MIN_SEQUENCE_LENGTH


def detect_repeats(
    candidates: Sequence[RepeatCandidate],
    config: Optional[RepeatDetectorConfig] = None,
) -> list[RepeatSequence]:
    """Find chronological runs whose **total span** (last timestamp
    minus first timestamp) is within ``config.window_seconds``.

    Greedy span-based grouping (mirrors :func:`core.bucket_scanner.
    annotate_clusters` — Nelson 2026-06-09: "5 seconds between first
    and last, NOT 5 seconds between each"): walk sorted-by-timestamp;
    while adding the next photo keeps ``last - first <= window``,
    accept it; otherwise close the current run and start a new one.

    The caller is responsible for excluding photos already claimed by a
    bracket or burst — pass the leftover *individuals* the bucket
    scanner produced. Candidates without a timestamp can't participate
    in a run; they are silently skipped (the cluster classifier treats
    them as ``kind='none'``).

    Sorting is done internally so the caller doesn't have to. Runs
    shorter than ``min_sequence_length`` are dropped.
    """
    cfg = config or RepeatDetectorConfig()

    with_ts = [c for c in candidates if c.timestamp is not None]
    if len(with_ts) < cfg.min_sequence_length:
        return []
    with_ts.sort(key=lambda c: c.timestamp)  # type: ignore[arg-type,return-value]

    sequences: list[RepeatSequence] = []
    run: list[RepeatCandidate] = [with_ts[0]]

    def _close() -> None:
        if len(run) >= cfg.min_sequence_length:
            sequences.append(
                RepeatSequence(
                    repeat_id=str(uuid.uuid4()),
                    photos=tuple(c.path for c in run),
                    representative_timestamp=run[0].timestamp,
                )
            )

    for candidate in with_ts[1:]:
        first = run[0]
        span = (candidate.timestamp - first.timestamp).total_seconds()  # type: ignore[operator]
        if span <= cfg.window_seconds:
            run.append(candidate)
        else:
            _close()
            run = [candidate]
    _close()

    log.debug(
        "detect_repeats: %d sequence(s) covering %d photo(s) from %d candidates",
        len(sequences),
        sum(s.photo_count for s in sequences),
        len(candidates),
    )
    return sequences
