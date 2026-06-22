"""spec/101 — the sync-pair picker's no-TZ branch must apply the RAW
measured offset (rounded to whole minutes), not the 15-minute TZ snap.

The bug: `_final_offset = snap_to_tz_offset(raw)` silently rounded a
camera's sub-15-min clock error away — 6 min → 0 (no correction),
8 min → 15 (over-correction). Photos then interleaved out of order in
the day grids, and near-midnight frames landed on the wrong day.

These tests pin the contract on the `selected_pair()` boundary
(`CalibrationPair.offset` is the value `build_calibration` and
`recompute_corrected_times` consume) and walk the end-to-end:
recompute with the raw-minute offset shifts items by the precise
delta and re-orders them correctly within and across days.

The pair-picker dialog is constructed offscreen; timestamps are set
directly on its panels so the QFileDialog branch never runs.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

try:
    from PyQt6.QtWidgets import QApplication, QDialog
except ImportError:                                          # pragma: no cover
    QApplication = None
    QDialog = None

from core.clock_calibration import snap_to_tz_offset
from mira.ui.base.sync_pair_picker import SyncPairPickerDialog


@pytest.fixture
def qapp():
    if QApplication is None:
        pytest.skip("PyQt6 not installed")
    app = QApplication.instance() or QApplication([])
    yield app


def _force_pair(
    dlg: SyncPairPickerDialog,
    cam_time: datetime, ref_time: datetime,
    tmp_path: Path,
) -> None:
    """Stand in for the QFileDialog → EXIF read flow: set the panels'
    private state so `_update_verdict()` reads cam_time / ref_time and
    `selected_pair()` can return a CalibrationPair without disk I/O."""
    cam_p = tmp_path / "cam.jpg"
    ref_p = tmp_path / "ref.jpg"
    cam_p.write_bytes(b"")
    ref_p.write_bytes(b"")
    dlg._cam_panel._path = cam_p                       # noqa: SLF001
    dlg._cam_panel._timestamp = cam_time                # noqa: SLF001
    dlg._ref_panel._path = ref_p                       # noqa: SLF001
    dlg._ref_panel._timestamp = ref_time                # noqa: SLF001
    dlg._update_verdict()                              # noqa: SLF001


def _accepted_pair(
    cam_time: datetime, ref_time: datetime,
    tmp_path: Path, qapp,
):
    dlg = SyncPairPickerDialog(
        camera_id="G9", reference_id="phone",
        camera_default_dir=str(tmp_path),
        reference_default_dir=str(tmp_path),
        trip_tz=0.0, configured_tz=None,    # no TZ declared → raw branch
    )
    try:
        _force_pair(dlg, cam_time, ref_time, tmp_path)
        # Accept the dialog without showing it.
        dlg.setResult(QDialog.DialogCode.Accepted)
        return dlg.selected_pair(), dlg
    finally:
        dlg.deleteLater()


# ── Core: selected_pair() applies raw, not snap ──────────────────


def test_raw_six_minutes_applies_six_not_zero(qapp, tmp_path):
    """A 6-min raw delta would have snapped to 0 (no correction).
    spec/101: applies 6 min to the minute. The previous bug silently
    rounded this away."""
    cam_t = datetime(2026, 4, 1, 10, 0, 0)
    ref_t = cam_t + timedelta(minutes=6)
    pair, _ = _accepted_pair(cam_t, ref_t, tmp_path, qapp)
    assert pair is not None
    assert pair.offset == timedelta(minutes=6), (
        f"6-min raw must apply 6 min — got {pair.offset}. "
        f"(snap would have given 0; that's the spec/101 bug.)")
    # Sanity: the snap rounded the wrong way → 0 indeed.
    assert snap_to_tz_offset(timedelta(minutes=6)) == timedelta(0)


def test_raw_eight_minutes_applies_eight_not_fifteen(qapp, tmp_path):
    """An 8-min raw delta would have snapped to +15 (over-correction).
    spec/101: applies 8 min to the minute."""
    cam_t = datetime(2026, 4, 1, 10, 0, 0)
    ref_t = cam_t + timedelta(minutes=8)
    pair, _ = _accepted_pair(cam_t, ref_t, tmp_path, qapp)
    assert pair is not None
    assert pair.offset == timedelta(minutes=8)
    # Sanity: the snap would have over-corrected.
    assert snap_to_tz_offset(timedelta(minutes=8)) == timedelta(minutes=15)


def test_raw_one_hour_seven_minutes_applies_67_not_60_or_75(
    qapp, tmp_path,
):
    """Spec/101 §6 example: a 1h 07min raw delta is 67 min; snap would
    pick 60 or 75 (both wrong by 7+ min)."""
    cam_t = datetime(2026, 4, 1, 10, 0, 0)
    ref_t = cam_t + timedelta(hours=1, minutes=7)
    pair, _ = _accepted_pair(cam_t, ref_t, tmp_path, qapp)
    assert pair is not None
    assert pair.offset == timedelta(minutes=67)
    snapped = snap_to_tz_offset(timedelta(hours=1, minutes=7))
    assert snapped in (timedelta(minutes=60), timedelta(minutes=75))


def test_clean_grid_offset_unchanged(qapp, tmp_path):
    """spec/101 acceptance: a pair whose raw delta IS a clean timezone
    (e.g. exactly +5:45 Nepal) applies the same correction it did
    before — raw ≈ grid, so the result is identical."""
    cam_t = datetime(2026, 3, 10, 8, 0, 0)
    ref_t = cam_t + timedelta(hours=5, minutes=45)
    pair, _ = _accepted_pair(cam_t, ref_t, tmp_path, qapp)
    assert pair is not None
    assert pair.offset == timedelta(hours=5, minutes=45)
    assert pair.offset == snap_to_tz_offset(pair.offset), (
        "clean-TZ pair must agree with the snap — the parity case")


def test_sub_minute_seconds_are_rounded_to_whole_minutes(qapp, tmp_path):
    """`recompute_corrected_times` takes `applied_offset_minutes: int`,
    so the apply boundary rounds to whole minutes — sub-minute is
    noise (cameras aren't NTP-synced; "same moment" agrees to ~seconds
    at best)."""
    cam_t = datetime(2026, 4, 1, 10, 0, 0)
    ref_t = cam_t + timedelta(minutes=6, seconds=12)
    pair, _ = _accepted_pair(cam_t, ref_t, tmp_path, qapp)
    assert pair is not None
    assert pair.offset == timedelta(minutes=6)


# ── TZ-declared branch is unchanged ─────────────────────────────


def test_tz_declared_branch_still_applies_tz_expected(qapp, tmp_path):
    """spec/101 leaves the TZ-declared branch UNCHANGED — a declaration
    is genuinely grid-aligned. The pair within tolerance still yields
    the declaration-derived value, not the raw."""
    cam_t = datetime(2026, 3, 10, 8, 0, 0)
    # trip_tz = +5.75 Nepal, configured_tz = -3 SP → expected +8:45.
    # Use a "messy" raw a few minutes off from the expected.
    ref_t = cam_t + timedelta(hours=8, minutes=43)
    dlg = SyncPairPickerDialog(
        camera_id="G9", reference_id="phone",
        camera_default_dir=str(tmp_path),
        reference_default_dir=str(tmp_path),
        trip_tz=5.75, configured_tz=-3.0,
    )
    try:
        _force_pair(dlg, cam_t, ref_t, tmp_path)
        dlg.setResult(QDialog.DialogCode.Accepted)
        pair = dlg.selected_pair()
        assert pair is not None
        # The declaration-derived value is what's applied: trip − conf
        # = 5.75 − (−3) = 8.75 h = 8:45 exactly.
        assert pair.offset == timedelta(hours=8, minutes=45)
    finally:
        dlg.deleteLater()


# ── Snap-disagreement warning still fires on a far-off pair ─────


def test_snap_disagreement_warning_still_fires(qapp, tmp_path):
    """spec/101 keeps the >5-min `snap_disagreement` warning — a pair
    that's far from any clean grid is still flagged as suspicious."""
    cam_t = datetime(2026, 4, 1, 10, 0, 0)
    # 22 min off a clean grid (snap goes to 15 OR 30; either way the
    # disagreement is >5 min).
    ref_t = cam_t + timedelta(minutes=22)
    dlg = SyncPairPickerDialog(
        camera_id="G9", reference_id="phone",
        camera_default_dir=str(tmp_path),
        reference_default_dir=str(tmp_path),
        trip_tz=0.0, configured_tz=None,
    )
    try:
        _force_pair(dlg, cam_t, ref_t, tmp_path)
        text = dlg._verdict.text()                         # noqa: SLF001
        # The warning is the orange/red span — assert by its colour
        # (kept verbatim across spec/101) so the test resists wording
        # tweaks.
        assert "d97706" in text, (
            f"expected the snap-disagreement warning span; got:\n{text}")
        # But the applied offset is STILL raw (22 min), not the snap.
        dlg.setResult(QDialog.DialogCode.Accepted)
        pair = dlg.selected_pair()
        assert pair is not None
        assert pair.offset == timedelta(minutes=22)
    finally:
        dlg.deleteLater()


