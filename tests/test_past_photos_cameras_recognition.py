"""Tests for the recognition wire-in inside ``PastPhotosCamerasDialog``
(spec/88 Slice 3).

The dialog's :meth:`_pick_pair` now tries the recognition flow first and
only falls back to the legacy hand-picked :class:`SyncPairPickerDialog`
when recognition isn't applicable (no EXIF source, no phone overlap, no
candidate clusters) or the user explicitly opted out.

These tests drive ``_try_recognition`` and ``_pick_pair`` through every
outcome without spinning a real event loop — :class:`RecognitionDialog`
and :class:`SyncPairPickerDialog` are monkey-patched out so we can
verify which one would have run.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

try:
    from PyQt6.QtWidgets import QApplication, QDialog
except ImportError:                                      # pragma: no cover
    QApplication = None
    QDialog = None

from core.fresh_source import SourceItem
from core.source_index import ScannedCamera, SourceIndex


@pytest.fixture
def qapp():
    if QApplication is None:
        pytest.skip("PyQt6 not installed")
    app = QApplication.instance() or QApplication([])
    yield app


# ── Fixture builders ─────────────────────────────────────────────────────


def _cam_item(name: str, t: datetime, camera_id: str) -> SourceItem:
    return SourceItem(
        path=Path(f"src/{name}"),
        timestamp=t,
        camera_id=camera_id,
    )


def _phone_item(name: str, t: datetime, tz_minutes: int) -> SourceItem:
    return SourceItem(
        path=Path(f"src/{name}"),
        timestamp=t,
        camera_id="iPhone",
        tz_offset_minutes=tz_minutes,
    )


def _index_with(items: list[SourceItem], root: Path) -> SourceIndex:
    """Build a SourceIndex with the cameras dict populated so the dialog's
    constructor can call ``cameras_sorted``."""
    cameras: dict[str, ScannedCamera] = {}
    for it in items:
        cam = cameras.setdefault(it.camera_id, ScannedCamera(
            camera_id=it.camera_id,
            is_phone=(it.camera_id == "iPhone"),
            file_count=0,
            date_range=None,
            paths=[],
            timestamps={},
        ))
        cam.file_count += 1
        cam.paths.append(it.path)
        cam.timestamps[it.path] = it.timestamp
    return SourceIndex(
        root=root, cameras=cameras, total_files=len(items),
        items=items,
    )


def _build_dialog(qapp, source_index, tmp_path):
    from mira.ui.pages.past_photos_cameras import PastPhotosCamerasDialog
    return PastPhotosCamerasDialog(
        source_index=source_index,
        root_dir=str(tmp_path),
        trip_tz=0.0,
        phone_reference_id="iPhone",
    )


# ── _try_recognition outcome gates ──────────────────────────────────────


def test_try_recognition_unavailable_without_source_index(qapp, tmp_path):
    """The legacy folder-name path doesn't carry EXIF — recognition can't
    do anything. The wire returns UNAVAILABLE so the caller goes straight
    to the manual picker."""
    from mira.ui.pages.past_photos_cameras import PastPhotosCamerasDialog

    dlg = PastPhotosCamerasDialog(
        camera_ids=["G9", "iPhone"],
        root_dir=str(tmp_path),
        trip_tz=0.0,
        phone_reference_id="iPhone",
    )
    try:
        assert dlg._try_recognition("G9") == dlg._REC_UNAVAILABLE
    finally:
        dlg.deleteLater()


def test_try_recognition_unavailable_when_camera_has_no_items(qapp, tmp_path):
    """Camera_id in the dialog's row list but zero EXIF items under it —
    recognition has nothing to pair, so it falls to manual."""
    idx = _index_with([
        _phone_item("p1.jpg", datetime(2025, 5, 12, 12, 0, 0), 0),
    ], tmp_path)
    # Ensure the camera_id we ask about exists as a row even if it has no items.
    idx.cameras["G9"] = ScannedCamera(
        camera_id="G9", is_phone=False, file_count=0,
        date_range=None, paths=[], timestamps={},
    )
    dlg = _build_dialog(qapp, idx, tmp_path)
    try:
        assert dlg._try_recognition("G9") == dlg._REC_UNAVAILABLE
    finally:
        dlg.deleteLater()


def test_try_recognition_unavailable_when_phone_has_no_items(qapp, tmp_path):
    """No phone EXIF items → spec/88 §5 "no phone overlap" → manual."""
    idx = _index_with([
        _cam_item("c1.rw2", datetime(2025, 5, 12, 12, 0, 0), "G9"),
    ], tmp_path)
    idx.cameras["iPhone"] = ScannedCamera(
        camera_id="iPhone", is_phone=True, file_count=0,
        date_range=None, paths=[], timestamps={},
    )
    dlg = _build_dialog(qapp, idx, tmp_path)
    try:
        assert dlg._try_recognition("G9") == dlg._REC_UNAVAILABLE
    finally:
        dlg.deleteLater()


def test_try_recognition_unavailable_when_no_clusters_form(qapp, tmp_path):
    """Spec/88 §5: sparse overlap with no cluster falls to manual rather
    than showing the user an empty recognition page."""
    # The cam item and phone item are >24h apart with the phone TZ at the
    # midpoint between zones — every implied κ snaps at the equidistant
    # boundary (tightness exactly 7.5 min → rejected).
    cam_items = [_cam_item("c1.rw2", datetime(2025, 5, 12, 12, 0, 0), "G9")]
    phone_items = [_phone_item(
        "p1.jpg", datetime(2025, 5, 12, 12, 7, 30), 0,
    )]
    idx = _index_with(cam_items + phone_items, tmp_path)
    dlg = _build_dialog(qapp, idx, tmp_path)
    try:
        assert dlg._try_recognition("G9") == dlg._REC_UNAVAILABLE
    finally:
        dlg.deleteLater()


# ── Recognition open/close outcomes (monkeypatched dialog) ──────────────


def _patch_recognition_dialog(monkeypatch, *, result, fallback=False,
                              cal_pair=None):
    """Install a fake RecognitionDialog that immediately returns the
    configured outcome from ``exec``."""
    class FakeRecognition:
        def __init__(self, **kwargs):
            self._kwargs = kwargs
            self._result = result
        def exec(self):
            return self._result
        def selected_pair(self):
            return cal_pair
        @property
        def fallback_to_manual(self):
            return fallback
        def deleteLater(self):
            pass

    monkeypatch.setattr(
        "mira.ui.pages.clock_recognition_dialog.RecognitionDialog",
        FakeRecognition,
    )


def _good_overlap_index(tmp_path):
    """Index that produces at least one cluster — diagonal pairs all at κ=0."""
    items = []
    for h in (10, 11, 12, 13):
        t = datetime(2025, 5, 12, h, 0, 0)
        items.append(_cam_item(f"c{h}.rw2", t, "G9"))
        items.append(_phone_item(f"p{h}.jpg", t, 0))
    return _index_with(items, tmp_path)


def test_try_recognition_confirmed_sets_the_pair_on_the_row(
    qapp, tmp_path, monkeypatch,
):
    """User picks a card + Apply → row gets the CalibrationPair from the
    recognition flow; method returns CONFIRMED."""
    from core.clock_calibration import CalibrationPair

    idx = _good_overlap_index(tmp_path)
    chosen = CalibrationPair(
        camera_path=Path("c10.rw2"),
        reference_path=Path("p10.jpg"),
        camera_time=datetime(2025, 5, 12, 10, 0, 0),
        reference_time=datetime(2025, 5, 12, 10, 0, 0),
    )
    _patch_recognition_dialog(
        monkeypatch,
        result=QDialog.DialogCode.Accepted,
        fallback=False,
        cal_pair=chosen,
    )
    dlg = _build_dialog(qapp, idx, tmp_path)
    try:
        outcome = dlg._try_recognition("G9")
        assert outcome == dlg._REC_CONFIRMED
        assert dlg._rows["G9"].pair() is chosen
    finally:
        dlg.deleteLater()


def test_try_recognition_fallback_returns_fallback_without_setting_pair(
    qapp, tmp_path, monkeypatch,
):
    """User clicks "Use manual pair…" → method returns FALLBACK; the row
    stays unset because manual picker hasn't run yet (caller decides)."""
    idx = _good_overlap_index(tmp_path)
    _patch_recognition_dialog(
        monkeypatch,
        result=QDialog.DialogCode.Accepted,
        fallback=True,
        cal_pair=None,
    )
    dlg = _build_dialog(qapp, idx, tmp_path)
    try:
        outcome = dlg._try_recognition("G9")
        assert outcome == dlg._REC_FALLBACK
        assert dlg._rows["G9"].pair() is None
    finally:
        dlg.deleteLater()


