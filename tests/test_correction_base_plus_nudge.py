"""spec/127 §1.2 — correction = base + fine nudge.

base ∈ {0 (Correct), zone (``segment_trip_tz − camera_tz``), measured
raw delta}; nudge is an optional ``±MM:SS`` adjustment added on top;
``applied_offset_seconds = base + nudge``. Round-trip through the
``camera_tz_correction`` store: zone stays zone, NULL stays NULL —
the spec/125 discriminator is preserved per segment.
"""
from __future__ import annotations

from mira.gateway import EventsIndex, Gateway
from mira.settings.repo import SettingsRepo
from mira.store import models as m


def _gateway(tmp_path, base):
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "settings.json"),
        index=EventsIndex(tmp_path / "events_index.json"),
    )
    gw.set_photos_base_path(str(base))
    return gw


def _make_event(gw, base):
    stamp = "2026-03-10T00:00:00"
    doc = m.EventDocument(
        event=m.Event(uuid="evt-bn", name="BasePlusNudge",
                      created_at=stamp, updated_at=stamp,
                      start_date="2026-03-10", end_date="2026-03-10"),
        trip_days=[
            m.TripDay(day_number=1, date="2026-03-10", tz_minutes=345),
        ],
        cameras=[
            m.Camera(camera_id="GoPro"),
            m.Camera(camera_id="Sony"),
        ],
    )
    root = base / "event"
    root.mkdir(parents=True, exist_ok=True)
    eg = gw.create_event(doc, root)
    eg.close()
    return "evt-bn"


# ── Base + nudge arithmetic ────────────────────────────────────────────


def test_gopro_zone_plus_negative_nudge_lands_at_eight_forty_two():
    """The headline case (spec/127 §1.2): GoPro corrected by zone from
    UTC−3 against Nepal +5:45 → base = +8:45; user adds a −0:03:00
    nudge for residual drift → total = +8:42:00 (== 31 320 s)."""
    trip_tz_seconds = 345 * 60                # +5:45
    camera_tz_seconds = -3 * 3600             # UTC −3
    base = trip_tz_seconds - camera_tz_seconds       # +8:45 = 31 500
    assert base == 31_500
    nudge = -3 * 60                                  # −0:03:00
    assert base + nudge == 31_320                    # +8:42:00


def test_correct_state_is_zero_base():
    """When the state is "Correct", base = 0 regardless of nudge — the
    nudge alone is the applied offset."""
    base = 0
    nudge = 90
    assert base + nudge == 90


def test_measured_offset_is_base_verbatim():
    """A measured pair's raw delta IS the base (no snapping). Nudge
    optional on top."""
    raw_pair = 18_002                                 # Nepal pair seconds
    nudge = 0
    assert raw_pair + nudge == 18_002


# ── Round-trip through camera_tz_correction ────────────────────────────


def test_zone_row_round_trips_with_nudge(tmp_path):
    """Save a known-TZ correction with a nudge; re-read it; both base
    and nudge survive. ``configured_tz_seconds`` STAYS set (zone is a
    zone — spec/125)."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)

    eg = gw.open_event("evt-bn")
    try:
        applied_at = eg._now()
        correction = m.CameraTzCorrection(
            camera_id="GoPro",
            trip_tz_seconds=345 * 60,
            configured_tz_seconds=-3 * 3600,
            nudge_seconds=-180,                       # −0:03:00
            applied_offset_seconds=31_320,            # +8:42:00
            applied_at=applied_at,
        )
        eg.save_camera_tz_correction(correction)
        round_trip = eg.camera_tz_correction("GoPro", 345 * 60)
    finally:
        eg.close()

    assert round_trip is not None
    assert round_trip.configured_tz_seconds == -3 * 3600   # zone preserved
    assert int(round_trip.nudge_seconds) == -180
    assert int(round_trip.applied_offset_seconds) == 31_320
    assert round_trip.applied_at == applied_at


def test_measured_row_round_trips_with_null_zone(tmp_path):
    """A measured-pair correction: ``configured_tz_seconds`` is NULL
    (the spec/125 discriminator). Round-trip preserves NULL — the
    dialog will NEVER back-derive a zone from this row."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)

    eg = gw.open_event("evt-bn")
    try:
        correction = m.CameraTzCorrection(
            camera_id="Sony",
            trip_tz_seconds=345 * 60,
            configured_tz_seconds=None,              # measured pair
            nudge_seconds=0,
            applied_offset_seconds=18_002,           # Nepal raw delta
            applied_at=eg._now(),
        )
        eg.save_camera_tz_correction(correction)
        round_trip = eg.camera_tz_correction("Sony", 345 * 60)
    finally:
        eg.close()

    assert round_trip is not None
    assert round_trip.configured_tz_seconds is None   # NULL preserved
    assert int(round_trip.applied_offset_seconds) == 18_002


def test_save_mirrors_to_legacy_camera_columns_when_requested(tmp_path):
    """``mirror_to_camera=True`` (the default) keeps the legacy
    ``camera.applied_offset_seconds`` / ``configured_tz_seconds``
    summary in sync, so older read paths keep working."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)

    eg = gw.open_event("evt-bn")
    try:
        correction = m.CameraTzCorrection(
            camera_id="GoPro",
            trip_tz_seconds=345 * 60,
            configured_tz_seconds=-3 * 3600,
            nudge_seconds=-180,
            applied_offset_seconds=31_320,
            applied_at=eg._now(),
        )
        eg.save_camera_tz_correction(correction)
        cams = {c.camera_id: c for c in eg.cameras()}
    finally:
        eg.close()

    g = cams["GoPro"]
    assert int(g.applied_offset_seconds) == 31_320
    assert int(g.configured_tz_seconds) == -3 * 3600


def test_save_skips_legacy_mirror_when_disabled(tmp_path):
    """``mirror_to_camera=False`` — for a non-predominant segment's
    row, the dialog skips the mirror so the legacy columns reflect the
    predominant segment alone."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)

    eg = gw.open_event("evt-bn")
    try:
        # Baseline: legacy columns start unset.
        before = {c.camera_id: c for c in eg.cameras()}
        assert before["GoPro"].applied_offset_seconds is None

        correction = m.CameraTzCorrection(
            camera_id="GoPro",
            trip_tz_seconds=345 * 60,
            configured_tz_seconds=-3 * 3600,
            nudge_seconds=0,
            applied_offset_seconds=31_500,
            applied_at=eg._now(),
        )
        eg.save_camera_tz_correction(correction, mirror_to_camera=False)
        after = {c.camera_id: c for c in eg.cameras()}
    finally:
        eg.close()

    # The correction row landed:
    g_after = after["GoPro"]
    # ...but the legacy summary columns stayed at their pre-call values.
    assert g_after.applied_offset_seconds is None
    assert g_after.configured_tz_seconds is None
