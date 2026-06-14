"""Tests for ``core.photo_thumb_pool.PhotoThumbPool``.

The pool is the Qt-free worker that materialises photo thumbnails to
disk on background daemon threads. It's fed by ingest (per captured
photo as bytes land) and by the legacy regenerate_thumbs CLI.

Covered:
* ``enqueue`` adds a unique sha exactly once (dedup via the ``_seen``
  set) and short-circuits if the cache file already exists.
* Workers drain the queue and call :func:`ensure_photo_thumb` per job.
* ``stop`` is idempotent and drains pending jobs so workers exit ASAP.
* Render failures don't kill the worker loop — the next job still runs.
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from core.photo_thumb_pool import PhotoThumbPool


def _wait_for(pred, *, timeout: float = 2.0, step: float = 0.01) -> bool:
    """Poll ``pred`` until True or ``timeout`` elapses. Returns the final value."""
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if pred():
            return True
        time.sleep(step)
    return pred()


# --------------------------------------------------------------------------- #
# Enqueue + dedup + cache-hit short-circuit
# --------------------------------------------------------------------------- #


def test_enqueue_returns_true_for_first_sha(tmp_path):
    """First sighting of a sha = job queued."""
    seen: list = []
    with patch(
        "core.photo_thumb_pool.ensure_photo_thumb",
        lambda root, src, sha: seen.append(sha),
    ):
        p = PhotoThumbPool(n_workers=1)
        try:
            assert p.enqueue(tmp_path, tmp_path / "a.jpg", "sha-a") is True
            assert _wait_for(lambda: seen == ["sha-a"])
        finally:
            p.stop()


def test_enqueue_dedups_same_sha(tmp_path):
    """Second enqueue of same sha = False, no extra job."""
    seen: list = []
    with patch(
        "core.photo_thumb_pool.ensure_photo_thumb",
        lambda root, src, sha: seen.append(sha),
    ):
        p = PhotoThumbPool(n_workers=1)
        try:
            assert p.enqueue(tmp_path, tmp_path / "a.jpg", "sha-x") is True
            assert p.enqueue(tmp_path, tmp_path / "a_dup.jpg", "sha-x") is False
            assert _wait_for(lambda: seen == ["sha-x"])
            # Give it a beat — a second job would land here if dedup failed.
            time.sleep(0.05)
            assert seen == ["sha-x"]
        finally:
            p.stop()


def test_enqueue_skips_when_cache_already_exists(tmp_path):
    """If the on-disk thumb is already there, the pool doesn't queue a
    no-op job — perfect for re-running on a fully-warm event."""
    from core.photo_thumb_cache import photo_thumb_path

    cached = photo_thumb_path(tmp_path, "cached")
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"prebaked")

    seen: list = []
    with patch(
        "core.photo_thumb_pool.ensure_photo_thumb",
        lambda root, src, sha: seen.append(sha),
    ):
        p = PhotoThumbPool(n_workers=1)
        try:
            assert p.enqueue(tmp_path, tmp_path / "src.jpg", "cached") is False
            # Now a fresh sha = True + worked.
            assert p.enqueue(tmp_path, tmp_path / "fresh.jpg", "fresh") is True
            assert _wait_for(lambda: seen == ["fresh"])
        finally:
            p.stop()


def test_workers_process_multiple_jobs(tmp_path):
    """A small batch — every job's sha lands in the worker side-effect."""
    seen: list = []
    with patch(
        "core.photo_thumb_pool.ensure_photo_thumb",
        lambda root, src, sha: seen.append(sha),
    ):
        p = PhotoThumbPool(n_workers=2)
        try:
            for i in range(8):
                p.enqueue(tmp_path, tmp_path / f"x{i}.jpg", f"sha-{i:02d}")
            assert _wait_for(lambda: len(seen) == 8, timeout=3.0)
            assert set(seen) == {f"sha-{i:02d}" for i in range(8)}
        finally:
            p.stop()


def test_worker_survives_render_exception(tmp_path):
    """If ensure_photo_thumb raises, the next job still gets run."""

    def explode_then_log(root, src, sha):
        if sha == "boom":
            raise RuntimeError("intentional")
        seen.append(sha)

    seen: list = []
    with patch("core.photo_thumb_pool.ensure_photo_thumb", explode_then_log):
        p = PhotoThumbPool(n_workers=1)
        try:
            p.enqueue(tmp_path, tmp_path / "a.jpg", "boom")
            p.enqueue(tmp_path, tmp_path / "b.jpg", "after-boom")
            assert _wait_for(lambda: seen == ["after-boom"], timeout=2.0)
        finally:
            p.stop()


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


def test_stop_is_idempotent():
    p = PhotoThumbPool(n_workers=1)
    p.stop()
    p.stop()
    # No assertion — must not raise.


def test_stop_drains_pending_jobs(tmp_path):
    """Stop empties the queue so workers exit ASAP instead of churning
    through every remaining pre-cache job."""

    def slow_ensure(root, src, sha):
        time.sleep(0.01)
        seen.append(sha)

    seen: list = []
    with patch("core.photo_thumb_pool.ensure_photo_thumb", slow_ensure):
        p = PhotoThumbPool(n_workers=1)
        for i in range(50):
            p.enqueue(tmp_path, tmp_path / f"x{i}.jpg", f"sha-{i:03d}")
        # Brief settle, then stop while many are still pending.
        time.sleep(0.03)
        p.stop()
        # Far fewer than 50 ran — drain short-circuited the rest.
        assert len(seen) < 50, f"expected partial drain, got {len(seen)}"


def test_stop_without_wait_returns_immediately(tmp_path):
    """``wait=False`` skips the join — used by ingest so engine.run_ingest()
    returns immediately while thumbs trail in background."""
    p = PhotoThumbPool(n_workers=2)
    start = time.perf_counter()
    p.stop(wait=False)
    elapsed = time.perf_counter() - start
    assert elapsed < 0.5, f"stop(wait=False) took {elapsed:.2f}s"
