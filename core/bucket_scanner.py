"""Bucket scanner — categorizes photos and videos into buckets.

Given a list of RawExifEntry (path + EXIF dict) and a SourceKind (camera or
phone), the scanner classifies every item into exactly one bucket. The
bucket types differ per source:

    Camera (5 types): Focus Bracket, Exposure Bracket, Burst, Individual, Video.
    Phone  (4 types): Burst, Individual, Live Photo, Video.

Bucket membership is mutually exclusive — an item appears in exactly one
bucket. Within the Individual bucket, photos may carry "moment cluster"
metadata when ≥3 photos fall within a sliding time window (default 5 min),
regardless of source. Cluster metadata is a navigation aid; it does not
change the interaction model.

See v2_design.md § 24.2 (buckets, source-aware) and § 24.9 (architecture)
for the canonical design. This module is the first responsibility of the
v2.0 culler: scan -> bucket overview UI -> per-bucket interaction.

Detection responsibilities by source:

    - Focus / Exposure brackets (camera only): reuses core.bracket_detector
      (the existing two-pass detector documented in v2_design.md § 12).
    - Burst (camera + phone, different signals):
        camera = future TODO (DriveMode=continuous + tight time + constant
                 params); for now uses the bracket detector's orphan list.
        phone  = iPhone BurstUUID grouping. Without BurstUUID, photos go
                 to Individual (cluster detection handles tight same-source
                 sequences naturally).
    - Live Photo (phone only): HEIC + MOV pair with same basename, MOV
      duration <= 4s, timestamps within ~1.5s.
    - Individual (both): everything not in a sequence/pair above. Cluster
      post-pass annotates ≥3-photo clusters within a 5-minute window.
    - Video (both): standalone movie files (excludes Live Photo MOVs
      already paired).

The scanner is pure — no filesystem I/O beyond reading the .path/.exif of
inputs the caller already prepared. The caller (UI or CLI) reads EXIF via
core.folder_scanner.scan_folder and passes the result here.
"""

from __future__ import annotations

import bisect
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.classifier_v2 import ClassificationResult

from core.bracket_detector import (
    BracketCandidate,
    BracketSequence,
    DetectorConfig,
    detect_brackets,
    load_detector_config,
)
from core.import_pipeline import RawExifEntry
from core.logging_setup import log_activity
from core.settings import user_data_dir

log = logging.getLogger(__name__)


# ── Defaults (overridable via assets/bucket_scanner.json or user override) ──

DEFAULT_LIVE_PHOTO_MAX_VIDEO_DURATION = 4.0      # seconds
DEFAULT_LIVE_PHOTO_TIMESTAMP_PROXIMITY = 1.5     # seconds
DEFAULT_CLUSTER_WINDOW_SECONDS = 300.0           # 5 minutes
DEFAULT_CLUSTER_MIN_SIZE = 3
# Camera-burst grouping: max wall-clock gap between consecutive frames
# of one burst, and the minimum number of frames a run must have before
# we promote it from "individuals" to a real Burst. Nelson's call:
# 2.0 s: the G9's DateTimeOriginal is WHOLE-SECOND, so genuine
# intra-burst steps read 0-1 s while a distinct shooting event is a
# multi-second pause (real Dia 9: a merged "burst" spanned 335 s —
# two different birds). 0.5 s chopped every whole-second burst; the
# gap split must apply even in sequence mode because the G9's
# SequenceNumber doesn't always reset between separate bursts. Min
# length 3: >=3 continuous frames is a real (short) burst —
# consistent with the bracket detector's min sequence size and
# `cluster_min_size`, both 3. The old 10 dropped genuine 3-9 frame
# bursts into Individuals (Nelson eyeball 2026-05-17). 1-2 frame
# continuous blips (accidental shutter doubles) stay out.
DEFAULT_CAMERA_BURST_MAX_GAP_SECONDS = 2.0
DEFAULT_CAMERA_BURST_MIN_SEQUENCE_LENGTH = 3
DEFAULT_VIDEO_EXTENSIONS = frozenset({".mov", ".mp4", ".m4v"})

# Tag candidates we read from EXIF dicts. The caller may not populate all
# of them; the scanner picks the first non-empty one.
TIMESTAMP_TAGS = (
    "SubSecDateTimeOriginal",
    "DateTimeOriginal",
    # iOS MP4 stores `CreationDate` as wall-clock with TZ offset (matches the
    # HEIC's DateTimeOriginal in the local trip timezone). Stock MP4
    # `CreateDate` stores UTC instead, which causes a 6-hour mismatch when
    # we treat the HEIC's bare DateTimeOriginal as wall-clock local.
    # Prefer `CreationDate` so motion-clip dedup against Individuals works.
    "CreationDate",
    "CreateDate",
)
VIDEO_DURATION_TAGS = ("Duration", "MediaDuration", "TrackDuration")
BURST_UUID_TAGS = ("BurstUUID", "MakerNotes:BurstUUID")


# ── Enums and data classes ─────────────────────────────────────────────────


class SourceKind(str, Enum):
    """Where the photos came from. Determines which buckets are emitted."""
    CAMERA = "camera"
    PHONE = "phone"


