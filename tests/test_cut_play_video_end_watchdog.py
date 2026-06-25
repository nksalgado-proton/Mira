"""spec/150 §2 — cut-play arms a last-frame watchdog on video entries.

The primary advance path stays ``QMediaPlayer.MediaStatus.EndOfMedia``
(spec/144 §C). On Windows the WMF/Qt6 backend fires that signal a few
hundred ms to ~1 s after the last visible frame while it drains audio
and flips ``playbackState``; the last frame stays on the
``QVideoWidget`` during the gap, which reads as a freeze. The
backstop:

* Every video entry arms a single-shot timer for
  ``duration_ms / rate + slack`` (``_VIDEO_END_SLACK_MS = 150``).
* If EndOfMedia fires first, the watchdog is torn down and the show
  advances normally.
* If EndOfMedia is laggy / silent, the watchdog fires and advances.
* The two paths are idempotent — a late watchdog fire after EndOfMedia
  already advanced is a no-op (the entry index has moved).
* Pause stops the watchdog; resume re-arms via ``_apply_video_rate``
  with the remaining time. Teardown stops it.
* The spec/145 rehearsal speed override scales the armed interval —
  a 2× rate halves the wait before the backstop fires.
"""
from __future__ import annotations

import itertools
from types import SimpleNamespace

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.shared.cut_session import show_entries
from mira.store.repo import EventStore
from mira.ui.shared.cut_play import (
    _VIDEO_END_SLACK_MS,
    CutPlayerDialog,
)

from tests.test_gateway_cuts import _doc, _now


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def dlg(qapp, tmp_path):
    """A Cut player with entries PHOTO → VIDEO → PHOTO in show order
    (same shape as ``tests/test_cut_play_video_advance.py``). The
    video at index 1 carries ``duration_ms = 30_000`` so the watchdog
    intervals are easy arithmetic."""
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    # Reorder so the video lands between the two photos in show order.
    store.conn.execute(
        "UPDATE item SET capture_time_corrected = ?, "
        "capture_time_raw = ? WHERE id = 'v1'",
        ("2026-04-02T09:30:00", "2026-04-02T09:30:00"))
    counter = itertools.count(1)
    gw = EventGateway(
        store, event_root=tmp_path, now=_now,
        new_id=lambda: f"id-{next(counter)}")
    gw.set_cut_members(
        "cut-s", ["Exported Media/e1.jpg",
                  "Exported Media/v1.mp4",
                  "Exported Media/e3a.jpg"])
    entries = show_entries(gw, gw.cut("cut-s"), separators_on=False)
    day_meta = {d.day_number: d for d in gw.trip_days()}
    cut_player = CutPlayerDialog(
        entries, event_root=tmp_path, photo_s=6.0,
        day_meta=day_meta, aspect="16:9")
    yield cut_player
    try:
        cut_player._teardown_media()
    except Exception:                                              # noqa: BLE001
        pass
    cut_player.deleteLater()
    gw.close()


# ── Stub player (extends the test_cut_play_video_advance shape) ─────


class _StubSink:
    """Captures the spec/140 one-shot ``videoFrameChanged`` handler so
    we don't need a real video sink."""

    def __init__(self):
        self._handlers: list = []

    @property
    def videoFrameChanged(self):                                   # noqa: N802
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
    """Just enough QMediaPlayer for the cut-play paths the watchdog
    cares about. ``position`` is a settable attribute so the rate
    re-arming tests can simulate a mid-clip rate change."""

    def __init__(self):
        self._sink = _StubSink()
        self.source = None
        self.played = False
        self.paused = False
        self.stopped = False
        self.rate = 1.0
        self._position = 0
        self.mediaStatusChanged = SimpleNamespace(
            disconnect=lambda: None)

    def setSource(self, url):                                      # noqa: N802
        self.source = url

    def play(self):
        self.played = True
        self.paused = False

    def pause(self):
        self.paused = True

    def stop(self):
        self.stopped = True

    def videoSink(self):                                           # noqa: N802
        return self._sink

    def position(self):
        return self._position

    def setPlaybackRate(self, r):                                  # noqa: N802
        self.rate = float(r)


def _install_stub_player(player: CutPlayerDialog) -> _StubPlayer:
    from PyQt6.QtWidgets import QWidget
    player._video_widget = QWidget(player._stack_widget)
    player._stack_layout.addWidget(player._video_widget)
    player._video_widget.hide()
    stub = _StubPlayer()
    player._player = stub
    player._ensure_video = lambda: None
    return stub


