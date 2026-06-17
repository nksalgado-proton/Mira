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


# ── inspect_bundle (slice 6) ──────────────────────────────────────


def _export_bundle(tmp_path: Path) -> Path:
    """Helper: build a fresh event tree and export it. Returns the
    bundle's final directory."""
    src = tmp_path / "RoundTripEvent"
    _seed_event_tree(src)
    dest = tmp_path / "bundles"
    export_event(src, src / "event.db", dest, created_at=NOW,
                 app_version="3.0.0")
    return dest / "RoundTripEvent"


def test_inspect_passes_on_a_fresh_export(tmp_path):
    """Round-trip: a freshly-exported bundle inspects clean against
    its own schema version. version_status == "ok"; can_proceed."""
    bundle = _export_bundle(tmp_path)
    plan = event_bundle.inspect_bundle(bundle, target_schema_version=7)
    assert plan.integrity.ok is True
    assert plan.version_status == event_bundle.VERSION_OK
    assert plan.can_proceed is True
    assert plan.manifest.event_uuid == "evt-uuid-1234"


def test_inspect_flags_schema_newer_than_local(tmp_path):
    """spec/82 §B.3 step 3 — refuse a bundle whose schema is ahead
    of this build."""
    bundle = _export_bundle(tmp_path)
    plan = event_bundle.inspect_bundle(
        bundle, target_schema_version=5)   # bundle is 7, local is 5
    assert plan.version_status == event_bundle.VERSION_NEWER_THAN_LOCAL
    assert plan.can_proceed is False


def test_inspect_classifies_older_schema_as_installable(tmp_path):
    """An older bundle can be copied in; the next open's migration
    catches it up. Verify status reflects this without blocking."""
    bundle = _export_bundle(tmp_path)
    plan = event_bundle.inspect_bundle(
        bundle, target_schema_version=10)  # bundle is 7, local is 10
    assert plan.version_status == event_bundle.VERSION_OLDER_THAN_LOCAL
    assert plan.can_proceed is True


def test_inspect_rejects_a_tampered_file(tmp_path):
    """spec/82 §B.3 step 2 — integrity gate. Splat bytes through a
    bundled media file → can_proceed is False."""
    bundle = _export_bundle(tmp_path)
    victim = (
        bundle / "Original Media" / "_cameras" / "IMG_0001.jpg")
    victim.write_bytes(b"\x00\x00\x00\x00")
    plan = event_bundle.inspect_bundle(bundle, target_schema_version=7)
    assert plan.integrity.ok is False
    assert plan.can_proceed is False


def test_inspect_handles_missing_manifest(tmp_path):
    """A directory that isn't a Mira bundle inspects cleanly: empty
    manifest, can_proceed=False (integrity failed)."""
    bogus = tmp_path / "not-a-bundle"
    bogus.mkdir()
    (bogus / "trash.bin").write_bytes(b"hi")
    plan = event_bundle.inspect_bundle(bogus, target_schema_version=7)
    assert plan.integrity.ok is False
    assert plan.manifest.event_uuid == ""
    assert plan.can_proceed is False


# ── install_bundle (slice 6) ──────────────────────────────────────


def test_install_round_trips_a_full_event(tmp_path):
    """Export → install into a different library_base → the new
    event_root carries every file with byte-identical content."""
    src = tmp_path / "RoundTripEvent"
    _seed_event_tree(src)
    export_dest = tmp_path / "bundles"
    export_event(src, src / "event.db", export_dest, created_at=NOW)
    bundle = export_dest / "RoundTripEvent"
    plan = event_bundle.inspect_bundle(bundle, target_schema_version=7)
    assert plan.can_proceed

    library_base = tmp_path / "library"
    new_root = event_bundle.install_bundle(plan, library_base)

    assert new_root == library_base / "RoundTripEvent"
    assert new_root.is_dir()
    # Every file the manifest names is at the destination.
    for f in plan.manifest.files:
        assert (new_root / f.relpath).exists()
    # Media bytes survived end-to-end.
    src_jpg = src / "Original Media" / "_cameras" / "IMG_0001.jpg"
    new_jpg = new_root / "Original Media" / "_cameras" / "IMG_0001.jpg"
    assert new_jpg.read_bytes() == src_jpg.read_bytes()


def test_install_refuses_when_plan_failed_pre_flight(tmp_path):
    """install_bundle must never proceed when can_proceed=False —
    the caller is responsible for showing the version / integrity
    error to the user instead."""
    bundle = _export_bundle(tmp_path)
    plan = event_bundle.inspect_bundle(
        bundle, target_schema_version=5)   # version-newer
    assert not plan.can_proceed
    library_base = tmp_path / "library"
    with pytest.raises(RuntimeError):
        event_bundle.install_bundle(plan, library_base)


def test_install_atomic_finalisation(tmp_path):
    """No ``.partial`` left behind on a successful install; the
    final event_root appears in one shot."""
    bundle = _export_bundle(tmp_path)
    plan = event_bundle.inspect_bundle(bundle, target_schema_version=7)
    library_base = tmp_path / "library"
    event_bundle.install_bundle(plan, library_base)
    assert not (
        library_base / f"RoundTripEvent{PARTIAL_SUFFIX}").exists()
    assert (library_base / "RoundTripEvent").is_dir()


def test_install_replace_target_overwrites_existing(tmp_path):
    """The Replace path: the existing event_root is present at the
    final name; install_bundle wipes it and lands the fresh bytes.
    Caller is responsible for taking a Part-A snapshot of the
    existing one first."""
    bundle = _export_bundle(tmp_path)
    plan = event_bundle.inspect_bundle(bundle, target_schema_version=7)
    library_base = tmp_path / "library"
    # First install to set up an "existing" event_root.
    event_bundle.install_bundle(plan, library_base)
    # Mutate the existing copy so we can detect that the install
    # actually replaced it.
    (library_base / "RoundTripEvent" / "spurious.txt").write_bytes(
        b"left over from a previous Mira install")
    # Second install — must replace, not error.
    event_bundle.install_bundle(plan, library_base)
    assert not (
        library_base / "RoundTripEvent" / "spurious.txt").exists()


def test_install_carries_the_manifest_into_the_event_root(tmp_path):
    """The destination event_root keeps mira-event.json so the
    provenance trail (event_uuid + bundle creation time) lives
    alongside the event for future debugging."""
    bundle = _export_bundle(tmp_path)
    plan = event_bundle.inspect_bundle(bundle, target_schema_version=7)
    library_base = tmp_path / "library"
    new_root = event_bundle.install_bundle(plan, library_base)
    assert (new_root / MANIFEST_FILENAME).exists()
