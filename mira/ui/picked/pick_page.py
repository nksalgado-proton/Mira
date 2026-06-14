"""The Cull page — host for the days panel + Day Grid + photo / video surfaces.

**M4 rewire (spec/32 §4):** the days-list level of :class:`BucketNavigator` still
opens the page, but selecting a day no longer pushes a bucket list — it opens a
flat :class:`DayGridView` of cells, one per item / video / real cluster (burst /
focus / exposure). Centre-clicking a cell opens it (photo / video surface or
cluster sub-grid); border-clicking cycles its state in place. The photo +
video surfaces operate on a **synthetic single-item bucket** for standalone
cells; cluster cells open a sub-grid first (spec/32 Q2), and clicking a
sub-grid member opens the photo surface against the **real cluster bucket**
so the in-bucket arrow nav stays scoped to its members.

The legacy ``_go_bucket`` / cross-bucket photo-edge signals stay connected —
in Day Grid mode they hit no-op handlers (single-item buckets are also
first-and-last of their "day" by construction), preserving the photo
surface's signal contract while the new model owns the page-level routing.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PyQt6.QtWidgets import QMessageBox, QStackedWidget, QVBoxLayout, QWidget

from core.video_discovery import VideoItem
from mira.picked import (
    CullBucket,
    CullCell,
    CullCluster,
    PickDay,
    CullItem,
    pick_days,
    day_grid_cells,
)
from mira.picked.status import (
    STATE_PICKED,
    STATE_SKIPPED,
    cell_color_for_item,
    cluster_color,
    default_state_for,
    project_status,
)
from mira.gateway import Gateway
from mira.ui.base.bucket_navigator import (
    CULL_CONFIG,
    BucketNavigator,
)
from mira.ui.base.day_grid_cell import CellRenderData
from mira.ui.base.day_grid_view import DayGridView
from mira.ui.base.progress import run_with_progress
from mira.ui.picked.pick_photo_surface import (
    PickPhotoSurface, _PLAY_KINDS,
)
from mira.ui.picked.video_pick_page import VideoPickPage
from mira.ui.i18n import tr
from mira.ui.media.image_loader import load_pixmap

log = logging.getLogger(__name__)


# How many item thumbnails to decode per timer tick (matches GridView's
# Speed-is-King budget). ~20 ms between ticks → ~200 thumbs/sec on a warm
# disk; lazy enough that big days don't freeze the surface.
_THUMBS_PER_TICK = 4
_THUMB_TIMER_MS = 20


# Bug 3 (2026-06-13): the Day Grid used to leave cells forever-blank
# when ``ensure_thumb`` / ``ensure_photo_thumb`` raised (corrupt cached
# JPEG, unsupported codec, etc.). Now the loader substitutes one of
# these kind-aware placeholders so the cell at least communicates
# "this item exists" + (for videos) "it's a video, no preview".
# Module-level cache — built once on first need, reused after.
_PLACEHOLDER_CACHE: Dict[str, "object"] = {}


def _placeholder_pixmap(kind: str):
    """A 320×180 (16:9) tinted placeholder pixmap for an unrenderable cell.

    Videos get a ▶ glyph + "no preview" caption; photos / snapshots
    get the caption only. The pixmap is built once per kind and cached
    at module level — DayGridCell may render the same pixmap into many
    cells across a session.
    """
    if kind in _PLACEHOLDER_CACHE:
        return _PLACEHOLDER_CACHE[kind]
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import (
        QBrush, QColor, QFont, QPainter, QPainterPath, QPixmap,
    )
    w, h = 320, 180
    pm = QPixmap(w, h)
    pm.fill(QColor("#2A2A2E"))  # neutral dark — both themes survive it
    p = QPainter(pm)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # ▶ play triangle for videos; nothing for photos (just the caption).
        if kind == "video":
            tri = QPainterPath()
            cx, cy = w / 2, h / 2 - 8
            r = 28
            tri.moveTo(cx - r * 0.5, cy - r * 0.7)
            tri.lineTo(cx + r * 0.8, cy)
            tri.lineTo(cx - r * 0.5, cy + r * 0.7)
            tri.closeSubpath()
            p.setBrush(QBrush(QColor("#9CA3AF")))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(tri)
        # Caption row centred under the glyph.
        p.setPen(QColor("#9CA3AF"))
        font = QFont(p.font())
        font.setPixelSize(13)
        p.setFont(font)
        rect = QRectF(0, h / 2 + 24, w, 30)
        p.drawText(
            rect,
            int(Qt.AlignmentFlag.AlignCenter),
            tr("no preview"),
        )
    finally:
        p.end()
    _PLACEHOLDER_CACHE[kind] = pm
    return pm


def _capture_ts(eg, item_id: str, video_path: Path) -> datetime:
    """Return the corrected capture time for a video item (DB > mtime > epoch)."""
    try:
        db_item = eg.item(item_id)
        if db_item and db_item.capture_time_corrected:
            return datetime.fromisoformat(db_item.capture_time_corrected)
    except Exception:  # noqa: BLE001
        pass
    try:
        return datetime.fromtimestamp(video_path.stat().st_mtime)
    except OSError:
        return datetime(2000, 1, 1)


class PickPage(QWidget):
    """Hosts the days panel, Day Grid, cluster sub-grid, and photo / video
    surfaces for one event; owns the open gateway."""

    closed = pyqtSignal()                  # back to the per-event dashboard
    fullscreen_changed = pyqtSignal(bool)  # shell hides/restores its chrome

    _NAV = 0
    _DAY_GRID = 1
    _CLUSTER_GRID = 2
    _PHOTO = 3
    _VIDEO = 4
    _COMPARE = 5

    def __init__(self, gateway: Gateway, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.gateway = gateway
        self._eg = None
        self._event_id: Optional[str] = None
        self._days: List[PickDay] = []
        # Buckets are still useful for batch-op execution (day batch ops),
        # so we keep the flat list — purely an internal lookup, not displayed.
        self._all_buckets: List[CullBucket] = []
        self._camera_id: Optional[str] = None
        # The Day Grid level: cells of the currently-open day, then of the
        # currently-open cluster (sub-grid).
        self._current_day_number: Optional[int] = None
        self._current_day_cells: List[CullCell] = []
        # The Day Grid cell currently driving the photo / video surface or
        # cluster sub-grid (spec/32 §2.7 — linear nav uses this as the cursor).
        self._current_day_cell_idx: Optional[int] = None
        self._current_cluster: Optional[CullCluster] = None
        self._current_cluster_members: tuple = ()
        self._current_day_label: str = ""
        # Days whose status the user changed during the current Day Grid
        # session. On Back from the Day Grid, ONLY these days re-project from
        # fresh phase_states — no full pick_days walk (Nelson eyeball
        # 2026-06-04 — "going Back to the days list is also slow").
        self._dirty_days: set = set()
        # Synthetic bucket currently driving the photo / video surface — used
        # when the host needs to recompute the cell's render data after a cycle.
        self._current_bucket: Optional[CullBucket] = None
        # Item-ids the user has opened during the current photo / video surface
        # session.  Day-Grid item arrow-nav re-enters via ``_open_photo_item`` /
        # ``_open_video_item`` for each step, so ``_current_bucket`` only points
        # at the LAST item — without this set, every other touched item would
        # leave the Day Grid with a stale border (and stale visited tick).
        # Cleared at the end of every photo / video surface Back.
        self._items_touched_in_surface: set = set()
        # The video the video surface is currently showing — the target of
        # its whole-video P/D cycle (spec/56: Pick decides the WHOLE video;
        # the page is pure presentation, this page persists).
        self._current_video_item_id: Optional[str] = None
        # Track whether the photo surface was opened from the Day Grid or the
        # cluster sub-grid so Back returns to the right level.
        self._surface_came_from: int = self._DAY_GRID
        # Configured default state for un-decided items at Cull.
        self._phase_default = STATE_SKIPPED
        # Defer the heavy day/cluster-open work to a zero-delay timer so the
        # "Preparing the page…" overlay paints before the work runs (Nelson
        # 2026-06-04 → 2026-06-05). Tests set this False to keep the
        # ``_on_day_activated`` → set_cells chain synchronous.
        self._defer_open_work: bool = True

        # Thumbnail loader (lazy; one widget index → resolved pixmap) +
        # session-level QPixmap cache so day-switch round trips don't re-decode
        # the same photos (Nelson perf eyeball 2026-06-04). Bounded by the
        # session — cleared on _close_gateway.
        self._thumb_pending: List[tuple[str, str, str]] = []   # (target,grid,item_id)
        self._thumb_pixmap_cache: Dict = {}                    # item_id → QPixmap
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setInterval(_THUMB_TIMER_MS)
        self._thumb_timer.timeout.connect(self._load_some_thumbs)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._stack = QStackedWidget()

        # Days panel — BucketNavigator with day_grid_mode=True so clicking a
        # day emits ``day_activated(day_number)`` instead of rendering the
        # legacy bucket list.
        # Nelson 2026-06-07 — event-scope "Pick all days" / "Skip all days"
        # on the days bar (one click marks every captured item in the
        # event). Day-scope ops on the day cards stay (already in
        # CULL_CONFIG); day-grid-internal ops light up on the DayGridView
        # below.
        nav_config = replace(
            CULL_CONFIG, day_grid_mode=True,
            show_pick_all_button=True,
            show_skip_all_button=True,
        )
        self.navigator = BucketNavigator(config=nav_config)
        self.navigator.pick_all_days_requested.connect(
            lambda: self._on_event_scope_batch("picked"))
        self.navigator.skip_all_days_requested.connect(
            lambda: self._on_event_scope_batch("skipped"))
        self.navigator.day_activated.connect(self._on_day_activated)
        self.navigator.back_requested.connect(self._on_back)
        self.navigator.batch_op_requested.connect(self._on_batch_op)
        self.navigator.clear_marks_requested.connect(self._on_clear_marks)

        # Day Grid + cluster sub-grid (same widget, two slots).
        # Nelson 2026-06-06: "Start a new pass…" lives on the day grid
        # itself — same handler the navigator's button uses.
        # Nelson 2026-06-07: + day-scope Pick all / Skip all on the day
        # grid top bar (acts on every cell the user is currently
        # looking at).
        self.day_grid = DayGridView(
            show_clear_marks_button=True,
            show_pick_all_button=True,
            show_skip_all_button=True,
            show_compare_button=True,
        )
        self.day_grid.back_requested.connect(self._on_day_grid_back)
        self.day_grid.cell_activated.connect(self._on_day_cell_activated)
        self.day_grid.clear_marks_requested.connect(self._on_clear_marks)
        self.day_grid.cell_border_clicked.connect(self._on_day_cell_border)
        self.day_grid.compare_requested.connect(
            lambda: self._on_compare_requested(self._DAY_GRID))
        self.day_grid.pick_all_requested.connect(
            lambda: self._on_current_day_batch("picked"))
        self.day_grid.skip_all_requested.connect(
            lambda: self._on_current_day_batch("skipped"))

        # Cluster sub-grid: arrow nav routes back to the conductor so the
        # cursor steps PAST the cluster (spec/32 Q2 — Nelson 2026-06-04).
        # ``show_play_button`` surfaces the cluster slideshow at the
        # sub-grid level (Nelson 2026-06-09 follow-up to the Compare
        # redesign — Play used to be one click in via grid mode; now
        # the sub-grid replaces grid mode and the affordance reappears
        # here). ``show_pick_all_button`` / ``show_skip_all_button``
        # carry the day-grid's batch ops down to the cluster scope so
        # the user can mass-pick / mass-skip every member of the open
        # cluster without leaving the sub-grid.
        self.cluster_grid = DayGridView(
            enable_arrow_nav=True,
            show_compare_button=True,
            show_play_button=True,
            show_pick_all_button=True,
            show_skip_all_button=True,
        )
        self.cluster_grid.compare_requested.connect(
            lambda: self._on_compare_requested(self._CLUSTER_GRID))
        self.cluster_grid.play_requested.connect(self._on_cluster_play)
        self.cluster_grid.pick_all_requested.connect(
            lambda: self._on_cluster_batch("picked"))
        self.cluster_grid.skip_all_requested.connect(
            lambda: self._on_cluster_batch("skipped"))
        self.cluster_grid.back_requested.connect(self._on_cluster_back)
        self.cluster_grid.cell_activated.connect(self._on_cluster_cell_activated)
        self.cluster_grid.cell_border_clicked.connect(self._on_cluster_cell_border)
        self.cluster_grid.navigate_at_edge.connect(self._navigate)

        # Photo + video surfaces.
        self.photo = PickPhotoSurface()
        self.photo.back_requested.connect(self._on_photo_back)
        self.photo.fullscreen_changed.connect(self.fullscreen_changed.emit)
        # Legacy cross-bucket signals are no-ops in Day Grid mode — the
        # surface's ``nav_context`` ("day_grid" / "cluster") suppresses them.
        self.photo.prev_bucket_requested.connect(lambda: None)
        self.photo.next_bucket_requested.connect(lambda: None)
        self.photo.prev_bucket_from_first_photo.connect(lambda: None)
        self.photo.next_bucket_from_last_photo.connect(lambda: None)
        # spec/32 §2.7 — the surface fires this at first/last item in Day Grid
        # context; we step the day-cell cursor and open the next cell.
        self.photo.navigate_at_edge.connect(self._navigate)
        # The "Reset all Compare" button on the photo surface retired
        # with the 2026-06-09 Compare redesign (Nelson) — the new
        # ComparePage handles the compare flow at the navigation grid
        # level. Gateway helper ``reset_compare_in_day`` stays for any
        # future caller.

        self.video = VideoPickPage()
        self.video.back_requested.connect(self._on_video_back)
        self.video.fullscreen_changed.connect(self.fullscreen_changed.emit)
        # In Day Grid mode the video page's legacy bucket-step signals (still
        # emitted by its keyboard handlers) are translated into Day Grid cell
        # steps via the same conductor (spec/32 §2.7).
        self.video.prev_bucket_requested.connect(lambda: self._navigate(-1))
        self.video.next_bucket_requested.connect(lambda: self._navigate(+1))
        # spec/63 §4: the WHOLE video's decision verbs (P/X/Space/C +
        # border click). The page is pure presentation — this page
        # persists the decision and pushes the new state back so the
        # border repaints. Videos are a binary ledger (no video compare
        # surface), so "cycle" degrades to "toggle" (§4's rule).
        self.video.decision_verb_requested.connect(self._on_video_decision_verb)

        # Compare surface (Nelson 2026-06-09) — opened from the day grid
        # or the cluster sub-grid when the user clicks Compare or hits
        # C with 2+ Compare-state photos visible.
        from mira.ui.picked.compare_page import ComparePage
        self.compare_page = ComparePage()
        self.compare_page.quit_requested.connect(self._on_compare_quit)

        self._compare_came_from: Optional[int] = None

        # "C" — scoped to each grid widget so the shortcut fires only
        # when that grid has keyboard focus (and thus is visible). Maps
        # to the same handler the button click triggers.
        sc_day = QShortcut(QKeySequence("C"), self.day_grid)
        sc_day.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_day.activated.connect(
            lambda: self._on_compare_requested(self._DAY_GRID))
        sc_cluster = QShortcut(QKeySequence("C"), self.cluster_grid)
        sc_cluster.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_cluster.activated.connect(
            lambda: self._on_compare_requested(self._CLUSTER_GRID))

        self._stack.addWidget(self.navigator)     # _NAV          = 0
        self._stack.addWidget(self.day_grid)      # _DAY_GRID     = 1
        self._stack.addWidget(self.cluster_grid)  # _CLUSTER_GRID = 2
        self._stack.addWidget(self.photo)         # _PHOTO        = 3
        self._stack.addWidget(self.video)         # _VIDEO        = 4
        self._stack.addWidget(self.compare_page)  # _COMPARE      = 5
        outer.addWidget(self._stack)

        # spec/32 §2 — "Preparing the page…" overlay (Nelson 2026-06-04).
        # A child widget so its paint is synchronous via ``repaint()`` — no
        # window-manager queue, no nested event loop. Reliably visible
        # before the heavy ``set_cells`` widget creation runs, even when
        # event processing inside the click handler is constrained.
        from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel
        self._loading_overlay = QFrame(self)
        self._loading_overlay.setObjectName("SelectLoadingOverlay")
        self._loading_overlay.setAutoFillBackground(True)
        self._loading_overlay.setStyleSheet(
            "QFrame#CullLoadingOverlay {"
            "  background-color: rgba(0, 0, 0, 140);"
            "}"
            "QLabel#CullLoadingLabel {"
            "  color: #ffffff;"
            "  font-size: 16px;"
            "  font-weight: 600;"
            "  background-color: rgba(40, 40, 40, 235);"
            "  border-radius: 10px;"
            "  padding: 18px 28px;"
            "}"
        )
        overlay_layout = QHBoxLayout(self._loading_overlay)
        overlay_layout.setContentsMargins(0, 0, 0, 0)
        overlay_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label = QLabel(tr("Preparing the page…"))
        self._loading_label.setObjectName("SelectLoadingLabel")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overlay_layout.addWidget(self._loading_label)
        self._loading_overlay.hide()

    # ── lifecycle ─────────────────────────────────────────────────────

    def open_event(self, event_id: str, camera_id: Optional[str] = None) -> bool:
        self._close_gateway()
        try:
            self._eg = self.gateway.open_event(event_id)
        except Exception:  # noqa: BLE001
            log.exception("could not open event %s for cull", event_id)
            QMessageBox.warning(
                self, tr("Pick"),
                tr("This event could not be opened for picking."))
            return False
        self._event_id = event_id
        self._camera_id = camera_id
        self._phase_default = default_state_for(self.gateway.settings, "pick")
        self.navigator.set_untouched_merge_target(self._phase_default)
        self._dirty_days = set()
        days = self._build_days_with_progress()
        if days is None:
            self._close_gateway()
            return False
        self._days = days
        self._all_buckets = [b for d in self._days for b in d.buckets]
        self.navigator.set_days(self._days)
        self._stack.setCurrentIndex(self._NAV)
        self.navigator.setFocus()
        self._seed_photo_proxies()
        return True

    def _seed_photo_proxies(self) -> None:
        """Whole-event proxy seed (spec/63 slice 7): queue every photo
        item for the background proxy builder at event open, so the
        screen-copy tier fills quietly while the user is still on the
        day grid. One SQL pass + a deque append — milliseconds; the
        builds themselves run on the builder thread."""
        if self._eg is None:
            return
        try:
            from mira.ui.media.photo_cache import photo_cache
            event_root = Path(self._eg.event_root)
            pairs = [
                (event_root / it.origin_relpath, it.sha256)
                for it in self._eg.items(kind="photo")
                if it.origin_relpath and it.sha256
            ]
            if pairs:
                photo_cache().seed_proxies(event_root, pairs)
        except Exception:                                          # noqa: BLE001
            log.exception("whole-event proxy seeding failed")

    def _refresh_days(self) -> None:
        self._days = self._build_days()
        self._all_buckets = [b for d in self._days for b in d.buckets]
        self.navigator.set_days(self._days)

    def _mark_current_day_dirty(self) -> None:
        """Record that the current day's status moved (Nelson perf fix
        2026-06-04). The days panel reads the updated rollup on Back."""
        if self._current_day_number is not None:
            self._dirty_days.add(self._current_day_number)

    def _fast_reproject_days_status(self, day_numbers) -> None:
        """Re-project status for the given ``day_numbers`` from fresh
        ``phase_states`` — no EXIF scan, no fingerprint hashing, no full
        ``pick_days`` walk. The bucket structure (item_ids per bucket) is
        stable across phase-state changes, so a pure in-memory roll-up of
        ``self._days`` matches what pick_days would produce — orders of
        magnitude faster for the common "user touched one day" case.
        """
        if self._eg is None or not self._days or not day_numbers:
            return
        from mira.picked.status import project_status, rollup_status
        targets = set(day_numbers)
        phase_states = self._eg.phase_states("pick")
        new_days: list = []
        for day in self._days:
            if day.day_number not in targets:
                new_days.append(day)
                continue
            new_buckets: list = []
            for b in day.buckets:
                soft = None
                try:
                    soft = self._eg.bucket(b.bucket_key, "pick")
                except Exception:  # noqa: BLE001
                    pass
                new_status = project_status(
                    list(b.item_ids), phase_states, soft)
                new_buckets.append(CullBucket(
                    bucket_key=b.bucket_key, kind=b.kind, title=b.title,
                    items=b.items, status=new_status,
                    detection_source=b.detection_source, camera=b.camera,
                ))
            new_day_status = rollup_status([b.status for b in new_buckets])
            new_days.append(PickDay(
                day_number=day.day_number, label=day.label,
                buckets=tuple(new_buckets), status=new_day_status,
            ))
        self._days = new_days
        self._all_buckets = [b for d in self._days for b in d.buckets]

    def _build_days(self) -> List[PickDay]:
        if self._eg is None:
            return []
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return pick_days(self._eg, phase="pick", camera_id=self._camera_id)
        finally:
            QGuiApplication.restoreOverrideCursor()

    def _build_days_with_progress(self) -> Optional[List[PickDay]]:
        if self._eg is None:
            return []

        def work(report):
            def on_day(done: int, total: int, day_number, n: int) -> None:
                if day_number is None:
                    report(done, total, tr("Finishing…"))
                    return
                report(
                    done, total,
                    tr("Reading day {n} — {c} item(s)…")
                    .replace("{n}", str(day_number)).replace("{c}", str(n)),
                )
            return pick_days(
                self._eg, phase="pick", progress=on_day, camera_id=self._camera_id)

        ok, result = run_with_progress(
            self, tr("Opening Pick"), work, label=tr("Reading the event…"))
        if not ok:
            QMessageBox.warning(
                self, tr("Pick"), tr("This event could not be read for picking."))
            return []
        if not self._show_cull_summary(result):
            return None
        return result

    def _show_cull_summary(self, days: List[PickDay]) -> bool:
        # Cell-based summary (spec/32 §2.5 yellow accounting handled at day
        # level via the existing BucketStatus rollup).
        n_buckets = sum(len(d.buckets) for d in days)
        photos = videos = 0
        kept = candidate = discarded = untouched = 0
        for d in days:
            kept += d.status.kept
            candidate += d.status.candidate
            discarded += d.status.discarded
            untouched += d.status.untouched
            for b in d.buckets:
                for ci in b.items:
                    if getattr(ci, "kind", "photo") == "video":
                        videos += 1
                    else:
                        photos += 1
        msg = (
            tr("{days} day(s)  ·  {buckets} bucket(s)")
            .replace("{days}", str(len(days))).replace("{buckets}", str(n_buckets))
            + "\n"
            + tr("{p} photo(s)  ·  {v} video(s)")
            .replace("{p}", str(photos)).replace("{v}", str(videos))
            + "\n\n"
            + tr("Picked {k}  ·  Compare {c}  ·  Skipped {d}  ·  Untouched {u}")
            .replace("{k}", str(kept)).replace("{c}", str(candidate))
            .replace("{d}", str(discarded)).replace("{u}", str(untouched))
            + "\n"
            + tr("(untouched items default to Skip)")
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setWindowTitle(tr("Ready to pick"))
        box.setText(msg)
        cont = box.addButton(
            tr("Continue to picker"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("Quit"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cont)
        box.exec()
        return box.clickedButton() is cont

    def _close_gateway(self) -> None:
        self._thumb_timer.stop()
        self._thumb_pending.clear()
        self._thumb_pixmap_cache.clear()
        if self._eg is not None:
            try:
                self._eg.close()
            except Exception:  # noqa: BLE001
                log.exception("error closing event gateway")
            self._eg = None

    # ── navigator → Day Grid ──────────────────────────────────────────

    def _on_day_activated(self, day_number) -> None:
        """Days panel day card clicked (day_grid_mode signal)."""
        if self._eg is None:
            return
        self._open_day(day_number)

    def _open_day(self, day_number) -> None:
        """Build and show the flat Day Grid for ``day_number`` (spec/32 §2).

        The expensive work (widget construction for N cells) runs inside the
        nested event loop of a small modal "Preparing the page…" dialog so
        the dialog actually paints first. Without this, the click handler
        is one synchronous transaction and the user sees nothing —
        ``processEvents()`` alone proved unreliable (Nelson 2026-06-04)."""
        if self._eg is None:
            return
        day = next(
            (d for d in self._days if d.day_number == day_number), None)
        if day is None:
            return

        def _work():
            import time
            t0 = time.perf_counter()
            self._current_day_number = day_number
            self._current_day_label = day.label
            # Pass the already-built ``self._days`` so day_grid_cells skips
            # its own ``pick_days`` walk. Opening a day becomes an
            # in-memory projection + phase_states query.
            cells = day_grid_cells(
                self._eg, day_number, phase="pick",
                camera_id=self._camera_id,
                days=self._days,
                default_state=self._phase_default,
            )
            t_cells = time.perf_counter()
            self._current_day_cells = list(cells)
            self._current_day_cell_idx = None
            render = [CellRenderData(cell=c, thumbnail=None) for c in cells]
            self.day_grid.set_header(self._day_header(day))
            t_pre_set = time.perf_counter()
            self.day_grid.set_cells(render)
            t_set = time.perf_counter()
            self._stack.setCurrentIndex(self._DAY_GRID)
            self.day_grid.setFocus()
            self._enqueue_thumbnails(self.day_grid, self._current_day_cells)
            t_done = time.perf_counter()
            log.debug(
                "open_day perf: n=%d "
                "day_grid_cells=%.0fms set_cells_pre=%.0fms set_cells=%.0fms "
                "enqueue=%.0fms total_sync=%.0fms",
                len(cells),
                (t_cells - t0) * 1000,
                (t_pre_set - t_cells) * 1000,
                (t_set - t_pre_set) * 1000,
                (t_done - t_set) * 1000,
                (t_done - t0) * 1000,
            )

        self._run_with_preparing_dialog(tr("Preparing the page…"), _work)

    def _run_with_preparing_dialog(self, text: str, work_fn) -> None:
        """Show the "Preparing the page…" overlay, RETURN from the click
        handler, then run ``work_fn`` from a zero-delay QTimer.

        The earlier "show overlay + repaint + processEvents + run work
        synchronously" pattern never made the overlay visible — Windows Qt
        does not paint a child widget while the originating click event is
        still on the call stack, no matter how many repaint/processEvents
        we throw at it. The fix (Nelson 2026-06-04 → 2026-06-05) is to
        defer the work entirely: show the overlay, schedule the real work
        for the next event-loop turn, return. The click handler unwinds
        → Qt paints the overlay → the timer fires → the work runs.

        Tests set ``_defer_open_work=False`` so the chain stays synchronous
        and assertions can fire immediately after the activation call."""
        self._loading_label.setText(text)
        self._loading_overlay.setGeometry(self.rect())
        self._loading_overlay.raise_()
        self._loading_overlay.show()

        def _run_and_hide() -> None:
            try:
                work_fn()
            finally:
                self._loading_overlay.hide()

        if self._defer_open_work:
            QTimer.singleShot(0, _run_and_hide)
        else:
            _run_and_hide()

    def resizeEvent(self, ev):  # noqa: N802
        super().resizeEvent(ev)
        if hasattr(self, "_loading_overlay"):
            self._loading_overlay.setGeometry(self.rect())

    def _day_header(self, day: PickDay) -> str:
        n = len(self._current_day_cells) or sum(b.count for b in day.buckets)
        return tr("{label} — {n} cell(s)").replace(
            "{label}", day.label).replace("{n}", str(n))

    # ── Day Grid clicks ───────────────────────────────────────────────

    def _on_day_cell_activated(self, idx: int) -> None:
        if not (0 <= idx < len(self._current_day_cells)):
            return
        # spec/32 §2.7: clicking a Day Grid cell sets the cell cursor — linear
        # ← / → from inside the surface will step from here.
        self._current_day_cell_idx = idx
        cell = self._current_day_cells[idx]
        if cell.is_cluster and cell.cluster is not None:
            self._open_cluster(cell.cluster)
        elif cell.item_kind == "video" and cell.item_id is not None:
            self._open_video_item(cell.item_id, came_from=self._DAY_GRID)
        elif cell.item_id is not None:
            self._open_photo_item(cell.item_id, came_from=self._DAY_GRID)

    def _on_day_cell_border(self, idx: int) -> None:
        if not (0 <= idx < len(self._current_day_cells)):
            return
        cell = self._current_day_cells[idx]
        if cell.is_cluster and cell.cluster is not None:
            self._cycle_cluster(cell.cluster)
        elif cell.item_id is not None:
            self._cycle_item(cell.item_id, cell.item_kind)
        # After a state change refresh just this cell + mark day dirty so
        # the days panel re-projects on Back (Nelson perf 2026-06-04).
        self._refresh_day_cell(idx)
        self._mark_current_day_dirty()

    # ── Cluster sub-grid ──────────────────────────────────────────────

    def _open_cluster(self, cluster: CullCluster) -> None:
        """Open the cluster in the cluster sub-grid (Nelson 2026-06-09
        redesign): a :class:`DayGridView` showing per-member cells with
        the K/D/C border-click + cell-activate routing the day grid
        uses. Replaces the prior "open the photo surface in grid mode"
        rule — the photo surface's grid mode retired with this commit
        because the new Compare flow gives the user a dedicated compare
        grid (:class:`ComparePage`), and the cluster sub-grid is the
        natural place to drill into individual members.

        Fires the cluster visited tick as before — opening the cluster
        IS the "I drilled into this cluster" act."""
        # spec/32 §2.10 — the cluster tick fires the moment the cluster opens.
        if self._eg is not None:
            try:
                self._eg.set_bucket_browsed(cluster.bucket_key, "pick")
            except Exception:  # noqa: BLE001
                log.exception(
                    "set_bucket_browsed failed for cluster %s",
                    cluster.bucket_key)

        def _work():
            if self._eg is None:
                return
            self._current_cluster = cluster
            self._current_cluster_members = cluster.members
            # Reflect the visited tick on the parent day-grid cell so the
            # ✓ is in place when the user Backs out.
            for i, c in enumerate(self._current_day_cells):
                if c.is_cluster and c.cluster is not None \
                        and c.cluster.bucket_key == cluster.bucket_key:
                    self._current_day_cell_idx = i
                    new_cluster_cell = CullCell(
                        end_time=c.end_time, color=c.color,
                        cluster=c.cluster, visited=True,
                    )
                    self._current_day_cells[i] = new_cluster_cell
                    self._refresh_day_cell(i)
                    break
            # Populate the cluster sub-grid with per-member cells and
            # show it. The widget already wires border-click cycling,
            # centre-click drill, Compare button, visited ticks.
            member_cells = self._build_cluster_member_cells(cluster)
            render = [CellRenderData(cell=c, thumbnail=None)
                      for c in member_cells]
            self.cluster_grid.set_header(self._cluster_header(cluster))
            self.cluster_grid.set_cells(render)
            # Play affordance — only the photo surface's playable cluster
            # kinds (burst + focus / exposure bracket) get the button.
            # Repeat clusters skip it (legacy semantics: rapid-fire phone
            # doublets aren't a sweep to watch).
            self.cluster_grid.set_play_button_visible(
                cluster.kind in _PLAY_KINDS)
            self._stack.setCurrentIndex(self._CLUSTER_GRID)
            self.cluster_grid.setFocus()
            self._enqueue_thumbnails(self.cluster_grid, member_cells)

        self._run_with_preparing_dialog(tr("Preparing the page…"), _work)

    def _cluster_header(self, cluster: CullCluster) -> str:
        kind = {
            "burst": tr("Burst"),
            "focus_bracket": tr("Focus bracket"),
            "exposure_bracket": tr("Exposure bracket"),
        }.get(cluster.kind, cluster.kind)
        return tr("{kind} — {n} photo(s)").replace(
            "{kind}", kind).replace("{n}", str(cluster.count))

    def _build_cluster_member_cells(
        self, cluster: CullCluster,
    ) -> List[CullCell]:
        """Per-member cells for the sub-grid, with live colour + visited tick
        (spec/32 §2.10).  One batched ``items_visited_for_day`` query keys the
        members' ticks at sub-grid open; subsequent opens flip individual bits
        in place via :meth:`_open_cluster_bucket`."""
        if self._eg is None:
            return []
        phase_states = self._eg.phase_states("pick")
        try:
            visited_items = self._eg.items_visited_for_day(
                self._current_day_number, "pick")
        except Exception:  # noqa: BLE001
            visited_items = set()
        return [
            CullCell(
                end_time=ci.capture_time_corrected or "",
                color=cell_color_for_item(
                    ci.item_id, ci.kind, "pick", phase_states,
                    default_state=self._phase_default),
                item_id=ci.item_id, item_kind=ci.kind,
                visited=ci.item_id in visited_items,
            )
            for ci in cluster.members
        ]

    def _on_cluster_cell_activated(self, idx: int) -> None:
        if self._current_cluster is None:
            return
        if not (0 <= idx < len(self._current_cluster_members)):
            return
        member = self._current_cluster_members[idx]
        # Opens against the REAL cluster bucket so arrow nav walks members.
        self._open_cluster_bucket(self._current_cluster, entry_idx=idx)

    def _on_cluster_cell_border(self, idx: int) -> None:
        if self._current_cluster is None:
            return
        if not (0 <= idx < len(self._current_cluster_members)):
            return
        member = self._current_cluster_members[idx]
        self._cycle_item(member.item_id, member.kind)
        self._refresh_cluster_cell(idx)
        # Re-render the parent Day Grid cell — the cluster's aggregate
        # colour may have moved (kept+discarded becomes MIXED, etc.).
        self._refresh_day_cell_for_cluster(self._current_cluster.bucket_key)
        self._mark_current_day_dirty()

    def _on_cluster_play(self) -> None:
        """Launch the cluster slideshow from the sub-grid top bar.

        Opens the cluster's real bucket on the photo surface at entry
        0; the surface's own playable-frame logic in
        :meth:`PickPhotoSurface._toggle_film` walks past skipped frames
        and rewinds to the first playable when the cursor is at the
        end. Safe no-op when no cluster is current (the signal can't
        normally fire then — the sub-grid only renders with a cluster
        loaded — but the guard keeps us honest)."""
        if self._current_cluster is None:
            return
        self._open_cluster_bucket(self._current_cluster, entry_idx=0)
        self.photo.start_play()

    # ── Open item / cluster bucket in photo or video surface ──────────

    def _open_photo_item(self, item_id: str, *, came_from: int) -> None:
        """Open a single photo / snapshot in the photo surface."""
        cull_item = self._cull_item(item_id)
        if cull_item is None:
            return
        # spec/32 §2.10 item tick — fires on centre-click open.
        if self._eg is not None:
            try:
                self._eg.set_item_visited(item_id, "pick")
            except Exception:  # noqa: BLE001
                log.exception("set_item_visited failed for %s", item_id)
        self._items_touched_in_surface.add(item_id)
        bucket = self._synthetic_bucket(cull_item)
        self._open_in_photo_surface(
            bucket, came_from=came_from, entry_idx=0)

    def _open_video_item(self, item_id: str, *, came_from: int) -> None:
        """Open a single video in the video surface — watch + whole-video
        P/D (spec/56: Pick is one uniform decision pass; clip authoring
        lives in the Edit workshop now)."""
        if self._eg is None:
            return
        cull_item = self._cull_item(item_id)
        if cull_item is None:
            return
        # spec/32 §2.10 item tick — fires on centre-click open.
        try:
            self._eg.set_item_visited(item_id, "pick")
        except Exception:  # noqa: BLE001
            log.exception("set_item_visited failed for %s", item_id)
        self._items_touched_in_surface.add(item_id)
        self._surface_came_from = came_from
        bucket = self._synthetic_bucket(cull_item)
        self._current_bucket = bucket
        self._current_video_item_id = cull_item.item_id
        ts = _capture_ts(self._eg, cull_item.item_id, cull_item.path)
        video_item = VideoItem(
            path=cull_item.path,
            source_folder=cull_item.path.parent.name, timestamp=ts,
            poster=self._poster_for_item(cull_item.item_id))
        # spec/32: video cells live only at the Day Grid level (no video
        # clusters). nav_context = "day_grid".
        self.video.load([video_item], nav_context="day_grid")
        # Paint the video's CURRENT decision on the media border (an
        # un-decided item reads as the configured phase default).
        ps = self._eg.phase_state(cull_item.item_id, "pick")
        self.video.set_binary_state(ps.state if ps else self._phase_default)
        self._stack.setCurrentIndex(self._VIDEO)
        self.video.setFocus()

    def _poster_for_item(self, item_id: str):
        """The cached Day-Grid poster for a video (spec/59 black-frame
        guarantee) — cache-only, never extracts; None when the grid
        hasn't populated it yet."""
        if self._eg is None:
            return None
        try:
            item = self._eg.item(item_id)
            if item is None or not item.origin_relpath:
                return None
            from core.thumb_cache import poster_path_if_cached
            return poster_path_if_cached(
                Path(self._eg.event_root), Path(item.origin_relpath))
        except Exception:  # noqa: BLE001 — poster is best-effort display
            log.debug("poster lookup failed for %s", item_id)
            return None

    def _on_video_decision_verb(self, verb: str) -> None:
        """A decision verb from the video surface (spec/63 §4): "pick" /
        "skip" SET the state; "toggle" and "cycle" flip Pick⇄Skip — the
        WHOLE video, one decision (spec/56). Videos carry no Compare
        (no video compare surface), so the cycle degrades to the toggle
        per the §4 binary-ledger rule."""
        item_id = self._current_video_item_id
        if self._eg is None or item_id is None:
            return
        if verb == "pick":
            nxt = STATE_PICKED
        elif verb == "skip":
            nxt = STATE_SKIPPED
        else:                               # "toggle" / "cycle"
            ps = self._eg.phase_state(item_id, "pick")
            cur = ps.state if ps else self._phase_default
            nxt = STATE_SKIPPED if cur == STATE_PICKED else STATE_PICKED
        try:
            self._eg.set_phase_state(item_id, "pick", nxt)
        except Exception:  # noqa: BLE001
            log.exception("video decision verb failed for %s", item_id)
            return
        self.video.set_binary_state(nxt)

    def _open_cluster_bucket(
        self, cluster: CullCluster, *, entry_idx: int = 0,
    ) -> None:
        """Open the cluster's real bucket in the photo surface."""
        if self._eg is None:
            return
        phase_states = self._eg.phase_states("pick")
        bucket = CullBucket(
            bucket_key=cluster.bucket_key,
            kind=cluster.kind,
            title=cluster.title,
            items=cluster.members,
            status=project_status(
                [m.item_id for m in cluster.members],
                phase_states,
                self._eg.bucket(cluster.bucket_key, "pick"),
            ),
            detection_source=cluster.detection_source,
            camera=cluster.camera,
        )
        # spec/32 §2.10 — opening a sub-grid member into the photo surface
        # marks that member visited.  Reflect it in the sub-grid cell right
        # away so the tick is present when the user Backs out.
        if 0 <= entry_idx < len(cluster.members):
            member_id = cluster.members[entry_idx].item_id
            try:
                self._eg.set_item_visited(member_id, "pick")
            except Exception:  # noqa: BLE001
                log.exception("set_item_visited failed for %s", member_id)
            self._items_touched_in_surface.add(member_id)
            current = self.cluster_grid.cell_at(entry_idx)
            if current is not None:
                old = current.render_data().cell
                if not old.visited:
                    self.cluster_grid.update_cell(
                        entry_idx,
                        CellRenderData(
                            cell=CullCell(
                                end_time=old.end_time, color=old.color,
                                item_id=old.item_id, item_kind=old.item_kind,
                                cluster=old.cluster, visited=True,
                            ),
                            thumbnail=current.render_data().thumbnail,
                        ),
                    )
        self._open_in_photo_surface(
            bucket, came_from=self._CLUSTER_GRID, entry_idx=entry_idx)

    def _open_in_photo_surface(
        self, bucket: CullBucket, *, came_from: int, entry_idx: int,
    ) -> None:
        if self._eg is None:
            return
        self._surface_came_from = came_from
        self._current_bucket = bucket
        try:
            self._eg.set_bucket_browsed(bucket.bucket_key, "pick")
        except Exception:  # noqa: BLE001
            log.exception("set_bucket_browsed failed for %s", bucket.bucket_key)
        nav_context, suffix = self._photo_nav_context_and_label(came_from)
        # Nelson 2026-06-06: feed the REAL day position so the compact_row
        # X/Y label reflects "where am I in the day". Day-grid context →
        # current cell index / total cells; cluster context → 1/1 (the
        # in-cluster i/N takes over).
        if nav_context == "day_grid" and self._current_day_cells:
            day_idx = (self._current_day_cell_idx or 0) + 1
            day_total = len(self._current_day_cells)
        else:
            day_idx, day_total = 1, 1
        self.photo.load(
            self._eg, bucket, "pick",
            bucket_index=day_idx, bucket_count=day_total,
            entry_override=entry_idx,
            # Day Grid linear nav is PickPage's job via ``nav_context``; the
            # legacy bucket-edge signals are suppressed by the surface.
            is_first_in_day=True, is_last_in_day=True,
            default_state=self._phase_default,
            nav_context=nav_context,
            nav_label_suffix=suffix,
        )
        self._stack.setCurrentIndex(self._PHOTO)
        self.photo.setFocus()

    def _photo_nav_context_and_label(
        self, came_from: int,
    ) -> tuple[str, str]:
        """Resolve the photo surface's nav context + position label suffix
        (spec/32 §2.7).

        * Day Grid (a standalone photo cell): ``"day_grid"`` + a suffix that
          places the user in the day ("· Cell N/M · Day 1 — Arrival"). The
          surface's photo position 1/1 collapses to "1/1 · Cell N/M · …".
        * Cluster (a sub-grid member): ``"cluster"`` + the cluster type
          ("· Burst", "· Focus bracket", "· Exposure bracket"). The surface's
          photo position shows where you are within the cluster.
        """
        if came_from == self._CLUSTER_GRID and self._current_cluster is not None:
            kind = {
                "burst": tr("Burst"),
                "focus_bracket": tr("Focus bracket"),
                "exposure_bracket": tr("Exposure bracket"),
            }.get(self._current_cluster.kind, self._current_cluster.kind)
            return "cluster", f"· {kind}"
        # Day Grid context — emit "· Cell N/M · {day_label}".
        total = len(self._current_day_cells)
        idx = (self._current_day_cell_idx or 0) + 1
        suffix = (
            f"· {tr('Cell')} {idx}/{total}"
            + (f"  ·  {self._current_day_label}" if self._current_day_label else "")
        )
        return "day_grid", suffix

    def _synthetic_bucket(self, ci: CullItem) -> CullBucket:
        """Single-item bucket the photo / video surface can drive (spec/32
        §4 — the Day Grid is the navigation level; the surface just shows
        one item)."""
        if self._eg is None:
            return CullBucket(
                bucket_key="daygrid|" + ci.item_id, kind="individual",
                title="", items=(ci,),
                status=project_status([ci.item_id], {}, None),
            )
        phase_states = self._eg.phase_states("pick")
        return CullBucket(
            bucket_key=(
                f"daygrid|{self._current_day_number}|{ci.item_id}"
            ),
            kind="individual",
            title="",
            items=(ci,),
            status=project_status([ci.item_id], phase_states, None),
        )

    def _cull_item(self, item_id: str) -> Optional[CullItem]:
        if self._eg is None:
            return None
        item = self._eg.item(item_id)
        if item is None or not item.origin_relpath:
            return None
        return CullItem(
            item_id=item.id,
            path=Path(self._eg.event_root) / item.origin_relpath,
            kind=item.kind,
            capture_time_corrected=item.capture_time_corrected or None,
            duration_ms=item.duration_ms,
        )

    # ── State cycles ──────────────────────────────────────────────────

    # Photo cycle: D → K → C → D…    Video cycle: D → K → D…
    _PHOTO_CYCLE = ("skipped", "picked", "candidate")
    _VIDEO_CYCLE = ("skipped", "picked")

    def _cycle_item(self, item_id: str, kind: str) -> None:
        if self._eg is None:
            return
        ps = self._eg.phase_states("pick").get(item_id)
        cur = ps.state if ps is not None else self._phase_default
        ladder = self._VIDEO_CYCLE if kind == "video" else self._PHOTO_CYCLE
        try:
            nxt = ladder[(ladder.index(cur) + 1) % len(ladder)]
        except ValueError:
            nxt = ladder[1] if len(ladder) > 1 else ladder[0]
        try:
            self._eg.set_phase_state(item_id, "pick", nxt)
        except Exception:  # noqa: BLE001
            log.exception("set_phase_state failed for %s", item_id)

    def _cycle_cluster(self, cluster: CullCluster) -> None:
        """Bulk-cycle a cluster (spec/32 Q3 — Mixed/Yellow → Keep all).

        Each ``set_phase_state`` opens its own transaction (SQLite can't nest),
        so the bulk is many small commits — fine for cluster sizes (3–10
        members in practice; cull_stats says ≤32 typical)."""
        if self._eg is None:
            return
        phase_states = self._eg.phase_states("pick")
        colors = [
            cell_color_for_item(m.item_id, m.kind, "pick", phase_states)
            for m in cluster.members
        ]
        agg = cluster_color(colors)
        if agg.value == "picked":
            target = "skipped"
        else:
            # discarded / mixed / compare / untouched → keep all (Nelson Q3)
            target = "picked"
        for cm in cluster.members:
            try:
                self._eg.set_phase_state(cm.item_id, "pick", target)
            except Exception:  # noqa: BLE001
                log.exception(
                    "cluster cycle failed at %s in %s",
                    cm.item_id, cluster.bucket_key)

    # ── Refresh helpers ───────────────────────────────────────────────

    def _refresh_day_cell(self, idx: int) -> None:
        if not (0 <= idx < len(self._current_day_cells)):
            return
        new_cell = self._reproject_cell(self._current_day_cells[idx])
        self._current_day_cells[idx] = new_cell
        # Preserve existing thumbnail so we don't re-load.
        current = self.day_grid.cell_at(idx)
        thumb = (current.render_data().thumbnail
                 if current is not None else None)
        self.day_grid.update_cell(
            idx, CellRenderData(cell=new_cell, thumbnail=thumb))

    def _refresh_cluster_cell(self, idx: int) -> None:
        if self._current_cluster is None:
            return
        if not (0 <= idx < len(self._current_cluster_members)):
            return
        ci = self._current_cluster_members[idx]
        phase_states = self._eg.phase_states("pick") if self._eg else {}
        current = self.cluster_grid.cell_at(idx)
        # Preserve visited (spec/32 §2.10) from the currently rendered cell —
        # nothing about a colour reproject changes whether the user has
        # opened it.  Opens are the only thing that sets visited (see
        # _open_cluster_bucket).
        was_visited = bool(
            current.render_data().cell.visited
            if current is not None else False
        )
        new_cell = CullCell(
            end_time=ci.capture_time_corrected or "",
            color=cell_color_for_item(
                ci.item_id, ci.kind, "pick", phase_states,
                default_state=self._phase_default),
            item_id=ci.item_id, item_kind=ci.kind,
            visited=was_visited,
        )
        thumb = (current.render_data().thumbnail
                 if current is not None else None)
        self.cluster_grid.update_cell(
            idx, CellRenderData(cell=new_cell, thumbnail=thumb))

    def _refresh_day_cell_for_cluster(self, bucket_key: str) -> None:
        for idx, cell in enumerate(self._current_day_cells):
            if cell.is_cluster and cell.cluster is not None \
               and cell.cluster.bucket_key == bucket_key:
                self._refresh_day_cell(idx)
                return

    def _reproject_cell(self, cell: CullCell) -> CullCell:
        """Recompute one cell's colour from live phase_states (used after a
        single state cycle — same data shape, fresh colour)."""
        if self._eg is None:
            return cell
        phase_states = self._eg.phase_states("pick")
        if cell.is_cluster and cell.cluster is not None:
            colors = [
                cell_color_for_item(
                    m.item_id, m.kind, "pick", phase_states,
                    default_state=self._phase_default)
                for m in cell.cluster.members
            ]
            agg = cluster_color(colors)
            new_cluster = CullCluster(
                bucket_key=cell.cluster.bucket_key,
                kind=cell.cluster.kind,
                title=cell.cluster.title,
                members=cell.cluster.members,
                color=agg,
                detection_source=cell.cluster.detection_source,
                camera=cell.cluster.camera,
            )
            return CullCell(
                end_time=cell.end_time, color=agg, cluster=new_cluster,
                visited=cell.visited,
            )
        if cell.item_id is None:
            return cell
        # (spec/56 retired the yellow-extracts rule — Pick creates no
        # children; a video cell shows its own P/D state like a photo.)
        return CullCell(
            end_time=cell.end_time,
            color=cell_color_for_item(
                cell.item_id, cell.item_kind, "pick", phase_states,
                default_state=self._phase_default,
            ),
            item_id=cell.item_id,
            item_kind=cell.item_kind,
            visited=cell.visited,
        )

    # ── Linear navigation conductor (spec/32 §2.7) ────────────────────

    def _navigate(self, delta: int) -> None:
        """Step the day-cell cursor by ``delta`` (±1) and open the next cell.

        Routed from three sources:
          * Photo surface ``navigate_at_edge`` (Day Grid context only).
          * Video surface ``prev/next_bucket_requested`` (translated).
          * Cluster sub-grid arrow keys (TODO M5b — DayGridView keys).

        Edge handling: at day boundaries → STOP (Nelson Q4). When entering
        a cluster cell, opens the sub-grid (Q1 = Option 1)."""
        if (self._current_day_cell_idx is None
                or not self._current_day_cells):
            return
        # We never escape from inside a cluster context — the photo surface
        # holds the line ("if inside a cluster we do not want to navigate
        # past the last/first photo"). This guard is defensive in case a
        # rogue navigate_at_edge sneaks through.
        if (self._stack.currentIndex() == self._PHOTO
                and self._surface_came_from == self._CLUSTER_GRID):
            return
        nxt = self._current_day_cell_idx + delta
        if nxt < 0 or nxt >= len(self._current_day_cells):
            return                                  # day boundary — stop
        self._current_day_cell_idx = nxt
        cell = self._current_day_cells[nxt]
        if cell.is_cluster and cell.cluster is not None:
            self._open_cluster(cell.cluster)
        elif cell.item_kind == "video" and cell.item_id is not None:
            self._open_video_item(cell.item_id, came_from=self._DAY_GRID)
        elif cell.item_id is not None:
            self._open_photo_item(cell.item_id, came_from=self._DAY_GRID)

    # ── Back routing ──────────────────────────────────────────────────

    def _on_day_grid_back(self) -> None:
        """Day Grid → days panel.

        Re-projects status for the days the user touched, in memory from
        ``self._days`` (no ``pick_days`` walk, no items() query). Only
        touches the navigator if at least one day changed — Nelson eyeball
        2026-06-04 perf fix ("going Back to the days list is also slow")."""
        if self._dirty_days:
            self._fast_reproject_days_status(self._dirty_days)
            self.navigator.set_days(self._days)
            self._dirty_days = set()
        self._stack.setCurrentIndex(self._NAV)
        self.navigator.setFocus()

    def _on_cluster_back(self) -> None:
        """Cluster sub-grid → Day Grid (parent of the cluster cell)."""
        # Sync the parent cell's colour to the cluster's current state.
        if self._current_cluster is not None:
            self._refresh_day_cell_for_cluster(self._current_cluster.bucket_key)
        self._current_cluster = None
        self._current_cluster_members = ()
        self._stack.setCurrentIndex(self._DAY_GRID)
        self.day_grid.setFocus()

    def _on_photo_back(self) -> None:
        """Photo surface → whichever grid we came from."""
        # State may have changed in the photo surface — mark dirty so the
        # days panel re-projects on Back from the Day Grid.
        self._mark_current_day_dirty()
        # Refresh the originating cell so border colour reflects fresh state.
        if self._surface_came_from == self._CLUSTER_GRID \
                and self._current_cluster is not None:
            # Re-project every member cell of the sub-grid.
            for i in range(len(self._current_cluster_members)):
                self._refresh_cluster_cell(i)
            self._refresh_day_cell_for_cluster(
                self._current_cluster.bucket_key)
            self._items_touched_in_surface = set()
            self._stack.setCurrentIndex(self._CLUSTER_GRID)
            self.cluster_grid.setFocus()
            return
        # came_from = DAY_GRID — refresh the originating cell.
        self._refresh_current_day_cells_from_bucket()
        self._items_touched_in_surface = set()
        self._stack.setCurrentIndex(self._DAY_GRID)
        self.day_grid.setFocus()

    def _on_video_back(self) -> None:
        """Video surface → grid we came from (mirrors _on_photo_back).
        The decision was already persisted per cycle (spec/56 — no more
        derive-the-master-from-kept-children on exit)."""
        self._current_video_item_id = None
        self._mark_current_day_dirty()
        if self._surface_came_from == self._CLUSTER_GRID \
                and self._current_cluster is not None:
            for i in range(len(self._current_cluster_members)):
                self._refresh_cluster_cell(i)
            self._refresh_day_cell_for_cluster(
                self._current_cluster.bucket_key)
            self._items_touched_in_surface = set()
            self._stack.setCurrentIndex(self._CLUSTER_GRID)
            self.cluster_grid.setFocus()
            return
        self._refresh_current_day_cells_from_bucket()
        self._items_touched_in_surface = set()
        self._stack.setCurrentIndex(self._DAY_GRID)
        self.day_grid.setFocus()

    def _refresh_current_day_cells_from_bucket(self) -> None:
        """After leaving the photo / video surface, refresh every Day-Grid
        cell whose item was opened during the surface session.

        Single-item Day-Grid arrow-nav reassigns ``_current_bucket`` to a new
        synthetic bucket on every step, so by Back-time it only holds the
        LAST item.  ``_items_touched_in_surface`` accumulates every opened
        ``item_id`` across the session; the refresh walks the union of that
        set with the current bucket's items.  Cluster cells whose members
        intersect the union also refresh (border-colour aggregate reproject).
        """
        bucket_item_ids = set()
        if self._current_bucket is not None:
            bucket_item_ids = {ci.item_id for ci in self._current_bucket.items}
        touched_ids = bucket_item_ids | self._items_touched_in_surface
        if not touched_ids:
            return
        for idx, cell in enumerate(self._current_day_cells):
            touched = False
            if cell.item_id is not None and cell.item_id in touched_ids:
                touched = True
                # spec/32 §2.10 — set_item_visited has already written
                # visited=1 to the DB; mirror it into the in-memory cell so
                # ``_reproject_cell`` (which preserves the input's visited)
                # picks up the tick on this refresh.  Without this, the DB
                # is correct but the rendered Day-Grid cell still reads
                # the stale visited=False.
                if (cell.item_id in self._items_touched_in_surface
                        and not cell.visited):
                    self._current_day_cells[idx] = CullCell(
                        end_time=cell.end_time, color=cell.color,
                        item_id=cell.item_id, item_kind=cell.item_kind,
                        cluster=cell.cluster, visited=True,
                    )
            elif cell.is_cluster and cell.cluster is not None and any(
                m.item_id in touched_ids for m in cell.cluster.members
            ):
                touched = True
            if touched:
                self._refresh_day_cell(idx)

    def _on_back(self) -> None:
        """Days-panel Quit → close event + return to per-event dashboard."""
        self._close_gateway()
        self._event_id = None
        self.closed.emit()

    # spec/56 slice 2 retired the cull-exit materialisation pass
    # (_finish_materialization / _sync_video_source_state / the
    # BackgroundMaterializer): Pick writes decisions only — bytes
    # commit at Export, in the Edit phase.

    # ── Batch ops (carried over from the pre-Day-Grid days panel) ─────

    def _on_batch_op(self, op, item_ids: list) -> None:
        if self._eg is None or not item_ids:
            return
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._eg.set_items_phase_state(item_ids, "pick", op.state)
        except Exception:  # noqa: BLE001
            log.exception("batch op failed")
        finally:
            QGuiApplication.restoreOverrideCursor()
        self._refresh_days()

    def _on_current_day_batch(self, state: str) -> None:
        """Day Grid top-bar Pick all / Skip all (Nelson 2026-06-07).
        Applies ``state`` to every item the user is currently looking at
        — flat-cell items + every cluster cell's members.

        Cluster cells carry no ``item_id`` (the cell binds to a
        :class:`CullCluster`, not a single item), so a "cells with
        item_id" pass silently drops every cluster member. The expanded
        walk includes them (Nelson 2026-06-09 bug — day full of clusters
        meant Pick all did nothing visible)."""
        if self._eg is None or not self._current_day_cells:
            return
        item_ids: List[str] = []
        for c in self._current_day_cells:
            if c.is_cluster and c.cluster is not None:
                item_ids.extend(
                    m.item_id for m in c.cluster.members if m.item_id)
            elif c.item_id:
                item_ids.append(c.item_id)
        if not item_ids:
            return
        n = len(item_ids)
        verb = tr("Pick") if state == "picked" else tr("Skip")
        resp = QMessageBox.question(
            self, tr("{verb} all in day?").replace("{verb}", verb),
            tr("This marks {n} item(s) as {verb}. Continue?").replace(
                "{n}", str(n)).replace("{verb}", verb.lower()),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._eg.set_items_phase_state(item_ids, "pick", state)
        except Exception:  # noqa: BLE001
            log.exception("current-day batch op failed")
        finally:
            QGuiApplication.restoreOverrideCursor()
        # Refresh every cell so its colour reflects the new state.
        for idx in range(len(self._current_day_cells)):
            self._refresh_day_cell(idx)

    def _on_cluster_batch(self, state: str) -> None:
        """Cluster sub-grid top-bar Pick all / Skip all (Nelson
        2026-06-09 — Compare-redesign follow-up). Applies ``state`` to
        every member of the open cluster.

        Mirrors :meth:`_on_current_day_batch`: same confirm dialog
        shape, same transactional write, same refresh fan-out.  After
        the write we re-project every sub-grid cell + the parent
        day-grid cell (its aggregate colour may have moved to e.g.
        all-Pick / all-Skip)."""
        if self._eg is None or self._current_cluster is None:
            return
        members = self._current_cluster_members
        item_ids = [m.item_id for m in members if m.item_id]
        if not item_ids:
            return
        n = len(item_ids)
        verb = tr("Pick") if state == "picked" else tr("Skip")
        resp = QMessageBox.question(
            self, tr("{verb} all in cluster?").replace("{verb}", verb),
            tr("This marks {n} item(s) as {verb}. Continue?").replace(
                "{n}", str(n)).replace("{verb}", verb.lower()),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._eg.set_items_phase_state(item_ids, "pick", state)
        except Exception:  # noqa: BLE001
            log.exception("cluster batch op failed")
        finally:
            QGuiApplication.restoreOverrideCursor()
        # Refresh every member cell + the parent cluster cell on the
        # day grid (its aggregate colour just collapsed to one state).
        for idx in range(len(members)):
            self._refresh_cluster_cell(idx)
        self._refresh_day_cell_for_cluster(self._current_cluster.bucket_key)
        self._mark_current_day_dirty()

    def _on_event_scope_batch(self, state: str) -> None:
        """Days bar Pick all days / Skip all days (Nelson 2026-06-07).
        Walks every captured item in the event and applies ``state``.
        Heavier op — uses a more cautious confirmation."""
        if self._eg is None:
            return
        item_ids = [
            it.id for it in self._eg.items(provenance="captured") if it.id
        ]
        if not item_ids:
            return
        n = len(item_ids)
        verb = tr("Pick") if state == "picked" else tr("Skip")
        resp = QMessageBox.question(
            self, tr("{verb} every item in the event?").replace(
                "{verb}", verb),
            tr(
                "This marks {n} item(s) across every day as {verb}. "
                "Already-decided items are overwritten. Continue?"
            ).replace("{n}", str(n)).replace("{verb}", verb.lower()),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._eg.set_items_phase_state(item_ids, "pick", state)
        except Exception:  # noqa: BLE001
            log.exception("event-scope batch op failed")
        finally:
            QGuiApplication.restoreOverrideCursor()
        self._refresh_days()

    # ── Compare flow (Nelson 2026-06-09) ──────────────────────────────

    def _on_compare_requested(self, origin_stack_index: int) -> None:
        """User clicked the "Compare" button on the day grid or the
        cluster sub-grid (or pressed ``C``). Gather the originating
        grid's flat Compare-state items, push :class:`ComparePage` onto
        the stack with them, and remember where to pop back to on Quit.

        Cluster cells in the day grid are skipped — per design, members
        compare only with members of the same cluster (the cluster sub-
        grid has its own Compare button)."""
        from mira.picked.status import CellColor, STATE_CANDIDATE
        if self._eg is None:
            return
        phase_states = self._eg.phase_states("pick")
        compare_items: List[CullItem] = []
        if origin_stack_index == self._DAY_GRID:
            for cell in self._current_day_cells:
                if cell.is_cluster or cell.item_id is None:
                    continue
                if cell.color != CellColor.COMPARE:
                    continue
                ci = self._cull_item(cell.item_id)
                if ci is not None:
                    compare_items.append(ci)
        elif origin_stack_index == self._CLUSTER_GRID:
            for ci in self._current_cluster_members:
                ps = phase_states.get(ci.item_id)
                state = getattr(ps, "state", None)
                if state == STATE_CANDIDATE and ci.kind != "video":
                    compare_items.append(ci)
        else:
            return
        if len(compare_items) < 2:
            return                                              # defensive
        self._compare_came_from = origin_stack_index
        self.compare_page.load(
            self._eg, "pick", compare_items, phase_states,
        )
        self._stack.setCurrentIndex(self._COMPARE)
        self.compare_page.setFocus()

    def _on_compare_quit(self) -> None:
        """User clicked Quit Comparison (or pressed Esc / C on the
        compare surface). Pop back to the originating grid and reproject
        every cell — states may have changed mid-compare."""
        target = self._compare_came_from or self._DAY_GRID
        self._compare_came_from = None
        if target == self._DAY_GRID:
            for idx in range(len(self._current_day_cells)):
                self._refresh_day_cell(idx)
        elif target == self._CLUSTER_GRID:
            for idx in range(len(self._current_cluster_members)):
                self._refresh_cluster_cell(idx)
            # Cluster's parent cell on the day grid may have moved too.
            if self._current_cluster is not None:
                self._refresh_day_cell_for_cluster(
                    self._current_cluster.bucket_key)
        self._stack.setCurrentIndex(target)
        target_widget = self._stack.currentWidget()
        if target_widget is not None:
            target_widget.setFocus()

    # ── "Start a new pass…" (spec/32 §2.10) ───────────────────────────

    def _on_clear_marks(self) -> None:
        """User clicked "Start a new pass…" on the days panel — confirm,
        then wipe every ✓ tick (item_visit + bucket.browsed) at Cull.
        Decisions (phase_state) are preserved.

        Refreshes the open Day Grid if any so cells stop reading visited
        without leaving + re-entering the day.
        """
        if self._eg is None:
            return
        resp = QMessageBox.question(
            self, tr("Start a new pass?"),
            tr(
                "This clears every ✓ tick on the Cull cells and clusters "
                "you've already opened.  Your Keep / Compare / Discard "
                "decisions are not touched.  Continue?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        try:
            n = self._eg.clear_visited_for_phase("pick")
        except Exception:  # noqa: BLE001
            log.exception("clear_visited_for_phase failed (cull)")
            return
        # Refresh any open Day Grid cells so the ticks disappear immediately.
        if self._current_day_cells:
            # Drop the visited bit in-memory + reproject every cell.
            for idx, cell in enumerate(self._current_day_cells):
                if cell.visited:
                    self._current_day_cells[idx] = CullCell(
                        end_time=cell.end_time, color=cell.color,
                        item_id=cell.item_id, item_kind=cell.item_kind,
                        cluster=cell.cluster, visited=False,
                    )
                    self._refresh_day_cell(idx)
        log.info("clear_visited_for_phase(cull): %d item ticks cleared", n)

    # ── Reset All Compare (spec/32 §2.8) — handler retired 2026-06-09 ─

    def _on_reset_compare(self) -> None:
        """Retained for direct callers (none today) — runs the gateway
        bulk op on the current day. The photo-surface button that used
        to fire this signal retired with the Compare redesign; left in
        place because the gateway helper remains useful for any future
        menu-driven equivalent."""
        if self._eg is None or self._current_day_number is None:
            return
        try:
            n = self._eg.reset_compare_in_day(
                "pick", self._current_day_number, self._phase_default)
        except Exception:  # noqa: BLE001
            log.exception("reset_compare_in_day failed")
            return
        # Refresh the current Day Grid cells so the user sees the change.
        for idx in range(len(self._current_day_cells)):
            self._refresh_day_cell(idx)
        if n > 0:
            self._mark_current_day_dirty()
        log.info("reset_compare_in_day: %d item(s) reset", n)

    # ── Thumbnail lazy loader ─────────────────────────────────────────

    def _enqueue_thumbnails(self, view, cells) -> None:
        """Queue item cells (photo + video) for lazy thumbnail decoding.

        Photos load via :func:`load_pixmap`; videos go through
        :func:`core.thumb_cache.ensure_thumb` (cached JPEG of an extracted
        frame at ~1 s with a 0-fallback for the black-opener guard — same
        helper the clip materialiser uses, so cached frames are reused).

        QPixmap cache hits (item already decoded earlier in the session)
        are applied immediately without queuing — day-switch round trips
        are instant for previously-seen photos (Nelson perf 2026-06-04).
        """
        target = "day" if view is self.day_grid else "cluster"
        for i, c in enumerate(cells):
            if c.is_cluster or c.item_id is None:
                continue
            cached = self._thumb_pixmap_cache.get(c.item_id)
            if cached is not None and not cached.isNull():
                view.set_cell_thumbnail(i, cached)
                continue
            self._thumb_pending.append((target, str(i), c.item_id))
        if self._thumb_pending and not self._thumb_timer.isActive():
            self._thumb_timer.start()

    def _load_some_thumbs(self) -> None:
        if self._eg is None:
            self._thumb_timer.stop()
            self._thumb_pending.clear()
            return
        # **Hold the loader while cells are still being constructed**
        # (Nelson 2026-06-05 perf eyeball — thumb decode on the main
        # thread was creating 600-1400ms gaps between chunked-build
        # batches because each ``load_pixmap`` call is 50-300ms of
        # blocking work). Cells build first → the user sees the grid
        # → then thumbs fill in. Timer stays armed so it auto-resumes
        # once construction finishes.
        if (self.day_grid.pending_cell_count() > 0
                or self.cluster_grid.pending_cell_count() > 0):
            return
        done = 0
        while self._thumb_pending and done < _THUMBS_PER_TICK:
            target, idx_str, item_id = self._thumb_pending.pop(0)
            try:
                idx = int(idx_str)
            except ValueError:
                continue
            view = self.day_grid if target == "day" else self.cluster_grid
            item = self._eg.item(item_id)
            if item is None or not item.origin_relpath:
                continue
            path = Path(self._eg.event_root) / item.origin_relpath
            pm = self._decode_thumbnail(item, path)
            if pm is None or pm.isNull():
                # Bug 3 (2026-06-13): the loader used to silently
                # `continue` here, leaving the cell forever-blank.
                # Show a kind-aware placeholder so the user sees that
                # the item IS there (it's just an unrenderable preview);
                # photos / video both get a generic "no preview" tile,
                # videos additionally get the ▶ play glyph.
                pm = _placeholder_pixmap(item.kind)
            self._thumb_pixmap_cache[item_id] = pm
            # Routes via the view's data cache too, so a still-pending cell
            # (chunked builder hasn't reached it yet) inherits the thumb
            # when it lands. ``set_cell_thumbnail`` is a no-op if idx is
            # out of range.
            view.set_cell_thumbnail(idx, pm)
            done += 1
        if not self._thumb_pending:
            self._thumb_timer.stop()

    def _decode_thumbnail(self, item, path: Path):
        """Decode a thumbnail for one item.

        **Photos / snapshots** → ``photo_thumb_cache.ensure_photo_thumb``
        materialises a 256-px JPEG under
        ``<event_root>/.cache/thumbs/photos/<sha256>.jpg`` on first
        request (~50–300 ms decoding the RAW or JPEG source). Every
        subsequent request reads the small JPEG (~2 ms). The on-miss
        fall-back returns the source path so ``load_pixmap`` can still
        decode it directly — the cache is a perf layer, not a
        correctness one (Nelson 2026-06-05 Option D).

        **Videos** → ``thumb_cache.ensure_thumb`` extracts a frame at
        1 s (0-s fallback for black openers) and caches the JPEG under
        ``.cache/thumbs/<source_rel_path>/`` (sibling tree, keyed by
        source path because a video's bucket-model item carries no
        per-frame sha)."""
        try:
            if item.kind == "video":
                from core.thumb_cache import ensure_thumb
                thumb_path = ensure_thumb(
                    event_root=Path(self._eg.event_root),
                    source_video=path,
                    source_rel_path=Path(item.origin_relpath),
                    item_id="daygrid",
                    position_ms=1000,
                    fallback_position_ms=0,
                )
                return load_pixmap(thumb_path)
            # Photo / snapshot path — disk-cached 256-px JPEG keyed by
            # item.sha256. The all-or-nothing CHECK on ``item`` guarantees
            # sha256 is populated whenever origin_relpath is (the gate
            # the loader already enforces upstream); the defensive branch
            # below covers a malformed item without crashing.
            if item.sha256:
                from core.photo_thumb_cache import ensure_photo_thumb
                thumb_path = ensure_photo_thumb(
                    event_root=Path(self._eg.event_root),
                    source_path=path,
                    sha256=item.sha256,
                )
                return load_pixmap(thumb_path)
            return load_pixmap(path)
        except Exception:  # noqa: BLE001 — never crash the loader
            # Bug 3 (2026-06-13): upgraded from DEBUG so the failure is
            # visible in the app log; the caller substitutes a
            # placeholder pixmap so the cell isn't blank.
            log.warning("thumbnail decode failed for %s", path, exc_info=True)
            return None
