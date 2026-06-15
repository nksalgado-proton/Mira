"""Unified base surfaces — TWO sibling bases (spec/42 two-base pivot, Nelson 2026-06-06).

Five media surfaces in Mira (Fast Cull / Cull Photo / Cull Video /
Process Photo / Process Video) compose into one of two base classes:

* :class:`BasePickSurface` — for Fast Cull / Cull Photo / Cull Video (Select
  uses the same surfaces in select_mode). TOOLS region sits BELOW the
  media. MediaHost border cycles cull state on click.
* :class:`BaseEditSurface` — for Process Photo / Process Video. TOOLS
  PANEL sits ABOVE the media (the AdjustmentSurface shape today, ~150 px).
  MediaHost border is status-only (green = exported / grey = not); click
  does nothing.

Both inherit :class:`_SurfaceCore` which owns the shared scaffolding:

    TOP_BAR     (~44 px)  info / back / help / CTA
    MEDIA       stretch=1 photo / video host with state border
    COMPACT_ROW (48 px)   timeline (video) or compact tools (photo)
    NAV         (~36 px)  Previous / centre / Next

plus the MediaHost state-border mechanism and the canonical factory
functions (BackButton / InfoLabel / KDPill / PrimaryAction / HelpButton /
FeatureToggle / StateChip). Every cross-surface affordance lives here —
NEVER in a derived surface (see spec/42 §"THE LOAD-BEARING RULE").

History: this file started as ``BaseSurface`` (one base for all six
surfaces). The two-base pivot landed 2026-06-06 because forcing Process
into the Cull mold was producing heroic compression. Same scaffolding,
two sibling layouts.
"""

from __future__ import annotations

import logging
from typing import NamedTuple, Optional

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger(__name__)


# ── Standard sizing constants (locked in spec/42 §4a) ────────────────────────

_REGION_MARGIN = (12, 6, 12, 6)
_COMPACT_ROW_MARGIN = (12, 4, 12, 4)
_REGION_SPACING = 8
_TIGHT_SPACING = 6              # COMPACT_ROW + TOOLS use slightly tighter spacing

_TOP_BAR_HINT_H = 44
_STATE_BAR_HINT_H = 40
_COMPACT_ROW_HINT_H = 48        # reserved on both bases (timeline on video,
                                # empty / compact tools on photo)
_TOOLS_HINT_H = 80              # BasePickSurface — ≤ 2 rows of ~36 px each
_PROCESS_TOOLS_PANEL_HINT_H = 232  # BaseEditSurface — tools panel above media.
                                   # Sized to the AdjustmentSurface content's natural
                                   # minimum (220 px after the 2026-06-09 compaction
                                   # pass — merged tone+strength grid, tighter group
                                   # margins, tighter row spacing) plus the region
                                   # card's 12 px internal padding. This is a FLOOR —
                                   # ``_make_v_region`` clamps the region's min at
                                   # exactly this value so the slider rows can never
                                   # overlap, while the max allows modest growth.
                                   # Was 280 (over the pre-compaction natural 282);
                                   # before that 200 (clipped the 6-slider grid).
_NAV_HINT_H = 36

# MediaHost state-border width (px). The QSS border at
# ``QWidget#MediaHost`` MUST match this value in BOTH theme files —
# the two are coupled because we set the media layout's margin
# explicitly. Why: Qt's QSS border on a plain QWidget does NOT
# propagate into the layout's contentsRect when a ``stretch=1``
# child fills the host (the small-region cards work because their
# children don't fill them; MediaHost's child does). Without the
# explicit margin, the child overlaps the border zone and the
# border paints invisibly under the child. So this constant is the
# *one* knob — bump it here, mirror it in QSS, and every media
# surface (cull/select/process, photo/video) gets the same state
# border.
_MEDIA_BORDER_W = 8


# ── _SurfaceCore — the shared scaffold ───────────────────────────────────────


