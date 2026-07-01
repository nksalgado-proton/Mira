"""spec/130 §1 — Esc / Back / hide leaves stop the video player so
audio doesn't bleed past the surface.

PhotoViewport's ``shutdown_video`` is the canonical stop+release;
prior to spec/130 the Picker's ``_on_back`` only stopped the
cluster-sweep ``_film_timer`` and left the player playing. Same shape
on the Editor host.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mira.gateway import EventsIndex, Gateway
from mira.settings.repo import SettingsRepo
from mira.ui.media.photo_viewport import ViewportItem
from mira.ui.pages.picker_page import PickerPage


@pytest.fixture()
def picker_page(qapp, tmp_path):
    settings = SettingsRepo(tmp_path / "settings.json")
    index = EventsIndex(tmp_path / "events_index.json")
    gw = Gateway(settings=settings, index=index)
    p = PickerPage(gw)
    yield p
    # Some tests monkeypatch viewport.shutdown_video to a no-op stub.
    # When the page is subsequently hidden / deleted, the real
    # QMediaPlayer state stays armed and a queued teardown callback
    # trips on a callable that was replaced with None-ish state. Drain
    # the event queue before deleteLater so any pending signal handler
    # runs while the objects are still alive, then delete.
    from PyQt6.QtWidgets import QApplication
    QApplication.processEvents()
    p.deleteLater()
    QApplication.processEvents()


def _cull(item_id: str, kind: str, path: Path) -> SimpleNamespace:
    return SimpleNamespace(item_id=item_id, path=path, kind=kind)


def _land_on_video(page: PickerPage, tmp_path) -> None:
    video = _cull("v1", "video", tmp_path / "v1.mp4")
    page._items = [video]
    page._state = {video.item_id: None}
    page.viewport.set_items(
        [ViewportItem(path=video.path, kind="video", payload=video)], 0)


# ── Picker ──────────────────────────────────────────────────────────────


def test_picker_back_calls_shutdown_video(picker_page, tmp_path):
    """``_on_back`` (the Esc / Back handler) must call
    ``viewport.shutdown_video()`` so the player stops + the source
    clears. Without this fix the audio bleeds off the surface."""
    called: list[bool] = []
    picker_page.viewport.shutdown_video = (   # type: ignore[assignment]
        lambda: called.append(True))
    _land_on_video(picker_page, tmp_path)
    picker_page._on_back()
    assert called == [True]


def test_picker_esc_calls_shutdown_video(picker_page, tmp_path):
    """Esc routes through ``_on_esc`` → ``_on_back`` (when not in
    fullscreen); the same shutdown_video call fires."""
    called: list[bool] = []
    picker_page.viewport.shutdown_video = (   # type: ignore[assignment]
        lambda: called.append(True))
    _land_on_video(picker_page, tmp_path)
    picker_page._on_esc()
    assert called == [True]


def test_picker_hide_event_calls_shutdown_video(picker_page, tmp_path):
    """spec/130 belt-and-braces — any path that hides the Picker
    (programmatic navigation, page-stack swap, window close) stops
    the video. ``hideEvent`` is the catch-all."""
    called: list[bool] = []
    picker_page.viewport.shutdown_video = (   # type: ignore[assignment]
        lambda: called.append(True))
    picker_page.show()
    _land_on_video(picker_page, tmp_path)
    picker_page.hide()
    assert called  # at least once — _on_back may also fire it


def test_picker_back_actually_stops_real_video_state(
    picker_page, tmp_path,
):
    """End-to-end: ``_on_back`` flips the viewport's tracked playback
    state to "not playing" and clears the armed source — the real
    ``shutdown_video`` did its job (no monkeypatch)."""
    vp = picker_page.viewport
    # Simulate an armed + playing video without launching QtMultimedia.
    vp._video_armed = Path("c:/v.mp4")
    vp._video_playing = True
    picker_page._on_back()
    assert vp.video_is_playing() is False
    assert vp._video_armed is None


# ── Editor ──────────────────────────────────────────────────────────────


def test_editor_back_calls_shutdown_video(qapp, tmp_path):
    """Same contract on the Editor host (spec/130 — audit the Editor
    for the same leave path)."""
    from mira.ui.pages.editor_page import EditorPage
    settings = SettingsRepo(tmp_path / "settings.json")
    index = EventsIndex(tmp_path / "events_index.json")
    gw = Gateway(settings=settings, index=index)
    page = EditorPage(gw)
    try:
        called: list[bool] = []
        page._viewport.shutdown_video = (     # type: ignore[assignment]
            lambda: called.append(True))
        page._on_back()
        assert called == [True]
    finally:
        page.deleteLater()


def test_editor_hide_event_calls_shutdown_video(qapp, tmp_path):
    """Editor's belt-and-braces hideEvent fires shutdown_video too."""
    from mira.ui.pages.editor_page import EditorPage
    settings = SettingsRepo(tmp_path / "settings.json")
    index = EventsIndex(tmp_path / "events_index.json")
    gw = Gateway(settings=settings, index=index)
    page = EditorPage(gw)
    try:
        called: list[bool] = []
        page._viewport.shutdown_video = (     # type: ignore[assignment]
            lambda: called.append(True))
        page.show()
        page.hide()
        assert called
    finally:
        page.deleteLater()
