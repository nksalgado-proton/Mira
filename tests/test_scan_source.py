"""Tests for ``core.scan_source`` — spec/52 §2 / slice E.2.

Pure-logic coverage of :func:`build_scan_result` with synthesized
``PhotoExif`` inputs (no real EXIF, no ExifTool). The wrapper
:func:`scan_source` is exercised via a single round-trip test that
monkeypatches the EXIF + walk seams so the test stays hermetic.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import List

import pytest

from core import autofill as _autofill
from core import scan_source
from core.autofill import DayAutofill
from core.exif_reader import PhotoExif
from core.peek_select import PeekCandidate
from core.scan_source import PhoneScanSummary, ScanResult, build_scan_result
from core.tz_calibration import CameraDayPresence
from core.scan_source import ScanDayRow


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _photo(
    name: str,
    *,
    at: str = "2026-04-01 10:00:00",
    make: str = "Sony",
    model: str = "ILCE-7M5",
    tz_minutes: int | None = None,
    gps: tuple[float, float] | None = None,
    duration_seconds: float = 0.0,
    parent: str = "/scan",
) -> PhotoExif:
    raw = {"Make": make} if make else {}
    if gps is None:
        gps_lat = None
        gps_lon = None
    else:
        gps_lat, gps_lon = gps
    return PhotoExif(
        path=Path(parent) / name,
        timestamp=datetime.fromisoformat(at),
        model=model,
        tz_offset_minutes=tz_minutes,
        gps_lat=gps_lat,
        gps_lon=gps_lon,
        duration_seconds=duration_seconds,
        raw=raw,
    )


@pytest.fixture
def stub_autofill(monkeypatch):
    """Replace autofill_for_day with a stub that returns an empty
    DayAutofill — keeps tests fast + deterministic + skips reverse
    geocoding. Tests that want to verify autofill plumbing override
    this in-place."""
    monkeypatch.setattr(
        _autofill, "autofill_for_day",
        lambda photos, *, source_root=None,
            home_country=None, home_tz_minutes=None: DayAutofill(),
    )


# --------------------------------------------------------------------------- #
# Empty + trivial input
# --------------------------------------------------------------------------- #


def test_empty_photos_returns_empty_result(stub_autofill):
    out = build_scan_result([], source_root=Path("/scan"))
    assert isinstance(out, ScanResult)
    assert out.scan_rows == []
    assert out.candidates_by_date == {}
    assert out.day_date_lookup == {}
    assert out.day_tz_lookup == {}
    assert out.presences == []
    assert out.total_photos == 0
    assert out.untimestamped_count == 0


def test_single_photo_produces_single_day(stub_autofill):
    photos = [_photo("IMG_1.JPG", at="2026-04-12 10:00:00")]
    out = build_scan_result(photos, source_root=Path("/scan"))
    assert len(out.scan_rows) == 1
    assert out.scan_rows[0].date.isoformat() == "2026-04-12"
    assert out.scan_rows[0].checked is True
    assert out.day_date_lookup == {1: out.scan_rows[0].date}


# --------------------------------------------------------------------------- #
# Day grouping + numbering
# --------------------------------------------------------------------------- #


def test_multiple_photos_same_day_collapse(stub_autofill):
    photos = [
        _photo("A.JPG", at="2026-04-12 08:00:00"),
        _photo("B.JPG", at="2026-04-12 14:00:00"),
        _photo("C.JPG", at="2026-04-12 20:00:00"),
    ]
    out = build_scan_result(photos, source_root=Path("/scan"))
    assert len(out.scan_rows) == 1


def test_multiple_days_emit_chronological_rows(stub_autofill):
    photos = [
        _photo("C.JPG", at="2026-04-14 10:00:00"),
        _photo("A.JPG", at="2026-04-12 10:00:00"),
        _photo("B.JPG", at="2026-04-13 10:00:00"),
    ]
    out = build_scan_result(photos, source_root=Path("/scan"))
    dates = [r.date.isoformat() for r in out.scan_rows]
    assert dates == ["2026-04-12", "2026-04-13", "2026-04-14"]


def test_day_numbering_is_one_based_chronological(stub_autofill):
    photos = [
        _photo("A.JPG", at="2026-04-12 10:00:00"),
        _photo("B.JPG", at="2026-04-13 10:00:00"),
        _photo("C.JPG", at="2026-04-15 10:00:00"),
    ]
    out = build_scan_result(photos, source_root=Path("/scan"))
    assert out.day_date_lookup[1].isoformat() == "2026-04-12"
    assert out.day_date_lookup[2].isoformat() == "2026-04-13"
    assert out.day_date_lookup[3].isoformat() == "2026-04-15"


# --------------------------------------------------------------------------- #
# Untimestamped photos
# --------------------------------------------------------------------------- #


def test_untimestamped_photos_are_counted_not_grouped(stub_autofill):
    photos = [
        _photo("A.JPG", at="2026-04-12 10:00:00"),
        PhotoExif(path=Path("/scan/B.JPG"), timestamp=None),
        PhotoExif(path=Path("/scan/C.JPG"), timestamp=None),
    ]
    out = build_scan_result(photos, source_root=Path("/scan"))
    assert out.total_photos == 3
    assert out.untimestamped_count == 2
    assert len(out.scan_rows) == 1


# --------------------------------------------------------------------------- #
# Phone vs camera detection in presence list
# --------------------------------------------------------------------------- #


def test_phone_photo_yields_presence_with_is_phone_true(stub_autofill):
    photos = [_photo(
        "IMG.JPG", make="Apple", model="iPhone 15 Pro",
        at="2026-04-12 10:00:00",
    )]
    out = build_scan_result(photos, source_root=Path("/scan"))
    assert len(out.presences) == 1
    assert out.presences[0].is_phone is True
    assert out.presences[0].camera_id == "iPhone 15 Pro"


def test_camera_photo_yields_presence_with_is_phone_false(stub_autofill):
    photos = [_photo(
        "DSC_1.ARW", make="Sony", model="ILCE-7M5",
        at="2026-04-12 10:00:00",
    )]
    out = build_scan_result(photos, source_root=Path("/scan"))
    assert len(out.presences) == 1
    assert out.presences[0].is_phone is False
    assert out.presences[0].camera_id == "ILCE-7M5"


def test_mixed_phone_and_camera_same_day_emit_two_presences(stub_autofill):
    photos = [
        _photo("IMG.HEIC", make="Apple", model="iPhone 15 Pro",
               at="2026-04-12 10:00:00"),
        _photo("DSC.ARW", make="Sony", model="ILCE-7M5",
               at="2026-04-12 12:00:00"),
    ]
    out = build_scan_result(photos, source_root=Path("/scan"))
    assert len(out.presences) == 2
    by_camera = {p.camera_id: p for p in out.presences}
    assert by_camera["iPhone 15 Pro"].is_phone is True
    assert by_camera["ILCE-7M5"].is_phone is False


def test_same_camera_different_days_emit_two_presences(stub_autofill):
    """spec/52 §8.4 border-crossing — same camera, two days, two
    presence rows."""
    photos = [
        _photo("A.ARW", model="ILCE-7M5", at="2026-04-12 10:00:00"),
        _photo("B.ARW", model="ILCE-7M5", at="2026-04-13 10:00:00"),
    ]
    out = build_scan_result(photos, source_root=Path("/scan"))
    assert len(out.presences) == 2
    by_day = {p.day_number: p for p in out.presences}
    assert by_day[1].camera_id == "ILCE-7M5"
    assert by_day[2].camera_id == "ILCE-7M5"


def test_same_camera_same_day_dedups_to_one_presence(stub_autofill):
    photos = [
        _photo("A.JPG", model="ILCE-7M5", at="2026-04-12 08:00:00"),
        _photo("B.JPG", model="ILCE-7M5", at="2026-04-12 14:00:00"),
        _photo("C.JPG", model="ILCE-7M5", at="2026-04-12 20:00:00"),
    ]
    out = build_scan_result(photos, source_root=Path("/scan"))
    assert len(out.presences) == 1


def test_empty_camera_id_yields_no_presence(stub_autofill):
    """A photo with no readable Make/Model still belongs to the day
    (counted in total_photos, appears in candidates_by_date) but
    can't anchor a presence row — there's no camera_id to key."""
    photos = [_photo("X.JPG", make="", model="", at="2026-04-12 10:00:00")]
    out = build_scan_result(photos, source_root=Path("/scan"))
    assert out.presences == []
    # But the candidate still appears.
    assert len(out.candidates_by_date) == 1


def test_presences_ordered_by_day_then_camera(stub_autofill):
    photos = [
        _photo("A.JPG", model="ZZZ", at="2026-04-13 10:00:00"),
        _photo("B.JPG", model="AAA", at="2026-04-12 10:00:00"),
        _photo("C.JPG", model="MMM", at="2026-04-12 10:00:00"),
    ]
    out = build_scan_result(photos, source_root=Path("/scan"))
    keys = [(p.day_number, p.camera_id) for p in out.presences]
    assert keys == [(1, "AAA"), (1, "MMM"), (2, "ZZZ")]


# --------------------------------------------------------------------------- #
# Autofill plumbing — the day's autofill flows into ScanDayRow + day_tz_lookup
# --------------------------------------------------------------------------- #


def test_autofill_country_tz_location_flow_into_scan_row(monkeypatch):
    """The autofill engine produces per-day values; scan_source must
    plumb them into the ScanDayRow + day_tz_lookup."""
    expected = DayAutofill(
        country_code="PT",
        tz_minutes=60,
        location="Lisbon, Portugal",
        description="Lisbon, Portugal",
        country_source="phone_exif",
        tz_source="phone_exif",
        location_source="phone_exif",
        description_source="phone_exif",
    )

    def stub(photos, *, source_root=None, home_country=None, home_tz_minutes=None):
        return expected

    monkeypatch.setattr(_autofill, "autofill_for_day", stub)

    photos = [_photo("IMG.HEIC", at="2026-04-12 10:00:00")]
    out = build_scan_result(photos, source_root=Path("/scan"))

    row = out.scan_rows[0]
    assert row.country_code == "PT"
    assert row.tz_minutes == 60
    assert row.location == "Lisbon, Portugal"
    assert row.description == "Lisbon, Portugal"
    assert out.day_tz_lookup[1] == 60


def test_autofill_none_values_become_empty_strings_and_none_tz(monkeypatch):
    monkeypatch.setattr(
        _autofill, "autofill_for_day",
        lambda photos, *, source_root=None,
            home_country=None, home_tz_minutes=None: DayAutofill(),    # all None
    )
    photos = [_photo("A.JPG", at="2026-04-12 10:00:00")]
    out = build_scan_result(photos, source_root=Path("/scan"))
    row = out.scan_rows[0]
    assert row.country_code == ""
    assert row.tz_minutes is None
    assert row.location == ""
    assert row.description == ""
    assert out.day_tz_lookup[1] is None


def test_autofill_receives_source_root(monkeypatch):
    """The subdir-name autofill needs source_root — verify scan_source
    passes it through."""
    captured = {}

    def stub(photos, *, source_root=None,
             home_country=None, home_tz_minutes=None):
        captured["source_root"] = source_root
        return DayAutofill()

    monkeypatch.setattr(_autofill, "autofill_for_day", stub)

    build_scan_result(
        [_photo("A.JPG", at="2026-04-12 10:00:00")],
        source_root=Path("/the/scan/root"),
    )
    assert captured["source_root"] == Path("/the/scan/root")


# --------------------------------------------------------------------------- #
# Candidates per day
# --------------------------------------------------------------------------- #


def test_candidates_by_date_groups_per_day(stub_autofill):
    photos = [
        _photo("A.JPG", at="2026-04-12 08:00:00"),
        _photo("B.JPG", at="2026-04-12 20:00:00"),
        _photo("C.JPG", at="2026-04-13 12:00:00"),
    ]
    out = build_scan_result(photos, source_root=Path("/scan"))
    assert len(out.candidates_by_date) == 2
    day12 = [d for d in out.candidates_by_date if d.isoformat() == "2026-04-12"][0]
    day13 = [d for d in out.candidates_by_date if d.isoformat() == "2026-04-13"][0]
    assert len(out.candidates_by_date[day12]) == 2
    assert len(out.candidates_by_date[day13]) == 1


def test_candidates_carry_timestamps_for_time_spread(stub_autofill):
    photos = [_photo("A.JPG", at="2026-04-12 14:30:00")]
    out = build_scan_result(photos, source_root=Path("/scan"))
    cand: PeekCandidate = next(iter(out.candidates_by_date.values()))[0]
    assert cand.timestamp == datetime(2026, 4, 12, 14, 30)


def test_video_photos_carry_is_video_true(stub_autofill):
    photos = [
        _photo("STILL.JPG", at="2026-04-12 10:00:00"),
        _photo("CLIP.MP4", at="2026-04-12 11:00:00", duration_seconds=12.0),
    ]
    out = build_scan_result(photos, source_root=Path("/scan"))
    cands = next(iter(out.candidates_by_date.values()))
    by_name = {c.path.name: c for c in cands}
    assert by_name["STILL.JPG"].is_video is False
    assert by_name["CLIP.MP4"].is_video is True


def test_candidates_byte_size_left_as_unknown(stub_autofill):
    """scan_source doesn't stat() — leaves byte_size=0 so the peek
    selector treats every file as eligible. The host can stat lazily."""
    photos = [_photo("A.JPG", at="2026-04-12 10:00:00")]
    out = build_scan_result(photos, source_root=Path("/scan"))
    cand = next(iter(out.candidates_by_date.values()))[0]
    assert cand.byte_size == 0


# --------------------------------------------------------------------------- #
# day_tz_lookup is sparse-None when autofill couldn't fill
# --------------------------------------------------------------------------- #


def test_day_tz_lookup_has_entry_per_day(stub_autofill):
    photos = [
        _photo("A.JPG", at="2026-04-12 10:00:00"),
        _photo("B.JPG", at="2026-04-13 10:00:00"),
    ]
    out = build_scan_result(photos, source_root=Path("/scan"))
    assert set(out.day_tz_lookup.keys()) == {1, 2}


# --------------------------------------------------------------------------- #
# Wrapper — scan_source(path) → ScanResult via patched walk + EXIF
# --------------------------------------------------------------------------- #


def test_scan_source_returns_empty_for_missing_path(tmp_path):
    out = scan_source.scan_source(tmp_path / "does_not_exist")
    assert out.scan_rows == []
    assert out.total_photos == 0


def test_scan_source_returns_empty_for_empty_directory(tmp_path):
    out = scan_source.scan_source(tmp_path)
    assert out.scan_rows == []


def test_scan_source_round_trips_through_build_scan_result(monkeypatch, tmp_path):
    """End-to-end with the EXIF + walk seams stubbed: a single photo
    flows through walk_photo_paths → read_exif_batch → build_scan_result
    and produces the expected ScanResult."""
    (tmp_path / "IMG.JPG").write_bytes(b"\xff\xd8\xff\xe0")           # JPEG SOI marker
    stub_photo = _photo("IMG.JPG", at="2026-04-12 10:00:00",
                         parent=str(tmp_path))

    monkeypatch.setattr(
        scan_source, "_empty_result",
        lambda: ScanResult(
            scan_rows=[], candidates_by_date={},
            day_date_lookup={}, day_tz_lookup={},
            presences=[], total_photos=0, untimestamped_count=0,
        ),
    )
    import core.exif_reader
    import core.folder_scanner
    monkeypatch.setattr(
        core.folder_scanner, "walk_photo_paths",
        lambda root: [tmp_path / "IMG.JPG"],
    )
    monkeypatch.setattr(
        core.exif_reader, "read_exif_batch",
        lambda files: [stub_photo],
    )
    monkeypatch.setattr(
        _autofill, "autofill_for_day",
        lambda photos, *, source_root=None,
            home_country=None, home_tz_minutes=None: DayAutofill(),
    )

    out = scan_source.scan_source(tmp_path)
    assert len(out.scan_rows) == 1
    assert out.scan_rows[0].date.isoformat() == "2026-04-12"


# --------------------------------------------------------------------------- #
# ScanDayRow type sanity
# --------------------------------------------------------------------------- #


def test_scan_rows_are_scan_day_row_instances(stub_autofill):
    photos = [_photo("A.JPG", at="2026-04-12 10:00:00")]
    out = build_scan_result(photos, source_root=Path("/scan"))
    assert isinstance(out.scan_rows[0], ScanDayRow)


def test_scan_day_row_checked_default_true(stub_autofill):
    photos = [_photo("A.JPG", at="2026-04-12 10:00:00")]
    out = build_scan_result(photos, source_root=Path("/scan"))
    assert out.scan_rows[0].checked is True


# --------------------------------------------------------------------------- #
# per_photo_records + build_ingest_jobs (slice E.5)
# --------------------------------------------------------------------------- #


def test_per_photo_records_populated(stub_autofill):
    photos = [
        _photo("A.JPG", model="ILCE-7M5", at="2026-04-12 10:00:00"),
        _photo("B.JPG", model="ILCE-7M5", at="2026-04-13 10:00:00"),
    ]
    out = build_scan_result(photos, source_root=Path("/scan"))
    assert len(out.per_photo_records) == 2
    rec = out.per_photo_records[0]
    assert rec.camera_id == "ILCE-7M5"
    assert rec.is_phone is False
    assert rec.day_number == 1


def test_per_photo_record_for_untimestamped_has_none(stub_autofill):
    from core.exif_reader import PhotoExif
    photos = [PhotoExif(path=Path("/scan/X.JPG"), timestamp=None,
                         model="ILCE-7M5")]
    out = build_scan_result(photos, source_root=Path("/scan"))
    assert len(out.per_photo_records) == 1
    rec = out.per_photo_records[0]
    assert rec.day_number is None
    assert rec.capture_time_raw is None


def test_per_photo_record_marks_phone_correctly(stub_autofill):
    photos = [_photo(
        "IMG.HEIC", make="Apple", model="iPhone 15 Pro",
        at="2026-04-12 10:00:00",
    )]
    out = build_scan_result(photos, source_root=Path("/scan"))
    assert out.per_photo_records[0].is_phone is True


def test_build_ingest_jobs_one_photo_per_day(stub_autofill):
    from core.scan_source import build_ingest_jobs
    from core.scan_source import ScanDayRow

    photos = [
        _photo("A.JPG", model="ILCE-7M5", at="2026-04-12 10:00:00"),
        _photo("B.JPG", model="ILCE-7M5", at="2026-04-13 10:00:00"),
    ]
    scan = build_scan_result(photos, source_root=Path("/scan"))
    accepted = [
        ScanDayRow(date=date(2026, 4, 12), checked=True,
                    tz_minutes=60, description="Day one"),
        ScanDayRow(date=date(2026, 4, 13), checked=True,
                    tz_minutes=60, description="Day two"),
    ]
    jobs = build_ingest_jobs(scan, accepted, calibration_decisions={})
    assert len(jobs) == 2
    by_day = {j.day_number: j for j in jobs}
    assert by_day[1].day_description == "Day one"
    assert by_day[2].day_description == "Day two"


def test_build_ingest_jobs_skips_unchecked_days(stub_autofill):
    from core.scan_source import build_ingest_jobs
    from core.scan_source import ScanDayRow

    photos = [
        _photo("A.JPG", model="ILCE-7M5", at="2026-04-12 10:00:00"),
        _photo("B.JPG", model="ILCE-7M5", at="2026-04-13 10:00:00"),
    ]
    scan = build_scan_result(photos, source_root=Path("/scan"))
    accepted = [
        ScanDayRow(date=date(2026, 4, 12), checked=True, tz_minutes=60),
        ScanDayRow(date=date(2026, 4, 13), checked=False, tz_minutes=60),
    ]
    jobs = build_ingest_jobs(scan, accepted, calibration_decisions={})
    assert len(jobs) == 1
    assert jobs[0].day_number == 1


def test_build_ingest_jobs_untimestamped_always_quarantined(stub_autofill):
    """Even when a day is unchecked, untimestamped photos still go to
    quarantine — the user can't make a per-day decision on a date we
    couldn't read."""
    from core.exif_reader import PhotoExif
    from core.scan_source import build_ingest_jobs
    from core.scan_source import ScanDayRow

    photos = [PhotoExif(path=Path("/scan/X.JPG"), timestamp=None,
                         model="ILCE-7M5")]
    scan = build_scan_result(photos, source_root=Path("/scan"))
    jobs = build_ingest_jobs(scan, accepted_rows=[], calibration_decisions={})
    assert len(jobs) == 1
    assert jobs[0].capture_time_raw is None


