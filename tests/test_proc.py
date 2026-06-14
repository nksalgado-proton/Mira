"""Tests for core.proc — window-suppressed subprocess launches.

docs/13 invariant #9 (frozen 2026-05-18, Nelson eyeball): every
bundled-exe spawn must suppress the child console window on Windows.
These run on Windows in CI here, so the win32 branch is exercised
for real; the cross-platform shape is asserted by inspecting the
kwargs the wrapper would pass.
"""

from __future__ import annotations

import subprocess
import sys

from core import proc


def test_no_window_kwargs_shape():
    kw = proc.no_window_kwargs()
    if sys.platform == "win32":
        assert "creationflags" in kw
        assert kw["creationflags"] & subprocess.CREATE_NO_WINDOW
    else:
        assert kw == {}


def test_no_window_kwargs_ors_in_existing():
    if sys.platform != "win32":
        return  # nothing to OR off-Windows
    base = subprocess.CREATE_NEW_PROCESS_GROUP
    kw = proc.no_window_kwargs(base)
    assert kw["creationflags"] & base
    assert kw["creationflags"] & subprocess.CREATE_NO_WINDOW


def test_run_injects_no_window(monkeypatch):
    seen: dict = {}

    def fake_run(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, "out", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cp = proc.run(["whatever"], capture_output=True, text=True)

    assert cp.returncode == 0 and cp.stdout == "out"
    assert seen["args"] == (["whatever"],)
    assert seen["kwargs"]["capture_output"] is True
    assert seen["kwargs"]["text"] is True
    if sys.platform == "win32":
        assert seen["kwargs"]["creationflags"] & \
            subprocess.CREATE_NO_WINDOW


def test_run_preserves_caller_creationflags(monkeypatch):
    if sys.platform != "win32":
        return
    seen: dict = {}
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: seen.update(k) or subprocess.CompletedProcess(
            a, 0, "", ""),
    )
    base = subprocess.CREATE_NEW_PROCESS_GROUP
    proc.run(["x"], creationflags=base)
    # OR-ed in, not clobbered.
    assert seen["creationflags"] & base
    assert seen["creationflags"] & subprocess.CREATE_NO_WINDOW


def test_run_executes_real_process():
    """End-to-end smoke: the wrapper actually runs a process and
    returns its output (and, on Windows, did so window-less)."""
    cp = proc.run(
        [sys.executable, "-c", "print('hello-proc')"],
        capture_output=True, text=True,
    )
    assert cp.returncode == 0
    assert "hello-proc" in cp.stdout


def test_exif_reader_uses_hidden_runner(monkeypatch, tmp_path):
    """The EXIF hot path (read every day/photo) must go through the
    window-suppressed runner — this is the flicker+slowness fix."""
    import core.exif_reader as er

    called: dict = {}

    def fake_run(*args, **kwargs):
        called["hit"] = True
        return subprocess.CompletedProcess(args, 0, "[]", "")

    monkeypatch.setattr(proc, "run", fake_run)
    # Force the exiftool-present branch regardless of bin/ layout.
    monkeypatch.setattr(er, "_get_exiftool_path",
                        lambda: tmp_path / "exiftool.exe")
    (tmp_path / "exiftool.exe").write_bytes(b"stub")
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x")

    out = er.read_exif_batch([f])
    assert out == []                 # fake returned '[]'
    assert called.get("hit") is True
