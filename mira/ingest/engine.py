"""The ingest commit engine (spec/10 §4).

``run_ingest(plan, gateway)`` scans the source, calibrates each camera, copies the
originals **verbatim** into ``Original Media`` (no EXIF bake — charter §3), builds the
new-model records (``capture_time_raw`` never mutated + ``capture_time_corrected``), and
materialises one ``event.db`` via :meth:`Gateway.create_event`.

The calibration / scan / day-assignment / filename-recovery are reused as pure logic from
legacy ``core/`` (charter §5.2 — these have no data tendril; ported into ``mira/`` at
the §4-step-8 archive). Only the commit is new. The reused day-assignment + folder-naming
helpers type their day argument as the *legacy* ``TripDay`` but touch only
``.day_number`` / ``.date`` / ``.description``; :class:`_LegacyDayLike` duck-types that so
the engine never depends on the legacy ``TripDay``'s full field set.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date as _date, datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from core.clock_calibration import CameraCalibration, build_calibration
from core.day_assignment import assign_one, build_day_index, corrected_timestamp
from core.filename_timestamp import parse_timestamp_from_filename
from core.fresh_source import SourceItem, read_source_items
from core.path_builder import (
    CAPTURED_CAMERAS_SUBDIR,
    CAPTURED_NO_TIMESTAMP_SUBDIR,
    CAPTURED_PHONES_SUBDIR,
    ORIGINAL_MEDIA_DIR_NAME,
    day_folder_name,
)
from core.photo_thumb_pool import PhotoThumbPool
from core.video_discovery import VIDEO_EXTENSIONS

from mira.gateway import Gateway
from mira.ingest.model import IngestPlan, IngestResult
from mira.store import models as m

log = logging.getLogger(__name__)

# Legacy routes a dated-but-out-of-plan photo to a `_out_of_day_range` sibling within its
# bucket (core/reconcile_pipeline.py); mirror the name so the tree reads the same.
OUT_OF_DAY_RANGE_SUBDIR = "_out_of_day_range"
_UNKNOWN_CAMERA_FOLDER = "_unknown"

_HASH_CHUNK = 1 << 20  # 1 MiB


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class _LegacyDayLike:
    """Duck-types the legacy ``TripDay`` for the reused day-assignment + folder-naming
    helpers (which only read ``day_number`` / ``date`` / ``description``)."""

    day_number: int
    date: Optional[_date]
    description: str


def _hash_size(path: Path) -> Tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_HASH_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            h.update(chunk)
    return h.hexdigest(), size


def _trip_tz_hours(days) -> float:
    """Dominant per-day UTC offset (ties → earliest day); 0.0 if no days."""
    if not days:
        return 0.0
    counts = Counter(d.tz_offset_hours for d in days)
    top = max(counts.values())
    for d in sorted(days, key=lambda x: x.day_number):
        if counts[d.tz_offset_hours] == top:
            return float(d.tz_offset_hours)
    return float(days[0].tz_offset_hours)


def _calibration_map(plan: IngestPlan) -> Dict[str, Optional[CameraCalibration]]:
    """Per-``camera_id`` calibration: the pre-built one, else a TZ-only build, else None
    (phones / cameras with no answer pass through uncorrected)."""
    trip_tz = _trip_tz_hours(plan.days)
    out: Dict[str, Optional[CameraCalibration]] = {}
    for cam in plan.cameras:
        if cam.is_phone:
            out[cam.camera_id] = None
        elif cam.calibration is not None:
            out[cam.camera_id] = cam.calibration
        elif cam.configured_tz_hours is not None:
            out[cam.camera_id] = build_calibration(
                cam.camera_id, [], configured_tz=cam.configured_tz_hours, trip_tz=trip_tz
            )
        else:
            out[cam.camera_id] = None
    return out


def _tz_source(cal: Optional[CameraCalibration], recovered: bool) -> str:
    # spec/52: tz_source enum aligned to camera_day_tz.source values.
    # 'pair' → 'pair_picker'; 'tz' (declared via configured_tz_hours) → 'user_declared'.
    if recovered or cal is None:
        return "none"
    if cal.pairs:
        return "pair_picker"
    if cal.tz_offset is not None:
        return "user_declared"
    return "none"


def _exif_facets(si) -> dict:
    """Extract EXIF technical facets from a SourceItem for storage on the Item row.
    Returns None for unknown/zero values so the DB gets NULL (not 0), enabling
    clean range-query filters in the future exploration app."""
    return dict(
        iso=si.iso if si.iso else None,
        aperture_f=si.aperture if si.aperture else None,
        shutter_speed_s=si.shutter_speed if si.shutter_speed else None,
        focal_length_mm=si.focal_length if si.focal_length else None,
        flash_fired=si.flash_fired,   # False is a valid value (no flash); keep it
        lens_model=si.lens_model or None,
    )


def _dedup_dest(dest: Path, seen: set[str], event_root: Path) -> Path:
    """Return ``dest`` if its relpath hasn't been used yet, else append ``_2``,
    ``_3`` … to the stem until the relpath is fresh. Logs when a rename happens
    so the user can trace duplicate filenames."""
    candidate = dest
    counter = 2
    while candidate.relative_to(event_root).as_posix() in seen:
        candidate = dest.with_stem(f"{dest.stem}_{counter}")
        counter += 1
    if candidate != dest:
        log.warning(
            "filename collision: %s already used in this event; "
            "renaming copy to %s", dest.name, candidate.name
        )
    return candidate


def _copy_verify(src: Path, dest: Path, result: IngestResult) -> Tuple[str, int]:
    """Copy ``src`` → ``dest`` verbatim and verify byte-equality (sha256 + size). A
    mismatch is recorded (never silent). Returns the dest's (sha256, size)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    s_hash, s_size = _hash_size(src)
    d_hash, d_size = _hash_size(dest)
    if s_hash != d_hash or s_size != d_size:
        result.integrity_failures.append(dest.as_posix())
        log.error("integrity verify failed for %s", dest)
    return d_hash, d_size


