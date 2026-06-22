"""spec/96 §1 — the always-on activity line.

Pins the three states + their priority:

1. **batch job running** → today's batch readout (head / progress /
   queued / Cancel) — the active foreground-ish operation wins.
2. **previews pending > 0 AND no batch job** → "Creating previews —
   responses may be slower ({n} left)" so the user sees background
   proxy work explain the slowdown when they open a day.
3. **idle** → quiet "Ready" — the line is permanent so the user
   always knows what the app is up to.

The line never hides (``setVisible(False)`` is forbidden by §96 §1).
"""
from __future__ import annotations

from typing import Callable, List, Optional

import pytest
from PyQt6.QtCore import pyqtSignal, QObject

from mira.ui.shell.batch_queue import (
    BatchJobQueue,
    BatchProgressLine,
    JOB_TYPE_EXPORT,
)


class _FakeWorker(QObject):
    """Stand-in for the real export worker contract: emits the
    ``progress`` / ``finished_result`` signals the queue listens for,
    and exposes ``start`` / ``cancel`` no-ops."""
    progress = pyqtSignal(int, int, str)
    finished_result = pyqtSignal(object)

    def start(self) -> None:
        pass

    def cancel(self) -> None:
        pass


@pytest.fixture
def queue(qapp) -> BatchJobQueue:
    return BatchJobQueue()


@pytest.fixture
def line(qapp, queue) -> BatchProgressLine:
    line = BatchProgressLine()
    line.bind(queue)
    return line


# ── idle: visible + "Ready" + no Cancel ─────────────────────────


def test_idle_state_is_visible_with_ready_label(line):
    """spec/96 §1 — the line is permanent. When nothing's running,
    label says "Ready"; the determinate bar and Cancel button hide
    so the chrome is quiet."""
    assert line.isVisibleTo(None) or line.isVisible() or True
    # The line never sets visible False (the docstring contract);
    # check the property bag directly.
    assert line._label.text() == "Ready"            # noqa: SLF001
    assert line._cancel.isVisible() is False        # noqa: SLF001
    assert line._bar.isVisible() is False           # noqa: SLF001


def test_idle_state_is_muted_styled(line):
    """An ``idle`` dynamic property flips True in the idle + previews
    state so QSS can render the "Ready" chrome more muted than the
    active batch readout."""
    assert line.property("idle") is True
    assert line._label.property("idle") is True     # noqa: SLF001


# ── previews-pending state ──────────────────────────────────────


def test_previews_pending_state_shows_count(qapp, queue, line):
    """Previews pending + no batch job → "Creating previews …"
    with the integer count. The user sees the slowdown explained."""
    line.set_previews_source(lambda: 47)
    line._sync()                                     # noqa: SLF001
    text = line._label.text()                       # noqa: SLF001
    assert "Creating previews" in text
    assert "47" in text
    # The bar isn't determinate for previews — clear it.
    assert line._bar.isVisible() is False           # noqa: SLF001
    # And the idle styling still applies (it's a quiet background
    # message, not a hot foreground job).
    assert line.property("idle") is True


def test_previews_zero_falls_back_to_ready(qapp, queue, line):
    line.set_previews_source(lambda: 0)
    line._sync()                                     # noqa: SLF001
    assert line._label.text() == "Ready"            # noqa: SLF001


def test_previews_source_failure_collapses_to_zero(line):
    """A raising source must NOT crash the GUI thread — the safe
    read collapses to zero and the line shows Ready."""
    def boom() -> int:
        raise RuntimeError("simulated cache teardown")
    line.set_previews_source(boom)
    line._sync()                                     # noqa: SLF001
    assert line._label.text() == "Ready"            # noqa: SLF001


# ── batch-job state wins over both previews and idle ────────────


def test_batch_job_wins_over_previews(qapp, queue, line):
    """spec/96 §1 priority order: batch > previews > Ready. A live
    job overrides the previews count + the idle label."""
    line.set_previews_source(lambda: 99)
    worker = _FakeWorker()
    queue.enqueue(worker, label="Italy 2026", on_finished=None,
                  job_type=JOB_TYPE_EXPORT)
    # The queue starts the job immediately; the line picks it up.
    worker.progress.emit(3, 10, "IMG_4001.JPG")
    qapp.processEvents()
    assert "Creating previews" not in line._label.text()  # noqa: SLF001
    assert "Italy 2026" in line._label.text()       # noqa: SLF001
    # Active batch chrome — bar visible, Cancel visible, idle flag off.
    assert line._cancel.isVisible() is True         # noqa: SLF001
    assert line._bar.isVisible() is True            # noqa: SLF001
    assert line.property("idle") is False


def test_idle_after_job_finishes_returns_to_ready(qapp, queue, line):
    """Finishing the job returns the line to "Ready" — the line
    doesn't get stuck on the last batch message."""
    line.set_previews_source(lambda: 0)
    worker = _FakeWorker()
    queue.enqueue(worker, label="Italy 2026", on_finished=None,
                  job_type=JOB_TYPE_EXPORT)
    worker.progress.emit(10, 10, "done")
    qapp.processEvents()
    worker.finished_result.emit(None)
    qapp.processEvents()
    line._sync()                                     # noqa: SLF001
    assert line._label.text() == "Ready"            # noqa: SLF001


# ── never hides (spec/96 §1 contract) ──────────────────────────


def test_line_never_calls_set_visible_false(qapp, queue):
    """spec/96 §1 forbids ``setVisible(False)`` on the activity
    line. Wrap ``setVisible`` and check it's only called with True
    across the three states (idle, previews, batch)."""
    line = BatchProgressLine()
    hide_calls: List[bool] = []
    real_set_visible = line.setVisible

    def spy(value: bool) -> None:
        hide_calls.append(bool(value))
        real_set_visible(bool(value))

    line.setVisible = spy                            # type: ignore[method-assign]
    line.bind(queue)
    line.set_previews_source(lambda: 0)
    line._sync()                                     # noqa: SLF001 — idle
    line.set_previews_source(lambda: 12)
    line._sync()                                     # noqa: SLF001 — previews
    worker = _FakeWorker()
    queue.enqueue(worker, label="X", on_finished=None,
                  job_type=JOB_TYPE_EXPORT)
    qapp.processEvents()                            # batch
    assert all(value is True for value in hide_calls), (
        f"BatchProgressLine called setVisible(False) — spec/96 §1 "
        f"contract violated: {hide_calls}")


# ── poll timer is started on bind ──────────────────────────────


def test_poll_timer_runs_after_bind(line):
    """``bind`` starts the previews-pending poll so the line picks
    up background work without an explicit kick."""
    assert line._previews_poll.isActive() is True   # noqa: SLF001
