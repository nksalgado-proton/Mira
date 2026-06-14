"""Per-row "Browse day" in the plan editor (Nelson 2026-05-31).

The legacy right-click "Browse photos for this day…" is replaced by a per-row Browse button
that opens the day's photos+videos in the read-only Fast Culler. These pin:
- the Browse column is present only when a day-photos provider is wired (existing event),
  hidden for New Event (no provider);
- the Fast Culler's browse mode hides every K/D control;
- the MainWindow provider returns the event's items for a given date as SourceItems.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from core.fresh_source import SourceItem
from core.models import TripDay as LegacyTripDay
from mira.gateway import EventsIndex, Gateway
from mira.ingest import CameraPlan, DayPlan, IngestPlan, run_ingest
from mira.settings.repo import SettingsRepo
from mira.ui.base.plan_editor_dialog import (
    COL_BROWSE,
    COL_COUNTRY,
    PlanEditorDialog,
)
from mira.ui.pages.quick_sweep_page import QuickSweepPage


def _legacy_days():
    return [
        LegacyTripDay(day_number=1, date=date(2026, 3, 10), description="KTM", tz_offset=5.75),
        LegacyTripDay(day_number=2, date=date(2026, 3, 11), description="Pokhara", tz_offset=5.75),
    ]


def test_browse_column_hidden_without_provider(qapp):
    dlg = PlanEditorDialog(trip_days=_legacy_days(), event=None)
    assert dlg._table.isColumnHidden(COL_BROWSE)
    # No Browse button widgets in the rows.
    assert dlg._table.cellWidget(0, COL_BROWSE) is None


def _browse_button(dlg, row):
    from PyQt6.QtWidgets import QPushButton
    cell = dlg._table.cellWidget(row, COL_BROWSE)
    return cell.findChild(QPushButton) if cell is not None else None


def test_browse_column_shown_with_provider(qapp):
    # Day 1 has photos → "Browse…" enabled; day 2 has none → disabled "Empty".
    dlg = PlanEditorDialog(
        trip_days=_legacy_days(), event=None,
        day_photos_provider=lambda _d: [],
        day_photo_counts={"2026-03-10": 3},
    )
    assert not dlg._table.isColumnHidden(COL_BROWSE)
    b0 = _browse_button(dlg, 0)
    assert b0 is not None and b0.text() == "Browse…" and b0.isEnabled()
    b1 = _browse_button(dlg, 1)
    assert b1 is not None and b1.text() == "Empty" and not b1.isEnabled()
    # Compact height comes from the QSS role (#PlanBrowseCell, both themes), so it fits
    # the row — a styled QPushButton ignores setMaximumHeight.
    assert b0.objectName() == "PlanBrowseCell"
    assert b1.objectName() == "PlanBrowseCell"


def test_browse_no_photos_shows_message(qapp, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "mira.ui.base.plan_editor_dialog.QMessageBox.information",
        lambda *a, **k: seen.setdefault("info", a),
    )
    opened = {"n": 0}
    dlg = PlanEditorDialog(
        trip_days=_legacy_days(), event=None,
        day_photos_provider=lambda _d: [],  # no photos for any day
    )
    monkeypatch.setattr(dlg, "_open_day_browser", lambda items: opened.__setitem__("n", 1))
    dlg._browse_day_for_row(0)
    assert "info" in seen and opened["n"] == 0  # message shown, browser not opened


def test_browse_with_photos_opens_browser(qapp, monkeypatch):
    captured = {}
    dlg = PlanEditorDialog(
        trip_days=_legacy_days(), event=None,
        day_photos_provider=lambda d: [SourceItem(Path("x.jpg"), datetime(2026, 3, 10, 8, 0, 0), "G9")],
    )
    monkeypatch.setattr(dlg, "_open_day_browser", lambda items: captured.setdefault("items", items))
    dlg._browse_day_for_row(0)
    assert len(captured.get("items", [])) == 1


# ── spec/47 — Past-Photos plan-edit Browse + Country column ──────────────────


def test_browse_column_shown_with_day_photo_paths(qapp):
    """Past-Photos has no event yet — instead it provides source paths per day.
    The Browse column must un-hide when ``day_photo_paths`` is set."""
    paths = {1: [Path("/src/g9/p1.jpg"), Path("/src/g9/p2.jpg")]}
    dlg = PlanEditorDialog(
        trip_days=_legacy_days(), event=None,
        day_photo_paths=paths,
    )
    assert not dlg._table.isColumnHidden(COL_BROWSE)
    # Day 1 has paths → Browse… enabled; day 2 has none → "Empty".
    b0 = _browse_button(dlg, 0)
    assert b0 is not None and b0.text() == "Browse…" and b0.isEnabled()
    b1 = _browse_button(dlg, 1)
    assert b1 is not None and b1.text() == "Empty" and not b1.isEnabled()


def test_browse_with_paths_opens_day_browse_dialog(qapp, monkeypatch):
    """Clicking Browse with paths should open the lighter ``DayBrowseDialog``
    over the source-folder paths — no QuickSweepPage, no gateway."""
    captured = {}

    class _StubDialog:
        def __init__(self, paths, parent=None):
            captured["paths"] = list(paths)
            captured["parent"] = parent

        def exec(self):
            captured["execed"] = True
            return 1

    monkeypatch.setattr(
        "mira.ui.pages.day_browse_dialog.DayBrowseDialog", _StubDialog)

    paths = {1: [Path("/src/g9/p1.jpg")]}
    dlg = PlanEditorDialog(
        trip_days=_legacy_days(), event=None,
        day_photo_paths=paths,
    )
    dlg._browse_day_for_row(0)
    assert captured.get("execed") is True
    assert captured["paths"] == [Path("/src/g9/p1.jpg")]


def test_country_column_is_in_the_table(qapp):
    """spec/47: a 6th column 'Country' between TZ and Location."""
    dlg = PlanEditorDialog(trip_days=_legacy_days(), event=None)
    assert dlg._table.columnCount() == 6
    header = dlg._table.horizontalHeaderItem(COL_COUNTRY)
    assert header is not None and header.text() == "Country"
    # Each row has a combo (currentData is the alpha-2 code).
    combo = dlg._table.cellWidget(0, COL_COUNTRY)
    assert combo is not None
    # Default — no country set on the legacy days → empty selection (data="").
    assert combo.currentData() in ("", None)


def test_country_code_round_trips_through_get_trip_days(qapp):
    """A pre-filled country_code (auto-fill at ingest) renders in the combo
    and survives a round-trip through get_trip_days."""
    days = list(_legacy_days())
    days[0].country_code = "BR"
    days[1].country_code = "CR"
    dlg = PlanEditorDialog(trip_days=days, event=None)
    out = dlg.get_trip_days()
    by_n = {d.day_number: d for d in out}
    # day_number is renumbered 1..N by date order; both days had Country, so
    # both should round-trip.
    assert {d.country_code for d in out} == {"BR", "CR"}
    # Combo's userData reflects the alpha-2 code (uppercase).
    combo0 = dlg._table.cellWidget(0, COL_COUNTRY)
    assert combo0.currentData() == "BR"


def test_country_code_normalises_typed_lowercase_name(qapp):
    """Even if a user types a country name into the editable combo instead
    of picking from the dropdown, get_trip_days resolves it to alpha-2."""
    dlg = PlanEditorDialog(trip_days=_legacy_days(), event=None)
    combo = dlg._table.cellWidget(0, COL_COUNTRY)
    combo.setEditText("brazil")  # case-insensitive name lookup
    days = dlg.get_trip_days()
    by_n = {d.day_number: d for d in days}
    assert by_n[1].country_code == "BR"


def test_fast_culler_browse_mode_hides_kd_controls(qapp):
    """Browse mode hides every K/D control. In the redesigned vocabulary
    that means the action cluster (Pick / Skip / Compare buttons) +
    the Save button. Bulk K/D buttons dropped 2026-06-05 — Day Grid
    replaces them; no bulk equivalent in the new model (deliberately)."""
    page = QuickSweepPage(browse_mode=True)
    assert not page._pick_btn.isVisible()
    assert not page._skip_btn.isVisible()
    assert not page._compare_btn.isVisible()
    assert not page._export_btn.isVisible()


# ── MainWindow provider ───────────────────────────────────────────────────────

NOW = "2026-06-01T00:00:00+00:00"


def _now():
    return NOW


def test_provider_returns_day_items(qapp, tmp_path):
    base = tmp_path / "photos"
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "s.json"),
        index=EventsIndex(tmp_path / "i.json"), now=_now,
    )
    gw.set_photos_base_path(str(base))
    src = tmp_path / "card"
    src.mkdir()
    (src / "g9_d1.JPG").write_bytes(b"a")
    (src / "g9_d2.JPG").write_bytes(b"b")
    run_ingest(
        IngestPlan(
            event_id="e1", event_name="Nepal", event_root=base / "Nepal", source_root=src,
            days=[DayPlan(1, date(2026, 3, 10), "KTM", 5.75),
                  DayPlan(2, date(2026, 3, 11), "Pokhara", 5.75)],
            cameras=[CameraPlan("G9", configured_tz_hours=5.75)],
        ),
        gw,
        source_items=[
            SourceItem(src / "g9_d1.JPG", datetime(2026, 3, 10, 9, 0, 0), "G9"),
            SourceItem(src / "g9_d2.JPG", datetime(2026, 3, 11, 9, 0, 0), "G9"),
        ],
        now=_now,
    )
    from mira.ui.shell.main_window import MainWindow
    w = MainWindow(gateway=gw)
    provider = w._make_day_photos_provider("e1")

    day1 = provider(date(2026, 3, 10))
    assert len(day1) == 1 and day1[0].path.name == "g9_d1.JPG"
    assert day1[0].path.is_absolute()
    day2 = provider(date(2026, 3, 11))
    assert len(day2) == 1 and day2[0].path.name == "g9_d2.JPG"
    # A date with no day → empty.
    assert provider(date(2026, 3, 20)) == []
