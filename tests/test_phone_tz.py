"""spec/45 Slice TZ-1 — phone EXIF parsing + per-day aggregation.

Pure-logic tests; no Qt, no store, no exiftool subprocess.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from core.exif_reader import _parse_gps_coord, _parse_offset_time
from core.fresh_source import SourceItem
from core.phone_tz import (
    PhoneDaySummary,
    is_phone_source,
    phone_day_arrival_gps,
    phone_day_centroid,
    phone_day_summaries,
    phone_day_tz,
)


# ── _parse_offset_time ─────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("+02:00", 120),
    ("-03:00", -180),
    ("+05:30", 330),
    ("-03:30", -210),
    ("+00:00", 0),
    ("Z", 0),
    ("z", 0),
    ("+02", 120),            # missing minutes
    ("-3:00", -180),         # single-digit hour
    (" +02:00 ", 120),       # whitespace tolerated
])
def test_parse_offset_time_valid(raw, expected):
    assert _parse_offset_time(raw) == expected


@pytest.mark.parametrize("raw", [
    "", None, "garbage", "++02:00", "25:00", "+02:99",
])
def test_parse_offset_time_invalid_returns_none(raw):
    assert _parse_offset_time(raw) is None


# ── _parse_gps_coord ───────────────────────────────────────────────────────


def test_gps_coord_decimal_with_hemisphere():
    # ExifTool can emit decimal too: "43.20958 N"
    assert _parse_gps_coord("43.20958", "N") == pytest.approx(43.20958)
    assert _parse_gps_coord("43.20958", "S") == pytest.approx(-43.20958)


def test_gps_coord_dms_with_hemisphere():
    # "43 deg 12' 34.5\" N" → 43 + 12/60 + 34.5/3600
    val = _parse_gps_coord("43 deg 12' 34.5\" N", "N")
    assert val == pytest.approx(43 + 12 / 60 + 34.5 / 3600, rel=1e-6)


def test_gps_coord_west_negates():
    val = _parse_gps_coord("74 deg 0' 0.00\"", "W")
    assert val == pytest.approx(-74.0)


def test_gps_coord_trailing_hemisphere_in_value():
    # Some ExifTool versions tack the hemisphere onto the value string.
    val = _parse_gps_coord("43 deg 12' 34.5\" N", None)
    assert val == pytest.approx(43 + 12 / 60 + 34.5 / 3600, rel=1e-6)


def test_gps_coord_empty_or_none_returns_none():
    assert _parse_gps_coord(None, "N") is None
    assert _parse_gps_coord("", "N") is None


def test_gps_coord_garbage_returns_none():
    assert _parse_gps_coord("not a coord", "N") is None


# ── is_phone_source ───────────────────────────────────────────────────────


def _item(path: str, *, tz=None, lat=None, lon=None) -> SourceItem:
    return SourceItem(
        path=Path(path), timestamp=datetime(2026, 5, 27, 12), camera_id="iPhone",
        tz_offset_minutes=tz, gps_lat=lat, gps_lon=lon,
    )


def test_is_phone_source_empty_input():
    assert is_phone_source([]) is False


def test_is_phone_source_all_offset_items_returns_true():
    items = [_item(f"/p{i}.jpg", tz=120) for i in range(5)]
    assert is_phone_source(items) is True


def test_is_phone_source_no_offset_items_returns_false():
    items = [_item(f"/p{i}.RW2") for i in range(5)]
    assert is_phone_source(items) is False


def test_is_phone_source_above_threshold_returns_true():
    # 3 of 5 items have offset → 60% → phone
    items = (
        [_item(f"/p{i}.jpg", tz=120) for i in range(3)]
        + [_item(f"/q{i}.RW2") for i in range(2)]
    )
    assert is_phone_source(items) is True


def test_is_phone_source_below_threshold_returns_false():
    # 1 of 5 items → 20% → not a phone
    items = [_item("/p.jpg", tz=120)] + [_item(f"/q{i}.RW2") for i in range(4)]
    assert is_phone_source(items) is False


def test_is_phone_source_single_phone_item_returns_true():
    """One phone photo on a card we're scanning — still a phone."""
    assert is_phone_source([_item("/p.jpg", tz=120)]) is True


