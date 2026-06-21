"""Tests for ``mira.paths`` — user-data dir + library-root resolution.

Three resolution paths live in :func:`mira.paths.user_data_dir`:

  1. ``MIRA_DATA_DIR`` env override (tests + custom deployments).
  2. ``library_root() / ".mira"`` when the bootstrap pointer is set.
  3. Legacy ``%LOCALAPPDATA%\\Mira`` / ``~/.mira`` fallback so first-run
     can still bootstrap.

This file pins each path independently. The pointer is redirected to
a tempdir-local file (mirroring ``test_library_root.py``) so the real
``%LOCALAPPDATA%\\Mira\\config.json`` is never touched.
"""
from __future__ import annotations

import platform
from pathlib import Path

import pytest

from core import library_root as _library_root
from core.library_root import (
    MIRA_DIRNAME,
    POINTER_FILENAME,
    scaffold_library,
    write_pointer,
)
from mira import paths


@pytest.fixture
def isolated_paths(tmp_path: Path, monkeypatch):
    """Redirect the bootstrap pointer to a tempdir-local file and
    clear ``MIRA_DATA_DIR``. Returns ``(library_dir, pointer_path)``.
    """
    monkeypatch.delenv("MIRA_DATA_DIR", raising=False)
    pointer_dir = tmp_path / "pointer"
    pointer_path = pointer_dir / POINTER_FILENAME
    monkeypatch.setattr(_library_root, "bootstrap_pointer_path",
                        lambda: pointer_path)
    return tmp_path / "library", pointer_path


# ── library_root() ────────────────────────────────────────────────


def test_library_root_returns_none_at_first_run(isolated_paths):
    """No pointer + no env → first-run → returns None."""
    assert paths.library_root() is None


def test_library_root_follows_pointer(isolated_paths):
    """Pointer set → library_root returns it."""
    root, _ = isolated_paths
    root.mkdir(parents=True)
    write_pointer(root)
    out = paths.library_root()
    assert out is not None and Path(out) == root


def test_library_root_env_override_wins(isolated_paths, monkeypatch):
    """``MIRA_DATA_DIR`` overrides the pointer for the library root."""
    root, _ = isolated_paths
    root.mkdir(parents=True)
    write_pointer(root)
    override = root.parent / "override"
    monkeypatch.setenv("MIRA_DATA_DIR", str(override))
    assert paths.library_root() == override


# ── user_data_dir() — env override path ───────────────────────────


def test_user_data_dir_uses_env_override_directly(tmp_path, monkeypatch):
    """``MIRA_DATA_DIR`` is returned verbatim (no ``.mira/`` subdir
    inserted) for backward compat with existing tests."""
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    out = paths.user_data_dir()
    assert out == tmp_path
    assert out.is_dir()                          # created on demand


# ── user_data_dir() — pointer-set path ────────────────────────────


def test_user_data_dir_returns_dot_mira_when_library_set(isolated_paths):
    """Pointer set → user_data_dir is ``<library_root>/.mira/`` (created
    on demand)."""
    root, _ = isolated_paths
    scaffold_library(root)
    write_pointer(root)
    out = paths.user_data_dir()
    assert out == root / MIRA_DIRNAME
    assert out.is_dir()


# ── user_data_dir() — legacy fallback (first-run before pointer) ──


def test_user_data_dir_falls_back_to_legacy_when_no_pointer(
        isolated_paths, monkeypatch):
    """No pointer + no env → returns the legacy AppData / ``~/.mira``
    location so the first-run wizard can read prior settings to seed
    the picker. We monkeypatch ``Path.home`` so the test doesn't touch
    the real AppData."""
    fake_home = isolated_paths[0].parent / "fake_home"
    fake_home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    out = paths.user_data_dir()
    if platform.system() == "Windows":
        assert out == fake_home / "AppData" / "Local" / "Mira"
    else:
        assert out == fake_home / ".mira"
    assert out.is_dir()