@dataclass
class BucketScannerConfig:
    live_photo_max_video_duration: float = DEFAULT_LIVE_PHOTO_MAX_VIDEO_DURATION
    live_photo_timestamp_proximity: float = DEFAULT_LIVE_PHOTO_TIMESTAMP_PROXIMITY
    cluster_window_seconds: float = DEFAULT_CLUSTER_WINDOW_SECONDS
    cluster_min_size: int = DEFAULT_CLUSTER_MIN_SIZE
    camera_burst_max_gap_seconds: float = DEFAULT_CAMERA_BURST_MAX_GAP_SECONDS
    camera_burst_min_sequence_length: int = DEFAULT_CAMERA_BURST_MIN_SEQUENCE_LENGTH
    video_extensions: frozenset[str] = field(
        default_factory=lambda: DEFAULT_VIDEO_EXTENSIONS
    )


@dataclass
class BurstSequence:
    """A burst of N photos taken in rapid succession from the same hardware.

    For phone source, detected via BurstUUID. For camera source, future TODO
    (DriveMode signal). Bursts are temporally cohesive and share a single
    destination scenario at save time.

    ``suggested_scenario`` is populated by the post-scan classifier
    integration; bursts share one suggestion for the whole sequence
    since burst frames are nearly identical.
    """
    burst_id: str
    photos: list[Path]                  # ordered chronologically
    detection_source: str               # "burst_uuid" / "drive_mode" / etc.
    suggested_scenario: Optional["ClassificationResult"] = None
    # Representative timestamp for the whole burst — first frame's
    # DateTimeOriginal. Used by CullerSession.save() to route every
    # frame of the burst to the matching trip-day folder. See the
    # mirror field on BracketSequence for the rationale.
    representative_timestamp: Optional[datetime] = None

    @property
    def photo_count(self) -> int:
        return len(self.photos)


@dataclass
class LivePhotoPair:
    """A HEIC still paired with its MOV motion component (iPhone Live Photo).

    The pair is identified by matching basename + short MOV duration +
    timestamp proximity. Phone source only.
    """
    pair_id: str
    still: Path                         # HEIC (or JPG)
    motion: Path                        # MOV


@dataclass
class IndividualPhoto:
    """A single photo not in any sequence or pair.

    Carries optional cluster metadata when the photo is part of a moment
    cluster (≥3 photos within cluster_window_seconds). Cluster metadata is a
    navigation aid; the photo is still independent for selection purposes.

    ``suggested_scenario`` is populated post-scan by the classifier
    integration step in ``classify_bucket_scan_result`` and surfaces to
    the v2.0 culler's info bar + Keep menu defaults. ``None`` means the
    classifier wasn't run yet (fresh scan from a unit test, or future
    code path that skips classification).
    """
    path: Path
    timestamp: Optional[datetime]
    make: str = ""
    model: str = ""
    cluster_id: Optional[str] = None
    cluster_size: int = 0
    cluster_position: int = 0           # 1-based
    cluster_sources: list[tuple[str, str]] = field(default_factory=list)
    suggested_scenario: Optional["ClassificationResult"] = None


@dataclass
class VideoFile:
    """A standalone video file (not part of a Live Photo pair)."""
    path: Path
    timestamp: Optional[datetime]
    duration_s: Optional[float]


@dataclass
class BucketScanResult:
    """All buckets for a single scan. Bucket lists may be empty per source."""
    source_kind: SourceKind
    focus_brackets: list[BracketSequence] = field(default_factory=list)
    exposure_brackets: list[BracketSequence] = field(default_factory=list)
    bursts: list[BurstSequence] = field(default_factory=list)
    live_photos: list[LivePhotoPair] = field(default_factory=list)
    individuals: list[IndividualPhoto] = field(default_factory=list)
    motion_clips: list[VideoFile] = field(default_factory=list)
    videos: list[VideoFile] = field(default_factory=list)

    # Number of phone-source short videos that were dropped because an
    # Individual photo existed at nearly the same timestamp. These are
    # almost certainly Live Photo motion fragments whose still partner
    # is also present (the basename pairing was lost in transfer).
    # Reported in the CLI output but not counted in `total_items`.
    motion_clips_filtered_as_duplicates: int = 0

    # Number of Live Photo pairs detected by basename — their stills were
    # merged into `individuals` and their motion partners discarded from
    # the user-facing buckets. The list `live_photos` is reserved for a
    # future iteration that re-introduces a separate Live Photo bucket
    # with a Keep ▶ kind submenu (§ 24.4); for now it stays empty so
    # nothing is double-counted in `total_items`.
    live_photo_pairs_merged: int = 0

    @property
    def total_items(self) -> int:
        """Count of all photos/videos across all buckets (each item once)."""
        return (
            sum(s.photo_count for s in self.focus_brackets)
            + sum(s.photo_count for s in self.exposure_brackets)
            + sum(s.photo_count for s in self.bursts)
            + 2 * len(self.live_photos)        # still + motion
            + len(self.individuals)
            + len(self.motion_clips)
            + len(self.videos)
        )


# ── Config loading ──────────────────────────────────────────────────────────


