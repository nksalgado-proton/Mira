"""AdjustmentSurface — the reusable per-image Look/crop editor.

Rebuilt for the spec/54 Looks redesign (2026-06-10): the slider era
(six tone sliders + Strength + AUTO toggle + Vibrance + Copy/Paste)
is gone. The tone story is three CHOICES, zero sliders (spec/54 §4):

* **Style** — what is this photo? (dropdown, classifier-seeded)
* **Look** — how should it feel? Original / Natural / Brighter /
  Deeper as a segmented row, plus the **grid moment**
  (:class:`~mira.ui.edited.look_grid.LookGridDialog` — a 2×2
  of THIS photo, every tile clickable).
* **Filter** — what should it become? (spec/54 §8 — the chooser slot
  exists but stays hidden until the Mira filter set lands.)

This is still the editing **core** the photo Edit page and the video
sub-surface both compose: the :class:`~ui.media.PhotoViewport` (the
ONE display engine — spec/63 §6.1 swapped the MediaCanvas out) + a
:class:`~ui.culler.crop_overlay.CropOverlay`, the CROP box (aspect +
box rotation), and the action row's **Compare / Preview / Reset**.
The render pipeline is the A-routed engine: the surface caches the
photo's Natural (:func:`core.photo_auto.compute_auto_params`, routed)
once per load and compiles the chosen Look via
:func:`core.photo_auto.look_params_from_natural` →
:func:`core.photo_render.apply_params`.

It is deliberately **single-image and persistence-free**: it holds
the state for ONE loaded image (full-res array + the current look /
style / crop / box-angle / aspect), renders it, and emits
:attr:`changed` with a *kind* tag ("look" / "style" / "crop" /
"angle" / "aspect" / "rotation" / "reset"). The host owns
persistence, navigation and export — it pushes saved state in via
:meth:`set_state`, reads it back via :meth:`get_state` (a
choice-shaped :class:`SurfaceState`), and persists on
:attr:`changed`. The surface knows nothing about files, paths or
buckets.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import NamedTuple, Optional

import numpy as np
from PIL import Image
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor, QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.aspect_ratio import get_aspect_ratio, transpose_label
from core.photo_auto import (
    FILTER_STRENGTH_DEFAULT,
    available_filters,
    available_looks,
    compute_auto_params,
    creative_filter_amount,
    filter_strength_scale,
    look_params_from_natural,
    resolve_filter_recipe,
)
from core.photo_render import (
    FilterRecipe,
    Params,
    apply_crop_norm,
    apply_filter,
    apply_params,
    apply_rotation,
    compute_default_crop,
    extract_rotated_crop,
)
from mira.ui.base.aspect_ratio_combo import AspectRatioCombo
from mira.ui.edited.crop_overlay import CropOverlay
from mira.ui.i18n import tr
from mira.ui.edited.look_grid import (
    LookGridDialog,
    filter_display_name,
    look_display_name,
)

log = logging.getLogger(__name__)


# Preview-size cap for chooser responsiveness (a 25 MP RAW render in
# float32 is ~3 s; downsampled to 1280-px-wide it's ~50 ms, interactive).
# Full-res is reserved for the Preview toggle (F10) + export.
PREVIEW_MAX_WIDTH = 1280
# spec/115 §1 — debounce timer for the keyboard / field / programmatic
# paths (any change that DIDN'T end on a ``sliderReleased`` so the
# release path can't render for it). Raised from 40 → 150 ms: 40 ms
# meant "blinked", so a held arrow key triggered repeated blocking
# whole-frame tone renders; 150 ms means "settled". Drag renders are
# render-on-release, not debounced — see :meth:`_install_drag_gating`.
RENDER_DEBOUNCE_MS = 150

# The genres the A-router is calibrated for (core.photo_looks_data.ROUTER
# + the legacy fallback). Changing it re-routes the Natural correction.
_STYLES = (
    "general", "portrait", "macro", "wildlife",
    "landscape", "selfie", "night_long_exposure",
)


def normalize_style(classification: Optional[str]) -> str:
    """Coerce a per-item classification (``item.classification``) into a
    valid AUTO style. Returns ``"general"`` for ``None``, empty, or
    classifications outside the AUTO-calibrated set (``sports`` etc.).
    Other classifications pass through unchanged."""
    if not classification:
        return "general"
    return classification if classification in _STYLES else "general"


# spec/58 §2 — the Style combo's classification badge. The stored
# classifier score maps onto DISCRETE QSS bands (a continuous ramp would
# need inline styles — banned by the QSS rule); ``human`` sits outside
# the ramp. v0 thresholds are deliberately bold, pending Nelson's live
# eyeball calibration (spec/58 §5.1).
CONFIDENCE_MID_FROM = 0.55    # below → "low" (red)
CONFIDENCE_HIGH_FROM = 0.80   # below → "mid" (amber); at/above → "high"


def classification_band(
    source: Optional[str], confidence: Optional[float],
) -> str:
    """Map an item's stored classification onto the Style combo's QSS
    band: ``human`` (the user decided — outside the ramp) or ``low`` /
    ``mid`` / ``high`` on the red→green ramp. No confidence — never
    classified, or a pre-confidence row — reads as ``low``: red means
    "needs your eye"."""
    if source == "user":
        return "human"
    if confidence is None or confidence < CONFIDENCE_MID_FROM:
        return "low"
    if confidence < CONFIDENCE_HIGH_FROM:
        return "mid"
    return "high"


class SurfaceState(NamedTuple):
    """The editable state of the loaded image (what :meth:`get_state`
    returns / :meth:`set_state` accepts the pieces of) — the spec/54
    CHOICE shape, mirroring the ``Adjustment`` row's tone payload."""
    look: str
    crop_norm: Optional[tuple[float, float, float, float]]
    box_angle: float
    style: str
    aspect_label: str
    creative_filter: Optional[str] = None     # spec/54 §8 — slot only
    # Nelson 2026-06-13 Look Strength slider — 0..2 multiplier on the
    # final Look Params (engine seam: .scaled(s)). 1.0 = the Look as
    # authored, 0.0 = identity, 2.0 = exaggerated. Default 1.0 keeps
    # legacy hosts that don't pass a value rendering identically.
    look_strength: float = 1.0
    # spec/115 §2 — independent user exposure (EV) added to
    # ``Params.exposure`` AFTER the Look's strength scaling. Default
    # 0.0 = no nudge. Range −2..+2 stops, clamped on read.
    user_exposure: float = 0.0
    # spec/156 — per-image creative-filter STRENGTH (−2..+2). Scales the
    # filter's blend amount: +2 = the shipped recipe, 0 (default) ≈ 70 %,
    # −2 ≈ 40 %. Inert when ``creative_filter`` is None.
    filter_strength: float = 0.0


