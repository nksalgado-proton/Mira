"""Tests for the day/event map attach dialog (spec/155)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import QByteArray, QBuffer, QIODevice
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QFileDialog

from mira.gateway.event_gateway import EventGateway
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.base.map_attach_dialog import MapAttachDialog


def _make_gateway(tmp_path: Path) -> EventGateway:
    store = EventStore.create(tmp_path / "e.db", event_id="evt-1")
    store.save_document(m.EventDocument(event=m.Event(
        uuid="evt-1", name="Trip", created_at="t", updated_at="t")))
    store.upsert(m.TripDay(day_number=1, date="2026-06-01"))
    store.upsert(m.TripDay(day_number=2, date="2026-06-02"))
    return EventGateway(store, event_root=tmp_path)


def _write_jpeg(path: Path) -> Path:
    """Write a tiny but valid JPEG so QPixmap can load it."""
    img = QImage(16, 16, QImage.Format.Format_RGB32)
    img.fill(0x808080)
    img.save(str(path), "JPEG")
    return path


# ── empty-state UI ──────────────────────────────────────────────

def test_empty_state_shows_pick_button_only(qapp, tmp_path):
    """When no map is attached, the dialog shows ``Pick image…`` and
    hides ``Replace…`` / ``Remove``."""
    eg = _make_gateway(tmp_path)
    try:
        dlg = MapAttachDialog(eg, day_number=1)
        try:
            assert dlg._pick_button.isHidden() is False
            assert dlg._replace_button.isHidden() is True
            assert dlg._remove_button.isHidden() is True
            assert dlg.current_relpath() is None
        finally:
            dlg.deleteLater()
    finally:
        eg.close()


def test_empty_state_placeholder_text(qapp, tmp_path):
    """The preview pane carries the ``No map attached`` placeholder."""
    eg = _make_gateway(tmp_path)
    try:
        dlg = MapAttachDialog(eg, day_number=1)
        try:
            assert dlg._preview.text() == "No map attached."
            # No pixmap rendered.
            assert dlg._preview.pixmap().isNull()
        finally:
            dlg.deleteLater()
    finally:
        eg.close()


# ── attached-state UI ───────────────────────────────────────────

def test_attached_state_shows_replace_and_remove(qapp, tmp_path):
    """When a map is attached, the dialog shows Replace/Remove and
    hides Pick."""
    src = _write_jpeg(tmp_path / "outside.jpg")
    eg = _make_gateway(tmp_path)
    try:
        eg.attach_day_map(2, src)
        dlg = MapAttachDialog(eg, day_number=2)
        try:
            assert dlg._pick_button.isHidden() is True
            assert dlg._replace_button.isHidden() is False
            assert dlg._remove_button.isHidden() is False
            assert dlg.current_relpath() == "Maps/day-02.jpg"
            # Preview pixmap is loaded (non-null).
            assert not dlg._preview.pixmap().isNull()
            # Meta line carries the relative path and the slot dimensions.
            assert "Maps/day-02.jpg" in dlg._meta.text()
            assert "16 × 16" in dlg._meta.text()
        finally:
            dlg.deleteLater()
    finally:
        eg.close()


def test_event_mode_titles_say_event_map(qapp, tmp_path):
    """``day_number=None`` puts the dialog in event-map mode."""
    eg = _make_gateway(tmp_path)
    try:
        dlg = MapAttachDialog(eg, day_number=None)
        try:
            assert dlg.windowTitle() == "Event map"
        finally:
            dlg.deleteLater()
    finally:
        eg.close()


def test_day_mode_title_carries_day_number(qapp, tmp_path):
    """The day-mode title interpolates the day number."""
    eg = _make_gateway(tmp_path)
    try:
        dlg = MapAttachDialog(eg, day_number=7)
        try:
            assert dlg.windowTitle() == "Day 7 map"
        finally:
            dlg.deleteLater()
    finally:
        eg.close()


# ── pick flow ────────────────────────────────────────────────────

def test_pick_flow_attaches_and_emits_signal(qapp, tmp_path, monkeypatch):
    """Picking a JPEG copies it into ``Maps/day-NN.jpg``, refreshes the
    dialog into attached state, and fires :attr:`mapChanged`."""
    src = _write_jpeg(tmp_path / "outside.jpg")
    eg = _make_gateway(tmp_path)
    try:
        monkeypatch.setattr(
            QFileDialog, "getOpenFileName",
            lambda *args, **kwargs: (str(src), ""),
        )
        dlg = MapAttachDialog(eg, day_number=1)
        try:
            emitted = []
            dlg.mapChanged.connect(lambda: emitted.append(True))
            dlg._on_pick()
            assert emitted == [True]
            assert dlg.current_relpath() == "Maps/day-01.jpg"
            # Dialog now reads as attached.
            assert dlg._pick_button.isHidden() is True
            assert dlg._replace_button.isHidden() is False
        finally:
            dlg.deleteLater()
    finally:
        eg.close()


def test_pick_flow_cancelled_does_not_attach(qapp, tmp_path, monkeypatch):
    """Cancelling the file picker (empty string) doesn't change state."""
    eg = _make_gateway(tmp_path)
    try:
        monkeypatch.setattr(
            QFileDialog, "getOpenFileName",
            lambda *args, **kwargs: ("", ""),
        )
        dlg = MapAttachDialog(eg, day_number=1)
        try:
            emitted = []
            dlg.mapChanged.connect(lambda: emitted.append(True))
            dlg._on_pick()
            assert emitted == []
            assert dlg.current_relpath() is None
        finally:
            dlg.deleteLater()
    finally:
        eg.close()


