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
from typing import Callable, List, Mapping, Optional, Sequence, Set, Tuple

from PyQt6.QtCore import QEvent, QObject, QRect, QSize, Qt
from PyQt6.QtGui import QColor, QCursor, QIcon, QPainter
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
    QStyledItemDelegate,
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
from mira.ui.design import (
    GLYPH_CROSS,
    GLYPH_EVENT,
    GLYPH_EYE,
    GLYPH_MAP,
    tinted_svg_pixmap,
)
from mira.ui.i18n import tr
from mira.ui.palette import PALETTE

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
# spec/155 — per-day map slot chip. Sits between Description and the
# (often-hidden) Override marker so it's adjacent to the day metadata
# it visualises.
COL_MAP = 6
COL_OVERRIDE = 7
COL_COUNT = 8


# Column-name keys used by the user-touched ledger + the propagate-down
# routine (spec/64 §4.3). Country / TZ are the two cascade-eligible
# columns; Location / Description mark touched but never trigger a
# propagate.
TOUCH_COUNTRY = "country"
TOUCH_TZ = "tz"
TOUCH_LOC = "location"
TOUCH_DESC = "description"


def _palette_mode() -> str:
    app = QApplication.instance()
    return (app.property("theme") if app else None) or "dark"


class _SelectedRowEdgeDelegate(QStyledItemDelegate):
    """Paint a 3px accent left-edge on the first column of every selected
    row. The mockup carries it via `tbody tr.sel td:first-child {
    box-shadow: inset 3px 0 0 var(--accent); }`; Qt QSS can't target "first
    column of selected row" cleanly, so this delegate handles it instead.
    The row-wide accent_soft wash already comes from
    `QTableWidget::item:selected` in the QSS — this just lays the edge on
    top so the selection has the redesigned visual cleft.
    """

    _EDGE_WIDTH = 3

    def paint(self, painter, option, index):  # noqa: D401, N802 — Qt override
        super().paint(painter, option, index)
        if index.column() != 0:
            return
        selected = bool(option.state & option.state.State_Selected)
        if not selected:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        accent = QColor(PALETTE[_palette_mode()]["accent"])
        rect = QRect(option.rect)
        rect.setWidth(self._EDGE_WIDTH)
        painter.fillRect(rect, accent)
        painter.restore()


