"""``DayGridView`` — the flat chronological day surface (spec/32 §2).

Replaces the legacy bucket-list level of the navigator with a single
wrapping grid of :class:`DayGridCell` cells, one per item / video / real
cluster, ordered by end-time.

Top bar (left → right):
  * ``← Back`` button (returns to the days panel),
  * Day-label header (``Day N · 2026-04-03 · Arrival``),
  * Cell-size slider (80–280 px), persists via the host on changed.

Body:
  * ``QScrollArea`` → ``FlowLayout`` of ``DayGridCell`` widgets.

Signals (the host wires them to the gateway + page stack):
  * ``back_requested`` — user pressed Back or Esc.
  * ``cell_activated(int)`` — cell index, centre-clicked → open it.
  * ``cell_border_clicked(int)`` — cell index, border-clicked → cycle.
  * ``cell_size_changed(int)`` — slider moved (host persists pref).

The view itself does **not** touch the gateway. The host builds a list
of :class:`CellRenderData` (one per :class:`CullCell`, with a pre-loaded
thumbnail for item cells) and calls :meth:`set_cells`. State cycles and
opens happen by signal — the host re-projects status, rebuilds the
:class:`CellRenderData` for that index, and calls :meth:`update_cell`.

The same widget powers the cluster sub-grid (spec/32 §3) — the host
just feeds it the cluster's members' cells and a header showing the
cluster info.
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional, Sequence

from PyQt6.QtCore import Qt, QTimer, pyqtSignal

log = logging.getLogger(__name__)
from PyQt6.QtGui import QCursor, QKeyEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from mira.ui.base.day_grid_cell import CellRenderData, DayGridCell
from mira.ui.base.flow_layout import FlowLayout
from mira.ui.base.surface import back_button
from mira.ui.i18n import tr


DEFAULT_CELL_SIZE = 140
MIN_CELL_SIZE = 80
MAX_CELL_SIZE = 280

# Chunked construction (Nelson 2026-06-05 — "still very slow"). The first
# ``_CHUNK_FIRST`` cells build synchronously inside ``set_cells`` so the
# day appears to open instantly; the rest land in batches of
# ``_CHUNK_BATCH`` per QTimer tick. Total cell count + per-cell logic is
# unchanged; only the time at which each widget is constructed shifts.
# 50 / 20 lands the first viewport of cells immediately on most monitors
# and keeps each tick's relayout cost bounded for big days.
_CHUNK_FIRST = 50
_CHUNK_BATCH = 20
_CHUNK_TICK_MS = 0   # singleShot(0) = run after the current event loop turn


class DayGridView(QWidget):
    """Flat day grid — host-facing widget (spec/32 §2)."""

    back_requested = pyqtSignal()
    cell_activated = pyqtSignal(int)         # index into the cells list
    cell_border_clicked = pyqtSignal(int)    # index into the cells list
    cell_size_changed = pyqtSignal(int)      # new px
    # spec/32 §2.7 + Nelson 2026-06-04 (cluster sub-grid only):
    # Left/Right arrow → fire ±1 so the host can step to next/prev day cell.
    # Enabled per-instance via ``enable_arrow_nav=True`` in the constructor.
    navigate_at_edge = pyqtSignal(int)
    # Nelson 2026-06-06: "Start a new pass…" — on the day grid (one level
    # above the single-photo view), so clicking it clears the ✓ ticks the
    # user sees right now. Hosts (PickPage / EditHostPage) connect this
    # to their existing _on_clear_marks handler (confirm + gateway call +
    # refresh-cells). Off by default; phases that opt in pass
    # ``show_clear_marks_button=True``.
    clear_marks_requested = pyqtSignal()
    # Nelson 2026-06-07 — day-scope batch ops inside the Day Grid. The
    # buttons appear on the top bar; clicking fires the matching signal
    # and the host applies the op to every item the user is currently
    # looking at (the day's cells). Off by default; phases opt in via
    # the constructor flags below.
    pick_all_requested = pyqtSignal()
    skip_all_requested = pyqtSignal()
    export_all_requested = pyqtSignal()
    # Nelson 2026-06-09 — Compare affordance. The button surfaces on the
    # top bar only when the grid currently has 2+ Compare-state photos
    # (counting cluster members when the cluster aggregate is COMPARE).
    # Host listens and pushes a temporary :class:`ComparePage` populated
    # with those items. Off by default; phases opt in via
    # ``show_compare_button=True``.
    compare_requested = pyqtSignal()
    # Nelson 2026-06-09 — cluster slideshow affordance. The Play button on
    # the photo surface only paints once the user has drilled into a
    # cluster member, which (post-Compare-redesign) is one click deeper
    # than the legacy "click cluster → grid → Play" path. This signal
    # surfaces the slideshow at the cluster sub-grid level so the host
    # can launch the photo surface with Play already running. Off by
    # default; the cluster sub-grid opts in via ``show_play_button=True``
    # and the host calls :meth:`set_play_button_visible` per-cluster
    # (only playable cluster kinds — burst / focus / exposure bracket —
    # should show it).
    play_requested = pyqtSignal()

    def __init__(
        self,
        *,
        cell_size: Optional[int] = None,
        enable_arrow_nav: bool = False,
        show_clear_marks_button: bool = False,
        show_pick_all_button: bool = False,
        show_skip_all_button: bool = False,
        show_export_all_button: bool = False,
        show_compare_button: bool = False,
        show_play_button: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("DayGridView")
        # ``cell_size=None`` (default) reads the user-tunable
        # ``default_day_grid_cell_size`` setting (Nelson 2026-06-09 audit).
        # An explicit value still overrides for tests / call-site needs.
        if cell_size is None:
            try:
                from mira.settings.repo import SettingsRepo
                cell_size = SettingsRepo().load().default_day_grid_cell_size
            except Exception:                                   # noqa: BLE001
                cell_size = DEFAULT_CELL_SIZE
        self._cell_size = max(MIN_CELL_SIZE, min(MAX_CELL_SIZE, int(cell_size)))
        # The full set of cell render data — drives ``cell_count``, the
        # chunked builder, and the host's update_cell / set_cell_thumbnail
        # paths even before a cell's widget exists.
        self._all_cell_data: List[CellRenderData] = []
        # The widgets actually constructed so far (built in chunks). Indices
        # in ``self._cells`` always match the position in
        # ``self._all_cell_data`` (i.e. ``self._cells[i].render_data() ==
        # self._all_cell_data[i]`` for ``i < len(self._cells)``).
        self._cells: List[DayGridCell] = []
        # A monotonic build-token: ``set_cells`` bumps it so any still-pending
        # builder ticks from a previous call drop their work instead of
        # appending stale widgets.
        self._build_token: int = 0
        self._header_text = ""
        self._enable_arrow_nav = bool(enable_arrow_nav)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Top bar ─────────────────────────────────────────────────────
        top = QWidget(self)
        top.setObjectName("DayGridTopBar")
        top_lay = QHBoxLayout(top)
        top_lay.setContentsMargins(16, 10, 16, 10)
        top_lay.setSpacing(12)

        # The day-grid's leave button. Plain Back via the factory; the
        # secondary objectName "DayGridBackButton" is kept for tests
        # and any future per-surface override (the QSS audit reserves
        # the slot but adds no rules today).
        self._back = back_button()
        self._back.setObjectName("DayGridBackButton")
        self._back.setToolTip(tr("Return to the days list. (Esc)"))
        self._back.clicked.connect(self.back_requested.emit)
        top_lay.addWidget(self._back)

        # Nelson 2026-06-07 — day-scope batch ops on the day grid top bar.
        # Three buttons, all off by default; phases opt in. Pick uses
        # Pick all + Skip all; Edit uses Export all. All act on the
        # ENTIRE day the user is currently looking at — the host gathers
        # the day's items and runs the op.
        self._pick_all_btn = QPushButton(tr("✓ Pick all"))
        self._pick_all_btn.setObjectName("DayGridPickAllButton")
        self._pick_all_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._pick_all_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._pick_all_btn.setToolTip(tr(
            "Mark every item in this day as Picked."))
        self._pick_all_btn.clicked.connect(self.pick_all_requested.emit)
        self._pick_all_btn.setVisible(bool(show_pick_all_button))
        top_lay.addWidget(self._pick_all_btn)

        self._skip_all_btn = QPushButton(tr("✗ Skip all"))
        self._skip_all_btn.setObjectName("DayGridSkipAllButton")
        self._skip_all_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._skip_all_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._skip_all_btn.setToolTip(tr(
            "Mark every item in this day as Skipped."))
        self._skip_all_btn.clicked.connect(self.skip_all_requested.emit)
        self._skip_all_btn.setVisible(bool(show_skip_all_button))
        top_lay.addWidget(self._skip_all_btn)

        # Nelson 2026-06-09 — Compare button: opens the dedicated compare
        # surface against the current grid's Compare-state photos. Always
        # constructed; visibility is opt-in AND state-driven (≥2 Compare
        # photos in the current cell set). Phases opt in via
        # ``show_compare_button=True``; legacy callers see a no-op.
        self._compare_btn_enabled = bool(show_compare_button)
        self._compare_btn = QPushButton(tr("Compare"))
        self._compare_btn.setObjectName("DayGridCompareButton")
        self._compare_btn.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        self._compare_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._compare_btn.setToolTip(tr(
            "Open the Compare-state photos in a side-by-side compare "
            "grid.  (C)"))
        self._compare_btn.clicked.connect(self.compare_requested.emit)
        self._compare_btn.setVisible(False)                 # gated by state
        top_lay.addWidget(self._compare_btn)

        # Nelson 2026-06-09 — Play affordance. Always constructed; the
        # ``show_play_button`` opt-in pins it to the cluster sub-grid in
        # PickPage, and the host flips per-cluster visibility via
        # :meth:`set_play_button_visible` so only playable cluster kinds
        # (burst + focus / exposure bracket) surface it. Click emits
        # ``play_requested`` — the host opens the cluster's photo
        # surface with the slideshow already running.
        self._play_btn_enabled = bool(show_play_button)
        self._play_btn = QPushButton(tr("▶ Play"))
        self._play_btn.setObjectName("DayGridPlayButton")
        self._play_btn.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        self._play_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._play_btn.setToolTip(tr(
            "Play the cluster as a sequence so you see the frames "
            "sweep."))
        self._play_btn.clicked.connect(self.play_requested.emit)
        self._play_btn.setVisible(False)                    # opt-in + host-gated
        top_lay.addWidget(self._play_btn)

        self._export_all_btn = QPushButton(tr("📤 Export all"))
        self._export_all_btn.setObjectName("DayGridExportAllButton")
        self._export_all_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._export_all_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._export_all_btn.setToolTip(tr(
            "Export every processed photo in this day."))
        self._export_all_btn.clicked.connect(self.export_all_requested.emit)
        self._export_all_btn.setVisible(bool(show_export_all_button))
        top_lay.addWidget(self._export_all_btn)

        self._header = QLabel("")
        self._header.setObjectName("DayGridHeader")
        self._header.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        top_lay.addWidget(self._header, stretch=1)

        # Nelson 2026-06-06 — "Start a new pass…" on the day grid itself
        # so clicking it clears the ✓ ticks the user is currently looking
        # at. The host (PickPage / EditHostPage) connects
        # ``clear_marks_requested`` to its existing _on_clear_marks
        # handler (confirm + gateway + refresh-cells).
        self._new_pass_btn = QPushButton(tr("Start a new pass…"))
        self._new_pass_btn.setObjectName("DayGridNewPassButton")
        self._new_pass_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._new_pass_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._new_pass_btn.setToolTip(tr(
            "Clear all ✓ visited ticks in this day so you can walk it "
            "fresh."))
        self._new_pass_btn.clicked.connect(self.clear_marks_requested.emit)
        self._new_pass_btn.setVisible(bool(show_clear_marks_button))
        top_lay.addWidget(self._new_pass_btn)

        # Size slider on the right.
        size_label = QLabel(tr("Size"))
        size_label.setObjectName("DayGridSizeLabel")
        size_label.setToolTip(tr(
            "Cell size — drag to enlarge or shrink the grid (80–280 px)."
        ))
        top_lay.addWidget(size_label)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setObjectName("DayGridSizeSlider")
        self._slider.setRange(MIN_CELL_SIZE, MAX_CELL_SIZE)
        self._slider.setValue(self._cell_size)
        self._slider.setFixedWidth(180)
        self._slider.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._slider.setToolTip(tr("Cell size: %d px") % self._cell_size)
        # Nelson 2026-06-04 — applying size on every valueChanged tick relayouts
        # all N cells per tick (200+ cells on a busy day → drag lag). Update the
        # tooltip live so the user sees the value moving, but defer the actual
        # cell resize to sliderReleased so the relayout fires only once.
        self._slider.valueChanged.connect(self._on_slider_value)
        self._slider.sliderReleased.connect(self._on_slider_released)
        top_lay.addWidget(self._slider)

        outer.addWidget(top)

        # ── Body: scrolling flow of cells ───────────────────────────────
        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("DayGridScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._host = QWidget(self._scroll)
        self._host.setObjectName("DayGridHost")
        self._flow = FlowLayout(self._host, margin=12, spacing=10)
        self._scroll.setWidget(self._host)
        outer.addWidget(self._scroll, stretch=1)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ── public API ────────────────────────────────────────────────────

    def set_header(self, text: str) -> None:
        """Set the day-label / cluster-info header text (spec/32 §5)."""
        self._header_text = text or ""
        self._header.setText(self._header_text)

    def header_text(self) -> str:
        return self._header_text

    def set_play_tooltip(self, text: str) -> None:
        """Host-specific Play wording (the Cut detail surface plays the
        whole show, not a cluster sweep)."""
        self._play_btn.setToolTip(text)

    def set_play_button_visible(self, visible: bool) -> None:
        """Flip the Play button visibility (host-driven, per-cluster).

        Only effective when the grid was constructed with
        ``show_play_button=True``; legacy callers stay no-op.
        PickPage calls this in :meth:`_open_cluster` so the button paints
        only for cluster kinds the photo surface's slideshow supports
        (burst / focus_bracket / exposure_bracket); repeat clusters and
        flat day grids see nothing."""
        if not self._play_btn_enabled:
            return
        self._play_btn.setVisible(bool(visible))

    def _count_flat_compare_cells(self) -> int:
        """Count cells in Compare state that are flat (non-cluster).

        Cluster cells are skipped — per the design (Nelson 2026-06-09),
        cluster members can only be compared against other members of
        the same cluster, so the Compare affordance for a cluster lives
        on the cluster sub-grid (whose cells are all flat members)."""
        from mira.picked.status import CellColor
        n = 0
        for c in self._all_cell_data:
            cell = c.cell
            if cell.is_cluster:
                continue
            if cell.color == CellColor.COMPARE:
                n += 1
        return n

    def _refresh_compare_button_visibility(self) -> None:
        """Show the Compare button iff the host opted in AND the current
        cell set has 2+ flat Compare-state cells."""
        if not self._compare_btn_enabled:
            return
        self._compare_btn.setVisible(self._count_flat_compare_cells() >= 2)

    def set_cells(self, cells: Sequence[CellRenderData]) -> None:
        """Replace the grid contents — clears existing cells and rebuilds
        from ``cells``. Order matters: index = position in the flow.

        **Chunked construction (Nelson 2026-06-05).** The first
        :data:`_CHUNK_FIRST` cells are built synchronously so the day
        appears to open instantly; the rest land in batches of
        :data:`_CHUNK_BATCH` per QTimer tick. ``cell_count`` always
        reports the full N; ``update_cell`` / ``set_cell_thumbnail`` route
        to the cached render-data so updates from the host (state cycles,
        lazy thumbnails) still apply to pending cells when they're built.

        Wrapped in ``setUpdatesEnabled(False)`` on the scroll viewport so
        Qt batches paint/layout events during the synchronous batch instead
        of firing per-widget (a 50-cell add would otherwise be 50 relayouts)."""
        # Bump the build token so any still-pending builder from a prior
        # set_cells call recognises it's stale and drops its work.
        self._build_token += 1
        token = self._build_token

        self._all_cell_data = list(cells)
        viewport = self._scroll.viewport()
        viewport.setUpdatesEnabled(False)
        t0 = time.perf_counter()
        try:
            self._clear_cells()
            t_clear = time.perf_counter()
            first = min(_CHUNK_FIRST, len(self._all_cell_data))
            for idx in range(first):
                self._build_cell_at(idx)
            t_build = time.perf_counter()
            # Scroll back to top whenever the contents change.
            self._scroll.verticalScrollBar().setValue(0)
        finally:
            viewport.setUpdatesEnabled(True)
        t_done = time.perf_counter()
        log.debug(
            "DayGridView.set_cells: total=%d first_batch=%d "
            "clear=%.0fms build=%.0fms enable_updates=%.0fms",
            len(self._all_cell_data), first,
            (t_clear - t0) * 1000,
            (t_build - t_clear) * 1000,
            (t_done - t_build) * 1000,
        )

        if len(self._cells) < len(self._all_cell_data):
            # Schedule the next batch from a zero-delay timer so the first
            # batch lands first and the user sees the day before the
            # remaining cells fill in.
            QTimer.singleShot(_CHUNK_TICK_MS, lambda: self._build_next_batch(token))

        # Compare button is gated by the current cell set, not by widget
        # construction state — recompute on every replacement.
        self._refresh_compare_button_visibility()

    def _build_cell_at(self, idx: int) -> None:
        """Build the widget for ``self._all_cell_data[idx]`` and append it.

        Caller must guarantee ``idx == len(self._cells)`` (append order)."""
        data = self._all_cell_data[idx]
        cell = DayGridCell(data, size=self._cell_size, parent=self._host)
        cell.border_clicked.connect(
            lambda _bound=idx: self.cell_border_clicked.emit(_bound))
        cell.center_clicked.connect(
            lambda _bound=idx: self.cell_activated.emit(_bound))
        self._flow.addWidget(cell)
        self._cells.append(cell)

    def _build_next_batch(self, token: int) -> None:
        """Build the next :data:`_CHUNK_BATCH` pending cells; re-schedule
        until all built. ``token`` drops the work if a fresh ``set_cells``
        call has since invalidated this builder."""
        if token != self._build_token:
            return
        if not self.isVisible() and self._scroll is None:
            return
        remaining = len(self._all_cell_data) - len(self._cells)
        if remaining <= 0:
            return
        batch = min(_CHUNK_BATCH, remaining)
        viewport = self._scroll.viewport()
        viewport.setUpdatesEnabled(False)
        t0 = time.perf_counter()
        try:
            start = len(self._cells)
            for idx in range(start, start + batch):
                self._build_cell_at(idx)
        finally:
            viewport.setUpdatesEnabled(True)
        t_done = time.perf_counter()
        built = len(self._cells)
        total = len(self._all_cell_data)
        log.debug(
            "DayGridView batch: built=%d/%d batch_size=%d cost=%.0fms",
            built, total, batch, (t_done - t0) * 1000,
        )
        if built < total:
            QTimer.singleShot(_CHUNK_TICK_MS, lambda: self._build_next_batch(token))

    def update_cell(self, index: int, data: CellRenderData) -> None:
        """Replace one cell's render data in place (host calls after a
        state cycle / a thumbnail finishes loading). Works for both built
        and pending cells — for a pending cell the data is stored so the
        chunked builder uses it when it lands."""
        if not (0 <= index < len(self._all_cell_data)):
            return
        self._all_cell_data[index] = data
        if index < len(self._cells):
            self._cells[index].set_data(data)
        # State may have moved into / out of Compare → re-evaluate.
        self._refresh_compare_button_visibility()

    def set_cell_thumbnail(self, index: int, pixmap) -> None:
        """Set just the thumbnail on a cell (lazy-load callback). Updates
        the cached render-data too so a still-pending cell shows the thumb
        when the chunked builder reaches it."""
        if not (0 <= index < len(self._all_cell_data)):
            return
        prev = self._all_cell_data[index]
        self._all_cell_data[index] = CellRenderData(
            cell=prev.cell, thumbnail=pixmap)
        if index < len(self._cells):
            self._cells[index].set_thumbnail(pixmap)

    def cell_count(self) -> int:
        return len(self._all_cell_data)

    def cell_at(self, index: int) -> Optional[DayGridCell]:
        if 0 <= index < len(self._cells):
            return self._cells[index]
        return None

    def cells(self) -> List[DayGridCell]:
        return list(self._cells)

    def pending_cell_count(self) -> int:
        """Cells not yet built (chunked builder still has work)."""
        return max(0, len(self._all_cell_data) - len(self._cells))

    def set_cell_size(self, size: int) -> None:
        """Programmatically change the cell size (and the slider). Re-flows."""
        size = max(MIN_CELL_SIZE, min(MAX_CELL_SIZE, int(size)))
        self._cell_size = size
        # The slider drives the change-emit path; setValue() without
        # touching the cells avoids a double-resize.
        prev = self._slider.blockSignals(True)
        self._slider.setValue(size)
        self._slider.blockSignals(prev)
        self._slider.setToolTip(tr("Cell size: %d px") % size)
        for c in self._cells:
            c.set_size(size)

    def cell_size(self) -> int:
        return self._cell_size

    # ── internals ─────────────────────────────────────────────────────

    def _clear_cells(self) -> None:
        for c in self._cells:
            self._flow.removeWidget(c)
            c.setParent(None)
            c.deleteLater()
        self._cells.clear()

    def _on_slider_value(self, value: int) -> None:
        """Value-changed handler. During an active drag (``isSliderDown()``)
        only the tooltip moves — the cell resize fires on release so a 200-
        cell day doesn't relayout per drag tick. Programmatic changes
        (``setValue``) and track-clicks land here too and ARE applied
        immediately (no drag in progress)."""
        self._slider.setToolTip(tr("Cell size: %d px") % int(value))
        if not self._slider.isSliderDown():
            self._apply_slider_value(int(value))

    def _on_slider_released(self) -> None:
        """Drag release — apply the final size in one shot."""
        self._apply_slider_value(int(self._slider.value()))

    def _apply_slider_value(self, value: int) -> None:
        if value == self._cell_size:
            return
        self._cell_size = value
        for c in self._cells:
            c.set_size(self._cell_size)
        self.cell_size_changed.emit(self._cell_size)

    # ── keyboard ──────────────────────────────────────────────────────

    def keyPressEvent(self, ev: QKeyEvent) -> None:  # noqa: N802
        if ev.key() == Qt.Key.Key_Escape:
            self.back_requested.emit()
            ev.accept()
            return
        if self._enable_arrow_nav:
            if ev.key() in (Qt.Key.Key_Right, Qt.Key.Key_Down,
                             Qt.Key.Key_PageDown):
                self.navigate_at_edge.emit(+1)
                ev.accept()
                return
            if ev.key() in (Qt.Key.Key_Left, Qt.Key.Key_Up,
                             Qt.Key.Key_PageUp):
                self.navigate_at_edge.emit(-1)
                ev.accept()
                return
        super().keyPressEvent(ev)
