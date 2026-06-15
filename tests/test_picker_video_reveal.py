"""Pins the unified Picker's video reveal (spec/70 row 11 folded into
07, Nelson 2026-06-15).

Contract:

* PickerPage embeds the :class:`VideoTransportBar` inside the
  ``BasePickSurface.compact_row`` slot. The slot itself stays VISIBLE
  on every item (the canvas position is invariant under photo↔video
  sweeps — Nelson: "the line where the transport buttons are placed
  has to exist (empty) when photos are displayed").
* The transport-bar widget INSIDE the slot is the toggle: shown when
  the viewport lands on a ``kind == "video"`` item (the "few transport
  buttons appear" moment), hidden on photos.
* Photo prefetch (``_spawn_exif_prefetch`` + the per-bucket
  ``photo_cache().set_event_context`` seed) skips video items so the
  proxy builder never tries ``PIL.Image.open`` on an MP4 (the source of
  the ``cannot identify image file …`` warning).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mira.gateway import EventsIndex, Gateway
from mira.settings.repo import SettingsRepo
from mira.ui.media.photo_viewport import ViewportItem
from mira.ui.pages.picker_page import PickerPage
from mira.ui.pages.video_transport import VideoTransportBar


@pytest.fixture()
def page(qapp, tmp_path):
    settings = SettingsRepo(tmp_path / "settings.json")
    index = EventsIndex(tmp_path / "events_index.json")
    gw = Gateway(settings=settings, index=index)
    p = PickerPage(gw)
    yield p
    p.deleteLater()


def _cull(item_id: str, kind: str, path: Path) -> SimpleNamespace:
    """Lightweight stand-in for a CullItem payload — only the attributes
    ``_on_current_changed`` reads (``item_id`` / ``path`` / ``kind``).
    """
    return SimpleNamespace(item_id=item_id, path=path, kind=kind)


def _land(page: PickerPage, payloads: list, index: int) -> None:
    """Drive the viewport directly without touching the gateway. Each
    payload becomes a ``ViewportItem`` carrying its kind; the viewport
    fires ``current_changed`` synchronously on ``set_items``."""
    page._items = list(payloads)
    page._state = {ci.item_id: None for ci in payloads}
    vitems = [
        ViewportItem(path=ci.path, kind=ci.kind, payload=ci)
        for ci in payloads
    ]
    page.viewport.set_items(vitems, index)


def test_transport_widget_is_hidden_by_default(page):
    """Surface 07 default — no item loaded → no transport buttons. The
    compact_row CONTAINER stays visible (geometry stable), only the
    transport widget inside is hidden."""
    assert page._transport_bar.isVisible() is False


def test_compact_row_container_is_always_visible(page):
    """Nelson 2026-06-15 — "the line where the transport buttons are
    placed has to exist (empty) when photos are displayed." The
    container holds the slot reserved so the canvas never reflows."""
    assert page._surface.compact_row.isHidden() is False
    assert page._surface.compact_row.minimumHeight() >= 48


def test_compact_row_reserved_height_is_constant_across_kinds(page, tmp_path):
    """Nelson 2026-06-15 Fix A — "the slot reserves 64 px on photos and
    videos so the canvas bottom edge is pixel-identical across the
    boundary." Constructed as ``setFixedHeight(64)`` so min == max
    regardless of which item kind is currently landed."""
    photo = _cull("p1", "photo", tmp_path / "p1.jpg")
    video = _cull("v1", "video", tmp_path / "v1.mp4")
    page.show()
    page._surface.adjustSize()
    cr = page._surface.compact_row
    _land(page, [photo, video], index=0)            # photo
    h_photo_min, h_photo_max = cr.minimumHeight(), cr.maximumHeight()
    assert h_photo_min == h_photo_max == 64
    page.viewport.show_index(1)                      # video
    assert cr.minimumHeight() == cr.maximumHeight() == 64
    page.viewport.show_index(0)                      # back to photo
    assert cr.minimumHeight() == cr.maximumHeight() == 64


def test_transport_bar_is_planted_in_compact_row(page):
    """The unified Picker owns one VideoTransportBar widget — the same
    catalogued ``#VideoTransport`` strip the standalone surface used."""
    assert isinstance(page._transport_bar, VideoTransportBar)
    bars = [
        c for c in page._surface.compact_row.findChildren(VideoTransportBar)
    ]
    assert bars == [page._transport_bar]
    # The strip carries the canonical Play / Pause TransportButton.
    assert page._transport_bar.play_btn.objectName() == "TransportButton"


def test_transport_reveals_on_video_landing(page, tmp_path):
    """Landing on a video flips the transport WIDGET visible — that's
    the 'few transport buttons appear' moment; the row container was
    already visible (geometry invariant)."""
    page.show()
    photo = _cull("p1", "photo", tmp_path / "p1.jpg")
    video = _cull("v1", "video", tmp_path / "v1.mp4")
    _land(page, [photo, video], index=1)
    assert page._transport_bar.isVisible() is True
    # Row container kept its slot reserved.
    assert page._surface.compact_row.isVisible() is True


