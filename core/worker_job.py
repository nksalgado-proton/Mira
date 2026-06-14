"""App-side plumbing for the spec/60 render worker process.

:class:`WorkerJob` owns one worker process: spawn (below-normal
priority + no console window), a Windows **job object** with
kill-on-close so the whole worker tree dies with the app (spec/60 §6
— app close mid-job leaks nothing), a thread-safe :meth:`kill` that
terminates the tree sub-second (cancel is process-shaped, no
cooperative polling), and :meth:`messages` — the blocking JSON-lines
reader the caller runs off the UI thread.

:func:`build_batch_result` turns the streamed unit messages into a
:class:`BatchJobResult` — an :class:`~core.cull_export.ExportResult`
whose buckets contain ONLY units that actually succeeded (per-unit
truth, §5), so the existing lineage writer and summary readers stay
correct unchanged. The raw per-unit outcomes ride along for the
commit seam.

Pure logic — no Qt (the Qt adapter lives in
``mira/ui/edited/export_job.py``).
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Iterator, Optional

from core.cull_export import ExportResult
from core.render_worker import worker_command

log = logging.getLogger(__name__)

_CREATE_NO_WINDOW = 0x08000000
_BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JobObjectExtendedLimitInformation = 9


class WorkerSpawnError(RuntimeError):
    """The worker process could not be started at all — the caller
    falls back to the in-process path (spec/60 §4 last resort)."""


# ── the Windows job object (kill-on-close) ───────────────────────────


def _make_kill_on_close_job() -> Optional[int]:
    """A job object whose handle, once closed (app exit included),
    terminates every process assigned to it. ``None`` off Windows or
    on any failure — the job object is hardening, not a requirement."""
    if sys.platform != "win32":
        return None
    import ctypes

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in (
            "ReadOperationCount", "WriteOperationCount",
            "OtherOperationCount", "ReadTransferCount",
            "WriteTransferCount", "OtherTransferCount")]

    class _BASIC(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class _EXTENDED(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BASIC),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    try:
        k32 = ctypes.windll.kernel32
        handle = k32.CreateJobObjectW(None, None)
        if not handle:
            return None
        info = _EXTENDED()
        info.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        if not k32.SetInformationJobObject(
                handle, _JobObjectExtendedLimitInformation,
                ctypes.byref(info), ctypes.sizeof(info)):
            k32.CloseHandle(handle)
            return None
        return int(handle)
    except Exception:  # noqa: BLE001
        return None


class WorkerJob:
    """One spawned worker process + its job object."""

    def __init__(self, manifest_path: Path) -> None:
        self._manifest_path = Path(manifest_path)
        self._proc: Optional[subprocess.Popen] = None
        self._job_handle: Optional[int] = None
        self._lock = threading.Lock()
        self._stderr_tail: list[str] = []

    def start(self) -> None:
        """Spawn the worker. Raises :class:`WorkerSpawnError` when the
        process cannot start (exotic AV, hostile environment — §4)."""
        cmd = worker_command(self._manifest_path)
        kwargs: dict = {}
        if sys.platform == "win32":
            # Belt and braces: the worker also lowers itself, but the
            # spawn flag covers the import-heavy startup window too.
            kwargs["creationflags"] = (
                _CREATE_NO_WINDOW | _BELOW_NORMAL_PRIORITY_CLASS)
        try:
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace", **kwargs)
        except OSError as exc:
            raise WorkerSpawnError(str(exc)) from exc

        self._job_handle = _make_kill_on_close_job()
        if self._job_handle is not None:
            import ctypes
            try:
                # Popen._handle is the documented-in-practice process
                # handle on Windows; OpenProcess would need extra
                # rights juggling for no gain.
                ok = ctypes.windll.kernel32.AssignProcessToJobObject(
                    self._job_handle, int(self._proc._handle))
                if not ok:
                    log.info("worker job object: assign failed — "
                             "kill-on-close unavailable this run")
                    ctypes.windll.kernel32.CloseHandle(self._job_handle)
                    self._job_handle = None
            except Exception:  # noqa: BLE001
                self._job_handle = None

        threading.Thread(
            target=self._drain_stderr, daemon=True,
            name="export-worker-stderr").start()
        log.info("render worker spawned (pid %s)", self._proc.pid)

    def _drain_stderr(self) -> None:
        """Relay worker logs into the app log (the forensics first
        stop) and keep a short tail for failure diagnostics. Also
        prevents the worker blocking on a full stderr pipe."""
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                line = line.rstrip()
                if not line:
                    continue
                self._stderr_tail = (self._stderr_tail + [line])[-30:]
                log.info("worker│ %s", line)
        except Exception:  # noqa: BLE001
            pass

    def messages(self) -> Iterator[dict]:
        """Yield protocol dicts from the worker's stdout until EOF.
        Blocking — run on a worker-side thread, never the UI thread.
        Non-JSON noise is skipped (the protocol owns stdout, but a
        stray print from a deep import must not kill the job)."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                log.debug("worker stdout noise: %s", line[:200])
                continue
            if isinstance(msg, dict) and msg.get("type"):
                yield msg

    def kill(self) -> None:
        """Terminate the worker TREE (ffmpeg grandchildren included)
        — sub-second, thread-safe, idempotent (spec/60 §6)."""
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                return
            if self._job_handle is not None and sys.platform == "win32":
                import ctypes
                try:
                    ctypes.windll.kernel32.TerminateJobObject(
                        self._job_handle, 1)
                    return
                except Exception:  # noqa: BLE001
                    pass
            try:
                proc.kill()
            except OSError:
                pass

    def wait(self) -> int:
        """Wait for exit; returns the return code and releases the
        job-object handle."""
        proc = self._proc
        rc = proc.wait() if proc is not None else -1
        with self._lock:
            if self._job_handle is not None and sys.platform == "win32":
                import ctypes
                try:
                    ctypes.windll.kernel32.CloseHandle(self._job_handle)
                except Exception:  # noqa: BLE001
                    pass
                self._job_handle = None
        return rc

    @property
    def stderr_tail(self) -> list[str]:
        return list(self._stderr_tail)


