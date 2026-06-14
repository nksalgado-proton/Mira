"""Tests for ``core.plan_csv`` — spec/52 §5.5.

Logic-only (no Qt). Pure-Python encode + decode + the
non-destructive-merge apply outcome. Edge cases: BOM tolerance, ``;``
delimiter, quoting around embedded ``;`` / ``"`` / newlines, the
``±HH:MM`` TZ codec, header validation, malformed-row errors with line
numbers.
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from core.plan_csv import (
    DELIMITER,
    HEADER,
    ApplyOutcome,
    PlanCsvError,
    PlanCsvRow,
    apply_to_scan_days,
    decode,
    encode,
    load_from_path,
    save_to_path,
    tz_minutes_to_string,
    tz_string_to_minutes,
)


# --------------------------------------------------------------------------- #
# TZ codec
# --------------------------------------------------------------------------- #


def test_tz_minutes_to_string_handles_signed_offsets():
    assert tz_minutes_to_string(0) == "+00:00"
    assert tz_minutes_to_string(120) == "+02:00"
    assert tz_minutes_to_string(-180) == "-03:00"
    assert tz_minutes_to_string(-210) == "-03:30"        # India-style half-hour
    assert tz_minutes_to_string(525) == "+08:45"         # Nepal-style 8h45
    assert tz_minutes_to_string(None) == ""


def test_tz_string_to_minutes_round_trips_the_encoder():
    for minutes in (-720, -210, -180, 0, 120, 345, 525, 840):
        assert tz_string_to_minutes(tz_minutes_to_string(minutes)) == minutes


def test_tz_string_to_minutes_accepts_short_and_z_forms():
    """The decoder is more permissive than the encoder — it accepts
    ``+0200`` (no colon, four digits) and ``Z`` (UTC)."""
    assert tz_string_to_minutes("+0200") == 120
    assert tz_string_to_minutes("-0330") == -210
    assert tz_string_to_minutes("Z") == 0
    assert tz_string_to_minutes("z") == 0


def test_tz_string_to_minutes_empty_is_none():
    assert tz_string_to_minutes("") is None
    assert tz_string_to_minutes("   ") is None


def test_tz_string_to_minutes_rejects_garbage():
    with pytest.raises(ValueError):
        tz_string_to_minutes("abc")
    with pytest.raises(ValueError):
        tz_string_to_minutes("+25:00")           # out of range
    with pytest.raises(ValueError):
        tz_string_to_minutes("+02:65")           # minutes out of range
    with pytest.raises(ValueError):
        tz_string_to_minutes("+2")               # bare 1-digit


# --------------------------------------------------------------------------- #
# Encode
# --------------------------------------------------------------------------- #


def test_encode_writes_bom_and_header():
    text = encode([])
    # BOM at the start, then the canonical header row on the first line.
    assert text.startswith("﻿")
    assert text.lstrip("﻿").split("\r\n", 1)[0] == "date;country;tz;location;description"


def test_encode_sorts_rows_by_date_for_stable_output():
    rows = [
        PlanCsvRow(date=date(2026, 4, 3), country="CR", tz_minutes=-360),
        PlanCsvRow(date=date(2026, 4, 1), country="CR", tz_minutes=-360),
        PlanCsvRow(date=date(2026, 4, 2), country="CR", tz_minutes=-360),
    ]
    text = encode(rows).lstrip("﻿")
    body_lines = [ln for ln in text.split("\r\n") if ln]
    # Skip the header — verify the body order is ascending by date.
    dates = [ln.split(";")[0] for ln in body_lines[1:]]
    assert dates == ["2026-04-01", "2026-04-02", "2026-04-03"]


def test_encode_uses_semicolon_delimiter():
    row = PlanCsvRow(
        date=date(2026, 4, 1), country="CR", tz_minutes=-360,
        location="Quepos", description="Arrival",
    )
    text = encode([row]).lstrip("﻿")
    assert ";" in text
    assert "," not in text                # the delimiter is explicit, no fallback


def test_encode_quotes_cells_with_embedded_delimiter():
    """A semicolon embedded in a free-text cell must be quoted so the round-
    trip preserves it."""
    row = PlanCsvRow(
        date=date(2026, 4, 1), country="CR", tz_minutes=-360,
        location="Quepos; Manuel Antonio",          # embedded ;
        description='Day "1" of the trip',           # embedded "
    )
    text = encode([row]).lstrip("﻿")
    # Round-trip via decode is the strongest evidence the quoting is right.
    assert decode(text)[0] == row


def test_encode_empty_optional_fields_become_empty_cells():
    row = PlanCsvRow(date=date(2026, 4, 1))
    text = encode([row]).lstrip("﻿")
    lines = text.split("\r\n")
    # date;country;tz;location;description — empty cells separated by ; only.
    assert lines[1] == "2026-04-01;;;;"


# --------------------------------------------------------------------------- #
# Decode
# --------------------------------------------------------------------------- #


def test_decode_returns_typed_rows():
    text = (
        "date;country;tz;location;description\r\n"
        "2026-04-01;CR;-06:00;Quepos;Arrival\r\n"
    )
    rows = decode(text)
    assert rows == [PlanCsvRow(
        date=date(2026, 4, 1), country="CR", tz_minutes=-360,
        location="Quepos", description="Arrival",
    )]


def test_decode_tolerates_bom_and_unix_line_endings():
    text = (
        "﻿"
        "date;country;tz;location;description\n"
        "2026-04-01;CR;-06:00;Quepos;Arrival\n"
    )
    rows = decode(text)
    assert len(rows) == 1 and rows[0].country == "CR"


def test_decode_skips_blank_trailing_lines():
    text = (
        "date;country;tz;location;description\r\n"
        "2026-04-01;CR;-06:00;Quepos;Arrival\r\n"
        "\r\n"
        ";;;;\r\n"          # all-empty data row counts as blank too
    )
    rows = decode(text)
    assert len(rows) == 1


def test_decode_strips_whitespace_around_cells():
    text = (
        "date;country;tz;location;description\r\n"
        "  2026-04-01  ;  CR  ;  -06:00  ;  Quepos  ;  Arrival  \r\n"
    )
    rows = decode(text)
    assert rows[0].country == "CR"
    assert rows[0].location == "Quepos"


def test_decode_round_trips_an_encoded_file():
    """Encode → Decode produces the same Python data structure."""
    rows = [
        PlanCsvRow(
            date=date(2026, 4, 1), country="CR", tz_minutes=-360,
            location="Quepos", description="Arrival",
        ),
        PlanCsvRow(
            date=date(2026, 4, 2), country="CR", tz_minutes=-360,
            location="La Fortuna", description='Day "2"',
        ),
        PlanCsvRow(
            date=date(2026, 4, 3), country="", tz_minutes=None,
            location="", description="",
        ),
    ]
    text = encode(rows)
    assert decode(text) == sorted(rows, key=lambda r: r.date.isoformat())


def test_decode_empty_file_raises():
    with pytest.raises(PlanCsvError) as exc:
        decode("")
    assert "empty" in str(exc.value).lower()


def test_decode_wrong_header_raises_with_line_1():
    text = "wrong;header;here;ok;ok\r\n"
    with pytest.raises(PlanCsvError) as exc:
        decode(text)
    assert exc.value.line == 1
    assert "header" in str(exc.value).lower()


def test_decode_wrong_column_count_carries_line_number():
    text = (
        "date;country;tz;location;description\r\n"
        "2026-04-01;CR;-06:00;Quepos\r\n"                # missing one cell
    )
    with pytest.raises(PlanCsvError) as exc:
        decode(text)
    assert exc.value.line == 2
    assert "column count" in str(exc.value).lower()


def test_decode_missing_date_carries_line_number():
    text = (
        "date;country;tz;location;description\r\n"
        ";CR;-06:00;Quepos;Arrival\r\n"
    )
    with pytest.raises(PlanCsvError) as exc:
        decode(text)
    assert exc.value.line == 2
    assert "date" in str(exc.value).lower()


def test_decode_unparseable_date_carries_line_number():
    text = (
        "date;country;tz;location;description\r\n"
        "2026-04-XX;CR;-06:00;Quepos;Arrival\r\n"
    )
    with pytest.raises(PlanCsvError) as exc:
        decode(text)
    assert exc.value.line == 2


def test_decode_unparseable_tz_carries_line_number():
    text = (
        "date;country;tz;location;description\r\n"
        "2026-04-01;CR;banana;Quepos;Arrival\r\n"
    )
    with pytest.raises(PlanCsvError) as exc:
        decode(text)
    assert exc.value.line == 2


# --------------------------------------------------------------------------- #
# File I/O
# --------------------------------------------------------------------------- #


def test_save_to_path_then_load_from_path(tmp_path):
    rows = [
        PlanCsvRow(
            date=date(2026, 4, 1), country="CR", tz_minutes=-360,
            location="Quepos", description="Arrival",
        ),
    ]
    csv_path = tmp_path / "plan.csv"
    save_to_path(rows, csv_path)
    assert csv_path.is_file()
    assert load_from_path(csv_path) == rows


def test_load_from_path_raises_oserror_on_missing_file(tmp_path):
    with pytest.raises(OSError):
        load_from_path(tmp_path / "does_not_exist.csv")


# --------------------------------------------------------------------------- #
# apply_to_scan_days — non-destructive partial-overlap rule
# --------------------------------------------------------------------------- #


def test_apply_to_scan_days_matches_dates_in_scan():
    loaded = [
        PlanCsvRow(date=date(2026, 4, 1), country="CR"),
        PlanCsvRow(date=date(2026, 4, 2), country="CR"),
        PlanCsvRow(date=date(2026, 4, 3), country="CR"),
    ]
    scan_dates = [date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 3)]
    out = apply_to_scan_days(loaded, scan_dates)
    assert out.applied_dates == tuple(scan_dates)
    assert out.unmatched_dates == ()


def test_apply_to_scan_days_reports_unmatched_csv_rows():
    """spec/52 §5.5 — CSV rows whose date isn't in the scan are ignored
    with a notice (the count comes back in unmatched_dates)."""
    loaded = [
        PlanCsvRow(date=date(2026, 4, 1), country="CR"),
        PlanCsvRow(date=date(2026, 4, 5), country="CR"),        # not in scan
        PlanCsvRow(date=date(2026, 4, 6), country="CR"),        # not in scan
    ]
    scan_dates = [date(2026, 4, 1), date(2026, 4, 2)]
    out = apply_to_scan_days(loaded, scan_dates)
    assert out.applied_dates == (date(2026, 4, 1),)
    assert out.unmatched_dates == (date(2026, 4, 5), date(2026, 4, 6))


def test_apply_to_scan_days_leaves_scan_days_with_no_csv_row_alone():
    """spec/52 §5.5 — scan days with no matching CSV row are left as-is
    (partial loads are non-destructive). The outcome simply doesn't list
    them — the caller renders them unchanged."""
    loaded = [PlanCsvRow(date=date(2026, 4, 1), country="CR")]
    scan_dates = [date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 3)]
    out = apply_to_scan_days(loaded, scan_dates)
    assert out.applied_dates == (date(2026, 4, 1),)
    # 4/2 + 4/3 are in scan but not in loaded → not in either list (the
    # dialog leaves them untouched).
    assert out.unmatched_dates == ()


def test_apply_to_scan_days_empty_loaded_is_noop():
    out = apply_to_scan_days([], [date(2026, 4, 1)])
    assert out.applied_dates == () and out.unmatched_dates == ()


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #


def test_header_constants_match_spec():
    """Lock the column header set + ordering from spec/52 §5.5."""
    assert HEADER == ("date", "country", "tz", "location", "description")
    assert DELIMITER == ";"
