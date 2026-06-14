"""Tests for core.process_discovery."""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

import pytest

from core.models import Event, TripDay
from core.path_builder import day_folder_name, sanitize_folder_name
from core.process_discovery import (
    PROCESSED_FOLDER_NAME,
    already_processed_paths,
    discover_processable,
)
from core.vocabulary import Scenario


def _touch(p: Path, mtime: float | None = None) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _make_event(tmp_path: Path) -> Event:
    """Build a minimal 2-day Event whose folder layout matches what
    discovery expects to walk."""
    event = Event(
        name="Test",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 2),
        photos_base_path=str(tmp_path),
    )
    event.trip_days = [
        TripDay(day_number=1, date=date(2026, 4, 1), description="Day one"),
        TripDay(day_number=2, date=date(2026, 4, 2), description="Day two"),
    ]
    return event


def test_empty_event_returns_empty(tmp_path):
    event = _make_event(tmp_path)
    assert discover_processable(event) == []


def test_finds_photos_across_eligible_scenarios(tmp_path):
    event = _make_event(tmp_path)
    d1 = tmp_path / "02 - Selected" / day_folder_name(event.trip_days[0])
    landscape = sanitize_folder_name(Scenario.LANDSCAPE.value)
    portrait = sanitize_folder_name(Scenario.PORTRAIT.value)

    p1 = _touch(d1 / landscape / "DSC_001.jpg", mtime=1000.0)
    p2 = _touch(d1 / portrait / "DSC_002.jpg", mtime=2000.0)

    items = discover_processable(event)
    assert {it.path for it in items} == {p1, p2}
    # Items should be sorted by timestamp ascending.
    assert items[0].path == p1
    assert items[1].path == p2


def test_skips_processed_output_folder(tmp_path):
    """The Process Culler writes into ``<event>/processed/`` and must
    not see its own output as fresh input."""
    event = _make_event(tmp_path)
    d1_name = day_folder_name(event.trip_days[0])
    landscape = sanitize_folder_name(Scenario.LANDSCAPE.value)

    _touch(tmp_path / "02 - Selected" / d1_name / landscape / "src.jpg", mtime=1000.0)
    _touch(tmp_path / PROCESSED_FOLDER_NAME / d1_name / "100000_src.jpg", mtime=2000.0)

    items = discover_processable(event)
    assert len(items) == 1
    assert items[0].path.name == "src.jpg"


def test_skips_video_and_brackets(tmp_path):
    """Video and bracket scenarios route to other tools, not to
    Process Culler."""
    event = _make_event(tmp_path)
    d1 = tmp_path / "02 - Selected" / day_folder_name(event.trip_days[0])
    landscape = sanitize_folder_name(Scenario.LANDSCAPE.value)
    video = sanitize_folder_name(Scenario.VIDEO.value)
    fb = sanitize_folder_name(Scenario.FOCUS_BRACKET.value)

    keep = _touch(d1 / landscape / "good.jpg", mtime=1000.0)
    _touch(d1 / video / "movie.mp4", mtime=1500.0)
    _touch(d1 / fb / "step1.rw2", mtime=1500.0)

    items = discover_processable(event)
    assert [it.path for it in items] == [keep]


def test_skips_unrecognized_day_folders(tmp_path):
    """Stray folders (manual notes, partial drafts) should be ignored
    rather than treated as a missing day."""
    event = _make_event(tmp_path)
    landscape = sanitize_folder_name(Scenario.LANDSCAPE.value)

    _touch(tmp_path / "Random Notes" / landscape / "x.jpg", mtime=1000.0)
    real = _touch(
        tmp_path / "02 - Selected" / day_folder_name(event.trip_days[0]) / landscape / "real.jpg",
        mtime=2000.0,
    )

    items = discover_processable(event)
    assert [it.path for it in items] == [real]


def test_skips_non_photo_extensions(tmp_path):
    event = _make_event(tmp_path)
    d1 = tmp_path / "02 - Selected" / day_folder_name(event.trip_days[0])
    landscape = sanitize_folder_name(Scenario.LANDSCAPE.value)

    _touch(d1 / landscape / "notes.txt", mtime=1000.0)
    _touch(d1 / landscape / "thumb.db", mtime=1500.0)
    keep = _touch(d1 / landscape / "shot.jpg", mtime=2000.0)

    items = discover_processable(event)
    assert [it.path for it in items] == [keep]


def test_chronological_order_across_days(tmp_path):
    """Output mixes scenarios within a day but sorts globally by
    timestamp, so the slideshow downstream reads chronologically."""
    event = _make_event(tmp_path)
    d1 = tmp_path / "02 - Selected" / day_folder_name(event.trip_days[0])
    d2 = tmp_path / "02 - Selected" / day_folder_name(event.trip_days[1])
    landscape = sanitize_folder_name(Scenario.LANDSCAPE.value)
    portrait = sanitize_folder_name(Scenario.PORTRAIT.value)

    a = _touch(d1 / landscape / "a.jpg", mtime=1000.0)
    b = _touch(d1 / portrait / "b.jpg", mtime=1500.0)
    c = _touch(d2 / landscape / "c.jpg", mtime=2000.0)

    items = discover_processable(event)
    assert [it.path for it in items] == [a, b, c]
    assert items[0].day.day_number == 1 and items[2].day.day_number == 2
    assert items[0].scenario == Scenario.LANDSCAPE
    assert items[1].scenario == Scenario.PORTRAIT


def test_event_with_no_base_path_returns_empty(tmp_path):
    event = Event(
        name="Bare",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 1),
    )
    event.trip_days = [TripDay(day_number=1, date=date(2026, 4, 1), description="x")]
    assert discover_processable(event) == []


