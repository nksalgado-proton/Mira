"""Tests for the user-store protection layer — spec/53 §3.1.

Three layers in one place: SHA-256 sidecar verify-on-open + recompute-on-close,
rolling backups (``.bak.1`` newest), and ``PRAGMA integrity_check`` plumbing.

The protection module itself is logic-only and is exercised directly; the
:class:`UserStore` lifecycle that drives it is exercised by reading the
file-state effects (sidecar present, backup count + ordering, log warnings).
"""
from __future__ import annotations

import logging
import sqlite3

import pytest

from mira import protect as _protect
from mira.user_store import protection, schema
from mira.user_store.repo import UserStore


def _read_file_sha256_via_protect(path):
    return _protect._read_file_sha256(path)  # noqa: SLF001 — module-internal helper


def _make_store(tmp_path) -> UserStore:
    return UserStore.create(
        tmp_path / "mira.db",
        app_version="test",
        created_at="2026-06-08T00:00:00+00:00",
    )


# --------------------------------------------------------------------------- #
# Sidecar verify
# --------------------------------------------------------------------------- #


def test_verify_sidecar_missing_is_not_corruption(tmp_path):
    """A brand-new DB has no sidecar yet — verify should return ``ok=True,
    sidecar_missing=True`` so the open path doesn't treat a fresh file as
    corrupt."""
    store = _make_store(tmp_path)
    try:
        outcome = protection.verify_sidecar(store.path)
        assert outcome.ok is True
        assert outcome.sidecar_missing is True
    finally:
        store.close()


def test_recompute_sidecar_writes_standard_sha256sum_format(tmp_path):
    store = _make_store(tmp_path)
    try:
        sha = protection.recompute_sidecar(store.path)
        sidecar = store.path.with_suffix(store.path.suffix + ".sha256")
        assert sidecar.is_file()
        line = sidecar.read_text(encoding="utf-8").strip()
        # ``<sha>  <basename>`` per the standard sha256sum format.
        assert line.endswith(store.path.name)
        assert line.startswith(sha)
    finally:
        store.close()


def test_close_writes_sidecar(tmp_path):
    """The clean-close path recomputes the sidecar so a subsequent open
    verifies cleanly."""
    store = _make_store(tmp_path)
    store.close()                                # this is what the test exercises
    sidecar = (tmp_path / "mira.db").with_suffix(".db.sha256")
    assert sidecar.is_file()

    # And a fresh open verifies (sidecar present + matching).
    outcome = protection.verify_sidecar(tmp_path / "mira.db")
    assert outcome.ok is True
    assert outcome.sidecar_missing is False


def test_open_logs_warning_on_sidecar_mismatch(tmp_path, caplog):
    """Tampering with the DB bytes between sessions trips the sidecar warning
    — visible to the user but not blocking (per spec/53 §3.1)."""
    store = _make_store(tmp_path)
    store.close()                                # writes a valid sidecar
    db_path = tmp_path / "mira.db"

    # Simulate external edit: append a stray byte (without going through
    # SQLite). Real-world equivalent: user opened the file in a text editor.
    with open(db_path, "ab") as f:
        f.write(b"\x00")

    with caplog.at_level(logging.WARNING):
        store2 = UserStore.open(db_path)
    try:
        # Open succeeded (warning, not failure) — confirm the connection is alive.
        assert schema.get_version(store2.conn) == schema.SCHEMA_VERSION
        assert any(
            "sidecar mismatch" in record.message
            for record in caplog.records
        )
    finally:
        store2.close()


def test_open_warns_on_integrity_check_failure(tmp_path, caplog):
    """If ``PRAGMA integrity_check`` returns something other than ``'ok'``,
    the open path logs a visible warning. We synthesise the failure by
    monkeypatching the helper, since corrupting a SQLite file deterministically
    is fragile."""
    store = _make_store(tmp_path)
    store.close()
    db_path = tmp_path / "mira.db"

    original_integrity_check = schema.integrity_check
    try:
        schema.integrity_check = lambda conn: "*** in database main ***"  # type: ignore[assignment]
        with caplog.at_level(logging.WARNING):
            store2 = UserStore.open(db_path)
        try:
            assert any(
                "integrity_check returned" in record.message
                for record in caplog.records
            )
        finally:
            store2.close()
    finally:
        schema.integrity_check = original_integrity_check  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# Rolling backups
# --------------------------------------------------------------------------- #


def test_roll_backup_creates_bak_1_on_first_call(tmp_path):
    store = _make_store(tmp_path)
    try:
        bak1 = protection.roll_backup(store.path)
        assert bak1 is not None
        assert bak1 == store.path.with_suffix(".db.bak.1")
        assert bak1.is_file()
        # No older slots populated yet.
        assert not store.path.with_suffix(".db.bak.2").exists()
    finally:
        store.close()