# ── 1. Watchdog arms on a video entry ────────────────────────────────


def test_watchdog_arms_on_video_entry_at_default_rate(dlg):
    """spec/150 §2 — entering a video entry arms the end watchdog
    for ``duration_ms / rate + slack``. The fixture's video is
    30_000 ms and the dialog defaults to rate 1.0."""
    _install_stub_player(dlg)
    dlg._show_index(1)                                  # the v1.mp4 file
    assert dlg._video_end_timer is not None, (
        "spec/150 §2: a video entry must arm the end watchdog")
    assert dlg._video_end_timer.isActive()
    assert dlg._video_end_armed_for_index == 1
    assert dlg._video_end_timer.interval() == 30_000 + _VIDEO_END_SLACK_MS


def test_watchdog_skips_zero_duration(dlg):
    """spec/150 §2 — when the entry's ``duration_ms`` is 0 (unresolved
    segment / probe failed), the watchdog is NOT armed; the show
    relies on EndOfMedia alone rather than guessing."""
    video_payload = dlg._entries[1][1]
    object.__setattr__(video_payload, "duration_ms", 0)
    _install_stub_player(dlg)
    dlg._show_index(1)
    assert dlg._video_end_timer is None, (
        "spec/150 §2: a zero/unknown duration must NOT arm the "
        "watchdog (no usable backstop interval)")


def test_watchdog_not_armed_on_photo_entry(dlg):
    """A photo entry must NOT arm the end watchdog — only video
    entries get the backstop (photos already have the photo timer)."""
    _install_stub_player(dlg)
    dlg._show_index(0)                                  # the e1.jpg photo
    assert dlg._video_end_timer is None


# ── 2. Watchdog firing advances the show ─────────────────────────────


def test_watchdog_advances_when_end_of_media_is_silent(dlg):
    """spec/150 §2 — when EndOfMedia never fires (laggy Windows
    backend, broken pipeline), the watchdog firing must advance
    the show so the user doesn't see a frozen last frame."""
    _install_stub_player(dlg)
    dlg._show_index(1)
    assert dlg._index == 1
    # Simulate the watchdog firing (without waiting wall-clock time).
    dlg._on_video_end_watchdog()
    assert dlg._index == 2, (
        "spec/150 §2: a late/absent EndOfMedia must NOT hold the show "
        "on the video — the watchdog drives advance instead")


# ── 3. EndOfMedia first → no double-advance ──────────────────────────


def test_end_of_media_stops_the_watchdog(dlg):
    """spec/150 §2 — EndOfMedia is the primary advance path. It must
    tear the watchdog down before advancing so a late timer fire
    can't double-step."""
    from PyQt6.QtMultimedia import QMediaPlayer
    _install_stub_player(dlg)
    dlg._show_index(1)
    assert dlg._video_end_timer is not None
    dlg._on_video_status(QMediaPlayer.MediaStatus.EndOfMedia)
    assert dlg._index == 2
    assert dlg._video_end_timer is None, (
        "spec/150 §2: EndOfMedia must stop the watchdog so a late "
        "fire can't trigger _on_video_end_watchdog again")
    assert dlg._video_end_armed_for_index == -1


def test_late_watchdog_after_end_of_media_does_not_double_advance(dlg):
    """spec/150 §2 — even if the watchdog handler somehow runs AFTER
    EndOfMedia already advanced (e.g. queued before stop took effect),
    the idempotency guard (``_video_end_armed_for_index`` vs current
    ``_index``) prevents a second advance."""
    from PyQt6.QtMultimedia import QMediaPlayer
    _install_stub_player(dlg)
    dlg._show_index(1)
    dlg._on_video_status(QMediaPlayer.MediaStatus.EndOfMedia)
    assert dlg._index == 2
    # Simulate a late watchdog fire — index has already moved off 1.
    dlg._on_video_end_watchdog()
    assert dlg._index == 2, (
        "spec/150 §2: a late watchdog fire after EndOfMedia already "
        "advanced must be a no-op (no double-step)")


def test_invalid_media_also_stops_the_watchdog(dlg):
    """``InvalidMedia`` routes through the same advance path; it must
    also tear the watchdog down."""
    from PyQt6.QtMultimedia import QMediaPlayer
    _install_stub_player(dlg)
    dlg._show_index(1)
    assert dlg._video_end_timer is not None
    dlg._on_video_status(QMediaPlayer.MediaStatus.InvalidMedia)
    assert dlg._index == 2
    assert dlg._video_end_timer is None