class _SurfaceCore(QWidget):
    """Shared scaffolding for :class:`BasePickSurface` + :class:`BaseEditSurface`.

    Provides the canonical regions that exist on EVERY media surface:

    * ``top_bar``     — info / back / help / CTA  (~44 px)
    * ``_media_host`` — the photo / video host carrying the state border
    * ``compact_row`` — 48 px reserved; timeline on video, compact tools on photo
    * ``nav``         — ``← Previous`` · centre · ``Next →``  (~36 px)

    Plus the MediaHost state-border mechanism: :meth:`set_media_state` to
    push the colour (``"skipped" / "candidate" / "picked" / "mixed"``)
    and :attr:`media_border_clicked` to listen for border clicks.
    BasePickSurface uses the click-to-cycle behaviour; BaseEditSurface
    leaves the signal unconnected (border is status-only).

    Subclasses implement :meth:`_assemble_layout` to add their
    surface-specific regions (`state_bar`, `tools`, `tools_panel`) in the
    correct vertical order and wire up the outer QVBoxLayout.
    """

    # Emitted when the user clicks inside the MEDIA host's border zone
    # (outside the inner contents rect). Cull surfaces connect this to
    # their state-cycle handler (same path as Space). Process surfaces
    # leave it unconnected — their border is status-only.
    media_border_clicked = pyqtSignal()

    # Subclasses override with the names of THEIR regions (a tuple of
    # attribute names). Used by ``set_region_visible`` / ``region``.
    _REGION_NAMES: tuple[str, ...] = ()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName(type(self).__name__)
        # Qt's stylesheet engine only paints background + border on a
        # ``QWidget`` subclass when ``WA_StyledBackground`` is set.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Build the shared regions (the order they're created here is
        # NOT the layout order — subclasses choose the order when they
        # assemble).
        self.top_bar = self._make_h_region("TopBar", _TOP_BAR_HINT_H)
        self._media_host = self._make_media_host()
        self.compact_row = self._make_h_region(
            "CompactRow", _COMPACT_ROW_HINT_H,
            margin=_COMPACT_ROW_MARGIN, spacing=_TIGHT_SPACING)
        self.nav = self._make_h_region("Nav", _NAV_HINT_H)
        # Subclass adds its own regions + assembles the layout
        self._assemble_layout()
        # State-border bookkeeping. ``_media_host_in_border_zone`` tracks
        # cursor zone transitions so we only re-apply the cursor on
        # change (not every MouseMove pixel).
        self._media_host_in_border_zone: bool = False
        self._media_host.setMouseTracking(True)
        self._media_host.installEventFilter(self)

    # ── To be implemented by subclasses ──────────────────────────────────────

    def _assemble_layout(self) -> None:
        """Build any subclass-specific regions (e.g. `state_bar`,
        `tools`, `tools_panel`), then wire the outer ``QVBoxLayout`` in
        the correct vertical order."""
        raise NotImplementedError(
            f"{type(self).__name__} must override _assemble_layout()")

    # ── Shared region construction ───────────────────────────────────────────

    def _make_media_host(self) -> QWidget:
        """The MEDIA host — a styled QWidget with the state-coloured border.
        The layout's explicit ``_MEDIA_BORDER_W`` inset reserves the border
        zone so the ``stretch=1`` child doesn't overlap it (see the
        constant's docstring for why)."""
        host = QWidget()
        host.setObjectName("MediaHost")
        host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(
            _MEDIA_BORDER_W, _MEDIA_BORDER_W,
            _MEDIA_BORDER_W, _MEDIA_BORDER_W)
        layout.setSpacing(0)
        self._media_layout = layout
        self._media_widget: Optional[QWidget] = None
        return host

    @staticmethod
    def _make_h_region(
        name: str,
        hint_h: int,
        *,
        margin: tuple[int, int, int, int] = _REGION_MARGIN,
        spacing: int = _REGION_SPACING,
    ) -> QWidget:
        """Horizontal region container (TOP_BAR / STATE_BAR / COMPACT_ROW / NAV)."""
        w = QWidget()
        w.setObjectName(f"Surface{name}")
        # Needed for the region-card QSS (background + 1 px border + 6 px
        # radius) to actually paint on a plain ``QWidget``.
        w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        w.setMinimumHeight(hint_h)
        w.setMaximumHeight(hint_h * 2)        # allow modest growth
        lo = QHBoxLayout(w)
        lo.setContentsMargins(*margin)
        lo.setSpacing(spacing)
        return w

    @staticmethod
    def _make_v_region(name: str, hint_h: int) -> QWidget:
        """Vertical region container (TOOLS / TOOLS_PANEL — stacks horizontal rows).

        Pre-2026-06-09 this used ``min=hint_h//2``, ``max=hint_h`` — but
        the max cap clipped Process's tools panel (whose natural content
        is ~282 px) at 200 px. Now mirrors :meth:`_make_h_region`:
        ``min=hint_h`` (always reserve the budget), ``max=hint_h * 2``
        (allow modest growth for content that genuinely needs it)."""
        w = QWidget()
        w.setObjectName(f"Surface{name}")
        w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        w.setMinimumHeight(hint_h)
        w.setMaximumHeight(hint_h * 2)
        lo = QVBoxLayout(w)
        lo.setContentsMargins(*_REGION_MARGIN)
        lo.setSpacing(_TIGHT_SPACING)
        return w

    # ── Public API ───────────────────────────────────────────────────────────

    def set_media(self, widget: QWidget) -> None:
        """Install ``widget`` as the media display. Replaces any prior media.

        The widget is added to the MEDIA region with stretch=1; it fills
        the remaining vertical space between the top regions and the
        bottom regions."""
        if self._media_widget is not None:
            self._media_layout.removeWidget(self._media_widget)
            self._media_widget.setParent(None)
        self._media_widget = widget
        self._media_layout.addWidget(widget, stretch=1)

    def set_region_visible(self, name: str, visible: bool) -> None:
        """Show/hide a region by name. Most regions collapse to 0 px when
        hidden — but ``compact_row`` is special: it's always RESERVED at
        ``_COMPACT_ROW_HINT_H`` (48 px) on every surface, and "hiding" it
        toggles its CHILDREN's visibility instead of the region itself.
        This is the geometry-stability reservation pattern (spec/42 §"Region
        reservation"): the media area stays anchored across photo ↔ video
        crossings even though one populates the timeline and the other
        leaves it empty.

        For all other regions (``state_bar``, ``tools``, ``tools_panel``,
        etc.), hidden = ``setVisible(False)`` and the region collapses.

        ``name`` must be one of this surface's regions (see
        ``_REGION_NAMES``). The ``media`` region is always visible."""
        if name not in self._REGION_NAMES:
            raise ValueError(
                f"unknown region {name!r} (expected one of "
                f"{self._REGION_NAMES})")
        region = getattr(self, name)
        if name == "compact_row":
            # Reservation pattern: keep the region at 48 px, toggle children.
            # The QSS rule for #SurfaceCompactRow has no border/background, so
            # an empty compact_row is visually invisible — the 48 px costs
            # nothing visually but anchors the MEDIA position.
            layout = region.layout()
            for i in range(layout.count()):
                item = layout.itemAt(i)
                if item is not None and item.widget() is not None:
                    item.widget().setVisible(bool(visible))
        else:
            region.setVisible(bool(visible))

    def region(self, name: str) -> QWidget:
        """Return the region container widget by name."""
        if name not in self._REGION_NAMES:
            raise ValueError(
                f"unknown region {name!r} (expected one of "
                f"{self._REGION_NAMES})")
        return getattr(self, name)

    def set_media_state(self, state: str) -> None:
        """Push ``state`` to the MEDIA host so its QSS border colour reflects it.

        BasePickSurface uses ``"skipped" / "candidate" / "picked"`` (photo) or
        ``"skipped" / "picked" / "mixed"`` (video). BaseEditSurface uses
        ``"picked" / "skipped"`` (or pass ``None`` for neutral grey).

        Re-polishes the host so the ``QWidget#MediaHost[state="…"]`` QSS
        selector re-evaluates."""
        self._media_host.setProperty("state", state)
        self._media_host.style().unpolish(self._media_host)
        self._media_host.style().polish(self._media_host)

    # ── Border-zone interactions (shared) ────────────────────────────────────

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        """Border-zone interactions on the MEDIA host:

        * **MouseButtonPress (Left) in border zone** — emit
          :attr:`media_border_clicked`. Cull surfaces route that to a
          cull-state-cycle handler (same as Space). Process surfaces
          leave it unconnected.
        * **MouseMove** — flip cursor to PointingHand inside the border,
          default cursor inside the content rect. Tracks zone
          transitions so we only call ``setCursor`` on change.
        * **Leave** — reset the cursor if we exit the host while still
          in the border zone.
        """
        if obj is self._media_host:
            etype = event.type()
            if (etype == QEvent.Type.MouseButtonPress
                    and event.button() == Qt.MouseButton.LeftButton):
                pos = event.position().toPoint()
                if self._pos_in_border_zone(pos):
                    self.media_border_clicked.emit()
                    event.accept()
                    return True
            elif etype == QEvent.Type.MouseMove:
                pos = event.position().toPoint()
                in_border = self._pos_in_border_zone(pos)
                if in_border != self._media_host_in_border_zone:
                    self._media_host_in_border_zone = in_border
                    if in_border:
                        self._media_host.setCursor(
                            QCursor(Qt.CursorShape.PointingHandCursor))
                    else:
                        self._media_host.unsetCursor()
            elif etype == QEvent.Type.Leave:
                if self._media_host_in_border_zone:
                    self._media_host_in_border_zone = False
                    self._media_host.unsetCursor()
        return super().eventFilter(obj, event)

    def _pos_in_border_zone(self, pos) -> bool:
        """True if ``pos`` (in MediaHost coordinates) lies in the
        ``_MEDIA_BORDER_W``-wide ring at the edge of the host. We do
        the math manually rather than relying on ``contentsRect()``
        because Qt's QSS-border-to-contents-rect propagation on a
        plain QWidget is unreliable when a stretch=1 child fills the
        host."""
        r = self._media_host.rect()
        inner = r.adjusted(
            _MEDIA_BORDER_W, _MEDIA_BORDER_W,
            -_MEDIA_BORDER_W, -_MEDIA_BORDER_W)
        return r.contains(pos) and not inner.contains(pos)