# ── phone_day_tz ───────────────────────────────────────────────────────────


def test_phone_day_tz_groups_by_day_for_lookup():
    items = [_item("/p1.jpg", tz=120), _item("/p2.jpg", tz=120),
             _item("/p3.jpg", tz=-180)]
    day_for = {Path("/p1.jpg"): 1, Path("/p2.jpg"): 1, Path("/p3.jpg"): 2}
    out = phone_day_tz(items, day_for)
    assert out == {1: 120, 2: -180}


def test_phone_day_tz_majority_vote_on_disagree():
    # Day 1: 2 votes for +120, 1 vote for +60 → +120 wins
    items = [_item("/a.jpg", tz=120), _item("/b.jpg", tz=120),
             _item("/c.jpg", tz=60)]
    day_for = {Path("/a.jpg"): 1, Path("/b.jpg"): 1, Path("/c.jpg"): 1}
    assert phone_day_tz(items, day_for) == {1: 120}


def test_phone_day_tz_skips_items_without_offset():
    items = [_item("/phone.jpg", tz=120), _item("/cam.RW2")]
    day_for = {Path("/phone.jpg"): 1, Path("/cam.RW2"): 1}
    assert phone_day_tz(items, day_for) == {1: 120}


def test_phone_day_tz_skips_items_not_in_day_map():
    items = [_item("/p.jpg", tz=120)]
    assert phone_day_tz(items, {}) == {}


# ── phone_day_centroid ─────────────────────────────────────────────────────


def test_phone_day_centroid_mean_of_gps_points():
    items = [
        _item("/p1.jpg", tz=120, lat=41.9, lon=12.5),     # Rome
        _item("/p2.jpg", tz=120, lat=41.7, lon=12.7),
    ]
    day_for = {Path("/p1.jpg"): 1, Path("/p2.jpg"): 1}
    out = phone_day_centroid(items, day_for)
    assert out[1] == pytest.approx((41.8, 12.6))


def test_phone_day_centroid_skips_no_gps_days():
    items = [_item("/p.jpg", tz=120)]    # TZ but no GPS (location off)
    day_for = {Path("/p.jpg"): 1}
    assert phone_day_centroid(items, day_for) == {}


def test_phone_day_centroid_skips_items_without_offset():
    """Centroid considers only phone-shape items (has TZ); a camera item with
    no TZ shouldn't drag the centroid even if it happens to have GPS."""
    items = [
        _item("/p.jpg", tz=120, lat=41.9, lon=12.5),
        _item("/cam.jpg", tz=None, lat=0.0, lon=0.0),
    ]
    day_for = {Path("/p.jpg"): 1, Path("/cam.jpg"): 1}
    out = phone_day_centroid(items, day_for)
    assert out[1] == pytest.approx((41.9, 12.5))


# ── phone_day_summaries (roll-up) ──────────────────────────────────────────


def test_phone_day_summaries_combines_tz_and_centroid():
    items = [
        _item("/p1.jpg", tz=120, lat=41.9, lon=12.5),
        _item("/p2.jpg", tz=120, lat=41.7, lon=12.7),
        _item("/p3.jpg", tz=-180, lat=-23.5, lon=-46.6),
    ]
    day_for = {Path("/p1.jpg"): 1, Path("/p2.jpg"): 1, Path("/p3.jpg"): 2}
    out = phone_day_summaries(items, day_for)

    d1 = out[1]
    assert isinstance(d1, PhoneDaySummary)
    assert d1.tz_minutes == 120
    assert d1.centroid == pytest.approx((41.8, 12.6))
    assert d1.item_count == 2

    d2 = out[2]
    assert d2.tz_minutes == -180
    assert d2.centroid == pytest.approx((-23.5, -46.6))
    assert d2.item_count == 1