# ── result building (pure) ───────────────────────────────────────────


@dataclass
class BatchJobResult(ExportResult):
    """An :class:`ExportResult` built from per-unit worker messages.
    The success buckets contain ONLY units that actually succeeded —
    the lineage writer and summary readers inherit per-unit truth
    without changes. ``unit_results`` carries the raw outcomes for
    the commit seam; ``resolved_by_name`` is the params_sink twin
    (source filename → resolved tone numbers)."""

    unit_results: list = field(default_factory=list)
    resolved_by_name: dict = field(default_factory=dict)
    ran_inline: bool = False
    cancelled: bool = False

    @property
    def ok_unit_ids(self) -> set:
        return {m["unit_id"] for m in self.unit_results
                if m.get("status") == "ok"}

    @property
    def ok_clip_results(self) -> list:
        """ok messages for clip units — lineage uses a different writer
        (record_single_lineage) and a per-clip final path."""
        return [m for m in self.unit_results
                if m.get("status") == "ok" and m.get("kind") == "clip"]


def build_batch_result(
    messages: list[dict],
    source_by_unit_id: dict[str, Path],
    *,
    ran_inline: bool = False,
    cancelled: bool = False,
) -> BatchJobResult:
    """Fold streamed messages into a :class:`BatchJobResult`."""
    result = BatchJobResult(ran_inline=ran_inline, cancelled=cancelled)
    for msg in messages:
        if msg.get("type") != "unit":
            continue
        result.unit_results.append(msg)
        uid = msg.get("unit_id", "")
        src = Path(source_by_unit_id.get(uid, Path(uid)))
        status = msg.get("status")
        kind = msg.get("kind", "photo")
        if status == "ok":
            final = Path(msg["final_path"])
            # Clip exports bypass the photo result-bucket fold: their
            # lineage rides record_single_lineage, keyed per clip-item.
            # Folding their finals into result.written would mislead the
            # photo lineage walker (it'd try to match clip stems back to
            # photo source items by name).
            if kind == "photo":
                if msg.get("renamed"):
                    result.renamed.append((src, final))
                elif msg.get("existed_before"):
                    result.overwritten.append(final)
                else:
                    result.written.append(final)
                if msg.get("params") is not None:
                    result.resolved_by_name[src.name] = msg["params"]
        elif status == "skipped":
            result.skipped.append((src, msg.get("reason", "")))
        else:
            result.errors.append((src, msg.get("error", "")))
    return result


__all__ = [
    "BatchJobResult",
    "WorkerJob",
    "WorkerSpawnError",
    "build_batch_result",
]
