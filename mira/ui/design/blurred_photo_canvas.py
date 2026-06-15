"""``BlurredPhotoCanvas`` — Thumb-style photo presentation widget.

Same visual treatment as :class:`mira.ui.design.thumbs.Thumb` and the
carousel's ``_Slide``: a low-resolution blurred copy of the pixmap fills
the whole slot, the photo paints KeepAspectRatio centred with a small
inner padding, and a hairline frame outlines the visible photo. The
point is twofold:

* no "black strip" letterboxing when the photo's aspect ratio doesn't
  match the slot's (the blurred backdrop fills the gap with a darkened
  version of the same image);
* a soft visible frame so the photo feels contained even on neutral
  backgrounds.

Drop-in replacement for a ``QLabel`` whose only job was to display a
pixmap: same ``setPixmap``/``pixmap`` API, no QSS rule needed. Used by
the Cut detail grid cells (DayGridCell ``photo_canvas_mode='blurred'``)
and the Cut play rehearsal (CutPlayerDialog).
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import (
    QColor,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QSizePolicy, QWidget


class BlurredPhotoCanvas(QWidget):
    """Custom-painted photo canvas with a blurred backdrop + framed photo."""

    #: Default padding (px) between the photo and the slot edge.
    DEFAULT_INNER_PAD = 8
    #: Default radius (px) of the slot's outer clip and the photo's frame.
    DEFAULT_RADIUS = 6.0
    #: Hairline frame around the visible photo — visible but unobtrusive.
    FRAME_COLOR = QColor(255, 255, 255, 96)
    #: Fallback fill when no pixmap has been set yet — a neutral dark
    #: tone, deliberately darker than the typical photo backdrop so the
    #: placeholder reads as "loading" rather than "broken".
    EMPTY_FILL = QColor(20, 22, 30)

    def __init__(
        self,
        pixmap: QPixmap | None = None,
        *,
        inner_pad: int | None = None,
        radius: float | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._pixmap = pixmap
        self._tiny: QPixmap | None = None
        self._inner_pad = (
            self.DEFAULT_INNER_PAD if inner_pad is None
            else max(0, int(inner_pad))
        )
        self._radius = (
            self.DEFAULT_RADIUS if radius is None
            else float(radius)
        )
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

    # ── public pixmap API (drop-in for QLabel) ──────────────────────────

    def setPixmap(self, pixmap: QPixmap | None) -> None:  # noqa: N802
        self._pixmap = pixmap
        self._tiny = None
        self.update()

    def pixmap(self) -> QPixmap | None:
        return self._pixmap

    def setInnerPad(self, pad: int) -> None:  # noqa: N802
        self._inner_pad = max(0, int(pad))
        self.update()

    # ── paint ──────────────────────────────────────────────────────────

    def _backdrop_src(self) -> QPixmap | None:
        if self._pixmap is None or self._pixmap.isNull():
            return None
        if self._tiny is not None:
            return self._tiny
        small = self._pixmap.scaled(
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
        self._tiny = QPixmap.fromImage(img)
        return self._tiny

    def paintEvent(self, _evt) -> None:  # noqa: N802 — Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        rect = QRectF(0, 0, self.width(), self.height())
        clip = QPainterPath()
        clip.addRoundedRect(rect, self._radius, self._radius)
        painter.setClipPath(clip)

        tiny = self._backdrop_src()
        if tiny is not None:
            cover = tiny.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            bx = (self.width() - cover.width()) // 2
            by = (self.height() - cover.height()) // 2
            painter.drawPixmap(bx, by, cover)
        else:
            painter.fillRect(rect, self.EMPTY_FILL)

        if self._pixmap is None or self._pixmap.isNull():
            painter.end()
            return

        pad = self._inner_pad
        avail = QSize(
            max(1, self.width() - pad * 2),
            max(1, self.height() - pad * 2),
        )
        scaled = self._pixmap.scaled(
            avail,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(self.FRAME_COLOR, 1))
        frame = QRectF(
            x + 0.5, y + 0.5,
            scaled.width() - 1.0, scaled.height() - 1.0,
        )
        painter.drawRoundedRect(frame, 3.0, 3.0)
        painter.end()
