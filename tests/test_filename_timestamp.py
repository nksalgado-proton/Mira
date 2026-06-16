"""Tests for core.filename_timestamp — task #120."""

from __future__ import annotations

from datetime import datetime

import pytest

from core.filename_timestamp import (
    ParsedTimestamp,
    parse_timestamp_from_filename,
)


# ── Full date+time patterns ────────────────────────────────────


def test_full_separated_dashes():
    p = parse_timestamp_from_filename("2025-05-03_17-31-43.jpg")
    assert p is not None
    assert p.dt == datetime(2025, 5, 3, 17, 31, 43)
    assert p.time_is_default is False


def test_full_separated_dots_in_time():
    """Google Drive-style: dashes in date, dots in time."""
    p = parse_timestamp_from_filename("2025-05-03 17.31.43.jpg")
    assert p is not None
    assert p.dt == datetime(2025, 5, 3, 17, 31, 43)


def test_full_separated_iso_8601():
    p = parse_timestamp_from_filename("2025-05-03T17:31:43.jpg")
    assert p is not None
    assert p.dt == datetime(2025, 5, 3, 17, 31, 43)


def test_full_compact_android_img():
    """Android camera: ``IMG_YYYYMMDD_HHMMSS.jpg``."""
    p = parse_timestamp_from_filename("IMG_20250503_173143.jpg")
    assert p is not None
    assert p.dt == datetime(2025, 5, 3, 17, 31, 43)


def test_full_compact_t_separator():
    """ISO-style compact: ``YYYYMMDDTHHMMSS``."""
    p = parse_timestamp_from_filename("20250503T173143_ANY.jpg")
    assert p is not None
    assert p.dt == datetime(2025, 5, 3, 17, 31, 43)


# ── Double-stamped (last wins) ─────────────────────────────────


def test_double_stamped_picks_the_last_timestamp(qapp=None):
    """``<mtime>__<original>.jpg`` — the LAST timestamp is the one
    we want (the original capture). This is Nelson's real case."""
    p = parse_timestamp_from_filename(
        "2025-05-03_15-38-15__2025-04-10_20.32.46.jpg")
    assert p is not None
    # The SECOND timestamp (the original) — not the first.
    assert p.dt == datetime(2025, 4, 10, 20, 32, 46)


def test_double_stamped_compact_last_wins():
    """Same convention but with compact format on both sides."""
    p = parse_timestamp_from_filename(
        "20250503_153815__20250410_203246.jpg")
    assert p is not None
    assert p.dt == datetime(2025, 4, 10, 20, 32, 46)


def test_double_stamped_mixed_formats_last_wins():
    """A separated prefix and a compact suffix — last still wins."""
    p = parse_timestamp_from_filename(
        "2025-05-03_15-38-15__IMG_20250410_203246.jpg")
    assert p is not None
    assert p.dt == datetime(2025, 4, 10, 20, 32, 46)


# ── Date-only patterns ─────────────────────────────────────────


def test_date_only_separated_defaults_to_noon():
    p = parse_timestamp_from_filename("Photo 2025-05-03.jpg")
    assert p is not None
    assert p.dt == datetime(2025, 5, 3, 12, 0, 0)
    assert p.time_is_default is True


def test_date_only_compact_whatsapp_style():
    """WhatsApp pattern: ``IMG-YYYYMMDD-WA0001.jpg`` (no time)."""
    p = parse_timestamp_from_filename("IMG-20250503-WA0001.jpg")
    assert p is not None
    assert p.dt == datetime(2025, 5, 3, 12, 0, 0)
    assert p.time_is_default is True


# ── Negative cases — must NOT false-match ──────────────────────


def test_random_name_returns_none():
    assert parse_timestamp_from_filename("DSCN0001.JPG") is None


def test_empty_name_returns_none():
    assert parse_timestamp_from_filename("") is None


def test_year_too_low_rejected():
    """1994 is below the accepted window — false-positive guard."""
    assert parse_timestamp_from_filename("1994-05-03_12-00-00.jpg") is None


def test_year_too_high_rejected():
    """2100+ is outside the window — won't match."""
    assert parse_timestamp_from_filename("2100-05-03_12-00-00.jpg") is None


