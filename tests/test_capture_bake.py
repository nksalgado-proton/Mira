"""Tests for core.capture_bake — the Model 3 v2 ingest-bake engine.

Verifies the bake correctly applies a per-camera offset to the EXIF
DateTimeOriginal of every file recorded in an offload manifest, and
that the no-op path (offset=0) skips cleanly.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from core.capture_bake import BakeResult, bake_offload_manifest
from core.event_backup_card import OffloadFileRecord, OffloadManifest
from core.exif_reader import _get_exiftool_path

pytestmark = pytest.mark.skipif(
    not _get_exiftool_path().exists(),
    reason="bundled exiftool not present; skipping bake integration tests",
)


def _make_test_jpeg(path: Path, when: datetime) -> Path:
    """Tiny JPEG with a real EXIF DateTimeOriginal we can read back."""
    from tests.test_exif_rewriter import _make_jpeg_with_exif_time
    return _make_jpeg_with_exif_time(path, when)


def _read_dto(path: Path) -> datetime:
    """Read DateTimeOriginal from a JPEG (helper shared with other tests)."""
    from tests.test_exif_rewriter import _read_dto as helper
    return helper(path)


def _make_manifest(
    tmp_path: Path,
    times: list[datetime],
    camera_id: str = "G9",
) -> OffloadManifest:
    """Build an OffloadManifest with N real on-disk JPEGs, each
    carrying a known DateTimeOriginal."""
    records: list[OffloadFileRecord] = []
    session_dir = tmp_path / "offload_session" / camera_id
    session_dir.mkdir(parents=True, exist_ok=True)
    for i, t in enumerate(times):
        p = _make_test_jpeg(session_dir / f"img_{i:03d}.jpg", t)
        records.append(OffloadFileRecord(
            src=str(tmp_path / "fake_source" / p.name),
            dest=str(p),
            sha256="0" * 64,
            bytes=p.stat().st_size,
            capture_time_raw=t.isoformat(timespec="seconds"),
            capture_time_corrected=None,
        ))
    return OffloadManifest(
        schema_version=1,
        source_dir=str(tmp_path / "fake_source"),
        event_root=str(tmp_path / "event"),
        camera_id=camera_id,
        bucket="_cameras",
        ran_at="2026-05-22T12:00:00",
        session_subdir_name="offload_20260522-120000",
        files=records,
    )


# ── No-op path ──────────────────────────────────────────────────


def test_bake_offset_zero_is_a_noop(tmp_path):
    """Passing offset=0.0 must not touch any file — no EXIF writes,
    no errors, no skipped files reported."""
    times = [datetime(2026, 5, 1, 14, 0, 0)]
    manifest = _make_manifest(tmp_path, times)
    # Capture the pre-bake EXIF to confirm it doesn't change.
    pre_dto = _read_dto(Path(manifest.files[0].dest))

    result = bake_offload_manifest(manifest, 0.0)

    assert isinstance(result, BakeResult)
    assert result.ok_count == 0
    assert result.skipped_no_timestamp == 0
    assert result.errors == []
    assert result.offset_hours == 0.0
    # EXIF unchanged.
    assert _read_dto(Path(manifest.files[0].dest)) == pre_dto


def test_bake_empty_manifest_returns_clean_result(tmp_path):
    """Zero-file manifest returns an empty BakeResult."""
    manifest = _make_manifest(tmp_path, [])
    result = bake_offload_manifest(manifest, 3.0)
    assert result.ok_count == 0
    assert result.errors == []


# ── Positive offset ─────────────────────────────────────────────


def test_bake_positive_offset_adds_hours(tmp_path):
    """Camera was 3h BEHIND trip-local time → offset=+3 → each
    photo's EXIF DateTimeOriginal gains 3 hours."""
    times = [
        datetime(2026, 5, 1, 14, 0, 0),
        datetime(2026, 5, 1, 14, 30, 0),
    ]
    manifest = _make_manifest(tmp_path, times)
    result = bake_offload_manifest(manifest, 3.0)

    assert result.ok_count == 2
    assert result.errors == []
    assert _read_dto(Path(manifest.files[0].dest)) == datetime(
        2026, 5, 1, 17, 0, 0)
    assert _read_dto(Path(manifest.files[1].dest)) == datetime(
        2026, 5, 1, 17, 30, 0)


# ── Negative offset ─────────────────────────────────────────────


