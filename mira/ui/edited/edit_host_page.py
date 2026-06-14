"""EditHostPage — Day-Grid parent for the Process phase (spec/32 §6.3).

Mirrors :class:`mira.ui.picked.pick_page.PickPage` shape but leaner:

* **Drops** the Select nudge dialog, the silent-sync engine, the "Ready to
  select" summary dialog, the Reset-All-Compare button, the batch-op
  navigator menus.  Process has no Keep/Compare/Discard at the item level —
  the only commitment is materialising via Export (Q3 + Q4 locked
  2026-06-08).
* **Keeps** the full nav stack: days panel → Day Grid → cluster sub-grid →
  photo / video surface; the linear-nav conductor (``_navigate``); the
  thumbnail lazy loader; the Mvis visited tick hooks
  (``set_bucket_browsed`` / ``set_item_visited``); the touched-set
  back-refresh (``_items_touched_in_surface`` + the in-memory visited
  stamp inside ``_refresh_current_day_cells_from_bucket`` — the bug
  Nelson hit on Select and we MUST replicate here).
* **Routes** centre-click by item kind: photo / snapshot →
  :class:`mira.ui.edited.edit_page.EditPage`; video / clip →
  :class:`mira.ui.edited.edit_video_page.EditVideoPage`;
  cluster → cluster sub-grid (then sub-grid centre-click opens EditPage
  with the real cluster bucket).
* **Item pool** = Select-Kept items only
  (:func:`mira.picked.edit_model.process_days`); ``phase="edit"``
  is passed to :func:`mira.picked.day_grid_cells` so the cell-colour
  resolver reads ``Adjustment.edit_exported`` (spec/32 §6.3).

Export scope routing: EditPage runs photo-scope inline; day / event scope
emits :attr:`EditPage.export_scope_requested` for this host to handle.
EditVideoPage runs its own per-clip export.  After a successful export
on either surface, the cell's green border lights up on Back via the
adjustments_for_day re-read in ``_reproject_cell``.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QMessageBox, QStackedWidget, QVBoxLayout, QWidget

from mira.picked import (
    CullBucket,
    CullCell,
    CullCluster,
    PickDay,
    CullItem,
    cluster_color,
    day_grid_cells,
    project_status,
)
from mira.picked.edit_model import process_days
from mira.picked.status import (
    STATE_SKIPPED,
    STATE_PICKED,
    cell_color_for_process_item,
    default_state_for,
)
from mira.gateway import Gateway
from mira.ui.base.bucket_navigator import SELECT_CONFIG, BucketNavigator
from mira.ui.base.day_grid_cell import CellRenderData
from mira.ui.base.day_grid_view import DayGridView
from mira.ui.base.progress import run_with_progress
from mira.ui.i18n import tr
from mira.ui.media.image_loader import load_pixmap
from mira.ui.edited.edit_page import EditPage
from mira.ui.edited.edit_video_page import EditVideoPage

log = logging.getLogger(__name__)

_THUMBS_PER_TICK = 4
_THUMB_TIMER_MS = 20


class EditHostPage(QWidget):
    """Day-Grid host for the Process phase — see module docstring."""

    closed = pyqtSignal()
    fullscreen_changed = pyqtSignal(bool)

    _NAV = 0
    _DAY_GRID = 1
    _CLUSTER_GRID = 2
    _PHOTO = 3
    _VIDEO = 4

    def __init__(
        self, gateway: Gateway, parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.gateway = gateway
        self._eg = None
        self._event_id: Optional[str] = None
        self._days: List[PickDay] = []
        self._all_buckets: List[CullBucket] = []
        self._current_day_number: Optional[int] = None
        self._current_day_cells: List[CullCell] = []
        self._current_day_cell_idx: Optional[int] = None
        self._current_cluster: Optional[CullCluster] = None
        self._current_cluster_members: tuple = ()
        self._current_day_label: str = ""
        self._dirty_days: set = set()
        self._current_bucket: Optional[CullBucket] = None
        # See [[feedback_back_refresh_track_touched_items]] — without this
        # set, only the LAST item touched in a surface session refreshes on
        # Back; every other one keeps a stale cell.  Cleared at the end of
        # every photo / video surface Back.
        self._items_touched_in_surface: set = set()
        self._surface_came_from: int = self._DAY_GRID
        # Configured defaults — Select drives the Cull→Process pool carry-
        # forward (edit_pool_ids); Process drives the cell colour for
        # un-decided items (Nelson 2026-06-04 — untouched is gone; cells
        # always render the phase default).  Both loaded in open_event().
        self._select_default = STATE_SKIPPED
        self._process_default = STATE_PICKED
        # spec/59 §8 Exported watermark — the edit-lineage id set (the
        # watermark driver) + the app-wide setting gate. Loaded in
        # open_event; the set refreshes on day open + export commits.
        self._exported_ids: set = set()
        self._watermark_enabled: bool = True
        # Per-day cache of Adjustment rows so cell-colour reprojection on
        # back-refresh doesn't N+1 the gateway.
        self._adjustments_cache: Dict[str, object] = {}
        # Same defer-overlay pattern as PickPage/PickPage.
        self._defer_open_work: bool = True

        # Thumbnail loader.
        self._thumb_pending: List[tuple[str, str, str]] = []
        self._thumb_pixmap_cache: Dict = {}
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setInterval(_THUMB_TIMER_MS)
        self._thumb_timer.timeout.connect(self._load_some_thumbs)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._stack = QStackedWidget()

        # Days panel — reuse SELECT_CONFIG but override the heading template
        # so the user knows they're in Process, and drop batch_ops entirely
        # (Process has no Keep/Discard at the item level — Q3/Q4 locked
        # 2026-06-08; the per-day "X/Y processed" count is the only signal).
        nav_config = replace(
            SELECT_CONFIG,
            day_list_heading_template=tr("{n} day(s) — pick where to edit"),
            return_button_label=tr("Back"),
            return_button_tooltip=tr(
                "Leave the editor and return to the event."),
            day_grid_mode=True,
            batch_ops=[],
            # spec/32 §2.10 — "Start a new pass…" opt-in for Edit
            # (Nelson 2026-06-09).  Clears every ✓ tick at this phase.
            show_clear_marks_button=True,
            # Nelson 2026-06-07 — "Export all days" opt-in for Edit.
            # One-click event-scope export from the days panel.
            show_export_all_button=True,
        )
        self.navigator = BucketNavigator(config=nav_config)
        self.navigator.day_activated.connect(self._on_day_activated)
        self.navigator.back_requested.connect(self._on_back)
        self.navigator.clear_marks_requested.connect(self._on_clear_marks)
        # Nelson 2026-06-07 — "Export all days" on the days panel routes
        # to the existing event-scope export handler.
        self.navigator.export_all_requested.connect(
            lambda: self._on_export_scope_requested("event"))
        # Drop batch ops at Edit — wire to a no-op so the navigator
        # doesn't crash if a button fires.
        try:
            self.navigator.batch_op_requested.connect(lambda *_a: None)
        except Exception:  # noqa: BLE001
            pass

        # Day Grid + cluster sub-grid (same widget, two slots).
        # Nelson 2026-06-06: "Start a new pass…" lives on the day grid
        # itself — same handler the navigator's button uses.
        # Nelson 2026-06-07: + day-scope Export all on the day grid
        # top bar (exports every processed photo in the day).
        self.day_grid = DayGridView(
            show_clear_marks_button=True,
            show_export_all_button=True,
        )
        self.day_grid.back_requested.connect(self._on_day_grid_back)
        self.day_grid.cell_activated.connect(self._on_day_cell_activated)
        self.day_grid.clear_marks_requested.connect(self._on_clear_marks)
        # Nelson 2026-06-07 — "Export all" in the day grid routes to the
        # existing day-scope export handler.
        self.day_grid.export_all_requested.connect(
            lambda: self._on_export_scope_requested("day"))
        # spec/59 export-status (supersedes the Q4 2026-06-08 no-op):
        # border-click toggles marked-for-export, same grammar as Pick.
        self.day_grid.cell_border_clicked.connect(self._on_day_cell_border)

        self.cluster_grid = DayGridView(enable_arrow_nav=True)
        self.cluster_grid.back_requested.connect(self._on_cluster_back)
        self.cluster_grid.cell_border_clicked.connect(
            self._on_cluster_cell_border)
        self.cluster_grid.cell_activated.connect(self._on_cluster_cell_activated)
        self.cluster_grid.cell_border_clicked.connect(lambda *_a: None)
        self.cluster_grid.navigate_at_edge.connect(self._navigate)

        # Process editor surfaces.
        self.photo = EditPage()
        self.photo.back_requested.connect(self._on_photo_back)
        self.photo.fullscreen_changed.connect(self.fullscreen_changed.emit)
        self.photo.navigate_at_edge.connect(self._navigate)
        self.photo.export_scope_requested.connect(
            self._on_export_scope_requested)
        self.photo.process_export_committed.connect(
            self._on_photo_export_committed)

        self.video = EditVideoPage()
        self.video.back_requested.connect(self._on_video_back)
        self.video.fullscreen_changed.connect(self.fullscreen_changed.emit)
        self.video.navigate_at_edge.connect(self._navigate)
        self.video.clip_exported.connect(self._on_video_export_committed)

        self._stack.addWidget(self.navigator)     # 0
        self._stack.addWidget(self.day_grid)      # 1
        self._stack.addWidget(self.cluster_grid)  # 2
        self._stack.addWidget(self.photo)         # 3
        self._stack.addWidget(self.video)         # 4
        outer.addWidget(self._stack)

        # "Preparing the page…" overlay (same pattern as PickPage).
        from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel
        self._loading_overlay = QFrame(self)
        self._loading_overlay.setObjectName("ProcessLoadingOverlay")
        self._loading_overlay.setAutoFillBackground(True)
        self._loading_overlay.setStyleSheet(
            "QFrame#ProcessLoadingOverlay {"
            "  background-color: rgba(0, 0, 0, 140);"
            "}"
            "QLabel#ProcessLoadingLabel {"
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
        self._loading_label.setObjectName("ProcessLoadingLabel")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overlay_layout.addWidget(self._loading_label)
        self._loading_overlay.hide()

    # ── lifecycle ────────────────────────────────────────────────────

    def open_event(self, event_id: str) -> bool:
        self._close_gateway()
        try:
            self._eg = self.gateway.open_event(event_id)
        except Exception:  # noqa: BLE001
            log.exception("could not open event %s for process", event_id)
            QMessageBox.warning(
                self, tr("Process"),
                tr("This event could not be opened for processing."))
            return False
        self._event_id = event_id
        self._select_default = default_state_for(
            self.gateway.settings, "pick")
        self._process_default = default_state_for(
            self.gateway.settings, "edit")
        # The navigator's untouched-merge target = the Process default, so
        # the days panel folds un-decided counts into the right colour.
        self.navigator.set_untouched_merge_target(self._process_default)
        # spec/59 export-status: the surfaces honour the live edit
        # default ("born green" out of the box).
        self.photo.set_phase_default(self._process_default)
        self.video.set_phase_default(self._process_default)
        # spec/59 §8 Exported watermark — the only control is the
        # app-wide hide setting; surfaces get the same gate.
        self._watermark_enabled = bool(getattr(
            self.gateway.settings, "show_exported_watermark", True))
        self.photo.set_watermark_enabled(self._watermark_enabled)
        self.video.set_watermark_enabled(self._watermark_enabled)
        self._refresh_exported_ids()
        self._dirty_days = set()
        days = self._build_days_with_progress()
        if days is None:
            self._close_gateway()
            return False
        # Empty Select-Kept pool — surface a friendly message instead of
        # showing an empty navigator (Nelson 2026-06-06 eyeball: blank page on
        # Transformation when the user has done Cull but not Select).
        if not days or not any(d.buckets for d in days):
            QMessageBox.information(
                self, tr("Nothing to transform yet"),
                tr(
                    "No items are kept through Selection yet. Run Selection "
                    "on this event and mark the items you want to take to "
                    "Transformation, then come back."
                ),
            )
            self._close_gateway()
            return False
        self._days = days
        self._all_buckets = [b for d in self._days for b in d.buckets]
        # Initial projection so the days panel shows per-day "X/Y processed"
        # straight away (the raw process_days() output reflects Select-Kept
        # counts only — we override with edit_exported counts here).
        self._reproject_days_for({d.day_number for d in self._days})
        self.navigator.set_days(self._days)
        self._stack.setCurrentIndex(self._NAV)
        self.navigator.setFocus()
        return True

    def _build_days_with_progress(self) -> Optional[List[PickDay]]:
        """Build the Day → Bucket tree behind a progress dialog.  No
        summary dialog at Process — the user goes straight into the days
        panel after a successful build."""
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
            return process_days(
                self._eg,
                progress=on_day,
                pick_default_state=self._select_default,
            )

        ok, result = run_with_progress(
            self, tr("Opening Process"), work,
            label=tr("Reading the event…"))
        if not ok:
            QMessageBox.warning(
                self, tr("Process"),
                tr("This event could not be read for processing."))
            return []
        return result

    def _mark_current_day_dirty(self) -> None:
        if self._current_day_number is not None:
            self._dirty_days.add(self._current_day_number)

    def _refresh_days_navigator(self) -> None:
        """Re-project status for dirty days from fresh adjustments, then push
        the updated days to the navigator so the panel shows "X/Y processed"
        per day.  No-op if nothing's dirty."""
        if not self._dirty_days or not self._days:
            return
        self._reproject_days_for(self._dirty_days)
        self.navigator.set_days(self._days)
        self._dirty_days = set()

    def _reproject_days_navigator(self) -> None:
        """Force re-projection of ALL days + push to navigator (used after a
        batched export that spans multiple days)."""
        if not self._days:
            return
        all_day_numbers = {d.day_number for d in self._days}
        self._reproject_days_for(all_day_numbers)
        self.navigator.set_days(self._days)
        self._dirty_days = set()

    def _reproject_days_for(self, day_numbers) -> None:
        """Walk ``self._days`` and rebuild each touched day's BucketStatus
        from per-item ``Adjustment.edit_exported`` flags.

        ``kept`` in the projected status = "this many items already exported";
        ``untouched`` = "this many still to process".  Process has no Compare
        and no Discard at the item level, so those counts are zero.  The
        ``in_progress`` badge fires as soon as any item is exported (so the
        navigator's day card highlights work-in-progress days)."""
        if self._eg is None or not self._days or not day_numbers:
            return
        from mira.picked import BucketStatus
        targets = set(day_numbers)
        for i, day in enumerate(self._days):
            if day.day_number not in targets:
                continue
            try:
                adjustments = self._eg.adjustments_for_day(day.day_number)
            except Exception:  # noqa: BLE001
                adjustments = {}
            new_buckets = []
            for b in day.buckets:
                exported = sum(
                    1 for ci in b.items
                    if (adjustments.get(ci.item_id) is not None
                        and adjustments[ci.item_id].edit_exported)
                )
                total = len(b.items)
                untouched = max(0, total - exported)
                new_status = BucketStatus(
                    total=total, kept=exported,
                    candidate=0, discarded=0, untouched=untouched,
                    reviewed=False, browsed=False,
                    badge="in_progress" if exported > 0 else "untouched",
                )
                new_buckets.append(CullBucket(
                    bucket_key=b.bucket_key, kind=b.kind, title=b.title,
                    items=b.items, status=new_status,
                    detection_source=b.detection_source, camera=b.camera,
                ))
            day_total = sum(b.status.total for b in new_buckets)
            day_exported = sum(b.status.kept for b in new_buckets)
            day_status = BucketStatus(
                total=day_total, kept=day_exported,
                candidate=0, discarded=0,
                untouched=max(0, day_total - day_exported),
                reviewed=False, browsed=False,
                badge="in_progress" if day_exported > 0 else "untouched",
            )
            self._days[i] = PickDay(
                day_number=day.day_number, label=day.label,
                buckets=tuple(new_buckets), status=day_status,
            )
        self._all_buckets = [b for d in self._days for b in d.buckets]

    def _close_gateway(self) -> None:
        self._thumb_timer.stop()
        self._thumb_pending.clear()
        self._thumb_pixmap_cache.clear()
        self._adjustments_cache.clear()
        if self._eg is not None:
            try:
                self._eg.close()
            except Exception:  # noqa: BLE001
                log.exception("error closing event gateway (process)")
            self._eg = None

    # ── Navigator → Day Grid ─────────────────────────────────────────

    def _on_day_activated(self, day_number) -> None:
        if self._eg is None:
            return
        self._open_day(day_number)

    def _open_day(self, day_number) -> None:
        if self._eg is None:
            return
        day = next(
            (d for d in self._days if d.day_number == day_number), None)
        if day is None:
            return

        def _work():
            self._current_day_number = day_number
            self._current_day_label = day.label
            # Pre-load the day's Adjustment rows (one batched query).
            try:
                self._adjustments_cache = self._eg.adjustments_for_day(
                    day_number)
            except Exception:  # noqa: BLE001
                self._adjustments_cache = {}
            self._refresh_exported_ids()
            cells = day_grid_cells(
                self._eg, day_number, phase="edit",
                days=self._days,
                default_state=self._process_default,
                exported_ids=(
                    self._exported_ids if self._watermark_enabled
                    else None),
            )
            self._current_day_cells = list(cells)
            self._current_day_cell_idx = None
            render = [CellRenderData(cell=c, thumbnail=None) for c in cells]
            self.day_grid.set_header(self._day_header(day))
            self.day_grid.set_cells(render)
            self._stack.setCurrentIndex(self._DAY_GRID)
            self.day_grid.setFocus()
            self._enqueue_thumbnails(self.day_grid, self._current_day_cells)

        self._run_with_preparing_dialog(tr("Preparing the page…"), _work)

    def _day_header(self, day: PickDay) -> str:
        n = len(self._current_day_cells) or sum(b.count for b in day.buckets)
        return tr("{label} — {n} cell(s)").replace(
            "{label}", day.label).replace("{n}", str(n))

    def _run_with_preparing_dialog(self, text: str, work_fn) -> None:
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

    # ── Day Grid clicks ──────────────────────────────────────────────

    def _on_day_cell_activated(self, idx: int) -> None:
        if not (0 <= idx < len(self._current_day_cells)):
            return
        self._current_day_cell_idx = idx
        cell = self._current_day_cells[idx]
        if cell.is_cluster and cell.cluster is not None:
            self._open_cluster(cell.cluster)
        elif cell.item_kind == "video" and cell.item_id is not None:
            self._open_video_item(cell.item_id, came_from=self._DAY_GRID)
        elif cell.item_id is not None:
            self._open_photo_item(cell.item_id, came_from=self._DAY_GRID)

    # ── Cluster sub-grid ─────────────────────────────────────────────

    def _open_cluster(self, cluster: CullCluster) -> None:
        """spec/32 §2.10 — cluster tick fires on sub-grid open."""
        if self._eg is not None:
            try:
                self._eg.set_bucket_browsed(cluster.bucket_key, "edit")
            except Exception:  # noqa: BLE001
                log.exception(
                    "set_bucket_browsed failed for cluster %s",
                    cluster.bucket_key)

        def _work():
            self._current_cluster = cluster
            self._current_cluster_members = cluster.members
            for i, c in enumerate(self._current_day_cells):
                if (c.is_cluster and c.cluster is not None
                        and c.cluster.bucket_key == cluster.bucket_key):
                    self._current_day_cell_idx = i
                    new_cluster_cell = CullCell(
                        end_time=c.end_time, color=c.color,
                        cluster=c.cluster, visited=True,
                    )
                    self._current_day_cells[i] = new_cluster_cell
                    self._refresh_day_cell(i)
                    break
            member_cells = self._build_cluster_member_cells(cluster)
            render = [CellRenderData(cell=c, thumbnail=None)
                      for c in member_cells]
            self.cluster_grid.set_header(self._cluster_header(cluster))
            self.cluster_grid.set_cells(render)
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
        """Per-member cells for the sub-grid, with Process colour + Mvis tick."""
        if self._eg is None:
            return []
        adjustments = self._adjustments_cache or {}
        try:
            visited_items = self._eg.items_visited_for_day(
                self._current_day_number, "edit")
        except Exception:  # noqa: BLE001
            visited_items = set()
        return [
            CullCell(
                end_time=ci.capture_time_corrected or "",
                color=cell_color_for_process_item(
                    ci.item_id, adjustments,
                    default_state=self._process_default,
                ),
                item_id=ci.item_id, item_kind=ci.kind,
                visited=ci.item_id in visited_items,
                exported=self._cell_exported(ci.item_id, ci.kind),
            )
            for ci in cluster.members
        ]

    def _on_cluster_cell_activated(self, idx: int) -> None:
        if self._current_cluster is None:
            return
        if not (0 <= idx < len(self._current_cluster_members)):
            return
        self._open_cluster_bucket(self._current_cluster, entry_idx=idx)

    # ── Open photo / video / cluster bucket in a surface ─────────────

    def _open_photo_item(self, item_id: str, *, came_from: int) -> None:
        cull_item = self._cull_item(item_id)
        if cull_item is None:
            return
        if self._eg is not None:
            try:
                self._eg.set_item_visited(item_id, "edit")
            except Exception:  # noqa: BLE001
                log.exception("set_item_visited failed for %s", item_id)
        self._items_touched_in_surface.add(item_id)
        bucket = self._synthetic_bucket(cull_item)
        self._open_in_photo_surface(
            bucket, came_from=came_from, entry_idx=0)

    def _open_video_item(self, item_id: str, *, came_from: int) -> None:
        if self._eg is None:
            return
        cull_item = self._cull_item(item_id)
        if cull_item is None:
            return
        try:
            self._eg.set_item_visited(item_id, "edit")
        except Exception:  # noqa: BLE001
            log.exception("set_item_visited failed for %s", item_id)
        self._items_touched_in_surface.add(item_id)
        self._surface_came_from = came_from
        bucket = self._synthetic_bucket(cull_item)
        self._current_bucket = bucket
        nav_context = ("cluster" if came_from == self._CLUSTER_GRID
                       else "day_grid")
        from core.path_builder import edited_media_dir
        processed_dir = (
            edited_media_dir(Path(self._eg.event_root))
            if self._eg.event_root else None
        )
        day_label = self._day_label_for_export()
        self.video.load(
            self._eg, bucket,
            nav_context=nav_context,
            processed_dir=processed_dir,
            day_label=day_label,
        )
        self._stack.setCurrentIndex(self._VIDEO)
        self.video.setFocus()

    def _open_cluster_bucket(
        self, cluster: CullCluster, *, entry_idx: int = 0,
    ) -> None:
        if self._eg is None:
            return
        bucket = CullBucket(
            bucket_key=cluster.bucket_key,
            kind=cluster.kind,
            title=cluster.title,
            items=cluster.members,
            status=project_status(
                [m.item_id for m in cluster.members],
                self._eg.phase_states("edit"),
                self._eg.bucket(cluster.bucket_key, "edit"),
            ),
            detection_source=cluster.detection_source,
            camera=cluster.camera,
        )
        # Mvis: mark the entry member visited + stamp the sub-grid cell.
        if 0 <= entry_idx < len(cluster.members):
            member_id = cluster.members[entry_idx].item_id
            try:
                self._eg.set_item_visited(member_id, "edit")
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
            self._eg.set_bucket_browsed(bucket.bucket_key, "edit")
        except Exception:  # noqa: BLE001
            log.exception("set_bucket_browsed failed for %s", bucket.bucket_key)
        nav_context, suffix = self._photo_nav_context_and_label(came_from)
        # Nelson 2026-06-06: feed the REAL day position so the compact_row
        # X/Y label reflects "where am I in the day". Day-grid context →
        # current cell index / total cells; cluster context → keep 1/1
        # (the in-cluster label takes over).
        if nav_context == "day_grid" and self._current_day_cells:
            day_idx = (self._current_day_cell_idx or 0) + 1
            day_total = len(self._current_day_cells)
        else:
            day_idx, day_total = 1, 1
        # EditPage.load takes (eg, bucket, ...).  See EditPage docstring.
        self.photo.load(
            self._eg, bucket,
            entry_override=entry_idx,
            nav_context=nav_context,
            nav_label_suffix=suffix,
            bucket_index=day_idx, bucket_count=day_total,
        )
        self._stack.setCurrentIndex(self._PHOTO)
        self.photo.setFocus()

    def _photo_nav_context_and_label(
        self, came_from: int,
    ) -> tuple[str, str]:
        if (came_from == self._CLUSTER_GRID
                and self._current_cluster is not None):
            kind = {
                "burst": tr("Burst"),
                "focus_bracket": tr("Focus bracket"),
                "exposure_bracket": tr("Exposure bracket"),
            }.get(self._current_cluster.kind, self._current_cluster.kind)
            return "cluster", f"· {kind}"
        total = len(self._current_day_cells)
        idx = (self._current_day_cell_idx or 0) + 1
        suffix = (
            f"· {tr('Cell')} {idx}/{total}"
            + (f"  ·  {self._current_day_label}" if self._current_day_label else "")
        )
        return "day_grid", suffix

    def _synthetic_bucket(self, ci: CullItem) -> CullBucket:
        if self._eg is None:
            return CullBucket(
                bucket_key="daygrid|" + ci.item_id, kind="individual",
                title="", items=(ci,),
                status=project_status([ci.item_id], {}, None),
            )
        phase_states = self._eg.phase_states("edit")
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

    def _day_label_for_export(self) -> str:
        """Day-folder label for the Process export tree (current day)."""
        return self._day_label_for_day_number(self._current_day_number)

    # ── Refresh helpers ──────────────────────────────────────────────

    def _on_day_cell_border(self, idx: int) -> None:
        """spec/59 export-status: border-click toggles marked-for-export.
        Item cells flip; a VIDEO or CLUSTER cell paints ALL its members
        to the inverse of the aggregate (all-green → all red, anything
        else → all green) — the one-gesture 'paint it' rule."""
        from mira.picked.status import CellColor
        if self._eg is None or not (
                0 <= idx < len(self._current_day_cells)):
            return
        cell = self._current_day_cells[idx]
        phase_states = self._eg.phase_states("edit")
        try:
            if cell.is_cluster and cell.cluster is not None:
                colors = [
                    self._edit_status_color(m.item_id, m.kind, phase_states)
                    for m in cell.cluster.members]
                new = ("skipped" if all(c == CellColor.KEPT for c in colors)
                       else "picked")
                self._eg.set_items_phase_state(
                    [m.item_id for m in cell.cluster.members], "edit", new)
            elif cell.item_id is not None and cell.item_kind == "video":
                ids = [s.item_id
                       for s in self._eg.video_segments(cell.item_id)]
                ids += [sn.item_id
                        for sn in self._eg.video_snapshots(cell.item_id)]
                if not ids:
                    # The workshop never opened this video — no clip rows
                    # to mark yet; the cell shows the default meanwhile.
                    return
                agg = self._edit_status_color(
                    cell.item_id, "video", phase_states)
                new = "skipped" if agg == CellColor.KEPT else "picked"
                self._eg.set_items_phase_state(ids, "edit", new)
            elif cell.item_id is not None:
                cur = ("picked" if self._is_marked_for_export(
                    cell.item_id, phase_states) else "skipped")
                self._eg.set_phase_state(
                    cell.item_id, "edit",
                    "skipped" if cur == "picked" else "picked")
            else:
                return
        except Exception:  # noqa: BLE001
            log.exception("export-status toggle failed (cell %d)", idx)
            return
        self._refresh_day_cell(idx)

    def _on_cluster_cell_border(self, idx: int) -> None:
        """Border-click inside a cluster sub-grid — flips ONE member."""
        if self._eg is None or self._current_cluster is None:
            return
        if not (0 <= idx < len(self._current_cluster_members)):
            return
        ci = self._current_cluster_members[idx]
        phase_states = self._eg.phase_states("edit")
        cur = ("picked" if self._is_marked_for_export(
            ci.item_id, phase_states) else "skipped")
        try:
            self._eg.set_phase_state(
                ci.item_id, "edit",
                "skipped" if cur == "picked" else "picked")
        except Exception:  # noqa: BLE001
            log.exception("export-status toggle failed for %s", ci.item_id)
            return
        self._refresh_cluster_cell(idx)
        self._refresh_day_cell_for_cluster(self._current_cluster.bucket_key)

    def _refresh_day_cell(self, idx: int) -> None:
        if not (0 <= idx < len(self._current_day_cells)):
            return
        new_cell = self._reproject_cell(self._current_day_cells[idx])
        self._current_day_cells[idx] = new_cell
        current = self.day_grid.cell_at(idx)
        thumb = (current.render_data().thumbnail
                 if current is not None else None)
        self.day_grid.update_cell(
            idx, CellRenderData(cell=new_cell, thumbnail=thumb))

    def _edit_status_color(self, item_id: str, item_kind: str,
                           phase_states) -> "CellColor":
        """spec/59 export-status cell colour: green = marked for export,
        red = not; videos aggregate their clips + snapshots (yellow =
        partial, the cluster grammar)."""
        from mira.picked.model import video_edit_color
        from mira.picked.status import CellColor
        if item_kind == "video":
            return video_edit_color(
                self._eg, item_id, phase_states, self._process_default)
        ps = phase_states.get(item_id)
        st = (ps.state if ps is not None
              and ps.state in ("picked", "skipped")
              else self._process_default)
        return CellColor.KEPT if st == "picked" else CellColor.DISCARDED

    def _refresh_cluster_cell(self, idx: int) -> None:
        if self._current_cluster is None:
            return
        if not (0 <= idx < len(self._current_cluster_members)):
            return
        ci = self._current_cluster_members[idx]
        current = self.cluster_grid.cell_at(idx)
        was_visited = bool(
            current.render_data().cell.visited
            if current is not None else False
        )
        phase_states = self._eg.phase_states("edit") if self._eg else {}
        new_cell = CullCell(
            end_time=ci.capture_time_corrected or "",
            color=self._edit_status_color(
                ci.item_id, ci.kind, phase_states),
            item_id=ci.item_id, item_kind=ci.kind,
            visited=was_visited,
            exported=self._cell_exported(ci.item_id, ci.kind),
        )
        thumb = (current.render_data().thumbnail
                 if current is not None else None)
        self.cluster_grid.update_cell(
            idx, CellRenderData(cell=new_cell, thumbnail=thumb))

    def _refresh_day_cell_for_cluster(self, bucket_key: str) -> None:
        for idx, cell in enumerate(self._current_day_cells):
            if (cell.is_cluster and cell.cluster is not None
                    and cell.cluster.bucket_key == bucket_key):
                self._refresh_day_cell(idx)
                return

    def _reproject_cell(self, cell: CullCell) -> CullCell:
        """spec/59 export-status colour from live ``phase_state`` rows —
        green = marked for export, red = not, video cells aggregate
        (yellow = partial). Honours ``self._process_default`` ("born
        green" out of the box). Preserves visited (Mvis)."""
        if self._eg is None:
            return cell
        phase_states = self._eg.phase_states("edit")
        if cell.is_cluster and cell.cluster is not None:
            colors = [
                self._edit_status_color(m.item_id, m.kind, phase_states)
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
        return CullCell(
            end_time=cell.end_time,
            color=self._edit_status_color(
                cell.item_id, cell.item_kind, phase_states),
            item_id=cell.item_id,
            item_kind=cell.item_kind,
            visited=cell.visited,
            exported=self._cell_exported(cell.item_id, cell.item_kind),
        )

    # ── Linear nav conductor (spec/32 §2.7) ──────────────────────────

    def _navigate(self, delta: int) -> None:
        if (self._current_day_cell_idx is None
                or not self._current_day_cells):
            return
        if (self._stack.currentIndex() == self._PHOTO
                and self._surface_came_from == self._CLUSTER_GRID):
            return
        nxt = self._current_day_cell_idx + delta
        if nxt < 0 or nxt >= len(self._current_day_cells):
            return
        self._current_day_cell_idx = nxt
        cell = self._current_day_cells[nxt]
        if cell.is_cluster and cell.cluster is not None:
            self._open_cluster(cell.cluster)
        elif cell.item_kind == "video" and cell.item_id is not None:
            self._open_video_item(cell.item_id, came_from=self._DAY_GRID)
        elif cell.item_id is not None:
            self._open_photo_item(cell.item_id, came_from=self._DAY_GRID)

    # ── Back routing ─────────────────────────────────────────────────

    def _on_day_grid_back(self) -> None:
        self._refresh_days_navigator()
        self._stack.setCurrentIndex(self._NAV)
        self.navigator.setFocus()

    def _on_cluster_back(self) -> None:
        if self._current_cluster is not None:
            self._refresh_day_cell_for_cluster(self._current_cluster.bucket_key)
        self._current_cluster = None
        self._current_cluster_members = ()
        self._stack.setCurrentIndex(self._DAY_GRID)
        self.day_grid.setFocus()

    def _on_photo_back(self) -> None:
        self._mark_current_day_dirty()
        # Re-fetch adjustments for this day so the colour reprojection picks
        # up any edit_exported flips from the surface session.
        self._reload_adjustments_cache()
        if (self._surface_came_from == self._CLUSTER_GRID
                and self._current_cluster is not None):
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

    def _on_video_back(self) -> None:
        self._mark_current_day_dirty()
        self._reload_adjustments_cache()
        if (self._surface_came_from == self._CLUSTER_GRID
                and self._current_cluster is not None):
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

    def _reload_adjustments_cache(self) -> None:
        if self._eg is None or self._current_day_number is None:
            return
        try:
            self._adjustments_cache = self._eg.adjustments_for_day(
                self._current_day_number)
        except Exception:  # noqa: BLE001
            log.exception("adjustments_for_day refresh failed")

    def _refresh_current_day_cells_from_bucket(self) -> None:
        """The back-refresh fix — see
        ``[[feedback_back_refresh_track_touched_items]]``."""
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
                # Mirror the DB visited bit into the in-memory cell BEFORE
                # _reproject_cell preserves it (otherwise the tick stays
                # off until the next day open).
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
        """Days panel Quit → close gateway → emit closed."""
        self._close_gateway()
        self._event_id = None
        self.closed.emit()

    # ── "Start a new pass…" (spec/32 §2.10) ──────────────────────────

    def _on_clear_marks(self) -> None:
        """User clicked "Start a new pass…" on the Process days panel —
        confirm, then wipe every ✓ tick (item_visit + bucket.browsed) at
        Process.  Export decisions (Adjustment.edit_exported) are
        preserved.

        Refreshes any open Day Grid cells so the ticks disappear without
        leaving + re-entering the day.
        """
        if self._eg is None:
            return
        resp = QMessageBox.question(
            self, tr("Start a new pass?"),
            tr(
                "This clears every ✓ tick on the Process cells and "
                "clusters you've already opened.  Your exported / "
                "unexported state is not touched.  Continue?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        try:
            n = self._eg.clear_visited_for_phase("edit")
        except Exception:  # noqa: BLE001
            log.exception("clear_visited_for_phase failed (process)")
            return
        # Refresh any open Day Grid cells so the ticks disappear immediately.
        if self._current_day_cells:
            for idx, cell in enumerate(self._current_day_cells):
                if cell.visited:
                    self._current_day_cells[idx] = CullCell(
                        end_time=cell.end_time, color=cell.color,
                        item_id=cell.item_id, item_kind=cell.item_kind,
                        cluster=cell.cluster, visited=False,
                    )
                    self._refresh_day_cell(idx)
        log.info(
            "clear_visited_for_phase(process): %d item ticks cleared", n)

    # ── Export scope hand-offs from EditPage ──────────────────────

    def _on_export_scope_requested(self, scope: str) -> None:
        """EditPage emits "day" / "event" when the user picks a wider
        export scope. The host gathers the green PHOTO items (incl.
        snapshots) AND the picked clip SEGMENTS in that scope, builds
        one fully-resolved manifest, and runs the spec/60 batch engine.

        Single-photo / single-clip exports stay on EditPage /
        EditVideoPage's own buttons (as-you-go path, spec/60 §8 — not
        through the queue)."""
        if scope == "day":
            if self._current_day_number is None:
                return
            items = self._collect_photo_items_for_day(
                self._current_day_number)
            clips = self._collect_clip_segments_for_day(
                self._current_day_number)
            scope_label = tr("edited media · day")
            confirm_threshold = 50
        elif scope == "event":
            items = self._collect_photo_items_for_event()
            clips = self._collect_clip_segments_for_event()
            scope_label = tr("edited media · event")
            confirm_threshold = 100
        else:
            return
        if not items and not clips:
            QMessageBox.information(
                self, tr("Nothing to export"),
                tr("No media to export here."))
            return
        total = len(items) + len(clips)
        if total >= confirm_threshold:
            resp = QMessageBox.question(
                self, tr("Export?"),
                tr("This will export {p} photo(s) and {c} clip(s). "
                   "Continue?").replace(
                    "{p}", str(len(items))).replace(
                    "{c}", str(len(clips))),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if resp != QMessageBox.StandardButton.Yes:
                return
        self._run_batched_export(items, scope_label, clip_segments=clips)

    def _refresh_exported_ids(self) -> None:
        """Re-read the edit-lineage id set (the spec/59 §8 watermark
        driver). Cheap (one indexed DISTINCT); skipped entirely when the
        watermark setting is off."""
        if self._eg is None or not self._watermark_enabled:
            self._exported_ids = set()
            return
        try:
            self._exported_ids = self._eg.exported_item_ids()
        except Exception:  # noqa: BLE001 — display-only; never block a page
            log.exception("exported_item_ids failed")
            self._exported_ids = set()

    def _cell_exported(self, item_id: str, item_kind: str) -> bool:
        return (self._watermark_enabled and item_kind == "photo"
                and item_id in self._exported_ids)

    def _is_marked_for_export(self, item_id: str, phase_states) -> bool:
        """spec/59: the GREEN set — explicit edit pick, or un-decided
        with a born-green default."""
        ps = phase_states.get(item_id)
        st = (ps.state if ps is not None
              and ps.state in ("picked", "skipped")
              else self._process_default)
        return st == "picked"

    def _collect_photo_items_for_day(self, day_number) -> List[CullItem]:
        """The MARKED-FOR-EXPORT photo items in ``day_number`` (spec/59:
        batch exports the green set). Includes snapshots (photo-shaped
        items per spec/56). Picked clip segments are collected
        separately via :meth:`_collect_clip_segments_for_day` —
        spec/56 §6 slice-4 walker."""
        states = self._eg.phase_states("edit") if self._eg else {}
        for d in self._days:
            if d.day_number != day_number:
                continue
            return [
                ci for b in d.buckets for ci in b.items
                if ci.kind == "photo"
                and self._is_marked_for_export(ci.item_id, states)
            ]
        return []

    def _collect_photo_items_for_event(self) -> List[CullItem]:
        states = self._eg.phase_states("edit") if self._eg else {}
        return [
            ci for d in self._days for b in d.buckets for ci in b.items
            if ci.kind == "photo"
            and self._is_marked_for_export(ci.item_id, states)
        ]

    def _collect_clip_segments_for_day(self, day_number) -> list:
        """Picked segment ROWS (:class:`VideoSegment`) for the day —
        the spec/60 §7 manifest-building input for the clip lane.
        Only PICKED items count (snapshots are photos and ride the
        photo collector; whole-video Skip means no segments at all)."""
        if self._eg is None or day_number is None:
            return []
        from mira.store import models as m
        picked = self._eg.items(
            phase="edit", state="picked", kind="video",
            provenance="clip", day=day_number)
        rows = []
        for it in picked:
            seg = self._eg.store.get(m.VideoSegment, it.id)
            if seg is not None:
                rows.append(seg)
        return rows

    def _collect_clip_segments_for_event(self) -> list:
        if self._eg is None:
            return []
        from mira.store import models as m
        picked = self._eg.items(
            phase="edit", state="picked", kind="video", provenance="clip")
        rows = []
        for it in picked:
            seg = self._eg.store.get(m.VideoSegment, it.id)
            if seg is not None:
                rows.append(seg)
        return rows

    def _run_batched_export(
        self, items: List[CullItem], scope_label: str,
        *, clip_segments: Optional[list] = None,
    ) -> None:
        """Batch export over ``items`` (photos + snapshots) and
        ``clip_segments`` (picked spec/56 segments) through the spec/60
        engine: a fully-resolved manifest (per-item recipes read from
        Adjustment rows HERE, where the gateway lives) handed to one
        render-worker process; per-unit results drive the commit.
        Items grouped per-day so each unit's ``dest_dir`` (the
        ``Edited Media/<Dia N>/`` sub-folder) is right.
        """
        from core.cull_export import ExportFileType
        from core.edit_export_walker import build_clip_units
        from core.export_manifest import ExportManifest, PhotoUnit
        from core.settings import load_settings
        from mira.ui.edited.export_dialog import ExportDialog
        from mira.ui.edited.export_job import BatchExportJob

        if self._eg is None:
            return
        clip_segments = list(clip_segments or [])

        from core.path_builder import edited_media_dir
        settings = load_settings()
        aspect_label = str(
            settings.get("preferred_aspect_ratio") or "Original")
        default_dest = edited_media_dir(Path(self._eg.event_root))

        # Group items by day_number.
        by_day: Dict[Optional[int], List[CullItem]] = {}
        for ci in items:
            it = self._eg.item(ci.item_id)
            d = it.day_number if it is not None else self._current_day_number
            by_day.setdefault(d, []).append(ci)
        label_by_day = {
            day_n: self._day_label_for_day_number(day_n)
            for day_n in by_day
        }
        # Clip segments inherit the source video's day_number for the
        # folder layout — the destination follows the source.
        clip_day_by_video: Dict[str, Optional[int]] = {}
        for seg in clip_segments:
            video = self._eg.item(seg.video_item_id)
            d = video.day_number if video is not None else None
            clip_day_by_video[seg.video_item_id] = d
            label_by_day.setdefault(d, self._day_label_for_day_number(d))

        def collision_probe(dest: Path) -> int:
            n = 0
            for day_n, day_items in by_day.items():
                day_dir = dest / label_by_day[day_n]
                for ci in day_items:
                    if (day_dir / (ci.path.stem + ".jpg")).exists():
                        n += 1
            return n

        choice = ExportDialog.ask(
            default_dest,
            default_file_type=ExportFileType.JPEG,
            collision_probe=collision_probe,
            scope_label=scope_label,
            parent=self,
        )
        if choice is None:
            return

        # Build the per-path style map so AUTO uses the right preset.
        styles_by_path = self._collect_styles_by_path(items)

        # The manifest: every unit fully resolved (spec/60 §1 — the
        # worker never touches event.db). The recipe fields mirror the
        # retired per-bucket journal glue exactly: explicit Adjustment
        # row → the CHOICE + crop/rotation; no row → fresh AUTO.
        units: list[PhotoUnit] = []
        source_by_unit_id: Dict[str, Path] = {}
        for day_n, day_items in by_day.items():
            dest_dir = str(Path(choice.destination) / label_by_day[day_n])
            for ci in day_items:
                adj = self._eg.adjustment(ci.item_id)
                look = None
                crop_norm = None
                crop_angle = 0.0
                rotation = 0
                if adj is not None:
                    look = {"look": adj.look or "natural"}
                    if adj.style:
                        look["style"] = adj.style
                    if adj.creative_filter:
                        look["creative_filter"] = adj.creative_filter
                    # Nelson 2026-06-13 — Look Strength threads through
                    # the manifest. Default 1.0 omitted to keep the
                    # JSON wire small (the worker reads via
                    # look.get("strength", 1.0)).
                    s = float(adj.look_strength)
                    if abs(s - 1.0) > 1e-6:
                        look["strength"] = s
                    if all(v is not None for v in (
                            adj.crop_x, adj.crop_y,
                            adj.crop_w, adj.crop_h)):
                        crop_norm = (adj.crop_x, adj.crop_y,
                                     adj.crop_w, adj.crop_h)
                    crop_angle = float(adj.crop_angle or 0.0)
                    rotation = int(adj.rotation or 0)
                units.append(PhotoUnit(
                    unit_id=ci.item_id,
                    source=str(ci.path),
                    dest_dir=dest_dir,
                    file_type=choice.file_type.value,
                    jpeg_quality=int(choice.jpeg_quality),
                    look=look,
                    auto_on=True,
                    style=styles_by_path.get(ci.path),
                    crop_norm=crop_norm,
                    crop_angle=crop_angle,
                    rotation=rotation,
                    aspect_label=aspect_label,
                ))
                source_by_unit_id[ci.item_id] = ci.path

        # Clip lane (spec/56 §6 + spec/60 §3). The walker stays
        # gateway-only; the workshop's tone compiler + override-shim
        # ride in as callables so we don't drag QtMultimedia code into
        # an export path that might run far from the workshop.
        from mira.ui.edited.edit_video_page import (
            _override_shim,
        )
        clip_units = build_clip_units(
            self._eg, clip_segments,
            event_root=Path(self._eg.event_root),
            dest_dir_for_video=lambda video: str(
                Path(choice.destination)
                / label_by_day[clip_day_by_video.get(video.id)]),
            resolved_params_for=None,    # batch tone compile is
            override_shim=_override_shim,
            # spec/54 §7 #1 deferred: per-clip rep-frame compile in
            # the walker would hammer ffmpeg up front for the whole
            # event. The override shim's params=None falls through to
            # the engine's identity Params; the look CHOICE rides in
            # the recipe for the lineage snapshot. (Re-introducing
            # rep-frame compile is a one-callable swap.)
        )
        for cu in clip_units:
            source_by_unit_id[cu.unit_id] = Path(cu.source)

        manifest = ExportManifest(
            units=tuple(units), clips=tuple(clip_units),
            collision=choice.collision.value)
        total = len(units) + len(clip_units)
        worker = BatchExportJob(manifest, source_by_unit_id)

        def commit(result) -> None:
            """The post-export DB commit — runs on the UI thread either
            way (queued: via the queue's finished relay; modal: via the
            local on_finished). PER-UNIT TRUTH (spec/60 §5): exported
            marks + lineage rows only for units that actually
            succeeded; failed cells simply don't turn. Cancel keeps the
            units already on disk — they are real, finished exports.
            A lost commit self-heals: the files sit under Edited Media
            and the next Edit-entry return scan associates them
            (spec/57 §3)."""
            ok_ids = getattr(result, "ok_unit_ids", set())
            unit_results = getattr(result, "unit_results", [])
            not_ok = [m for m in unit_results
                      if m.get("status") != "ok"]
            if not_ok:
                log.warning(
                    "batch export: %d of %d unit(s) did not export "
                    "(first: %s)", len(not_ok), total, not_ok[0])
            if not ok_ids:
                return
            ok_items = [ci for ci in items if ci.item_id in ok_ids]
            for ci in ok_items:
                try:
                    self._eg.set_edit_exported(ci.item_id, True)
                except Exception:  # noqa: BLE001
                    log.exception(
                        "set_edit_exported failed for %s",
                        ci.item_id)
            # Record export→source lineage so Share can walk back from
            # each processed file to its origin Item.
            try:
                from mira.ui.edited._lineage import (
                    record_edit_export_lineage,
                )
                record_edit_export_lineage(
                    self._eg,
                    Path(self._eg.event_root),
                    items_with_sources=[
                        (ci.item_id, ci.path) for ci in ok_items
                    ],
                    result=result,
                    recipe_by_item=self._collect_recipes(ok_items),
                    resolved_by_stem=getattr(
                        result, "resolved_by_name", {}),
                )
            except Exception:  # noqa: BLE001
                log.exception("record_edit_export_lineage failed")

            # Clip results: per-unit set_edit_exported + lineage. The
            # photo lineage writer keys on stems back to source items
            # by name; clips don't share that property (the output
            # name is "<video>_clipN.mp4") so they need their own
            # row-at-a-time path. The recipe re-reads VideoAdjustment
            # here, mirroring the workshop's single-clip commit.
            from mira.ui.edited._lineage import (
                record_single_lineage,
            )
            for cmsg in getattr(result, "ok_clip_results", []):
                clip_item_id = cmsg.get("unit_id", "")
                try:
                    self._eg.set_edit_exported(clip_item_id, True)
                except Exception:  # noqa: BLE001
                    log.exception(
                        "set_edit_exported failed for clip %s",
                        clip_item_id)
                try:
                    vadj = self._eg.video_adjustment(clip_item_id)
                    recipe: dict = {"look": "natural"}
                    if vadj is not None:
                        recipe["look"] = vadj.look or "natural"
                        if vadj.style:
                            recipe["style"] = vadj.style
                        if vadj.creative_filter:
                            recipe["creative_filter"] = vadj.creative_filter
                        if vadj.rep_frame_ms is not None:
                            recipe["rep_frame_ms"] = vadj.rep_frame_ms
                    record_single_lineage(
                        self._eg,
                        Path(self._eg.event_root),
                        item_id=clip_item_id,
                        dest_path=Path(cmsg["final_path"]),
                        recipe=recipe,
                        resolved_params=cmsg.get("params"),
                    )
                except Exception:  # noqa: BLE001
                    log.exception(
                        "clip lineage failed for %s", clip_item_id)
            self._reload_adjustments_cache()
            self._refresh_exported_ids()
            self._mark_current_day_dirty()
            # Refresh every visible cell whose item ended up green.
            touched_ids = set(ok_ids)
            for idx, cell in enumerate(self._current_day_cells):
                if (cell.item_id is not None
                        and cell.item_id in touched_ids):
                    self._refresh_day_cell(idx)
                elif (cell.is_cluster and cell.cluster is not None
                      and any(m.item_id in touched_ids
                              for m in cell.cluster.members)):
                    self._refresh_day_cell(idx)
            # Re-project the days panel so per-day "X/Y processed"
            # picks up the new flags.
            self._reproject_days_navigator()

        # spec/59 §8 — the batch queue: jobs run app-level, one at a
        # time, progress on the line below the menubar; the user keeps
        # working (no modal, no completion popup — the strip going idle
        # and the cells turning are the signal).
        queue = getattr(self.window(), "batch_queue", None)
        if queue is not None:
            worker.finished.connect(worker.deleteLater)
            queue.enqueue(
                worker,
                tr("Export — {label} ({n})")
                .replace("{label}", scope_label)
                .replace("{n}", str(total)),
                commit,
            )
            return

        # Fallback (no app window around, e.g. tests/standalone): the
        # legacy modal progress dialog.
        from PyQt6.QtWidgets import QProgressDialog
        dialog = QProgressDialog(
            tr("Exporting…"), tr("Cancel"), 0, total, self)
        dialog.setWindowTitle(tr("Process export"))
        dialog.setMinimumDuration(0)
        dialog.setValue(0)

        def on_progress(done: int, _total: int, name: str) -> None:
            dialog.setMaximum(_total)
            dialog.setValue(done)
            dialog.setLabelText(
                tr("Exporting {d}/{t}: {n}")
                .replace("{d}", str(done))
                .replace("{t}", str(_total))
                .replace("{n}", name))

        def on_finished(result) -> None:
            dialog.close()
            commit(result)
            cancelled = bool(getattr(worker, "_cancel", False))
            title = tr("Export cancelled") if cancelled else tr(
                "Export finished")
            QMessageBox.information(
                self, title,
                tr("Exported {n} of {t} photo(s).")
                .replace("{n}", str(getattr(result, "ok_count", 0)))
                .replace("{t}", str(total)))

        worker.progress.connect(on_progress)
        worker.finished_result.connect(on_finished)
        worker.finished.connect(worker.deleteLater)
        dialog.canceled.connect(worker.cancel)
        worker.start()
        dialog.exec()

    def _collect_recipes(self, items: List[CullItem]) -> Dict[str, dict]:
        """Per-item spec/54 §8 lineage-snapshot CHOICE dicts, keyed by
        item id — the day/event-scope twin of EditPage._recipe_for_item."""
        out: Dict[str, dict] = {}
        if self._eg is None:
            return out
        for ci in items:
            adj = self._eg.adjustment(ci.item_id)
            recipe: dict = {"look": "natural"}
            if adj is not None:
                recipe["look"] = adj.look or "natural"
                if adj.style:
                    recipe["style"] = adj.style
                if adj.creative_filter:
                    recipe["creative_filter"] = adj.creative_filter
                # Nelson 2026-06-13 — the lineage snapshot remembers
                # the strength the export baked, so a re-render years
                # later reproduces the exact pixels.
                if abs(float(adj.look_strength) - 1.0) > 1e-6:
                    recipe["look_strength"] = float(adj.look_strength)
                if all(v is not None for v in (
                        adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h)):
                    recipe["crop_norm"] = [
                        adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h]
                if adj.crop_angle:
                    recipe["crop_angle"] = adj.crop_angle
                if adj.rotation:
                    recipe["rotation"] = adj.rotation
                if adj.aspect_label:
                    recipe["aspect_label"] = adj.aspect_label
            out[ci.item_id] = recipe
        return out

    def _collect_styles_by_path(
        self, items: List[CullItem],
    ) -> Dict[Path, str]:
        """Per-photo style map for the engine's ``style_resolver``.

        Priority (Nelson 2026-06-09, spec/54 columns):
        1. ``Adjustment.style`` — explicit save from a previous edit
           session.
        2. ``item.classification`` — the photo's effective genre from the
           wizard / classifier (the previous-phases classification Nelson
           noticed was being dropped at AUTO time).
        3. ``None`` — fall through to engine default ("general").
        """
        from mira.ui.edited.edit_page import _normalize_style
        out: Dict[Path, str] = {}
        if self._eg is None:
            return out
        for ci in items:
            style: Optional[str] = None
            adj = self._eg.adjustment(ci.item_id)
            if adj is not None and adj.style and adj.style != "general":
                style = adj.style
            if style is None:
                # Fall back to the item's classification.
                try:
                    it = self._eg.item(ci.item_id)
                    cls = it.classification if it is not None else None
                except Exception:  # noqa: BLE001
                    cls = None
                normalised = _normalize_style(cls)
                if normalised != "general":
                    style = normalised
            if style is not None:
                out[ci.path] = style
        return out

    def _day_label_for_day_number(self, day_number) -> str:
        """Day-folder label for an arbitrary day_number (not just current).
        Used by the batched-export per-day grouping."""
        if self._eg is None or day_number is None:
            return ""
        try:
            days = {d.day_number: d for d in self._eg.trip_days()}
            td = days.get(day_number)
            if td is not None:
                bits = [b for b in (
                    f"Dia {td.day_number}", td.description, td.date,
                ) if b]
                return " — ".join(bits) if bits else f"Dia {td.day_number}"
        except Exception:  # noqa: BLE001
            pass
        return f"Dia {day_number}"

    def _on_photo_export_committed(self, item_id: str) -> None:
        """Photo-scope export from EditPage finished — refresh the
        relevant cell so its border goes green (and the watermark
        appears: new lineage row)."""
        self._reload_adjustments_cache()
        self._refresh_exported_ids()
        self._refresh_cell_for_item(item_id)
        self._mark_current_day_dirty()

    def _on_video_export_committed(self, item_id: str) -> None:
        """Video clip export finished — refresh."""
        self._reload_adjustments_cache()
        self._refresh_exported_ids()
        self._refresh_cell_for_item(item_id)
        self._mark_current_day_dirty()

    def _refresh_cell_for_item(self, item_id: str) -> None:
        for idx, cell in enumerate(self._current_day_cells):
            if cell.item_id == item_id:
                self._refresh_day_cell(idx)
                return
            if cell.is_cluster and cell.cluster is not None and any(
                m.item_id == item_id for m in cell.cluster.members
            ):
                self._refresh_day_cell(idx)
                return

    # ── Thumbnail lazy loader ────────────────────────────────────────

    def _enqueue_thumbnails(self, view, cells) -> None:
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
                continue
            self._thumb_pixmap_cache[item_id] = pm
            view.set_cell_thumbnail(idx, pm)
            done += 1
        if not self._thumb_pending:
            self._thumb_timer.stop()

    def _decode_thumbnail(self, item, path: Path):
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
            if item.sha256:
                from core.photo_thumb_cache import ensure_photo_thumb
                thumb_path = ensure_photo_thumb(
                    event_root=Path(self._eg.event_root),
                    source_path=path,
                    sha256=item.sha256,
                )
                return load_pixmap(thumb_path)
            return load_pixmap(path)
        except Exception:  # noqa: BLE001
            log.debug("thumbnail decode failed for %s", path)
            return None


__all__ = ["EditHostPage"]
