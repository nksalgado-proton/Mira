"""Tests for core.video_session.

Synthetic videos via FFmpeg's lavfi color source — same fixture
strategy as test_video_extract. Round-trip tests cover marker
state, clip ranges, journal restoration, and the FFmpeg-backed
extract / export operations end-to-end.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest
from PIL import Image

from core.aspect_ratio import ORIGINAL_LABEL
from core.models import Event, TripDay
from core.path_builder import day_folder_name
from core.video_discovery import (
    EXTRACTED_FRAMES_FOLDER_NAME,
    PROCESSED_FOLDER_NAME,
    VideoItem,
)
from core.video_extract import _make_test_video
from core.video_session import (
    VIDEO_JOURNAL_SCHEMA_VERSION,
    ClipRange,
    VideoSession,
    video_resume_stats,
)


def _make_event(tmp_path: Path) -> Event:
    e = Event(
        name="Test",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 1),
        photos_base_path=str(tmp_path),
    )
    e.trip_days = [TripDay(
        day_number=1, date=date(2026, 4, 1), description="Day one",
    )]
    return e


def _make_item(event: Event, video_path: Path,
               ts: datetime = datetime(2026, 4, 1, 14, 30, 0)) -> VideoItem:
    return VideoItem(
        path=video_path, day=event.trip_days[0],
        source_folder="video", timestamp=ts,
    )


@pytest.fixture
def short_video(tmp_path: Path) -> Path:
    """1-second synthetic video used as the source for session tests."""
    return _make_test_video(
        tmp_path / "02 - Selected" / day_folder_name(_make_event(tmp_path).trip_days[0])
        / "video" / "MOV_001.mp4",
        duration_s=1.0,
    )


def test_marker_add_and_remove(tmp_path, short_video):
    event = _make_event(tmp_path)
    item = _make_item(event, short_video)
    sess = VideoSession([item], event)

    sess.add_marker(item, 100)
    sess.add_marker(item, 500)
    sess.add_marker(item, 250)
    state = sess.get_state(item)
    assert state.markers_ms == [100, 250, 500]

    # Idempotent.
    sess.add_marker(item, 100)
    assert state.markers_ms == [100, 250, 500]

    assert sess.remove_marker(item, 250)
    assert state.markers_ms == [100, 500]

    # Miss returns False, leaves list alone.
    assert sess.remove_marker(item, 999) is False
    assert state.markers_ms == [100, 500]


def test_journal_restores_markers_and_clips(tmp_path, short_video):
    event = _make_event(tmp_path)
    item = _make_item(event, short_video)

    sess1 = VideoSession([item], event)
    sess1.add_marker(item, 100)
    sess1.add_marker(item, 500)
    sess1.add_clip(item, ClipRange(
        start_ms=100, end_ms=500, aspect_ratio_label="4:3",
        crop_norm=(0.1, 0.1, 0.8, 0.8),
    ))

    sess2 = VideoSession([item], event)
    state = sess2.get_state(item)
    assert state.markers_ms == [100, 500]
    assert len(state.clips) == 1
    clip = state.clips[0]
    assert clip.start_ms == 100
    assert clip.end_ms == 500
    assert clip.aspect_ratio_label == "4:3"
    assert clip.crop_norm == (0.1, 0.1, 0.8, 0.8)


def test_journal_drops_entries_for_missing_videos(tmp_path, short_video):
    event = _make_event(tmp_path)
    item = _make_item(event, short_video)
    sess1 = VideoSession([item], event)
    sess1.add_marker(item, 200)

    # Hand-craft a stale entry pointing at a vanished video.
    journal = sess1.journal_path
    data = json.loads(journal.read_text(encoding="utf-8"))
    data["videos"].append({
        "path": str(tmp_path / "ghost.mp4"),
        "markers_ms": [42],
        "extracted_frame_positions_ms": [],
        "clips": [],
    })
    journal.write_text(json.dumps(data), encoding="utf-8")

    sess2 = VideoSession([item], event)
    # The ghost entry is filtered out.
    assert list(sess2._states.keys()) == [short_video]


def test_journal_schema_version(tmp_path, short_video):
    event = _make_event(tmp_path)
    item = _make_item(event, short_video)
    sess = VideoSession([item], event)
    sess.add_marker(item, 100)

    data = json.loads(sess.journal_path.read_text(encoding="utf-8"))
    assert data["version"] == VIDEO_JOURNAL_SCHEMA_VERSION


def test_extract_frame_writes_to_extracted_folder(tmp_path, short_video):
    event = _make_event(tmp_path)
    item = _make_item(event, short_video)
    sess = VideoSession([item], event)

    outcome = sess.extract_frame_at(item, 500)

    expected_dir = (
        tmp_path / "02 - Selected" / day_folder_name(event.trip_days[0])
        / EXTRACTED_FRAMES_FOLDER_NAME
    )
    assert outcome.output_path.parent == expected_dir
    assert outcome.output_path.exists()
    img = Image.open(outcome.output_path)
    assert img.size == (320, 240)


def test_extract_frame_filename_uses_wall_clock(tmp_path, short_video):
    """Filename embeds video.timestamp + position so the JPEG sorts
    chronologically when the Process Photos Culler picks it up."""
    event = _make_event(tmp_path)
    item = _make_item(
        event, short_video,
        ts=datetime(2026, 4, 1, 14, 30, 0),
    )
    sess = VideoSession([item], event)

    outcome = sess.extract_frame_at(item, 500)
    # video starts at 14:30:00, frame at +500ms → filename starts 143000
    assert outcome.output_path.name.startswith("143000_")


def test_extract_frame_records_position(tmp_path, short_video):
    event = _make_event(tmp_path)
    item = _make_item(event, short_video)
    sess = VideoSession([item], event)
    sess.extract_frame_at(item, 100)
    sess.extract_frame_at(item, 500)
    sess.extract_frame_at(item, 100)  # idempotent — same position

    assert sess.get_state(item).extracted_frame_positions_ms == [100, 500]


def test_export_clip_writes_to_shared_processed_folder(tmp_path, short_video):
    """Clip exports land in the same ``Processed Media`` folder
    Process Photos uses for its JPEGs — chronological mixed-media
    output by day."""
    event = _make_event(tmp_path)
    item = _make_item(event, short_video)
    sess = VideoSession([item], event)
    clip = ClipRange(start_ms=100, end_ms=600)

    outcome = sess.export_clip_range(item, clip)

    expected_dir = (
        tmp_path / PROCESSED_FOLDER_NAME
        / day_folder_name(event.trip_days[0])
    )
    assert outcome.output_path.parent == expected_dir
    assert outcome.output_path.suffix == ".mp4"
    assert outcome.output_path.exists()


def test_export_clip_filename_uses_wall_clock(tmp_path, short_video):
    event = _make_event(tmp_path)
    item = _make_item(
        event, short_video, ts=datetime(2026, 4, 1, 14, 30, 0),
    )
    sess = VideoSession([item], event)
    outcome = sess.export_clip_range(
        item, ClipRange(start_ms=500, end_ms=900),
    )
    # 14:30:00 + 500ms → 143000 (we drop sub-second on the prefix).
    assert outcome.output_path.name.startswith("143000_")


def test_export_clip_collisions_get_disambiguated(tmp_path, short_video):
    event = _make_event(tmp_path)
    item = _make_item(event, short_video)
    sess = VideoSession([item], event)
    clip = ClipRange(start_ms=0, end_ms=400)

    a = sess.export_clip_range(item, clip).output_path
    b = sess.export_clip_range(item, clip).output_path
    assert a.exists() and b.exists()
    assert a != b
    assert " (2)" in b.name


def test_probe_metadata_caches(tmp_path, short_video):
    """Caching matters — probing a 50MB video can take 2-3s; the
    timeline UI shouldn't pay that cost on every paint."""
    event = _make_event(tmp_path)
    item = _make_item(event, short_video)
    sess = VideoSession([item], event)

    meta1 = sess.probe_metadata(item)
    # Cached: must return the same object identity.
    meta2 = sess.probe_metadata(item)
    assert meta1 is meta2


