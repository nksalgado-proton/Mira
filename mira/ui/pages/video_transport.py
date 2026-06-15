"""Video transport strip — the few buttons that appear on the Picker's
``compact_row`` when the current item is a video (spec/70 §2 row 11
folded into surface 07, 2026-06-15).

Salvaged from the now-retired ``video_picker_page.py``. The widget is
pure presentation: it emits ``play_pause_requested`` /
``seek_requested(ms)`` and renders position / duration / playing state
pushed in by the host. The host (PickerPage) wires those signals to the
embedded :class:`PhotoViewport`'s video API (``video_toggle_play`` /
``video_seek``) and listens to ``video_position_changed`` /
``video_duration_changed`` / ``video_playing_changed``.

The ``◀|`` / ``|▶`` buttons jump to start / end (Nelson 2026-06-15 Fix
B — the frame-step path retired; both buttons reuse the existing seek
wiring). Every icon comes from the SVG line-icon family
(:mod:`mira.ui.design.icons`) tinted to the active theme's palette
token via :func:`tinted_svg_pixmap` and re-tinted on
``QEvent.PaletteChange`` — the "white emoji disappears on light" bug
is dead (Nelson 2026-06-15 line-icon sweep).

Visual treatment lives in the theme QSS (spec/05 §5.1):
``#VideoTransport`` (the strip card), ``#VideoScrubber``, ``#VideoTime``,
``#VideoVolume``, ``#VideoMuteToggle``, ``#VideoSpeed``. Both light +
dark themes carry the rules; no inline ``setStyleSheet`` in this widget.
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStyle,
    QStyleOptionSlider,
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
from mira.ui.i18n import tr
from mira.ui.palette import PALETTE


#: Render size for the side-button glyphs (◀| / |▶ / mute) — matches
#: the line-icon family's 16 px reference for inline icons. The mute
#: chip is slightly larger (18 px) so it stays legible inside its
#: 34×34 button face.
_SIDE_ICON_PX = 16
_MUTE_ICON_PX = 18


def _theme_mode() -> str:
    app = QApplication.instance()
    return (app.property("theme") if app else None) or "dark"


def _ink(mode: str) -> QColor:
    """Active-state tint for transport glyphs — full ink token."""
    return QColor(PALETTE[mode]["ink"])


def _ink_soft(mode: str) -> QColor:
    """Muted / inactive-state tint — the palette's ``ink_soft`` token,
    the same the search field's magnifier uses."""
    return QColor(PALETTE[mode]["ink_soft"])


def _fmt_time(ms: int) -> str:
    ms = max(0, int(ms))
    m, rem = divmod(ms, 60_000)
    s, msec = divmod(rem, 1000)
    return f"{m}:{s:02d}.{msec:03d}"


