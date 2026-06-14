"""Tests for the create-event-from-photos ingest engine (spec/10).

Logic-level: real tiny files in a tmp tree + injected ``SourceItem`` lists (so the unit
path needs no exiftool). Covers the timezone correction that makes Nepal importable, day
routing, the quarantine / out-of-range / filename-recovery bins, byte-verbatim copy +
integrity verify, and the round-trip through the gateway.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from core.clock_calibration import CalibrationPair, build_calibration
from core.fresh_source import SourceItem
from mira.gateway import EventsIndex, Gateway
from mira.ingest import CameraPlan, DayPlan, IngestPlan, run_ingest
from mira.settings.repo import SettingsRepo

FIXED_NOW = "2026-06-01T12:00:00+00:00"


def _now() -> str:
    return FIXED_NOW


def _gateway(tmp_path: Path, base: Path) -> Gateway:
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "settings.json"),
        index=EventsIndex(tmp_path / "events_index.json"),
        now=_now,
    )
    gw.set_photos_base_path(str(base))
    return gw


def _mkfile(root: Path, name: str, content: bytes) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    p = root / name
    p.write_bytes(content)
    return p


# Nepal: a two-day trip at UTC+5:45.
_DAYS = [
    DayPlan(day_number=1, date=date(2026, 3, 10), description="Kathmandu", tz_offset_hours=5.75),
    DayPlan(day_number=2, date=date(2026, 3, 11), description="Pokhara", tz_offset_hours=5.75),
]


@pytest.fixture
def nepal(tmp_path):
    """A populated source + a configured gateway; returns (gateway, plan, paths)."""
    base = tmp_path / "photos"
    src = tmp_path / "src"
    event_root = base / "2026 - Nepal"

    # G9 clock was left on São Paulo (UTC-3); raw 08:00 → corrected 16:45 (+8:45) on day 1.
    g9 = _mkfile(src / "g9", "g9_0001.JPG", b"g9-photo-bytes")
    g9_vid = _mkfile(src / "g9", "g9_0002.MP4", b"g9-video-bytes-xyz")
    # iPhone is NTP-synced trip-local; passes through.
    iphone = _mkfile(src / "iphone", "IMG_5555.JPG", b"iphone-bytes")
    # A camera calibrated by a sync pair (constant +1h).
    paircam = _mkfile(src / "pc", "PC_0007.JPG", b"paircam-bytes")
    # No-timestamp, name not parseable → quarantine.
    notime = _mkfile(src / "x", "scanned-slide.jpg", b"no-time-bytes")
    # No EXIF time but a parseable filename → recovered, calibration skipped.
    recov = _mkfile(src / "x", "IMG_20260310_090000.jpg", b"recovered-bytes")
    # G9 photo whose corrected date (03-20) is outside the plan → out-of-day-range.
    g9_oor = _mkfile(src / "g9", "g9_0099.JPG", b"g9-oor-bytes")

    pair_cal = build_calibration(
        "PairCam",
        [CalibrationPair(
            camera_path=paircam, reference_path=paircam,
            camera_time=datetime(2026, 3, 10, 12, 0, 0),
            reference_time=datetime(2026, 3, 10, 13, 0, 0),  # +1h
        )],
    )

    items = [
        SourceItem(g9, datetime(2026, 3, 10, 8, 0, 0), "G9"),
        SourceItem(g9_vid, datetime(2026, 3, 10, 8, 1, 0), "G9"),
        SourceItem(iphone, datetime(2026, 3, 10, 18, 30, 0), "iPhone"),
        SourceItem(paircam, datetime(2026, 3, 10, 12, 0, 0), "PairCam"),
        SourceItem(notime, None, "Scanner"),
        SourceItem(recov, None, "iPhone"),
        SourceItem(g9_oor, datetime(2026, 3, 20, 8, 0, 0), "G9"),
    ]

    plan = IngestPlan(
        event_id="evt-nepal", event_name="2026 - Nepal",
        event_root=event_root, source_root=src, days=_DAYS,
        cameras=[
            CameraPlan("G9", configured_tz_hours=-3.0),
            CameraPlan("iPhone", is_phone=True),
            CameraPlan("PairCam", calibration=pair_cal),
            CameraPlan("Scanner"),
        ],
        start_date="2026-03-10", end_date="2026-03-11",
    )
    gw = _gateway(tmp_path, base)
    return gw, plan, items, event_root


def _run(nepal):
    gw, plan, items, event_root = nepal
    result = run_ingest(plan, gw, source_items=items, now=_now)
    return gw, result, event_root


# --------------------------------------------------------------------------- #
# Timezone correction (the Nepal-import reason this exists)
# --------------------------------------------------------------------------- #


def test_declared_offset_corrects_capture_time(nepal):
    gw, result, event_root = _run(nepal)
    eg = gw.open_event("evt-nepal")
    try:
        g9 = next(i for i in eg.items() if i.origin_relpath.endswith("g9_0001.JPG"))
    finally:
        eg.close()
    # raw 08:00 + (5.75 − (−3.0)) = +8:45 → 16:45, same day.
    assert g9.capture_time_raw == "2026-03-10T08:00:00"
    assert g9.capture_time_corrected == "2026-03-10T16:45:00"
    assert g9.tz_offset_minutes == 525
    # spec/52: tz_source enum aligned to camera_day_tz.source.
    assert g9.tz_source == "user_declared"
    assert g9.day_number == 1
    assert g9.origin_relpath == "Original Media/_cameras/Dia 1 - 2026-03-10 - Kathmandu/G9/g9_0001.JPG"


def test_phone_passes_through_uncorrected(nepal):
    gw, result, event_root = _run(nepal)
    eg = gw.open_event("evt-nepal")
    try:
        ip = next(i for i in eg.items() if i.origin_relpath.endswith("IMG_5555.JPG"))
    finally:
        eg.close()
    assert ip.capture_time_raw == ip.capture_time_corrected == "2026-03-10T18:30:00"
    assert ip.tz_offset_minutes == 0
    assert ip.tz_source == "none"
    assert ip.day_number == 1
    assert "_phones/" in ip.origin_relpath


def test_pair_calibration_applies_and_is_tagged(nepal):
    gw, result, event_root = _run(nepal)
    eg = gw.open_event("evt-nepal")
    try:
        pc = next(i for i in eg.items() if i.origin_relpath.endswith("PC_0007.JPG"))
    finally:
        eg.close()
    # +1h pair offset.
    assert pc.capture_time_corrected == "2026-03-10T13:00:00"
    assert pc.tz_offset_minutes == 60
    assert pc.tz_source == "pair_picker"
    assert pc.day_number == 1


# --------------------------------------------------------------------------- #
# The fidelity bins
# --------------------------------------------------------------------------- #


def test_no_timestamp_is_quarantined(nepal):
    gw, result, event_root = _run(nepal)
    assert result.quarantined == 1
    eg = gw.open_event("evt-nepal")
    try:
        q = next(i for i in eg.items() if i.origin_relpath.endswith("__scanned-slide.jpg"))
    finally:
        eg.close()
    assert q.quarantine_status == "no_timestamp"
    assert q.day_number is None
    assert q.capture_time_raw == ""
    assert "/_no_timestamp/" in q.origin_relpath


def test_filename_recovery_skips_calibration(nepal):
    gw, result, event_root = _run(nepal)
    assert result.filename_recovered == 1
    eg = gw.open_event("evt-nepal")
    try:
        r = next(i for i in eg.items() if i.origin_relpath.endswith("IMG_20260310_090000.jpg"))
    finally:
        eg.close()
    assert r.recovered_from_filename is True
    assert r.tz_source == "none"  # calibration skipped
    assert r.capture_time_raw == r.capture_time_corrected == "2026-03-10T09:00:00"
    assert r.day_number == 1


def test_out_of_day_range_binned(nepal):
    gw, result, event_root = _run(nepal)
    assert result.out_of_day_range == 1
    eg = gw.open_event("evt-nepal")
    try:
        oor = next(i for i in eg.items() if i.origin_relpath.endswith("g9_0099.JPG"))
    finally:
        eg.close()
    assert oor.day_number is None
    assert oor.quarantine_status == "ok"
    assert "/_out_of_day_range/" in oor.origin_relpath


# --------------------------------------------------------------------------- #
# Copy + integrity + round-trip
# --------------------------------------------------------------------------- #


def test_copies_are_verbatim_and_integrity_verifies(nepal):
    gw, result, event_root = _run(nepal)
    assert result.integrity_failures == []
    # The original bytes landed unchanged at the projected path.
    dest = event_root / "Original Media/_cameras/Dia 1 - 2026-03-10 - Kathmandu/G9/g9_0001.JPG"
    assert dest.read_bytes() == b"g9-photo-bytes"


def test_kind_split_and_counts(nepal):
    gw, result, event_root = _run(nepal)
    assert result.photos == 6  # 7 files, one is the mp4
    assert result.videos == 1
    assert result.items_created == 7
    eg = gw.open_event("evt-nepal")
    try:
        kinds = sorted(i.kind for i in eg.items())
    finally:
        eg.close()
    assert kinds.count("video") == 1 and kinds.count("photo") == 6


def test_ingest_enqueues_photo_thumbs_to_the_pool(tmp_path, monkeypatch):
    """Captured photos are immutable so their thumb is written ONCE — at
    ingest. The engine spawns a :class:`PhotoThumbPool` and feeds every
    photo item; videos are skipped (they use the video thumb cache).
    Test the wiring by capturing the enqueue calls."""
    seen: list = []

    class _StubPool:
        def __init__(self, *a, **k):
            pass

        def enqueue(self, event_root, source_path, sha256):
            seen.append((Path(event_root), Path(source_path), sha256))
            return True

        def stop(self, *, wait=True):
            pass

    monkeypatch.setattr(
        "mira.ingest.engine.PhotoThumbPool", _StubPool)

    # Tiny real fixture so the captured-photo branch and video branch
    # both fire.
    base = tmp_path / "photos"
    src = tmp_path / "src"
    event_root = base / "evt"
    a = _mkfile(src / "g9", "a.JPG", b"img-a")
    v = _mkfile(src / "g9", "v.MP4", b"vid-bytes")
    b = _mkfile(src / "g9", "b.JPG", b"img-b")
    items = [
        SourceItem(a, datetime(2026, 3, 10, 8, 0, 0), "G9"),
        SourceItem(v, datetime(2026, 3, 10, 8, 1, 0), "G9"),
        SourceItem(b, datetime(2026, 3, 10, 8, 2, 0), "G9"),
    ]
    plan = IngestPlan(
        event_id="evt", event_name="evt",
        event_root=event_root, source_root=src,
        days=[DayPlan(day_number=1, date=date(2026, 3, 10),
                      description="Day 1", tz_offset_hours=0.0)],
        cameras=[CameraPlan("G9", configured_tz_hours=0.0)],
        start_date="2026-03-10", end_date="2026-03-10",
    )
    gw = _gateway(tmp_path, base)
    run_ingest(plan, gw, source_items=items, now=_now)

    # Only the two photos are enqueued — the video is skipped.
    assert len(seen) == 2, seen
    paths = sorted(p.name for _, p, _ in seen)
    assert paths == ["a.JPG", "b.JPG"]
    # Each call carries the event root and a non-empty sha.
    for er, _, sha in seen:
        assert er == event_root
        assert sha and len(sha) == 64


def test_ingest_pool_stop_is_called_even_when_no_photos(
    tmp_path, monkeypatch,
):
    """``pool.stop`` must run on the empty-photos path too — guards against
    the pool's daemon threads outliving an empty-ingest test invocation."""
    stop_calls: list = []

    class _StubPool:
        def __init__(self, *a, **k):
            pass

        def enqueue(self, *a, **k):
            return False

        def stop(self, *, wait=True):
            stop_calls.append(wait)

    monkeypatch.setattr(
        "mira.ingest.engine.PhotoThumbPool", _StubPool)

    base = tmp_path / "photos"
    src = tmp_path / "src"
    event_root = base / "evt"
    plan = IngestPlan(
        event_id="evt", event_name="evt",
        event_root=event_root, source_root=src,
        days=[DayPlan(day_number=1, date=date(2026, 3, 10),
                      description="Day 1", tz_offset_hours=0.0)],
        cameras=[CameraPlan("G9", configured_tz_hours=0.0)],
        start_date="2026-03-10", end_date="2026-03-10",
    )
    gw = _gateway(tmp_path, base)
    run_ingest(plan, gw, source_items=[], now=_now)
    assert stop_calls == [False]   # ingest calls stop(wait=False)


