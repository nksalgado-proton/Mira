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
    g.set_cut_members("cut-s", ["Exported Media/e1.jpg", "Exported Media/e3a.jpg"])
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
        ("sep", 1), ("file", "Exported Media/e1.jpg"),
        ("sep", 2), ("file", "Exported Media/e3a.jpg")]
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
    synchronous resize negotiation could wedge the event loop.

    Post-2026-06-19 (transport-as-layout): the dialog's natural min
    width is whatever the transport bar contents demand. The 'a big
    slide must never drive the dialog min' assertion still holds, just
    expressed differently — showing a 3000×2000 pixmap doesn't bump
    the min width above the transport's own contribution."""
    from PyQt6.QtGui import QPixmap
    p = _player(gw, tmp_path)
    p.start()
    g0 = p.geometry()
    # Record the dialog's min width BEFORE the big slide arrives — this
    # is what the transport bar's chrome contributes. The pixmap must
    # not push it any higher.
    min_w_before = p.minimumSizeHint().width()
    p._show_pixmap(QPixmap(3000, 2000))
    assert p.minimumSizeHint().width() == min_w_before
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


# ─────────────────────────────────────────────────────────────────────────────
# Live overlays (spec/81 §3.1) — when/where/how¹/how² over each frame
# ─────────────────────────────────────────────────────────────────────────────


def _player_with_overlays(gw, tmp_path, *, fields, resolver):
    from PyQt6.QtGui import QImage
    entries = show_entries(gw, gw.cut("cut-s"), separators_on=True)
    day_meta = {d.day_number: d for d in gw.trip_days()}
    return CutPlayerDialog(
        entries, event_root=tmp_path, photo_s=6.0,
        day_meta=day_meta, aspect="16:9",
        opener_image=QImage(16, 9, QImage.Format.Format_RGB32),
        overlay_fields=fields,
        provenance_resolver=resolver)


def test_no_overlay_label_when_fields_empty(qapp, gw, tmp_path):
    """Spec/81 §3.1: a Cut with no overlay fields runs the rehearsal
    without ever building the overlay label — byte-for-byte the pre-
    spec/81 player path."""
    from core.cut_overlay import FrameProvenance
    p = _player_with_overlays(
        gw, tmp_path, fields=(),
        resolver=lambda _rel: FrameProvenance(when="2026-06-16"))
    assert p._overlay_label is None                # noqa: SLF001


def test_overlay_text_draws_for_file_frames(qapp, gw, tmp_path):
    """A file frame whose resolver returns provenance with the selected
    fields lights up the overlay label with composed text. Each
    advance() refreshes from the resolver."""
    from core.cut_overlay import FrameProvenance
    calls: list[str] = []

    def resolver(relpath):
        calls.append(relpath)
        return FrameProvenance(
            when="June 14, 2026 · 14:23",
            city="Cabaceira", country="Portugal",
            event_name="Costa Rica 2026")

    p = _player_with_overlays(
        gw, tmp_path, fields=("when", "where"), resolver=resolver)
    p.advance()                                    # opener — no overlay
    assert not p._overlay_label.isVisible()        # noqa: SLF001
    p.advance()                                    # sep — no overlay
    assert not p._overlay_label.isVisible()        # noqa: SLF001
    p.advance()                                    # file frame 1
    text = p._overlay_label.text()                 # noqa: SLF001
    assert "June 14, 2026 · 14:23" in text
    assert "Costa Rica 2026" in text
    assert "Cabaceira" in text
    assert calls[-1] == "Exported Media/e1.jpg"


def test_overlay_hides_when_resolver_returns_none(qapp, gw, tmp_path):
    """A relpath the gateway can't join (missing provenance) hides the
    label gracefully — never crashes the rehearsal."""
    p = _player_with_overlays(
        gw, tmp_path, fields=("when",), resolver=lambda _rel: None)
    # walk to the first file frame
    p.advance(); p.advance(); p.advance()
    assert not p._overlay_label.isVisible()        # noqa: SLF001


def test_overlay_resolver_errors_are_swallowed(qapp, gw, tmp_path):
    """A resolver that raises (corrupt row, gateway hiccup) must not
    crash the player; the label hides for that frame and Play continues."""
    def boom(_relpath):
        raise RuntimeError("synthetic")

    p = _player_with_overlays(
        gw, tmp_path, fields=("when",), resolver=boom)
    p.advance(); p.advance(); p.advance()          # walk to file frame
    assert not p._overlay_label.isVisible()        # noqa: SLF001
    # And the rehearsal didn't end / break.
    assert p._index == 2


