"""spec/64 slice 3 — ``EventDaysTableDialog``: the per-day schedule
surface, with every legacy PlanDialog per-day feature preserved.

Pins the §4 fixes:

* **Focus stays put (§4.2)** — wheel events on unfocused cell widgets
  are swallowed (no value change, no focus shift).
* **Country / TZ propagate-down with confirm (§4.3)** — yes path
  cascades the value, no path leaves only the seed row changed, the
  cascade walls at the first row the user has touched previously.
* **Free-text Location / Description (§4.5)** — never required;
  editing marks the cell as user-touched.

Also pins the **restored** legacy features:

* Include checkbox round-trip + the cell's date label.
* Browse-day handler callback.
* Override marker column hides itself when no row carries a marker.
* ``frozen_after_ingest`` disables the TZ picker (or stays live with
  ``tz_editable_when_frozen=True`` — spec/57 §4.2).
* CSV save / load (premium-gated; ``can_save_load_csv=True``).
* Delete-day (opt-in; ``can_delete_days=True``).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

try:
    from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
    from PyQt6.QtGui import QWheelEvent
    from PyQt6.QtWidgets import QApplication, QComboBox, QLineEdit
except ImportError:                                          # pragma: no cover
    QApplication = None

from core.scan_source import OverrideMarker, ScanDayRow
from mira.ui.base.tz_picker import TzPicker
from mira.ui.pages.event_days_table_dialog import (
    COL_BROWSE,
    COL_COUNTRY,
    COL_DESC,
    COL_INCLUDE,
    COL_LOC,
    COL_OVERRIDE,
    COL_TZ,
    EventDaysTableDialog,
    TOUCH_COUNTRY,
    TOUCH_DESC,
    TOUCH_LOC,
    TOUCH_TZ,
)


@pytest.fixture
def qapp():
    if QApplication is None:
        pytest.skip("PyQt6 not installed")
    app = QApplication.instance() or QApplication([])
    yield app


def _rows(*entries):
    """Helper — each entry: ``(month, day, code, tz_min, loc, desc)``."""
    return [
        ScanDayRow(
            date=date(2026, m, d),
            country_code=cc,
            tz_minutes=tz,
            location=loc,
            description=desc,
        )
        for (m, d, cc, tz, loc, desc) in entries
    ]


def _country_combo(dlg, row_idx) -> QComboBox:
    return dlg._table.cellWidget(row_idx, COL_COUNTRY)


def _tz_picker(dlg, row_idx) -> TzPicker:
    return dlg._table.cellWidget(row_idx, COL_TZ)


def _loc_edit(dlg, row_idx) -> QLineEdit:
    return dlg._table.cellWidget(row_idx, COL_LOC)


def _desc_edit(dlg, row_idx) -> QLineEdit:
    return dlg._table.cellWidget(row_idx, COL_DESC)


def _include_checkbox(dlg, row_idx):
    cell = dlg._table.cellWidget(row_idx, COL_INCLUDE)
    return cell.property("_checkbox") if cell is not None else None


def _browse_button(dlg, row_idx):
    cell = dlg._table.cellWidget(row_idx, COL_BROWSE)
    return cell.findChild(type(cell.children()[1])) if cell is not None else None


def _set_country(combo: QComboBox, code: str) -> None:
    idx = combo.findData(code.upper())
    assert idx >= 0, f"country {code!r} not in combo"
    combo.setCurrentIndex(idx)


# ── Construct + round-trip ──────────────────────────────────────────


def test_constructs_with_rows(qapp):
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "Lisbon", "Arrival"),
              (9, 2, "", None, "", "")),
        parent=None)
    assert dlg._table.rowCount() == 2
    assert dlg.was_applied() is False


def test_rows_round_trip_unchanged_input(qapp):
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "Lisbon", "Arrival"),
              (9, 2, "ES", -60, "Madrid", "")),
        parent=None)
    out = dlg.rows()
    assert len(out) == 2
    assert out[0].country_code == "PT"
    assert out[0].tz_minutes == 60
    assert out[0].location == "Lisbon"
    assert out[0].description == "Arrival"
    assert out[1].country_code == "ES"
    assert out[1].tz_minutes == -60
    assert out[1].location == "Madrid"


# ── Include checkbox (restored) ────────────────────────────────────


def test_include_cell_carries_the_date_label(qapp):
    """Legacy UX preserved: the Include checkbox shows the ISO date so
    the row identity is readable right next to the affordance."""
    dlg = EventDaysTableDialog(_rows((9, 1, "PT", 60, "", "")), parent=None)
    box = _include_checkbox(dlg, 0)
    assert box is not None
    assert box.text() == "2026-09-01"


def test_include_checkbox_round_trips_through_rows(qapp):
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "", ""),
              (9, 2, "PT", 60, "", "")),
        parent=None)
    # Default = checked (ScanDayRow defaults to checked=True).
    assert dlg.rows()[0].checked is True
    _include_checkbox(dlg, 1).setChecked(False)
    out = dlg.rows()
    assert out[0].checked is True
    assert out[1].checked is False


# ── Browse-day handler (restored) ──────────────────────────────────


def test_browse_button_disabled_without_handler(qapp):
    dlg = EventDaysTableDialog(_rows((9, 1, "PT", 60, "", "")), parent=None)
    cell = dlg._table.cellWidget(0, COL_BROWSE)
    # The single button child of the cell — find without depending on a
    # specific layout API.
    from PyQt6.QtWidgets import QPushButton
    btn = cell.findChild(QPushButton)
    assert btn is not None
    assert btn.isEnabled() is False


def test_browse_button_fires_handler_with_the_day(qapp):
    seen = []
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "", ""),
              (9, 2, "ES", -60, "", "")),
        browse_handler=lambda d: seen.append(d),
        parent=None)
    from PyQt6.QtWidgets import QPushButton
    btn = dlg._table.cellWidget(1, COL_BROWSE).findChild(QPushButton)
    btn.click()
    assert seen == [date(2026, 9, 2)]


# ── Override marker (restored) ─────────────────────────────────────


def test_override_column_hidden_when_no_row_has_marker(qapp):
    dlg = EventDaysTableDialog(_rows((9, 1, "PT", 60, "", "")), parent=None)
    assert dlg._table.isColumnHidden(COL_OVERRIDE) is True


def test_override_column_visible_when_a_row_has_marker(qapp):
    marker = OverrideMarker(
        existing_country="PT", existing_tz_minutes=60, existing_location="",
        new_country="ES", new_tz_minutes=-60, new_location="")
    rows = [ScanDayRow(date=date(2026, 9, 1), country_code="PT",
                       tz_minutes=60, override_marker=marker)]
    dlg = EventDaysTableDialog(rows, parent=None)
    assert dlg._table.isColumnHidden(COL_OVERRIDE) is False


def test_override_handler_fires_on_click(qapp):
    seen = []
    marker = OverrideMarker(
        existing_country="PT", existing_tz_minutes=60, existing_location="",
        new_country="ES", new_tz_minutes=-60, new_location="")
    rows = [ScanDayRow(date=date(2026, 9, 1), country_code="PT",
                       tz_minutes=60, override_marker=marker)]
    dlg = EventDaysTableDialog(
        rows, override_handler=lambda d: seen.append(d), parent=None)
    from PyQt6.QtWidgets import QPushButton
    btn = dlg._table.cellWidget(0, COL_OVERRIDE).findChild(QPushButton)
    btn.click()
    assert seen == [date(2026, 9, 1)]


# ── frozen_after_ingest (restored) ─────────────────────────────────


def test_frozen_after_ingest_disables_tz_picker(qapp):
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "", "")),
        frozen_after_ingest=True, parent=None)
    assert _tz_picker(dlg, 0).isEnabled() is False


def test_frozen_after_ingest_tz_editable_when_frozen_leaves_picker_live(qapp):
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "", "")),
        frozen_after_ingest=True, tz_editable_when_frozen=True, parent=None)
    assert _tz_picker(dlg, 0).isEnabled() is True


# ── CSV save / load (restored) ─────────────────────────────────────


def test_csv_buttons_hidden_without_premium_gate(qapp):
    dlg = EventDaysTableDialog(_rows((9, 1, "PT", 60, "", "")), parent=None)
    assert dlg._save_csv_button.isHidden() is True
    assert dlg._load_csv_button.isHidden() is True


def test_csv_buttons_visible_with_premium_gate(qapp):
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "", "")),
        can_save_load_csv=True, parent=None)
    assert dlg._save_csv_button.isHidden() is False
    assert dlg._load_csv_button.isHidden() is False


def test_csv_save_then_load_round_trips_country_tz_loc_desc(qapp, tmp_path):
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "Lisbon", "Arrival"),
              (9, 2, "ES", -60, "Madrid", "Day 2")),
        can_save_load_csv=True, parent=None)
    csv_path = tmp_path / "plan.csv"
    dlg.set_csv_paths(save=str(csv_path))
    dlg._on_save_csv()
    assert csv_path.exists()

    # Wipe + reload into a fresh dialog backed by the same dates.
    dlg2 = EventDaysTableDialog(
        _rows((9, 1, "", None, "", ""), (9, 2, "", None, "", "")),
        can_save_load_csv=True, parent=None)
    dlg2.set_csv_paths(load=str(csv_path))
    dlg2._on_load_csv()
    out = dlg2.rows()
    assert out[0].country_code == "PT"
    assert out[0].tz_minutes == 60
    assert out[0].location == "Lisbon"
    assert out[0].description == "Arrival"
    assert out[1].country_code == "ES"
    assert out[1].tz_minutes == -60


def test_csv_load_skips_tz_when_frozen_after_ingest(qapp, tmp_path):
    """spec/57 §4.2: CSV-load ignores the file's TZ values on
    frozen-after-ingest dialogs so a re-imported plan can't shift
    photos across a TZ boundary."""
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "Lisbon", ""),
              (9, 2, "ES", -60, "Madrid", "")),
        can_save_load_csv=True, parent=None)
    csv_path = tmp_path / "plan.csv"
    dlg.set_csv_paths(save=str(csv_path))
    dlg._on_save_csv()

    # Open a frozen dialog on the same dates but with DIFFERENT initial
    # TZ values; load the CSV and verify the TZ stays at the initial
    # values (the loader skipped them).
    dlg2 = EventDaysTableDialog(
        _rows((9, 1, "PT", 0, "", ""), (9, 2, "ES", 0, "", "")),
        can_save_load_csv=True, frozen_after_ingest=True, parent=None)
    dlg2.set_csv_paths(load=str(csv_path))
    dlg2._on_load_csv()
    out = dlg2.rows()
    # Country / location loaded (those are not gated by frozen).
    assert out[0].country_code == "PT"
    assert out[0].location == "Lisbon"
    # TZ deliberately unchanged.
    assert out[0].tz_minutes == 0
    assert out[1].tz_minutes == 0


# ── Delete-day (restored) ──────────────────────────────────────────


def test_delete_day_button_hidden_by_default(qapp):
    dlg = EventDaysTableDialog(_rows((9, 1, "PT", 60, "", "")), parent=None)
    assert dlg._delete_day_button.isHidden() is True


def test_delete_day_button_visible_when_opted_in(qapp):
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "", "")),
        can_delete_days=True, parent=None)
    assert dlg._delete_day_button.isHidden() is False
    # Disabled until a row is selected.
    assert dlg._delete_day_button.isEnabled() is False


def test_delete_day_yes_removes_selected_rows(qapp):
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "", ""),
              (9, 2, "ES", -60, "", ""),
              (9, 3, "FR", 0, "", "")),
        can_delete_days=True, parent=None)
    dlg.set_delete_confirm(True)
    dlg._table.selectRow(1)
    dlg._on_delete_day()
    out = dlg.rows()
    assert len(out) == 2
    assert out[0].date == date(2026, 9, 1)
    assert out[1].date == date(2026, 9, 3)


def test_delete_day_no_keeps_rows(qapp):
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "", ""),
              (9, 2, "ES", -60, "", "")),
        can_delete_days=True, parent=None)
    dlg.set_delete_confirm(False)
    dlg._table.selectRow(0)
    dlg._on_delete_day()
    assert len(dlg.rows()) == 2


# ── Country / TZ propagate-down (§4.3) ─────────────────────────────


def test_country_change_no_rows_below_does_not_prompt(qapp):
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "", "")), parent=None)
    _set_country(_country_combo(dlg, 0), "ES")
    assert dlg.rows()[0].country_code == "ES"


def test_country_yes_propagates_to_all_rows_below(qapp):
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "", ""),
              (9, 2, "PT", 60, "", ""),
              (9, 3, "PT", 60, "", "")),
        parent=None)
    dlg.set_propagate_confirm(True)
    _set_country(_country_combo(dlg, 0), "ES")
    out = dlg.rows()
    assert out[0].country_code == "ES"
    assert out[1].country_code == "ES"
    assert out[2].country_code == "ES"


def test_country_no_does_not_propagate(qapp):
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "", ""),
              (9, 2, "PT", 60, "", ""),
              (9, 3, "PT", 60, "", "")),
        parent=None)
    dlg.set_propagate_confirm(False)
    _set_country(_country_combo(dlg, 0), "ES")
    out = dlg.rows()
    assert out[0].country_code == "ES"
    assert out[1].country_code == "PT"
    assert out[2].country_code == "PT"


def test_country_cascade_walls_at_user_touched_row(qapp):
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "", ""),
              (9, 2, "PT", 60, "", ""),
              (9, 3, "FR", 60, "", ""),
              (9, 4, "PT", 60, "", "")),
        parent=None)
    dlg.set_propagate_confirm(False)
    _set_country(_country_combo(dlg, 2), "BR")
    assert dlg.rows()[2].country_code == "BR"
    assert dlg.rows()[3].country_code == "PT"
    dlg.set_propagate_confirm(True)
    _set_country(_country_combo(dlg, 0), "ES")
    out = dlg.rows()
    assert out[0].country_code == "ES"
    assert out[1].country_code == "ES"
    assert out[2].country_code == "BR"
    assert out[3].country_code == "PT"


def test_tz_yes_propagates_to_all_rows_below(qapp):
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "", ""),
              (9, 2, "PT", 60, "", ""),
              (9, 3, "PT", 60, "", "")),
        parent=None)
    dlg.set_propagate_confirm(True)
    _tz_picker(dlg, 0).setValue(2.0)
    out = dlg.rows()
    assert out[0].tz_minutes == 120
    assert out[1].tz_minutes == 120
    assert out[2].tz_minutes == 120


def test_tz_cascade_walls_at_user_touched_row(qapp):
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "", ""),
              (9, 2, "PT", 60, "", ""),
              (9, 3, "PT", 60, "", ""),
              (9, 4, "PT", 60, "", "")),
        parent=None)
    dlg.set_propagate_confirm(False)
    _tz_picker(dlg, 2).setValue(-3.0)
    assert dlg.rows()[2].tz_minutes == -180
    assert dlg.rows()[3].tz_minutes == 60
    dlg.set_propagate_confirm(True)
    _tz_picker(dlg, 0).setValue(2.0)
    out = dlg.rows()
    assert out[0].tz_minutes == 120
    assert out[1].tz_minutes == 120
    assert out[2].tz_minutes == -180
    assert out[3].tz_minutes == 60


def test_country_and_tz_walls_are_independent(qapp):
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "", ""),
              (9, 2, "PT", 60, "", ""),
              (9, 3, "PT", -180, "", "")),
        parent=None)
    dlg.set_propagate_confirm(False)
    _tz_picker(dlg, 2).setValue(-3.0)
    dlg.set_propagate_confirm(True)
    _set_country(_country_combo(dlg, 0), "ES")
    out = dlg.rows()
    assert out[0].country_code == "ES"
    assert out[1].country_code == "ES"
    assert out[2].country_code == "ES"
    assert out[2].tz_minutes == -180


# ── Free-text Location / Description (§4.5) ────────────────────────


def test_location_edit_marks_touched_only_does_not_cascade(qapp):
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "", ""),
              (9, 2, "PT", 60, "", ""),
              (9, 3, "PT", 60, "", "")),
        parent=None)
    _loc_edit(dlg, 1).textEdited.emit("Madrid")
    assert (1, TOUCH_LOC) in dlg._touched
    dlg.set_propagate_confirm(False)
    _loc_edit(dlg, 1).textEdited.emit("Madrid, ES")
    assert (1, TOUCH_LOC) in dlg._touched


def test_description_edit_marks_touched(qapp):
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "", "")), parent=None)
    _desc_edit(dlg, 0).textEdited.emit("Lisbon arrival")
    assert (0, TOUCH_DESC) in dlg._touched


# ── Focus stays put (§4.2) — wheel events on unfocused widgets ─────


def test_wheel_on_unfocused_cell_is_swallowed(qapp):
    """spec/64 §4.2 part 1: wheeling over a cell the user has NOT
    clicked must NOT change its value — the wheel scrolls the table
    instead (the forward target is asserted separately)."""
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "", ""),
              (9, 2, "", None, "", "")), parent=None)
    combo = _country_combo(dlg, 0)
    initial_index = combo.currentIndex()
    event = QWheelEvent(
        QPointF(10, 10), QPointF(combo.mapToGlobal(QPoint(10, 10))),
        QPoint(0, -120), QPoint(0, -120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )
    handled = dlg._wheel_filter.eventFilter(combo, event)
    assert handled is True
    assert combo.currentIndex() == initial_index


def test_wheel_on_focused_cell_is_let_through(qapp, monkeypatch):
    """spec/64 §4.2 part 2 (Nelson 2026-06-13): "After left clicking
    on a field with a dropdown, the mouse wheel should work over that
    field." Once focus is on the cell, the wheel passes through so the
    combo / picker shifts as Qt normally does."""
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "", "")), parent=None)
    combo = _country_combo(dlg, 0)
    monkeypatch.setattr(combo, "hasFocus", lambda: True)
    event = QWheelEvent(
        QPointF(10, 10), QPointF(combo.mapToGlobal(QPoint(10, 10))),
        QPoint(0, -120), QPoint(0, -120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )
    handled = dlg._wheel_filter.eventFilter(combo, event)
    assert handled is False                              # filter passes through


def test_wheel_on_unfocused_cell_forwards_to_table_viewport(qapp, monkeypatch):
    """The forward path is the visible behaviour: when the filter
    swallows the wheel on an unfocused cell, it calls
    QApplication.sendEvent with the table viewport as the target so
    the table scrolls."""
    from PyQt6.QtWidgets import QApplication as _QApp
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "", ""),
              (9, 2, "ES", -60, "", "")), parent=None)
    combo = _country_combo(dlg, 0)
    viewport = dlg._table.viewport()
    forwarded = []
    real_send = _QApp.sendEvent

    def _spy(target, event):
        if event.type() == QEvent.Type.Wheel:
            forwarded.append(target)
        return real_send(target, event)

    monkeypatch.setattr(
        "mira.ui.pages.event_days_table_dialog.QApplication.sendEvent",
        _spy)
    event = QWheelEvent(
        QPointF(10, 10), QPointF(combo.mapToGlobal(QPoint(10, 10))),
        QPoint(0, -120), QPoint(0, -120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )
    dlg._wheel_filter.eventFilter(combo, event)
    assert viewport in forwarded                          # forwarded to viewport


# ── Hint coverage ──────────────────────────────────────────────────


def test_every_column_header_has_a_tooltip(qapp):
    dlg = EventDaysTableDialog(
        _rows((9, 1, "PT", 60, "", "")), parent=None)
    for col in range(dlg._table.columnCount()):
        item = dlg._table.horizontalHeaderItem(col)
        assert item is not None
        assert item.toolTip()
