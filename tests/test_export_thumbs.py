"""spec/63 slice 8 — export-file thumbs: Cut grids fill from disk.

Layers under test:

* ``core/photo_thumb_cache`` export functions: key/path math, the
  280-px build, the non-image (clip) skip, make-style mtime staleness,
  the background queue used by the four lineage writers.
* ``PhotoCache`` integration (offscreen): a Cut-grid class request
  (≤ 280 target) for a non-item path serves the on-disk thumb (proven
  by pixel color), keeps the ORIGINAL's native dims, lazily persists a
  thumb on miss (the pre-slice-8 self-heal), and never serves thumbs
  to bigger targets or to item (sha-registered) paths.
* The writer hook: ``record_single_lineage`` queues a thumb that
  materialises in the background; clip exports (.mp4) do not.

Module name deliberately dodges the conftest slice-B skip list.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from PyQt6.QtCore import QSize

from core.photo_thumb_cache import (
    EXPORT_THUMB_MAX_EDGE, ensure_export_thumb, export_thumb_path,
    queue_export_thumb, resolve_export_thumb, stop_export_thumb_builder,
    write_export_thumb_bytes)
from mira.ui.media.photo_cache import PhotoCache


def _spin_until(qapp, predicate, timeout_s: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _wait_for(predicate, timeout_s: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _make_jpeg(path: Path, w: int, h: int, color=(40, 90, 160)) -> Path:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (w, h), color).save(path, format="JPEG", quality=90)
    return path


def _thumb_dims(thumb: Path):
    from PIL import Image
    with Image.open(thumb) as im:
        return im.size


@pytest.fixture(autouse=True)
def _fresh_export_builder():
    """Each test gets a fresh module-level builder (the singleton holds
    a per-root dedupe set that would leak across tmp_paths)."""
    stop_export_thumb_builder()
    yield
    stop_export_thumb_builder()


# ── core: build / resolve / staleness ────────────────────────────────


def test_ensure_builds_280_thumb_under_exports_dir(tmp_path):
    root = tmp_path / "event"
    rel = "Edited Media/finals/photo.jpg"
    _make_jpeg(root / rel, 3000, 1500)
    thumb = ensure_export_thumb(root, rel)
    assert thumb is not None
    assert thumb == export_thumb_path(root, rel)
    assert ".cache" in thumb.parts and "exports" in thumb.parts
    w, h = _thumb_dims(thumb)
    assert max(w, h) == EXPORT_THUMB_MAX_EDGE == 280


def test_ensure_skips_clip_exports(tmp_path):
    root = tmp_path / "event"
    rel = "Edited Media/finals/clip.mp4"
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\x00" * 64)
    assert ensure_export_thumb(root, rel) is None
    assert not export_thumb_path(root, rel).exists()


def test_resolve_requires_freshness(tmp_path):
    root = tmp_path / "event"
    rel = "Edited Media/finals/photo.jpg"
    src = _make_jpeg(root / rel, 1000, 500)
    assert ensure_export_thumb(root, rel) is not None
    assert resolve_export_thumb(root, src) is not None
    # Re-export: the file is rewritten AFTER the thumb was made.
    # Simulated by aging the thumb behind the source (a forward bump
    # of the source would also outrun the rebuild's own timestamp).
    thumb = export_thumb_path(root, rel)
    src_stat = src.stat()
    os.utime(thumb, ns=(src_stat.st_atime_ns,
                        src_stat.st_mtime_ns - 5_000_000_000))
    assert resolve_export_thumb(root, src) is None       # stale
    assert ensure_export_thumb(root, rel) is not None    # rebuild
    assert resolve_export_thumb(root, src) is not None


def test_resolve_outside_root_is_none(tmp_path):
    root = tmp_path / "event"
    outsider = _make_jpeg(tmp_path / "elsewhere" / "p.jpg", 400, 200)
    assert resolve_export_thumb(root, outsider) is None


def test_key_normalises_separators(tmp_path):
    root = tmp_path / "event"
    assert (export_thumb_path(root, "Edited Media/finals/p.jpg")
            == export_thumb_path(root, "Edited Media\\finals\\p.jpg"))


def test_write_bytes_atomic_and_guarded(tmp_path):
    root = tmp_path / "event"
    rel = "Edited Media/finals/p.jpg"
    src = _make_jpeg(root / rel, 400, 200)
    assert not write_export_thumb_bytes(root, src, b"")
    assert not write_export_thumb_bytes(
        root, tmp_path / "elsewhere" / "p.jpg", b"data")
    assert write_export_thumb_bytes(root, src, b"jpegbytes")
    assert export_thumb_path(root, rel).read_bytes() == b"jpegbytes"


def test_queue_export_thumb_builds_in_background(tmp_path):
    root = tmp_path / "event"
    rel = "Edited Media/finals/photo.jpg"
    src = _make_jpeg(root / rel, 1200, 600)
    queue_export_thumb(root, rel)
    assert _wait_for(lambda: resolve_export_thumb(root, src) is not None)


def test_queue_skips_clips_without_starting_anything(tmp_path):
    root = tmp_path / "event"
    queue_export_thumb(root, "Edited Media/finals/clip.mp4")
    # Nothing to assert beyond "no thumb, no crash".
    assert not export_thumb_path(
        root, "Edited Media/finals/clip.mp4").exists()


# ── engine integration (offscreen) ───────────────────────────────────


@pytest.fixture
def cache(qapp):
    c = PhotoCache()
    yield c
    c.shutdown()


def _deliveries(cache):
    got = []
    cache.scaled_pixmap_ready.connect(
        lambda path, pm, native: got.append((Path(path), pm, native)))
    return got


_GRID_TARGET = QSize(280, 280)


def test_grid_request_serves_thumb_with_original_native_dims(
        qapp, cache, tmp_path):
    root = tmp_path / "event"
    rel = "Edited Media/finals/photo.jpg"
    src = _make_jpeg(root / rel, 3000, 1500)
    assert ensure_export_thumb(root, rel) is not None
    # Repaint the thumb a distinct color (mtime moves FORWARD, so it
    # stays fresh): a delivery in this color proves the thumb served.
    _make_jpeg(export_thumb_path(root, rel), 280, 140, color=(200, 30, 30))
    got = _deliveries(cache)
    cache.set_event_context(root, {})        # the Cut pages' registration
    cache.request_scaled_pixmap(src, _GRID_TARGET)
    assert _spin_until(qapp, lambda: len(got) >= 1)
    path, pm, native = got[0]
    assert path == src
    assert native == QSize(3000, 1500)       # the ORIGINAL's dims
    color = pm.toImage().pixelColor(5, 5)
    assert color.red() > 150 and color.blue() < 90


def test_big_target_bypasses_thumb(qapp, cache, tmp_path):
    root = tmp_path / "event"
    rel = "Edited Media/finals/photo.jpg"
    src = _make_jpeg(root / rel, 3000, 1500, color=(20, 60, 120))
    assert ensure_export_thumb(root, rel) is not None
    _make_jpeg(export_thumb_path(root, rel), 280, 140, color=(200, 30, 30))
    got = _deliveries(cache)
    cache.set_event_context(root, {})
    cache.request_scaled_pixmap(src, QSize(1024, 1024))
    assert _spin_until(qapp, lambda: len(got) >= 1)
    color = got[0][1].toImage().pixelColor(5, 5)
    assert color.blue() > 80 and color.red() < 80    # the ORIGINAL's color


def test_grid_miss_lazily_persists_thumb(qapp, cache, tmp_path):
    """The pre-slice-8 self-heal: an export with no thumb gets one on
    the first grid decode."""
    root = tmp_path / "event"
    rel = "Edited Media/finals/photo.jpg"
    src = _make_jpeg(root / rel, 3000, 1500)
    got = _deliveries(cache)
    cache.set_event_context(root, {})
    cache.request_scaled_pixmap(src, _GRID_TARGET)
    assert _spin_until(qapp, lambda: len(got) >= 1)
    assert _spin_until(
        qapp, lambda: resolve_export_thumb(root, src) is not None)


def test_item_paths_never_get_export_thumbs(qapp, cache, tmp_path):
    """A sha-registered (item) path decoded at a small target must not
    leave an export thumb — items belong to the proxy tier."""
    root = tmp_path / "event"
    rel = "Original Media/photo.jpg"
    src = _make_jpeg(root / rel, 3000, 1500)
    cache._proxy_builder.stop()              # isolate the routing rule
    got = _deliveries(cache)
    cache.set_event_context(root, {src: "sha-item"})
    cache.request_scaled_pixmap(src, _GRID_TARGET)
    assert _spin_until(qapp, lambda: len(got) >= 1)
    deadline = time.monotonic() + 0.6
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    assert resolve_export_thumb(root, src) is None


# ── the writer hook ──────────────────────────────────────────────────


def test_record_single_lineage_queues_thumb(tmp_path):
    """The one-shot lineage writer (photo single / clip) queues the
    background thumb; the clip variant no-ops by suffix."""
    import itertools

    from mira.gateway.event_gateway import EventGateway
    from mira.store.repo import EventStore
    from mira.store import models as m
    from mira.ui.edited._lineage import record_single_lineage

    now = "2026-06-12T10:00:00+00:00"
    root = tmp_path / "event"
    root.mkdir()
    store = EventStore.create(root / "event.db", event_id="evt-thumbs")
    doc = m.EventDocument(event=m.Event(
        uuid="evt-thumbs", name="Thumbs fixture",
        created_at=now, updated_at=now))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-06-12")]
    doc.cameras = [m.Camera(camera_id="G9")]
    doc.items = [m.Item(
        id="it-1", kind="photo", created_at=now, provenance="captured",
        origin_relpath="Original Media/it-1.jpg", sha256="a" * 64,
        byte_size=1000, materialized_at=now, materialized_phase="ingest",
        camera_id="G9", day_number=1,
        capture_time_raw="2026-06-12T09:00:00",
        capture_time_corrected="2026-06-12T09:00:00",
    )]
    store.save_document(doc)
    counter = itertools.count(1)
    eg = EventGateway(store, new_id=lambda: f"id-{next(counter)}")
    try:
        rel = "Edited Media/finals/photo.jpg"
        src = _make_jpeg(root / rel, 1200, 600)
        assert record_single_lineage(
            eg, root, item_id="it-1", dest_path=src)
        assert _wait_for(lambda: resolve_export_thumb(root, src) is not None)

        clip_rel = "Edited Media/finals/clip.mp4"
        clip = root / clip_rel
        clip.write_bytes(b"\x00" * 64)
        assert record_single_lineage(
            eg, root, item_id="it-1", dest_path=clip)
        assert not export_thumb_path(root, clip_rel).exists()
    finally:
        eg.close()