def run_ingest(
    plan: IngestPlan,
    gateway: Gateway,
    *,
    source_items: Optional[List[SourceItem]] = None,
    now: Callable[[], str] = _utc_now_iso,
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> IngestResult:
    """Execute the ingest and return a summary (Create Event from Photos).

    ``source_items`` lets a caller (and tests) inject a pre-scanned list; otherwise the
    engine scans ``plan.source_root`` once (the heavy batched-EXIF read). ``progress`` —
    if given — is called ``progress(done, total, message)`` as each file is copied (the
    slow part) and once more before the database is written, so a UI can show a real
    progress bar (spec/05 §4b). Builds a whole ``EventDocument`` and materialises a fresh
    ``event.db`` via :meth:`Gateway.create_event`. (Capture — adding a card to an *existing*
    event — does not use this; it ports the legacy offload engine + records via
    ``mira.ingest.offload_record``, spec/13 §3.)"""
    def _report(done: int, total: int, message: str = "") -> None:
        if progress is not None:
            progress(done, total, message)

    result = IngestResult(event_id=plan.event_id)
    items: List[m.Item] = []
    stamp = now()

    if source_items is None:
        source_items = read_source_items(plan.source_root)
    total = len(source_items)

    cam_by_id = {c.camera_id: c for c in plan.cameras}
    cal_map = _calibration_map(plan)
    legacy_days = [_LegacyDayLike(d.day_number, d.date, d.description) for d in plan.days]
    by_date, by_number = build_day_index(legacy_days)

    cap = plan.event_root / ORIGINAL_MEDIA_DIR_NAME
    seen_camera_ids: set[str] = set()
    seen_dest_relpaths: set[str] = set()  # guard against same-name files in same dest dir

    for idx, si in enumerate(source_items, start=1):
        _report(idx, total, si.path.name)  # filename = data, not a translatable string
        camera_id = si.camera_id or ""
        seen_camera_ids.add(camera_id)
        cam_plan = cam_by_id.get(camera_id)
        is_phone = cam_plan.is_phone if cam_plan else False
        cam_folder = camera_id or _UNKNOWN_CAMERA_FOLDER
        bucket = CAPTURED_PHONES_SUBDIR if is_phone else CAPTURED_CAMERAS_SUBDIR
        kind = "video" if si.path.suffix.lower() in VIDEO_EXTENSIONS else "photo"
        # Persist the probed running time on videos (NULL for stills / unknown) so the
        # Capture time-share chart can weigh a full video by its real length.
        dur_ms = si.duration_ms if (kind == "video" and si.duration_ms) else None

        # 1. Resolve a raw capture time (EXIF, else recovered from filename).
        raw_t = si.timestamp
        recovered = False
        if raw_t is None:
            parsed = parse_timestamp_from_filename(si.path.name)
            if parsed is not None:
                raw_t = parsed.dt
                recovered = True
                result.filename_recovered += 1

        # 2. No usable time → quarantine (route by mtime-prefixed name, never guess a day).
        if raw_t is None:
            qdir = cap / CAPTURED_NO_TIMESTAMP_SUBDIR / cam_folder
            dest_name = si.path.name
            try:
                mtime = datetime.fromtimestamp(si.path.stat().st_mtime)
                dest_name = f"{mtime.strftime('%Y-%m-%d_%H-%M-%S')}__{si.path.name}"
            except OSError:
                pass
            dest = _dedup_dest(qdir / dest_name, seen_dest_relpaths, plan.event_root)
            seen_dest_relpaths.add(dest.relative_to(plan.event_root).as_posix())
            sha, size = _copy_verify(si.path, dest, result)
            items.append(m.Item(
                id=uuid.uuid4().hex, kind=kind,
                origin_relpath=dest.relative_to(plan.event_root).as_posix(),
                sha256=sha, byte_size=size,
                materialized_at=stamp, materialized_phase="ingest",
                camera_id=camera_id,
                capture_time_raw="", capture_time_corrected="", created_at=stamp,
                tz_offset_minutes=0, tz_source="none", day_number=None,
                duration_ms=dur_ms,
                quarantine_status="no_timestamp", recovered_from_filename=False,
                **_exif_facets(si),
            ))
            result.quarantined += 1
            result.videos += kind == "video"
            result.photos += kind == "photo"
            continue

        # 3. Correct + assign a day. Recovered (filename) times are already trip-local
        #    wall-clock → skip calibration (applying it would shift a correct time).
        cal = None if recovered else cal_map.get(camera_id)
        corrected = corrected_timestamp(raw_t, cal)
        assignment = assign_one(raw_t, cal, by_date, by_number)
        day_number = assignment.day_number

        if day_number is None:
            dest_dir = cap / bucket / OUT_OF_DAY_RANGE_SUBDIR / cam_folder
            result.out_of_day_range += 1
        else:
            dest_dir = cap / bucket / assignment.label / cam_folder

        dest = _dedup_dest(dest_dir / si.path.name, seen_dest_relpaths, plan.event_root)
        seen_dest_relpaths.add(dest.relative_to(plan.event_root).as_posix())
        sha, size = _copy_verify(si.path, dest, result)

        offset_min = 0
        if corrected is not None and raw_t is not None:
            offset_min = round((corrected - raw_t).total_seconds() / 60)

        items.append(m.Item(
            id=uuid.uuid4().hex, kind=kind,
            origin_relpath=dest.relative_to(plan.event_root).as_posix(),
            sha256=sha, byte_size=size,
            materialized_at=stamp, materialized_phase="ingest",
            camera_id=camera_id,
            capture_time_raw=raw_t.isoformat(),
            capture_time_corrected=(corrected or raw_t).isoformat(),
            created_at=stamp,
            tz_offset_minutes=offset_min, tz_source=_tz_source(cal, recovered),
            day_number=day_number, quarantine_status="ok",
            duration_ms=dur_ms,
            recovered_from_filename=recovered,
            **_exif_facets(si),
        ))
        result.videos += kind == "video"
        result.photos += kind == "photo"

    # 4. Assemble the EventDocument and materialise. Every item's camera_id needs a
    #    camera row (FK) — cover the union of the plan's cameras and everything scanned.
    doc = m.EventDocument(
        event=m.Event(
            uuid=plan.event_id, name=plan.event_name, created_at=stamp, updated_at=stamp,
            start_date=plan.start_date, end_date=plan.end_date,
        ),
        trip_days=[
            m.TripDay(
                day_number=d.day_number,
                date=d.date.isoformat() if d.date else None,
                description=d.description, location=d.location,
                tz_minutes=round(d.tz_offset_hours * 60),
                # spec/47 — per-day country code in extras_json (per schema convention
                # at trip_day.extras_json["country_code"]).
                extras_json=(
                    json.dumps({"country_code": d.country_code.upper()})
                    if d.country_code else '{}'
                ),
            )
            for d in plan.days
        ],
        cameras=_camera_rows(plan, cal_map, seen_camera_ids, stamp),
        items=items,
    )
    _report(total, total, "")  # copy done; writing the database (UI keeps its label)
    eg = gateway.create_event(doc, plan.event_root)
    eg.close()
    # BUGS.md B-012 — derive event start/end from trip_days. The Header
    # dialog no longer asks for From/To; whatever range the plan carried
    # is replaced by ``min(day.date) .. max(day.date)`` over the
    # trip_days the ingest just wrote.
    try:
        gateway.recompute_event_date_range(plan.event_id)
    except Exception:
        log.exception(
            "recompute_event_date_range failed after ingest %s",
            plan.event_id)

    # Pre-warm the photo thumb cache in the background. Captured photos are
    # immutable (charter §3) → each photo's thumb is written **once, ever**
    # right here at ingest, never re-checked at Cull entry. The pool feeds
    # daemon worker threads that decode + atomic-write the 256-px JPEG
    # under ``<event_root>/.cache/thumbs/photos/<sha256>.jpg``. We do NOT
    # block on completion — ingest returns immediately; thumbs trail in
    # background. If the user enters Cull before all thumbs are written,
    # the on-demand fallback in ``pick_page._decode_thumbnail`` covers any
    # not-yet-cached items (and writes them to the cache too).
    pool = PhotoThumbPool()
    for it in items:
        if it.kind != "photo" or not it.origin_relpath or not it.sha256:
            continue
        pool.enqueue(
            plan.event_root,
            plan.event_root / it.origin_relpath,
            it.sha256,
        )
    # Signal "no more work coming" — workers drain the queue then exit.
    # Daemon threads die with the interpreter regardless.
    pool.stop(wait=False)

    result.db_path = plan.event_root / "event.db"
    result.items_created = len(items)
    return result


def _camera_rows(plan, cal_map, seen_camera_ids, stamp) -> List[m.Camera]:
    rows: List[m.Camera] = []
    planned_ids = set()
    for cam in plan.cameras:
        planned_ids.add(cam.camera_id)
        cal = cal_map.get(cam.camera_id)
        applied = (
            round(cal.tz_offset.total_seconds() / 60)
            if cal is not None and cal.tz_offset is not None else None
        )
        # spec/52: m.Camera.is_reference retired (phone EXIF is the calibration
        # reference when present). cam.is_reference stays a plan-level concept
        # for the pair-picker UI; it does not persist into event.db.
        rows.append(m.Camera(
            camera_id=cam.camera_id, is_phone=cam.is_phone,
            configured_tz_minutes=(
                round(cam.configured_tz_hours * 60)
                if cam.configured_tz_hours is not None else None
            ),
            applied_offset_minutes=applied,
            applied_at=stamp if applied is not None else None,
        ))
    # Cameras seen in the scan but never answered for: a bare row so the FK holds.
    for cid in sorted(seen_camera_ids - planned_ids):
        rows.append(m.Camera(camera_id=cid))
    return rows
