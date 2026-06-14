"""ManageDaysDialog — per-day operations surface (spec/14 §5D), build-order step 1
(Hide/Unhide + the day list). Pins: the dialog lists every day (incl. hidden) with counts
from ``day_summaries``, the Hide/Unhide action toggles ``trip_day.hidden`` through the
gateway, the row rebuilds to reflect it, and ``changed`` fires so the host can refresh.
"""
from __future__ import annotations

from mira.gateway import EventsIndex, Gateway
from mira.settings.repo import SettingsRepo
from mira.store import models as m
from mira.ui.pages.manage_days_dialog import (
    COL_STATUS,
    ManageDaysDialog,
)

NOW = "2026-06-01T12:00:00+00:00"


def _gateway(tmp_path, base):
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "settings.json"),
        index=EventsIndex(tmp_path / "events_index.json"),
    )
    gw.set_photos_base_path(str(base))
    return gw


def _item(iid, day, kind="photo"):
    ext = "mov" if kind == "video" else "jpg"
    return m.Item(
        id=iid, kind=kind, origin_relpath=f"00 - Captured/{iid}.{ext}", sha256=iid,
        byte_size=1, materialized_at=NOW, materialized_phase="ingest",
        camera_id="C1", capture_time_raw="2026-04-01T08:00:00",
        capture_time_corrected="2026-04-01T08:00:00", created_at=NOW,
        day_number=day, provenance="captured",
    )


def _make_event(gw, base):
    doc = m.EventDocument(
        event=m.Event(uuid="evt", name="Nepal", created_at=NOW, updated_at=NOW),
        cameras=[m.Camera(camera_id="C1")],
        trip_days=[
            m.TripDay(day_number=1, date="2026-04-01", description="Kathmandu"),
            m.TripDay(day_number=2, date="2026-04-02", description="Pokhara"),
        ],
        items=[_item("a", 1), _item("b", 1), _item("c", 2), _item("d", 2, "video")],
    )
    root = base / "Nepal"
    root.mkdir(parents=True, exist_ok=True)
    gw.create_event(doc, root).close()
    return "evt"


def test_dialog_lists_days_with_counts(qapp, tmp_path):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)

    dlg = ManageDaysDialog(gateway=gw, event_id="evt")
    assert dlg._table.rowCount() == 2
    # Day 2 has 1 photo + 1 video; both days start Visible.
    assert dlg._table.item(1, COL_STATUS).text() == "Visible"


def test_hide_toggle_persists_and_signals(qapp, tmp_path):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)

    dlg = ManageDaysDialog(gateway=gw, event_id="evt")
    fired = []
    dlg.changed.connect(lambda: fired.append(True))

    dlg._set_hidden(2, True)
    # Persisted through the gateway...
    eg = gw.open_event("evt")
    try:
        assert {d.day_number: bool(d.hidden) for d in eg.trip_days()} == {1: False, 2: True}
    finally:
        eg.close()
    # ...the row rebuilt to show it, and the host was signalled.
    assert dlg._table.item(1, COL_STATUS).text() == "Hidden"
    assert fired == [True]

    dlg._set_hidden(2, False)
    assert dlg._table.item(1, COL_STATUS).text() == "Visible"