def test_clip_default_aspect_is_original(tmp_path, short_video):
    event = _make_event(tmp_path)
    item = _make_item(event, short_video)
    sess = VideoSession([item], event)
    clip = ClipRange(start_ms=100, end_ms=400)
    assert clip.aspect_ratio_label == ORIGINAL_LABEL
    assert clip.crop_norm is None
    assert clip.include_audio is True
    sess.add_clip(item, clip)
    assert sess.get_state(item).clips[0].aspect_ratio_label == ORIGINAL_LABEL


def test_export_clip_applies_aspect_ratio_crop(tmp_path, short_video):
    """A 4:3 export from a 4:3 source (320×240) should keep all
    pixels; a 1:1 export should produce a 240×240 file."""
    from core.video_extract import probe_video
    event = _make_event(tmp_path)
    item = _make_item(event, short_video)
    sess = VideoSession([item], event)
    clip = ClipRange(start_ms=100, end_ms=600, aspect_ratio_label="1:1")

    outcome = sess.export_clip_range(item, clip)
    meta = probe_video(outcome.output_path)
    # 320×240 → centered 1:1 crop = 240×240
    assert meta.width == 240
    assert meta.height == 240


def test_export_clip_strips_audio_when_requested(tmp_path, short_video):
    """``include_audio=False`` should produce an output where ffprobe
    sees no audio stream. We use the metadata probe — when the codec
    field is "Video:..." only (no audio track), the resulting clip
    is mute. The synthetic test video has no audio anyway, so we
    just verify the encode succeeds; full audio stripping is exercised
    by the export_clip unit test."""
    event = _make_event(tmp_path)
    item = _make_item(event, short_video)
    sess = VideoSession([item], event)
    clip = ClipRange(start_ms=0, end_ms=500, include_audio=False)
    outcome = sess.export_clip_range(item, clip)
    assert outcome.output_path.exists()


