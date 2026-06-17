"""Tests for ``core.event_bundle`` export half — spec/82 Part B.

The bundle is the user-driven event-migration artefact: a
self-contained directory copy of the event tree with the live db
swapped for a consistent online-backup snapshot and every file
hashed into a top-level ``mira-event.json`` manifest. ``.partial`` →
``os.replace`` finalises atomically so an interrupted copy is never
mistaken for a complete bundle.

Pure logic + filesystem — no Qt; the suite spins event trees in
``tmp_path`` and inspects the resulting bundle dir on disk.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core import event_bundle
from core.event_bundle import (
    MANIFEST_FILENAME,
    MANIFEST_VERSION,
    PARTIAL_SUFFIX,
    BundleManifest,
    ManifestFile,
    VerifyResult,
    export_event,
    read_manifest,
    verify_bundle,
)


NOW = datetime(2026, 6, 17, 9, 30, tzinfo=timezone.utc)


# ── Fixture: a plausible event tree ───────────────────────────────


def _seed_event_tree(root: Path) -> Path:
    """Build the spec/57 skeleton + one item in each tier + a
    populated event.db. Mirrors the shape ``export_event`` would
    actually see in production."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "Original Media" / "_cameras").mkdir(parents=True)
    (root / "Original Media" / "_phones").mkdir()
    (root / "Edited Media").mkdir()
    (root / "Exported Media").mkdir()
    (root / "Cuts").mkdir()
    (root / ".cache").mkdir()
    # A handful of media files of varying sizes — enough to make the
    # manifest's per-file SHA-256 list cover multiple rows.
    (root / "Original Media" / "_cameras" / "IMG_0001.jpg").write_bytes(
        b"\xff\xd8\xff\xd9" + b"\x00" * 1024)
    (root / "Edited Media" / "IMG_0001.tif").write_bytes(
        b"II*\x00" + b"\x00" * 2048)
    (root / "Exported Media" / "IMG_0001.jpg").write_bytes(
        b"\xff\xd8\xff\xd9" + b"\x42" * 4096)
    (root / ".cache" / "thumb-0001.webp").write_bytes(b"RIFF\x00" * 16)
    (root / "Cuts" / "cut-1.json").write_text(
        '{"name":"cut-1"}', encoding="utf-8")
    # A minimal event.db with a real ``event`` row so the manifest
    # carries an identity.
    db_path = root / "event.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript("""
            CREATE TABLE schema_info (
                id INTEGER PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                app_version TEXT NOT NULL
            );
            CREATE TABLE event (
                id INTEGER PRIMARY KEY,
                uuid TEXT NOT NULL,
                name TEXT NOT NULL
            );
        """)
        conn.execute(
            "INSERT INTO schema_info (id, schema_version, app_version) "
            "VALUES (1, ?, ?)", (7, "test"))
        conn.execute(
            "INSERT INTO event (id, uuid, name) VALUES (1, ?, ?)",
            ("evt-uuid-1234", "TestEvent"))
        conn.commit()
    finally:
        conn.close()
    return db_path


# ── export_event happy path ───────────────────────────────────────


def test_export_creates_bundle_directory_and_manifest(tmp_path):
    """Round-trip the smallest export: a fresh event tree out into
    a bundle. The bundle directory exists, the manifest exists, no
    .partial leftover."""
    src = tmp_path / "TestEvent"
    db_path = _seed_event_tree(src)
    dest = tmp_path / "bundles"

    result = export_event(
        src, db_path, dest,
        app_version="1.2.3", created_at=NOW)

    assert result.bundle_dir == dest / "TestEvent"
    assert result.bundle_dir.is_dir()
    assert (result.bundle_dir / MANIFEST_FILENAME).exists()
    assert not (dest / f"TestEvent{PARTIAL_SUFFIX}").exists()


def test_export_copies_every_source_file_verbatim(tmp_path):
    """The bundle contains every file the source did, byte-equal
    except for event.db (which is the snapshot, not the live bytes —
    tested separately)."""
    src = tmp_path / "TestEvent"
    _seed_event_tree(src)
    dest = tmp_path / "bundles"

    export_event(src, src / "event.db", dest, created_at=NOW)
    bundle = dest / "TestEvent"

    for src_path in src.rglob("*"):
        if not src_path.is_file():
            continue
        rel = src_path.relative_to(src)
        dst_path = bundle / rel
        assert dst_path.exists(), f"missing {rel} in bundle"
        if rel.as_posix() == "event.db":
            continue                                       # snapshot, not verbatim
        assert dst_path.read_bytes() == src_path.read_bytes()


def test_export_uses_a_snapshot_for_event_db_not_the_live_file(tmp_path):
    """The bundled event.db is a SQLite online-backup snapshot, not
    a raw shutil.copy of the WAL state. Test: hold the source open
    in WAL mode + write a row before export starts; the bundle
    should still carry a consistent db that quick_check passes."""
    src = tmp_path / "TestEvent"
    _seed_event_tree(src)
    # Hold the source open in WAL mode + write a row mid-flight.
    conn = sqlite3.connect(str(src / "event.db"))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "INSERT INTO event (id, uuid, name) VALUES (2, ?, ?)",
            ("evt-uuid-2", "Second event row"))
        conn.commit()
        export_event(
            src, src / "event.db", tmp_path / "bundles",
            created_at=NOW)
    finally:
        conn.close()
    bundle_db = tmp_path / "bundles" / "TestEvent" / "event.db"
    # Bundle's db is a valid sqlite file with the row we wrote
    # before export (the row count proves the snapshot caught
    # the live WAL state, not stale bytes).
    conn2 = sqlite3.connect(str(bundle_db))
    try:
        cnt = conn2.execute("SELECT COUNT(*) FROM event").fetchone()[0]
        assert cnt == 2
    finally:
        conn2.close()


def test_manifest_carries_required_fields(tmp_path):
    """spec/82 §B.2 step 4 — identity + version + counts + the per-
    file SHA-256 list."""
    src = tmp_path / "TestEvent"
    db_path = _seed_event_tree(src)
    dest = tmp_path / "bundles"
    export_event(src, db_path, dest, app_version="2.0.0", created_at=NOW)

    manifest = read_manifest(dest / "TestEvent")
    assert manifest.manifest_version == MANIFEST_VERSION
    assert manifest.event_uuid == "evt-uuid-1234"
    assert manifest.event_name == "TestEvent"
    assert manifest.app_version == "2.0.0"
    assert manifest.schema_version == 7
    assert manifest.total_file_count == len(manifest.files)
    assert manifest.total_bytes == sum(f.byte_size for f in manifest.files)
    # The per-file SHA list covers every regular file under the bundle.
    relpaths = {f.relpath for f in manifest.files}
    assert "event.db" in relpaths
    assert "Original Media/_cameras/IMG_0001.jpg" in relpaths
    assert "Cuts/cut-1.json" in relpaths


def test_manifest_relpaths_are_posix_style(tmp_path):
    """A Windows-side export must produce a manifest a Linux-side
    importer reads cleanly — POSIX forward-slashes everywhere."""
    src = tmp_path / "TestEvent"
    _seed_event_tree(src)
    export_event(src, src / "event.db", tmp_path / "bundles",
                 created_at=NOW)
    manifest = read_manifest(tmp_path / "bundles" / "TestEvent")
    for f in manifest.files:
        assert "\\" not in f.relpath, (
            f"backslash in manifest relpath {f.relpath!r}")


# ── verify_bundle ─────────────────────────────────────────────────


def test_verify_passes_on_a_fresh_export(tmp_path):
    """The export's own internal verify_after_copy already ran
    successfully; an independent verify call against the finalised
    bundle should also pass."""
    src = tmp_path / "TestEvent"
    _seed_event_tree(src)
    export_event(src, src / "event.db", tmp_path / "bundles",
                 created_at=NOW)
    result = verify_bundle(tmp_path / "bundles" / "TestEvent")
    assert result.ok is True
    assert result.missing == []
    assert result.mismatch == []


def test_verify_flags_a_missing_file(tmp_path):
    """Drop a file from the finalised bundle → verify surfaces it
    as missing without raising."""
    src = tmp_path / "TestEvent"
    _seed_event_tree(src)
    export_event(src, src / "event.db", tmp_path / "bundles",
                 created_at=NOW)
    victim = tmp_path / "bundles" / "TestEvent" / "Cuts" / "cut-1.json"
    victim.unlink()
    result = verify_bundle(tmp_path / "bundles" / "TestEvent")
    assert result.ok is False
    assert "Cuts/cut-1.json" in result.missing


def test_verify_flags_a_byte_corrupted_file(tmp_path):
    """Splat random bytes through a bundled file → verify catches
    the SHA mismatch."""
    src = tmp_path / "TestEvent"
    _seed_event_tree(src)
    export_event(src, src / "event.db", tmp_path / "bundles",
                 created_at=NOW)
    victim = (
        tmp_path / "bundles" / "TestEvent"
        / "Original Media" / "_cameras" / "IMG_0001.jpg")
    victim.write_bytes(b"\x00\x00\x00\x00")
    result = verify_bundle(tmp_path / "bundles" / "TestEvent")
    assert result.ok is False
    assert "Original Media/_cameras/IMG_0001.jpg" in result.mismatch


def test_verify_fails_when_manifest_missing(tmp_path):
    """A directory that isn't a Mira bundle should verify-fail
    cleanly with an error message rather than raising."""
    bogus = tmp_path / "not-a-bundle"
    bogus.mkdir()
    (bogus / "random.txt").write_bytes(b"hi")
    result = verify_bundle(bogus)
    assert result.ok is False
    assert "missing" in (result.error or "").lower()


def test_verify_catches_corrupt_bundled_db(tmp_path):
    """Manifest can be clean while the db's page bytes are bad. The
    quick_check inside verify catches that."""
    src = tmp_path / "TestEvent"
    _seed_event_tree(src)
    export_event(src, src / "event.db", tmp_path / "bundles",
                 created_at=NOW)
    bundle = tmp_path / "bundles" / "TestEvent"
    bundle_db = bundle / "event.db"
    # Overwrite the db file with garbage AND update the manifest's
    # SHA + size so the manifest verify passes but quick_check fails.
    bundle_db.write_bytes(b"this is not a sqlite database, not at all")
    manifest = read_manifest(bundle)
    import hashlib
    new_sha = hashlib.sha256(bundle_db.read_bytes()).hexdigest()
    new_size = bundle_db.stat().st_size
    fixed_files = [
        ManifestFile(
            relpath="event.db",
            sha256=new_sha, byte_size=new_size)
        if f.relpath == "event.db" else f
        for f in manifest.files
    ]
    fixed = BundleManifest(
        manifest_version=manifest.manifest_version,
        event_uuid=manifest.event_uuid,
        event_name=manifest.event_name,
        app_version=manifest.app_version,
        schema_version=manifest.schema_version,
        created_at=manifest.created_at,
        total_file_count=manifest.total_file_count,
        total_bytes=manifest.total_bytes,
        files=fixed_files,
    )
    (bundle / MANIFEST_FILENAME).write_text(
        fixed.to_json(), encoding="utf-8")
    result = verify_bundle(bundle)
    assert result.ok is False
    assert "quick_check" in (result.error or "")


# ── Atomic finalisation ───────────────────────────────────────────


def test_export_refuses_to_overwrite_existing_bundle(tmp_path):
    """A second export with the same name into the same dest must
    refuse rather than silently overwrite a previous bundle."""
    src = tmp_path / "TestEvent"
    _seed_event_tree(src)
    dest = tmp_path / "bundles"
    export_event(src, src / "event.db", dest, created_at=NOW)
    with pytest.raises(FileExistsError):
        export_event(src, src / "event.db", dest, created_at=NOW)


def test_export_cleans_a_stale_partial_dir(tmp_path):
    """If a previous export crashed mid-flight and left
    ``<event>.partial/``, the next export wipes it first rather
    than building on top."""
    src = tmp_path / "TestEvent"
    _seed_event_tree(src)
    dest = tmp_path / "bundles"
    dest.mkdir()
    stale = dest / f"TestEvent{PARTIAL_SUFFIX}"
    stale.mkdir()
    (stale / "trash.bin").write_bytes(b"\x00" * 100)
    export_event(src, src / "event.db", dest, created_at=NOW)
    # .partial gone, real bundle in place.
    assert not stale.exists()
    assert (dest / "TestEvent").is_dir()


# ── Module shape ──────────────────────────────────────────────────


def test_no_qt_imports_in_event_bundle():
    """spec/82 — core/event_bundle.py is Qt-free."""
    import core.event_bundle as mod
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "PyQt6" not in src
    assert "QtCore" not in src
