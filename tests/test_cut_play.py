"""spec/61 slice 8 — the rehearsal player's sequencing (multimedia-free).

The player is built so a photos-only show with no music NEVER touches
QtMultimedia (lazy players) — these tests drive the sequencing over a
real entry list: order, photo timing, pause, stepping, and the finish.
Real audio/video playback is the user's eyeball territory.
"""
from __future__ import annotations

import itertools

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.shared.cut_session import show_entries
from mira.store.repo import EventStore
from mira.ui.shared.cut_play import CutPlayerDialog

from tests.test_gateway_cuts import _doc, _now


@pytest.fixture
def gw(tmp_path):
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    for ln in ("e1.jpg", "e3a.jpg"):
        p = tmp_path / "Edited Media" / ln
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    counter = itertools.count(1)
    g = EventGateway(store, event_root=tmp_path, now=_now,
                     new_id=lambda: f"id-{next(counter)}")
    g.set_cut_members("cut-s", ["Edited Media/e1.jpg", "Edited Media/e3a.jpg"])
    yield g
    g.close()


def _player(gw, tmp_path) -> CutPlayerDialog:
    from PyQt6.QtGui import QImage
    entries = show_entries(gw, gw.cut("cut-s"), separators_on=True)
    day_meta = {d.day_number: d for d in gw.trip_days()}
    return CutPlayerDialog(
        entries, event_root=tmp_path, photo_s=6.0,
        day_meta=day_meta, aspect="16:9",
        opener_image=QImage(16, 9, QImage.Format.Format_RGB32))


def test_show_entries_is_the_wysiwyg_sequence(gw):
    """Opener first (round 2), then separators at day boundaries."""
    entries = show_entries(gw, gw.cut("cut-s"), separators_on=True)
    assert [(k, getattr(p, "export_relpath", p)) for k, p in entries] == [
        ("opener", None),
        ("sep", 1), ("file", "Edited Media/e1.jpg"),
        ("sep", 2), ("file", "Edited Media/e3a.jpg")]
    bare = show_entries(gw, gw.cut("cut-s"), separators_on=False)
    assert [k for k, _ in bare] == ["file", "file"]


def test_advance_walks_entries_and_times_photos(qapp, gw, tmp_path):
    p = _player(gw, tmp_path)
    assert p._photo_ms == 6000
    p.advance()                                   # the opener card
    assert p._index == 0 and p._timer.isActive()
    p.advance()                                   # sep day 1
    assert p._index == 1 and p._timer.isActive()
    # a photos-only show never instantiated QtMultimedia
    assert p._player is None and p._music is None


def test_missing_opener_image_skips_cleanly(qapp, gw, tmp_path):
    entries = show_entries(gw, gw.cut("cut-s"), separators_on=True)
    p = CutPlayerDialog(
        entries, event_root=tmp_path, photo_s=6.0,
        day_meta={d.day_number: d for d in gw.trip_days()}, aspect="16:9")
    p.advance()                                   # opener has no image → hops
    assert p._index == 1


def test_windowed_by_default_f11_toggles_esc_steps_down(qapp, gw, tmp_path):
    """Nelson 2026-06-12: Play opens WINDOWED; F11/F toggles full
    screen; Esc steps down one level (full → window → end)."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeyEvent
    p = _player(gw, tmp_path)
    p.start()
    assert not p.isFullScreen()
    p._toggle_fullscreen()
    assert p.isFullScreen()
    esc = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape,
                    Qt.KeyboardModifier.NoModifier)
    p.keyPressEvent(esc)                          # full → window
    assert not p.isFullScreen()
    done = []
    p.accepted.connect(lambda: done.append(True))
    p.keyPressEvent(esc)                          # window → end
    assert done == [True]


def test_fullscreen_roundtrip_restores_geometry(qapp, gw, tmp_path):
    """Bug 2026-06-12: the label's pixmap drove the window minimum
    (QLabel minimumSizeHint == pixmap size, enforced as the WINDOW
    minimum by the top-level layout), so F11-out restored a near-
    screen-sized window — and the min-size fight inside Windows'
    synchronous resize negotiation could wedge the event loop."""
    from PyQt6.QtGui import QPixmap
    p = _player(gw, tmp_path)
    p.start()
    g0 = p.geometry()
    # a big slide must never drive the dialog's minimum size
    p._show_pixmap(QPixmap(3000, 2000))
    assert p.minimumSizeHint().width() < 400
    p._toggle_fullscreen()
    assert p.isFullScreen()
    p._show_pixmap(QPixmap(3000, 2000))       # a fullscreen-sized slide
    p._toggle_fullscreen()                    # back down one level
    qapp.processEvents()                      # the deferred re-assert
    assert not p.isFullScreen()
    g1 = p.geometry()
    assert (g1.width(), g1.height()) == (g0.width(), g0.height())


def test_pause_stops_the_clock(qapp, gw, tmp_path):
    p = _player(gw, tmp_path)
    p.advance()
    p._toggle_pause()
    assert p._paused and not p._timer.isActive()
    p._toggle_pause()
    assert not p._paused and p._timer.isActive()


def test_step_back_and_finish(qapp, gw, tmp_path):
    p = _player(gw, tmp_path)
    for _ in range(5):
        p.advance()
    assert p._index == 4
    p.step_back()
    assert p._index == 3
    done = []
    p.accepted.connect(lambda: done.append(True))
    p.advance()
    p.advance()                                   # past the end → finish
    assert done == [True]
    assert not p._timer.isActive()