def test_build_ingest_jobs_applies_calibration_to_corrected_time(stub_autofill):
    """Spec/52 §8 — calibration says the camera was on TZ X; the day's
    actual TZ is Y; offset = Y - X."""
    from core.scan_source import build_ingest_jobs
    from core.scan_source import ScanDayRow

    photos = [_photo(
        "A.JPG", model="ILCE-7M5", at="2026-04-12 10:00:00",
    )]
    scan = build_scan_result(photos, source_root=Path("/scan"))
    # Day TZ = +05:45 (Nepal, 345 min); camera was set to -03:00 (-180 min)
    # → offset = 345 - (-180) = 525 min = +8:45. Raw 10:00 → corrected 18:45.
    accepted = [ScanDayRow(date=date(2026, 4, 12), checked=True, tz_minutes=345)]
    jobs = build_ingest_jobs(
        scan, accepted,
        calibration_decisions={("ILCE-7M5", 1): -180},
    )
    assert len(jobs) == 1
    assert jobs[0].capture_time_corrected == datetime(2026, 4, 12, 18, 45)


def test_build_ingest_jobs_no_calibration_keeps_raw_time(stub_autofill):
    from core.scan_source import build_ingest_jobs
    from core.scan_source import ScanDayRow

    photos = [_photo(
        "A.JPG", model="ILCE-7M5", at="2026-04-12 10:00:00",
    )]
    scan = build_scan_result(photos, source_root=Path("/scan"))
    accepted = [ScanDayRow(date=date(2026, 4, 12), checked=True, tz_minutes=60)]
    jobs = build_ingest_jobs(scan, accepted, calibration_decisions={})
    assert jobs[0].capture_time_corrected == jobs[0].capture_time_raw