def test_already_processed_paths_recovers_source(tmp_path):
    """A processed JPEG sitting at the deterministic
    ``<HHMMSS>_<orig_stem>.jpg`` path under ``03 - Processed/<Dia N>/``
    must be detected against its source. The HHMMSS prefix is derived
    from the source's EXIF/mtime timestamp so the output path is
    fully predictable per item."""
    from core.process_render import output_filename

    event = _make_event(tmp_path)
    d1_name = day_folder_name(event.trip_days[0])
    landscape = sanitize_folder_name(Scenario.LANDSCAPE.value)

    src = _touch(
        tmp_path / "02 - Selected" / d1_name / landscape / "DSC_001.jpg",
        mtime=1000.0,
    )
    items = discover_processable(event)
    [item] = items
    _touch(
        tmp_path / PROCESSED_FOLDER_NAME / d1_name
        / output_filename(item.timestamp, item.path),
        mtime=2000.0,
    )

    done = already_processed_paths(event, items=items)
    assert src in done
    assert len(done) == 1


def test_already_processed_handles_duplicate_stems_across_days(tmp_path):
    """Costa Rica re-test 2026-05-01: two photos with the same stem
    on different days (e.g. ``IMG_5765.HEIC`` shot on Day 1 and a
    photo sharing the same stem on Day 2) used to collapse in the
    legacy ``{stem: path}`` dict, dropping one of the days from the
    Process tab's "X / Y exported" indicator. Per-item path checks
    keep both visible."""
    from core.process_render import output_filename

    event = _make_event(tmp_path)
    d1_name = day_folder_name(event.trip_days[0])
    d2_name = day_folder_name(event.trip_days[1])
    landscape = sanitize_folder_name(Scenario.LANDSCAPE.value)

    src1 = _touch(
        tmp_path / "02 - Selected" / d1_name / landscape / "IMG_001.jpg",
        mtime=1000.0,
    )
    src2 = _touch(
        tmp_path / "02 - Selected" / d2_name / landscape / "IMG_001.jpg",
        mtime=1_700_000_000.0,
    )
    items = discover_processable(event)
    by_path = {it.path: it for it in items}
    item1 = by_path[src1]
    item2 = by_path[src2]
    _touch(
        tmp_path / PROCESSED_FOLDER_NAME / d1_name
        / output_filename(item1.timestamp, item1.path),
    )
    _touch(
        tmp_path / PROCESSED_FOLDER_NAME / d2_name
        / output_filename(item2.timestamp, item2.path),
    )

    done = already_processed_paths(event, items=items)
    assert src1 in done
    assert src2 in done
    assert len(done) == 2


def test_pt_br_alias_maps_to_canonical_scenario(tmp_path):
    """Folders curated by hand in FastStone may be in pt-BR. The
    discovery's alias table should pick them up under the canonical
    Scenario enum so the rest of the pipeline sees the same shape."""
    event = _make_event(tmp_path)
    d1 = tmp_path / "02 - Selected" / day_folder_name(event.trip_days[0])

    p1 = _touch(d1 / "Pessoas" / "DSC_001.jpg", mtime=1000.0)
    p2 = _touch(d1 / "Paisagem" / "DSC_002.jpg", mtime=2000.0)
    p3 = _touch(d1 / "Animais" / "DSC_003.jpg", mtime=3000.0)

    items = discover_processable(event)
    by_path = {it.path: it for it in items}
    assert by_path[p1].scenario == Scenario.PORTRAIT
    assert by_path[p1].source_folder == "Pessoas"
    assert by_path[p2].scenario == Scenario.LANDSCAPE
    assert by_path[p2].source_folder == "Paisagem"
    assert by_path[p3].scenario == Scenario.WILDLIFE
    assert by_path[p3].source_folder == "Animais"


def test_unknown_folder_falls_back_to_general(tmp_path):
    """A folder name that doesn't match any alias must still be
    discovered — silently dropping the user's photos because their
    folder is named ``Architecture`` or ``Comida`` is exactly the
    bug the user hit."""
    event = _make_event(tmp_path)
    d1 = tmp_path / "02 - Selected" / day_folder_name(event.trip_days[0])
    p = _touch(d1 / "Architecture" / "DSC_001.jpg", mtime=1000.0)

    items = discover_processable(event)
    assert len(items) == 1
    assert items[0].scenario == Scenario.GENERAL
    assert items[0].source_folder == "Architecture"


def test_two_folders_mapping_to_same_scenario_stay_distinct(tmp_path):
    """``Pessoas`` and ``People`` both map to PORTRAIT but represent
    distinct on-disk folders. They should appear as separate items
    so the UI can show both as separate cards — the user decides
    whether to consolidate by renaming on disk."""
    event = _make_event(tmp_path)
    d1 = tmp_path / "02 - Selected" / day_folder_name(event.trip_days[0])
    p1 = _touch(d1 / "Pessoas" / "a.jpg", mtime=1000.0)
    p2 = _touch(d1 / "People" / "b.jpg", mtime=2000.0)

    items = discover_processable(event)
    folders = {it.source_folder for it in items}
    assert folders == {"Pessoas", "People"}
    assert all(it.scenario == Scenario.PORTRAIT for it in items)


def test_already_processed_skips_output_without_matching_source(tmp_path):
    """If the user deletes a source after processing, the output is
    still on disk but doesn't correspond to anything discoverable.
    Recovery should silently drop it."""
    event = _make_event(tmp_path)
    d1_name = day_folder_name(event.trip_days[0])
    _touch(tmp_path / PROCESSED_FOLDER_NAME / d1_name / "143027_orphan.jpg", mtime=2000.0)
    assert already_processed_paths(event) == set()
