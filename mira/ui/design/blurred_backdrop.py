"""Shared blurred-cover backdrop recipe used by every photo / video
canvas that needs a "no hard black bar" letterbox fill.

The recipe was originally locked inside
:class:`mira.ui.design.blurred_photo_canvas.BlurredPhotoCanvas` for the
Cut detail grid + Cut player slot. The 2026-06-15 Picker canvas sweep
(Nelson — "bring the blurred-fill backdrop into PhotoViewport, for both
photos and video") extracted it here so:

1. one place owns the look — same tile size, same darken alpha, same
   cover-scale mode — and a tweak to the recipe touches every surface;
2. PhotoViewport (the one display engine, spec/63) reuses it without
   re-implementing the blur on its own paint path.

Two functions, both pure:

* :func:`blurred_tiny` — the cacheable step. Downscale to 48×48 with
  ``IgnoreAspectRatio`` (the blur), darken with ~alpha-120 black via
  ``CompositionMode_SourceAtop``, return the resulting ``QPixmap``.
  Hosts cache this and invalidate on source change.
* :func:`blurred_cover` — the per-paint step. Scale a tiny up to fit
  the slot with ``KeepAspectRatioByExpanding`` (cover) +
  ``SmoothTransformation``. Cheap; safe to call per paint.

Both return ``None`` when their input is empty so callers can branch
on the placeholder fallback (BlurredPhotoCanvas paints
``EMPTY_FILL``; the viewport's bg shows through).
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap


#: The blur "resolution" — every backdrop step downscales the source
#: to this size before scaling back up, which IS the blur (a 48×48
#: smooth-scaled photo carries no detail). Same value
#: BlurredPhotoCanvas locked.
TINY_SIZE = QSize(48, 48)

#: The darken alpha applied on top of the tiny so the sharp media on
#: top reads as the focal point. ~alpha 120 = 47% black — strong
#: enough to settle the bars under a bright photo without crushing
#: the dim ones.
DARKEN_ALPHA = 120


def blurred_tiny(source: Optional[QPixmap]) -> Optional[QPixmap]:
    """The cacheable 48×48 darkened blur of ``source``. ``None`` if the
    source is missing or empty."""
    if source is None or source.isNull():
        return None
    small = source.scaled(
        TINY_SIZE,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    img = small.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    p = QPainter(img)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
    p.fillRect(img.rect(), QColor(0, 0, 0, DARKEN_ALPHA))
    p.end()
    return QPixmap.fromImage(img)


def blurred_cover(
    tiny: Optional[QPixmap],
    size: QSize,
) -> Optional[QPixmap]:
    """Scale a tiny backdrop to cover ``size`` with
    ``KeepAspectRatioByExpanding``. Returns ``None`` when ``tiny`` is
    missing or ``size`` is empty so callers can branch on the
    placeholder fallback."""
    if tiny is None or tiny.isNull():
        return None
    if size.width() <= 0 or size.height() <= 0:
        return None
    return tiny.scaled(
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
