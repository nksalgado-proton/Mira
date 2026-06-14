"""Surface 12 — Video Editor.

The Surface 08 Editor pattern adapted for video clips: keeps the same
look/filter/crop adjustments, adds Audio + Vibrations chips, and replaces
the photo Editor's reserved transport slot with a richer trio of rows —
a trim timeline (with draggable in/out handles + playhead) plus a Tools
row (markers / snapshots / mute/volume/speed) plus a Transport row
(start / step / frame / markers menu / play / snapshots menu / frame /
step / end). Together they fill the StableMediaStage's video control
zone (~280px) so stepping photo→video keeps the canvas anchored.

Composition (design-system §Surface 12):
    Toolbar:        Back · meta line (Video N / M · filename · K
                    segments · J picked) · spacer · ↺ Reset all ·
                    primary ↑ Export all.
    Controls panel: Look segmented · Strength slider · Style · Filter
                    · Crop aspect + rotate · Audio (Fade in/out) ·
                    Vibrations (Stabilise 0–5).
    Stage:          VideoStage from Surface 11 — blurred backdrop +
                    poster + LOCKED state border + duration chip +
                    center play + floating nav arrows.
    Reserved zone:  Timeline + Tools row + Transport row.
    Filmstrip:      same Filmstrip variant as Surface 11.

Live wiring (QMediaPlayer + segment marker materialisation + audio fade /
stabilise pipeline params on export) lands in the route-swap commit.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
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
    pill_toggle,
    primary_button,
    select,
)
from mira.ui.palette import PALETTE
from mira.ui.pages.video_picker_page import TransportBar, VideoItem, VideoStage

log = logging.getLogger(__name__)


@dataclass
class Segment:
    """One trimmed in/out region of a clip. start_seconds and
    end_seconds are in clip-relative seconds."""

    start_seconds: float = 0.0
    end_seconds: float = 0.0


@dataclass
class VideoEditorItem(VideoItem):
    """Video Editor item — adds segments + picked flag."""

    segments: list[Segment] = field(default_factory=list)
    picked: bool = False


_LOOK_PRESETS = ("Original", "Natural", "Brighten", "Deeper", "Grid")


class _Timeline(QFrame):
    """Custom-painted timeline with the trimmed segment highlighted +
    playhead. The trim handles + drag handling are stubs for the route-
    swap commit; today the widget just paints the look."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Card2")
        self.setMinimumHeight(54)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._duration = 1.0
        self._position = 0.0
        self._segments: list[Segment] = []

    def setItem(self, item: VideoEditorItem) -> None:
        self._duration = max(0.1, float(item.duration_seconds))
        self._position = float(item.position_seconds)
        self._segments = list(item.segments)
        self.update()

    def paintEvent(self, _evt) -> None:  # noqa: N802
        app = QApplication.instance()
        mode = (app.property("theme") if app else None) or "dark"
        palette = PALETTE[mode]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        # Track
        track = QRectF(12, r.height() / 2 - 5, r.width() - 24, 10)
        p.setBrush(QColor(palette["track"]))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(track, 5, 5)
        # Segments — accent fill
        for seg in self._segments:
            x0 = 12 + (seg.start_seconds / self._duration) * (r.width() - 24)
            x1 = 12 + (seg.end_seconds / self._duration) * (r.width() - 24)
            seg_rect = QRectF(x0, track.y(), max(0.0, x1 - x0), track.height())
            p.setBrush(QColor(palette["accent"]))
            p.drawRoundedRect(seg_rect, 5, 5)
            # Drag handles at each end
            handle = QColor(palette["accent"]).lighter(140)
            p.setBrush(handle)
            for hx in (x0, x1):
                p.drawRoundedRect(
                    QRectF(hx - 4, track.y() - 6, 8, track.height() + 12),
                    3, 3,
                )
        # Playhead
        px = 12 + (self._position / self._duration) * (r.width() - 24)
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.setBrush(QColor("#ffffff"))
        p.drawLine(int(px), int(track.y() - 8), int(px), int(track.bottom() + 8))
        p.drawEllipse(QRectF(px - 5, track.y() - 12, 10, 10))
        p.end()


class _ToolsRow(QFrame):
    """Marker · Snapshot · Remove · Toggle Status · Reset right ·
    Mute · Vol · Speed (right cluster)."""

    add_marker_requested = pyqtSignal()
    snapshot_requested = pyqtSignal()
    remove_requested = pyqtSignal()
    toggle_status_requested = pyqtSignal()
    reset_requested = pyqtSignal()
    volume_changed = pyqtSignal(int)
    speed_changed = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Card2")
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 8, 14, 8)
        h.setSpacing(8)
        for label, signal in (
            ("Marker", self.add_marker_requested),
            ("Snapshot", self.snapshot_requested),
            ("Remove", self.remove_requested),
        ):
            b = ghost_button(label)
            b.clicked.connect(signal.emit)
            h.addWidget(b)
        h.addSpacing(12)
        toggle = ghost_button("Toggle Status")
        toggle.clicked.connect(self.toggle_status_requested.emit)
        h.addWidget(toggle)
        reset = ghost_button("Reset ▾")
        reset.clicked.connect(self.reset_requested.emit)
        h.addWidget(reset)
        h.addStretch()
        mute = ghost_button("🔇 Mute")
        h.addWidget(mute)
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
        self._speed = select(["0.25×", "0.5×", "1×", "1.5×", "2×"])
        self._speed.setCurrentText("1×")
        self._speed.currentTextChanged.connect(self.speed_changed.emit)
        self._speed.setFixedWidth(80)
        h.addWidget(self._speed)


