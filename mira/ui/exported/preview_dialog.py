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

        # spec/63 § canonical single-photo display engine. F10 opens the
        # inspection lens (full-resolution honest decode + peaking
        # tools); F11 is delegated to the host via fullscreen_requested
        # so the dialog can toggle showFullScreen / showNormal. The
        # labelled "Full Resolution" button below replaces the corner
        # 🔍 chip.
        self._viewport = PhotoViewport(self)
        self._viewport.set_corner_inspect_visible(False)
        self._viewport.set_lens_tools_visible(True)
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
        the whole ordered list to the viewport. Develop-pipeline items
        pre-render via :func:`core.preview_render.develop_photo_array`
        and ride as ``pixmap``-bearing items so the viewport shows the
        developed pixels for browse; F10 still inspects the on-disk
        file (acceptable trade-off — the staleness chip warns when the
        render is older than the recipe)."""
        from mira.ui.media.photo_viewport import ViewportItem

        viewport_items: list = []
        for it in self._items:
            pm: Optional[QPixmap] = None
            if it.develop_for_preview:
                pm = self._develop_pixmap(it)
            viewport_items.append(ViewportItem(
                path=Path(it.path) if it.path else None,
                kind="photo",
                payload=it.item_id,
                pixmap=pm,
            ))
        self._viewport.set_items(viewport_items, current=self._index)

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
        sync the dialog's index + repaint chrome for the new item."""
        if 0 <= new_index < len(self._items):
            self._index = new_index
            self._render_chrome()

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
