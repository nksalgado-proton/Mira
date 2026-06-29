"""spec/155 v3 — PTE Video overlay on separator + opener slides.

When a PteMember carries ``video_overlay_path`` + ``video_overlay_duration_ms``,
the generator nests a ``:Video`` block alongside the slide's text overlays
so PTE plays the MP4 over the slide's flat background. The slide's
``[Times]`` slot is bumped to at least the video duration so PTE holds
the slide long enough for the whole clip to play.
"""
from __future__ import annotations

import re
from pathlib import Path

from mira.shared.pte_project import (
    PteMember,
    PteText,
    TEXT_SEP_TITLE,
    bundled_skeleton_path,
    generate,
    load_skeleton,
)


def _slide_section(text: str, name: str) -> str:
    pattern = rf"\[{re.escape(name)}\][\r\n]+([\s\S]*?)(?=\[Slide\d+\]|\[Times\]|\Z)"
    m = re.search(pattern, text)
    return m.group(1) if m else ""


def _times_section(text: str) -> str:
    m = re.search(r"\[Times\][\r\n]+([\s\S]*?)\Z", text)
    return m.group(1) if m else ""


def _make_jpeg(path: Path) -> Path:
    # Tiny placeholder JPEG -- the generator doesn't read pixels here.
    path.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00")
    return path


def _make_mp4_placeholder(path: Path) -> Path:
    # The generator stamps the path + duration straight through; it
    # doesn't probe the file.
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    return path


# ── _video_overlay_object ──────────────────────────────────────

def test_video_overlay_block_is_well_formed():
    """The emitter produces a ``:Video`` block at 4-space indent with
    the canonical fields the user's manual PTE example used."""
    from mira.shared.pte_project import _video_overlay_object
    block = _video_overlay_object(
        idx=1, video_path=r"C:\maps\day-02.mp4",
        duration_ms=15000)
    # 4-space indent on the object header (sibling of :Text blocks).
    assert block.startswith("    object MapVideo1:Video\r\n")
    # The locked rule: muted by default.
    assert "      Mute=1\r\n" in block
    # Scale defaults to 65 % per Nelson 2026-06-29.
    assert "        ScaleX=65.0\r\n" in block
    assert "        ScaleY=65.0\r\n" in block
    # Centred (Position=0,0); the user's manual example had an off-
    # centre placement but that was an artefact of hand-editing.
    assert "        Position=0,0\r\n" in block
    # File path + duration ride straight through.
    assert r"      FileName=C:\maps\day-02.mp4" in block
    assert "      Duration=15000\r\n" in block
    # And the block closes cleanly.
    assert block.endswith("    end\r\n")


def test_video_overlay_block_has_a_fresh_guid():
    """Each emitted block carries a fresh PTE-shaped GUID — two calls
    never collide."""
    from mira.shared.pte_project import _video_overlay_object
    a = _video_overlay_object(idx=1, video_path="x.mp4", duration_ms=1000)
    b = _video_overlay_object(idx=2, video_path="y.mp4", duration_ms=1000)
    guid_a = re.search(r"GUID=(\{[^}]+\})", a).group(1)  # type: ignore[union-attr]
    guid_b = re.search(r"GUID=(\{[^}]+\})", b).group(1)  # type: ignore[union-attr]
    assert guid_a != guid_b


# ── end-to-end generator ───────────────────────────────────────

def test_generator_emits_video_overlay_on_separator_slide(tmp_path):
    """spec/155 v3 — a PteMember with ``video_overlay_path`` nests a
    ``:Video`` object next to the text overlays."""
    folder = tmp_path / "show"
    folder.mkdir(parents=True, exist_ok=True)
    sep = _make_jpeg(folder / "001_day1.jpg")
    mp4 = _make_mp4_placeholder(folder / "day-01.mp4")
    members = [
        PteMember(
            kind="photo", path=sep,
            texts=[PteText("Day 1", TEXT_SEP_TITLE)],
            video_overlay_path=mp4,
            video_overlay_duration_ms=4000,
        ),
    ]
    text = generate(
        load_skeleton(bundled_fallback=bundled_skeleton_path()),
        members, [],
        aspect="16:9", photo_seconds=6.0,
        project_path=folder / "slideshow.pte",
        images_folder=folder,
    )
    body = _slide_section(text, "Slide1")
    assert ":Video" in body, "Slide1 has no embedded :Video block"
    # FileName ride-through.
    assert "FileName=" in body
    assert "day-01.mp4" in body
    # Mute is forced.
    assert "Mute=1" in body
    # The :Text block survives next to the video.
    assert 'Text="Day 1"' in body


