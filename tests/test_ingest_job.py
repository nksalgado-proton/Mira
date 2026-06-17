"""spec/84 §2 — the IngestJob's queue contract.

Pins: progress relays through, the final result carries the engine
payload + the ``cancelled`` flag, cancel sets the flag the copy loop
polls, and a crash inside the work callable lands as a non-None
``error`` string (the job MUST always finish, spec/84 §6 — else the
shared queue stalls and no other job can run).
"""
from __future__ import annotations

from PyQt6.QtCore import QEventLoop, QTimer

from mira.ui.ingest.ingest_job import IngestJob, IngestJobResult


def _wait_for(job, timeout_ms: int = 5000) -> IngestJobResult:
    """Spin the event loop until the job's ``finished_result`` fires
    (or the timeout trips). Returns the result the job emitted, or
    ``None`` if it didn't finish in time."""
    captured: list = []
    loop = QEventLoop()
    job.finished_result.connect(captured.append)
    job.finished_result.connect(lambda _r: loop.quit())
    job.finished.connect(loop.quit)
    QTimer.singleShot(timeout_ms, loop.quit)
    job.start()
    loop.exec()
    job.wait(timeout_ms)
    return captured[0] if captured else None


def test_ingest_job_emits_progress_and_returns_payload(qapp):
    """Happy path — the work callable's ticks reach the queue's
    progress signal in order, and the payload it returned rides home
    on the result."""
    progress_ticks: list = []

    def _work(progress_cb, should_cancel):
        progress_cb(1, 3, "a.jpg")
        progress_cb(2, 3, "b.jpg")
        progress_cb(3, 3, "c.jpg")
        return {"copied": 3}

    job = IngestJob(_work)
    job.progress.connect(
        lambda d, t, n: progress_ticks.append((d, t, n)))
    result = _wait_for(job)

    assert isinstance(result, IngestJobResult)
    assert result.payload == {"copied": 3}
    assert result.cancelled is False
    assert result.error is None
    assert progress_ticks == [
        (1, 3, "a.jpg"), (2, 3, "b.jpg"), (3, 3, "c.jpg")]


def test_ingest_job_cancel_before_start_is_observed(qapp):
    """Pre-cancel: a work callable polled ``should_cancel`` BEFORE the
    first file sees ``True``. Models the "cancel arrived faster than
    the thread booted" edge case the queue needs to handle cleanly."""
    saw_cancel: list = []

    def _work(progress_cb, should_cancel):
        saw_cancel.append(should_cancel())
        return {"completed": 0}

    job = IngestJob(_work)
    job.cancel()
    result = _wait_for(job)

    assert saw_cancel == [True]
    assert result.cancelled is True
    assert result.payload == {"completed": 0}


def test_ingest_job_cancel_during_work_bails_at_next_file(qapp):
    """Mid-run cancel: the copy loop polls between files and bails at
    the next one. Pins the loop contract slice 5 wires into
    ``run_ingest``."""
    journal: list = []
    job_holder: list = []

    def _work(progress_cb, should_cancel):
        for i in range(1, 11):
            if should_cancel():
                journal.append(("bailed_at", i))
                return {"completed": i - 1}
            progress_cb(i, 10, f"f{i}.jpg")
            if i == 3:
                job_holder[0].cancel()
        return {"completed": 10}

    job = IngestJob(_work)
    job_holder.append(job)
    result = _wait_for(job)

    assert result.cancelled is True
    assert journal == [("bailed_at", 4)]
    assert result.payload == {"completed": 3}


def test_ingest_job_crash_is_captured_in_error(qapp):
    """A raised exception in the work callable surfaces as a non-None
    ``error`` on the result and the job STILL finishes — the queue
    cannot afford a dead job stalling the line (spec/84 §6)."""
    def _boom(progress_cb, should_cancel):
        raise RuntimeError("disk gone")

    job = IngestJob(_boom)
    result = _wait_for(job)

    assert result.payload is None
    assert result.cancelled is False
    assert result.error is not None
    assert "disk gone" in result.error


def test_ingest_job_finished_result_carries_a_single_payload(qapp):
    """The queue connects ``finished_result.connect(on_finished)`` and
    hands that one object straight to the commit closure — pin the
    signal's arity so a future refactor can't silently break the
    handshake."""
    captured: list = []

    def _work(progress_cb, should_cancel):
        return "done"

    job = IngestJob(_work)
    job.finished_result.connect(lambda *args: captured.append(args))
    _wait_for(job)

    assert len(captured) == 1
    assert len(captured[0]) == 1                     # ONE positional payload
    assert isinstance(captured[0][0], IngestJobResult)
    assert captured[0][0].payload == "done"
