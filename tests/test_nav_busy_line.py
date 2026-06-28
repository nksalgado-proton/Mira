"""The app activity line's transient "busy" cue for surface transitions.

spec/96 (Nelson 2026-06-28) — a wait CURSOR can't repaint while the GUI
thread is busy on Windows, so navigation feedback rides the already-
painted activity line instead. BatchProgressLine.set_busy / clear_busy
drive a transient message (below a running batch job, above previews /
Ready), and the shell's @_nav_busy decorator shows it around a
navigation handler — and is signature-safe for Qt slots.
"""
from __future__ import annotations

import pytest

try:
    from PyQt6.QtWidgets import QApplication
except ImportError:                                          # pragma: no cover
    QApplication = None

from mira.ui.shell.batch_queue import BatchJobQueue, BatchProgressLine


@pytest.fixture
def qapp():
    if QApplication is None:
        pytest.skip("PyQt6 not installed")
    yield QApplication.instance() or QApplication([])


def test_set_busy_shows_transient_then_clears(qapp):
    line = BatchProgressLine()
    line.bind(BatchJobQueue())
    try:
        line.set_busy("Loading…")
        assert line._label.text() == "Loading…"
        assert line._bar.isVisible()
        line.clear_busy()
        # Falls back to the idle "Ready" readout (no batch, no previews).
        assert line._label.text() == "Ready"
    finally:
        line.deleteLater()


def test_running_batch_job_outranks_transient(qapp):
    """A real batch job's readout wins over the navigation cue."""
    class _Worker:
        def __init__(self):
            from PyQt6.QtCore import pyqtSignal  # noqa: F401
        def start(self):
            pass
        def cancel(self):
            pass

    # Minimal queue stub whose `idle` is False and reports a progress
    # tuple, so _sync takes the batch-job branch.
    class _Q(BatchJobQueue):
        @property
        def idle(self):
            return False

        @property
        def progress(self):
            return (2, 5, "")

        @property
        def running_job_type(self):
            return "export"

        @property
        def queued_count(self):
            return 0

        @property
        def file_fraction(self):
            return None

    line = BatchProgressLine()
    line.bind(_Q())
    try:
        line.set_busy("Loading…")
        # Batch-job branch wins: label reads the export readout, not the cue.
        assert "of 5" in line._label.text()
        assert "Loading" not in line._label.text()
    finally:
        line.deleteLater()


def test_nav_busy_decorator_is_signature_safe_and_toggles_line(qapp):
    """@_nav_busy must (a) tolerate an extra Qt signal arg without
    raising, and (b) set the line busy during the call and clear it
    after."""
    from mira.ui.shell.main_window import _nav_busy

    line = BatchProgressLine()
    line.bind(BatchJobQueue())
    seen = []

    class _Host:
        def __init__(self):
            self.batch_line = line

        @_nav_busy
        def on_back(self):
            # Inside the handler the line shows the busy cue.
            seen.append(line._label.text())

    host = _Host()
    try:
        host.on_back(False)        # Qt-style spurious `checked` bool
        assert seen == ["Loading…"]          # busy during the call
        assert line._label.text() == "Ready"  # cleared after
    finally:
        line.deleteLater()
