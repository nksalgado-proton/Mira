"""SVG icon helpers — the line-icon family's one render path.

Every spec/69 surface (and the pre-existing `_CategoryTile` /
`_CrossEventGlyph` / `_render_search_glyph`) renders the same shape:

    1. Load the SVG with ``QSvgRenderer``.
    2. Render it into a transparent ARGB ``QImage`` at the target size.
    3. Re-fill the image with the tint colour using
       ``CompositionMode_SourceIn`` — the tint shows through the SVG's
       alpha mask, so a black/white/currentColor SVG comes out as the
       palette colour we asked for.
    4. Return / draw the resulting ``QPixmap``.

Centralising it here means:

* one cache, keyed by (path, size, color, theme_mode) so theme toggles
  invalidate transparently;
* one place to fix when the line-icon family grows (e.g. variable
  stroke-width, hi-DPI handling, missing-file logging);
* one rendering pass at every call site (the inline copies in
  ``_event_card_redesign``, ``_cross_event_band`` and ``inputs.py``
  retire below to this helper).

The pixmap is suitable for `QLabel.setPixmap`; for in-paintEvent
callers, see :func:`paint_tinted_svg` for the QPainter shortcut.
"""
from __future__ import annotations

from pathlib import Path
from typing import Tuple, Union

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

PathLike = Union[str, Path]
ColorLike = Union[QColor, str]


def _normalise_color(color: ColorLike) -> QColor:
    return color if isinstance(color, QColor) else QColor(color)


# Cache key: (str(path), size, color_argb). Module-level dict keeps
# repeated renders cheap (a cell that scrolls past + back doesn't
# re-rasterise the SVG). The cache never invalidates — a theme change
# passes a different colour, which keys to a different entry, so an
# old entry sticks around until the process exits. That's fine; even a
# heavily-used app stays well under a megabyte of cached pixmaps.
_CACHE: dict[Tuple[str, int, int], QPixmap] = {}


def tinted_svg_pixmap(
    path: PathLike,
    size: int,
    color: ColorLike,
) -> QPixmap:
    """Return a ``size × size`` QPixmap of the SVG at ``path`` tinted
    with ``color``. Missing file or invalid SVG returns an empty
    pixmap (the caller's UI degrades to no icon rather than crashing).

    Reference implementation: ``_event_card_redesign._CategoryTile.
    paintEvent`` (spec/69). This is the same pattern, factored out.
    """
    p = str(path)
    qcolor = _normalise_color(color)
    cache_key = (p, int(size), qcolor.rgba())
    cached = _CACHE.get(cache_key)
    if cached is not None and not cached.isNull():
        return cached

    if not Path(p).is_file():
        pm = QPixmap()
        _CACHE[cache_key] = pm
        return pm

    renderer = QSvgRenderer(p)
    if not renderer.isValid():
        pm = QPixmap()
        _CACHE[cache_key] = pm
        return pm

    img = QImage(int(size), int(size), QImage.Format.Format_ARGB32)
    img.fill(0)
    ip = QPainter(img)
    try:
        ip.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(ip)
        ip.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceIn)
        ip.fillRect(img.rect(), qcolor)
    finally:
        ip.end()
    pm = QPixmap.fromImage(img)
    _CACHE[cache_key] = pm
    return pm


def paint_tinted_svg(
    painter: QPainter,
    path: PathLike,
    rect_x: float,
    rect_y: float,
    size: int,
    color: ColorLike,
) -> None:
    """Paint the SVG centred in a ``size × size`` square at ``(rect_x,
    rect_y)`` using the active painter. The painter caller is in
    charge of save/restore around any clipping or transforms."""
    pm = tinted_svg_pixmap(path, size, color)
    if pm.isNull():
        return
    painter.drawPixmap(int(rect_x), int(rect_y), pm)


def clear_cache() -> None:
    """Drop the cached pixmap dict. Production callers don't need this;
    tests use it between cases to keep state isolated."""
    _CACHE.clear()


# Convenience constants — every surface that wires a glyph references
# the same path, so spell the absolute paths once here. Resolved at
# import (the repo layout is static) so call sites don't replay the
# parents[] math.
_ICONS_ROOT = Path(__file__).resolve().parents[3] / "assets" / "icons"
GLYPHS_DIR = _ICONS_ROOT / "glyphs"
CATEGORIES_DIR = _ICONS_ROOT / "categories"
CLUSTERS_DIR = _ICONS_ROOT / "clusters" / "badge"

# Named line-icon glyphs from ``assets/icons/glyphs/``. Importable
# constants give a single typo-checkable surface for the wiring
# sites + a discoverable directory of the family.
GLYPH_SEARCH = GLYPHS_DIR / "search.svg"
GLYPH_CROSS_EVENT = GLYPHS_DIR / "cross_event.svg"
GLYPH_EYE = GLYPHS_DIR / "eye.svg"
GLYPH_CHECK = GLYPHS_DIR / "check.svg"
GLYPH_CROSS = GLYPHS_DIR / "cross.svg"


__all__ = [
    "CATEGORIES_DIR",
    "CLUSTERS_DIR",
    "GLYPHS_DIR",
    "GLYPH_CHECK",
    "GLYPH_CROSS",
    "GLYPH_CROSS_EVENT",
    "GLYPH_EYE",
    "GLYPH_SEARCH",
    "clear_cache",
    "paint_tinted_svg",
    "tinted_svg_pixmap",
]
