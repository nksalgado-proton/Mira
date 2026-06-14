"""Tests for the reusable off-thread progress helper (spec/05 §4b)."""
from __future__ import annotations

from mira.ui.base.progress import run_with_progress


def test_runs_work_and_returns_result(qapp):
    def work(progress):
        progress(1, 2, "half")
        progress(2, 2, "done")
        return 42

    ok, value = run_with_progress(None, "Test", work, label="Working")
    assert ok is True
    assert value == 42


def test_carries_failure_back_as_text(qapp):
    def boom(_progress):
        raise ValueError("kaboom")

    ok, value = run_with_progress(None, "Test", boom)
    assert ok is False
    assert "kaboom" in value  # the traceback text comes back, never raised
