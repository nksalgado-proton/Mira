"""``ThumbGrid`` — the shared scrolling thumbnail grid.

A single reusable widget for every flat capture grid in the app
(Days Grid, Cut detail, Cut session, #exported pool detail). Wraps a
``QScrollArea`` over a ``FlowLayout`` of :class:`mira.ui.design.Thumb`
tiles + the chunked-construction discipline so a 200-cell day appears
to open instantly. Replaces ``mira.ui.base.day_grid_view.DayGridView``
and ``mira.ui.base.day_grid_cell.DayGridCell``: the locked §5a 3px
state border, the blurred-fill backdrop, and the corner badges all
ride :class:`Thumb`'s painter directly, so every surface gets the
same look without forking the chrome.

API:
    set_items(items)            — replace the contents; chunked build
    update_item(index, item)    — refresh one cell's data
    set_pixmap(index, pixmap)   — convenience for the async thumb loader
    count()                     — current item count
    cell_at(index)              — the Thumb widget (tests/host introspection)

Signals:
    cell_activated(index)       — single click (or center-zone click in
                                  two-zone mode)
    cell_border_clicked(index)  — border-zone click; only fires when
                                  ``two_zone_clicks=True``
    back_requested()            — Esc pressed inside the grid

The async thumb decoder is NOT bundled here — each consumer keeps its
own loader (PickPage's tier vs ``photo_cache.request_scaled_pixmap``)
and calls :meth:`set_pixmap` / :meth:`update_item` as pixmaps arrive.
The chunked-construction discipline lives here so the surface opens
fast without each consumer reinventing it.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QMouseEvent, QPixmap
from PyQt6.QtWidgets import (
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mira.ui.base.flow_layout import FlowLayout
from mira.ui.design.thumbs import Thumb

log = logging.getLogger(__name__)


# Chunked construction (Nelson 2026-06-05 — same numbers DayGridView shipped
# with): the first ``_CHUNK_FIRST`` cells build synchronously inside
# ``set_items`` so the surface appears to open instantly; the rest land in
# batches of ``_CHUNK_BATCH`` per QTimer tick.
_CHUNK_FIRST = 50
_CHUNK_BATCH = 20
_CHUNK_TICK_MS = 0   # singleShot(0) — after the current event loop turn

# Default tile size — matches DaysGridPage's ``_TILE_SIZE`` so the
# capture grid looks identical after the migration.
DEFAULT_CELL_SIZE = QSize(196, 146)

# Border-zone hit-test ratio: a click within ``BORDER_RATIO * min(w, h)``
# of any edge is the border zone (mirrors the legacy DayGridCell rule).
# Nelson 2026-06-18 — bumped from 0.10 / 6 / 22 to 0.15 / 10 / 32: the
# painted border stays the same width, only the click-hit zone widens
# inward so "near the boundary" reliably catches the cycle. Wide tile
# (~280px) hit zone goes from ~22px to ~32px; small tile (~140px)
# from ~14px to ~21px.
BORDER_RATIO = 0.15
MIN_BORDER_PX = 10
MAX_BORDER_PX = 32


@dataclass
class ThumbGridItem:
    """One grid cell's rendered content. Maps onto :class:`Thumb`'s
    constructor 1:1 plus an opaque ``payload`` the host uses to route
    clicks back to its own model (relpath, item id, CullCell, whatever).

    The host owns the payload; the grid is content-agnostic."""

    pixmap: Optional[QPixmap] = None
    state: Optional[str] = None           # 'picked'/'skipped'/'compare'/'mixed'/None
    visited: bool = False
    exported: bool = False
    edit_reasons: Tuple[str, ...] = ()    # 'look'/'filter'/'crop' → amber pill glyphs
    border_token: Optional[str] = None    # 'green' unedited / 'amber' edited (Edit grid)
    cluster_type: Optional[str] = None    # 'repeated'/'burst'/'focus'/'exposure'/'video'
    cluster_count: int = 0
    cluster_split: Optional[Tuple[int, int]] = None
    stamp: Optional[str] = None           # 'clip' / 'snapshot' (spec/56 child stamp)
    origin: Optional[str] = None          # spec/89 §2.1 — Mira / LRC / Helicon / CO / ext
    skipped_in_pick: bool = False         # spec/89 Block 7 D2.B indicator
    export_destructive_mode: bool = False # spec/89 Block 7 D3.B Slice 7 — watermark = destructive cue
    payload: object = None
    focusable: bool = False               # Tab-focusable for the locked §63 keymap
    tooltip: str = ""


class _GridCell(Thumb):
    """A :class:`Thumb` that knows its grid index + can route clicks
    through a border/center hit-test. Subclasses :class:`Thumb` so the
    paint + state-border contract is inherited verbatim; only the
    mouse routing is extended."""

    border_clicked = pyqtSignal(int)
    center_clicked = pyqtSignal(int)

    def __init__(
        self,
        index: int,
        item: ThumbGridItem,
        *,
        size: QSize,
        two_zone: bool,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(
            item.pixmap,
            state=item.state,
            size=size,
            cluster_type=item.cluster_type,
            cluster_count=item.cluster_count,
            cluster_split=item.cluster_split,
            visited=item.visited,
            exported=item.exported,
            edit_reasons=item.edit_reasons,
            border_token=item.border_token,
            stamp=item.stamp,
            origin=item.origin,
            skipped_in_pick=item.skipped_in_pick,
            parent=parent,
        )
        self.setExportDestructiveMode(item.export_destructive_mode)
        self._index = index
        self._two_zone = bool(two_zone)
        self._payload = item.payload
        if item.focusable:
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        if item.tooltip:
            self.setToolTip(item.tooltip)

    # ── public introspection used by ThumbGrid.update_item ───────────

    def index(self) -> int:
        return self._index

    def payload(self) -> object:
        return self._payload

    def apply_item(self, item: ThumbGridItem) -> None:
        """Replace this cell's content + state without rebuilding the
        widget. Stays inside :class:`Thumb`'s painted contract — one
        ``update()`` call repaints the new border + chips."""
        self._payload = item.payload
        self.setPixmap(item.pixmap)
        self.setState(item.state)
        self.setVisited(item.visited)
        self.setExported(item.exported)
        self.setEditReasons(item.edit_reasons)
        self.setBorderToken(item.border_token)
        self.setStamp(item.stamp)
        self.setOrigin(item.origin)
        self.setSkippedInPick(item.skipped_in_pick)
        self.setExportDestructiveMode(item.export_destructive_mode)
        self.setClusterCount(item.cluster_count)
        # cluster_type + cluster_split aren't setter-exposed on Thumb;
        # write them directly (paintEvent reads instance attrs).
        self._cluster_type = item.cluster_type
        self._cluster_split = item.cluster_split
        if item.tooltip:
            self.setToolTip(item.tooltip)
        self.update()

    # ── click routing ────────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — Qt
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        if not self._two_zone:
            # Single-zone: the inherited Thumb routes the click via its
            # ``clicked`` signal — the grid listens there.
            super().mousePressEvent(event)
            return
        # Two-zone hit-test: a press within ``b`` of any edge routes to
        # border; everything else to center. Mirrors the legacy
        # DayGridCell._border_px rule so the migration preserves the
        # exact click grammar.
        b = self._border_px()
        pos = event.position().toPoint()
        in_border = (
            pos.x() < b
            or pos.x() >= self.width() - b
            or pos.y() < b
            or pos.y() >= self.height() - b
        )
        if in_border:
            self.border_clicked.emit(self._index)
        else:
            self.center_clicked.emit(self._index)
        event.accept()

    def _border_px(self) -> int:
        side = min(self.width(), self.height())
        return max(MIN_BORDER_PX, min(MAX_BORDER_PX, int(side * BORDER_RATIO)))

    # Hit-test helper (exposed for unit tests, same shape as the
    # legacy DayGridCell.hit_zone).
    def hit_zone(self, x: int, y: int) -> str:
        if not (0 <= x < self.width() and 0 <= y < self.height()):
            return "outside"
        b = self._border_px()
        if (
            x < b or x >= self.width() - b
            or y < b or y >= self.height() - b
        ):
            return "border"
        return "center"


class ThumbGrid(QWidget):
    """Shared scrolling thumbnail grid.

    Internally: ``QScrollArea`` over a ``FlowLayout`` host that holds
    one :class:`_GridCell` per item. Construction is chunked
    (:data:`_CHUNK_FIRST` cells synchronously, the rest in batches per
    QTimer tick) so a 200-cell day opens instantly.

    The widget is content-agnostic — it never reads the host's model.
    Items in / items out: :meth:`set_items`, :meth:`update_item`,
    :meth:`set_pixmap` + the ``payload`` field on each
    :class:`ThumbGridItem`.
    """

    cell_activated = pyqtSignal(int)
    cell_border_clicked = pyqtSignal(int)
    back_requested = pyqtSignal()

    def __init__(
        self,
        *,
        cell_size: QSize = DEFAULT_CELL_SIZE,
        two_zone_clicks: bool = False,
        flow_spacing: int = 10,
        flow_margin: int = 12,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ThumbGrid")
        self._cell_size = QSize(cell_size)
        self._two_zone = bool(two_zone_clicks)
        self._items: List[ThumbGridItem] = []
        self._cells: List[_GridCell] = []
        # A monotonic build-token: ``set_items`` bumps it so any pending
        # builder tick from a previous call drops its work instead of
        # appending stale widgets.
        self._build_token: int = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("ThumbGridScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._host = QWidget(self._scroll)
        self._host.setObjectName("ThumbGridHost")
        self._flow = FlowLayout(
            self._host, margin=flow_margin, spacing=flow_spacing)
        self._scroll.setWidget(self._host)
        self._scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        outer.addWidget(self._scroll, 1)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ── public API ────────────────────────────────────────────────────

    def set_cell_size(self, size: QSize) -> None:
        """Replace the per-cell fixed size. Rebuilds the existing cells
        in place (no host data is touched)."""
        self._cell_size = QSize(size)
        for c in self._cells:
            c.setFixedSize(self._cell_size)

    def cell_size(self) -> QSize:
        return QSize(self._cell_size)

    def set_items(self, items: Sequence[ThumbGridItem]) -> None:
        """Replace the grid contents.

        Chunked construction: the first :data:`_CHUNK_FIRST` cells
        build synchronously inside this call; the rest land via
        QTimer.singleShot batches. Updates ``update_item`` /
        ``set_pixmap`` arriving while the chunked builder is still
        working are kept on the items list and applied when the cell
        is finally built."""
        self._build_token += 1
        token = self._build_token
        self._items = list(items)
        viewport = self._scroll.viewport()
        viewport.setUpdatesEnabled(False)
        t0 = time.perf_counter()
        try:
            self._clear_cells()
            first = min(_CHUNK_FIRST, len(self._items))
            for idx in range(first):
                self._build_cell_at(idx)
            self._scroll.verticalScrollBar().setValue(0)
        finally:
            viewport.setUpdatesEnabled(True)
        log.debug(
            "ThumbGrid.set_items: total=%d first_batch=%d cost=%.0fms",
            len(self._items), first,
            (time.perf_counter() - t0) * 1000,
        )
        if len(self._cells) < len(self._items):
            QTimer.singleShot(
                _CHUNK_TICK_MS, lambda: self._build_next_batch(token))

    def update_item(self, index: int, item: ThumbGridItem) -> None:
        """Replace one cell's data. Works for both built and pending
        cells — pending cells pick up the new data when the chunked
        builder reaches them."""
        if not (0 <= index < len(self._items)):
            return
        self._items[index] = item
        if index < len(self._cells):
            self._cells[index].apply_item(item)

    def set_pixmap(self, index: int, pixmap: Optional[QPixmap]) -> None:
        """Convenience: refresh just the pixmap on one cell. The async
        thumb loaders use this when a decode lands."""
        if not (0 <= index < len(self._items)):
            return
        prev = self._items[index]
        # Mutate the stored item so re-builds pick up the new pixmap.
        prev.pixmap = pixmap
        if index < len(self._cells):
            self._cells[index].setPixmap(pixmap)

    def count(self) -> int:
        return len(self._items)

    def pending_count(self) -> int:
        return max(0, len(self._items) - len(self._cells))

    def items(self) -> List[ThumbGridItem]:
        return list(self._items)

    def cell_at(self, index: int) -> Optional[_GridCell]:
        if 0 <= index < len(self._cells):
            return self._cells[index]
        return None

    def cells(self) -> List[_GridCell]:
        return list(self._cells)

    # ── internals ─────────────────────────────────────────────────────

    def _clear_cells(self) -> None:
        for c in self._cells:
            self._flow.removeWidget(c)
            c.setParent(None)
            c.deleteLater()
        self._cells.clear()

    def _build_cell_at(self, idx: int) -> None:
        item = self._items[idx]
        cell = _GridCell(
            idx, item,
            size=self._cell_size,
            two_zone=self._two_zone,
            parent=self._host,
        )
        if self._two_zone:
            cell.border_clicked.connect(self.cell_border_clicked.emit)
            cell.center_clicked.connect(self.cell_activated.emit)
        else:
            cell.clicked.connect(
                lambda _bound=idx: self.cell_activated.emit(_bound))
        self._flow.addWidget(cell)
        self._cells.append(cell)

    def _build_next_batch(self, token: int) -> None:
        if token != self._build_token:
            return
        remaining = len(self._items) - len(self._cells)
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
        log.debug(
            "ThumbGrid batch: built=%d/%d batch=%d cost=%.0fms",
            len(self._cells), len(self._items), batch,
            (time.perf_counter() - t0) * 1000,
        )
        if len(self._cells) < len(self._items):
            QTimer.singleShot(
                _CHUNK_TICK_MS, lambda: self._build_next_batch(token))

    # ── keyboard ──────────────────────────────────────────────────────

    def keyPressEvent(self, ev: QKeyEvent) -> None:  # noqa: N802 — Qt
        if ev.key() == Qt.Key.Key_Escape:
            self.back_requested.emit()
            ev.accept()
            return
        super().keyPressEvent(ev)


__all__ = ["ThumbGrid", "ThumbGridItem", "DEFAULT_CELL_SIZE"]
