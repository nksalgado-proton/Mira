"""Soft-hide of a trip day (spec/14 §5C.1) — the day-level ``trip_day.hidden`` flag with
item visibility **derived** via the ``visible_item`` view.

Pins the contract: a hidden day's items are disregarded by every phase-facing read +
completion metric (``items`` / ``day_tree`` / ``phase_progress`` / ``phase_day_progress``),
``include_hidden=True`` still sees them, ``phase_state`` is left untouched so unhiding
restores prior decisions, and the flag round-trips through the backup document.
"""
from __future__ import annotations

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.store import models as m
from mira.store.repo import EventStore

NOW = "2026-06-01T12:00:00+00:00"


def _item(iid, day):
    return m.Item(
        id=iid, kind="photo", origin_relpath=f"00 - Captured/{iid}.jpg", sha256=iid,
        byte_size=1, materialized_at=NOW, materialized_phase="ingest",
        camera_id="C1", capture_time_raw="2026-04-0%dT08:00:00" % day,
        capture_time_corrected="2026-04-0%dT08:00:00" % day,
        created_at=NOW, day_number=day, provenance="captured",
    )


def _doc():
    return m.EventDocument(
        event=m.Event(uuid="evt", name="Test", created_at=NOW, updated_at=NOW),
        cameras=[m.Camera(camera_id="C1")],
        trip_days=[
            m.TripDay(day_number=1, date="2026-04-01", description="Day one"),
            m.TripDay(day_number=2, date="2026-04-02", description="Day two"),
        ],
        # 2 items per day.
        items=[_item("d1a", 1), _item("d1b", 1), _item("d2a", 2), _item("d2b", 2)],
        # Both day-2 items decided in cull (kept) — these marks must survive a hide/unhide.
        phase_states=[
            m.PhaseState(item_id="d2a", phase="pick", state="picked"),
            m.PhaseState(item_id="d2b", phase="pick", state="picked"),
            m.PhaseState(item_id="d1a", phase="pick", state="skipped"),
        ],
    )


def _gateway(tmp_path):
    db = tmp_path / "event.db"
    store = EventStore.create(db, event_id="evt")
    store.save_document(_doc())
    store.close()
    return EventGateway.open(db, event_root=tmp_path, now=lambda: NOW)


def test_hidden_day_dropped_from_item_reads(tmp_path):
    gw = _gateway(tmp_path)
    assert {i.id for i in gw.items()} == {"d1a", "d1b", "d2a", "d2b"}

    gw.set_day_hidden(2, True)
    # Phase-facing reads disregard the hidden day...
    assert {i.id for i in gw.items()} == {"d1a", "d1b"}
    assert [d["day_number"] for d in gw.day_tree()] == [1]
    # ...but include_hidden still sees everything (TZ recompute / plan viewer / delete).
    assert {i.id for i in gw.items(include_hidden=True)} == {"d1a", "d1b", "d2a", "d2b"}


def test_hidden_day_dropped_from_metrics(tmp_path):
    gw = _gateway(tmp_path)
    before = gw.phase_progress("pick")
    assert before["counts"].get("picked") == 2 and before["counts"].get("skipped") == 1

    gw.set_day_hidden(2, True)
    after = gw.phase_progress("pick")
    # The two day-2 kept marks no longer skew the funnel; day-1 discard remains.
    assert after["counts"].get("picked") is None
    assert after["counts"].get("skipped") == 1

    pdp = gw.phase_day_progress()
    assert 2 not in pdp.get("pick", {})  # hidden day contributes no cell
    assert pdp["pick"][1]["total"] == 2


def test_unhide_restores_items_and_decisions(tmp_path):
    gw = _gateway(tmp_path)
    gw.set_day_hidden(2, True)
    gw.set_day_hidden(2, False)

    assert {i.id for i in gw.items()} == {"d1a", "d1b", "d2a", "d2b"}
    # phase_state was never touched by hide/unhide — the kept marks survive.
    assert gw.phase_state("d2a", "pick").state == "picked"
    assert gw.phase_state("d2b", "pick").state == "picked"
    assert gw.phase_progress("pick")["counts"].get("picked") == 2


