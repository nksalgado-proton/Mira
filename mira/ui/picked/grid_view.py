"""GridView — the culler's grid/contact-sheet view (E9, docs/18 §Grid).

**Reused from the legacy ``ui/culler/grid_view.py`` (charter §0/§5.2).** Rendering is
verbatim; the changes are (1) the data seam — imports rewired to ``mira.*`` (settings,
state constants, the shared canvas decode helpers); (2) one approved improvement (Nelson
2026-06-01): an optional per-tile rich-text **EXIF caption** (``GridItem.caption_html``)
that the host fills in the exactly-two-photos compare case to highlight the differing
exposure params (see ``mira.picked.exif_compare``). Empty caption ⇒ the bulk grid is
unchanged.

Triage + navigation, **not** the focus decision (that stays the 1:1
box-zoom). The host (IngestPickerPage) feeds it the photos passing
the active K/C/D filter, in order; the grid shows a thumbnail tile
per photo with a mini 3-state bar and the 0–1000 focus rating.

Interactions (docs/18 §Grid refinements, Nelson 2026-05-16):

* **Click the image** → open that photo in single view
  (``photo_activated``).
* **Click the mini state bar** → cycle that photo's state
  discarded→candidate→kept (``state_cycle_requested``) — same
  "the state bar IS the control" model as the main canvas; the
  host owns the journal and calls :meth:`update_tile_state` back.

Thumbnails load **lazily** off a timer (a few per tick) so a
bucket of hundreds never blocks the UI (Speed is King). Tile size
maximises by count (few → big, many → contact-sheet) but never
shrinks below a legible minimum (the area scrolls instead).
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.aspect_ratio import get_aspect_ratio
from mira.picked.status import STATE_CANDIDATE, STATE_SKIPPED, STATE_PICKED
from mira.settings.repo import SettingsRepo
from mira.ui.i18n import tr
from mira.ui.media.media_canvas import (   # shared RAW decode paths
    _RAW_EXTENSIONS,
    _load_pixmap,
    _load_raw_half_res,
)


log = logging.getLogger(__name__)

# Same uppercase, glyph-free, present-tense action labels as the
# main canvas state bar (MediaCanvas._STATE_LABELS) — kept in sync
# deliberately (Nelson 2026-05-16: past tense was factually wrong).
_STATE_LABELS = {
    STATE_SKIPPED: tr("DISCARD"),
    STATE_CANDIDATE: tr("COMPARE"),
    STATE_PICKED: tr("KEEP"),
}

# Minimum tile side. Below this the thumbnail is unreadable → the
# grid scrolls instead of shrinking further. There is deliberately
# no max: the photo grows to fill whatever space the cell gets
# (Nelson 2026-05-16 — the picture should grow, not the chrome).
_TILE_MIN = 170
_THUMBS_PER_TICK = 4            # lazy-load budget per timer tick

# At/below this many tiles the grid is a *fine A/B tie-break
# comparison*, not bulk triage — render those tiles from the honest
# half-res sensor decode, not the camera thumb (Nelson 2026-05-16:
# "2, 3 or 4 photos"). Bounded cost (≤N decodes), off the lazy
# loader thread → still Speed-is-King. Eyeball-tunable constant.
_GRID_HONEST_MAX = 4

# Aspect within ±this of 1.0 reads as "square-ish" — it doesn't
# vote landscape or portrait (a near-square crop shouldn't tip a
# bucket's orientation either way).
_ORIENT_EPS = 0.06


def grid_columns(count: int, avail_w: int) -> int:
    """Columns that *maximise tile size by count* (docs/18): few
    photos → few wide columns; many → contact-sheet. Never so many
    that a tile would fall below ``_TILE_MIN`` (scroll instead).
    Pure + deterministic → unit-tested."""
    if count <= 0 or avail_w <= 0:
        return 1
    by_count = max(1, int(math.ceil(math.sqrt(count))))
    by_width = max(1, avail_w // _TILE_MIN)
    return max(1, min(by_count, by_width, count))


def grid_tile_aspect(
    sizes: list[tuple[int, int]],
    fallback_value: float,
) -> float:
    """Tile aspect (**w / h**) for the grid, from the loaded thumbs.

    docs/18 §Grid refinement: "shape cells to the modal aspect of
    the filtered set — most buckets are single-orientation, so the
    majority fills edge-to-edge, the few odd ones letterbox" (fit,
    never crop — docs/18 line 172).

    A photo votes *landscape* (ratio > 1+ε) or *portrait*
    (ratio < 1−ε); near-square photos abstain. If one orientation
    has **strictly more** votes, the tile takes the **median aspect
    of that orientation** (a 3:2 bucket → 1.5 real tiles, a 4:3 →
    1.333; the few off-shape letterbox). On a tie — or no oriented
    photos at all — fall back to ``fallback_value`` (the user's
    preferred-aspect-ratio value, or 1.0 = square for "Original").
    Pure + deterministic → unit-tested.
    """
    land: list[float] = []
    port: list[float] = []
    for w, h in sizes:
        if w <= 0 or h <= 0:
            continue
        r = w / h
        if r > 1.0 + _ORIENT_EPS:
            land.append(r)
        elif r < 1.0 - _ORIENT_EPS:
            port.append(r)
        # else: square-ish → abstains
    if len(land) > len(port) and land:
        return float(statistics.median(land))
    if len(port) > len(land) and port:
        return float(statistics.median(port))
    return fallback_value if fallback_value > 0 else 1.0


def preferred_aspect_value() -> float:
    """The user's preferred-aspect-ratio as a w/h float, or 1.0
    (square) for "Original"/unset — the grid's no-dominant-
    orientation tie-break (docs/18; the same setting seeds the
    Process default crop). Reads the rebuild settings (charter §5.2)."""
    try:
        ar = get_aspect_ratio(SettingsRepo().load().preferred_aspect_ratio or "")
    except Exception:  # noqa: BLE001 — a settings hiccup must not break the grid
        return 1.0
    return 1.0 if ar.is_original else ar.value


@dataclass
class GridItem:
    """One tile's data. ``index`` is the position in the host's
    full item list (so click → navigate is unambiguous even though
    the grid may only show a filtered subset).

    docs/24 Step 2c (2026-05-28): the tile can represent a
    photo OR a clip. ``kind`` defaults to ``"photo"`` (legacy
    behaviour) and the tile renders from ``path`` via the photo
    decode pipeline (RAW half-res / embedded thumb). When
    ``kind == "clip"`` and ``preview_path`` is set, the tile loads
    that cached JPEG directly (no RAW decode, no full-video
    extraction) and paints a small "▶" overlay so the user reads
    the tile as a clip at a glance.
    """

    index: int
    path: Path
    state: str
    rating: int | None
    kind: str = "photo"
    preview_path: Path | None = None
    # M2.5 (Nelson 2026-06-01): rich-text EXIF caption shown in the exactly-two-photos
    # compare case (the host builds it via cull.exif_compare, with the differing exposure
    # params emphasised). Empty = no caption (bulk grid / not a 2-photo compare). Rendered
    # as a translucent pill OVERLAID on the bottom of the photo (no separate layout row, so
    # the photo keeps its full size).
    caption_html: str = ""


# Constant tile-border width — "just enough to be clickable"
# (Nelson 2026-05-16). MUST match the `QFrame#GridTile` border-width
# in both QSS themes so the ring == the border exactly.
_BORDER_W = 6


class _ClickLabel(QLabel):
    """A QLabel that *fully consumes* a left click and emits
    ``clicked`` on release. It accepts the press too so the click
    never propagates to the parent frame (whose own clicks are the
    border-ring = cycle-state target — they must stay distinct)."""

    clicked = pyqtSignal()

    def mousePressEvent(self, ev):  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)


class _GridTile(QFrame):
    """A constant-width, state-coloured border framing the photo
    (Nelson 2026-05-16). The **border IS the status + the cycle
    control**; the photo fills the interior and scales with the
    cell — chrome never grows, the picture does. Two distinct
    click targets: the photo (→ open Single) and the border ring
    (→ cycle state). The host owns the journal."""

    activated = pyqtSignal(int)        # photo clicked → index
    cycle_requested = pyqtSignal(int)  # border ring clicked → index

    def __init__(self, item: GridItem,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._index = item.index
        self.setObjectName("GridTile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)  # ring clickable
        sp = self.sizePolicy()
        sp.setHorizontalPolicy(QSizePolicy.Policy.Expanding)
        sp.setVerticalPolicy(QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)             # cell tracks modal aspect
        self.setSizePolicy(sp)
        # Minimum is applied via _apply_min() once _aspect is set
        # below — it must follow the aspect, not be a square floor.

        lay = QVBoxLayout(self)
        # Margins == border width → the photo sits flush inside the
        # ring; the ring is the only frame-clickable area.
        lay.setContentsMargins(
            _BORDER_W, _BORDER_W, _BORDER_W, _BORDER_W,
        )
        lay.setSpacing(0)
        self._photo = _ClickLabel()
        self._photo.setObjectName("GridTilePhoto")
        self._photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._photo.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored,
        )
        self._photo.clicked.connect(
            lambda: self.activated.emit(self._index)
        )
        lay.addWidget(self._photo)

        # M2.5: optional EXIF caption (rich text), shown only in the 2-photo compare case
        # (Nelson's EXIF-diff improvement). It OVERLAYS the photo (a child of the photo
        # label, not a layout row) so the photo keeps its full size — Nelson 2026-06-01:
        # "just write the info on top of the photos, no separate info area". Click-through;
        # repositioned in _rescale (same pattern as the clip ▶ overlay below). Hidden when
        # there is no caption, so the bulk grid is unchanged.
        self._caption = QLabel(self._photo)
        self._caption.setObjectName("GridTileExif")
        self._caption.setTextFormat(Qt.TextFormat.RichText)
        self._caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._caption.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        if item.caption_html:
            self._caption.setText(item.caption_html)
            self._caption.show()
        else:
            self._caption.hide()

        # docs/24 Step 2c: clip tiles get a small overlay indicator
        # so the user reads them as clips at a glance, not as photos
        # that happen to come from a video. The "▶" glyph is the
        # universal play-button affordance; it sits centred on the
        # photo label (z-order > the pixmap) and is non-interactive
        # (clicks pass through to the photo's click handler).
        self._is_clip = item.kind == "clip"
        if self._is_clip:
            self._play_overlay = QLabel("▶", self._photo)
            self._play_overlay.setObjectName("GridTilePlayOverlay")
            self._play_overlay.setStyleSheet(
                "QLabel#GridTilePlayOverlay {"
                "  color: rgba(255, 255, 255, 230);"
                "  background: rgba(0, 0, 0, 110);"
                "  border-radius: 24px;"
                "  font-size: 28pt;"
                "  padding: 4px 14px;"
                "}"
            )
            self._play_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._play_overlay.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self._play_overlay.show()
        else:
            self._play_overlay = None

        self._src: QPixmap | None = None
        self._state = item.state
        # Tile aspect = w / h. 1.0 = square (the start state + the
        # "Original"/no-dominant fallback). GridView sets the modal
        # aspect once thumbs load (see grid_tile_aspect).
        self._aspect = 1.0
        # Min *width* only (readable floor + matches grid_columns'
        # avail_w // _TILE_MIN). Height is driven TOP-DOWN by
        # GridView._apply_tile_heights — QGridLayout in a scrolling
        # area collapses rows to the min and ignores heightForWidth,
        # so the cell shape must be set explicitly, not via Qt's
        # weak height-for-width (Nelson 2026-05-16: "4×3 on a
        # square").
        self.setMinimumWidth(_TILE_MIN)
        self.set_state(item.state)

    def set_aspect(self, aspect: float) -> None:
        """Record the cell's modal w/h. GridView reads this in
        :meth:`GridView._apply_tile_heights` to pin the actual pixel
        height — heights flow top-down from the known viewport, never
        from the tile's own size, so there is no resize feedback."""
        a = aspect if aspect and aspect > 0 else 1.0
        self._aspect = a

    @property
    def aspect(self) -> float:
        return self._aspect

    def set_pixmap(self, pm: QPixmap | None) -> None:
        if pm is None or pm.isNull():
            self._src = None
            self._photo.setText(tr("…"))
            return
        self._src = pm
        self._rescale()

    def _rescale(self) -> None:
        if self._src is None or self._src.isNull():
            return
        sz = self._photo.size()
        if sz.width() < 2 or sz.height() < 2:
            return
        self._photo.setPixmap(self._src.scaled(
            sz,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))
        # docs/24 Step 2c — re-centre the clip play overlay on the
        # photo label whenever the photo resizes.
        if self._play_overlay is not None:
            hint = self._play_overlay.sizeHint()
            ow = max(60, hint.width())
            oh = max(60, hint.height())
            x = (sz.width() - ow) // 2
            y = (sz.height() - oh) // 2
            self._play_overlay.setGeometry(x, y, ow, oh)

        # M2.5 — the EXIF caption overlay (a centred pill along the bottom of the photo,
        # Nelson 2026-06-01). Only when it carries text (the 2-photo compare case).
        if self._caption is not None and self._caption.text():
            hint = self._caption.sizeHint()
            cw = min(sz.width(), hint.width() + 16)
            ch = hint.height()
            x = (sz.width() - cw) // 2
            y = max(0, sz.height() - ch - 6)
            self._caption.setGeometry(x, y, cw, ch)
            self._caption.raise_()

    def resizeEvent(self, ev):  # noqa: N802
        super().resizeEvent(ev)
        self._rescale()                 # photo grows with the cell

    def set_state(self, state: str) -> None:
        # Dynamic property drives the QFrame#GridTile[state=…]
        # border-colour rules (both themes). No text — colour only.
        self._state = state
        self.setProperty("state", state)
        self.setToolTip(
            f"{_STATE_LABELS.get(state, _STATE_LABELS[STATE_SKIPPED])}"
        )
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, ev):  # noqa: N802 — ring grabs the click
        if ev.button() == Qt.MouseButton.LeftButton:
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):  # noqa: N802 — border ring → cycle
        if ev.button() == Qt.MouseButton.LeftButton:
            self.cycle_requested.emit(self._index)
            ev.accept()
            return
        super().mouseReleaseEvent(ev)


