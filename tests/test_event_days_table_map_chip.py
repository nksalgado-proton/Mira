"""Tests for the spec/155 map chip on EventDaysTableDialog (the
schedule surface reached from the event tile's meta-line click)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QFileDialog

from core.scan_source import ScanDayRow
from mira.gateway.event_gateway import EventGateway
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.pages.event_days_table_dialog import (
    COL_COUNT,
    COL_MAP,
    COL_OVERRIDE,
    EventDaysTableDialog,
)


def _make_gateway(tmp_path: Path) -> EventGateway:
    store = EventStore.create(tmp_path / "e.db", event_id="evt-1")
    store.save_document(m.EventDocument(event=m.Event(
        uuid="evt-1", name="Trip", created_at="t", updated_at="t")))
    store.upsert(m.TripDay(day_number=1, date="2026-06-01"))
    store.upsert(m.TripDay(day_number=2, date="2026-06-02"))
    return EventGateway(store, event_root=tmp_path)


def _write_jpeg(path: Path) -> Path:
    img = QImage(16, 16, QImage.Format.Format_RGB32)
    img.fill(0x707070)
    img.save(str(path), "JPEG")
    return path


def _scan_rows() -> list[ScanDayRow]:
    return [
        ScanDayRow(date=date(2026, 6, 1), checked=True, location="Lisbon"),
        ScanDayRow(date=date(2026, 6, 2), checked=True, location="Sintra"),
    ]


# ── column layout ───────────────────────────────────────────────

def test_col_constants_make_map_sit_before_override():
    """spec/155 — the Map column sits between Description and Override
    so it stays adjacent to the day metadata it visualises."""
    assert COL_MAP == 6
    assert COL_OVERRIDE == 7
    assert COL_COUNT == 8


# ── chip column hidden without gateway ──────────────────────────

def test_map_column_hidden_when_gateway_absent(qapp):
    """The new-event scan path (no event.db yet) passes no gateway —
    the Map column hides so the dialog still renders cleanly."""
    dlg = EventDaysTableDialog(_scan_rows())
    try:
        assert dlg._maps_enabled is False
        assert dlg._table.isColumnHidden(COL_MAP) is True
        # No event-map chip on the header either.
        assert dlg._event_map_chip is None
    finally:
        dlg.deleteLater()


# ── chip column visible with gateway ────────────────────────────

def test_map_column_visible_when_gateway_present(qapp, tmp_path):
    eg = _make_gateway(tmp_path)
    try:
        dlg = EventDaysTableDialog(
            _scan_rows(),
            gateway=eg,
            day_number_by_date={
                date(2026, 6, 1): 1,
                date(2026, 6, 2): 2,
            },
        )
        try:
            assert dlg._maps_enabled is True
            assert dlg._table.isColumnHidden(COL_MAP) is False
            assert dlg._event_map_chip is not None
            assert dlg._event_map_chip.property("attached") == "false"
        finally:
            dlg.deleteLater()
    finally:
        eg.close()


def test_per_day_chip_paints_attached_state_from_scan_row(qapp, tmp_path):
    """When the ScanDayRow carries a map_image_path, the row's chip
    starts in attached state (no extra fetch)."""
    (tmp_path / "Maps").mkdir()
    _write_jpeg(tmp_path / "Maps" / "day-02.jpg")
    rows = [
        ScanDayRow(date=date(2026, 6, 1), checked=True, location="Lisbon"),
        ScanDayRow(date=date(2026, 6, 2), checked=True, location="Sintra",
                   map_image_path="Maps/day-02.jpg"),
    ]
    eg = _make_gateway(tmp_path)
    try:
        dlg = EventDaysTableDialog(
            rows,
            gateway=eg,
            day_number_by_date={
                date(2026, 6, 1): 1,
                date(2026, 6, 2): 2,
            },
        )
        try:
            chip_empty = dlg._row_map_chip(0)
            chip_attached = dlg._row_map_chip(1)
            assert chip_empty.property("attached") == "false"
            assert chip_attached.property("attached") == "true"
            assert chip_attached.map_path() == "Maps/day-02.jpg"
        finally:
            dlg.deleteLater()
    finally:
        eg.close()


def test_event_map_chip_paints_attached_state_from_constructor(
        qapp, tmp_path):
    """The header's Event map chip respects ``event_map_path`` on first
    paint."""
    (tmp_path / "Maps").mkdir()
    _write_jpeg(tmp_path / "Maps" / "event.jpg")
    eg = _make_gateway(tmp_path)
    try:
        dlg = EventDaysTableDialog(
            _scan_rows(),
            gateway=eg,
            day_number_by_date={
                date(2026, 6, 1): 1,
                date(2026, 6, 2): 2,
            },
            event_map_path="Maps/event.jpg",
        )
        try:
            assert dlg._event_map_chip.property("attached") == "true"
            assert dlg._event_map_chip.map_path() == "Maps/event.jpg"
        finally:
            dlg.deleteLater()
    finally:
        eg.close()


# ── attach flow ─────────────────────────────────────────────────

def test_per_day_chip_click_attaches_and_refreshes(
        qapp, tmp_path, monkeypatch):
    src = _write_jpeg(tmp_path / "outside.jpg")
    eg = _make_gateway(tmp_path)
    try:
        monkeypatch.setattr(
            QFileDialog, "getOpenFileName",
            lambda *args, **kwargs: (str(src), ""),
        )
        dlg = EventDaysTableDialog(
            _scan_rows(),
            gateway=eg,
            day_number_by_date={
                date(2026, 6, 1): 1,
                date(2026, 6, 2): 2,
            },
        )
        try:
            # Patch the dialog's exec so it doesn't block; simulate
            # the user's pick + close path.
            from mira.ui.base.map_attach_dialog import MapAttachDialog
            orig_exec = MapAttachDialog.exec

            def fake_exec(self):
                self._on_pick()
                return 1  # Accepted
            monkeypatch.setattr(MapAttachDialog, "exec", fake_exec)
            try:
                dlg._open_map_dialog_for_row(1)
            finally:
                monkeypatch.setattr(MapAttachDialog, "exec", orig_exec)
            # DB now carries the path; chip refreshed; ScanDayRow updated.
            assert eg.get_day_map_path(2) == "Maps/day-02.jpg"
            assert dlg._rows[1].map_image_path == "Maps/day-02.jpg"
            assert dlg._row_map_chip(1).property("attached") == "true"
        finally:
            dlg.deleteLater()
    finally:
        eg.close()


def test_event_map_chip_click_attaches_and_refreshes(
        qapp, tmp_path, monkeypatch):
    src = _write_jpeg(tmp_path / "outside.jpg")
    eg = _make_gateway(tmp_path)
    try:
        monkeypatch.setattr(
            QFileDialog, "getOpenFileName",
            lambda *args, **kwargs: (str(src), ""),
        )
        dlg = EventDaysTableDialog(
            _scan_rows(),
            gateway=eg,
            day_number_by_date={
                date(2026, 6, 1): 1,
                date(2026, 6, 2): 2,
            },
        )
        try:
            from mira.ui.base.map_attach_dialog import MapAttachDialog
            orig_exec = MapAttachDialog.exec

            def fake_exec(self):
                self._on_pick()
                return 1
            monkeypatch.setattr(MapAttachDialog, "exec", fake_exec)
            try:
                dlg._open_event_map_dialog()
            finally:
                monkeypatch.setattr(MapAttachDialog, "exec", orig_exec)
            assert eg.get_event_map_path() == "Maps/event.jpg"
            assert dlg._event_map_rel == "Maps/event.jpg"
            assert dlg._event_map_chip.property("attached") == "true"
        finally:
            dlg.deleteLater()
    finally:
        eg.close()


def test_dialog_renders_in_smoke_path_without_gateway(qapp):
    """Constructing the dialog with no gateway (new-event scan path)
    must still work — the Map column simply stays hidden."""
    dlg = EventDaysTableDialog(_scan_rows())
    try:
        # Two rows, each with a placeholder cell at COL_MAP.
        assert dlg._table.rowCount() == 2
        for r in range(2):
            assert dlg._table.cellWidget(r, COL_MAP) is not None
            assert dlg._row_map_chip(r) is None
    finally:
        dlg.deleteLater()
