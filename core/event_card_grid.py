"""Phase × normalized-day grid aggregation for the EventCard.

Nelson 2026-05-21: the dashboard's per-event cards show a phase × day
status grid. Days vary wildly (3-day weekends to 60-day expeditions),
so to keep every card the same width we normalize the horizontal
axis to a fixed cell count (default 20). Each cell aggregates the
day-status values that fall inside its day-range.

Worst-case aggregation (least-advanced status wins) is the rule:
when a cell covers multiple days, the cell's status is the one
"furthest from done" — so a cell coloured ``in_progress`` means
"at least one day in this range still has work to do." The
optimistic alternative would hide laggards behind a single completed
day, which is the wrong signal for a "what needs my attention"
dashboard.

The status ordering (least → most advanced) is:

    not_started < ready < in_progress < done

``unavailable`` is *opted out* of the aggregation when any other
status is present — a phase that hasn't fired for day N (because
day N has no photos for that bucket type) shouldn't drag down the
display when the days around it are in progress. When every day in
the cell's range is ``unavailable``, the cell shows ``unavailable``.

Pure-Python; no Qt; safe to call off the GUI thread."""

from __future__ import annotations

from typing import Iterable

# ── Status vocabulary ─────────────────────────────────────────

# The canonical status set, in least → most advanced order. Caller
# may pass any subset; unrecognised values are treated as the
# minimum (``not_started``) so we err on the side of "show as work
# still to do".
STATUS_NOT_STARTED = "not_started"
STATUS_READY = "ready"
STATUS_IN_PROGRESS = "in_progress"
STATUS_DONE = "done"
STATUS_UNAVAILABLE = "unavailable"

_RANK = {
    STATUS_NOT_STARTED: 0,
    STATUS_READY: 1,
    STATUS_IN_PROGRESS: 2,
    STATUS_DONE: 3,
}


def _worst_of(statuses: Iterable[str]) -> str:
    """Pick the least-advanced status from the iterable. Ignores
    ``unavailable`` *unless* it's the only thing there — in which
    case it's returned as-is.

    Unknown values are mapped to ``not_started`` (safe default —
    "we don't know yet, assume not started")."""
    active = [s for s in statuses if s != STATUS_UNAVAILABLE]
    if not active:
        # Everything was unavailable (or empty input) — return
        # unavailable if we got anything, else not_started.
        return STATUS_UNAVAILABLE if any(True for _ in statuses) else STATUS_NOT_STARTED
    worst = active[0]
    worst_rank = _RANK.get(worst, 0)
    for s in active[1:]:
        r = _RANK.get(s, 0)
        if r < worst_rank:
            worst = s
            worst_rank = r
    return worst


# ── Mapping days → fixed-count cells ──────────────────────────


def cell_day_range(cell_index: int, n_cells: int, total_days: int) -> tuple[int, int]:
    """Day-number range (1-based, inclusive) that cell ``cell_index``
    (0..n_cells-1) covers in a trip of ``total_days`` days.

    Half-open underneath, then snapped to integer day numbers:
    cell ``i`` covers fractional days ``[i·D/N, (i+1)·D/N)``. When
    the fractional range has no integer day inside it (which can
    happen for short trips), the cell falls back to the single
    nearest day. Empty trips (``total_days <= 0``) return
    ``(0, 0)``.

    Returns a ``(first_day, last_day)`` tuple — both 1-based, both
    inclusive. ``first_day > last_day`` never happens (we always
    snap to at least one day per cell when ``total_days >= 1``)."""
    if total_days <= 0 or n_cells <= 0:
        return (0, 0)
    if cell_index < 0 or cell_index >= n_cells:
        return (0, 0)
    # Fractional day boundaries.
    lo = cell_index * total_days / n_cells
    hi = (cell_index + 1) * total_days / n_cells
    # Snap to integer day numbers (1-based). Day d covers fractional
    # range [d-1, d). The cell covers integer days d such that
    # ``d-1 < hi`` AND ``d > lo`` → ``lo < d <= hi+ε``. Use ceil(lo+ε)
    # for first day and floor(hi - ε) for last day, but it's easier
    # to express as: first integer in [lo, hi], last integer in
    # [lo, hi], with the half-open right-edge.
    first = int(lo) + 1                  # ceil(lo) for non-integer lo
    last = int(hi) if hi > int(hi) else int(hi)
    # ``hi`` may exactly equal int(hi) at right boundaries → don't
    # include the next day; use the cell's *own* upper integer.
    # Compensate by treating closed-on-the-right when hi is integer
    # AND lo is integer (single-day boundary).
    if hi == int(hi) and hi > 0:
        last = int(hi)
    # Single-day fallback: when first > last (sub-1-day cell)
    # snap to the nearest integer day inside [lo, hi].
    if first > last:
        snapped = int(round((lo + hi) / 2))
        snapped = max(1, min(total_days, snapped + 1))
        first = last = snapped
    # Clamp into [1, total_days].
    first = max(1, min(first, total_days))
    last = max(1, min(last, total_days))
    if last < first:
        first, last = last, first
    return (first, last)


def aggregate_to_cells(
    per_day_status: dict[int, str],
    *,
    total_days: int,
    n_cells: int = 20,
) -> list[str]:
    """Aggregate a per-day status map into ``n_cells`` cells using
    worst-case rules (least-advanced status wins).

    ``per_day_status`` maps 1-based day number → one of the
    ``STATUS_*`` constants. Days missing from the map are treated as
    ``not_started`` (the safe default for "I have no data"). The
    returned list always has exactly ``n_cells`` entries even when
    ``total_days < n_cells`` (multiple cells will share the same
    day's status — visually a wider block, which is fine).

    Empty trip (``total_days <= 0``) returns ``n_cells`` copies of
    ``not_started``."""
    if n_cells <= 0:
        return []
    if total_days <= 0:
        return [STATUS_NOT_STARTED] * n_cells

    out: list[str] = []
    for i in range(n_cells):
        first, last = cell_day_range(i, n_cells, total_days)
        if first <= 0 or last <= 0:
            out.append(STATUS_NOT_STARTED)
            continue
        statuses = [
            per_day_status.get(d, STATUS_NOT_STARTED)
            for d in range(first, last + 1)
        ]
        out.append(_worst_of(statuses))
    return out
