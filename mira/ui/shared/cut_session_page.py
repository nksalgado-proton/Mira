"""The Cut picking session surface (spec/61 §2 step 6) — Picker-shaped,
session-ledgered.

Days panel → day grid of **exported files** → single view. Same feel as
the Pick phase, different ledger: every Pick/Skip lands on the in-memory
:class:`~mira.shared.cut_session.CutSession`, never on
``phase_state``. Cells render the exported JPEG/MP4 bytes themselves
(the user picks among finals — two versions of one photo are two
cells). No cluster layer: finals don't bucket.

The live budget line rides the top bar in the export-progress-line
slot's visual language: show length vs target/max, green / amber / red
(spec/61 §2 step 5). Ctrl+Z undoes the last decision anywhere on the
page. **Create Cut** is the one persistence moment —
``session.commit(gateway)`` writes the definition + membership and the
page emits ``finished``; Cancel emits ``cancelled`` and nothing was
ever written.

Hosting (slice 6): the Cuts list page constructs this with a fresh
session (from the New Cut dialog's draft) or a re-entered one
(``CutSession.for_cut``).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mira.shared.cut_session import CutSession, SessionFile
from mira.ui.base.shortcuts import show_shortcuts
from mira.ui.base.surface import back_button, help_button
from mira.ui.design import ThumbGrid, ThumbGridItem, ghost_button
from mira.ui.i18n import tr
from mira.ui.media.photo_viewport import PhotoViewport, ViewportItem

log = logging.getLogger(__name__)

#: Grid thumbs decode at the grid's cell size, so resizing never
#: re-decodes (the Pick page's trade) — but ASYNC, through the
#: cache's scaled tier at priority 1 (spec/63 slice 2: the old
#: 4-per-20 ms UI-thread timer crammed ~96 ms of decode into every
#: 20 ms tick — the grid jammed exactly while the user moused over it).
_CELL_PX = 220
_GRID_THUMB_TARGET = QSize(_CELL_PX, _CELL_PX)
_CELL_SIZE = QSize(_CELL_PX, _CELL_PX)


def _fmt_mmss(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    return f"{s // 60}:{s % 60:02d}"


class CutBudgetLine(QWidget):
    """The live minutes budget (spec/61 §2 step 5) — Nelson eyeball
    round 3: the subtle top-bar text was never SEEN, so this is now an
    unmissable full-width STRIP: a filling bar (show length against the
    budget, the fill wearing the zone colour — green at/under target,
    amber to max, red past) plus the numbers. Sits above the picker
    stack, so the grid AND the picture view both carry it; refreshed on
    every Pick/Skip from any surface."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        from PyQt6.QtWidgets import QProgressBar
        self.setObjectName("CutBudgetLine")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 4, 12, 4)
        row.setSpacing(10)
        self._label = QLabel("")
        self._label.setObjectName("CutBudgetLabel")
        self._label.setToolTip(tr(
            "The show so far: picked items + day separators at the "
            "per-photo seconds, clips at their real length — against "
            "this Cut's target and max."))
        row.addWidget(self._label)
        self._bar = QProgressBar()
        self._bar.setObjectName("CutBudgetBar")
        self._bar.setTextVisible(False)
        self._bar.setMaximumHeight(14)
        self._bar.setToolTip(self._label.toolTip())
        row.addWidget(self._bar, stretch=1)
        self._limit = QLabel("")
        self._limit.setObjectName("CutBudgetLabel")
        self._limit.setToolTip(tr("This Cut's target — max."))
        row.addWidget(self._limit)

    def _repolish(self, w) -> None:
        w.style().unpolish(w)
        w.style().polish(w)

    def refresh(self, session: CutSession) -> None:
        totals = session.totals()
        picked = totals.photo_count + totals.video_count
        text = tr("{n} picked · {len}").replace("{n}", str(picked)).replace(
            "{len}", _fmt_mmss(session.show_seconds()))
        if totals.separator_count:
            text += tr(" · {d} card(s)").replace(
                "{d}", str(totals.separator_count))
        self._label.setText(text)

        # The bar fills against the budget: 0 → max (or target when no
        # max). A Cut with no limit shows numbers only.
        ceiling = session.max_s or session.target_s
        if ceiling:
            self._bar.setVisible(True)
            self._bar.setMaximum(int(ceiling))
            self._bar.setValue(min(int(session.show_seconds()), int(ceiling)))
            limit_bits = []
            if session.target_s:
                limit_bits.append(_fmt_mmss(session.target_s))
            if session.max_s:
                limit_bits.append(_fmt_mmss(session.max_s))
            self._limit.setText(" — ".join(limit_bits))
        else:
            self._bar.setVisible(False)
            self._limit.setText(tr("no limit"))

        zone = session.zone()
        if self.property("zone") != zone:
            self.setProperty("zone", zone)
            # Repolish the CHILDREN too — descendant QSS rules keyed on a
            # parent property don't recompute on Windows otherwise (the
            # round-1 line stayed grey forever).
            for w in (self, self._label, self._bar, self._limit):
                self._repolish(w)


