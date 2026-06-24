"""Tests for core.fresh_source (Stage B.3b, increment 1).

Proves the fresh-source adapter groups a flat folder into the
navigator's DayFolder shape using the brain (assign_days):
per-camera TZ correction pulls a midnight frame onto the right Dia
(the Nepal day-shift), an uncalibrated camera / phone passes
through, off-plan & no-timestamp files fall into a trailing Undated
day, day rows are ordered by plan day_number with plan-derived
keys, and an empty / missing source is [] (never raises).

`read_exif_batch` is monkeypatched so the grouping logic is tested
without real camera metadata; the files on disk are real so the
folder walk is exercised for true.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from core.clock_calibration import CameraCalibration
from core.day_assignment import UNDATED_LABEL
from core.exif_reader import PhotoExif
from core.models import TripDay
from core.fresh_source import (
    SourceItem,
    build_tz_calibrations,
    camera_id_for,
    cameras_in,
    group_items_to_days,
    plan_trip_tz,
    read_source_items,
    scan_fresh_source_days,
)


def _td(day, tz):
    return TripDay(day_number=day, date=date(2025, 10, day),
                   description="", tz_offset=tz)


def test_plan_trip_tz_single_zone():
    assert plan_trip_tz([_td(1, 5.75), _td(2, 5.75), _td(3, 5.75)]) \
        == 5.75


def test_plan_trip_tz_predominant_wins():
    days = [_td(1, -3.0), _td(2, 5.75), _td(3, 5.75), _td(4, 5.75)]
    assert plan_trip_tz(days) == 5.75


def test_plan_trip_tz_tie_goes_to_earliest_day():
    days = [_td(2, 9.0), _td(1, -3.0)]      # 1 each → earliest day_num
    assert plan_trip_tz(days) == -3.0


def test_plan_trip_tz_no_offsets_is_zero():
    assert plan_trip_tz([_td(1, None), _td(2, None)]) == 0.0
    assert plan_trip_tz([]) == 0.0

_G9 = {"Make": "Panasonic", "Model": "DC-G9M2"}
_PHONE = {"Make": "Apple", "Model": "iPhone 15"}


def _days():
    return [
        TripDay(day_number=9, date=date(2026, 4, 20),
                description="Manuel Antonio National Park"),
        TripDay(day_number=10, date=date(2026, 4, 21),
                description="Departure"),
    ]


def _touch(folder: Path, name: str) -> Path:
    p = folder / name
    p.write_bytes(b"\xff\xd8\xff\xe0jpegish")
    return p


def _patch_exif(monkeypatch, mapping: dict[str, tuple]):
    """mapping: filename -> (timestamp|None, raw_dict). Patched on
    core.exif_reader where scan_fresh_source_days imports it lazily."""
    def fake(files):
        return [
            PhotoExif(path=Path(f), timestamp=mapping[Path(f).name][0],
                      raw=mapping[Path(f).name][1])
            for f in files
        ]
    monkeypatch.setattr("core.exif_reader.read_exif_batch", fake)


def test_parse_duration_seconds_formats():
    from core.exif_reader import _parse_duration_seconds as pd
    assert pd("0:00:30") == 30.0       # H:MM:SS
    assert pd("0:01:23") == 83.0
    assert pd("1:02:03") == 3723.0
    assert pd("30.00 s") == 30.0       # "N.NN s"
    assert pd("12.5") == 12.5          # bare numeric
    assert pd("") == 0.0
    assert pd(None) == 0.0
    assert pd("garbage") == 0.0


def test_read_source_items_carries_video_duration_ms(tmp_path, monkeypatch):
    """A video's running time (PhotoExif.duration_seconds, from the single EXIF pass)
    rides onto the SourceItem as duration_ms so ingest can persist it."""
    src = tmp_path / "card"
    src.mkdir()
    (src / "clip.mp4").write_bytes(b"x")

    def fake(files):
        return [PhotoExif(path=Path(f), timestamp=None, model="HERO12",
                          duration_seconds=12.5, raw={"Model": "HERO12"})
                for f in files]
    monkeypatch.setattr("core.exif_reader.read_exif_batch", fake)

    items = read_source_items(src)
    assert items and items[0].duration_ms == 12500


def test_camera_id_for_uses_model_only():
    """Model alone is the canonical id (Nelson 2026-05-20: the same
    GoPro body writes Make on stills but not on videos — joining
    them produced two distinct ids for one camera). Make is only the
    fallback when Model is missing."""
    assert camera_id_for(_G9) == "DC-G9M2"
    assert camera_id_for(_PHONE) == "iPhone 15"
    assert camera_id_for({"Model": "DC-G9M2"}) == "DC-G9M2"
    # Make-only fallback (rare; video-only cameras with no Model tag).
    assert camera_id_for({"Make": "GoPro"}) == "GoPro"
    assert camera_id_for({}) == ""


def test_camera_id_for_collapses_make_present_vs_absent():
    """The regression Nelson hit on Chapada: the same GoPro body
    writes Make+Model on stills but only Model on video. Both must
    produce the SAME camera id."""
    still = {"Make": "GoPro", "Model": "HERO12 Black"}
    video = {"Model": "HERO12 Black"}
    assert camera_id_for(still) == camera_id_for(video) == "HERO12 Black"


def test_missing_or_empty_source_is_empty_list(tmp_path):
    assert scan_fresh_source_days(
        tmp_path / "nope", _days(), {}) == []
    (tmp_path / "empty").mkdir()
    assert scan_fresh_source_days(
        tmp_path / "empty", _days(), {}) == []


def test_groups_into_plan_days_ordered_with_plan_keys(
    tmp_path, monkeypatch,
):
    src = tmp_path / "card"
    src.mkdir()
    a = _touch(src, "a.jpg")   # Dia 10
    b = _touch(src, "b.jpg")   # Dia 9
    c = _touch(src, "c.jpg")   # Dia 9
    _patch_exif(monkeypatch, {
        "a.jpg": (datetime(2026, 4, 21, 9, 0), _PHONE),
        "b.jpg": (datetime(2026, 4, 20, 8, 0), _PHONE),
        "c.jpg": (datetime(2026, 4, 20, 18, 0), _PHONE),
    })
    days = scan_fresh_source_days(src, _days(), {})
    # Dated days ordered by plan day_number; key == day_folder_name.
    assert [d.key for d in days] == [
        "Dia 9 - 2026-04-20 - Manuel Antonio National Park",
        "Dia 10 - 2026-04-21 - Departure",
    ]
    assert [d.label for d in days] == [d.key for d in days]
    d9 = days[0]
    assert d9.files == (b, c)            # sorted within the day
    assert days[1].files == (a,)
    assert all(d.style_mix == () for d in days)


def test_per_camera_tz_pulls_midnight_frame_to_right_dia(
    tmp_path, monkeypatch,
):
    """Nepal day-shift: a camera left on a prior trip's clock
    (-3h here) reads 21 Apr 01:30 for a frame actually shot 20 Apr
    22:30 — calibration must file it under Dia 9, not Dia 10."""
    src = tmp_path / "card"
    src.mkdir()
    shot = _touch(src, "g9.jpg")
    _patch_exif(monkeypatch, {
        "g9.jpg": (datetime(2026, 4, 21, 1, 30), _G9),
    })
    cal = {camera_id_for(_G9): CameraCalibration(
        camera_id="g9", offset_seconds=-3 * 3600)}
    days = scan_fresh_source_days(src, _days(), cal)
    assert len(days) == 1
    assert days[0].key == "Dia 9 - 2026-04-20 - Manuel Antonio National Park"

    # Same frame, NO calibration for that camera key → passes
    # through uncorrected → mis-files to Dia 10 (proves the brain,
    # not raw EXIF, did the work above).
    days_raw = scan_fresh_source_days(src, _days(), {})
    assert days_raw[0].key == "Dia 10 - 2026-04-21 - Departure"


def test_uncalibrated_camera_in_nonempty_map_passes_through(
    tmp_path, monkeypatch,
):
    """A camera with no entry in a non-empty calibration map (e.g.
    the phone alongside a calibrated camera) is pass-through."""
    src = tmp_path / "card"
    src.mkdir()
    _touch(src, "p.jpg")
    _patch_exif(monkeypatch, {
        "p.jpg": (datetime(2026, 4, 20, 10, 0), _PHONE),
    })
    cal = {camera_id_for(_G9): CameraCalibration(
        camera_id="g9", offset_seconds=-3 * 3600)}
    days = scan_fresh_source_days(src, _days(), cal)
    assert days[0].key == "Dia 9 - 2026-04-20 - Manuel Antonio National Park"


def test_undated_and_offplan_collect_in_trailing_day(
    tmp_path, monkeypatch,
):
    src = tmp_path / "card"
    src.mkdir()
    ok = _touch(src, "ok.jpg")        # in plan → Dia 9
    no_ts = _touch(src, "nots.jpg")   # no timestamp → Undated
    off = _touch(src, "off.jpg")      # date matches no Dia → Undated
    _patch_exif(monkeypatch, {
        "ok.jpg": (datetime(2026, 4, 20, 9, 0), _PHONE),
        "nots.jpg": (None, _PHONE),
        "off.jpg": (datetime(2030, 1, 1, 9, 0), _PHONE),
    })
    days = scan_fresh_source_days(src, _days(), {})
    assert days[0].key == "Dia 9 - 2026-04-20 - Manuel Antonio National Park"
    assert days[-1].key == UNDATED_LABEL          # trailing
    assert days[-1].label == UNDATED_LABEL
    assert set(days[-1].files) == {no_ts, off}


def test_never_raises_on_bad_timestamp(tmp_path, monkeypatch):
    src = tmp_path / "card"
    src.mkdir()
    _touch(src, "x.jpg")
    _patch_exif(monkeypatch, {"x.jpg": (None, {})})
    # No camera id, no timestamp, empty plan — must degrade, not raise.
    out = scan_fresh_source_days(src, [], {})
    assert out and out[0].key == UNDATED_LABEL


# ── B.3b inc.3a: composable parts for the clock dialog ────────────

def test_read_source_items_single_read(tmp_path, monkeypatch):
    src = tmp_path / "card"
    src.mkdir()
    g = _touch(src, "g.jpg")
    p = _touch(src, "p.jpg")
    _patch_exif(monkeypatch, {
        "g.jpg": (datetime(2026, 4, 20, 8, 0), _G9),
        "p.jpg": (None, _PHONE),
    })
    items = read_source_items(src)
    by_name = {it.path.name: it for it in items}
    assert by_name["g.jpg"] == SourceItem(
        path=g, timestamp=datetime(2026, 4, 20, 8, 0),
        camera_id="DC-G9M2")
    assert by_name["p.jpg"].timestamp is None
    assert by_name["p.jpg"].camera_id == "iPhone 15"
    assert read_source_items(tmp_path / "nope") == []


def test_cameras_in_counts_orders_and_drops_blank():
    items = [
        SourceItem(Path("a"), None, "iPhone 15"),
        SourceItem(Path("b"), None, "DC-G9M2"),
        SourceItem(Path("c"), None, "DC-G9M2"),
        SourceItem(Path("d"), None, ""),          # blank → dropped
    ]
    # Most-shots-first, then name; the blank-id file is omitted.
    assert cameras_in(items) == [
        "DC-G9M2", "iPhone 15"]


def test_build_tz_calibrations_only_problem_cameras():
    # Nelson's model: only the cameras the user said were WRONG get
    # an entry; trip Nepal +5.75, camera left on São Paulo -3.
    cals = build_tz_calibrations(
        {"DC-G9M2": -3.0}, trip_tz=5.75)
    assert set(cals) == {"DC-G9M2"}
    assert cals["DC-G9M2"].tz_offset == \
        timedelta(hours=8.75)
    # A correct-clock camera is simply absent → pass-through.
    assert build_tz_calibrations({}, trip_tz=5.75) == {}


def test_group_items_to_days_is_pure_and_applies_calibration():
    """No disk, no EXIF read — pure grouping over pre-read items.
    The G9 frame reads 21 Apr 01:30; with the -3h→+? calibration it
    must land on Dia 9, the phone on Dia 9 too; ordered, Undated
    last."""
    cal = {camera_id_for(_G9): CameraCalibration(
        camera_id="g9", offset_seconds=-3 * 3600)}
    items = [
        SourceItem(Path("g9.jpg"), datetime(2026, 4, 21, 1, 30),
                   camera_id_for(_G9)),
        SourceItem(Path("ph.jpg"), datetime(2026, 4, 20, 12, 0),
                   camera_id_for(_PHONE)),
        SourceItem(Path("no.jpg"), None, camera_id_for(_PHONE)),
    ]
    days = group_items_to_days(items, _days(), cal)
    assert [d.key for d in days] == [
        "Dia 9 - 2026-04-20 - Manuel Antonio National Park", UNDATED_LABEL]
    assert set(days[0].files) == {Path("g9.jpg"), Path("ph.jpg")}
    assert days[-1].files == (Path("no.jpg"),)
    assert group_items_to_days([], _days(), {}) == []
