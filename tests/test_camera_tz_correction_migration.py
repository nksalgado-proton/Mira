"""spec/127 §2 — migration from the single per-camera
``applied_offset_seconds`` / ``configured_tz_seconds`` columns into the
new per-(camera, trip-TZ-segment) ``camera_tz_correction`` table.

For an existing event whose camera rows carry a single applied offset,
the v17→v18 migration creates ONE correction row per camera (with the
old offset + zone), keyed by the event's **predominant** trip TZ (the
most common ``trip_day.tz_minutes`` value × 60).
"""
from __future__ import annotations

import sqlite3

from mira.store import models as m, schema
from mira.store.repo import EventStore


def _make_store(tmp_path) -> EventStore:
    return EventStore.create(
        tmp_path / "event.db",
        event_id="evt-mig",
        app_version="test",
        created_at="2026-05-30T00:00:00+00:00",
    )


def _roll_back_to_v17(conn: sqlite3.Connection) -> None:
    """Roll the fresh schema back to v17 so the v17→v18 migration
    has the right shape to operate on (the test rebuilds v17 then
    seeds + migrates forward to SCHEMA_VERSION)."""
    # The v18 DDL added camera_tz_correction; drop it so v17→v18 will
    # CREATE it fresh.
    conn.execute("DROP INDEX IF EXISTS ix_camera_tz_correction_tz")
    conn.execute("DROP TABLE IF EXISTS camera_tz_correction")
    # spec/144 v18→v19 added lineage.duration_ms; strip it so the
    # ADD COLUMN on the way back up doesn't collide.
    conn.execute("ALTER TABLE lineage DROP COLUMN duration_ms")
    # spec/152 v19→v20 added cut.transition_ms; strip it so the ADD
    # COLUMN on the way back up doesn't collide.
    conn.execute("ALTER TABLE cut DROP COLUMN transition_ms")
    # spec/156 v20→v21 added adjustment.filter_strength + video_adjustment.filter_strength
    # — strip so the ADD COLUMN steps on the way back up don't collide.
    conn.execute("ALTER TABLE adjustment DROP COLUMN filter_strength")
    conn.execute("ALTER TABLE video_adjustment DROP COLUMN filter_strength")
    # spec/155 v21→v22 added trip_day.map_image_path + event.map_image_path
    # — strip both so the ADD COLUMN steps on the way back up don't collide.
    conn.execute("ALTER TABLE trip_day DROP COLUMN map_image_path")
    conn.execute("ALTER TABLE event DROP COLUMN map_image_path")
    conn.execute("UPDATE schema_info SET schema_version = 17 WHERE id = 1")


# ── Predominant trip TZ ─────────────────────────────────────────────────


def test_migrates_offset_to_predominant_tz(tmp_path):
    """A standard trip — all 3 days share +5:45 → predominant TZ is
    +5:45 → 345×60 = 20 700 seconds. The GoPro's −3 zone + +8:45
    offset migrate to a single row at that key."""
    store = _make_store(tmp_path)
    conn = store.conn
    _roll_back_to_v17(conn)

    conn.execute(
        "INSERT INTO trip_day (day_number, date, tz_minutes) VALUES "
        "(1, '2026-03-10', 345), "
        "(2, '2026-03-11', 345), "
        "(3, '2026-03-12', 345)")
    conn.execute(
        "INSERT INTO camera (camera_id, configured_tz_seconds, "
        "applied_offset_seconds, applied_at) VALUES "
        "('GoPro', -10800, 31500, '2026-03-10T00:00:00')")

    schema.migrate(conn)

    assert schema.get_version(conn) == schema.SCHEMA_VERSION
    rows = conn.execute(
        "SELECT camera_id, trip_tz_seconds, configured_tz_seconds, "
        "nudge_seconds, applied_offset_seconds, applied_at "
        "FROM camera_tz_correction"
    ).fetchall()
    assert len(rows) == 1
    cam_id, tz_seconds, cfg, nudge, applied, stamp = rows[0]
    assert cam_id == "GoPro"
    assert int(tz_seconds) == 345 * 60               # +5:45
    assert int(cfg) == -10800                        # zone preserved
    assert int(nudge) == 0                           # fresh migration
    assert int(applied) == 31500                     # offset preserved
    assert stamp == "2026-03-10T00:00:00"
    store.close()


def test_zero_or_null_offset_cameras_get_no_row(tmp_path):
    """Cameras whose ``applied_offset_seconds`` is NULL (never asked)
    or 0 (Correct) get NO migration row — the dialog renders them as
    "Correct" by default. (Migration row presence ≡ a non-zero
    correction was already recorded.)"""
    store = _make_store(tmp_path)
    conn = store.conn
    _roll_back_to_v17(conn)

    conn.execute(
        "INSERT INTO trip_day (day_number, date, tz_minutes) VALUES "
        "(1, '2026-03-10', 0)")
    conn.execute(
        "INSERT INTO camera (camera_id, configured_tz_seconds, "
        "applied_offset_seconds, applied_at) VALUES "
        "('NullCam', NULL, NULL, NULL), "
        "('ZeroCam', NULL, 0, '2026-03-10T00:00:00'), "
        "('RealCam', NULL, 3600, '2026-03-10T00:00:00')")

    schema.migrate(conn)

    ids = {r[0] for r in conn.execute(
        "SELECT camera_id FROM camera_tz_correction").fetchall()}
    assert ids == {"RealCam"}      # only the non-zero one migrates
    store.close()


