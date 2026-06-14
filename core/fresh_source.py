"""Fresh-source → navigator day list (Stage B.3b, increment 1).

docs/18 §"Culling contexts" → the **B.3b** note (frozen
2026-05-18): the fresh-source cull (camera card / phone dump /
other) reuses the **exact same navigator** as Home consolidation —
only the *source of the day grouping* differs. Home groups by
folder name; a fresh source groups by each photo's **corrected**
capture time mapped to the event plan's ``Dia N``.

The brain already exists and is tested: :mod:`core.day_assignment`
(``assign_days`` → ``{path: DayAssignment}``, reusing
:mod:`core.clock_calibration` for the per-camera offset). This
module is the thin adapter that walks a source, reads EXIF, looks
each camera's calibration up in the passed map, runs ``assign_days``
and groups the result into the navigator's existing
:class:`~core.bucket_navigator_model.DayFolder` shape.

Because the output is ``DayFolder`` objects, the **entire lazy /
off-thread / cached / Moment-view machinery** and
``BucketCullShell.set_day_folders`` are reused unchanged — the
SIGSEGV-safe lazy design covers fresh sources for free, and the
heavy per-day scan still runs only on day-open.

``calibrations`` is an explicit parameter (decoupled, deterministic
tests). A camera with no entry (or ``None``) passes through
uncorrected — Phone passes ``{}`` (phone clocks auto-sync; the
frozen ``needs_clock_check=False``). Day keys are plan-derived
(``day_folder_name``), so per-day / per-bucket journals are stable
across re-scans (same guarantee as Home). Brain-only: nothing is
moved or quarantined; Export stays the sole file-writer. Qt-free.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Optional, Sequence

from core.bucket_navigator_model import DayFolder
from core.clock_calibration import CameraCalibration, build_calibration
from core.day_assignment import UNDATED_LABEL, assign_days
from core.models import TripDay

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceItem:
    """One scanned source file: its path, capture time (``None`` if
    unreadable) and ``camera_id`` (``camera_id_for`` over its EXIF).
    The single EXIF read produces these once; camera detection and
    day grouping then work in-memory (no double scan of a big card —
    the SIGSEGV-safe constraint).

    spec/45 fields (``tz_offset_minutes``, ``gps_lat``, ``gps_lon``) are
    ``None`` when the underlying EXIF tag was absent. The spec/45 phone
    detection heuristic counts items with a non-``None`` ``tz_offset_minutes``
    against the source's total — modern phones write
    ``OffsetTimeOriginal`` consistently; dedicated cameras virtually never
    do, so a high fill rate is the load-bearing signal."""

    path: Path
    timestamp: Optional[datetime]
    camera_id: str
    tz_offset_minutes: Optional[int] = None
    gps_lat: Optional[float] = None
    gps_lon: Optional[float] = None
    # Exposure quartet captured in the SAME single EXIF pass (defaults 0 = unknown). Lets
    # surfaces show the exposure readout without a second per-photo read (Nelson 2026-06-01,
    # Fast-Culler speed). Field names match ``ExifData`` so the cull caption helpers accept a
    # SourceItem directly.
    shutter_speed: float = 0.0
    aperture: float = 0.0
    iso: int = 0
    focal_length: float = 0.0
    flash_fired: bool = False   # from ExifData.flash_fired — stored on captured items
    lens_model: str = ''        # from ExifData.lens — lens string for exploration queries
    # Video running time in ms (0 for stills / unknown), read from the same EXIF pass
    # (ExifTool ``Duration``). Persisted on the captured video item so the Capture-phase
    # time-share chart can weigh a full video by its real length (Nelson 2026-06-01).
    duration_ms: int = 0


def camera_id_for(raw: Mapping) -> str:
    """Compact camera identity used to key the per-camera calibration
    map. ``Model`` alone is the canonical id — Make is only used as a
    fallback when Model is missing.

    THE source of truth for fresh-source camera identity — the
    clock-timezone dialog derives its per-camera question list from
    this same string, so the keys line up. (Mirrors
    ``bucket_navigator_model._camera_str`` — that one is for the
    provenance row; kept separate so the two concerns stay decoupled.)

    Nelson eyeball 2026-05-20 (Chapada Diamantina GoPro): the same
    camera body writes ``Make=GoPro, Model=HERO12 Black`` on stills
    but only ``Model=HERO12 Black`` on video. The old "Make Model"
    join produced two distinct ids for one camera and triggered a
    redundant per-camera clock dialog. Model alone is the reliable
    unique id; Make is a brand qualifier and effectively noise for
    keying purposes."""
    make = str((raw or {}).get("Make", "") or "").strip()
    model = str((raw or {}).get("Model", "") or "").strip()
    return model or make


def read_source_items(source_root: Path) -> list[SourceItem]:
    """Walk ``source_root`` and do the **single** EXIF read → one
    :class:`SourceItem` per media file. This is the heavy step
    (``read_exif_batch``); the camera-clock dialog runs it **once
    off-thread**, then both camera detection (:func:`cameras_in`)
    and day grouping (:func:`group_items_to_days`) work on the
    returned list in memory — never a second scan of a big card.
    Missing / empty root → ``[]``."""
    from core.exif_reader import read_exif_batch
    from core.folder_scanner import walk_photo_paths
    from core.video_discovery import VIDEO_EXTENSIONS

    source_root = Path(source_root)
    try:
        photos = list(walk_photo_paths(source_root))
    except (FileNotFoundError, NotADirectoryError):
        photos = []
    vids = (
        [
            p for p in source_root.rglob("*")
            if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
        ]
        if source_root.exists() else []
    )
    files = photos + vids
    if not files:
        return []
    return [
        SourceItem(path=pe.path, timestamp=pe.timestamp,
                   camera_id=camera_id_for(pe.raw or {}),
                   shutter_speed=getattr(pe, "shutter_speed", 0.0) or 0.0,
                   aperture=getattr(pe, "aperture", 0.0) or 0.0,
                   iso=getattr(pe, "iso", 0) or 0,
                   focal_length=getattr(pe, "focal_length", 0.0) or 0.0,
                   flash_fired=bool(getattr(pe, "flash_fired", False)),
                   lens_model=str(getattr(pe, "lens", "") or "").strip(),
                   duration_ms=round((getattr(pe, "duration_seconds", 0.0) or 0.0) * 1000))
        for pe in read_exif_batch(files)
    ]


def cameras_in(items: Sequence[SourceItem]) -> list[tuple[str, int]]:
    """Distinct non-empty camera ids in ``items`` → ``(id, count)``,
    most-shots-first then name. This is the list the clock-timezone
    dialog asks about (one Yes/No per camera); an empty id (no
    readable Make/Model) is omitted — it has nothing to key a
    per-camera calibration on and passes through uncorrected."""
    ct: Counter = Counter(
        it.camera_id for it in items if it.camera_id)
    return [cam for cam, _ in sorted(
        ct.items(), key=lambda kv: (-kv[1], kv[0]))]  # type: ignore[misc]


def build_tz_calibrations(
    wrong_tz_by_camera: Mapping[str, float],
    trip_tz: float,
) -> dict[str, CameraCalibration]:
    """Turn the dialog's answers into the calibration map.

    ``wrong_tz_by_camera`` holds **only** the cameras the user said
    were NOT on the trip's timezone, mapped to the UTC offset (in
    hours) the camera's clock *was* actually set to. Each becomes a
    constant-offset :class:`CameraCalibration`
    (``trip_tz - configured_tz``). Cameras whose clock was correct
    are simply absent → pass-through (no entry). ``trip_tz`` is the
    destination's offset in hours (e.g. Nepal ``+5.75``)."""
    return {
        cam: build_calibration(
            cam, [], configured_tz=configured, trip_tz=trip_tz)
        for cam, configured in wrong_tz_by_camera.items()
    }


