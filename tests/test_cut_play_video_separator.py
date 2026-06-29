"""spec/155 v2 — video-map separator playback in Cut Play.

When a day's map slot is an MP4 (not a JPEG/PNG), the day-separator
slot plays the clip muted at native duration, EndOfMedia-driven,
instead of holding a still QImage for ``photo_s`` seconds.
"""
from __future__ import annotations

import itertools
import subprocess
from pathlib import Path

import pytest
from PyQt6.QtGui import QImage

from mira.gateway.event_gateway import EventGateway
from mira.shared.cut_session import show_entries
from mira.store.repo import EventStore
from mira.ui.shared.cut_play import CutPlayerDialog

from tests.test_gateway_cuts import _doc, _now


def _write_tiny_mp4(path: Path) -> Path:
    from core.video_extract import _FFMPEG_EXE
    cmd = [
        _FFMPEG_EXE, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=gray:s=64x36:d=1:r=24",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "1",
        str(path),
    ]
    subprocess.run(cmd, check=True, timeout=30)
    return path


def _write_tiny_jpeg(path: Path) -> Path:
    img = QImage(16, 16, QImage.Format.Format_RGB32)
    img.fill(0x707070)
    img.save(str(path), "JPEG")
    return path


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
    g.set_cut_members(
        "cut-s", ["Exported Media/e1.jpg", "Exported Media/e3a.jpg"])
    yield g
    g.close()


def _player(gw, tmp_path) -> CutPlayerDialog:
    entries = show_entries(gw, gw.cut("cut-s"), separators_on=True)
    day_meta = {d.day_number: d for d in gw.trip_days()}
    return CutPlayerDialog(
        entries, event_root=tmp_path, photo_s=6.0,
        day_meta=day_meta, aspect="16:9",
        opener_image=QImage(16, 9, QImage.Format.Format_RGB32))


# ── _sep_video_path ────────────────────────────────────────────

def test_sep_video_path_returns_none_when_no_map(qapp, gw, tmp_path):
    """Day with no map slot reports no sep video."""
    p = _player(gw, tmp_path)
    try:
        assert p._sep_video_path(1) is None
        assert p._sep_video_path(2) is None
    finally:
        p.close()


def test_sep_video_path_returns_none_when_image_map(
        qapp, gw, tmp_path):
    """An image map slot stays in the still-render path."""
    src = _write_tiny_jpeg(tmp_path / "map.jpg")
    gw.attach_day_map(1, src)
    p = _player(gw, tmp_path)
    try:
        assert p._sep_video_path(1) is None
    finally:
        p.close()


def test_sep_video_path_returns_abs_path_for_mp4_map(
        qapp, gw, tmp_path):
    """An MP4 map returns the absolute slot file path so the player
    can hand it to QMediaPlayer."""
    src = _write_tiny_mp4(tmp_path / "map.mp4")
    gw.attach_day_map(1, src)
    p = _player(gw, tmp_path)
    try:
        result = p._sep_video_path(1)
        assert result is not None
        assert result.is_file()
        assert result.name == "day-01.mp4"
    finally:
        p.close()


# ── duration ───────────────────────────────────────────────────

def test_sep_video_duration_ms_probes_mp4(qapp, gw, tmp_path):
    """The cached probe returns the MP4's real duration (≈1 s)."""
    src = _write_tiny_mp4(tmp_path / "map.mp4")
    gw.attach_day_map(1, src)
    p = _player(gw, tmp_path)
    try:
        ms = p._sep_video_duration_ms(1)
        # Allow a small ±200 ms tolerance for encoder rounding.
        assert 800 <= ms <= 1200, f"expected ~1000ms, got {ms}"
    finally:
        p.close()


def test_sep_video_duration_ms_returns_zero_when_no_video(
        qapp, gw, tmp_path):
    """Days without an MP4 map probe-cache to 0 (still-render path)."""
    p = _player(gw, tmp_path)
    try:
        assert p._sep_video_duration_ms(1) == 0
        assert p._sep_video_duration_ms(2) == 0
    finally:
        p.close()


# ── _entry_class ───────────────────────────────────────────────

def test_entry_class_reads_sep_video_as_video(qapp, gw, tmp_path):
    """The crossfade math treats a sep MP4 the same as a file video —
    half boundary on photo↔video, zero on video↔video."""
    src = _write_tiny_mp4(tmp_path / "map.mp4")
    gw.attach_day_map(1, src)
    p = _player(gw, tmp_path)
    try:
        # show_entries fixture above gives [opener, sep(day=1), file, sep(day=2), file].
        # Index 1 is the sep for day 1 (the one with the MP4).
        assert p._entry_class(1) == "video"
        # Sep for day 2 has no map → still classed as photo.
        assert p._entry_class(3) == "photo"
    finally:
        p.close()