def test_roll_backup_rotates_newest_first(tmp_path):
    """Successive rolls shift everything one slot older — ``.bak.1`` is
    always the freshest copy."""
    store = _make_store(tmp_path)
    try:
        # First roll: .bak.1 only.
        protection.roll_backup(store.path)
        # Tag the file so we can identify which slot it ends up in.
        first_sha = protection.recompute_sidecar(store.path)
        # Mutate the live DB (add a setting row, recompute sidecar).
        from mira.user_store import models as m
        store.upsert(m.Setting(
            key="theme", value_json='"light"',
            updated_at="2026-06-08T01:00:00+00:00",
        ))
        store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        second_sha = protection.recompute_sidecar(store.path)
        assert second_sha != first_sha, "DB content must differ between rolls"

        # Second roll: live → .bak.1, previous .bak.1 → .bak.2.
        protection.roll_backup(store.path)
        bak1_sha = _read_file_sha256_via_protect(store.path.with_suffix(".db.bak.1"))  # noqa: SLF001
        bak2_sha = _read_file_sha256_via_protect(store.path.with_suffix(".db.bak.2"))  # noqa: SLF001
        assert bak1_sha == second_sha           # freshest copy
        assert bak2_sha == first_sha            # older
    finally:
        store.close()


def test_roll_backup_drops_oldest_at_max(tmp_path):
    """After ``MAX_ROLLING_BACKUPS`` rolls, the oldest is dropped; the live
    DB is always preserved across the rotation."""
    store = _make_store(tmp_path)
    try:
        # Roll once more than the max to force the oldest off the end.
        for _ in range(protection.MAX_ROLLING_BACKUPS + 1):
            protection.roll_backup(store.path)

        # Every slot from .bak.1 to .bak.MAX exists; .bak.<MAX+1> does NOT.
        for i in range(1, protection.MAX_ROLLING_BACKUPS + 1):
            assert store.path.with_suffix(f".db.bak.{i}").is_file()
        assert not store.path.with_suffix(
            f".db.bak.{protection.MAX_ROLLING_BACKUPS + 1}"
        ).exists()
    finally:
        store.close()


def test_list_backups_returns_newest_first(tmp_path):
    store = _make_store(tmp_path)
    try:
        protection.roll_backup(store.path)
        protection.roll_backup(store.path)
        out = protection.list_backups(store.path)
        assert [b.name for b in out] == [
            "mira.db.bak.1", "mira.db.bak.2",
        ]
    finally:
        store.close()


def test_close_rolls_a_backup(tmp_path):
    """The clean-close path produces a backup so a subsequent corruption has
    a restore point."""
    store = _make_store(tmp_path)
    store.close()
    assert (tmp_path / "mira.db.bak.1").is_file()


def test_roll_backup_on_missing_file_returns_none(tmp_path):
    """Defensive — the create path may call close() on a file that wasn't
    actually written yet (rare; e.g., disk-full mid-create). roll_backup
    must not crash; it returns None."""
    missing = tmp_path / "never_written.db"
    assert protection.roll_backup(missing) is None


# --------------------------------------------------------------------------- #
# integrity_check helper (schema.py)
# --------------------------------------------------------------------------- #


def test_integrity_check_ok_on_fresh_db(tmp_path):
    store = _make_store(tmp_path)
    try:
        assert schema.integrity_check(store.conn) == "ok"
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# End-to-end lifecycle: open → mutate → close → reopen
# --------------------------------------------------------------------------- #


def test_open_close_open_roundtrip_preserves_state_and_sidecar(tmp_path):
    """A full lifecycle: create + write a setting + close → reopen + verify
    the setting survives AND the sidecar verifies cleanly."""
    from mira.user_store import models as m

    NOW = "2026-06-08T00:00:00+00:00"

    store = _make_store(tmp_path)
    store.upsert(m.Setting(key="theme", value_json='"dark"', updated_at=NOW))
    store.close()

    # Sidecar is present and matches.
    outcome = protection.verify_sidecar(tmp_path / "mira.db")
    assert outcome.ok is True and outcome.sidecar_missing is False
    # Backup .bak.1 was produced (from the close path).
    assert (tmp_path / "mira.db.bak.1").is_file()

    # Reopen and check that the setting survives.
    store2 = UserStore.open(tmp_path / "mira.db")
    try:
        got = store2.get(m.Setting, "theme")
        assert got is not None and got.value_json == '"dark"'
    finally:
        store2.close()
