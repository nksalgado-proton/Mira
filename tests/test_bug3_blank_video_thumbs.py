"""Bug 3 — Day Grid video cells render forever-blank when the thumb
extractor fails (corrupt cached JPEG, unsupported codec, partial write
from an interrupted prior run, …). Three fixes pinned here:

1. ``core.thumb_cache.ensure_thumb`` self-heals an unreadable cached
   JPEG: drop the file (and the ``.vetted`` sidecar) and fall through
   to the ladder.
2. Thumb decoders log failures at WARNING (was DEBUG) so the app log
   carries enough breadcrumbs to triage a repro.
3. ``mira.ui.picked.placeholder.placeholder_pixmap`` builds a kind-aware
   fallback so a cell never stays visually blank (extracted from the
   retired ``picked/pick_page.py`` shell during the Surface 11 wiring).

Module name dodges the conftest ``_SLICE_B_FILES`` bulk-skip.
"""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from core import thumb_cache


# ── Fixture: a real readable JPEG so the cached-hit path has something to load.


def _write_pure_white_jpeg(dest: Path, w: int = 64, h: int = 36) -> None:
    """A bright JPEG (luma > 250) — bypasses the ladder re-run path."""
    from PIL import Image

    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (w, h), color=(255, 255, 255)).save(dest, "JPEG")


def _write_corrupt_jpeg(dest: Path) -> None:
    """A file with a JPEG extension but garbage bytes. PIL raises on read."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"NOT A REAL JPEG \x00\x01\x02\x03\x04")


# ── 1. ensure_thumb self-heals a corrupt cached file ──────────────────────


def test_ensure_thumb_self_heals_corrupt_cached_file(tmp_path, caplog):
    """When the cached JPEG is unreadable (corrupt / partial write),
    ensure_thumb drops it + the .vetted sidecar and re-runs the
    ladder rather than raising up to the caller (the legacy behaviour
    that left the Day Grid cell forever-blank)."""
    event_root = tmp_path / "event"
    src_rel = Path("00 - Captured/Day 1/G9/X.MP4")
    fake_video = event_root / src_rel
    fake_video.parent.mkdir(parents=True, exist_ok=True)
    fake_video.write_bytes(b"")  # never actually decoded — extract_frame mocked

    dest = thumb_cache.thumb_path(event_root, src_rel, "daygrid")
    vetted = dest.with_suffix(".vetted")
    _write_corrupt_jpeg(dest)
    vetted.write_bytes(b"")

    extracted: list[Path] = []

    def fake_extract(video_path, position_ms, output_path):
        # The ladder calls us — emit a healthy JPEG at the dest path.
        extracted.append(Path(output_path))
        _write_pure_white_jpeg(Path(output_path))

    with patch.object(thumb_cache, "extract_frame", side_effect=fake_extract):
        with caplog.at_level(logging.WARNING, logger="core.thumb_cache"):
            out = thumb_cache.ensure_thumb(
                event_root=event_root,
                source_video=fake_video,
                source_rel_path=src_rel,
                item_id="daygrid",
                position_ms=1000,
                fallback_position_ms=0,
            )

    # The ladder ran (corrupt file dropped + re-extracted).
    assert len(extracted) >= 1
    # Output exists and is now a real readable JPEG (mean luma 255 ≫ threshold).
    assert out.exists()
    assert thumb_cache._mean_luma(out) > 200
    # The self-heal logged at WARNING so triage has a trail.
    assert any("unreadable" in rec.getMessage().lower()
               for rec in caplog.records)


def test_ensure_thumb_treats_corrupt_vetted_thumb_as_unreadable(tmp_path):
    """A vetted-but-corrupt cached thumb used to short-circuit on the
    .vetted existence check (returning the broken path); load_pixmap
    later returned null → Day Grid cell stayed blank. Now the read is
    validated even on the vetted fast path."""
    event_root = tmp_path / "event"
    src_rel = Path("00 - Captured/Day 1/G9/Y.MP4")
    fake_video = event_root / src_rel
    fake_video.parent.mkdir(parents=True, exist_ok=True)
    fake_video.write_bytes(b"")

    dest = thumb_cache.thumb_path(event_root, src_rel, "daygrid")
    vetted = dest.with_suffix(".vetted")
    _write_corrupt_jpeg(dest)
    vetted.write_bytes(b"")

    def fake_extract(video_path, position_ms, output_path):
        _write_pure_white_jpeg(Path(output_path))

    with patch.object(thumb_cache, "extract_frame", side_effect=fake_extract):
        out = thumb_cache.ensure_thumb(
            event_root=event_root,
            source_video=fake_video,
            source_rel_path=src_rel,
            item_id="daygrid",
            position_ms=1000,
            fallback_position_ms=0,
        )

    # File is healthy + re-vetted.
    assert thumb_cache._mean_luma(out) > 200
    assert vetted.exists()


def test_ensure_thumb_keeps_a_healthy_cached_file_untouched(tmp_path):
    """The self-heal must NOT regenerate a perfectly good cached thumb
    (regression guard for the cache-hit fast path)."""
    event_root = tmp_path / "event"
    src_rel = Path("00 - Captured/Day 1/G9/Z.MP4")
    fake_video = event_root / src_rel
    fake_video.parent.mkdir(parents=True, exist_ok=True)
    fake_video.write_bytes(b"")

    dest = thumb_cache.thumb_path(event_root, src_rel, "daygrid")
    _write_pure_white_jpeg(dest)
    original_bytes = dest.read_bytes()

    def fake_extract(video_path, position_ms, output_path):
        pytest.fail("ladder ran on a healthy cached file — perf regression")

    with patch.object(thumb_cache, "extract_frame", side_effect=fake_extract):
        out = thumb_cache.ensure_thumb(
            event_root=event_root,
            source_video=fake_video,
            source_rel_path=src_rel,
            item_id="daygrid",
            position_ms=1000,
            fallback_position_ms=0,
        )

    assert out == dest
    assert dest.read_bytes() == original_bytes


# ── 2. Placeholder pixmap + loader log level ──────────────────────────────


def test_placeholder_pixmap_built_for_video(qapp):
    from mira.ui.picked.placeholder import placeholder_pixmap as _placeholder_pixmap

    pm = _placeholder_pixmap("video")
    assert pm is not None
    assert not pm.isNull()
    assert pm.width() > 0 and pm.height() > 0


def test_placeholder_pixmap_built_for_photo(qapp):
    from mira.ui.picked.placeholder import placeholder_pixmap as _placeholder_pixmap

    pm = _placeholder_pixmap("photo")
    assert pm is not None
    assert not pm.isNull()


def test_placeholder_pixmap_is_cached(qapp):
    """A placeholder is built once + reused — same QPixmap returned on
    repeat calls (saves rebuilding the same QPainter dance per blank
    cell on a big day)."""
    from mira.ui.picked.placeholder import placeholder_pixmap as _placeholder_pixmap

    a = _placeholder_pixmap("video")
    b = _placeholder_pixmap("video")
    assert a is b
