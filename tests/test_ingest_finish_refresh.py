"""spec/126 §B — refresh current-event surfaces on ingest finish.

Once ``_mark_ingest_finished`` clears the in-progress flag, the
phases page must re-read the just-committed event.db so a follow-up
``open_event().cameras()`` shows the committed ``applied_offset_seconds``
without a restart. Background ingests on a non-current event must NOT
bounce the visible surface.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mira.gateway import Gateway
from mira.store import models as m
from mira.ui.shell.main_window import MainWindow


@pytest.fixture
def finish_main_window(qapp, tmp_path, monkeypatch):
    from mira.gateway.index import EventsIndex
    from mira.settings.repo import SettingsRepo
    user_data = tmp_path / "user_data"
    user_data.mkdir()
    base = tmp_path / "lib"
    base.mkdir()
    monkeypatch.setattr(
        "mira.paths.user_data_dir", lambda: user_data)
    monkeypatch.setattr(
        "core.settings.user_data_dir", lambda: user_data)
    monkeypatch.setattr(
        "mira.gateway.index.user_data_dir", lambda: user_data)
    monkeypatch.setattr(
        "mira.settings.repo.user_data_dir", lambda: user_data)
    gw = Gateway(
        settings=SettingsRepo(user_data / "settings.json"),
        index=EventsIndex(user_data / "events_index.json"),
        user_store_path=user_data / "mira.db",
    )
    _ = gw.user_store
    gw.settings.update(photos_base_path=str(base))
    w = MainWindow(gateway=gw)
    yield w
    w.deleteLater()


def _make_event(gw, event_id, event_root):
    """An event with one camera whose offset will be UPDATED after
    ingest-start (simulating the late commit the spec describes)."""
    doc = m.EventDocument(
        event=m.Event(
            uuid=event_id, name="Test", created_at="t", updated_at="t",
            start_date="2026-04-01", end_date="2026-04-01"),
        trip_days=[m.TripDay(
            day_number=1, date="2026-04-01",
            tz_minutes=0, extras_json='{}')],
        cameras=[m.Camera(camera_id="C1", applied_offset_seconds=0)],
    )
    event_root.mkdir(parents=True, exist_ok=True)
    eg = gw.create_event(doc, event_root)
    eg.close()


def _events_root(w):
    return Path(w.gateway.settings.load().photos_base_path)


def _commit_offset(gw, event_id, *, seconds):
    """Stand-in for the background engine's late ``create_event`` commit
    of the camera offset — open the event, save the updated camera, close."""
    eg = gw.open_event(event_id)
    try:
        cam = m.Camera(
            camera_id="C1",
            applied_offset_seconds=seconds,
            applied_at=eg._now(),
        )
        eg.save_camera(cam)
    finally:
        eg.close()


# ── §B: current-event finish refreshes phases + events ──────────────────


def test_finish_refreshes_phases_and_events_for_current_event(
    finish_main_window, monkeypatch,
):
    w = finish_main_window
    event_id = "evt-126-refresh"
    _make_event(w.gateway, event_id, _events_root(w) / "refresh-event")
    w._current_event_id = event_id
    w._mark_ingest_started(event_id)

    set_event_calls: list = []
    refresh_calls: list = []
    monkeypatch.setattr(
        w.phases_page, "set_event",
        lambda eid: set_event_calls.append(eid))
    monkeypatch.setattr(
        w.events_page, "refresh",
        lambda: refresh_calls.append(True))

    # The background commit lands while the ingest flag is still set
    # (the case the spec describes: read mid-ingest = stale; commit then
    # arrives; the flag clears).
    _commit_offset(w.gateway, event_id, seconds=18002)   # spec/123 Nepal

    w._mark_ingest_finished(event_id)

    # Both refresh hooks fired exactly once.
    assert set_event_calls == [event_id]
    assert refresh_calls == [True]

    # And — the actual point of the refresh — a follow-up open_event
    # reads the committed offset, without a restart.
    eg = w.gateway.open_event(event_id)
    try:
        cams = {c.camera_id: c for c in eg.cameras()}
    finally:
        eg.close()
    assert cams["C1"].applied_offset_seconds == 18002


def test_finish_does_not_refresh_other_event(
    finish_main_window, monkeypatch,
):
    """Background ingest on a non-current event must NOT bounce the
    currently visible surface — the refresh is guarded by current."""
    w = finish_main_window
    visible_id = "evt-126-visible"
    background_id = "evt-126-bg"
    _make_event(w.gateway, visible_id, _events_root(w) / "visible-event")
    _make_event(w.gateway, background_id, _events_root(w) / "bg-event")
    w._current_event_id = visible_id
    w._mark_ingest_started(background_id)

    set_event_calls: list = []
    refresh_calls: list = []
    monkeypatch.setattr(
        w.phases_page, "set_event",
        lambda eid: set_event_calls.append(eid))
    monkeypatch.setattr(
        w.events_page, "refresh",
        lambda: refresh_calls.append(True))

    w._mark_ingest_finished(background_id)

    # Neither phases.set_event nor events.refresh ran (no bounce).
    assert set_event_calls == []
    assert refresh_calls == []