def test_overlay_fields_filter_what_renders(qapp, gw, tmp_path):
    """Selecting only 'where' renders ONLY the where line — no when,
    no how¹/². Confirms the dialog uses the shared formatter."""
    from core.cut_overlay import FrameProvenance
    p = _player_with_overlays(
        gw, tmp_path, fields=("where",),
        resolver=lambda _rel: FrameProvenance(
            when="2026-06-14", city="Cabaceira",
            camera="Canon R5", lens_model="100-500mm",
            aperture_f=7.1, iso=400))
    p.advance(); p.advance(); p.advance()          # walk to file frame
    text = p._overlay_label.text()                 # noqa: SLF001
    assert "Cabaceira" in text
    assert "2026-06-14" not in text
    assert "Canon R5" not in text
    assert "ISO" not in text


# ─────────────────────────────────────────────────────────────────────────────
# Transport bar (Nelson 2026-06-19) — Stop / Sep jumps / Play-Pause / scrubber
# seek / live slide-time / time read-out
# ─────────────────────────────────────────────────────────────────────────────


def test_durations_table_uses_true_clip_length(qapp, gw, tmp_path):
    """spec/152 Phase 3 — photos / opener / separators each get
    ``photo_ms + transition_ms`` (matches PTE's [Times] cumulative
    which adds transition_ms per non-video slide). Videos get their
    true ``SessionFile.duration_ms`` (spec/150 §1)."""
    from mira.shared.cut_session import SessionFile
    entries = [
        ("opener", None),
        ("sep", 1),
        ("file", SessionFile(export_relpath="a.jpg", kind="photo")),
        ("file", SessionFile(export_relpath="b.mp4", kind="video",
                             duration_ms=4_500)),
    ]
    from PyQt6.QtGui import QImage
    p = CutPlayerDialog(
        entries, event_root=tmp_path, photo_s=6.0,
        day_meta={d.day_number: d for d in gw.trip_days()},
        aspect="16:9",
        opener_image=QImage(16, 9, QImage.Format.Format_RGB32))
    # Force a known transition_ms (the stub dialog has no settings
    # backplane; ``_transition_ms_value`` defaults to 2000).
    p._transition_ms_value = lambda: 2000
    p._recompute_durations()
    # 3 non-video slots × (6_000 + 2_000) + 1 video × 4_500 = 28_500
    assert p._durations == [8000, 8000, 8000, 4500]
    assert p._sep_indexes == [1]
    assert p._total_ms() == 28_500


def test_scrubber_click_seeks_to_entry(qapp, gw, tmp_path):
    """Clicking the scrubber jumps the playhead — we snap to the entry
    start (intra-clip seeking is for v2)."""
    p = _player(gw, tmp_path)
    p.start()
    p._on_scrubber_seeked(3, 0)
    assert p._index == 3


def test_jump_to_separator_walks_prev_next(qapp, gw, tmp_path):
    """⏮ / ⏭ jumps to the previous / next separator. The opener counts
    as an anchor so an early ⏮ still has a destination."""
    p = _player(gw, tmp_path)
    p.start()
    # entries: opener(0), sep1(1), file(2), sep2(3), file(4)
    p._show_index(4)
    p._jump_to_separator(-1)
    assert p._index == 3
    p._jump_to_separator(-1)
    assert p._index == 1
    p._jump_to_separator(-1)
    assert p._index == 0                  # opener is the prev-most anchor
    p._jump_to_separator(1)
    assert p._index == 1


def test_slide_time_spinbox_updates_durations_live(qapp, gw, tmp_path):
    """Changing 'Per slide' updates ``_photo_ms`` AND rebuilds the
    scrubber durations on the spot. spec/152 Phase 3: the per-entry
    duration is ``photo_ms + transition_ms`` for non-video slides."""
    p = _player(gw, tmp_path)
    p.start()
    p._transition_ms_value = lambda: 2000
    p._recompute_durations()
    assert p._photo_ms == 6000
    p._on_slide_time_changed(3.5)
    assert p._photo_ms == 3500
    # photos / separators / opener all hold for 3.5 s + 2.0 s = 5500.
    assert all(d == 5500 for d in p._durations)


def test_time_label_reflects_played_and_total(qapp, gw, tmp_path):
    """The mm:ss read-out shows played / total. spec/152 Phase 3:
    each non-video slot carries ``photo_ms + transition_ms``."""
    p = _player(gw, tmp_path)
    p._transition_ms_value = lambda: 2000
    p._recompute_durations()
    p.start()
    p._show_index(2)                     # the first file frame
    p._update_time_label()
    # total = 5 entries * (6 + 2) s = 40 s; played starts at entry prefix.
    assert p._time_label.text().endswith("/ 00:40")


