"""Tests for the schedule + event-header map chip (spec/155)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtGui import QImage

from mira.ui.base.map_chip import MapChip
from mira.ui.pages.days_lists_page import DayRow, DaySnapshot, DaysListsPage


def _write_jpeg(path: Path) -> Path:
    img = QImage(8, 8, QImage.Format.Format_RGB32)
    img.fill(0x404040)
    img.save(str(path), "JPEG")
    return path


# ── MapChip widget ──────────────────────────────────────────────

def test_map_chip_empty_state_carries_attached_property_false(qapp, tmp_path):
    chip = MapChip(event_root=tmp_path)
    try:
        assert chip.map_path() is None
        assert chip.property("attached") == "false"
        # Empty state shows the "+" hint instead of a thumbnail.
        assert chip._thumb.text() == "+"
        assert chip._thumb.pixmap().isNull()
    finally:
        chip.deleteLater()


def test_map_chip_attached_state_loads_thumbnail(qapp, tmp_path):
    (tmp_path / "Maps").mkdir()
    _write_jpeg(tmp_path / "Maps" / "day-02.jpg")
    chip = MapChip(event_root=tmp_path)
    try:
        chip.set_map_path("Maps/day-02.jpg")
        assert chip.map_path() == "Maps/day-02.jpg"
        assert chip.property("attached") == "true"
        assert not chip._thumb.pixmap().isNull()
        assert chip._thumb.text() == ""
    finally:
        chip.deleteLater()


def test_map_chip_round_trips_back_to_empty(qapp, tmp_path):
    (tmp_path / "Maps").mkdir()
    _write_jpeg(tmp_path / "Maps" / "day-02.jpg")
    chip = MapChip(event_root=tmp_path)
    try:
        chip.set_map_path("Maps/day-02.jpg")
        chip.set_map_path(None)
        assert chip.property("attached") == "false"
        assert chip._thumb.text() == "+"
        assert chip.map_path() is None
    finally:
        chip.deleteLater()


def test_map_chip_missing_file_falls_back_to_question_mark(qapp, tmp_path):
    """If the DB says a map is attached but the file is missing on
    disk, the chip stays in attached-state but shows a fallback marker
    so the user can re-pick."""
    chip = MapChip(event_root=tmp_path)
    try:
        chip.set_map_path("Maps/missing.jpg")
        assert chip.property("attached") == "true"
        assert chip._thumb.text() == "?"
    finally:
        chip.deleteLater()


# ── DayRow chip placement ───────────────────────────────────────

def test_day_row_carries_chip_when_event_root_known(qapp, tmp_path):
    """When constructed with event_root, the row hosts a MapChip and
    forwards clicks via map_attach_requested(day_number)."""
    snap = DaySnapshot(day_number=2, title="Sintra", date_iso="2026-06-02",
                       location="Sintra, PT")
    row = DayRow(snap, event_root=tmp_path)
    try:
        assert row._map_chip is not None
        emitted = []
        row.map_attach_requested.connect(lambda n: emitted.append(n))
        row._map_chip.clicked.emit()
        assert emitted == [2]
    finally:
        row.deleteLater()


def test_day_row_skips_chip_when_event_root_missing(qapp):
    """Smoke-path rows (no event_root) do not host the chip."""
    snap = DaySnapshot(day_number=1, title="x", date_iso="2026-06-01")
    row = DayRow(snap)
    try:
        assert row._map_chip is None
    finally:
        row.deleteLater()


def test_day_row_renders_attached_state_from_snapshot(qapp, tmp_path):
    """The chip picks up the snapshot's ``map_rel`` at construction."""
    (tmp_path / "Maps").mkdir()
    _write_jpeg(tmp_path / "Maps" / "day-03.jpg")
    snap = DaySnapshot(
        day_number=3, title="x", date_iso="2026-06-03",
        map_rel="Maps/day-03.jpg",
    )
    row = DayRow(snap, event_root=tmp_path)
    try:
        assert row._map_chip is not None
        assert row._map_chip.map_path() == "Maps/day-03.jpg"
        assert row._map_chip.property("attached") == "true"
    finally:
        row.deleteLater()


