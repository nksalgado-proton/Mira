"""Tests for core.source_index — the EXIF-driven scan that replaces
the "user pre-sorts into per-camera subfolders" requirement of the
photos-import flow (Nelson 2026-05-21)."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from core.exif_rewriter import _get_exiftool_path
from core.source_index import (
    UNIDENTIFIED_CAMERA_ID,
    looks_like_phone,
    scan_source_tree,
)


def _make_jpeg(
    path: Path, dto: datetime,
    *, model: str = "DC-G9", make: str = "Panasonic",
) -> Path:
    """Write a tiny JPEG with EXIF DateTimeOriginal + Make + Model."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), color=(127, 127, 127)).save(
        path, "JPEG", quality=90,
    )
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


# ── looks_like_phone ─────────────────────────────────────────────


@pytest.mark.parametrize("camera_id,expected", [
    ("iPhone 15", True),
    ("iPhone 11 Pro", True),
    ("Pixel 8", True),
    ("SM-G998B", False),                          # cryptic Samsung code
    ("Galaxy S24", True),
    ("Redmi Note 12", True),
    ("DC-G9", False),
    ("HERO12 Black", False),
    ("", False),
    (UNIDENTIFIED_CAMERA_ID, False),
])
def test_looks_like_phone(camera_id, expected):
    assert looks_like_phone(camera_id) is expected


# ── scan_source_tree ─────────────────────────────────────────────


def test_scan_groups_by_exif_model_regardless_of_subfolders(tmp_path):
    """The point of the new flow: a flat root with mixed cameras
    still groups correctly. The user can dump everything into one
    folder; the scan figures out who shot what."""
    root = tmp_path / "archive"
    # Mix three cameras in ONE flat folder + a nested subfolder.
    _make_jpeg(root / "DSC_001.jpg", datetime(2025, 10, 26, 12, 0, 0),
               model="DC-G9", make="Panasonic")
    _make_jpeg(root / "DSC_002.jpg", datetime(2025, 10, 26, 13, 0, 0),
               model="DC-G9", make="Panasonic")
    _make_jpeg(root / "IMG_001.jpg", datetime(2025, 10, 27, 9, 0, 0),
               model="iPhone 15", make="Apple")
    _make_jpeg(root / "stuff" / "GOPR0001.jpg",
               datetime(2025, 10, 28, 14, 0, 0),
               model="HERO12 Black", make="GoPro")

    idx = scan_source_tree(root)

    assert idx.total_files == 4
    assert set(idx.cameras.keys()) == {
        "DC-G9", "iPhone 15", "HERO12 Black",
    }
    assert idx.cameras["DC-G9"].file_count == 2
    assert idx.cameras["iPhone 15"].file_count == 1
    assert idx.cameras["HERO12 Black"].file_count == 1


def test_scan_marks_phone_via_model_heuristic(tmp_path):
    """The is_phone flag on each ScannedCamera defaults from the
    Model string — the user can override in step 3 if needed."""
    root = tmp_path / "archive"
    _make_jpeg(root / "g.jpg", datetime(2025, 10, 26, 12, 0, 0),
               model="DC-G9", make="Panasonic")
    _make_jpeg(root / "i.jpg", datetime(2025, 10, 27, 9, 0, 0),
               model="iPhone 15", make="Apple")

    idx = scan_source_tree(root)

    assert idx.cameras["iPhone 15"].is_phone is True
    assert idx.cameras["DC-G9"].is_phone is False


def test_scan_records_date_range_per_camera(tmp_path):
    """date_range is the min/max calendar date over the camera's
    timestamped files — the UI uses this to show users a 'iPhone —
    1,247 photos, Oct 24 – Nov 9' hint per row."""
    root = tmp_path / "archive"
    _make_jpeg(root / "a.jpg", datetime(2025, 10, 26, 12, 0, 0))
    _make_jpeg(root / "b.jpg", datetime(2025, 10, 29, 14, 0, 0))
    _make_jpeg(root / "c.jpg", datetime(2025, 11, 3, 9, 0, 0))

    idx = scan_source_tree(root)

    cam = next(iter(idx.cameras.values()))
    assert cam.date_range is not None
    earliest, latest = cam.date_range
    assert earliest.isoformat() == "2025-10-26"
    assert latest.isoformat() == "2025-11-03"


