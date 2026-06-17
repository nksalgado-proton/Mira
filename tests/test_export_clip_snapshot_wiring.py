"""The Days-Grid Export trigger wires picked segments + snapshots into
the spec/60 batch (Nelson 2026-06-15 — clip + snapshot Export reconnect).

Pins the contract:

* Picked SEGMENTS off a video cell get collected as
  :class:`~mira.store.models.VideoSegment` rows for the spec/56 slice-4
  :func:`~core.edit_export_walker.build_clip_units` walker — they do
  NOT travel through the PhotoUnit path.
* Picked SNAPSHOTS off the same video cell collect as
  :class:`~mira.ui.exported.batch.SnapshotCell` rows (their frames
  extract at submit time and ship through the photo lane).
* :func:`~mira.ui.exported.batch.submit_export_batch` builds the
  manifest with ``clips=tuple(ClipUnit...)`` derived from the walker
  (the prior wiring passed ``clips=()`` and silently dropped every
  segment).
"""
from __future__ import annotations

import itertools
from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest

from mira.gateway import Gateway
from mira.gateway.event_gateway import EventGateway
from mira.picked.status import STATE_PICKED, STATE_SKIPPED
from mira.settings.repo import SettingsRepo
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.pages.days_grid_page import DaysGridPage, GridItem

FIXED_NOW = "2026-06-15T12:00:00+00:00"


def _now() -> str:
    return FIXED_NOW


@pytest.fixture(autouse=True)
def _stub_exif(monkeypatch):
    import core.exif_reader as er
    monkeypatch.setattr(er, "read_exif_single", lambda path: None)
    monkeypatch.setattr(er, "read_exif_batch", lambda paths: [])


