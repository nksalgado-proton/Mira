"""Play — the full-screen rehearsal (spec/61 §5.4).

The exact grid sequence as a show: photos held the Cut's seconds-per-
photo, separator cards the same, clips at their TRUE length, music from
the Cut's category underneath. The point is feeling the final show —
timing, separators, soundtrack — before PTE ever opens.

Media players are created LAZILY (a photos-only Cut with no music never
touches QtMultimedia — keeps tests and odd machines safe).

Windowed by default (Nelson 2026-06-12) — a normal movable, resizable
black-canvas window. **F11 or F (or double-click) toggles full screen**;
Esc steps DOWN one level: full screen → window → end the rehearsal.
Space pause/resume · ←/→ step. Photos rescale live with the window.

Transport bar (Nelson 2026-06-19) — anchored at the bottom of the
canvas: Stop · Prev separator · Play/Pause · Next separator ·
clickable timeline (with day-separator ticks + hover thumb preview) ·
'Per slide' spinbox (live seconds-per-photo) · time read-out ·
fullscreen toggle. Auto-hides after 2.5 s of mouse idleness in
fullscreen; always visible in the windowed mode (the user already has
the window frame to grab — the bar is the steering wheel).

Overlays (spec/81 §3.1): when ``overlay_fields`` is non-empty and
``provenance_resolver`` is wired, every file frame draws its provenance
text live (when / where / how¹ / how²) on top of the image or video.
The in-app Play **always** draws overlays live — independent of the
Cut's export ``overlay_mode`` (embedded vs burn-in) — because the
rehearsal is the user's preview of the final hand-off.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from PyQt6.QtCore import (
    QEvent, QPoint, QPointF, QRect, Qt, QTimer, QUrl, pyqtSignal)
from PyQt6.QtGui import (
    QColor, QImage, QPainter, QPen, QPixmap, QPolygonF)
from PyQt6.QtWidgets import (
    QDialog, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QSizePolicy,
    QStackedLayout, QToolButton, QVBoxLayout, QWidget)

from core import audio_library, cut_overlay
from mira.ui.design.blurred_photo_canvas import BlurredPhotoCanvas
from mira.ui.i18n import tr
from mira.ui.media.image_loader import load_pixmap
from mira.ui.shared.separator_card import render_separator_image

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Timeline scrubber (internal)
# ─────────────────────────────────────────────────────────────────────────────


class _Scrubber(QWidget):
    """Horizontal track split into proportional per-entry segments.

    The total width represents the rehearsal's full duration: photo /
    opener / separator slides take ``photo_ms`` each; videos take their
    TRUE clip length (``SessionFile.duration_ms``). Day-separator entries
    get a yellow tick so the user can spot day boundaries at a glance,
    and the white playhead shows where the show is now.

    Click anywhere → ``seeked(entry_index, ms_offset_inside)``.
    Move the cursor over the track → ``hovered(entry_index, x_in_self)``
    so the dialog can pop a thumb preview at the right place.
    """

    seeked = pyqtSignal(int, int)
    hovered = pyqtSignal(int, int)
    hover_left = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._durations: List[int] = []
        self._sep_indexes: set = set()
        self._index = 0
        self._fraction = 0.0
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)

    # ── data ─────────────────────────────────────────────────────────

    def set_entries(
        self,
        durations: Sequence[int],
        sep_indexes: Sequence[int],
    ) -> None:
        self._durations = [max(1, int(d)) for d in durations]
        self._sep_indexes = {int(i) for i in sep_indexes}
        self._index = max(0, min(self._index, len(self._durations) - 1))
        self._fraction = 0.0
        self.update()

    def set_playhead(self, index: int, fraction: float) -> None:
        if not self._durations:
            return
        self._index = max(0, min(int(index), len(self._durations) - 1))
        self._fraction = max(0.0, min(1.0, float(fraction)))
        self.update()

    def total_ms(self) -> int:
        return sum(self._durations) or 1

    def playhead_ms(self) -> int:
        if not self._durations:
            return 0
        prefix = sum(self._durations[: self._index])
        cur = self._durations[self._index]
        return prefix + int(cur * self._fraction)

    def x_to_index_and_offset(self, x: int) -> Tuple[int, int]:
        """Map a pixel x-coordinate to ``(entry_index, ms_inside_entry)``."""
        if not self._durations:
            return 0, 0
        w = max(1, self.width())
        x = max(0, min(w - 1, int(x)))
        target_ms = int(self.total_ms() * x / w)
        acc = 0
        for i, d in enumerate(self._durations):
            if acc + d > target_ms:
                return i, target_ms - acc
            acc += d
        return len(self._durations) - 1, self._durations[-1]

    # ── paint ────────────────────────────────────────────────────────

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self.rect()
        track_h = 6
        ty = (r.height() - track_h) // 2
        # unplayed
        p.fillRect(QRect(0, ty, r.width(), track_h),
                   QColor(255, 255, 255, 60))
        if not self._durations:
            p.end()
            return
        total = self.total_ms()
        # played
        px = int(r.width() * (self.playhead_ms() / total))
        p.fillRect(QRect(0, ty, px, track_h),
                   QColor(255, 255, 255, 210))
        # Chapter markers — soft amber diamonds floating just above the
        # track (Nelson 2026-06-19). A diamond reads as "bookmark" at a
        # glance and doesn't visually compete with the playhead's
        # circle (the earlier 2 px yellow line through the track read
        # as a stray pen mark whenever the playhead crossed it).
        marker_fill = QColor(244, 184, 96, 245)        # warm amber
        marker_stroke = QColor(28, 22, 14, 170)        # near-black, soft
        marker_pen = QPen(marker_stroke, 1.0)
        marker_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        half = 5
        cy = ty - half - 3
        for i in self._sep_indexes:
            if i >= len(self._durations):
                continue
            t = sum(self._durations[:i])
            sx = int(r.width() * t / total)
            diamond = QPolygonF([
                QPointF(sx, cy - half),
                QPointF(sx + half, cy),
                QPointF(sx, cy + half),
                QPointF(sx - half, cy),
            ])
            p.setBrush(marker_fill)
            p.setPen(marker_pen)
            p.drawPolygon(diamond)
            # A 1 px amber pip at the boundary on the track itself —
            # keeps the "the chapter starts here" cue legible after the
            # playhead sweeps past and the diamond reads as a "future"
            # bookmark.
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(marker_fill)
            p.drawRect(QRect(sx - 1, ty - 1, 2, track_h + 2))
        # playhead
        p.setPen(QPen(QColor(255, 255, 255, 240), 2))
        p.drawLine(px, ty - 6, px, ty + track_h + 6)
        p.setBrush(QColor(255, 255, 255, 255))
        p.drawEllipse(QPoint(px, ty + track_h // 2), 6, 6)
        p.end()

    # ── input ────────────────────────────────────────────────────────

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton and self._durations:
            i, off = self.x_to_index_and_offset(int(ev.position().x()))
            self.seeked.emit(i, off)
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if self._durations:
            x = int(ev.position().x())
            i, _off = self.x_to_index_and_offset(x)
            self.hovered.emit(i, x)
        super().mouseMoveEvent(ev)

    def leaveEvent(self, ev) -> None:  # noqa: N802
        self.hover_left.emit()
        super().leaveEvent(ev)


# ─────────────────────────────────────────────────────────────────────────────
# The dialog
# ─────────────────────────────────────────────────────────────────────────────


def _fmt_time(ms: int) -> str:
    """``mm:ss`` for the read-out (``hh:mm:ss`` for long shows)."""
    s = max(0, int(ms // 1000))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


class CutPlayerDialog(QDialog):
    """Full-screen rehearsal over a prebuilt entry sequence."""

    def __init__(
        self,
        entries: List[Tuple[str, object]],
        *,
        event_root: Path,
        photo_s: float,
        day_meta: dict,
        aspect: str = "16:9",
        music_tracks: Optional[list] = None,
        opener_image: Optional[QImage] = None,
        card_style: str = "black",
        seed_prefix: str = "",
        overlay_fields: Sequence[str] = (),
        provenance_resolver: Optional[
            Callable[[str], Optional[cut_overlay.FrameProvenance]]
        ] = None,
        resolve_path: Optional[Callable[[object], Path]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        """spec/94 Phase 4a-iii — ``resolve_path``, when wired,
        replaces ``event_root / payload.export_relpath`` for every
        file-kind entry. Cross-event Play passes a callable that
        resolves each member to its source event's bytes path; for
        event-scope Play, leave it unset and the historical
        ``event_root``-relative path stays in force."""
        super().__init__(parent)
        self.setObjectName("CutPlayerDialog")
        self.setWindowTitle(tr("Play — the rehearsal"))
        # Modality must be declared BEFORE the first show: the launch is
        # start() then exec(), and applying modality to an already-visible
        # window is unreliable on Windows (owner left half-disabled).
        self.setModal(True)
        self.setStyleSheet("background-color: black;")  # pragma: no-qss
        # the show's canvas — a deliberate exception to the no-inline rule: this
        # surface is the slideshow itself, not app chrome, and must stay black in
        # any theme.
        self._entries = list(entries)
        self._root = Path(event_root)
        self._resolve_path = resolve_path
        self._photo_s = float(photo_s)
        self._photo_ms = max(200, int(photo_s * 1000))
        self._day_meta = day_meta
        self._aspect = aspect
        self._music_tracks = list(music_tracks or [])
        self._opener_image = opener_image
        self._card_style = card_style
        self._seed_prefix = seed_prefix
        self._music_index = 0
        self._index = -1
        self._paused = False
        # Spec/81 §3.1 — live overlays.
        self._overlay_fields: tuple = tuple(overlay_fields or ())
        self._provenance_resolver = provenance_resolver
        self._overlay_label: Optional[QLabel] = None

        # Two-tier layout (Nelson 2026-06-19): the slide canvas sits in a
        # stack-widget on top, the transport bar rides the bottom of the
        # dialog as a real layout participant. The earlier design floated
        # the transport over the canvas and lost it the moment the video
        # widget started painting (Qt's video sink draws over child
        # overlays). Reserving vertical space below the canvas keeps the
        # bar present at all times and makes the canvas exactly the
        # photo/video area, nothing more.
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._stack_widget = QWidget(self)
        self._stack_widget.setSizePolicy(QSizePolicy.Policy.Ignored,
                                         QSizePolicy.Policy.Ignored)
        self._stack_layout = QStackedLayout(self._stack_widget)
        self._stack_layout.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self._stack_layout.setContentsMargins(0, 0, 0, 0)
        self._layout.addWidget(self._stack_widget, 1)
        self._photo = BlurredPhotoCanvas(
            parent=self._stack_widget, inner_pad=28, radius=10.0,
        )
        # Ignored policy: the canvas's pixmap must NEVER drive the layout
        # (would set the WINDOW minimum after fullscreen — see 2026-06-12
        # freeze).
        self._photo.setSizePolicy(QSizePolicy.Policy.Ignored,
                                  QSizePolicy.Policy.Ignored)
        self._stack_layout.addWidget(self._photo)
        self._missing_label = None
        self._normal_geometry = None
        self._video_widget = None
        self._player = None
        self._video_audio = None
        self._music = None
        self._music_audio = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.advance)

        # Build the overlay label lazily — the empty-fields case skips
        # construction entirely so a Cut without overlays is byte-for-byte
        # the pre-spec/81 player.
        if self._overlay_fields and self._provenance_resolver is not None:
            self._overlay_label = QLabel(self)
            self._overlay_label.setObjectName("CutPlayOverlay")
            self._overlay_label.setStyleSheet(  # pragma: no-qss — slideshow overlay
                "color: #ffffff;"
                " background-color: rgba(0, 0, 0, 150);"
                " padding: 8px 12px;"
                " font-size: 14px;"
                " border-radius: 6px;")
            self._overlay_label.setWordWrap(True)
            self._overlay_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.NoTextInteraction)
            self._overlay_label.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._overlay_label.hide()

        # Pre-compute the per-entry duration table the scrubber walks.
        self._durations: List[int] = []
        self._sep_indexes: List[int] = []
        self._recompute_durations()

        # The transport bar (Nelson 2026-06-19) — a real layout
        # participant pinned to the bottom of the dialog so the video
        # widget never paints over it (the earlier overlay design lost
        # the bar the moment QVideoWidget claimed its surface).
        self._transport: Optional[QFrame] = None
        self._scrubber: Optional[_Scrubber] = None
        self._btn_play: Optional[QToolButton] = None
        self._btn_fs: Optional[QToolButton] = None
        self._time_label: Optional[QLabel] = None
        self._slide_spin: Optional[QDoubleSpinBox] = None
        self._preview_label: Optional[QLabel] = None
        self._preview_cache: dict = {}
        self._build_transport()
        if self._transport is not None:
            self._layout.addWidget(self._transport, 0)

        # 10 Hz progress ticker — drives the scrubber playhead + time
        # read-out. Cheap (one repaint of a tiny widget) and only spends
        # cycles while the dialog is visible.
        self._ticker = QTimer(self)
        self._ticker.setInterval(100)
        self._ticker.timeout.connect(self._tick)

    # ── lazy multimedia ──────────────────────────────────────────────

    def _ensure_video(self) -> None:
        if self._player is not None:
            return
        from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PyQt6.QtMultimediaWidgets import QVideoWidget
        self._video_widget = QVideoWidget(self._stack_widget)
        self._stack_layout.addWidget(self._video_widget)
        self._player = QMediaPlayer(self)
        self._video_audio = QAudioOutput(self)
        self._player.setAudioOutput(self._video_audio)
        self._player.setVideoOutput(self._video_widget)
        self._player.mediaStatusChanged.connect(self._on_video_status)

    def _ensure_music(self) -> None:
        if self._music is not None or not self._music_tracks:
            return
        from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
        self._music = QMediaPlayer(self)
        self._music_audio = QAudioOutput(self)
        self._music_audio.setVolume(0.6)
        self._music.setAudioOutput(self._music_audio)
        self._music.mediaStatusChanged.connect(self._on_music_status)

    # ── the show ─────────────────────────────────────────────────────

    def start(self, *, fullscreen: bool = False) -> None:
        self.resize(1100, 700)
        if fullscreen:
            self._normal_geometry = self.geometry()
            self.showFullScreen()
        else:
            self.show()
        if self._music_tracks:
            self._ensure_music()
            self._play_music_track(0)
        self.advance()
        self._ticker.start()

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self._exit_fullscreen()
        else:
            self._normal_geometry = self.geometry()
            self.showFullScreen()

    def _exit_fullscreen(self) -> None:
        self.showNormal()
        self._restore_normal_geometry()
        # Windows applies the state change asynchronously; re-assert the
        # saved geometry once the transition settles.
        QTimer.singleShot(0, self._restore_normal_geometry)

    def _restore_normal_geometry(self) -> None:
        g = self._normal_geometry
        if g is not None and not self.isFullScreen():
            self.setGeometry(g)

    def advance(self) -> None:
        self._show_index(self._index + 1)

    def step_back(self) -> None:
        self._show_index(max(0, self._index - 1))

    def _show_index(self, index: int) -> None:
        self._timer.stop()
        if self._player is not None:
            self._player.stop()
        if not (0 <= index < len(self._entries)):
            self._finish()
            return
        self._index = index
        kind, payload = self._entries[index]
        if kind == "opener":
            if self._opener_image is not None:
                self._show_image(self._opener_image)
                if not self._paused:
                    self._timer.start(self._photo_ms)
            else:
                self.advance()
                return
        elif kind == "sep":
            self._show_image(self._separator_image(payload))
            if not self._paused:
                self._timer.start(self._photo_ms)
        elif getattr(payload, "kind", "photo") == "video":
            self._show_video(self._resolve_payload_path(payload))
        else:
            pm = load_pixmap(self._resolve_payload_path(payload))
            self._show_pixmap(pm)
            if not self._paused:
                self._timer.start(self._photo_ms)
        # Refresh the overlay AFTER the frame paint.
        self._update_overlay(kind, payload)
        # Snap the scrubber to the new entry; the ticker keeps it warm.
        if self._scrubber is not None:
            self._scrubber.set_playhead(self._index, 0.0)
        self._update_time_label()
        self._update_play_icon()

    def _separator_image(self, day) -> QImage:
        meta = self._day_meta.get(day)
        return render_separator_image(
            day_number=day,
            date=getattr(meta, "date", None),
            location=getattr(meta, "location", None),
            description=getattr(meta, "description", "") or "",
            aspect=self._aspect,
            height=max(480, self.height() or 1080),
            card_style=self._card_style,
            seed_key=f"{self._seed_prefix}:{day}")

    def _resolve_payload_path(self, payload) -> Path:
        """Where the bytes live on disk. Cross-event Play wires
        ``resolve_path`` so each member is found in its source event's
        Exported Media/; event-scope Play (no override) keeps the
        historical ``event_root / payload.export_relpath`` shape."""
        if self._resolve_path is not None:
            return self._resolve_path(payload)
        return self._root / payload.export_relpath

    def _show_image(self, img: QImage) -> None:
        self._show_pixmap(QPixmap.fromImage(img))

    def _show_pixmap(self, pm: QPixmap) -> None:
        if self._video_widget is not None:
            self._video_widget.hide()
        self._photo.show()
        self._raw_pixmap = pm
        if pm.isNull():
            self._show_missing_label()
            return
        self._hide_missing_label()
        self._fit_current()
        self._stack_layout.setCurrentWidget(self._photo)

    def _show_missing_label(self) -> None:
        if self._missing_label is None:
            from PyQt6.QtWidgets import QLabel
            self._missing_label = QLabel(self._stack_widget)
            self._missing_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._missing_label.setStyleSheet(  # pragma: no-qss — slideshow text
                "color: #cccccc; background: transparent;")
            self._stack_layout.addWidget(self._missing_label)
        self._missing_label.setText(tr("(file missing)"))
        self._stack_layout.setCurrentWidget(self._missing_label)

    def _hide_missing_label(self) -> None:
        if self._missing_label is not None:
            self._missing_label.setText("")

    def _fit_current(self) -> None:
        pm = getattr(self, "_raw_pixmap", QPixmap())
        if pm.isNull():
            return
        # Scale against the canvas-area size (the stack widget), not the
        # whole dialog — the transport now reserves real estate below.
        size = self._stack_widget.size()
        if size.width() > 0 and size.height() > 0:
            pm = pm.scaled(size, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        self._photo.setPixmap(pm)

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self._fit_current()
        self._position_overlay()
        self._hide_hover_preview()

    # ── overlays (spec/81 §3.1) ──────────────────────────────────────

    def _update_overlay(self, kind: str, payload) -> None:
        lbl = self._overlay_label
        if lbl is None or self._provenance_resolver is None:
            return
        relpath = getattr(payload, "export_relpath", None)
        if not relpath or kind in ("opener", "sep"):
            lbl.hide()
            return
        try:
            provenance = self._provenance_resolver(relpath)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "rehearsal overlay: provenance_resolver raised for %s",
                relpath)
            lbl.hide()
            return
        if provenance is None:
            lbl.hide()
            return
        lines = cut_overlay.compose_overlay_lines(
            self._overlay_fields, provenance)
        if not lines:
            lbl.hide()
            return
        lbl.setText("\n".join(lines))
        lbl.adjustSize()
        self._position_overlay()
        lbl.raise_()
        lbl.show()

    def _position_overlay(self) -> None:
        lbl = self._overlay_label
        if lbl is None or not lbl.isVisible() and not lbl.text():
            return
        margin = 24
        # Lift the overlay above the transport bar when it's visible.
        bottom_inset = margin
        if self._transport is not None and self._transport.isVisible():
            bottom_inset = margin + self._transport.height()
        max_w = max(180, int(self.width() * 0.66))
        lbl.setMaximumWidth(max_w)
        lbl.adjustSize()
        y = max(margin, self.height() - lbl.height() - bottom_inset)
        lbl.move(margin, y)

    def mouseDoubleClickEvent(self, ev) -> None:  # noqa: N802
        self._toggle_fullscreen()
        ev.accept()

    def _show_video(self, path: Path) -> None:
        self._ensure_video()
        self._photo.hide()
        self._video_widget.show()
        self._stack_layout.setCurrentWidget(self._video_widget)
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        if not self._paused:
            self._player.play()

    def _on_video_status(self, status) -> None:
        from PyQt6.QtMultimedia import QMediaPlayer
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.advance()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            log.warning("rehearsal: invalid media, skipping")
            self.advance()

    # ── music ────────────────────────────────────────────────────────

    def _play_music_track(self, i: int) -> None:
        if self._music is None or not (0 <= i < len(self._music_tracks)):
            return
        self._music_index = i
        self._music.setSource(
            QUrl.fromLocalFile(str(self._music_tracks[i].path)))
        self._music.play()

    def _on_music_status(self, status) -> None:
        from PyQt6.QtMultimedia import QMediaPlayer
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._play_music_track(self._music_index + 1)

    # ── controls ─────────────────────────────────────────────────────

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        if self._paused:
            self._timer.stop()
            if self._player is not None:
                self._player.pause()
            if self._music is not None:
                self._music.pause()
        else:
            kind, payload = self._entries[self._index]
            if kind == "file" and getattr(payload, "kind", "") == "video":
                if self._player is not None:
                    self._player.play()
            else:
                self._timer.start(self._photo_ms)
            if self._music is not None:
                self._music.play()
        self._update_play_icon()

    def _finish(self) -> None:
        self._teardown_media()
        self.accept()

    def closeEvent(self, ev) -> None:  # noqa: N802
        """Catch the window-close (X / system menu / Alt-F4) path that
        bypasses ``_finish``. Without this, the QMediaPlayers stay alive
        and the music keeps playing after the user dismisses the
        dialog — Nelson 2026-06-19."""
        self._teardown_media()
        super().closeEvent(ev)

    def reject(self) -> None:  # noqa: D401 — Qt slot
        """Defence-in-depth — Esc on a native widget that doesn't bubble
        through ``keyPressEvent`` still reaches Qt's ``reject``."""
        self._teardown_media()
        super().reject()

    def _teardown_media(self) -> None:
        """Idempotent: silence + dispose every player / timer / ticker
        the dialog spun up. Safe to call multiple times.

        The mediaStatusChanged handler can re-trigger ``play()`` on the
        next track between ``stop()`` and dispose, so we disconnect the
        signal FIRST, then stop, then null the source so the decoder
        doesn't buffer-out more audio, then drop the volume to zero as
        a last-ditch silencer. The QAudioOutput holds its own buffer
        on Windows — stop alone is sometimes audible for a fraction of
        a second otherwise."""
        if getattr(self, "_torn_down", False):
            return
        self._torn_down = True
        self._timer.stop()
        self._ticker.stop()
        if self._music is not None:
            try:
                self._music.mediaStatusChanged.disconnect()
            except (TypeError, RuntimeError):
                pass
            self._music.stop()
            try:
                self._music.setSource(QUrl())
            except Exception:                                          # noqa: BLE001
                pass
            if self._music_audio is not None:
                self._music_audio.setVolume(0.0)
        if self._player is not None:
            try:
                self._player.mediaStatusChanged.disconnect()
            except (TypeError, RuntimeError):
                pass
            self._player.stop()
            try:
                self._player.setSource(QUrl())
            except Exception:                                          # noqa: BLE001
                pass
            if self._video_audio is not None:
                self._video_audio.setVolume(0.0)
        self._hide_hover_preview()

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        key = ev.key()
        if key == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self._exit_fullscreen()
            else:
                self._finish()
        elif key in (Qt.Key.Key_F11, Qt.Key.Key_F):
            self._toggle_fullscreen()
        elif key == Qt.Key.Key_Space:
            self._toggle_pause()
        elif key == Qt.Key.Key_Right:
            self.advance()
        elif key == Qt.Key.Key_Left:
            self.step_back()
        elif key in (Qt.Key.Key_F1, Qt.Key.Key_Question):
            self._show_shortcuts()
        else:
            super().keyPressEvent(ev)

    def _show_shortcuts(self) -> None:
        # Pauses the rehearsal — the modal dialog steals focus.
        if self._timer is not None and self._timer.isActive():
            self._toggle_pause()
        from mira.ui.base.shortcuts import show_shortcuts
        show_shortcuts(self, tr("Cut play — rehearsal"), [
            (tr("Space"),           tr("Pause / resume")),
            (tr("◀ / ▶"),            tr("Previous / next slide")),
            (tr("F / F11"),         tr("Toggle fullscreen")),
            (tr("Double-click"),    tr("Toggle fullscreen")),
            (tr("Esc"),             tr("Exit fullscreen, then end rehearsal")),
            (tr("F1 · ?"),          tr("This help")),
        ])

    # ── transport bar ────────────────────────────────────────────────

    def _build_transport(self) -> None:
        """Bottom-anchored Stop · ⏮ Sep · ▶/⏸ · Sep ⏭ · timeline ·
        time read-out · 'Per slide' spinbox · fullscreen toggle.

        Inline styling matches the dialog's own black canvas (the docstring
        precedent at the top of the file): the transport belongs to the
        show, not to app chrome."""
        bar = QFrame(self)
        bar.setObjectName("CutPlayTransport")
        bar.setStyleSheet(  # pragma: no-qss — slideshow transport bar
            "QFrame#CutPlayTransport {"
            " background-color: rgba(0, 0, 0, 220);"
            " border-top: 1px solid rgba(255, 255, 255, 50);"
            "}"
            " QToolButton {"
            " color: #ffffff; background: transparent;"
            " border: 1px solid rgba(255, 255, 255, 60);"
            " border-radius: 4px;"
            " padding: 6px 10px; font-size: 14px;"
            "}"
            " QToolButton:hover {"
            " background-color: rgba(255, 255, 255, 30);"
            " border-color: rgba(255, 255, 255, 140);"
            "}"
            " QToolButton:pressed {"
            " background-color: rgba(255, 255, 255, 50);"
            "}"
            " QLabel {"
            " color: #ffffff; background: transparent;"
            " font-size: 13px;"
            "}"
            " QDoubleSpinBox {"
            " color: #ffffff;"
            " background-color: rgba(255, 255, 255, 20);"
            " border: 1px solid rgba(255, 255, 255, 60);"
            " border-radius: 4px;"
            " padding: 2px 4px;"
            " min-width: 70px;"
            "}")
        # Pointing-hand on the buttons (PyQt6 QSS cursor is unreliable on
        # Windows — manual setCursor matches the rest of the app).
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(8)

        def mk_btn(label: str, tip: str, callback) -> QToolButton:
            b = QToolButton(bar)
            b.setText(label)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(callback)
            return b

        btn_stop = mk_btn("⏹", tr("Stop (end rehearsal)"), self._finish)
        btn_prev_sep = mk_btn("⏮", tr("Previous day separator"),
                              lambda: self._jump_to_separator(-1))
        self._btn_play = mk_btn("⏸", tr("Pause / resume"),
                                self._toggle_pause)
        btn_next_sep = mk_btn("⏭", tr("Next day separator"),
                              lambda: self._jump_to_separator(1))

        scrub = _Scrubber(bar)
        scrub.seeked.connect(self._on_scrubber_seeked)
        scrub.hovered.connect(self._on_scrubber_hovered)
        scrub.hover_left.connect(self._hide_hover_preview)
        scrub.set_entries(self._durations, self._sep_indexes)
        self._scrubber = scrub

        self._time_label = QLabel(_fmt_time(0) + " / " +
                                  _fmt_time(self._total_ms()), bar)
        self._time_label.setMinimumWidth(96)
        self._time_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self._slide_spin = QDoubleSpinBox(bar)
        self._slide_spin.setRange(0.5, 30.0)
        self._slide_spin.setSingleStep(0.5)
        self._slide_spin.setDecimals(1)
        self._slide_spin.setSuffix(" s")
        self._slide_spin.setValue(self._photo_s)
        self._slide_spin.setToolTip(tr("Seconds per photo (applies live)"))
        # Avoid wheel/focus churn (auto-memory feedback: input focus only
        # via click / Tab on QAbstractSpinBox).
        self._slide_spin.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._slide_spin.installEventFilter(self)
        self._slide_spin.valueChanged.connect(self._on_slide_time_changed)
        slide_lbl = QLabel(tr("Per slide:"), bar)

        self._btn_fs = mk_btn("⛶", tr("Toggle fullscreen"),
                              self._toggle_fullscreen)

        lay.addWidget(btn_stop)
        lay.addWidget(btn_prev_sep)
        lay.addWidget(self._btn_play)
        lay.addWidget(btn_next_sep)
        lay.addWidget(scrub, 1)
        lay.addWidget(self._time_label)
        lay.addSpacing(8)
        lay.addWidget(slide_lbl)
        lay.addWidget(self._slide_spin)
        lay.addWidget(self._btn_fs)

        bar.setSizePolicy(QSizePolicy.Policy.Preferred,
                          QSizePolicy.Policy.Fixed)
        bar.adjustSize()
        self._transport = bar

        # Hover preview — frameless top-level child so it can pop above
        # everything without clipping inside the bar.
        prev = QLabel(self)
        prev.setObjectName("CutPlayHoverPreview")
        prev.setWindowFlags(Qt.WindowType.ToolTip)
        prev.setStyleSheet(  # pragma: no-qss — slideshow scrubber preview
            "background-color: rgba(0, 0, 0, 220);"
            " border: 1px solid rgba(255, 255, 255, 90);"
            " color: #ffffff; padding: 4px;")
        prev.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prev.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        prev.hide()
        self._preview_label = prev

    # ── transport callbacks ──────────────────────────────────────────

    def _on_slide_time_changed(self, seconds: float) -> None:
        self._photo_s = float(seconds)
        self._photo_ms = max(200, int(seconds * 1000))
        self._recompute_durations()
        if self._scrubber is not None:
            self._scrubber.set_entries(self._durations, self._sep_indexes)
        self._preview_cache.clear()        # separator height depends on duration? no — but cheap to drop
        self._update_time_label()

    def _on_scrubber_seeked(self, index: int, _ms_offset: int) -> None:
        # Snap to the entry's start — seeking inside a clip is for v2.
        self._show_index(int(index))

    def _on_scrubber_hovered(self, index: int, x_in_scrubber: int) -> None:
        self._show_hover_preview(int(index), int(x_in_scrubber))

    def _jump_to_separator(self, direction: int) -> None:
        """Step to the previous / next ``("sep", _)`` or ``("opener", _)``
        entry. The opener counts as a separator so ⏮ from early entries
        still gives you a destination."""
        anchors = [
            i for i, (k, _p) in enumerate(self._entries)
            if k in ("sep", "opener")
        ]
        if not anchors:
            return
        if direction < 0:
            target = max((a for a in anchors if a < self._index),
                         default=anchors[0])
        else:
            target = min((a for a in anchors if a > self._index),
                         default=anchors[-1])
        self._show_index(target)

    def _update_play_icon(self) -> None:
        b = self._btn_play
        if b is None:
            return
        b.setText("▶" if self._paused else "⏸")

    # ── ticker + time read-out ───────────────────────────────────────

    def _entry_elapsed_ms(self) -> int:
        """How far into the current entry we are. For photos we read the
        QTimer's remaining ms; for videos QMediaPlayer.position()."""
        if not (0 <= self._index < len(self._entries)):
            return 0
        kind, payload = self._entries[self._index]
        if kind == "file" and getattr(payload, "kind", "") == "video":
            if self._player is None:
                return 0
            return max(0, int(self._player.position()))
        # photo / opener / separator
        total = self._photo_ms
        if self._paused or not self._timer.isActive():
            return 0
        remaining = int(self._timer.remainingTime())
        if remaining < 0:
            return 0
        return max(0, total - remaining)

    def _entry_total_ms(self, index: int) -> int:
        if not (0 <= index < len(self._entries)):
            return self._photo_ms
        kind, payload = self._entries[index]
        if kind == "file" and getattr(payload, "kind", "") == "video":
            d = int(getattr(payload, "duration_ms", 0) or 0)
            return d if d > 0 else self._photo_ms
        if kind == "opener" and self._opener_image is None:
            return 0
        return self._photo_ms

    def _recompute_durations(self) -> None:
        self._durations = [
            self._entry_total_ms(i) for i in range(len(self._entries))
        ]
        self._sep_indexes = [
            i for i, (k, _p) in enumerate(self._entries) if k == "sep"
        ]

    def _total_ms(self) -> int:
        return sum(self._durations) or 1

    def _played_ms(self) -> int:
        if self._index < 0:
            return 0
        prefix = sum(self._durations[: self._index])
        return prefix + self._entry_elapsed_ms()

    def _tick(self) -> None:
        if not (0 <= self._index < len(self._entries)):
            return
        cur_total = self._durations[self._index] or 1
        frac = self._entry_elapsed_ms() / cur_total
        if self._scrubber is not None:
            self._scrubber.set_playhead(self._index, frac)
        self._update_time_label()

    def _update_time_label(self) -> None:
        if self._time_label is None:
            return
        self._time_label.setText(
            f"{_fmt_time(self._played_ms())} / "
            f"{_fmt_time(self._total_ms())}")

    # ── hover preview ────────────────────────────────────────────────

    def _show_hover_preview(self, index: int, x_in_scrubber: int) -> None:
        prev = self._preview_label
        if prev is None or not (0 <= index < len(self._entries)):
            return
        pm = self._preview_pixmap(index)
        if pm.isNull():
            prev.hide()
            return
        prev.setPixmap(pm)
        prev.adjustSize()
        # Place the popup horizontally above the cursor on the scrubber,
        # vertically just above the transport bar.
        scrub = self._scrubber
        if scrub is None or self._transport is None:
            return
        scrub_origin = scrub.mapTo(self, QPoint(0, 0))
        px = scrub_origin.x() + x_in_scrubber - prev.width() // 2
        px = max(8, min(self.width() - prev.width() - 8, px))
        py = self._transport.geometry().top() - prev.height() - 8
        py = max(8, py)
        prev.move(self.mapToGlobal(QPoint(px, py)))
        prev.show()
        prev.raise_()

    def _hide_hover_preview(self) -> None:
        if self._preview_label is not None:
            self._preview_label.hide()

    def _preview_pixmap(self, index: int) -> QPixmap:
        cached = self._preview_cache.get(index)
        if cached is not None:
            return cached
        kind, payload = self._entries[index]
        target_w, target_h = 220, 124
        pm = QPixmap()
        if kind == "opener":
            if self._opener_image is not None:
                pm = QPixmap.fromImage(self._opener_image)
        elif kind == "sep":
            pm = QPixmap.fromImage(
                render_separator_image(
                    day_number=payload,
                    date=getattr(self._day_meta.get(payload), "date", None),
                    location=getattr(
                        self._day_meta.get(payload), "location", None),
                    description=getattr(
                        self._day_meta.get(payload), "description", "") or "",
                    aspect=self._aspect, height=target_h,
                    card_style=self._card_style,
                    seed_key=f"{self._seed_prefix}:{payload}"))
        else:
            relpath = getattr(payload, "export_relpath", None)
            if relpath:
                pm = load_pixmap(self._root / relpath)
        if not pm.isNull():
            pm = pm.scaled(target_w, target_h,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        self._preview_cache[index] = pm
        return pm

    # ── event filter (spinbox guard) ─────────────────────────────────

    def eventFilter(self, obj, ev):  # noqa: N802
        # Per auto-memory feedback: don't let the wheel/hover mutate the
        # spinbox value; only click/Tab + arrow keys may move it.
        if obj is self._slide_spin and ev.type() == QEvent.Type.Wheel \
                and not self._slide_spin.hasFocus():
            ev.ignore()
            return True
        return super().eventFilter(obj, ev)
