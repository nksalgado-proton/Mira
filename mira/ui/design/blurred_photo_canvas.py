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
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QSizePolicy, QWidget

from mira.ui.design.blurred_backdrop import blurred_cover, blurred_tiny


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
        """The cached 48×48 darkened tiny — the shared
        :func:`mira.ui.design.blurred_backdrop.blurred_tiny` recipe so
        every backdrop in the app stays visually identical."""
        if self._tiny is not None:
            return self._tiny
        self._tiny = blurred_tiny(self._pixmap)
        return self._tiny

    def paintEvent(self, _evt) -> None:  # noqa: N802 — Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        rect = QRectF(0, 0, self.width(), self.height())
        clip = QPainterPath()
        clip.addRoundedRect(rect, self._radius, self._radius)
        painter.setClipPath(clip)

        cover = blurred_cover(self._backdrop_src(), self.size())
        if cover is not None:
            bx = (self.width() - cover.width()) // 2
            by = (self.height() - cover.height()) // 2
            painter.drawPixmap(bx, by, cover)
        else:
            painter.fillRect(rect, self.EMPTY_FILL)

        if self._pixmap is None or self._pixmap.isNull():
            painter.end()
            return

        # spec/152 §X — DPR-aware foreground scale. The pre-fix path
        # called ``self._pixmap.scaled(avail_logical, …)`` which Qt6
        # treats as DEVICE pixels and discards the source DPR; on a
        # HiDPI screen (DPR > 1) the result rendered at half logical
        # size and then upscaled to fill ``avail``, which the user
        # read as "the in-line PTE slideshow is sharper than our
        # Play". We now scale to (avail_logical × screen_DPR) device
        # pixels and stamp the DPR back so ``drawPixmap`` lays the
        # result down at full screen resolution.
        pad = self._inner_pad
        avail = QSize(
            max(1, self.width() - pad * 2),
            max(1, self.height() - pad * 2),
        )
        dpr = self.devicePixelRatioF() or 1.0
        device_target = QSize(
            max(1, int(round(avail.width() * dpr))),
            max(1, int(round(avail.height() * dpr))),
        )
        scaled = self._pixmap.scaled(
            device_target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(dpr)
        # ``scaled.width()`` is now in DEVICE pixels; convert back to
        # logical when laying it down so it occupies the right area.
        logical_w = scaled.width() / dpr
        logical_h = scaled.height() / dpr
        x = int((self.width() - logical_w) // 2)
        y = int((self.height() - logical_h) // 2)
        painter.drawPixmap(x, y, scaled)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(self.FRAME_COLOR, 1))
        frame = QRectF(
            x + 0.5, y + 0.5,
            logical_w - 1.0, logical_h - 1.0,
        )
        painter.drawRoundedRect(frame, 3.0, 3.0)
        painter.end()