def test_try_recognition_cancel_returns_cancel(
    qapp, tmp_path, monkeypatch,
):
    """User dismisses the dialog → method returns CANCEL; the row stays
    unset and the caller does NOT open the manual picker behind their back."""
    idx = _good_overlap_index(tmp_path)
    _patch_recognition_dialog(
        monkeypatch,
        result=QDialog.DialogCode.Rejected,
        fallback=False,
        cal_pair=None,
    )
    dlg = _build_dialog(qapp, idx, tmp_path)
    try:
        outcome = dlg._try_recognition("G9")
        assert outcome == dlg._REC_CANCEL
        assert dlg._rows["G9"].pair() is None
    finally:
        dlg.deleteLater()


# ── _pick_pair routing ──────────────────────────────────────────────────


def test_pick_pair_opens_manual_when_recognition_unavailable(
    qapp, tmp_path, monkeypatch,
):
    """No source_index → recognition unavailable → manual picker opens."""
    from mira.ui.pages.past_photos_cameras import PastPhotosCamerasDialog

    manual_called = []

    def fake_manual(self, camera_id):
        manual_called.append(camera_id)

    monkeypatch.setattr(
        PastPhotosCamerasDialog, "_pick_pair_manual", fake_manual,
    )

    dlg = PastPhotosCamerasDialog(
        camera_ids=["G9", "iPhone"],
        root_dir=str(tmp_path),
        trip_tz=0.0,
        phone_reference_id="iPhone",
    )
    try:
        dlg._pick_pair("G9")
        assert manual_called == ["G9"]
    finally:
        dlg.deleteLater()