def test_pick_flow_invalid_extension_surfaces_error(
        qapp, tmp_path, monkeypatch):
    """Picking a non-image file surfaces ``show_error`` and leaves the
    DB clean."""
    rogue = tmp_path / "rogue.tiff"
    rogue.write_bytes(b"not a real tiff")
    eg = _make_gateway(tmp_path)
    try:
        monkeypatch.setattr(
            QFileDialog, "getOpenFileName",
            lambda *args, **kwargs: (str(rogue), ""),
        )
        from mira.ui.design import dialogs as design_dialogs
        called = []
        monkeypatch.setattr(
            design_dialogs, "show_error",
            lambda *args, **kwargs: called.append(args))
        dlg = MapAttachDialog(eg, day_number=1)
        try:
            dlg._on_pick()
            assert called, "show_error was not invoked for a rejected pick"
            assert dlg.current_relpath() is None
        finally:
            dlg.deleteLater()
    finally:
        eg.close()


# ── remove flow ──────────────────────────────────────────────────

def test_remove_flow_clears_and_emits_signal(qapp, tmp_path, monkeypatch):
    """``Remove`` calls clear_*_map and fires :attr:`mapChanged`."""
    src = _write_jpeg(tmp_path / "outside.jpg")
    eg = _make_gateway(tmp_path)
    try:
        eg.attach_day_map(2, src)
        # Force the destructive confirm to return True.
        from mira.ui.base import map_attach_dialog as mod
        monkeypatch.setattr(
            mod, "confirm_destructive",
            lambda *args, **kwargs: True)
        dlg = MapAttachDialog(eg, day_number=2)
        try:
            emitted = []
            dlg.mapChanged.connect(lambda: emitted.append(True))
            dlg._on_remove()
            assert emitted == [True]
            assert dlg.current_relpath() is None
            # Dialog now reads as empty.
            assert dlg._pick_button.isHidden() is False
            assert dlg._remove_button.isHidden() is True
        finally:
            dlg.deleteLater()
    finally:
        eg.close()


def test_remove_flow_cancelled_keeps_attachment(qapp, tmp_path, monkeypatch):
    """If the user cancels the destructive confirm, the map stays."""
    src = _write_jpeg(tmp_path / "outside.jpg")
    eg = _make_gateway(tmp_path)
    try:
        eg.attach_day_map(2, src)
        from mira.ui.base import map_attach_dialog as mod
        monkeypatch.setattr(
            mod, "confirm_destructive",
            lambda *args, **kwargs: False)
        dlg = MapAttachDialog(eg, day_number=2)
        try:
            dlg._on_remove()
            assert dlg.current_relpath() == "Maps/day-02.jpg"
        finally:
            dlg.deleteLater()
    finally:
        eg.close()