class _DaysPanel(QWidget):
    """The session's days list — lean rows (Day N · date · picked/total),
    not the Pick navigator (no buckets, no pass machinery)."""

    day_activated = pyqtSignal(int)     # index into session.days()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 12, 16, 12)
        box.setSpacing(6)
        hint = QLabel(tr("Pick into the Cut day by day — or dive into "
                         "any day and use the keys: P picks, X skips, "
                         "Space toggles."))
        hint.setObjectName("PageHint")
        hint.setWordWrap(True)
        box.addWidget(hint)
        self._rows_box = QVBoxLayout()
        self._rows_box.setSpacing(6)
        box.addLayout(self._rows_box)
        box.addStretch(1)
        self._buttons: List[QPushButton] = []

    def set_days(
        self,
        groups: List[Tuple[Optional[int], List[SessionFile]]],
        session: CutSession,
        day_labels: Dict[int, str],
    ) -> None:
        while self._rows_box.count():
            item = self._rows_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._buttons = []
        for idx, (day, files) in enumerate(groups):
            picked = sum(1 for f in files if session.is_picked(f.export_relpath))
            if day is None:
                title = tr("Undated")
            else:
                title = tr("Day {n}").replace("{n}", str(day))
                extra = day_labels.get(day, "")
                if extra:
                    title += f" · {extra}"
            btn = QPushButton(
                f"{title}   —   "
                + tr("{p} of {t} picked").replace(
                    "{p}", str(picked)).replace("{t}", str(len(files))))
            btn.setObjectName("CutSessionDayRow")
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setToolTip(tr("Open this day's exported files in a grid."))
            btn.clicked.connect(lambda _=False, i=idx: self.day_activated.emit(i))
            self._rows_box.addWidget(btn)
            self._buttons.append(btn)


