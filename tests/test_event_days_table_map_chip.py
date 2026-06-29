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
            # Header event-map button is rendered + reads empty (no
            # event_map_path passed).
            assert dlg._event_map_chip is not None
            assert dlg._event_map_rel is None
            assert dlg._event_map_chip.toolTip() == (
                "Attach a map for the whole event.")
        finally:
            dlg.deleteLater()
    finally:
        eg.close()


def test_per_day_button_paints_attached_state_from_scan_row(qapp, tmp_path):
    """When the ScanDayRow carries a map_image_path, the row's button
    starts in attached state — tooltip flips from "Attach…" to
    "Replace or remove…" so the user knows it's already set."""
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
            btn_empty = dlg._row_map_button(0)
            btn_attached = dlg._row_map_button(1)
            assert btn_empty is not None and btn_attached is not None
            assert btn_empty.toolTip() == (
                "Attach a map for this day (JPEG, PNG or MP4).")
            assert btn_attached.toolTip() == (
                "Replace or remove the day's map.")
            assert dlg._rows[1].map_image_path == "Maps/day-02.jpg"
            # The button uses the dialog's PlanBrowseCell chrome — same
            # object name as the Browse column control.
            assert btn_attached.objectName() == "PlanBrowseCell"
        finally:
            dlg.deleteLater()
    finally:
        eg.close()


def test_event_map_button_paints_attached_state_from_constructor(
        qapp, tmp_path):
    """The header's Event map button respects ``event_map_path`` on
    first paint — tooltip is the "Replace or remove…" variant."""
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
            assert dlg._event_map_rel == "Maps/event.jpg"
            assert dlg._event_map_chip.toolTip() == (
                "Replace or remove the event map.")
            assert dlg._event_map_chip.objectName() == "PlanBrowseCell"
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
            # DB now carries the path; ScanDayRow updated; button's
            # tooltip flipped to the "Replace or remove…" variant.
            assert eg.get_day_map_path(2) == "Maps/day-02.jpg"
            assert dlg._rows[1].map_image_path == "Maps/day-02.jpg"
            assert dlg._row_map_button(1).toolTip() == (
                "Replace or remove the day's map.")
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
            assert dlg._event_map_chip.toolTip() == (
                "Replace or remove the event map.")
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
            assert dlg._row_map_button(r) is None
    finally:
        dlg.deleteLater()
