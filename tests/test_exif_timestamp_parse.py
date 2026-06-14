"""Tests for ``core.exif_reader._parse_timestamp``.

Nelson 2026-06-13 — bug surfaced when GoPro / iOS CreationDate values
landed with a non-US-Mountain timezone trailer (``-03:00`` Brazil,
``-04:00`` East Coast, etc.) and got dropped to ``None``, routing
those videos to the no-timestamp quarantine even though their EXIF
clearly carried a wall-clock time. The hardcoded ``split("-07:00")``
was the cause.

These pins cover every shape ExifTool / QuickTime is observed to
emit so the fix can't silently regress.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from core.exif_reader import _parse_timestamp


def test_returns_none_for_empty():
    assert _parse_timestamp("") is None
    assert _parse_timestamp(None) is None


def test_basic_exif_colon_format():
    """Pure EXIF DateTimeOriginal — the calendar form."""
    got = _parse_timestamp("2026:03:30 08:58:12")
    assert got == datetime(2026, 3, 30, 8, 58, 12)


def test_alternate_dash_format():
    got = _parse_timestamp("2026-03-30 08:58:12")
    assert got == datetime(2026, 3, 30, 8, 58, 12)


def test_strips_fractional_seconds():
    got = _parse_timestamp("2026:03:30 08:58:12.123")
    assert got == datetime(2026, 3, 30, 8, 58, 12)


def test_strips_positive_tz_offset():
    got = _parse_timestamp("2026:03:30 08:58:12+02:00")
    assert got == datetime(2026, 3, 30, 8, 58, 12)


def test_strips_negative_tz_offset_us_mountain():
    """The prior implementation hardcoded this one. Make sure the fix
    still handles it."""
    got = _parse_timestamp("2026:03:30 08:58:12-07:00")
    assert got == datetime(2026, 3, 30, 8, 58, 12)


def test_strips_negative_tz_offset_brazil():
    """The bug Nelson hit on his Brazil 2023 GoPro footage — the
    timestamp ended in ``-03:00`` (BR local) and survived the splits,
    failed strptime, returned None, and the video landed without a
    date in the no-timestamp quarantine. Must round-trip now."""
    got = _parse_timestamp("2023:09:15 14:30:25-03:00")
    assert got == datetime(2023, 9, 15, 14, 30, 25)


def test_strips_negative_tz_offset_east_coast():
    got = _parse_timestamp("2026:03:30 08:58:12-04:00")
    assert got == datetime(2026, 3, 30, 8, 58, 12)


def test_strips_z_designator():
    """Some sources emit ``Z`` for UTC; the truncate-at-19 approach
    handles it by dropping the trailing ``Z`` anyway."""
    got = _parse_timestamp("2026:03:30 08:58:12Z")
    assert got == datetime(2026, 3, 30, 8, 58, 12)


def test_strips_fractional_plus_tz():
    got = _parse_timestamp("2026:03:30 08:58:12.500-03:00")
    assert got == datetime(2026, 3, 30, 8, 58, 12)


def test_returns_none_for_garbage():
    assert _parse_timestamp("not a date") is None


def test_returns_none_for_too_short():
    """Truncation makes a too-short string still too short; strptime
    rejects."""
    assert _parse_timestamp("2026:03:30") is None
