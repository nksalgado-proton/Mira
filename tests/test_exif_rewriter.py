"""Tests for core.exif_rewriter — round-trip + UserComment preservation.

Real exiftool calls against synthetic JPEGs. Each test:

  1. Creates a tiny JPG via PIL.
  2. Stamps an initial DateTimeOriginal via exiftool (bootstrap).
  3. Exercises our rewrite_capture_time() and asserts the result.

This is slower than mocking subprocess (~50-200 ms per test under
exiftool startup overhead) but verifies the actual integration —
the cost has caught real exiftool argument-format bugs in similar
projects.

Skipped automatically when the bundled exiftool isn't available
(e.g. CI without the bin/ folder), so the tests don't false-fail.
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from core.exif_rewriter import (
    format_original_time_marker,
    parse_original_time_marker,
    rewrite_capture_time,
)
from core.exif_reader import _get_exiftool_path


pytestmark = pytest.mark.skipif(
    not _get_exiftool_path().exists(),
    reason="bundled exiftool not present; skipping rewriter integration tests",
)


def _make_jpeg_with_exif_time(
    path: Path, dto: datetime,
) -> Path:
    """Create a minimal JPEG with a stamped DateTimeOriginal.
    PIL produces the JPEG; exiftool stamps the EXIF (PIL's EXIF
    write support is too thin for our needs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (16, 16), color=(127, 127, 127))
    img.save(path, "JPEG", quality=90)
    exiftool = _get_exiftool_path()
    cp = subprocess.run(
        [
            str(exiftool), "-overwrite_original",
            f"-DateTimeOriginal={dto.strftime('%Y:%m:%d %H:%M:%S')}",
            f"-CreateDate={dto.strftime('%Y:%m:%d %H:%M:%S')}",
            str(path),
        ],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert cp.returncode == 0, f"exiftool stamp failed: {cp.stderr}"
    return path


def _read_dto(path: Path) -> datetime:
    """Read DateTimeOriginal — used in assertions."""
    cp = subprocess.run(
        [
            str(_get_exiftool_path()), "-s", "-s", "-s",
            "-DateTimeOriginal", str(path),
        ],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert cp.returncode == 0
    raw = cp.stdout.strip()
    return datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")


def _read_user_comment(path: Path) -> str:
    cp = subprocess.run(
        [
            str(_get_exiftool_path()), "-s", "-s", "-s",
            "-UserComment", str(path),
        ],
        capture_output=True, text=True, encoding="utf-8",
    )
    return cp.stdout.strip() if cp.returncode == 0 else ""


# ── Regression: non-ASCII path in the output (Nelson 2026-06-03) ──


def test_rewrite_in_ascii_day_folder_bakes(tmp_path):
    """The materialiser writes snapshots into an ASCII ``01 - Culled/Day N - <date>/`` folder
    (hyphen, not em-dash) precisely so the EXIF bake survives exiftool's Windows path handling.
    Mirrors production: a plain JPEG (no DateTimeOriginal) → ``rewrite_capture_time`` bakes it."""
    folder = tmp_path / "01 - Culled" / "Day 1 - 2025-11-02"
    folder.mkdir(parents=True, exist_ok=True)
    jpeg = folder / "180932_2025-11-02_09.24.50_s1.jpg"
    Image.new("RGB", (16, 16), color=(127, 127, 127)).save(jpeg, "JPEG", quality=90)

    outcome = rewrite_capture_time(
        jpeg, datetime(2025, 11, 2, 18, 9, 32), preserve_original=False)

    assert outcome.error == ""
    assert _read_dto(jpeg) == datetime(2025, 11, 2, 18, 9, 32)   # the bake landed


def test_rewrite_tolerates_nonascii_output_without_crashing(tmp_path):
    """A non-ASCII char in the path (e.g. an em-dash) comes back from exiftool in the Windows
    codepage; a strict UTF-8 decode previously crashed the subprocess reader thread →
    stdout=None → NoneType.strip (an uncaught AttributeError). The per-file path now decodes
    tolerantly and guards None, so it returns a graceful RewriteOutcome instead of raising —
    regardless of whether exiftool can resolve the path on this OS."""
    folder = tmp_path / "Day 1 — 2025-11-02"      # em-dash on purpose
    folder.mkdir(parents=True, exist_ok=True)
    jpeg = folder / "snap.jpg"
    Image.new("RGB", (16, 16), color=(127, 127, 127)).save(jpeg, "JPEG", quality=90)

    outcome = rewrite_capture_time(   # must NOT raise (no reader-thread crash / NoneType)
        jpeg, datetime(2025, 11, 2, 18, 9, 32), preserve_original=False)
    assert isinstance(outcome.error, str)


# ── Format helpers ────────────────────────────────────────────────


def test_format_and_parse_round_trip():
    """The marker round-trips losslessly so the rewriter can re-read
    its own preserved-original bookkeeping."""
    t = datetime(2025, 5, 12, 14, 30, 27)
    marker = format_original_time_marker(t)
    assert marker.startswith("OriginalCaptureTime:")
    parsed = parse_original_time_marker(marker)
    assert parsed == t


def test_parse_marker_rejects_non_marker():
    """Non-marker strings return None — distinguishing user-set
    UserComments from our preservation marker."""
    assert parse_original_time_marker("just a comment") is None
    assert parse_original_time_marker("") is None


# ── Round-trip rewrites ───────────────────────────────────────────


def test_rewrite_changes_datetimeoriginal(tmp_path):
    """Basic happy path — DateTimeOriginal changes to the new value."""
    src = _make_jpeg_with_exif_time(
        tmp_path / "photo.jpg", datetime(2025, 5, 12, 10, 0, 0),
    )
    new_time = datetime(2025, 5, 12, 10, 5, 30)
    outcome = rewrite_capture_time(src, new_time)

    assert outcome.error == ""
    assert outcome.new_time == new_time
    assert _read_dto(src) == new_time


def test_rewrite_preserves_original_in_user_comment(tmp_path):
    """First rewrite captures the file's current DateTimeOriginal
    into UserComment as the audit trail."""
    original = datetime(2025, 5, 12, 10, 0, 0)
    src = _make_jpeg_with_exif_time(tmp_path / "photo.jpg", original)
    new_time = datetime(2025, 5, 12, 10, 5, 30)

    outcome = rewrite_capture_time(src, new_time)

    assert outcome.preserved_original is True
    comment = _read_user_comment(src)
    parsed = parse_original_time_marker(comment)
    assert parsed == original


def test_second_rewrite_does_not_overwrite_user_comment(tmp_path):
    """Re-running Reconcile with a refined calibration must NOT
    change the UserComment marker — the FIRST rewrite captures
    the true original. Otherwise we'd pollute the audit trail
    with intermediate (already-corrected) values."""
    original = datetime(2025, 5, 12, 10, 0, 0)
    src = _make_jpeg_with_exif_time(tmp_path / "photo.jpg", original)

    rewrite_capture_time(src, datetime(2025, 5, 12, 10, 5, 0))
    rewrite_capture_time(src, datetime(2025, 5, 12, 10, 7, 0))
    rewrite_capture_time(src, datetime(2025, 5, 12, 10, 9, 0))

    comment = _read_user_comment(src)
    parsed = parse_original_time_marker(comment)
    # The TRUE original is preserved, not any intermediate.
    assert parsed == original


def test_second_rewrite_outcome_says_not_preserved(tmp_path):
    """The outcome flag tells the caller whether THIS rewrite
    captured the original — useful for status logging in the
    pipeline ("X files had originals preserved, Y were already
    preserved")."""
    src = _make_jpeg_with_exif_time(
        tmp_path / "photo.jpg", datetime(2025, 5, 12, 10, 0, 0),
    )
    first = rewrite_capture_time(src, datetime(2025, 5, 12, 10, 5, 0))
    second = rewrite_capture_time(src, datetime(2025, 5, 12, 10, 7, 0))

    assert first.preserved_original is True
    assert second.preserved_original is False  # already preserved


def test_preserve_original_false_skips_user_comment_write(tmp_path):
    """When the caller opts out of preservation (e.g. a re-run
    where the user knows they want a clean rewrite), UserComment
    must be left alone."""
    src = _make_jpeg_with_exif_time(
        tmp_path / "photo.jpg", datetime(2025, 5, 12, 10, 0, 0),
    )
    rewrite_capture_time(
        src, datetime(2025, 5, 12, 10, 5, 0), preserve_original=False,
    )
    comment = _read_user_comment(src)
    # Empty or whatever JPEG default is — not a marker.
    assert parse_original_time_marker(comment) is None


# ── Failure modes ─────────────────────────────────────────────────


def test_missing_file_returns_error_outcome(tmp_path):
    """Caller hands us a nonexistent path — surface as an error
    in the outcome rather than raising. Keeps the batch loop
    resilient when a few files have moved between scan and
    rewrite."""
    outcome = rewrite_capture_time(
        tmp_path / "does_not_exist.jpg", datetime(2025, 5, 1),
    )
    assert outcome.error != ""
    assert outcome.new_time is None


def test_corrupt_file_returns_error_outcome(tmp_path):
    """Non-image content: exiftool fails. Outcome must capture
    the failure cleanly."""
    junk = tmp_path / "junk.jpg"
    junk.write_bytes(b"not a real jpeg")
    outcome = rewrite_capture_time(junk, datetime(2025, 5, 1))
    assert outcome.error != ""
    assert outcome.new_time is None


# ── Video files (QuickTime tags) ─────────────────────────────────


def _make_video_with_create_date(path: Path, dto: datetime) -> Path:
    """Build a tiny MP4 via ffmpeg + stamp QuickTime CreateDate via
    exiftool. Used for video-mode rewrite tests."""
    from core.video_extract import _make_test_video

    _make_test_video(path, duration_s=0.5)
    cp = subprocess.run(
        [
            str(_get_exiftool_path()), "-overwrite_original",
            f"-CreateDate={dto.strftime('%Y:%m:%d %H:%M:%S')}",
            f"-MediaCreateDate={dto.strftime('%Y:%m:%d %H:%M:%S')}",
            f"-TrackCreateDate={dto.strftime('%Y:%m:%d %H:%M:%S')}",
            str(path),
        ],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert cp.returncode == 0, cp.stderr
    return path


def _read_video_create_date(path: Path) -> datetime:
    """Read QuickTime CreateDate."""
    cp = subprocess.run(
        [
            str(_get_exiftool_path()), "-s", "-s", "-s",
            "-CreateDate", str(path),
        ],
        capture_output=True, text=True, encoding="utf-8",
    )
    raw = cp.stdout.strip().split(".")[0].split("+")[0]
    return datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")


def _read_video_media_create_date(path: Path) -> datetime:
    cp = subprocess.run(
        [
            str(_get_exiftool_path()), "-s", "-s", "-s",
            "-MediaCreateDate", str(path),
        ],
        capture_output=True, text=True, encoding="utf-8",
    )
    raw = cp.stdout.strip().split(".")[0].split("+")[0]
    return datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")


def test_video_rewrite_changes_quicktime_create_date(tmp_path):
    """Video files: rewrite_capture_time targets QuickTime
    CreateDate / MediaCreateDate / TrackCreateDate instead of
    EXIF DateTimeOriginal (which videos don't carry)."""
    src = _make_video_with_create_date(
        tmp_path / "v.mp4", datetime(2025, 5, 12, 10, 0, 0),
    )
    new_time = datetime(2025, 5, 12, 10, 5, 30)
    outcome = rewrite_capture_time(src, new_time)

    assert outcome.error == ""
    assert outcome.new_time == new_time
    assert _read_video_create_date(src) == new_time
    assert _read_video_media_create_date(src) == new_time


def test_video_rewrite_preserves_original_in_user_comment(tmp_path):
    """Same audit-trail behavior on video as on photo — the first
    rewrite captures the original CreateDate into UserComment."""
    original = datetime(2025, 5, 12, 10, 0, 0)
    src = _make_video_with_create_date(tmp_path / "v.mp4", original)
    new_time = datetime(2025, 5, 12, 10, 5, 30)

    outcome = rewrite_capture_time(src, new_time)

    assert outcome.preserved_original is True
    comment = _read_user_comment(src)
    parsed = parse_original_time_marker(comment)
    assert parsed == original


def _read_video_creation_date(path: Path) -> str:
    """Read GoPro / iOS MP4 ``CreationDate`` (brand-specific tag,
    distinct from the QuickTime ``CreateDate`` triple). Returned as
    the raw exiftool string — empty if the tag is missing on the
    file (which is the pre-00.095 state we want to assert against)."""
    cp = subprocess.run(
        [
            str(_get_exiftool_path()), "-s", "-s", "-s",
            "-CreationDate", str(path),
        ],
        capture_output=True, text=True, encoding="utf-8",
    )
    return cp.stdout.strip()


def test_video_rewrite_writes_creation_date(tmp_path):
    """Nelson 2026-05-28 / 00.095 regression pin: GoPro and iOS MP4
    write a brand-specific ``CreationDate`` tag (the local wall-clock
    of recording, in the camera's TZ at capture time). The bucket
    scanner reads it FIRST in its TIMESTAMP_TAGS chain — so when
    the rewriter bakes a TZ correction, ``CreationDate`` MUST be
    baked alongside the QuickTime triple or the scanner reads stale
    values and sorts the video wrong.

    Repro before 00.095: Nelson's Nepal HERO12 — Adjust TZ baked
    CreateDate/MediaCreateDate/TrackCreateDate but ``CreationDate``
    stayed in São Paulo wall-clock; Select sorted the morning Lukla
    videos as 'yesterday evening' relative to G9 photos taken
    minutes later. This test pins the fix so a future refactor
    doesn't quietly drop the ``-CreationDate=`` exiftool argument."""
    src = _make_video_with_create_date(
        tmp_path / "v.mp4", datetime(2025, 5, 12, 10, 0, 0),
    )
    new_time = datetime(2025, 5, 12, 18, 45, 0)
    rewrite_capture_time(src, new_time)

    raw = _read_video_creation_date(src)
    assert raw, (
        "CreationDate must be present after the bake — 00.095 "
        "regression: the rewriter dropped the ``-CreationDate=`` "
        "exiftool arg and the GoPro tag stayed at its pre-bake value"
    )
    parsed = datetime.strptime(
        raw.split(".")[0].split("+")[0].split("-")[0].strip(),
        "%Y:%m:%d %H:%M:%S",
    ) if " " in raw else None
    # The expected format is "YYYY:MM:DD HH:MM:SS"; some exiftool
    # versions add a TZ suffix that we strip above. Either way the
    # date portion should match new_time exactly.
    assert parsed == new_time, (
        f"CreationDate after bake = {raw!r}, expected {new_time}"
    )