# ── 4. Teardown stops the watchdog ───────────────────────────────────


def test_teardown_stops_the_watchdog(dlg):
    """A dialog being torn down (Esc / Stop / close) must stop the
    watchdog so it can't fire on a half-destroyed widget."""
    _install_stub_player(dlg)
    dlg._show_index(1)
    assert dlg._video_end_timer is not None
    dlg._teardown_media()
    assert dlg._video_end_timer is None, (
        "spec/150 §2: teardown must stop the end watchdog (no fires "
        "on a destroyed dialog)")


def test_video_to_photo_transition_stops_the_watchdog(dlg):
    """Moving from a video entry to a photo entry retires the
    watchdog through the same ``_reset_video_swap_state`` path the
    first-frame swap helpers use — both per-clip helpers go away
    together."""
    _install_stub_player(dlg)
    dlg._show_index(1)
    assert dlg._video_end_timer is not None
    dlg._show_index(2)                                  # next photo entry
    assert dlg._video_end_timer is None, (
        "spec/150 §2: video → photo must retire the end watchdog "
        "via _reset_video_swap_state")


# ── 5. Pause stops the watchdog; resume re-arms ──────────────────────


def test_pause_stops_the_watchdog(dlg):
    """Pausing the rehearsal must stop the watchdog — otherwise it
    would fire on wall-clock time even though the player is paused
    on the frame it stopped at."""
    _install_stub_player(dlg)
    dlg._show_index(1)
    assert dlg._video_end_timer is not None
    dlg._toggle_pause()
    assert dlg._paused is True
    assert dlg._video_end_timer is None, (
        "spec/150 §2: pause must stop the watchdog (wall-clock-vs-"
        "playback drift would fire it too early on resume)")


def test_resume_rearms_the_watchdog(dlg):
    """Resuming from pause on a video entry re-arms the watchdog via
    ``_apply_video_rate`` so the backstop survives a pause cycle."""
    stub = _install_stub_player(dlg)
    dlg._show_index(1)
    dlg._toggle_pause()
    assert dlg._video_end_timer is None
    # Simulate the clip having played 5 s before the pause.
    stub._position = 5_000
    dlg._toggle_pause()
    assert dlg._paused is False
    assert dlg._video_end_timer is not None, (
        "spec/150 §2: resume from pause must re-arm the watchdog")
    # remaining = 30_000 - 5_000 = 25_000; rate = 1.0 → 25_150 ms.
    assert dlg._video_end_timer.interval() == 25_000 + _VIDEO_END_SLACK_MS


# ── 6. Rate override scales the armed interval ───────────────────────


def test_rate_override_scales_the_initial_interval(dlg):
    """spec/145 + spec/150 §2 — a 2× rehearsal rate halves the time
    until the backstop would fire (the clip ends sooner at 2×, so
    the watchdog's wait shrinks to match)."""
    _install_stub_player(dlg)
    dlg._video_rate = 2.0
    dlg._show_index(1)
    assert dlg._video_end_timer is not None
    # 30_000 / 2 + 150 = 15_150.
    assert dlg._video_end_timer.interval() == 15_000 + _VIDEO_END_SLACK_MS


def test_live_rate_change_rearms_the_watchdog(dlg):
    """A mid-clip rate change re-arms the watchdog with the remaining
    time at the new rate — mirrors ``_apply_video_rate``. Without
    this, a 2× rate would still wait the old 1× interval."""
    stub = _install_stub_player(dlg)
    dlg._show_index(1)
    assert dlg._video_end_timer.interval() == 30_000 + _VIDEO_END_SLACK_MS
    # The clip has played 4 s; the user bumps the rate to 2×.
    stub._position = 4_000
    dlg._video_rate = 2.0
    dlg._apply_video_rate()
    # remaining = 30_000 - 4_000 = 26_000; rate = 2.0 → 13_150 ms.
    assert dlg._video_end_timer.interval() == 13_000 + _VIDEO_END_SLACK_MS, (
        "spec/150 §2: a live rate change must re-arm the watchdog "
        "with (duration - position) / new_rate + slack")


def test_slower_rate_extends_the_interval(dlg):
    """A 0.5× rate doubles the interval — the clip takes twice as
    long to end, so the backstop waits twice as long."""
    _install_stub_player(dlg)
    dlg._video_rate = 0.5
    dlg._show_index(1)
    # 30_000 / 0.5 + 150 = 60_150.
    assert dlg._video_end_timer.interval() == 60_000 + _VIDEO_END_SLACK_MS
