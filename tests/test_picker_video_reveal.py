"""Pins the unified Picker's video reveal (spec/70 row 11 folded into
07, Nelson 2026-06-15; spec/130 unification 2026-06-23).

Contract:

* PickerPage embeds the shared :class:`VideoWorkshopBar` (spec/130 —
  one transport widget backs both the Picker and the Editor) inside
  the ``BasePickSurface.compact_row`` slot. The slot itself stays
  VISIBLE on every item (the canvas position is invariant under
  photo↔video sweeps — Nelson: "the line where the transport
  buttons are placed has to exist (empty) when photos are displayed").
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
from mira.ui.media.transport_bar import VideoWorkshopBar
from mira.ui.pages.picker_page import PickerPage


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
    """Nelson 2026-06-15 Fix A (updated 2026-06-21) — "the slot reserves
    a constant pixel height on photos and videos so the canvas bottom
    edge is pixel-identical across the boundary." The spec/92 dense
    tier re-sizes the slot to the DENSE transport bar's measured
    height (``sizeHint().height() + 14``) instead of the old static
    64 px, so the test pins the invariant — fixed-size, identical
    across photo / video / back-to-photo — without nailing a specific
    pixel count that drifts when the dense bar's metrics change."""
    photo = _cull("p1", "photo", tmp_path / "p1.jpg")
    video = _cull("v1", "video", tmp_path / "v1.mp4")
    page.show()
    page._surface.adjustSize()
    cr = page._surface.compact_row
    _land(page, [photo, video], index=0)            # photo
    reserved = cr.minimumHeight()
    assert reserved >= 48                            # comfortable floor
    assert cr.maximumHeight() == reserved            # fixed-size policy
    page.viewport.show_index(1)                      # video
    assert cr.minimumHeight() == reserved
    assert cr.maximumHeight() == reserved
    page.viewport.show_index(0)                      # back to photo
    assert cr.minimumHeight() == reserved
    assert cr.maximumHeight() == reserved


def test_transport_bar_is_the_shared_widget_planted_in_compact_row(page):
    """spec/130 — the Picker uses the shared :class:`VideoWorkshopBar`
    (moved to ``mira.ui.media.transport_bar``), the same widget the
    Editor uses on surface 12. The strip carries the canonical
    TransportButton-role play/pause button."""
    assert isinstance(page._transport_bar, VideoWorkshopBar)
    bars = [
        c for c in page._surface.compact_row.findChildren(VideoWorkshopBar)
    ]
    assert bars == [page._transport_bar]
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
    """The volume slider drives ``viewport.video_set_volume``. spec/130
    moved the slider attribute name to ``vol_slider`` (the shared
    widget's name)."""
    page._transport_bar.vol_slider.setValue(42)
    # Viewport cached the rate as 0..1.0; 42 → 0.42.
    assert abs(page.viewport._video_volume - 0.42) < 1e-3


def test_speed_selector_pushes_into_the_viewport(page):
    """Same pin for the speed selector — drives
    ``viewport.video_set_playback_rate``. spec/130 — the shared
    widget's combo is named ``speed_combo`` and emits a float."""
    page._transport_bar.speed_combo.setCurrentText("2×")
    assert abs(page.viewport._video_rate - 2.0) < 1e-3
    page._transport_bar.speed_combo.setCurrentText("0.5×")
    assert abs(page.viewport._video_rate - 0.5) < 1e-3


def test_jump_start_button_seeks_to_zero(page):
    """spec/130 — the shared widget's ⏮ Start button fires
    ``jump_start_requested``; the Picker wires it to
    ``viewport.video_seek(0)``."""
    seeks: list[int] = []
    page.viewport.video_seek = seeks.append        # type: ignore[assignment]
    page._transport_bar.start_btn.click()
    assert seeks == [0]


def test_jump_end_button_seeks_to_duration(page):
    """spec/130 — the shared widget's End ⏭ button fires
    ``jump_end_requested``; the Picker wires it to
    ``viewport.video_seek(duration - 1)``. The host respects the
    cached ``_video_duration_ms`` (matches the Editor)."""
    seeks: list[int] = []
    page.viewport.video_seek = seeks.append        # type: ignore[assignment]
    page._video_duration_ms = 30_000
    page._transport_bar.end_btn.click()
    assert seeks == [29_999]