def test_two_segment_event_migrates_to_predominant_only(tmp_path):
    """A TZ-crossing trip: Days 1–6 at +5:45 (predominant) + Day 7 at
    +5:30. The legacy single per-camera row migrates ONLY to the
    predominant segment; the user fixes the second segment via the
    unified dialog (the bug the spec exists to fix)."""
    store = _make_store(tmp_path)
    conn = store.conn
    _roll_back_to_v17(conn)

    conn.execute(
        "INSERT INTO trip_day (day_number, date, tz_minutes) VALUES "
        "(1, '2026-03-10', 345), "
        "(2, '2026-03-11', 345), "
        "(3, '2026-03-12', 345), "
        "(4, '2026-03-13', 345), "
        "(5, '2026-03-14', 345), "
        "(6, '2026-03-15', 345), "
        "(7, '2026-03-16', 330)")
    conn.execute(
        "INSERT INTO camera (camera_id, configured_tz_seconds, "
        "applied_offset_seconds, applied_at) VALUES "
        "('GoPro', -10800, 31500, '2026-03-10T00:00:00')")

    schema.migrate(conn)

    rows = conn.execute(
        "SELECT camera_id, trip_tz_seconds, applied_offset_seconds "
        "FROM camera_tz_correction"
    ).fetchall()
    assert len(rows) == 1                              # only predominant
    assert int(rows[0][1]) == 345 * 60                 # +5:45, not +5:30
    assert int(rows[0][2]) == 31500
    store.close()


def test_event_with_no_trip_day_tz_skips_migration(tmp_path):
    """No ``trip_day`` row carries a TZ → there's no segment to key
    the correction by → migration creates NO rows (the table exists
    but is empty, and the camera columns stay readable as a legacy
    single-segment summary)."""
    store = _make_store(tmp_path)
    conn = store.conn
    _roll_back_to_v17(conn)

    conn.execute(
        "INSERT INTO trip_day (day_number, date, tz_minutes) VALUES "
        "(1, '2026-03-10', NULL)")
    conn.execute(
        "INSERT INTO camera (camera_id, configured_tz_seconds, "
        "applied_offset_seconds, applied_at) VALUES "
        "('Cam', NULL, 3600, '2026-03-10T00:00:00')")

    schema.migrate(conn)

    n = conn.execute(
        "SELECT COUNT(*) FROM camera_tz_correction").fetchone()[0]
    assert n == 0
    store.close()


def test_migration_is_idempotent(tmp_path):
    """Re-running migrate() after reaching v18 must not insert a
    second row for the same (camera, trip_tz_seconds) — the INSERT
    OR IGNORE protects against that."""
    store = _make_store(tmp_path)
    conn = store.conn
    _roll_back_to_v17(conn)
    conn.execute(
        "INSERT INTO trip_day (day_number, date, tz_minutes) VALUES "
        "(1, '2026-03-10', 0)")
    conn.execute(
        "INSERT INTO camera (camera_id, configured_tz_seconds, "
        "applied_offset_seconds, applied_at) VALUES "
        "('Cam', NULL, 3600, '2026-03-10T00:00:00')")

    schema.migrate(conn)
    n1 = conn.execute(
        "SELECT COUNT(*) FROM camera_tz_correction").fetchone()[0]
    schema.migrate(conn)
    n2 = conn.execute(
        "SELECT COUNT(*) FROM camera_tz_correction").fetchone()[0]
    assert n1 == n2 == 1
    store.close()


def test_round_trip_through_document_carries_correction(tmp_path):
    """End-to-end — write a CameraTzCorrection via EventDocument,
    load_document brings it back; round-trip equality."""
    from mira.store import json_dump

    store = _make_store(tmp_path)
    doc = m.EventDocument(
        event=m.Event(
            uuid="evt-mig",
            name="Round trip",
            created_at="2026-03-10T00:00:00+00:00",
            updated_at="2026-03-10T00:00:00+00:00"),
        trip_days=[m.TripDay(day_number=1, date="2026-03-10",
                             tz_minutes=345)],
        cameras=[m.Camera(camera_id="GoPro")],
        camera_tz_corrections=[m.CameraTzCorrection(
            camera_id="GoPro",
            trip_tz_seconds=345 * 60,
            configured_tz_seconds=-10800,
            nudge_seconds=-180,
            applied_offset_seconds=31_320,
            applied_at="2026-03-10T01:00:00",
        )],
    )
    store.save_document(doc)
    reloaded = store.load_document()
    assert len(reloaded.camera_tz_corrections) == 1
    rt = reloaded.camera_tz_corrections[0]
    assert rt.camera_id == "GoPro"
    assert int(rt.trip_tz_seconds) == 345 * 60
    assert int(rt.configured_tz_seconds) == -10800
    assert int(rt.nudge_seconds) == -180
    assert int(rt.applied_offset_seconds) == 31_320
    # JSON round-trip too — backup/restore parity.
    assert json_dump.from_json(json_dump.to_json(reloaded)) == reloaded
    store.close()