def test_day_row_set_map_path_refreshes_chip_in_place(qapp, tmp_path):
    """set_map_path should re-render the chip without rebuilding the row."""
    snap = DaySnapshot(day_number=4, title="x", date_iso="2026-06-04")
    row = DayRow(snap, event_root=tmp_path)
    try:
        assert row._map_chip.property("attached") == "false"
        (tmp_path / "Maps").mkdir()
        _write_jpeg(tmp_path / "Maps" / "day-04.jpg")
        row.set_map_path("Maps/day-04.jpg")
        assert row._map_chip.property("attached") == "true"
        assert row._snapshot.map_rel == "Maps/day-04.jpg"
    finally:
        row.deleteLater()


# ── DaysListsPage event-map chip + signal wiring ────────────────

def test_page_event_map_chip_hidden_until_event_root_set(qapp):
    """No event open → no event-map chip visible."""
    page = DaysListsPage()
    try:
        assert page._event_map_chip is not None
        assert page._event_map_chip.isHidden() is True
    finally:
        page.deleteLater()


def test_page_set_event_root_shows_chip(qapp, tmp_path):
    """Binding the page to an event root reveals the event-map chip."""
    page = DaysListsPage()
    try:
        page.set_event_root(tmp_path)
        assert page._event_map_chip.isHidden() is False
        assert page._event_map_chip._event_root == tmp_path
    finally:
        page.deleteLater()


def test_page_event_map_chip_emits_attach_signal(qapp, tmp_path):
    page = DaysListsPage()
    try:
        page.set_event_root(tmp_path)
        emitted = []
        page.event_map_attach_requested.connect(lambda: emitted.append(True))
        page._event_map_chip.clicked.emit()
        assert emitted == [True]
    finally:
        page.deleteLater()


def test_page_set_event_map_path_updates_chip(qapp, tmp_path):
    (tmp_path / "Maps").mkdir()
    _write_jpeg(tmp_path / "Maps" / "event.jpg")
    page = DaysListsPage()
    try:
        page.set_event_root(tmp_path)
        page.set_event_map_path("Maps/event.jpg")
        assert page.event_map_path() == "Maps/event.jpg"
        assert page._event_map_chip.property("attached") == "true"
    finally:
        page.deleteLater()


def test_page_set_day_map_path_updates_day_row_chip(qapp, tmp_path):
    """After the host calls set_day_map_path, the row's chip refreshes
    and the cached snapshot carries the new map_rel."""
    page = DaysListsPage()
    try:
        page.set_event_root(tmp_path)
        page.setEventForPreview("Trip", [
            DaySnapshot(day_number=1, title="x", date_iso="2026-06-01"),
            DaySnapshot(day_number=2, title="y", date_iso="2026-06-02"),
        ])
        (tmp_path / "Maps").mkdir()
        _write_jpeg(tmp_path / "Maps" / "day-02.jpg")
        page.set_day_map_path(2, "Maps/day-02.jpg")
        row = page._find_day_row(2)
        assert row is not None
        assert row._snapshot.map_rel == "Maps/day-02.jpg"
        assert row._map_chip.property("attached") == "true"
        # The cached snapshot list is updated too.
        cached = next(s for s in page._snapshots if s.day_number == 2)
        assert cached.map_rel == "Maps/day-02.jpg"
    finally:
        page.deleteLater()


def test_page_day_row_attach_signal_routes_through_page(qapp, tmp_path):
    """Clicking a day-row chip surfaces as ``day_map_attach_requested``
    on the page, with the day_number payload."""
    page = DaysListsPage()
    try:
        page.set_event_root(tmp_path)
        page.setEventForPreview("Trip", [
            DaySnapshot(day_number=4, title="x", date_iso="2026-06-04"),
        ])
        emitted = []
        page.day_map_attach_requested.connect(lambda n: emitted.append(n))
        row = page._find_day_row(4)
        assert row is not None
        row._map_chip.clicked.emit()
        assert emitted == [4]
    finally:
        page.deleteLater()
