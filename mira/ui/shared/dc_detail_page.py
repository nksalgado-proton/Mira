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
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

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
    """The #exported DC drill-down (spec/81 §2)."""

    back_requested = pyqtSignal()
    files_deleted = pyqtSignal(set)         # set[export_relpath]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # QSS object name kept for back-compat (assets/themes/redesign.qss
        # rules attach to ``#PoolDetailPage``).
        self.setObjectName("PoolDetailPage")
        self._eg = None
        self._root: Optional[Path] = None
        self._files: List[m.Lineage] = []
        self._thumbs: Dict[str, QPixmap] = {}
        self._selected: set[str] = set()
        self._undo_stack: list[DCUnexportSnapshot] = []

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
        self._meta_lbl = QLabel("")
        self._meta_lbl.setObjectName("PageHint")
        head.addWidget(self._meta_lbl, stretch=1)
        # The Delete affordance — hidden when nothing is marked. Uses
        # the design system's danger button so the verb's blast reads
        # at a glance (this is the only place in the pool surface that
        # touches on-disk files).
        self._delete_btn = danger_ghost_button(tr("Delete"))
        self._delete_btn.setVisible(False)
        self._delete_btn.clicked.connect(self._on_delete_clicked)
        head.addWidget(self._delete_btn)
        self._clear_btn = ghost_button(tr("Clear selection"))
        self._clear_btn.setVisible(False)
        self._clear_btn.clicked.connect(self._clear_selection)
        head.addWidget(self._clear_btn)
        outer.addLayout(head)

        # ── Grid chrome (Back + header) ──────────────────────────────
        chrome = QHBoxLayout()
        chrome.setContentsMargins(12, 4, 12, 8)
        chrome.setSpacing(12)
        # Back is in the shared title bar now (routed via
        # ShareCutsPage.on_titlebar_back). The grid's own back_requested
        # (Esc / edge) still fires the same signal.
        self._header_lbl = QLabel(tr("#exported — the base Dynamic Collection"))
        self._header_lbl.setObjectName("DayGridHeader")
        chrome.addWidget(self._header_lbl, stretch=1)
        outer.addLayout(chrome)

        # The grid — Cut-detail's renderer, but the user's interaction
        # is purely "click to mark for deletion". Single-zone clicks
        # toggle the deletion mark on the clicked cell.
        self._grid = ThumbGrid(cell_size=_CELL_SIZE)
        self._grid.cell_activated.connect(self._on_cell_activated)
        self._grid.back_requested.connect(self.back_requested.emit)
        outer.addWidget(self._grid, stretch=1)

        # Keyboard: Del / Backspace = delete; Ctrl+Z = undo last
        # single-cell delete; Esc = back.
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self,
                  activated=self._on_delete_clicked)
        QShortcut(QKeySequence(Qt.Key.Key_Backspace), self,
                  activated=self._on_delete_clicked)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self._on_undo)

    # ── lifecycle ─────────────────────────────────────────────────────

    def open_pool(self, eg) -> None:
        """Bind to a live event gateway + render the pool."""
        self._eg = eg
        self._root = Path(eg.event_root) if eg.event_root else Path(".")
        self._cache.set_event_context(self._root, {})
        self._selected.clear()
        self._undo_stack.clear()
        self._refresh()

    def close_event(self) -> None:
        """Drop in-memory state — called on Back. The on-disk thumb
        + proxy caches stay (they're event-scoped)."""
        self._selected.clear()
        self._undo_stack.clear()
        self._eg = None
        self._root = None
        self._files = []
        self._thumbs = {}

    # ── data → cells ──────────────────────────────────────────────────

    def _refresh(self) -> None:
        if self._eg is None or self._root is None:
            return
        # Use the lenient query so the pool's set matches the Export
        # grid's "Exported" watermark exactly — both read lineage
        # without the visible_item hidden-day filter (Nelson
        # 2026-06-15 bug: pool empty while watermark showed several
        # files). Cuts logic still uses the strict ``exported_files``.
        self._files = list(self._eg.exported_files_all())
        # Drop stale undo entries whose file is no longer on the
        # roster (a fresh batch delete should not re-appear in Ctrl+Z).
        live = {f.export_relpath for f in self._files}
        self._selected = {r for r in self._selected if r in live}
        self._undo_stack = [
            s for s in self._undo_stack if s.export_relpath not in live]
        self._rebuild_cells()
        self._update_chrome()

    def _rebuild_cells(self) -> None:
        items: List[ThumbGridItem] = []
        for f in self._files:
            state = "skipped" if f.export_relpath in self._selected else None
            items.append(ThumbGridItem(
                pixmap=self._thumbs.get(f.export_relpath),
                state=state,
                payload=f.export_relpath,
            ))
        self._header_lbl.setText(tr("#exported — the base Dynamic Collection"))
        self._grid.set_items(items)
        self._request_missing_thumbs()

    def _request_missing_thumbs(self) -> None:
        if self._root is None:
            return
        for f in self._files:
            if f.export_relpath in self._thumbs:
                continue
            self._cache.request_scaled_pixmap(
                self._root / f.export_relpath, _GRID_THUMB_TARGET,
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
        try:
            idx = next(i for i, f in enumerate(self._files)
                       if f.export_relpath == rel)
        except StopIteration:
            return
        self._grid.set_pixmap(idx, hit[0])

    # ── selection ─────────────────────────────────────────────────────

    def _on_cell_activated(self, index: int) -> None:
        """Single click on a cell — toggles the deletion mark. The
        red border IS the mark; the toolbar "Delete N" button is the
        commit. (Center-click double-duty as 'open lightbox' retires
        for this surface — the pool is a housekeeping surface, not a
        browsing one. Nelson 2026-06-15.)"""
        if not (0 <= index < len(self._files)):
            return
        rel = self._files[index].export_relpath
        if rel in self._selected:
            self._selected.discard(rel)
        else:
            self._selected.add(rel)
        # Repaint just this cell with the new border colour. The locked
        # §5a "skipped" state token renders the red 3px border that
        # signals "marked for deletion".
        state = "skipped" if rel in self._selected else None
        self._grid.update_item(index, ThumbGridItem(
            pixmap=self._thumbs.get(rel),
            state=state,
            payload=rel,
        ))
        self._update_chrome()

    def _clear_selection(self) -> None:
        if not self._selected:
            return
        self._selected.clear()
        self._rebuild_cells()
        self._update_chrome()

    def _update_chrome(self) -> None:
        n_files = len(self._files)
        n_sel = len(self._selected)
        # Header counts + delete-button label.
        self._meta_lbl.setText(
            tr("{n} exported file(s)").replace("{n}", str(n_files)))
        self._delete_btn.setVisible(n_sel > 0)
        self._clear_btn.setVisible(n_sel > 0)
        if n_sel > 0:
            self._delete_btn.setText(
                tr("Delete {n} selected").replace("{n}", str(n_sel)))

    # ── delete ────────────────────────────────────────────────────────

    def _on_delete_clicked(self) -> None:
        if self._eg is None or self._root is None:
            return
        if not self._selected:
            return
        if len(self._selected) == 1:
            self._delete_one_quick(next(iter(self._selected)))
        else:
            self._delete_batch_with_confirm(list(self._selected))

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
        self._selected.discard(relpath)
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
        deleted: set[str] = set()
        for rel in relpaths:
            try:
                res = self._eg.delete_exported_file_by_relpath(rel)
                if res.get("rows_deleted"):
                    deleted.add(rel)
            except Exception:                                      # noqa: BLE001
                log.exception(
                    "pool delete: batch delete failed for %s", rel)
        self._selected -= deleted
        self._undo_stack.clear()           # batch is non-undoable
        self._refresh()
        if deleted:
            self.files_deleted.emit(deleted)

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
