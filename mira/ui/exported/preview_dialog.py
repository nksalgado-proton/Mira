"""Spec/89 §3.2 / Slice 6 — the Export-surface preview viewer.

A read-only viewer that shows the **would-be or already-is shipped
pixels** for one Export-mode cell. Opens on a flat-cell center click
(:meth:`mira.ui.pages.days_grid_page.DaysGridPage._on_thumb_clicked`),
on a versions-cluster sub-grid center click, and on the keyboard
preview verb (deferred — keys still decide on the grid).

What the user sees:

* The image at full window size (centred, aspect-preserved, read from
  the on-disk file under ``Exported Media/`` for shipped cells, or
  from the source thumbnail for 0-version cells whose Mira render
  hasn't been produced yet — the live develop pipeline preview is a
  later polish pass, per spec/89 §9).
* A small action row with the current intent ("Will export" /
  "Dropped" / "Undecided"), an **Open in Editor** button (spec/89
  §3.2 D4.C), and an **Export this** button (spec/89 §5.2, disabled
  until the cell is green per D5.A).
* The locked keymap (spec/63): **P** sets intent to ``picked``, **X**
  to ``skipped``, **Space** toggles between the two, **Esc** closes,
  **←/→** step to the previous / next sibling in the surface the
  caller passed in (Block 5 D1b.A — stepping stays within the
  current surface; the caller decides whether siblings are day-grid
  flat cells or versions-cluster members).

The dialog never mutates the gateway itself — it emits high-level
signals (``intent_pick_requested`` / ``intent_skip_requested`` /
``open_editor_requested`` / ``export_this_requested``) that the
host wires to its existing verb path. The host pushes back the new
state via :meth:`set_intent_state` so the chrome stays accurate
without the dialog needing a gateway reference.
"""
from __future__ import annotations

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


