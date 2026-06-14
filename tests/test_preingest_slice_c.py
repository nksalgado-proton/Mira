"""Slice C tests for the rebuild ``mira.ui.pages.preingest_dialog``.

Pin the new affordances added in Slice C:

* Include checkbox + Browse button on every per-day card.
* ``included_day_numbers`` / ``included_source_paths`` API.
* At-least-one-day Apply gate (warning + no accept when zero days included).
* ClassificationPanel mounted at the top, seeded from the event row,
  persisted through ``Gateway.set_classification`` on Apply.

The pre-existing rebuild dialog tests live in tests/test_preingest_dialog.py
under the *legacy* import path; this file targets the rebuild copy explicitly
and shares no fixtures with that file.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

try:
    from PyQt6.QtWidgets import QApplication, QPushButton
except ImportError:                                      # pragma: no cover
    QApplication = None
    QPushButton = None

from core.fresh_source import SourceItem
from core.models import Event as LegacyEvent, TripDay as LegacyTripDay
from mira.gateway import EventsIndex, Gateway
from mira.settings.repo import SettingsRepo
from mira.store import models as m
from mira.ui.pages.preingest_dialog import PreingestPlanConfirmDialog

NOW = "2026-06-01T00:00:00+00:00"


@pytest.fixture
def qapp():
    if QApplication is None:
        pytest.skip("PyQt6 not installed")
    app = QApplication.instance() or QApplication([])
    yield app


def _trip_day(n, d, *, desc="x", tz=-3.0):
    return LegacyTripDay(day_number=n, date=d, description=desc, tz_offset=tz, location=None)


def _legacy_event(plan):
    return LegacyEvent(id="evt-c", name="Slice C test", start_date=plan[0].date, trip_days=plan)


def _item(path, ts):
    return SourceItem(path=Path(path), timestamp=ts, camera_id="DC-G9M2")


def _make_gateway_with_event(tmp_path) -> tuple[Gateway, str, LegacyEvent]:
    base = tmp_path / "lib"
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "settings.json"),
        index=EventsIndex(tmp_path / "events_index.json"),
    )
    gw.set_photos_base_path(str(base))
    plan = [
        _trip_day(1, date(2026, 5, 27)),
        _trip_day(2, date(2026, 5, 28)),
        _trip_day(3, date(2026, 5, 29)),
    ]
    legacy = _legacy_event(plan)
    doc = m.EventDocument(
        event=m.Event(
            uuid="evt-c", name="Slice C test",
            created_at=NOW, updated_at=NOW,
            start_date="2026-05-27", event_type="trip",
            event_subtype="One week",
        ),
        trip_days=[
            m.TripDay(day_number=d.day_number, date=d.date.isoformat(),
                      description=d.description, location=d.location,
                      tz_minutes=int(d.tz_offset * 60))
            for d in plan
        ],
    )
    gw.create_event(doc, base / "evt-c").close()
    return gw, "evt-c", legacy


# ── _DayCard: include checkbox + browse button ─────────────────────────────


def test_day_card_has_include_checkbox_default_true(qapp, tmp_path):
    _gw, _eid, legacy = _make_gateway_with_event(tmp_path)
    items = [
        _item("/a/1.jpg", datetime(2026, 5, 27, 10)),
        _item("/a/2.jpg", datetime(2026, 5, 28, 11)),
    ]
    dlg = PreingestPlanConfirmDialog(legacy, items)
    try:
        for card in dlg._day_cards:
            assert card.is_included() is True
            assert card._include_check is not None
            assert card._browse_button is not None
    finally:
        dlg.deleteLater()


def test_day_card_set_included_toggles_state(qapp, tmp_path):
    _gw, _eid, legacy = _make_gateway_with_event(tmp_path)
    items = [
        _item("/a/1.jpg", datetime(2026, 5, 27, 10)),
        _item("/a/2.jpg", datetime(2026, 5, 28, 11)),
    ]
    dlg = PreingestPlanConfirmDialog(legacy, items)
    try:
        card = dlg._day_cards[0]
        card.set_included(False)
        assert card.is_included() is False
        card.set_included(True)
        assert card.is_included() is True
    finally:
        dlg.deleteLater()


# ── Dialog: included_day_numbers / included_source_paths ───────────────────


def test_included_day_numbers_default_all_days(qapp, tmp_path):
    _gw, _eid, legacy = _make_gateway_with_event(tmp_path)
    items = [
        _item("/a/1.jpg", datetime(2026, 5, 27, 10)),
        _item("/a/2.jpg", datetime(2026, 5, 28, 11)),
    ]
    dlg = PreingestPlanConfirmDialog(legacy, items)
    try:
        assert dlg.included_day_numbers() == frozenset({1, 2})
    finally:
        dlg.deleteLater()


def test_included_day_numbers_drops_unchecked(qapp, tmp_path):
    _gw, _eid, legacy = _make_gateway_with_event(tmp_path)
    items = [
        _item("/a/1.jpg", datetime(2026, 5, 27, 10)),
        _item("/a/2.jpg", datetime(2026, 5, 28, 11)),
    ]
    dlg = PreingestPlanConfirmDialog(legacy, items)
    try:
        dlg._day_cards[0].set_included(False)
        assert dlg.included_day_numbers() == frozenset({2})
    finally:
        dlg.deleteLater()


def test_included_source_paths_match_included_days(qapp, tmp_path):
    _gw, _eid, legacy = _make_gateway_with_event(tmp_path)
    items = [
        _item("/a/1.jpg", datetime(2026, 5, 27, 10)),
        _item("/a/2.jpg", datetime(2026, 5, 28, 11)),
    ]
    dlg = PreingestPlanConfirmDialog(legacy, items)
    try:
        dlg._day_cards[0].set_included(False)
        paths = dlg.included_source_paths()
        assert Path("/a/2.jpg") in paths
        assert Path("/a/1.jpg") not in paths
    finally:
        dlg.deleteLater()


# ── Apply gate: at-least-one-day required ──────────────────────────────────


def test_apply_blocks_when_no_days_included(qapp, tmp_path):
    gw, eid, legacy = _make_gateway_with_event(tmp_path)
    items = [
        _item("/a/1.jpg", datetime(2026, 5, 27, 10)),
        _item("/a/2.jpg", datetime(2026, 5, 28, 11)),
    ]
    dlg = PreingestPlanConfirmDialog(legacy, items, gateway=gw, event_id=eid)
    try:
        for card in dlg._day_cards:
            card.set_included(False)
        with patch("PyQt6.QtWidgets.QMessageBox.warning") as warn:
            dlg._on_apply()
            assert warn.called
        # Apply did not accept the dialog
        from PyQt6.QtWidgets import QDialog
        assert dlg.result() != int(QDialog.DialogCode.Accepted)
    finally:
        dlg.deleteLater()


# ── Classification: seeded from event + persisted on Apply ─────────────────


def test_classification_seeded_from_event(qapp, tmp_path):
    gw, eid, legacy = _make_gateway_with_event(tmp_path)
    items = [_item("/a/1.jpg", datetime(2026, 5, 27, 10))]
    dlg = PreingestPlanConfirmDialog(legacy, items, gateway=gw, event_id=eid)
    try:
        v = dlg._classification.values()
        assert v.event_type == "trip"
        assert v.event_subtype == "One week"
    finally:
        dlg.deleteLater()


def test_apply_persists_classification_through_gateway(qapp, tmp_path):
    gw, eid, legacy = _make_gateway_with_event(tmp_path)
    items = [_item("/a/1.jpg", datetime(2026, 5, 27, 10))]
    dlg = PreingestPlanConfirmDialog(legacy, items, gateway=gw, event_id=eid)
    try:
        # User edits classification (description + new subtype). spec/52
        # retired event-level tags; the panel's tags widget still exists
        # but no longer round-trips through the gateway.
        dlg._classification.set_values(
            event_type="trip", event_subtype="Two weeks",
            description="Reclassified at pre-ingest.",
        )
        dlg._on_apply()
        eg = gw.open_event(eid)
        try:
            ev = eg.event()
            assert ev.event_subtype == "Two weeks"
            assert ev.description == "Reclassified at pre-ingest."
        finally:
            eg.close()
    finally:
        dlg.deleteLater()
