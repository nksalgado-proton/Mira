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

#: spec/159 — center-click placeholder until Session B lands the
#: review-mode editor. Set to True to log the would-be open; False
#: makes it a silent no-op.
_LOG_CENTER_CLICK_PLACEHOLDER = True


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
        outer.addLayout(head)

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

        # spec/159 — two-zone grid: border-click toggles the lineage
        # row's ``to_delete`` flag; center-click opens the review-mode
        # editor (placeholder until Session B). The two-zone hit-test
        # is the same one days_grid uses.
        self._grid = ThumbGrid(cell_size=_CELL_SIZE, two_zone_clicks=True)
        self._grid.cell_border_clicked.connect(self._on_cell_border_clicked)
        self._grid.cell_activated.connect(self._on_cell_activated)
        self._grid.back_requested.connect(self.back_requested.emit)
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

    # ── lifecycle ─────────────────────────────────────────────────────

    def open_pool(self, eg) -> None:
        """Bind to a live event gateway + render the pool."""
        self._eg = eg
        self._root = Path(eg.event_root) if eg.event_root else Path(".")
        self._cache.set_event_context(self._root, {})
        self._undo_stack.clear()
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
        # Drop stale undo entries whose file is no longer on the
        # roster (a fresh batch delete should not re-appear in Ctrl+Z).
        live = {f.export_relpath for f in self._files}
        self._undo_stack = [
            s for s in self._undo_stack if s.export_relpath not in live]
        self._rebuild_cells()
        self._update_chrome()

    def _rebuild_cells(self) -> None:
        items: List[ThumbGridItem] = []
        for f in self._files:
            # spec/159 — the cell carries the lineage row's ratings +
            # delete flag. The locked colour grammar (state border)
            # stays neutral on this surface (per spec/159 §4.2 "state
            # border DROP at default"); the "Marked for deletion" badge
            # paints from to_delete instead.
            items.append(ThumbGridItem(
                pixmap=self._thumbs.get(f.export_relpath),
                state=None,
                payload=f.export_relpath,
                stars=getattr(f, "stars", None),
                color_label=getattr(f, "color_label", None),
                flag=bool(getattr(f, "flag", False)),
                to_delete=bool(getattr(f, "to_delete", False)),
            ))
        self._header_lbl.setText(tr("#exported — the base Collection"))
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

    def _on_cell_border_clicked(self, index: int) -> None:
        """spec/159 — border-click toggles the lineage row's
        ``to_delete`` flag. The flag persists across sessions; the
        toolbar's "⌫ Delete marked…" action commits the batch."""
        if self._eg is None:
            return
        if not (0 <= index < len(self._files)):
            return
        f = self._files[index]
        new_state = not bool(getattr(f, "to_delete", False))
        try:
            self._eg.set_lineage_to_delete(f.export_relpath, new_state)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "DCDetailPage: set_lineage_to_delete failed for %s",
                f.export_relpath)
            return
        # In-place update — repaint just this cell + the toolbar chrome.
        # Mutate the cached Lineage row so the next rebuild reads the
        # new flag without another DB hit.
        try:
            f.to_delete = new_state                                # type: ignore[misc]
        except AttributeError:
            self._files[index] = m.Lineage(
                **{**f.__dict__, "to_delete": new_state})
        self._grid.update_item(index, ThumbGridItem(
            pixmap=self._thumbs.get(f.export_relpath),
            state=None,
            payload=f.export_relpath,
            stars=getattr(f, "stars", None),
            color_label=getattr(f, "color_label", None),
            flag=bool(getattr(f, "flag", False)),
            to_delete=new_state,
        ))
        self._update_chrome()

    def _on_cell_activated(self, index: int) -> None:
        """spec/159 — center-click opens the review viewer on the
        clicked version, with the rest of the visible list available
        for ←/→ nav. Emits ``review_requested`` for any host that
        wants to observe the open."""
        if not (0 <= index < len(self._files)) or self._eg is None:
            return
        rel = self._files[index].export_relpath
        self.review_requested.emit(rel)
        self._open_review_dialog(index)

    def _open_review_dialog(self, start_index: int) -> None:
        """Build the ReviewItem list from the current lineage roster
        and open the dialog. Mutations from the dialog write through
        the gateway and update the cached lineage rows + the grid
        chrome in real time."""
        from mira.ui.exported.review_dialog import (
            ReviewItem, ReviewMediaDialog,
        )
        if self._eg is None or self._root is None:
            return
        items: List[ReviewItem] = []
        for f in self._files:
            items.append(ReviewItem(
                export_relpath=f.export_relpath,
                abs_path=self._root / f.export_relpath,
                stars=getattr(f, "stars", None),
                color_label=getattr(f, "color_label", None),
                flag=bool(getattr(f, "flag", False)),
                to_delete=bool(getattr(f, "to_delete", False)),
                title=Path(f.export_relpath).name,
            ))
        if not items:
            return
        dlg = ReviewMediaDialog(items, start_index=start_index, parent=self)
        dlg.stars_changed.connect(self._on_review_stars_changed)
        dlg.color_label_changed.connect(
            self._on_review_color_label_changed)
        dlg.flag_changed.connect(self._on_review_flag_changed)
        dlg.to_delete_changed.connect(
            self._on_review_to_delete_changed)
        dlg.exec()
        # On close: rebuild the cells so any rating changes paint.
        self._rebuild_cells()
        self._update_chrome()

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
        # Header counts + delete-button label.
        self._meta_lbl.setText(
            tr("{n} exported file(s)").replace("{n}", str(n_files)))
        self._delete_btn.setVisible(n_marked > 0)
        self._clear_btn.setVisible(n_marked > 0)
        if n_marked > 0:
            self._delete_btn.setText(
                tr("⌫ Delete {n} marked…").replace("{n}", str(n_marked)))

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
