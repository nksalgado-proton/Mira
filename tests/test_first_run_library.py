"""Tests for the spec/76 §B.4 first-run dialog.

The dialog is a Qt widget but the path-bearing logic underneath is
purely :mod:`core.library_root` — so we drive each path (Create / Open
/ Cancel) by stubbing :func:`QFileDialog.getExistingDirectory` and
asserting the bootstrap pointer ended up at the expected place.

The dialog never touches the real ``%LOCALAPPDATA%\\Mira\\config.json``
because every test redirects :func:`bootstrap_pointer_path` to a
tempdir-local file.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from core import library_root as _library_root
from core.library_root import POINTER_FILENAME, MIRA_DIRNAME, scaffold_library


@pytest.fixture
def isolate(tmp_path: Path, monkeypatch):
    """Redirect the bootstrap pointer + clear ``MIRA_DATA_DIR``."""
    monkeypatch.delenv("MIRA_DATA_DIR", raising=False)
    pointer_dir = tmp_path / "pointer"
    pointer_path = pointer_dir / POINTER_FILENAME
    monkeypatch.setattr(_library_root, "bootstrap_pointer_path",
                        lambda: pointer_path)
    # Also stub the legacy-data-dir lookup so the migration step in the
    # Create path doesn't reach into the real %LOCALAPPDATA%.
    legacy = tmp_path / "legacy_unused"
    monkeypatch.setattr(_library_root, "legacy_user_data_dir",
                        lambda: legacy)
    return tmp_path, pointer_path


def _stub_folder_picker(monkeypatch, chosen: Path) -> list:
    """Replace ``QFileDialog.getExistingDirectory`` with a stub that
    returns ``chosen`` (or ``""`` to simulate the user cancelling).
    Returns the calls list so tests can assert how many prompts fired.
    """
    calls: list = []

    def _fake(*args, **kwargs):
        calls.append((args, kwargs))
        return str(chosen) if chosen else ""

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", _fake)
    return calls


def _suppress_message_boxes(monkeypatch) -> list:
    """Stub the warning/critical message boxes so headless tests don't
    open them. Returns the calls list for assertions."""
    calls: list = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **kw: calls.append(("warning", a)) or 0)
    monkeypatch.setattr(QMessageBox, "critical",
                        lambda *a, **kw: calls.append(("critical", a)) or 0)
    return calls


# ── Create flow ───────────────────────────────────────────────────


def test_create_scaffolds_and_writes_pointer(qapp, isolate, monkeypatch):
    """Create → folder picker → scaffold + pointer + accept."""
    from mira.ui.wizard.first_run_library import FirstRunLibraryDialog
    tmp, pointer_path = isolate
    target = tmp / "library"
    _stub_folder_picker(monkeypatch, target)
    _suppress_message_boxes(monkeypatch)

    dlg = FirstRunLibraryDialog()
    dlg._on_create_clicked()                    # noqa: SLF001 — directly drive the slot
    assert dlg.chosen_root() == target
    assert pointer_path.is_file()
    # The pointer points at our chosen library root.
    assert _library_root.read_pointer() == target
    # And the scaffold ran (.mira/, Collections/, Recipes/).
    assert (target / MIRA_DIRNAME).is_dir()
    assert (target / "Collections").is_dir()
    assert (target / "Recipes").is_dir()


def test_create_rejects_non_empty_folder(qapp, isolate, monkeypatch):
    """Create on a folder with real content → warning, no pointer."""
    from mira.ui.wizard.first_run_library import FirstRunLibraryDialog
    tmp, pointer_path = isolate
    target = tmp / "not_empty"
    target.mkdir()
    (target / "important.dat").write_text("user's data", encoding="utf-8")
    _stub_folder_picker(monkeypatch, target)
    warnings = _suppress_message_boxes(monkeypatch)

    dlg = FirstRunLibraryDialog()
    dlg._on_create_clicked()                    # noqa: SLF001
    assert dlg.chosen_root() is None
    assert not pointer_path.exists()
    assert warnings and warnings[0][0] == "warning"


def test_create_tolerates_filesystem_cruft(qapp, isolate, monkeypatch):
    """``.DS_Store`` / ``Thumbs.db`` don't block Create — they're OS
    cruft the user didn't put there."""
    from mira.ui.wizard.first_run_library import FirstRunLibraryDialog
    tmp, _ = isolate
    target = tmp / "with_cruft"
    target.mkdir()
    (target / ".DS_Store").write_text("", encoding="utf-8")
    (target / "Thumbs.db").write_text("", encoding="utf-8")
    _stub_folder_picker(monkeypatch, target)
    _suppress_message_boxes(monkeypatch)

    dlg = FirstRunLibraryDialog()
    dlg._on_create_clicked()                    # noqa: SLF001
    assert dlg.chosen_root() == target


