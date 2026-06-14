"""spec/64 §4.4 — phone-without-GPS per-location-group prompt.

Pins both halves of the slice 5 contract:

* The pure-logic stretch detector (``MainWindow._collect_phone_gps_stretches``)
  finds consecutive runs of rows missing country OR TZ.
* The prompt dialog (``PhoneGpsStretchDialog``) returns the values the
  user picked, defaults to the home values when nothing is changed, and
  supports the Skip path that leaves rows blank.
"""
from __future__ import annotations

from datetime import date

import pytest

try:
    from PyQt6.QtWidgets import QApplication
except ImportError:                                          # pragma: no cover
    QApplication = None

from core.scan_source import ScanDayRow
from mira.ui.pages.phone_gps_stretch_dialog import PhoneGpsStretchDialog


@pytest.fixture
def qapp():
    if QApplication is None:
        pytest.skip("PyQt6 not installed")
    app = QApplication.instance() or QApplication([])
    yield app


def _row(d_month: int, d_day: int, *, country: str = "", tz: int = None) -> ScanDayRow:
    return ScanDayRow(
        date=date(2026, d_month, d_day),
        country_code=country,
        tz_minutes=tz,
    )


# ── _collect_phone_gps_stretches (pure logic) ──────────────────────


def _collector():
    """The static method lives on MainWindow; import lazily to avoid
    construct cost for the pure-logic tests."""
    from mira.ui.shell.main_window import MainWindow
    return MainWindow._collect_phone_gps_stretches


def test_no_blanks_means_no_stretches():
    rows = [
        _row(9, 1, country="PT", tz=60),
        _row(9, 2, country="ES", tz=-60),
    ]
    assert _collector()(rows) == []


def test_all_blanks_collapses_into_one_stretch():
    rows = [_row(9, 1), _row(9, 2), _row(9, 3)]
    stretches = _collector()(rows)
    assert len(stretches) == 1
    assert [r.date for r in stretches[0]] == [
        date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3),
    ]


def test_one_blank_in_the_middle_is_a_singleton_stretch():
    rows = [
        _row(9, 1, country="PT", tz=60),
        _row(9, 2),
        _row(9, 3, country="ES", tz=-60),
    ]
    stretches = _collector()(rows)
    assert len(stretches) == 1
    assert [r.date for r in stretches[0]] == [date(2026, 9, 2)]


def test_blanks_at_edges_become_separate_stretches():
    rows = [
        _row(9, 1),
        _row(9, 2),
        _row(9, 3, country="PT", tz=60),
        _row(9, 4),
        _row(9, 5),
    ]
    stretches = _collector()(rows)
    assert len(stretches) == 2
    assert [r.date for r in stretches[0]] == [
        date(2026, 9, 1), date(2026, 9, 2),
    ]
    assert [r.date for r in stretches[1]] == [
        date(2026, 9, 4), date(2026, 9, 5),
    ]


def test_missing_country_alone_counts_as_blank():
    """spec/64 §4.4: a day where country is blank but TZ is known
    (from phone OffsetTimeOriginal) still belongs to a stretch — the
    user gets to fill country in the prompt."""
    rows = [_row(9, 1, country="", tz=60)]
    stretches = _collector()(rows)
    assert len(stretches) == 1
    assert stretches[0][0].tz_minutes == 60          # the existing TZ survives


def test_missing_tz_alone_counts_as_blank():
    rows = [_row(9, 1, country="PT", tz=None)]
    stretches = _collector()(rows)
    assert len(stretches) == 1
    assert stretches[0][0].country_code == "PT"      # existing country survives


# ── PhoneGpsStretchDialog ──────────────────────────────────────────


def test_dialog_constructs_with_dates(qapp):
    dlg = PhoneGpsStretchDialog(
        dates=[date(2026, 9, 1), date(2026, 9, 2)],
        initial_country="BR", initial_tz_minutes=-180,
        parent=None)
    assert dlg.was_applied() is False
    # Defaults round-trip even without Apply.
    country, tz = dlg.result_values()
    assert country == "BR"
    assert tz == -180


def test_dialog_constructs_with_single_day(qapp):
    """Single-day variant — the heading text branches per spec/64 §4.4
    (singular vs plural messaging)."""
    dlg = PhoneGpsStretchDialog(
        dates=[date(2026, 9, 1)],
        initial_country=None, initial_tz_minutes=None,
        parent=None)
    country, tz = dlg.result_values()
    assert country is None
    # TzPicker has no None state; it defaults to 0 (UTC) when initial
    # is not given. The user can change it via the picker; downstream
    # treats this as a valid TZ.
    assert tz == 0


def test_dialog_apply_marks_applied(qapp):
    dlg = PhoneGpsStretchDialog(
        dates=[date(2026, 9, 1)], parent=None)
    dlg._on_apply()
    assert dlg.was_applied() is True


def test_dialog_result_values_reflects_user_pick(qapp):
    """After construction with home suggestions, the user can change
    the country. result_values picks up the change."""
    dlg = PhoneGpsStretchDialog(
        dates=[date(2026, 9, 1)],
        initial_country="BR", initial_tz_minutes=-180,
        parent=None)
    # Simulate user picking Portugal instead.
    idx = dlg._country_combo.findData("PT")
    dlg._country_combo.setCurrentIndex(idx)
    country, _tz = dlg.result_values()
    assert country == "PT"


def test_dialog_pickers_have_tooltips(qapp):
    """Every editable widget has a hint — memory
    ``ui_editable_fields_need_hints``."""
    dlg = PhoneGpsStretchDialog(
        dates=[date(2026, 9, 1)], parent=None)
    assert dlg._country_combo.toolTip()
    assert dlg._tz_picker.toolTip()
