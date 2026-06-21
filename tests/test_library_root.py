"""Tests for ``core.library_root`` — spec/76 §B.4 library-root resolution.

Every test points :func:`bootstrap_pointer_path` at a tempdir-local
path so the real ``%LOCALAPPDATA%\\Mira\\config.json`` is never
touched. Similarly, ``MIRA_DATA_DIR`` is cleared in the fixture so
the env override doesn't leak across tests.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core import library_root
from core.library_root import (
    MARKER_FILENAME,
    MIRA_DIRNAME,
    POINTER_FILENAME,
    is_library_shape,
    legacy_user_data_dir,
    migrate_legacy_data_dir,
    read_pointer,
    resolve_library_root,
    scaffold_library,
    write_pointer,
    clear_pointer,
)


@pytest.fixture
def isolate(tmp_path: Path, monkeypatch):
    """Redirect the bootstrap pointer to a tempdir-local file AND
    clear ``MIRA_DATA_DIR``. Returns ``(root, pointer_path)`` so
    callers can scaffold + verify without globals.

    Two sub-trees, both inside ``tmp_path``:

    * ``<tmp>/library`` — where the library would live (user-chosen).
    * ``<tmp>/pointer/config.json`` — where the bootstrap pointer
      would live (the ``%LOCALAPPDATA%\\Mira\\`` analogue).
    """
    monkeypatch.delenv("MIRA_DATA_DIR", raising=False)
    pointer_dir = tmp_path / "pointer"
    pointer_path = pointer_dir / POINTER_FILENAME
    monkeypatch.setattr(library_root, "bootstrap_pointer_path",
                        lambda: pointer_path)
    return tmp_path / "library", pointer_path


# ── Bootstrap pointer round-trip ───────────────────────────────────


def test_read_pointer_returns_none_when_missing(isolate):
    """No pointer file → read returns None (not an error)."""
    root, pointer_path = isolate
    assert not pointer_path.exists()
    assert read_pointer() is None


def test_write_pointer_then_read_pointer_round_trip(isolate):
    """write → read returns the same Path."""
    root, pointer_path = isolate
    root.mkdir(parents=True)

    write_pointer(root)
    assert pointer_path.is_file()
    out = read_pointer()
    assert out is not None
    assert Path(out) == root


def test_write_pointer_is_atomic(isolate):
    """The tmp file from the write-then-rename is gone after the call."""
    root, pointer_path = isolate
    root.mkdir(parents=True)
    write_pointer(root)
    # No leftover .tmp sibling.
    tmp = pointer_path.with_suffix(pointer_path.suffix + ".tmp")
    assert not tmp.exists()


def test_write_pointer_creates_parent_directory(isolate):
    """Pointer's parent dir is created on demand (mirrors AppData)."""
    root, pointer_path = isolate
    root.mkdir(parents=True)
    assert not pointer_path.parent.exists()
    write_pointer(root)
    assert pointer_path.parent.is_dir()


def test_clear_pointer_removes_file(isolate):
    """clear_pointer removes the pointer; missing-file is no-op."""
    root, pointer_path = isolate
    root.mkdir(parents=True)
    assert clear_pointer() is False        # nothing to remove
    write_pointer(root)
    assert pointer_path.exists()
    assert clear_pointer() is True
    assert not pointer_path.exists()
    assert clear_pointer() is False        # idempotent


def test_read_pointer_handles_corrupt_json(isolate):
    """Malformed pointer → read returns None, logs the warning, doesn't
    raise. The caller will re-prompt via first-run."""
    root, pointer_path = isolate
    pointer_path.parent.mkdir(parents=True)
    pointer_path.write_text("{not valid json", encoding="utf-8")
    assert read_pointer() is None


def test_read_pointer_handles_non_object_payload(isolate):
    """Pointer payload that is a JSON list, not an object → None."""
    root, pointer_path = isolate
    pointer_path.parent.mkdir(parents=True)
    pointer_path.write_text("[]", encoding="utf-8")
    assert read_pointer() is None


