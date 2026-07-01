"""The #exported DC detail surface — flat grid of every shipped
file, with multi-select + Delete affordance (spec/61 §1.4).

Spec/81 §2 vocabulary: ``#exported`` is the event's **base Dynamic
Collection** (the universe every Cut starts from). This surface is
the drill-down for THAT DC — it is what the user opens to manage
the files in it (delete a stale export, etc.). User DCs (recipes
saved via Save as DC…) get a different surface (the New Cut dialog
re-prefilled, not a detail page) since they're recipes, not stored
files.

Reused machinery:

* :class:`~mira.ui.design.ThumbGrid` — the shared flat show-order
  grid the Cut detail uses; the ``#exported`` DC's resolution is
  everything under ``Exported Media/`` (spec/61 §1.1, spec/81 §2).
* :meth:`~mira.gateway.event_gateway.EventGateway.
  delete_exported_file_by_relpath` — drops one lineage row + its
  on-disk file; ``cut_member.export_relpath`` FK CASCADE handles
  membership cleanup automatically (spec/61 §1.4 + schema FK).
* :class:`DCUnexportSnapshot` — mirror of the Days-Grid
  ``_UnexportSnapshot`` for the Ctrl+Z restore of a single quick
  delete.

Selection grammar (Nelson 2026-06-15 — "deliberate management
action, separate from the Export-phase ship toggle"):

* Click a cell → toggles its membership in the deletion set; the
  cell's border swaps to red (``CellColor.DISCARDED``) so the user
  reads "marked for deletion" at a glance.
* Del / Backspace OR the toolbar "Delete N selected" button: if
  exactly one cell is marked → quick delete with Ctrl+Z undo; if
  more → confirm dialog stating the file count + the cut count
  the cascade will hit, no undo (the confirm IS the safety).

Refresh contract: every successful delete re-resolves
``exported_files()`` and rebuilds the grid; cut_member rows for
the deleted relpaths drop via the FK CASCADE — open Cut sessions
must re-read their ledger on entry.

Charter pin: only ``Exported Media/`` files come into the
deletion set. ``Original Media/`` is never reachable from this
surface.

History: spec/81 renamed "pool" → DC (Dynamic Collection). The QSS
object names (``#PoolDetailPage``, ``#PoolCountLabel``) keep their
old identifiers since they're widely referenced in
``assets/themes/redesign.qss``; the visual treatment is unchanged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from core.export_provenance import lineage_origin_label
from core.video_discovery import VIDEO_EXTENSIONS
from mira.store import models as m
from mira.ui.design import (
    ThumbGrid,
    ThumbGridItem,
    danger_ghost_button,
    ghost_button,
)
from mira.ui.i18n import tr

log = logging.getLogger(__name__)

_CELL_PX = 220
_GRID_THUMB_TARGET = QSize(_CELL_PX, _CELL_PX)
_CELL_SIZE = QSize(_CELL_PX, _CELL_PX)

#: Cap on the Ctrl+Z stack — same bound the Days-Grid uses. Each
#: entry holds the deleted file's bytes in memory, so ~16 × ~5 MB
#: = ~80 MB ceiling.
_UNDO_MAX = 16

#: spec/159 — center-click placeholder until Session B lands the
#: review-mode editor. Set to True to log the would-be open; False
#: makes it a silent no-op.
_LOG_CENTER_CLICK_PLACEHOLDER = True


@dataclass
class _GridCell:
    """One slot in the rendered grid.

    Single-version (``kind='flat'``) cells carry one lineage row;
    versions-cluster (``kind='cluster'``) covers carry every row that
    shares a ``source_item_id``. The virtual "Mira render is intended
    but no file on disk yet" version (``kind='mira_pending'``) only
    appears inside a drilled-in cluster — it adds to the cluster
    threshold so a single LRC return + a Mira intent fold into a
    cluster (spec/89 §6, mirrored from
    :meth:`mira.ui.pages.days_grid_page._versions_cluster_grid_item`).
    """

    kind: str                                  # 'flat' | 'cluster' | 'mira_pending'
    cover_relpath: str                         # the pixmap key + payload
    rows: List[m.Lineage] = field(default_factory=list)
    source_item_id: Optional[str] = None       # cluster covers + pending
    virtual_mira: bool = False                 # cluster covers: include virtual Mira member?


@dataclass
class DCUnexportSnapshot:
    """One reversible #exported-DC quick-delete (single cell). Holds
    the on-disk file's bytes + the lineage row so Ctrl+Z restores
    both. Cut membership stays gone — the user has to re-add the
    file from this surface after restoring (matches the Days-Grid
    pattern; the Cut cascade is one-way for explicit deletes)."""

    export_relpath: str
    file_bytes: bytes
    dest_path: Path
    lineage_row: object        # m.Lineage — typed loosely (import cycle)


def _kind_for(relpath: str) -> str:
    """``"video"`` when the relpath ends in a known video extension;
    ``"photo"`` otherwise. Kept on the legacy contract for callers
    that snapshot the deletion set with kind info."""
    if Path(relpath).suffix.lower() in VIDEO_EXTENSIONS:
        return "video"
    return "photo"


class DCDetailPage(QWidget):
    """The #exported DC drill-down (spec/81 §2 + spec/159).

    Spec/159 promoted this surface from "click anywhere to mark for
    deletion" to a real review-and-classify grid:

    * Selection state is **persistent** — the per-version ``to_delete``
      column on ``lineage`` (added in schema v23) carries the mark
      across sessions. The toolbar's "⌫ Delete N marked…" action
      commits via :meth:`EventGateway.delete_marked_exported_files`.
    * Click grammar is **two-zone** — border-click toggles
      ``to_delete`` on a single-version cell; center-click opens the
      review-mode editor (placeholder until Session B lands it).
      Cluster covers open the cluster on any click (existing
      cluster behaviour).
    * Each cell shows the spec/159 review chrome — star chip, colour
      label, portfolio flag, "Marked for deletion" badge — read from
      lineage. Writing the rating fields happens in the editor
      (Session B); this surface only renders them + handles
      to_delete.

    Spec/159 §11 calls Ctrl+Z out as scoped to the editor's rating
    history, not the to_delete flag — the batched delete confirm IS
    the safety. The legacy single-cell quick-delete path is retired.
    """

    back_requested = pyqtSignal()
    files_deleted = pyqtSignal(set)         # set[export_relpath]
    #: spec/159 — emitted when center-click opens a cell for review.
    #: Carries the export_relpath. Until Session B wires the editor,
    #: callers may ignore this signal (the surface logs the would-be
    #: open and stays put).
    review_requested = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # QSS object name kept for back-compat (assets/themes/redesign.qss
        # rules attach to ``#PoolDetailPage``).
        self.setObjectName("PoolDetailPage")
        self._eg = None
        self._root: Optional[Path] = None
        self._files: List[m.Lineage] = []
        self._thumbs: Dict[str, QPixmap] = {}
        self._undo_stack: list[DCUnexportSnapshot] = []
        # spec/159 §4.4 — drill-in state. ``flat`` shows every shipped
        # file with versions of one item folded into a cluster cover;
        # ``cluster`` shows just the versions of one source item.
        self._mode: str = "flat"
        self._cluster_item_id: Optional[str] = None
        # spec/89 §6 / spec/159 §4.4 — source items whose adjustment
        # row carries a non-default look / filter / crop / rotation
        # count as a virtual "Mira render" ship intent even when no
        # JPEG has been materialised under ``Exported Media/`` yet.
        # Refreshed in :meth:`_refresh` so the cluster math reflects
        # gateway writes the user just made.
        self._mira_intent_ids: set = set()
        # spec/159 §6+ — source items whose virtual-Mira intent is
        # currently marked preferred (item.preferred_virtual_mira).
        # Refreshed in :meth:`_refresh`. Drives the cluster-cover
        # "✓ Mira" chip + the virtual tile's "✓ Use this" toggle.
        self._virtual_mira_preferred_ids: set = set()
        # spec/159 §6 — Compare-mark set (session-local). Keys are
        # cell payload strings: ``export_relpath`` for flat cells.
        # Cleared on mode change so a flat-mode marking doesn't bleed
        # into the cluster sub-grid (where the Compare button targets
        # ALL members by design).
        self._compare_marked: set = set()

        from mira.ui.media.photo_cache import photo_cache
        self._cache = photo_cache()
        self._cache.scaled_pixmap_ready.connect(self._on_thumb_ready)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Flush full-width pink rail (Share state) at the very top, like the
        # other surfaces. Back lives in the shared title bar — routed here by
        # ShareCutsPage.on_titlebar_back (Nelson 2026-06-21).
        self._rail = QFrame()
        self._rail.setObjectName("SurfaceHeaderRail")
        self._rail.setProperty("phase", "share")
        self._rail.setFixedHeight(2)
        outer.addWidget(self._rail)

        head = QHBoxLayout()
        head.setContentsMargins(12, 8, 12, 4)
        self._tag_lbl = QLabel("#exported")
        self._tag_lbl.setObjectName("PoolCountLabel")
        head.addWidget(self._tag_lbl)
        # Nelson 2026-06-30 — the legacy "{n} exported file(s)" tag
        # next to ``#exported`` rendered without a separator ("exported
        # 500 exported file(s)") and duplicated information the new
        # FilterBar's "Showing N of M" indicator carries. Dropped.
        head.addStretch(1)
        # spec/159 — "Delete N marked…" primary action. Visible only
        # when at least one lineage row carries ``to_delete = 1``;
        # opens a confirm dialog naming the file + cut-cascade count.
        self._delete_btn = danger_ghost_button(tr("⌫ Delete marked…"))
        self._delete_btn.setVisible(False)
        self._delete_btn.clicked.connect(self._on_delete_clicked)
        head.addWidget(self._delete_btn)
        # spec/159 — "Clear all marks" releases the to_delete flag on
        # every visible row in one transaction. Visible alongside the
        # delete button when there's anything to clear.
        self._clear_btn = ghost_button(tr("Clear marks"))
        self._clear_btn.setVisible(False)
        self._clear_btn.clicked.connect(self._clear_marks)
        head.addWidget(self._clear_btn)
        # spec/159 §6 — Compare button reuse. Hidden until either
        # (a) flat mode: ≥2 cells are Compare-marked via the C key, or
        # (b) cluster sub-grid: always shown so the user can open every
        # version of one shot side-by-side. Hooked up here so the
        # toolbar row reads:  [⌫ Delete N…] [Clear marks] [⇄ Compare]
        self._compare_btn = ghost_button(tr("⇄ Compare"))
        self._compare_btn.setVisible(False)
        self._compare_btn.clicked.connect(self._on_compare_clicked)
        head.addWidget(self._compare_btn)
        outer.addLayout(head)

        # spec/159 §4.5 (Nelson 2026-06-30 eyeball pivot) — the filter
        # is a group-box bar between the title row and the section
        # header, replacing the previous QToolButton + popup menu
        # (which read as non-standard, especially the menu checkmarks
        # next to colour names instead of colour swatches). Reusable
        # widget; the host owns the LineageFilter state.
        from mira.ui.exported.filter_bar import FilterBar
        from mira.ui.exported.filter_popup import LineageFilter
        self._filter: LineageFilter = LineageFilter()
        self._filter_bar = FilterBar(self)
        self._filter_bar.filter_changed.connect(self._on_filter_changed)
        outer.addWidget(self._filter_bar)

        # ── Grid chrome (Back + header) ──────────────────────────────
        chrome = QHBoxLayout()
        chrome.setContentsMargins(12, 4, 12, 8)
        chrome.setSpacing(12)
        # Back is in the shared title bar now (routed via
        # ShareCutsPage.on_titlebar_back). The grid's own back_requested
        # (Esc / edge) still fires the same signal.
        self._header_lbl = QLabel(tr("#exported — the base Collection"))
        self._header_lbl.setObjectName("DayGridHeader")
        chrome.addWidget(self._header_lbl, stretch=1)
        outer.addLayout(chrome)

        # spec/159 — every click on a single-version cell opens the
        # review viewer. Earlier in spec/159's life the border zone
        # toggled the ``to_delete`` flag in-place, but accidental
        # marks were too easy (Nelson 2026-06-30 — "anyone can
        # accidentally mark a photo for deletion by clicking on the
        # border"); marking now happens only inside the dialog (D key
        # / DeleteToggle) or via the toolbar batch action. Cluster
        # covers keep their existing "any click opens the cluster"
        # behaviour.
        self._grid = ThumbGrid(cell_size=_CELL_SIZE, two_zone_clicks=True)
        self._grid.cell_border_clicked.connect(self._on_cell_activated)
        self._grid.cell_activated.connect(self._on_cell_activated)
        # In cluster mode the grid's back_requested pops the cluster
        # first; only the flat mode's back propagates to the host.
        self._grid.back_requested.connect(self._on_grid_back_requested)
        outer.addWidget(self._grid, stretch=1)

        # Keyboard: Del / Backspace = commit the batch delete confirm;
        # Ctrl+Z = undo last single-cell delete; Esc = back. (spec/159
        # §5.4 scopes the per-rating undo to the editor's history; the
        # surface keeps the legacy Ctrl+Z for the rare single-cell
        # quick delete the legacy flow used to support — Session B
        # may retire it.)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self,
                  activated=self._on_delete_clicked)
        QShortcut(QKeySequence(Qt.Key.Key_Backspace), self,
                  activated=self._on_delete_clicked)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self._on_undo)
        # spec/159 §6 / spec/63 §4 — C key toggles Compare-mark on the
        # focused cell. The grid's flat cells are made ``focusable``
        # so the locked §63 keymap can target them; cluster covers +
        # virtual mira_pending cells are skipped (the Compare button
        # in cluster mode already targets every member).
        QShortcut(QKeySequence(Qt.Key.Key_C), self,
                  activated=self._toggle_compare_on_focused)

    # ── lifecycle ─────────────────────────────────────────────────────

    def open_pool(self, eg) -> None:
        """Bind to a live event gateway + render the pool."""
        self._eg = eg
        self._root = Path(eg.event_root) if eg.event_root else Path(".")
        self._cache.set_event_context(self._root, {})
        self._undo_stack.clear()
        # Always land on the flat grid — drill-in state from a prior
        # open never carries over (Nelson 2026-06-30 round 4: returning
        # to the page used to dump the user back into the same cluster
        # they last viewed).
        self._mode = "flat"
        self._cluster_item_id = None
        self._compare_marked = set()
        # spec/159 §4.6 — filter is session-local: a fresh open is a
        # fresh, unfiltered view. The bar's controls re-sync via
        # ``set_filter``; the host owns the predicate.
        from mira.ui.exported.filter_popup import LineageFilter
        self._filter = LineageFilter()
        if hasattr(self, "_filter_bar"):
            self._filter_bar.set_filter(self._filter)
        self._refresh()

    def close_event(self) -> None:
        """Drop in-memory state — called on Back. The on-disk thumb
        + proxy caches stay (they're event-scoped). spec/159 — no
        in-memory selection state to clear; the ``to_delete`` flag
        lives on lineage and persists across opens."""
        self._undo_stack.clear()
        self._eg = None
        self._root = None
        self._files = []
        self._thumbs = {}
        self._mode = "flat"
        self._cluster_item_id = None
        self._mira_intent_ids = set()
        self._compare_marked = set()
        from mira.ui.exported.filter_popup import LineageFilter
        self._filter = LineageFilter()
        if hasattr(self, "_filter_bar"):
            self._filter_bar.set_filter(self._filter)

    def on_titlebar_back(self) -> None:
        """Shared title-bar Back hook (spec/142).

        In cluster mode, the title-bar Back FIRST pops the cluster so
        the user lands back on the flat #exported grid; only when
        already on the flat grid does it propagate the host-level
        ``back_requested`` that exits the page. Without this hook the
        host's :meth:`ShareCutsPage.on_titlebar_back` falls through to
        ``back_requested.emit()`` directly and the cluster step is
        skipped (Nelson 2026-06-30 round 4)."""
        if self._mode == "cluster":
            self._close_cluster()
            return
        self.back_requested.emit()

    # ── data → cells ──────────────────────────────────────────────────

    def _refresh(self) -> None:
        if self._eg is None or self._root is None:
            return
        # Use the lenient query so the pool's set matches the Export
        # grid's "Exported" watermark exactly — both read lineage
        # without the visible_item hidden-day filter (Nelson
        # 2026-06-15 bug: pool empty while watermark showed several
        # files). Cuts logic still uses the strict ``exported_files``.
        # The query already pulls every Lineage column (the dataclass
        # round-trip carries the spec/159 stars / color_label / flag /
        # to_delete values).
        self._files = list(self._eg.exported_files_all())
        try:
            self._mira_intent_ids = set(
                self._eg.items_with_mira_intent())
        except Exception:                                      # noqa: BLE001
            log.exception(
                "DCDetailPage: items_with_mira_intent failed")
            self._mira_intent_ids = set()
        # spec/159 §6+ — fan out the virtual-Mira preferred ids in
        # bulk (one SQL hit) so the cluster paint isn't N+1.
        self._virtual_mira_preferred_ids = set()
        try:
            for src in {getattr(f, "source_item_id", None)
                        for f in self._files
                        if getattr(f, "source_item_id", None)}:
                if not src:
                    continue
                it = self._eg.item(src)
                if it is not None and bool(getattr(
                        it, "preferred_virtual_mira", False)):
                    self._virtual_mira_preferred_ids.add(src)
        except Exception:                                      # noqa: BLE001
            log.exception(
                "DCDetailPage: preferred_virtual_mira lookup failed")
        # Drop stale undo entries whose file is no longer on the
        # roster (a fresh batch delete should not re-appear in Ctrl+Z).
        live = {f.export_relpath for f in self._files}
        self._undo_stack = [
            s for s in self._undo_stack if s.export_relpath not in live]
        self._rebuild_cells()
        self._update_chrome()

    def _rebuild_cells(self) -> None:
        """Build the visible grid from ``self._files``.

        Flat mode (default): rows that share a ``source_item_id`` get
        folded into one versions-cluster cover; standalone rows pass
        through as single-version cells. Cluster mode: every cell is
        the matching source's rows, flat (the cover's drill-in view).

        Side-effects: ``self._cells`` holds the per-index mapping the
        rest of the surface dispatches through.
        """
        self._cells: List[_GridCell] = self._compute_cells()
        items: List[ThumbGridItem] = []
        for cell in self._cells:
            items.append(self._make_grid_item(cell))
        if self._mode == "cluster":
            cluster_label = self._cluster_header_label()
            self._header_lbl.setText(cluster_label)
        else:
            self._header_lbl.setText(
                tr("#exported — the base Collection"))
        self._grid.set_items(items)
        self._request_missing_thumbs()

    def _compute_cells(self) -> List[_GridCell]:
        """Roll ``self._files`` into the cell list for the active mode.

        spec/89 §6 — a source item's **ship intents** = its lineage
        rows + a virtual "Mira render" intent when the adjustment row
        is non-default AND no ``mira_render`` lineage row already
        exists. Cluster threshold = 2 ship intents.

        spec/159 §4.5 — the session-local ``self._filter`` is applied
        AFTER cluster formation: a cluster cover stays visible when
        at least one of its members passes the filter; an individual
        flat cell stays visible only when its own row passes. The
        virtual Mira-pending member follows the cluster's verdict
        (no per-row state to test against the rating filter).
        """
        if self._mode == "cluster" and self._cluster_item_id:
            rows = [f for f in self._files
                    if getattr(f, "source_item_id", None)
                    == self._cluster_item_id]
            cells: List[_GridCell] = [
                _GridCell(
                    kind="flat",
                    cover_relpath=r.export_relpath,
                    rows=[r],
                    source_item_id=getattr(r, "source_item_id", None),
                )
                for r in rows
                if self._filter.matches(r)
            ]
            if self._has_virtual_mira_intent(self._cluster_item_id, rows):
                # Virtual member rides along whenever any member of
                # the cluster passes — the user opened this sub-grid
                # to compare versions, and excluding the virtual member
                # would defeat the comparison even when the filter
                # would technically reject it.
                if cells or not self._filter.is_active():
                    cells.append(self._make_mira_pending_cell(
                        self._cluster_item_id))
            return cells

        # Flat mode — group by source_item_id (None stays ungrouped).
        groups: Dict[str, List[m.Lineage]] = {}
        order: list[str] = []                  # group keys in first-sight order
        for f in self._files:
            sid = getattr(f, "source_item_id", None)
            if not sid:
                key = f"__solo:{f.export_relpath}"
                groups.setdefault(key, []).append(f)
                order.append(key)
                continue
            key = f"src:{sid}"
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(f)
        seen = set()
        cells = []
        for key in order:
            if key in seen:
                continue
            seen.add(key)
            rows = groups[key]
            sid = (None if key.startswith("__solo:")
                   else getattr(rows[0], "source_item_id", None))
            virtual = (sid is not None
                       and self._has_virtual_mira_intent(sid, rows))
            total_intents = len(rows) + (1 if virtual else 0)
            if total_intents >= 2 and sid is not None:
                # Cluster: cover survives if ANY inner version passes
                # the filter (drill-in shows the matching subset).
                if not any(self._filter.matches(r) for r in rows):
                    continue
                cells.append(_GridCell(
                    kind="cluster",
                    cover_relpath=rows[0].export_relpath,
                    rows=rows,
                    source_item_id=sid,
                    virtual_mira=virtual,
                ))
            else:
                row = rows[0]
                if not self._filter.matches(row):
                    continue
                cells.append(_GridCell(
                    kind="flat",
                    cover_relpath=row.export_relpath,
                    rows=[row],
                    source_item_id=sid,
                ))
        return cells

    def _has_virtual_mira_intent(
        self,
        source_item_id: str,
        rows: List[m.Lineage],
    ) -> bool:
        """``True`` when this source carries a Mira intent that hasn't
        already been materialised as a lineage row. Skipping when a
        ``mira_render`` row already exists avoids double-counting (the
        materialised file represents that intent)."""
        if source_item_id not in self._mira_intent_ids:
            return False
        for r in rows:
            if getattr(r, "provenance", None) == "mira_render":
                return False
        return True

    def _make_mira_pending_cell(
        self, source_item_id: str,
    ) -> _GridCell:
        """Build the virtual "Mira render pending" cell for the drill-
        in sub-grid. Its pixmap slot points at the source item's
        ``origin_relpath`` (the Original Media file) so the cell shows
        the user what the rendering would be of. The cell is informal:
        clicking it is a no-op (there's no shipped file to review)."""
        rel = ""
        if self._eg is not None:
            try:
                item = self._eg.item(source_item_id)
            except Exception:                                  # noqa: BLE001
                item = None
            if item is not None and item.origin_relpath:
                rel = item.origin_relpath
        return _GridCell(
            kind="mira_pending",
            cover_relpath=rel,
            rows=[],
            source_item_id=source_item_id,
        )

    def _make_grid_item(self, cell: _GridCell) -> ThumbGridItem:
        """Render one ``_GridCell`` into the matching ThumbGridItem.

        Flat cells carry the lineage row's full rating chrome. Cluster
        covers paint the spec/159 §6 "no inner ratings on the cover"
        rule — no star chip, no flag, no color border, no delete
        badge — and add the ``×N`` count + the ``N/M to delete``
        sub-chip when any inner version is marked. ``mira_pending``
        cells (drill-in only) paint the source item's preview pixmap
        with a "Mira" origin wordmark so the user reads "this is what
        the Mira render WOULD look like" at a glance.
        """
        pix = self._thumbs.get(cell.cover_relpath)
        if cell.kind == "cluster":
            n_marked = sum(
                1 for r in cell.rows
                if bool(getattr(r, "to_delete", False)))
            split = ((n_marked, len(cell.rows))
                     if n_marked > 0 else None)
            count = len(cell.rows) + (1 if cell.virtual_mira else 0)
            # spec/159 §6+ — surface the cluster's preferred wordmark
            # (e.g. "✓ LRC") when one of the members is marked
            # preferred. Real lineage rows win over the virtual flag
            # because the gateway clears the virtual on any real-row
            # write — but read both so we paint the right chip even
            # if the local cache is mid-flight.
            preferred_origin: Optional[str] = None
            for r in cell.rows:
                if bool(getattr(r, "is_preferred", False)):
                    label = lineage_origin_label(
                        getattr(r, "provenance", None) or "mira_render",
                        r.export_relpath)
                    preferred_origin = label
                    break
            if (preferred_origin is None
                    and cell.source_item_id
                    in self._virtual_mira_preferred_ids):
                preferred_origin = "Mira"
            return ThumbGridItem(
                pixmap=pix,
                state=None,
                payload=("cluster", cell.source_item_id or ""),
                cluster_type="versions",
                cluster_count=count,
                to_delete_split=split,
                preferred_origin=preferred_origin,
            )
        if cell.kind == "mira_pending":
            return ThumbGridItem(
                pixmap=pix,
                state=None,
                payload=("mira_pending", cell.source_item_id or ""),
                origin="Mira",
            )
        row = cell.rows[0]
        origin = lineage_origin_label(
            getattr(row, "provenance", None) or "mira_render",
            row.export_relpath,
        )
        # Origin wordmark on the cell is only meaningful when the row
        # came from outside Mira (LRC / Helicon / CO / ext); for plain
        # Mira renders, hide it so the cell stays clean.
        if origin == "Mira":
            origin = None
        # spec/159 §6 — Compare-mark paints the orange state border.
        is_compare = row.export_relpath in self._compare_marked
        return ThumbGridItem(
            pixmap=pix,
            state="compare" if is_compare else None,
            payload=("flat", row.export_relpath),
            stars=getattr(row, "stars", None),
            color_label=getattr(row, "color_label", None),
            flag=bool(getattr(row, "flag", False)),
            to_delete=bool(getattr(row, "to_delete", False)),
            origin=origin,
            focusable=True,
            preferred=bool(getattr(row, "is_preferred", False)),
        )

    def _cluster_header_label(self) -> str:
        """Title bar text for the cluster sub-grid view."""
        item = None
        if self._eg is not None and self._cluster_item_id:
            try:
                item = self._eg.item(self._cluster_item_id)
            except Exception:                                  # noqa: BLE001
                item = None
        name = ""
        if item is not None and item.origin_relpath:
            name = Path(item.origin_relpath).name
        if name:
            return tr("Versions of {n}").replace("{n}", name)
        return tr("Versions")

    def _request_missing_thumbs(self) -> None:
        if self._root is None:
            return
        # Only fetch what the current view actually paints — cluster
        # mode shows a subset, no point pulling thumbs the user can't
        # see. Always include the cover relpaths (a flat row is its
        # own cover; a mira_pending cell's cover is the source item's
        # ``origin_relpath`` under ``Original Media/``).
        wanted = {c.cover_relpath for c in getattr(self, "_cells", [])
                  if c.cover_relpath}
        for rel in wanted:
            if rel in self._thumbs:
                continue
            self._cache.request_scaled_pixmap(
                self._root / rel, _GRID_THUMB_TARGET,
                priority=1)

    def _on_thumb_ready(self, path: Path, _pm: QPixmap, _native) -> None:
        if self._root is None:
            return
        try:
            rel = Path(path).relative_to(self._root).as_posix()
        except ValueError:
            return
        hit = self._cache.get_scaled_pixmap_if_cached(
            path, _GRID_THUMB_TARGET)
        if hit is None:
            return
        self._thumbs[rel] = hit[0]
        # Push the new pixmap onto any cell whose COVER relpath matches.
        for idx, cell in enumerate(getattr(self, "_cells", [])):
            if cell.cover_relpath == rel:
                self._grid.set_pixmap(idx, hit[0])

    # ── selection ─────────────────────────────────────────────────────

    def _on_cell_activated(self, index: int) -> None:
        """spec/159 — flat cells open the review viewer; cluster
        covers drill the surface into the cluster sub-grid; the virtual
        ``mira_pending`` cell is a no-op (the file the user would
        review hasn't been rendered yet).

        ``review_requested`` is emitted for any host that wants to
        observe the viewer open (NOT for cluster drill-in or pending
        cells)."""
        cells = getattr(self, "_cells", [])
        if self._eg is None or not (0 <= index < len(cells)):
            return
        cell = cells[index]
        if cell.kind == "cluster":
            if cell.source_item_id:
                self._open_cluster(cell.source_item_id)
            return
        if cell.kind == "mira_pending":
            return
        rel = cell.cover_relpath
        self.review_requested.emit(rel)
        self._open_review_dialog_for_cell(index)

    def _open_cluster(self, source_item_id: str) -> None:
        """spec/159 §4.4 — drill into a versions cluster. Switches the
        surface to ``cluster`` mode + re-renders the same grid showing
        only the rows of ``source_item_id``. The flat-mode Compare
        marks reset on mode change (the cluster's Compare button
        targets every member, no per-cell marking)."""
        self._mode = "cluster"
        self._cluster_item_id = source_item_id
        self._compare_marked = set()
        self._rebuild_cells()
        self._update_chrome()

    def _close_cluster(self) -> None:
        """Return from the cluster sub-grid to the flat grid."""
        if self._mode != "cluster":
            return
        self._mode = "flat"
        self._cluster_item_id = None
        self._compare_marked = set()
        self._rebuild_cells()
        self._update_chrome()

    def _on_grid_back_requested(self) -> None:
        """Esc / edge nav. In cluster mode → pop the cluster; in flat
        mode → propagate so the host (the shared title bar) returns
        to the Cut page (Nelson 2026-06-21)."""
        if self._mode == "cluster":
            self._close_cluster()
            return
        self.back_requested.emit()

    # ── spec/159 §4.5 — Filter ────────────────────────────────────────

    def _on_filter_changed(self, value) -> None:
        """Filter popup notified us of a change — store the new
        snapshot and re-render the grid."""
        self._filter = value
        self._rebuild_cells()
        self._update_chrome()

    # ── spec/159 §6 — Compare ─────────────────────────────────────────

    def _toggle_compare_on_focused(self) -> None:
        """C key entry point: flip the focused flat cell's Compare mark.

        Only flat cells participate; the Compare button inside a
        cluster sub-grid already opens every member, and cluster
        covers are tile aggregates with no per-cover state to mark."""
        if self._mode == "cluster":
            return
        cells = getattr(self, "_cells", [])
        focused = QApplication.focusWidget()
        try:
            grid_cells = self._grid.cells()
        except Exception:                                      # noqa: BLE001
            grid_cells = []
        idx: Optional[int] = None
        for i, cell_widget in enumerate(grid_cells):
            if cell_widget is focused:
                idx = i
                break
        if idx is None or not (0 <= idx < len(cells)):
            return
        cell = cells[idx]
        if cell.kind != "flat":
            return
        rel = cell.cover_relpath
        if rel in self._compare_marked:
            self._compare_marked.discard(rel)
        else:
            self._compare_marked.add(rel)
        self._rebuild_cells()
        self._update_chrome()

    def _refresh_compare_btn(self) -> None:
        """Recompute the Compare button label + visibility.

        * In cluster sub-grid mode: always visible, "⇄ Compare versions",
          opens every member side-by-side (spec/89 §11.3).
        * In flat mode: visible only when ≥2 flat cells are
          Compare-marked, labelled "⇄ Compare (N)" (spec/63 §4 follow-up).
        """
        if self._mode == "cluster":
            n = sum(
                1 for c in getattr(self, "_cells", [])
                if c.kind in ("flat", "mira_pending"))
            self._compare_btn.setText(tr("⇄ Compare versions"))
            self._compare_btn.setVisible(n >= 2)
            return
        n = len(self._compare_marked)
        if n >= 2:
            self._compare_btn.setText(
                tr("⇄ Compare ({n})").replace("{n}", str(n)))
            self._compare_btn.setVisible(True)
        else:
            self._compare_btn.setVisible(False)

    def _on_compare_clicked(self) -> None:
        """Dispatch Compare-button click: cluster sub-grid opens every
        member; flat mode opens the marked set."""
        if self._mode == "cluster":
            self._open_compare_cluster()
        else:
            self._open_compare_marked()

    def _build_compare_item(self, cell: _GridCell):
        """Build the :class:`CompareItem` for one ``_GridCell``."""
        from mira.ui.exported.compare_dialog import CompareItem

        root = self._root or Path(".")
        if cell.kind == "mira_pending":
            # Virtual member — preview the source item's bytes; the
            # develop pipeline does the rest at fit-to-tile size.
            # ``can_be_preferred=True`` (Nelson 2026-06-30 pivot —
            # users genuinely want to mark the Mira intent preferred
            # before it ships); the gateway routes through
            # :meth:`EventGateway.set_item_preferred_virtual_mira`
            # which stores the flag on the source item row.
            return CompareItem(
                item_id=f"mira:{cell.source_item_id or ''}",
                path=root / cell.cover_relpath,
                state=None,
                title=tr("Mira (pending)"),
                develop_for_preview=True,
                can_be_preferred=True,
            )
        row = cell.rows[0]
        origin = lineage_origin_label(
            getattr(row, "provenance", None) or "mira_render",
            row.export_relpath,
        )
        return CompareItem(
            item_id=row.export_relpath,
            path=root / row.export_relpath,
            state=None,
            title=origin,
        )

    def _open_compare_marked(self) -> None:
        if self._eg is None or self._root is None:
            return
        cells = [
            c for c in getattr(self, "_cells", [])
            if c.kind == "flat" and c.cover_relpath in self._compare_marked
        ]
        if len(cells) < 2:
            return
        self._open_compare_dialog(cells)

    def _open_compare_cluster(self) -> None:
        if self._eg is None or self._root is None or self._mode != "cluster":
            return
        cells = [
            c for c in getattr(self, "_cells", [])
            if c.kind in ("flat", "mira_pending")
        ]
        if len(cells) < 2:
            return
        self._open_compare_dialog(cells)

    def _open_compare_dialog(self, cells: List[_GridCell]) -> None:
        from mira.ui.exported.compare_dialog import CompareVersionsDialog

        compare_items = [self._build_compare_item(c) for c in cells]
        # ``show_titles=True`` is the spec/159 §6 contract — the
        # closed-event surface has no state grammar so the user needs
        # the per-tile provenance caption (LRC / Mira / ext) to tell
        # versions apart. ``show_use_this=True`` adds the "✓ Use this"
        # action so the user can mark the preferred version straight
        # from Compare (spec/159 §6+).
        preferred_id = self._preferred_item_id_in_cells(cells)
        dlg = CompareVersionsDialog(
            compare_items, parent=self,
            show_titles=True,
            show_use_this=True,
            preferred_item_id=preferred_id,
        )
        dlg.use_this_requested.connect(self._on_compare_use_this)
        # The closed-event surface has no Pick / Skip ledger to drive
        # from a Compare click — the dialog's intent signals stay
        # unwired by design. The user inspects + closes; ratings +
        # to_delete are handled through the review dialog instead.
        dlg.exec()
        # Repaint after close so any preferred-change reflects on the
        # cluster cover + grid cells.
        self._rebuild_cells()
        self._update_chrome()

    def _preferred_item_id_in_cells(
        self, cells: List[_GridCell],
    ) -> Optional[str]:
        """The Compare ``item_id`` of the currently-preferred member
        among these cells, or ``None``. Used to pre-light the
        Compare dialog's "Use this" toggle on open. Real lineage
        rows return their ``export_relpath``; the virtual Mira member
        returns ``mira:<source_item_id>`` so the dialog can find the
        matching tile in its build loop."""
        for c in cells:
            if c.kind == "flat" and c.rows:
                if bool(getattr(c.rows[0], "is_preferred", False)):
                    return c.rows[0].export_relpath
            elif c.kind == "mira_pending":
                if c.source_item_id in self._virtual_mira_preferred_ids:
                    return f"mira:{c.source_item_id}"
        return None

    def _on_compare_use_this(self, item_id: str) -> None:
        """spec/159 §6+ — Compare dialog asked us to write the
        preferred flag.

        ``item_id`` shape:
          * a lineage row's ``export_relpath`` — real preferred row;
          * ``mira:<source_item_id>`` — virtual Mira intent preferred
            (writes to ``item.preferred_virtual_mira``);
          * empty — clear whichever flag is currently set."""
        if self._eg is None:
            return
        if not item_id:
            # The user cleared. Hunt for whoever was preferred (real
            # row or virtual flag) and clear that.
            target_rel = None
            for f in self._files:
                if bool(getattr(f, "is_preferred", False)):
                    target_rel = f.export_relpath
                    break
            if target_rel is not None:
                self._on_review_preferred_changed(target_rel, False)
                return
            for src_id in list(self._virtual_mira_preferred_ids):
                self._set_virtual_mira_preferred(src_id, False)
            return
        if item_id.startswith("mira:"):
            src_id = item_id.split(":", 1)[1]
            if src_id:
                self._set_virtual_mira_preferred(src_id, True)
            return
        self._on_review_preferred_changed(item_id, True)

    def _set_virtual_mira_preferred(
        self, source_item_id: str, value: bool,
    ) -> None:
        """spec/159 §6+ — write the virtual-Mira preferred flag through
        the gateway + mirror it locally so the next paint reflects
        the new state without a fresh ``_refresh``."""
        if self._eg is None or not source_item_id:
            return
        try:
            self._eg.set_item_preferred_virtual_mira(
                source_item_id, value)
        except Exception:                                      # noqa: BLE001
            log.exception(
                "DCDetailPage: set_item_preferred_virtual_mira "
                "failed for %s", source_item_id)
            return
        if value:
            self._virtual_mira_preferred_ids.add(source_item_id)
            # Mirror the gateway's "clear sibling lineage" so the
            # cached rows lose their is_preferred flag too.
            for f in self._files:
                if (getattr(f, "source_item_id", None)
                        == source_item_id):
                    try:
                        f.is_preferred = False             # type: ignore[misc]
                    except AttributeError:
                        pass
        else:
            self._virtual_mira_preferred_ids.discard(
                source_item_id)

    def _open_review_dialog_for_cell(self, cell_index: int) -> None:
        """spec/159 — open the review viewer with the visible flat
        cells as nav siblings.

        Cluster covers are NOT navigable from the viewer (the cluster
        is drilled into to walk its versions). In cluster-mode views,
        every cell is flat, so ←/→ walks all that cover's versions
        in order."""
        from mira.ui.exported.review_dialog import (
            ReviewItem, ReviewMediaDialog,
        )
        cells = getattr(self, "_cells", [])
        if (self._eg is None or self._root is None
                or not (0 <= cell_index < len(cells))):
            return
        if cells[cell_index].kind != "flat":
            return
        # Flat-only sibling list — same order they appear in the grid.
        siblings = [c for c in cells if c.kind == "flat"]
        try:
            start = next(i for i, c in enumerate(siblings)
                         if c.cover_relpath
                         == cells[cell_index].cover_relpath)
        except StopIteration:
            return
        items = [self._review_item_for(c.rows[0]) for c in siblings]
        if not items:
            return
        dlg = ReviewMediaDialog(items, start_index=start, parent=self)
        dlg.stars_changed.connect(self._on_review_stars_changed)
        dlg.color_label_changed.connect(
            self._on_review_color_label_changed)
        dlg.flag_changed.connect(self._on_review_flag_changed)
        dlg.to_delete_changed.connect(
            self._on_review_to_delete_changed)
        dlg.classification_changed.connect(
            self._on_review_classification_changed)
        dlg.preferred_changed.connect(self._on_review_preferred_changed)
        dlg.exec()
        # On close: rebuild the cells so any rating changes paint.
        self._rebuild_cells()
        self._update_chrome()

    def _review_item_for(self, row):
        """Build a ReviewItem from a Lineage row (with the source item's
        classification eagerly resolved so the Style picker reads the
        current value without a round-trip)."""
        from mira.ui.exported.review_dialog import ReviewItem

        item_id = getattr(row, "source_item_id", None)
        classification: Optional[str] = None
        if item_id and self._eg is not None:
            try:
                src = self._eg.item(item_id)
            except Exception:                                  # noqa: BLE001
                log.exception(
                    "DCDetailPage: item lookup failed for %s", item_id)
                src = None
            if src is not None:
                classification = getattr(src, "classification", None)
        # spec/159 §6+ — does this row have any sibling lineage row
        # for the same source? Drives the PreferredToggle's visibility.
        has_siblings = False
        if item_id:
            has_siblings = sum(
                1 for f in self._files
                if getattr(f, "source_item_id", None) == item_id
            ) >= 2
            # Virtual Mira intent also counts as a sibling (spec/89 §6)
            # so the user can mark the LRC return preferred even before
            # the Mira render materialises.
            if (not has_siblings
                    and item_id in self._mira_intent_ids
                    and not any(
                        getattr(f, "provenance", None) == "mira_render"
                        for f in self._files
                        if getattr(f, "source_item_id", None) == item_id)):
                has_siblings = True
        return ReviewItem(
            export_relpath=row.export_relpath,
            abs_path=(self._root or Path(".")) / row.export_relpath,
            stars=getattr(row, "stars", None),
            color_label=getattr(row, "color_label", None),
            flag=bool(getattr(row, "flag", False)),
            to_delete=bool(getattr(row, "to_delete", False)),
            title=Path(row.export_relpath).name,
            item_id=item_id,
            classification=classification,
            is_preferred=bool(getattr(row, "is_preferred", False)),
            has_siblings=has_siblings,
        )

    def _find_file_index(self, rel: str) -> Optional[int]:
        for i, f in enumerate(self._files):
            if f.export_relpath == rel:
                return i
        return None

    def _on_review_stars_changed(self, rel: str, value) -> None:
        if self._eg is None:
            return
        try:
            self._eg.set_lineage_stars(rel, value)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "DCDetailPage: set_lineage_stars failed for %s", rel)
            return
        idx = self._find_file_index(rel)
        if idx is not None:
            try:
                self._files[idx].stars = value                     # type: ignore[misc]
            except AttributeError:
                pass

    def _on_review_color_label_changed(self, rel: str, value) -> None:
        if self._eg is None:
            return
        try:
            self._eg.set_lineage_color_label(rel, value)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "DCDetailPage: set_lineage_color_label failed for %s", rel)
            return
        idx = self._find_file_index(rel)
        if idx is not None:
            try:
                self._files[idx].color_label = value               # type: ignore[misc]
            except AttributeError:
                pass

    def _on_review_flag_changed(self, rel: str, value: bool) -> None:
        if self._eg is None:
            return
        try:
            self._eg.set_lineage_flag(rel, value)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "DCDetailPage: set_lineage_flag failed for %s", rel)
            return
        idx = self._find_file_index(rel)
        if idx is not None:
            try:
                self._files[idx].flag = value                      # type: ignore[misc]
            except AttributeError:
                pass

    def _on_review_to_delete_changed(self, rel: str, value: bool) -> None:
        if self._eg is None:
            return
        try:
            self._eg.set_lineage_to_delete(rel, value)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "DCDetailPage: set_lineage_to_delete failed for %s", rel)
            return
        idx = self._find_file_index(rel)
        if idx is not None:
            try:
                self._files[idx].to_delete = value                 # type: ignore[misc]
            except AttributeError:
                pass

    def _on_review_preferred_changed(
        self, rel: str, value: bool,
    ) -> None:
        """spec/159 §6+ — write the preferred flag through the gateway
        (which clears any sibling row's flag first), then mirror the
        new state onto the cached lineage rows so the grid repaints
        the right chip after the dialog closes."""
        if self._eg is None:
            return
        try:
            self._eg.set_lineage_preferred(rel, value)
        except Exception:                                      # noqa: BLE001
            log.exception(
                "DCDetailPage: set_lineage_preferred failed for %s",
                rel)
            return
        # Find this row's source_item_id so we mirror the sibling
        # clear locally too.
        src_id: Optional[str] = None
        for f in self._files:
            if f.export_relpath == rel:
                src_id = getattr(f, "source_item_id", None)
                try:
                    f.is_preferred = bool(value)               # type: ignore[misc]
                except AttributeError:
                    pass
        if value and src_id:
            for f in self._files:
                if (getattr(f, "source_item_id", None) == src_id
                        and f.export_relpath != rel):
                    try:
                        f.is_preferred = False                 # type: ignore[misc]
                    except AttributeError:
                        pass
            # Setting a real preferred row clears the virtual flag on
            # the source — keep the local cache in sync with the
            # gateway's mutual-exclusion write (spec/159 §6+).
            self._virtual_mira_preferred_ids.discard(src_id)

    def _on_review_classification_changed(
        self, item_id: str, value: str,
    ) -> None:
        """spec/159 §2.2 / §5.3 — Style is per-source-item; a single
        ``set_classification`` call covers every version. ``source =
        'user'`` because the action came from the review dialog
        (mirrors the Editor's classification capture path)."""
        if self._eg is None or not item_id:
            return
        try:
            self._eg.set_classification(item_id, value, source="user")
        except Exception:                                          # noqa: BLE001
            log.exception(
                "DCDetailPage: set_classification failed for %s",
                item_id)
            return

    def _marked_relpaths(self) -> List[str]:
        """Every visible lineage row whose ``to_delete = 1``. Drives
        the toolbar count + the "Delete N marked…" confirm dialog.
        Reads off the cached lineage rows so we don't re-query mid
        click sequence."""
        return [
            f.export_relpath for f in self._files
            if bool(getattr(f, "to_delete", False))
        ]

    def _clear_marks(self) -> None:
        """spec/159 — release the ``to_delete`` flag on every visible
        lineage row whose flag is set, in one logical operation."""
        if self._eg is None:
            return
        marked = self._marked_relpaths()
        if not marked:
            return
        for rel in marked:
            try:
                self._eg.set_lineage_to_delete(rel, False)
            except Exception:                                      # noqa: BLE001
                log.exception(
                    "DCDetailPage: clear set_lineage_to_delete failed "
                    "for %s", rel)
        # Reflect the clear locally so the rebuild paints clean
        # without re-querying the gateway.
        for f in self._files:
            try:
                f.to_delete = False                                # type: ignore[misc]
            except AttributeError:
                pass
        self._rebuild_cells()
        self._update_chrome()

    def _update_chrome(self) -> None:
        n_files = len(self._files)
        n_marked = len(self._marked_relpaths())
        # Filter-bar "Showing N of M" indicator — paints the visible
        # cell count (one cell per cluster cover OR per flat row) over
        # the total lineage row count. Falls back to the bare row
        # count when the filter is inactive (matches no-filter UX).
        n_shown_rows = self._visible_lineage_row_count()
        if hasattr(self, "_filter_bar"):
            self._filter_bar.setRenderedCount(n_shown_rows, n_files)
        self._delete_btn.setVisible(n_marked > 0)
        self._clear_btn.setVisible(n_marked > 0)
        if n_marked > 0:
            self._delete_btn.setText(
                tr("⌫ Delete {n} marked…").replace("{n}", str(n_marked)))
        self._refresh_compare_btn()

    def _visible_lineage_row_count(self) -> int:
        """How many real lineage rows the current cell list represents.

        A cluster cover counts every member (the cells the user could
        drill into); a flat cell counts its own row; ``mira_pending``
        cells contribute nothing (they aren't lineage rows). Used by
        the FilterBar's "Showing N of M" indicator."""
        n = 0
        for c in getattr(self, "_cells", []):
            if c.kind == "cluster":
                n += len(c.rows)
            elif c.kind == "flat":
                n += 1
        return n

    # ── delete ────────────────────────────────────────────────────────

    def _on_delete_clicked(self) -> None:
        """spec/159 — open the confirm dialog naming the marked count +
        the cut-cascade reach, then commit via
        :meth:`EventGateway.delete_marked_exported_files`. Every
        commit path goes through here (Del / Backspace / button) —
        the legacy single-cell quick-delete + Ctrl+Z restore stays
        wired against the per-row capture path for any future caller
        but is no longer reachable from this surface's gestures."""
        if self._eg is None or self._root is None:
            return
        marked = self._marked_relpaths()
        if not marked:
            return
        self._delete_batch_with_confirm(marked)

    def _capture_snapshot(self, relpath: str) -> Optional[DCUnexportSnapshot]:
        """Capture file bytes + lineage row BEFORE the delete so
        Ctrl+Z can restore both. Returns ``None`` if the file is
        missing on disk or the lineage row is gone (defensive — the
        delete will no-op too)."""
        if self._root is None:
            return None
        dest = self._root / relpath
        try:
            file_bytes = dest.read_bytes()
        except OSError:
            log.warning(
                "pool delete: cannot read %s for undo (skipping snapshot)",
                dest, exc_info=True)
            file_bytes = b""
        # Find the lineage row for this relpath.
        try:
            rows = self._eg.store.conn.execute(
                "SELECT * FROM lineage WHERE export_relpath = ?",
                (relpath,)).fetchall()
        except Exception:                                          # noqa: BLE001
            log.exception("pool delete: lineage query failed for %s", relpath)
            return None
        if not rows:
            return None
        row = rows[0]
        lineage = m.Lineage(
            export_relpath=row["export_relpath"],
            phase=row["phase"],
            source_kind=row["source_kind"],
            source_item_id=row["source_item_id"],
            source_bracket_id=row["source_bracket_id"],
            recipe_json=row["recipe_json"],
            exported_at=row["exported_at"],
            # spec/89 §1.4 — preserve origin signal on undo round-trip
            # so a restored third-party row doesn't silently revert to
            # 'mira_render' (the dataclass default).
            provenance=row["provenance"],
            # spec/89 Slice 5 — preserve per-version intent so an undo
            # restores the cluster member to the state it was in.
            intent_state=row["intent_state"],
        )
        return DCUnexportSnapshot(
            export_relpath=relpath,
            file_bytes=file_bytes,
            dest_path=dest,
            lineage_row=lineage,
        )

    def _push_undo(self, snap: DCUnexportSnapshot) -> None:
        self._undo_stack.append(snap)
        if len(self._undo_stack) > _UNDO_MAX:
            self._undo_stack.pop(0)

    def _delete_one_quick(self, relpath: str) -> None:
        """Single-cell quick delete: snapshot → delete → push undo.
        No confirm — the gesture is the commit; Ctrl+Z is the safety
        net (Nelson 2026-06-15: 'individual delete stays quick')."""
        snap = self._capture_snapshot(relpath)
        try:
            self._eg.delete_exported_file_by_relpath(relpath)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "pool delete: delete_exported_file_by_relpath failed for %s",
                relpath)
            return
        if snap is not None:
            self._push_undo(snap)
        # spec/159 — no in-memory selection to discard; the lineage
        # row is gone now, so the next refresh drops the cell anyway.
        self._refresh()
        self.files_deleted.emit({relpath})

    def _delete_batch_with_confirm(self, relpaths: List[str]) -> None:
        """Cascade-aware batch confirm: count files + the unique Cuts
        the cascade will touch + reassure that originals/edits are
        untouched. Batch delete is gated by the confirm — there is no
        Ctrl+Z (the user has just acknowledged the blast). Nelson
        2026-06-15: 'batch is gated by the confirm rather than undo'."""
        try:
            cuts = self._eg.cuts_containing_any(relpaths)
        except Exception:                                          # noqa: BLE001
            log.exception("pool delete: cuts_containing_any failed")
            cuts = []
        n_files = len(relpaths)
        n_cuts = len(cuts)
        title = tr("Delete {n} exported file(s)?").replace(
            "{n}", str(n_files))
        if n_cuts == 0:
            body = tr(
                "These {n} file(s) will be removed from Exported "
                "Media/. They aren't in any Cut. Originals and "
                "edits are untouched — these can be re-exported."
            ).replace("{n}", str(n_files))
        else:
            body = tr(
                "These {n} file(s) will be removed from Exported "
                "Media/ AND from {c} Cut(s) that reference them. "
                "Originals and edits are untouched — these can be "
                "re-exported."
            ).replace("{n}", str(n_files)).replace("{c}", str(n_cuts))
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setText(body)
        delete_btn = box.addButton(
            tr("Delete {n}").replace("{n}", str(n_files)),
            QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not delete_btn:
            return
        # spec/159 — route through the gateway's batch helper so the
        # cascade (file unlink + lineage drop + edit_exported flip
        # + cut_member cleanup) stays in one well-tested code path.
        # The helper reads ``to_delete = 1`` itself so we don't need
        # to thread the relpath list through.
        deleted_before = set(relpaths)
        try:
            n_deleted = self._eg.delete_marked_exported_files()
        except Exception:                                          # noqa: BLE001
            log.exception(
                "DCDetailPage: delete_marked_exported_files failed")
            return
        self._undo_stack.clear()           # batch is non-undoable
        self._refresh()
        # Surviving rows = rows that still appear after the refresh.
        surviving = {f.export_relpath for f in self._files}
        deleted_relpaths = {r for r in deleted_before
                            if r not in surviving}
        if deleted_relpaths or n_deleted:
            self.files_deleted.emit(deleted_relpaths)

    # ── undo ──────────────────────────────────────────────────────────

    def _on_undo(self) -> None:
        if self._eg is None or self._root is None:
            return
        if not self._undo_stack:
            return
        snap = self._undo_stack.pop()
        try:
            snap.dest_path.parent.mkdir(parents=True, exist_ok=True)
            if snap.file_bytes:
                snap.dest_path.write_bytes(snap.file_bytes)
        except OSError:
            log.exception(
                "pool undo: restore file write failed for %s",
                snap.dest_path)
        try:
            self._eg.record_lineage(snap.lineage_row)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "pool undo: record_lineage failed for %s",
                snap.export_relpath)
        # Re-flip edit_exported for the source item (the
        # by-relpath delete cleared it only when no other rows
        # survived; the restore re-adds the row so the flag should
        # be on again).
        item_id = getattr(snap.lineage_row, "source_item_id", None)
        if item_id:
            try:
                self._eg.set_edit_exported(item_id, True)
            except Exception:                                      # noqa: BLE001
                log.exception(
                    "pool undo: set_edit_exported(True) failed for %s",
                    item_id)
        self._refresh()


__all__ = ["DCDetailPage", "DCUnexportSnapshot"]
