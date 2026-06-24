"""Tests for the spec/94 Phase 4b startup reconcile wiring.

The reconcile primitive (``Gateway.reconcile_global_items``) was
already covered by :mod:`tests.test_phase2_wiring` — what was missing
before Phase 4b was the call site at app startup. Without it, an
event that landed (or migrated through a schema bump) without being
opened-and-mutated by THIS process kept a stale or absent
``global_items`` slice, and the cross-event resolver returned empty
or partial results for EXIF / gear / location filters.

These tests pin the small helper :func:`mira.ui.app._startup_reconcile_global_items`
that ``main()`` now calls right after the MainWindow is built.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mira.store.repo import EventStore
from mira.user_store import models as um


NOW = "2026-06-21T00:00:00+00:00"


def _make_umbrella(tmp_path):
    from mira.gateway.gateway import Gateway
    from mira.gateway.index import EventsIndex
    from mira.settings.repo import SettingsRepo

    settings = SettingsRepo(tmp_path / "settings.json")
    index = EventsIndex(tmp_path / "events_index.json")
    photos_base = tmp_path / "photos"
    photos_base.mkdir()
    gw = Gateway(
        settings=settings, index=index,
        user_store_path=tmp_path / "mira.db",
        now=lambda: NOW, installation_profile="XMC",
    )
    _ = gw.user_store
    settings.update(photos_base_path=str(photos_base))
    return gw, photos_base


def _seed_event(photos_base, *, name, eid):
    root = photos_base / name
    root.mkdir()
    store = EventStore.create(
        root / "event.db",
        event_id=eid, app_version="test", created_at=NOW,
    )
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO event (id, uuid, name, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?)", (eid, name, NOW, NOW))
        conn.execute(
            "INSERT INTO trip_day (day_number, date) VALUES (1, '2026-04-01')")
        conn.execute(
            "INSERT INTO camera (camera_id) VALUES ('cam-A')")
        conn.execute(
            "INSERT INTO item (id, kind, provenance, origin_relpath, sha256, "
            "byte_size, materialized_at, materialized_phase, camera_id, "
            "day_number, capture_time_raw, capture_time_corrected, "
            "tz_offset_seconds, tz_source, extras_json, created_at) "
            "VALUES ('it-1', 'photo', 'captured', 'p.jpg', ?, 1, ?, 'ingest', "
            "'cam-A', 1, ?, ?, -21600, 'pair_picker', '{}', ?)",
            ("a" * 64, NOW, "2026-04-01T10:00:00", "2026-04-01T04:00:00", NOW))
    store.close()
    return root


def _register_event(gw, photos_base: Path, root: Path,
                    *, eid: str, name: str) -> None:
    from mira.gateway.index import make_entry
    gw.index.upsert(make_entry(
        event_id=eid, name=name,
        start_date=None, end_date=None, is_closed=False,
        event_root=root, photos_base_path=photos_base))


# ── _startup_reconcile_global_items ──────────────────────────────


def test_startup_reconcile_syncs_events_that_never_ran_close_hook(tmp_path):
    """The Phase 4b gap: events that landed without being mutated in
    this session keep stale ``global_items`` slices. ``main()`` now
    forces a reconcile at startup so the projection is current before
    the user reaches any cross-event surface.

    Build a fresh library with two events, register them in the
    index but never call ``open_event`` / ``mark_dirty``; the
    projection is empty. Call the helper; the projection is full."""
    from mira.ui.app import _startup_reconcile_global_items

    gw, photos_base = _make_umbrella(tmp_path)
    try:
        r1 = _seed_event(photos_base, name="Italy", eid="evt-italy")
        r2 = _seed_event(photos_base, name="Japan", eid="evt-japan")
        _register_event(gw, photos_base, r1,
                        eid="evt-italy", name="Italy")
        _register_event(gw, photos_base, r2,
                        eid="evt-japan", name="Japan")
        # Pre-reconcile: no projection rows exist yet.
        assert gw.user_store.all(um.GlobalItem) == []

        summary = _startup_reconcile_global_items(gw)

        assert summary["synced"] == 2
        rows = gw.user_store.all(um.GlobalItem)
        assert {r.event_uuid for r in rows} == {"evt-italy", "evt-japan"}
    finally:
        gw.close()


def test_startup_reconcile_returns_empty_on_failure(tmp_path, monkeypatch):
    """Failure is logged but NEVER blocks launch — the helper swallows
    the exception and returns an empty dict so the caller (``main()``)
    proceeds to ``window.show()``."""
    from mira.ui.app import _startup_reconcile_global_items

    class _BoomGateway:
        def reconcile_global_items(self):
            raise RuntimeError("simulated mid-reconcile crash")

    summary = _startup_reconcile_global_items(_BoomGateway())
    assert summary == {}


def test_startup_reconcile_noop_when_library_is_empty(tmp_path):
    """Empty library (no events) → reconcile syncs nothing, drops
    nothing, doesn't raise. The cross-event surface still works
    against the empty projection."""
    from mira.ui.app import _startup_reconcile_global_items
    gw, _ = _make_umbrella(tmp_path)
    try:
        summary = _startup_reconcile_global_items(gw)
        assert summary["synced"] == 0
        assert summary["dropped"] == 0
    finally:
        gw.close()


def test_startup_reconcile_skipped_in_read_only_session(tmp_path):
    """Read-only sessions don't own the writer lock; the
    LibraryGateway maintenance methods skip silently
    (``_skip_if_read_only`` — slice 5a). The startup helper must
    inherit that behaviour: it's called for both writeable and
    read-only sessions, and the read-only case returns the empty
    skip shape without raising or writing."""
    from mira.ui.app import _startup_reconcile_global_items
    from mira import session as mira_session
    from core.library_lock import LockInfo

    holder = LockInfo(
        hostname="other-host", pid=999, app_version="dev",
        acquired_at=NOW, heartbeat_at=NOW, mtime=0.0,
    )
    gw, photos_base = _make_umbrella(tmp_path)
    try:
        r1 = _seed_event(photos_base, name="Italy", eid="evt-italy")
        _register_event(gw, photos_base, r1,
                        eid="evt-italy", name="Italy")
        # Flip to read-only; the maintenance methods skip.
        mira_session.set_read_only(True, holder)
        try:
            summary = _startup_reconcile_global_items(gw)
            # Skip path returns the empty shape; no projection rows
            # were written by THIS session (the writer-half machine
            # owns the sync).
            assert summary == {"synced": 0, "dropped": 0, "skipped": []}
            assert gw.user_store.all(um.GlobalItem) == []
        finally:
            mira_session.reset_for_tests()
    finally:
        gw.close()
