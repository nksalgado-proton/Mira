"""Tests for the gateway-driven EventPlanPage 2×2 overview stats (Slice C data seam).

Pure-logic (no Qt): the four quadrant inputs recomputed from an open ``EventGateway`` —
the rebuild's replacement for ``core.event_stats`` filesystem/journal walks. Pins the
funnel %, the 'furthest phase with kept items' style/photo rule, the per-camera fallback,
and the on-the-fly ``event_root / origin_relpath`` photo resolution.
"""
from __future__ import annotations

import random

from mira import overview_stats
from mira.gateway import EventsIndex, Gateway
from mira.settings.repo import SettingsRepo
from mira.store import models as m

STAMP = "2026-03-10T00:00:00"


def _gateway(tmp_path, base):
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "settings.json"),
        index=EventsIndex(tmp_path / "events_index.json"),
    )
    gw.set_photos_base_path(str(base))
    return gw


def _photo(item_id, camera_id, raw, classification=None):
    return m.Item(
        id=item_id, kind="photo", origin_relpath=f"{item_id}.rw2", sha256=item_id,
        byte_size=1, materialized_at=STAMP, materialized_phase="ingest",
        camera_id=camera_id, capture_time_raw=raw, capture_time_corrected=raw,
        created_at=STAMP, day_number=1, classification=classification,
    )


def _make_event(gw, base):
    """5 captured photos (3 G9 + 2 phone); 3 kept in cull (2 Wildlife, 1 unclassified)."""
    doc = m.EventDocument(
        event=m.Event(uuid="evt-1", name="Trip", created_at=STAMP, updated_at=STAMP,
                      start_date="2026-03-10", end_date="2026-03-10"),
        trip_days=[m.TripDay(day_number=1, date="2026-03-10", tz_minutes=0)],
        cameras=[m.Camera(camera_id="G9"), m.Camera(camera_id="phone")],
        items=[
            _photo("g1", "G9", "2026-03-10T10:00:00", "wildlife"),
            _photo("g2", "G9", "2026-03-10T10:01:00", "wildlife"),
            _photo("g3", "G9", "2026-03-10T10:02:00", "landscape"),
            _photo("p1", "phone", "2026-03-10T10:03:00", None),
            _photo("p2", "phone", "2026-03-10T10:04:00", None),
        ],
    )
    root = base / "Trip"
    root.mkdir(parents=True, exist_ok=True)
    eg = gw.create_event(doc, root)
    eg.set_phase_state("g1", "pick", "picked")
    eg.set_phase_state("g2", "pick", "picked")
    eg.set_phase_state("p1", "pick", "picked")
    eg.close()
    # Materialise the on-disk originals the random-photo picker resolves + verifies.
    for stem in ("g1", "g2", "g3", "p1", "p2"):
        (root / f"{stem}.rw2").write_bytes(b"x")
    return "evt-1"


def test_phase_funnel_breakdown(tmp_path):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    eg = gw.open_event("evt-1")
    try:
        bars = overview_stats.phase_funnel_breakdown(eg)
    finally:
        eg.close()
    # spec/48 + spec/52: cull+select collapsed to 'pick'; 'share' dropped
    # until the Cuts surfaces land (no kept-count source yet).
    labels = [b[0] for b in bars]
    assert labels == ["Captured", "Picked", "Edited"]
    by_label = {b[0]: (b[1], b[2]) for b in bars}
    assert by_label["Captured"] == (5, 100.0)
    assert by_label["Picked"] == (3, 60.0)
    assert by_label["Edited"] == (0, 0.0)


def test_style_breakdown_last_phase_groups_kept_by_classification(tmp_path):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    eg = gw.open_event("evt-1")
    try:
        slices, label = overview_stats.style_breakdown_last_phase(eg)
    finally:
        eg.close()
    # spec/48 vocab — collapsed cull→pick.
    assert label == "Picked"                                   # furthest phase with keeps
    assert slices == (("Wildlife", 2), ("General", 1))         # title-cased; unset → General


def test_captured_per_camera_counts(tmp_path):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    eg = gw.open_event("evt-1")
    try:
        counts = overview_stats.captured_per_camera_counts(eg)
    finally:
        eg.close()
    assert counts == (("G9", 3), ("phone", 2))                 # desc by count