def load_bucket_scanner_config() -> BucketScannerConfig:
    """Load thresholds from JSON, falling back to defaults if missing.

    Looks at the user override first (user_data_dir() / 'bucket_scanner.json'),
    then the built-in (assets/bucket_scanner.json). If neither exists or
    neither is readable, returns defaults.
    """
    user_path = user_data_dir() / "bucket_scanner.json"
    builtin_path = (
        Path(__file__).parent.parent / "assets" / "bucket_scanner.json"
    )
    for path in (user_path, builtin_path):
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("bucket_scanner.json at %s unreadable: %s", path, exc)
            continue
        return BucketScannerConfig(
            live_photo_max_video_duration=float(data.get(
                "live_photo_max_video_duration",
                DEFAULT_LIVE_PHOTO_MAX_VIDEO_DURATION,
            )),
            live_photo_timestamp_proximity=float(data.get(
                "live_photo_timestamp_proximity",
                DEFAULT_LIVE_PHOTO_TIMESTAMP_PROXIMITY,
            )),
            cluster_window_seconds=float(data.get(
                "cluster_window_seconds",
                DEFAULT_CLUSTER_WINDOW_SECONDS,
            )),
            cluster_min_size=int(data.get(
                "cluster_min_size",
                DEFAULT_CLUSTER_MIN_SIZE,
            )),
            camera_burst_max_gap_seconds=float(data.get(
                "camera_burst_max_gap_seconds",
                DEFAULT_CAMERA_BURST_MAX_GAP_SECONDS,
            )),
            camera_burst_min_sequence_length=int(data.get(
                "camera_burst_min_sequence_length",
                DEFAULT_CAMERA_BURST_MIN_SEQUENCE_LENGTH,
            )),
            video_extensions=frozenset(
                ext.lower()
                for ext in data.get(
                    "video_extensions",
                    list(DEFAULT_VIDEO_EXTENSIONS),
                )
            ),
        )
    return BucketScannerConfig()


# ── Helpers — parsing EXIF strings ──────────────────────────────────────────


def _parse_timestamp(value) -> Optional[datetime]:
    """Parse an EXIF timestamp string into a naive datetime.

    Handles offsets like '-06:00' that Python's strptime requires without
    the colon. Always returns a naive datetime (drops tzinfo) — EXIF
    timestamps within a single trip are consistently in the local
    timezone of capture, and treating them as wall-clock prevents
    offset-naive vs offset-aware comparison errors when sorting mixed
    sources (e.g. HEIC photos from one tag and MP4 videos from another).
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # Normalize TZ offset: Python wants '-0600' not '-06:00'
    s_norm = s
    if len(s) >= 6:
        last6 = s[-6:]
        if (last6[0] in "+-") and last6[3] == ":":
            s_norm = s[:-3] + s[-2:]
    formats = (
        "%Y:%m:%d %H:%M:%S.%f%z",
        "%Y:%m:%d %H:%M:%S%z",
        "%Y:%m:%d %H:%M:%S.%f",
        "%Y:%m:%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
    )
    for fmt in formats:
        for candidate in (s_norm, s):
            try:
                dt = datetime.strptime(candidate, fmt)
                # Always naive — drop offset
                return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt
            except ValueError:
                pass
    return None


def parse_duration_seconds(value) -> Optional[float]:
    """Parse exiftool duration outputs.

    Common shapes: '13.93 s', '0:00:13', '0:00:13.93', or a bare number.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    if s.endswith(" s"):
        try:
            return float(s[:-2])
        except ValueError:
            return None
    if ":" in s:
        parts = s.split(":")
        try:
            parts_f = [float(p) for p in parts]
        except ValueError:
            return None
        if len(parts_f) == 3:
            return parts_f[0] * 3600 + parts_f[1] * 60 + parts_f[2]
        if len(parts_f) == 2:
            return parts_f[0] * 60 + parts_f[1]
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _first_nonempty(exif: dict, keys: tuple[str, ...]):
    """Return the first non-empty value for any of the given keys."""
    for k in keys:
        v = exif.get(k)
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return v
    return None


def _read_timestamp(exif: dict) -> Optional[datetime]:
    return _parse_timestamp(_first_nonempty(exif, TIMESTAMP_TAGS))


def _read_duration(exif: dict) -> Optional[float]:
    return parse_duration_seconds(_first_nonempty(exif, VIDEO_DURATION_TAGS))


def _read_burst_uuid(exif: dict) -> Optional[str]:
    val = _first_nonempty(exif, BURST_UUID_TAGS)
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _is_video(path: Path, video_extensions: frozenset[str]) -> bool:
    return path.suffix.lower() in video_extensions


# ── Cluster detection ──────────────────────────────────────────────────────


