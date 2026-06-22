"""Surface — Quick Sweep viewer (redesign).

spec/70 Phase 3 — the Quick Sweep single-photo viewer. The day-grid level
above lives in :class:`~mira.ui.pages.days_grid_page.DaysGridPage` (the
SAME redesigned widget the Picker uses); a click on a Days Grid cell
opens this viewer with all items in capture-time order. No nav level,
no cluster sub-grid, no compare are owned here — DaysGridPage handles
those for the gateway path. Standalone (paths) mode flattens to a flat
GridItem list per spec/70's "speed-first / stripped chrome" pull.

Behaviour:
* Default state for an untouched item is configurable via Settings →
  ``quick_sweep_default_state`` (defaults to Keep — the permissive
  Quick Sweep contract).
* Keep / Discard / Compare verbs apply to the current item; the state
  border + the action cluster track it. Compare counts as Keep at save
  time (the user re-decides them in the main Picker).
* Save fires :sig:`saved(set[Path])` with Keep + Compare. Cancel /
  Back without saving fires :sig:`cancelled`.
* Browse mode (``browse_mode=True``) — read-only viewer used by the
  plan-editor / manage-days dialogs. Hides every K/D control.

Signals:
* ``saved(set[Path])`` — kept set on Save.
* ``cancelled()`` — user backed out / Esc without saving.
* ``closed()`` — page-stack outermost back.
* ``fullscreen_changed(bool)`` — shell hides/restores chrome.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Sequence

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor, QKeyEvent
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.cull_state import (
    STATE_CANDIDATE,
    STATE_DISCARDED as STATE_SKIPPED,
    STATE_KEPT as STATE_PICKED,
)
from core.fresh_source import SourceItem
from core.video_discovery import VIDEO_EXTENSIONS
from mira.picked.exif_compare import (
    caption_html,
    file_size_text,
    file_type_label,
    source_chip_html,
)
from mira.settings.repo import SettingsRepo
from mira.ui.base.surface import (
    BasePickSurface,
    set_transport_playing,
    transport_button,
)
from mira.ui.design import (
    SurfaceIdentityHeader,
    danger_ghost_button,
    ghost_button,
    nav_arrow,
    primary_button,
)
from mira.ui.i18n import tr
from mira.ui.media.photo_overlay import PhotoExposureOverlay
from mira.ui.media.photo_viewport import PhotoViewport, ViewportItem

log = logging.getLogger(__name__)


# State cycle: K → D → C → K. Default is Keep (Quick Sweep convention).
_STATE_CYCLE = (STATE_PICKED, STATE_SKIPPED, STATE_CANDIDATE)


# ── StandaloneCullSetupDialog (folder picker) ─────────────────────────


class StandaloneCullSetupDialog(QDialog):
    """Lean source + destination picker for the standalone Quick Sweep.

    Owned here so the standalone entry on the menu can call it before
    pushing :class:`DaysGridPage` (paths mode) onto the stack."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Quick Sweep — source & destination"))
        self.setModal(True)
        self._source = ""
        self._dest = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)
        intro = QLabel(tr(
            "Choose a folder of photos/videos to sweep, and where to "
            "copy the ones you keep."))
        intro.setWordWrap(True)
        outer.addWidget(intro)

        self._source_edit = self._add_row(
            outer, tr("Source folder:"), self._browse_source)
        self._dest_edit = self._add_row(
            outer, tr("Copy keepers to:"), self._browse_dest)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = ghost_button(tr("Cancel"))
        cancel.clicked.connect(self.reject)
        self._ok = primary_button(tr("Start sweep"))
        self._ok.setDefault(True)
        self._ok.setEnabled(False)
        self._ok.clicked.connect(self._accept)
        for b in (cancel, self._ok):
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        row.addWidget(cancel)
        row.addWidget(self._ok)
        outer.addLayout(row)

    def _add_row(
        self, outer: QVBoxLayout, label: str, on_browse,
    ) -> QLineEdit:
        row = QHBoxLayout()
        lab = QLabel(label)
        lab.setMinimumWidth(110)
        row.addWidget(lab)
        edit = QLineEdit()
        edit.setReadOnly(True)
        edit.setMinimumWidth(360)
        row.addWidget(edit, stretch=1)
        btn = ghost_button(tr("Browse…"))
        btn.clicked.connect(on_browse)
        row.addWidget(btn)
        outer.addLayout(row)
        return edit

    def _browse_source(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, tr("Select the source folder"))
        if d:
            self._source = d
            self._source_edit.setText(d)
            self._sync_ok()

    def _browse_dest(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, tr("Select the destination folder"))
        if d:
            self._dest = d
            self._dest_edit.setText(d)
            self._sync_ok()

    def _sync_ok(self) -> None:
        self._ok.setEnabled(
            bool(self._source) and bool(self._dest)
            and self._source != self._dest
        )

    def _accept(self) -> None:
        if not self._source or not self._dest:
            return
        if self._source == self._dest:
            QMessageBox.warning(
                self, tr("Quick Sweep"),
                tr("Source and destination must be different folders."))
            return
        self.accept()

    def source_path(self) -> Path:
        return Path(self._source)

    def dest_path(self) -> Path:
        return Path(self._dest)