class _TransportRow(QFrame):
    """Start · Stop ← · Frame ← · Markers ▾ · Play · Snapshots ▾ ·
    Frame → · Stop → · End · segment info (right)."""

    start_requested = pyqtSignal()
    stop_back_requested = pyqtSignal()
    frame_back_requested = pyqtSignal()
    markers_menu_requested = pyqtSignal()
    play_pause_requested = pyqtSignal()
    snapshots_menu_requested = pyqtSignal()
    frame_fwd_requested = pyqtSignal()
    stop_fwd_requested = pyqtSignal()
    end_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Card2")
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 8, 14, 8)
        h.setSpacing(6)
        for label, signal in (
            ("⟨⟨ Start", self.start_requested),
            ("◀ Stop", self.stop_back_requested),
            ("◀| Frame", self.frame_back_requested),
            ("Markers ▾", self.markers_menu_requested),
        ):
            b = ghost_button(label)
            b.clicked.connect(signal.emit)
            h.addWidget(b)
        self._play = primary_button("▶ Play")
        self._play.setFixedHeight(34)
        self._play.clicked.connect(self.play_pause_requested.emit)
        h.addWidget(self._play)
        for label, signal in (
            ("Snapshots ▾", self.snapshots_menu_requested),
            ("|▶ Frame", self.frame_fwd_requested),
            ("Stop ▶", self.stop_fwd_requested),
            ("End ⟩⟩", self.end_requested),
        ):
            b = ghost_button(label)
            b.clicked.connect(signal.emit)
            h.addWidget(b)
        h.addStretch()
        self._segment_label = QLabel("Segment 1 of 1 · 0:00")
        self._segment_label.setObjectName("Sub")
        h.addWidget(self._segment_label)

    def setSegmentInfo(self, label: str) -> None:
        self._segment_label.setText(label)


def _controls_group(label: str, *widgets: QWidget) -> QFrame:
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


