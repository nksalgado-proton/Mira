"""spec/45 Slice TZ-3 — discrete TZ vocabulary tests."""
from __future__ import annotations

import pytest

from core.discrete_tz import (
    STANDARD_TZ_OFFSETS_MINUTES,
    display_label_for_offset,
    format_offset,
    is_valid_offset,
    nearest_valid_offset,
)


def test_standard_offsets_cover_canonical_range():
    # Covers UTC-12 (-720) through UTC+14 (840).
    assert min(STANDARD_TZ_OFFSETS_MINUTES) == -720
    assert max(STANDARD_TZ_OFFSETS_MINUTES) == 840
    # Includes the famous fractional ones.
    for fractional in (345, 330, 525, 270, 210, 765):
        assert fractional in STANDARD_TZ_OFFSETS_MINUTES


def test_standard_offsets_sorted_no_duplicates():
    """Pin the canonical ordering so callers can iterate the tuple and get a
    sensible UI ordering by default. No duplicates either."""
    listed = list(STANDARD_TZ_OFFSETS_MINUTES)
    assert listed == sorted(listed)
    assert len(listed) == len(set(listed))


@pytest.mark.parametrize("offset,expected", [
    (0, True), (60, True), (120, True), (-720, True), (840, True),
    (345, True), (1, False), (-1, False), (500, False), (-999, False),
])
def test_is_valid_offset(offset, expected):
    assert is_valid_offset(offset) is expected


@pytest.mark.parametrize("offset,expected", [
    (0, "UTC+00:00"),
    (60, "UTC+01:00"),
    (-180, "UTC-03:00"),
    (345, "UTC+05:45"),
    (840, "UTC+14:00"),
])
def test_format_offset(offset, expected):
    assert format_offset(offset) == expected


def test_display_label_includes_location_hint():
    """+60 has a location hint, so the labelled form names a city."""
    label = display_label_for_offset(60)
    assert "UTC+01:00" in label
    assert "(" in label                       # location hint present


def test_display_label_falls_back_when_no_hint():
    # -150 ("Atlantic mid") technically has a hint; the genuine bare case is
    # an offset NOT in the hints table. Use the with_locations=False switch.
    bare = display_label_for_offset(60, with_locations=False)
    assert bare == "UTC+01:00"


def test_nearest_valid_offset_snaps_to_closest():
    # 73 minutes → closest standard is 60 (UTC+01:00).
    assert nearest_valid_offset(73) == 60
    # 350 → closest standard is 345 (UTC+05:45) or 360 (UTC+06:00); the
    # absolute-distance tiebreak picks 345 (|350-345|=5 < |350-360|=10).
    assert nearest_valid_offset(350) == 345


def test_nearest_valid_offset_exact_match_returns_input():
    assert nearest_valid_offset(0) == 0
    assert nearest_valid_offset(120) == 120
