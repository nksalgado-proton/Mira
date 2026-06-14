"""Tests for VideoPickPage — spec/56 slice 2 (watch + P/D) on the ONE
display engine (spec/63 5e).

The Pick video surface keeps playback chrome (Play/Pause, the timeline,
frame stepping — Nelson 2026-06-10: "leave the Play/Pause and timeline")
and the whole-video P/D border; pixels + the player live in the embedded
PhotoViewport (arm-on-landing — PosterStack retired). Decisions leave as
``decision_verb_requested`` verbs per the spec/63 §4 locked map: P pick ·
X skip · Space toggle · C cycle (binary ledger → the shell degrades it
to toggle) · Tab = TRANSPORT (the legacy Tab-cycles-state binding is
evicted by §4 — that old pin was rewritten with this migration).

Construction-level: the viewport's player only arms on the settle beat,
which never fires without a pumped event loop — QtMultimedia stays
untouched.
"""
from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtCore import QEvent
from PyQt6.QtTest import QTest

from mira.ui.picked.video_pick_page import VideoPickPage


@pytest.fixture()
def page(qapp):
    p = VideoPickPage()
    yield p
    p.deleteLater()


@pytest.fixture()
def fake_meta(monkeypatch):
    """Stub ffprobe — instant, deterministic fps/duration."""
    from core.video_extract import VideoMetadata

    def _probe(path):
        return VideoMetadata(
            duration_ms=8_000, width=1920, height=1080, fps=50.0,
            codec="h264")

    import mira.ui.picked.video_pick_page as mod
    monkeypatch.setattr(mod, "probe_video", _probe)
    return _probe


def _vitem(tmp_path, name="clip.mp4", poster=None):
    vid = tmp_path / name
    vid.write_bytes(b"not really a video")
    item = VideoPickPage.video_item(vid)
    if poster is not None:
        item = type(item)(
            path=item.path, source_folder=item.source_folder,
            timestamp=item.timestamp, day=item.day, poster=poster)
    return item


def test_workshop_chrome_is_gone(page):
    """spec/56 slice 2: no creation buttons, no mark nav, no mode machinery."""
    for retired in ("_btn_mark", "_btn_still", "_btn_remove", "_btn_status",
                    "_btn_new_pass", "_nav_pmark", "_nav_nmark",
                    "_action_line", "_session", "_model"):
        assert not hasattr(page, retired), retired
    assert not hasattr(page, "set_mode")
    assert not hasattr(page, "set_immersive")
    # 5e: the page-owned player + PosterStack went into the viewport.
    for retired in ("_player", "_audio", "_video", "_poster_stack"):
        assert not hasattr(page, retired), retired


def test_playback_transport_stays(page):
    """Nelson 2026-06-10: Play/Pause + timeline stay on the picker surface."""
    assert page._nav_play.text()       # ▶ Play
    assert page._nav_pf is not None and page._nav_nf is not None
    assert page._nav_start is not None and page._nav_end is not None
    assert page._timeline is not None
    assert page._time.text().startswith("0:00")
    # The timeline click-to-jump wiring routes into _seek_to → _refresh:
    # the readout picks up the (probed) duration even with no media armed.
    page._duration_ms = 10_000
    page._timeline.seek_requested.emit(5_000)
    assert page._time.text().endswith("0:10.000")
    assert page._time.text().startswith("0:05.000")


def test_set_binary_state_drives_media_border(page):
    """The MediaHost border is the P/D indicator (spec/42 mechanism)."""
    page.set_binary_state("picked")
    assert page._surface._media_host.property("state") == "picked"
    page.set_binary_state("skipped")
    assert page._surface._media_host.property("state") == "skipped"


