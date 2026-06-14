"""Tests for core.photo_decoder — file → numpy array
(Nelson 2026-05-21 Phase 3a).

Most tests use synthetic JPEG / PNG / TIFF files; RAW + HEIC are
covered when the LRC ground-truth pairs become available (Phase 3b
calibration harness). Decoder smoke-test ensures the format triage
+ basic Pillow path works headless."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from core.photo_decoder import (
    HEIC_EXTENSIONS,
    PILLOW_EXTENSIONS,
    RAW_EXTENSIONS,
    decode_image,
    is_supported,
)


# ── is_supported ───────────────────────────────────────────────


def test_is_supported_recognises_common_formats():
    assert is_supported(Path("photo.jpg"))
    assert is_supported(Path("photo.jpeg"))
    assert is_supported(Path("photo.JPG"))            # case-insensitive
    assert is_supported(Path("photo.tif"))
    assert is_supported(Path("photo.tiff"))
    assert is_supported(Path("photo.png"))
    assert is_supported(Path("photo.heic"))
    assert is_supported(Path("photo.RW2"))            # Panasonic RAW
    assert is_supported(Path("photo.nef"))            # Nikon
    assert is_supported(Path("photo.cr3"))            # Canon


def test_is_supported_rejects_unrelated_extensions():
    assert not is_supported(Path("doc.pdf"))
    assert not is_supported(Path("video.mp4"))
    assert not is_supported(Path("readme.txt"))


def test_extension_sets_disjoint():
    """A given extension should not be in multiple format buckets —
    the triage in ``decode_image`` would otherwise be ambiguous."""
    assert RAW_EXTENSIONS.isdisjoint(HEIC_EXTENSIONS)
    assert RAW_EXTENSIONS.isdisjoint(PILLOW_EXTENSIONS)
    assert HEIC_EXTENSIONS.isdisjoint(PILLOW_EXTENSIONS)


# ── decode_image: error paths ──────────────────────────────────


def test_decode_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        decode_image(tmp_path / "nope.jpg")


def test_decode_unsupported_extension_raises(tmp_path):
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    with pytest.raises(ValueError, match="unsupported"):
        decode_image(p)


# ── decode_image: JPEG path (the test workhorse) ───────────────


def test_decode_jpeg_returns_uint8_rgb(tmp_path):
    """A JPEG saved + decoded round-trips through uint8 RGB."""
    src_path = tmp_path / "test.jpg"
    src = np.tile(
        np.linspace(0, 255, 64, dtype=np.uint8), (48, 1)
    )
    rgb = np.stack([src, src, src], axis=-1)
    Image.fromarray(rgb).save(src_path, "JPEG", quality=95)

    out = decode_image(src_path)
    assert out.dtype == np.uint8
    assert out.shape == (48, 64, 3)
    # Mean preserved within JPEG compression tolerance.
    assert abs(int(out.mean()) - int(rgb.mean())) < 4


def test_decode_png_lossless_roundtrip(tmp_path):
    """PNG is lossless — round-trip should match exactly."""
    src_path = tmp_path / "test.png"
    rgb = np.zeros((32, 32, 3), dtype=np.uint8)
    rgb[..., 0] = 200          # red channel
    Image.fromarray(rgb).save(src_path, "PNG")

    out = decode_image(src_path)
    np.testing.assert_array_equal(out, rgb)


def test_decode_grayscale_jpeg_returns_3_channel(tmp_path):
    """A grayscale JPEG decodes to (H, W, 3) RGB — the pipeline
    assumes 3 channels."""
    src_path = tmp_path / "gray.jpg"
    gray = np.full((32, 32), 100, dtype=np.uint8)
    Image.fromarray(gray, mode="L").save(src_path, "JPEG", quality=95)
    out = decode_image(src_path)
    assert out.shape == (32, 32, 3)
    # All channels should match (it was monochrome).
    diff = out.astype(np.int16)
    assert abs(diff[..., 0] - diff[..., 1]).max() <= 2
    assert abs(diff[..., 1] - diff[..., 2]).max() <= 2


def test_decode_applies_exif_orientation(tmp_path):
    """Portrait photos with EXIF orientation should arrive
    upright. We synthesise a 4×8 landscape image, tag it with
    orientation=8 (rotated 90° CCW), and verify the decode comes
    back rotated."""
    src_path = tmp_path / "rotated.jpg"
    # Source: 4 high × 8 wide (landscape). With orientation=8 the
    # display should be 8 high × 4 wide (portrait).
    landscape = np.tile(
        np.arange(8, dtype=np.uint8) * 32, (4, 1)
    )
    rgb = np.stack([landscape, landscape, landscape], axis=-1)
    img = Image.fromarray(rgb)
    # Pillow's exif writer.
    exif = img.getexif()
    exif[274] = 8                     # 274 = Orientation, 8 = rotated 90° CCW
    img.save(src_path, "JPEG", quality=95, exif=exif)

    out = decode_image(src_path)
    # After orientation, the shape should be (8, 4, 3).
    assert out.shape == (8, 4, 3)
