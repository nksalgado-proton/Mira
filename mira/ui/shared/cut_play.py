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
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QLabel, QSizePolicy, QStackedLayout, QWidget)

from core import audio_library
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

        self._layout = QStackedLayout(self)
        self._layout.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self._photo = QLabel(self)
        self._photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Ignored policy: the label's pixmap must NEVER drive the layout.
        # A QLabel's minimum size hint is its pixmap size, and a top-level
        # layout enforces that as the WINDOW minimum — after fullscreen the
        # window could never shrink back to its windowed size, and the
        # min-size fight inside Windows' synchronous resize negotiation
        # could wedge the event loop (2026-06-12 freeze).
        self._photo.setSizePolicy(QSizePolicy.Policy.Ignored,
                                  QSizePolicy.Policy.Ignored)
        self._layout.addWidget(self._photo)
        self._normal_geometry = None        # saved on entering fullscreen
        self._video_widget = None           # lazy — QtMultimedia on demand
        self._player = None
        self._video_audio = None
        self._music = None
        self._music_audio = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.advance)

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
            self._photo.setText(tr("(file missing)"))
            return
        self._photo.setText("")
        self._fit_current()
        self._layout.setCurrentWidget(self._photo)

    def _fit_current(self) -> None:
        pm = getattr(self, "_raw_pixmap", QPixmap())
        if pm.isNull():
            return
        size = self.size()
        if size.width() > 0 and size.height() > 0:
            pm = pm.scaled(size, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        self._photo.setPixmap(pm)

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self._fit_current()

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