def test_scan_handles_files_with_no_make_or_model(tmp_path):
    """Files with stripped EXIF (no Make/Model) go into the
    UNIDENTIFIED bucket, not silently dropped. The user can still
    import them — they just won't get auto-categorised."""
    root = tmp_path / "archive"
    # JPEG with EXIF DTO but no Make/Model (we strip them after).
    p = _make_jpeg(root / "naked.jpg", datetime(2025, 10, 26, 12, 0, 0))
    cp = subprocess.run(
        [
            str(_get_exiftool_path()), "-overwrite_original",
            "-Make=", "-Model=",
            str(p),
        ],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert cp.returncode == 0, cp.stderr

    idx = scan_source_tree(root)

    assert UNIDENTIFIED_CAMERA_ID in idx.cameras
    assert idx.cameras[UNIDENTIFIED_CAMERA_ID].file_count == 1


def test_scan_empty_root_returns_empty_index(tmp_path):
    """An empty root → empty SourceIndex (not a crash). The UI
    surfaces a 'no photos found' message in that case."""
    root = tmp_path / "empty"
    root.mkdir()
    idx = scan_source_tree(root)
    assert idx.is_empty
    assert idx.cameras == {}
    assert idx.total_files == 0


def test_scan_missing_root_returns_empty_index(tmp_path):
    """A non-existent root → empty SourceIndex (not a crash).
    fresh_source.read_source_items already guards this."""
    idx = scan_source_tree(tmp_path / "does-not-exist")
    assert idx.is_empty


def test_cameras_sorted_most_files_first(tmp_path):
    """cameras_sorted() orders by file_count descending then by id —
    the trip's main body lands at the top of the step-3 list."""
    root = tmp_path / "archive"
    for i in range(5):
        _make_jpeg(
            root / f"g{i}.jpg",
            datetime(2025, 10, 26, 12, 0, i),
            model="DC-G9",
        )
    _make_jpeg(root / "i.jpg", datetime(2025, 10, 27, 9, 0),
               model="iPhone 15")
    _make_jpeg(root / "h1.jpg", datetime(2025, 10, 28, 9, 0),
               model="HERO12 Black")
    _make_jpeg(root / "h2.jpg", datetime(2025, 10, 28, 9, 1),
               model="HERO12 Black")

    idx = scan_source_tree(root)

    ordered = idx.cameras_sorted()
    assert [c.camera_id for c in ordered] == [
        "DC-G9", "HERO12 Black", "iPhone 15",
    ]


def test_scan_progress_callback_fires_at_named_stages(tmp_path):
    """The progress callback fires at four named stages (Nelson
    2026-05-21): walking → reading-EXIF → grouping → done. The
    multi-stage labels exist so the modal "Scanning photos" dialog
    has *something* to paint before each blocking call freezes the
    GUI thread (an earlier single-emit version produced an empty
    dialog body)."""
    root = tmp_path / "archive"
    _make_jpeg(root / "a.jpg", datetime(2025, 10, 26, 12, 0, 0))
    _make_jpeg(root / "b.jpg", datetime(2025, 10, 27, 9, 0))

    calls: list[tuple[str, int, int]] = []
    scan_source_tree(root, progress=lambda m, c, t: calls.append((m, c, t)))

    # At least four emits (one per stage).
    assert len(calls) >= 4
    msgs = [c[0] for c in calls]
    # Stage 1: walking (indeterminate).
    assert "walking" in msgs[0].lower()
    assert calls[0][1] == 0 and calls[0][2] == 0
    # Stage 2: reading EXIF — fires BEFORE the slow exiftool batch
    # AND carries the file count so the user knows what's coming.
    reading_stage = next(
        (i for i, m in enumerate(msgs) if "reading exif" in m.lower()),
        None,
    )
    assert reading_stage is not None
    assert calls[reading_stage][2] == 2          # total = file count
    # Stage 3: grouping.
    assert any("grouping" in m.lower() for m in msgs)
    # Stage 4 (last): final summary with file count.
    last = calls[-1]
    assert "found" in last[0].lower()
    assert last[1] == 2 and last[2] == 2


def test_scan_progress_callback_fires_on_empty_root(tmp_path):
    """Empty root still emits the walking-stage label AND a final
    'Found 0 camera(s), 0 file(s).' so the dialog never has a
    blank period."""
    root = tmp_path / "empty"
    root.mkdir()
    calls: list[tuple[str, int, int]] = []
    scan_source_tree(root, progress=lambda m, c, t: calls.append((m, c, t)))
    assert len(calls) >= 2
    assert "walking" in calls[0][0].lower()
    assert "0 camera" in calls[-1][0].lower()


# ── B-002: filename-recovery for files with no EXIF (WhatsApp) ──


def _make_jpeg_no_exif(path: Path) -> Path:
    """Write a 16×16 JPEG with NO EXIF whatsoever — WhatsApp/Telegram
    re-encode and strip everything. The scanner has nothing to read
    from the file content itself; the only date signal is the name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), color=(127, 127, 127)).save(
        path, "JPEG", quality=90,
    )
    return path


def test_b002_filename_recovery_for_whatsapp_style_names(tmp_path):
    """B-002 — files with no EXIF Make/Model AND no EXIF timestamp
    but a recognisable date in the filename (the WhatsApp pattern
    ``WhatsApp Image YYYY-MM-DD at HH.MM.SS_xxx.jpg``) must get a
    timestamp from filename-recovery, not be dropped silently. Argentina
    source 2025-09-24 lost 29 files + 2 calendar days to this bug."""
    src = tmp_path / "source"
    _make_jpeg_no_exif(
        src / "WhatsApp Image 2025-10-06 at 11.31.18_aaaa.jpg"
    )
    _make_jpeg_no_exif(
        src / "WhatsApp Image 2025-10-07 at 14.43.16_bbbb.jpg"
    )
    idx = scan_source_tree(tmp_path)
    assert idx.total_files == 2
    # Both files must have a non-None timestamp, recovered from name.
    items = list(idx.items)
    assert all(it.timestamp is not None for it in items), (
        "WhatsApp files dropped — filename-recovery not wired"
    )
    dates = sorted({it.timestamp.date().isoformat() for it in items})
    assert dates == ["2025-10-06", "2025-10-07"]


def test_b002_exif_dates_unchanged_when_present(tmp_path):
    """The fix must not regress the happy path: files with EXIF
    DateTimeOriginal keep that timestamp, NOT a filename-derived one.
    Catches any future refactor that swaps the precedence."""
    # File with EXIF date 2025-01-01 but a misleading 2099-12-31 in
    # the name — EXIF must win.
    path = tmp_path / "2099-12-31_misleading.jpg"
    _make_jpeg(path, datetime(2025, 1, 1, 12, 0, 0))
    idx = scan_source_tree(tmp_path)
    assert idx.total_files == 1
    assert idx.items[0].timestamp.date().isoformat() == "2025-01-01"