# --------------------------------------------------------------------- #
# Blurred backdrop on the canvas — video widget geometry
# --------------------------------------------------------------------- #


def _push_label_pixmap(vp, w: int, h: int, colour: str = "teal") -> None:
    """Drive ``_display`` directly so the QLabel ends up with the
    centered, aspect-fitted pixmap the user would see in the live
    app — this is what ``video_widget_rect`` reads."""
    from PyQt6.QtGui import QColor, QImage, QPixmap
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(colour))
    vp._display(QPixmap.fromImage(img))


def test_video_widget_letterboxes_to_label_pixmap_not_full_canvas(
    qapp, tmp_path,
):
    """Nelson 2026-06-15 canvas sweep — the QVideoWidget paints opaque
    black inside its own rect on Windows, so the host sizes it to the
    QLabel's centered-pixmap rect (the rect the user is looking at —
    poster or held-previous frame) instead of the full canvas. The
    bars around it show the blurred backdrop.

    With a 16:9 source in a 4:3 viewport the widget's height shrinks
    to fit the aspect — its top/bottom never touch the canvas edges.
    The pixmap is also inset by ``_MEDIA_INNER_PAD`` per side so the
    media never touches the canvas border."""
    from PyQt6.QtCore import QSize
    from mira.ui.media.photo_viewport import PhotoViewport
    vp = PhotoViewport()
    try:
        vp.resize(QSize(1200, 900))                     # 4:3 canvas
        vp.show()
        pad = vp._MEDIA_INNER_PAD                        # 8
        _push_label_pixmap(vp, 160, 90)                  # 16:9 source
        rect = vp.video_widget_rect()
        # Inner pad shrinks the fit area to (1200-2p, 900-2p); 16:9
        # then constrains width → height. Width = 1200 - 2p = 1184;
        # height = 1184 * 9/16 = 666. Centered in the 1200×900 label.
        assert rect.width() == 1200 - 2 * pad
        assert rect.height() == 666
        # Left/top symmetric inset; vertical bars top + bottom.
        assert rect.x() == pad
        assert rect.y() == (900 - 666) // 2
        # Confirm the canvas edge is not touched on any side.
        assert rect.x() >= pad
        assert rect.y() >= pad
        assert rect.right() <= 1200 - pad
        assert rect.bottom() <= 900 - pad
        # Confirm we're NOT painting the full canvas — that's the
        # bar guarantee.
        assert rect != vp.rect()
    finally:
        vp.deleteLater()


def test_video_widget_letterboxes_portrait_label_pixmap_horizontally(
    qapp, tmp_path,
):
    """The other axis: a portrait source (taller than the canvas)
    letterboxes left/right — backdrop shows in the side bars. The
    inner pad shaves another 2×pad off the available space."""
    from PyQt6.QtCore import QSize
    from mira.ui.media.photo_viewport import PhotoViewport
    vp = PhotoViewport()
    try:
        vp.resize(QSize(1200, 600))                     # 2:1 canvas
        vp.show()
        pad = vp._MEDIA_INNER_PAD                        # 8
        _push_label_pixmap(vp, 45, 90)                   # 1:2 source
        rect = vp.video_widget_rect()
        # Inner pad shrinks the fit area to (1184, 584); the portrait
        # aspect constrains height → width. Height = 584; width =
        # 584 * (1/2) = 292. Centered in the 1200×600 label.
        assert rect.height() == 600 - 2 * pad
        assert rect.width() == 292
        assert rect.y() == pad
        assert rect.x() == (1200 - 292) // 2
    finally:
        vp.deleteLater()


def test_media_never_touches_the_canvas_edge(qapp, tmp_path):
    """Nelson 2026-06-15 final touch — "make the photo/video a little
    bit smaller so it never touches any border". With ANY source
    aspect, the displayed pixmap's rect leaves the inner pad as a
    margin all around. The hairline frame painted in ``paintEvent``
    sits in that gap so it never butts against the canvas border."""
    from PyQt6.QtCore import QSize
    from mira.ui.media.photo_viewport import PhotoViewport
    vp = PhotoViewport()
    try:
        vp.resize(QSize(1200, 900))
        vp.show()
        pad = vp._MEDIA_INNER_PAD
        for w, h in ((160, 90), (90, 160), (100, 100), (240, 75)):
            _push_label_pixmap(vp, w, h)
            rect = vp.video_widget_rect()
            assert rect.x() >= pad, (w, h, rect)
            assert rect.y() >= pad, (w, h, rect)
            assert rect.right() <= vp.width() - pad, (w, h, rect)
            assert rect.bottom() <= vp.height() - pad, (w, h, rect)
    finally:
        vp.deleteLater()


