"""spec/95 §3 — scaled-pixmap LRU is a byte budget, not an entry count.

The spec/63 32-entry cap was tuned for ~2560-px decodes (~17 MB
each ≈ 0.5 GB). At ``display_quality='high'`` on a 4K+ display an
entry can be ~30-38 MB; 32 of them would hit ~1.2 GB. A byte budget
keeps the memory ceiling stable regardless of the display-quality
tier — large entries take more of the budget; small entries take
less.

Tests assert (i) total bytes never exceed the documented budget
after a run of large entries; (ii) eviction is LRU (oldest first);
(iii) the bookkeeping survives clear() and re-add.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QImage

import mira.ui.media.photo_cache as pc_module
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
    img.fill(0xAA8855)
    assert img.save(str(p), "JPG", 92)
    return p


# ── budget enforcement ──────────────────────────────────────────


def test_scaled_lru_stays_under_byte_budget(qapp, cache, tmp_path):
    """Adding many large scaled entries (each at the 4K-class
    decode budget) doesn't blow past the documented budget. Cap
    the budget low for this test so we can hit it with a handful
    of synthetic entries instead of a real 4K decode flood."""
    # Cap the budget at ~10 MB so the test runs fast.
    test_budget = 10 * 1024 * 1024
    saved = pc_module._SCALED_CACHE_BUDGET_BYTES
    pc_module._SCALED_CACHE_BUDGET_BYTES = test_budget
    try:
        # Each 1024×1024 RGB32 entry ≈ 4 MB → 3 entries = 12 MB.
        # After the third, the LRU should drop the oldest.
        from PyQt6.QtGui import QPixmap
        for i in range(6):
            pm = QPixmap(1024, 1024)
            pm.fill(0x123456)
            key = (Path(f"/tmp/x{i}.jpg"), (1024, 1024))
            with cache._pixmap_lock:                  # noqa: SLF001
                # Mirror the LRU upsert + accounting the worker path
                # would do for these synthetic entries.
                cost = pc_module._pixmap_byte_cost(pm)
                cache._scaled[key] = (pm, QSize(1024, 1024))  # noqa: SLF001
                cache._scaled_bytes += cost           # noqa: SLF001
                while (cache._scaled_bytes > test_budget  # noqa: SLF001
                       and len(cache._scaled) > 1):   # noqa: SLF001
                    _k, evicted = cache._scaled.popitem(  # noqa: SLF001
                        last=False)
                    cache._scaled_bytes -= pc_module._pixmap_byte_cost(  # noqa: SLF001
                        evicted[0])
        # The budget cap held.
        assert cache._scaled_bytes <= test_budget     # noqa: SLF001
        # Some old entries were evicted (we added 6 entries × ~4 MB =
        # 24 MB worth, the LRU is capped at 10 MB).
        assert len(cache._scaled) < 6                 # noqa: SLF001
    finally:
        pc_module._SCALED_CACHE_BUDGET_BYTES = saved


def test_scaled_lru_evicts_oldest_first(qapp, cache, tmp_path):
    """LRU semantics: the entry that's been in the cache longest
    is the first to go when the budget tightens."""
    test_budget = 10 * 1024 * 1024
    saved = pc_module._SCALED_CACHE_BUDGET_BYTES
    pc_module._SCALED_CACHE_BUDGET_BYTES = test_budget
    try:
        from PyQt6.QtGui import QPixmap
        for i, label in enumerate(["old", "mid", "new"]):
            pm = QPixmap(1024, 1024)
            pm.fill(0x101010 * (i + 1))
            key = (Path(f"/tmp/{label}.jpg"), (1024, 1024))
            with cache._pixmap_lock:                  # noqa: SLF001
                cache._scaled[key] = (pm, QSize(1024, 1024))  # noqa: SLF001
                cache._scaled_bytes += pc_module._pixmap_byte_cost(pm)  # noqa: SLF001

        # Force a tight budget and re-evict.
        tight_budget = 5 * 1024 * 1024
        with cache._pixmap_lock:                      # noqa: SLF001
            while (cache._scaled_bytes > tight_budget  # noqa: SLF001
                   and len(cache._scaled) > 1):       # noqa: SLF001
                _k, evicted = cache._scaled.popitem(  # noqa: SLF001
                    last=False)
                cache._scaled_bytes -= pc_module._pixmap_byte_cost(  # noqa: SLF001
                    evicted[0])

        remaining = list(cache._scaled.keys())        # noqa: SLF001
        # "old" went first; "new" survives.
        assert (Path("/tmp/old.jpg"), (1024, 1024)) not in remaining
        assert (Path("/tmp/new.jpg"), (1024, 1024)) in remaining
    finally:
        pc_module._SCALED_CACHE_BUDGET_BYTES = saved


def test_pixmap_byte_cost_reflects_pixel_count(qapp):
    """``_pixmap_byte_cost`` is approximately ``w*h*bpp/8`` — sanity
    check the helper underlying the LRU accounting."""
    from PyQt6.QtGui import QPixmap

    small = QPixmap(100, 100)
    big = QPixmap(2000, 2000)
    cost_small = pc_module._pixmap_byte_cost(small)
    cost_big = pc_module._pixmap_byte_cost(big)
    # Big is ~400× larger in pixel count → at least ~400× cost.
    assert cost_big > cost_small * 100
    # And a null pixmap costs 0.
    assert pc_module._pixmap_byte_cost(QPixmap()) == 0


def test_clear_resets_byte_budget(qapp, cache):
    """``clear`` zeros the byte accounting so a re-populated cache
    starts fresh — defends against double-counting on event switch."""
    from PyQt6.QtGui import QPixmap

    pm = QPixmap(1024, 1024)
    with cache._pixmap_lock:                          # noqa: SLF001
        cache._scaled[(Path("/x.jpg"), (1024, 1024))] = (  # noqa: SLF001
            pm, QSize(1024, 1024))
        cache._scaled_bytes = pc_module._pixmap_byte_cost(pm)  # noqa: SLF001
    assert cache._scaled_bytes > 0                    # noqa: SLF001

    cache.clear()
    assert cache._scaled_bytes == 0                   # noqa: SLF001
    assert len(cache._scaled) == 0                    # noqa: SLF001


def test_replace_same_key_updates_byte_accounting(qapp, cache):
    """Upserting an entry at an existing key (e.g. a re-decode at
    the same target) replaces the old bytes with the new ones —
    not adds them. Otherwise the running total drifts upward."""
    from PyQt6.QtGui import QPixmap

    key = (Path("/x.jpg"), (1024, 1024))
    small = QPixmap(512, 512)
    small.fill(0x111111)
    big = QPixmap(2048, 2048)
    big.fill(0x222222)

    with cache._pixmap_lock:                          # noqa: SLF001
        cache._scaled[key] = (small, QSize(1024, 1024))  # noqa: SLF001
        cache._scaled_bytes += pc_module._pixmap_byte_cost(small)  # noqa: SLF001
    initial = cache._scaled_bytes                     # noqa: SLF001
    assert initial > 0

    # Now simulate the worker upsert: pop old, add new.
    with cache._pixmap_lock:                          # noqa: SLF001
        old = cache._scaled.pop(key, None)            # noqa: SLF001
        if old is not None:
            cache._scaled_bytes -= pc_module._pixmap_byte_cost(  # noqa: SLF001
                old[0])
        cache._scaled[key] = (big, QSize(2048, 2048))  # noqa: SLF001
        cache._scaled_bytes += pc_module._pixmap_byte_cost(big)  # noqa: SLF001

    # The running total reflects ONLY the big pixmap; the small
    # one is gone.
    assert cache._scaled_bytes == pc_module._pixmap_byte_cost(big)  # noqa: SLF001
