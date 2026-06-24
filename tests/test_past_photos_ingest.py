"""Tests for the reused past-photos flow's data seam (charter §5.2, spec/10).

The UI dialogs (PastPhotosDialog / PastPhotosCamerasDialog / PlanEditorDialog) are the
reused legacy widgets — their behaviour is the legacy's, exhaustively tuned. The ONLY new
logic is the commit seam: `plan_from_reconcile` converts the flow's gathered plan +
calibration into an engine `IngestPlan`, which `run_ingest` materialises. That's what
these tests pin down.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

from core.fresh_source import SourceItem
from core.models import TripDay
from mira.gateway import EventsIndex, Gateway
from mira.ingest import plan_from_reconcile, run_ingest
from mira.settings.repo import SettingsRepo


def _gateway(tmp_path, base):
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "settings.json"),
        index=EventsIndex(tmp_path / "events_index.json"),
    )
    gw.set_photos_base_path(str(base))
    return gw


def _cam(camera_id, configured_tz, *, is_phone=False, is_reference=False, pairs=()):
    # Duck-typed CameraInput (the legacy calibration loop's output rows).
    return SimpleNamespace(
        camera_id=camera_id, configured_tz=configured_tz,
        calibration_pairs=list(pairs), is_phone=is_phone, is_reference=is_reference,
    )


def test_plan_from_reconcile_maps_days_and_cameras():
    edited = [
        TripDay(day_number=1, date=date(2026, 3, 10), description="Kathmandu", tz_offset=5.75),
        TripDay(day_number=2, date=date(2026, 3, 11), description="Pokhara", tz_offset=5.75),
    ]
    groups = {5.75: [
        _cam("DC-G9M2", -3.0),                      # clock on São Paulo
        _cam("iPhone 15", 5.75, is_phone=True, is_reference=True),
    ]}
    plan = plan_from_reconcile(
        event_id="e1", event_name="2026 - Nepal",
        event_root=Path("/x/2026 - Nepal"), source_root=Path("/src"),
        edited_days=edited, tz_camera_groups=groups, trip_tz=5.75,
    )
    assert [d.tz_offset_hours for d in plan.days] == [5.75, 5.75]
    assert plan.start_date == "2026-03-10" and plan.end_date == "2026-03-11"

    g9 = next(c for c in plan.cameras if c.camera_id == "DC-G9M2")
    assert g9.configured_tz_hours == -3.0 and g9.calibration is None
    ip = next(c for c in plan.cameras if c.camera_id == "iPhone 15")
    assert ip.is_phone and ip.is_reference and ip.configured_tz_hours is None


def test_plan_from_reconcile_then_ingest_applies_the_correction(tmp_path):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    g9 = src / "g9_0001.JPG"
    g9.write_bytes(b"g9-bytes")
    items = [SourceItem(g9, datetime(2026, 3, 10, 8, 0), "DC-G9M2")]

    plan = plan_from_reconcile(
        event_id="e1", event_name="2026 - Nepal",
        event_root=base / "2026 - Nepal", source_root=src,
        edited_days=[TripDay(day_number=1, date=date(2026, 3, 10), description="K", tz_offset=5.75)],
        tz_camera_groups={5.75: [_cam("DC-G9M2", -3.0)]},
        trip_tz=5.75,
    )
    run_ingest(plan, gw, source_items=items)

    eg = gw.open_event("e1")
    try:
        it = next(i for i in eg.items() if i.origin_relpath.endswith("g9_0001.JPG"))
    finally:
        eg.close()
    # raw 08:00 + (5.75 − (−3.0)) = +8:45 → 16:45.
    assert it.capture_time_corrected == "2026-03-10T16:45:00"
    assert it.tz_offset_seconds == 525 * 60


def test_past_photos_flow_constructs(qapp, tmp_path):
    """The reused dialogs build against the gateway (import + data-seam wiring intact)."""
    from mira.ui.pages.past_photos_dialog import PastPhotosDialog

    gw = _gateway(tmp_path, tmp_path / "lib")
    dlg = PastPhotosDialog(gw)
    assert dlg.gateway is gw
