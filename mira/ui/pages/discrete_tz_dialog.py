"""``DiscreteTzDialog`` — per-(camera, day) discrete TZ pick (spec/45 Slice TZ-3).

Phone EXIF tells us the *actual* timezone for each day (Slice TZ-1). Cameras
don't carry a TZ, so the user has to declare what TZ each non-phone camera was
*set to* on each day. The correction at bake time is then a simple subtraction:
``trip_day.tz_minutes - camera_day_tz.declared_tz_minutes``.

The picker is **discrete** by design — real-world TZs come in fixed increments;
a continuous hours+minutes picker invites wrong-minute precision errors that
this dialog eliminates by construction.

One dialog handles every (camera, day) pair that needs an answer. Days where
the camera matches the phone (no question to ask) are pre-filled and disabled,
keeping the dialog short. The host (capture flow) collects answers via
:meth:`picked_tz_for` after Apply.

Phones (anything Slice TZ-1's :func:`core.phone_tz.is_phone_source` flagged)
never appear as a row in the dialog — their EXIF IS the answer.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.discrete_tz import (
    STANDARD_TZ_OFFSETS_MINUTES,
    display_label_for_offset,
    format_offset,
    nearest_valid_offset,
)
from mira.ui.i18n import tr

log = logging.getLogger(__name__)


_COL_DAY = 0
_COL_PHONE_TZ = 1
_COL_CAMERA = 2
_COL_CAMERA_TZ = 3

_COLUMN_HEADERS = (tr("Day"), tr("Phone TZ"), tr("Camera"), tr("Camera TZ"))


class DiscreteTzDialog(QDialog):
    """Modal table: one row per (day, non-phone-camera). Each row picks a TZ
    from the discrete enum."""

    # Emitted on Apply with ``{(camera_id, day_number): tz_minutes, …}``.
    answers_collected = pyqtSignal(dict)

    def __init__(
        self,
        *,
        phone_day_tz: Dict[int, int],
        rows: Sequence[Tuple[int, str, str, Optional[int]]],
        parent: Optional[QWidget] = None,
    ) -> None:
        """``rows`` = list of ``(day_number, date_iso, camera_id, seed_tz)``.
        The capture flow assembles these from its per-day camera inventory;
        ``seed_tz`` is the best initial guess (last-used / pair-picker fallback /
        snapped phone value), or ``None`` to default the picker to the phone's
        TZ for that day."""
        super().__init__(parent)
        self.setWindowTitle(tr("Confirm camera timezones"))
        self.setModal(True)
        self.resize(880, 520)
        self._phone_day_tz = dict(phone_day_tz)
        self._rows = list(rows)
        self._answers: Dict[Tuple[str, int], int] = {}
        self._build_ui()

    # ── Build ───────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        hint = QLabel(tr(
            "Your phone told us what timezone each day was on. The cameras "
            "below didn't write the timezone to their files, so pick the "
            "timezone each one was set to that day. The correction is the "
            "difference between the two — exact, no minutes to nudge."
        ))
        hint.setObjectName("PageHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._empty = QLabel(tr(
            "Nothing to confirm — all the cameras matched the phone."
        ))
        self._empty.setObjectName("PageHint")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setVisible(False)
        layout.addWidget(self._empty)

        self._table = QTableWidget(0, len(_COLUMN_HEADERS))
        self._table.setHorizontalHeaderLabels(_COLUMN_HEADERS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        # [[feedback_busy_cursor_on_lag]] global UI rule: every table column is
        # user-resizable; trailing column stretches.
        header = self._table.horizontalHeader()
        for col in range(len(_COLUMN_HEADERS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self._table.setColumnWidth(_COL_DAY, 110)
        self._table.setColumnWidth(_COL_PHONE_TZ, 100)
        self._table.setColumnWidth(_COL_CAMERA, 220)
        layout.addWidget(self._table, stretch=1)

        self._populate_table()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _populate_table(self) -> None:
        self._table.setRowCount(0)
        self._empty.setVisible(not self._rows)
        self._table.setVisible(bool(self._rows))
        for day, date_iso, camera_id, seed_tz in self._rows:
            self._add_row(day, date_iso, camera_id, seed_tz)

    def _add_row(
        self, day: int, date_iso: str, camera_id: str, seed_tz: Optional[int],
    ) -> None:
        r = self._table.rowCount()
        self._table.insertRow(r)

        # Day label — "Day 3 · 2026-04-03"
        day_text = f"Day {day}" + (f" · {date_iso}" if date_iso else "")
        self._table.setItem(r, _COL_DAY, QTableWidgetItem(day_text))

        # Phone TZ readout — the truth we're correcting toward.
        phone_tz = self._phone_day_tz.get(day)
        phone_text = format_offset(phone_tz) if phone_tz is not None else "—"
        self._table.setItem(r, _COL_PHONE_TZ, QTableWidgetItem(phone_text))

        # Camera id (column 2)
        self._table.setItem(r, _COL_CAMERA, QTableWidgetItem(camera_id))

        # Discrete TZ combo (column 3)
        combo = QComboBox()
        for offset in STANDARD_TZ_OFFSETS_MINUTES:
            combo.addItem(display_label_for_offset(offset), offset)
        # Pre-select: seed_tz (if valid) → snapped seed → phone TZ → 0.
        default_offset: Optional[int] = None
        if seed_tz is not None:
            default_offset = (
                seed_tz if seed_tz in STANDARD_TZ_OFFSETS_MINUTES
                else nearest_valid_offset(seed_tz)
            )
        if default_offset is None and phone_tz is not None:
            default_offset = (
                phone_tz if phone_tz in STANDARD_TZ_OFFSETS_MINUTES
                else nearest_valid_offset(phone_tz)
            )
        if default_offset is None:
            default_offset = 0
        idx = combo.findData(default_offset)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        combo.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._table.setCellWidget(r, _COL_CAMERA_TZ, combo)

    # ── Public API ──────────────────────────────────────────────────────────

    def picked_tz_for(self, camera_id: str, day_number: int) -> Optional[int]:
        """The user's answer for one row, or ``None`` if not collected
        (Cancel pressed before Accept)."""
        return self._answers.get((camera_id, day_number))

    def all_answers(self) -> Dict[Tuple[str, int], int]:
        """``{(camera_id, day_number): tz_minutes, …}`` after Accept."""
        return dict(self._answers)

    # ── Internals ───────────────────────────────────────────────────────────

    def _on_accept(self) -> None:
        """Read every combo into the answers dict, fire the signal, accept."""
        self._answers = {}
        for r, (day, _date_iso, camera_id, _seed) in enumerate(self._rows):
            combo = self._table.cellWidget(r, _COL_CAMERA_TZ)
            if isinstance(combo, QComboBox):
                tz = combo.currentData()
                if tz is None:
                    continue
                self._answers[(camera_id, day)] = int(tz)
        self.answers_collected.emit(self.all_answers())
        self.accept()


def rows_needing_answers(
    *,
    phone_day_tz: Dict[int, int],
    cameras_by_day: Dict[int, List[str]],
    phone_camera_ids: Sequence[str],
    seed_tz: Dict[Tuple[str, int], int],
    days: Sequence[Tuple[int, str]],
) -> List[Tuple[int, str, str, Optional[int]]]:
    """Capture flow → DiscreteTzDialog row list.

    For each (day, non-phone-camera) pair where the camera might be on a
    different TZ than the phone, emit one row. Days the phone didn't cover
    fall back to the legacy pair-picker flow (no row here — the caller
    detects that case from an empty result).

    * ``phone_day_tz`` — :func:`core.phone_tz.phone_day_tz` output.
    * ``cameras_by_day`` — for each day, the camera_ids the user actually
      captured with that day.
    * ``phone_camera_ids`` — the camera_ids flagged as phones (so they're
      excluded from the row list).
    * ``seed_tz`` — best initial guess per (camera, day) (last-used /
      pair-picker fallback). Empty when no prior data.
    * ``days`` — ``[(day_number, date_iso), …]`` for label rendering.
    """
    phone_set = set(phone_camera_ids)
    rows: List[Tuple[int, str, str, Optional[int]]] = []
    date_by_day = dict(days)
    for day_number, _ in days:
        if day_number not in phone_day_tz:
            continue        # no phone for this day → legacy flow
        for camera_id in cameras_by_day.get(day_number, ()):
            if camera_id in phone_set:
                continue
            rows.append((
                day_number,
                date_by_day.get(day_number, ""),
                camera_id,
                seed_tz.get((camera_id, day_number)),
            ))
    return rows