def annotate_clusters(
    individuals: list[IndividualPhoto],
    config: BucketScannerConfig,
) -> list[IndividualPhoto]:
    """Annotate individuals with moment-cluster metadata where applicable.

    A cluster is a contiguous-by-timestamp sequence of photos whose total
    span (last - first) is ≤ cluster_window_seconds, with at least
    cluster_min_size members. Solo photos and short runs receive no
    annotation. Photos without timestamps are passed through untouched at
    the end of the returned list.

    Greedy span-based grouping: walk sorted-by-timestamp; while adding the
    next photo keeps span <= window, accept it; otherwise close the run
    and start a new one. Deterministic, predictable, simple. Two-source
    composition (Make/Model variety) is captured in cluster_sources.
    """
    with_ts = [p for p in individuals if p.timestamp is not None]
    without_ts = [p for p in individuals if p.timestamp is None]

    with_ts.sort(key=lambda p: p.timestamp)

    clusters: list[list[IndividualPhoto]] = []
    current: list[IndividualPhoto] = []

    def _close():
        if len(current) >= config.cluster_min_size:
            clusters.append(list(current))

    for photo in with_ts:
        if not current:
            current = [photo]
            continue
        span = (photo.timestamp - current[0].timestamp).total_seconds()
        if span <= config.cluster_window_seconds:
            current.append(photo)
        else:
            _close()
            current = [photo]
    _close()

    for cluster in clusters:
        cid = str(uuid.uuid4())
        # Distinct (Make, Model) preserving order of first appearance
        sources: list[tuple[str, str]] = []
        for p in cluster:
            key = (p.make, p.model)
            if key not in sources:
                sources.append(key)
        for idx, p in enumerate(cluster, start=1):
            p.cluster_id = cid
            p.cluster_size = len(cluster)
            p.cluster_position = idx
            p.cluster_sources = list(sources)

    return with_ts + without_ts


# ── Phone scan ─────────────────────────────────────────────────────────────


def scan_phone(
    entries: list[RawExifEntry],
    config: Optional[BucketScannerConfig] = None,
) -> BucketScanResult:
    """Scan phone photos and videos into the 4 phone buckets.

    Buckets emitted: Burst, Live Photo, Individual, Video. No Focus/Exposure
    brackets (phones don't bracket).
    """
    cfg = config or load_bucket_scanner_config()
    result = BucketScanResult(source_kind=SourceKind.PHONE)

    # Split videos vs images
    image_entries: list[RawExifEntry] = []
    video_entries: list[RawExifEntry] = []
    for e in entries:
        if _is_video(e.path, cfg.video_extensions):
            video_entries.append(e)
        else:
            image_entries.append(e)

    # ── 1. Live Photo pairing ──────────────────────────────────────────
    paired_videos: set[Path] = set()
    paired_stills: set[Path] = set()

    # Index videos by stem for O(1) lookup
    videos_by_stem: dict[str, list[RawExifEntry]] = {}
    for v in video_entries:
        videos_by_stem.setdefault(v.path.stem, []).append(v)

    for img in image_entries:
        if img.path.suffix.lower() not in (".heic", ".heif", ".jpg", ".jpeg"):
            continue
        candidates = videos_by_stem.get(img.path.stem, [])
        for vid in candidates:
            if vid.path in paired_videos:
                continue
            duration = _read_duration(vid.exif)
            if duration is None or duration > cfg.live_photo_max_video_duration:
                continue
            img_ts = _read_timestamp(img.exif)
            vid_ts = _read_timestamp(vid.exif)
            if img_ts and vid_ts:
                # Compare aware-vs-naive safely: drop tzinfo for the diff
                a = img_ts.replace(tzinfo=None)
                b = vid_ts.replace(tzinfo=None)
                if abs((a - b).total_seconds()) > cfg.live_photo_timestamp_proximity:
                    continue
            result.live_photos.append(LivePhotoPair(
                pair_id=str(uuid.uuid4()),
                still=img.path,
                motion=vid.path,
            ))
            paired_videos.add(vid.path)
            paired_stills.add(img.path)
            break

    # ── 2. Burst detection via BurstUUID (iPhone) ─────────────────────
    bursts_by_uuid: dict[str, list[RawExifEntry]] = {}
    for img in image_entries:
        if img.path in paired_stills:
            continue
        buuid = _read_burst_uuid(img.exif)
        if not buuid:
            continue
        bursts_by_uuid.setdefault(buuid, []).append(img)

    burst_paths: set[Path] = set()
    for buuid, members in bursts_by_uuid.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda e: _read_timestamp(e.exif) or datetime.min)
        first_ts = _read_timestamp(members[0].exif) if members else None
        result.bursts.append(BurstSequence(
            burst_id=buuid,
            photos=[m.path for m in members],
            detection_source="burst_uuid",
            representative_timestamp=first_ts,
        ))
        burst_paths.update(m.path for m in members)

    # Move detected Live Photo pairs out of the user-visible bucket list
    # for the MVP — the stills will land in `individuals` below, and the
    # motion partners stay in `paired_videos` so they're not surfaced as
    # videos or motion clips. The count is preserved as diagnostic.
    result.live_photo_pairs_merged = len(result.live_photos)
    result.live_photos = []

    # ── 3. Individuals = images not in bursts ─────────────────────────
    # Live Photo stills (paired_stills) ARE included here as Individuals
    # for the MVP. The motion partner was already excluded from the videos
    # bucket via paired_videos and will be silently discarded at save
    # time per the only-Keep rule + § 24.4 "Still only" default. A future
    # iteration may revive a separate Live Photo bucket with a Keep ▶
    # kind submenu for the rare case where motion is worth keeping; the
    # `result.live_photos` list is preserved for that future use.
    individuals: list[IndividualPhoto] = []
    for img in image_entries:
        if img.path in burst_paths:
            continue
        individuals.append(IndividualPhoto(
            path=img.path,
            timestamp=_read_timestamp(img.exif),
            make=str(img.exif.get("Make", "") or ""),
            model=str(img.exif.get("Model", "") or ""),
        ))
    result.individuals = annotate_clusters(individuals, cfg)

    # ── 4. Videos not paired as Live Photo motion ────────────────────
    # Phone-source videos shorter than the Live-Photo motion duration are
    # treated as orphaned Live Photo motion fragments (still partner lost
    # during transfer — e.g. Google Photos shared album strips the basename
    # relationship). Routed to motion_clips bucket with default-discard
    # attitude. Real videos go to the Video bucket.
    for v in video_entries:
        if v.path in paired_videos:
            continue
        duration = _read_duration(v.exif)
        video_record = VideoFile(
            path=v.path,
            timestamp=_read_timestamp(v.exif),
            duration_s=duration,
        )
        if duration is not None and duration <= cfg.live_photo_max_video_duration:
            result.motion_clips.append(video_record)
        else:
            result.videos.append(video_record)

    # ── 5. Drop motion clips that duplicate an existing Individual ───
    # When a Live Photo's HEIC + MOV both arrive in the dump but with their
    # basename relationship broken (Google Photos shared-album upload
    # transcodes the MOV to MP4 with a different name), we end up with the
    # same scene appearing in two buckets: HEIC in Individuals, MP4 in
    # Motion Clips. The MP4 is redundant noise — same moment, lower
    # quality. Drop it from the scan when there is an Individual photo
    # within ±live_photo_timestamp_proximity.
    individual_timestamps = sorted(
        p.timestamp for p in result.individuals if p.timestamp is not None
    )
    surviving_clips: list[VideoFile] = []
    duplicates_dropped = 0
    for clip in result.motion_clips:
        if clip.timestamp is not None and _has_close_timestamp(
            clip.timestamp,
            individual_timestamps,
            cfg.live_photo_timestamp_proximity,
        ):
            duplicates_dropped += 1
            continue
        surviving_clips.append(clip)
    if duplicates_dropped:
        log.info(
            "Filtered %d motion clip(s) as duplicates of Individual photos "
            "(Live Photos with broken basename pairing)",
            duplicates_dropped,
        )
    result.motion_clips = surviving_clips
    result.motion_clips_filtered_as_duplicates = duplicates_dropped

    return result


