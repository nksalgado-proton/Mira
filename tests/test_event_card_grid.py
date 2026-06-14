"""Tests for core.event_card_grid — the phase × normalized-day
aggregation used by the dashboard's EventCard widgets (Nelson
2026-05-21)."""

from __future__ import annotations

import pytest

from core.event_card_grid import (
    STATUS_DONE,
    STATUS_IN_PROGRESS,
    STATUS_NOT_STARTED,
    STATUS_READY,
    STATUS_UNAVAILABLE,
    aggregate_to_cells,
    cell_day_range,
)


# ── cell_day_range ────────────────────────────────────────────


def test_cell_day_range_exact_match_trip_length_equals_cells():
    """14-day trip, 14 cells → each cell covers exactly one day,
    1-indexed."""
    for i in range(14):
        first, last = cell_day_range(i, n_cells=14, total_days=14)
        assert first == last == i + 1


def test_cell_day_range_long_trip_each_cell_spans_multiple_days():
    """60-day trip, 20 cells → 3 days per cell."""
    for i in range(20):
        first, last = cell_day_range(i, n_cells=20, total_days=60)
        assert last - first == 2          # 3 days per cell (inclusive)
        assert first == i * 3 + 1


def test_cell_day_range_short_trip_cells_share_days():
    """5-day trip, 20 cells → multiple cells share the same day
    (sub-1-day cells snap to the nearest integer day)."""
    # Day numbers across all 20 cells must be in [1, 5].
    all_days = []
    for i in range(20):
        first, last = cell_day_range(i, n_cells=20, total_days=5)
        all_days.extend(range(first, last + 1))
    assert min(all_days) == 1
    assert max(all_days) == 5
    # Every day in the trip must appear at least once.
    assert set(all_days) >= {1, 2, 3, 4, 5}


def test_cell_day_range_single_day_trip_all_cells_show_day_one():
    """1-day trip → every cell shows day 1."""
    for i in range(20):
        first, last = cell_day_range(i, n_cells=20, total_days=1)
        assert first == 1 and last == 1


def test_cell_day_range_empty_trip_returns_zero():
    assert cell_day_range(0, n_cells=20, total_days=0) == (0, 0)


def test_cell_day_range_out_of_bounds_index_returns_zero():
    assert cell_day_range(20, n_cells=20, total_days=10) == (0, 0)
    assert cell_day_range(-1, n_cells=20, total_days=10) == (0, 0)


# ── aggregate_to_cells: worst-case rule ────────────────────────


def test_aggregate_one_day_per_cell_passes_through():
    """7-day trip, 7 cells, one status per day — each cell shows
    exactly that day's status."""
    per_day = {
        1: STATUS_DONE,
        2: STATUS_DONE,
        3: STATUS_IN_PROGRESS,
        4: STATUS_READY,
        5: STATUS_NOT_STARTED,
        6: STATUS_NOT_STARTED,
        7: STATUS_NOT_STARTED,
    }
    cells = aggregate_to_cells(per_day, total_days=7, n_cells=7)
    assert cells == [
        STATUS_DONE, STATUS_DONE, STATUS_IN_PROGRESS,
        STATUS_READY, STATUS_NOT_STARTED, STATUS_NOT_STARTED,
        STATUS_NOT_STARTED,
    ]


def test_aggregate_worst_case_wins_when_cell_covers_multiple_days():
    """20-day trip with day 1 done and day 2 in_progress, aggregated
    into 10 cells → cell 0 covers days 1+2 → shows in_progress
    (the worst)."""
    per_day = {
        1: STATUS_DONE,
        2: STATUS_IN_PROGRESS,
        # rest defaults to not_started
    }
    cells = aggregate_to_cells(per_day, total_days=20, n_cells=10)
    # Each cell covers 2 days. Cell 0 = days 1+2.
    assert cells[0] == STATUS_IN_PROGRESS
    # Cells 1..9 cover days 3..20 (all default not_started).
    assert all(c == STATUS_NOT_STARTED for c in cells[1:])