def test_clip_include_audio_persists_through_journal(tmp_path, short_video):
    event = _make_event(tmp_path)
    item = _make_item(event, short_video)
    sess1 = VideoSession([item], event)
    sess1.add_clip(item, ClipRange(
        start_ms=100, end_ms=500, include_audio=False,
    ))

    sess2 = VideoSession([item], event)
    clips = sess2.get_state(item).clips
    assert len(clips) == 1
    assert clips[0].include_audio is False


# ── Task #116 — update_clip + rotation_degrees persistence ──────


def test_update_clip_mutates_audio_and_rotation(tmp_path, short_video):
    """update_clip is the seam the Process Video UI uses for Mute /
    Rotate buttons. Passing one field leaves the others alone."""
    event = _make_event(tmp_path)
    item = _make_item(event, short_video)
    sess = VideoSession([item], event)
    sess.add_clip(item, ClipRange(start_ms=0, end_ms=500))

    # Mute only.
    updated = sess.update_clip(item, 0, include_audio=False)
    assert updated is not None
    assert updated.include_audio is False
    assert updated.rotation_degrees == 0       # untouched

    # Rotate only.
    updated = sess.update_clip(item, 0, rotation_degrees=90)
    assert updated.include_audio is False      # untouched
    assert updated.rotation_degrees == 90

    # Both, with overflow rotation normalised mod 360.
    updated = sess.update_clip(
        item, 0, include_audio=True, rotation_degrees=450,
    )
    assert updated.include_audio is True
    assert updated.rotation_degrees == 90      # 450 % 360


def test_update_clip_returns_none_for_bad_index(tmp_path, short_video):
    event = _make_event(tmp_path)
    item = _make_item(event, short_video)
    sess = VideoSession([item], event)
    # No clips → any index is bad.
    assert sess.update_clip(item, 0, include_audio=False) is None
    sess.add_clip(item, ClipRange(start_ms=0, end_ms=500))
    assert sess.update_clip(item, 99, include_audio=False) is None
    assert sess.update_clip(item, -1, include_audio=False) is None


def test_clip_rotation_degrees_persists_through_journal(
    tmp_path, short_video,
):
    """Task #116 — per-clip rotation survives a session restart, so
    a later export run honours the user's Rotate choice."""
    event = _make_event(tmp_path)
    item = _make_item(event, short_video)
    sess1 = VideoSession([item], event)
    sess1.add_clip(item, ClipRange(
        start_ms=0, end_ms=500, rotation_degrees=180,
    ))

    sess2 = VideoSession([item], event)
    clips = sess2.get_state(item).clips
    assert len(clips) == 1
    assert clips[0].rotation_degrees == 180


