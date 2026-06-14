"""Ingest pipeline — bytes-moving for the new event-creation flow
(spec/52, slice E.3).

Replaces the copy half of :mod:`core.reconcile_pipeline` for the new
model. The legacy module stays in place until slice E.5 rewrites
:mod:`mira.ui.pages.past_photos_dialog` to call this one, at
which point reconcile_pipeline + trip_plan_parser + trip_plan_skeleton
all retire together (slice E.7).

Why a fresh module instead of an extraction from reconcile_commit:

* The legacy ``reconcile_commit`` is ~530 lines tangled with plan-text
  parsing, year inference, ``data.event_store.save_event``, multi-TZ
  ``tz_camera_groups`` handling, and several per-camera declaration
  validations that the spec/52 model doesn't need. The new flow gives
  us the day plan + per-(camera, day) TZ pre-resolved by the time
  ingest runs, so the pipeline collapses to: route → copy → bake.

* The pipeline now takes a flat list of :class:`IngestPhotoJob` —
  each carries the source path + already-decided destination metadata
  (camera_id, is_phone, day_number, raw + corrected capture times).
  The host (slice E.5 wiring) assembles the jobs from
  :class:`core.scan_source.ScanResult` +
  :class:`mira.ui.pages.event_creation_flow.FlowResult`; that
  assembly logic is the consumer's concern, not this module's.

Path layout matches the existing convention (spec/03 / charter §3):

    <event_root>/
        00 - Captured/
            _cameras/<day_folder>/<camera_id>/
            _phones/<day_folder>/<camera_id>/
            _no_timestamp/<camera_id>/

Day folder format mirrors :func:`core.path_builder.day_folder_name`:
``Dia {N} - YYYY-MM-DD - description``. EXIF correction baking
delegates to :func:`core.capture_bake.bake_operations`, the shared
primitive both legacy ingest paths used.

Pure-ish module — does real filesystem I/O via ``shutil.copy2`` +
``Path.mkdir``. No Qt. Tests use ``tmp_path`` for the actual copies.
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from core.path_builder import (
    CAPTURED_CAMERAS_SUBDIR,
    CAPTURED_NO_TIMESTAMP_SUBDIR,
    CAPTURED_OTHER_SUBDIR,
    CAPTURED_PHONES_SUBDIR,
    captured_dir,
    sanitize_folder_name,
)

log = logging.getLogger(__name__)


#: Progress callback shape — same as :data:`core.reconcile_pipeline.ProgressCallback`.
#: ``(message, current, total)``. ``total == 0`` means indeterminate.
ProgressCallback = Callable[[str, int, int], None]


# --------------------------------------------------------------------------- #
# Input + output shapes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class IngestPhotoJob:
    """One source photo to ingest.

    The host has already resolved every routing question by the time
    the job is built:

    * ``camera_id`` keys the bucket subfolder + the leaf folder.
    * ``is_phone`` picks between ``_cameras`` and ``_phones``.
    * ``day_number`` + ``day_date`` + ``day_description`` build the
      day folder name.
    * ``capture_time_raw`` is the EXIF DateTimeOriginal as-read.
    * ``capture_time_corrected`` is the time AFTER applying any
      per-(camera, day) TZ offset. When equal to ``capture_time_raw``,
      no EXIF rewrite happens (saves the bake cost for already-correct
      cameras and phones).
    """

    source_path: Path
    camera_id: str
    is_phone: bool
    day_number: int
    day_date: Optional[date]
    day_description: str
    capture_time_raw: Optional[datetime]
    capture_time_corrected: Optional[datetime] = None


@dataclass(frozen=True)
class IngestWarning:
    """One issue surfaced during ingest. ``severity='info'`` is logged
    but not surfaced as an error (e.g. a quarantine notice)."""

    severity: str
    message: str
    path: Optional[Path] = None


@dataclass(frozen=True)
class JobOutcome:
    """Per-job copy result. ``sha256`` + ``byte_size`` are computed during
    the copy stream (one read of the source — Nelson 2026-06-08 eyeball:
    "why two passes and both take a long time"). ``destination`` is the
    absolute path the bytes landed at."""

    destination: Path
    sha256: str
    byte_size: int


@dataclass
class IngestResult:
    """Per-run counters + warnings. The host renders these in the
    progress dialog / a post-run summary.

    ``per_job_info`` carries the (sha256, byte_size, destination) for
    each successfully-copied job, keyed by the original source path so
    the host can write item rows to event.db without re-reading every
    file from disk.

    ``photos_duplicates`` counts jobs whose bytes were already ingested
    at their destination THIS run (identical sha256) — common when a
    backfill source carries the same file in several subtrees (a legacy
    event folder's captured + selected copies, spec/57 §4.3). Duplicates
    are ingested once and carry no ``per_job_info`` entry."""

    photos_copied: int = 0
    photos_skipped: int = 0
    photos_quarantined: int = 0
    photos_baked: int = 0
    photos_duplicates: int = 0
    warnings: List[IngestWarning] = field(default_factory=list)
    per_job_info: Dict[Path, JobOutcome] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Path helpers
# --------------------------------------------------------------------------- #


def day_folder_name(day_number: int, day_date: Optional[date], description: str) -> str:
    """Build the day folder name from raw fields. Mirrors
    :func:`core.path_builder.day_folder_name` but accepts primitives
    instead of a ``TripDay`` so the new flow doesn't have to build a
    schema object just to name a folder.

    Format: ``Dia {N} - YYYY-MM-DD - description``. Empty description
    drops the trailing segment; missing date drops the middle segment.
    """
    safe = sanitize_folder_name(description or "")
    parts = [f"Dia {day_number}"]
    if day_date is not None:
        parts.append(day_date.isoformat())
    if safe:
        parts.append(safe)
    return " - ".join(parts)


def _bucket_for(is_phone: bool, camera_id: str) -> str:
    """Top-level Captured subdir: phones in ``_phones``, cameras in
    ``_cameras``, no-camera-id in ``_other``."""
    if not camera_id:
        return CAPTURED_OTHER_SUBDIR
    return CAPTURED_PHONES_SUBDIR if is_phone else CAPTURED_CAMERAS_SUBDIR


def destination_for(
    event_root: Path, job: IngestPhotoJob,
) -> Path:
    """Compute the destination path for one job. Pure — no filesystem
    side effects (mkdir happens in :func:`run_ingest`). Exposed so
    callers can pre-check for collisions or render a dry-run preview."""
    cap = captured_dir(event_root)
    bucket = _bucket_for(job.is_phone, job.camera_id)
    if job.capture_time_raw is None:
        # Quarantine path — flat layout, no day folder.
        leaf = job.camera_id or "_unknown"
        return cap / CAPTURED_NO_TIMESTAMP_SUBDIR / leaf / job.source_path.name
    day_folder = day_folder_name(
        job.day_number, job.day_date, job.day_description,
    )
    leaf = job.camera_id or "_unknown"
    return cap / bucket / day_folder / leaf / job.source_path.name


# --------------------------------------------------------------------------- #
# The pipeline
# --------------------------------------------------------------------------- #


def _copy_and_hash(src: Path, dest: Path) -> tuple:
    """Stream-copy ``src`` → ``dest`` while computing sha256 of the bytes
    in flight. Returns ``(sha256_hex, byte_size)``. One read of the
    source — eliminates the second-pass disk read the legacy "hash the
    dest afterwards" approach needed (Nelson 2026-06-08).

    Mirrors :func:`shutil.copy2` for metadata (calls
    :func:`shutil.copystat` after the byte copy)."""
    import hashlib
    import shutil

    h = hashlib.sha256()
    size = 0
    with open(src, "rb") as fsrc, open(dest, "wb") as fdest:
        for chunk in iter(lambda: fsrc.read(256 * 1024), b""):
            h.update(chunk)
            fdest.write(chunk)
            size += len(chunk)
    shutil.copystat(src, dest)
    return h.hexdigest(), size


def _sha256_of_file(path: Path) -> str:
    """SHA-256 of a file on disk. Used to refresh the hash for jobs
    whose copies were modified by the EXIF bake step (the bake rewrites
    in place, so the hash captured during the copy no longer matches)."""
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(256 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _free_destination(dest: Path, claimed: Dict[Path, str]) -> Path:
    """First ``name (N).ext`` (N ≥ 2) sibling that is neither claimed by
    this run nor present on disk — the divert target when two DIFFERENT
    files map to one destination (same camera + day + filename). The
    Windows-Explorer suffix convention, matching the adoption path's
    collision style."""
    n = 2
    while True:
        candidate = dest.with_name(f"{dest.stem} ({n}){dest.suffix}")
        if candidate not in claimed and not candidate.exists():
            return candidate
        n += 1


def _ensure_event_structure(event_root: Path) -> None:
    """Create the spec/57 event folder skeleton (Original Media +
    Edited Media + Cuts) via the single tree-birthing helper —
    Nelson's first create-from-files run (2026-06-10) caught this
    pipeline still building the retired numbered trio."""
    from core.path_builder import ensure_event_tree
    event_root.mkdir(parents=True, exist_ok=True)
    ensure_event_tree(event_root)


def run_ingest(
    jobs: Sequence[IngestPhotoJob],
    event_root: Path,
    *,
    bake_corrections: bool = True,
    progress: Optional[ProgressCallback] = None,
) -> IngestResult:
    """Execute one full ingest run for the new event-creation flow.

    For each job: route → copy → bake (when the corrected capture
    time differs from the raw EXIF reading). The source files are
    NEVER touched (CLAUDE.md invariant — only the SD-wipe gate is
    allowed to remove user originals). All edits land on the copies
    under ``event_root``.

    When ``bake_corrections`` is ``False`` the EXIF rewrite is
    skipped — useful for dry runs, or for live-card ingest where the
    bake happens in a separate step.

    ``progress`` follows the same shape as
    :data:`core.reconcile_pipeline.ProgressCallback`. The function
    emits one message per 10 photos plus one for the bake batch.
    """
    result = IngestResult()

    def _emit(msg: str, cur: int = 0, tot: int = 0) -> None:
        if progress is not None:
            progress(msg, cur, tot)

    _emit("Preparing event folder structure...")
    _ensure_event_structure(event_root)

    total = len(jobs)
    if total == 0:
        return result

    _emit(f"Copying {total} photo(s)...", 0, total)
    pending_bake: list[tuple[Path, datetime]] = []
    # Destinations written (or kept) this run → their sha256. The seam
    # for same-destination handling below; also lets the host trust that
    # per_job_info destinations are unique (item.origin_relpath UNIQUE).
    claimed: Dict[Path, str] = {}

    for idx, job in enumerate(jobs, start=1):
        if idx % 10 == 0 or idx == total:
            _emit(
                f"Copying {job.source_path.name} ({idx}/{total})",
                idx, total,
            )

        dest = destination_for(event_root, job)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            result.warnings.append(IngestWarning(
                severity="error",
                message=f"mkdir failed for {dest.parent}: {exc}",
                path=job.source_path,
            ))
            result.photos_skipped += 1
            continue

        # Same-destination handling. A destination is NEVER blindly
        # overwritten (the pre-fix behavior silently destroyed the first
        # copy and then crashed the DB write on item.origin_relpath
        # UNIQUE — Nelson's first backfill run, 2026-06-10):
        #   * identical bytes already claimed this run → a duplicate
        #     (legacy backfill folders carry the same file in several
        #     subtrees) — ingested once, counted, no per_job_info entry;
        #   * identical bytes already on disk → resume of an interrupted
        #     run: keep the copy, report the outcome so the item row
        #     still gets recorded;
        #   * different bytes → divert to "name (N).ext"; both survive
        #     as distinct items. (With ``bake_corrections`` a re-run sees
        #     baked copies as "different" and diverts instead of
        #     re-baking — the live flow never bakes, legacy-only edge.)
        try:
            claimed_sha = claimed.get(dest)
            existing_sha = None
            if claimed_sha is None and dest.exists():
                existing_sha = _sha256_of_file(dest)
            if claimed_sha is not None or existing_sha is not None:
                src_sha = _sha256_of_file(job.source_path)
                if src_sha == claimed_sha:
                    result.photos_duplicates += 1
                    log.info(
                        "ingest: %s is a duplicate of %s — ingested once",
                        job.source_path, dest,
                    )
                    continue
                if src_sha == existing_sha:
                    claimed[dest] = src_sha
                    result.per_job_info[job.source_path] = JobOutcome(
                        destination=dest, sha256=src_sha,
                        byte_size=dest.stat().st_size,
                    )
                    if job.capture_time_raw is None:
                        result.photos_quarantined += 1
                    else:
                        result.photos_copied += 1
                    log.info(
                        "ingest: %s already in place at %s — kept",
                        job.source_path, dest,
                    )
                    continue
                dest = _free_destination(dest, claimed)
        except OSError as exc:
            result.warnings.append(IngestWarning(
                severity="error",
                message=f"collision check failed: {exc}",
                path=job.source_path,
            ))
            result.photos_skipped += 1
            continue

        try:
            sha, size = _copy_and_hash(job.source_path, dest)
        except OSError as exc:
            result.warnings.append(IngestWarning(
                severity="error",
                message=f"copy failed: {exc}",
                path=job.source_path,
            ))
            result.photos_skipped += 1
            continue

        claimed[dest] = sha
        result.per_job_info[job.source_path] = JobOutcome(
            destination=dest, sha256=sha, byte_size=size,
        )

        if job.capture_time_raw is None:
            result.photos_quarantined += 1
            log.info("ingest: quarantined %s → %s", job.source_path, dest)
            continue

        result.photos_copied += 1

        if (
            bake_corrections
            and job.capture_time_corrected is not None
            and job.capture_time_corrected != job.capture_time_raw
        ):
            pending_bake.append((dest, job.capture_time_corrected))

    if pending_bake and bake_corrections:
        _emit(
            f"Correcting capture timestamps in {len(pending_bake)} photo(s)…",
            0, len(pending_bake),
        )
        from core.capture_bake import bake_operations

        def _bake_progress(msg: str, cur: int, tot: int) -> None:
            _emit(msg, cur, tot)

        bake_result = bake_operations(pending_bake, progress=_bake_progress)
        result.photos_baked = len(pending_bake) - len(bake_result.errors)
        for path, err in bake_result.errors:
            result.warnings.append(IngestWarning(
                severity="warning",
                message=f"EXIF rewrite failed on copy: {err}",
                path=path,
            ))
        # Bake rewrites EXIF in place, so the sha256 captured during the
        # copy stream no longer matches the file on disk. Recompute the
        # hash for jobs whose dest was baked successfully — the per_job_info
        # entry stays the source-of-truth for the DB write step.
        bake_errors_set = {p for p, _ in bake_result.errors}
        dest_to_source = {
            v.destination: src for src, v in result.per_job_info.items()
        }
        for dest, _ in pending_bake:
            if dest in bake_errors_set:
                continue
            src = dest_to_source.get(dest)
            if src is None:
                continue
            try:
                new_sha = _sha256_of_file(dest)
            except OSError as exc:
                log.warning("ingest: rehash-after-bake failed for %s: %s",
                            dest, exc)
                continue
            old = result.per_job_info[src]
            try:
                new_size = dest.stat().st_size
            except OSError:
                new_size = old.byte_size
            result.per_job_info[src] = JobOutcome(
                destination=dest, sha256=new_sha, byte_size=new_size,
            )

    log.info(
        "ingest_pipeline: %d copied, %d quarantined, %d baked, %d skipped, "
        "%d warning(s)",
        result.photos_copied, result.photos_quarantined,
        result.photos_baked, result.photos_skipped,
        len(result.warnings),
    )
    return result
