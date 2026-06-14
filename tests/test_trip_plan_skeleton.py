"""Tests for core.trip_plan_skeleton — per-day-folder driven generation.

The new skeleton walks ``Dia N - LOC`` subfolders, samples one photo
from each (preferring the reference camera) for the date, and emits
plan text that ``parse_trip_plan`` consumes. Tests use real PIL-built
JPEGs with exiftool-stamped EXIF so the EXIF reader is exercised.

Skipped automatically when bundled exiftool isn't present.
"""

from __future__ import annotations

import subprocess
from datetime import date, datetime
from pathlib import Path

import pytest
from PIL import Image

from core.trip_plan_parser import parse_trip_plan
from core.trip_plan_skeleton import (
    discover_day_folders,
    generate_plan_skeleton_from_per_camera,
    generate_plan_skeleton_from_per_day,
)
from core.exif_reader import _get_exiftool_path

pytestmark = pytest.mark.skipif(
    not _get_exiftool_path().exists(),
    reason="bundled exiftool not present; skipping skeleton integration tests",
)


# ── Fixture helpers ──────────────────────────────────────────────


def _make_jpeg(
    path: Path, dto: datetime,
    *, model: str = "iPhone 11", make: str = "Apple",
) -> Path:
    """Create a JPEG with EXIF DateTimeOriginal + Make + Model.
    Make/Model are stamped because the skeleton uses them to pick
    reference-camera photos."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), color=(127, 127, 127)).save(path, "JPEG", quality=90)
    cp = subprocess.run(
        [
            str(_get_exiftool_path()), "-overwrite_original",
            f"-DateTimeOriginal={dto.strftime('%Y:%m:%d %H:%M:%S')}",
            f"-CreateDate={dto.strftime('%Y:%m:%d %H:%M:%S')}",
            f"-Make={make}",
            f"-Model={model}",
            str(path),
        ],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert cp.returncode == 0, cp.stderr
    return path


# ── discover_day_folders ─────────────────────────────────────────


def test_discover_finds_dia_folders_and_ignores_others(tmp_path):
    (tmp_path / "Dia 1 - Katmandu").mkdir()
    (tmp_path / "Dia 2 - Lukla").mkdir()
    (tmp_path / "extras").mkdir()
    (tmp_path / "Snapshots").mkdir()
    folders = discover_day_folders(tmp_path)
    nums = [f.day_number for f in folders]
    locs = [f.location for f in folders]
    assert nums == [1, 2]
    assert "Katmandu" in locs
    assert "Lukla" in locs


def test_discover_handles_double_space_typo(tmp_path):
    """Users sometimes type two spaces between Dia and the dash —
    the regex tolerates that ('Dia 3  - EVH' style)."""
    (tmp_path / "Dia 3  - EVH - Trilha").mkdir()
    folders = discover_day_folders(tmp_path)
    assert len(folders) == 1
    assert folders[0].day_number == 3
    assert folders[0].location == "EVH - Trilha"


def test_discover_returns_empty_when_no_dia_folders(tmp_path):
    (tmp_path / "random").mkdir()
    (tmp_path / "extras").mkdir()
    assert discover_day_folders(tmp_path) == []


def test_discover_returns_empty_for_missing_root(tmp_path):
    """Defensive — caller might pass a path that doesn't exist."""
    assert discover_day_folders(tmp_path / "does_not_exist") == []


# ── generate_plan_skeleton_from_per_day ──────────────────────────


def test_skeleton_simple_three_consecutive_days(tmp_path):
    _make_jpeg(
        tmp_path / "Dia 1 - Katmandu" / "p1.jpg",
        datetime(2025, 10, 26, 10, 0, 0),
    )
    _make_jpeg(
        tmp_path / "Dia 2 - Lukla" / "p2.jpg",
        datetime(2025, 10, 27, 10, 0, 0),
    )
    _make_jpeg(
        tmp_path / "Dia 3 - Khumjung" / "p3.jpg",
        datetime(2025, 10, 28, 10, 0, 0),
    )
    result = generate_plan_skeleton_from_per_day(tmp_path)
    lines = result.plan_text.strip().split("\n")
    assert len(lines) == 3
    # Description pre-populated from folder name, then (DD/MM)
    assert lines[0] == "Dia 1 - Katmandu (26/10)"
    assert lines[1] == "Dia 2 - Lukla (27/10)"
    assert lines[2] == "Dia 3 - Khumjung (28/10)"