def _has_close_timestamp(
    target: datetime,
    sorted_timestamps: list[datetime],
    threshold_seconds: float,
) -> bool:
    """O(log n) check whether `sorted_timestamps` contains any value within
    threshold_seconds of `target`. Both must be naive (or both aware)."""
    if not sorted_timestamps:
        return False
    idx = bisect.bisect_left(sorted_timestamps, target)
    candidates = []
    if idx > 0:
        candidates.append(sorted_timestamps[idx - 1])
    if idx < len(sorted_timestamps):
        candidates.append(sorted_timestamps[idx])
    for c in candidates:
        if abs((target - c).total_seconds()) <= threshold_seconds:
            return True
    return False


# ── Camera scan ────────────────────────────────────────────────────────────


def _detect_camera_bursts(
    orphans: list[tuple[RawExifEntry, BracketCandidate]],
    cfg: BucketScannerConfig,
) -> tuple[list[BurstSequence], set[Path]]:
    """Group continuous-shooting orphan frames into BurstSequences.

    Called after bracket detection has consumed focus/exposure brackets;
    everything passed in is a candidate for burst membership. Two signals
    are used, in order of preference:

      1. **EXIF sequence number** (per-brand, via ``BrandProfile.detect_burst``).
         When the camera writes a monotonically-increasing frame counter
         that resets to 1 at each new burst (Panasonic ``SequenceNumber`` —
         confirmed on G9 MkII in the Costa Rica field test), boundaries
         are deterministic: a strictly-increasing run is one burst; a
         reset (drop or jump-down) closes it.
      2. **Time-gap clustering** (fallback). When the brand profile doesn't
         declare a sequence tag, or the camera doesn't emit one, we group
         frames whose consecutive timestamps are within
         ``camera_burst_max_gap_seconds``.

    A run is closed when ANY of these changes:
      - body (Make/Model) — different camera
      - detection mode flips (sequence-number ↔ no-sequence-number)
      - sequence number is non-monotonic (closes the previous burst)
      - wall-clock gap exceeds ``camera_burst_max_gap_seconds``

    Runs of fewer than ``camera_burst_min_sequence_length`` frames are
    dropped — those photos go back to Individuals (where the moment-
    cluster post-pass may still annotate them).

    Frames whose timestamps are missing can't participate in a burst
    (the gap check would always fail). They fall through to Individuals.
    Frames where ``continuous_shooting_active`` is False likewise fall
    through — burst mode wasn't on, so by definition this isn't a burst
    frame regardless of timing.

    Returns ``(bursts, burst_paths)``. ``burst_paths`` is the set of
    photo paths consumed; the caller excludes these from Individuals.
    """
    from core.brand_profile import match_brand_profile_for_photo

    if not orphans:
        return [], set()

    # Only frames the camera flagged as continuous-mode can be in a burst.
    # Drop the rest immediately — they're individuals.
    continuous = [
        (e, c) for e, c in orphans
        if c.continuous_shooting_active and c.timestamp is not None
    ]
    if len(continuous) < cfg.camera_burst_min_sequence_length:
        return [], set()

    continuous.sort(key=lambda ec: ec[1].timestamp)

    # Cache the brand profile per Make string — typical scan has 1–2 makes.
    brand_cache: dict[str, object] = {}

    def _seq_for(entry: RawExifEntry) -> Optional[int]:
        make = str(entry.exif.get("Make", "") or "")
        if make not in brand_cache:
            brand_cache[make] = match_brand_profile_for_photo(entry.exif)
        brand = brand_cache[make]
        if brand is None:
            return None
        return brand.detect_burst(entry.exif)

    bursts: list[BurstSequence] = []
    burst_paths: set[Path] = set()
    run: list[tuple[RawExifEntry, BracketCandidate]] = []
    run_seqs: list[Optional[int]] = []

    def _close_run() -> None:
        if len(run) < cfg.camera_burst_min_sequence_length:
            return
        detection_source = (
            "sequence_number" if run_seqs[0] is not None else "time_gap"
        )
        seq = BurstSequence(
            burst_id=str(uuid.uuid4()),
            photos=[e.path for e, _ in run],
            detection_source=detection_source,
            representative_timestamp=run[0][1].timestamp,
        )
        bursts.append(seq)
        for e, _ in run:
            burst_paths.add(e.path)

    for entry, cand in continuous:
        seq_value = _seq_for(entry)

        if not run:
            run = [(entry, cand)]
            run_seqs = [seq_value]
            continue

        prev_entry, prev_cand = run[-1]
        prev_seq = run_seqs[-1]

        body_changed = prev_cand.body_id != cand.body_id
        mode_changed = (prev_seq is None) != (seq_value is None)
        in_seq_mode = prev_seq is not None and seq_value is not None
        seq_reset = in_seq_mode and seq_value <= prev_seq
        gap = (cand.timestamp - prev_cand.timestamp).total_seconds()
        # Two boundary signals, BOTH always applied:
        #  • sequence reset (seq mode) — back-to-back bursts with no
        #    time gap still split (a per-burst counter resets to 1);
        #  • wall-clock gap — a real pause means a new shooting event
        #    EVEN when the sequence number keeps climbing. The G9's
        #    SequenceNumber does NOT always reset between separate
        #    bursts (real Dia 9 B1: seq 1,2,10 spanning 335 s — "two
        #    different birds in different trees", Nelson eyeball
        #    2026-05-17), so the gap must split it. The threshold is
        #    sized for the G9's WHOLE-SECOND DateTimeOriginal: genuine
        #    intra-burst steps are 0-1 s, distinct events are >> 2 s
        #    (B1's was 334 s) — `camera_burst_max_gap_seconds` is 2.0,
        #    not the old 0.5 that chopped every whole-second burst.
        gap_too_big = gap > cfg.camera_burst_max_gap_seconds

        if body_changed or mode_changed or seq_reset or gap_too_big:
            _close_run()
            run = [(entry, cand)]
            run_seqs = [seq_value]
        else:
            run.append((entry, cand))
            run_seqs.append(seq_value)

    _close_run()
    return bursts, burst_paths


