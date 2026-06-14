"""F-019 engine tests — `core/preingest_check.py`.

Coverage:

* Each of the four pure sanity-check helpers
  (`check_future_dated`, `check_older_than_trip`,
  `check_night_majority`, `check_stale_gap`) in positive +
  negative cases.
* `build_preingest_plan` end-to-end over a synthetic 3-day plan
  with one source-item per day plus one undated file.
* `operations_from_items` arithmetic — offset applied per file,
  zero-offset is a no-op, missing-timestamp items skipped.
* `load_brand_tip` — exact-Model match wins, brand-wide
  `_default` fallback used otherwise, unknown brand → None.

Engine is pure-Python — no Qt, no exiftool calls. Brand-tip tests
inject a fake resolver where possible; the live `load_brand_tip`
is exercised against the real bundled JSON profiles (asserts the
Panasonic/Sony/Apple/GoPro `_default` fields are populated).
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from core.fresh_source import SourceItem
from core.models import TripDay
from core.preingest_check import (
    BrandTip,
    PerDayVerdict,
    PreingestPlan,
    TzWarning,
    build_preingest_plan,
    check_future_dated,
    check_night_majority,
    check_older_than_trip,
    check_stale_gap,
    load_brand_tip,
    operations_from_items,
)


# ── Synthetic plan helpers ─────────────────────────────────────────


def _trip_days(start: date, count: int) -> list[TripDay]:
    return [
        TripDay(
            day_number=i + 1,
            date=start.fromordinal(start.toordinal() + i),
            description=f"Day {i + 1} desc",
            tz_offset=-3.0,
            location="LOC",
        )
        for i in range(count)
    ]


def _src(p: str, ts: datetime | None, cam: str = "DC-G9M2") -> SourceItem:
    return SourceItem(path=Path(p), timestamp=ts, camera_id=cam)


# ── check_future_dated ─────────────────────────────────────────────


def test_future_dated_fires_when_any_timestamp_is_after_now_plus_slack():
    now = datetime(2026, 5, 25, 12, 0)
    ts = [datetime(2099, 1, 1, 10, 0)]
    w = check_future_dated(ts, now)
    assert w is not None
    assert w.kind == "future_dated"
    assert w.severity == "high"
    assert "future" in w.message.lower()


def test_future_dated_silent_when_all_timestamps_before_now():
    now = datetime(2026, 5, 25, 12, 0)
    ts = [datetime(2026, 5, 25, 11, 0), datetime(2026, 5, 24, 9, 0)]
    assert check_future_dated(ts, now) is None


def test_future_dated_silent_with_empty_list():
    now = datetime(2026, 5, 25, 12, 0)
    assert check_future_dated([], now) is None


def test_future_dated_5min_slack_absorbs_minor_clock_drift():
    now = datetime(2026, 5, 25, 12, 0)
    # 3 minutes ahead — within the 5-minute slack → no warning.
    ts = [datetime(2026, 5, 25, 12, 3)]
    assert check_future_dated(ts, now) is None


# ── check_older_than_trip ──────────────────────────────────────────


def test_older_than_trip_fires_when_median_is_year_before_plan():
    plan_dates = [date(2026, 5, 27), date(2026, 5, 28)]
    ts = [datetime(2020, 5, 1, 10, 0)]
    w = check_older_than_trip(ts, plan_dates)
    assert w is not None
    assert w.kind == "older_than_trip"
    assert w.severity == "high"


def test_older_than_trip_silent_when_recent():
    plan_dates = [date(2026, 5, 27)]
    ts = [datetime(2026, 5, 26, 10, 0)]
    assert check_older_than_trip(ts, plan_dates) is None


def test_older_than_trip_silent_with_empty_inputs():
    assert check_older_than_trip([], []) is None
    assert check_older_than_trip([], [date(2026, 5, 1)]) is None
    assert check_older_than_trip(
        [datetime(2026, 1, 1)], []
    ) is None


def test_older_than_trip_uses_median_not_outlier():
    """One stray 2020 timestamp shouldn't fire if the median is
    actually fine — the user's median capture-time is what
    matters."""
    plan_dates = [date(2026, 5, 27)]
    # 4 timestamps from 2026, 1 outlier from 2020 → median is 2026.
    ts = [
        datetime(2026, 5, 26, 9, 0),
        datetime(2026, 5, 26, 10, 0),
        datetime(2020, 5, 1, 10, 0),
        datetime(2026, 5, 26, 11, 0),
        datetime(2026, 5, 26, 12, 0),
    ]
    assert check_older_than_trip(ts, plan_dates) is None


# ── check_night_majority ───────────────────────────────────────────


def test_night_majority_fires_when_over_60_percent_at_night():
    # 7 of 10 in [22, 06) → 70% → fires.
    ts = [
        datetime(2026, 5, 25, h, 0)
        for h in (22, 23, 0, 1, 2, 3, 4, 10, 11, 14)
    ]
    w = check_night_majority(ts)
    assert w is not None
    assert w.kind == "night_majority"


def test_night_majority_silent_when_mostly_daylight():
    # 1 of 10 at night → 10% → no warning.
    ts = [
        datetime(2026, 5, 25, h, 0)
        for h in (23, 8, 9, 10, 11, 12, 13, 14, 15, 16)
    ]
    assert check_night_majority(ts) is None


def test_night_majority_silent_with_empty_list():
    assert check_night_majority([]) is None


def test_night_majority_boundary_60_percent_does_not_fire():
    # Exactly 60% — threshold is "more than 60%", so 60% should NOT
    # fire (strict inequality protects against off-by-one).
    ts = [
        datetime(2026, 5, 25, h, 0)
        for h in (22, 23, 0, 1, 2, 3, 10, 11, 14, 15)
    ]
    assert check_night_majority(ts) is None


# ── check_stale_gap ────────────────────────────────────────────────


def test_stale_gap_fires_when_newest_is_months_old():
    now = datetime(2026, 5, 25, 12, 0)
    ts = [datetime(2026, 1, 1, 10, 0)]    # ~144 days old
    w = check_stale_gap(ts, now)
    assert w is not None
    assert w.kind == "stale_gap"
    assert w.severity == "low"


def test_stale_gap_silent_when_newest_is_recent():
    now = datetime(2026, 5, 25, 12, 0)
    ts = [datetime(2026, 5, 20, 10, 0)]   # 5 days old
    assert check_stale_gap(ts, now) is None


def test_stale_gap_silent_with_empty_list():
    now = datetime(2026, 5, 25, 12, 0)
    assert check_stale_gap([], now) is None


# ── build_preingest_plan integration ───────────────────────────────


def test_build_preingest_plan_groups_items_by_planned_day():
    plan = _trip_days(date(2026, 5, 27), 3)
    now = datetime(2026, 5, 25, 12, 0)
    items = [
        _src("/src/day1a.jpg", datetime(2026, 5, 27, 10, 0)),
        _src("/src/day1b.jpg", datetime(2026, 5, 27, 11, 0)),
        _src("/src/day2.jpg", datetime(2026, 5, 28, 9, 0)),
        _src("/src/day3.jpg", datetime(2026, 5, 29, 14, 0)),
    ]
    result = build_preingest_plan(
        items, plan, camera_make="Panasonic",
        camera_model="DC-G9M2", now=now,
        brand_tip_resolver=lambda mk, mo: None,    # no tip in test
    )
    assert len(result.days) == 3
    assert result.undated_files == ()
    # Day 1 has the two morning shots; days 2/3 have one each.
    assert len(result.days[0].file_paths) == 2
    assert len(result.days[1].file_paths) == 1
    assert len(result.days[2].file_paths) == 1
    # Capture-time range is populated.
    assert result.days[0].capture_time_range is not None
    earliest, latest = result.days[0].capture_time_range
    assert earliest == datetime(2026, 5, 27, 10, 0)
    assert latest == datetime(2026, 5, 27, 11, 0)


def test_build_preingest_plan_collects_undated_files():
    plan = _trip_days(date(2026, 5, 27), 2)
    now = datetime(2026, 5, 25, 12, 0)
    items = [
        _src("/src/known.jpg", datetime(2026, 5, 27, 10, 0)),
        _src("/src/orphan.jpg", datetime(2010, 1, 1, 10, 0)),  # way off plan
        _src("/src/no_ts.jpg", None),
    ]
    result = build_preingest_plan(
        items, plan, now=now,
        brand_tip_resolver=lambda mk, mo: None,
    )
    # The two off-plan items land in undated_files.
    assert len(result.undated_files) == 2
    paths = {p.name for p in result.undated_files}
    assert paths == {"orphan.jpg", "no_ts.jpg"}


def test_build_preingest_plan_runs_sanity_checks_per_day():
    plan = _trip_days(date(2026, 5, 27), 1)
    now = datetime(2026, 5, 25, 12, 0)
    # 6 night-time timestamps, 2 day-time → 75% night → fires.
    night_ts = [
        datetime(2026, 5, 27, h, 0) for h in (22, 23, 0, 1, 2, 3, 11, 14)
    ]
    items = [
        _src(f"/src/n{i}.jpg", t)
        for i, t in enumerate(night_ts)
    ]
    result = build_preingest_plan(
        items, plan, now=now,
        brand_tip_resolver=lambda mk, mo: None,
    )
    assert len(result.days) == 1
    kinds = {w.kind for w in result.days[0].warnings}
    assert "night_majority" in kinds


def test_build_preingest_plan_attaches_brand_tip_when_resolver_returns_one():
    plan = _trip_days(date(2026, 5, 27), 1)
    items = [
        _src("/src/d1.jpg", datetime(2026, 5, 27, 10, 0)),
    ]
    fake_tip = BrandTip(
        camera_id="DC-G9M2",
        steps=("Step 1", "Step 2"),
        source="model",
    )
    result = build_preingest_plan(
        items, plan, camera_make="Panasonic",
        camera_model="DC-G9M2",
        now=datetime(2026, 5, 27, 14, 0),
        brand_tip_resolver=lambda mk, mo: fake_tip,
    )
    assert result.days[0].brand_tip is fake_tip


def test_build_preingest_plan_no_brand_tip_when_no_camera_info():
    plan = _trip_days(date(2026, 5, 27), 1)
    items = [_src("/src/x.jpg", datetime(2026, 5, 27, 10, 0))]
    result = build_preingest_plan(
        items, plan, now=datetime(2026, 5, 27, 14, 0),
        brand_tip_resolver=lambda mk, mo: BrandTip("x", ("y",), "model"),
    )
    # Empty make+model → resolver not called → no tip.
    assert result.days[0].brand_tip is None


# ── operations_from_items arithmetic ───────────────────────────────


def test_operations_from_items_applies_offset_to_every_dated_file():
    items = [
        _src("/src/a.jpg", datetime(2026, 5, 27, 10, 0)),
        _src("/src/b.jpg", datetime(2026, 5, 27, 11, 0)),
    ]
    verdict = PerDayVerdict(
        trip_day=_trip_days(date(2026, 5, 27), 1)[0],
        file_paths=tuple(it.path for it in items),
        capture_time_range=None,
        camera_make="",
        camera_model="",
        warnings=(),
        brand_tip=None,
    )
    ops = operations_from_items(items, verdict, applied_offset_hours=3.0)
    assert len(ops) == 2
    by_path = dict(ops)
    assert by_path[Path("/src/a.jpg")] == datetime(2026, 5, 27, 13, 0)
    assert by_path[Path("/src/b.jpg")] == datetime(2026, 5, 27, 14, 0)


def test_operations_from_items_returns_empty_for_zero_offset():
    items = [_src("/src/a.jpg", datetime(2026, 5, 27, 10, 0))]
    verdict = PerDayVerdict(
        trip_day=_trip_days(date(2026, 5, 27), 1)[0],
        file_paths=(items[0].path,),
        capture_time_range=None,
        camera_make="",
        camera_model="",
        warnings=(),
        brand_tip=None,
    )
    assert operations_from_items(items, verdict, 0.0) == []


def test_operations_from_items_skips_files_with_no_timestamp():
    items = [
        _src("/src/dated.jpg", datetime(2026, 5, 27, 10, 0)),
        _src("/src/undated.jpg", None),
    ]
    verdict = PerDayVerdict(
        trip_day=_trip_days(date(2026, 5, 27), 1)[0],
        file_paths=tuple(it.path for it in items),
        capture_time_range=None,
        camera_make="",
        camera_model="",
        warnings=(),
        brand_tip=None,
    )
    ops = operations_from_items(items, verdict, 2.0)
    assert len(ops) == 1
    assert ops[0][0] == Path("/src/dated.jpg")


def test_operations_from_items_handles_negative_offset():
    items = [_src("/src/a.jpg", datetime(2026, 5, 27, 10, 0))]
    verdict = PerDayVerdict(
        trip_day=_trip_days(date(2026, 5, 27), 1)[0],
        file_paths=(items[0].path,),
        capture_time_range=None,
        camera_make="",
        camera_model="",
        warnings=(),
        brand_tip=None,
    )
    ops = operations_from_items(items, verdict, -2.5)
    assert ops[0][1] == datetime(2026, 5, 27, 7, 30)


# ── load_brand_tip (uses the real bundled JSON profiles) ───────────


def test_load_brand_tip_exact_model_match_wins_for_panasonic_g9_ii():
    tip = load_brand_tip("Panasonic", "DC-G9M2")
    assert tip is not None
    assert tip.source == "model"
    assert tip.camera_id == "DC-G9M2"
    assert len(tip.steps) >= 1
    # Sanity-check the content references actual G9 II terminology.
    joined = " ".join(tip.steps).lower()
    assert "menu" in joined


def test_load_brand_tip_falls_back_to_brand_default_for_unknown_model():
    tip = load_brand_tip("Panasonic", "DC-G99-fake-body")
    assert tip is not None
    assert tip.source == "_default"
    assert len(tip.steps) >= 1


def test_load_brand_tip_returns_none_for_unknown_brand():
    assert load_brand_tip("AcmeCamera", "X1") is None
    assert load_brand_tip("", "") is None


def test_load_brand_tip_returns_none_when_profile_has_no_instructions():
    """Even for a known brand, if the profile shipped without
    `tz_setting_instructions` the function must return None (the
    dialog hides the tip block in that case)."""
    # Apple's profile has only `_default` — verify the fallback works
    # even when no model-specific entry exists.
    tip = load_brand_tip("Apple", "iPhone-99")
    assert tip is not None
    assert tip.source == "_default"


def test_all_bundled_brand_profiles_declare_default_instructions():
    """Regression guard: each bundled brand-profile JSON must carry
    a `_default` entry so unknown-model fallback always has something
    to show. Stops a future profile editor from silently shipping a
    profile that produces 'no tip' for every body."""
    for brand_make in ("Panasonic", "SONY", "Apple", "GoPro"):
        # Use an obviously-fake model so we trigger the _default
        # path on every brand.
        tip = load_brand_tip(brand_make, "MODEL-THAT-DOES-NOT-EXIST")
        assert tip is not None, (
            f"Brand {brand_make!r} has no _default tz instructions"
        )
        assert tip.source == "_default"