#: Maximum scaled width / height for the preview image. The dialog
#: itself sizes to ~80% of the parent window; the scaled pixmap fits
#: inside that with a small bleed so the action row stays anchored.
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
    "skipped": "Dropped",
    "compare": "Undecided",
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
        self._build_ui()
        self._render_current()
        # Size to most of the parent window; the user can drag the
        # dialog corner to claim more if they want.
        if parent is not None:
            geo = parent.geometry()
            self.resize(int(geo.width() * 0.85), int(geo.height() * 0.85))
        else:
            self.resize(1200, 800)

    # ── UI ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(10)

        self._title_label = QLabel("")
        self._title_label.setObjectName("Sub")
        outer.addWidget(self._title_label)

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._image_label.setMinimumHeight(360)
        # Transparent background so the dialog's theme background
        # shows through any letterbox margins.
        self._image_label.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground)
        outer.addWidget(self._image_label, 1)

        # Action row: state chip + stale chip + step indicator + Open
        # in Editor + Export this + Close.
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

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        actions.addWidget(close_btn)

        outer.addLayout(actions)

    # ── content ─────────────────────────────────────────────────────────

    def _render_current(self) -> None:
        if not self._items:
            return
        item = self._items[self._index]
        # Store the focused item so resizeEvent's recompute path can
        # reach for the develop flag too (it bypasses _render_current
        # to avoid re-applying intent/state work on every drag).
        self._current_item = item
        self._title_label.setText(item.title or item.path.name)
        # Step indicator (1 of N).
        if len(self._items) > 1:
            self._step_label.setText(
                f"{self._index + 1} / {len(self._items)}")
        else:
            self._step_label.setText("")
        # State chip.
        label = _STATE_LABEL.get(item.state or "", "")
        self._state_chip.setText(f"Intent: {label}" if label else "")
        # spec/89 §11.3 — stale chip visibility tracks the focused
        # cell; the host computes the bool by diffing current recipe
        # vs shipped recipe_json.
        self._stale_chip.setVisible(bool(item.is_stale))
        # Export-this enablement: only fires when intent is picked AND
        # there's either no file yet (Mira-render-and-ship) or there
        # IS one (re-render with the ask-on-rerender dialog the host
        # owns; spec/89 §5.2 D6.C).
        self._export_this_btn.setEnabled(item.state == "picked")
        self._open_editor_btn.setEnabled(True)
        # Image — develop pipeline when the host asks for it
        # (0-version cells + virtual Mira members; spec/89 §11.3
        # polish), else raw file read.
        pm = self._load_pixmap_for(item)
        self._set_image(pm)

    @classmethod
    def _load_preview_pixmap(cls, path: Path) -> Optional[QPixmap]:
        """Read the would-be / already-is shipped pixels from disk —
        the on-disk-Mira-render and third-party-return path."""
        if not path.exists():
            return None
        pm = QPixmap(str(path))
        if pm.isNull():
            return None
        # Pre-scale ridiculously large images so the QLabel paint stays
        # cheap; the on-screen scale step is fast.
        if pm.width() > _PREVIEW_MAX_W or pm.height() > _PREVIEW_MAX_H:
            pm = pm.scaled(
                _PREVIEW_MAX_W, _PREVIEW_MAX_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
        return pm

    @classmethod
    def _load_pixmap_for(cls, item: "PreviewItem") -> Optional[QPixmap]:
        """Resolve one PreviewItem to a QPixmap. When the host asked
        for a live develop preview (0-version cells + virtual Mira
        members; spec/89 §11.3 polish), pipe the source photo through
        :func:`core.preview_render.develop_photo_array` so the user
        sees what the next Export run would produce. Falls back to a
        raw file read on any pipeline failure."""
        if item.develop_for_preview:
            try:
                from core.preview_render import develop_photo_array
                from mira.ui.edited.adjustment_surface import (
                    _array_to_pixmap,
                )
            except Exception:                                      # noqa: BLE001
                return cls._load_preview_pixmap(item.path)
            arr = develop_photo_array(
                item.path, item.develop_adjustment,
                style_fallback=item.develop_style_fallback,
                max_long_edge=_PREVIEW_MAX_W,
            )
            if arr is not None:
                try:
                    return _array_to_pixmap(arr)
                except Exception:                                  # noqa: BLE001
                    pass
            # Pipeline / conversion failed — fall back to source bytes
            # so the user still sees SOMETHING.
        return cls._load_preview_pixmap(item.path)

    def _set_image(self, pm: Optional[QPixmap]) -> None:
        if pm is None:
            self._image_label.setText(
                "(no preview — file is missing on disk)")
            self._image_label.setPixmap(QPixmap())
            return
        self._image_label.setText("")
        # Scale to fit the label's current size.
        target = self._image_label.size()
        if target.width() > 0 and target.height() > 0:
            scaled = pm.scaled(
                target.width(), target.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            self._image_label.setPixmap(scaled)
        else:
            self._image_label.setPixmap(pm)

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
                    self._render_current()
                return

    # ── navigation ──────────────────────────────────────────────────────

    def _step(self, delta: int) -> None:
        if not self._items:
            return
        new_idx = self._index + delta
        if new_idx < 0 or new_idx >= len(self._items):
            return
        self._index = new_idx
        self._render_current()

    # ── verbs (locked keymap, spec/63) ──────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 — Qt
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
            return
        if not self._items:
            super().keyPressEvent(event)
            return
        current = self._items[self._index]
        if key == Qt.Key.Key_P:
            self.intent_pick_requested.emit(current.item_id)
            event.accept()
            return
        if key == Qt.Key.Key_X:
            self.intent_skip_requested.emit(current.item_id)
            event.accept()
            return
        if key == Qt.Key.Key_Space:
            self.intent_toggle_requested.emit(current.item_id)
            event.accept()
            return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_PageUp):
            self._step(-1)
            event.accept()
            return
        if key in (Qt.Key.Key_Right, Qt.Key.Key_PageDown):
            self._step(+1)
            event.accept()
            return
        super().keyPressEvent(event)

    # ── button handlers ─────────────────────────────────────────────────

    def _on_open_editor(self) -> None:
        if not self._items:
            return
        self.open_editor_requested.emit(self._items[self._index].item_id)

    def _on_export_this(self) -> None:
        if not self._items:
            return
        self.export_this_requested.emit(self._items[self._index].item_id)

    # ── responsive resize ───────────────────────────────────────────────

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt
        super().resizeEvent(event)
        # Re-scale on resize so the image keeps the aspect+fit contract.
        # Dispatch through ``_load_pixmap_for`` so live-develop items
        # still see the pipeline (a raw QPixmap load would ignore the
        # develop flag and show the source bytes).
        if self._items:
            pm = self._load_pixmap_for(self._items[self._index])
            self._set_image(pm)


__all__ = ["ExportPreviewDialog", "PreviewItem"]
