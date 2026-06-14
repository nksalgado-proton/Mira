"""Tests for the Slice B gateway primitive — ``EventGateway.recompute_corrected_times`` +
``save_camera`` (spec/14 §5B). Pure-logic (no Qt): the virtual-EXIF replacement for the
legacy EXIF re-bake. Pins: raw never mutated, corrected re-derived, day reassigned, downstream
marks flagged dirty, quarantined items skipped, the per-day filter, and camera persistence.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from mira.gateway import EventsIndex, Gateway
from mira.settings.repo import SettingsRepo
from mira.store import models as m


def _gateway(tmp_path, base):
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "settings.json"),
        index=EventsIndex(tmp_path / "events_index.json"),
    )
    gw.set_photos_base_path(str(base))
    return gw


def _item(item_id, camera_id, raw, day, **kw):
    return m.Item(
        id=item_id, kind="photo", origin_relpath=f"{item_id}.rw2", sha256=item_id,
        byte_size=1, materialized_at="2026-03-10T00:00:00", materialized_phase="ingest",
        camera_id=camera_id, capture_time_raw=raw,
        capture_time_corrected=raw, created_at="2026-03-10T00:00:00",
        day_number=day, **kw,
    )


def _make_event(gw, base):
    """Nepal-shaped: trip TZ +5:45 (345 min), 2 days; a G9 camera left on UTC−3 and a phone."""
    stamp = "2026-03-10T00:00:00"
    doc = m.EventDocument(
        event=m.Event(uuid="evt-1", name="Nepal", created_at=stamp, updated_at=stamp,
                      start_date="2026-03-10", end_date="2026-03-11"),
        trip_days=[
            m.TripDay(day_number=1, date="2026-03-10", tz_minutes=345),
            m.TripDay(day_number=2, date="2026-03-11", tz_minutes=345),
        ],
        cameras=[m.Camera(camera_id="G9"), m.Camera(camera_id="phone")],
        items=[
            # G9 raw 23:30 on 03-09 → +8h45 = 08:15 on 03-10 (Day 1)
            _item("a", "G9", "2026-03-09T23:30:00", None),
            # G9 raw 20:00 on 03-10 → +8h45 = 04:45 on 03-11 (Day 2)
            _item("b", "G9", "2026-03-10T20:00:00", 1),
            # phone — must be untouched
            _item("p", "phone", "2026-03-10T12:00:00", 1),
            # quarantined G9 (no raw) — skipped
            _item("q", "G9", "", None),
        ],
    )
    root = base / "Nepal"
    root.mkdir(parents=True, exist_ok=True)
    eg = gw.create_event(doc, root)
    # Create the cull decisions the way the app does — via the gateway (so the rows certainly
    # exist for the dirty-cascade assertion); set_phase_state clears derived_dirty, recompute
    # then re-flags it.
    eg.set_phase_state("a", "pick", "picked")
    eg.set_phase_state("b", "pick", "picked")
    eg.close()
    return "evt-1"


def test_recompute_corrected_and_day(tmp_path):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)

    eg = gw.open_event("evt-1")
    try:
        affected = eg.recompute_corrected_times("G9", applied_offset_minutes=525)  # +8h45
        assert set(affected) == {"a", "b"}

        a = eg.item("a")
        assert a.capture_time_raw == "2026-03-09T23:30:00", "raw NEVER mutated"
        assert a.capture_time_corrected == "2026-03-10T08:15:00"
        assert a.day_number == 1, "moved into Day 1 by the corrected date"
        assert a.tz_offset_minutes == 525 and a.tz_source == "user_declared"

        b = eg.item("b")
        assert b.capture_time_corrected == "2026-03-11T04:45:00"
        assert b.day_number == 2

        # Other camera + quarantined item untouched.
        assert eg.item("p").capture_time_corrected == "2026-03-10T12:00:00"
        assert eg.item("p").tz_source == "none"
        assert eg.item("q").capture_time_corrected == ""

        # Downstream marks flagged dirty (re-entry will recompute).
        assert eg.phase_state("a", "pick").derived_dirty is True
        assert eg.phase_state("b", "pick").derived_dirty is True
    finally:
        eg.close()


def test_recompute_day_filter(tmp_path):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    eg = gw.open_event("evt-1")
    try:
        # Only G9 items on Day 1: "b" (day_number=1). "a" (day None) + "q" (no raw) excluded.
        affected = eg.recompute_corrected_times(
            "G9", applied_offset_minutes=60, day_number=1)
        assert affected == ["b"], affected
        assert eg.item("b").tz_source == "user_declared"
        assert eg.item("a").tz_source == "none"  # excluded by the day filter
    finally:
        eg.close()


def test_save_camera_replaces_or_inserts(tmp_path):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    eg = gw.open_event("evt-1")
    try:
        # Update an existing camera's applied offset.
        g9 = next(c for c in eg.cameras() if c.camera_id == "G9")
        g9.applied_offset_minutes = 525
        g9.configured_tz_minutes = -180
        eg.save_camera(g9)
        again = next(c for c in eg.cameras() if c.camera_id == "G9")
        assert again.applied_offset_minutes == 525
        assert again.configured_tz_minutes == -180

        # Insert a brand-new camera.
        eg.save_camera(m.Camera(camera_id="GoPro", applied_offset_minutes=0))
        assert any(c.camera_id == "GoPro" for c in eg.cameras())
    finally:
        eg.close()


# --------------------------------------------------------------------------- #
# spec/57 §4.2 — retime_day (the plan editor's single-day TZ unlock)
# --------------------------------------------------------------------------- #


def test_retime_day_shifts_and_moves_across_days(tmp_path):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    eg = gw.open_event("evt-1")
    try:
        # Anchor the fixture: give day-1 items concrete corrected times.
        eg.store.conn.execute(
            "UPDATE item SET capture_time_corrected = '2026-03-10T23:50:00', "
            "tz_offset_minutes = 0 WHERE id = 'b'")
        eg.store.conn.execute(
            "UPDATE item SET capture_time_corrected = '2026-03-10T12:00:00', "
            "tz_offset_minutes = 0 WHERE id = 'p'")
        # Day 1 declared +5:45; the user fixes it to +6:45 (delta +60).
        out = eg.retime_day(1, 345 + 60)
        # Both day-1 items re-time ('b' raw 20:00 + 60min = 21:00, stays
        # day 1... and 'p' raw 12:00 + 60 = 13:00). Affected = items ON
        # day 1 with a raw time.
        assert out["affected"] == 2
        b = eg.item("b")
        assert b.capture_time_corrected == "2026-03-10T21:00:00"
        assert b.tz_offset_minutes == 60 and b.tz_source == "user_declared"
        day = eg.store.get(m.TripDay, 1)
        assert day.tz_minutes == 405
        # A second fix that pushes 'b' past midnight moves it to day 2.
        out2 = eg.retime_day(1, 405 + 300)        # +5h more → b lands 03-11
        assert out2["moved"] >= 1
        assert eg.item("b").day_number == 2
        # Downstream marks went dirty.
        assert eg.phase_state("b", "pick").derived_dirty is True
    finally:
        eg.close()


def test_retime_day_unknown_day_raises(tmp_path):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    eg = gw.open_event("evt-1")
    try:
        with pytest.raises(ValueError):
            eg.retime_day(99, 0)
    finally:
        eg.close()
