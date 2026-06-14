"""The Cut detail surface (spec/61 §5.1) — the flat WYSIWYG grid.

One flat grid in true show order, **separator slides sitting at the day
boundaries as real tiles** (the rendered card IS the thumbnail — what
you see is what PTE receives and what Play shows). Deliberately NOT the
Picker's day drill-down: a Cut is small, already decided, consumed as a
whole. No decision borders either — nothing is being decided here, so
every cell wears the neutral ring.

Top bar (the DayGridView's own): Back · header ("#tag — N items ·
M:SS") · size slider — plus Play all / Export all once the rehearsal
and export slices wire their handlers (the construction flags stay off
until then: no dead buttons). A slim row above carries Adjust (re-enter
the picking session). Center-click opens the lightweight single view
(read-only — arrows step the WHOLE show order, separator/opener cards
included, rendered fresh at full size — Nelson eyeball 2026-06-12).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QCursor, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core import cut_budget, cut_names
from mira.picked.model import CullCell
from mira.picked.status import CellColor
from mira.shared.cut_session import SessionFile, show_entries
from mira.ui.base.day_grid_cell import CellRenderData
from mira.ui.base.day_grid_view import MAX_CELL_SIZE, DayGridView
from mira.ui.base.shortcuts import show_shortcuts
from mira.ui.base.surface import help_button
from mira.ui.i18n import tr
from mira.ui.media.photo_viewport import ViewportItem
from mira.ui.shared.cut_session_page import _SingleView, _fmt_mmss
from mira.ui.shared.separator_card import (
    cut_opener_lines,
    render_cut_opener_image,
    render_separator_image,
    render_separator_pixmap,
)

log = logging.getLogger(__name__)

#: Grid thumbs decode async at the grid's max cell side through the
#: cache's scaled tier (spec/63 slice 2 — same cure as the session
#: page: the old 4-per-20 ms UI-thread timer jammed the grid).
_GRID_THUMB_TARGET = QSize(MAX_CELL_SIZE, MAX_CELL_SIZE)

#: Full-size card height for the single view (the grid thumb would be
#: blurry — cards render fresh, Nelson eyeball 2026-06-12).
_CARD_FULL_HEIGHT = 1080


class CutDetailPage(QWidget):
    """Hosts the flat grid + the read-only single view for one Cut."""

    back_requested = pyqtSignal()
    adjust_requested = pyqtSignal(str)      # cut_id — re-enter the session
    play_requested = pyqtSignal(str)        # cut_id — slice 8 wires
    export_requested = pyqtSignal(str)      # cut_id — slice 9 wires

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        show_play: bool = False,
        show_export: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CutDetailPage")
        self._cut_id: Optional[str] = None
        self._root: Optional[Path] = None
        self._entries: List[Tuple[str, object]] = []
        self._cells: List[CullCell] = []
        self._thumbs: dict = {}
        self._items: List[ViewportItem] = []
        self._index_by_abs: Dict[Path, int] = {}
        from mira.ui.media.photo_cache import photo_cache
        self._cache = photo_cache()
        self._cache.scaled_pixmap_ready.connect(self._on_thumb_ready)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(12, 8, 12, 4)
        self._tag_lbl = QLabel("")
        self._tag_lbl.setObjectName("PoolCountLabel")
        head.addWidget(self._tag_lbl)
        self._meta_lbl = QLabel("")
        self._meta_lbl.setObjectName("PageHint")
        head.addWidget(self._meta_lbl, stretch=1)
        adjust = QPushButton(tr("Adjust"))
        adjust.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        adjust.setToolTip(tr(
            "Re-enter the picking session to add or remove files."))
        adjust.clicked.connect(
            lambda: self._cut_id and self.adjust_requested.emit(self._cut_id))
        head.addWidget(adjust)
        # The shared Help control (Nelson 2026-06-12 UI round).
        self._help_btn = help_button()
        self._help_btn.setToolTip(tr("Keyboard shortcuts  (F1)"))
        self._help_btn.clicked.connect(self._show_shortcuts)
        head.addWidget(self._help_btn)
        outer.addLayout(head)

        self._stack = QStackedWidget()
        self._grid = DayGridView(
            show_play_button=show_play,
            show_export_all_button=show_export,
        )
        self._grid.back_requested.connect(self.back_requested.emit)
        self._grid.cell_activated.connect(self._open_single)
        self._grid.play_requested.connect(
            lambda: self._cut_id and self.play_requested.emit(self._cut_id))
        self._grid.export_all_requested.connect(
            lambda: self._cut_id and self.export_requested.emit(self._cut_id))
        # The ▶ Play button ships hidden until the host gates it on
        # (DayGridView's cluster-era contract) — this surface always
        # plays (Nelson eyeball 2026-06-12: "could not play").
        if show_play:
            self._grid.set_play_button_visible(True)
            self._grid.set_play_tooltip(tr(
                "Play this Cut full-screen — the rehearsal: timed photos, "
                "real clip lengths, separators, music."))
        # No decisions on this surface — border clicks are ignored.
        self._stack.addWidget(self._grid)
        self._single = _SingleView(interactive=False)
        self._single.back_requested.connect(self._back_to_grid)
        self._single.fullscreen_requested.connect(self._toggle_fullscreen)
        self._stack.addWidget(self._single)
        outer.addWidget(self._stack, stretch=1)
        QShortcut(QKeySequence("F1"), self, activated=self._show_shortcuts)

    # ── content ──────────────────────────────────────────────────────

    def show_cut(self, eg, cut, *, separators_on: bool, aspect: str) -> None:
        """Build the flat show-order grid for one Cut: file tiles + a
        rendered separator tile at every day boundary."""
        self._cut_id = cut.id
        self._root = Path(eg.event_root) if eg.event_root else Path(".")
        # Register the event root for the export-thumb tier (spec/63
        # slice 8 — see CutSessionPage; same straight-to-Share reason).
        self._cache.set_event_context(self._root, {})
        day_meta = {d.day_number: d for d in eg.trip_days()}
        self._day_meta = day_meta
        self._aspect = aspect
        self._entries = show_entries(eg, cut, separators_on=separators_on)

        totals_for_opener = eg.cut_show_totals(cut.id)
        if not separators_on:
            from dataclasses import replace as _replace
            totals_for_opener = _replace(totals_for_opener, separator_count=0)
        self._cut = cut
        self._card_style = eg.cut_card_style(cut)
        self._opener_lines = cut_opener_lines(
            cut, totals_for_opener, cut.photo_s)

        self._cells = []
        datas: List[CellRenderData] = []
        self._thumbs = {}
        self._items = []
        self._index_by_abs = {}
        for kind, payload in self._entries:
            if kind == "opener":
                img = render_cut_opener_image(
                    tag_text=cut_names.display_tag(cut.tag),
                    lines=self._opener_lines,
                    aspect=aspect, height=MAX_CELL_SIZE,
                    card_style=self._card_style, seed_key=cut.id)
                pm = QPixmap.fromImage(img)
                if pm.width() > MAX_CELL_SIZE:
                    pm = pm.scaledToWidth(MAX_CELL_SIZE)
                cell = CullCell(
                    end_time="", color=CellColor.UNTOUCHED,
                    item_id="opener", item_kind="photo")
                self._cells.append(cell)
                datas.append(CellRenderData(cell=cell, thumbnail=pm))
                full = render_cut_opener_image(
                    tag_text=cut_names.display_tag(cut.tag),
                    lines=self._opener_lines,
                    aspect=aspect, height=_CARD_FULL_HEIGHT,
                    card_style=self._card_style, seed_key=cut.id)
                self._items.append(ViewportItem(
                    kind="card", payload=tr("Opener"),
                    pixmap=QPixmap.fromImage(full)))
            elif kind == "sep":
                day = payload
                meta = day_meta.get(day)
                pm = render_separator_pixmap(
                    size=MAX_CELL_SIZE,
                    day_number=day,
                    date=getattr(meta, "date", None),
                    location=getattr(meta, "location", None),
                    description=getattr(meta, "description", "") or "",
                    aspect=aspect,
                    card_style=self._card_style,
                    seed_key=f"{cut.id}:{day}")
                cell = CullCell(
                    end_time="", color=CellColor.UNTOUCHED,
                    item_id=f"sep:{day}", item_kind="photo")
                self._cells.append(cell)
                datas.append(CellRenderData(cell=cell, thumbnail=pm))
                full = render_separator_image(
                    day_number=day,
                    date=getattr(meta, "date", None),
                    location=getattr(meta, "location", None),
                    description=getattr(meta, "description", "") or "",
                    aspect=aspect, height=_CARD_FULL_HEIGHT,
                    card_style=self._card_style,
                    seed_key=f"{cut.id}:{day}")
                title = (tr("Day {n} separator").replace("{n}", str(day))
                         if day is not None else tr("Separator"))
                self._items.append(ViewportItem(
                    kind="card", payload=title,
                    pixmap=QPixmap.fromImage(full)))
            else:
                f = payload
                cell = CullCell(
                    end_time=f.capture_time or "",
                    color=CellColor.UNTOUCHED,
                    item_id=f.export_relpath, item_kind=f.kind)
                self._cells.append(cell)
                datas.append(CellRenderData(cell=cell, thumbnail=None))
                abs_path = self._root / f.export_relpath
                self._index_by_abs[abs_path] = len(self._items)
                self._items.append(ViewportItem(
                    path=abs_path, kind=f.kind, payload=f))

        totals = eg.cut_show_totals(cut.id)
        if not separators_on:
            from dataclasses import replace as _replace
            totals = _replace(totals, separator_count=0)
        n = totals.photo_count + totals.video_count
        self._tag_lbl.setText(cut_names.display_tag(cut.tag))
        self._meta_lbl.setText(
            tr("{n} items · {len} projected").replace("{n}", str(n)).replace(
                "{len}", _fmt_mmss(totals.seconds(cut.photo_s))))
        self._grid.set_header(
            tr("{tag} — the show, in order").replace(
                "{tag}", cut_names.display_tag(cut.tag)))
        self._grid.set_cells(datas)
        self._stack.setCurrentIndex(0)
        self._single.set_entries(self._items, 0)
        self._request_missing_thumbs()

    def _request_missing_thumbs(self) -> None:
        """Queue async grid-thumb decodes (priority 1) for every file
        tile without one. Navigation elsewhere may drop queued ones
        (generation rule) — callers re-invoke on re-entry."""
        for kind, f in self._entries:
            if kind == "file" and f.export_relpath not in self._thumbs:
                self._cache.request_scaled_pixmap(
                    self._root / f.export_relpath, _GRID_THUMB_TARGET,
                    priority=1)

    def _on_thumb_ready(self, path: Path, _pm: QPixmap, _native) -> None:
        index = self._index_by_abs.get(Path(path))
        if index is None:
            return
        hit = self._cache.get_scaled_pixmap_if_cached(
            path, _GRID_THUMB_TARGET)
        if hit is None:
            return
        if not (0 <= index < len(self._entries)):
            return
        kind, f = self._entries[index]
        if kind != "file":
            return
        self._thumbs[f.export_relpath] = hit[0]
        if 0 <= index < len(self._cells):
            self._grid.update_cell(
                index, CellRenderData(cell=self._cells[index],
                                      thumbnail=hit[0]))

    # ── single view (read-only) ──────────────────────────────────────

    def _open_single(self, index: int) -> None:
        if not (0 <= index < len(self._items)):
            return
        self._single.show_entry(index)
        self._stack.setCurrentIndex(1)
        self._single.setFocus()

    def _back_to_grid(self) -> None:
        self._stack.setCurrentIndex(0)
        self._grid.setFocus()
        self._request_missing_thumbs()      # gens may have dropped some

    def _toggle_fullscreen(self) -> None:
        win = self.window()
        if win.isFullScreen():
            win.showNormal()
        else:
            win.showFullScreen()

    def _show_shortcuts(self) -> None:
        show_shortcuts(self, tr("Cut detail"), [
            ("",                    tr("Navigate")),
            (tr("Click a cell"),    tr("Open the single view")),
            (tr("◀ / ▶"),            tr("Previous / next file (single view)")),
            (tr("Esc"),              tr("Back to the grid / out of the Cut")),
            ("",                    tr("Actions")),
            (tr("Click Adjust"),    tr("Re-enter the session to add or "
                                       "remove files")),
            (tr("Click ▶"),          tr("Play the Cut full-screen — the "
                                        "rehearsal")),
            (tr("F11"),             tr("Fullscreen")),
            (tr("F1 · ?"),          tr("This help")),
        ])
