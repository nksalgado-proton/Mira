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

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QLabel, QSizePolicy, QStackedLayout, QWidget)

from core import audio_library, cut_overlay
from mira.ui.design.blurred_photo_canvas import BlurredPhotoCanvas
from mira.ui.i18n import tr
from mira.ui.media.image_loader import load_pixmap
from mira.ui.shared.separator_card import render_separator_image

log = logging.getLogger(__name__)


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
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CutPlayerDialog")
        self.setWindowTitle(tr("Play — the rehearsal"))
        # Modality must be declared BEFORE the first show: the launch is
        # start() then exec(), and applying modality to an already-visible
        # window is unreliable on Windows (owner left half-disabled).
        self.setModal(True)
        self.setStyleSheet("background-color: black;")  # the show's canvas —
        # a deliberate exception to the no-inline rule: this surface is the
        # slideshow itself, not app chrome, and must stay black in any theme.
        self._entries = list(entries)
        self._root = Path(event_root)
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
        # Spec/81 §3.1 — live overlays. ``overlay_fields`` is the
        # multi-select (when / where / how¹ / how²) and the resolver
        # returns one :class:`FrameProvenance` per relpath. When either
        # is missing or empty, the overlay simply never paints — the
        # rehearsal still plays cleanly. The mode (embedded / burn_in)
        # only matters at export; Play always draws live.
        self._overlay_fields: tuple = tuple(overlay_fields or ())
        self._provenance_resolver = provenance_resolver
        self._overlay_label: Optional[QLabel] = None

        self._layout = QStackedLayout(self)
        self._layout.setStackingMode(QStackedLayout.StackingMode.StackAll)
        # spec/61 §5.4 (Nelson 2026-06-15): blurred-fill canvas + framed
        # photo replaces the plain centred ``QLabel``. The dialog's black
        # background is the canvas of last resort — between the canvas
        # and the frame, aspect-mismatched photos no longer ride on
        # bare black, and 16:9 separators don't letterbox in a square
        # window.
        self._photo = BlurredPhotoCanvas(
            parent=self, inner_pad=28, radius=10.0,
        )
        # Ignored policy: the canvas's pixmap must NEVER drive the layout.
        # The previous QLabel set its minimumSizeHint to its pixmap size,
        # which a top-level layout enforced as the WINDOW minimum — after
        # fullscreen the window could never shrink back to its windowed
        # size, and the min-size fight inside Windows' synchronous resize
        # negotiation could wedge the event loop (2026-06-12 freeze).
        self._photo.setSizePolicy(QSizePolicy.Policy.Ignored,
                                  QSizePolicy.Policy.Ignored)
        self._layout.addWidget(self._photo)
        self._missing_label = None              # lazy "(file missing)"
        self._normal_geometry = None        # saved on entering fullscreen
        self._video_widget = None           # lazy — QtMultimedia on demand
        self._player = None
        self._video_audio = None
        self._music = None
        self._music_audio = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.advance)

        # Build the overlay label lazily on first frame that has text;
        # the empty-fields case skips construction entirely so a Cut
        # without overlays is byte-for-byte the pre-spec/81 player.
        if self._overlay_fields and self._provenance_resolver is not None:
            self._overlay_label = QLabel(self)
            self._overlay_label.setObjectName("CutPlayOverlay")
            # Inline styling on the rehearsal canvas mirrors the existing
            # ``setStyleSheet("background-color: black;")`` on the dialog
            # itself — Play is the show, not app chrome, so it owns its
            # pixels rather than riding the theme QSS.
            self._overlay_label.setStyleSheet(
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

    # ── lazy multimedia ──────────────────────────────────────────────

    def _ensure_video(self) -> None:
        if self._player is not None:
            return
        from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PyQt6.QtMultimediaWidgets import QVideoWidget
        self._video_widget = QVideoWidget(self)
        self._layout.addWidget(self._video_widget)
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
        # saved geometry once the transition settles (singleShot(0) — the
        # reliable Windows ordering, see the overlay pattern).
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
        elif kind == "sep":
            self._show_image(self._separator_image(payload))
            if not self._paused:
                self._timer.start(self._photo_ms)
        elif getattr(payload, "kind", "photo") == "video":
            self._show_video(self._root / payload.export_relpath)
        else:
            pm = load_pixmap(self._root / payload.export_relpath)
            self._show_pixmap(pm)
            if not self._paused:
                self._timer.start(self._photo_ms)
        # Refresh the overlay AFTER the frame paint (the frame setup
        # raises_() its widget; the overlay then raises ABOVE it). Opener
        # / separator slides carry no provenance — the helper hides the
        # label for them.
        self._update_overlay(kind, payload)

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
        self._layout.setCurrentWidget(self._photo)

    def _show_missing_label(self) -> None:
        if self._missing_label is None:
            from PyQt6.QtWidgets import QLabel
            self._missing_label = QLabel(self)
            self._missing_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._missing_label.setStyleSheet(
                "color: #cccccc; background: transparent;")
            self._layout.addWidget(self._missing_label)
        self._missing_label.setText(tr("(file missing)"))
        self._layout.setCurrentWidget(self._missing_label)

    def _hide_missing_label(self) -> None:
        if self._missing_label is not None:
            self._missing_label.setText("")

    def _fit_current(self) -> None:
        pm = getattr(self, "_raw_pixmap", QPixmap())
        if pm.isNull():
            return
        # Pre-scale the raw pixmap to the dialog's current size so the
        # canvas's per-paint scale-into-frame stays cheap on large
        # originals. The BlurredPhotoCanvas itself runs the
        # KeepAspectRatio fit + blurred backdrop on whatever it gets.
        size = self.size()
        if size.width() > 0 and size.height() > 0:
            pm = pm.scaled(size, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        self._photo.setPixmap(pm)

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self._fit_current()
        self._position_overlay()

    # ── overlays (spec/81 §3.1) ──────────────────────────────────────

    def _update_overlay(self, kind: str, payload) -> None:
        """Recompute the overlay text for the current frame and place it
        bottom-left over the frame. Hides the label on slides that carry
        no provenance (opener / separator) and on any miss-by-resolver
        (a relpath the gateway can't join — graceful, no crash)."""
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
        # adjustSize() before reading height for placement
        lbl.adjustSize()
        self._position_overlay()
        lbl.raise_()
        lbl.show()

    def _position_overlay(self) -> None:
        """Bottom-left corner with a 24 px margin. Width is capped at
        2/3 of the dialog so long where/how lines wrap instead of
        sprawling across the frame."""
        lbl = self._overlay_label
        if lbl is None or not lbl.isVisible() and not lbl.text():
            return
        margin = 24
        max_w = max(180, int(self.width() * 0.66))
        lbl.setMaximumWidth(max_w)
        lbl.adjustSize()
        y = max(margin, self.height() - lbl.height() - margin)
        lbl.move(margin, y)

    def mouseDoubleClickEvent(self, ev) -> None:  # noqa: N802
        self._toggle_fullscreen()
        ev.accept()

    def _show_video(self, path: Path) -> None:
        self._ensure_video()
        self._photo.hide()
        self._video_widget.show()
        self._layout.setCurrentWidget(self._video_widget)
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

    def _finish(self) -> None:
        self._timer.stop()
        for p in (self._player, self._music):
            if p is not None:
                p.stop()
        self.accept()

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        key = ev.key()
        if key == Qt.Key.Key_Escape:
            # One level down: full screen → window → end the rehearsal.
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
        # Pauses the rehearsal — the modal dialog steals focus, so a
        # running timer would tick on through. The post-close resume is
        # the user's call (they hit Space).
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
