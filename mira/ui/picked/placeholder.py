"""Kind-aware placeholder pixmap for unrenderable Day-Grid cells.

Extracted from the retired ``mira/ui/picked/pick_page.py`` shell when the
Pick host was replaced by ``mira/ui/pages/picker_page.py`` (Surface 07)
+ ``mira/ui/pages/video_picker_page.py`` (Surface 11) wired directly on
the page stack. The helper itself is unchanged — DayGridCell still asks
for it when a thumbnail can't be decoded (truncated JPEG, unsupported
codec, etc.) so the cell at least communicates "this item exists" +
(for videos) "it's a video, no preview".
"""
from __future__ import annotations

from typing import Dict

from PyQt6.QtCore import Qt

from mira.ui.i18n import tr


_PLACEHOLDER_CACHE: Dict[str, "object"] = {}


def placeholder_pixmap(kind: str):
    """A 320×180 (16:9) tinted placeholder pixmap for an unrenderable cell.

    Videos get a ▶ glyph + "no preview" caption; photos / snapshots
    get the caption only. The pixmap is built once per kind and cached
    at module level — DayGridCell may render the same pixmap into many
    cells across a session.
    """
    if kind in _PLACEHOLDER_CACHE:
        return _PLACEHOLDER_CACHE[kind]
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import (
        QBrush, QColor, QFont, QPainter, QPainterPath, QPixmap,
    )
    w, h = 320, 180
    pm = QPixmap(w, h)
    pm.fill(QColor("#2A2A2E"))  # neutral dark — both themes survive it
    p = QPainter(pm)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if kind == "video":
            tri = QPainterPath()
            cx, cy = w / 2, h / 2 - 8
            r = 28
            tri.moveTo(cx - r * 0.5, cy - r * 0.7)
            tri.lineTo(cx + r * 0.8, cy)
            tri.lineTo(cx - r * 0.5, cy + r * 0.7)
            tri.closeSubpath()
            p.setBrush(QBrush(QColor("#9CA3AF")))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(tri)
        p.setPen(QColor("#9CA3AF"))
        font = QFont(p.font())
        font.setPixelSize(13)
        p.setFont(font)
        rect = QRectF(0, h / 2 + 24, w, 30)
        p.drawText(
            rect,
            int(Qt.AlignmentFlag.AlignCenter),
            tr("no preview"),
        )
    finally:
        p.end()
    _PLACEHOLDER_CACHE[kind] = pm
    return pm


# Backwards-compatible alias for the test that pinned the underscore name.
_placeholder_pixmap = placeholder_pixmap