def test_invalid_month_rejected():
    """13 is not a month."""
    assert parse_timestamp_from_filename("2025-13-03_12-00-00.jpg") is None


def test_invalid_day_rejected_via_datetime_constructor():
    """Feb 30 — invalid; datetime() constructor catches it."""
    assert parse_timestamp_from_filename("2025-02-30_12-00-00.jpg") is None


def test_invalid_hour_rejected():
    """25:00 — invalid hour."""
    assert parse_timestamp_from_filename("2025-05-03_25-00-00.jpg") is None


def test_full_pattern_wins_over_date_only_substring():
    """A full timestamp should fire, not the date-only fallback,
    even though the date-only regex would also match the leading
    YYYY-MM-DD part."""
    p = parse_timestamp_from_filename("2025-05-03_17-31-43.jpg")
    assert p is not None
    assert p.time_is_default is False     # full, not noon-default


# ── Extension handling ─────────────────────────────────────────


def test_no_extension():
    p = parse_timestamp_from_filename("IMG_20250503_173143")
    assert p is not None
    assert p.dt == datetime(2025, 5, 3, 17, 31, 43)


def test_uppercase_extension():
    p = parse_timestamp_from_filename("IMG_20250503_173143.JPG")
    assert p is not None


def test_double_extension():
    """``.tar.gz``-like double extensions — strip only the LAST."""
    p = parse_timestamp_from_filename("backup.IMG_20250503_173143.jpg")
    assert p is not None
    assert p.dt == datetime(2025, 5, 3, 17, 31, 43)


# ── Pattern label is returned ──────────────────────────────────


def test_pattern_label_set():
    """Callers may want to surface what pattern matched (debug,
    logging). The ParsedTimestamp.pattern field carries it."""
    p = parse_timestamp_from_filename("2025-05-03_17-31-43.jpg")
    assert p is not None
    assert p.pattern == "full_separated"

    p = parse_timestamp_from_filename("IMG_20250503_173143.jpg")
    assert p is not None
    assert p.pattern == "full_compact"

    p = parse_timestamp_from_filename("Photo 2025-05-03.jpg")
    assert p is not None
    assert p.pattern == "date_only_separated"


# ── WhatsApp filename convention (spec/78 §B) ──────────────────


def test_whatsapp_full_image():
    """``WhatsApp Image YYYY-MM-DD at HH.MM.SS.jpeg`` — the WhatsApp
    convention with ``at`` between date and time."""
    p = parse_timestamp_from_filename(
        "WhatsApp Image 2018-02-24 at 20.42.37.jpeg")
    assert p is not None
    assert p.dt == datetime(2018, 2, 24, 20, 42, 37)
    assert p.time_is_default is False


def test_whatsapp_dedupe_suffix():
    """WhatsApp's ``(N)`` dedupe suffix after the time still parses."""
    p = parse_timestamp_from_filename(
        "WhatsApp Image 2018-02-24 at 20.42.37 (1).jpeg")
    assert p is not None
    assert p.dt == datetime(2018, 2, 24, 20, 42, 37)
    assert p.time_is_default is False


def test_whatsapp_date_only_falls_back_to_noon():
    """``WhatsApp Image YYYY-MM-DD`` with no time → noon default via
    the existing date-only fallback."""
    p = parse_timestamp_from_filename("WhatsApp Image 2018-02-24.jpg")
    assert p is not None
    assert p.dt == datetime(2018, 2, 24, 12, 0, 0)
    assert p.time_is_default is True


def test_whatsapp_video_same_convention():
    """``WhatsApp Video …`` uses the same ``DATE at TIME`` convention —
    the generalisation should cover any prefix."""
    p = parse_timestamp_from_filename(
        "WhatsApp Video 2018-02-24 at 20.42.37.mp4")
    assert p is not None
    assert p.dt == datetime(2018, 2, 24, 20, 42, 37)


def test_non_whatsapp_no_date_still_none():
    """A non-WhatsApp filename with no recoverable date still returns
    None — the WhatsApp generalisation must not lower the bar."""
    assert parse_timestamp_from_filename(
        "WhatsApp Image at the park.jpeg") is None
