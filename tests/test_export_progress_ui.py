"""spec/139 §3 — two-level export progress UI.

Aggregate bar: ``"Exporting N of M"`` — no filename (the filename was
clutter and overflowed). Per-file bar (under the aggregate): the
current clip's ``file_fraction`` as a 0..100% bar, hidden when no
fraction signal has landed, reset to 0 every time the aggregate
``done`` ticks forward (new file starting), ``"encoding…"`` hint for
in-flight video.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mira.ui.shell.batch_queue import (
    BatchJobQueue,
    BatchProgressLine,
    JOB_TYPE_EXPORT,
    JOB_TYPE_IMPORT,
)


class _StubWorker:
    """Minimal duck for what ``BatchJobQueue._maybe_start`` wires
    onto: ``progress`` + ``file_fraction`` + ``finished_result``
    signals plus a ``start()`` method (the queue calls it; we never
    let it actually run anything)."""

    def __init__(self, with_file_fraction: bool = True):
        # pyqtSignal stand-ins: list-of-slots; ``emit`` calls them all.
        self.progress = _Signal()
        self.file_fraction = _Signal() if with_file_fraction else None
        self.finished_result = _Signal()
        self.started = False
        self.cancelled = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True


class _Signal:
    """Fake pyqtSignal with the connect()/emit() shape the queue uses."""

    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


def _enqueue_and_start(queue: BatchJobQueue,
                       worker, label: str = "Italy (3)") -> None:
    """Enqueue + step into the worker so ``current`` is set + signals
    are connected. We never let the worker actually finish (the test
    body emits progress/fraction directly to exercise the line)."""
    queue.enqueue(worker, label, lambda _r: None, job_type=JOB_TYPE_EXPORT)


# ── Aggregate label: "Exporting N of M", no filename ───────────────


def test_aggregate_label_is_n_of_m_no_filename(qapp):
    """spec/139 §3 — aggregate text is ``"Exporting N of M"``: no
    event label, no per-file name. A long source filename or event
    name must NEVER appear on the line."""
    queue = BatchJobQueue()
    line = BatchProgressLine()
    line.bind(queue)
    worker = _StubWorker()
    _enqueue_and_start(queue, worker, label="Some Long Event Name (15)")
    # 3 of 15, currently on a file with a long descriptive name.
    worker.progress.emit(3, 15, "DSC_0123_a_very_long_export_filename.jpg")

    text = line._label.text()
    assert "3 of 15" in text, (
        f"aggregate must read 'N of M'; got {text!r}"
    )
    assert "Exporting" in text, (
        f"aggregate must include the verb 'Exporting'; got {text!r}"
    )
    # The acceptance contract — no filename anywhere.
    assert "DSC_0123" not in text, (
        f"spec/139 §3: aggregate must NOT show the filename; got {text!r}"
    )
    assert ".jpg" not in text, (
        f"spec/139 §3: aggregate must NOT show the filename; got {text!r}"
    )
    # And no event label clutter either — the count + the moving
    # per-file bar are the signal, per the spec.
    assert "Some Long Event Name" not in text, (
        f"spec/139 §3: aggregate drops the event label; got {text!r}"
    )


def test_aggregate_label_uses_importing_verb_for_import_jobs(qapp):
    """The same N-of-M form applies to import jobs (spec/84 §2 —
    the line serves both export and ingest)."""
    queue = BatchJobQueue()
    line = BatchProgressLine()
    line.bind(queue)
    worker = _StubWorker()
    queue.enqueue(worker, "card1", lambda _r: None,
                  job_type=JOB_TYPE_IMPORT)
    worker.progress.emit(7, 200, "IMG_0007.cr3")

    text = line._label.text()
    assert "Importing" in text and "7 of 200" in text
    assert "IMG_0007" not in text


# ── Per-file bar: reflects file_fraction, resets per file ──────────


def test_per_file_bar_reflects_file_fraction(qapp):
    """A ``file_fraction(0.42)`` emit paints the per-file bar at ~42%."""
    queue = BatchJobQueue()
    line = BatchProgressLine()
    line.bind(queue)
    worker = _StubWorker()
    _enqueue_and_start(queue, worker)
    worker.progress.emit(1, 3, "")
    worker.file_fraction.emit(0.42)

    # The line uses a 0..1000 range for 0.1% resolution.
    assert abs(line._file_bar.value() - 420) <= 1, (
        f"per-file bar should reflect 0.42 (≈420 of 1000); got "
        f"{line._file_bar.value()}"
    )
    assert line._file_bar.isVisible() is True


def test_per_file_bar_resets_when_aggregate_done_advances(qapp):
    """When the aggregate ``done`` ticks (new file starting), the
    per-file bar snaps back to 0 — otherwise the user would see the
    bar paused at the previous file's tail while the next file
    starts."""
    queue = BatchJobQueue()
    worker = _StubWorker()
    _enqueue_and_start(queue, worker)
    worker.progress.emit(1, 3, "a.jpg")
    worker.file_fraction.emit(0.9)
    assert queue.file_fraction == pytest.approx(0.9)
    # Next file starts → done ticks 1 → 2.
    worker.progress.emit(2, 3, "b.jpg")
    assert queue.file_fraction == pytest.approx(0.0), (
        f"spec/139 §3: per-file fraction must reset to 0 on new file; "
        f"got {queue.file_fraction}"
    )


def test_per_file_bar_hidden_when_no_fraction_signal_yet(qapp):
    """A photo-only batch may complete without ever emitting a
    fraction (BatchExportJob unit-completes will emit 1.0; but
    BEFORE the first emit the per-file bar should stay hidden)."""
    queue = BatchJobQueue()
    line = BatchProgressLine()
    line.bind(queue)
    worker = _StubWorker()
    _enqueue_and_start(queue, worker)
    # Aggregate tick without any file_fraction → bar hidden.
    worker.progress.emit(1, 5, "p.jpg")
    assert line._file_bar.isVisible() is False
    assert line._file_label.isVisible() is False


def test_per_file_label_hint_only_while_encoding(qapp):
    """While ``fraction < 1.0`` the ``"encoding…"`` hint shows; at
    1.0 the hint clears (the bar is full anyway; no need to draw
    attention to a completed file)."""
    queue = BatchJobQueue()
    line = BatchProgressLine()
    line.bind(queue)
    worker = _StubWorker()
    _enqueue_and_start(queue, worker)
    worker.progress.emit(1, 2, "")
    worker.file_fraction.emit(0.3)
    assert line._file_label.text() == "encoding…"
    assert line._file_label.isVisible() is True
    worker.file_fraction.emit(1.0)
    assert line._file_label.isVisible() is False


# ── Queue state-tracking ───────────────────────────────────────────


def test_queue_starts_with_no_file_fraction(qapp):
    """At construction the queue has no per-file signal yet — the
    UI reads ``None`` and hides the second bar."""
    queue = BatchJobQueue()
    assert queue.file_fraction is None


def test_queue_clears_file_fraction_at_job_start(qapp):
    """A leftover fraction from the previous job MUST NOT bleed into
    the next one — each new job starts at ``None`` so the second
    bar collapses until the next ``file_fraction`` lands."""
    queue = BatchJobQueue()
    worker1 = _StubWorker()
    _enqueue_and_start(queue, worker1)
    worker1.progress.emit(1, 1, "")
    worker1.file_fraction.emit(0.5)
    assert queue.file_fraction == pytest.approx(0.5)
    worker1.finished_result.emit(SimpleNamespace())

    # Next job — fraction is reset to None.
    worker2 = _StubWorker()
    _enqueue_and_start(queue, worker2)
    assert queue.file_fraction is None, (
        f"queue must reset file_fraction per job; got {queue.file_fraction}"
    )


def test_worker_without_file_fraction_signal_still_wires(qapp):
    """spec/139 §3 — the queue's ``hasattr(worker, "file_fraction")``
    guard lets older workers / ingest workers without the signal
    still enqueue cleanly (no AttributeError)."""
    queue = BatchJobQueue()
    worker = _StubWorker(with_file_fraction=False)
    _enqueue_and_start(queue, worker)
    # Just exercising the wire — no crash means the guard works.
    worker.progress.emit(1, 1, "")
    assert queue.file_fraction is None


def test_bar_hidden_when_queue_idle(qapp):
    """Idle queue → no per-file bar; the line just says "Ready"."""
    queue = BatchJobQueue()
    line = BatchProgressLine()
    line.bind(queue)
    assert line._file_bar.isVisible() is False
    assert line._file_label.isVisible() is False
    assert "Ready" in line._label.text() or line._label.text() == ""
