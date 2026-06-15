"""The bottom strip of the Editor's video workshop (surface 12 fold).

Three rows stacked under the canvas when the EditorPage's viewport lands
on a video item (spec/56 §1 marker-partition + spec/59 §4-§5 lines):

1. **The marker timeline** (:class:`MarkerTimeline`) — clip bands +
   marker handles + snapshot glyphs + the playhead.
2. **Tools row** — Marker · Snapshot · Remove · Toggle Status · Reset ▾ ·
   spacer · Mute · Vol · Speed.
3. **Transport row** — ⏮ Start · ◀ Stop · ◀ Frame · ▼ Markers · Play ·
   📷 Snapshots · Frame ▶ · Stop ▶ End ⏭ · segment-info chip.

The Tab key plays/pauses (spec/63 §4 LOCKED keymap); decision keys
P/X/Space/C operate on whichever stop the cursor is on (segment or
snapshot) — wired by the host EditorPage, not this widget.

The whole strip sits inside a **fixed-height reveal host** so the canvas
geometry above is invariant under photo↔video sweeps in EditorPage
(the no-canvas-jump rule, lifted from PickerPage's compact_row Fix A
on 2026-06-15). The host's ``setVisible(False)`` hides the inner
content while keeping the reserved space.

The widget is pure presentation: it emits high-level signals, takes
state pushes (set_position / set_playing / set_duration / set_segment_info
/ set_volume / etc.), and the host wires those to:

* the embedded :class:`~mira.ui.media.photo_viewport.PhotoViewport`'s
  video_* API (play/pause/seek/volume/rate)
* the :class:`~mira.gateway.event_gateway.EventGateway`'s marker /
  segment / snapshot mutators
* the development panel's selection-scoped persistence
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QEvent, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from mira.ui.base.surface import (
    set_transport_playing,
    transport_button,
)
from mira.ui.design import (
    GLYPH_TO_END,
    GLYPH_TO_START,
    GLYPH_VOLUME,
    GLYPH_VOLUME_MUTED,
    ghost_button,
    select,
    tinted_svg_pixmap,
)
from mira.ui.edited.marker_timeline import MarkerTimeline
from mira.ui.i18n import tr
from mira.ui.palette import PALETTE

#: The strip's total reserved height — the timeline (~36) + tools row
#: (~36) + transport row (~36) + the QSS card margins/seams. Pinned so
#: the canvas above never shifts between a photo and a video landing
#: (the no-canvas-jump rule).
WORKSHOP_REVEAL_HEIGHT = 168

_SIDE_ICON_PX = 16
_MUTE_ICON_PX = 18


def _theme_mode() -> str:
    app = QApplication.instance()
    return (app.property("theme") if app else None) or "dark"


def _ink(mode: str) -> QColor:
    return QColor(PALETTE[mode]["ink"])


def _ink_soft(mode: str) -> QColor:
    return QColor(PALETTE[mode]["ink_soft"])


def _fmt_ms(ms: int) -> str:
    ms = max(0, int(ms))
    m, rem = divmod(ms, 60_000)
    s, msec = divmod(rem, 1000)
    return f"{m}:{s:02d}.{msec:03d}"


class VideoWorkshopBar(QWidget):
    """The composite workshop strip — timeline + tools + transport.

    The widget owns no data — the host (EditorPage) loads markers /
    segments / snapshots from the gateway and pushes them in via
    :meth:`set_timeline_model` / :meth:`set_position` / etc.
    """

    # ── Timeline (forwarded from the inner MarkerTimeline) ────────────
    seek_requested = pyqtSignal(int)            # ms; from clicks/drags on the bar
    segment_clicked = pyqtSignal(int)           # seg_index under the click
    marker_selected = pyqtSignal(str)           # marker id, "" = cleared
    marker_moved = pyqtSignal(str, int)         # id, new at_ms (drag commit)

    # ── Tools row ──
    add_marker_requested = pyqtSignal()         # M / button
    add_snapshot_requested = pyqtSignal()       # S / button
    remove_requested = pyqtSignal()             # Del / button
    toggle_status_requested = pyqtSignal()      # Space / C / button
    reset_all_requested = pyqtSignal()          # Reset everything
    clear_markers_requested = pyqtSignal()      # Clear markers only
    clear_snapshots_requested = pyqtSignal()    # Clear snapshots only

    # ── Transport row ──
    play_pause_requested = pyqtSignal()         # Tab / play button / play tile
    jump_start_requested = pyqtSignal()         # ⏮
    jump_end_requested = pyqtSignal()           # ⏭
    prev_stop_requested = pyqtSignal()          # ◀ Stop (markers ∪ snapshots ∪ ends)
    next_stop_requested = pyqtSignal()          # Stop ▶
    prev_frame_requested = pyqtSignal()         # ◀ Frame
    next_frame_requested = pyqtSignal()         # Frame ▶
    jump_to_marker_requested = pyqtSignal(str)  # marker id from the dropdown
    jump_to_snapshot_requested = pyqtSignal(str)  # snapshot item id

    # ── Per-segment extras (live + export) ─────────────────────────────
    mute_toggled = pyqtSignal(bool)
    volume_changed = pyqtSignal(int)            # 0..100 percent
    speed_changed = pyqtSignal(float)           # 0.25 / 0.5 / 1.0 / 1.5 / 2.0

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoWorkshopBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 0)
        outer.setSpacing(4)

        # ── Row 1: the timeline ──
        self._timeline = MarkerTimeline()
        self._timeline.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._timeline.setToolTip(tr(
            "The marker timeline: clips tile the video; green = picked "
            "for export, red = skipped. Click to move the cursor; drag a "
            "handle to move a marker."))
        self._timeline.seek_requested.connect(self.seek_requested.emit)
        self._timeline.segment_clicked.connect(self.segment_clicked.emit)
        self._timeline.marker_selected.connect(self.marker_selected.emit)
        self._timeline.marker_moved.connect(self.marker_moved.emit)
        outer.addWidget(self._timeline)

        # ── Row 2: tools row ──
        tools = QWidget()
        tools.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tr_layout = QHBoxLayout(tools)
        tr_layout.setContentsMargins(0, 0, 0, 0)
        tr_layout.setSpacing(6)

        self.marker_btn = ghost_button(tr("Marker"))
        self.marker_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.marker_btn.setToolTip(tr(
            "Place a marker at the playhead — it splits the clip under "
            "it; both halves keep its decision + development."))
        self.marker_btn.clicked.connect(self.add_marker_requested.emit)
        tr_layout.addWidget(self.marker_btn)

        self.snapshot_btn = ghost_button(tr("Snapshot"))
        self.snapshot_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.snapshot_btn.setToolTip(tr(
            "Place a snapshot at the playhead — it arrives picked and "
            "gets full photo treatment."))
        self.snapshot_btn.clicked.connect(self.add_snapshot_requested.emit)
        tr_layout.addWidget(self.snapshot_btn)

        self.remove_btn = ghost_button(tr("Remove"))
        self.remove_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.remove_btn.setToolTip(tr(
            "Remove the stop under the cursor — a snapshot or a marker. "
            "Start and end are permanent."))
        self.remove_btn.clicked.connect(self.remove_requested.emit)
        tr_layout.addWidget(self.remove_btn)

        self.toggle_btn = ghost_button(tr("Toggle Status"))
        self.toggle_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.toggle_btn.setToolTip(tr(
            "Pick / Skip — on a snapshot it toggles the snapshot; "
            "anywhere else it toggles the clip you are inside  (Space)"))
        self.toggle_btn.clicked.connect(self.toggle_status_requested.emit)
        tr_layout.addWidget(self.toggle_btn)

        self.reset_btn = ghost_button(tr("Reset ▾"))
        self.reset_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.reset_btn.setToolTip(tr(
            "Clear marks to start fresh. Choose what to reset."))
        reset_menu = QMenu(self.reset_btn)
        reset_menu.addAction(tr("Reset everything")).triggered.connect(
            self.reset_all_requested.emit)
        reset_menu.addAction(tr("Clear markers only")).triggered.connect(
            self.clear_markers_requested.emit)
        reset_menu.addAction(tr("Clear snapshots only")).triggered.connect(
            self.clear_snapshots_requested.emit)
        self.reset_btn.setMenu(reset_menu)
        tr_layout.addWidget(self.reset_btn)

        tr_layout.addStretch(1)

        # Mute + Volume + Speed cluster (per segment — live preview AND
        # export persistence; the host writes them to VideoAdjustment).
        self.mute_chk = QCheckBox(tr("Mute"))
        self.mute_chk.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mute_chk.toggled.connect(self.mute_toggled.emit)
        tr_layout.addWidget(self.mute_chk)

        self.vol_label = QLabel(tr("Vol"))
        self.vol_label.setObjectName("Sub")
        tr_layout.addWidget(self.vol_label)
        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(80)
        self.vol_slider.setFixedWidth(90)
        self.vol_slider.setObjectName("VideoVolume")
        self.vol_slider.valueChanged.connect(self.volume_changed.emit)
        tr_layout.addWidget(self.vol_slider)
        self._last_volume = self.vol_slider.value()

        self.speed_combo = select(["0.25×", "0.5×", "1×", "1.5×", "2×"])
        self.speed_combo.setObjectName("VideoSpeed")
        self.speed_combo.setCurrentText("1×")
        self.speed_combo.setFixedWidth(82)
        self.speed_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.speed_combo.currentTextChanged.connect(self._on_speed_text)
        tr_layout.addWidget(self.speed_combo)

        outer.addWidget(tools)

        # ── Row 3: transport row ──
        transport = QWidget()
        transport.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tx_layout = QHBoxLayout(transport)
        tx_layout.setContentsMargins(0, 0, 0, 0)
        tx_layout.setSpacing(6)

        self.start_btn = ghost_button(tr("⏮ Start"))
        self.start_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.start_btn.setToolTip(tr("Jump to the start."))
        self.start_btn.clicked.connect(self.jump_start_requested.emit)
        tx_layout.addWidget(self.start_btn)

        self.stop_prev_btn = ghost_button(tr("◀ Stop"))
        self.stop_prev_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.stop_prev_btn.setToolTip(tr(
            "Jump to the previous stop — markers and snapshots both "
            "count; start and end are stops too."))
        self.stop_prev_btn.clicked.connect(self.prev_stop_requested.emit)
        tx_layout.addWidget(self.stop_prev_btn)

        self.frame_prev_btn = ghost_button(tr("◀ Frame"))
        self.frame_prev_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.frame_prev_btn.setToolTip(tr("Step one frame back."))
        self.frame_prev_btn.clicked.connect(self.prev_frame_requested.emit)
        tx_layout.addWidget(self.frame_prev_btn)

        tx_layout.addStretch(1)

        self.markers_btn = ghost_button(tr("▼ Markers"))
        self.markers_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.markers_btn.setToolTip(tr(
            "Jump straight to a marker — every one listed with its time."))
        self._markers_menu = QMenu(self.markers_btn)
        self.markers_btn.setMenu(self._markers_menu)
        tx_layout.addWidget(self.markers_btn)

        self.play_btn = transport_button(tr("Play / pause  (Tab)"))
        self.play_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.play_btn.clicked.connect(self.play_pause_requested.emit)
        tx_layout.addWidget(self.play_btn)

        self.snapshots_btn = ghost_button(tr("📷 Snapshots"))
        self.snapshots_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.snapshots_btn.setToolTip(tr(
            "Jump straight to a snapshot — every one listed with its time."))
        self._snapshots_menu = QMenu(self.snapshots_btn)
        self.snapshots_btn.setMenu(self._snapshots_menu)
        tx_layout.addWidget(self.snapshots_btn)

        tx_layout.addStretch(1)

        self.frame_next_btn = ghost_button(tr("Frame ▶"))
        self.frame_next_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.frame_next_btn.setToolTip(tr("Step one frame forward."))
        self.frame_next_btn.clicked.connect(self.next_frame_requested.emit)
        tx_layout.addWidget(self.frame_next_btn)

        self.stop_next_btn = ghost_button(tr("Stop ▶"))
        self.stop_next_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.stop_next_btn.setToolTip(tr(
            "Jump to the next stop — markers and snapshots both count; "
            "start and end are stops too."))
        self.stop_next_btn.clicked.connect(self.next_stop_requested.emit)
        tx_layout.addWidget(self.stop_next_btn)

        self.end_btn = ghost_button(tr("End ⏭"))
        self.end_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.end_btn.setToolTip(tr("Jump to the end."))
        self.end_btn.clicked.connect(self.jump_end_requested.emit)
        tx_layout.addWidget(self.end_btn)

        self.seginfo_label = QLabel("")
        self.seginfo_label.setObjectName("Sub")
        self.seginfo_label.setToolTip(tr(
            "The clip under the cursor — segment index and duration."))
        tx_layout.addWidget(self.seginfo_label)

        outer.addWidget(transport)

        # Internal state mirrored from the host.
        self._duration_ms = 0
        self._fps_hint = 30.0
        self._frame_ms = 33

    # ── Public state pushes from the host ─────────────────────────────

    def set_timeline_model(
        self,
        *,
        markers: list[tuple[str, int]],
        bounds: list[tuple[int, int]],
        states: list[str],
        snapshots: list[tuple[int, str]],
        selected_seg: int,
        selected_marker: str = "",
        duration_ms: int,
    ) -> None:
        """Push the full timeline model — host calls this after every
        marker / segment / snapshot mutation (cheap; the widget repaints).

        Also refreshes the markers / snapshots dropdown menus so the
        Markers ▾ / Snapshots ▾ jump targets stay in lockstep with the
        timeline."""
        self._duration_ms = max(0, int(duration_ms))
        self._timeline.setRange(0, self._duration_ms)
        self._timeline.set_model(
            markers, bounds, states, selected_seg, selected_marker, snapshots,
        )
        self._refresh_markers_menu(markers)
        self._refresh_snapshots_menu(snapshots)

    def _refresh_markers_menu(self, markers: list[tuple[str, int]]) -> None:
        self._markers_menu.clear()
        if not markers:
            act = self._markers_menu.addAction(tr("No markers yet"))
            act.setEnabled(False)
            return
        for mid, ms in markers:
            act = self._markers_menu.addAction(_fmt_ms(ms))
            act.triggered.connect(
                lambda _checked=False, m=mid: self.jump_to_marker_requested.emit(m))

    def _refresh_snapshots_menu(self, snapshots: list[tuple]) -> None:
        # snapshots = list of (at_ms, state) — for jump-target labels we
        # only need the at_ms; the host resolves snapshot → item_id by
        # matching at_ms back to its loaded snapshot list.
        self._snapshots_menu.clear()
        if not snapshots:
            act = self._snapshots_menu.addAction(tr("No snapshots yet"))
            act.setEnabled(False)
            return
        for entry in snapshots:
            ms = entry[0]
            act = self._snapshots_menu.addAction(_fmt_ms(ms))
            # Pass the at_ms as a string id — the host resolves it back
            # to the snapshot's item id by lookup. (Snapshots are unique
            # per at_ms within a video by the spec.)
            act.triggered.connect(
                lambda _checked=False, m=ms: self.jump_to_snapshot_requested.emit(str(m)))

    def set_position(self, pos_ms: int) -> None:
        self._timeline.setValue(int(pos_ms))

    def set_duration(self, duration_ms: int) -> None:
        self._duration_ms = max(0, int(duration_ms))
        self._timeline.setRange(0, self._duration_ms)

    def set_playing(self, playing: bool) -> None:
        set_transport_playing(self.play_btn, playing)

    def set_segment_info(
        self, seg_index: int, seg_count: int, length_ms: int,
    ) -> None:
        if seg_count <= 0:
            self.seginfo_label.setText("")
            return
        # Display indexes 1-based ("Segment 1 of 1") to match the mockup.
        m, rem = divmod(max(0, int(length_ms)), 60_000)
        s = rem // 1000
        self.seginfo_label.setText(
            tr("Segment {n} of {t} · {m}:{s:02d}").replace(
                "{n}", str(seg_index + 1)).replace(
                "{t}", str(seg_count)).replace(
                "{m}", str(m)).replace(
                "{s:02d}", f"{s:02d}")
        )

    def set_fps_hint(self, fps: float) -> None:
        """Update the host's notion of one frame so prev/next-frame seeks
        land on the right millisecond. fps falls back to 30 if unknown."""
        self._fps_hint = max(1.0, float(fps) or 30.0)
        self._frame_ms = int(round(1000.0 / self._fps_hint))

    def frame_ms(self) -> int:
        return self._frame_ms

    def set_volume(self, percent: int) -> None:
        was = self.vol_slider.blockSignals(True)
        self.vol_slider.setValue(max(0, min(100, int(percent))))
        self.vol_slider.blockSignals(was)
        self._refresh_mute_state()

    def set_muted(self, muted: bool) -> None:
        was = self.mute_chk.blockSignals(True)
        self.mute_chk.setChecked(bool(muted))
        self.mute_chk.blockSignals(was)

    def set_speed(self, rate: float) -> None:
        was = self.speed_combo.blockSignals(True)
        # Snap to the nearest available label.
        labels = {0.25: "0.25×", 0.5: "0.5×", 1.0: "1×", 1.5: "1.5×", 2.0: "2×"}
        rounded = min(labels.keys(), key=lambda k: abs(k - float(rate)))
        self.speed_combo.setCurrentText(labels[rounded])
        self.speed_combo.blockSignals(was)

    def set_tools_enabled(
        self, *, marker: bool, snapshot: bool, remove: bool, toggle: bool,
    ) -> None:
        """Spec/59 §4 enable rules: Marker/Snapshot grey while the
        cursor sits on a stop (or at a permanent end); Remove greys
        off-stop / at the permanent endpoints; Toggle works anywhere."""
        self.marker_btn.setEnabled(marker)
        self.snapshot_btn.setEnabled(snapshot)
        self.remove_btn.setEnabled(remove)
        self.toggle_btn.setEnabled(toggle)

    # ── Internal handlers ─────────────────────────────────────────────

    def _on_speed_text(self, label: str) -> None:
        rate = {
            "0.25×": 0.25, "0.5×": 0.5, "1×": 1.0,
            "1.5×": 1.5, "2×": 2.0,
        }.get(label, 1.0)
        self.speed_changed.emit(rate)

    def _refresh_mute_state(self) -> None:
        # If the user dragged volume to 0, the mute checkbox tracks.
        muted = self.vol_slider.value() == 0
        if self.mute_chk.isChecked() != muted:
            self.set_muted(muted)


__all__ = ["VideoWorkshopBar", "WORKSHOP_REVEAL_HEIGHT"]