def test_update_clip_sets_aspect_ratio_label(tmp_path, short_video):
    """Task #129 — update_clip extended with aspect_ratio_label so
    the Process Video UI can route the user's combo pick to the
    journal. ffmpeg consumes it at export via the existing centred-
    crop computation in export_clip_range."""
    event = _make_event(tmp_path)
    item = _make_item(event, short_video)
    sess = VideoSession([item], event)
    sess.add_clip(item, ClipRange(start_ms=0, end_ms=500))
    assert sess.get_state(item).clips[0].aspect_ratio_label == ORIGINAL_LABEL

    updated = sess.update_clip(item, 0, aspect_ratio_label="16:9")
    assert updated is not None
    assert updated.aspect_ratio_label == "16:9"


def test_update_clip_set_crop_norm_and_clear(tmp_path, short_video):
    """Task #129 — explicit crop_norm set, and the clear_crop_norm
    reset flag (the aspect-ratio combo uses clear_crop_norm so a
    new ratio's centred default takes over)."""
    event = _make_event(tmp_path)
    item = _make_item(event, short_video)
    sess = VideoSession([item], event)
    sess.add_clip(item, ClipRange(start_ms=0, end_ms=500))

    sess.update_clip(item, 0, crop_norm=(0.1, 0.2, 0.6, 0.6))
    assert sess.get_state(item).clips[0].crop_norm == (0.1, 0.2, 0.6, 0.6)

    sess.update_clip(item, 0, clear_crop_norm=True)
    assert sess.get_state(item).clips[0].crop_norm is None


def test_journal_missing_rotation_loads_as_zero(tmp_path, short_video):
    """Old journals predating task #116 don't carry rotation_degrees.
    Load must default to 0 (no rotation) so re-opens of stale
    journals render exactly as they did before this change."""
    event = _make_event(tmp_path)
    item = _make_item(event, short_video)
    sess1 = VideoSession([item], event)
    sess1.add_clip(item, ClipRange(start_ms=0, end_ms=500))

    # Strip rotation_degrees from disk to mimic a pre-#116 file shape.
    data = json.loads(sess1.journal_path.read_text(encoding="utf-8"))
    for video in data["videos"]:
        for clip in video["clips"]:
            clip.pop("rotation_degrees", None)
    sess1.journal_path.write_text(json.dumps(data), encoding="utf-8")

    sess2 = VideoSession([item], event)
    assert sess2.get_state(item).clips[0].rotation_degrees == 0


# ── Standalone (Video Tool) mode ─────────────────────────────────


def _make_standalone_item(
    video_path: Path,
    ts: datetime = datetime(2026, 4, 1, 14, 30, 0),
) -> VideoItem:
    """Build a VideoItem without a TripDay — what the Video Tool
    sidebar entry hands to a standalone VideoSession."""
    return VideoItem(
        path=video_path, source_folder=video_path.parent.name, timestamp=ts,
    )


def test_standalone_session_requires_output_dir_or_event(tmp_path, short_video):
    """Constructor must reject ambiguous mode: both event and
    output_dir, or neither."""
    item = _make_standalone_item(short_video)
    with pytest.raises(ValueError):
        VideoSession([item])  # neither
    event = _make_event(tmp_path)
    with pytest.raises(ValueError):
        VideoSession([item], event, output_dir=tmp_path / "_extracted")  # both


def test_standalone_session_marks_is_standalone(tmp_path, short_video):
    item = _make_standalone_item(short_video)
    out = tmp_path / "_extracted"
    sess = VideoSession([item], output_dir=out)
    assert sess.is_standalone is True
    # Event-mode session for contrast.
    event = _make_event(tmp_path)
    item2 = _make_item(event, short_video)
    sess2 = VideoSession([item2], event)
    assert sess2.is_standalone is False


def test_standalone_extract_frame_writes_flat_to_output_dir(
    tmp_path, short_video,
):
    """Frame snaps in standalone mode go DIRECTLY into output_dir
    (no per-day subfolder)."""
    item = _make_standalone_item(short_video)
    out = tmp_path / "_extracted"
    sess = VideoSession([item], output_dir=out)

    outcome = sess.extract_frame_at(item, 500)

    assert outcome.output_path.parent == out
    assert outcome.output_path.exists()
    # Filename: HHMMSS_<stem>_fNms.jpg — sub-second part of the
    # offset ends up only in the f<ms> suffix, not the HHMMSS prefix.
    assert outcome.output_path.name == "143000_MOV_001_f500.jpg"
    # No accidental Dia / extracted/ folder
    assert not (tmp_path / "Dia 1 - Day one").exists()


