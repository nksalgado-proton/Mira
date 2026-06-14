"""DaySplitDialog — the multi-date Collect split confirmation (spec/57 §4.1).

A Collect run spanning several capture dates first shows the proposed
day split (dates + counts) so the user confirms before any metadata
prompts — and this is the locked moment to pull 00:30 night shots into
the previous evening's day: the **"day starts at"** control regroups the
counts live (photos taken before that time belong to the previous day),
re-feeding ``core.scan_source.build_scan_result`` via
``day_start_minutes`` when accepted.

Single-date runs never see this dialog (they ingest straight through to
the plan dialog).
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.scan_source import effective_capture_date
from mira.ui.i18n import tr

# "Day starts at" choices, in minutes past midnight. 00:00 = calendar
# days (the default); the late options absorb post-midnight shooting.
_BOUNDARY_CHOICES = (0, 60, 120, 180, 240, 300, 360)


class DaySplitDialog(QDialog):
    """Confirm (and optionally re-boundary) the day split of one scan."""

    def __init__(
        self,
        timestamps: Sequence[datetime],
        *,
        initial_minutes: int = 0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("DaySplitDialog")
        self.setWindowTitle(tr("Confirm the day split"))
        self._timestamps: List[datetime] = [t for t in timestamps if t is not None]
        self._minutes = int(initial_minutes)

        outer = QVBoxLayout(self)
        intro = QLabel(tr(
            "This Collect run spans several days. Confirm how the photos "
            "split into plan days — each day below becomes (or merges "
            "into) one day of the event."
        ))
        intro.setWordWrap(True)
        outer.addWidget(intro)

        row = QHBoxLayout()
        lbl = QLabel(tr("Day starts at"))
        lbl.setToolTip(tr(
            "Photos taken before this time count as the PREVIOUS day — "
            "use it to pull late-night shots into the evening they "
            "belong to."
        ))
        row.addWidget(lbl)
        self._boundary = QComboBox()
        self._boundary.setObjectName("DayBoundaryCombo")
        self._boundary.setToolTip(lbl.toolTip())
        for minutes in _BOUNDARY_CHOICES:
            self._boundary.addItem(f"{minutes // 60:02d}:{minutes % 60:02d}", minutes)
        idx = self._boundary.findData(self._minutes)
        self._boundary.setCurrentIndex(idx if idx >= 0 else 0)
        self._boundary.currentIndexChanged.connect(self._refresh)
        row.addWidget(self._boundary)
        row.addStretch(1)
        outer.addLayout(row)

        self._table = QTableWidget(0, 2)
        self._table.setObjectName("DaySplitTable")
        self._table.setHorizontalHeaderLabels([tr("Date"), tr("Files")])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setToolTip(tr(
            "The proposed split — dates with their file counts. Adjust "
            "\"Day starts at\" to regroup."
        ))
        outer.addWidget(self._table, stretch=1)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)

        self.resize(420, 360)
        self._refresh()

    # ── live regroup ────────────────────────────────────────────────

    def day_start_minutes(self) -> int:
        """The accepted boundary, in minutes past midnight."""
        return int(self._boundary.currentData() or 0)

    def _refresh(self) -> None:
        minutes = self.day_start_minutes()
        counts: dict = {}
        for ts in self._timestamps:
            d = effective_capture_date(ts, minutes)
            counts[d] = counts.get(d, 0) + 1
        self._table.setRowCount(len(counts))
        for r, (d, n) in enumerate(sorted(counts.items())):
            date_item = QTableWidgetItem(d.isoformat())
            date_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            count_item = QTableWidgetItem(str(n))
            count_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._table.setItem(r, 0, date_item)
            self._table.setItem(r, 1, count_item)
