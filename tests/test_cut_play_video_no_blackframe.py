"""spec/140 §1 — Cut play photo→video shows the photo until the first
valid video frame, then swaps to the video widget.

The old behaviour hid the photo and showed the empty ``QVideoWidget``
immediately on ``_show_video`` — the sink paints opaque black until
the first decoded frame arrives, so the transition flashed black for
~100 ms. The fix mirrors ``PhotoViewport``'s no-black-frame contract:
arm the player, but defer the visible swap to the first
``videoFrameChanged`` (or a watchdog timeout, so a clip that never
produces a frame doesn't hang the show).

All tests run offscreen — the QVideoWidget is real but the QMediaPlayer
is a stub so we never spin QtMultimedia for real video decoding.
"""
from __future__ import annotations

import itertools
from pathlib import Path
from types import SimpleNamespace

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.shared.cut_session import show_entries
from mira.store.repo import EventStore
from mira.ui.shared.cut_play import CutPlayerDialog

from tests.test_gateway_cuts import _doc, _now


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def player(qapp, tmp_path):
    """A minimal Cut player wrapped around two photo entries (we
    drive `_show_video` directly, the entry list is just to satisfy
    the constructor)."""
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    for ln in ("e1.jpg", "e3a.jpg"):
        p = tmp_path / "Edited Media" / ln
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    counter = itertools.count(1)
    gw = EventGateway(
        store, event_root=tmp_path, now=_now,
        new_id=lambda: f"id-{next(counter)}")
    gw.set_cut_members(
        "cut-s", ["Exported Media/e1.jpg", "Exported Media/e3a.jpg"])
    entries = show_entries(gw, gw.cut("cut-s"), separators_on=True)
    day_meta = {d.day_number: d for d in gw.trip_days()}
    dlg = CutPlayerDialog(
        entries, event_root=tmp_path, photo_s=6.0,
        day_meta=day_meta, aspect="16:9")
    yield dlg
    try:
        dlg._teardown_media()
    except Exception:                                              # noqa: BLE001
        pass
    dlg.deleteLater()
    gw.close()


class _StubSink:
    """Captures the one-shot ``videoFrameChanged`` connection so the
    test can fire it on demand. Behaves like the QVideoSink the real
    ``QMediaPlayer.videoSink()`` returns — just the signal slot the
    swap handler uses."""

    def __init__(self):
        self._handlers: list = []

    @property
    def videoFrameChanged(self):                                   # noqa: N802
        # The real sink exposes a Qt signal; we expose a duck object
        # with ``connect`` + ``disconnect`` since the production code
        # treats it as a signal.
        return self

    def connect(self, slot):
        self._handlers.append(slot)

    def disconnect(self, slot):
        try:
            self._handlers.remove(slot)
        except ValueError:
            pass

    def emit(self, frame):
        for h in list(self._handlers):
            h(frame)


class _StubPlayer:
    """Just enough of QMediaPlayer for ``_show_video`` to drive +
    ``_teardown_media`` to clean up. ``mediaStatusChanged`` is a duck
    signal so the teardown's disconnect call doesn't AttributeError."""

    def __init__(self):
        self._sink = _StubSink()
        self.source = None
        self.played = False
        self.stopped = False
        self.mediaStatusChanged = SimpleNamespace(
            disconnect=lambda: None)

    def setSource(self, url):                                      # noqa: N802
        self.source = url

    def play(self):
        self.played = True

    def stop(self):
        self.stopped = True

    def videoSink(self):                                           # noqa: N802
        return self._sink


def _install_stub_player(dlg: CutPlayerDialog) -> _StubPlayer:
    """Replace the lazy QMediaPlayer + QVideoWidget with stubs so the
    show_video path runs offscreen. Keeps the player/widget refs the
    swap handler reads."""
    from PyQt6.QtWidgets import QWidget
    dlg._video_widget = QWidget(dlg._stack_widget)
    dlg._stack_layout.addWidget(dlg._video_widget)
    dlg._video_widget.hide()
    stub = _StubPlayer()
    dlg._player = stub
    # Block the lazy constructor that would otherwise overwrite our stubs.
    dlg._ensure_video = lambda: None
    return stub


# ── The headline contract ───────────────────────────────────────────


