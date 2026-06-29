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


def test_caption_label_stays_child_of_dialog_on_ensure_video(
        qapp, gw, tmp_path):
    """Nelson 2026-06-29 round 5 — the video occupies bottom 70 % and
    the caption rides the top 30 % as a SIBLING (not child) of the
    video, so the two never overlap and there is no native-surface
    compositing fight."""
    p = _player(gw, tmp_path)
    try:
        assert p._caption_label.parentWidget() is p
        p._ensure_video()
        # The caption stays a child of the dialog — the 70/30 split
        # keeps it OUT of the video's bounds so no reparent is needed.
        assert p._caption_label.parentWidget() is p
    finally:
        p.close()


def test_fit_sep_video_geometry_insets_video_from_slide_borders(
        qapp, gw, tmp_path):
    """spec/155 v7 — the video sits inside the slide's inner rect with
    horizontal + bottom margins so the slide border stays visible
    around it. Pinned via the photo canvas's fallback rect (no pixmap
    needed) so the math is deterministic."""
    p = _player(gw, tmp_path)
    try:
        p._ensure_video()
        p.show()
        p._stack_widget.resize(1000, 1000)
        p._photo.resize(1000, 1000)
        pad = 28  # BlurredPhotoCanvas DEFAULT_INNER_PAD
        inner_w = 1000 - 2 * pad
        inner_h = 1000 - 2 * pad
        p._fit_sep_video_geometry()
        g = p._video_widget.geometry()
        margin_x = int(inner_w * 0.05)
        margin_b = int(inner_h * 0.05)
        expected_w = inner_w - 2 * margin_x
        expected_h = int(inner_h * 0.60)
        # Centred horizontally inside the slide border.
        assert g.x() == pad + margin_x
        assert g.width() == expected_w
        # Bottom edge sits ``margin_b`` above the inner rect's bottom
        # so the slide border shows below the video too.
        assert g.height() == expected_h
        inner_bottom = pad + inner_h - 1
        assert g.y() == inner_bottom + 1 - margin_b - expected_h
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


# ── top-centre caption overlay (spec/155 v2) ───────────────────

def test_sep_caption_html_carries_day_title_and_sub(
        qapp, gw, tmp_path):
    """The sep-video overlay text composes Day N + date/location/desc,
    matching the still QImage's baked text."""
    src = _write_tiny_mp4(tmp_path / "map.mp4")
    gw.attach_day_map(1, src)
    p = _player(gw, tmp_path)
    try:
        html = p._compose_sep_caption_html(1)
        assert "Day 1" in html
        # The bundled fixture's day 1 has a date + a location; both
        # should land in the sub line, joined by " · ".
        meta = p._day_meta.get(1)
        if getattr(meta, "date", None):
            assert str(meta.date) in html
        if getattr(meta, "location", None):
            assert meta.location in html
    finally:
        p.close()


def test_opener_caption_html_carries_tag_and_lines(qapp, gw, tmp_path):
    """The opener overlay text composes the show title + facts row
    (same shape the still render bakes in)."""
    src = _write_tiny_mp4(tmp_path / "event.mp4")
    entries = show_entries(gw, gw.cut("cut-s"), separators_on=True)
    day_meta = {d.day_number: d for d in gw.trip_days()}
    p = CutPlayerDialog(
        entries, event_root=tmp_path, photo_s=6.0,
        day_meta=day_meta, aspect="16:9",
        opener_image=QImage(16, 9, QImage.Format.Format_RGB32),
        opener_video_path=src,
        opener_caption_tag="My Show",
        opener_caption_lines=("12 photos", "3 min", "♪ travel"))
    try:
        html = p._compose_opener_caption_html()
        assert "My Show" in html
        assert "12 photos" in html
        assert "3 min" in html
        # The lines join on " · ".
        assert " · " in html
    finally:
        p.close()