def test_captured_per_camera_time_share(tmp_path):
    """The Capture pie weighs by running time: photos at the short slide duration,
    videos by their probed ``duration_ms``. A NULL-duration video (pre-probe
    ingest) counts as one photo-slide equivalent — the old summed-clip-spans
    fallback retired with spec/56 (segment geometry is derived, not stored)."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    doc = m.EventDocument(
        event=m.Event(uuid="evt-1", name="Trip", created_at=STAMP, updated_at=STAMP),
        trip_days=[m.TripDay(day_number=1, date="2026-03-10", tz_minutes=0)],
        cameras=[m.Camera(camera_id="G9"), m.Camera(camera_id="phone")],
        items=[
            _photo("p1", "phone", "2026-03-10T10:00:00"),
            _photo("p2", "phone", "2026-03-10T10:01:00"),
            m.Item(id="v1", kind="video", origin_relpath="v1.mp4", sha256="v1",
                   byte_size=1, materialized_at=STAMP, materialized_phase="ingest",
                   camera_id="G9", capture_time_raw="2026-03-10T10:02:00",
                   capture_time_corrected="2026-03-10T10:02:00", created_at=STAMP,
                   day_number=1, duration_ms=40_000),     # probed: 40s
            m.Item(id="v2", kind="video", origin_relpath="v2.mp4", sha256="v2",
                   byte_size=1, materialized_at=STAMP, materialized_phase="ingest",
                   camera_id="G9", capture_time_raw="2026-03-10T10:03:00",
                   capture_time_corrected="2026-03-10T10:03:00", created_at=STAMP,
                   day_number=1),                          # un-probed: NULL duration
        ],
    )
    eg = gw.create_event(doc, base / "Trip")
    try:
        share = overview_stats.captured_per_camera_time_share(eg, photo_seconds=4.0)
    finally:
        eg.close()
    # G9: 40s probed + 4s photo-equivalent for the un-probed video = 44s.
    # phone: 2 photos × 4.0s = 8s.
    assert share == (("G9", 44), ("phone", 8))


def test_pick_random_last_phase_photo_resolves_under_event_root(tmp_path):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    eg = gw.open_event("evt-1")
    try:
        path = overview_stats.pick_random_last_phase_photo(eg, rng=random.Random(0))
    finally:
        eg.close()
    assert path is not None
    assert path.exists()
    # A kept cull photo, resolved on the fly as event_root / origin_relpath.
    assert path.parent == base / "Trip"
    assert path.name in {"g1.rw2", "g2.rw2", "p1.rw2"}


def test_empty_event_yields_empty_stats(tmp_path):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    doc = m.EventDocument(
        event=m.Event(uuid="evt-empty", name="Empty", created_at=STAMP, updated_at=STAMP),
    )
    root = base / "Empty"
    root.mkdir(parents=True, exist_ok=True)
    gw.create_event(doc, root).close()
    eg = gw.open_event("evt-empty")
    try:
        assert overview_stats.phase_funnel_breakdown(eg) == ()
        assert overview_stats.style_breakdown_last_phase(eg) == ((), "")
        assert overview_stats.pick_random_last_phase_photo(eg) is None
        assert overview_stats.captured_per_camera_counts(eg) == ()
    finally:
        eg.close()


def test_no_keeps_falls_back_to_captured_photo(tmp_path):
    """With no kept items, the photo picker falls back to the captured pool."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    doc = m.EventDocument(
        event=m.Event(uuid="evt-2", name="Raw", created_at=STAMP, updated_at=STAMP),
        trip_days=[m.TripDay(day_number=1, date="2026-03-10", tz_minutes=0)],
        cameras=[m.Camera(camera_id="G9")],
        items=[_photo("c1", "G9", "2026-03-10T10:00:00", None)],
    )
    root = base / "Raw"
    root.mkdir(parents=True, exist_ok=True)
    gw.create_event(doc, root).close()
    (root / "c1.rw2").write_bytes(b"x")
    eg = gw.open_event("evt-2")
    try:
        # No phase has keeps → style empty, but the photo falls back to the captured photo.
        assert overview_stats.style_breakdown_last_phase(eg) == ((), "")
        path = overview_stats.pick_random_last_phase_photo(eg)
    finally:
        eg.close()
    assert path == base / "Raw" / "c1.rw2"
