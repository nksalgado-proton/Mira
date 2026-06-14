"""Tests for core.bucket_scanner."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.bucket_scanner import (
    BucketScannerConfig,
    IndividualPhoto,
    SourceKind,
    parse_duration_seconds,
    _parse_orientation,
    _parse_timestamp,
    annotate_clusters,
    scan,
    scan_camera,
    scan_phone,
)
from core.import_pipeline import RawExifEntry


# ─────────────────────────────────────────────────────────────────────────
# Helpers — synthetic RawExifEntry builders
# ─────────────────────────────────────────────────────────────────────────


def _entry(name: str, **exif) -> RawExifEntry:
    """Build a RawExifEntry with a path and arbitrary EXIF kwargs."""
    return RawExifEntry(path=Path(name), exif=dict(exif))


def _ts(s: str) -> str:
    """Format a timestamp string in EXIF format."""
    return s


# ─────────────────────────────────────────────────────────────────────────
# _parse_timestamp
# ─────────────────────────────────────────────────────────────────────────


def test_parse_timestamp_basic_format():
    dt = _parse_timestamp("2026:04:14 13:35:51")
    assert dt == datetime(2026, 4, 14, 13, 35, 51)


def test_parse_timestamp_with_subsec():
    dt = _parse_timestamp("2026:04:14 13:35:51.152")
    assert dt == datetime(2026, 4, 14, 13, 35, 51, 152000)


def test_parse_timestamp_with_offset_returns_naive():
    """Offset is parsed but dropped — we treat trip timestamps as wall-clock
    to avoid offset-naive vs offset-aware comparison errors when sorting
    mixed-source items (HEIC vs MP4)."""
    dt = _parse_timestamp("2026:04:14 13:35:51-06:00")
    assert dt is not None
    assert dt.year == 2026
    assert dt.tzinfo is None


def test_parse_timestamp_with_subsec_and_offset():
    dt = _parse_timestamp("2026:04:14 13:35:51.152-06:00")
    assert dt is not None
    assert dt.microsecond == 152000
    assert dt.tzinfo is None


def test_parsed_timestamps_compare_across_tz_styles():
    """Regression: a sort that mixes timestamps from with-offset and
    without-offset EXIF strings must not raise. Photo EXIF often lacks
    offset; video EXIF often includes one. They must coexist after parse."""
    a = _parse_timestamp("2026:04:14 13:35:51-06:00")
    b = _parse_timestamp("2026:04:14 13:36:00")  # no offset
    assert a is not None and b is not None
    # Neither comparison should raise
    assert a < b
    assert sorted([b, a]) == [a, b]


def test_parse_timestamp_dash_format():
    dt = _parse_timestamp("2026-04-14 13:35:51")
    assert dt == datetime(2026, 4, 14, 13, 35, 51)


def test_parse_timestamp_none_or_empty():
    assert _parse_timestamp(None) is None
    assert _parse_timestamp("") is None
    assert _parse_timestamp("   ") is None


def test_parse_timestamp_garbage():
    assert _parse_timestamp("not a date") is None
    assert _parse_timestamp("12345") is None


# ─────────────────────────────────────────────────────────────────────────
# parse_duration_seconds
# ─────────────────────────────────────────────────────────────────────────


def testparse_duration_seconds_with_suffix():
    assert parse_duration_seconds("13.93 s") == pytest.approx(13.93)


def testparse_duration_seconds_hms():
    assert parse_duration_seconds("0:00:13.93") == pytest.approx(13.93)
    assert parse_duration_seconds("1:00:00") == 3600.0
    assert parse_duration_seconds("0:30") == 30.0


def testparse_duration_seconds_bare_number():
    assert parse_duration_seconds("13.93") == pytest.approx(13.93)
    assert parse_duration_seconds(13.93) == 13.93
    assert parse_duration_seconds(14) == 14.0


def testparse_duration_seconds_garbage():
    assert parse_duration_seconds(None) is None
    assert parse_duration_seconds("") is None
    assert parse_duration_seconds("not a duration") is None


# ─────────────────────────────────────────────────────────────────────────
# _parse_orientation — handles exiftool's text form
# ─────────────────────────────────────────────────────────────────────────


def test_parse_orientation_accepts_int():
    assert _parse_orientation(1) == 1
    assert _parse_orientation(6) == 6


def test_parse_orientation_accepts_numeric_string():
    assert _parse_orientation("1") == 1
    assert _parse_orientation("6") == 6


def test_parse_orientation_accepts_exiftool_text_normal():
    """The case Nelson hit on real Lumix RAW: ExifTool returns
    'Horizontal (normal)' instead of the numeric 1, and a raw int()
    blew up. _parse_orientation maps the text back to its EXIF code."""
    assert _parse_orientation("Horizontal (normal)") == 1


def test_parse_orientation_accepts_other_exiftool_text_forms():
    cases = {
        "Mirror horizontal": 2,
        "Rotate 180": 3,
        "Mirror vertical": 4,
        "Rotate 90 CW": 6,
        "Rotate 270 CW": 8,
    }
    for text, expected in cases.items():
        assert _parse_orientation(text) == expected, text


def test_parse_orientation_case_insensitive():
    assert _parse_orientation("ROTATE 90 CW") == 6
    assert _parse_orientation("rotate 90 cw") == 6


def test_parse_orientation_unknown_falls_back_to_one():
    assert _parse_orientation("totally weird value") == 1
    assert _parse_orientation(None) == 1
    assert _parse_orientation("") == 1


# ─────────────────────────────────────────────────────────────────────────
# annotate_clusters
# ─────────────────────────────────────────────────────────────────────────


def _ind(name: str, ts: datetime, make="Apple", model="iPhone 11") -> IndividualPhoto:
    return IndividualPhoto(path=Path(name), timestamp=ts, make=make, model=model)


def test_annotate_clusters_empty():
    assert annotate_clusters([], BucketScannerConfig()) == []


def test_annotate_clusters_single_photo_no_cluster():
    cfg = BucketScannerConfig(cluster_window_seconds=300, cluster_min_size=3)
    photos = [_ind("a.jpg", datetime(2026, 4, 14, 13, 0, 0))]
    result = annotate_clusters(photos, cfg)
    assert len(result) == 1
    assert result[0].cluster_id is None


def test_annotate_clusters_below_min_size():
    """2 photos in window -> no cluster annotation when min_size=3."""
    cfg = BucketScannerConfig(cluster_window_seconds=300, cluster_min_size=3)
    base = datetime(2026, 4, 14, 13, 0, 0)
    photos = [
        _ind("a.jpg", base),
        _ind("b.jpg", base + timedelta(seconds=10)),
    ]
    result = annotate_clusters(photos, cfg)
    assert all(p.cluster_id is None for p in result)


def test_annotate_clusters_at_min_size():
    cfg = BucketScannerConfig(cluster_window_seconds=300, cluster_min_size=3)
    base = datetime(2026, 4, 14, 13, 0, 0)
    photos = [
        _ind("a.jpg", base),
        _ind("b.jpg", base + timedelta(seconds=10)),
        _ind("c.jpg", base + timedelta(seconds=20)),
    ]
    result = annotate_clusters(photos, cfg)
    assert all(p.cluster_id is not None for p in result)
    assert all(p.cluster_size == 3 for p in result)
    # Position is 1-based and sorted by time
    sorted_result = sorted(result, key=lambda p: p.timestamp)
    assert [p.cluster_position for p in sorted_result] == [1, 2, 3]
    # All in same cluster
    assert len({p.cluster_id for p in result}) == 1


def test_annotate_clusters_two_separate_clusters():
    cfg = BucketScannerConfig(cluster_window_seconds=60, cluster_min_size=3)
    base = datetime(2026, 4, 14, 13, 0, 0)
    photos = [
        # Cluster A — within 60s
        _ind("a1.jpg", base),
        _ind("a2.jpg", base + timedelta(seconds=10)),
        _ind("a3.jpg", base + timedelta(seconds=20)),
        # Gap > 60s
        _ind("b1.jpg", base + timedelta(seconds=200)),
        _ind("b2.jpg", base + timedelta(seconds=210)),
        _ind("b3.jpg", base + timedelta(seconds=220)),
    ]
    result = annotate_clusters(photos, cfg)
    # Two distinct cluster IDs
    cluster_ids = {p.cluster_id for p in result}
    assert len(cluster_ids) == 2
    assert all(p.cluster_size == 3 for p in result)


def test_annotate_clusters_unsorted_input_handled():
    cfg = BucketScannerConfig(cluster_window_seconds=300, cluster_min_size=3)
    base = datetime(2026, 4, 14, 13, 0, 0)
    # Pass photos out of timestamp order
    photos = [
        _ind("c.jpg", base + timedelta(seconds=20)),
        _ind("a.jpg", base),
        _ind("b.jpg", base + timedelta(seconds=10)),
    ]
    result = annotate_clusters(photos, cfg)
    # All clustered
    assert all(p.cluster_id is not None for p in result)
    # Position assignments are by chronological order
    sorted_by_ts = sorted(result, key=lambda p: p.timestamp)
    assert sorted_by_ts[0].cluster_position == 1
    assert sorted_by_ts[2].cluster_position == 3


def test_annotate_clusters_records_distinct_sources():
    cfg = BucketScannerConfig(cluster_window_seconds=300, cluster_min_size=3)
    base = datetime(2026, 4, 14, 17, 35, 0)
    photos = [
        _ind("a.jpg", base, make="Apple", model="iPhone 11"),
        _ind("b.jpg", base + timedelta(seconds=5),
             make="Apple", model="iPhone 12"),
        _ind("c.jpg", base + timedelta(seconds=10),
             make="Apple", model="iPhone 11"),
    ]
    result = annotate_clusters(photos, cfg)
    sources = result[0].cluster_sources
    assert ("Apple", "iPhone 11") in sources
    assert ("Apple", "iPhone 12") in sources
    assert len(sources) == 2  # distinct only


def test_annotate_clusters_photos_without_timestamp_passed_through():
    cfg = BucketScannerConfig(cluster_window_seconds=300, cluster_min_size=3)
    photos = [
        IndividualPhoto(path=Path("orphan.jpg"), timestamp=None),
        _ind("a.jpg", datetime(2026, 4, 14, 13, 0, 0)),
    ]
    result = annotate_clusters(photos, cfg)
    paths = {p.path.name for p in result}
    assert "orphan.jpg" in paths
    # Orphan has no cluster
    orphan = next(p for p in result if p.path.name == "orphan.jpg")
    assert orphan.cluster_id is None


def test_annotate_clusters_window_boundary_inclusive():
    """Photos exactly at the window boundary are still part of the cluster."""
    cfg = BucketScannerConfig(cluster_window_seconds=300, cluster_min_size=3)
    base = datetime(2026, 4, 14, 13, 0, 0)
    photos = [
        _ind("a.jpg", base),
        _ind("b.jpg", base + timedelta(seconds=150)),
        _ind("c.jpg", base + timedelta(seconds=300)),  # exactly at boundary
    ]
    result = annotate_clusters(photos, cfg)
    assert all(p.cluster_id is not None for p in result)


# ─────────────────────────────────────────────────────────────────────────
# scan_phone — empty / trivial
# ─────────────────────────────────────────────────────────────────────────


def test_scan_phone_empty():
    result = scan_phone([])
    assert result.source_kind == SourceKind.PHONE
    assert result.total_items == 0


def test_scan_phone_single_photo_to_individual():
    entries = [
        _entry("IMG_001.HEIC",
               Make="Apple", Model="iPhone 11",
               DateTimeOriginal="2026:04:14 13:00:00"),
    ]
    result = scan_phone(entries)
    assert len(result.individuals) == 1
    assert result.individuals[0].path.name == "IMG_001.HEIC"
    assert result.individuals[0].make == "Apple"
    assert result.individuals[0].cluster_id is None


# ─────────────────────────────────────────────────────────────────────────
# scan_phone — Live Photo pairing
# ─────────────────────────────────────────────────────────────────────────


def test_scan_phone_live_photo_pair_basename_match_and_short_duration():
    entries = [
        _entry("IMG_001.HEIC",
               DateTimeOriginal="2026:04:14 13:00:00.000"),
        _entry("IMG_001.MOV",
               CreateDate="2026:04:14 13:00:00.500",
               Duration="2.5 s"),
    ]
    result = scan_phone(entries)
    # MVP: Live Photo pair was detected (counter incremented), still
    # merged into individuals, motion claimed as paired and never
    # surfaced. The user-facing live_photos list stays empty so nothing
    # is double-counted.
    assert result.live_photo_pairs_merged == 1
    assert len(result.live_photos) == 0
    assert any(i.path.name == "IMG_001.HEIC" for i in result.individuals)
    assert all(v.path.name != "IMG_001.MOV" for v in result.videos)
    assert all(c.path.name != "IMG_001.MOV" for c in result.motion_clips)


def test_scan_phone_long_video_with_matching_basename_is_not_live_photo():
    """MOV with same basename but duration > 4s is NOT a Live Photo."""
    entries = [
        _entry("IMG_001.HEIC", DateTimeOriginal="2026:04:14 13:00:00"),
        _entry("IMG_001.MOV", CreateDate="2026:04:14 13:00:00",
               Duration="14 s"),
    ]
    result = scan_phone(entries)
    assert len(result.live_photos) == 0
    # HEIC -> individual; MOV -> video
    assert len(result.individuals) == 1
    assert len(result.videos) == 1


def test_scan_phone_live_photo_rejected_when_timestamps_far_apart():
    entries = [
        _entry("IMG_001.HEIC", DateTimeOriginal="2026:04:14 13:00:00"),
        _entry("IMG_001.MOV", CreateDate="2026:04:14 14:00:00",
               Duration="2 s"),
    ]
    result = scan_phone(entries)
    assert len(result.live_photos) == 0


def test_scan_phone_video_without_matching_still():
    entries = [
        _entry("VID_999.MOV", CreateDate="2026:04:14 13:00:00",
               Duration="20 s"),
    ]
    result = scan_phone(entries)
    assert len(result.videos) == 1
    assert len(result.live_photos) == 0
    assert len(result.motion_clips) == 0


def test_scan_phone_short_orphan_video_routes_to_motion_clips():
    """A short video without a paired still is an orphaned Live Photo motion."""
    entries = [
        _entry("VID_001.MP4", CreateDate="2026:04:14 13:00:00",
               Duration="2.5 s"),
    ]
    result = scan_phone(entries)
    assert len(result.motion_clips) == 1
    assert len(result.videos) == 0
    assert result.motion_clips[0].duration_s == pytest.approx(2.5)


def test_scan_phone_long_orphan_video_routes_to_videos():
    """A real video (long, no pair) goes to the Video bucket, not motion_clips."""
    entries = [
        _entry("VID_REAL.MP4", CreateDate="2026:04:14 13:00:00",
               Duration="35 s"),
    ]
    result = scan_phone(entries)
    assert len(result.videos) == 1
    assert len(result.motion_clips) == 0


def test_scan_phone_motion_clips_bucket_independent_of_extension():
    """Both MP4 and MOV under the threshold count as motion_clips. The
    distinction is duration, not extension — Google Photos transcodes
    iPhone Live Photo MOVs to MP4 on shared-album upload."""
    entries = [
        _entry("A.MP4", CreateDate="2026:04:14 13:00:00", Duration="2.5 s"),
        _entry("B.MOV", CreateDate="2026:04:14 13:00:00", Duration="1.0 s"),
    ]
    result = scan_phone(entries)
    assert len(result.motion_clips) == 2
    assert len(result.videos) == 0


def test_scan_phone_video_at_threshold_goes_to_motion_clips():
    """Boundary: duration exactly equal to threshold is motion_clip (<=)."""
    entries = [
        _entry("VID_BOUNDARY.MP4", CreateDate="2026:04:14 13:00:00",
               Duration="4.0 s"),
    ]
    result = scan_phone(entries)
    assert len(result.motion_clips) == 1
    assert len(result.videos) == 0


def test_scan_phone_video_just_over_threshold_goes_to_videos():
    """Boundary: 0.01s over threshold goes to videos."""
    entries = [
        _entry("VID_REAL.MP4", CreateDate="2026:04:14 13:00:00",
               Duration="4.01 s"),
    ]
    result = scan_phone(entries)
    assert len(result.motion_clips) == 0
    assert len(result.videos) == 1


def test_scan_phone_video_without_duration_still_goes_to_videos():
    """If we can't read duration, default to videos (don't false-discard)."""
    entries = [
        _entry("VID_UNKNOWN.MP4", CreateDate="2026:04:14 13:00:00"),
    ]
    result = scan_phone(entries)
    assert len(result.videos) == 1
    assert len(result.motion_clips) == 0


# ─────────────────────────────────────────────────────────────────────────
# Motion-clip dedup against Individual photos by timestamp
# ─────────────────────────────────────────────────────────────────────────


def test_scan_phone_motion_clip_dropped_when_individual_at_same_time():
    """If a HEIC photo exists at almost the same timestamp as a short MP4,
    the MP4 is the orphan motion of a Live Photo whose basename pairing
    was lost in transfer. Drop it — the still already represents the moment."""
    entries = [
        _entry("IMG_001.HEIC",
               Make="Apple", Model="iPhone 11",
               DateTimeOriginal="2026:04:14 13:00:00.500"),
        # MP4 timestamp is 0.4s after the HEIC — well within the 1.5s window
        _entry("VID_X.MP4",
               CreateDate="2026:04:14 13:00:00.900",
               Duration="3 s"),
    ]
    result = scan_phone(entries)
    assert len(result.individuals) == 1
    assert len(result.motion_clips) == 0           # filtered out
    assert result.motion_clips_filtered_as_duplicates == 1


def test_scan_phone_motion_clip_kept_when_no_individual_nearby():
    """A short MP4 with no Individual at a nearby timestamp is a true
    orphan — keep it in motion_clips."""
    entries = [
        _entry("IMG_001.HEIC", DateTimeOriginal="2026:04:14 13:00:00"),
        # MP4 is 30 minutes later — not a Live Photo of the HEIC
        _entry("VID_X.MP4", CreateDate="2026:04:14 13:30:00", Duration="3 s"),
    ]
    result = scan_phone(entries)
    assert len(result.individuals) == 1
    assert len(result.motion_clips) == 1
    assert result.motion_clips_filtered_as_duplicates == 0


def test_scan_phone_motion_clip_without_timestamp_is_not_filtered():
    """If the motion clip has no timestamp we cannot dedup it — keep it
    in motion_clips so the user sees it (don't silently lose data)."""
    entries = [
        _entry("IMG_001.HEIC", DateTimeOriginal="2026:04:14 13:00:00"),
        _entry("VID_X.MP4", Duration="3 s"),  # no timestamp
    ]
    result = scan_phone(entries)
    assert len(result.individuals) == 1
    assert len(result.motion_clips) == 1
    assert result.motion_clips_filtered_as_duplicates == 0


def test_scan_phone_long_video_at_same_time_as_individual_still_goes_to_videos():
    """A real video (>4s) that happens to be at the same time as a HEIC
    is NOT a Live Photo motion — keep it in Videos as normal."""
    entries = [
        _entry("IMG_001.HEIC", DateTimeOriginal="2026:04:14 13:00:00"),
        _entry("VID_LONG.MP4",
               CreateDate="2026:04:14 13:00:01",
               Duration="35 s"),  # > threshold
    ]
    result = scan_phone(entries)
    assert len(result.videos) == 1
    assert len(result.motion_clips) == 0
    assert result.motion_clips_filtered_as_duplicates == 0


def test_scan_phone_motion_clip_just_outside_window_kept():
    """Boundary: a clip 1.6s away from any HEIC is OUTSIDE the 1.5s
    window — must NOT be dedup-filtered."""
    entries = [
        _entry("IMG_001.HEIC", DateTimeOriginal="2026:04:14 13:00:00"),
        _entry("VID_X.MP4",
               CreateDate="2026:04:14 13:00:01.6",
               Duration="3 s"),
    ]
    result = scan_phone(entries)
    assert len(result.motion_clips) == 1
    assert result.motion_clips_filtered_as_duplicates == 0


def test_scan_phone_jpg_can_also_be_live_photo_still():
    """Some iOS exports give JPG instead of HEIC — pairing still works.
    The pair counter increments and the still goes to individuals."""
    entries = [
        _entry("IMG_001.JPG", DateTimeOriginal="2026:04:14 13:00:00"),
        _entry("IMG_001.MOV", CreateDate="2026:04:14 13:00:00",
               Duration="3 s"),
    ]
    result = scan_phone(entries)
    assert result.live_photo_pairs_merged == 1
    assert any(i.path.name == "IMG_001.JPG" for i in result.individuals)
    # Motion partner is silently absorbed — not in videos or motion_clips
    assert all(v.path.name != "IMG_001.MOV" for v in result.videos)
    assert all(c.path.name != "IMG_001.MOV" for c in result.motion_clips)


# ─────────────────────────────────────────────────────────────────────────
# scan_phone — Burst by BurstUUID
# ─────────────────────────────────────────────────────────────────────────


def test_scan_phone_burst_via_uuid():
    entries = [
        _entry("IMG_100.HEIC", BurstUUID="ABC-123",
               DateTimeOriginal="2026:04:14 13:00:00"),
        _entry("IMG_101.HEIC", BurstUUID="ABC-123",
               DateTimeOriginal="2026:04:14 13:00:00.5"),
        _entry("IMG_102.HEIC", BurstUUID="ABC-123",
               DateTimeOriginal="2026:04:14 13:00:01"),
        _entry("IMG_200.HEIC",   # not in the burst
               DateTimeOriginal="2026:04:14 13:30:00"),
    ]
    result = scan_phone(entries)
    assert len(result.bursts) == 1
    burst = result.bursts[0]
    assert burst.burst_id == "ABC-123"
    assert len(burst.photos) == 3
    assert burst.detection_source == "burst_uuid"
    # The non-burst photo is in individuals
    assert len(result.individuals) == 1
    assert result.individuals[0].path.name == "IMG_200.HEIC"


def test_scan_phone_singleton_uuid_is_not_a_burst():
    """A single photo with a BurstUUID is treated as Individual."""
    entries = [
        _entry("IMG_100.HEIC", BurstUUID="ABC-123",
               DateTimeOriginal="2026:04:14 13:00:00"),
    ]
    result = scan_phone(entries)
    assert len(result.bursts) == 0
    assert len(result.individuals) == 1


def test_scan_phone_no_burst_uuid_falls_into_individual_with_clusters():
    """Without BurstUUID, tight same-source photos go to Individual and
    are picked up by the cluster post-pass."""
    entries = [
        _entry(f"IMG_{i:03d}.HEIC",
               Make="Apple", Model="iPhone 11",
               DateTimeOriginal=f"2026:04:14 13:00:0{i}")
        for i in range(5)
    ]
    result = scan_phone(entries)
    assert len(result.bursts) == 0
    assert len(result.individuals) == 5
    # All 5 should be clustered (5 photos within 5 seconds, well inside 5min window)
    assert all(i.cluster_id is not None for i in result.individuals)


# ─────────────────────────────────────────────────────────────────────────
# scan_phone — combined / realistic scenarios
# ─────────────────────────────────────────────────────────────────────────


def test_scan_phone_realistic_mixed_dataset():
    """Mixed scene: 2 individuals + 1 live photo pair + 1 burst (3 frames)
    + 1 standalone video. Verify counts across all buckets."""
    entries = [
        # Individuals (different times, no cluster)
        _entry("IMG_010.HEIC", DateTimeOriginal="2026:04:14 09:00:00"),
        _entry("IMG_011.HEIC", DateTimeOriginal="2026:04:14 18:00:00"),
        # Live Photo pair
        _entry("IMG_020.HEIC", DateTimeOriginal="2026:04:14 12:00:00"),
        _entry("IMG_020.MOV", CreateDate="2026:04:14 12:00:00",
               Duration="3 s"),
        # Burst (3 frames same UUID)
        _entry("IMG_030.HEIC", BurstUUID="X",
               DateTimeOriginal="2026:04:14 14:00:00"),
        _entry("IMG_031.HEIC", BurstUUID="X",
               DateTimeOriginal="2026:04:14 14:00:01"),
        _entry("IMG_032.HEIC", BurstUUID="X",
               DateTimeOriginal="2026:04:14 14:00:02"),
        # Standalone video
        _entry("VID_040.MP4", CreateDate="2026:04:14 16:00:00",
               Duration="60 s"),
    ]
    result = scan_phone(entries)
    # MVP: live_photos list is empty post-merge (just a counter remains).
    # Stills are in individuals; motion partners are absorbed silently.
    assert len(result.live_photos) == 0
    assert result.live_photo_pairs_merged == 1
    assert len(result.bursts) == 1
    # 2 plain individuals + 1 LP still merged in = 3
    assert len(result.individuals) == 3
    assert len(result.videos) == 1
    # Total accounting (each surfaced unique file once):
    # 3 individuals + 3 burst + 1 video = 7. The Live Photo motion was
    # absorbed by pair detection and is not counted in any user bucket.
    assert result.total_items == 7


def test_scan_phone_orphan_jpg_no_makemodel_still_routes_to_individual():
    """Files lacking Make/Model still go to Individual when no other bucket fits."""
    entries = [
        _entry("UUID-ABC.jpg"),  # no EXIF at all
    ]
    result = scan_phone(entries)
    assert len(result.individuals) == 1
    assert result.individuals[0].timestamp is None
    assert result.individuals[0].cluster_id is None


# ─────────────────────────────────────────────────────────────────────────
# Cluster integration in scan_phone
# ─────────────────────────────────────────────────────────────────────────


def test_scan_phone_cluster_with_two_iphones_grouped_together():
    """Multi-source cluster — the volcano-at-sunset scenario."""
    base = datetime(2026, 4, 22, 17, 35, 0)
    entries = []
    for i in range(3):
        entries.append(_entry(
            f"IMG_30{i}.HEIC", Make="Apple", Model="iPhone 11",
            DateTimeOriginal=base.strftime("%Y:%m:%d %H:%M:") + f"{(i*10):02d}",
        ))
    for i in range(3):
        entries.append(_entry(
            f"IMG_60{i}.HEIC", Make="Apple", Model="iPhone 12",
            DateTimeOriginal=(base + timedelta(seconds=5 + i*10)).strftime(
                "%Y:%m:%d %H:%M:%S"),
        ))
    result = scan_phone(entries)
    assert len(result.individuals) == 6
    assert all(p.cluster_id is not None for p in result.individuals)
    # Should be a single cluster
    assert len({p.cluster_id for p in result.individuals}) == 1
    # cluster_sources contains both models
    sources = result.individuals[0].cluster_sources
    assert ("Apple", "iPhone 11") in sources
    assert ("Apple", "iPhone 12") in sources


# ─────────────────────────────────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────────────────────────────────


def test_scan_dispatcher_phone():
    entries = [_entry("a.HEIC", DateTimeOriginal="2026:04:14 13:00:00")]
    result = scan(entries, SourceKind.PHONE)
    assert result.source_kind == SourceKind.PHONE


def test_scan_dispatcher_camera():
    entries = [
        _entry("P0001.RW2", Make="Panasonic", Model="DC-G9",
               DateTimeOriginal="2026:04:14 13:00:00"),
    ]
    result = scan(entries, SourceKind.CAMERA)
    assert result.source_kind == SourceKind.CAMERA
    # No brackets/bursts on a single photo
    assert len(result.individuals) == 1


# ─────────────────────────────────────────────────────────────────────────
# scan_camera — basic behavior (full bracket logic is exercised by
# test_bracket_detector; here we just verify the wrapping)
# ─────────────────────────────────────────────────────────────────────────


def test_scan_camera_video_passthrough():
    entries = [
        _entry("P0001.MP4", CreateDate="2026:04:14 13:00:00",
               Duration="30 s"),
    ]
    result = scan_camera(entries)
    assert len(result.videos) == 1
    assert result.videos[0].duration_s == 30.0


def test_scan_camera_individual_no_brackets():
    entries = [
        _entry("P0001.RW2", Make="Panasonic", Model="DC-G9II",
               LensModel="LUMIX 12-60mm",
               DateTimeOriginal="2026:04:14 13:00:00",
               FocalLength="35", FNumber="5.6", ExposureTime="1/250",
               ISO="400", Orientation=1),
    ]
    result = scan_camera(entries)
    assert len(result.focus_brackets) == 0
    assert len(result.exposure_brackets) == 0
    assert len(result.individuals) == 1


def test_scan_camera_detects_aeb_via_burst_mode():
    """Regression for the 2026-04-29 Costa Rica field test where the
    Panasonic G9 II AEB sequence was missed because the candidate
    builder hard-coded the EXIF tag name `Bracketing` (which the G9
    II doesn't write) instead of consulting the brand profile.

    The G9 II writes the AEB signal to MakerNotes `BurstMode` with
    value `Auto Exposure Bracketing (AEB)`. The brand-profile-driven
    builder must turn this into ``exposure_bracket_tag_active=True``
    so the bracket detector groups the frames into one sequence."""
    common = dict(
        Make="Panasonic", Model="DC-G9M2",
        LensModel="LUMIX G VARIO 12-35/F2.8II",
        FocalLength="24", FNumber="8.0", ExposureTime="1/80",
        ISO="100", Orientation=1,
        BurstMode="Auto Exposure Bracketing (AEB)",
        BracketSettings="7 Images, Sequence 0/-/+",
    )
    # Seven AEB frames, ~1 s apart — enough to satisfy the detector's
    # temporal cohesion requirement.
    entries = [
        _entry(f"P{i:04d}.RW2",
               DateTimeOriginal=f"2026:04:16 10:30:0{i}",
               ExposureCompensation=str((i - 3)),
               **common)
        for i in range(7)
    ]
    result = scan_camera(entries)
    assert len(result.exposure_brackets) == 1
    assert len(result.exposure_brackets[0].photos) == 7
    # All seven frames should be in the bracket, none as orphans
    assert len(result.individuals) == 0


# ─────────────────────────────────────────────────────────────────────────
# scan_camera — Burst detection (SequenceNumber + time-gap fallback)
# ─────────────────────────────────────────────────────────────────────────


def _burst_frame(
    name: str,
    seq: int,
    ts_seconds_offset: float,
    *,
    make: str = "Panasonic",
    model: str = "DC-G9M2",
    drive_mode_tag: str = "BurstMode",
    drive_mode_value: str = "Super High",
    sequence_number_tag: str = "SequenceNumber",
    base_ts: str = "2026:04:14 13:00:00",
) -> RawExifEntry:
    """Synthesize one camera burst frame.

    ``ts_seconds_offset`` shifts the timestamp from ``base_ts`` by a
    fractional second amount, letting tests express 0.1s-spaced bursts
    or 1s-spaced non-bursts compactly.
    """
    # Build "13:00:0X.YYY" — only seconds resolution for the .ss part and
    # subsec for the .YYY part. Tests use offsets in [0, 9.999].
    whole = int(ts_seconds_offset)
    frac = int(round((ts_seconds_offset - whole) * 1000))
    ts = f"{base_ts[:-2]}{whole:02d}.{frac:03d}"
    exif = {
        "Make": make, "Model": model,
        "LensModel": "LUMIX G VARIO 100-400",
        "DateTimeOriginal": ts,
        "FocalLength": "400", "FNumber": "6.3",
        "ExposureTime": "1/2000", "ISO": "800", "Orientation": 1,
        drive_mode_tag: drive_mode_value,
    }
    if seq is not None:
        exif[sequence_number_tag] = str(seq)
    return _entry(name, **exif)


def test_scan_camera_burst_via_sequence_number():
    """12 Panasonic frames in continuous mode with SequenceNumber 1-12
    spaced 0.1s apart → one BurstSequence detected via sequence_number."""
    entries = [
        _burst_frame(f"P{i:04d}.RW2", seq=i + 1, ts_seconds_offset=i * 0.1)
        for i in range(12)
    ]
    result = scan_camera(entries)
    assert len(result.bursts) == 1
    burst = result.bursts[0]
    assert burst.detection_source == "sequence_number"
    assert burst.photo_count == 12
    assert len(result.individuals) == 0


def test_scan_camera_two_back_to_back_bursts_via_sequence_reset():
    """Two 10-frame bursts back-to-back: seq 1..10 then 1..10 again.
    A SequenceNumber reset closes the first burst and starts the second.
    """
    entries = []
    # First burst: 10 frames at t=0.0..0.9
    for i in range(10):
        entries.append(_burst_frame(
            f"A{i:04d}.RW2", seq=i + 1, ts_seconds_offset=i * 0.1,
        ))
    # Second burst: 10 frames at t=1.0..1.9, sequence resets to 1
    for i in range(10):
        entries.append(_burst_frame(
            f"B{i:04d}.RW2", seq=i + 1, ts_seconds_offset=1.0 + i * 0.1,
        ))
    # Need a gap >0.5s between the two bursts for them to split — push
    # the second by 1s instead of 0.1s.
    entries[10] = _burst_frame(
        "B0000.RW2", seq=1, ts_seconds_offset=2.0,
    )
    for i in range(1, 10):
        entries[10 + i] = _burst_frame(
            f"B{i:04d}.RW2", seq=i + 1, ts_seconds_offset=2.0 + i * 0.1,
        )
    result = scan_camera(entries)
    assert len(result.bursts) == 2
    assert all(b.detection_source == "sequence_number" for b in result.bursts)
    assert sorted(b.photo_count for b in result.bursts) == [10, 10]
    assert len(result.individuals) == 0


def test_scan_camera_burst_below_threshold_stays_individual():
    """A 2-frame continuous blip is below the min-3 threshold (an
    accidental shutter double, not a burst) — it falls through to
    Individuals. >=3 IS a burst (see the regression test below)."""
    entries = [
        _burst_frame(f"P{i:04d}.RW2", seq=i + 1, ts_seconds_offset=i * 0.1)
        for i in range(2)
    ]
    result = scan_camera(entries)
    assert len(result.bursts) == 0
    assert len(result.individuals) == 2


def test_scan_camera_three_frame_continuous_run_is_a_burst():
    """Regression — real G9 Dia 9 (Nelson eyeball 2026-05-17): genuine
    3-9 frame continuous runs were being dropped to Individuals by the
    arbitrary min-10. >=3 continuous frames is a (short) burst."""
    entries = [
        _burst_frame(f"P{i:04d}.RW2", seq=i + 1, ts_seconds_offset=i * 0.1)
        for i in range(3)
    ]
    result = scan_camera(entries)
    assert len(result.bursts) == 1
    assert result.bursts[0].photo_count == 3
    assert len(result.individuals) == 0


def test_scan_camera_seq_number_burst_survives_whole_second_gaps():
    """Regression — real G9 Dia 9 (Nelson eyeball 2026-05-17). The
    G9's RW2 DateTimeOriginal is WHOLE-SECOND, so a 7-9 fps burst has
    consecutive EXIF timestamps 0 s / 1 s apart — every intra-burst
    step exceeds camera_burst_max_gap_seconds (0.5). The wall-clock
    gap is only the FALLBACK heuristic: when a clean monotonic
    SequenceNumber is present it is the DETERMINISTIC signal and the
    gap must NOT chop the run. 12 continuous frames, seq 1..12, each
    a whole second apart → exactly ONE burst (was 0 before the fix).
    """
    entries = [
        _burst_frame(f"P{i:04d}.RW2", seq=i + 1, ts_seconds_offset=float(i))
        for i in range(12)
    ]
    result = scan_camera(entries)
    assert len(result.bursts) == 1
    assert result.bursts[0].detection_source == "sequence_number"
    assert result.bursts[0].photo_count == 12
    assert len(result.individuals) == 0


def test_scan_camera_burst_time_gap_closes_run_no_sequence():
    """Time-gap FALLBACK still governs frames with no SequenceNumber:
    2 close + >0.5s gap + 2 close — the gap splits the run, and each
    side (2 frames) is below the min-3 threshold → no bursts, all
    individuals. (No seq tag, so the gap is the only boundary signal
    — its splitting job is preserved.)"""
    entries = []
    for i in range(2):
        entries.append(_burst_frame(
            f"A{i:04d}.RW2", seq=None, ts_seconds_offset=i * 0.1,
            make="Acme", model="ZX1", drive_mode_tag="DriveMode",
            drive_mode_value="Continuous burst",
        ))
    for i in range(2):
        entries.append(_burst_frame(
            f"B{i:04d}.RW2", seq=None, ts_seconds_offset=2.5 + i * 0.1,
            make="Acme", model="ZX1", drive_mode_tag="DriveMode",
            drive_mode_value="Continuous burst",
        ))
    result = scan_camera(entries)
    assert len(result.bursts) == 0
    assert len(result.individuals) == 4


def test_scan_camera_burst_no_continuous_mode_no_burst():
    """12 frames with SequenceNumber but DriveMode=Off (single-shot
    mode). Not a burst — the camera wasn't in continuous mode."""
    entries = [
        _burst_frame(
            f"P{i:04d}.RW2", seq=i + 1, ts_seconds_offset=i * 0.1,
            drive_mode_value="Off",
        )
        for i in range(12)
    ]
    result = scan_camera(entries)
    assert len(result.bursts) == 0
    assert len(result.individuals) == 12


def test_scan_camera_burst_time_gap_fallback_no_sequence_tag():
    """Unprofiled brand (no Panasonic profile match) with continuous
    mode and tight spacing → time-gap fallback kicks in, detection_source
    is "time_gap". We simulate an unprofiled brand by setting Make to
    a string that no brand profile matches, but keeping a
    brand-agnostic continuous-mode tag (DriveMode containing "burst")
    so the substring fallback flags it as continuous."""
    entries = [
        _burst_frame(
            f"X{i:04d}.RAW", seq=None, ts_seconds_offset=i * 0.1,
            make="UnknownBrand", model="UnknownBody",
            drive_mode_tag="DriveMode", drive_mode_value="Continuous-H",
        )
        for i in range(12)
    ]
    result = scan_camera(entries)
    assert len(result.bursts) == 1
    burst = result.bursts[0]
    assert burst.detection_source == "time_gap"
    assert burst.photo_count == 12


def test_scan_camera_burst_different_body_closes_run():
    """Mid-sequence body change splits the run: two cameras in the
    same dump shooting continuous mode each get their OWN burst (the
    body change closes the first run and opens the second)."""
    entries = []
    for i in range(6):
        entries.append(_burst_frame(
            f"P{i:04d}.RW2", seq=i + 1, ts_seconds_offset=i * 0.1,
            model="DC-G9M2",
        ))
    for i in range(6):
        entries.append(_burst_frame(
            f"S{i:04d}.RW2", seq=i + 1, ts_seconds_offset=0.6 + i * 0.1,
            make="Sony", model="ILCE-6700",
            drive_mode_tag="DriveMode", drive_mode_value="Continuous Hi",
        ))
    result = scan_camera(entries)
    # Body change splits into two separate 6-frame bursts (>= min-3).
    assert len(result.bursts) == 2
    assert sorted(b.photo_count for b in result.bursts) == [6, 6]
    assert len(result.individuals) == 0


def test_scan_camera_burst_detect_source_drives_destination_routing():
    """Sanity check that the BurstSequence carries the representative
    timestamp from the first frame — the bucket scanner relies on this
    field to route the entire burst to the right trip-day folder at
    save time (see BurstSequence docstring)."""
    entries = [
        _burst_frame(f"P{i:04d}.RW2", seq=i + 1, ts_seconds_offset=i * 0.1)
        for i in range(10)
    ]
    result = scan_camera(entries)
    assert len(result.bursts) == 1
    assert result.bursts[0].representative_timestamp is not None
    assert result.bursts[0].representative_timestamp.year == 2026


def test_scan_camera_burst_mixed_with_bracket_sequence():
    """A bracket sequence and a burst in the same scan — bracket wins
    its frames, burst gets its own (no double-counting). The bracket
    detector consumes its frames first; remaining orphans go through
    burst detection."""
    bracket_common = dict(
        Make="Panasonic", Model="DC-G9M2",
        LensModel="LUMIX G VARIO 12-35/F2.8II",
        FocalLength="24", FNumber="8.0", ExposureTime="1/80",
        ISO="100", Orientation=1,
        BurstMode="Auto Exposure Bracketing (AEB)",
        BracketSettings="7 Images, Sequence 0/-/+",
    )
    bracket_entries = [
        _entry(f"AEB{i:04d}.RW2",
               DateTimeOriginal=f"2026:04:16 10:30:0{i}",
               ExposureCompensation=str(i - 3),
               **bracket_common)
        for i in range(7)
    ]
    # Burst frames much later same day, on the wildlife lens.
    burst_entries = [
        _burst_frame(
            f"BURST{i:04d}.RW2", seq=i + 1, ts_seconds_offset=i * 0.1,
            base_ts="2026:04:16 14:00:00",
        )
        for i in range(11)
    ]
    result = scan_camera(bracket_entries + burst_entries)
    assert len(result.exposure_brackets) == 1
    assert result.exposure_brackets[0].photo_count == 7
    assert len(result.bursts) == 1
    assert result.bursts[0].photo_count == 11
    assert result.bursts[0].detection_source == "sequence_number"
    assert len(result.individuals) == 0


# ─────────────────────────────────────────────────────────────────────────
# brand_profile.detect_burst — unit tests for the new hook
# ─────────────────────────────────────────────────────────────────────────


def test_brand_profile_detect_burst_panasonic_reads_sequence_number():
    """Panasonic profile declares SequenceNumber as its burst tag.
    Non-zero integer values come back as positive ints; zero / missing
    / non-numeric come back as None."""
    from core.brand_profile import load_brand_profile
    p = load_brand_profile("panasonic")
    assert p.detect_burst({"SequenceNumber": "5"}) == 5
    assert p.detect_burst({"SequenceNumber": 7}) == 7
    assert p.detect_burst({"SequenceNumber": "0"}) is None  # not in burst
    assert p.detect_burst({"SequenceNumber": ""}) is None
    assert p.detect_burst({}) is None
    assert p.detect_burst({"SequenceNumber": "abc"}) is None


def test_brand_profile_detect_burst_none_for_brands_without_tag():
    """Brands that don't declare burst_detection.sequence_tag return
    None regardless of EXIF contents — scanner falls back to time-gap
    clustering for them."""
    from core.brand_profile import load_brand_profile
    for brand_id in ("sony", "apple", "gopro"):
        p = load_brand_profile(brand_id)
        assert p.detect_burst({"SequenceNumber": "5"}) is None, (
            f"{brand_id}: expected None (no burst_detection block declared)"
        )


def test_brand_profile_is_continuous_shooting_panasonic():
    """Panasonic BurstMode='Super High' resolves to BURST_HIGH ->
    is_continuous_shooting True. 'Off' -> False (single shot)."""
    from core.brand_profile import load_brand_profile
    p = load_brand_profile("panasonic")
    assert p.is_continuous_shooting({"BurstMode": "Super High"}) is True
    assert p.is_continuous_shooting({"BurstMode": "Low"}) is True
    assert p.is_continuous_shooting({"BurstMode": "Off"}) is False
    assert p.is_continuous_shooting({}) is False
