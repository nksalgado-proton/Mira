"""spec/159 (Nelson 2026-07-02) — QuickTime UTC ↔ camera-local reconciliation.

Pins the fix for the "photo and video on the same trip land on different
days" bug: MP4 ``CreateDate`` reads UTC (mvhd atom), photo
``DateTimeOriginal`` reads naive local. Without reconciliation, a Nepal
(UTC+5:45) shoot processed on a São Paulo (UTC-3) laptop lands the two
kinds on adjacent days.

The reader now:

* Passes ``-api QuickTimeUTC=1`` to ExifTool so QuickTime timestamps
  come back with a ``Z`` suffix instead of being silently re-encoded
  as machine-local.
* Parses TZ-tagged strings via :func:`_parse_timestamp_and_tz`, which
  returns ``(naive_dt, tz_offset_seconds)``.
* Shifts UTC / offset-tagged timestamps into camera-local wall clock
  using ``Settings.saved_camera_tz`` (per-model) or
  ``Settings.home_timezone`` as a fallback.
* Leaves naive timestamps (``DateTimeOriginal``) untouched, so photos
  keep their existing behaviour.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from core.exif_reader import (
    _parse_timestamp,
    _parse_timestamp_and_tz,
    read_exif_batch,
)


# ── _parse_timestamp_and_tz ───────────────────────────────────────────

def test_parse_and_tz_returns_none_offset_for_naive_wall_clock():
    dt, tz = _parse_timestamp_and_tz("2026:03:30 08:58:12")
    assert dt == datetime(2026, 3, 30, 8, 58, 12)
    assert tz is None


def test_parse_and_tz_recognises_utc_z_designator():
    dt, tz = _parse_timestamp_and_tz("2026:03:30 08:58:12Z")
    assert dt == datetime(2026, 3, 30, 8, 58, 12)
    assert tz == 0


def test_parse_and_tz_recognises_positive_colon_offset():
    dt, tz = _parse_timestamp_and_tz("2026:03:30 08:58:12+05:45")
    assert dt == datetime(2026, 3, 30, 8, 58, 12)
    assert tz == 5 * 3600 + 45 * 60


def test_parse_and_tz_recognises_negative_colon_offset():
    dt, tz = _parse_timestamp_and_tz("2026:03:30 08:58:12-03:00")
    assert dt == datetime(2026, 3, 30, 8, 58, 12)
    assert tz == -3 * 3600


def test_parse_and_tz_recognises_offset_without_colon():
    dt, tz = _parse_timestamp_and_tz("2026:03:30 08:58:12+0200")
    assert tz == 2 * 3600


def test_parse_and_tz_ignores_fractional_seconds_before_trailer():
    dt, tz = _parse_timestamp_and_tz("2026:03:30 08:58:12.123Z")
    assert dt == datetime(2026, 3, 30, 8, 58, 12)
    assert tz == 0


def test_parse_and_tz_returns_none_for_empty():
    assert _parse_timestamp_and_tz("") == (None, None)


def test_parse_returns_only_naive_datetime_backwards_compatible():
    # The historical single-return function must still work for the
    # existing callers that only want the wall clock.
    dt = _parse_timestamp("2026:03:30 08:58:12+05:45")
    assert dt == datetime(2026, 3, 30, 8, 58, 12)


# ── read_exif_batch: camera-TZ shift on TZ-tagged QuickTime dates ─────


@pytest.fixture
def tmp_files(tmp_path):
    """One placeholder photo and one placeholder video. The bytes are
    irrelevant — the ExifTool call is stubbed."""
    p_photo = tmp_path / "photo.jpg"
    p_video = tmp_path / "video.mp4"
    p_photo.write_bytes(b"jpeg-not-real")
    p_video.write_bytes(b"mp4-not-real")
    return p_photo, p_video


def _fake_exiftool_result(json_stdout: str):
    """Mimic the ``subprocess.CompletedProcess`` shape that
    ``core.proc.run`` returns."""
    class _R:
        returncode = 0
        stdout = json_stdout
        stderr = ""
    return _R()


def test_read_batch_shifts_utc_video_into_camera_local(tmp_files):
    """The Nepal-camera / SP-laptop scenario. Video's ``CreateDate``
    comes back with ``Z`` (QuickTimeUTC on); ``saved_camera_tz`` for
    the camera model = 5.75 (Nepal). Reader shifts 02:55Z into 08:40
    Nepal local — same day as the sibling photo's 07:34."""
    photo_path, video_path = tmp_files
    import json
    fake_json = json.dumps([
        {
            "SourceFile": str(photo_path),
            "Model": "HERO12 Black",
            "DateTimeOriginal": "2025:10:28 07:34:15",
        },
        {
            "SourceFile": str(video_path),
            "Model": "HERO12 Black",
            # No CreationDate — CreateDate is the mvhd UTC value.
            "CreateDate": "2025:10:28 02:55:10Z",
        },
    ])
    with patch("core.proc.run",
               return_value=_fake_exiftool_result(fake_json)), \
         patch("mira.settings.repo.SettingsRepo") as _MockRepo:
        _MockRepo.return_value.load.return_value.saved_camera_tz = {
            "HERO12 Black": 5.75,
        }
        _MockRepo.return_value.load.return_value.home_timezone = -3.0
        photos = read_exif_batch([photo_path, video_path])

    photo, video = photos
    assert photo.timestamp == datetime(2025, 10, 28, 7, 34, 15)
    # 02:55Z + 5:45 = 08:40 Nepal local — same day as the photo.
    assert video.timestamp == datetime(2025, 10, 28, 8, 40, 10)
    assert photo.timestamp.date() == video.timestamp.date()


