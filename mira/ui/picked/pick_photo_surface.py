"""The per-bucket photo cull surface — on the ONE display engine
(spec/63 §8, the 5d full absorb).

The surface owns chrome and decisions; the embedded
:class:`PhotoViewport` owns pixels (spec/63 §1): placeholder→sharp
display, prefetch-on-settle, the app-wide pixmap budget, F10 (the
inspection lens: full-res + honest peaking + zoom + AF), and the
LOCKED keyboard grammar (spec/63 §4) translated once into verb
signals. This widget keeps the **data seam** (gateway marks, the
decision verbs, bucket soft-state, edge routing) and the Pick chrome
(state pill/border, genre + Reclassify, exposure overlay, position,
the cluster Play sweep + exposure-bracket Combined preview).

The locked map, as wired here: **P** Pick · **X** Skip · **Space**
toggle Pick⇄Skip · **C** cycle Pick→Skip→Compare · **Enter**
play/pause the cluster sweep (peaking rides it — the stack-film fast
mode, spec/63 §7 Sweep) · **F10** the inspection lens · **F/F11**
fullscreen · **Esc** one level back · arrows/wheel navigate. The
surface adds only R (reclassify), Home/End, and F1 — keys the
viewport ignores propagate up to its small ``keyPressEvent``.

Retired by the absorb (now live inside F10's InspectView): the
in-view zoom cluster, the peaking cluster + colour/sensitivity, the
AF toggle, and their Z/F bindings. The legacy P-sweep moved to Enter;
Shift+P-Combined is button-only.

Signals:
  * ``back_requested``           — Esc / Back button
  * ``fullscreen_changed(bool)`` — shell hides/restores its chrome
  * ``navigate_at_edge(int)``    — spec/32 §2.7 day-grid edge step
  * legacy bucket-list edge signals (PickPage connects them to no-ops)

Bound to the gateway only; the resume cursor (bucket ``current_index``)
is restored on open and saved on Back.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor, QKeyEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mira.picked import CullBucket
from mira.picked.exif_compare import caption_html
from mira.picked.status import STATE_CANDIDATE, STATE_SKIPPED, STATE_PICKED
from mira.ui.base.surface import (
    BasePickSurface,
    back_button,
    help_button,
    kd_pill,
    populate_nav_row,
    set_transport_playing,
    transport_button,
)
from mira.ui.i18n import tr
from mira.ui.media.photo_cache import photo_cache
from mira.ui.media.photo_overlay import PhotoExposureOverlay
from mira.ui.media.photo_viewport import PhotoViewport, ViewportItem

log = logging.getLogger(__name__)

# Cycle order matches the legacy culler: discard → KEEP → compare → (wrap).
_CYCLE = (STATE_SKIPPED, STATE_PICKED, STATE_CANDIDATE)
_KIND_LABEL = {
    "focus_bracket": "Focus bracket", "exposure_bracket": "Exposure bracket",
    "burst": "Burst", "moment": "Moment", "individual": "Individuals",
    "video": "Video", "video_moment": "Video moment",
}
# Bracket kinds that get atomic K/D in Select mode (whole-stack decision only).
_BRACKET_KINDS = frozenset(("focus_bracket", "exposure_bracket"))
# Cluster kinds the Play button steps as a sequence (legacy: focus_bracket only;
# Nelson 2026-06-06 extension: burst + exposure_bracket too, since the user wants to
# review burst sequences and exposure brackets as a paced sweep).
_PLAY_KINDS = frozenset(("burst", "focus_bracket", "exposure_bracket"))
# Cluster kind that gets the legacy "Combined" exposure-fusion preview button.
_COMBINED_KINDS = frozenset(("exposure_bracket",))
# Film cadence (ms per frame). Legacy default was 150 (~6-7 fps); Nelson
# 2026-06-06 wanted it slower so the focus shift is actually visible per frame.
# 400 ms ≈ 2.5 fps — leisurely enough to register each frame without dragging.
_FILM_MS = 400


class _ExifPrefetchSignals(QObject):
    """Cross-thread signal carrier for the bulk EXIF prefetcher.

    Nelson 2026-06-09 fast-nav redesign (follow-up). The PhotoCache
    redesign made the JPEG decode async, but ``_exif_for`` still spawns
    an ``exiftool.exe`` subprocess per photo (~300-500 ms cold on
    Windows). On a 482-photo day that's the actual 1-2 s "click → wait
    → photo lands" delay the user feels. ``read_exif_batch`` (already
    in ``core.exif_reader``) reads N files in ONE subprocess via the
    argfile path, so a whole bucket warms in roughly the cost of a
    single per-file call. We spawn it on ``load()`` in a daemon
    thread and feed results back to the surface via this signal."""
    fetched = pyqtSignal(int, dict)


class PickPhotoSurface(QWidget):
    """Rich per-bucket culler — chrome + decisions over a ``PhotoViewport``."""

    back_requested = pyqtSignal()
    fullscreen_changed = pyqtSignal(bool)  # shell hides/restores its chrome
    prev_bucket_requested = pyqtSignal()
    next_bucket_requested = pyqtSignal()
    # Emitted when the user tries to leave the first/last photo of a bucket via navigation
    # (arrow, button, wheel) and the next bucket is in the same day — PickPage handles the
    # actual bucket switch.  Silent (not emitted) at day boundaries. (Legacy bucket-list
    # mode signals; still used by PickPage until M5.)
    prev_bucket_from_first_photo = pyqtSignal()
    next_bucket_from_last_photo = pyqtSignal()
    # spec/32 §2.7 — single unified "navigate at edge" signal for the Day Grid model.
    # Delta is -1 (prev) or +1 (next). The host (PickPage) routes it: in Day Grid
    # context it steps the day-cell cursor; in Cluster context it is never emitted
    # (cluster edges stop). Replaces the four legacy bucket signals when ``nav_context``
    # is ``"day_grid"`` or ``"cluster"``.
    navigate_at_edge = pyqtSignal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("PickPhotoSurface")
        self._eg = None
        self._phase = "pick"
        self._bucket: Optional[CullBucket] = None
        self._items: list = []
        self._index = 0
        self._state: dict = {}            # item_id -> explicit state | None (untouched)
        self._default_state = STATE_SKIPPED
        self._fullscreen = False
        self._is_first_in_day = False   # hard-stop prev-boundary
        self._is_last_in_day = False    # hard-stop next-boundary

        # -- Total bucket count / index (set by PickPage for the position readout).
        self._bucket_index = 1
        self._bucket_count = 1
        # -- spec/32 §2.7 — navigation context: "bucket" (legacy) / "day_grid" / "cluster"
        # plus optional label suffix (e.g. " · Cell 5/12 · Day 1") for the position chip.
        self._nav_context: str = "bucket"
        self._nav_label_suffix: str = ""

        # -- AF / EXIF / sharpness caches (per-bucket, cleared on load).
        self._af_cache: dict[str, object] = {}
        self._exif_cache: dict[str, object] = {}
        self._sharpness_cache: dict[str, float] = {}   # item_id -> raw score

        # Play/Pause for cluster sequences (ported from legacy ingest_culler_page;
        # legacy: focus brackets only — extended 2026-06-06 to burst + exposure too).
        # The timer paces frame steps at ``_FILM_MS``; _film_step skips explicit
        # Skips. The Sweep carries the viewport's FAST stack-film peaking
        # (spec/63 §7 — the user watches focus travel through the burst).
        self._film_timer = QTimer(self)
        self._film_timer.setInterval(_FILM_MS)
        self._film_timer.timeout.connect(self._film_step)
        # Exposure-bracket "Combined" non-destructive preview (legacy verbatim).
        # ``_combined_on`` locks per-frame nav (one synthetic image, shown as a
        # viewport loose slide); the cache is built lazily, reset per bucket load.
        self._combined_on = False
        self._combined_cache = None
        # Bulk-EXIF prefetcher. Each ``load()`` spawns one daemon
        # thread that runs ``core.exif_reader.read_exif_batch`` over
        # every item in the bucket — single subprocess instead of N.
        # Results merge into ``_exif_cache`` via the signal so the
        # subsequent ``_exif_for`` calls on every navigation are dict
        # lookups, not subprocess spawns. Generation counter drops
        # stale results when the user moves to a different bucket
        # before the prefetch lands.
        self._exif_gen = 0
        self._exif_prefetch_signals = _ExifPrefetchSignals()
        self._exif_prefetch_signals.fetched.connect(
            self._on_exif_prefetched)

        # ── Outer + BasePickSurface skeleton (spec/42 Alternative B) ──────
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._surface = BasePickSurface()
        outer.addWidget(self._surface)

        # ── MEDIA — the one display engine (spec/63 5d). Pixels, nav,
        # prefetch, F10 and the locked key grammar live in the viewport;
        # the surface reacts to its verbs + signals. Predecode-on-settle
        # moved INTO the viewport (slice 1) — the surface's own timer died.
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
        # Keys aimed at the surface (PickPage does ``photo.setFocus()``)
        # land on the viewport — the one place the grammar lives.
        self.setFocusProxy(self.viewport)

        # Exposure overlay ON the photo (Nelson 2026-06-01) — a pill at the
        # bottom of the media area; it follows the host's resizes itself.
        self._expo_overlay = PhotoExposureOverlay(self.viewport)

        # ── TOP_BAR — back · stretch · help ───────────────────────────
        # Nelson 2026-06-13: the genre readout + Reclassify dropdown
        # retired here — photographic classification (Macro / Wildlife /
        # Birds / Landscape / Urban-Street / None) only surfaces in the
        # Edit phase, where it drives the Auto-correction recipes. The
        # data layer stays (background classify_pass tags items on event
        # open; the gateway's set_classification accepts user overrides
        # from Edit).
        self._back_btn = back_button()
        self._back_btn.setToolTip(tr("Return to the cluster list  (Esc)"))
        self._back_btn.clicked.connect(self._on_back)
        self._surface.top_bar.layout().addWidget(self._back_btn)

        self._surface.top_bar.layout().addStretch(1)

        # Help ?
        self._help_btn = help_button()
        self._help_btn.setToolTip(tr("Keyboard shortcuts  (F1)"))
        self._help_btn.clicked.connect(self._show_help)
        self._surface.top_bar.layout().addWidget(self._help_btn)

        # ── STATE_BAR — hidden (spec/42, Nelson 2026-06-04).
        # State is shown as a coloured border on the BasePickSurface MEDIA
        # host (``QWidget#MediaHost[state="…"]``). Clicking the border (or
        # C) cycles. The ``_state_pill`` attribute is kept (tests reference
        # it + ``_sync_state_pill`` updates its text/state) but the widget
        # is never displayed.
        self._state_pill = kd_pill()
        self._state_pill.setText(tr("✓ Pick"))
        self._state_pill.setProperty("state", "picked")
        self._state_pill.clicked.connect(self._cycle)
        self._surface.set_region_visible("state_bar", False)

        # ── COMPACT_ROW — X/Y position indicator only (Nelson 2026-06-06).
        compact = self._surface.compact_row.layout()
        compact.addStretch(1)
        self._position_label = QLabel("")
        self._position_label.setObjectName("PickPositionLabel")
        self._position_label.setToolTip(tr(
            "Position in the current day — item index of total."))
        compact.addWidget(self._position_label)
        compact.addStretch(1)
        self._surface.set_region_visible("compact_row", True)

        # ── The TOOLS row died (Nelson 2026-06-12 UI round): it sat
        # EMPTY for plain photos — three lines under the canvas where
        # the video surface uses two. The cluster affordances (Play ·
        # Combined) + the Full Resolution View button live in the NAV
        # row's centre slot instead, between Previous and Next.
        centre = QWidget()
        crow = QHBoxLayout(centre)
        crow.setContentsMargins(0, 0, 0, 0)
        crow.setSpacing(8)
        self._build_cluster_controls(crow)
        self._surface.set_region_visible("tools", False)

        # ── NAV — ← Previous · (Play · Combined · Full Resolution View)
        # · Next → ──
        nav = populate_nav_row(
            self._surface, with_buckets=False, centre_widget=centre)
        nav.prev.clicked.connect(lambda: self._go(self._index - 1))
        nav.next.clicked.connect(lambda: self._go(self._index + 1))
        self._nav_prev = nav.prev
        self._nav_next = nav.next

        # Canonical media-border click (BasePickSurface) cycles state.
        self._surface.media_border_clicked.connect(self._cycle)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ── chrome: the state pill + border ───────────────────────────────

    def _sync_state_pill(self, state: str) -> None:
        """Mirror the cull state onto the (hidden) BasePickSurface KDPill AND
        push it to the BasePickSurface MEDIA host so its canonical state
        border paints. The MEDIA-border-as-state mechanism is the SAME
        one QuickSweepPage and VideoPickPage use (spec/42 Nelson
        2026-06-04)."""
        labels = {
            STATE_PICKED: "✓ Pick",
            STATE_SKIPPED: "⊘ Skip",
            STATE_CANDIDATE: "Compare",
        }
        self._state_pill.setText(tr(labels.get(state, "⊘ Skip")))
        self._state_pill.setProperty("state", state)
        # Re-polish to re-evaluate the [state="…"] QSS selectors.
        self._state_pill.style().unpolish(self._state_pill)
        self._state_pill.style().polish(self._state_pill)
        self._surface.set_media_state(state)

    def _build_cluster_controls(self, b2) -> None:
        """The NAV-row centre cluster: Play (burst + brackets), Combined
        (exposure brackets) and Full Resolution View. Play/Combined
        visibility is bucket-kind-driven (:meth:`_refresh_clusters`);
        the lens button follows the current item's kind."""
        # Play / Pause — visible only in cluster buckets (burst + brackets).
        # Steps the bucket items in sequence so the user sees the sweep / burst
        # play out as a paced movie, with the viewport's FAST stack-film
        # peaking riding along (spec/63 §7 Sweep — watch focus travel).
        # TransportButton role (Nelson 2026-06-12 UI round): the
        # earlier FeatureToggle borrow turned the button accent-coloured
        # when checked (the "non-house colour" Nelson called out). The
        # transport factory pins a fixed width too, so swapping ▶ ↔ ⏸
        # never shifts the neighbouring Compare / Lens buttons.
        self._film_btn = transport_button(
            tr("Play the cluster as a sequence so you see the frames "
               "sweep  (Enter)"))
        self._film_btn.setCheckable(True)
        self._film_btn.clicked.connect(self._toggle_film)
        self._film_btn.setVisible(False)
        b2.addWidget(self._film_btn)

        # Combined (exposure-bracket only): non-destructive preview of the
        # fused exposure stack — a decision aid, never saved. Button-only
        # (the legacy Shift+P binding retired with the locked map).
        self._combined_btn = QPushButton(tr("Combined"))
        self._combined_btn.setObjectName("FeatureToggle")
        self._combined_btn.setCheckable(True)
        self._combined_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._combined_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._combined_btn.setToolTip(
            tr("Preview the exposure-fused composite of the whole bracket")
        )
        self._combined_btn.clicked.connect(self._toggle_combined)
        self._combined_btn.setVisible(False)
        b2.addWidget(self._combined_btn)

        # Full Screen + Full Resolution — the standard centre pair on
        # every photo nav line (Nelson 2026-06-12 standardisation; the
        # labelled lens button replaced the viewport's corner 🔍).
        from mira.ui.base.surface import feature_toggle
        self._fullscreen_btn = feature_toggle(tr("Full Screen"))
        self._fullscreen_btn.setToolTip(tr(
            "Use the whole screen for culling  (F / F11)"))
        self._fullscreen_btn.clicked.connect(self._toggle_fullscreen)
        b2.addWidget(self._fullscreen_btn)
        self._fullres_btn = QPushButton(tr("Full Resolution"))
        self._fullres_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._fullres_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._fullres_btn.setToolTip(tr(
            "Inspect this frame at full resolution — peaking, true 1:1 "
            "zoom, AF point  (F10)"))
        self._fullres_btn.clicked.connect(self.viewport.truth_requested.emit)
        b2.addWidget(self._fullres_btn)
        self.viewport.set_corner_inspect_visible(False)

        self._refresh_clusters()

    def _refresh_clusters(self, *_a) -> None:
        """Play / Combined visibility — driven by the current bucket's
        cluster kind. Play applies to burst + focus + exposure brackets;
        Combined applies to exposure brackets only."""
        kind = self._bucket.kind if self._bucket is not None else ""
        self._film_btn.setVisible(kind in _PLAY_KINDS)
        self._combined_btn.setVisible(kind in _COMBINED_KINDS)

    # ── Play / Combined (cluster surfaces — legacy port + 2026-06-06 ext) ──

    def start_play(self) -> None:
        """Programmatically launch the cluster slideshow.

        Used by PickPage when the user clicks Play on the cluster
        sub-grid: load() has already painted the first member, so we
        just flip ``_film_btn`` on and reuse the same toggle the user's
        click runs. No-op if the button isn't visible (current bucket
        isn't a playable cluster kind) or the combined preview is on.
        """
        if not self._film_btn.isVisible() or self._combined_on:
            return
        if self._film_btn.isChecked():
            return
        self._film_btn.setChecked(True)
        self._toggle_film()

    def _on_sweep_key(self) -> None:
        """Enter — play/pause the cluster sweep (spec/63 §4). Inert when
        the bucket isn't a playable cluster kind."""
        if not self._film_btn.isVisible():
            return
        self._film_btn.toggle()
        self._toggle_film()

    def _toggle_film(self) -> None:
        """Play/Pause the cluster sweep — paced navigation from the first
        non-discarded frame to the last (no auto-loop; Nelson 2026-06-06).
        While playing, the viewport's FAST stack-film peaking is on
        (spec/63 §7 Sweep — compute on the quick display pixels so frames
        keep flipping; the honest half-res path is F10's). Locked-out
        while the exposure-Combined preview is on (one synthetic image,
        not frame-by-frame).

        **User-initiated rewind** (Nelson 2026-06-06b): clicking Play
        rewinds the cursor to the first playable frame WHEN the cursor is
        at or past the last playable frame — standard video-player
        semantics: "Play at the end means start over". This is distinct
        from auto-looping (which only happens via user re-click, never
        autonomously). Mid-sequence Play continues from the cursor.
        """
        if self._combined_on:
            self._film_btn.setChecked(False)
            return
        if self._film_btn.isChecked():
            # Where does Play actually start?
            # • If a playable frame exists STRICTLY after the cursor, the
            #   sweep can continue forward — start from the cursor (or the
            #   first playable at-or-after, if cursor sits on a discarded).
            # • If not, the cursor is at/past the last playable frame —
            #   rewind to the first playable so Play means "start over".
            has_next = self._first_playable_from(
                self._index + 1, inclusive=True) is not None
            if has_next:
                start = self._first_playable_from(self._index, inclusive=True)
            else:
                start = self._first_playable_from(0, inclusive=True)
            if start is None:
                # Truly nothing to play (every frame discarded) → revert.
                self._film_btn.setChecked(False)
                return
            if start != self._index:
                self.viewport.show_index(start)
            # Sweep-with-peaking (Nelson 2026-06-12): fast stack-film
            # peaking ON for the ride; the everyday browse stays clean.
            self.viewport.set_stack_film_peaking(True)
            self.viewport.set_peaking_enabled(True)
            set_transport_playing(self._film_btn, True)
            self._film_timer.start()
        else:
            self._film_timer.stop()
            set_transport_playing(self._film_btn, False)
            self.viewport.set_peaking_enabled(False)

    def _film_step(self) -> None:
        """One film tick: advance to the next playable frame **after** the
        current index. **No wrap** (Nelson 2026-06-06): when no playable
        frame remains ahead, the sweep stops at the last playable position
        and the user can hit Play again to replay.

        **Discarded frames are skipped** — explicit Skips only; untouched
        items play even when the phase default is discard (the user hasn't
        dismissed them yet; ``feedback_no_untouched_status_users_see_default``).
        """
        nxt = self._first_playable_from(self._index + 1, inclusive=True)
        if nxt is None:
            # Reached the end (or no playable frames remain): pause cleanly.
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
        """Return the first index >= ``start`` (or > ``start`` when
        ``inclusive=False``) whose item is playable. Walks forward only —
        no wrap. Returns ``None`` if no playable item exists in
        [start, len(items)).

        Playable rule (Nelson 2026-06-04 — "Play the photos in the cluster
        in sequence"): skip only items EXPLICITLY marked Discard.
        Untouched items play even when the phase default is Discard
        (the user hasn't dismissed them yet — they need to see them to
        decide)."""
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
        """The exposure-fused preview of the whole bracket, lazily built once
        and cached. The whole bracket feeds the merge (not the filtered subset)
        — "the single photo resulting from combining the photos" is the bracket
        as a whole; cull filters decide which frames survive to Process, not
        which exposures compose the look. RAW → embedded-thumb decode (a
        preview, not a deliverable). Ported verbatim from legacy."""
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
        except Exception:  # noqa: BLE001 — preview must never crash the surface
            log.exception("fuse_exposures failed")
            return None
        return self._combined_cache

    def _toggle_combined(self) -> None:
        """Show / hide the non-destructive Combined preview. ON → the
        viewport shows the fused image as a loose slide (one synthetic
        item — per-frame nav is genuinely locked); OFF → restore the real
        item list at the cursor. Never saves (Phase-0: HDR is external).
        Mutually exclusive with Play — turning Combined on pauses the film."""
        if self._combined_btn.isChecked():
            # Pause Play if it was running — Combined is the focus now.
            if self._film_btn.isChecked():
                self._film_btn.setChecked(False)
                self._toggle_film()
            pm = self._combined_pixmap()
            if pm is None or pm.isNull():     # decode failed — defensive
                self._combined_btn.setChecked(False)
                return
            self._combined_on = True
            # A loose slide: payload None marks it synthetic, so
            # ``_on_current_changed`` leaves the real cursor untouched.
            self.viewport.set_items(
                [ViewportItem(kind="card", pixmap=pm)], 0)
        else:
            self._combined_on = False
            self._sync_viewport_items(self._index)

    # ── EXIF + AF resolution (M2.2) ──────────────────────────────────

    def _exif_for(self, path: Path):
        """read_exif_single once per photo, cached. The exiftool subprocess
        is the cost; AF + the exposure overlay share this one read.

        Nelson 2026-06-09 fast-nav redesign: the bucket's EXIF is
        usually already cached by the time the user navigates here
        (the prefetcher fires at ``load()``); this lazy path is the
        fallback for early navigation that races the prefetch."""
        key = str(path)
        if key in self._exif_cache:
            return self._exif_cache[key]
        try:
            from core.exif_reader import read_exif_single
            exif = read_exif_single(path)
        except Exception as exc:  # noqa: BLE001
            log.debug("EXIF read failed for %s: %s", path.name, exc)
            exif = None
        self._exif_cache[key] = exif
        return exif

    def _spawn_exif_prefetch(self) -> None:
        """Kick off a background ``read_exif_batch`` over every item
        path in the current bucket. One exiftool subprocess instead
        of N — amortises the ~300-500 ms Windows process startup
        cost across the whole bucket.

        Generation-tagged: when the user opens a different bucket the
        old prefetch's result is dropped on arrival."""
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
            except Exception:                                       # noqa: BLE001
                log.exception("EXIF prefetch batch failed")
                return
            by_path: dict = {}
            for photo in results:
                if photo is not None and photo.path is not None:
                    by_path[Path(photo.path)] = photo
            signals.fetched.emit(gen, by_path)

        threading.Thread(target=_run, daemon=True).start()

    def _on_exif_prefetched(self, gen: int, results: dict) -> None:
        """Merge prefetched EXIF into ``_exif_cache``. Stale generation
        (user moved to a different bucket before this batch landed)
        → drop. ``results`` is ``{Path → PhotoExif}`` keyed by
        exiftool's ``SourceFile``; we look up by input path to absorb
        any case / separator normalisation by ``Path`` equality."""
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
            # If the user is currently on a photo whose EXIF JUST
            # landed, re-dress the chrome (exposure overlay + AF feed)
            # without waiting for the next navigation.
            if 0 <= self._index < len(self._items):
                cur_path = getattr(
                    self._items[self._index], "path", None)
                if cur_path is not None and str(cur_path) in self._exif_cache:
                    try:
                        self._refresh_current_chrome()
                    except Exception:                                # noqa: BLE001
                        log.exception("post-prefetch refresh failed")

    def _resolve_af(self, path: Path):
        """EXIF → brand profile → normalized AfPoint, cached per path."""
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
            except Exception as exc:  # noqa: BLE001
                log.debug("AF resolve failed for %s: %s", path.name, exc)
        self._af_cache[key] = af
        return af

    # ── Sharpness scoring (M2.3 → spec/63 §8 HONESTY) ─────────────────

    def _refresh_sharpness(self, item) -> None:
        """Compute-on-miss sharpness + persist via the gateway. The score
        is load-bearing for the cull ranking.

        The spec/62 audit's score-the-thumb bug dies here: the score is
        taken from the viewport's SHARP pixels (the decoded native-tier
        pixmap), never from whatever placeholder happens to be up. While
        only a placeholder is shown this skips; the viewport's
        ``sharp_changed`` re-enters when the real pixels land. Whole-frame
        always (in-view zoom retired — region scrutiny lives in F10)."""
        if item.item_id in self._sharpness_cache:
            return
        info = self.viewport.sharp_pixmap_info()
        if info is None:
            return                       # placeholder up — sharp_changed re-enters
        raw = self._score_pixmap(info[0])
        self._sharpness_cache[item.item_id] = raw
        # Persist to the gateway so re-entry doesn't recompute.
        if self._eg is not None:
            try:
                self._eg.set_sharpness(item.item_id, raw)
            except Exception:  # noqa: BLE001
                log.debug("failed to persist sharpness for %s", item.item_id)

    def _on_sharp_landed(self) -> None:
        """The viewport's sharp pixels arrived for the item on screen —
        score it now if it still needs a score. Keyed off the VIEWPORT's
        current item (this can fire inside ``show_index`` before
        ``current_changed`` updates ``_index``)."""
        vp_item = self.viewport.current_item()
        if vp_item is None or vp_item.payload is None:
            return
        self._refresh_sharpness(vp_item.payload)

    @staticmethod
    def _score_pixmap(pm) -> float:
        """QPixmap → sharpness score (ported from legacy IngestPickerPage)."""
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
        except Exception as exc:  # noqa: BLE001
            log.debug("sharpness scoring failed: %s", exc)
            return 0.0

    # ── lifecycle ─────────────────────────────────────────────────────

    def load(
        self, eg, bucket: CullBucket, phase: str = "pick",
        *, bucket_index: int = 1, bucket_count: int = 1,
        entry_override: Optional[int] = None,
        is_first_in_day: bool = False, is_last_in_day: bool = False,
        default_state: str = STATE_SKIPPED,
        nav_context: str = "bucket",
        nav_label_suffix: str = "",
    ) -> None:
        """Open ``bucket`` for the unified Select pass. Restores the resume
        cursor + marks.

        ``entry_override`` = 0 → first photo; -1 → last photo (cross-bucket photo nav).
        ``None`` → restore the saved cursor as normal.

        ``default_state`` is the configured per-phase default for un-decided items (Settings →
        "Default state for untouched items"); the page passes it from
        :func:`~mira.picked.status.default_state_for`. An item with no explicit mark reads
        as this state on the pill (Nelson 2026-06-03).

        ``nav_context`` (spec/32 §2.7) selects the edge-of-bucket behaviour:

        * ``"bucket"`` (legacy, Select default) — at the photo edge, fire the legacy
          ``prev_bucket_from_first_photo`` / ``next_bucket_from_last_photo`` signals
          (bucket-list mode; PickPage's pre-Day-Grid path + PickPage).
        * ``"day_grid"`` (Cull Day Grid mode for a standalone item cell) — at the photo
          edge, fire :attr:`navigate_at_edge` with delta = ±1 so the host steps the
          day-cell cursor.
        * ``"cluster"`` (Cull Day Grid mode for a cluster sub-grid member) — at the
          photo edge, **do nothing**: cluster edges stop per Nelson 2026-06-04. The
          user presses Back to leave the cluster.

        ``nav_label_suffix`` overrides the position label format for Day Grid /
        Cluster contexts (e.g. ``"· Cell 5/12 · Day 1 — Arrival"`` or ``"· Burst"``).
        Empty → legacy ``N/M`` / ``N/M · P/Q`` format.
        """
        self._eg = eg
        self._phase = phase
        self._bucket = bucket
        self._items = list(bucket.items)
        self._bucket_index = bucket_index
        self._bucket_count = bucket_count
        self._is_first_in_day = is_first_in_day
        self._is_last_in_day = is_last_in_day
        self._nav_context = nav_context
        self._nav_label_suffix = nav_label_suffix
        # Nelson 2026-06-09 fast-nav redesign — teach the shared
        # PhotoCache about this bucket's items so the thumb tier
        # (256 px on-disk JPEG via ``core.photo_thumb_cache``,
        # sha256-keyed) is reachable for the items the surface is
        # about to navigate over. ``set_event_context`` merges into
        # the existing map for the same event root, so cluster drill /
        # back-out preserves the day grid's earlier registrations.
        try:
            from pathlib import Path as _Path
            sha256_by_path: dict = {}
            for ci in self._items:
                if not getattr(ci, "path", None):
                    continue
                it = eg.item(ci.item_id)
                if it is None or not getattr(it, "sha256", None):
                    continue
                sha256_by_path[_Path(ci.path)] = it.sha256
            photo_cache().set_event_context(
                _Path(eg.event_root), sha256_by_path)
        except Exception:                                          # noqa: BLE001
            log.exception("PhotoCache context registration failed")
        # Bulk EXIF prefetch — spawn one subprocess for the whole
        # bucket. Replaces the per-photo cold subprocess that was
        # the actual 1-2 s click-to-photo delay (the JPEG decode is
        # ~100 ms; exiftool spawn was the dominant cost).
        self._spawn_exif_prefetch()
        states = eg.phase_states(phase)
        self._state = {
            ci.item_id: (states[ci.item_id].state if ci.item_id in states else None)
            for ci in self._items
        }
        soft = eg.bucket(bucket.bucket_key, phase)
        # Default for un-decided items = the configured per-phase setting (passed by the page).
        # The bucket soft-state default_state column is dead (always 'skipped', never set
        # from a caller) so it is NOT read here — the setting is the single source.
        self._default_state = default_state
        if entry_override is not None:
            n = len(self._items)
            self._index = (n - 1) if entry_override < 0 else max(0, min(entry_override, n - 1))
        else:
            self._index = max(0, min(soft.current_index if soft else 0, len(self._items) - 1))

        # Clear per-bucket caches.
        self._af_cache.clear()
        self._exif_cache.clear()
        self._sharpness_cache.clear()

        # Pre-load existing sharpness scores from the gateway items (one
        # pass — navigation then never recomputes what's already stored).
        # Classification used to ride this prewarm + a bracket-bucket
        # exiftool batch to feed the now-retired Reclassify dropdown;
        # both are gone with the genre UI (Nelson 2026-06-13).
        for ci in self._items:
            it = eg.item(ci.item_id)
            if it is None:
                continue
            if it.sharpness_score is not None:
                self._sharpness_cache[ci.item_id] = it.sharpness_score

        # Play / Combined reset on bucket change. Peaking belongs to the
        # Sweep (and F10) — the everyday browse always starts clean; the
        # stack-film flag pre-arms for focus brackets (legacy semantics).
        self._film_timer.stop()
        self._film_btn.blockSignals(True)
        self._film_btn.setChecked(False)
        set_transport_playing(self._film_btn, False)
        self._film_btn.blockSignals(False)
        self._combined_on = False
        self._combined_cache = None
        self._combined_btn.blockSignals(True)
        self._combined_btn.setChecked(False)
        self._combined_btn.blockSignals(False)
        self.viewport.set_peaking_enabled(False)
        self.viewport.set_stack_film_peaking(bucket.kind == "focus_bracket")
        self._refresh_clusters()

        # Hand the items to the viewport — it shows the cursor item
        # immediately and ``current_changed`` dresses the chrome.
        self._sync_viewport_items(self._index)
        if not self._items:
            self._expo_overlay.set_html("")
            self._sync_state_pill(self._default_state)
        self.viewport.setFocus()

    def _sync_viewport_items(self, index: int) -> None:
        """Hand the current bucket's items to the viewport (it owns nav +
        pixels; ``payload`` carries the CullItem back to the chrome)."""
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

    def _effective(self, item_id: str) -> str:
        return self._state.get(item_id) or self._default_state

    # ── chrome refresh (the viewport landed on an item) ───────────────

    def _on_current_changed(self, index: int) -> None:
        """The viewport landed on ``index`` — dress the surface chrome.
        No pixels here; the viewport owns those. A synthetic loose slide
        (the Combined preview, payload None) leaves the real cursor and
        chrome untouched."""
        vp_item = self.viewport.current_item()
        if vp_item is None or vp_item.payload is None:
            return
        self._index = index
        item = vp_item.payload

        eff = self._effective(item.item_id)
        self._sync_state_pill(eff)

        # Exposure overlay ON the photo (the live EXIF readout).
        exif = self._exif_for(item.path)
        self._expo_overlay.set_html(
            caption_html(exif) if getattr(item, "kind", "photo") == "photo" else "")

        # AF — resolve and feed; the viewport stores it for F10's
        # inspection overlay (no in-view AF toggle anymore).
        self.viewport.set_af_point(self._resolve_af(item.path))

        # The lens button follows the item: videos have nothing
        # full-res to inspect (mirrors the corner affordance's rule).
        self._fullres_btn.setVisible(
            getattr(item, "kind", "photo") == "photo")

        # Sharpness (spec/63 §8 honesty — skips until sharp pixels land).
        self._refresh_sharpness(item)

        # X/Y position indicator in the compact_row (Nelson 2026-06-06).
        self._refresh_position_label()

    def _refresh_current_chrome(self) -> None:
        """Re-dress the chrome for the item already on screen (EXIF
        prefetch landings)."""
        self._on_current_changed(self.viewport.current_index())

    def _refresh_position_label(self) -> None:
        """Update the compact_row's "X / Y" indicator. ``_bucket_index`` /
        ``_bucket_count`` is the day position the host passes in; intra-
        cluster index is shown when the bucket has more than one item."""
        if not hasattr(self, "_position_label"):
            return
        day_n = self._bucket_index
        day_total = self._bucket_count
        if len(self._items) > 1:
            in_n = self._index + 1
            in_total = len(self._items)
            self._position_label.setText(
                f"{day_n} / {day_total}  ·  {in_n} / {in_total}")
        else:
            self._position_label.setText(f"{day_n} / {day_total}")

    # ── navigation + marking ────────────────────────────────────────────

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
        """The user tried to step past the first or last photo. spec/32 §2.7
        routes by ``nav_context``:

        * ``"bucket"`` → legacy bucket-list crossing (fire
          ``prev_bucket_from_first_photo`` / ``next_bucket_from_last_photo``
          when there's another bucket in the same day; stop at day edges).
        * ``"day_grid"`` → fire :attr:`navigate_at_edge` so PickPage steps the
          day-cell cursor across surface boundaries.
        * ``"cluster"`` → **stop** (cluster edges don't escape; Nelson Q2
          + 2026-06-04: "if inside a cluster we do not want to navigate past
          the last/first photo in the cluster").
        """
        if self._combined_on:
            return                       # one synthetic image — nav is locked
        if self._nav_context == "day_grid":
            self.navigate_at_edge.emit(delta)
            return
        if self._nav_context == "cluster":
            return
        # Legacy bucket-list mode (Select).
        if delta < 0:
            if not self._is_first_in_day:
                self.prev_bucket_from_first_photo.emit()
        else:
            if not self._is_last_in_day:
                self.next_bucket_from_last_photo.emit()

    # ── the decision verbs (spec/63 §4) ────────────────────────────────

    def _persist_state(self, nxt: str) -> None:
        """Write the current item's mark through the gateway and dress the
        chrome. Slice B (Nelson 2026-06-06): individual Pick/Discard inside
        brackets; whole-stack moves go through the grid's Pick all / Skip all."""
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
        """Space — the binary toggle Pick ⇄ Skip (spec/63 §4): the fast
        two-state flip a sweep leans on. From Compare it resolves to Skip
        (same rule as Quick Sweep: anything not Skipped toggles to Skip)."""
        if not self._items or self._eg is None:
            return
        cur = self._effective(self._items[self._index].item_id)
        self._persist_state(
            STATE_PICKED if cur == STATE_SKIPPED else STATE_SKIPPED)

    def _cycle(self) -> None:
        """C / border-click — the full deliberate cycle
        Pick → Skip → Compare → Pick (order anchored by ``_CYCLE``)."""
        if not self._items or self._eg is None:
            return
        cur = self._effective(self._items[self._index].item_id)
        nxt = _CYCLE[(_CYCLE.index(cur) + 1) % len(_CYCLE)]
        self._persist_state(nxt)

    # ── fullscreen ───────────────────────────────────────────────────────

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

    # ── back ─────────────────────────────────────────────────────────────

    def _on_esc(self) -> None:
        """Esc — one level back (spec/63 §4): fullscreen → windowed → out."""
        if not self._exit_fullscreen():
            self._on_back()

    def _on_back(self) -> None:
        self._save_cursor()
        self.back_requested.emit()

    def _save_cursor(self) -> None:
        if self._eg is not None and self._bucket is not None and self._items:
            try:
                self._eg.set_bucket_current_index(self._bucket.bucket_key, self._phase, self._index)
            except Exception:  # noqa: BLE001
                log.exception("failed to save cull resume cursor")

    # ── keyboard. The viewport owns the locked grammar (focus lives
    # there via the proxy); the surface adds R, Home/End and F1 —
    # unhandled viewport keys propagate up here. The nav/Esc/fullscreen
    # branches below are the STRAY-FOCUS fallback: should a key ever
    # land on the surface itself, it routes to the SAME verb handlers
    # the viewport drives — never a dead key on the cull surface. ─────

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_F1:
            self._show_help()
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
        super().keyPressEvent(event)

    def _show_help(self) -> None:
        from mira.ui.base.shortcuts import show_shortcuts
        show_shortcuts(self, tr("Pick — photo"), [
            ("",                    tr("Decide")),
            (tr("P / X"),           tr("Pick / Skip")),
            (tr("Space"),           tr("Toggle Pick ⇄ Skip")),
            (tr("C"),               tr("Cycle Pick → Skip → Compare")),
            (tr("Click the border"), tr("Cycle Pick → Skip → Compare")),
            ("",                    tr("Navigate")),
            (tr("◀ / ▶ · ▲ / ▼"),    tr("Previous / next photo")),
            (tr("Home / End"),      tr("First / last")),
            (tr("Enter"),           tr("Play / pause the cluster sweep")),
            ("",                    tr("View")),
            (tr("F10"),             tr("Inspect at full resolution "
                                       "(F peaking · Z zoom 1:1)")),
            (tr("F / F11"),         tr("Fullscreen")),
            (tr("Esc"),             tr("Back")),
            (tr("F1 · ?"),          tr("This help")),
        ])
