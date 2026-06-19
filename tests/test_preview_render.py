"""spec/89 §11.3 polish — core.preview_render.develop_photo_array.

Pins the live Mira-develop preview pipeline that the Export preview
viewer pipes 0-version cells + virtual Mira cluster members through.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.preview_render import develop_photo_array


def _write_source(tmp_path: Path) -> Path:
    """Write a tiny RGB JPEG and return its path."""
    from PyQt6.QtGui import QColor, QImage
    img = QImage(64, 48, QImage.Format.Format_RGB888)
    img.fill(QColor(120, 80, 60))
    out = tmp_path / "src.jpg"
    assert img.save(str(out), "JPG", 90)
    return out


def test_develop_photo_array_identity_returns_source_shape(qapp, tmp_path):
    """An adjustment at baseline (look='original', no filter, no crop)
    returns an array with the source's H × W × 3 shape — the
    pipeline is a clean pass-through."""
    src = _write_source(tmp_path)
    adj = SimpleNamespace(
        look="original", creative_filter=None,
        crop_x=None, crop_y=None, crop_w=None, crop_h=None,
        crop_angle=0.0, rotation=0, look_strength=1.0, style=None,
    )
    arr = develop_photo_array(src, adj)
    assert arr is not None
    assert arr.shape == (48, 64, 3)


def test_develop_photo_array_with_crop_returns_cropped_shape(
        qapp, tmp_path):
    """An adjustment with a normalised crop returns the cropped sub-
    region's shape (80% × 80% of 64 × 48 ≈ 52 × 38)."""
    src = _write_source(tmp_path)
    adj = SimpleNamespace(
        look="natural", creative_filter=None,
        crop_x=0.1, crop_y=0.1, crop_w=0.8, crop_h=0.8,
        crop_angle=0.0, rotation=0, look_strength=1.0,
        style="general",
    )
    arr = develop_photo_array(src, adj)
    assert arr is not None
    h, w, c = arr.shape
    assert c == 3
    assert 36 <= h <= 40                     # 48 * 0.8 = 38.4
    assert 50 <= w <= 54                     # 64 * 0.8 = 51.2


def test_develop_photo_array_none_adjustment_is_identity_pass(
        qapp, tmp_path):
    """``adjustment=None`` (no row in store for this item) yields the
    identity-developed source rather than a crash — the pipeline
    treats absence as baseline."""
    src = _write_source(tmp_path)
    arr = develop_photo_array(src, None)
    assert arr is not None
    assert arr.shape == (48, 64, 3)


def test_develop_photo_array_missing_source_returns_none(
        qapp, tmp_path):
    """A missing source path yields ``None``; the dialog falls back to
    a raw-pixmap read (which itself fails, but no crash)."""
    missing = tmp_path / "does-not-exist.jpg"
    arr = develop_photo_array(missing, None)
    assert arr is None


def test_develop_photo_array_unknown_filter_yields_none(
        qapp, tmp_path):
    """An unknown creative_filter key bubbles a ValueError out of the
    photo_auto resolver; the pipeline catches it and returns None so
    the caller falls back to a raw read."""
    src = _write_source(tmp_path)
    adj = SimpleNamespace(
        look="natural", creative_filter="not_a_real_filter",
        crop_x=None, crop_y=None, crop_w=None, crop_h=None,
        crop_angle=0.0, rotation=0, look_strength=1.0,
        style="general",
    )
    arr = develop_photo_array(src, adj)
    assert arr is None


def test_develop_photo_array_downscales_huge_inputs(qapp, tmp_path):
    """A source past ``max_long_edge`` gets scaled down before the
    pipeline runs so the dialog doesn't pay full-resolution cost on
    a preview that fits in ~2400 px anyway."""
    from PyQt6.QtGui import QColor, QImage
    img = QImage(4000, 3000, QImage.Format.Format_RGB888)
    img.fill(QColor(10, 20, 30))
    src = tmp_path / "huge.jpg"
    assert img.save(str(src), "JPG", 90)
    adj = SimpleNamespace(
        look="original", creative_filter=None,
        crop_x=None, crop_y=None, crop_w=None, crop_h=None,
        crop_angle=0.0, rotation=0, look_strength=1.0, style=None,
    )
    arr = develop_photo_array(src, adj, max_long_edge=1200)
    assert arr is not None
    long_edge = max(arr.shape[0], arr.shape[1])
    assert long_edge <= 1200
