"""A small translucent overlay pill anchored to the bottom of a photo display widget.

Used for the single-view **exposure overlay** (Nelson 2026-06-01: the exposure must read
*on* the picture, like the grid tiles — not in a top info line). Parent it to the photo
label (``MediaCanvas.photo_area_widget()`` for the full culler, the ``_PhotoCanvas`` for the
Quick Sweep); it repositions itself whenever that label resizes. Click-through. Reuses the
``GridTileExif`` QSS role so the single-view pill matches the grid pill.

When constructed with a ``rect_provider`` (a callable returning the displayed image's
letterboxed rect inside the host, e.g. ``PhotoViewport.image_rect_in_photo_area``), the
pill anchors to the **bottom edge of the photo itself** rather than the full host — so it
never floats over the letterbox bars when the photo's aspect ratio doesn't fill the view.
The host must re-call :meth:`reposition` whenever that rect moves (the
``photo_geometry_changed`` pulse).
"""
from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import QEvent, QRect, Qt
from PyQt6.QtWidgets import QLabel, QWidget


class PhotoExposureOverlay(QLabel):
    """A bottom-centred, translucent rich-text pill over a photo. ``set_html("")`` hides it.

    ``rect_provider`` (optional): a callable returning the displayed image's rect in the
    host's coordinates. When given, the pill anchors to the bottom-centre of THAT rect (the
    photo), not the full host (the view). When ``None`` it anchors to the host rect (the
    legacy behaviour — Quick Sweep)."""

    #: Vertical gap (px) between the pill and the anchor's bottom edge.
    _BOTTOM_MARGIN = 6

    def __init__(
        self,
        host: QWidget,
        *,
        rect_provider: Optional[Callable[[], QRect]] = None,
    ) -> None:
        super().__init__(host)
        self.setObjectName("GridTileExif")
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._host = host
        self._rect_provider = rect_provider
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

    def _anchor_rect(self) -> QRect:
        """The rect to anchor within — the displayed photo when a
        ``rect_provider`` is set and yields a non-empty rect, else the
        whole host."""
        if self._rect_provider is not None:
            area = self._rect_provider()
            if area is not None and not area.isEmpty():
                return area
        return self._host.rect()

    def reposition(self) -> None:
        if not self.text() or not self.isVisible():
            return
        area = self._anchor_rect()
        hint = self.sizeHint()
        # Cap width at the host so a long single line stays fully visible
        # (it may overhang a very narrow portrait photo — better than
        # clipping the text); centre it on the photo and sit it just above
        # the photo's bottom edge.
        w = min(self._host.width(), hint.width() + 16)
        h = hint.height()
        cx = area.x() + area.width() // 2
        x = cx - w // 2
        x = max(0, min(x, self._host.width() - w))
        y = max(0, area.y() + area.height() - h - self._BOTTOM_MARGIN)
        self.setGeometry(int(x), int(y), int(max(1, w)), int(max(1, h)))
        self.raise_()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self._host and event.type() == QEvent.Type.Resize:
            self.reposition()
        return False
