"""The spec/84 ingest job — the queue-shaped Qt adapter around the
Qt-free ``run_ingest`` copy engine.

Satisfies the :class:`~mira.ui.shell.batch_queue.BatchJobQueue` contract
(``progress(int, int, str)`` + ``finished_result(object)`` signals,
``start()``, ``cancel()``) so the queue can run it the same way it
already runs :class:`~mira.ui.edited.export_job.BatchExportJob`. The
thread does **copy + hash + bake** (the Qt-free
``core/ingest_pipeline`` half) and relays per-file progress; the
``item`` rows are written by the queue's ``on_finished`` callback on
the UI thread (spec/84 §3 — one SQLite connection per thread, the copy
thread never writes ``event.db``).

The job is engine-agnostic: it wraps any callable ``work(progress_cb,
should_cancel) -> payload`` so a single class drives both the
create-event-from-photos engine path AND the lower-level Collect-OK
``core.ingest_pipeline`` path. The wiring layer (slice 3) supplies the
callable; what the payload contains is the wiring layer's concern.

Cancel sets a flag the work callable polls — when the underlying copy
loop checks it between files (slice 5 threads ``should_cancel`` into
``run_ingest``), the run bails at the next file. Whatever finished
copying stays on disk; spec/57's re-run-resumes rule picks up the
remainder on the next attempt.
"""
from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Optional

from PyQt6.QtCore import QThread, pyqtSignal

log = logging.getLogger(__name__)


#: Progress shape the queue speaks — same as :class:`BatchExportJob`.
ProgressCb = Callable[[int, int, str], None]
#: Polled inside the copy loop; ``True`` once :meth:`IngestJob.cancel`
#: has been called.
ShouldCancel = Callable[[], bool]
#: The Qt-free work the job runs on its own thread. Returns whatever
#: payload the committer needs (typically a
#: :class:`core.ingest_pipeline.IngestResult` carrying ``per_job_info``).
WorkCallable = Callable[[ProgressCb, ShouldCancel], Any]


@dataclass
class IngestJobResult:
    """What :class:`IngestJob` hands back to the queue's
    ``on_finished`` callback.

    ``payload`` is the engine's own result object — the committer reads
    it to write ``item`` rows on the UI thread. ``cancelled`` is set
    when the user pressed Cancel mid-run (the copy loop bailed at the
    next file). ``error`` carries a short traceback when the worker
    crashed — the committer skips DB writes and the wiring layer
    surfaces the failure (spec/84 §6 — the job MUST always finish so
    the line never stalls).
    """

    payload: Any = None
    cancelled: bool = False
    error: Optional[str] = None


class IngestJob(QThread):
    """One ingest run, queue-shaped.

    The thread runs a Qt-free copy callable (``run_ingest`` & friends)
    and relays its progress ticks. Cancelling sets a flag the copy
    loop polls (slice 5 threads it through ``run_ingest``); whatever
    copied so far stays on disk and surfaces as a partial result that
    a re-run can finish (spec/57 §4.3.1).
    """

    progress = pyqtSignal(int, int, str)
    finished_result = pyqtSignal(object)

    def __init__(self, work: WorkCallable, parent=None) -> None:
        super().__init__(parent)
        self._work = work
        self._cancel = False

    # ── queue contract ───────────────────────────────────────────────
    def cancel(self) -> None:
        self._cancel = True

    @property
    def cancelled(self) -> bool:
        return self._cancel

    # ── the job body (queue calls start(); QThread runs this off the
    #    UI thread) ────────────────────────────────────────────────────
    def run(self) -> None:  # noqa: D401
        try:
            payload = self._work(self._emit_progress, self._is_cancelled)
            result = IngestJobResult(
                payload=payload, cancelled=self._cancel)
        except Exception:  # noqa: BLE001 — a job must always finish
            log.exception("ingest job failed")
            result = IngestJobResult(
                payload=None, cancelled=self._cancel,
                error=traceback.format_exc(limit=4),
            )
        self.finished_result.emit(result)

    # ── private — bound callbacks handed to the work callable ────────
    def _emit_progress(self, done: int, total: int, name: str) -> None:
        self.progress.emit(int(done), int(total), str(name))

    def _is_cancelled(self) -> bool:
        return self._cancel


__all__ = ["IngestJob", "IngestJobResult", "WorkCallable"]
