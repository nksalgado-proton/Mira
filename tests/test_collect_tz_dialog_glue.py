"""Targeted tests for the Collect-flow additions to PastPhotosCamerasDialog
and SyncPairPickerDialog (Nelson 2026-06-09).

Covers the three Collect-specific seams without touching the legacy
QFileDialog-driven paths the existing tests already exercise:

* ``phone_reference_id`` excludes the phone from displayed rows but
  preserves it as the sync-pair reference.
* Auto-detect with no phone substring AND no picker_factory hides the
  "I don't know" combo item so Path A is the only mode the user sees.
* SyncPairPickerDialog's ``cam_picker_callback`` / ``ref_picker_callback``
  wire down to ``_PhotoPanel`` so a custom picker replaces the default
  QFileDialog.
"""
from __future__ import annotations

import pytest

try:
    from PyQt6.QtWidgets import QApplication
except ImportError:                                          # pragma: no cover
    QApplication = None

from mira.ui.base.sync_pair_picker import SyncPairPickerDialog
from mira.ui.pages.past_photos_cameras import PastPhotosCamerasDialog


@pytest.fixture
def qapp():
    if QApplication is None:
        pytest.skip("PyQt6 not installed")
    app = QApplication.instance() or QApplication([])
    yield app


# ── PastPhotosCamerasDialog: phone_reference_id ────────────────────────────


def test_phone_reference_id_sets_reference_without_adding_row(qapp, tmp_path):
    """``phone_reference_id`` declares the reference outside the row list.
    The phone is NOT in camera_ids (caller pre-filtered) so no row for
    it; ``reference_id`` still resolves to that phone for pair-picking."""
    dlg = PastPhotosCamerasDialog(
        camera_ids=["G9M2", "Sony A7R"],                     # phones excluded
        root_dir=str(tmp_path),
        trip_tz=2.0,
        phone_reference_id="Apple iPhone 13",
    )
    try:
        assert dlg.reference_id == "Apple iPhone 13"
        # The two non-phone cameras are the only rows.
        assert set(dlg._rows.keys()) == {"G9M2", "Sony A7R"}
        # Reference is NOT a row — phones don't need calibration.
        assert "Apple iPhone 13" not in dlg._rows
    finally:
        dlg.deleteLater()


def test_phone_reference_id_keeps_unknown_mode_available(qapp, tmp_path):
    """With a phone reference declared, pair-pick (Path B) is available
    even if no camera_id in the row list matches the phone substring."""
    dlg = PastPhotosCamerasDialog(
        camera_ids=["G9M2"],
        root_dir=str(tmp_path),
        trip_tz=2.0,
        phone_reference_id="Apple iPhone 13",
    )
    try:
        row = dlg._rows["G9M2"]
        # "I know" and "I don't know" both present.
        assert row.mode_combo.count() == 2
        assert row.mode_combo.itemData(0) == "know"
        assert row.mode_combo.itemData(1) == "unknown"
    finally:
        dlg.deleteLater()


def test_no_phone_anywhere_hides_unknown_mode(qapp, tmp_path):
    """No phone_reference_id, no substring match, no picker_factory
    → only "I know" mode is selectable (Path B impossible without a
    reference)."""
    dlg = PastPhotosCamerasDialog(
        camera_ids=["G9M2", "Sony A7R"],
        root_dir=str(tmp_path),
        trip_tz=2.0,
    )
    try:
        for cam in ("G9M2", "Sony A7R"):
            row = dlg._rows[cam]
            assert row.mode_combo.count() == 1
            assert row.mode_combo.itemData(0) == "know"
    finally:
        dlg.deleteLater()


def test_phone_substring_match_keeps_unknown_mode(qapp, tmp_path):
    """Legacy behavior: a camera_id that matches the phone substring
    list (e.g. contains "iphone") triggers auto-detect and pair-pick
    stays available."""
    dlg = PastPhotosCamerasDialog(
        camera_ids=["Apple iPhone 13", "G9M2"],              # substring match
        root_dir=str(tmp_path),
        trip_tz=2.0,
    )
    try:
        assert dlg.reference_id == "Apple iPhone 13"
        row = dlg._rows["G9M2"]
        assert row.mode_combo.count() == 2
    finally:
        dlg.deleteLater()


# ── SyncPairPickerDialog: picker_callback overrides ───────────────────────


def test_picker_callbacks_pass_through_to_panels(qapp, tmp_path):
    """Constructor accepts cam_picker_callback + ref_picker_callback;
    each panel records its callback so it can use it instead of
    QFileDialog when the user clicks "Pick photo…"."""
    cam_cb_called = []
    ref_cb_called = []

    def _cam_cb(_parent):
        cam_cb_called.append(True)
        return None                                          # cancel
    def _ref_cb(_parent):
        ref_cb_called.append(True)
        return None

    dlg = SyncPairPickerDialog(
        camera_id="G9M2",
        reference_id="Apple iPhone 13",
        camera_default_dir=str(tmp_path),
        reference_default_dir=str(tmp_path),
        trip_tz=2.0,
        configured_tz=None,
        cam_picker_callback=_cam_cb,
        ref_picker_callback=_ref_cb,
    )
    try:
        # Panels are wired with the callbacks.
        assert dlg._cam_panel._picker_callback is _cam_cb
        assert dlg._ref_panel._picker_callback is _ref_cb
        # Trigger each panel's picker; the callbacks should fire and
        # the QFileDialog branch should NOT (it would block the test).
        dlg._cam_panel.open_picker(dlg)
        dlg._ref_panel.open_picker(dlg)
        assert cam_cb_called == [True]
        assert ref_cb_called == [True]
    finally:
        dlg.deleteLater()


def test_picker_callback_returning_none_does_not_set_path(qapp, tmp_path):
    """Callback returning None ⇒ open_picker returns False, the panel
    state stays empty (no path / no timestamp)."""

    def _cb(_parent):
        return None
    dlg = SyncPairPickerDialog(
        camera_id="G9M2",
        reference_id="Apple iPhone 13",
        camera_default_dir=str(tmp_path),
        reference_default_dir=str(tmp_path),
        trip_tz=2.0,
        configured_tz=None,
        cam_picker_callback=_cb,
    )
    try:
        ok = dlg._cam_panel.open_picker(dlg)
        assert ok is False
        assert dlg._cam_panel.path is None
        assert dlg._cam_panel.timestamp is None
    finally:
        dlg.deleteLater()
