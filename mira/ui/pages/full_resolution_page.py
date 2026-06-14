"""Surface 10 — Full Resolution viewer (F10).

Near-chromeless 1:1 pannable photo viewer. Reached from the Picker /
Editor via F10. Same MediaNav (arrows + Filmstrip) as the other media
surfaces so navigation feels consistent.

Composition (design-system §Surface 10):
    Pannable viewer:    QScrollArea over the QPixmap at 100% by default.
                        Drag to pan; wheel to zoom (Ctrl+wheel for finer).
                        Photo keeps the LOCKED §5a state border (thin
                        outline since the chrome is intentionally minimal).
    Floating top bar:   translucent dark pill centered above the viewer:
                        ✕ close · filename + EXIF · state chip ·
                        zoom stepper · Fit / 1:1 toggle.
    Bottom MediaNav:    floating ‹ / › arrows over the viewer + Filmstrip
                        of neighbours at the bottom.

Live image loading + per-item EXIF wiring lands in the route-swap commit
alongside the Picker/Editor integration.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mira.ui.design import (
    Filmstrip,
    chip_closed,
    chip_done,
    chip_idle,
    ghost_button,
    nav_arrow,
)

log = logging.getLogger(__name__)


@dataclass
class FullResItem:
    item_id: str
    pixmap: QPixmap | None = None
    filename: str = ""
    exif_text: str = ""
    state: str | None = None


class _PannableImage(QScrollArea):
    """QScrollArea hosting a QLabel pixmap. Wheel = zoom step. Drag = pan.

    Maintains a logical zoom factor between FIT_MIN and 4.0 and rescales
    the pixmap on every change. Drag-pan handled via standard QScrollArea
    handDrag mode.
    """

    FIT = 0.0    # sentinel
    ONE_TO_ONE = 1.0
    MIN = 0.05
    MAX = 4.0

    zoom_changed = pyqtSignal(float)

    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWidgetResizable(False)
        self.setStyleSheet("QScrollArea { background: #07080c; border: none; }")
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("background: transparent;")
        self.setWidget(self._label)
        self._src: QPixmap | None = None
        self._zoom: float = 1.0
        self._mode = "one_to_one"

    def setPixmap(self, pm: QPixmap | None) -> None:
        self._src = pm
        if self._mode == "fit":
            self._apply_fit()
        else:
            self._apply_one_to_one()

    def setMode(self, mode: str) -> None:
        if mode not in ("fit", "one_to_one"):
            return
        self._mode = mode
        if mode == "fit":
            self._apply_fit()
        else:
            self._apply_one_to_one()

    def setZoom(self, z: float) -> None:
        self._zoom = max(self.MIN, min(self.MAX, float(z)))
        self._mode = "custom"
        self._apply_scaled()
        self.zoom_changed.emit(self._zoom)

    def zoom(self) -> float:
        return self._zoom

    def _apply_fit(self) -> None:
        if self._src is None or self._src.isNull():
            return
        avail = self.viewport().size()
        scaled = self._src.scaled(
            avail.width() - 8, avail.height() - 8,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._zoom = scaled.width() / max(1, self._src.width())
        self._label.setPixmap(scaled)
        self._label.adjustSize()
        self.zoom_changed.emit(self._zoom)

    def _apply_one_to_one(self) -> None:
        if self._src is None or self._src.isNull():
            return
        self._zoom = 1.0
        self._label.setPixmap(self._src)
        self._label.adjustSize()
        self.zoom_changed.emit(self._zoom)

    def _apply_scaled(self) -> None:
        if self._src is None or self._src.isNull():
            return
        new_w = max(1, int(self._src.width() * self._zoom))
        new_h = max(1, int(self._src.height() * self._zoom))
        scaled = self._src.scaled(
            new_w, new_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._label.setPixmap(scaled)
        self._label.adjustSize()

    def wheelEvent(self, e):  # noqa: N802
        step = 1.10 if e.angleDelta().y() > 0 else (1 / 1.10)
        self.setZoom(self._zoom * step)


def _state_chip(state: str | None) -> QLabel:
    if state == "picked":
        return chip_done("Picked")
    if state == "skipped":
        return chip_closed("Skipped")
    return chip_idle("Neutral")


class _FloatingTopBar(QFrame):
    """Translucent dark pill centered above the viewer."""

    close_clicked = pyqtSignal()
    fit_clicked = pyqtSignal()
    one_clicked = pyqtSignal()
    zoom_in_clicked = pyqtSignal()
    zoom_out_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FullResTopBar")
        self.setStyleSheet(
            "QFrame#FullResTopBar {"
            " background: rgba(8,10,16,0.74);"
            " border: 1px solid rgba(255,255,255,0.18);"
            " border-radius: 14px;"
            "}"
        )
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(10)

        close_btn = ghost_button("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self.close_clicked.emit)
        h.addWidget(close_btn)

        self._info = QLabel("")
        self._info.setStyleSheet("color: #ffffff;")
        h.addWidget(self._info)

        self._state_slot = QHBoxLayout()
        h.addLayout(self._state_slot)

        h.addSpacing(8)

        zoom_out = ghost_button("−")
        zoom_out.setFixedSize(28, 28)
        zoom_out.clicked.connect(self.zoom_out_clicked.emit)
        h.addWidget(zoom_out)
        self._zoom_readout = QLabel("100%")
        self._zoom_readout.setStyleSheet("color: #ffffff; min-width: 48px;")
        self._zoom_readout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.addWidget(self._zoom_readout)
        zoom_in = ghost_button("+")
        zoom_in.setFixedSize(28, 28)
        zoom_in.clicked.connect(self.zoom_in_clicked.emit)
        h.addWidget(zoom_in)
        h.addSpacing(8)
        fit_btn = ghost_button("Fit")
        fit_btn.clicked.connect(self.fit_clicked.emit)
        h.addWidget(fit_btn)
        one_btn = ghost_button("1:1")
        one_btn.clicked.connect(self.one_clicked.emit)
        h.addWidget(one_btn)

    def setItem(self, item: FullResItem) -> None:
        bits = []
        if item.filename:
            bits.append(item.filename)
        if item.exif_text:
            bits.append(item.exif_text)
        self._info.setText("   ·   ".join(bits))
        while self._state_slot.count():
            w = self._state_slot.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self._state_slot.addWidget(_state_chip(item.state))

    def setZoom(self, z: float) -> None:
        self._zoom_readout.setText(f"{int(round(z * 100))}%")


class FullResolutionPage(QWidget):
    """Surface 10 — full-resolution pannable viewer."""

    close_requested = pyqtSignal()
    prev_requested = pyqtSignal()
    next_requested = pyqtSignal()
    index_changed = pyqtSignal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._items: list[FullResItem] = []
        self._index = 0
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Stage container — pannable image + overlay top bar + nav arrows
        self._stage_host = QWidget()
        self._stage_host.setStyleSheet("background: #07080c;")
        self._viewer = _PannableImage()
        self._viewer.zoom_changed.connect(self._on_zoom_changed)
        viewer_wrap = QVBoxLayout(self._stage_host)
        viewer_wrap.setContentsMargins(0, 0, 0, 0)
        viewer_wrap.addWidget(self._viewer)

        # Floating top bar overlay
        self._top_bar = _FloatingTopBar(self._stage_host)
        self._top_bar.close_clicked.connect(self.close_requested.emit)
        self._top_bar.fit_clicked.connect(
            lambda: self._viewer.setMode("fit")
        )
        self._top_bar.one_clicked.connect(
            lambda: self._viewer.setMode("one_to_one")
        )
        self._top_bar.zoom_in_clicked.connect(
            lambda: self._viewer.setZoom(self._viewer.zoom() * 1.1)
        )
        self._top_bar.zoom_out_clicked.connect(
            lambda: self._viewer.setZoom(self._viewer.zoom() / 1.1)
        )

        # Floating prev/next arrows over the viewer
        self._prev_btn = nav_arrow("left", self._stage_host)
        self._prev_btn.clicked.connect(self.prev_requested.emit)
        self._next_btn = nav_arrow("right", self._stage_host)
        self._next_btn.clicked.connect(self.next_requested.emit)

        self._stage_host.installEventFilter(self)
        outer.addWidget(self._stage_host, 1)

        # Filmstrip bottom
        self._filmstrip = Filmstrip()
        self._filmstrip.thumbClicked.connect(self._on_filmstrip_jump)
        outer.addWidget(self._filmstrip)

    def eventFilter(self, obj, e):
        if obj is self._stage_host and e.type() == e.Type.Resize:
            self._position_overlays()
        return super().eventFilter(obj, e)

    def _position_overlays(self) -> None:
        if self._top_bar is None:
            return
        self._top_bar.adjustSize()
        w = self._stage_host.width()
        h = self._stage_host.height()
        self._top_bar.move(
            (w - self._top_bar.width()) // 2, 16,
        )
        cy = (h - self._prev_btn.height()) // 2
        self._prev_btn.move(16, cy)
        self._next_btn.move(w - self._next_btn.width() - 16, cy)
        self._top_bar.raise_()
        self._prev_btn.raise_()
        self._next_btn.raise_()

    # ── data API ────────────────────────────────────────────────────────

    def setItemsForPreview(
        self,
        items: list[FullResItem],
        *,
        start_index: int = 0,
    ) -> None:
        self._items = list(items)
        self._index = max(0, min(start_index, len(items) - 1)) if items else 0
        self._refresh()

    # ── handlers ────────────────────────────────────────────────────────

    def _on_filmstrip_jump(self, i: int) -> None:
        if 0 <= i < len(self._items):
            self._index = i
            self.index_changed.emit(i)
            self._refresh()

    def _on_zoom_changed(self, z: float) -> None:
        self._top_bar.setZoom(z)

    def _refresh(self) -> None:
        if not self._items:
            return
        cur = self._items[self._index]
        self._viewer.setPixmap(cur.pixmap)
        self._top_bar.setItem(cur)
        items = [(it.pixmap, it.state) for it in self._items]
        self._filmstrip.setItems(items, current_index=self._index)
        self._position_overlays()
        self._prev_btn.setVisible(self._index > 0)
        self._next_btn.setVisible(self._index < len(self._items) - 1)

    # ── keyboard ───────────────────────────────────────────────────────

    def keyPressEvent(self, e):  # noqa: N802
        key = e.key()
        if key in (Qt.Key.Key_Left,):
            if self._index > 0:
                self._index -= 1
                self.index_changed.emit(self._index)
                self._refresh()
            return
        if key in (Qt.Key.Key_Right,):
            if self._index < len(self._items) - 1:
                self._index += 1
                self.index_changed.emit(self._index)
                self._refresh()
            return
        if key in (Qt.Key.Key_F10, Qt.Key.Key_Escape):
            self.close_requested.emit()
            return
        super().keyPressEvent(e)