def test_undated_items_always_visible(tmp_path):
    """An item with no day_number has no day to hide it — it stays visible even when
    other days are hidden."""
    db = tmp_path / "event.db"
    store = EventStore.create(db, event_id="evt")
    doc = _doc()
    doc.items.append(_item("orphan", 1))
    doc.items[-1].day_number = None
    store.save_document(doc)
    store.close()
    gw = EventGateway.open(db, event_root=tmp_path, now=lambda: NOW)
    gw.set_day_hidden(1, True)
    ids = {i.id for i in gw.items()}
    assert "orphan" in ids
    assert "d1a" not in ids  # day-1 dated items are hidden


def test_hidden_flag_round_trips_through_backup(tmp_path):
    gw = _gateway(tmp_path)
    gw.set_day_hidden(2, True)
    doc = gw.store.load_document()
    days = {d.day_number: d for d in doc.trip_days}
    assert days[2].hidden is True and days[1].hidden is False


def test_day_summaries_lists_every_day_with_counts_and_hidden(tmp_path):
    """day_summaries feeds the Manage-days dialog: ALL days (incl. hidden), with
    captured counts + the hidden flag, ordered by day_number."""
    gw = _gateway(tmp_path)
    gw.set_day_hidden(2, True)
    rows = gw.day_summaries()
    assert [r["day_number"] for r in rows] == [1, 2]
    by_num = {r["day_number"]: r for r in rows}
    assert by_num[1]["photos"] == 2 and by_num[1]["videos"] == 0
    assert by_num[1]["hidden"] is False
    assert by_num[2]["photos"] == 2 and by_num[2]["hidden"] is True
    assert by_num[2]["description"] == "Day two"


# --------------------------------------------------------------------------- #
# delete_day (spec/14 §5C.2) — records + this event's copied files
# --------------------------------------------------------------------------- #


def _gateway_with_files(tmp_path):
    db = tmp_path / "event.db"
    store = EventStore.create(db, event_id="evt")
    store.save_document(_doc())
    store.close()
    cap = tmp_path / "00 - Captured"
    cap.mkdir(parents=True, exist_ok=True)
    for iid in ("d1a", "d1b", "d2a", "d2b"):
        (cap / f"{iid}.jpg").write_bytes(b"x")
    return EventGateway.open(db, event_root=tmp_path, now=lambda: NOW)


def test_delete_day_removes_records_and_files(tmp_path):
    gw = _gateway_with_files(tmp_path)
    res = gw.delete_day(2)
    assert res["files_deleted"] == 2
    cap = tmp_path / "00 - Captured"
    assert not (cap / "d2a.jpg").exists() and not (cap / "d2b.jpg").exists()
    assert (cap / "d1a.jpg").exists()  # day 1 untouched
    # Records: day-2 items + the trip_day row gone; their phase_state cascaded.
    assert {i.id for i in gw.items(include_hidden=True)} == {"d1a", "d1b"}
    assert [d.day_number for d in gw.trip_days()] == [1]
    assert gw.phase_state("d2a", "pick") is None


def test_delete_day_blocked_by_downstream_lineage(tmp_path):
    db = tmp_path / "event.db"
    store = EventStore.create(db, event_id="evt")
    doc = _doc()
    doc.lineage.append(m.Lineage(
        export_relpath="03 - Processed/x.jpg", phase="edit",
        source_kind="item", source_item_id="d2a",
    ))
    store.save_document(doc)
    store.close()
    gw = EventGateway.open(db, event_root=tmp_path, now=lambda: NOW)
    with pytest.raises(ValueError):
        gw.delete_day(2)
    # Nothing removed — the day's items + trip_day survive.
    assert {i.id for i in gw.items(include_hidden=True)} == {"d1a", "d1b", "d2a", "d2b"}
    assert [d.day_number for d in gw.trip_days()] == [1, 2]
