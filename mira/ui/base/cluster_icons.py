"""Cluster icon loader (spec/32 §7).

Loads the SVG cluster icons (``burst.svg`` / ``focus.svg`` /
``exposure.svg`` / ``repeated.svg``) from
``assets/icons/clusters/badge/`` — the spec/69 canonical set the
redesigned ``Thumb`` already consumes. Renders each to a ``QPixmap`` at
any requested cell size, with a count badge composited in the
bottom-right corner.

The status border colour is **not** baked into the icon — the host
``DayGridCell`` paints it as a CSS border using the
``DayGridCell[status="…"]`` QSS roles. The icon SVG draws inside an
inner art area, leaving the outer ~16 px of the 200 px viewBox empty
so the status border lands cleanly around it.

Per-(kind, size) pixmap cache so the slider doesn't re-rasterise every
frame. SVGs themselves are loaded once and reused via the renderer.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

from PyQt6.QtCore import QByteArray, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

log = logging.getLogger(__name__)


# spec/32 §1 + spec/52 Quick Sweep slice A — cluster kinds rendered as
# day-grid cells. ``repeat`` joined the set 2026-06-09 (the Quick Sweep
# redesign added the "tap-twice doublet" detector — see
# ``core.repeat_detector``). Main Cull surfaces still only emit the
# first three; only Quick Sweep produces ``repeat`` buckets today.
CLUSTER_KINDS = ("burst", "focus_bracket", "exposure_bracket", "repeat")

# Map cluster kinds to their SVG file stem. The badge/ filename
# convention spells ``repeated`` (vs the legacy top-level ``repeat``);
# spec/69 retired the top-level set in favour of ``badge/`` so this
# mapping picks up the canonical filename.
_FILE_STEM = {
    "burst": "burst",
    "focus_bracket": "focus",
    "exposure_bracket": "exposure",
    "repeat": "repeated",
}


def _icons_dir() -> Path:
    """Locate ``<repo>/assets/icons/clusters/badge/`` from this package's
    depth. spec/69 chose ``badge/`` as the canonical cluster-icon set
    (shared with :class:`mira.ui.design.Thumb`); the top-level
    ``clusters/*.svg`` retired.

    ``__file__`` is ``…/mira/ui/base/cluster_icons.py`` → parents[3] is
    the repo root.
    """
    return (
        Path(__file__).resolve().parents[3]
        / "assets" / "icons" / "clusters" / "badge"
    )


# Lazy global caches — keyed singletons so multiple DayGridCells share one
# renderer (cheap) and one rasterised pixmap per (kind, size).
_renderers: Dict[str, QSvgRenderer] = {}
_pixmaps: Dict[Tuple[str, int, int], QPixmap] = {}


def _renderer_for(kind: str) -> Optional[QSvgRenderer]:
    """Return the cached ``QSvgRenderer`` for ``kind``, loading on first use.
    Returns ``None`` if the SVG file is missing or fails to parse (the cell
    will render an empty pixmap rather than crash)."""
    cached = _renderers.get(kind)
    if cached is not None:
        return cached
    stem = _FILE_STEM.get(kind)
    if stem is None:
        log.warning("cluster_icons: unknown kind %r", kind)
        return None
    path = _icons_dir() / f"{stem}.svg"
    if not path.is_file():
        log.warning("cluster_icons: missing %s", path)
        return None
    try:
        data = path.read_bytes()
    except OSError as exc:
        log.warning("cluster_icons: cannot read %s: %s", path, exc)
        return None
    renderer = QSvgRenderer(QByteArray(data))
    if not renderer.isValid():
        log.warning("cluster_icons: invalid SVG %s", path)
        return None
    _renderers[kind] = renderer
    return renderer


def cluster_icon(kind: str, size: int, count: int = 0) -> QPixmap:
    """Return a ``QPixmap`` of the cluster icon for ``kind`` at ``size`` px,
    with an optional count badge (omitted when ``count <= 0``).

    The returned pixmap fills the full ``size × size`` square; the icon art
    occupies the centre with ~10 % padding (the host paints the status
    border around the cell, not the pixmap). Transparent background.
    """
    size = max(16, int(size))
    cache_key = (kind, size, int(count))
    cached = _pixmaps.get(cache_key)
    if cached is not None and not cached.isNull():
        return cached

    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        renderer = _renderer_for(kind)
        if renderer is not None:
            # Render the SVG into the full pixmap area (the SVG's own
            # viewBox padding handles the host border's safe zone).
            renderer.render(painter, QRectF(0, 0, size, size))
        if count > 0:
            _paint_count_badge(painter, size, count)
    finally:
        painter.end()
    _pixmaps[cache_key] = pm
    return pm


def _paint_count_badge(painter: QPainter, size: int, count: int) -> None:
    """Paint a small dark-pill count badge in the bottom-right corner,
    sized proportionally to the icon (spec/32 §7 — visible at small +
    large sizes without colliding with the cluster art)."""
    text = str(count)
    # Font scales with cell size; clamps so 80 px reads + 280 px doesn't
    # overflow the icon.
    font_pt = max(7, min(16, size // 14))
    font = QFont()
    font.setPointSize(font_pt)
    font.setBold(True)
    painter.setFont(font)
    fm = painter.fontMetrics()
    text_w = fm.horizontalAdvance(text)
    text_h = fm.height()
    pad_x = max(4, size // 36)
    pad_y = max(1, size // 70)
    badge_w = text_w + pad_x * 2
    badge_h = text_h + pad_y * 2
    radius = badge_h / 2
    margin = max(2, size // 50)
    x = size - badge_w - margin
    y = size - badge_h - margin
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 200))
    painter.drawRoundedRect(QRectF(x, y, badge_w, badge_h), radius, radius)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(
        QRectF(x, y, badge_w, badge_h),
        Qt.AlignmentFlag.AlignCenter,
        text,
    )


def clear_caches() -> None:
    """Drop the renderer + pixmap caches. Tests use this between cases;
    production callers don't need to."""
    _renderers.clear()
    _pixmaps.clear()
