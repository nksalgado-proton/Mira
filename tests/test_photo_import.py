"""Tests for walking-skeleton step 6 — folder → day bucket import."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from core.models import Event, TripDay
from core.path_builder import day_folder_name
from core.photo_import import (
    BUCKET_INDIVIDUAL,
    CULLER_JOURNAL_NAME,
    CULLER_JOURNAL_VERSION,
    count_bucket,
    day_bucket_dir,
    ensure_event_root,
    import_folder_to_day,
)


# ── Helpers ──────────────────────────────────────────────────────


def _isolate_user_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path / "_user"))


def _make_event(name: str = "Costa Rica 2026", start=None) -> Event:
    return Event(
        name=name,
        start_date=start or date(2026, 7, 1),
        trip_days=[TripDay(day_number=1, date=date.today(), description="")],
    )


def _write_image(folder: Path, name: str, size: int = 64) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_bytes(b"x" * size)
    return p


# ── ensure_event_root ────────────────────────────────────────────


def test_ensure_event_root_fills_empty_photos_base_path(tmp_path, monkeypatch):
    _isolate_user_dir(tmp_path, monkeypatch)
    event = _make_event()
    assert event.photos_base_path == ""

    root = ensure_event_root(event)

    assert event.photos_base_path != ""
    assert root.exists() and root.is_dir()
    # The "01 - Culled/" stage parent is created up-front (skeleton
    # importer lands in the Cull tree per the 2026-05-19 taxonomy).
    assert (root / "01 - Culled").exists()


def test_ensure_event_root_honors_existing_photos_base_path(tmp_path, monkeypatch):
    _isolate_user_dir(tmp_path, monkeypatch)
    custom = tmp_path / "custom_photos"
    event = _make_event()
    event.photos_base_path = str(custom)

    root = ensure_event_root(event)

    assert str(root).startswith(str(custom))
    assert event.photos_base_path == str(custom)


# ── import_folder_to_day ─────────────────────────────────────────


def test_import_folder_copies_images_into_individual_bucket(
    tmp_path, monkeypatch,
):
    _isolate_user_dir(tmp_path, monkeypatch)
    event = _make_event()
    day = event.trip_days[0]

    source = tmp_path / "card"
    _write_image(source, "DSC_001.RW2", 100)
    _write_image(source, "DSC_002.RW2", 100)
    _write_image(source, "ignore.txt", 100)  # not a photo extension

    result = import_folder_to_day(event, day, source)

    assert result.copied == 2
    assert result.skipped == 0
    # Files arrived at <event_root>/01 - Culled/Dia 1/Individual/
    bucket_dir = day_bucket_dir(event, day, BUCKET_INDIVIDUAL)
    assert (bucket_dir / "DSC_001.RW2").exists()
    assert (bucket_dir / "DSC_002.RW2").exists()
    assert not (bucket_dir / "ignore.txt").exists()


def test_import_folder_initializes_culler_journal(tmp_path, monkeypatch):
    _isolate_user_dir(tmp_path, monkeypatch)
    event = _make_event()
    day = event.trip_days[0]

    source = tmp_path / "card"
    _write_image(source, "a.jpg")

    import_folder_to_day(event, day, source)

    # Journal lives in the day folder, alongside the Individual/ bucket.
    bucket_dir = day_bucket_dir(event, day, BUCKET_INDIVIDUAL)
    journal_path = bucket_dir.parent / CULLER_JOURNAL_NAME
    assert journal_path.exists()
    data = json.loads(journal_path.read_text(encoding="utf-8"))
    assert data["version"] == CULLER_JOURNAL_VERSION
    assert data["day_number"] == 1
    assert "Individual" in data["buckets"]
    assert data["marks"] == {}


def test_import_folder_is_idempotent_on_same_source(tmp_path, monkeypatch):
    """Re-running the import against the same source skips files of
    the same name + size (idempotent)."""
    _isolate_user_dir(tmp_path, monkeypatch)
    event = _make_event()
    day = event.trip_days[0]
    source = tmp_path / "card"
    _write_image(source, "a.jpg", 100)
    _write_image(source, "b.jpg", 100)

    first = import_folder_to_day(event, day, source)
    second = import_folder_to_day(event, day, source)

    assert first.copied == 2
    assert second.copied == 0
    assert second.skipped == 2


def test_import_folder_preserves_journal_across_re_import(tmp_path, monkeypatch):
    """Re-importing must not clobber an existing journal."""
    _isolate_user_dir(tmp_path, monkeypatch)
    event = _make_event()
    day = event.trip_days[0]
    source = tmp_path / "card"
    _write_image(source, "a.jpg")

    import_folder_to_day(event, day, source)
    bucket_dir = day_bucket_dir(event, day, BUCKET_INDIVIDUAL)
    journal_path = bucket_dir.parent / CULLER_JOURNAL_NAME
    data = json.loads(journal_path.read_text(encoding="utf-8"))
    data["marks"]["a.jpg"] = "picked"
    journal_path.write_text(json.dumps(data), encoding="utf-8")

    _write_image(source, "b.jpg")
    import_folder_to_day(event, day, source)

    # The mark survives.
    after = json.loads(journal_path.read_text(encoding="utf-8"))
    assert after["marks"].get("a.jpg") == "picked"


def test_import_folder_recursive_picks_up_subfolders(tmp_path, monkeypatch):
    _isolate_user_dir(tmp_path, monkeypatch)
    event = _make_event()
    day = event.trip_days[0]

    source = tmp_path / "card"
    _write_image(source / "sub_a", "x.jpg")
    _write_image(source / "sub_b", "y.jpg")

    result = import_folder_to_day(event, day, source)

    assert result.copied == 2
    bucket = day_bucket_dir(event, day, BUCKET_INDIVIDUAL)
    assert (bucket / "x.jpg").exists()
    assert (bucket / "y.jpg").exists()


def test_import_folder_missing_source_raises(tmp_path, monkeypatch):
    _isolate_user_dir(tmp_path, monkeypatch)
    event = _make_event()
    day = event.trip_days[0]
    missing = tmp_path / "nope"

    with pytest.raises(FileNotFoundError):
        import_folder_to_day(event, day, missing)


# ── count_bucket ─────────────────────────────────────────────────


def test_count_bucket_zero_for_empty_event(tmp_path, monkeypatch):
    _isolate_user_dir(tmp_path, monkeypatch)
    event = _make_event()
    day = event.trip_days[0]
    # photos_base_path still empty → count is 0 without crashing.
    assert count_bucket(event, day) == 0


def test_count_bucket_reflects_imported_files(tmp_path, monkeypatch):
    _isolate_user_dir(tmp_path, monkeypatch)
    event = _make_event()
    day = event.trip_days[0]
    source = tmp_path / "card"
    _write_image(source, "1.jpg")
    _write_image(source, "2.jpg")
    _write_image(source, "3.jpg")

    import_folder_to_day(event, day, source)

    assert count_bucket(event, day) == 3


# ── path_builder edge: empty description ─────────────────────────


def test_day_folder_name_no_description_drops_dash():
    """Step 5 days have no description; the folder name should be
    'Dia N - YYYY-MM-DD' (no trailing ' - ' for the empty desc).
    Nelson 2026-05-23 task #107: date is embedded in the name."""
    today = date.today()
    day = TripDay(day_number=1, date=today, description="")
    assert day_folder_name(day) == f"Dia 1 - {today.isoformat()}"