def test_skeleton_with_calendar_gaps_preserves_narrative_numbers(tmp_path):
    """The user's narrative numbering wins — gap days (long flights
    with no photos) just don't appear in the skeleton, and the
    calendar dates jump accordingly."""
    _make_jpeg(
        tmp_path / "Dia 1 - Katmandu" / "p1.jpg",
        datetime(2025, 10, 26, 10, 0, 0),
    )
    # 2-day gap — no Dia 2/3 folders; user has no photos those days.
    _make_jpeg(
        tmp_path / "Dia 2 - Lukla" / "p2.jpg",
        datetime(2025, 10, 29, 10, 0, 0),
    )
    result = generate_plan_skeleton_from_per_day(tmp_path)
    lines = result.plan_text.strip().split("\n")
    assert len(lines) == 2
    assert "(26/10)" in lines[0]
    assert "(29/10)" in lines[1]


def test_skeleton_merges_duplicate_dia_numbers(tmp_path):
    """Two folders sharing Dia 6 (split morning/afternoon at the
    same hotel) collapse into one Dia 6 line. The folder names
    aren't carried into the plan text — user fills the description
    fresh from memory."""
    _make_jpeg(
        tmp_path / "Dia 6 - As montanhas" / "p1.jpg",
        datetime(2025, 11, 2, 8, 0, 0),
    )
    _make_jpeg(
        tmp_path / "Dia 6 - Trilha Khunde" / "p2.jpg",
        datetime(2025, 11, 2, 14, 0, 0),
    )
    result = generate_plan_skeleton_from_per_day(tmp_path)
    lines = result.plan_text.strip().split("\n")
    assert len(lines) == 1
    # Two folder names joined with " + " in the description
    assert lines[0] == "Dia 6 - As montanhas + Trilha Khunde (02/11)"


def test_skeleton_two_dia_numbers_same_calendar_date(tmp_path):
    """Nepal's narrative: Dia 7 (EBC flight, morning) and Dia 8
    (Lukla→Kathmandu, afternoon) on the same date 03/11.
    Different Dia numbers → two distinct lines, both with the
    same date."""
    _make_jpeg(
        tmp_path / "Dia 7 - EBC Flight" / "p1.jpg",
        datetime(2025, 11, 3, 8, 0, 0),
    )
    _make_jpeg(
        tmp_path / "Dia 8 - Lukla a Kathmandu" / "p2.jpg",
        datetime(2025, 11, 3, 14, 0, 0),
    )
    result = generate_plan_skeleton_from_per_day(tmp_path)
    lines = result.plan_text.strip().split("\n")
    assert len(lines) == 2
    assert lines[0] == "Dia 7 - EBC Flight (03/11)"
    assert lines[1] == "Dia 8 - Lukla a Kathmandu (03/11)"


def test_skeleton_emits_tz_tag_on_day_1_only(tmp_path):
    _make_jpeg(
        tmp_path / "Dia 1 - Katmandu" / "p1.jpg",
        datetime(2025, 10, 26, 10, 0, 0),
    )
    _make_jpeg(
        tmp_path / "Dia 2 - Lukla" / "p2.jpg",
        datetime(2025, 10, 27, 10, 0, 0),
    )
    result = generate_plan_skeleton_from_per_day(
        tmp_path, home_tz_offset=5.75,
    )
    lines = result.plan_text.strip().split("\n")
    assert "[TZ:+5.75]" in lines[0]
    assert "[TZ:" not in lines[1]


