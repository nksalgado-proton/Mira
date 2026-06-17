"""Tests for ``core.db_backup`` — spec/79 §7 pre-freeze backup
subset. No Qt; the module is pure filesystem + sqlite3.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core import db_backup
from core.db_backup import (
    CORRUPT_PREFIX,
    DEFAULT_KEEP_MILESTONE,
    DEFAULT_KEEP_PERIODIC,
    REASON_MILESTONE,
    REASON_PERIODIC,
    SIDECAR_SUFFIX,
    SnapshotInfo,
    VALID_REASONS,
    latest_snapshot,
    list_snapshots,
    quick_check,
    restore,
    snapshot,
    verify,
)


# ── Test fixtures ─────────────────────────────────────────────────


def _make_db(path: Path, *, rows: int = 3, schema_version: int = 6) -> None:
    """Build a small sqlite db with a schema_info table (mimicking
    the Mira convention) plus a payload table with ``rows`` rows."""
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript("""
            CREATE TABLE schema_info (
                id INTEGER PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                app_version TEXT NOT NULL
            );
            CREATE TABLE notes (
                id INTEGER PRIMARY KEY,
                body TEXT NOT NULL
            );
        """)
        conn.execute(
            "INSERT INTO schema_info (id, schema_version, app_version) "
            "VALUES (1, ?, ?)",
            (schema_version, "test"),
        )
        for i in range(rows):
            conn.execute("INSERT INTO notes (id, body) VALUES (?, ?)",
                         (i, f"row-{i}"))
        conn.commit()
    finally:
        conn.close()


def _row_count(db: Path) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    p = tmp_path / "event.db"
    _make_db(p)
    return p


@pytest.fixture
def backups(tmp_path: Path) -> Path:
    return tmp_path / "backups"


def _ts(seconds_from_epoch: int) -> datetime:
    """Deterministic timestamp for ordering snapshots in tests."""
    return datetime(2026, 6, 16, tzinfo=timezone.utc) + timedelta(
        seconds=seconds_from_epoch
    )


# ── snapshot — basic shape ────────────────────────────────────────


def test_snapshot_creates_db_and_sidecar(db, backups):
    out = snapshot(db, backups, app_version="1.2.3")
    assert out.exists()
    assert out.suffix == ".db"
    sidecar = out.with_suffix(".json")
    assert sidecar.exists()


def test_snapshot_sidecar_carries_metadata(db, backups):
    snap_path = snapshot(db, backups, app_version="1.2.3")
    sidecar = json.loads(
        snap_path.with_suffix(".json").read_text(encoding="utf-8"))
    assert sidecar["app_version"] == "1.2.3"
    assert sidecar["schema_version"] == 6           # from schema_info
    assert "sha256" in sidecar and len(sidecar["sha256"]) == 64
    assert "created_at" in sidecar


def test_snapshot_explicit_schema_version_overrides_probe(db, backups):
    """Caller-supplied schema_version wins over the probe."""
    snap_path = snapshot(db, backups, schema_version=99)
    sidecar = json.loads(
        snap_path.with_suffix(".json").read_text(encoding="utf-8"))
    assert sidecar["schema_version"] == 99


def test_snapshot_uses_atomic_write_no_tmp_left_behind(db, backups):
    snapshot(db, backups)
    leftovers = list(backups.glob("*.tmp"))
    assert leftovers == []


def test_snapshot_works_on_open_wal_database(tmp_path):
    """spec/79 §2 — the whole reason for the online backup API. A
    plain shutil.copy of an open WAL db is unsafe; src.backup(dst)
    is the safe path. We hold the source open while snapshotting
    and confirm the snapshot is a valid db with the same content."""
    src_path = tmp_path / "live.db"
    _make_db(src_path, rows=5)
    conn = sqlite3.connect(str(src_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("INSERT INTO notes (id, body) VALUES (99, 'live')")
    conn.commit()
    try:
        out = snapshot(src_path, tmp_path / "backups")
    finally:
        conn.close()
    assert _row_count(out) == 6                        # 5 + the live row


# ── snapshot — round-trip ─────────────────────────────────────────


def test_snapshot_then_restore_round_trips(db, backups, tmp_path):
    """snapshot → mutate the original → restore → original matches
    the snapshot again. The signature regression test for the
    spec/79 §5 path."""
    snap_path = snapshot(db, backups)
    before = _row_count(db)
    # Damage the original.
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DELETE FROM notes")
        conn.commit()
    finally:
        conn.close()
    assert _row_count(db) == 0

    info = latest_snapshot(backups)
    assert info is not None
    restore(info, db)
    assert _row_count(db) == before


# ── rotation ──────────────────────────────────────────────────────


def test_rotation_prunes_milestone_class_to_keep(db, backups):
    """Seven milestone snapshots with keep_milestone=5 → only the 5
    newest survive."""
    for i in range(7):
        snapshot(db, backups, reason=REASON_MILESTONE,
                 keep_milestone=5, created_at=_ts(i))
    remaining = list_snapshots(backups)
    assert len(remaining) == 5
    # The newest five (timestamps 6, 5, 4, 3, 2) should remain.
    expected = {_format_stem(_ts(i)) for i in range(2, 7)}
    actual = {info.db_path.stem for info in remaining}
    assert actual == expected


def test_rotation_drops_the_oldest_sidecar_too(db, backups):
    """Pruned snapshots take their sidecars with them — no orphans."""
    for i in range(3):
        snapshot(db, backups, reason=REASON_MILESTONE,
                 keep_milestone=2, created_at=_ts(i))
    db_files = sorted(backups.glob("*.db"))
    json_files = sorted(backups.glob("*.json"))
    assert len(db_files) == 2
    assert len(json_files) == 2
    # Sidecar stems match db stems exactly.
    assert {p.stem for p in db_files} == {p.stem for p in json_files}


def test_rotation_keep_minimum_one(db, backups):
    """keep_milestone=0 still keeps the newest (no point pruning to
    nothing)."""
    snapshot(db, backups, reason=REASON_MILESTONE,
             keep_milestone=0, created_at=_ts(0))
    snapshot(db, backups, reason=REASON_MILESTONE,
             keep_milestone=0, created_at=_ts(1))
    remaining = list_snapshots(backups)
    assert len(remaining) == 1


# ── verify / quick_check ──────────────────────────────────────────


def test_verify_passes_on_fresh_snapshot(db, backups):
    snapshot(db, backups)
    info = latest_snapshot(backups)
    assert info is not None
    assert verify(info) is True


def test_verify_fails_on_byte_corrupted_snapshot(db, backups):
    """spec/79 §6 — a deliberately-corrupted snapshot file fails
    verify (sha mismatch + quick_check failure)."""
    snap_path = snapshot(db, backups)
    # Splat bytes through the middle to break both the hash and the
    # sqlite header.
    raw = bytearray(snap_path.read_bytes())
    for i in range(64, min(256, len(raw))):
        raw[i] = 0xFF
    snap_path.write_bytes(bytes(raw))

    info = latest_snapshot(backups)
    assert info is not None
    assert verify(info) is False


def test_quick_check_passes_on_good_db(db):
    assert quick_check(db) is True


def test_quick_check_fails_on_garbage_file(tmp_path):
    p = tmp_path / "garbage.db"
    p.write_bytes(b"this is not a sqlite database, not at all")
    assert quick_check(p) is False


def test_quick_check_false_on_missing_file(tmp_path):
    assert quick_check(tmp_path / "does-not-exist.db") is False


# ── restore — safety net ──────────────────────────────────────────


def test_restore_backs_up_corrupt_original_first(db, backups, tmp_path):
    """spec/79 §5 — restore is itself reversible. The current
    (corrupt) file is moved to ``corrupt-<ts>.db`` before the
    snapshot is swapped in."""
    snapshot(db, backups)
    # Make the original obviously different so we can detect the
    # corrupt copy below by content.
    db.write_bytes(b"corrupt bytes that are not a sqlite db")
    info = latest_snapshot(backups)
    assert info is not None

    saved = restore(info, db, created_at=_ts(0))
    assert saved is not None
    assert saved.exists()
    assert saved.name.startswith(CORRUPT_PREFIX)
    assert saved.read_bytes() == b"corrupt bytes that are not a sqlite db"


def test_restore_returns_none_when_target_missing(db, backups, tmp_path):
    snapshot(db, backups)
    info = latest_snapshot(backups)
    assert info is not None
    fresh_target = tmp_path / "missing_event.db"
    saved = restore(info, fresh_target)
    assert saved is None
    assert fresh_target.exists()


def test_restore_refuses_unverified_snapshot(db, backups):
    """spec/79 §5 — we never restore a known-bad snapshot. A
    corrupted snapshot raises ValueError."""
    snap_path = snapshot(db, backups)
    # Wreck the snapshot bytes.
    snap_path.write_bytes(b"corrupted snapshot")
    info = latest_snapshot(backups)
    assert info is not None
    with pytest.raises(ValueError):
        restore(info, db)


def test_restore_preserves_snapshot_file(db, backups):
    """The snapshot itself stays put in the backups dir after a
    restore — restore copies, never moves the snapshot."""
    snap_path = snapshot(db, backups)
    info = latest_snapshot(backups)
    assert info is not None
    restore(info, db)
    assert snap_path.exists()
    # And the bytes still match the sidecar (still verifiable).
    assert verify(info) is True


# ── list_snapshots ────────────────────────────────────────────────


def test_list_snapshots_newest_first(db, backups):
    for i in range(3):
        snapshot(db, backups, created_at=_ts(i))
    snaps = list_snapshots(backups)
    assert len(snaps) == 3
    assert [s.db_path.stem for s in snaps] == [
        _format_stem(_ts(2)),
        _format_stem(_ts(1)),
        _format_stem(_ts(0)),
    ]


def test_list_snapshots_skips_db_without_sidecar(db, backups):
    snapshot(db, backups, created_at=_ts(0))
    # Drop an orphan .db that doesn't have a sidecar.
    orphan = backups / "20260616T120000000Z.db"
    orphan.write_bytes(b"not a real snapshot")
    snaps = list_snapshots(backups)
    assert len(snaps) == 1
    assert orphan not in {s.db_path for s in snaps}


def test_list_snapshots_skips_malformed_sidecar(db, backups):
    snap_path = snapshot(db, backups, created_at=_ts(0))
    # Corrupt the sidecar JSON.
    snap_path.with_suffix(".json").write_text("{not valid", encoding="utf-8")
    snaps = list_snapshots(backups)
    assert snaps == []


def test_list_snapshots_ignores_corrupt_backup_files(db, backups, tmp_path):
    """``corrupt-*.db`` files (written by :func:`restore`) live in
    the same dir but must NOT be returned by list_snapshots — they
    are damaged originals, not valid backups to restore from."""
    snapshot(db, backups, created_at=_ts(0))
    db.write_bytes(b"corrupt")
    info = latest_snapshot(backups)
    assert info is not None
    restore(info, db, created_at=_ts(1))
    snaps = list_snapshots(backups)
    # Only the one real snapshot — the corrupt-*.db is excluded.
    assert len(snaps) == 1


def test_list_snapshots_empty_when_dir_missing(tmp_path):
    assert list_snapshots(tmp_path / "no-such") == []


# ── module shape ──────────────────────────────────────────────────


def test_no_qt_imports_in_db_backup():
    """spec/79 §6 — pure logic + filesystem + sqlite, no Qt."""
    import core.db_backup as mod
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "PyQt6" not in src
    assert "QtCore" not in src


# ── spec/82 §A.2 — two-class retention ────────────────────────────


def test_snapshot_sidecar_records_reason(db, backups):
    """Every snapshot writes its class into the sidecar JSON so
    rotation can read it back later."""
    snap = snapshot(db, backups, reason=REASON_PERIODIC)
    sidecar = json.loads(
        snap.with_suffix(".json").read_text(encoding="utf-8"))
    assert sidecar["reason"] == REASON_PERIODIC


def test_snapshot_default_reason_is_milestone(db, backups):
    """Existing callers that don't pass ``reason`` keep getting the
    safer class — milestone — so the on-close + pre-risky-op call
    sites land in the long-retention bucket without modification."""
    snap = snapshot(db, backups)
    sidecar = json.loads(
        snap.with_suffix(".json").read_text(encoding="utf-8"))
    assert sidecar["reason"] == REASON_MILESTONE


def test_snapshot_rejects_unknown_reason(db, backups):
    """A typoed reason must fail loudly — silent fall-back would
    misclassify the snapshot for the rest of its life."""
    with pytest.raises(ValueError):
        snapshot(db, backups, reason="manual")          # not in VALID_REASONS


def test_list_snapshots_surfaces_reason(db, backups):
    snapshot(db, backups, reason=REASON_MILESTONE, created_at=_ts(0))
    snapshot(db, backups, reason=REASON_PERIODIC, created_at=_ts(1))
    snaps = list_snapshots(backups)
    by_reason = {s.created_at: s.reason for s in snaps}
    # Newest-first ordering is unchanged.
    assert snaps[0].reason == REASON_PERIODIC
    assert snaps[1].reason == REASON_MILESTONE
    assert set(by_reason.values()) == {REASON_MILESTONE, REASON_PERIODIC}


def test_legacy_sidecar_without_reason_defaults_to_milestone(db, backups):
    """A sidecar pre-spec/82 has no ``reason`` field. Reading it as
    milestone (the conservative default) keeps the older snapshot
    on the longer retention budget — never silently demoted to the
    short periodic budget where it'd be evicted by churn."""
    snapshot(db, backups, created_at=_ts(0))
    sidecar_path = next(backups.glob("*.json"))
    blob = json.loads(sidecar_path.read_text(encoding="utf-8"))
    blob.pop("reason", None)
    sidecar_path.write_text(json.dumps(blob), encoding="utf-8")
    snaps = list_snapshots(backups)
    assert len(snaps) == 1
    assert snaps[0].reason == REASON_MILESTONE


