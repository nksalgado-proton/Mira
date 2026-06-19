"""Spec/89 §11.3 — side-by-side compare for a versions cluster.

The user opens this from the **Compare** button on the versions
cluster sub-grid toolbar (DaysGridPage gates the button on
``self._mode == "cluster"`` AND ``cluster.kind == "versions"``).

What the user sees:

* Every version of the source item, laid out horizontally (or scrolled
  horizontally when the count overflows the dialog). Each tile shows
  the would-be / already-is shipped pixels at fit-to-tile size — same
  semantic as :class:`~mira.ui.exported.preview_dialog.ExportPreviewDialog`,
  so the user is staring at the actual export pixels: the live Mira
  develop pipeline for virtual Mira members and 0-version cells, and
  the on-disk file for shipped Mira renders and third-party returns.
* A small caption under each tile naming the provenance ("Mira" /
  "LRC" / "Helicon" / …).
* A coloured 3-px border on each tile encoding the version's current
  intent — green Will export, red Set aside, orange Undecided.

What the user can do:

* **Click a tile's border** to cycle that version's state
  ``picked ↔ skipped`` (the historical ComparePage cycle: K↔D only;
  the user came here to finalise, not to re-mark as Compare).
* **Esc** closes the dialog (the host's grid re-reads gateway state
  on return).

The dialog never mutates the gateway itself — it emits
``intent_pick_requested`` / ``intent_skip_requested`` /
``intent_toggle_requested(item_id)`` and the host routes through its
existing ``_apply_version_verb_at_index`` path. The host pushes new
state back via :meth:`set_intent_state` so the chrome stays accurate
without a gateway round-trip.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeyEvent, QPalette, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mira.ui.palette import PALETTE


_TILE_MIN_WIDTH = 320
_TILE_DEFAULT_WIDTH = 540
_TILE_BORDER_WIDTH = 3


@dataclass(frozen=True)
class CompareItem:
    """One version to compare. The dialog renders these side-by-side.

    Mirrors :class:`mira.ui.exported.preview_dialog.PreviewItem`'s
    develop-pipeline contract — pass ``develop_for_preview=True`` for
    a 0-version cell or virtual Mira member so the dialog runs the
    source through :func:`core.preview_render.develop_photo_array`
    instead of reading the file raw."""

    item_id: str
    path: Path
    state: Optional[str] = None      # 'picked' / 'skipped' / 'compare' / 'candidate' / None
    title: str = ""                  # caption under the tile (e.g. "Mira" / "LRC")
    develop_for_preview: bool = False
    develop_adjustment: Any = None
    develop_style_fallback: str = "general"


_STATE_LABEL = {
    "picked": "Will export",
    "skipped": "Set aside",
    "compare": "Undecided",
    "candidate": "Undecided",
}


def _border_color_for_state(state: Optional[str]) -> str:
    """spec/89 §1.1 / §4.2 — the locked colour grammar:
    green=Will export · red=Set aside · orange=Undecided.

    Pulls from the active :data:`mira.ui.palette.PALETTE` so light /
    dark themes both work. Unknown / missing states paint the neutral
    line colour so the tile still has a visible border."""
    from mira.ui.pages.days_lists_page import _palette_mode
    p = PALETTE[_palette_mode()]
    if state == "picked":
        return p.get("green", "#2da44e")
    if state == "skipped":
        return p.get("red", "#cf222e")
    if state in ("compare", "candidate"):
        return p.get("compare", "#fb8500")
    return p.get("line", "#cccccc")


class _CompareTile(QFrame):
    """One tile inside the compare dialog. A QFrame with a coloured
    border that reflects the version's intent state, a fit-to-tile
    image, and a caption line under the image. Clicking anywhere on
    the tile cycles the state (``picked ↔ skipped``) by emitting the
    parent dialog's toggle signal. A mouse click also moves keyboard
    focus to this tile so the next P/X/Space keypress acts on it."""

    toggled = pyqtSignal(str)             # item_id
    focused = pyqtSignal(str)             # item_id (mouse-focus the tile)

    def __init__(
        self,
        item: CompareItem,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._item = item
        self._raw_pixmap: Optional[QPixmap] = None
        self._focused = False
        self.setMinimumWidth(_TILE_MIN_WIDTH)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Frame chrome: solid 3-px border whose colour is repainted by
        # _refresh_border on every state change.
        self.setFrameShape(QFrame.Shape.Box)
        self.setLineWidth(_TILE_BORDER_WIDTH)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._image_label.setMinimumHeight(280)
        outer.addWidget(self._image_label, 1)

        self._title_label = QLabel(item.title or "")
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_label.setObjectName("Sub")
        outer.addWidget(self._title_label)

        self._state_chip = QLabel("")
        self._state_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._state_chip.setObjectName("Faint")
        outer.addWidget(self._state_chip)

        self.set_state(item.state)
        self._load_pixmap()

    # ── Public API ──────────────────────────────────────────────────

    def item_id(self) -> str:
        return self._item.item_id

    def set_state(self, state: Optional[str]) -> None:
        """Repaint the border + state chip for ``state``."""
        self._item = CompareItem(
            item_id=self._item.item_id, path=self._item.path,
            state=state, title=self._item.title,
            develop_for_preview=self._item.develop_for_preview,
            develop_adjustment=self._item.develop_adjustment,
            develop_style_fallback=self._item.develop_style_fallback,
        )
        self._refresh_border()
        self._state_chip.setText(
            _STATE_LABEL.get(state or "", ""))

    def set_focused(self, focused: bool) -> None:
        """Paint the focus halo (or remove it). The focused tile is
        the implicit target of P / X / Space keypresses inside the
        dialog (spec/63 locked keymap)."""
        if self._focused == bool(focused):
            return
        self._focused = bool(focused)
        self._refresh_border()

    # ── Internals ───────────────────────────────────────────────────

    def _refresh_border(self) -> None:
        color = _border_color_for_state(self._item.state)
        # Inline stylesheet on the QFrame's border-colour token. The
        # rest of the dialog uses theme-driven QSS; only the per-state
        # border colour varies per tile so we paint it directly. The
        # focus halo lands as an inner-padding ring (extra padding
        # plus a fainter outline) so it reads distinctly from the
        # state border without changing tile geometry.
        focus_rule = ""
        if self._focused:
            from mira.ui.pages.days_lists_page import _palette_mode
            ring = PALETTE[_palette_mode()].get("accent", "#0969da")
            focus_rule = (
                f"_CompareTile {{ outline: 2px solid {ring}; "
                f"outline-offset: -1px; }}")
        self.setStyleSheet(
            f"_CompareTile {{ border: {_TILE_BORDER_WIDTH}px solid "
            f"{color}; border-radius: 6px; }} {focus_rule}")

    def _load_pixmap(self) -> None:
        """Load the tile's full-definition pixels — develop pipeline
        when the host asked for it, else raw file read."""
        from mira.ui.exported.preview_dialog import ExportPreviewDialog
        # We piggy-back on ExportPreviewDialog._load_pixmap_for so the
        # develop-vs-file dispatch stays in one place. The class is
        # constructed only for the helper method (no widget mount).
        from mira.ui.exported.preview_dialog import PreviewItem as _PI
        proxy = _PI(
            item_id=self._item.item_id,
            path=self._item.path,
            develop_for_preview=self._item.develop_for_preview,
            develop_adjustment=self._item.develop_adjustment,
            develop_style_fallback=self._item.develop_style_fallback,
        )
        pm = ExportPreviewDialog._load_pixmap_for(proxy)
        self._raw_pixmap = pm
        self._paint_pixmap()

    def _paint_pixmap(self) -> None:
        if self._raw_pixmap is None or self._raw_pixmap.isNull():
            self._image_label.setText(
                "(no preview — file missing or render failed)")
            self._image_label.setPixmap(QPixmap())
            return
        self._image_label.setText("")
        target = self._image_label.size()
        if target.width() <= 0 or target.height() <= 0:
            self._image_label.setPixmap(self._raw_pixmap)
            return
        scaled = self._raw_pixmap.scaled(
            target.width(), target.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self._image_label.setPixmap(scaled)

    # ── Qt overrides ────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:                  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            # Move focus FIRST so a subsequent keyboard verb hits this
            # tile, THEN toggle. The two emits are decoupled so a
            # test / host can hook them independently.
            self.focused.emit(self._item.item_id)
            self.toggled.emit(self._item.item_id)
            event.accept()
            return
        super().mousePressEvent(event)

    def resizeEvent(self, event) -> None:                      # noqa: N802
        super().resizeEvent(event)
        # Re-fit on every resize so the image keeps the aspect+fit
        # contract as the dialog stretches.
        self._paint_pixmap()


class CompareVersionsDialog(QDialog):
    """Side-by-side compare for the versions cluster sub-grid
    (spec/89 §11.3). Modal, host pattern matches ExportPreviewDialog.

    Tiles render every version at fit-to-tile size; clicking a tile
    cycles its intent state ``picked ↔ skipped`` via
    :sig:`intent_toggle_requested`. Esc closes."""

    intent_pick_requested = pyqtSignal(str)     # item_id
    intent_skip_requested = pyqtSignal(str)     # item_id
    intent_toggle_requested = pyqtSignal(str)   # item_id

    def __init__(
        self,
        items: List[CompareItem],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CompareVersionsDialog")
        self.setWindowTitle("Compare versions")
        self.setModal(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._items: List[CompareItem] = list(items)
        self._tiles: List[_CompareTile] = []
        # spec/63 locked keymap — the focused tile is the implicit
        # target of P / X / Space. Tiles 0..N-1; the first tile starts
        # focused so the user can act immediately without clicking.
        self._focused_index = 0 if items else -1
        self._build_ui()
        if self._tiles:
            self._tiles[self._focused_index].set_focused(True)
        if parent is not None:
            geo = parent.geometry()
            self.resize(int(geo.width() * 0.92), int(geo.height() * 0.88))
        else:
            target_w = max(900, _TILE_DEFAULT_WIDTH * min(3, len(items)))
            self.resize(target_w, 760)

    # ── UI ──────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        from mira.ui.i18n import tr

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(10)

        hint = QLabel(tr(
            "Click a tile, or use ← → to focus. "
            "P Will-export · X Set-aside · Space toggle · Esc closes."
        ))
        hint.setObjectName("Sub")
        outer.addWidget(hint)

        # Tile row inside a scroll area — handles 4+-version clusters
        # without resizing the dialog past its parent. ≤3 versions fit
        # the dialog at its default size without scrolling.
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll, 1)

        host = QWidget()
        host_layout = QHBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(12)
        for item in self._items:
            tile = _CompareTile(item, parent=host)
            tile.toggled.connect(self.intent_toggle_requested.emit)
            tile.focused.connect(self._on_tile_focused)
            host_layout.addWidget(tile, 1)
            self._tiles.append(tile)
        scroll.setWidget(host)

    # ── intent updates from the host ────────────────────────────────

    def set_intent_state(self, item_id: str, new_state: str) -> None:
        """Push a new intent state for one tile — the host calls this
        after :meth:`intent_toggle_requested` fires so the tile's
        border + chip re-paint without a full rebuild."""
        for tile in self._tiles:
            if tile.item_id() == item_id:
                tile.set_state(new_state or None)
                return

    # ── Focus + keymap (spec/63) ────────────────────────────────────

    def _on_tile_focused(self, item_id: str) -> None:
        """Mouse-focus signal from a tile — sync the dialog's
        ``_focused_index`` so the next keyboard verb hits the same
        tile the user clicked on."""
        for i, tile in enumerate(self._tiles):
            if tile.item_id() == item_id:
                self._set_focused_index(i)
                return

    def _set_focused_index(self, idx: int) -> None:
        """Move the focus halo from the current tile to ``idx``. No-op
        when ``idx`` is out of range or already focused."""
        if idx < 0 or idx >= len(self._tiles) or idx == self._focused_index:
            return
        if 0 <= self._focused_index < len(self._tiles):
            self._tiles[self._focused_index].set_focused(False)
        self._focused_index = idx
        self._tiles[idx].set_focused(True)

    def _step_focus(self, delta: int) -> None:
        new_idx = self._focused_index + delta
        if 0 <= new_idx < len(self._tiles):
            self._set_focused_index(new_idx)

    def _focused_item_id(self) -> Optional[str]:
        if 0 <= self._focused_index < len(self._tiles):
            return self._tiles[self._focused_index].item_id()
        return None

    # ── Qt overrides ────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent) -> None:         # noqa: N802
        """spec/63 locked keymap inside the dialog:
        ← / →   move focus to the previous / next tile
        P       Will export (focused tile)
        X       Set aside (focused tile)
        Space   toggle picked ↔ skipped (focused tile)
        Esc     close the dialog
        """
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
            return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_PageUp):
            self._step_focus(-1)
            event.accept()
            return
        if key in (Qt.Key.Key_Right, Qt.Key.Key_PageDown):
            self._step_focus(+1)
            event.accept()
            return
        target = self._focused_item_id()
        if target is None:
            super().keyPressEvent(event)
            return
        if key == Qt.Key.Key_P:
            self.intent_pick_requested.emit(target)
            event.accept()
            return
        if key == Qt.Key.Key_X:
            self.intent_skip_requested.emit(target)
            event.accept()
            return
        if key == Qt.Key.Key_Space:
            self.intent_toggle_requested.emit(target)
            event.accept()
            return
        super().keyPressEvent(event)


__all__ = ["CompareVersionsDialog", "CompareItem"]