# ── BasePickSurface — TOOLS below MEDIA, 3-state border cycle ────────────────


class BasePickSurface(_SurfaceCore):
    """The Cull-family base surface (Fast Cull / Cull Photo / Cull Video).

    Layout (top to bottom)::

        ┌─ TOP_BAR   (~44 px)                                 ┐
        ├─ STATE_BAR (~40 px, hidden by default — state lives ┤
        │            on the MediaHost border)                  │
        ├─ MEDIA     (stretch=1) with state border             │
        ├─ COMPACT_ROW (48 px) — timeline on video, empty on  ┤
        │              photo (currently collapses; reservation │
        │              pattern lands when needed)              │
        ├─ TOOLS     (≤ 2 rows of ~36 px) — view-aware in     ┤
        │            CullPhoto, the action line in VideoCull   │
        └─ NAV       (~36 px) ← Previous · centre · Next →    ┘

    The MediaHost border cycles cull state on click. Photo surfaces
    cycle ``discard → keep → candidate``; video surfaces cycle
    ``discard → keep → mixed`` (spec/42 §"Video MediaHost is a 3-state
    cycle").

    Surfaces compose by:
      1) Populating regions via ``self.<region>.layout()``
      2) Hiding regions they don't use via ``set_region_visible(name, False)``
      3) Installing media via ``set_media(widget)``
      4) Connecting ``media_border_clicked`` to a state-cycle handler
    """

    _REGION_NAMES = ("top_bar", "state_bar", "compact_row", "tools", "nav")

    def _assemble_layout(self) -> None:
        # Cull-specific regions
        self.state_bar = self._make_h_region("StateBar", _STATE_BAR_HINT_H)
        self.tools = self._make_v_region("Tools", _TOOLS_HINT_H)
        # Outer layout: TOP_BAR · STATE_BAR · MEDIA · COMPACT_ROW · TOOLS · NAV
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)
        outer.addWidget(self.top_bar)
        outer.addWidget(self.state_bar)
        outer.addWidget(self._media_host, stretch=1)
        outer.addWidget(self.compact_row)
        outer.addWidget(self.tools)
        outer.addWidget(self.nav)


