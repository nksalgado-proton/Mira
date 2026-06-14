"""Tests for core.event_backup_card — the Stage D card-offload engine.

Synthetic JPEGs via PIL + exiftool to populate ``DateTimeOriginal``,
exercise the byte-untouched copy + SHA-256 hash + manifest contract,
and verify the integrity-gate (``verify_offload``) catches tamper and
deletion.

Skipped when bundled exiftool isn't available (same convention as
test_reconcile_pipeline.py).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import date, datetime
from pathlib import Path

import pytest
from PIL import Image

from core.clock_calibration import CalibrationPair, build_calibration
from core.cull_export import CollisionPolicy
from core.event_backup_card import (
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    OffloadConfig,
    OffloadManifest,
    OffloadFileRecord,
    hash_file,
    offload_to_captured,
    read_manifest,
    verify_offload,
    write_manifest,
)
from core.exif_reader import _get_exiftool_path
from core.models import TripDay
from core.path_builder import (
    CAPTURED_CAMERAS_SUBDIR,
    CAPTURED_NO_TIMESTAMP_SUBDIR,
    CAPTURED_PHONES_SUBDIR,
)


pytestmark = pytest.mark.skipif(
    not _get_exiftool_path().exists(),
    reason="bundled exiftool not present",
)


# ── Helpers ─────────────────────────────────────────────────────


def _make_jpeg(
    path: Path,
    dto: datetime,
    *,
    model: str = "DC-G9M2",
    make: str = "Panasonic",
    seed: int = 0,
) -> Path:
    """Synthetic JPEG with a stamped DateTimeOriginal. ``seed`` varies
    the pixel content so two files at the same path don't end up with
    identical bytes (hash-mismatch tests need distinct content)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    color = (seed % 256, (seed * 3) % 256, (seed * 7) % 256)
    Image.new("RGB", (16, 16), color=color).save(path, "JPEG", quality=90)
    cp = subprocess.run(
        [
            str(_get_exiftool_path()), "-overwrite_original",
            f"-DateTimeOriginal={dto.strftime('%Y:%m:%d %H:%M:%S')}",
            f"-CreateDate={dto.strftime('%Y:%m:%d %H:%M:%S')}",
            f"-Make={make}",
            f"-Model={model}",
            str(path),
        ],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert cp.returncode == 0, cp.stderr
    return path


def _make_no_exif_jpeg(path: Path, seed: int = 0) -> Path:
    """JPEG with no EXIF timestamp — exercises the quarantine path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    color = (seed % 256, (seed * 5) % 256, (seed * 11) % 256)
    Image.new("RGB", (16, 16), color=color).save(path, "JPEG", quality=90)
    return path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _basic_days() -> dict[int, TripDay]:
    return {
        1: TripDay(day_number=1, date=date(2026, 5, 19), description="Lukla"),
        2: TripDay(day_number=2, date=date(2026, 5, 20), description="Phakding"),
        3: TripDay(day_number=3, date=date(2026, 5, 21), description="Namche"),
    }


# ── hash_file / _hash_and_copy ──────────────────────────────────


def test_hash_file_matches_hashlib(tmp_path):
    """``hash_file`` returns the same digest as a one-shot hashlib
    over the same bytes, plus the correct byte count."""
    src = _make_jpeg(tmp_path / "p.jpg", datetime(2026, 5, 19, 12, 0, 0))
    expected = _sha256(src)
    digest, n = hash_file(src)
    assert digest == expected
    assert n == src.stat().st_size


# ── offload_to_captured: flat layout (no plan) ──────────────────


def test_offload_flat_layout_no_plan(tmp_path):
    """No ``day_by_number`` → flat layout under the session subdir.
    Manifest is written; copies match source bytes; hash recorded."""
    src_dir = tmp_path / "card"
    p1 = _make_jpeg(src_dir / "P1000001.JPG",
                    datetime(2026, 5, 19, 14, 0, 0), seed=1)
    p2 = _make_jpeg(src_dir / "sub" / "P1000002.JPG",
                    datetime(2026, 5, 19, 14, 5, 0), seed=2)

    event_root = tmp_path / "event"
    config = OffloadConfig(
        source_dir=src_dir,
        event_root=event_root,
        camera_id="G9_mkII",
        bucket=CAPTURED_CAMERAS_SUBDIR,
        calibration=None,
        day_by_number=None,
        ran_at=datetime(2026, 5, 20, 14, 30, 52),
    )
    result = offload_to_captured(config)

    assert result.written_count == 2
    expected_session = (
        event_root / "Original Media" / CAPTURED_CAMERAS_SUBDIR
        / "G9_mkII" / "offload_20260520-143052"
    )
    assert result.session_subdir == expected_session
    # Both files landed at the bucket-session root (flat).
    for rec in result.manifest.files:
        dest = Path(rec.dest)
        assert dest.parent == expected_session
        assert dest.is_file()
        assert rec.sha256 == _sha256(dest)
        # Byte-untouched: dest bytes == source bytes
        src = Path(rec.src)
        assert dest.read_bytes() == src.read_bytes()
    # Manifest sidecar exists
    assert (expected_session / MANIFEST_FILENAME).is_file()


# ── Day routing ─────────────────────────────────────────────────


def test_offload_routes_by_plan_day(tmp_path):
    """``day_by_number`` provided → each file lands in its Dia folder
    based on EXIF capture date."""
    src_dir = tmp_path / "card"
    _make_jpeg(src_dir / "a.jpg", datetime(2026, 5, 19, 10, 0, 0), seed=1)
    _make_jpeg(src_dir / "b.jpg", datetime(2026, 5, 20, 10, 0, 0), seed=2)
    _make_jpeg(src_dir / "c.jpg", datetime(2026, 5, 21, 10, 0, 0), seed=3)

    event_root = tmp_path / "event"
    config = OffloadConfig(
        source_dir=src_dir,
        event_root=event_root,
        camera_id="G9_mkII",
        bucket=CAPTURED_CAMERAS_SUBDIR,
        day_by_number=_basic_days(),
        ran_at=datetime(2026, 5, 22, 9, 0, 0),
    )
    result = offload_to_captured(config)

    by_day = {rec.day_number for rec in result.manifest.files}
    assert by_day == {1, 2, 3}
    # Path shape: Original Media/_cameras/Dia 1 - 2026-05-19 - Lukla/G9_mkII/offload_<TS>/a.jpg
    cap = event_root / "Original Media"
    assert (cap / CAPTURED_CAMERAS_SUBDIR / "Dia 1 - 2026-05-19 - Lukla"
            / "G9_mkII" / "offload_20260522-090000" / "a.jpg").is_file()
    assert (cap / CAPTURED_CAMERAS_SUBDIR / "Dia 2 - 2026-05-20 - Phakding"
            / "G9_mkII" / "offload_20260522-090000" / "b.jpg").is_file()


def test_offload_quarantines_no_exif(tmp_path):
    """File with no DateTimeOriginal → ``_no_timestamp/<camera>/offload_<TS>/``
    with mtime prefix on the filename."""
    src_dir = tmp_path / "card"
    _make_no_exif_jpeg(src_dir / "broken.jpg", seed=42)

    event_root = tmp_path / "event"
    config = OffloadConfig(
        source_dir=src_dir,
        event_root=event_root,
        camera_id="G9_mkII",
        bucket=CAPTURED_CAMERAS_SUBDIR,
        day_by_number=_basic_days(),
        ran_at=datetime(2026, 5, 22, 10, 0, 0),
    )
    result = offload_to_captured(config)

    quarantine = (
        event_root / "Original Media" / CAPTURED_NO_TIMESTAMP_SUBDIR
        / "G9_mkII" / "offload_20260522-100000"
    )
    assert result.quarantine_subdir == quarantine
    files = list(quarantine.glob("*.jpg"))
    assert len(files) == 1
    # Filename has a YYYY-MM-DD_HH-MM-SS prefix
    assert files[0].name.endswith("__broken.jpg")
    # Manifest records day_number==0 (quarantine sentinel)
    assert result.manifest.files[0].day_number == 0
    assert result.manifest.files[0].capture_time_raw is None


def test_offload_unmatched_calibrated_date(tmp_path):
    """Calibrated date doesn't match any plan day → ``_unmatched``
    sibling with a warning."""
    src_dir = tmp_path / "card"
    # 2026-06-01 is outside the plan (only 19-21 in _basic_days)
    _make_jpeg(src_dir / "stray.jpg", datetime(2026, 6, 1, 12, 0, 0), seed=1)

    event_root = tmp_path / "event"
    config = OffloadConfig(
        source_dir=src_dir,
        event_root=event_root,
        camera_id="G9_mkII",
        bucket=CAPTURED_CAMERAS_SUBDIR,
        day_by_number=_basic_days(),
        ran_at=datetime(2026, 6, 5, 12, 0, 0),
    )
    result = offload_to_captured(config)

    unmatched = (
        event_root / "Original Media" / CAPTURED_CAMERAS_SUBDIR
        / "_unmatched" / "G9_mkII" / "offload_20260605-120000"
    )
    assert (unmatched / "stray.jpg").is_file()
    assert result.manifest.files[0].day_number is None
    assert any("doesn't match any plan day" in w.message
               for w in result.warnings)


# ── Calibration ─────────────────────────────────────────────────


def test_offload_calibration_shifts_day(tmp_path):
    """Camera clock 1h behind real time → photo near midnight gets
    shifted forward and lands in the NEXT Dia, not the recorded Dia."""
    src_dir = tmp_path / "card"
    # Camera says 23:30 on the 19th; reality (after +1h shift) is
    # 00:30 on the 20th → should route to Dia 2, not Dia 1.
    _make_jpeg(src_dir / "edge.jpg",
               datetime(2026, 5, 19, 23, 30, 0), seed=1)

    # +1h offset via a single pair: ref_time = camera + 1h
    pair = CalibrationPair(
        camera_path=src_dir / "edge.jpg",
        reference_path=src_dir / "edge.jpg",
        camera_time=datetime(2026, 5, 19, 12, 0, 0),
        reference_time=datetime(2026, 5, 19, 13, 0, 0),
    )
    cal = build_calibration("G9_mkII", [pair])
    assert cal.has_any_source

    event_root = tmp_path / "event"
    config = OffloadConfig(
        source_dir=src_dir,
        event_root=event_root,
        camera_id="G9_mkII",
        bucket=CAPTURED_CAMERAS_SUBDIR,
        calibration=cal,
        day_by_number=_basic_days(),
        ran_at=datetime(2026, 5, 22, 9, 0, 0),
    )
    result = offload_to_captured(config)

    rec = result.manifest.files[0]
    assert rec.day_number == 2
    dest = Path(rec.dest)
    assert "Dia 2 - 2026-05-20 - Phakding" in dest.parts


# ── Collision policy ────────────────────────────────────────────


def test_offload_collision_unique_renames(tmp_path):
    """UNIQUE policy on a duplicate filename → " (2)" suffix; existing
    file untouched."""
    src_dir = tmp_path / "card"
    _make_jpeg(src_dir / "a.jpg", datetime(2026, 5, 19, 12, 0, 0), seed=1)

    event_root = tmp_path / "event"
    # Pre-create a same-named file in the target dir so the offload
    # collides with it.
    target_dir = (
        event_root / "Original Media" / CAPTURED_CAMERAS_SUBDIR
        / "Dia 1 - 2026-05-19 - Lukla" / "G9_mkII" / "offload_20260522-090000"
    )
    target_dir.mkdir(parents=True)
    pre_existing = target_dir / "a.jpg"
    pre_existing.write_bytes(b"PRE-EXISTING-CONTENT")
    pre_existing_hash = _sha256(pre_existing)

    config = OffloadConfig(
        source_dir=src_dir,
        event_root=event_root,
        camera_id="G9_mkII",
        bucket=CAPTURED_CAMERAS_SUBDIR,
        day_by_number=_basic_days(),
        collision=CollisionPolicy.UNIQUE,
        ran_at=datetime(2026, 5, 22, 9, 0, 0),
    )
    result = offload_to_captured(config)

    # Pre-existing untouched.
    assert _sha256(pre_existing) == pre_existing_hash
    # New file landed at "a (2).jpg".
    new_file = target_dir / "a (2).jpg"
    assert new_file.is_file()
    assert result.manifest.files[0].dest == str(new_file)


def test_offload_collision_override_replaces(tmp_path):
    """OVERRIDE policy on a duplicate filename → destination is
    replaced; no rename. Manifest records the canonical name."""
    src_dir = tmp_path / "card"
    src = _make_jpeg(src_dir / "a.jpg",
                     datetime(2026, 5, 19, 12, 0, 0), seed=1)

    event_root = tmp_path / "event"
    target_dir = (
        event_root / "Original Media" / CAPTURED_CAMERAS_SUBDIR
        / "Dia 1 - 2026-05-19 - Lukla" / "G9_mkII" / "offload_20260522-090000"
    )
    target_dir.mkdir(parents=True)
    pre_existing = target_dir / "a.jpg"
    pre_existing.write_bytes(b"OLD")

    config = OffloadConfig(
        source_dir=src_dir,
        event_root=event_root,
        camera_id="G9_mkII",
        bucket=CAPTURED_CAMERAS_SUBDIR,
        day_by_number=_basic_days(),
        collision=CollisionPolicy.OVERRIDE,
        ran_at=datetime(2026, 5, 22, 9, 0, 0),
    )
    result = offload_to_captured(config)

    # Replaced — its bytes now match the source.
    assert (target_dir / "a.jpg").read_bytes() == src.read_bytes()
    assert result.manifest.files[0].dest == str(target_dir / "a.jpg")


# ── Manifest round-trip ─────────────────────────────────────────


def test_manifest_round_trip(tmp_path):
    """write_manifest → read_manifest preserves every field."""
    m = OffloadManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        source_dir="/some/source",
        event_root="/some/event",
        camera_id="G9_mkII",
        bucket=CAPTURED_CAMERAS_SUBDIR,
        ran_at="2026-05-20T14:30:52",
        session_subdir_name="offload_20260520-143052",
        files=[
            OffloadFileRecord(
                src="/some/source/a.jpg",
                dest="/some/event/Original Media/_cameras/Dia 1 - X/cam/offload_X/a.jpg",
                sha256="a" * 64,
                bytes=12345,
                day_number=1,
                capture_time_raw="2026-05-19T12:00:00",
                capture_time_corrected="2026-05-19T13:00:00",
            ),
        ],
    )
    path = tmp_path / MANIFEST_FILENAME
    write_manifest(m, path)
    back = read_manifest(path)
    assert back == m


def test_read_manifest_rejects_future_schema(tmp_path):
    """A manifest with schema_version > engine's must raise — refuse
    to silently misread."""
    path = tmp_path / MANIFEST_FILENAME
    path.write_text(json.dumps({
        "schema_version": 999,
        "source_dir": "/x",
        "event_root": "/y",
        "camera_id": "c",
        "bucket": "_cameras",
        "ran_at": "2026-05-20T00:00:00",
        "session_subdir_name": "offload_x",
        "files": [],
    }))
    with pytest.raises(ValueError, match="schema_version"):
        read_manifest(path)


# ── verify_offload ──────────────────────────────────────────────


def test_verify_passes_clean_session(tmp_path):
    """Fresh offload → verify reports all OK, passed=True."""
    src_dir = tmp_path / "card"
    _make_jpeg(src_dir / "a.jpg", datetime(2026, 5, 19, 10, 0, 0), seed=1)
    _make_jpeg(src_dir / "b.jpg", datetime(2026, 5, 20, 10, 0, 0), seed=2)

    event_root = tmp_path / "event"
    config = OffloadConfig(
        source_dir=src_dir,
        event_root=event_root,
        camera_id="G9_mkII",
        bucket=CAPTURED_CAMERAS_SUBDIR,
        day_by_number=_basic_days(),
        ran_at=datetime(2026, 5, 22, 9, 0, 0),
    )
    result = offload_to_captured(config)
    verified = verify_offload(result.manifest)
    assert verified.passed
    assert len(verified.ok) == 2
    assert verified.missing == []
    assert verified.mismatch == []


def test_verify_catches_tamper(tmp_path):
    """A file modified after offload → verify flags mismatch,
    passed=False (the wipe-gate refuses)."""
    src_dir = tmp_path / "card"
    _make_jpeg(src_dir / "a.jpg", datetime(2026, 5, 19, 10, 0, 0), seed=1)

    event_root = tmp_path / "event"
    config = OffloadConfig(
        source_dir=src_dir,
        event_root=event_root,
        camera_id="G9_mkII",
        bucket=CAPTURED_CAMERAS_SUBDIR,
        day_by_number=_basic_days(),
        ran_at=datetime(2026, 5, 22, 9, 0, 0),
    )
    result = offload_to_captured(config)

    # Tamper: overwrite the dest file's bytes
    dest = Path(result.manifest.files[0].dest)
    dest.write_bytes(b"TAMPERED")
    verified = verify_offload(result.manifest)
    assert not verified.passed
    assert len(verified.mismatch) == 1
    assert verified.mismatch[0][0] == dest


def test_verify_catches_missing(tmp_path):
    """Dest file deleted after offload → verify flags missing,
    passed=False."""
    src_dir = tmp_path / "card"
    _make_jpeg(src_dir / "a.jpg", datetime(2026, 5, 19, 10, 0, 0), seed=1)

    event_root = tmp_path / "event"
    config = OffloadConfig(
        source_dir=src_dir,
        event_root=event_root,
        camera_id="G9_mkII",
        bucket=CAPTURED_CAMERAS_SUBDIR,
        day_by_number=_basic_days(),
        ran_at=datetime(2026, 5, 22, 9, 0, 0),
    )
    result = offload_to_captured(config)

    Path(result.manifest.files[0].dest).unlink()
    verified = verify_offload(result.manifest)
    assert not verified.passed
    assert len(verified.missing) == 1


# ── Empty source ────────────────────────────────────────────────


def test_offload_empty_source_is_noop(tmp_path):
    """No media files under source → empty manifest + info warning,
    no error."""
    src_dir = tmp_path / "card"
    src_dir.mkdir()

    event_root = tmp_path / "event"
    config = OffloadConfig(
        source_dir=src_dir,
        event_root=event_root,
        camera_id="G9_mkII",
        bucket=CAPTURED_CAMERAS_SUBDIR,
        ran_at=datetime(2026, 5, 22, 9, 0, 0),
    )
    result = offload_to_captured(config)
    assert result.written_count == 0
    assert any(w.severity == "info" for w in result.warnings)


def test_offload_missing_source_raises(tmp_path):
    """Source dir doesn't exist → engine raises (caller's bug)."""
    event_root = tmp_path / "event"
    config = OffloadConfig(
        source_dir=tmp_path / "does_not_exist",
        event_root=event_root,
        camera_id="G9_mkII",
        bucket=CAPTURED_CAMERAS_SUBDIR,
        ran_at=datetime(2026, 5, 22, 9, 0, 0),
    )
    with pytest.raises(FileNotFoundError):
        offload_to_captured(config)


# ── Source untouched (the load-bearing invariant) ──────────────


def test_source_files_untouched(tmp_path):
    """The whole point of offload: SOURCE bytes + mtime must be
    identical before and after. Engine never writes back."""
    src_dir = tmp_path / "card"
    src = _make_jpeg(src_dir / "a.jpg",
                     datetime(2026, 5, 19, 12, 0, 0), seed=1)
    src_bytes_before = src.read_bytes()
    src_hash_before = _sha256(src)
    src_mtime_before = src.stat().st_mtime

    event_root = tmp_path / "event"
    config = OffloadConfig(
        source_dir=src_dir,
        event_root=event_root,
        camera_id="G9_mkII",
        bucket=CAPTURED_CAMERAS_SUBDIR,
        day_by_number=_basic_days(),
        ran_at=datetime(2026, 5, 22, 9, 0, 0),
    )
    offload_to_captured(config)

    assert src.read_bytes() == src_bytes_before
    assert _sha256(src) == src_hash_before
    # mtime unchanged (Python timestamp precision varies; allow 1ms)
    assert abs(src.stat().st_mtime - src_mtime_before) < 0.001


# ── Phone bucket ────────────────────────────────────────────────


def test_offload_routes_phone_bucket(tmp_path):
    """Phone camera_id with phones bucket → lands under ``_phones/``
    not ``_cameras/``. (No special phone logic in the engine; the
    bucket is a path-segment choice the caller makes.)"""
    src_dir = tmp_path / "phone"
    _make_jpeg(src_dir / "IMG_0001.jpg",
               datetime(2026, 5, 19, 12, 0, 0),
               make="Apple", model="iPhone 13", seed=1)

    event_root = tmp_path / "event"
    config = OffloadConfig(
        source_dir=src_dir,
        event_root=event_root,
        camera_id="iPhone_13",
        bucket=CAPTURED_PHONES_SUBDIR,
        day_by_number=_basic_days(),
        ran_at=datetime(2026, 5, 22, 9, 0, 0),
    )
    result = offload_to_captured(config)
    dest = Path(result.manifest.files[0].dest)
    assert CAPTURED_PHONES_SUBDIR in dest.parts
    assert CAPTURED_CAMERAS_SUBDIR not in dest.parts


# ── Mode B filter (task #84) ───────────────────────────────────


def test_offload_included_names_filters_to_subset(tmp_path):
    """Task #84: ``included_names`` filter copies only the files
    whose basenames are in the set — the rest are silently skipped.
    Models the Mode B (pre-cull during ingest) flow where the user
    has already discarded a subset via the standalone culler."""
    src_dir = tmp_path / "card"
    _make_jpeg(src_dir / "A.jpg",
               datetime(2026, 5, 19, 12, 0, 0), seed=1)
    _make_jpeg(src_dir / "B.jpg",
               datetime(2026, 5, 19, 12, 1, 0), seed=2)
    _make_jpeg(src_dir / "C.jpg",
               datetime(2026, 5, 19, 12, 2, 0), seed=3)

    event_root = tmp_path / "event"
    config = OffloadConfig(
        source_dir=src_dir,
        event_root=event_root,
        camera_id="G9",
        bucket=CAPTURED_CAMERAS_SUBDIR,
        day_by_number=_basic_days(),
        ran_at=datetime(2026, 5, 22, 9, 0, 0),
        included_names=frozenset({"A.jpg", "C.jpg"}),
    )
    result = offload_to_captured(config)

    copied = {Path(f.dest).name for f in result.manifest.files}
    assert copied == {"A.jpg", "C.jpg"}
    # B.jpg was filtered out — it doesn't appear in the manifest.
    assert "B.jpg" not in copied


def test_offload_included_names_none_is_legacy_copy_all(tmp_path):
    """``included_names=None`` (the default) preserves the legacy
    behaviour: every media file on the source is copied."""
    src_dir = tmp_path / "card"
    _make_jpeg(src_dir / "A.jpg",
               datetime(2026, 5, 19, 12, 0, 0), seed=1)
    _make_jpeg(src_dir / "B.jpg",
               datetime(2026, 5, 19, 12, 1, 0), seed=2)

    event_root = tmp_path / "event"
    config = OffloadConfig(
        source_dir=src_dir,
        event_root=event_root,
        camera_id="G9",
        bucket=CAPTURED_CAMERAS_SUBDIR,
        day_by_number=_basic_days(),
        ran_at=datetime(2026, 5, 22, 9, 0, 0),
        # included_names omitted — defaults to None.
    )
    result = offload_to_captured(config)
    copied = {Path(f.dest).name for f in result.manifest.files}
    assert copied == {"A.jpg", "B.jpg"}


def test_offload_included_names_empty_set_copies_nothing(tmp_path):
    """A user who pre-culled and discarded EVERYTHING produces an
    empty kept-set. The engine should copy zero files (not crash,
    not interpret as 'no filter')."""
    src_dir = tmp_path / "card"
    _make_jpeg(src_dir / "A.jpg",
               datetime(2026, 5, 19, 12, 0, 0), seed=1)

    event_root = tmp_path / "event"
    config = OffloadConfig(
        source_dir=src_dir,
        event_root=event_root,
        camera_id="G9",
        bucket=CAPTURED_CAMERAS_SUBDIR,
        day_by_number=_basic_days(),
        ran_at=datetime(2026, 5, 22, 9, 0, 0),
        included_names=frozenset(),
    )
    result = offload_to_captured(config)
    assert result.manifest.files == []
