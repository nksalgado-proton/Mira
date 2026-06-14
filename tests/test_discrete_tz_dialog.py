"""spec/45 Slice TZ-3 — DiscreteTzDialog + gateway camera_day_tz methods.

The dialog is exercised via direct widget access (no exec); the gateway
methods round-trip through the new camera_day_tz table.
"""
from __future__ import annotations

from datetime import date

import pytest

try:
    from PyQt6.QtWidgets import QApplication, QComboBox
except ImportError:                                      # pragma: no cover
    QApplication = None
    QComboBox = None

from mira.gateway import EventsIndex, Gateway
from mira.settings.repo import SettingsRepo
from mira.store import models as m
from mira.ui.pages.discrete_tz_dialog import (
    DiscreteTzDialog,
    rows_needing_answers,
)

NOW = "2026-06-06T00:00:00+00:00"


@pytest.fixture
def qapp():
    if QApplication is None:
        pytest.skip("PyQt6 not installed")
    app = QApplication.instance() or QApplication([])
    yield app


def _make_gateway_with_event(tmp_path):
    base = tmp_path / "lib"
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "settings.json"),
        index=EventsIndex(tmp_path / "events_index.json"),
    )
    gw.set_photos_base_path(str(base))
    doc = m.EventDocument(
        event=m.Event(uuid="evt-tz3", name="Slice TZ-3 test",
                      created_at=NOW, updated_at=NOW, start_date="2026-05-27"),
        trip_days=[
            m.TripDay(day_number=1, date="2026-05-27", description="Roma"),
            m.TripDay(day_number=2, date="2026-05-28", description="Lisbon"),
        ],
        cameras=[m.Camera(camera_id="G9M2"), m.Camera(camera_id="Sony A6700")],
    )
    gw.create_event(doc, base / "evt-tz3").close()
    return gw, "evt-tz3"


# ── Gateway: set_camera_day_tz / camera_day_tz / bulk ─────────────────────


def test_set_camera_day_tz_round_trips(qapp, tmp_path):
    gw, eid = _make_gateway_with_event(tmp_path)
    eg = gw.open_event(eid)
    try:
        eg.set_camera_day_tz(
            "G9M2", 1, tz_minutes=120, source="user_declared",
        )
        row = eg.camera_day_tz("G9M2", 1)
        assert row is not None
        assert row.declared_tz_minutes == 120
        assert row.source == "user_declared"
    finally:
        eg.close()


def test_set_camera_day_tz_rejects_unknown_offset(qapp, tmp_path):
    gw, eid = _make_gateway_with_event(tmp_path)
    eg = gw.open_event(eid)
    try:
        with pytest.raises(ValueError):
            eg.set_camera_day_tz(
                "G9M2", 1, tz_minutes=73, source="user_declared",
            )
    finally:
        eg.close()


def test_set_camera_day_tz_rejects_unknown_source(qapp, tmp_path):
    gw, eid = _make_gateway_with_event(tmp_path)
    eg = gw.open_event(eid)
    try:
        with pytest.raises(ValueError):
            eg.set_camera_day_tz(
                "G9M2", 1, tz_minutes=120, source="ouija",
            )
    finally:
        eg.close()


def test_bulk_set_camera_day_tz_from_phone(qapp, tmp_path):
    gw, eid = _make_gateway_with_event(tmp_path)
    eg = gw.open_event(eid)
    try:
        eg.bulk_set_camera_day_tz_from_phone(
            camera_ids=["G9M2", "Sony A6700"],
            day_offsets={1: 120, 2: 0},
        )
        # Both cameras × both days = 4 rows
        assert eg.camera_day_tz("G9M2", 1).declared_tz_minutes == 120
        assert eg.camera_day_tz("G9M2", 2).declared_tz_minutes == 0
        assert eg.camera_day_tz("Sony A6700", 1).declared_tz_minutes == 120
        assert eg.camera_day_tz("Sony A6700", 2).declared_tz_minutes == 0
        for row in eg.camera_day_tz_all():
            assert row.source == "phone_auto"
    finally:
        eg.close()


# ── rows_needing_answers ──────────────────────────────────────────────────


