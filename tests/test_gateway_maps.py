"""Gateway map helpers (spec/155).

Covers attach / clear / get for both the per-day and event-level map
slots, including the file-copy + atomic rename, stale-sibling sweep,
and round-trip of the DB path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.store import models as m
from mira.store.repo import EventStore


def _make_gateway(tmp_path: Path) -> EventGateway:
    store = EventStore.create(tmp_path / "e.db", event_id="evt-1")
    store.save_document(m.EventDocument(event=m.Event(
        uuid="evt-1", name="Trip", created_at="t", updated_at="t")))
    store.upsert(m.TripDay(day_number=1, date="2026-06-01"))
    store.upsert(m.TripDay(day_number=2, date="2026-06-02"))
    return EventGateway(store, event_root=tmp_path)


def _write_jpeg(path: Path, content: bytes = b"\xff\xd8\xff\xe0pretend-jpeg") -> Path:
    path.write_bytes(content)
    return path


# ── reads ────────────────────────────────────────────────────────

def test_get_day_map_path_returns_none_when_no_map_attached(tmp_path):
    eg = _make_gateway(tmp_path)
    try:
        assert eg.get_day_map_path(1) is None
        assert eg.get_day_map_path(2) is None
    finally:
        eg.close()


def test_get_event_map_path_returns_none_when_no_map_attached(tmp_path):
    eg = _make_gateway(tmp_path)
    try:
        assert eg.get_event_map_path() is None
    finally:
        eg.close()


def test_get_day_map_path_unknown_day_returns_none(tmp_path):
    """An unknown day_number simply has no row — return None, don't raise."""
    eg = _make_gateway(tmp_path)
    try:
        assert eg.get_day_map_path(99) is None
    finally:
        eg.close()


# ── attach ───────────────────────────────────────────────────────

def test_attach_day_map_copies_to_slot_and_writes_db(tmp_path):
    """Copy into ``Maps/day-02.jpg``, write the relative path to the DB."""
    src = _write_jpeg(tmp_path / "outside.jpg")
    eg = _make_gateway(tmp_path)
    try:
        rel = eg.attach_day_map(2, src)
        assert rel == "Maps/day-02.jpg"
        assert eg.get_day_map_path(2) == "Maps/day-02.jpg"
        slot = tmp_path / "Maps" / "day-02.jpg"
        assert slot.is_file()
        assert slot.read_bytes() == src.read_bytes()
        # The source file is left untouched (we copy, not move).
        assert src.is_file()
    finally:
        eg.close()


def test_attach_event_map_copies_to_slot_and_writes_db(tmp_path):
    """Event-level slot lands at ``Maps/event.<ext>``."""
    src = _write_jpeg(tmp_path / "overview.png", content=b"\x89PNGpretend-png")
    src = src.rename(src.with_suffix(".png"))
    eg = _make_gateway(tmp_path)
    try:
        rel = eg.attach_event_map(src)
        assert rel == "Maps/event.png"
        assert eg.get_event_map_path() == "Maps/event.png"
        assert (tmp_path / "Maps" / "event.png").is_file()
    finally:
        eg.close()


def test_attach_day_map_replaces_existing_slot(tmp_path):
    """Re-attaching overwrites the slot file atomically."""
    first = _write_jpeg(tmp_path / "first.jpg", content=b"\xff\xd8first")
    second = _write_jpeg(tmp_path / "second.jpg", content=b"\xff\xd8second")
    eg = _make_gateway(tmp_path)
    try:
        eg.attach_day_map(2, first)
        eg.attach_day_map(2, second)
        slot = tmp_path / "Maps" / "day-02.jpg"
        assert slot.read_bytes() == b"\xff\xd8second"
        # Only one slot file — no stale .tmp / sibling left behind.
        assert sorted(p.name for p in (tmp_path / "Maps").iterdir()) == [
            "day-02.jpg"]
    finally:
        eg.close()


def test_attach_day_map_sweeps_stale_sibling_with_different_extension(tmp_path):
    """If the slot already held ``day-02.png`` and a JPEG is now attached,
    the stale ``.png`` is removed (the slot has one truth)."""
    png = _write_jpeg(tmp_path / "first.png", content=b"\x89PNGfirst")
    png = png.rename(png.with_suffix(".png"))
    jpg = _write_jpeg(tmp_path / "second.jpg")
    eg = _make_gateway(tmp_path)
    try:
        eg.attach_day_map(2, png)
        assert (tmp_path / "Maps" / "day-02.png").is_file()
        eg.attach_day_map(2, jpg)
        assert not (tmp_path / "Maps" / "day-02.png").exists()
        assert (tmp_path / "Maps" / "day-02.jpg").is_file()
        assert eg.get_day_map_path(2) == "Maps/day-02.jpg"
    finally:
        eg.close()


