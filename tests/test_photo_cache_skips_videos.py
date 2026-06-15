"""The photo thumb + proxy caches short-circuit on non-image
extensions (Nelson 2026-06-15 log-spam report).

A video accidentally fed to the photo cache (e.g. via a stale
``set_event_context`` batch, or a synthetic video-cluster GridItem
whose ``_path`` is the source MP4) used to log "cannot identify image
file" on every request. Pillow's open() doesn't know the format and
the warning storm hides real failures.

The fix is a single ``is_supported(path)`` gate at the top of
``ensure_photo_thumb`` / ``ensure_photo_proxy``: return the no-op
fallback when the extension isn't a known still-image format. The
upstream callers should already filter videos (Picker + days-grid +
editor all guard with ``kind == "photo"``); this is defence-in-depth so
a future regression can't bring the warning storm back.
"""
from __future__ import annotations

import logging
from pathlib import Path


def test_ensure_photo_thumb_short_circuits_on_video_extension(
        tmp_path, caplog):
    """A .mp4 source skips the Pillow open() that would log
    'cannot identify image file' and just returns the source path
    (the no-op fallback the loader already handles)."""
    from core.photo_thumb_cache import ensure_photo_thumb

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00" * 32)            # plausible junk
    with caplog.at_level(logging.WARNING, logger="core.photo_thumb_cache"):
        out = ensure_photo_thumb(
            event_root=tmp_path, source_path=video, sha256="v" * 64)
    assert out == video                        # no-op fallback
    # The "cannot identify image file" warning never fires.
    assert not any(
        "photo thumb render failed" in r.message for r in caplog.records), \
        caplog.text


def test_ensure_photo_proxy_short_circuits_on_video_extension(
        tmp_path, caplog):
    """Mirror for ``ensure_photo_proxy`` — returns False without ever
    logging the "proxy render failed" warning for a video source."""
    from core.photo_proxy_cache import ensure_photo_proxy

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00" * 32)
    with caplog.at_level(logging.WARNING, logger="core.photo_proxy_cache"):
        out = ensure_photo_proxy(
            event_root=tmp_path, source_path=video, sha256="v" * 64)
    assert out is False
    assert not any(
        "proxy render failed" in r.message for r in caplog.records), \
        caplog.text


def test_ensure_photo_thumb_still_renders_a_real_jpeg(tmp_path):
    """Sanity: the early-return guard doesn't break the happy path."""
    from PIL import Image
    from core.photo_thumb_cache import ensure_photo_thumb

    src = tmp_path / "p.jpg"
    Image.new("RGB", (48, 32), (90, 120, 200)).save(str(src), "JPEG")
    out = ensure_photo_thumb(
        event_root=tmp_path, source_path=src, sha256="a" * 64)
    assert out != src                          # cache path, not the source
    assert out.is_file()
