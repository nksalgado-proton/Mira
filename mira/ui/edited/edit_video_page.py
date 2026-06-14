"""EditVideoPage — the video WORKSHOP (spec/56 slice 3).

The marker-partition model made real on one surface. The page loads a
SOURCE video (the whole-video item the user Picked); the bottom zone is
the marker timeline + the snapshot strip; the top stays development (the
same AdjustmentSurface as photos), scoped to the SELECTION:

  * **Segments** tile the timeline — consecutive markers define them
    (``core.video_segments.segment_bounds``); zero markers = ONE segment
    (whole-video export is not a special case). Each segment is its own
    child item: independently Pick/Skip (``phase_state`` edit, default
    Skip) and independently developed (its own ``VideoAdjustment`` row —
    look / style / creative_filter / crop / box_angle / aspect /
    rep_frame_ms / audio / speed / stabilise).
  * **Markers** are the cut points: M cuts at the playhead (the gateway
    splits the containing segment, both halves inherit), dragging a
    handle moves the cut (segment identity is order position — state +
    adjustments ride along), Del merges (the LEFT segment survives).
    Trimming IS moving markers; the trim deltas are gone.
  * **Snapshots** are photo-shaped children placed at the playhead (S);
    placing one auto-Picks it. Selecting a snapshot points the top panel
    at its photo ``Adjustment`` row — full photo treatment.
  * **Selection** follows the playhead (the segment under it) until a
    snapshot chip is explicitly selected; any transport/timeline action
    returns the selection to the playhead's segment.

Bytes never materialise here — Export (slice 4) walks picked segments +
picked snapshots and renders through their own adjustments.

Same shell contract as :class:`mira.ui.edited.edit_page.EditPage`:
``load(eg, bucket, *, nav_context, …)`` takes a synthetic single-video
bucket from a Day Grid centre-click. Edge nav emits
:attr:`navigate_at_edge` in ``day_grid`` context.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import (
    QEvent,
    QPointF,
    QRect,
    QRectF,
    QSizeF,
    Qt,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QCursor, QKeyEvent, QPainter, QPen, QPixmap
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.cull_state import STATE_KEPT as STATE_PICKED
from core.photo_auto import (
    compute_look_params,
    creative_filter_amount,
    resolve_filter_recipe,
)
from core.photo_decoder import decode_image
from core.photo_render import Params
from core.video_extract import extract_frame, probe_video
from core.video_segments import containing_segment, segment_bounds

from mira.picked import CullBucket, CullItem
from mira.gateway.event_gateway import EventGateway
from mira.store import models as m
from mira.ui.base.surface import (
    BaseEditSurface,
    back_button,
    help_button,
    info_label,
    populate_nav_row,
    set_transport_playing,
    transport_button,
)
from mira.ui.i18n import tr
from mira.ui.edited.adjustment_surface import (
    AdjustmentSurface, normalize_style,
)

log = logging.getLogger(__name__)


def mark_jump_target(
    pos_ms: int, direction: int, marks: list[int], frame_ms: int,
) -> Optional[int]:
    """The ◀ Mark / Mark ▶ landing position (docs/18 BOTTOM-3 nav,
    restored 2026-06-11 — Edit Surface design pass 1): ``marks`` is the
    sorted union of cuts, snapshots and the two permanent endpoints; one
    frame of tolerance keeps a repeat press from re-landing on the mark
    under the playhead. ``None`` = nothing further that way."""
    if not marks:
        return None
    if direction < 0:
        cand = [m for m in marks if m < pos_ms - frame_ms]
        return cand[-1] if cand else None
    cand = [m for m in marks if m > pos_ms + frame_ms]
    return cand[0] if cand else None

# Mirror Cull timeline palette for the rep-frame marker (same glyph the user
# saw at Cull).
_C_KEEP = QColor(0x2E, 0xA0, 0x6B)
_C_BASE = QColor(0x4A, 0x52, 0x5C)
_C_PLAYHEAD = QColor(0xFF, 0xFF, 0xFF)

def _fmt_ms(ms: int) -> str:
    if ms is None:
        return ""
    s, ms = divmod(int(ms), 1000)
    m, s = divmod(s, 60)
    return f"{m:d}:{s:02d}.{ms:03d}"


# --------------------------------------------------------------------------- #
# Marker timeline (spec/56 slice 3) — segments tile the bar, markers are the
# draggable cut handles, the playhead rides on top. Pure widget, no data
# calls; the page feeds it geometry + states and listens to its signals.
# --------------------------------------------------------------------------- #

# A REAL red (Nelson 2026-06-11: "clips that are not picked should be
# red and not the other color") — the old muted 0x8A4A4A wash read as
# brown next to the green.
_C_SKIP = QColor(0xC5, 0x30, 0x30)            # skipped clip wash
_C_MARKER = QColor(0xE8, 0xC5, 0x4A)          # cut-handle accent
_MARKER_GRAB_PX = 6                            # half-width of the hit zone


class _MarkerTimeline(QWidget):
    """The workshop timeline (spec/59 §5): clip bands (Pick/Skip washes
    from their markers' statuses), draggable marker handles, the
    permanent endpoint marks, snapshot glyphs, and the playhead."""

    seek_requested = pyqtSignal(int)           # ms — click/drag on a band
    segment_clicked = pyqtSignal(int)          # seg_index under the click
    marker_selected = pyqtSignal(str)          # marker id ("" = cleared)
    marker_moved = pyqtSignal(str, int)        # id, new at_ms (drag commit)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoScrub")
        self.setMinimumHeight(34)
        self._lo = 0
        self._hi = 0
        self._pos = 0
        self._markers: list[tuple[str, int]] = []      # (id, at_ms) ascending
        self._bounds: list[tuple[int, int]] = []       # per seg_index
        self._states: list[str] = []                   # per seg_index
        self._snapshots: list[tuple[int, str]] = []    # (at_ms, state)
        self._selected_seg = -1
        self._selected_marker = ""
        self._min_gap = 1
        self._drag_marker = ""                          # id while dragging
        self._drag_ms: Optional[int] = None             # live drag position

    # ── model in ────────────────────────────────────────────────────
    def setRange(self, lo: int, hi: int) -> None:
        self._lo, self._hi = int(lo), int(hi)
        self.update()

    def setValue(self, ms: int) -> None:
        self._pos = int(ms)
        self.update()

    def value(self) -> int:
        return self._pos

    def set_min_gap(self, ms: int) -> None:
        self._min_gap = max(1, int(ms))

    def set_model(
        self,
        markers: list[tuple[str, int]],
        bounds: list[tuple[int, int]],
        states: list[str],
        selected_seg: int,
        selected_marker: str = "",
        snapshots: list[tuple[int, str]] = (),
    ) -> None:
        self._markers = list(markers)
        self._bounds = list(bounds)
        self._states = list(states)
        self._snapshots = list(snapshots)
        self._selected_seg = int(selected_seg)
        self._selected_marker = selected_marker
        if self._drag_marker and self._drag_marker not in {
                mid for mid, _ in self._markers}:
            self._drag_marker = ""
            self._drag_ms = None
        self.update()

    # ── geometry ────────────────────────────────────────────────────
    def _x(self, ms: int) -> int:
        span = max(1, self._hi - self._lo)
        frac = min(1.0, max(0.0, (ms - self._lo) / span))
        return int(round(frac * max(1, self.width() - 1)))

    def _ms_at(self, x: float) -> int:
        frac = min(1.0, max(0.0, x / max(1, self.width())))
        return int(round(self._lo + frac * (self._hi - self._lo)))

    def _marker_at(self, x: float) -> str:
        """Marker id whose handle covers pixel ``x`` (nearest wins)."""
        best, best_d = "", _MARKER_GRAB_PX + 1
        for mid, ms in self._markers:
            d = abs(self._x(ms) - x)
            if d <= _MARKER_GRAB_PX and d < best_d:
                best, best_d = mid, d
        return best

    def _drag_bounds(self, marker_id: str) -> tuple[int, int]:
        """Legal ``at_ms`` window for a marker drag: strictly between its
        neighbours (and the implicit ends), one ``min_gap`` apart — the
        UI half of the gateway's may-not-cross rule."""
        ids = [mid for mid, _ in self._markers]
        i = ids.index(marker_id)
        lo = self._markers[i - 1][1] if i > 0 else self._lo
        hi = self._markers[i + 1][1] if i + 1 < len(self._markers) else self._hi
        return lo + self._min_gap, hi - self._min_gap

    # ── mouse ───────────────────────────────────────────────────────
    def mousePressEvent(self, ev):  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton and self._hi > self._lo:
            x = ev.position().x()
            mid = self._marker_at(x)
            if mid:
                self._drag_marker = mid
                self._drag_ms = None
                self.marker_selected.emit(mid)
            else:
                if self._selected_marker:
                    self.marker_selected.emit("")
                ms = self._ms_at(x)
                self.seek_requested.emit(ms)
                if self._bounds:
                    idx = max(0, min(
                        len(self._bounds) - 1,
                        sum(1 for b in self._bounds if b[0] <= ms) - 1))
                    self.segment_clicked.emit(idx)
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):  # noqa: N802
        if ev.buttons() & Qt.MouseButton.LeftButton and self._hi > self._lo:
            x = ev.position().x()
            if self._drag_marker:
                lo, hi = self._drag_bounds(self._drag_marker)
                if lo <= hi:
                    self._drag_ms = max(lo, min(self._ms_at(x), hi))
                    self.update()
            else:
                self.seek_requested.emit(self._ms_at(x))
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):  # noqa: N802
        if self._drag_marker and self._drag_ms is not None:
            self.marker_moved.emit(self._drag_marker, int(self._drag_ms))
        self._drag_marker = ""
        self._drag_ms = None
        super().mouseReleaseEvent(ev)

    # ── paint ───────────────────────────────────────────────────────
    def _marker_paint_ms(self, mid: str, ms: int) -> int:
        if mid == self._drag_marker and self._drag_ms is not None:
            return int(self._drag_ms)
        return ms

    def paintEvent(self, ev):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            w, h = self.width(), self.height()
            bar_t, bar_b = 8, h - 12
            p.fillRect(0, bar_t, w, bar_b - bar_t, _C_BASE)
            if self._hi <= self._lo:
                return
            # Segment bands — live drag shifts the shared boundary too.
            shift = {mid: self._marker_paint_ms(mid, ms)
                     for mid, ms in self._markers}
            edges = [self._lo, *shift.values(), self._hi]
            for idx, state in enumerate(self._states[:max(0, len(edges) - 1)]):
                x0, x1 = self._x(edges[idx]), self._x(edges[idx + 1])
                colour = QColor(_C_KEEP if state == "picked" else _C_SKIP)
                # Equal weight both ways — red is a first-class status
                # (not-marked-for-export), not a de-emphasis.
                colour.setAlpha(120)
                p.fillRect(x0, bar_t, max(1, x1 - x0), bar_b - bar_t, colour)
                if idx == self._selected_seg:
                    sel = QPen(_C_PLAYHEAD)
                    sel.setWidth(2)
                    p.setPen(sel)
                    p.drawRect(QRect(x0 + 1, bar_t + 1,
                                     max(2, x1 - x0 - 2), bar_b - bar_t - 2))
            # Snapshot glyphs — small squares below the bar, state-
            # coloured (spec/59 §5: snapshots are stops with their own
            # graphical representation on the timeline).
            for s_ms, s_state in self._snapshots:
                if not (self._lo <= s_ms <= self._hi):
                    continue
                sx = self._x(s_ms)
                p.setPen(QPen(QColor(0, 0, 0, 220), 1))
                p.setBrush(QColor(
                    _C_KEEP if s_state == "picked" else _C_SKIP))
                p.drawRect(sx - 4, bar_b + 1, 9, 9)
            # The permanent endpoint markers (auto start + end).
            for e_ms in (self._lo, self._hi):
                ex = self._x(e_ms)
                pen = QPen(QColor(_C_MARKER))
                pen.setWidth(2)
                p.setPen(pen)
                p.drawLine(ex, bar_t - 4, ex, bar_b + 4)
            # Marker handles (the user's draggable cut points).
            for mid, ms in self._markers:
                mx = self._x(self._marker_paint_ms(mid, ms))
                accent = QColor(_C_PLAYHEAD) if mid == self._selected_marker \
                    else QColor(_C_MARKER)
                pen = QPen(accent)
                pen.setWidth(3 if mid == self._selected_marker else 2)
                p.setPen(pen)
                p.drawLine(mx, bar_t - 4, mx, bar_b + 4)
                p.setBrush(accent)
                p.setPen(QPen(QColor(0, 0, 0, 200), 1))
                p.drawPolygon(
                    QPointF(mx - 5, bar_t - 4), QPointF(mx + 5, bar_t - 4),
                    QPointF(mx, bar_t + 3))
            # Playhead.
            px = self._x(self._pos)
            halo = QPen(QColor(0, 0, 0, 200))
            halo.setWidth(4)
            p.setPen(halo)
            p.drawLine(px, 0, px, h)
            core = QPen(_C_PLAYHEAD)
            core.setWidth(2)
            p.setPen(core)
            p.drawLine(px, 0, px, h)
        finally:
            p.end()