class AdjustmentSurface(QWidget):
    """Reusable per-image colour/crop editor. See module docstring."""

    # Emitted on any user edit, tagged with the *kind* so the host can
    # persist appropriately (immediate for crop/angle/aspect/reset/paste;
    # debounced for tone/style). Never emitted while loading state in.
    changed = pyqtSignal(str)

    # spec/58 §2 — the user explicitly picked a style from the dropdown,
    # INCLUDING re-picking the one already shown: choosing IS the human
    # decision. Hosts persist ``classification_source='user'`` on the
    # right item row. Never fires programmatically (``activated``-backed).
    style_decided = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AdjustmentSurface")

        # ── Single-image state ───────────────────────────────────────
        self._preview_array: Optional[np.ndarray] = None   # downsampled
        self._full_array: Optional[np.ndarray] = None       # full-res
        # The A-routed Natural correction for the loaded image —
        # computed once per load; every Look compiles from it.
        self._natural_params = Params()
        # Baseline Look is Original = identity, no correction (Nelson
        # 2026-06-18); Natural is a deliberate Look choice, not the default.
        self._look = "original"
        self._creative_filter: Optional[str] = None        # spec/54 §8 slot
        self._aspect_label = "Original"
        self._crop_norm: Optional[tuple[float, float, float, float]] = None
        self._box_angle = 0.0
        # 90° image rotation — 0 / 90 / 180 / 270, clockwise (mirrors
        # ``Adjustment.rotation``). Distinct from ``_box_angle`` (which
        # spins the crop overlay only). Applied FIRST in :meth:`render_now`
        # so the crop rect — normalised against the displayed/rotated
        # frame — lands correctly (docs/25 §12).
        self._rotation: int = 0
        self._style = "general"
        # Nelson 2026-06-13 Look Strength slider — multiplier on the
        # final Look Params. The slider lives under the Look group.
        self._look_strength = 1.0
        # spec/115 §2 — independent user exposure (EV). Added to the
        # resolved ``Params.exposure`` after Strength scales the Look,
        # so a user nudge sits on top of (rather than scaling with)
        # the Look. 0.0 = no nudge, ±2 EV swing covers a one-stop
        # recovery in either direction.
        self._user_exposure = 0.0
        # spec/156 — per-image creative-filter STRENGTH (−2..+2 step). The
        # dropdown lives under the filter combo in the Filter group.
        self._filter_strength = FILTER_STRENGTH_DEFAULT
        # spec/116 §2 — the photo's AF point (the Subject Spotlight's
        # subject anchor) in normalised image coords. ``None`` falls
        # back to the frame centre at render time. The host pushes it
        # via :meth:`set_af_point` when the item lands.
        self._af_point: Optional[tuple[float, float]] = None
        self._comparing = False
        self._preview_full = False
        # Guard: while loading state into the widgets, suppress the
        # render/persist signal storm (the value setters would otherwise
        # each fire ``changed``).
        self._loading = False
        # spec/115 §1 — render-on-release state machine. ``_dragging``
        # is True between ``sliderPressed`` and ``sliderReleased`` on
        # ANY direct slider (Strength, Exposure) or any tone-grid
        # slider: while dragging, ``valueChanged`` updates the numeric
        # label only — no render. The release path drives
        # :meth:`render_now`; the debounce timer catches keyboard /
        # field / programmatic changes (no release fires for those).
        self._dragging = False
        # spec/115 §1 — early-out for ``render_now``: skip the whole-
        # frame tone math when the resolved Params + crop + comparing
        # state would produce the same output as the last render. Keeps
        # the post-release debounce render (the one that fires when the
        # 150 ms timer expires after a release already rendered) cheap.
        self._last_rendered_signature: Optional[tuple] = None

        self._crop_overlay: Optional[CropOverlay] = None
        self._build_ui()

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(RENDER_DEBOUNCE_MS)
        # spec/115 §1 — the timer fires the render AND a ``changed``
        # emit so the host persists keyboard / field changes too. The
        # bare ``render_now`` wiring it carried before never persisted
        # those because it skipped the surface's own commit path.
        self._render_timer.timeout.connect(self._on_render_timer)

        photo_host = self._display.photo_area_widget()
        self._crop_overlay = CropOverlay(photo_host)
        self._crop_overlay.setVisible(False)
        self._crop_overlay.set_aspect_ratio(self._aspect_label)
        self._crop_overlay.rect_changed.connect(self._on_crop_rect_changed)
        self._crop_overlay.angle_changed.connect(self._on_overlay_angle)
        self._display.photo_geometry_changed.connect(
            self._sync_crop_overlay_geometry)

        # spec/134 — the configurable viewer overlay (When / Where /
        # Camera / Exposure). Picker + Editor share one widget so the
        # pill reads the same on both surfaces. A single-line strip pinned
        # to the bottom edge of the displayed image (not the view), via the
        # viewport's letterbox rect + ``photo_geometry_changed`` pulse —
        # the same anchoring PickerPage and the crop overlay use. The host
        # pushes content via :meth:`set_viewer_overlay_html`.
        from mira.ui.media.photo_overlay import PhotoExposureOverlay
        self._viewer_overlay = PhotoExposureOverlay(
            photo_host,
            rect_provider=self._display.image_rect_in_photo_area,
        )
        self._display.photo_geometry_changed.connect(
            self._viewer_overlay.reposition)

    # ── Construction ─────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # spec/42 (Nelson 2026-06-06): AdjustmentSurface is no longer a
        # self-displaying widget — it's a state-holder that exposes its
        # display widget + tools widget for re-parenting into a
        # ``BaseEditSurface``'s ``media`` + ``tools_panel`` regions.
        # The host (the spec/70 EditorPage owns BOTH photo and video
        # Edit-phase items) calls :meth:`display_widget` and
        # :meth:`tools_widget` and reparents them; AdjustmentSurface
        # itself paints nothing.
        #
        # spec/63 §6.1 (6b, 2026-06-12): the display is THE PhotoViewport
        # — the one engine. The surface pushes its developed renders via
        # ``set_rendered_pixmap``; browse pixels, the locked key map,
        # posters and prefetch are the viewport's own. The viewport's
        # built-in F10 lens is disabled here (the HOST owns truth on
        # editing surfaces: Edit's F10 = the developed full-res preview)
        # and the corner 🔍 is hidden (the nav-row "Full Resolution"
        # button is the affordance).
        from mira.ui.media.photo_viewport import PhotoViewport
        self._display = PhotoViewport(parent=self)
        self._display.set_truth_internal(False)
        self._display.set_corner_inspect_visible(False)
        self._display.set_lens_tools_visible(False)
        self._tools_widget = self._build_tools()
        self._tools_widget.setParent(self)

    def display_widget(self) -> QWidget:
        """The PhotoViewport this surface renders into. Owned by
        AdjustmentSurface but typically reparented into a
        ``BaseEditSurface``'s media region by the host."""
        return self._display

    def canvas(self) -> QWidget:
        """Legacy name for :meth:`display_widget` (the MediaCanvas era —
        spec/63 §6.1 swapped the display engine; the reparenting
        contract and the watermark/preview push APIs survived)."""
        return self._display

    def tools_widget(self) -> QWidget:
        """The tools panel (Tone / Vibrance / Crop / action row). Typically
        reparented into a ``BaseEditSurface``'s ``tools_panel`` region by
        the host."""
        return self._tools_widget

    def set_viewer_overlay_html(self, html: str) -> None:
        """spec/134 — push the composed viewer-overlay HTML to the pill.
        Empty string hides it. The Editor's host (EditorPage) calls
        this on every item landing with the output of
        :func:`mira.ui.media.viewer_overlay.compose_viewer_overlay_html`,
        so Picker + Editor paint identical text."""
        self._viewer_overlay.set_html(html or "")

    def _build_tools(self) -> QWidget:
        host = QWidget()
        host.setObjectName("ProcessToolsHost")
        v = QVBoxLayout(host)
        v.setContentsMargins(10, 6, 10, 6)
        v.setSpacing(6)
        # Top reorganization (Nelson 2026-06-11; revised 2026-06-20). TWO
        # lines. Line 1 = the three SELF-TITLED boxes Look · Style · Filter
        # (the redundant outer "Style, Look & Filter" wrapper was dropped —
        # the boxes already name themselves). Line 2 = Crop on the left + the
        # view-control buttons (Toggle Adjustments / Toggle Crop / Reset all)
        # folded onto the SAME line on the right, reclaiming a whole row. The
        # video-only Audio / Vibrations slots ride between them (hosts fill
        # them via :meth:`set_video_extra_boxes`; empty otherwise).
        line1 = QHBoxLayout()
        line1.setSpacing(10)
        line1.addWidget(self._build_look_group(), stretch=3)
        line1.addWidget(self._build_style_group(), stretch=1)
        line1.addWidget(self._build_filter_group(), stretch=1)
        v.addLayout(line1)

        line2 = QHBoxLayout()
        line2.setSpacing(10)
        line2.addWidget(self._build_crop_group())
        line2.addWidget(self._build_image_rotate_group())
        self._audio_slot = QVBoxLayout()
        self._audio_slot.setContentsMargins(0, 0, 0, 0)
        self._vibrations_slot = QVBoxLayout()
        self._vibrations_slot.setContentsMargins(0, 0, 0, 0)
        line2.addLayout(self._audio_slot)
        line2.addLayout(self._vibrations_slot)
        line2.addStretch(1)
        self._add_action_buttons(line2)
        v.addLayout(line2)
        return host

    def set_video_extra_boxes(
        self, audio: QWidget, vibrations: QWidget,
    ) -> None:
        """Place the video host's Audio / Vibrations boxes into line 2,
        under Style and Filter (Nelson 2026-06-11 — the top grid).
        Photo hosts never call this; the empty slots still claim their
        column widths, so Crop keeps the Look box's width."""
        self._audio_slot.addWidget(audio)
        self._vibrations_slot.addWidget(vibrations)

    def _build_look_group(self) -> QWidget:
        """The LOOK box (spec/54 §4) — the segmented chooser + the grid
        moment. Zero sliders. One choice per titled box (Nelson
        2026-06-10 — the form grammar; Style and Filter live in their
        own boxes now, no label-beside-input)."""
        box, col = self._group(tr("Look"))

        # Segmented chooser (Original / Natural / Brighter / Deeper) +
        # the grid moment. Checkable buttons reuse the FeatureToggle
        # role; exclusivity is hand-managed so loading state in can set
        # any button without firing the others.
        look_row = QHBoxLayout()
        look_row.setSpacing(4)
        self._look_buttons: dict[str, QPushButton] = {}
        for key in available_looks():
            b = self._toggle(look_display_name(key))
            b.setToolTip(tr(
                "Show this photo with the {name} look. The choice is "
                "stored and only baked into the exported file when you "
                "click Export.").replace("{name}", look_display_name(key)))
            b.clicked.connect(lambda _=False, k=key: self._on_look_clicked(k))
            self._look_buttons[key] = b
            look_row.addWidget(b, stretch=1)
        self._grid_btn = self._btn(tr("Grid"))
        self._grid_btn.setToolTip(tr(
            "See this photo under all four Looks side by side and click "
            "the one you like. ( G )"))
        self._grid_btn.clicked.connect(self.open_look_grid)
        look_row.addWidget(self._grid_btn)
        col.addLayout(look_row)
        self._sync_look_buttons()

        # ── Strength + Exposure dropdowns (spec/157) ─────────────────
        # Replaced the continuous sliders (Nelson 2026-06-27) with two
        # side-by-side −5..+5 graduations (0 = default), matching the
        # filter-strength dropdown idiom but at higher resolution (11
        # steps). STRENGTH scales the chosen Look across 0..2 (−5 →
        # identity, 0 → the Look as authored, +5 → 2×); EXPOSURE is an
        # independent EV nudge across a deliberately small ±0.4 EV (−5 →
        # −0.4 EV, 0 → none, +5 → +0.4 EV — the full ±2 read far too
        # strong; this keeps the swing subtle while still well inside the
        # column's ±2 cap). The underlying value range + render path are
        # unchanged — only the control is. A combo pick is a settled
        # change, so it renders immediately (no slider drag-debounce).
        # Labels sit INLINE beside their combos so the whole pair fits one
        # row (the slider era stacked two full rows here; spec/157 reclaims
        # that vertical budget — the top group boxes shrink accordingly).
        tone_row = QHBoxLayout()
        tone_row.setSpacing(6)
        self._strength_label = QLabel(tr("Strength"))
        self._strength_label.setObjectName("LookStrengthLabel")
        tone_row.addWidget(self._strength_label)
        self._strength_combo = self._build_graduation_combo(
            object_name="LookStrengthCombo",
            values=[round(1.0 + step * 0.2, 4) for step in range(-5, 6)],
            tooltip=tr(
                "How much of the Look to apply. 0 = the Look as designed; "
                "+5 doubles it; −5 leaves the photo untouched."),
            on_changed=self._on_strength_changed)
        tone_row.addWidget(self._strength_combo, stretch=1)
        tone_row.addSpacing(10)
        self._exposure_label = QLabel(tr("Exposure"))
        self._exposure_label.setObjectName("UserExposureLabel")
        tone_row.addWidget(self._exposure_label)
        self._exposure_combo = self._build_graduation_combo(
            object_name="UserExposureCombo",
            values=[round(step * 0.08, 4) for step in range(-5, 6)],
            tooltip=tr(
                "Per-image exposure nudge. 0 = none; +5 ≈ +0.4 EV, −5 ≈ "
                "−0.4 EV. Independent of the Look and Strength."),
            on_changed=self._on_exposure_changed)
        tone_row.addWidget(self._exposure_combo, stretch=1)

        col.addLayout(tone_row)
        self._sync_strength_widgets()
        self._sync_exposure_widgets()

        col.addStretch(1)

        box.setMinimumHeight(box.minimumSizeHint().height())
        return box

    def _build_graduation_combo(self, *, object_name, values, tooltip,
                                on_changed):
        """spec/157 — build a −5..+5 graduation dropdown. Each item's DATA
        is the underlying continuous value; the label is the signed step
        (``-5`` … ``0`` … ``+5``). The middle item (``len // 2``) is the
        0/default. Used for both Strength and Exposure so they read
        identically."""
        combo = QComboBox()
        combo.setObjectName(object_name)
        combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        combo.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        mid = len(values) // 2
        for i, value in enumerate(values):
            step = i - mid
            label = "0" if step == 0 else f"{step:+d}"
            combo.addItem(label, float(value))
        combo.setToolTip(tooltip)
        combo.currentIndexChanged.connect(on_changed)
        return combo

    @staticmethod
    def _select_nearest_combo(combo, value: float) -> None:
        """Select the graduation step whose value is closest to ``value``
        (a legacy slider value may sit between steps). Display-only — the
        signal is blocked so it never echoes a change; the surface keeps
        the exact ``value`` for rendering until the user picks a step."""
        best_i, best_d = 0, None
        for i in range(combo.count()):
            data = combo.itemData(i)
            if data is None:
                continue
            d = abs(float(data) - float(value))
            if best_d is None or d < best_d:
                best_i, best_d = i, d
        combo.blockSignals(True)
        combo.setCurrentIndex(best_i)
        combo.blockSignals(False)

    def _on_strength_changed(self, _idx: int) -> None:
        """spec/157 — Strength dropdown picked. A combo change is settled,
        so render + persist immediately (no drag-debounce). Suppressed
        while ``set_state`` is loading."""
        data = self._strength_combo.currentData()
        if data is not None:
            self._look_strength = float(data)
        if self._loading:
            return
        self._strength_combo.repaint()
        self.render_now()
        self.changed.emit("tone")

    def _on_exposure_changed(self, _idx: int) -> None:
        """spec/157 — Exposure dropdown picked (EV value as the item
        data). Same settled-change render/persist as Strength."""
        data = self._exposure_combo.currentData()
        if data is not None:
            self._user_exposure = float(data)
        if self._loading:
            return
        self._exposure_combo.repaint()
        self.render_now()
        self.changed.emit("tone")

    # ── Render-on-release state machine (spec/115 §1) ────────────────

    def _on_drag_pressed(self) -> None:
        """A slider grab started. While ``_dragging`` is True the live
        ``valueChanged`` handlers update the label only — they do NOT
        (re)start the debounce. The render happens on release."""
        self._dragging = True
        self._render_timer.stop()

    def _on_drag_released(self) -> None:
        """Drag ended — stop the debounce (which the keyboard path
        might have armed earlier; releasing supersedes it) and render
        ONCE. ``changed`` fires with the ``"tone"`` kind so the host
        persists the new look_strength / user_exposure."""
        self._dragging = False
        self._render_timer.stop()
        if self._loading:
            return
        self.render_now()
        self.changed.emit("tone")

    def _note_value_changed(self) -> None:
        """A direct-slider tick fired. Only arm the debounce when NOT
        currently dragging (drag end has its own render path). The
        ``changed`` emit happens at commit time (release / debounce
        timeout) so the host doesn't persist every tick either."""
        if self._dragging:
            return
        # Keyboard / field / programmatic — debounce so a held arrow
        # key doesn't fire a render per repeat.
        self._render_timer.start()

    def _on_render_timer(self) -> None:
        """Debounce timer expired — render now and emit one ``changed``.
        Only fires for the keyboard / field / programmatic path; drag
        endings stop the timer in :meth:`_on_drag_released`."""
        if self._loading:
            return
        self.render_now()
        self.changed.emit("tone")

    def _sync_strength_widgets(self) -> None:
        """Reflect the current strength on the dropdown (nearest step),
        without firing a change. Strength is inert on Original — grey the
        control + caption to make that clear."""
        self._select_nearest_combo(self._strength_combo, self._look_strength)
        on_original = (self._look == "original")
        self._strength_combo.setEnabled(not on_original)
        self._strength_label.setEnabled(not on_original)

    def _sync_exposure_widgets(self) -> None:
        """Reflect the current ``_user_exposure`` on the dropdown (nearest
        step) without firing a change. Unlike Strength, Exposure STAYS
        ENABLED on Original — a user can nudge brightness on any photo,
        Look or no Look."""
        self._select_nearest_combo(self._exposure_combo, self._user_exposure)

    def _build_style_group(self) -> QWidget:
        """The STYLE box — what is this photo? Re-routes the Natural."""
        box, col = self._group(tr("Style"))
        self._style_combo = QComboBox()
        self._style_combo.setObjectName("ProcessStyleCombo")
        self._style_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._style_combo.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        for s in _STYLES:
            self._style_combo.addItem(s.replace("_", " ").title(), s)
        # Base hint — the badge setter appends a live classification
        # status line below it (spec/58 §5.2 tooltip knob).
        self._style_tooltip_base = tr(
            "The genre this photo's automatic correction is calibrated "
            "for (portrait, landscape, macro, …). Changing it re-routes "
            "every Look.")
        self._style_combo.setToolTip(self._style_tooltip_base)
        self._style_combo.currentIndexChanged.connect(self._on_style_changed)
        # ``activated`` fires on every USER pick — even re-picking the
        # shown value — and never programmatically (spec/58 §2).
        self._style_combo.activated.connect(self._on_style_activated)
        col.addWidget(self._style_combo)
        col.addStretch(1)
        box.setMinimumHeight(box.minimumSizeHint().height())
        return box

    def _build_filter_group(self) -> QWidget:
        """The FILTER box (spec/55 — the locked nine, live 2026-06-10)."""
        box, col = self._group(tr("Filter"))
        self._filter_combo = QComboBox()
        self._filter_combo.setObjectName("ProcessFilterCombo")
        self._filter_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._filter_combo.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        self._filter_combo.addItem(tr("None"), None)
        for key in available_filters():
            self._filter_combo.addItem(filter_display_name(key), key)
        self._filter_combo.setToolTip(tr(
            "Creative filters — give the photo a distinctive character "
            "on top of the chosen Look. The choice is stored and only "
            "baked into the exported file when you click Export."))
        self._filter_combo.currentIndexChanged.connect(
            self._on_filter_changed)
        col.addWidget(self._filter_combo)
        # spec/156 — filter STRENGTH graduation, directly below the filter
        # in the same group. +2 = the filter's full effect (today's
        # recipe); 0 (default) eases it to ~70 %; −2 ≈ 40 %. Disabled
        # while no filter is chosen (nothing to scale).
        self._filter_strength_combo = QComboBox()
        self._filter_strength_combo.setObjectName("ProcessFilterStrengthCombo")
        self._filter_strength_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._filter_strength_combo.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        for value, label in (
            (-2.0, tr("Strength −2 (subtle)")),
            (-1.0, tr("Strength −1")),
            (0.0, tr("Strength 0 (medium)")),
            (1.0, tr("Strength +1")),
            (2.0, tr("Strength +2 (full)")),
        ):
            self._filter_strength_combo.addItem(label, value)
        self._filter_strength_combo.setToolTip(tr(
            "How strongly the chosen filter is applied. +2 is the filter's "
            "full effect; 0 (the default) eases it back to about 70 %."))
        self._filter_strength_combo.currentIndexChanged.connect(
            self._on_filter_strength_changed)
        col.addWidget(self._filter_strength_combo)
        self._sync_filter_strength_combo()
        col.addStretch(1)
        box.setMinimumHeight(box.minimumSizeHint().height())
        self._filter_box = box
        return box

    def _build_crop_group(self) -> QWidget:
        """The Crop box — one HORIZONTAL row (Nelson 2026-06-11: line 2
        of the top grid, width-matched to Look). Functionality is the
        spec/42 Phase B shape unchanged: the Aspect combo IS the crop
        toggle — "No Crop" (the persisted "Original" label, renamed on
        display only) clears it, any ratio starts cropping."""
        box, col = self._group(tr("Crop"))

        crop_row = QHBoxLayout()
        crop_row.setSpacing(4)
        self._aspect_combo = AspectRatioCombo()
        self._aspect_combo.setObjectName("ProcessAspectCombo")
        self._aspect_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._aspect_combo.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._aspect_combo.set_selected_label(self._aspect_label)
        self._aspect_combo.label_changed.connect(self._on_aspect_changed)
        self._aspect_combo.setToolTip(tr(
            "Aspect ratio to crop to. 'No Crop' = leave the photo "
            "uncropped."))
        crop_row.addWidget(self._aspect_combo, stretch=1)

        b90l = self._btn("↺ 90°")
        b90l.setToolTip(tr(
            "Turn the crop box 90° — swaps its orientation between "
            "landscape and portrait (e.g. 16:9 → 9:16). The photo is "
            "NOT rotated; use 'Rotate photo' for that."))
        b90l.clicked.connect(self._box_transpose)
        b90r = self._btn("90° ↻")
        b90r.setToolTip(tr(
            "Turn the crop box 90° — swaps its orientation between "
            "landscape and portrait (e.g. 16:9 → 9:16). The photo is "
            "NOT rotated; use 'Rotate photo' for that."))
        b90r.clicked.connect(self._box_transpose)
        self._box_rot_reset = self._btn(tr("Reset"))
        self._box_rot_reset.setToolTip(tr(
            "Set the straighten angle back to 0°. Drag the round handle "
            "above the crop box to straighten by any other angle."))
        self._box_rot_reset.clicked.connect(self._box_rotate_reset)
        crop_row.addWidget(b90l)
        crop_row.addWidget(b90r)
        crop_row.addWidget(self._box_rot_reset)
        col.addLayout(crop_row)

        col.addStretch(1)
        box.setMinimumHeight(box.minimumSizeHint().height())
        return box

    def _build_image_rotate_group(self) -> QWidget:
        """The 'Rotate photo' box — rotates the WHOLE PICTURE by ±90°
        (distinct from the Crop group's box-rotate row, which only
        spins the crop frame). Wired to :meth:`rotate_image`, which
        fires ``changed('rotation')``; the host persists
        ``adj.rotation`` from that signal. Restored 2026-06-22 — the
        buttons were dropped in the dense-control-tier rework
        (``a4c2a12``) while the backend stayed intact (spec/59)."""
        box, col = self._group(tr("Rotate photo"))
        row = QHBoxLayout()
        row.setSpacing(4)
        self._img_rot_ccw_btn = self._btn(tr("Rotate photo ↺"))
        self._img_rot_ccw_btn.setToolTip(tr(
            "Rotate the entire photo 90° counter-clockwise. The crop "
            "is reset so it matches the new frame."))
        self._img_rot_ccw_btn.clicked.connect(
            lambda: self.rotate_image(-90))
        row.addWidget(self._img_rot_ccw_btn)
        self._img_rot_cw_btn = self._btn(tr("Rotate photo ↻"))
        self._img_rot_cw_btn.setToolTip(tr(
            "Rotate the entire photo 90° clockwise. The crop is reset "
            "so it matches the new frame."))
        self._img_rot_cw_btn.clicked.connect(
            lambda: self.rotate_image(90))
        row.addWidget(self._img_rot_cw_btn)
        col.addLayout(row)
        col.addStretch(1)
        box.setMinimumHeight(box.minimumSizeHint().height())
        return box

    def _add_action_buttons(self, into) -> None:
        """Create the view-control buttons (Toggle Adjustments / Toggle Crop /
        Reset all) and append them to ``into`` — folded onto the Crop line
        (Nelson 2026-06-20) so they no longer cost a separate row. NO Export
        and NO navigation — those belong to the host (docs/26 §4)."""
        self._compare_toggle = self._toggle(tr("Toggle Adjustments"))
        self._compare_toggle.setToolTip(tr(
            "ON = the original (before any tone / Vibrance change). OFF = "
            "your adjusted version. ( \\ )"))
        self._compare_toggle.toggled.connect(self._on_compare_toggled)
        into.addWidget(self._compare_toggle)

        self._preview_toggle = self._toggle(tr("Toggle Crop"))
        self._preview_toggle.setToolTip(tr(
            "ON = the final output cropped to the box (what export "
            "produces), fit to the canvas. OFF = the full frame with "
            "the crop box overlaid. For the full-resolution view, use "
            "Full Resolution (F10)."))
        self._preview_toggle.toggled.connect(self._on_preview_toggled)
        into.addWidget(self._preview_toggle)

        self._reset_btn = self._btn(tr("Reset all"))
        self._reset_btn.setToolTip(tr(
            "Reset this image's adjustments + crop + rotation ( R )."))
        self._reset_btn.clicked.connect(self._on_reset_all)
        into.addWidget(self._reset_btn)

    # ── Small widget helpers ─────────────────────────────────────────

    def _toggle(self, text: str, *, checked: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("FeatureToggle")
        b.setCheckable(True)
        b.setChecked(checked)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        return b

    def _btn(self, text: str) -> QPushButton:
        b = QPushButton(text)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        return b

    def _group(self, title: str):
        box = QFrame()
        box.setObjectName("ProcessGroupBox")
        col = QVBoxLayout(box)
        # Compacted 2026-06-09: (10, 6, 10, 10) → (10, 4, 10, 4),
        # spacing 8 → 4. Each group box has a 1 px QSS border + 6 px
        # radius — the visible chrome no longer needs internal breathing
        # room since the content is already grouped visually.
        col.setContentsMargins(10, 4, 10, 4)
        col.setSpacing(4)
        lbl = QLabel(title)
        lbl.setObjectName("ProcessGroupTitle")
        col.addWidget(lbl)
        return box, col

    # ── Public API (host ⇄ surface) ──────────────────────────────────

    def load_image(self, full_array: np.ndarray, *, style: str = "general") -> None:
        """Load a new full-resolution image (a decoded photo, or a frame
        extracted from a clip). Computes the downsampled preview + the
        A-routed Natural for ``style``. Does NOT push look/crop state —
        the host follows with :meth:`set_state`.

        Synchronous — the video workshop's frame loads and tests use it
        directly; the photo Edit page prepares the same triple OFF the
        UI thread (spec/63 §6.1) and lands it via :meth:`load_prepared`."""
        self._full_array = full_array
        self._preview_array = _downsample(full_array, PREVIEW_MAX_WIDTH)
        self._style = style or "general"
        self._natural_params = compute_auto_params(
            self._preview_array, style=self._style_for_auto())

    def load_prepared(
        self,
        full_array: np.ndarray,
        preview_array: np.ndarray,
        natural_params: Params,
        *,
        style: str = "general",
    ) -> None:
        """Land a working copy the host prepared OFF the UI thread
        (spec/63 §6.1 — the Edit prep worker): the decode, the preview
        downsample and the A-routed Natural already happened; this just
        adopts them. The host follows with :meth:`set_state` exactly as
        after :meth:`load_image`."""
        self._full_array = full_array
        self._preview_array = preview_array
        self._style = style or "general"
        self._natural_params = natural_params

    def clear(self) -> None:
        """Drop the loaded image (host shows an unrenderable file via the
        canvas directly). No render."""
        self._full_array = None
        self._preview_array = None

    def set_state(
        self,
        *,
        look: str = "original",
        crop_norm: Optional[tuple[float, float, float, float]],
        box_angle: float,
        style: str,
        aspect_label: str,
        rotation: int = 0,
        creative_filter: Optional[str] = None,
        look_strength: float = 1.0,
        user_exposure: float = 0.0,
        filter_strength: float = 0.0,
    ) -> None:
        """Push a saved CHOICE into the widgets (no ``changed`` emitted)
        and render. ``rotation`` carries the per-item 90° image rotation
        (``Adjustment.rotation``); ``creative_filter`` is the spec/54 §8
        slot (stored, not yet rendered — the filter set is pending);
        ``look_strength`` is the Nelson 2026-06-13 slider (0..2 clamped
        — the gateway seam is the canonical clamp, but a stale row
        with a wild value still loads safely); ``user_exposure`` is the
        spec/115 per-image EV nudge (−2..+2 clamped — same belt-and-
        braces shape)."""
        with self._suppress():
            self._style = style or "general"
            self._style_combo.blockSignals(True)
            self._style_combo.setCurrentIndex(
                max(0, self._style_combo.findData(self._style)))
            self._style_combo.blockSignals(False)
            self._aspect_label = aspect_label
            self._aspect_combo.blockSignals(True)
            self._aspect_combo.set_selected_label(aspect_label)
            self._aspect_combo.blockSignals(False)
            if self._crop_overlay is not None:
                self._crop_overlay.set_aspect_ratio(aspect_label)
            self._look = look if look in available_looks() else "original"
            self._creative_filter = creative_filter
            self._look_strength = max(0.0, min(2.0, float(look_strength)))
            self._user_exposure = max(
                -2.0, min(2.0, float(user_exposure or 0.0)))
            self._filter_strength = max(
                -2.0, min(2.0, float(filter_strength or 0.0)))
            self._sync_look_buttons()
            self._sync_strength_widgets()
            self._sync_exposure_widgets()
            self._sync_filter_combo()
            self._sync_filter_strength_combo()
            self._crop_norm = crop_norm
            self._box_angle = box_angle or 0.0
            self.set_rotation(rotation)
            if self._crop_overlay is not None:
                self._crop_overlay.set_rect(crop_norm)
                self._crop_overlay.set_box_angle(self._box_angle)
        # spec/115 — re-loading state is an explicit re-render: drop the
        # signature cache so the early-out in render_now never blocks a
        # fresh load. (Same item with the same params would otherwise
        # silently no-op; harmless for tone but breaks a reload that
        # changed e.g. rotation+filter without touching tone Params.)
        self._last_rendered_signature = None
        self.render_now()
        self._sync_crop_overlay_geometry()

    def get_state(self) -> SurfaceState:
        """Read the current CHOICE back out (for the host to persist,
        or for the video sub-surface's Keep)."""
        return SurfaceState(
            look=self._look,
            crop_norm=self._crop_norm,
            box_angle=self._box_angle,
            style=self._style,
            aspect_label=self._aspect_label,
            creative_filter=self._creative_filter,
            look_strength=self._look_strength,
            user_exposure=self._user_exposure,
            filter_strength=self._filter_strength,
        )

    def set_af_point(self, af) -> None:
        """spec/116 §2 — set the Subject-Spotlight anchor for the
        loaded photo. Accepts an :class:`AfPoint` (Mira's normalised
        AF box), a bare ``(cx, cy)`` tuple, or ``None`` (frame-centre
        fallback). The render path consumes it on the next
        :meth:`render_now`."""
        if af is None:
            self._af_point = None
        elif hasattr(af, "cx") and hasattr(af, "cy"):
            self._af_point = (float(af.cx), float(af.cy))
        else:
            cx, cy = af                        # (cx, cy) tuple
            self._af_point = (float(cx), float(cy))
        # The signature snapshot includes the anchor; invalidate so
        # the next render runs even when no other field changed.
        self._last_rendered_signature = None

    def _spotlight_center(self) -> tuple[float, float]:
        """The Subject-Spotlight anchor for the current frame: the
        AF point if set, else the frame centre — never raises (spec/116
        §2 fallback contract)."""
        if self._af_point is None:
            return (0.5, 0.5)
        cx, cy = self._af_point
        # Clamp so a stray out-of-range value can't crash the radial
        # mask (defensive — brand profiles compute in [0,1] already).
        cx = max(0.0, min(1.0, cx))
        cy = max(0.0, min(1.0, cy))
        return (cx, cy)

    def set_classification_badge(
        self, source: Optional[str], confidence: Optional[float],
    ) -> None:
        """Color the STYLE combo by the loaded item's stored
        classification (spec/58 §2): red → amber → green by confidence,
        the fourth color when the human decided. Hosts call this on
        load; the surface flips it to ``human`` itself when the user
        picks a style (:meth:`_on_style_activated`)."""
        band = classification_band(source, confidence)
        combo = self._style_combo
        if combo.property("confidenceBand") != band:
            combo.setProperty("confidenceBand", band)
            st = combo.style()
            st.unpolish(combo)
            st.polish(combo)
        if band == "human":
            status = tr("You decided this style.")
        elif confidence is None:
            status = tr(
                "Not auto-classified yet — picking a style decides it.")
        else:
            status = tr(
                "Auto-classified — confidence {pct}%. Picking a style "
                "makes it your decision.").replace(
                    "{pct}", str(round(confidence * 100)))
        combo.setToolTip(self._style_tooltip_base + "\n\n" + status)

    def set_tools_enabled(self, enabled: bool) -> None:
        """Enable/disable every interactive AdjustmentSurface control
        (look buttons, combos, crop buttons, action row).

        Walks the child tree of ``tools_widget`` and sets ``enabled`` on
        each interactive widget directly (not on ``tools_widget`` itself):
        on Windows the QSS ``:disabled`` pseudo-state often doesn't fire
        visually when only the parent is disabled — same propagation quirk
        that forced the click-cursor event filter (CLAUDE.md "Clickable
        affordances"). Hosts can mark widgets to be skipped by setting
        the Qt property ``excludeFromToolsEnable = True`` on them."""
        from PyQt6.QtWidgets import (
            QComboBox, QLineEdit, QPushButton, QSlider,
        )
        for cls in (QSlider, QPushButton, QLineEdit, QComboBox):
            for w in self._tools_widget.findChildren(cls):
                if w.property("excludeFromToolsEnable"):
                    continue
                w.setEnabled(enabled)

    def set_filter_features_visible(self, visible: bool) -> None:
        """Show/hide the FILTER box. VISIBLE by default since the
        spec/55 set landed (2026-06-10); the hook stays for hosts that
        need to suppress it."""
        self._filter_box.setVisible(visible)

    # ── Render ───────────────────────────────────────────────────────

    def render_now(self) -> None:
        """Render the preview pixmap. Compare → show the original;
        Preview → full-res + crop baked (final output); else the working
        view (downsampled, full frame, crop shown only as the overlay).

        Pipeline order (docs/25 §12, mirrored by ``core.photo_render``):
        rotation FIRST (so crop+overlay land on the rotated frame),
        then tone, then crop. ``_rotation`` is the per-item 90° image
        rotation (independent from ``_box_angle``, which only spins the
        crop overlay).

        spec/115 §1 — early-out when the resolved Params + the rest of
        the render signature would produce the same pixmap as the last
        call. Keeps the post-release debounce render (which fires when
        the keyboard-path timer expires AFTER a drag already rendered)
        cheap, without breaking the explicit re-render callers
        (Compare/Preview/aspect/rotate stamp a fresh signature)."""
        base = self._full_array if self._preview_full else self._preview_array
        if base is None:
            return
        signature = self._render_signature()
        if signature is not None and signature == self._last_rendered_signature:
            return
        # The render runs ON the UI thread and can lag visibly (tone +
        # filter math over the whole frame). Honest UI during the freeze
        # (Nelson 2026-06-10): the wait cursor goes up BEFORE the work
        # starts and drops in finally; the click handlers flush their
        # control repaints first so no stale button/label survives into
        # the lag. Override cursors stack, so nested callers are safe.
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            out = base
            if self._rotation:
                try:
                    out = apply_rotation(out, self._rotation)
                except Exception:                          # noqa: BLE001
                    log.exception("apply_rotation failed")
            if not self._comparing:
                params = self._params_for_look()
                if not params.is_identity:
                    try:
                        out = apply_params(out, params)
                    except Exception:                      # noqa: BLE001
                        log.exception("apply_params failed")
                if self._creative_filter:
                    try:
                        recipe = resolve_filter_recipe(
                            self._creative_filter, self._style)
                        if recipe is not None:
                            out = apply_filter(
                                out, FilterRecipe.from_dict(recipe),
                                creative_filter_amount(self._creative_filter)
                                * filter_strength_scale(self._filter_strength),
                                center=self._spotlight_center())
                    except Exception:                      # noqa: BLE001
                        log.exception("apply_filter failed")
            if self._preview_full:
                h, w = out.shape[:2]
                crop = self._crop_rect_for_display(w, h)
                if crop is not None:
                    angle = self._box_angle
                    if angle:
                        out = extract_rotated_crop(out, crop, angle)
                    else:
                        out = np.ascontiguousarray(
                            apply_crop_norm(out, crop))
            self._display.set_rendered_pixmap(_array_to_pixmap(out))
            self._last_rendered_signature = signature
        finally:
            QApplication.restoreOverrideCursor()

    def render_full_pixmap(self) -> Optional[QPixmap]:
        """The FINAL-OUTPUT render as a pixmap: full resolution, tone +
        filter + rotation applied, crop BAKED — exactly what export
        produces. Feeds the F10 inspection lens so the user sees the
        processed/cropped photo at full res BEFORE exporting (Nelson
        2026-06-12). Pure read: the canvas, the Toggle-Crop preview and
        every bit of surface state are untouched. ``None`` when no
        image is loaded. Runs under the caller's wait cursor."""
        base = self._full_array
        if base is None:
            return None
        out = base
        if self._rotation:
            try:
                out = apply_rotation(out, self._rotation)
            except Exception:                          # noqa: BLE001
                log.exception("apply_rotation failed")
        params = self._params_for_look()
        if not params.is_identity:
            try:
                out = apply_params(out, params)
            except Exception:                          # noqa: BLE001
                log.exception("apply_params failed")
        if self._creative_filter:
            try:
                recipe = resolve_filter_recipe(
                    self._creative_filter, self._style)
                if recipe is not None:
                    out = apply_filter(
                        out, FilterRecipe.from_dict(recipe),
                        creative_filter_amount(self._creative_filter)
                        * filter_strength_scale(self._filter_strength),
                        center=self._spotlight_center())
            except Exception:                          # noqa: BLE001
                log.exception("apply_filter failed")
        h, w = out.shape[:2]
        crop = self._crop_rect_for_display(w, h)
        if crop is not None:
            angle = self._box_angle
            if angle:
                out = extract_rotated_crop(out, crop, angle)
            else:
                out = np.ascontiguousarray(apply_crop_norm(out, crop))
        return _array_to_pixmap(out)

    def _crop_rect_for_display(
        self, w: int, h: int,
    ) -> Optional[tuple[float, float, float, float]]:
        """The crop rect to apply in Preview: the per-image override if
        drawn, else the centred default for the aspect ratio. ``None``
        means no crop — Original with no drawn rect (same as the full
        image), or any state where there's nothing to bake.

        Nelson 2026-06-09: Original means "keep the source photo's
        aspect ratio but still crop" — so a drawn rect IS honoured
        under Original (the overlay locks resize to the source ratio).
        Crop OFF is the "no crop" gesture."""
        if self._crop_norm is not None:
            return self._crop_norm
        ratio = get_aspect_ratio(self._aspect_label)
        if ratio.is_original:
            return None
        return compute_default_crop(w, h, ratio)

    # ── Look -> Params ───────────────────────────────────────────────

    def _params_for_look(self) -> Params:
        """Compile the current choice to engine Params — the cached
        Natural plus the chosen Look's bias (spec/54 §3.2). Strength
        scales the WHOLE composed Look at the seam (Nelson 2026-06-13).
        Creative filters will extend this once the filter engine lands.

        spec/115 §2 — the per-image USER exposure is added to
        ``Params.exposure`` AFTER the Look has been strength-scaled.
        That's why it lives here (the only seam that returns the final
        Params for the engine) and not as an extra ``.scaled()``
        argument: it should NOT scale with Strength, and it should NOT
        scale with the Look's own ``exposure`` baseline. A nudge sits
        on top, period."""
        params = look_params_from_natural(
            self._natural_params, self._look,
            strength=self._look_strength)
        if self._user_exposure:
            from dataclasses import replace
            params = replace(
                params,
                exposure=params.exposure + float(self._user_exposure),
            )
        return params

    def _render_signature(self) -> Optional[tuple]:
        """A hashable snapshot of every input that influences
        :meth:`render_now`'s output. ``None`` while comparing (the
        render path becomes "show the original" and skips tone — early-
        out via the signature would block the toggle from re-painting).
        Used purely as a cheap guard against redundant renders; the
        actual render code remains the source of truth."""
        if self._comparing:
            return None
        params = self._params_for_look()
        crop_norm = self._crop_norm if self._crop_norm is not None else None
        return (
            "preview" if not self._preview_full else "full",
            int(self._rotation or 0),
            self._look,
            self._creative_filter,
            float(self._filter_strength or 0.0),
            self._style,
            self._aspect_label,
            tuple(crop_norm) if crop_norm is not None else None,
            float(self._box_angle or 0.0),
            float(params.exposure), float(params.contrast),
            float(params.highlights), float(params.shadows),
            float(params.whites), float(params.blacks),
            float(params.sharpness), float(params.saturation),
            float(params.vibrance),
        )

    def _style_for_auto(self) -> Optional[str]:
        return self._style if self._style and self._style != "general" else None

    @contextmanager
    def _suppress(self):
        """While set, the value-changed handlers don't render/emit (used
        when the host or an internal restore pushes values in)."""
        prev = self._loading
        self._loading = True
        try:
            yield
        finally:
            self._loading = prev

    # ── Slots — the Look chooser ─────────────────────────────────────

    def _sync_look_buttons(self) -> None:
        """Reflect ``self._look`` on the segmented row — hand-managed
        exclusivity so programmatic loads can set any button without a
        QButtonGroup signal storm. The Strength slider's enabled
        state follows the Look (inert on Original)."""
        for key, b in self._look_buttons.items():
            b.blockSignals(True)
            b.setChecked(key == self._look)
            b.blockSignals(False)
        # The strength dropdown exists once _build_look_group has run;
        # _sync_look_buttons fires from inside that build too (before
        # the combo exists), so guard the call.
        if hasattr(self, "_strength_combo"):
            self._sync_strength_widgets()

    def _on_look_clicked(self, key: str) -> None:
        """A segmented-row button was clicked. Re-assert exclusivity
        (clicking the already-checked button must not uncheck it) and
        apply the choice."""
        self.set_look(key)

    def _sync_filter_combo(self) -> None:
        """Reflect ``self._creative_filter`` on the combo without
        echoing a change signal."""
        self._filter_combo.blockSignals(True)
        idx = self._filter_combo.findData(self._creative_filter)
        self._filter_combo.setCurrentIndex(max(0, idx))
        self._filter_combo.blockSignals(False)

    def _sync_filter_strength_combo(self) -> None:
        """spec/156 — reflect ``self._filter_strength`` on the strength
        dropdown without echoing a change, and grey it out when no filter
        is chosen (there's nothing to scale). Built lazily, so guard for
        callers that run before ``_build_filter_group``."""
        combo = getattr(self, "_filter_strength_combo", None)
        if combo is None:
            return
        combo.blockSignals(True)
        idx = combo.findData(float(self._filter_strength))
        combo.setCurrentIndex(idx if idx >= 0 else combo.findData(
            FILTER_STRENGTH_DEFAULT))
        combo.blockSignals(False)
        combo.setEnabled(bool(self._creative_filter))

    def _on_filter_changed(self, _idx: int) -> None:
        """The Filter combo changed — apply, render, persist."""
        if self._loading:
            return
        self._creative_filter = self._filter_combo.currentData()
        # The strength control only bites when a filter is set — keep its
        # enabled state in step with the selection.
        self._sync_filter_strength_combo()
        if self._preview_array is None:
            return
        # Flush the combo NOW — its display text otherwise keeps the
        # previous filter's name through the render lag (Nelson
        # 2026-06-10).
        self._filter_combo.repaint()
        self.render_now()
        self.changed.emit("filter")

    def _on_filter_strength_changed(self, _idx: int) -> None:
        """spec/156 — the strength dropdown changed. Persist via the same
        ``"filter"`` signal the host already routes (the strength rides
        the Adjustment row next to ``creative_filter``)."""
        if self._loading:
            return
        data = self._filter_strength_combo.currentData()
        self._filter_strength = (FILTER_STRENGTH_DEFAULT if data is None
                                 else float(data))
        if self._preview_array is None:
            return
        self._filter_strength_combo.repaint()
        self.render_now()
        self.changed.emit("filter")

    def set_look(self, key: str) -> None:
        """Apply a Look choice (segmented row, grid moment, or the
        host's keyboard cycling). No-op outside ``available_looks()``."""
        if key not in available_looks():
            return
        changed = key != self._look
        self._look = key
        self._sync_look_buttons()
        if self._preview_array is None or self._loading:
            return
        if changed:
            # Flush the segmented row NOW — without this the old
            # button's uncheck only paints after the render returns,
            # so two buttons sit blue through the lag (Nelson
            # 2026-06-10).
            for b in self._look_buttons.values():
                b.repaint()
            self.render_now()
            self.changed.emit("look")

    def cycle_look(self, delta: int = 1) -> None:
        """Step the Look forward/backward (host keyboard: L / Shift+L)."""
        keys = list(available_looks())
        idx = (keys.index(self._look) + delta) % len(keys) \
            if self._look in keys else 0
        self.set_look(keys[idx])

    def open_look_grid(self) -> None:
        """The grid moment (spec/54 §4.2) — 2×2 of THIS photo, every
        tile clickable. Host keyboard: G."""
        if self._preview_array is None:
            return
        base = self._preview_array
        if self._rotation:
            try:
                base = apply_rotation(base, self._rotation)
            except Exception:                          # noqa: BLE001
                log.exception("apply_rotation failed for the look grid")
        picked = LookGridDialog.choose(
            base, self._natural_params, self._look, parent=self)
        if picked is not None:
            self.set_look(picked)

    def _on_compare_toggled(self, checked: bool) -> None:
        self._comparing = checked
        self.render_now()

    def _on_preview_toggled(self, checked: bool) -> None:
        self._preview_full = checked
        self.render_now()
        self._sync_crop_overlay_geometry()

    def _on_reset_all(self) -> None:
        """Reset the loaded image's choice + crop + crop-box rotation +
        90° image rotation: back to **Original** (no look), no filter, no
        crop. The image rotation is part of reset because it's a
        destructive per-item override — Reset is "back to the original
        file" and the user expects rotation to undo too."""
        if self._preview_array is None:
            return
        with self._suppress():
            self._look = "original"
            self._creative_filter = None
            self._look_strength = 1.0
            self._user_exposure = 0.0
            self._filter_strength = FILTER_STRENGTH_DEFAULT
            self._sync_look_buttons()
            self._sync_filter_combo()
            self._sync_filter_strength_combo()
            self._sync_exposure_widgets()
            self._crop_norm = None
            self._box_angle = 0.0
            self._rotation = 0
            if self._crop_overlay is not None:
                self._crop_overlay.set_rect(None)
                self._crop_overlay.set_box_angle(0.0)
        # spec/115 §1 — Reset must always re-render, even when the
        # outgoing Params happen to equal the incoming (e.g. on a
        # pristine photo). Drop the signature cache so the early-out
        # never blocks a deliberate reset.
        self._last_rendered_signature = None
        self.render_now()
        self._sync_crop_overlay_geometry()
        self.changed.emit("reset")

    # ── Slots — style ────────────────────────────────────────────────

    def _on_style_changed(self, _idx: int) -> None:
        """Re-route the Natural for the newly-chosen style; every Look
        recompiles from it on the next render."""
        if self._loading or self._preview_array is None:
            return
        self._style = self._style_combo.currentData()
        # The Natural re-route (compute_auto_params) is heavy and runs
        # BEFORE render_now's own wait cursor — flush the combo and
        # raise the cursor here so the whole freeze is covered.
        self._style_combo.repaint()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._natural_params = compute_auto_params(
                self._preview_array, style=self._style_for_auto())
            self.render_now()
        finally:
            QApplication.restoreOverrideCursor()
        self.changed.emit("style")

    def _on_style_activated(self, _idx: int) -> None:
        """The user explicitly picked a style from the dropdown — even
        the one already shown (spec/58 §2: choosing IS the human
        decision). When the value changed, the re-route already ran via
        ``currentIndexChanged``; here the badge flips to ``human`` and
        the host persists ``classification_source='user'``."""
        if self._loading or self._preview_array is None:
            return
        self.set_classification_badge("user", None)
        self.style_decided.emit(self._style_combo.currentData())

    # ── Slots — crop + rotation ──────────────────────────────────────

    def _displayed_dims(self) -> Optional[tuple[int, int]]:
        """``(w, h)`` of the DISPLAYED preview frame — the source array's
        dimensions, swapped on a 90°/270° rotation. Used wherever a crop
        rect or overlay geometry needs to be normalised against what the
        user actually sees (Nelson 2026-06-06 — preventing the "photo
        rotates back when I toggle the crop" regression: the default
        crop must be computed for the rotated frame, not the source)."""
        if self._preview_array is None:
            return None
        h, w = self._preview_array.shape[:2]
        if self._rotation in (90, 270):
            w, h = h, w
        return w, h

    def _on_aspect_changed(self, label: str) -> None:
        """Switch aspect ratio. Recompose the current crop to the new
        ratio rather than wiping it (docs/25 §4)."""
        self._aspect_label = label
        ratio = get_aspect_ratio(label)
        if self._crop_overlay is not None:
            self._crop_overlay.set_aspect_ratio(label)
        if self._preview_array is not None:
            if ratio.is_original:
                self._crop_norm = None
                if self._crop_overlay is not None:
                    self._crop_overlay.set_rect(None)
            else:
                dims = self._displayed_dims()
                if dims is not None:
                    w, h = dims
                    rect = compute_default_crop(w, h, ratio)
                    if rect is not None:
                        self._crop_norm = rect
                        if self._crop_overlay is not None:
                            self._crop_overlay.set_rect(rect)
        self._sync_crop_overlay_geometry()
        self.render_now()
        if not self._loading:
            self.changed.emit("aspect")

    def _on_crop_rect_changed(
        self, rect_norm: tuple[float, float, float, float],
    ) -> None:
        self._crop_norm = rect_norm
        if not self._loading:
            self.changed.emit("crop")

    def _box_transpose(self) -> None:
        """Turn the crop box 90° — swap its ORIENTATION between landscape
        and portrait (e.g. 16:9 → 9:16) by transposing the aspect label.

        This is the user-facing "rotate the crop box" action and is
        deliberately ISOLATED from the photo's pixels: only the crop
        rectangle's shape flips; the image is never rotated (that's the
        separate "Rotate photo" control). Both ±90° buttons map here —
        an orientation swap is its own inverse, so clockwise vs counter-
        clockwise land on the same portrait/landscape rectangle.

        Reuses :meth:`_on_aspect_changed` (re-locks the overlay, recomputes
        the centred crop at the swapped ratio, re-renders, persists via the
        ``"aspect"`` signal). No-op on Original / 1:1, which have no
        orientation to flip. Straighten (``_box_angle``, the round drag
        handle) is untouched."""
        if self._preview_array is None:
            return
        new_label = transpose_label(self._aspect_label)
        if new_label == self._aspect_label:
            return
        # Reflect the swap on the combo (display-only; blocks signals so it
        # doesn't re-enter ``_on_aspect_changed``), then run the canonical
        # aspect-change path which recomposes the crop + persists.
        if self._aspect_combo is not None:
            self._aspect_combo.set_selected_label(new_label)
        self._on_aspect_changed(new_label)

    def _box_rotate_reset(self) -> None:
        if self._preview_array is not None:
            self._set_box_angle(0.0)

    def _on_overlay_angle(self, angle: float) -> None:
        """Rotation-handle commit (Nelson 2026-06-10 — free angle by
        dragging the lollipop, not by stepping buttons). Same path as
        the 90° buttons: persist + re-render via _set_box_angle."""
        if self._preview_array is None:
            return
        self._set_box_angle(float(angle))

    # ── Image rotation (90° steps) — independent from the crop box ─────

    def rotate_image(self, delta: int) -> None:
        """Rotate the IMAGE (not the crop box) by ``delta`` degrees (90 or
        -90). Wraps to one of {0, 90, 180, 270}. Resets the crop rect and
        box rotation because the displayed frame's width/height swap on a
        90/270 turn — the previously-drawn rect would land in the wrong
        place (see ``core.photo_render.apply_rotation`` docstring). The
        host persists ``_rotation`` to ``Adjustment.rotation`` on the
        ``changed("rotation")`` signal that this fires."""
        if self._preview_array is None:
            return
        # Normalise to the engine's 90° steps; positive = clockwise.
        step = 90 if delta > 0 else -90
        new = (self._rotation + step) % 360
        if new == self._rotation:
            return
        self._rotation = new
        # The crop+overlay's coordinate system flipped under us — clear.
        self._crop_norm = None
        if self._box_angle:
            self._box_angle = 0.0
            if self._crop_overlay is not None:
                self._crop_overlay.set_box_angle(0.0)
        # Aspect default lock is preserved (Original stays Original); the
        # crop overlay re-syncs against the new dimensions on the next
        # photo_geometry_changed pulse.
        self.render_now()
        self._sync_crop_overlay_geometry()
        if not self._loading:
            self.changed.emit("rotation")

    def set_rotation(self, rotation: int) -> None:
        """Programmatic loader for ``Adjustment.rotation`` (used when a new
        item loads). Doesn't fire ``changed`` — the loader is the host's
        own ``_seed_from_event`` style path."""
        normalised = int(rotation or 0) % 360
        # Round to the nearest 90° in case a wild value slipped in.
        self._rotation = int(round(normalised / 90.0)) * 90 % 360

    def _set_box_angle(self, angle: float) -> None:
        self._box_angle = angle
        if self._crop_overlay is not None:
            self._crop_overlay.set_box_angle(angle)
        self.render_now()
        self._sync_crop_overlay_geometry()
        if not self._loading:
            self.changed.emit("angle")

    def _sync_crop_overlay_geometry(self) -> None:
        if self._crop_overlay is None:
            return
        host = self._display.photo_area_widget()
        self._crop_overlay.setGeometry(host.rect())
        image_rect = self._display.image_rect_in_photo_area()
        # The overlay's normalised coords are against the DISPLAYED
        # (rotated) frame — :meth:`_displayed_dims` does the source ↔
        # rotated swap centrally. Without this, dragging the crop on a
        # rotated photo crashed (out-of-range slice) and the default
        # rect was computed for the wrong axis.
        dims = self._displayed_dims()
        if dims is not None:
            w, h = dims
        else:
            w, h = (image_rect.width() or 1, image_rect.height() or 1)
        self._crop_overlay.set_image_geometry(image_rect, (w, h))
        self._crop_overlay.set_box_angle(self._box_angle)
        # spec/42 Phase B: overlay visibility is now derived from the
        # Aspect combo's value (the Crop toggle was merged into Aspect —
        # "Original" = no crop, anything else = show the crop overlay).
        ratio = get_aspect_ratio(self._aspect_label)
        should_show = (not ratio.is_original) and not self._preview_full
        self._crop_overlay.setVisible(should_show)
        if should_show:
            self._crop_overlay.raise_()

# ── Module-level helpers ──────────────────────────────────────────────


def _downsample(img: np.ndarray, max_width: int) -> np.ndarray:
    """Bound ``img`` to ``max_width`` keeping aspect — the spec/62
    111-ms flaw's fix (measured 2026-06-12 on a 24 MP frame: LANCZOS
    111 ms → integer box ``reduce`` to the nearest ≥-target size +
    BILINEAR finish = 37 ms; box-average pre-reduce IS anti-aliased,
    and at preview scale the finish quality is indistinguishable).
    The 6b prep worker also moved this OFF the UI thread entirely —
    the per-navigation UI cost is zero either way."""
    h, w = img.shape[:2]
    if w <= max_width:
        return img
    new_w = max_width
    new_h = int(round(h * new_w / w))
    pil = Image.fromarray(img)
    factor = max(1, w // max_width)
    if factor > 1:
        pil = pil.reduce(factor)          # integer box shrink, ≥ target
    pil = pil.resize((new_w, new_h), Image.Resampling.BILINEAR)
    return np.asarray(pil)


def _array_to_pixmap(arr: np.ndarray) -> QPixmap:
    if not arr.flags["C_CONTIGUOUS"]:
        arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    qimg = QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimg)
