"""Verdict-row tests for SyncPairPickerDialog (spec/124).

The measured-pair branch (no ``configured_tz``) used to fire a "15-minute"
warning that contradicted spec/123 — for a real TZ correction the raw
delta IS the offset, at any magnitude. These tests pin the corrected
behaviour and regress the untouched ``configured_tz``-present branch.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

try:
    from PyQt6.QtWidgets import QApplication
except ImportError:                                          # pragma: no cover
    QApplication = None

from mira.ui.base.sync_pair_picker import SyncPairPickerDialog


@pytest.fixture
def qapp():
    if QApplication is None:
        pytest.skip("PyQt6 not installed")
    app = QApplication.instance() or QApplication([])
    yield app


def _set_panel(panel, ts: datetime) -> None:
    """Stub the two attributes the verdict path reads without dragging
    a real photo through ``open_picker`` / EXIF."""
    panel._timestamp = ts
    panel._path = None  # _final_offset doesn't read it; selected_pair would


# ── Measured-pair branch (no configured_tz) ──────────────────────────────


def test_five_hour_raw_delta_no_warning(qapp, tmp_path):
    """5-hour raw delta (Nepal-style real TZ offset): verdict shows the
    delta + the "applied as-is" note, NO 15-minute warning, Use enabled,
    final offset = raw."""
    dlg = SyncPairPickerDialog(
        camera_id="G9M2",
        reference_id="Apple iPhone 13",
        camera_default_dir=str(tmp_path),
        reference_default_dir=str(tmp_path),
        trip_tz=5.75,
        configured_tz=None,
    )
    try:
        cam_t = datetime(2026, 6, 23, 12, 0, 0)
        ref_t = cam_t + timedelta(hours=5)
        _set_panel(dlg._cam_panel, cam_t)
        _set_panel(dlg._ref_panel, ref_t)
        dlg._update_verdict()

        text = dlg._verdict.text()
        assert "15 minutes" not in text
        assert "closer pair" not in text
        assert "applying the measured offset as-is" in text
        assert "+5:00" in text
        assert dlg._use_btn.isEnabled()
        assert dlg._final_offset == timedelta(hours=5)
    finally:
        dlg.deleteLater()


def test_small_raw_delta_still_clean(qapp, tmp_path):
    """A sub-minute raw delta also shows no warning — the dialog never
    second-guesses the magnitude."""
    dlg = SyncPairPickerDialog(
        camera_id="G9M2",
        reference_id="Apple iPhone 13",
        camera_default_dir=str(tmp_path),
        reference_default_dir=str(tmp_path),
        trip_tz=0.0,
        configured_tz=None,
    )
    try:
        cam_t = datetime(2026, 6, 23, 12, 0, 0)
        ref_t = cam_t + timedelta(seconds=42)
        _set_panel(dlg._cam_panel, cam_t)
        _set_panel(dlg._ref_panel, ref_t)
        dlg._update_verdict()

        text = dlg._verdict.text()
        assert "15 minutes" not in text
        assert "closer pair" not in text
        assert dlg._use_btn.isEnabled()
        assert dlg._final_offset == timedelta(seconds=42)
    finally:
        dlg.deleteLater()


# ── Declared-TZ branch (untouched by spec/124) ──────────────────────────


def test_configured_tz_within_tolerance_accepts_declared_offset(qapp, tmp_path):
    """Declared-TZ branch is the *separate* check spec/124 left alone.
    Pair within the 30-min tolerance is accepted; the FINAL offset is
    the declaration-derived value, not the raw diff."""
    dlg = SyncPairPickerDialog(
        camera_id="G9M2",
        reference_id="Apple iPhone 13",
        camera_default_dir=str(tmp_path),
        reference_default_dir=str(tmp_path),
        trip_tz=2.0,
        configured_tz=-3.0,  # expected shift = 5h
    )
    try:
        cam_t = datetime(2026, 6, 23, 12, 0, 0)
        ref_t = cam_t + timedelta(hours=5, minutes=2)  # 2 min off, within 30
        _set_panel(dlg._cam_panel, cam_t)
        _set_panel(dlg._ref_panel, ref_t)
        dlg._update_verdict()

        text = dlg._verdict.text()
        assert "within tolerance" in text
        assert dlg._use_btn.isEnabled()
        assert dlg._final_offset == timedelta(hours=5)
    finally:
        dlg.deleteLater()


def test_configured_tz_over_tolerance_rejects(qapp, tmp_path):
    """Declared-TZ branch over the 30-min tolerance disables Use and
    leaves the final offset cleared. (Regress: spec/124's measured-pair
    cleanup must not weaken this guard.)"""
    dlg = SyncPairPickerDialog(
        camera_id="G9M2",
        reference_id="Apple iPhone 13",
        camera_default_dir=str(tmp_path),
        reference_default_dir=str(tmp_path),
        trip_tz=2.0,
        configured_tz=-3.0,  # expected shift = 5h
    )
    try:
        cam_t = datetime(2026, 6, 23, 12, 0, 0)
        ref_t = cam_t + timedelta(hours=6)  # 1h off, way past 30min
        _set_panel(dlg._cam_panel, cam_t)
        _set_panel(dlg._ref_panel, ref_t)
        dlg._update_verdict()

        text = dlg._verdict.text()
        assert "OVER tolerance" in text
        assert not dlg._use_btn.isEnabled()
        assert dlg._final_offset is None
    finally:
        dlg.deleteLater()