def test_legacy_sidecar_with_garbage_reason_defaults_to_milestone(db, backups):
    """An unknown ``reason`` value (typo, future class, hand-edit)
    falls back to milestone — same conservative rule as a missing
    field."""
    snapshot(db, backups, created_at=_ts(0))
    sidecar_path = next(backups.glob("*.json"))
    blob = json.loads(sidecar_path.read_text(encoding="utf-8"))
    blob["reason"] = "scheduled-by-hand"
    sidecar_path.write_text(json.dumps(blob), encoding="utf-8")
    snaps = list_snapshots(backups)
    assert snaps[0].reason == REASON_MILESTONE


def test_periodic_churn_does_not_evict_milestone(db, backups):
    """spec/82 §A.2 — the reason the two-class split exists. A flood
    of periodic snapshots must never push out the durable milestone
    snapshots."""
    # 1 milestone at t=0.
    snapshot(db, backups, reason=REASON_MILESTONE,
             keep_milestone=2, keep_periodic=3, created_at=_ts(0))
    # 20 periodic snapshots after it.
    for i in range(1, 21):
        snapshot(db, backups, reason=REASON_PERIODIC,
                 keep_milestone=2, keep_periodic=3, created_at=_ts(i))
    snaps = list_snapshots(backups)
    milestones = [s for s in snaps if s.reason == REASON_MILESTONE]
    periodics = [s for s in snaps if s.reason == REASON_PERIODIC]
    # The single old milestone survived despite 20 periodics churning
    # through; periodic class is capped at 3.
    assert len(milestones) == 1
    assert milestones[0].db_path.stem == _format_stem(_ts(0))
    assert len(periodics) == 3
    # The 3 surviving periodics are the newest three (t=20, 19, 18).
    assert {p.db_path.stem for p in periodics} == {
        _format_stem(_ts(i)) for i in (18, 19, 20)
    }