def test_load_relabels_nav_for_day_grid(page, tmp_path, fake_meta):
    """spec/32 §2.7 — Day Grid context relabels the outer nav buttons."""
    item = _vitem(tmp_path)
    assert page.load([item], nav_context="day_grid") is True
    assert page._nav_pb.text() == "← Previous"
    assert page._nav_nb.text() == "Next →"
    assert page.load([item], nav_context="bucket") is True
    assert "Bucket" in page._nav_pb.text()


# --------------------------------------------------------------------------- #
# The locked key map (spec/63 §4) — verbs through the viewport
# --------------------------------------------------------------------------- #


def test_viewport_keys_speak_the_decision_verbs(page, tmp_path, fake_meta):
    """P/X/Space/C arrive as verbs; Tab is TRANSPORT (plays/pauses the
    clip — never a decision; the §4 eviction of the legacy binding)."""
    page.load([_vitem(tmp_path)], nav_context="day_grid")
    verbs = []
    page.decision_verb_requested.connect(verbs.append)
    for key in (Qt.Key.Key_P, Qt.Key.Key_X, Qt.Key.Key_Space, Qt.Key.Key_C):
        QTest.keyClick(page._viewport, key)
    assert verbs == ["pick", "skip", "toggle", "cycle"]
    QTest.keyClick(page._viewport, Qt.Key.Key_Tab)
    assert verbs == ["pick", "skip", "toggle", "cycle"]   # no new verb


def test_stray_focus_fallback_speaks_the_same_verbs(page):
    """Keys landing on the page itself route to the same verbs (the
    stray-focus fallback — never a dead key on a cull surface)."""
    verbs = []
    page.decision_verb_requested.connect(verbs.append)
    for key in (Qt.Key.Key_P, Qt.Key.Key_X, Qt.Key.Key_Space, Qt.Key.Key_C):
        ev = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
        page.keyPressEvent(ev)
    assert verbs == ["pick", "skip", "toggle", "cycle"]


def test_border_click_requests_the_cycle_verb(page):
    verbs = []
    page.decision_verb_requested.connect(verbs.append)
    page._surface.media_border_clicked.emit()
    assert verbs == ["cycle"]


def test_focus_proxy_routes_page_focus_to_the_viewport(page):
    assert page.focusProxy() is page._viewport


def test_keyboard_arrows_navigate_cells(page):
    """Single-item load → arrows emit cell-nav signals (PickPage translates
    them into Day Grid steps)."""
    prev_fired, next_fired = [], []
    page.prev_bucket_requested.connect(lambda: prev_fired.append(1))
    page.next_bucket_requested.connect(lambda: next_fired.append(1))
    left = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier)
    right = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier)
    page.keyPressEvent(left)
    page.keyPressEvent(right)
    assert prev_fired and next_fired


def test_viewport_edges_cross_cells(page, tmp_path, fake_meta):
    """Arrows INSIDE the viewport on a single-item list step past the
    edge → the same cell-crossing signals (spec/32 §2.7)."""
    page.load([_vitem(tmp_path)], nav_context="day_grid")
    prev_fired, next_fired = [], []
    page.prev_bucket_requested.connect(lambda: prev_fired.append(1))
    page.next_bucket_requested.connect(lambda: next_fired.append(1))
    QTest.keyClick(page._viewport, Qt.Key.Key_Left)
    QTest.keyClick(page._viewport, Qt.Key.Key_Right)
    assert prev_fired == [1] and next_fired == [1]


def test_multi_item_arrows_navigate_in_list_first(page, tmp_path, fake_meta):
    page.load([_vitem(tmp_path, "a.mp4"), _vitem(tmp_path, "b.mp4")],
              nav_context="day_grid")
    landed = []
    page.current_item_changed.connect(landed.append)
    next_fired = []
    page.next_bucket_requested.connect(lambda: next_fired.append(1))
    QTest.keyClick(page._viewport, Qt.Key.Key_Right)   # in-list → index 1
    assert page._index == 1 and landed == [1] and next_fired == []
    QTest.keyClick(page._viewport, Qt.Key.Key_Right)   # at the end → cross
    assert next_fired == [1]


