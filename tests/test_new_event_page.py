"""Tests for the reused New Event page (charter §5.2 data rewire, build-order #2).

The form/plan-editor *UI* is the legacy widget; what's new is the data seam — Create builds
an ``EventDocument`` (Event + trip_days, no items) and materialises via
``Gateway.create_event`` instead of ``data.event_store.save_event`` +
``create_folder_structure``. These pin the create commit, the legacy→store TripDay mapping,
the name-collision guard, and the no-base guard.
"""
from __future__ import annotations

from datetime import date

import pytest

from core.models import TripDay as LegacyTripDay
from mira.gateway import EventsIndex, Gateway
from mira.settings.repo import SettingsRepo
from mira.ui.pages.new_event_page import NewEventPage


def _gateway(tmp_path, base):
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "settings.json"),
        index=EventsIndex(tmp_path / "events_index.json"),
    )
    gw.set_photos_base_path(str(base))
    return gw


def test_create_materialises_a_plan_only_event(qapp, tmp_path):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    page = NewEventPage(gw)
    page.set_form_values("2026 - Pantanal", date(2026, 7, 1))

    created: list[str] = []
    page.event_created.connect(created.append)
    page._on_create()

    # The event was materialised through the gateway + registered in the index.
    assert len(created) == 1
    events = gw.list_events()
    assert len(events) == 1
    row = events[0]
    assert row["name"] == "2026 - Pantanal"
    assert row["start_date"] == "2026-07-01"

    # event.db lives under <base>/<name> and round-trips through the gateway.
    event_root = base / "2026 - Pantanal"
    assert (event_root / "event.db").exists()
    eg = gw.open_event(created[0])
    try:
        assert eg.event().name == "2026 - Pantanal"
        assert eg.items() == []  # plan-only — no items
    finally:
        eg.close()

    # Form reset after a successful create.
    assert page._name_edit.text() == ""
    assert page._pending_trip_days is None
    # event_type left blank → stored as the spec/44 enum default 'unclassified'
    # (extras_json carries no legacy_type_label since the user typed nothing).
    eg = gw.open_event(created[0])
    try:
        import json as _json
        ev = eg.event()
        assert ev.event_type == "unclassified"
        assert "legacy_type_label" not in _json.loads(ev.extras_json)
    finally:
        eg.close()


def test_create_persists_classification_panel_values(qapp, tmp_path):
    """Slice B: the wizard's ClassificationPanel persists Type/Subtype/Description/
    Extras through the create commit. spec/52 retired event-level tags + the
    v4→v5 free-text migration path."""
    import json as _json
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    page = NewEventPage(gw)
    page.set_form_values(
        "Costa Rica 2026", date(2026, 4, 1),
        event_type="trip", event_subtype="Two weeks",
        description="Birds and rainforest.",
        extras={"countries": ["CR"], "people": ["Nelson", "Maria"]},
    )

    created: list[str] = []
    page.event_created.connect(created.append)
    page._on_create()

    eg = gw.open_event(created[0])
    try:
        ev = eg.event()
        assert ev.event_type == "trip"
        assert ev.event_subtype == "Two weeks"
        assert ev.description == "Birds and rainforest."
        extras = _json.loads(ev.extras_json)
        assert extras.get("countries") == ["CR"]
        assert extras.get("people") == ["Nelson", "Maria"]
    finally:
        eg.close()


def test_create_accepts_canonical_enum_event_type(qapp, tmp_path):
    """Canonical enum value via the panel passes through unchanged (no
    legacy_type_label written)."""
    import json as _json
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    page = NewEventPage(gw)
    page.set_form_values("Birding morning", date(2026, 7, 2), event_type="session")

    created: list[str] = []
    page.event_created.connect(created.append)
    page._on_create()

    eg = gw.open_event(created[0])
    try:
        ev = eg.event()
        assert ev.event_type == "session"
        assert "legacy_type_label" not in _json.loads(ev.extras_json)
    finally:
        eg.close()


def test_create_carries_the_plan_and_maps_tz_to_minutes(qapp, tmp_path):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    page = NewEventPage(gw)
    page.set_form_values("Nepal", date(2026, 3, 10))
    page._pending_trip_days = [
        LegacyTripDay(day_number=1, date=date(2026, 3, 10), description="Kathmandu",
                      tz_offset=5.75, location="Kathmandu"),
        LegacyTripDay(day_number=2, date=date(2026, 3, 11), description="Lukla",
                      tz_offset=5.75, location=None),
    ]

    created: list[str] = []
    page.event_created.connect(created.append)
    page._on_create()

    eg = gw.open_event(created[0])
    try:
        days = {d.day_number: d for d in eg.trip_days()}
        assert days[1].tz_minutes == 345 and days[1].location == "Kathmandu"
        assert days[2].tz_minutes == 345 and days[2].description == "Lukla"
    finally:
        eg.close()


def test_create_without_name_is_rejected(qapp, tmp_path):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    page = NewEventPage(gw)
    page.set_form_values("   ", date(2026, 7, 1))
    page._on_create()
    assert gw.list_events() == []


def test_create_without_base_path_is_rejected(qapp, tmp_path):
    # No photos_base_path set → cannot resolve an event root, so no event is created.
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "settings.json"),
        index=EventsIndex(tmp_path / "events_index.json"),
    )
    page = NewEventPage(gw)
    page.set_form_values("Homeless", date(2026, 7, 1))
    created: list[str] = []
    page.event_created.connect(created.append)
    page._on_create()
    assert created == []
    assert gw.list_events() == []


def test_name_collision_blocks_when_declined(qapp, tmp_path, monkeypatch):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    page = NewEventPage(gw)
    page.set_form_values("Twin", date(2026, 7, 1))
    page._on_create()  # first one succeeds (no collision)
    assert len(gw.list_events()) == 1

    # Second with the same name — decline the collision dialog → no second event.
    monkeypatch.setattr(
        "mira.ui.base.name_collision.confirm_name_collision",
        lambda *a, **k: False,
    )
    page.set_form_values("Twin", date(2026, 8, 1))
    page._on_create()
    assert len(gw.list_events()) == 1
