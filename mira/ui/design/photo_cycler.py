"""PhotoCycler — chrome-free ambient photo slideshow.

The closed-event tile (spec/75 §6) needs the ambient feel of a slideshow
without any of the heavyweight ``Carousel`` chrome — Nelson called out the
overlaid arrows + dot row + hover-pause + click-to-jump as artefacts he
sees mis-painting their children. The whole closed tile is the one click
target; the slideshow inside should advance on its own and never paint
its own controls over the photo.

This widget keeps the one genuinely useful piece of ``carousel.py`` — the
blurred-fill backdrop pattern so a photo of any aspect ratio shows
*contained* over a darkened blurred copy of itself, never cropped, no
letterbox bars — and strips everything else:

* Photos are shuffled into a random order on construction (and re-shuffled
  if the list is later swapped via :meth:`setPixmaps`).
* A ``QTimer`` advances to the next photo every ``interval_ms`` (default
  3500 — the spec calls for 3–4 s).
* No arrows, no dots, no hover-pause, no click-to-jump. Resizing rescales
  the contained photo; that's the entire interaction surface.
* An optional bottom caption strip carries name + counts plus a small
  ``Closed`` pill top-right and a ``Trip``/``Session`` tag top-left.
  Captions paint inside the cycler so they don't add to the tile's
  layout cost — the whole tile remains a flat box.

Inline ``setStyleSheet`` is avoided on the cycler body itself. The two
caption pieces use simple inline styles for translucent-dark fills (no
themable equivalent exists in QSS for partial transparency at this scale);
that follows the same pattern the carousel chrome used.
"""
from __future__ import annotations

import random
from typing import List, Optional

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QSizePolicy, QWidget


_RADIUS = 14.0
_PHOTO_INSET = 6  # px the contained photo sits inset from the tile edge
_DEFAULT_INTERVAL_MS = 3500


def _mixed_rounded_path(
    rect: QRectF, *, top: float, bottom: float
) -> QPainterPath:
    """Build a path with the top corners rounded by ``top`` and the
    bottom corners rounded by ``bottom``. ``0`` on either pair leaves
    those corners square — used by the event tile's closed body where
    the photo's top edge meets the title row (square) and its bottom
    edge meets the tile's bottom rounding (radius_xl)."""
    path = QPainterPath()
    if top <= 0 and bottom <= 0:
        path.addRect(rect)
        return path
    x, y, w, h = rect.left(), rect.top(), rect.width(), rect.height()
    path.moveTo(x + top, y)
    path.lineTo(x + w - top, y)
    if top > 0:
        path.arcTo(x + w - 2 * top, y, 2 * top, 2 * top, 90, -90)
    path.lineTo(x + w, y + h - bottom)
    if bottom > 0:
        path.arcTo(
            x + w - 2 * bottom, y + h - 2 * bottom,
            2 * bottom, 2 * bottom, 0, -90,
        )
    path.lineTo(x + bottom, y + h)
    if bottom > 0:
        path.arcTo(x, y + h - 2 * bottom, 2 * bottom, 2 * bottom, 270, -90)
    path.lineTo(x, y + top)
    if top > 0:
        path.arcTo(x, y, 2 * top, 2 * top, 180, -90)
    path.closeSubpath()
    return path