def test_aggregate_status_ordering():
    """The ranking is not_started < ready < in_progress < done.
    Verify each pairwise comparison picks the lower-ranked one."""
    cells = aggregate_to_cells(
        {1: STATUS_DONE, 2: STATUS_READY},
        total_days=2, n_cells=1,
    )
    assert cells == [STATUS_READY]
    cells = aggregate_to_cells(
        {1: STATUS_READY, 2: STATUS_NOT_STARTED},
        total_days=2, n_cells=1,
    )
    assert cells == [STATUS_NOT_STARTED]
    cells = aggregate_to_cells(
        {1: STATUS_DONE, 2: STATUS_IN_PROGRESS},
        total_days=2, n_cells=1,
    )
    assert cells == [STATUS_IN_PROGRESS]


def test_aggregate_unavailable_excluded_when_others_present():
    """A cell covering days where some are ``unavailable`` and some
    are active picks the worst of the *active* days, NOT
    unavailable."""
    cells = aggregate_to_cells(
        {1: STATUS_UNAVAILABLE, 2: STATUS_IN_PROGRESS,
         3: STATUS_DONE},
        total_days=3, n_cells=1,
    )
    # Active = {in_progress, done}; worst = in_progress.
    assert cells == [STATUS_IN_PROGRESS]


def test_aggregate_unavailable_passed_through_when_only_value():
    """When every day in the cell's range is unavailable, the cell
    shows unavailable (not "not_started")."""
    cells = aggregate_to_cells(
        {1: STATUS_UNAVAILABLE, 2: STATUS_UNAVAILABLE},
        total_days=2, n_cells=1,
    )
    assert cells == [STATUS_UNAVAILABLE]


def test_aggregate_missing_days_default_to_not_started():
    """Days absent from the input map are treated as not_started."""
    cells = aggregate_to_cells(
        {1: STATUS_DONE},                # day 2 missing
        total_days=2, n_cells=1,
    )
    # worst-case picks the missing day's default → not_started.
    assert cells == [STATUS_NOT_STARTED]


def test_aggregate_empty_trip_returns_n_not_started():
    cells = aggregate_to_cells({}, total_days=0, n_cells=20)
    assert cells == [STATUS_NOT_STARTED] * 20


def test_aggregate_short_trip_into_20_cells():
    """5-day trip, all days done, 20 cells → all 20 cells show
    done (multiple cells share the same day's status)."""
    per_day = {d: STATUS_DONE for d in range(1, 6)}
    cells = aggregate_to_cells(per_day, total_days=5, n_cells=20)
    assert cells == [STATUS_DONE] * 20


def test_aggregate_long_trip_partial_progress():
    """60-day trip, 20 cells (3 days/cell). First 30 days done,
    next 30 not started → first 10 cells done, last 10
    not_started."""
    per_day = {d: STATUS_DONE for d in range(1, 31)}
    cells = aggregate_to_cells(per_day, total_days=60, n_cells=20)
    assert cells[:10] == [STATUS_DONE] * 10
    assert cells[10:] == [STATUS_NOT_STARTED] * 10


def test_aggregate_unknown_status_treated_as_not_started():
    """Defensive: garbage status string ranks as not_started (so we
    err on 'work still to do' rather than 'all good')."""
    cells = aggregate_to_cells(
        {1: "garbage", 2: STATUS_DONE},
        total_days=2, n_cells=1,
    )
    # "garbage" ranks 0 (same as not_started); done ranks 3.
    # worst → "garbage" but we accept either as "not advanced".
    assert cells[0] in {"garbage", STATUS_NOT_STARTED}


def test_aggregate_n_cells_can_be_overridden():
    """Default n_cells is 20 but the caller can change it."""
    cells = aggregate_to_cells({1: STATUS_DONE}, total_days=1)
    assert len(cells) == 20
    cells = aggregate_to_cells(
        {1: STATUS_DONE}, total_days=1, n_cells=10)
    assert len(cells) == 10