def test_clean_pair_does_not_warn(qapp, tmp_path):
    """The parity case: a clean +5:45 pair has 0 disagreement, no
    warning span."""
    cam_t = datetime(2026, 3, 10, 8, 0, 0)
    ref_t = cam_t + timedelta(hours=5, minutes=45)
    dlg = SyncPairPickerDialog(
        camera_id="G9", reference_id="phone",
        camera_default_dir=str(tmp_path),
        reference_default_dir=str(tmp_path),
        trip_tz=0.0, configured_tz=None,
    )
    try:
        _force_pair(dlg, cam_t, ref_t, tmp_path)
        assert "d97706" not in dlg._verdict.text()         # noqa: SLF001
    finally:
        dlg.deleteLater()


# ── End-to-end: raw minutes shift items via recompute_corrected_times


def test_recompute_with_raw_pair_offset_orders_items_correctly(tmp_path):
    """spec/101 acceptance walk-through: a 6-min raw pair → 6-min
    apply → `recompute_corrected_times` shifts every G9 item by 6
    min, interleaving correctly with the phone's items. Before
    spec/101 the snap to 0 would have left G9 unshifted and the
    interleave wrong."""
    from datetime import datetime as _dt

    from mira.gateway import EventsIndex, Gateway
    from mira.settings.repo import SettingsRepo
    from mira.store import models as m

    base = tmp_path / "lib"
    settings = SettingsRepo(tmp_path / "settings.json")
    index = EventsIndex(tmp_path / "events_index.json")
    gw = Gateway(settings=settings, index=index)
    gw.set_photos_base_path(str(base))

    # A G9 frame at 10:00 that should sit JUST BEFORE the phone's
    # 10:05 frame after correction. With the buggy snap (0 min), the
    # G9 would stay at 10:00 raw and a phone frame at 10:03 would
    # have already interleaved between them (we'd need a separate
    # phone snapshot to see the order flip). The simplest end-to-end
    # check: assert the corrected time for the G9 photo is RAW + 6 min.
    doc = m.EventDocument(
        event=m.Event(
            uuid="evt-101", name="spec101",
            created_at="2026-04-01T00:00:00",
            updated_at="2026-04-01T00:00:00",
            start_date="2026-04-01", end_date="2026-04-02",
        ),
        trip_days=[
            m.TripDay(day_number=1, date="2026-04-01", tz_minutes=0),
            m.TripDay(day_number=2, date="2026-04-02", tz_minutes=0),
        ],
        cameras=[m.Camera(camera_id="G9"), m.Camera(camera_id="phone")],
        items=[
            # G9 at 10:00 — should become 10:06 after the raw 6-min
            # apply. Snap (the bug) would have left this at 10:00.
            m.Item(
                id="g9-a", kind="photo", origin_relpath="g9-a.rw2",
                sha256="g9-a", byte_size=1,
                materialized_at="2026-04-01T00:00:00",
                materialized_phase="ingest", camera_id="G9",
                capture_time_raw="2026-04-01T10:00:00",
                capture_time_corrected="2026-04-01T10:00:00",
                day_number=1,
                created_at="2026-04-01T00:00:00",
            ),
            # G9 frame at 23:57 — with the +6 min apply it crosses
            # midnight into Day 2 (00:03). Snap (0 min) would have
            # kept it on Day 1 — the near-midnight "wrong day" bug.
            m.Item(
                id="g9-b", kind="photo", origin_relpath="g9-b.rw2",
                sha256="g9-b", byte_size=1,
                materialized_at="2026-04-01T00:00:00",
                materialized_phase="ingest", camera_id="G9",
                capture_time_raw="2026-04-01T23:57:00",
                capture_time_corrected="2026-04-01T23:57:00",
                day_number=1,
                created_at="2026-04-01T00:00:00",
            ),
            # Phone reference at 10:10 — must NOT shift. Placed
            # AFTER the post-correction G9 frame (10:06) so the
            # interleave check is meaningful.
            m.Item(
                id="ph", kind="photo", origin_relpath="ph.jpg",
                sha256="ph", byte_size=1,
                materialized_at="2026-04-01T00:00:00",
                materialized_phase="ingest", camera_id="phone",
                capture_time_raw="2026-04-01T10:10:00",
                capture_time_corrected="2026-04-01T10:10:00",
                day_number=1,
                created_at="2026-04-01T00:00:00",
            ),
        ],
    )
    root = base / "spec101"
    root.mkdir(parents=True, exist_ok=True)
    eg = gw.create_event(doc, root)
    try:
        # Apply the RAW minute offset the spec/101 fix yields.
        affected = eg.recompute_corrected_times(
            "G9", applied_offset_minutes=6)
        assert set(affected) == {"g9-a", "g9-b"}

        a = eg.item("g9-a")
        assert a.capture_time_raw == "2026-04-01T10:00:00"   # never mutated
        assert a.capture_time_corrected == "2026-04-01T10:06:00", (
            "raw 10:00 + 6 min = 10:06; the buggy snap to 0 would "
            "have left this at 10:00 and the G9/phone interleave "
            "would be wrong")
        # Sits BEFORE the phone's 10:05 frame is the natural reading;
        # since the corrected time is the source of truth for ordering,
        # the comparison itself is the proof.
        assert a.capture_time_corrected < eg.item("ph").capture_time_corrected

        b = eg.item("g9-b")
        assert b.capture_time_corrected == "2026-04-02T00:03:00", (
            "raw 23:57 + 6 min crosses midnight; spec/101's "
            "near-midnight wrong-day case is exactly this")
        assert b.day_number == 2, (
            "Day 2 by the corrected date — the snap to 0 would have "
            "left this on Day 1 (the 'wrong day' user symptom)")

        # The phone is untouched.
        assert eg.item("ph").capture_time_corrected == "2026-04-01T10:10:00"
    finally:
        eg.close()
