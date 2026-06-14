"""MediaCanvas — shared photo / video viewer for culler + processors.

Replaces ``ui/culler/photo_canvas.py``. Generalises the zoned layout
so the culler, photo processor, video processor, and stack processor
can embed the same viewer and inject their own bottom-row controls.

Layout (per docs/15 §1.6, updated 2026-05-13 with Nelson's design):

  ┌─────── coloured border (alternates blue/orange per bucket) ────┐
  │ TOP-1: EXIF info line (focal · aperture · ISO · shutter · lens) │
  │ TOP-2: [✓ KEPT badge]                      top-2 slot (Type ▾) │
  ├────────────────────────────────────────────────────────────────┤
  │                                                                │
  │                       PHOTO / VIDEO                            │
  │                                                                │
  ├────────────────────────────────────────────────────────────────┤
  │ BOT-1 slot — primary actions (e.g. Keep / Remove / Type)       │
  │ BOT-2 slot — tool-specific (peaking, sliders, etc.)            │
  │ BOT-3:  [⏮] [◀]   "12/47 · 2/3"   [▶] [⏭]                       │
  └────────────────────────────────────────────────────────────────┘

The TOP-2 right side, BOT-1 and BOT-2 are slot layouts the host
fills with widgets. Navigation (BOT-3) is built-in: four buttons emit
signals; the host wires them to its own bucket / photo navigation.

The coloured border flips between two values (``BUCKET_COLOR_PRIMARY``
= Gulf blue, ``BUCKET_COLOR_ACCENT`` = Gulf orange) on every bucket
change — purely a "bucket changed" visual cue, not an identity colour
per bucket.

RAW formats are previewed via rawpy's embedded thumbnail (same path
the old PhotoCanvas used).
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

from PyQt6.QtCore import (
    QBuffer, QByteArray, QEvent, Qt, QPoint, QRect, QSize, QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QImage,
    QImageReader,
    QMouseEvent,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QResizeEvent,
    QTransform,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.brand_profile import AfPoint
from mira.ui.media.photo_cache import photo_cache
from core.cull_state import (
    STATE_CANDIDATE,
    STATE_DISCARDED as STATE_SKIPPED,
    STATE_KEPT as STATE_PICKED,
)
from mira.ui.i18n import tr


log = logging.getLogger(__name__)


# Border colour states — used as dynamic property values that the QSS
# rules in ``assets/themes/*.qss`` match against. Two-value alternating
# scheme; the host flips between them whenever the bucket changes.
BUCKET_COLOR_PRIMARY = "primary"  # Gulf blue
BUCKET_COLOR_ACCENT = "accent"    # Gulf orange


# Extensions that QPixmap can load directly. Anything else needs a
# preview-extraction path (RAW via rawpy, HEIF via pillow-heif).
_QPIXMAP_NATIVE = frozenset({
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp",
})

# RAW extensions where we attempt the rawpy embedded-thumbnail path.
_RAW_EXTENSIONS = frozenset({
    ".rw2", ".arw", ".srf", ".sr2",
    ".cr2", ".cr3", ".crw",
    ".nef", ".nrw",
    ".raf",
    ".pef",
    ".rwl",
    ".ori", ".orf",
    ".dng",
})

# HEIF/HEIC (iPhone). Qt has no built-in codec on Windows; decoded
# via pillow-heif (a declared dep — docs/13 "iPhone HEIC support").
# Already a final image (not a RAW), so one decode serves both the
# preview AND the Phase-B box crop — no separate thumbnail path.
_HEIF_EXTENSIONS = frozenset({".heic", ".heif"})


class MediaCanvas(QFrame):
    """Zoned media viewer with slot-injection for per-tool controls.

    Navigation signals (emitted from the mouse-wheel handler; page-level
    keyboard handlers do the rest):
      * ``prev_bucket_requested``
      * ``prev_photo_requested``
      * ``next_photo_requested``
      * ``next_bucket_requested``

    Public methods:
      * ``set_photo(path)``                           — central area
      * ``set_cull_state(state)``                     — photo-border colour
      * ``set_bucket_color(BUCKET_COLOR_*)``          — outer border
      * ``set_immersive(bool)``                       — F11 fullscreen
    """

    prev_bucket_requested = pyqtSignal()
    prev_photo_requested = pyqtSignal()
    next_photo_requested = pyqtSignal()
    next_bucket_requested = pyqtSignal()
    # Effective magnification of the box-zoom, as a percentage of
    # actual image pixels (100.0 = exactly 1:1). The host wires this
    # to the % readout in the canvas chrome (E8).
    box_zoom_percent_changed = pyqtSignal(float)
    # The state bar IS the cycle control (docs/18 E0). Clicking it
    # asks the host to advance discarded→candidate→kept on the
    # journal (the journal owns state; the canvas is a viewer) and
    # call set_cull_state back. Tab is wired at page level.
    cull_state_cycle_requested = pyqtSignal()
    # Fires when the CURRENT photo's sticky per-photo pan override is
    # created (first Phase-B drag) or cleared (reset). The host uses
    # it to keep "Reset Framing" honest — enabled only when there is
    # actually a framing to reset (Nelson 2026-05-16).
    box_pan_override_changed = pyqtSignal()
    # Fires whenever the displayed pixmap's rect inside the photo
    # area changes — overlay widgets (crop tool, future ROI tools)
    # use this to re-sync their geometry. Emitted after the pixmap
    # is set or the photo label is resized.
    photo_geometry_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MediaCanvas")
        # Default border colour — the host calls set_bucket_color() to
        # flip on bucket changes. The actual border-color value comes
        # from the QSS rule keyed on this dynamic property.
        self.setProperty("bucket_color", BUCKET_COLOR_PRIMARY)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(0)

        # State-bar / top-zone / bottom-zone all retired 2026-06-09 —
        # they had no live content in the rebuild (every text/sharpness/
        # nav slot was either hidden or never populated) and the empty
        # 12 px+ frames painted as ghost "status bars" above and below
        # the photo. The MediaCanvasArea border carries cull state now
        # (driven by ``set_cull_state``); the photo fills the canvas.
        self._build_photo_area(outer)
        self.set_cull_state(STATE_SKIPPED)

        self._source_pixmap: QPixmap | None = None
        self._current_path: Path | None = None
        # Nelson 2026-06-09 fast-nav redesign — session-wide PhotoCache
        # singleton drives ``set_photo`` so a navigation tick paints
        # cache-or-thumb instantly while the full decode runs on the
        # background worker. The handler filters by ``_current_path``
        # so out-of-order decode landings (a predecode for N+2
        # arriving while the user is already at N+3) silently drop.
        self._photo_cache = photo_cache()
        self._photo_cache.pixmap_ready.connect(self._on_cache_pixmap_ready)
        # Mouse-wheel delta accumulator. High-resolution trackpads emit
        # many small deltas per gesture; we only advance one photo per
        # WHEEL_STEP_UNITS so a single gesture doesn't jump multiple
        # photos.
        self._wheel_accum: int = 0

        # Focus-peaking state. The non-zoom path computes the mask
        # lazily and caches it keyed by (size, colour, sensitivity)
        # so toggling on/off is instant but a colour/sensitivity
        # change still recomputes. set_photo clears the cache. Both
        # the non-zoom and the Phase-B box path use the SAME
        # ABSOLUTE algorithm (compute_peaking_absolute — ramped
        # against the derived reference constant, invisible to the
        # user; docs/18 §"Sharp-reference anchor"). The legacy
        # binary compute_peaking_mask is reserved for the future
        # stack-film overlay (E10). See core/focus_peaking.py.
        self._peaking_enabled: bool = False
        self._peaking_color_name: str = "magenta"
        # Stack-film peaking (docs/18 §"Focus peaking" — the second
        # implementation of the deliberate split). When True the
        # overlay uses the legacy binary compute_peaking_mask (thick,
        # full-opacity, preview-based) instead of the thin
        # NMS-ridged compute_peaking_absolute: the focus-bracket
        # surface flips through stills fast and the eye integrates a
        # BOLD moving band, not a 1-px static ridge (Nelson
        # 2026-05-17: "peaking should have a boost, thicker lines, as
        # we had before"). Sensitivity is a single-photo concept and
        # does not apply to the binary path.
        self._stack_film_peaking: bool = False
        # (key, mask) where key =
        # (w, h, colour_name, sensitivity, stack_film).
        self._peaking_mask_cache: (
            tuple[tuple[int, int, str, int, bool], QPixmap] | None
        ) = None
        # Single-photo peaking sensitivity (0-100). None → fall back
        # to the settings.json default at render time. Set live via
        # set_peaking_sensitivity (E8 wires the LabeledSlider to it —
        # a live slider must not round-trip through settings.json).
        self._peaking_sensitivity: int | None = None
        # Peaking is ALWAYS absolute (docs/18 §"Sharp-reference
        # anchor"): compute_peaking_absolute ramps against a derived
        # constant (the canonical sharp target), invisible to the
        # user. No visible band, no toggle, no geometry change —
        # Nelson 2026-05-16: the visible band was a net distraction.

        # ── Box-zoom 1:1 (docs/18 §"Box-zoom 1:1") ─────────────────
        # The box is a FIXED CENTRED viewport. Only two variables —
        # there is no "box position":
        #   * factor — magnification (screen px per image px). 1.0 =
        #     exactly 1:1. Frozen on activation (fair comparison).
        #   * pan — which image region falls under the centred box,
        #     as the box-centre in normalised image coords (0..1).
        #     A global default + a sticky per-photo override.
        # All of this resets on an off→on toggle of the mechanism
        # ("zero everything" — no separate reset-all control).
        # Everything here is inert until set_box_zoom_enabled(True);
        # default-off means MediaCanvas behaves exactly as before
        # (the existing callers + tests are untouched).
        self._boxzoom_enabled: bool = False
        self._boxzoom_active: bool = False          # Phase A=False / B=True
        self._boxzoom_factor: float = 1.0           # 1.0 == 1:1
        self._pan_global: tuple[float, float] = (0.5, 0.5)
        self._pan_overrides: dict[str, tuple[float, float]] = {}
        self._drag_anchor: QPoint | None = None
        self._drag_pan_start: tuple[float, float] | None = None
        # Image-fraction per screen-pixel, refreshed every box render
        # so the drag handler converts mouse delta → pan correctly.
        self._pan_px_scale: tuple[float, float] = (0.0, 0.0)
        # Lazily-decoded full-res RAW for the Phase-B crop, current
        # photo only (path, pixmap). Cleared on set_photo. See
        # _box_crop_pixmap.
        self._fullres_cache: tuple[str, QPixmap] | None = None
        # Lazily-decoded HALF-res RAW for the *non-zoom* peaking read,
        # current photo only (path, pixmap). Cleared on set_photo.
        # The displayed preview stays the cheap embedded thumb; only
        # the peaking *mask* is computed from real (half-res) sensor
        # pixels — an honest whole-frame focus cue for first-pass
        # triage, ~4x cheaper than the full-res Phase-B decode and
        # off the navigation hot path (only decoded when peaking is
        # actually on for a RAW). See _peaking_source_pixmap.
        self._halfres_cache: tuple[str, QPixmap] | None = None
        # The Phase-B crop *before* overlays — the real region
        # pixels (full-res for RAW). The host scores THIS for a
        # region-aware sharpness rating when zoomed (Nelson
        # 2026-05-16). None unless Phase B is active.
        self._region_pixmap: QPixmap | None = None
        # (path, (w, h)) of what Phase B will crop from — full-res
        # for a RAW (read cheaply from the header, NOT decoded),
        # the source pixmap otherwise. Phase A sizes its box against
        # THIS so the framed region == the Phase-B 1:1 crop (Nelson
        # 2026-05-16: RAW zoomed ~3x tighter than the box because
        # setup used the 1920px thumb, Phase B the 5784px full-res).
        self._fullres_dims_cache: tuple[str, tuple[int, int]] | None = None

        # ── AF-point (docs/18 §AF-point, E7) ───────────────────────
        # The canvas is a viewer: the host resolves the brand
        # profile + EXIF and feeds the normalized AfPoint per photo
        # via set_af_point (None when the body/mode wrote no AF data
        # — overlay won't draw, pan-seed tier skipped). Cleared on
        # set_photo. (a) overlay is an independent toggle; (b)
        # pan-seed is automatic precedence (no toggle) — see
        # _effective_pan.
        self._af_point: AfPoint | None = None
        self._af_overlay_enabled: bool = False

    # Box-zoom factor clamp — below 0.25 the box is larger than most
    # photos (nothing to inspect); above 8 the pixels are unusable
    # mush. 1.0 is 1:1.
    _BOXZOOM_MIN_FACTOR = 0.25
    _BOXZOOM_MAX_FACTOR = 8.0

    # ── Construction helpers ────────────────────────────────────────

    def _build_photo_area(self, outer: QVBoxLayout) -> None:
        self._photo_label = QLabel("")
        self._photo_label.setObjectName("MediaCanvasArea")
        # Seed the state property to discarded; the canvas's __init__
        # immediately calls ``set_cull_state(STATE_SKIPPED)`` once
        # this method returns, which is the single funnel for state
        # changes from then on. The border colour reflects this state
        # via QSS (spec/42 Nelson 2026-06-04 — border-as-state).
        self._photo_label.setProperty("state", STATE_SKIPPED)
        self._photo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._photo_label.setMinimumSize(QSize(200, 200))
        self._photo_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding,
        )
        self._photo_label.setText(tr("(no photo loaded)"))
        # The displayed pixmap is scaled to THIS label's size, so it
        # must re-fit whenever the label's geometry actually changes.
        # showEvent / singleShot proved too early: the label sits in
        # a QStackedWidget and its real size is assigned a layout
        # pass *after* the canvas is shown, so a host that calls
        # set_photo before the first show left the first photo
        # under-sized until the user navigated (Nelson 2026-05-17 —
        # "1st photo does not fill; move to the next and it does").
        # An event filter on the label's Resize is timing-independent
        # — the canonical "scaled pixmap tracks its label" pattern.
        self._photo_label.installEventFilter(self)
        self._in_fit = False        # re-entrancy guard for the above
        # Track mouse motion without a button held — needed so the
        # eventFilter can flip the cursor to PointingHand whenever the
        # mouse enters the state-border zone (the 12 px QSS frame outside
        # ``contentsRect``). Nelson 2026-06-04: the border is clickable
        # to cycle state; the cursor must reflect that affordance.
        self._photo_label.setMouseTracking(True)
        # Remembered for the MouseMove handler so it only re-applies the
        # cursor when the zone actually changes (avoids per-pixel churn).
        self._photo_label_in_border_zone: bool = False

        # Immersive (F11) state — drives the wider border in QSS. The
        # legacy ``_fs_badge`` corner overlay was retired 2026-06-09 with
        # the rest of the canvas chrome: the photo area's border carries
        # cull state in BOTH normal and fullscreen mode now, so the
        # badge would have been a duplicate state pill painted inside
        # the photo render area.
        self._immersive = False

        # spec/59 §8 Exported watermark — diagonal translucent text over
        # the displayed image when the host says an exported version of
        # the current photo exists (``set_exported_watermark``). Created
        # FIRST on the label so later overlays (crop tool) stack above
        # it; geometry tracks the letterboxed image rect via the same
        # photo_geometry_changed pulse the crop overlay rides.
        from mira.ui.base.exported_watermark import ExportedWatermark
        self._exported_watermark = ExportedWatermark(self._photo_label)
        self._exported_watermark_on = False
        self.photo_geometry_changed.connect(
            self._sync_exported_watermark_geometry)

        # Central area is a stack: index 0 = the single-photo label,
        # index 1 = an alternate view the host injects (the culler's
        # Grid, E9). The chrome (TOP/BOTTOM incl. the nav bar's
        # Grid⇄Single toggle + filters) stays put around it so grid
        # mode keeps every control usable. Single-photo rendering
        # always targets _photo_label regardless of which page shows.
        self._central_stack = QStackedWidget()
        self._central_stack.addWidget(self._photo_label)        # 0
        self._alt_central_host = QWidget()
        self._alt_central_layout = QVBoxLayout(self._alt_central_host)
        self._alt_central_layout.setContentsMargins(0, 0, 0, 0)
        self._central_stack.addWidget(self._alt_central_host)   # 1
        outer.addWidget(self._central_stack, stretch=1)

    # ── Overlay support (crop tool, future ROI tools) ──────────────

    def photo_area_widget(self) -> QWidget:
        """Return the QLabel that hosts the photo pixmap. Overlay
        widgets (e.g. crop rectangle, region-of-interest editor) parent
        themselves here so they paint over the photo and inherit its
        geometry. The :pyattr:`photo_geometry_changed` signal fires
        whenever the host should refresh the overlay's geometry."""
        return self._photo_label

    def image_rect_in_photo_area(self) -> QRect:
        """Return the rectangle inside :meth:`photo_area_widget` where
        the displayed pixmap actually paints (centered + letterboxed
        to fit). Empty rect when no pixmap is loaded.

        QLabel's ``alignment=AlignCenter`` + ``scaledContents=False``
        means the pixmap is rendered at its current size, centred. So
        the image rect = ``(label_size - pixmap_size) // 2`` offset
        plus the pixmap's actual size."""
        pixmap = self._photo_label.pixmap()
        if pixmap is None or pixmap.isNull():
            return QRect()
        label_size = self._photo_label.size()
        px_size = pixmap.size()
        x = (label_size.width() - px_size.width()) // 2
        y = (label_size.height() - px_size.height()) // 2
        return QRect(x, y, px_size.width(), px_size.height())

    def set_exported_watermark(self, on: bool) -> None:
        """spec/59 §8 — show/hide the diagonal "Exported" watermark for
        the CURRENT image. The host decides per item (edit-phase
        lineage membership × the app-wide ``show_exported_watermark``
        setting); the canvas only displays."""
        self._exported_watermark_on = bool(on)
        self._sync_exported_watermark_geometry()

    def _sync_exported_watermark_geometry(self) -> None:
        if not self._exported_watermark_on:
            self._exported_watermark.setVisible(False)
            return
        rect = self.image_rect_in_photo_area()
        if rect.isEmpty():
            self._exported_watermark.setVisible(False)
            return
        self._exported_watermark.setGeometry(rect)
        self._exported_watermark.setVisible(True)

    def set_alt_central_widget(self, widget: QWidget) -> None:
        """Inject the alternate central view (the culler Grid, E9).
        Replaces any previous one. Shown via :meth:`show_alt_central`."""
        while self._alt_central_layout.count():
            item = self._alt_central_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self._alt_central_layout.addWidget(widget)

    def show_alt_central(self, on: bool) -> None:
        """Swap the central area between the single-photo view (off)
        and the injected alternate view (on). Chrome is untouched."""
        self._central_stack.setCurrentIndex(1 if on else 0)

    def is_alt_central_shown(self) -> bool:
        return self._central_stack.currentIndex() == 1

    # ── Public API ──────────────────────────────────────────────────

    # Display label per state — uppercase, no glyph (the ``○``/``●``
    # prefix of the prototype read as a radio button). The bar's
    # colour comes from QSS keyed on the ``state`` property.
    # Present-tense ACTION words (Nelson 2026-05-16): the photo
    # hasn't been "skipped"/"picked" — those are *intended
    # dispositions*, decided at commit, not facts yet. The
    # serialized values (``core.cull_state``) stay legacy strings;
    # only the words the user reads change. KEEP / COMPARE / DISCARD
    # also preserves the K/C/D filter mnemonic.
    _STATE_LABELS = {
        STATE_SKIPPED: "DISCARD",
        STATE_CANDIDATE: "COMPARE",
        STATE_PICKED: "KEEP",
    }

    def set_cull_state(self, state: str) -> None:
        """Set the cull state — drives the photo area's coloured border
        via the ``MediaCanvasArea`` QSS rule (green=Keep, red=Discard,
        orange=Compare). Unknown values fall back to ``discarded`` rather
        than raising — a display widget must never crash the cull on a
        surprising value. The state cycle is triggered by Space (keyboard)
        or by clicking the photo area's border (mouse)."""
        if state not in self._STATE_LABELS:
            state = STATE_SKIPPED
        # Guarded with ``getattr`` — the initial ``set_cull_state`` call
        # from ``__init__`` runs immediately after ``_build_photo_area``,
        # but the rest of the canvas's init may still be running.
        photo_label = getattr(self, "_photo_label", None)
        if photo_label is not None:
            photo_label.setProperty("state", state)
            photo_label.style().unpolish(photo_label)
            photo_label.style().polish(photo_label)

    def set_photo(self, path: Path | None) -> None:
        """Load + display an image, or clear the canvas when ``path``
        is ``None`` / missing on disk.

        Nelson 2026-06-09 fast-nav redesign — paint priority:

        1. **LRU hit** in :class:`PhotoCache` → sync paint, return.
        2. **Thumb hit** (on-disk 256-px JPEG via
           :mod:`core.photo_thumb_cache`) → paint as placeholder.
        3. Otherwise → leave the previous photo painted (closer to
           "instant" than blanking for a mid-scroll user).

        Then queue the full decode at priority 0; when the worker
        lands it on the UI thread via :meth:`_on_cache_pixmap_ready`,
        the canvas swaps the placeholder for the full-resolution
        pixmap. Cache pixmaps are sized to the photo label, so
        repeated navigation never re-decodes."""
        self._current_path = path
        # New photo → invalidate cached peaking mask + the full-res
        # RAW crop (only ever one photo's full-res in memory) + the
        # AF point (host re-feeds it for the new photo).
        self._peaking_mask_cache = None
        self._fullres_cache = None
        self._halfres_cache = None
        self._fullres_dims_cache = None
        self._region_pixmap = None
        self._af_point = None
        if path is None or not path.exists():
            self._source_pixmap = None
            self._photo_label.setPixmap(QPixmap())
            self._photo_label.setText(tr("(no photo loaded)"))
            return

        target_size = self._photo_label.size()
        # 1. Display-pixmap LRU.
        cached = self._photo_cache.get_pixmap_if_cached(path)
        if cached is not None and not cached.isNull():
            self._source_pixmap = cached
            self._photo_label.setText("")
            self._update_displayed_pixmap()
            return

        # 2. On-disk thumb as a placeholder while the worker decodes.
        thumb = self._photo_cache.get_thumb_pixmap_sync(path)
        if thumb is not None and not thumb.isNull():
            self._source_pixmap = thumb
            self._photo_label.setText("")
            self._update_displayed_pixmap()
        # 3. No-op fall-through: the previous _source_pixmap stays
        #    painted until the worker lands the full decode. Blanking
        #    on every miss makes navigation feel flickery — the
        #    previous photo is closer to "instant" feedback for a
        #    user mid-scroll.

        # Queue the full decode. Priority 0 = current target — the
        # worker never drops priority-0 jobs even when newer ticks
        # supersede them (the user is waiting on this photo).
        self._photo_cache.request_pixmap(
            path, target_size, priority=0)

    def target_render_size(self) -> QSize:
        """The current photo-label rectangle. Hosts pass this to
        :meth:`PhotoCache.request_pixmap` so predecodes are sized to
        match what :meth:`set_photo` would request itself — cache hits
        on neighbour photos paint at full quality without a second
        decode."""
        return self._photo_label.size()

    def _on_cache_pixmap_ready(
        self, path: Path, pixmap: QPixmap,
    ) -> None:
        """Worker delivered a full decode. Paint only when we're still
        on this photo — out-of-order landings (a predecode for N+2
        arriving after the user is already at N+3) silently drop."""
        if self._current_path != path:
            return
        if self._source_pixmap is pixmap:
            # Sync cache hit re-emitted from request_pixmap — already
            # painted by set_photo, nothing to do.
            return
        # New full-res pixmap → the peaking mask cached against the
        # placeholder (or the previous photo) is stale.
        self._peaking_mask_cache = None
        self._source_pixmap = pixmap
        self._photo_label.setText("")
        self._update_displayed_pixmap()

    def set_preview_pixmap(self, pixmap: QPixmap | None) -> None:
        """Display a **computed** image instead of a file-backed photo
        (docs/18 §"Bracket surfaces" — the exposure-bracket Combined
        preview). Path-less: per-photo caches are cleared and
        ``_current_path`` is ``None`` so the RAW-decode / box-zoom
        paths fall back gracefully (a synthetic preview has no file).
        The caller restores the real frame via its normal show path
        (``set_photo``). ``None`` clears the canvas."""
        self._current_path = None
        self._peaking_mask_cache = None
        self._fullres_cache = None
        self._halfres_cache = None
        self._fullres_dims_cache = None
        self._region_pixmap = None
        self._af_point = None
        if pixmap is None or pixmap.isNull():
            self._source_pixmap = None
            self._photo_label.setPixmap(QPixmap())
            self._photo_label.setText(tr("(no preview)"))
            return
        self._source_pixmap = pixmap
        self._photo_label.setText("")
        self._update_displayed_pixmap()

    # ── Focus peaking ──────────────────────────────────────────────

    def set_peaking_enabled(self, enabled: bool) -> None:
        """Toggle the focus-peaking overlay on the photo canvas.

        Off (default) → photo renders as-is. On → after the next
        :meth:`set_photo` / resize / explicit refresh, edges are
        highlighted in the configured peaking colour. Toggling is
        instant: the mask is computed once per photo and cached.
        """
        if self._peaking_enabled == bool(enabled):
            return
        self._peaking_enabled = bool(enabled)
        self._update_displayed_pixmap()

    def set_peaking_color(self, color_name: str) -> None:
        """Pick the focus-peaking highlight colour by name.

        Valid names live in ``core.focus_peaking.PEAKING_COLORS``
        (magenta default, yellow, red, cyan). Unknown values fall
        back to magenta per ``color_tuple_for_name``. Calling this
        invalidates the mask cache so the next render picks up the
        new colour.
        """
        new_name = (color_name or "").strip().lower() or "magenta"
        if new_name == self._peaking_color_name:
            return
        self._peaking_color_name = new_name
        self._peaking_mask_cache = None
        if self._peaking_enabled:
            self._update_displayed_pixmap()

    def set_peaking_sensitivity(self, value: int | None) -> None:
        """Set the single-photo peaking sensitivity (0-100) live.

        ``None`` restores the settings.json default. The E8 chrome
        wires the LabeledSlider straight to this — a live slider must
        not round-trip through settings.json on every drag tick.
        Affects every single-photo peaking render (non-zoom view and
        Phase-B box alike): the cache is invalidated so the next
        paint picks up the new cut.
        """
        if value is not None:
            value = max(0, min(100, int(value)))
        if value == self._peaking_sensitivity:
            return
        self._peaking_sensitivity = value
        self._peaking_mask_cache = None
        if self._peaking_enabled:
            self._update_displayed_pixmap()

    def set_stack_film_peaking(self, enabled: bool) -> None:
        """Switch the peaking overlay to the **stack-film** variant
        (docs/18 §"Focus peaking" — the second implementation of the
        deliberate split, reserved for the focus-bracket / E10 stack
        overlay).

        On → the legacy binary ``compute_peaking_mask`` (thick,
        full-opacity, preview-based) so the in-focus band reads BOLD
        while the bracket stills flip past and the eye integrates the
        motion. Off (default) → the thin NMS-ridged
        ``compute_peaking_absolute`` single-photo verdict. Invalidates
        the mask cache; repaints iff peaking is currently on.
        """
        if self._stack_film_peaking == bool(enabled):
            return
        self._stack_film_peaking = bool(enabled)
        self._peaking_mask_cache = None
        if self._peaking_enabled:
            self._update_displayed_pixmap()

    def is_stack_film_peaking(self) -> bool:
        return self._stack_film_peaking

    def _effective_peaking_sensitivity(self) -> int:
        if self._peaking_sensitivity is not None:
            return self._peaking_sensitivity
        from core.settings import load_settings
        return int(load_settings().get("peaking_sensitivity", 50))

    def is_peaking_enabled(self) -> bool:
        return self._peaking_enabled

    def peaking_color_name(self) -> str:
        return self._peaking_color_name

    def peaking_sensitivity(self) -> int:
        """The effective sensitivity (instance override, else the
        settings.json default)."""
        return self._effective_peaking_sensitivity()

    # ── AF-point (docs/18 §AF-point, E7) ───────────────────────────

    def set_af_point(self, af: AfPoint | None) -> None:
        """Feed the normalized AF point for the current photo (host
        resolves brand profile + EXIF; ``None`` = no AF data). Drives
        both the (a) overlay and the (b) pan-seed precedence tier."""
        self._af_point = af
        self._update_displayed_pixmap()

    def set_af_overlay_enabled(self, enabled: bool) -> None:
        """Toggle the (a) AF-rectangle overlay. Drawn only in the
        plain (non-zoom) view: when zoom is on the AF rectangle is
        suppressed and the AF point instead seeds the single zoom
        box (Nelson 2026-05-16 — two boxes of different aspect
        ratios looked like a bug). The feature/toggle is unaffected;
        no-op visual when the photo has no AF data."""
        enabled = bool(enabled)
        if enabled == self._af_overlay_enabled:
            return
        self._af_overlay_enabled = enabled
        self._update_displayed_pixmap()

    def is_af_overlay_enabled(self) -> bool:
        return self._af_overlay_enabled

    def af_point(self) -> AfPoint | None:
        return self._af_point

    def _draw_af_overlay(
        self,
        target: QPixmap,
        ox: float,
        oy: float,
        draw_w: float,
        draw_h: float,
    ) -> None:
        """Paint the AF rectangle onto ``target``. The image occupies
        ``(ox, oy, draw_w, draw_h)`` in ``target``'s pixel space
        (Phase B passes the crop-relative mapping so an AF box
        outside the crop is clipped out naturally). No-op when the
        overlay is off or there's no AF data.

        **Mutually exclusive with the zoom box (Nelson 2026-05-16):**
        when box-zoom is enabled the AF rectangle is *not* drawn —
        the AF point is consumed purely as the zoom pan-seed and the
        single zoom box (centred on it) is the only indicator. Two
        boxes of different aspect ratios looked like a bug. The AF
        *feature* stays ON; only its rectangle is suppressed while
        zooming. Zoom off → the AF box returns."""
        if (
            not self._af_overlay_enabled
            or self._af_point is None
            or self._boxzoom_enabled
        ):
            return
        af = self._af_point
        rw = max(2.0, af.w * draw_w)
        rh = max(2.0, af.h * draw_h)
        rx = ox + af.cx * draw_w - rw / 2.0
        ry = oy + af.cy * draw_h - rh / 2.0
        painter = QPainter(target)
        try:
            pen = QPen(self.palette().color(QPalette.ColorRole.Link))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawRect(
                int(round(rx)), int(round(ry)),
                int(round(rw)), int(round(rh)),
            )
        finally:
            painter.end()

    # ── Box-zoom 1:1 (docs/18) ─────────────────────────────────────

    def set_box_zoom_enabled(self, enabled: bool) -> None:
        """Arm / disarm the box-zoom mechanism.

        Enabling is the **full reset** ("zero everything" per
        docs/18): box re-centred, factor back to 1:1, global default
        cleared, every per-photo override discarded, back to Phase A.
        Disabling returns to the plain fit-to-window view. There is
        deliberately no separate reset-all control — off→on is it.
        """
        enabled = bool(enabled)
        if enabled:
            # Full reset on (re-)arm.
            self._boxzoom_active = False
            self._boxzoom_factor = 1.0
            self._pan_global = (0.5, 0.5)
            self._pan_overrides.clear()
            self._drag_anchor = None
        self._boxzoom_enabled = enabled
        if not enabled:
            self._boxzoom_active = False
        self._update_displayed_pixmap()
        self._emit_zoom_percent()

    def activate_box_zoom(self) -> None:
        """Phase A → Phase B. Freezes the box size (factor) for the
        comparison session and switches the canvas to render the box
        region full-screen. No-op unless armed."""
        if not self._boxzoom_enabled or self._boxzoom_active:
            return
        self._boxzoom_active = True
        self._update_displayed_pixmap()
        self._emit_zoom_percent()

    def deactivate_box_zoom(self) -> None:
        """Phase B → Phase A (back to fit-to-window + box overlay so
        the user can re-frame). Size is *not* reset — only off→on
        resets. No-op unless currently zoomed."""
        if not self._boxzoom_active:
            return
        self._boxzoom_active = False
        self._update_displayed_pixmap()
        self._emit_zoom_percent()

    def set_box_factor(self, factor: float) -> None:
        """Set the magnification (screen px per image px; 1.0 = 1:1).

        Only honoured in Phase A — once zoom is activated the size is
        frozen (fair comparison across the burst). Clamped to a sane
        range.
        """
        if not self._boxzoom_enabled or self._boxzoom_active:
            return
        f = max(
            self._BOXZOOM_MIN_FACTOR,
            min(self._BOXZOOM_MAX_FACTOR, float(factor)),
        )
        if f == self._boxzoom_factor:
            return
        self._boxzoom_factor = f
        self._update_displayed_pixmap()
        self._emit_zoom_percent()

    def reset_current_photo_box(self) -> None:
        """Dedicated per-photo reset: drop *this* photo's manual pan
        override so it falls back to the AF-derived position (Phase
        B, if any) or the global default. No-op (and no signal) when
        the photo has no override — there is nothing to reset."""
        key = self._pan_key()
        if key is not None and key in self._pan_overrides:
            del self._pan_overrides[key]
            self._update_displayed_pixmap()
            self.box_pan_override_changed.emit()

    def has_current_photo_box_override(self) -> bool:
        """True iff the current photo has a sticky per-photo pan
        override (set by a Phase-B drag). Drives whether
        "Reset Framing" is enabled — it must not look clickable
        when there is nothing to reset (Nelson 2026-05-16)."""
        key = self._pan_key()
        return key is not None and key in self._pan_overrides

    def is_box_zoom_enabled(self) -> bool:
        return self._boxzoom_enabled

    def is_box_zoom_active(self) -> bool:
        return self._boxzoom_active

    def current_region_pixmap(self) -> QPixmap | None:
        """The Phase-B crop *before* overlays — real region pixels
        (full-res for RAW). For a region-aware sharpness rating
        when zoomed (docs/18 §Ranking). ``None`` unless Phase B is
        active (whole-frame score applies otherwise)."""
        return self._region_pixmap if self._boxzoom_active else None

    def box_zoom_percent(self) -> float:
        """Effective magnification vs actual image pixels, as a
        percentage (100.0 == exactly 1:1). 0.0 when not armed."""
        if not self._boxzoom_enabled:
            return 0.0
        return self._boxzoom_factor * 100.0

    # Internal pan helpers ------------------------------------------

    def _pan_key(self) -> str | None:
        return str(self._current_path) if self._current_path else None

    def _effective_pan(self) -> tuple[float, float]:
        """Precedence (docs/18 §AF-point, Nelson 2026-05-16):
          1. per-photo manual override (sticky) — always wins
          2. AF-derived (computed, non-sticky) — in **both** phases
             now: in setup it anchors the box on the AF area so what
             you frame/resize is what Phase B will use (a manual
             Phase-B nudge promotes to a sticky override that wins)
          3. global default (Phase A framing, only when NO AF point)

        Extending AF to setup is the fix for "the centred box is
        never used when there's an AF area" — the box now sits on
        the AF area from the start; (−)/(+) just resize it.
        """
        key = self._pan_key()
        if key is not None and key in self._pan_overrides:
            return self._pan_overrides[key]
        if self._af_point is not None:
            return (self._af_point.cx, self._af_point.cy)
        return self._pan_global

    def _set_pan(self, fx: float, fy: float) -> None:
        """Write the pan. Both phases → per-photo override (highest
        precedence in _effective_pan), falling back to _pan_global only
        when there is no current photo key.

        Phase A used to write to _pan_global, but that is invisible when
        an AF point exists — _effective_pan returns the AF point instead
        of _pan_global. Promoting Phase A drag to a per-photo override
        makes it visible and consistent with Phase B behaviour.
        'Reset Framing' (which clears the override) then works in both
        phases; re-arming zoom (Z) resets all overrides as before."""
        fx = 0.0 if fx < 0.0 else 1.0 if fx > 1.0 else fx
        fy = 0.0 if fy < 0.0 else 1.0 if fy > 1.0 else fy
        key = self._pan_key()
        if key is not None:
            was_present = key in self._pan_overrides
            self._pan_overrides[key] = (fx, fy)
            if not was_present:
                # absent → present: "Reset Framing" becomes live.
                self.box_pan_override_changed.emit()
        else:
            self._pan_global = (fx, fy)
        self._update_displayed_pixmap()

    def _emit_zoom_percent(self) -> None:
        self.box_zoom_percent_changed.emit(self.box_zoom_percent())

    def set_bucket_color(self, color: str) -> None:
        """Flip the outer border colour between ``BUCKET_COLOR_PRIMARY``
        (Gulf blue) and ``BUCKET_COLOR_ACCENT`` (Gulf orange). Called
        by the host on every bucket change — pure visual cue, no
        identity meaning per bucket."""
        if color not in (BUCKET_COLOR_PRIMARY, BUCKET_COLOR_ACCENT):
            log.warning("Unknown bucket color: %r — ignoring", color)
            return
        self.setProperty("bucket_color", color)
        self.style().unpolish(self)
        self.style().polish(self)

    # ── Immersive (full-screen) chrome ─────────────────────────────

    def set_immersive(self, on: bool) -> None:
        """Full-screen photo mode — widens the outer bucket border via
        the ``[immersive="true"]`` QSS rule. With the canvas chrome
        (state strip, top zone, bottom zone) retired, the photo already
        fills the canvas in both modes; this method now only widens the
        outer border + content margins. Idempotent."""
        self._immersive = on
        self.setProperty("immersive", "true" if on else "false")
        lay = self.layout()
        if lay is not None:
            m = 8 if on else 4
            lay.setContentsMargins(m, m, m, m)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    @property
    def current_path(self) -> Path | None:
        return self._current_path

    # ── Qt overrides ────────────────────────────────────────────────

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_displayed_pixmap()

    def showEvent(self, event) -> None:  # noqa: N802
        """Re-fit the displayed pixmap once the canvas is actually
        shown. A host that calls ``set_photo`` *before* the first
        show (the standard load → show order) sized the pixmap
        against an un-laid-out label; the photo then looked too
        small until the user navigated and triggered a fresh
        ``_update_displayed_pixmap`` (Nelson 2026-05-17: "the 1st
        photo does not fill the whole area; move to the next and it
        does"). The deferred call runs after this show's layout pass
        so the label has its real size."""
        super().showEvent(event)
        self._update_displayed_pixmap()
        QTimer.singleShot(0, self._update_displayed_pixmap)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        """Re-fit the pixmap whenever the photo label is actually
        resized — the layout assigns the label its real size a pass
        *after* show, which showEvent/singleShot miss (see
        :meth:`_build_photo_area`). ``_in_fit`` guards the (benign,
        but cheap to avoid) case of a re-entrant Resize.

        Also handles left-mouse clicks on the photo label's BORDER
        area (the 12 px-wide frame outside ``contentsRect``) — those
        emit :attr:`cull_state_cycle_requested`, matching the legacy
        click-the-state-bar-to-cycle interaction (spec/42 Nelson
        2026-06-04: border-as-state with Space-or-border-click as the
        cycle gestures).

        And tracks MouseMove + Leave so the cursor flips to PointingHand
        while inside the border zone (Nelson 2026-06-04: the border
        IS a clickable affordance, so its cursor must read as such)."""
        if (
            obj is self._photo_label
            and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            # Border click = anywhere on the photo label OUTSIDE its
            # contentsRect (the 12 px QSS border zone). Clicks INSIDE
            # the contents area are left for box-zoom / AF / future
            # tools to handle.
            pos = event.position().toPoint()
            content = self._photo_label.contentsRect()
            if not content.contains(pos):
                self.cull_state_cycle_requested.emit()
                event.accept()
                return True
        if (
            obj is self._photo_label
            and event.type() == QEvent.Type.MouseMove
        ):
            pos = event.position().toPoint()
            in_border = not self._photo_label.contentsRect().contains(pos)
            if in_border != self._photo_label_in_border_zone:
                self._photo_label_in_border_zone = in_border
                if in_border:
                    self._photo_label.setCursor(
                        QCursor(Qt.CursorShape.PointingHandCursor))
                else:
                    self._photo_label.unsetCursor()
        if (
            obj is self._photo_label
            and event.type() == QEvent.Type.Leave
        ):
            # Reset on leave so the cursor doesn't stick if the mouse
            # exits the label while still in the border zone.
            if self._photo_label_in_border_zone:
                self._photo_label_in_border_zone = False
                self._photo_label.unsetCursor()
        if (
            obj is self._photo_label
            and event.type() == QEvent.Type.Resize
            and not self._in_fit
        ):
            self._in_fit = True
            try:
                self._update_displayed_pixmap()
            finally:
                self._in_fit = False
            # Tell overlay widgets (crop tool, future ROI tools) the
            # photo's painted rect just moved. They re-anchor to the
            # new image_rect_in_photo_area() value.
            self.photo_geometry_changed.emit()
        return super().eventFilter(obj, event)

    # Mouse wheel emits standard photo-nav signals so any host page
    # (PickerPage, ClassificationPreviewPage, future processors) gets
    # film-strip wheel navigation for free. Convention: wheel UP =
    # previous, wheel DOWN = next (matches Windows Photos / file
    # thumbnails). One photo per WHEEL_STEP_UNITS of accumulated delta,
    # so high-resolution trackpads don't fire multiple photo jumps per
    # gesture.
    _WHEEL_STEP_UNITS = 120

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        delta_y = event.angleDelta().y()
        if delta_y == 0:
            super().wheelEvent(event)
            return
        # Direction change → drop the opposite-direction carry
        # (Nelson 2026-05-16: scrolling back felt dead for a click
        # or two, then jumped — that was the old travel being
        # "repaid" before the new direction registered, and the
        # leftover then discharging 2 photos at once). A reversal
        # now responds on the very next notch.
        if self._wheel_accum and (delta_y > 0) != (self._wheel_accum > 0):
            self._wheel_accum = 0
        self._wheel_accum += delta_y
        # Discharge whole notches, in either direction.
        while self._wheel_accum >= self._WHEEL_STEP_UNITS:
            self._wheel_accum -= self._WHEEL_STEP_UNITS
            self.prev_photo_requested.emit()
        while self._wheel_accum <= -self._WHEEL_STEP_UNITS:
            self._wheel_accum += self._WHEEL_STEP_UNITS
            self.next_photo_requested.emit()
        event.accept()

    # ── Internals ──────────────────────────────────────────────────

    def _update_displayed_pixmap(self) -> None:
        if self._source_pixmap is None or self._source_pixmap.isNull():
            return
        area = self._photo_label.size()
        if area.width() <= 0 or area.height() <= 0:
            return

        if not self._boxzoom_enabled:
            # Non-zoom view. Default base = the cheap embedded thumb
            # fitted to the view (instant — paging stays fast). When
            # peaking is on for a RAW the base IS the half-res sensor
            # decode instead: the camera's embedded JPEG is lens-
            # distortion-corrected while rawpy.postprocess is the
            # uncorrected sensor projection (centre matches but
            # corners diverge — Nelson 2026-05-16 "peaking not on the
            # right edges, gaps"). Displaying the SAME pixels we peak
            # makes the mask register by construction. Peaking off →
            # thumb (unchanged, fast).
            base = self._non_zoom_base(area)
            if self._peaking_enabled:
                base = self._composite_peaking(base)
            # AF overlay (a) — the fitted image fills `base`.
            self._draw_af_overlay(
                base, 0.0, 0.0, base.width(), base.height(),
            )
            self._photo_label.setPixmap(base)
            self.photo_geometry_changed.emit()
            return

        if self._boxzoom_active:
            self._photo_label.setPixmap(self._render_box_zoomed(area))
        else:
            self._photo_label.setPixmap(self._render_box_setup(area))
        self.photo_geometry_changed.emit()

    # ── Box-zoom rendering ─────────────────────────────────────────

    def _full_res_dims(self) -> tuple[int, int]:
        """Dimensions of what Phase B will actually crop from — the
        **full-res** image. For a RAW that is *not* the loaded
        ``_source_pixmap`` (a ~1920 px embedded thumb) but the
        decoded sensor frame (~5784 px). Read cheaply from the RAW
        header (``rawpy.sizes`` — no demosaic) and cached per photo;
        if the full-res decode already happened (Phase B) its real
        size wins. Non-RAW sources are already full-res → the
        pixmap size. Falls back to the source size on any failure
        (graceful — worst case reverts to the old behaviour).
        """
        sp = self._source_pixmap
        fallback = (sp.width(), sp.height()) if sp is not None else (1, 1)
        path = self._current_path
        if path is None:
            return fallback
        if path.suffix.lower() not in _RAW_EXTENSIONS:
            return fallback                       # JPG/HEIC = full-res
        cached = self._fullres_cache              # decoded already?
        if cached is not None and cached[0] == str(path):
            return (cached[1].width(), cached[1].height())
        dc = self._fullres_dims_cache
        if dc is not None and dc[0] == str(path):
            return dc[1]
        try:
            import rawpy
            with rawpy.imread(str(path)) as raw:
                s = raw.sizes
                dims = (int(s.width), int(s.height))
            if dims[0] > 0 and dims[1] > 0:
                self._fullres_dims_cache = (str(path), dims)
                return dims
        except Exception as exc:  # noqa: BLE001 — graceful fallback
            log.debug("rawpy.sizes failed for %s: %s", path, exc)
        return fallback

    def _box_image_dims(
        self, area: QSize, src: QPixmap | None = None,
    ) -> tuple[int, int]:
        """Box size in *image* pixels for the current factor.

        1:1 (factor 1.0) = exactly the view's pixel dimensions in
        image px. Higher factor → fewer image px under the box (more
        magnification). Clamped to the **full-res** dims so Phase A
        (no ``src``) and Phase B (``src`` = the full-res crop
        source) compute the *same* region — previously Phase A
        clamped to the embedded thumb, making a RAW zoom ~3× tighter
        than the framed box (Nelson 2026-05-16).
        """
        if src is not None:
            sw, sh = src.width(), src.height()
        else:
            sw, sh = self._full_res_dims()
        f = self._boxzoom_factor or 1.0
        bw = max(1, min(sw, int(round(area.width() / f))))
        bh = max(1, min(sh, int(round(area.height() / f))))
        return bw, bh

    def _box_crop_pixmap(self) -> QPixmap:
        """The pixmap the Phase-B box crops from.

        For a RAW the loaded ``_source_pixmap`` is only the camera's
        embedded thumbnail — cropping it gives a *lie* about
        sharpness (docs/18 RAW-fidelity requirement). When the box
        is active on a RAW we decode the **full-res** RAW once,
        lazily, and cache it for the current photo only (a 24 MP
        decode is ~70 MB — never accumulate; ``set_photo`` clears
        it). Phase A never triggers the decode (it only frames a
        guide rectangle on the cheap thumb). Decode failure (rawpy
        quirk on some bodies) silently falls back to the thumb —
        a degraded preview beats a blank canvas.
        """
        path = self._current_path
        if path is None:
            return self._source_pixmap
        if path.suffix.lower() not in _RAW_EXTENSIONS:
            return self._source_pixmap          # JPG/PNG already full-res
        key = str(path)
        cached = self._fullres_cache
        if cached is not None and cached[0] == key:
            return cached[1]
        image = _load_raw_full_res(path)
        if image is None or image.isNull():
            return self._source_pixmap          # graceful fallback
        pix = QPixmap.fromImage(image)
        self._fullres_cache = (key, pix)
        return pix

    def _render_box_zoomed(self, area: QSize) -> QPixmap:
        """Phase B: crop the box region (per factor + effective pan)
        and scale it to fill the view. For RAW the crop comes from
        the full-res decode (real sensor pixels — 1:1 truth), not
        the embedded thumb. Single-photo peaking (E5) composites on
        those real pixels."""
        src = self._box_crop_pixmap()
        sw, sh = src.width(), src.height()
        bw, bh = self._box_image_dims(area, src)
        fx, fy = self._effective_pan()
        cx, cy = fx * sw, fy * sh
        x = int(round(_clamp(cx - bw / 2.0, 0.0, max(0, sw - bw))))
        y = int(round(_clamp(cy - bh / 2.0, 0.0, max(0, sh - bh))))
        crop = src.copy(QRect(x, y, bw, bh))
        self._region_pixmap = crop          # pre-overlay, for scoring
        scaled = crop.scaled(
            area,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        # Screen-px ↔ image-fraction for the drag handler.
        self._pan_px_scale = (
            (bw / sw) / max(1, scaled.width()),
            (bh / sh) / max(1, scaled.height()),
        )
        if self._peaking_enabled:
            scaled = self._composite_box_peaking(scaled)
        # AF overlay (a), crop-relative: map the FULL image onto the
        # scaled view as if the whole frame were drawn at the crop's
        # scale, origin shifted by the crop offset. An AF box outside
        # the visible crop falls off-pixmap and QPainter clips it.
        if scaled.width() and scaled.height():
            sx = scaled.width() / bw
            sy = scaled.height() / bh
            self._draw_af_overlay(
                scaled, -x * sx, -y * sy, sw * sx, sh * sy,
            )
        return scaled

    def _render_box_setup(self, area: QSize) -> QPixmap:
        """Phase A: whole photo fit-to-window, panned so the chosen
        region sits under a fixed centred box rectangle the user can
        resize. Dragging the photo here sets the *global default*."""
        src = self._source_pixmap
        fitted = src.scaled(
            area,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        fw, fh = fitted.width(), fitted.height()
        fx, fy = self._effective_pan()
        # Offset so the panned image point lands at the view centre.
        ox = area.width() / 2.0 - fx * fw
        oy = area.height() / 2.0 - fy * fh

        canvas = QPixmap(area)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        try:
            painter.drawPixmap(int(round(ox)), int(round(oy)), fitted)
            # Centred box rectangle = the 1:1 region on the fitted
            # image. The fitted thumb shows the WHOLE image in fw×fh,
            # so a region of `bw` FULL-RES px occupies bw/full_w of
            # it. Scale by full-res dims (NOT src/thumb) — that's the
            # fix for "RAW zooms ~3× tighter than the framed box".
            full_w, full_h = self._full_res_dims()
            bw, bh = self._box_image_dims(area)
            rw = max(4, int(round(bw * fw / max(1, full_w))))
            rh = max(4, int(round(bh * fh / max(1, full_h))))
            aw, ah = area.width(), area.height()
            rx = (aw - rw) // 2
            ry = (ah - rh) // 2
            # Dim everything OUTSIDE the box (classic crop affordance)
            # so "what will be zoomed" is unmistakable and resizing
            # the box is visually obvious — four translucent panels
            # around the bright box region.
            dim = QColor(0, 0, 0, 130)
            painter.fillRect(QRect(0, 0, aw, ry), dim)             # top
            painter.fillRect(QRect(0, ry + rh, aw, ah - ry - rh), dim)  # bottom
            painter.fillRect(QRect(0, ry, rx, rh), dim)            # left
            painter.fillRect(
                QRect(rx + rw, ry, aw - rx - rw, rh), dim,
            )                                                       # right
            pen = QPen(self.palette().color(QPalette.ColorRole.Highlight))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawRect(QRect(rx, ry, rw, rh))
        finally:
            painter.end()
        # Drag in Phase A moves the photo under the fixed box: screen
        # px → image fraction via the fitted size.
        self._pan_px_scale = (1.0 / max(1, fw), 1.0 / max(1, fh))
        # AF overlay (a): the fitted image sits at (ox, oy) sized
        # (fw, fh) on the area-sized canvas.
        self._draw_af_overlay(canvas, ox, oy, fw, fh)
        return canvas

    def _single_photo_peaking_mask(self, scaled: QPixmap) -> QPixmap:
        """The peaking mask for ``scaled`` (same size; may be null).

        Single Laplacian-based implementation for every context — both
        the focus-bracket "stack-film" overlay and the per-photo view.
        The sensitivity slider drives the same threshold mapping in
        both cases (Nelson 2026-06-06 — the prior split into Sobel +
        NMS-ridged "absolute" variants was worse than the prototype's
        original simple Laplacian, especially when focus sits on
        texture rather than on hard boundaries).

        ``_stack_film_peaking`` is kept as a state flag for future
        layout differences but no longer changes the algorithm.
        """
        from core.focus_peaking import (
            color_tuple_for_name,
            compute_peaking_absolute,
        )
        color = color_tuple_for_name(self._peaking_color_name)
        sens = self._effective_peaking_sensitivity()
        return compute_peaking_absolute(
            scaled, color=color, sensitivity=sens,
        ).mask

    def _overlay_mask(self, scaled: QPixmap, mask: QPixmap) -> QPixmap:
        """Composite ``mask`` over ``scaled`` (no-op if mask null)."""
        if mask is None or mask.isNull():
            return scaled
        out = QPixmap(scaled.size())
        out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        try:
            painter.drawPixmap(0, 0, scaled)
            painter.drawPixmap(0, 0, mask)
        finally:
            painter.end()
        return out

    def _composite_box_peaking(self, scaled: QPixmap) -> QPixmap:
        """Single-photo peaking (E5) on the zoomed box content —
        recomputed each render (box content changes with pan/photo,
        so caching by size would be wrong here)."""
        if scaled.isNull():
            return scaled
        return self._overlay_mask(
            scaled, self._single_photo_peaking_mask(scaled),
        )

    # ── Drag-the-photo gesture (box-zoom only) ─────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            self._boxzoom_enabled
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self._drag_anchor = event.pos()
            self._drag_pan_start = self._effective_pan()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_anchor is None or self._drag_pan_start is None:
            super().mouseMoveEvent(event)
            return
        dx = event.pos().x() - self._drag_anchor.x()
        dy = event.pos().y() - self._drag_anchor.y()
        sx, sy = getattr(self, "_pan_px_scale", (0.0, 0.0))
        # Drag the photo right → reveal content to the left → pan
        # fraction decreases. Hence the minus.
        pfx, pfy = self._drag_pan_start
        self._set_pan(pfx - dx * sx, pfy - dy * sy)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_anchor is not None:
            self._drag_anchor = None
            self._drag_pan_start = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _halfres_fitted(self, area: QSize) -> QPixmap | None:
        """The half-res RAW decode (real demosaiced sensor pixels)
        fitted to ``area``; ``None`` for non-RAW or a decode failure.

        Lazy + cached per photo (cleared in :meth:`set_photo`); only
        ever called when peaking is on, so the slow decode never
        hits the navigation hot path. This single pixmap is BOTH the
        displayed base and the peaking source in the non-zoom RAW
        path, so the mask registers to the picture by construction.

        Why it must also be what's *displayed* (fixed 2026-05-16,
        Nelson — "peaking not on the right edges, there is a gap"):
        Panasonic applies in-camera lens-distortion correction to
        its embedded JPEG thumb, while ``rawpy.postprocess`` returns
        the *uncorrected* sensor projection. Measured on a real
        RW2: centre NCC ≈0.985 but corners diverge to ≈0.63 — a
        radial warp no aspect-ratio scaling can undo. Peaking a
        postprocess-derived mask onto the corrected thumb put the
        highlight on the wrong pixels near the edges. Showing the
        decode we peak removes the mismatch entirely."""
        path = self._current_path
        if path is None or path.suffix.lower() not in _RAW_EXTENSIONS:
            return None
        key = str(path)
        cached = self._halfres_cache
        if cached is not None and cached[0] == key:
            half = cached[1]
        else:
            image = _load_raw_half_res(path)
            if image is None or image.isNull():
                return None                    # graceful fallback
            half = QPixmap.fromImage(image)
            self._halfres_cache = (key, half)
        return half.scaled(
            area,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def _non_zoom_base(self, area: QSize) -> QPixmap:
        """The pixmap shown in the non-zoom view.

        Normally the cheap embedded thumb fitted to ``area`` (instant
        — paging stays fast). When peaking is on for a RAW it is the
        **half-res sensor decode** instead, so the displayed pixels
        and the peaked pixels are identical (registration is exact by
        construction) and the user judges focus on honest sensor
        data, not the camera's sharpened/distortion-corrected JPEG
        (Nelson 2026-05-16). Decode failure falls back to the thumb
        (degraded cue beats none; never crash). JPG/HEIC: always the
        loaded pixmap (already real, full-res pixels).

        Exception — **stack-film peaking** stays on the cheap thumb:
        the focus-bracket sweep flips frames fast and a per-frame
        half-res RAW decode would wreck that (docs/18 §"Focus
        peaking" — Stack film is "preview-based, kept as-is — fast
        frame-flip is the priority")."""
        if self._peaking_enabled and not self._stack_film_peaking:
            half = self._halfres_fitted(area)
            if half is not None:
                return half
        return self._source_pixmap.scaled(
            area,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def _composite_peaking(self, scaled: QPixmap) -> QPixmap:
        """Paint the single-photo peaking mask on top of ``scaled``
        for the non-zoom view.

        Same content-adaptive algorithm as the Phase-B box
        (:meth:`_single_photo_peaking_mask`) so the sensitivity
        slider has identical teeth here: a soft frame at low
        sensitivity goes dark, it doesn't "show focus" everywhere
        (Nelson 2026-05-15). The non-zoom photo is stable per
        (size, colour, sensitivity), so unlike the box path we
        cache by that key — toggling on/off stays instant while a
        colour/sensitivity change still recomputes.

        RAW fidelity (docs/18 §"RAW non-zoom peaking", Nelson
        2026-05-16): ``scaled`` here is the *honest* base — for a
        RAW the caller already swapped in the half-res sensor decode
        (:meth:`_non_zoom_base`), so the mask is computed from and
        overlaid on the **same** pixels: edges land exactly. The
        Phase-B box remains the full-res 1:1 verdict; this is the
        fast whole-frame read. (JPG/HEIC: ``scaled`` is the real
        image already.)
        """
        if scaled.isNull():
            return scaled
        key = (
            scaled.width(),
            scaled.height(),
            self._peaking_color_name,
            self._effective_peaking_sensitivity(),
            self._stack_film_peaking,
        )
        cache = self._peaking_mask_cache
        if cache is None or cache[0] != key:
            mask = self._single_photo_peaking_mask(scaled)
            self._peaking_mask_cache = (key, mask)
            cache = self._peaking_mask_cache
        return self._overlay_mask(scaled, cache[1])


# ── Helpers ──────────────────────────────────────────────────────


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def _monospace_font() -> QFont:
    """Consolas on Windows, system mono elsewhere. Used for the EXIF
    info line and the position label so digit-width changes don't
    cause the layout to jitter."""
    font = QFont("Consolas")
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


# ── Image-loading helpers (carried over verbatim from PhotoCanvas) ─


# ── EXIF / libraw orientation ────────────────────────────────────
# Frozen 2026-05-18 (Nelson eyeball: "vertical photos displayed
# horizontally"). Qt's QPixmap(path) does NOT apply the EXIF
# Orientation tag and rawpy.postprocess returns un-rotated sensor
# pixels, so portrait shots showed sideways. Every load path must
# present the image upright (HEIF already did via exif_transpose).

def _qimagereader_oriented(reader: QImageReader) -> QPixmap:
    """Read via QImageReader with EXIF auto-transform → upright
    QPixmap (empty on failure). Honours the Orientation tag for
    JPEG/TIFF/PNG and camera embedded-JPEG thumbs."""
    reader.setAutoTransform(True)
    img = reader.read()
    if img.isNull():
        return QPixmap()
    return QPixmap.fromImage(img)


def _read_oriented_pixmap(path: Path) -> QPixmap:
    return _qimagereader_oriented(QImageReader(str(path)))


def _oriented_pixmap_from_jpeg_bytes(data: object) -> QPixmap:
    """Orientation-correct load of an in-memory JPEG (the RAW's
    embedded preview, which carries the camera Orientation tag)."""
    buf = QBuffer()
    buf.setData(QByteArray(bytes(data)))  # type: ignore[arg-type]
    buf.open(QBuffer.OpenModeFlag.ReadOnly)
    try:
        return _qimagereader_oriented(QImageReader(buf))
    finally:
        buf.close()


# libraw `raw.sizes.flip` → the rotation (deg, CW) that makes the
# postprocessed buffer upright. 0 = none, 3 = 180, 5 = 90° CCW,
# 6 = 90° CW (standard libraw convention).
_LIBRAW_FLIP_ROTATION = {3: 180, 5: 270, 6: 90}


def _apply_libraw_flip(img: QImage, flip: object) -> QImage:
    try:
        angle = _LIBRAW_FLIP_ROTATION.get(int(flip or 0), 0)
    except (TypeError, ValueError):
        angle = 0
    if angle == 0 or img.isNull():
        return img
    return img.transformed(QTransform().rotate(angle))


def _load_pixmap(path: Path) -> QPixmap:
    """Native QPixmap → Pillow JPEG fallback → rawpy embedded thumb
    → pillow-heif → empty. Every path is EXIF/libraw orientation-
    corrected (upright).

    The Pillow JPEG fallback (B-013, frozen 2026-05-25 / Nelson +
    Nepal trip) catches **MPO** files — Multi-Picture JPEGs the
    G9 II writes for High-Resolution Mode shots (100 MP composite
    + thumbs, ~25 MB on disk, ``.jpg`` extension). Qt's built-in
    JPEG decoder parses the header (returns the right size) but
    errors on the data with "Unable to read image data"; Pillow
    handles MPO natively. Only fires when the native path
    produces a null pixmap — JPEGs Qt CAN decode never pay the
    fallback's import cost.
    """
    suffix = path.suffix.lower()
    if suffix in _QPIXMAP_NATIVE:
        pix = _read_oriented_pixmap(path)
        if not pix.isNull():
            return pix
        if suffix in {".jpg", ".jpeg"}:
            pix = _load_jpeg_via_pillow(path)
            if not pix.isNull():
                return pix
    if suffix in _RAW_EXTENSIONS:
        return _load_raw_thumbnail(path)
    if suffix in _HEIF_EXTENSIONS:
        return _load_heif(path)
    return _read_oriented_pixmap(path)


# Display-tier cap on the Pillow fallback. The cull canvas runs
# at screen-res (~2 MP on a 1080p display); decoding a 100 MP
# G9 II HighRes-Mode shot at native size would cost ~300 MB of
# RGB memory per frame. 4096 keeps headroom for 4K monitors with
# zoom and stays well under the 1-second-per-photo browse budget.
_PILLOW_FALLBACK_MAX_SIDE = 4096


def _load_jpeg_via_pillow(path: Path) -> QPixmap:
    """Decode a JPEG via Pillow when Qt's reader gives up — most
    often a G9 II HighRes-Mode MPO file (multi-picture JPEG that
    Qt parses the SIZE of but errors decoding the DATA).

    Bounded to :data:`_PILLOW_FALLBACK_MAX_SIDE` via
    ``Image.thumbnail`` so a 100 MP shot doesn't materialise a
    half-gigabyte RGB buffer just to be downscaled by the canvas
    on the very next call. EXIF orientation honored via
    ``ImageOps.exif_transpose``.

    Returns an empty QPixmap on any failure (Pillow missing,
    truncated file, etc.). Never raises — the caller already
    treats a null pixmap as "preview unavailable" and surfaces
    that to the user; an exception here would be worse.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:
        log.warning(
            "Pillow not available — can't decode JPEG variant: %s",
            path,
        )
        return QPixmap()
    try:
        with Image.open(path) as im:
            im.load()
            im = ImageOps.exif_transpose(im)
            im.thumbnail(
                (_PILLOW_FALLBACK_MAX_SIDE, _PILLOW_FALLBACK_MAX_SIDE),
                Image.Resampling.LANCZOS,
            )
            if im.mode != "RGB":
                im = im.convert("RGB")
            width, height = im.size
            raw = im.tobytes("raw", "RGB")
    except Exception as exc:                          # noqa: BLE001
        log.warning(
            "Pillow JPEG fallback failed for %s: %s", path, exc,
        )
        return QPixmap()
    # Materialise into a QImage. The .copy() is load-bearing —
    # the QImage(buffer, …) constructor borrows the buffer; once
    # ``raw`` goes out of scope here the QImage's bytes would be
    # freed under it.
    qimg = QImage(
        raw, width, height, width * 3,
        QImage.Format.Format_RGB888,
    ).copy()
    return QPixmap.fromImage(qimg)


def _load_heif(path: Path) -> QPixmap:
    """Decode an HEIF/HEIC still (iPhone) to a full-res QPixmap.

    Qt ships no HEIF codec on Windows; ``pillow-heif`` provides one
    (declared dep — docs/13). iPhone HEICs are already final images
    (not RAW with an embedded thumb), so this single full decode
    serves both the preview *and* the Phase-B box crop — there is
    no degraded-thumbnail caveat here, unlike RAW. EXIF orientation
    is baked in so the canvas treats the pixels as upright.

    ``register_heif_opener`` is idempotent (pillow-heif guards
    double registration), so calling it lazily here keeps the
    plug-in self-contained without a global import side-effect.

    Returns an empty QPixmap on any failure (missing dep, corrupt
    file) — the caller then behaves exactly as for any other
    unreadable file. Never raises.
    """
    try:
        import pillow_heif
        from PIL import Image, ImageOps
    except ImportError:
        log.warning(
            "pillow-heif/Pillow unavailable; cannot preview %s", path,
        )
        return QPixmap()
    try:
        pillow_heif.register_heif_opener()  # idempotent
        with Image.open(str(path)) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode != "RGB":
                im = im.convert("RGB")
            data = im.tobytes("raw", "RGB")
            qimg = QImage(
                data, im.width, im.height, 3 * im.width,
                QImage.Format.Format_RGB888,
            )
            return QPixmap.fromImage(qimg.copy())  # detach from `data`
    except Exception as exc:  # noqa: BLE001 — best-effort preview
        log.warning("HEIF decode failed for %s: %s", path, exc)
        return QPixmap()


def _load_raw_full_res(path: Path):
    """Decode a RAW to a full-resolution QImage (real sensor pixels).

    Used only by the Phase-B box crop (docs/18 RAW-fidelity): the
    embedded thumbnail lies about sharpness, so judging focus needs
    the actual demosaiced sensor data. ``use_camera_wb`` for correct
    colour; defaults otherwise (a faithful render, not auto-exposed
    — detail matters more than brightness for a focus call).

    Returns a ``QImage`` (RGB888) or ``None`` on any failure (rawpy
    quirks vary per body); the caller falls back to the thumb. This
    is the slow path (~hundreds of ms for 24 MP) — the caller must
    keep it lazy + cached, never on the navigation hot path.
    """
    try:
        import numpy as np
        import rawpy
    except ImportError:
        log.warning("rawpy/numpy unavailable; cannot full-res %s", path)
        return None
    try:
        with rawpy.imread(str(path)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, output_bps=8)
            flip = getattr(raw.sizes, "flip", 0)
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.warning("rawpy full-res decode failed for %s: %s", path, exc)
        return None
    try:
        rgb = np.ascontiguousarray(rgb)
        h, w = rgb.shape[:2]
        img = QImage(
            rgb.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888,
        )
        # rawpy.postprocess returns un-rotated sensor pixels — apply
        # the libraw flip so portrait RAWs aren't sideways.
        return _apply_libraw_flip(img.copy(), flip)  # detach + upright
    except Exception as exc:  # noqa: BLE001
        log.warning("RAW→QImage failed for %s: %s", path, exc)
        return None


def _load_raw_half_res(path: Path):
    """Decode a RAW to a **half-resolution** QImage (real sensor
    pixels, ~1/4 the area / memory of the full-res decode).

    The non-zoom peaking read (docs/18 §"Sharp-reference anchor",
    RAW-fidelity) uses this: the camera's embedded JPEG thumb is
    sharpened + noise-reduced by the body, so peaking on it invents
    edges (everything looks "in focus"). A half-size demosaic is
    honest sensor detail — enough to tell sharp from soft across the
    whole frame in first-pass triage — while ``half_size=True`` makes
    rawpy skip the full demosaic (~4x faster than
    :func:`_load_raw_full_res`). The Phase-B box still decodes
    full-res for the 1:1 verdict; this is the cheap whole-frame cue.

    Returns a ``QImage`` (RGB888) or ``None`` on any failure (rawpy
    quirks vary per body); the caller falls back to the thumb. Slow
    relative to nav — the caller must keep it lazy + cached, never on
    the navigation hot path.
    """
    try:
        import numpy as np
        import rawpy
    except ImportError:
        log.warning("rawpy/numpy unavailable; cannot half-res %s", path)
        return None
    try:
        with rawpy.imread(str(path)) as raw:
            rgb = raw.postprocess(
                use_camera_wb=True, output_bps=8, half_size=True,
            )
            flip = getattr(raw.sizes, "flip", 0)
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.warning("rawpy half-res decode failed for %s: %s", path, exc)
        return None
    try:
        rgb = np.ascontiguousarray(rgb)
        h, w = rgb.shape[:2]
        img = QImage(
            rgb.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888,
        )
        return _apply_libraw_flip(img.copy(), flip)  # detach + upright
    except Exception as exc:  # noqa: BLE001
        log.warning("RAW→QImage (half) failed for %s: %s", path, exc)
        return None


def _load_raw_thumbnail(path: Path) -> QPixmap:
    """Preview a RAW file. Fast path = the camera's embedded JPEG
    thumb; slow fallback = a half-res rawpy demosaic.

    Task #131 (Nelson 2026-05-24): some RAWs (Lumix high-resolution
    shot mode, certain DNG variants, ancient firmware) carry NO
    embedded preview at all. The fast path then returned an empty
    QPixmap and the viewer rendered nothing — a silent blank canvas
    instead of the photo. The fix is a slow-but-reliable demosaic
    fallback so EVERY supported RAW renders SOMETHING.

    Tries in order:
      1. ``raw.extract_thumb()`` — ~10 ms; embedded JPEG / BITMAP.
      2. ``_load_raw_half_res(path)`` — ~100-200 ms for 24 MP;
         always succeeds when rawpy can decode the file at all.

    Returns an empty QPixmap only when BOTH paths fail (file is
    genuinely corrupt or rawpy doesn't know the format).
    """
    try:
        import rawpy
    except ImportError:
        log.warning("rawpy not available; cannot preview RAW %s", path)
        return QPixmap()

    # ── Fast path: extract the camera's embedded thumb ──
    thumb = None
    try:
        with rawpy.imread(str(path)) as raw:
            thumb = raw.extract_thumb()
    except Exception as exc:  # noqa: BLE001 — best-effort preview
        log.info(
            "RAW %s has no embedded thumb (%s); falling back to "
            "half-res demosaic",
            path.name, exc,
        )
        return _load_raw_thumbnail_via_demosaic(path)

    if thumb.format == rawpy.ThumbFormat.JPEG:
        pix = _oriented_pixmap_from_jpeg_bytes(thumb.data)
        if not pix.isNull():
            return pix
        log.info(
            "Embedded JPEG thumb did not load from %s; falling back "
            "to half-res demosaic", path.name,
        )
        return _load_raw_thumbnail_via_demosaic(path)
    if thumb.format == rawpy.ThumbFormat.BITMAP:
        try:
            from PIL import Image
        except ImportError:
            log.warning(
                "Pillow not available; falling back to half-res "
                "demosaic for BITMAP thumb in %s", path,
            )
            return _load_raw_thumbnail_via_demosaic(path)
        try:
            img = Image.fromarray(thumb.data)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=88)
            qimg = QImage.fromData(buf.getvalue(), "JPG")
            if not qimg.isNull():
                return QPixmap.fromImage(qimg)
        except Exception as exc:  # noqa: BLE001
            log.info(
                "BITMAP thumb path failed for %s (%s); falling back "
                "to half-res demosaic", path.name, exc,
            )
        return _load_raw_thumbnail_via_demosaic(path)

    # Unknown ThumbFormat — try the demosaic too rather than
    # leaving a blank.
    log.info(
        "RAW %s carries an unknown thumb format; falling back to "
        "half-res demosaic", path.name,
    )
    return _load_raw_thumbnail_via_demosaic(path)


def _load_raw_thumbnail_via_demosaic(path: Path) -> QPixmap:
    """Slow-but-reliable RAW preview path (task #131).

    Half-res rawpy demosaic → QPixmap, used when the embedded
    thumbnail path fails. ~4x faster than full-res while still
    covering every RAW that rawpy can decode at all. Returns an
    empty QPixmap only when rawpy itself can't read the file
    (genuine corruption / unsupported format)."""
    qimg = _load_raw_half_res(path)
    if qimg is None or qimg.isNull():
        log.warning(
            "RAW %s could not be previewed by any path (thumb + "
            "demosaic both failed); canvas will render blank",
            path,
        )
        return QPixmap()
    return QPixmap.fromImage(qimg)
