"""spec/45 Slice TZ-2 — preingest dialog auto-fills country per day.

End-to-end tests: phone items with GPS + TZ → preingest dialog runs
country derivation → day cards render the hint → Apply persists to
trip_day.extras_json via the new gateway shallow-merge seam.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

try:
    from PyQt6.QtWidgets import QApplication
except ImportError:                                      # pragma: no cover
    QApplication = None

from core.fresh_source import SourceItem
from core.models import Event as LegacyEvent, TripDay as LegacyTripDay
from mira.gateway import EventsIndex, Gateway
from mira.settings.repo import SettingsRepo
from mira.store import models as m
from mira.ui.pages.preingest_dialog import PreingestPlanConfirmDialog

NOW = "2026-06-06T00:00:00+00:00"


@pytest.fixture
def qapp():
    if QApplication is None:
        pytest.skip("PyQt6 not installed")
    app = QApplication.instance() or QApplication([])
    yield app


def _trip_day(n, d, *, desc="x", tz=2.0):
    return LegacyTripDay(day_number=n, date=d, description=desc, tz_offset=tz, location=None)


def _legacy_event(plan):
    return LegacyEvent(id="evt-tz2", name="Slice TZ-2 test",
                       start_date=plan[0].date, trip_days=plan)


def _phone_item(path, ts, *, lat, lon, tz_minutes=120) -> SourceItem:
    """A phone-shape SourceItem: has tz_offset + GPS, like an iPhone."""
    return SourceItem(
        path=Path(path), timestamp=ts, camera_id="iPhone",
        tz_offset_minutes=tz_minutes,
        gps_lat=lat, gps_lon=lon,
    )


def _make_gateway_with_event(tmp_path):
    base = tmp_path / "lib"
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "settings.json"),
        index=EventsIndex(tmp_path / "events_index.json"),
    )
    gw.set_photos_base_path(str(base))
    plan = [
        _trip_day(1, date(2026, 5, 27)),
        _trip_day(2, date(2026, 5, 28)),
    ]
    legacy = _legacy_event(plan)
    doc = m.EventDocument(
        event=m.Event(
            uuid="evt-tz2", name="Slice TZ-2 test",
            created_at=NOW, updated_at=NOW,
            start_date="2026-05-27",
        ),
        trip_days=[
            m.TripDay(day_number=d.day_number, date=d.date.isoformat(),
                      description=d.description, location=d.location,
                      tz_minutes=int(d.tz_offset * 60))
            for d in plan
        ],
    )
    gw.create_event(doc, base / "evt-tz2").close()
    return gw, "evt-tz2", legacy


# ── Gateway: set_trip_day_extras shallow-merge ────────────────────────────


def test_set_trip_day_extras_writes_country_code(qapp, tmp_path):
    gw, eid, _legacy = _make_gateway_with_event(tmp_path)
    eg = gw.open_event(eid)
    try:
        eg.set_trip_day_extras(1, {"country_code": "IT"})
        days = {d.day_number: d for d in eg.trip_days()}
        extras = json.loads(days[1].extras_json or "{}")
        assert extras.get("country_code") == "IT"
    finally:
        eg.close()


def test_set_trip_day_extras_shallow_merges_preserving_other_keys(qapp, tmp_path):
    """A subsequent write of country_code mustn't clobber an unrelated key
    that the user (or a future surface) set elsewhere."""
    gw, eid, _legacy = _make_gateway_with_event(tmp_path)
    eg = gw.open_event(eid)
    try:
        eg.set_trip_day_extras(1, {"city": "Rome"})
        eg.set_trip_day_extras(1, {"country_code": "IT"})
        days = {d.day_number: d for d in eg.trip_days()}
        extras = json.loads(days[1].extras_json or "{}")
        assert extras.get("city") == "Rome"
        assert extras.get("country_code") == "IT"
    finally:
        eg.close()


def test_set_trip_day_extras_empty_updates_noop(qapp, tmp_path):
    gw, eid, _legacy = _make_gateway_with_event(tmp_path)
    eg = gw.open_event(eid)
    try:
        before = {d.day_number: d.extras_json for d in eg.trip_days()}
        eg.set_trip_day_extras(1, {})
        after = {d.day_number: d.extras_json for d in eg.trip_days()}
        assert before == after
    finally:
        eg.close()


def test_set_trip_day_extras_unknown_day_warns_noop(qapp, tmp_path, caplog):
    import logging as _logging
    gw, eid, _legacy = _make_gateway_with_event(tmp_path)
    eg = gw.open_event(eid)
    try:
        with caplog.at_level(_logging.WARNING):
            eg.set_trip_day_extras(99, {"country_code": "IT"})
        assert any("99" in r.message for r in caplog.records)
    finally:
        eg.close()


# ── Dialog: day card renders country + Apply persists ─────────────────────


def test_day_card_shows_country_hint_when_detected(qapp, tmp_path):
    gw, eid, legacy = _make_gateway_with_event(tmp_path)
    items = [
        # Rome (Italy) — day 1
        _phone_item("/r1.jpg", datetime(2026, 5, 27, 10), lat=41.9, lon=12.5),
        _phone_item("/r2.jpg", datetime(2026, 5, 27, 12), lat=41.9, lon=12.5),
        # São Paulo (Brazil) — day 2 (cross-continent for clarity)
        _phone_item("/sp.jpg", datetime(2026, 5, 28, 14),
                    lat=-23.5, lon=-46.6, tz_minutes=-180),
    ]
    dlg = PreingestPlanConfirmDialog(legacy, items, gateway=gw, event_id=eid)
    try:
        per_day = {c._trip_day.day_number: c for c in dlg._day_cards}
        assert per_day[1].detected_country_code() == "IT"
        assert per_day[2].detected_country_code() == "BR"
        # The hint label visibility tracks the detected code.
        assert per_day[1]._country_label is not None
        assert not per_day[1]._country_label.isHidden()
    finally:
        dlg.deleteLater()


def test_day_card_country_hint_hidden_when_no_phone_gps(qapp, tmp_path):
    """A day with only camera-shape items (no tz_offset, no GPS) gets no
    country detection and the hint stays hidden."""
    gw, eid, legacy = _make_gateway_with_event(tmp_path)
    items = [
        SourceItem(
            path=Path("/c.RW2"), timestamp=datetime(2026, 5, 27, 10),
            camera_id="G9M2",
        ),
    ]
    dlg = PreingestPlanConfirmDialog(legacy, items, gateway=gw, event_id=eid)
    try:
        card = dlg._day_cards[0]
        assert card.detected_country_code() is None
        assert card._country_label.isHidden()
    finally:
        dlg.deleteLater()


def test_apply_persists_detected_country_to_trip_day_extras(qapp, tmp_path):
    """End-to-end: Apply writes country_code via the gateway shallow-merge
    so the next time the event is opened, trip_day.extras_json.country_code
    is there."""
    gw, eid, legacy = _make_gateway_with_event(tmp_path)
    items = [
        _phone_item("/r1.jpg", datetime(2026, 5, 27, 10), lat=41.9, lon=12.5),
        _phone_item("/sp.jpg", datetime(2026, 5, 28, 14),
                    lat=-23.5, lon=-46.6, tz_minutes=-180),
    ]
    dlg = PreingestPlanConfirmDialog(legacy, items, gateway=gw, event_id=eid)
    try:
        dlg._on_apply()
    finally:
        dlg.deleteLater()

    eg = gw.open_event(eid)
    try:
        days = {d.day_number: d for d in eg.trip_days()}
        d1_extras = json.loads(days[1].extras_json or "{}")
        d2_extras = json.loads(days[2].extras_json or "{}")
        assert d1_extras.get("country_code") == "IT"
        assert d2_extras.get("country_code") == "BR"
    finally:
        eg.close()


def test_apply_skips_country_for_unticked_days(qapp, tmp_path):
    """If the user unticks a day via the Slice C include checkbox, we don't
    write a country code for it — that day's files won't be copied so the
    metadata would be misleading."""
    gw, eid, legacy = _make_gateway_with_event(tmp_path)
    items = [
        _phone_item("/r1.jpg", datetime(2026, 5, 27, 10), lat=41.9, lon=12.5),
        _phone_item("/sp.jpg", datetime(2026, 5, 28, 14),
                    lat=-23.5, lon=-46.6, tz_minutes=-180),
    ]
    dlg = PreingestPlanConfirmDialog(legacy, items, gateway=gw, event_id=eid)
    try:
        # Untick day 2
        per_day = {c._trip_day.day_number: c for c in dlg._day_cards}
        per_day[2].set_included(False)
        dlg._on_apply()
    finally:
        dlg.deleteLater()

    eg = gw.open_event(eid)
    try:
        days = {d.day_number: d for d in eg.trip_days()}
        d1_extras = json.loads(days[1].extras_json or "{}")
        d2_extras = json.loads(days[2].extras_json or "{}")
        assert d1_extras.get("country_code") == "IT"      # written
        assert "country_code" not in d2_extras            # skipped
    finally:
        eg.close()