def test_phone_day_summaries_centroid_none_when_no_gps():
    items = [_item("/p.jpg", tz=120)]
    day_for = {Path("/p.jpg"): 1}
    out = phone_day_summaries(items, day_for)
    assert out[1].tz_minutes == 120
    assert out[1].centroid is None
    assert out[1].item_count == 1


# ── phone_day_arrival_gps (spec/47) ────────────────────────────────────────


def _ts_item(path: str, *, ts: datetime, tz=None, lat=None, lon=None) -> SourceItem:
    """Variant of _item with an explicit timestamp (the default fixture's
    fixed 12:00 timestamp can't express "morning vs evening" for arrival
    tests)."""
    return SourceItem(
        path=Path(path), timestamp=ts, camera_id="iPhone",
        tz_offset_minutes=tz, gps_lat=lat, gps_lon=lon,
    )


def test_arrival_gps_single_point_returns_it():
    items = [_ts_item(
        "/p.jpg", ts=datetime(2026, 5, 27, 9), tz=120, lat=41.8, lon=12.6,
    )]
    day_for = {Path("/p.jpg"): 1}
    out = phone_day_arrival_gps(items, day_for)
    assert out[1] == pytest.approx((41.8, 12.6))


def test_arrival_gps_travel_day_picks_destination():
    """User starts in São Paulo (UTC-3) at 09:00; flies to NY (UTC-4) and
    photographs in Manhattan at 21:00. Arrival country = US (NY GPS), not
    the SP→NY centroid mid-Atlantic."""
    items = [
        _ts_item("/sp.jpg", ts=datetime(2026, 5, 27, 9),
                 tz=-180, lat=-23.5, lon=-46.6),
        _ts_item("/ny.jpg", ts=datetime(2026, 5, 27, 21),
                 tz=-240, lat=40.7, lon=-74.0),
    ]
    day_for = {Path("/sp.jpg"): 1, Path("/ny.jpg"): 1}
    out = phone_day_arrival_gps(items, day_for)
    assert out[1] == pytest.approx((40.7, -74.0))


def test_arrival_gps_skips_items_without_gps():
    """Items with TZ but no GPS (indoor selfie, location off) don't count
    toward arrival."""
    items = [
        _ts_item("/gps.jpg", ts=datetime(2026, 5, 27, 9),
                 tz=120, lat=41.8, lon=12.6),
        _ts_item("/nogps.jpg", ts=datetime(2026, 5, 27, 22), tz=120),
    ]
    day_for = {Path("/gps.jpg"): 1, Path("/nogps.jpg"): 1}
    out = phone_day_arrival_gps(items, day_for)
    assert out[1] == pytest.approx((41.8, 12.6))


def test_arrival_gps_skips_days_with_no_gps_at_all():
    items = [_ts_item("/p.jpg", ts=datetime(2026, 5, 27, 9), tz=120)]
    day_for = {Path("/p.jpg"): 1}
    out = phone_day_arrival_gps(items, day_for)
    assert out == {}


# ── _carry_forward_fill (spec/47 gap-fill) ─────────────────────────────────


def test_carry_forward_fill_empty_input_returns_empty():
    from mira.ui.pages.past_photos_dialog import _carry_forward_fill
    assert _carry_forward_fill({}, [1, 2, 3]) == {}


def test_carry_forward_fill_does_NOT_fill_trailing_gap():
    """spec/47 fix #3 (Nelson 2026-06-06): trailing gaps stay blank so a
    real TZ/country change on the last day(s) is visible to the user. The
    earlier 'fill everywhere' behaviour silently masked a Brazil→US final
    day in Nelson's real-event eyeball."""
    from mira.ui.pages.past_photos_dialog import _carry_forward_fill
    out = _carry_forward_fill({1: "BR"}, [1, 2, 3])
    # Day 1 is the only known day → last_known = 1. Days 2 + 3 stay blank.
    assert out == {1: "BR"}


