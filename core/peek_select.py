"""Browse-peek thumbnail selection — spec/52 §5.6 (slice D.1.a).

The Plan dialog's per-row Browse button opens a read-only peek of that day's
photos. This module produces the curated subset shown in the peek. Pure
Python — no Qt — so the algorithm is independently testable and the UI
layer (slice D.1.b) just needs a list of paths to render.

Selection rules (spec/52 §5.6):

* **Target ~20 photos per day.** Configurable via ``target`` parameter so
  the UI can scale to its grid size; spec default is 20 (matches a 6×4
  grid with a couple of empty slots).
* **Time-spread.** Pick photos across the day's time range so the peek
  shows an arc (morning / midday / evening), not 20 clustered at
  breakfast. Implemented as equal-time-bucket sampling — each of the N
  buckets contributes the photo whose timestamp is closest to its
  center.
* **Skip videos.** Videos are heavy to preview and the peek is meant to
  be a fast first impression — the user can still see the count in the
  caller's "(N videos hidden)" hint.
* **Skip huge files.** Files above ``max_bytes`` (default 40 MB) are
  skipped — a single big RAW takes several seconds to decode and would
  block the peek dialog open.
* **JPEG / HEIC preferred over RAW for sibling pairs.** When a camera
  shoots RAW+JPEG, both files share the same stem in the same directory.
  Dedup by ``(parent, stem)`` keeping the non-RAW; the JPEG decodes
  ~100× faster than RAW.
* **Fewer than ``target`` after filtering → return all.** Don't pad,
  don't repeat; show what's there.
* **Empty peek.** When the day has only videos / huge files / nothing
  decodable, returns ``[]``. The dialog renders a short "(no
  preview-able photos)" label in that case.

The output preserves chronological order so the peek reads
morning-to-evening top-left to bottom-right.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Sequence

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Input shape — the minimum the selector needs per candidate file
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PeekCandidate:
    """One file from the day's scan + its peek-relevant metadata.

    The host constructs these from whatever scan output it has
    (typically :class:`core.exif_reader.PhotoExif`, but the selector
    doesn't care). ``timestamp`` carries the capture time used for
    time-spread — pass the corrected-time projection (per the per-
    ``(camera, day)`` offset) so the spread reflects what the user
    will perceive, not raw EXIF.
    """

    path: Path
    timestamp: Optional[datetime] = None
    is_video: bool = False
    byte_size: int = 0


# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #

#: spec/52 §5.6 default — a 6×4 grid with a couple of empty slots feels
#: airier than packing 24.
DEFAULT_TARGET = 20

#: Photos taken within this many seconds of each other collapse to one
#: "moment" (Nelson 2026-06-08 — phones routinely capture the same shot
#: twice when the user taps again "just to be sure"; bursts of 5-10
#: frames at one event are also the norm). 15 s catches both without
#: killing a slow-walking continuous shoot (Nelson's typical day has
#: ≥ 30 s between distinct compositions).
DEFAULT_MIN_SECONDS_BETWEEN = 15

#: Files above this size are skipped (RAWs from modern bodies routinely run
#: 25-35 MB; 40 MB catches the biggest medium-format + lets through normal
#: Sony/Canon RAWs in case the JPEG sibling is absent).
DEFAULT_MAX_BYTES = 40 * 1024 * 1024

#: Extensions treated as RAW for sibling-dedup. Sourced from
#: :data:`core.photo_decoder.RAW_EXTENSIONS` but imported lazily so this
#: module stays Qt-free and import-cheap. Kept in sync via the test
#: ``test_raw_extension_set_matches_photo_decoder``.
_RAW_EXTS = frozenset({
    ".rw2", ".nef", ".nrw", ".cr2", ".cr3", ".crw",
    ".arw", ".srf", ".sr2", ".raf", ".orf", ".ori",
    ".pef", ".ptx", ".rwl", ".dng",
})


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def select_for_peek(
    candidates: Sequence[PeekCandidate],
    *,
    target: int = DEFAULT_TARGET,
    max_bytes: int = DEFAULT_MAX_BYTES,
    min_seconds_between: float = DEFAULT_MIN_SECONDS_BETWEEN,
) -> List[PeekCandidate]:
    """Pick the curated subset to show in the Browse peek dialog.

    Returns a list of at most ``target`` candidates in chronological
    order. Empty when every candidate was filtered out (only videos /
    huge files / unsupported).

    The selection is deterministic — same input → same output — so the
    peek doesn't shuffle between opens.

    ``min_seconds_between`` collapses near-duplicate moments (Nelson
    2026-06-08) — phones routinely capture the same shot twice when the
    user re-taps. A cluster of photos whose consecutive gaps are all
    below this threshold contributes a single representative.
    """
    if target <= 0 or not candidates:
        return []

    eligible = [
        c for c in candidates
        if _passes_filter(c, max_bytes=max_bytes)
    ]
    if not eligible:
        return []

    eligible = _dedup_raw_jpeg_pairs(eligible)
    eligible = _collapse_near_duplicates(
        eligible, window_seconds=min_seconds_between,
    )
    if len(eligible) <= target:
        return _sorted_chronologically(eligible)

    with_ts = [c for c in eligible if c.timestamp is not None]
    no_ts = [c for c in eligible if c.timestamp is None]
    with_ts.sort(key=lambda c: c.timestamp)

    if len(with_ts) <= target:
        # Not enough timestamped to fill target; show all timestamped +
        # backfill from untimestamped (deterministic by path).
        no_ts_sorted = sorted(no_ts, key=lambda c: str(c.path))
        return with_ts + no_ts_sorted[: target - len(with_ts)]

    return _time_spread_sample(with_ts, target)


def _collapse_near_duplicates(
    candidates: Sequence[PeekCandidate],
    *,
    window_seconds: float,
) -> List[PeekCandidate]:
    """Collapse near-duplicate "moments" to one representative per moment.

    A moment is a chronologically-consecutive run of timestamped photos
    where each adjacent gap is below ``window_seconds``. From each moment
    we keep the earliest photo and drop the rest. Untimestamped photos
    pass through unchanged (no temporal info to dedup against). When
    ``window_seconds <= 0`` the input is returned as-is (the feature
    opt-out path used by tests).
    """
    if window_seconds <= 0 or not candidates:
        return list(candidates)
    with_ts = sorted(
        (c for c in candidates if c.timestamp is not None),
        key=lambda c: c.timestamp,
    )
    no_ts = [c for c in candidates if c.timestamp is None]
    if not with_ts:
        return list(no_ts)
    out: List[PeekCandidate] = [with_ts[0]]
    last_ts = with_ts[0].timestamp
    for c in with_ts[1:]:
        gap = (c.timestamp - last_ts).total_seconds()
        if gap >= window_seconds:
            out.append(c)
            last_ts = c.timestamp
    return out + no_ts


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _passes_filter(c: PeekCandidate, *, max_bytes: int) -> bool:
    """Eligibility for the peek: not a video, not over the size cap."""
    if c.is_video:
        return False
    if c.byte_size > 0 and c.byte_size > max_bytes:
        return False
    return True


def _dedup_raw_jpeg_pairs(
    candidates: Sequence[PeekCandidate],
) -> List[PeekCandidate]:
    """Group ``(parent_dir, stem)`` and keep ONE per group, preferring
    non-RAW. Within a group, ties are broken by extension lexical sort
    so the choice is deterministic across platforms."""
    groups: dict[tuple, list[PeekCandidate]] = {}
    order: list[tuple] = []
    for c in candidates:
        key = (str(c.path.parent), c.path.stem.lower())
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(c)

    out: List[PeekCandidate] = []
    for key in order:
        members = groups[key]
        if len(members) == 1:
            out.append(members[0])
            continue
        non_raw = [m for m in members if m.path.suffix.lower() not in _RAW_EXTS]
        pool = non_raw or members
        # Deterministic tie-break — sort by extension, then path.
        pool.sort(key=lambda m: (m.path.suffix.lower(), str(m.path)))
        out.append(pool[0])
    return out


def _sorted_chronologically(
    candidates: Sequence[PeekCandidate],
) -> List[PeekCandidate]:
    """Sort by ``timestamp`` (untimestamped at the end, sorted by path)."""
    with_ts = sorted(
        (c for c in candidates if c.timestamp is not None),
        key=lambda c: c.timestamp,
    )
    no_ts = sorted(
        (c for c in candidates if c.timestamp is None),
        key=lambda c: str(c.path),
    )
    return list(with_ts) + list(no_ts)


def _time_spread_sample(
    sorted_candidates: Sequence[PeekCandidate], target: int,
) -> List[PeekCandidate]:
    """Bucket the day's time range into ``target`` equal slices; pick the
    candidate nearest each bucket's center.

    Assumes ``sorted_candidates`` is already sorted by timestamp (the
    caller has done this). When every timestamp is the same (rare but
    possible — a card holding only burst frames timestamped to the
    second), falls back to index-spread.
    """
    n = len(sorted_candidates)
    if n <= target:
        return list(sorted_candidates)

    t_min = sorted_candidates[0].timestamp
    t_max = sorted_candidates[-1].timestamp
    span_seconds = (t_max - t_min).total_seconds()
    if span_seconds <= 0:
        # All same timestamp — index-spread.
        return [
            sorted_candidates[round(i * (n - 1) / (target - 1))]
            for i in range(target)
        ]

    bucket_seconds = span_seconds / target
    used: set[int] = set()
    selected: List[PeekCandidate] = []
    for i in range(target):
        center = t_min + timedelta(seconds=(i + 0.5) * bucket_seconds)
        best_idx: Optional[int] = None
        best_diff: Optional[float] = None
        for idx, c in enumerate(sorted_candidates):
            if idx in used:
                continue
            diff = abs((c.timestamp - center).total_seconds())
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best_idx = idx
        if best_idx is not None:
            used.add(best_idx)
            selected.append(sorted_candidates[best_idx])
    selected.sort(key=lambda c: c.timestamp)
    return selected


# --------------------------------------------------------------------------- #
# Diagnostics — for the dialog's empty-peek hint
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PeekStats:
    """Counters surfaced to the dialog's empty-peek hint.

    spec/52 §5.6 — when the day has nothing previewable, show "(no
    preview-able photos — N videos, M RAWs)". These counts let the
    dialog render that hint without recomputing.
    """

    total: int
    eligible: int
    videos: int
    raws_skipped: int
    huge_files: int


def stats_for_peek(
    candidates: Sequence[PeekCandidate],
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> PeekStats:
    """Compute the per-day counters the dialog uses for the empty-peek
    hint. ``raws_skipped`` only counts RAWs that were dropped because of
    a JPEG sibling — a lone RAW is still eligible and shows up in the
    peek (decoded via embedded preview)."""
    total = len(candidates)
    videos = sum(1 for c in candidates if c.is_video)
    huge = sum(
        1 for c in candidates
        if not c.is_video and c.byte_size > 0 and c.byte_size > max_bytes
    )

    eligible_pre_dedup = [
        c for c in candidates
        if _passes_filter(c, max_bytes=max_bytes)
    ]
    eligible_post_dedup = _dedup_raw_jpeg_pairs(eligible_pre_dedup)
    raws_skipped = len(eligible_pre_dedup) - len(eligible_post_dedup)

    return PeekStats(
        total=total,
        eligible=len(eligible_post_dedup),
        videos=videos,
        raws_skipped=raws_skipped,
        huge_files=huge,
    )
