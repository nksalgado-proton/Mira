"""Tests for ``Gateway.snapshot_event`` — spec/82 §A.1 per-day-add
+ manual-restore wiring.

The Part-A snapshot primitive itself has its own suite
(``test_db_backup.py``). This file pins the gateway-level helper
that every fixed trigger point now calls: a thin, never-raises
wrapper that resolves the event's db + backups dir and forwards
the ``reason`` tag.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core import db_backup
from core.db_backup import REASON_MILESTONE, REASON_PERIODIC
from mira.gateway.gateway import Gateway
from mira.gateway.index import EventsIndex, make_entry
from mira.settings.repo import SettingsRepo
from mira.store.repo import EventStore


NOW = "2026-06-17T00:00:00+00:00"


def _make_gw(tmp_path: Path):
    """Same shape as test_phase2_wiring._make_umbrella but tighter:
    settings + index + photos_base + user_store wired so
    ``snapshot_event`` can resolve a real event."""
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


def _seed_event(photos_base: Path, eid: str, name: str = "Test event") -> Path:
    root = photos_base / name
    root.mkdir(exist_ok=True)
    EventStore.create(
        root / "event.db",
        event_id=eid, app_version="test", created_at=NOW,
    ).close()
    return root


def _register(gw: Gateway, photos_base: Path, root: Path,
              eid: str, name: str) -> None:
    gw.index.upsert(make_entry(
        event_id=eid, name=name,
        start_date=None, end_date=None, is_closed=False,
        event_root=root, photos_base_path=photos_base,
    ))


# ── happy path ─────────────────────────────────────────────────────


def test_snapshot_event_creates_a_milestone_snapshot(tmp_path):
    """The default reason is milestone — same as every fixed trigger
    point (close-if-dirty, pre-risky-op, per-day-add)."""
    gw, photos_base = _make_gw(tmp_path)
    root = _seed_event(photos_base, "evt-1", "E1")
    _register(gw, photos_base, root, "evt-1", "E1")

    snap = gw.snapshot_event("evt-1")
    assert snap is not None
    assert snap.exists()
    snaps = db_backup.list_snapshots(gw.event_backups_dir("evt-1"))
    assert len(snaps) == 1
    assert snaps[0].reason == REASON_MILESTONE
    gw.close()


def test_snapshot_event_honours_periodic_reason(tmp_path):
    """The slice-3 timer will call this with ``reason="periodic"``;
    pin the round-trip through the sidecar."""
    gw, photos_base = _make_gw(tmp_path)
    root = _seed_event(photos_base, "evt-1")
    _register(gw, photos_base, root, "evt-1", "Test event")

    gw.snapshot_event("evt-1", reason=REASON_PERIODIC)
    snaps = db_backup.list_snapshots(gw.event_backups_dir("evt-1"))
    assert len(snaps) == 1
    assert snaps[0].reason == REASON_PERIODIC
    gw.close()


# ── never raises ───────────────────────────────────────────────────


def test_snapshot_event_unknown_event_returns_none(tmp_path):
    """A bad event id → ``None`` + no exception; the caller (post-
    ingest, periodic timer) is never interrupted by a backup miss."""
    gw, _ = _make_gw(tmp_path)
    assert gw.snapshot_event("does-not-exist") is None
    gw.close()


def test_snapshot_event_missing_db_returns_none(tmp_path):
    """Event registered but its db is gone (failed materialise, etc.)
    → ``None`` + no exception."""
    gw, photos_base = _make_gw(tmp_path)
    root = photos_base / "missing-db"
    root.mkdir()
    _register(gw, photos_base, root, "evt-x", "missing")
    assert gw.snapshot_event("evt-x") is None
    gw.close()


def test_snapshot_event_no_library_anchor_returns_none(tmp_path):
    """Pre-wizard / un-set photos_base → ``event_backups_dir`` is
    ``None``; helper short-circuits without trying to snapshot."""
    gw, _ = _make_gw(tmp_path)
    # Blow away the photos_base setting so the helper hits the
    # ``backups_dir is None`` branch.
    gw.settings.update(photos_base_path="")
    assert gw.snapshot_event("evt-anything") is None
    gw.close()
