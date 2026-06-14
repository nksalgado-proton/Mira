"""Reconcile pipeline — orchestrator for retroactive trip processing.

Two-phase end-to-end:

* ``reconcile_scan(config)`` walks ``per_day_source``, builds a plan
  skeleton, returns it for the user to review and edit (add
  descriptions and ``[LOC:..]`` tags). No photo movement, no Event
  creation.
* ``reconcile_commit(config, plan_text)`` takes the user-edited plan,
  parses it, validates that every day has a non-empty description,
  builds the per-camera calibrations, copies photos into the standard
  ``00 - Captured/<bucket>/<day_folder>/<camera_id>/`` layout, and
  persists an ``Event`` JSON via ``data.event_store.save_event``.
  The result is identical in shape to a freshly-imported trip — the
  user can open it in the dashboard and start culling immediately,
  no further setup needed.

Source files are NEVER touched. Two branches on the copies (Model 3
amendment, 2026-05-21):

* **Live-card** imports (``ReconcileConfig.is_past_photos=False``,
  default): copies are byte-untouched (``shutil.copy2`` preserves
  EXIF + mtime). The calibration is used only to pick the right
  ``Dia N`` folder. The one-time correction is materialised LATER,
  at Cull-Export onto the keepers Export writes to ``01 - Culled``,
  by the same machinery the live-camera flow uses. ``00 - Captured``
  is the byte-for-byte mirror the SD-wipe safety gate
  (CLAUDE.md #9) requires.
* **Past-photos** imports (``is_past_photos=True``, set only by
  ``PastPhotosDialog``): no live card to wipe — the cards are long
  gone — so the byte-untouched constraint is meaningless. After
  ``shutil.copy2`` the calibration offset is baked into the EXIF of
  the copy in ``00 - Captured`` via
  ``core.exif_rewriter.rewrite_capture_time``. Source still never
  touched; ``UserComment`` audit marker preserved.

Inputs match Nelson's mental model for revisiting past trips:

1. ``per_camera_source`` — one subfolder per device with all originals
2. ``per_day_source`` — ``Dia N - LOC`` folders the user already
   organized manually, used to extract the calendar
3. ``trip_tz_offset`` — the trip's true TZ (Nepal = +5.75)
4. ``cameras`` — per-device declarations, each with optional
   ``configured_tz``, 0+ calibration pairs, ``is_phone`` (routes to
   ``_celulares`` vs ``_cameras``), and ``is_reference`` (exactly one)
5. ``photos_base_path`` + ``event_name`` + ``event_type`` — where
   the new event folder lands and how it's identified
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

from core import exif_rewriter
from core.source_index import SourceIndex


# Progress callback shape: ``(message, current, total)`` — message is
# a short human-readable status ("Walking G9 mkII..."), current/total
# track per-photo progress through the long EXIF-rewrite loop. ``total
# == 0`` means indeterminate (use spinner). Always called from the
# pipeline's own thread; the UI worker bridges to GUI thread via Qt
# signals. Optional — pass ``None`` to skip progress updates.
ProgressCallback = Callable[[str, int, int], None]

from core.clock_calibration import (
    CalibrationPair,
    CameraCalibration,
    build_calibration,
)
from core.event_service import create_event
from core.models import Event
from core.path_builder import (
    CAPTURED_CAMERAS_SUBDIR,
    CAPTURED_NO_TIMESTAMP_SUBDIR,
    CAPTURED_OTHER_SUBDIR,
    CAPTURED_PHONES_SUBDIR,
    captured_dir,
    day_folder_name,
    event_root_path,
)
from core.trip_plan_parser import parse_trip_plan
from core.trip_plan_skeleton import (
    generate_plan_skeleton_from_per_camera,
    generate_plan_skeleton_from_per_day,
)
from core.exif_reader import read_exif_batch
# Legacy ``data.event_store.save_event`` is used by ``reconcile_commit`` only,
# which MC's flow bypasses (it goes via the gateway). Imported lazily inside
# that function so this module loads cleanly without a legacy ``data/`` tree.

log = logging.getLogger(__name__)


# Media extensions Reconcile knows how to read + correct.
# Photos: timestamps via EXIF DateTimeOriginal.
# Videos: timestamps via QuickTime CreateDate / MediaCreateDate /
# TrackCreateDate (handled transparently by ``culler.exif_reader``'s
# fallback chain and ``core.exif_rewriter``'s video-mode write).
# GoPros routinely write video-only — calibration is TZ-only for
# them (no pair photo on the camera side); the pipeline applies the
# constant offset to whichever timestamp the file carries.
_PHOTO_EXTENSIONS: frozenset[str] = frozenset({
    ".rw2", ".raf", ".arw", ".nef", ".cr2", ".cr3", ".dng", ".orf", ".pef",
    ".jpg", ".jpeg",
    ".heic", ".heif",
    ".tif", ".tiff",
    # Videos (Phase 3)
    ".mp4", ".mov", ".m4v",
})


@dataclass
class CameraInput:
    """One camera's calibration inputs.

    ``camera_id`` matches the subfolder name in ``per_camera_source``.

    ``configured_tz`` is what the camera thought its TZ was when the
    photos were taken (e.g. ``-3.0`` for a São Paulo G9 brought to
    Nepal). ``None`` means unknown — system can't TZ-derive an offset
    and falls back entirely to pairs.

    ``calibration_pairs`` is 0+ pairs. Typical: 1 pair for confirming
    the TZ-derived expectation. Multiple pairs unlock drift correction
    via linear interpolation. Empty + TZ-only is also valid.

    ``is_phone`` routes the camera's photos to ``01 Captured/_celulares/``
    instead of ``01 Captured/_cameras/`` — matches the standard import
    bucket layout the rest of the pipeline expects.

    ``is_reference``: true for exactly ONE camera (the trusted-clock
    phone). Reference photos pass through uncorrected; pairs/TZ would
    typically be empty for the reference anyway.
    """
    camera_id: str
    configured_tz: Optional[float] = None
    calibration_pairs: list[CalibrationPair] = field(default_factory=list)
    is_phone: bool = False
    is_reference: bool = False


@dataclass
class ReconcileConfig:
    """Reconcile inputs.

    ``per_day_source`` is optional. When ``None`` (or empty/missing
    on disk), the scan derives the day skeleton from the reference
    camera's photos in ``per_camera_source`` — clustering by
    calendar date and emitting blank-description rows for the user
    to fill via the Describe Day dialog.

    Single-camera mode (Nelson 2026-05-20 v6): when
    ``per_camera_source`` itself contains media files directly
    (no per-camera subdirs — typical for an SD card's
    ``100GOPRO/`` style folder), the dialog passes
    ``single_camera_id`` set to the basename. The pipeline then
    treats ``per_camera_source`` itself as the one camera dir,
    skipping the per-subdir walk.
    """
    per_camera_source: Path
    per_day_source: Optional[Path]
    photos_base_path: Path
    event_name: str
    trip_tz_offset: float
    cameras: list[CameraInput]
    # When set, ``per_camera_source`` IS the single camera's
    # directory (not a parent of camera dirs). The value is the
    # camera_id to use.
    single_camera_id: Optional[str] = None
    # Past-photos branch (Model 3 amendment — FROZEN 2026-05-21,
    # Nelson; docs/14 + docs/18 + CLAUDE.md amended in lockstep).
    # The original Model 3 freeze (2026-05-19) said reconcile NEVER
    # rewrites EXIF — the rationale was the SD-card wipe gate: the
    # ``00 - Captured`` mirror must be byte-for-byte identical to
    # the source card so the wipe is safe to offer. Past-photos has
    # NO card to wipe (the cards are long gone — these are years-old
    # archives), so the "byte-untouched" constraint is meaningless
    # there. Setting this flag to True makes reconcile materialise
    # the calibration-derived correction into the EXIF of the
    # copies as they land in ``00 - Captured`` (same code path the
    # Cull-Export uses — ``core/exif_rewriter.rewrite_capture_time``).
    # The flag is False by default so live-card imports keep the
    # original Model 3 behaviour. Set to True ONLY from the
    # past-photos UI entry (``PastPhotosDialog``).
    is_past_photos: bool = False
    # EXIF-driven source index (Nelson 2026-05-21). When set, the
    # commit loop iterates the index's per-camera groups instead of
    # walking per-camera subfolders under ``per_camera_source``. The
    # index already carries the path list + per-file timestamps from
    # a single batched EXIF scan, so reconcile does NO second walk
    # in this mode. ``per_camera_source`` is still required (it's
    # used as the index root for logging + for the past-photos
    # ``00 - Captured`` event-tree placement); ``single_camera_id``
    # is ignored in this mode (the index handles single-camera vs
    # multi-camera transparently). The field is None by default so
    # the legacy folder-walk path keeps working (CLI tool +
    # existing tests). See ``core/source_index.py``.
    source_index: Optional["SourceIndex"] = None
    # Per-day-TZ calibration groups (Nelson 2026-05-22 — multi-TZ
    # ingest). When set, the bake step looks up each photo's day in
    # the plan, reads that day's ``tz_offset``, and uses the camera's
    # calibration from the matching group. Allows a trip that
    # crosses timezones (e.g. Nepal +5:45 with one day at India
    # +5:30) to bake each day's files with the right offset in a
    # single ingest pass.
    #
    # Shape: ``{tz_offset_in_hours: [CameraInput, ...]}``. Each
    # ``CameraInput`` carries the per-camera ``configured_tz`` +
    # pairs that the calibration dialog gathered FOR THAT TZ. The
    # group's ``tz_offset_in_hours`` key is the plan-side TZ; the
    # delta applied to each photo is computed as
    # ``tz_offset_in_hours − cam.configured_tz`` (or via pairs).
    #
    # When ``tz_camera_groups`` is None, falls back to ``cameras``
    # (the legacy single-TZ path — back-compat for the CLI tool +
    # offload pipeline + every pre-2026-05-22 caller).
    tz_camera_groups: Optional[dict[float, list[CameraInput]]] = None


@dataclass
class ReconcileWarning:
    """One non-fatal issue. ``severity`` is "info" / "warning" /
    "error" — UI color-codes; CLI prints all in a final report."""
    severity: str
    message: str
    path: Optional[Path] = None


@dataclass
class ScanResult:
    """Output of ``reconcile_scan``. ``plan_text`` is the skeleton
    the user edits (description + LOC) before the commit phase.

    ``folder_hints`` maps day_number → the original per-day folder
    location string when ``per_day_source`` was used; empty in the
    per-camera fallback path (no folder names to draw from).

    ``day_photo_samples`` maps day_number → reference-camera photo
    paths for that day; the Describe Day dialog samples 9 of these
    to render its 3×3 thumbnail grid.
    """
    plan_text: str
    day_dates: dict[int, date] = field(default_factory=dict)
    folder_hints: dict[int, str] = field(default_factory=dict)
    day_photo_samples: dict[int, list[Path]] = field(default_factory=dict)
    warnings: list[ReconcileWarning] = field(default_factory=list)


@dataclass
class CommitResult:
    """Output of ``reconcile_commit`` — the post-trip-equivalent state.

    ``event`` is the Event object that was persisted; ``event_root``
    is the on-disk root where photos landed. The Event is in
    ``%LOCALAPPDATA%/Mira/events/`` so it shows up
    in the Dashboard after the next refresh.

    ``photos_quarantined`` counts photos routed to
    ``01 Captured/_no_timestamp/<camera_id>/`` because they had no
    readable EXIF timestamp. ``photos_quarantined_renamed`` is the
    subset of those whose filenames got an mtime prefix so manual
    placement can lean on filename-sort chronology.
    """
    event: Optional[Event] = None
    event_root: Optional[Path] = None
    photos_processed: int = 0
    photos_skipped: int = 0
    photos_quarantined: int = 0
    photos_quarantined_renamed: int = 0
    # Photos whose corrected capture date didn't fall within any
    # ``Dia N`` in the plan — they're still imported, into the
    # ``_out_of_day_range`` sibling folder (Nelson 2026-05-21). Counts
    # in addition to ``photos_processed`` (not instead of).
    photos_out_of_day_range: int = 0
    # Task #120/#121 hybrid (Nelson 2026-05-23 C-option): photos that
    # had no readable EXIF but whose FILENAME carried a parseable
    # timestamp (Android ``IMG_YYYYMMDD_HHMMSS``, WhatsApp, double-
    # stamped exports). Those files skip the ``_no_timestamp`` /
    # quarantine, get their timestamp baked into EXIF, and land in
    # the proper day folder. The recovered time is treated as
    # wall-clock trip-local — no calibration applied. Counts in
    # addition to ``photos_processed``.
    photos_filename_recovered: int = 0
    photos_per_day: dict[int, int] = field(default_factory=dict)
    warnings: list[ReconcileWarning] = field(default_factory=list)


# ── Helpers ──────────────────────────────────────────────────────


def _list_camera_subdirs(source_dir: Path) -> list[Path]:
    return sorted(
        d for d in source_dir.iterdir()
        if d.is_dir() and not d.name.startswith((".", "_"))
    )


class _SingleCameraDir:
    """Path-like wrapper used by single-camera mode (Nelson
    2026-05-20 v6): the pipeline's per-camera loop reads
    ``camera_dir.name`` to key into the cameras_by_id map and
    walks the same path for photos. In single-camera mode the
    "camera dir" IS ``per_camera_source`` (no parent layer); we
    expose the user-chosen camera_id via ``.name`` and delegate
    everything else to the wrapped Path."""

    __slots__ = ("_path", "name")

    def __init__(self, path: Path, name: str) -> None:
        self._path = Path(path)
        self.name = name

    def rglob(self, pattern: str):
        return self._path.rglob(pattern)

    def __truediv__(self, other):
        return self._path / other

    def __fspath__(self) -> str:
        return str(self._path)

    def __str__(self) -> str:
        return str(self._path)


def _walk_photos(camera_dir: Path) -> list[Path]:
    return sorted(
        p for p in camera_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in _PHOTO_EXTENSIONS
    )


def _read_camera_times(
    photos: list[Path],
) -> dict[Path, Optional[datetime]]:
    if not photos:
        return {}
    entries = read_exif_batch(photos)
    return {e.path: e.timestamp for e in entries if e is not None}


def _build_calibrations_for_group(
    cameras: list[CameraInput],
    trip_tz: float,
) -> tuple[dict[str, CameraCalibration], list[ReconcileWarning]]:
    """Build per-camera calibrations for ONE TZ group. Same math as
    :func:`_build_calibrations` but parameterised so the per-day
    multi-TZ flow (Nelson 2026-05-22) can call it once per TZ."""
    calibrations: dict[str, CameraCalibration] = {}
    warnings: list[ReconcileWarning] = []
    for cam in cameras:
        if cam.is_phone:
            continue
        cal = build_calibration(
            cam.camera_id,
            cam.calibration_pairs,
            configured_tz=cam.configured_tz,
            trip_tz=trip_tz,
        )
        calibrations[cam.camera_id] = cal
        for w in cal.warnings:
            warnings.append(ReconcileWarning(severity="warning", message=w))
        if not cal.has_any_source:
            warnings.append(ReconcileWarning(
                severity="warning",
                message=(
                    f"camera {cam.camera_id!r} has no calibration source "
                    f"(no pairs, no configured TZ); photos will pass "
                    f"through uncorrected"
                ),
            ))
    return calibrations, warnings


def _build_calibrations(
    config: ReconcileConfig,
) -> tuple[dict[str, CameraCalibration], list[ReconcileWarning]]:
    """Build per-camera calibrations.

    Skips phones (they auto-sync TZ via NTP+location, so their EXIF
    is taken as-is). Processes everyone else — including the
    reference camera. Before 2026-05-08 the reference was skipped
    entirely; that left its TZ implicit and worked only when the
    reference was set to the trip's destination TZ. Now the
    reference also gets a ``trip_tz − configured_tz`` shift so plan
    dates and photo timestamps end up in trip-local terms regardless
    of what TZ the reference camera was configured to.
    """
    calibrations: dict[str, CameraCalibration] = {}
    warnings: list[ReconcileWarning] = []
    for cam in config.cameras:
        if cam.is_phone:
            # Phone EXIF is trip-local by NTP-sync convention — no
            # arithmetic to apply, no warning warranted.
            continue
        cal = build_calibration(
            cam.camera_id,
            cam.calibration_pairs,
            configured_tz=cam.configured_tz,
            trip_tz=config.trip_tz_offset,
        )
        calibrations[cam.camera_id] = cal
        for w in cal.warnings:
            warnings.append(ReconcileWarning(severity="warning", message=w))
        if not cal.has_any_source:
            warnings.append(ReconcileWarning(
                severity="warning",
                message=(
                    f"camera {cam.camera_id!r} has no calibration source "
                    f"(no pairs, no configured TZ); photos will pass "
                    f"through uncorrected"
                ),
            ))
    return calibrations, warnings


def _camera_bucket(cam: CameraInput) -> str:
    """Map a camera to its ``01 Captured/`` sub-bucket. Phones go to
    ``_celulares``; everything else (camera, action_cam) to
    ``_cameras``. ``_outros`` is reserved for non-photo media; not
    used by this pipeline."""
    if cam.is_phone:
        return CAPTURED_PHONES_SUBDIR
    return CAPTURED_CAMERAS_SUBDIR


def _validate_descriptions(days: list) -> Optional[ReconcileWarning]:
    """Refuse to commit when any day has an empty description — the
    user forgot to edit the skeleton. Return a fatal warning so the
    caller aborts before touching disk."""
    empty_days = [d.day_number for d in days if not d.description.strip()]
    if empty_days:
        return ReconcileWarning(
            severity="error",
            message=(
                f"days with empty descriptions: {empty_days}. Edit the "
                f"plan text to add a description for each day before "
                f"committing."
            ),
        )
    return None


# ── Phase A: scan ────────────────────────────────────────────────


def _reference_offset_hours(config: ReconcileConfig) -> float:
    """Compute the shift to apply to reference-camera timestamps so
    plan dates come out in trip-local terms.

    Returns 0 when there's no reference, when the reference is a
    phone (auto-syncs to trip-local already), or when the reference's
    configured_tz is unset. Otherwise returns
    ``trip_tz − reference.configured_tz`` — e.g. for a reference set
    to Dubai +4 on a Nepal +5:45 trip, returns +1.75.
    """
    ref = next((c for c in config.cameras if c.is_reference), None)
    if ref is None or ref.is_phone or ref.configured_tz is None:
        return 0.0
    return float(config.trip_tz_offset) - float(ref.configured_tz)


def _build_skeleton(config: ReconcileConfig):
    """Pick the skeleton source based on what the user provided.

    Per-day path is preferred because the folder names auto-fill
    descriptions. When per-day is missing, empty, or contains no
    ``Dia N - LOC`` folders, fall back to clustering the reference
    camera's photos by date — descriptions stay blank for the user
    to fill via the Describe Day dialog.

    Both code paths apply the reference camera's TZ offset (computed
    via ``_reference_offset_hours``) before extracting calendar
    dates, so the plan dates always land in trip-local terms.
    """
    ref_offset = _reference_offset_hours(config)
    per_day = config.per_day_source
    use_per_day = (
        per_day is not None
        and per_day.is_dir()
        and any(
            p.is_dir() and p.name.lower().startswith(("dia ", "day "))
            for p in per_day.iterdir()
        )
    )
    if use_per_day:
        return generate_plan_skeleton_from_per_day(
            per_day,
            home_tz_offset=config.trip_tz_offset,
            reference_offset_hours=ref_offset,
        ), "per_day"

    # Fallback: per-camera. Need the reference camera's photos.
    ref = next((c for c in config.cameras if c.is_reference), None)
    if ref is None:
        # No reference camera marked — can't fall back. Return empty.
        return None, "none"
    # EXIF-scan-first path (Nelson 2026-05-21): the source index has
    # already walked + EXIF-read every file, so we cluster the
    # reference camera's items in memory instead of walking again.
    if config.source_index is not None:
        ref_cam = config.source_index.cameras.get(ref.camera_id)
        if ref_cam is None:
            return None, "none"
        items = [(p, ref_cam.timestamps.get(p)) for p in ref_cam.paths]
        from core.trip_plan_skeleton import generate_plan_skeleton_from_items
        return generate_plan_skeleton_from_items(
            items,
            home_tz_offset=config.trip_tz_offset,
            reference_offset_hours=ref_offset,
            source_label=ref.camera_id,
        ), "per_camera"
    # Legacy folder-walk path: single-camera mode keeps
    # per_camera_source as the camera dir; multi-camera appends the
    # reference's camera_id (= subfolder name).
    if config.single_camera_id:
        ref_folder = config.per_camera_source
    else:
        ref_folder = config.per_camera_source / ref.camera_id
    return generate_plan_skeleton_from_per_camera(
        ref_folder,
        home_tz_offset=config.trip_tz_offset,
        reference_offset_hours=ref_offset,
    ), "per_camera"


def reconcile_scan(config: ReconcileConfig) -> ScanResult:
    """Build the plan skeleton — from per-day folders if present, or
    by clustering the reference camera's photos by date when not.
    No photo movement, no Event creation; this is the dry-run that
    feeds the editor."""
    skel, source = _build_skeleton(config)
    if skel is None:
        return ScanResult(
            plan_text="",
            warnings=[ReconcileWarning(
                severity="error",
                message=(
                    "no per-day folders and no reference camera marked; "
                    "either organize photos under 'Dia N - LOC' folders "
                    "or pick a reference camera in Step 2"
                ),
            )],
        )
    warnings = [
        ReconcileWarning(severity="warning", message=w)
        for w in skel.warnings
    ]
    if source == "per_camera":
        warnings.insert(0, ReconcileWarning(
            severity="info",
            message=(
                "no 'Dia N - LOC' folders found — derived skeleton from "
                "reference camera photos (one row per calendar date). "
                "Use the 📷 button on each row to fill descriptions."
            ),
        ))
    return ScanResult(
        plan_text=skel.plan_text,
        day_dates=skel.day_dates,
        folder_hints=skel.folder_hints,
        day_photo_samples=skel.day_photo_samples,
        warnings=warnings,
    )


# ── Phase B: commit ──────────────────────────────────────────────


def reconcile_commit(
    config: ReconcileConfig,
    plan_text: str,
    *,
    progress: Optional[ProgressCallback] = None,
) -> CommitResult:
    """Execute the full Reconcile pipeline.

    Validates the user-edited plan (every day must have a non-empty
    description), builds calibrations, copies photos into the
    standard ``01 Captured/<bucket>/<day_folder>/<camera_id>/``
    layout under ``<photos_base_path>/trips/<event>/``, rewrites
    EXIF on the copies (when enabled), and persists the Event JSON.

    The Event is saved to ``%LOCALAPPDATA%/Mira/events/``
    via ``data.event_store.save_event`` — refresh the Dashboard and
    it appears like a fresh import.

    ``progress`` is called repeatedly during the long photo-rewrite
    loop so the UI can update a real progress bar instead of an
    indeterminate spinner. Nepal-class trips run for several minutes;
    silent waiting felt broken to the user. Phases that aren't
    per-photo (validation, scan, calibration build) emit one
    ``(message, 0, 0)`` update each so the status label reflects the
    current step even when no countable progress is happening.
    """
    result = CommitResult()

    def _emit(msg: str, cur: int = 0, tot: int = 0) -> None:
        if progress is not None:
            progress(msg, cur, tot)

    _emit("Validating inputs...")

    # 1. Path validation
    if not config.per_camera_source.is_dir():
        result.warnings.append(ReconcileWarning(
            severity="error",
            message=f"per_camera_source not found: {config.per_camera_source}",
        ))
        return result
    # per_day_source is optional — when None or missing we'll derive
    # the year from the reference camera's photos in step 2.
    references = [c for c in config.cameras if c.is_reference]
    if len(references) != 1:
        result.warnings.append(ReconcileWarning(
            severity="error",
            message=(
                f"exactly one camera must have is_reference=True; "
                f"found {len(references)}"
            ),
        ))
        return result
    if not config.event_name.strip():
        result.warnings.append(ReconcileWarning(
            severity="error",
            message="event_name is required",
        ))
        return result

    _emit("Deriving trip year from photos...")

    # 2. Derive the trip year so the plan's ``(DD/MM)`` date hints
    # (no year by user convention) resolve to real calendar dates.
    # Without this, ``parse_trip_plan`` uses a year=1 sentinel and
    # ``Event.display_name`` ends up as ``1 - Trip Name``.
    # Reuse the same scan helper the UI uses so per-day vs per-camera
    # fallback stays consistent.
    scan_for_year, _ = _build_skeleton(config)
    inferred_start: Optional[date] = (
        min(scan_for_year.day_dates.values())
        if scan_for_year is not None and scan_for_year.day_dates else None
    )

    _emit("Parsing plan...")

    # 3. Parse plan + validate descriptions
    parsed_days = parse_trip_plan(
        plan_text,
        start_date=inferred_start,
        home_timezone=config.trip_tz_offset,
    )
    if not parsed_days:
        result.warnings.append(ReconcileWarning(
            severity="error",
            message="trip plan is empty or unparseable",
        ))
        return result
    desc_error = _validate_descriptions(parsed_days)
    if desc_error:
        result.warnings.append(desc_error)
        return result

    # Map calendar date → day_number for routing. Same-date duplicates
    # default to smallest day_number (Nepal Dia 7 + Dia 8 share 03/11
    # — Dia 7 wins; user moves outliers manually if needed).
    day_dates: dict[int, date] = {d.day_number: d.date for d in parsed_days}
    day_by_number: dict[int, object] = {d.day_number: d for d in parsed_days}

    def pick_day_for_date(d: date) -> Optional[int]:
        candidates = sorted(
            num for num, dt in day_dates.items() if dt == d
        )
        return candidates[0] if candidates else None

    _emit("Creating event folder structure...")

    # 3. Build the Event (in-memory; saved at the end)
    start_d = min(d.date for d in parsed_days)
    end_d = max(d.date for d in parsed_days)
    event = create_event(
        name=config.event_name,
        start_date=start_d,
        end_date=end_d,
        trip_plan_text=plan_text,
    )
    # Nelson 2026-05-22: no /trips/ insertion. The event root lives
    # directly under the user's chosen photos base, named for the event.
    # If the caller passed an already-resolved event root (the last
    # path segment matches the event folder name), use it verbatim.
    from core.path_builder import event_folder_name
    base = Path(str(config.photos_base_path))
    folder = event_folder_name(event)
    event_root = base if base.name == folder else base / folder
    # ``event.photos_base_path`` IS the absolute event root (single
    # source of truth post Nelson 2026-05-22).
    event.photos_base_path = str(event_root)
    result.event = event
    result.event_root = event_root

    # Create the spec/57 event folder skeleton on disk via the single
    # tree-birthing helper (Original Media + Edited Media + Cuts).
    from core.path_builder import ensure_event_tree
    event_root.mkdir(parents=True, exist_ok=True)
    ensure_event_tree(event_root)

    _emit("Building calibrations...")

    # 4. Build calibrations.
    # Single-TZ (legacy) path: one map ``camera_id → calibration`` and
    # one ``cameras_by_id`` for bucket lookup.
    # Multi-TZ path (Nelson 2026-05-22): a per-TZ map of those, plus
    # a day → TZ index so the bake loop can pick the right calibration
    # per-photo. The day → TZ index reads ``parsed_days[*].tz_offset``;
    # days with ``tz_offset is None`` inherit ``config.trip_tz_offset``
    # (the "main trip TZ"), keeping single-TZ trips a no-op.
    if config.tz_camera_groups:
        per_tz_calibrations: dict[float, dict[str, CameraCalibration]] = {}
        per_tz_cameras_by_id: dict[float, dict[str, CameraInput]] = {}
        for tz_key, cams in config.tz_camera_groups.items():
            cal_map, cal_warnings = _build_calibrations_for_group(
                cams, float(tz_key))
            result.warnings.extend(cal_warnings)
            per_tz_calibrations[float(tz_key)] = cal_map
            per_tz_cameras_by_id[float(tz_key)] = {
                c.camera_id: c for c in cams
            }
        # Day → TZ index. Fallback for days without an explicit
        # ``tz_offset`` is the main trip TZ (single-TZ behavior).
        day_to_tz: dict[int, float] = {
            d.day_number: float(
                d.tz_offset
                if d.tz_offset is not None
                else config.trip_tz_offset
            )
            for d in parsed_days
        }
        # Choose a "primary" group for bucket lookups + unknown-camera
        # warnings: the one matching the main trip TZ if present,
        # otherwise the first group declared. The legacy
        # ``calibrations`` / ``cameras_by_id`` names below alias the
        # primary so the per-photo branches keep working unchanged
        # when ``photo's day TZ == primary TZ``.
        primary_tz = (
            float(config.trip_tz_offset)
            if float(config.trip_tz_offset) in per_tz_calibrations
            else next(iter(per_tz_calibrations))
        )
        calibrations = per_tz_calibrations[primary_tz]
        cameras_by_id = per_tz_cameras_by_id[primary_tz]
    else:
        per_tz_calibrations = {}
        per_tz_cameras_by_id = {}
        day_to_tz = {}
        primary_tz = float(config.trip_tz_offset)
        calibrations, cal_warnings = _build_calibrations(config)
        result.warnings.extend(cal_warnings)
        cameras_by_id = {c.camera_id: c for c in config.cameras}

    # 5. Enumerate cameras to process. Two paths converge into one
    # list of (camera_id, photos, camera_times) jobs:
    #   a) ``source_index`` set (Nelson 2026-05-21, EXIF-scan-first
    #      path): pull groups straight from the pre-scanned index;
    #      no second walk, no second EXIF read.
    #   b) Legacy per-camera-subdir walk: the original contract, kept
    #      for the CLI tool + back-compat. ``single_camera_id`` =
    #      ``per_camera_source`` IS the one camera's dir.
    _emit("Counting photos...")
    if config.source_index is not None:
        jobs: list[tuple[str, list[Path], dict[Path, Optional[datetime]]]] = [
            (sc.camera_id, sc.paths, dict(sc.timestamps))
            for sc in config.source_index.cameras_sorted()
            if sc.file_count > 0
        ]
    else:
        if config.single_camera_id:
            camera_dirs = [
                _SingleCameraDir(
                    config.per_camera_source, config.single_camera_id)
            ]
        else:
            camera_dirs = _list_camera_subdirs(config.per_camera_source)
        if not camera_dirs:
            result.warnings.append(ReconcileWarning(
                severity="error",
                message=(
                    f"no camera subfolders under {config.per_camera_source}"
                ),
            ))
            return result
        jobs = []
        for camera_dir in camera_dirs:
            photos = _walk_photos(camera_dir)
            if not photos:
                continue
            jobs.append((camera_dir.name, photos, _read_camera_times(photos)))

    if not jobs:
        result.warnings.append(ReconcileWarning(
            severity="error",
            message=(
                "no photos found to import — the source index was "
                "empty or every camera subfolder was empty"
            ),
        ))
        return result

    total_photos = sum(len(photos) for _, photos, _ in jobs)
    photos_done = 0
    _emit(f"Processing {total_photos} photo(s)...", 0, total_photos)

    # Past-photos branch defers EXIF bakes until after the copy
    # loop ends, then runs them all in ONE persistent exiftool
    # process (-stay_open). Process-launch overhead dominates the
    # per-file path on Windows — ~0.5s × 2 launches × N files —
    # so for the 1300-photo Nepal trip this turned a 20-minute
    # bake into ~30 s (Nelson 2026-05-22). Empty for the legacy
    # live-card branch (which doesn't bake at this step).
    pending_bake: list[tuple[Path, datetime]] = []

    for camera_id, photos, camera_times in jobs:
        cam_input = cameras_by_id.get(camera_id)
        if cam_input is None:
            # Camera in source but not declared — pass through with warning
            result.warnings.append(ReconcileWarning(
                severity="warning",
                message=(
                    f"camera {camera_id!r} present in source but not "
                    f"declared in config; passing through uncorrected "
                    f"into {CAPTURED_OTHER_SUBDIR}"
                ),
            ))
            bucket = CAPTURED_OTHER_SUBDIR
            calibration: Optional[CameraCalibration] = None
        else:
            bucket = _camera_bucket(cam_input)
            calibration = calibrations.get(camera_id)

        for photo in photos:
            photos_done += 1
            # Throttle progress emits — the EXIF rewrite is the
            # bottleneck (~50ms each), so per-photo updates would
            # flood the GUI's signal queue without adding info.
            # Emit on every 10th file plus the last one for fast
            # cameras and rough alignment with what the user sees
            # changing on disk.
            if photos_done % 10 == 0 or photos_done == total_photos:
                _emit(
                    f"Processing {camera_id}: {photo.name} "
                    f"({photos_done}/{total_photos})",
                    photos_done, total_photos,
                )
            camera_t = camera_times.get(photo)
            recovered_from_filename = False
            if camera_t is None:
                # Task #120/#121 hybrid (Nelson 2026-05-23): try to
                # recover a timestamp from the FILENAME before
                # quarantining. Common cases — Android
                # ``IMG_YYYYMMDD_HHMMSS``, WhatsApp
                # ``IMG-YYYYMMDD-WA*``, double-stamped Google Drive
                # exports — carry a perfectly-good capture time in
                # the name even when EXIF is missing.
                #
                # The recovered time is treated as **wall-clock
                # trip-local** (Nelson's C-option freeze):
                # filename timestamps are usually already in local
                # time (the phone's TZ at capture), not the camera's
                # configured TZ, so applying the camera's clock
                # calibration would *shift them away* from accuracy.
                # We mark ``recovered_from_filename`` so the
                # calibration branch below skips this photo.
                from core.filename_timestamp import (
                    parse_timestamp_from_filename,
                )
                parsed = parse_timestamp_from_filename(photo.name)
                if parsed is not None:
                    camera_t = parsed.dt
                    recovered_from_filename = True
                    result.photos_filename_recovered += 1

            if camera_t is None:
                # Quarantine: route to ``01 Captured/_no_timestamp/
                # <camera_id>/`` instead of guessing a day from
                # mtime (which is unreliable on copies — placing
                # photos in the wrong day is more harmful than
                # leaving them aside for manual placement). Prefix
                # the filename with the file's mtime in
                # ``YYYY-MM-DD_HH-MM-SS`` form so a name-sort inside
                # the quarantine folder still groups photos in
                # rough chronological order.
                quarantine_dir = (
                    cap_dir / CAPTURED_NO_TIMESTAMP_SUBDIR / camera_id
                )
                quarantine_dir.mkdir(parents=True, exist_ok=True)
                renamed = False
                try:
                    mtime_dt = datetime.fromtimestamp(photo.stat().st_mtime)
                    prefix = mtime_dt.strftime("%Y-%m-%d_%H-%M-%S")
                    dest_name = f"{prefix}__{photo.name}"
                    renamed = True
                except OSError:
                    dest_name = photo.name
                dest = quarantine_dir / dest_name
                try:
                    shutil.copy2(photo, dest)
                except OSError as exc:
                    result.warnings.append(ReconcileWarning(
                        severity="error",
                        message=f"quarantine copy failed: {exc}",
                        path=photo,
                    ))
                    result.photos_skipped += 1
                    continue
                result.photos_quarantined += 1
                if renamed:
                    result.photos_quarantined_renamed += 1
                continue

            # Calibration is applied uniformly to every non-phone
            # camera, including the reference. ``_build_calibrations``
            # skips phones (their EXIF is trip-local by NTP-sync
            # convention) and builds entries for everyone else — so
            # ``calibration is None`` here only for phones, which
            # correctly fall through to the passthrough branch.
            # Before 2026-05-08 the reference was special-cased to
            # passthrough, but that left its TZ implicit and broke on
            # trips where the reference camera's TZ differed from the
            # trip TZ by more than a couple of hours.
            #
            # Multi-TZ awareness (Nelson 2026-05-22): when
            # ``tz_camera_groups`` is set, the camera's offset depends
            # on which day the photo was taken. Strategy:
            #   1. Tentatively apply the primary-TZ calibration to
            #      find which day the photo belongs to.
            #   2. Look up the day's TZ in ``day_to_tz``.
            #   3. If the day's TZ differs from primary, recompute
            #      with that TZ-group's calibration.
            # For single-TZ trips ``tz_camera_groups`` is None and the
            # branch collapses to the legacy single-calibration path.
            if recovered_from_filename:
                # Task #120/#121 hybrid: filename-recovered times are
                # already wall-clock trip-local (the typical Android /
                # WhatsApp / Drive-export convention). Skip the
                # camera-clock calibration — applying it would shift a
                # correct time into the camera's configured TZ, which
                # is the opposite of what we want.
                primary_t = camera_t
            elif calibration is not None and calibration.has_any_source:
                primary_t = camera_t + calibration.offset_at(camera_t)
            else:
                primary_t = camera_t
            corrected_t = primary_t

            day_num = pick_day_for_date(corrected_t.date())

            if (
                config.tz_camera_groups
                and day_num is not None
                and day_to_tz.get(day_num, primary_tz) != primary_tz
            ):
                day_tz = day_to_tz[day_num]
                group_cal = per_tz_calibrations.get(
                    day_tz, {}).get(camera_id)
                if group_cal is not None and group_cal.has_any_source:
                    corrected_t = (
                        camera_t + group_cal.offset_at(camera_t))
                    # Re-check the day in case the group's larger
                    # delta shifts the photo to a different day.
                    new_day_num = pick_day_for_date(corrected_t.date())
                    if new_day_num is not None:
                        day_num = new_day_num
            if day_num is None:
                # Nelson 2026-05-21: photos whose corrected capture
                # date falls outside the trip's day range are still
                # imported — they land in an ``_out_of_day_range``
                # folder alongside the ``Dia N`` folders (same
                # bucket / camera_id structure underneath). The user
                # can review them in the culler and either keep,
                # discard, or reorganise. Previously these were
                # SKIPPED entirely, which surprised the user when
                # e.g. an iPhone date-cluster spilled into a
                # pre-trip or post-trip day. The warning is logged
                # as ``info`` (informational, not actionable — the
                # photo is safely on disk).
                result.warnings.append(ReconcileWarning(
                    severity="info",
                    message=(
                        f"corrected date {corrected_t.date()} is "
                        f"outside the trip's day range; placed in "
                        f"_out_of_day_range"
                    ),
                    path=photo,
                ))
                day_dir = (
                    cap_dir / bucket / "_out_of_day_range" / camera_id
                )
            else:
                day_folder = day_folder_name(day_by_number[day_num])
                day_dir = (
                    cap_dir / bucket / day_folder / camera_id
                )
            day_dir.mkdir(parents=True, exist_ok=True)
            dest = day_dir / photo.name
            try:
                shutil.copy2(photo, dest)
            except OSError as exc:
                result.warnings.append(ReconcileWarning(
                    severity="error",
                    message=f"copy failed: {exc}",
                    path=photo,
                ))
                result.photos_skipped += 1
                continue

            result.photos_processed += 1
            if day_num is None:
                result.photos_out_of_day_range += 1
            else:
                result.photos_per_day[day_num] = (
                    result.photos_per_day.get(day_num, 0) + 1
                )

            # Model 3 v2 (FROZEN 2026-05-22; B-008 convergence
            # 2026-05-25): the per-file TZ correction is materialised
            # HERE, in ``00 - Captured``, whenever the corrected
            # timestamp differs from the camera's raw reading. After
            # this step ``00 - Captured`` is the canonical event
            # origin carrying TZ-correct EXIF.
            #
            # Live-card flow has its own bake step in the offload
            # dialog (``ui/pages/back_up_card_dialog`` calls
            # ``core.capture_bake.bake_offload_manifest`` after
            # ``verify_offload``). The live-card flow does NOT call
            # ``reconcile_commit`` at all — it has its own
            # source-→-Captured path (``event_backup_card.offload_to_captured``).
            # So this branch is reached only via the past-photos
            # dialog today; that's why ``is_past_photos`` was the
            # original gate. With the engine converged onto
            # ``capture_bake.bake_operations`` (one engine; called
            # by both paths via the right surface) the gate is no
            # longer needed — the only safety property worth
            # preserving is "don't bake if there's nothing to bake",
            # and ``corrected_t != camera_t`` already enforces that.
            #
            # ``corrected_t != camera_t`` covers both the legacy
            # single-TZ path (primary calibration produced a delta)
            # AND the multi-TZ path (a day's-TZ group recomputed
            # ``corrected_t``).
            if corrected_t != camera_t:
                # Defer the bake until after the copy loop completes
                # so all rewrites share ONE persistent exiftool
                # session (via ``capture_bake.bake_operations``).
                pending_bake.append((dest, corrected_t))

    # Drain the deferred-bake queue. Single engine call (B-008,
    # 2026-05-25) — was an inline loop using
    # ``exif_rewriter.rewrite_capture_times_batch`` + warning
    # mapping; now delegates to ``capture_bake.bake_operations``
    # which uses the same underlying batch primitive but is the
    # shared surface both ingest paths (live-card + past-photos)
    # consume.
    if pending_bake:
        from core.capture_bake import bake_operations

        _emit(
            f"Correcting capture timestamps in {len(pending_bake)} "
            f"photo(s)…",
            0, len(pending_bake),
        )

        def _bake_progress(msg: str, cur: int, tot: int) -> None:
            _emit(msg, cur, tot)

        bake_result = bake_operations(
            pending_bake, progress=_bake_progress,
        )
        for path, err in bake_result.errors:
            result.warnings.append(ReconcileWarning(
                severity="warning",
                message=f"EXIF rewrite failed on copy: {err}",
                path=path,
            ))

    _emit("Saving event JSON...", total_photos, total_photos)

    # 7. Persist the Event JSON (legacy path — MC bypasses this via the gateway).
    from data.event_store import save_event  # noqa: PLC0415 — legacy import
    save_event(event)
    log.info(
        "reconcile commit: event %s saved; %d photos processed "
        "(byte-untouched copies — EXIF correction materialised at "
        "Export per Model 3)",
        event.id, result.photos_processed,
    )
    return result
