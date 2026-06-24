"""spec/127 §1.2 + acceptance — applying corrections per trip-TZ
segment.

A 2-segment trip (Nepal +5:45 with a day at India +5:30) gets its
corrections applied **per segment**: ``recompute_corrected_times`` is
scoped via ``day_numbers`` so the corrected times of one segment's
items never get rewritten by the other segment's apply. A camera with
items in both segments gets its right offset in EACH.
"""
from __future__ import annotations

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


def _photo(item_id, camera_id, raw, day=None):
    return m.Item(
        id=item_id, kind="photo", origin_relpath=f"{item_id}.rw2",
        sha256=item_id, byte_size=1,
        materialized_at="2026-03-10T00:00:00",
        materialized_phase="ingest",
        camera_id=camera_id, capture_time_raw=raw,
        capture_time_corrected=raw,
        created_at="2026-03-10T00:00:00",
        day_number=day,
    )


def _make_two_segment_event(gw, base):
    """Nepal (+5:45) Days 1–2, India (+5:30) Day 3. A GoPro spans BOTH
    (left on UTC−3, so base offset differs per segment); a phone is on
    the trip's local time."""
    stamp = "2026-03-10T00:00:00"
    doc = m.EventDocument(
        event=m.Event(uuid="evt-2seg", name="Nepal+India",
                      created_at=stamp, updated_at=stamp,
                      start_date="2026-03-10", end_date="2026-03-12"),
        trip_days=[
            m.TripDay(day_number=1, date="2026-03-10", tz_minutes=345),
            m.TripDay(day_number=2, date="2026-03-11", tz_minutes=345),
            m.TripDay(day_number=3, date="2026-03-12", tz_minutes=330),
        ],
        cameras=[
            m.Camera(camera_id="GoPro"),
            m.Camera(camera_id="phone", is_phone=True),
        ],
        items=[
            _photo("g1", "GoPro", "2026-03-10T08:00:00", day=1),
            _photo("g2", "GoPro", "2026-03-11T08:00:00", day=2),
            _photo("g3", "GoPro", "2026-03-12T08:00:00", day=3),
            _photo("ph1", "phone", "2026-03-10T12:00:00", day=1),
            _photo("ph2", "phone", "2026-03-12T12:00:00", day=3),
        ],
    )
    root = base / "Nepal-India"
    root.mkdir(parents=True, exist_ok=True)
    eg = gw.create_event(doc, root)
    eg.close()
    return "evt-2seg"


def test_two_segment_apply_scopes_to_each_segments_days(tmp_path):
    """Apply the Nepal-segment correction first (Days 1+2 only); g3 on
    Day 3 must remain at its raw time. Then apply the India-segment
    correction (Day 3 only); g1/g2 must be unchanged from the first
    apply."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_two_segment_event(gw, base)

    eg = gw.open_event("evt-2seg")
    try:
        # Segment A — Nepal +5:45, GoPro on −3 → applied = +8:45 = 31_500
        affected_a = eg.recompute_corrected_times(
            "GoPro", offset_seconds=31_500, day_numbers=[1, 2])
        assert set(affected_a) == {"g1", "g2"}    # g3 not touched
        g3 = eg.item("g3")
        # g3 still untouched — corrected == raw, tz_offset still 0.
        assert g3.capture_time_corrected == "2026-03-12T08:00:00"
        assert int(g3.tz_offset_seconds) == 0

        # Segment B — India +5:30, GoPro on −3 → applied = +8:30 = 30_600
        affected_b = eg.recompute_corrected_times(
            "GoPro", offset_seconds=30_600, day_numbers=[3])
        assert affected_b == ["g3"]

        # After both: each segment's items carry their own offset.
        g1 = eg.item("g1")
        g2 = eg.item("g2")
        g3 = eg.item("g3")
        assert int(g1.tz_offset_seconds) == 31_500
        assert int(g2.tz_offset_seconds) == 31_500
        assert int(g3.tz_offset_seconds) == 30_600
        # And the Day 1/2 g items KEEP their first-apply corrected
        # times — the second apply (Day 3 scope) didn't touch them.
        assert g1.capture_time_corrected == "2026-03-10T16:45:00"
        assert g2.capture_time_corrected == "2026-03-11T16:45:00"
        assert g3.capture_time_corrected == "2026-03-12T16:30:00"
    finally:
        eg.close()


def test_camera_in_both_segments_gets_both_offsets(tmp_path):
    """Acceptance — a camera with photos in both segments gets the
    correct offset in EACH (today's bug: the second segment was
    silently dropped). Verified via item.tz_offset_seconds."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_two_segment_event(gw, base)

    eg = gw.open_event("evt-2seg")
    try:
        eg.recompute_corrected_times(
            "GoPro", offset_seconds=31_500, day_numbers=[1, 2])
        eg.recompute_corrected_times(
            "GoPro", offset_seconds=30_600, day_numbers=[3])

        per_day = {it.day_number: int(it.tz_offset_seconds)
                   for it in eg.items(camera_id="GoPro")}
        assert per_day == {1: 31_500, 2: 31_500, 3: 30_600}
    finally:
        eg.close()


