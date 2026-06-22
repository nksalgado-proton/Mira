"""spec/63 slice 0 — PhotoCache v2: the queue cure + the scaled tier.

First tests this module has ever had. The decode worker is a real
QThread; tests make the races deterministic by replacing the module's
``load_pixmap`` with a gated fake (blocks until the test opens the
gate, records every call), then spinning the event loop until the
expected cross-thread signals land.

Covers the two slice-0 behaviors:
* ANY stale queued job drops on a newer navigation generation — a
  held arrow key decodes only the landed photo, never the backlog
  (the audit's Picker blur-backlog).
* ``request_scaled_pixmap`` decodes AT the display target and
  delivers the TRUE native size probed from the header (box-zoom 1:1
  honesty — the recorded reason native-only decode existed).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QImage, QPixmap

import mira.ui.media.photo_cache as pc_module
from mira.ui.media.image_loader import native_image_size
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


class _GatedLoader:
    """Stand-in for ``load_qimage``: blocks until the gate opens,
    records every (path, target) call. Returns a QImage — the worker
    decodes to QImage (thread-safe) and the cache converts to QPixmap
    on the GUI thread (2026-06-12 off-thread-QPixmap fix)."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.entered = threading.Event()
        self.calls: list[tuple[Path, QSize | None]] = []
        self._lock = threading.Lock()

    def __call__(self, path: Path, target: QSize | None = None) -> QImage:
        with self._lock:
            self.calls.append((Path(path), target))
        self.entered.set()
        assert self.gate.wait(5.0), "test gate never opened"
        return QImage(8, 8, QImage.Format.Format_RGB32)

    def called_paths(self) -> list[Path]:
        with self._lock:
            return [p for p, _ in self.calls]


def test_held_arrow_decodes_only_the_landed_photo(qapp, cache, monkeypatch):
    """Three rapid navigations: the in-flight decode finishes, the
    flown-past one DROPS, the landed one decodes. (Old behavior:
    priority 0 was never dropped — all three decoded, FIFO, landed
    photo last.)"""
    loader = _GatedLoader()
    monkeypatch.setattr(pc_module, "load_qimage", loader)
    a, b, c = Path("C:/x/a.jpg"), Path("C:/x/b.jpg"), Path("C:/x/c.jpg")
    ready: list[Path] = []
    cache.pixmap_ready.connect(lambda p, pm: ready.append(p))

    cache.request_pixmap(a, QSize(100, 100))          # nav 1
    assert loader.entered.wait(5.0)                   # a is in-flight
    cache.request_pixmap(b, QSize(100, 100))          # nav 2 (flown past)
    cache.request_pixmap(c, QSize(100, 100))          # nav 3 (landed)
    loader.gate.set()

    assert _spin_until(qapp, lambda: c in ready)
    paths = loader.called_paths()
    assert b not in paths, "flown-past navigation target must drop"
    assert paths.count(c) == 1


def test_pending_dedupe_decodes_once_per_generation(qapp, cache, monkeypatch):
    """Re-requesting the same path at the same generation (e.g. a
    settle predecode firing twice) queues ONE decode."""
    loader = _GatedLoader()
    monkeypatch.setattr(pc_module, "load_qimage", loader)
    a, n = Path("C:/x/a.jpg"), Path("C:/x/n.jpg")
    ready: list[Path] = []
    cache.pixmap_ready.connect(lambda p, pm: ready.append(p))

    cache.request_pixmap(a, QSize(100, 100))          # current target
    assert loader.entered.wait(5.0)
    cache.request_pixmap(n, QSize(100, 100), priority=1)
    cache.request_pixmap(n, QSize(100, 100), priority=1)   # duplicate
    loader.gate.set()

    assert _spin_until(qapp, lambda: n in ready)
    assert loader.called_paths().count(n) == 1


def test_scaled_request_delivers_display_pixmap_with_true_native_size(
    qapp, cache, tmp_path,
):
    """Real loader end-to-end: decode AT the target, native size from
    the header; second request is a cache hit (no second decode)."""
    src = tmp_path / "wide.jpg"
    img = QImage(800, 400, QImage.Format.Format_RGB32)
    img.fill(0xFF8040)
    assert img.save(str(src), "JPG", 90)

    got: list[tuple[Path, QPixmap, QSize]] = []
    cache.scaled_pixmap_ready.connect(
        lambda p, pm, nat: got.append((p, pm, nat)))
    cache.request_scaled_pixmap(src, QSize(200, 200))
    assert _spin_until(qapp, lambda: len(got) >= 1)

    p, pm, native = got[0]
    assert p == src
    assert native == QSize(800, 400), "true dims, not the scaled dims"
    assert pm.width() <= 200 and pm.height() <= 200
    assert pm.width() < 800, "must be decoded at the target, not native"

    hit = cache.get_scaled_pixmap_if_cached(src, QSize(200, 200))
    assert hit is not None and hit[1] == QSize(800, 400)
    cache.request_scaled_pixmap(src, QSize(200, 200))   # synchronous re-emit
    assert len(got) >= 2 and got[-1][2] == QSize(800, 400)


def test_native_image_size_reads_header_only(tmp_path):
    src = tmp_path / "tiny.jpg"
    img = QImage(123, 45, QImage.Format.Format_RGB32)
    img.fill(0x202020)
    assert img.save(str(src), "JPG", 90)
    assert native_image_size(src) == QSize(123, 45)
    assert native_image_size(tmp_path / "missing.jpg") is None


# --------------------------------------------------------------------------- #
# spec/96 §1 — proxy_pending_count delegates to the builder
# --------------------------------------------------------------------------- #


def test_proxy_pending_count_delegates_to_builder(qapp, cache):
    """The activity line polls ``cache.proxy_pending_count()`` to
    decide whether to show the "Creating previews …" message. It
    must read straight from the builder's thread-safe count."""

    class _FakeBuilder:
        def __init__(self) -> None:
            self.calls = 0
            self._value = 0

        def pending_count(self) -> int:
            self.calls += 1
            return self._value

        def stop(self, *_args, **_kwargs) -> None:    # teardown path
            pass

    fake = _FakeBuilder()
    cache._proxy_builder = fake                       # noqa: SLF001
    assert cache.proxy_pending_count() == 0
    fake._value = 17                                  # noqa: SLF001
    assert cache.proxy_pending_count() == 17
    assert fake.calls == 2


def test_proxy_pending_count_swallows_builder_failure(qapp, cache):
    """The line polls every ~400 ms — a transient failure in the
    builder must never crash the GUI thread; the accessor returns
    zero so the line falls back to "Ready"."""

    class _BoomBuilder:
        def pending_count(self) -> int:
            raise RuntimeError("simulated builder teardown")

        def stop(self, *_args, **_kwargs) -> None:    # teardown path
            pass

    cache._proxy_builder = _BoomBuilder()             # noqa: SLF001
    assert cache.proxy_pending_count() == 0
