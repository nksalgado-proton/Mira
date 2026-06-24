"""spec/123 — ``EventGateway.recompute_corrected_times(offset_seconds=…)``
pins.

* Applies the offset to EVERY captured item of the camera, including
  videos (the GoPro zero-correction bug must be gone).
* Raw capture time NEVER mutated; corrected = raw + offset_seconds.
* Day reassignment is HONEST: a planned-date hit lands on that day; a
  date with no planned day moves the item to the undated bucket
  (``day_number = None``). NEVER silently keep the stale
  pre-correction day.
* GoPro Nepal-style evening clip raw 23:30 with +8:45 lands the next
  day (corrected 08:15 on the next calendar date).
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


def _video(item_id, camera_id, raw, day=None):
    return m.Item(
        id=item_id, kind="video", origin_relpath=f"{item_id}.mp4",
        sha256=item_id, byte_size=1,
        materialized_at="2026-03-10T00:00:00",
        materialized_phase="ingest",
        camera_id=camera_id, capture_time_raw=raw,
        capture_time_corrected=raw,
        created_at="2026-03-10T00:00:00",
        day_number=day,
        duration_ms=5000,
    )


def _make_event(gw, base):
    """Nepal-shaped: trip TZ +5:45 (345 min), 2 days; a GoPro left on
    UTC−3 (the spec/122 reverted case — now just source 1)."""
    stamp = "2026-03-10T00:00:00"
    doc = m.EventDocument(
        event=m.Event(uuid="evt-1", name="Nepal",
                      created_at=stamp, updated_at=stamp,
                      start_date="2026-03-10",
                      end_date="2026-03-11"),
        trip_days=[
            m.TripDay(day_number=1, date="2026-03-10", tz_minutes=345),
            m.TripDay(day_number=2, date="2026-03-11", tz_minutes=345),
        ],
        cameras=[m.Camera(camera_id="GoPro"),
                 m.Camera(camera_id="phone")],
        items=[
            # GoPro photo 2026-03-09T14:45  + 8:45 → 2026-03-09T23:30 (no plan day)
            _photo("p1", "GoPro", "2026-03-09T14:45:00", day=None),
            # GoPro VIDEO raw 2026-03-09T23:30 → +8:45 → 2026-03-10T08:15 (Day 1)
            _video("v1", "GoPro", "2026-03-09T23:30:00", day=None),
            # GoPro evening clip raw 2026-03-10T20:00 → +8:45 → 2026-03-11T04:45 (Day 2)
            _video("v2", "GoPro", "2026-03-10T20:00:00", day=1),
            # Phone item — must NOT be touched.
            _photo("ph", "phone", "2026-03-10T12:00:00", day=1),
        ],
    )
    root = base / "Nepal"
    root.mkdir(parents=True, exist_ok=True)
    eg = gw.create_event(doc, root)
    eg.close()
    return "evt-1"


def test_applies_offset_seconds_to_photos_and_videos(tmp_path):
    """The GoPro fix: a single recompute touches the photo AND every
    video (the zero-correction bug must be gone)."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)

    eg = gw.open_event("evt-1")
    try:
        affected = eg.recompute_corrected_times(
            "GoPro", offset_seconds=31_500)         # +8:45
        assert set(affected) == {"p1", "v1", "v2"}, affected

        # Video v1 — evening-of-day-before clip lands on Day 1.
        v1 = eg.item("v1")
        assert v1.kind == "video"
        assert v1.capture_time_raw == "2026-03-09T23:30:00", \
            "raw NEVER mutated"
        assert v1.capture_time_corrected == "2026-03-10T08:15:00"
        assert v1.day_number == 1
        assert v1.tz_offset_seconds == 31_500
        assert v1.tz_source == "user_declared"

        # Video v2 — late-evening clip lands on Day 2.
        v2 = eg.item("v2")
        assert v2.kind == "video"
        assert v2.capture_time_corrected == "2026-03-11T04:45:00"
        assert v2.day_number == 2

        # Phone item is untouched.
        ph = eg.item("ph")
        assert ph.capture_time_corrected == "2026-03-10T12:00:00"
        assert ph.tz_source == "none"
    finally:
        eg.close()


def test_raw_capture_time_never_mutated(tmp_path):
    """The virtual-EXIF invariant: capture_time_raw is sacred."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    eg = gw.open_event("evt-1")
    try:
        before = {it.id: it.capture_time_raw for it in eg.items()}
        eg.recompute_corrected_times("GoPro", offset_seconds=31_500)
        after = {it.id: it.capture_time_raw for it in eg.items()}
        assert before == after
    finally:
        eg.close()


def test_no_stale_day_retention(tmp_path):
    """A corrected date with no planned day moves the item to the
    undated bucket (None) — NEVER silently keeps the pre-correction
    day. spec/123 acceptance: the GoPro fix without honest day
    reassignment was the original-bug shape."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    eg = gw.open_event("evt-1")
    try:
        # Apply +8:45 — p1 raw 2026-03-09T14:45:00 → corrected
        # 2026-03-09T23:30:00; that date is NOT in the plan (plan starts
        # 2026-03-10). The item must land on None, not silently keep
        # its pre-correction day_number.
        eg.recompute_corrected_times("GoPro", offset_seconds=31_500)
        p1 = eg.item("p1")
        assert p1.capture_time_corrected == "2026-03-09T23:30:00"
        assert p1.day_number is None
    finally:
        eg.close()


def test_recompute_uses_offset_seconds_kwarg(tmp_path):
    """The new API surface is ``offset_seconds=…``; the legacy
    ``applied_offset_minutes`` is gone."""
    import inspect

    from mira.gateway.event_gateway import EventGateway
    sig = inspect.signature(EventGateway.recompute_corrected_times)
    assert "offset_seconds" in sig.parameters
    assert "applied_offset_minutes" not in sig.parameters


def test_recompute_quarantined_items_skipped(tmp_path):
    """Items without a raw timestamp are left alone."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    eg = gw.open_event("evt-1")
    try:
        # Pre-write a quarantined GoPro item (no raw time).
        eg.add_items([_photo("qx", "GoPro", "", day=None)])
        eg.recompute_corrected_times("GoPro", offset_seconds=31_500)
        qx = eg.item("qx")
        assert qx.capture_time_corrected == ""
        # Default tz_offset_seconds on a fresh row is 0 — recompute
        # must not have flipped it to 31 500.
        assert qx.tz_offset_seconds == 0
        assert qx.tz_source == "none"
    finally:
        eg.close()
