"""Tests for ``core.plan_gate`` — spec/52 §10.

Logic-only (no Qt). Builds a real event via the gateway + store layer, mutates
its rows to model each gate-failure case, and verifies the typed outcome
(complete bool + event_gaps + day_gaps).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.plan_gate import (
    PICK_GATE_TOOLTIP,
    DayGap,
    PlanGateOutcome,
    evaluate,
)
from mira.gateway import EventsIndex, Gateway
from mira.gateway.event_gateway import EventGateway
from mira.settings.repo import SettingsRepo
from mira.store import models as m
from mira.store.repo import EventStore


NOW = "2026-06-09T00:00:00+00:00"


# --------------------------------------------------------------------------- #
# Event-building helpers
# --------------------------------------------------------------------------- #


def _make_event(
    tmp_path: Path,
    *,
    name: str = "Costa Rica 2026",
    event_type: str = "trip",
    event_subtype: str | None = "Two weeks",
    days: list[m.TripDay] | None = None,
) -> EventGateway:
    """Build an event.db with one event row + the supplied trip_days; return
    an opened EventGateway over it."""
    base = tmp_path / "lib"
    base.mkdir(parents=True, exist_ok=True)
    db = base / "event.db"
    store = EventStore.create(db, event_id="evt-1")
    store.save_document(m.EventDocument(
        event=m.Event(
            uuid="evt-1", name=name,
            created_at=NOW, updated_at=NOW,
            start_date="2026-04-01", end_date="2026-04-02",
            event_type=event_type,
            event_subtype=event_subtype,
        ),
        trip_days=days or [],
    ))
    store.close()
    return EventGateway.open(db, event_root=base, now=lambda: NOW)


def _complete_day(day_number: int, *, country_code: str = "CR") -> m.TripDay:
    """A trip_day with country / tz / location all set — passes the gate."""
    return m.TripDay(
        day_number=day_number,
        date=f"2026-04-0{day_number}",
        location=f"Quepos day {day_number}",
        tz_minutes=-360,
        extras_json=json.dumps({"country_code": country_code}),
    )


# --------------------------------------------------------------------------- #
# The happy path
# --------------------------------------------------------------------------- #


def test_complete_event_passes_the_gate(tmp_path):
    eg = _make_event(tmp_path, days=[_complete_day(1), _complete_day(2)])
    try:
        outcome = evaluate(eg)
        assert outcome.complete is True
        assert outcome.event_gaps == ()
        assert outcome.day_gaps == ()
        assert outcome.summary() == ""
    finally:
        eg.close()


# --------------------------------------------------------------------------- #
# Event-level gaps
# --------------------------------------------------------------------------- #


def test_missing_event_name_blocks_the_gate(tmp_path):
    eg = _make_event(tmp_path, name="", days=[_complete_day(1)])
    try:
        outcome = evaluate(eg)
        assert outcome.complete is False
        assert "name" in outcome.event_gaps
    finally:
        eg.close()


def test_unclassified_event_type_blocks_the_gate(tmp_path):
    """``event_type='unclassified'`` is the DDL default — spec/52 §10 requires
    a real type to be chosen."""
    eg = _make_event(tmp_path, event_type="unclassified", event_subtype=None,
                     days=[_complete_day(1)])
    try:
        outcome = evaluate(eg)
        assert outcome.complete is False
        assert "type" in outcome.event_gaps
        assert "subtype" in outcome.event_gaps
    finally:
        eg.close()


def test_missing_event_subtype_blocks_the_gate(tmp_path):
    eg = _make_event(tmp_path, event_subtype=None, days=[_complete_day(1)])
    try:
        outcome = evaluate(eg)
        assert outcome.complete is False
        assert "subtype" in outcome.event_gaps
    finally:
        eg.close()


def test_whitespace_only_event_name_blocks_the_gate(tmp_path):
    eg = _make_event(tmp_path, name="   ", days=[_complete_day(1)])
    try:
        outcome = evaluate(eg)
        assert outcome.complete is False
        assert "name" in outcome.event_gaps
    finally:
        eg.close()


# --------------------------------------------------------------------------- #
# Day-level gaps
# --------------------------------------------------------------------------- #


def test_missing_day_country_blocks_the_gate(tmp_path):
    day = m.TripDay(
        day_number=1, date="2026-04-01",
        location="Quepos", tz_minutes=-360,
        extras_json="{}",            # no country_code
    )
    eg = _make_event(tmp_path, days=[day])
    try:
        outcome = evaluate(eg)
        assert outcome.complete is False
        assert outcome.day_gaps == (DayGap(day_number=1, missing=("country",)),)
    finally:
        eg.close()


def test_missing_day_tz_blocks_the_gate(tmp_path):
    day = m.TripDay(
        day_number=1, date="2026-04-01",
        location="Quepos", tz_minutes=None,
        extras_json=json.dumps({"country_code": "CR"}),
    )
    eg = _make_event(tmp_path, days=[day])
    try:
        outcome = evaluate(eg)
        assert outcome.complete is False
        assert outcome.day_gaps == (DayGap(day_number=1, missing=("timezone",)),)
    finally:
        eg.close()


def test_missing_day_location_blocks_the_gate(tmp_path):
    day = m.TripDay(
        day_number=1, date="2026-04-01",
        location=None, tz_minutes=-360,
        extras_json=json.dumps({"country_code": "CR"}),
    )
    eg = _make_event(tmp_path, days=[day])
    try:
        outcome = evaluate(eg)
        assert outcome.complete is False
        assert outcome.day_gaps == (DayGap(day_number=1, missing=("location",)),)
    finally:
        eg.close()


def test_day_tz_minutes_zero_is_a_valid_utc_offset(tmp_path):
    """``tz_minutes=0`` is UTC — a real, valid value. The gate must not
    confuse 0 with "missing"."""
    day = m.TripDay(
        day_number=1, date="2026-04-01",
        location="Greenwich", tz_minutes=0,
        extras_json=json.dumps({"country_code": "GB"}),
    )
    eg = _make_event(tmp_path, days=[day])
    try:
        outcome = evaluate(eg)
        assert outcome.complete is True
    finally:
        eg.close()


def test_whitespace_only_location_blocks_the_gate(tmp_path):
    day = m.TripDay(
        day_number=1, date="2026-04-01",
        location="   ", tz_minutes=-360,
        extras_json=json.dumps({"country_code": "CR"}),
    )
    eg = _make_event(tmp_path, days=[day])
    try:
        outcome = evaluate(eg)
        assert outcome.complete is False
        assert outcome.day_gaps[0].missing == ("location",)
    finally:
        eg.close()


def test_hidden_day_is_skipped_by_the_gate(tmp_path):
    """Hidden days are soft-excluded everywhere (spec/14 §5C.1) — they must
    not gate the Pick affordance."""
    incomplete_hidden_day = m.TripDay(
        day_number=2, date="2026-04-02",
        location=None, tz_minutes=None, hidden=True,
        extras_json="{}",
    )
    eg = _make_event(tmp_path, days=[
        _complete_day(1),
        incomplete_hidden_day,
    ])
    try:
        outcome = evaluate(eg)
        assert outcome.complete is True
        assert outcome.day_gaps == ()
    finally:
        eg.close()


# --------------------------------------------------------------------------- #
# Multiple gaps + summary text
# --------------------------------------------------------------------------- #


def test_multiple_gaps_aggregate_into_summary(tmp_path):
    """The summary one-liner lists event gaps first then a day-level count
    with the field names — the form the 'Why locked?' panel uses."""
    incomplete = m.TripDay(
        day_number=1, date="2026-04-01",
        location=None, tz_minutes=None,
        extras_json="{}",
    )
    eg = _make_event(tmp_path, event_subtype=None, days=[
        incomplete,
        _complete_day(2),
    ])
    try:
        outcome = evaluate(eg)
        assert outcome.complete is False
        # Event-level subtype gap.
        assert "subtype" in outcome.event_gaps
        # Day-level: only the one incomplete day.
        assert len(outcome.day_gaps) == 1
        assert outcome.day_gaps[0].day_number == 1
        assert set(outcome.day_gaps[0].missing) == {"country", "timezone", "location"}
        # Summary: lists what's missing in human terms.
        text = outcome.summary()
        assert "Event needs" in text
        assert "subtype" in text
        assert "1 day need" in text
        assert "country" in text and "timezone" in text and "location" in text
    finally:
        eg.close()


def test_summary_pluralisation(tmp_path):
    """The summary handles 1 vs N days + 1 vs N day-uses-of-field-X correctly."""
    day_a = m.TripDay(
        day_number=1, date="2026-04-01",
        location="Quepos", tz_minutes=-360, extras_json="{}",  # missing country
    )
    day_b = m.TripDay(
        day_number=2, date="2026-04-02",
        location="La Fortuna", tz_minutes=-360, extras_json="{}",  # also missing
    )
    eg = _make_event(tmp_path, days=[day_a, day_b])
    try:
        outcome = evaluate(eg)
        assert outcome.complete is False
        text = outcome.summary()
        # Plural "days" form when N > 1; plural "(2 days)" suffix on the field.
        assert "2 days need" in text
        assert "country (2 days)" in text
    finally:
        eg.close()


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #


def test_pick_gate_tooltip_matches_spec_text():
    """Lock the user-facing tooltip text from spec/52 §10 so a future rewrite
    of the gate logic doesn't accidentally drift the affordance copy."""
    assert PICK_GATE_TOOLTIP == \
        "Pick is locked until each day has country, timezone and location."
