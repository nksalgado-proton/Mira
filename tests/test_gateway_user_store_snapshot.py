"""Tests for ``Gateway.snapshot_user_store`` + the pre-migration
hook — spec/82 §A.3.

The user-data store gets the same ``db_backup`` snapshot primitive
as the event dbs. This file pins the gateway-level wiring: the
backups dir resolves to ``<library_root>/.mira-backups/user-store``,
``snapshot_user_store`` honours the dirty gate at app close, and
the pre-migration probe takes a milestone snapshot when a v3 → v4
upgrade is about to run.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core import db_backup
from core.db_backup import REASON_MILESTONE
from mira.gateway.gateway import Gateway, BACKUPS_DIR_NAME
from mira.gateway.index import EventsIndex
from mira.settings.repo import SettingsRepo


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


# ── user_store_backups_dir ────────────────────────────────────────


def test_user_store_backups_dir_lands_under_library_root(tmp_path):
    """spec/82 §A.3 — ``<library_root>/.mira-backups/user-store``."""
    gw, photos_base = _make_gw(tmp_path)
    assert gw.user_store_backups_dir() == (
        photos_base / BACKUPS_DIR_NAME / "user-store")
    gw.close()


def test_user_store_backups_dir_none_without_library_anchor(tmp_path):
    """Pre-wizard / no photos_base_path → no backups location yet."""
    gw, _ = _make_gw(tmp_path)
    gw.settings.update(photos_base_path="")
    assert gw.user_store_backups_dir() is None
    gw.close()


# ── snapshot_user_store: manual path ──────────────────────────────


def test_snapshot_user_store_creates_a_milestone_snapshot(tmp_path):
    """The manual / pre-risky-op path doesn't gate on dirty — always
    lays one down so the user can deliberately roll back even from a
    quiet session."""
    gw, _ = _make_gw(tmp_path)
    snap = gw.snapshot_user_store(reason=REASON_MILESTONE)
    assert snap is not None
    snaps = db_backup.list_snapshots(gw.user_store_backups_dir())
    assert len(snaps) == 1
    assert snaps[0].reason == REASON_MILESTONE
    gw.close()


def test_snapshot_user_store_none_when_no_anchor(tmp_path):
    """No library root → no backups location → snapshot returns
    ``None`` instead of raising."""
    gw, _ = _make_gw(tmp_path)
    gw.settings.update(photos_base_path="")
    assert gw.snapshot_user_store() is None
    gw.close()


# ── snapshot_user_store: only_if_dirty (the app-close path) ───────


def test_only_if_dirty_skips_when_no_writes_since_open(tmp_path):
    """A read-only session (open the user_store, browse, never
    write) shouldn't lay down a redundant snapshot at quit."""
    gw, _ = _make_gw(tmp_path)
    # ``_make_gw`` triggers user_store first-access + the importer
    # write path. To get a clean baseline, take a snapshot now (the
    # `<=` dirty check will then short-circuit on the next call).
    gw.snapshot_user_store()
    # Re-arm baseline at the current total_changes — the importer
    # writes from gw construction shouldn't count toward this test.
    gw._user_store_changes_at_open = gw.user_store.conn.total_changes
    before = len(db_backup.list_snapshots(gw.user_store_backups_dir()))
    assert gw.snapshot_user_store(only_if_dirty=True) is None
    after = len(db_backup.list_snapshots(gw.user_store_backups_dir()))
    assert before == after
    gw.close()


def test_only_if_dirty_snapshots_when_writes_landed(tmp_path):
    """The standard close path: the session DID write something to
    the user_store connection → a fresh snapshot lands."""
    from mira.user_store import models as um
    gw, _ = _make_gw(tmp_path)
    # Re-arm baseline so the importer's setup writes don't count.
    gw._user_store_changes_at_open = gw.user_store.conn.total_changes
    # Force a real write through the user_store connection (the one
    # the dirty check reads ``total_changes`` from). A Setting upsert
    # is the smallest thing that goes through the store.
    gw.user_store.upsert(um.Setting(
        key="theme", value_json='"dark"', updated_at=NOW))
    snap = gw.snapshot_user_store(only_if_dirty=True)
    assert snap is not None
    snaps = db_backup.list_snapshots(gw.user_store_backups_dir())
    assert len(snaps) == 1
    gw.close()


# ── pre-migration snapshot probe ──────────────────────────────────


def test_pre_migration_probe_snapshots_when_schema_behind(
    tmp_path, monkeypatch,
):
    """spec/82 §A.3 — when ``mira.db`` is at an older schema than
    this build understands, a milestone snapshot lands BEFORE the
    migrate() runs. The user can roll back if the migration breaks
    something.

    Rather than hand-rolling a real older mira.db (drop-then-add of
    every spec/53 / spec/81 table the migrations have ever added),
    monkeypatch the build's TARGET schema version higher. The
    pre-migration probe in ``Gateway.user_store`` reads the live
    target via the same import, so a higher target makes the
    on-disk file look "behind" without rewriting it.
    """
    settings_path = tmp_path / "settings.json"
    index_path = tmp_path / "events_index.json"
    user_store_path = tmp_path / "mira.db"
    photos_base = tmp_path / "photos"
    photos_base.mkdir(exist_ok=True)

    # 1. Build a populated user-store at the current schema, settings
    #    point at photos_base, close.
    gw0 = Gateway(
        settings=SettingsRepo(settings_path),
        index=EventsIndex(index_path),
        user_store_path=user_store_path, now=lambda: NOW,
        installation_profile="XMC",
    )
    _ = gw0.user_store
    gw0.settings.update(photos_base_path=str(photos_base))
    gw0.close()

    # 2. Patch the BUILD's target schema to one higher than what's
    #    on disk so the probe sees a pending migration. We don't
    #    actually run migrate() — ``UserStore.open`` will, and
    #    will raise because there's no v5 step. We catch that and
    #    only assert the pre-migration snapshot landed first.
    import mira.user_store.schema as us_schema
    monkeypatch.setattr(us_schema, "SCHEMA_VERSION", us_schema.SCHEMA_VERSION + 1)

    gw = Gateway(
        settings=SettingsRepo(settings_path),
        index=EventsIndex(index_path),
        user_store_path=user_store_path, now=lambda: NOW,
        installation_profile="XMC",
    )
    # The actual migration will IndexError (no step for the patched
    # target) — we only care that the snapshot ran first.
    with pytest.raises(IndexError):
        _ = gw.user_store

    snaps = db_backup.list_snapshots(
        photos_base / BACKUPS_DIR_NAME / "user-store")
    assert len(snaps) == 1, (
        "expected a pre-migration milestone snapshot to land before "
        "UserStore.open() ran migrate()")
    assert snaps[0].reason == REASON_MILESTONE
    gw.close()


def test_pre_migration_probe_skips_when_schema_already_current(tmp_path):
    """No version mismatch → no extra snapshot, just the normal open."""
    gw, _ = _make_gw(tmp_path)
    snaps = db_backup.list_snapshots(gw.user_store_backups_dir())
    # _make_gw opens the user_store + library_root is set, but no
    # migration is pending (fresh-create path stamps the current
    # SCHEMA_VERSION), so the pre-migration probe shouldn't run.
    assert snaps == []
    gw.close()