# --------------------------------------------------------------------------- #
# Phone-coverage summary (banner above the day list, Nelson 2026-06-08)
# --------------------------------------------------------------------------- #


def test_phone_summary_zero_days_when_no_photos(stub_autofill):
    out = build_scan_result([], source_root=Path("/scan"))
    assert out.phone_summary == PhoneScanSummary()


def test_phone_summary_full_coverage(stub_autofill):
    """Three days, each with at least one phone photo carrying TZ + GPS."""
    photos = [
        _photo("A.JPG", at="2026-05-01 10:00:00", make="Apple",
               model="iPhone 11", tz_minutes=-180, gps=(-34.6, -58.4)),
        _photo("B.JPG", at="2026-05-02 11:00:00", make="Apple",
               model="iPhone 11", tz_minutes=-180, gps=(-24.7, -65.4)),
        _photo("C.JPG", at="2026-05-03 12:00:00", make="Apple",
               model="iPhone 11", tz_minutes=-180, gps=(-32.9, -68.8)),
    ]
    out = build_scan_result(photos, source_root=Path("/scan"))
    assert out.phone_summary.total_days == 3
    assert out.phone_summary.days_with_phone_photos == 3
    assert out.phone_summary.days_with_phone_tz == 3
    assert out.phone_summary.days_with_phone_gps == 3


