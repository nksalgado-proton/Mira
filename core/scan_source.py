"""Scan source → ScanResult (slice E.2, spec/52 §2).

The orchestration layer between the existing EXIF/walk primitives and the
new event-creation dialogs. Replaces what
:func:`core.trip_plan_skeleton.generate_plan_skeleton_from_items` used to
do, but for the spec/52 model: photos are the source of truth, each day
is auto-filled from phone EXIF (country / TZ / location / description),
and the user's checkbox per day decides what to import.

Two layers:

* :func:`build_scan_result` — **pure logic** over a pre-read
  ``list[PhotoExif]``. Groups photos by capture day, derives the
  ``camera_id`` per file, runs :func:`core.autofill.autofill_for_day` per
  day, and packages everything into the
  :class:`~mira.ui.pages.event_creation_flow.FlowInputs`-shaped
  pieces the unified flow consumes. Testable with synthesized
  ``PhotoExif`` inputs.

* :func:`scan_source` — **thin wrapper** that does the actual file walk +
  EXIF batch read, then delegates to :func:`build_scan_result`. The
  expensive operation (``read_exif_batch``) is here, not in the logic,
  so the logic stays Qt-free + import-cheap and tests don't need
  ExifTool present.

This module is Qt-free. The host (slice E.6 wiring into MainWindow)
calls :func:`scan_source` off-thread via
:func:`mira.ui.base.progress.run_with_progress` per spec/05 §4b.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Set, Tuple

from core import autofill as _autofill
from core import phone_detector
from core.fresh_source import camera_id_for as _camera_id_for_raw
from core.ingest_pipeline import IngestPhotoJob
from core.peek_select import PeekCandidate
from core.tz_calibration import CameraDayPresence

if TYPE_CHECKING:
    from core.exif_reader import PhotoExif

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Day-row + override dataclasses — pure-logic shapes the Plan dialog (and any
# other consumer) reads + writes. Defined here so this module is self-
# sufficient (CLAUDE.md invariant #8 — pure-logic core/ modules carry no
# UI deps).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class OverrideMarker:
    """Set on a :class:`ScanDayRow` when an incremental ingest brought new
    phone data that differs from the existing per-day values (spec/52 §6.2).

    Carries both the existing and the new value for each conflicting field
    so the override-ask UI can render the side-by-side comparison without
    re-querying the gateway."""

    existing_country: str = ""
    existing_tz_minutes: Optional[int] = None
    existing_location: str = ""
    new_country: str = ""
    new_tz_minutes: Optional[int] = None
    new_location: str = ""


@dataclass
class ScanDayRow:
    """One day-row carried from the scan through the Plan dialog to ingest.

    Mutable so the Plan dialog can write user edits back into it; ``date``
    is scan output and never user-editable. ``checked`` defaults to
    ``True`` — the user opts a day OUT of the import, not in.
    ``override_marker`` is non-None only when an incremental ingest
    detected a per-day conflict (spec/52 §6.2)."""

    date: date
    checked: bool = True
    country_code: str = ""
    tz_minutes: Optional[int] = None
    location: str = ""
    description: str = ""
    override_marker: Optional[OverrideMarker] = None


# --------------------------------------------------------------------------- #
# Output shape — everything FlowInputs needs from a scan
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ScanPhotoRecord:
    """Minimum per-photo metadata for the ingest step (slice E.5).

    Captured during scan so :func:`build_ingest_jobs` doesn't have to
    re-walk + re-read EXIF after the user accepts the plan.
    ``day_number`` and ``capture_time_raw`` are both ``None`` when the
    EXIF DateTimeOriginal was unreadable; those records still ingest
    (routed to the ``_no_timestamp`` quarantine) so the user doesn't
    silently lose files."""

    source_path: Path
    camera_id: str
    is_phone: bool
    day_number: Optional[int]
    capture_time_raw: Optional[datetime]


@dataclass(frozen=True)
class PhoneScanSummary:
    """Per-scan rollup of phone-photo coverage across days, surfaced as a
    banner above the day list so the user immediately understands which
    days will pre-fill and which won't (Nelson 2026-06-08).

    A day "has phone TZ" when at least one phone photo on that day
    carries ``OffsetTimeOriginal``; "has phone GPS" when at least one
    phone photo has GPS coords. Both feed :func:`core.autofill.autofill_for_day`'s
    country / location / TZ pre-fill — a day without GPS will have a
    blank country + location even when phones are present.

    ``days_with_home_country_default`` / ``days_with_home_tz_default``
    track how many days fell back to the user's home settings (rather
    than being left blank). The coverage popup surfaces these so the
    user knows the defaults were assumed and verifies / overrides per
    day in the Plan dialog."""

    total_days: int = 0
    days_with_phone_photos: int = 0
    days_with_phone_tz: int = 0
    days_with_phone_gps: int = 0
    days_with_home_country_default: int = 0
    days_with_home_tz_default: int = 0


@dataclass(frozen=True)
class ScanResult:
    """Per-scan aggregated output.

    Fields map 1:1 to the shape
    :class:`~mira.ui.pages.event_creation_flow.FlowInputs` expects
    (minus ``home_tz_minutes`` and ``existing_offsets`` — those come from
    settings + gateway, not the scan).

    ``total_photos`` and ``untimestamped_count`` are diagnostics so the
    host can render a "(N photos scanned, M without DateTimeOriginal —
    those skipped)" hint above the Plan dialog.

    ``per_photo_records`` (slice E.5) carries the minimum per-file
    metadata :func:`build_ingest_jobs` needs after the flow accepts.

    ``phone_summary`` carries the per-day phone-photo coverage rollup
    (Nelson 2026-06-08) — the banner above the day list reads from it.
    """

    scan_rows: List[ScanDayRow]
    candidates_by_date: Dict[date, List[PeekCandidate]]
    day_date_lookup: Dict[int, date]
    day_tz_lookup: Dict[int, Optional[int]]
    presences: List[CameraDayPresence]
    per_photo_records: List[ScanPhotoRecord] = field(default_factory=list)
    total_photos: int = 0
    untimestamped_count: int = 0
    phone_summary: PhoneScanSummary = field(default_factory=PhoneScanSummary)
    # spec/57 §4.1 multi-date split: the raw inputs are RETAINED so the
    # split-confirm preview can regroup with a different day boundary
    # without re-walking the disk (build_scan_result is pure).
    photos: tuple = ()
    source_root: Optional[Path] = None
    day_start_minutes: int = 0


def effective_capture_date(ts: "datetime", day_start_minutes: int) -> date:
    """The capture DATE under a day boundary (spec/57 §4.1): photos taken
    before ``day_start_minutes`` past midnight belong to the PREVIOUS
    day — the "pull 00:30 night shots into the previous evening" rule.
    ``0`` = plain calendar dates."""
    if day_start_minutes <= 0:
        return ts.date()
    return (ts - timedelta(minutes=day_start_minutes)).date()


# --------------------------------------------------------------------------- #
# Pure-logic builder
# --------------------------------------------------------------------------- #


def _camera_id_for_photo(p: "PhotoExif") -> str:
    """Adapter — :func:`core.fresh_source.camera_id_for` takes a raw
    mapping; PhotoExif exposes ``model`` as a dedicated field and
    ``Make`` in its ``raw`` catch-all. Pre-builds the mapping so the
    canonical "Model first, Make fallback" rule fires."""
    raw = getattr(p, "raw", None) or {}
    return _camera_id_for_raw({
        "Make": raw.get("Make"),
        "Model": p.model,
    })


def _is_video(p: "PhotoExif") -> bool:
    """Video-vs-still classification for the peek-candidate flag.
    ``duration_seconds`` is the same field
    :class:`core.fresh_source.SourceItem` reads from EXIF; non-zero
    duration is a reliable video signal."""
    return float(getattr(p, "duration_seconds", 0.0) or 0.0) > 0.0


def build_scan_result(
    photos: Sequence["PhotoExif"],
    *,
    source_root: Path,
    home_country: Optional[str] = None,
    home_tz_minutes: Optional[int] = None,
    day_start_minutes: int = 0,
) -> ScanResult:
    """Pure-logic core. Group photos by capture day, run autofill per
    day, build the per-(camera, day) presence list + per-day peek
    candidates. Returns a :class:`ScanResult`.

    Day numbering is 1-based, ascending by date — day 1 = earliest
    date with any photos. Untimestamped photos (no
    ``DateTimeOriginal``) are NOT grouped into any day; they're
    counted in ``untimestamped_count`` so the host can surface them
    to the user.

    A photo with an empty ``camera_id`` (no readable Make/Model)
    doesn't contribute a presence row but still shows up in the
    peek candidate list — the user can still preview it.
    """
    by_day: Dict[date, List["PhotoExif"]] = {}
    untimestamped = 0
    for p in photos:
        if p.timestamp is None:
            untimestamped += 1
            continue
        day = effective_capture_date(p.timestamp, day_start_minutes)
        by_day.setdefault(day, []).append(p)

    sorted_dates: List[date] = sorted(by_day.keys())
    day_to_number: Dict[date, int] = {
        d: i + 1 for i, d in enumerate(sorted_dates)
    }

    scan_rows: List[ScanDayRow] = []
    day_date_lookup: Dict[int, date] = {}
    day_tz_lookup: Dict[int, Optional[int]] = {}
    days_with_home_country_default = 0
    days_with_home_tz_default = 0

    for day_number, day_date in enumerate(sorted_dates, start=1):
        day_photos = by_day[day_date]
        fill = _autofill.autofill_for_day(
            day_photos,
            source_root=source_root,
            home_country=home_country,
            home_tz_minutes=home_tz_minutes,
        )

        if fill.country_source == "home_default":
            days_with_home_country_default += 1
        if fill.tz_source == "home_default":
            days_with_home_tz_default += 1

        scan_rows.append(ScanDayRow(
            date=day_date,
            checked=True,
            country_code=fill.country_code or "",
            tz_minutes=fill.tz_minutes,
            location=fill.location or "",
            description=fill.description or "",
        ))
        day_date_lookup[day_number] = day_date
        day_tz_lookup[day_number] = fill.tz_minutes

    seen_pairs: Set[Tuple[str, int]] = set()
    presences: List[CameraDayPresence] = []
    for p in photos:
        if p.timestamp is None:
            continue
        camera_id = _camera_id_for_photo(p)
        if not camera_id:
            continue
        day_number = day_to_number[
            effective_capture_date(p.timestamp, day_start_minutes)]
        key = (camera_id, day_number)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        raw = getattr(p, "raw", None) or {}
        is_phone = phone_detector.is_phone(raw.get("Make"), p.model)
        presences.append(CameraDayPresence(
            camera_id=camera_id,
            day_number=day_number,
            is_phone=is_phone,
        ))
    # Stable ordering: by day then camera_id. Same convention as
    # core.tz_calibration.needs_calibration so the candidate lists line up.
    presences.sort(key=lambda r: (r.day_number, r.camera_id))

    candidates_by_date: Dict[date, List[PeekCandidate]] = {}
    for day_date, day_photos in by_day.items():
        candidates_by_date[day_date] = [
            PeekCandidate(
                path=p.path,
                timestamp=p.timestamp,
                is_video=_is_video(p),
                byte_size=0,                                # selector tolerates 0 = unknown
            )
            for p in day_photos
        ]

    # Per-photo records for ingest (slice E.5).
    per_photo_records: List[ScanPhotoRecord] = []
    for p in photos:
        camera_id = _camera_id_for_photo(p)
        raw = getattr(p, "raw", None) or {}
        is_phone = phone_detector.is_phone(raw.get("Make"), p.model)
        if p.timestamp is None:
            day_number = None
        else:
            day_number = day_to_number.get(
                effective_capture_date(p.timestamp, day_start_minutes))
        per_photo_records.append(ScanPhotoRecord(
            source_path=p.path,
            camera_id=camera_id,
            is_phone=is_phone,
            day_number=day_number,
            capture_time_raw=p.timestamp,
        ))

    # Phone coverage per day (Nelson 2026-06-08: banner above the day
    # list so the user can immediately tell why some days pre-filled
    # and others didn't).
    days_with_phone_photos = 0
    days_with_phone_tz = 0
    days_with_phone_gps = 0
    for day_date in sorted_dates:
        has_phone = False
        has_tz = False
        has_gps = False
        for p in by_day[day_date]:
            raw = getattr(p, "raw", None) or {}
            if not phone_detector.is_phone(raw.get("Make"), p.model):
                continue
            has_phone = True
            if p.tz_offset_minutes is not None:
                has_tz = True
            if p.gps_lat is not None and p.gps_lon is not None:
                has_gps = True
            if has_tz and has_gps:
                break                                       # nothing else this day can change
        if has_phone:
            days_with_phone_photos += 1
        if has_tz:
            days_with_phone_tz += 1
        if has_gps:
            days_with_phone_gps += 1

    phone_summary = PhoneScanSummary(
        total_days=len(sorted_dates),
        days_with_phone_photos=days_with_phone_photos,
        days_with_phone_tz=days_with_phone_tz,
        days_with_phone_gps=days_with_phone_gps,
        days_with_home_country_default=days_with_home_country_default,
        days_with_home_tz_default=days_with_home_tz_default,
    )

    return ScanResult(
        scan_rows=scan_rows,
        candidates_by_date=candidates_by_date,
        day_date_lookup=day_date_lookup,
        day_tz_lookup=day_tz_lookup,
        presences=presences,
        per_photo_records=per_photo_records,
        total_photos=len(photos),
        untimestamped_count=untimestamped,
        phone_summary=phone_summary,
        photos=tuple(photos),
        source_root=source_root,
        day_start_minutes=day_start_minutes,
    )


# --------------------------------------------------------------------------- #
# Ingest-job assembly — bridges Scan + Flow output to ingest_pipeline
# --------------------------------------------------------------------------- #


def build_ingest_jobs(
    scan: ScanResult,
    accepted_rows: Sequence[ScanDayRow],
    calibration_decisions: Dict[Tuple[str, int], int],
) -> List[IngestPhotoJob]:
    """Translate a completed scan + the user's accepted plan into the
    per-photo job list :func:`core.ingest_pipeline.run_ingest` consumes.

    Day-level routing:

    * ``accepted_rows`` carries the user's per-day decisions. A row with
      ``checked=False`` is excluded — its photos do not become jobs.
    * Untimestamped photos (``day_number is None``) emit a quarantine
      job regardless of the per-day decisions; the user shouldn't
      silently lose stripped-EXIF files.

    Per-photo capture-time correction:

    * If the user picked a calibration for ``(camera_id, day_number)``
      in DiscreteTzDialog (carried in ``calibration_decisions`` as
      minutes-east-of-UTC), the corrected time is
      ``raw + (day_tz - calibrated_tz)`` — the camera's clock was set
      to ``calibrated_tz``; we shift it to align with the day's actual
      TZ from the plan.
    * If no calibration was set for that pair, ``capture_time_corrected``
      stays equal to the raw EXIF time — no bake happens, the photo
      copies through with its EXIF intact.
    """
    accepted_by_date = {r.date: r for r in accepted_rows if r.checked}
    jobs: List[IngestPhotoJob] = []
    for rec in scan.per_photo_records:
        if rec.day_number is None or rec.capture_time_raw is None:
            # Quarantine path — always run, even if the day is unchecked
            # (the user can't make a decision on a date we couldn't read).
            jobs.append(IngestPhotoJob(
                source_path=rec.source_path,
                camera_id=rec.camera_id,
                is_phone=rec.is_phone,
                day_number=0,
                day_date=None,
                day_description="",
                capture_time_raw=None,
                capture_time_corrected=None,
            ))
            continue

        day_date = scan.day_date_lookup.get(rec.day_number)
        if day_date is None:
            continue                                                # defensive
        accepted = accepted_by_date.get(day_date)
        if accepted is None:
            continue                                                # day unchecked

        corrected = rec.capture_time_raw
        key = (rec.camera_id, rec.day_number)
        if key in calibration_decisions and accepted.tz_minutes is not None:
            offset = accepted.tz_minutes - calibration_decisions[key]
            corrected = rec.capture_time_raw + timedelta(minutes=offset)

        jobs.append(IngestPhotoJob(
            source_path=rec.source_path,
            camera_id=rec.camera_id,
            is_phone=rec.is_phone,
            day_number=rec.day_number,
            day_date=day_date,
            day_description=accepted.description or "",
            capture_time_raw=rec.capture_time_raw,
            capture_time_corrected=corrected,
        ))
    return jobs


# --------------------------------------------------------------------------- #
# Thin wrapper — file walk + EXIF read, then delegate
# --------------------------------------------------------------------------- #


def scan_source(
    source_path: Path,
    *,
    home_country: Optional[str] = None,
    home_tz_minutes: Optional[int] = None,
    day_start_minutes: int = 0,
) -> ScanResult:
    """Walk ``source_path``, read EXIF in one batch, return the
    :class:`ScanResult`.

    The expensive operation (``core.exif_reader.read_exif_batch``) lives
    here so the host can run it off-thread via
    :func:`mira.ui.base.progress.run_with_progress`. Missing or
    empty source → empty ``ScanResult`` (the host renders a "(no
    photos at this path)" hint).

    ``home_country`` / ``home_tz_minutes`` thread the user's home
    settings down to :func:`core.autofill.autofill_for_day` so a day
    with no phone GPS / TZ doesn't end up blank — see the docstring
    on :func:`autofill_for_day` (Nelson 2026-06-08).
    """
    from core.exif_reader import read_exif_batch
    from core.folder_scanner import walk_photo_paths
    from core.video_discovery import VIDEO_EXTENSIONS

    source_path = Path(source_path)
    if not source_path.exists():
        return _empty_result()

    try:
        photos: List[Path] = list(walk_photo_paths(source_path))
    except (FileNotFoundError, NotADirectoryError):
        photos = []
    videos: List[Path] = []
    for p in source_path.rglob("*"):
        try:
            if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS:
                videos.append(p)
        except OSError:
            continue
    files = photos + videos
    if not files:
        return _empty_result()

    exifs = read_exif_batch(files)
    return build_scan_result(
        exifs, source_root=source_path,
        home_country=home_country,
        home_tz_minutes=home_tz_minutes,
        day_start_minutes=day_start_minutes,
    )


def _empty_result() -> ScanResult:
    return ScanResult(
        scan_rows=[],
        candidates_by_date={},
        day_date_lookup={},
        day_tz_lookup={},
        presences=[],
        total_photos=0,
        untimestamped_count=0,
        phone_summary=PhoneScanSummary(),
    )
