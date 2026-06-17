"""Periodic-while-open snapshot timer (spec/82 §A.1).

A long working session (large ingest, deep Pick pass, multi-hour
Edit) can sit between two clean closes. Battery dies, OS crashes,
power blip — without periodic snapshots the rollback line is hours
stale. This module is the crash insurance: a UI-layer ``QTimer``
that fires every N minutes and asks
:meth:`Gateway.snapshot_event` to take a ``reason="periodic"``
snapshot **only if the db changed** since the last snapshot.

Two design points called out by the brief:

* **Off the GUI thread.** The snapshot itself goes through
  ``QThreadPool`` as a ``QRunnable`` so a large event.db can't
  freeze the UI for the duration of the backup. WAL allows the
  online backup API to read concurrent with writes on the live
  connection, so the worker is safe to run while the user keeps
  clicking.
* **No-op when clean or when the interval is 0.** The "dirty
  since last snapshot" check is filesystem-mtime-based: the live
  event.db's mtime vs the newest snapshot file's mtime. Cheap, no
  SQLite open, no false positives from WAL flushes.

The cadence (``backup_periodic_minutes``) is hardcoded here at
spec/82's suggested 15 minutes; slice 8 reads it from the
Backups settings tab instead. ``interval_minutes=0`` disables the
timer entirely (milestone snapshots keep running).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QObject, QThreadPool, QTimer, QRunnable, pyqtSlot

from core import db_backup
from core.db_backup import REASON_PERIODIC

log = logging.getLogger(__name__)


DEFAULT_INTERVAL_MINUTES = 15


def _is_dirty_since_last_snapshot(
    db_path: Path,
    backups_dir: Path,
) -> bool:
    """Pure-logic dirty check: True iff the live db's filesystem
    mtime is **strictly newer** than the newest snapshot's.

    Newest-of-any-class is correct here: a milestone snapshot taken
    by close-if-dirty / per-day-add captures the current state just
    as much as a periodic one, so the periodic timer should skip
    until the next change. With no snapshots yet, returns True so
    the first tick after the app opens lays down a baseline.

    Missing db → False (nothing to snapshot); missing backups dir is
    treated as "no snapshots yet" → True.
    """
    if not db_path.exists():
        return False
    try:
        db_mtime = db_path.stat().st_mtime
    except OSError:
        return False
    snaps = db_backup.list_snapshots(backups_dir)
    if not snaps:
        return True
    try:
        snap_mtime = snaps[0].db_path.stat().st_mtime
    except OSError:
        return True
    return db_mtime > snap_mtime


class _SnapshotJob(QRunnable):
    """The off-thread worker: re-checks dirty, then calls
    :meth:`Gateway.snapshot_event`. The result is logged on the
    pool's thread — the caller doesn't wait."""

    def __init__(
        self,
        gateway,
        event_id: str,
        db_path: Path,
        backups_dir: Path,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._gateway = gateway
        self._event_id = event_id
        self._db_path = db_path
        self._backups_dir = backups_dir

    def run(self) -> None:                                  # QRunnable hook
        # Re-check dirty inside the worker — the UI thread might have
        # raced through several clean ticks before this job ran.
        if not _is_dirty_since_last_snapshot(
                self._db_path, self._backups_dir):
            return
        try:
            snap = self._gateway.snapshot_event(
                self._event_id, reason=REASON_PERIODIC)
        except Exception as exc:                            # noqa: BLE001
            log.warning(
                "spec/82 §A.1 periodic snapshot FAILED for %s: %s",
                self._event_id, exc)
            return
        if snap is not None:
            log.info(
                "spec/82 §A.1 periodic snapshot saved at %s", snap)


class PeriodicSnapshotter(QObject):
    """Owns the ``QTimer`` + the off-thread worker dispatch.

    Construct with the gateway + a callable that returns the
    currently-open event id (the MainWindow's
    ``_current_event_id`` pointer, accessed via lambda so the
    snapshotter doesn't import the MainWindow). Call :meth:`start`
    once after the app is up; :meth:`stop` on shutdown.

    ``interval_minutes=0`` short-circuits :meth:`start` — milestone
    snapshots keep firing through their own triggers, periodic
    insurance is off.
    """

    def __init__(
        self,
        gateway,
        current_event_id: Callable[[], Optional[str]],
        *,
        interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._gateway = gateway
        self._current_event_id = current_event_id
        self._interval_minutes = int(interval_minutes)
        self._timer: Optional[QTimer] = None

    def start(self) -> None:
        if self._interval_minutes <= 0:
            log.info(
                "spec/82 §A.1: periodic snapshots disabled "
                "(interval_minutes=%d)", self._interval_minutes)
            return
        if self._timer is not None:
            return                                          # already started
        self._timer = QTimer(self)
        self._timer.setInterval(self._interval_minutes * 60 * 1000)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()
        log.info(
            "spec/82 §A.1: periodic snapshot timer started (every %d min)",
            self._interval_minutes)

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    @pyqtSlot()
    def _on_tick(self) -> None:
        """UI-thread entry. Resolves the current event + dispatches
        the off-thread snapshot job. The dirty check is cheap
        (filesystem mtime) but we still want it off the GUI thread
        because the snapshot itself can spike on a large db."""
        event_id = self._current_event_id()
        if not event_id:
            return
        entry = self._gateway.index.get(event_id)
        if entry is None:
            return
        root = self._gateway.index.resolve_root(
            entry, self._gateway.photos_base_path())
        if root is None:
            return
        db_path = root / "event.db"
        backups_dir = self._gateway.event_backups_dir(event_id)
        if backups_dir is None:
            return
        # Cheap pre-check on the UI thread — skip the pool dispatch
        # when the dir is unmistakably clean. The worker re-checks
        # anyway so this is purely a hot-path optimisation.
        if not _is_dirty_since_last_snapshot(db_path, backups_dir):
            return
        job = _SnapshotJob(self._gateway, event_id, db_path, backups_dir)
        QThreadPool.globalInstance().start(job)


__all__ = [
    "DEFAULT_INTERVAL_MINUTES",
    "PeriodicSnapshotter",
    "_is_dirty_since_last_snapshot",
]
