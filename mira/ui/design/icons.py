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
from PyQt6.QtWidgets import QApplication

PathLike = Union[str, Path]
ColorLike = Union[QColor, str]


def _normalise_color(color: ColorLike) -> QColor:
    return color if isinstance(color, QColor) else QColor(color)


def _device_pixel_ratio() -> float:
    """The active screen's device-pixel ratio, or 1.0 if Qt isn't up.

    The SVG render path multiplies by this so a 16-logical-pixel icon
    renders at 32 actual pixels on a 2× display; ``setDevicePixelRatio``
    on the returned pixmap then tells Qt to draw it at 16 logical px.
    Without this step every icon on a HiDPI screen looks low-res
    (Qt scales the LOGICAL-sized pixmap up to fill the physical area).
    """
    app = QApplication.instance()
    if app is None:
        return 1.0
    screen = app.primaryScreen()
    if screen is None:
        return 1.0
    try:
        return float(screen.devicePixelRatio())
    except Exception:                                              # noqa: BLE001
        return 1.0


# Cache key: (str(path), size, color_argb, dpr_quantised). The DPR
# component lets a window moved between screens with different DPRs
# pick up a sharp re-render rather than reusing the cached low-res
# version. Module-level dict keeps repeated renders cheap (a cell that
# scrolls past + back doesn't re-rasterise the SVG). The cache never
# invalidates explicitly — a theme change passes a different colour,
# which keys to a different entry, so an old entry sticks around until
# the process exits. That's fine; even a heavily-used app stays well
# under a megabyte of cached pixmaps.
_CACHE: dict[Tuple[str, int, int, int], QPixmap] = {}


def tinted_svg_pixmap(
    path: PathLike,
    size: int,
    color: ColorLike,
) -> QPixmap:
    """Return a logical ``size × size`` QPixmap of the SVG at ``path``
    tinted with ``color``, rasterised at the active screen's
    device-pixel ratio so it stays sharp on HiDPI displays. Missing
    file or invalid SVG returns an empty pixmap (the caller's UI
    degrades to no icon rather than crashing).

    HiDPI contract: the returned pixmap reports ``devicePixelRatio()``
    matching the active screen, and its physical ``width()`` /
    ``height()`` are ``size × dpr``. When callers position the pixmap
    they MUST use the LOGICAL ``size`` they passed in, not
    ``pm.width()``; otherwise the position scales with the DPR.
    ``painter.drawPixmap(x, y, pm)`` Just Works because Qt reads the
    pixmap's DPR.
    """
    p = str(path)
    qcolor = _normalise_color(color)
    dpr = _device_pixel_ratio()
    # Quantise the DPR to 0.01 so 1.0 / 1.25 / 1.5 / 2.0 each get
    # their own cache slot without floating-point noise.
    cache_key = (p, int(size), qcolor.rgba(), int(round(dpr * 100)))
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

    phys = max(1, int(round(size * dpr)))
    img = QImage(phys, phys, QImage.Format.Format_ARGB32)
    img.fill(0)
    ip = QPainter(img)
    try:
        ip.setRenderHint(QPainter.RenderHint.Antialiasing)
        ip.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        renderer.render(ip)
        ip.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceIn)
        ip.fillRect(img.rect(), qcolor)
    finally:
        ip.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
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
PHASES_DIR = _ICONS_ROOT / "phases"

# Named line-icon glyphs from ``assets/icons/glyphs/``. Importable
# constants give a single typo-checkable surface for the wiring
# sites + a discoverable directory of the family.
GLYPH_SEARCH = GLYPHS_DIR / "search.svg"
GLYPH_CROSS_EVENT = GLYPHS_DIR / "cross_event.svg"
GLYPH_EYE = GLYPHS_DIR / "eye.svg"
GLYPH_CHECK = GLYPHS_DIR / "check.svg"
GLYPH_CROSS = GLYPHS_DIR / "cross.svg"
GLYPH_EVENT = GLYPHS_DIR / "event.svg"
GLYPH_CUT = GLYPHS_DIR / "cut.svg"
GLYPH_VOLUME = GLYPHS_DIR / "volume.svg"
GLYPH_VOLUME_MUTED = GLYPHS_DIR / "volume_muted.svg"
GLYPH_PLAY = GLYPHS_DIR / "play.svg"
GLYPH_PAUSE = GLYPHS_DIR / "pause.svg"
GLYPH_TO_START = GLYPHS_DIR / "to_start.svg"
GLYPH_TO_END = GLYPHS_DIR / "to_end.svg"
GLYPH_CLIP = GLYPHS_DIR / "clip.svg"
GLYPH_SNAPSHOT = GLYPHS_DIR / "snapshot.svg"

# Phase glyphs — drawn for Surface 01's open-card pipeline rows so the
# mockup's `📥 / ⭐ / 🎨 / 📤` emojis become real line-icons (spec/65
# §2.1). Same 24×24 viewBox / 1.8 stroke-width family as the others.
PHASE_GLYPH = {
    "collect": PHASES_DIR / "collect.svg",
    "pick":    PHASES_DIR / "pick.svg",
    "edit":    PHASES_DIR / "edit.svg",
    "export":  PHASES_DIR / "export.svg",
}


__all__ = [
    "CATEGORIES_DIR",
    "CLUSTERS_DIR",
    "GLYPHS_DIR",
    "GLYPH_CHECK",
    "GLYPH_CLIP",
    "GLYPH_CROSS",
    "GLYPH_CROSS_EVENT",
    "GLYPH_CUT",
    "GLYPH_EVENT",
    "GLYPH_EYE",
    "GLYPH_PAUSE",
    "GLYPH_PLAY",
    "GLYPH_SEARCH",
    "GLYPH_SNAPSHOT",
    "GLYPH_TO_END",
    "GLYPH_TO_START",
    "GLYPH_VOLUME",
    "GLYPH_VOLUME_MUTED",
    "PHASES_DIR",
    "PHASE_GLYPH",
    "clear_cache",
    "paint_tinted_svg",
    "tinted_svg_pixmap",
]
