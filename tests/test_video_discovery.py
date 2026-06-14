"""Tests for core.video_discovery."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from core.models import Event, TripDay
from core.path_builder import day_folder_name
from core.video_discovery import (
    EXTRACTED_FRAMES_FOLDER_NAME,
    PROCESSED_FOLDER_NAME,
    discover_videos,
)


def _touch(p: Path, mtime: float | None = None) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _make_event(tmp_path: Path) -> Event:
    e = Event(
        name="Test",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 2),
        photos_base_path=str(tmp_path),
    )
    e.trip_days = [
        TripDay(day_number=1, date=date(2026, 4, 1), description="Day one"),
        TripDay(day_number=2, date=date(2026, 4, 2), description="Day two"),
    ]
    return e


def test_empty_event_returns_empty(tmp_path):
    event = _make_event(tmp_path)
    assert discover_videos(event) == []


def test_finds_videos_under_day_subfolders(tmp_path):
    event = _make_event(tmp_path)
    d1 = tmp_path / "02 - Selected" / day_folder_name(event.trip_days[0])
    v1 = _touch(d1 / "video" / "MOV_001.mp4", mtime=1000.0)
    v2 = _touch(d1 / "gopro" / "GH010001.mp4", mtime=2000.0)

    items = discover_videos(event)
    assert {it.path for it in items} == {v1, v2}
    # Source folders preserved verbatim.
    assert {it.source_folder for it in items} == {"video", "gopro"}


def test_finds_videos_recursively_inside_source_folder(tmp_path):
    """GoPros write into ``GH01/100GOPRO/``-style nested folders.
    Discovery should find videos at any depth under the immediate
    day-child folder, but the ``source_folder`` should stay as that
    immediate child for the UI grouping."""
    event = _make_event(tmp_path)
    d1 = tmp_path / "02 - Selected" / day_folder_name(event.trip_days[0])
    v = _touch(d1 / "gopro" / "100GOPRO" / "GH010001.mp4", mtime=1000.0)

    items = discover_videos(event)
    assert len(items) == 1
    assert items[0].path == v
    assert items[0].source_folder == "gopro"


def test_chronological_order_across_days(tmp_path):
    event = _make_event(tmp_path)
    d1 = tmp_path / "02 - Selected" / day_folder_name(event.trip_days[0])
    d2 = tmp_path / "02 - Selected" / day_folder_name(event.trip_days[1])
    a = _touch(d1 / "video" / "a.mp4", mtime=1000.0)
    b = _touch(d2 / "video" / "b.mp4", mtime=2000.0)
    c = _touch(d1 / "gopro" / "c.mp4", mtime=1500.0)

    items = discover_videos(event)
    assert [it.path for it in items] == [a, c, b]


def test_skips_processed_and_extracted_outputs(tmp_path):
    event = _make_event(tmp_path)
    d1_name = day_folder_name(event.trip_days[0])

    real = _touch(tmp_path / "02 - Selected" / d1_name / "video" / "shot.mp4", mtime=1000.0)
    # ``processed/`` holds both Process Photos JPEGs and Process
    # Videos clip exports — never re-discovered as input.
    _touch(
        tmp_path / PROCESSED_FOLDER_NAME / d1_name / "143027_shot.jpg",
        mtime=2000.0,
    )
    _touch(
        tmp_path / PROCESSED_FOLDER_NAME / d1_name / "143027_shot.mp4",
        mtime=2000.0,
    )
    # And the per-day extracted-frames folder is reserved too.
    _touch(
        tmp_path / "02 - Selected" / d1_name / EXTRACTED_FRAMES_FOLDER_NAME / "frame.jpg",
        mtime=2500.0,
    )

    items = discover_videos(event)
    assert [it.path for it in items] == [real]


def test_ignores_non_video_extensions(tmp_path):
    event = _make_event(tmp_path)
    d1 = tmp_path / "02 - Selected" / day_folder_name(event.trip_days[0])
    _touch(d1 / "video" / "shot.jpg", mtime=1000.0)
    _touch(d1 / "video" / "notes.txt", mtime=1000.0)
    keep = _touch(d1 / "video" / "real.mp4", mtime=2000.0)

    items = discover_videos(event)
    assert [it.path for it in items] == [keep]


def test_ignores_unrecognized_day_folders(tmp_path):
    event = _make_event(tmp_path)
    _touch(tmp_path / "RandomNotes" / "video" / "x.mp4", mtime=1000.0)
    real = _touch(
        tmp_path / "02 - Selected" / day_folder_name(event.trip_days[0]) / "video" / "real.mp4",
        mtime=2000.0,
    )

    items = discover_videos(event)
    assert [it.path for it in items] == [real]


def test_video_at_day_root_uses_day_description_as_source(tmp_path):
    """A stray video right under ``Dia N - desc/`` (no scenario
    folder) should still be discovered; the UI labels it with the
    day's description so it's clearly distinct."""
    event = _make_event(tmp_path)
    d1 = tmp_path / "02 - Selected" / day_folder_name(event.trip_days[0])
    v = _touch(d1 / "stray.mp4", mtime=1000.0)

    items = discover_videos(event)
    assert len(items) == 1
    assert items[0].path == v
    assert items[0].source_folder == event.trip_days[0].description


def test_event_with_no_base_path_returns_empty(tmp_path):
    event = Event(
        name="Bare",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 1),
    )
    event.trip_days = [TripDay(day_number=1, date=date(2026, 4, 1), description="x")]
    assert discover_videos(event) == []