# --------------------------------------------------------------------------- #
# Read-only crop visualisation, in-scene (so it sits ABOVE QGraphicsVideoItem
# under Qt's own compositor — no Windows native-rendering Z-order issues).
# --------------------------------------------------------------------------- #


class _VideoCropItem(QGraphicsItem):
    """In-scene crop box (Black + white outline, rule-of-thirds grid).
    Mirrors :class:`~mira.ui.picked.crop_overlay.CropOverlay`'s visual
    style but as a ``QGraphicsItem`` instead of a ``QWidget`` — so it
    composites correctly on top of ``QGraphicsVideoItem``. Read-only: no
    drag, no resize, no signals (the interactive editor lives on the
    AdjustmentSurface canvas in Adjust mode).

    Coordinate spaces:
      * ``video_rect`` — scene coords where the video paints
      * ``crop_norm`` — ``(x, y, w, h)`` in ``[0, 1]`` over the source image
    """

    def __init__(self) -> None:
        super().__init__()
        self._video_rect = QRectF()
        self._crop_norm: Optional[tuple[float, float, float, float]] = None
        self._angle = 0.0
        # Paint above the video item.
        self.setZValue(10)

    def set_video_rect(self, rect: QRectF) -> None:
        self.prepareGeometryChange()
        self._video_rect = QRectF(rect)
        self.update()

    def set_crop_norm(
        self, rect: Optional[tuple[float, float, float, float]],
    ) -> None:
        self._crop_norm = (
            tuple(rect) if rect is not None else None  # type: ignore[assignment]
        )
        self.update()

    def set_angle(self, degrees: float) -> None:
        self._angle = float(degrees)
        self.update()

    def boundingRect(self) -> QRectF:                # noqa: N802
        return self._video_rect

    def paint(self, painter: QPainter, _option, _widget=None) -> None:
        if self._crop_norm is None or self._video_rect.isEmpty():
            return
        x, y, w, h = self._crop_norm
        vr = self._video_rect
        rect = QRectF(
            vr.x() + x * vr.width(),
            vr.y() + y * vr.height(),
            w * vr.width(),
            h * vr.height(),
        )

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        if self._angle:
            c = rect.center()
            painter.translate(c)
            painter.rotate(self._angle)
            painter.translate(-c)

        # Black outline + bright inner stroke — same scheme as CropOverlay.
        outline_pen = QPen(QColor(0, 0, 0, 220))
        outline_pen.setWidth(3)
        painter.setPen(outline_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
        inner_pen = QPen(QColor(255, 255, 255, 240))
        inner_pen.setWidth(1)
        painter.setPen(inner_pen)
        painter.drawRect(rect)

        # Rule-of-thirds grid.
        thirds_pen = QPen(QColor(255, 255, 255, 110))
        thirds_pen.setWidth(1)
        painter.setPen(thirds_pen)
        for i in (1, 2):
            gx = rect.left() + i * rect.width() / 3.0
            painter.drawLine(
                QPointF(gx, rect.top()), QPointF(gx, rect.bottom()))
            gy = rect.top() + i * rect.height() / 3.0
            painter.drawLine(
                QPointF(rect.left(), gy), QPointF(rect.right(), gy))


# --------------------------------------------------------------------------- #
# EditVideoPage
# --------------------------------------------------------------------------- #


class EditVideoPage(QWidget):
    """Lean per-clip video editor for Process — on BaseEditSurface.

    spec/42 (Nelson 2026-06-09): composes the same chassis as
    :class:`EditPage` so transitioning photo → video feels seamless.
    The TOOLS_PANEL above MEDIA hosts the AdjustmentSurface tools
    (TONE / Vibrance / Crop / action row), *disabled* until the user
    clicks "Adjust based on this frame" — that pauses the player,
    extracts the current frame, swaps MEDIA from QVideoWidget to the
    AdjustmentSurface canvas, and enables the tools. "Back to video"
    returns to the player. Adjustments auto-persist to VideoAdjustment
    on every edit (no Keep/Cancel gate).

    Shell contract: ``back_requested`` / ``fullscreen_changed`` +
    :attr:`navigate_at_edge` (mirrors :class:`EditPage`).  No
    ``prev_bucket_requested`` / ``next_bucket_requested`` — the rebuilt
    nav stops at the bucket level (the host owns the day-cell cursor).
    """

    is_process_surface = True

    # ── Shell contract signals ───────────────────────────────────────
    back_requested = pyqtSignal()
    fullscreen_changed = pyqtSignal(bool)
    navigate_at_edge = pyqtSignal(int)      # ±1
    finished = pyqtSignal()                  # EOF

    _SPEEDS = (0.25, 0.5, 1.0, 2.0, 4.0)
    # Selectable fade durations (seconds). Driving the audio_fade_ms column
    # in 0.5-second steps from 0.5 s to 5 s covers the entire typical fade
    # range (longer fades are export-only edge cases — bump if needed).
    _FADE_SECONDS = (0.5, 1.0, 1.5, 2.0, 3.0, 5.0)
    # Stabilise intensity 1..5 → the normalised ``stabilise`` schema value.
    # 1 = lightest correction, 5 = max. The engine re-scales to vidstab's
    # shakiness 1..10 / smoothing 1..60 ranges.
    _STAB_LEVELS = ((1, 0.2), (2, 0.4), (3, 0.6), (4, 0.8), (5, 1.0))

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("EditVideoPage")

        # ── Per-clip state (gateway-backed) ───────────────────────────
        self._eg: Optional[EventGateway] = None
        self._bucket: Optional[CullBucket] = None
        self._items: list[CullItem] = []
        self._index = 0
        self._nav_context: str = "day_grid"
        self._item_id: str = ""
        self._source: Optional[Path] = None
        self._frames_dir: Optional[Path] = None
        self._processed_dir: Optional[Path] = None
        self._day_label = ""
        self._frame_ms = 33
        self._src_fps = 30.0
        self._duration_ms = 0
        # Source video pixel size — needed so the read-only crop overlay
        # painted over QVideoWidget can map normalised crop coords onto
        # the letterboxed video rect.
        self._video_w = 0
        self._video_h = 0
        self._poster_shown = False
        self._immersive = False
        self._pending_rep_ms = 0          # frame the AdjustmentSurface edits
        # Modeless development (spec/59 §3): no Watch/Adjust mode — the
        # cursor IS the selection. ``_dev_open`` tracks whether MEDIA
        # currently shows the development canvas; ``_dev_latch`` dedupes
        # re-opens for the same (kind, target, frame).
        self._dev_open = False
        self._dev_latch: Optional[tuple] = None
        self._last_ctx_seg = ""                    # containing-clip tracker

        # spec/66 §1.1 — the per-clip export trigger retired here; the
        # batch export (spec/60) lives on the Export surface and walks
        # picked segments + snapshots. No per-page export worker state.

        # ── Workshop model (spec/56 data; spec/59 surface) ─────────────
        # Markers + segment child items + snapshots of the LOADED source
        # video; the CURSOR drives what the top panel + tool boxes edit.
        self._markers: list[m.VideoMarker] = []
        self._segment_items: list[m.Item] = []
        self._seg_bounds: list[tuple[int, int]] = []
        self._seg_states: dict[str, str] = {}     # segment item_id → state
        self._snapshots: list[m.VideoSnapshot] = []
        self._snap_states: dict[str, str] = {}
        self._workshop_ready = False               # segments birthed OK
        # The configured edit default (spec/59 export-status, "born
        # green" out of the box) — the host injects it via
        # :meth:`set_phase_default`; standalone/tests keep Skip.
        self._phase_default = "skipped"
        # spec/59 §8 Exported watermark gate (host-injected setting);
        # only developed snapshots can wear it on this page.
        self._watermark_enabled = True

        # Video-only tools (persisted per segment on VideoAdjustment columns).
        # _trim_* stay zeroed: _eff_range + the legacy export shim read them.
        self._trim_start = 0
        self._trim_end = 0
        self._mute = False
        self._volume = 1.0
        self._fade_ms = 0
        self._speed = 1.0
        self._stabilise = 0.0
        self._tools_loading = False
        # Suppress the AdjustmentSurface.changed → persist hook while the
        # host is loading state in (set_state during _open_development);
        # the host is the source of truth, not the user.
        self._surface_loading = False

        self._build_ui()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ── Construction (UI; no data calls) ─────────────────────────────
    # spec/42 (Nelson 2026-06-09) — EditVideoPage composes BaseProcess-
    # Surface (the same chassis as EditPage) so transitioning photo →
    # video feels seamless. The tools panel above MEDIA is the
    # AdjustmentSurface tools widget, *disabled by default*; the user
    # clicks "Adjust based on this frame" to extract a frame, swap MEDIA
    # to the AdjustmentSurface canvas with live preview, and enable the
    # tools. Click "Back to video" to return to the player. NAV centre
    # hosts the video transport + the Adjust / Back-to-video toggle.
    # COMPACT_ROW hosts the scrub timeline and the per-clip audio /
    # speed / stabilise controls.

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._chrome = BaseEditSurface()
        outer.addWidget(self._chrome)

        # AdjustmentSurface holds the tools widget (TONE / Vibrance / Crop
        # / Copy-Paste-Compare-Preview-Reset) AND the canvas that the
        # extracted-frame preview renders to. Both are reparented out of
        # the surface as needed.
        self._surface = AdjustmentSurface()
        # spec/54 §7 #1 (Nelson 2026-06-10): video shares the Looks
        # chooser, UNCALIBRATED — the photo-fitted constants run on the
        # rep frame. The old AUTO/Style hiding died with the AUTO toggle.
        self._surface.changed.connect(self._on_surface_changed)
        self._surface.style_decided.connect(self._on_style_decided)
        # spec/59 export-status: the border is a click target again —
        # toggles the cursor target's marked-for-export status (the
        # same works-anywhere rule as the Toggle Status button).
        self._chrome.media_border_clicked.connect(self._on_toggle_status)

        # Video output goes through a QGraphicsScene/View so the read-only
        # crop box can sit ABOVE the video under Qt's own graphics
        # compositor (Nelson 2026-06-09). The earlier QVideoWidget + child-
        # widget overlay approach failed on Windows: QVideoWidget uses
        # platform-native rendering and Qt child widgets often paint
        # underneath the video surface. A QGraphicsVideoItem + a custom
        # QGraphicsItem for the crop both live inside the same scene, so
        # Z-order is internal to Qt and always honoured.
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)

        self._video_scene = QGraphicsScene(self)
        self._video_view = QGraphicsView(self._video_scene, self)
        self._video_view.setObjectName("VideoHost")
        self._video_view.setRenderHints(QPainter.RenderHint.Antialiasing)
        self._video_view.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._video_view.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._video_view.setFrameShape(QGraphicsView.Shape.NoFrame)
        self._video_view.setBackgroundBrush(QColor(0, 0, 0))
        # QGraphicsView grabs StrongFocus by default — that's what swallows
        # our keyboard shortcuts after the first click. NoFocus keeps the
        # page widget focused, so Space / ← / → / F11 / Esc keep working
        # after any click on the video surface.
        self._video_view.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._video_item = QGraphicsVideoItem()
        # KeepAspectRatio = letterbox so the source aspect is preserved.
        self._video_item.setAspectRatioMode(
            Qt.AspectRatioMode.KeepAspectRatio)
        self._video_scene.addItem(self._video_item)
        self._player.setVideoOutput(self._video_item)

        # spec/59 black-frame guarantee — the load poster (the cached
        # Day-Grid thumb) painted in-scene ABOVE the video item until
        # the decoder delivers its first real frame. Same compositor
        # reasoning as the crop item: in-scene Z is always honoured.
        self._poster_item = QGraphicsPixmapItem()
        self._poster_item.setVisible(False)
        self._video_scene.addItem(self._poster_item)
        self._poster_src: Optional[QPixmap] = None
        self._video_item.videoSink().videoFrameChanged.connect(
            self._on_first_video_frame)

        # Read-only crop visualisation — same scene, higher Z.
        self._video_crop_item = _VideoCropItem()
        self._video_crop_item.setVisible(False)
        self._video_scene.addItem(self._video_crop_item)

        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.mediaStatusChanged.connect(self._on_media_status)

        # Track view resizes so we can re-fit the video item to the viewport
        # and re-position the crop overlay onto the new video rect.
        self._video_view.installEventFilter(self)

        self._build_top_bar()
        self._build_tools_panel()
        self._build_compact_row()
        self._build_nav()

        # Start with the player in MEDIA and the top tools HIDDEN with
        # their space preserved (spec/59 §2.1a) — the cursor decides
        # everything from here on.
        self._chrome.set_media(self._video_view)
        tools = self._surface.tools_widget()
        sp = tools.sizePolicy()
        sp.setRetainSizeWhenHidden(True)
        tools.setSizePolicy(sp)
        tools.setVisible(False)

    def _build_top_bar(self) -> None:
        """TOP_BAR: Back · info · stretch · ⛶ · ?

        spec/66 §1.1 (2026-06-14) — no inline export progress, no per-clip
        Export button. Batch export moved to the Export surface; the app-
        level BatchProgressLine below the menubar shows running jobs.
        """
        tb = self._chrome.top_bar.layout()

        self._back_btn = back_button(tr("⟵ Back"))
        self._back_btn.setToolTip(tr("Return to the day cell  (Esc)"))
        self._back_btn.clicked.connect(self.back_requested.emit)
        tb.addWidget(self._back_btn)

        self._info = info_label("")
        tb.addWidget(self._info)

        tb.addStretch(1)

        # Fullscreen toggle — visible affordance so the user doesn't have
        # to know F11. The same handler is wired to the F11 key in
        # ``keyPressEvent``.
        self._fs_btn = self._btn(tr("⛶"))
        self._fs_btn.setToolTip(tr("Toggle full-screen (F11)."))
        self._fs_btn.clicked.connect(self._toggle_fullscreen)
        tb.addWidget(self._fs_btn)

        self._help_btn = help_button()
        self._help_btn.setToolTip(tr("Keyboard shortcuts  (F1)"))
        self._help_btn.clicked.connect(self._show_shortcuts)
        tb.addWidget(self._help_btn)

    def _build_tools_panel(self) -> None:
        """TOOLS_PANEL: AdjustmentSurface tools widget + an AUDIO box and
        a VIBRATIONS box below CROP. They sit in separate group frames
        (not under one shared box) because Fade is an audio operation
        and Stabilise is a video / motion one — different domains
        (Nelson 2026-06-09)."""
        self._chrome.tools_panel.layout().addWidget(
            self._surface.tools_widget(), stretch=1)
        # Line 2 of the top grid (Nelson 2026-06-11): Audio under
        # Style, Vibrations under Filter — contents unchanged.
        self._surface.set_video_extra_boxes(
            self._build_audio_box(), self._build_vibrations_box())
        # Same min-height bump as EditPage — without it the panel's
        # 232 px floor leaves the AdjustmentSurface content overlapping.
        panel = self._chrome.tools_panel
        needed_h = panel.layout().minimumSize().height()
        if needed_h > panel.minimumHeight():
            panel.setMinimumHeight(needed_h)

    def _set_adjustment_tools_enabled(self, enabled: bool) -> None:
        """Drive EVERY AdjustmentSurface tool on or off — the video
        extras included (spec/59 §2.1b: a Skipped stop greys ALL the
        controls; the old export-settings exemption died)."""
        self._surface.set_tools_enabled(enabled)
        if enabled:
            # Secondary controls follow their toggle state.
            self._fade_combo.setEnabled(self._fade_btn.isChecked())
            self._stab_combo.setEnabled(self._stab_btn.isChecked())

    def _make_group_box(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        """Build a ProcessGroupBox with a title label (matches the look of
        ADJUSTMENTS / CROP on the AdjustmentSurface)."""
        box = QFrame()
        box.setObjectName("ProcessGroupBox")
        col = QVBoxLayout(box)
        col.setContentsMargins(10, 4, 10, 4)
        col.setSpacing(4)
        lbl = QLabel(title)
        lbl.setObjectName("ProcessGroupTitle")
        col.addWidget(lbl)
        return box, col

    def _build_audio_box(self) -> QWidget:
        """AUDIO group box — the Fade toggle + duration dropdown.
        Export-only (QMediaPlayer has no audio-fade hook)."""
        box, col = self._make_group_box(tr("Audio"))
        row = QHBoxLayout()
        row.setSpacing(8)

        self._fade_btn = QPushButton(tr("Fade"))
        self._fade_btn.setObjectName("FeatureToggle")
        self._fade_btn.setCheckable(True)
        self._fade_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._fade_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._fade_btn.setToolTip(tr(
            "Toggle audio fade in + out. Applies on export only — "
            "QMediaPlayer can't preview fades."))
        self._fade_btn.toggled.connect(self._on_fade_toggled)
        row.addWidget(self._fade_btn)

        self._fade_combo = QComboBox()
        self._fade_combo.setObjectName("VideoExtraCombo")
        self._fade_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._fade_combo.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        for sec in self._FADE_SECONDS:
            self._fade_combo.addItem(f"{sec:g} s", sec)
        try:
            self._fade_combo.setCurrentIndex(self._FADE_SECONDS.index(1.0))
        except ValueError:
            self._fade_combo.setCurrentIndex(0)
        self._fade_combo.setToolTip(tr(
            "Fade duration in seconds. Only acts when Fade is on."))
        self._fade_combo.currentIndexChanged.connect(
            self._on_fade_duration_changed)
        row.addWidget(self._fade_combo, stretch=1)
        col.addLayout(row)

        # Secondary control disabled until its toggle is on. The extras
        # follow the spec/59 visibility rules like every other top
        # control (the export-settings exemption died).
        self._fade_combo.setEnabled(False)
        return box

    def _build_vibrations_box(self) -> QWidget:
        """VIBRATIONS group box — the Stabilise toggle + intensity
        dropdown. Export-only (vidstab runs its analysis pass off-line)."""
        box, col = self._make_group_box(tr("Vibrations"))
        row = QHBoxLayout()
        row.setSpacing(8)

        self._stab_btn = QPushButton(tr("Stabilise"))
        self._stab_btn.setObjectName("FeatureToggle")
        self._stab_btn.setCheckable(True)
        self._stab_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._stab_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._stab_btn.setToolTip(tr(
            "Toggle vidstab stabilisation. Applies on export only — the "
            "analysis runs off-line."))
        self._stab_btn.toggled.connect(self._on_stab_toggled)
        row.addWidget(self._stab_btn)

        self._stab_combo = QComboBox()
        self._stab_combo.setObjectName("VideoExtraCombo")
        self._stab_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._stab_combo.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        for level, norm in self._STAB_LEVELS:
            self._stab_combo.addItem(str(level), norm)
        self._stab_combo.setCurrentIndex(2)
        self._stab_combo.setToolTip(tr(
            "Stabilisation intensity from 1 (light) to 5 (strong). "
            "Only acts when Stabilise is on."))
        self._stab_combo.currentIndexChanged.connect(self._on_stab_changed)
        row.addWidget(self._stab_combo, stretch=1)
        col.addLayout(row)

        self._stab_combo.setEnabled(False)
        return box

    def _build_compact_row(self) -> None:
        """COMPACT_ROW: the marker timeline on top; the snapshot strip +
        live-preview controls (Mute · Vol · Speed) beneath it."""
        row = self._chrome.compact_row.layout()

        stack_host = QWidget()
        stack = QVBoxLayout(stack_host)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(2)

        self._scrub = _MarkerTimeline()
        self._scrub.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scrub.setToolTip(tr(
            "The marker timeline: clips tile the video; green = picked "
            "for export, red = skipped. Click to move the cursor; drag a "
            "handle to move a marker; M places one at the playhead."))
        self._scrub.seek_requested.connect(self._on_timeline_seek)
        self._scrub.segment_clicked.connect(self._on_segment_clicked)
        self._scrub.marker_selected.connect(self._on_marker_selected)
        self._scrub.marker_moved.connect(self._on_marker_moved)
        stack.addWidget(self._scrub)

        # ── The MIDDLE line (spec/59 §4): Stop management on the left,
        # the tenants (snapshot strip · Mute · Vol · Speed) to the right
        # for now — space is judged at the eyeball.
        below = QHBoxLayout()
        below.setContentsMargins(0, 0, 0, 0)
        below.setSpacing(6)

        self._create_marker_btn = self._btn(tr("Marker"))
        self._create_marker_btn.setToolTip(tr(
            "Place a marker at the playhead — it splits the clip under "
            "it; both halves keep its decision + development. Greyed "
            "while the cursor sits on a stop  (M)"))
        self._create_marker_btn.clicked.connect(self._on_cut)
        below.addWidget(self._create_marker_btn)
        self._create_snap_btn = self._btn(tr("Snapshot"))
        self._create_snap_btn.setToolTip(tr(
            "Place a snapshot at the playhead — it arrives picked and "
            "gets full photo treatment. Greyed while the cursor sits on "
            "a stop  (S)"))
        self._create_snap_btn.clicked.connect(self._on_snapshot)
        below.addWidget(self._create_snap_btn)
        self._remove_btn = self._btn(tr("Remove"))
        self._remove_btn.setToolTip(tr(
            "Remove the stop under the cursor — a snapshot or a marker. "
            "Start and end are permanent  (Del)"))
        self._remove_btn.clicked.connect(self._on_remove_at_playhead)
        below.addWidget(self._remove_btn)
        self._toggle_btn = self._btn(tr("Toggle Status"))
        self._toggle_btn.setToolTip(tr(
            "Pick / Skip — on a snapshot it toggles the snapshot; "
            "anywhere else it toggles the clip you are inside (the "
            "marker that starts it)  (Space / C)"))
        self._toggle_btn.clicked.connect(self._on_toggle_status)
        below.addWidget(self._toggle_btn)
        self._reset_menu_btn = self._btn(tr("Reset"))
        self._reset_menu_btn.setToolTip(tr(
            "Clear marks to start fresh. Choose what to reset."))
        reset_menu = QMenu(self._reset_menu_btn)
        reset_menu.addAction(tr("Reset everything")).triggered.connect(
            lambda: self._reset_everything())
        reset_menu.addAction(tr("Clear markers only")).triggered.connect(
            lambda: self._clear_markers())
        reset_menu.addAction(tr("Clear snapshots only")).triggered.connect(
            lambda: self._clear_snapshots())
        self._reset_menu_btn.setMenu(reset_menu)
        below.addWidget(self._reset_menu_btn)

        # The chip strip died with Nelson's 2026-06-11 eyeball — direct
        # snapshot access lives in the NAV dropdowns now (it scales);
        # the timeline glyphs remain the visual representation.
        below.addStretch(1)

        # Mute — live + export (per SEGMENT now, like every tool here).
        self._mute_chk = QCheckBox(tr("Mute"))
        self._mute_chk.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._mute_chk.toggled.connect(self._on_mute_toggled)
        below.addWidget(self._mute_chk)

        # Volume — live + export.
        below.addWidget(QLabel(tr("Vol")))
        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._vol_slider.setRange(0, 200)
        self._vol_slider.setValue(100)
        self._vol_slider.setFixedWidth(90)
        self._vol_slider.setToolTip(tr(
            "Playback volume of the selected segment. Affects live "
            "preview AND export."))
        self._vol_slider.valueChanged.connect(self._on_volume_changed)
        below.addWidget(self._vol_slider)

        # Speed — live + export.
        below.addWidget(QLabel(tr("Speed")))
        self._speed_combo = QComboBox()
        self._speed_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._speed_combo.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        for sp in self._SPEEDS:
            self._speed_combo.addItem(f"{sp:g}×", sp)
        self._speed_combo.setCurrentIndex(self._SPEEDS.index(1.0))
        self._speed_combo.setToolTip(tr(
            "Slow-motion / time-lapse factor for the selected segment. "
            "Affects live preview AND export."))
        self._speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        below.addWidget(self._speed_combo)

        stack.addLayout(below)

        row.addWidget(stack_host, stretch=1)

    def _build_nav(self) -> None:
        """NAV (spec/59 §4, Nelson's eyeball 2026-06-11): the transport
        hugs the sides near Previous/Next; the freed centre hosts the
        Markers / Snapshots dropdowns either side of Play — direct
        access that scales past what the old chip strip could fit.

        ← Previous · ⏮ Start ◀ Stop ◀ Frame · … · ▼ Markers · ▶/⏸ ·
        📷 Snapshots · … · Frame ▶ Stop ▶ End ⏭ · Next →"""
        centre = QWidget()
        crow = QHBoxLayout(centre)
        crow.setContentsMargins(0, 0, 0, 0)
        crow.setSpacing(6)

        # U+FE0E forces TEXT presentation on ⏮/⏭ — without it Windows
        # renders the emoji glyph with its own coloured background
        # (Nelson's eyeball: "a blue background only they have").
        self._b_start = self._btn(tr("⏮︎ Start"))
        self._b_start.setToolTip(tr("Jump to the start."))
        self._b_start.clicked.connect(
            lambda: self._on_timeline_seek(self._eff_range()[0]))
        crow.addWidget(self._b_start)
        self._b_stop_prev = self._btn(tr("◀ Stop"))
        self._b_stop_prev.setToolTip(tr(
            "Jump to the previous stop — markers and snapshots both "
            "count; start and end are stops too."))
        self._b_stop_prev.clicked.connect(lambda: self._jump_stop(-1))
        crow.addWidget(self._b_stop_prev)
        self._b_fprev = self._btn(tr("◀ Frame"))
        self._b_fprev.setToolTip(tr("Step one frame back  (←)"))
        self._b_fprev.clicked.connect(lambda: self._step(-1))
        crow.addWidget(self._b_fprev)

        crow.addStretch(1)
        self._markers_menu_btn = self._btn(tr("▼ Markers"))
        self._markers_menu_btn.setToolTip(tr(
            "Jump straight to a marker — every one listed with its "
            "time."))
        self._markers_menu = QMenu(self._markers_menu_btn)
        self._markers_menu_btn.setMenu(self._markers_menu)
        crow.addWidget(self._markers_menu_btn)
        self._nav_play = transport_button(tr("Play / pause  (Space)"))
        self._nav_play.clicked.connect(self._toggle_play)
        crow.addWidget(self._nav_play)
        self._snapshots_menu_btn = self._btn(tr("📷 Snapshots"))
        self._snapshots_menu_btn.setToolTip(tr(
            "Jump straight to a snapshot — every one listed with its "
            "time."))
        self._snapshots_menu = QMenu(self._snapshots_menu_btn)
        self._snapshots_menu_btn.setMenu(self._snapshots_menu)
        crow.addWidget(self._snapshots_menu_btn)
        crow.addStretch(1)

        self._b_fnext = self._btn(tr("Frame ▶"))
        self._b_fnext.setToolTip(tr("Step one frame forward  (→)"))
        self._b_fnext.clicked.connect(lambda: self._step(1))
        crow.addWidget(self._b_fnext)
        self._b_stop_next = self._btn(tr("Stop ▶"))
        self._b_stop_next.setToolTip(tr(
            "Jump to the next stop — markers and snapshots both "
            "count; start and end are stops too."))
        self._b_stop_next.clicked.connect(lambda: self._jump_stop(+1))
        crow.addWidget(self._b_stop_next)
        self._b_end = self._btn(tr("End ⏭︎"))
        self._b_end.setToolTip(tr("Jump to the end."))
        self._b_end.clicked.connect(
            lambda: self._on_timeline_seek(
                self._eff_range()[1] - self._frame_ms))
        crow.addWidget(self._b_end)

        # Modeless development (spec/59 §3): the Adjust / Reopen / Adopt
        # buttons died with the Adjust mode — landing the cursor on a
        # Picked stop IS development; the transport is always visible.
        nav = populate_nav_row(
            self._chrome, with_buckets=False, centre_widget=centre)
        # Let the centre span the whole row — its internal stretches
        # push the transport groups out toward Previous / Next and free
        # the middle for the dropdowns (Nelson 2026-06-11).
        self._chrome.nav.layout().setStretchFactor(centre, 1)
        nav.prev.clicked.connect(lambda: self._emit_edge(-1))
        nav.next.clicked.connect(lambda: self._emit_edge(+1))
        self._nav_prev = nav.prev
        self._nav_next = nav.next

    def _btn(self, text: str) -> QPushButton:
        b = QPushButton(text)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        return b

    # ── load ─────────────────────────────────────────────────────────

    def load(
        self,
        eg: EventGateway,
        bucket: CullBucket,
        *,
        entry_override: Optional[int] = None,
        nav_context: str = "day_grid",
        bucket_index: int = 1,
        bucket_count: int = 1,
        processed_dir: Optional[Path] = None,
        day_label: str = "",
        frames_dir: Optional[Path] = None,
    ) -> None:
        """Open the bucket's clip(s) for Process editing.

        In v1 the cluster scanner never produces a video cluster, so ``bucket``
        is the synthetic single-clip bucket the host built for the centre-clicked
        Day Grid cell.  The cluster branch (multi-clip bucket) is defensive only
        — it works if the scanner ever emits one.

        ``processed_dir`` / ``day_label`` route the export output under
        ``<processed_dir>/<day_label>/…``.  ``frames_dir`` is where extracted
        representative frames are cached (defaults to a per-event scratch dir).
        """
        self._eg = eg
        self._bucket = bucket
        self._items = list(bucket.items)
        self._nav_context = nav_context
        self._processed_dir = Path(processed_dir) if processed_dir else None
        self._day_label = str(day_label or "")
        if frames_dir is not None:
            self._frames_dir = Path(frames_dir)
        elif eg is not None and eg.event_root is not None:
            self._frames_dir = Path(eg.event_root) / ".mira_cache" / "video_frames"
        if not self._items:
            return
        if entry_override is not None:
            n = len(self._items)
            self._index = (n - 1) if entry_override < 0 else max(
                0, min(entry_override, n - 1))
        else:
            self._index = 0
        self._show(self._index)
        self.setFocus()

    # ── Show one clip ────────────────────────────────────────────────

    def _current_item(self) -> Optional[CullItem]:
        if not self._items:
            return None
        return self._items[self._index]

    def _show(self, index: int) -> None:
        if not self._items:
            return
        index = max(0, min(index, len(self._items) - 1))
        self._index = index
        ci = self._items[index]
        self._item_id = ci.item_id
        self._source = ci.path

        self._probe_and_mount(ci)
        self._load_workshop()
        # First-mount fit: the viewport may not have its final size yet
        # (Nelson's eyeball: "video started very small at the centre" —
        # a window nudge fixed it). Defer one refit past the pending
        # layout pass — the locked singleShot(0) pattern.
        QTimer.singleShot(0, self._fit_video_to_view)

        s, _e = self._eff_range()
        self._apply_eff_range_to_scrub()
        self._scrub.setValue(s)

        # New video → the player shows; the cursor (at the start marker)
        # decides the rest via _reload_model's context refresh.
        self._close_development()
        self._refresh_info()
        self._refresh_media_border()
        self._refresh_video_crop_overlay()
        self.setFocus()

    def _probe_and_mount(self, ci: CullItem) -> None:
        """Probe fps/duration/dimensions + point the player at the file.
        Kept separate from :meth:`_show` so headless tests can stub the
        player/probe half and exercise the workshop half for real."""
        self._frame_ms = 33
        self._src_fps = 30.0
        self._duration_ms = 0
        self._video_w = 0
        self._video_h = 0
        try:
            meta = probe_video(ci.path)
            fps = meta.fps if meta and meta.fps and meta.fps > 0 else 30.0
            self._src_fps = fps
            self._frame_ms = max(1, int(round(1000.0 / fps)))
            if meta and meta.duration_ms:
                self._duration_ms = int(meta.duration_ms)
            if meta:
                self._video_w = int(meta.display_width or meta.width or 0)
                self._video_h = int(meta.display_height or meta.height or 0)
        except Exception:  # noqa: BLE001
            log.exception("probe failed for %s", ci.path)

        self._poster_shown = False
        self._player.stop()
        # spec/59 black-frame guarantee — poster the load window with
        # the cached grid thumb (cache-only; honest no-op when the
        # grid hasn't populated it).
        self._arm_poster(ci)
        self._player.setSource(QUrl.fromLocalFile(str(ci.path)))

    def _arm_poster(self, ci: CullItem) -> None:
        self._poster_src = None
        self._poster_item.setVisible(False)
        if self._eg is None:
            return
        try:
            it = self._eg.item(ci.item_id)
            if it is None or not it.origin_relpath:
                return
            from core.thumb_cache import poster_path_if_cached
            p = poster_path_if_cached(
                Path(self._eg.event_root), Path(it.origin_relpath))
            if p is None:
                return
            pm = QPixmap(str(p))
            if pm.isNull():
                return
            self._poster_src = pm
            self._refit_poster()
            self._poster_item.setVisible(True)
        except Exception:  # noqa: BLE001 — poster is best-effort display
            log.debug("poster arm failed for %s", ci.item_id)

    def _on_first_video_frame(self, frame) -> None:
        """The decoder delivered a real frame — the poster yields."""
        if not self._poster_item.isVisible():
            return
        try:
            valid = frame.isValid()
        except Exception:  # noqa: BLE001 — defensive over backends
            valid = True
        if valid:
            self._poster_item.setVisible(False)

    def _refit_poster(self) -> None:
        """Scale + centre the poster pixmap onto the video's letterboxed
        paint rect (rides :meth:`_fit_video_to_view`)."""
        if self._poster_src is None or self._poster_src.isNull():
            return
        vp = self._video_view.viewport()
        rect = self._video_paint_rect(
            max(1, vp.width()), max(1, vp.height()))
        scaled = self._poster_src.scaled(
            max(1, int(rect.width())), max(1, int(rect.height())),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self._poster_item.setPixmap(scaled)
        self._poster_item.setPos(
            rect.x() + (rect.width() - scaled.width()) / 2.0,
            rect.y() + (rect.height() - scaled.height()) / 2.0)

    # ── Workshop model (spec/56 slice 3) ──────────────────────────────

    def _playhead_ms(self) -> int:
        return int(self._player.position())

    def _load_workshop(self) -> None:
        """Lazy-birth the segment set for the loaded video and bind the
        selection. Marker ops need ``item.duration_ms`` in the DB —
        ingest leaves it NULL, so the first workshop open backfills it."""
        self._workshop_ready = False
        if self._eg is None or not self._item_id:
            return
        try:
            it = self._eg.item(self._item_id)
            if it is not None and not it.duration_ms and self._duration_ms > 0:
                self._eg.backfill_video_durations()
            self._eg.ensure_video_segments(
                self._item_id, default_state=self._phase_default)
            self._workshop_ready = True
        except Exception:  # noqa: BLE001
            log.exception(
                "workshop: segment birth failed for %s — timeline runs "
                "scrub-only", self._item_id)
        self._reload_model(keep_selection=False)

    def set_phase_default(self, state: str) -> None:
        """Inject the configured edit default (``default_state_for``) —
        spec/59: it colours un-decided rows AND seeds segment birth."""
        if state in ("picked", "skipped"):
            self._phase_default = state

    def set_watermark_enabled(self, on: bool) -> None:
        """Host-injected ``show_exported_watermark`` gate (spec/59 §8).
        On the video page only developed SNAPSHOTS can wear the
        watermark — they're photo items; clips never do."""
        self._watermark_enabled = bool(on)

    def _snapshot_exported(self, snapshot_id: str) -> bool:
        """True iff this snapshot has an exported version (edit-phase
        lineage) and the watermark is enabled."""
        if not self._watermark_enabled or self._eg is None:
            return False
        try:
            return snapshot_id in self._eg.exported_item_ids()
        except Exception:  # noqa: BLE001 — display-only
            log.exception("exported_item_ids failed")
            return False

    def _edit_state(self, item_id: str) -> str:
        ps = self._eg.phase_state(item_id, "edit") if self._eg else None
        return ps.state if ps is not None else self._phase_default

    def _reload_model(self, *, keep_selection: bool) -> None:
        """Re-read markers / segment items / snapshots and re-project the
        timeline + strip. ``keep_selection`` holds the seg_index across
        geometry-only changes (marker moves)."""
        if self._eg is None or not self._item_id or not self._workshop_ready:
            self._markers, self._segment_items = [], []
            self._seg_bounds, self._seg_states = [], {}
            self._snapshots, self._snap_states = [], {}
            self._push_timeline()
            return
        self._markers = self._eg.video_markers(self._item_id)
        self._segment_items = self._eg.segment_items(self._item_id)
        self._snapshots = self._eg.video_snapshots(self._item_id)
        try:
            self._seg_bounds = segment_bounds(
                [mk.at_ms for mk in self._markers], self._duration_ms,
            ) if self._duration_ms > 0 else []
        except ValueError:
            log.exception("workshop: bad marker geometry for %s", self._item_id)
            self._seg_bounds = []
        self._seg_states = {
            it.id: self._edit_state(it.id) for it in self._segment_items}
        self._snap_states = {
            sn.item_id: self._edit_state(sn.item_id) for sn in self._snapshots}
        self._push_timeline()
        self._refresh_cursor_context()

    def _segment_at(self, ms: int) -> int:
        if not self._markers or self._duration_ms <= 0:
            return 0
        try:
            return containing_segment(
                [mk.at_ms for mk in self._markers],
                max(0, min(int(ms), self._duration_ms)),
                self._duration_ms)
        except ValueError:
            return 0

    def _push_timeline(self) -> None:
        """Project the model onto the timeline + strip. The highlight
        ring follows the cursor's containing clip (spec/59 — the cursor
        is the selection)."""
        self._scrub.set_min_gap(self._frame_ms)
        states = [
            self._seg_states.get(it.id, "skipped")
            for it in self._segment_items]
        self._scrub.set_model(
            [(mk.id, mk.at_ms) for mk in self._markers],
            self._seg_bounds, states,
            self._segment_at(self._playhead_ms()),
            "",
            [(sn.at_ms, self._snap_states.get(sn.item_id, "skipped"))
             for sn in self._snapshots])
        # The NAV dropdowns are the direct-access path (Nelson
        # 2026-06-11 — the chip strip didn't scale).
        self._markers_menu.clear()
        for mk in self._markers:
            act = self._markers_menu.addAction(
                "▼ " + _fmt_ms(int(mk.at_ms)))
            act.triggered.connect(
                lambda _=False, ms=int(mk.at_ms): self._seek_to(ms))
        self._markers_menu_btn.setEnabled(
            self._workshop_ready and bool(self._markers))
        self._snapshots_menu.clear()
        for sn in self._snapshots:
            act = self._snapshots_menu.addAction(
                "📷 " + _fmt_ms(int(sn.at_ms)))
            act.triggered.connect(
                lambda _=False, ms=int(sn.at_ms): self._seek_to(ms))
        self._snapshots_menu_btn.setEnabled(
            self._workshop_ready and bool(self._snapshots))
        self._refresh_info()

    def _adj_target_id(self) -> str:
        """The item under the cursor — the snapshot at the playhead,
        else the containing clip (spec/59: the cursor is the
        selection)."""
        pos = self._playhead_ms()
        stop = self._stop_at(pos)
        if stop is not None and stop[0] == "snapshot":
            return stop[1].item_id
        seg = self._segment_item_at(pos)
        return seg.id if seg is not None else ""

    def _classification_target_id(self) -> str:
        """The item whose CLASSIFICATION the STYLE badge reads and a
        style pick decides (spec/58 §2): a snapshot under the cursor
        carries its own row (inherited from the video at creation);
        otherwise the SOURCE video's — deciding a clip's style decides
        the video's genre."""
        stop = self._stop_at(self._playhead_ms())
        if stop is not None and stop[0] == "snapshot":
            return stop[1].item_id
        return self._item_id or ""

    def _classification_row(self) -> Optional[m.Item]:
        """The :meth:`_classification_target_id` item row, or ``None``."""
        target = self._classification_target_id()
        if self._eg is None or not target:
            return None
        try:
            return self._eg.item(target)
        except Exception:  # noqa: BLE001
            log.exception("classification lookup failed for %s", target)
            return None

    def _on_style_decided(self, style: str) -> None:
        """spec/58 §2 — picking a style (even the one already shown) IS
        the human decision, scoped like the badge: snapshot → its own
        row, segment → the source video's."""
        target = self._classification_target_id()
        if self._eg is None or not target:
            return
        try:
            self._eg.set_classification(target, style, "user")
        except Exception:  # noqa: BLE001
            log.exception("style decision write failed for %s", target)

    # ── Workshop actions ──────────────────────────────────────────────

    def _on_timeline_seek(self, ms: int) -> None:
        self._seek_to(ms)

    def _on_segment_clicked(self, _idx: int) -> None:
        # seek_requested already moved the cursor; the click signal is
        # kept for tests/tools.
        pass

    def _on_marker_selected(self, marker_id: str) -> None:
        """Clicking a marker handle seeks the cursor to it (spec/59 §5)
        — the visibility rules then react to the stop."""
        mk = next((mrk for mrk in self._markers
                   if mrk.id == marker_id), None)
        if mk is not None:
            self._seek_to(int(mk.at_ms))

    def _on_marker_moved(self, marker_id: str, new_ms: int) -> None:
        if self._eg is None:
            return
        try:
            self._eg.move_video_marker(marker_id, int(new_ms))
        except (ValueError, Exception):  # noqa: BLE001
            log.exception("workshop: move marker %s failed", marker_id)
        self._reload_model(keep_selection=True)

    def _on_cut(self) -> None:
        if self._eg is None or not self._workshop_ready:
            return
        pos = self._playhead_ms()
        try:
            self._eg.add_video_marker(self._item_id, pos)
        except ValueError as exc:
            log.info("workshop: cut at %d rejected: %s", pos, exc)
            return
        except Exception:  # noqa: BLE001
            log.exception("workshop: cut at %d failed", pos)
            return
        self._reload_model(keep_selection=False)

    # ── The Stop model (spec/59): cursor IS the selection ─────────────

    def _stop_at(self, pos: int):
        """The stop under the cursor, with one frame of tolerance:
        ``("snapshot", row)`` · ``("marker", at_ms)`` (interior OR the
        permanent start) · ``("endmarker", duration)`` (the permanent
        end — a stop, but it starts no clip) · ``None`` off-stop."""
        tol = max(1, self._frame_ms)
        sn = next((s for s in self._snapshots
                   if abs(s.at_ms - pos) <= tol), None)
        if sn is not None:
            return ("snapshot", sn)
        mk = next((mrk for mrk in self._markers
                   if abs(mrk.at_ms - pos) <= tol), None)
        if mk is not None:
            return ("marker", int(mk.at_ms))
        if abs(pos) <= tol:
            return ("marker", 0)
        if self._duration_ms > 0 and abs(pos - self._duration_ms) <= tol:
            return ("endmarker", self._duration_ms)
        return None

    def _interior_marker_at(self, pos: int):
        tol = max(1, self._frame_ms)
        return next((mrk for mrk in self._markers
                     if abs(mrk.at_ms - pos) <= tol), None)

    def _segment_item_at(self, pos: int):
        """The clip item CONTAINING ``pos`` — on a marker, the clip
        starting there (the owning-marker rule, ancestor + spec/56)."""
        idx = self._segment_at(int(pos))
        if self._segment_items and 0 <= idx < len(self._segment_items):
            return self._segment_items[idx]
        return None

    def _jump_stop(self, direction: int) -> None:
        """◀ Stop / Stop ▶ — markers ∪ snapshots ∪ the permanent
        endpoints; with nothing further that way, park at the start /
        end (the ancestor culler's fallback)."""
        if not self._workshop_ready or self._duration_ms <= 0:
            return
        marks = sorted(
            {mk.at_ms for mk in self._markers}
            | {sn.at_ms for sn in self._snapshots}
            | {0, self._duration_ms})
        target = mark_jump_target(
            self._playhead_ms(), direction, marks, self._frame_ms)
        if target is None:
            target = 0 if direction < 0 else self._duration_ms
        # The end parks one frame short, like End ⏭ — seeking to the
        # very last millisecond stalls some decoders on a poster.
        target = min(int(target),
                     max(0, self._duration_ms - self._frame_ms))
        self._on_timeline_seek(target)

    def _on_toggle_status(self) -> None:
        self._set_status_at_cursor(None)

    def _set_status_at_cursor(self, state) -> None:
        """Toggle Status — the old culler rule, works anywhere: on a
        snapshot it acts on the snapshot; otherwise on the clip owning
        the position (the marker that starts it). ``state=None``
        toggles; ``"picked"``/``"skipped"`` set explicitly."""
        if self._eg is None or not self._workshop_ready:
            return
        pos = self._playhead_ms()
        stop = self._stop_at(pos)
        if stop is not None and stop[0] == "snapshot":
            target = stop[1].item_id
            states = self._snap_states
        else:
            seg = self._segment_item_at(pos)
            if seg is None:
                return
            target = seg.id
            states = self._seg_states
        new = state or (
            "skipped" if states.get(target) == "picked" else "picked")
        try:
            self._eg.set_phase_state(target, "edit", new)
        except Exception:  # noqa: BLE001
            log.exception("workshop: status write failed for %s", target)
            return
        states[target] = new
        self._push_timeline()
        # Status drives the visibility rules — a freshly Picked stop
        # opens development on the spot; a Skipped one greys it.
        self._refresh_cursor_context()

    def _refresh_stop_actions(self) -> None:
        """Cursor-driven enables (spec/59 §4): creators grey on any
        stop; Remove greys off-stop and at the permanent endpoints."""
        ready = self._workshop_ready
        stop = self._stop_at(self._playhead_ms()) if ready else None
        for b in (self._create_marker_btn, self._create_snap_btn):
            b.setEnabled(ready and stop is None)
        removable = ready and stop is not None and (
            stop[0] == "snapshot"
            or (stop[0] == "marker"
                and self._interior_marker_at(self._playhead_ms())
                is not None))
        self._remove_btn.setEnabled(removable)
        self._toggle_btn.setEnabled(ready)
        self._reset_menu_btn.setEnabled(ready)

    # ── Modeless development (spec/59 §2.1 + §3) ──────────────────────

    def _refresh_cursor_context(self) -> None:
        """Everything that follows the cursor: button enables, the
        media border, the per-clip playback settings, and the
        development state machine."""
        self._refresh_stop_actions()
        self._refresh_media_border()
        # Per-clip playback settings (Mute · Vol · Speed) follow the
        # CONTAINING clip across boundaries, as the selection used to.
        seg = self._segment_item_at(self._playhead_ms())
        seg_id = seg.id if seg is not None else ""
        if seg_id != self._last_ctx_seg:
            self._last_ctx_seg = seg_id
            if not self._dev_open:
                self._restore_tools_from_adjustment()
        self._sync_development_to_cursor()

    def _sync_development_to_cursor(self) -> None:
        """The spec/59 §2.1 rules. Hidden (space preserved) off-stop or
        while playing; all-greyed on a Skipped stop; live — media
        swapped to the development canvas on the stop's frame — on a
        Picked stop."""
        if not self._workshop_ready:
            self._close_development()
            self._set_top_tools_state("hidden")
            return
        playing = (self._player.playbackState()
                   == QMediaPlayer.PlaybackState.PlayingState)
        pos = self._playhead_ms()
        stop = None if playing else self._stop_at(pos)
        kind = target = None
        frame = pos
        state = None
        if stop is not None:
            if stop[0] == "snapshot":
                kind, target = "snapshot", stop[1].item_id
                frame = int(stop[1].at_ms)
                state = self._snap_states.get(target, "skipped")
            elif stop[0] == "marker":
                seg = self._segment_item_at(int(stop[1]))
                if seg is not None:
                    kind, target, frame = "segment", seg.id, int(stop[1])
                    state = self._seg_states.get(target, "skipped")
        if target is None:
            self._close_development()
            self._set_top_tools_state("hidden")
            self._dev_latch = None
        elif state != "picked":
            self._close_development()
            self._set_top_tools_state("greyed")
            self._dev_latch = None
        else:
            latch = (kind, target, frame)
            if self._dev_latch != latch or not self._dev_open:
                self._open_development(kind, target, frame)
                self._dev_latch = latch

    def _set_top_tools_state(self, state: str) -> None:
        """``hidden`` (space preserved) · ``greyed`` (ALL controls) ·
        ``live``."""
        tools = self._surface.tools_widget()
        if state == "hidden":
            tools.setVisible(False)
            return
        tools.setVisible(True)
        self._set_adjustment_tools_enabled(state == "live")

    def _open_development(self, kind: str, target_id: str,
                          frame_ms: int) -> None:
        """Landing on a Picked stop IS development (spec/59 §3): pause,
        extract the stop's frame (a clip's = its initial marker's),
        load the surface bound to the stop, swap the media to the
        canvas."""
        if self._source is None or self._frames_dir is None:
            return
        self._player.pause()
        set_transport_playing(self._nav_play, False)
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            self._frames_dir.mkdir(parents=True, exist_ok=True)
            out = self._frames_dir / f"{target_id}_{frame_ms}.jpg"
            try:
                if not out.exists():
                    extract_frame(self._source, frame_ms, out)
                arr = decode_image(out)
            except Exception:  # noqa: BLE001
                # A partial file from an interrupted extraction would
                # poison the cache forever — drop it and retry once.
                log.warning("cached frame unusable for %s @ %d — "
                            "re-extracting", target_id, frame_ms)
                out.unlink(missing_ok=True)
                extract_frame(self._source, frame_ms, out)
                arr = decode_image(out)
        except Exception:  # noqa: BLE001
            log.exception("frame extract failed for %s @ %d",
                          self._source, frame_ms)
            return
        finally:
            QApplication.restoreOverrideCursor()

        cls_row = self._classification_row()
        default_style = normalize_style(
            cls_row.classification if cls_row else None)
        if kind == "snapshot":
            padj = self._eg.adjustment(target_id) if self._eg else None
            style = (padj.style if padj and padj.style else default_style)
            look = (padj.look if padj else "natural") or "natural"
            cfilter = padj.creative_filter if padj else None
            angle = float(padj.crop_angle or 0.0) if padj else 0.0
            aspect = (padj.aspect_label if padj and padj.aspect_label
                      else "Original")
            crop_src = padj
        else:
            adj = (self._eg.video_adjustment(target_id)
                   if self._eg else None)
            style = (adj.style if adj and adj.style else default_style)
            look = (adj.look if adj else "natural") or "natural"
            cfilter = adj.creative_filter if adj else None
            angle = float(adj.box_angle or 0.0) if adj else 0.0
            aspect = (adj.aspect_ratio_label
                      if adj and adj.aspect_ratio_label else "Original")
            crop_src = adj
        self._surface_loading = True
        try:
            self._surface.load_image(arr, style=style)
            crop = None
            if crop_src is not None and all(v is not None for v in (
                    crop_src.crop_x, crop_src.crop_y,
                    crop_src.crop_w, crop_src.crop_h)):
                crop = (crop_src.crop_x, crop_src.crop_y,
                        crop_src.crop_w, crop_src.crop_h)
            self._surface.set_state(
                look=look,
                crop_norm=crop,
                box_angle=angle,
                style=style,
                aspect_label=aspect,
                creative_filter=cfilter)
        finally:
            self._surface_loading = False
        self._surface.set_classification_badge(
            cls_row.classification_source if cls_row else None,
            cls_row.classification_confidence if cls_row else None)
        self._pending_rep_ms = frame_ms

        # spec/59 §8 — a developed SNAPSHOT with an exported version
        # wears the watermark; clips (marker stops) never do.
        self._surface.canvas().set_exported_watermark(
            kind == "snapshot" and self._snapshot_exported(target_id))
        self._chrome.set_media(self._surface.canvas())
        self._set_top_tools_state("live")
        self._dev_open = True
        self._refresh_rep_frame_chrome()
        self._video_crop_item.setVisible(False)

    def _close_development(self) -> None:
        """Back to the player. Adjustments were persisted per edit —
        nothing to adopt (the Adopt button died with the mode)."""
        if not self._dev_open:
            return
        self._chrome.set_media(self._video_view)
        # Window-proof the detached canvas: set_media left it parentless,
        # and a parentless widget that ever gets shown becomes its own
        # top-level window (Nelson eyeball 2026-06-11 — "a completely
        # new window with the canvas"). Hand it back to its birth parent
        # (the surface holder) so a stray show() stays invisible.
        self._surface.canvas().setParent(self._surface)
        self._surface.canvas().set_exported_watermark(False)
        self._dev_open = False
        # Force the video sink to redeliver a frame — a reparented
        # QGraphicsVideoItem paints BLACK until the next seek (the old
        # Adjust-exit seeked for exactly this reason).
        self._player.setPosition(int(self._player.position()))
        self._refresh_video_crop_overlay()

    def _refresh_rep_frame_chrome(self) -> None:
        """Kept as a no-op shim — the rep-frame glyph + Reopen button
        died with the adjustment-frame concept (spec/59 §6)."""

    # ── The middle line's Reset menu ──────────────────────────────────

    def _on_remove_at_playhead(self) -> None:
        """Remove the stop under the cursor — the snapshot first, else
        the marker; the permanent start/end refuse by construction. One
        frame of tolerance on both matches."""
        if self._eg is None or not self._workshop_ready:
            return
        pos = self._playhead_ms()
        tol = max(1, self._frame_ms)
        sn = next((s for s in self._snapshots
                   if abs(s.at_ms - pos) <= tol), None)
        if sn is not None:
            try:
                self._eg.delete_child(sn.item_id)
            except Exception:  # noqa: BLE001
                log.exception(
                    "workshop: remove snapshot %s failed", sn.item_id)
                return
            self._reload_model(keep_selection=False)
            return
        mk = self._interior_marker_at(pos)
        if mk is not None:
            try:
                self._eg.delete_video_marker(mk.id)
            except Exception:  # noqa: BLE001
                log.exception("workshop: remove marker %s failed", mk.id)
                return
            self._reload_model(keep_selection=False)

    def _confirm_box(self, title: str, text: str) -> bool:
        """Yes/No confirm without the stock icon chrome (the
        QMessageBox rule: Icon.NoIcon until the custom component
        lands)."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _clear_markers(self, *, confirm: bool = True) -> None:
        """Reset menu: delete every marker. Clips merge leftward (the
        locked left-survives rule), so the single survivor carries the
        FIRST clip's decision + development."""
        if self._eg is None or not self._markers:
            return
        if confirm and not self._confirm_box(
                tr("Clear markers"),
                tr("Remove all {n} marker(s)? Clips merge back into "
                   "one; the first clip's decision and development "
                   "survive.").replace("{n}", str(len(self._markers)))):
            return
        for mk in list(self._markers):
            try:
                self._eg.delete_video_marker(mk.id)
            except Exception:  # noqa: BLE001
                log.exception("workshop: clear marker %s failed", mk.id)
        self._reload_model(keep_selection=False)

    def _clear_snapshots(self, *, confirm: bool = True) -> None:
        """Reset menu: delete every snapshot (their development rows
        cascade with them)."""
        if self._eg is None or not self._snapshots:
            return
        if confirm and not self._confirm_box(
                tr("Clear snapshots"),
                tr("Remove all {n} snapshot(s)? Their development goes "
                   "with them.").replace(
                       "{n}", str(len(self._snapshots)))):
            return
        for sn in list(self._snapshots):
            try:
                self._eg.delete_child(sn.item_id)
            except Exception:  # noqa: BLE001
                log.exception(
                    "workshop: clear snapshot %s failed", sn.item_id)
        self._reload_model(keep_selection=False)

    def _reset_everything(self) -> None:
        """Reset menu: markers + snapshots go and the ONE surviving
        clip returns to the default Skip. Development of the survivor
        is deliberately untouched — Reset wipes marking work, not
        colour work (merged-away clips lose theirs via left-survives,
        spec/59 §1)."""
        if self._eg is None or not self._workshop_ready:
            return
        if not self._confirm_box(
                tr("Reset everything"),
                tr("Remove {c} marker(s) and {s} snapshot(s) and set "
                   "the whole video back to Skip?")
                .replace("{c}", str(len(self._markers)))
                .replace("{s}", str(len(self._snapshots)))):
            return
        self._clear_snapshots(confirm=False)
        self._clear_markers(confirm=False)
        if self._segment_items:
            seg_id = self._segment_items[0].id
            try:
                self._eg.set_phase_state(seg_id, "edit", "skipped")
            except Exception:  # noqa: BLE001
                log.exception("workshop: reset state failed for %s", seg_id)
            self._seg_states[seg_id] = "skipped"
        self._push_timeline()
        self._refresh_cursor_context()

    def _on_snapshot(self) -> None:
        if self._eg is None or not self._workshop_ready:
            return
        pos = self._playhead_ms()
        try:
            self._eg.create_video_snapshot(self._item_id, pos)
        except Exception:  # noqa: BLE001
            log.exception("workshop: snapshot at %d failed", pos)
            return
        # Placing IS the intent — the snapshot arrives picked AT the
        # cursor, so the context refresh opens its development on the
        # spot (spec/59 §3).
        self._reload_model(keep_selection=False)

    def _refresh_info(self) -> None:
        ci = self._current_item()
        name = ci.path.name if ci is not None else ""
        n_seg = len(self._segment_items)
        n_picked = sum(
            1 for s in self._seg_states.values() if s == "picked")
        n_picked += sum(
            1 for s in self._snap_states.values() if s == "picked")
        self._info.setText(
            tr("Video {i}/{n} · {name} · {segs} segment(s) · {picked} picked")
            .replace("{i}", str(self._index + 1))
            .replace("{n}", str(len(self._items)))
            .replace("{name}", name)
            .replace("{segs}", str(n_seg))
            .replace("{picked}", str(n_picked)))

    # ── Adjustment read/write — scoped to the CURSOR (spec/59) ────────

    def _current_adjustment(self) -> Optional[m.VideoAdjustment]:
        """The CONTAINING clip's VideoAdjustment — cursor-scoped; a
        snapshot under the cursor doesn't change which clip you're
        inside (its own development is a photo Adjustment, read via
        :meth:`_current_photo_adjustment`)."""
        if self._eg is None:
            return None
        seg = self._segment_item_at(self._playhead_ms())
        return self._eg.video_adjustment(seg.id) if seg is not None else None

    def _current_photo_adjustment(self) -> Optional[m.Adjustment]:
        if self._eg is None:
            return None
        stop = self._stop_at(self._playhead_ms())
        if stop is None or stop[0] != "snapshot":
            return None
        return self._eg.adjustment(stop[1].item_id)

    def _persist_tool(self, **fields) -> None:
        """Patch the CONTAINING clip's VideoAdjustment row with
        ``fields`` — like Toggle, the playback tenants work anywhere:
        a snapshot under the cursor doesn't change which clip you're
        inside."""
        if self._tools_loading or self._eg is None:
            return
        seg = self._segment_item_at(self._playhead_ms())
        target = seg.id if seg is not None else ""
        if not target:
            return
        adj = self._eg.video_adjustment(target) or m.VideoAdjustment(
            item_id=target)
        for k, v in fields.items():
            setattr(adj, k, v)
        try:
            self._eg.save_video_adjustment(adj)
        except Exception:  # noqa: BLE001
            log.exception("save_video_adjustment failed for %s", target)

    def _has_adjustment(self, adj: Optional[m.VideoAdjustment]) -> bool:
        """True iff ANY colour / crop / aspect choice has been made —
        a non-Natural Look, a creative filter, or any geometry."""
        if adj is None:
            return False
        if (adj.look or "natural") != "natural":
            return True
        if adj.creative_filter:
            return True
        if any(v is not None for v in (
                adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h)):
            return True
        if adj.box_angle:
            return True
        if adj.aspect_ratio_label:
            return True
        return False

    # ── Video crop overlay (read-only, in-scene) ──────────────────────

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        """Refit the video item + crop box whenever the QGraphicsView
        resizes, and forward wheel events to a frame-step (matches the
        ←/→ keyboard shortcut). The event filter is the only path that
        works for the wheel — wheel events route to the widget under
        the cursor, not the focused widget."""
        if obj is self._video_view:
            etype = event.type()
            if etype in (QEvent.Type.Resize, QEvent.Type.Show):
                # Show: the page-stack swap can reveal the view without
                # a resize — refit so the video never opens tiny.
                self._fit_video_to_view()
            elif etype == QEvent.Type.Wheel:
                # Wheel up → previous frame; wheel down → next frame.
                # (Convention: wheel-up reads as "go back / scroll up".)
                delta = event.angleDelta().y()
                if delta > 0:
                    self._step(-1)
                elif delta < 0:
                    self._step(+1)
                event.accept()
                return True
        return super().eventFilter(obj, event)

    def _fit_video_to_view(self) -> None:
        """Size the QGraphicsVideoItem to fill the view (letterboxed to
        the source aspect by QGraphicsVideoItem.aspectRatioMode), set the
        scene rect to match, and push the actual video rect to the crop
        item so its (x, y, w, h)_norm coordinates map onto the right
        pixels."""
        vp = self._video_view.viewport()
        vw = max(1, vp.width())
        vh = max(1, vp.height())
        # Make the scene exactly the viewport — no scrolling, no zoom.
        self._video_scene.setSceneRect(QRectF(0.0, 0.0, vw, vh))
        # Size the video item to fill the viewport; KeepAspectRatio
        # letterboxes internally.
        self._video_item.setSize(QSizeF(vw, vh))
        self._video_item.setPos(0.0, 0.0)
        # Compute the actual letterboxed rect of the video inside the item
        # so the crop overlay maps to the same pixels the video paints to.
        video_rect = self._video_paint_rect(vw, vh)
        self._video_crop_item.set_video_rect(video_rect)
        # The load poster rides the same letterbox geometry.
        self._refit_poster()

    def _video_paint_rect(self, vw: float, vh: float) -> QRectF:
        """The letterboxed sub-rect of the view that the video actually
        paints to. When source dimensions are unknown, fall back to the
        full viewport so the crop coordinates still make rough sense."""
        if self._video_w <= 0 or self._video_h <= 0:
            return QRectF(0.0, 0.0, vw, vh)
        aspect_v = self._video_w / self._video_h
        aspect_w = vw / vh
        if aspect_v > aspect_w:
            paint_w = vw
            paint_h = vw / aspect_v
            paint_x = 0.0
            paint_y = (vh - paint_h) / 2.0
        else:
            paint_h = vh
            paint_w = vh * aspect_v
            paint_x = (vw - paint_w) / 2.0
            paint_y = 0.0
        return QRectF(paint_x, paint_y, paint_w, paint_h)

    def _refresh_video_crop_overlay(self) -> None:
        """Refresh the in-scene crop item's rect / angle / visibility from
        the clip's saved VideoAdjustment. Hidden when the clip has no crop
        OR when the surface is in Adjust mode (the AdjustmentSurface
        canvas owns the interactive overlay there)."""
        adj = self._current_adjustment()
        crop_norm: Optional[tuple[float, float, float, float]] = None
        angle = 0.0
        if adj is not None:
            if all(v is not None for v in (
                    adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h)):
                crop_norm = (adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h)
            if adj.box_angle:
                angle = float(adj.box_angle)
        self._video_crop_item.set_crop_norm(crop_norm)
        self._video_crop_item.set_angle(angle)
        # Make sure the video_rect reflects the current view + source
        # dimensions (the source dims may have just been probed in _show).
        self._fit_video_to_view()
        show = (not self._dev_open) and crop_norm is not None
        self._video_crop_item.setVisible(show)

    def _refresh_media_border(self) -> None:
        """Drive the MediaHost border from the CURSOR target's status —
        green = marked for export, red = not (spec/59 export-status:
        the border is the mark indicator AND the click target, the same
        grammar as Pick)."""
        if self._eg is None:
            self._chrome.set_media_state(None)
            return
        target = self._adj_target_id()
        if not target:
            self._chrome.set_media_state(None)
            return
        state = (self._seg_states.get(target)
                 or self._snap_states.get(target)
                 or self._phase_default)
        self._chrome.set_media_state(
            "picked" if state == "picked" else "skipped")

    def _restore_tools_from_adjustment(self) -> None:
        """Re-hydrate the tool UI from the saved VideoAdjustment row AND
        push the live-previewable settings (mute / volume / speed) onto
        the player so what the user hears + sees matches what they'll
        get on export."""
        adj = self._current_adjustment()
        self._tools_loading = True
        try:
            # Trim deltas retired (spec/56 — markers are the trim); the
            # in-player effective range stays the full clip until slice 3.
            self._trim_start = 0
            self._trim_end = 0
            # ``include_audio`` defaults to True; mute is its inverse.
            include_audio = bool(adj.include_audio) if adj is not None else True
            self._mute = not include_audio
            self._mute_chk.setChecked(self._mute)
            self._volume = (
                adj.audio_volume if adj and adj.audio_volume is not None else 1.0)
            self._vol_slider.setValue(int(round(self._volume * 100)))
            self._fade_ms = adj.audio_fade_ms if adj and adj.audio_fade_ms else 0
            fade_on = self._fade_ms > 0
            self._fade_btn.setChecked(fade_on)
            self._fade_combo.setEnabled(fade_on)
            if fade_on:
                # Snap the saved ms to the nearest dropdown second value.
                secs = self._fade_ms / 1000.0
                nearest = min(self._FADE_SECONDS, key=lambda s: abs(s - secs))
                self._fade_combo.setCurrentIndex(
                    self._FADE_SECONDS.index(nearest))
            self._speed = adj.speed if adj and adj.speed else 1.0
            if self._speed in self._SPEEDS:
                self._speed_combo.setCurrentIndex(self._SPEEDS.index(self._speed))
            # Schema column is [0, 1]; restore by snapping to the nearest
            # intensity level in the dropdown (1..5 → 0.2..1.0).
            stab_norm = float(adj.stabilise) if adj and adj.stabilise else 0.0
            self._stabilise = stab_norm
            stab_on = stab_norm > 0
            self._stab_btn.setChecked(stab_on)
            if stab_on:
                nearest_idx = min(
                    range(len(self._STAB_LEVELS)),
                    key=lambda i: abs(self._STAB_LEVELS[i][1] - stab_norm))
                self._stab_combo.setCurrentIndex(nearest_idx)
            else:
                # Keep the combo at its default (intensity 3) when off so
                # flipping the toggle later picks up a sensible starting
                # value rather than whatever stale state was in there.
                self._stab_combo.setCurrentIndex(2)
            self._stab_combo.setEnabled(stab_on)
        finally:
            self._tools_loading = False
        # Apply the live-previewable settings to the player + audio output.
        self._apply_live_audio()
        self._apply_live_speed()

    def _apply_live_audio(self) -> None:
        """Push mute + volume onto the QAudioOutput so live playback
        reflects the user's settings."""
        self._audio.setMuted(bool(self._mute))
        # QAudioOutput.setVolume takes [0.0, 1.0]; the slider scales to
        # 0-200 (200 = 2× the original level for the export filter). We
        # cap the live preview at 1.0 since most platforms don't boost
        # past unity. Export still honours the >1.0 value via the
        # ``volume=`` audio filter.
        self._audio.setVolume(min(1.0, max(0.0, self._volume)))

    def _apply_live_speed(self) -> None:
        """Push playback speed onto the player so live playback matches
        the export setting (slow-mo / time-lapse preview)."""
        self._player.setPlaybackRate(float(self._speed))

    # ── Player / scrub helpers ───────────────────────────────────────

    def _on_media_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.finished.emit()
            return
        if self._poster_shown:
            return
        loaded = (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        )
        if status in loaded:
            self._poster_shown = True
            # Ported from legacy ui/process/edit_video_page.py: without
            # this dance QMediaPlayer paints the QVideoWidget black until
            # play is hit. play() + pause() + setPosition forces a real
            # first frame to render. Deferred to the next event-loop tick
            # so it runs after the media-status handler returns.
            QTimer.singleShot(0, self._paint_poster)

    def _paint_poster(self) -> None:
        """Force QMediaPlayer to render a real first frame instead of the
        initial black surface. Ported from the legacy video page.

        Immersive: jump to start and auto-play (the user opened a
        full-screen view of a clip — they expect playback).
        Normal: play+pause+seek so the widget paints a frame and stays
        paused at the clip start. Nav-play button stays at 'Play'."""
        start_ms = self._eff_range()[0]
        if self._immersive:
            self._player.setPosition(start_ms)
            self._player.play()
            set_transport_playing(self._nav_play, True)
        else:
            self._player.play()
            self._player.pause()
            self._player.setPosition(start_ms)
            set_transport_playing(self._nav_play, False)

    def _on_position(self, pos: int) -> None:
        self._scrub.setValue(int(pos))
        s, e = self._eff_range()
        if pos < s or pos > e:
            self._seek_to(s if pos < s else e)
            return
        # The cursor IS the selection (spec/59) — everything follows it:
        # button enables, the media border, development open/grey/hide.
        self._refresh_cursor_context()

    def _on_duration(self, ms: int) -> None:
        if ms and ms > self._duration_ms:
            self._duration_ms = int(ms)
            self._apply_eff_range_to_scrub()
            # Segment geometry derives from duration — re-project.
            if self._workshop_ready:
                self._reload_model(keep_selection=True)

    def _eff_range(self) -> tuple[int, int]:
        """The user-visible playback range after trim shaves."""
        s = max(0, int(self._trim_start))
        e = max(s, int(self._duration_ms) - int(self._trim_end))
        return s, e

    def _apply_eff_range_to_scrub(self) -> None:
        s, e = self._eff_range()
        self._scrub.setRange(s, e)
        pos = self._player.position()
        if pos < s:
            self._seek_to(s)
        elif pos > e:
            self._seek_to(e)

    def _seek_to(self, ms: int) -> None:
        s, e = self._eff_range()
        ms = max(s, min(int(ms), e))
        self._player.setPosition(ms)

    def _step(self, direction: int) -> None:
        delta = int(direction) * self._frame_ms
        self._seek_to(int(self._player.position()) + delta)

    def _toggle_play(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            set_transport_playing(self._nav_play, False)
        else:
            s, e = self._eff_range()
            pos = self._player.position()
            if pos < s or pos >= e:
                self._seek_to(s)
            self._player.play()
            set_transport_playing(self._nav_play, True)
        # Pausing on a stop opens/greys development; playing hides it.
        self._refresh_cursor_context()

    # ── Trim ─────────────────────────────────────────────────────────
    # Retired with spec/56 schema v4 (markers ARE the trim). The trim UI
    # chrome had already been removed (Nelson 2026-06-04); the persistence
    # methods (_set_trim_in/_set_trim_out/_reset_trim/_commit_trim) went
    # with the VideoAdjustment trim columns. _trim_start/_trim_end stay as
    # zeroed instance state only because _eff_range reads them; the slice-3
    # workshop rebuild replaces this page's whole timeline model.

    # ── Tools change handlers ────────────────────────────────────────

    def _on_mute_toggled(self, checked: bool) -> None:
        if self._tools_loading:
            return
        self._mute = bool(checked)
        self._persist_tool(include_audio=not checked)
        self._apply_live_audio()

    def _on_volume_changed(self, val: int) -> None:
        if self._tools_loading:
            return
        self._volume = val / 100.0
        self._persist_tool(audio_volume=self._volume)
        self._apply_live_audio()

    def _on_fade_toggled(self, checked: bool) -> None:
        """Fade toggle: ON → use the dropdown's seconds value; OFF →
        persist 0 ms (no fade). Export-only; QMediaPlayer can't preview."""
        if self._tools_loading:
            return
        self._fade_combo.setEnabled(checked)
        if checked:
            secs = float(self._fade_combo.currentData() or 1.0)
            self._fade_ms = int(round(secs * 1000.0))
        else:
            self._fade_ms = 0
        self._persist_tool(audio_fade_ms=self._fade_ms)

    def _on_fade_duration_changed(self, _idx: int) -> None:
        if self._tools_loading or not self._fade_btn.isChecked():
            return
        secs = float(self._fade_combo.currentData() or 1.0)
        self._fade_ms = int(round(secs * 1000.0))
        self._persist_tool(audio_fade_ms=self._fade_ms)

    def _on_speed_changed(self, _idx: int) -> None:
        if self._tools_loading:
            return
        self._speed = float(self._speed_combo.currentData() or 1.0)
        self._persist_tool(speed=self._speed)
        self._apply_live_speed()

    def _on_stab_toggled(self, checked: bool) -> None:
        """Stabilise toggle: ON → use the dropdown's intensity; OFF →
        persist 0 (no stabilisation). Export-only — vidstab runs the
        analysis pass off-line."""
        if self._tools_loading:
            return
        self._stab_combo.setEnabled(checked)
        if checked:
            self._stabilise = float(self._stab_combo.currentData() or 0.6)
        else:
            self._stabilise = 0.0
        self._persist_tool(stabilise=self._stabilise)

    def _on_stab_changed(self, _idx: int) -> None:
        if self._tools_loading or not self._stab_btn.isChecked():
            return
        self._stabilise = float(self._stab_combo.currentData() or 0.6)
        self._persist_tool(stabilise=self._stabilise)

    # The Adjust mode (✎ Adjust / Reopen / Adopt, the replace-frame
    # dialog, _refresh_nav_mode) died with spec/59 §3 — landing the
    # cursor on a Picked stop IS development (_open_development /
    # _close_development / _sync_development_to_cursor above).

    def _resolved_params_for(
        self, adj: Optional[m.VideoAdjustment],
    ) -> Optional[Params]:
        """Compile the clip's Look CHOICE to engine Params on its rep
        frame (spec/54 §7 #1 — Looks on video, uncalibrated: the
        photo-fitted router reads the frame the user adjusted on).
        ``None`` for no-choice/Original (the export applies no tone)."""
        if adj is None:
            return None
        look = adj.look or "natural"
        if look == "original" and not adj.creative_filter:
            return None
        if self._source is None or self._frames_dir is None:
            return None
        try:
            pos_ms = int(adj.rep_frame_ms or 0)
            self._frames_dir.mkdir(parents=True, exist_ok=True)
            out = self._frames_dir / (
                f"{self._item_id or 'clip'}_{pos_ms}.jpg")
            if not out.exists():
                extract_frame(self._source, pos_ms, out)
            frame = decode_image(out)
            return compute_look_params(
                frame, style=adj.style or None, look=look)
        except Exception:  # noqa: BLE001
            log.exception(
                "look compile failed for %s — exporting untouched",
                self._item_id)
            return None

    def _on_surface_changed(self, _kind: str) -> None:
        """``AdjustmentSurface.changed`` hook — per-edit auto-persist
        (spec/42 Nelson 2026-06-09; matches EditPage's persistence
        model). Reads the current surface state and patches the clip's
        VideoAdjustment row. Suppressed during host-driven set_state."""
        if self._surface_loading or self._eg is None:
            return
        if not self._adj_target_id():
            return
        self._save_surface_state_to_adjustment()

    def _save_surface_state_to_adjustment(self) -> None:
        """Read the AdjustmentSurface's current CHOICE and write it onto
        the SELECTION's row — the segment's VideoAdjustment, or the
        snapshot's photo Adjustment (full photo treatment, spec/56)."""
        if self._eg is None:
            return
        target = self._adj_target_id()
        if not target:
            return
        st = self._surface.get_state()
        stop = self._stop_at(self._playhead_ms())
        if stop is not None and stop[0] == "snapshot":
            padj = self._eg.adjustment(target) or m.Adjustment(item_id=target)
            padj.look = st.look or "natural"
            padj.creative_filter = st.creative_filter
            padj.style = st.style or padj.style
            padj.crop_angle = float(st.box_angle or 0.0)
            padj.aspect_label = st.aspect_label or None
            if st.crop_norm is not None:
                padj.crop_x, padj.crop_y, padj.crop_w, padj.crop_h = st.crop_norm
            else:
                padj.crop_x = padj.crop_y = padj.crop_w = padj.crop_h = None
            padj.edit_exported = False
            try:
                self._eg.save_adjustment(padj)
            except Exception:  # noqa: BLE001
                log.exception("save_adjustment failed for %s", target)
            return
        adj = self._eg.video_adjustment(target) or m.VideoAdjustment(
            item_id=target)
        adj.look = st.look or "natural"
        adj.creative_filter = st.creative_filter
        adj.style = st.style or adj.style
        adj.box_angle = float(st.box_angle or 0.0)
        adj.rep_frame_ms = int(self._pending_rep_ms)
        adj.aspect_ratio_label = st.aspect_label or None
        if st.crop_norm is not None:
            adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h = st.crop_norm
        else:
            adj.crop_x = adj.crop_y = adj.crop_w = adj.crop_h = None
        try:
            self._eg.save_video_adjustment(adj)
        except Exception:  # noqa: BLE001
            log.exception("save_video_adjustment failed for %s", target)

    # ── Navigation ───────────────────────────────────────────────────

    def _emit_edge(self, delta: int) -> None:
        if self._nav_context == "day_grid":
            self.navigate_at_edge.emit(int(delta))
        # cluster context: stop (videos don't form clusters in v1; defensive).

    # ── Full-screen + keyboard ───────────────────────────────────────

    def set_immersive(self, on: bool) -> None:
        """Hide all chrome (top bar, tools panel, scrub row, nav) so the
        media fills the viewport. The MEDIA region stays mounted as-is —
        QVideoWidget in Watch mode, AdjustmentSurface canvas in Adjust
        mode — so toggling F11 in either mode just removes chrome."""
        self._immersive = on
        self._chrome.set_region_visible("top_bar", not on)
        self._chrome.set_region_visible("tools_panel", not on)
        self._chrome.set_region_visible("compact_row", not on)
        self._chrome.set_region_visible("nav", not on)
        if on and self._poster_shown and not self._dev_open:
            self._player.setPosition(self._eff_range()[0])
            self._player.play()
            set_transport_playing(self._nav_play, True)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Escape:
            if self._immersive:
                self.set_immersive(False)
                self.fullscreen_changed.emit(False)
            else:
                self.back_requested.emit()
            event.accept()
            return
        if key in (Qt.Key.Key_F, Qt.Key.Key_F11):
            # F / F11 — fullscreen (spec/63 §4 locked map).
            self._toggle_fullscreen()
            event.accept()
            return
        # Modeless (spec/59): the transport keys always work — moving
        # the cursor off a stop closes development by itself.
        # spec/63 §4: Tab is TRANSPORT (play/pause) and Space is the
        # DECISION toggle — transport and decision keys never share.
        # (The pre-locked-map Space-plays binding is evicted.)
        if key == Qt.Key.Key_Tab:
            self._toggle_play()
            event.accept()
            return
        if key in (Qt.Key.Key_Space, Qt.Key.Key_C):
            # Toggle the stop at the cursor (the snapshot under it, else
            # the owning clip). C degrades to the toggle — a binary
            # ledger, no Compare on the workshop (§4's rule).
            self._set_status_at_cursor(None)
            event.accept()
            return
        if key == Qt.Key.Key_Left:
            self._step(-1)
            event.accept()
            return
        if key == Qt.Key.Key_Right:
            self._step(+1)
            event.accept()
            return
        if key == Qt.Key.Key_M:
            self._on_cut()
            event.accept()
            return
        if key == Qt.Key.Key_S:
            self._on_snapshot()
            event.accept()
            return
        if key == Qt.Key.Key_P:
            self._set_status_at_cursor("picked")
            event.accept()
            return
        if key == Qt.Key.Key_X:
            # X = the universal Skip key (Nelson 2026-06-12; D retired).
            self._set_status_at_cursor("skipped")
            event.accept()
            return
        if key == Qt.Key.Key_Delete:
            self._on_remove_at_playhead()
            event.accept()
            return
        if key in (Qt.Key.Key_PageUp, Qt.Key.Key_Up):
            self._emit_edge(-1)
            event.accept()
            return
        if key in (Qt.Key.Key_PageDown, Qt.Key.Key_Down):
            self._emit_edge(+1)
            event.accept()
            return
        super().keyPressEvent(event)

    def _toggle_fullscreen(self) -> None:
        """One handler that drives both the F11 key and the ⛶ button."""
        self.set_immersive(not self._immersive)
        self.fullscreen_changed.emit(self._immersive)

    def _show_shortcuts(self) -> None:
        from mira.ui.base.shortcuts import show_shortcuts
        show_shortcuts(self, tr("Edit — video workshop"), [
            ("",                    tr("Transport")),
            (tr("Tab"),             tr("Play / pause")),
            (tr("◀ / ▶"),            tr("Step one frame")),
            (tr("Mouse wheel"),     tr("Step one frame")),
            ("",                    tr("Decide")),
            (tr("P / X"),           tr("Pick / Skip — the snapshot under "
                                       "the cursor, else the clip you are "
                                       "inside")),
            (tr("Space · C"),       tr("Toggle that Pick / Skip")),
            ("",                    tr("Edit the clip")),
            (tr("M"),               tr("Place a marker at the playhead")),
            (tr("S"),               tr("Place a snapshot at the playhead "
                                       "(arrives picked)")),
            (tr("Del"),             tr("Remove the stop under the cursor "
                                       "(start and end are permanent)")),
            ("",                    tr("Navigate")),
            (tr("Page Up / Down · ▲ / ▼"),
                                    tr("Previous / next day cell")),
            (tr("F / F11"),         tr("Fullscreen")),
            (tr("Esc"),             tr("Back")),
            (tr("F1 · ?"),          tr("This help")),
        ])


# --------------------------------------------------------------------------- #
# Legacy override shim for the export engine
# --------------------------------------------------------------------------- #


class _OverrideShim:
    """Duck-typed view of a :class:`VideoAdjustment` that satisfies
    :func:`core.video_export.build_export_plan`'s field reads.

    The legacy engine consumes a ``VideoOverride`` with attribute access for
    ``params`` / ``crop_norm`` / ``box_angle`` / ``trim_*`` / ``audio_*`` /
    ``speed`` / ``stabilise`` / ``style`` / ``aspect_ratio_label`` /
    ``include_audio`` / ``auto_on`` / ``rep_frame_ms`` / ``has_adjustment``.
    """

    def __init__(self, adj: Optional[m.VideoAdjustment], params: Optional[Params]):
        self._adj = adj
        self.params = params
        if adj is None:
            self.crop_norm = None
            self.box_angle = 0.0
            self.trim_start_delta_ms = 0
            self.trim_end_delta_ms = 0
            self.include_audio = True
            self.audio_volume = 1.0
            self.audio_fade_ms = 0
            self.speed = 1.0
            self.stabilise = 0.0
            self.style = "general"
            self.aspect_ratio_label = "Original"
            self.auto_on = True
            self.rep_frame_ms = None
            self.filter_recipe = None
            self.filter_amount = 1.0
            self.has_adjustment = False
            return
        if all(v is not None for v in (
                adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h)):
            self.crop_norm = (adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h)
        else:
            self.crop_norm = None
        self.box_angle = adj.box_angle or 0.0
        # Trim deltas retired (spec/56) — the engine duck still reads the
        # attributes, so they pin to 0 until the slice-4 export rebuild.
        self.trim_start_delta_ms = 0
        self.trim_end_delta_ms = 0
        self.include_audio = bool(adj.include_audio)
        self.audio_volume = adj.audio_volume if adj.audio_volume is not None else 1.0
        self.audio_fade_ms = adj.audio_fade_ms or 0
        self.speed = adj.speed if adj.speed else 1.0
        self.stabilise = adj.stabilise if adj.stabilise else 0.0
        self.style = adj.style or "general"
        self.aspect_ratio_label = adj.aspect_ratio_label or "Original"
        # The engine duck still reads ``auto_on``; under the Looks model
        # the resolved ``params`` carry the whole tone story, so the
        # flag is inert-True (no fresh-AUTO fallback wanted for video).
        self.auto_on = True
        self.rep_frame_ms = adj.rep_frame_ms
        # spec/55: the creative filter rides the plan as a resolved
        # recipe dict (per-style override honoured) — applied per frame
        # by core.video_export_run._process_frame — with the spec/54
        # §4.1 calibration trim resolved here too.
        try:
            self.filter_recipe = resolve_filter_recipe(
                adj.creative_filter, adj.style)
            self.filter_amount = creative_filter_amount(
                adj.creative_filter)
        except ValueError:
            log.exception("unknown creative filter on clip — ignoring")
            self.filter_recipe = None
            self.filter_amount = 1.0
        self.has_adjustment = bool(
            (adj.look or "natural") != "natural" or adj.creative_filter
            or self.crop_norm or self.box_angle
            or adj.aspect_ratio_label)


def _override_shim(
    adj: Optional[m.VideoAdjustment], params: Optional[Params],
) -> _OverrideShim:
    return _OverrideShim(adj, params)


__all__ = ["EditVideoPage"]