class _SingleView(QWidget):
    """Single-file view, Picker-shaped (Nelson eyeball 2026-06-12): the
    current item sits inside the DECISION FRAME (the same green/red
    border language as the grid cells), with explicit Pick / Skip
    buttons. Pixels + navigation + the key grammar live in the embedded
    :class:`PhotoViewport` (spec/63 slice 2); this class is CHROME —
    title, decision frame, state pill — plus the verb wiring: P picks,
    X skips, Space toggles, C cycles (binary ledger here, so C degrades
    to the toggle — spec/63 §4), Esc backs out, F/F11 bubble up as
    ``fullscreen_requested``.

    ``interactive=False`` (the Cut detail surface) hides the decision
    chrome — the view is then a pure look (separator/opener cards ride
    the entry list as loose-pixmap items)."""

    back_requested = pyqtSignal()
    state_requested = pyqtSignal(str, bool)  # relpath, picked
    fullscreen_requested = pyqtSignal()
    current_changed = pyqtSignal(int)        # index within the entry list

    def __init__(self, parent: Optional[QWidget] = None, *,
                 interactive: bool = True) -> None:
        super().__init__(parent)
        self._interactive = bool(interactive)
        box = QVBoxLayout(self)
        box.setContentsMargins(12, 8, 12, 8)
        top = QHBoxLayout()
        back = back_button()
        back.setToolTip(tr("Back to the grid (Esc)."))
        back.clicked.connect(self.back_requested.emit)
        top.addWidget(back)
        self._title = QLabel("")
        self._title.setObjectName("PageHint")
        top.addWidget(self._title, stretch=1)
        self._pick_btn = QPushButton(tr("✓ Pick (P)"))
        self._pick_btn.setToolTip(tr("Put this file in the Cut."))
        self._pick_btn.clicked.connect(self._emit_pick)
        self._skip_btn = QPushButton(tr("✗ Skip (X)"))
        self._skip_btn.setToolTip(tr("Leave this file out of the Cut."))
        self._skip_btn.clicked.connect(self._emit_skip)
        for b in (self._pick_btn, self._skip_btn):
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setVisible(self._interactive)
            top.addWidget(b)
        self._state = QLabel("")
        self._state.setObjectName("PoolCountLabel")
        top.addWidget(self._state)
        box.addLayout(top)
        # The decision frame — same border language as the grid cells.
        self._frame = QFrame()
        self._frame.setObjectName("CutSingleFrame")
        frame_box = QVBoxLayout(self._frame)
        b = 4
        frame_box.setContentsMargins(b, b, b, b)
        self._viewport = PhotoViewport(self)
        self._viewport.setObjectName("CutSingleImage")
        self._viewport.setMinimumSize(320, 240)
        # The F10 lens opens CLEAN here — no zoom/peaking bar on the
        # Cut surfaces (Nelson 2026-06-12 standardisation).
        self._viewport.set_lens_tools_visible(False)
        # The labelled "Full Resolution F10" button below covers the
        # corner 🔍 affordance, same pattern as picker_page /
        # quick_sweep_page / editor_page.
        self._viewport.set_corner_inspect_visible(False)
        frame_box.addWidget(self._viewport)
        box.addWidget(self._frame, stretch=1)
        # Bottom row — Full Resolution + Full Screen labelled buttons,
        # centred under the frame. Matches the visible affordance every
        # other PhotoViewport host carries; the F10 / F11 keys still
        # flow through the viewport + the page-level shortcut.
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 6, 0, 0)
        bottom.addStretch(1)
        self._fullres_btn = ghost_button(tr("Full Resolution  F10"))
        self._fullres_btn.setToolTip(tr(
            "Inspect this frame up close at full resolution  (F10)"))
        self._fullres_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._fullres_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._fullres_btn.clicked.connect(self._viewport.truth_requested.emit)
        bottom.addWidget(self._fullres_btn)
        self._fullscreen_btn = ghost_button(tr("Full Screen  F11"))
        self._fullscreen_btn.setToolTip(tr(
            "Toggle fullscreen  (F11)"))
        self._fullscreen_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._fullscreen_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._fullscreen_btn.clicked.connect(self.fullscreen_requested.emit)
        bottom.addWidget(self._fullscreen_btn)
        bottom.addStretch(1)
        box.addLayout(bottom)
        self.setFocusProxy(self._viewport)
        self._picked = False
        self._picked_lookup: Callable[[str], bool] = lambda _r: False

        vp = self._viewport
        vp.back_requested.connect(self.back_requested.emit)
        vp.fullscreen_requested.connect(self.fullscreen_requested.emit)
        vp.current_changed.connect(self._on_current_changed)
        vp.pick_requested.connect(self._emit_pick)
        vp.skip_requested.connect(self._emit_skip)
        vp.toggle_requested.connect(self._emit_toggle)
        # Binary ledger: a file is in the Cut or not — C has no third
        # state to walk, so the cycle degrades to the toggle (spec/63 §4).
        vp.cycle_requested.connect(self._emit_toggle)

    # ── content ──────────────────────────────────────────────────────

    def set_entries(
        self,
        items: List[ViewportItem],
        current: int,
        *,
        picked_lookup: Optional[Callable[[str], bool]] = None,
    ) -> None:
        """Hand the show order to the viewport. File items carry their
        SessionFile as payload; card items carry their title string."""
        if picked_lookup is not None:
            self._picked_lookup = picked_lookup
        self._viewport.set_items(items, current)

    def show_entry(self, index: int) -> None:
        self._viewport.show_index(index)

    def current_index(self) -> int:
        return self._viewport.current_index()

    def current_file(self) -> Optional[SessionFile]:
        item = self._viewport.current_item()
        if item is None or item.kind == "card":
            return None
        return item.payload

    def _on_current_changed(self, index: int) -> None:
        item = self._viewport.current_item()
        if item is None:
            return
        if item.kind == "card":
            self._title.setText(str(item.payload or ""))
            self._set_status("untouched")
            self._state.setText("")
        else:
            f: SessionFile = item.payload
            title = f.export_relpath
            if f.kind == "video":
                secs = (f.duration_ms or 0) / 1000.0
                title += "   —   " + tr("video · {len}").replace(
                    "{len}", _fmt_mmss(secs))
            self._title.setText(title)
            self.set_picked(self._picked_lookup(f.export_relpath))
        self.current_changed.emit(index)

    def set_picked(self, picked: bool) -> None:
        self._picked = bool(picked)
        if not self._interactive:
            self._set_status("untouched")
            self._state.setText("")
            return
        self._set_status("picked" if picked else "skipped")
        self._state.setText(
            tr("✓ in the Cut") if picked else tr("✗ not in the Cut"))

    def _set_status(self, status: str) -> None:
        if self._frame.property("status") != status:
            self._frame.setProperty("status", status)
            self._frame.style().unpolish(self._frame)
            self._frame.style().polish(self._frame)

    # ── decisions (the verbs — keys arrive via the viewport) ─────────

    def _emit_pick(self) -> None:
        f = self.current_file()
        if f is not None and self._interactive:
            self.state_requested.emit(f.export_relpath, True)

    def _emit_skip(self) -> None:
        f = self.current_file()
        if f is not None and self._interactive:
            self.state_requested.emit(f.export_relpath, False)

    def _emit_toggle(self) -> None:
        f = self.current_file()
        if f is not None and self._interactive:
            self.state_requested.emit(
                f.export_relpath,
                not self._picked_lookup(f.export_relpath))