def test_attach_day_map_normalizes_jpeg_extension_to_jpg(tmp_path):
    """``.jpeg`` is accepted but lands on disk as ``.jpg`` so slot files
    are consistently named."""
    src = _write_jpeg(tmp_path / "outside.jpeg")
    eg = _make_gateway(tmp_path)
    try:
        rel = eg.attach_day_map(1, src)
        assert rel == "Maps/day-01.jpg"
        assert (tmp_path / "Maps" / "day-01.jpg").is_file()
    finally:
        eg.close()


def test_attach_day_map_rejects_non_image_extension(tmp_path):
    """Anything outside JPEG/PNG is a clean ValueError."""
    src = _write_jpeg(tmp_path / "rogue.tiff")
    eg = _make_gateway(tmp_path)
    try:
        with pytest.raises(ValueError, match="map image must be"):
            eg.attach_day_map(1, src)
        # The DB stays clean (no spurious row).
        assert eg.get_day_map_path(1) is None
    finally:
        eg.close()


def test_attach_day_map_missing_source_raises(tmp_path):
    """A vanished source is a FileNotFoundError, not a silent no-op."""
    eg = _make_gateway(tmp_path)
    try:
        with pytest.raises(FileNotFoundError):
            eg.attach_day_map(1, tmp_path / "nope.jpg")
    finally:
        eg.close()


def test_attach_creates_maps_dir_when_missing(tmp_path):
    """If Maps/ doesn't exist yet (older event tree), the helper creates it."""
    # Don't pre-create Maps/.
    assert not (tmp_path / "Maps").exists()
    src = _write_jpeg(tmp_path / "outside.jpg")
    eg = _make_gateway(tmp_path)
    try:
        eg.attach_day_map(1, src)
        assert (tmp_path / "Maps").is_dir()
    finally:
        eg.close()


# ── clear ────────────────────────────────────────────────────────

def test_clear_day_map_deletes_file_and_nulls_db(tmp_path):
    src = _write_jpeg(tmp_path / "outside.jpg")
    eg = _make_gateway(tmp_path)
    try:
        eg.attach_day_map(2, src)
        assert (tmp_path / "Maps" / "day-02.jpg").exists()
        eg.clear_day_map(2)
        assert not (tmp_path / "Maps" / "day-02.jpg").exists()
        assert eg.get_day_map_path(2) is None
    finally:
        eg.close()


def test_clear_day_map_is_idempotent_when_unattached(tmp_path):
    """Clearing a day that never had a map is a no-op."""
    eg = _make_gateway(tmp_path)
    try:
        eg.clear_day_map(1)  # no map, no error
        assert eg.get_day_map_path(1) is None
    finally:
        eg.close()


def test_clear_event_map_deletes_file_and_nulls_db(tmp_path):
    src = _write_jpeg(tmp_path / "outside.jpg")
    eg = _make_gateway(tmp_path)
    try:
        eg.attach_event_map(src)
        assert (tmp_path / "Maps" / "event.jpg").exists()
        eg.clear_event_map()
        assert not (tmp_path / "Maps" / "event.jpg").exists()
        assert eg.get_event_map_path() is None
    finally:
        eg.close()


def test_clear_day_map_does_not_touch_other_days(tmp_path):
    """Clearing day 2 must not delete day 1's slot file."""
    src1 = _write_jpeg(tmp_path / "one.jpg")
    src2 = _write_jpeg(tmp_path / "two.jpg")
    eg = _make_gateway(tmp_path)
    try:
        eg.attach_day_map(1, src1)
        eg.attach_day_map(2, src2)
        eg.clear_day_map(2)
        assert (tmp_path / "Maps" / "day-01.jpg").exists()
        assert eg.get_day_map_path(1) == "Maps/day-01.jpg"
        assert not (tmp_path / "Maps" / "day-02.jpg").exists()
    finally:
        eg.close()


# ── isolation between day and event slots ───────────────────────

def test_attach_day_and_event_maps_are_independent(tmp_path):
    """Day-2 and event slots coexist without colliding."""
    src1 = _write_jpeg(tmp_path / "day.jpg")
    src2 = _write_jpeg(tmp_path / "evt.jpg")
    eg = _make_gateway(tmp_path)
    try:
        eg.attach_day_map(2, src1)
        eg.attach_event_map(src2)
        assert eg.get_day_map_path(2) == "Maps/day-02.jpg"
        assert eg.get_event_map_path() == "Maps/event.jpg"
        eg.clear_day_map(2)
        assert eg.get_day_map_path(2) is None
        # Clearing the day does not touch the event slot.
        assert eg.get_event_map_path() == "Maps/event.jpg"
        assert (tmp_path / "Maps" / "event.jpg").exists()
    finally:
        eg.close()