def test_read_pointer_handles_missing_library_root_key(isolate):
    """A JSON object without ``library_root`` → None (not a parse error)."""
    root, pointer_path = isolate
    pointer_path.parent.mkdir(parents=True)
    pointer_path.write_text("{\"something_else\": 1}", encoding="utf-8")
    assert read_pointer() is None


# ── Resolution order ──────────────────────────────────────────────


def test_resolve_library_root_returns_none_at_first_run(isolate):
    """No env, no pointer → resolve returns None."""
    assert resolve_library_root() is None


def test_resolve_library_root_uses_pointer_when_present(isolate):
    """A written pointer is used when env is unset."""
    root, _ = isolate
    root.mkdir(parents=True)
    write_pointer(root)
    out = resolve_library_root()
    assert out is not None
    assert Path(out) == root


def test_resolve_library_root_env_override_wins(isolate, monkeypatch):
    """``MIRA_DATA_DIR`` overrides everything, even a written pointer."""
    root, _ = isolate
    root.mkdir(parents=True)
    write_pointer(root)
    override = root.parent / "other"
    monkeypatch.setenv("MIRA_DATA_DIR", str(override))
    out = resolve_library_root()
    assert Path(out) == override


# ── Scaffold ──────────────────────────────────────────────────────


def test_scaffold_library_creates_shape(isolate):
    """scaffold creates root, .mira/, Collections/, Recipes/, marker."""
    root, _ = isolate
    scaffold_library(root)
    assert root.is_dir()
    assert (root / MIRA_DIRNAME).is_dir()
    assert (root / "Collections").is_dir()
    assert (root / "Recipes").is_dir()
    assert (root / MIRA_DIRNAME / MARKER_FILENAME).is_file()


def test_scaffold_library_marker_is_valid_json(isolate):
    """The marker is a JSON object with the documented fields."""
    root, _ = isolate
    scaffold_library(root)
    marker = root / MIRA_DIRNAME / MARKER_FILENAME
    blob = json.loads(marker.read_text(encoding="utf-8"))
    assert blob["kind"] == "mira_library"
    assert "schema_version" in blob


def test_scaffold_library_is_idempotent(isolate):
    """Running scaffold twice on the same root succeeds and refreshes
    the marker. Existing content (e.g. a Collection JSON the user
    pre-wrote) is preserved."""
    root, _ = isolate
    scaffold_library(root)
    (root / "Collections" / "existing.json").write_text(
        "{\"keep\": true}", encoding="utf-8")
    scaffold_library(root)
    assert (root / "Collections" / "existing.json").is_file()


def test_is_library_shape_recognises_scaffold(isolate):
    """A scaffolded folder probes True; an empty folder probes False."""
    root, _ = isolate
    empty = root.parent / "empty"
    empty.mkdir(parents=True)
    assert is_library_shape(empty) is False
    scaffold_library(root)
    assert is_library_shape(root) is True


# ── Reinstall / OS-wipe recovery ──────────────────────────────────


def test_open_existing_after_pointer_loss(isolate):
    """The recovery story (spec/76 §B.4): a Windows reinstall wipes
    only the bootstrap pointer; the library on disk is intact. Open
    Existing re-creates the pointer and resolution proceeds."""
    root, pointer_path = isolate
    scaffold_library(root)
    write_pointer(root)
    # Simulate a reinstall — wipe the pointer.
    clear_pointer()
    assert resolve_library_root() is None
    # The library is still there.
    assert is_library_shape(root)
    # Open Existing flow: probe + write pointer → resolution works again.
    assert is_library_shape(root)
    write_pointer(root)
    out = resolve_library_root()
    assert out is not None and Path(out) == root


# ── Migration: legacy user-data dir → <root>/.mira/ ───────────────


@pytest.fixture
def legacy_dir(tmp_path: Path, monkeypatch):
    """A fake legacy user-data dir with two files + one subdir.

    We monkeypatch :func:`legacy_user_data_dir` to point at this
    location, since the real implementation reads
    ``%LOCALAPPDATA%`` which we don't want to touch.
    """
    legacy = tmp_path / "legacy"
    legacy.mkdir(parents=True)
    (legacy / "settings.json").write_text("{}", encoding="utf-8")
    (legacy / "events_index.json").write_text(
        "{\"events\": []}", encoding="utf-8")
    (legacy / "logs").mkdir()
    (legacy / "logs" / "yesterday.log").write_text(
        "log entry", encoding="utf-8")
    monkeypatch.setattr(library_root, "legacy_user_data_dir",
                        lambda: legacy)
    return legacy