def plan_trip_tz(trip_days: Sequence[TripDay]) -> float:
    """The event plan's trip timezone (UTC-offset hours) for the
    camera-clock correction. In an event the plan already carries
    per-day ``tz_offset`` — the user must NOT be re-asked for it
    (Nelson eyeball 2026-05-18). Picks the **most common** per-day
    offset (a single-tz trip like Nepal → that one value); ties go
    to the earliest day. No dated offsets → ``0.0``. Multi-tz trips
    use the predominant zone (a documented v1 simplification — the
    per-camera offset is constant anyway)."""
    offs = [
        d.tz_offset for d in trip_days
        if getattr(d, "tz_offset", None) is not None
    ]
    if not offs:
        return 0.0
    ct = Counter(offs)
    top = max(ct.values())
    for d in sorted(trip_days, key=lambda x: x.day_number):
        if d.tz_offset is not None and ct[d.tz_offset] == top:
            return float(d.tz_offset)
    return float(offs[0])


def group_items_to_days(
    items: Sequence[SourceItem],
    trip_days: Sequence[TripDay],
    calibrations: Mapping[str, Optional[CameraCalibration]],
) -> list[DayFolder]:
    """Correct each item's capture time per its camera's calibration,
    assign to a plan ``Dia N`` and group into navigator
    :class:`DayFolder` rows. Dated days first (by plan
    ``day_number``); a trailing :data:`UNDATED_LABEL` day collects
    files with no usable timestamp or whose corrected date matches
    no Dia. No EXIF read here (works on pre-read ``items``); the
    heavy bucket scan stays the lazy per-day ``scan_day`` on
    day-open."""
    if not items:
        return []
    assignments = assign_days(
        (
            (it.path, it.timestamp,
             calibrations.get(it.camera_id) if calibrations else None)
            for it in items
        ),
        trip_days,
    )

    # Group by (rank, day_number): dated days by number; the single
    # Undated day (day_number None) sorts last via rank 1.
    groups: dict[tuple[int, Optional[int]], dict] = {}
    for it in items:
        a = assignments.get(it.path)
        if a is None:                       # defensive — assign_days is total
            day_number, label = None, UNDATED_LABEL
        else:
            day_number, label = a.day_number, a.label
        key = (0 if day_number is not None else 1, day_number)
        g = groups.get(key)
        if g is None:
            g = {"label": label, "paths": []}
            groups[key] = g
        g["paths"].append(it.path)

    out: list[DayFolder] = []
    for sort_key in sorted(groups, key=lambda k: (k[0], k[1] or 0)):
        g = groups[sort_key]
        paths = sorted(g["paths"])
        if not paths:
            continue
        out.append(DayFolder(
            key=g["label"],
            label=g["label"],
            files=tuple(paths),
            style_mix=(),               # flat source has no style mix
        ))
    return out


def scan_fresh_source_days(
    source_root: Path,
    trip_days: Sequence[TripDay],
    calibrations: Mapping[str, Optional[CameraCalibration]],
) -> list[DayFolder]:
    """Compose :func:`read_source_items` + :func:`group_items_to_days`
    — the simple path (Phone / no clock dialog needed). The
    camera-clock dialog path instead calls the two separately so it
    can read once, ask, then regroup with the chosen calibrations."""
    return group_items_to_days(
        read_source_items(source_root), trip_days, calibrations)
