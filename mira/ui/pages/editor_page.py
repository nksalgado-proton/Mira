"""Surface 08 — Editor (look / filter / crop / export).

Same Stable media stage shape as Surface 07 (Picker) so stepping
photo↔video keeps the canvas anchored. Adds a horizontal Controls panel
above the stage (look presets / strength / style / filter / crop aspect +
rotate) and replaces the Pick/Skip/Compare action cluster with Reset all
/ Export all.

Composition (design-system §Surface 08):
    Toolbar:        ghost Back · '2 / 24' counter · spacer · ghost ↺ Reset
                    all · primary ↑ Export all.
    Controls panel: grouped Card2 chips, horizontal layout (wraps on
                    narrow widths):
                      - Look: segmented presets (Original / Natural /
                        Brighten / Deeper / Grid)
                      - Strength: slider 0–1 with live readout
                      - Style: select
                      - Filter: select
                      - Crop: aspect select + rotate-left/right ghost
                        icon buttons.
    Stage:          EditorStage — blurred backdrop, contained photo,
                    LOCKED §5a state border, rule-of-thirds crop
                    overlay with dimmed surround + corner handles,
                    visited eye + exported badge overlays.
    Reserved zone:  StableMediaStage's control zone; empty for photos,
                    holds the timeline + tools + transport rows for
                    videos (Surface 12).
    Bottom bar:     Filmstrip (left) · Full screen F11 · Full resolution
                    F10 (right).

Live image-pipeline wiring (look presets / strength / filter / crop
materialisation / export to disk) lands in the route-swap commit
alongside the existing core/adjustment_pipeline.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from mira.gateway import Gateway
from mira.ui.design import (
    Filmstrip,
    StableMediaStage,
    ghost_button,
    line_input,
    nav_arrow,
    pill_toggle,
    primary_button,
    select,
)
from mira.ui.palette import PALETTE

log = logging.getLogger(__name__)


@dataclass
class EditorItem:
    """One item on the editor's filmstrip."""

    item_id: str
    pixmap: QPixmap | None = None
    state: str | None = None
    visited: bool = False
    exported: bool = False


_LOOK_PRESETS = ("Original", "Natural", "Brighten", "Deeper", "Grid")
_CROP_ASPECTS = ("Free", "3:2", "4:3", "1:1", "16:9")