def test_phone_summary_gps_gap_only(stub_autofill):
    """Argentina-ish case: phones on all days, TZ on all days, GPS on
    some — the trio of counters tells the user exactly which days will
    need manual country + location."""
    photos = [
        _photo("A.JPG", at="2026-05-01 10:00:00", make="Apple",
               model="iPhone 11", tz_minutes=-180),                  # no GPS
        _photo("B.JPG", at="2026-05-02 11:00:00", make="Apple",
               model="iPhone 11", tz_minutes=-180),                  # no GPS
        _photo("C.JPG", at="2026-05-03 12:00:00", make="Apple",
               model="iPhone 11", tz_minutes=-180, gps=(-24.7, -65.4)),
    ]
    out = build_scan_result(photos, source_root=Path("/scan"))
    assert out.phone_summary.total_days == 3
    assert out.phone_summary.days_with_phone_photos == 3
    assert out.phone_summary.days_with_phone_tz == 3
    assert out.phone_summary.days_with_phone_gps == 1


def test_phone_summary_camera_only_days_excluded(stub_autofill):
    """Days with only camera photos (no phones) don't count toward the
    phone-coverage tallies, but they DO count toward total_days."""
    photos = [
        _photo("CAM.JPG", at="2026-05-01 10:00:00",
               make="Panasonic", model="DC-G9"),
        _photo("PHONE.JPG", at="2026-05-02 11:00:00",
               make="Apple", model="iPhone 11",
               tz_minutes=-180, gps=(-24.7, -65.4)),
    ]
    out = build_scan_result(photos, source_root=Path("/scan"))
    assert out.phone_summary.total_days == 2
    assert out.phone_summary.days_with_phone_photos == 1
    assert out.phone_summary.days_with_phone_tz == 1
    assert out.phone_summary.days_with_phone_gps == 1


