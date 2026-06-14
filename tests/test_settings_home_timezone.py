"""Tests for the home_timezone Settings row (spec/52 §8.2 calibration trigger
substrate).

The data field ``Settings.home_timezone`` defaults to the system UTC offset
on first launch and round-trips through ``SettingsRepo`` cleanly. This test
file pins:

1. The default at construct time matches the system TZ helper.
2. The new ``tz_picker`` widget kind binds to the field — reading the
   widget after ``load_settings`` yields the persisted value; writing
   through the binding round-trips into ``SettingsRepo``.

Together these guarantee spec/52 §8.2's calibration trigger has a real
``home_tz_minutes`` to compare against.
"""
from __future__ import annotations

from mira.settings.model import Settings, _system_tz_hours
from mira.settings.repo import SettingsRepo


def test_home_timezone_defaults_to_system_tz():
    s = Settings()
    assert s.home_timezone == _system_tz_hours()


def test_home_timezone_round_trips_through_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    repo = SettingsRepo()
    repo.update(home_timezone=-3.0)
    assert repo.load().home_timezone == -3.0
    repo.update(home_timezone=5.75)                     # Kathmandu, NOT 5.45
    assert repo.load().home_timezone == 5.75


def test_settings_dialog_renders_home_timezone_row(qapp, tmp_path, monkeypatch):
    """The tz_picker widget kind binds to home_timezone and round-trips a
    float value through the binding's read/write pair."""
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    from mira.ui.base.settings_dialog import SettingsDialog
    from mira.ui.base.tz_picker import TzPicker

    SettingsRepo().update(home_timezone=2.0)            # CEST-ish
    dlg = SettingsDialog()

    binding = next(b for b in dlg._bindings if b.key == "home_timezone")
    assert isinstance(binding.widget, TzPicker)
    assert binding.read() == 2.0

    binding.write(-3.0)
    dlg._on_apply()

    assert SettingsRepo().load().home_timezone == -3.0
