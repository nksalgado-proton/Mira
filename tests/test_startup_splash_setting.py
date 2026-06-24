"""spec/136 — the ``startup_photo_splash`` Settings toggle.

Defaults on; ``True`` lets ``build_splash_pixmap`` consult the gateway
for a random exported frame, ``False`` skips straight to the bundled
mark (a user who screen-shares / prefers neutral boots).
"""
from __future__ import annotations

from pathlib import Path
import unittest.mock as mock

import pytest

from mira.settings import Settings, SettingsRepo
from mira.ui.shell import splash as splash_mod


def test_default_is_on():
    """Fresh ``Settings()`` ships the photo splash enabled."""
    s = Settings()
    assert s.startup_photo_splash is True


def test_setting_round_trip(tmp_path):
    """A flipped value persists through save/load through the repo."""
    repo = SettingsRepo(tmp_path / "settings.json")
    repo.update(startup_photo_splash=False)
    assert repo.load().startup_photo_splash is False


def test_build_splash_pixmap_honours_setting_on(qapp, tmp_path):
    """``photo_enabled=True`` (the default) invokes the picker."""
    calls = []

    def _fake_pick(gw, **kw):
        calls.append(True)
        return None        # picker returns None → bundled fallback path

    bundled = Path(__file__).resolve().parents[1] / "assets" / "icons" / "mira.png"
    with mock.patch.object(splash_mod, "pick_random_exported_frame", _fake_pick):
        splash_mod.build_splash_pixmap(
            object(),                 # gateway value is never .list_events()'d
            bundled_fallback=bundled,
            photo_enabled=True,
        )
    assert calls == [True]


def test_build_splash_pixmap_honours_setting_off(qapp, tmp_path):
    """``photo_enabled=False`` MUST NOT consult the picker (the user
    opted out — even the index read is skipped)."""

    def _fake_pick(gw, **kw):
        raise AssertionError(
            "spec/136: photo_enabled=False must not consult the picker")

    bundled = Path(__file__).resolve().parents[1] / "assets" / "icons" / "mira.png"
    with mock.patch.object(splash_mod, "pick_random_exported_frame", _fake_pick):
        pix = splash_mod.build_splash_pixmap(
            object(), bundled_fallback=bundled, photo_enabled=False)
    assert pix is not None and not pix.isNull()
