"""spec/63 slice 7 — the proxy tier: core cache, builder, engine wiring.

Three layers under test:

* ``core/photo_proxy_cache`` pure I/O: build → resolve round-trip with
  the ORIGINAL's native dims in the sidecar, mtime/size invalidation,
  sidecar-as-commit-marker atomicity, EXIF-orientation native swap,
  stats + clear, the write-on-decode qualification floor.
* ``ProxyBuilder``: seeded builds, cross-root queue drop, sha dedupe.
* ``PhotoCache`` integration (offscreen): the scaled tier SERVES the
  proxy while reporting the original's native dims (proven by pixel
  color — the proxy file is repainted a distinct color after build),
  write-on-decode persists proxy-grade decodes and skips small-window
  ones, corrupt proxies self-heal, and ``set_event_context`` alone
  (no scaled request) gets proxies built in the background.

Module name deliberately dodges the conftest slice-B skip list.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QImage

from core import photo_proxy_cache as ppc
from core.photo_proxy_cache import (
    PROXY_MAX_EDGE, ProxyBuilder, clear_proxy_cache, ensure_photo_proxy,
    invalidate_proxy, proxy_cache_stats, proxy_meta_path, proxy_path,
    qualifies_as_proxy, resolve_proxy, write_proxy)
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
    """Qt-free poll for the core/builder tests."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _make_jpeg(path: Path, w: int, h: int, color=(40, 90, 160),
               orientation: int | None = None) -> Path:
    from PIL import Image
    im = Image.new("RGB", (w, h), color)
    kwargs = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = orientation
        kwargs["exif"] = exif
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, format="JPEG", quality=90, **kwargs)
    return path


def _proxy_dims(event_root: Path, sha: str):
    from PIL import Image
    with Image.open(proxy_path(event_root, sha)) as im:
        return im.size


# ── core: build → resolve round-trip ─────────────────────────────────


def test_build_bounds_to_proxy_edge_and_keeps_native_dims(tmp_path):
    root = tmp_path / "event"
    src = _make_jpeg(tmp_path / "src" / "big.jpg", 4000, 2000)
    assert ensure_photo_proxy(root, src, "sha-big")
    hit = resolve_proxy(root, "sha-big", src)
    assert hit is not None
    assert (hit.native_w, hit.native_h) == (4000, 2000)
    w, h = _proxy_dims(root, "sha-big")
    assert max(w, h) == PROXY_MAX_EDGE
    assert (w, h) == (2560, 1280)


def test_small_source_is_native_complete(tmp_path):
    root = tmp_path / "event"
    src = _make_jpeg(tmp_path / "src" / "small.jpg", 800, 600)
    assert ensure_photo_proxy(root, src, "sha-small")
    hit = resolve_proxy(root, "sha-small", src)
    assert hit is not None
    assert (hit.native_w, hit.native_h) == (800, 600)
    assert _proxy_dims(root, "sha-small") == (800, 600)


def test_exif_orientation_swaps_native_and_uprights_proxy(tmp_path):
    root = tmp_path / "event"
    src = _make_jpeg(tmp_path / "src" / "rot.jpg", 400, 200, orientation=6)
    assert ensure_photo_proxy(root, src, "sha-rot")
    hit = resolve_proxy(root, "sha-rot", src)
    assert hit is not None
    # Orientation 6 = 90° CW: post-orientation dims swap.
    assert (hit.native_w, hit.native_h) == (200, 400)
    assert _proxy_dims(root, "sha-rot") == (200, 400)


