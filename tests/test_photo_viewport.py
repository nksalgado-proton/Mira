"""spec/63 slice 1 — the PhotoViewport: pipeline, edges, key grammar.

Offscreen tests over a REAL PhotoCache (fresh instance per test,
injected through the cache seam): placeholder→sharp delivery with
true native dimensions, stale-delivery filtering, settle prefetch,
edge signaling, the locked key map as semantic verbs, and the
pixmaps-never-drive-layout rule (the cut_play F11 lesson).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QTest

from mira.ui.media.photo_cache import PhotoCache
from mira.ui.media.photo_viewport import PhotoViewport, ViewportItem


def _spin_until(qapp, predicate, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture(autouse=True)
def _never_write_real_settings(monkeypatch):
    """The lens bar PERSISTS peaking prefs through
    core.settings.update_setting (round 5) — a test clicking the bar
    must NEVER reach the user's real settings.rebuild.json. Tests that
    assert persistence re-monkeypatch their own spy on top."""
    import core.settings as cs
    monkeypatch.setattr(cs, "update_setting", lambda k, v: None)


@pytest.fixture
def cache(qapp):
    c = PhotoCache()
    yield c
    c.shutdown()


@pytest.fixture
def viewport(qapp, cache):
    vp = PhotoViewport(cache=cache)
    vp.resize(800, 500)            # bucket → (1024, 512), deterministic
    yield vp
    vp.deleteLater()


def _make_jpegs(tmp_path, sizes) -> list[ViewportItem]:
    items = []
    for i, (w, h) in enumerate(sizes):
        p = tmp_path / f"p{i}.jpg"
        img = QImage(w, h, QImage.Format.Format_RGB32)
        img.fill(0x336699 + i * 0x111111)
        assert img.save(str(p), "JPG", 90)
        items.append(ViewportItem(path=p))
    return items


def test_sharp_lands_with_true_native_dims(qapp, viewport, tmp_path):
    items = _make_jpegs(tmp_path, [(640, 320)])
    viewport.set_items(items)
    assert _spin_until(qapp, lambda: viewport.sharp_pixmap_info() is not None)
    pm, native = viewport.sharp_pixmap_info()
    assert native == QSize(640, 320)
    assert not pm.isNull()


def test_fast_navigation_lands_on_the_landed_photo(qapp, viewport, tmp_path):
    """A→B with no waiting: the sharp that sticks is B's (stale A
    deliveries are filtered by current path)."""
    items = _make_jpegs(tmp_path, [(600, 300), (500, 400), (400, 200)])
    viewport.set_items(items)              # shows 0
    QTest.keyClick(viewport, Qt.Key.Key_Right)   # 1
    QTest.keyClick(viewport, Qt.Key.Key_Right)   # 2 — landed
    assert viewport.current_index() == 2
    assert _spin_until(
        qapp, lambda: (viewport.sharp_pixmap_info() or (None, None))[1]
        == QSize(400, 200))


def test_edges_emit_instead_of_wrapping(qapp, viewport, tmp_path):
    items = _make_jpegs(tmp_path, [(300, 200), (300, 200)])
    viewport.set_items(items, current=1)
    edges, changes = [], []
    viewport.edge_reached.connect(edges.append)
    viewport.current_changed.connect(changes.append)
    QTest.keyClick(viewport, Qt.Key.Key_Right)
    assert edges == [1] and viewport.current_index() == 1
    QTest.keyClick(viewport, Qt.Key.Key_Left)    # back to 0
    QTest.keyClick(viewport, Qt.Key.Key_Left)    # past the start
    assert edges == [1, -1] and viewport.current_index() == 0
    assert changes == [0]


def test_settle_prefetches_neighbours(qapp, viewport, cache, tmp_path):
    items = _make_jpegs(tmp_path, [(300, 200)] * 4)
    viewport.set_items(items)              # settle timer starts for idx 0
    target = viewport._target_size()
    assert _spin_until(
        qapp,
        lambda: cache.get_scaled_pixmap_if_cached(items[1].path, target)
        is not None
        and cache.get_scaled_pixmap_if_cached(items[2].path, target)
        is not None,
    ), "N+1 / N+2 must be decoded after the settle beat"


def test_key_grammar_emits_the_locked_verbs(qapp, viewport, tmp_path):
    items = _make_jpegs(tmp_path, [(300, 200)])
    viewport.set_items(items)
    got: list[str] = []
    viewport.pick_requested.connect(lambda: got.append("pick"))
    viewport.skip_requested.connect(lambda: got.append("skip"))
    viewport.toggle_requested.connect(lambda: got.append("toggle"))
    viewport.cycle_requested.connect(lambda: got.append("cycle"))
    viewport.transport_requested.connect(lambda: got.append("transport"))
    viewport.sweep_requested.connect(lambda: got.append("sweep"))
    viewport.truth_requested.connect(lambda: got.append("truth"))
    viewport.fullscreen_requested.connect(lambda: got.append("fs"))
    viewport.back_requested.connect(lambda: got.append("back"))
    for key in (Qt.Key.Key_P, Qt.Key.Key_X, Qt.Key.Key_Space,
                Qt.Key.Key_C, Qt.Key.Key_Tab, Qt.Key.Key_Return,
                Qt.Key.Key_F10, Qt.Key.Key_F, Qt.Key.Key_F11,
                Qt.Key.Key_Escape):
        QTest.keyClick(viewport, key)
    assert got == ["pick", "skip", "toggle", "cycle", "transport",
                   "sweep", "truth", "fs", "fs", "back"]


def test_pixmaps_never_drive_layout_minimums(qapp, viewport, tmp_path):
    """The cut_play F11 lesson, pinned for the viewport from birth."""
    items = _make_jpegs(tmp_path, [(3000, 2000)])
    viewport.set_items(items)
    _spin_until(qapp, lambda: viewport.sharp_pixmap_info() is not None)
    assert viewport.minimumSizeHint().width() < 200
    assert viewport.minimumSizeHint().height() < 200


# ── video: arm-on-landing (slice 3) ──────────────────────────────────

class _FakePlayer:
    """Stand-in for QMediaPlayer — records the arm-on-landing state
    machine without touching QtMultimedia (the charter's lazy-media
    rule; real frame delivery is the eyeball's job)."""

    def __init__(self) -> None:
        self.source = None
        self.playing = False
        self.position = 0
        self.stopped = 0
        # spec/138 §2A — ``_arm_video`` now re-applies the sticky
        # rate after every ``setSource`` so Qt's silent reset can't
        # survive. The fake mirrors the contract so the existing
        # arming tests don't AttributeError on ``setPlaybackRate``.
        self.rate = 1.0

    def setSource(self, url) -> None:       # noqa: N802
        self.source = url
        self.playing = False
        # Emulate Qt: setSource resets the playback rate.
        self.rate = 1.0

    def setPlaybackRate(self, rate) -> None:  # noqa: N802
        self.rate = float(rate)

    def play(self) -> None:
        self.playing = True

    def pause(self) -> None:
        self.playing = False

    def stop(self) -> None:
        self.playing = False
        self.stopped += 1

    def setPosition(self, ms) -> None:      # noqa: N802
        self.position = ms


class _FakeFrame:
    def __init__(self, valid: bool = True) -> None:
        self._valid = valid

    def isValid(self) -> bool:              # noqa: N802
        return self._valid


def _install_fake_player(vp, monkeypatch):
    """Make _ensure_player install a fake player + a real placeholder
    video widget in the stack (so the poster→live flip is observable)."""
    from PyQt6.QtWidgets import QWidget

    def fake_ensure() -> None:
        if vp._player is not None:
            return
        vp._video_widget = QWidget(vp)
        vp._stack.addWidget(vp._video_widget)
        vp._player = _FakePlayer()
    monkeypatch.setattr(vp, "_ensure_player", fake_ensure)


def _vid(path: Path) -> ViewportItem:
    return ViewportItem(path=path, kind="video")


def test_video_arms_only_on_settle_not_on_flyby(qapp, viewport, monkeypatch):
    _install_fake_player(viewport, monkeypatch)
    a = Path("C:/x/a.jpg")
    v = Path("C:/x/clip.mp4")
    b = Path("C:/x/b.jpg")
    viewport.set_items([ViewportItem(path=a), _vid(v), ViewportItem(path=b)])
    # Land on the video, then let the settle beat fire.
    viewport.show_index(1)
    assert viewport._video_armed is None        # not armed yet — just landed
    viewport._on_settle()                        # the beat
    assert viewport._video_armed == v
    assert viewport._player.playing               # autoplay default

    # Fly THROUGH the video (no settle): arm must not happen.
    viewport2 = viewport
    viewport2._disarm_video()
    viewport2.show_index(0)                       # photo
    viewport2.show_index(1)                       # video (flying)
    viewport2.show_index(2)                       # photo — past it
    viewport2._on_settle()                        # settle now lands on photo b
    assert viewport2._video_armed is None


def test_video_disarms_when_navigating_away(qapp, viewport, monkeypatch):
    _install_fake_player(viewport, monkeypatch)
    v = Path("C:/x/clip.mp4")
    p = Path("C:/x/p.jpg")
    viewport.set_items([_vid(v), ViewportItem(path=p)])
    viewport.show_index(0)
    viewport._on_settle()
    assert viewport._video_armed == v
    player = viewport._player
    viewport.show_index(1)                        # away → tear down
    assert viewport._video_armed is None
    assert player.stopped >= 1
    assert viewport._stack.currentWidget() is viewport._label


def test_poster_to_live_flip_only_on_real_frame_for_current(
    qapp, viewport, monkeypatch,
):
    """The video widget rides as a raised SIBLING of the stack (not
    inside it — Nelson 2026-06-15 canvas sweep) so the blurred
    backdrop shows in the bars. The poster→live flip swaps its
    visibility, not the stack's current widget."""
    _install_fake_player(viewport, monkeypatch)
    v = Path("C:/x/clip.mp4")
    viewport.set_items([_vid(v), ViewportItem(path=Path("C:/x/p.jpg"))])
    viewport.show_index(0)
    viewport._on_settle()
    # Poster up: stack on the label, video widget hidden. Use
    # ``isHidden()`` (negation of the explicit setVisible) so the
    # check is parent-visibility-independent — the offscreen test
    # parent never shows.
    assert viewport._stack.currentWidget() is viewport._label
    assert viewport._video_widget is not None
    assert viewport._video_widget.isHidden() is True
    viewport._on_video_frame(_FakeFrame(valid=False))           # no frame yet
    assert viewport._video_widget.isHidden() is True
    viewport._on_video_frame(_FakeFrame(valid=True))            # first frame
    # Live: the video widget is now visible (raised over the label),
    # the stack's current widget stays the label so the backdrop
    # paints behind everything.
    assert viewport._video_widget.isHidden() is False
    assert viewport._stack.currentWidget() is viewport._label
    assert viewport._video_live


def test_inspect_corner_button_shows_per_item_and_triggers_f10(
    qapp, viewport, tmp_path,
):
    """Nelson 2026-06-12: a corner magnifier on the media mirrors F10,
    on every surface for free. Shown on photos/cards, hidden on video,
    hidden when a surface owns F10 itself."""
    from PyQt6.QtGui import QPixmap
    vis = lambda: viewport._inspect_btn.isVisibleTo(viewport)  # noqa: E731
    photos = _make_jpegs(tmp_path, [(800, 600)])
    viewport.set_items(photos)
    assert vis()
    # Clicking it opens the inspection view (same as F10).
    _spin_until(qapp, lambda: viewport.sharp_pixmap_info() is not None)
    viewport._inspect_btn.click()
    assert viewport._truth_window is not None
    viewport._truth_window.close()
    # Video → nothing full-res to inspect → hidden.
    viewport.set_items([ViewportItem(path=Path("C:/x/c.mp4"), kind="video")])
    assert not vis()
    # Card → inspectable → shown.
    viewport.set_items([ViewportItem(kind="card", payload="x",
                                     pixmap=QPixmap(64, 64))])
    assert vis()
    # A surface that owns F10 (Edit) → the viewport hides its own button.
    viewport.set_items(photos)
    viewport.set_truth_internal(False)
    assert not vis()


def test_f10_opens_a_full_res_truth_view_for_photos(qapp, viewport, tmp_path):
    """F10 (Nelson 2026-06-12): full-resolution, fit-to-screen, no
    chrome. Esc closes it. Built from the FULL native pixels, not the
    display-size scaled cache."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    items = _make_jpegs(tmp_path, [(1600, 1000)])
    viewport.set_items(items)
    _spin_until(qapp, lambda: viewport.sharp_pixmap_info() is not None)
    QTest.keyClick(viewport, Qt.Key.Key_F10)
    tw = viewport._truth_window
    assert tw is not None and tw.isVisible()
    assert tw._base.width() == 1600 and tw._base.height() == 1000   # full res
    # F toggles honest peaking inside the inspection lens.
    assert not tw._peaking
    QTest.keyClick(tw, Qt.Key.Key_F)
    assert tw._peaking
    QTest.keyClick(tw, Qt.Key.Key_F)
    assert not tw._peaking
    QTest.keyClick(tw, Qt.Key.Key_Escape)
    assert not tw.isVisible()


def test_inspect_zoom_toggles_to_1to1_and_pans(qapp, viewport, tmp_path):
    """Full-absorb 5c: Z in the inspection lens toggles fit ↔ 1:1; the
    crop is screen-sized source pixels centred on the pan point; arrows
    + drag move it; Esc steps zoom→fit before close."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    # A source bigger than the inspect window so 1:1 actually crops.
    items = _make_jpegs(tmp_path, [(4000, 3000)])
    viewport.set_items(items)
    _spin_until(qapp, lambda: viewport.sharp_pixmap_info() is not None)
    QTest.keyClick(viewport, Qt.Key.Key_F10)
    iv = viewport._truth_window
    iv.resize(1000, 700)
    assert not iv._zoom
    QTest.keyClick(iv, Qt.Key.Key_Z)                # zoom to 1:1
    assert iv._zoom and iv._zoom_source is not None
    assert iv._zoom_source.width() == 4000          # full-res JPEG = base
    crop = iv._zoom_crop()
    view = iv._view_size()                          # the label — the bar
    assert crop.width() == view.width()             # eats window height
    assert crop.height() == view.height()           # (true 1:1 = view-sized)
    pan0 = (iv._pan.x(), iv._pan.y())
    QTest.keyClick(iv, Qt.Key.Key_Right)            # pan
    assert (iv._pan.x(), iv._pan.y()) != pan0
    QTest.keyClick(iv, Qt.Key.Key_Escape)           # zoom → fit (not close)
    assert not iv._zoom and iv.isVisible()
    QTest.keyClick(iv, Qt.Key.Key_Escape)           # fit → close
    assert not iv.isVisible()


def test_inspect_zoom_pan_clamps_within_source(qapp, viewport, tmp_path):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    items = _make_jpegs(tmp_path, [(4000, 3000)])
    viewport.set_items(items)
    _spin_until(qapp, lambda: viewport.sharp_pixmap_info() is not None)
    QTest.keyClick(viewport, Qt.Key.Key_F10)
    iv = viewport._truth_window
    iv.resize(1000, 700)
    QTest.keyClick(iv, Qt.Key.Key_Z)
    for _ in range(200):                            # hammer pan past the edge
        iv._pan_by(1000, 1000)
    assert 0 <= iv._pan.x() <= 4000
    assert 0 <= iv._pan.y() <= 3000
    crop = iv._zoom_crop()                          # still a valid in-bounds crop
    view = iv._view_size()
    assert crop.width() == view.width() and crop.height() == view.height()


def test_lens_opens_windowed_with_the_control_bar(qapp, viewport, tmp_path):
    """Nelson 2026-06-12 UI round: F10 = best resolution in a normal
    RESIZABLE window (no screen takeover) carrying the zoom + peaking
    bar; the title is the honest pixel readout. Colour/sensitivity
    collapse until Peaking is on (the old tools-row behaviour)."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    items = _make_jpegs(tmp_path, [(1600, 1000)])
    viewport.set_items(items)
    _spin_until(qapp, lambda: viewport.sharp_pixmap_info() is not None)
    QTest.keyClick(viewport, Qt.Key.Key_F10)
    iv = viewport._truth_window
    assert iv is not None and iv.isVisible()
    assert not iv.isFullScreen()                     # windowed by default
    # MODAL (Nelson 2026-06-12): the app waits until the lens closes.
    assert iv.isModal()
    assert iv.windowModality() == Qt.WindowModality.ApplicationModal
    avail = iv.screen().availableGeometry()
    assert iv.width() <= avail.width() and iv.height() <= avail.height()
    assert "1600 × 1000" in iv.windowTitle()
    assert iv._bar.isVisibleTo(iv)
    # Collapse-until-active + the bar drives the lens state.
    assert not iv._peak_colour_btn.isVisibleTo(iv._bar)
    iv._peak_btn.click()                             # peaking ON via the bar
    assert iv._peaking
    assert iv._peak_colour_btn.isVisibleTo(iv._bar)
    before = iv._peak_colour_name
    iv._peak_colour_btn.click()
    assert iv._peak_colour_name != before
    iv._zoom_btn.click()                             # 1:1 via the bar
    assert iv._zoom
    QTest.keyClick(iv, Qt.Key.Key_Z)                 # Z keeps the button honest
    assert not iv._zoom and not iv._zoom_btn.isChecked()
    iv.close()


def test_lens_peaking_derives_every_view_from_the_fullres_mask(
        qapp, viewport, tmp_path):
    """Round 5 (Nelson 2026-06-12): the binary mask is computed ONCE at
    the SOURCE's resolution; the fit view downscales it, the 1:1 zoom
    slices it. Colour changes only re-tint (the binary stands);
    sensitivity changes recompute through the settle debounce. For a
    JPEG the zoom source IS the base — one mask serves both views."""
    items = _make_jpegs(tmp_path, [(1600, 1000)])
    viewport.set_items(items)
    _spin_until(qapp, lambda: viewport.sharp_pixmap_info() is not None)
    QTest = __import__("PyQt6.QtTest", fromlist=["QTest"]).QTest
    from PyQt6.QtCore import Qt
    QTest.keyClick(viewport, Qt.Key.Key_F10)
    iv = viewport._truth_window
    iv._peak_btn.click()                             # peaking ON (fit view)
    assert "base" in iv._peak_binary
    sens, mask = iv._peak_binary["base"]
    assert sens == iv._peak_sens_val
    assert mask.shape == (1000, 1600)                # FULL res, not display
    iv._peak_colour_btn.click()                      # colour: re-tint only
    assert iv._peak_binary["base"][1] is mask        # binary untouched
    # The doubled sensitivity track (Nelson 2026-06-12).
    assert iv._peak_sens._rows["sensitivity"].slider.minimumWidth() == 180
    QTest.keyClick(iv, Qt.Key.Key_Z)                 # 1:1 — same mask serves
    assert set(iv._peak_binary) == {"base"}          # no second compute (JPEG)
    QTest.keyClick(iv, Qt.Key.Key_Z)
    iv._on_peak_sens("sensitivity", 80.0)            # slider tick…
    assert iv._peak_binary                           # …not applied yet
    iv._apply_peak_sens()                            # the settle fires
    assert iv._peak_binary == {} or iv._peak_binary.get(
        "base", (None,))[0] == 80                    # cleared, lazily rebuilt
    iv._fit()
    assert iv._peak_binary["base"][0] == 80          # recomputed at new sens
    iv.close()


def test_lens_peaking_prefs_persist_via_the_settings_writer(
        qapp, viewport, tmp_path, monkeypatch):
    """The bar's tuning sticks (round 5): colour + settled sensitivity
    write through core.settings.update_setting — spied here, the real
    user file untouched."""
    import core.settings as cs
    writes = []
    monkeypatch.setattr(
        cs, "update_setting", lambda k, v: writes.append((k, v)))
    items = _make_jpegs(tmp_path, [(800, 600)])
    viewport.set_items(items)
    _spin_until(qapp, lambda: viewport.sharp_pixmap_info() is not None)
    QTest = __import__("PyQt6.QtTest", fromlist=["QTest"]).QTest
    from PyQt6.QtCore import Qt
    QTest.keyClick(viewport, Qt.Key.Key_F10)
    iv = viewport._truth_window
    iv._peak_btn.click()
    before = iv._peak_colour_name
    iv._peak_colour_btn.click()
    assert ("peaking_color", iv._peak_colour_name) in writes
    assert iv._peak_colour_name != before
    iv._on_peak_sens("sensitivity", 65.0)
    iv._apply_peak_sens()
    assert ("peaking_sensitivity", 65) in writes
    iv.close()


def test_lens_tools_off_for_edit_and_cut_surfaces(qapp, viewport, tmp_path):
    """Nelson 2026-06-12 standardisation: the zoom/peaking bar shows on
    the cull surfaces only — a host that sets lens tools OFF gets the
    clean lens (no bar, F/Z inert, window = the picture alone)."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    viewport.set_lens_tools_visible(False)
    items = _make_jpegs(tmp_path, [(1600, 1000)])
    viewport.set_items(items)
    _spin_until(qapp, lambda: viewport.sharp_pixmap_info() is not None)
    QTest.keyClick(viewport, Qt.Key.Key_F10)
    iv = viewport._truth_window
    assert iv is not None and iv.isVisible()
    assert not iv._with_tools
    assert not iv._bar.isVisibleTo(iv)
    QTest.keyClick(iv, Qt.Key.Key_Z)                 # inert — no tools
    assert not iv._zoom
    QTest.keyClick(iv, Qt.Key.Key_F)
    assert not iv._peaking
    view = iv._view_size()                           # window = picture alone
    assert abs(iv.height() - view.height()) <= 2
    QTest.keyClick(iv, Qt.Key.Key_F11)               # the pure look still works
    assert iv.isFullScreen()
    QTest.keyClick(iv, Qt.Key.Key_Escape)
    assert not iv.isFullScreen() and not iv._bar.isVisibleTo(iv)
    iv.close()


def test_open_inspect_lens_helper_serves_viewportless_hosts(qapp):
    """Edit has no viewport — open_inspect_lens() opens the SAME
    standard lens (modal, aspect-locked, clean) on an arbitrary pixmap
    (the processed/cropped full-res render before export)."""
    from PyQt6.QtGui import QPixmap
    from mira.ui.media.photo_viewport import open_inspect_lens
    pm = QPixmap(800, 500)
    pm.fill()
    lens = open_inspect_lens(pm, with_tools=False)
    assert lens.isVisible() and lens.isModal()
    assert not lens._with_tools
    assert "800 × 500" in lens.windowTitle()
    lens.close()


def test_lens_window_is_aspect_locked_and_house_themed(qapp, viewport, tmp_path):
    """Nelson 2026-06-12: the lens VIEW area keeps the picture's ratio
    through any resize — the photo fills it edge to edge, no letterbox
    bands; the window height is exactly view + bar. Themed via QSS
    roles (InspectView / InspectBar in BOTH themes), never inline."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    items = _make_jpegs(tmp_path, [(1600, 1000)])      # aspect 1.6
    viewport.set_items(items)
    _spin_until(qapp, lambda: viewport.sharp_pixmap_info() is not None)
    QTest.keyClick(viewport, Qt.Key.Key_F10)
    iv = viewport._truth_window
    assert iv.styleSheet() == ""                       # QSS-themed, not inline
    qapp.processEvents()
    view = iv._view_size()
    assert abs(view.width() / view.height() - 1.6) < 0.03
    iv.resize(900, 900)                                # a wild user drag…
    qapp.processEvents()
    view = iv._view_size()
    assert abs(view.width() / view.height() - 1.6) < 0.03   # …stays locked
    assert abs(iv._label.height() + iv._bar.height() - iv.height()) <= 4
    iv.close()


def test_lens_resize_uses_the_fast_path_then_settles_smooth(
        qapp, viewport, tmp_path):
    """Nelson 2026-06-12: smooth-rescaling the full-res base per drag
    tick made the photo flicker. Mid-drag the lens fits a ≤2560 proxy
    FAST (the picture stays visible — never blanked) and skips
    peaking; the smooth render + peaking run once on settle."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    items = _make_jpegs(tmp_path, [(1600, 1000)])
    viewport.set_items(items)
    _spin_until(qapp, lambda: viewport.sharp_pixmap_info() is not None)
    QTest.keyClick(viewport, Qt.Key.Key_F10)
    iv = viewport._truth_window
    iv._peak_btn.click()                          # peaking ON
    assert not iv._resizing
    iv.resize(900, 700)                           # a drag tick
    assert iv._resizing                           # fast path engaged
    assert iv._resize_settle.isActive()
    pm = iv._label.pixmap()
    assert pm is not None and not pm.isNull()     # never blanked mid-drag
    assert iv._drag_proxy is not None
    assert max(iv._drag_proxy.width(), iv._drag_proxy.height()) <= 2560
    iv._on_resize_settled()                       # the settle fires
    assert not iv._resizing
    assert iv._peaking                            # peaking returned with it
    iv.close()


def test_lens_f11_is_the_pure_look_and_esc_steps_down(qapp, viewport, tmp_path):
    """F11 inside the lens: truly fullscreen, bar hidden, peaking +
    zoom forced OFF (no peaking, no zoom in the pure look — Nelson
    2026-06-12); F/Z are inert there. Esc one level at a time:
    fullscreen → windowed → close."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    items = _make_jpegs(tmp_path, [(1600, 1000)])
    viewport.set_items(items)
    _spin_until(qapp, lambda: viewport.sharp_pixmap_info() is not None)
    QTest.keyClick(viewport, Qt.Key.Key_F10)
    iv = viewport._truth_window
    iv._peak_btn.click()
    iv._zoom_btn.click()
    assert iv._peaking and iv._zoom
    QTest.keyClick(iv, Qt.Key.Key_F11)               # → the pure look
    assert iv.isFullScreen()
    assert not iv._bar.isVisibleTo(iv)
    assert not iv._peaking and not iv._zoom
    QTest.keyClick(iv, Qt.Key.Key_F)                 # inert in fullscreen
    assert not iv._peaking
    QTest.keyClick(iv, Qt.Key.Key_Z)
    assert not iv._zoom
    QTest.keyClick(iv, Qt.Key.Key_Escape)            # level 1 → windowed
    assert iv.isVisible() and not iv.isFullScreen()
    assert iv._bar.isVisibleTo(iv)
    QTest.keyClick(iv, Qt.Key.Key_Escape)            # level 2 → close
    assert not iv.isVisible()


def test_f10_is_a_noop_on_video_and_when_overridden(qapp, viewport, tmp_path):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    # Video: nothing full-res to show.
    viewport.set_items([ViewportItem(path=Path("C:/x/clip.mp4"), kind="video")])
    QTest.keyClick(viewport, Qt.Key.Key_F10)
    assert viewport._truth_window is None
    # Overridden (Edit will own F10): the viewport opens nothing itself.
    viewport.set_truth_internal(False)
    photos = _make_jpegs(tmp_path, [(800, 600)])
    viewport.set_items(photos)
    _spin_until(qapp, lambda: viewport.sharp_pixmap_info() is not None)
    QTest.keyClick(viewport, Qt.Key.Key_F10)
    assert viewport._truth_window is None


def test_af_overlay_draws_only_when_enabled_with_a_point(qapp, viewport,
                                                         tmp_path):
    """Full-absorb 5a: the AF rectangle is an opt-in overlay on the
    displayed photo. Off / no-point / non-photo → nothing painted."""
    from core.brand_profile import AfPoint
    items = _make_jpegs(tmp_path, [(1600, 1000)])
    viewport.set_items(items)
    _spin_until(qapp, lambda: viewport.sharp_pixmap_info() is not None)
    af = AfPoint(cx=0.5, cy=0.5, w=0.1, h=0.1)
    # No point yet → no draw.
    assert not viewport._should_draw_af()
    viewport.set_af_point(af)
    assert not viewport._should_draw_af()           # overlay still off
    viewport.set_af_overlay_enabled(True)
    assert viewport._should_draw_af()               # on + point + photo
    assert viewport.is_af_overlay_enabled()
    assert viewport.af_point() is af
    # Navigating clears the point until the host re-feeds it.
    viewport.set_items(items)
    assert viewport.af_point() is None
    assert not viewport._should_draw_af()


def test_af_overlay_not_drawn_on_cards(qapp, viewport):
    from core.brand_profile import AfPoint
    from PyQt6.QtGui import QPixmap
    card = ViewportItem(kind="card", payload="x", pixmap=QPixmap(64, 64))
    viewport.set_items([card])
    viewport.set_af_point(AfPoint(cx=0.5, cy=0.5, w=0.1, h=0.1))
    viewport.set_af_overlay_enabled(True)
    assert not viewport._should_draw_af()           # cards never get AF


def test_peaking_toggle_caches_mask_and_invalidates_on_change(
    qapp, viewport, tmp_path,
):
    """Full-absorb 5b: peaking computes a mask once per (size, colour,
    sensitivity) and caches it; colour/sensitivity changes invalidate
    it. JPEG path = the display pixmap (real pixels)."""
    items = _make_jpegs(tmp_path, [(800, 600)])
    viewport.set_items(items)
    _spin_until(qapp, lambda: viewport.sharp_pixmap_info() is not None)
    assert not viewport.is_peaking_enabled()
    assert viewport._peaking_mask_cache is None
    viewport.set_peaking_enabled(True)
    assert viewport.is_peaking_enabled()
    cached = viewport._peaking_mask_cache
    assert cached is not None                          # mask computed
    viewport._fit()                                    # same key → reuse
    assert viewport._peaking_mask_cache is cached
    viewport.set_peaking_color("yellow")               # invalidates
    assert viewport.peaking_color_name() == "yellow"
    assert viewport._peaking_mask_cache is not cached  # recomputed
    c2 = viewport._peaking_mask_cache
    viewport.set_peaking_sensitivity(80)               # invalidates
    assert viewport.peaking_sensitivity() == 80
    assert viewport._peaking_mask_cache is not c2


def test_peaking_base_is_display_for_jpeg_and_clears_on_nav(
    qapp, viewport, tmp_path,
):
    items = _make_jpegs(tmp_path, [(800, 600), (640, 480)])
    viewport.set_items(items)
    _spin_until(qapp, lambda: viewport.sharp_pixmap_info() is not None)
    viewport.set_peaking_enabled(True)
    # JPEG: the peaking base is the display pixmap (no half-res decode).
    assert viewport._halfres_pixmap() is None
    assert not viewport._peaking_base().isNull()
    # Nav clears the per-photo peaking caches.
    viewport.show_index(1)
    assert viewport._halfres_cache is None
    assert viewport._peaking_mask_cache is None


def test_tab_toggles_play_only_on_armed_video(qapp, viewport, monkeypatch):
    _install_fake_player(viewport, monkeypatch)
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    v = Path("C:/x/clip.mp4")
    viewport.set_items([_vid(v)])
    viewport.show_index(0)
    viewport._on_settle()
    assert viewport._player.playing
    QTest.keyClick(viewport, Qt.Key.Key_Tab)      # pause
    assert not viewport._player.playing
    QTest.keyClick(viewport, Qt.Key.Key_Tab)      # play
    assert viewport._player.playing


def test_stale_cache_delivery_after_widget_destroyed_is_silent(qapp, cache):
    """Regression (Nelson 2026-06-19 — uncaught exception, app
    restart): the photo cache is an app-lifetime singleton; a
    viewport destroyed mid-decode (user navigates away during an
    async request) keeps its Python wrapper alive — long enough for
    the cache's signal to find us — but the C++ widget is gone, so
    `self.update()` raised `RuntimeError: wrapped C/C++ object of
    type PhotoViewport has been deleted`. The slots now early-return
    via `sip.isdeleted`."""
    from PyQt6 import sip
    from PyQt6.QtCore import QSize
    from PyQt6.QtGui import QPixmap
    vp = PhotoViewport(cache=cache)
    # Force the C++ side to die without going through deleteLater + the
    # event loop, so the Python wrapper is the only thing left.
    sip.delete(vp)
    # The cache emits, the slot finds a dead widget — both entry points
    # must be safe.
    vp._on_scaled_ready(Path("nope.jpg"), QPixmap(), QSize(0, 0))
    vp._on_decode_failed(Path("nope.jpg"))