def test_pick_pair_skips_manual_after_recognition_confirm(
    qapp, tmp_path, monkeypatch,
):
    """Recognition CONFIRMED → manual must NOT open (the row's done)."""
    from core.clock_calibration import CalibrationPair
    from mira.ui.pages.past_photos_cameras import PastPhotosCamerasDialog

    idx = _good_overlap_index(tmp_path)
    chosen = CalibrationPair(
        camera_path=Path("c10.rw2"),
        reference_path=Path("p10.jpg"),
        camera_time=datetime(2025, 5, 12, 10, 0, 0),
        reference_time=datetime(2025, 5, 12, 10, 0, 0),
    )
    _patch_recognition_dialog(
        monkeypatch,
        result=QDialog.DialogCode.Accepted,
        fallback=False,
        cal_pair=chosen,
    )
    manual_called = []
    monkeypatch.setattr(
        PastPhotosCamerasDialog, "_pick_pair_manual",
        lambda self, cid: manual_called.append(cid),
    )

    dlg = _build_dialog(qapp, idx, tmp_path)
    try:
        dlg._pick_pair("G9")
        assert manual_called == []
        assert dlg._rows["G9"].pair() is chosen
    finally:
        dlg.deleteLater()


def test_pick_pair_skips_manual_after_recognition_cancel(
    qapp, tmp_path, monkeypatch,
):
    """Recognition CANCEL (user dismissed) → manual must NOT open behind
    the user's back. Their intent was to leave."""
    from mira.ui.pages.past_photos_cameras import PastPhotosCamerasDialog

    idx = _good_overlap_index(tmp_path)
    _patch_recognition_dialog(
        monkeypatch,
        result=QDialog.DialogCode.Rejected,
        fallback=False,
        cal_pair=None,
    )
    manual_called = []
    monkeypatch.setattr(
        PastPhotosCamerasDialog, "_pick_pair_manual",
        lambda self, cid: manual_called.append(cid),
    )

    dlg = _build_dialog(qapp, idx, tmp_path)
    try:
        dlg._pick_pair("G9")
        assert manual_called == []
    finally:
        dlg.deleteLater()


def test_pick_pair_opens_manual_after_recognition_fallback(
    qapp, tmp_path, monkeypatch,
):
    """Recognition FALLBACK (user clicked "Use manual pair…") → manual
    picker opens as the explicit next step."""
    from mira.ui.pages.past_photos_cameras import PastPhotosCamerasDialog

    idx = _good_overlap_index(tmp_path)
    _patch_recognition_dialog(
        monkeypatch,
        result=QDialog.DialogCode.Accepted,
        fallback=True,
        cal_pair=None,
    )
    manual_called = []
    monkeypatch.setattr(
        PastPhotosCamerasDialog, "_pick_pair_manual",
        lambda self, cid: manual_called.append(cid),
    )

    dlg = _build_dialog(qapp, idx, tmp_path)
    try:
        dlg._pick_pair("G9")
        assert manual_called == ["G9"]
    finally:
        dlg.deleteLater()


def test_pick_pair_on_reference_camera_warns_and_skips_both_paths(
    qapp, tmp_path, monkeypatch,
):
    """Asking the dialog to pair the reference against itself short-circuits
    via the existing QMessageBox — neither recognition nor manual runs."""
    from mira.ui.pages.past_photos_cameras import PastPhotosCamerasDialog
    from PyQt6.QtWidgets import QMessageBox

    idx = _good_overlap_index(tmp_path)
    manual_called = []
    monkeypatch.setattr(
        PastPhotosCamerasDialog, "_pick_pair_manual",
        lambda self, cid: manual_called.append(cid),
    )
    rec_called = []
    monkeypatch.setattr(
        PastPhotosCamerasDialog, "_try_recognition",
        lambda self, cid: rec_called.append(cid) or "fallback",
    )
    # Suppress the actual MessageBox so the test doesn't pop a window.
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda *a, **kw: QMessageBox.StandardButton.Ok),
    )

    dlg = _build_dialog(qapp, idx, tmp_path)
    try:
        dlg._pick_pair("iPhone")
        assert rec_called == []
        assert manual_called == []
    finally:
        dlg.deleteLater()