# ── BaseEditSurface — TOOLS PANEL above MEDIA, status-only border ─────────


class BaseEditSurface(_SurfaceCore):
    """The Process-family base surface (Process Photo / Process Video).

    Layout (top to bottom)::

        ┌─ TOP_BAR     (~44 px) Back · [progress] · Export → · ?  ┐
        ├─ TOOLS_PANEL (~150-200 px, fixed) — the adjustment      ┤
        │              groups + action row, ABOVE the media       │
        ├─ MEDIA       (stretch=1) with state border               │
        ├─ COMPACT_ROW (48 px) — timeline on video, empty on photo┤
        └─ NAV         (~36 px) ← Previous · centre · Next →     ┘

    The MediaHost border is **status-only** — green when
    ``Adjustment.edit_exported == True``, neutral grey otherwise.
    **Click does nothing** (no K/D to cycle at Process). Surfaces
    should call ``set_media_state("picked")`` when exported,
    ``set_media_state(None)`` otherwise.

    The TOOLS_PANEL holds the adjustment chrome (Tone / Vibrance /
    Crop / action row). It's a fixed-height container above MEDIA
    — the "AdjustmentSurface shape" that ProcessPhoto used before
    the unification.

    Surfaces compose by:
      1) Populating ``self.tools_panel.layout()`` with the adjustment
         groups
      2) Installing media via ``set_media(widget)``
      3) Populating ``self.compact_row`` with the timeline (video only)
      4) Populating ``self.nav`` via ``populate_nav_row``
      5) Driving the border via ``set_media_state(state)`` — no
         ``media_border_clicked`` connection (status-only)
    """

    _REGION_NAMES = ("top_bar", "tools_panel", "compact_row", "nav")

    def _assemble_layout(self) -> None:
        # Process-specific region: the tools panel above the media.
        self.tools_panel = self._make_v_region(
            "ToolsPanel", _PROCESS_TOOLS_PANEL_HINT_H)
        # Outer layout: TOP_BAR · TOOLS_PANEL · MEDIA · COMPACT_ROW · NAV
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)
        outer.addWidget(self.top_bar)
        outer.addWidget(self.tools_panel)
        outer.addWidget(self._media_host, stretch=1)
        outer.addWidget(self.compact_row)
        outer.addWidget(self.nav)