def scan_camera(
    entries: list[RawExifEntry],
    config: Optional[BucketScannerConfig] = None,
    detector_config: Optional[DetectorConfig] = None,
) -> BucketScanResult:
    """Scan camera photos and videos into the 5 camera buckets.

    Pipeline:
      1. Split videos vs images by extension.
      2. Build BracketCandidate per image (raw-EXIF only — no full
         classifier here; we just need temporal/contextual fields).
      3. ``detect_brackets`` consumes focus/exposure bracket sequences.
      4. ``_detect_camera_bursts`` groups continuous-mode orphans into
         Bursts via EXIF sequence number (preferred) or time-gap
         fallback.
      5. Anything not consumed by step 3 or 4 lands in Individuals,
         with moment-cluster annotation as a post-pass.
      6. Videos pass through to result.videos.
    """
    cfg = config or load_bucket_scanner_config()
    dcfg = detector_config or load_detector_config()
    result = BucketScanResult(source_kind=SourceKind.CAMERA)

    image_entries: list[RawExifEntry] = []
    video_entries: list[RawExifEntry] = []
    for e in entries:
        if _is_video(e.path, cfg.video_extensions):
            video_entries.append(e)
        else:
            image_entries.append(e)

    candidates = [
        _build_bracket_candidate_from_exif(e) for e in image_entries
    ]
    bracket_result = detect_brackets(candidates, dcfg)

    from core.vocabulary import BracketType
    for seq in bracket_result.sequences:
        if seq.sequence_type == BracketType.FOCUS:
            result.focus_brackets.append(seq)
        elif seq.sequence_type == BracketType.EXPOSURE:
            result.exposure_brackets.append(seq)

    sequence_paths: set[Path] = set()
    for seq in bracket_result.sequences:
        sequence_paths.update(seq.photos)

    # Camera bursts: continuous-shooting orphans grouped by sequence
    # number (preferred) or time-gap (fallback).
    orphan_pairs = [
        (e, c) for e, c in zip(image_entries, candidates)
        if e.path not in sequence_paths
    ]
    bursts, burst_paths = _detect_camera_bursts(orphan_pairs, cfg)
    result.bursts.extend(bursts)
    if bursts:
        log.info(
            "Detected %d camera burst(s) totaling %d frame(s)",
            len(bursts), sum(b.photo_count for b in bursts),
        )

    individuals: list[IndividualPhoto] = []
    for e in image_entries:
        if e.path in sequence_paths or e.path in burst_paths:
            continue
        individuals.append(IndividualPhoto(
            path=e.path,
            timestamp=_read_timestamp(e.exif),
            make=str(e.exif.get("Make", "") or ""),
            model=str(e.exif.get("Model", "") or ""),
        ))
    result.individuals = annotate_clusters(individuals, cfg)

    for v in video_entries:
        result.videos.append(VideoFile(
            path=v.path,
            timestamp=_read_timestamp(v.exif),
            duration_s=_read_duration(v.exif),
        ))

    return result


