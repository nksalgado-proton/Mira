"""Surface 11 — Video Picker.

The Surface 07 (Picker) pattern adapted for video clips. Lives on top of
the same StableMediaStage scaffold so the canvas stays anchored when the
event mixes photos and videos. The video-mode control zone hosts a
proper Transport bar (frame-step / play-pause / time / scrubber / volume
/ speed) instead of the photo's reserved spacer.

Composition (design-system §Surface 11):
    Toolbar:        Back · '1 / 8 · videos' counter · ✓ Pick (P) /
                    ✗ Skip (X) / ⇄ Compare (C) · Full screen F11.
    Stage:          VideoStage — blurred backdrop (from a representative
                    frame), contained video frame with LOCKED §5a state
                    border, top-left ▶ duration/format badge, top-right
                    visited eye, bottom-left ↑ Exported badge, big
                    center play/pause button, floating ‹/› nav arrows.
    Transport bar:  inside the StableMediaStage's video control slot.
                    Frame ◀ · play/pause (accent) · Frame ▶ · current /
                    total time · played-fill scrubber · mute + volume ·
                    speed select.
    Filmstrip:      Filmstrip (reuses the design-catalog variant) — 16:9
                    thumbs with a small ▶ glyph + duration chip per item.

Live QMediaPlayer + QVideoWidget wiring lands in the route-swap commit;
this page's setItemsForPreview path renders the chrome against still
poster pixmaps.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
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
    danger_ghost_button,
    ghost_button,
    nav_arrow,
    primary_button,
    select,
)
from mira.ui.palette import PALETTE

log = logging.getLogger(__name__)


@dataclass
class VideoItem:
    """One pickable video clip."""

    item_id: str
    poster_pixmap: QPixmap | None = None
    state: str | None = None
    visited: bool = False
    exported: bool = False
    duration_seconds: int = 0
    format_text: str = ""        # e.g. '4K · 60fps'
    position_seconds: int = 0


def _fmt_time(s: int) -> str:
    s = max(0, int(s))
    m, sec = divmod(s, 60)
    return f"{m}:{sec:02d}"


class VideoStage(QWidget):
    """Custom-painted video stage — blurred backdrop, contained poster
    frame, LOCKED §5a state border, overlays."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._state: str | None = None
        self._visited = False
        self._exported = False
        self._duration_text = ""
        self._blurred_cache: QPixmap | None = None
        self._cache_key: tuple = ()
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.setMinimumHeight(300)

        self._duration_chip = QLabel("", self)
        self._duration_chip.setStyleSheet(
            "background: rgba(8,10,16,0.74); color: #ffffff;"
            " border: 1px solid rgba(255,255,255,0.18); border-radius: 10px;"
            " padding: 4px 10px; font-size: 12px; font-weight: 600;"
        )
        self._duration_chip.hide()
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
        self._play_btn = QPushButton("▶", self)
        self._play_btn.setObjectName("VideoBigPlay")
        self._play_btn.setStyleSheet(
            "QPushButton#VideoBigPlay {"
            " background: rgba(8,10,16,0.78); color: #ffffff;"
            " border: 2px solid rgba(255,255,255,0.28);"
            " border-radius: 36px; font-size: 28px; font-weight: 700;"
            "}"
            "QPushButton#VideoBigPlay:hover {"
            " border-color: #7c6cff;"
            "}"
        )
        self._play_btn.setFixedSize(72, 72)
        self._prev_btn = nav_arrow("left", self)
        self._next_btn = nav_arrow("right", self)

    def setItem(self, item: VideoItem) -> None:
        self._pixmap = item.poster_pixmap
        self._state = item.state
        self._visited = item.visited
        self._exported = item.exported
        bits = []
        if item.duration_seconds:
            bits.append(f"▶ {_fmt_time(item.duration_seconds)}")
        if item.format_text:
            bits.append(item.format_text)
        self._duration_text = "  ·  ".join(bits)
        self._duration_chip.setText(self._duration_text)
        self._duration_chip.setVisible(bool(self._duration_text))
        self._eye.setVisible(self._visited)
        self._exported_chip.setVisible(self._exported)
        for chip in (
            self._duration_chip, self._eye, self._exported_chip,
        ):
            chip.adjustSize()
        self._blurred_cache = None
        self._position_overlays()
        self.update()

    @property
    def prev_button(self) -> QPushButton:
        return self._prev_btn

    @property
    def next_button(self) -> QPushButton:
        return self._next_btn

    @property
    def play_button(self) -> QPushButton:
        return self._play_btn

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
        painter.drawPixmap(int(x), int(y), scaled)

        # State border
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QPen
        border = QColor(
            palette["picked"] if self._state == "picked"
            else palette["skipped"] if self._state == "skipped"
            else palette["compare"] if self._state == "compare"
            else palette["line"]
        )
        pen = QPen(border, 3)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen)
        painter.drawRect(QRectF(x, y, scaled.width(), scaled.height()))
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
        from PyQt6.QtGui import QImage
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

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._blurred_cache = None
        self._position_overlays()

    def _position_overlays(self) -> None:
        pad = 24
        self._duration_chip.move(pad + 8, pad + 8)
        self._eye.move(
            self.width() - self._eye.width() - pad - 8, pad + 8,
        )
        self._exported_chip.move(
            pad + 8,
            self.height() - self._exported_chip.height() - pad - 8,
        )
        # Big play centered
        self._play_btn.move(
            (self.width() - self._play_btn.width()) // 2,
            (self.height() - self._play_btn.height()) // 2,
        )
        # Floating nav arrows
        cy = (self.height() - self._prev_btn.height()) // 2
        self._prev_btn.move(pad + 8, cy)
        self._next_btn.move(
            self.width() - self._next_btn.width() - pad - 8, cy
        )


