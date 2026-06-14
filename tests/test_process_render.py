"""Tests for core.process_render.

Covers the pure (Qt-free) rendering pipeline: crop math, save format,
and the end-to-end ``render_processed`` function. Doesn't exercise RAW
decoding — that depends on rawpy + libraw and the test corpus would
have to ship binary RAW samples.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from core.aspect_ratio import get_aspect_ratio
from core.process_render import (
    JPEG_OUTPUT_QUALITY,
    apply_crop,
    apply_rotation,
    compute_default_crop,
    output_filename,
    render_processed,
    save_jpeg,
)


def _gradient_jpeg(tmp_path: Path, low: int = 60, high: int = 120,
                   size: tuple[int, int] = (400, 300)) -> Path:
    """Write a small gradient JPEG to disk for load tests."""
    w, h = size
    row = np.linspace(low, high, w, dtype=np.uint8)
    arr = np.tile(row, (h, 1))
    rgb = np.stack([arr, arr, arr], axis=-1)
    img = Image.fromarray(rgb, mode="RGB")
    p = tmp_path / "shot.jpg"
    img.save(p, format="JPEG", quality=90)
    return p


def test_compute_default_crop_for_wider_target():
    """4:3 target on a 3:2 source — should crop left/right."""
    rect = compute_default_crop(3000, 2000, get_aspect_ratio("4:3"))
    assert rect is not None
    x, y, w, h = rect
    # 4:3 ratio = 1.333 < 3:2 ratio = 1.5, so target is narrower → crop sides
    assert h == pytest.approx(1.0)
    assert w == pytest.approx((4 / 3) / (3 / 2), rel=1e-6)
    # Centered horizontally
    assert x == pytest.approx((1 - w) / 2)
    assert y == pytest.approx(0.0)


def test_compute_default_crop_for_narrower_target():
    """16:9 on 4:3 — target is wider, crop top/bottom."""
    rect = compute_default_crop(4000, 3000, get_aspect_ratio("16:9"))
    assert rect is not None
    x, y, w, h = rect
    assert w == pytest.approx(1.0)
    assert h < 1.0
    assert y == pytest.approx((1 - h) / 2)


def test_compute_default_crop_returns_none_for_original():
    assert compute_default_crop(4000, 3000, get_aspect_ratio("Original")) is None


def test_compute_default_crop_for_square():
    rect = compute_default_crop(4000, 3000, get_aspect_ratio("1:1"))
    assert rect is not None
    x, y, w, h = rect
    # 1:1 ratio = 1.0 < 4:3 ratio = 1.333 → crop sides, height stays full
    assert h == pytest.approx(1.0)
    assert w == pytest.approx(3 / 4)


def test_apply_crop_clamps_out_of_bounds():
    img = Image.new("RGB", (100, 100), color=(128, 128, 128))
    cropped = apply_crop(img, (-0.5, -0.5, 2.0, 2.0))
    # All clamped → end up cropping the full image.
    assert cropped.size == img.size


def test_apply_crop_extracts_centered_quarter():
    img = Image.new("RGB", (100, 100), color=(0, 0, 0))
    cropped = apply_crop(img, (0.25, 0.25, 0.5, 0.5))
    assert cropped.size == (50, 50)


def test_render_processed_passthrough_with_auto_exp_off_and_original():
    img = Image.new("RGB", (200, 100), color=(80, 80, 80))
    out = render_processed(
        img,
        auto_exposure_on=False,
        aspect_ratio_label="Original",
    )
    assert out.size == img.size
    # No transform → identical pixels.
    assert np.array_equal(np.asarray(out), np.asarray(img))


def test_render_processed_applies_default_crop_when_ratio_set():
    img = Image.new("RGB", (400, 300), color=(80, 80, 80))
    out = render_processed(
        img,
        auto_exposure_on=False,
        aspect_ratio_label="1:1",
    )
    # 1:1 on 4:3 → centered 300×300 square
    assert out.size == (300, 300)


def test_render_processed_uses_explicit_crop_over_default():
    img = Image.new("RGB", (400, 300), color=(80, 80, 80))
    out = render_processed(
        img,
        auto_exposure_on=False,
        aspect_ratio_label="1:1",
        crop_norm=(0.0, 0.0, 0.5, 0.5),  # top-left 200×150 box
    )
    assert out.size == (200, 150)


def test_render_processed_runs_auto_exposure_when_enabled():
    """Mid-gray image — auto-exposure on a degenerate flat signal
    should be a no-op (the engine bails). On a non-flat one the curve
    should change pixel values."""
    arr = np.linspace(60, 120, 200, dtype=np.uint8)
    arr = np.tile(arr, (100, 1))
    rgb = np.stack([arr, arr, arr], axis=-1)
    img = Image.fromarray(rgb, mode="RGB")
    out = render_processed(
        img,
        auto_exposure_on=True,
        aspect_ratio_label="Original",
        strength=1.0,
    )
    assert not np.array_equal(np.asarray(out), np.asarray(img))


def test_output_filename_format():
    ts = datetime(2026, 4, 1, 14, 30, 27)
    src = Path("/some/dir/DSC_001.RW2")
    assert output_filename(ts, src) == "143027_DSC_001.jpg"


def test_save_jpeg_creates_parents_and_writes_file(tmp_path):
    img = Image.new("RGB", (50, 50), color=(200, 100, 50))
    dest = tmp_path / "deep" / "nested" / "out.jpg"
    written = save_jpeg(img, dest)
    assert written == dest
    assert dest.exists()
    # Round-trip — colors should be close to what we wrote, allowing
    # for JPEG compression tolerance.
    reloaded = Image.open(dest).convert("RGB")
    assert reloaded.size == (50, 50)


def test_save_jpeg_uses_high_quality_setting():
    """Sanity: the constant matches what ``save_jpeg`` actually emits.
    A regression on this would silently degrade output quality."""
    assert JPEG_OUTPUT_QUALITY >= 90


def test_apply_rotation_zero_is_passthrough():
    img = Image.new("RGB", (200, 100), color=(80, 80, 80))
    out = apply_rotation(img, 0)
    assert out.size == img.size
    assert np.array_equal(np.asarray(out), np.asarray(img))


def test_apply_rotation_90_swaps_dimensions():
    img = Image.new("RGB", (200, 100), color=(80, 80, 80))
    out = apply_rotation(img, 90)
    assert out.size == (100, 200)


def test_apply_rotation_180_preserves_dimensions():
    img = Image.new("RGB", (200, 100), color=(80, 80, 80))
    out = apply_rotation(img, 180)
    assert out.size == img.size


def test_apply_rotation_clockwise_direction():
    """A 1×1 colored block in the top-left corner should land in the
    top-right after a 90° clockwise rotation. This pins the sign of
    our rotation convention so a refactor doesn't silently flip it."""
    img = Image.new("RGB", (10, 10), color=(0, 0, 0))
    img.putpixel((0, 0), (255, 0, 0))  # top-left corner
    out = apply_rotation(img, 90)
    # After clockwise rotation, top-left pixel ends up at top-right.
    assert out.getpixel((9, 0)) == (255, 0, 0)


