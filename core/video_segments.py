"""Segment geometry derivation — the marker-partition model (spec/56 §1).

Segments are DERIVED from marker order, never stored: a video whose user
markers sit at ``m1 < m2 < … < mN`` has ``N + 1`` segments tiling
``[0, duration]`` — segment ``k`` spans boundary ``k`` → boundary ``k + 1``
over the boundary list ``(0, m1, …, mN, duration)``. The implicit start/end
markers are exactly that — implicit — so a video with zero stored markers is
one whole-timeline segment (whole-video export is not a special case).

A segment's *durable* identity is its INDEX in that order
(``video_segment.seg_index``); this module turns marker positions into
per-index geometry at read time, which is what makes "trimming IS moving
markers" true: a marker move changes the derived bounds here without touching
any stored segment row.

Pure logic — no Qt, no SQLite (CLAUDE.md invariant #8). The gateway resolves
marker rows + ``item.duration_ms`` and calls in; the Edit workshop timeline
(spec/56 slice 3) renders straight from these tuples.
"""
from __future__ import annotations

from bisect import bisect_right
from typing import List, Sequence, Tuple


def _validated(marker_ms: Sequence[int], duration_ms: int) -> List[int]:
    """Common validation: positive duration, strictly ascending markers, every
    marker strictly inside ``(0, duration)`` (0/duration are the implicit ends)."""
    if duration_ms <= 0:
        raise ValueError(f"duration_ms must be > 0, got {duration_ms}")
    markers = [int(ms) for ms in marker_ms]
    for prev, cur in zip(markers, markers[1:]):
        if cur <= prev:
            raise ValueError(f"markers must be strictly ascending, got {markers}")
    if markers and not (0 < markers[0] and markers[-1] < duration_ms):
        raise ValueError(
            f"markers must lie strictly inside (0, {duration_ms}), got {markers}")
    return markers


def segment_bounds(marker_ms: Sequence[int], duration_ms: int) -> List[Tuple[int, int]]:
    """``[(in_ms, out_ms)]`` for every segment, in ``seg_index`` order.

    ``marker_ms`` are the stored user markers in ascending order; the result
    always has ``len(marker_ms) + 1`` entries covering ``[0, duration_ms]``
    with no gaps and no overlaps.
    """
    markers = _validated(marker_ms, duration_ms)
    bounds = [0, *markers, int(duration_ms)]
    return list(zip(bounds[:-1], bounds[1:]))


def containing_segment(marker_ms: Sequence[int], at_ms: int, duration_ms: int) -> int:
    """The ``seg_index`` whose half-open span ``[in, out)`` contains ``at_ms``
    (the final segment is closed: ``at_ms == duration`` belongs to it).

    A position equal to a marker belongs to the segment *starting* there.
    This is the split index for marker insertion: a new marker at ``at_ms``
    splits segment ``containing_segment(existing, at_ms, duration)``.
    """
    markers = _validated(marker_ms, duration_ms)
    at_ms = int(at_ms)
    if not (0 <= at_ms <= duration_ms):
        raise ValueError(f"at_ms {at_ms} outside [0, {duration_ms}]")
    if at_ms == duration_ms:
        return len(markers)          # the last segment, closed at the far end
    return bisect_right(markers, at_ms)
