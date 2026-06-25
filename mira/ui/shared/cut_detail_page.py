"""The Cut detail surface (spec/61 §5.1) — the flat WYSIWYG grid.

One flat grid in true show order, **separator slides sitting at the day
boundaries as real tiles** (the rendered card IS the thumbnail — what
you see is what PTE receives and what Play shows). Deliberately NOT the
Picker's day drill-down: a Cut is small, already decided, consumed as a
whole. No decision borders either — nothing is being decided here, so
every cell wears the neutral ring.

Top bar: Back · header ("#tag — the show, in order") · Adjust · Play
all / Export all (when the host wires them) · Help. A slim row above
carries Adjust (re-enter the picking session). Center-click opens the
lightweight single view (read-only — arrows step the WHOLE show order,
separator/opener cards included, rendered fresh at full size — Nelson
eyeball 2026-06-12).

Built on :class:`mira.ui.design.ThumbGrid` (the shared scrolling
thumb grid). The blurred-fill backdrop + 3px state border come from
:class:`mira.ui.design.Thumb` painters — every Cut cell carries the
same look without the legacy ``DayGridCell`` chrome.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QCursor, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core import cut_budget, cut_names
from mira.shared.cut_session import SessionFile, show_entries
from mira.ui.base.shortcuts import show_shortcuts
from mira.ui.design import ThumbGrid, ThumbGridItem
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

#: Grid thumbs decode async at the grid's cell size through the cache's
#: scaled tier (spec/63 slice 2 — same cure as the session page: the
#: old 4-per-20 ms UI-thread timer jammed the grid).
_CELL_PX = 220
_GRID_THUMB_TARGET = QSize(_CELL_PX, _CELL_PX)
_CELL_SIZE = QSize(_CELL_PX, _CELL_PX)

#: Full-size card height for the single view (the grid thumb would be
#: blurry — cards render fresh, Nelson eyeball 2026-06-12).
_CARD_FULL_HEIGHT = 1080


class CutDetailPage(QWidget):
    """Hosts the flat grid + the read-only single view for one Cut."""

    back_requested = pyqtSignal()
    adjust_requested = pyqtSignal(str)      # cut_id — re-enter the session
    play_requested = pyqtSignal(str)        # cut_id — slice 8 wires
    export_requested = pyqtSignal(str)      # cut_id — slice 9 wires
    publish_requested = pyqtSignal(str)     # cut_id — spec/76 §B.3
    # spec/117 — persistent post-export actions on a shipped Cut. The
    # host resolves the export folder + ``.pte`` and either reveals the
    # bundle in Explorer (Open folder) or launches PTE (Open in PTE).
    # Both fire only when ``last_exported_at`` is set; the host gates
    # PTE further on ``use_pte`` + ``pte_launch_available``.
    open_folder_requested = pyqtSignal(str)
    open_in_pte_requested = pyqtSignal(str)
    # spec/149 — standalone Generate PTE. The host scans the resolved
    # export folder, rebuilds the member list from the files there, and
    # writes a fresh ``.pte`` (no media re-materialisation). Gated on
    # ``use_pte`` + ``folder_exists`` (independent of ``pte_path`` —
    # generating doesn't need a launcher).
    generate_pte_requested = pyqtSignal(str)

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
        self._thumbs: dict = {}
        self._items: List[ViewportItem] = []
        self._index_by_abs: Dict[Path, int] = {}
        from mira.ui.media.photo_cache import photo_cache
        self._cache = photo_cache()
        self._cache.scaled_pixmap_ready.connect(self._on_thumb_ready)

        # spec/94 Phase 3 — Back lives in the shared title bar.
        self.uses_titlebar_back = True

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Flush full-width pink rail (Share state) at the very top, like the
        # other surfaces. Back lives in the shared title bar — routed here by
        # ShareCutsPage.on_titlebar_back (Nelson 2026-06-21).
        self._rail = QFrame()
        self._rail.setObjectName("SurfaceHeaderRail")
        self._rail.setProperty("phase", "share")
        self._rail.setFixedHeight(2)
        root.addWidget(self._rail)

        # ── Content host (standard margins/spacing — spec/94 Phase 3) ─
        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(28, 18, 28, 22)
        outer.setSpacing(12)
        root.addWidget(content, 1)

        # ── Top band: Adjust row + grid chrome row in one SurfaceBand ─
        top_band = QFrame()
        top_band.setObjectName("SurfaceBand")
        top_l = QVBoxLayout(top_band)
        top_l.setContentsMargins(16, 12, 16, 12)
        top_l.setSpacing(10)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)
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
        # Help is in the shared title bar now (routed to show_help / F1).
        top_l.addLayout(head)

        chrome = QHBoxLayout()
        chrome.setContentsMargins(0, 0, 0, 0)
        chrome.setSpacing(12)
        # Back is in the shared title bar now (routed via
        # ShareCutsPage.on_titlebar_back). The grid's own back_requested
        # (Esc / edge) still fires the same signal.
        self._header_lbl = QLabel("")
        self._header_lbl.setObjectName("DayGridHeader")
        chrome.addWidget(self._header_lbl, stretch=1)
        # Play / Export are opt-in via host flags. spec/61 §5.4 / §5.2 —
        # the host wires the buttons when the corresponding handlers
        # land; absent host wiring leaves them hidden so no dead control.
        self._play_btn = QPushButton(tr("▶ Play"))
        self._play_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._play_btn.setToolTip(tr(
            "Play this Cut full-screen — the rehearsal: timed photos, "
            "real clip lengths, separators, music."))
        self._play_btn.clicked.connect(
            lambda: self._cut_id and self.play_requested.emit(self._cut_id))
        self._play_btn.setVisible(bool(show_play))
        chrome.addWidget(self._play_btn)
        self._export_btn = QPushButton(tr("📤 Export all"))
        self._export_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._export_btn.setToolTip(tr(
            "Export every processed photo in this Cut."))
        self._export_btn.clicked.connect(
            lambda: self._cut_id and self.export_requested.emit(self._cut_id))
        self._export_btn.setVisible(bool(show_export))
        # spec/76 §B.1 — Export stamps last_exported_at in the store;
        # refuse in read-only mode (the gateway guard catches it
        # defensively too).
        from mira.ui.read_only import disable_if_read_only
        disable_if_read_only(self._export_btn)
        chrome.addWidget(self._export_btn)
        # spec/76 §B.3 — Publish materialises the Cut to the library
        # publish slot + writes a manifest, for a TV media server to
        # read. Same enable/visibility shape as Export.
        self._publish_btn = QPushButton(tr("📡 Publish"))
        self._publish_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._publish_btn.setToolTip(tr(
            "Publish this Cut to the library's TV media-server slot "
            "with a manifest sidecar."))
        self._publish_btn.clicked.connect(
            lambda: self._cut_id and self.publish_requested.emit(self._cut_id))
        self._publish_btn.setVisible(bool(show_export))
        disable_if_read_only(self._publish_btn)
        chrome.addWidget(self._publish_btn)
        # spec/117 — persistent post-export actions. Hidden by default;
        # :meth:`set_exported_actions` flips them on when the host
        # resolved a shipped bundle. Read-only is fine: launching PTE
        # / revealing a folder doesn't touch the store.
        self._open_pte_btn = QPushButton(tr("Open in PTE"))
        self._open_pte_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._open_pte_btn.setToolTip(tr(
            "Reopen this exported Cut's slideshow.pte in PTE — no "
            "re-export needed."))
        self._open_pte_btn.clicked.connect(
            lambda: self._cut_id
            and self.open_in_pte_requested.emit(self._cut_id))
        self._open_pte_btn.setVisible(False)
        chrome.addWidget(self._open_pte_btn)
        # spec/149 — Generate PTE writes a fresh slideshow.pte into the
        # exported folder using the files already there. Visible when
        # ``use_pte`` is on AND the folder still exists (independent of
        # whether the launcher path resolves — generating doesn't need
        # one). Covers the rename-broke-the-pte / use_pte-was-off /
        # deleted-pte cases without a full re-export.
        self._generate_pte_btn = QPushButton(tr("Generate PTE"))
        self._generate_pte_btn.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        self._generate_pte_btn.setToolTip(tr(
            "Rewrite slideshow.pte for this exported folder using the "
            "files there (no media re-export)."))
        self._generate_pte_btn.clicked.connect(
            lambda: self._cut_id
            and self.generate_pte_requested.emit(self._cut_id))
        self._generate_pte_btn.setVisible(False)
        chrome.addWidget(self._generate_pte_btn)
        self._open_folder_btn = QPushButton(tr("Open folder"))
        self._open_folder_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._open_folder_btn.setToolTip(tr(
            "Reveal this exported Cut's bundle folder in Explorer."))
        self._open_folder_btn.clicked.connect(
            lambda: self._cut_id
            and self.open_folder_requested.emit(self._cut_id))
        self._open_folder_btn.setVisible(False)
        chrome.addWidget(self._open_folder_btn)
        top_l.addLayout(chrome)

        outer.addWidget(top_band)

        # ── Grid band: the flat show-order grid ↔ single view ────────
        grid_band = QFrame()
        grid_band.setObjectName("SurfaceBand")
        grid_l = QVBoxLayout(grid_band)
        grid_l.setContentsMargins(16, 12, 16, 14)
        grid_l.setSpacing(10)

        self._stack = QStackedWidget()
        # spec/61 §5.1 — Cuts are post-pick, so the grid carries no
        # decision-state border. The blurred-fill canvas + hairline
        # frame are :class:`Thumb`'s native rendering so aspect-mismatched
        # photos and 16:9 separator cards no longer letterbox.
        self._grid = ThumbGrid(cell_size=_CELL_SIZE)
        self._grid.cell_activated.connect(self._open_single)
        self._grid.back_requested.connect(self.back_requested.emit)
        self._stack.addWidget(self._grid)
        self._single = _SingleView(interactive=False)
        self._single.back_requested.connect(self._back_to_grid)
        self._single.fullscreen_requested.connect(self._toggle_fullscreen)
        self._stack.addWidget(self._single)
        grid_l.addWidget(self._stack, stretch=1)
        outer.addWidget(grid_band, 1)
        QShortcut(QKeySequence("F1"), self, activated=self._show_shortcuts)

    # ── content ──────────────────────────────────────────────────────

    def set_exported_actions(
        self, *, show_folder: bool, show_pte: bool,
        show_generate: bool = False,
    ) -> None:
        """spec/117 + spec/149 — toggle the persistent post-export
        action buttons.

        The host resolves the bundle (via
        :func:`mira.shared.exported_cut_actions.resolve_event_cut_location`
        or its cross-event sibling) and the PTE gates (``use_pte`` +
        :func:`mira.shared.pte_launch.pte_launch_available`), then
        calls this with the booleans. A never-exported Cut shows
        none; a moved/deleted bundle shows folder-only (which
        degrades to the parent ``Cuts/…``).

        spec/149 — ``show_generate`` (default False for back-compat)
        flips the standalone Generate PTE action. The host gates it on
        ``use_pte`` + folder-exists; the launcher path is irrelevant
        here (writing the .pte is independent of opening it).
        """
        self._open_folder_btn.setVisible(bool(show_folder))
        self._open_pte_btn.setVisible(bool(show_pte))
        self._generate_pte_btn.setVisible(bool(show_generate))

    def show_cut(
        self, eg, cut, *,
        separators_on: bool, aspect: str,
        transition_s: float = 0.0,
    ) -> None:
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
        from dataclasses import replace as _replace
        # spec/152 §3 — opener slot + transition spend the same wall
        # time the rehearsal does; sum them into the opener-card line
        # so it agrees with the in-page meta + the audio playlist.
        totals_for_opener = _replace(
            totals_for_opener,
            separator_count=(totals_for_opener.separator_count
                             if separators_on else 0),
            opener_count=(1 if separators_on else 0),
        )
        self._cut = cut
        self._card_style = eg.cut_card_style(cut)
        self._opener_lines = cut_opener_lines(
            cut, totals_for_opener, cut.photo_s, transition_s)

        grid_items: List[ThumbGridItem] = []
        self._thumbs = {}
        self._items = []
        self._index_by_abs = {}
        for kind, payload in self._entries:
            if kind == "opener":
                img = render_cut_opener_image(
                    tag_text=cut_names.display_tag(cut.tag),
                    lines=self._opener_lines,
                    aspect=aspect, height=_CELL_PX,
                    card_style=self._card_style, seed_key=cut.id)
                pm = QPixmap.fromImage(img)
                if pm.width() > _CELL_PX:
                    pm = pm.scaledToWidth(_CELL_PX)
                grid_items.append(ThumbGridItem(pixmap=pm, payload=("opener", None)))
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
                    size=_CELL_PX,
                    day_number=day,
                    date=getattr(meta, "date", None),
                    location=getattr(meta, "location", None),
                    description=getattr(meta, "description", "") or "",
                    aspect=aspect,
                    card_style=self._card_style,
                    seed_key=f"{cut.id}:{day}")
                grid_items.append(ThumbGridItem(pixmap=pm, payload=("sep", day)))
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
                # Video tiles need the ``cluster_type='video'`` badge
                # so they read as videos at a glance — the generic
                # image cache can't decode mp4 poster frames, so
                # without the badge the cell stays a blank square
                # forever. Same fix the CutSessionPage picker grid
                # uses (Nelson 2026-06-19 for spec/144 picker; now
                # extended to the cut-detail flat grid 2026-06-25).
                is_video = f.kind == "video"
                grid_items.append(ThumbGridItem(
                    pixmap=None, payload=("file", f.export_relpath),
                    cluster_type="video" if is_video else None,
                    cluster_count=1 if is_video else 0))
                abs_path = self._root / f.export_relpath
                self._index_by_abs[abs_path] = len(self._items)
                self._items.append(ViewportItem(
                    path=abs_path, kind=f.kind, payload=f))

        totals = eg.cut_show_totals(cut.id)
        # spec/152 §3 — include transition + opener slots so the
        # detail-page projection matches the rehearsal / audio / PTE
        # total. Per-Cut transition wins over the Settings default.
        from dataclasses import replace as _replace
        totals = _replace(
            totals,
            separator_count=(totals.separator_count
                             if separators_on else 0),
            opener_count=(1 if separators_on else 0),
        )
        # The caller passes the resolved transition_s (per-Cut value if
        # set, else Settings.default_transition_ms). Defaults to 0 so
        # legacy callers that don't pass it still produce sane totals.
        n = totals.photo_count + totals.video_count
        self._tag_lbl.setText(cut_names.display_tag(cut.tag))
        self._meta_lbl.setText(
            tr("{n} items · {len} projected").replace("{n}", str(n)).replace(
                "{len}", _fmt_mmss(totals.seconds(
                    cut.photo_s, transition_s))))
        self._header_lbl.setText(
            tr("{tag} — the show, in order").replace(
                "{tag}", cut_names.display_tag(cut.tag)))
        self._grid.set_items(grid_items)
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
        self._grid.set_pixmap(index, hit[0])

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

    def show_help(self) -> None:
        """Title-bar Help / F1 hook (routed via ShareCutsPage.show_help)."""
        self._show_shortcuts()

    def on_titlebar_back(self) -> None:
        """spec/94 Phase 3 — shared title-bar Back dispatch.

        At the flat grid (the landing state) Back leaves the Cut detail
        via ``back_requested``; the single view falls back to the grid
        one level at a time.
        """
        if self._stack.currentIndex() == 1:
            self._back_to_grid()
        else:
            self.back_requested.emit()

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
