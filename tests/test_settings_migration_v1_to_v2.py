"""Tests for spec/82 §G settings migration — v1 → v2.

The Backups tab introduces ``event_backup_destination`` as the one
home for "where bundle exports land". Two legacy keys folded into
it:

* ``default_ssd_path`` — was meant for "default external backup
  destination" but never plugged into a feature.
* ``backup_on_quit_root`` — was the destination for the old
  incremental backup-on-quit (never wired either).

The migration moves either non-empty value into
``event_backup_destination`` and drops both legacy keys so they
can't drift apart. Whichever was set wins; a writer-set explicit
``event_backup_destination`` takes priority over both.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mira.settings.model import (
    SETTINGS_SCHEMA_VERSION,
    Settings,
    _v1_to_v2,
)
from mira.settings.repo import SettingsRepo


def test_v1_to_v2_moves_default_ssd_path(tmp_path):
    """A v1 settings file with default_ssd_path → the value lands in
    event_backup_destination + the legacy key is gone."""
    out = _v1_to_v2({
        "default_ssd_path": "D:/Backups",
    })
    assert out["event_backup_destination"] == "D:/Backups"
    assert "default_ssd_path" not in out


def test_v1_to_v2_moves_backup_on_quit_root(tmp_path):
    """The other legacy key folds in the same way."""
    out = _v1_to_v2({
        "backup_on_quit_root": "E:/Mirror",
    })
    assert out["event_backup_destination"] == "E:/Mirror"
    assert "backup_on_quit_root" not in out


def test_v1_to_v2_explicit_destination_wins(tmp_path):
    """If the user already set event_backup_destination explicitly,
    the migration must not stomp on it with a legacy value."""
    out = _v1_to_v2({
        "event_backup_destination": "F:/MyChoice",
        "default_ssd_path": "D:/Legacy",
        "backup_on_quit_root": "E:/AlsoLegacy",
    })
    assert out["event_backup_destination"] == "F:/MyChoice"
    assert "default_ssd_path" not in out
    assert "backup_on_quit_root" not in out


def test_v1_to_v2_keeps_other_keys_untouched():
    """Migration is targeted: the rest of the settings dict
    survives untouched."""
    payload = {
        "photos_base_path": "C:/Photos",
        "theme": "dark",
        "default_ssd_path": "D:/Backups",
    }
    out = _v1_to_v2(payload)
    assert out["photos_base_path"] == "C:/Photos"
    assert out["theme"] == "dark"


def test_v1_to_v2_empty_legacy_values_left_clean():
    """Both legacy keys present but empty → event_backup_destination
    stays empty, keys are dropped."""
    out = _v1_to_v2({
        "default_ssd_path": "",
        "backup_on_quit_root": "",
    })
    assert out.get("event_backup_destination", "") == ""
    assert "default_ssd_path" not in out
    assert "backup_on_quit_root" not in out


# ── End-to-end: loading a v1 file applies the migration ──────────


def test_settings_repo_migrates_a_v1_file_on_load(tmp_path):
    """Drop a v1 settings.json + load via SettingsRepo. The migrated
    Settings instance has event_backup_destination populated and the
    legacy keys gone."""
    settings_path = tmp_path / "settings.json"
    legacy_payload = {
        "schema_version": 1,
        "default_ssd_path": "D:/LegacyBackup",
        "photos_base_path": "C:/Photos",
    }
    settings_path.write_text(
        json.dumps(legacy_payload), encoding="utf-8")
    settings = SettingsRepo(settings_path).load()
    # ``event_backup_destination`` carries the migrated value; the
    # default_ssd_path attribute no longer exists on Settings.
    assert settings.event_backup_destination == "D:/LegacyBackup"
    assert not hasattr(settings, "default_ssd_path")


def test_schema_version_bumped_to_v2():
    """Sanity check: the model's bumped version matches the
    migration we just added."""
    assert SETTINGS_SCHEMA_VERSION == 2