def test_skeleton_filters_to_reference_make_when_specified(tmp_path):
    """Folder has both Apple iPhone and Panasonic G9 photos.
    The skeleton should pick the iPhone shot (reference clock)
    for date extraction, not the (uncalibrated) G9. Important
    when the G9 has wrong clock — we don't want to put the
    photo in the wrong day."""
    folder = tmp_path / "Dia 5 - Some place"
    # iPhone photo — correct date
    _make_jpeg(
        folder / "ip.jpg",
        datetime(2025, 11, 1, 10, 0, 0),
        model="iPhone 11", make="Apple",
    )
    # Panasonic photo — clock is hours off (would point to wrong day
    # if the skeleton naïvely picked the first photo)
    _make_jpeg(
        folder / "p1.jpg",
        datetime(2025, 10, 31, 22, 0, 0),  # different calendar day!
        model="DC-G9M2", make="Panasonic",
    )
    result = generate_plan_skeleton_from_per_day(
        tmp_path, reference_model_contains="iPhone",
    )
    lines = result.plan_text.strip().split("\n")
    assert "(01/11)" in lines[0]  # iPhone won
    # No warning since the reference photo was found.
    assert not any(
        "no reference-camera" in w for w in result.warnings
    )


def test_skeleton_warns_when_no_reference_photo_in_day(tmp_path):
    """Day folder has only non-reference cameras → fall back to
    first photo + emit warning so the user knows the date came
    from an uncalibrated camera (and may shift after Reconcile)."""
    folder = tmp_path / "Dia 5 - G9 only day"
    _make_jpeg(
        folder / "p1.jpg",
        datetime(2025, 11, 1, 10, 0, 0),
        model="DC-G9M2", make="Panasonic",
    )
    result = generate_plan_skeleton_from_per_day(
        tmp_path, reference_model_contains="iPhone",
    )
    assert any("no reference-camera" in w for w in result.warnings)


def test_skeleton_handles_empty_day_folder(tmp_path):
    """A Dia folder with no photo files at all → emit warning;
    the skeleton line still appears with placeholder ``(??/??)``
    so the user notices and edits."""
    (tmp_path / "Dia 5 - Empty").mkdir()
    _make_jpeg(
        tmp_path / "Dia 6 - Has photo" / "p.jpg",
        datetime(2025, 11, 2, 10, 0, 0),
    )
    result = generate_plan_skeleton_from_per_day(tmp_path)
    assert any("no photos found" in w for w in result.warnings)
    lines = result.plan_text.strip().split("\n")
    # Both days appear; Dia 5 has placeholder date.
    # Description pre-populated from folder name even when no photos.
    assert any("Dia 5 - Empty (??/??)" in line for line in lines)
    assert any("Dia 6 - Has photo (02/11)" in line for line in lines)


def test_skeleton_round_trips_through_parser(tmp_path):
    """End-to-end: walk per-day folder → emit skeleton → parse it
    back → verify TripDay dates match the photo dates we stamped."""
    _make_jpeg(
        tmp_path / "Dia 1 - Katmandu" / "p1.jpg",
        datetime(2025, 10, 26, 10, 0, 0),
    )
    _make_jpeg(
        tmp_path / "Dia 2 - Lukla" / "p2.jpg",
        datetime(2025, 10, 29, 10, 0, 0),
    )
    _make_jpeg(
        tmp_path / "Dia 7 - EBC" / "p3.jpg",
        datetime(2025, 11, 3, 8, 0, 0),
    )
    _make_jpeg(
        tmp_path / "Dia 8 - Return" / "p4.jpg",
        datetime(2025, 11, 3, 14, 0, 0),
    )
    result = generate_plan_skeleton_from_per_day(
        tmp_path, home_tz_offset=5.75,
    )
    # The skeleton emits (DD/MM) without year — match the real
    # wizard flow where the caller passes start_date alongside the
    # plan text. Use the earliest discovered date as start_date.
    start = min(result.day_dates.values())
    days = parse_trip_plan(result.plan_text, start_date=start)
    assert {(d.day_number, d.date) for d in days} == {
        (1, date(2025, 10, 26)),
        (2, date(2025, 10, 29)),
        (7, date(2025, 11, 3)),
        (8, date(2025, 11, 3)),
    }
    # TZ inherited through all days
    for d in days:
        assert d.tz_offset == 5.75


