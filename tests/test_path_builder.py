"""Tests for path builder."""

from datetime import date
from pathlib import Path

from core.models import Event, TripDay
from core.path_builder import (
    EDITED_MEDIA_DIR_NAME,
    EXPORTED_MEDIA_DIR_NAME,
    RESERVED_DIR_NAMES,
    day_folder_name,
    day_folder_path,
    edited_media_dir,
    ensure_event_tree,
    event_root_path,
    exported_media_dir,
    sanitize_folder_name,
)


def test_sanitize_basic():
    assert sanitize_folder_name("Hello World") == "Hello World"


def test_sanitize_slashes():
    assert sanitize_folder_name("Chegada (12/04)") == "Chegada (12-04)"
    assert sanitize_folder_name("Path\\with\\backslash") == "Path-with-backslash"


def test_sanitize_special_chars():
    assert sanitize_folder_name('File: "test" <ok>') == "File- -test- -ok-"
    assert sanitize_folder_name("What?") == "What-"


def test_sanitize_dots_and_spaces():
    assert sanitize_folder_name("  hello  ") == "hello"
    assert sanitize_folder_name("...dots...") == "dots"


def test_day_folder_name():
    day = TripDay(1, date(2026, 7, 15), "Chegada em San José")
    # Nelson 2026-05-23 task #107: ISO date now embedded.
    assert day_folder_name(day) == (
        "Dia 1 - 2026-07-15 - Chegada em San José"
    )


def test_day_folder_name_sanitizes():
    day = TripDay(2, date(2026, 7, 16), "Transfer (12/04)")
    assert day_folder_name(day) == (
        "Dia 2 - 2026-07-16 - Transfer (12-04)"
    )


def test_day_folder_name_without_description():
    """Walking-skeleton days carry no description until the user
    types one — the folder name still has the day number + date."""
    day = TripDay(3, date(2026, 7, 17), "")
    assert day_folder_name(day) == "Dia 3 - 2026-07-17"


def test_day_folder_name_without_date_falls_back_to_legacy():
    """Defensive: mid-edit transient state can have a description
    but no date yet. The folder name falls back to the legacy
    ``Dia N - desc`` shape rather than erroring."""
    day = TripDay(4, None, "Some description")
    assert day_folder_name(day) == "Dia 4 - Some description"


def test_day_number_from_folder_recognises_new_shape():
    from core.path_builder import day_number_from_folder
    assert day_number_from_folder(
        "Dia 5 - 2026-07-15 - La Fortuna") == 5


def test_day_number_from_folder_recognises_legacy_shape():
    """Folders created before the date was embedded still parse."""
    from core.path_builder import day_number_from_folder
    assert day_number_from_folder("Dia 5 - La Fortuna") == 5
    assert day_number_from_folder("Dia 5") == 5


def test_day_number_from_folder_returns_none_for_junk():
    from core.path_builder import day_number_from_folder
    assert day_number_from_folder("_no_timestamp") is None
    assert day_number_from_folder("_out_of_day_range") is None
    assert day_number_from_folder("just some folder") is None


def test_day_folder_path():
    """Pipeline-taxonomy freeze 2026-05-19: the day-folder helper
    points at the Select tree ``02 - Selected/`` (what Process
    reads); the cull bank is ``01 - Culled/`` via culled_day_path."""
    day = TripDay(1, date(2026, 7, 15), "Test")
    root = Path("/photos/event")
    result = day_folder_path(root, day)
    assert result == Path(
        "/photos/event/02 - Selected/Dia 1 - 2026-07-15 - Test")


def test_event_root_path_returns_stored_absolute_path():
    """Nelson 2026-05-22: under the cleaned model
    ``Event.photos_base_path`` is the absolute event root and
    ``event_root_path()`` is a trivial getter. No ``/trips/``
    insertion. The first argument is vestigial — preserved for
    signature compatibility but ignored."""
    event = Event(
        name="Costa Rica", start_date=date(2026, 7, 15),
        photos_base_path="/photos/2026 - Costa Rica",
    )
    # The first argument is ignored; the stored absolute path wins.
    assert event_root_path("/anything", event) == Path(
        "/photos/2026 - Costa Rica")
    assert event_root_path("", event) == Path(
        "/photos/2026 - Costa Rica")


def test_event_root_path_uses_arbitrary_user_layout():
    """Users get to decide their directory structure. Mira adds
    nothing. If the user stores their event at
    ``D:\\Photos\\mira\\2026-Chapada``, that's the event root."""
    event = Event(
        name="Chapada", start_date=date(2026, 5, 1),
        photos_base_path="D:/Photos/mira/2026-Chapada",
    )
    assert event_root_path("", event) == Path(
        "D:/Photos/mira/2026-Chapada")


# ── spec/66 §1.2 — Exported Media/ tier ─────────────────────────────


def test_exported_media_dir_distinct_from_edited(tmp_path):
    """spec/66 §1.2 — Exported Media/ is the shipped set; Edited Media/
    is the third-party return inbox. Two different folders, two
    different roles."""
    root = tmp_path / "Event"
    assert exported_media_dir(root) == root / "Exported Media"
    assert edited_media_dir(root) == root / "Edited Media"
    assert exported_media_dir(root) != edited_media_dir(root)
    assert EXPORTED_MEDIA_DIR_NAME == "Exported Media"
    assert EDITED_MEDIA_DIR_NAME == "Edited Media"


def test_ensure_event_tree_creates_exported_media(tmp_path):
    """spec/66 §1.2 — the event skeleton must include Exported Media/
    so the Export surface always has a destination root to write to."""
    root = tmp_path / "Event"
    root.mkdir()
    ensure_event_tree(root)
    assert exported_media_dir(root).is_dir()
    assert edited_media_dir(root).is_dir()


def test_reserved_dir_names_includes_exported_media():
    """Walks of the event tree must skip Exported Media/ alongside the
    other tier folders."""
    assert "Exported Media" in RESERVED_DIR_NAMES
    assert "Edited Media" in RESERVED_DIR_NAMES
