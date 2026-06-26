"""spec/144 §C — cut-play advances a video on ``EndOfMedia``, NEVER
on a precomputed timer.

Before spec/144 the cut-play scrubber's per-entry duration table
read ``SessionFile.duration_ms`` — i.e. the source video's WHOLE
length (which was 0 when unprobed, or much longer than the segment
otherwise). The earlier design relied on a fixed ``_timer.start()``
keyed off that value; the symptom Nelson saw was the show holding
the LAST FRAME of a short clip for the remainder of the (wrong) source
length, sometimes black on entry. The fix:

* Videos never call ``self._timer.start(...)`` — the timer stays idle.
* ``QMediaPlayer.MediaStatus.EndOfMedia`` triggers ``advance()``, so the
  show moves on the instant the clip's real bytes end.
* Photos / openers / separators still ride the photo timer.

The scrubber's per-entry duration table still uses the segment length
for LAYOUT (so the playhead reads accurately), but the **advance** is
event-driven, decoupling the timing from the table's accuracy.
"""
from __future__ import annotations

import itertools
from pathlib import Path
from types import SimpleNamespace

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.shared.cut_session import show_entries
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.shared.cut_play import CutPlayerDialog

from tests.test_gateway_cuts import _doc, _now


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def dlg(qapp, tmp_path):
    """A Cut player whose entries are PHOTO → VIDEO → PHOTO in show
    order. We pin index 1 = video so the advance tests can target it
    directly. Photos read capture_time-ordered; pinning order means
    setting the lineage rows' source items so the capture_time
    ordering lands us PHOTO/VIDEO/PHOTO.

    Show entries (with separators_on=False): e1.jpg (photo, day 1
    8:00), v1.mp4 (video, day 2 11:00, BUMPED earlier than e3a),
    e3a.jpg (photo, day 2 10:00 → bumped LATER than v1)."""
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    # Reorder the lineage's video to land BEFORE e3a in show order
    # so we get photo → video → photo. (The fixture's source item v1
    # is captured at 11:00 vs e3a at 10:00, so capture_time sorting
    # leaves the video AFTER e3a by default. Bump v1's source
    # capture time to 9:30 so it sorts BETWEEN e1 (day 1) and e3a
    # (day 2 10:00).)
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


class _StubSink:
    """The QVideoSink stand-in — captures the spec/140 one-shot
    ``videoFrameChanged`` handler so the swap test can fire it on
    demand."""

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
    """Just enough QMediaPlayer for ``_show_video`` + ``_on_video_status``
    to drive. ``mediaStatusChanged`` is a duck signal so teardown's
    disconnect call doesn't AttributeError."""

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

    def position(self):
        return 0


def _install_stub_player(player: CutPlayerDialog) -> _StubPlayer:
    from PyQt6.QtWidgets import QWidget
    player._video_widget = QWidget(player._stack_widget)
    player._stack_layout.addWidget(player._video_widget)
    player._video_widget.hide()
    stub = _StubPlayer()
    player._player = stub
    player._ensure_video = lambda: None
    return stub


# --------------------------------------------------------------------- #
# 1. Videos never ride the photo timer; advance is event-driven
# --------------------------------------------------------------------- #


def test_video_entry_does_not_start_photo_timer(dlg):
    """spec/144 §C — the photo timer (``_timer``) is for non-video
    entries. A video entry MUST leave the timer idle so the precomputed
    length can't fire ``advance()`` ahead of (or behind) the clip's
    real EndOfMedia."""
    _install_stub_player(dlg)
    # Force-show the video entry (index 1 = the v1.mp4 file). The dialog
    # initialises ``_index = -1``; ``_show_index(1)`` lands it on the
    # video entry directly.
    dlg._show_index(1)
    kind, payload = dlg._entries[dlg._index]
    assert kind == "file"
    assert getattr(payload, "kind", "") == "video"
    assert dlg._timer.isActive() is False, (
        "spec/144 — videos must leave the photo timer idle; the "
        "EndOfMedia signal drives advance, not a precomputed timer"
    )


def test_end_of_media_advances_to_next_entry(dlg):
    """spec/144 §C — the player's ``EndOfMedia`` mediaStatus triggers
    ``advance()`` to the next entry. The advance is event-driven so a
    short clip doesn't hold the last frame for any extra time, AND a
    long clip doesn't get cut off early."""
    from PyQt6.QtMultimedia import QMediaPlayer
    _install_stub_player(dlg)
    dlg._show_index(1)                                  # the video entry
    assert dlg._index == 1

    # Simulate the clip's real EndOfMedia.
    dlg._on_video_status(QMediaPlayer.MediaStatus.EndOfMedia)
    assert dlg._index == 2, (
        "spec/144 — EndOfMedia must advance the show one entry "
        "(without waiting on any timer)")


