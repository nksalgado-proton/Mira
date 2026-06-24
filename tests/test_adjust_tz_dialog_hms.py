"""spec/123 — H:M:S entry regression against the decimal-hours bug.

The pre-spec/123 dialog took ``5.45`` as 5h27m, not 5:45 — off by
minutes. The new entry parses ``±H:MM:SS`` to integer seconds, full
stop:

* ``5:45``  → 20 700 s
* ``-3:00`` → -10 800 s
* ``+5:00:02`` → 18 002 s (the Nepal pair, NOT 18 000 / 17 100)

The decimal form is rejected — ``5.45`` returns None at the parser
seam (no longer secretly interpreted as 5h27m).

spec/127 — the AdjustEventTzDialog retired with the unified Camera
Clock Correction dialog; ``format_seconds_hms`` / ``parse_hms_to_seconds``
moved to ``mira.ui.pages.camera_clock_dialog``. This test imports the
helpers from their new home; the H:M:S parser/formatter contract is
unchanged.
"""
from __future__ import annotations

from mira.ui.pages.camera_clock_dialog import (
    format_seconds_hms,
    parse_hms_to_seconds,
)


# ── Parse ────────────────────────────────────────────────────────────


def test_parse_five_forty_five_is_twenty_thousand_seven_hundred():
    """The headline regression — ``5:45`` MUST mean 5h45m, not 5h27m."""
    assert parse_hms_to_seconds("5:45") == 20_700


def test_parse_plus_sign_five_forty_five():
    assert parse_hms_to_seconds("+5:45") == 20_700


def test_parse_negative_three_zero():
    assert parse_hms_to_seconds("-3:00") == -10_800


def test_parse_eight_forty_five_nepal_gopro():
    """The corrected GoPro offset for the Nepal trip."""
    assert parse_hms_to_seconds("+8:45") == 31_500


def test_parse_nepal_measured_pair_seconds_precision():
    """Source-3 measured deltas can carry seconds (Nepal pair
    5h00m02s = 18 002 s)."""
    assert parse_hms_to_seconds("+5:00:02") == 18_002


def test_parse_hours_only():
    assert parse_hms_to_seconds("8") == 8 * 3600
    assert parse_hms_to_seconds("-1") == -3600


def test_parse_zero():
    assert parse_hms_to_seconds("0:00") == 0
    assert parse_hms_to_seconds("+0:00:00") == 0


def test_parse_rejects_decimal_hours():
    """The 'off by minutes' bug — ``5.45`` MUST NOT secretly mean
    5h27m. Parser returns None so the dialog snaps back to the last
    good value."""
    assert parse_hms_to_seconds("5.45") is None
    assert parse_hms_to_seconds("8.45") is None


def test_parse_rejects_minutes_above_59():
    assert parse_hms_to_seconds("5:60") is None
    assert parse_hms_to_seconds("5:99:00") is None


def test_parse_rejects_seconds_above_59():
    assert parse_hms_to_seconds("5:00:60") is None


def test_parse_rejects_garbage():
    assert parse_hms_to_seconds("hello") is None
    assert parse_hms_to_seconds("") is None
    assert parse_hms_to_seconds(":45") is None
    assert parse_hms_to_seconds("--3:00") is None


# ── Format (round-trip) ──────────────────────────────────────────────


def test_format_drops_seconds_when_zero():
    """Minute-aligned values display as ``±H:MM`` (no trailing :00)."""
    assert format_seconds_hms(20_700) == "+5:45"
    assert format_seconds_hms(-10_800) == "-3:00"
    assert format_seconds_hms(0) == "+0:00"


def test_format_keeps_seconds_when_nonzero():
    """Source-3 measured deltas show the seconds (so the user can
    see they aren't aligned to a zone)."""
    assert format_seconds_hms(18_002) == "+5:00:02"
    assert format_seconds_hms(-18_002) == "-5:00:02"


def test_format_parse_roundtrip_minute_aligned():
    for raw in ("+8:45", "-3:00", "+0:30", "-12:00"):
        assert format_seconds_hms(parse_hms_to_seconds(raw)) == raw


def test_format_parse_roundtrip_with_seconds():
    for raw in ("+5:00:02", "-2:30:15", "+0:00:42"):
        assert format_seconds_hms(parse_hms_to_seconds(raw)) == raw
