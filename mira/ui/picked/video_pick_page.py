"""VideoPickPage — the Pick-phase video surface: WATCH + Pick/Skip,
on the ONE display engine (spec/63 5e).

spec/56 (the video workshop) returned Pick to ONE uniform decision pass:
the user watches the video and Picks or Skips the WHOLE video — the same
gesture as a photo (spec/48). All clip/snapshot authoring lives in the
Edit workshop.

The embedded :class:`PhotoViewport` owns the pixels AND the player
(spec/63 slice 3, arm-on-landing): the poster shows like a photo
placeholder, the QMediaPlayer arms on the settle beat, and the
poster→live flip happens only when real frames flow — the spec/59
no-black-frame guarantee without the retired ``PosterStack``. The page
keeps the chrome:

* **The skeleton** — BasePickSurface regions, aligned with the photo
  surface: Back · Help on TOP_BAR; the MediaHost border is the P/D
  indicator AND click target. (The legacy genre readout + bucket-level
  Reclassify dropdown retired here 2026-06-13 — photographic
  classification only surfaces in the Edit phase.)
* **COMPACT_ROW — the timeline**: the neutral playback bar with the
  playhead + click-to-jump, plus the position / duration readout, fed
  by the viewport's timeline signals.
* **NAV — the transport**: ⏮/⏭ cell nav · ⏮ Start · ◀ Frame ·
  ▶ Play / ⏸ Pause · Frame ▶ · End ⏭. Frame stepping stays at 1/fps
  from the ffprobe metadata.

The LOCKED key map (spec/63 §4) arrives as viewport verbs: **P** Pick ·
**X** Skip · **Space** toggle · **C** cycle (videos are a BINARY ledger
— no video compare surface — so C degrades to Space's behaviour, the §4
rule) · **Tab** = play/pause (transport — the old Tab-cycles-state
binding is evicted) · **F/F11** fullscreen · **Esc** one level back.

Persistence is the shell's job: the page emits
``decision_verb_requested("pick"|"skip"|"toggle"|"cycle")`` and renders
whatever ``set_binary_state`` pushes — pure presentation, no gateway
access (charter §5.2).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFont, QKeyEvent, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
)

from mira.ui.base.surface import (
    BasePickSurface,
    back_button,
    help_button,
    set_transport_playing,
    transport_button,
)
from core.video_discovery import VideoItem
from core.video_extract import VideoMetadata, probe_video
from mira.ui.i18n import tr
from mira.ui.media.photo_viewport import PhotoViewport, ViewportItem

log = logging.getLogger(__name__)

_C_PLAYHEAD = QColor(0xFF, 0xFF, 0xFF)
# Neutral slate-grey playback bar (Nelson 2026-05-25): the timeline is a
# play-position + click-to-jump surface. The cull-state palette is
# reserved for actual P/D affordances (the MediaHost border); painting
# the bar red would read as Skip.
_C_BAR_BASE = QColor(0x4A, 0x52, 0x5C)


def _fmt(ms: int) -> str:
    ms = max(0, int(ms))
    m, rem = divmod(ms, 60_000)
    s, msec = divmod(rem, 1000)
    return f"{m}:{s:02d}.{msec:03d}"


class _Timeline(QWidget):
    """The playback timeline: neutral base bar + outlined playhead +
    click-to-jump."""

    seek_requested = pyqtSignal(int)     # ms

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoTimeline")
        self.setMinimumHeight(48)
        self.setToolTip(tr("Click to jump to that position."))
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._dur = 0
        self._pos = 0

    def set_data(self, *, duration_ms: int, pos_ms: int) -> None:
        self._dur = max(0, duration_ms)
        self._pos = max(0, pos_ms)
        self.update()

    def _x(self, ms: int) -> int:
        if self._dur <= 0:
            return 0
        return int(round((ms / self._dur) * max(1, self.width() - 1)))

    def mousePressEvent(self, ev):  # noqa: N802 — click-to-jump
        if ev.button() == Qt.MouseButton.LeftButton and self._dur > 0:
            frac = min(1.0, max(0.0, ev.position().x() / max(1, self.width())))
            self.seek_requested.emit(int(round(frac * self._dur)))
            ev.accept()
            return
        super().mousePressEvent(ev)

    def paintEvent(self, ev):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            w, h = self.width(), self.height()
            bar_t = 14
            bar_b = h - 14
            # Base bar — ALWAYS painted, even before the duration is
            # known (docs/18 §"Eyeball follow-up" #3: the timeline was
            # invisibly blank until QtMultimedia's async durationChanged
            # arrived; the bar must always be there).
            p.fillRect(0, bar_t, w, bar_b - bar_t, _C_BAR_BASE)
            if self._dur <= 0:
                return                       # no position to paint yet
            # Playhead — white 2-px stem with a black halo so it reads
            # on both bright and dark frames behind the bar.
            px = self._x(self._pos)
            halo = QPen(QColor(0, 0, 0, 200))
            halo.setWidth(4)
            p.setPen(halo)
            p.drawLine(px, 0, px, h)
            core_pen = QPen(_C_PLAYHEAD)
            core_pen.setWidth(2)
            p.setPen(core_pen)
            p.drawLine(px, 0, px, h)
        finally:
            p.end()


class VideoPickPage(QWidget):
    """The Pick-phase video surface. :meth:`load` videos, then show.
    Emits ``back_requested`` on Esc / Back."""

    back_requested = pyqtSignal()
    fullscreen_changed = pyqtSignal(bool)   # shell hides/restores chrome
    # Cell navigation — emitted from the nav buttons, wheel, the
    # viewport's edge_reached, and Left/Right/PageUp/PageDown so the
    # video reads as just another cell in the Day Grid; PickPage
    # translates them into ``_navigate(±1)`` (spec/32 §2.7).
    prev_bucket_requested = pyqtSignal()
    next_bucket_requested = pyqtSignal()
    # The user spoke a decision verb (spec/63 §4): "pick" (P) / "skip"
    # (X) / "toggle" (Space) / "cycle" (C + border click — the shell
    # degrades it to toggle: videos are a binary ledger). The shell
    # persists and pushes the new state back via :meth:`set_binary_state`.
    decision_verb_requested = pyqtSignal(str)
    # Emitted whenever the viewport lands on a different item index.
    current_item_changed = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoPickPage")
        self._items: list[VideoItem] = []
        self._index = 0
        self._duration_ms = 0
        self._pos_ms = 0
        self._frame_ms = 33                  # 1/fps; set per video on load
        self._binary_state = "skipped"       # shell pushes via set_binary_state
        self._fullscreen = False
        self._msg = ""                       # transient readout message
        self._wh = ""                        # "WxH @fps" readout
        self._nav_context = "bucket"
        # Per-path ffprobe cache — probing is the only metadata read the
        # watch surface needs (fps for frame stepping, duration seed).
        self._meta_cache: dict[Path, VideoMetadata] = {}
        self._build_ui()
        self._install_keyboard_focus()

    # ── construction ────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # ── BasePickSurface composition (spec/42 Phase A, 2026-06-06) ──
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._surface = BasePickSurface()
        outer.addWidget(self._surface)

        # ── TOP_BAR — Back left · stretch · Help right.
        # Nelson 2026-06-13: the genre readout + bucket-level Reclassify
        # dropdown retired here — photographic classification only
        # surfaces in the Edit phase.
        self._btn_back = back_button()
        self._btn_back.setToolTip(tr("Return to the day grid  (Esc)"))
        self._btn_back.clicked.connect(self._on_back)
        self._surface.top_bar.layout().addWidget(self._btn_back)

        self._surface.top_bar.layout().addStretch(1)

        self._btn_help = help_button()
        self._btn_help.setToolTip(tr("Keyboard shortcuts  (F1)"))
        self._btn_help.clicked.connect(self._show_shortcuts)
        self._surface.top_bar.layout().addWidget(self._btn_help)

        # ── STATE_BAR — hidden. The canonical MediaHost border is the
        # P/D indicator AND click target (spec/42 Phase D): border click
        # → the "cycle" decision verb (the shell degrades it to toggle —
        # binary ledger), border colour driven by `set_binary_state`.
        self._surface.set_region_visible("state_bar", False)
        self._surface.media_border_clicked.connect(
            lambda: self.decision_verb_requested.emit("cycle"))

        # ── MEDIA — the one display engine (spec/63 5e): poster, player
        # (arm-on-landing), nav and the locked key grammar live in the
        # embedded PhotoViewport. PosterStack + the page-owned
        # QMediaPlayer retired — the no-black-frame guarantee is the
        # viewport's poster→live flip.
        self._viewport = PhotoViewport()
        vp = self._viewport
        vp.current_changed.connect(self._on_current_changed)
        vp.edge_reached.connect(self._on_edge)
        vp.pick_requested.connect(
            lambda: self.decision_verb_requested.emit("pick"))
        vp.skip_requested.connect(
            lambda: self.decision_verb_requested.emit("skip"))
        vp.toggle_requested.connect(
            lambda: self.decision_verb_requested.emit("toggle"))
        vp.cycle_requested.connect(
            lambda: self.decision_verb_requested.emit("cycle"))
        vp.fullscreen_requested.connect(self._toggle_fullscreen)
        vp.back_requested.connect(self._on_esc)
        vp.video_position_changed.connect(self._on_position)
        vp.video_duration_changed.connect(self._on_duration)
        vp.video_playing_changed.connect(self._on_playing_changed)
        vp.video_error.connect(self._on_player_error)
        self._surface.set_media(self._viewport)

        # ── TOOLS — empty (Pick decides the whole video; the workshop
        # is the Edit phase's marker timeline).
        self._surface.set_region_visible("tools", False)

        # ── COMPACT_ROW: the timeline + a time readout ────────────────
        self._timeline = _Timeline()
        self._timeline.seek_requested.connect(self._seek_to)
        self._surface.compact_row.layout().addWidget(self._timeline, stretch=1)

        self._time = QLabel("0:00.000 / 0:00.000")
        self._time.setObjectName("VideoTime")
        self._time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._surface.compact_row.layout().addWidget(self._time)

        # ── NAV: the transport line ───────────────────────────────────
        n = QWidget()
        self._nav_line = n
        n.setObjectName("VideoNavLine")
        nrow = QHBoxLayout(n)
        nrow.setContentsMargins(10, 4, 10, 8)
        nrow.setSpacing(6)
        self._nav_pb = QPushButton(tr("⏮ Bucket"))
        self._nav_pb.setToolTip(tr("Previous bucket (another video or a photo bucket)"))
        self._nav_pb.clicked.connect(self.prev_bucket_requested.emit)
        # U+FE0E forces TEXT presentation — the bare emoji renders with
        # its own coloured background on Windows (Nelson 2026-06-11).
        self._nav_start = QPushButton(tr("⏮︎ Start"))
        self._nav_start.setToolTip(tr("Jump to the start of the video"))
        self._nav_start.clicked.connect(lambda: self._seek_to(0))
        self._nav_pf = QPushButton(tr("◀ Frame"))
        self._nav_pf.setToolTip(tr("Step one frame back"))
        self._nav_pf.clicked.connect(lambda: self._step(-1))
        self._nav_play = transport_button(tr("Play / pause the video  (Tab)"))
        self._nav_play.clicked.connect(self._viewport.video_toggle_play)
        self._nav_nf = QPushButton(tr("Frame ▶"))
        self._nav_nf.setToolTip(tr("Step one frame forward"))
        self._nav_nf.clicked.connect(lambda: self._step(1))
        self._nav_end = QPushButton(tr("End ⏭︎"))
        self._nav_end.setToolTip(tr("Jump to the end of the video"))
        self._nav_end.clicked.connect(
            lambda: self._seek_to(self._duration_ms)
        )
        self._nav_nb = QPushButton(tr("Bucket ⏭"))
        self._nav_nb.setToolTip(tr("Next bucket (another video or a photo bucket)"))
        self._nav_nb.clicked.connect(self.next_bucket_requested.emit)
        # Spread the 7 transport buttons evenly across the nav width.
        for i, w in enumerate((
            self._nav_pb, self._nav_start, self._nav_pf,
            self._nav_play,
            self._nav_nf, self._nav_end, self._nav_nb,
        )):
            if i > 0:
                nrow.addStretch(1)
            nrow.addWidget(w)
        self._surface.nav.layout().addWidget(n, stretch=1)

    def _install_keyboard_focus(self) -> None:
        """Buttons never take focus (a watch+shortcut tool, no text
        entry); the viewport is the ONE focus target — the page's focus
        proxies to it so the locked grammar always hears the keys."""
        for w in self.findChildren(QWidget):
            if w is self._viewport or self._viewport.isAncestorOf(w):
                continue
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocusProxy(self._viewport)

    def focusNextPrevChild(self, nxt: bool) -> bool:  # noqa: N802
        return False

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.setFocus()

    def hideEvent(self, event) -> None:  # noqa: N802
        """Every leave path (Esc · Back · stack switch · app close)
        hides the page — the single reliable place to stop the
        audio/video (docs/18 §"Eyeball follow-up" #4). The viewport's
        teardown is idempotent."""
        self._viewport.shutdown_video()
        set_transport_playing(self._nav_play, False)
        super().hideEvent(event)

    # ── public API ──────────────────────────────────────────────────

    def load(
        self,
        items: list[VideoItem],
        *,
        nav_context: str = "bucket",
    ) -> bool:
        """Open ``items`` for watching + the whole-video P/D decision.

        ``nav_context`` (spec/32 §2.7): ``"bucket"`` keeps the legacy
        bucket-step labels on the two outer nav buttons; ``"day_grid"``
        relabels them "← Previous" / "Next →" with Day-Grid-aware
        tooltips. The underlying signals stay
        ``prev_bucket_requested`` / ``next_bucket_requested``; PickPage
        translates them into ``_navigate(±1)``.
        """
        items = list(items)
        if not items:
            return False
        self._items = items
        self._index = 0
        self._nav_context = nav_context
        bucket_mode = nav_context == "bucket"
        if bucket_mode:
            self._nav_pb.setText(tr("⏮ Bucket"))
            self._nav_pb.setToolTip(
                tr("Previous bucket (another video or a photo bucket)"))
            self._nav_nb.setText(tr("Bucket ⏭"))
            self._nav_nb.setToolTip(
                tr("Next bucket (another video or a photo bucket)"))
        else:
            self._nav_pb.setText(tr("← Previous"))
            self._nav_pb.setToolTip(
                tr("Previous cell in the day (photo, video, or cluster)"))
            self._nav_nb.setText(tr("Next →"))
            self._nav_nb.setToolTip(
                tr("Next cell in the day (photo, video, or cluster)"))
        # Hand the list to the viewport. The Day-Grid poster (the
        # spec/59 black-frame guarantee) rides each item as a
        # host-supplied pixmap — the daygrid thumb store is not the
        # photo-cache thumb tier, so the page bridges it.
        vitems = []
        for it in items:
            poster_pm = None
            poster = getattr(it, "poster", None)
            if poster:
                pm = QPixmap(str(poster))
                if not pm.isNull():
                    poster_pm = pm
            vitems.append(ViewportItem(
                path=Path(it.path), kind="video",
                payload=it, pixmap=poster_pm))
        self._viewport.set_items(vitems, 0)
        self.setFocus()
        return True

    def set_binary_state(self, state: str) -> None:
        """Render the WHOLE video's P/D state. ``state`` is a phase-state
        value (``picked`` / ``skipped`` / ``candidate``). Called by the
        shell when the page loads a video AND after the shell persists a
        decision verb — the MediaHost border is the canonical indicator
        (spec/42: one photo-border-as-state mechanism across all media
        surfaces)."""
        self._binary_state = state
        self._surface.set_media_state(
            state if state in ("picked", "candidate") else "skipped")

    @staticmethod
    def video_item(path: Path) -> VideoItem:
        p = Path(path)
        # A missing / unreadable file must never crash routing (same
        # never-crash discipline as the photo culler) — fall back to
        # the epoch so the type stays datetime and ordering is still
        # deterministic.
        try:
            ts = datetime.fromtimestamp(p.stat().st_mtime)
        except OSError:
            ts = datetime.fromtimestamp(0)
        return VideoItem(
            path=p, source_folder=p.parent.name,
            timestamp=ts, day=None,
        )

    # ── per-video ───────────────────────────────────────────────────

    @property
    def _item(self) -> Optional[VideoItem]:
        if 0 <= self._index < len(self._items):
            return self._items[self._index]
        return None

    def _probe(self, item: VideoItem) -> Optional[VideoMetadata]:
        """Cached ffprobe — once per path per page lifetime."""
        cached = self._meta_cache.get(item.path)
        if cached is not None:
            return cached
        meta = probe_video(item.path)
        self._meta_cache[item.path] = meta
        return meta

    def _on_current_changed(self, index: int) -> None:
        """The viewport landed on ``index`` — dress the chrome (probe
        metadata, duration seed, readouts). Pixels + the player are the
        viewport's."""
        if not self._items or not (0 <= index < len(self._items)):
            return
        self._index = index
        self.current_item_changed.emit(self._index)
        item = self._items[self._index]
        self._duration_ms = 0
        self._pos_ms = 0
        self._msg = ""
        self._wh = ""
        try:
            meta = self._probe(item)
            fps = meta.fps if meta and meta.fps and meta.fps > 0 else 30.0
            self._frame_ms = max(1, int(round(1000.0 / fps)))
            if meta and meta.width and meta.height:
                self._wh = (
                    f"{meta.display_width}x{meta.display_height} "
                    f"@{fps:g}fps"
                )
            # docs/18 §"Eyeball follow-up" #3: seed the duration from
            # the ffprobe metadata so the timeline is there immediately
            # — never blocked on QtMultimedia's async durationChanged
            # (which sometimes arrives late, or 0). _on_duration still
            # corrects it from the player.
            dur = int(getattr(meta, "duration_ms", 0) or 0)
            if dur > 0:
                self._duration_ms = dur
        except Exception:  # noqa: BLE001 — probe is best-effort
            self._frame_ms = 33
        self._refresh()

    # ── refresh ─────────────────────────────────────────────────────

    def _refresh(self) -> None:
        self._timeline.set_data(
            duration_ms=self._duration_ms, pos_ms=self._pos_ms)
        self._time.setText(
            f"{_fmt(self._pos_ms)} / {_fmt(self._duration_ms)}")

    def _set_msg(self, text: str) -> None:
        """Transient message — shown in the time readout slot (the
        dedicated info line retired with the workshop chrome)."""
        self._msg = text
        if text:
            self._time.setText(text)
        else:
            self._refresh()

    def _on_player_error(self, msg: str) -> None:
        """A graceful QMediaPlayer failure (corrupt / unsupported codec
        — NOT a native crash), passed through by the viewport. Tell the
        user instead of a silent blank; Pick/Skip still works."""
        self._set_msg(
            tr("Cannot play this video ({e}) — Pick/Skip still works.")
            .replace("{e}", str(msg))
        )

    # ── player signals (via the viewport's timeline API) ────────────

    def _on_duration(self, dur_ms: int) -> None:
        self._duration_ms = max(0, int(dur_ms))
        self._refresh()

    def _on_position(self, pos_ms: int) -> None:
        self._pos_ms = max(0, int(pos_ms))
        self._refresh()

    def _on_playing_changed(self, playing: bool) -> None:
        set_transport_playing(self._nav_play, playing)

    # ── navigation ──────────────────────────────────────────────────

    def _seek_to(self, ms: int) -> None:
        ms = max(0, min(self._duration_ms or ms, int(ms)))
        self._pos_ms = ms
        self._viewport.video_seek(ms)
        self._refresh()

    def _step(self, frames: int) -> None:
        self._seek_to(self._pos_ms + frames * self._frame_ms)

    def _prev_video(self) -> None:
        if self._index > 0:
            self._viewport.show_index(self._index - 1)

    def _next_video(self) -> None:
        if self._index < len(self._items) - 1:
            self._viewport.show_index(self._index + 1)

    def _on_edge(self, delta: int) -> None:
        """The viewport stepped past either end of the item list —
        cross to the neighbouring Day-Grid cell (spec/32 §2.7)."""
        if delta < 0:
            self.prev_bucket_requested.emit()
        else:
            self.next_bucket_requested.emit()

    # ── fullscreen (the locked map: F / F11 on every photo surface) ──

    def _toggle_fullscreen(self) -> None:
        win = self.window()
        if win is None:
            return
        self._fullscreen = not self._fullscreen
        if self._fullscreen:
            win.showFullScreen()
        else:
            win.showNormal()
        self.fullscreen_changed.emit(self._fullscreen)
        self.setFocus()

    def _exit_fullscreen(self) -> bool:
        if self._fullscreen:
            self._toggle_fullscreen()
            return True
        return False

    def _on_esc(self) -> None:
        """Esc — one level back (spec/63 §4): fullscreen → windowed → out."""
        if not self._exit_fullscreen():
            self._on_back()

    def _on_back(self) -> None:
        self.back_requested.emit()

    # ── keyboard ────────────────────────────────────────────────────

    def _show_shortcuts(self) -> None:
        """The spec/63 §4 locked map for the video Pick surface."""
        from mira.ui.base.shortcuts import show_shortcuts
        show_shortcuts(self, tr("Pick — video"), [
            (tr("P / X"),           tr("Pick / Skip the whole video")),
            (tr("Space · C"),       tr("Toggle Pick ⇄ Skip")),
            (tr("Tab"),             tr("Play / pause")),
            (tr("◀ / ▶ · ▲ / ▼"),    tr("Previous / next cell")),
            (tr("Page Up / Down"),  tr("Previous / next cell")),
            (tr("Mouse wheel"),     tr("Previous / next cell")),
            (tr("Click timeline"),  tr("Jump to that position")),
            (tr("Click the border"), tr("Toggle Pick / Skip (whole video)")),
            (tr("F / F11"),         tr("Fullscreen")),
            (tr("Esc"),             tr("Back")),
            (tr("F1 · ?"),          tr("This help")),
        ])

    # Mouse-wheel notch accumulator over the CHROME (timeline, nav,
    # top bar) — over the media the viewport's own wheel runs first
    # (in-list nav + edge → the same cell-crossing signals).
    _WHEEL_STEP_UNITS = 120

    def __init_wheel_state(self) -> None:
        if not hasattr(self, "_wheel_accum"):
            self._wheel_accum = 0

    def wheelEvent(self, event) -> None:  # noqa: N802
        """Wheel → cell navigation (Nelson 2026-05-23). The natural
        wheel gesture is prev/next cell, not in-clip seek (which the
        timeline + frame buttons already cover)."""
        self.__init_wheel_state()
        delta_y = event.angleDelta().y()
        if delta_y == 0:
            super().wheelEvent(event)
            return
        if self._wheel_accum and (delta_y > 0) != (self._wheel_accum > 0):
            self._wheel_accum = 0
        self._wheel_accum += delta_y
        while self._wheel_accum >= self._WHEEL_STEP_UNITS:
            self._wheel_accum -= self._WHEEL_STEP_UNITS
            self.prev_bucket_requested.emit()
        while self._wheel_accum <= -self._WHEEL_STEP_UNITS:
            self._wheel_accum += self._WHEEL_STEP_UNITS
            self.next_bucket_requested.emit()
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """The viewport owns the locked grammar (focus proxies there);
        the page adds PageUp/Down, R and F1 — and carries the
        stray-focus fallback so a key landing on the page itself routes
        to the SAME verbs (never a dead key on a cull surface)."""
        k = event.key()
        if k == Qt.Key.Key_Escape:
            self._on_esc()
        elif k in (Qt.Key.Key_F, Qt.Key.Key_F11):
            self._toggle_fullscreen()
        elif k == Qt.Key.Key_P:
            self.decision_verb_requested.emit("pick")
        elif k == Qt.Key.Key_X:
            self.decision_verb_requested.emit("skip")
        elif k == Qt.Key.Key_Space:
            self.decision_verb_requested.emit("toggle")
        elif k == Qt.Key.Key_C:
            self.decision_verb_requested.emit("cycle")
        elif k == Qt.Key.Key_Tab:
            self._viewport.video_toggle_play()
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_Up,
                   Qt.Key.Key_PageUp):
            # Multi-item: navigate within the list before crossing to
            # the previous cell; at the first item, go to the prev cell.
            if len(self._items) > 1 and self._index > 0:
                self._prev_video()
            else:
                self.prev_bucket_requested.emit()
        elif k in (Qt.Key.Key_Right, Qt.Key.Key_Down,
                   Qt.Key.Key_PageDown):
            if len(self._items) > 1 and self._index < len(self._items) - 1:
                self._next_video()
            else:
                self.next_bucket_requested.emit()
        elif k == Qt.Key.Key_F1:
            self._show_shortcuts()
        else:
            super().keyPressEvent(event)
            return
        event.accept()
