"""Surface 05 — Days Lists (per-day Picked/Skipped dashboard).

A 'pick where to start' surface that sits between Surface 03 (Phases) and
Surface 06 (Days Grid). Shows every event day as a card with two stacked
progress bars (green Picked / red Skipped) so the user can decide which
day to step into next.

This surface is a DASHBOARD, not capture-level — its bars use the design-
system progress styling (green = picked / red = skipped). The fixed
§5a photo-border semantics apply to Days Grid / Picker / Editor, not here.

Composition (design-system §Surface 05):
    Header row: ghost Back · title block (Pick where to start · "event · N days")
                · primary '+ Start a new pass…' · ghost '✓ Pick all days'
                · ghost '✗ Skip all days' (hover red).
    Body:       scrollable QScrollArea of DayRow cards. Each DayRow:
                  · accent day-number badge (left)
                  · title + date stacked on top of two StageProgress bars
                    (picked green, skipped red)
                  · per-row ✓ Pick all / ✗ Skip all mini ghost buttons
                  · meta column on the right (Buckets / Items)
                Clicking a row emits ``day_activated(day_number)`` so the
                host routes into the Days Grid for that day.

No legacy counterpart in the project — this is a new dashboard introduced
by the redesign. MainWindow integration lands when Surface 03's Pick tile
click is rerouted to land here (today it routes straight to PickPage).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mira.gateway import Gateway
from mira.ui.design import (
    Card,
    PageHeader,
    StageProgress,
    danger_ghost_button,
    ghost_button,
    primary_button,
)

log = logging.getLogger(__name__)


@dataclass
class DaySnapshot:
    """One day's Pick / Skip totals + meta. The page builds these from
    gateway queries in the live path; mocked in the smoke path."""

    day_number: int
    title: str
    date_iso: str          # 'YYYY-MM-DD'
    picked: int = 0
    skipped: int = 0
    buckets: int = 0
    items: int = 0
    location: str = ""
    notes: list[str] = field(default_factory=list)


class _DayBadge(QLabel):
    """Accent-soft tile carrying the day number."""

    def __init__(self, n: int) -> None:
        super().__init__(str(n))
        self.setFixedSize(46, 46)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            "background: #211f3a; color: #7c6cff;"
            " border: 1px solid #7c6cff; border-radius: 12px;"
            " font-size: 18px; font-weight: 800;"
        )


class DayRow(Card):
    """One day card. Click anywhere fires :sig:`activated(day_number)`."""

    activated = pyqtSignal(int)
    pick_all_requested = pyqtSignal(int)
    skip_all_requested = pyqtSignal(int)

    def __init__(
        self,
        snapshot: DaySnapshot,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, padded=True)
        self._snapshot = snapshot
        self.setMinimumHeight(120)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.layout().setContentsMargins(16, 14, 16, 14)
        self.layout().setSpacing(10)

        row = QHBoxLayout()
        row.setSpacing(14)

        # Day badge
        row.addWidget(_DayBadge(snapshot.day_number))

        # Center column: title + per-row actions + bars
        center = QVBoxLayout()
        center.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(8)
        title_block = QVBoxLayout()
        title_block.setSpacing(0)
        title = QLabel(snapshot.title or f"Day {snapshot.day_number}")
        title.setObjectName("CardTitle")
        title_block.addWidget(title)
        sub_bits = [snapshot.date_iso]
        if snapshot.location:
            sub_bits.append(snapshot.location)
        sub = QLabel(" · ".join(b for b in sub_bits if b))
        sub.setObjectName("Sub")
        title_block.addWidget(sub)
        top.addLayout(title_block, 1)
        pick_all = ghost_button("✓ Pick all")
        pick_all.setToolTip(f"Pick every undecided item on day {snapshot.day_number}.")
        pick_all.clicked.connect(
            lambda: self.pick_all_requested.emit(snapshot.day_number)
        )
        top.addWidget(pick_all)
        skip_all = danger_ghost_button("✗ Skip all")
        skip_all.setToolTip(f"Skip every undecided item on day {snapshot.day_number}.")
        skip_all.clicked.connect(
            lambda: self.skip_all_requested.emit(snapshot.day_number)
        )
        top.addWidget(skip_all)
        center.addLayout(top)

        # Stacked bars
        total = max(1, snapshot.items)
        for label, count, state in (
            ("Picked", snapshot.picked, "done"),
            ("Skipped", snapshot.skipped, "skip"),
        ):
            bar_row = QHBoxLayout()
            lab = QLabel(label)
            lab.setFixedWidth(64)
            bar_row.addWidget(lab)
            bar = StageProgress()
            pct = int(round(count / total * 100)) if snapshot.items > 0 else 0
            bar.setValue(pct)
            bar.setState(state if count > 0 else None)
            bar_row.addWidget(bar, 1)
            count_label = QLabel(
                f"{count} ({pct}%)"
                if count > 0 else "—"
            )
            count_label.setObjectName("Faint" if count == 0 else "Sub")
            count_label.setFixedWidth(96)
            count_label.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            bar_row.addWidget(count_label)
            center.addLayout(bar_row)
        row.addLayout(center, 1)

        # Right meta column
        meta = QVBoxLayout()
        meta.setSpacing(4)
        meta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        meta_label = QLabel("META")
        meta_label.setObjectName("Micro")
        meta_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        meta.addWidget(meta_label)
        buckets_label = QLabel(f"Buckets · <b>{snapshot.buckets}</b>")
        buckets_label.setObjectName("Sub")
        buckets_label.setTextFormat(Qt.TextFormat.RichText)
        buckets_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        meta.addWidget(buckets_label)
        items_label = QLabel(f"Items · <b>{snapshot.items}</b>")
        items_label.setObjectName("Sub")
        items_label.setTextFormat(Qt.TextFormat.RichText)
        items_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        meta.addWidget(items_label)
        meta_wrap = QWidget()
        meta_wrap.setLayout(meta)
        meta_wrap.setMinimumWidth(140)
        row.addWidget(meta_wrap)

        self.layout().addLayout(row)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        super().mousePressEvent(e)
        self.activated.emit(self._snapshot.day_number)


class DaysListsPage(QWidget):
    """Surface 05 — per-day Picked/Skipped dashboard.

    Header signals route back to the host (Back / + New pass / global
    Pick-all / global Skip-all). Day-row signals route per-day actions.
    """

    back_requested = pyqtSignal()
    new_pass_requested = pyqtSignal()
    pick_all_days_requested = pyqtSignal()
    skip_all_days_requested = pyqtSignal()

    day_activated = pyqtSignal(int)               # day_number
    day_pick_all_requested = pyqtSignal(int)
    day_skip_all_requested = pyqtSignal(int)

    def __init__(
        self,
        gateway: Optional[Gateway] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.gateway = gateway
        self._event_id: Optional[str] = None
        self._event_name: str = ""
        self._snapshots: list[DaySnapshot] = []
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 24, 32, 24)
        outer.setSpacing(18)

        # Header
        head = QHBoxLayout()
        head.setSpacing(12)
        self._back = ghost_button("‹ Back")
        self._back.clicked.connect(self.back_requested.emit)
        head.addWidget(self._back)
        # Title block
        title_block = QVBoxLayout()
        title_block.setSpacing(0)
        self._title = QLabel("Pick where to start")
        self._title.setObjectName("PageTitle")
        title_block.addWidget(self._title)
        self._sub = QLabel("")
        self._sub.setObjectName("Sub")
        title_block.addWidget(self._sub)
        head.addLayout(title_block, 1)
        # Action cluster
        new_pass = primary_button("+ Start a new pass…")
        new_pass.clicked.connect(self.new_pass_requested.emit)
        head.addWidget(new_pass)
        pick_all = ghost_button("✓ Pick all days")
        pick_all.clicked.connect(self.pick_all_days_requested.emit)
        head.addWidget(pick_all)
        skip_all = danger_ghost_button("✗ Skip all days")
        skip_all.clicked.connect(self.skip_all_days_requested.emit)
        head.addWidget(skip_all)
        outer.addLayout(head)

        # Day rows scroll
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        inner = QWidget()
        self._rows = QVBoxLayout(inner)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(12)
        self._rows.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(inner)
        self._scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        outer.addWidget(self._scroll, 1)

    # ── data ────────────────────────────────────────────────────────────

    def setEventForPreview(
        self,
        event_name: str,
        snapshots: list[DaySnapshot],
    ) -> None:
        self._event_name = event_name
        self._snapshots = list(snapshots)
        self._render()

    def _render(self) -> None:
        self._sub.setText(
            f"{self._event_name} · {len(self._snapshots)} day"
            + ("" if len(self._snapshots) == 1 else "s")
        )
        while self._rows.count():
            it = self._rows.takeAt(0)
            w = it.widget() if it else None
            if w is not None:
                w.deleteLater()
        for snap in self._snapshots:
            row = DayRow(snap)
            row.activated.connect(self.day_activated.emit)
            row.pick_all_requested.connect(self.day_pick_all_requested.emit)
            row.skip_all_requested.connect(self.day_skip_all_requested.emit)
            self._rows.addWidget(row)
