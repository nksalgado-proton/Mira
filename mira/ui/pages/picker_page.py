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
from typing import Optional

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from mira.gateway import Gateway
from mira.picked import CullBucket, CullCluster, CullItem
from mira.picked.exif_compare import caption_html
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
    danger_ghost_button,
    ghost_button,
    nav_arrow,
)
from mira.ui.i18n import tr
from mira.ui.media.photo_cache import photo_cache
from mira.ui.media.photo_overlay import PhotoExposureOverlay
from mira.ui.media.photo_viewport import PhotoViewport, ViewportItem

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

        self._build_ui()

    # ── UI assembly ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Surface scaffold — the state border lives on this host. ──
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

        # ── TOP_BAR — ‹ Back · stretch · position chip ──
        self._back_btn = ghost_button(tr("‹ Back"))
        self._back_btn.setToolTip(tr("Return to the day grid  (Esc)"))
        self._back_btn.clicked.connect(self._on_back)
        self._surface.top_bar.layout().addWidget(self._back_btn)
        self._surface.top_bar.layout().addStretch(1)
        self._position_label = QLabel("")
        self._position_label.setObjectName("Sub")
        self._position_label.setToolTip(tr(
            "Position in the current day — item index of total."))
        self._surface.top_bar.layout().addWidget(self._position_label)

        # ── STATE_BAR — hidden. State lives on the MediaHost border. ──
        self._surface.set_region_visible("state_bar", False)
        # ── COMPACT_ROW — hidden (position lives in the top bar now). ──
        self._surface.set_region_visible("compact_row", False)
        # ── TOOLS — hidden (cluster controls moved into the nav row). ──
        self._surface.set_region_visible("tools", False)

        # ── NAV — left arrow · action cluster · right arrow ──
        nav_layout = self._surface.nav.layout()
        # The redesign's floating ‹ / › nav arrows live inside the nav row
        # for now (until we add them as overlay children on the viewport).
        self._prev_btn = nav_arrow("left")
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
        self._fullscreen_btn = ghost_button(tr("Full Screen  F11"))
        self._fullscreen_btn.setCheckable(True)
        self._fullscreen_btn.setToolTip(tr(
            "Use the whole screen for picking  (F / F11)"))
        self._fullscreen_btn.clicked.connect(self._toggle_fullscreen)
        nav_layout.addWidget(self._fullscreen_btn)

        nav_layout.addStretch(1)
        self._next_btn = nav_arrow("right")
        self._next_btn.setToolTip(tr("Next photo  (→)"))
        self._next_btn.clicked.connect(lambda: self._go(self._index + 1))
        nav_layout.addWidget(self._next_btn)

        # The viewport's corner inspect button is suppressed — the labelled
        # Full Resolution button covers it.
        self.viewport.set_corner_inspect_visible(False)

        # Canonical media-border click cycles state.
        self._surface.media_border_clicked.connect(self._cycle)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ── Public entry points (Days Grid bridge) ────────────────────────

    def open_to_item(
        self, event_id: str, day_number: int, item_id: str,
    ) -> bool:
        """Open the Picker on a flat single-item click from the Days Grid
        (Surface 06). Builds a synthetic 1-item bucket so the Picker chrome
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
        # spec/32 §2.10 item tick.
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
        return True

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
        """Wrap a single CullItem in a 1-item bucket so the page's
        bucket-shaped load path lights up uniformly."""
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

    def _compute_day_position(self, day_number: int, item_id: str) -> None:
        """Resolve "Cell N of Total" for the day-grid position chip. We
        ask the gateway for the day's cells once per item open — same
        engine the Days Grid uses, so a second call is cache-hit cheap."""
        if self._eg is None:
            return
        try:
            from mira.picked import day_grid_cells
            cells = day_grid_cells(
                self._eg, day_number, phase=self._phase,
                default_state=self._default_state)
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
        # navigate over.
        try:
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
        """One ``read_exif_batch`` over every item path in the current
        bucket. Generation-tagged so a moved cursor drops stale results."""
        self._exif_gen += 1
        gen = self._exif_gen
        paths = [
            Path(ci.path) for ci in self._items
            if getattr(ci, "path", None) is not None
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
        item = vp_item.payload

        eff = self._effective(item.item_id)
        self._sync_state_pill(eff)

        # Exposure overlay ON the photo.
        exif = self._exif_for(item.path)
        self._expo_overlay.set_html(
            caption_html(exif)
            if getattr(item, "kind", "photo") == "photo" else "")

        # AF — the viewport stores it for F10's inspection overlay.
        self.viewport.set_af_point(self._resolve_af(item.path))

        # The lens button only makes sense for photos.
        self._fullres_btn.setVisible(
            getattr(item, "kind", "photo") == "photo")

        # Sharpness (skips until sharp pixels land).
        self._refresh_sharpness(item)

        # Position chip.
        self._refresh_position_label()

    def _refresh_position_label(self) -> None:
        if len(self._items) > 1:
            in_n = self._index + 1
            in_total = len(self._items)
            txt = f"{self._day_index} / {self._day_total}  ·  {in_n} / {in_total}"
        else:
            txt = f"{self._day_index} / {self._day_total}"
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
