"""Tests for core.tz_locations — the shared TZ named-location table
(P4, docs/14 §"TZ named-location picker"). Pure, no Qt."""

from __future__ import annotations

from core.tz_locations import (
    TZ_LOCATIONS,
    format_utc_offset,
    has_exact,
    nearest_location,
    offset_label,
    picker_label_for,
)


def test_format_utc_offset_matches_legacy_fmt():
    # The exact strings the old camera_clock_dialog._fmt_offset
    # produced — re-exported, so these must not drift.
    assert format_utc_offset(5.75) == "UTC+05:45"     # Nepal
    assert format_utc_offset(-3.0) == "UTC-03:00"     # São Paulo
    assert format_utc_offset(0.0) == "UTC+00:00"
    assert format_utc_offset(5.5) == "UTC+05:30"      # India
    assert format_utc_offset(-3.5) == "UTC-03:30"     # Newfoundland
    assert format_utc_offset(14.0) == "UTC+14:00"


def test_table_is_sorted_and_unique_and_covers_key_zones():
    offsets = [off for _p, off in TZ_LOCATIONS]
    assert offsets == sorted(offsets)                  # west → east
    assert len(offsets) == len(set(offsets))           # one place/zone
    by_off = {off: place for place, off in TZ_LOCATIONS}
    assert "Kathmandu" in by_off[5.75]                 # the trap zone
    assert "São Paulo" in by_off[-3.0]                 # Nelson's home
    assert 0.0 in by_off                               # UTC anchor


def test_offset_label_combines_place_and_offset():
    assert offset_label("Kathmandu (Nepal)", 5.75) == \
        "Kathmandu (Nepal) — UTC+05:45"


def test_nearest_location_exact_and_approximate():
    # Exact value → that very entry.
    assert nearest_location(5.75) == ("Kathmandu (Nepal)", 5.75)
    assert nearest_location(-3.0)[1] == -3.0
    # The +5.45 trap: a user typing 5.45 lands on India (+5.5),
    # NOT Nepal (+5.75) — exactly why the named picker exists.
    assert nearest_location(5.45)[1] == 5.5


def test_has_exact():
    assert has_exact(5.75) is True
    assert has_exact(0.0) is True
    assert has_exact(5.45) is False                    # the bad value


def test_picker_label_for_exact_match_uses_named_location():
    """An offset that matches a known location → "<place> — UTC±HH:MM",
    the exact string the TzPicker row shows."""
    assert picker_label_for(-3.0) == "São Paulo / Buenos Aires — UTC-03:00"
    assert picker_label_for(5.75) == "Kathmandu (Nepal) — UTC+05:45"
    assert picker_label_for(0.0) == "London / Lisbon (UTC) — UTC+00:00"


def test_picker_label_for_unknown_offset_falls_to_custom():
    """No exact match → "Custom — UTC±HH:MM", mirroring the
    TzPicker's transient custom row."""
    # 5:50 = 5.83333… isn't in the table.
    assert picker_label_for(5.0 + 50.0 / 60.0) == "Custom — UTC+05:50"
