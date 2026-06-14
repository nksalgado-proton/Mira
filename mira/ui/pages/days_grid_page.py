"""Surface 06 — Days Grid (capture grid).

Capture-level thumbnail grid for picking / skipping a day's photos.
Replaces the legacy :class:`~mira.ui.base.day_grid_view.DayGridView` chrome
visually; the §5a state borders, §5b corner overlays, cluster pile, and
mixed-cluster split-chip are already baked into the
:class:`~mira.ui.design.Thumb` widget so this page is mostly composition.

LOCKED semantics (design-system §5a — never restyle):
    border 3px = state  picked=green / skipped=red / compare=orange
                       / mixed=yellow / neutral=line
    cluster icons       repeated / burst / focus / exposure — from
                       assets/icons/clusters/badge/
    visited eye         top-right translucent chip
    exported badge      bottom-left accent ↑ Exported
    cluster count       bottom-right ×N or split chip 3✓·2✗ for mixed

Composition (design-system §Surface 06):
    Sticky toolbar:  Back · day navigator pill · ✓ Pick all · ✗ Skip all
                     · primary + Start a new pass… · review progress
                     (StageProgress with count text)
    Legend strip:    picked / skipped / compare swatches + reminder
                     'border = state, badge = cluster'
    Scrolling grid:  flow of Thumb widgets, responsive (~180px tiles)

Live data wiring (gateway: day captures, cluster groupings, per-item
state + visited + exported flags) lands in the follow-up route-swap
commit alongside the keyboard mapping (P/X/Space/C per the project's
locked keyboard map). For now :meth:`setItemsForPreview` populates from
mock data so the smoke + tests can land independently.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mira.gateway import Gateway
from mira.ui.base.flow_layout import FlowLayout
from mira.ui.design import (
    StageProgress,
    Thumb,
    danger_ghost_button,
    ghost_button,
    primary_button,
)
from mira.ui.palette import PALETTE

log = logging.getLogger(__name__)


@dataclass
class GridItem:
    """One grid cell's content payload. Mirrors the Thumb constructor so
    the page can hand a list of these straight in."""

    item_id: str
    pixmap: QPixmap | None = None
    state: str | None = None
    visited: bool = False
    exported: bool = False
    cluster_type: str | None = None
    cluster_count: int = 0
    cluster_split: tuple[int, int] | None = None


class _DayNavigatorPill(QFrame):
    """Card2-styled pill ‹ Day N · title · date · N items ›."""

    prev_clicked = pyqtSignal()
    next_clicked = pyqtSignal()

    def __init__(
        self,
        day_number: int,
        title: str,
        date_iso: str,
        item_count: int,
    ) -> None:
        super().__init__()
        self.setObjectName("Card2")
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(10)
        prev_btn = ghost_button("‹")
        prev_btn.setFixedSize(28, 28)
        prev_btn.clicked.connect(self.prev_clicked.emit)
        h.addWidget(prev_btn)
        meta = " · ".join(b for b in (
            f"Day {day_number}",
            title,
            date_iso,
            f"{item_count} items",
        ) if b)
        label = QLabel(meta)
        label.setObjectName("Sub")
        h.addWidget(label)
        next_btn = ghost_button("›")
        next_btn.setFixedSize(28, 28)
        next_btn.clicked.connect(self.next_clicked.emit)
        h.addWidget(next_btn)


def _state_swatch(state: str, label: str) -> QWidget:
    """Tiny picked/skipped/compare/mixed legend chip — one square outline
    in the locked PALETTE color + a small label."""
    host = QWidget()
    h = QHBoxLayout(host)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(6)
    # Use the dark palette as the canonical legend colors (the swatches
    # are painted with the FIXED §5a values, never the theme accent).
    color = PALETTE["dark"][state]
    swatch = QLabel()
    swatch.setFixedSize(18, 14)
    swatch.setStyleSheet(
        f"background: transparent; border: 3px solid {color};"
        f" border-radius: 5px;"
    )
    h.addWidget(swatch)
    txt = QLabel(label)
    txt.setObjectName("Sub")
    h.addWidget(txt)
    return host


class DaysGridPage(QWidget):
    """Surface 06 — the capture grid page."""

    back_requested = pyqtSignal()
    prev_day_requested = pyqtSignal()
    next_day_requested = pyqtSignal()
    pick_all_requested = pyqtSignal()
    skip_all_requested = pyqtSignal()
    new_pass_requested = pyqtSignal()
    item_activated = pyqtSignal(str)   # item_id (cluster cover or single)

    def __init__(
        self,
        gateway: Optional[Gateway] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.gateway = gateway
        self._items: list[GridItem] = []
        self._day_number = 1
        self._day_title = ""
        self._day_date = ""
        self._reviewed = 0
        self._total = 0
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.setSpacing(14)

        # ── Sticky toolbar ──
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        self._back = ghost_button("‹ Back")
        self._back.clicked.connect(self.back_requested.emit)
        toolbar.addWidget(self._back)
        self._day_pill = _DayNavigatorPill(1, "", "", 0)
        self._day_pill.prev_clicked.connect(self.prev_day_requested.emit)
        self._day_pill.next_clicked.connect(self.next_day_requested.emit)
        toolbar.addWidget(self._day_pill)
        pick_all = ghost_button("✓ Pick all")
        pick_all.clicked.connect(self.pick_all_requested.emit)
        toolbar.addWidget(pick_all)
        skip_all = danger_ghost_button("✗ Skip all")
        skip_all.clicked.connect(self.skip_all_requested.emit)
        toolbar.addWidget(skip_all)
        new_pass = primary_button("+ Start a new pass…")
        new_pass.clicked.connect(self.new_pass_requested.emit)
        toolbar.addWidget(new_pass)
        toolbar.addStretch()
        # Review progress on the right
        progress_block = QVBoxLayout()
        progress_block.setSpacing(2)
        self._progress_label = QLabel("0 / 0 reviewed")
        self._progress_label.setObjectName("Sub")
        self._progress_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        progress_block.addWidget(self._progress_label)
        self._progress_bar = StageProgress()
        self._progress_bar.setMinimumWidth(180)
        progress_block.addWidget(self._progress_bar)
        toolbar.addLayout(progress_block)
        outer.addLayout(toolbar)

        # ── Legend strip ──
        legend = QHBoxLayout()
        legend.setSpacing(18)
        legend.addWidget(_state_swatch("picked", "Picked"))
        legend.addWidget(_state_swatch("skipped", "Skipped"))
        legend.addWidget(_state_swatch("compare", "Compare"))
        legend.addWidget(_state_swatch("mixed", "Mixed cluster"))
        reminder = QLabel(
            "<span style='color:#8b94a7'>"
            "border <b style='color:#eef1f7'>= state</b>"
            " · badge <b style='color:#eef1f7'>= cluster</b>"
            " · eye <b style='color:#eef1f7'>= visited</b>"
            "</span>"
        )
        reminder.setObjectName("Sub")
        reminder.setTextFormat(Qt.TextFormat.RichText)
        legend.addWidget(reminder)
        legend.addStretch()
        outer.addLayout(legend)

        # ── Scrolling grid ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        grid_host = QWidget()
        self._flow = FlowLayout(grid_host, spacing=18)
        self._flow.setContentsMargins(0, 0, 0, 0)
        self._scroll.setWidget(grid_host)
        self._scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        outer.addWidget(self._scroll, 1)

    # ── data API ────────────────────────────────────────────────────────

    def setDay(
        self,
        day_number: int,
        title: str,
        date_iso: str,
        items: list[GridItem],
    ) -> None:
        """Replace the day pill data + the grid contents in one shot."""
        self._day_number = day_number
        self._day_title = title
        self._day_date = date_iso
        self._items = list(items)
        self._total = len(items)
        self._reviewed = sum(
            1 for it in items if it.state in ("picked", "skipped", "compare")
        )
        self._refresh()

    setItemsForPreview = setDay  # alias for smoke convenience

    # ── render ─────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        # Day navigator pill — rebuild because its label is in the constructor
        old_pill = self._day_pill
        new_pill = _DayNavigatorPill(
            self._day_number, self._day_title, self._day_date, self._total,
        )
        new_pill.prev_clicked.connect(self.prev_day_requested.emit)
        new_pill.next_clicked.connect(self.next_day_requested.emit)
        old_pill.parentWidget().layout().replaceWidget(old_pill, new_pill)
        old_pill.deleteLater()
        self._day_pill = new_pill

        # Review progress
        pct = int(round(self._reviewed / self._total * 100)) if self._total else 0
        self._progress_label.setText(
            f"{self._reviewed} / {self._total} reviewed"
        )
        self._progress_bar.setValue(pct)
        self._progress_bar.setState(
            "done" if self._reviewed == self._total and self._total > 0
            else "prog" if self._reviewed > 0
            else None
        )

        # Clear current cells
        while self._flow.count():
            w = self._flow.itemAt(0).widget()
            self._flow.removeWidget(w)
            w.deleteLater()
        # Populate new cells
        tile_size = QSize(184, 138)
        for item in self._items:
            t = Thumb(
                item.pixmap,
                state=item.state,
                size=tile_size,
                cluster_type=item.cluster_type,
                cluster_count=item.cluster_count,
                cluster_split=item.cluster_split,
                visited=item.visited,
                exported=item.exported,
            )
            t.clicked.connect(
                lambda _=False, iid=item.item_id: self.item_activated.emit(iid)
            )
            self._flow.addWidget(t)