class CutSessionPage(QWidget):
    """Hosts the session: top bar (tag · budget line · Create Cut /
    Cancel) over a days-panel → day-grid → single-view stack."""

    finished = pyqtSignal(object)       # the committed cut row
    cancelled = pyqtSignal()

    def __init__(
        self,
        gateway,
        session: CutSession,
        *,
        event_root: Path,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CutSessionPage")
        self._gw = gateway
        self._session = session
        self._root = Path(event_root)
        self._groups = session.days()
        self._day_labels = self._load_day_labels()
        self._open_group: int = -1
        self._thumbs: Dict[str, QPixmap] = {}
        self._index_by_abs: Dict[Path, int] = {}
        self._day_items: List[ViewportItem] = []
        self._single_index: int = -1
        # Decisions made while stepping the single view repaint their
        # grid cells on Back (the Day-Grid touched-set rule).
        self._touched: Set[str] = set()
        from mira.ui.media.photo_cache import photo_cache
        self._cache = photo_cache()
        # Register the event root (no sha map — export files are not
        # items): the export-thumb tier resolves grid requests against
        # `<root>/.cache/thumbs/exports/` (spec/63 slice 8), and a
        # straight-to-Share flow never passes a Pick surface that
        # would have registered it.
        self._cache.set_event_context(self._root, {})
        self._cache.scaled_pixmap_ready.connect(self._on_thumb_ready)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        top = QHBoxLayout()
        top.setContentsMargins(12, 8, 12, 8)
        title = QLabel("#" + session.name if session.cut_id else
                       tr("New Cut: {name}").replace("{name}", session.name))
        title.setObjectName("PageHeading")
        top.addWidget(title)
        top.addStretch(1)
        self._create_btn = QPushButton(
            tr("Save Cut") if session.cut_id else tr("Create Cut"))
        self._create_btn.setObjectName("Primary")
        self._create_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._create_btn.setToolTip(tr(
            "Commit the picked files as this Cut. Until now nothing is "
            "saved — walking away costs nothing."))
        self._create_btn.clicked.connect(self._on_create)
        top.addWidget(self._create_btn)
        # The session's leave button. Plain Back (Nelson 2026-06-12 sweep
        # — quit-and-return is always "Back", the tooltip explains the
        # side effect). The confirm-on-unsaved-decisions dialog rides
        # in _on_cancel.
        cancel = back_button()
        cancel.setToolTip(tr("Leave without saving anything."))
        cancel.clicked.connect(self._on_cancel)
        top.addWidget(cancel)
        # The shared Help control (Nelson 2026-06-12 UI round).
        self._help_btn = help_button()
        self._help_btn.setToolTip(tr("Keyboard shortcuts  (F1)"))
        self._help_btn.clicked.connect(self._show_shortcuts)
        top.addWidget(self._help_btn)
        outer.addLayout(top)

        # The budget strip — its own full-width row above the picker
        # stack (grid AND picture view both live under it).
        self._budget = CutBudgetLine()
        outer.addWidget(self._budget)

        self._stack = QStackedWidget()
        self._days = _DaysPanel()
        self._days.day_activated.connect(self._open_day)
        days_scroll = QScrollArea()
        days_scroll.setWidgetResizable(True)
        days_scroll.setWidget(self._days)
        self._stack.addWidget(days_scroll)                    # 0
        # The grid: border-zone click toggles Pick/Skip, center-zone
        # click opens the single view (spec/61 §2 step 6 grammar).
        grid_host = QWidget()
        grid_host_v = QVBoxLayout(grid_host)
        grid_host_v.setContentsMargins(0, 0, 0, 0)
        grid_host_v.setSpacing(6)
        grid_chrome = QHBoxLayout()
        grid_chrome.setContentsMargins(12, 8, 12, 0)
        grid_chrome.setSpacing(12)
        self._grid_back_btn = back_button()
        self._grid_back_btn.setToolTip(tr(
            "Back to the days list. (Esc)"))
        self._grid_back_btn.clicked.connect(self._back_to_days)
        grid_chrome.addWidget(self._grid_back_btn)
        self._grid_header = QLabel("")
        self._grid_header.setObjectName("DayGridHeader")
        grid_chrome.addWidget(self._grid_header, stretch=1)
        grid_host_v.addLayout(grid_chrome)
        self._grid = ThumbGrid(
            cell_size=_CELL_SIZE, two_zone_clicks=True)
        self._grid.cell_activated.connect(self._open_single)
        self._grid.cell_border_clicked.connect(self._toggle_cell)
        self._grid.back_requested.connect(self._back_to_days)
        grid_host_v.addWidget(self._grid, stretch=1)
        self._stack.addWidget(grid_host)                       # 1
        self._single = _SingleView()
        self._single.back_requested.connect(self._back_to_grid)
        self._single.state_requested.connect(self._set_relpath_state)
        self._single.current_changed.connect(self._on_single_stepped)
        self._single.fullscreen_requested.connect(self._toggle_fullscreen)
        self._stack.addWidget(self._single)                   # 2
        outer.addWidget(self._stack, stretch=1)

        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self._on_undo)
        QShortcut(QKeySequence("F11"), self, activated=self._toggle_fullscreen)
        QShortcut(QKeySequence("F1"), self, activated=self._show_shortcuts)

        self._refresh_days()
        self._budget.refresh(session)
        # Nelson eyeball 2026-06-12: Start must land ON PHOTOS — open the
        # first day's grid immediately; the days panel is one Back away.
        if self._groups:
            self._open_day(0)

    # ── data helpers ─────────────────────────────────────────────────

    def _load_day_labels(self) -> Dict[int, str]:
        labels: Dict[int, str] = {}
        try:
            for d in self._gw.trip_days():
                bits = [b for b in (d.date, d.location) if b]
                labels[d.day_number] = " · ".join(bits)
        except Exception:  # noqa: BLE001 — labels are decoration
            pass
        return labels

    def _files_of_open_group(self) -> List[SessionFile]:
        if 0 <= self._open_group < len(self._groups):
            return self._groups[self._open_group][1]
        return []

    def _grid_item_for(self, f: SessionFile) -> ThumbGridItem:
        """Build a :class:`ThumbGridItem` for one session file. The
        ledger picked-state drives the locked §5a state token (picked /
        skipped) so the 3px border colour reads the binary decision.

        Video files get the ``cluster_type='video'`` badge so they read
        as videos even before the poster frame finishes extracting (the
        photo cache short-circuits on .mp4 — without the badge the cell
        was a blank tile the user couldn't recognise as a video to
        pick or skip; Nelson 2026-06-19)."""
        state = "picked" if self._session.is_picked(f.export_relpath) else "skipped"
        is_video = f.kind == "video"
        return ThumbGridItem(
            pixmap=self._thumbs.get(f.export_relpath),
            state=state,
            payload=f.export_relpath,
            cluster_type="video" if is_video else None,
            cluster_count=1 if is_video else 0,
        )

    # ── days level ───────────────────────────────────────────────────

    def _refresh_days(self) -> None:
        self._days.set_days(self._groups, self._session, self._day_labels)

    def _back_to_days(self) -> None:
        self._refresh_days()
        self._stack.setCurrentIndex(0)

    # ── grid level ───────────────────────────────────────────────────

    def _open_day(self, group_index: int) -> None:
        self._open_group = group_index
        files = self._files_of_open_group()
        day, _ = self._groups[group_index]
        header = (tr("Undated") if day is None
                  else tr("Day {n}").replace("{n}", str(day)))
        extra = self._day_labels.get(day or -1, "")
        if extra:
            header += f" · {extra}"
        items = [self._grid_item_for(f) for f in files]
        self._grid_header.setText(header)
        self._grid.set_items(items)
        self._stack.setCurrentIndex(1)
        self._index_by_abs = {
            self._root / f.export_relpath: i for i, f in enumerate(files)}
        self._day_items = [
            ViewportItem(path=self._root / f.export_relpath,
                         kind=f.kind, payload=f)
            for f in files]
        self._request_missing_thumbs()

    def _request_missing_thumbs(self) -> None:
        """Queue async grid-thumb decodes (priority 1) for every file of
        the open day that has none yet. Navigation elsewhere may drop
        queued ones (generation rule) — callers re-invoke on re-entry.

        Photos ride the shared photo cache (Pillow decode). Videos
        ride the FFmpeg poster cache (``core.thumb_cache.ensure_thumb``)
        because the photo cache short-circuits on .mp4 (spec/63 slice 7
        + the 2026-06-15 'no log spam on video paths' guard) and would
        otherwise leave video tiles blank forever. The video decode
        runs on a small QTimer queue so the UI doesn't freeze opening
        a day with many unposted videos."""
        for f in self._files_of_open_group():
            if f.export_relpath in self._thumbs:
                continue
            if f.kind == "video":
                self._enqueue_video_poster(f)
            else:
                self._cache.request_scaled_pixmap(
                    self._root / f.export_relpath, _GRID_THUMB_TARGET,
                    priority=1)

    def _enqueue_video_poster(self, f: SessionFile) -> None:
        """Queue a video poster extraction. The QTimer fires every
        ~50 ms; each tick pulls one video off the queue and runs the
        FFmpeg-backed ``ensure_thumb`` synchronously. Each is a single
        ffmpeg invocation that typically returns in well under a second
        and is permanently cached on disk, so subsequent visits of the
        same day are instant. Re-entries that re-call
        ``_request_missing_thumbs`` skip files already in ``_thumbs``
        AND files already queued."""
        if not hasattr(self, "_video_thumb_pending"):
            self._video_thumb_pending: List[str] = []
            self._video_thumb_timer = QTimer(self)
            self._video_thumb_timer.setInterval(50)
            self._video_thumb_timer.timeout.connect(self._tick_video_thumb)
        relpath = f.export_relpath
        if relpath in self._video_thumb_pending:
            return
        self._video_thumb_pending.append(relpath)
        if not self._video_thumb_timer.isActive():
            self._video_thumb_timer.start()

    def _tick_video_thumb(self) -> None:
        """Process the next queued video poster. The whole cycle —
        FFmpeg extract + JPEG load + grid update — runs on the UI
        thread; the QTimer interval keeps it from looking like a hang
        even when multiple videos queue up."""
        if not self._video_thumb_pending:
            self._video_thumb_timer.stop()
            return
        relpath = self._video_thumb_pending.pop(0)
        try:
            from core.thumb_cache import ensure_thumb
            from mira.ui.media.image_loader import load_pixmap
            source = self._root / relpath
            thumb = ensure_thumb(
                event_root=self._root,
                source_video=source,
                source_rel_path=Path(relpath),
                item_id="cut_session_grid",
                position_ms=1000,
                fallback_position_ms=0)
            pm = load_pixmap(thumb)
        except Exception:                                              # noqa: BLE001
            log.warning(
                "cut session: video poster extract failed for %s",
                relpath, exc_info=True)
            pm = QPixmap()
        if pm.isNull():
            return
        self._thumbs[relpath] = pm
        # Update the cell IFF the same day is still on screen and this
        # file is in its current item list — navigation may have moved.
        files = self._files_of_open_group()
        for i, f in enumerate(files):
            if f.export_relpath == relpath:
                self._grid.set_pixmap(i, pm)
                break

    def _on_thumb_ready(self, path: Path, _pm: QPixmap, _native) -> None:
        """A scaled delivery landed — adopt it as a grid thumb iff it
        exists at the grid-thumb key (viewport-size deliveries for the
        same path probe as misses and fall through)."""
        index = self._index_by_abs.get(Path(path))
        if index is None:
            return
        hit = self._cache.get_scaled_pixmap_if_cached(
            path, _GRID_THUMB_TARGET)
        if hit is None:
            return
        files = self._files_of_open_group()
        if not (0 <= index < len(files)):
            return
        relpath = files[index].export_relpath
        self._thumbs[relpath] = hit[0]
        self._grid.set_pixmap(index, hit[0])

    def _toggle_cell(self, index: int) -> None:
        files = self._files_of_open_group()
        if not (0 <= index < len(files)):
            return
        relpath = files[index].export_relpath
        self._session.toggle(relpath)
        self._refresh_cell(index)
        self._budget.refresh(self._session)

    def _refresh_cell(self, index: int) -> None:
        files = self._files_of_open_group()
        if not (0 <= index < len(files)):
            return
        self._grid.update_item(index, self._grid_item_for(files[index]))

    def _back_to_grid(self) -> None:
        # Repaint every cell the single-view session decided on — not
        # just the last one (the Day-Grid touched-set rule).
        files = self._files_of_open_group()
        for i, f in enumerate(files):
            if f.export_relpath in self._touched:
                self._refresh_cell(i)
        self._touched.clear()
        self._stack.setCurrentIndex(1)
        self._grid.setFocus()
        self._request_missing_thumbs()      # gens may have dropped some

    # ── single level ─────────────────────────────────────────────────

    def _open_single(self, index: int) -> None:
        if not (0 <= index < len(self._day_items)):
            return
        self._single_index = index
        self._single.set_entries(
            self._day_items, index,
            picked_lookup=self._session.is_picked)
        self._stack.setCurrentIndex(2)
        self._single.setFocus()

    def _on_single_stepped(self, index: int) -> None:
        self._single_index = index

    def _set_relpath_state(self, relpath: str, picked: bool) -> None:
        """Single view P/X — the Picker's SET semantics, not a toggle."""
        self._session.set_state(relpath, picked)
        self._single.set_picked(self._session.is_picked(relpath))
        self._budget.refresh(self._session)
        self._touched.add(relpath)

    # ── page-level actions ───────────────────────────────────────────

    def _on_undo(self) -> None:
        relpath = self._session.undo()
        if relpath is None:
            return
        files = self._files_of_open_group()
        for i, f in enumerate(files):
            if f.export_relpath == relpath:
                self._refresh_cell(i)
                if self._stack.currentIndex() == 2 and i == self._single_index:
                    self._single.set_picked(self._session.is_picked(relpath))
                break
        self._budget.refresh(self._session)
        if self._stack.currentIndex() == 0:
            self._refresh_days()

    def _toggle_fullscreen(self) -> None:
        win = self.window()
        if win.isFullScreen():
            win.showNormal()
        else:
            win.showFullScreen()

    def _show_shortcuts(self) -> None:
        show_shortcuts(self, tr("Cut session"), [
            ("",                    tr("Decide")),
            (tr("P / X"),           tr("Pick / Skip the file")),
            (tr("Space"),           tr("Toggle Pick ⇄ Skip")),
            (tr("Click the border"), tr("Toggle Pick ⇄ Skip")),
            ("",                    tr("Navigate")),
            (tr("◀ / ▶"),            tr("Previous / next photo (single view)")),
            (tr("Esc"),              tr("Back one level (single → grid → "
                                        "days)")),
            ("",                    tr("Session")),
            (tr("Ctrl+Z"),          tr("Undo the last decision")),
            (tr("F11"),             tr("Fullscreen")),
            (tr("F1 · ?"),          tr("This help")),
        ])

    def _on_create(self) -> None:
        try:
            cut = self._session.commit(self._gw)
        except ValueError as exc:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.NoIcon)
            box.setWindowTitle(tr("Cut name problem"))
            code = str(exc)
            text = {
                "taken": tr("Another Cut took this name meanwhile — "
                            "rename and try again."),
                "reserved": tr("That name is a built-in Cut."),
                "empty": tr("The name has no usable characters."),
            }.get(code, code)
            box.setText(text)
            box.exec()
            return
        self.finished.emit(cut)

    def _on_cancel(self) -> None:
        if self._session._undo:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.NoIcon)
            box.setWindowTitle(tr("Leave the session?"))
            box.setText(tr("Your picks in this session are not saved. "
                           "Leave anyway?"))
            box.setStandardButtons(
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No)
            if box.exec() != QMessageBox.StandardButton.Yes:
                return
        self.cancelled.emit()