def test_milestone_class_prunes_independently(db, backups):
    """A flood of milestones doesn't evict periodics, either —
    classes are symmetric in independence."""
    snapshot(db, backups, reason=REASON_PERIODIC,
             keep_milestone=3, keep_periodic=2, created_at=_ts(0))
    for i in range(1, 11):
        snapshot(db, backups, reason=REASON_MILESTONE,
                 keep_milestone=3, keep_periodic=2, created_at=_ts(i))
    snaps = list_snapshots(backups)
    milestones = [s for s in snaps if s.reason == REASON_MILESTONE]
    periodics = [s for s in snaps if s.reason == REASON_PERIODIC]
    assert len(milestones) == 3
    assert len(periodics) == 1
    assert periodics[0].db_path.stem == _format_stem(_ts(0))


def test_default_retention_limits_match_spec(db, backups):
    """Spec/82 §A.2: defaults are 10 milestones, 3 periodics."""
    assert DEFAULT_KEEP_MILESTONE == 10
    assert DEFAULT_KEEP_PERIODIC == 3
    assert set(VALID_REASONS) == {REASON_MILESTONE, REASON_PERIODIC}


# ── helpers ───────────────────────────────────────────────────────


def _format_stem(when: datetime) -> str:
    """Mirror of the module's filename convention, used in
    assertions about which timestamps survived rotation."""
    return when.strftime("%Y%m%dT%H%M%S") + f"{when.microsecond // 1000:03d}Z"