# ── Canonical affordances ────────────────────────────────────────────────────


def _btn(text: str, *, object_name: str, checkable: bool = False) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName(object_name)
    b.setCheckable(checkable)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
    return b


_PLAY_GLYPH = "▶"
_PAUSE_GLYPH = "⏸"


def transport_button(tooltip: str = "") -> QPushButton:
    """The ONE Play/Pause transport button used by every video
    surface (Nelson 2026-06-12 UI round).

    Two bugs the surfaces all shared and this factory closes:

    1. **Non-house colour.** Some surfaces wore the accent
       ``#Primary`` role on Play (always coloured) or the
       ``#FeatureToggle`` role (coloured when checked) — both painted
       the transport as if it were a CTA. The transport is a chrome
       control; it gets the plain button look.
    2. **The width-dance.** Toggling the LABEL ``▶ Play`` ⇄
       ``⏸ Pause`` (Pause is wider) made every sibling on the row
       jump on each tick. Icon-only ▶ / ⏸ pair on a fixed width
       eliminates the dance — the symbols carry the meaning, the
       tooltip carries the binding (e.g. ``"Play / pause  (Space)"``).
    """
    b = _btn(_PLAY_GLYPH, object_name="TransportButton")
    # Wide enough for either glyph + a comfortable hit target; the
    # exact value was eyeballed against the Picker and workshop
    # transports. setFixedWidth means a label swap can never push
    # neighbouring buttons (and can never shrink in a tight row).
    b.setFixedWidth(40)
    if tooltip:
        b.setToolTip(tooltip)
    return b


def set_transport_playing(btn: QPushButton, playing: bool) -> None:
    """Flip a :func:`transport_button` between its two states. The
    glyph is the only visible change — width stays pinned."""
    btn.setText(_PAUSE_GLYPH if playing else _PLAY_GLYPH)


def back_button(text: str = "Back") -> QPushButton:
    """The ONE canonical back button. Plain ``Back`` everywhere.

    Two audits: the first standardised to ``← Back`` (one glyph instead
    of five). Nelson 2026-06-12: drop the glyph too — ``Back`` in the
    standard button style, uniform across every surface. Callers
    should pass no text; the few that historically passed verbose
    variants ("← Library", "Back to days") now degrade to plain
    ``Back`` — the surface heading next to the button already names
    where you're returning to, so the label doesn't need to."""
    return _btn(text, object_name="BackButton")


def info_label(text: str = "") -> QLabel:
    """Dynamic info label — ``CullBucketInfo``-style. ``#InfoLabel`` role.

    Use for "Day 2 · Bucket 4 of 12" or "Clip 3/8 · cabaceira.mp4" etc."""
    lbl = QLabel(text)
    lbl.setObjectName("InfoLabel")
    return lbl