class _FocusGuardedLineEdit(QLineEdit):
    """``QLineEdit`` that refuses focus from anything other than a
    left-click, Tab/Backtab, keyboard shortcut, or popup transfer.

    Nelson 2026-06-29 — Qt's QTableWidget cell widgets surface a long-
    standing quirk: focus follows the mouse across cells, so simply
    hovering a cell makes the embedded QLineEdit / QComboBox look
    focused. The same fix the country picker + TzPicker apply for
    themselves (rejecting non-allowed FocusIn reasons) is wrapped here
    for the plain text cells (Location / Description).

    ``clearFocus()`` runs on the next event-loop tick so the synchronous
    focus-in chain finishes before the cell is un-focused — re-entering
    Qt's focus machinery inside :meth:`focusInEvent` is otherwise a
    light footgun.
    """

    def focusInEvent(self, event) -> None:  # noqa: N802
        allowed = (
            Qt.FocusReason.MouseFocusReason,
            Qt.FocusReason.TabFocusReason,
            Qt.FocusReason.BacktabFocusReason,
            Qt.FocusReason.ShortcutFocusReason,
            Qt.FocusReason.PopupFocusReason,
        )
        if event.reason() not in allowed:
            # Sync clearFocus: Qt drains the focusOut chain before the
            # call returns. Skipping the super().focusInEvent leaves
            # the widget's selection/cursor untouched.
            self.clearFocus()
            return
        super().focusInEvent(event)


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
        # spec/155 — when the host wires the per-event gateway + the
        # date→day_number map, the dialog grows the per-day map chip
        # column + the event-header chip; chip clicks open the inline
        # MapAttachDialog and persist via the gateway. Both stay
        # ``None`` on the new-event scan path (no event.db yet).
        gateway=None,
        day_number_by_date: Optional[Mapping[date, int]] = None,
        event_map_path: Optional[str] = None,
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
                map_image_path=r.map_image_path,
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
        # spec/155 — map-chip plumbing. Both ``None`` ⇒ chip column +
        # event-header chip stay hidden.
        self._gateway = gateway
        self._day_number_by_date = dict(day_number_by_date or {})
        self._event_map_rel: Optional[str] = event_map_path
        self._event_root = (
            Path(gateway.event_root) if gateway is not None
            and getattr(gateway, "event_root", None) is not None else None)
        self._maps_enabled = (
            self._gateway is not None and self._event_root is not None)
        self._event_map_chip = None  # set in _build_header_bar

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
        # spec/155 — hide the per-day Map column when the host didn't
        # wire the gateway (new-event scan path; no event.db yet).
        if not self._maps_enabled:
            self._table.setColumnHidden(COL_MAP, True)

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
        self._table.setObjectName("EventDaysTable")
        # 3px accent left-edge on the selected row (mockup .sel td:first-child).
        # Kept on the instance so Qt doesn't garbage-collect it.
        self._selection_delegate = _SelectedRowEdgeDelegate(self._table)
        self._table.setItemDelegate(self._selection_delegate)
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
        d.setObjectName("DialogDivider")  # themed hairline (redesign.qss)
        return d

    def _build_header_bar(self) -> QWidget:
        host = QWidget()
        h = QHBoxLayout(host)
        h.setContentsMargins(18, 14, 14, 14)
        h.setSpacing(12)
        p = PALETTE[_palette_mode()]
        # Accent calendar icon tile — line-icon family event glyph (the
        # spec/65 §3.2 / §2.1 fix that landed for Surface 02 also applies
        # here: Unicode 📅 reads as a colour emoji + the inline #211f3a
        # broke in light mode). Theme-aware tile colours so the tile reads
        # right against both surfaces.
        tile = QLabel()
        tile.setFixedSize(32, 32)
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setObjectName("CutHeaderTile")  # shared accent-soft dialog tile (redesign.qss)
        tile.setPixmap(
            tinted_svg_pixmap(GLYPH_EVENT, 18, QColor(p["accent"]))
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

        # spec/155 — event-level map button. Mirrors the per-row Map
        # button's chrome (#PlanBrowseCell + tinted 16 px line glyph) so
        # both controls speak the same visual language. Hidden when the
        # host didn't wire the gateway (new-event scan path; no event.db
        # yet).
        if self._maps_enabled and self._event_root is not None:
            self._event_map_chip = QPushButton()
            self._event_map_chip.setObjectName("PlanBrowseCell")
            attached_e = bool(self._event_map_rel)
            tint_e = (QColor(p["accent"]) if attached_e
                      else QColor(p["ink_soft"]))
            self._event_map_chip.setIcon(QIcon(
                tinted_svg_pixmap(GLYPH_MAP, 16, tint_e)))
            self._event_map_chip.setIconSize(QSize(16, 16))
            self._event_map_chip.setToolTip(
                tr("Replace or remove the event map.") if attached_e
                else tr("Attach a map for the whole event."))
            self._event_map_chip.setCursor(
                QCursor(Qt.CursorShape.PointingHandCursor))
            self._event_map_chip.clicked.connect(
                self._open_event_map_dialog)
            h.addWidget(self._event_map_chip)

        # Close X — line-icon cross.svg in a 9px squircle (mockup .modal-head
        # .x). Same fix as Surface 02: Unicode ✕ was invisible in both
        # themes; rendering via QIcon tints correctly per theme.
        close = QPushButton()
        close.setObjectName("DialogClose")
        close.setFixedSize(30, 30)
        close.setIcon(QIcon(
            tinted_svg_pixmap(GLYPH_CROSS, 14, QColor(p["ink_soft"]))
        ))
        close.setIconSize(QSize(14, 14))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setToolTip(tr("Cancel and close"))  # styled by QPushButton#DialogClose (redesign.qss)
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
            (tr("Map"), tr(
                "Attach an image (JPEG / PNG) of the day's geography. "
                "Shown in Cut day-separator slides; the user supplies "
                "the image (Mira never fetches map tiles)."
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
        # Location stays Interactive (user-draggable) so the divider BETWEEN
        # Location and Description can be moved — dragging it left grows the
        # Description field. Only Description stretches to fill the remainder;
        # two adjacent Stretch columns share a divider that can't be dragged
        # (Nelson 2026-06-20). Location seeds at 220 px from make_columns_resizable.
        header.setSectionResizeMode(COL_DESC, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_MAP, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(COL_MAP, 96)
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
            idx, COL_MAP, self._make_map_cell(row, idx))
        self._table.setCellWidget(
            idx, COL_OVERRIDE,
            self._make_override_cell(row.override_marker, row.date))

    # ── Cell factories ────────────────────────────────────────────────

    def _make_include_cell(self, row: ScanDayRow) -> QWidget:
        """Checkbox + ISO date label. The checkbox text IS the date, so
        the row identity is immediately readable next to the include
        affordance — same UX as the legacy PlanDialog. ObjectName picks
        up the redesigned 18px accent-fill check tile (QSS rules in
        redesign.qss) instead of the legacy 14px checkbox."""
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(8, 2, 8, 2)
        lay.setSpacing(8)
        box = QCheckBox(row.date.isoformat())
        box.setObjectName("DaysTableCheck")
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
        # Eye icon instead of the "Browse…" label (Nelson 2026-06-20). The
        # column header still reads "Browse"; the per-row control is just the
        # glyph. Tinted per theme like the other dialog icons.
        btn = QPushButton()
        btn.setObjectName("PlanBrowseCell")
        p = PALETTE[_palette_mode()]
        btn.setIcon(QIcon(
            tinted_svg_pixmap(GLYPH_EYE, 16, QColor(p["ink_soft"]))))
        btn.setIconSize(QSize(16, 16))
        btn.setToolTip(tr("Browse this day's photos and videos (read-only)."))
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
        # Surface 04 redesigned cell chrome (#DaysCellSelect) — gives the
        # combo the card2 / accent-focus styling + the Card-styled popup
        # the mockup carries instead of Qt-native.
        combo.setObjectName("DaysCellSelect")
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
        # TzPicker subclasses QComboBox + already carries its own
        # objectName; ride on the days-cell select rule so the per-row TZ
        # field matches the country combo's redesigned chrome.
        picker.setObjectName("DaysCellSelect")
        picker.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        picker.installEventFilter(self._wheel_filter)
        picker.valueChanged.connect(
            lambda _hours, r=row_idx, p=picker: self._on_tz_changed(r, p))
        return picker

    def _make_text_cell(
        self, initial: str, kind: str, row_idx: int,
    ) -> QLineEdit:
        # Nelson 2026-06-29 — _FocusGuardedLineEdit blocks the hover-
        # induced focus theft so the field's accent border only lights
        # up after a real left-click or a Tab.
        editor = _FocusGuardedLineEdit(initial or "")
        editor.setObjectName("DaysCellInput")
        if kind == TOUCH_LOC:
            editor.setPlaceholderText(tr("e.g. Lisbon, Portugal"))
        else:
            editor.setPlaceholderText(tr("Describe the day's activities…"))
        editor.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        editor.installEventFilter(self._wheel_filter)
        editor.textEdited.connect(
            lambda _text, r=row_idx, k=kind: self._touched.add((r, k)))
        return editor

    # ── Map chip (spec/155) ───────────────────────────────────────

    def _make_map_cell(self, row: ScanDayRow, idx: int) -> QWidget:
        """The per-day map slot button. Mirrors :meth:`_make_browse_cell`'s
        chrome (``#PlanBrowseCell`` QSS role + tinted 16 px line glyph) so
        it lives within the cell space cleanly. Click opens
        :class:`MapAttachDialog`. Hidden (placeholder cell) when the
        gateway isn't wired in (new-event scan path)."""
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(0)
        if not self._maps_enabled:
            return cell
        btn = QPushButton()
        btn.setObjectName("PlanBrowseCell")
        p = PALETTE[_palette_mode()]
        # Attached state tints the glyph accent so the user can tell at a
        # glance which days have a map; empty state is ink-soft, matching
        # the Browse / Override buttons.
        attached = bool(row.map_image_path)
        tint = QColor(p["accent"]) if attached else QColor(p["ink_soft"])
        btn.setIcon(QIcon(tinted_svg_pixmap(GLYPH_MAP, 16, tint)))
        btn.setIconSize(QSize(16, 16))
        btn.setToolTip(
            tr("Replace or remove the day's map.") if attached
            else tr("Attach a map for this day (JPEG, PNG or MP4)."))
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn.clicked.connect(
            lambda _checked=False, i=idx: self._open_map_dialog_for_row(i))
        # Stash on the cell so :meth:`_open_map_dialog_for_row`'s
        # ``mapChanged`` handler can re-tint the button + refresh tooltip.
        cell.setProperty("_map_button", btn)
        lay.addWidget(btn)
        return cell

    def _row_map_button(self, idx: int):
        cell = self._table.cellWidget(idx, COL_MAP)
        if cell is None:
            return None
        return cell.property("_map_button")

    def _refresh_row_map_button(self, idx: int) -> None:
        """Re-tint the per-row map button + refresh tooltip after the
        attach dialog reports a change. Mirrors the chip's previous
        in-place refresh — same role, smaller surface."""
        btn = self._row_map_button(idx)
        if btn is None:
            return
        attached = bool(self._rows[idx].map_image_path)
        p = PALETTE[_palette_mode()]
        tint = QColor(p["accent"]) if attached else QColor(p["ink_soft"])
        btn.setIcon(QIcon(tinted_svg_pixmap(GLYPH_MAP, 16, tint)))
        btn.setIconSize(QSize(16, 16))
        btn.setToolTip(
            tr("Replace or remove the day's map.") if attached
            else tr("Attach a map for this day (JPEG, PNG or MP4)."))

    def _open_map_dialog_for_row(self, idx: int) -> None:
        if not self._maps_enabled or self._gateway is None:
            return
        row = self._rows[idx]
        day_number = self._day_number_by_date.get(row.date)
        if day_number is None:
            return
        from mira.ui.base.map_attach_dialog import MapAttachDialog
        dlg = MapAttachDialog(
            self._gateway, day_number=day_number, parent=self)

        def _on_changed() -> None:
            new_rel = self._gateway.get_day_map_path(day_number)
            row.map_image_path = new_rel
            self._refresh_row_map_button(idx)

        dlg.mapChanged.connect(_on_changed)
        dlg.exec()

    def _open_event_map_dialog(self) -> None:
        if not self._maps_enabled or self._gateway is None:
            return
        from mira.ui.base.map_attach_dialog import MapAttachDialog
        dlg = MapAttachDialog(self._gateway, day_number=None, parent=self)

        def _on_changed() -> None:
            self._event_map_rel = self._gateway.get_event_map_path()
            self._refresh_event_map_button()

        dlg.mapChanged.connect(_on_changed)
        dlg.exec()

    def _refresh_event_map_button(self) -> None:
        """Re-tint the header event-map button + refresh tooltip after
        the attach dialog reports a change."""
        if self._event_map_chip is None:
            return
        attached = bool(self._event_map_rel)
        p = PALETTE[_palette_mode()]
        tint = QColor(p["accent"]) if attached else QColor(p["ink_soft"])
        self._event_map_chip.setIcon(QIcon(
            tinted_svg_pixmap(GLYPH_MAP, 16, tint)))
        self._event_map_chip.setIconSize(QSize(16, 16))
        self._event_map_chip.setToolTip(
            tr("Replace or remove the event map.") if attached
            else tr("Attach a map for the whole event."))

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
                # spec/155 — carry the per-day map slot through dialog
                # accept. ``src.map_image_path`` was updated in place by
                # the chip's mapChanged callback when the user attached
                # a file. Without this kwarg, _save_trip_day_edits sees
                # map_image_path=None and the upsert nulls every map
                # the user just attached (Nelson 2026-06-30 root cause).
                map_image_path=src.map_image_path,
            ))
        return out

    # ── Accept guard ──────────────────────────────────────────────────

    def _on_ok(self) -> None:
        self._was_applied = True
        self.accept()

    def was_applied(self) -> bool:
        return self._was_applied