def test_skeleton_warns_when_no_dia_folders_present(tmp_path):
    (tmp_path / "extras").mkdir()
    result = generate_plan_skeleton_from_per_day(tmp_path)
    assert result.plan_text == ""
    assert any("no 'Dia N - LOC'" in w for w in result.warnings)


def test_skeleton_per_day_populates_photo_samples(tmp_path):
    """``day_photo_samples`` should carry the reference-camera photos
    per day so the Describe Day dialog has thumbnails to render."""
    _make_jpeg(
        tmp_path / "Dia 1 - Katmandu" / "p1.jpg",
        datetime(2025, 10, 26, 9, 0, 0),
    )
    _make_jpeg(
        tmp_path / "Dia 1 - Katmandu" / "p2.jpg",
        datetime(2025, 10, 26, 14, 0, 0),
    )
    result = generate_plan_skeleton_from_per_day(tmp_path)
    assert 1 in result.day_photo_samples
    assert len(result.day_photo_samples[1]) == 2


# ── generate_plan_skeleton_from_per_camera (fallback) ──────────


def test_per_camera_skeleton_clusters_unique_dates(tmp_path):
    """Three photos across three calendar dates → three Dia rows
    with blank descriptions, sequential numbering."""
    _make_jpeg(tmp_path / "p1.jpg", datetime(2025, 10, 26, 9, 0, 0))
    _make_jpeg(tmp_path / "p2.jpg", datetime(2025, 10, 27, 14, 0, 0))
    _make_jpeg(tmp_path / "p3.jpg", datetime(2025, 10, 28, 8, 0, 0))
    result = generate_plan_skeleton_from_per_camera(
        tmp_path, home_tz_offset=5.75,
    )
    lines = result.plan_text.strip().split("\n")
    assert lines[0] == "Dia 1 - (26/10) [TZ:+5.75]"
    assert lines[1] == "Dia 2 - (27/10)"
    assert lines[2] == "Dia 3 - (28/10)"


def test_per_camera_skeleton_groups_same_date_photos(tmp_path):
    """Multiple photos on the same calendar date → one Dia row;
    all photos collected into ``day_photo_samples`` for the grid."""
    _make_jpeg(tmp_path / "p1.jpg", datetime(2025, 10, 26, 8, 0, 0))
    _make_jpeg(tmp_path / "p2.jpg", datetime(2025, 10, 26, 12, 0, 0))
    _make_jpeg(tmp_path / "p3.jpg", datetime(2025, 10, 26, 18, 0, 0))
    _make_jpeg(tmp_path / "p4.jpg", datetime(2025, 10, 27, 10, 0, 0))
    result = generate_plan_skeleton_from_per_camera(tmp_path)
    lines = result.plan_text.strip().split("\n")
    assert len(lines) == 2
    assert len(result.day_photo_samples[1]) == 3
    assert len(result.day_photo_samples[2]) == 1


def test_per_camera_skeleton_empty_folder_returns_warning(tmp_path):
    """No photos → empty plan + clear warning so the wizard halts."""
    result = generate_plan_skeleton_from_per_camera(tmp_path)
    assert result.plan_text == ""
    assert any("no photos" in w for w in result.warnings)


def test_per_camera_skeleton_missing_folder_returns_warning(tmp_path):
    result = generate_plan_skeleton_from_per_camera(
        tmp_path / "does_not_exist",
    )
    assert result.plan_text == ""
    assert any("not found" in w for w in result.warnings)