def test_caption_label_built_and_starts_hidden(qapp, gw, tmp_path):
    """The label is built unconditionally (cheap, lazy QSS apply) but
    starts hidden — the still-frame path bakes its own text, so showing
    the label on a still would double-paint."""
    p = _player(gw, tmp_path)
    try:
        assert p._caption_label is not None
        assert p._caption_label.isHidden() is True
    finally:
        p.close()


def test_update_caption_shows_for_sep_video(qapp, gw, tmp_path):
    """spec/155 v2 — entering a sep-video slot shows the caption with
    the right text. We drive _update_caption directly to avoid the
    QMediaPlayer dance."""
    src = _write_tiny_mp4(tmp_path / "map.mp4")
    gw.attach_day_map(1, src)
    p = _player(gw, tmp_path)
    try:
        p.show()
        p._update_caption("sep", 1)
        assert p._caption_label.isHidden() is False
        assert "Day 1" in p._caption_label.text()
    finally:
        p.close()


def test_update_caption_shows_for_opener_video(qapp, gw, tmp_path):
    src = _write_tiny_mp4(tmp_path / "event.mp4")
    entries = show_entries(gw, gw.cut("cut-s"), separators_on=True)
    day_meta = {d.day_number: d for d in gw.trip_days()}
    p = CutPlayerDialog(
        entries, event_root=tmp_path, photo_s=6.0,
        day_meta=day_meta, aspect="16:9",
        opener_image=QImage(16, 9, QImage.Format.Format_RGB32),
        opener_video_path=src,
        opener_caption_tag="My Show",
        opener_caption_lines=("12 photos · 3 min",))
    try:
        p.show()
        p._update_caption("opener", None)
        assert p._caption_label.isHidden() is False
        assert "My Show" in p._caption_label.text()
    finally:
        p.close()


def test_update_caption_hides_for_still_sep(qapp, gw, tmp_path):
    """Sep WITHOUT an MP4 map → still-render path; caption stays hidden
    so the QImage's baked text isn't double-painted."""
    p = _player(gw, tmp_path)
    try:
        p.show()
        p._update_caption("sep", 1)
        assert p._caption_label.isHidden() is True
    finally:
        p.close()


def test_show_index_on_opener_video_shows_caption_with_text(
        qapp, gw, tmp_path, monkeypatch):
    """Integration: _show_index for an opener with a video AND tag/lines
    populated must end with the caption label visible AND carrying the
    title text. Pins the exact path the user reports failing."""
    src = _write_tiny_mp4(tmp_path / "event.mp4")
    entries = show_entries(gw, gw.cut("cut-s"), separators_on=True)
    day_meta = {d.day_number: d for d in gw.trip_days()}
    p = CutPlayerDialog(
        entries, event_root=tmp_path, photo_s=6.0,
        day_meta=day_meta, aspect="16:9",
        opener_image=QImage(16, 9, QImage.Format.Format_RGB32),
        opener_video_path=src,
        opener_caption_tag="My Show",
        opener_caption_lines=("12 items · 1:30", "music: travel"))
    try:
        # Prevent _show_video from blocking on the QMediaPlayer dance.
        monkeypatch.setattr(p, "_show_video", lambda _path: None)
        p.show()
        p._show_index(0)  # opener slot
        # The caption label should be visible AND carry text.
        assert p._caption_label is not None
        assert p._caption_label.isHidden() is False
        text = p._caption_label.text()
        assert "My Show" in text, (
            f"expected the opener caption to carry 'My Show', got: {text!r}")
        assert "12 items" in text
    finally:
        p.close()


def test_update_caption_hides_for_file_frames(qapp, gw, tmp_path):
    """File-kind frames (photos / cut videos) get the per-frame
    overlay/origin labels; the sep/opener caption stays hidden."""
    src = _write_tiny_mp4(tmp_path / "map.mp4")
    gw.attach_day_map(1, src)
    p = _player(gw, tmp_path)
    try:
        p.show()
        # Show it once so it's not hidden, then assert it hides on a
        # 'file' kind.
        p._update_caption("sep", 1)
        assert p._caption_label.isHidden() is False
        p._update_caption("file", object())
        assert p._caption_label.isHidden() is True
    finally:
        p.close()
