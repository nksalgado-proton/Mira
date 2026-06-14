"""The app-level batch-export queue + its progress line (spec/59 §8).

A user can launch as many batch jobs as they like (day-scope,
event-scope); they QUEUE and run strictly ONE at a time, app-level —
the user keeps working anywhere in the app, dashboard included. The
one progress line lives directly below the menubar and shows the
running job (label · per-file progress · how many wait · Cancel);
hidden when idle.

A *job* is a prepared worker object exposing the ``_ExportWorker``
contract — ``progress(int, int, str)`` + ``finished_result(object)``
signals, ``start()`` and ``cancel()`` — plus a display label and an
``on_finished(result)`` callback the queue runs on the UI thread.
Today's worker renders sequentially; the hardware-maximising engine
(GPU encode, frame-parallel cores, yield-to-foreground) replaces the
worker INTERNALS in its own design session — the queue and the line
stay exactly as they are.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, List, Optional

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QWidget,
)

from mira.ui.i18n import tr

log = logging.getLogger(__name__)


@dataclass
class _Job:
    worker: object
    label: str
    on_finished: Optional[Callable[[object], None]]


class BatchExportQueue(QObject):
    """Strictly-serial job runner. ``changed`` fires on every state or
    progress tick — the progress line re-syncs from the properties."""

    changed = pyqtSignal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._pending: List[_Job] = []
        self._current: Optional[_Job] = None
        self._done = 0
        self._total = 0
        self._name = ""

    # ── state the line reads ─────────────────────────────────────────
    @property
    def running_label(self) -> str:
        return self._current.label if self._current is not None else ""

    @property
    def queued_count(self) -> int:
        return len(self._pending)

    @property
    def progress(self) -> tuple:
        return (self._done, self._total, self._name)

    @property
    def idle(self) -> bool:
        return self._current is None and not self._pending

    # ── API ──────────────────────────────────────────────────────────
    def enqueue(self, worker, label: str,
                on_finished: Optional[Callable] = None) -> None:
        self._pending.append(_Job(worker, label, on_finished))
        log.info("batch queue: +1 job (%s); %d pending", label,
                 len(self._pending))
        self.changed.emit()
        self._maybe_start()

    def cancel_current(self) -> None:
        if self._current is not None:
            try:
                self._current.worker.cancel()
            except Exception:  # noqa: BLE001
                log.exception("batch queue: cancel failed")

    # ── runner ───────────────────────────────────────────────────────
    def _maybe_start(self) -> None:
        if self._current is not None or not self._pending:
            return
        job = self._pending.pop(0)
        self._current = job
        self._done = self._total = 0
        self._name = ""
        job.worker.progress.connect(self._on_progress)
        job.worker.finished_result.connect(
            lambda result, j=job: self._on_finished(j, result))
        log.info("batch queue: starting %s", job.label)
        self.changed.emit()
        job.worker.start()

    def _on_progress(self, done: int, total: int, name: str) -> None:
        self._done, self._total, self._name = int(done), int(total), name
        self.changed.emit()

    def _on_finished(self, job: _Job, result) -> None:
        if job.on_finished is not None:
            try:
                job.on_finished(result)
            except Exception:  # noqa: BLE001
                # A lost commit self-heals via the Edited Media return
                # scan on the next Edit entry (spec/57 §3).
                log.exception(
                    "batch queue: commit failed for %s", job.label)
        log.info("batch queue: finished %s", job.label)
        self._current = None
        self.changed.emit()
        self._maybe_start()


class BatchProgressLine(QWidget):
    """The one app-level progress line (spec/59 §8) — sits directly
    below the menubar, spans the window, hidden when the queue idles."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("BatchProgressLine")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 3, 10, 3)
        row.setSpacing(10)
        self._label = QLabel("")
        self._label.setObjectName("BatchProgressLabel")
        row.addWidget(self._label)
        self._bar = QProgressBar()
        self._bar.setMaximumHeight(14)
        self._bar.setTextVisible(False)
        row.addWidget(self._bar, stretch=1)
        self._queued = QLabel("")
        self._queued.setObjectName("BatchProgressQueued")
        self._queued.setToolTip(tr(
            "Batch jobs run one at a time; the rest wait in line."))
        row.addWidget(self._queued)
        self._cancel = QPushButton(tr("Cancel"))
        self._cancel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._cancel.setToolTip(tr("Stop the running batch job."))
        row.addWidget(self._cancel)
        self._queue: Optional[BatchExportQueue] = None
        self.setVisible(False)

    def bind(self, queue: BatchExportQueue) -> None:
        self._queue = queue
        queue.changed.connect(self._sync)
        self._cancel.clicked.connect(queue.cancel_current)
        self._sync()

    def _sync(self) -> None:
        q = self._queue
        if q is None or q.idle:
            self.setVisible(False)
            return
        done, total, name = q.progress
        self._label.setText(q.running_label + (
            "  ·  " + name if name else ""))
        self._bar.setMaximum(max(1, total))
        self._bar.setValue(min(done, max(1, total)))
        self._queued.setText(
            tr("+{n} waiting").replace("{n}", str(q.queued_count))
            if q.queued_count else "")
        self.setVisible(True)