def test_bake_negative_offset_subtracts_hours(tmp_path):
    """Camera was 5h AHEAD of trip-local time → offset=-5 → each
    photo's EXIF DateTimeOriginal loses 5 hours."""
    times = [datetime(2026, 5, 1, 19, 0, 0)]
    manifest = _make_manifest(tmp_path, times)
    result = bake_offload_manifest(manifest, -5.0)

    assert result.ok_count == 1
    assert _read_dto(Path(manifest.files[0].dest)) == datetime(
        2026, 5, 1, 14, 0, 0)


# ── Fractional offset ────────────────────────────────────────────


def test_bake_fractional_offset(tmp_path):
    """India is +5h30 — fractional offsets must work (timedelta
    handles them natively)."""
    times = [datetime(2026, 5, 1, 12, 0, 0)]
    manifest = _make_manifest(tmp_path, times)
    result = bake_offload_manifest(manifest, 5.5)

    assert result.ok_count == 1
    assert _read_dto(Path(manifest.files[0].dest)) == datetime(
        2026, 5, 1, 17, 30, 0)


# ── Missing capture_time_raw ────────────────────────────────────


def test_bake_skips_files_without_raw_timestamp(tmp_path):
    """Files whose capture_time_raw is None (no readable EXIF on
    the source) are skipped — we can't compute a target. Counted
    in skipped_no_timestamp; not an error."""
    times = [datetime(2026, 5, 1, 14, 0, 0)]
    manifest = _make_manifest(tmp_path, times)
    # Strip the raw timestamp on the one record.
    manifest.files[0].capture_time_raw = None

    result = bake_offload_manifest(manifest, 3.0)

    assert result.ok_count == 0
    assert result.skipped_no_timestamp == 1
    assert result.errors == []


def test_bake_skips_unparseable_raw_timestamp(tmp_path):
    """Malformed capture_time_raw is treated like missing — skipped,
    not errored. Defensive against legacy manifests."""
    times = [datetime(2026, 5, 1, 14, 0, 0)]
    manifest = _make_manifest(tmp_path, times)
    manifest.files[0].capture_time_raw = "not-a-datetime"

    result = bake_offload_manifest(manifest, 3.0)

    assert result.ok_count == 0
    assert result.skipped_no_timestamp == 1


# ── Progress callback ───────────────────────────────────────────


def test_bake_progress_callback_fires(tmp_path):
    """The optional progress callback is called during the bake
    so Qt hosts can drive a progress dialog."""
    times = [datetime(2026, 5, 1, 14, i, 0) for i in range(3)]
    manifest = _make_manifest(tmp_path, times)
    calls: list[tuple[str, int, int]] = []

    def _capture(msg: str, cur: int, tot: int) -> None:
        calls.append((msg, cur, tot))

    result = bake_offload_manifest(manifest, 2.0, progress=_capture)

    assert result.ok_count == 3
    # At minimum the prepare + applying messages fired with a real total.
    assert any("Preparing" in m or "Applying" in m for m, _, _ in calls)
    assert any(t == 3 for _, _, t in calls)


# ── Mixed valid + invalid ───────────────────────────────────────


def test_bake_partial_success_when_some_files_lack_timestamps(tmp_path):
    """A manifest where SOME files have raw timestamps and others
    don't: the valid ones get baked, the invalid ones are skipped,
    and the result reports both counts."""
    times = [
        datetime(2026, 5, 1, 14, 0, 0),
        datetime(2026, 5, 1, 14, 30, 0),
        datetime(2026, 5, 1, 15, 0, 0),
    ]
    manifest = _make_manifest(tmp_path, times)
    # Wipe the raw timestamp on the middle record.
    manifest.files[1].capture_time_raw = None

    result = bake_offload_manifest(manifest, 1.0)

    assert result.ok_count == 2
    assert result.skipped_no_timestamp == 1
    # First file shifted, second untouched, third shifted.
    assert _read_dto(Path(manifest.files[0].dest)) == datetime(
        2026, 5, 1, 15, 0, 0)
    # Middle file's EXIF stays at its original time (never baked).
    assert _read_dto(Path(manifest.files[1].dest)) == datetime(
        2026, 5, 1, 14, 30, 0)
    assert _read_dto(Path(manifest.files[2].dest)) == datetime(
        2026, 5, 1, 16, 0, 0)