def test_rows_needing_answers_excludes_phone_cameras():
    rows = rows_needing_answers(
        phone_day_tz={1: 120, 2: -180},
        cameras_by_day={1: ["iPhone", "G9M2"], 2: ["iPhone", "G9M2"]},
        phone_camera_ids=["iPhone"],
        seed_tz={},
        days=[(1, "2026-04-01"), (2, "2026-04-02")],
    )
    cams = [r[2] for r in rows]
    assert "iPhone" not in cams
    assert cams.count("G9M2") == 2


def test_rows_needing_answers_skips_phone_absent_days():
    """Days with no phone TZ aren't part of this dialog — the legacy
    pair-picker handles them."""
    rows = rows_needing_answers(
        phone_day_tz={1: 120},                # phone only present on day 1
        cameras_by_day={1: ["G9M2"], 2: ["G9M2"]},
        phone_camera_ids=[],
        seed_tz={},
        days=[(1, "2026-04-01"), (2, "2026-04-02")],
    )
    assert [r[0] for r in rows] == [1]        # only day 1


def test_rows_needing_answers_seed_tz_propagates():
    rows = rows_needing_answers(
        phone_day_tz={1: 120},
        cameras_by_day={1: ["G9M2"]},
        phone_camera_ids=[],
        seed_tz={("G9M2", 1): -180},
        days=[(1, "2026-04-01")],
    )
    assert rows[0][3] == -180


# ── Dialog widget ─────────────────────────────────────────────────────────


def test_dialog_renders_one_row_per_input(qapp):
    rows = [
        (1, "2026-04-01", "G9M2", None),
        (1, "2026-04-01", "Sony A6700", None),
        (2, "2026-04-02", "G9M2", None),
    ]
    dlg = DiscreteTzDialog(
        phone_day_tz={1: 120, 2: 0},
        rows=rows,
    )
    try:
        assert dlg._table.rowCount() == 3
    finally:
        dlg.deleteLater()


def test_dialog_combo_pre_selects_phone_tz_when_no_seed(qapp):
    """Without a seed, the picker pre-selects the day's phone TZ so the user
    can leave it alone for matching-TZ cameras (the common case)."""
    rows = [(1, "2026-04-01", "G9M2", None)]
    dlg = DiscreteTzDialog(
        phone_day_tz={1: 120},
        rows=rows,
    )
    try:
        combo = dlg._table.cellWidget(0, 3)
        assert combo.currentData() == 120
    finally:
        dlg.deleteLater()


def test_dialog_combo_pre_selects_seed_tz_when_provided(qapp):
    rows = [(1, "2026-04-01", "G9M2", -180)]
    dlg = DiscreteTzDialog(
        phone_day_tz={1: 120},
        rows=rows,
    )
    try:
        combo = dlg._table.cellWidget(0, 3)
        assert combo.currentData() == -180
    finally:
        dlg.deleteLater()


def test_dialog_combo_seed_snaps_to_nearest_when_off_grid(qapp):
    """Seed of 73 minutes (impossible value) snaps to 60 (the nearest
    standard offset)."""
    rows = [(1, "2026-04-01", "G9M2", 73)]
    dlg = DiscreteTzDialog(
        phone_day_tz={1: 120},
        rows=rows,
    )
    try:
        combo = dlg._table.cellWidget(0, 3)
        assert combo.currentData() == 60
    finally:
        dlg.deleteLater()


def test_dialog_accept_collects_picked_answers(qapp):
    rows = [
        (1, "2026-04-01", "G9M2", None),
        (2, "2026-04-02", "G9M2", None),
    ]
    dlg = DiscreteTzDialog(
        phone_day_tz={1: 120, 2: 0},
        rows=rows,
    )
    try:
        # User changes day 1 to -180 (Brazil); leaves day 2 at the default
        # phone-TZ (0).
        combo1 = dlg._table.cellWidget(0, 3)
        combo1.setCurrentIndex(combo1.findData(-180))
        dlg._on_accept()
        answers = dlg.all_answers()
        assert answers[("G9M2", 1)] == -180
        assert answers[("G9M2", 2)] == 0
    finally:
        dlg.deleteLater()


def test_dialog_empty_state_when_no_rows(qapp):
    """No rows → table hidden, "nothing to confirm" hint shown."""
    dlg = DiscreteTzDialog(phone_day_tz={}, rows=[])
    try:
        assert dlg._table.rowCount() == 0
        assert not dlg._empty.isHidden()
        assert dlg._table.isHidden()
    finally:
        dlg.deleteLater()