def _parse_orientation(value) -> int:
    """Parse the EXIF Orientation tag.

    ExifTool emits this tag as a human-readable string by default
    ('Horizontal (normal)') rather than the numeric 1..8 spec value, so a
    raw int() chokes on real-world data. Accept either form. Unknown
    string -> 1 (the most common 'normal' orientation).

    The bracket detector only uses orientation for equality comparison
    inside _same_context, so any consistent mapping is enough.
    """
    if value is None:
        return 1
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    s = str(value).strip().lower()
    if not s:
        return 1
    try:
        return int(s)
    except ValueError:
        pass
    text_to_int = {
        "horizontal (normal)": 1,
        "mirror horizontal": 2,
        "rotate 180": 3,
        "mirror vertical": 4,
        "mirror horizontal and rotate 270 cw": 5,
        "rotate 90 cw": 6,
        "mirror horizontal and rotate 90 cw": 7,
        "rotate 270 cw": 8,
    }
    return text_to_int.get(s, 1)


def _build_bracket_candidate_from_exif(entry: RawExifEntry) -> BracketCandidate:
    """Construct a BracketCandidate from raw EXIF.

    This is intentionally light — it does not call brand_profile / body_profile
    (which the full import pipeline does for richer normalization). The
    bracket detector only needs lens name / body id / orientation /
    timestamp / focal_length / aperture / shutter / iso / focus_distance /
    exposure_compensation / explicit bracket tags. Pulled directly from the
    raw EXIF dict here.
    """
    e = entry.exif

    def _num(v, default=0.0) -> float:
        if v is None:
            return default
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip()
        # exiftool sometimes returns "1/250" for shutter, "f/2.8" for aperture
        if s.startswith("f/"):
            s = s[2:]
        if "/" in s:
            try:
                num, den = s.split("/", 1)
                return float(num) / float(den)
            except (ValueError, ZeroDivisionError):
                return default
        try:
            # "55.0 mm" -> 55.0
            return float(s.split()[0])
        except (ValueError, IndexError):
            return default

    # Resolve brand-aware bracket signals when possible. Hard-coding
    # `FocusBracket` / `Bracketing` here was Panasonic-naive and missed
    # actual AEB sequences on the G9 II, which writes the bracket signal
    # to `BurstMode` (value "Auto Exposure Bracketing (AEB)"). Delegate
    # to the brand profile's detect_bracket so per-brand EXIF mappings
    # (panasonic.json, sony.json, …) are the single source of truth.
    # 2026-04-29 Costa Rica field test: AEB sequence in Day 5 was
    # mis-bucketed as 105 orphans because of the hard-coded tag names.
    from core.brand_profile import match_brand_profile_for_photo
    from core.vocabulary import BracketType
    brand = match_brand_profile_for_photo(e)
    if brand is not None:
        btype = brand.detect_bracket(e)
        focus_active = btype == BracketType.FOCUS
        exposure_active = btype == BracketType.EXPOSURE
    else:
        # Unknown brand (e.g. Nikon — no nikon.json yet). Best-effort
        # fallback: try the historical Panasonic-naive names AND the
        # universal Nikon/Sony/Canon ``ExposureBracketValue`` tag,
        # which is non-zero on every frame of a real AEB sequence.
        # Without this the detector falls through to the inferred
        # path and a plain burst with mid-sequence shutter jitter
        # gets mis-classified as an exposure bracket.
        focus_active = bool(e.get("FocusBracket"))
        exposure_active = bool(e.get("Bracketing"))
        if not exposure_active:
            ebv = e.get("ExposureBracketValue")
            try:
                if ebv is not None and float(ebv) != 0.0:
                    exposure_active = True
            except (TypeError, ValueError):
                pass

    # Continuous-shooting signal. Brand-aware first (so Panasonic's
    # ``BurstMode`` tag fires correctly — the substring fallback below
    # doesn't read that tag, so without the brand path Panasonic bursts
    # would silently look like single shots). OR'd with a brand-agnostic
    # substring fallback over the usual continuous-mode tag names so
    # unprofiled brands still get a best-effort detection.
    brand_says_continuous = brand.is_continuous_shooting(e) if brand else False
    cs_value = (
        e.get("ShootingMode")            # Nikon MakerNote
        or e.get("DriveMode")            # Panasonic / Olympus
        or e.get("ContinuousDrive")      # Canon
        or e.get("ReleaseMode")          # Sony
        or ""
    )
    cs_str = str(cs_value).lower()
    continuous_shooting_active = brand_says_continuous or (
        "continuous" in cs_str or "burst" in cs_str
    )

    # Brand-aware per-frame sequence counter (Panasonic ``SequenceNumber``,
    # etc. — resets to 1 at the start of every new burst / bracket). Used
    # by the windowing pass to split two back-to-back brackets that the
    # time-window heuristic would otherwise merge (Nelson 2026-06-06).
    # Brand-agnostic fallback reads the universal EXIF ``SequenceNumber``
    # tag so cameras without a brand profile still benefit.
    seq_n: Optional[int] = None
    if brand is not None:
        seq_n = brand.detect_burst(e)
    if seq_n is None:
        raw_seq = e.get("SequenceNumber")
        if raw_seq not in (None, "", []):
            try:
                n = int(float(str(raw_seq).strip()))
                seq_n = n if n > 0 else None
            except (TypeError, ValueError):
                seq_n = None

    return BracketCandidate(
        path=entry.path,
        timestamp=_read_timestamp(e),
        lens_name=str(e.get("LensModel") or e.get("LensType") or e.get("LensID") or ""),
        body_id=str(e.get("Model", "") or ""),
        orientation=_parse_orientation(e.get("Orientation")),
        focal_length=_num(e.get("FocalLength")),
        aperture=_num(e.get("FNumber") or e.get("Aperture")),
        shutter_speed=_num(e.get("ExposureTime") or e.get("ShutterSpeed")),
        iso=int(_num(e.get("ISO"))),
        focus_distance=(
            _num(e.get("FocusDistance")) if e.get("FocusDistance") is not None
            else None
        ),
        exposure_compensation=(
            _num(e.get("ExposureCompensation"))
            if e.get("ExposureCompensation") is not None
            else None
        ),
        focus_bracket_tag_active=focus_active,
        exposure_bracket_tag_active=exposure_active,
        continuous_shooting_active=continuous_shooting_active,
        sequence_number=seq_n,
    )


