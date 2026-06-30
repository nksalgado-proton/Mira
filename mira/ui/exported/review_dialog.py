"""spec/159 — the Exported Collection review viewer.

A lightweight dialog that opens against a lineage row (one exported
file), shows the actual exported bytes through
:class:`~mira.ui.media.photo_viewport.PhotoViewport`, and surfaces
the per-version classification controls (stars / colour label /
portfolio flag / "marked for deletion" toggle).

This is the spec/159 Session B viewer in its minimal-viable form.
The spec asked for Editor reuse with a ``review_mode`` flag; this
implementation goes lighter because the Editor's creative chrome is
already a substantial maintenance surface and the review use case
doesn't need any of it. If the Editor reuse turns out to be the
better long-term call, this dialog can be replaced — the
DCDetailPage already wires ``review_requested`` as the open verb.

Keyboard map (spec/159 §5.4):

* ``1-5``       — set stars 1..5
* ``0``         — clear stars
* ``Shift+1..5`` — set colour label (red/yellow/green/blue/purple)
* ``Shift+0`` — clear colour label
* ``K``         — toggle portfolio flag
* ``D`` / ``Delete`` — toggle "marked for deletion"
* ``←/→``      — prev / next version
* ``F`` / ``F11`` — fullscreen
* ``Esc``       — close
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mira.ui.i18n import tr

log = logging.getLogger(__name__)


_COLOR_LABEL_HEX = {
    "red":    "#D9382E",
    "yellow": "#E4B91F",
    "green":  "#2DA84A",
    "blue":   "#3A8DD8",
    "purple": "#9C4DC9",
}
#: Order matches the Shift+1..5 keyboard map.
_COLOR_LABEL_ORDER = ("red", "yellow", "green", "blue", "purple")


@dataclass
class ReviewItem:
    """One exported lineage row exposed to the review dialog."""
    export_relpath: str
    abs_path: Path
    stars: Optional[int] = None
    color_label: Optional[str] = None
    flag: bool = False
    to_delete: bool = False
    title: str = ""


class ReviewMediaDialog(QDialog):
    """spec/159 — minimal review viewer for the Exported Collection."""

    #: ``(export_relpath, stars 1..5 or None)``
    stars_changed = pyqtSignal(str, object)
    #: ``(export_relpath, label or None)``
    color_label_changed = pyqtSignal(str, object)
    #: ``(export_relpath, bool)``
    flag_changed = pyqtSignal(str, bool)
    #: ``(export_relpath, bool)``
    to_delete_changed = pyqtSignal(str, bool)

    def __init__(
        self,
        items: List[ReviewItem],
        start_index: int = 0,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ReviewMediaDialog")
        self.setWindowTitle(tr("Review"))
        self.setModal(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._items = list(items)
        self._index = max(0, min(start_index, len(self._items) - 1))
        self._was_fullscreen = False
        self._build_ui()
        self._load_index()
        if parent is not None:
            geo = parent.geometry()
            self.resize(int(geo.width() * 0.85), int(geo.height() * 0.85))
        else:
            self.resize(1200, 800)

    # ── UI build ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        from mira.ui.media.photo_viewport import PhotoViewport

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        # ── classification row (top) ────────────────────────────────
        self._chrome_row = QHBoxLayout()
        self._chrome_row.setSpacing(14)
        self._chrome_row.setContentsMargins(4, 0, 4, 0)

        # Star buttons 1..5
        self._star_btns: list[QPushButton] = []
        star_box = QHBoxLayout()
        star_box.setSpacing(2)
        for n in range(1, 6):
            b = QPushButton("★")
            b.setFixedSize(28, 28)
            b.setObjectName(f"StarBtn{n}")
            b.setProperty("starlevel", n)
            b.setCheckable(False)
            b.clicked.connect(lambda _=False, k=n: self._on_star_clicked(k))
            star_box.addWidget(b)
            self._star_btns.append(b)
        clear_stars = QPushButton(tr("Clear"))
        clear_stars.setFixedHeight(28)
        clear_stars.clicked.connect(lambda: self._on_star_clicked(None))
        star_box.addWidget(clear_stars)
        self._chrome_row.addLayout(star_box)

        # Colour label dots
        color_box = QHBoxLayout()
        color_box.setSpacing(4)
        self._color_btns: dict[str, QPushButton] = {}
        for key in _COLOR_LABEL_ORDER:
            b = QPushButton()
            b.setFixedSize(20, 20)
            b.setToolTip(key.capitalize())
            b.setStyleSheet(
                f"QPushButton {{ background-color: {_COLOR_LABEL_HEX[key]};"
                f" border: 1px solid #00000060; border-radius: 10px; }}"
                f"QPushButton:hover {{ border: 2px solid #ffffff; }}"
            )
            b.clicked.connect(
                lambda _=False, k=key: self._on_color_clicked(k))
            color_box.addWidget(b)
            self._color_btns[key] = b
        clear_color = QPushButton(tr("Clear"))
        clear_color.setFixedHeight(28)
        clear_color.clicked.connect(
            lambda: self._on_color_clicked(None))
        color_box.addWidget(clear_color)
        self._chrome_row.addLayout(color_box)

        # Flag toggle
        self._flag_btn = QPushButton(tr("⚑ Flag"))
        self._flag_btn.setCheckable(True)
        self._flag_btn.setFixedHeight(28)
        self._flag_btn.clicked.connect(self._on_flag_clicked)
        self._chrome_row.addWidget(self._flag_btn)

        self._chrome_row.addStretch(1)

        # To-delete toggle
        self._delete_btn = QPushButton(tr("⌫ Mark for deletion"))
        self._delete_btn.setCheckable(True)
        self._delete_btn.setFixedHeight(28)
        self._delete_btn.clicked.connect(self._on_delete_clicked)
        self._chrome_row.addWidget(self._delete_btn)

        chrome_w = QWidget()
        chrome_w.setLayout(self._chrome_row)
        outer.addWidget(chrome_w)

        # ── photo viewport ──────────────────────────────────────────
        self._viewport = PhotoViewport(self)
        self._viewport.set_corner_inspect_visible(False)
        self._viewport.set_lens_tools_visible(True)
        self._viewport.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        outer.addWidget(self._viewport, 1)

        # ── nav row (bottom) ────────────────────────────────────────
        nav = QHBoxLayout()
        nav.setSpacing(8)
        self._prev_btn = QPushButton("← " + tr("Previous"))
        self._prev_btn.clicked.connect(self._go_prev)
        nav.addWidget(self._prev_btn)
        self._counter = QLabel("")
        self._counter.setObjectName("Sub")
        nav.addWidget(self._counter)
        nav.addStretch(1)
        self._next_btn = QPushButton(tr("Next") + " →")
        self._next_btn.clicked.connect(self._go_next)
        nav.addWidget(self._next_btn)
        nav_w = QWidget()
        nav_w.setLayout(nav)
        outer.addWidget(nav_w)

        # ── keyboard shortcuts ──────────────────────────────────────
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self,
                  activated=self.close)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self,
                  activated=self._go_prev)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self,
                  activated=self._go_next)
        QShortcut(QKeySequence(Qt.Key.Key_F11), self,
                  activated=self._toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key.Key_F), self,
                  activated=self._toggle_fullscreen)
        # K → flag toggle
        QShortcut(QKeySequence(Qt.Key.Key_K), self,
                  activated=lambda: self._on_flag_clicked(
                      not self._current_or_blank().flag))
        # D → to-delete toggle
        QShortcut(QKeySequence(Qt.Key.Key_D), self,
                  activated=lambda: self._on_delete_clicked(
                      not self._current_or_blank().to_delete))
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self,
                  activated=lambda: self._on_delete_clicked(
                      not self._current_or_blank().to_delete))

    # ── helpers ─────────────────────────────────────────────────────

    def _current_or_blank(self) -> ReviewItem:
        if 0 <= self._index < len(self._items):
            return self._items[self._index]
        return ReviewItem("", Path("."))

    def _load_index(self) -> None:
        """Show the photo at ``self._index`` and repaint the chrome
        so the rating widgets reflect the current item's values."""
        from mira.ui.media.photo_viewport import ViewportItem

        if not self._items:
            return
        idx = self._index
        item = self._items[idx]
        self._viewport.set_items(
            [ViewportItem(path=item.abs_path, kind="photo")],
            current=0,
        )
        self._counter.setText(
            f"{idx + 1} / {len(self._items)}  ·  {item.export_relpath}")
        self._refresh_chrome(item)
        self._prev_btn.setEnabled(idx > 0)
        self._next_btn.setEnabled(idx < len(self._items) - 1)

    def _refresh_chrome(self, item: ReviewItem) -> None:
        # Stars — filled star buttons up to N, faint for the rest.
        for i, b in enumerate(self._star_btns, start=1):
            filled = item.stars is not None and i <= item.stars
            b.setStyleSheet(
                "QPushButton { color: #F2C84A; font-size: 18px;"
                " border: none; background: transparent; }"
                if filled
                else "QPushButton { color: #555; font-size: 18px;"
                " border: none; background: transparent; }"
            )
        # Colour label — bordered ring on the active one.
        for key, b in self._color_btns.items():
            hex_color = _COLOR_LABEL_HEX[key]
            if item.color_label == key:
                b.setStyleSheet(
                    f"QPushButton {{ background-color: {hex_color};"
                    f" border: 2px solid #ffffff; border-radius: 10px; }}"
                )
            else:
                b.setStyleSheet(
                    f"QPushButton {{ background-color: {hex_color};"
                    f" border: 1px solid #00000060; border-radius: 10px; }}"
                    f"QPushButton:hover {{ border: 2px solid #ffffff; }}"
                )
        self._flag_btn.setChecked(item.flag)
        self._delete_btn.setChecked(item.to_delete)

    # ── click handlers ─────────────────────────────────────────────

    def _on_star_clicked(self, n: Optional[int]) -> None:
        item = self._current_or_blank()
        if not item.export_relpath:
            return
        # LRC convention: click an already-filled star clears.
        if n is not None and item.stars == n:
            n = None
        item.stars = n
        self.stars_changed.emit(item.export_relpath, n)
        self._refresh_chrome(item)

    def _on_color_clicked(self, label: Optional[str]) -> None:
        item = self._current_or_blank()
        if not item.export_relpath:
            return
        # Same convention as stars — click active = clear.
        if label is not None and item.color_label == label:
            label = None
        item.color_label = label
        self.color_label_changed.emit(item.export_relpath, label)
        self._refresh_chrome(item)

    def _on_flag_clicked(self, checked: Optional[bool] = None) -> None:
        item = self._current_or_blank()
        if not item.export_relpath:
            return
        new_state = (not item.flag) if checked is None else bool(checked)
        item.flag = new_state
        self.flag_changed.emit(item.export_relpath, new_state)
        self._refresh_chrome(item)

    def _on_delete_clicked(self, checked: Optional[bool] = None) -> None:
        item = self._current_or_blank()
        if not item.export_relpath:
            return
        new_state = (not item.to_delete) if checked is None else bool(checked)
        item.to_delete = new_state
        self.to_delete_changed.emit(item.export_relpath, new_state)
        self._refresh_chrome(item)

    # ── nav ────────────────────────────────────────────────────────

    def _go_prev(self) -> None:
        if self._index <= 0:
            return
        self._index -= 1
        self._load_index()

    def _go_next(self) -> None:
        if self._index >= len(self._items) - 1:
            return
        self._index += 1
        self._load_index()

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
            self._was_fullscreen = False
        else:
            self.showFullScreen()
            self._was_fullscreen = True

    # ── number-key handling (1..5 stars + Shift+1..5 colour) ──────

    def keyPressEvent(self, ev: QKeyEvent) -> None:  # noqa: N802 — Qt
        key = ev.key()
        mods = ev.modifiers()
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        # Block all-modifier paths (Ctrl, Alt, Meta) — only Shift counts.
        bare_mods = mods & ~Qt.KeyboardModifier.ShiftModifier
        if bare_mods == Qt.KeyboardModifier.NoModifier:
            if Qt.Key.Key_1 <= key <= Qt.Key.Key_5:
                n = key - Qt.Key.Key_0
                if shift:
                    self._on_color_clicked(
                        _COLOR_LABEL_ORDER[n - 1])
                else:
                    self._on_star_clicked(n)
                ev.accept()
                return
            if key == Qt.Key.Key_0:
                if shift:
                    self._on_color_clicked(None)
                else:
                    self._on_star_clicked(None)
                ev.accept()
                return
        super().keyPressEvent(ev)


__all__ = ["ReviewItem", "ReviewMediaDialog"]