def _doc_with_video() -> m.EventDocument:
    """One day with one source video item (no captured photos)."""
    doc = m.EventDocument(event=m.Event(
        uuid="evt-clipwire", name="Clip wiring fixture",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    doc.items.append(m.Item(
        id="vidA", kind="video", provenance="captured",
        created_at=FIXED_NOW,
        origin_relpath="Original Media/vidA.mp4",
        sha256="v" * 64, byte_size=128,
        materialized_at=FIXED_NOW, materialized_phase="ingest",
        duration_ms=30_000,
        camera_id="G9", day_number=1,
        capture_time_raw="2026-04-01T08:00:00",
        capture_time_corrected="2026-04-01T08:00:00",
    ))
    # The parent video must be Pick-kept so the Days Grid surfaces it
    # in Export mode (the day-grid engine reuses Pick's projection).
    doc.phase_states.append(m.PhaseState(
        item_id="vidA", phase="pick", state="picked"))
    return doc


@pytest.fixture
def event_dir(tmp_path):
    p = tmp_path / "Original Media" / "vidA.mp4"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00" * 128)
    return tmp_path


@pytest.fixture
def store_and_gateway(event_dir):
    store = EventStore.create(event_dir / "event.db", event_id="evt-clipwire")
    store.save_document(_doc_with_video())
    counter = itertools.count(1)
    eg = EventGateway(
        store, event_root=event_dir,
        now=_now, new_id=lambda: f"id-{next(counter)}")
    yield store, eg
    eg.close()


@pytest.fixture
def app_gateway(event_dir, store_and_gateway, monkeypatch, tmp_path):
    store, _ = store_and_gateway
    gw = Gateway(settings=SettingsRepo(tmp_path / "settings.json"))
    counter = itertools.count(100)

    def _open_event(_event_id):
        return EventGateway(
            store, event_root=event_dir, now=_now,
            new_id=lambda: f"app-{next(counter)}")
    monkeypatch.setattr(gw, "open_event", _open_event)
    yield gw


def _add_segments_and_snap(eg: EventGateway) -> tuple:
    """Lay down two cut points (→ 3 segments), pick the outer two, and
    drop one snapshot (auto-picked by ``create_video_snapshot``)."""
    # Markers at 10s and 20s → segments [0,10), [10,20), [20,30).
    eg.add_video_marker("vidA", 10_000)
    eg.add_video_marker("vidA", 20_000)
    eg.ensure_video_segments("vidA", default_state="skipped")
    segs = eg.video_segments("vidA")
    assert len(segs) == 3
    eg.set_phase_state(segs[0].item_id, "edit", "picked")
    eg.set_phase_state(segs[2].item_id, "edit", "picked")
    snap_id = eg.create_video_snapshot("vidA", 5_000)
    return segs, snap_id


def _open_with_video(app_gateway, event_dir: Path) -> DaysGridPage:
    """Open the page in Export mode, then inject a single video
    GridItem directly. The day-grid bucket pipeline reads EXIF from
    real photo bytes; videos in tests have no EXIF, so we bypass the
    grid-build step and pin the input to ``_collect_ship_cells`` to a
    deterministic shape (the method only depends on ``_items``,
    ``_eg`` and ``_day_number``)."""
    page = DaysGridPage(app_gateway)
    assert page.open_for_day(
        "evt-clipwire", 1, title="Day", date_iso="2026-04-01",
        phase="export")
    page._items = [GridItem(
        item_id="vidA",
        item_kind="video",
        state=STATE_PICKED,
        _path=event_dir / "Original Media" / "vidA.mp4",
    )]
    return page


def test_collect_ship_cells_pulls_picked_segments_and_snapshots(
        qapp, app_gateway, store_and_gateway, event_dir):
    """The page's _collect_ship_cells expands a video cell into its
    picked segments + picked snapshots — the parent video item never
    becomes its own ship unit."""
    _, eg_setup = store_and_gateway
    segs, snap_id = _add_segments_and_snap(eg_setup)
    expected_picked = {segs[0].item_id, segs[2].item_id}

    page = _open_with_video(app_gateway, event_dir)
    # The grid has one video cell (the parent); the children are not
    # surfaced as their own grid items pre-Export (no origin_relpath).
    kinds = sorted({it.item_kind for it in page._items})
    assert "video" in kinds

    photo_cells, segment_rows, snapshot_cells = page._collect_ship_cells()
    assert photo_cells == []                       # no captured photos
    assert {sr.item_id for sr in segment_rows} == expected_picked
    assert [sc.item_id for sc in snapshot_cells] == [snap_id]
    assert snapshot_cells[0].at_ms == 5_000
    assert snapshot_cells[0].video_item_id == "vidA"
    page.close_event()


def test_already_shipped_segment_is_skipped(
        qapp, app_gateway, store_and_gateway, event_dir):
    """A segment with an Exported Media lineage row is not re-shipped
    (the re-export gate matches the photo path)."""
    _, eg_setup = store_and_gateway
    segs, _snap_id = _add_segments_and_snap(eg_setup)
    # Mark segs[0] as already shipped via a fake lineage row.
    eg_setup.record_lineage(m.Lineage(
        export_relpath="Exported Media/Dia 1/vidA_clip1.mp4",
        phase="edit", source_kind="item",
        source_item_id=segs[0].item_id,
        recipe_json=None, exported_at=FIXED_NOW))
    assert segs[0].item_id in eg_setup.exported_item_ids()

    page = _open_with_video(app_gateway, event_dir)
    _, segment_rows, _ = page._collect_ship_cells()
    # Only the second picked segment remains in the ship set.
    assert {sr.item_id for sr in segment_rows} == {segs[2].item_id}
    page.close_event()


def test_submit_export_batch_builds_clip_units_via_the_walker(
        qapp, app_gateway, store_and_gateway, event_dir, monkeypatch):
    """End-to-end the wiring slice: the page collects picked segments;
    :func:`submit_export_batch` translates them into
    :class:`~core.export_manifest.ClipUnit` rows on the manifest. Pre-
    fix, ``clips=()`` was hardcoded and every segment silently
    disappeared (batch.py:199)."""
    from core.export_manifest import ExportManifest

    _, eg_setup = store_and_gateway
    segs, snap_id = _add_segments_and_snap(eg_setup)

    page = _open_with_video(app_gateway, event_dir)

    captured = {}

    class _FakeQueue:
        def enqueue(self, job, title, commit, *, job_type="export"):
            captured["job"] = job
            captured["title"] = title
            captured["commit"] = commit
            captured["job_type"] = job_type
            return None

    monkeypatch.setattr(
        page, "window", lambda: type("W", (), {"batch_queue": _FakeQueue()})())

    # Stub the snapshot frame extract so the test doesn't spawn
    # ffmpeg — write a tiny JPEG at the expected temp path.
    def _fake_extract_frame(video_path, position_ms, output_path, **_kw):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        from PIL import Image
        Image.new("RGB", (8, 8), (50, 50, 50)).save(
            str(output_path), "JPEG", quality=85)
        return output_path
    import core.video_extract as ve
    monkeypatch.setattr(ve, "extract_frame", _fake_extract_frame)

    page._on_export_clicked()

    job = captured["job"]
    assert job is not None
    manifest = job._manifest                       # accessed by the test only
    assert isinstance(manifest, ExportManifest)

    # Two ClipUnits — one per picked segment — with the walker's
    # base-name shape: ``<videostem>_clipN`` (1-based seg_index).
    clip_unit_ids = {cu.unit_id for cu in manifest.clips}
    assert clip_unit_ids == {segs[0].item_id, segs[2].item_id}
    base_names = {cu.base_name for cu in manifest.clips}
    assert base_names == {"vidA_clip1", "vidA_clip3"}
    # The plan carries the marker-derived bounds for each segment.
    plans_by_uid = {cu.unit_id: cu.plan for cu in manifest.clips}
    assert plans_by_uid[segs[0].item_id]["in_ms"] == 0
    assert plans_by_uid[segs[0].item_id]["out_ms"] == 10_000
    assert plans_by_uid[segs[2].item_id]["in_ms"] == 20_000
    assert plans_by_uid[segs[2].item_id]["out_ms"] == 30_000

    # The snapshot rides the photo lane — it lands as a PhotoUnit on
    # the manifest, source = the extracted temp JPEG (stem = item id
    # so the lineage stem-map can re-attach).
    photo_unit_ids = {u.unit_id for u in manifest.units}
    assert snap_id in photo_unit_ids
    snap_unit = next(u for u in manifest.units if u.unit_id == snap_id)
    assert Path(snap_unit.source).stem == snap_id

    page.close_event()
