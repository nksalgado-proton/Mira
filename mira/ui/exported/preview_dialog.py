"""Spec/89 §3.2 / Slice 6 — the Export-surface preview viewer.

A read-only viewer that shows the **would-be or already-is shipped
pixels** for one Export-mode cell. Opens on a flat-cell center click
(:meth:`mira.ui.pages.days_grid_page.DaysGridPage._on_thumb_clicked`),
on a versions-cluster sub-grid center click, and on the keyboard
preview verb (deferred — keys still decide on the grid).

**spec/63 reuse (2026-06-19 polish).** The dialog body is the canonical
:class:`~mira.ui.media.photo_viewport.PhotoViewport` — the same engine
the Picker, Editor and Cut surfaces use, so F10 (Full Resolution
inspection lens) and the locked P/X/Space/Esc/← →/F11 keymap all wire
for free. The action row gains **Full Resolution F10** + **Full Screen
F11** ghost buttons that match the labels Picker exposes.

What the user sees:

* The image at full window size (the viewport handles letterboxing +
  the soft blurred backdrop). For shipped cells the viewport loads
  the on-disk file; for 0-version cells + virtual Mira cluster
  members the host pre-renders the develop pipeline via
  :func:`core.preview_render.develop_photo_array` and hands the
  developed pixmap in through :class:`ViewportItem.pixmap`.
* A small action row with the current intent ("Will export" /
  "Set aside" / "Undecided"), an **Open in Editor** button (spec/89
  §3.2 D4.C), an **Export this** button (spec/89 §5.2, disabled
  until the cell is Will export per D5.A), and the **Full Resolution
  F10** / **Full Screen F11** centre pair.
* The locked keymap (spec/63): **P** sets intent to ``picked``, **X**
  to ``skipped``, **Space** toggles between the two, **F10** opens
  the full-resolution inspection lens, **F/F11** toggles the
  dialog's fullscreen mode, **Esc** closes, **←/→** step to the
  previous / next sibling in the surface the caller passed in
  (Block 5 D1b.A — stepping stays within the current surface; the
  caller decides whether siblings are day-grid flat cells or
  versions-cluster members).

The dialog never mutates the gateway itself — it emits high-level
signals (``intent_pick_requested`` / ``intent_skip_requested`` /
``open_editor_requested`` / ``export_this_requested``) that the
host wires to its existing verb path. The host pushes back the new
state via :meth:`set_intent_state` so the chrome stays accurate
without the dialog needing a gateway reference.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger(__name__)


#: Maximum scaled width / height for fallback bitmap loads (the
#: develop-pipeline preview also clamps to this). The PhotoViewport
#: itself decodes the source at full res when needed for F10 truth.
_PREVIEW_MAX_W = 2400
_PREVIEW_MAX_H = 1600


@dataclass(frozen=True)
class PreviewItem:
    """One entry in the preview's neighbour list. The caller hands the
    dialog a sequence of these plus the starting index; ←/→ step
    through them in order."""

    item_id: str
    path: Path
    state: Optional[str] = None      # 'picked' / 'skipped' / 'compare' / None
    has_shipped_file: bool = False   # drives "Export this" enable + label
    title: str = ""                  # header label (e.g. filename)
    # spec/89 §11.3 polish — when the live Adjustment row would render
    # a different recipe than the on-disk Mira version's recorded
    # recipe_json, the dialog paints a "Adjustments changed — Export
    # to refresh" chip so the user knows the preview pixels lag behind
    # the current edit state. Computed by the host (see
    # DaysGridPage._open_export_preview); always False for third-party
    # cells (their recipe is the file itself).
    is_stale: bool = False
    # spec/89 §11.3 polish — for 0-version cells + virtual Mira
    # members the dialog runs ``core.preview_render.develop_photo_array``
    # against ``develop_adjustment`` (the source's live Adjustment row)
    # so the user sees the actual would-be-shipped pixels instead of
    # the raw source. ``path`` is then the source photo's absolute
    # path; the on-disk Mira render doesn't exist yet.
    develop_for_preview: bool = False
    develop_adjustment: Any = None
    develop_style_fallback: str = "general"


_STATE_LABEL = {
    "picked": "Will export",
    "skipped": "Set aside",
    "compare": "Undecided",
    "candidate": "Undecided",
}


class ExportPreviewDialog(QDialog):
    """Read-only preview viewer for Export-mode cells (spec/89 §3.2)."""

    intent_pick_requested = pyqtSignal(str)     # item_id
    intent_skip_requested = pyqtSignal(str)     # item_id
    intent_toggle_requested = pyqtSignal(str)   # item_id (Space)
    open_editor_requested = pyqtSignal(str)     # item_id
    export_this_requested = pyqtSignal(str)     # item_id

    def __init__(
        self,
        items: List[PreviewItem],
        start_index: int = 0,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ExportPreviewDialog")
        self.setWindowTitle("Preview")
        self.setModal(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._items = list(items)
        self._index = max(0, min(start_index, len(self._items) - 1))
        self._was_fullscreen = False
        self._build_ui()
        self._load_viewport()
        self._render_chrome()
        # Size to most of the parent window; the user can drag the
        # dialog corner to claim more if they want.
        if parent is not None:
            geo = parent.geometry()
            self.resize(int(geo.width() * 0.85), int(geo.height() * 0.85))
        else:
            self.resize(1200, 800)

    # ── UI ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        from mira.ui.design import ghost_button
        from mira.ui.i18n import tr
        from mira.ui.media.photo_viewport import PhotoViewport

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(10)

        self._title_label = QLabel("")
        self._title_label.setObjectName("Sub")
        outer.addWidget(self._title_label)

        # spec/63 § canonical single-photo display engine. F10 opens
        # the inspection lens (full-resolution honest decode + peaking
        # tools); F11 is delegated to the host via fullscreen_requested
        # so the dialog can toggle showFullScreen / showNormal. The
        # labelled "Full Resolution" button below replaces the corner
        # 🔍 chip.
        #
        # **Truth-override (Nelson 2026-06-19).** We opt OUT of the
        # viewport's internal F10 handler (``set_truth_internal(False)``)
        # so the dialog can decide per-item: develop-pipeline cells
        # (0-version + virtual Mira members) render the lens base at
        # full resolution through ``develop_photo_array`` so the user
        # inspects the WOULD-BE-EXPORTED pixels, not the raw source.
        # On-disk versions (Mira renders + third-party returns) still
        # open the file directly — the file IS the export.
        self._viewport = PhotoViewport(self)
        self._viewport.set_corner_inspect_visible(False)
        self._viewport.set_lens_tools_visible(True)
        self._viewport.set_truth_internal(False)
        self._viewport.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._viewport.setMinimumHeight(360)
        outer.addWidget(self._viewport, 1)
        self._viewport.pick_requested.connect(self._on_pick_key)
        self._viewport.skip_requested.connect(self._on_skip_key)
        self._viewport.toggle_requested.connect(self._on_toggle_key)
        self._viewport.fullscreen_requested.connect(self._toggle_fullscreen)
        self._viewport.back_requested.connect(self.reject)
        self._viewport.current_changed.connect(self._on_viewport_step)
        self._viewport.truth_requested.connect(self._on_truth_requested)
        # Track the currently-open inspection lens so a follow-up F10
        # closes it instead of stacking another window.
        self._inspect_window = None

        # Action row: state chip + stale chip + step indicator + Open
        # in Editor + Export this + Full Resolution + Full Screen + Close.
        actions = QHBoxLayout()
        actions.setSpacing(10)

        self._state_chip = QLabel("")
        self._state_chip.setObjectName("Sub")
        actions.addWidget(self._state_chip)

        # spec/89 §11.3 — staleness chip: the on-disk render lags
        # behind the live Adjustment row (current recipe ≠ shipped
        # recipe_json). The "Warn" object name picks up the warning
        # palette token. Hidden unless the current item is stale.
        self._stale_chip = QLabel("Adjustments changed — Export to refresh")
        self._stale_chip.setObjectName("Warn")
        self._stale_chip.setToolTip(
            "The on-disk JPEG was rendered with older adjustments. "
            "Re-running Export now will refresh it.")
        self._stale_chip.setVisible(False)
        actions.addWidget(self._stale_chip)

        actions.addStretch(1)

        self._step_label = QLabel("")
        self._step_label.setObjectName("Faint")
        actions.addWidget(self._step_label)

        actions.addStretch(1)

        self._open_editor_btn = QPushButton("Open in Editor")
        self._open_editor_btn.clicked.connect(self._on_open_editor)
        actions.addWidget(self._open_editor_btn)

        self._export_this_btn = QPushButton("Export this")
        self._export_this_btn.clicked.connect(self._on_export_this)
        self._export_this_btn.setToolTip(
            "Render and ship this one item now. Disabled until the cell "
            "is Will export (press P).")
        actions.addWidget(self._export_this_btn)

        # spec/63 §4 / Picker-parity pair — the same labels Picker uses
        # for the F10 / F11 affordances, wired through the viewport's
        # canonical signals so the inspection lens is the same one
        # every photo surface opens.
        self._fullres_btn = ghost_button(tr("Full Resolution  F10"))
        self._fullres_btn.setToolTip(tr(
            "Inspect this frame at full resolution — peaking, true 1:1 "
            "zoom, AF point  (F10)"))
        self._fullres_btn.clicked.connect(
            self._viewport.truth_requested.emit)
        actions.addWidget(self._fullres_btn)

        self._fullscreen_btn = ghost_button(tr("Full Screen  F11"))
        self._fullscreen_btn.setCheckable(True)
        self._fullscreen_btn.setToolTip(tr(
            "Use the whole screen for inspection  (F / F11)"))
        self._fullscreen_btn.clicked.connect(self._toggle_fullscreen)
        actions.addWidget(self._fullscreen_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        actions.addWidget(close_btn)

        outer.addLayout(actions)

    # ── Viewport hand-off ───────────────────────────────────────────────

    def _load_viewport(self) -> None:
        """Translate every :class:`PreviewItem` to a
        :class:`~mira.ui.media.photo_viewport.ViewportItem` and hand
        the whole ordered list to the viewport.

        **Lazy develop (Nelson 2026-06-19).** Build all items with
        ``pixmap=None`` so the viewport's normal source-decode path
        runs — the dialog paints the raw source quickly and the user
        sees a photo within a few hundred ms instead of waiting for
        N sequential develop pipelines (one per neighbour). The
        focused item's developed pixmap is rendered deferred, after
        the dialog paints, via :meth:`_schedule_develop_current`; the
        viewport's :meth:`set_rendered_pixmap` swaps the developed
        pixels in over the raw source. The same happens on every
        step (← / →), so neighbours only pay the develop cost when
        the user actually visits them. F10 still inspects the on-disk
        source file (acceptable trade-off — the staleness chip warns
        when the render is older than the recipe)."""
        from mira.ui.media.photo_viewport import ViewportItem

        viewport_items: list = []
        for it in self._items:
            viewport_items.append(ViewportItem(
                path=Path(it.path) if it.path else None,
                kind="photo",
                payload=it.item_id,
                pixmap=None,
            ))
        # Cache of develop-pipeline pixmaps keyed by item_id so a
        # second step back to the same neighbour re-uses the work
        # instead of decoding again.
        self._develop_cache: dict = {}
        self._viewport.set_items(viewport_items, current=self._index)
        # Render the focused item's developed pixmap AFTER the dialog
        # paints. ``QTimer.singleShot(0, ...)`` defers to the next
        # event-loop pass so the user sees the raw source first.
        self._schedule_develop_current()

    def _schedule_develop_current(self) -> None:
        """Kick the develop pipeline for the currently-focused item
        after the dialog has painted. No-op for items that don't
        need developing (on-disk versions render straight from
        ``ViewportItem.path``)."""
        if not (0 <= self._index < len(self._items)):
            return
        item = self._items[self._index]
        if not item.develop_for_preview:
            return
        # Cache hit — paint immediately.
        cached = self._develop_cache.get(item.item_id)
        if cached is not None:
            self._viewport.set_rendered_pixmap(cached)
            return
        # Defer the expensive decode + pipeline to the next event-
        # loop pass so the dialog can paint the raw source first.
        from PyQt6.QtCore import QTimer
        target_id = item.item_id
        QTimer.singleShot(
            0, lambda: self._run_develop_for(target_id))

    def _run_develop_for(self, item_id: str) -> None:
        """Run the develop pipeline for ``item_id`` and, if the
        viewport is still on that item, swap the developed pixmap
        in over the raw source. A wait cursor signals the user the
        render is in flight (the pipeline blocks the UI thread; a
        proper QThreadPool offload is a follow-up if 1-3 s renders
        feel sluggish in practice)."""
        from PyQt6.QtCore import Qt as _Qt
        from PyQt6.QtGui import QCursor, QGuiApplication

        # Resolve back from the item_id (the dialog may have advanced
        # while we were queued).
        item = next(
            (it for it in self._items if it.item_id == item_id), None)
        if item is None or not item.develop_for_preview:
            return
        QGuiApplication.setOverrideCursor(QCursor(_Qt.CursorShape.WaitCursor))
        try:
            pm = self._develop_pixmap(item)
        finally:
            QGuiApplication.restoreOverrideCursor()
        if pm is None:
            return
        self._develop_cache[item_id] = pm
        # Only paint over if the user hasn't stepped away in the
        # meantime — otherwise the next _schedule_develop_current
        # will repaint with the correct item's pixmap.
        if (0 <= self._index < len(self._items)
                and self._items[self._index].item_id == item_id):
            self._viewport.set_rendered_pixmap(pm)

    # ── F10 — full-resolution inspection of the EXPORTED pixels ─────────

    def _on_truth_requested(self) -> None:
        """spec/89 §3.2 + spec/63 §4 (Nelson 2026-06-19) — F10 opens
        the inspection lens with the **would-be-exported** pixels:

        * Develop-pipeline items (0-version + virtual Mira members)
          run :func:`core.preview_render.develop_photo_array` at full
          resolution (no max-edge cap) so the lens shows what the
          next Export run will produce, including look / filter /
          crop. This blocks under a wait cursor — the user pressed
          F10 deliberately and a 1-3 s render for full-res inspection
          is acceptable.
        * On-disk items (Mira renders + third-party returns) open the
          file directly via the standard
          :class:`mira.ui.media.photo_viewport._InspectView` path —
          the file IS the export, no pipeline to run."""
        from pathlib import Path as _Path
        from PyQt6.QtCore import Qt as _Qt
        from PyQt6.QtGui import QCursor, QGuiApplication

        if not (0 <= self._index < len(self._items)):
            return
        item = self._items[self._index]
        # A second F10 closes any already-open lens (mirror the
        # PhotoViewport _on_truth_requested contract: F10 is a toggle).
        if self._inspect_window is not None:
            try:
                self._inspect_window.close()
            except Exception:                                       # noqa: BLE001
                pass
            self._inspect_window = None
            return

        base = None
        path = _Path(item.path) if item.path else None
        if item.develop_for_preview:
            QGuiApplication.setOverrideCursor(
                QCursor(_Qt.CursorShape.WaitCursor))
            try:
                base = self._develop_pixmap_full(item)
            finally:
                QGuiApplication.restoreOverrideCursor()
            if base is None:
                return
        else:
            # Mirror PhotoViewport._honest_full_res — load JPEG/HEIC
            # directly, RAW via half-res sensor demosaic. Wait cursor
            # in case the source is huge.
            from mira.ui.media.image_loader import (
                _RAW_EXTENSIONS, load_pixmap, load_raw_half_res,
            )
            from PyQt6.QtGui import QPixmap as _QPixmap

            if path is None or not path.is_file():
                return
            is_raw = path.suffix.lower() in _RAW_EXTENSIONS
            QGuiApplication.setOverrideCursor(
                QCursor(_Qt.CursorShape.WaitCursor))
            try:
                if is_raw:
                    img = load_raw_half_res(path)
                    base = (
                        _QPixmap.fromImage(img)
                        if not img.isNull() else load_pixmap(path))
                else:
                    base = load_pixmap(path)
            finally:
                QGuiApplication.restoreOverrideCursor()
            if base is None or base.isNull():
                return

        # Open the canonical inspection lens with the EXPORTED pixels.
        from mira.ui.media.photo_viewport import _InspectView
        is_raw = (
            path is not None
            and path.suffix.lower() in {
                ".rw2", ".cr2", ".cr3", ".nef", ".arw", ".raf",
                ".orf", ".dng",
            })
        # Develop-pipeline items don't behave as raw: the developed
        # pixmap is an 8-bit composite, not a sensor demosaic.
        if item.develop_for_preview:
            is_raw = False
        self._inspect_window = _InspectView(
            base, None, path=path, is_raw=is_raw,
            with_tools=True, parent=self,
        )
        self._inspect_window.open_windowed()
        self._inspect_window.setFocus()

    @classmethod
    def _develop_pixmap_full(cls, item: "PreviewItem") -> Optional[QPixmap]:
        """Full-resolution develop for F10 inspection — no max-edge
        cap. The 2400-px-bound :meth:`_develop_pixmap` covers the
        dialog body's lazy preview; this one is for the lens."""
        try:
            from core.preview_render import develop_photo_array
            from mira.ui.edited.adjustment_surface import _array_to_pixmap
        except Exception:                                          # noqa: BLE001
            log.exception("preview-dialog: full-develop imports failed")
            return None
        arr = develop_photo_array(
            item.path, item.develop_adjustment,
            style_fallback=item.develop_style_fallback,
            max_long_edge=0,        # 0 disables the downscale
        )
        if arr is None:
            return None
        try:
            return _array_to_pixmap(arr)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "preview-dialog: full-res _array_to_pixmap failed")
            return None

    @classmethod
    def _develop_pixmap(cls, item: "PreviewItem") -> Optional[QPixmap]:
        """Run one item through the develop pipeline (spec/89 §11.3
        polish). Returns ``None`` on any failure so the viewport falls
        back to the raw source decode."""
        try:
            from core.preview_render import develop_photo_array
            from mira.ui.edited.adjustment_surface import _array_to_pixmap
        except Exception:                                          # noqa: BLE001
            log.exception("preview-dialog: develop imports failed")
            return None
        arr = develop_photo_array(
            item.path, item.develop_adjustment,
            style_fallback=item.develop_style_fallback,
            max_long_edge=_PREVIEW_MAX_W,
        )
        if arr is None:
            return None
        try:
            return _array_to_pixmap(arr)
        except Exception:                                          # noqa: BLE001
            log.exception("preview-dialog: _array_to_pixmap failed")
            return None

    # ── Compatibility helpers ───────────────────────────────────────────

    @classmethod
    def _load_preview_pixmap(cls, path: Path) -> Optional[QPixmap]:
        """Read a file from disk to a QPixmap, downscaled to the
        dialog's max edges. Kept as a classmethod helper so the
        :class:`~mira.ui.exported.compare_dialog._CompareTile` can
        re-use the same load-or-fail logic without instantiating the
        dialog."""
        if not path.exists():
            return None
        pm = QPixmap(str(path))
        if pm.isNull():
            return None
        if pm.width() > _PREVIEW_MAX_W or pm.height() > _PREVIEW_MAX_H:
            pm = pm.scaled(
                _PREVIEW_MAX_W, _PREVIEW_MAX_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
        return pm

    @classmethod
    def _load_pixmap_for(cls, item: "PreviewItem") -> Optional[QPixmap]:
        """Resolve one PreviewItem to a QPixmap — develop pipeline
        when the host asked for it, else raw file read. Used by the
        compare dialog's tiles."""
        if item.develop_for_preview:
            pm = cls._develop_pixmap(item)
            if pm is not None:
                return pm
        return cls._load_preview_pixmap(item.path)

    # ── Chrome refresh ──────────────────────────────────────────────────

    def _render_chrome(self) -> None:
        """Re-paint title / state chip / stale chip / step label /
        button-enabled state for the currently focused item. Called
        from :meth:`_load_viewport` (initial paint), from
        :meth:`_on_viewport_step` (← → navigation), and from
        :meth:`set_intent_state` (state push from the host)."""
        if not self._items:
            return
        item = self._items[self._index]
        self._title_label.setText(item.title or item.path.name)
        if len(self._items) > 1:
            self._step_label.setText(
                f"{self._index + 1} / {len(self._items)}")
        else:
            self._step_label.setText("")
        label = _STATE_LABEL.get(item.state or "", "")
        self._state_chip.setText(f"Intent: {label}" if label else "")
        self._stale_chip.setVisible(bool(item.is_stale))
        self._export_this_btn.setEnabled(item.state == "picked")
        self._open_editor_btn.setEnabled(True)

    # ── intent updates from the host ────────────────────────────────────

    def set_intent_state(self, item_id: str, new_state: str) -> None:
        """Push a new intent state for one item — keeps the chrome
        in sync without forcing the host to round-trip through the
        gateway just to update the chip. The next neighbour-step or
        the close path will read the latest state from the list."""
        for i, it in enumerate(self._items):
            if it.item_id == item_id:
                self._items[i] = PreviewItem(
                    item_id=it.item_id, path=it.path,
                    state=new_state,
                    has_shipped_file=it.has_shipped_file,
                    title=it.title,
                    is_stale=it.is_stale,
                    develop_for_preview=it.develop_for_preview,
                    develop_adjustment=it.develop_adjustment,
                    develop_style_fallback=it.develop_style_fallback,
                )
                if i == self._index:
                    self._render_chrome()
                return

    # ── Fullscreen (F11) ────────────────────────────────────────────────

    def _toggle_fullscreen(self) -> None:
        """Toggle between fullscreen + windowed. The viewport's
        ``fullscreen_requested`` signal (F / F11 inside the viewport)
        and the labelled button both route here. Also keeps the
        button's checked state in sync with the dialog's window
        state."""
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        try:
            self._fullscreen_btn.setChecked(self.isFullScreen())
        except Exception:                                          # noqa: BLE001
            pass

    # ── Viewport signal handlers ────────────────────────────────────────

    def _current_item_id(self) -> Optional[str]:
        if 0 <= self._index < len(self._items):
            return self._items[self._index].item_id
        return None

    def _on_viewport_step(self, new_index: int) -> None:
        """Viewport advanced (← → keys, prev/next buttons, etc.) —
        sync the dialog's index, repaint chrome for the new item, and
        kick the lazy develop pipeline for it (no-op for items that
        don't need developing or for cache-hits from a previous
        visit)."""
        if 0 <= new_index < len(self._items):
            self._index = new_index
            self._render_chrome()
            self._schedule_develop_current()

    def _on_pick_key(self) -> None:
        target = self._current_item_id()
        if target is not None:
            self.intent_pick_requested.emit(target)

    def _on_skip_key(self) -> None:
        target = self._current_item_id()
        if target is not None:
            self.intent_skip_requested.emit(target)

    def _on_toggle_key(self) -> None:
        target = self._current_item_id()
        if target is not None:
            self.intent_toggle_requested.emit(target)

    # ── verbs (locked keymap, spec/63) ──────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 — Qt
        """The viewport owns most of spec/63 (P / X / Space / F10 /
        Esc); the dialog handles the remainder — ← →, F11, F (the
        Picker's fullscreen alias)."""
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
            return
        if key in (Qt.Key.Key_F11, Qt.Key.Key_F):
            self._toggle_fullscreen()
            event.accept()
            return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_PageUp):
            self._viewport.show_index(self._index - 1)
            event.accept()
            return
        if key in (Qt.Key.Key_Right, Qt.Key.Key_PageDown):
            self._viewport.show_index(self._index + 1)
            event.accept()
            return
        super().keyPressEvent(event)

    # ── button handlers ─────────────────────────────────────────────────

    def _on_open_editor(self) -> None:
        target = self._current_item_id()
        if target is not None:
            self.open_editor_requested.emit(target)

    def _on_export_this(self) -> None:
        target = self._current_item_id()
        if target is not None:
            self.export_this_requested.emit(target)


__all__ = ["ExportPreviewDialog", "PreviewItem"]
