"""Tests for core.capture_plan_check — task #109."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from core.capture_plan_check import (
    extend_plan_with_dates,
    find_orphan_dates,
    summarise_orphans,
)
from core.models import Event, TripDay


def _plan() -> list[TripDay]:
    return [
        TripDay(day_number=1, date=date(2026, 5, 1), description="D1"),
        TripDay(day_number=2, date=date(2026, 5, 2), description="D2"),
    ]


# ── find_orphan_dates ──────────────────────────────────────────


def test_no_orphans_returns_empty():
    pairs = [
        (Path("a.jpg"), datetime(2026, 5, 1, 12, 0)),
        (Path("b.jpg"), datetime(2026, 5, 2, 12, 0)),
    ]
    assert find_orphan_dates(pairs, _plan()) == {}


def test_orphans_grouped_by_date():
    pairs = [
        (Path("a.jpg"), datetime(2026, 5, 1, 12, 0)),    # in plan
        (Path("b.jpg"), datetime(2026, 5, 3, 9, 0)),     # orphan
        (Path("c.jpg"), datetime(2026, 5, 3, 18, 0)),    # orphan, same day
        (Path("d.jpg"), datetime(2026, 5, 4, 10, 0)),    # orphan, diff day
    ]
    result = find_orphan_dates(pairs, _plan())
    assert list(result.keys()) == [date(2026, 5, 3), date(2026, 5, 4)]
    assert result[date(2026, 5, 3)] == [Path("b.jpg"), Path("c.jpg")]
    assert result[date(2026, 5, 4)] == [Path("d.jpg")]


def test_files_with_none_timestamp_are_skipped():
    pairs = [
        (Path("a.jpg"), None),                            # quarantine path
        (Path("b.jpg"), datetime(2026, 5, 3, 12, 0)),     # orphan
    ]
    result = find_orphan_dates(pairs, _plan())
    assert result == {date(2026, 5, 3): [Path("b.jpg")]}


def test_empty_plan_treats_every_dated_file_as_orphan():
    pairs = [
        (Path("a.jpg"), datetime(2026, 5, 1, 12, 0)),
        (Path("b.jpg"), datetime(2026, 5, 2, 12, 0)),
    ]
    result = find_orphan_dates(pairs, [])
    assert set(result.keys()) == {date(2026, 5, 1), date(2026, 5, 2)}


def test_plan_days_without_date_are_ignored():
    """A TripDay with date=None doesn't cover anything; it shouldn't
    accidentally let an orphan slip through."""
    plan = [
        TripDay(day_number=1, date=None, description="floating"),
        TripDay(day_number=2, date=date(2026, 5, 2), description="D2"),
    ]
    pairs = [
        (Path("a.jpg"), datetime(2026, 5, 1, 12, 0)),    # orphan
        (Path("b.jpg"), datetime(2026, 5, 2, 12, 0)),    # in plan
    ]
    result = find_orphan_dates(pairs, plan)
    assert list(result.keys()) == [date(2026, 5, 1)]


# ── extend_plan_with_dates ─────────────────────────────────────


def test_extend_appends_and_renumbers():
    event = Event(name="X")
    event.trip_days = [
        TripDay(day_number=1, date=date(2026, 5, 1), description="D1"),
        TripDay(day_number=2, date=date(2026, 5, 2), description="D2"),
    ]
    added = extend_plan_with_dates(event, [date(2026, 5, 3)])
    assert len(added) == 1
    assert added[0].date == date(2026, 5, 3)
    assert added[0].description.startswith("(added")
    assert [d.day_number for d in event.trip_days] == [1, 2, 3]
    assert [d.date for d in event.trip_days] == [
        date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3),
    ]


def test_extend_inserts_in_chronological_order():
    """New dates that fall BEFORE existing days renumber the whole
    plan so day_numbers stay date-ordered."""
    event = Event(name="X")
    event.trip_days = [
        TripDay(day_number=1, date=date(2026, 5, 5), description="D5"),
    ]
    added = extend_plan_with_dates(event, [date(2026, 5, 3)])
    assert len(added) == 1
    # The new day got day_number=1; the existing one shifted to 2.
    assert [(d.day_number, d.date) for d in event.trip_days] == [
        (1, date(2026, 5, 3)),
        (2, date(2026, 5, 5)),
    ]


def test_extend_is_idempotent_for_already_present_dates():
    """Adding a date that's already in the plan is a no-op."""
    event = Event(name="X")
    event.trip_days = [
        TripDay(day_number=1, date=date(2026, 5, 1), description="D1"),
    ]
    added = extend_plan_with_dates(event, [date(2026, 5, 1)])
    assert added == []
    assert len(event.trip_days) == 1


def test_extend_handles_multiple_orphan_dates():
    event = Event(name="X")
    event.trip_days = [
        TripDay(day_number=1, date=date(2026, 5, 5), description="D5"),
    ]
    added = extend_plan_with_dates(
        event, [date(2026, 5, 3), date(2026, 5, 7), date(2026, 5, 4)],
    )
    assert len(added) == 3
    assert [d.date for d in event.trip_days] == [
        date(2026, 5, 3),
        date(2026, 5, 4),
        date(2026, 5, 5),
        date(2026, 5, 7),
    ]
    assert [d.day_number for d in event.trip_days] == [1, 2, 3, 4]


# ── summarise_orphans ─────────────────────────────────────────


def test_summarise_returns_dates_and_counts():
    orphans = {
        date(2026, 5, 3): [Path("a"), Path("b"), Path("c")],
        date(2026, 5, 4): [Path("d")],
    }
    assert summarise_orphans(orphans) == [
        (date(2026, 5, 3), 3),
        (date(2026, 5, 4), 1),
    ]
