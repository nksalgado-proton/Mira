"""Tests for ``core.machine_settings`` — the per-install override
file that holds spec/95 §C `display_quality` (and any future keys
that must NOT roam between machines pointing at the same library).

Each test redirects :func:`machine_settings_path` to a tempdir-local
file so the real ``%LOCALAPPDATA%\\Mira\\machine.json`` is never
touched. The roaming-isolation tests ALSO build a fake
``settings.rebuild.json`` in a separate tempdir to confirm the
module never reaches into the roaming Settings store.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import machine_settings


@pytest.fixture
def isolate(tmp_path: Path, monkeypatch):
    """Point the module at ``<tmp>/machine.json`` for the duration
    of one test. Returns the path."""
    machine_file = tmp_path / "machine.json"
    monkeypatch.setattr(
        machine_settings, "machine_settings_path",
        lambda: machine_file)
    return machine_file


# ── default + round-trip ─────────────────────────────────────────


def test_default_is_balanced_when_file_missing(isolate):
    """No file on disk → :func:`read_display_quality` returns the
    documented default. (Spec/95 §C: balanced is the cheap-on-laptop
    sharp-on-4K-monitor pick.)"""
    assert not isolate.exists()
    assert machine_settings.read_display_quality() == "balanced"


def test_round_trip_high_then_back_to_balanced(isolate):
    """Write → read → write → read returns each persisted value."""
    machine_settings.write_display_quality("high")
    assert isolate.is_file()
    assert machine_settings.read_display_quality() == "high"
    machine_settings.write_display_quality("balanced")
    assert machine_settings.read_display_quality() == "balanced"


def test_write_persists_atomic_json_envelope(isolate):
    """The on-disk shape is a JSON object with the documented key —
    not a bare string, so other machine-local overrides can land
    later without breaking forward-compat readers."""
    machine_settings.write_display_quality("high")
    blob = json.loads(isolate.read_text(encoding="utf-8"))
    assert isinstance(blob, dict)
    assert blob.get("display_quality") == "high"


def test_write_preserves_other_keys_in_envelope(isolate):
    """Future per-install keys round-trip through the same file. If
    another module writes a key directly, ``write_display_quality``
    must NOT clobber it (spec/95 §C envelope contract)."""
    isolate.parent.mkdir(parents=True, exist_ok=True)
    isolate.write_text(
        json.dumps({"future_key": "future_value"}), encoding="utf-8")
    machine_settings.write_display_quality("high")
    blob = json.loads(isolate.read_text(encoding="utf-8"))
    assert blob.get("future_key") == "future_value"
    assert blob.get("display_quality") == "high"


# ── tolerant readers ─────────────────────────────────────────────


def test_corrupt_json_falls_back_to_default(isolate):
    """A malformed file is treated as "no override recorded" — the
    reader returns the default and does not raise. The bad bytes are
    NOT preserved (it's disposable per-install state)."""
    isolate.parent.mkdir(parents=True, exist_ok=True)
    isolate.write_text("{not valid json", encoding="utf-8")
    assert machine_settings.read_display_quality() == "balanced"


def test_non_object_payload_falls_back_to_default(isolate):
    """A JSON array (or string, or number) where an object was
    expected → default."""
    isolate.parent.mkdir(parents=True, exist_ok=True)
    isolate.write_text("[1, 2, 3]", encoding="utf-8")
    assert machine_settings.read_display_quality() == "balanced"


def test_unknown_enum_value_falls_back_to_default(isolate):
    """An out-of-band string in the file (a future tier that this
    build doesn't recognise, or a typo) → default. The closed enum
    keeps the viewport from honouring a value it can't map to a
    ceiling."""
    isolate.parent.mkdir(parents=True, exist_ok=True)
    isolate.write_text(
        json.dumps({"display_quality": "ultra"}), encoding="utf-8")
    assert machine_settings.read_display_quality() == "balanced"


def test_write_rejects_unknown_value(isolate):
    """The writer enforces the closed enum. A typo at the call site
    raises rather than silently producing an envelope a future
    reader can't honour."""
    with pytest.raises(ValueError):
        machine_settings.write_display_quality("ultra")
    # And nothing was written — the reader still returns the default.
    assert machine_settings.read_display_quality() == "balanced"


# ── roaming isolation ───────────────────────────────────────────


def test_module_never_touches_roaming_settings(
    tmp_path: Path, monkeypatch,
):
    """Spec/95 §C contract: ``machine_settings`` must NOT read or
    write the roaming ``settings.rebuild.json`` (which lives under
    ``<library_root>/.mira/`` and is shared between machines). This
    test plants a roaming settings file in a separate tempdir,
    redirects the machine.json to a third tempdir, exercises the
    module's full API, and asserts the roaming file's bytes are
    untouched.
    """
    library_root = tmp_path / "library"
    (library_root / ".mira").mkdir(parents=True)
    roaming = library_root / ".mira" / "settings.rebuild.json"
    roaming_bytes = json.dumps(
        {"schema_version": 2, "theme": "dark"}, indent=2,
    ).encode("utf-8")
    roaming.write_bytes(roaming_bytes)

    machine_file = tmp_path / "appconfig" / "machine.json"
    monkeypatch.setattr(
        machine_settings, "machine_settings_path",
        lambda: machine_file)

    # Exercise the full API.
    assert machine_settings.read_display_quality() == "balanced"
    machine_settings.write_display_quality("high")
    assert machine_settings.read_display_quality() == "high"

    # The roaming Settings file is byte-identical.
    assert roaming.read_bytes() == roaming_bytes
    # The machine.json landed in the OS-local config dir, NOT inside
    # the library root.
    assert machine_file.is_file()
    assert ".mira" not in machine_file.parts


def test_module_ignores_mira_data_dir_env(tmp_path: Path, monkeypatch):
    """``MIRA_DATA_DIR`` retargets the LIBRARY root for tests but
    must NEVER move the machine.json — the file is per-install,
    independent of which library this binary is currently open on.
    Spec/95 §C: "NEVER under MIRA_DATA_DIR"."""
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path / "fake-library"))
    # The default path resolver doesn't consult the env — so even with
    # the override set, the path lands at the OS-local config dir.
    p = machine_settings.machine_settings_path()
    assert "fake-library" not in p.parts


def test_path_sibling_to_bootstrap_pointer():
    """The override file lives in the same OS-local config dir as
    :func:`core.library_root.bootstrap_pointer_path`. Spec/95 §C
    says "beside the bootstrap pointer"; pin the contract so a
    future relocation of one drags the other."""
    from core.library_root import bootstrap_pointer_path
    pointer = bootstrap_pointer_path()
    machine = machine_settings.machine_settings_path()
    assert pointer.parent == machine.parent


# ── Qt-free / charter inv. 8 ─────────────────────────────────────


def test_no_qt_imports_in_machine_settings():
    """``core/`` stays GUI-free (charter inv. 8). Pin the rule
    directly so a stray Qt import gets flagged at suite time."""
    import core.machine_settings as mod
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "PyQt6" not in src
    assert "QtCore" not in src
    assert "QtWidgets" not in src