def test_per_camera_skeleton_falls_back_to_mtime_when_no_exif(tmp_path):
    """AirDrop'd / messaging-app / edited iPhone photos often arrive
    with EXIF stripped. The skeleton should fall back to file mtime
    so those photos still cluster into days, with a warning that
    surfaces the fallback count for transparency."""
    import os
    import time

    # Two real EXIF photos on day 1 and day 2.
    _make_jpeg(tmp_path / "p1.jpg", datetime(2025, 10, 26, 9, 0, 0))
    _make_jpeg(tmp_path / "p2.jpg", datetime(2025, 10, 27, 9, 0, 0))

    # Three EXIF-less photos: write raw JPEGs without exiftool stamping.
    # These mimic the "received via WhatsApp / AirDrop / iCloud export
    # with metadata stripped" case where DateTimeOriginal is missing.
    from PIL import Image
    for name, mtime_dt in [
        ("noexif1.jpg", datetime(2025, 10, 28, 10, 0, 0)),
        ("noexif2.jpg", datetime(2025, 10, 28, 14, 0, 0)),
        ("noexif3.jpg", datetime(2025, 10, 29, 11, 0, 0)),
    ]:
        path = tmp_path / name
        Image.new("RGB", (16, 16)).save(path, "JPEG")
        mtime_ts = time.mktime(mtime_dt.timetuple())
        os.utime(path, (mtime_ts, mtime_ts))

    result = generate_plan_skeleton_from_per_camera(tmp_path)
    lines = result.plan_text.strip().split("\n")
    # 4 unique calendar dates: 26, 27, 28, 29
    assert len(lines) == 4
    assert any("(28/10)" in line for line in lines)
    assert any("(29/10)" in line for line in lines)
    # Warning surfaces the mtime fallback count.
    assert any(
        "mtime" in w.lower() or "modification time" in w.lower()
        for w in result.warnings
    )


# ── days_to_plan_text (inverse of parse_trip_plan) ──────────────────


def test_days_to_plan_text_round_trips():
    """A list of TripDays serialised → re-parsed yields the same days
    (day_number / date / description / location preserved). The TZ
    anchor lives on Day 1 like the parser expects."""
    from datetime import date as _date
    from core.models import TripDay
    from core.trip_plan_skeleton import days_to_plan_text

    days = [
        TripDay(day_number=1, date=_date(2026, 5, 19),
                description="Lukla", tz_offset=5.75, location="Lukla"),
        TripDay(day_number=2, date=_date(2026, 5, 20),
                description="Phakding", tz_offset=5.75, location=None),
    ]
    text = days_to_plan_text(days, home_tz_offset=5.75)
    parsed = parse_trip_plan(text, home_timezone=5.75)
    assert len(parsed) == 2
    assert parsed[0].day_number == 1
    assert parsed[0].date == _date(2026, 5, 19)
    assert parsed[0].description == "Lukla"
    assert parsed[0].location == "Lukla"
    assert parsed[1].day_number == 2
    assert parsed[1].date == _date(2026, 5, 20)


def test_days_to_plan_text_emits_tz_change_only_on_difference():
    """Day 1 always emits [TZ:..]; later days only when TZ actually
    changes (parser inherits TZ otherwise — emitting on every row
    bloats the text and tempts mismatched edits)."""
    from datetime import date as _date
    from core.models import TripDay
    from core.trip_plan_skeleton import days_to_plan_text

    days = [
        TripDay(day_number=1, date=_date(2026, 1, 1),
                description="Day 1", tz_offset=-3.0),
        TripDay(day_number=2, date=_date(2026, 1, 2),
                description="Day 2", tz_offset=-3.0),
        TripDay(day_number=3, date=_date(2026, 1, 3),
                description="Day 3", tz_offset=5.75),
    ]
    text = days_to_plan_text(days, home_tz_offset=-3.0)
    lines = text.strip().split("\n")
    assert "[TZ:-3]" in lines[0]
    assert "[TZ:" not in lines[1]            # inherited
    assert "[TZ:+5.75]" in lines[2]
