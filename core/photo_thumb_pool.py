"""Background worker pool that materialises photo thumbnails to the
on-disk cache (:mod:`core.photo_thumb_cache`).

Captured photos are immutable post-ingest (charter §3): the thumbnail
for a given ``item.sha256`` only needs to be written **once**, ever.
This pool is the writer:

* **Ingest** feeds each captured photo as soon as its bytes land in
  ``00 - Captured/``. Ingest itself doesn't wait — the pool keeps
  running in the background; by the time the user enters Cull most
  thumbnails are already cached.
* **Materializer** feeds each snapshot at Cull-exit so a fresh
  snapshot has its thumb ready by the time Select opens.

Pure :mod:`threading` — NO :class:`PyQt6.QtCore.QThread`. Qt-free so
the headless ingest engine + the pure-logic materialiser can use it
without bringing in a UI dependency. Daemon threads die with the
interpreter so an un-stopped pool never blocks shutdown or causes a
Qt-thread destruction crash.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.photo_thumb_cache import ensure_photo_thumb, photo_thumb_path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _PhotoThumbJob:
    """One photo to pre-warm. ``source_path`` is absolute; ``sha256`` is
    the content-address key for the cache (matches ``item.sha256``)."""

    event_root: Path
    source_path: Path
    sha256: str


def _worker_loop(q: "queue.Queue") -> None:
    """Worker entry point — runs in its own daemon thread.

    Pulls jobs from the queue and calls :func:`ensure_photo_thumb` on
    each. Idempotent and atomic at the file-system level, so concurrent
    workers don't conflict. Exits when a ``None`` sentinel is received.
    """
    while True:
        try:
            job = q.get()
        except Exception:  # noqa: BLE001 — defensive (interpreter shutdown)
            return
        if job is None:
            try:
                q.task_done()
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            ensure_photo_thumb(job.event_root, job.source_path, job.sha256)
        except Exception:  # noqa: BLE001 — never let the loop die
            log.debug(
                "photo thumb pool: %s — %s",
                job.source_path, "render failed", exc_info=True)
        try:
            q.task_done()
        except Exception:  # noqa: BLE001
            pass


class PhotoThumbPool:
    """A small pool of daemon threads pre-warming the photo thumb cache.

    Lifecycle:
      1. Construct (spawns ``n_workers`` daemon threads).
      2. :meth:`enqueue` per photo as bytes land.
      3. Optional :meth:`stop` for explicit teardown; otherwise daemon
         threads die with the interpreter.

    Disk I/O scales cleanly to ~4 concurrent reads on a typical SSD
    without thrashing; the default ``n_workers`` reflects that.
    """

    def __init__(self, n_workers: Optional[int] = None) -> None:
        self._q: "queue.Queue" = queue.Queue()
        self._seen: set[str] = set()
        self._lock = threading.Lock()
        self._stopped = False
        if n_workers is None:
            n_workers = max(1, min(4, (os.cpu_count() or 2) - 1))
        self._n_workers = int(n_workers)
        self._threads: list = []
        for i in range(self._n_workers):
            t = threading.Thread(
                target=_worker_loop, args=(self._q,),
                name=f"photo-thumb-pool-{i}", daemon=True,
            )
            t.start()
            self._threads.append(t)

    # ── enqueue ──────────────────────────────────────────────────────

    def enqueue(
        self, event_root: Path, source_path: Path, sha256: str,
    ) -> bool:
        """Queue one photo for thumb generation. Returns ``True`` if a
        new job was added, ``False`` if the sha was already seen this
        session or the cache file already exists.

        Idempotent: re-calling with the same sha is cheap (dict lookup +
        single ``Path.exists`` syscall). Thread-safe."""
        with self._lock:
            if sha256 in self._seen:
                return False
            self._seen.add(sha256)
        if photo_thumb_path(event_root, sha256).exists():
            return False
        self._q.put(_PhotoThumbJob(
            event_root=Path(event_root),
            source_path=Path(source_path),
            sha256=sha256,
        ))
        return True

    def pending(self) -> int:
        """Approximate queued-but-not-yet-processed count. ``queue.qsize``
        is approximate by design (multi-producer/consumer) — fine for
        diagnostics."""
        try:
            return self._q.qsize()
        except NotImplementedError:
            return 0

    # ── stop ─────────────────────────────────────────────────────────

    def stop(self, *, wait: bool = True, timeout_per_thread: float = 5.0) -> None:
        """Stop the pool. Drains the queue first so workers exit ASAP
        (at most one in-flight item per worker — a few hundred ms),
        then pushes a ``None`` sentinel per worker. Idempotent.

        Set ``wait=False`` to skip the join (workers will exit at their
        own pace; the daemon flag ensures interpreter shutdown is clean
        regardless)."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
        # Empty the queue so workers don't churn through pending jobs
        # after the caller has decided to stop.
        try:
            while True:
                self._q.get_nowait()
                try:
                    self._q.task_done()
                except ValueError:
                    break
        except queue.Empty:
            pass
        # One sentinel per worker.
        for _ in self._threads:
            self._q.put(None)
        if wait:
            for t in self._threads:
                t.join(timeout=timeout_per_thread)
