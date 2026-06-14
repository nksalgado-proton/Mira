"""``image_loader.load_pixmap`` target-size hint (Nelson 2026-06-09
fast-nav redesign).

The hint asks the JPEG decoder to emit a scaled image directly via
``QImageReader.setScaledSize`` — that's where the 3–4× JPEG decode
speed-up on the Picker photo surface comes from. These tests pin the
contract that the hint is honoured AND that aspect ratio is preserved
inside the requested bounding box."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from PyQt6.QtCore import QSize

from mira.ui.media.image_loader import load_pixmap


@pytest.fixture()
def big_jpeg(tmp_path: Path) -> Path:
    """A 4000×3000 JPEG large enough that DCT-domain downscale matters."""
    path = tmp_path / "big.jpg"
    Image.new("RGB", (4000, 3000), color=(180, 60, 90)).save(path, "JPEG")
    return path


def test_load_pixmap_no_hint_returns_full_size(qapp, big_jpeg):
    pixmap = load_pixmap(big_jpeg)
    assert not pixmap.isNull()
    assert pixmap.width() == 4000
    assert pixmap.height() == 3000


def test_load_pixmap_target_size_scales_jpeg(qapp, big_jpeg):
    pixmap = load_pixmap(big_jpeg, target_size=QSize(800, 600))
    assert not pixmap.isNull()
    # Must fit inside the requested box.
    assert pixmap.width() <= 800
    assert pixmap.height() <= 600
    # And must touch one edge of the box — aspect-preserving fit.
    assert pixmap.width() == 800 or pixmap.height() == 600


def test_load_pixmap_target_size_preserves_aspect(qapp, big_jpeg):
    pixmap = load_pixmap(big_jpeg, target_size=QSize(800, 800))
    assert not pixmap.isNull()
    # Source is 4:3. Bounded to 800×800 → fits to 800×600.
    assert pixmap.width() == 800
    assert pixmap.height() == 600


def test_load_pixmap_target_size_larger_than_source_is_noop(qapp, big_jpeg):
    """When the target box is bigger than the source, we don't upscale —
    setScaledSize is skipped and the decoder emits native size."""
    pixmap = load_pixmap(big_jpeg, target_size=QSize(8000, 6000))
    assert not pixmap.isNull()
    assert pixmap.width() == 4000
    assert pixmap.height() == 3000


def test_load_pixmap_invalid_target_size_falls_back_to_full(qapp, big_jpeg):
    """``QSize()`` (invalid / zero-area) silently degrades to no-hint."""
    pixmap = load_pixmap(big_jpeg, target_size=QSize())
    assert not pixmap.isNull()
    assert pixmap.width() == 4000
    assert pixmap.height() == 3000
