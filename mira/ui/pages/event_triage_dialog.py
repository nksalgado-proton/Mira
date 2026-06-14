"""``EventTriageDialog`` — bulk-classify unclassified events (spec/44 Slice E).

A QTableWidget of every unclassified event in the library, with a per-row
:class:`QComboBox` to pick a type. Picking a type immediately persists via
:meth:`Gateway.set_classification` so the user can sweep through their
backlog quickly. The dialog can be closed at any point — already-classified
rows survive; unchanged rows stay unclassified.

The "Classify all…" button on the dashboard's Unclassified section header
emits ``classify_all_requested``; MainWindow opens this dialog in response.

Heuristic via :func:`mira.event_classification.suggest_type_from_signals`
based on day count / camera count / TZ diversity. ``None`` means no
suggestion (the row's "Suggested" column stays blank — the user picks fresh).
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mira import event_classification
from mira.gateway import EventsQuery, Gateway
from mira.ui.i18n import tr

log = logging.getLogger(__name__)


_COL_NAME = 0
_COL_START = 1
_COL_DAYS = 2
_COL_CAMERAS = 3
_COL_SUGGESTED = 4
_COL_TYPE_PICKER = 5

_COLUMN_HEADERS = (
    tr("Event"), tr("Start"), tr("Days"), tr("Cameras"),
    tr("Suggested"), tr("Type"),
)


class EventTriageDialog(QDialog):
    """Modal table of unclassified events; per-row Type combo persists immediately."""

    # Emitted whenever the user picks a type for a row — the host (MainWindow)
    # connects this to a dashboard refresh so the Unclassified chip-count
    # shrinks live.
    event_classified = pyqtSignal(str, str)   # event_id, event_type

    def __init__(
        self,
        gateway: Gateway,
        *,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Classify unclassified events"))
        self.setModal(True)
        self.resize(900, 560)
        self._gateway = gateway
        self._rows: List[Dict] = []
        self._build_ui()
        self._reload()

    # ── Build ───────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        hint = QLabel(tr(
            "Pick a type for each event — choices save immediately. "
            "Close when you're done; rows you skip stay unclassified."
        ))
        hint.setObjectName("PageHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._empty = QLabel(tr(
            "No unclassified events. Open an event and use \"Edit info\" "
            "to change its type."))
        self._empty.setObjectName("PageHint")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        self._empty.setVisible(False)
        layout.addWidget(self._empty)

        self._table = QTableWidget(0, len(_COLUMN_HEADERS))
        self._table.setHorizontalHeaderLabels(_COLUMN_HEADERS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        # [[feedback_busy_cursor_on_lag]] GLOBAL UI rule: every table column is
        # user-resizable (Interactive); the trailing column stretches so there
        # is no dead gap. Sensible default widths below so the initial render
        # is balanced — the user can drag any boundary from there.
        header = self._table.horizontalHeader()
        for col in range(len(_COLUMN_HEADERS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self._table.setColumnWidth(_COL_NAME, 280)
        self._table.setColumnWidth(_COL_START, 100)
        self._table.setColumnWidth(_COL_DAYS, 60)
        self._table.setColumnWidth(_COL_CAMERAS, 80)
        self._table.setColumnWidth(_COL_SUGGESTED, 110)
        layout.addWidget(self._table, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    # ── Data load ───────────────────────────────────────────────────────────

    def _reload(self) -> None:
        """Read the unclassified events through the gateway + render rows."""
        self._rows = self._collect_unclassified()
        self._populate_table()

    def _collect_unclassified(self) -> List[Dict]:
        """Build the row list — one dict per unclassified event with the
        coarse signals the heuristic + UI need."""
        listing = self._gateway.events_index_filtered(EventsQuery(
            type=event_classification.EVENT_TYPE_UNCLASSIFIED,
        ))
        rows: List[Dict] = []
        for entry in listing.rows:
            event_id = str(entry.get("id", ""))
            if not event_id:
                continue
            try:
                eg = self._gateway.open_event(event_id)
                try:
                    days = eg.trip_days()
                    cameras = eg.cameras()
                finally:
                    eg.close()
            except Exception:                            # noqa: BLE001
                log.exception("EventTriage: skip event %s (could not open)", event_id)
                continue
            day_count = len(days)
            camera_count = len(cameras)
            tz_count = len({
                d.tz_minutes for d in days if d.tz_minutes is not None
            })
            suggestion = event_classification.suggest_type_from_signals(
                day_count=day_count,
                camera_count=camera_count,
                tz_count=tz_count,
            )
            rows.append({
                "id": event_id,
                "name": entry.get("name") or tr("(unnamed event)"),
                "start_date": entry.get("start_date") or "",
                "day_count": day_count,
                "camera_count": camera_count,
                "tz_count": tz_count,
                "suggestion": suggestion,
            })
        return rows

    def _populate_table(self) -> None:
        self._table.setRowCount(0)
        self._empty.setVisible(not self._rows)
        self._table.setVisible(bool(self._rows))
        for row in self._rows:
            self._add_row(row)

    def _add_row(self, row: Dict) -> None:
        r = self._table.rowCount()
        self._table.insertRow(r)

        self._table.setItem(r, _COL_NAME, QTableWidgetItem(str(row["name"])))
        self._table.setItem(r, _COL_START, QTableWidgetItem(str(row["start_date"])))
        self._table.setItem(r, _COL_DAYS, QTableWidgetItem(str(row["day_count"])))
        self._table.setItem(r, _COL_CAMERAS, QTableWidgetItem(str(row["camera_count"])))
        suggestion = row.get("suggestion")
        suggested_text = (
            event_classification.display_label_for_type(suggestion)
            if suggestion else ""
        )
        self._table.setItem(r, _COL_SUGGESTED, QTableWidgetItem(suggested_text))

        combo = QComboBox()
        combo.addItem(tr("Leave unclassified"), event_classification.EVENT_TYPE_UNCLASSIFIED)
        for et in event_classification.ALL_EVENT_TYPES:
            if et == event_classification.EVENT_TYPE_UNCLASSIFIED:
                continue
            combo.addItem(event_classification.display_label_for_type(et), et)
        # Pre-select the suggestion (but don't fire the persist — we only
        # commit on user interaction).
        if suggestion:
            idx = combo.findData(suggestion)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        event_id = row["id"]
        combo.activated.connect(  # `activated` fires only on user interaction
            lambda _i, eid=event_id, c=combo: self._on_type_picked(eid, c),
        )
        self._table.setCellWidget(r, _COL_TYPE_PICKER, combo)

    def _on_type_picked(self, event_id: str, combo: QComboBox) -> None:
        """Persist the user's pick immediately. On success emit
        ``event_classified`` so the host can refresh dashboard chip counts."""
        chosen = combo.currentData() or event_classification.EVENT_TYPE_UNCLASSIFIED
        try:
            self._gateway.set_classification(event_id, event_type=chosen)
        except Exception as exc:                          # noqa: BLE001
            log.exception("EventTriage: persist failed for %s", event_id)
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, tr("Couldn't save"),
                tr("Type couldn't be persisted for this event.\n\n{err}").replace(
                    "{err}", f"{type(exc).__name__}: {exc}",
                ),
            )
            return
        self.event_classified.emit(event_id, chosen)