class _Scrubber(QSlider):
    """Custom scrubber painting the played portion in accent."""

    def __init__(self) -> None:
        super().__init__(Qt.Orientation.Horizontal)
        self.setRange(0, 1000)
        self.setStyleSheet(
            "QSlider::groove:horizontal {"
            " background: #222734; height: 6px; border-radius: 3px;"
            "}"
            "QSlider::sub-page:horizontal {"
            " background: #7c6cff; border-radius: 3px;"
            "}"
            "QSlider::handle:horizontal {"
            " background: #ffffff; width: 12px; margin: -4px 0;"
            " border-radius: 6px;"
            "}"
        )


class TransportBar(QFrame):
    """Frame-step · play/pause · time · scrubber · volume · speed."""

    play_pause_requested = pyqtSignal()
    frame_step_requested = pyqtSignal(int)   # -1 / +1
    position_changed = pyqtSignal(int)        # 0..1000
    volume_changed = pyqtSignal(int)          # 0..100
    speed_changed = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Card2")
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 10, 14, 10)
        h.setSpacing(10)
        # Frame step + play/pause
        prev_frame = ghost_button("◀|")
        prev_frame.setFixedSize(34, 34)
        prev_frame.clicked.connect(lambda: self.frame_step_requested.emit(-1))
        h.addWidget(prev_frame)
        self._play_btn = primary_button("▶")
        self._play_btn.setFixedSize(46, 34)
        self._play_btn.clicked.connect(self.play_pause_requested.emit)
        h.addWidget(self._play_btn)
        next_frame = ghost_button("|▶")
        next_frame.setFixedSize(34, 34)
        next_frame.clicked.connect(lambda: self.frame_step_requested.emit(1))
        h.addWidget(next_frame)
        # Time
        self._time_label = QLabel("0:00 / 0:00")
        self._time_label.setObjectName("Sub")
        h.addWidget(self._time_label)
        # Scrubber
        self._scrubber = _Scrubber()
        self._scrubber.valueChanged.connect(self.position_changed.emit)
        h.addWidget(self._scrubber, 1)
        # Volume
        vol_icon = QLabel("🔊")
        vol_icon.setStyleSheet("color: #8b94a7;")
        h.addWidget(vol_icon)
        self._volume = QSlider(Qt.Orientation.Horizontal)
        self._volume.setRange(0, 100)
        self._volume.setValue(80)
        self._volume.setFixedWidth(80)
        self._volume.setStyleSheet(
            "QSlider::groove:horizontal { background: #222734; height: 4px; border-radius: 2px; }"
            "QSlider::sub-page:horizontal { background: #7c6cff; border-radius: 2px; }"
            "QSlider::handle:horizontal { background: #ffffff; width: 10px; margin: -3px 0; border-radius: 5px; }"
        )
        self._volume.valueChanged.connect(self.volume_changed.emit)
        h.addWidget(self._volume)
        # Speed
        self._speed = select(["0.25×", "0.5×", "1×", "1.5×", "2×"])
        self._speed.setCurrentText("1×")
        self._speed.currentTextChanged.connect(self.speed_changed.emit)
        self._speed.setFixedWidth(80)
        h.addWidget(self._speed)

    def setItem(self, item: VideoItem) -> None:
        self._time_label.setText(
            f"{_fmt_time(item.position_seconds)} / "
            f"{_fmt_time(item.duration_seconds)}"
        )
        if item.duration_seconds > 0:
            v = int(item.position_seconds / item.duration_seconds * 1000)
            self._scrubber.blockSignals(True)
            self._scrubber.setValue(v)
            self._scrubber.blockSignals(False)