def test_raw_capture_time_never_mutated(tmp_path):
    """spec/123 invariant — raw stays pristine through every apply,
    even multi-segment."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_two_segment_event(gw, base)

    eg = gw.open_event("evt-2seg")
    try:
        before = {it.id: it.capture_time_raw for it in eg.items()}
        eg.recompute_corrected_times(
            "GoPro", offset_seconds=31_500, day_numbers=[1, 2])
        eg.recompute_corrected_times(
            "GoPro", offset_seconds=30_600, day_numbers=[3])
        after = {it.id: it.capture_time_raw for it in eg.items()}
        assert before == after
    finally:
        eg.close()


def test_phone_items_in_segment_untouched_by_camera_recompute(tmp_path):
    """``recompute_corrected_times`` is camera-scoped — applying a GoPro
    correction must not touch phone items even when they share a
    segment's days (regress the camera-id filter)."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_two_segment_event(gw, base)

    eg = gw.open_event("evt-2seg")
    try:
        before = {it.id: (it.capture_time_corrected,
                          int(it.tz_offset_seconds))
                  for it in eg.items() if it.camera_id == "phone"}
        eg.recompute_corrected_times(
            "GoPro", offset_seconds=31_500, day_numbers=[1, 2])
        eg.recompute_corrected_times(
            "GoPro", offset_seconds=30_600, day_numbers=[3])
        after = {it.id: (it.capture_time_corrected,
                         int(it.tz_offset_seconds))
                 for it in eg.items() if it.camera_id == "phone"}
        assert before == after
    finally:
        eg.close()


def test_empty_day_numbers_is_a_noop(tmp_path):
    """Passing ``day_numbers=[]`` is the "scope to nothing" branch —
    returns empty + no rows touched (so the dialog can iterate
    segments fearlessly without special-casing the empty case)."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_two_segment_event(gw, base)

    eg = gw.open_event("evt-2seg")
    try:
        before = {it.id: it.capture_time_corrected for it in eg.items()}
        affected = eg.recompute_corrected_times(
            "GoPro", offset_seconds=31_500, day_numbers=[])
        assert affected == []
        after = {it.id: it.capture_time_corrected for it in eg.items()}
        assert before == after
    finally:
        eg.close()


def test_day_number_and_day_numbers_mutually_exclusive(tmp_path):
    """Passing both ``day_number`` and ``day_numbers`` raises
    ValueError — the dialog must pick one shape, never blend."""
    import pytest
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_two_segment_event(gw, base)

    eg = gw.open_event("evt-2seg")
    try:
        with pytest.raises(ValueError):
            eg.recompute_corrected_times(
                "GoPro", offset_seconds=31_500,
                day_number=1, day_numbers=[1, 2])
    finally:
        eg.close()