def test_play_icon_toggles_on_pause(qapp, gw, tmp_path):
    """The transport play/pause state flips with the pause state.

    spec/152 §X — the dialog now uses the canonical
    :func:`transport_button` (SVG line-icon, theme-tinted) instead of
    the legacy Unicode ▶/⏸ glyph pair. The visible state lives on the
    button's ``_playing`` flag (an underscore attribute by design —
    :func:`set_transport_playing` is the public flip), so the test
    asserts that instead of widget text."""
    p = _player(gw, tmp_path)
    p.start()
    # _player.start() runs in not-paused mode → button shows the
    # "playing" icon (pause glyph).
    assert getattr(p._btn_play, "_playing", None) is True
    p._toggle_pause()
    assert getattr(p._btn_play, "_playing", None) is False
    p._toggle_pause()
    assert getattr(p._btn_play, "_playing", None) is True


def test_stop_button_finishes_and_stops_timers(qapp, gw, tmp_path):
    """The Stop button (== ``_finish``) accepts the dialog AND stops the
    ticker so an idle background timer doesn't leak past the rehearsal."""
    p = _player(gw, tmp_path)
    p.start()
    assert p._ticker.isActive()
    done = []
    p.accepted.connect(lambda: done.append(True))
    p._finish()
    assert done == [True]
    assert not p._ticker.isActive()
    assert not p._timer.isActive()


def test_close_event_tears_down_media(qapp, gw, tmp_path):
    """Nelson 2026-06-19 — closing the window via the X button (or
    Alt-F4) bypasses ``_finish`` (it's only on the Stop button + Esc +
    natural end). The closeEvent override now runs the same teardown
    so the music QMediaPlayer is stopped on every exit path."""
    from PyQt6.QtGui import QCloseEvent
    p = _player(gw, tmp_path)
    p.start()
    assert p._ticker.isActive()
    # Simulate Qt sending a close event (what the X button does).
    p.closeEvent(QCloseEvent())
    assert not p._ticker.isActive()
    assert not p._timer.isActive()
    assert getattr(p, "_torn_down", False) is True


def test_teardown_is_idempotent(qapp, gw, tmp_path):
    """A user pressing Stop, then closing the window, must not crash —
    the second teardown is a no-op."""
    p = _player(gw, tmp_path)
    p.start()
    p._finish()
    p._teardown_media()                       # second pass
    p._teardown_media()                       # third pass — still no-op
    assert getattr(p, "_torn_down", False) is True


def test_teardown_silences_music_player_on_finish(qapp, gw, tmp_path):
    """The Alaska report (Nelson 2026-06-19): music kept playing after
    the rehearsal ended. ``_teardown_media`` now (a) disconnects the
    EndOfMedia handler so the next-track race can't re-arm, (b) stops
    the player, (c) drops the source so the decoder stops buffering,
    (d) zeroes the audio-output volume so any in-flight samples are
    silent. Verified via a lightweight stub — instantiating a real
    QMediaPlayer is unstable in pytest-qt teardown."""
    p = _player(gw, tmp_path)
    p.start()

    class _MusicStub:
        def __init__(self):
            self.stopped = False
            self.source_cleared = False
            self.disconnected = False
        def stop(self): self.stopped = True
        def setSource(self, _url):
            self.source_cleared = True
            self.url = _url

        class _Sig:
            def __init__(self, outer):
                self._outer = outer
            def disconnect(self):
                if self._outer.disconnected:
                    raise TypeError("already disconnected")
                self._outer.disconnected = True

        @property
        def mediaStatusChanged(self):
            if not hasattr(self, "_sig"):
                self._sig = self._Sig(self)
            return self._sig

    class _AudioStub:
        def __init__(self):
            self._vol = 0.6
        def setVolume(self, v): self._vol = float(v)
        def volume(self): return self._vol

    music = _MusicStub()
    audio = _AudioStub()
    p._music = music
    p._music_audio = audio

    p._finish()
    assert music.disconnected is True
    assert music.stopped is True
    assert music.source_cleared is True
    assert audio.volume() == 0.0


def test_transport_is_a_layout_participant_below_the_canvas(qapp, gw, tmp_path):
    """Nelson 2026-06-19 — the transport rides the dialog's QVBoxLayout
    pinned to the bottom; the slide canvas sits in a stack widget on
    top. The earlier overlay design lost the bar the moment the video
    widget started painting (Qt's video sink draws over child
    overlays). The bar is now a real layout participant: always
    visible, never overlapped by the video, with the canvas getting
    exactly the area above it."""
    p = _player(gw, tmp_path)
    p.start()
    qapp.processEvents()
    # The transport is the SECOND widget in the dialog's layout (the
    # stack widget is first), with a non-zero height.
    assert p._layout.count() == 2
    assert p._layout.itemAt(0).widget() is p._stack_widget
    assert p._layout.itemAt(1).widget() is p._transport
    assert p._transport.height() > 0
    # The slide canvas lives inside the stack widget, not the dialog,
    # so its bottom never crosses the transport's top.
    assert p._photo.parent() is p._stack_widget
    assert p._stack_widget.geometry().bottom() <= p._transport.geometry().top() + 1
