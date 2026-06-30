"""Tests for the spec/155 letterboxed-map Cut day-separator + opener."""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtGui import QColor, QImage

from mira.ui.shared.separator_card import (
    render_cut_opener_image,
    render_flat_background,
    render_separator_image,
)


def _write_recognizable_map(path: Path, color: int = 0xFFBB2233) -> Path:
    """Write a tiny solid-coloured JPEG so we can detect the
    letterboxed composition vs. the flat colour card."""
    img = QImage(64, 36, QImage.Format.Format_RGB32)
    img.fill(color)
    img.save(str(path), "JPEG", 95)
    return path


def _has_pixel_near(img: QImage, target: QColor, tolerance: int = 32) -> bool:
    """True if any sampled pixel in ``img`` is within ``tolerance`` of
    ``target`` (per channel). Samples a 6×6 grid to avoid a 720k-pixel
    walk."""
    w, h = img.width(), img.height()
    for x_frac in (0.1, 0.25, 0.4, 0.5, 0.6, 0.75, 0.9):
        for y_frac in (0.1, 0.25, 0.4, 0.5, 0.6, 0.75, 0.9):
            x, y = int(w * x_frac), int(h * y_frac)
            c = QColor(img.pixel(x, y))
            dr = abs(c.red() - target.red())
            dg = abs(c.green() - target.green())
            db = abs(c.blue() - target.blue())
            if dr <= tolerance and dg <= tolerance and db <= tolerance:
                return True
    return False


# ── fallback to text card when no map ───────────────────────────

def test_render_separator_image_without_map_is_flat_text_card(qapp):
    """No map_image_path → existing flat colour card with text."""
    img = render_separator_image(
        day_number=2, date="2026-06-02", location="Sintra",
        description="x", aspect="16:9", height=360,
        card_style="black", seed_key="cut-1:2",
        map_image_path=None,
    )
    # Top-left corner pixel comes from the flat fill (no map composite).
    c = QColor(img.pixel(0, 0))
    assert c.red() == 0x15 and c.green() == 0x17 and c.blue() == 0x1B, (
        f"Expected flat near-black background, got {c.name()}")


def test_render_separator_image_falls_back_when_map_missing(
        qapp, tmp_path):
    """An unreadable / missing map_image_path falls back to the flat card."""
    img = render_separator_image(
        day_number=1, aspect="16:9", height=360,
        card_style="black", seed_key="cut-1:1",
        map_image_path=tmp_path / "nope.jpg",
    )
    c = QColor(img.pixel(0, 0))
    assert c.red() == 0x15  # flat near-black


# ── letterboxed-map branch ──────────────────────────────────────

def test_render_separator_image_with_map_paints_map_pixels(qapp, tmp_path):
    """With a valid map_image_path, the rendered image carries the
    map's colour somewhere in the inset region — proving the
    composite ran (and the flat-black fill was overwritten)."""
    src = _write_recognizable_map(tmp_path / "map.jpg")
    img = render_separator_image(
        day_number=2, date="2026-06-02", location="Sintra",
        description="x", aspect="16:9", height=360,
        card_style="black", seed_key="cut-1:2",
        map_image_path=src,
    )
    # The map is solid red-ish (#BB2233); the composite should leave
    # red-ish pixels somewhere in the centred inset region.
    assert _has_pixel_near(img, QColor(0xBB, 0x22, 0x33)), (
        "Expected to find map-coloured pixels in the rendered slide.")


def test_render_separator_image_with_map_has_caption_strip(qapp, tmp_path):
    """The bottom strip (~16% slide height) should be appreciably
    darker than the centred inset above it — proving the caption
    backing was painted on top of the map."""
    src = _write_recognizable_map(tmp_path / "map.jpg")
    img = render_separator_image(
        day_number=2, date="2026-06-02", location="Sintra",
        description="day 2", aspect="16:9", height=360,
        card_style="black", seed_key="cut-1:2",
        map_image_path=src,
    )
    h = img.height()
    centre_y = h // 2
    # Sample inside the caption strip but BELOW the text glyphs. Title
    # + sub render in the upper portion of the strip; the bottom few
    # pixels are pure scrim. h-3 is reliably below the glyph block
    # regardless of how the date / description line wraps (Nelson
    # 2026-06-30 — the shorter date-only sub used to leave glyphs at
    # h-16 which read brighter than the centre map pixel).
    strip_y = h - 3
    centre = QColor(img.pixel(img.width() // 2, centre_y))
    strip = QColor(img.pixel(img.width() // 2, strip_y))
    # Strip should be noticeably darker (the translucent black scrim).
    assert strip.red() + strip.green() + strip.blue() \
        < centre.red() + centre.green() + centre.blue()


def test_render_flat_background_with_map_letterboxes(qapp, tmp_path):
    """spec/153 export path: the text-less background, with a map set,
    is the letterboxed map (no caption text)."""
    src = _write_recognizable_map(tmp_path / "map.jpg")
    img = render_flat_background(
        aspect="16:9", height=360,
        card_style="black", seed_key="cut-1:2",
        map_image_path=src,
    )
    assert _has_pixel_near(img, QColor(0xBB, 0x22, 0x33))


def test_render_flat_background_without_map_is_flat_color(qapp):
    """Sanity — no map → original flat behaviour."""
    img = render_flat_background(
        aspect="16:9", height=180,
        card_style="black", seed_key="cut-1:1",
    )
    c = QColor(img.pixel(img.width() // 2, img.height() // 2))
    assert c.red() == 0x15  # flat near-black


def test_render_cut_opener_image_with_map_letterboxes(qapp, tmp_path):
    """spec/155 §5 — event-level map drives the Cut intro slide too."""
    src = _write_recognizable_map(tmp_path / "evt.jpg")
    img = render_cut_opener_image(
        tag_text="Portugal 2026", lines=["12 photos · 3 min"],
        aspect="16:9", height=360,
        card_style="black", seed_key="cut-1",
        map_image_path=src,
    )
    assert _has_pixel_near(img, QColor(0xBB, 0x22, 0x33))


# ── aspect preservation under contain ───────────────────────────

def test_letterboxed_map_preserves_canvas_dimensions(qapp, tmp_path):
    """The composite must never resize the canvas — the output image
    has exactly the aspect-driven (w, h) regardless of the map's
    own aspect."""
    # Map is 64x36 (~16:9); render at 4:3 360h to force a contain-fit.
    src = _write_recognizable_map(tmp_path / "map.jpg")
    img = render_separator_image(
        day_number=1, aspect="4:3", height=300,
        card_style="black", seed_key="cut-1:1",
        map_image_path=src,
    )
    assert img.height() == 300
    # Width follows aspect (4:3 → 400 wide).
    assert img.width() == 400