def test_video_widget_rect_falls_back_to_full_canvas_without_pixmap(
    qapp,
):
    """Defensive — no label pixmap means we can't compute the aspect;
    the widget then fills the canvas and ``KeepAspectRatio`` mode
    letterboxes internally. Rare path (the next poster arrives within
    milliseconds via the cache and re-pins through ``_display``)."""
    from PyQt6.QtCore import QSize
    from mira.ui.media.photo_viewport import PhotoViewport
    vp = PhotoViewport()
    try:
        vp.resize(QSize(800, 600))
        # No pixmap pushed — QLabel is empty.
        rect = vp.video_widget_rect()
        assert rect == vp.rect()
    finally:
        vp.deleteLater()


def test_native_video_size_overrides_a_mismatched_poster(qapp, monkeypatch):
    """Nelson 2026-06-15 follow-up — the QVideoWidget had black stripes
    inside the photo's letterbox rect when the previous item was a
    portrait photo (held up by spec/63's "never blank the canvas" rule)
    and the wide video armed before its own poster landed.

    Fix: ``_on_video_frame`` captures the video's native size and
    pins the widget to it, overriding the QLabel's pixmap rect (which
    still carries the portrait poster's aspect)."""
    from PyQt6.QtCore import QSize
    from mira.ui.media.photo_viewport import PhotoViewport
    vp = PhotoViewport()
    try:
        vp.resize(QSize(1200, 900))
        vp.show()
        # Stand-in for a portrait poster from a previous item.
        _push_label_pixmap(vp, 90, 160, colour="darkred")
        # Without a native size, the rect tracks the (portrait) label
        # pixmap — height-pinned, narrow.
        before = vp.video_widget_rect()
        assert before.height() < 900                    # padded
        assert before.width() < before.height()         # portrait shape
        # Player arms; the first valid frame reports 16:9.
        vp._video_armed = Path("c:/v.mp4")
        vp._video_native_size = QSize(1920, 1080)
        after = vp.video_widget_rect()
        # Wide rect now — width-pinned, lower height; the portrait
        # poster's geometry is gone.
        assert after.width() > after.height()
        pad = vp._MEDIA_INNER_PAD
        assert after.width() == 1200 - 2 * pad
        # 16:9 inside (1184, 884) constrained by width → 1184 × 666.
        assert after.height() == 666
    finally:
        vp.deleteLater()


def test_video_widget_geometry_resyncs_when_poster_lands_after_arming(
    qapp, monkeypatch,
):
    """The bug Nelson hit live: PickerPage doesn't supply item.pixmap
    for videos — the viewport's poster path resolves one through the
    PhotoCache asynchronously. The poster can land AFTER the video
    widget shows. Pin that ``_display`` re-pins the widget geometry
    on every fresh poster while the widget is visible."""
    from PyQt6.QtCore import QSize
    from PyQt6.QtGui import QColor, QImage, QPixmap
    from PyQt6.QtMultimedia import QMediaPlayer
    from mira.ui.media.photo_viewport import PhotoViewport
    vp = PhotoViewport()
    try:
        vp.resize(QSize(1200, 900))
        vp.show()
        # Fake the player + show the widget as if a live frame arrived.
        vp._ensure_player()
        vp._video_widget.show()
        # No pixmap yet → full canvas (the "rare path" fallback).
        assert vp.video_widget_rect() == vp.rect()
        # Poster lands. _display must re-pin the widget geometry.
        _push_label_pixmap(vp, 160, 90)                 # 16:9
        # After _display, the widget's geometry tracks the letterboxed
        # rect (with the inner pad applied) — bars exist top + bottom
        # AND a sliver of backdrop on either side.
        pad = vp._MEDIA_INNER_PAD
        geom = vp._video_widget.geometry()
        assert geom.width() == 1200 - 2 * pad           # 1184
        assert geom.height() == 666                      # 1184 * 9/16
    finally:
        vp.deleteLater()


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
