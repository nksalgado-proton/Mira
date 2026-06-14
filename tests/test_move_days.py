"""Gateway.move_days — the subdivide-a-trip primitive (spec/14 §5C.3/§5D).

Pins the no-data-loss contract: files are copied + verified into the target and only then
removed from the source; the moved items' records + cull decisions travel; the target day is
created (or merged into a same-date day); a day with video clips / downstream work is blocked.
"""
from __future__ import annotations

import hashlib

import pytest

from mira.gateway import EventsIndex, Gateway
from mira.settings.repo import SettingsRepo
from mira.store import models as m

NOW = "2026-06-01T12:00:00+00:00"
CONTENT = b"photo-bytes"
SHA = hashlib.sha256(CONTENT).hexdigest()


def _gateway(tmp_path, base):
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "settings.json"),
        index=EventsIndex(tmp_path / "events_index.json"),
    )
    gw.set_photos_base_path(str(base))
    return gw


def _item(iid, day, *, kind="photo"):
    ext = "mov" if kind == "video" else "jpg"
    return m.Item(
        id=iid, kind=kind, origin_relpath=f"00 - Captured/{iid}.{ext}", sha256=SHA,
        byte_size=len(CONTENT), materialized_at=NOW, materialized_phase="ingest",
        camera_id="C1", capture_time_raw="2026-04-01T08:00:00",
        capture_time_corrected="2026-04-01T08:00:00", created_at=NOW,
        day_number=day, provenance="captured",
    )


def _make_event(gw, base, *, uuid, name, days, items, phase_states=None):
    doc = m.EventDocument(
        event=m.Event(uuid=uuid, name=name, created_at=NOW, updated_at=NOW),
        cameras=[m.Camera(camera_id="C1")],
        trip_days=days,
        items=items,
        phase_states=phase_states or [],
    )
    eg = gw.create_event(doc, base / name)
    root = eg.event_root
    eg.close()
    for it in items:
        if it.origin_relpath:
            p = root / it.origin_relpath
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(CONTENT)
    return uuid, root


def test_move_day_copies_then_removes_and_carries_decisions(tmp_path):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    src_id, src_root = _make_event(
        gw, base, uuid="src", name="Source",
        days=[
            m.TripDay(day_number=1, date="2026-04-01", description="A"),
            m.TripDay(day_number=2, date="2026-04-02", description="B"),
        ],
        items=[_item("s1", 1), _item("s2", 2)],
        phase_states=[m.PhaseState(item_id="s2", phase="pick", state="picked")],
    )
    tgt_id, tgt_root = _make_event(
        gw, base, uuid="tgt", name="Target",
        days=[m.TripDay(day_number=1, date="2026-03-01", description="X")],
        items=[],
    )

    res = gw.move_days(src_id, [2], tgt_id)
    assert res == {"moved_days": 1, "moved_items": 1}

    # Target got the file + record + the carried 'picked' decision, on a new day.
    assert (tgt_root / "00 - Captured/s2.jpg").exists()
    tgt = gw.open_event(tgt_id)
    try:
        assert {i.id for i in tgt.items()} == {"s2"}
        assert tgt.phase_state("s2", "pick").state == "picked"
        moved_day = next(i for i in tgt.items()).day_number
        assert {d.date for d in tgt.trip_days() if d.day_number == moved_day} == {"2026-04-02"}
    finally:
        tgt.close()

    # Source no longer has day 2 — file gone, record gone, trip_day gone. Day 1 intact.
    assert not (src_root / "00 - Captured/s2.jpg").exists()
    assert (src_root / "00 - Captured/s1.jpg").exists()
    src = gw.open_event(src_id)
    try:
        assert {i.id for i in src.items()} == {"s1"}
        assert [d.day_number for d in src.trip_days()] == [1]
    finally:
        src.close()


def test_move_day_merges_into_same_date_target_day(tmp_path):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    src_id, _ = _make_event(
        gw, base, uuid="src", name="Source",
        days=[m.TripDay(day_number=1, date="2026-04-02", description="B")],
        items=[_item("s1", 1)],
    )
    tgt_id, _ = _make_event(
        gw, base, uuid="tgt", name="Target",
        days=[m.TripDay(day_number=1, date="2026-04-02", description="existing")],
        items=[],
    )
    gw.move_days(src_id, [1], tgt_id)
    tgt = gw.open_event(tgt_id)
    try:
        # Merged into the existing same-date day 1 (no second day created).
        assert [d.day_number for d in tgt.trip_days()] == [1]
        assert next(i for i in tgt.items()).day_number == 1
    finally:
        tgt.close()


def test_move_blocked_when_day_has_clips(tmp_path):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    # A captured video on day 1 with a virtual clip child.
    video = _item("vid", 1, kind="video")
    clip = m.Item(
        id="clip", kind="video", provenance="clip", parent_item_id="vid",
        camera_id="C1", capture_time_raw="2026-04-01T08:00:00",
        capture_time_corrected="2026-04-01T08:00:00", created_at=NOW, day_number=1,
    )
    src_id, src_root = _make_event(
        gw, base, uuid="src", name="Source",
        days=[m.TripDay(day_number=1, date="2026-04-01")],
        items=[video, clip],
    )
    # segment satellite (1:1 order-identity row for the segment item, spec/56).
    eg = gw.open_event(src_id)
    try:
        eg.store.upsert(m.VideoSegment(
            item_id="clip", video_item_id="vid", seg_index=0, created_at=NOW))
    finally:
        eg.close()
    tgt_id, _ = _make_event(
        gw, base, uuid="tgt", name="Target",
        days=[m.TripDay(day_number=1, date="2026-03-01")], items=[])

    with pytest.raises(ValueError):
        gw.move_days(src_id, [1], tgt_id)
    # Nothing moved — source day + file intact.
    assert (src_root / "00 - Captured/vid.mov").exists()
    src = gw.open_event(src_id)
    try:
        assert [d.day_number for d in src.trip_days()] == [1]
    finally:
        src.close()
