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

# Two-zone hit-test rule (spec/103, Nelson 2026-06-22): the outer
# quarter on each axis is the BORDER zone (status toggle); the central
# 50%×50% rectangle is the CENTER zone (open / drill-in). This is the
# literal "closer to an edge than to the centre line" split — on a
# single axis ``x < w/4`` means the click is nearer the left edge
# than the centre. The painted 3 px state border is untouched; this
# only widens the click target so "near the border" reliably toggles
# the status instead of missing a thin 32-px band.


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
    edited_since_export: bool = False     # spec/118 §2 — loud "edited" badge on stale exports
    payload: object = None
    focusable: bool = False               # Tab-focusable for the locked §63 keymap
    tooltip: str = ""
    # spec/159 — per-version review chrome for the Exported Collection.
    stars: Optional[int] = None
    color_label: Optional[str] = None
    flag: bool = False
    to_delete: bool = False


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
            edited_since_export=item.edited_since_export,
            stars=item.stars,
            color_label=item.color_label,
            flag=item.flag,
            to_delete=item.to_delete,
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
        self.setEditedSinceExport(item.edited_since_export)
        self.setClusterCount(item.cluster_count)
        # spec/159 — per-version review chrome.
        self.setStars(item.stars)
        self.setColorLabel(item.color_label)
        self.setFlag(item.flag)
        self.setToDelete(item.to_delete)
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
        # Two-zone hit-test (spec/103): the outer quarter on each axis
        # is BORDER (status toggle); the central 50%×50% is CENTER
        # (open / drill-in). A click closer to an edge than to the
        # centre line on EITHER axis toggles status — a much larger
        # target than the legacy ≤32-px band that scales with the tile.
        bx = self.width() // 4
        by = self.height() // 4
        pos = event.position().toPoint()
        in_border = (
            pos.x() < bx
            or pos.x() >= self.width() - bx
            or pos.y() < by
            or pos.y() >= self.height() - by
        )
        if in_border:
            self.border_clicked.emit(self._index)
        else:
            self.center_clicked.emit(self._index)
        event.accept()

    # Hit-test helper (exposed for unit tests). Same outer-quarter
    # rule as ``mousePressEvent`` above — must stay in lockstep.
    def hit_zone(self, x: int, y: int) -> str:
        if not (0 <= x < self.width() and 0 <= y < self.height()):
            return "outside"
        bx = self.width() // 4
        by = self.height() // 4
        if (
            x < bx or x >= self.width() - bx
            or y < by or y >= self.height() - by
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
    # spec/131 — fired once the chunked builder has placed every cell
    # for the current ``set_items`` call. Lets the host (or
    # :meth:`ensure_item_visible`) defer "scroll to an anchor" work
    # until the target cell actually exists in the layout.
    build_finished = pyqtSignal()

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
        # spec/131 — deferred-anchor state. When the host calls
        # :meth:`ensure_item_visible` before the chunked builder reaches
        # the target cell, we stash the request here and apply it on
        # :sig:`build_finished`. A fresh ``set_items`` clears it (a
        # stale anchor from the prior contents shouldn't fire on the
        # new ones).
        self._pending_anchor_payload: object = None
        self._pending_anchor_select: bool = True

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

        # spec/131 — auto-apply any pending anchor when the chunked
        # builder finishes. The host queues the request via
        # ``ensure_item_visible`` right after ``set_items``; the cell
        # usually isn't built yet on a 200-cell day, so we wait.
        self.build_finished.connect(self._apply_pending_anchor)

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
        # spec/131 — drop any anchor stashed from the previous contents.
        # The host re-asserts the anchor for the new set after this call.
        self._pending_anchor_payload = None
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
        else:
            # spec/131 — small set built entirely synchronously; signal
            # build completion on the next event-loop turn so any anchor
            # ``ensure_item_visible`` call queued by the host (which
            # typically runs RIGHT AFTER ``set_items``) still gets the
            # deferred-application path. singleShot(0) keeps the order
            # deterministic + matches the chunked builder's tail. The
            # token guard + try/except guard against the Qt-zombie case
            # — a widget destroyed before the timer fires.
            QTimer.singleShot(
                0, lambda t=token: self._safe_emit_build_finished(t))

    def _safe_emit_build_finished(self, token: int) -> None:
        """spec/131 — emit ``build_finished`` only when this timer's
        token still matches the current build (a fresh ``set_items``
        bumps the token, dropping stale ticks). Swallows the
        ``RuntimeError`` raised when the widget's C++ side was
        destroyed before the deferred tick fired (Qt-zombie case the
        existing ``set_pixmap`` / ``update_item`` guards already
        cover)."""
        try:
            if token != self._build_token:
                return
            self.build_finished.emit()
        except RuntimeError:
            log.debug(
                "ThumbGrid: build_finished emit dropped — widget gone",
                exc_info=True)

    def update_item(self, index: int, item: ThumbGridItem) -> None:
        """Replace one cell's data. Works for both built and pending
        cells — pending cells pick up the new data when the chunked
        builder reaches them.

        Guarded against the Qt zombie case (Nelson 2026-06-19): an
        async caller may land after the host has rebuilt the grid
        (set_items → new cells), in which case the cell list still
        carries a Python wrapper for a now-deleted C++ widget.
        Touching it raises ``RuntimeError`` — log + drop.
        """
        if not (0 <= index < len(self._items)):
            return
        self._items[index] = item
        if index < len(self._cells):
            try:
                self._cells[index].apply_item(item)
            except RuntimeError:
                log.debug(
                    "ThumbGrid.update_item: cell %d already deleted",
                    index, exc_info=True)

    def set_pixmap(self, index: int, pixmap: Optional[QPixmap]) -> None:
        """Convenience: refresh just the pixmap on one cell. The async
        thumb loaders use this when a decode lands.

        Guarded against the Qt zombie case (same as
        :meth:`update_item`) — the cell may have been deleted before
        the async decode landed, in which case touching it raises
        ``RuntimeError: wrapped C/C++ object … has been deleted``.
        Log + drop the late pixmap.
        """
        if not (0 <= index < len(self._items)):
            return
        prev = self._items[index]
        # Mutate the stored item so re-builds pick up the new pixmap.
        prev.pixmap = pixmap
        if index < len(self._cells):
            try:
                self._cells[index].setPixmap(pixmap)
            except RuntimeError:
                log.debug(
                    "ThumbGrid.set_pixmap: cell %d already deleted",
                    index, exc_info=True)

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

    # ── spec/131 — restore-anchor helpers ─────────────────────────────

    def index_of_payload(self, payload: object) -> Optional[int]:
        """Return the index of the first item whose ``payload`` matches,
        or ``None`` when no item carries that payload. Linear scan; the
        host calls this once per restore, so the cost is fine even on a
        200-cell day."""
        for i, item in enumerate(self._items):
            if item.payload == payload:
                return i
        return None

    def ensure_item_visible(
        self, payload: object, *, select: bool = True,
    ) -> bool:
        """spec/131 — scroll the inner viewport so the cell whose item
        carries ``payload`` is visible. When ``select=True`` (the
        default) the cell gets keyboard focus too, so the locked §63
        keys land on it.

        When the target cell hasn't been built yet (chunked construction
        still pending), the request is **stashed** and applied on the
        next :sig:`build_finished` signal — so the host can call this
        immediately after ``set_items`` without timing the chunked
        builder. Returns ``True`` when the request was applied OR
        deferred; ``False`` when no item carries ``payload`` (the
        request is dropped).

        Returns False on graceful misses (anchor for an item not on this
        page) so the host doesn't loop forever."""
        idx = self.index_of_payload(payload)
        if idx is None:
            # Drop any prior pending anchor too — a missed restore
            # request shouldn't quietly resurrect on the next build.
            self._pending_anchor_payload = None
            return False
        if 0 <= idx < len(self._cells):
            self._scroll_to_cell(self._cells[idx], select=select)
            self._pending_anchor_payload = None
            return True
        # Cell not built yet — queue.
        self._pending_anchor_payload = payload
        self._pending_anchor_select = bool(select)
        return True

    def select_item(self, payload: object) -> bool:
        """Convenience alias — same as :meth:`ensure_item_visible` with
        ``select=True``. Named for the spec/131 reader so the intent
        ("highlight this item") is clear at the call site."""
        return self.ensure_item_visible(payload, select=True)

    def _scroll_to_cell(self, cell: "_GridCell", *, select: bool) -> None:
        """Scroll the cell into view; optionally give it focus so the
        cell paints its focus ring + the locked §63 keys target it.
        Resilient against the Qt zombie case (cell already deleted) —
        log + drop."""
        try:
            self._scroll.ensureWidgetVisible(cell)
            if select:
                cell.setFocus(Qt.FocusReason.OtherFocusReason)
        except RuntimeError:
            log.debug(
                "ThumbGrid._scroll_to_cell: cell already deleted",
                exc_info=True)

    def _apply_pending_anchor(self) -> None:
        """Build-finished hook — applies any anchor the host queued
        before the chunked builder reached the target cell."""
        payload = self._pending_anchor_payload
        if payload is None:
            return
        select = self._pending_anchor_select
        self._pending_anchor_payload = None
        self.ensure_item_visible(payload, select=select)

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
        else:
            # spec/131 — the chunked build has reached every cell.
            # Defer the signal by one tick so the layout settles before
            # ensure_item_visible runs (otherwise the QScrollArea's
            # geometry can still be mid-relayout and the scroll lands
            # short). Token-guarded against the Qt-zombie case.
            QTimer.singleShot(
                0, lambda t=token: self._safe_emit_build_finished(t))

    # ── keyboard ──────────────────────────────────────────────────────

    def keyPressEvent(self, ev: QKeyEvent) -> None:  # noqa: N802 — Qt
        if ev.key() == Qt.Key.Key_Escape:
            self.back_requested.emit()
            ev.accept()
            return
        super().keyPressEvent(ev)


__all__ = ["ThumbGrid", "ThumbGridItem", "DEFAULT_CELL_SIZE"]
