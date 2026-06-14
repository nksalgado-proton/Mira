"""Surface 07 — Picker (single-photo cull).

Full-screen single-photo review for picking / skipping / comparing. Built
on top of :class:`~mira.ui.design.StableMediaStage` so the canvas never
shifts when stepping photo → video (the reserved transport zone is sized
for the richest case).

Composition (design-system §Surface 07):
    Toolbar:   ghost Back · position counter (4 / 121) · action cluster
               (✓ Pick P · ✗ Skip X · ⇄ Compare C · Full screen F11
               · Full resolution F10). Active state tints the chosen
               action and the matching state border on the stage.
    Stage:     PickerStage — blurred + darkened backdrop (the same photo,
               heavily blurred so any letterbox area extends the image
               instead of leaving black bars) · contained photo
               (KeepAspectRatio, rounded, soft shadow) · LOCKED §5a
               state border · top-left EXIF chip (or cluster badge for
               cluster covers) · top-right visited eye · bottom-left
               Exported accent badge · floating prev/next circular
               nav arrows.
    Reserved transport zone (StableMediaStage's control slot): empty for
               photos, holds the video transport bar in Surface 11.
    Filmstrip: horizontal scroll of neighbor thumbs reusing the design
               catalog's Filmstrip; current item gets an accent ring;
               others dim.

Keyboard map (LOCKED, spec/63 §4):
    P pick · X skip · C compare · ←/→ navigate · F11 fullscreen toggle
    · F10 full-resolution surface (Surface 10).

Live gateway wiring (load items for an event+day, decision persistence,
visited stamping, advance-after-pick) lands in the route-swap commit.
For now the page exposes a setItemsForPreview() path so the smoke +
tests can land independently.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
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
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mira.gateway import Gateway
from mira.ui.design import (
    Filmstrip,
    StableMediaStage,
    danger_ghost_button,
    ghost_button,
    nav_arrow,
    primary_button,
)
from mira.ui.palette import PALETTE

log = logging.getLogger(__name__)


@dataclass
class PickerItem:
    """One pickable item — single photo or cluster cover."""

    item_id: str
    pixmap: QPixmap | None = None
    state: str | None = None
    visited: bool = False
    exported: bool = False
    exif_text: str = ""             # e.g. 'Z9 · 200mm · 1/2000 · f/4 · ISO 400'
    cluster_type: str | None = None  # if set, replaces the EXIF chip top-left
    cluster_count: int = 0


# ── PickerStage ────────────────────────────────────────────────────────


class PickerStage(QWidget):
    """Custom-painted single-item stage.

    Paints (in order): blurred backdrop, contained photo, locked-color
    state border, then defers the overlay widgets (EXIF chip, visited
    eye, exported badge, prev/next nav) to absolute-positioned children.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._state: str | None = None
        self._exif_text: str = ""
        self._cluster_type: str | None = None
        self._cluster_count: int = 0
        self._visited: bool = False
        self._exported: bool = False
        self._blurred_cache: QPixmap | None = None
        self._cache_key: tuple = ()
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.setMinimumHeight(300)

        # Overlay widgets — children of the stage, repositioned in
        # resizeEvent.
        self._exif_chip = QLabel(self)
        self._exif_chip.setObjectName("StageExifChip")
        self._exif_chip.setStyleSheet(
            "background: rgba(8,10,16,0.74); color: #ffffff;"
            " border: 1px solid rgba(255,255,255,0.18); border-radius: 10px;"
            " padding: 4px 10px; font-size: 12px; font-weight: 600;"
        )
        self._exif_chip.hide()
        self._eye_chip = QLabel("◉", self)
        self._eye_chip.setObjectName("StageEyeChip")
        self._eye_chip.setStyleSheet(
            "background: rgba(8,10,16,0.74); color: #ffffff;"
            " border: 1px solid rgba(255,255,255,0.18); border-radius: 10px;"
            " padding: 4px 8px; font-size: 12px;"
        )
        self._eye_chip.hide()
        self._exported_chip = QLabel("↑ Exported", self)
        self._exported_chip.setObjectName("StageExportedChip")
        self._exported_chip.setStyleSheet(
            "background: #7c6cff; color: #ffffff; border: none;"
            " border-radius: 10px; padding: 4px 10px; font-size: 12px;"
            " font-weight: 700;"
        )
        self._exported_chip.hide()
        self._prev_btn = nav_arrow("left", self)
        self._prev_btn.hide()
        self._next_btn = nav_arrow("right", self)
        self._next_btn.hide()

    # ── public ─────────────────────────────────────────────────────────

    def setItem(self, item: PickerItem) -> None:
        self._pixmap = item.pixmap
        self._state = item.state
        self._exif_text = item.exif_text
        self._cluster_type = item.cluster_type
        self._cluster_count = item.cluster_count
        self._visited = item.visited
        self._exported = item.exported
        self._blurred_cache = None
        self._refresh_overlays()
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
        # Background — solid bg color first to absorb the no-photo case
        painter.fillRect(rect, QColor(palette["bg"]))

        if self._pixmap is None or self._pixmap.isNull():
            painter.end()
            return

        # Blurred backdrop — cached pre-blurred QPixmap
        backdrop = self._build_backdrop()
        if backdrop is not None:
            # Stretch to fill, anchored center
            painter.drawPixmap(rect, backdrop, backdrop.rect())

        # Compute contained photo rect
        pad = 24
        avail = rect.adjusted(pad, pad, -pad, -pad)
        photo = self._pixmap.scaled(
            avail.width(), avail.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        photo_x = avail.x() + (avail.width() - photo.width()) // 2
        photo_y = avail.y() + (avail.height() - photo.height()) // 2
        photo_rect = QRectF(
            photo_x, photo_y, photo.width(), photo.height(),
        )

        # Clip-paint with rounded corners
        radius = 12.0
        path = QPainterPath()
        path.addRoundedRect(photo_rect, radius, radius)
        painter.save()
        painter.setClipPath(path)
        painter.drawPixmap(int(photo_x), int(photo_y), photo)
        painter.restore()

        # State border
        border_color = QColor(
            palette["picked"] if self._state == "picked"
            else palette["skipped"] if self._state == "skipped"
            else palette["compare"] if self._state == "compare"
            else palette["mixed"] if self._state == "mixed"
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
        # Downscale → up-scale → darken — fast blur approximation.
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

    # ── overlay positioning ───────────────────────────────────────────

    def _refresh_overlays(self) -> None:
        if self._cluster_type:
            self._exif_chip.setText(
                f"⬡ {self._cluster_type.title()} ×{self._cluster_count}"
            )
            self._exif_chip.show()
        elif self._exif_text:
            self._exif_chip.setText(self._exif_text)
            self._exif_chip.show()
        else:
            self._exif_chip.hide()
        self._eye_chip.setVisible(self._visited)
        self._exported_chip.setVisible(self._exported)
        self._exif_chip.adjustSize()
        self._eye_chip.adjustSize()
        self._exported_chip.adjustSize()
        self._position_overlays()

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._position_overlays()
        # Invalidate the backdrop cache on resize
        self._blurred_cache = None

    def _position_overlays(self) -> None:
        # Account for the 24px padding around the photo
        pad = 24
        # EXIF chip top-left, eye top-right, exported badge bottom-left
        self._exif_chip.move(pad + 8, pad + 8)
        self._eye_chip.move(
            self.width() - self._eye_chip.width() - pad - 8,
            pad + 8,
        )
        self._exported_chip.move(
            pad + 8,
            self.height() - self._exported_chip.height() - pad - 8,
        )
        # Floating nav arrows — visible only when wired by the host via
        # setNavVisible(True). Positioned at vertical center.
        cy = (self.height() - self._prev_btn.height()) // 2
        self._prev_btn.move(pad + 8, cy)
        self._next_btn.move(
            self.width() - self._next_btn.width() - pad - 8, cy
        )


def _action_button(label: str, hint: str, role: str) -> QPushButton:
    """Pick / Skip / Compare action button with a small keyboard-hint chip
    after the label."""
    btn = QPushButton(f"{label}  {hint}")
    btn.setObjectName(role)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setCheckable(True)
    return btn


class PickerPage(QWidget):
    """Surface 07 — single-photo cull page."""

    back_requested = pyqtSignal()
    pick_requested = pyqtSignal(str)
    skip_requested = pyqtSignal(str)
    compare_requested = pyqtSignal(str)
    prev_requested = pyqtSignal()
    next_requested = pyqtSignal()
    fullscreen_toggled = pyqtSignal()
    full_resolution_requested = pyqtSignal()
    index_changed = pyqtSignal(int)

    def __init__(
        self,
        gateway: Optional[Gateway] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.gateway = gateway
        self._items: list[PickerItem] = []
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
        self._pick_btn = _action_button("✓ Pick", "P", "Pick")
        self._pick_btn.clicked.connect(self._on_pick)
        toolbar.addWidget(self._pick_btn)
        self._skip_btn = _action_button("✗ Skip", "X", "Skip")
        self._skip_btn.clicked.connect(self._on_skip)
        toolbar.addWidget(self._skip_btn)
        self._compare_btn = _action_button("⇄ Compare", "C", "Compare")
        self._compare_btn.clicked.connect(self._on_compare)
        toolbar.addWidget(self._compare_btn)
        toolbar.addSpacing(12)
        full_res = ghost_button("Full resolution F10")
        full_res.clicked.connect(self.full_resolution_requested.emit)
        toolbar.addWidget(full_res)
        full_screen = ghost_button("Full screen F11")
        full_screen.clicked.connect(self.fullscreen_toggled.emit)
        toolbar.addWidget(full_screen)
        outer.addLayout(toolbar)

        # ── Stable media stage ──
        self._stage = PickerStage()
        # Nav arrows are children of the stage; wire them now
        self._stage.prev_button.clicked.connect(self.prev_requested.emit)
        self._stage.next_button.clicked.connect(self.next_requested.emit)

        self._stable = StableMediaStage(control_zone_height=84)
        self._stable.setStage(self._stage)
        # Photo mode: empty spacer reserving the height
        spacer = QWidget()
        spacer.setMinimumHeight(84)
        self._stable.setPhotoControls(spacer)
        # Video mode placeholder (real transport bar comes with Surface 11)
        video_spacer = QWidget()
        video_spacer.setMinimumHeight(84)
        self._stable.setVideoControls(video_spacer)
        self._stable.setMode(StableMediaStage.PHOTO)
        outer.addWidget(self._stable, 1)

        # ── Filmstrip ──
        self._filmstrip = Filmstrip()
        self._filmstrip.thumbClicked.connect(self._on_filmstrip_jump)
        outer.addWidget(self._filmstrip)

    # ── data API ────────────────────────────────────────────────────────

    def setItemsForPreview(
        self,
        items: list[PickerItem],
        *,
        start_index: int = 0,
    ) -> None:
        self._items = list(items)
        self._index = max(0, min(start_index, len(items) - 1)) if items else 0
        self._refresh()

    # ── handlers ────────────────────────────────────────────────────────

    def _current_id(self) -> str:
        if not self._items:
            return ""
        return self._items[self._index].item_id

    def _on_pick(self) -> None:
        if self._items:
            self._items[self._index].state = "picked"
            self._items[self._index].visited = True
            self.pick_requested.emit(self._current_id())
            self._refresh()

    def _on_skip(self) -> None:
        if self._items:
            self._items[self._index].state = "skipped"
            self._items[self._index].visited = True
            self.skip_requested.emit(self._current_id())
            self._refresh()

    def _on_compare(self) -> None:
        if self._items:
            self._items[self._index].state = "compare"
            self._items[self._index].visited = True
            self.compare_requested.emit(self._current_id())
            self._refresh()

    def _on_filmstrip_jump(self, i: int) -> None:
        if 0 <= i < len(self._items):
            self._index = i
            self.index_changed.emit(i)
            self._refresh()

    # ── render ─────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        if not self._items:
            self._counter.setText("0 / 0")
            return
        cur = self._items[self._index]
        self._counter.setText(f"{self._index + 1} / {len(self._items)}")
        self._stage.setItem(cur)
        self._stage.prev_button.setVisible(self._index > 0)
        self._stage.next_button.setVisible(self._index < len(self._items) - 1)
        # Action button tints
        for btn, state in (
            (self._pick_btn, "picked"),
            (self._skip_btn, "skipped"),
            (self._compare_btn, "compare"),
        ):
            btn.setChecked(cur.state == state)
        # Filmstrip — small thumbnails of neighbors
        items = [
            (it.pixmap, it.state) for it in self._items
        ]
        self._filmstrip.setItems(items, current_index=self._index)

    # ── keyboard ───────────────────────────────────────────────────────

    def keyPressEvent(self, e):  # noqa: N802
        key = e.key()
        # Project's LOCKED keyboard map (spec/63 §4): P pick, X skip,
        # C compare, ←/→ navigate, F11 fullscreen, F10 full resolution.
        if key == Qt.Key.Key_P:
            self._on_pick()
            return
        if key == Qt.Key.Key_X:
            self._on_skip()
            return
        if key == Qt.Key.Key_C:
            self._on_compare()
            return
        if key == Qt.Key.Key_Left:
            if self._index > 0:
                self._index -= 1
                self.index_changed.emit(self._index)
                self._refresh()
            return
        if key == Qt.Key.Key_Right:
            if self._index < len(self._items) - 1:
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
