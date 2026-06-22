"""spec/95 §B — worker dispatches scaled jobs by target size.

* ``target ≤ PROXY_MAX_EDGE`` → the proxy fast path (today's
  behavior, the laptop's small targets always stay here).
* ``target > PROXY_MAX_EDGE`` → decode the ORIGINAL down to target,
  bypassing the proxy. The on-disk proxy is too small to satisfy
  this target; Qt's upscale from 2560 to 4K is the "soft on a 4K
  monitor" path the spec removes.

Plus the persist guard: an upgrade-target decode is NOT written to
disk as a proxy (the proxy on-disk size stays at 2560).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional, Tuple

import pytest
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QImage

import mira.ui.media.photo_cache as pc_module
from core import photo_proxy_cache
from mira.ui.media.photo_cache import PhotoCache


def _spin_until(qapp, predicate, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def cache(qapp):
    c = PhotoCache()
    yield c
    c.shutdown()


def _make_jpeg(tmp_path: Path, name: str, w: int, h: int) -> Path:
    p = tmp_path / name
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(0x336699)
    assert img.save(str(p), "JPG", 90)
    return p


# ── proxy path: target ≤ PROXY_MAX_EDGE ─────────────────────────


def test_worker_serves_proxy_when_target_at_or_below_edge(
    qapp, cache, tmp_path, monkeypatch,
):
    """spec/95 §B — when ``max(target) ≤ PROXY_MAX_EDGE`` (2560),
    the worker serves the on-disk proxy (today's fast path), NOT
    the original. The original-decode call site is exercised
    only when the proxy doesn't satisfy the request."""
    # A real source on disk so the worker can resolve native dims;
    # we don't read the original because the proxy resolver returns
    # a valid hit, so the resolver is the load_qimage target.
    original = _make_jpeg(tmp_path, "orig.jpg", 4000, 3000)
    proxy_file = tmp_path / "proxy.jpg"
    proxy_img = QImage(800, 600, QImage.Format.Format_RGB32)
    proxy_img.fill(0x224488)
    assert proxy_img.save(str(proxy_file), "JPG", 85)

    served_from: list[Path] = []
    real_loader = pc_module.load_qimage

    def spy_loader(p: Path, size: Optional[QSize] = None) -> QImage:
        served_from.append(Path(p))
        return real_loader(p, size)

    monkeypatch.setattr(pc_module, "load_qimage", spy_loader)
    cache._worker._resolve_proxy = (                  # noqa: SLF001
        lambda _p: (proxy_file, QSize(4000, 3000)))

    got: list[QSize] = []
    cache.scaled_pixmap_ready.connect(
        lambda p, pm, nat: got.append(QSize(pm.width(), pm.height())))
    # target 800 — well under PROXY_MAX_EDGE (2560).
    cache.request_scaled_pixmap(original, QSize(800, 600))
    assert _spin_until(qapp, lambda: len(got) >= 1)

    # The proxy file was opened, not the original.
    assert proxy_file in served_from
    assert original not in served_from


# ── upgrade path: target > PROXY_MAX_EDGE ──────────────────────


def test_worker_decodes_original_when_target_exceeds_edge(
    qapp, cache, tmp_path, monkeypatch,
):
    """spec/95 §B — when ``max(target) > PROXY_MAX_EDGE``, the
    worker SKIPS the proxy lookup and decodes the ORIGINAL down to
    target. The proxy can't satisfy a 4K-class target."""
    original = _make_jpeg(tmp_path, "big.jpg", 6000, 4000)
    proxy_file = tmp_path / "proxy.jpg"
    proxy_img = QImage(800, 600, QImage.Format.Format_RGB32)
    proxy_img.fill(0x224488)
    assert proxy_img.save(str(proxy_file), "JPG", 85)

    served_from: list[Path] = []
    real_loader = pc_module.load_qimage

    def spy_loader(p: Path, size: Optional[QSize] = None) -> QImage:
        served_from.append(Path(p))
        return real_loader(p, size)

    monkeypatch.setattr(pc_module, "load_qimage", spy_loader)
    # A proxy resolver is wired, but the worker must NOT use it for
    # this target.
    cache._worker._resolve_proxy = (                  # noqa: SLF001
        lambda _p: (proxy_file, QSize(6000, 4000)))

    got: list[Tuple[int, int]] = []
    cache.scaled_pixmap_ready.connect(
        lambda p, pm, nat: got.append((pm.width(), pm.height())))
    # target 4096 — well above PROXY_MAX_EDGE (2560).
    cache.request_scaled_pixmap(original, QSize(4096, 2730))
    assert _spin_until(qapp, lambda: len(got) >= 1)

    # The ORIGINAL was opened; the proxy was not.
    assert original in served_from
    assert proxy_file not in served_from
    # And the served pixmap's long edge tracks the target, not the
    # 2560 proxy — it's >2560 (decoded down to 4096).
    long_edge = max(got[0][0], got[0][1])
    assert long_edge > photo_proxy_cache.PROXY_MAX_EDGE


def test_worker_does_not_persist_upgrade_target_as_proxy(
    qapp, cache, tmp_path, monkeypatch,
):
    """spec/95 §B — the §C "on-disk proxy size unchanged" contract:
    when the worker decodes at an upgrade target (> PROXY_MAX_EDGE),
    the resulting bitmap is NOT written as the proxy. The on-disk
    proxy stays at the canonical 2560 long edge (the background
    builder fills it via its own path)."""
    original = _make_jpeg(tmp_path, "big.jpg", 6000, 4000)
    persist_calls: list[Tuple[Path, int]] = []

    def fake_persist(path, image, native, target):
        persist_calls.append((Path(path), max(target.width(), target.height())))

    cache._worker._persist_decode = fake_persist     # noqa: SLF001
    # No proxy resolver — the worker takes the original-decode path.
    cache._worker._resolve_proxy = lambda _p: None   # noqa: SLF001

    got: list[QSize] = []
    cache.scaled_pixmap_ready.connect(
        lambda p, pm, nat: got.append(QSize(pm.width(), pm.height())))
    cache.request_scaled_pixmap(original, QSize(4096, 2730))
    assert _spin_until(qapp, lambda: len(got) >= 1)

    # The persist hook was either skipped entirely or called only
    # for sub-proxy-edge targets. Spec/95 §B forbids writing the
    # upgrade decode to disk.
    for _path, target_long in persist_calls:
        assert target_long <= photo_proxy_cache.PROXY_MAX_EDGE, (
            f"upgrade-target decode persisted with long={target_long} "
            f"px — would inflate the on-disk proxy past the spec/63 "
            f"§5 cap (2560).")


def test_worker_still_persists_proxy_grade_decode(
    qapp, cache, tmp_path, monkeypatch,
):
    """Sanity for the §B persist guard: a sub-proxy-edge decode
    (the original miss falls through to load_qimage at proxy edge)
    still flows into the persist hook so the proxy self-heals."""
    original = _make_jpeg(tmp_path, "small.jpg", 3000, 2000)
    persist_calls: list[int] = []

    def fake_persist(path, image, native, target):
        persist_calls.append(max(target.width(), target.height()))

    cache._worker._persist_decode = fake_persist     # noqa: SLF001
    cache._worker._resolve_proxy = lambda _p: None   # noqa: SLF001

    got: list[QSize] = []
    cache.scaled_pixmap_ready.connect(
        lambda p, pm, nat: got.append(QSize(pm.width(), pm.height())))
    cache.request_scaled_pixmap(original, QSize(2048, 1536))
    assert _spin_until(qapp, lambda: len(got) >= 1)

    assert persist_calls, "sub-edge decode should still persist"
    assert max(persist_calls) <= photo_proxy_cache.PROXY_MAX_EDGE