class EditorStage(QWidget):
    """Custom-painted editor canvas — blurred backdrop, contained photo,
    state border, rule-of-thirds crop overlay, overlays."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._state: str | None = None
        self._visited = False
        self._exported = False
        self._crop_aspect = "Free"
        self._blurred_cache: QPixmap | None = None
        self._cache_key: tuple = ()
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.setMinimumHeight(320)

        # Overlay children (visited eye, exported badge, nav arrows)
        self._eye = QLabel("◉", self)
        self._eye.setStyleSheet(
            "background: rgba(8,10,16,0.74); color: #ffffff;"
            " border: 1px solid rgba(255,255,255,0.18); border-radius: 10px;"
            " padding: 4px 8px;"
        )
        self._eye.hide()
        self._exported_chip = QLabel("↑ Exported", self)
        self._exported_chip.setStyleSheet(
            "background: #7c6cff; color: #ffffff; border: none;"
            " border-radius: 10px; padding: 4px 10px; font-weight: 700;"
        )
        self._exported_chip.hide()
        self._prev_btn = nav_arrow("left", self)
        self._next_btn = nav_arrow("right", self)

    # ── public ─────────────────────────────────────────────────────────

    def setItem(self, item: EditorItem) -> None:
        self._pixmap = item.pixmap
        self._state = item.state
        self._visited = item.visited
        self._exported = item.exported
        self._blurred_cache = None
        self._eye.setVisible(self._visited)
        self._exported_chip.setVisible(self._exported)
        self._eye.adjustSize()
        self._exported_chip.adjustSize()
        self._position_overlays()
        self.update()

    def setCropAspect(self, aspect: str) -> None:
        self._crop_aspect = aspect
        self.update()

    @property
    def prev_button(self) -> QPushButton:
        return self._prev_btn

    @property
    def next_button(self) -> QPushButton:
        return self._next_btn

    # ── paint ──────────────────────────────────────────────────────────

    def paintEvent(self, _evt) -> None:  # noqa: N802
        app = QApplication.instance()
        mode = (app.property("theme") if app else None) or "dark"
        palette = PALETTE[mode]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        painter.fillRect(rect, QColor(palette["bg"]))

        if self._pixmap is None or self._pixmap.isNull():
            painter.end()
            return

        backdrop = self._build_backdrop()
        if backdrop is not None:
            painter.drawPixmap(rect, backdrop, backdrop.rect())

        pad = 24
        avail = rect.adjusted(pad, pad, -pad, -pad)
        scaled = self._pixmap.scaled(
            avail.width(), avail.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = avail.x() + (avail.width() - scaled.width()) // 2
        y = avail.y() + (avail.height() - scaled.height()) // 2
        photo_rect = QRectF(x, y, scaled.width(), scaled.height())

        radius = 12.0
        path = QPainterPath()
        path.addRoundedRect(photo_rect, radius, radius)
        painter.save()
        painter.setClipPath(path)
        painter.drawPixmap(int(x), int(y), scaled)

        # Crop overlay — rule-of-thirds inside the photo rect
        crop_rect = self._compute_crop_rect(photo_rect)
        # Dim outside the crop
        dim = QColor(0, 0, 0, 110)
        painter.fillRect(
            QRectF(photo_rect.x(), photo_rect.y(),
                   crop_rect.x() - photo_rect.x(), photo_rect.height()),
            dim,
        )
        painter.fillRect(
            QRectF(crop_rect.right(), photo_rect.y(),
                   photo_rect.right() - crop_rect.right(),
                   photo_rect.height()),
            dim,
        )
        painter.fillRect(
            QRectF(crop_rect.x(), photo_rect.y(), crop_rect.width(),
                   crop_rect.y() - photo_rect.y()),
            dim,
        )
        painter.fillRect(
            QRectF(crop_rect.x(), crop_rect.bottom(),
                   crop_rect.width(),
                   photo_rect.bottom() - crop_rect.bottom()),
            dim,
        )
        # Thirds grid
        pen = QPen(QColor(255, 255, 255, 100), 1)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for i in (1, 2):
            x_t = crop_rect.x() + crop_rect.width() * i / 3
            y_t = crop_rect.y() + crop_rect.height() * i / 3
            painter.drawLine(
                int(x_t), int(crop_rect.y()),
                int(x_t), int(crop_rect.bottom()),
            )
            painter.drawLine(
                int(crop_rect.x()), int(y_t),
                int(crop_rect.right()), int(y_t),
            )
        # Crop frame + corner handles
        pen = QPen(QColor(255, 255, 255, 220), 2)
        painter.setPen(pen)
        painter.drawRect(crop_rect)
        handle_size = 10
        handle_color = QColor(palette["accent"])
        painter.setBrush(handle_color)
        painter.setPen(Qt.PenStyle.NoPen)
        for (cx, cy) in (
            (crop_rect.x(), crop_rect.y()),
            (crop_rect.right(), crop_rect.y()),
            (crop_rect.x(), crop_rect.bottom()),
            (crop_rect.right(), crop_rect.bottom()),
        ):
            painter.drawRect(
                QRectF(cx - handle_size / 2, cy - handle_size / 2,
                       handle_size, handle_size)
            )
        painter.restore()

        # State border on top of the photo rect
        border_color = QColor(
            palette["picked"] if self._state == "picked"
            else palette["skipped"] if self._state == "skipped"
            else palette["compare"] if self._state == "compare"
            else palette["line"]
        )
        pen = QPen(border_color, 3)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen)
        painter.drawRoundedRect(
            photo_rect.adjusted(1.5, 1.5, -1.5, -1.5),
            radius - 1.5, radius - 1.5,
        )
        painter.end()

    def _build_backdrop(self) -> QPixmap | None:
        if self._pixmap is None or self._pixmap.isNull():
            return None
        key = (self._pixmap.cacheKey(), self.width(), self.height())
        if self._blurred_cache is not None and self._cache_key == key:
            return self._blurred_cache
        small = self._pixmap.scaled(
            36, 36,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        big = small.scaled(
            self.width(), self.height(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        img = big.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        p = QPainter(img)
        p.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceAtop
        )
        p.fillRect(img.rect(), QColor(0, 0, 0, 150))
        p.end()
        self._blurred_cache = QPixmap.fromImage(img)
        self._cache_key = key
        return self._blurred_cache

    def _compute_crop_rect(self, photo_rect: QRectF) -> QRectF:
        """Center crop rect inside the photo rect honouring the aspect
        select. 'Free' uses 92% of the photo. Other aspects center an
        inscribed rect of the right ratio."""
        if self._crop_aspect == "Free":
            margin_w = photo_rect.width() * 0.04
            margin_h = photo_rect.height() * 0.04
            return photo_rect.adjusted(margin_w, margin_h, -margin_w, -margin_h)
        try:
            num, den = self._crop_aspect.split(":")
            ratio = float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            return photo_rect
        photo_ratio = photo_rect.width() / max(1.0, photo_rect.height())
        if photo_ratio > ratio:
            # Photo wider than target — crop horizontally
            crop_h = photo_rect.height() * 0.92
            crop_w = crop_h * ratio
        else:
            crop_w = photo_rect.width() * 0.92
            crop_h = crop_w / ratio
        cx = photo_rect.center().x()
        cy = photo_rect.center().y()
        return QRectF(cx - crop_w / 2, cy - crop_h / 2, crop_w, crop_h)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._blurred_cache = None
        self._position_overlays()

    def _position_overlays(self) -> None:
        pad = 24
        self._eye.move(
            self.width() - self._eye.width() - pad - 8, pad + 8,
        )
        self._exported_chip.move(
            pad + 8,
            self.height() - self._exported_chip.height() - pad - 8,
        )
        cy = (self.height() - self._prev_btn.height()) // 2
        self._prev_btn.move(pad + 8, cy)
        self._next_btn.move(
            self.width() - self._next_btn.width() - pad - 8, cy
        )


# ── Controls panel helpers ────────────────────────────────────────────


def _controls_group(label: str, *widgets: QWidget) -> QFrame:
    """Titled Card2 group hosting a row of widgets."""
    box = QFrame()
    box.setObjectName("Card2")
    v = QVBoxLayout(box)
    v.setContentsMargins(12, 8, 12, 10)
    v.setSpacing(6)
    micro = QLabel(label.upper())
    micro.setObjectName("Micro")
    v.addWidget(micro)
    row = QHBoxLayout()
    row.setSpacing(6)
    for w in widgets:
        row.addWidget(w)
    v.addLayout(row)
    return box


class EditorPage(QWidget):
    """Surface 08 — editor page."""

    back_requested = pyqtSignal()
    reset_all_requested = pyqtSignal()
    export_all_requested = pyqtSignal()
    prev_requested = pyqtSignal()
    next_requested = pyqtSignal()
    fullscreen_toggled = pyqtSignal()
    full_resolution_requested = pyqtSignal()
    index_changed = pyqtSignal(int)
    look_changed = pyqtSignal(str)
    strength_changed = pyqtSignal(int)
    style_changed = pyqtSignal(str)
    filter_changed = pyqtSignal(str)
    crop_aspect_changed = pyqtSignal(str)
    rotate_requested = pyqtSignal(int)   # ±90

    def __init__(
        self,
        gateway: Optional[Gateway] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.gateway = gateway
        self._items: list[EditorItem] = []
        self._index = 0
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(10)

        # ── Toolbar ──
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        self._back = ghost_button("‹ Back")
        self._back.clicked.connect(self.back_requested.emit)
        toolbar.addWidget(self._back)
        self._counter = QLabel("0 / 0")
        self._counter.setObjectName("Sub")
        toolbar.addWidget(self._counter)
        toolbar.addStretch()
        reset_btn = ghost_button("↺ Reset all")
        reset_btn.clicked.connect(self.reset_all_requested.emit)
        toolbar.addWidget(reset_btn)
        export_btn = primary_button("↑ Export all")
        export_btn.clicked.connect(self.export_all_requested.emit)
        toolbar.addWidget(export_btn)
        outer.addLayout(toolbar)

        # ── Controls panel ──
        controls = QHBoxLayout()
        controls.setSpacing(10)
        # Look segmented
        look_group = QFrame()
        look_group.setObjectName("Card2")
        lg = QVBoxLayout(look_group)
        lg.setContentsMargins(12, 8, 12, 10)
        lg.setSpacing(6)
        lg.addWidget(self._micro("Look"))
        look_row = QHBoxLayout()
        look_row.setSpacing(4)
        self._look_buttons = QButtonGroup(self)
        for name in _LOOK_PRESETS:
            btn = pill_toggle(name, checked=(name == "Natural"))
            btn.clicked.connect(
                lambda _c=False, n=name: self.look_changed.emit(n)
            )
            self._look_buttons.addButton(btn)
            look_row.addWidget(btn)
        lg.addLayout(look_row)
        controls.addWidget(look_group)
        # Strength
        s_group = QFrame()
        s_group.setObjectName("Card2")
        sg = QVBoxLayout(s_group)
        sg.setContentsMargins(12, 8, 12, 10)
        sg.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(self._micro("Strength"))
        head.addStretch()
        self._strength_readout = QLabel("1.00")
        self._strength_readout.setObjectName("Sub")
        head.addWidget(self._strength_readout)
        sg.addLayout(head)
        self._strength = QSlider(Qt.Orientation.Horizontal)
        self._strength.setRange(0, 100)
        self._strength.setValue(100)
        self._strength.valueChanged.connect(self._on_strength)
        sg.addWidget(self._strength)
        controls.addWidget(s_group, 1)
        # Style
        style_combo = select(["Macro", "Portrait", "Landscape", "Urban", "Wildlife"])
        style_combo.currentTextChanged.connect(self.style_changed.emit)
        controls.addWidget(_controls_group("Style", style_combo))
        # Filter
        filter_combo = select(["Crisp", "Soft", "Vivid", "Muted", "B&W"])
        filter_combo.currentTextChanged.connect(self.filter_changed.emit)
        controls.addWidget(_controls_group("Filter", filter_combo))
        # Crop + rotate
        crop_combo = select(list(_CROP_ASPECTS))
        crop_combo.currentTextChanged.connect(self._on_crop_aspect)
        rot_l = ghost_button("⟲")
        rot_l.setFixedSize(34, 34)
        rot_l.setToolTip("Rotate 90° left")
        rot_l.clicked.connect(lambda: self.rotate_requested.emit(-90))
        rot_r = ghost_button("⟳")
        rot_r.setFixedSize(34, 34)
        rot_r.setToolTip("Rotate 90° right")
        rot_r.clicked.connect(lambda: self.rotate_requested.emit(90))
        controls.addWidget(
            _controls_group("Crop", crop_combo, rot_l, rot_r)
        )
        outer.addLayout(controls)

        # ── Stable media stage ──
        self._stage = EditorStage()
        self._stage.prev_button.clicked.connect(self.prev_requested.emit)
        self._stage.next_button.clicked.connect(self.next_requested.emit)
        self._stable = StableMediaStage(control_zone_height=104)
        self._stable.setStage(self._stage)
        photo_spacer = QWidget()
        photo_spacer.setMinimumHeight(104)
        self._stable.setPhotoControls(photo_spacer)
        video_spacer = QWidget()
        video_spacer.setMinimumHeight(104)
        self._stable.setVideoControls(video_spacer)
        self._stable.setMode(StableMediaStage.PHOTO)
        outer.addWidget(self._stable, 1)

        # ── Bottom bar: filmstrip + view controls ──
        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        self._filmstrip = Filmstrip()
        self._filmstrip.thumbClicked.connect(self._on_filmstrip_jump)
        bottom.addWidget(self._filmstrip, 1)
        full_screen = ghost_button("⛶ Full screen  F11")
        full_screen.clicked.connect(self.fullscreen_toggled.emit)
        bottom.addWidget(full_screen)
        full_res = ghost_button("⤢ Full resolution  F10")
        full_res.clicked.connect(self.full_resolution_requested.emit)
        bottom.addWidget(full_res)
        outer.addLayout(bottom)

    @staticmethod
    def _micro(text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setObjectName("Micro")
        return lbl

    # ── data API ────────────────────────────────────────────────────────

    def setItemsForPreview(
        self,
        items: list[EditorItem],
        *,
        start_index: int = 0,
    ) -> None:
        self._items = list(items)
        self._index = max(0, min(start_index, len(items) - 1)) if items else 0
        self._refresh()

    # ── handlers ────────────────────────────────────────────────────────

    def _on_strength(self, v: int) -> None:
        self._strength_readout.setText(f"{v / 100.0:.2f}")
        self.strength_changed.emit(v)

    def _on_crop_aspect(self, aspect: str) -> None:
        self._stage.setCropAspect(aspect)
        self.crop_aspect_changed.emit(aspect)

    def _on_filmstrip_jump(self, i: int) -> None:
        if 0 <= i < len(self._items):
            self._index = i
            self.index_changed.emit(i)
            self._refresh()

    def _refresh(self) -> None:
        if not self._items:
            self._counter.setText("0 / 0")
            return
        cur = self._items[self._index]
        self._counter.setText(f"{self._index + 1} / {len(self._items)}")
        self._stage.setItem(cur)
        self._stage.prev_button.setVisible(self._index > 0)
        self._stage.next_button.setVisible(self._index < len(self._items) - 1)
        items = [(it.pixmap, it.state) for it in self._items]
        self._filmstrip.setItems(items, current_index=self._index)

    # ── keyboard ───────────────────────────────────────────────────────

    def keyPressEvent(self, e):  # noqa: N802
        key = e.key()
        if key == Qt.Key.Key_Left and self._index > 0:
            self._index -= 1
            self.index_changed.emit(self._index)
            self._refresh()
            return
        if key == Qt.Key.Key_Right and self._index < len(self._items) - 1:
            self._index += 1
            self.index_changed.emit(self._index)
            self._refresh()
            return
        if key == Qt.Key.Key_F11:
            self.fullscreen_toggled.emit()
            return
        if key == Qt.Key.Key_F10:
            self.full_resolution_requested.emit()
            return
        if key == Qt.Key.Key_Escape:
            self.back_requested.emit()
            return
        super().keyPressEvent(e)