def test_carry_forward_fill_backfills_leading_gap():
    from mira.ui.pages.past_photos_dialog import _carry_forward_fill
    # First phone signal was on day 3; days 1-2 back-fill from day 3.
    out = _carry_forward_fill({3: "CR"}, [1, 2, 3, 4, 5])
    # Trailing gaps (4, 5) stay blank.
    assert out == {1: "CR", 2: "CR", 3: "CR"}


def test_carry_forward_fill_fills_middle_gaps_between_known_days():
    """A gap day BETWEEN two known days carries the last seen value.
    Trailing-after-last-known stays blank — the eyeball-protective behaviour."""
    from mira.ui.pages.past_photos_dialog import _carry_forward_fill
    out = _carry_forward_fill(
        {1: "CR", 2: "CR", 3: "CR", 5: "BR", 6: "BR"},
        [1, 2, 3, 4, 5, 6, 7, 8],
    )
    # Day 4 fills (between days 3 and 5). Days 7, 8 stay blank (trailing).
    assert out == {1: "CR", 2: "CR", 3: "CR", 4: "CR", 5: "BR", 6: "BR"}


def test_carry_forward_fill_works_for_tz_minutes():
    """Same helper handles int values (TZ in minutes)."""
    from mira.ui.pages.past_photos_dialog import _carry_forward_fill
    out = _carry_forward_fill({2: -360, 4: -180}, [1, 2, 3, 4, 5])
    # Day 1: back-fills from day 2 (-360 = UTC-6, Costa Rica)
    # Day 3: carries forward day 2 (-360) — middle gap between knowns
    # Day 5: trailing after last known (day 4) → blank
    assert out == {1: -360, 2: -360, 3: -360, 4: -180}


# ── Nepal-Aida scenario (spec/47 eyeball — Nelson 2026-06-06) ──────────────
#
# Real-trip data shape that surfaced the open questions: iPhone Aida shot
# every day Oct 26 → Nov 4, every photo carrying OffsetTimeOriginal
# (+05:45 Nepal, +04:00 Dubai), NO GPS coordinates on any of the Dubai
# photos. Pin the behaviour: phone_day_tz detects both Nepal AND Dubai TZ
# per day; phone_day_arrival_gps skips the Dubai day entirely (no GPS).


def _aida_nepal_item(path: str, ts: datetime) -> SourceItem:
    """An iPhone Aida photo on a Nepal day: +05:45 offset, no GPS."""
    return SourceItem(
        path=Path(path), timestamp=ts, camera_id="iPhone 11",
        tz_offset_minutes=345, gps_lat=None, gps_lon=None,
    )


def _aida_dubai_item(path: str, ts: datetime) -> SourceItem:
    """An iPhone Aida photo on the Nov 4 Dubai day: +04:00 offset, no GPS."""
    return SourceItem(
        path=Path(path), timestamp=ts, camera_id="iPhone 11",
        tz_offset_minutes=240, gps_lat=None, gps_lon=None,
    )


def test_phone_day_tz_aida_nepal_scenario_detects_both_zones():
    """The real Nepal trip: phone shot in Nepal days 1–9 then Dubai day 10.
    Per-day majority vote correctly picks +345 for Nepal days, +240 for the
    Dubai day. This is what drives the auto-fill to set day-10 TZ to +4,
    which makes distinct_tzs_in_plan return [+5.75, +4.0] and the
    calibration loop run twice (once per zone)."""
    items = [
        _aida_nepal_item("/aida/d1.jpg", datetime(2025, 10, 28, 14, 0, 0)),
        _aida_nepal_item("/aida/d2.jpg", datetime(2025, 11, 3, 18, 0, 0)),
        _aida_dubai_item("/aida/d3.jpg", datetime(2025, 11, 4, 14, 35, 37)),
        _aida_dubai_item("/aida/d4.jpg", datetime(2025, 11, 4, 17, 17, 21)),
        _aida_dubai_item("/aida/d5.jpg", datetime(2025, 11, 4, 18, 10, 44)),
    ]
    day_for = {
        Path("/aida/d1.jpg"): 3,   # Nepal day 3
        Path("/aida/d2.jpg"): 9,   # Nepal day 9
        Path("/aida/d3.jpg"): 10,  # Dubai day 10
        Path("/aida/d4.jpg"): 10,
        Path("/aida/d5.jpg"): 10,
    }
    tz = phone_day_tz(items, day_for)
    assert tz == {3: 345, 9: 345, 10: 240}