# --------------------------------------------------------------------------- #
# spec/57 §4.1 — the day boundary (multi-date split regroup)
# --------------------------------------------------------------------------- #


def test_effective_capture_date_boundary():
    from core.scan_source import effective_capture_date
    ts = datetime.fromisoformat("2026-04-02 00:30:00")
    assert effective_capture_date(ts, 0) == date(2026, 4, 2)
    # 00:30 with a 03:00 boundary belongs to the previous evening.
    assert effective_capture_date(ts, 180) == date(2026, 4, 1)
    assert effective_capture_date(
        datetime.fromisoformat("2026-04-02 03:00:00"), 180) == date(2026, 4, 2)


def test_day_start_minutes_regroups_consistently(stub_autofill):
    photos = [
        _photo("evening.jpg", at="2026-04-01 22:00:00"),
        _photo("night.jpg", at="2026-04-02 00:30:00"),   # the spill-over
        _photo("morning.jpg", at="2026-04-02 09:00:00"),
    ]
    plain = build_scan_result(photos, source_root=Path("/scan"))
    assert [r.date for r in plain.scan_rows] == [date(2026, 4, 1), date(2026, 4, 2)]
    shifted = build_scan_result(
        photos, source_root=Path("/scan"), day_start_minutes=180)
    # Same two days, but the 00:30 shot moved to April 1 everywhere —
    # rows, presences, and the per-photo ingest records agree.
    assert [r.date for r in shifted.scan_rows] == [date(2026, 4, 1), date(2026, 4, 2)]
    by_day = {}
    for rec in shifted.per_photo_records:
        by_day.setdefault(rec.day_number, []).append(rec.source_path.name)
    assert sorted(by_day[1]) == ["evening.jpg", "night.jpg"]
    assert by_day[2] == ["morning.jpg"]
    # The inputs are retained for pure regrouping (spec/57 split preview).
    assert len(shifted.photos) == 3 and shifted.day_start_minutes == 180
    assert shifted.source_root == Path("/scan")
