"""Tests for core.removable_drive — Stage D wipe gate.

Pure unit tests: the Windows ``GetDriveTypeW`` call is monkeypatched
so the suite runs on any platform.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core import removable_drive
from core.removable_drive import DRIVE_REMOVABLE, is_removable


def test_drive_removable_constant():
    """Lock the Win32 constant — the engine compares against this
    integer, so a typo would silently break the wipe gate."""
    assert DRIVE_REMOVABLE == 2


def test_non_windows_always_returns_false(monkeypatch):
    """On Linux/macOS/CI we can't tell, so the answer must always be
    False — never accidentally surface the wipe offer."""
    monkeypatch.setattr(sys, "platform", "linux")
    assert is_removable(Path("/anywhere")) is False
    monkeypatch.setattr(sys, "platform", "darwin")
    assert is_removable(Path("/anywhere")) is False


def test_returns_true_when_api_reports_removable(monkeypatch, tmp_path):
    """Patch ``_get_drive_type`` to return ``DRIVE_REMOVABLE`` →
    is_removable returns True. Forces ``sys.platform`` to ``win32`` so
    the function takes the Windows branch on a Linux test host."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(removable_drive, "_get_drive_type",
                        lambda drive: DRIVE_REMOVABLE)
    monkeypatch.setattr(removable_drive, "_resolve_drive_root",
                        lambda p: "E:\\")
    assert is_removable(tmp_path) is True


def test_returns_false_for_fixed_drive(monkeypatch, tmp_path):
    """DRIVE_FIXED (the internal SSD) must not offer wipe."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(removable_drive, "_get_drive_type",
                        lambda drive: 3)  # DRIVE_FIXED
    monkeypatch.setattr(removable_drive, "_resolve_drive_root",
                        lambda p: "C:\\")
    assert is_removable(tmp_path) is False


def test_returns_false_for_network_share(monkeypatch, tmp_path):
    """UNC path → no drive letter → False (network share, not a card)."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(removable_drive, "_resolve_drive_root",
                        lambda p: None)
    assert is_removable(tmp_path) is False


def test_fail_closed_on_api_error(monkeypatch, tmp_path):
    """A ctypes/Win32 failure must fall through to False — the wipe
    gate must NEVER open on the basis of an undefined API result."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(removable_drive, "_resolve_drive_root",
                        lambda p: "E:\\")

    def boom(drive):
        # _get_drive_type catches all exceptions internally and
        # returns 0 (DRIVE_UNKNOWN). Simulate that contract here.
        return 0

    monkeypatch.setattr(removable_drive, "_get_drive_type", boom)
    assert is_removable(tmp_path) is False


def test_internal_helper_swallows_ctypes_failure(monkeypatch):
    """_get_drive_type must return 0 (not raise) when ctypes itself
    blows up — confirms the fail-closed contract at the bottom layer."""
    monkeypatch.setattr(sys, "platform", "win32")

    # Force the import-of-ctypes branch to raise.
    def boom_import(name, *args, **kwargs):
        if name == "ctypes":
            raise ImportError("simulated")
        return __import__(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", boom_import)
    # Even on Windows with ctypes broken, expect DRIVE_UNKNOWN (0)
    assert removable_drive._get_drive_type("E:\\") == 0
