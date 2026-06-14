"""Plan-editor reassembly (build-order #4 pulled forward, charter §5.2).

Two layers:
- the gateway mutator ``EventGateway.save_trip_days`` (replace-all, FK-safe);
- the shell wiring: New Event lands straight in the editable plan table, and the Plan tile
  on the per-event dashboard opens the same reused ``PlanEditorDialog``, persisting through
  the gateway. The dialog is stubbed (a real modal would block headless).
"""
from __future__ import annotations

from datetime import date

import pytest

from core.models import TripDay as LegacyTripDay
from mira.gateway import EventsIndex, Gateway
from mira.settings.repo import SettingsRepo
from mira.store import models as m

NOW = "2026-06-01T00:00:00+00:00"


def _gateway(tmp_path, base):
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "settings.json"),
        index=EventsIndex(tmp_path / "events_index.json"),
    )
    gw.set_photos_base_path(str(base))
    return gw


def _doc(*, items=None, days=None):
    return m.EventDocument(
        event=m.Event(uuid="e1", name="Nepal", created_at=NOW, updated_at=NOW,
                      start_date="2026-03-10"),
        trip_days=days if days is not None else [
            m.TripDay(day_number=1, date="2026-03-10", description="Kathmandu", tz_minutes=345),
            m.TripDay(day_number=2, date="2026-03-11", description="Lukla", tz_minutes=345),
        ],
        cameras=[m.Camera(camera_id="C1")],
        items=items or [],
    )


def _item(iid, day):
    return m.Item(
        id=iid, kind="photo", origin_relpath=f"00 - Captured/{iid}.jpg", sha256=iid,
        byte_size=1, materialized_at=NOW, materialized_phase="ingest",
        camera_id="C1", capture_time_raw="2026-03-10T08:00:00",
        capture_time_corrected="2026-03-10T08:00:00", created_at=NOW, day_number=day,
    )


# ── gateway mutator ──────────────────────────────────────────────────────────


def test_save_trip_days_replaces_the_whole_set(tmp_path):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    gw.create_event(_doc(), base / "Nepal").close()

    eg = gw.open_event("e1")
    try:
        eg.save_trip_days([
            m.TripDay(day_number=1, date="2026-03-10", description="Kathmandu rev",
                      tz_minutes=345, location="KTM"),
            m.TripDay(day_number=2, date="2026-03-11", description="Lukla", tz_minutes=345),
            m.TripDay(day_number=3, date="2026-03-12", description="Namche", tz_minutes=345),
        ])
        days = {d.day_number: d for d in eg.trip_days()}
        assert len(days) == 3
        assert days[1].description == "Kathmandu rev" and days[1].location == "KTM"
        assert days[3].description == "Namche"
    finally:
        eg.close()


def test_save_trip_days_can_shrink_a_plan_only_event(tmp_path):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    gw.create_event(_doc(), base / "Nepal").close()  # no items
    eg = gw.open_event("e1")
    try:
        eg.save_trip_days([
            m.TripDay(day_number=1, date="2026-03-10", description="Kathmandu", tz_minutes=345),
        ])
        assert [d.day_number for d in eg.trip_days()] == [1]
    finally:
        eg.close()


def test_save_trip_days_rejects_removing_a_day_with_items(tmp_path):
    import sqlite3

    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    # day 2 has an item → removing day 2 must be rejected (FK orphan).
    gw.create_event(_doc(items=[_item("a", 1), _item("b", 2)]), base / "Nepal").close()
    eg = gw.open_event("e1")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            eg.save_trip_days([
                m.TripDay(day_number=1, date="2026-03-10", description="Kathmandu",
                          tz_minutes=345),
            ])
        # The rollback left the original two days intact.
        assert sorted(d.day_number for d in eg.trip_days()) == [1, 2]
    finally:
        eg.close()


# ── shell wiring (stubbed dialog) ─────────────────────────────────────────────


class _StubDialog:
    """Stands in for PlanEditorDialog — records the seed, returns a chosen plan."""

    last_seed: list = []
    chosen: list = []
    applied = True

    def __init__(self, parent=None, trip_days=None, event=None, day_photos_provider=None,
                 day_photo_counts=None):
        type(self).last_seed = list(trip_days or [])

    def exec(self):
        return 0

    def was_applied(self):
        return type(self).applied

    def get_trip_days(self):
        return list(type(self).chosen)


def _main_window(gw, monkeypatch):
    monkeypatch.setattr(
        "mira.ui.base.plan_editor_dialog.PlanEditorDialog", _StubDialog
    )
    from mira.ui.shell.main_window import MainWindow
    return MainWindow(gateway=gw)


def test_new_event_create_lands_in_the_plan_editor_and_persists(qapp, tmp_path, monkeypatch):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    # The stub will return a 2-day plan when the editor "applies".
    _StubDialog.applied = True
    _StubDialog.chosen = [
        LegacyTripDay(day_number=1, date=date(2026, 7, 1), description="Arrival",
                      tz_offset=-3.0, location="Cuiabá"),
        LegacyTripDay(day_number=2, date=date(2026, 7, 2), description="Pantanal",
                      tz_offset=-4.0),
    ]
    w = _main_window(gw, monkeypatch)
    w.new_event_page.set_form_values("2026 - Pantanal", date(2026, 7, 1))
    w.new_event_page._on_create()  # → _on_new_event_created → open event + plan editor

    # spec/46 Slice 2+3: new-event creation now lands directly on the activity
    # dashboard (the transitional EventPlanPage retired).
    assert w.page_stack.current_key == w._ACTIVITY_PAGE_KEY
    eg = gw.open_event(w._current_event_id)
    try:
        days = {d.day_number: d for d in eg.trip_days()}
        assert len(days) == 2
        assert days[1].description == "Arrival" and days[1].tz_minutes == -180
        assert days[2].tz_minutes == -240
    finally:
        eg.close()


def test_plan_tile_opens_editor_with_existing_days_seeded(qapp, tmp_path, monkeypatch):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    gw.create_event(_doc(), base / "Nepal").close()
    w = _main_window(gw, monkeypatch)
    assert w._open_event("e1")

    _StubDialog.applied = False  # user cancels — nothing persists, just check the seed
    _StubDialog.last_seed = []
    w._on_phase_activated("plan")

    # The dialog was seeded with the event's two days, converted to the legacy shape
    # (tz_minutes 345 → tz_offset 5.75).
    seed = {d.day_number: d for d in _StubDialog.last_seed}
    assert len(seed) == 2
    assert seed[1].description == "Kathmandu" and abs(seed[1].tz_offset - 5.75) < 1e-9
