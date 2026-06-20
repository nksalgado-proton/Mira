"""Carousel — closed-event exported-photo strip with auto-advance.

Used on the redesigned event card's closed variant (Surface 01) to scroll
through a sample of the event's exported photos. Auto-advances every 4
seconds while visible; pauses when the user hovers. Click dots or arrows
to jump.

Simple implementation: QStackedWidget of QLabels + two circular ghost
arrows overlaid + a row of dot QPushButtons below + a QTimer. Pixmaps are
KeepAspectRatio-scaled to the carousel's current size.
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class _Slide(QWidget):
    """One carousel frame: a blurred-fill backdrop so any aspect ratio fills
    the slot without black bars, with the full photo contained on top
    (KeepAspectRatio, never cropped). Same treatment the Days Grid Thumb uses,
    plus the mockup's 14px rounded corners. Repaints itself on resize, so no
    manual rescale is needed."""

    _RADIUS = 14.0

    def __init__(self, pixmap: QPixmap, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = pixmap
        self._tiny: QPixmap | None = None
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

    def _backdrop_src(self) -> QPixmap | None:
        """A tiny darkened copy (cheap blur approximation), size-independent so
        it's computed once and upscaled per paint."""
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
        if self._pixmap is None or self._pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        rect = QRectF(0, 0, self.width(), self.height())
        clip = QPainterPath()
        clip.addRoundedRect(rect, self._RADIUS, self._RADIUS)
        painter.setClipPath(clip)

        # Blurred-fill backdrop covering the whole slot.
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

        # Contained photo — full, uncropped, inset by a margin so it floats on
        # the backdrop and its hairline frame never touches the slot edges.
        pad = 10
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

        # Hairline frame around the photo.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 50), 1))
        frame = QRectF(
            x + 0.5, y + 0.5, scaled.width() - 1.0, scaled.height() - 1.0
        )
        painter.drawRoundedRect(frame, 3.0, 3.0)
        painter.end()


class Carousel(QWidget):
    """Image carousel with prev / next / dots / auto-advance.

    Args:
        pixmaps: list of QPixmap. Empty list -> renders a placeholder.
        interval_ms: auto-advance interval. Pass 0 to disable.
    """

    indexChanged = pyqtSignal(int)

    def __init__(
        self,
        pixmaps: list[QPixmap] | None = None,
        *,
        interval_ms: int = 4000,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._pixmaps: list[QPixmap] = list(pixmaps or [])
        self._index = 0
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.setMinimumHeight(120)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._stack = QStackedWidget(self)
        outer.addWidget(self._stack, 1)
        self._populate()

        # Dots overlay — child of the stack so it floats over the photo at
        # the bottom-center; no vertical layout cost. Positioned in
        # resizeEvent. A translucent dark host pill keeps the dots legible
        # over both bright and dark photo regions.
        self._dots_host = QWidget(self._stack)
        self._dots_host.setStyleSheet(  # pragma: no-qss — translucent overlay over photos
            "background: rgba(8,10,16,0.55); border-radius: 9px;"
        )
        self._dots_host.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, False
        )
        self._dots_row = QHBoxLayout(self._dots_host)
        self._dots_row.setContentsMargins(6, 3, 6, 3)
        self._dots_row.setSpacing(6)
        self._dots: list[QPushButton] = []
        for i in range(len(self._pixmaps)):
            d = QPushButton(self._dots_host)
            d.setObjectName("CarouselDot")
            d.setFixedSize(QSize(8, 8))
            d.setCursor(Qt.CursorShape.PointingHandCursor)
            d.clicked.connect(lambda _=False, idx=i: self.setIndex(idx))
            self._dots_row.addWidget(d)
            self._dots.append(d)
        self._refresh_dot_state()
        self._dots_host.raise_()

        # Prev/next arrows overlaid on the stack
        self._prev = QPushButton("‹", self._stack)
        self._next = QPushButton("›", self._stack)
        for b, sig in ((self._prev, self.previous), (self._next, self.next)):
            b.setObjectName("CarouselArrow")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedSize(QSize(28, 28))
            b.clicked.connect(sig)
            b.raise_()

        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.next)
        if interval_ms > 0 and len(self._pixmaps) > 1:
            self._timer.start()

    def setPixmaps(self, pixmaps: list[QPixmap]) -> None:
        # Clear stack
        while self._stack.count():
            w = self._stack.widget(0)
            self._stack.removeWidget(w)
            w.deleteLater()
        # Clear dots
        while self._dots:
            d = self._dots.pop()
            self._dots_row.removeWidget(d)
            d.deleteLater()
        self._pixmaps = list(pixmaps)
        self._index = 0
        self._populate()
        for i in range(len(self._pixmaps)):
            d = QPushButton(self._dots_host)
            d.setObjectName("CarouselDot")
            d.setFixedSize(QSize(8, 8))
            d.setCursor(Qt.CursorShape.PointingHandCursor)
            d.clicked.connect(lambda _=False, idx=i: self.setIndex(idx))
            self._dots_row.addWidget(d)
            self._dots.append(d)
        self._refresh_dot_state()

    def _populate(self) -> None:
        if not self._pixmaps:
            ph = QLabel("No exported photos yet")
            ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ph.setObjectName("Faint")
            self._stack.addWidget(ph)
            return
        for pm in self._pixmaps:
            self._stack.addWidget(_Slide(pm))

    def setIndex(self, i: int) -> None:
        if not self._pixmaps:
            return
        self._index = i % len(self._pixmaps)
        self._stack.setCurrentIndex(self._index)
        self._refresh_dot_state()
        self._rescale_current()
        self.indexChanged.emit(self._index)

    def _rescale_current(self) -> None:
        """Rescale the visible label's pixmap to the stack's current size,
        KeepAspectRatio. Called on resize + on every setIndex so advancing
        always paints at the right size."""
        if not self._pixmaps:
            return
        cur = self._stack.currentWidget()
        if not isinstance(cur, QLabel):
            return
        src = self._pixmaps[self._index]
        scaled = src.scaled(
            self._stack.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        cur.setPixmap(scaled)

    def previous(self) -> None:
        self.setIndex(self._index - 1)

    def next(self) -> None:
        self.setIndex(self._index + 1)

    def _refresh_dot_state(self) -> None:
        for i, d in enumerate(self._dots):
            d.setProperty("active", i == self._index)
            d.style().unpolish(d)
            d.style().polish(d)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        h = self._stack.height()
        w = self._stack.width()
        self._prev.move(8, max(8, (h - self._prev.height()) // 2))
        self._next.move(
            w - self._next.width() - 8,
            max(8, (h - self._next.height()) // 2),
        )
        # Position the dots pill at the bottom-center, 10px from the
        # bottom edge of the stack. Width is intrinsic to the dot row.
        if self._dots:
            self._dots_host.adjustSize()
            dx = (w - self._dots_host.width()) // 2
            dy = h - self._dots_host.height() - 10
            self._dots_host.move(max(0, dx), max(0, dy))
            self._dots_host.raise_()
        self._rescale_current()

    def enterEvent(self, e):  # noqa: N802
        if self._timer.interval() > 0:
            self._timer.stop()
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        if self._timer.interval() > 0 and len(self._pixmaps) > 1:
            self._timer.start()
        super().leaveEvent(e)
