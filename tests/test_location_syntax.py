"""Tests for core.location_syntax — task #110."""

from __future__ import annotations

import pytest

from core.location_syntax import LocationParts, parse_location


# ── Plain (legacy) ─────────────────────────────────────────────


def test_plain_location_is_origin_only():
    p = parse_location("San José")
    assert p.origin == "San José"
    assert p.destination is None
    assert p.transport is None
    assert not p.is_travel


def test_empty_string_is_empty_parts():
    p = parse_location("")
    assert p == LocationParts(origin="")
    assert p.display == ""
    assert p.folder_safe == ""


def test_whitespace_only_is_empty():
    assert parse_location("   ").origin == ""


def test_leading_trailing_whitespace_stripped():
    p = parse_location("  Kathmandu  ")
    assert p.origin == "Kathmandu"


# ── Travel days (>) ────────────────────────────────────────────


def test_travel_day_simple():
    p = parse_location("San José > La Fortuna")
    assert p.origin == "San José"
    assert p.destination == "La Fortuna"
    assert p.transport is None
    assert p.is_travel


def test_travel_day_tight_no_spaces():
    p = parse_location("Kathmandu>Pokhara")
    assert p.origin == "Kathmandu"
    assert p.destination == "Pokhara"
    assert p.is_travel


def test_travel_day_with_trailing_arrow_collapses_to_stay():
    """User typed ``A >`` but hadn't finished — origin holds, dest is None."""
    p = parse_location("La Fortuna >")
    assert p.origin == "La Fortuna"
    assert p.destination is None
    assert not p.is_travel


def test_multiple_arrows_first_wins():
    p = parse_location("A > B > C")
    assert p.origin == "A"
    # Only the FIRST > splits; the rest is part of the destination.
    assert p.destination == "B > C"


# ── Transport (#) ──────────────────────────────────────────────


def test_transport_on_stay_day():
    p = parse_location("Kathmandu # walking")
    assert p.origin == "Kathmandu"
    assert p.destination is None
    assert p.transport == "walking"


def test_transport_on_travel_day():
    p = parse_location("Kathmandu > Pokhara # bus")
    assert p.origin == "Kathmandu"
    assert p.destination == "Pokhara"
    assert p.transport == "bus"


def test_hash_without_preceding_space_stays_in_name():
    """``#`` not preceded by a space is part of the name (e.g.,
    ``Restaurant#3`` keeps the ``#3`` as origin text). The space-
    preceded form ``Restaurant #3`` is treated as transport — the
    syntax is opt-in so users who use ``#`` accept that constraint."""
    p = parse_location("Restaurant#3")
    assert p.origin == "Restaurant#3"
    assert p.transport is None


def test_empty_transport_collapses_to_none():
    p = parse_location("Kathmandu # ")
    assert p.origin == "Kathmandu"
    assert p.transport is None


def test_transport_at_start():
    """A pathological case — user wrote ``#bus`` only — origin
    empty, transport set. Honoured as the user's intent."""
    p = parse_location("#bus")
    assert p.origin == ""
    assert p.transport == "bus"


# ── Display / folder_safe ──────────────────────────────────────


def test_display_stay():
    p = parse_location("San José")
    assert p.display == "San José"


def test_display_travel():
    p = parse_location("San José > La Fortuna")
    # Travel arrow uses → for readability.
    assert p.display == "San José → La Fortuna"


def test_display_transport_appended_in_parens():
    p = parse_location("Kathmandu > Pokhara # bus")
    assert p.display == "Kathmandu → Pokhara (bus)"


def test_display_stay_with_transport():
    p = parse_location("Kathmandu # walking")
    assert p.display == "Kathmandu (walking)"


def test_folder_safe_uses_plain_words():
    """folder_safe avoids ``>`` and ``#`` for Explorer readability."""
    p = parse_location("Kathmandu > Pokhara # bus")
    assert p.folder_safe == "Kathmandu to Pokhara - bus"


def test_folder_safe_stay():
    p = parse_location("San José")
    assert p.folder_safe == "San José"


# ── Round-trip ─────────────────────────────────────────────────


def test_parse_normalises_inner_whitespace_around_separators():
    """Inputs differing only in whitespace AROUND separators parse
    to the same result. (Note: ``#`` must be preceded by a space to
    act as transport — see ``test_hash_without_preceding_space``.)"""
    a = parse_location("San José > La Fortuna #car")
    b = parse_location("San José  >  La Fortuna  #  car")
    assert a == b
    assert a.origin == "San José"
    assert a.destination == "La Fortuna"
    assert a.transport == "car"