def test_show_video_does_not_hide_photo_until_first_frame(player):
    """spec/140 §1 — calling ``_show_video`` MUST leave the photo
    visible AND the video widget hidden until the first valid frame
    arrives. (Pre-fix the photo was hidden and the empty video
    widget was shown immediately → black flash.)"""
    stub = _install_stub_player(player)
    player._photo.show()                # baseline: the previous photo is up
    photo_was_visible = not player._photo.isHidden()
    assert photo_was_visible, "precondition: photo starts visible"

    player._show_video(Path("C:/cut/clip.mp4"))

    # Player is armed + playing, but the widgets did NOT swap yet.
    assert stub.source is not None, "setSource must run on arm"
    assert stub.played is True, "player.play() must fire on arm"
    assert player._photo.isHidden() is False, (
        "spec/140 §1: the outgoing photo must stay VISIBLE until "
        "the first video frame arrives (no black flash)"
    )
    assert player._video_widget.isHidden() is True, (
        "spec/140 §1: the empty video widget must stay HIDDEN until "
        "the first frame arrives"
    )


def test_first_valid_frame_triggers_the_swap(player):
    """The one-shot handler hides the photo + shows the video widget
    when (and only when) a valid frame arrives."""
    stub = _install_stub_player(player)
    player._photo.show()
    player._show_video(Path("C:/cut/clip.mp4"))

    # First frame lands → swap.
    stub._sink.emit(SimpleNamespace(isValid=lambda: True))

    assert player._photo.isHidden() is True
    assert player._video_widget.isHidden() is False
    # And the watchdog timer is retired so it can't fire on a
    # stale widget later.
    assert player._video_swap_timer is None


def test_invalid_frame_does_not_trigger_swap(player):
    """The sink sometimes emits an INVALID frame while priming the
    pipeline; the swap must NOT happen on those — only on the
    first VALID frame."""
    stub = _install_stub_player(player)
    player._photo.show()
    player._show_video(Path("C:/cut/clip.mp4"))

    stub._sink.emit(SimpleNamespace(isValid=lambda: False))
    stub._sink.emit(None)

    # No swap yet — photo still up.
    assert player._photo.isHidden() is False
    assert player._video_widget.isHidden() is True

    # Then a real frame lands.
    stub._sink.emit(SimpleNamespace(isValid=lambda: True))
    assert player._photo.isHidden() is True
    assert player._video_widget.isHidden() is False


def test_watchdog_forces_swap_when_no_frame_arrives(player):
    """A clip that never produces a frame (codec mismatch, etc.)
    must NOT hang on the previous photo. The watchdog fires after
    the timeout and swaps anyway so the show advances."""
    stub = _install_stub_player(player)
    player._photo.show()
    player._show_video(Path("C:/cut/broken.mp4"))

    # Manually fire the watchdog instead of waiting the real
    # timeout — that's the contract this test pins.
    player._force_video_swap()

    assert player._photo.isHidden() is True
    assert player._video_widget.isHidden() is False
    # Pending state cleared so subsequent clips start fresh.
    assert player._video_swap_timer is None
    assert player._pending_video_sink is None


def test_video_to_photo_clears_pending_swap(player):
    """A video→photo transition cancels any pending first-frame swap
    so the watchdog can't fire after we've moved on to a photo."""
    stub = _install_stub_player(player)
    player._photo.show()
    player._show_video(Path("C:/cut/clip.mp4"))
    assert player._video_swap_timer is not None
    assert player._pending_video_sink is stub._sink

    # User skips forward to a photo — _show_pixmap is the call path.
    from PyQt6.QtGui import QPixmap
    player._show_pixmap(QPixmap(8, 8))
    assert player._video_swap_timer is None
    assert player._pending_video_sink is None


def test_teardown_clears_pending_swap(player):
    """``_teardown_media`` (close / Esc / advance-off-end) must drop
    the pending swap state so the watchdog can't fire on a freed
    widget after the dialog is gone."""
    _install_stub_player(player)
    player._photo.show()
    player._show_video(Path("C:/cut/clip.mp4"))
    assert player._video_swap_timer is not None

    player._teardown_media()
    assert player._video_swap_timer is None
    assert player._pending_video_sink is None


def test_second_show_video_resets_swap_state(player):
    """Two videos back-to-back: the second ``_show_video`` resets the
    one-shot from the first so the watchdog from clip A can't fire
    against clip B's widget."""
    stub = _install_stub_player(player)
    player._photo.show()
    player._show_video(Path("C:/cut/a.mp4"))
    first_timer = player._video_swap_timer
    first_sink = player._pending_video_sink
    assert first_timer is not None

    player._show_video(Path("C:/cut/b.mp4"))
    # Fresh timer + sink registration; the previous handler is
    # disconnected so emit on it doesn't trip the new clip.
    assert player._video_swap_timer is not first_timer
    assert player._pending_video_sink is first_sink   # same player→same sink
    # Emit a frame: it MUST drive the new clip's swap (single
    # registered handler from the new call).
    stub._sink.emit(SimpleNamespace(isValid=lambda: True))
    assert player._photo.isHidden() is True
    assert player._video_widget.isHidden() is False