def test_mtime_change_invalidates_then_rebuild_repairs(tmp_path):
    root = tmp_path / "event"
    src = _make_jpeg(tmp_path / "src" / "p.jpg", 1000, 500)
    assert ensure_photo_proxy(root, src, "sha-p")
    assert resolve_proxy(root, "sha-p", src) is not None
    stat = src.stat()
    os.utime(src, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    assert resolve_proxy(root, "sha-p", src) is None     # stale
    assert ensure_photo_proxy(root, src, "sha-p")        # rebuild
    assert resolve_proxy(root, "sha-p", src) is not None


def test_size_change_invalidates(tmp_path):
    root = tmp_path / "event"
    src = _make_jpeg(tmp_path / "src" / "p.jpg", 1000, 500)
    assert ensure_photo_proxy(root, src, "sha-p")
    _make_jpeg(src, 1400, 700)                            # replaced source
    assert resolve_proxy(root, "sha-p", src) is None


def test_proxy_without_sidecar_is_invisible(tmp_path):
    root = tmp_path / "event"
    src = _make_jpeg(tmp_path / "src" / "p.jpg", 1000, 500)
    pfile = proxy_path(root, "sha-naked")
    pfile.parent.mkdir(parents=True, exist_ok=True)
    pfile.write_bytes(src.read_bytes())
    assert resolve_proxy(root, "sha-naked", src) is None


def test_invalidate_drops_the_pair(tmp_path):
    root = tmp_path / "event"
    src = _make_jpeg(tmp_path / "src" / "p.jpg", 1000, 500)
    assert ensure_photo_proxy(root, src, "sha-p")
    invalidate_proxy(root, "sha-p")
    assert not proxy_path(root, "sha-p").exists()
    assert not proxy_meta_path(root, "sha-p").exists()
    assert resolve_proxy(root, "sha-p", src) is None


def test_write_proxy_rejects_garbage_args(tmp_path):
    root = tmp_path / "event"
    src = _make_jpeg(tmp_path / "src" / "p.jpg", 100, 50)
    assert not write_proxy(root, "sha-x", src, b"", 100, 50)
    assert not write_proxy(root, "sha-x", src, b"data", 0, 50)
    assert not write_proxy(
        root, "sha-x", tmp_path / "missing.jpg", b"data", 100, 50)


def test_stats_and_clear(tmp_path):
    root = tmp_path / "event"
    for i, name in enumerate(("a", "b")):
        src = _make_jpeg(tmp_path / "src" / f"{name}.jpg", 600 + i, 400)
        assert ensure_photo_proxy(root, src, f"sha-{name}")
    count, total = proxy_cache_stats(root)
    assert count == 2
    assert total > 0
    removed = clear_proxy_cache(root)
    assert removed >= 2                       # jpgs + sidecars
    assert proxy_cache_stats(root) == (0, 0)


def test_stats_on_missing_dir(tmp_path):
    assert proxy_cache_stats(tmp_path / "nope") == (0, 0)
    assert clear_proxy_cache(tmp_path / "nope") == 0


def test_qualifies_as_proxy_floor():
    # Full-size decode of a big source → proxy-grade at the 2560 cap.
    assert qualifies_as_proxy(2560, 1707, 6000, 4000)
    # Small-window decode of a big source → NOT proxy-grade.
    assert not qualifies_as_proxy(1536, 1024, 6000, 4000)
    # Native-complete small source qualifies, with rounding tolerance.
    assert qualifies_as_proxy(800, 600, 800, 600)
    assert qualifies_as_proxy(798, 600, 800, 600)
    assert not qualifies_as_proxy(640, 480, 800, 600)
    # Degenerate dims never qualify.
    assert not qualifies_as_proxy(0, 0, 800, 600)
    assert not qualifies_as_proxy(800, 600, 0, 0)


# ── the builder ──────────────────────────────────────────────────────


def test_builder_builds_seeded_pairs(tmp_path):
    root = tmp_path / "event"
    pairs = []
    for i in range(3):
        src = _make_jpeg(tmp_path / "src" / f"s{i}.jpg", 900 + i, 450)
        pairs.append((src, f"sha-{i}"))
    builder = ProxyBuilder()
    try:
        assert builder.seed(root, pairs) == 3
        assert _wait_for(lambda: all(
            resolve_proxy(root, sha, src) is not None
            for src, sha in pairs))
    finally:
        builder.stop()


def test_builder_seed_dedupes_currently_queued(tmp_path):
    """Dedupe means "currently queued" — an EXECUTED (or in-flight)
    key may re-queue later (stale proxy rebuilds, re-exported files);
    ensure()'s fast resolve-hit makes re-seeds nearly free."""
    root = tmp_path / "event"
    src1 = _make_jpeg(tmp_path / "src" / "s1.jpg", 500, 250)
    src2 = _make_jpeg(tmp_path / "src" / "s2.jpg", 500, 250)
    builder = ProxyBuilder(is_busy=lambda: True)     # hold the queue
    try:
        assert builder.seed(root, [(src1, "sha-1"), (src2, "sha-2")]) == 2
        # The thread may have popped job 1 and parked at the busy gate;
        # job 2 cannot have been popped — re-seeding it dedupes.
        assert builder.seed(root, [(src2, "sha-2")]) == 0
    finally:
        builder.stop()


def test_builder_cross_root_seed_drops_previous_queue(tmp_path):
    root_a = tmp_path / "event-a"
    root_b = tmp_path / "event-b"
    pairs_a = [(_make_jpeg(tmp_path / "src" / f"a{i}.jpg", 500, 250),
                f"sha-a{i}") for i in range(4)]
    pairs_b = [(_make_jpeg(tmp_path / "src" / f"b{i}.jpg", 500, 250),
                f"sha-b{i}") for i in range(2)]
    builder = ProxyBuilder(is_busy=lambda: True)     # nothing executes
    try:
        builder.seed(root_a, pairs_a)
        builder.seed(root_b, pairs_b)
        # The cross-root seed replaced the queue (at most one in-flight
        # root-A job survives, held by the busy gate — never more).
        assert builder.pending_count() == len(pairs_b)
    finally:
        builder.stop()


# ── engine integration (offscreen) ───────────────────────────────────


@pytest.fixture
def cache(qapp):
    c = PhotoCache()
    yield c
    c.shutdown()


def _register(cache, root: Path, src: Path, sha: str) -> None:
    cache.set_event_context(root, {src: sha})


def _deliveries(cache):
    got = []
    cache.scaled_pixmap_ready.connect(
        lambda path, pm, native: got.append((Path(path), pm, native)))
    return got


def test_scaled_tier_serves_proxy_with_original_native_dims(
        qapp, cache, tmp_path):
    root = tmp_path / "event"
    src = _make_jpeg(tmp_path / "src" / "p.jpg", 3000, 1500)
    assert ensure_photo_proxy(root, src, "sha-p")
    # Repaint the proxy a distinct solid color (sidecar checks the
    # SOURCE stat, so it stays valid): a delivery in this color proves
    # the pixels came from the proxy file, not the original.
    _make_jpeg(proxy_path(root, "sha-p"), 2560, 1280, color=(200, 30, 30))
    got = _deliveries(cache)
    _register(cache, root, src, "sha-p")
    cache.request_scaled_pixmap(src, QSize(1024, 1024))
    assert _spin_until(qapp, lambda: len(got) >= 1)
    path, pm, native = got[0]
    assert path == src                       # keyed by the ORIGINAL path
    assert native == QSize(3000, 1500)       # original dims, not 2560×1280
    color = pm.toImage().pixelColor(5, 5)
    assert color.red() > 150 and color.blue() < 90   # the repaint color


def test_write_on_decode_persists_proxy_grade_decode(qapp, cache, tmp_path):
    root = tmp_path / "event"
    src = _make_jpeg(tmp_path / "src" / "p.jpg", 3000, 1500)
    got = _deliveries(cache)
    # Isolate write-on-decode: the context seed would ALSO build this
    # proxy via the background builder and mask the pin.
    cache._proxy_builder.stop()
    _register(cache, root, src, "sha-p")
    cache.request_scaled_pixmap(src, QSize(2560, 2560))
    assert _spin_until(qapp, lambda: len(got) >= 1)
    assert got[0][2] == QSize(3000, 1500)
    assert _spin_until(
        qapp, lambda: resolve_proxy(root, "sha-p", src) is not None)
    hit = resolve_proxy(root, "sha-p", src)
    assert (hit.native_w, hit.native_h) == (3000, 1500)
    assert max(_proxy_dims(root, "sha-p")) >= PROXY_MAX_EDGE - 2


def test_small_target_decode_does_not_persist(qapp, cache, tmp_path):
    root = tmp_path / "event"
    src = _make_jpeg(tmp_path / "src" / "p.jpg", 3000, 1500)
    got = _deliveries(cache)
    # The builder would legitimately build this proxy from the context
    # seed — stop it so only write-on-decode could (wrongly) persist.
    cache._proxy_builder.stop()
    _register(cache, root, src, "sha-p")
    cache.request_scaled_pixmap(src, QSize(1024, 1024))
    assert _spin_until(qapp, lambda: len(got) >= 1)
    # Negative check: give the (wrong) persist a moment to happen.
    deadline = time.monotonic() + 0.6
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    assert resolve_proxy(root, "sha-p", src) is None


def test_corrupt_proxy_self_heals_from_original(qapp, cache, tmp_path):
    root = tmp_path / "event"
    src = _make_jpeg(tmp_path / "src" / "p.jpg", 3000, 1500)
    assert ensure_photo_proxy(root, src, "sha-p")
    proxy_path(root, "sha-p").write_bytes(b"not a jpeg")
    got = _deliveries(cache)
    _register(cache, root, src, "sha-p")
    cache.request_scaled_pixmap(src, QSize(2560, 2560))
    assert _spin_until(qapp, lambda: len(got) >= 1)
    path, pm, native = got[0]
    assert not pm.isNull()                   # served from the original
    assert native == QSize(3000, 1500)
    # The pair was dropped and write-on-decode rebuilt a VALID one.
    assert _spin_until(qapp, lambda: (
        resolve_proxy(root, "sha-p", src) is not None
        and _proxy_dims(root, "sha-p")[0] > 0))


def test_set_event_context_alone_builds_proxies(qapp, cache, tmp_path):
    root = tmp_path / "event"
    src = _make_jpeg(tmp_path / "src" / "p.jpg", 1200, 600)
    _register(cache, root, src, "sha-p")     # no scaled request at all
    assert _spin_until(
        qapp, lambda: resolve_proxy(root, "sha-p", src) is not None)
    hit = resolve_proxy(root, "sha-p", src)
    assert (hit.native_w, hit.native_h) == (1200, 600)


def test_seed_proxies_public_api_builds(qapp, cache, tmp_path):
    root = tmp_path / "event"
    pairs = [(_make_jpeg(tmp_path / "src" / f"p{i}.jpg", 700, 350),
              f"sha-{i}") for i in range(2)]
    assert cache.seed_proxies(root, pairs) == 2
    assert _spin_until(qapp, lambda: all(
        resolve_proxy(root, sha, src) is not None for src, sha in pairs))


def test_native_tier_never_reads_proxies(qapp, cache, tmp_path):
    """Compare surfaces ride request_pixmap — spec/63 §7 defers them
    OFF proxies. A poisoned proxy must not leak into that tier."""
    root = tmp_path / "event"
    src = _make_jpeg(tmp_path / "src" / "p.jpg", 1000, 500, color=(20, 60, 120))
    assert ensure_photo_proxy(root, src, "sha-p")
    _make_jpeg(proxy_path(root, "sha-p"), 1000, 500, color=(220, 20, 20))
    _register(cache, root, src, "sha-p")
    got = []
    cache.pixmap_ready.connect(lambda path, pm: got.append((path, pm)))
    cache.request_pixmap(src, QSize(1000, 500))
    assert _spin_until(qapp, lambda: len(got) >= 1)
    color = got[0][1].toImage().pixelColor(5, 5)
    assert color.blue() > 80 and color.red() < 80    # the ORIGINAL's color