def test_generator_skips_video_overlay_when_unset(tmp_path):
    """A PteMember without ``video_overlay_path`` produces a clean
    photo slide — no stray ``:Video`` block sneaks into the body."""
    folder = tmp_path / "show"
    folder.mkdir(parents=True, exist_ok=True)
    sep = _make_jpeg(folder / "001_day1.jpg")
    members = [
        PteMember(
            kind="photo", path=sep,
            texts=[PteText("Day 1", TEXT_SEP_TITLE)],
        ),
    ]
    text = generate(
        load_skeleton(bundled_fallback=bundled_skeleton_path()),
        members, [],
        aspect="16:9", photo_seconds=6.0,
        project_path=folder / "slideshow.pte",
        images_folder=folder,
    )
    body = _slide_section(text, "Slide1")
    assert ":Video" not in body


def test_generator_skips_video_overlay_when_duration_zero(tmp_path):
    """A probe that returns 0 ms (corrupt file, unreadable codec) → the
    overlay is suppressed gracefully and the slide falls back to a
    plain photo card."""
    folder = tmp_path / "show"
    folder.mkdir(parents=True, exist_ok=True)
    sep = _make_jpeg(folder / "001_day1.jpg")
    mp4 = _make_mp4_placeholder(folder / "day-01.mp4")
    members = [
        PteMember(
            kind="photo", path=sep,
            texts=[PteText("Day 1", TEXT_SEP_TITLE)],
            video_overlay_path=mp4,
            video_overlay_duration_ms=0,
        ),
    ]
    text = generate(
        load_skeleton(bundled_fallback=bundled_skeleton_path()),
        members, [],
        aspect="16:9", photo_seconds=6.0,
        project_path=folder / "slideshow.pte",
        images_folder=folder,
    )
    body = _slide_section(text, "Slide1")
    assert ":Video" not in body


def test_generator_bumps_slide_time_to_fit_video(tmp_path):
    """The slide's [Times] slot is at least the video's duration so PTE
    holds long enough for the whole clip to play. With a 15s video and
    a 6s photo_s default, the slide contributes 15 000 ms (not 6 000)."""
    folder = tmp_path / "show"
    folder.mkdir(parents=True, exist_ok=True)
    sep = _make_jpeg(folder / "001_day1.jpg")
    mp4 = _make_mp4_placeholder(folder / "day-01.mp4")
    members = [
        PteMember(
            kind="photo", path=sep,
            texts=[PteText("Day 1", TEXT_SEP_TITLE)],
            video_overlay_path=mp4,
            video_overlay_duration_ms=15000,
        ),
    ]
    text = generate(
        load_skeleton(bundled_fallback=bundled_skeleton_path()),
        members, [],
        aspect="16:9", photo_seconds=6.0,
        project_path=folder / "slideshow.pte",
        images_folder=folder,
    )
    times = _times_section(text)
    # opt_synchpos1 = first slide's cumulative end. Must be ≥ 15 000 so
    # the 15s video fits inside the slide's own hold.
    m = re.search(r"opt_synchpos1=(\d+)", times)
    assert m is not None
    assert int(m.group(1)) >= 15000


def test_generator_keeps_slide_time_when_video_is_shorter_than_photo_slot(tmp_path):
    """If the video is shorter than the still slot (photo_s), the slot
    DOESN'T shrink. The slide still rides the longer photo_s + transition
    so the visual rhythm of separators matches surrounding stills."""
    folder = tmp_path / "show"
    folder.mkdir(parents=True, exist_ok=True)
    sep = _make_jpeg(folder / "001_day1.jpg")
    mp4 = _make_mp4_placeholder(folder / "day-01.mp4")
    members = [
        PteMember(
            kind="photo", path=sep,
            texts=[PteText("Day 1", TEXT_SEP_TITLE)],
            video_overlay_path=mp4,
            video_overlay_duration_ms=1000,
        ),
    ]
    text = generate(
        load_skeleton(bundled_fallback=bundled_skeleton_path()),
        members, [],
        aspect="16:9", photo_seconds=6.0,
        project_path=folder / "slideshow.pte",
        images_folder=folder,
    )
    times = _times_section(text)
    m = re.search(r"opt_synchpos1=(\d+)", times)
    assert m is not None
    # Photo slot is 6 000 ms + transition — comfortably more than the
    # 1 000 ms video.
    assert int(m.group(1)) >= 6000
