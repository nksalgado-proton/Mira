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
    P=Pick  X=Skip  Space=toggle Pick⇄Skip  C=cycle Pick→Skip→Compare
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
from mira.ui.base.flow_layout import FlowLayout
from mira.ui.design import (
    StageProgress,
    SurfaceIdentityHeader,
    Thumb,
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
    cluster_type: str | None = None
    cluster_count: int = 0
    cluster_split: tuple[int, int] | None = None
    stamp: str | None = None
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
    """Card2-styled pill ‹ Day N · title · date · N items ›.

    Mutable: ``set_day(...)`` updates the label in place so the page
    can refresh without rebuilding the widget (rebuild + deleteLater
    left the old widget painted under the new one — Nelson 2026-06-14)."""

    prev_clicked = pyqtSignal()
    next_clicked = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Card2")
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
    swatch.setStyleSheet(
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

        # spec/63 §4 Ctrl+Z = undo last decision. Plain phase-state
        # flips push lightweight entries (~80 bytes); Export-mode
        # X-on-shipped pushes a snapshot of the deleted JPEG + its
        # lineage row so the restore reproduces the on-disk state.
        # Stack-bounded by ``_UNDO_MAX`` so the bytes can't grow
        # unbounded; cleared on event close.
        self._undo_stack: list[_UndoEntry] = []

        # Per-cell focus tracking — the locked P/X/Space/C keys act on
        # the Thumb the user last clicked (Qt focus follows). The
        # index also drives "skip the cluster placeholder" decisions.
        self._thumb_widgets: list[Thumb] = []

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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.setSpacing(14)

        # spec/71 identity header — the SHARED Days Grid inherits its
        # host phase's colour. Rebuilt on every phase swap via
        # _refresh_identity(); the existing legend strip below stays
        # (it documents the badge/eye chrome unique to this grid).
        self._identity_host = QWidget()
        self._identity_host_layout = QVBoxLayout(self._identity_host)
        self._identity_host_layout.setContentsMargins(0, 0, 0, 0)
        self._identity_host_layout.setSpacing(0)
        outer.addWidget(self._identity_host)
        self._refresh_identity()

        # ── Sticky toolbar ──
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        self._back = ghost_button("‹ Back")
        self._back.clicked.connect(self._on_back_clicked)
        toolbar.addWidget(self._back)
        self._day_pill = _DayNavigatorPill()
        self._day_pill.set_day(1, "", "", 0)
        self._day_pill.prev_clicked.connect(self.prev_day_requested.emit)
        self._day_pill.next_clicked.connect(self.next_day_requested.emit)
        toolbar.addWidget(self._day_pill)
        self._pick_all_btn = ghost_button("✓ Pick all")
        self._pick_all_btn.clicked.connect(self._on_pick_all_clicked)
        toolbar.addWidget(self._pick_all_btn)
        self._skip_all_btn = danger_ghost_button("✗ Skip all")
        self._skip_all_btn.clicked.connect(self._on_skip_all_clicked)
        toolbar.addWidget(self._skip_all_btn)
        self._new_pass_btn = primary_button("+ Start a new pass…")
        self._new_pass_btn.clicked.connect(self.new_pass_requested.emit)
        toolbar.addWidget(self._new_pass_btn)
        # spec/68 §3 — Export-mode primary trigger. Hidden outside
        # Export mode; the per-day batch submitter wires below.
        self._export_btn = primary_button("↑ Export green")
        self._export_btn.clicked.connect(self._on_export_clicked)
        toolbar.addWidget(self._export_btn)
        toolbar.addStretch()
        # Review progress on the right
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
        toolbar.addLayout(progress_block)
        outer.addLayout(toolbar)

        # ── Legend strip ──
        legend = QHBoxLayout()
        legend.setSpacing(18)
        legend.addWidget(_state_swatch("picked", "Picked"))
        legend.addWidget(_state_swatch("skipped", "Skipped"))
        legend.addWidget(_state_swatch("compare", "Compare"))
        legend.addWidget(_state_swatch("mixed", "Mixed cluster"))
        reminder = QLabel(
            "<span style='color:#8b94a7'>"
            "border <b style='color:#eef1f7'>= state</b>"
            " · badge <b style='color:#eef1f7'>= cluster</b>"
            " · eye <b style='color:#eef1f7'>= visited</b>"
            "</span>"
        )
        reminder.setObjectName("Sub")
        reminder.setTextFormat(Qt.TextFormat.RichText)
        legend.addWidget(reminder)
        legend.addStretch()
        # Locked-keymap hint at the right edge of the legend — the
        # user never has to leave the surface to remember the verbs.
        keys = QLabel(
            "<span style='color:#8b94a7'>"
            "P Pick · X Skip · Space toggle · C Compare"
            "</span>"
        )
        keys.setObjectName("Sub")
        keys.setTextFormat(Qt.TextFormat.RichText)
        legend.addWidget(keys)
        outer.addLayout(legend)

        # ── Scrolling grid ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        grid_host = QWidget()
        self._flow = FlowLayout(grid_host, spacing=18)
        self._flow.setContentsMargins(0, 0, 0, 0)
        self._scroll.setWidget(grid_host)
        self._scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        outer.addWidget(self._scroll, 1)

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
        # spec/66 §1.1 — Edit is creative-only: hide the Pick all /
        # Skip all / Start a new pass… buttons (no decision to make
        # here). They reappear when the page opens for the Pick phase.
        # Export mode swaps the toolbar to the ship verbs.
        self._apply_phase_chrome()
        # spec/71 — sync the identity header. QS hosts override
        # afterwards via :meth:`set_phase_identity`.
        if self._identity_phase != new_identity:
            self._identity_phase = new_identity
            self._refresh_identity()
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
        return True

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

    def _apply_phase_chrome(self) -> None:
        """spec/70 Phase 3 / spec/68 §3 — phase-driven chrome:

        * **Pick**: Pick all / Skip all / Start a new pass… visible.
        * **Edit**: bulk buttons hidden — Edit is creative-only
          (spec/66 §1.1), no Pick/Skip decision to make on the grid.
        * **Export**: Pick all + Skip all visible but **relabelled**
          to the ship verbs (Export all / Drop all), Start-a-new-pass
          hidden, and the Export-green primary action revealed.
        """
        is_pick = (self._phase == "pick" and not self._export_mode)
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
        """(Re)build the SurfaceIdentityHeader for the current host phase.

        Replacing the widget rather than mutating in place is simpler than
        chasing repolish() across the rail + badge property selectors and
        is cheap (one paint, no decode)."""
        if self._identity is not None:
            self._identity_host_layout.removeWidget(self._identity)
            self._identity.deleteLater()
            self._identity = None
        name, purpose = self._IDENTITY_SPEC.get(
            self._identity_phase, self._IDENTITY_SPEC["pick"])
        self._identity = SurfaceIdentityHeader(
            phase=self._identity_phase,
            name=tr(name),
            purpose=tr(purpose),
        )
        self._identity_host_layout.addWidget(self._identity)

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

    def _refresh_from_gateway(self) -> None:
        """Rebuild ``self._day_items`` from the live gateway and render.

        Engine is :func:`day_grid_cells` (the spec/32 cell list); the
        page only maps the resulting :class:`CullCell` shape onto the
        Thumb-shaped :class:`GridItem` model.

        Export-mode reshape (spec/56): a video cell that owns picked
        segments / snapshots becomes a "video" cluster cover; drilling
        in surfaces each member as its own cell with a "Video Clip" /
        "Snapshot" type stamp. Pick/Edit grids are NOT reshaped — the
        clustering is an Export-only presentation, not a model change.
        """
        if self._eg is None:
            return
        cells = day_grid_cells(
            self._eg, self._day_number, phase=self._phase,
            default_state=self._phase_default,
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
            day_items = self._reshape_for_export(day_items, phase_states)
        self._day_items = day_items
        self._items = list(self._day_items)
        self._update_counts()
        self._refresh()

    def _reshape_for_export(
        self,
        items: List["GridItem"],
        phase_states: Dict,
    ) -> List["GridItem"]:
        """Export-mode reshape (spec/56): replace each flat video cell
        whose source owns ≥1 segment or snapshot with a synthetic
        "video" cluster cover. Videos with no workshop touch yet (zero
        segments + zero snapshots) stay flat. Photos + scanner clusters
        pass through unchanged."""
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
            out.append(cluster_item if cluster_item is not None else it)
        return out

    def _video_cluster_grid_item(
        self,
        video_item: "GridItem",
        event_root: Path,
        phase_states: Dict,
    ) -> Optional["GridItem"]:
        """Build a synthetic "video" cluster cover for one source video,
        bundling its segment + snapshot child items as members. Returns
        ``None`` when the video has no children yet (caller keeps the
        flat cell). The cluster's ``bucket_key`` is
        ``video:<video_item_id>`` so the day-grid soft-state (and the
        existing visited / browsed plumbing) doesn't collide with
        scanner buckets."""
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
        if not segs and not snaps:
            return None
        video_row = self._eg.item(video_id)
        video_path = (
            event_root / video_row.origin_relpath
            if (video_row and video_row.origin_relpath) else None
        )

        members: list[CullItem] = []
        for seg in segs:
            seg_item = self._eg.item(seg.item_id)
            if seg_item is None:
                continue
            members.append(CullItem(
                item_id=seg.item_id,
                path=video_path or Path(""),
                kind="video",
            ))
        for snap in snaps:
            snap_item = self._eg.item(snap.item_id)
            if snap_item is None:
                continue
            members.append(CullItem(
                item_id=snap.item_id,
                path=video_path or Path(""),
                kind="photo",
            ))
        if not members:
            return None

        member_colors = [
            cell_color_for_item(
                m.item_id, m.kind, self._phase, phase_states,
                default_state=self._phase_default)
            for m in members
        ]
        color = cluster_color(member_colors)
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
            out.append(GridItem(
                item_id=cell.item_id,
                item_kind=cell.item_kind,
                state=_CELL_TO_THUMB_STATE.get(cell.color),
                visited=bool(cell.visited),
                exported=bool(cell.exported),
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
        """
        is_video_cluster = (cluster.kind == "video")
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
        members: list[GridItem] = []
        for ci in cluster.members:
            path = ci.path if ci.path.is_absolute() else event_root / ci.path
            color = cell_color_for_item(
                ci.item_id, ci.kind, self._phase, phase_states,
                default_state=self._phase_default,
            )
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
                state=_CELL_TO_THUMB_STATE.get(color),
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

    def _close_cluster(self) -> None:
        """Return from the cluster sub-grid to the day grid."""
        if self._mode != "cluster":
            return
        self._mode = "day"
        self._cluster = None
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

    # ── Counts (toolbar progress block) ────────────────────────────────

    def _update_counts(self) -> None:
        """Recompute reviewed/total from the currently-displayed items."""
        self._total = len(self._items)
        self._reviewed = sum(
            1 for it in self._items
            if it.state in ("picked", "skipped", "compare", "mixed")
        )

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

        # The "Back" label flips to "Close cluster" while drilled in
        # so the user understands where Back goes.
        self._back.setText("‹ Close cluster" if self._mode == "cluster" else "‹ Back")

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

        # Clear current cells
        while self._flow.count():
            w = self._flow.itemAt(0).widget()
            self._flow.removeWidget(w)
            w.deleteLater()
        self._thumb_widgets = []
        # Stop any in-flight decode for old cells; populate fresh queue.
        self._thumb_pending.clear()

        # Populate new cells
        for idx, item in enumerate(self._items):
            t = Thumb(
                item.pixmap,
                state=item.state,
                size=_TILE_SIZE,
                cluster_type=item.cluster_type,
                cluster_count=item.cluster_count,
                cluster_split=item.cluster_split,
                visited=item.visited,
                exported=item.exported,
                stamp=item.stamp,
            )
            # Make the thumb keyboard-focusable so the locked §63 keys
            # P/X/Space/C act on whichever Thumb the user last clicked
            # (or Tab-walked to). The thumb doesn't handle the keys
            # itself — they bubble to the page.
            t.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            t.clicked.connect(
                lambda iid=item.item_id, w=t: self._on_thumb_clicked(iid, w)
            )
            self._flow.addWidget(t)
            self._thumb_widgets.append(t)

        # Re-prime the thumbnail loader. Cells that already have a
        # cached pixmap render it instantly; the rest queue up for a
        # background decode. Photos go through the on-disk 256-px
        # thumb tier (~2 ms warm hits); cluster covers reuse the same
        # tier on the cover member's path.
        self._enqueue_thumbnails()

    # ── Click routing ──────────────────────────────────────────────────

    def _on_thumb_clicked(self, item_id: str, widget: Thumb) -> None:
        """A click on a Thumb.

        * Cluster covers expand in place (all modes).
        * Pick / Edit modes: single items emit :sig:`item_activated`
          so the host (MainWindow) drills the user into the Picker /
          Editor.
        * Export mode (spec/68 §3): no drill-in. The click is the
          locked-grammar ``toggle`` verb on this cell — green↔red
          flips, persisted to ``phase_state(phase="edit")``. If the
          item was shipped and the toggle lands on red, the on-disk
          file unlinks via ``delete_exported_file`` and the ship
          status clears.
        """
        widget.setFocus(Qt.FocusReason.MouseFocusReason)
        item = self._find_item(item_id)
        if item is None:
            return
        if item.item_kind == "cluster" and item._cull_cluster is not None:
            self._open_cluster(item._cull_cluster)
            return
        if self._export_mode:
            # The clicked widget IS known — apply the toggle verb to
            # its index directly so we don't depend on Qt's
            # focusWidget() propagation (which doesn't hold in
            # headless tests).
            try:
                idx = self._thumb_widgets.index(widget)
            except ValueError:
                return
            self._apply_verb_at_index(idx, "toggle")
            return
        self.item_activated.emit(item_id)

    def _on_back_clicked(self) -> None:
        if self._mode == "cluster":
            self._close_cluster()
            return
        self.back_requested.emit()

    # ── Locked keymap (spec/63 §4) ────────────────────────────────────

    def keyPressEvent(self, ev: QKeyEvent) -> None:  # noqa: N802 — Qt
        """Locked §4 keymap. Events bubble up from the focused Thumb
        when no Thumb captures them (Thumb has no keyPressEvent
        override). The verb applies to the currently focused Thumb.

        Ctrl+Z (spec/63 §4) undoes the most recent decision on this
        page — a phase_state flip restores the previous state; an
        Export-mode X-on-shipped (the silent file unlink) restores
        the on-disk JPEG + its lineage row + the ``edit_exported``
        flag from the in-memory snapshot captured at the verb."""
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

    @staticmethod
    def _next_state(item_kind: str, cur: Optional[str], verb: str) -> Optional[str]:
        """spec/63 §4: P=Pick, X=Skip, Space=binary toggle,
        C=cycle Pick→Skip→Compare→Pick.

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
            ladder = (STATE_PICKED, STATE_SKIPPED, STATE_CANDIDATE)
            if cur not in ladder:
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
        gateway so segments + snapshots ship in their own lanes."""
        from mira.ui.exported.batch import ExportCell, SnapshotCell

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
            if it.item_id in already_shipped:
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
            photo_cells.append(ExportCell(
                item_id=it.item_id,
                path=Path(src),
                day_number=self._day_number,
            ))
        return photo_cells, segment_rows, snapshot_cells

    def _on_export_clicked(self) -> None:
        """Submit this day's not-yet-shipped green cells to the spec/60
        batch engine via the spec/59 §8 ``BatchExportQueue``.

        The pool is the day's flat photo cells whose state is
        ``picked`` and that aren't already in
        ``exported_item_ids()`` — re-shipping a file the user hasn't
        un-exported is a no-op here (the user toggles red first to
        clear the on-disk file + lineage, then green again to
        re-ship). For video cells, the ship targets are the video's
        picked SEGMENTS (spec/56 — ClipUnits via the slice-4 walker)
        and picked SNAPSHOTS (PhotoUnits over a frame extracted from
        the source video). The engine + queue are locked (spec/68 §4);
        this handler only builds the manifest and enqueues."""
        if self._eg is None or not self._export_mode:
            return
        if self._eg.event_root is None:
            return
        from mira.ui.exported.batch import (
            day_label_for,
            submit_export_batch,
        )

        cells_to_ship, segment_rows, snapshot_cells = (
            self._collect_ship_cells())
        total_units = (
            len(cells_to_ship) + len(segment_rows) + len(snapshot_cells))

        if total_units == 0:
            show_info(
                self,
                tr("Nothing new to ship"),
                tr(
                    "Every green cell on this day is already exported "
                    "— toggle one to red to un-ship, or open another "
                    "day."
                ),
            )
            return

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

        if total_units >= 50 and not confirm(
            self,
            tr("Export {n} item(s)?").replace(
                "{n}", str(total_units)),
            tr(
                "The job runs in the background — the strip below the "
                "menu bar shows progress. You can keep working."
            ),
            primary_text=tr("Export"),
        ):
            return

        day_labels = {self._day_number: day_label_for(
            self._eg, self._day_number)}
        try:
            submit_export_batch(
                self._eg, self.gateway.settings, batch_queue,
                event_name=self._event_name,
                cells=cells_to_ship,
                day_labels=day_labels,
                parent_widget=self,
                segment_rows=segment_rows,
                snapshot_cells=snapshot_cells,
            )
        except Exception as exc:                                   # noqa: BLE001
            log.exception("DaysGridPage: export submit failed")
            show_error(
                self,
                tr("Could not start the export"),
                tr("The batch could not be queued.\n\n{err}").replace(
                    "{err}", str(exc)),
            )

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
        """Queue every photo item for the background proxy builder so
        the screen-copy tier fills quietly while the user is on the
        grid. One SQL pass + a deque append — milliseconds. The builds
        themselves run on the builder thread."""
        if self._eg is None:
            return
        try:
            from mira.ui.media.photo_cache import photo_cache
            event_root = Path(self._eg.event_root) if self._eg.event_root else None
            if event_root is None:
                return
            pairs = [
                (event_root / it.origin_relpath, it.sha256)
                for it in self._eg.items(kind="photo")
                if it.origin_relpath and it.sha256
            ]
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
        if 0 <= idx < len(self._thumb_widgets):
            self._thumb_widgets[idx].setPixmap(pixmap)
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