def test_f11_fullscreen_and_esc_steps_down_one_level(page, tmp_path, fake_meta):
    page.load([_vitem(tmp_path)], nav_context="day_grid")
    flips, backs = [], []
    page.fullscreen_changed.connect(flips.append)
    page.back_requested.connect(lambda: backs.append(True))
    QTest.keyClick(page._viewport, Qt.Key.Key_F11)
    assert page._fullscreen and flips == [True]
    QTest.keyClick(page._viewport, Qt.Key.Key_Escape)
    assert not page._fullscreen and backs == []
    QTest.keyClick(page._viewport, Qt.Key.Key_Escape)
    assert backs == [True]


# --------------------------------------------------------------------------- #
# The viewport carries the poster + the timeline chrome follows its signals
# --------------------------------------------------------------------------- #


def test_poster_rides_the_viewport_item(page, tmp_path, fake_meta):
    """The Day-Grid poster (spec/59 black-frame guarantee) bridges into
    the viewport as a host-supplied pixmap; no poster → plain item."""
    from PyQt6.QtGui import QColor, QImage
    poster = tmp_path / "poster.jpg"
    img = QImage(64, 36, QImage.Format.Format_RGB32)
    img.fill(QColor("orange"))
    assert img.save(str(poster), "JPG", 85)

    page.load([_vitem(tmp_path, "with.mp4", poster=poster),
               _vitem(tmp_path, "without.mp4")], nav_context="day_grid")
    items = page._viewport.items()
    assert items[0].pixmap is not None and not items[0].pixmap.isNull()
    assert items[1].pixmap is None
    assert all(i.kind == "video" for i in items)


def test_f10_is_inert_on_video_even_with_poster(page, tmp_path, fake_meta):
    from PyQt6.QtGui import QColor, QImage
    poster = tmp_path / "poster.jpg"
    img = QImage(64, 36, QImage.Format.Format_RGB32)
    img.fill(QColor("teal"))
    assert img.save(str(poster), "JPG", 85)
    page.load([_vitem(tmp_path, poster=poster)], nav_context="day_grid")
    QTest.keyClick(page._viewport, Qt.Key.Key_F10)
    assert page._viewport._truth_window is None
    assert not page._viewport._inspect_btn.isVisibleTo(page._viewport)


def test_probe_seeds_duration_and_frame_step(page, tmp_path, fake_meta):
    """fps 50 → 20 ms frame steps; the probed duration paints the
    timeline before QtMultimedia says anything."""
    page.load([_vitem(tmp_path)], nav_context="day_grid")
    assert page._frame_ms == 20
    assert page._duration_ms == 8_000
    assert page._time.text().endswith("0:08.000")
    page._seek_to(1_000)
    page._step(+2)
    assert page._pos_ms == 1_040
    page._step(-3)
    assert page._pos_ms == 980


def test_play_button_follows_the_viewport_playing_signal(page):
    # Nelson 2026-06-12 Play/Pause polish — the transport is icon-only
    # now (⏸ when playing, ▶ when stopped); the underlying state-follow
    # contract is unchanged.
    page._on_playing_changed(True)
    assert page._nav_play.text() == "⏸"
    page._on_playing_changed(False)
    assert page._nav_play.text() == "▶"


def test_player_error_shows_the_graceful_message(page, tmp_path, fake_meta):
    """Corrupt/unsupported clip → honest message in the readout slot,
    Pick/Skip untouched (Nelson #4c, passed through by the viewport)."""
    page.load([_vitem(tmp_path)], nav_context="day_grid")
    page._viewport.video_error.emit("DirectShow failed")
    assert "Pick/Skip still works" in page._time.text()
    verbs = []
    page.decision_verb_requested.connect(verbs.append)
    QTest.keyClick(page._viewport, Qt.Key.Key_P)
    assert verbs == ["pick"]
