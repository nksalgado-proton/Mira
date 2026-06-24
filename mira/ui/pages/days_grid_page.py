"""Surface 06 — Days Grid (capture grid).

Capture-level thumbnail grid for picking / skipping a day's photos. The
legacy chrome (``mira/ui/base/day_grid_view.py`` + ``day_grid_cell.py``)
shipped the spec/32 cell-grammar, the gateway path, and the on-disk
thumbnail loader; this surface is the spec/70 §1 reconciliation — the
redesigned ``Thumb`` widget + a redesigned chrome on TOP of the same
engines (``mira.picked.day_grid_cells``, ``core.photo_thumb_cache``,
``mira.ui.media.photo_cache``). Engines never get rewritten (spec/70 §7).

LOCKED §5a colour grammar (PALETTE, never the accent palette):
    border 3px = state  picked=green / skipped=red / compare=orange
                       / mixed=yellow / neutral=line
    cluster icons       repeated / burst / focus / exposure — from
                       assets/icons/clusters/badge/
    visited eye         top-right translucent chip
    exported badge      bottom-left accent ↑ Exported
    cluster count       bottom-right ×N or split chip 3✓·2✗ for mixed

LOCKED §63 keymap (universal on every photo surface):
    P=Pick  X=Skip  Space=toggle Pick⇄Skip  C=cycle Skip→Pick→Compare
    Esc=back (in cluster mode, closes the cluster; on the day grid,
        emits back_requested)

Composition:
    Sticky toolbar:  Back · day navigator pill · ✓ Pick all · ✗ Skip all
                     · primary + Start a new pass… · review progress
    Legend strip:    swatches + reminder
    Scrolling grid:  flow of Thumb widgets, responsive ~196 px tiles

Live data wiring:
    ``open_for_day(event_id, day_number, …)`` opens the EventGateway,
    runs ``day_grid_cells(eg, day_number, phase="pick")`` (the reused
    engine), maps the resulting :class:`CullCell` list onto the Thumb-
    shaped :class:`GridItem` model, seeds whole-event proxy builds
    (spec/63 slice 7), and feeds an off-thread on-disk thumb decoder.

Cluster expansion:
    A click on a cluster cover swaps the grid contents to the cluster's
    members in place (sub-grid mode); Back/Esc returns to the day grid.

Single-item click:
    Emits :sig:`item_activated(item_id)` so the host can route to the
    Picker. Surface 07 lands the redesigned Picker; until then the host
    bridges to the legacy :class:`PickPage` photo surface.

The mock ``setItemsForPreview`` entry point is preserved so smokes/tests
can populate the grid without a gateway.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QKeyEvent, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from mira.gateway import Gateway
from mira.picked import (
    CullCell,
    CullCluster,
    CullItem,
    day_grid_cells,
)
from mira.picked.status import (
    STATE_CANDIDATE,
    STATE_PICKED,
    STATE_SKIPPED,
    CellColor,
    cell_color_for_item,
    cluster_color,
    default_state_for,
)
from mira.ui.design import (
    StageProgress,
    SurfaceIdentityHeader,
    Thumb,
    ThumbGrid,
    ThumbGridItem,
    confirm,
    danger_ghost_button,
    ghost_button,
    primary_button,
    show_error,
    show_info,
)
from mira.ui.i18n import tr
from mira.ui.palette import PALETTE

log = logging.getLogger(__name__)


def _edit_reason_tooltip(reasons: tuple[str, ...]) -> str:
    """Human, translatable tooltip for the edit-reason pill — e.g.
    "Edited · Look, Crop". Empty when unedited (Nelson 2026-06-18)."""
    if not reasons:
        return ""
    labels = {
        "look": tr("Look"), "filter": tr("Filter"), "crop": tr("Crop"),
    }
    names = ", ".join(labels.get(r, r) for r in reasons)
    return f"{tr('Edited')} · {names}"


# ── Cell-state ↔ Thumb-state mapping ─────────────────────────────────
# CellColor wire values map onto the Thumb's _STATE_KEY directly; this
# table makes the intent explicit for review.
_CELL_TO_THUMB_STATE: Dict[CellColor, Optional[str]] = {
    CellColor.KEPT: "picked",
    CellColor.DISCARDED: "skipped",
    CellColor.COMPARE: "compare",
    CellColor.MIXED: "mixed",
    CellColor.UNTOUCHED: None,
}

# Bucket-scanner cluster kind → Thumb cluster_type. The Thumb's cluster
# badge family (assets/icons/clusters/badge/) keys on these short names.
# ``video`` is the spec/56 Export-mode synthetic cluster — one source
# video's clips + snapshots grouped under one cover (not a scanner
# bucket; the days-grid Export reshape emits it).
_CLUSTER_KIND_TO_THUMB: Dict[str, str] = {
    "burst": "burst",
    "focus_bracket": "focus",
    "exposure_bracket": "exposure",
    "repeat": "repeated",
    "video": "video",
}


# How many item thumbnails to decode per timer tick. Matches the
# PickPage cadence (~20 ms ticks × 4 thumbs = ~200 thumbs/sec on a warm
# disk) so big days don't freeze the surface.
_THUMBS_PER_TICK = 4
_THUMB_TIMER_MS = 20

# Tile sizing — bigger than the smoke's 184×138 so real photos read
# well against the §3.6 punch list ("blurred-fill never shines —
# smokes used gradient placeholders").
_TILE_SIZE = QSize(196, 146)
# Slider bounds (Nelson 2026-06-18, narrowed). Minimum sits just below
# the default so the user can shrink a touch without flipping to
# tiny-thumbnail territory; maximum is 2× the default for the "make
# it big" case (~4K-friendly). Height tracks via the locked aspect
# ratio so the cycle-click hit rectangle stays proportional.
_TILE_ASPECT = _TILE_SIZE.height() / _TILE_SIZE.width()
_TILE_SIZE_MIN = QSize(180, int(round(180 * _TILE_ASPECT)))
_TILE_SIZE_MAX = QSize(
    _TILE_SIZE.width() * 2,
    int(round(_TILE_SIZE.width() * 2 * _TILE_ASPECT)),
)


@dataclass
class _UnexportSnapshot:
    """One ``Exported Media/`` ship row captured for the
    Export-mode undo (Ctrl+Z and re-press-P).

    The file bytes are held in memory until the snapshot is restored
    or evicted from the page's undo stack; that keeps the locked X
    grammar fast (no dialog, instant feedback) while making the
    silent unlink trivially reversible (the user can recover one
    stray X with a single keystroke). The trade is small — the cap
    on the undo stack (``_UNDO_MAX``) bounds memory; the bytes drop
    when the user leaves the day or fills the stack with newer
    decisions."""

    item_id: str
    file_bytes: bytes
    dest_path: Path
    lineage_row: object        # m.Lineage — typed loosely to avoid an import cycle


@dataclass
class _UndoEntry:
    """One reversible Days-Grid decision. Plain phase-state flips
    record only ``item_id`` + ``prev_state``; the Export-mode
    un-export adds the file snapshot so the restore reproduces both
    the in-DB state AND the on-disk file."""

    item_id: str
    prev_state: Optional[str]   # what phase_state.state was before the verb
    new_state: str              # what the verb wrote
    snapshots: list = None      # list[_UnexportSnapshot] when X-on-shipped, else empty

    def __post_init__(self) -> None:
        if self.snapshots is None:
            self.snapshots = []


# Per-page Ctrl+Z stack size. Each Export-mode undo snapshot keeps a
# JPEG's bytes alive; ~16 entries × 2-5 MB each stays under 100 MB
# even at the high end. The next snapshot drops the oldest.
_UNDO_MAX = 16


@dataclass
class GridItem:
    """One grid cell's rendered content. The page builds these from
    :class:`CullCell` on the gateway path; smokes and tests can build
    them directly to populate the grid without a gateway.

    ``item_id`` is the photo / video / cluster-cover identifier:
    * single photo / video / snapshot — the captured item id
    * cluster cover                   — ``"cluster:<bucket_key>"``

    ``item_kind`` is ``"photo"`` | ``"video"`` | ``"cluster"``; the page
    routes clicks by this.

    ``stamp`` (spec/56) is the per-cell type stamp shown by Thumb on
    Export-mode child cells: ``"clip"`` → "Video Clip", ``"snapshot"``
    → "Snapshot". ``None`` on photos, parent clusters, and Pick/Edit
    grids (the stamp is Export-mode-only).
    """

    item_id: str
    item_kind: str = "photo"
    pixmap: QPixmap | None = None
    state: str | None = None
    visited: bool = False
    exported: bool = False
    edit_reasons: tuple[str, ...] = ()
    border_token: str | None = None
    edit_tooltip: str = ""
    cluster_type: str | None = None
    cluster_count: int = 0
    cluster_split: tuple[int, int] | None = None
    stamp: str | None = None
    # spec/89 §2.1 / Block 2 D3.B — origin wordmark for the strip
    # under the thumb (Mira / LRC / Helicon / CO / ext). ``None``
    # leaves the strip empty (0-version cells, Pick/Edit grids).
    origin: str | None = None
    # spec/89 §4.2 / Block 7 D2.B — Export mode: this item is in the
    # pool only because it has a file under Exported Media/, NOT
    # because it was picked in Pick. Renders a small "skipped in Pick"
    # indicator chip on the cell so the user knows why it's here.
    skipped_in_pick: bool = False
    # spec/118 §2 — the on-disk Mira render's recipe no longer matches
    # the live Adjustment. Drives the loud "Edited" badge on the cell;
    # on a versions cluster cover, set whenever any member is stale.
    edited_since_export: bool = False
    # Internal — populated only on the gateway path. Held so the
    # P/X/Space/C verbs can persist directly via the EventGateway and
    # so cluster covers can expand without a second lookup.
    _path: Path | None = None
    _sha256: str | None = None
    _cull_cluster: Optional[CullCluster] = None
    # Backing video item id, for video clusters. Used by the ship
    # logic to find segments/snapshots when iterating cluster covers
    # at the day level.
    _video_item_id: Optional[str] = None


class _DayNavigatorPill(QFrame):
    """#Card[level="2"]-styled pill ‹ Day N · title · date · N items ›.

    Mutable: ``set_day(...)`` updates the label in place so the page
    can refresh without rebuilding the widget (rebuild + deleteLater
    left the old widget painted under the new one — Nelson 2026-06-14)."""

    prev_clicked = pyqtSignal()
    next_clicked = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        # spec/92 §2.3 → #Card[level="2"] (collapsed from #Card2).
        self.setObjectName("Card")
        self.setProperty("level", "2")
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(10)
        # ``#DayPillNav`` chevrons: tight, no Ghost padding (the prev/next
        # used to render blank — see redesign.qss for the fix).
        prev_btn = QPushButton("‹")
        prev_btn.setObjectName("DayPillNav")
        prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        prev_btn.clicked.connect(self.prev_clicked.emit)
        h.addWidget(prev_btn)
        self._label = QLabel("")
        self._label.setObjectName("Sub")
        h.addWidget(self._label)
        next_btn = QPushButton("›")
        next_btn.setObjectName("DayPillNav")
        next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        next_btn.clicked.connect(self.next_clicked.emit)
        h.addWidget(next_btn)

    def set_day(
        self,
        day_number: int,
        title: str,
        date_iso: str,
        item_count: int,
    ) -> None:
        meta = " · ".join(b for b in (
            f"Day {day_number}",
            title,
            date_iso,
            f"{item_count} items",
        ) if b)
        self._label.setText(meta)


def _state_swatch(state: str, label: str) -> QWidget:
    """Tiny picked/skipped/compare/mixed legend chip — one square outline
    in the locked PALETTE color + a small label."""
    host = QWidget()
    h = QHBoxLayout(host)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(6)
    # Use the dark palette as the canonical legend colors (the swatches
    # are painted with the FIXED §5a values, never the theme accent).
    color = PALETTE["dark"][state]
    swatch = QLabel()
    swatch.setFixedSize(18, 14)
    swatch.setStyleSheet(  # pragma: no-qss — §5a legend swatch border colour is data-driven
        f"background: transparent; border: 3px solid {color};"
        f" border-radius: 5px;"
    )
    h.addWidget(swatch)
    txt = QLabel(label)
    txt.setObjectName("Sub")
    h.addWidget(txt)
    return host


class DaysGridPage(QWidget):
    """Surface 06 — the capture grid page.

    Lifecycle:
        open_for_day(event_id, day_number, …) → opens an EventGateway,
        renders the day's cells. close_event() releases the gateway.
        The page can be re-entered on a different day without
        recreating it.
    """

    back_requested = pyqtSignal()
    prev_day_requested = pyqtSignal()
    next_day_requested = pyqtSignal()
    pick_all_requested = pyqtSignal()
    skip_all_requested = pyqtSignal()
    new_pass_requested = pyqtSignal()
    # spec/118 §3 — last batch collision choice. Shared across pages so
    # the run-level Overwrite / Keep both dialog can default to the
    # user's previous pick. Reset only on app restart (intentional —
    # a deliberate one-time pick should ride the rest of the session).
    _last_batch_collision: str = "unique"
    # spec/70 / Nelson 2026-06-22 — standalone Quick Sweep footer fires
    # this so the host copies the kept set to the destination + finishes.
    quick_sweep_export_requested = pyqtSignal()
    # Single-photo / video click on the day grid OR cluster sub-grid.
    # Routed by the host to the Picker (legacy PickPage until surface
    # 07 lands the redesigned Picker shell).
    item_activated = pyqtSignal(str)

    def __init__(
        self,
        gateway: Optional[Gateway] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.gateway = gateway
        # Live event state — set by ``open_for_day``; None when the
        # page is in smoke/mock mode (setItemsForPreview path).
        self._event_id: Optional[str] = None
        self._event_name: str = ""
        self._eg = None
        # spec/63 slice 7 — event roots whose proxies we already
        # kicked off (Nelson 2026). The whole-event seed is a one-
        # shot "be proactive" call; firing it again on every day-
        # switch makes the BatchProgressLine tick through "Creating
        # previews — N left" each grid open, even when every proxy
        # is already on disk. The set survives ``_close_event_
        # internal`` so reopening ANY day of an already-seeded
        # event (or returning to it after visiting another event)
        # is a no-op for the lifetime of this DaysGridPage.
        self._seeded_proxy_event_roots: set[Path] = set()
        # ``_phase`` is the **phase_state storage key** the grid reads
        # and writes (``"pick"`` or ``"edit"``). ``_export_mode`` is
        # the orthogonal UX flag the Export phase carries: shares the
        # ``"edit"`` phase_state storage with the Edit surface
        # (spec/66 §1.1 — Edit and Export are one decision ledger),
        # but flips the click handler from drill-in to in-place
        # toggle and wires X-on-shipped to ``delete_exported_file``.
        # The identity header reads ``_identity_phase`` separately
        # ("export") so the surface chrome is green per spec/71.
        self._phase = "pick"
        self._phase_default = STATE_SKIPPED
        self._export_mode = False
        # Quick Sweep footer variant: ``"export"`` (standalone — copy to
        # a folder), ``"back"`` (event-context — flow on into the event),
        # or ``None`` (not a QS host; normal phase chrome). Set by
        # :meth:`set_quick_sweep_footer`; reset by ``open_for_day``.
        self._qs_footer: Optional[str] = None
        # spec/71 identity phase — drives the SurfaceIdentityHeader rail
        # + badge. Defaults to ``"pick"``; ``open_for_day(phase=...)``
        # syncs it from ``self._phase`` (Pick/Edit) and Quick Sweep
        # hosts call :meth:`set_phase_identity("collect")` so the shared
        # grid reads Collect/blue under their wrappers.
        self._identity_phase = "pick"
        self._identity: Optional[SurfaceIdentityHeader] = None
        # Day-mode bookkeeping.
        self._day_number = 1
        self._day_title = ""
        self._day_date = ""
        # Items currently shown — day mode OR cluster sub-grid mode.
        self._items: list[GridItem] = []
        # The day's items, cached so we can rebuild on Back from cluster.
        self._day_items: list[GridItem] = []
        # Cluster mode tracks the cluster we drilled into.
        self._mode = "day"  # "day" | "cluster"
        self._cluster: Optional[CullCluster] = None
        # Paths-mode (no gateway) callbacks. The standalone / wizard
        # Quick Sweep host registers these so cluster drill-in colours
        # sub-grid cells from the QS ledger and Back-from-cluster can
        # ask the host for a fresh day-grid GridItem list (so cluster
        # covers repaint with their updated aggregate state after the
        # user marks members inside).
        self._paths_state_lookup = None      # Callable[[Path], Optional[str]]
        self._paths_day_rebuild = None       # Callable[[], list[GridItem]]
        # Counts displayed in the toolbar progress block.
        self._reviewed = 0
        self._total = 0

        # spec/131 — the last item_id the user clicked on the grid.
        # Hosts read this via :meth:`current_entry_anchor` as a
        # fallback for restoring the grid's scroll position when a
        # viewer reports no current item (rare). The live restore path
        # uses the viewer's own last item id; this is the safety net.
        self._entry_anchor_item_id: Optional[str] = None

        # spec/63 §4 Ctrl+Z = undo last decision. Plain phase-state
        # flips push lightweight entries (~80 bytes); Export-mode
        # X-on-shipped pushes a snapshot of the deleted JPEG + its
        # lineage row so the restore reproduces the on-disk state.
        # Stack-bounded by ``_UNDO_MAX`` so the bytes can't grow
        # unbounded; cleared on event close.
        self._undo_stack: list[_UndoEntry] = []

        # Per-cell focus tracking — the locked P/X/Space/C keys act on
        # the Thumb the user last clicked (Qt focus follows). The
        # ``_thumb_widgets`` property below proxies to the live
        # :class:`ThumbGrid` cells (the chunked build adds more after
        # the first batch lands).

        # On-disk thumbnail loader (port of the PickPage cadence —
        # chunked, off-the-UI-thread, never freezes the grid).
        self._thumb_pending: list[tuple[int, str]] = []   # (index, item_id)
        self._thumb_pixmap_cache: Dict[str, QPixmap] = {}
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setInterval(_THUMB_TIMER_MS)
        self._thumb_timer.timeout.connect(self._load_some_thumbs)

        self._build_ui()
        # DaysGridPage itself takes the focus role for the locked
        # keymap — when a Thumb has focus the key bubbles up to the
        # page, when no Thumb is focused the page handles it directly.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ── UI assembly ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Back lives in the shared title bar; the click is routed through
        # this page's mode-aware handler via ``on_titlebar_back`` so an open
        # cluster closes before the surface itself backs out.
        self.uses_titlebar_back = True

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # spec/71 identity — the SHARED Days Grid inherits its host phase's
        # colour as a full-width rail (matching Events / Phases / Days List).
        # Recoloured on every phase swap via _refresh_identity(). The legend
        # strip (the picked/skipped/compare chrome unique to this grid) now
        # lives inside the grid band below, captioning the grid it describes.
        self._rail = QFrame()
        self._rail.setObjectName("SurfaceHeaderRail")
        self._rail.setFixedHeight(2)
        root.addWidget(self._rail)
        self._refresh_identity()

        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(28, 18, 28, 22)
        outer.setSpacing(12)
        root.addWidget(content, 1)

        # ── Top band — the header, broken into three logical lines ──
        #   line 1: day navigator (where am I)   · review progress (how far)
        #   line 2: bulk decide verbs (left)     · primary flow actions (right)
        #   line 3: view density — the cell-size slider (right)
        top_band = QFrame()
        top_band.setObjectName("SurfaceBand")
        top_l = QVBoxLayout(top_band)
        top_l.setContentsMargins(16, 12, 16, 12)
        top_l.setSpacing(10)

        # line 1 — day navigator (left) · review progress (right)
        line1 = QHBoxLayout()
        line1.setSpacing(10)
        self._day_pill = _DayNavigatorPill()
        self._day_pill.set_day(1, "", "", 0)
        self._day_pill.prev_clicked.connect(self.prev_day_requested.emit)
        self._day_pill.next_clicked.connect(self.next_day_requested.emit)
        line1.addWidget(self._day_pill)
        line1.addStretch()
        progress_block = QVBoxLayout()
        progress_block.setSpacing(2)
        self._progress_label = QLabel("0 / 0 reviewed")
        self._progress_label.setObjectName("Sub")
        self._progress_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        progress_block.addWidget(self._progress_label)
        self._progress_bar = StageProgress()
        self._progress_bar.setMinimumWidth(180)
        progress_block.addWidget(self._progress_bar)
        line1.addLayout(progress_block)
        top_l.addLayout(line1)

        # line 2 — bulk decide verbs (left) · view density (centre) ·
        # primary flow actions (right). Folding the cell-size slider into the
        # centre keeps the header to two lines (Nelson 2026-06-20).
        line2 = QHBoxLayout()
        line2.setSpacing(10)
        self._pick_all_btn = ghost_button("✓ Pick all")
        self._pick_all_btn.clicked.connect(self._on_pick_all_clicked)
        line2.addWidget(self._pick_all_btn)
        self._skip_all_btn = danger_ghost_button("✗ Skip all")
        self._skip_all_btn.clicked.connect(self._on_skip_all_clicked)
        line2.addWidget(self._skip_all_btn)
        # spec/89 §11.3 — Compare button for the versions cluster sub-grid.
        # Visible only when ``_mode == "cluster"`` AND the open cluster's
        # kind is "versions" (set in :meth:`_apply_phase_chrome`). Opens
        # :class:`CompareVersionsDialog` side-by-side.
        self._compare_btn = ghost_button("⇄ Compare versions")
        self._compare_btn.setToolTip(
            "Open every version side-by-side at full definition. "
            "Click a tile's border to mark Will export / Set aside.")
        self._compare_btn.clicked.connect(self._on_compare_versions)
        self._compare_btn.setVisible(False)
        line2.addWidget(self._compare_btn)
        # view density — the cell-size slider, centred between the verb group
        # and the flow actions. Nelson 2026-06-18: the slider value drives the
        # grid's cell width in 10-px steps; height tracks via the cell's
        # locked aspect ratio. Session-local — no persistence.
        line2.addStretch()
        size_label = QLabel("Size")
        size_label.setObjectName("Sub")
        line2.addWidget(size_label)
        self._size_slider = QSlider(Qt.Orientation.Horizontal)
        self._size_slider.setObjectName("GridSizeSlider")
        self._size_slider.setMinimum(_TILE_SIZE_MIN.width())
        self._size_slider.setMaximum(_TILE_SIZE_MAX.width())
        self._size_slider.setSingleStep(10)
        self._size_slider.setPageStep(20)
        self._size_slider.setValue(_TILE_SIZE.width())
        self._size_slider.setFixedWidth(120)
        self._size_slider.setToolTip("Resize the grid cells")
        self._size_slider.valueChanged.connect(self._on_size_slider_changed)
        line2.addWidget(self._size_slider)
        line2.addStretch()
        self._new_pass_btn = primary_button("+ Start a new pass…")
        self._new_pass_btn.clicked.connect(self.new_pass_requested.emit)
        line2.addWidget(self._new_pass_btn)
        # spec/68 §3 / spec/89 §5.1 D1.A — Export-mode primary trigger.
        # Hidden outside Export mode; the per-day batch submitter wires
        # below. Label is locked to "Export now" per spec/89 §5.1.
        self._export_btn = primary_button("↑ Export now")
        self._export_btn.clicked.connect(self._on_export_clicked)
        self._export_btn.setVisible(False)   # only in Export phase
        line2.addWidget(self._export_btn)
        # Quick Sweep footer (Nelson 2026-06-22). The shared grid is
        # reused under two QS hosts with DIFFERENT endings:
        #   * standalone QS → the kept set is COPIED to a destination
        #     folder; the grid shows a primary "Export now" that fires
        #     :sig:`quick_sweep_export_requested` so the host runs the
        #     copy-and-finish.
        #   * event-context QS (Collect / new-event) → there is no
        #     export step: the kept photos flow on into the event, so
        #     the grid shows a ghost "Back". (It's also hosted in a
        #     modal with no app title bar, so it needs its own Back.)
        # Both are hidden outside a QS session; the variant is chosen by
        # :meth:`set_quick_sweep_footer` and applied in
        # :meth:`_apply_phase_chrome`.
        self._qs_export_btn = primary_button("↑ Export now")
        self._qs_export_btn.setToolTip(
            "Copy the kept photos to the destination folder and finish.")
        self._qs_export_btn.clicked.connect(
            self.quick_sweep_export_requested.emit)
        self._qs_export_btn.setVisible(False)
        line2.addWidget(self._qs_export_btn)
        self._qs_back_btn = ghost_button("‹ Back")
        self._qs_back_btn.setToolTip("Back to the day list  (Esc)")
        self._qs_back_btn.clicked.connect(self.back_requested.emit)
        self._qs_back_btn.setVisible(False)
        line2.addWidget(self._qs_back_btn)
        # spec/76 §B.1 — Export materialises files + stamps event.db.
        # Both refuse in read-only; grey the trigger so the user sees
        # the closure upfront. Same for the "new pass" verb (starts a
        # pick session that mutates).
        from mira.ui.read_only import disable_if_read_only
        disable_if_read_only(self._new_pass_btn)
        disable_if_read_only(self._export_btn)
        top_l.addLayout(line2)

        outer.addWidget(top_band)
        outer.addSpacing(8)

        # ── Grid band — the box around the grid content INCLUDES the legend ──
        # Per Nelson 2026-06-20: the state legend now caps the grid it
        # describes, inside the same border.
        grid_band = QFrame()
        grid_band.setObjectName("SurfaceBand")
        grid_l = QVBoxLayout(grid_band)
        grid_l.setContentsMargins(16, 12, 16, 14)
        grid_l.setSpacing(10)

        # ── Legend strip ──
        # Wrapped in a QWidget so the Edit phase (creative-only, spec/66
        # §1.1 — no picked/skipped/compare decision) can hide it as a
        # whole (BUGS.md B-010, Nelson 2026-06-17). The content is
        # phase-driven: Pick has the four picked/skipped/compare/mixed
        # swatches, Export swaps to the spec/89 §4.2 (Block 4) vocabulary —
        # "Will export · Dropped · Undecided". Rebuilt in
        # :meth:`_rebuild_legend_strip` so a phase swap swaps labels cleanly.
        self._legend_host = QWidget()
        self._legend_layout = QHBoxLayout(self._legend_host)
        self._legend_layout.setContentsMargins(0, 0, 0, 0)
        self._legend_layout.setSpacing(18)
        grid_l.addWidget(self._legend_host)
        self._rebuild_legend_strip()
        # spec/89 §2.2 — the external-edits scan chip, visible only under the
        # Export identity. ``set_scan_status`` updates the wording.
        self._scan_chip = QLabel("External edits: up to date")
        self._scan_chip.setObjectName("Faint")
        self._scan_chip.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._scan_chip.setVisible(False)
        grid_l.addWidget(self._scan_chip)

        # ── Scrolling grid ──
        # Built on the shared :class:`ThumbGrid` so the locked §5a 3px
        # state border + blurred-fill canvas are the same paint as the
        # Cuts surfaces. Two-zone clicks (BUGS.md B-006, Nelson 2026-06-17):
        # a click in the cell BORDER changes status (cycle Skip→Pick→Compare,
        # matching the §63 single-photo border-click grammar), a click in
        # the CENTER opens — drills into the Picker (Pick/Edit) or toggles
        # green/red (Export mode). Mirrors the legacy DayGridCell rule.
        self._grid = ThumbGrid(
            cell_size=_TILE_SIZE,
            two_zone_clicks=True,
            flow_spacing=18,
            flow_margin=0,
        )
        self._grid.cell_activated.connect(self._on_grid_cell_activated)
        self._grid.cell_border_clicked.connect(
            self._on_grid_cell_border_clicked)
        grid_l.addWidget(self._grid, 1)

        outer.addWidget(grid_band, 1)

    # ── Public API (gateway path) ──────────────────────────────────────

    def open_for_day(
        self,
        event_id: str,
        day_number: int,
        *,
        title: str = "",
        date_iso: str = "",
        default_state: Optional[str] = None,
        phase: str = "pick",
        anchor_item_id: Optional[str] = None,
    ) -> bool:
        """Open ``event_id`` and render its ``day_number`` grid.

        Reuses the spec/32 ``day_grid_cells`` engine (clusters become
        cluster covers, real cluster kinds; videos and flattened items
        are flat cells). Status comes from the live ``phase_state``.

        ``default_state`` overrides the phase-default for un-decided
        items (e.g. host sets ``"picked"`` during a Quick Sweep session
        so the QS default rules instead of ``pick_default_state``).
        Defaults to the configured ``{phase}_default_state``.

        ``phase`` (spec/70 Phase 3 / spec/68 §3) selects the phase the
        grid colours + cell-state writes target:

        * ``"pick"`` (the default) — Pick decisions; cells read from
          ``phase_state(phase="pick")``; click drills into the Picker.
        * ``"edit"`` — Edit-phase chrome; creative-only (spec/66 §1.1),
          no Pick/Skip decision; click drills into the Editor.
        * ``"export"`` (spec/68 §3) — green/red ship decision; cells
          read from ``phase_state(phase="edit")`` (the shared ledger
          per spec/66 §1.1), default green ("born ship"), click
          toggles in place (no drill-in), X on a shipped cell also
          unlinks the file via ``delete_exported_file`` and the
          toolbar carries an "Export green (N)" primary button.

        ``item_activated`` still fires for Pick/Edit so the host
        (MainWindow) can route to the leaf surface; in Export mode
        the click handler swallows it (the decision lands in place).

        Returns ``True`` on success. On a gateway open failure the
        page is left in its previous state and ``False`` is returned;
        the host should remain on the Days Lists.
        """
        if self.gateway is None:
            log.warning("open_for_day called without a gateway")
            return False
        self._close_event_internal()
        try:
            eg = self.gateway.open_event(event_id)
        except Exception:                                          # noqa: BLE001
            log.exception("DaysGridPage: cannot open event %s", event_id)
            return False
        self._eg = eg
        self._event_id = event_id
        try:
            ev = eg.event()
            self._event_name = ev.name or tr("(unnamed event)")
        except Exception:                                          # noqa: BLE001
            self._event_name = tr("(unnamed event)")
        # spec/68 §3 — Export mode reuses the Edit phase_state storage
        # (same green/red ledger as the Edit surface) but flips
        # behaviour: born-green default, click toggles in place,
        # X-on-shipped also cleans the on-disk file.
        if phase == "export":
            self._phase = "edit"             # shared storage
            self._export_mode = True
            self._phase_default = (
                default_state if default_state in (
                    STATE_PICKED, STATE_SKIPPED)
                else STATE_PICKED            # born green
            )
            new_identity = "export"
        else:
            self._phase = phase if phase in ("pick", "edit") else "pick"
            self._export_mode = False
            self._phase_default = (
                default_state if default_state in (
                    STATE_PICKED, STATE_SKIPPED)
                else default_state_for(self.gateway.settings, self._phase)
            )
            new_identity = self._phase
        # Opening for a real phase clears any Quick Sweep footer left on
        # the shared page from a prior QS session (Nelson 2026-06-22).
        self._qs_footer = None
        # spec/71 — sync the identity header FIRST so _apply_phase_chrome
        # reads the final identity (QS hosts override afterwards via
        # :meth:`set_phase_identity` / :meth:`set_quick_sweep_footer`).
        if self._identity_phase != new_identity:
            self._identity_phase = new_identity
            self._refresh_identity()
        # spec/66 §1.1 — Edit is creative-only: hide the Pick all /
        # Skip all / Start a new pass… buttons (no decision to make
        # here). They reappear when the page opens for the Pick phase.
        # Export mode swaps the toolbar to the ship verbs.
        self._apply_phase_chrome()
        self._day_number = day_number
        self._day_title = title or ""
        self._day_date = date_iso or ""
        self._mode = "day"
        self._cluster = None
        try:
            self._seed_proxies_for_event()
            self._refresh_from_gateway()
        except Exception:                                          # noqa: BLE001
            log.exception(
                "DaysGridPage: building day %s failed; closing gateway",
                day_number)
            self._close_event_internal()
            return False
        # spec/131 — restore the user's last position. The grid's
        # ThumbGrid builds in chunks, so this may not scroll
        # immediately; ``ensure_item_visible`` queues the request and
        # the ``build_finished`` signal applies it once the target cell
        # exists in the layout. Graceful when the item isn't on this
        # day (returns False → no scroll → grid stays at top).
        if anchor_item_id is not None:
            self._grid.ensure_item_visible(anchor_item_id)
            # Remember this as the entry anchor too — if the viewer
            # later closes without reporting an item (rare), the host
            # can fall back to this on the next restore.
            self._entry_anchor_item_id = anchor_item_id
        return True

    def current_entry_anchor(self) -> Optional[str]:
        """spec/131 — the item id the host should treat as a fallback
        restore anchor (the last item the user clicked on this grid).
        ``None`` when nothing has been clicked yet this session."""
        return self._entry_anchor_item_id

    def close_event(self) -> None:
        """Release any open event gateway. Idempotent."""
        self._close_event_internal()

    def current_day_number(self) -> int:
        """Day the page is currently rendering (used by the host's
        single-item handoff to the Picker to know which day to open)."""
        return self._day_number

    def current_event_id(self) -> Optional[str]:
        """Event id the page is currently rendering."""
        return self._event_id

    def current_cluster(self) -> Optional[CullCluster]:
        """The cluster currently drilled into (cluster sub-grid mode), or
        ``None`` in flat day mode. The host uses this to route a cluster
        member click into :meth:`PickerPage.open_to_cluster` so Enter
        sweep + intra-cluster ← → + Combined preview all work."""
        if self._mode == "cluster":
            return self._cluster
        return None

    def set_scan_status(self, report) -> None:
        """spec/89 §2.2 — push a spec/57 §3 return-scan report to the
        Export scan chip. The chip is hidden on non-Export phases; the
        text is updated here so the next phase swap reads the latest."""
        try:
            from core.export_provenance import scan_chip_text
            self._scan_chip.setText(scan_chip_text(report))
        except Exception:                                          # noqa: BLE001
            log.exception("DaysGridPage: scan_chip_text failed")

    def _rebuild_legend_strip(self) -> None:
        """Rebuild the legend host's content for the current phase. Pick
        keeps the four picked/skipped/compare/mixed swatches; Export
        swaps to the spec/89 §4.2 vocabulary (Block 4 D1.B / D2.A / D3.A);
        Edit hides the whole strip (`_apply_phase_chrome` controls the
        host's visibility). Idempotent on phase swap."""
        # Clear the layout in place — `setParent(None)` so the widgets
        # get reaped on the next event-loop pass without leaking
        # parents into the QSS selector graph.
        while self._legend_layout.count():
            item = self._legend_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        is_export = bool(getattr(self, "_export_mode", False))
        if is_export:
            # Block 4 D1.B — three swatches with action-verb labels.
            self._legend_layout.addWidget(_state_swatch("picked", tr("Will export")))
            self._legend_layout.addWidget(_state_swatch("skipped", tr("Set aside")))
            self._legend_layout.addWidget(_state_swatch("compare", tr("Undecided")))
            # Block 4 D3.A — reminder line.
            reminder_text = (
                "border <b>= decision</b>"
                " · wordmark <b>= origin</b>"
                " · count chip <b>= versions</b>"
            )
            # Block 4 D2.A — Export-specific keymap vocabulary.
            keys_text = "P Export · X Drop · Space toggle"
        else:
            # Pick / generic legend (unchanged).
            self._legend_layout.addWidget(_state_swatch("picked", tr("Picked")))
            self._legend_layout.addWidget(_state_swatch("skipped", tr("Skipped")))
            self._legend_layout.addWidget(_state_swatch("compare", tr("Compare")))
            self._legend_layout.addWidget(_state_swatch("mixed", tr("Mixed cluster")))
            reminder_text = (
                "border <b>= state</b>"
                " · badge <b>= cluster</b>"
                " · eye <b>= visited</b>"
            )
            keys_text = "P Pick · X Skip · Space toggle · C Compare"
        reminder = QLabel(reminder_text)
        reminder.setObjectName("Sub")
        reminder.setTextFormat(Qt.TextFormat.RichText)
        self._legend_layout.addWidget(reminder)
        self._legend_layout.addStretch()
        keys = QLabel(keys_text)
        keys.setObjectName("Sub")
        keys.setTextFormat(Qt.TextFormat.RichText)
        self._legend_layout.addWidget(keys)

    def _apply_phase_chrome(self) -> None:
        """spec/70 Phase 3 / spec/68 §3 — phase-driven chrome:

        * **Pick**: Pick all / Skip all / Start a new pass… visible.
        * **Edit**: bulk buttons hidden — Edit is creative-only
          (spec/66 §1.1), no Pick/Skip decision to make on the grid.
          The picked/skipped/compare legend strip also disappears here
          (BUGS.md B-010 — there is no border state to legend).
        * **Export**: Pick all + Skip all visible but **relabelled**
          to the ship verbs (Export all / Drop all), Start-a-new-pass
          hidden, and the Export-green primary action revealed.
        """
        is_pick = (self._phase == "pick" and not self._export_mode)
        is_edit = (self._phase == "edit" and not self._export_mode)
        is_export = bool(self._export_mode)
        # Pick all / Skip all show in Pick and Export. Their wiring is
        # the same (bulk phase_state write); only the labels change.
        for w in (self._pick_all_btn, self._skip_all_btn):
            try:
                w.setVisible(is_pick or is_export)
            except Exception:                                      # noqa: BLE001
                pass
        try:
            self._new_pass_btn.setVisible(is_pick)
        except Exception:                                          # noqa: BLE001
            pass
        try:
            self._export_btn.setVisible(is_export)
        except Exception:                                          # noqa: BLE001
            pass
        # spec/89 §11.3 — Compare versions only fires when the user is
        # already inside a versions cluster sub-grid. Hide everywhere
        # else; :meth:`_open_cluster` / :meth:`_close_cluster`
        # re-evaluate this on enter / exit.
        try:
            in_versions_subgrid = (
                is_export
                and self._mode == "cluster"
                and self._cluster is not None
                and getattr(self._cluster, "kind", "") == "versions"
            )
            self._compare_btn.setVisible(bool(in_versions_subgrid))
        except Exception:                                          # noqa: BLE001
            pass
        # Relabel the bulk verbs for the Export context (the
        # underlying action stays a bulk phase_state write; spec/71's
        # reminder line tells the user the verbs mean ship/drop here).
        try:
            if is_export:
                self._pick_all_btn.setText(tr("✓ Export all"))
                self._skip_all_btn.setText(tr("✗ Drop all"))
            else:
                self._pick_all_btn.setText(tr("✓ Pick all"))
                self._skip_all_btn.setText(tr("✗ Skip all"))
        except Exception:                                          # noqa: BLE001
            pass
        # BUGS.md B-010 — Edit is creative-only per spec/66 §1.1; the
        # legend belongs to phases that carry a per-cell state
        # decision. Hide it whenever the grid is in pure Edit mode
        # (Export mode keeps it — the ship grammar still uses the
        # green/red border, just with the spec/89 §4.2 vocabulary
        # rebuilt below).
        try:
            self._legend_host.setVisible(not is_edit)
            # spec/89 §4.2 — rebuild the strip's content for the new
            # phase (swaps Export's "Will export / Dropped / Undecided"
            # in for Pick's four-swatch row).
            self._rebuild_legend_strip()
        except Exception:                                          # noqa: BLE001
            pass
        # spec/89 §2.2 — the scan chip is Export-only.
        try:
            self._scan_chip.setVisible(is_export)
        except Exception:                                          # noqa: BLE001
            pass
        # Quick Sweep footer override (Nelson 2026-06-22). When a QS host
        # is driving the grid, its footer replaces the gateway Export
        # trigger + "Start a new pass…": standalone shows "Export now"
        # (copy to folder), event-context shows "Back". Applied last so
        # it wins over the phase-driven visibility above.
        qs = getattr(self, "_qs_footer", None)
        try:
            self._qs_export_btn.setVisible(qs == "export")
            self._qs_back_btn.setVisible(qs == "back")
        except Exception:                                          # noqa: BLE001
            pass
        if qs in ("export", "back"):
            for w in (self._export_btn, self._new_pass_btn):
                try:
                    w.setVisible(False)
                except Exception:                                  # noqa: BLE001
                    pass

    def set_quick_sweep_footer(self, variant: Optional[str]) -> None:
        """Select the Quick Sweep footer (Nelson 2026-06-22). Hosts call
        this after :meth:`set_phase_identity` / :meth:`setDay` /
        :meth:`open_for_day`:

        * ``"export"`` — standalone QS: a primary "Export now" that fires
          :sig:`quick_sweep_export_requested` (host copies the kept set
          to the destination folder + finishes).
        * ``"back"`` — event-context QS (Collect / new-event): a ghost
          "Back" (the kept photos flow on into the event; no export).
        * ``None`` — not a QS host; restore the normal phase chrome.
        """
        self._qs_footer = variant if variant in ("export", "back") else None
        self._apply_phase_chrome()

    # ── spec/71 identity header (per-phase chrome) ────────────────────

    _IDENTITY_SPEC = {
        "collect": ("Quick Sweep",
                    "Fast pass — skip the obvious rejects"),
        "pick":    ("Pick",
                    "Decide each shot — pick the keepers"),
        "edit":    ("Edit",
                    "Develop your picked keepers"),
        "export":  ("Export",
                    "Choose what ships"),
    }

    def _refresh_identity(self) -> None:
        """Recolour the full-width identity rail for the current host phase.

        The Days Grid no longer carries a SurfaceIdentityHeader badge/purpose
        block (spec/92 design pass — nothing floating); the host phase now
        reads only through the rail accent, matching Events / Phases /
        Days List. The phase-specific name/purpose live in ``_IDENTITY_SPEC``
        and still drive the legend + bulk-verb wording elsewhere."""
        rail = getattr(self, "_rail", None)
        if rail is None:
            return
        rail.setProperty("phase", self._identity_phase)
        rail.style().unpolish(rail)
        rail.style().polish(rail)

    def set_phase_identity(self, phase: str) -> None:
        """Override the identity-header phase (used by Quick Sweep hosts
        whose paths-mode call sites don't go through ``open_for_day``).
        Valid tokens: ``"collect" / "pick" / "edit" / "export"``."""
        if phase in self._IDENTITY_SPEC and phase != self._identity_phase:
            self._identity_phase = phase
            self._refresh_identity()

    # ── Public API (smoke / mock path — kept for test ergonomics) ──────

    def setDay(
        self,
        day_number: int,
        title: str,
        date_iso: str,
        items: list[GridItem],
    ) -> None:
        """Replace the day pill data + grid contents in one shot. Used
        by smokes / tests that synthesise :class:`GridItem` directly
        rather than going through ``open_for_day``."""
        self._event_id = None
        self._eg = None
        self._day_number = day_number
        self._day_title = title
        self._day_date = date_iso
        self._mode = "day"
        self._cluster = None
        self._day_items = list(items)
        self._items = list(items)
        self._update_counts()
        self._refresh()

    setItemsForPreview = setDay  # alias for smoke convenience

    def set_paths_mode_callbacks(
        self,
        state_lookup=None,
        day_rebuild=None,
    ) -> None:
        """Register the paths-mode (no-gateway) callbacks. The Quick
        Sweep host calls this once before `setDay` to plug:

        * ``state_lookup(path) -> "picked" | "skipped" | "compare" | None``
          — used by `_open_cluster` to paint sub-grid member cells
          from the QS ledger (cluster covers carry their own state on
          the GridItem, but the members don't until we look them up).
        * ``day_rebuild() -> list[GridItem]`` — called by
          `_close_cluster` so the cluster cover repaints with its
          fresh aggregate state after the user marked members inside.

        Both default to ``None`` (gateway-mode pages skip both
        paths). Pass ``None`` to clear when the QS session ends."""
        self._paths_state_lookup = state_lookup
        self._paths_day_rebuild = day_rebuild

    # ── Gateway → grid items ───────────────────────────────────────────

    def _exported_ids_for_grid(self) -> Optional[set]:
        """The shipped-item id set the corner exported badge reads from
        (spec/59 §8 + spec/66 §1.2). Returns ``None`` when the user has
        hidden the indicator (``show_exported_watermark = False``), so
        :func:`day_grid_cells` stamps no cells. Safe to call without a
        gateway — empty set as a fall-through."""
        if self._eg is None:
            return None
        try:
            settings = self.gateway.settings.load()
            if not bool(getattr(settings, "show_exported_watermark", True)):
                return None
        except Exception:                                          # noqa: BLE001
            log.exception("DaysGridPage: settings.load failed; "
                          "defaulting to show watermark")
        try:
            return self._eg.exported_item_ids()
        except Exception:                                          # noqa: BLE001
            log.exception("DaysGridPage: exported_item_ids failed")
            return set()

    def _edit_adjustments_for_grid(self) -> dict:
        """Per-item ``{item_id: Adjustment}`` for the day, used to colour the
        green/amber edit border and stamp the Look/Filter/Crop reason pill
        (Nelson 2026-06-18). The edit decoration is an Edit-phase signal, so
        it is computed ONLY on the Edit grid (Pick has nothing edited yet;
        Export shares the edit storage and keeps showing it). Empty
        otherwise. Safe without a gateway."""
        if self._eg is None or self._phase != "edit":
            return {}
        try:
            return self._eg.adjustments_for_day(self._day_number)
        except Exception:                                          # noqa: BLE001
            log.exception("DaysGridPage: adjustments_for_day failed")
            return {}

    def _refresh_from_gateway(self) -> None:
        """Rebuild ``self._day_items`` from the live gateway and render.

        Engine is :func:`day_grid_cells` (the spec/32 cell list); the
        page only maps the resulting :class:`CullCell` shape onto the
        Thumb-shaped :class:`GridItem` model.

        Edit-only filter (BUGS.md B-010, spec/66 §1.1): in pure Edit
        the grid surfaces ONLY the Pick survivors — the engine is
        scoped to that subset via ``item_ids``. Export-mode (which
        shares the ``edit`` storage but flips behaviour) keeps the
        full pool — that's a separate decision pass.

        Export-mode reshape (spec/56): a video cell that owns picked
        segments / snapshots becomes a "video" cluster cover; drilling
        in surfaces each member as its own cell with a "Video Clip" /
        "Snapshot" type stamp. Pick/Edit grids are NOT reshaped — the
        clustering is an Export-only presentation, not a model change.
        """
        if self._eg is None:
            return
        # BUGS.md B-010 — Edit grid pool is the Pick survivors (spec/66
        # §1.1 "Edit pool = all picked keepers"). Restrict the engine
        # via item_ids when the page is in pure Edit mode.
        # spec/89 §4.2 / Block 7 D1.B — Export's pool is picked keepers
        # UNION any item with a file under Exported Media/ (the latter
        # surfaces the "skipped in Pick but a third-party edit exists"
        # edge case so the user can drop the file or re-Pick).
        item_ids_filter = None
        shipped_ids: set = set()
        skipped_in_pick_ids: set = set()
        if self._phase == "edit" and not self._export_mode:
            item_ids_filter = self._picked_item_ids_filter()
        elif self._export_mode:
            item_ids_filter, shipped_ids, skipped_in_pick_ids = (
                self._export_pool_filter())
        cells = day_grid_cells(
            self._eg, self._day_number, phase=self._phase,
            default_state=self._phase_default,
            item_ids=item_ids_filter,
            # spec/59 §8 / spec/66 §1.2 — shipped items wear the
            # corner exported badge (the redesign replaced the legacy
            # diagonal in grids). Gated by the app-wide
            # ``show_exported_watermark`` setting; ``None`` stamps
            # nothing so the cells stay clean when the user hides
            # the indicator.
            exported_ids=self._exported_ids_for_grid(),
        )
        phase_states = self._eg.phase_states(self._phase)
        day_items = self._items_from_cells(cells, phase_states)
        if self._export_mode:
            # spec/89 §1.1 / Block 1 D1.C — per-item intent inference
            # for flat cells without an explicit phase_state(edit) row:
            # 0 versions on disk → red default · ≥1 version → green
            # default. Then stamp the "skipped in Pick" indicator on
            # items that only made it into the pool via shipped_ids
            # AND the Block 2 origin wordmark on cells with a single
            # ship row.
            shipped_rows_by_item = self._shipped_rows_by_item()
            # spec/89 Slice 5 (Nelson 2026-06-19) — Mira-edit intent
            # counts as a virtual version for the cluster threshold.
            # An item with non-default look/crop/filter AND one
            # third-party return on disk reads as two ship intents,
            # so the cluster surfaces both side-by-side for comparison
            # without forcing the user to render the Mira version
            # first.
            try:
                mira_intent_ids = self._eg.items_with_mira_intent()
            except Exception:                                      # noqa: BLE001
                log.exception(
                    "DaysGridPage: items_with_mira_intent failed")
                mira_intent_ids = set()
            from core.export_provenance import (
                cell_origin_label, stack_output_origin_label,
            )
            # spec/109 §5 — stack-output masters wear their bracket's
            # producer wordmark on the export strip when no ship row
            # is yet on disk (Mira-fused → ``Mira``, third-party-
            # stacker → ``ext``). Once an export ships, the lineage
            # row's wordmark takes over via :func:`cell_origin_label`.
            try:
                stack_producers = self._eg.stack_producers_by_output()
            except Exception:                                      # noqa: BLE001
                log.exception(
                    "DaysGridPage: stack_producers_by_output failed")
                stack_producers = {}
            for it in day_items:
                if it.item_kind == "cluster":
                    continue
                if phase_states.get(it.item_id) is None:
                    # spec/89 §1.1 (Nelson 2026-06-19 lock) — flat-cell
                    # default mirrors the gateway's per-source rule:
                    # ANY ship intent (lineage row OR Mira-edit
                    # intent) → Will export; 0 intents → Set aside.
                    # The earlier check only looked at lineage rows,
                    # which left Mira-edited-only photos painting red
                    # on the grid even though the Days List bar
                    # (phase_day_progress) was already counting them
                    # green. Both surfaces now agree.
                    has_shipped = it.item_id in shipped_ids
                    has_mira_intent = it.item_id in mira_intent_ids
                    it.state = (
                        STATE_PICKED if (has_shipped or has_mira_intent)
                        else STATE_SKIPPED)
                if it.item_id in skipped_in_pick_ids:
                    it.skipped_in_pick = True
                rows = shipped_rows_by_item.get(it.item_id, [])
                origin = cell_origin_label(rows)
                if origin is None and it.item_id in stack_producers:
                    origin = stack_output_origin_label(
                        stack_producers[it.item_id])
                it.origin = origin
            # spec/89 Slice 5 / Block 1 D2 — items with 2+ ship intents
            # (lineage rows on disk + the Mira-render intent) become a
            # versions cluster cover; the existing video reshape runs
            # afterwards so a video that ALSO has multiple export
            # versions of one of its snapshots is layered correctly
            # (versions clusters wrap individual photo items, video
            # clusters wrap the parent video).
            day_items = self._reshape_for_versions(
                day_items, shipped_rows_by_item, mira_intent_ids,
                phase_states)
            day_items = self._reshape_for_export(day_items, phase_states)
        elif self._phase == "edit":
            # BUGS.md B-010 — pure Edit is creative-only (spec/66 §1.1):
            # no picked/skipped/compare/mixed border. Strip the cell
            # state so the Thumb renders the neutral (no-border) chrome.
            for it in day_items:
                it.state = None
        self._day_items = day_items
        self._items = list(self._day_items)
        self._update_counts()
        self._refresh()

    def _reshape_for_versions(
        self,
        items,
        shipped_rows_by_item: dict,
        mira_intent_ids: set,
        phase_states: dict,
    ):
        """spec/89 Slice 5 / Block 1 D2 — replace any flat photo cell
        whose source carries ≥2 **ship intents** with a synthetic
        **versions cluster cover**. A ship intent is either:

        * one ``Exported Media/`` lineage row (a Mira render or a
          third-party return already on disk), or
        * the Mira-render intent itself — the source has a non-default
          ``adjustment`` row (look / crop / filter / rotation) the
          next Export run would materialise.

        So a source with one third-party return + a non-default Mira
        edit reads as TWO ship intents and joins a cluster, surfacing
        both side-by-side for comparison before the Mira render even
        materialises (Nelson 2026-06-19 eyeball — "they should be
        in a cluster"). Single-intent and zero-intent flat cells pass
        through unchanged.

        Member intent is read per-source:
        * Lineage members → ``lineage.intent_state`` (set by the
          scanner to ``'compare'`` on first sight).
        * The virtual Mira member → ``phase_state(edit, source)``.
          A missing row defaults to ``'compare'`` so the cluster
          presents the "needs your attention" signal on first form.

        The cover's border colour is derived per the Block 1 D3 state
        machine: any member still in compare → Compare orange; all
        picked → green; all skipped → red; mixed → yellow (Block 4
        D1.B). The cover thumbnail stays on the source item's thumb
        so the day grid is visually anchored (newest-version cover
        thumb is spec/89 §9 deferred polish)."""
        out = []
        for it in items:
            if it.item_kind != "photo":
                out.append(it)
                continue
            rows = shipped_rows_by_item.get(it.item_id, [])
            has_mira_intent = it.item_id in mira_intent_ids
            total_intents = len(rows) + (1 if has_mira_intent else 0)
            if total_intents < 2:
                out.append(it)
                continue
            cluster_item = self._versions_cluster_grid_item(
                it, rows, has_mira_intent, phase_states)
            out.append(cluster_item if cluster_item is not None else it)
        return out

    def _versions_cluster_grid_item(
        self,
        source_item,
        rows: list,
        has_mira_intent: bool,
        phase_states: dict,
    ):
        """Build a synthetic versions cluster cover for one source
        item with ≥2 ship intents (lineage rows on disk + the
        Mira-render intent). The virtual Mira member's ``item_id`` is
        ``mira:<source_id>`` so the per-version verb dispatcher in
        :meth:`_apply_version_verb_at_index` knows to write to
        ``phase_state(edit, source)`` instead of
        ``lineage.intent_state``. Lineage members keep their
        ``export_relpath`` as the id (unchanged from the pre-eyeball
        path)."""
        if not rows and not has_mira_intent:
            return None
        from mira.picked.model import CellColor as _CC, CullCluster, CullItem
        from pathlib import Path as _Path
        event_root = (
            _Path(self._eg.event_root) if self._eg and self._eg.event_root
            else _Path("."))
        members: list[CullItem] = []
        member_states: list[str] = []
        if has_mira_intent:
            # The virtual Mira member — backed by the source item's
            # phase_state(edit). Default 'candidate' (Compare orange)
            # when no row exists so a freshly-formed cluster reads as
            # "needs your attention." The source photo's path is the
            # preview source until the Mira render materialises.
            ps = phase_states.get(source_item.item_id)
            mira_state = (
                ps.state if ps is not None
                else "candidate")
            members.append(CullItem(
                item_id=f"mira:{source_item.item_id}",
                path=source_item._path or _Path(""),
                kind="photo",
            ))
            member_states.append(mira_state)
        for row in rows:
            path = event_root / row.export_relpath
            members.append(CullItem(
                item_id=row.export_relpath,
                path=path,
                kind="photo",
            ))
            member_states.append(
                getattr(row, "intent_state", "compare") or "compare")
        cover_color = self._versions_cover_color(member_states)
        cluster = CullCluster(
            bucket_key=f"versions:{source_item.item_id}",
            kind="versions",
            title="",
            members=tuple(members),
            color=cover_color,
        )
        # spec/89 §11.3 / Block 1 D5.A — cover thumb is the newest
        # version's actual file when the cluster carries lineage rows
        # (``versions_for_item`` returns newest-first per Slice 5).
        # The sha256 is cleared so the cache key falls to path:<rel>
        # — using the source's sha would mis-serve the source thumb
        # from the in-memory pixmap cache. The initial pixmap stays
        # the source thumb as a brief placeholder until the async
        # decoder paints the version's file.
        cover_path = source_item._path
        cover_sha = source_item._sha256
        if rows:
            cover_path = event_root / rows[0].export_relpath
            cover_sha = None
        # spec/118 §2 — cluster cover reads stale when ANY Mira-render
        # member is stale. The user sees the loud "edited" cue at the
        # day grid without drilling into the cluster first.
        from mira.ui.exported.staleness import is_cluster_cover_stale
        cover_stale = is_cluster_cover_stale(
            self._eg, source_item.item_id)
        return GridItem(
            item_id=f"cluster:versions:{source_item.item_id}",
            item_kind="cluster",
            pixmap=source_item.pixmap,
            state=_CELL_TO_THUMB_STATE.get(cover_color),
            visited=bool(source_item.visited),
            exported=False,
            cluster_type="versions",
            cluster_count=len(members),
            edited_since_export=cover_stale,
            _path=cover_path,
            _sha256=cover_sha,
            _cull_cluster=cluster,
        )

    @staticmethod
    def _versions_cover_color(member_states):
        """spec/89 Block 1 D3 — derive the cover border colour from the
        member intent_state list:

        * Any compare → Compare orange (the user still has unfinished
          decisions; cover paints "needs your attention").
        * All 'picked' → KEPT (green).
        * All 'skipped' → DISCARDED (red).
        * Mix of 'picked' + 'skipped' (no compare) → MIXED (yellow,
          distinct from Edit's amber per Block 4 D3.A).

        ``'candidate'`` is the persisted ``phase_state`` value for the
        Mira member's Compare reading; ``'compare'`` is the on-row
        wire value the lineage members carry. Both fold to Compare
        for cover-colour purposes."""
        from mira.picked.model import CellColor as _CC
        compare_states = {"compare", "candidate"}
        if not member_states:
            return _CC.UNTOUCHED
        if any(s in compare_states for s in member_states):
            return _CC.COMPARE
        if all(s == "picked" for s in member_states):
            return _CC.KEPT
        if all(s == "skipped" for s in member_states):
            return _CC.DISCARDED
        return _CC.MIXED

    def _shipped_rows_by_item(self) -> dict:
        """spec/89 §2.1 — group every ``Exported Media/`` lineage row by
        source item id. The badge layer reads this to pick a per-cell
        origin label (or to count versions for the cluster cover chip).
        Returns an empty dict when the gateway is unreachable."""
        out: dict = {}
        if self._eg is None:
            return out
        try:
            rows = self._eg.lineage()
        except Exception:                                          # noqa: BLE001
            log.exception("DaysGridPage: lineage() failed")
            return out
        for row in rows:
            if not row.export_relpath.startswith("Exported Media/"):
                continue
            if not row.source_item_id:
                continue
            out.setdefault(row.source_item_id, []).append(row)
        return out

    def _export_pool_filter(self):
        """spec/89 §4.2 / Block 7 D1.B — the Export grid's pool is
        **picked keepers ∪ any item with a file under Exported Media/**.
        The second term surfaces the "third-party return for an item I
        skipped in Pick" edge case so the user can either drop the
        file or re-Pick.

        Returns ``(item_ids_filter, shipped_ids, skipped_in_pick_ids)``:

        * ``item_ids_filter`` — frozenset of item ids the day-grid
          engine should include (``None`` when the gateway can't tell
          us, signalling "show everything in the day" as a defensive
          fallback).
        * ``shipped_ids`` — items with at least one ``Exported Media/``
          lineage row. The flat-cell intent inference uses this to
          decide a default-green vs default-red border (Block 1 D1.C).
        * ``skipped_in_pick_ids`` — items in the pool **only** because
          they have a ship row. The caller stamps the "skipped in Pick"
          indicator on them (Block 7 D2.B).
        """
        empty = (None, set(), set())
        if self._eg is None:
            return empty
        try:
            picked_filter = self._picked_item_ids_filter()
        except Exception:                                          # noqa: BLE001
            log.exception("DaysGridPage: pick keeper lookup failed")
            picked_filter = None
        try:
            shipped_ids = set(self._eg.exported_item_ids())
        except Exception:                                          # noqa: BLE001
            log.exception("DaysGridPage: exported_item_ids failed")
            shipped_ids = set()
        picked_set: set = set(picked_filter) if picked_filter is not None else set()
        pool = picked_set | shipped_ids
        skipped_in_pick_ids = shipped_ids - picked_set
        # If neither lookup yielded anything (fresh event, gateway hiccup)
        # fall back to the unfiltered day so the user sees the captures.
        if not pool and not shipped_ids and picked_filter is None:
            return empty
        return frozenset(pool), shipped_ids, skipped_in_pick_ids

    def _picked_item_ids_filter(self) -> Optional[frozenset]:
        """Return the frozenset of item ids the Edit grid should show
        (BUGS.md B-010 — spec/66 §1.1 says Edit's pool is "all picked
        keepers"). Reads the Pick phase ledger:

        * Items with an explicit ``phase_state(phase="pick").state ==
          "picked"`` are always in the pool.
        * Items with no Pick row count as picked only when the
          configured Pick default IS "picked" (spec locks default-Skip,
          but a power user could flip it). In that case the pool also
          includes every captured item whose row is missing — which we
          expand by walking the gateway's captured items once.

        Returns ``None`` if the gateway isn't open (caller falls back
        to the unfiltered day, same as Pick mode)."""
        if self._eg is None:
            return None
        try:
            pick_states = self._eg.phase_states("pick")
        except Exception:                                          # noqa: BLE001
            log.exception("DaysGridPage: phase_states('pick') failed")
            return None
        try:
            pick_default = default_state_for(
                self.gateway.settings, "pick")
        except Exception:                                          # noqa: BLE001
            log.exception("DaysGridPage: default_state_for(pick) failed")
            pick_default = STATE_SKIPPED
        explicit_picked = {
            iid for iid, ps in pick_states.items()
            if ps.state == STATE_PICKED
        }
        if pick_default != STATE_PICKED:
            return frozenset(explicit_picked)
        # Default-Pick mode: items with no row are also picked. Walk
        # the gateway's captured items once and add anything that
        # doesn't carry a non-picked explicit row.
        try:
            non_picked_explicit = {
                iid for iid, ps in pick_states.items()
                if ps.state != STATE_PICKED
            }
            implicit_picked = {
                it.id for it in self._eg.items()
                if (it.provenance == "captured"
                    and it.id not in non_picked_explicit)
            }
        except Exception:                                          # noqa: BLE001
            log.exception(
                "DaysGridPage: captured-items walk for Edit filter failed")
            return frozenset(explicit_picked)
        return frozenset(explicit_picked | implicit_picked)

    def _reshape_for_export(
        self,
        items: List["GridItem"],
        phase_states: Dict,
    ) -> List["GridItem"]:
        """Export-mode reshape (spec/56 + spec/89 Slice 9 Block 6):
        replace each flat video cell with a synthetic "video" cluster
        cover bundling its workshop-greened segments + snapshots.

        spec/89 §6.1 / Block 6 D3.B — a source video with **no picked
        segments AND no picked snapshots** drops out of the Export grid
        entirely; the flat-video fallback the pre-Slice-9 path used is
        gone. The user has to go back to the Workshop and green
        something to bring it back.

        Photos + scanner clusters pass through unchanged."""
        if self._eg is None:
            return items
        event_root = (
            Path(self._eg.event_root) if self._eg.event_root else Path("."))
        out: list[GridItem] = []
        for it in items:
            if it.item_kind != "video":
                out.append(it)
                continue
            cluster_item = self._video_cluster_grid_item(
                it, event_root, phase_states)
            if cluster_item is not None:
                out.append(cluster_item)
            # else: spec/89 Block 6 D3.B — drop the cell entirely; no
            # workshop-greened children means nothing to ship.
        return out

    def _video_cluster_grid_item(
        self,
        video_item: "GridItem",
        event_root: Path,
        phase_states: Dict,
    ) -> Optional["GridItem"]:
        """Build a synthetic "video" cluster cover for one source video,
        bundling its **workshop-greened** segment + snapshot child items
        as members (spec/89 Block 6 D1.C — only ``phase_state(edit) ==
        picked`` children are shown; workshop-skipped ones don't appear
        in Export at all). Returns ``None`` when no workshop-greened
        children exist (caller drops the cell per Block 6 D3.B).

        The cluster's ``bucket_key`` is ``video:<video_item_id>`` so the
        day-grid soft-state (and the existing visited / browsed
        plumbing) doesn't collide with scanner buckets."""
        if self._eg is None:
            return None
        video_id = video_item.item_id
        try:
            segs = self._eg.video_segments(video_id)
        except Exception:                                              # noqa: BLE001
            log.exception(
                "DaysGridPage: video_segments(%s) failed", video_id)
            segs = []
        try:
            snaps = self._eg.video_snapshots(video_id)
        except Exception:                                              # noqa: BLE001
            log.exception(
                "DaysGridPage: video_snapshots(%s) failed", video_id)
            snaps = []
        video_row = self._eg.item(video_id)
        video_path = (
            event_root / video_row.origin_relpath
            if (video_row and video_row.origin_relpath) else None
        )

        members: list[CullItem] = []
        member_states: list[str] = []
        for seg in segs:
            seg_item = self._eg.item(seg.item_id)
            if seg_item is None:
                continue
            ps = phase_states.get(seg.item_id)
            if ps is None or ps.state != STATE_PICKED:
                continue            # workshop-skipped → not shown
            members.append(CullItem(
                item_id=seg.item_id,
                path=video_path or Path(""),
                kind="video",
            ))
            member_states.append(STATE_PICKED)
        for snap in snaps:
            snap_item = self._eg.item(snap.item_id)
            if snap_item is None:
                continue
            ps = phase_states.get(snap.item_id)
            if ps is None or ps.state != STATE_PICKED:
                continue
            members.append(CullItem(
                item_id=snap.item_id,
                path=video_path or Path(""),
                kind="photo",
            ))
            member_states.append(STATE_PICKED)
        if not members:
            return None

        color = self._video_cover_color(member_states)
        cluster = CullCluster(
            bucket_key=f"video:{video_id}",
            kind="video",
            title="",
            members=tuple(members),
            color=color,
        )
        split = self._cluster_split_for(cluster, phase_states)
        return GridItem(
            item_id=f"cluster:video:{video_id}",
            item_kind="cluster",
            state=_CELL_TO_THUMB_STATE.get(color),
            visited=bool(video_item.visited),
            exported=False,
            cluster_type="video",
            cluster_count=len(members),
            cluster_split=split,
            _path=video_path,
            _sha256=video_item._sha256,
            _cull_cluster=cluster,
            _video_item_id=video_id,
        )

    @staticmethod
    def _video_cover_color(member_states):
        """spec/89 §6.3 / Block 6 — video cluster cover state machine.
        Same shape as the versions cluster but without the Compare leg
        (video members can never be Compare — they're flat green/red):

        * All ``picked`` → KEPT (green).
        * All ``skipped`` → DISCARDED (red).
        * Mix of ``picked`` + ``skipped`` → MIXED (yellow, distinct
          from Edit's amber per Block 4 D3.A).

        After Slice 9 the membership filter only admits ``picked``
        children, so a freshly-rebuilt cover starts green. The
        mixed/red branches paint when the user flips X on a member
        inside the cluster sub-grid before the next full
        :meth:`_refresh_from_gateway` re-runs the workshop-greened
        filter."""
        from mira.picked.model import CellColor as _CC
        if not member_states:
            return _CC.UNTOUCHED
        if all(s == STATE_PICKED for s in member_states):
            return _CC.KEPT
        if all(s == STATE_SKIPPED for s in member_states):
            return _CC.DISCARDED
        return _CC.MIXED

    def _items_from_cells(
        self,
        cells: List[CullCell],
        phase_states: Dict,
    ) -> List[GridItem]:
        """Translate a :class:`CullCell` list into :class:`GridItem`s.

        * Cluster cells become ONE cluster-cover GridItem keyed by the
          cluster's bucket_key; the cluster's first member supplies the
          cover thumbnail. The cluster_count / cluster_split chip on
          the cover comes straight from §5a.
        * Item cells (photo / video) become flat GridItems carrying
          the captured item's path + sha for the thumb loader.
        """
        if self._eg is None:
            return []
        event_root = Path(self._eg.event_root) if self._eg.event_root else Path(".")
        # Per-item adjustments for the green/amber edit border + the
        # Look/Filter/Crop reason pill — computed once per rebuild (Edit
        # grid only; empty elsewhere; Nelson 2026-06-18).
        from core.edit_status import edit_reasons as _edit_reasons
        adj_map = self._edit_adjustments_for_grid()
        # Pure Edit grid only — Export mode shares the 'edit' storage
        # phase but the cell border must show ship/drop intent (green
        # / red), NOT the edited-vs-unedited baseline (Nelson eyeball
        # 2026-06-19: every cell read amber on the user's Alaska event
        # because every photo carried an Adjustment row).
        is_edit_grid = self._phase == "edit" and not self._export_mode
        # spec/118 §2 — the loud "edited since export" badge fires only
        # on the Export grid (or any surface that wants to honour the
        # same truth). Resolved per-cell from the shared helper so the
        # logic stays one source of truth across preview + grid +
        # editor.
        from mira.ui.exported.staleness import is_cell_stale
        check_stale = bool(self._export_mode)
        out: list[GridItem] = []
        for cell in cells:
            if cell.is_cluster and cell.cluster is not None:
                out.append(self._cluster_grid_item(
                    cell, event_root, phase_states))
                continue
            if cell.item_id is None:
                continue
            it = self._eg.item(cell.item_id)
            if it is None or not it.origin_relpath:
                continue
            path = event_root / it.origin_relpath
            reasons = _edit_reasons(adj_map.get(cell.item_id))
            # Edit grid: every photo cell's border encodes edited (amber) vs
            # unedited (green). Other grids keep the decision-state border.
            border_token = (
                ("amber" if reasons else "green") if is_edit_grid else None)
            stale = (
                is_cell_stale(self._eg, cell.item_id)
                if check_stale else False
            )
            out.append(GridItem(
                item_id=cell.item_id,
                item_kind=cell.item_kind,
                state=_CELL_TO_THUMB_STATE.get(cell.color),
                visited=bool(cell.visited),
                exported=bool(cell.exported),
                edit_reasons=reasons,
                border_token=border_token,
                edit_tooltip=_edit_reason_tooltip(reasons),
                edited_since_export=stale,
                _path=path,
                _sha256=getattr(it, "sha256", None) or None,
            ))
        return out

    def _cluster_grid_item(
        self,
        cell: CullCell,
        event_root: Path,
        phase_states: Dict,
    ) -> GridItem:
        """Build a cluster-cover GridItem from the cluster :class:`CullCell`.

        The split chip (mixed clusters only) is computed honestly from
        per-member phase_state so the cover communicates "3 picked, 2
        skipped" rather than a Unicode placeholder (spec/65 §3.6 calls
        this out — the split chip never landed in a real day grid)."""
        cluster = cell.cluster
        # The cluster cover's preview pixmap is the first photo member.
        # That gives blurred-fill something real to extend from, per
        # spec/65 §2.3 ("blurred-fill never shines — smokes used
        # gradient placeholders"). Falls back to the first member if
        # the cluster is all-video (no photos).
        cover_path = None
        cover_sha = None
        for m in cluster.members:
            if m.kind == "photo":
                cover_path = m.path
                cover_sha = getattr(m, "sha256", None) or None
                break
        if cover_path is None and cluster.members:
            cover_path = cluster.members[0].path
            cover_sha = getattr(cluster.members[0], "sha256", None) or None
        cluster_type = _CLUSTER_KIND_TO_THUMB.get(cluster.kind)
        split = self._cluster_split_for(cluster, phase_states)
        item = GridItem(
            item_id=f"cluster:{cluster.bucket_key}",
            item_kind="cluster",
            state=_CELL_TO_THUMB_STATE.get(cell.color),
            visited=bool(cell.visited),
            exported=False,
            cluster_type=cluster_type,
            cluster_count=cluster.count,
            cluster_split=split,
            _path=cover_path,
            _sha256=cover_sha,
            _cull_cluster=cluster,
        )
        return item

    def _cluster_split_for(
        self,
        cluster: CullCluster,
        phase_states: Dict,
    ) -> Optional[Tuple[int, int]]:
        """Return ``(picked, skipped)`` for a MIXED cluster cover; None
        for clusters whose aggregate isn't mixed (the Thumb falls back
        to the ×N count chip)."""
        member_colors = [
            cell_color_for_item(
                m.item_id, m.kind, self._phase, phase_states,
                default_state=self._phase_default)
            for m in cluster.members
        ]
        agg = cluster_color(member_colors)
        if agg != CellColor.MIXED:
            return None
        picked = sum(1 for c in member_colors if c == CellColor.KEPT)
        skipped = sum(
            1 for c in member_colors
            if c in (CellColor.DISCARDED, CellColor.COMPARE)
        )
        return (picked, skipped)

    # ── Cluster expansion ──────────────────────────────────────────────

    def _open_cluster(self, cluster: CullCluster) -> None:
        """Drill into ``cluster`` — replace the grid contents with its
        members. Mark the cluster as browsed (spec/32 §2.10).

        Paths mode (standalone / wizard Quick Sweep — no gateway): the
        host's ``state_lookup`` callback colours each member; the
        bucket-browsed mark has no gateway target, so it is a no-op
        here. When no lookup is registered, members render in the
        Thumb's "no state" placeholder.

        Video clusters (spec/56 Export-mode synthetic): each member
        wears a "clip" / "snapshot" type stamp derived from the source
        item's provenance. The synthetic ``video:<id>`` bucket_key is
        NOT recorded as a browsed bucket — scanner soft-state stays
        clean of UI synthetics.

        Versions clusters (spec/89 Slice 5 synthetic): one source
        item's ≥2 ``Exported Media/`` lineage rows. Each member is a
        lineage row — ``item_id`` = the row's ``export_relpath`` so
        per-version verbs route directly via :meth:`set_lineage_intent`.
        Member state reads from ``lineage.intent_state``; the cover's
        ``versions:<id>`` bucket_key never lands in scanner soft-state.
        """
        is_video_cluster = (cluster.kind == "video")
        is_versions_cluster = (cluster.kind == "versions")
        if is_versions_cluster and self._eg is not None:
            self._open_versions_cluster(cluster)
            return
        if self._eg is None:
            members: list[GridItem] = []
            lookup = self._paths_state_lookup
            for ci in cluster.members:
                thumb_state = (
                    lookup(ci.path) if lookup is not None else None
                )
                members.append(GridItem(
                    item_id=ci.item_id,
                    item_kind=ci.kind,
                    state=thumb_state,
                    visited=False,
                    exported=False,
                    _path=ci.path,
                ))
            self._mode = "cluster"
            self._cluster = cluster
            self._items = members
            self._update_counts()
            self._refresh()
            return
        if not is_video_cluster:
            try:
                self._eg.set_bucket_browsed(cluster.bucket_key, self._phase)
            except Exception:                                      # noqa: BLE001
                log.exception(
                    "set_bucket_browsed failed for %s", cluster.bucket_key)
        # Rebuild items from the cluster members; the cluster cover's
        # ``visited`` flips True on its day-mode rebuild so the user
        # comes back to the right tick.
        phase_states = self._eg.phase_states(self._phase)
        event_root = (
            Path(self._eg.event_root) if self._eg.event_root else Path(".")
        )
        # BUGS.md B-010 — Edit is creative-only (spec/66 §1.1): cluster
        # members inherit the no-border treatment of the day-mode cells.
        edit_neutral = (self._phase == "edit" and not self._export_mode)
        # spec/66 §1.1: Edit's pool is "all picked keepers". The DAY grid
        # already filters via ``_picked_item_ids_filter`` in
        # ``_refresh_from_gateway``, but drilling into a cluster used to
        # iterate every member regardless of Pick state — so a bracket
        # with mixed picks showed its skipped frames too. Apply the same
        # filter here when the page is in pure Edit mode (Nelson
        # 2026-06-18, the recurring "Edit shows photos discarded in Pick"
        # complaint).
        edit_pool: Optional[frozenset] = None
        if edit_neutral:
            edit_pool = self._picked_item_ids_filter()
        members: list[GridItem] = []
        for ci in cluster.members:
            if edit_pool is not None and ci.item_id not in edit_pool:
                continue
            path = ci.path if ci.path.is_absolute() else event_root / ci.path
            if edit_neutral:
                thumb_state = None
            else:
                color = cell_color_for_item(
                    ci.item_id, ci.kind, self._phase, phase_states,
                    default_state=self._phase_default,
                )
                thumb_state = _CELL_TO_THUMB_STATE.get(color)
            stamp = None
            if is_video_cluster:
                # Map provenance → stamp so the type chip reads
                # "Video Clip" / "Snapshot" on every member.
                m_item = self._eg.item(ci.item_id)
                prov = getattr(m_item, "provenance", None) if m_item else None
                if prov == "clip":
                    stamp = "clip"
                elif prov == "snapshot":
                    stamp = "snapshot"
            members.append(GridItem(
                item_id=ci.item_id,
                item_kind=ci.kind,
                state=thumb_state,
                visited=False,
                exported=False,
                stamp=stamp,
                _path=path,
                _sha256=getattr(ci, "sha256", None) or None,
            ))
        self._mode = "cluster"
        self._cluster = cluster
        self._items = members
        self._update_counts()
        self._refresh()

    def _open_versions_cluster(self, cluster: CullCluster) -> None:
        """spec/89 Slice 5 — drill into a versions cluster. Surfaces
        every ship intent for the source: the **virtual Mira member**
        (if the source carries a non-default ``adjustment`` row — its
        item_id is ``mira:<source_id>``) AND every ``Exported Media/``
        lineage row. Each member shows its current intent state and
        the Block 2 origin wordmark."""
        from core.export_provenance import lineage_origin_label
        source_item_id = cluster.bucket_key.split(":", 1)[1] if ":" in cluster.bucket_key else ""
        # Re-read the rows so we see live intent_state writes since
        # the cluster was built (the cover's CullItems froze member
        # states at reshape time; the sub-grid needs the latest).
        try:
            rows = self._eg.versions_for_item(source_item_id)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "DaysGridPage: versions_for_item(%s) failed", source_item_id)
            rows = []
        # Mira intent re-read — adjustment row could have changed
        # since the cluster was built; phase_state(edit, source) carries
        # the per-member decision.
        try:
            mira_intent_ids = self._eg.items_with_mira_intent()
        except Exception:                                          # noqa: BLE001
            log.exception(
                "DaysGridPage: items_with_mira_intent (sub-grid) failed")
            mira_intent_ids = set()
        try:
            edit_phase_states = self._eg.phase_states("edit")
        except Exception:                                          # noqa: BLE001
            log.exception(
                "DaysGridPage: phase_states('edit') for sub-grid failed")
            edit_phase_states = {}
        members: list[GridItem] = []
        if source_item_id in mira_intent_ids:
            src_item = self._eg.item(source_item_id)
            src_path = (
                Path(self._eg.event_root) / src_item.origin_relpath
                if (src_item and src_item.origin_relpath
                    and self._eg.event_root)
                else None)
            ps = edit_phase_states.get(source_item_id)
            mira_state = (
                ps.state if ps is not None
                else "candidate")
            members.append(GridItem(
                item_id=f"mira:{source_item_id}",
                item_kind="photo",
                state=mira_state,
                visited=False,
                exported=False,
                origin="Mira",
                _path=src_path,
            ))
        from mira.ui.exported.staleness import is_lineage_row_stale
        for row in rows:
            path = Path(self._eg.event_root) / row.export_relpath if (
                self._eg.event_root) else Path(row.export_relpath)
            members.append(GridItem(
                item_id=row.export_relpath,
                item_kind="photo",
                state=row.intent_state,
                visited=False,
                exported=False,
                origin=lineage_origin_label(
                    row.provenance, row.export_relpath),
                edited_since_export=is_lineage_row_stale(self._eg, row),
                _path=path,
            ))
        self._mode = "cluster"
        self._cluster = cluster
        # spec/89 §11.3 — Compare button is sub-grid-only; reveal it on
        # entry (mirror of the _close_cluster recompute).
        self._apply_phase_chrome()
        self._items = members
        self._update_counts()
        self._refresh()

    def _close_cluster(self) -> None:
        """Return from the cluster sub-grid to the day grid."""
        if self._mode != "cluster":
            return
        self._mode = "day"
        self._cluster = None
        # spec/89 §11.3 — the Compare button is sub-grid-only; recompute
        # phase chrome so it hides on exit.
        self._apply_phase_chrome()
        # Rebuild the day items so the cluster cover repaints with its
        # new aggregate (members may have moved while inside) + the
        # visited tick now that the cluster has been browsed.
        if self._eg is not None:
            self._refresh_from_gateway()
        elif self._paths_day_rebuild is not None:
            # Paths mode (Quick Sweep) — ask the host for fresh day
            # GridItems so the cluster cover's aggregate state, count
            # and split chip reflect the latest QS ledger.
            self._day_items = list(self._paths_day_rebuild())
            self._items = list(self._day_items)
            self._update_counts()
            self._refresh()
        else:
            self._items = list(self._day_items)
            self._update_counts()
            self._refresh()

    # ── Cell access (the live grid cells, post-chunk-built) ───────────

    @property
    def _thumb_widgets(self) -> list[Thumb]:
        """The :class:`Thumb` widgets currently in the grid. Backed by
        :class:`ThumbGrid`'s chunked-construction cell list — readers
        always see the up-to-date snapshot (later batches add cells
        after :meth:`set_items` returns)."""
        return list(self._grid.cells())

    # ── Counts (toolbar progress block) ────────────────────────────────

    def _update_counts(self) -> None:
        """Recompute reviewed/total from the currently-displayed items."""
        self._total = len(self._items)
        self._reviewed = sum(
            1 for it in self._items
            if it.state in ("picked", "skipped", "compare", "mixed")
        )

    # ── Size slider ────────────────────────────────────────────────────

    def _on_size_slider_changed(self, value: int) -> None:
        """Resize every grid cell to the new width and re-derive height
        from the locked aspect ratio. The hit-test border zone scales
        with cell size automatically (see ``thumb_grid.BORDER_RATIO``)
        so the click grammar keeps working as the tile shrinks/grows."""
        width = max(_TILE_SIZE_MIN.width(),
                    min(_TILE_SIZE_MAX.width(), int(value)))
        height = int(round(width * _TILE_ASPECT))
        self._grid.set_cell_size(QSize(width, height))

    # ── Render ─────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        # Day navigator pill — update its label in place. Earlier the
        # page rebuilt the widget + deleteLater'd the old one, but
        # Qt's deferred deletion left the old pill painted under the
        # new one for a frame, which leaked into the screenshot smoke
        # (Nelson 2026-06-14).
        self._day_pill.set_day(
            self._day_number, self._day_title,
            self._day_date, self._total,
        )

        # Back now lives in the shared title bar (generic "‹ Back"); while a
        # cluster is open the title-bar Back closes it first, routed through
        # ``on_titlebar_back`` → ``_on_back_clicked``.

        # Review progress
        pct = (
            int(round(self._reviewed / self._total * 100))
            if self._total else 0
        )
        self._progress_label.setText(
            f"{self._reviewed} / {self._total} reviewed"
        )
        self._progress_bar.setValue(pct)
        self._progress_bar.setState(
            "done" if self._reviewed == self._total and self._total > 0
            else "prog" if self._reviewed > 0
            else None
        )

        # Stop any in-flight decode for old cells before rebuilding.
        self._thumb_pending.clear()

        # Build :class:`ThumbGridItem` payloads — every grid cell is a
        # GridItem-keyed Thumb on the shared widget. ``focusable=True``
        # so the locked §63 keys P/X/Space/C act on whichever Thumb the
        # user last clicked or Tab-walked to.
        grid_items: list[ThumbGridItem] = []
        for item in self._items:
            grid_items.append(ThumbGridItem(
                pixmap=item.pixmap,
                state=item.state,
                visited=item.visited,
                exported=item.exported,
                edit_reasons=item.edit_reasons,
                border_token=item.border_token,
                cluster_type=item.cluster_type,
                cluster_count=item.cluster_count,
                cluster_split=item.cluster_split,
                stamp=item.stamp,
                origin=item.origin,
                skipped_in_pick=item.skipped_in_pick,
                # spec/89 Slice 7 — Export-mode cells flip the
                # "Exported" stamp into a destructive cue.
                export_destructive_mode=bool(self._export_mode),
                edited_since_export=item.edited_since_export,
                payload=item.item_id,
                focusable=True,
                tooltip=item.edit_tooltip,
            ))
        self._grid.set_items(grid_items)
        # ``_thumb_widgets`` is a live view of the grid's Thumb cells
        # (chunked construction means more cells appear after the first
        # batch — the @property below proxies to the grid's current
        # cells() snapshot so every reader sees the up-to-date list).

        # Re-prime the thumbnail loader. Cells that already have a
        # cached pixmap render it instantly; the rest queue up for a
        # background decode. Photos go through the on-disk 256-px
        # thumb tier (~2 ms warm hits); cluster covers reuse the same
        # tier on the cover member's path.
        self._enqueue_thumbnails()

    # ── Click routing ──────────────────────────────────────────────────

    def _on_grid_cell_activated(self, index: int) -> None:
        """The shared :class:`ThumbGrid` emits ``cell_activated(index)``.
        Translate to the legacy ``(item_id, widget)`` signature the
        page's routing speaks."""
        if not (0 <= index < len(self._items)):
            return
        item_id = self._items[index].item_id
        cell = self._grid.cell_at(index)
        if cell is None:
            return
        self._on_thumb_clicked(item_id, cell)

    def _on_grid_cell_border_clicked(self, index: int) -> None:
        """Border-zone click (two-zone grammar, BUGS.md B-006): change the
        cell's status in place rather than drilling in.

        * Cluster covers have no state of their own — a border click
          expands them, same as a center click would.
        * Export mode: toggle green↔red (the locked Export grammar).
        * Pick: cycle Skip→Pick→Compare, matching the §63 single-photo
          border-click verb.
        * Edit: creative-only (spec/66 §1.1) — no per-cell decision,
          so the border click drills into the editor (BUGS.md B-010),
          same as a center click. The cell carries no picked/skipped
          state to cycle through.
        """
        if not (0 <= index < len(self._items)):
            return
        item = self._items[index]
        if item.item_kind == "cluster" and item._cull_cluster is not None:
            self._open_cluster(item._cull_cluster)
            return
        cell = self._grid.cell_at(index)
        if cell is not None:
            cell.setFocus(Qt.FocusReason.MouseFocusReason)
        # BUGS.md B-010 — pure Edit: border click is a drill-in (same
        # as the center click). Pick/Export keep the state-changing
        # verbs (cycle / toggle). The cluster-cover branch above
        # already short-circuited; here we know the cell is a single
        # photo or video, so the host's item_activated route opens the
        # editor for it.
        if self._phase == "edit" and not self._export_mode:
            # spec/131 — Edit-phase border-click drills in too; remember
            # the item for the host's fallback restore anchor.
            self._entry_anchor_item_id = item.item_id
            self.item_activated.emit(item.item_id)
            return
        self._apply_verb_at_index(
            index, "toggle" if self._export_mode else "cycle")

    def _on_thumb_clicked(self, item_id: str, widget: Thumb) -> None:
        """A click on a Thumb.

        * Cluster covers expand in place (all modes).
        * Pick / Edit modes: single items emit :sig:`item_activated`
          so the host (MainWindow) drills the user into the Picker /
          Editor.
        * Export mode (spec/89 §3.1 / Block 5 D1.A): center click on a
          flat cell opens the read-only preview viewer; the border
          zone keeps the locked-grammar ``toggle`` verb (handled in
          :meth:`_on_grid_cell_border_clicked`). Cluster covers drill
          in either way (Block 5 D3.A).
        """
        widget.setFocus(Qt.FocusReason.MouseFocusReason)
        item = self._find_item(item_id)
        if item is None:
            return
        if item.item_kind == "cluster" and item._cull_cluster is not None:
            self._open_cluster(item._cull_cluster)
            return
        if self._export_mode:
            # spec/89 §3.1 / Block 5 D1.A — center click on a flat cell
            # opens the preview viewer instead of toggling. The border
            # zone (caught earlier by the two-zone splitter) still does
            # the toggle.
            self._open_export_preview(item)
            return
        # spec/131 — remember which item the user dove into; the host
        # uses this as a fallback restore anchor if the viewer reports
        # no current item on close.
        self._entry_anchor_item_id = item_id
        self.item_activated.emit(item_id)

    def _is_preview_item_stale(self, item) -> bool:
        """spec/89 §11.3 polish / spec/118 §2 — true when the focused
        cell has a Mira render on disk whose recorded ``recipe_json`` no
        longer matches what the live :class:`Adjustment` would emit.
        Drives the "Adjustments changed — Export to refresh" chip in the
        preview viewer AND the grid cell's loud "Edited" badge.

        Thin wrapper over
        :func:`mira.ui.exported.staleness.is_cell_stale` so the same
        truth feeds preview + grid + the Editor's exported badge."""
        from mira.ui.exported.staleness import is_cell_stale
        return is_cell_stale(self._eg, item.item_id)

    def _preview_develop_kwargs(self, item, path) -> dict:
        """spec/89 §11.3 polish — decide whether the preview viewer
        should pipe ``path`` through Mira's develop pipeline (so the
        user sees the would-be-shipped pixels) or read it raw from
        disk. Returns kwargs ready to splat onto :class:`PreviewItem`.

        Develop pipeline fires when:
        * **Day-grid 0-version cell** — no shipped Mira render yet,
          so the source photo passes through ``develop_photo_array``
          using the live Adjustment row to preview the next Export
          result. ``path`` is the source photo's path (per
          :meth:`_resolve_preview_path` rule 3).
        * **Virtual Mira cluster member** (``item_id`` starts with
          ``"mira:"``) — same shape; the on-disk Mira render doesn't
          exist yet.

        Skipped for cells whose ``path`` already points at an
        ``Exported Media/`` file (versions sub-grid members, shipped
        Mira renders) — the file IS the answer, the pipeline would
        re-render the same recipe at preview cost for nothing.
        """
        empty: dict = {"develop_for_preview": False}
        if self._eg is None:
            return empty
        try:
            event_root = (
                Path(self._eg.event_root) if self._eg.event_root else None)
            rel = (
                str(path).replace("\\", "/")
                if event_root is not None else "")
            event_rel_prefix = str(event_root).replace("\\", "/") + "/" if event_root else ""
            if event_rel_prefix and rel.startswith(event_rel_prefix):
                rel_after = rel[len(event_rel_prefix):]
                if rel_after.startswith("Exported Media/"):
                    return empty
        except Exception:                                          # noqa: BLE001
            log.debug(
                "preview-develop: prefix check failed for %s", path,
                exc_info=True)
        # Resolve the source item id (Mira-virtual members carry
        # ``mira:<source_id>`` as their cell id; everything else is
        # already a source id).
        iid = item.item_id
        source_id = (
            iid.split(":", 1)[1] if isinstance(iid, str)
            and iid.startswith("mira:") else iid
        )
        try:
            adj = self._eg.adjustment(source_id)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "preview-develop: adjustment(%s) failed", source_id)
            return empty
        return {
            "develop_for_preview": True,
            "develop_adjustment": adj,
        }

    def _open_export_preview(self, item) -> None:
        """spec/89 §3.2 / Slice 6 — open the read-only preview viewer
        for an Export-mode cell. Neighbours come from the current
        surface (Block 5 D1b.A — stepping stays within the surface):
        the flat day-grid view steps across flat cells, the versions
        sub-grid steps across the cluster's members.

        Click → preview content per §3.2:
        * 1-version Mira / third-party cell → the actual file on disk
          under ``Exported Media/``.
        * 0-version cell → the source photo on disk (Original Media/…)
          — the live Mira-develop preview is a later polish pass.
        * Versions sub-grid member → the version's file (the cell's
          ``item_id`` is the row's ``export_relpath``).

        Wires P / X / Space to the existing verb path; ``open_editor``
        / ``export_this`` route through ``item_activated`` and a TODO
        stub respectively (the batch + single-item run land in Slice 8).
        """
        from mira.ui.exported.preview_dialog import (
            ExportPreviewDialog, PreviewItem,
        )
        # Build the neighbour list from the current surface — flat day
        # grid OR versions sub-grid. Skip cluster covers (they would
        # drill in, not preview).
        neighbours: list = []
        start_idx = 0
        for it in self._items:
            if it.item_kind == "cluster":
                continue
            path = self._resolve_preview_path(it)
            if path is None:
                continue
            neighbours.append((it, path))
        for i, (it, _path) in enumerate(neighbours):
            if it.item_id == item.item_id:
                start_idx = i
                break
        if not neighbours:
            return
        preview_items = [
            PreviewItem(
                item_id=it.item_id,
                path=path,
                state=it.state,
                has_shipped_file=bool(it.exported),
                title=it.item_id.split("/")[-1] if "/" in it.item_id else "",
                is_stale=self._is_preview_item_stale(it),
                **self._preview_develop_kwargs(it, path),
            )
            for it, path in neighbours
        ]
        dlg = ExportPreviewDialog(
            preview_items, start_index=start_idx, parent=self)
        dlg.intent_pick_requested.connect(
            lambda iid: self._on_preview_intent(dlg, iid, "pick"))
        dlg.intent_skip_requested.connect(
            lambda iid: self._on_preview_intent(dlg, iid, "skip"))
        dlg.intent_toggle_requested.connect(
            lambda iid: self._on_preview_intent(dlg, iid, "toggle"))
        dlg.open_editor_requested.connect(
            lambda iid: self._on_preview_open_editor(dlg, iid))
        dlg.export_this_requested.connect(
            lambda iid: self._on_preview_export_this(dlg, iid))
        # Stored so tests can introspect; the headless-mode flag lets
        # them skip the modal exec so simulating a click doesn't block.
        self._last_preview_dialog = dlg
        if not getattr(self, "_preview_headless", False):
            dlg.exec()

    def _resolve_preview_path(self, item) -> Optional[Path]:
        """Pick the file the preview viewer should display for ``item``
        — spec/89 §3.2 mapping per cell type.

        Order of preference (Nelson 2026-06-19 corrected):
        1. **Versions sub-grid member** (``item_id`` starts with
           ``Exported Media/``) → ``item._path`` is already the
           lineage row's file. Use it.
        2. **Flat cell with a shipped lineage row** → the newest
           lineage row's file under ``Exported Media/`` (matches
           §3.2's "1-version third-party return" / "1-version
           Mira-rendered cell" rules — pre-fix this returned the
           source photo even when a third-party export existed, so
           the preview never showed the actual export).
        3. **Mira-virtual member** (``item_id`` starts with ``mira:``)
           OR **0-version flat cell** → the source photo under
           ``Original Media/`` so :meth:`_preview_develop_kwargs`'s
           develop-pipeline path can fire on it.
        4. Fall back to whatever ``item._path`` carries (defensive).
        """
        iid = item.item_id
        # Rule 1 — versions sub-grid member.
        if (isinstance(iid, str)
                and iid.startswith("Exported Media/")
                and item._path is not None
                and Path(item._path).is_file()):
            return Path(item._path)
        if self._eg is None or self._eg.event_root is None:
            return Path(item._path) if item._path else None
        event_root = Path(self._eg.event_root)
        # Mira-virtual members don't have a lineage row of their own;
        # the source path is what we want for the develop pipeline.
        is_mira_virtual = (
            isinstance(iid, str) and iid.startswith("mira:"))
        # Rule 2 — flat cell, prefer the shipped lineage row over the
        # source. Skipped for Mira-virtual members.
        if not is_mira_virtual:
            try:
                rows = self._eg.versions_for_item(iid)
            except Exception:                                      # noqa: BLE001
                log.exception(
                    "DaysGridPage: versions_for_item failed for %s",
                    iid)
                rows = []
            if rows:
                lineage_path = event_root / rows[0].export_relpath
                if lineage_path.is_file():
                    return lineage_path
        # Rule 3 — fall back to the source photo for 0-version cells +
        # Mira-virtual members (develop pipeline runs on top).
        source_id = (
            iid.split(":", 1)[1] if is_mira_virtual else iid)
        try:
            src_item = self._eg.item(source_id)
        except Exception:                                          # noqa: BLE001
            src_item = None
        if src_item and src_item.origin_relpath:
            return event_root / src_item.origin_relpath
        # Rule 4 — defensive fallback to whatever the GridItem carries.
        if item._path is not None and Path(item._path).is_file():
            return Path(item._path)
        return None

    def _on_preview_intent(self, dlg, item_id: str, verb: str) -> None:
        """Find the cell + apply the verb through the existing path.
        The preview viewer doesn't reach into the gateway itself —
        it just emits the intent, and we route it the same way a
        keyboard verb would route from the grid."""
        idx = next(
            (i for i, it in enumerate(self._items)
             if it.item_id == item_id), -1)
        if idx < 0:
            return
        self._apply_verb_at_index(idx, verb)
        # Push the post-verb state back to the dialog so its chrome
        # (state chip + Export-this enablement) re-reads without the
        # user needing to close + reopen.
        new_state = self._items[idx].state
        dlg.set_intent_state(item_id, new_state or "")

    def _on_preview_open_editor(self, dlg, item_id: str) -> None:
        """spec/89 §3.2 D4.C — "Open in Editor" button. Closes the
        preview and emits ``item_activated`` so MainWindow routes
        the user to the Editor for last-minute tone / crop tweaks."""
        dlg.accept()
        # For versions sub-grid members the item_id is the
        # export_relpath, not a source item; fall back to the cluster's
        # source item id so the Editor opens something it can render.
        target_id = item_id
        if (self._mode == "cluster" and self._cluster is not None
                and self._cluster.kind == "versions"
                and ":" in self._cluster.bucket_key):
            target_id = self._cluster.bucket_key.split(":", 1)[1]
        self.item_activated.emit(target_id)

    def _on_compare_versions(self) -> None:
        """spec/89 §11.3 — open the versions cluster sub-grid's
        side-by-side compare dialog. Builds a
        :class:`CompareItem` per visible member (carrying the same
        develop kwargs as the preview viewer so virtual Mira members
        render through the develop pipeline), opens
        :class:`CompareVersionsDialog`, routes border-click toggles
        back through the existing per-version verb path
        (:meth:`_apply_verb_at_index` → set_lineage_intent or
        set_phase_state for the Mira member).

        Gated by the toolbar visibility rule — the button only shows
        in cluster mode + versions kind — so a stray call outside that
        mode is a no-op."""
        if (self._mode != "cluster" or self._cluster is None
                or getattr(self._cluster, "kind", "") != "versions"):
            return
        if not self._items:
            return
        from mira.ui.exported.compare_dialog import (
            CompareItem, CompareVersionsDialog,
        )
        compare_items: list = []
        for it in self._items:
            if it.item_kind != "photo":
                continue
            path = self._resolve_preview_path(it)
            if path is None:
                continue
            title = getattr(it, "origin", "") or (
                it.item_id.split("/")[-1] if "/" in it.item_id
                else it.item_id)
            develop_kwargs = self._preview_develop_kwargs(it, path)
            compare_items.append(CompareItem(
                item_id=it.item_id,
                path=path,
                state=it.state,
                title=title,
                develop_for_preview=develop_kwargs.get(
                    "develop_for_preview", False),
                develop_adjustment=develop_kwargs.get(
                    "develop_adjustment"),
            ))
        if not compare_items:
            return
        dlg = CompareVersionsDialog(compare_items, parent=self)
        dlg.intent_toggle_requested.connect(
            lambda iid: self._on_compare_toggle(dlg, iid))
        dlg.intent_pick_requested.connect(
            lambda iid: self._on_compare_intent(dlg, iid, "pick"))
        dlg.intent_skip_requested.connect(
            lambda iid: self._on_compare_intent(dlg, iid, "skip"))
        self._last_compare_dialog = dlg
        if not getattr(self, "_compare_headless", False):
            dlg.exec()

    def _on_compare_toggle(self, dlg, item_id: str) -> None:
        """Route a compare-tile border click through the existing
        per-version verb path. Inside a versions sub-grid
        :meth:`_apply_verb_at_index` dispatches to
        :meth:`_apply_version_verb_at_index` which writes
        ``lineage.intent_state`` (for lineage members) or
        ``phase_state(edit, source)`` (for the virtual Mira member)."""
        idx = next(
            (i for i, it in enumerate(self._items)
             if it.item_id == item_id), -1)
        if idx < 0:
            return
        self._apply_verb_at_index(idx, "toggle")
        new_state = self._items[idx].state
        dlg.set_intent_state(item_id, new_state or "")

    def _on_compare_intent(
        self, dlg, item_id: str, verb: str,
    ) -> None:
        """Twin of :meth:`_on_compare_toggle` for explicit P / X (not
        bound to a tile click in the current UI, but available so a
        future keyboard / button path can reuse the routing)."""
        idx = next(
            (i for i, it in enumerate(self._items)
             if it.item_id == item_id), -1)
        if idx < 0:
            return
        self._apply_verb_at_index(idx, verb)
        new_state = self._items[idx].state
        dlg.set_intent_state(item_id, new_state or "")

    def _on_preview_export_this(self, dlg, item_id: str) -> None:
        """spec/89 §5.2 — single-item Export run trigger from the
        preview viewer's "Export this" button. Submits one item to the
        spec/60 batch engine via the same :func:`submit_export_batch`
        the bulk Export now uses.

        Re-render-ask (D6.C): if the source item already carries a
        Mira-render lineage row, ask "An export already exists. Re-
        render with current settings?" before submitting. Third-party
        returns alone never trigger the prompt — they have no recipe to
        re-render, and a fresh Mira render lands as a *new* version
        alongside them under the spec/54 §8 versions-as-exports policy
        (Block 1 D1.A — multi-version cells live together).

        Disabled-when-red contract (D5.A) is enforced at the button
        level inside :class:`ExportPreviewDialog`; this handler is
        defensive about the cell still being green at submit time."""
        from mira.ui.exported.batch import (
            ExportCell, day_label_for, submit_export_batch,
        )

        if self._eg is None or self._eg.event_root is None:
            dlg.accept()
            return

        # In versions sub-grid mode the preview's item_id is a lineage
        # relpath, not a source item. Walk back to the cluster's source
        # item so we render fresh against the original.
        source_item_id = item_id
        if (self._mode == "cluster" and self._cluster is not None
                and self._cluster.kind == "versions"
                and ":" in self._cluster.bucket_key):
            source_item_id = self._cluster.bucket_key.split(":", 1)[1]

        src_item = None
        try:
            src_item = self._eg.item(source_item_id)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "Export this: item(%s) failed", source_item_id)
        if src_item is None or not src_item.origin_relpath:
            log.warning(
                "Export this: no source item / origin path for %s",
                source_item_id)
            dlg.accept()
            return

        event_root = Path(self._eg.event_root)
        source_path = event_root / src_item.origin_relpath
        if not source_path.is_file():
            show_error(
                self,
                tr("Source missing"),
                tr(
                    "The original file isn't on disk — Mira can't "
                    "render this item.\n\n{path}"
                ).replace("{path}", str(source_path)),
            )
            dlg.accept()
            return

        # spec/118 §3 — when a Mira render already exists for this
        # item, ask the LRC-style three-way: Overwrite (replace in
        # place, same lineage row + path → any Cut containing it just
        # sees fresh pixels) / Keep both (today's UNIQUE default; a
        # "(2)" file lands as a new version → the Cut now shows BOTH
        # until re-picked) / Cancel.
        try:
            versions = self._eg.versions_for_item(source_item_id)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "Export this: versions_for_item(%s) failed",
                source_item_id)
            versions = []
        has_mira_render = any(
            (getattr(v, "provenance", "") or "") == "mira_render"
            for v in versions
        )
        collision = "unique"
        existing_mira_row = next(
            (v for v in versions
             if (getattr(v, "provenance", "") or "") == "mira_render"),
            None,
        )
        if has_mira_render:
            from mira.ui.exported.collision_dialog import (
                ask_overwrite_or_keep_both,
            )
            choice = ask_overwrite_or_keep_both(self)
            if choice is None:
                return
            collision = choice

        batch_queue = getattr(self.window(), "batch_queue", None)
        if batch_queue is None:
            show_error(
                self,
                tr("Batch queue unavailable"),
                tr(
                    "The app's batch queue isn't reachable — try "
                    "restarting Mira."),
            )
            return

        day_number = getattr(src_item, "day_number", None) or self._day_number
        day_labels = {day_number: day_label_for(self._eg, day_number)}
        # spec/118 §3 — OVERRIDE must land at the EXACT existing
        # ``export_relpath`` so the lineage row upserts in place and
        # any Cut referencing it keeps its membership (the file's
        # identity is unchanged). Pin via ``dest_dir_override`` to the
        # existing row's parent, robust against trip-day description
        # drift since the last export.
        dest_override = None
        if collision == "override" and existing_mira_row is not None:
            dest_override = Path(
                existing_mira_row.export_relpath).parent.as_posix()
        cell = ExportCell(
            item_id=source_item_id, path=source_path,
            day_number=day_number,
            dest_dir_override=dest_override,
        )
        try:
            submit_export_batch(
                self._eg, self.gateway.settings, batch_queue,
                event_name=self._event_name,
                cells=[cell],
                day_labels=day_labels,
                parent_widget=self,
                collision=collision,
            )
        except Exception as exc:                                   # noqa: BLE001
            log.exception("Export this: submit failed for %s",
                          source_item_id)
            show_error(
                self,
                tr("Could not start the export"),
                tr("The batch could not be queued.\n\n{err}")
                .replace("{err}", str(exc)),
            )
            return
        dlg.accept()

    def _on_back_clicked(self) -> None:
        if self._mode == "cluster":
            self._close_cluster()
            return
        self.back_requested.emit()

    def on_titlebar_back(self) -> None:
        """Shared title-bar Back hook (MainWindow._on_titlebar_back prefers
        this over the raw ``back_requested`` signal). Mode-aware: closes an
        open cluster sub-grid before backing out of the surface."""
        self._on_back_clicked()

    # ── Locked keymap (spec/63 §4) ────────────────────────────────────

    def keyPressEvent(self, ev: QKeyEvent) -> None:  # noqa: N802 — Qt
        """Locked §4 keymap. Events bubble up from the focused Thumb
        when no Thumb captures them (Thumb has no keyPressEvent
        override). The verb applies to the currently focused Thumb.

        Ctrl+Z (spec/63 §4) undoes the most recent decision on this
        page — a phase_state flip restores the previous state; an
        Export-mode X-on-shipped (the silent file unlink) restores
        the on-disk JPEG + its lineage row + the ``edit_exported``
        flag from the in-memory snapshot captured at the verb.

        BUGS.md B-010 — pure Edit (spec/66 §1.1 creative-only) takes no
        P/X/Space/C verbs: there is no per-cell decision on the Edit
        grid. Esc + Ctrl+Z still work."""
        key = ev.key()
        mods = ev.modifiers()
        if key == Qt.Key.Key_Escape:
            if self._mode == "cluster":
                self._close_cluster()
            else:
                self.back_requested.emit()
            ev.accept()
            return
        if (key == Qt.Key.Key_Z
                and mods & Qt.KeyboardModifier.ControlModifier):
            if self._undo_last_decision():
                ev.accept()
                return
        is_edit_creative = (
            self._phase == "edit" and not self._export_mode)
        if not is_edit_creative:
            if key == Qt.Key.Key_P:
                if self._verb_on_focused("pick"):
                    ev.accept()
                    return
            if key == Qt.Key.Key_X:
                if self._verb_on_focused("skip"):
                    ev.accept()
                    return
            if key == Qt.Key.Key_Space:
                if self._verb_on_focused("toggle"):
                    ev.accept()
                    return
            if key == Qt.Key.Key_C:
                if self._verb_on_focused("cycle"):
                    ev.accept()
                    return
        super().keyPressEvent(ev)

    def _verb_on_focused(self, verb: str) -> bool:
        """Apply a §4 verb to the currently focused Thumb. Returns
        ``True`` if a verb was applied (so the caller can accept the
        event), ``False`` otherwise. Used by :meth:`keyPressEvent`
        (P/X/Space/C); the click handler bypasses this and calls
        :meth:`_apply_verb_at_index` directly with the widget's known
        index."""
        idx = self._focused_thumb_index()
        if idx is None:
            return False
        return self._apply_verb_at_index(idx, verb)

    # ── Undo stack (spec/63 §4 Ctrl+Z) ────────────────────────────────

    def _push_undo(self, entry: "_UndoEntry") -> None:
        """Append ``entry`` to the page's undo stack, evicting the
        oldest if the cap is reached so the captured JPEG bytes can't
        grow unbounded."""
        self._undo_stack.append(entry)
        while len(self._undo_stack) > _UNDO_MAX:
            self._undo_stack.pop(0)

    def _pop_most_recent_unexport(
        self, item_id: str,
    ) -> "Optional[_UndoEntry]":
        """Find + remove the most recent un-export entry for
        ``item_id``. Used by re-press-P so a stray X is trivially
        recoverable: pressing P after X on a shipped cell restores
        everything (file + lineage + flag) without the user needing
        to know about Ctrl+Z. Returns ``None`` if no un-export entry
        for that item is in the stack."""
        for i in range(len(self._undo_stack) - 1, -1, -1):
            e = self._undo_stack[i]
            if e.item_id == item_id and e.snapshots:
                return self._undo_stack.pop(i)
        return None

    def _capture_unexport(
        self, item_id: str,
    ) -> list["_UnexportSnapshot"]:
        """Snapshot every ``Exported Media/`` lineage row + on-disk
        file for ``item_id`` so a later restore can put both back.

        Reads happen BEFORE the caller invokes
        :meth:`EventGateway.delete_exported_file`, which is the only
        way to capture the bytes — once that helper runs the file is
        gone. Empty list when no shipped rows exist (a no-op snapshot
        wraps a no-op delete so the verb path stays branch-free at
        the call site)."""
        if self._eg is None or self._eg.event_root is None:
            return []
        snapshots: list[_UnexportSnapshot] = []
        try:
            # spec/61 §1.2 / spec/66 §1.2 — the same WHERE
            # delete_exported_file uses, so the capture/delete pair
            # operates on the same row set.
            rows = self._eg.store.conn.execute(
                "SELECT * FROM lineage "
                "WHERE phase = 'edit' AND source_item_id = ? "
                "AND export_relpath LIKE 'Exported Media/%'",
                (item_id,),
            ).fetchall()
        except Exception:                                          # noqa: BLE001
            log.exception(
                "DaysGridPage: capture lineage for %s failed", item_id)
            return []
        from mira.store import models as _m
        from mira.store.repo import _BY_CLS
        info = _BY_CLS.get(_m.Lineage)
        event_root = Path(self._eg.event_root)
        for r in rows:
            rel = r["export_relpath"]
            abs_path = event_root / rel
            try:
                file_bytes = (
                    abs_path.read_bytes() if abs_path.is_file() else b"")
            except OSError:
                log.exception(
                    "DaysGridPage: read %s for undo failed", abs_path)
                file_bytes = b""
            try:
                lineage_row = (
                    self._eg.store._row_to_obj(r, info)
                    if info is not None else None)
            except Exception:                                      # noqa: BLE001
                log.exception(
                    "DaysGridPage: row_to_obj for %s failed", rel)
                lineage_row = None
            if lineage_row is None:
                # Without the row we can't restore the lineage; skip
                # the snapshot rather than save half-state.
                continue
            snapshots.append(_UnexportSnapshot(
                item_id=item_id, file_bytes=file_bytes,
                dest_path=abs_path, lineage_row=lineage_row,
            ))
        return snapshots

    def _restore_unexport(self, snapshots: list) -> None:
        """Replay a captured un-export: rewrite the file bytes, re-
        insert the lineage row, set ``edit_exported = True``. Each
        snapshot is restored independently — a partial failure on
        one doesn't abort the others."""
        if self._eg is None:
            return
        for snap in snapshots:
            try:
                snap.dest_path.parent.mkdir(parents=True, exist_ok=True)
                if snap.file_bytes:
                    snap.dest_path.write_bytes(snap.file_bytes)
            except OSError:
                log.exception(
                    "DaysGridPage: restore write %s failed",
                    snap.dest_path)
            try:
                self._eg.record_lineage(snap.lineage_row)
            except Exception:                                      # noqa: BLE001
                log.exception(
                    "DaysGridPage: restore record_lineage for %s "
                    "failed", snap.item_id)
            try:
                self._eg.set_edit_exported(snap.item_id, True)
            except Exception:                                      # noqa: BLE001
                log.exception(
                    "DaysGridPage: restore set_edit_exported for %s "
                    "failed", snap.item_id)

    def _undo_last_decision(self) -> bool:
        """Ctrl+Z — pop and apply the most recent undo entry. Returns
        ``True`` if anything was undone (so the key handler can
        accept the event)."""
        if not self._undo_stack or self._eg is None:
            return False
        entry = self._undo_stack.pop()
        self._apply_undo_entry(entry)
        return True

    def _apply_undo_entry(self, entry: "_UndoEntry") -> None:
        """Restore phase_state + (when present) the on-disk file +
        lineage row, then refresh the cell's chrome."""
        idx = None
        for i, it in enumerate(self._items):
            if it.item_id == entry.item_id:
                idx = i
                break
        if idx is None:
            return
        item = self._items[idx]
        # phase_state restoration — write prev explicitly. If the
        # original was ``None`` (born-default), write the phase
        # default so the cell reads the same as before the verb.
        restore_state = entry.prev_state or self._phase_default
        try:
            self._eg.set_phase_state(
                entry.item_id, self._phase, restore_state)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "_apply_undo_entry: set_phase_state(%s) failed",
                entry.item_id)
        item.state = restore_state
        self._thumb_widgets[idx].setState(restore_state)
        # Export-side restoration when this entry carried a file.
        if entry.snapshots:
            self._restore_unexport(entry.snapshots)
            item.exported = True
            self._thumb_widgets[idx].setExported(True)
        self._update_counts()

    def _apply_verb_at_index(self, idx: int, verb: str) -> bool:
        """Apply a §4 verb to the cell at ``idx``. Returns ``True`` if
        a verb landed (so a caller using this from keyPressEvent can
        accept the event), ``False`` otherwise.

        In Export mode (spec/68 §3) the verb's effect on the in-place
        cell state is the same, but a landing on red (skipped) for a
        cell that is already shipped also calls
        :meth:`EventGateway.delete_exported_file` — the file under
        ``Exported Media/`` unlinks, its lineage row drops, the
        ``edit_exported`` flag clears, and Cut membership cascades
        (spec/61 §1.4). This is the "delete exported file" affordance
        the spec asks for — no separate UI, the existing X grammar
        carries it (spec/68 §3 second bullet).
        """
        if idx < 0 or idx >= len(self._items):
            return False
        item = self._items[idx]
        if item.item_kind == "cluster":
            # Cluster covers don't accept per-cell verbs — the user
            # drills in and acts on members. Bulk Pick-all/Skip-all
            # on the toolbar handles the all-cluster case (and would
            # cover this cluster's members along with everything else
            # at the day level).
            return False
        if self._eg is None:
            return False
        # spec/89 Slice 5 — when the user is inside a versions cluster
        # sub-grid, each cell IS a lineage row (item_id = the row's
        # export_relpath). Route P/X/Space to set_lineage_intent so the
        # per-version decision lands on lineage.intent_state rather
        # than the item-level phase_state.
        if (self._mode == "cluster" and self._cluster is not None
                and self._cluster.kind == "versions"):
            return self._apply_version_verb_at_index(idx, verb)
        cur_state = item.state  # "picked" | "skipped" | "compare" | None
        new_state = self._next_state(item.item_kind, cur_state, verb)
        if new_state is None or new_state == cur_state:
            return True
        # spec/63 §4 + spec/68 §3 — re-press-P quick-recover: if the
        # user pressed P (or otherwise lands on green) on a cell
        # whose most recent decision was an X-on-shipped un-export,
        # the press IS the undo of that un-export. The file + lineage
        # row come back without the user needing to know about
        # Ctrl+Z. Falls through to the normal verb path when no
        # pending un-export entry exists for this item.
        if (self._export_mode
                and new_state == STATE_PICKED
                and not item.exported):
            entry = self._pop_most_recent_unexport(item.item_id)
            if entry is not None:
                self._apply_undo_entry(entry)
                return True
        try:
            self._eg.set_phase_state(item.item_id, self._phase, new_state)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "set_phase_state(%s, %s, %s) failed",
                item.item_id, self._phase, new_state)
            return True
        # spec/68 §3 — Export-mode un-export: a green→red flip on a
        # cell that already shipped also tears down the ship-side
        # state (file + lineage row + edit_exported flag). The bytes
        # + lineage row are captured BEFORE the gateway tears them
        # down so the undo stack can put them back if the user
        # presses Ctrl+Z (or re-presses P) — the silent unlink is
        # trivially recoverable.
        snapshots: list[_UnexportSnapshot] = []
        if (self._export_mode
                and new_state == STATE_SKIPPED
                and item.exported):
            snapshots = self._capture_unexport(item.item_id)
            try:
                self._eg.delete_exported_file(item.item_id)
            except Exception:                                      # noqa: BLE001
                log.exception(
                    "delete_exported_file(%s) failed", item.item_id)
            # The cell's badge clears straight away — the shipped
            # status is gone, the watermark must go with it.
            item.exported = False
            self._thumb_widgets[idx].setExported(False)
        # Push the undo entry AFTER the writes land so a Ctrl+Z
        # pop'd state restoration can rewrite to the correct prev.
        self._push_undo(_UndoEntry(
            item_id=item.item_id, prev_state=cur_state,
            new_state=new_state, snapshots=snapshots))
        item.state = new_state
        self._thumb_widgets[idx].setState(new_state)
        self._update_counts()
        # Refresh just the toolbar progress block — no full grid relayout.
        pct = (
            int(round(self._reviewed / self._total * 100))
            if self._total else 0
        )
        self._progress_label.setText(
            f"{self._reviewed} / {self._total} reviewed"
        )
        self._progress_bar.setValue(pct)
        self._progress_bar.setState(
            "done" if self._reviewed == self._total and self._total > 0
            else "prog" if self._reviewed > 0
            else None
        )
        return True

    def _apply_version_verb_at_index(self, idx: int, verb: str) -> bool:
        """spec/89 Slice 5 — per-version verb inside a versions cluster
        sub-grid. ``P`` and ``X`` write the decision ledger that
        backs the member:

        * **Virtual Mira member** (``item_id`` starts with ``mira:``)
          → ``phase_state(edit, source_item)``. The next Export run
          will render or skip the Mira version based on this.
        * **Lineage member** (``item_id`` is the row's
          ``export_relpath``) → ``lineage.intent_state``.

        ``Space`` toggles between picked and skipped (Compare is the
        initial-only state — once the user touches a version they
        pick a side). The actual file unlink + render commit happen
        later at Export-run time (Slice 8); here we only flip the
        intent ledger so the cluster cover state machine can re-read."""
        if idx < 0 or idx >= len(self._items):
            return False
        item = self._items[idx]
        cur = item.state
        if verb == "pick":
            new_state = "picked"
        elif verb == "skip":
            new_state = "skipped"
        elif verb == "toggle":
            new_state = "skipped" if cur == "picked" else "picked"
        else:
            return True
        if new_state == cur:
            return True
        is_mira = item.item_id.startswith("mira:")
        try:
            if is_mira:
                source_id = item.item_id.split(":", 1)[1]
                self._eg.set_phase_state(source_id, "edit", new_state)
            else:
                self._eg.set_lineage_intent(item.item_id, new_state)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "version-verb write failed (%s → %s)",
                item.item_id, new_state)
            return True
        item.state = new_state
        self._thumb_widgets[idx].setState(new_state)
        self._update_counts()
        return True

    @staticmethod
    def _next_state(item_kind: str, cur: Optional[str], verb: str) -> Optional[str]:
        """spec/63 §4: P=Pick, X=Skip, Space=binary toggle,
        C=cycle Skip→Pick→Compare→Skip (Nelson 2026-06-18; was
        Pick→Skip→Compare). The default-Skip starting state goes to
        Pick on first click — match the user's "red→green" mental
        model.

        Videos are a binary ledger (§4 rule): C degrades to Space. The
        same rule applies to the page when a video Thumb has focus.
        """
        if verb == "pick":
            return STATE_PICKED
        if verb == "skip":
            return STATE_SKIPPED
        if verb == "toggle":
            return STATE_SKIPPED if cur == STATE_PICKED else STATE_PICKED
        if verb == "cycle":
            if item_kind == "video":
                # Binary degradation
                return STATE_SKIPPED if cur == STATE_PICKED else STATE_PICKED
            ladder = (STATE_SKIPPED, STATE_PICKED, STATE_CANDIDATE)
            if cur not in ladder:
                # No explicit state yet (default-Skip on a fresh cell)
                # → first click goes green (Pick). Matches the user's
                # "red→green→compare" expectation.
                return STATE_PICKED
            return ladder[(ladder.index(cur) + 1) % len(ladder)]
        return None

    def _focused_thumb_index(self) -> Optional[int]:
        """Index of the Thumb currently holding keyboard focus, or the
        last-clicked Thumb if focus has drifted away."""
        focused = QApplication.focusWidget()
        if focused is not None:
            try:
                return self._thumb_widgets.index(focused)
            except ValueError:
                pass
        return None

    # ── Bulk Pick all / Skip all ──────────────────────────────────────

    def _on_pick_all_clicked(self) -> None:
        self._bulk_set_state(STATE_PICKED)

    def _on_skip_all_clicked(self) -> None:
        self._bulk_set_state(STATE_SKIPPED)

    # ── Export-mode primary trigger (spec/68 §3) ─────────────────────

    def _collect_ship_cells(self) -> tuple:
        """Walk ``self._items`` and return ``(photo_cells, segment_rows,
        snapshot_cells)`` for the spec/60 batch — the trio that
        :func:`submit_export_batch` translates into PhotoUnits + the
        slice-4-walker ClipUnits + frame-extracted snapshot PhotoUnits.

        Day mode: photos ship as ExportCells; flat videos and synthetic
        video clusters (spec/56) expand into their picked segments +
        snapshots. Scanner clusters (burst / focus / exposure / repeat)
        are skipped — the user drills in to ship from their members,
        matching the existing grammar.

        Sub-grid mode (after drilling into a video cluster): each
        member already carries its type stamp (``"clip"`` →
        :class:`VideoSegment`, ``"snapshot"`` → :class:`SnapshotCell`).
        The walker reverses to a :class:`VideoSegment` / at_ms via the
        gateway so segments + snapshots ship in their own lanes.

        **spec/118 §3** — re-edited items that already have a Mira
        render on disk are RE-INCLUDED in the run (not skipped by the
        ``already_shipped`` filter) so the batch can offer the
        Overwrite / Keep both choice. The eventual write policy is
        picked at the run-confirm layer; this method only widens the
        pool."""
        from mira.ui.exported.batch import ExportCell, SnapshotCell
        from mira.ui.exported.staleness import is_cell_stale

        already_shipped: set = set()
        try:
            already_shipped = self._eg.exported_item_ids()
        except Exception:                                          # noqa: BLE001
            log.exception("DaysGridPage: exported_item_ids failed")

        event_root = Path(self._eg.event_root)
        try:
            phase_states = self._eg.phase_states("edit")
        except Exception:                                          # noqa: BLE001
            log.exception("DaysGridPage: phase_states('edit') failed")
            phase_states = {}

        photo_cells: list[ExportCell] = []
        segment_rows: list = []
        snapshot_cells: list[SnapshotCell] = []

        def _expand_video(video_id: str) -> None:
            """Expand one source video into its picked segments +
            picked snapshots."""
            try:
                segs = self._eg.video_segments(video_id)
            except Exception:                                      # noqa: BLE001
                log.exception(
                    "DaysGridPage: video_segments(%s) failed", video_id)
                segs = []
            for seg in segs:
                if seg.item_id in already_shipped:
                    continue
                ps = phase_states.get(seg.item_id)
                if (ps is None) or (ps.state != STATE_PICKED):
                    continue
                segment_rows.append(seg)
            try:
                snaps = self._eg.video_snapshots(video_id)
            except Exception:                                      # noqa: BLE001
                log.exception(
                    "DaysGridPage: video_snapshots(%s) failed", video_id)
                snaps = []
            for snap in snaps:
                if snap.item_id in already_shipped:
                    continue
                ps = phase_states.get(snap.item_id)
                if (ps is None) or (ps.state != STATE_PICKED):
                    continue
                snapshot_cells.append(SnapshotCell(
                    item_id=snap.item_id,
                    video_item_id=video_id,
                    at_ms=int(snap.at_ms),
                    day_number=self._day_number,
                ))

        for it in self._items:
            # Sub-grid members of a video cluster carry their type
            # stamp directly — translate by stamp before the kind-based
            # branches fire (a clip's item_kind is "video", a
            # snapshot's is "photo"; the stamp disambiguates from a
            # source video / regular photo).
            if it.stamp == "clip":
                if it.item_id in already_shipped:
                    continue
                ps = phase_states.get(it.item_id)
                if (ps is None) or (ps.state != STATE_PICKED):
                    continue
                seg_item = self._eg.item(it.item_id)
                parent_id = (
                    getattr(seg_item, "parent_item_id", None)
                    if seg_item else None)
                if not parent_id:
                    continue
                for seg in self._eg.video_segments(parent_id):
                    if seg.item_id == it.item_id:
                        segment_rows.append(seg)
                        break
                continue
            if it.stamp == "snapshot":
                if it.item_id in already_shipped:
                    continue
                ps = phase_states.get(it.item_id)
                if (ps is None) or (ps.state != STATE_PICKED):
                    continue
                snap_item = self._eg.item(it.item_id)
                parent_id = (
                    getattr(snap_item, "parent_item_id", None)
                    if snap_item else None)
                if not parent_id:
                    continue
                for snap in self._eg.video_snapshots(parent_id):
                    if snap.item_id == it.item_id:
                        snapshot_cells.append(SnapshotCell(
                            item_id=snap.item_id,
                            video_item_id=parent_id,
                            at_ms=int(snap.at_ms),
                            day_number=self._day_number,
                        ))
                        break
                continue

            if it.item_kind == "cluster":
                # Synthetic video cluster (spec/56) — expand its
                # source video's children. Scanner clusters
                # (_video_item_id is None) are skipped; the user drills
                # in to ship from them.
                video_id = getattr(it, "_video_item_id", None)
                if not video_id:
                    continue
                _expand_video(video_id)
                continue
            if it.item_kind == "video":
                # spec/56: a video's PICKED segments + snapshots are the
                # ship units; the parent video cell is the aggregate.
                # (In Export mode the reshape turns most videos into
                # clusters above — this branch covers videos with no
                # workshop touch yet, where the flat cell survives.)
                _expand_video(it.item_id)
                continue
            # Photo path — existing per-cell behaviour.
            if it.item_kind != "photo":
                continue
            if it.state != STATE_PICKED:
                continue
            stale = bool(it.edited_since_export)
            # spec/118 §3 — already-shipped items normally drop out
            # here. Re-edited items (stale) re-enter so the run-level
            # Overwrite / Keep both prompt can apply.
            if it.item_id in already_shipped and not stale:
                continue
            if it._path is None and it._sha256 is None:
                continue
            src = it._path or (
                event_root / (
                    self._eg.item(it.item_id).origin_relpath
                    if self._eg.item(it.item_id) else "")
            )
            if not src or not Path(src).is_file():
                continue
            # If we're re-shipping a stale item, source must always be
            # the ORIGINAL photo, not the on-disk export. ``it._path``
            # on a flat cell already points at the source; defensive
            # re-resolve here in case a future caller bound the cell
            # path to the rendered file by mistake.
            if stale:
                src_item = self._eg.item(it.item_id)
                if src_item is not None and src_item.origin_relpath:
                    src = event_root / src_item.origin_relpath
            photo_cells.append(ExportCell(
                item_id=it.item_id,
                path=Path(src),
                day_number=self._day_number,
            ))
        return photo_cells, segment_rows, snapshot_cells

    def _render_cell_is_stale(self, cell) -> bool:
        """spec/118 §3 — is this batch ExportCell an edited-since-export
        case (i.e. an item that already has a Mira render on disk whose
        recipe has since diverged)? Drives the run-level Overwrite /
        Keep both prompt; cells that are NOT stale pass through the
        legacy keep-both path unchanged."""
        if self._eg is None:
            return False
        try:
            from mira.ui.exported.staleness import is_cell_stale
            return is_cell_stale(self._eg, cell.item_id)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "DaysGridPage: _render_cell_is_stale(%s) failed",
                cell.item_id)
            return False

    def _cell_with_override_dest(self, cell):
        """spec/118 §3 — return ``cell`` annotated with
        ``dest_dir_override`` pointing at the existing lineage row's
        parent. Idempotent for cells with no existing Mira row (they
        just keep ``day_labels`` routing)."""
        if self._eg is None:
            return cell
        try:
            versions = self._eg.versions_for_item(cell.item_id)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "DaysGridPage: versions_for_item(%s) failed",
                cell.item_id)
            return cell
        existing = next(
            (v for v in versions
             if (getattr(v, "provenance", "") or "") == "mira_render"),
            None,
        )
        if existing is None:
            return cell
        from dataclasses import replace
        return replace(
            cell,
            dest_dir_override=Path(
                existing.export_relpath).parent.as_posix(),
        )

    def _collect_delete_relpaths(self) -> list:
        """spec/89 §5.1 step 2 — the "Delete M files" pool for an Export
        now run on the current grid. Returns the ``Exported Media/``
        relpaths whose lineage row carries ``intent_state='skipped'``
        and whose file still exists on disk.

        Two routing modes:

        * **Day / cluster cover mode** — walk every visible item's
          source id (flat cell → ``item.item_id``; versions cluster
          cover → the bucket's ``versions:<id>`` tail) and pull every
          ``versions_for_item`` row whose intent is ``skipped`` + still
          materialised. Slice 5's versions cluster sub-grid defers the
          file delete to here (spec/89 Slice 5 contract); a Drop on a
          flat cell already deletes immediately, but the sweep catches
          orphans (manual file copy + state reload + bulk-Drop on
          shipped before file delete landed).
        * **Versions sub-grid mode** — each cell's ``item_id`` IS the
          lineage row's relpath. Drop targets are the visible cells
          whose ``state == STATE_SKIPPED``.
        """
        if self._eg is None or self._eg.event_root is None:
            return []
        event_root = Path(self._eg.event_root)
        delete_rels: set = set()

        in_versions_subgrid = (
            self._mode == "cluster"
            and self._cluster is not None
            and getattr(self._cluster, "kind", "") == "versions"
        )
        if in_versions_subgrid:
            for it in self._items:
                if it.item_kind != "photo":
                    continue
                if it.state != STATE_SKIPPED:
                    continue
                rel = it.item_id
                if not isinstance(rel, str):
                    continue
                if not rel.startswith("Exported Media/"):
                    continue
                if (event_root / rel).is_file():
                    delete_rels.add(rel)
            return sorted(delete_rels)

        # Day mode — collect source item ids visible on the surface.
        source_ids: set = set()
        for it in self._items:
            if it.item_kind == "cluster":
                ck = getattr(
                    getattr(it, "_cull_cluster", None), "bucket_key", "")
                if isinstance(ck, str) and ck.startswith("versions:"):
                    source_ids.add(ck.split(":", 1)[1])
                continue
            source_ids.add(it.item_id)

        for sid in source_ids:
            try:
                versions = self._eg.versions_for_item(sid)
            except Exception:                                      # noqa: BLE001
                log.exception(
                    "DaysGridPage: versions_for_item(%s) failed", sid)
                continue
            for v in versions:
                if (getattr(v, "intent_state", "") or "") != "skipped":
                    continue
                rel = v.export_relpath
                if (event_root / rel).is_file():
                    delete_rels.add(rel)
        return sorted(delete_rels)

    def _collect_export_run_plan(self) -> dict:
        """Pre-flight for "Export now" — the math the user sees in the
        confirm modal IS the math the run executes. Returns
        ``{"render_cells", "render_segments", "render_snapshots",
        "delete_relpaths"}``."""
        cells: list = []
        segments: list = []
        snapshots: list = []
        in_versions_subgrid = (
            self._mode == "cluster"
            and self._cluster is not None
            and getattr(self._cluster, "kind", "") == "versions"
        )
        # spec/89 Slice 5 — versions sub-grid cells are existing lineage
        # rows, not render targets. Skip the render lane entirely so the
        # run is delete-only when the user clicks Export now from inside
        # a versions cluster.
        if not in_versions_subgrid:
            cells, segments, snapshots = self._collect_ship_cells()
        delete_rels = self._collect_delete_relpaths()
        return {
            "render_cells": cells,
            "render_segments": segments,
            "render_snapshots": snapshots,
            "delete_relpaths": delete_rels,
        }

    def _on_export_clicked(self) -> None:
        """spec/89 §5.1 — "Export now" batch trigger. Shows the locked
        "Render N · Delete M files. Proceed?" confirm; on Run, deletes
        the red-intent files first (so cascading Cut-membership cleanup
        lands before fresh rows arrive), then enqueues the render
        manifest through the spec/59 §8 ``BatchJobQueue`` (renamed from
        ``BatchExportQueue`` by spec/84 once ingest started riding it).

        N = green-intent items with a render to produce. For photos
        that's the day's ``picked`` flat cells not yet in
        ``exported_item_ids()``; for videos it's their picked SEGMENTS
        (spec/56 — ClipUnits via the slice-4 walker) and picked
        SNAPSHOTS (PhotoUnits over an extracted source frame).

        M = ``Exported Media/`` files whose lineage carries
        ``intent_state='skipped'`` and that still exist on disk. The
        common source is the versions cluster sub-grid (Slice 5
        defers the delete to here); a Drop on a flat cell already
        deletes immediately, so M is usually 0 in plain day mode.

        Engine + queue are locked (spec/68 §4); this handler only
        builds the manifest, runs the delete sweep, and enqueues."""
        if self._eg is None or not self._export_mode:
            return
        if self._eg.event_root is None:
            return
        from mira.ui.exported.batch import (
            day_label_for,
            submit_export_batch,
        )

        plan = self._collect_export_run_plan()
        n_render = (
            len(plan["render_cells"])
            + len(plan["render_segments"])
            + len(plan["render_snapshots"])
        )
        m_delete = len(plan["delete_relpaths"])

        if n_render == 0 and m_delete == 0:
            show_info(
                self,
                tr("Nothing to do"),
                tr(
                    "No green renders pending and no red files to "
                    "delete — toggle a cell, or open another day."
                ),
            )
            return

        batch_queue = getattr(self.window(), "batch_queue", None)
        if n_render > 0 and batch_queue is None:
            show_error(
                self,
                tr("Batch queue unavailable"),
                tr(
                    "The app's batch queue isn't reachable — try "
                    "restarting Mira."),
            )
            return

        # spec/118 §3 — when the run includes ≥1 edited-since-export
        # item, replace the plain confirm with the run-level Overwrite
        # / Keep both ask. A run with no stale items keeps the original
        # confirm so the existing UX is unchanged.
        stale_cells = [
            c for c in plan["render_cells"]
            if self._render_cell_is_stale(c)
        ]
        collision_policy = "unique"
        if stale_cells:
            from mira.ui.exported.collision_dialog import (
                ask_batch_collision_policy,
            )
            choice = ask_batch_collision_policy(
                self,
                n_render=n_render,
                m_delete=m_delete,
                n_stale=len(stale_cells),
                default=DaysGridPage._last_batch_collision,
            )
            if choice is None:
                return
            collision_policy = choice
            DaysGridPage._last_batch_collision = choice
        else:
            title, body, primary = self._export_now_modal_text(
                n_render, m_delete)
            if not confirm(self, title, body, primary_text=primary):
                return

        # spec/89 §5.1 step 2 — delete first so the cascading Cut-
        # membership cleanup lands before any fresh rows arrive. The
        # sweep is best-effort: a single failed unlink is logged and
        # the rest of the run continues.
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for rel in plan["delete_relpaths"]:
                try:
                    self._eg.delete_exported_file_by_relpath(rel)
                except Exception:                                  # noqa: BLE001
                    log.exception(
                        "DaysGridPage: delete_exported_file_by_relpath"
                        "(%s) failed during Export now", rel)
        finally:
            QGuiApplication.restoreOverrideCursor()

        if n_render > 0:
            day_labels = {self._day_number: day_label_for(
                self._eg, self._day_number)}
            # spec/118 §3 — under OVERRIDE, pin each stale cell's
            # dest_dir to the existing lineage row's parent so the
            # atomic replace lands at the EXACT existing
            # ``export_relpath`` (keeps the Cut's frame identity stable
            # even if the day folder has been renamed since the last
            # export).
            render_cells = plan["render_cells"]
            if collision_policy == "override":
                render_cells = [
                    self._cell_with_override_dest(c) for c in render_cells
                ]
            try:
                submit_export_batch(
                    self._eg, self.gateway.settings, batch_queue,
                    event_name=self._event_name,
                    cells=render_cells,
                    day_labels=day_labels,
                    parent_widget=self,
                    segment_rows=plan["render_segments"],
                    snapshot_cells=plan["render_snapshots"],
                    collision=collision_policy,
                )
            except Exception as exc:                               # noqa: BLE001
                log.exception("DaysGridPage: export submit failed")
                show_error(
                    self,
                    tr("Could not start the export"),
                    tr("The batch could not be queued.\n\n{err}")
                    .replace("{err}", str(exc)),
                )
                return

        # Cluster covers + flat-cell badges read from gateway state; a
        # full refresh keeps the surface honest after the delete sweep
        # lands (the render is async — the corresponding refresh comes
        # from the batch commit closure).
        if m_delete > 0:
            if self._mode == "cluster" and self._cluster is not None:
                self._open_cluster(self._cluster)
            else:
                self._refresh_from_gateway()

    @staticmethod
    def _export_now_modal_text(
        n_render: int, m_delete: int,
    ) -> tuple:
        """spec/89 §5.1 D2.B — the locked "Render N · Delete M files.
        Proceed?" wording. Factored out so the all-days variant on the
        Days List can share the exact phrasing. Returns
        ``(title, body, primary_button_label)``."""
        if n_render == 0 and m_delete == 0:
            # Caller short-circuits on this; defensive default for tests.
            return (tr("Nothing to do"), tr(""), tr("OK"))
        title = (
            tr("Render {n} · Delete {m} files. Proceed?")
            .replace("{n}", str(n_render))
            .replace("{m}", str(m_delete))
        )
        body_bits: list = []
        if n_render > 0:
            body_bits.append(
                tr("{n} item(s) render to Exported Media/.")
                .replace("{n}", str(n_render))
            )
        if m_delete > 0:
            body_bits.append(
                tr(
                    "{m} file(s) drop from Exported Media/ "
                    "(Original Media/ stays untouched)."
                ).replace("{m}", str(m_delete))
            )
        body = "\n\n".join(body_bits)
        primary = tr("Run")
        return (title, body, primary)

    def _bulk_set_state(self, state: str) -> None:
        """Apply ``state`` to every item currently visible (day mode:
        all flat items + every cluster member; cluster mode: every
        member of the open cluster). Goes through one bulk gateway
        call (single transaction) per the spec/63 §5d pattern.

        Export-mode Drop all (spec/68 §3 second bullet) cascades the
        un-export: every item in the affected set that was already
        shipped gets its ``Exported Media/`` file deleted + its
        lineage row dropped + ``edit_exported`` cleared. The confirm
        text reads the shipped count out loud so the user knows the
        on-disk blast before confirming. Charter-safe: only
        ``Exported Media/`` is touched; ``Original Media/`` stays
        immutable."""
        if self._eg is None:
            # Mock mode — apply against in-memory items so smokes/tests
            # see the visual change. No gateway round trip.
            thumb_state = _CELL_TO_THUMB_STATE[
                CellColor.KEPT if state == STATE_PICKED
                else CellColor.DISCARDED
            ]
            for i, it in enumerate(self._items):
                if it.item_kind == "cluster":
                    it.cluster_split = None
                it.state = thumb_state
                self._thumb_widgets[i].setState(thumb_state)
            self._update_counts()
            self._refresh()
            return
        item_ids = self._affected_item_ids()
        if not item_ids:
            return
        is_drop_all = (
            self._export_mode and state == STATE_SKIPPED)
        # spec/68 §3 — figure out the on-disk blast for the confirm
        # text + the un-export cascade.
        shipped_to_drop: list[str] = []
        if is_drop_all:
            try:
                shipped_set = self._eg.exported_item_ids()
            except Exception:                                      # noqa: BLE001
                log.exception(
                    "DaysGridPage: exported_item_ids failed; bulk "
                    "Drop-all may leave orphans")
                shipped_set = set()
            shipped_to_drop = [
                iid for iid in item_ids if iid in shipped_set]
        if self._export_mode:
            # Export-mode confirm: name the verb + the shipped blast.
            if is_drop_all and shipped_to_drop:
                title = tr("Drop all {n}?").replace(
                    "{n}", str(len(item_ids)))
                body = tr(
                    "{shipped} of these are already exported — their "
                    "files will be deleted from Exported Media/ "
                    "(Original Media/ is untouched). Continue?"
                ).replace("{shipped}", str(len(shipped_to_drop)))
                primary = tr("Drop")
            elif is_drop_all:
                title = tr("Drop all {n}?").replace(
                    "{n}", str(len(item_ids)))
                body = tr(
                    "None of these have shipped yet — Drop just "
                    "marks them won't-export. Continue?")
                primary = tr("Drop")
            else:
                title = tr("Export all {n}?").replace(
                    "{n}", str(len(item_ids)))
                body = tr(
                    "Every cell turns green — they'll all ship on the "
                    "next Export-green run. Continue?")
                primary = tr("Export")
        else:
            verb = "Pick" if state == STATE_PICKED else "Skip"
            title = tr("{verb} all in view?").replace("{verb}", verb)
            body = tr(
                "This marks {n} item(s) as {verb}. Continue?"
            ).replace("{n}", str(len(item_ids))).replace(
                "{verb}", verb.lower())
            primary = verb
        confirmed = confirm(self, title, body, primary_text=primary)
        if not confirmed:
            return
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._eg.set_items_phase_state(item_ids, self._phase, state)
            # spec/68 §3 — un-export cascade. Runs AFTER the
            # phase_state write so the per-item teardown sees a
            # consistent gateway view (the phase_state already says
            # skipped). delete_exported_file is idempotent — a missing
            # row is a no-op — so partial failures don't corrupt the
            # remaining items.
            for iid in shipped_to_drop:
                try:
                    self._eg.delete_exported_file(iid)
                except Exception:                                  # noqa: BLE001
                    log.exception(
                        "delete_exported_file(%s) failed during "
                        "bulk Drop-all", iid)
        except Exception:                                          # noqa: BLE001
            log.exception("bulk state set failed for %d items", len(item_ids))
        finally:
            QGuiApplication.restoreOverrideCursor()
        # Rebuild from gateway so cluster covers, aggregates,
        # toolbar progress, AND the corner exported badge all settle
        # consistently against the now-truthful lineage.
        if self._mode == "cluster" and self._cluster is not None:
            self._open_cluster(self._cluster)
        else:
            self._refresh_from_gateway()

    def _affected_item_ids(self) -> List[str]:
        """item_ids affected by a bulk verb at the current mode.

        Day mode: every flat item + every member of every cluster
        cover. Cluster mode: every member of the open cluster.
        Honors the §5a rule that cluster covers don't have a state
        of their own — they aggregate their members'."""
        ids: list[str] = []
        if self._mode == "cluster" and self._cluster is not None:
            for m in self._cluster.members:
                if m.item_id:
                    ids.append(m.item_id)
            return ids
        for it in self._items:
            if it.item_kind == "cluster" and it._cull_cluster is not None:
                ids.extend(
                    m.item_id for m in it._cull_cluster.members if m.item_id
                )
            elif it.item_id and not it.item_id.startswith("cluster:"):
                ids.append(it.item_id)
        return ids

    # ── Internal helpers ───────────────────────────────────────────────

    def _find_item(self, item_id: str) -> Optional[GridItem]:
        for it in self._items:
            if it.item_id == item_id:
                return it
        return None

    def _close_event_internal(self) -> None:
        self._thumb_timer.stop()
        self._thumb_pending.clear()
        self._thumb_pixmap_cache.clear()
        self._event_name = ""
        # Drop any pending undo entries so their captured JPEG bytes
        # don't outlive the event's session.
        self._undo_stack.clear()
        if self._eg is not None:
            try:
                self._eg.close()
            except Exception:                                      # noqa: BLE001
                log.exception("EventGateway close failed")
            self._eg = None

    # ── Whole-event proxy seeding (spec/63 slice 7) ────────────────────

    def _seed_proxies_for_event(self) -> None:
        """Queue MISSING photo proxies for the background builder so
        the screen-copy tier fills quietly while the user is on the
        grid. Two passes vs the naive version (Nelson 2026):

        1. **Memoise per event root.** A second call for the same
           event (e.g. switching between days) is a no-op — the first
           call already queued everything. Survives ``_close_event_
           internal``; reset by an event-root change.
        2. **Filter out already-cached items.** ``resolve_proxy`` is
           a stat + tiny JSON read per item (~0.1 ms each on warm
           disk); skipping cached items here means the
           ``BatchProgressLine``'s "Creating previews — N left"
           counter only reflects ACTUAL work to do, not a flicker
           through every item the builder would short-circuit
           anyway.

        Builds themselves still run on the builder thread; this is
        the seed boundary only."""
        if self._eg is None:
            return
        try:
            from core.photo_proxy_cache import resolve_proxy
            from mira.ui.media.photo_cache import photo_cache
            event_root = Path(self._eg.event_root) if self._eg.event_root else None
            if event_root is None:
                return
            if event_root in self._seeded_proxy_event_roots:
                return
            self._seeded_proxy_event_roots.add(event_root)
            pairs: list[tuple[Path, str]] = []
            for it in self._eg.items(kind="photo"):
                if not it.origin_relpath or not it.sha256:
                    continue
                source_path = event_root / it.origin_relpath
                try:
                    if resolve_proxy(
                            event_root, it.sha256, source_path) is not None:
                        continue        # already cached → no work to queue
                except Exception:                                  # noqa: BLE001
                    pass                # fall through and queue it
                pairs.append((source_path, it.sha256))
            if pairs:
                photo_cache().seed_proxies(event_root, pairs)
        except Exception:                                          # noqa: BLE001
            log.exception("whole-event proxy seed failed")

    # ── On-disk thumbnail loader (port of PickPage cadence) ────────────

    def _enqueue_thumbnails(self) -> None:
        """Queue every visible cell that needs a thumbnail. Cache hits
        paint instantly (the cache is per-session, bounded by the items
        the user has touched). Misses queue onto the timer-driven
        decoder so a 200-cell day doesn't freeze the surface."""
        for idx, item in enumerate(self._items):
            if item._path is None:
                continue
            cache_key = self._thumb_cache_key(item)
            cached = self._thumb_pixmap_cache.get(cache_key)
            if cached is not None and not cached.isNull():
                self._apply_thumb_pixmap(idx, cached)
                continue
            self._thumb_pending.append((idx, cache_key))
        if self._thumb_pending and not self._thumb_timer.isActive():
            self._thumb_timer.start()

    @staticmethod
    def _thumb_cache_key(item: GridItem) -> str:
        """Cache key for the in-memory pixmap cache. Cluster covers
        share their cover member's sha so the same pixmap is reused
        if the user enters the cluster afterwards (the member shows
        the same image at a similar scale)."""
        if item._sha256:
            return f"sha:{item._sha256}"
        if item._path is not None:
            return f"path:{item._path}"
        return f"id:{item.item_id}"

    def _apply_thumb_pixmap(self, idx: int, pixmap: QPixmap) -> None:
        # Route through ThumbGrid.set_pixmap rather than poking the live
        # cell directly. The grid builds cells in chunks (50 sync, the
        # rest on QTimer ticks); poking ``_thumb_widgets[idx]`` only
        # worked for cells already built. A decode that landed before a
        # later-chunk cell existed was lost — and because the grid's own
        # ThumbGridItem still carried ``pixmap=None``, the cell painted
        # empty (border only) when it finally built. set_pixmap mutates
        # the stored item too, so the pixmap survives the build
        # (Nelson 2026-06-22 — "only borders" on Quick Sweep grids).
        if 0 <= idx < self._grid.count():
            self._grid.set_pixmap(idx, pixmap)
        if 0 <= idx < len(self._items):
            self._items[idx].pixmap = pixmap

    def _load_some_thumbs(self) -> None:
        if not self._thumb_pending:
            self._thumb_timer.stop()
            return
        done = 0
        while self._thumb_pending and done < _THUMBS_PER_TICK:
            idx, cache_key = self._thumb_pending.pop(0)
            if not (0 <= idx < len(self._items)):
                continue
            item = self._items[idx]
            if item._path is None:
                continue
            pm = self._decode_thumbnail(item)
            if pm is None or pm.isNull():
                continue
            self._thumb_pixmap_cache[cache_key] = pm
            self._apply_thumb_pixmap(idx, pm)
            done += 1
        if not self._thumb_pending:
            self._thumb_timer.stop()

    def _decode_thumbnail(self, item: GridItem) -> Optional[QPixmap]:
        """Decode one cell's thumbnail.

        Photos / snapshots → ``photo_thumb_cache.ensure_photo_thumb``
        materialises a 256-px JPEG on first request and reads the small
        JPEG on every subsequent call (~2 ms). Videos →
        ``thumb_cache.ensure_thumb`` extracts a frame at 1 s and caches
        it. Same pipeline the legacy PickPage uses — the engines are
        reused, not rewritten (spec/70 §7).

        Routing is by FILE EXTENSION, not ``item_kind`` — a synthetic
        video cluster cover (spec/56 Export-mode) has
        ``item_kind="cluster"`` but its ``_path`` is the source MP4, so
        the photo thumb cache would log "cannot identify image file"
        (Nelson 2026-06-15 log spam report). Extension routing handles
        it correctly.

        Paths mode (standalone Quick Sweep — no gateway, no sha256):
        decode the source AT the tile size (JPEG DCT-domain downscale,
        ~3× faster than full decode + scale). Videos in paths mode
        return ``None`` — there is no event ``.cache/`` to materialise
        a frame thumb into; the Thumb widget paints its placeholder.
        """
        from core.video_discovery import VIDEO_EXTENSIONS
        from mira.ui.media.image_loader import load_pixmap

        path = item._path
        if path is None:
            return None
        is_video_source = path.suffix.lower() in VIDEO_EXTENSIONS
        try:
            if self._eg is not None:
                if is_video_source:
                    from core.thumb_cache import ensure_thumb
                    thumb_path = ensure_thumb(
                        event_root=Path(self._eg.event_root),
                        source_video=path,
                        source_rel_path=path.relative_to(
                            Path(self._eg.event_root)),
                        item_id="daysgrid",
                        position_ms=1000,
                        fallback_position_ms=0,
                    )
                    return load_pixmap(thumb_path)
                if item._sha256:
                    from core.photo_thumb_cache import ensure_photo_thumb
                    thumb_path = ensure_photo_thumb(
                        event_root=Path(self._eg.event_root),
                        source_path=path,
                        sha256=item._sha256,
                    )
                    return load_pixmap(thumb_path)
                return load_pixmap(path)
            if is_video_source:
                return None
            return load_pixmap(path, _TILE_SIZE)
        except Exception:                                          # noqa: BLE001
            log.warning(
                "thumbnail decode failed for %s", path, exc_info=True)
            return None
