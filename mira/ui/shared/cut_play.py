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
    QEasingCurve, QEvent, QPoint, QPointF, QPropertyAnimation,
    QRect, QSize, Qt, QTimer, QUrl, pyqtSignal)
from PyQt6.QtGui import (
    QColor, QImage, QPainter, QPen, QPixmap, QPolygonF)
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFrame, QGraphicsOpacityEffect,
    QHBoxLayout, QLabel, QPushButton, QSizePolicy, QStackedLayout,
    QVBoxLayout, QWidget)

from core import audio_library, cut_overlay
from mira.ui.base.surface import set_transport_playing, transport_button
from mira.ui.design import ghost_button
from mira.ui.design.blurred_photo_canvas import BlurredPhotoCanvas
from mira.ui.design.inputs import select
from mira.ui.i18n import tr
from mira.ui.media.image_loader import load_pixmap
from mira.ui.palette import PALETTE

#: Plain-text separator between overlay field GROUPS (When / Where / Camera
#: / Exposure) on the single-line Cut-play pill — the plain-text twin of the
#: Picker's HTML ``_FIELD_SEPARATOR``. Heavier than the ``·`` used *within* a
#: group so the eye can still tell the groups apart.
_OVERLAY_FIELD_SEPARATOR = "  •  "


def _theme_mode() -> str:
    """Active theme (``'dark'`` / ``'light'``). Mirrors the helper in
    :mod:`mira.ui.media.transport_bar`; defaults to ``'dark'`` when
    the QApplication property isn't set yet (early tests)."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    return (app.property("theme") if app else None) or "dark"
from mira.ui.shared.separator_card import render_separator_image

log = logging.getLogger(__name__)


#: spec/140 §1 — watchdog for the photo→video swap. The first
#: ``videoFrameChanged`` typically arrives within ~50–150 ms of
#: ``setSource`` + ``play``. If it doesn't (codec mismatch, file
#: missing the right frames, etc.) we MUST still swap so the show
#: advances instead of hanging on the previous photo. 750 ms is
#: comfortably past the worst real first-frame latency without
#: feeling like a stall.
_VIDEO_SWAP_TIMEOUT_MS = 750

#: spec/150 §2 — slack added to the END-of-clip watchdog. EndOfMedia
#: is the primary advance path; on Windows it lags the last visible
#: frame by a few hundred ms to ~1 s. The watchdog fires
#: ``duration_ms / rate + slack`` after the clip starts, only if
#: EndOfMedia hasn't already advanced. 150 ms is comfortably past
#: the QMediaPlayer scheduling jitter without holding a freeze the
#: user can notice.
_VIDEO_END_SLACK_MS = 150


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

    #: Horizontal inset on each end of the track so the first / last
    #: chapter diamond (half-width = 5 px) and the playhead's line never
    #: clip against the widget edge. Picked once here so paint + input
    #: mapping stay consistent.
    _SIDE_INSET = 8

    def x_to_index_and_offset(self, x: int) -> Tuple[int, int]:
        """Map a pixel x-coordinate to ``(entry_index, ms_inside_entry)``."""
        if not self._durations:
            return 0, 0
        usable = max(1, self.width() - 2 * self._SIDE_INSET)
        x = max(self._SIDE_INSET,
                min(self.width() - self._SIDE_INSET - 1, int(x)))
        target_ms = int(self.total_ms() * (x - self._SIDE_INSET) / usable)
        acc = 0
        for i, d in enumerate(self._durations):
            if acc + d > target_ms:
                return i, target_ms - acc
            acc += d
        return len(self._durations) - 1, self._durations[-1]

    # ── paint ────────────────────────────────────────────────────────

    def paintEvent(self, ev) -> None:  # noqa: N802
        """Draw the scrubber track + markers + playhead.

        spec/89 §12.6 — guarded against the Qt zombie case (same class
        as the documented ThumbGrid teardown crash): if a queued paint
        event fires after the underlying C++ widget has been destroyed
        (a leaked widget swept up by Python's garbage collector while
        the QApplication is still alive), every Qt call here raises
        ``RuntimeError: wrapped C/C++ object … has been deleted``.
        Swallow + log; the live-widget path is unchanged."""
        try:
            self._paint(ev)
        except RuntimeError:
            log.debug(
                "_Scrubber.paintEvent: widget already deleted — dropping "
                "paint", exc_info=True)

    def _paint(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self.rect()
        track_h = 6
        ty = (r.height() - track_h) // 2

        # spec/152 §X — theme-aware track + playhead. The transport bar
        # the scrubber sits in follows the active theme via QSS, so the
        # scrubber must too. The pre-fix white-with-60-alpha track was
        # invisible on the light theme's white card background; here we
        # read the palette so dark theme picks a light track and light
        # theme picks a dark one.
        mode = _theme_mode()
        pal = PALETTE[mode]
        track_unplayed = QColor(pal["track"])
        track_played = QColor(pal["ink_soft"])
        ink = QColor(pal["ink"])

        # Inset so the leftmost / rightmost markers and the playhead's
        # vertical line never half-clip against the widget edge — the
        # diamond at index 0 (a Cut whose opener is off but whose first
        # entry is a day separator) used to sit at sx=0 with half of
        # the polygon off-screen; same shape with the playhead at the
        # very start of the show.
        inset = self._SIDE_INSET
        usable_w = max(1, r.width() - 2 * inset)

        # Unplayed track.
        p.fillRect(QRect(inset, ty, usable_w, track_h), track_unplayed)
        if not self._durations:
            p.end()
            return
        total = self.total_ms()
        # Played portion.
        px = inset + int(usable_w * (self.playhead_ms() / total))
        played_w = max(0, px - inset)
        if played_w > 0:
            p.fillRect(QRect(inset, ty, played_w, track_h), track_played)
        # Chapter markers — soft amber diamonds floating just above the
        # track. A diamond reads as "bookmark" at a glance and doesn't
        # visually compete with the playhead.
        marker_fill = QColor(244, 184, 96, 245)        # warm amber
        # Stroke darkens on light theme (dark slate) so the diamond
        # has a visible outline on the white transport background.
        marker_stroke = (
            QColor(28, 22, 14, 170) if mode == "dark"
            else QColor(120, 80, 30, 200)
        )
        marker_pen = QPen(marker_stroke, 1.0)
        marker_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        half = 5
        cy = ty - half - 3
        for i in self._sep_indexes:
            if i >= len(self._durations):
                continue
            t = sum(self._durations[:i])
            sx = inset + int(usable_w * t / total)
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
        # Playhead — a slim vertical line (no circle thumb), matching
        # the Edit surface's MarkerTimeline. The black halo gives it
        # contrast on both themes: invisible on dark, visible on light;
        # the ``ink``-coloured core reads on both.
        halo_color = QColor(0, 0, 0, 180)
        halo = QPen(halo_color)
        halo.setWidth(3)
        p.setPen(halo)
        p.drawLine(px, ty - 6, px, ty + track_h + 6)
        core = QPen(ink)
        core.setWidth(1)
        p.setPen(core)
        p.drawLine(px, ty - 6, px, ty + track_h + 6)
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
        # spec/155 v2 — when the event has an MP4 map, the host passes
        # the absolute path here and the opener slot PLAYS the clip
        # (muted, native duration, one play) instead of showing the
        # rendered first-frame still in opener_image. opener_image stays
        # the fallback / scrubber-hover-thumb source either way.
        opener_video_path: Optional[Path] = None,
        # spec/155 v2 (Nelson 2026-06-29) — when the opener slot plays a
        # video, opener_image's baked title disappears. Threading the
        # title text + facts as plain strings lets Cut Play paint them
        # as a top-centre overlay on the video the same way the still
        # rendering bakes them. For the still path these are unused
        # (opener_image carries the baked text).
        opener_caption_tag: str = "",
        opener_caption_lines: Sequence[str] = (),
        card_style: str = "black",
        seed_prefix: str = "",
        overlay_fields: Sequence[str] = (),
        provenance_resolver: Optional[
            Callable[[object], Optional[cut_overlay.FrameProvenance]]
        ] = None,
        origin_resolver: Optional[Callable[[object], Optional[str]]] = None,
        resolve_path: Optional[Callable[[object], Path]] = None,
        video_rate: float = 1.0,
        transition_ms: Optional[int] = None,
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
        # The dialog itself uses the active theme — no inline override.
        # Only the slideshow canvas (the ``CutPlayCanvas`` stack widget
        # below) carries a forced black background via QSS so photos
        # paint against a neutral dark regardless of theme; everything
        # else (transport chrome, dialog frame) follows the theme.
        self._entries = list(entries)
        self._root = Path(event_root)
        self._resolve_path = resolve_path
        self._photo_s = float(photo_s)
        self._photo_ms = max(200, int(photo_s * 1000))
        self._day_meta = day_meta
        self._aspect = aspect
        self._music_tracks = list(music_tracks or [])
        self._opener_image = opener_image
        self._opener_video_path: Optional[Path] = (
            Path(opener_video_path) if opener_video_path is not None
            else None)
        self._opener_video_duration_cache: Optional[int] = None
        self._opener_caption_tag = str(opener_caption_tag or "")
        self._opener_caption_lines: Tuple[str, ...] = tuple(
            str(s) for s in (opener_caption_lines or ()))
        # spec/155 v2 — built lazily below, paints over the video widget
        # on sep / opener video slots so the day metadata + Cut title
        # stay visible while the clip plays.
        self._caption_label: Optional[QLabel] = None
        self._card_style = card_style
        self._seed_prefix = seed_prefix
        self._music_index = 0
        self._index = -1
        self._paused = False
        # spec/145 — rehearsal-only video-speed override. Compounds with
        # the clip's baked speed (a 2×-baked clip at 1.5× plays 3×) and
        # never touches the exported bytes or the PTE generator. The
        # control lives next to the per-photo spinner on the transport
        # bar; the host seeds the initial value from
        # ``Settings.default_video_speed`` (spec/138). Clamped > 0 so
        # ``setPlaybackRate`` never receives a degenerate value.
        try:
            seed_rate = float(video_rate)
        except (TypeError, ValueError):
            seed_rate = 1.0
        self._video_rate: float = seed_rate if seed_rate > 0 else 1.0
        self._video_rate_combo: Optional[QComboBox] = None
        # spec/152 §3 — per-Cut transition_ms, explicit. The host
        # passes the resolved value (per-Cut override > global
        # default > 2000 fallback) so the rehearsal's slot math
        # matches the budget + PTE [Times] exactly — without the
        # dialog having to walk ``parent()._settings()`` and risk
        # silently disagreeing with the host's own ``_transition_ms``.
        # ``None`` keeps the legacy parent-walking behaviour as a
        # fallback for stub tests that construct the dialog with no
        # host page.
        if transition_ms is None:
            self._explicit_transition_ms: Optional[int] = None
        else:
            try:
                self._explicit_transition_ms = max(
                    0, int(round(float(transition_ms))))
            except (TypeError, ValueError):
                self._explicit_transition_ms = None
        # Spec/81 §3.1 — live overlays.
        self._overlay_fields: tuple = tuple(overlay_fields or ())
        self._provenance_resolver = provenance_resolver
        self._overlay_label: Optional[QLabel] = None
        # spec/154 — the per-slide origin label (cross-event: source event
        # name + capture date), anchored at the TOP. Independent of the
        # bottom caption: wired only when ``origin_resolver`` is passed
        # (event-scope Play leaves it unset, so the label never builds).
        self._origin_resolver = origin_resolver
        self._origin_label: Optional[QLabel] = None

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
        # spec/152 §X — the slideshow canvas. ``CutPlayCanvas`` is the
        # one role with a forced black background (defined in QSS) so
        # photos always paint against a neutral dark surface even when
        # the active theme is light. WA_StyledBackground lets the QSS
        # rule actually apply to a plain QWidget.
        self._stack_widget.setObjectName("CutPlayCanvas")
        self._stack_widget.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True)
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
        # spec/140 §1 — no-black-frame swap state. ``_pending_video_sink``
        # is the QVideoSink the one-shot ``videoFrameChanged`` handler
        # is connected to; ``_video_swap_timer`` is the watchdog.
        self._pending_video_sink = None
        self._video_swap_timer: Optional[QTimer] = None
        # spec/150 §2 — END-of-clip watchdog. Symmetric to the swap
        # watchdog above. ``_video_end_timer`` is the single-shot timer
        # armed at the start of a video entry; ``_video_end_armed_for_index``
        # is the entry index the timer is bound to, so a late timer
        # fire after EndOfMedia already advanced is a no-op.
        self._video_end_timer: Optional[QTimer] = None
        self._video_end_armed_for_index: int = -1

        # spec/152 Phase 2 — crossfade overlay between consecutive
        # entries. A QLabel with a QGraphicsOpacityEffect captures
        # the OUTGOING entry's pixels (photo bitmap or last video
        # frame) at swap time and fades to transparent over
        # ``transition_ms`` while the next entry paints underneath.
        # Built lazily on the first swap so a single-slide Cut never
        # constructs it.
        self._transition_overlay: Optional[QLabel] = None
        self._transition_opacity: Optional[QGraphicsOpacityEffect] = None
        self._transition_anim: Optional[QPropertyAnimation] = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.advance)

        # Build the overlay label lazily — the empty-fields case skips
        # construction entirely so a Cut without overlays is byte-for-byte
        # the pre-spec/81 player.
        if self._overlay_fields and self._provenance_resolver is not None:
            self._overlay_label = QLabel(self)
            self._overlay_label.setObjectName("CutPlayOverlay")
            # Styling lives in QSS under ``QLabel#CutPlayOverlay`` so
            # the slideshow overlay keeps its white-on-translucent-black
            # look without an inline override.
            self._overlay_label.setAttribute(
                Qt.WidgetAttribute.WA_StyledBackground, True)
            # Single strip along the photo's bottom edge (mirrors the
            # Picker pill) — no wrap; the field groups join onto one line.
            self._overlay_label.setWordWrap(False)
            self._overlay_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._overlay_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.NoTextInteraction)
            self._overlay_label.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._overlay_label.hide()

        # spec/154 — the top-anchored origin label. Built lazily on the
        # same terms as the bottom caption (skipped entirely when no
        # resolver is wired) so an event-scope Cut is byte-for-byte the
        # pre-spec/154 player.
        if self._origin_resolver is not None:
            self._origin_label = QLabel(self)
            self._origin_label.setObjectName("CutPlayOrigin")
            self._origin_label.setAttribute(
                Qt.WidgetAttribute.WA_StyledBackground, True)
            self._origin_label.setWordWrap(False)
            self._origin_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._origin_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.NoTextInteraction)
            self._origin_label.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._origin_label.hide()

        # spec/155 v2 — top-centre caption that overlays the video
        # widget during a sep / opener video slot. Carries the same
        # title + sub the still QImage would have baked in. Hidden the
        # rest of the time. Styled via QSS under ``QLabel#CutPlayCaption``.
        self._caption_label = QLabel(self)
        self._caption_label.setObjectName("CutPlayCaption")
        self._caption_label.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True)
        self._caption_label.setWordWrap(False)
        self._caption_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._caption_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.NoTextInteraction)
        self._caption_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._caption_label.setTextFormat(Qt.TextFormat.RichText)
        self._caption_label.hide()

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
        self._btn_play: Optional[QPushButton] = None
        self._btn_fs: Optional[QPushButton] = None
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
        # spec/152 Phase 2 — cache the most recent valid video frame
        # so ``_capture_outgoing_pixmap`` has something to fade from
        # at swap time. ``QMediaPlayer.videoSink().videoFrame()``
        # returns null / invalid on Windows-WMF backends right after
        # ``stop()``, so we observe ``videoFrameChanged`` directly
        # and stash the latest QImage as it streams in.
        self._latest_video_image: Optional[QImage] = None
        sink = self._player.videoSink()
        if sink is not None:
            sink.videoFrameChanged.connect(self._on_video_frame_observed)

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
        # TEMP DIAGNOSTIC (pause-jump investigation 2026-06-27) — every
        # slide change funnels through here. Log the move + who called
        # it so a pause-then-jump repro shows whether advance() fired
        # from the timer, a video status, or the watchdog. Remove once
        # the cause is pinned.
        import traceback as _tb
        log.warning(
            "CUTPLAY _show_index: %d -> %d  paused=%s  caller=%s",
            self._index, index, self._paused,
            " <- ".join(
                f.name for f in reversed(_tb.extract_stack()[-5:-1])),
        )
        # spec/152 Phase 2 — capture the OUTGOING entry's pixels
        # BEFORE we tear down the timer / player so the crossfade has
        # a real frame to fade from. ``_index >= 0`` skips capture on
        # the very first slide (no outgoing) and on a teardown that
        # ran ``_index = -1`` (rare; defensive).
        outgoing_pm: Optional[QPixmap] = (
            self._capture_outgoing_pixmap()
            if self._index >= 0 else None
        )
        # spec/152 Phase 3 — boundary-aware transition duration. The
        # value is fixed BEFORE we mutate ``self._index``: photo↔photo
        # gets the full transition; photo↔video gets half; video↔video
        # gets zero (the frozen last-frame on the photo widget bridges
        # the codec swap, so no black gap appears). A "0" boundary
        # short-circuits the overlay entirely.
        boundary_ms = (
            self._boundary_transition_ms(self._index, index)
            if self._index >= 0 and 0 <= index < len(self._entries)
            else 0
        )
        # spec/152 Phase 2 — when the outgoing entry is a video,
        # FREEZE its last frame on the photo widget BEFORE we stop
        # the player. Otherwise the QVideoWidget paints opaque black
        # immediately on ``stop()`` and the user sees that black
        # while the next entry's setup runs. The frozen still on the
        # photo widget acts as a guaranteed visual fallback even when
        # the crossfade overlay can't be constructed (e.g. the cache
        # listener never observed a frame on some Qt backend), AND it
        # is what bridges a video→video swap with no overlay
        # (boundary_ms == 0) — the photo widget shows A's last frame
        # until ``_do_video_swap`` reveals B's first frame.
        coming_from_video = (
            self._video_widget is not None
            and self._video_widget.isVisible()
        )
        if (coming_from_video and outgoing_pm is not None
                and not outgoing_pm.isNull()):
            self._raw_pixmap = QPixmap(outgoing_pm)
            self._photo.show()
            self._fit_current()
            self._video_widget.hide()
            self._stack_layout.setCurrentWidget(self._photo)
        # spec/152 Phase 2/3 — show the overlay with the captured frame
        # IMMEDIATELY, before the new entry's paint reaches the user.
        # Without this the new photo / video paints fully (because the
        # underlying ``QStackedLayout`` swap is instant) for a frame
        # or two BEFORE the overlay is raised, which reads as a "pop"
        # to the right size on a photo→photo swap and as a black flash
        # on any video transition. ``boundary_ms <= 0`` short-circuits
        # — the video→video bridge runs on the frozen-last-frame path
        # alone.
        if (outgoing_pm is not None and not outgoing_pm.isNull()
                and boundary_ms > 0):
            self._show_transition_overlay(outgoing_pm, ms=boundary_ms)
            # Force a synchronous paint so the overlay actually covers
            # the canvas before the next-entry setup runs; without this
            # Qt may defer the show() paint to the next event-loop tick
            # and the user briefly sees the new entry's geometry.
            if self._transition_overlay is not None:
                self._transition_overlay.repaint()
        self._timer.stop()
        if self._player is not None:
            self._player.stop()
        if not (0 <= index < len(self._entries)):
            self._finish()
            return
        self._index = index
        kind, payload = self._entries[index]
        # spec/152 Phase 3 — the photo / opener / separator slot hold
        # time is ``photo_ms + transition_ms`` (see :meth:`_entry_total_ms`
        # for the rationale). Computing once here keeps the timer in
        # step with the scrubber's per-entry duration table.
        slot_ms = self._entry_total_ms(self._index)
        if kind == "opener":
            # spec/155 v2 — when the event has an MP4 map, the opener
            # PLAYS the clip; EndOfMedia drives advance just like a
            # file video / sep video. opener_image is still rendered
            # (first-frame still) as the scrubber-hover thumb fallback.
            if self._opener_video_path is not None:
                if outgoing_pm is not None and not outgoing_pm.isNull():
                    self._photo.setPixmap(QPixmap())
                self._ensure_video()
                if self._video_audio is not None:
                    self._video_audio.setMuted(True)
                self._show_video(self._opener_video_path)
            elif self._opener_image is not None:
                self._show_image(self._opener_image)
                if not self._paused:
                    self._timer.start(slot_ms)
            else:
                self.advance()
                return
        elif kind == "sep":
            # spec/155 v2 — when the day has an MP4 map attached, the
            # separator PLAYS the clip (muted, native duration, one
            # play) instead of holding the still QImage. Advance is
            # EndOfMedia-driven, same as a file video; the timer stays
            # idle.
            sep_vid = self._sep_video_path(payload)
            if sep_vid is not None:
                if outgoing_pm is not None and not outgoing_pm.isNull():
                    self._photo.setPixmap(QPixmap())
                self._ensure_video()
                if self._video_audio is not None:
                    self._video_audio.setMuted(True)
                self._show_video(sep_vid)
            else:
                self._show_image(self._separator_image(payload))
                if not self._paused:
                    self._timer.start(slot_ms)
        elif getattr(payload, "kind", "photo") == "video":
            # spec/144 — videos NEVER ride :attr:`_timer`. Advance is
            # event-driven via :data:`QMediaPlayer.MediaStatus.EndOfMedia`
            # in :meth:`_on_video_status`, so the show moves on the
            # *instant* the clip ends — no hold-the-last-frame pause
            # (the old precomputed-timer path could over-run a
            # segment) and no early cut-off (under-running the segment
            # with a stale ``SessionFile.duration_ms``).
            #
            # spec/152 Phase 3 — at a photo→video boundary the photo
            # widget would otherwise keep painting the OUTGOING photo
            # underneath the crossfade overlay (which carries the
            # SAME image), so the fade has no visible target and the
            # user sees an abrupt cut when the video's first frame
            # arrives. Clearing the photo widget makes the overlay's
            # fade reveal the dialog's black canvas — visually
            # "photo fades out, video hard-cuts in" which is the
            # half-transition shape Phase 3 commits to. The video
            # widget takes over on its first frame (see
            # ``_do_video_swap``); the brief moment of dark canvas
            # between overlay-finish and first-frame is typically
            # tens of milliseconds on a warm machine.
            if outgoing_pm is not None and not outgoing_pm.isNull():
                # Only clear when there IS an outgoing fade — for
                # the very first slide there's no overlay so we
                # leave the photo widget alone (no transition).
                self._photo.setPixmap(QPixmap())
            # spec/155 v2 — file videos play with their own audio. A
            # preceding sep video may have muted the audio output;
            # restore it here so the user's actual clip audio isn't
            # silenced by accident.
            self._ensure_video()
            if self._video_audio is not None:
                self._video_audio.setMuted(False)
            self._show_video(self._resolve_payload_path(payload))
        else:
            pm = load_pixmap(self._resolve_payload_path(payload))
            self._show_pixmap(pm)
            if not self._paused:
                self._timer.start(slot_ms)
        # spec/152 Phase 2 — start the fade animation now that the
        # new entry is set up underneath the overlay. The overlay
        # itself was raised earlier (so the new entry doesn't pop);
        # this just kicks off the opacity 1 → 0 tween.
        if (outgoing_pm is not None and not outgoing_pm.isNull()
                and boundary_ms > 0):
            self._start_transition_animation()
        # Refresh the overlay AFTER the frame paint.
        self._update_overlay(kind, payload)
        self._update_origin(kind, payload)
        self._update_caption(kind, payload)
        # Snap the scrubber to the new entry; the ticker keeps it warm.
        if self._scrubber is not None:
            self._scrubber.set_playhead(self._index, 0.0)
        self._update_time_label()
        self._update_play_icon()

    # ── spec/152 Phase 2 — crossfade transition ─────────────────────

    def _entry_class(self, idx: int) -> str:
        """spec/152 Phase 3 — classify an entry as ``'photo'`` or
        ``'video'`` for boundary-transition arithmetic. Opener and
        separator slides read as ``'photo'`` because they hold for the
        photo slot and crossfade like one. Out-of-range indices read
        as ``'photo'`` (the only safe default — never claim a sentinel
        index is a video boundary)."""
        if not (0 <= idx < len(self._entries)):
            return "photo"
        kind, payload = self._entries[idx]
        if kind == "file" and getattr(payload, "kind", "") == "video":
            return "video"
        # spec/155 v2 — a video-map separator behaves like a file video
        # for crossfade math: the transition uses the video boundary
        # variant (half on photo↔video, zero on video↔video).
        if kind == "sep" and self._sep_video_path(payload) is not None:
            return "video"
        # spec/155 v2 — same shape for an MP4 event-map opener.
        if kind == "opener" and self._opener_video_path is not None:
            return "video"
        return "photo"

    def _boundary_transition_ms(
        self, outgoing_idx: int, incoming_idx: int
    ) -> int:
        """spec/152 Phase 3 — boundary-aware transition duration.

        * photo↔photo: full ``transition_ms`` (a true crossfade).
        * photo↔video: ``transition_ms / 2`` (only the photo side is
          rendered; the video hard-cuts in or out).
        * video↔video: ``0`` (a hard cut; the frozen last-frame of A
          held on the photo widget bridges the gap until B's first
          frame arrives — see ``_show_index``'s ``coming_from_video``
          path and ``_do_video_swap`` for the swap mechanics).

        The shorter video-boundary transitions free up wall-clock that
        the global ``video_rate`` (computed once per Cut in
        :func:`core.cut_budget.mira_play_video_speed`) reclaims so the
        rehearsal lands at the same total length as the generated PTE
        show."""
        full_ms = self._transition_ms_value()
        if full_ms <= 0:
            return 0
        out_class = self._entry_class(outgoing_idx)
        in_class = self._entry_class(incoming_idx)
        if out_class == "photo" and in_class == "photo":
            return full_ms
        if out_class == "video" and in_class == "video":
            return 0
        return full_ms // 2

    def _transition_ms_value(self) -> int:
        """spec/152 §3 — the per-Cut transition duration in ms.

        The host passes the resolved value (per-Cut override > global
        default > 2000 fallback) at construction time via
        ``transition_ms=`` so the rehearsal's slot math matches the
        PTE [Times] generator and the budget exactly. The legacy
        parent-walking path is kept as a fallback for stub tests and
        a defensive default (still 2000) for any caller that wires
        neither."""
        if self._explicit_transition_ms is not None:
            return self._explicit_transition_ms
        # Legacy path — walk the parent for ``_settings`` / ``settings``
        # (kept for stub tests that don't pass ``transition_ms``).
        parent = self.parent()
        for attr in ("_settings", "settings"):
            getter = getattr(parent, attr, None)
            if callable(getter):
                try:
                    s = getter()
                except Exception:                                  # noqa: BLE001
                    s = None
                if s is not None:
                    raw = getattr(s, "default_transition_ms", 2000)
                    try:
                        return max(0, int(round(float(raw))))
                    except (TypeError, ValueError):
                        return 2000
        return 2000

    def _on_video_frame_observed(self, frame) -> None:
        """spec/152 Phase 2 — stash the latest valid QImage so the
        crossfade overlay always has a real frame to fade from.
        Cheap: one QImage clone per visible frame, dropped on the
        next observation. Invalid / null frames are ignored."""
        if frame is None:
            return
        try:
            if not frame.isValid():
                return
            img = frame.toImage()
        except Exception:                                          # noqa: BLE001
            return
        if not img.isNull():
            # ``QImage`` is implicitly shared; copy to detach from the
            # sink's buffer so it survives the next frame arrival.
            self._latest_video_image = QImage(img)

    def _capture_outgoing_pixmap(self) -> Optional[QPixmap]:
        """Capture the current entry's displayed pixels as a QPixmap
        so the crossfade overlay can fade them out while the next
        entry sets up.

        Photos / opener / separator: ``QWidget.grab()`` snapshots the
        widget exactly as rendered (matches the new entry's painted
        geometry beat-for-beat, so a photo→photo swap doesn't show a
        size jump between overlay and new entry).

        Video: returns the most recent observed frame, cached by the
        sink listener wired in ``_ensure_video``. ``videoFrame()``
        returns invalid frames on Windows-WMF immediately after
        ``stop()``; the cached path side-steps that race."""
        # Photo / sep / opener path — use the canvas's RENDERED size
        # so the overlay matches the new entry's pixel-perfect
        # geometry. Falls back to the raw pixmap when grab() fails
        # (e.g. the canvas hasn't been painted yet on the first slide).
        if (self._video_widget is None
                or not self._video_widget.isVisible()):
            try:
                grabbed = self._photo.grab()
            except Exception:                                      # noqa: BLE001
                grabbed = QPixmap()
            if isinstance(grabbed, QPixmap) and not grabbed.isNull():
                return grabbed
            raw = getattr(self, "_raw_pixmap", None)
            if isinstance(raw, QPixmap) and not raw.isNull():
                return QPixmap(raw)
            return None
        # Video path — use the cached latest frame from the sink
        # listener. ``videoFrame()`` is unreliable here (returns
        # invalid frames on Windows-WMF after stop()).
        img = self._latest_video_image
        if img is not None and not img.isNull():
            return QPixmap.fromImage(img)
        return None

    def _ensure_transition_overlay(self) -> None:
        """Lazily build the overlay widget. Single-slide Cuts skip
        construction entirely; the QPropertyAnimation only fires
        ``finished`` when a real fade ran."""
        if self._transition_overlay is not None:
            return
        overlay = QLabel(self._stack_widget)
        overlay.setObjectName("CutPlayTransition")
        overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overlay.setScaledContents(False)
        overlay.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # Sit above the photo canvas / video widget on the stack but
        # below the bottom transport bar (which lives outside the
        # stack widget, so the overlay can never cover the controls).
        overlay.hide()
        effect = QGraphicsOpacityEffect(overlay)
        effect.setOpacity(1.0)
        overlay.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity")
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        # spec/152 Phase 3 — Linear easing so the fade duration the
        # user perceives matches the configured transition_ms. With
        # the previous InOutQuad curve the steep mid-section made a
        # 2 s fade feel like ~1.3 s (opacity hit 0.125 by t=0.75 of
        # the duration), and the user read the transition as "not
        # taking the 2 s that are set".
        anim.setEasingCurve(QEasingCurve.Type.Linear)
        anim.finished.connect(self._on_transition_finished)
        self._transition_overlay = overlay
        self._transition_opacity = effect
        self._transition_anim = anim

    def _show_transition_overlay(
        self, outgoing_pm: QPixmap, *, ms: Optional[int] = None,
    ) -> None:
        """spec/152 Phase 2 — install the captured outgoing frame on
        the overlay AT FULL OPACITY and raise it above the stack. The
        animation is NOT started here — that happens later in
        :meth:`_start_transition_animation` once the new entry's
        widgets are set up underneath. This split is what stops a
        photo→photo swap from "popping" (the new photo paints fully
        BEFORE the overlay is in place if the two steps run together)
        and a photo→video swap from flashing black (the video sink
        paints opaque black before the first frame).

        ``ms`` overrides the duration — used by spec/152 Phase 3's
        boundary-aware caller in :meth:`_show_index` to halve the
        transition at photo↔video boundaries. ``ms is None`` falls
        back to the global Settings value (the back-compat shim
        :meth:`_start_transition_fade` calls in this shape and the
        legacy test surface still drives it).

        ``transition_ms <= 0`` (Settings "hard cut" mode, OR the
        Phase 3 video↔video boundary) skips construction entirely —
        no overlay, no fade."""
        if outgoing_pm is None or outgoing_pm.isNull():
            return
        transition_ms = (
            self._transition_ms_value() if ms is None else int(ms)
        )
        if transition_ms <= 0:
            return
        self._ensure_transition_overlay()
        overlay = self._transition_overlay
        if overlay is None:
            return
        sz = self._stack_widget.size()
        # spec/152 Phase 2 — the photo / opener / separator capture is
        # ``self._photo.grab()``, which already returns a pixmap whose
        # LOGICAL size matches the canvas (with the screen's DPR baked
        # in). Calling ``QPixmap.scaled(sz, …)`` here would re-scale
        # against the canvas's LOGICAL size while Qt6 treats that QSize
        # as DEVICE pixels — and neither ``QPixmap.scaled()`` nor a
        # follow-up ``setDevicePixelRatio()`` reliably propagates the
        # source DPR across a scale call. On HiDPI the result rendered
        # at half logical size, which the user saw as the photo
        # "popping smaller" the instant the overlay raised. The fix:
        # use the grab AS-IS when its logical size already matches the
        # canvas (the common photo→photo case), and only fall through
        # to a DPR-aware scale when the source differs — the cached
        # video-frame path, whose QImage lands at the clip's native
        # pixel resolution and HAS to fit the canvas with letterboxing.
        if sz.width() > 0 and sz.height() > 0:
            dpr = outgoing_pm.devicePixelRatio() or 1.0
            logical_w = int(round(outgoing_pm.width() / dpr))
            logical_h = int(round(outgoing_pm.height() / dpr))
            if logical_w == sz.width() and logical_h == sz.height():
                scaled = outgoing_pm
            else:
                target = QSize(
                    max(1, int(round(sz.width() * dpr))),
                    max(1, int(round(sz.height() * dpr))),
                )
                scaled = outgoing_pm.scaled(
                    target, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                scaled.setDevicePixelRatio(dpr)
        else:
            scaled = outgoing_pm
        overlay.setPixmap(scaled)
        overlay.setGeometry(0, 0, sz.width(), sz.height())
        self._transition_opacity.setOpacity(1.0)
        overlay.raise_()
        overlay.show()
        # Stash the duration the matching ``_start_transition_animation``
        # call will read. Keeps the two halves connected without
        # smuggling state through a method arg.
        self._pending_transition_ms = int(transition_ms)

    def _start_transition_animation(self) -> None:
        """spec/152 Phase 2 — start the opacity 1 → 0 tween the
        ``_show_transition_overlay`` call queued up. Called AFTER the
        new entry's widgets are set up underneath the overlay."""
        anim = self._transition_anim
        ms = getattr(self, "_pending_transition_ms", 0)
        if anim is None or ms <= 0:
            return
        anim.stop()
        anim.setDuration(int(ms))
        anim.start()
        self._pending_transition_ms = 0

    # Back-compat shim — tests in tests/test_cut_play_transition.py
    # still drive ``_start_transition_fade``. The new behaviour is
    # show-then-start; the shim collapses them so the existing tests
    # exercise the same surface.
    def _start_transition_fade(self, outgoing_pm: QPixmap) -> None:
        self._show_transition_overlay(outgoing_pm)
        self._start_transition_animation()

    def _on_transition_finished(self) -> None:
        """Animation done — drop the overlay so it stops eating
        repaints and doesn't sit on top of the next entry."""
        if self._transition_overlay is not None:
            self._transition_overlay.hide()
            # Drop the pixmap reference so the next swap captures
            # fresh bytes instead of stale ones.
            self._transition_overlay.clear()

    def _teardown_transition(self) -> None:
        """Stop the animation and hide the overlay. Called from the
        dialog's media teardown path so a half-finished fade can't
        outlive the dialog."""
        anim = self._transition_anim
        if anim is not None:
            try:
                anim.stop()
            except Exception:                                      # noqa: BLE001
                pass
        if self._transition_overlay is not None:
            try:
                self._transition_overlay.hide()
                self._transition_overlay.clear()
            except Exception:                                      # noqa: BLE001
                pass

    # ── spec/155 v2 — video-map separator helpers ──────────────

    def _opener_video_duration_ms(self) -> int:
        """Probed duration of the opener-slot MP4 (event map), cached.
        Returns 0 when no MP4 opener path is set or the probe fails."""
        if self._opener_video_path is None:
            return 0
        if self._opener_video_duration_cache is not None:
            return self._opener_video_duration_cache
        try:
            from core.video_extract import probe_video
            ms = int(probe_video(self._opener_video_path).duration_ms or 0)
        except Exception:                                          # noqa: BLE001
            ms = 0
        self._opener_video_duration_cache = ms
        return ms

    def _sep_video_path(self, day) -> Optional[Path]:
        """Returns the absolute MP4 path when this day's map slot is a
        video and the file exists; ``None`` otherwise (image map or no
        map). The Cut Play branches on this to swap the still-render
        path for video playback."""
        meta = self._day_meta.get(day)
        rel = getattr(meta, "map_image_path", None)
        if not rel:
            return None
        from core.path_builder import is_video_map_path
        if not is_video_map_path(rel):
            return None
        abs_p = self._root / rel
        return abs_p if abs_p.is_file() else None

    def _sep_video_duration_ms(self, day) -> int:
        """Probed duration of the day's video-map separator, cached.
        Returns 0 for non-video / missing slots."""
        if not hasattr(self, "_sep_video_duration_cache"):
            self._sep_video_duration_cache: dict = {}
        if day in self._sep_video_duration_cache:
            return self._sep_video_duration_cache[day]
        path = self._sep_video_path(day)
        if path is None:
            self._sep_video_duration_cache[day] = 0
            return 0
        try:
            from core.video_extract import probe_video
            ms = int(probe_video(path).duration_ms or 0)
        except Exception:                                          # noqa: BLE001
            ms = 0
        self._sep_video_duration_cache[day] = ms
        return ms

    def _separator_image(self, day) -> QImage:
        meta = self._day_meta.get(day)
        map_rel = getattr(meta, "map_image_path", None)
        # spec/155 v2 — when the map slot is .mp4 the live separator
        # plays the clip (the still QImage path doesn't fire here),
        # but ``_separator_image`` is also called by the small-thumb
        # preview in the scrubber hover. Substitute the first-frame
        # sidecar so the hover thumb still has something to show.
        if map_rel:
            from core.path_builder import (
                MAP_VIDEO_THUMB_SUFFIX,
                is_video_map_path,
            )
            if is_video_map_path(map_rel):
                sidecar_rel = map_rel + MAP_VIDEO_THUMB_SUFFIX
                map_rel = sidecar_rel
        map_abs = (self._root / map_rel) if map_rel else None
        return render_separator_image(
            day_number=day,
            date=getattr(meta, "date", None),
            location=getattr(meta, "location", None),
            description=getattr(meta, "description", "") or "",
            aspect=self._aspect,
            height=max(480, self.height() or 1080),
            card_style=self._card_style,
            seed_key=f"{self._seed_prefix}:{day}",
            # spec/154 — cross-event separators carry a SOURCE EVENT title
            # override; event-scope cards leave it None ("Day N").
            title=getattr(meta, "title", None),
            # spec/155 — when this day has an attached map, the renderer
            # switches to the letterboxed-map form.
            map_image_path=map_abs)

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
        # spec/140 §1 — a video→photo transition must cancel any
        # pending first-frame swap (the next clip starts clean).
        self._reset_video_swap_state()
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
            self._missing_label = QLabel(self._stack_widget)
            self._missing_label.setObjectName("CutPlayMissing")
            self._missing_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._stack_layout.addWidget(self._missing_label)
        self._missing_label.setText(tr("(file missing)"))
        self._stack_layout.setCurrentWidget(self._missing_label)

    def _hide_missing_label(self) -> None:
        if self._missing_label is not None:
            self._missing_label.setText("")

    def _fit_current(self) -> None:
        """spec/152 §X — feed ``BlurredPhotoCanvas`` the FULL-resolution
        raw pixmap. The pre-fix path pre-scaled it to the stack widget's
        LOGICAL size with ``QPixmap.scaled(QSize, …)``, which Qt6
        treats as DEVICE pixels (DPR dropped). The canvas then scaled
        that already-downsampled pixmap a second time, so HiDPI users
        saw a blurry approximation of the source while the inline PTE
        slideshow rendered the same bytes at full screen resolution.
        With the raw pixmap reaching the canvas untouched, the DPR-
        aware scale inside ``BlurredPhotoCanvas.paintEvent`` is the
        only resampling step — single pass, full quality, matches PTE.

        Resize handling: the canvas redraws on its own resize event
        and re-scales from ``_pixmap`` each time, so dropping the
        pre-scale here doesn't cost a tier of cached work — the
        canvas's per-paint downscale is the work that used to be
        duplicated."""
        pm = getattr(self, "_raw_pixmap", QPixmap())
        if pm.isNull():
            return
        self._photo.setPixmap(pm)

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self._position_caption()
        self._fit_current()
        self._position_overlay()
        self._position_origin()
        self._hide_hover_preview()
        # spec/152 Phase 2 — keep the crossfade overlay sized to
        # the canvas while a fade is mid-flight; otherwise the user
        # resizing the window during a transition would see the
        # overlay frozen at its old geometry.
        if (self._transition_overlay is not None
                and self._transition_overlay.isVisible()):
            sz = self._stack_widget.size()
            self._transition_overlay.setGeometry(
                0, 0, sz.width(), sz.height())

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
            # spec/154 — the resolver receives the PAYLOAD (not just the
            # relpath) so cross-event Play can key on (event_uuid, relpath);
            # event-scope wraps its lineage resolver at the call site.
            provenance = self._provenance_resolver(payload)
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
        lbl.setText(_OVERLAY_FIELD_SEPARATOR.join(lines))
        lbl.adjustSize()
        self._position_overlay()
        lbl.raise_()
        lbl.show()

    def _position_overlay(self) -> None:
        lbl = self._overlay_label
        if lbl is None or not lbl.isVisible() and not lbl.text():
            return
        margin = 8
        # Anchor to the displayed photo's bottom edge, centred (mirrors the
        # Picker pill) — not the player's bottom-left corner. The photo is
        # KeepAspectRatio-centred inside the BlurredPhotoCanvas, so use its
        # foreground rect mapped into this widget's coordinates; fall back to
        # the stack area (above the transport) when no photo rect is known.
        photo_rect = (
            self._photo.foreground_rect() if self._photo is not None
            else QRect())
        if not photo_rect.isEmpty():
            top_left = self._photo.mapTo(self, photo_rect.topLeft())
            area = QRect(top_left, photo_rect.size())
        else:
            host = self._stack_widget if self._stack_widget is not None else self
            area = QRect(host.mapTo(self, QPoint(0, 0)), host.size())
        lbl.setMaximumWidth(self.width())
        lbl.adjustSize()
        w = min(lbl.width(), self.width())
        cx = area.x() + area.width() // 2
        x = max(0, min(cx - w // 2, self.width() - w))
        y = max(0, area.y() + area.height() - lbl.height() - margin)
        lbl.move(int(x), int(y))

    def _update_origin(self, kind: str, payload) -> None:
        """spec/154 — refresh the top origin label. Shows on file frames
        only (opener / separator cards carry their own title), hidden when
        the resolver yields nothing for this frame."""
        lbl = self._origin_label
        if lbl is None or self._origin_resolver is None:
            return
        if kind in ("opener", "sep") or not getattr(
                payload, "export_relpath", None) and not getattr(
                payload, "origin_relpath", None):
            lbl.hide()
            return
        try:
            text = self._origin_resolver(payload)
        except Exception:                                          # noqa: BLE001
            log.exception("rehearsal origin: origin_resolver raised")
            lbl.hide()
            return
        if not text:
            lbl.hide()
            return
        lbl.setText(text)
        lbl.adjustSize()
        self._position_origin()
        lbl.raise_()
        lbl.show()

    # ── spec/155 v2 — sep / opener video caption overlay ──────────

    @staticmethod
    def _caption_html(title: str, sub: str) -> str:
        """Compose the top-centre caption HTML with inline-styled
        :func:`html.escape`-d title + sub. Inline ``font-size`` /
        ``font-weight`` because Qt rich-text renders via QTextDocument
        and doesn't honour QSS pseudo-class selectors on label
        children."""
        from html import escape
        parts: list[str] = []
        if title:
            parts.append(
                f'<div style="font-weight:700;">'
                f'{escape(title)}</div>')
        if sub:
            parts.append(
                f'<div style="font-size:14px;color:#dddddd;">'
                f'{escape(sub)}</div>')
        return "".join(parts)

    def _compose_sep_caption_html(self, day) -> str:
        """Day-separator caption HTML (title + sub) for the top-centre
        overlay. Mirrors what
        :func:`mira.ui.shared.separator_card.render_separator_image`
        bakes into the still QImage so the look survives the swap to
        video playback."""
        meta = self._day_meta.get(day)
        title_override = getattr(meta, "title", None)
        if title_override:
            title = title_override
        elif isinstance(day, int):
            title = tr("Day {n}").replace("{n}", str(day))
        else:
            title = tr("More moments")
        sub_bits = [
            b for b in (
                getattr(meta, "date", None),
                getattr(meta, "location", None),
                (getattr(meta, "description", "") or "").strip(),
            ) if b]
        sub = " · ".join(str(b) for b in sub_bits)
        return self._caption_html(title, sub)

    def _compose_opener_caption_html(self) -> str:
        """Cut opener caption HTML. ``opener_caption_tag`` is the show
        title; ``opener_caption_lines`` joins on ``·`` for the facts
        row (matches :func:`render_cut_opener_image`'s layout)."""
        if not self._opener_caption_tag and not self._opener_caption_lines:
            return ""
        sub = " · ".join(self._opener_caption_lines)
        return self._caption_html(self._opener_caption_tag or "", sub)

    def _update_caption(self, kind: str, payload) -> None:
        """Refresh the top-centre caption overlay.

        Visible only during a sep / opener video slot — the still
        QImage already bakes the same text on those frames, so showing
        the Qt overlay too would double-paint. Hidden on file frames
        (per-frame overlay/origin labels cover those)."""
        lbl = self._caption_label
        if lbl is None:
            return
        if kind == "sep" and self._sep_video_path(payload) is not None:
            html = self._compose_sep_caption_html(payload)
        elif kind == "opener" and self._opener_video_path is not None:
            html = self._compose_opener_caption_html()
        else:
            lbl.hide()
            return
        if not html:
            lbl.hide()
            return
        lbl.setText(html)
        lbl.adjustSize()
        self._position_caption()
        lbl.raise_()
        lbl.show()

    def _position_caption(self) -> None:
        """Anchor the caption label to the canvas's TOP edge, centred —
        same arithmetic as :meth:`_position_origin` but uses the video
        widget's geometry when the video is the active stack child."""
        lbl = self._caption_label
        if lbl is None:
            return
        margin = 16
        # Prefer the video widget's rect while it's active (the photo
        # widget may be empty mid-swap); fall back to the photo's
        # foreground_rect / the stack widget like the origin label does.
        if (self._video_widget is not None
                and self._video_widget.isVisible()):
            host = self._video_widget
            area = QRect(
                host.mapTo(self, QPoint(0, 0)), host.size())
        else:
            photo_rect = (
                self._photo.foreground_rect() if self._photo is not None
                else QRect())
            if not photo_rect.isEmpty():
                top_left = self._photo.mapTo(self, photo_rect.topLeft())
                area = QRect(top_left, photo_rect.size())
            else:
                host = (
                    self._stack_widget if self._stack_widget is not None
                    else self)
                area = QRect(host.mapTo(self, QPoint(0, 0)), host.size())
        lbl.setMaximumWidth(self.width())
        lbl.adjustSize()
        w = min(lbl.width(), self.width())
        cx = area.x() + area.width() // 2
        x = max(0, min(cx - w // 2, self.width() - w))
        y = max(0, area.y() + margin)
        lbl.move(int(x), int(y))

    def _position_origin(self) -> None:
        """Anchor the origin label to the displayed photo's TOP edge,
        centred — the mirror of :meth:`_position_overlay`'s bottom anchor."""
        lbl = self._origin_label
        if lbl is None or not lbl.isVisible() and not lbl.text():
            return
        margin = 8
        photo_rect = (
            self._photo.foreground_rect() if self._photo is not None
            else QRect())
        if not photo_rect.isEmpty():
            top_left = self._photo.mapTo(self, photo_rect.topLeft())
            area = QRect(top_left, photo_rect.size())
        else:
            host = self._stack_widget if self._stack_widget is not None else self
            area = QRect(host.mapTo(self, QPoint(0, 0)), host.size())
        lbl.setMaximumWidth(self.width())
        lbl.adjustSize()
        w = min(lbl.width(), self.width())
        cx = area.x() + area.width() // 2
        x = max(0, min(cx - w // 2, self.width() - w))
        y = max(0, area.y() + margin)
        lbl.move(int(x), int(y))

    def mouseDoubleClickEvent(self, ev) -> None:  # noqa: N802
        self._toggle_fullscreen()
        ev.accept()

    def _show_video(self, path: Path) -> None:
        """spec/140 §1 — start the player but hold the photo until
        the first valid video frame, then swap. A ``QVideoWidget``
        with no frame paints opaque black, which used to flash on
        every photo→video transition. Mirrors PhotoViewport's
        no-black-frame contract."""
        self._ensure_video()
        # Don't hide _photo / show the video widget here — the swap
        # happens in :meth:`_on_first_video_frame` once the sink
        # reports a valid frame. Belt-and-braces: a watchdog timer
        # forces the swap if no frame arrives (an unreadable clip
        # would otherwise hang on the previous photo).
        self._reset_video_swap_state()
        sink = self._player.videoSink()
        if sink is not None:
            sink.videoFrameChanged.connect(self._on_first_video_frame)
            self._pending_video_sink = sink
        self._video_swap_timer = QTimer(self)
        self._video_swap_timer.setSingleShot(True)
        self._video_swap_timer.timeout.connect(self._force_video_swap)
        self._video_swap_timer.start(_VIDEO_SWAP_TIMEOUT_MS)
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        if not self._paused:
            self._player.play()
        # spec/145 — re-apply the rehearsal rate AFTER setSource (Qt
        # resets the rate to 1.0 on a new source on some backends). The
        # rate must land BEFORE the first frame so the user doesn't see
        # the clip play one frame at 1× then jump.
        self._apply_video_rate()

    def _reset_video_swap_state(self) -> None:
        """Tear down any pending one-shot frame handler / watchdog
        from a previous clip (the swap is per-clip). Also stops the
        spec/150 §2 END-of-clip watchdog — the two are siblings:
        a video → photo (or video → next-video) transition retires
        both per-clip helpers in one move."""
        if self._video_swap_timer is not None:
            try:
                self._video_swap_timer.stop()
            except Exception:                                  # noqa: BLE001
                pass
            self._video_swap_timer = None
        if self._pending_video_sink is not None:
            try:
                self._pending_video_sink.videoFrameChanged.disconnect(
                    self._on_first_video_frame)
            except (TypeError, RuntimeError):
                pass
            self._pending_video_sink = None
        self._stop_video_end_watchdog()

    def _on_first_video_frame(self, frame) -> None:
        """spec/140 §1 — one-shot: on the first VALID frame the
        ``QVideoWidget`` has live pixels, so flip the stack and
        retire the watchdog. Ignores invalid / null frames the sink
        sometimes emits while priming the pipeline."""
        try:
            if frame is None or not frame.isValid():
                return
        except Exception:                                          # noqa: BLE001
            # Some frame objects don't expose isValid (test stubs);
            # treat a non-None frame as good enough to swap.
            if frame is None:
                return
        self._do_video_swap()

    def _force_video_swap(self) -> None:
        """Watchdog: a clip that never produces a frame
        (unreadable / codec mismatch) must NOT hang on the previous
        photo. After ``_VIDEO_SWAP_TIMEOUT_MS`` we swap anyway so
        the user at least sees the eventual EndOfMedia / error and
        the show advances."""
        log.warning(
            "cut play: no first video frame arrived in %d ms — "
            "forcing swap so the show doesn't stall",
            _VIDEO_SWAP_TIMEOUT_MS,
        )
        self._do_video_swap()

    def _do_video_swap(self) -> None:
        """The actual photo→video swap, called from EITHER the
        first-frame handler OR the watchdog. Idempotent."""
        self._reset_video_swap_state()
        if self._video_widget is None:
            return
        self._photo.hide()
        self._video_widget.show()
        self._video_widget.raise_()
        self._stack_layout.setCurrentWidget(self._video_widget)
        # spec/152 Phase 2 — the ``video_widget.raise_()`` above
        # restacks the video on top of the crossfade overlay; if a
        # fade is still mid-flight we'd see the new video pop in at
        # 100 % the moment the first frame arrived. Re-raise the
        # overlay so it stays above until its own animation finishes.
        if (self._transition_overlay is not None
                and self._transition_overlay.isVisible()):
            self._transition_overlay.raise_()

    def _on_video_status(self, status) -> None:
        from PyQt6.QtMultimedia import QMediaPlayer
        # TEMP DIAGNOSTIC (pause-jump investigation 2026-06-27) — log
        # EVERY status the backend emits, with the paused flag, so a
        # pause-triggered spurious EndOfMedia/InvalidMedia shows up.
        log.warning(
            "CUTPLAY _on_video_status: status=%s  index=%d  paused=%s",
            status, self._index, self._paused)
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            # spec/150 §2 — primary advance path. Tear the end
            # watchdog down before advancing so a late timer fire
            # can't double-advance the show.
            self._stop_video_end_watchdog()
            self.advance()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            log.warning("rehearsal: invalid media, skipping")
            self._stop_video_end_watchdog()
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
        # TEMP DIAGNOSTIC (pause-jump investigation 2026-06-27).
        _k = (self._entries[self._index][0]
              if 0 <= self._index < len(self._entries) else "?")
        log.warning(
            "CUTPLAY _toggle_pause: now_paused=%s  index=%d  kind=%s",
            (not self._paused), self._index, _k)
        self._paused = not self._paused
        if self._paused:
            self._timer.stop()
            if self._player is not None:
                self._player.pause()
            if self._music is not None:
                self._music.pause()
            # spec/150 §2 — stop the end-of-clip watchdog while paused
            # (otherwise it would fire after wall-clock duration even
            # though the player hasn't reached the end of the clip).
            # Resume re-arms via ``_apply_video_rate`` with the new
            # remaining time.
            self._stop_video_end_watchdog()
        else:
            kind, payload = self._entries[self._index]
            if kind == "file" and getattr(payload, "kind", "") == "video":
                if self._player is not None:
                    self._player.play()
                # spec/145 — resume the clip at the selected rate
                # (the player drops back to 1.0 across pause/resume
                # on some backends).
                self._apply_video_rate()
            else:
                # spec/152 Phase 3 — resume on the entry's full slot
                # (photo_ms + transition_ms). The resume currently
                # restarts the FULL slot rather than the remainder
                # which is a pre-existing rough edge; preserving that
                # behaviour, just on the new total.
                self._timer.start(self._entry_total_ms(self._index))
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
        # spec/140 §1 — drop the pending first-frame swap so the
        # watchdog can't fire on a half-destroyed widget.
        self._reset_video_swap_state()
        # spec/152 Phase 2 — stop any running crossfade so its
        # ``finished`` callback doesn't try to update an overlay
        # that's about to be torn down with the dialog.
        self._teardown_transition()
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
        """Bottom-anchored Stop · ⏮ Sep · Play/Pause · Sep ⏭ · timeline ·
        time read-out · 'Per slide' spinbox · video-speed select ·
        fullscreen toggle.

        Uses the same standard-widget vocabulary as the Edit surface's
        ``VideoWorkshopBar`` (spec/56): :func:`ghost_button` for the
        chrome buttons, :func:`transport_button` for Play/Pause,
        :func:`select` for the speed combo, plain ``QLabel`` /
        ``QDoubleSpinBox`` for the rest. No inline styles — the bar
        follows the active theme (light or dark) via QSS roles."""
        bar = QFrame(self)
        bar.setObjectName("CutPlayTransport")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(6)

        btn_stop = ghost_button(tr("Stop"))
        btn_stop.setToolTip(tr("End rehearsal"))
        btn_stop.clicked.connect(self._finish)

        btn_prev_sep = ghost_button("⏮")
        btn_prev_sep.setToolTip(tr("Previous day separator"))
        btn_prev_sep.clicked.connect(lambda: self._jump_to_separator(-1))

        self._btn_play = transport_button(tr("Play / pause"))
        self._btn_play.clicked.connect(self._toggle_pause)

        btn_next_sep = ghost_button("⏭")
        btn_next_sep.setToolTip(tr("Next day separator"))
        btn_next_sep.clicked.connect(lambda: self._jump_to_separator(1))

        scrub = _Scrubber(bar)
        scrub.seeked.connect(self._on_scrubber_seeked)
        scrub.hovered.connect(self._on_scrubber_hovered)
        scrub.hover_left.connect(self._hide_hover_preview)
        scrub.set_entries(self._durations, self._sep_indexes)
        self._scrubber = scrub

        self._time_label = QLabel(_fmt_time(0) + " / " +
                                  _fmt_time(self._total_ms()), bar)
        self._time_label.setObjectName("Sub")
        self._time_label.setMinimumWidth(96)
        self._time_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        slide_lbl = QLabel(tr("Per slide:"), bar)
        slide_lbl.setObjectName("Sub")
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

        # spec/145 — rehearsal-only video-speed override. The select sits
        # next to the per-photo spinner because it's the sibling concept
        # (both tune rehearsal pacing live). On change we call
        # ``QMediaPlayer.setPlaybackRate(r)`` — multiplicative with the
        # baked clip speed (a 2×-baked clip at 1.5× plays 3×). The
        # exported bytes and PTE output are unchanged; this is a
        # browse-the-rehearsal knob.
        rate_options = [
            (0.5, "0.5×"), (0.75, "0.75×"), (1.0, "1×"),
            (1.25, "1.25×"), (1.5, "1.5×"), (2.0, "2×"),
        ]
        self._video_rate_combo = select([label for _, label in rate_options])
        self._video_rate_combo.setObjectName("CutPlayVideoRateCombo")
        self._video_rate_combo.setToolTip(tr(
            "Video playback speed for the rehearsal (compounds with the "
            "clip's baked speed). Doesn't change the exported clips or "
            "the generated PTE."))
        # ``select`` doesn't take per-item userData; attach the rate via
        # ``setItemData`` so the existing ``_on_video_rate_changed``
        # currentData() read still works.
        for i, (rate, _label) in enumerate(rate_options):
            self._video_rate_combo.setItemData(i, float(rate))
        for i, (rate, _label) in enumerate(rate_options):
            if abs(rate - float(self._video_rate)) < 1e-6:
                self._video_rate_combo.setCurrentIndex(i)
                break
        self._video_rate_combo.currentIndexChanged.connect(
            self._on_video_rate_changed)
        video_lbl = QLabel(tr("Video:"), bar)
        video_lbl.setObjectName("Sub")

        self._btn_fs = ghost_button("⛶")
        self._btn_fs.setToolTip(tr("Toggle fullscreen"))
        self._btn_fs.clicked.connect(self._toggle_fullscreen)

        lay.addWidget(btn_stop)
        lay.addWidget(btn_prev_sep)
        lay.addWidget(self._btn_play)
        lay.addWidget(btn_next_sep)
        lay.addWidget(scrub, 1)
        lay.addWidget(self._time_label)
        lay.addSpacing(8)
        lay.addWidget(slide_lbl)
        lay.addWidget(self._slide_spin)
        lay.addSpacing(8)
        lay.addWidget(video_lbl)
        lay.addWidget(self._video_rate_combo)
        lay.addWidget(self._btn_fs)

        bar.setSizePolicy(QSizePolicy.Policy.Preferred,
                          QSizePolicy.Policy.Fixed)
        bar.adjustSize()
        self._transport = bar

        # Hover preview — frameless top-level child so it can pop above
        # everything without clipping inside the bar. Styling lives in
        # QSS under ``QLabel#CutPlayHoverPreview``.
        prev = QLabel(self)
        prev.setObjectName("CutPlayHoverPreview")
        prev.setWindowFlags(Qt.WindowType.ToolTip)
        prev.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
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

    def _on_video_rate_changed(self, _index: int) -> None:
        """spec/145 — apply the rehearsal speed live. The current clip
        (if any) jumps to the new rate immediately; the next
        :meth:`_show_video` call re-arms the rate so following clips
        play at it too. Pairs with spec/144's EndOfMedia advance:
        a higher rate ends the clip sooner and the show moves on
        without any timing desync."""
        if self._video_rate_combo is None:
            return
        data = self._video_rate_combo.currentData()
        try:
            rate = float(data)
        except (TypeError, ValueError):
            return
        if rate <= 0:
            return
        self._video_rate = rate
        self._apply_video_rate()

    def _apply_video_rate(self) -> None:
        """Push the current ``_video_rate`` to the QMediaPlayer. Safe
        to call when the player isn't constructed yet (a photos-only
        Cut hasn't lazy-initialised it) — it's a no-op then. Also
        catches the stub-player case from the test suite where the
        ``setPlaybackRate`` slot exists as a duck method."""
        if self._player is None:
            return
        try:
            self._player.setPlaybackRate(float(self._video_rate))
        except Exception:                                              # noqa: BLE001
            log.exception(
                "setPlaybackRate(%s) failed on the cut player",
                self._video_rate)
        # spec/150 §2 — (re-)arm the end-of-clip watchdog. This is
        # called from ``_show_video`` (fresh clip — position is 0,
        # interval = duration / rate + slack), ``_on_video_rate_changed``
        # (live rate change — interval reflects remaining time at the
        # new rate), and ``_toggle_pause`` on resume (interval reflects
        # remaining time from the paused position). All three paths
        # converge here so the watchdog never gets out of step with
        # the rate.
        self._arm_video_end_watchdog()

    def _arm_video_end_watchdog(self) -> None:
        """spec/150 §2 — start a single-shot timer that calls
        :meth:`advance` if ``EndOfMedia`` hasn't fired by
        ``(duration_ms − position) / rate + slack``. Skipped when:
          * the show is paused (watchdog re-arms on resume);
          * the current entry isn't a video file;
          * the entry's ``duration_ms`` is 0/unknown — we rely on
            EndOfMedia alone in that case rather than guessing.
        Replaces any prior arming first (idempotent)."""
        self._stop_video_end_watchdog()
        if self._paused:
            return
        if not (0 <= self._index < len(self._entries)):
            return
        kind, payload = self._entries[self._index]
        if kind != "file" or getattr(payload, "kind", "") != "video":
            return
        duration_ms = int(getattr(payload, "duration_ms", 0) or 0)
        if duration_ms <= 0:
            return
        pos = 0
        if self._player is not None:
            try:
                pos = int(self._player.position())
            except Exception:                                          # noqa: BLE001
                pos = 0
        remaining = max(0, duration_ms - pos)
        rate = self._video_rate if self._video_rate > 0 else 1.0
        interval_ms = int(remaining / rate) + _VIDEO_END_SLACK_MS
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._on_video_end_watchdog)
        timer.start(interval_ms)
        self._video_end_timer = timer
        self._video_end_armed_for_index = self._index

    def _stop_video_end_watchdog(self) -> None:
        """Tear down the end-of-clip watchdog. Idempotent."""
        if self._video_end_timer is not None:
            try:
                self._video_end_timer.stop()
            except Exception:                                          # noqa: BLE001
                pass
            self._video_end_timer = None
        self._video_end_armed_for_index = -1

    def _on_video_end_watchdog(self) -> None:
        """spec/150 §2 — fires only when EndOfMedia is late or absent.
        Idempotent against EndOfMedia arriving first: if the index has
        moved off the entry we armed for, EndOfMedia (or a manual
        step) already advanced — do nothing."""
        armed_for = self._video_end_armed_for_index
        self._video_end_timer = None
        self._video_end_armed_for_index = -1
        if self._index != armed_for:
            return
        log.debug(
            "cut play: end-of-clip watchdog fired before EndOfMedia "
            "for entry %d — advancing",
            self._index)
        self.advance()

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
        """Flip the transport play/pause glyph. The button is now the
        canonical :func:`transport_button` (SVG line-icon, theme-tinted),
        so :func:`set_transport_playing` drives the swap — no inline
        Unicode glyph dance."""
        b = self._btn_play
        if b is None:
            return
        set_transport_playing(b, not self._paused)

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
        # spec/152 Phase 3 — the slot's full hold is ``photo_ms +
        # transition_ms`` (matches the timer the slot was armed with
        # in :meth:`_show_index`). Reading ``self._photo_ms`` here
        # under-reported elapsed time by transition_ms, which back
        # into the scrubber as a playhead that moved faster than the
        # show actually was.
        total = self._entry_total_ms(self._index)
        if self._paused or not self._timer.isActive():
            return 0
        remaining = int(self._timer.remainingTime())
        if remaining < 0:
            return 0
        return max(0, total - remaining)

    def _entry_total_ms(self, index: int) -> int:
        """spec/152 Phase 3 — wall-clock the entry occupies in the
        rehearsal. PHOTO / opener / separator slots ALWAYS carry
        ``photo_ms + transition_ms`` (matching PTE's ``[Times]``
        cumulative, which adds ``transition_ms`` to every non-video
        slide regardless of neighbor). Videos hold for ``clip_ms``
        only (spec/150 §1).

        The boundary-aware crossfade — full at photo↔photo, half at
        photo↔video, zero at video↔video — affects the VISIBLE fade
        duration only; it fits inside this fixed photo slot so the
        Cut's total wall-clock equals the audio playlist / budget /
        PTE total without needing a global video speed adjustment."""
        if not (0 <= index < len(self._entries)):
            return self._photo_ms + self._transition_ms_value()
        kind, payload = self._entries[index]
        if kind == "file" and getattr(payload, "kind", "") == "video":
            d = int(getattr(payload, "duration_ms", 0) or 0)
            return d if d > 0 else (
                self._photo_ms + self._transition_ms_value())
        # spec/155 v2 — video-map separators hold for the MP4's native
        # duration (probed once and cached). Falls back to the photo
        # slot's hold when the probe fails (corrupt file).
        if kind == "sep":
            sep_d = self._sep_video_duration_ms(payload)
            if sep_d > 0:
                return sep_d
        # spec/155 v2 — same shape for an MP4 event-map opener.
        if kind == "opener":
            op_d = self._opener_video_duration_ms()
            if op_d > 0:
                return op_d
            if self._opener_image is None:
                return 0
        return self._photo_ms + self._transition_ms_value()

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
            _meta = self._day_meta.get(payload)
            _rel = getattr(_meta, "map_image_path", None)
            _abs = (self._root / _rel) if _rel else None
            pm = QPixmap.fromImage(
                render_separator_image(
                    day_number=payload,
                    date=getattr(_meta, "date", None),
                    location=getattr(_meta, "location", None),
                    description=getattr(_meta, "description", "") or "",
                    aspect=self._aspect, height=target_h,
                    card_style=self._card_style,
                    seed_key=f"{self._seed_prefix}:{payload}",
                    map_image_path=_abs))
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