def test_invalid_media_also_advances(dlg):
    """A clip that fails to open (corrupted, missing codec) must NOT
    hang the show — ``InvalidMedia`` status routes through the same
    advance() so the user moves on instead of staring at the previous
    photo forever."""
    from PyQt6.QtMultimedia import QMediaPlayer
    _install_stub_player(dlg)
    dlg._show_index(1)
    dlg._on_video_status(QMediaPlayer.MediaStatus.InvalidMedia)
    assert dlg._index == 2


def test_other_media_statuses_do_not_advance(dlg):
    """Buffering / loading / loaded / stalled etc. fire on the way
    INTO playback; they MUST NOT advance the show."""
    from PyQt6.QtMultimedia import QMediaPlayer
    _install_stub_player(dlg)
    dlg._show_index(1)
    for status in (
        QMediaPlayer.MediaStatus.LoadingMedia,
        QMediaPlayer.MediaStatus.LoadedMedia,
        QMediaPlayer.MediaStatus.BufferingMedia,
        QMediaPlayer.MediaStatus.BufferedMedia,
        QMediaPlayer.MediaStatus.StalledMedia,
    ):
        dlg._on_video_status(status)
        assert dlg._index == 1, (
            f"status {status!r} must NOT advance the show — only "
            "EndOfMedia / InvalidMedia drive ``advance()``")


# --------------------------------------------------------------------- #
# 2. Photo / opener / separator entries still drive the timer
# --------------------------------------------------------------------- #


def test_photo_entry_starts_the_photo_timer(dlg):
    """Non-video entries (photos, separators, openers) keep their
    timer-based advance contract; otherwise the show would never move
    on past a still slide."""
    dlg._show_index(0)                                  # the e1.jpg photo
    kind, payload = dlg._entries[dlg._index]
    assert kind == "file"
    assert getattr(payload, "kind", "") == "photo"
    assert dlg._timer.isActive() is True
    # ``remainingTime`` is in ms; the dialog sets ``photo_s = 6.0`` →
    # ~6_000 ms. Allow some slack for test scheduling.
    assert dlg._timer.remainingTime() > 0


def test_video_to_photo_restores_the_timer(dlg):
    """A video → photo transition must re-arm the photo timer for
    the photo entry. (Pre-fix this worked, but pinning it prevents a
    regression where a stray ``_timer.stop()`` from the video path
    leaks into the photo branch.)"""
    _install_stub_player(dlg)
    dlg._show_index(1)                                  # video entry — no timer
    assert dlg._timer.isActive() is False

    dlg._show_index(2)                                  # next photo entry
    kind, payload = dlg._entries[dlg._index]
    assert getattr(payload, "kind", "") == "photo"
    assert dlg._timer.isActive() is True


# --------------------------------------------------------------------- #
# 3. The scrubber still reads the segment length for layout
# --------------------------------------------------------------------- #


def test_scrubber_duration_uses_session_file_duration_for_video(dlg):
    """The scrubber's per-entry duration table (``_durations``)
    continues to use ``SessionFile.duration_ms`` for video entries.
    With spec/144 fixing the SessionFile to carry the SEGMENT length
    (not the source video's whole length), the layout is accurate
    even though advance is event-driven."""
    # Video entry is at index 1; the v1.mp4 lineage in the test
    # fixture carries duration_ms = 30_000.
    assert dlg._durations[1] == 30_000

    # spec/152 Phase 3 — photo entries hold for
    # ``photo_ms + transition_ms`` (matches PTE's [Times] cumulative).
    expected_photo = dlg._photo_ms + dlg._transition_ms_value()
    assert dlg._durations[0] == expected_photo
    assert dlg._durations[2] == expected_photo


def test_scrubber_falls_back_to_photo_ms_for_zero_duration(dlg):
    """When the segment duration can't be resolved (legacy lineage +
    file missing — both yield 0 from ``files_from_lineage``), the
    scrubber MUST use ``photo_ms + transition_ms`` for the layout so
    the playhead is in a sane place. Advance still rides EndOfMedia
    so the show doesn't stall."""
    # Forcefully drop the video entry's duration to 0 to mimic the
    # unresolved case.
    video_payload = dlg._entries[1][1]
    object.__setattr__(video_payload, "duration_ms", 0)
    dlg._recompute_durations()
    expected_fallback = dlg._photo_ms + dlg._transition_ms_value()
    assert dlg._durations[1] == expected_fallback, (
        "spec/144 / spec/152 Phase 3 — a 0-ms video entry must paint "
        "as photo_ms + transition_ms for scrubber layout; advance "
        "still rides EndOfMedia")