class _Scrubber(QSlider):
    """Position scrubber. Visual treatment lives under ``#VideoScrubber``
    in both themes (spec/05 §5.1). Left-clicking anywhere on the groove
    jumps the playhead to that point (the default QSlider behaviour is
    page-step toward the click — useless for a media scrubber)."""

    def __init__(self) -> None:
        super().__init__(Qt.Orientation.Horizontal)
        self.setObjectName("VideoScrubber")
        self.setRange(0, 1000)
        self.setFixedHeight(20)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            groove = self.style().subControlRect(
                QStyle.ComplexControl.CC_Slider, opt,
                QStyle.SubControl.SC_SliderGroove, self)
            handle = self.style().subControlRect(
                QStyle.ComplexControl.CC_Slider, opt,
                QStyle.SubControl.SC_SliderHandle, self)
            x = int(event.position().x()) - handle.width() // 2
            span = groove.right() - groove.left() - handle.width()
            if span <= 0:
                super().mousePressEvent(event)
                return
            frac = max(0.0, min(1.0, (x - groove.left()) / span))
            value = int(round(self.minimum()
                              + frac * (self.maximum() - self.minimum())))
            self.setValue(value)
            # Emit sliderMoved so hosts treating the scrubber as a drag
            # see the jump; emit sliderReleased so the seek_requested
            # finalises immediately (the host wires it that way).
            self.sliderMoved.emit(value)
            self.sliderReleased.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class VideoTransportBar(QWidget):
    """Jump-to-start · play/pause · jump-to-end · time · scrubber ·
    mute · volume · speed.

    Roles owned by the theme QSS: ``#VideoTransport`` (strip card),
    ``#VideoScrubber``, ``#VideoTime``, ``#VideoVolume``,
    ``#VideoMuteToggle``, ``#VideoSpeed``.
    """

    play_pause_requested = pyqtSignal()
    seek_requested = pyqtSignal(int)            # ms (start/end jumps + scrubber)
    volume_changed = pyqtSignal(int)
    speed_changed = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoTransport")
        # WA_StyledBackground so the QSS card background paints on a
        # plain QWidget host.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(64)
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 10, 18, 10)
        h.setSpacing(12)

        # ◀| — jump to the first frame. Icon-only via tinted SVG (no
        # text glyph). Reuses ``seek_requested`` so the host's existing
        # wiring to ``viewport.video_seek`` covers it.
        self.prev_frame = ghost_button("")
        self.prev_frame.setFixedSize(36, 36)
        self.prev_frame.setIconSize(QSize(_SIDE_ICON_PX, _SIDE_ICON_PX))
        self.prev_frame.setToolTip(tr("Jump to start"))
        self.prev_frame.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.prev_frame.clicked.connect(self._on_jump_to_start)
        h.addWidget(self.prev_frame)

        self.play_btn = transport_button(
            tr("Play / pause the video  (Tab)"))
        self.play_btn.setFixedHeight(36)
        self.play_btn.clicked.connect(self.play_pause_requested.emit)
        h.addWidget(self.play_btn)

        # |▶ — jump to the last frame. Guarded against unknown
        # duration in :meth:`_on_jump_to_end` (the player hasn't
        # reported it yet); the click is a deliberate no-op then.
        self.next_frame = ghost_button("")
        self.next_frame.setFixedSize(36, 36)
        self.next_frame.setIconSize(QSize(_SIDE_ICON_PX, _SIDE_ICON_PX))
        self.next_frame.setToolTip(tr("Jump to end"))
        self.next_frame.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.next_frame.clicked.connect(self._on_jump_to_end)
        h.addWidget(self.next_frame)

        self.time_label = QLabel("0:00.000 / 0:00.000")
        self.time_label.setObjectName("VideoTime")
        self.time_label.setMinimumWidth(170)
        h.addWidget(self.time_label)

        self.scrubber = _Scrubber()
        self.scrubber.sliderMoved.connect(self._on_scrubber_moved)
        self.scrubber.sliderReleased.connect(self._on_scrubber_released)
        h.addWidget(self.scrubber, 1)

        # Mute toggle — a real QPushButton (not a label). Click flips
        # between 0 and the last non-zero volume; the slider tracks the
        # state so manually dragging to 0 reads as muted too. Icon
        # swaps between GLYPH_VOLUME / GLYPH_VOLUME_MUTED via the line-
        # icon family (no Segoe UI Emoji); colour follows the palette.
        self.mute_btn = QPushButton()
        self.mute_btn.setObjectName("VideoMuteToggle")
        self.mute_btn.setFixedSize(34, 34)
        self.mute_btn.setIconSize(QSize(_MUTE_ICON_PX, _MUTE_ICON_PX))
        self.mute_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mute_btn.setToolTip(tr("Mute / unmute"))
        self.mute_btn.clicked.connect(self._on_mute_clicked)
        h.addWidget(self.mute_btn)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setObjectName("VideoVolume")
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(90)
        self.volume.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.volume.setCursor(Qt.CursorShape.PointingHandCursor)
        self.volume.valueChanged.connect(self._on_volume_changed)
        h.addWidget(self.volume)
        # Remember the last non-zero volume so the mute toggle has
        # somewhere to restore to. Seeded from the slider's initial
        # value (80).
        self._last_volume = self.volume.value()

        self.speed = select(["0.25×", "0.5×", "1×", "1.5×", "2×"])
        self.speed.setObjectName("VideoSpeed")
        self.speed.setCurrentText("1×")
        self.speed.setFixedWidth(86)
        self.speed.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.speed.currentTextChanged.connect(self.speed_changed.emit)
        h.addWidget(self.speed)

        self._duration_ms = 0
        self._scrubbing = False

        # Initial render of every glyph using the active theme — the
        # changeEvent hook below re-tints on later theme swaps.
        self._refresh_icons()

    # ── public state pushes ───────────────────────────────────────────

    def set_position(self, pos_ms: int, duration_ms: int) -> None:
        self._duration_ms = max(0, int(duration_ms))
        self.time_label.setText(
            f"{_fmt_time(pos_ms)} / {_fmt_time(duration_ms)}")
        if self._scrubbing:
            return
        if duration_ms > 0:
            v = int(round(min(1.0, max(0.0, pos_ms / duration_ms)) * 1000))
        else:
            v = 0
        self.scrubber.blockSignals(True)
        self.scrubber.setValue(v)
        self.scrubber.blockSignals(False)

    def set_playing(self, playing: bool) -> None:
        set_transport_playing(self.play_btn, playing)

    def show_error(self, msg: str) -> None:
        """Graceful QMediaPlayer failure — print the message in the time
        readout slot so Pick/Skip stays usable while the user knows the
        clip can't play."""
        self.time_label.setText(
            tr("Cannot play this video ({e}) — Pick/Skip still works.")
            .replace("{e}", str(msg))
        )

    # ── scrubber + jump handlers ──────────────────────────────────────

    def _on_scrubber_moved(self, _v: int) -> None:
        self._scrubbing = True

    def _on_scrubber_released(self) -> None:
        v = self.scrubber.value()
        self._scrubbing = False
        if self._duration_ms > 0:
            ms = int(round((v / 1000.0) * self._duration_ms))
            self.seek_requested.emit(ms)

    def _on_jump_to_start(self) -> None:
        self.seek_requested.emit(0)

    def _on_jump_to_end(self) -> None:
        # No-op when duration is unknown (player hasn't reported it
        # yet) — emitting 0 again would be misleading; the cleaner
        # behaviour is to wait until duration arrives.
        if self._duration_ms > 0:
            self.seek_requested.emit(self._duration_ms)

    # ── volume / mute ─────────────────────────────────────────────────

    def _on_volume_changed(self, value: int) -> None:
        """Forward the slider's value AND keep the mute icon honest.
        Remembers the last non-zero value so the mute toggle has
        somewhere to restore to."""
        if value > 0:
            self._last_volume = int(value)
        self._refresh_mute_icon()
        self.volume_changed.emit(value)

    def _on_mute_clicked(self) -> None:
        """Toggle between 0 and the last non-zero volume. ``_last_volume``
        defaults to 80 so a fresh page that's never been touched still
        has somewhere to restore to."""
        if self.volume.value() > 0:
            self._last_volume = self.volume.value()
            self.volume.setValue(0)
        else:
            self.volume.setValue(
                self._last_volume if self._last_volume > 0 else 80)

    # ── theme-aware icon refresh ──────────────────────────────────────

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            # Re-tint every line-icon glyph for the new theme — the
            # cached pixmap is keyed on (path, size, colour) so a
            # theme swap keys to a new entry; calling _refresh_icons
            # just re-fetches with the new palette token.
            self._refresh_icons()

    def _refresh_icons(self) -> None:
        mode = _theme_mode()
        ink = _ink(mode)
        # Jump-to-start / jump-to-end — full ink. They're active
        # controls; the disabled state's QSS handles dimming.
        self.prev_frame.setIcon(QIcon(
            tinted_svg_pixmap(GLYPH_TO_START, _SIDE_ICON_PX, ink)))
        self.next_frame.setIcon(QIcon(
            tinted_svg_pixmap(GLYPH_TO_END, _SIDE_ICON_PX, ink)))
        # Play / pause + mute follow their own state-aware refreshers.
        if hasattr(self.play_btn, "_refresh_icon"):
            self.play_btn._refresh_icon()              # noqa: SLF001
        self._refresh_mute_icon()

    def _refresh_mute_icon(self) -> None:
        muted = self.volume.value() == 0
        glyph = GLYPH_VOLUME_MUTED if muted else GLYPH_VOLUME
        mode = _theme_mode()
        # ink_soft when off (the magnifier-style "neutral inactive"
        # tone), full ink when on so it reads from across the strip.
        colour = _ink_soft(mode) if muted else _ink(mode)
        self.mute_btn.setIcon(QIcon(
            tinted_svg_pixmap(glyph, _MUTE_ICON_PX, colour)))
        # Expose the muted state to the theme QSS via a dynamic property
        # so the rule can paint a different border accent if it wants.
        self.mute_btn.setProperty("muted", muted)
        self.mute_btn.style().unpolish(self.mute_btn)
        self.mute_btn.style().polish(self.mute_btn)