class PhotoCycler(QWidget):
    """Chrome-free slideshow over a shuffled photo list.

    Args:
        pixmaps:       The photos to cycle through. ``None`` / empty draws a
                       "no photos" placeholder so the tile never reads as
                       broken.
        interval_ms:   Auto-advance interval. Pass ``0`` to freeze on the
                       first frame (useful for tests + the single-photo
                       case).
        caption:       Bottom caption line (typically the event name). Empty
                       string omits the bottom strip entirely.
        sub_caption:   Secondary line under ``caption`` ("169 shot · 18
                       exported"). Optional.
        tag_text:      Small uppercase pill painted top-left (the
                       ``Trip``/``Session`` type tag). Empty string skips.
        pill_text:     Small pill painted top-right (the ``Closed`` status).
                       Empty string skips.
        top_radius:    Round the top-left / top-right corners by this many
                       pixels (default ``_RADIUS``). Set to ``0`` when the
                       cycler sits below a title row so the top edge meets
                       the row at a straight line.
        bottom_radius: Round the bottom-left / bottom-right corners by this
                       many pixels (default ``_RADIUS``). Set to the host
                       tile's outer radius (``radius_xl``) when the cycler
                       sits inside an event tile, so the photo's bottom
                       lines up with the tile border exactly.
    """

    indexChanged = pyqtSignal(int)

    def __init__(
        self,
        pixmaps: Optional[List[QPixmap]] = None,
        *,
        interval_ms: int = _DEFAULT_INTERVAL_MS,
        caption: str = "",
        sub_caption: str = "",
        tag_text: str = "",
        pill_text: str = "",
        top_radius: float = _RADIUS,
        bottom_radius: float = _RADIUS,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._caption = caption
        self._sub_caption = sub_caption
        self._tag_text = tag_text
        self._pill_text = pill_text
        self._top_radius = float(top_radius)
        self._bottom_radius = float(bottom_radius)
        self._interval_ms = max(0, int(interval_ms))
        self._index = 0
        self._tiny_cache: dict[int, QPixmap] = {}
        self._set_pixmaps(pixmaps or [])

        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.setMinimumHeight(120)

        self._timer = QTimer(self)
        self._timer.setInterval(self._interval_ms or _DEFAULT_INTERVAL_MS)
        self._timer.timeout.connect(self._advance)
        if self._interval_ms > 0 and len(self._pixmaps) > 1:
            self._timer.start()

    # ── public API ────────────────────────────────────────────────────

    def setPixmaps(self, pixmaps: List[QPixmap]) -> None:
        """Replace the photo list. Re-shuffles, resets to index 0, and
        re-starts the timer if there are now ≥2 photos."""
        self._set_pixmaps(pixmaps)
        if self._interval_ms > 0 and len(self._pixmaps) > 1:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def index(self) -> int:
        """Current frame index (into the shuffled order)."""
        return self._index

    def count(self) -> int:
        return len(self._pixmaps)

    # ── internals ─────────────────────────────────────────────────────

    def _set_pixmaps(self, pixmaps: List[QPixmap]) -> None:
        # Filter null pixmaps defensively — the loader path can hand back
        # an empty QPixmap when the source file disappeared between the
        # gateway probe and the load. A null entry would paint the
        # "no photos" placeholder mid-cycle.
        items: list[QPixmap] = [pm for pm in pixmaps if not pm.isNull()]
        random.shuffle(items)
        self._pixmaps = items
        self._index = 0
        self._tiny_cache = {}

    def _advance(self) -> None:
        if len(self._pixmaps) <= 1:
            return
        self._index = (self._index + 1) % len(self._pixmaps)
        self.indexChanged.emit(self._index)
        self.update()

    def _backdrop_for(self, idx: int) -> Optional[QPixmap]:
        """Return a cached darkened-thumbnail copy of the photo at
        ``idx``. Size-independent so a single 48×48 cache entry is reused
        across every resize."""
        if not self._pixmaps:
            return None
        cached = self._tiny_cache.get(idx)
        if cached is not None:
            return cached
        src = self._pixmaps[idx]
        small = src.scaled(
            48, 48,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        img = small.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        p = QPainter(img)
        p.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceAtop
        )
        p.fillRect(img.rect(), QColor(0, 0, 0, 120))
        p.end()
        tiny = QPixmap.fromImage(img)
        self._tiny_cache[idx] = tiny
        return tiny

    # ── painting ──────────────────────────────────────────────────────

    def paintEvent(self, _evt) -> None:  # noqa: N802 — Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        rect = QRectF(0, 0, self.width(), self.height())
        # Mixed-corner clip: when embedded in an event tile we want a
        # square top edge (meets the title row) + bottom corners that
        # match the tile's outer radius. Default round-all keeps the
        # standalone usage unchanged.
        clip = _mixed_rounded_path(
            rect, top=self._top_radius, bottom=self._bottom_radius,
        )
        painter.setClipPath(clip)

        if not self._pixmaps:
            self._paint_placeholder(painter)
            painter.end()
            return

        # HiDPI-aware sizing (Nelson 2026-07-01):
        # ``QPixmap.scaled`` in Qt 6 preserves the source's DPR, so a
        # target ``QSize`` is interpreted as PHYSICAL pixels. On a
        # widget with devicePixelRatio 2 that means passing
        # ``self.size()`` (logical 248×186) yields a pixmap only 124×93
        # logical — half the widget in both dimensions, so the backdrop
        # stops at the tile's midline and the photo shrinks. Scale to
        # ``logical × widget_dpr`` PHYSICAL pixels instead, then stamp
        # the widget's DPR on the result so its device-independent size
        # matches the intended logical extent.
        widget_dpr = max(1.0, float(self.devicePixelRatioF()))

        # Blurred-fill backdrop covering the whole tile.
        tiny = self._backdrop_for(self._index)
        if tiny is not None:
            cover_target = QSize(
                int(self.width() * widget_dpr),
                int(self.height() * widget_dpr),
            )
            cover = tiny.scaled(
                cover_target,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            cover.setDevicePixelRatio(widget_dpr)
            cover_w_logical = cover.width() / widget_dpr
            cover_h_logical = cover.height() / widget_dpr
            bx = int((self.width() - cover_w_logical) // 2)
            by = int((self.height() - cover_h_logical) // 2)
            painter.drawPixmap(bx, by, cover)

        # Contained photo — full, uncropped, inset a few px so the blurred
        # backdrop reads as a frame.
        src = self._pixmaps[self._index]
        avail_logical = QSize(
            max(1, self.width() - _PHOTO_INSET * 2),
            max(1, self.height() - _PHOTO_INSET * 2),
        )
        photo_target = QSize(
            int(avail_logical.width() * widget_dpr),
            int(avail_logical.height() * widget_dpr),
        )
        scaled = src.scaled(
            photo_target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(widget_dpr)
        scaled_w_logical = scaled.width() / widget_dpr
        scaled_h_logical = scaled.height() / widget_dpr
        x = int((self.width() - scaled_w_logical) // 2)
        y = int((self.height() - scaled_h_logical) // 2)
        painter.drawPixmap(x, y, scaled)

        # Hairline frame on the contained photo so its edge reads off the
        # darkened backdrop — the same affordance the legacy carousel used.
        # Use LOGICAL widths so the frame aligns with the drawn pixmap on
        # HiDPI displays (see the centering note above).
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 50), 1))
        frame = QRectF(
            x + 0.5, y + 0.5,
            scaled_w_logical - 1.0, scaled_h_logical - 1.0,
        )
        painter.drawRoundedRect(frame, 3.0, 3.0)

        # Caption + corner pills on top.
        if self._caption or self._sub_caption:
            self._paint_caption_strip(painter)
        if self._tag_text:
            self._paint_corner_pill(
                painter, self._tag_text, top=True, left=True,
                bg=QColor(8, 10, 16, 180), fg=QColor(228, 232, 245),
            )
        if self._pill_text:
            self._paint_corner_pill(
                painter, self._pill_text, top=True, left=False,
                bg=QColor(255, 93, 162, 220), fg=QColor(255, 255, 255),
            )
        painter.end()

    def _paint_placeholder(self, painter: QPainter) -> None:
        # Quiet card2-ish fill + faint "no photos" label so the tile never
        # reads as broken. We deliberately don't reach into PALETTE here
        # to keep this widget importable in any context (tests, smokes).
        painter.fillRect(self.rect(), QColor(30, 34, 45))
        painter.setPen(QColor(139, 148, 167))
        painter.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignCenter,
            "No exported photos yet",
        )

    def _paint_caption_strip(self, painter: QPainter) -> None:
        """Translucent-dark band across the bottom carrying the event name
        + optional sub-line. Painted inside the tile so the cycler stays a
        flat box with no layout children."""
        base_h = 26 if not self._sub_caption else 42
        strip = QRectF(
            0, self.height() - base_h, self.width(), base_h
        )
        painter.save()
        painter.setBrush(QColor(8, 10, 16, 150))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(strip)
        # Name line
        f_name = QFont(self.font())
        f_name.setPointSizeF(max(9.5, f_name.pointSizeF()))
        f_name.setWeight(QFont.Weight.DemiBold)
        painter.setFont(f_name)
        painter.setPen(QColor(255, 255, 255))
        text_x = 10
        if self._sub_caption:
            painter.drawText(
                QPointF(text_x, self.height() - base_h + 16), self._caption
            )
            f_sub = QFont(self.font())
            f_sub.setPointSizeF(max(8.5, f_sub.pointSizeF() - 1))
            painter.setFont(f_sub)
            painter.setPen(QColor(200, 206, 220))
            painter.drawText(
                QPointF(text_x, self.height() - base_h + 32),
                self._sub_caption,
            )
        else:
            painter.drawText(
                QPointF(text_x, self.height() - 8), self._caption
            )
        painter.restore()

    def _paint_corner_pill(
        self,
        painter: QPainter,
        text: str,
        *,
        top: bool,
        left: bool,
        bg: QColor,
        fg: QColor,
    ) -> None:
        f = QFont(self.font())
        f.setPointSizeF(max(8.5, f.pointSizeF() - 1.5))
        f.setWeight(QFont.Weight.DemiBold)
        fm = QFontMetrics(f)
        text_w = fm.horizontalAdvance(text)
        pad_x = 8
        pad_y = 3
        w = text_w + pad_x * 2
        h = fm.height() + pad_y * 2
        margin = 8
        if left:
            x = margin
        else:
            x = self.width() - w - margin
        y = margin if top else self.height() - h - margin
        painter.save()
        painter.setBrush(bg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(x, y, w, h), h / 2, h / 2)
        painter.setFont(f)
        painter.setPen(fg)
        baseline = y + h - pad_y - fm.descent()
        painter.drawText(QPointF(x + pad_x, baseline), text)
        painter.restore()
