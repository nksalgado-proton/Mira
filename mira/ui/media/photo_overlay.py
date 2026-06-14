"""A small translucent overlay pill anchored to the bottom of a photo display widget.

Used for the single-view **exposure overlay** (Nelson 2026-06-01: the exposure must read
*on* the picture, like the grid tiles — not in a top info line). Parent it to the photo
label (``MediaCanvas.photo_area_widget()`` for the full culler, the ``_PhotoCanvas`` for the
Quick Sweep); it repositions itself whenever that label resizes. Click-through. Reuses the
``GridTileExif`` QSS role so the single-view pill matches the grid pill.
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import QLabel, QWidget


class PhotoExposureOverlay(QLabel):
    """A bottom-centred, translucent rich-text pill over a photo. ``set_html("")`` hides it."""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setObjectName("GridTileExif")
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._host = host
        host.installEventFilter(self)
        self.hide()

    def set_html(self, html: str) -> None:
        if html:
            self.setText(html)
            self.show()
            self.reposition()
        else:
            self.clear()
            self.hide()

    def reposition(self) -> None:
        if not self.text() or not self.isVisible():
            return
        hint = self.sizeHint()
        w = min(self._host.width(), hint.width() + 16)
        h = hint.height()
        x = (self._host.width() - w) // 2
        y = max(0, self._host.height() - h - 8)
        self.setGeometry(int(x), int(y), int(max(1, w)), int(max(1, h)))
        self.raise_()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self._host and event.type() == QEvent.Type.Resize:
            self.reposition()
        return False