class GridView(QScrollArea):
    """Scrollable grid of :class:`_GridTile`. Feed it with
    :meth:`set_items`; it lazily fills thumbnails and re-flows
    columns on resize."""

    photo_activated = pyqtSignal(int)
    state_cycle_requested = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("GridView")
        self.setWidgetResizable(True)
        # The vertical scrollbar is ALWAYS present and the
        # horizontal one NEVER. Without this, a rebuild that grows
        # the content toggles the scrollbar, which changes the
        # viewport width, which fires resizeEvent, which rebuilds…
        # an infinite relayout/repaint storm (Nelson 2026-05-16:
        # "creating dialogs like crazy" — that was the flicker
        # storm). Fixed bars ⇒ stable width ⇒ no oscillation.
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        self.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._host = QWidget()
        self._grid = QGridLayout(self._host)
        self._grid.setContentsMargins(8, 8, 8, 8)
        self._grid.setSpacing(8)
        self.setWidget(self._host)

        self._items: list[GridItem] = []
        self._tiles: dict[int, _GridTile] = {}     # index → tile
        # Keyed by (path, honest) — the same RAW is a different
        # pixmap as a thumb (bulk) vs a half-res decode (≤N tiles),
        # so a filter change that crosses the threshold can't serve
        # the wrong one.
        self._thumb_cache: dict[tuple[str, bool], QPixmap] = {}
        self._cols = 0
        # Tile w/h. 1.0 = square (start + "Original"/no-dominant
        # fallback); set to the bucket's modal aspect once thumbs
        # load (grid_tile_aspect).
        self._tile_aspect = 1.0
        # The no-dominant-orientation fallback = the user's
        # preferred-aspect-ratio setting; read once per grid open
        # (in set_items) so the 20 ms loader tick never touches disk.
        self._fallback_aspect = 1.0
        self._laying_out = False        # re-entrancy guard
        # Honest mode = the half-res sensor decode for ≤N tiles (a
        # sharpness tie-break aid). It applies rawpy auto-bright,
        # which independently normalises each frame's brightness —
        # fine for focus/burst comparison, but for an EXPOSURE
        # bracket it erases the very exposure spread the grid exists
        # to show (Nelson 2026-05-17: "all the photos in the grid
        # are the [same processed] preview, not the originals" —
        # measured: −EV/0/+EV all flattened to ~120 mean). The host
        # disables it for exposure brackets so tiles use the
        # exposure-faithful embedded thumb instead.
        self._honest_enabled = True

        self._loader = QTimer(self)
        self._loader.setInterval(20)    # not 0 — 0 spins the loop hot
        self._loader.timeout.connect(self._load_some_thumbs)
        self._pending: list[int] = []              # indices to load

    # ── Public API ─────────────────────────────────────────────────

    def set_items(self, items: list[GridItem]) -> None:
        """Rebuild the grid for the given (already filtered, ordered)
        photos. Cheap: tiles are placeholders; thumbnails stream in
        off the timer."""
        self._items = list(items)
        # Start square; settle to the bucket's modal aspect as the
        # lazy thumbs arrive (docs/18 "computed from loaded thumbs").
        self._tile_aspect = 1.0
        self._fallback_aspect = preferred_aspect_value()
        self._relayout(force=True)
        self._pending = [it.index for it in self._items]
        if self._pending:
            self._loader.start()

    def set_honest_enabled(self, enabled: bool) -> None:
        """Allow / forbid the honest half-res decode (default on).
        Exposure-bracket grids set this False: the auto-bright in the
        half-res path normalises each frame and destroys the exposure
        spread; the embedded thumb is exposure-faithful (docs/18
        §"Bracket surfaces", Nelson 2026-05-17)."""
        self._honest_enabled = bool(enabled)

    def _honest(self) -> bool:
        """≤N tiles → fine tie-break comparison, render from the
        honest half-res sensor decode (Nelson 2026-05-16). Disabled
        for exposure brackets — see :meth:`set_honest_enabled`."""
        return (
            self._honest_enabled
            and 0 < len(self._items) <= _GRID_HONEST_MAX
        )

    def update_tile_state(self, index: int, state: str,
                          rating: int | None = None) -> None:
        """Recolour one tile's border after the host advanced the
        journal — no rebuild, no thumbnail reload. ``rating`` is
        accepted for call-site compatibility but unused (the new
        tile has no text — colour only)."""
        tile = self._tiles.get(index)
        if tile is not None:
            tile.set_state(state)

    # ── Internals ──────────────────────────────────────────────────

    def _avail_width(self) -> int:
        return max(1, self.viewport().width() - 24)   # margins/spacing

    def _avail_height(self) -> int:
        return max(1, self.viewport().height() - 24)

    def _tile_width(self) -> int:
        """The pixel width one tile gets — deterministic from the
        (stable, scrollbar-AlwaysOn) viewport, the column count and
        the 8 px grid spacing. Top-down: tiles never report their own
        width back, so no resize feedback.

        Bounded by BOTH viewport dimensions: the few-photo comparison
        set (2-4 tiles) must fit the window *whole*, not blow up and
        force scroll / grow the dialog — the grid fits the window,
        never the window the grid (Nelson 2026-05-18). ``_TILE_MIN``
        stays the floor: many photos still hit it and scroll
        (contact-sheet, unchanged)."""
        cols = max(1, self._cols)
        # Width-bounded: _avail_width already nets the 8+8 grid
        # margins; subtract inter-column spacing.
        usable_w = self._avail_width() - 8 * (cols - 1)
        w_by_width = usable_w // cols
        # Height-bounded: the whole rows×cols grid should fit the
        # viewport height so every comparison tile is visible at once.
        n = max(1, len(self._items))
        rows = max(1, (n + cols - 1) // cols)
        usable_h = self._avail_height() - 8 * (rows - 1)
        h_per_row = max(1, usable_h // rows)
        w_by_height = int(h_per_row * max(0.1, self._tile_aspect))
        return max(_TILE_MIN, min(w_by_width, w_by_height))

    def _apply_tile_heights(self) -> None:
        """Pin every tile's pixel height to width / modal-aspect.

        QGridLayout inside a QScrollArea collapses rows to the tile
        minimum and ignores ``heightForWidth`` once the grid scrolls,
        so the cell shape can't be expressed declaratively — it must
        be set explicitly (Nelson 2026-05-16: "4×3 on a square"). The
        height is derived from the *viewport* (top-down), and the
        vertical scrollbar is AlwaysOn (stable width), so growing the
        content can't change the viewport width → no resize loop."""
        if not self._tiles:
            return
        w = self._tile_width()
        h = max(1, int(round(w / self._tile_aspect)))
        for tile in self._tiles.values():
            if tile.maximumHeight() != h or tile.minimumHeight() != h:
                tile.setFixedHeight(h)

    def _relayout(self, *, force: bool = False) -> None:
        if self._laying_out:            # re-entrancy guard (a rebuild
            return                      # can nest a resizeEvent)
        if not self._items:
            self._laying_out = True
            try:
                self._clear_grid()
                self._tiles.clear()
            finally:
                self._laying_out = False
            return
        cols = grid_columns(len(self._items), self._avail_width())
        # Rebuild ONLY when the column count changes (or forced).
        # Tile pixel size is *not* in the trigger — sub-pixel width
        # jitter must never cause a rebuild (that was the loop).
        if not force and cols == self._cols:
            return
        self._laying_out = True
        try:
            self._cols = cols
            self._clear_grid()
            self._tiles.clear()
            honest = self._honest()
            for pos, it in enumerate(self._items):
                tile = _GridTile(it)
                tile.set_aspect(self._tile_aspect)   # keep across rebuilds
                tile.activated.connect(self.photo_activated.emit)
                tile.cycle_requested.connect(
                    self.state_cycle_requested.emit
                )
                cache_path = (
                    it.preview_path if it.kind == "clip"
                    else it.path
                )
                cached = self._thumb_cache.get(
                    (str(cache_path) if cache_path else "",
                     it.kind, honest)
                )
                if cached is not None:
                    tile.set_pixmap(cached)
                self._tiles[it.index] = tile
                self._grid.addWidget(tile, pos // cols, pos % cols)
            # Columns share width equally so tiles grow with the
            # window (the PHOTO scales, the constant border does not).
            # No row stretch → many rows scroll (contact-sheet).
            # Tile *height* is pinned to width / modal-aspect by
            # _apply_tile_heights (QGridLayout won't do it itself).
            n_rows = (len(self._items) + cols - 1) // cols
            for c in range(cols):
                self._grid.setColumnStretch(c, 1)
            for c in range(cols, max(cols, self._grid.columnCount())):
                self._grid.setColumnStretch(c, 0)
            for r in range(n_rows):
                self._grid.setRowStretch(r, 0)
            self._apply_tile_heights()
        finally:
            self._laying_out = False

    def _clear_grid(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

    def _load_one(self, item: "GridItem", honest: bool) -> QPixmap:
        """Decode one tile's pixmap. Bulk grid → the cheap embedded
        thumb. Few-tile honest mode + RAW → the half-res sensor
        decode (real demosaiced pixels — the thumb is sharpened/NR'd
        and lies about fine sharpness, exactly what you compare when
        tie-breaking 2–4 frames). Decode failure → fall back to the
        thumb (degraded beats none; never crash).

        docs/24 Step 2c: clip items load from ``item.preview_path``
        (the cached JPEG written at Cull-create time by
        :func:`core.thumb_cache.ensure_thumb`). No RAW decode, no
        full-video frame extraction — the cached thumb is the
        already-decoded frame. When ``preview_path`` is missing or
        unreadable, return an empty pixmap (the tile shows a
        placeholder rather than crashing)."""
        if item.kind == "clip":
            if item.preview_path is None or not item.preview_path.is_file():
                return QPixmap()
            return _load_pixmap(item.preview_path)
        path = item.path
        if honest and path.suffix.lower() in _RAW_EXTENSIONS:
            img = _load_raw_half_res(path)
            if img is not None and not img.isNull():
                return QPixmap.fromImage(img)
        return _load_pixmap(path)

    def _load_some_thumbs(self) -> None:
        done = 0
        honest = self._honest()
        while self._pending and done < _THUMBS_PER_TICK:
            idx = self._pending.pop(0)
            tile = self._tiles.get(idx)
            it = next((i for i in self._items if i.index == idx), None)
            if tile is None or it is None:
                continue
            # docs/24 Step 2c: cache key includes kind so a photo
            # and a clip with the same path (the clip's source
            # video) don't collide.
            cache_path = (
                it.preview_path if it.kind == "clip"
                else it.path
            )
            key = (str(cache_path) if cache_path else "", it.kind, honest)
            pm = self._thumb_cache.get(key)
            if pm is None:
                try:
                    pm = self._load_one(it, honest)
                except Exception as exc:  # noqa: BLE001 — never crash
                    log.debug("grid thumb failed for %s: %s", key, exc)
                    pm = QPixmap()
                self._thumb_cache[key] = pm
            tile.set_pixmap(pm)
            done += 1
        # Settle the modal tile aspect as thumbs stream in (docs/18:
        # "computed from loaded thumbs"). Heights only — never the
        # column count, so this can't restart the relayout loop.
        self._recompute_aspect()
        if not self._pending:
            self._loader.stop()

    def _recompute_aspect(self) -> None:
        sizes: list[tuple[int, int]] = []
        honest = self._honest()
        for it in self._items:
            cache_path = (
                it.preview_path if it.kind == "clip"
                else it.path
            )
            pm = self._thumb_cache.get(
                (str(cache_path) if cache_path else "",
                 it.kind, honest)
            )
            if pm is not None and not pm.isNull():
                sizes.append((pm.width(), pm.height()))
        if not sizes:
            return
        new_a = grid_tile_aspect(sizes, self._fallback_aspect)
        if abs(new_a - self._tile_aspect) < 1e-3:
            return
        self._tile_aspect = new_a
        for tile in self._tiles.values():
            tile.set_aspect(new_a)
        self._apply_tile_heights()      # re-pin heights to new shape

    def resizeEvent(self, ev):  # noqa: N802
        super().resizeEvent(ev)
        self._relayout()
        # Width changed even when the column count didn't (_relayout
        # then early-returns) → re-pin tile heights to the new width.
        self._apply_tile_heights()
