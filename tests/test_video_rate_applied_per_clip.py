"""spec/138 §2A — the engine re-applies the sticky rate on every clip.

``QMediaPlayer.setSource()`` resets ``playbackRate`` back to 1.0
(backend/timing-dependent), which is why a clip after a 2× clip
sometimes drops to 1×. ``_arm_video`` MUST re-apply
``self._video_rate`` after every ``setSource(...)`` so the engine
actually plays at the intended speed.

The tests stub the player + audio so we never instantiate a real
``QMediaPlayer`` (offscreen-safe) and assert on the recorded
``setPlaybackRate`` / ``setSource`` call sequence.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mira.ui.media.photo_viewport import PhotoViewport


class _StubPlayer:
    """Captures the call sequence the viewport runs against the
    player so the test can assert on order + values without spinning
    up Qt's multimedia backend."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.rate: float = 1.0
        self.source = None

    def setSource(self, url):
        # spec/138 §1.1 — emulate Qt's setSource resetting the rate
        # to 1.0 (this is the silent reset the deterministic apply
        # in ``_arm_video`` exists to counteract).
        self.source = url
        self.rate = 1.0
        self.calls.append(("setSource", url))

    def setPlaybackRate(self, rate):
        self.rate = float(rate)
        self.calls.append(("setPlaybackRate", float(rate)))

    def play(self):
        self.calls.append(("play",))

    def stop(self):
        self.calls.append(("stop",))


def _arm_with_stub_player(vp: PhotoViewport, path: Path) -> _StubPlayer:
    """Drive ``_arm_video`` against a stub player so we can read the
    recorded call sequence. Skips ``_ensure_player`` (which would
    construct a real QMediaPlayer) by pre-installing the stub."""
    stub = _StubPlayer()
    vp._player = stub
    vp._video_armed = None         # force the arm path
    vp._video_native_size = None
    vp._arm_video(path)
    return stub


def test_set_playback_rate_called_after_set_source(qapp, tmp_path):
    """The deterministic-apply contract — the first call after
    ``setSource`` MUST be ``setPlaybackRate(self._video_rate)`` so
    Qt's silent rate-reset can't survive into the play() call."""
    vp = PhotoViewport()
    try:
        vp._video_rate = 2.0
        path = tmp_path / "v.mp4"
        path.write_bytes(b"\x00" * 16)
        stub = _arm_with_stub_player(vp, path)

        names = [c[0] for c in stub.calls]
        assert "setSource" in names, "setSource was never called"
        assert "setPlaybackRate" in names, (
            "spec/138 §2A: ``_arm_video`` MUST call setPlaybackRate "
            "after setSource; got call sequence " + repr(names)
        )
        i_src = names.index("setSource")
        i_rate = names.index("setPlaybackRate")
        assert i_rate > i_src, (
            "spec/138 §2A: setPlaybackRate must come AFTER setSource "
            "(setSource resets the rate to 1.0 first); got order "
            + repr(names)
        )
    finally:
        vp._player = None
        vp.deleteLater()


def test_set_playback_rate_uses_cached_video_rate(qapp, tmp_path):
    """The re-applied rate is the viewport's CURRENT cached
    ``_video_rate`` (the session truth), not a hardcoded 1.0."""
    vp = PhotoViewport()
    try:
        vp._video_rate = 1.5
        path = tmp_path / "v.mp4"
        path.write_bytes(b"\x00" * 16)
        stub = _arm_with_stub_player(vp, path)

        rate_calls = [c[1] for c in stub.calls if c[0] == "setPlaybackRate"]
        assert rate_calls == [pytest.approx(1.5)], (
            f"spec/138 §2A: the re-applied rate must equal the "
            f"viewport's _video_rate (1.5); got {rate_calls}"
        )
        assert stub.rate == pytest.approx(1.5)
    finally:
        vp._player = None
        vp.deleteLater()


def test_2x_survives_a_new_clip_arm(qapp, tmp_path):
    """The headline regression — a clip set to 2× must STILL drive
    the engine at 2× on the next arm. Before the spec/138 fix the
    silent setSource reset left the player at 1.0 while
    ``_video_rate`` stayed at 2.0."""
    vp = PhotoViewport()
    try:
        # User picked 2× on the previous clip (sticky state).
        vp._video_rate = 2.0
        # Arm a new clip.
        path = tmp_path / "next.mp4"
        path.write_bytes(b"\x00" * 16)
        stub = _arm_with_stub_player(vp, path)

        # Final rate the player carries MUST be the sticky 2.0 —
        # NOT the 1.0 setSource silently reset to.
        assert stub.rate == pytest.approx(2.0), (
            "spec/138 §2A: a 2× clip followed by a new arm must "
            "still drive the engine at 2× — the cached rate is "
            "re-applied after Qt's setSource reset"
        )
    finally:
        vp._player = None
        vp.deleteLater()


def test_rearm_with_same_path_short_circuits(qapp, tmp_path):
    """Sanity: the early-out path (``_video_armed == path``) MUST
    skip the player calls entirely so a redundant arm is free."""
    vp = PhotoViewport()
    try:
        vp._video_rate = 1.5
        path = tmp_path / "v.mp4"
        path.write_bytes(b"\x00" * 16)
        stub = _arm_with_stub_player(vp, path)
        first_call_count = len(stub.calls)
        # Second arm with the SAME path → no further calls (the
        # short-circuit fires before any player mutation).
        vp._arm_video(path)
        assert len(stub.calls) == first_call_count, (
            "redundant arm should short-circuit; got new calls "
            + repr(stub.calls[first_call_count:])
        )
    finally:
        vp._player = None
        vp.deleteLater()


def test_video_set_playback_rate_applies_live_and_updates_cache(qapp):
    """The user-driven rate change still applies live AND updates the
    cache so the NEXT arm carries the new rate too."""
    vp = PhotoViewport()
    try:
        stub = _StubPlayer()
        vp._player = stub
        vp.video_set_playback_rate(0.5)
        assert vp.video_playback_rate() == pytest.approx(0.5)
        # Live application landed on the player.
        assert ("setPlaybackRate", pytest.approx(0.5)) in stub.calls
    finally:
        vp._player = None
        vp.deleteLater()
