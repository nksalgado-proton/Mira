"""The app-level batch-job queue + its progress line (spec/59 §8, spec/84 §2).

A user can launch as many batch jobs as they like (day-scope export,
event-scope export, ingest of an event); they QUEUE and run strictly
ONE at a time, app-level — the user keeps working anywhere in the app,
dashboard included. The one progress line lives directly below the
menubar and shows the running job (verb + label · per-file progress ·
how many wait · Cancel); hidden when idle.

A *job* is a prepared worker object exposing the ``_ExportWorker``
contract — ``progress(int, int, str)`` + ``finished_result(object)``
signals, ``start()`` and ``cancel()`` — plus a display label, a
``job_type`` (``"export"`` / ``"import"``) the line uses to render the
verb prefix, and an ``on_finished(result)`` callback the queue runs on
the UI thread. Each job type's worker is its own concern; the queue
and the line don't care which lane the work is in.

History — spec/84 generalised the queue so ingest can ride it too
(rename ``BatchExportQueue`` → ``BatchJobQueue``, ``job_type`` arg on
``enqueue``). A thin ``BatchExportQueue`` alias stays so existing test +
docstring sites that named the class verbatim keep working.
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


#: Job-type tag passed to :meth:`BatchJobQueue.enqueue` — picks the verb
#: prefix the progress line draws in front of the caller's label.
JOB_TYPE_EXPORT = "export"
JOB_TYPE_IMPORT = "import"


@dataclass
class _Job:
    worker: object
    label: str
    on_finished: Optional[Callable[[object], None]]
    job_type: str = JOB_TYPE_EXPORT


class BatchJobQueue(QObject):
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
    def running_job_type(self) -> str:
        return self._current.job_type if self._current is not None else ""

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
                on_finished: Optional[Callable] = None,
                *, job_type: str = JOB_TYPE_EXPORT) -> None:
        self._pending.append(_Job(worker, label, on_finished, job_type))
        log.info("batch queue: +1 %s job (%s); %d pending",
                 job_type, label, len(self._pending))
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
        log.info("batch queue: starting %s (%s)", job.job_type, job.label)
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
        log.info("batch queue: finished %s (%s)", job.job_type, job.label)
        self._current = None
        self.changed.emit()
        self._maybe_start()


#: Back-compat alias — spec/84 renamed the class but several call sites
#: + tests still import the old name. New code uses ``BatchJobQueue``.
BatchExportQueue = BatchJobQueue


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
        row.addWidget(self._cancel)
        self._queue: Optional[BatchJobQueue] = None
        self.setVisible(False)

    def bind(self, queue: BatchJobQueue) -> None:
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
        head = self._format_head(q.running_job_type, q.running_label)
        self._label.setText(head + ("  ·  " + name if name else ""))
        self._cancel.setToolTip(
            self._cancel_tooltip(q.running_job_type))
        self._bar.setMaximum(max(1, total))
        self._bar.setValue(min(done, max(1, total)))
        self._queued.setText(
            tr("+{n} waiting").replace("{n}", str(q.queued_count))
            if q.queued_count else "")
        self.setVisible(True)

    @staticmethod
    def _format_head(job_type: str, label: str) -> str:
        """Compose the head of the progress line as ``"{verb} {label}"``.

        The verb is chosen from ``job_type`` (spec/84 §2 — the queue +
        line now serve ingest as well as export). Unknown / empty types
        fall back to the bare label so a future job type renders
        usefully even before this map learns the verb.
        """
        if not label:
            return ""
        if job_type == JOB_TYPE_IMPORT:
            return tr("Importing {label}").replace("{label}", label)
        if job_type == JOB_TYPE_EXPORT:
            return tr("Exporting {label}").replace("{label}", label)
        return label

    @staticmethod
    def _cancel_tooltip(job_type: str) -> str:
        if job_type == JOB_TYPE_IMPORT:
            return tr("Stop the running import.")
        if job_type == JOB_TYPE_EXPORT:
            return tr("Stop the running export.")
        return tr("Stop the running batch job.")


__all__ = [
    "BatchJobQueue",
    "BatchExportQueue",
    "BatchProgressLine",
    "JOB_TYPE_EXPORT",
    "JOB_TYPE_IMPORT",
]
