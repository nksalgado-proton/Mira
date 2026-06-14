"""``EventDaysTableDialog`` — the per-day schedule surface (spec/64 §4).

The second half of the events-information split: schedule, not identity.
Edits the per-day rows Collect built up over the event's lifetime;
identity (name, type, context, etc.) lives on ``EventHeaderDialog``
(slice 2).

Per spec/64 §4, three real changes vs. the legacy per-day editor:

* **Focus stops following the mouse pointer (§4.2).** Cell widgets
  receive a wheel-event filter that drops wheel events on widgets that
  don't already have focus, so scrolling the table over a combo /
  picker doesn't change its value or shift focus to it.
* **Country / TZ propagate-down with confirm (§4.3).** Changing the
  country (or TZ) in row N opens a plain yes/no prompt — "Apply the
  new value to the rows below, stopping at the first one you've
  already touched?". On Yes the cascade runs from row N+1 downward,
  walling at the first row the user has previously edited in the
  same column.
* **Location / Description are free text, never required (§4.5).**

The dialog **keeps every feature** the legacy ``PlanDialog`` per-day
editor offered: Include checkbox (with the date label inside the
cell), Browse-day peek button, Country / TZ pickers, Location +
Description editors, Override conflict marker, CSV Save / Load
(premium-gated), Delete-day (opt-in), and the spec/57 §4.2
frozen-after-ingest TZ guard with the single-day-TZ unlock.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Set, Tuple

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import plan_csv
from core.scan_source import OverrideMarker, ScanDayRow
from mira.ui.base.country_picker import (
    country_code_from_combo,
    make_single_country_combo,
)
from mira.ui.base.tables import make_columns_resizable
from mira.ui.base.tz_picker import TzPicker
from mira.ui.i18n import tr

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Column indices — match the legacy PlanDialog per-day table layout. The
# date is the row identity and rides inside the Include cell as the
# checkbox's label (matching the UX Nelson is used to). The Override
# marker column hides itself when no row carries a marker.
# --------------------------------------------------------------------------- #

COL_INCLUDE = 0
COL_BROWSE = 1
COL_COUNTRY = 2
COL_TZ = 3
COL_LOC = 4
COL_DESC = 5
COL_OVERRIDE = 6
COL_COUNT = 7


# Column-name keys used by the user-touched ledger + the propagate-down
# routine (spec/64 §4.3). Country / TZ are the two cascade-eligible
# columns; Location / Description mark touched but never trigger a
# propagate.
TOUCH_COUNTRY = "country"
TOUCH_TZ = "tz"
TOUCH_LOC = "location"
TOUCH_DESC = "description"


class _WheelToTableFilter(QObject):
    """spec/64 §4.2 — Nelson's locked rule, in two parts:

    * **Wheel over an UNFOCUSED cell** (the user hasn't clicked it) →
      swallow it AND forward to the table viewport so the table
      scrolls. Hovering the mouse over a field while wheeling never
      changes the field's value.
    * **Wheel over a FOCUSED cell** (the user has clicked it) → let
      the wheel through unchanged. The combo / picker shifts as Qt
      normally does — the user explicitly engaged the field by
      clicking, so wheel-to-cycle is the wanted behaviour.

    Qt's default delivers wheel events to whichever widget is under
    the cursor regardless of focus state. That meant a stray
    wheel-over-the-cell shifted the value invisibly — the exact bug
    Nelson hit ("you are trying to use the mouse wheel to scroll the
    days table down and you just note it has scrolled the country or
    tz in the fields where the mouse pointer was"). A first cut
    swallowed every wheel, but that broke the legitimate
    click-then-wheel path: "After left clicking on a field with a
    dropdown, the mouse wheel should work over that field." The
    focus-aware fork above gives Nelson both contracts."""

    def __init__(self, viewport: QWidget, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._viewport = viewport

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Wheel:
            if isinstance(obj, QWidget) and obj.hasFocus():
                # User has clicked the field; let the wheel through.
                return super().eventFilter(obj, event)
            # Otherwise forward to the table viewport so the table
            # scrolls; the cell stays untouched.
            QApplication.sendEvent(self._viewport, event)
            return True
        return super().eventFilter(obj, event)


class EventDaysTableDialog(QDialog):
    """The Event Days Table dialog (spec/64 §4).

    Constructor flags carry forward from the legacy PlanDialog per-day
    editor; the host opts each feature in per call site:

    * ``can_save_load_csv`` — show the Save / Load CSV footer buttons
      (premium gate on the plan-editor flow; off by default).
    * ``can_delete_days`` — show the Delete-day footer button (opt-in
      for the Collect → Edit plan flow).
    * ``frozen_after_ingest`` — when an event already has photos filed
      into trip_days, the TZ picker disables (or stays live when
      ``tz_editable_when_frozen=True`` — the spec/57 §4.2 single-day
      TZ unlock the host gates with an explicit re-time confirmation
      at Apply).
    * ``browse_handler(date)`` — clicking the per-day Browse… button
      calls this; without a handler the button stays disabled.
    * ``override_handler(date)`` — clicking the override marker calls
      this; without one the marker stays disabled (still visible if
      the row has one).
    """

    def __init__(
        self,
        rows: Sequence[ScanDayRow],
        *,
        can_save_load_csv: bool = False,
        can_delete_days: bool = False,
        frozen_after_ingest: bool = False,
        tz_editable_when_frozen: bool = False,
        browse_handler: Optional[Callable[[date], None]] = None,
        override_handler: Optional[Callable[[date], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Event Days Table"))
        self.setModal(True)
        self.resize(1200, 720)

        self._rows: List[ScanDayRow] = [
            ScanDayRow(
                date=r.date,
                checked=r.checked,
                country_code=r.country_code,
                tz_minutes=r.tz_minutes,
                location=r.location,
                description=r.description,
                override_marker=r.override_marker,
            )
            for r in rows
        ]
        self._can_save_load_csv = can_save_load_csv
        self._can_delete_days = can_delete_days
        self._frozen_after_ingest = frozen_after_ingest
        self._tz_editable_when_frozen = tz_editable_when_frozen
        self._browse_handler = browse_handler
        self._override_handler = override_handler
        self._was_applied = False

        # Per-cell user-touched ledger (spec/64 §4.3) — keyed on
        # (row_index, column_name). A cell appears here once the user
        # has edited it; the propagate-down cascade walls at the first
        # touched row.
        self._touched: Set[Tuple[int, str]] = set()

        # Re-entrancy guard: setting a value during a cascade should NOT
        # mark the target rows as user-touched (the user only touched
        # the seed row).
        self._cascading: bool = False

        # Re-entrancy guard for CSV-load + bulk programmatic writes —
        # the country / TZ change handlers skip the propagate prompt
        # when the change comes from this seam, not from the user.
        self._loading: bool = False

        self._build_ui()
        # The wheel filter routes wheel events on cell widgets to the
        # table viewport (so the table scrolls instead of the cell
        # changing value). Built AFTER the table so we can hand it the
        # viewport reference; cell widgets install it as they're added.
        self._wheel_filter = _WheelToTableFilter(
            self._table.viewport(), self)
        self._populate_rows()

        # Override column hides itself unless at least one row has a
        # marker (the legacy PlanDialog convention).
        if not any(r.override_marker is not None for r in self._rows):
            self._table.setColumnHidden(COL_OVERRIDE, True)

        # Frozen-after-ingest TZ disable (spec/57 §4.2 — pickers stay
        # live when ``tz_editable_when_frozen=True``; the host gates
        # actual writes with a re-time confirmation).
        if self._frozen_after_ingest and not self._tz_editable_when_frozen:
            for table_row in range(self._table.rowCount()):
                tz_picker = self._table.cellWidget(table_row, COL_TZ)
                if tz_picker is not None:
                    tz_picker.setEnabled(False)

    # ── Build ─────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        from mira.ui.design import (
            ghost_button as _ghost_button,
            primary_button as _primary_button,
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header bar ──
        outer.addWidget(self._build_header_bar())
        outer.addWidget(self._divider())

        # ── Body: table ──
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(22, 16, 22, 16)
        body_layout.setSpacing(10)
        self._table = QTableWidget(0, COL_COUNT)
        self._configure_table()
        body_layout.addWidget(self._table, stretch=1)
        outer.addWidget(body, stretch=1)

        # ── Footer ──
        outer.addWidget(self._divider())
        footer_host = QWidget()
        footer = QHBoxLayout(footer_host)
        footer.setContentsMargins(22, 14, 22, 14)
        footer.setSpacing(10)

        # Left: include count
        self._footer_info = QLabel("")
        self._footer_info.setObjectName("Sub")
        footer.addWidget(self._footer_info)
        footer.addSpacing(18)

        # CSV + delete utility ghost buttons (still left-aligned)
        self._save_csv_button = _ghost_button(tr("Save plan to file…"))
        self._save_csv_button.setToolTip(tr(
            "Save this plan to a CSV file (semicolon-separated; opens "
            "cleanly in Excel). Useful for filling many per-day rows "
            "offline before coming back to import."
        ))
        self._save_csv_button.clicked.connect(self._on_save_csv)
        footer.addWidget(self._save_csv_button)

        self._load_csv_button = _ghost_button(tr("Load plan from file…"))
        self._load_csv_button.setToolTip(tr(
            "Load a plan CSV. Each loaded row matches the day with the "
            "same date and overrides that day's country / time zone / "
            "location / description. Days the file doesn't cover are "
            "left alone."
        ))
        self._load_csv_button.clicked.connect(self._on_load_csv)
        footer.addWidget(self._load_csv_button)
        if not self._can_save_load_csv:
            self._save_csv_button.hide()
            self._load_csv_button.hide()

        self._delete_day_button = _ghost_button(tr("Delete day…"))
        self._delete_day_button.setToolTip(tr(
            "Remove the selected day from the plan. The gateway rejects "
            "removals that would orphan photos already filed under the "
            "day."
        ))
        self._delete_day_button.clicked.connect(self._on_delete_day)
        self._delete_day_button.setEnabled(False)
        footer.addWidget(self._delete_day_button)
        if not self._can_delete_days:
            self._delete_day_button.hide()

        footer.addStretch(1)

        # Cancel + primary Apply days
        cancel_btn = _ghost_button(tr("Cancel"))
        cancel_btn.clicked.connect(self.reject)
        footer.addWidget(cancel_btn)
        self._apply_btn = _primary_button(tr("Apply days"))
        self._apply_btn.clicked.connect(self._on_ok)
        footer.addWidget(self._apply_btn)
        # Keep self._buttons as a parity shim for any test that still
        # looked for it. None of the current tests poke at the OK / Cancel
        # buttons directly via QDialogButtonBox semantics; the dialog's
        # public API (header_info / was_applied) is unchanged.
        self._buttons = None
        outer.addWidget(footer_host)

    @staticmethod
    def _divider() -> QFrame:
        d = QFrame()
        d.setFrameShape(QFrame.Shape.HLine)
        d.setStyleSheet(
            "background: #262b38; max-height: 1px; min-height: 1px;"
        )
        return d

    def _build_header_bar(self) -> QWidget:
        host = QWidget()
        h = QHBoxLayout(host)
        h.setContentsMargins(18, 12, 12, 12)
        h.setSpacing(12)
        # Accent calendar icon tile
        tile = QLabel("📅")
        tile.setFixedSize(36, 36)
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setStyleSheet(
            "background: #211f3a; color: #7c6cff;"
            " border: 1px solid #7c6cff; border-radius: 10px;"
            " font-size: 18px;"
        )
        h.addWidget(tile)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(0)
        title = QLabel(tr("Event Days Table"))
        title.setObjectName("CardTitle")
        text_col.addWidget(title)
        hint = QLabel(tr(
            "Tick the days you want included. Country / time zone / "
            "location / description per day — Location and Description "
            "are free text."
        ))
        hint.setObjectName("Sub")
        hint.setWordWrap(True)
        text_col.addWidget(hint)
        h.addLayout(text_col, 1)

        close = QPushButton("✕")
        close.setObjectName("DialogClose")
        close.setFixedSize(30, 30)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setToolTip(tr("Cancel and close"))
        close.setStyleSheet(
            "QPushButton#DialogClose {"
            " background: transparent; color: #8b94a7;"
            " border: 1px solid #262b38; border-radius: 15px;"
            " font-size: 14px; font-weight: 700;"
            "}"
            "QPushButton#DialogClose:hover { color: #eef1f7; border-color: #7c6cff; }"
        )
        close.clicked.connect(self.reject)
        h.addWidget(close)
        return host

    def _refresh_footer_info(self) -> None:
        """Update the 'N days · N included' summary in the footer left."""
        total = self._table.rowCount() if hasattr(self, "_table") else 0
        included = 0
        for r in range(total):
            cb = self._table.cellWidget(r, COL_INCLUDE)
            if isinstance(cb, QCheckBox) and cb.isChecked():
                included += 1
            else:
                # Some rows wrap the checkbox in a container; locate it.
                if cb is not None:
                    children = cb.findChildren(QCheckBox)
                    if children and children[0].isChecked():
                        included += 1
        suffix_day = "day" if total == 1 else "days"
        self._footer_info.setText(
            f"{total} {suffix_day} · {included} included"
        )

    def _configure_table(self) -> None:
        headers = (
            (tr("Include?"), tr(
                "Tick to include this day in the event. Untick to leave "
                "it out. The date is the row identity and isn't editable."
            )),
            (tr("Browse"), tr(
                "Open a quick preview of this day's photos (read-only) "
                "before deciding whether to import it."
            )),
            (tr("Country"), tr(
                "Country for this day. Auto-filled from phone GPS when "
                "a phone photo was found in this day's scan; editable. "
                "Pick from the dropdown of ISO 3166-1 countries — "
                "search by name or alpha-2 code. Changing this row "
                "offers to apply the value to the days below."
            )),
            (tr("TZ"), tr(
                "Time zone for this day. Auto-filled from phone EXIF "
                "(OffsetTimeOriginal) when a phone photo was found; "
                "editable. Pick the location whose local time the "
                "camera was set to — the named-place picker avoids the "
                "+5:45 vs +5.45 decimal mistake. Changing this row "
                "offers to apply the value to the days below."
            )),
            (tr("Location"), tr(
                "Human-readable location (free text). Auto-filled from "
                "phone GPS reverse-geocode; editable when the geocoded "
                "label is wrong or imprecise."
            )),
            (tr("Description"), tr(
                "Free-text description of the day. Optional. "
                "Auto-filled from the location text by default; if the "
                "source directory is organised per-day (e.g. ‘Day 1 - "
                "Lisbon’), the subdir name takes precedence."
            )),
            (tr("Override"), tr(
                "Shown when a re-scan brought new phone data for this "
                "day that differs from the existing values."
            )),
        )
        for col, (title, tip) in enumerate(headers):
            item = QTableWidgetItem(title)
            item.setToolTip(tip)
            self._table.setHorizontalHeaderItem(col, item)

        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self._table.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers,
        )
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows,
        )
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection,
        )
        self._table.setAlternatingRowColors(True)
        # Selection changes drive the Delete-day footer button's enabled
        # state (harmless when the button is hidden).
        self._table.itemSelectionChanged.connect(
            self._refresh_delete_day_enabled)
        make_columns_resizable(
            self._table,
            widths=(140, 96, 280, 160, 220, 220, 56),
        )
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COL_LOC, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_DESC, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_OVERRIDE, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(COL_OVERRIDE, 56)

    # ── Row population ────────────────────────────────────────────────

    def _populate_rows(self) -> None:
        for row in self._rows:
            self._append_row(row)
        self._refresh_footer_info()

    def _append_row(self, row: ScanDayRow) -> None:
        idx = self._table.rowCount()
        self._table.insertRow(idx)
        self._table.setCellWidget(
            idx, COL_INCLUDE, self._make_include_cell(row))
        self._table.setCellWidget(
            idx, COL_BROWSE, self._make_browse_cell(row.date))
        self._table.setCellWidget(
            idx, COL_COUNTRY, self._make_country_cell(row.country_code, idx))
        self._table.setCellWidget(
            idx, COL_TZ, self._make_tz_cell(row.tz_minutes, idx))
        self._table.setCellWidget(
            idx, COL_LOC, self._make_text_cell(row.location, TOUCH_LOC, idx))
        self._table.setCellWidget(
            idx, COL_DESC, self._make_text_cell(
                row.description, TOUCH_DESC, idx))
        self._table.setCellWidget(
            idx, COL_OVERRIDE,
            self._make_override_cell(row.override_marker, row.date))

    # ── Cell factories ────────────────────────────────────────────────

    def _make_include_cell(self, row: ScanDayRow) -> QWidget:
        """Checkbox + ISO date label. The checkbox text IS the date, so
        the row identity is immediately readable next to the include
        affordance — same UX as the legacy PlanDialog."""
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(8, 2, 8, 2)
        lay.setSpacing(6)
        box = QCheckBox(row.date.isoformat())
        box.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        box.setChecked(bool(row.checked))
        # Surface 04 footer count — update on every include toggle.
        box.stateChanged.connect(lambda _s: self._refresh_footer_info())
        cell.setProperty("_checkbox", box)
        lay.addWidget(box)
        lay.addStretch(1)
        return cell

    def _make_browse_cell(self, day: date) -> QWidget:
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(0)
        btn = QPushButton(tr("Browse…"))
        btn.setObjectName("PlanBrowseCell")
        if self._browse_handler is not None:
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.clicked.connect(
                lambda _checked=False, d=day: self._browse_handler(d))
        else:
            btn.setEnabled(False)
        lay.addWidget(btn)
        return cell

    def _make_country_cell(
        self, initial_code: str, row_idx: int,
    ) -> QComboBox:
        combo = make_single_country_combo(initial_code or None)
        combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        combo.installEventFilter(self._wheel_filter)
        combo.currentIndexChanged.connect(
            lambda _ix, r=row_idx, c=combo: self._on_country_changed(r, c))
        return combo

    def _make_tz_cell(
        self, tz_minutes: Optional[int], row_idx: int,
    ) -> TzPicker:
        initial: Optional[float] = (
            tz_minutes / 60.0 if tz_minutes is not None else None
        )
        picker = TzPicker(initial)
        picker.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        picker.installEventFilter(self._wheel_filter)
        picker.valueChanged.connect(
            lambda _hours, r=row_idx, p=picker: self._on_tz_changed(r, p))
        return picker

    def _make_text_cell(
        self, initial: str, kind: str, row_idx: int,
    ) -> QLineEdit:
        editor = QLineEdit(initial or "")
        if kind == TOUCH_LOC:
            editor.setPlaceholderText(tr("e.g. Lisbon, Portugal"))
        else:
            editor.setPlaceholderText(tr("Describe the day's activities…"))
        editor.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        editor.installEventFilter(self._wheel_filter)
        editor.textEdited.connect(
            lambda _text, r=row_idx, k=kind: self._touched.add((r, k)))
        return editor

    def _make_override_cell(
        self, marker: Optional[OverrideMarker], day: date,
    ) -> QWidget:
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(0)
        if marker is None:
            return cell
        btn = QPushButton("⚠")
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn.setToolTip(tr(
            "A re-scan brought new phone data that differs from the "
            "existing values for this day. Click to compare and pick."
        ))
        if self._override_handler is not None:
            btn.clicked.connect(
                lambda _checked=False, d=day: self._override_handler(d))
        else:
            btn.setEnabled(False)
        lay.addWidget(btn)
        return cell

    # ── Cell-change handlers + propagate-down (§4.3) ──────────────────

    def _on_country_changed(self, row_idx: int, combo: QComboBox) -> None:
        if self._cascading or self._loading:
            return
        new_code = country_code_from_combo(combo)
        self._touched.add((row_idx, TOUCH_COUNTRY))
        self._maybe_propagate_country(row_idx, new_code)

    def _on_tz_changed(self, row_idx: int, picker: TzPicker) -> None:
        if self._cascading or self._loading:
            return
        new_hours = picker.value()
        self._touched.add((row_idx, TOUCH_TZ))
        self._maybe_propagate_tz(row_idx, new_hours)

    def _candidate_rows_below(self, row_idx: int, touch_key: str) -> List[int]:
        candidates: List[int] = []
        for r in range(row_idx + 1, self._table.rowCount()):
            if (r, touch_key) in self._touched:
                break
            candidates.append(r)
        return candidates

    def _maybe_propagate_country(
        self, row_idx: int, new_code: Optional[str],
    ) -> None:
        candidates = self._candidate_rows_below(row_idx, TOUCH_COUNTRY)
        if not candidates:
            return
        if not self._confirm_propagate(tr("country")):
            return
        self._cascading = True
        try:
            for r in candidates:
                combo = self._table.cellWidget(r, COL_COUNTRY)
                if not isinstance(combo, QComboBox):
                    continue
                idx = combo.findData((new_code or "").upper())
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                else:
                    combo.setCurrentIndex(0)
        finally:
            self._cascading = False

    def _maybe_propagate_tz(self, row_idx: int, new_hours: float) -> None:
        candidates = self._candidate_rows_below(row_idx, TOUCH_TZ)
        if not candidates:
            return
        if not self._confirm_propagate(tr("time zone")):
            return
        self._cascading = True
        try:
            for r in candidates:
                picker = self._table.cellWidget(r, COL_TZ)
                if not isinstance(picker, TzPicker):
                    continue
                picker.setValue(new_hours)
        finally:
            self._cascading = False

    def _confirm_propagate(self, field_name: str) -> bool:
        """spec/64 §4.3: plain yes/no — "Apply the new value to the
        rows below, stopping at the first one you've already touched?"
        ``Icon.NoIcon`` per memory ``feedback_qmessagebox_chrome_disliked``.
        Tests stub via :meth:`set_propagate_confirm`."""
        if self._propagate_confirm_override is not None:
            return self._propagate_confirm_override
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setWindowTitle(tr("Apply to the days below?"))
        box.setText(tr(
            "Apply the new {field} to the rows below, stopping at the "
            "first one you've already touched?"
        ).replace("{field}", field_name))
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.Yes)
        result = box.exec()
        return result == QMessageBox.StandardButton.Yes

    _propagate_confirm_override: Optional[bool] = None

    def set_propagate_confirm(self, value: Optional[bool]) -> None:
        """Force the next propagate-down confirms to return ``value``
        without showing a modal. ``None`` restores the real prompt."""
        self._propagate_confirm_override = value

    # ── Delete-day (opt-in via ``can_delete_days=True``) ─────────────

    def _refresh_delete_day_enabled(self) -> None:
        if not self._can_delete_days:
            return
        rows_selected = {ix.row() for ix in self._table.selectedIndexes()}
        self._delete_day_button.setEnabled(bool(rows_selected))

    def _on_delete_day(self) -> None:
        """Remove the currently-selected day(s) from the plan after
        confirmation. The gateway's ``save_trip_days`` rejects removals
        that would orphan photos already filed under the day."""
        rows_selected = sorted({
            ix.row() for ix in self._table.selectedIndexes()
        }, reverse=True)
        if not rows_selected:
            return
        dates = [self._rows[r].date.isoformat() for r in rows_selected
                 if 0 <= r < len(self._rows)]
        if not dates:
            return
        if not self._confirm_delete_days(dates):
            return
        for table_row in rows_selected:
            if 0 <= table_row < len(self._rows):
                del self._rows[table_row]
                self._table.removeRow(table_row)
        self._refresh_delete_day_enabled()

    def _confirm_delete_days(self, dates: List[str]) -> bool:
        if self._delete_confirm_override is not None:
            return self._delete_confirm_override
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setWindowTitle(tr("Delete day(s) from the plan"))
        if len(dates) == 1:
            text = tr("Remove {date} from the plan?").replace(
                "{date}", dates[0])
        else:
            text = tr("Remove {n} day(s) from the plan?\n\n{list}") \
                .replace("{n}", str(len(dates))) \
                .replace("{list}", ", ".join(dates))
        box.setText(text)
        box.setInformativeText(tr(
            "Photos already filed under a day cannot be orphaned — the "
            "gateway rejects the save if that would happen, and you can "
            "edit again."
        ))
        remove_btn = box.addButton(
            tr("Remove"), QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(remove_btn)
        box.exec()
        return box.clickedButton() is remove_btn

    _delete_confirm_override: Optional[bool] = None

    def set_delete_confirm(self, value: Optional[bool]) -> None:
        """Force the next Delete-day confirm to return ``value``
        without showing a modal. ``None`` restores the real prompt."""
        self._delete_confirm_override = value

    # ── CSV save / load (opt-in via ``can_save_load_csv=True``) ──────

    def _on_save_csv(self) -> None:
        current = self.rows()
        csv_rows = [
            plan_csv.PlanCsvRow(
                date=r.date,
                country=(r.country_code or ""),
                tz_minutes=r.tz_minutes,
                location=(r.location or ""),
                description=(r.description or ""),
            )
            for r in current
        ]
        chosen = self._csv_save_path or QFileDialog.getSaveFileName(
            self, tr("Save plan to file"), "plan.csv",
            tr("CSV files (*.csv);;All files (*.*)"),
        )[0]
        if not chosen:
            return
        try:
            plan_csv.save_to_path(csv_rows, Path(chosen))
        except OSError as exc:
            QMessageBox.warning(
                self, tr("Could not save plan"),
                tr("Failed to write {path}: {err}")
                .replace("{path}", chosen).replace("{err}", str(exc)),
            )
            return
        log.info("Days Table saved CSV to %s (%d rows)",
                 chosen, len(csv_rows))

    def _on_load_csv(self) -> None:
        chosen = self._csv_load_path or QFileDialog.getOpenFileName(
            self, tr("Load plan from file"), "",
            tr("CSV files (*.csv);;All files (*.*)"),
        )[0]
        if not chosen:
            return
        try:
            loaded = plan_csv.load_from_path(Path(chosen))
        except (OSError, plan_csv.PlanCsvError) as exc:
            QMessageBox.warning(
                self, tr("Could not load plan"),
                tr("Failed to read {path}: {err}")
                .replace("{path}", chosen).replace("{err}", str(exc)),
            )
            return

        scan_dates = [r.date for r in self._rows]
        outcome = plan_csv.apply_to_scan_days(loaded, scan_dates)
        by_date = {r.date: r for r in loaded}
        applied_set = set(outcome.applied_dates)

        self._loading = True
        try:
            for table_row, scan_row in enumerate(self._rows):
                if scan_row.date not in applied_set:
                    continue
                self._apply_loaded_to_table_row(table_row, by_date[scan_row.date])
        finally:
            self._loading = False

        if outcome.unmatched_dates:
            QMessageBox.information(
                self, tr("Plan loaded — some dates skipped"),
                tr(
                    "Loaded {n} row(s) into matching days. {m} row(s) "
                    "in the file had dates that aren't in this scan "
                    "and were skipped."
                )
                .replace("{n}", str(len(applied_set)))
                .replace("{m}", str(len(outcome.unmatched_dates))),
            )
        log.info(
            "Days Table loaded CSV from %s: %d applied, %d unmatched",
            chosen, len(applied_set), len(outcome.unmatched_dates),
        )

    # Test seams — let the suite drive the CSV save/load without a
    # native file dialog. ``None`` = real ``QFileDialog``.
    _csv_save_path: Optional[str] = None
    _csv_load_path: Optional[str] = None

    def set_csv_paths(
        self, *, save: Optional[str] = None, load: Optional[str] = None,
    ) -> None:
        self._csv_save_path = save
        self._csv_load_path = load

    def _apply_loaded_to_table_row(
        self, table_row: int, loaded: "plan_csv.PlanCsvRow",
    ) -> None:
        country_combo = self._table.cellWidget(table_row, COL_COUNTRY)
        if isinstance(country_combo, QComboBox):
            code = (loaded.country or "").upper()
            idx = country_combo.findData(code) if code else 0
            country_combo.setCurrentIndex(idx if idx >= 0 else 0)

        tz_picker = self._table.cellWidget(table_row, COL_TZ)
        if (isinstance(tz_picker, TzPicker)
                and loaded.tz_minutes is not None
                and not self._frozen_after_ingest):
            # spec/57 §4.2 — frozen-after-ingest: CSV-load ignores TZ so
            # a re-imported plan can't shift photos across a TZ boundary.
            tz_picker.setValue(loaded.tz_minutes / 60.0)

        loc_editor = self._table.cellWidget(table_row, COL_LOC)
        if isinstance(loc_editor, QLineEdit):
            loc_editor.setText(loaded.location or "")

        desc_editor = self._table.cellWidget(table_row, COL_DESC)
        if isinstance(desc_editor, QLineEdit):
            desc_editor.setText(loaded.description or "")

    # ── Output ────────────────────────────────────────────────────────

    def rows(self) -> List[ScanDayRow]:
        """Snapshot the current row state, picking each cell's value out
        of the table widgets. The ``date`` + ``override_marker`` carry
        through from the input."""
        out: List[ScanDayRow] = []
        for r, src in enumerate(self._rows):
            include_cell = self._table.cellWidget(r, COL_INCLUDE)
            checkbox = (
                include_cell.property("_checkbox")
                if include_cell is not None else None
            )
            checked = bool(checkbox.isChecked()) if checkbox is not None else True

            country_combo = self._table.cellWidget(r, COL_COUNTRY)
            country_code = (
                country_code_from_combo(country_combo)
                if isinstance(country_combo, QComboBox)
                else src.country_code
            ) or ""

            tz_widget = self._table.cellWidget(r, COL_TZ)
            tz_minutes: Optional[int]
            if isinstance(tz_widget, TzPicker):
                hours = tz_widget.value()
                tz_minutes = (
                    int(round(hours * 60)) if hours is not None else None)
            else:
                tz_minutes = src.tz_minutes

            loc_edit = self._table.cellWidget(r, COL_LOC)
            description_edit = self._table.cellWidget(r, COL_DESC)
            location = (
                loc_edit.text() if isinstance(loc_edit, QLineEdit)
                else (src.location or ""))
            description = (
                description_edit.text()
                if isinstance(description_edit, QLineEdit)
                else (src.description or ""))
            out.append(ScanDayRow(
                date=src.date,
                checked=checked,
                country_code=country_code,
                tz_minutes=tz_minutes,
                location=location,
                description=description,
                override_marker=src.override_marker,
            ))
        return out

    # ── Accept guard ──────────────────────────────────────────────────

    def _on_ok(self) -> None:
        self._was_applied = True
        self.accept()

    def was_applied(self) -> bool:
        return self._was_applied
