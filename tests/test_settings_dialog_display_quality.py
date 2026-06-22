"""spec/95 §C — the Settings dialog shim routes ``display_quality``
through ``core.machine_settings``, NOT through ``SettingsRepo``.

Pins the contract that:

* ``load_settings()`` reads ``display_quality`` from the per-install
  machine.json (default ``"balanced"`` when nothing's written yet).
* ``save_settings()`` pops ``display_quality`` out of the dict, writes
  it to machine.json via ``write_display_quality``, and lets the rest
  of the dict round-trip through ``SettingsRepo`` untouched.
* The roaming ``settings.rebuild.json`` does NOT acquire a
  ``display_quality`` field (would defeat the per-install goal).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import machine_settings
from mira.settings.repo import SETTINGS_FILENAME


@pytest.fixture
def isolate(tmp_path: Path, monkeypatch):
    """Redirect both the machine.json AND the roaming user_data_dir
    so neither test touches the user's real config."""
    # Machine.json — the per-install file.
    machine_file = tmp_path / "machine.json"
    monkeypatch.setattr(
        machine_settings, "machine_settings_path",
        lambda: machine_file)
    # Roaming settings.json — redirected via MIRA_DATA_DIR.
    roaming_dir = tmp_path / "library_root"
    roaming_dir.mkdir()
    monkeypatch.setenv("MIRA_DATA_DIR", str(roaming_dir))
    return machine_file, roaming_dir / SETTINGS_FILENAME


def test_load_settings_includes_machine_local_display_quality(
    isolate, qapp,
):
    """``load_settings`` projects ``display_quality`` from the
    per-install machine.json into the same dict the schema-driven
    dialog reads. Default is ``"balanced"`` when the file is absent."""
    from mira.ui.base.settings_dialog import load_settings
    data = load_settings()
    assert data.get("display_quality") == "balanced"


def test_load_settings_reads_existing_machine_local_value(
    isolate, qapp,
):
    """When the machine.json carries ``"high"``, ``load_settings``
    returns it. The dialog's combo then renders the right choice."""
    machine_settings.write_display_quality("high")
    from mira.ui.base.settings_dialog import load_settings
    data = load_settings()
    assert data.get("display_quality") == "high"


def test_save_settings_writes_display_quality_to_machine_file(
    isolate, qapp,
):
    """A dialog Save with ``display_quality='high'`` lands in the
    per-install machine.json — NOT in the roaming settings.json
    under user_data_dir."""
    machine_file, roaming_file = isolate
    from mira.ui.base.settings_dialog import load_settings, save_settings

    data = load_settings()
    data["display_quality"] = "high"
    # Also tweak one normal (roaming) setting so the round-trip
    # exercises both pipes.
    data["theme"] = "dark"
    save_settings(data)

    # machine.json got the display_quality.
    blob = json.loads(machine_file.read_text(encoding="utf-8"))
    assert blob.get("display_quality") == "high"
    # And settings.rebuild.json does NOT carry display_quality.
    roaming = json.loads(roaming_file.read_text(encoding="utf-8"))
    assert "display_quality" not in roaming
    # But it does carry the regular setting we touched.
    assert roaming.get("theme") == "dark"


def test_save_settings_ignores_invalid_display_quality_value(
    isolate, qapp,
):
    """A bogus ``display_quality`` doesn't blow up the save —
    the rest of the dict still persists; the machine.json keeps
    its previous (default) value. ``write_display_quality`` itself
    raises on the bad enum, but the shim guards against that to
    keep the dialog's Save bulletproof."""
    from mira.ui.base.settings_dialog import load_settings, save_settings

    data = load_settings()
    data["display_quality"] = "ultra"            # not in the closed enum
    data["theme"] = "light"
    save_settings(data)                           # must not raise

    # display_quality stays at default; the rest persisted.
    assert load_settings().get("display_quality") == "balanced"
    assert load_settings().get("theme") == "light"


def test_reset_to_defaults_resets_display_quality_too(
    isolate, qapp,
):
    """The Reset-to-defaults entry point clears the machine-local
    override too, so the user can't accidentally leave a
    ``"high"`` setting behind after Reset."""
    machine_settings.write_display_quality("high")
    from mira.ui.base.settings_dialog import (
        load_settings, reset_settings_to_defaults,
    )
    out = reset_settings_to_defaults()
    assert out.get("display_quality") == "balanced"
    assert load_settings().get("display_quality") == "balanced"
