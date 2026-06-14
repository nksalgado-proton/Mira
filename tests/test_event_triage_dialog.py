"""Slice E tests — EventTriageDialog + suggest_type_from_signals heuristic.

Covers the heuristic table, the dialog's row list (unclassified-only), the
per-row picker persist, the suggested column, and the empty-state hint.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

try:
    from PyQt6.QtWidgets import QApplication, QComboBox
except ImportError:                                      # pragma: no cover
    QApplication = None
    QComboBox = None

from mira import event_classification as ec
from mira.gateway import EventsIndex, Gateway
from mira.settings.repo import SettingsRepo
from mira.store import models as m
from mira.ui.pages.event_triage_dialog import (
    _COL_NAME,
    _COL_SUGGESTED,
    _COL_TYPE_PICKER,
    EventTriageDialog,
)

NOW = "2026-06-01T00:00:00+00:00"


@pytest.fixture
def qapp():
    if QApplication is None:
        pytest.skip("PyQt6 not installed")
    app = QApplication.instance() or QApplication([])
    yield app


# ── Heuristic ─────────────────────────────────────────────────────────────


def test_suggest_session_for_one_day_one_camera():
    assert ec.suggest_type_from_signals(day_count=1, camera_count=1, tz_count=1) == "session"


def test_suggest_trip_for_long_multi_tz():
    assert ec.suggest_type_from_signals(day_count=14, camera_count=2, tz_count=3) == "trip"


def test_suggest_none_in_the_grey_zone():
    """2–4 days could be Trip / Session / Occasion — no clear guess."""
    assert ec.suggest_type_from_signals(day_count=3, camera_count=2, tz_count=1) is None


def test_suggest_none_for_single_day_multi_camera():
    """Two-camera shoots break the 1-day-1-camera Session signal."""
    assert ec.suggest_type_from_signals(day_count=1, camera_count=2, tz_count=1) is None


def test_suggest_none_for_long_single_tz():
    """5+ days but a single TZ is more likely a project / studio series."""
    assert ec.suggest_type_from_signals(day_count=8, camera_count=1, tz_count=1) is None


# ── Dialog: row collection + persist + empty state ────────────────────────


def _make_library(tmp_path):
    base = tmp_path / "lib"
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "settings.json"),
        index=EventsIndex(tmp_path / "events_index.json"),
    )
    gw.set_photos_base_path(str(base))
    seeds = [
        ("e-1", "Costa Rica", "2026-04-01", "unclassified", [
            (1, "2026-04-01", -360), (2, "2026-04-02", -360),
            (3, "2026-04-03", -360), (4, "2026-04-04", -360),
            (5, "2026-04-05", 60), (6, "2026-04-06", 60),
        ], ["G9M2", "iPhone"]),                # heuristic → trip (6 days, 2 TZ)
        ("e-2", "Birds", "2026-05-12", "unclassified", [
            (1, "2026-05-12", -180),
        ], ["G9M2"]),                          # heuristic → session (1 day, 1 cam)
        ("e-3", "Already typed", "2026-05-20", "trip", [
            (1, "2026-05-20", -180),
        ], ["G9M2"]),                          # excluded from the triage list
    ]
    for uuid, name, sd, typ, days, cams in seeds:
        doc = m.EventDocument(
            event=m.Event(
                uuid=uuid, name=name, created_at=NOW, updated_at=NOW,
                start_date=sd, event_type=typ,
            ),
            trip_days=[
                m.TripDay(day_number=n, date=d, tz_minutes=tz)
                for (n, d, tz) in days
            ],
            cameras=[m.Camera(camera_id=c) for c in cams],
        )
        gw.create_event(doc, base / uuid).close()
    return gw


def test_dialog_lists_unclassified_only(qapp, tmp_path):
    gw = _make_library(tmp_path)
    dlg = EventTriageDialog(gw)
    try:
        names = [dlg._table.item(r, _COL_NAME).text() for r in range(dlg._table.rowCount())]
        assert set(names) == {"Costa Rica", "Birds"}
        assert "Already typed" not in names
    finally:
        dlg.deleteLater()


def test_dialog_suggested_column_reflects_heuristic(qapp, tmp_path):
    gw = _make_library(tmp_path)
    dlg = EventTriageDialog(gw)
    try:
        suggestions = {
            dlg._table.item(r, _COL_NAME).text():
            dlg._table.item(r, _COL_SUGGESTED).text()
            for r in range(dlg._table.rowCount())
        }
        assert suggestions["Costa Rica"] == "Trip"
        assert suggestions["Birds"] == "Session"
    finally:
        dlg.deleteLater()


def test_dialog_picker_persists_through_gateway(qapp, tmp_path):
    gw = _make_library(tmp_path)
    dlg = EventTriageDialog(gw)
    try:
        # Find the row for "Costa Rica" and pick Trip via its combo
        for r in range(dlg._table.rowCount()):
            if dlg._table.item(r, _COL_NAME).text() == "Costa Rica":
                combo: QComboBox = dlg._table.cellWidget(r, _COL_TYPE_PICKER)
                idx = combo.findData("trip")
                combo.setCurrentIndex(idx)
                dlg._on_type_picked("e-1", combo)
                break
    finally:
        dlg.deleteLater()

    eg = gw.open_event("e-1")
    try:
        assert eg.event().event_type == "trip"
    finally:
        eg.close()


def test_dialog_event_classified_signal_fires_with_id_and_type(qapp, tmp_path):
    gw = _make_library(tmp_path)
    dlg = EventTriageDialog(gw)
    try:
        fired: list[tuple] = []
        dlg.event_classified.connect(lambda eid, et: fired.append((eid, et)))
        for r in range(dlg._table.rowCount()):
            if dlg._table.item(r, _COL_NAME).text() == "Birds":
                combo: QComboBox = dlg._table.cellWidget(r, _COL_TYPE_PICKER)
                idx = combo.findData("session")
                combo.setCurrentIndex(idx)
                dlg._on_type_picked("e-2", combo)
                break
        assert ("e-2", "session") in fired
    finally:
        dlg.deleteLater()


def test_dialog_empty_state_shows_hint(qapp, tmp_path):
    """When the library has zero unclassified events, the table is hidden
    and the hint label is shown."""
    base = tmp_path / "lib"
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "settings.json"),
        index=EventsIndex(tmp_path / "events_index.json"),
    )
    gw.set_photos_base_path(str(base))
    # All events classified — none qualify for triage
    doc = m.EventDocument(event=m.Event(
        uuid="e-only", name="Only event",
        created_at=NOW, updated_at=NOW,
        start_date="2026-05-12", event_type="trip",
    ))
    gw.create_event(doc, base / "e-only").close()

    dlg = EventTriageDialog(gw)
    try:
        assert dlg._table.rowCount() == 0
        assert not dlg._empty.isHidden()
        assert dlg._table.isHidden()
    finally:
        dlg.deleteLater()
