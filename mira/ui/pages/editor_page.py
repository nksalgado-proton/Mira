"""Surface 08 — Editor (look / strength / style / filter / crop).

spec/70 Phase 3 §3 reconciliation. The redesigned shell **embeds**
:class:`~mira.ui.media.photo_viewport.PhotoViewport` (spec/63's "one
engine, every surface" thesis) and **absorbs** the gateway/engine wiring
the legacy ``EditPage`` carried — the off-thread working-copy prep, the
``Adjustment`` row round-trip, the developed-preview F10 lens, the crop
overlay with draggable handles. The engines (the
:class:`~mira.ui.edited.adjustment_surface.AdjustmentSurface` state +
math + the :class:`~core.photo_render` pipeline + the per-process
:func:`~mira.ui.edited.edit_prep.edit_prep` worker) are REUSED, not
rewritten.

spec/66 (2026-06-14): Edit is **purely creative** — Classification +
Tone + Crop only. The export grammar (the green/red mark-for-export
border, the Exported watermark, the batch trigger) moved to the
separate Export phase surface (slice 4). The locked-map decision keys
P/X/Space/C are inert here — there is no Pick/Skip ledger on a
creative-only surface; the viewport still emits the verbs but this
page does not connect to them.

Locked keymap (spec/63 §4):
    L · Shift+L     next / previous Look
    G               Look grid (all four side by side)
    [ · ]           rotate the crop box 90°
    \\               toggle Compare (before / after)
    R               reset this photo
    F10             developed Preview — full-resolution, cropped, the
                    exact output Export produces (the "in Edit = the
                    developed Preview" rule)
    F / F11         fullscreen
    Esc             back (one level — fullscreen → windowed → out)
    Home / End      first / last item
    ← / ↑ / → / ↓   previous / next item

Composition (spec/70 §1 — shell wraps engine):
    Toolbar:    ‹ Back · 'N / Total' counter · spacer
    Tools area: AdjustmentSurface.tools_widget() reparented here —
                the spec/59 §2 top grid (Style / Look / Filter row +
                Crop row + Compare / Toggle Crop / Reset all action
                row). One source of truth for tone editing.
    Stage:      AdjustmentSurface.display_widget() = PhotoViewport,
                reparented here. The CropOverlay (with draggable
                corner + edge handles, aspect-lock and the standard-
                correction baseline) parents to the viewport's
                photo_area_widget(); drag and aspect both work for
                free.
    Bottom:     Filmstrip · Full Resolution F10 · Full Screen F11.

Public API (Days Grid bridge — mirrors :class:`PickerPage`):
    * :meth:`open_to_item` — flat single-item click (synthetic 1-item
      bucket so the chrome lights up uniformly).
    * :meth:`open_to_cluster` — cluster sub-grid member click (real
      cluster bucket so intra-cluster ← → works).

Signals:
    * ``closed`` — Back / Esc; the host returns to the Days Grid.
    * ``fullscreen_changed(bool)`` — shell hides/restores its chrome.

The legacy ``mira/ui/edited/edit_page.py`` + ``edit_host_page.py`` are
retired with this surface; ``edit_video_page.py`` stays (Surface 12 is
a separate reconciliation).
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor, QKeyEvent
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.photo_decoder import is_supported

from mira.gateway import Gateway
from mira.gateway.event_gateway import EventGateway
from mira.picked import CullBucket, CullCluster, CullItem
from mira.picked.status import (
    BADGE_UNTOUCHED,
    BucketStatus,
    default_state_for,
    project_status,
)
from mira.store import models as m
from mira.ui.design import (
    ghost_button,
    nav_arrow,
)
from mira.ui.edited.adjustment_surface import (
    AdjustmentSurface,
    normalize_style,
)
from mira.ui.edited.edit_prep import PrepResult, edit_prep
from mira.ui.i18n import tr
from mira.ui.media.photo_viewport import ViewportItem, open_inspect_lens

log = logging.getLogger(__name__)


# ── EditorPage ────────────────────────────────────────────────────────


class EditorPage(QWidget):
    """Surface 08 — single-photo Editor (PhotoViewport-backed)."""

    closed = pyqtSignal()
    fullscreen_changed = pyqtSignal(bool)

    def __init__(
        self,
        gateway: Optional[Gateway] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("EditorPage")
        self.gateway = gateway
        self._phase = "edit"
        self._eg: Optional[EventGateway] = None
        self._event_id: Optional[str] = None
        self._bucket: Optional[CullBucket] = None
        self._items: list[CullItem] = []
        self._index = 0
        self._fullscreen = False
        # Position chip — "where am I in the current day" (set by the
        # Days Grid bridge via open_to_item / open_to_cluster).
        self._day_number = 1
        self._day_index = 1
        self._day_total = 1
        # The path whose working copy is currently loaded into the
        # surface — used to gate the developed view + drop stale prep
        # deliveries.
        self._cached_path: Optional[Path] = None
        # Re-entrancy guard for laggy ops (the developed-preview render
        # cost; the wait-cursor pattern from the legacy EditPage).
        self._busy_flag = False

        # ── The engine: AdjustmentSurface owns the math + state +
        # display_widget (PhotoViewport) + the CropOverlay's draggable
        # handles. We reparent its tools_widget into our controls area
        # and its display_widget into our stage. The legacy EditPage
        # did the same dance through BaseEditSurface.
        self._surface = AdjustmentSurface()
        self._surface.changed.connect(self._on_surface_changed)
        self._surface.style_decided.connect(self._on_style_decided)

        # ── The viewport — the one display engine (spec/63 §6.1). ──
        self._viewport = self._surface.display_widget()
        self._viewport.current_changed.connect(self._on_current_changed)
        self._viewport.edge_reached.connect(self._on_edge)
        # spec/63 §4 — F10 = the developed Preview (Edit's truth key,
        # the lens shows the processed-cropped full-res render → what
        # Export produces).
        self._viewport.truth_requested.connect(self._open_processed_lens)
        self._viewport.fullscreen_requested.connect(self._toggle_fullscreen)
        self._viewport.back_requested.connect(self._on_esc)
        # spec/66 §1.1 — Edit is creative-only. P/X/Space/C STILL fire
        # on the viewport (the locked map is universal) but this page
        # does not connect to them.

        # ── Prep worker (off-thread decode + downsample + Natural). ──
        # The process-wide singleton — the worker emits ONLY to it,
        # same-thread delivery into this page so a dying page never
        # races a cross-thread emission (the 0xC0000409 fix).
        self._prep = edit_prep()
        self._prep.prepared.connect(self._on_prep_ready)
        self._prep.prep_failed.connect(self._on_prep_failed)
        # The settle gate (the Picker's 150 ms cadence): a held arrow
        # never queues a prep job; only the photo the user LANDED on
        # asks for a working copy.
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(150)
        self._settle.timeout.connect(self._on_settle)

        self._build_ui()
        # The legacy focus discipline (5d): every chrome widget except the
        # viewport (and QLineEdits, which need ClickFocus to type into) is
        # NoFocus, so a click on Back / Look pills / combos / arrows never
        # steals focus from the viewport and the locked §4 keys keep
        # firing. Without this the redesigned chrome's StrongFocus
        # buttons swallowed every L / R / G / [ / ] press the moment the
        # user clicked any control.
        self._install_keyboard_focus()
        # The page's setFocus lands on the viewport — the §4 grammar's
        # home (the 5d focus-proxy pattern).
        self.setFocusProxy(self._viewport)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ── UI assembly ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(20, 16, 20, 16)
        self._outer.setSpacing(10)

        # ── Toolbar — Back · counter ──
        self._toolbar_widget = QWidget()
        toolbar = QHBoxLayout(self._toolbar_widget)
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(10)
        self._back_btn = ghost_button(tr("‹ Back"))
        self._back_btn.setToolTip(tr("Return to the day grid  (Esc)"))
        self._back_btn.clicked.connect(self._on_back)
        toolbar.addWidget(self._back_btn)
        self._counter = QLabel("0 / 0")
        self._counter.setObjectName("Sub")
        self._counter.setToolTip(tr(
            "Position in the current day — item index of total."))
        toolbar.addWidget(self._counter)
        toolbar.addStretch(1)
        self._outer.addWidget(self._toolbar_widget)

        # ── Tools area — the AdjustmentSurface tools widget. The spec/59
        # §2 top grid IS the redesigned controls panel; reparenting it
        # keeps engine + UI in lockstep (no double source of truth).
        # Compare / Toggle Crop / Reset all live in its action row.
        self._tools = self._surface.tools_widget()
        self._tools.setParent(self)
        self._outer.addWidget(self._tools)

        # ── Stage — the PhotoViewport (the one display engine). The
        # CropOverlay parents to its photo_area_widget() inside
        # AdjustmentSurface; drag handles + aspect-lock + standard
        # correction baseline come for free.
        self._viewport.setParent(self)
        self._viewport.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._outer.addWidget(self._viewport, 1)

        # ── Bottom bar — ◀ prev · spacer · Full Resolution F10 · Full
        # Screen F11 · spacer · ▶ next. The bucket-level filmstrip
        # belongs to the Days Grid (the upstream overview); this row
        # keeps the per-bucket arrows handy so the user has a click
        # path that matches ← / → and a single hand always knows where
        # the chrome lives.
        self._bottom_widget = QWidget()
        bottom = QHBoxLayout(self._bottom_widget)
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(10)
        self._prev_btn = nav_arrow("left")
        self._prev_btn.setToolTip(tr("Previous photo  (←)"))
        self._prev_btn.clicked.connect(self._on_prev)
        bottom.addWidget(self._prev_btn)
        bottom.addStretch(1)
        self._fullres_btn = ghost_button(tr("Full Resolution  F10"))
        self._fullres_btn.setToolTip(tr(
            "See the processed, cropped photo at full resolution — "
            "exactly what Export produces  (F10)"))
        self._fullres_btn.clicked.connect(self._open_processed_lens)
        bottom.addWidget(self._fullres_btn)
        self._fullscreen_btn = ghost_button(tr("Full Screen  F11"))
        self._fullscreen_btn.setCheckable(True)
        self._fullscreen_btn.setToolTip(tr(
            "Fill the screen with the photo (chrome hides)  (F / F11)"))
        self._fullscreen_btn.clicked.connect(self._toggle_fullscreen)
        bottom.addWidget(self._fullscreen_btn)
        bottom.addStretch(1)
        self._next_btn = nav_arrow("right")
        self._next_btn.setToolTip(tr("Next photo  (→)"))
        self._next_btn.clicked.connect(self._on_next)
        bottom.addWidget(self._next_btn)
        self._outer.addWidget(self._bottom_widget)

    # ── Focus discipline ───────────────────────────────────────────────

    def _install_keyboard_focus(self) -> None:
        """Walk every child widget and lock its focus policy: the
        viewport keeps StrongFocus (the §4 grammar's home),
        QLineEdits get ClickFocus (so the user can type), everything
        else (buttons, combos, sliders, labels) gets NoFocus. Without
        this discipline a click on the redesign-shell Back / nav arrow
        / Full Screen / Full Resolution / Look pill steals focus from
        the viewport and the locked-map keys go silent — the bug
        Nelson hit on first eyeball (2026-06-14)."""
        from PyQt6.QtWidgets import QLineEdit
        for w in self.findChildren(QWidget):
            if w is self._viewport:
                continue
            if isinstance(w, QLineEdit):
                w.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            else:
                w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def focusNextPrevChild(self, nxt: bool) -> bool:  # noqa: N802
        """Disable Tab focus traversal — Tab is the §4 transport key
        (inert on stills, play/pause on clips), never a focus walker."""
        return False

    def showEvent(self, event) -> None:  # noqa: N802
        """Each time the page becomes visible, route focus to the
        viewport (via the proxy) so the locked map fires immediately —
        no click required."""
        super().showEvent(event)
        self.setFocus()

    # ── Prev / next handlers (chrome shortcuts; viewport also handles
    # the keys itself, but these power the bottom arrows) ─────────────

    def _on_prev(self) -> None:
        if self._items and self._index > 0:
            self._viewport.show_index(self._index - 1)

    def _on_next(self) -> None:
        if self._items and self._index < len(self._items) - 1:
            self._viewport.show_index(self._index + 1)

    # ── Public entry points (Days Grid bridge — mirror PickerPage) ────

    def open_to_item(
        self, event_id: str, day_number: int, item_id: str,
    ) -> bool:
        """Open the Editor on a flat single-item click from the Days Grid
        (Surface 06). Builds a synthetic 1-item bucket so the chrome
        + viewport navigate within just that item.

        Returns ``True`` on success. ``False`` leaves the page in a clean
        state so the host can leave the user on the Days Grid.
        """
        if not self._open_event(event_id):
            return False
        try:
            cull_item = self._cull_item_for(item_id)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "open_to_item: cannot resolve %s in event %s",
                item_id, event_id)
            self._close_event()
            return False
        if cull_item is None:
            self._close_event()
            return False
        # spec/32 §2.10 — opening an item marks it visited at Edit.
        try:
            self._eg.set_item_visited(item_id, self._phase)
        except Exception:                                          # noqa: BLE001
            log.exception("set_item_visited failed for %s", item_id)
        bucket = self._synthetic_bucket(cull_item)
        self._day_number = day_number
        self._compute_day_position(day_number, item_id)
        self._load_bucket(bucket, entry_idx=0)
        return True

    def open_to_cluster(
        self, event_id: str, day_number: int,
        cluster: CullCluster, entry_idx: int = 0,
    ) -> bool:
        """Open the Editor on a cluster sub-grid member click. Loads the
        REAL cluster bucket so intra-cluster ← → works.

        Returns ``True`` on success.
        """
        if not self._open_event(event_id):
            return False
        try:
            if 0 <= entry_idx < len(cluster.members):
                self._eg.set_item_visited(
                    cluster.members[entry_idx].item_id, self._phase)
        except Exception:                                          # noqa: BLE001
            log.exception("cluster set_item_visited failed")
        phase_states = self._eg.phase_states(self._phase)
        bucket = CullBucket(
            bucket_key=cluster.bucket_key,
            kind=cluster.kind,
            title=cluster.title,
            items=cluster.members,
            status=project_status(
                [mem.item_id for mem in cluster.members],
                phase_states,
                self._eg.bucket(cluster.bucket_key, self._phase),
            ),
            detection_source=cluster.detection_source,
            camera=cluster.camera,
        )
        self._day_number = day_number
        self._day_index = entry_idx + 1
        self._day_total = len(cluster.members)
        self._load_bucket(bucket, entry_idx=entry_idx)
        return True

    def close_event(self) -> None:
        """Release any open event gateway. Idempotent."""
        self._close_event()

    def shutdown(self) -> None:
        """Quiesce before destruction: stop the settle timer, leave the
        prep singleton's signal fan-out, drop the viewport's items.
        After this, nothing external can deliver into the page — the
        defined lifecycle end (the legacy EditPage shutdown discipline).
        Idempotent."""
        try:
            self._settle.stop()
        except Exception:                                          # noqa: BLE001
            pass
        for sig, slot in (
                (self._prep.prepared, self._on_prep_ready),
                (self._prep.prep_failed, self._on_prep_failed)):
            try:
                sig.disconnect(slot)
            except Exception:                                      # noqa: BLE001
                pass
        try:
            self._viewport.set_items([])
        except Exception:                                          # noqa: BLE001
            pass

    def event(self, ev) -> bool:  # noqa: N802
        from PyQt6.QtCore import QEvent
        if ev.type() == QEvent.Type.DeferredDelete:
            self.shutdown()
        return super().event(ev)

    # ── Lifecycle helpers ──────────────────────────────────────────────

    def _open_event(self, event_id: str) -> bool:
        if self.gateway is None:
            log.warning("EditorPage._open_event called without a gateway")
            return False
        self._close_event()
        try:
            self._eg = self.gateway.open_event(event_id)
        except Exception:                                          # noqa: BLE001
            log.exception("EditorPage: cannot open event %s", event_id)
            return False
        self._event_id = event_id
        return True

    def _close_event(self) -> None:
        self._settle.stop()
        if self._eg is not None:
            try:
                self._eg.close()
            except Exception:                                      # noqa: BLE001
                log.exception("EventGateway close failed")
        self._eg = None
        self._event_id = None
        self._bucket = None
        self._items = []
        self._index = 0
        self._cached_path = None

    def _cull_item_for(self, item_id: str) -> Optional[CullItem]:
        """Build a one-shot ``CullItem`` for a flat single-item bridge."""
        if self._eg is None:
            return None
        item = self._eg.item(item_id)
        if item is None or not item.origin_relpath:
            return None
        path = Path(self._eg.event_root) / item.origin_relpath
        return CullItem(
            item_id=item.id,
            path=path,
            kind=item.kind,
            capture_time_corrected=item.capture_time_corrected or None,
            duration_ms=item.duration_ms,
        )

    def _synthetic_bucket(self, ci: CullItem) -> CullBucket:
        """Wrap a single CullItem in a 1-item bucket so the page's
        bucket-shaped load path lights up uniformly."""
        return CullBucket(
            bucket_key=f"single:{ci.item_id}",
            kind=ci.kind,
            title="",
            items=(ci,),
            status=BucketStatus(
                total=1, kept=0, candidate=0, discarded=0, untouched=1,
                reviewed=False, browsed=False, badge=BADGE_UNTOUCHED),
        )

    def _compute_day_position(self, day_number: int, item_id: str) -> None:
        """Resolve "Cell N of Total" for the position chip. Mirrors
        :class:`PickerPage._compute_day_position`."""
        if self._eg is None:
            return
        try:
            from mira.picked import day_grid_cells
            cells = day_grid_cells(
                self._eg, day_number, phase=self._phase,
                default_state=default_state_for(
                    self.gateway.settings, self._phase),
            )
        except Exception:                                          # noqa: BLE001
            log.exception(
                "day_grid_cells(%s) failed for position chip", day_number)
            self._day_index, self._day_total = 1, 1
            return
        idx = next(
            (i for i, c in enumerate(cells) if c.item_id == item_id),
            None,
        )
        self._day_index = (idx or 0) + 1
        self._day_total = len(cells) if cells else 1

    # ── Bucket load ────────────────────────────────────────────────────

    def _load_bucket(
        self, bucket: CullBucket, *, entry_idx: int = 0,
    ) -> None:
        """Hand a bucket to the viewport, prime caches."""
        if self._eg is None:
            return
        self._bucket = bucket
        self._items = list(bucket.items)

        # PhotoCache event context — the proxy / thumb tiers light up
        # for these items as soon as they get a request.
        try:
            from mira.ui.media.photo_cache import photo_cache
            sha256_by_path: dict = {}
            for ci in self._items:
                if not getattr(ci, "path", None):
                    continue
                it = self._eg.item(ci.item_id)
                if it is None or not getattr(it, "sha256", None):
                    continue
                sha256_by_path[Path(ci.path)] = it.sha256
            photo_cache().set_event_context(
                Path(self._eg.event_root), sha256_by_path)
        except Exception:                                          # noqa: BLE001
            log.exception("PhotoCache context registration failed")

        n = len(self._items)
        self._index = max(0, min(entry_idx, n - 1)) if n else 0
        self._cached_path = None

        # Hand the items to the viewport — browse pixels land instantly;
        # ``current_changed`` runs the chrome + settle-gated prep.
        self._sync_viewport_items(self._index)
        self._refresh_position_label()
        self._viewport.setFocus()

    def _sync_viewport_items(self, index: int) -> None:
        vitems = [
            ViewportItem(
                path=Path(ci.path) if getattr(ci, "path", None) else None,
                kind=getattr(ci, "kind", "photo"),
                payload=ci,
            )
            for ci in self._items
        ]
        if vitems:
            index = max(0, min(index, len(vitems) - 1))
        self._viewport.set_items(vitems, index)

    # ── Chrome refresh on viewport landing ─────────────────────────────

    def _on_current_changed(self, index: int) -> None:
        """A navigation landed. The browse pixels are already on screen
        (the viewport's job). Tools grey for the development gap (the
        Q1 ruling — undeveloped flash accepted); set_state re-syncs the
        crop overlay; the working copy preps on settle (Q3)."""
        if not self._items:
            return
        self._index = max(0, min(index, len(self._items) - 1))
        ci = self._current_item()
        self._refresh_position_label()
        developed = (
            ci is not None
            and ci.path == self._cached_path
            and self._surface._full_array is not None
        )
        if not developed:
            self._surface.set_tools_enabled(False)
            # The overlay still carries the PREVIOUS photo's rect —
            # hidden until set_state re-syncs it for the landed photo.
            if self._surface._crop_overlay is not None:
                self._surface._crop_overlay.setVisible(False)
        self._settle.start()
        # Persist the bucket's resume cursor (cluster sub-grids restore
        # here; the single-item bucket is a no-op).
        if self._eg is not None and self._bucket is not None:
            try:
                self._eg.set_bucket_current_index(
                    self._bucket.bucket_key, self._phase, self._index)
            except Exception:                                      # noqa: BLE001
                log.exception(
                    "set_bucket_current_index failed for %s",
                    self._bucket.bucket_key)

    def _current_item(self) -> Optional[CullItem]:
        if not self._items:
            return None
        return self._items[self._index]

    def _refresh_position_label(self) -> None:
        if not self._items:
            self._counter.setText("0 / 0")
            return
        if len(self._items) > 1:
            in_n = self._index + 1
            in_total = len(self._items)
            txt = (
                f"{self._day_index} / {self._day_total}"
                f"  ·  {in_n} / {in_total}"
            )
        else:
            txt = f"{self._day_index} / {self._day_total}"
        self._counter.setText(txt)

    def _on_edge(self, _delta: int) -> None:
        """Stop at bucket edges — the Days Grid bridge owns cross-cell
        nav (the user backs out to the grid and clicks the next cell).
        Mirrors :meth:`PickerPage._on_edge`."""

    # ── Prep (off-thread working-copy) ─────────────────────────────────

    def _on_settle(self) -> None:
        """The settle beat (Q3): only the photo the user LANDED on
        preps a working copy — fly-bys never even request one."""
        ci = self._current_item()
        if ci is None:
            return
        path = ci.path
        if (ci.kind or "photo") == "video" or not is_supported(path):
            # Nothing to develop — the viewport shows the file natively
            # (poster / decode-failure honesty); tools stay greyed.
            self._surface.clear()
            self._cached_path = path
            return
        if (path == self._cached_path
                and self._surface._full_array is not None):
            # Same photo, working copy still loaded — re-push the
            # developed view (navigation cleared the display override).
            self._surface.render_now()
            self._surface._sync_crop_overlay_geometry()
            self._surface.set_tools_enabled(True)
            return
        self._prep.request(path, self._resolve_router_style(ci))

    def _resolve_router_style(self, ci: CullItem) -> str:
        """The style the A-router needs at prep time: the saved
        ``Adjustment.style`` beats the item's classification; "general"
        is the floor."""
        default_style = "general"
        if self._eg is not None:
            try:
                it = self._eg.item(ci.item_id)
                if it is not None:
                    default_style = normalize_style(it.classification)
            except Exception:                                      # noqa: BLE001
                log.exception("item lookup failed for %s", ci.item_id)
            try:
                adj = self._eg.adjustment(ci.item_id)
                if adj is not None and adj.style:
                    return adj.style
            except Exception:                                      # noqa: BLE001
                log.exception("adjustment lookup failed for %s", ci.item_id)
        return default_style or "general"

    def _on_prep_ready(self, result: PrepResult) -> None:
        """The working copy landed (off-thread decode + preview +
        Natural). Adopt it, push the item's saved state, flip the
        developed view in place, wake the tools."""
        ci = self._current_item()
        if ci is None or Path(result.path) != Path(ci.path):
            return                                                  # stale
        adj = self._eg.adjustment(ci.item_id) if self._eg else None
        item_classification = None
        cls_source: Optional[str] = None
        cls_confidence: Optional[float] = None
        if self._eg is not None:
            try:
                it = self._eg.item(ci.item_id)
                if it is not None:
                    item_classification = it.classification
                    cls_source = it.classification_source
                    cls_confidence = it.classification_confidence
            except Exception:                                      # noqa: BLE001
                log.exception("item lookup failed for %s", ci.item_id)
        default_style = normalize_style(item_classification)
        style, look, creative_filter, crop, angle, aspect = \
            self._unpack_adjustment(adj, default_style=default_style)
        rotation = int(getattr(adj, "rotation", 0) or 0) if adj else 0
        look_strength = (
            float(getattr(adj, "look_strength", 1.0))
            if adj is not None else 1.0
        )
        if style != result.style:
            # The router style moved between request and delivery
            # (defensive: cannot happen while tools are greyed). Re-prep.
            self._prep.request(ci.path, style)
            return

        self._surface.load_prepared(
            result.full_array, result.preview_array,
            result.natural_params, style=style)
        self._cached_path = ci.path
        # spec/59 §3 — the standard-correction baseline applies on
        # entry: the AdjustmentSurface defaults to the Natural look
        # (the A-routed correction); set_state pushes the saved choice
        # on top so an unedited photo lands ON its Natural baseline.
        self._surface.set_state(
            look=look, crop_norm=crop, box_angle=angle or 0.0,
            style=style, aspect_label=aspect, rotation=rotation,
            creative_filter=creative_filter,
            look_strength=look_strength,
        )
        # spec/58 §2 — the STYLE combo's classification badge follows
        # the ITEM's stored classification (not Adjustment.style).
        self._surface.set_classification_badge(cls_source, cls_confidence)
        self._surface.set_tools_enabled(True)

    def _on_prep_failed(self, path) -> None:
        ci = self._current_item()
        if ci is None or Path(path) != Path(ci.path):
            return
        self._surface.clear()
        self._cached_path = ci.path

    def _unpack_adjustment(
        self, adj: Optional[m.Adjustment], *,
        default_style: str = "general",
    ) -> tuple[str, str, Optional[str], Optional[tuple], float, str]:
        """Decompose an Adjustment row into the surface's load shape.

        Returns ``(style, look, creative_filter, crop_norm, crop_angle,
        aspect_label)``. No row means the spec/54 defaults: Natural, no
        filter, no crop."""
        style = default_style or "general"
        look = "natural"
        creative_filter: Optional[str] = None
        crop: Optional[tuple[float, float, float, float]] = None
        angle = 0.0
        aspect = "Original"
        if adj is None:
            return style, look, creative_filter, crop, angle, aspect
        if adj.style:
            style = adj.style
        look = adj.look or "natural"
        creative_filter = adj.creative_filter
        if all(v is not None for v in (
                adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h)):
            crop = (adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h)
        angle = adj.crop_angle or 0.0
        aspect = adj.aspect_label or "Original"
        return style, look, creative_filter, crop, angle, aspect

    # ── Persistence (dispatched from the surface's changed(kind)) ──────

    def _on_style_decided(self, style: str) -> None:
        """spec/58 §2 — picking a style (even the one already shown) IS
        the human decision: the item's classification flips to
        ``source='user'``."""
        ci = self._current_item()
        if ci is None or self._eg is None:
            return
        try:
            self._eg.set_classification(ci.item_id, style, "user")
        except Exception:                                          # noqa: BLE001
            log.exception(
                "style decision write failed for %s", ci.item_id)

    def _on_surface_changed(self, kind: str) -> None:
        """The surface edited something — every kind persists immediately
        (spec/54: all edits are discrete clicks now)."""
        if (self._cached_path is None
                or self._eg is None or not self._items):
            return
        if kind in ("look", "style", "filter", "tone"):
            self._persist_choice()
            return

        ci = self._items[self._index]
        adj = self._eg.adjustment(ci.item_id) or m.Adjustment(
            item_id=ci.item_id)

        if kind == "crop":
            rect = self._surface._crop_norm
            if rect is not None:
                adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h = rect
            else:
                adj.crop_x = adj.crop_y = adj.crop_w = adj.crop_h = None
            self._eg.save_adjustment(adj)
            return

        if kind == "angle":
            adj.crop_angle = self._surface._box_angle or 0.0
            self._eg.save_adjustment(adj)
            return

        if kind == "rotation":
            # 90° image rotation. The surface cleared the crop + box
            # angle when the displayed frame's dimensions flipped;
            # persist them all in one shot so the on-disk row matches
            # what the surface shows.
            adj.rotation = int(self._surface._rotation or 0)
            adj.crop_x = adj.crop_y = adj.crop_w = adj.crop_h = None
            adj.crop_angle = 0.0
            self._eg.save_adjustment(adj)
            return

        if kind == "aspect":
            adj.aspect_label = self._surface._aspect_label
            rect = self._surface._crop_norm
            if rect is not None:
                adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h = rect
            else:
                adj.crop_x = adj.crop_y = adj.crop_w = adj.crop_h = None
            self._eg.save_adjustment(adj)
            return

        if kind == "reset":
            # Reset clears choice + crop/angle/aspect/rotation on THIS
            # item — back to Natural, no filter, no rotation.
            adj.crop_x = adj.crop_y = adj.crop_w = adj.crop_h = None
            adj.crop_angle = 0.0
            adj.rotation = 0
            adj.look = "natural"
            adj.creative_filter = None
            adj.style = None
            adj.aspect_label = None
            self._eg.save_adjustment(adj)
            return

    def _persist_choice(self) -> None:
        """Write the current CHOICE (style / look / creative_filter /
        look_strength) to the item's Adjustment row."""
        if (self._cached_path is None
                or self._eg is None or not self._items):
            return
        ci = self._items[self._index]
        try:
            adj = self._eg.adjustment(ci.item_id) or m.Adjustment(
                item_id=ci.item_id)
            state = self._surface.get_state()
            adj.style = state.style
            adj.look = state.look
            adj.creative_filter = state.creative_filter
            # Clamp at the gateway seam (the v4→v5 migration omits the
            # CHECK on existing rows).
            adj.look_strength = max(0.0, min(2.0, float(
                getattr(state, "look_strength", 1.0))))
            self._eg.save_adjustment(adj)
        except Exception:                                          # noqa: BLE001
            log.exception("persist failed for %s", ci.item_id)

    # ── F10 — the developed Preview lens ───────────────────────────────

    @contextmanager
    def _busy(self):
        if self._busy_flag:
            yield
            return
        self._busy_flag = True
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        QApplication.processEvents()
        try:
            yield
        finally:
            QApplication.restoreOverrideCursor()
            self._busy_flag = False

    def _open_processed_lens(self) -> None:
        """F10 / the Full Resolution button: the STANDARD modal lens
        showing the PROCESSED, CROPPED image at FULL resolution —
        exactly what Export produces, seen before exporting. The
        in-canvas Toggle-Crop preview (button-driven, full-res
        computed, canvas-fit) keeps working — this ADDS to it."""
        if self._busy_flag or not self._items:
            return
        ci = self._current_item()
        if ci is None or not is_supported(ci.path):
            return
        if self._surface._full_array is None:
            # The working copy is still preparing (the Q1 gap) — the
            # truth render has nothing honest to show yet.
            return
        with self._busy():
            pm = self._surface.render_full_pixmap()
        if pm is None or pm.isNull():
            return
        self._lens = open_inspect_lens(
            pm, parent=self, path=ci.path, with_tools=False)

    # ── Fullscreen ─────────────────────────────────────────────────────

    def _toggle_fullscreen(self) -> None:
        """Photo-fullscreen, not app-fullscreen (Nelson 2026-06-14).
        Window goes fullscreen AND the editor chrome (toolbar + tools
        area + bottom nav row) hides + content margins drop to 0, so
        the viewport (the photo) fills the entire screen edge to edge.
        Exit restores everything. The ``fullscreen_changed`` signal
        still drives MainWindow's menubar hide."""
        win = self.window()
        if win is None:
            return
        self._fullscreen = not self._fullscreen
        self._fullscreen_btn.setChecked(self._fullscreen)
        chrome_visible = not self._fullscreen
        for w in (self._toolbar_widget, self._tools, self._bottom_widget):
            w.setVisible(chrome_visible)
        if self._fullscreen:
            self._outer.setContentsMargins(0, 0, 0, 0)
            win.showFullScreen()
        else:
            self._outer.setContentsMargins(20, 16, 20, 16)
            win.showNormal()
        self.fullscreen_changed.emit(self._fullscreen)
        self._viewport.setFocus()

    def _exit_fullscreen(self) -> bool:
        if self._fullscreen:
            self._toggle_fullscreen()
            return True
        return False

    # ── Back / Esc ─────────────────────────────────────────────────────

    def _on_esc(self) -> None:
        """Esc — one level at a time: fullscreen → windowed → out."""
        if not self._exit_fullscreen():
            self._on_back()

    def _on_back(self) -> None:
        self._settle.stop()
        # Save the bucket cursor (cluster sub-grids restore on re-entry).
        if (self._eg is not None and self._bucket is not None
                and self._items):
            try:
                self._eg.set_bucket_current_index(
                    self._bucket.bucket_key, self._phase, self._index)
            except Exception:                                      # noqa: BLE001
                log.exception("failed to save bucket cursor")
        self._close_event()
        self.closed.emit()

    # ── Keyboard ───────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent) -> None:              # noqa: N802
        """The viewport owns the locked grammar (we proxy focus there);
        these are stray-focus fallbacks routing to the SAME handlers,
        plus the Edit-specific keys (L / G / [ / ] / R / \\) that don't
        belong on the universal map."""
        from PyQt6.QtWidgets import QApplication as _QA, QLineEdit
        key = event.key()
        fw = _QA.focusWidget()
        if isinstance(fw, QLineEdit):
            if key == Qt.Key.Key_Escape:
                fw.clearFocus()
                event.accept()
                return
            super().keyPressEvent(event)
            return
        if key == Qt.Key.Key_Escape:
            self._on_esc()
            event.accept()
            return
        if key in (Qt.Key.Key_F, Qt.Key.Key_F11):
            self._toggle_fullscreen()
            event.accept()
            return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            if self._items and self._index > 0:
                self._viewport.show_index(self._index - 1)
            event.accept()
            return
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            if self._items and self._index < len(self._items) - 1:
                self._viewport.show_index(self._index + 1)
            event.accept()
            return
        if key == Qt.Key.Key_Home:
            if self._items:
                self._viewport.show_index(0)
            event.accept()
            return
        if key == Qt.Key.Key_End:
            if self._items:
                self._viewport.show_index(len(self._items) - 1)
            event.accept()
            return
        # Edit-specific (spec/63 §4 footnote — surface extras live in
        # the surface hint line and may never collide with the
        # universal table).
        if key == Qt.Key.Key_L:
            delta = -1 if event.modifiers() & \
                Qt.KeyboardModifier.ShiftModifier else 1
            self._surface.cycle_look(delta)
            event.accept()
            return
        if key == Qt.Key.Key_G:
            self._surface.open_look_grid()
            event.accept()
            return
        if key == Qt.Key.Key_BracketLeft:
            self._surface._box_rotate(-90)
            event.accept()
            return
        if key == Qt.Key.Key_BracketRight:
            self._surface._box_rotate(90)
            event.accept()
            return
        if key == Qt.Key.Key_F10:
            self._open_processed_lens()
            event.accept()
            return
        if key == Qt.Key.Key_Backslash:
            self._surface._compare_toggle.toggle()
            event.accept()
            return
        if key == Qt.Key.Key_R:
            self._surface._on_reset_all()
            event.accept()
            return
        # spec/66 §1.1 — Edit is creative-only: P/X/Space/C inert here.
        super().keyPressEvent(event)


__all__ = ["EditorPage"]
