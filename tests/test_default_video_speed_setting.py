"""spec/138 §2D — the global ``Settings.default_video_speed`` seeds the
sticky session rate.

  * Setting defined on the ``Settings`` model with default ``1.0``;
    round-trips through ``SettingsRepo``.
  * ``PhotoViewport._video_rate`` initialises from
    :meth:`PhotoViewport._initial_video_rate` which reads the setting.
  * ``VideoWorkshopBar``'s speed combo seeds from the same setting on
    construction.
  * Both are tolerant of a missing settings file (the headless /
    pre-first-launch case) — fall back to ``1.0``.
"""
from __future__ import annotations

import pytest

from mira.settings import Settings, SettingsRepo
from mira.ui.media.photo_viewport import PhotoViewport
from mira.ui.media.transport_bar import VideoWorkshopBar


# ── Settings model ──────────────────────────────────────────────────


def test_default_video_speed_default_is_1x():
    """Fresh ``Settings()`` ships 1.0 (today's behaviour preserved
    for users who never visit the new control)."""
    assert Settings().default_video_speed == pytest.approx(1.0)


def test_default_video_speed_round_trips_through_repo(tmp_path):
    """A user-picked value (e.g. 1.5) persists through save/load."""
    repo = SettingsRepo(tmp_path / "settings.json")
    repo.update(default_video_speed=1.5)
    assert repo.load().default_video_speed == pytest.approx(1.5)


def test_default_video_speed_accepts_all_combo_tiers(tmp_path):
    """Every combo tier round-trips — pin the contract so the dialog
    options + the model defaults can't drift apart."""
    for tier in (0.25, 0.5, 1.0, 1.5, 2.0):
        repo = SettingsRepo(tmp_path / f"settings_{tier}.json")
        repo.update(default_video_speed=tier)
        assert repo.load().default_video_speed == pytest.approx(tier)


# ── PhotoViewport seed ──────────────────────────────────────────────


def test_initial_video_rate_default_is_1x(qapp, monkeypatch):
    """With a fresh-defaults settings load (stubbed so the dev
    machine's real settings.rebuild.json doesn't bleed in), a
    viewport's ``_video_rate`` opens at 1.0."""
    class _StubSettings:
        default_video_speed = 1.0
    monkeypatch.setattr(
        "mira.settings.repo.SettingsRepo.load",
        lambda self: _StubSettings(),
    )
    vp = PhotoViewport()
    try:
        assert vp.video_playback_rate() == pytest.approx(1.0)
    finally:
        vp.deleteLater()


def test_initial_video_rate_reads_from_settings(qapp, monkeypatch):
    """Tests/headless callers can inject a known seed by
    monkeypatching :meth:`PhotoViewport._initial_video_rate`. The
    viewport's ``_video_rate`` opens at the patched value."""
    monkeypatch.setattr(
        PhotoViewport, "_initial_video_rate", staticmethod(lambda: 1.5))
    vp = PhotoViewport()
    try:
        assert vp.video_playback_rate() == pytest.approx(1.5), (
            "spec/138 §2B: ``_video_rate`` must seed from "
            "``Settings.default_video_speed`` (via "
            "``_initial_video_rate``)"
        )
    finally:
        vp.deleteLater()


def test_initial_video_rate_swallows_settings_load_failure(qapp, monkeypatch):
    """A broken settings file MUST NOT crash the viewport — the
    helper falls back to 1.0 so the splash + first surface still
    work on a fresh install with no settings yet."""
    def _explode():
        raise RuntimeError("simulated settings read failure")
    # Patch the inner SettingsRepo so the helper's try/except path runs.
    monkeypatch.setattr(
        "mira.settings.repo.SettingsRepo.load", lambda self: _explode())
    assert PhotoViewport._initial_video_rate() == pytest.approx(1.0)


# ── Transport bar combo seed ────────────────────────────────────────


def test_speed_combo_seeds_to_default_video_speed(qapp, monkeypatch):
    """The combo's default text comes from
    ``Settings.default_video_speed``, NOT a hardcoded 1×. Patch the
    repo's load to return 1.5 and confirm a fresh bar opens showing
    1.5×."""
    class _StubSettings:
        default_video_speed = 1.5
    monkeypatch.setattr(
        "mira.settings.repo.SettingsRepo.load",
        lambda self: _StubSettings(),
    )
    bar = VideoWorkshopBar()
    try:
        assert bar.speed_combo.currentText() == "1.5×", (
            "spec/138 §2D: combo default text must come from "
            f"Settings.default_video_speed; got {bar.speed_combo.currentText()!r}"
        )
    finally:
        bar.deleteLater()


def test_speed_combo_seed_does_not_emit_speed_changed(qapp, monkeypatch):
    """The seed itself happens under ``blockSignals`` — constructing
    the bar MUST NOT fire ``speed_changed`` (otherwise every host
    construction would briefly drive the engine to whatever the
    settings default happens to be)."""
    class _StubSettings:
        default_video_speed = 0.5
    monkeypatch.setattr(
        "mira.settings.repo.SettingsRepo.load",
        lambda self: _StubSettings(),
    )
    bar = VideoWorkshopBar()
    try:
        emitted: list[float] = []

        def _record(rate: float) -> None:
            emitted.append(rate)

        bar.speed_changed.connect(_record)
        # The seed happens in __init__ — by the time we get here it's
        # done. Trigger a no-op repolish to flush any queued signal.
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()
        try:
            assert emitted == [], (
                f"spec/138 §2D: combo seed must use blockSignals; "
                f"got speed_changed emissions {emitted}"
            )
        finally:
            try:
                bar.speed_changed.disconnect(_record)
            except (TypeError, RuntimeError):
                pass
    finally:
        bar.deleteLater()


def test_speed_combo_seed_swallows_settings_load_failure(qapp, monkeypatch):
    """A broken/missing settings file MUST NOT crash bar construction
    — the seed falls back to 1×."""
    def _explode(self):
        raise RuntimeError("simulated settings read failure")
    monkeypatch.setattr(
        "mira.settings.repo.SettingsRepo.load", _explode)
    bar = VideoWorkshopBar()
    try:
        assert bar.speed_combo.currentText() == "1×"
    finally:
        bar.deleteLater()