# ── QuickSweepPage (viewer only) ───────────────────────────────────────


class QuickSweepPage(QWidget):
    """The Quick Sweep single-photo viewer — redesigned on
    :class:`BasePickSurface` + :class:`PhotoViewport`.

    Call :meth:`load` with a sequence of :class:`SourceItem` (and an
    optional ``start_index``) before showing. The viewer walks them in
    capture-time order; ←/→ navigate, P/X/Space/C apply the locked
    spec/63 §4 verbs.

    For host-driven flows where the day-grid level lives elsewhere
    (DaysGridPage in main_window's QS routes), pass the SourceItem list
    that the user clicked into (single item, cluster members, or all
    items if the host shows the full day flat).
    """

    saved = pyqtSignal(set)            # set[Path] of Keep + Compare
    cancelled = pyqtSignal()
    closed = pyqtSignal()
    fullscreen_changed = pyqtSignal(bool)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        browse_mode: bool = False,
    ) -> None:
        super().__init__(parent)
        self._browse_mode = browse_mode

        # Flat sequence of items the viewer walks.
        self._items: List[SourceItem] = []
        # Per-path K/D state. Missing key == ``_legacy_default``.
        self._state: dict[Path, str] = {}
        self._index = 0
        self._fullscreen = False

        # Configurable default state.
        self._legacy_default: str = STATE_PICKED

        # Video transport bookkeeping.
        self._video_duration_ms = 0

        self._build_ui()
        if self._browse_mode:
            self._enter_browse_mode()
        # Direct focus on the viewport (it owns the locked grammar).
        self.setFocusProxy(self._viewport)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _enter_browse_mode(self) -> None:
        """Hide every K/D control — read-only photo/video browser. The
        redesigned action cluster (Pick / Skip / Compare buttons) +
        Save button vanish; navigation, fullscreen, F10, exposure
        overlay, and the viewport itself stay live."""
        self._surface.set_region_visible("state_bar", False)
        self._export_btn.setVisible(False)
        self._pick_btn.setVisible(False)
        self._skip_btn.setVisible(False)
        self._compare_btn.setVisible(False)

    # ── UI assembly ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # spec/71 identity header — Collect phase chrome (blue rail +
        # QUICK SWEEP badge). Quick Sweep's permissive contract drives
        # the legend: green = Keeping (default), red = Skipped, yellow
        # = Mixed cluster.
        self._identity = SurfaceIdentityHeader(
            phase="collect",
            name=tr("Quick Sweep"),
            purpose=tr("Fast pass — skip the obvious rejects"),
            legend=[
                ("picked", tr("Keeping")),
                ("skipped", tr("Skipped")),
                ("mixed", tr("Mixed")),
            ],
            reminder=tr(
                "Everything starts kept — press X to skip the rejects."),
        )
        identity_host = QWidget()
        ihl = QVBoxLayout(identity_host)
        ihl.setContentsMargins(24, 14, 24, 6)
        ihl.setSpacing(0)
        ihl.addWidget(self._identity)
        outer.addWidget(identity_host)

        self._surface = self._build_viewer()
        outer.addWidget(self._surface)

    def _build_viewer(self) -> BasePickSurface:
        """The single-item viewer on the BasePickSurface scaffold +
        embedded PhotoViewport. Redesign-catalog buttons throughout."""
        surface = BasePickSurface()
        surface.setObjectName("QuickSweepViewer")

        # ── TOP_BAR — ‹ Back · position · info · stretch · Save kept · ?
        self._back_btn = ghost_button(tr("‹ Back"))
        self._back_btn.setToolTip(tr("Back  (Esc)"))
        self._back_btn.clicked.connect(self._on_back_clicked)
        surface.top_bar.layout().addWidget(self._back_btn)

        self._position_label = QLabel("")
        self._position_label.setObjectName("Sub")
        self._position_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        surface.top_bar.layout().addWidget(self._position_label)

        self._info_label = QLabel("")
        self._info_label.setObjectName("Sub")
        self._info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        surface.top_bar.layout().addWidget(self._info_label, stretch=1)

        self._export_btn = primary_button(tr("Save kept →"))
        self._export_btn.setToolTip(tr(
            "Hand the kept set back to the capture flow. Compare-marked "
            "items count as kept (you can re-decide them in the main "
            "Picker)."
        ))
        self._export_btn.clicked.connect(self._on_save)
        surface.top_bar.layout().addWidget(self._export_btn)
        # Help is in the shared title bar now (routed to show_help / F1).

        # ── STATE_BAR — hidden. State lives on the MediaHost border;
        # Space / C / border-click cycle.
        surface.set_region_visible("state_bar", False)
        surface.media_border_clicked.connect(self._toggle_state)

        # ── MEDIA — PhotoViewport (the shared engine).
        self._viewport = PhotoViewport()
        self._expo_overlay = PhotoExposureOverlay(self._viewport)
        vp = self._viewport
        vp.current_changed.connect(self._on_viewport_current_changed)
        vp.pick_requested.connect(
            lambda: self._verb_set_state(STATE_PICKED))
        vp.skip_requested.connect(
            lambda: self._verb_set_state(STATE_SKIPPED))
        vp.toggle_requested.connect(self._verb_toggle_pick_skip)
        vp.cycle_requested.connect(self._verb_cycle_state)
        vp.fullscreen_requested.connect(self._toggle_fullscreen)
        vp.back_requested.connect(self._on_viewport_back)
        vp.video_playing_changed.connect(self._on_video_playing_changed)
        vp.video_position_changed.connect(self._on_position)
        vp.video_duration_changed.connect(self._on_duration)
        surface.set_media(self._viewport)

        # ── COMPACT_ROW — video transport (only shown for video items).
        self._play_btn = transport_button(tr("Play / pause the video"))
        self._play_btn.clicked.connect(self._viewport.video_toggle_play)
        surface.compact_row.layout().addWidget(self._play_btn)
        self._timeline = QSlider(Qt.Orientation.Horizontal)
        self._timeline.setObjectName("VideoTimeline")
        self._timeline.setRange(0, 0)
        self._timeline.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._timeline.sliderMoved.connect(self._viewport.video_seek)
        surface.compact_row.layout().addWidget(self._timeline, stretch=1)
        self._time_label = QLabel("0:00 / 0:00")
        self._time_label.setObjectName("Sub")
        surface.compact_row.layout().addWidget(self._time_label)
        surface.set_region_visible("compact_row", False)

        # ── TOOLS — hidden.
        surface.set_region_visible("tools", False)

        # ── NAV — ‹ Prev · action cluster · Full Res · Full Screen · Next ›
        nav_layout = surface.nav.layout()
        self._prev_btn = nav_arrow("left")
        self._prev_btn.setToolTip(tr(
            "Previous item  (←  or wheel)"))
        self._prev_btn.clicked.connect(self._go_prev)
        nav_layout.addWidget(self._prev_btn)
        nav_layout.addStretch(1)

        self._pick_btn = ghost_button(tr("✓ Keep  P"))
        self._pick_btn.setObjectName("Pick")
        self._pick_btn.setCheckable(True)
        self._pick_btn.clicked.connect(
            lambda: self._set_state(STATE_PICKED))
        nav_layout.addWidget(self._pick_btn)
        self._skip_btn = danger_ghost_button(tr("✗ Discard  X"))
        self._skip_btn.setObjectName("Skip")
        self._skip_btn.setCheckable(True)
        self._skip_btn.clicked.connect(
            lambda: self._set_state(STATE_SKIPPED))
        nav_layout.addWidget(self._skip_btn)
        self._compare_btn = ghost_button(tr("⇄ Compare  C"))
        self._compare_btn.setObjectName("Compare")
        self._compare_btn.setCheckable(True)
        self._compare_btn.clicked.connect(
            lambda: self._set_state(STATE_CANDIDATE))
        nav_layout.addWidget(self._compare_btn)

        self._fullres_btn = ghost_button(tr("Full Resolution  F10"))
        self._fullres_btn.setToolTip(tr(
            "Inspect this frame at full resolution — peaking, true 1:1 "
            "zoom  (F10)"))
        self._fullres_btn.clicked.connect(
            self._viewport.truth_requested.emit)
        nav_layout.addWidget(self._fullres_btn)
        self._fullscreen_btn = ghost_button(tr("Full Screen  F11"))
        self._fullscreen_btn.setCheckable(True)
        self._fullscreen_btn.setToolTip(tr(
            "Use the whole screen  (F / F11)"))
        self._fullscreen_btn.clicked.connect(self._toggle_fullscreen)
        nav_layout.addWidget(self._fullscreen_btn)

        nav_layout.addStretch(1)
        self._next_btn = nav_arrow("right")
        self._next_btn.setToolTip(tr(
            "Next item  (→  or wheel)"))
        self._next_btn.clicked.connect(self._go_next)
        nav_layout.addWidget(self._next_btn)

        self._viewport.set_corner_inspect_visible(False)
        return surface

    # ── Public API ─────────────────────────────────────────────────────

    def load(
        self,
        items: Sequence[SourceItem],
        *,
        start_index: int = 0,
        state: Optional[dict[Path, str]] = None,
    ) -> bool:
        """Load ``items`` into the viewer. Optional ``state`` carries a
        K/D ledger from the host (e.g. main_window's standalone QS) so
        decisions persist across viewer Open/Close cycles. ``False`` if
        ``items`` is empty.
        """
        if not items:
            self._items = []
            self._state = {}
            return False
        items_sorted = sorted(
            (it for it in items),
            key=lambda it: (
                it.timestamp is None,
                it.timestamp or 0,
                it.path.name,
            ),
        )
        self._read_default_state_setting()
        self._items = list(items_sorted)
        if state is not None:
            self._state = state
        else:
            self._state = {
                it.path: self._legacy_default for it in items_sorted
            }
        self._index = max(0, min(start_index, len(self._items) - 1))
        self._sync_viewport_items(self._index)
        self._viewport.setFocus()
        return True

    def kept_paths(self) -> set[Path]:
        """The set of paths the user marked Keep OR Compare. Compare
        items count as kept (the user re-decides them in the main
        Picker)."""
        return {
            p for p, s in self._state.items()
            if s in (STATE_PICKED, STATE_CANDIDATE)
        }

    def state_for(self, path: Path) -> str:
        """The page's K/D state for ``path``. Hosts that share the
        ledger across DaysGridPage refreshes read this between viewer
        sessions."""
        return self._state.get(path, self._legacy_default)

    def state_ledger(self) -> dict[Path, str]:
        """The full K/D ledger (path → state). Returned by reference;
        the host can re-use it when re-entering :meth:`load`."""
        return self._state

    # ── Lifecycle helpers ─────────────────────────────────────────────

    def _read_default_state_setting(self) -> None:
        """Pull ``quick_sweep_default_state`` out of Settings; fall back
        to Pick (the permissive Quick Sweep default)."""
        try:
            value = SettingsRepo().load().quick_sweep_default_state
        except Exception:                                          # noqa: BLE001
            value = "picked"
        self._legacy_default = (
            STATE_SKIPPED if value == "skipped" else STATE_PICKED
        )

    def _sync_viewport_items(self, index: int) -> None:
        vitems = [
            ViewportItem(
                path=it.path,
                kind="video" if self._is_video(it.path) else "photo",
                payload=it)
            for it in self._items
        ]
        self._viewport.set_items(
            vitems, max(0, min(index, len(vitems) - 1)))

    @staticmethod
    def _is_video(path: Path) -> bool:
        return path.suffix.lower() in VIDEO_EXTENSIONS

    @staticmethod
    def _show_exposure_overlay() -> bool:
        """spec/96 §2 — read the roaming Settings flag at call time so
        a Settings dialog toggle applies on the next item show without
        a relaunch. Defaults to True (preserves today's behaviour) on
        load failure / missing field."""
        try:
            from mira.settings.repo import SettingsRepo
            return bool(SettingsRepo().load().show_exposure_overlay)
        except Exception:                                          # noqa: BLE001
            return True

    @staticmethod
    def _file_size_text_for(path: Path) -> str:
        """Filesystem stat → spec/96 chip-friendly size text. Missing
        file / unreadable stat → ``""`` so the chip drops the segment
        cleanly."""
        try:
            return file_size_text(path.stat().st_size)
        except OSError:
            return ""

    # ── Navigation ─────────────────────────────────────────────────────

    def _go_prev(self) -> None:
        if self._items:
            self._viewport.show_index(
                self._viewport.current_index() - 1)

    def _go_next(self) -> None:
        if self._items:
            self._viewport.show_index(
                self._viewport.current_index() + 1)

    def _on_viewport_current_changed(self, index: int) -> None:
        if not (0 <= index < len(self._items)):
            return
        self._index = index
        item = self._items[index]
        is_video = self._is_video(item.path)
        self._position_label.setText(
            f"{index + 1} / {len(self._items)}")
        bits: List[str] = []
        if item.camera_id:
            bits.append(item.camera_id)
        if item.timestamp is not None:
            bits.append(item.timestamp.strftime("%Y-%m-%d %H:%M:%S"))
        bits.append(item.path.name)
        self._info_label.setText("   ·   ".join(bits))
        # spec/96 §2 — exposure pill: camera + exposure + type + size.
        # Gated by the roaming ``show_exposure_overlay`` setting
        # (default True). The Quick Sweep ``SourceItem`` carries
        # ``camera_id`` and exposes the COMPARE_PARAMS attrs directly,
        # so ``caption_html(item)`` still works for the exposure
        # segment.
        if is_video or not self._show_exposure_overlay():
            self._expo_overlay.set_html("")
        else:
            type_label = file_type_label(item.path.suffix)
            size_text = self._file_size_text_for(item.path)
            self._expo_overlay.set_html(source_chip_html(
                camera=item.camera_id,
                type_label=type_label,
                size_text=size_text,
                exposure_html=caption_html(item),
            ))
        self._fullres_btn.setVisible(not is_video)
        self._surface.set_region_visible("compact_row", is_video)
        if is_video:
            self._video_duration_ms = 0
            set_transport_playing(self._play_btn, True)
            self._timeline.setRange(0, 0)
            self._timeline.setValue(0)
            self._time_label.setText("0:00 / 0:00")
        self._sync_state_pill()

    # ── Viewport verbs ────────────────────────────────────────────────

    def _verb_set_state(self, state: str) -> None:
        if not self._browse_mode:
            self._set_state(state)

    def _verb_toggle_pick_skip(self) -> None:
        if self._browse_mode:
            return
        path = self._current_path()
        if path is None:
            return
        cur = self._state.get(path, self._legacy_default)
        self._set_state(
            STATE_PICKED if cur == STATE_SKIPPED else STATE_SKIPPED)

    def _verb_cycle_state(self) -> None:
        if not self._browse_mode:
            self._toggle_state()

    def _on_viewport_back(self) -> None:
        if self._fullscreen:
            self._exit_fullscreen()
        else:
            self._on_back_clicked()

    # ── State (K/D/C cycle) ────────────────────────────────────────────

    def _current_path(self) -> Optional[Path]:
        item = self._viewport.current_item()
        if item is not None and item.path is not None:
            return item.path
        if self._items and 0 <= self._index < len(self._items):
            return self._items[self._index].path
        return None

    def _cycle_state(self, path: Path) -> None:
        cur = self._state.get(path, STATE_PICKED)
        try:
            idx = _STATE_CYCLE.index(cur)
        except ValueError:
            idx = 0
        new = _STATE_CYCLE[(idx + 1) % len(_STATE_CYCLE)]
        self._state[path] = new

    def _toggle_state(self) -> None:
        path = self._current_path()
        if path is None:
            return
        self._cycle_state(path)
        self._sync_state_pill()

    def _set_state(self, state: str) -> None:
        path = self._current_path()
        if path is None:
            return
        self._state[path] = state
        self._sync_state_pill()

    def _sync_state_pill(self) -> None:
        """Push the current item's state into the action cluster AND
        the MediaHost border colour."""
        path = self._current_path()
        if path is None:
            return
        s = self._state.get(path, self._legacy_default)
        prop = (
            "candidate" if s == STATE_CANDIDATE
            else "skipped" if s == STATE_SKIPPED
            else "picked"
        )
        self._pick_btn.setChecked(s == STATE_PICKED)
        self._skip_btn.setChecked(s == STATE_SKIPPED)
        self._compare_btn.setChecked(s == STATE_CANDIDATE)
        self._surface.set_media_state(prop)

    # ── Video transport ───────────────────────────────────────────────

    def _on_video_playing_changed(self, playing: bool) -> None:
        set_transport_playing(self._play_btn, playing)

    def _on_position(self, ms: int) -> None:
        if not self._timeline.isSliderDown():
            self._timeline.setValue(int(ms))
        self._refresh_time_label(int(ms), self._video_duration_ms)

    def _on_duration(self, ms: int) -> None:
        self._video_duration_ms = int(ms)
        self._timeline.setRange(0, int(ms))
        self._refresh_time_label(self._timeline.value(), int(ms))

    def _refresh_time_label(self, pos_ms: int, dur_ms: int) -> None:
        pos, dur = pos_ms // 1000, dur_ms // 1000
        self._time_label.setText(
            f"{pos // 60}:{pos % 60:02d} / {dur // 60}:{dur % 60:02d}"
        )

    # ── Fullscreen ────────────────────────────────────────────────────

    def _toggle_fullscreen(self) -> None:
        win = self.window()
        if win is None:
            return
        if self._fullscreen:
            win.showNormal()
            self._fullscreen = False
            self._fullscreen_btn.setChecked(False)
        else:
            win.showFullScreen()
            self._fullscreen = True
            self._fullscreen_btn.setChecked(True)
        self.fullscreen_changed.emit(self._fullscreen)

    def _exit_fullscreen(self) -> None:
        if self._fullscreen:
            self._toggle_fullscreen()

    # ── Back routing ──────────────────────────────────────────────────

    def _on_save(self) -> None:
        kept = self.kept_paths()
        log.info(
            "QuickSweepPage Save: %d kept of %d "
            "(Keep + Compare counted)",
            len(kept), len(self._items),
        )
        self.saved.emit(kept)

    def _on_back_clicked(self) -> None:
        """Back / Esc — emits :sig:`cancelled` (the host decides whether
        to confirm-and-save or just route back)."""
        self._viewport.shutdown_video()
        self._exit_fullscreen()
        self.cancelled.emit()

    # ── Keyboard ──────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent) -> None:              # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_F1, Qt.Key.Key_Question):
            self._show_shortcuts()
            event.accept()
            return
        if self._items and key in (Qt.Key.Key_Home, Qt.Key.Key_End):
            self._viewport.show_index(
                0 if key == Qt.Key.Key_Home
                else len(self._items) - 1)
            event.accept()
            return
        super().keyPressEvent(event)

    def show_help(self) -> None:
        """Title-bar Help / F1 hook (this is a page-stack surface)."""
        self._show_shortcuts()

    def _show_shortcuts(self) -> None:
        from mira.ui.base.shortcuts import show_shortcuts
        show_shortcuts(self, tr("Quick Sweep"), [
            ("",                     tr("Triage")),
            (tr("P / X"),             tr("Keep / Discard")),
            (tr("Space"),             tr("Toggle Keep ⇄ Discard")),
            (tr("C"),                 tr("Cycle Keep → Discard → Compare")),
            (tr("Click the border"),  tr("Cycle Keep → Discard → Compare")),
            ("",                     tr("Navigate")),
            (tr("◀ / ▶"),              tr("Previous / next item")),
            (tr("Home / End"),        tr("First / last")),
            (tr("Mouse wheel"),       tr("Previous / next item")),
            ("",                     tr("View")),
            (tr("F10"),               tr("Inspect at full resolution")),
            (tr("F / F11"),           tr("Fullscreen")),
            (tr("Esc"),               tr("Back")),
            (tr("F1 · ?"),            tr("This help")),
        ])