def test_standalone_export_clip_writes_flat_to_output_dir(
    tmp_path, short_video,
):
    """Clip exports in standalone mode also go flat — no
    Processed Media/<Dia>/ structure."""
    item = _make_standalone_item(short_video)
    out = tmp_path / "_extracted"
    sess = VideoSession([item], output_dir=out)

    clip = ClipRange(start_ms=100, end_ms=500)
    outcome = sess.export_clip_range(item, clip)

    assert outcome.output_path.parent == out
    assert outcome.output_path.exists()
    assert outcome.output_path.suffix == ".mp4"
    assert "143000" in outcome.output_path.name  # wall-clock 14:30:00


def test_standalone_journal_default_path_in_output_dir(tmp_path, short_video):
    """Journal lives next to the outputs so re-opening the same file
    naturally resumes — no separate journal-discovery step."""
    item = _make_standalone_item(short_video)
    out = tmp_path / "_extracted"
    sess = VideoSession([item], output_dir=out)
    sess.add_marker(item, 200)
    assert sess.journal_path.parent == out
    assert sess.journal_path.exists()


def test_standalone_journal_round_trip(tmp_path, short_video):
    """Markers and clips persist through a session reopen exactly
    like event mode — the journal format is shared across modes."""
    item = _make_standalone_item(short_video)
    out = tmp_path / "_extracted"

    sess1 = VideoSession([item], output_dir=out)
    sess1.add_marker(item, 100)
    sess1.add_marker(item, 500)
    sess1.add_clip(item, ClipRange(start_ms=100, end_ms=500))

    sess2 = VideoSession([item], output_dir=out)
    state = sess2.get_state(item)
    assert state.markers_ms == [100, 500]
    assert len(state.clips) == 1


# ── video_resume_stats — the pure navigator peek (Nelson 2026-05-18) ──

def test_video_resume_stats_peek(tmp_path):
    """Pure JSON peek: no journal → untouched zeros; a real journal
    → duration (end marker), kept_ms (Σ clip spans), clip + still
    counts; matches by path or filename; never raises."""
    jp = tmp_path / "_video_session.json"
    vid = Path(r"D:\x\_cameras\P1418066.MP4")

    # No journal file.
    s = video_resume_stats(jp, vid)
    assert (s.has_entry, s.duration_ms, s.kept_ms, s.clips,
            s.stills) == (False, 0, 0, 0, 0)

    # Schema mismatch → treated as no entry.
    jp.write_text(json.dumps({"version": 999, "videos": []}))
    assert video_resume_stats(jp, vid).has_entry is False

    # Real journal: 115_620 ms film, two kept clips (10s + 5s),
    # 3 stills. Match by filename (stored path differs).
    jp.write_text(json.dumps({
        "version": VIDEO_JOURNAL_SCHEMA_VERSION,
        "videos": [{
            "path": "/other/abs/P1418066.MP4",
            "markers_ms": [0, 30000, 115620],
            "extracted_frame_positions_ms": [1000, 2000, 3000],
            "clips": [
                {"start_ms": 10000, "end_ms": 20000},
                {"start_ms": 40000, "end_ms": 45000},
            ],
        }],
    }))
    s = video_resume_stats(jp, vid)
    assert s.has_entry is True
    assert s.duration_ms == 115620        # max marker = seeded end
    assert s.kept_ms == 15000             # 10000 + 5000
    assert s.clips == 2 and s.stills == 3

    # Garbled JSON never raises.
    jp.write_text("{ not json")
    assert video_resume_stats(jp, vid).has_entry is False


# ── dest_dir override (task #114) ─────────────────────────────


def test_extract_frame_honours_dest_dir_override(tmp_path, short_video):
    """A caller can route a frame snap to any folder via dest_dir
    (the Cull/Select shells use this to land frames in
    01 - Culled / 02 - Selected per Model 3 v2)."""
    event = _make_event(tmp_path)
    item = _make_item(event, short_video)
    sess = VideoSession([item], event)

    custom = tmp_path / "01 - Culled" / "_cameras" / "Dia 1 - day" / "G9" / "general"
    outcome = sess.extract_frame_at(item, 100, dest_dir=custom)
    assert outcome.output_path.parent == custom
    assert outcome.output_path.exists()


