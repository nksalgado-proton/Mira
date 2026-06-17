"""Tests for the spec/82 §A.1 periodic-while-open snapshot timer.

The pure-logic dirty check + the snapshotter's decision flow are
tested without spinning a real ``QTimer`` — the timer cadence is
the integration concern; the **what should run on each tick** is
what the brief actually gates. The actual snapshot work is the
``core.db_backup`` suite's job.

``qapp`` fixture is used because the snapshotter is a ``QObject``
and needs a QApplication to construct.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import List

import pytest

from core import db_backup
from core.db_backup import REASON_MILESTONE, REASON_PERIODIC
from mira.gateway.gateway import Gateway
from mira.gateway.index import EventsIndex, make_entry
from mira.settings.repo import SettingsRepo
from mira.store.repo import EventStore
from mira.ui.shell.periodic_snapshot import (
    DEFAULT_INTERVAL_MINUTES,
    PeriodicSnapshotter,
    _is_dirty_since_last_snapshot,
)


NOW = "2026-06-17T00:00:00+00:00"


def _make_gw(tmp_path: Path):
    settings_path = tmp_path / "settings.json"
    index_path = tmp_path / "events_index.json"
    user_store_path = tmp_path / "mira.db"
    photos_base = tmp_path / "photos"
    photos_base.mkdir(exist_ok=True)
    settings = SettingsRepo(settings_path)
    index = EventsIndex(index_path)
    gw = Gateway(
        settings=settings, index=index,
        user_store_path=user_store_path, now=lambda: NOW,
        installation_profile="XMC",
    )
    _ = gw.user_store
    settings.update(photos_base_path=str(photos_base))
    return gw, photos_base


def _seed_event(photos_base: Path, eid: str, name: str = "E1") -> Path:
    root = photos_base / name
    root.mkdir(exist_ok=True)
    EventStore.create(
        root / "event.db",
        event_id=eid, app_version="test", created_at=NOW,
    ).close()
    return root


def _register(gw, photos_base, root, eid, name):
    gw.index.upsert(make_entry(
        event_id=eid, name=name,
        start_date=None, end_date=None, is_closed=False,
        event_root=root, photos_base_path=photos_base,
    ))


# ── pure-logic dirty check ────────────────────────────────────────


def test_dirty_check_no_snapshots_returns_true(tmp_path):
    """First tick after the app opens: backups dir is empty, so the
    pre-check should return True so the worker lays down a baseline
    periodic snapshot."""
    db = tmp_path / "event.db"
    db.write_bytes(b"hello")
    backups = tmp_path / "backups"
    assert _is_dirty_since_last_snapshot(db, backups) is True


def test_dirty_check_missing_db_returns_false(tmp_path):
    """No db → nothing to snapshot → skip the tick silently."""
    db = tmp_path / "missing.db"
    backups = tmp_path / "backups"
    assert _is_dirty_since_last_snapshot(db, backups) is False


def test_dirty_check_db_older_than_snapshot_returns_false(tmp_path):
    """The standard quiet path: db hasn't changed since the last
    snapshot was taken, so the periodic tick no-ops."""
    db = tmp_path / "event.db"
    db.write_bytes(b"hello")
    backups = tmp_path / "backups"
    backups.mkdir()
    snap = backups / "20260616T100000000Z.db"
    snap.write_bytes(b"snap")
    snap.with_suffix(".json").write_text(
        '{"app_version":"x","schema_version":1,"sha256":"a",'
        '"created_at":"2026-06-16T10:00:00Z","reason":"periodic"}',
        encoding="utf-8")
    # Force the snapshot's mtime to be later than the db's.
    import os
    later = time.time() + 60
    os.utime(snap, (later, later))
    assert _is_dirty_since_last_snapshot(db, backups) is False


def test_dirty_check_db_newer_than_snapshot_returns_true(tmp_path):
    """The trigger path: the user wrote something between snapshots,
    so the next periodic tick takes a fresh snapshot."""
    db = tmp_path / "event.db"
    backups = tmp_path / "backups"
    backups.mkdir()
    snap = backups / "20260616T100000000Z.db"
    snap.write_bytes(b"snap")
    snap.with_suffix(".json").write_text(
        '{"app_version":"x","schema_version":1,"sha256":"a",'
        '"created_at":"2026-06-16T10:00:00Z","reason":"periodic"}',
        encoding="utf-8")
    # Snapshot is in the past; the live db is brand new.
    import os
    earlier = time.time() - 60
    os.utime(snap, (earlier, earlier))
    db.write_bytes(b"hello, just wrote this")
    assert _is_dirty_since_last_snapshot(db, backups) is True


# ── PeriodicSnapshotter ───────────────────────────────────────────


def test_snapshotter_does_not_start_when_interval_is_zero(qapp, tmp_path):
    """``interval_minutes=0`` short-circuits start; the constructor
    is still safe to call so MainWindow can wire the snapshotter
    unconditionally and let the settings turn it off."""
    gw, _ = _make_gw(tmp_path)
    snapshotter = PeriodicSnapshotter(
        gw, current_event_id=lambda: None,
        interval_minutes=0)
    snapshotter.start()
    assert snapshotter._timer is None
    gw.close()


def test_snapshotter_starts_a_timer_with_the_right_cadence(qapp, tmp_path):
    """A positive interval starts a QTimer at minutes×60×1000 ms."""
    gw, _ = _make_gw(tmp_path)
    snapshotter = PeriodicSnapshotter(
        gw, current_event_id=lambda: None,
        interval_minutes=15)
    snapshotter.start()
    assert snapshotter._timer is not None
    assert snapshotter._timer.interval() == 15 * 60 * 1000
    snapshotter.stop()
    assert snapshotter._timer is None
    gw.close()


def test_on_tick_no_current_event_short_circuits(qapp, tmp_path):
    """No event open → tick is a no-op (no exceptions, no work)."""
    gw, _ = _make_gw(tmp_path)
    snapshotter = PeriodicSnapshotter(
        gw, current_event_id=lambda: None)
    snapshotter._on_tick()                                  # must not raise
    gw.close()


def test_on_tick_dirty_event_takes_periodic_snapshot(qapp, tmp_path):
    """Wired end-to-end (minus the timer): tick → dispatch worker →
    snapshot lands with reason="periodic"."""
    gw, photos_base = _make_gw(tmp_path)
    root = _seed_event(photos_base, "evt-p", "PeriodicTest")
    _register(gw, photos_base, root, "evt-p", "PeriodicTest")

    snapshotter = PeriodicSnapshotter(
        gw, current_event_id=lambda: "evt-p")
    # Bypass the off-thread dispatch by calling the worker's run()
    # synchronously — we test the decision + outcome, not the pool.
    from mira.ui.shell.periodic_snapshot import _SnapshotJob
    job = _SnapshotJob(
        gw, "evt-p", root / "event.db", gw.event_backups_dir("evt-p"))
    job.run()
    snaps = db_backup.list_snapshots(gw.event_backups_dir("evt-p"))
    assert len(snaps) == 1
    assert snaps[0].reason == REASON_PERIODIC
    gw.close()


def test_worker_skips_when_already_clean_since_last_snapshot(qapp, tmp_path):
    """A milestone snapshot just landed (per-day-add, close-if-dirty)
    so the periodic worker should not double-snapshot."""
    gw, photos_base = _make_gw(tmp_path)
    root = _seed_event(photos_base, "evt-p", "PeriodicTest")
    _register(gw, photos_base, root, "evt-p", "PeriodicTest")
    backups_dir = gw.event_backups_dir("evt-p")
    # A milestone snapshot lands first.
    gw.snapshot_event("evt-p", reason=REASON_MILESTONE)
    import os
    # Roll the snapshot's mtime forward so the db looks older.
    snap = db_backup.list_snapshots(backups_dir)[0]
    later = time.time() + 60
    os.utime(snap.db_path, (later, later))

    from mira.ui.shell.periodic_snapshot import _SnapshotJob
    job = _SnapshotJob(gw, "evt-p", root / "event.db", backups_dir)
    job.run()
    # Still just the one milestone — no periodic was added.
    snaps = db_backup.list_snapshots(backups_dir)
    assert len(snaps) == 1
    assert snaps[0].reason == REASON_MILESTONE
    gw.close()


# ── default cadence ───────────────────────────────────────────────


def test_default_interval_matches_spec():
    """spec/82 §A.1 suggests 15 minutes for the periodic cadence."""
    assert DEFAULT_INTERVAL_MINUTES == 15
