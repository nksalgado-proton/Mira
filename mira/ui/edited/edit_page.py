"""EditPage — the non-destructive Edit photo surface.

spec/66 (2026-06-14): Edit is **purely creative** — correct classification,
adjust tone, crop. The export status / batch-queue UI that spec/59 §8 put
here MOVED OUT to the Export surface (slice 4 of the spec/66 implementation
pass). Edit no longer triggers export, no longer carries the green/red
mark-for-export border, no longer paints the Exported watermark, and the
locked P/X/Space/C decision keys are inert here (a creative-only surface
has no Pick/Skip ledger to drive). The keys still fire on the viewport —
the page just doesn't connect to them.

What stays:

* **Data layer:** per-item state via the gateway's ``Adjustment`` row —
  the spec/54 CHOICE (``style`` / ``look`` / ``creative_filter``) +
  ``crop_x/y/w/h``, ``crop_angle``, ``rotation``, ``aspect_label``.
* **Navigation layer:** opens a :class:`mira.picked.CullBucket` (synthetic
  single-item from Day-Grid centre-click OR a real bracket / burst from a
  cluster sub-grid). At bucket-edges the page emits :attr:`navigate_at_edge`
  in ``day_grid`` context (parent steps the cursor) or stops in ``cluster``
  context (spec/32 §2.7).

The reusable :class:`mira.ui.edited.adjustment_surface.AdjustmentSurface`
provides the editing chrome (canvas + crop overlay + Look/Style/Filter +
Crop + Compare/Preview/Reset/Copy-Paste-Undo). EditPage is the host: it
composes the surface, dispatches its ``changed(kind)`` signal to gateway
writes, and owns nav.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor, QKeyEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.aspect_ratio import get_aspect_ratio
# ``decode_image`` is no longer called on this module's UI thread —
# the prep worker owns decoding (spec/63 §6.1) — but the import STAYS:
# it is the pre-6b decode seam the era-portable net counts against.
from core.photo_decoder import decode_image, is_supported  # noqa: F401
from core.photo_render import Params, compute_default_crop
from core.settings import load_settings

from mira.picked import CullBucket, CullItem
from mira.gateway.event_gateway import EventGateway
from mira.store import models as m
from mira.ui.base.surface import (
    BaseEditSurface,
    back_button,
    feature_toggle,
    help_button,
    populate_nav_row,
)
from mira.ui.i18n import tr
from mira.ui.edited.adjustment_surface import (
    AdjustmentSurface, normalize_style,
)
from mira.ui.edited.edit_prep import PrepResult, edit_prep
from mira.ui.media.photo_viewport import ViewportItem

log = logging.getLogger(__name__)

# AUTO has per-style tuning for a strict subset of the classifier's scenario
# vocabulary (``core.vocabulary.Scenario``); everything else (sports /
# street / travel / family / astro / video, plus the intermediate bracket
# kinds) falls back to "general" — same as a photo with no classification.
# The single source of truth is ``adjustment_surface._STYLES``; the alias
# keeps this module's historical import path alive (edit_host_page, tests).
_normalize_style = normalize_style


class EditPage(QWidget):
    """Rebuilt Process editing surface (host).  See module docstring."""

    # Marks this surface as the Process page for any host-side duck-typed
    # ``_is_process_mode`` (kept for parity with the legacy class).
    is_process_surface = True

    # ── Shell contract signals ───────────────────────────────────────
    back_requested = pyqtSignal()
    fullscreen_changed = pyqtSignal(bool)
    # spec/32 §2.7 edge nav — parent steps the day-cell cursor when context is
    # "day_grid"; "cluster" context stops at edges (no signal emitted).
    navigate_at_edge = pyqtSignal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("EditPage")

        # ── State ─────────────────────────────────────────────────────
        self._eg: Optional[EventGateway] = None
        self._bucket: Optional[CullBucket] = None
        self._items: list[CullItem] = []
        self._index = 0
        self._nav_context: str = "day_grid"
        self._nav_label_suffix: str = ""
        self._bucket_index = 1
        self._bucket_count = 1
        self._is_first_in_day = True
        self._is_last_in_day = True
        self._cached_path: Optional[Path] = None

        # Aspect default — Settings (legacy reused; not stored on Adjustment
        # at item level until the user actively picks a per-item aspect).
        settings = load_settings()
        self._aspect_default = str(
            settings.get("preferred_aspect_ratio") or "Original")

        # Re-entrancy guard for laggy ops (decode / full-res preview).
        self._busy_flag = False

        self._fullscreen = False

        # ── The reusable editing surface ─────────────────────────────
        self._surface = AdjustmentSurface()
        self._surface.changed.connect(self._on_surface_changed)
        self._surface.style_decided.connect(self._on_style_decided)
        self._surface._aspect_label = self._aspect_default

        # ── The viewport pipeline (spec/63 §6.1, Nelson's checkpoint
        # 2026-06-12): the surface's display IS the PhotoViewport —
        # browse pixels land instantly (proxy-sharp), the working copy
        # preps OFF-thread on settle (Q3: develop only where the user
        # stops), and the developed view flips in place (Q1: the
        # undeveloped flash accepted; tools grey in the window).
        self._viewport = self._surface.display_widget()
        self._viewport.current_changed.connect(self._on_current_changed)
        self._viewport.edge_reached.connect(self._emit_edge)
        # spec/66 §1.1 — Edit is creative-only; P/X/Space/C are inert
        # here (no Pick/Skip ledger to drive). The viewport still emits
        # the locked-map verbs; the page just doesn't wire them.
        self._viewport.truth_requested.connect(self._open_processed_lens)
        self._viewport.fullscreen_requested.connect(self._toggle_fullscreen)
        self._viewport.back_requested.connect(self._on_back_key)

        # The process-wide prep singleton (the PhotoCache shape): the
        # worker emits only to the singleton; pages get SAME-THREAD
        # delivery, so a dying page can never race a cross-thread
        # emission (the 0xC0000409 fail-fast class a per-page QThread
        # hit). Stale results drop by path-match in the handler.
        self._prep = edit_prep()
        self._prep.prepared.connect(self._on_prep_ready)
        self._prep.prep_failed.connect(self._on_prep_failed)
        from PyQt6.QtCore import QTimer
        self._prep_settle = QTimer(self)
        self._prep_settle.setSingleShot(True)
        self._prep_settle.setInterval(150)       # the Picker's cadence
        self._prep_settle.timeout.connect(self._on_prep_settle)

        self._build_ui()
        self._install_keyboard_focus()
        # The page's setFocus lands on the viewport — the key grammar's
        # home (the 5d focus-proxy pattern; PickPage untouched there).
        self.setFocusProxy(self._viewport)

        # No persist debounce remains (spec/54 zero-sliders lock): every
        # edit is a discrete click — look / style / crop / aspect /
        # rotation / reset all persist immediately in
        # ``_on_surface_changed``.

        # ── Test / external-callsite proxies (mirror legacy) ─────────
        s = self._surface
        self._canvas = s.display_widget()      # legacy name, the viewport
        self._crop_overlay = s._crop_overlay
        self._style_combo = s._style_combo
        self._aspect_combo = s._aspect_combo
        self._compare_toggle = s._compare_toggle
        self._preview_toggle = s._preview_toggle
        self._look_buttons = s._look_buttons
        self._grid_btn = s._grid_btn
        # Method proxies — calling these on the page runs the surface's
        # version (which emits ``changed`` → host persists).
        self._on_crop_rect_changed = s._on_crop_rect_changed
        self._box_rotate = s._box_rotate
        self._box_rotate_reset = s._box_rotate_reset
        # Image rotation (90° steps) — Nelson 2026-06-06, distinct from
        # the crop-box rotation. Driven by the COMPACT_ROW buttons below.
        self._rotate_image = s.rotate_image
        self._on_aspect_changed = s._on_aspect_changed
        self._on_reset_all = s._on_reset_all
        self._set_look = s.set_look
        self._cycle_look = s.cycle_look
        self._open_look_grid = s.open_look_grid
        self._render_now = s.render_now
        self._sync_crop_overlay_geometry = s._sync_crop_overlay_geometry

    def _on_back_key(self) -> None:
        """Esc from the viewport: one level at a time — fullscreen
        first, then back (the same logic as the page's own Esc branch)."""
        if self._fullscreen:
            self._exit_fullscreen()
        else:
            self.back_requested.emit()

    def shutdown(self) -> None:
        """Quiesce before destruction: stop the settle timer, leave the
        prep singleton's signal fan-out, drop the viewport's items.
        After this, nothing external can deliver into the page — the
        defined lifecycle end. Idempotent; runs AUTOMATICALLY when the
        page's deferred deletion arrives (see :meth:`event`), so a
        plain ``deleteLater`` is always safe — a prep delivery landing
        mid-destruction was a 0xC0000409 fail-fast (PyQt lifetime
        corruption; 2026-06-12, the 6b churn hunt)."""
        try:
            self._prep_settle.stop()
        except Exception:                                  # noqa: BLE001
            pass
        for sig, slot in (
                (self._prep.prepared, self._on_prep_ready),
                (self._prep.prep_failed, self._on_prep_failed)):
            try:
                sig.disconnect(slot)
            except Exception:                              # noqa: BLE001
                pass                       # already disconnected
        try:
            self._viewport.set_items([])
        except Exception:                                  # noqa: BLE001
            pass

    def event(self, ev) -> bool:  # noqa: N802
        from PyQt6.QtCore import QEvent
        if ev.type() == QEvent.Type.DeferredDelete:
            # The deleteLater flush — quiesce BEFORE destruction begins
            # so no external signal can deliver into a dying page.
            self.shutdown()
        return super().event(ev)

    # ── Properties delegating to the surface ─────────────────────────

    @property
    def _aspect_label(self) -> str:
        return self._surface._aspect_label

    @_aspect_label.setter
    def _aspect_label(self, value: str) -> None:
        self._surface._aspect_label = value

    @property
    def _comparing(self) -> bool:
        return self._surface._comparing

    @property
    def _preview_full(self) -> bool:
        return self._surface._preview_full

    @property
    def _natural_params(self) -> Params:
        return self._surface._natural_params

    @property
    def _preview_array(self):
        return self._surface._preview_array

    @property
    def _full_array(self):
        return self._surface._full_array

    # ── Construction ─────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # spec/42 (Nelson 2026-06-06) — compose BaseEditSurface. TOOLS
        # PANEL sits ABOVE the media (the AdjustmentSurface shape) and
        # holds the editing chrome; MEDIA is the canvas; NAV is the
        # canonical ← Previous / Next → row; COMPACT_ROW is reserved-
        # invisible on photo (timeline lives on EditVideoPage).
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._chrome = BaseEditSurface()
        outer.addWidget(self._chrome)

        # ── TOP_BAR — Back · stretch · position · stretch · ?
        # spec/66 §1.1 — no Export button, no inline progress here. The
        # batch trigger + status line moved to the Export surface; the
        # app-level BatchProgressLine below the menubar shows running
        # jobs from every surface.
        self._back_btn = back_button()
        self._back_btn.setToolTip(tr("Return to the cluster list  (Esc)"))
        self._back_btn.clicked.connect(self.back_requested.emit)
        self._chrome.top_bar.layout().addWidget(self._back_btn)

        self._chrome.top_bar.layout().addStretch(1)

        # X/Y position — "where am I in the current day" — CENTRED on
        # the top line (Nelson 2026-06-11; it lived on the compact row
        # and read as nav-bar clutter). Updated on every ``_show``.
        self._position_label = QLabel("")
        self._position_label.setObjectName("ProcessPositionLabel")
        self._position_label.setToolTip(tr(
            "Position in the current day — item index of total."))
        self._chrome.top_bar.layout().addWidget(self._position_label)

        self._chrome.top_bar.layout().addStretch(1)

        self._help_btn = help_button()
        self._help_btn.setToolTip(tr("Keyboard shortcuts  (F1)"))
        self._help_btn.clicked.connect(self._show_shortcuts)
        self._chrome.top_bar.layout().addWidget(self._help_btn)

        # ── TOOLS_PANEL — AdjustmentSurface tools content (sliders + crop
        # + action row). AdjustmentSurface itself owns the widgets +
        # state; we reparent its tools widget here.
        self._chrome.tools_panel.layout().addWidget(
            self._surface.tools_widget(), stretch=1)
        # Bump the tools_panel's explicit minimumHeight to match what the
        # content actually needs (Nelson 2026-06-09). ``_make_v_region``
        # sets a 232 px default floor, but the AdjustmentSurface content —
        # with TONE + Vibrance + ADJUSTMENTS all now Fixed at their natural
        # heights — needs more than that. Qt's parent layout reports a
        # widget's ``minimumSize()`` (the explicit value) up the chain,
        # not its layout's computed minimum, so without this bump the
        # outer layout gives tools_panel only 232 px and the contents
        # slide under the action row.
        panel = self._chrome.tools_panel
        needed_h = panel.layout().minimumSize().height()
        if needed_h > panel.minimumHeight():
            panel.setMinimumHeight(needed_h)

        # ── MEDIA — the PhotoViewport owned by AdjustmentSurface
        # (spec/63 §6.1 — the one display engine).
        self._chrome.set_media(self._surface.display_widget())

        # ── COMPACT_ROW — image-rotation pair (Nelson 2026-06-06).
        # Per the surface.py docstring the photo's compact_row is reserved
        # for "compact tools on photo"; the 90° image rotation pair is
        # exactly that. Two buttons + a stretch (left-anchored). They
        # rotate the PHOTO, not the crop box — distinct from the ↺/↻
        # buttons in the CROP group which spin only the overlay.
        compact = self._chrome.compact_row.layout()
        self._rotate_ccw_btn = QPushButton(tr("⟲ 90°"))
        self._rotate_ccw_btn.setObjectName("ProcessImageRotateButton")
        self._rotate_ccw_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._rotate_ccw_btn.setToolTip(tr(
            "Rotate the photo 90° counter-clockwise. Crop is reset because "
            "the image's width and height swap on a quarter turn."))
        self._rotate_ccw_btn.clicked.connect(lambda: self._rotate_image(-90))
        compact.addWidget(self._rotate_ccw_btn)
        self._rotate_cw_btn = QPushButton(tr("90° ⟳"))
        self._rotate_cw_btn.setObjectName("ProcessImageRotateButton")
        self._rotate_cw_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._rotate_cw_btn.setToolTip(tr(
            "Rotate the photo 90° clockwise. Crop is reset because the "
            "image's width and height swap on a quarter turn."))
        self._rotate_cw_btn.clicked.connect(lambda: self._rotate_image(90))
        compact.addWidget(self._rotate_cw_btn)
        compact.addStretch(1)
        # The row stays VISIBLE on photo (was hidden in spec/42's initial
        # land); the 48 px is now content instead of reservation.
        self._chrome.set_region_visible("compact_row", True)

        # ── NAV — ← Previous · (Full Screen · Full Resolution) · Next →
        # (Nelson 2026-06-12 standardisation: the same two centre
        # buttons on every photo nav line). Full Resolution opens the
        # STANDARD modal lens with the PROCESSED, CROPPED full-res
        # render — what export produces; the in-canvas Toggle-Crop
        # preview keeps existing untouched (button-driven).
        self._fullscreen_btn = feature_toggle(tr("Full Screen"))
        self._fullscreen_btn.setToolTip(tr(
            "Use the whole screen for editing  (F / F11)"))
        self._fullscreen_btn.clicked.connect(self._toggle_fullscreen)
        self._fullres_btn = QPushButton(tr("Full Resolution"))
        self._fullres_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._fullres_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._fullres_btn.setToolTip(tr(
            "See the processed, cropped photo at full resolution — "
            "exactly what export produces  (F10)"))
        self._fullres_btn.clicked.connect(self._open_processed_lens)
        centre = QWidget()
        centre_row = QHBoxLayout(centre)
        centre_row.setContentsMargins(0, 0, 0, 0)
        centre_row.setSpacing(8)
        centre_row.addWidget(self._fullscreen_btn)
        centre_row.addWidget(self._fullres_btn)
        nav = populate_nav_row(
            self._chrome, with_buckets=False, centre_widget=centre)
        nav.prev.clicked.connect(self._on_prev)
        nav.next.clicked.connect(self._on_next)
        self._nav_prev = nav.prev
        self._nav_next = nav.next

        # spec/66 §1.1 — no border click target: there's no mark-for-
        # export decision on the Edit surface (it moved to Export).
        # The media border stays neutral (set_media_state(None)).

    def _install_keyboard_focus(self) -> None:
        from PyQt6.QtWidgets import QLineEdit
        for w in self.findChildren(QWidget):
            if w is self._viewport:
                # The Quick Sweep lesson (2026-06-12): the NoFocus loop
                # must EXEMPT the viewport or the whole §4 grammar goes
                # dead on this surface.
                continue
            if isinstance(w, QLineEdit):
                w.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            else:
                w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def focusNextPrevChild(self, nxt: bool) -> bool:  # noqa: N802
        return False

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.setFocus()

    # ── load ─────────────────────────────────────────────────────────

    def load(
        self,
        eg: EventGateway,
        bucket: CullBucket,
        *,
        entry_override: Optional[int] = None,
        nav_context: str = "day_grid",
        nav_label_suffix: str = "",
        bucket_index: int = 1,
        bucket_count: int = 1,
        is_first_in_day: bool = True,
        is_last_in_day: bool = True,
    ) -> None:
        """Open ``bucket`` for Process editing.

        ``bucket`` is a :class:`mira.picked.CullBucket` (synthetic single-item
        from a Day-Grid centre-click, OR a real cluster sub-grid bucket with
        bracket / burst members).  Per-item state comes from the gateway's
        ``Adjustment`` row.

        ``entry_override``: 0 → first item, -1 → last item, ``None`` →
        resume the bucket's persisted ``current_index`` cursor.

        ``nav_context`` (spec/32 §2.7) decides edge behaviour: ``"day_grid"``
        → emit :attr:`navigate_at_edge`; ``"cluster"`` → stop at edges.
        """
        self._eg = eg
        self._bucket = bucket
        self._items = list(bucket.items)
        self._nav_context = nav_context
        self._nav_label_suffix = nav_label_suffix
        self._bucket_index = max(1, int(bucket_index))
        self._bucket_count = max(1, int(bucket_count))
        self._is_first_in_day = bool(is_first_in_day)
        self._is_last_in_day = bool(is_last_in_day)

        if not self._items:
            return

        if entry_override is not None:
            n = len(self._items)
            self._index = (n - 1) if entry_override < 0 else max(
                0, min(entry_override, n - 1))
        else:
            soft = eg.bucket(bucket.bucket_key, "edit") if eg else None
            self._index = max(
                0,
                min(soft.current_index if soft else 0, len(self._items) - 1),
            )

        self._cached_path = None
        # Hand the ordered list to the viewport (spec/63 §6.1): browse
        # pixels land instantly; ``current_changed`` drives the chrome
        # + the settle-gated prep below.
        self._viewport.set_items(
            [ViewportItem(path=ci.path, kind=(ci.kind or "photo"),
                          payload=ci)
             for ci in self._items],
            current=self._index)
        self.setFocus()

    # ── Display (spec/63 §6.1 — the async pipeline) ──────────────────

    def _current_item(self) -> Optional[CullItem]:
        if not self._items:
            return None
        return self._items[self._index]

    def _show(self, index: int) -> None:
        """Programmatic navigation — the same pipeline as the arrows
        (the viewport navigates; ``current_changed`` does the rest)."""
        if not self._items:
            return
        index = max(0, min(index, len(self._items) - 1))
        self._viewport.show_index(index)

    def _on_current_changed(self, index: int) -> None:
        """A navigation landed (arrows / wheel / programmatic). The
        browse pixels are already on screen (the viewport's job); the
        chrome updates instantly, the working copy preps on settle
        (Q3), and the tools grey for the gap (Q1)."""
        self._index = index
        ci = self._current_item()
        self._refresh_position_label()
        developed = (
            ci is not None and ci.path == self._cached_path
            and self._surface._full_array is not None)
        if not developed:
            self._set_editing_enabled(False)
            # The overlay still carries the PREVIOUS photo's rect — a
            # drag in the gap would persist it onto THIS item. Hidden
            # until set_state re-syncs it for the landed photo.
            if self._crop_overlay is not None:
                self._crop_overlay.setVisible(False)
        self._prep_settle.start()
        # Persist resume cursor on the bucket soft-state (cluster
        # sub-grids restore here; the Day-Grid synthetic 1-item bucket
        # is a no-op).
        if self._eg is not None and self._bucket is not None:
            try:
                self._eg.set_bucket_current_index(
                    self._bucket.bucket_key, "edit", index)
            except Exception:  # noqa: BLE001
                log.exception(
                    "set_bucket_current_index failed for %s",
                    self._bucket.bucket_key)

    def _set_editing_enabled(self, on: bool) -> None:
        """The development gap (Q1): every editing control greys while
        the working copy preps — the surface tools AND the page's
        compact-row rotate pair (their writes would land on the
        PREVIOUS photo's surface state)."""
        self._surface.set_tools_enabled(on)
        self._rotate_ccw_btn.setEnabled(on)
        self._rotate_cw_btn.setEnabled(on)

    @contextmanager
    def _busy(self):
        from PyQt6.QtWidgets import QApplication
        if self._busy_flag:
            yield
            return
        self._busy_flag = True
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        QApplication.processEvents()
        try:
            yield
        finally:
            QApplication.restoreOverrideCursor()
            self._busy_flag = False

    def _on_prep_settle(self) -> None:
        """The settle beat (the Picker's cadence, Q3): only the photo
        the user LANDED on preps a working copy — fly-bys never even
        request one."""
        ci = self._current_item()
        if ci is None:
            return
        path = ci.path
        if (ci.kind or "photo") == "video" or not is_supported(path):
            # Nothing to develop — the viewport shows the file natively
            # (poster / decode-failure honesty); tools stay greyed.
            self._surface.clear()
            self._cached_path = path
            return
        if path == self._cached_path and self._surface._full_array is not None:
            # Same photo, working copy still loaded — re-push the
            # developed view (navigation cleared the display override).
            self._render_now()
            self._sync_crop_overlay_geometry()
            self._set_editing_enabled(True)
            return
        self._prep.request(path, self._resolve_router_style(ci))

    def _resolve_router_style(self, ci: CullItem) -> str:
        """The style the A-router needs at prep time: the saved
        ``Adjustment.style`` beats the item's classification; "general"
        is the floor (the same precedence the unpack uses)."""
        default_style = "general"
        if self._eg is not None:
            try:
                it = self._eg.item(ci.item_id)
                if it is not None:
                    default_style = _normalize_style(it.classification)
            except Exception:  # noqa: BLE001
                log.exception("item lookup failed for %s", ci.item_id)
            try:
                adj = self._eg.adjustment(ci.item_id)
                if adj is not None and adj.style:
                    return adj.style
            except Exception:  # noqa: BLE001
                log.exception("adjustment lookup failed for %s", ci.item_id)
        return default_style or "general"

    def _on_prep_ready(self, result: PrepResult) -> None:
        """The working copy landed (off-thread decode + preview +
        Natural). Adopt it, push the item's saved state, flip the
        developed view in place, wake the tools."""
        ci = self._current_item()
        if ci is None or Path(result.path) != Path(ci.path):
            return                                  # stale — flown past
        adj = self._eg.adjustment(ci.item_id) if self._eg else None
        item_classification = None
        cls_source: Optional[str] = None
        cls_confidence: Optional[float] = None
        if self._eg is not None:
            try:
                it = self._eg.item(ci.item_id)
                if it is not None:
                    item_classification = it.classification
                    cls_source = it.classification_source
                    cls_confidence = it.classification_confidence
            except Exception:  # noqa: BLE001
                log.exception("item lookup failed for %s", ci.item_id)
        default_style = _normalize_style(item_classification)
        style, look, creative_filter, crop, angle, aspect = \
            self._unpack_adjustment(adj, default_style=default_style)
        rotation = int(getattr(adj, "rotation", 0) or 0) if adj else 0
        look_strength = (
            float(getattr(adj, "look_strength", 1.0))
            if adj is not None else 1.0)
        if style != result.style:
            # The router style moved between request and delivery
            # (cannot happen while the tools are greyed — defensive):
            # the Natural was routed for the wrong style; re-prep.
            self._prep.request(ci.path, style)
            return

        self._surface.load_prepared(
            result.full_array, result.preview_array,
            result.natural_params, style=style)
        self._cached_path = ci.path
        self._aspect_label = aspect
        self._aspect_combo.set_selected_label(aspect)
        if self._crop_overlay is not None:
            self._crop_overlay.set_aspect_ratio(aspect)
        self._surface.set_state(
            look=look, crop_norm=crop, box_angle=angle or 0.0,
            style=style, aspect_label=aspect, rotation=rotation,
            creative_filter=creative_filter,
            look_strength=look_strength,
        )
        # spec/58 §2 — the STYLE combo's classification badge follows
        # the ITEM's stored classification (not Adjustment.style).
        self._surface.set_classification_badge(cls_source, cls_confidence)
        self._set_editing_enabled(True)

    def _on_prep_failed(self, path) -> None:
        ci = self._current_item()
        if ci is None or Path(path) != Path(ci.path):
            return
        # The viewport's browse pixels (or its honest decode-failure
        # state) stay up; there is just nothing to develop.
        self._surface.clear()
        self._cached_path = ci.path

    def _unpack_adjustment(
        self, adj: Optional[m.Adjustment],
        *,
        default_style: str = "general",
    ) -> tuple[str, str, Optional[str], Optional[tuple], float, str]:
        """Decompose an Adjustment row into the surface's load shape.

        Returns ``(style, look, creative_filter, crop_norm, crop_angle,
        aspect_label)``. No row means the spec/54 defaults: Natural, no
        filter, no crop.

        ``default_style`` is the photo's effective genre when the row
        has no saved style (the wizard / classifier's choice carries
        through to the router). Falls through to ``"general"``.
        """
        style = default_style or "general"
        look = "natural"
        creative_filter: Optional[str] = None
        crop: Optional[tuple[float, float, float, float]] = None
        angle = 0.0
        aspect = self._aspect_default
        if adj is None:
            return style, look, creative_filter, crop, angle, aspect
        if adj.style:
            style = adj.style
        look = adj.look or "natural"
        creative_filter = adj.creative_filter
        if all(v is not None for v in (
                adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h)):
            crop = (adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h)
        angle = adj.crop_angle or 0.0
        aspect = adj.aspect_label or self._aspect_default
        return style, look, creative_filter, crop, angle, aspect

    def _crop_rect_for_display(
        self, w: int, h: int,
    ) -> Optional[tuple[float, float, float, float]]:
        """Test-facing resolver — the surface owns the real path.

        Honours a saved per-item crop regardless of Original vs a
        named ratio (Nelson 2026-06-09 — Original keeps the source
        aspect but the user can still crop)."""
        ci = self._current_item()
        if ci is not None and self._eg is not None:
            adj = self._eg.adjustment(ci.item_id)
            if adj is not None and all(v is not None for v in (
                    adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h)):
                return (adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h)
        ratio = get_aspect_ratio(self._aspect_label)
        if ratio.is_original:
            return None
        return compute_default_crop(w, h, ratio)

    # ── Persistence (dispatched from the surface's changed(kind)) ────

    def _on_style_decided(self, style: str) -> None:
        """spec/58 §2 — picking a style (even the one already shown) IS
        the human decision: the item's classification flips to
        ``source='user'`` and never auto-reopens (§3). The render
        routing (``Adjustment.style``) persists separately through the
        regular ``changed("style")`` path."""
        ci = self._current_item()
        if ci is None or self._eg is None:
            return
        try:
            self._eg.set_classification(ci.item_id, style, "user")
        except Exception:  # noqa: BLE001
            log.exception("style decision write failed for %s", ci.item_id)

    def _on_surface_changed(self, kind: str) -> None:
        """The surface edited something — every kind persists
        immediately (spec/54: all edits are discrete clicks now)."""
        if self._cached_path is None or self._eg is None or not self._items:
            return
        if kind in ("look", "style", "filter"):
            self._persist_choice()
            return

        ci = self._items[self._index]
        adj = self._eg.adjustment(ci.item_id) or m.Adjustment(
            item_id=ci.item_id)

        if kind == "crop":
            rect = self._surface._crop_norm
            if rect is not None:
                adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h = rect
            else:
                adj.crop_x = adj.crop_y = adj.crop_w = adj.crop_h = None
            self._eg.save_adjustment(adj)
            return

        if kind == "angle":
            adj.crop_angle = self._surface._box_angle or 0.0
            self._eg.save_adjustment(adj)
            return

        if kind == "rotation":
            # 90° image rotation (Nelson 2026-06-06). ``rotate_image``
            # cleared the crop + box angle alongside changing rotation
            # because the displayed frame's dimensions flipped — persist
            # them all in one shot so the on-disk row matches what the
            # surface shows.
            adj.rotation = int(self._surface._rotation or 0)
            adj.crop_x = adj.crop_y = adj.crop_w = adj.crop_h = None
            adj.crop_angle = 0.0
            self._eg.save_adjustment(adj)
            return

        if kind == "aspect":
            adj.aspect_label = self._surface._aspect_label
            rect = self._surface._crop_norm
            if rect is not None:
                adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h = rect
            else:
                adj.crop_x = adj.crop_y = adj.crop_w = adj.crop_h = None
            self._eg.save_adjustment(adj)
            return

        if kind == "reset":
            # Reset clears the choice + crop/angle/aspect/rotation on THIS
            # item — back to Natural, no filter. Image rotation (the 90°
            # steps) is part of the reset because it's a destructive
            # per-item override — the user expects "back to the file as
            # it was" to include any rotation they applied here.
            adj.crop_x = adj.crop_y = adj.crop_w = adj.crop_h = None
            adj.crop_angle = 0.0
            adj.rotation = 0
            adj.look = "natural"
            adj.creative_filter = None
            adj.style = None
            adj.aspect_label = None
            self._eg.save_adjustment(adj)
            return

    def _persist_choice(self) -> None:
        """Write the current CHOICE (style / look / creative_filter) to
        the item's Adjustment row."""
        if self._cached_path is None or self._eg is None or not self._items:
            return
        ci = self._items[self._index]
        try:
            adj = self._eg.adjustment(ci.item_id) or m.Adjustment(
                item_id=ci.item_id)
            state = self._surface.get_state()
            adj.style = state.style
            adj.look = state.look
            adj.creative_filter = state.creative_filter
            # Nelson 2026-06-13 Look Strength slider — clamp at the
            # gateway seam since the v4→v5 migration deliberately
            # omits the CHECK on existing rows.
            adj.look_strength = max(0.0, min(2.0, float(
                getattr(state, "look_strength", 1.0))))
            self._eg.save_adjustment(adj)
        except Exception:  # noqa: BLE001
            log.exception("persist failed for %s", ci.item_id)

    def _refresh_position_label(self) -> None:
        """Update the compact_row's "X / Y" indicator. ``_bucket_index`` /
        ``_bucket_count`` is the position in the DAY (set by the host
        when it loads a bucket). For multi-item clusters the within-
        cluster index is also shown, so the user can tell whether they're
        navigating across the day or inside a cluster."""
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

    def _on_prev(self) -> None:
        if self._busy_flag:
            return
        if self._index > 0:
            self._show(self._index - 1)
        else:
            self._emit_edge(-1)

    def _on_next(self) -> None:
        if self._busy_flag:
            return
        if self._index < len(self._items) - 1:
            self._show(self._index + 1)
        else:
            self._emit_edge(+1)

    def _on_first(self) -> None:
        if self._busy_flag:
            return
        if self._items:
            self._show(0)

    def _on_last(self) -> None:
        if self._busy_flag:
            return
        if self._items:
            self._show(len(self._items) - 1)

    def _emit_edge(self, delta: int) -> None:
        """Spec/32 §2.7: day_grid context steps the parent's cursor;
        cluster context stops at edges (no signal)."""
        if self._nav_context == "day_grid":
            self.navigate_at_edge.emit(delta)
        # cluster: stop. (Legacy bucket-list mode is gone — no buckets.)

    # ── Full-screen ──────────────────────────────────────────────────

    def _toggle_fullscreen(self) -> None:
        if self._fullscreen:
            self._exit_fullscreen()
        else:
            self._enter_fullscreen()

    def _enter_fullscreen(self) -> None:
        self._fullscreen = True
        self._fullscreen_btn.setChecked(True)
        w = self.window()
        if w is not None and w is not self and not w.isFullScreen():
            w.showFullScreen()
        self.setFocus()
        self.fullscreen_changed.emit(True)

    def _exit_fullscreen(self) -> None:
        was = self._fullscreen
        self._fullscreen = False
        self._fullscreen_btn.setChecked(False)
        w = self.window()
        if w is not None and w is not self and w.isFullScreen():
            w.showNormal()
        self.setFocus()
        if was:
            self.fullscreen_changed.emit(False)

    def _open_processed_lens(self) -> None:
        """F10 / the Full Resolution button (Nelson 2026-06-12
        standardisation): the STANDARD modal lens showing the
        PROCESSED, CROPPED image at FULL resolution — exactly what
        export produces, seen before exporting. This ADDS to the
        in-canvas Toggle-Crop preview (which keeps existing, button-
        driven, full-res-computed but canvas-fit); it never replaces
        it. No zoom/peaking tools in Edit's lens (his ruling)."""
        if self._busy_flag or not self._items:
            return
        ci = self._current_item()
        if ci is None or not is_supported(ci.path):
            return
        if self._surface._full_array is None:
            # The working copy is still preparing (the Q1 gap) — the
            # truth render has nothing honest to show yet.
            return
        with self._busy():
            pm = self._surface.render_full_pixmap()
        if pm is None or pm.isNull():
            return
        from mira.ui.media.photo_viewport import open_inspect_lens
        self._lens = open_inspect_lens(
            pm, parent=self, path=ci.path, with_tools=False)

    # ── Keyboard ─────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        from PyQt6.QtWidgets import QApplication, QLineEdit
        fw = QApplication.focusWidget()
        if isinstance(fw, QLineEdit):
            if key == Qt.Key.Key_Escape:
                fw.clearFocus()
                event.accept()
                return
            super().keyPressEvent(event)
            return
        if key == Qt.Key.Key_Escape:
            if self._fullscreen:
                self._exit_fullscreen()
            else:
                self.back_requested.emit()
            event.accept()
            return
        if key in (Qt.Key.Key_F, Qt.Key.Key_F11):
            # F / F11 — fullscreen (spec/63 §4 locked map).
            self._toggle_fullscreen()
            event.accept()
            return
        if key in (Qt.Key.Key_F1, Qt.Key.Key_Question):
            self._show_shortcuts()
            event.accept()
            return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self._on_prev()
            event.accept()
            return
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self._on_next()
            event.accept()
            return
        if key == Qt.Key.Key_Home:
            self._on_first()
            event.accept()
            return
        if key == Qt.Key.Key_End:
            self._on_last()
            event.accept()
            return
        if key == Qt.Key.Key_PageUp:
            self._emit_edge(-1)
            event.accept()
            return
        if key == Qt.Key.Key_PageDown:
            self._emit_edge(+1)
            event.accept()
            return
        if key == Qt.Key.Key_L:
            # Cycle the Look (Shift+L backwards) — spec/54 §4.2.
            delta = -1 if event.modifiers() & \
                Qt.KeyboardModifier.ShiftModifier else 1
            self._cycle_look(delta)
            event.accept()
            return
        if key == Qt.Key.Key_G:
            self._open_look_grid()
            event.accept()
            return
        if key == Qt.Key.Key_BracketLeft:
            self._box_rotate(-90)
            event.accept()
            return
        if key == Qt.Key.Key_BracketRight:
            self._box_rotate(90)
            event.accept()
            return
        # spec/66 §1.1 — Edit is creative-only: P/X/Space/C are inert
        # here (no Pick/Skip ledger to drive). The viewport still owns
        # the locked map; the page just doesn't handle them.
        if key == Qt.Key.Key_F10:
            # The standard lens (Nelson 2026-06-12): the processed,
            # cropped photo at full resolution — what export produces.
            # The in-canvas Toggle-Crop preview stays button-driven.
            self._open_processed_lens()
            event.accept()
            return
        if key == Qt.Key.Key_Backslash:
            self._compare_toggle.toggle()
            event.accept()
            return
        if key == Qt.Key.Key_R:
            self._on_reset_all()
            event.accept()
            return
        super().keyPressEvent(event)

    def _show_shortcuts(self) -> None:
        from mira.ui.base.shortcuts import show_shortcuts
        show_shortcuts(self, tr("Edit — photo"), [
            ("",                    tr("Navigate")),
            (tr("◀ / ▶ · ▲ / ▼"),    tr("Previous / next photo")),
            (tr("Home / End"),      tr("First / last photo")),
            (tr("Page Up / Down"),  tr("Previous / next day cell")),
            ("",                    tr("Develop")),
            (tr("L · Shift+L"),     tr("Next / previous Look")),
            (tr("G"),               tr("Look grid (all four side by side)")),
            (tr("[ · ]"),           tr("Rotate 90°")),
            (tr("\\"),              tr("Compare (before / after)")),
            (tr("R"),               tr("Reset this photo")),
            ("",                    tr("View")),
            (tr("F10"),             tr("Full Resolution — the processed, "
                                       "cropped photo (what export produces)")),
            (tr("F / F11"),         tr("Fullscreen")),
            (tr("Esc"),             tr("Back")),
            (tr("F1 · ?"),          tr("This help")),
        ])


__all__ = ["EditPage"]