def test_create_skipped_when_folder_picker_cancelled(
        qapp, isolate, monkeypatch):
    """User cancels the native folder picker → no scaffold, no
    pointer, dialog stays open (reject not called)."""
    from mira.ui.wizard.first_run_library import FirstRunLibraryDialog
    tmp, pointer_path = isolate
    _stub_folder_picker(monkeypatch, Path(""))     # cancelled
    _suppress_message_boxes(monkeypatch)

    dlg = FirstRunLibraryDialog()
    dlg._on_create_clicked()                    # noqa: SLF001
    assert dlg.chosen_root() is None
    assert not pointer_path.exists()


# ── Open flow ─────────────────────────────────────────────────────


def test_open_validates_library_shape(qapp, isolate, monkeypatch):
    """Open on a scaffolded folder → pointer written, accept."""
    from mira.ui.wizard.first_run_library import FirstRunLibraryDialog
    tmp, pointer_path = isolate
    target = tmp / "existing"
    scaffold_library(target)
    _stub_folder_picker(monkeypatch, target)
    _suppress_message_boxes(monkeypatch)

    dlg = FirstRunLibraryDialog()
    dlg._on_open_clicked()                      # noqa: SLF001
    assert dlg.chosen_root() == target
    assert pointer_path.is_file()


def test_open_rejects_unscaffolded_folder(qapp, isolate, monkeypatch):
    """Open on a folder without .mira/ → warning, no pointer."""
    from mira.ui.wizard.first_run_library import FirstRunLibraryDialog
    tmp, pointer_path = isolate
    target = tmp / "not_a_library"
    target.mkdir()
    _stub_folder_picker(monkeypatch, target)
    warnings = _suppress_message_boxes(monkeypatch)

    dlg = FirstRunLibraryDialog()
    dlg._on_open_clicked()                      # noqa: SLF001
    assert dlg.chosen_root() is None
    assert not pointer_path.exists()
    assert warnings and warnings[0][0] == "warning"


# ── Cancel ────────────────────────────────────────────────────────


def test_cancel_button_rejects(qapp, isolate):
    """Cancel slot → dialog rejected → caller exits."""
    from mira.ui.wizard.first_run_library import FirstRunLibraryDialog
    dlg = FirstRunLibraryDialog()
    dlg._on_cancel()                            # noqa: SLF001
    assert dlg.chosen_root() is None
    # Cancel is reject(), not accept(); test exec() result-style.
    assert dlg.result() == 0


# ── Recovery: pointer exists, first-run shouldn't fire ────────────


def test_pointer_set_means_first_run_is_skipped(isolate):
    """A written pointer satisfies :func:`mira.paths.library_root` —
    the caller (app.py) skips showing the first-run dialog at all."""
    from mira.paths import library_root as _library_root_from_paths
    tmp, _ = isolate
    target = tmp / "established"
    scaffold_library(target)
    _library_root.write_pointer(target)
    assert _library_root_from_paths() == target


# ── Migration hook ────────────────────────────────────────────────


