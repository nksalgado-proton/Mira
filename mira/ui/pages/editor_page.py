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

The legacy ``mira/ui/edited/edit_page.py`` + ``edit_host_page.py`` +
``edit_video_page.py`` + ``mira/ui/pages/video_editor_page.py`` all
retire with this surface — every Edit-phase item (photo OR video)
flows through the one route now.
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
    STATE_PICKED,
    STATE_SKIPPED,
    default_state_for,
    project_status,
)
from mira.store import models as m
from mira.ui.design import (
    SurfaceIdentityHeader,
    ghost_button,
    nav_arrow,
)
from mira.ui.edited.adjustment_surface import (
    AdjustmentSurface,
    normalize_style,
)
from mira.ui.edited.edit_prep import PrepResult, edit_prep
from mira.ui.edited.video_workshop_bar import (
    VideoWorkshopBar,
    WORKSHOP_REVEAL_HEIGHT,
)
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
        # spec/59 §8 / spec/66 §1.2 — shipped-item set + watermark gate.
        # Loaded on _open_event; consumed per nav in _on_current_changed
        # to drive ``PhotoViewport.set_exported_watermark``.
        self._exported_set: set = set()
        self._watermark_enabled: bool = True

        # ── Video workshop state (spec/56 §1 + spec/59 §4-§5) ─────────
        # The current source video's workshop model: markers, segment
        # rows + their backing items, snapshots, derived bounds. Loaded
        # in :meth:`_load_video_workshop` whenever the viewport lands on
        # a video; cleared when leaving for a photo.
        self._video_id: Optional[str] = None
        self._video_source_path: Optional[Path] = None
        self._video_duration_ms: int = 0
        self._video_pos_ms: int = 0
        self._video_fps: float = 30.0
        self._video_meta_cache: dict = {}
        self._markers: list = []                       # list[VideoMarker]
        self._segments: list = []                      # list[VideoSegment]
        self._segment_items: list = []                 # list[Item], same order
        self._segment_bounds: list = []                # list[(in_ms, out_ms)]
        self._snapshots: list = []                     # list[VideoSnapshot]
        # Cache for extracted segment-anchor frames (decoded as numpy
        # arrays). Keyed by (video_item_id, seg_index_or_-1, at_ms);
        # the F10 lens reads them.
        self._video_frame_cache: dict = {}
        # ── Modeless development (spec/59 §3) ─────────────────────────
        # When the video is PAUSED and the cursor sits on a stop
        # (marker = segment start, snapshot, or the implicit start),
        # the canvas swaps to a DEVELOPED frame: the QVideoWidget
        # hides and the AdjustmentSurface pushes a fully-developed
        # pixmap into the viewport. Adjustments fan out automatically
        # because the surface already calls render_now() on every
        # state change. Stepping off / playing exits the swap. The
        # state lives here so position / playing / selection handlers
        # can coordinate.
        self._dev_mode_active: bool = False
        self._dev_mode_item_id: Optional[str] = None
        self._dev_mode_anchor_ms: Optional[int] = None
        # The current selection — what the development panel binds to.
        # ``("segment", seg_index, item_id)`` for a segment under the
        # cursor; ``("snapshot", -1, item_id)`` for a held snapshot.
        self._selection: Optional[tuple] = None
        # Guard while pushing state INTO AdjustmentSurface from a
        # selection swap so its ``changed`` signal doesn't echo back as
        # a write (the legacy EditVideoPage tracked the same guard).
        self._suppress_persist: bool = False
        # Default phase state for newly-born segments — read from
        # settings on _open_event (spec/59 §8 — born-green).
        self._edit_default_state: str = "skipped"

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
        # Video transport signals → the workshop bar's timeline + play
        # button. Wired here (before _build_ui) because the workshop bar
        # is constructed in the layout pass.
        self._viewport.video_position_changed.connect(self._on_video_position)
        self._viewport.video_duration_changed.connect(self._on_video_duration)
        self._viewport.video_playing_changed.connect(self._on_video_playing)
        # spec/66 §1.1 — Edit is creative-only on PHOTOS. P/X/Space/C
        # STILL fire on the viewport (the locked map is universal); the
        # page connects to them only to scope decisions onto the
        # SELECTED video segment / snapshot when a video is landed.
        self._viewport.pick_requested.connect(self._on_pick_key)
        self._viewport.skip_requested.connect(self._on_skip_key)
        self._viewport.toggle_requested.connect(self._on_toggle_key)
        self._viewport.cycle_requested.connect(self._on_toggle_key)
        self._viewport.sweep_requested.connect(self._on_sweep_key)

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

        # spec/71 identity header — Edit phase chrome (amber rail + EDIT
        # badge). NO state legend: P/X are inert on a creative-only
        # surface (spec/66 §1.1). The reminder names the two truth-keys
        # the user actually presses here.
        self._identity = SurfaceIdentityHeader(
            phase="edit",
            name=tr("Edit"),
            purpose=tr("Develop your picked keepers"),
            reminder=tr(
                "\\ compare before/after · F10 full-res preview."),
        )
        self._outer.addWidget(self._identity)

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

        # ── Workshop reveal host — fixed reserved height (spec/56 §1).
        # The host stays visible on photos AND videos so the canvas
        # geometry above is invariant under photo↔video sweeps (the
        # no-canvas-jump rule, lifted from PickerPage's compact_row Fix
        # A 2026-06-15). On photos the inner workshop is hidden; on
        # videos it appears in place. The viewport's video machinery
        # (arm-on-landing, position/duration/playing signals) handles
        # the in-place player swap above this row.
        self._workshop_host = QWidget()
        self._workshop_host.setObjectName("EditorWorkshopHost")
        self._workshop_host.setFixedHeight(WORKSHOP_REVEAL_HEIGHT)
        wh_layout = QVBoxLayout(self._workshop_host)
        wh_layout.setContentsMargins(0, 0, 0, 0)
        wh_layout.setSpacing(0)
        self._workshop_bar = VideoWorkshopBar()
        wh_layout.addWidget(self._workshop_bar)
        self._workshop_bar.setVisible(False)
        self._wire_workshop_signals()
        self._outer.addWidget(self._workshop_host)

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
        (Surface 06). Loads the ENTIRE day's navigable items
        (chronological; cluster members flattened in place) and
        positions the viewport at the clicked item — so prev/next walks
        the whole day, not just that one click (Nelson 2026-06-14
        eyeball #3: a 1-item bucket made nav dead).

        Videos STAY in the list (surface 12 fold, 2026-06-15): the
        EditorPage sweeps photos AND videos in one unified surface;
        when the cursor lands on a video the canvas becomes a video in
        place and the spec/56 workshop reveals under it.

        Returns ``True`` on success. ``False`` leaves the page in a clean
        state so the host can leave the user on the Days Grid.
        """
        if not self._open_event(event_id):
            return False
        try:
            items = self._day_navigable_items(day_number)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "open_to_item: day-items build failed for %s/%s",
                event_id, day_number)
            items = []
        idx = next(
            (i for i, it in enumerate(items) if it.item_id == item_id),
            None,
        )
        if idx is None:
            # The clicked item didn't surface in the navigable list —
            # OR the day's buckets aren't materialised in this gateway
            # snapshot. Fall back to a single-item bucket so the
            # surface still opens rather than collapsing back to the
            # grid silently.
            cull_item = self._cull_item_for(item_id)
            if cull_item is None:
                self._close_event()
                return False
            items = [cull_item]
            idx = 0
        # spec/32 §2.10 — opening an item marks it visited at Edit.
        try:
            self._eg.set_item_visited(item_id, self._phase)
        except Exception:                                          # noqa: BLE001
            log.exception("set_item_visited failed for %s", item_id)
        bucket = self._day_bucket(day_number, items)
        self._day_number = day_number
        self._day_index = idx + 1
        self._day_total = len(items)
        self._load_bucket(bucket, entry_idx=idx)
        return True

    def _day_navigable_items(self, day_number: int) -> list[CullItem]:
        """Build the day's navigable item list in chronological order.

        Walks :func:`day_grid_cells` and FLATTENS clusters into their
        members so prev/next steps through every individual frame the
        user can edit, not just the cluster covers. Both photos AND
        videos surface (the Surface 12 fold, 2026-06-15) — the viewport
        sweeps both kinds and EditorPage reveals the spec/56 workshop
        in place when a video lands.
        """
        if self._eg is None:
            return []
        from mira.picked import day_grid_cells
        try:
            default_state = default_state_for(
                self.gateway.settings, self._phase)
        except Exception:                                          # noqa: BLE001
            default_state = "skipped"
        cells = day_grid_cells(
            self._eg, day_number, phase=self._phase,
            default_state=default_state,
        )
        ordered_ids: list[str] = []
        for c in cells:
            if c.is_cluster and c.cluster is not None:
                ordered_ids.extend(m.item_id for m in c.cluster.members)
            elif c.item_id is not None:
                ordered_ids.append(c.item_id)
        out: list[CullItem] = []
        for iid in ordered_ids:
            it = self._eg.item(iid)
            if it is None or not it.origin_relpath:
                continue
            out.append(CullItem(
                item_id=it.id,
                path=Path(self._eg.event_root) / it.origin_relpath,
                kind=it.kind,
                capture_time_corrected=it.capture_time_corrected or None,
                duration_ms=it.duration_ms,
            ))
        return out

    def _day_bucket(
        self, day_number: int, items: list[CullItem],
    ) -> CullBucket:
        """Wrap a whole-day item list in a CullBucket the load path
        understands. Status uses ``project_status`` so the bucket reads
        sensibly if anything ever inspects it (the surface itself only
        cares about ``items``)."""
        if self._eg is not None:
            phase_states = self._eg.phase_states(self._phase)
        else:
            phase_states = {}
        status = project_status(
            [ci.item_id for ci in items], phase_states, None)
        return CullBucket(
            bucket_key=f"day:{day_number}",
            kind="day",
            title="",
            items=tuple(items),
            status=status,
        )

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
        # Re-connect to the prep singleton — ``_close_event`` cleared the
        # fan-out so a stale delivery couldn't reach us; opening a new
        # event re-arms the wire so this session's preps land.
        self._prep.prepared.connect(self._on_prep_ready)
        self._prep.prep_failed.connect(self._on_prep_failed)
        try:
            self._eg = self.gateway.open_event(event_id)
        except Exception:                                          # noqa: BLE001
            log.exception("EditorPage: cannot open event %s", event_id)
            return False
        self._event_id = event_id
        # spec/59 §8 / spec/66 §1.2 — shipped set + watermark gate for
        # the single-photo viewport's diagonal "Exported" overlay.
        self._exported_set, self._watermark_enabled = (
            self._load_exported_state())
        # spec/59 §8 — the configured edit default ("born green" out of
        # the box) governs segment lazy-birth too.
        try:
            self._edit_default_state = default_state_for(
                self.gateway.settings, self._phase)
        except Exception:                                          # noqa: BLE001
            log.exception("EditorPage: default_state_for failed")
            self._edit_default_state = "skipped"
        return True

    def _load_exported_state(self) -> "tuple[set, bool]":
        """Read the shipped-item set + watermark gate for the open
        event. Falls back to (empty set, True) on either error so the
        viewport never mistakenly stamps an unshipped item."""
        if self._eg is None or self.gateway is None:
            return set(), True
        try:
            settings = self.gateway.settings.load()
            enabled = bool(getattr(
                settings, "show_exported_watermark", True))
        except Exception:                                          # noqa: BLE001
            log.exception(
                "EditorPage: settings.load failed; assuming watermark on")
            enabled = True
        try:
            shipped = self._eg.exported_item_ids()
        except Exception:                                          # noqa: BLE001
            log.exception("EditorPage: exported_item_ids failed")
            shipped = set()
        return shipped, enabled

    def _close_event(self) -> None:
        self._settle.stop()
        # Tear the video workshop down BEFORE the gateway closes — its
        # row arrays reference rows we read through ``self._eg``; we
        # don't want stale references to outlive the gateway.
        self._teardown_video_workshop()
        # Disconnect from the prep singleton's signal fan-out — without
        # this an in-flight prep delivery from this event can land
        # AFTER the gateway closed and reach ``_on_prep_ready`` against
        # a half-torn page (the cross-test interference Nelson saw on
        # 2026-06-15 verify). Mirrors :meth:`shutdown` but runs at every
        # event boundary, not only at destruction. Idempotent.
        for sig, slot in (
                (self._prep.prepared, self._on_prep_ready),
                (self._prep.prep_failed, self._on_prep_failed)):
            try:
                sig.disconnect(slot)
            except Exception:                                      # noqa: BLE001
                pass
        # CRITICAL ORDER (Nelson 2026-06-15 race): null out ``_eg``
        # BEFORE calling ``.close()`` on the held reference. A queued
        # prep delivery firing between the close() and the assignment
        # would see ``self._eg`` still pointing at a CLOSED gateway and
        # crash with "Cannot operate on a closed database". With the
        # null in place first, the delivery's ``if self._eg`` guard
        # reads False and returns harmlessly.
        eg = self._eg
        self._eg = None
        self._event_id = None
        self._bucket = None
        self._items = []
        self._index = 0
        if eg is not None:
            try:
                eg.close()
            except Exception:                                      # noqa: BLE001
                log.exception("EventGateway close failed")
        self._cached_path = None
        self._exported_set = set()
        self._watermark_enabled = True

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
                # Skip videos: the photo proxy/thumb caches can't
                # decode MP4 etc., and queuing them spams the log with
                # "cannot identify image file" warnings (Nelson
                # 2026-06-15 log report). Matches the Picker's filter
                # at picker_page.py:635.
                if getattr(ci, "kind", "photo") != "photo":
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
        crop overlay; the working copy preps on settle (Q3).

        For a VIDEO landing the spec/56 workshop reveals under the
        canvas in place (no page jump); the development panel stays on
        top and its writes route to the selected segment's
        ``VideoAdjustment`` (or a snapshot's photo ``Adjustment``)."""
        if not self._items:
            return
        self._index = max(0, min(index, len(self._items) - 1))
        # In a whole-day bucket the day-position IS the within-bucket
        # position; keep day_index in sync so the chip reads honestly
        # as the user navigates. Cluster buckets keep day_index fixed
        # at the entry point.
        if self._bucket is not None and self._bucket.kind == "day":
            self._day_index = self._index + 1
        ci = self._current_item()
        self._refresh_position_label()
        is_video = ci is not None and (getattr(ci, "kind", "photo") == "video")

        if is_video:
            # The spec/56 workshop branch. Tear the photo-prep down so
            # a left-over working copy isn't pushed through the panel;
            # the development panel rebinds in _load_video_workshop.
            self._settle.stop()
            self._cached_path = None
            self._viewport.set_exported_watermark(False)
            self._load_video_workshop(ci)
        else:
            # The standard photo branch — develop the working copy on
            # settle (Q3), keep the workshop reserved-but-hidden so the
            # canvas geometry is invariant.
            self._teardown_video_workshop()
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
            # spec/59 §8 / spec/66 §1.2 — the diagonal "Exported" overlay.
            shipped = (
                ci is not None
                and self._watermark_enabled
                and ci.item_id in self._exported_set
            )
            self._viewport.set_exported_watermark(shipped)
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
        """Position chip — single-line "N / Total" reflecting the
        viewport's cursor within the loaded bucket. For a whole-day
        bucket (the open_to_item path) ``day_*`` and bucket counts
        agree, so we show one figure. For a cluster bucket (the
        open_to_cluster path) the day-position diverges from the
        within-cluster position; both surface as "DAY/DAYTOTAL · N/T"
        so the user can tell where they are in the day vs the cluster."""
        if not self._items:
            self._counter.setText("0 / 0")
            return
        in_n = self._index + 1
        in_total = len(self._items)
        same = (in_n == self._day_index and in_total == self._day_total)
        if not same and in_total > 1:
            txt = (
                f"{self._day_index} / {self._day_total}"
                f"  ·  {in_n} / {in_total}"
            )
        else:
            txt = f"{in_n} / {in_total}"
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
        # Defensive — a queued delivery firing between ``_close_event``
        # disconnecting and the next ``_open_event`` re-arming can land
        # while ``_eg`` references a closed gateway. Any read here would
        # throw ``sqlite3.ProgrammingError: Cannot operate on a closed
        # database`` (Nelson 2026-06-15 verify race). Treat as stale.
        try:
            adj = self._eg.adjustment(ci.item_id) if self._eg else None
        except Exception:                                          # noqa: BLE001
            return
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
                # Same closed-DB race as above; treat as stale rather
                # than logging (it floods the test log otherwise).
                return
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
        ``source='user'``. On a VIDEO with a segment selected the style
        decision targets the SEGMENT's parent video classification (the
        snapshot inherits the parent's classification at birth)."""
        if self._eg is None:
            return
        ci = self._current_item()
        target_id = None
        if self._video_id is not None and self._selection is not None:
            kind_sel = self._selection[0]
            if kind_sel == "snapshot":
                target_id = self._selection[2]
            elif kind_sel == "segment":
                # Style on a segment doesn't flip a per-segment
                # classification (segments inherit from the parent video
                # which inherits from capture metadata); skip the write
                # rather than misroute it. The segment's own
                # VideoAdjustment.style is persisted by the surface-changed
                # router.
                return
        else:
            target_id = ci.item_id if ci else None
        if target_id is None:
            return
        try:
            self._eg.set_classification(target_id, style, "user")
        except Exception:                                          # noqa: BLE001
            log.exception(
                "style decision write failed for %s", target_id)

    def _on_surface_changed(self, kind: str) -> None:
        """The surface edited something — every kind persists immediately
        (spec/54: all edits are discrete clicks now). When a video
        segment is selected the writes route to ``save_video_adjustment``;
        a snapshot routes to the standard ``save_adjustment`` (it IS a
        photo Adjustment row); a photo on the photo branch uses the
        original path."""
        if self._suppress_persist or self._eg is None or not self._items:
            return
        # Video segment branch ───────────────────────────────────────
        if self._video_id is not None and self._selection is not None \
                and self._selection[0] == "segment":
            self._persist_video_adjustment(kind)
            return
        # Snapshot branch (uses the photo Adjustment row) ────────────
        if self._video_id is not None and self._selection is not None \
                and self._selection[0] == "snapshot":
            self._persist_snapshot_adjustment(kind)
            return
        # Photo branch — the original behaviour. ─────────────────────
        if self._cached_path is None:
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

    # ── Video-branch persistence (selection-scoped) ────────────────────

    def _persist_video_adjustment(self, kind: str) -> None:
        """The surface changed and a SEGMENT is selected: write to
        ``video_adjustment(seg_item_id)``. Mirrors the photo branch's
        shape (Look / Style / Filter / Crop / Aspect / Rotation /
        Angle / Reset). Per-segment audio / speed / stabilise live in
        the workshop bar — those write their own paths."""
        if self._selection is None or self._eg is None:
            return
        _kind_sel, _idx, item_id = self._selection
        vadj = self._eg.video_adjustment(item_id) or m.VideoAdjustment(
            item_id=item_id)
        state = self._surface.get_state()
        if kind in ("look", "style", "filter", "tone"):
            vadj.style = state.style
            vadj.look = state.look
            vadj.creative_filter = state.creative_filter
        elif kind == "crop":
            rect = self._surface._crop_norm
            if rect is not None:
                vadj.crop_x, vadj.crop_y, vadj.crop_w, vadj.crop_h = rect
            else:
                vadj.crop_x = vadj.crop_y = vadj.crop_w = vadj.crop_h = None
        elif kind == "angle":
            vadj.box_angle = self._surface._box_angle or 0.0
        elif kind == "rotation":
            vadj.rotation_degrees = int(self._surface._rotation or 0)
            vadj.crop_x = vadj.crop_y = vadj.crop_w = vadj.crop_h = None
            vadj.box_angle = 0.0
        elif kind == "aspect":
            vadj.aspect_ratio_label = self._surface._aspect_label
            rect = self._surface._crop_norm
            if rect is not None:
                vadj.crop_x, vadj.crop_y, vadj.crop_w, vadj.crop_h = rect
            else:
                vadj.crop_x = vadj.crop_y = vadj.crop_w = vadj.crop_h = None
        elif kind == "reset":
            vadj.crop_x = vadj.crop_y = vadj.crop_w = vadj.crop_h = None
            vadj.box_angle = 0.0
            vadj.rotation_degrees = 0
            vadj.look = "natural"
            vadj.creative_filter = None
            vadj.style = None
            vadj.aspect_ratio_label = None
        else:
            return
        try:
            self._eg.save_video_adjustment(vadj)
        except Exception:                                          # noqa: BLE001
            log.exception("save_video_adjustment failed for %s", item_id)

    def _persist_snapshot_adjustment(self, kind: str) -> None:
        """A SNAPSHOT is selected: it's a photo item shape (the
        gateway creates it with ``kind='photo'`` + ``provenance='snapshot'``
        per spec/56 §1). Reuse the photo-branch routes via the standard
        ``save_adjustment`` mutator."""
        if self._selection is None or self._eg is None:
            return
        _kind_sel, _idx, item_id = self._selection
        adj = self._eg.adjustment(item_id) or m.Adjustment(
            item_id=item_id)
        if kind in ("look", "style", "filter", "tone"):
            state = self._surface.get_state()
            adj.style = state.style
            adj.look = state.look
            adj.creative_filter = state.creative_filter
            adj.look_strength = max(0.0, min(2.0, float(
                getattr(state, "look_strength", 1.0))))
        elif kind == "crop":
            rect = self._surface._crop_norm
            if rect is not None:
                adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h = rect
            else:
                adj.crop_x = adj.crop_y = adj.crop_w = adj.crop_h = None
        elif kind == "angle":
            adj.crop_angle = self._surface._box_angle or 0.0
        elif kind == "rotation":
            adj.rotation = int(self._surface._rotation or 0)
            adj.crop_x = adj.crop_y = adj.crop_w = adj.crop_h = None
            adj.crop_angle = 0.0
        elif kind == "aspect":
            adj.aspect_label = self._surface._aspect_label
            rect = self._surface._crop_norm
            if rect is not None:
                adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h = rect
            else:
                adj.crop_x = adj.crop_y = adj.crop_w = adj.crop_h = None
        elif kind == "reset":
            adj.crop_x = adj.crop_y = adj.crop_w = adj.crop_h = None
            adj.crop_angle = 0.0
            adj.rotation = 0
            adj.look = "natural"
            adj.creative_filter = None
            adj.style = None
            adj.aspect_label = None
        else:
            return
        try:
            self._eg.save_adjustment(adj)
        except Exception:                                          # noqa: BLE001
            log.exception("snapshot adjustment save failed for %s", item_id)

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
        computed, canvas-fit) keeps working — this ADDS to it.

        On a VIDEO segment (spec/56 §1 fold, 2026-06-15) the lens
        extracts the segment's anchor frame, develops it through the
        segment's ``VideoAdjustment`` (Look / Style / Filter / Crop)
        and shows that — the user's first-class way to verify
        adjustments work before Export. Live in-canvas preview swap
        on pause is a follow-up slice.
        """
        if self._busy_flag or not self._items:
            return
        ci = self._current_item()
        if ci is None:
            return

        # Video segment branch — extract + develop the anchor frame.
        if self._video_id is not None:
            self._open_video_processed_lens(ci)
            return

        if not is_supported(ci.path):
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

    def _open_video_processed_lens(self, ci: CullItem) -> None:
        """Video F10 — extract the SELECTED segment's anchor frame,
        develop it through that segment's adjustments, show in the
        standard inspect lens. Develops via the core pipeline
        DIRECTLY (not through AdjustmentSurface) so the page's
        canvas / video widget Z-order stays untouched."""
        if (self._selection is None
                or self._selection[0] not in ("segment", "snapshot")
                or self._video_source_path is None
                or not self._video_source_path.exists()):
            return
        kind, idx, item_id = self._selection
        if kind == "segment":
            if not (0 <= idx < len(self._segment_bounds)):
                return
            in_ms, _out_ms = self._segment_bounds[idx]
            anchor_ms = in_ms
        else:                                                       # snapshot
            snap = next(
                (s for s in self._snapshots if s.item_id == item_id),
                None,
            )
            if snap is None:
                return
            anchor_ms = int(snap.at_ms)

        with self._busy():
            arr = self._extract_video_frame_array(item_id, anchor_ms)
            if arr is None:
                return
            pm = self._develop_array_for_lens(arr, item_id, kind)
        if pm is None or pm.isNull():
            return
        self._lens = open_inspect_lens(
            pm, parent=self, path=self._video_source_path,
            with_tools=False)

    def _develop_array_for_lens(
        self, arr, item_id: str, kind: str,
    ) -> Optional["QPixmap"]:
        """Apply the SELECTED stop's adjustments to a frame array using
        the SAME pipeline a photo uses (core.photo_render +
        core.photo_auto). Pure read against the surface's tone math —
        no AdjustmentSurface mutation, so the canvas / video widget
        Z-order stays untouched."""
        try:
            import numpy as np
            from core.photo_auto import (
                compute_auto_params,
                creative_filter_amount,
                look_params_from_natural,
                resolve_filter_recipe,
            )
            from core.photo_render import (
                FilterRecipe, apply_crop_norm, apply_filter, apply_params,
                apply_rotation, extract_rotated_crop,
            )
            from mira.ui.edited.adjustment_surface import _array_to_pixmap
        except Exception:                                          # noqa: BLE001
            log.exception("lens develop: import failed")
            return None
        # Resolve refinements from the row (segment → VideoAdjustment,
        # snapshot → Adjustment).
        if kind == "segment":
            vadj = self._eg.video_adjustment(item_id) if self._eg else None
            look_key = (vadj.look if vadj else "natural") or "natural"
            style_key = (vadj.style if vadj else None) \
                or self._style_for_selection()
            creative_filter = vadj.creative_filter if vadj else None
            crop = None
            if vadj is not None and all(v is not None for v in (
                    vadj.crop_x, vadj.crop_y, vadj.crop_w, vadj.crop_h)):
                crop = (vadj.crop_x, vadj.crop_y, vadj.crop_w, vadj.crop_h)
            box_angle = float(vadj.box_angle) if vadj else 0.0
            rotation = int(vadj.rotation_degrees) if vadj else 0
            look_strength = 1.0                                     # video looks: uncalibrated
        else:                                                       # snapshot
            adj = self._eg.adjustment(item_id) if self._eg else None
            look_key = (adj.look if adj else "natural") or "natural"
            style_key = (adj.style if adj else None) \
                or self._style_for_selection()
            creative_filter = adj.creative_filter if adj else None
            crop = None
            if adj is not None and all(v is not None for v in (
                    adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h)):
                crop = (adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h)
            box_angle = float(adj.crop_angle) if adj else 0.0
            rotation = int(adj.rotation) if adj else 0
            look_strength = float(getattr(adj, "look_strength", 1.0)) \
                if adj else 1.0

        try:
            out = arr
            if rotation:
                out = apply_rotation(out, rotation)
            # The pipeline: tone (look_params, style-routed) → filter →
            # crop. Mirrors core.photo_render usage in AdjustmentSurface.
            # Natural = the A-routed correction computed on this
            # frame; ``look_params_from_natural`` then layers the mood
            # bias on top (it handles ``"original"`` / ``"natural"`` /
            # any LOOK_BIASES key itself, applies ``strength``, raises
            # on unknown looks).
            natural_params = compute_auto_params(out, style=style_key)
            params = look_params_from_natural(
                natural_params, look_key, strength=look_strength)
            if not params.is_identity:
                out = apply_params(out, params)
            if creative_filter:
                recipe = resolve_filter_recipe(creative_filter, style_key)
                if recipe is not None:
                    out = apply_filter(
                        out, FilterRecipe.from_dict(recipe),
                        creative_filter_amount(creative_filter))
            if crop is not None:
                if box_angle:
                    out = extract_rotated_crop(out, crop, box_angle)
                else:
                    out = np.ascontiguousarray(apply_crop_norm(out, crop))
            return _array_to_pixmap(out)
        except Exception:                                          # noqa: BLE001
            log.exception("lens develop pipeline failed")
            return None

    def _extract_video_frame_array(
        self, anchor_id: str, anchor_ms: int,
    ) -> Optional[object]:
        """Extract one frame at ``anchor_ms`` from the loaded source
        video, return it as a numpy array suitable for
        :meth:`AdjustmentSurface.load_image`. Caches per
        ``(video_id, anchor_id, anchor_ms)`` so repeat F10 presses
        on the same selection don't re-spawn FFmpeg.
        """
        if self._video_source_path is None:
            return None
        key = (self._video_id, anchor_id, int(anchor_ms))
        cached = self._video_frame_cache.get(key)
        if cached is not None:
            return cached
        import tempfile
        from core.photo_decoder import decode_image
        from core.video_extract import extract_frame
        tmp = Path(tempfile.gettempdir()) / (
            f"mira_segframe_{self._video_id}_{anchor_id}_{int(anchor_ms)}.jpg")
        try:
            extract_frame(
                self._video_source_path, int(anchor_ms), tmp,
                timeout=20.0,
            )
        except Exception:                                          # noqa: BLE001
            log.exception(
                "extract_frame failed for %s @ %dms",
                self._video_source_path, anchor_ms)
            return None
        try:
            arr = decode_image(tmp)
        except Exception:                                          # noqa: BLE001
            log.exception("decode of extracted frame failed: %s", tmp)
            return None
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:                                      # noqa: BLE001
                pass
        if arr is None:
            return None
        self._video_frame_cache[key] = arr
        return arr

    def _style_for_selection(self) -> str:
        """The A-router style for the SELECTED item's classification
        (photo Adjustment for snapshot, segment's stored style or the
        parent video's classification for a segment)."""
        if self._selection is None or self._eg is None:
            return "general"
        kind, _idx, item_id = self._selection
        try:
            if kind == "segment":
                vadj = self._eg.video_adjustment(item_id)
                if vadj is not None and vadj.style:
                    return vadj.style
                # Inherit from the source video's classification.
                src = self._eg.item(self._video_id) if self._video_id else None
                return normalize_style(
                    src.classification if src else None) or "general"
            if kind == "snapshot":
                it = self._eg.item(item_id)
                return normalize_style(
                    it.classification if it else None) or "general"
        except Exception:                                          # noqa: BLE001
            log.exception("style resolution failed for %s", item_id)
        return "general"

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
        # spec/66 §1.1 — Edit is creative-only ON PHOTOS: P/X/Space/C
        # are inert on the photo branch (no Pick/Skip ledger). The video
        # branch routes them through _on_*_key earlier (those handlers
        # consult ``self._video_id``).
        super().keyPressEvent(event)

    # ── Video workshop wiring (spec/56 §1 + spec/59 §4-§5) ─────────────

    def _wire_workshop_signals(self) -> None:
        """Connect the VideoWorkshopBar's high-level signals to gateway
        mutators + viewport transport. Called once during _build_ui."""
        wb = self._workshop_bar
        # Timeline
        wb.seek_requested.connect(self._on_workshop_seek)
        wb.segment_clicked.connect(self._on_segment_clicked)
        wb.marker_selected.connect(self._on_marker_selected)
        wb.marker_moved.connect(self._on_marker_moved)
        # Tools row
        wb.add_marker_requested.connect(self._add_marker_at_playhead)
        wb.add_snapshot_requested.connect(self._add_snapshot_at_playhead)
        wb.remove_requested.connect(self._remove_stop_at_playhead)
        wb.toggle_status_requested.connect(self._toggle_status_at_selection)
        wb.reset_all_requested.connect(self._workshop_reset_all)
        wb.clear_markers_requested.connect(self._workshop_clear_markers)
        wb.clear_snapshots_requested.connect(self._workshop_clear_snapshots)
        # Transport
        wb.play_pause_requested.connect(self._viewport.video_toggle_play)
        wb.jump_start_requested.connect(lambda: self._viewport.video_seek(0))
        wb.jump_end_requested.connect(
            lambda: self._viewport.video_seek(max(0, self._video_duration_ms - 1)))
        wb.prev_stop_requested.connect(lambda: self._jump_stop(-1))
        wb.next_stop_requested.connect(lambda: self._jump_stop(+1))
        wb.prev_frame_requested.connect(lambda: self._step_frame(-1))
        wb.next_frame_requested.connect(lambda: self._step_frame(+1))
        wb.jump_to_marker_requested.connect(self._jump_to_marker)
        wb.jump_to_snapshot_requested.connect(self._jump_to_snapshot)
        # Per-segment extras
        wb.mute_toggled.connect(self._on_mute_toggled)
        wb.volume_changed.connect(self._on_volume_changed)
        wb.speed_changed.connect(self._on_speed_changed)

    def _load_video_workshop(self, ci: CullItem) -> None:
        """Land on a video item — ensure segments exist, load markers /
        segments / snapshots from the gateway, reveal the workshop bar,
        seed transport + selection."""
        if self._eg is None or ci is None:
            return
        self._video_id = ci.item_id
        self._video_source_path = Path(ci.path) if ci.path else None
        # Probe duration + fps once per path. The viewport's
        # arm-on-landing kicks in independently to display the player.
        self._seed_video_metadata(ci.path)
        # Make segments exist for the current marker set (lazy birth,
        # default Skip per spec/56 §1 — segments do NOT inherit the
        # ``edit_default_state`` "born green" setting that governs
        # photos. The marker-partition model means each segment is a
        # boundary, not an endorsement: adding a marker splits a
        # parent and BOTH halves inherit the parent's state (so an
        # un-touched video starts at one default-Skip segment, and
        # the user explicitly picks the cuts they want to ship —
        # Nelson 2026-06-15, "the status was lost" bug report).
        try:
            self._eg.ensure_video_segments(
                self._video_id, default_state=STATE_SKIPPED)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "ensure_video_segments failed for %s", self._video_id)
        self._reload_workshop_rows()
        # Reset transport state for the new clip.
        self._video_pos_ms = 0
        self._workshop_bar.set_position(0)
        self._workshop_bar.set_playing(False)
        self._workshop_bar.set_duration(self._video_duration_ms)
        # Show the workshop in place; the canvas above keeps geometry
        # by virtue of the host's fixed height.
        self._workshop_bar.setVisible(True)
        # The cursor lands at position 0 — select the segment that
        # contains it (the segment that starts at the first marker).
        # ``_select_segment_for_position`` calls ``_bind_panel_to_selection``
        # which pushes adjustment state into the surface AND enables
        # the tools so Look/Style/Filter/Crop clicks actually flow
        # through the persistence router (Nelson 2026-06-15 eyeball —
        # "none of the adjustments made no difference").
        self._refresh_workshop_model()
        self._select_segment_for_position(0)
        self._surface.set_tools_enabled(True)
        # Push initial transport state — fps hint drives prev/next-frame.
        self._workshop_bar.set_fps_hint(self._video_fps)
        # Initial landing is PAUSED at 0 (a stop) — engage dev mode so
        # the user sees the developed first-frame BEFORE pressing play
        # (spec/59 §3 modeless development).
        self._maybe_enter_dev_mode()

    def _teardown_video_workshop(self) -> None:
        """Leaving a video for a photo (or unloading): hide the
        workshop, drop loaded rows. The host stays at fixed height so
        the canvas above doesn't shift."""
        if self._video_id is None and not self._workshop_bar.isVisible():
            return
        # Exit dev mode FIRST so the video widget visibility is
        # restored and the rendered pixmap clears before the photo
        # branch's own display semantics take over.
        self._exit_dev_mode()
        self._workshop_bar.setVisible(False)
        self._video_id = None
        self._video_source_path = None
        self._video_duration_ms = 0
        self._video_pos_ms = 0
        self._markers = []
        self._segments = []
        self._segment_items = []
        self._segment_bounds = []
        self._snapshots = []
        self._selection = None

    def _seed_video_metadata(self, path) -> None:
        """One-shot ffprobe per path for duration + fps. Failure leaves
        duration at 0 until QtMultimedia's async durationChanged fills
        it in; fps falls back to 30."""
        key = str(path) if path is not None else None
        if key is None:
            return
        meta = self._video_meta_cache.get(key)
        if meta is None:
            try:
                from core.video_extract import probe_video
                meta = probe_video(path)
            except Exception:                                      # noqa: BLE001
                meta = None
            self._video_meta_cache[key] = meta
        dur = int(getattr(meta, "duration_ms", 0) or 0)
        fps = float(getattr(meta, "fps", 0.0) or 0.0)
        if dur > 0:
            self._video_duration_ms = dur
        if fps > 0:
            self._video_fps = fps

    def _reload_workshop_rows(self) -> None:
        """Re-read markers / segments / snapshots from the gateway —
        call after every mutator that changes the row sets."""
        if self._eg is None or self._video_id is None:
            return
        try:
            self._markers = list(self._eg.video_markers(self._video_id))
            self._segments = list(self._eg.video_segments(self._video_id))
            self._segment_items = list(self._eg.segment_items(self._video_id))
            self._snapshots = list(self._eg.video_snapshots(self._video_id))
            if self._video_duration_ms > 0:
                self._segment_bounds = list(
                    self._eg.segment_bounds(self._video_id))
            else:
                self._segment_bounds = []
        except Exception:                                          # noqa: BLE001
            log.exception("workshop rows reload failed")
            self._markers = []
            self._segments = []
            self._segment_items = []
            self._segment_bounds = []
            self._snapshots = []

    def _segment_state(self, seg_item_id: str) -> str:
        """Return 'picked' or 'skipped' for a segment item — reads
        phase_state(item, 'edit'); falls back to ``"skipped"`` when no
        row exists (spec/56 §1 default Skip — matches the lazy-birth
        contract in :meth:`_reload_workshop_rows`)."""
        if self._eg is None:
            return STATE_SKIPPED
        try:
            ps_map = self._eg.phase_states(self._phase)
        except Exception:                                          # noqa: BLE001
            return STATE_SKIPPED
        ps = ps_map.get(seg_item_id)
        if ps is None:
            return STATE_SKIPPED
        return ps.state or STATE_SKIPPED

    def _snapshot_state(self, snap_item_id: str) -> str:
        if self._eg is None:
            return "picked"
        try:
            ps_map = self._eg.phase_states(self._phase)
        except Exception:                                          # noqa: BLE001
            return "picked"
        ps = ps_map.get(snap_item_id)
        return (ps.state if ps else "picked") or "picked"

    def _refresh_workshop_model(self) -> None:
        """Build the timeline model from the loaded rows + push it to
        the workshop bar. Cheap; called after every mutation and on the
        playhead arrival of a new segment."""
        if self._video_id is None:
            return
        markers = [(mk.id, int(mk.at_ms)) for mk in self._markers]
        bounds = list(self._segment_bounds)
        seg_states: list[str] = []
        for seg, item in zip(self._segments, self._segment_items):
            seg_states.append(self._segment_state(item.id))
        snaps = [(int(s.at_ms), self._snapshot_state(s.item_id))
                 for s in self._snapshots]
        # Selection — the cursor selects a segment when the user hasn't
        # explicitly grabbed a snapshot.
        sel_seg = -1
        sel_marker = ""
        if self._selection is not None:
            kind = self._selection[0]
            if kind == "segment":
                sel_seg = int(self._selection[1])
            elif kind == "marker":
                sel_marker = str(self._selection[2])
        self._workshop_bar.set_timeline_model(
            markers=markers, bounds=bounds, states=seg_states,
            snapshots=snaps, selected_seg=sel_seg,
            selected_marker=sel_marker,
            duration_ms=self._video_duration_ms,
        )
        # Segment info chip
        if sel_seg >= 0 and sel_seg < len(bounds):
            in_ms, out_ms = bounds[sel_seg]
            self._workshop_bar.set_segment_info(
                sel_seg, len(bounds), out_ms - in_ms)
        else:
            self._workshop_bar.set_segment_info(0, len(bounds), 0)
        # Tools enable rules (spec/59 §4): Marker greys ON a stop AND
        # at the permanent endpoints (the gateway rejects markers at
        # 0 / duration); Snapshot greys only ON an existing stop;
        # Remove only fires on a stop and never at the endpoints (the
        # endpoint markers are permanent).
        on_stop = self._cursor_on_stop()
        on_end = self._cursor_on_endpoint()
        self._workshop_bar.set_tools_enabled(
            marker=not on_stop and not on_end,
            snapshot=not on_stop,
            remove=on_stop and not on_end,
            toggle=True,
        )

    def _cursor_on_stop(self) -> bool:
        """True iff the playhead sits on a marker or snapshot (within
        one frame's tolerance)."""
        tol = max(1, self._workshop_bar.frame_ms())
        pos = self._video_pos_ms
        if any(abs(mk.at_ms - pos) <= tol for mk in self._markers):
            return True
        if any(abs(int(s.at_ms) - pos) <= tol for s in self._snapshots):
            return True
        return False

    def _cursor_on_endpoint(self) -> bool:
        return (self._video_pos_ms <= 1
                or self._video_pos_ms >= max(0, self._video_duration_ms - 1))

    def _select_segment_for_position(self, pos_ms: int) -> None:
        """Pick the segment whose half-open span [in, out) contains
        ``pos_ms`` and bind the development panel to it."""
        if not self._segment_bounds:
            return
        idx = 0
        for i, (lo, hi) in enumerate(self._segment_bounds):
            if lo <= pos_ms < hi:
                idx = i
                break
        else:
            idx = len(self._segment_bounds) - 1
        if idx >= len(self._segment_items):
            return
        item_id = self._segment_items[idx].id
        self._selection = ("segment", idx, item_id)
        self._bind_panel_to_selection()
        self._refresh_workshop_model()

    def _bind_panel_to_selection(self) -> None:
        """Push the selected item's adjustment state into
        AdjustmentSurface so the panel reflects what the cursor's on.
        Suppress the resulting ``changed`` echo so the load doesn't
        loop back as a write.

        For a SEGMENT: reads VideoAdjustment(item_id) — Look / Style /
        Filter / Crop / box_angle / aspect.
        For a SNAPSHOT: reads Adjustment(item_id) — same shape as a
        photo, so we use the standard unpack path.
        """
        if self._selection is None or self._eg is None:
            return
        kind, _idx, item_id = self._selection
        self._suppress_persist = True
        try:
            if kind == "segment":
                vadj = self._eg.video_adjustment(item_id)
                style = (vadj.style if vadj and vadj.style else "general")
                look = (vadj.look if vadj else "natural") or "natural"
                creative_filter = vadj.creative_filter if vadj else None
                crop = None
                if vadj is not None and all(v is not None for v in (
                        vadj.crop_x, vadj.crop_y, vadj.crop_w, vadj.crop_h)):
                    crop = (vadj.crop_x, vadj.crop_y, vadj.crop_w, vadj.crop_h)
                angle = (vadj.box_angle if vadj else 0.0) or 0.0
                aspect = (vadj.aspect_ratio_label if vadj else None) or "Original"
                rotation = int(vadj.rotation_degrees if vadj else 0) or 0
                # Push state into the panel. Per-segment extras (mute /
                # volume / speed / fade / stabilise) drive the workshop
                # bar's cluster — push them too.
                self._surface.set_state(
                    look=look, crop_norm=crop, box_angle=angle,
                    style=style, aspect_label=aspect, rotation=rotation,
                    creative_filter=creative_filter,
                    look_strength=1.0,
                )
                volume_pct = int(round((vadj.audio_volume if vadj else 1.0) * 100))
                volume_pct = max(0, min(200, volume_pct))   # the schema goes to 2.0
                self._workshop_bar.set_volume(min(100, volume_pct))
                self._workshop_bar.set_muted(
                    (vadj.audio_volume if vadj else 1.0) == 0.0
                    or not (vadj.include_audio if vadj else True))
                self._workshop_bar.set_speed(vadj.speed if vadj else 1.0)
            elif kind == "snapshot":
                adj = self._eg.adjustment(item_id)
                style, look, creative_filter, crop, angle, aspect = \
                    self._unpack_adjustment(adj, default_style="general")
                rotation = int(getattr(adj, "rotation", 0) or 0) if adj else 0
                look_strength = float(
                    getattr(adj, "look_strength", 1.0)) if adj else 1.0
                self._surface.set_state(
                    look=look, crop_norm=crop, box_angle=angle or 0.0,
                    style=style, aspect_label=aspect, rotation=rotation,
                    creative_filter=creative_filter,
                    look_strength=look_strength,
                )
        finally:
            self._suppress_persist = False

    # ── Workshop signal handlers (timeline) ────────────────────────────

    def _on_workshop_seek(self, ms: int) -> None:
        self._viewport.video_seek(int(ms))

    def _on_segment_clicked(self, seg_idx: int) -> None:
        if 0 <= seg_idx < len(self._segment_items):
            item_id = self._segment_items[seg_idx].id
            self._selection = ("segment", seg_idx, item_id)
            self._bind_panel_to_selection()
            self._refresh_workshop_model()
            # If we're already in dev mode, switching segments means
            # re-extracting a different anchor frame.
            if self._dev_mode_active:
                self._maybe_enter_dev_mode()

    def _on_marker_selected(self, marker_id: str) -> None:
        if marker_id:
            self._selection = ("marker", -1, marker_id)
        else:
            # Cleared — fall back to the segment under the playhead.
            self._select_segment_for_position(self._video_pos_ms)
            return
        self._refresh_workshop_model()

    def _on_marker_moved(self, marker_id: str, new_at_ms: int) -> None:
        if self._eg is None:
            return
        try:
            self._eg.move_video_marker(marker_id, int(new_at_ms))
        except ValueError as e:
            log.info("marker move rejected: %s", e)
        except Exception:                                          # noqa: BLE001
            log.exception("marker move failed: %s", marker_id)
        self._reload_workshop_rows()
        self._refresh_workshop_model()

    # ── Workshop signal handlers (tools row) ───────────────────────────

    def _add_marker_at_playhead(self) -> None:
        if self._eg is None or self._video_id is None:
            return
        try:
            self._eg.add_video_marker(self._video_id, self._video_pos_ms)
        except ValueError as e:
            log.info("marker add rejected: %s", e)
            return
        except Exception:                                          # noqa: BLE001
            log.exception("marker add failed")
            return
        self._reload_workshop_rows()
        self._select_segment_for_position(self._video_pos_ms)
        self._refresh_workshop_model()

    def _add_snapshot_at_playhead(self) -> None:
        if self._eg is None or self._video_id is None:
            return
        try:
            new_id = self._eg.create_video_snapshot(
                self._video_id, self._video_pos_ms)
        except ValueError as e:
            log.info("snapshot add rejected: %s", e)
            return
        except Exception:                                          # noqa: BLE001
            log.exception("snapshot add failed")
            return
        self._reload_workshop_rows()
        self._selection = ("snapshot", -1, new_id)
        self._bind_panel_to_selection()
        self._refresh_workshop_model()
        # A snapshot landed at the playhead — the cursor IS on a stop
        # now, so swap to the developed frame view.
        self._maybe_enter_dev_mode()

    def _remove_stop_at_playhead(self) -> None:
        """Remove the marker or snapshot under the cursor."""
        if self._eg is None:
            return
        tol = max(1, self._workshop_bar.frame_ms())
        pos = self._video_pos_ms
        # Snapshot first (snapshots are first-class stops with their own
        # graphical representation; if both are within tolerance, the
        # snapshot wins — the user can move the playhead to disambiguate
        # if they wanted the marker).
        for s in self._snapshots:
            if abs(int(s.at_ms) - pos) <= tol:
                try:
                    self._eg.delete_child(s.item_id)
                except Exception:                                  # noqa: BLE001
                    log.exception("snapshot delete failed")
                self._reload_workshop_rows()
                self._select_segment_for_position(pos)
                self._refresh_workshop_model()
                return
        for mk in self._markers:
            if abs(int(mk.at_ms) - pos) <= tol:
                try:
                    self._eg.delete_video_marker(mk.id)
                except Exception:                                  # noqa: BLE001
                    log.exception("marker delete failed")
                self._reload_workshop_rows()
                self._select_segment_for_position(pos)
                self._refresh_workshop_model()
                return

    def _status_target_at_cursor(self) -> Optional[tuple]:
        """The Pick/Skip/Toggle target under the cursor (spec/59 §4 —
        "the old culler rule").

        Rules (cursor-position-only, never the click-driven
        ``self._selection``):

        * Cursor on a SNAPSHOT (within one frame of tolerance) →
          ``("snapshot", snap_item_id)``.
        * Otherwise → ``("segment", seg_item_id)`` for the segment
          whose half-open ``[lo, hi)`` interval contains the cursor.
          A position exactly ON a marker therefore targets the
          segment that the marker STARTS (i.e. the clip to the
          marker's right, per spec/56 §1).

        Returns ``None`` when no video is loaded yet.
        """
        if self._video_id is None or not self._segment_items:
            return None
        tol = max(1, self._workshop_bar.frame_ms())
        pos = self._video_pos_ms
        # Snapshot wins — first-class graphical stops.
        for s in self._snapshots:
            if abs(int(s.at_ms) - pos) <= tol:
                return ("snapshot", s.item_id)
        # Else the segment whose [lo, hi) interval contains the cursor.
        idx = self._segment_at(pos)
        if idx is None or idx >= len(self._segment_items):
            return None
        return ("segment", self._segment_items[idx].id)

    def _toggle_status_at_selection(self) -> None:
        """Flip the status of the stop UNDER THE CURSOR (spec/59 §4
        "the old culler rule"). Snapshot wins; otherwise the segment
        containing the cursor. Selection is for the development panel,
        NOT for status — Pick/Skip/Toggle never read it (Nelson
        2026-06-15: "the status control is not working as it should ...
        look at the legacy and implement it exactly as it was")."""
        if self._eg is None:
            return
        target = self._status_target_at_cursor()
        if target is None:
            return
        kind, item_id = target
        try:
            ps_map = self._eg.phase_states(self._phase)
            current = ps_map.get(item_id)
            cur_state = (current.state if current else None)
            if cur_state is None:
                # spec/56 §1 — segments default-Skip; snapshots auto-Pick
                # on placement (creating one IS the intent).
                cur_state = STATE_SKIPPED if kind == "segment" else STATE_PICKED
            new_state = STATE_SKIPPED if cur_state == STATE_PICKED else STATE_PICKED
            self._eg.set_phase_state(item_id, self._phase, new_state)
        except Exception:                                          # noqa: BLE001
            log.exception("toggle status failed for %s", item_id)
            return
        self._refresh_workshop_model()

    def _workshop_reset_all(self) -> None:
        """Reset everything — drop every marker (segments merge back to
        one), drop every snapshot, set the surviving segment back to
        the spec/56 §1 default ("skipped"). Matches the lazy-birth
        contract so a reset reads identically to a fresh open."""
        if self._eg is None or self._video_id is None:
            return
        try:
            for mk in list(self._markers):
                try:
                    self._eg.delete_video_marker(mk.id)
                except Exception:                                  # noqa: BLE001
                    log.exception("reset: marker delete failed")
            for s in list(self._snapshots):
                try:
                    self._eg.delete_child(s.item_id)
                except Exception:                                  # noqa: BLE001
                    log.exception("reset: snapshot delete failed")
            # The surviving single segment — spec/56 §1 default Skip.
            self._reload_workshop_rows()
            if self._segment_items:
                try:
                    self._eg.set_phase_state(
                        self._segment_items[0].id,
                        self._phase, STATE_SKIPPED)
                except Exception:                                  # noqa: BLE001
                    log.exception("reset: phase_state reset failed")
        finally:
            self._reload_workshop_rows()
            self._select_segment_for_position(self._video_pos_ms)
            self._refresh_workshop_model()

    def _workshop_clear_markers(self) -> None:
        if self._eg is None or self._video_id is None:
            return
        for mk in list(self._markers):
            try:
                self._eg.delete_video_marker(mk.id)
            except Exception:                                      # noqa: BLE001
                log.exception("clear markers: delete failed")
        self._reload_workshop_rows()
        self._select_segment_for_position(self._video_pos_ms)
        self._refresh_workshop_model()

    def _workshop_clear_snapshots(self) -> None:
        if self._eg is None or self._video_id is None:
            return
        for s in list(self._snapshots):
            try:
                self._eg.delete_child(s.item_id)
            except Exception:                                      # noqa: BLE001
                log.exception("clear snapshots: delete failed")
        self._reload_workshop_rows()
        self._refresh_workshop_model()

    # ── Workshop signal handlers (transport) ───────────────────────────

    def _step_frame(self, direction: int) -> None:
        delta = int(direction) * max(1, self._workshop_bar.frame_ms())
        new = max(0, min(self._video_duration_ms, self._video_pos_ms + delta))
        self._viewport.video_seek(new)

    def _jump_stop(self, direction: int) -> None:
        """Walk markers ∪ snapshots ∪ endpoints; the spec/59 §4 ◀ Stop /
        Stop ▶ semantics. One frame of tolerance keeps a repeat press
        from re-landing on the stop under the playhead."""
        tol = max(1, self._workshop_bar.frame_ms())
        stops: list[int] = [0, max(0, self._video_duration_ms - 1)]
        stops.extend(int(mk.at_ms) for mk in self._markers)
        stops.extend(int(s.at_ms) for s in self._snapshots)
        stops = sorted(set(stops))
        pos = self._video_pos_ms
        if direction < 0:
            cand = [s for s in stops if s < pos - tol]
            target = cand[-1] if cand else stops[0] if stops else 0
        else:
            cand = [s for s in stops if s > pos + tol]
            target = cand[0] if cand else stops[-1] if stops else 0
        self._viewport.video_seek(target)

    def _jump_to_marker(self, marker_id: str) -> None:
        for mk in self._markers:
            if mk.id == marker_id:
                self._viewport.video_seek(int(mk.at_ms))
                return

    def _jump_to_snapshot(self, at_ms_str: str) -> None:
        try:
            at_ms = int(at_ms_str)
        except ValueError:
            return
        self._viewport.video_seek(at_ms)

    # ── Per-segment extras handlers ────────────────────────────────────

    def _on_mute_toggled(self, muted: bool) -> None:
        """Mute = LIVE audio off + ``include_audio=False`` on the row
        (Nelson 2026-06-15 eyeball — "Mute does not work").
        Unmuting restores the volume slider's current value to the
        live player AND flips ``include_audio`` back on."""
        # Live player: push 0 when muted, restore from the slider on
        # unmute. The viewport caches the volume so a later arm still
        # carries it.
        if muted:
            self._viewport.video_set_volume(0)
        else:
            self._viewport.video_set_volume(
                self._workshop_bar.vol_slider.value())
        # Persist to the SELECTED segment's row.
        if self._selection is None or self._eg is None:
            return
        kind, _idx, item_id = self._selection
        if kind != "segment":
            return
        try:
            vadj = self._eg.video_adjustment(item_id) or m.VideoAdjustment(
                item_id=item_id)
            vadj.include_audio = not bool(muted)
            self._eg.save_video_adjustment(vadj)
        except Exception:                                          # noqa: BLE001
            log.exception("save mute failed for %s", item_id)

    def _on_volume_changed(self, percent: int) -> None:
        """Apply 0..100 percent to the live player AND persist to the
        selected segment's VideoAdjustment.audio_volume (0..2 schema)."""
        v = max(0, min(100, int(percent)))
        self._viewport.video_set_volume(v)
        if self._selection is None or self._eg is None:
            return
        kind, _idx, item_id = self._selection
        if kind != "segment":
            return
        try:
            vadj = self._eg.video_adjustment(item_id) or m.VideoAdjustment(
                item_id=item_id)
            vadj.audio_volume = max(0.0, min(2.0, v / 100.0))
            self._eg.save_video_adjustment(vadj)
        except Exception:                                          # noqa: BLE001
            log.exception("save volume failed for %s", item_id)

    def _on_speed_changed(self, rate: float) -> None:
        r = max(0.05, float(rate))
        self._viewport.video_set_playback_rate(r)
        if self._selection is None or self._eg is None:
            return
        kind, _idx, item_id = self._selection
        if kind != "segment":
            return
        try:
            vadj = self._eg.video_adjustment(item_id) or m.VideoAdjustment(
                item_id=item_id)
            vadj.speed = r
            self._eg.save_video_adjustment(vadj)
        except Exception:                                          # noqa: BLE001
            log.exception("save speed failed for %s", item_id)

    # ── Viewport transport signals → workshop bar ──────────────────────

    def _on_video_position(self, pos_ms: int) -> None:
        self._video_pos_ms = max(0, int(pos_ms))
        if self._video_id is None:
            return
        self._workshop_bar.set_position(self._video_pos_ms)
        # The cursor moved — if the user isn't holding a snapshot or
        # marker explicitly, re-select the segment under the new
        # position so the development panel binds to the right row.
        if self._selection is None or self._selection[0] == "segment":
            new_idx = self._segment_at(self._video_pos_ms)
            sel_changed = (
                new_idx is not None
                and (self._selection is None
                     or self._selection[0] != "segment"
                     or self._selection[1] != new_idx)
            )
            if sel_changed:
                self._on_segment_clicked(new_idx)
                self._maybe_enter_dev_mode()
                return
        # Tools-enable (Marker / Snapshot grey ON a stop; Remove greys
        # off-stop and at endpoints) depends on the live cursor — must
        # recompute on EVERY position move, not only on segment swap.
        # Without this, after placing a marker the cursor sits on it
        # → button greys → seeking elsewhere within the same segment
        # never re-runs the rule and the button stays greyed (Nelson
        # 2026-06-15 eyeball — "I could place only one marker, then
        # the button became non responsive").
        self._refresh_workshop_model()
        # Dev mode check — paused + on a stop = developed frame in
        # the canvas (spec/59 §3 modeless development).
        self._maybe_enter_dev_mode()

    def _segment_at(self, pos_ms: int) -> Optional[int]:
        for i, (lo, hi) in enumerate(self._segment_bounds):
            if lo <= pos_ms < hi:
                return i
        if self._segment_bounds:
            return len(self._segment_bounds) - 1
        return None

    def _on_video_duration(self, dur_ms: int) -> None:
        # If ffprobe gave us a better number on landing, keep it.
        if int(dur_ms) > self._video_duration_ms:
            self._video_duration_ms = int(dur_ms)
        if self._video_id is None:
            return
        self._workshop_bar.set_duration(self._video_duration_ms)
        # Segment bounds depend on duration — re-derive if it just
        # became known.
        if self._video_id is not None and self._video_duration_ms > 0 and \
                not self._segment_bounds:
            self._reload_workshop_rows()
            self._refresh_workshop_model()

    def _on_video_playing(self, playing: bool) -> None:
        if self._video_id is None:
            return
        self._workshop_bar.set_playing(bool(playing))
        # Playing → exit dev mode (the player resumes). Paused →
        # re-check whether we're on a stop and should enter.
        if bool(playing):
            self._exit_dev_mode()
        else:
            self._maybe_enter_dev_mode()

    # ── Modeless development (spec/59 §3) ──────────────────────────────

    def _stop_anchor_at_cursor(self) -> Optional[tuple]:
        """Return ``(kind, item_id, anchor_ms)`` for the stop under
        the cursor, or ``None`` if not on a stop.

        Per Nelson 2026-06-15 #2 eyeball:
        * a **snapshot** at the cursor → its at_ms
        * a **marker** that STARTS a segment (segments are indexed by
          the marker order; segment ``k`` starts at marker ``k``) →
          the segment's item id + that marker's at_ms
        * the implicit start at 0 → segment 0's item id + 0

        The user wanted "marker that is the start point of a video or
        in a snapshot" — both are first-class entry points to dev mode.
        """
        if self._video_id is None or not self._segment_items:
            return None
        tol = max(1, self._workshop_bar.frame_ms())
        pos = self._video_pos_ms
        # Snapshot wins on tie — they're first-class graphical stops.
        for s in self._snapshots:
            if abs(int(s.at_ms) - pos) <= tol:
                return ("snapshot", s.item_id, int(s.at_ms))
        # The implicit start at 0 → segment 0 anchors here.
        if pos <= tol:
            return ("segment", self._segment_items[0].id, 0)
        # User markers — each starts segment ``k = marker_order + 1``.
        for mk in self._markers:
            if abs(int(mk.at_ms) - pos) <= tol:
                # Resolve the segment whose start IS this marker.
                for i, (lo, _hi) in enumerate(self._segment_bounds):
                    if abs(lo - int(mk.at_ms)) <= tol \
                            and i < len(self._segment_items):
                        return (
                            "segment",
                            self._segment_items[i].id,
                            int(mk.at_ms),
                        )
        return None

    def _maybe_enter_dev_mode(self) -> None:
        """Re-check whether the swap-to-developed-frame view should be
        active. Conditions (spec/59 §3): the video is PAUSED AND the
        cursor sits on a stop. Otherwise exit."""
        if self._video_id is None:
            self._exit_dev_mode()
            return
        if self._viewport.video_is_playing():
            self._exit_dev_mode()
            return
        anchor = self._stop_anchor_at_cursor()
        if anchor is None:
            self._exit_dev_mode()
            return
        _kind, item_id, anchor_ms = anchor
        # Already on this anchor → nothing to re-extract (adjustment
        # changes re-render through AdjustmentSurface automatically).
        if (self._dev_mode_active
                and self._dev_mode_item_id == item_id
                and self._dev_mode_anchor_ms == anchor_ms):
            return
        self._enter_dev_mode(item_id, anchor_ms)

    def _enter_dev_mode(self, item_id: str, anchor_ms: int) -> None:
        """Extract the anchor frame, push it through AdjustmentSurface
        (load_image → set_state → render_now pushes the developed
        pixmap to the viewport). Hide the video widget so the
        developed frame is visible. Subsequent adjustment clicks
        re-render automatically via AdjustmentSurface's existing
        changed→render_now plumbing — no extra wiring needed."""
        if self._video_source_path is None or self._eg is None:
            return
        with self._busy():
            arr = self._extract_video_frame_array(item_id, anchor_ms)
            if arr is None:
                return
            try:
                self._surface.load_image(
                    arr, style=self._style_for_selection())
                # set_state pushes adjustments and calls render_now →
                # the developed pixmap lands on the viewport's QLabel.
                self._bind_panel_to_selection()
                # Hide the video widget so the pixmap is visible.
                self._viewport.set_video_widget_visible(False)
            except Exception:                                      # noqa: BLE001
                log.exception("enter dev mode failed for %s @ %dms",
                              item_id, anchor_ms)
                return
        self._dev_mode_active = True
        self._dev_mode_item_id = item_id
        self._dev_mode_anchor_ms = anchor_ms

    def _exit_dev_mode(self) -> None:
        """Step off the stop / play / change selection → restore the
        normal viewport view (video widget visible, no rendered
        override). The AdjustmentSurface's _full_array is cleared so
        subsequent adjustment clicks DO NOT re-render-to-canvas while
        the player is in control."""
        if not self._dev_mode_active:
            return
        self._dev_mode_active = False
        self._dev_mode_item_id = None
        self._dev_mode_anchor_ms = None
        try:
            self._viewport.set_video_widget_visible(True)
            self._viewport.clear_rendered_pixmap()
        except Exception:                                          # noqa: BLE001
            log.exception("exit dev mode: viewport restore failed")
        try:
            self._surface.clear()
        except Exception:                                          # noqa: BLE001
            log.exception("exit dev mode: surface clear failed")

    # ── Locked-keymap routing for video items ──────────────────────────

    def _on_pick_key(self) -> None:
        """P — set the stop UNDER THE CURSOR to Picked (spec/59 §4).
        Snapshot wins; otherwise the segment containing the cursor."""
        if self._video_id is None or self._eg is None:
            return
        target = self._status_target_at_cursor()
        if target is None:
            return
        _kind, item_id = target
        try:
            self._eg.set_phase_state(item_id, self._phase, STATE_PICKED)
        except Exception:                                          # noqa: BLE001
            log.exception("Pick on %s failed", item_id)
        self._refresh_workshop_model()

    def _on_skip_key(self) -> None:
        """X — set the stop UNDER THE CURSOR to Skipped (spec/59 §4)."""
        if self._video_id is None or self._eg is None:
            return
        target = self._status_target_at_cursor()
        if target is None:
            return
        _kind, item_id = target
        try:
            self._eg.set_phase_state(item_id, self._phase, STATE_SKIPPED)
        except Exception:                                          # noqa: BLE001
            log.exception("Skip on %s failed", item_id)
        self._refresh_workshop_model()

    def _on_toggle_key(self) -> None:
        """Space — flip the stop UNDER THE CURSOR (spec/59 §4)."""
        if self._video_id is None:
            return
        self._toggle_status_at_selection()

    def _on_sweep_key(self) -> None:
        """Enter — the locked map's cluster-sweep verb. On a video this
        toggles play/pause (the second transport key alongside Tab),
        matching the legacy EditVideoPage's behaviour. No-op on photos
        (cluster sweep doesn't apply on a creative-only surface)."""
        if self._video_id is not None:
            self._viewport.video_toggle_play()


__all__ = ["EditorPage"]
