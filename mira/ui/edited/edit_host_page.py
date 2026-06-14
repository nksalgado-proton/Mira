"""EditHostPage — Day-Grid parent for the Edit phase.

spec/66 (2026-06-14): Edit is creative-only — classification / tone / crop.
The batch-export trigger and the green/red mark-for-export grammar that
spec/59 §8 put on this host both **moved out** to the new Export surface
(Slice 4 of the spec/66 implementation pass).

* **Keeps** the full nav stack: days panel → Day Grid → cluster sub-grid →
  photo / video surface; the linear-nav conductor (``_navigate``); the
  thumbnail lazy loader; the Mvis visited tick hooks
  (``set_bucket_browsed`` / ``set_item_visited``); the touched-set
  back-refresh (``_items_touched_in_surface`` + the in-memory visited
  stamp inside ``_refresh_current_day_cells_from_bucket``).
* **Routes** centre-click by item kind: photo / snapshot →
  :class:`mira.ui.edited.edit_page.EditPage`; video →
  :class:`mira.ui.edited.edit_video_page.EditVideoPage`;
  cluster → cluster sub-grid (then sub-grid centre-click opens EditPage
  with the real cluster bucket).
* **Item pool** = Pick-kept items only (:func:`mira.picked.edit_model.process_days`).

The day-grid cell border is a no-op here (border-click marking moved to
the Export surface). Cells stay coloured at the phase default.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QStackedWidget, QVBoxLayout, QWidget

from mira.picked import (
    CullBucket,
    CullCell,
    CullCluster,
    PickDay,
    CullItem,
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
        # spec/66 §1.1 — Exported watermark + green/red marking moved to
        # the Export surface; Edit no longer keeps the lineage id set.
        # Per-day cache of Adjustment rows survives — visited-tick refresh
        # uses it to project cells back from a surface session.
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
        # so the user knows they're in Edit. spec/66 §1.1 (2026-06-14):
        # Edit is creative-only — no Export-All button, no border-click
        # marking (that grammar moved to the Export surface). The per-day
        # "X/Y reviewed" count tracks how many keepers have an Adjustment
        # row (developed / cleared via Edit), the spec/66 Edit metric.
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
        )
        self.navigator = BucketNavigator(config=nav_config)
        self.navigator.day_activated.connect(self._on_day_activated)
        self.navigator.back_requested.connect(self._on_back)
        self.navigator.clear_marks_requested.connect(self._on_clear_marks)
        # Drop batch ops at Edit — wire to a no-op so the navigator
        # doesn't crash if a button fires.
        try:
            self.navigator.batch_op_requested.connect(lambda *_a: None)
        except Exception:  # noqa: BLE001
            pass

        # Day Grid + cluster sub-grid (same widget, two slots).
        # Nelson 2026-06-06: "Start a new pass…" lives on the day grid
        # itself — same handler the navigator's button uses.
        self.day_grid = DayGridView(
            show_clear_marks_button=True,
        )
        self.day_grid.back_requested.connect(self._on_day_grid_back)
        self.day_grid.cell_activated.connect(self._on_day_cell_activated)
        self.day_grid.clear_marks_requested.connect(self._on_clear_marks)
        # spec/66 §1.1 — border click is a no-op on Edit: the green/red
        # mark-for-export decision moved to the Export surface.
        self.day_grid.cell_border_clicked.connect(lambda *_a: None)

        self.cluster_grid = DayGridView(enable_arrow_nav=True)
        self.cluster_grid.back_requested.connect(self._on_cluster_back)
        self.cluster_grid.cell_activated.connect(self._on_cluster_cell_activated)
        self.cluster_grid.cell_border_clicked.connect(lambda *_a: None)
        self.cluster_grid.navigate_at_edge.connect(self._navigate)

        # Process editor surfaces. spec/66 §1.1 — the per-surface export
        # signals retired with Slice 4; the host no longer listens for
        # them (none exist).
        self.photo = EditPage()
        self.photo.back_requested.connect(self._on_photo_back)
        self.photo.fullscreen_changed.connect(self.fullscreen_changed.emit)
        self.photo.navigate_at_edge.connect(self._navigate)

        self.video = EditVideoPage()
        self.video.back_requested.connect(self._on_video_back)
        self.video.fullscreen_changed.connect(self.fullscreen_changed.emit)
        self.video.navigate_at_edge.connect(self._navigate)

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
        # spec/66 §1.1 — Edit is creative-only; the "born-green" mark
        # default, the watermark gate and the marking-for-export plumbing
        # all moved to the Export surface (Slice 5).
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
            cells = day_grid_cells(
                self._eg, day_number, phase="edit",
                days=self._days,
                default_state=self._process_default,
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
        self.video.load(
            self._eg, bucket,
            nav_context=nav_context,
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

    # ── Refresh helpers ──────────────────────────────────────────────

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
        adjustments = self._adjustments_cache or {}
        new_cell = CullCell(
            end_time=ci.capture_time_corrected or "",
            color=cell_color_for_process_item(
                ci.item_id, adjustments,
                default_state=self._process_default,
            ),
            item_id=ci.item_id, item_kind=ci.kind,
            visited=was_visited,
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
        """spec/66 §1.1 — Edit no longer drives a green/red marking
        decision; cell colour stays at the phase default (the picked
        keepers are all "kept", coloured by the process default). This
        pass exists to refresh the visited (Mvis) tick after a surface
        session, not to recompute status."""
        return cell

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