def test_transport_hides_on_photo_landing(page, tmp_path):
    """Sweeping back to a photo hides the transport widget but the
    compact_row container stays — the canvas position doesn't move."""
    page.show()
    photo = _cull("p1", "photo", tmp_path / "p1.jpg")
    video = _cull("v1", "video", tmp_path / "v1.mp4")
    _land(page, [photo, video], index=1)            # video first
    assert page._transport_bar.isVisible() is True
    page.viewport.show_index(0)                      # back to photo
    assert page._transport_bar.isVisible() is False
    assert page._surface.compact_row.isVisible() is True


def test_volume_slider_pushes_into_the_viewport(page):
    """The volume slider was emitting into the void before this fix —
    pin that it now drives ``viewport.video_set_volume``."""
    page._transport_bar.volume.setValue(42)
    # Viewport cached the rate as 0..1.0; 42 → 0.42.
    assert abs(page.viewport._video_volume - 0.42) < 1e-3


def test_speed_selector_pushes_into_the_viewport(page):
    """Same pin for the speed selector — drives
    ``viewport.video_set_playback_rate``."""
    page._transport_bar.speed.setCurrentText("2×")
    assert abs(page.viewport._video_rate - 2.0) < 1e-3
    page._transport_bar.speed.setCurrentText("0.5×")
    assert abs(page.viewport._video_rate - 0.5) < 1e-3


def test_prev_button_seeks_to_start(page):
    """Nelson 2026-06-15 Fix B — ◀| jumps the playhead to frame 0
    (reuses ``seek_requested`` so the host's existing wiring covers it)."""
    seeks: list[int] = []
    page._transport_bar.seek_requested.connect(seeks.append)
    # Duration must be set first or the button is a deliberate no-op
    # only for the END jump — start jump fires unconditionally.
    page._transport_bar.set_position(5_000, 30_000)
    page._transport_bar.prev_frame.click()
    assert seeks == [0]


def test_next_button_seeks_to_end_when_duration_known(page):
    """|▶ snaps to the last frame — duration must be reported first."""
    seeks: list[int] = []
    page._transport_bar.seek_requested.connect(seeks.append)
    page._transport_bar.set_position(1_000, 30_000)
    page._transport_bar.next_frame.click()
    assert seeks == [30_000]


def test_next_button_is_a_no_op_until_duration_arrives(page):
    """Without a known duration the end-jump is a deliberate no-op —
    emitting 0 again would be misleading and steal the user's intent."""
    seeks: list[int] = []
    page._transport_bar.seek_requested.connect(seeks.append)
    # Duration defaults to 0; clicking |▶ should NOT fire.
    page._transport_bar.next_frame.click()
    assert seeks == []


def test_frame_step_path_is_retired(page):
    """Fix B retires the frame-step path entirely — no signal, no host
    handler, no fps probe. The pin enforces that future edits can't
    quietly resurrect it."""
    assert not hasattr(page._transport_bar, "frame_step_requested")
    assert not hasattr(page, "_on_video_frame_step")
    assert not hasattr(page, "_frame_ms")


def test_scrubber_click_jumps_the_position(qapp, page):
    """The legacy QSlider behaviour is page-step toward the click — the
    custom mousePressEvent overrides it to jump on click (a media
    scrubber must seek to where you point)."""
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtGui import QMouseEvent
    page.show()
    scrubber = page._transport_bar.scrubber
    scrubber.resize(300, scrubber.height())
    # A click at ~75% of the groove should land near value 750.
    point = QPoint(int(scrubber.width() * 0.75), scrubber.height() // 2)
    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, point.toPointF(),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier)
    scrubber.mousePressEvent(press)
    # Allow ~30 either side for handle-offset / style metrics.
    assert 700 < scrubber.value() < 800


def test_exif_prefetch_skips_video_items(page, tmp_path, monkeypatch):
    """``_spawn_exif_prefetch`` must never hand a video path to
    ``read_exif_batch``. The whole reason: stop the proxy/EXIF path
    decoding MP4s as still images and the resulting 'cannot identify
    image file …' warnings (Nelson 2026-06-15)."""
    page._items = [
        _cull("p1", "photo", tmp_path / "p1.jpg"),
        _cull("v1", "video", tmp_path / "v1.mp4"),
        _cull("p2", "photo", tmp_path / "p2.jpg"),
    ]
    captured: list = []

    def _fake_read_exif_batch(paths):
        captured.extend(paths)
        return []

    import core.exif_reader as exr
    monkeypatch.setattr(exr, "read_exif_batch", _fake_read_exif_batch)
    page._spawn_exif_prefetch()
    # The worker runs on a thread — wait for it briefly.
    for _ in range(50):
        if captured:
            break
        from PyQt6.QtCore import QCoreApplication
        QCoreApplication.processEvents()
        import time
        time.sleep(0.02)
    assert captured  # the prefetch ran
    assert all(str(p).endswith(".jpg") for p in captured)