class VideoPickerPage(QWidget):
    """Surface 11 — video pick page."""

    back_requested = pyqtSignal()
    pick_requested = pyqtSignal(str)
    skip_requested = pyqtSignal(str)
    compare_requested = pyqtSignal(str)
    prev_requested = pyqtSignal()
    next_requested = pyqtSignal()
    fullscreen_toggled = pyqtSignal()
    play_pause_requested = pyqtSignal()
    index_changed = pyqtSignal(int)

    def __init__(
        self,
        gateway: Optional[Gateway] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.gateway = gateway
        self._items: list[VideoItem] = []
        self._index = 0
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(10)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        self._back = ghost_button("‹ Back")
        self._back.clicked.connect(self.back_requested.emit)
        toolbar.addWidget(self._back)
        self._counter = QLabel("0 / 0 · videos")
        self._counter.setObjectName("Sub")
        toolbar.addWidget(self._counter)
        toolbar.addStretch()
        self._pick_btn = ghost_button("✓ Pick  P")
        self._pick_btn.setCheckable(True)
        self._pick_btn.clicked.connect(self._on_pick)
        toolbar.addWidget(self._pick_btn)
        self._skip_btn = danger_ghost_button("✗ Skip  X")
        self._skip_btn.setCheckable(True)
        self._skip_btn.clicked.connect(self._on_skip)
        toolbar.addWidget(self._skip_btn)
        self._compare_btn = ghost_button("⇄ Compare  C")
        self._compare_btn.setCheckable(True)
        self._compare_btn.clicked.connect(self._on_compare)
        toolbar.addWidget(self._compare_btn)
        full_screen = ghost_button("Full screen  F11")
        full_screen.clicked.connect(self.fullscreen_toggled.emit)
        toolbar.addWidget(full_screen)
        outer.addLayout(toolbar)

        # Stable media stage with VideoStage + TransportBar
        self._stage = VideoStage()
        self._stage.prev_button.clicked.connect(self.prev_requested.emit)
        self._stage.next_button.clicked.connect(self.next_requested.emit)
        self._stage.play_button.clicked.connect(self.play_pause_requested.emit)

        self._transport = TransportBar()
        self._transport.play_pause_requested.connect(
            self.play_pause_requested.emit
        )

        self._stable = StableMediaStage(control_zone_height=84)
        self._stable.setStage(self._stage)
        photo_spacer = QWidget()
        photo_spacer.setMinimumHeight(84)
        self._stable.setPhotoControls(photo_spacer)
        self._stable.setVideoControls(self._transport)
        self._stable.setMode(StableMediaStage.VIDEO)
        outer.addWidget(self._stable, 1)

        # Filmstrip
        self._filmstrip = Filmstrip()
        self._filmstrip.thumbClicked.connect(self._on_filmstrip_jump)
        outer.addWidget(self._filmstrip)

    # ── data ────────────────────────────────────────────────────────────

    def setItemsForPreview(
        self,
        items: list[VideoItem],
        *,
        start_index: int = 0,
    ) -> None:
        self._items = list(items)
        self._index = max(0, min(start_index, len(items) - 1)) if items else 0
        self._refresh()

    def _current_id(self) -> str:
        if not self._items:
            return ""
        return self._items[self._index].item_id

    def _on_pick(self) -> None:
        if not self._items:
            return
        self._items[self._index].state = "picked"
        self._items[self._index].visited = True
        self.pick_requested.emit(self._current_id())
        self._refresh()

    def _on_skip(self) -> None:
        if not self._items:
            return
        self._items[self._index].state = "skipped"
        self._items[self._index].visited = True
        self.skip_requested.emit(self._current_id())
        self._refresh()

    def _on_compare(self) -> None:
        if not self._items:
            return
        self._items[self._index].state = "compare"
        self._items[self._index].visited = True
        self.compare_requested.emit(self._current_id())
        self._refresh()

    def _on_filmstrip_jump(self, i: int) -> None:
        if 0 <= i < len(self._items):
            self._index = i
            self.index_changed.emit(i)
            self._refresh()

    def _refresh(self) -> None:
        if not self._items:
            self._counter.setText("0 / 0 · videos")
            return
        cur = self._items[self._index]
        self._counter.setText(
            f"{self._index + 1} / {len(self._items)} · videos"
        )
        self._stage.setItem(cur)
        self._transport.setItem(cur)
        self._stage.prev_button.setVisible(self._index > 0)
        self._stage.next_button.setVisible(
            self._index < len(self._items) - 1
        )
        for btn, state in (
            (self._pick_btn, "picked"),
            (self._skip_btn, "skipped"),
            (self._compare_btn, "compare"),
        ):
            btn.setChecked(cur.state == state)
        items = [(it.poster_pixmap, it.state) for it in self._items]
        self._filmstrip.setItems(items, current_index=self._index)

    # ── keyboard ───────────────────────────────────────────────────────

    def keyPressEvent(self, e):  # noqa: N802
        k = e.key()
        if k == Qt.Key.Key_P:
            self._on_pick(); return
        if k == Qt.Key.Key_X:
            self._on_skip(); return
        if k == Qt.Key.Key_C:
            self._on_compare(); return
        if k == Qt.Key.Key_Space or k == Qt.Key.Key_Tab:
            self.play_pause_requested.emit(); return
        if k == Qt.Key.Key_Left and self._index > 0:
            self._index -= 1; self.index_changed.emit(self._index); self._refresh(); return
        if k == Qt.Key.Key_Right and self._index < len(self._items) - 1:
            self._index += 1; self.index_changed.emit(self._index); self._refresh(); return
        if k == Qt.Key.Key_F11:
            self.fullscreen_toggled.emit(); return
        if k == Qt.Key.Key_Escape:
            self.back_requested.emit(); return
        super().keyPressEvent(e)
