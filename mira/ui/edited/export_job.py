"""The spec/60 batch export job — the queue-shaped Qt adapter.

Satisfies the :class:`~mira.ui.shell.batch_queue.
BatchExportQueue` contract (``progress(int, int, str)`` +
``finished_result(object)`` signals, ``start()``, ``cancel()``) around
one :class:`~core.worker_job.WorkerJob`: write the manifest to a temp
file, spawn the worker, relay its per-unit stream as progress ticks,
fold the messages into a :class:`~core.worker_job.BatchJobResult` for
the host's commit.

Cancel kills the worker process tree (sub-second — §6); units already
on disk stay in the result and commit honestly. When the worker
cannot SPAWN at all, the same manifest renders in-process and
sequentially (§4's last resort) — every machine completes every job.
A worker that started and then died mid-job is NOT retried inline
(§6: the job fails cleanly with whatever units finished; a blind
re-run could double-write).
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Dict, Optional

from PyQt6.QtCore import QThread, pyqtSignal

from core.export_manifest import ExportManifest
from core.render_worker import run_manifest_inline
from core.worker_job import (
    WorkerJob,
    WorkerSpawnError,
    build_batch_result,
)

log = logging.getLogger(__name__)


class BatchExportJob(QThread):
    """One batch job over a fully-resolved manifest."""

    progress = pyqtSignal(int, int, str)
    finished_result = pyqtSignal(object)

    def __init__(self, manifest: ExportManifest,
                 source_by_unit_id: Dict[str, Path],
                 parent=None) -> None:
        super().__init__(parent)
        self._manifest = manifest
        self._sources = dict(source_by_unit_id)
        self._cancel = False
        self._job: Optional[WorkerJob] = None

    def cancel(self) -> None:
        self._cancel = True
        job = self._job
        if job is not None:
            job.kill()

    # ── the job body (queue calls start(); QThread runs this off the
    #    UI thread) ────────────────────────────────────────────────────
    def run(self) -> None:  # noqa: D401
        manifest_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                    "w", suffix=".json", prefix="mc_export_job_",
                    delete=False, encoding="utf-8") as fh:
                fh.write(self._manifest.to_json())
                manifest_path = Path(fh.name)
            result = self._run_worker(manifest_path)
        except Exception:  # noqa: BLE001 — a job must always finish
            log.exception("batch export job failed")
            result = build_batch_result(
                [], self._sources, cancelled=self._cancel)
        finally:
            if manifest_path is not None:
                try:
                    manifest_path.unlink(missing_ok=True)
                except OSError:
                    pass
        self.finished_result.emit(result)

    def _run_worker(self, manifest_path: Path):
        try:
            job = WorkerJob(manifest_path)
            job.start()
        except WorkerSpawnError as exc:
            log.warning(
                "render worker could not spawn (%s) — running the "
                "in-process fallback (spec/60 §4)", exc)
            return self._run_inline()

        self._job = job
        if self._cancel:                 # cancelled during spawn
            job.kill()

        total = len(self._manifest.units)
        done = 0
        unit_messages: list[dict] = []
        for msg in job.messages():
            kind = msg.get("type")
            if kind == "start":
                total = int(msg.get("total", total))
            elif kind == "unit":
                done += 1
                unit_messages.append(msg)
                src = self._sources.get(msg.get("unit_id", ""))
                self.progress.emit(done, total,
                                   src.name if src else "")
            elif kind == "fatal":
                log.error("render worker fatal: %s", msg.get("error"))
        rc = job.wait()
        if rc != 0 and not self._cancel:
            log.warning(
                "render worker exited rc=%s after %d/%d unit(s); "
                "stderr tail: %s", rc, done, total,
                " ⏎ ".join(job.stderr_tail[-5:]))
        return build_batch_result(
            unit_messages, self._sources, cancelled=self._cancel)

    def _run_inline(self):
        def cb(done: int, total: int, name: str) -> bool:
            self.progress.emit(done, total, name)
            return not self._cancel

        messages = run_manifest_inline(self._manifest, progress=cb)
        return build_batch_result(
            messages, self._sources, ran_inline=True,
            cancelled=self._cancel)


__all__ = ["BatchExportJob"]