def kd_pill(checked: bool = False) -> QPushButton:
    """The ONE canonical K/D pill. ``#KDPill`` role.

    Replaces ``CullStateBar`` (FastPicker) + ``BinaryStatePill`` (VideoCull
    binary mode) + the unnamed pill inside CullPhoto's state strip with a
    single role. Always checkable; ``checked`` is the *initial* check state.
    Text + visual state are surface-managed."""
    b = _btn("", object_name="PDPill", checkable=True)
    b.setChecked(bool(checked))
    return b


def primary_action(text: str) -> QPushButton:
    """The ONE primary CTA. ``#PrimaryAction`` role.

    Use for Save / Export / Keep adjustments — whatever the surface's main
    commit verb is. Always sits at the right end of TOP_BAR (or the right
    end of NAV / TOOLS' action row when the surface needs a per-row CTA)."""
    return _btn(text, object_name="PrimaryAction")


def help_button() -> QPushButton:
    """The ONE help button. ``#HelpButton`` role. ``?`` glyph.

    Always sits at the right end of TOP_BAR (after the primary CTA)."""
    return _btn("?", object_name="HelpButton")


def feature_toggle(text: str, *, checked: bool = False) -> QPushButton:
    """The ONE feature-toggle pill. ``#FeatureToggle`` role.

    Use for Zoom / AF / Peaking / Compare / Preview / AUTO / Crop — any
    surface-specific affordance that's an on/off toggle."""
    b = _btn(text, object_name="FeatureToggle", checkable=True)
    b.setChecked(bool(checked))
    return b


def state_chip(text: str = "") -> QLabel:
    """The ONE state chip. ``#StateChip`` role.

    Use for "✓ Exported" / "✓ Adjusted" — the status badges on Process
    surfaces. Was previously ``#ProcessExportedChip``; unified now."""
    lbl = QLabel(text)
    lbl.setObjectName("StateChip")
    return lbl


# ── Standard nav-row builder ─────────────────────────────────────────────────


class NavRow(NamedTuple):
    """The four standard nav buttons returned by :func:`populate_nav_row`.

    Surfaces wire signals on these; the buttons themselves are owned by the
    base's NAV region (added to its layout)."""
    prev_bucket: Optional[QPushButton]
    prev: QPushButton
    next: QPushButton
    next_bucket: Optional[QPushButton]


def populate_nav_row(
    surface: _SurfaceCore,
    *,
    with_buckets: bool = True,
    centre_widget: Optional[QWidget] = None,
) -> NavRow:
    """Populate the NAV region with the standard nav row shape.

    Layout::

        ⏮ Bucket  ← Previous  [stretch · centre_widget · stretch]  Next →  Bucket ⏭

    When ``with_buckets=False``, the edge-bucket buttons are omitted (this is
    the canonical case — bucket-level nav was retired when the Day Grid took
    over that level; ``with_buckets=True`` is vestigial, left in only for
    surfaces that haven't migrated yet).

    ``centre_widget`` is dropped in the centre stretch slot — for the
    Grid-toggle button on PickPhotoSurface, the Fullscreen button on
    QuickSweepPage, or the video transport on PickerPage / EditVideoPage.
    Use ``None`` for a plain stretch.

    Returns the four buttons (with ``prev_bucket`` and ``next_bucket`` as
    ``None`` when ``with_buckets=False``) so the surface can wire signals."""
    lo = surface.nav.layout()
    pb: Optional[QPushButton] = None
    nb: Optional[QPushButton] = None
    if with_buckets:
        pb = _btn("⏮ Bucket", object_name="NavEdgeButton")
        lo.addWidget(pb)
    pv = _btn("← Previous", object_name="NavStepButton")
    lo.addWidget(pv)
    lo.addStretch(1)
    if centre_widget is not None:
        lo.addWidget(centre_widget)
    lo.addStretch(1)
    nx = _btn("Next →", object_name="NavStepButton")
    lo.addWidget(nx)
    if with_buckets:
        nb = _btn("Bucket ⏭", object_name="NavEdgeButton")
        lo.addWidget(nb)
    return NavRow(prev_bucket=pb, prev=pv, next=nx, next_bucket=nb)


__all__ = (
    "BasePickSurface",
    "BaseEditSurface",
    "NavRow",
    "back_button",
    "feature_toggle",
    "help_button",
    "info_label",
    "kd_pill",
    "populate_nav_row",
    "primary_action",
    "set_transport_playing",
    "state_chip",
    "transport_button",
)
