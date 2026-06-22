"""Surface 07 — Picker (single-photo cull) on the ONE display engine.

spec/70 Phase 3 §2 reconciliation. The redesign shell embeds
:class:`~mira.ui.media.photo_viewport.PhotoViewport` (spec/63's "one
engine, every surface" thesis) and absorbs the gateway/engine wiring
the legacy ``PickPhotoSurface`` carried — decision persistence, sharpness
honesty, visited stamping, advance-after-pick, cluster cover expansion,
sweep-with-peaking, F10 inspection lens. The engine is reused, not
rewritten.

The locked map (spec/63 §4): **P** Pick · **X** Skip · **Space** toggle
Pick⇄Skip · **C** cycle Pick→Skip→Compare · **Enter** play/pause the
cluster sweep (peaking rides it) · **F10** inspection lens · **F/F11**
fullscreen · **Esc** one level back · ←/→ navigate · **Ctrl+Z** undo.
The viewport owns the grammar; the page proxies focus to it and adds
Home/End/F1 as stray-focus fallbacks.

Composition:
    Toolbar:   ‹ Back · position · ✓ Pick · ✗ Skip · ⇄ Compare ·
               ▶ Play (cluster) · Combined (exposure brackets) ·
               Full Resolution · Full Screen
    Stage:     BasePickSurface scaffold (state border on the media host)
               + embedded PhotoViewport (blurred-fill backdrop +
               proxy-sharp pixels + F10 lens) + PhotoExposureOverlay.
    Filmstrip: horizontal scroll of neighbour thumbs (redesign catalog).

Live entries from the redesigned :class:`DaysGridPage` bridge:

* :meth:`open_to_item` — flat single-item click (synthetic 1-item bucket).
* :meth:`open_to_cluster` — cluster sub-grid member click (real cluster
  bucket so Enter sweep + intra-cluster ← → + Combined preview all work).

Signals:
    * ``closed`` — Back / Esc; the host returns to the Days Grid.
    * ``fullscreen_changed(bool)`` — shell hides/restores its chrome.

The legacy ``mira/ui/picked/pick_photo_surface.py`` is no longer wired
from MainWindow; it stays in tree for the Quick Sweep session that comes
next. New work goes here.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Optional

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from mira.gateway import Gateway
from mira.picked import CullBucket, CullCluster, CullItem
from mira.picked.exif_compare import (
    caption_html,
    exposure_for_chip,
    file_size_text,
    file_type_label,
    source_chip_html,
)
from mira.picked.status import (
    STATE_CANDIDATE,
    STATE_PICKED,
    STATE_SKIPPED,
    default_state_for,
    project_status,
)
from mira.ui.base.surface import (
    BasePickSurface,
    set_transport_playing,
    transport_button,
)
from mira.ui.design import (
    apply_density,
    danger_ghost_button,
    ghost_button,
)
from mira.ui.i18n import tr
from mira.ui.media.photo_cache import photo_cache
from mira.ui.media.photo_overlay import PhotoExposureOverlay
from mira.ui.media.photo_viewport import PhotoViewport, ViewportItem
from mira.ui.pages.video_transport import VideoTransportBar

log = logging.getLogger(__name__)

# Cycle order matches the legacy culler: Skip → Pick → Compare → (wrap).
_CYCLE = (STATE_SKIPPED, STATE_PICKED, STATE_CANDIDATE)
# Bracket / burst kinds the Play button steps as a sequence.
_PLAY_KINDS = frozenset(("burst", "focus_bracket", "exposure_bracket"))
_COMBINED_KINDS = frozenset(("exposure_bracket",))
# Film cadence (ms per frame) — 2.5 fps so a focus shift / burst frame
# is actually visible on each tick.
_FILM_MS = 400


class _ExifPrefetchSignals(QObject):
    """Cross-thread signal carrier for the bulk EXIF prefetcher.

    The PhotoCache decode is async; ``read_exif_single`` would spawn an
    exiftool subprocess per photo (~300-500 ms cold). ``read_exif_batch``
    reads N files in one subprocess via the argfile path, so a whole
    bucket warms in the cost of one call. We fire it on :meth:`load` in
    a daemon thread and merge results via this signal.
    """

    fetched = pyqtSignal(int, dict)


# ── PickerPage ────────────────────────────────────────────────────────


class PickerPage(QWidget):
    """Surface 07 — single-photo cull page (PhotoViewport-backed)."""

    closed = pyqtSignal()
    fullscreen_changed = pyqtSignal(bool)

    def __init__(
        self,
        gateway: Optional[Gateway] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.gateway = gateway
        self._eg = None
        self._event_id: Optional[str] = None
        self._phase = "pick"
        self._bucket: Optional[CullBucket] = None
        self._items: list = []
        self._index = 0
        self._state: dict = {}              # item_id -> explicit state | None
        self._default_state = STATE_SKIPPED
        self._fullscreen = False

        # Position label feed (day-grid context — "Cell N / Total").
        self._day_index = 1
        self._day_total = 1
        self._day_number = 1

        # AF / EXIF / sharpness caches (per-bucket, cleared on load).
        self._af_cache: dict[str, object] = {}
        self._exif_cache: dict[str, object] = {}
        self._sharpness_cache: dict[str, float] = {}

        # Play/Pause for cluster sequences. The timer paces frame steps at
        # _FILM_MS; _film_step skips explicit Skips. The Sweep carries the
        # viewport's FAST stack-film peaking (spec/63 §7).
        self._film_timer = QTimer(self)
        self._film_timer.setInterval(_FILM_MS)
        self._film_timer.timeout.connect(self._film_step)
        # Exposure-bracket "Combined" non-destructive preview. ``_combined_on``
        # locks per-frame nav; the cache is built lazily, reset per bucket load.
        self._combined_on = False
        self._combined_cache = None
        # Bulk-EXIF prefetcher (generation-tagged so stale results drop).
        self._exif_gen = 0
        self._exif_prefetch_signals = _ExifPrefetchSignals()
        self._exif_prefetch_signals.fetched.connect(
            self._on_exif_prefetched)

        # spec/59 §8 / spec/66 §1.2 — shipped-item set + watermark gate.
        # Loaded on _open_event; consumed per nav in _on_current_changed
        # to drive ``PhotoViewport.set_exported_watermark``.
        self._exported_set: set = set()
        self._watermark_enabled: bool = True

        self._build_ui()

    # ── UI assembly ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Back lives in the shared title bar (mode-aware via on_titlebar_back).
        self.uses_titlebar_back = True

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # spec/71 identity — Pick inherits its phase colour as a full-width
        # rail flush at the top, matching Events / Phases / Days List / Days
        # Grid / Editor. The §5a state colours stay on the media border below;
        # the Pick / Skip / Compare toggles in the nav band carry the legend.
        self._rail = QFrame()
        self._rail.setObjectName("SurfaceHeaderRail")
        self._rail.setProperty("phase", "pick")
        self._rail.setFixedHeight(2)
        outer.addWidget(self._rail)

        # ── Surface scaffold — the state border lives on this host. Media is
        # kept full-width here (unlike the Editor's inset content) so the
        # picking canvas gets every pixel. ──
        self._surface = BasePickSurface()
        outer.addWidget(self._surface)

        # ── MEDIA — PhotoViewport. Pixels, nav, prefetch, F10 and the
        # locked key grammar live here; the page reacts to verbs. ──
        self.viewport = PhotoViewport()
        vp = self.viewport
        vp.current_changed.connect(self._on_current_changed)
        vp.edge_reached.connect(self._on_edge)
        vp.sharp_changed.connect(self._on_sharp_landed)
        vp.pick_requested.connect(lambda: self._set_state(STATE_PICKED))
        vp.skip_requested.connect(lambda: self._set_state(STATE_SKIPPED))
        vp.toggle_requested.connect(self._toggle_pick_skip)
        vp.cycle_requested.connect(self._cycle)
        vp.sweep_requested.connect(self._on_sweep_key)
        vp.fullscreen_requested.connect(self._toggle_fullscreen)
        vp.back_requested.connect(self._on_esc)
        self._surface.set_media(self.viewport)
        # Keys aimed at the page (we receive focus via the page) land on the
        # viewport — the one place the grammar lives.
        self.setFocusProxy(self.viewport)

        # Exposure overlay ON the photo — a pill at the bottom of the
        # media area; follows the host's resizes itself.
        self._expo_overlay = PhotoExposureOverlay(self.viewport)

        # ── TOP_BAR — hidden. Back moved to the shared title bar; the only
        # other occupant (the position counter) rides into the nav band below,
        # between Full Resolution and Full Screen (Nelson 2026-06-20). ──
        self._surface.set_region_visible("top_bar", False)
        self._position_label = QLabel("")
        self._position_label.setObjectName("Sub")
        self._position_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._position_label.setToolTip(tr(
            "Position in the current day — item index of total."))

        # ── STATE_BAR — hidden. State lives on the MediaHost border. ──
        self._surface.set_region_visible("state_bar", False)
        # ── COMPACT_ROW — video transport reveal slot. The 64 px
        # ``compact_row`` host stays VISIBLE on every item so the
        # canvas position is invariant under photo↔video sweeps
        # (Nelson 2026-06-15 — "the line where the transport buttons
        # are placed has to exist (empty) when photos are displayed").
        # Only the transport widget INSIDE the row toggles: hidden on
        # photos, shown on videos. The compact_row QSS rule is
        # transparent + borderless so an empty slot is invisible.
        self._transport_bar = VideoTransportBar()
        self._transport_bar.play_pause_requested.connect(
            self.viewport.video_toggle_play)
        self._transport_bar.seek_requested.connect(
            self.viewport.video_seek)
        self._transport_bar.volume_changed.connect(
            self.viewport.video_set_volume)
        self._transport_bar.speed_changed.connect(
            self._on_video_speed_changed)
        # The viewport pushes timeline state out as we play.
        self.viewport.video_position_changed.connect(
            self._on_video_position)
        self.viewport.video_duration_changed.connect(
            self._on_video_duration)
        self.viewport.video_playing_changed.connect(
            self._transport_bar.set_playing)
        self.viewport.video_error.connect(self._transport_bar.show_error)
        cr_layout = self._surface.compact_row.layout()
        cr_layout.setContentsMargins(12, 6, 12, 8)
        cr_layout.addWidget(self._transport_bar)
        # The compact_row is the bottom transport BAND (boxed like the
        # Editor's video bottom). It reserves the SAME height on photos and
        # videos so the canvas bottom edge is pixel-identical across the
        # boundary (Nelson 2026-06-15 Fix A); the exact height is measured
        # from the dense transport at the end of _build_ui, replacing the old
        # hardcoded 64 px. Bordered on videos, hollow on photos.
        self._surface.compact_row.setProperty("surfaceBand", True)
        # Seed the viewport with the transport bar's initial volume so
        # the first video respects the slider's default position. The
        # speed selector defaults to 1× which matches the viewport's
        # default playback rate — no need to push.
        self.viewport.video_set_volume(self._transport_bar.volume.value())
        # Hide the transport widget; the row container stays visible
        # so the canvas geometry is invariant.
        self._transport_bar.setVisible(False)
        # Per-path video metadata cache + last position/duration the
        # transport painted. Duration is seeded from ffprobe on landing
        # so the scrubber + the |▶ "jump to end" snap to the correct
        # endpoint before QtMultimedia's async ``durationChanged``
        # arrives (Nelson 2026-06-15 Fix B — the fps/frame-step path
        # retired with ◀|/|▶ repurposed as start/end jumps).
        self._video_meta_cache: dict = {}
        self._video_pos_ms = 0
        self._video_duration_ms = 0
        # ── TOOLS — hidden (cluster controls moved into the nav row). ──
        self._surface.set_region_visible("tools", False)

        # ── NAV — left arrow · action cluster · right arrow ──
        nav_layout = self._surface.nav.layout()
        # The redesign's floating ‹ / › nav arrows live inside the nav row
        # for now (until we add them as overlay children on the viewport).
        self._prev_btn = ghost_button("‹")
        self._prev_btn.setToolTip(tr("Previous photo  (←)"))
        self._prev_btn.clicked.connect(lambda: self._go(self._index - 1))
        nav_layout.addWidget(self._prev_btn)
        nav_layout.addStretch(1)

        # Action cluster — Pick / Skip / Compare (the locked verbs).
        self._pick_btn = ghost_button(tr("✓ Pick  P"))
        self._pick_btn.setObjectName("Pick")
        self._pick_btn.setCheckable(True)
        self._pick_btn.clicked.connect(lambda: self._set_state(STATE_PICKED))
        nav_layout.addWidget(self._pick_btn)
        self._skip_btn = danger_ghost_button(tr("✗ Skip  X"))
        self._skip_btn.setObjectName("Skip")
        self._skip_btn.setCheckable(True)
        self._skip_btn.clicked.connect(lambda: self._set_state(STATE_SKIPPED))
        nav_layout.addWidget(self._skip_btn)
        self._compare_btn = ghost_button(tr("⇄ Compare  C"))
        self._compare_btn.setObjectName("Compare")
        self._compare_btn.setCheckable(True)
        self._compare_btn.clicked.connect(self._cycle_to_compare)
        nav_layout.addWidget(self._compare_btn)

        # Cluster controls — Play (Enter sweep) + Combined (exposure
        # brackets). The transport_button() factory pins the width and
        # swaps ▶ ⇄ ⏸ via set_transport_playing() so the row never dances.
        self._film_btn = transport_button(tr(
            "Play the cluster as a sequence so you see the frames sweep  "
            "(Enter)"))
        self._film_btn.setCheckable(True)
        self._film_btn.clicked.connect(self._toggle_film)
        self._film_btn.setVisible(False)
        nav_layout.addWidget(self._film_btn)
        self._combined_btn = ghost_button(tr("Combined"))
        self._combined_btn.setCheckable(True)
        self._combined_btn.setToolTip(tr(
            "Preview the exposure-fused composite of the whole bracket"))
        self._combined_btn.clicked.connect(self._toggle_combined)
        self._combined_btn.setVisible(False)
        nav_layout.addWidget(self._combined_btn)

        # Full Resolution + Full Screen — the standard centre pair on every
        # photo surface (spec/63 §4 F10 / F11).
        self._fullres_btn = ghost_button(tr("Full Resolution  F10"))
        self._fullres_btn.setToolTip(tr(
            "Inspect this frame at full resolution — peaking, true 1:1 "
            "zoom, AF point  (F10)"))
        self._fullres_btn.clicked.connect(self.viewport.truth_requested.emit)
        nav_layout.addWidget(self._fullres_btn)
        # Position counter rides between the two centre buttons (Nelson
        # 2026-06-20 — same spot as the Editor footer).
        nav_layout.addWidget(self._position_label)
        self._fullscreen_btn = ghost_button(tr("Full Screen  F11"))
        self._fullscreen_btn.setCheckable(True)
        self._fullscreen_btn.setToolTip(tr(
            "Use the whole screen for picking  (F / F11)"))
        self._fullscreen_btn.clicked.connect(self._toggle_fullscreen)
        nav_layout.addWidget(self._fullscreen_btn)

        nav_layout.addStretch(1)
        self._next_btn = ghost_button("›")
        self._next_btn.setToolTip(tr("Next photo  (→)"))
        self._next_btn.clicked.connect(lambda: self._go(self._index + 1))
        nav_layout.addWidget(self._next_btn)

        # The viewport's corner inspect button is suppressed — the labelled
        # Full Resolution button covers it.
        self.viewport.set_corner_inspect_visible(False)

        # Canonical media-border click cycles state.
        self._surface.media_border_clicked.connect(self._cycle)

        # ── The nav row IS the bottom control band (#SurfaceBand box around
        # the Pick / Skip / Compare toggles + Full Res / Full Screen + the
        # counter). The scaffold's nav region is borderless by default; the
        # `surfaceBand` property opts it into the band border (Nelson
        # 2026-06-20). ──
        self._surface.nav.setProperty("surfaceBand", True)
        self._surface.nav.style().unpolish(self._surface.nav)
        self._surface.nav.style().polish(self._surface.nav)

        # spec/92 dense tier — slim the nav chrome + the video transport so the
        # picking canvas keeps the vertical room. Reused from the Editor.
        apply_density(self._surface.nav)
        apply_density(self._transport_bar)

        # Pin the transport band to the DENSE bar's measured height (+ the
        # compact_row's 6/8 margins) so photo (empty, hollow) and video
        # (transport) reserve the exact same space — no canvas jump. Replaces
        # the old hardcoded 64 px, which was sized for the full-height bar.
        self._transport_bar.ensurePolished()
        self._surface.compact_row.setFixedHeight(
            self._transport_bar.sizeHint().height() + 14)
        # Photo is the default landing — start the transport band hollow.
        self._set_transport_band_hollow(True)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _set_transport_band_hollow(self, hollow: bool) -> None:
        """Drop (hollow) or restore the transport band's border. Borderless on
        photos (the transport is hidden — reserved space only); bordered on
        videos where the transport fills it. Mirrors the Editor's bottom band."""
        cr = self._surface.compact_row
        cr.setProperty("hollow", bool(hollow))
        cr.style().unpolish(cr)
        cr.style().polish(cr)

    def on_titlebar_back(self) -> None:
        """Shared title-bar Back hook (MainWindow._on_titlebar_back prefers
        this). Steps out of fullscreen first, else backs out of the surface —
        the same one-level behaviour as Esc."""
        if not self._exit_fullscreen():
            self._on_back()

    # ── Public entry points (Days Grid bridge) ────────────────────────

    def open_to_item(
        self, event_id: str, day_number: int, item_id: str,
    ) -> bool:
        """Open the Picker on a flat single-item click from the Days Grid
        (Surface 06). Loads the ENTIRE day's navigable items
        (chronological; cluster members flattened in place) and
        positions the viewport at the clicked item — so prev/next walks
        the whole day, not just that one click (Nelson 2026-06-14
        eyeball #3: a 1-item bucket made nav dead). Videos stay in the
        list (the Picker can decide on a video — Skip / Pick — just like
        a photo; the viewport's arm-on-landing handles poster + clip).

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
            # snapshot. Fall back to a synthetic single-item bucket so
            # the surface still opens rather than collapsing back to
            # the grid silently.
            cull_item = self._cull_item_for(item_id)
            if cull_item is None:
                self._close_event()
                return False
            items = [cull_item]
            idx = 0
        # spec/32 §2.10 item tick.
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

    def _day_navigable_items(self, day_number: int) -> list:
        """Build the day's navigable item list in chronological order.

        Walks :func:`day_grid_cells` and FLATTENS clusters into their
        members so prev/next steps through every individual frame the
        user can decide on. Photos AND videos stay in the list — the
        Picker can decide on either kind."""
        if self._eg is None:
            return []
        from mira.picked import day_grid_cells
        from mira.picked.status import default_state_for
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
        out: list = []
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

    def _day_bucket(self, day_number: int, items: list) -> CullBucket:
        """Wrap a whole-day item list in a CullBucket the load path
        understands. Status uses ``project_status`` so the bucket reads
        sensibly if anything inspects it."""
        from mira.picked.status import project_status
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
        """Open the Picker on a cluster sub-grid member click. Loads the
        REAL cluster bucket so Enter sweep, intra-cluster ← → and (for
        exposure brackets) Combined preview all work.

        Returns ``True`` on success.
        """
        if not self._open_event(event_id):
            return False
        # spec/32 §2.10 — opening a sub-grid member marks it visited.
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
                [m.item_id for m in cluster.members],
                phase_states,
                self._eg.bucket(cluster.bucket_key, self._phase),
            ),
            detection_source=cluster.detection_source,
            camera=cluster.camera,
        )
        self._day_number = day_number
        # In cluster context the day position is opaque; use cluster index.
        self._day_index = entry_idx + 1
        self._day_total = len(cluster.members)
        self._load_bucket(bucket, entry_idx=entry_idx)
        return True

    def close_event(self) -> None:
        """Release any open event gateway. Idempotent."""
        self._close_event()

    # ── Lifecycle helpers ──────────────────────────────────────────────

    def _open_event(self, event_id: str) -> bool:
        if self.gateway is None:
            log.warning("PickerPage._open_event called without a gateway")
            return False
        self._close_event()
        try:
            self._eg = self.gateway.open_event(event_id)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "PickerPage: cannot open event %s", event_id)
            return False
        self._event_id = event_id
        self._default_state = default_state_for(
            self.gateway.settings, self._phase)
        # spec/59 §8 / spec/66 §1.2 — the diagonal "Exported" watermark
        # on the single-photo viewport. The set is cached for the
        # session; an Export run mid-session refreshes it on next
        # event entry. Gated by ``show_exported_watermark``.
        self._exported_set, self._watermark_enabled = (
            self._load_exported_state())
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
                "PickerPage: settings.load failed; assuming watermark on")
            enabled = True
        try:
            shipped = self._eg.exported_item_ids()
        except Exception:                                          # noqa: BLE001
            log.exception("PickerPage: exported_item_ids failed")
            shipped = set()
        return shipped, enabled

    def _close_event(self) -> None:
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
        self._state = {}
        self._exported_set: set = set()
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
            item_id=item_id, path=path, kind=item.kind,
            capture_time_corrected=item.capture_time_corrected,
        )

    def _synthetic_bucket(self, ci: CullItem) -> CullBucket:
        """Fallback: wrap a single CullItem in a 1-item bucket when the
        clicked item doesn't surface in :meth:`_day_navigable_items`
        (the surface still opens rather than collapsing back to the
        grid). The normal open_to_item path uses :meth:`_day_bucket`."""
        from mira.picked.status import (
            BADGE_UNTOUCHED, BucketStatus,
        )
        return CullBucket(
            bucket_key=f"single:{ci.item_id}",
            kind=ci.kind,
            title="",
            items=(ci,),
            status=BucketStatus(
                total=1, kept=0, candidate=0, discarded=0, untouched=1,
                reviewed=False, browsed=False, badge=BADGE_UNTOUCHED),
        )

    # ── Bucket load (the absorbed PickPhotoSurface.load wiring) ────────

    def _load_bucket(
        self, bucket: CullBucket, *, entry_idx: int = 0,
    ) -> None:
        """Hand a bucket to the viewport, prime caches, dress chrome."""
        if self._eg is None:
            return
        self._bucket = bucket
        self._items = list(bucket.items)

        # Teach the shared PhotoCache about this bucket's items so the
        # thumb tier is reachable for the items the surface is about to
        # navigate over. **Videos are filtered out** — the proxy builder
        # decodes via PIL.Image.open which fails on MP4 ("cannot
        # identify image file …"); videos play through QMediaPlayer
        # via the viewport, no still proxy ever needed.
        try:
            sha256_by_path: dict = {}
            for ci in self._items:
                if not getattr(ci, "path", None):
                    continue
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

        # Bulk EXIF prefetch — one subprocess for the whole bucket.
        self._spawn_exif_prefetch()

        states = self._eg.phase_states(self._phase)
        self._state = {
            ci.item_id: (
                states[ci.item_id].state
                if ci.item_id in states else None
            )
            for ci in self._items
        }

        n = len(self._items)
        self._index = max(0, min(entry_idx, n - 1)) if n else 0

        # Clear per-bucket caches.
        self._af_cache.clear()
        self._exif_cache.clear()
        self._sharpness_cache.clear()

        # Pre-load existing sharpness scores from the gateway (one pass).
        for ci in self._items:
            it = self._eg.item(ci.item_id)
            if it is None:
                continue
            if it.sharpness_score is not None:
                self._sharpness_cache[ci.item_id] = it.sharpness_score

        # Reset Play / Combined on bucket change. Peaking belongs to the
        # Sweep (and F10); everyday browse always starts clean.
        self._film_timer.stop()
        self._film_btn.blockSignals(True)
        self._film_btn.setChecked(False)
        self._film_btn.blockSignals(False)
        self._combined_on = False
        self._combined_cache = None
        self._combined_btn.blockSignals(True)
        self._combined_btn.setChecked(False)
        self._combined_btn.blockSignals(False)
        self.viewport.set_peaking_enabled(False)
        self.viewport.set_stack_film_peaking(bucket.kind == "focus_bracket")
        self._refresh_cluster_buttons()

        # Hand the items to the viewport — it shows the cursor item
        # immediately and ``current_changed`` dresses the chrome.
        self._sync_viewport_items(self._index)
        if not self._items:
            self._expo_overlay.set_html("")
            self._sync_state_pill(self._default_state)
        self.viewport.setFocus()

    def _sync_viewport_items(self, index: int) -> None:
        """Hand the current bucket's items to the viewport."""
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
        self.viewport.set_items(vitems, index)

    def _refresh_cluster_buttons(self) -> None:
        """Play / Combined visibility — driven by the bucket's kind."""
        kind = self._bucket.kind if self._bucket is not None else ""
        self._film_btn.setVisible(kind in _PLAY_KINDS)
        self._combined_btn.setVisible(kind in _COMBINED_KINDS)

    def _effective(self, item_id: str) -> str:
        return self._state.get(item_id) or self._default_state

    # ── EXIF + AF resolution ───────────────────────────────────────────

    def _show_exposure_overlay(self) -> bool:
        """spec/96 §2 — read the roaming Settings flag at call time so
        a toggle in the Settings dialog applies on the next item show
        without a relaunch. Defaults to True (preserves today's
        behaviour) when the setting is missing or the load fails."""
        try:
            from mira.settings.repo import SettingsRepo
            return bool(SettingsRepo().load().show_exposure_overlay)
        except Exception:                                          # noqa: BLE001
            return True

    @staticmethod
    def _file_size_text_for(path: Path, store_item: Any = None) -> str:
        """Filesystem stat → spec/96 chip-friendly size text. Falls
        back to ``store_item.byte_size`` (post-ingest, persisted by
        the gateway) when the live stat fails — the chip still shows
        the size when the source file moved between ingest and now
        (e.g., card pulled). Returns ``""`` when neither source
        carries a value."""
        try:
            return file_size_text(path.stat().st_size)
        except OSError:
            if store_item is not None:
                fallback = getattr(store_item, "byte_size", None)
                if fallback:
                    return file_size_text(fallback)
            return ""

    def _exif_for(self, path: Path):
        key = str(path)
        if key in self._exif_cache:
            return self._exif_cache[key]
        try:
            from core.exif_reader import read_exif_single
            exif = read_exif_single(path)
        except Exception as exc:                                   # noqa: BLE001
            log.debug("EXIF read failed for %s: %s", path.name, exc)
            exif = None
        self._exif_cache[key] = exif
        return exif

    def _spawn_exif_prefetch(self) -> None:
        """One ``read_exif_batch`` over every PHOTO item path in the
        current bucket. Generation-tagged so a moved cursor drops stale
        results. Videos are skipped — the exposure overlay is photo-only
        (the transport-bar reveal handles video chrome separately)."""
        self._exif_gen += 1
        gen = self._exif_gen
        paths = [
            Path(ci.path) for ci in self._items
            if getattr(ci, "path", None) is not None
            and getattr(ci, "kind", "photo") == "photo"
        ]
        if not paths:
            return
        signals = self._exif_prefetch_signals

        def _run():
            try:
                from core.exif_reader import read_exif_batch
                results = read_exif_batch(paths)
            except Exception:                                      # noqa: BLE001
                log.exception("EXIF prefetch batch failed")
                return
            by_path: dict = {}
            for photo in results:
                if photo is not None and photo.path is not None:
                    by_path[Path(photo.path)] = photo
            signals.fetched.emit(gen, by_path)

        threading.Thread(target=_run, daemon=True).start()

    def _on_exif_prefetched(self, gen: int, results: dict) -> None:
        if gen != self._exif_gen:
            return
        if not results:
            return
        filled = 0
        for ci in self._items:
            in_path = getattr(ci, "path", None)
            if in_path is None:
                continue
            cache_key = str(in_path)
            if cache_key in self._exif_cache:
                continue
            photo = results.get(Path(in_path))
            if photo is None:
                continue
            self._exif_cache[cache_key] = photo
            filled += 1
        if filled:
            log.info(
                "EXIF prefetch filled %d / %d items in bucket",
                filled, len(self._items))
            if 0 <= self._index < len(self._items):
                cur_path = getattr(
                    self._items[self._index], "path", None)
                if cur_path is not None and str(cur_path) in self._exif_cache:
                    try:
                        self._on_current_changed(self.viewport.current_index())
                    except Exception:                              # noqa: BLE001
                        log.exception("post-prefetch refresh failed")

    def _resolve_af(self, path: Path):
        key = str(path)
        if key in self._af_cache:
            return self._af_cache[key]
        af = None
        exif = self._exif_for(path)
        if exif is not None and getattr(exif, "raw", None):
            try:
                from core.brand_profile import match_brand_profile_for_photo
                prof = match_brand_profile_for_photo(exif.raw)
                if prof is not None:
                    af = prof.read_af_point(exif.raw)
            except Exception as exc:                               # noqa: BLE001
                log.debug("AF resolve failed for %s: %s", path.name, exc)
        self._af_cache[key] = af
        return af

    # ── Sharpness scoring (spec/63 §8 honesty) ────────────────────────

    def _refresh_sharpness(self, item) -> None:
        """Compute-on-miss sharpness + persist via the gateway. The score
        is taken from the viewport's SHARP pixels (the decoded native-tier
        pixmap), never from a placeholder. While only a placeholder is up
        this skips; the viewport's ``sharp_changed`` re-enters."""
        if item.item_id in self._sharpness_cache:
            return
        info = self.viewport.sharp_pixmap_info()
        if info is None:
            return
        raw = self._score_pixmap(info[0])
        self._sharpness_cache[item.item_id] = raw
        if self._eg is not None:
            try:
                self._eg.set_sharpness(item.item_id, raw)
            except Exception:                                      # noqa: BLE001
                log.debug("failed to persist sharpness for %s", item.item_id)

    def _on_sharp_landed(self) -> None:
        vp_item = self.viewport.current_item()
        if vp_item is None or vp_item.payload is None:
            return
        self._refresh_sharpness(vp_item.payload)

    @staticmethod
    def _score_pixmap(pm) -> float:
        if pm is None or pm.isNull():
            return 0.0
        try:
            import numpy as np
            from PyQt6.QtGui import QImage
            from core.sharpness import sharpness_score
            img = pm.toImage().convertToFormat(QImage.Format.Format_RGB888)
            w, h = img.width(), img.height()
            ptr = img.bits()
            ptr.setsize(img.bytesPerLine() * h)
            arr = np.frombuffer(
                bytes(ptr), dtype=np.uint8,
            ).reshape((h, img.bytesPerLine()))[:, : w * 3].reshape(
                (h, w, 3)
            )
            return sharpness_score(arr)
        except Exception as exc:                                   # noqa: BLE001
            log.debug("sharpness scoring failed: %s", exc)
            return 0.0

    # ── Chrome refresh on viewport landing ─────────────────────────────

    def _on_current_changed(self, index: int) -> None:
        """The viewport landed on ``index`` — dress the chrome."""
        vp_item = self.viewport.current_item()
        if vp_item is None or vp_item.payload is None:
            return
        self._index = index
        # Keep the day-position synced with the cursor on whole-day buckets
        # (cluster buckets keep day_index fixed at the entry point) so the
        # counter shows ONE figure, not two diverging ones (Nelson 2026-06-20).
        if self._bucket is not None and self._bucket.kind == "day":
            self._day_index = self._index + 1
        item = vp_item.payload
        kind = getattr(item, "kind", "photo")
        is_video = kind == "video"

        eff = self._effective(item.item_id)
        self._sync_state_pill(eff)

        # Exposure overlay ON the photo (spec/96 §2 — camera +
        # shutter / aperture / ISO / focal + type + size). The
        # roaming ``show_exposure_overlay`` setting gates the pill
        # across both single views; default True preserves the
        # historical behaviour.
        if is_video or not self._show_exposure_overlay():
            self._expo_overlay.set_html("")
        else:
            # Two sources merge: live EXIF (read off the file each
            # time the chip refreshes — fast on the cache) AND the
            # gateway's store Item (post-ingest, populated once
            # with the EXIF reader's full pass + the user's edits).
            # Some camera bodies' EXIF returns the Model tag but
            # zeroes the FNumber / ExposureTime tags on a live re-
            # read — the chip then shows camera but no exposure,
            # which is the report Nelson hit 2026-06-22. The store
            # Item has the post-ingest values, so we use it as the
            # fallback per param via :func:`exposure_for_chip`.
            exif = self._exif_for(item.path)
            store_item = (
                self._eg.item(item.item_id)
                if self._eg is not None else None
            )
            camera = (
                (store_item.camera_id or "") if (
                    store_item is not None
                    and getattr(store_item, "camera_id", None))
                else (getattr(exif, "model", "")
                      if exif is not None else "")
            )
            exposure_html = exposure_for_chip(exif, store_item)
            type_label = file_type_label(item.path.suffix)
            size_text = self._file_size_text_for(item.path, store_item)
            self._expo_overlay.set_html(source_chip_html(
                camera=camera,
                type_label=type_label,
                size_text=size_text,
                exposure_html=exposure_html,
            ))

        # AF — the viewport stores it for F10's inspection overlay.
        self.viewport.set_af_point(self._resolve_af(item.path))

        # spec/59 §8 / spec/66 §1.2 — the diagonal "Exported" overlay
        # over the displayed image when the item shipped (lineage row
        # under ``Exported Media/``) AND the user hasn't hidden the
        # indicator via ``show_exported_watermark``. Videos and
        # snapshots are out of scope here (the watermark is photo-only).
        shipped = (
            self._watermark_enabled
            and not is_video
            and item.item_id in self._exported_set
        )
        self.viewport.set_exported_watermark(shipped)

        # The lens button only makes sense for photos.
        self._fullres_btn.setVisible(not is_video)

        # spec/70 row 11 — the transport bar appears only on a landed
        # video (Nelson 2026-06-15: "a few transport buttons appear").
        # The whole compact_row collapses on photos so the canvas takes
        # the space; the row appears with the transport when a video
        # lands. ffprobe seeds the frame-step size lazily per path.
        self._video_pos_ms = 0
        self._video_duration_ms = 0
        if is_video:
            self._seed_video_metadata(item.path)
            self._transport_bar.set_position(0, self._video_duration_ms)
            self._transport_bar.set_playing(False)
            self._transport_bar.setVisible(True)
            self._set_transport_band_hollow(False)   # video → boxed
        else:
            self._transport_bar.setVisible(False)
            self._set_transport_band_hollow(True)    # photo → borderless

        # Sharpness (skips until sharp pixels land). Skip for videos —
        # the score is photo-only.
        if not is_video:
            self._refresh_sharpness(item)

        # Position chip.
        self._refresh_position_label()

    # ── Video transport handlers ───────────────────────────────────────

    def _seed_video_metadata(self, path) -> None:
        """Probe (once per path) for duration so the scrubber paints +
        the |▶ end-jump snaps to the real endpoint before
        QtMultimedia's async ``durationChanged`` arrives. Best-effort —
        a failed probe leaves the duration at 0 until the player
        reports it."""
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
        if dur > 0:
            self._video_duration_ms = dur

    def _on_video_position(self, pos_ms: int) -> None:
        self._video_pos_ms = max(0, int(pos_ms))
        self._transport_bar.set_position(
            self._video_pos_ms, self._video_duration_ms)

    def _on_video_duration(self, dur_ms: int) -> None:
        self._video_duration_ms = max(0, int(dur_ms))
        self._transport_bar.set_position(
            self._video_pos_ms, self._video_duration_ms)

    def _on_video_speed_changed(self, text: str) -> None:
        """Parse the speed selector's "0.5×" / "1×" / "2×" labels and
        push the rate to the viewport's media player."""
        try:
            rate = float(text.replace("×", "").strip())
        except ValueError:
            return
        self.viewport.video_set_playback_rate(rate)

    def _refresh_position_label(self) -> None:
        if not self._items:
            self._position_label.setText("")
            return
        in_n = self._index + 1
        in_total = len(self._items)
        # One figure when the day-position and the bucket-position agree (the
        # whole-day case); the dual "day · cluster" only when a cluster
        # sub-grid makes them genuinely differ (mirrors the Editor).
        same = (in_n == self._day_index and in_total == self._day_total)
        if not same and in_total > 1:
            txt = f"{self._day_index} / {self._day_total}  ·  {in_n} / {in_total}"
        else:
            txt = f"{in_n} / {in_total}"
        self._position_label.setText(txt)

    def _sync_state_pill(self, state: str) -> None:
        """Mirror the state onto the action cluster + media border."""
        self._pick_btn.setChecked(state == STATE_PICKED)
        self._skip_btn.setChecked(state == STATE_SKIPPED)
        self._compare_btn.setChecked(state == STATE_CANDIDATE)
        self._surface.set_media_state(state)

    # ── Cluster Play / Combined (absorbed) ─────────────────────────────

    def _on_sweep_key(self) -> None:
        """Enter — play/pause the cluster sweep. Inert on non-playable kinds."""
        if not self._film_btn.isVisible():
            return
        self._film_btn.toggle()
        self._toggle_film()

    def _toggle_film(self) -> None:
        """Play/Pause the cluster sweep — paced navigation from the first
        non-discarded frame to the last (no auto-loop). Peaking rides
        along on the FAST stack-film path."""
        if self._combined_on:
            self._film_btn.setChecked(False)
            return
        if self._film_btn.isChecked():
            has_next = self._first_playable_from(
                self._index + 1, inclusive=True) is not None
            if has_next:
                start = self._first_playable_from(
                    self._index, inclusive=True)
            else:
                start = self._first_playable_from(0, inclusive=True)
            if start is None:
                self._film_btn.setChecked(False)
                return
            if start != self._index:
                self.viewport.show_index(start)
            self.viewport.set_stack_film_peaking(True)
            self.viewport.set_peaking_enabled(True)
            set_transport_playing(self._film_btn, True)
            self._film_timer.start()
        else:
            self._film_timer.stop()
            set_transport_playing(self._film_btn, False)
            self.viewport.set_peaking_enabled(False)

    def _film_step(self) -> None:
        nxt = self._first_playable_from(self._index + 1, inclusive=True)
        if nxt is None:
            self._film_timer.stop()
            self._film_btn.blockSignals(True)
            self._film_btn.setChecked(False)
            self._film_btn.blockSignals(False)
            set_transport_playing(self._film_btn, False)
            self.viewport.set_peaking_enabled(False)
            return
        self.viewport.show_index(nxt)

    def _first_playable_from(
        self, start: int, *, inclusive: bool,
    ) -> Optional[int]:
        if not self._items:
            return None
        begin = max(0, int(start) + (0 if inclusive else 1))
        for idx in range(begin, len(self._items)):
            explicit = self._state.get(self._items[idx].item_id)
            if explicit == STATE_SKIPPED:
                continue
            return idx
        return None

    def _combined_pixmap(self):
        if self._combined_cache is not None:
            return self._combined_cache
        if not self._items:
            return None
        try:
            from core.exposure_fusion import fuse_exposures
            from mira.ui.media.image_loader import load_pixmap
        except ImportError:
            log.exception("exposure_fusion unavailable")
            return None
        frames = []
        for ci in self._items:
            pm = load_pixmap(ci.path)
            if pm is not None and not pm.isNull():
                frames.append(pm)
        if not frames:
            return None
        try:
            self._combined_cache = fuse_exposures(frames)
        except Exception:                                          # noqa: BLE001
            log.exception("fuse_exposures failed")
            return None
        return self._combined_cache

    def _toggle_combined(self) -> None:
        if self._combined_btn.isChecked():
            if self._film_btn.isChecked():
                self._film_btn.setChecked(False)
                self._toggle_film()
            pm = self._combined_pixmap()
            if pm is None or pm.isNull():
                self._combined_btn.setChecked(False)
                return
            self._combined_on = True
            self.viewport.set_items(
                [ViewportItem(kind="card", pixmap=pm)], 0)
        else:
            self._combined_on = False
            self._sync_viewport_items(self._index)

    # ── Navigation + decision verbs ────────────────────────────────────

    def _go(self, index: int) -> None:
        if not self._items or self._combined_on:
            return
        if index < 0:
            self._on_edge(-1)
            return
        if index >= len(self._items):
            self._on_edge(+1)
            return
        self.viewport.show_index(index)

    def _on_edge(self, delta: int) -> None:
        """Stop at bucket edges — the Days Grid bridge owns cross-cell nav
        (the user backs out to the grid and clicks the next cell).

        spec/70 §1 keeps the Picker reconciliation surface-scoped; cross-
        surface day-cell nav is the Days Grid's job. ``_combined_on``
        also stops here — one synthetic image, nav is locked.
        """
        # No-op intentionally. The bridge model means ← / → at the bucket
        # edge stays put; the user presses Esc to leave.

    def _persist_state(self, nxt: str) -> None:
        # spec/76 §B.1 — decision verbs are no-ops in read-only mode.
        # The Picker chrome shows the "Library is read-only" hint via
        # tooltip; we additionally guard here so a stray keystroke /
        # border-click doesn't reach the gateway.
        from mira.session import is_read_only
        if is_read_only():
            return
        item = self._items[self._index]
        self._eg.set_phase_state(item.item_id, self._phase, nxt)
        self._state[item.item_id] = nxt
        self._sync_state_pill(nxt)

    def _set_state(self, state: str) -> None:
        """P / X — SET the decision (never a toggle; spec/63 §4)."""
        if not self._items or self._eg is None:
            return
        self._persist_state(state)

    def _toggle_pick_skip(self) -> None:
        """Space — the binary toggle Pick ⇄ Skip (spec/63 §4)."""
        if not self._items or self._eg is None:
            return
        cur = self._effective(self._items[self._index].item_id)
        self._persist_state(
            STATE_PICKED if cur == STATE_SKIPPED else STATE_SKIPPED)

    def _cycle(self) -> None:
        """C / border-click — Pick → Skip → Compare → Pick."""
        if not self._items or self._eg is None:
            return
        cur = self._effective(self._items[self._index].item_id)
        nxt = _CYCLE[(_CYCLE.index(cur) + 1) % len(_CYCLE)]
        self._persist_state(nxt)

    def _cycle_to_compare(self) -> None:
        """Compare button — set the current item to Compare directly."""
        if not self._items or self._eg is None:
            return
        self._persist_state(STATE_CANDIDATE)

    # ── Fullscreen ─────────────────────────────────────────────────────

    def _toggle_fullscreen(self) -> None:
        win = self.window()
        if win is None:
            return
        self._fullscreen = not self._fullscreen
        self._fullscreen_btn.setChecked(self._fullscreen)
        if self._fullscreen:
            win.showFullScreen()
        else:
            win.showNormal()
        self.fullscreen_changed.emit(self._fullscreen)
        self.viewport.setFocus()

    def _exit_fullscreen(self) -> bool:
        if self._fullscreen:
            self._toggle_fullscreen()
            return True
        return False

    # ── Back / Esc ──────────────────────────────────────────────────────

    def _on_esc(self) -> None:
        """Esc — one level back: fullscreen → windowed → out."""
        if not self._exit_fullscreen():
            self._on_back()

    def _on_back(self) -> None:
        self._save_cursor()
        self._film_timer.stop()
        self.viewport.set_peaking_enabled(False)
        # Release the gateway when the user is done with this bucket.
        self._close_event()
        self.closed.emit()

    def _save_cursor(self) -> None:
        if (self._eg is not None and self._bucket is not None
                and self._items):
            try:
                self._eg.set_bucket_current_index(
                    self._bucket.bucket_key, self._phase, self._index)
            except Exception:                                      # noqa: BLE001
                log.exception("failed to save bucket cursor")

    # ── Stray-focus keyboard fallback ─────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent) -> None:              # noqa: N802
        """The viewport owns the locked grammar (we proxy focus there);
        these are stray-focus fallbacks routing to the SAME handlers."""
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._on_esc()
            event.accept()
            return
        if key in (Qt.Key.Key_F, Qt.Key.Key_F11):
            self._toggle_fullscreen()
            event.accept()
            return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self._go(self._index - 1)
            event.accept()
            return
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self._go(self._index + 1)
            event.accept()
            return
        if key == Qt.Key.Key_Home:
            self._go(0)
            event.accept()
            return
        if key == Qt.Key.Key_End:
            self._go(len(self._items) - 1)
            event.accept()
            return
        super().keyPressEvent(event)