def test_migrate_legacy_data_dir_copies_into_dot_mira(isolate, legacy_dir):
    """First run: migrate copies settings, events index, and logs/ into
    ``<root>/.mira/``."""
    root, _ = isolate
    scaffold_library(root)
    moved = migrate_legacy_data_dir(root)
    assert moved is True
    dot_mira = root / MIRA_DIRNAME
    assert (dot_mira / "settings.json").is_file()
    assert (dot_mira / "events_index.json").is_file()
    assert (dot_mira / "logs" / "yesterday.log").is_file()


def test_migrate_legacy_data_dir_is_idempotent(isolate, legacy_dir):
    """Second call → False; nothing further copied; existing files
    untouched."""
    root, _ = isolate
    scaffold_library(root)
    assert migrate_legacy_data_dir(root) is True
    # Mark one of the migrated files so we can detect a re-copy.
    settings = root / MIRA_DIRNAME / "settings.json"
    settings.write_text("{\"already_set\": true}", encoding="utf-8")
    assert migrate_legacy_data_dir(root) is False
    # Untouched.
    assert "already_set" in settings.read_text(encoding="utf-8")


def test_migrate_legacy_data_dir_leaves_legacy_in_place(isolate, legacy_dir):
    """The legacy directory is non-destructively read; a fallback git
    switch still has its original data."""
    root, _ = isolate
    scaffold_library(root)
    migrate_legacy_data_dir(root)
    assert (legacy_dir / "settings.json").is_file()
    assert (legacy_dir / "logs" / "yesterday.log").is_file()


def test_migrate_skips_when_legacy_absent(isolate, tmp_path, monkeypatch):
    """No legacy dir → False; not an error."""
    root, _ = isolate
    scaffold_library(root)
    monkeypatch.setattr(library_root, "legacy_user_data_dir",
                        lambda: tmp_path / "never_existed")
    assert migrate_legacy_data_dir(root) is False


def test_migrate_skips_when_env_override_set(
        isolate, legacy_dir, monkeypatch):
    """``MIRA_DATA_DIR`` set → migration is skipped (explicit override,
    caller owns the path)."""
    root, _ = isolate
    scaffold_library(root)
    monkeypatch.setenv("MIRA_DATA_DIR", str(root / MIRA_DIRNAME))
    assert migrate_legacy_data_dir(root) is False


def test_migrate_skips_when_destination_has_content(
        isolate, legacy_dir):
    """An existing live install in <root>/.mira/ blocks migration —
    we never overwrite live data."""
    root, _ = isolate
    scaffold_library(root)
    # Pretend a live install already wrote a mira.db.
    (root / MIRA_DIRNAME / "mira.db").write_bytes(b"\x00" * 16)
    assert migrate_legacy_data_dir(root) is False


def test_migrate_skips_when_legacy_is_destination(
        isolate, monkeypatch, tmp_path):
    """If the user pointed the library at the legacy AppData folder
    itself, migration is a no-op (legacy == <root>/.mira/)."""
    legacy = tmp_path / "library" / MIRA_DIRNAME
    legacy.mkdir(parents=True)
    (legacy / "settings.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(library_root, "legacy_user_data_dir",
                        lambda: legacy)
    root = tmp_path / "library"
    # Don't scaffold (would touch the same folder); migration handles
    # the empty .mira-dir case via the equality short-circuit.
    assert migrate_legacy_data_dir(root) is False


def test_marker_file_alone_is_treated_as_empty(isolate, legacy_dir):
    """A freshly-scaffolded library has only marker.json in .mira/ —
    that doesn't count as "already populated"."""
    root, _ = isolate
    scaffold_library(root)
    # Only marker.json should be present at this point.
    contents = list((root / MIRA_DIRNAME).iterdir())
    assert {p.name for p in contents} == {MARKER_FILENAME}
    # Migration proceeds.
    assert migrate_legacy_data_dir(root) is True
