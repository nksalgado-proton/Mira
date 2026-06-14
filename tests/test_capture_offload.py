"""Capture Option 1 — record a legacy offload manifest into the event DB (spec/13 §1).

Covers ``mira.ingest.offload_record.record_offload`` directly (the one new data-layer
piece): the legacy offload engine copies + writes a manifest verbatim; this projects that
manifest into item rows (raw + corrected times, no bake) + a camera row, appended via the
gateway into the existing event.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from core.fresh_source import SourceItem
from mira.gateway import EventsIndex, Gateway
from mira.ingest import CameraPlan, DayPlan, IngestPlan, run_ingest
from mira.ingest.offload_record import record_offload
from mira.settings.repo import SettingsRepo

NOW = "2026-06-01T00:00:00+00:00"


def _now() -> str:
    return NOW


def _gateway(tmp_path, base):
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "settings.json"),
        index=EventsIndex(tmp_path / "events_index.json"),
        now=_now,
    )
    gw.set_photos_base_path(str(base))
    return gw


class _Rec:
    """Duck-typed OffloadFileRecord."""
    def __init__(self, dest, sha256, bytes_, day_number, capture_time_raw):
        self.dest = str(dest)
        self.sha256 = sha256
        self.bytes = bytes_
        self.day_number = day_number
        self.capture_time_raw = capture_time_raw
        self.capture_time_corrected = capture_time_raw


class _Manifest:
    def __init__(self, files):
        self.files = files


@pytest.fixture
def seeded(tmp_path):
    base = tmp_path / "photos"
    event_root = base / "Nepal"
    gw = _gateway(tmp_path, base)
    src = tmp_path / "card1"
    src.mkdir(parents=True)
    f = src / "g9_0001.JPG"
    f.write_bytes(b"seed")
    run_ingest(
        IngestPlan(
            event_id="evt1", event_name="Nepal", event_root=event_root, source_root=src,
            days=[DayPlan(1, date(2026, 3, 10), "Kathmandu", 5.75),
                  DayPlan(2, date(2026, 3, 11), "Pokhara", 5.75)],
            cameras=[CameraPlan("G9", configured_tz_hours=-3.0)],
        ),
        gw, source_items=[SourceItem(f, None, "G9")], now=_now,
    )
    return gw, event_root


def test_record_offload_projects_manifest_into_items(seeded):
    gw, event_root = seeded
    cap = event_root / "00 - Captured" / "_cameras" / "Dia 2 - 2026-03-11 - Pokhara" / "G9_mkII"
    files = [
        _Rec(cap / "P1.JPG", "sha-p1", 100, 2, "2026-03-11T08:00:00"),
        _Rec(cap / "P2.JPG", "sha-p2", 200, 2, "2026-03-11T08:05:00"),
    ]
    n = record_offload(
        _Manifest(files), gateway=gw, event_id="evt1", camera_id="G9_mkII",
        bucket="_cameras", offset_hours=8.75, event_root=event_root, now=_now,
    )
    assert n == 2
    eg = gw.open_event("evt1")
    try:
        items = {Path(i.origin_relpath).name: i for i in eg.items()}
        assert "P1.JPG" in items and "P2.JPG" in items
        p1 = items["P1.JPG"]
        # camera_id is the user-typed one (folder name), not EXIF-derived.
        assert p1.camera_id == "G9_mkII"
        # corrected = raw + 8:45; no bake — raw is preserved verbatim.
        assert p1.capture_time_raw == "2026-03-11T08:00:00"
        assert p1.capture_time_corrected == "2026-03-11T16:45:00"
        assert p1.tz_offset_minutes == 525 and p1.day_number == 2
        # The new camera row was added (insert-only-missing).
        cams = {c.camera_id for c in eg.cameras()}
        assert "G9_mkII" in cams
    finally:
        eg.close()


def test_record_offload_quarantines_files_without_exif(seeded):
    gw, event_root = seeded
    qdir = event_root / "00 - Captured" / "_no_timestamp" / "G9_mkII"
    files = [_Rec(qdir / "noexif.JPG", "sha-q", 50, 0, None)]
    record_offload(
        _Manifest(files), gateway=gw, event_id="evt1", camera_id="G9_mkII",
        bucket="_cameras", offset_hours=8.75, event_root=event_root, now=_now,
    )
    eg = gw.open_event("evt1")
    try:
        q = next(i for i in eg.items() if Path(i.origin_relpath).name == "noexif.JPG")
        assert q.quarantine_status == "no_timestamp"
        assert q.day_number is None and q.capture_time_raw == ""
    finally:
        eg.close()


def test_record_offload_zero_offset_leaves_times_equal(seeded):
    gw, event_root = seeded
    cap = event_root / "00 - Captured" / "_cameras" / "Dia 1 - 2026-03-10 - Kathmandu" / "Phone"
    files = [_Rec(cap / "IMG.JPG", "sha-i", 10, 1, "2026-03-10T09:00:00")]
    record_offload(
        _Manifest(files), gateway=gw, event_id="evt1", camera_id="Phone",
        bucket="_phones", offset_hours=0.0, event_root=event_root, now=_now,
    )
    eg = gw.open_event("evt1")
    try:
        i = next(x for x in eg.items() if Path(x.origin_relpath).name == "IMG.JPG")
        assert i.capture_time_raw == i.capture_time_corrected == "2026-03-10T09:00:00"
        assert i.tz_offset_minutes == 0 and i.tz_source == "none"
        phone = next(c for c in eg.cameras() if c.camera_id == "Phone")
        assert phone.is_phone is True
    finally:
        eg.close()
