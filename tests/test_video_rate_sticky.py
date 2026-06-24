"""spec/137 — the video playback rate is sticky across clips.

The engine has carried the rate across clips since spec/130
(``PhotoViewport._video_rate`` initialised to 1.0, re-applied to every
new clip by ``_ensure_player``). The spec/137 surface contract:

  * Expose the engine's current rate via :meth:`PhotoViewport.
    video_playback_rate` — a mirror of :meth:`video_is_playing` that
    transport-bar hosts read to keep their dropdown in sync.
  * Setting the rate live still applies to the currently-armed clip
    (the cached ``_video_rate`` AND, when the player exists, the
    real ``QMediaPlayer.setPlaybackRate``).
  * The carry-over is the WANTED behaviour: a clip after a 2× clip
    must still play at 2× — only the UI indicator was wrong before
    (a separate spec/137 fix on the transport-bar host wiring).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from mira.ui.media.photo_viewport import PhotoViewport


def test_default_playback_rate_is_1x(qapp):
    """A fresh viewport reports 1.0× — the engine baseline."""
    vp = PhotoViewport()
    try:
        assert vp.video_playback_rate() == pytest.approx(1.0)
    finally:
        vp.deleteLater()


def test_video_playback_rate_returns_cached_video_rate(qapp):
    """``video_playback_rate`` MUST read the engine's truth — the
    ``_video_rate`` field that ``_ensure_player`` re-applies to every
    clip — so the transport bar's reveal-resync can show the carried
    rate on the next clip."""
    vp = PhotoViewport()
    try:
        vp.video_set_playback_rate(2.0)
        assert vp.video_playback_rate() == pytest.approx(2.0)
        # And it round-trips: changing back to 0.5 reflects too.
        vp.video_set_playback_rate(0.5)
        assert vp.video_playback_rate() == pytest.approx(0.5)
    finally:
        vp.deleteLater()


def test_rate_carries_across_a_clip_arm_change(qapp):
    """spec/137 §1 — sticky behaviour: the cached ``_video_rate``
    survives an arm-change (the engine re-applies it in
    ``_ensure_player`` on the next clip). ``video_playback_rate``
    must reflect that — it never resets on clip change."""
    vp = PhotoViewport()
    try:
        vp.video_set_playback_rate(2.0)
        assert vp.video_playback_rate() == pytest.approx(2.0)
        # Simulate the arm-change by clearing the player + armed-path
        # (the same state ``_disarm_video`` leaves behind) and
        # re-arming with a new clip path. The cached rate MUST persist.
        vp._video_armed = None
        vp._player = None
        # Fresh "arm" — just rebind the path attribute the way
        # ``_ensure_player`` would on the next clip landing.
        vp._video_armed = "v2.mp4"
        assert vp.video_playback_rate() == pytest.approx(2.0), (
            "spec/137: the rate must carry across clips — a clip after "
            "a 2× clip still plays at 2×"
        )
    finally:
        vp.deleteLater()


def test_video_set_playback_rate_applies_live_to_current_player(qapp):
    """When a player object exists (a clip is armed), setting the
    rate MUST call into the player so the change applies to the
    currently-playing clip — not just the next arm."""
    vp = PhotoViewport()
    try:
        applied: list[float] = []
        vp._player = SimpleNamespace(
            setPlaybackRate=lambda r: applied.append(float(r)))
        vp.video_set_playback_rate(1.5)
        assert applied == [pytest.approx(1.5)], (
            "spec/137: ``video_set_playback_rate`` must apply live to "
            "the current clip when a player exists; got "
            f"setPlaybackRate calls {applied}"
        )
        # And the cache reflects it.
        assert vp.video_playback_rate() == pytest.approx(1.5)
    finally:
        vp._player = None
        vp.deleteLater()


def test_video_set_playback_rate_clamps_floor(qapp):
    """Defensive floor (``max(0.05, rate)``) — the rate never lands
    at zero or negative so playback can't stall on a typo."""
    vp = PhotoViewport()
    try:
        vp.video_set_playback_rate(0.0)
        assert vp.video_playback_rate() == pytest.approx(0.05)
        vp.video_set_playback_rate(-1.0)
        assert vp.video_playback_rate() == pytest.approx(0.05)
    finally:
        vp.deleteLater()