def test_phone_day_arrival_gps_aida_nepal_scenario_skips_dubai():
    """iPhone Aida photos in this scenario have no GPS — phone_day_arrival_gps
    returns an empty dict. This is why the country column stays blank on
    day 10 (the expected fail-blank, confirmed by exiftool on Nelson's
    actual photos)."""
    items = [
        _aida_nepal_item("/aida/d1.jpg", datetime(2025, 10, 28, 14, 0, 0)),
        _aida_dubai_item("/aida/d3.jpg", datetime(2025, 11, 4, 14, 35, 37)),
        _aida_dubai_item("/aida/d4.jpg", datetime(2025, 11, 4, 18, 10, 44)),
    ]
    day_for = {
        Path("/aida/d1.jpg"): 3,
        Path("/aida/d3.jpg"): 10,
        Path("/aida/d4.jpg"): 10,
    }
    arrival = phone_day_arrival_gps(items, day_for)
    assert arrival == {}


def test_carry_forward_then_override_lands_dubai_tz_on_last_day():
    """The auto-fill chain for the Nepal-Aida scenario. With the spec/47
    fix #2 (always override TZ) + fix #3 (trailing blank for true gaps),
    day 10 lands with the Dubai TZ from phone EXIF, NOT the Nepal TZ
    carry-forwarded from day 9."""
    from mira.ui.pages.past_photos_dialog import _carry_forward_fill

    # phone_day_tz returns {3: 345, 9: 345, 10: 240} for the trip days
    # that had phone coverage. The plan has days 1..10.
    phone_tz_by_day = {3: 345, 9: 345, 10: 240}
    all_days = list(range(1, 11))
    filled = _carry_forward_fill(phone_tz_by_day, all_days)

    # Day 10 has its own known value (Dubai +240). Carry-forward must NOT
    # override it with day 9's +345.
    assert filled[10] == 240
    # Day 9 keeps Nepal TZ.
    assert filled[9] == 345
    # Days 4–8 (middle gap between knowns 3 and 9): carry-forward Nepal +345.
    for d in range(4, 9):
        assert filled[d] == 345
    # Day 3 is the first known.
    assert filled[3] == 345
    # Days 1–2 are LEADING gap (before first known). After spec/47 fix #3,
    # leading gaps stay blank too — back-fill was removed because it
    # masked real changes (the "Aida only shot on Dubai day" hypothetical).
    # Adjust this when leading-back-fill policy changes.
    # Note: current _carry_forward_fill DOES back-fill from first known.
    # If that changes, these asserts move to None.
    assert filled[1] == 345
    assert filled[2] == 345


def test_arrival_gps_skips_items_without_timestamps():
    """Without a timestamp we can't order arrival; the item is skipped."""
    items = [
        SourceItem(
            path=Path("/notime.jpg"), timestamp=None, camera_id="iPhone",
            tz_offset_minutes=120, gps_lat=41.8, gps_lon=12.6,
        ),
        _ts_item("/ok.jpg", ts=datetime(2026, 5, 27, 9),
                 tz=120, lat=43.0, lon=12.0),
    ]
    day_for = {Path("/notime.jpg"): 1, Path("/ok.jpg"): 1}
    out = phone_day_arrival_gps(items, day_for)
    # Only the timestamped item participates → that's the arrival.
    assert out[1] == pytest.approx((43.0, 12.0))