def test_create_runs_legacy_migration(qapp, isolate, monkeypatch):
    """When the legacy %LOCALAPPDATA%\\Mira dir has content, Create
    migrates it into ``<root>/.mira/`` as part of the same step."""
    from mira.ui.wizard.first_run_library import FirstRunLibraryDialog
    tmp, _ = isolate
    legacy = tmp / "legacy"
    legacy.mkdir()
    (legacy / "settings.json").write_text(
        "{\"photos_base_path\": \"D:/Photos\"}", encoding="utf-8")
    monkeypatch.setattr(_library_root, "legacy_user_data_dir",
                        lambda: legacy)

    target = tmp / "library"
    _stub_folder_picker(monkeypatch, target)
    _suppress_message_boxes(monkeypatch)

    dlg = FirstRunLibraryDialog()
    dlg._on_create_clicked()                    # noqa: SLF001
    assert dlg.chosen_root() == target
    assert dlg.did_migrate_legacy() is True
    assert (target / MIRA_DIRNAME / "settings.json").is_file()


# ── Validation hook — spec/76 §B.2 ────────────────────────────────


def test_create_aborts_on_validation_failure(qapp, isolate, monkeypatch):
    """Create against an unwritable / unreachable target: validation
    fails → critical dialog → no scaffold + no pointer."""
    from mira.ui.wizard.first_run_library import FirstRunLibraryDialog

    tmp, pointer_path = isolate
    target = tmp / "fresh_library"
    _stub_folder_picker(monkeypatch, target)
    msgs = _suppress_message_boxes(monkeypatch)

    # Force validation to fail with a specific reason.
    from core.library_root import ValidationResult
    monkeypatch.setattr(
        _library_root, "validate_root",
        lambda p: ValidationResult(
            ok=False, reasons=["share is unreachable"], warnings=[]),
    )

    dlg = FirstRunLibraryDialog()
    dlg._on_create_clicked()                    # noqa: SLF001

    assert dlg.chosen_root() is None
    assert not pointer_path.exists()
    # The user was told what went wrong.
    assert msgs and msgs[-1][0] == "critical"


def test_open_aborts_on_validation_failure(qapp, isolate, monkeypatch):
    """Open against an existing-but-stale library (share dropped):
    validation fails → critical dialog → no pointer."""
    from mira.ui.wizard.first_run_library import FirstRunLibraryDialog

    tmp, pointer_path = isolate
    target = tmp / "stale_library"
    scaffold_library(target)
    _stub_folder_picker(monkeypatch, target)
    msgs = _suppress_message_boxes(monkeypatch)

    from core.library_root import ValidationResult
    monkeypatch.setattr(
        _library_root, "validate_root",
        lambda p: ValidationResult(
            ok=False, reasons=["share unreachable"], warnings=[]),
    )

    dlg = FirstRunLibraryDialog()
    dlg._on_open_clicked()                      # noqa: SLF001

    assert dlg.chosen_root() is None
    assert not pointer_path.exists()
    assert msgs and msgs[-1][0] == "critical"


def test_create_proceeds_through_unc_positive_note(qapp, isolate, monkeypatch):
    """The "good — UNC" positive note is informational and NEVER
    blocks scaffold. The user is not prompted; the note just logs."""
    from mira.ui.wizard.first_run_library import FirstRunLibraryDialog

    tmp, pointer_path = isolate
    target = tmp / "library_unc"
    _stub_folder_picker(monkeypatch, target)
    _suppress_message_boxes(monkeypatch)

    from core.library_root import ValidationResult
    monkeypatch.setattr(
        _library_root, "validate_root",
        lambda p: ValidationResult(
            ok=True, reasons=[],
            warnings=["UNC path — good — multi-PC ready"]),
    )

    dlg = FirstRunLibraryDialog()
    dlg._on_create_clicked()                    # noqa: SLF001

    # Scaffold proceeded; positive note didn't ask for confirmation.
    assert dlg.chosen_root() == target
    assert pointer_path.is_file()