# ── Dispatcher ─────────────────────────────────────────────────────────────


def scan(
    entries: list[RawExifEntry],
    source_kind: SourceKind,
    config: Optional[BucketScannerConfig] = None,
) -> BucketScanResult:
    """Run the bucket scanner. Dispatches to scan_phone or scan_camera."""
    with log_activity(log, f"scan({source_kind.value}, n={len(entries)})"):
        if source_kind == SourceKind.PHONE:
            return scan_phone(entries, config)
        if source_kind == SourceKind.CAMERA:
            return scan_camera(entries, config)
        raise ValueError(f"Unknown source kind: {source_kind!r}")


def classify_bucket_scan_result(
    result: BucketScanResult,
    entries: list[RawExifEntry],
) -> None:
    """Run the v2 classifier on each individual photo and burst sequence
    and attach the result as ``suggested_scenario`` on every item.

    Mutates ``result`` in place — populates the new field on each
    ``IndividualPhoto`` and ``BurstSequence``. Bracket sequences and
    Live Photo pairs are left alone (brackets are deterministic in
    Tier 1 of the rule set, motion partners are dropped silently).

    On any error during classification (missing brand profile, broken
    rule file, etc.) the field stays None for that item and a warning
    is logged. The culler treats None as "no suggestion — fall back to
    standard menu" so a partial classifier failure doesn't block use.
    """
    # Imports kept local so bucket_scanner stays importable from contexts
    # that don't need the heavier classifier dependencies (e.g. tests of
    # the scanner itself, lightweight CLI tools).
    from core.brand_profile import match_brand_profile_for_photo
    from core.classifier_v2 import (
        classify,
        load_camera_rules,
        load_phone_rules,
    )
    from core.import_pipeline import _build_photo_context, _resolve_body_profile
    from core.lens_registry import load_lens_registry

    if not result.individuals and not result.bursts:
        return

    source = "phone" if result.source_kind == SourceKind.PHONE else "camera"

    # Load shared inputs once — these are stable across the scan.
    lens_registry = load_lens_registry()
    rules = (
        load_phone_rules() if source == "phone"
        else load_camera_rules()
    )

    exif_by_path: dict[Path, dict] = {e.path: e.exif for e in entries}

    def _classify_path(path: Path):
        """Build context for one path and run the classifier. Returns
        the ClassificationResult or None on failure."""
        exif = exif_by_path.get(path)
        if exif is None:
            return None
        try:
            brand = match_brand_profile_for_photo(exif)
            body = _resolve_body_profile(exif)
            lens_model = ""
            if brand is not None:
                lens_model = brand.lens_normalization.read_raw_lens(exif)
            lens = lens_registry.match(lens_model) if lens_model else None
            entry = RawExifEntry(path=path, exif=exif)
            ctx = _build_photo_context(entry, brand, body, lens, source=source)
            return classify(ctx, rules)
        except Exception as exc:  # noqa: BLE001 — classifier failures shouldn't break scan
            log.warning(
                "classifier failed for %s: %s — leaving suggestion empty",
                path.name, exc,
            )
            return None

    for photo in result.individuals:
        photo.suggested_scenario = _classify_path(photo.path)

    for seq in result.bursts:
        if seq.photos:
            seq.suggested_scenario = _classify_path(seq.photos[0])