# ── _entry_total_ms ────────────────────────────────────────────

def test_entry_total_ms_uses_probed_duration_for_sep_video(
        qapp, gw, tmp_path):
    """The sep video slot's wall-clock is the MP4's native duration,
    not ``photo_s + transition``."""
    src = _write_tiny_mp4(tmp_path / "map.mp4")
    gw.attach_day_map(1, src)
    p = _player(gw, tmp_path)
    try:
        sep_idx = 1  # sep for day 1
        ms = p._entry_total_ms(sep_idx)
        # Probed (~1000ms) is far below the still slot (6 s photo + transition).
        assert 800 <= ms <= 1200, f"sep video slot read as {ms}ms, expected ~1000"
        # Sanity — sep for day 2 (no MP4) still reads as the photo slot.
        assert p._entry_total_ms(3) >= 6000
    finally:
        p.close()


# ── opener video (event map) ──────────────────────────────────

def _player_with_opener_video(gw, tmp_path, opener_video_path) -> CutPlayerDialog:
    """Player fixture whose opener slot is wired to play ``opener_video_path``
    (the event-map MP4)."""
    entries = show_entries(gw, gw.cut("cut-s"), separators_on=True)
    day_meta = {d.day_number: d for d in gw.trip_days()}
    return CutPlayerDialog(
        entries, event_root=tmp_path, photo_s=6.0,
        day_meta=day_meta, aspect="16:9",
        opener_image=QImage(16, 9, QImage.Format.Format_RGB32),
        opener_video_path=opener_video_path)


def test_opener_video_path_stored_when_passed(qapp, gw, tmp_path):
    """The constructor stashes the opener video path as an absolute
    Path so the show-time branch can decide image vs. video."""
    src = _write_tiny_mp4(tmp_path / "event.mp4")
    p = _player_with_opener_video(gw, tmp_path, src)
    try:
        assert p._opener_video_path == src
    finally:
        p.close()


def test_opener_video_path_default_is_none(qapp, gw, tmp_path):
    """When no opener_video_path is passed (the still-image case), the
    field stays None and the existing render path stands."""
    p = _player(gw, tmp_path)
    try:
        assert p._opener_video_path is None
    finally:
        p.close()


def test_opener_video_duration_probes_mp4(qapp, gw, tmp_path):
    """The cached probe returns the MP4's real duration (≈1 s)."""
    src = _write_tiny_mp4(tmp_path / "event.mp4")
    p = _player_with_opener_video(gw, tmp_path, src)
    try:
        ms = p._opener_video_duration_ms()
        assert 800 <= ms <= 1200, f"expected ~1000ms, got {ms}"
        # Cache works — second call returns same value without re-probing.
        assert p._opener_video_duration_ms() == ms
    finally:
        p.close()


def test_opener_video_duration_zero_when_no_path(qapp, gw, tmp_path):
    p = _player(gw, tmp_path)
    try:
        assert p._opener_video_duration_ms() == 0
    finally:
        p.close()


def test_entry_class_reads_opener_video_as_video(qapp, gw, tmp_path):
    """spec/152 crossfade math reads an MP4 opener as 'video' so the
    boundary into the next entry uses the half/zero transition shape."""
    src = _write_tiny_mp4(tmp_path / "event.mp4")
    p = _player_with_opener_video(gw, tmp_path, src)
    try:
        # show_entries fixture: index 0 is the opener.
        assert p._entry_class(0) == "video"
    finally:
        p.close()


def test_entry_class_reads_still_opener_as_photo(qapp, gw, tmp_path):
    """No opener_video_path → opener is the still card and reads as
    'photo' for the crossfade boundary math."""
    p = _player(gw, tmp_path)
    try:
        assert p._entry_class(0) == "photo"
    finally:
        p.close()


def test_entry_total_ms_uses_probed_duration_for_opener_video(
        qapp, gw, tmp_path):
    """Opener video slot's wall-clock is the MP4's native duration,
    not ``photo_s + transition``."""
    src = _write_tiny_mp4(tmp_path / "event.mp4")
    p = _player_with_opener_video(gw, tmp_path, src)
    try:
        ms = p._entry_total_ms(0)
        assert 800 <= ms <= 1200, f"opener video slot read as {ms}ms, expected ~1000"
    finally:
        p.close()


def test_entry_total_ms_falls_back_to_still_when_no_opener_video(
        qapp, gw, tmp_path):
    """Without an MP4 opener path, slot 0 holds for ``photo_ms +
    transition_ms`` exactly like before spec/155 v2 §opener landed."""
    p = _player(gw, tmp_path)
    try:
        ms = p._entry_total_ms(0)
        # photo_s=6.0 → photo_ms=6000; transition_ms default is small.
        assert ms >= 6000
    finally:
        p.close()
