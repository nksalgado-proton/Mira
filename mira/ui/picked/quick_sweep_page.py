"""F-018 — Quick Sweep.

The capture-phase triage surface. The user is not curating; they're
yanking the obvious garbage (closed eyes, lens cap on, wrong
settings) out of a fresh SD card before the bake step writes the
keepers into ``Original Media/``.

Sibling to :class:`ui.culler.ingest_culler_page.IngestPickerPage`,
**not** a subclass — the two have such different UX premises that
sharing inheritance would create new debt instead of removing it:

* **Default = Keep** (opposite of Cull/Select). A mistake of
  inattention preserves the photo; the user must actively discard.
* **Reuses the main Cull's days panel + Day Grid** (Nelson
  2026-06-05). The bucket scanner clusters the card into day → bucket
  shape; :func:`build_fast_days` wraps that as the same
  ``PickDay`` / ``CullCell`` shapes
  :class:`mira.ui.base.bucket_navigator.BucketNavigator` +
  :class:`mira.ui.base.day_grid_view.DayGridView` already render
  — no new navigator widget, no contact-sheet grid, no bucket-step
  shortcuts.
* **Single-day bypass** — if the source has photos from one calendar
  day, the days panel is skipped and the Day Grid opens directly
  (both at capture-flow ingest AND on the sidebar standalone use).
* **K → D → C → K** state cycle, same as main Cull. The Compare state
  is preserved here for the user who wants to flag "look at this
  again later" during fast triage; at save time Compare items count
  as **kept** (they get copied; the real decision lands in main Cull).
* **Stripped chrome.** Top bar: Back + position chip + K/D pill +
  Save. Bottom bar: ← Previous / Fullscreen / Next →. No peaking, no
  zoom, no classification, no bulk K/D buttons.
* **Videos:** ``QMediaPlayer`` + ``QVideoWidget`` + scrubbable
  timeline + play/pause. Same K/D pill as photos. No marker creation,
  no clip carving — that's Process-phase work.
* **Speed-as-first-class-requirement.** Mouse-wheel one click =
  one photo, forward + backward, no decode lag (we use the embedded
  thumbnail via the same ``_load_pixmap`` that the rich canvas uses).
  Arrow keys same. Full-screen single-key toggle (``F``).

Output: the kept set (Keep + Compare) is handed back via the
:attr:`saved` signal (a ``set[Path]``). The capture orchestrator
wires this to the existing offload pipeline's
``OffloadConfig.included_names`` filter — the bake + copy
infrastructure is unchanged.

Spec: ``docs/18-culler-spec.md`` §"Quick Sweep"; rebuild seam: spec/13 §3.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Sequence

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QCursor,
    QKeyEvent,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.cull_state import STATE_CANDIDATE, STATE_DISCARDED as STATE_SKIPPED, STATE_KEPT as STATE_PICKED
from mira.settings.repo import SettingsRepo
# Rebuild-vocabulary state values used by the rendering pipeline
# (``cell_color_for_item`` / ``_phase_state_map`` filter against these).
# The page stores legacy values internally (``"kept"`` / ``"discarded"`` /
# ``"candidate"``) for compatibility with the surrounding capture-flow
# code; ``_state_for`` translates on the way out so the renderer sees
# the rebuild values it expects. Issue surfaced 2026-06-09: without
# translation, every state change vanishes between the page and the
# Day Grid border colour.
from mira.picked.status import (
    STATE_PICKED as _RB_STATE_PICKED,
    STATE_SKIPPED as _RB_STATE_SKIPPED,
    STATE_CANDIDATE as _RB_STATE_CANDIDATE,
)

_LEGACY_TO_REBUILD = {
    STATE_PICKED: _RB_STATE_PICKED,
    STATE_SKIPPED: _RB_STATE_SKIPPED,
    STATE_CANDIDATE: _RB_STATE_CANDIDATE,
}
# Reverse — ComparePage speaks rebuild values; the page stores legacy.
_REBUILD_TO_LEGACY = {v: k for k, v in _LEGACY_TO_REBUILD.items()}
from core.fresh_source import SourceItem
from core.video_discovery import VIDEO_EXTENSIONS
from mira.picked.exif_compare import caption_html
from mira.picked.quick_sweep_buckets import (
    build_fast_days,
    fast_day_grid_cells,
    refresh_day_statuses,
    _phase_state_map,
)
from mira.picked.model import CullCell, CullCluster, CullItem, PickDay
from mira.picked.status import (
    CellColor, cell_color_for_item, cluster_color)
from mira.ui.base.bucket_navigator import (
    BucketNavigator,
    BucketNavigatorConfig,
)
from mira.ui.base.day_grid_cell import CellRenderData
from mira.ui.base.day_grid_view import DayGridView
from mira.ui.base.progress import run_with_progress
from mira.ui.base.status_breakdown import StatusLabels
from mira.ui.base.surface import (
    BasePickSurface,
    back_button,
    feature_toggle,
    help_button,
    info_label,
    kd_pill,
    populate_nav_row,
    primary_action,
    set_transport_playing,
    transport_button,
)
from mira.ui.i18n import tr
from mira.ui.media.image_loader import load_pixmap as _load_pixmap
from mira.ui.media.photo_overlay import PhotoExposureOverlay
from mira.ui.media.photo_viewport import PhotoViewport, ViewportItem

log = logging.getLogger(__name__)


# Per-Cull config for the Quick Sweep's days panel.
# * ``day_grid_mode=True`` — day click emits ``day_activated``; no bucket list.
# * Status labels include Compare (orange) since the K/D/C cycle is in scope.
# * No batch ops — bulk K/D buttons were dropped per Nelson 2026-06-05.
# * Heading speaks the Quick Sweep's job (triage, not cull).
FAST_CULL_CONFIG = BucketNavigatorConfig(
    day_list_heading_template="{n} day(s) — pick where to triage",
    return_button_label="Back",
    return_button_tooltip="Leave the Quick Sweep — any in-progress marks are discarded.",
    # Nelson 2026-06-09 — same "Start a new pass…" affordance the
    # main Picker has (spec/32 §2.10 port). Quick Sweep tracks visited
    # in-memory (no event.db) so the button clears the session sets;
    # decisions stay intact.
    show_clear_marks_button=True,
    status_labels=StatusLabels(
        kept="Keep",
        candidate="Compare",
        discarded="Discard",
        show_candidate=True,
        # Default = Keep at Quick Sweep. Untouched cells fold to Keep so the
        # status bar reads the way the user expects.
        merge_untouched=True,
        merge_untouched_into="picked",
    ),
    batch_ops=[],   # no bulk K/D buttons in Quick Sweep
    day_grid_mode=True,
)


# Lazy thumbnail loader — ported from PickPage's pattern (Nelson
# 2026-06-09: "do not reinvent the wheel"). ``_THUMBS_PER_TICK`` decodes
# bounded per timer tick so the main thread stays responsive;
# ``_THUMB_TIMER_MS`` is the interval. Cache is unbounded but session-
# scoped — cleared in ``load()`` whenever a fresh card is opened.
# Embedded-thumbnail pixmaps run ~1-3 MB each so even a 1000-photo card
# stays under 3 GB; the prior bounded LRU caused per-tick thrash that
# stalled cluster sub-grid thumbs beyond cell ~24 (Nelson eyeball
# 2026-06-09).
_THUMBS_PER_TICK = 4
_THUMB_TIMER_MS = 20

# Cycle order: K → D → C → K. Default state is K (Quick Sweep convention,
# Nelson 2026-05-25 freeze; Compare added 2026-06-05).
_STATE_CYCLE = (STATE_PICKED, STATE_SKIPPED, STATE_CANDIDATE)


class QuickSweepPage(QWidget):
    """The Quick Sweep page. Call :meth:`load` before showing.

    Signals:
      * ``saved(set[Path])`` — the user clicked Save with a kept set
        (used by the capture orchestrator to drive offload). Compare
        items count as kept (Nelson 2026-06-05).
      * ``cancelled()`` — the user backed out / hit Esc / clicked the
        days-panel return button.
    """

    saved = pyqtSignal(set)
    cancelled = pyqtSignal()

    # Top-level stack pages.
    _NAV = 0           # BucketNavigator (days panel)
    _DAY_GRID = 1      # DayGridView (one day's cells)
    _VIEWER = 2        # photo / video single-item view
    _CLUSTER_GRID = 3  # DayGridView reused as the cluster expansion sub-grid
                       # (spec/52 slice C, Nelson 2026-06-09)
    _COMPARE = 4       # ComparePage — side-by-side compare of Compare-state
                       # photos (ported from the Picker, Nelson 2026-06-12)

    def __init__(
        self, parent: Optional[QWidget] = None, *,
        browse_mode: bool = False,
    ) -> None:
        super().__init__(parent)
        # Browse mode (Nelson 2026-05-31): the same surface, stripped of every K/D
        # control — a read-only photo+video browser (the plan editor's per-row
        # Browse button uses this). Hides K/D pill + Save; ignores K/D keys.
        # Always single-day (caller scopes to one day's items), so the days panel
        # is naturally skipped via the single-day-bypass path.
        self._browse_mode = browse_mode

        # The flat sequence of items the single-item viewer walks through. In
        # the new model this is the CURRENT DAY's items in cell order (the
        # viewer is day-scoped). Multi-day works because the days panel /
        # DayGridView switches days; when the user opens a cell the viewer is
        # populated with that day's items.
        self._items: List[SourceItem] = []
        # The whole card's source items, in original order (used to rebuild the
        # day list and to populate a day on demand).
        self._all_items: List[SourceItem] = []
        # Per-path K/D state. Missing key == STATE_PICKED (Quick Sweep's default).
        # Materialized to STATE_PICKED on load so callers can read self._state
        # directly without thinking about the default.
        self._state: dict[Path, str] = {}
        # The day grid model — built in load(), refreshed when state changes.
        self._days: List[PickDay] = []
        self._current_day_number: Optional[int] = None
        self._current_day_cells: List[CullCell] = []
        # Pos within the current day (the single-item viewer cursor).
        self._index = 0

        # Cluster sub-grid state (spec/52 slice C, Nelson 2026-06-09).
        # Set when the user centre-clicks a cluster cell on the day grid;
        # carries the cluster + its per-member sub-grid cells so border /
        # batch / state-cycle handlers know which cells to refresh.
        self._current_cluster: Optional[CullCluster] = None
        self._current_cluster_cells: List[CullCell] = []
        # Which stack page launched the single-item viewer — drives the
        # Back / Esc routing so the viewer returns to the right level.
        self._viewer_came_from: int = self._DAY_GRID

        # Default state for un-decided items — user-tunable via Settings →
        # Picker → "Default state for untouched items (Quick Sweep)"
        # (spec/52 Quick Sweep redesign, Nelson 2026-06-09). The page
        # stores the legacy value internally (drives ``self._state``
        # seeding + ``_STATE_CYCLE`` semantics); ``_renderer_default`` is
        # the rebuild value handed to the renderer's ``default_state``
        # argument. Re-read on every ``load()`` so a settings change
        # takes effect on the next card.
        self._legacy_default: str = STATE_PICKED          # "kept"
        self._renderer_default: str = _RB_STATE_PICKED    # "picked"

        # Unbounded session-scoped pixmap cache + FIFO decode queue —
        # mirrors PickPage._thumb_pixmap_cache / _thumb_pending /
        # _thumb_timer. Once a photo decodes, its pixmap is retained
        # for the session so day-switch + cluster-expand round trips
        # are instant; ``load()`` resets state on a fresh card.
        self._thumb_pixmap_cache: dict[Path, QPixmap] = {}
        self._thumb_pending: list[tuple[str, str, Path]] = []   # (target, idx_str, path)
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setInterval(_THUMB_TIMER_MS)
        self._thumb_timer.timeout.connect(self._load_some_thumbs)
        self._fullscreen = False

        # Visited tracking (Nelson 2026-06-09 — port of PickPage spec/32 §2.10).
        # In-memory only: Quick Sweep has no event.db, so the "Start a new
        # pass…" button just clears these sets (decisions in self._state
        # are preserved). ``_visited_paths`` is keyed by CullItem.item_id
        # (the path string used as an id pre-ingest); ``_visited_clusters``
        # is keyed by bucket_key.
        self._visited_paths: set[str] = set()
        self._visited_clusters: set[str] = set()

        # Video machinery now lives in the embedded PhotoViewport
        # (spec/63 slice 4 — arm-on-landing).

        self._build_ui()
        if self._browse_mode:
            self._enter_browse_mode()
        self._install_keyboard_focus()

    def _enter_browse_mode(self) -> None:
        """Hide every Keep/Discard control — a read-only photo/video browser."""
        # Hide the STATE_BAR region entirely (it only carries the K/D pill)
        # and the Export button on the TOP_BAR.
        self._viewer.set_region_visible("state_bar", False)
        self._export_btn.setVisible(False)

    # ── UI construction ─────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Top-level stack: nav → day grid → viewer.
        self._stack = QStackedWidget()

        # NAV — the same days panel PickPage uses.
        self._nav = BucketNavigator(config=FAST_CULL_CONFIG)
        self._nav.day_activated.connect(self._on_day_activated)
        self._nav.back_requested.connect(self._on_nav_back)
        self._nav.clear_marks_requested.connect(self._on_clear_marks)
        self._stack.addWidget(self._nav)               # _NAV = 0

        # DAY_GRID — flat Day Grid for one day; cells border-click cycle
        # K/D/C and centre-click open the single-item viewer OR (cluster
        # cells, spec/52 slice C) the cluster sub-grid. ``show_pick_all_
        # button`` / ``show_skip_all_button`` light up the day-scope batch
        # buttons on the top bar (Nelson 2026-06-09).
        self._day_grid = DayGridView(
            show_pick_all_button=True,
            show_skip_all_button=True,
            show_clear_marks_button=True,
            show_compare_button=True,
        )
        self._day_grid.back_requested.connect(self._on_day_grid_back)
        self._day_grid.cell_activated.connect(self._on_day_cell_activated)
        self._day_grid.cell_border_clicked.connect(self._on_day_cell_border)
        self._day_grid.clear_marks_requested.connect(self._on_clear_marks)
        self._day_grid.pick_all_requested.connect(self._on_day_pick_all)
        self._day_grid.skip_all_requested.connect(self._on_day_skip_all)
        self._day_grid.compare_requested.connect(
            lambda: self._on_compare_requested(self._DAY_GRID))
        self._stack.addWidget(self._day_grid)          # _DAY_GRID = 1

        # VIEWER — refactored 2026-06-04 to use BasePickSurface (spec/42).
        self._viewer = self._build_viewer()
        self._stack.addWidget(self._viewer)            # _VIEWER = 2

        # CLUSTER_GRID — second DayGridView instance (spec/52 slice C,
        # Nelson 2026-06-09). Mirror PickPage's cluster sub-grid pattern:
        # one widget, fed with the cluster's member cells when the user
        # centre-clicks a cluster cell on the day grid. Same batch buttons
        # so the user can Pick-all / Skip-all within the cluster.
        self._cluster_grid = DayGridView(
            show_pick_all_button=True,
            show_skip_all_button=True,
            show_clear_marks_button=True,
            show_compare_button=True,
        )
        self._cluster_grid.back_requested.connect(self._on_cluster_back)
        self._cluster_grid.cell_activated.connect(self._on_cluster_cell_activated)
        self._cluster_grid.cell_border_clicked.connect(self._on_cluster_cell_border)
        self._cluster_grid.clear_marks_requested.connect(self._on_clear_marks)
        self._cluster_grid.pick_all_requested.connect(self._on_cluster_pick_all)
        self._cluster_grid.skip_all_requested.connect(self._on_cluster_skip_all)
        self._cluster_grid.compare_requested.connect(
            lambda: self._on_compare_requested(self._CLUSTER_GRID))
        self._stack.addWidget(self._cluster_grid)      # _CLUSTER_GRID = 3

        # COMPARE — the dedicated side-by-side compare grid, same surface
        # the Picker uses (Nelson 2026-06-12). Quick Sweep is pre-ingest
        # so it drives ComparePage with eg=None and persists the K/D
        # decisions into its in-memory ledger via ``state_changed``.
        from mira.ui.picked.compare_page import ComparePage
        self._compare_page = ComparePage()
        self._compare_page.state_changed.connect(self._on_compare_state_changed)
        self._compare_page.quit_requested.connect(self._on_compare_quit)
        self._stack.addWidget(self._compare_page)      # _COMPARE = 4
        self._compare_came_from = self._DAY_GRID

        outer.addWidget(self._stack)

    def _build_viewer(self) -> BasePickSurface:
        """The single-item viewer (photo + video) composed on BasePickSurface.

        spec/42 Alternative B refactor (2026-06-04):
        - K/D pill moved from TOP_BAR (small button) to STATE_BAR
          (full-width clickable bar — Nelson's "large horizontal coloured
          bar").
        - Save button renamed *Export →* with canonical ``PrimaryAction``
          role + position shared with other surfaces' Export action.
        - Video transport (play / timeline / time) moved from inside the
          video host widget to the BasePickSurface COMPACT_ROW region; shown only
          for videos.
        - TOOLS region hidden — Quick Sweep has no slider/tool affordances.
        - NAV uses canonical ``populate_nav_row`` (no bucket-edge buttons,
          since Day Grid IS the navigation level above); Fullscreen toggle
          sits in the NAV centre slot.
        """
        surface = BasePickSurface()
        surface.setObjectName("FastPickerViewer")

        # ── TOP_BAR — back · position · info · stretch · Export →
        self._back_btn = back_button()
        self._back_btn.setToolTip(tr("Return to the day grid. (Esc)"))
        self._back_btn.clicked.connect(self._on_viewer_back)
        surface.top_bar.layout().addWidget(self._back_btn)

        self._position_label = QLabel("")
        self._position_label.setObjectName("PositionLabel")
        self._position_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        surface.top_bar.layout().addWidget(self._position_label)

        self._info_label = info_label("")
        self._info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        surface.top_bar.layout().addWidget(self._info_label, stretch=1)

        self._export_btn = primary_action(tr("Export →"))
        self._export_btn.setToolTip(tr(
            "Hand the kept set back to the capture flow. The offload step "
            "then physically copies the survivors into Original Media/. "
            "Compare-marked items count as kept (you can re-decide them "
            "in the main Cull)."
        ))
        self._export_btn.clicked.connect(self._on_save)
        surface.top_bar.layout().addWidget(self._export_btn)

        # The shared Help control (Nelson 2026-06-12 UI round — every
        # photo/video surface gets a top-right "?"). F1/? also opens it.
        self._help_btn = help_button()
        self._help_btn.setToolTip(tr("Keyboard shortcuts  (F1)"))
        self._help_btn.clicked.connect(self._show_shortcuts)
        surface.top_bar.layout().addWidget(self._help_btn)

        # ── STATE_BAR — hidden (spec/42, Nelson 2026-06-04).
        # State is now shown as a coloured border on the BasePickSurface
        # MEDIA host (``QWidget#MediaHost[state="…"]``) — green=Keep,
        # red=Discard, orange=Compare. Space bar OR clicking the
        # photo's border cycles the state. The dedicated K/D pill row
        # is no longer needed; this saves ~40 px of vertical chrome.
        #
        # The ``_state_pill`` attribute is kept (it's referenced by
        # tests + the page's own state logic in ``_toggle_state`` /
        # ``_sync_state_pill``) but the widget is never displayed.
        self._state_pill = kd_pill()
        self._state_pill.setText(tr("✓ Pick"))
        self._state_pill.setProperty("state", "picked")
        self._state_pill.clicked.connect(self._toggle_state)
        # Canonical media-border click (BasePickSurface) cycles state too —
        # same affordance as Space and the pill click. Matches the wiring
        # in PickPhotoSurface.
        surface.media_border_clicked.connect(self._toggle_state)
        surface.set_region_visible("state_bar", False)

        # ── MEDIA — the one display engine (spec/63 slice 4): photos,
        # video (arm-on-landing), nav and the locked key grammar all
        # live in the embedded PhotoViewport. The surface keeps the
        # chrome (state pill/border, info line, transport row) and the
        # K/D/C ledger, reacting to the viewport's verbs + signals.
        self._viewport = PhotoViewport()
        self._expo_overlay = PhotoExposureOverlay(self._viewport)
        vp = self._viewport
        vp.current_changed.connect(self._on_viewport_current_changed)
        vp.pick_requested.connect(lambda: self._verb_set_state(STATE_PICKED))
        vp.skip_requested.connect(lambda: self._verb_set_state(STATE_SKIPPED))
        vp.toggle_requested.connect(self._verb_toggle_pick_skip)
        vp.cycle_requested.connect(self._verb_cycle_state)
        vp.fullscreen_requested.connect(self._toggle_fullscreen)
        vp.back_requested.connect(self._on_viewport_back)
        vp.video_playing_changed.connect(self._on_video_playing_changed)
        vp.video_position_changed.connect(self._on_position)
        vp.video_duration_changed.connect(self._on_duration)
        surface.set_media(self._viewport)

        # ── COMPACT_ROW — video transport (play / timeline / time)
        # Hidden by default; shown for video items in
        # ``_on_viewport_current_changed``.
        self._play_btn = transport_button(tr("Play / pause the video"))
        self._play_btn.clicked.connect(self._viewport.video_toggle_play)
        surface.compact_row.layout().addWidget(self._play_btn)
        self._timeline = QSlider(Qt.Orientation.Horizontal)
        self._timeline.setObjectName("VideoTimeline")
        self._timeline.setRange(0, 0)
        self._timeline.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._timeline.sliderMoved.connect(self._viewport.video_seek)
        surface.compact_row.layout().addWidget(self._timeline, stretch=1)
        self._time_label = QLabel("0:00 / 0:00")
        surface.compact_row.layout().addWidget(self._time_label)
        surface.set_region_visible("compact_row", False)
        self._video_duration_ms = 0

        # ── TOOLS hidden (Quick Sweep has no slider/tool affordances)
        surface.set_region_visible("tools", False)

        # ── NAV — Previous · (Fullscreen · Full Resolution View) · Next
        # (no bucket-step since Day Grid IS the navigation level above).
        # The labelled lens button replaces the corner 🔍 here, the same
        # Picker treatment (Nelson 2026-06-12 — "it has a Full Screen
        # button, but no Full Resolution").
        self._fullscreen_btn = feature_toggle(tr("Full Screen"))
        self._fullscreen_btn.setToolTip(tr(
            "Use the whole screen for sweeping  (F / F11)"))
        self._fullscreen_btn.clicked.connect(self._toggle_fullscreen)
        self._fullres_btn = QPushButton(tr("Full Resolution"))
        self._fullres_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._fullres_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._fullres_btn.setToolTip(tr(
            "Inspect this frame at full resolution — peaking, true 1:1 "
            "zoom  (F10)"))
        self._fullres_btn.clicked.connect(self._viewport.truth_requested.emit)
        self._viewport.set_corner_inspect_visible(False)
        centre = QWidget()
        centre_row = QHBoxLayout(centre)
        centre_row.setContentsMargins(0, 0, 0, 0)
        centre_row.setSpacing(8)
        centre_row.addWidget(self._fullscreen_btn)
        centre_row.addWidget(self._fullres_btn)
        nav = populate_nav_row(
            surface, with_buckets=False, centre_widget=centre)
        self._prev_btn = nav.prev
        self._prev_btn.setToolTip(tr(
            "Previous item in this day — photo or video  (←  or scroll up)"))
        self._prev_btn.clicked.connect(self._go_prev)
        self._next_btn = nav.next
        self._next_btn.setToolTip(tr(
            "Next item in this day — photo or video  (→  or scroll down)"))
        self._next_btn.clicked.connect(self._go_next)
        return surface

    def _install_keyboard_focus(self) -> None:
        """The page owns the keyboard for the GRID levels (Speed-is-
        King): buttons never take focus. The VIEWPORT is the one
        exception — inside the viewer it owns the locked key grammar
        (spec/63 §4), so it must be focusable. The original loop
        NoFocus'd it too, silently turning every later
        ``_viewport.setFocus()`` into a no-op — the whole grammar
        (F10 included) was dead in the Quick Sweep viewer (Nelson
        2026-06-12)."""
        for w in self.findChildren(QWidget):
            if w is self._viewport or self._viewport.isAncestorOf(w):
                continue
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def focusNextPrevChild(self, nxt: bool) -> bool:  # noqa: N802
        return False

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # Focus follows the level: inside the viewer the VIEWPORT owns
        # the locked grammar — a blanket self.setFocus() here was the
        # second half of the dead-keys bug (it stole focus right back
        # from the viewport after load).
        if self._stack.currentIndex() == self._VIEWER:
            self._viewport.setFocus()
        else:
            self.setFocus()

    # ── Public API ──────────────────────────────────────────────────

    def load(self, items: Sequence[SourceItem]) -> bool:
        """Load source items into the Quick Sweep.

        * **Browse mode** — single-day flat viewer; items are shown in
          chronological order with no buckets / no day grid.
        * **Single source day** — days panel is skipped; Day Grid opens
          immediately on the one day. ``Back`` from the grid cancels.
        * **Multi-day** — days panel opens first; day click → Day Grid.

        Returns ``False`` if there are no items to show."""
        items_sorted = sorted(
            (it for it in items),
            key=lambda it: (it.timestamp is None,
                            it.timestamp or 0, it.path.name),
        )
        if not items_sorted:
            self._items = []
            self._all_items = []
            self._state = {}
            self._days = []
            return False
        # Read the configurable Quick Sweep default state. Defaults to
        # 'picked' (Quick Sweep's permissive contract); a user can flip
        # to 'skipped' for a stricter "actively pick keepers" flow.
        self._read_default_state_setting()
        self._all_items = list(items_sorted)
        self._state = {it.path: self._legacy_default for it in items_sorted}
        self._thumb_pixmap_cache.clear()
        self._thumb_pending.clear()
        self._thumb_timer.stop()
        # Fresh card → fresh visited state.
        self._visited_paths.clear()
        self._visited_clusters.clear()

        if self._browse_mode:
            # Read-only single-day flat viewer — no bucketing, no days
            # panel, no Day Grid, no progress dialog.
            self._items = list(items_sorted)
            self._days = []
            self._current_day_number = None
            self._current_day_cells = []
            self._index = 0
            self._stack.setCurrentIndex(self._VIEWER)
            self._sync_viewport_items(0)
            self._viewport.setFocus()
            return True

        # Cull mode — build the days panel model behind the load progress.
        self._days = self._build_days(items_sorted)
        if not self._days:
            # Defensive fallback — should never happen if items_sorted is
            # non-empty, but if it does, show a single synthesised day.
            return False

        if len(self._days) == 1:
            # Single-day bypass (Nelson 2026-06-05).
            self._open_day(self._days[0].day_number)
        else:
            self._nav.set_days(self._days)
            self._stack.setCurrentIndex(self._NAV)
            self._nav.setFocus()
        return True

    def kept_paths(self) -> set[Path]:
        """The set of paths the user marked Keep OR Compare. Read after
        the ``saved`` signal fires (or any time — the K/D/C state is live).

        Compare items count as kept (Nelson 2026-06-05): at Fast-Picker
        triage Compare means "I'll decide this again in the main Cull",
        which only makes sense if the item is copied through to
        ``Original Media/``. The offload pipeline sees these as kept."""
        return {
            p for p, s in self._state.items()
            if s in (STATE_PICKED, STATE_CANDIDATE)
        }

    # ── Days panel + Day Grid wiring ───────────────────────────────

    def _build_days(self, items_sorted) -> List[PickDay]:
        """Run the shared progress dialog over the bucket scan + build the
        Days panel model. Falls back to an empty list if cancelled."""
        def work(report):
            def on_day(done, total, label, n):
                report(done, total,
                       tr("Reading {label} — {n} item(s)…")
                       .replace("{label}", label).replace("{n}", str(n)))
            return build_fast_days(
                items_sorted, progress=on_day, state_for=self._state_for)
        ok, days = run_with_progress(
            self, tr("Reading the card…"), work,
            label=tr("Reading the card…"))
        if not ok or not days:
            return []
        return days

    def _read_default_state_setting(self) -> None:
        """Pull ``quick_sweep_default_state`` out of Settings and refresh
        ``_legacy_default`` + ``_renderer_default``. Called from ``load()``
        so a Settings change takes effect on the next card. Falls back to
        the permissive Quick Sweep default (Pick) on any read error — a
        display surface must never crash."""
        try:
            value = SettingsRepo().load().quick_sweep_default_state
        except Exception:  # noqa: BLE001 — display surface; never crash
            value = "picked"
        if value == "skipped":
            self._legacy_default = STATE_SKIPPED
            self._renderer_default = _RB_STATE_SKIPPED
        else:
            self._legacy_default = STATE_PICKED
            self._renderer_default = _RB_STATE_PICKED

    def _state_for(self, path: Path) -> str:
        """The page's in-memory K/D map exposed for ``build_fast_days`` and
        ``fast_day_grid_cells``.

        The page stores legacy ``core.cull_state`` values internally
        (``"kept"`` / ``"discarded"`` / ``"candidate"``), but the renderer's
        ``_phase_state_map`` filter expects the rebuild-vocabulary values
        from :mod:`mira.picked.status` (``"picked"`` / ``"skipped"`` /
        ``"candidate"``). Translate here so the renderer actually sees state
        changes (without this every border-click would silently drop on the
        floor)."""
        raw = self._state.get(path, self._legacy_default)
        return _LEGACY_TO_REBUILD.get(raw, raw)

    def _items_for_day(self, day_number: Optional[int]) -> List[SourceItem]:
        """All ``SourceItem`` objects whose capture date matches the day
        ``day_number`` was synthesised from. Lookup is by path — the cells
        carry path-strings as item_id (no gateway, no sha)."""
        day = next(
            (d for d in self._days if d.day_number == day_number), None)
        if day is None:
            return []
        wanted = {
            Path(ci.item_id)
            for b in day.buckets for ci in b.items
        }
        # Preserve original chronological order from the sorted card.
        return [it for it in self._all_items if it.path in wanted]

    def _on_day_activated(self, day_number) -> None:
        """Days panel day card clicked → open the Day Grid for that day."""
        self._open_day(day_number)

    def _open_day(self, day_number) -> None:
        """Build and show the Day Grid for ``day_number``."""
        day = next(
            (d for d in self._days if d.day_number == day_number), None)
        if day is None:
            return
        self._current_day_number = day_number
        self._current_day_cells = fast_day_grid_cells(
            day, self._state_for,
            visited_for=self._visited_paths.__contains__,
            cluster_visited_for=self._visited_clusters.__contains__,
        )
        self._items = self._items_for_day(day_number)
        # Map item_id (path string) → index in self._items so the cell-grid
        # → viewer transition can land at the right offset.
        render = [
            CellRenderData(cell=c, thumbnail=self._maybe_cached_pixmap(c))
            for c in self._current_day_cells
        ]
        self._day_grid.set_header(day.label)
        self._day_grid.set_cells(render)
        self._stack.setCurrentIndex(self._DAY_GRID)
        self._day_grid.setFocus()
        # Lazy thumbnail decode for cells that don't yet have one.
        self._enqueue_thumbnails(self._day_grid, self._current_day_cells)

    def _maybe_cached_pixmap(self, cell: CullCell) -> Optional[QPixmap]:
        if cell.item_id is None:
            return None
        try:
            p = Path(cell.item_id)
        except (TypeError, ValueError):
            return None
        cached = self._thumb_pixmap_cache.get(p)
        if cached is not None and not cached.isNull():
            return cached
        return None

    def _enqueue_thumbnails(self, view, cells) -> None:
        """Queue item cells (photo only) for lazy thumbnail decoding —
        the unified loader pattern ported from PickPage. Cache hits get
        applied immediately so day-switch / cluster-expand round trips
        are instant for previously-decoded photos; misses go on the FIFO
        queue and the timer picks them up. Videos skip (the Day Grid
        renders the ▶ placeholder overlay)."""
        target = "day" if view is self._day_grid else "cluster"
        for i, c in enumerate(cells):
            if c.is_cluster or c.item_id is None:
                continue
            try:
                p = Path(c.item_id)
            except (TypeError, ValueError):
                continue
            if self._is_video(p):
                continue
            cached = self._thumb_pixmap_cache.get(p)
            if cached is not None and not cached.isNull():
                view.set_cell_thumbnail(i, cached)
                continue
            self._thumb_pending.append((target, str(i), p))
        if self._thumb_pending and not self._thumb_timer.isActive():
            self._thumb_timer.start()

    def _load_some_thumbs(self) -> None:
        """Pop up to ``_THUMBS_PER_TICK`` items from the queue, decode,
        cache, and apply to the right view. Stops itself when the queue
        is empty. Holds while either grid is still chunking cells so the
        chunked builder isn't starved of main-thread time (PickPage perf
        rule, Nelson 2026-06-05)."""
        if (self._day_grid.pending_cell_count() > 0
                or self._cluster_grid.pending_cell_count() > 0):
            return
        done = 0
        while self._thumb_pending and done < _THUMBS_PER_TICK:
            target, idx_str, path = self._thumb_pending.pop(0)
            try:
                idx = int(idx_str)
            except ValueError:
                continue
            view = self._day_grid if target == "day" else self._cluster_grid
            pm = _load_pixmap(path)
            if pm is None or pm.isNull():
                continue
            self._thumb_pixmap_cache[path] = pm
            view.set_cell_thumbnail(idx, pm)
            done += 1
        if not self._thumb_pending:
            self._thumb_timer.stop()

    # ── Day Grid clicks ────────────────────────────────────────────

    def _on_day_cell_activated(self, idx: int) -> None:
        """Centre click → cluster cells open the expansion sub-grid;
        item cells open in the single-item viewer.

        Marks the cell visited (PickPage spec/32 §2.10 port) before
        switching surfaces so the ✓ tick is already in place when the
        user Backs out."""
        if not (0 <= idx < len(self._current_day_cells)):
            return
        cell = self._current_day_cells[idx]
        if cell.is_cluster and cell.cluster is not None:
            self._visited_clusters.add(cell.cluster.bucket_key)
            self._refresh_cell(idx)
            self._open_cluster(cell.cluster)
            return
        if cell.item_id is None:
            return
        self._visited_paths.add(cell.item_id)
        self._refresh_cell(idx)
        # Ensure the viewer's item list matches the day (it may have been
        # swapped to cluster members during a previous cluster session).
        self._items = self._items_for_day(self._current_day_number)
        # Find the matching item index in self._items.
        path = Path(cell.item_id)
        for i, it in enumerate(self._items):
            if it.path == path:
                self._index = i
                self._viewer_came_from = self._DAY_GRID
                self._stack.setCurrentIndex(self._VIEWER)
                self._sync_viewport_items(i)
                self._viewport.setFocus()
                return

    def _on_day_cell_border(self, idx: int) -> None:
        """Border click on the day grid — cycle K → D → C → K for an
        item cell, or bulk-cycle every member of a cluster cell so the
        aggregate state moves uniformly."""
        if not (0 <= idx < len(self._current_day_cells)):
            return
        cell = self._current_day_cells[idx]
        if cell.is_cluster and cell.cluster is not None:
            # Spec/32 main-Cull convention: border-click on a cluster
            # cycles every member's state together. Quick Sweep follows
            # the same rule so a user can wipe a whole burst to Skip with
            # one click without entering the sub-grid.
            self._cycle_cluster(cell.cluster)
            self._refresh_cell(idx)
            return
        if cell.item_id is None:
            return
        path = Path(cell.item_id)
        self._cycle_state(path)
        self._refresh_cell(idx)

    # ── Cluster sub-grid (spec/52 slice C, Nelson 2026-06-09) ──────

    def _open_cluster(self, cluster: CullCluster) -> None:
        """Open the cluster's members in the cluster sub-grid.

        Builds per-member :class:`CullCell` data with live colours from
        the in-memory K/D map, feeds it to the second :class:`DayGridView`,
        and switches the stack to ``_CLUSTER_GRID``. Mirrors PickPage's
        ``_open_cluster`` pattern — the sub-grid IS the same widget the
        day grid uses, just fed a different cell list."""
        self._current_cluster = cluster
        self._current_cluster_cells = self._build_cluster_member_cells(cluster)
        render = [
            CellRenderData(
                cell=c,
                thumbnail=self._maybe_cached_pixmap(c),
            )
            for c in self._current_cluster_cells
        ]
        kind_label = {
            "burst": tr("Burst"),
            "focus_bracket": tr("Focus bracket"),
            "exposure_bracket": tr("Exposure bracket"),
            "repeat": tr("Repeat"),
        }.get(cluster.kind, tr("Cluster"))
        header = (
            tr("{kind} — {n} photo(s)")
            .replace("{kind}", kind_label)
            .replace("{n}", str(cluster.count))
        )
        self._cluster_grid.set_header(header)
        self._cluster_grid.set_cells(render)
        self._stack.setCurrentIndex(self._CLUSTER_GRID)
        self._cluster_grid.setFocus()
        # Lazy thumbnail decode for any sub-grid cells whose pixmap
        # isn't already in the session cache.
        self._enqueue_thumbnails(self._cluster_grid, self._current_cluster_cells)

    def _build_cluster_member_cells(
        self, cluster: CullCluster,
    ) -> List[CullCell]:
        """Per-member :class:`CullCell` list for the cluster sub-grid.
        Colours come from the page's in-memory K/D map (no gateway).
        The default state honours the user-tunable
        ``quick_sweep_default_state`` setting."""
        item_ids = [ci.item_id for ci in cluster.members]
        phase_states = _phase_state_map(item_ids, self._state_for)
        cells: List[CullCell] = []
        for ci in cluster.members:
            color = cell_color_for_item(
                ci.item_id, ci.kind, "pick", phase_states,
                default_state=self._renderer_default,
            )
            cells.append(CullCell(
                end_time=(ci.capture_time_corrected or ""),
                color=color,
                item_id=ci.item_id,
                item_kind=ci.kind,
                visited=ci.item_id in self._visited_paths,
            ))
        return cells

    def _items_for_cluster(
        self, cluster: CullCluster,
    ) -> List[SourceItem]:
        """``SourceItem`` list scoped to the cluster's members, preserving
        the cluster's chronological order. Used as the viewer's ``_items``
        when the user opens a member from the sub-grid — Previous/Next
        then walks within the cluster only."""
        by_path = {it.path: it for it in self._all_items}
        out: List[SourceItem] = []
        for m in cluster.members:
            try:
                p = Path(m.item_id)
            except (TypeError, ValueError):
                continue
            it = by_path.get(p)
            if it is not None:
                out.append(it)
        return out

    def _on_cluster_back(self) -> None:
        """Back from the cluster sub-grid → day grid. Refresh the parent
        day cell so the cluster's aggregate colour reflects any state
        changes the user made inside the sub-grid."""
        cluster = self._current_cluster
        self._current_cluster = None
        self._current_cluster_cells = []
        if cluster is not None:
            self._refresh_cluster_parent_cell(cluster.bucket_key)
        self._stack.setCurrentIndex(self._DAY_GRID)
        self._day_grid.setFocus()

    # ── Compare (ported from the Picker, Nelson 2026-06-12) ───────────

    def _on_compare_requested(self, origin: int) -> None:
        """Compare button on the day grid or cluster sub-grid: gather the
        origin grid's flat Compare-state photos and open them in the
        side-by-side ComparePage. Cluster cells on the day grid are
        skipped (members compare within their own cluster — the sub-grid
        has its own button), matching the Picker."""
        from types import SimpleNamespace
        cells = (self._current_day_cells if origin == self._DAY_GRID
                 else self._current_cluster_cells)
        items: List[CullItem] = []
        phase_states: dict = {}
        for cell in cells:
            if getattr(cell, "is_cluster", False) or cell.item_id is None:
                continue
            if cell.color != CellColor.COMPARE or cell.item_kind == "video":
                continue
            items.append(CullItem(
                item_id=cell.item_id, path=Path(cell.item_id),
                kind=cell.item_kind or "photo"))
            phase_states[cell.item_id] = SimpleNamespace(
                state=self._state_for(Path(cell.item_id)))
        if len(items) < 2:
            return                                          # defensive
        self._compare_came_from = origin
        self._compare_page.load(None, "pick", items, phase_states)
        self._stack.setCurrentIndex(self._COMPARE)
        self._compare_page.setFocus()

    def _on_compare_state_changed(self, item_id: str, rebuild_state: str) -> None:
        """A tile was finalised in the compare grid — persist into the
        in-memory ledger (translating rebuild → legacy) and repaint the
        item everywhere it shows."""
        path = Path(item_id)
        self._state[path] = _REBUILD_TO_LEGACY.get(rebuild_state, rebuild_state)
        self._refresh_after_state_change(path)

    def _on_compare_quit(self) -> None:
        """Quit Comparison → back to the origin grid, every cell
        reprojected (states may have changed mid-compare)."""
        target = self._compare_came_from
        if target == self._CLUSTER_GRID:
            for idx in range(len(self._current_cluster_cells)):
                self._refresh_cluster_cell(idx)
            if self._current_cluster is not None:
                self._refresh_cluster_parent_cell(
                    self._current_cluster.bucket_key)
            self._stack.setCurrentIndex(self._CLUSTER_GRID)
            self._cluster_grid.setFocus()
        else:
            for idx in range(len(self._current_day_cells)):
                self._refresh_cell(idx)
            self._stack.setCurrentIndex(self._DAY_GRID)
            self._day_grid.setFocus()

    def _on_cluster_cell_activated(self, idx: int) -> None:
        """Centre-click on a cluster member → open it in the single-item
        viewer. Viewer's ``_items`` becomes the cluster's members so
        Previous/Next walks within the cluster only; Back from the viewer
        returns to the cluster sub-grid.

        Marks the member visited (PickPage spec/32 §2.10 port)."""
        cluster = self._current_cluster
        if cluster is None:
            return
        if not (0 <= idx < len(self._current_cluster_cells)):
            return
        cell = self._current_cluster_cells[idx]
        if cell.item_id is None:
            return
        self._visited_paths.add(cell.item_id)
        self._refresh_cluster_cell(idx)
        self._items = self._items_for_cluster(cluster)
        path = Path(cell.item_id)
        for i, it in enumerate(self._items):
            if it.path == path:
                self._index = i
                self._viewer_came_from = self._CLUSTER_GRID
                self._stack.setCurrentIndex(self._VIEWER)
                self._sync_viewport_items(i)
                self._viewport.setFocus()
                return

    def _on_cluster_cell_border(self, idx: int) -> None:
        """Border-click on a cluster member → cycle K/D/C for that member.
        Refresh both the sub-grid cell AND the parent day cell (cluster
        aggregate colour may have moved)."""
        cluster = self._current_cluster
        if cluster is None:
            return
        if not (0 <= idx < len(self._current_cluster_cells)):
            return
        cell = self._current_cluster_cells[idx]
        if cell.item_id is None:
            return
        path = Path(cell.item_id)
        self._cycle_state(path)
        self._refresh_cluster_cell(idx)
        self._refresh_cluster_parent_cell(cluster.bucket_key)

    def _on_cluster_pick_all(self) -> None:
        """Pick all members of the current cluster (sub-grid batch op)."""
        self._set_cluster_members_state(STATE_PICKED)

    def _on_cluster_skip_all(self) -> None:
        """Skip all members of the current cluster (sub-grid batch op)."""
        self._set_cluster_members_state(STATE_SKIPPED)

    def _set_cluster_members_state(self, state: str) -> None:
        cluster = self._current_cluster
        if cluster is None:
            return
        for m in cluster.members:
            try:
                self._state[Path(m.item_id)] = state
            except (TypeError, ValueError):
                continue
        # Rebuild sub-grid cells from scratch — all member colours move
        # together; cheaper than per-cell refresh.
        self._current_cluster_cells = self._build_cluster_member_cells(cluster)
        render = [
            CellRenderData(
                cell=c,
                thumbnail=self._maybe_cached_pixmap(c),
            )
            for c in self._current_cluster_cells
        ]
        self._cluster_grid.set_cells(render)
        self._enqueue_thumbnails(self._cluster_grid, self._current_cluster_cells)
        self._refresh_cluster_parent_cell(cluster.bucket_key)

    def _refresh_cluster_cell(self, idx: int) -> None:
        """Re-project one cluster sub-grid cell after its member's state
        changed."""
        cluster = self._current_cluster
        if cluster is None:
            return
        if not (0 <= idx < len(self._current_cluster_cells)):
            return
        new_cells = self._build_cluster_member_cells(cluster)
        if idx >= len(new_cells):
            return
        nc = new_cells[idx]
        self._current_cluster_cells[idx] = nc
        existing_widget = self._cluster_grid.cell_at(idx)
        thumb = (
            existing_widget.render_data().thumbnail
            if existing_widget is not None else None
        )
        self._cluster_grid.update_cell(
            idx, CellRenderData(cell=nc, thumbnail=thumb))

    def _refresh_cluster_parent_cell(self, bucket_key: str) -> None:
        """Re-render the parent day-grid cell for a cluster after one or
        more members changed state."""
        for i, c in enumerate(self._current_day_cells):
            if c.is_cluster and c.cluster is not None \
                    and c.cluster.bucket_key == bucket_key:
                self._refresh_cell(i)
                return

    def _cycle_cluster(self, cluster: CullCluster) -> None:
        """Bulk-cycle every member of a cluster — one step in the
        K → D → C → K cycle uniformly across the cluster."""
        cur = self._state.get(Path(cluster.members[0].item_id), STATE_PICKED) \
            if cluster.members else STATE_PICKED
        try:
            idx = _STATE_CYCLE.index(cur)
        except ValueError:
            idx = 0
        new = _STATE_CYCLE[(idx + 1) % len(_STATE_CYCLE)]
        for m in cluster.members:
            try:
                self._state[Path(m.item_id)] = new
            except (TypeError, ValueError):
                continue

    # ── Day-grid batch (spec/52 slice C, Nelson 2026-06-09) ───────

    def _on_day_pick_all(self) -> None:
        """Pick everything in the current day (day-scope batch op)."""
        self._set_day_state(STATE_PICKED)

    def _on_day_skip_all(self) -> None:
        """Skip everything in the current day (day-scope batch op)."""
        self._set_day_state(STATE_SKIPPED)

    def _set_day_state(self, state: str) -> None:
        day_number = self._current_day_number
        if day_number is None:
            return
        for it in self._items_for_day(day_number):
            self._state[it.path] = state
        # Rebuild day grid cells from scratch — every colour moves at once.
        day = next(
            (d for d in self._days if d.day_number == day_number), None)
        if day is None:
            return
        self._current_day_cells = fast_day_grid_cells(
            day, self._state_for,
            visited_for=self._visited_paths.__contains__,
            cluster_visited_for=self._visited_clusters.__contains__,
        )
        render = [
            CellRenderData(cell=c, thumbnail=self._maybe_cached_pixmap(c))
            for c in self._current_day_cells
        ]
        self._day_grid.set_cells(render)
        self._enqueue_thumbnails(self._day_grid, self._current_day_cells)

    def _refresh_cell(self, idx: int) -> None:
        """Re-project one Day Grid cell's colour from live in-memory state.
        Handles both item cells AND cluster cells — cluster cells get their
        members' colours recomputed and aggregated via :func:`cluster_color`
        so a mixed cluster paints yellow (spec/32 §2.4). Ported from
        PickPage._refresh_day_cell + _reproject_cell (Nelson 2026-06-09:
        "do not reinvent the wheel — it works in the culler")."""
        if not (0 <= idx < len(self._current_day_cells)):
            return
        new_cell = self._reproject_cell(self._current_day_cells[idx])
        self._current_day_cells[idx] = new_cell
        current = self._day_grid.cell_at(idx)
        thumb = (current.render_data().thumbnail
                 if current is not None else None)
        self._day_grid.update_cell(
            idx, CellRenderData(cell=new_cell, thumbnail=thumb))

    def _reproject_cell(self, cell: CullCell) -> CullCell:
        """Fresh ``CullCell`` with the same shape as ``cell`` but colour
        recomputed from the page's live state. Mirrors PickPage's helper
        of the same name — works for item cells AND cluster cells."""
        if cell.is_cluster and cell.cluster is not None:
            member_ids = [m.item_id for m in cell.cluster.members]
            phase_states = _phase_state_map(member_ids, self._state_for)
            colors = [
                cell_color_for_item(
                    m.item_id, m.kind, "pick", phase_states,
                    default_state=self._renderer_default,
                )
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
                end_time=cell.end_time,
                color=agg,
                cluster=new_cluster,
                # Re-read visited from the live in-memory set so cells
                # added to ``_visited_clusters`` between renders pick up
                # the new tick on the next refresh (Nelson 2026-06-09 —
                # port of PickPage visited semantics).
                visited=(
                    cell.cluster.bucket_key in self._visited_clusters
                    if cell.cluster is not None else cell.visited
                ),
            )
        if cell.item_id is not None:
            phase_states = _phase_state_map([cell.item_id], self._state_for)
            color = cell_color_for_item(
                cell.item_id, cell.item_kind, "pick", phase_states,
                default_state=self._renderer_default,
            )
            return CullCell(
                end_time=cell.end_time,
                color=color,
                item_id=cell.item_id,
                item_kind=cell.item_kind,
                visited=cell.item_id in self._visited_paths,
            )
        return cell

    # ── Navigation (within the day) ────────────────────────────────

    def _go_prev(self) -> None:
        if self._items:
            self._viewport.show_index(self._viewport.current_index() - 1)

    def _go_next(self) -> None:
        if self._items:
            self._viewport.show_index(self._viewport.current_index() + 1)

    def _sync_viewport_items(self, index: int) -> None:
        """Hand the current item list to the viewport (it owns nav +
        pixels + arm-on-landing video); shows ``index``, which fires
        :meth:`_on_viewport_current_changed` to dress the chrome."""
        vitems = [
            ViewportItem(
                path=it.path,
                kind="video" if self._is_video(it.path) else "photo",
                payload=it)
            for it in self._items
        ]
        self._viewport.set_items(vitems, max(0, min(index, len(vitems) - 1)))

    def _show(self, index: int) -> None:
        """Home/End and programmatic jumps route through the viewport."""
        if self._items:
            self._viewport.show_index(index)

    def _on_viewport_current_changed(self, index: int) -> None:
        """The viewport landed on ``index`` — dress the surface chrome
        (position, info line, exposure caption, state pill, transport
        row). No pixels here; the viewport owns those."""
        if not (0 <= index < len(self._items)):
            return
        self._index = index
        item = self._items[index]
        is_video = self._is_video(item.path)
        self._position_label.setText(f"{index + 1} / {len(self._items)}")
        bits: List[str] = []
        if self._current_day_number is not None:
            day = next(
                (d for d in self._days
                 if d.day_number == self._current_day_number), None)
            if day is not None:
                bits.append(day.label)
        if item.camera_id:
            bits.append(item.camera_id)
        if item.timestamp is not None:
            bits.append(item.timestamp.strftime("%Y-%m-%d %H:%M:%S"))
        bits.append(item.path.name)
        self._info_label.setText("   ·   ".join(bits))
        # Exposure overlay on photos only.
        self._expo_overlay.set_html("" if is_video else caption_html(item))
        # The lens button follows the item: videos have nothing
        # full-res to inspect (the corner affordance's rule).
        self._fullres_btn.setVisible(not is_video)
        # Transport row shows for videos; reset its readout per item.
        self._viewer.set_region_visible("compact_row", is_video)
        if is_video:
            self._video_duration_ms = 0
            set_transport_playing(self._play_btn, True)   # arms autoplaying
            self._timeline.setRange(0, 0)
            self._timeline.setValue(0)
            self._time_label.setText("0:00 / 0:00")
        self._sync_state_pill()

    @staticmethod
    def _is_video(path: Path) -> bool:
        return path.suffix.lower() in VIDEO_EXTENSIONS

    # ── Viewport verbs (the locked key map — spec/63 §4) ──────────────
    # The viewport translates keys to verbs; the surface decides what
    # they mean here. Decisions are no-ops in browse mode (read-only).

    def _verb_set_state(self, state: str) -> None:
        if not self._browse_mode:
            self._set_state(state)

    def _verb_toggle_pick_skip(self) -> None:
        """Space — the binary toggle (spec/63 §4): Pick ⇄ Skip. C runs
        the full K→D→C cycle; Space is the fast two-state flip a sweep
        leans on."""
        if self._browse_mode:
            return
        path = self._current_path()
        if path is None:
            return
        cur = self._state.get(path, self._legacy_default)
        self._set_state(
            STATE_PICKED if cur == STATE_SKIPPED else STATE_SKIPPED)

    def _verb_cycle_state(self) -> None:
        if not self._browse_mode:
            self._toggle_state()

    def _on_viewport_back(self) -> None:
        """Esc inside the viewport — step down one level (fullscreen →
        windowed → out), mirroring the old viewer Esc."""
        if self._fullscreen:
            self._exit_fullscreen()
        else:
            self._on_viewer_back()

    # ── State (K/D/C cycle) ────────────────────────────────────────

    def _current_path(self) -> Optional[Path]:
        item = self._viewport.current_item()
        if item is not None and item.path is not None:
            return item.path
        if self._items and 0 <= self._index < len(self._items):
            return self._items[self._index].path
        return None

    def _cycle_state(self, path: Path) -> None:
        """K → D → C → K for ``path``. Used by both the K/D pill in the
        viewer and the cell border-click."""
        cur = self._state.get(path, STATE_PICKED)
        try:
            idx = _STATE_CYCLE.index(cur)
        except ValueError:
            idx = 0
        new = _STATE_CYCLE[(idx + 1) % len(_STATE_CYCLE)]
        self._state[path] = new

    def _toggle_state(self) -> None:
        """K/D pill in the viewer — cycle the current item's state. After
        the cycle, refresh every place ``path`` is visible (day-grid
        flat cell, cluster sub-grid cell, parent cluster day-grid cell)."""
        path = self._current_path()
        if path is None:
            return
        self._cycle_state(path)
        self._sync_state_pill()
        self._refresh_after_state_change(path)

    def _set_state(self, state: str) -> None:
        """Direct keyboard set: K/D/C key → state for current item."""
        path = self._current_path()
        if path is None:
            return
        self._state[path] = state
        self._sync_state_pill()
        self._refresh_after_state_change(path)

    def _refresh_after_state_change(self, path: Path) -> None:
        """Bring every visible cell that references ``path`` back in sync
        with ``self._state``. Day-grid flat cells, cluster sub-grid cells,
        and the parent day-grid cluster cell may all be affected (a
        cluster member's colour change can move the cluster's aggregate
        from KEPT to MIXED)."""
        # Day-grid flat cell (item lives directly on the day grid).
        for i, c in enumerate(self._current_day_cells):
            if c.item_id is not None and Path(c.item_id) == path:
                self._refresh_cell(i)
                break
        # Cluster context — if the path is a member of the currently-open
        # cluster, refresh the sub-grid cell + the parent day cell.
        cluster = self._current_cluster
        if cluster is None:
            return
        for i, m in enumerate(cluster.members):
            try:
                if Path(m.item_id) == path:
                    self._refresh_cluster_cell(i)
                    self._refresh_cluster_parent_cell(cluster.bucket_key)
                    return
            except (TypeError, ValueError):
                continue

    def _sync_state_pill(self) -> None:
        path = self._current_path()
        if path is None:
            return
        s = self._state.get(path, STATE_PICKED)
        if s == STATE_CANDIDATE:
            text, prop, checked = tr("? Compare"), "candidate", True
        elif s == STATE_SKIPPED:
            text, prop, checked = tr("⊘ Skip"), "skipped", False
        else:
            text, prop, checked = tr("✓ Pick"), "picked", True
        self._state_pill.blockSignals(True)
        self._state_pill.setChecked(checked)
        self._state_pill.blockSignals(False)
        self._state_pill.setText(text)
        self._state_pill.setProperty("state", prop)
        # Re-evaluate stylesheet so [state="..."] picks up the change.
        self._state_pill.style().unpolish(self._state_pill)
        self._state_pill.style().polish(self._state_pill)
        # Canonical photo-border-as-state — drive the BasePickSurface MEDIA
        # host's border colour. Same mechanism the photo culler uses;
        # one rule in QSS, one knob in surface.py.
        self._viewer.set_media_state(prop)

    # ── Video transport — the scrubber over the viewport's player ─────
    # The viewport owns the QMediaPlayer (arm-on-landing); this row is
    # pure chrome driven by its timeline signals.

    def _on_video_playing_changed(self, playing: bool) -> None:
        set_transport_playing(self._play_btn, playing)

    def _on_position(self, ms: int) -> None:
        if not self._timeline.isSliderDown():
            self._timeline.setValue(int(ms))
        self._refresh_time_label(int(ms), self._video_duration_ms)

    def _on_duration(self, ms: int) -> None:
        self._video_duration_ms = int(ms)
        self._timeline.setRange(0, int(ms))
        self._refresh_time_label(self._timeline.value(), int(ms))

    def _refresh_time_label(self, pos_ms: int, dur_ms: int) -> None:
        pos, dur = pos_ms // 1000, dur_ms // 1000
        self._time_label.setText(
            f"{pos // 60}:{pos % 60:02d} / {dur // 60}:{dur % 60:02d}")

    # ── Fullscreen ──────────────────────────────────────────────────

    def _toggle_fullscreen(self) -> None:
        win = self.window()
        if win is None:
            return
        if self._fullscreen:
            win.showNormal()
            self._fullscreen = False
            self._fullscreen_btn.setChecked(False)
        else:
            win.showFullScreen()
            self._fullscreen = True
            self._fullscreen_btn.setChecked(True)

    def _exit_fullscreen(self) -> None:
        if self._fullscreen:
            self._toggle_fullscreen()

    # ── Back routing ────────────────────────────────────────────────

    def _on_save(self) -> None:
        kept = self.kept_paths()
        log.info(
            "QuickSweepPage Save: %d kept of %d (Keep + Compare counted)",
            len(kept), len(self._all_items),
        )
        self.saved.emit(kept)

    def _on_viewer_back(self) -> None:
        """Back from the single-item viewer → return to whichever level
        opened the viewer. Cluster cells route back to the cluster sub-
        grid; day-grid cells route back to the day grid. Browse mode and
        the empty-days fallback cancel the whole sweep."""
        self._viewport.shutdown_video()
        if self._browse_mode or not self._days:
            self._exit_fullscreen()
            self.cancelled.emit()
            return
        if (self._viewer_came_from == self._CLUSTER_GRID
                and self._current_cluster is not None):
            # Refresh the sub-grid in case state changed inside the viewer.
            self._current_cluster_cells = self._build_cluster_member_cells(
                self._current_cluster)
            render = [
                CellRenderData(
                    cell=c,
                    thumbnail=self._maybe_cached_pixmap(c),
                )
                for c in self._current_cluster_cells
            ]
            self._cluster_grid.set_cells(render)
            self._stack.setCurrentIndex(self._CLUSTER_GRID)
            self._cluster_grid.setFocus()
            return
        self._stack.setCurrentIndex(self._DAY_GRID)
        self._day_grid.setFocus()

    def _on_day_grid_back(self) -> None:
        """Back from the Day Grid → days panel (or, when there's only
        one day, this IS the outermost level — confirm-and-commit per
        Nelson 2026-06-09).

        Nelson 2026-06-13 (Bug 2) — refresh per-day Pick / Skip counts
        from ``self._state`` before showing the days panel; PickDay /
        BucketStatus are frozen and were stale otherwise."""
        if len(self._days) <= 1:
            self._finish_via_back()
            return
        self._days = refresh_day_statuses(self._days, self._state_for)
        self._stack.setCurrentIndex(self._NAV)
        self._nav.set_days(self._days)
        self._nav.setFocus()

    def _on_nav_back(self) -> None:
        """Days panel return button — outermost Back for multi-day. Show
        the confirmation dialog (Nelson 2026-06-09); on Confirm emit
        ``saved`` so the capture orchestrator commits the copy; on
        Cancel stay in the days panel."""
        self._finish_via_back()

    # ── "Start a new pass…" (spec/32 §2.10 — PickPage port) ────────

    def _on_clear_marks(self) -> None:
        """Wipe every ✓ tick on the cells / clusters the user has already
        opened in this Quick Sweep session. Decisions in ``self._state``
        are NOT touched — same contract as PickPage's "Start a new pass…"
        button (Nelson 2026-06-09).

        Reprojects the open day grid (and the cluster sub-grid if one is
        open) so the ticks disappear immediately without leaving and
        re-entering the day."""
        from PyQt6.QtWidgets import QMessageBox

        if not self._visited_paths and not self._visited_clusters:
            return                                              # nothing to clear
        msg = QMessageBox(self)
        msg.setWindowTitle(tr("Start a new pass?"))
        msg.setIcon(QMessageBox.Icon.NoIcon)
        msg.setText(tr(
            "This clears every ✓ tick on the Quick Sweep cells and "
            "clusters you've already opened.  Your Keep / Compare / "
            "Skip decisions are not touched.  Continue?"
        ))
        yes_btn = msg.addButton(
            QMessageBox.StandardButton.Yes,
        )
        msg.addButton(QMessageBox.StandardButton.No)
        msg.setDefaultButton(QMessageBox.StandardButton.No)
        msg.exec()
        if msg.clickedButton() is not yes_btn:
            return

        self._visited_paths.clear()
        self._visited_clusters.clear()

        # Refresh whichever grid the user is currently looking at so the
        # ticks disappear right away.
        for idx in range(len(self._current_day_cells)):
            self._refresh_cell(idx)
        if self._current_cluster is not None and self._current_cluster_cells:
            for idx in range(len(self._current_cluster_cells)):
                self._refresh_cluster_cell(idx)

    def _finish_via_back(self) -> None:
        """Outermost-Back handler. Browse mode (read-only) drops the
        sweep with no confirmation; otherwise pop the confirmation
        dialog with pick / skip counts and route to ``saved`` or
        stay-put based on the user's choice."""
        if self._browse_mode or not self._days:
            self._exit_fullscreen()
            self.cancelled.emit()
            return
        if self._confirm_done():
            self._exit_fullscreen()
            kept = self.kept_paths()
            log.info(
                "QuickSweepPage finish via Back: %d kept of %d "
                "(Keep + Compare counted)",
                len(kept), len(self._all_items),
            )
            self.saved.emit(kept)
        # On Cancel the user stays where they are — no signal, no
        # navigation; the dialog dismissal returns focus to the page.

    def _confirm_done(self) -> bool:
        """Pop the pick/skip/compare summary dialog. Returns True iff
        the user chose to copy and finish. Uses ``Icon.NoIcon`` per the
        QMessageBox-chrome feedback rule."""
        picked = sum(1 for s in self._state.values() if s == STATE_PICKED)
        skipped = sum(1 for s in self._state.values() if s == STATE_SKIPPED)
        compare = sum(1 for s in self._state.values() if s == STATE_CANDIDATE)
        total = len(self._state)
        to_copy = picked + compare

        from PyQt6.QtWidgets import QMessageBox
        body_lines = [
            tr("{n} of {total} photo(s) will be copied.")
            .replace("{n}", str(to_copy))
            .replace("{total}", str(total)),
        ]
        if compare:
            body_lines.append(
                tr("Includes {n} marked Compare (counted as Pick).")
                .replace("{n}", str(compare))
            )
        if skipped:
            body_lines.append(
                tr("{n} skipped photo(s) will not be copied.")
                .replace("{n}", str(skipped))
            )
        body_lines.append("")
        body_lines.append(tr("Copy and finish?"))

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setWindowTitle(tr("Finish Quick Sweep"))
        box.setText(tr("Quick Sweep — ready to copy."))
        box.setInformativeText("\n".join(body_lines))
        copy_btn = box.addButton(
            tr("Copy and finish"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(
            tr("Stay in Quick Sweep"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(copy_btn)
        box.exec()
        return box.clickedButton() is copy_btn

    # ── Keyboard ────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        # The viewport owns the locked key grammar (arrows/wheel, P/X/
        # Space/C, Tab, F/F11, Esc — spec/63 §4) and has focus inside
        # the viewer. The page only adds Home/End, which the viewport
        # leaves unhandled and propagates up here.
        key = event.key()
        if key in (Qt.Key.Key_F1, Qt.Key.Key_Question):
            self._show_shortcuts()
            event.accept()
            return
        if (self._stack.currentIndex() == self._VIEWER and self._items
                and key in (Qt.Key.Key_Home, Qt.Key.Key_End)):
            self._viewport.show_index(
                0 if key == Qt.Key.Key_Home else len(self._items) - 1)
            event.accept()
            return
        super().keyPressEvent(event)

    def _show_shortcuts(self) -> None:
        from mira.ui.base.shortcuts import show_shortcuts
        show_shortcuts(self, tr("Quick Sweep"), [
            ("",                    tr("Triage")),
            (tr("P / X"),           tr("Pick / Skip")),
            (tr("Space"),           tr("Toggle Pick ⇄ Skip")),
            (tr("C"),               tr("Cycle Pick → Skip → Compare")),
            (tr("Click the border"), tr("Cycle Pick → Skip → Compare")),
            ("",                    tr("Navigate")),
            (tr("◀ / ▶"),            tr("Previous / next item")),
            (tr("Home / End"),      tr("First / last")),
            (tr("Mouse wheel"),     tr("Previous / next item")),
            ("",                    tr("View")),
            (tr("F10"),             tr("Inspect at full resolution")),
            (tr("F / F11"),         tr("Fullscreen")),
            (tr("Esc"),             tr("Back")),
            (tr("F1 · ?"),          tr("This help")),
        ])