def test_run_ingest_reports_progress(nepal):
    gw, plan, items, _ = nepal
    seen = []
    run_ingest(plan, gw, source_items=items, now=_now,
               progress=lambda d, t, m: seen.append((d, t)))
    assert seen, "progress should be reported"
    assert seen[-1] == (len(items), len(items))  # finishes at total/total
    assert all(t == len(items) for _, t in seen)  # total stable
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)  # non-decreasing


def test_materialised_event_is_in_the_index_and_queryable(nepal):
    gw, result, event_root = _run(nepal)
    listed = gw.list_events()
    assert [e["id"] for e in listed] == ["evt-nepal"]
    assert listed[0]["event_root"] == event_root
    eg = gw.open_event("evt-nepal")
    try:
        assert eg.event().name == "2026 - Nepal"
        # day_tree groups the dated items under days 1/2 (+ None bucket for un-dated).
        tree = {d["day_number"]: d["total"] for d in eg.day_tree()}
        assert tree[1] >= 4
        # every camera referenced by an item exists (FK held).
        cam_ids = {c.camera_id for c in eg.cameras()}
        assert {"G9", "iPhone", "PairCam", "Scanner"} <= cam_ids
    finally:
        eg.close()


def test_run_ingest_writes_country_code_to_trip_day_extras(tmp_path):
    """spec/47 — DayPlan.country_code threads through engine into the store
    trip_day's extras_json["country_code"]. Days without country_code keep
    an empty extras_json."""
    import json as _json

    base = tmp_path / "photos"
    src = tmp_path / "src"
    event_root = base / "evt"
    plan = IngestPlan(
        event_id="evt-cr", event_name="Costa Rica trip",
        event_root=event_root, source_root=src,
        days=[
            DayPlan(day_number=1, date=date(2026, 3, 10),
                    description="La Fortuna", tz_offset_hours=-6.0,
                    country_code="CR"),
            DayPlan(day_number=2, date=date(2026, 3, 11),
                    description="No country day", tz_offset_hours=-6.0),
        ],
        cameras=[CameraPlan("G9", configured_tz_hours=-6.0)],
        start_date="2026-03-10", end_date="2026-03-11",
    )
    gw = _gateway(tmp_path, base)
    run_ingest(plan, gw, source_items=[], now=_now)

    eg = gw.open_event("evt-cr")
    try:
        by_n = {d.day_number: d for d in eg.trip_days()}
        d1_extras = _json.loads(by_n[1].extras_json)
        assert d1_extras == {"country_code": "CR"}
        d2_extras = _json.loads(by_n[2].extras_json)
        assert d2_extras == {}
    finally:
        eg.close()


def test_run_ingest_uppercases_country_code(tmp_path):
    """Lowercase country codes from upstream auto-fill (rare but possible)
    are normalised to uppercase in extras_json — matches the schema
    convention (alpha-2 always upper)."""
    import json as _json

    base = tmp_path / "photos"
    src = tmp_path / "src"
    event_root = base / "evt"
    plan = IngestPlan(
        event_id="evt-lc", event_name="lowercase",
        event_root=event_root, source_root=src,
        days=[DayPlan(day_number=1, date=date(2026, 3, 10),
                      description="Day 1", tz_offset_hours=0.0,
                      country_code="cr")],
        cameras=[CameraPlan("G9", configured_tz_hours=0.0)],
        start_date="2026-03-10", end_date="2026-03-10",
    )
    gw = _gateway(tmp_path, base)
    run_ingest(plan, gw, source_items=[], now=_now)

    eg = gw.open_event("evt-lc")
    try:
        extras = _json.loads(eg.trip_days()[0].extras_json)
        assert extras == {"country_code": "CR"}
    finally:
        eg.close()