def test_read_batch_falls_back_to_home_tz_when_camera_unknown(tmp_files):
    """No entry in ``saved_camera_tz`` for the model — reader falls
    back to ``home_timezone`` (the user's own TZ, a sensible default
    for at-home shooting)."""
    photo_path, video_path = tmp_files
    import json
    fake_json = json.dumps([
        {
            "SourceFile": str(video_path),
            "Model": "UnknownCam",
            "CreateDate": "2025:10:28 12:00:00Z",
        },
    ])
    with patch("core.proc.run",
               return_value=_fake_exiftool_result(fake_json)), \
         patch("mira.settings.repo.SettingsRepo") as _MockRepo:
        _MockRepo.return_value.load.return_value.saved_camera_tz = {}
        _MockRepo.return_value.load.return_value.home_timezone = -3.0
        photos = read_exif_batch([video_path])

    # 12:00Z + (-3h) = 09:00 São Paulo.
    assert photos[0].timestamp == datetime(2025, 10, 28, 9, 0, 0)


def test_read_batch_leaves_utc_untouched_when_no_tz_configured(tmp_files):
    """No camera TZ, no home TZ — the reader stays honest and returns
    the UTC wall clock as-is rather than guessing. Consistent with the
    pre-fix behaviour when there's genuinely no way to reconcile."""
    _photo_path, video_path = tmp_files
    import json
    fake_json = json.dumps([
        {
            "SourceFile": str(video_path),
            "Model": "UnknownCam",
            "CreateDate": "2025:10:28 12:00:00Z",
        },
    ])
    with patch("core.proc.run",
               return_value=_fake_exiftool_result(fake_json)), \
         patch("mira.settings.repo.SettingsRepo") as _MockRepo:
        _MockRepo.return_value.load.return_value.saved_camera_tz = {}
        _MockRepo.return_value.load.return_value.home_timezone = None
        photos = read_exif_batch([video_path])

    # No shift — the UTC wall clock survives verbatim.
    assert photos[0].timestamp == datetime(2025, 10, 28, 12, 0, 0)


def test_read_batch_leaves_naive_photo_dateimeoriginal_untouched(tmp_files):
    """Photos with a bare ``DateTimeOriginal`` (no TZ trailer) MUST NOT
    be shifted — they're already in camera-local wall clock and any
    shift would drift them away from photos ingested pre-fix."""
    photo_path, _video_path = tmp_files
    import json
    fake_json = json.dumps([
        {
            "SourceFile": str(photo_path),
            "Model": "HERO12 Black",
            "DateTimeOriginal": "2025:10:28 07:34:15",
        },
    ])
    with patch("core.proc.run",
               return_value=_fake_exiftool_result(fake_json)), \
         patch("mira.settings.repo.SettingsRepo") as _MockRepo:
        _MockRepo.return_value.load.return_value.saved_camera_tz = {
            "HERO12 Black": 5.75,
        }
        _MockRepo.return_value.load.return_value.home_timezone = -3.0
        photos = read_exif_batch([photo_path])

    # Verbatim — no shift applied.
    assert photos[0].timestamp == datetime(2025, 10, 28, 7, 34, 15)


def test_read_batch_prefers_creation_date_over_utc_create_date(tmp_files):
    """``CreationDate`` (local wall-clock with TZ trailer) is preferred
    over ``CreateDate`` (mvhd UTC) — the chain order at
    ``_pick_capture_timestamp`` guarantees this. Even when both are
    present, the reader uses ``CreationDate`` and no shift is needed."""
    _photo_path, video_path = tmp_files
    import json
    fake_json = json.dumps([
        {
            "SourceFile": str(video_path),
            "Model": "HERO12 Black",
            "CreationDate": "2025:10:28 08:40:10+05:45",
            "CreateDate": "2025:10:28 02:55:10Z",
        },
    ])
    with patch("core.proc.run",
               return_value=_fake_exiftool_result(fake_json)), \
         patch("mira.settings.repo.SettingsRepo") as _MockRepo:
        _MockRepo.return_value.load.return_value.saved_camera_tz = {
            "HERO12 Black": 5.75,
        }
        _MockRepo.return_value.load.return_value.home_timezone = -3.0
        photos = read_exif_batch([video_path])

    # CreationDate: 08:40+05:45. Shift into camera TZ (5.75h) → net 0.
    assert photos[0].timestamp == datetime(2025, 10, 28, 8, 40, 10)