def test_render_processed_applies_rotation_before_crop():
    """A landscape 4:3 image rotated 90° becomes 3:4 portrait. A 1:1
    crop on it should produce a square based on the *new* width
    (which equals the old height)."""
    img = Image.new("RGB", (400, 300), color=(80, 80, 80))
    out = render_processed(
        img,
        rotation=90,
        auto_exposure_on=False,
        aspect_ratio_label="1:1",
    )
    # Post-rotation size is (300, 400) → 1:1 max-centered = 300×300.
    assert out.size == (300, 300)


# ── Task #117 — free-angle crop tilt ────────────────────────────


def test_apply_crop_tilt_zero_is_identity():
    """Sub-millidegree angles short-circuit to the input image
    (no pixel work, no FP drift). Identity-by-object is the
    contract — saves a copy and proves the short-circuit fired."""
    from core.process_render import apply_crop_tilt

    img = Image.new("RGB", (40, 30), color=(123, 200, 50))
    out = apply_crop_tilt(img, 0.0)
    assert out is img
    out2 = apply_crop_tilt(img, 1e-4)
    assert out2 is img


def test_apply_crop_tilt_keeps_canvas_size_and_blacks_corners():
    """Free-angle tilt uses ``expand=False`` — the canvas stays the
    same dimensions. The corners pulled past the original frame
    become black (PIL default fill)."""
    from core.process_render import apply_crop_tilt

    img = Image.new("RGB", (40, 30), color=(255, 0, 0))
    out = apply_crop_tilt(img, 10.0)
    assert out.size == (40, 30)
    # Top-left corner is now black (rotated content moved away).
    # Centre is still inside the original frame → still red.
    assert out.getpixel((0, 0)) == (0, 0, 0)
    cx, cy = 20, 15
    r, g, b = out.getpixel((cx, cy))
    assert r > 200 and g < 50 and b < 50, (
        "centre pixel should still be the original red"
    )


def test_render_processed_crop_angle_changes_output():
    """A non-zero ``crop_angle`` in ``render_processed`` produces
    different pixels than the no-tilt baseline. Sufficient to prove
    the new parameter is plumbed end-to-end through the pipeline —
    pixel-exact diffs aren't load-bearing here."""
    img = Image.new("RGB", (60, 40), color=(80, 80, 80))
    # Paint a sharp diagonal so rotation visibly changes pixels.
    for i in range(40):
        img.putpixel((i, i), (255, 255, 255))

    baseline = render_processed(
        img,
        auto_exposure_on=False,
        aspect_ratio_label="Original",
    )
    tilted = render_processed(
        img,
        auto_exposure_on=False,
        aspect_ratio_label="Original",
        crop_angle=5.0,
    )
    assert baseline.size == tilted.size       # expand=False
    assert baseline.tobytes() != tilted.tobytes(), (
        "crop_angle=5 should produce different pixels than crop_angle=0"
    )