class VideoEditorPage(QWidget):
    """Surface 12 — video editor page."""

    back_requested = pyqtSignal()
    reset_all_requested = pyqtSignal()
    export_all_requested = pyqtSignal()
    play_pause_requested = pyqtSignal()
    index_changed = pyqtSignal(int)
    look_changed = pyqtSignal(str)
    strength_changed = pyqtSignal(int)
    style_changed = pyqtSignal(str)
    filter_changed = pyqtSignal(str)
    crop_aspect_changed = pyqtSignal(str)
    audio_fade_changed = pyqtSignal(str)
    stabilise_changed = pyqtSignal(int)

    def __init__(
        self,
        gateway: Optional[Gateway] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.gateway = gateway
        self._items: list[VideoEditorItem] = []
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
        self._meta_label = QLabel("Video 0 / 0")
        self._meta_label.setObjectName("Sub")
        toolbar.addWidget(self._meta_label)
        toolbar.addStretch()
        reset_btn = ghost_button("↺ Reset all")
        reset_btn.clicked.connect(self.reset_all_requested.emit)
        toolbar.addWidget(reset_btn)
        export_btn = primary_button("↑ Export all")
        export_btn.clicked.connect(self.export_all_requested.emit)
        toolbar.addWidget(export_btn)
        outer.addLayout(toolbar)

        # Controls panel
        controls = QHBoxLayout()
        controls.setSpacing(10)
        # Look segmented
        look_group = QFrame(); look_group.setObjectName("Card2")
        lg = QVBoxLayout(look_group); lg.setContentsMargins(12, 8, 12, 10); lg.setSpacing(6)
        lg.addWidget(self._micro("Look"))
        look_row = QHBoxLayout(); look_row.setSpacing(4)
        self._look_buttons = QButtonGroup(self)
        for name in _LOOK_PRESETS:
            b = pill_toggle(name, checked=(name == "Natural"))
            b.clicked.connect(lambda _c=False, n=name: self.look_changed.emit(n))
            self._look_buttons.addButton(b)
            look_row.addWidget(b)
        lg.addLayout(look_row)
        controls.addWidget(look_group)
        # Strength
        s_group = QFrame(); s_group.setObjectName("Card2")
        sg = QVBoxLayout(s_group); sg.setContentsMargins(12, 8, 12, 10); sg.setSpacing(6)
        head = QHBoxLayout(); head.addWidget(self._micro("Strength"))
        head.addStretch()
        self._strength_readout = QLabel("1.00")
        self._strength_readout.setObjectName("Sub")
        head.addWidget(self._strength_readout)
        sg.addLayout(head)
        self._strength = QSlider(Qt.Orientation.Horizontal)
        self._strength.setRange(0, 100); self._strength.setValue(100)
        self._strength.valueChanged.connect(self._on_strength)
        sg.addWidget(self._strength)
        controls.addWidget(s_group, 1)
        # Style + Filter
        style_combo = select(["Wildlife", "Macro", "Landscape", "Urban"])
        style_combo.currentTextChanged.connect(self.style_changed.emit)
        controls.addWidget(_controls_group("Style", style_combo))
        filter_combo = select(["None", "Crisp", "Vivid", "B&W"])
        filter_combo.currentTextChanged.connect(self.filter_changed.emit)
        controls.addWidget(_controls_group("Filter", filter_combo))
        # Crop
        crop_combo = select(["No Crop", "16:9", "1:1", "Free"])
        crop_combo.currentTextChanged.connect(self.crop_aspect_changed.emit)
        rot_l = ghost_button("⟲"); rot_l.setFixedSize(34, 34)
        rot_r = ghost_button("⟳"); rot_r.setFixedSize(34, 34)
        controls.addWidget(_controls_group("Crop", crop_combo, rot_l, rot_r))
        # Audio
        audio_combo = select(["No fade", "Fade in", "Fade out", "Fade in/out"])
        audio_combo.currentTextChanged.connect(self.audio_fade_changed.emit)
        controls.addWidget(_controls_group("Audio", audio_combo))
        # Vibrations / Stabilise
        stab_slider = QSlider(Qt.Orientation.Horizontal)
        stab_slider.setRange(0, 5); stab_slider.setValue(2)
        stab_slider.setFixedWidth(120)
        stab_slider.valueChanged.connect(self.stabilise_changed.emit)
        controls.addWidget(_controls_group("Stabilise 0–5", stab_slider))
        outer.addLayout(controls)

        # Stage
        self._stage = VideoStage()
        self._stage.play_button.clicked.connect(self.play_pause_requested.emit)

        # Timeline + tools + transport
        self._timeline = _Timeline()
        self._tools_row = _ToolsRow()
        self._transport_row = _TransportRow()
        self._transport_row.play_pause_requested.connect(self.play_pause_requested.emit)

        # StableMediaStage hosts the video control trio (timeline + tools + transport)
        # in a single composite widget at the floor of ~280px.
        video_zone = QWidget()
        vz = QVBoxLayout(video_zone)
        vz.setContentsMargins(0, 0, 0, 0)
        vz.setSpacing(6)
        vz.addWidget(self._timeline)
        vz.addWidget(self._tools_row)
        vz.addWidget(self._transport_row)
        video_zone.setMinimumHeight(220)

        self._stable = StableMediaStage(control_zone_height=220)
        self._stable.setStage(self._stage)
        photo_spacer = QWidget(); photo_spacer.setMinimumHeight(220)
        self._stable.setPhotoControls(photo_spacer)
        self._stable.setVideoControls(video_zone)
        self._stable.setMode(StableMediaStage.VIDEO)
        outer.addWidget(self._stable, 1)

        # Filmstrip
        self._filmstrip = Filmstrip()
        self._filmstrip.thumbClicked.connect(self._on_filmstrip_jump)
        outer.addWidget(self._filmstrip)

    @staticmethod
    def _micro(text: str) -> QLabel:
        lbl = QLabel(text.upper()); lbl.setObjectName("Micro"); return lbl

    # ── data ────────────────────────────────────────────────────────────

    def setItemsForPreview(
        self,
        items: list[VideoEditorItem],
        *,
        start_index: int = 0,
    ) -> None:
        self._items = list(items)
        self._index = max(0, min(start_index, len(items) - 1)) if items else 0
        self._refresh()

    def _on_strength(self, v: int) -> None:
        self._strength_readout.setText(f"{v / 100.0:.2f}")
        self.strength_changed.emit(v)

    def _on_filmstrip_jump(self, i: int) -> None:
        if 0 <= i < len(self._items):
            self._index = i
            self.index_changed.emit(i)
            self._refresh()

    def _refresh(self) -> None:
        if not self._items:
            self._meta_label.setText("Video 0 / 0")
            return
        cur = self._items[self._index]
        picked_count = sum(1 for it in self._items if it.picked)
        self._meta_label.setText(
            f"Video {self._index + 1} / {len(self._items)} · "
            f"{cur.item_id} · {len(cur.segments)} segment(s)"
            f" · {picked_count} picked"
        )
        self._stage.setItem(cur)
        self._timeline.setItem(cur)
        if cur.segments:
            seg = cur.segments[0]
            dur = max(0.0, seg.end_seconds - seg.start_seconds)
            m = int(dur // 60); s = int(dur % 60)
            self._transport_row.setSegmentInfo(
                f"Segment 1 of {len(cur.segments)} · {m}:{s:02d}"
            )
        else:
            self._transport_row.setSegmentInfo("No segments")
        items = [(it.poster_pixmap, it.state) for it in self._items]
        self._filmstrip.setItems(items, current_index=self._index)
