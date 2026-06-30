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
    QGridLayout,
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
    #: spec/159 §6+ — caller-controlled gate on the "Use this" button.
    #: Virtual members (Mira-pending) clear this so the user can't
    #: pick a version that has no bytes on disk; once it materialises
    #: as a real lineage row, the flag flips True by default.
    can_be_preferred: bool = True


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
    #: spec/159 §6+ — Use-this click from this tile's action button.
    use_this_clicked = pyqtSignal(str)    # item_id

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

        # spec/63 §4 follow-up — Nelson 2026-06-30: the per-tile title
        # + state chip read as redundant / wrong when this dialog is
        # reused outside the Export Compare flow (the day-grid Compare
        # uses the coloured 3 px border to encode state, no caption
        # needed).
        # spec/159 §6 (Nelson 2026-06-30 round 5) — the closed-event
        # Compare has no state grammar (review surface, no Pick/Skip),
        # so the user needs the per-tile caption back to tell "LRC"
        # from "Mira" at a glance. Caller opts in via the dialog's
        # ``show_titles`` constructor flag (off by default to keep the
        # day-grid Compare clean).
        if item.title:
            self._title_lbl: Optional[QLabel] = QLabel(item.title)
            # Reuse the existing ``#Sub`` typography role from
            # redesign.qss — theme-aware ink, no inline styles.
            self._title_lbl.setObjectName("Sub")
            self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._title_lbl.setVisible(False)
            outer.addWidget(self._title_lbl, 0)
        else:
            self._title_lbl = None

        # spec/159 §6+ — per-tile "Use this" action. Hidden by default
        # so the day-grid Compare reuse stays unchanged; the closed-
        # event Compare opts in via the dialog's ``show_use_this``
        # flag. The button is a custom-painted PreferredToggle re-used
        # so the visual language matches the review-dialog chrome.
        from mira.ui.exported.rating_widgets import PreferredToggle
        self._use_this_btn = PreferredToggle(self)
        self._use_this_btn.setVisible(False)
        self._use_this_btn.toggled.connect(self._on_use_this_toggled)
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.addStretch(1)
        action_row.addWidget(self._use_this_btn)
        action_row.addStretch(1)
        outer.addLayout(action_row)

        self.set_state(item.state)
        self._load_pixmap()

    def set_title_visible(self, visible: bool) -> None:
        """Show or hide the per-tile caption (the provenance badge)."""
        if self._title_lbl is not None:
            self._title_lbl.setVisible(bool(visible))

    def set_use_this_visible(self, visible: bool) -> None:
        """Show or hide the "✓ Use this" action button."""
        self._use_this_btn.setVisible(bool(visible))

    def set_preferred(self, preferred: bool) -> None:
        """Push the preferred-state onto the action button without
        firing a toggle signal back."""
        self._use_this_btn.setValue(bool(preferred))

    def _on_use_this_toggled(self, on: bool) -> None:
        # Per spec/159 §6+, clicking "Use this" on a tile sets it as
        # the preferred version. Toggling the same tile OFF would
        # leave the source with no preferred — surface that to the
        # host too (the host calls set_lineage_preferred(rel, False)).
        if on:
            self.use_this_clicked.emit(self._item.item_id)
        else:
            # Empty payload = "clear this tile's preferred flag." The
            # host knows it called set_preferred(True) on this tile so
            # the inverse is unambiguous.
            self.use_this_clicked.emit("")

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
        self.setStyleSheet(  # pragma: no-qss — §5a state-border colour is per-tile data
            f"_CompareTile {{ border: {_TILE_BORDER_WIDTH}px solid "
            f"{color}; border-radius: 6px; }} {focus_rule}")

    def _load_pixmap(self) -> None:
        """Load the tile's full-definition pixels — develop pipeline
        when the host asked for it, else raw file read.

        **Lazy develop (Nelson 2026-06-19).** On-disk tiles (lineage
        members — third-party returns + shipped Mira renders) load
        synchronously from the file (fast). Develop-pipeline tiles
        (the virtual Mira member) defer the expensive pipeline via
        :meth:`_kick_develop`: paint the raw source first so the tile
        appears immediately, then swap in the developed pixmap once
        the pipeline finishes. Same pattern the preview dialog uses
        for its main image."""
        from mira.ui.exported.preview_dialog import ExportPreviewDialog
        if self._item.develop_for_preview:
            # Fast paint: show the raw source first so the tile isn't
            # a blank waiting on the pipeline.
            self._raw_pixmap = ExportPreviewDialog._load_preview_pixmap(
                self._item.path)
            self._paint_pixmap()
            self._kick_develop()
            return
        # Lineage tiles: the file IS the export, no pipeline.
        self._raw_pixmap = ExportPreviewDialog._load_preview_pixmap(
            self._item.path)
        self._paint_pixmap()

    def _kick_develop(self) -> None:
        """Schedule the develop pipeline to run after the dialog has
        painted (``QTimer.singleShot(0, ...)`` defers to the next
        event-loop pass). The dialog instantiates every tile in its
        constructor; without this defer, opening Compare on a 5+
        version cluster blocks for several seconds while every Mira
        member develops."""
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._run_develop)

    def _run_develop(self) -> None:
        """Run the develop pipeline for this tile under a wait
        cursor, then swap the developed pixmap in over the raw
        source. Failure falls back silently to the raw source paint
        already in place."""
        from PyQt6.QtCore import Qt as _Qt
        from PyQt6.QtGui import QCursor, QGuiApplication
        from mira.ui.exported.preview_dialog import ExportPreviewDialog
        from mira.ui.exported.preview_dialog import PreviewItem as _PI

        proxy = _PI(
            item_id=self._item.item_id,
            path=self._item.path,
            develop_for_preview=self._item.develop_for_preview,
            develop_adjustment=self._item.develop_adjustment,
            develop_style_fallback=self._item.develop_style_fallback,
        )
        QGuiApplication.setOverrideCursor(
            QCursor(_Qt.CursorShape.WaitCursor))
        try:
            pm = ExportPreviewDialog._develop_pixmap(proxy)
        finally:
            QGuiApplication.restoreOverrideCursor()
        if pm is None:
            return
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
    #: spec/159 §6+ — the user picked this tile as the preferred
    #: version of its source. Carries the tile's ``item_id`` (which
    #: for spec/159 callers is the lineage row's ``export_relpath``).
    use_this_requested = pyqtSignal(str)

    def __init__(
        self,
        items: List[CompareItem],
        parent: Optional[QWidget] = None,
        *,
        show_titles: bool = False,
        show_use_this: bool = False,
        preferred_item_id: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CompareVersionsDialog")
        self.setWindowTitle("Compare versions")
        self.setModal(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._items: List[CompareItem] = list(items)
        self._tiles: List[_CompareTile] = []
        self._show_titles = bool(show_titles)
        self._show_use_this = bool(show_use_this)
        self._preferred_item_id = preferred_item_id
        # spec/63 locked keymap — the focused tile is the implicit
        # target of P / X / Space. Tiles 0..N-1; the first tile starts
        # focused so the user can act immediately without clicking.
        self._focused_index = 0 if items else -1
        # spec/89 §11.3 polish — track the currently-open F10
        # inspection lens so a second F10 closes it instead of
        # stacking another window.
        self._inspect_window = None
        self._build_ui()
        if self._tiles:
            self._tiles[self._focused_index].set_focused(True)
        if parent is not None:
            geo = parent.geometry()
            self.resize(int(geo.width() * 0.92), int(geo.height() * 0.88))
        else:
            # No parent — pick a default that fits the grid shape
            # (cols × tile_width, rows × ~tile_width since tiles are
            # roughly square at fit-to-aspect).
            import math
            n = max(1, len(items))
            cols = max(1, int(math.ceil(math.sqrt(n))))
            rows = max(1, int(math.ceil(n / cols)))
            target_w = max(900, _TILE_DEFAULT_WIDTH * min(3, cols))
            target_h = max(760, int(_TILE_DEFAULT_WIDTH * 0.75) * min(3, rows))
            self.resize(target_w, target_h)

    # ── UI ──────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        from mira.ui.i18n import tr

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(10)

        hint = QLabel(tr(
            "Click a tile, or use ← → to focus. "
            "P Will-export · X Set-aside · Space toggle · "
            "F10 Full Resolution · Esc closes."
        ))
        hint.setObjectName("Sub")
        outer.addWidget(hint)

        # Tile grid inside a scroll area. spec/63 §4 follow-up — Nelson
        # 2026-06-30 asked for a grid layout instead of the single-row
        # side-by-side so 3+ items don't crush each other. The grid
        # aims for near-square (cols = ceil(sqrt(N))): 2 items → 1×2
        # (still side-by-side); 3-4 → 2×2; 5-6 → 2×3 or 3×2; etc. The
        # scroll area now scrolls vertically when the grid overflows.
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        outer.addWidget(scroll, 1)

        import math
        n = len(self._items)
        cols = max(1, int(math.ceil(math.sqrt(max(1, n)))))

        host = QWidget()
        host_layout = QGridLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setHorizontalSpacing(12)
        host_layout.setVerticalSpacing(12)
        for idx, item in enumerate(self._items):
            tile = _CompareTile(item, parent=host)
            tile.set_title_visible(self._show_titles)
            # The "Use this" button only appears when the dialog opts
            # in AND the underlying item allows it (virtual Mira-
            # pending members opt out — see CompareItem docstring).
            tile.set_use_this_visible(
                self._show_use_this and item.can_be_preferred)
            if (self._preferred_item_id is not None
                    and item.item_id == self._preferred_item_id):
                tile.set_preferred(True)
            tile.toggled.connect(self.intent_toggle_requested.emit)
            tile.focused.connect(self._on_tile_focused)
            tile.use_this_clicked.connect(self._on_tile_use_this)
            row, col = divmod(idx, cols)
            host_layout.addWidget(tile, row, col)
            self._tiles.append(tile)
        # Make every column stretch equally so tiles share the width.
        for c in range(cols):
            host_layout.setColumnStretch(c, 1)
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

    def set_preferred_item_id(self, item_id: Optional[str]) -> None:
        """spec/159 §6+ — set or clear the preferred tile. Empty /
        ``None`` clears every tile's "Use this" toggle."""
        self._preferred_item_id = item_id or None
        for tile in self._tiles:
            tile.set_preferred(
                bool(item_id) and tile.item_id() == item_id)

    def _on_tile_use_this(self, item_id: str) -> None:
        """spec/159 §6+ — a tile fired Use-this. The dialog enforces
        the singleton invariant (mirror of the gateway's clear-then-
        set transaction): set this tile preferred + clear every
        other, then surface to the host via :data:`use_this_requested`
        which writes through ``set_lineage_preferred``. An empty
        ``item_id`` payload from the tile means the user just
        toggled the current preferred OFF."""
        if not item_id:
            cleared = self._preferred_item_id
            self.set_preferred_item_id(None)
            self.use_this_requested.emit(cleared or "")
            return
        self.set_preferred_item_id(item_id)
        self.use_this_requested.emit(item_id)

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

    # ── F10 — full-resolution inspection of the focused tile ────────

    def _open_inspect_for_focused(self) -> None:
        """spec/89 §3.2 + spec/63 §4 (Nelson 2026-06-19) — F10 on the
        focused Compare tile opens the canonical inspection lens with
        the **would-be-exported** pixels.

        * Develop-pipeline tile (the virtual Mira member) → run
          develop_photo_array at full resolution and open the lens
          with the developed pixmap.
        * Lineage tile (third-party return / shipped Mira render) →
          open the lens directly on the on-disk file.

        Re-uses ``ExportPreviewDialog._develop_pixmap_full`` for the
        develop branch and mirrors the file-load branch (raw + RAW
        handling) so the lens always shows the actual export bytes
        regardless of which tile carries them. A second F10 closes
        the lens (matches the PhotoViewport toggle contract)."""
        from pathlib import Path as _Path
        from PyQt6.QtCore import Qt as _Qt
        from PyQt6.QtGui import QCursor, QGuiApplication

        if not (0 <= self._focused_index < len(self._tiles)):
            return
        # Toggle off any already-open lens before opening another.
        if self._inspect_window is not None:
            try:
                self._inspect_window.close()
            except Exception:                                      # noqa: BLE001
                pass
            self._inspect_window = None
            return
        item = self._tiles[self._focused_index]._item

        base = None
        path = _Path(item.path) if item.path else None
        if item.develop_for_preview:
            from mira.ui.exported.preview_dialog import (
                ExportPreviewDialog, PreviewItem as _PI,
            )
            proxy = _PI(
                item_id=item.item_id, path=item.path,
                develop_for_preview=True,
                develop_adjustment=item.develop_adjustment,
                develop_style_fallback=item.develop_style_fallback,
            )
            QGuiApplication.setOverrideCursor(
                QCursor(_Qt.CursorShape.WaitCursor))
            try:
                base = ExportPreviewDialog._develop_pixmap_full(proxy)
            finally:
                QGuiApplication.restoreOverrideCursor()
        else:
            from PyQt6.QtGui import QPixmap as _QPixmap
            from mira.ui.media.image_loader import (
                _RAW_EXTENSIONS, load_pixmap, load_raw_half_res,
            )
            if path is None or not path.is_file():
                return
            QGuiApplication.setOverrideCursor(
                QCursor(_Qt.CursorShape.WaitCursor))
            try:
                if path.suffix.lower() in _RAW_EXTENSIONS:
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
        from mira.ui.media.photo_viewport import _InspectView
        self._inspect_window = _InspectView(
            base, None, path=path,
            is_raw=False,                # developed / display pixels
            with_tools=True, parent=self,
        )
        self._inspect_window.open_windowed()
        self._inspect_window.setFocus()

    # ── Qt overrides ────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent) -> None:         # noqa: N802
        """spec/63 locked keymap inside the dialog:
        ← / →   move focus to the previous / next tile
        P       Will export (focused tile)
        X       Set aside (focused tile)
        Space   toggle picked ↔ skipped (focused tile)
        F10     Full Resolution lens on the focused tile (exported pixels)
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
        if key == Qt.Key.Key_F10:
            self._open_inspect_for_focused()
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
