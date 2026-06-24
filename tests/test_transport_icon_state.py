"""spec/130 §2 — play/pause icon tracks the real state.

Convention: the button shows the action it will perform — pause glyph
when the video is playing, play glyph when paused. ``set_playing(True)``
selects the pause glyph; ``set_playing(False)`` selects the play glyph.

Re-sync on reveal: the bar is hidden on photos and revealed on videos;
if a clip is already playing when the bar is first shown, the host
pushes ``viewport.video_is_playing()`` so the bar isn't stuck on the
stale default play glyph.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mira.gateway import EventsIndex, Gateway
from mira.settings.repo import SettingsRepo
from mira.ui.base.surface import transport_button
from mira.ui.media.photo_viewport import ViewportItem
from mira.ui.media.transport_bar import VideoWorkshopBar
from mira.ui.pages.picker_page import PickerPage


@pytest.fixture()
def picker_page(qapp, tmp_path):
    settings = SettingsRepo(tmp_path / "settings.json")
    index = EventsIndex(tmp_path / "events_index.json")
    gw = Gateway(settings=settings, index=index)
    p = PickerPage(gw)
    yield p
    p.deleteLater()


def _cull(item_id, kind, path):
    return SimpleNamespace(item_id=item_id, path=path, kind=kind)


# ── Convention: button shows the action it WILL perform ──────────────


def test_transport_button_set_playing_true_uses_pause_glyph(qapp):
    """``set_playing(True)`` → the button paints the PAUSE glyph (so
    clicking it pauses); ``False`` → PLAY glyph (so clicking starts).
    The actual pixmap comes from the SVG family; this test pins the
    state flag the painter reads."""
    btn = transport_button()
    btn.set_playing(True)
    assert btn.is_playing() is True
    btn.set_playing(False)
    assert btn.is_playing() is False


def test_transport_button_pixmap_changes_on_state_flip(qapp):
    """End-to-end pin: the rendered pixmap actually swaps between the
    two glyphs when ``set_playing`` toggles. Compare by image content
    (``toImage()``) since QIcon.pixmap() synthesises a fresh QPixmap
    on every call (spec/77 §10.4 HiDPI rework)."""
    btn = transport_button()
    btn.set_playing(False)
    play_img = btn.icon().pixmap(20, 20).toImage()
    btn.set_playing(True)
    pause_img = btn.icon().pixmap(20, 20).toImage()
    assert play_img != pause_img, (
        "set_playing() must visibly swap the glyph (play ↔ pause)")
    # Round-trip back to confirm both directions paint.
    btn.set_playing(False)
    again = btn.icon().pixmap(20, 20).toImage()
    assert again == play_img


# ── Workshop bar end-to-end ──────────────────────────────────────────


def test_workshop_bar_set_playing_drives_play_button(qapp):
    """The shared widget's ``set_playing`` forwards to the underlying
    transport button — both surfaces (Picker + Editor) rely on this
    forwarding to flip the glyph from a single host call."""
    bar = VideoWorkshopBar()
    bar.set_playing(True)
    assert bar.play_btn.is_playing() is True
    bar.set_playing(False)
    assert bar.play_btn.is_playing() is False


# ── Re-sync on reveal ──────────────────────────────────────────────────


def test_picker_reveals_bar_with_current_playback_state(
    picker_page, tmp_path,
):
    """spec/130 §2 — when a video is landed and the bar is shown, the
    host pushes ``viewport.video_is_playing()`` so the glyph reflects
    REALITY at reveal time. Before the fix the Picker hardcoded
    ``set_playing(False)``; an already-playing auto-armed clip would
    show the stale play glyph while audio was already coming out."""
    picker_page.show()
    vp = picker_page.viewport
    # Simulate the auto-arm having started the player before the bar
    # ever appears (no real QtMultimedia — the tracked bool is what the
    # bar reads via set_playing).
    vp._video_playing = True
    vp._video_armed = Path(tmp_path / "v1.mp4")
    video = _cull("v1", "video", tmp_path / "v1.mp4")
    picker_page._items = [video]
    picker_page._state = {video.item_id: None}
    picker_page.viewport.set_items(
        [ViewportItem(path=video.path, kind="video", payload=video)], 0)
    # After landing on the video, the bar must mirror the live state.
    assert picker_page._transport_bar.isVisible() is True
    assert picker_page._transport_bar.play_btn.is_playing() is True


def test_picker_reveals_bar_with_paused_state_when_not_playing(
    picker_page, tmp_path,
):
    """The flip side: a freshly-armed-but-not-yet-playing clip shows
    the PLAY glyph at reveal (clicking starts playback)."""
    picker_page.show()
    video = _cull("v2", "video", tmp_path / "v2.mp4")
    picker_page._items = [video]
    picker_page._state = {video.item_id: None}
    picker_page.viewport.set_items(
        [ViewportItem(path=video.path, kind="video", payload=video)], 0)
    assert picker_page._transport_bar.isVisible() is True
    assert picker_page._transport_bar.play_btn.is_playing() is False