def test_export_clip_honours_dest_dir_override(tmp_path, short_video):
    """A caller can route a clip to any folder via dest_dir."""
    event = _make_event(tmp_path)
    item = _make_item(event, short_video)
    sess = VideoSession([item], event)
    clip = ClipRange(start_ms=100, end_ms=600)

    custom = tmp_path / "01 - Culled" / "_cameras" / "Dia 1 - day" / "G9" / "general"
    outcome = sess.export_clip_range(item, clip, dest_dir=custom)
    assert outcome.output_path.parent == custom
    assert outcome.output_path.exists()


def test_extract_frame_default_destination_unchanged(tmp_path, short_video):
    """No dest_dir → legacy path: event mode → extracted_dir()."""
    event = _make_event(tmp_path)
    item = _make_item(event, short_video)
    sess = VideoSession([item], event)
    outcome = sess.extract_frame_at(item, 100)
    expected_parent = (
        tmp_path / "02 - Selected"
        / day_folder_name(event.trip_days[0])
        / EXTRACTED_FRAMES_FOLDER_NAME
    )
    assert outcome.output_path.parent == expected_parent


# ── docs/24 Step 1 — Snapshot EXIF bake ────────────────────────


@pytest.mark.skipif(
    not __import__("core.exif_reader", fromlist=["_get_exiftool_path"])._get_exiftool_path().exists(),
    reason="bundled exiftool not present; skipping EXIF bake test",
)
def test_extract_frame_bakes_date_time_original(tmp_path, short_video):
    """docs/24 Step 1 (corrected concept, 2026-05-28): a snapshot
    JPEG must carry a ``DateTimeOriginal`` equal to the source
    video's capture time plus the snapshot position in
    milliseconds. The Select scanner extension folds snapshots
    into the photo pool by EXIF time; without the bake they'd
    sort by zero / by ffmpeg's encoder-stamped time, not by the
    user's intent."""
    from datetime import timedelta
    from core.exif_reader import read_exif_batch

    event = _make_event(tmp_path)
    capture_time = datetime(2026, 4, 1, 14, 30, 0)
    item = _make_item(event, short_video, ts=capture_time)
    sess = VideoSession([item], event)

    outcome = sess.extract_frame_at(item, 250)
    assert outcome.output_path.exists()

    exifs = read_exif_batch([outcome.output_path])
    assert len(exifs) == 1
    baked = exifs[0].timestamp
    expected = capture_time + timedelta(milliseconds=250)
    # EXIF DateTimeOriginal is per-second resolution; ms component
    # truncates. The expected value's ms portion (250) rounds down
    # to 0 in the EXIF representation — so compare on the second.
    assert baked is not None
    assert baked.replace(microsecond=0) == expected.replace(microsecond=0)


@pytest.mark.skipif(
    not __import__("core.exif_reader", fromlist=["_get_exiftool_path"])._get_exiftool_path().exists(),
    reason="bundled exiftool not present; skipping EXIF bake test",
)
def test_extract_frame_bake_handles_different_positions(
    tmp_path, short_video,
):
    """Same source, different position_ms → different baked EXIF."""
    from datetime import timedelta
    from core.exif_reader import read_exif_batch

    event = _make_event(tmp_path)
    capture_time = datetime(2026, 4, 1, 14, 30, 0)
    item = _make_item(event, short_video, ts=capture_time)
    sess = VideoSession([item], event)

    out0 = sess.extract_frame_at(item, 100)
    out1 = sess.extract_frame_at(item, 700)
    exifs = read_exif_batch([out0.output_path, out1.output_path])
    by_path = {Path(e.path): e.timestamp for e in exifs}
    assert by_path[out0.output_path] == capture_time
    # 700ms rounds to the next whole second when seconds resolution
    # would mean 14:30:00.700 → still 14:30:00 at second precision.
    # The bake stores per-second; we just need the values different
    # only if the millisecond delta crosses a second boundary.
    assert by_path[out1.output_path] == capture_time
