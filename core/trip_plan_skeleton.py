"""Build a trip-plan skeleton by reading the user's per-day folder layout.

The Reconcile workflow expects the user to have already organized photos
into ``Dia N - LOC`` subfolders (Nelson always does this manually for
past trips). This module walks that layout, extracts each day's
calendar date from the photos inside (preferring the reference camera —
phone clocks are trusted), and emits a plan-text skeleton in the format
``parse_trip_plan`` accepts:

    Dia 1 - (26/10) [TZ:+5.75]                # da pasta: "Katmandu"
    Dia 2 - (29/10)                           # da pasta: "Lukla e Chegada ao EVH"
    Dia 6 - (02/11)                           # da pasta: "EVH - As montanhas como vistas do Hotel + EVH - Trilha para Khunde Peak"
    Dia 7 - (03/11)                           # da pasta: "EBC Flight"
    Dia 8 - (03/11)                           # da pasta: "Lukla a Kathmandu"

Behavior:

* The user's narrative day numbering is preserved (no renumbering to
  match calendar). Two folders sharing the same Dia number are merged
  into one entry — locations concatenated with " + ".
* Two folders with different Dia numbers but the same calendar date
  (a redeye flight day, e.g. Nepal's Dia 7 EBC + Dia 8 Lukla→Kathmandu
  both on 03/11) emit two distinct lines — the user's day numbering
  decides the slideshow chapters.
* The folder's location string is shown as a trailing comment, NOT
  pre-filled into the description. The user fills description + LOC
  tag manually so they can refine wording without fighting auto-fill.
* Days where no reference-camera photo exists fall back to the first
  photo's date (worse but better than nothing); a warning is emitted
  so the user knows that day's date is from an uncalibrated camera.

This module is Qt-free; the calling pipeline / CLI handles UI.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from core.exif_reader import read_exif_batch

log = logging.getLogger(__name__)


# Subfolder name pattern: "Dia N - whatever" or "Day N - whatever".
# Multi-space and ``-``/``–``/``—`` are tolerated since users sometimes
# typo the separator. The location capture is lazy-greedy so trailing
# whitespace gets trimmed by ``.strip()``.
_DAY_FOLDER_RE = re.compile(
    r"^\s*(?:dia|day)\s+(\d+)\s*[-–—]\s*(.+?)\s*$",
    re.IGNORECASE,
)

_PHOTO_EXTS = {".jpg", ".jpeg", ".heic", ".heif", ".rw2", ".dng", ".tif", ".tiff"}


@dataclass
class DayFolder:
    """One ``Dia N - LOC`` folder discovered under the per-day root."""
    day_number: int
    location: str
    folder: Path


@dataclass
class SkeletonResult:
    """Output of the skeleton generators. The caller surfaces
    ``warnings`` to the user — typical entries are "no reference
    photo on Day N" or "couldn't read EXIF for sampled photo X".

    ``folder_hints`` maps day_number → the original folder
    location string (with ``" + "`` joining when multiple folders
    share the same day_number). Empty in the per-camera fallback
    flow (no folder names to draw from).

    ``day_photo_samples`` maps day_number → the list of reference-camera
    photo paths that fell on that day. The "Describe Day" dialog uses
    these to render a 3×3 thumbnail grid that helps the user recall
    what the day was about while filling the description.
    """
    plan_text: str
    day_dates: dict[int, date] = field(default_factory=dict)
    folder_hints: dict[int, str] = field(default_factory=dict)
    day_photo_samples: dict[int, list[Path]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def discover_day_folders(per_day_root: Path) -> list[DayFolder]:
    """Walk top-level subfolders of ``per_day_root`` and return the
    ones that look like ``Dia N - LOC``. Other folders (``extras``,
    ``Snapshots``, etc.) are silently ignored. Sort order is
    deterministic — folder name ascending — so tests / tooling have
    predictable output."""
    out: list[DayFolder] = []
    if not per_day_root.is_dir():
        return out
    for sub in sorted(per_day_root.iterdir(), key=lambda p: p.name):
        if not sub.is_dir():
            continue
        m = _DAY_FOLDER_RE.match(sub.name.strip())
        if not m:
            continue
        out.append(DayFolder(
            day_number=int(m.group(1)),
            location=m.group(2).strip(),
            folder=sub,
        ))
    return out


def _resolve_photo_timestamp(
    path: Path, exif_entry,
) -> tuple[Optional[datetime], str]:
    """Best-effort timestamp resolution.

    Tries EXIF first (the reader has already walked the standard
    chain DateTimeOriginal → CreateDate → MediaCreateDate →
    TrackCreateDate). When that yields nothing — happens with
    AirDrop'd photos, edited-and-resaved exports, screenshots, and
    other paths where iOS strips EXIF — falls back to the file's
    on-disk modification time. mtime is only good to day-precision
    on most copies (it can reset to the copy time depending on
    OS-to-OS transfer), but day-precision is what the skeleton
    cares about. Returns ``(None, "none")`` only when even ``stat``
    fails.

    The ``source`` tag lets callers count mtime fallbacks separately
    so the user knows how many photos went through the imprecise
    path — useful when the warning says "291 photos used file mtime"
    and they want to investigate.
    """
    if exif_entry is not None and exif_entry.timestamp is not None:
        return exif_entry.timestamp, "exif"
    try:
        return datetime.fromtimestamp(path.stat().st_mtime), "mtime"
    except OSError:
        return None, "none"


def _list_reference_photos(
    folder: Path,
    *,
    reference_model_contains: Optional[str],
) -> list[Path]:
    """Return all photos under ``folder`` (recursive) whose EXIF
    Model matches ``reference_model_contains``. Used to feed the
    Describe Day thumbnail grid — restricted to the reference
    camera so the user sees consistent context (typically phone
    shots) rather than mixed cameras.

    With ``reference_model_contains=None`` returns everything.
    Files that fail EXIF read are silently dropped (better an
    empty grid for that day than a crash)."""
    photos = sorted(
        f for f in folder.rglob("*")
        if f.is_file() and f.suffix.lower() in _PHOTO_EXTS
    )
    if not photos or reference_model_contains is None:
        return photos
    entries = read_exif_batch(photos)
    by_path = {e.path: e for e in entries if e is not None}
    ref_lower = reference_model_contains.lower()
    return [
        p for p in photos
        if (e := by_path.get(p)) and e.model
        and ref_lower in e.model.lower()
    ]


def _pick_sample_photo(
    folder: Path,
    *,
    reference_model_contains: Optional[str],
) -> tuple[Optional[Path], Optional[str]]:
    """Pick one photo from ``folder`` for date extraction. Recursive.

    When ``reference_model_contains`` is set, prefer files whose EXIF
    ``Model`` contains that substring (case-insensitive). The check
    is on Model rather than Make because ``culler.exif_reader.PhotoExif``
    only surfaces Model — and "iPhone" / "G9" / etc. uniquely identify
    the camera class anyway. Falls back to the first photo regardless
    if no reference photo is present, returning a warning so the
    caller can flag the day.

    Returns ``(path, warning)``. ``path`` is None when the folder
    has no photo files at all.
    """
    photos = sorted(
        f for f in folder.rglob("*")
        if f.is_file() and f.suffix.lower() in _PHOTO_EXTS
    )
    if not photos:
        return None, None

    if reference_model_contains is None:
        return photos[0], None

    # Probe in batches — cheap because we ask for one tag.
    entries = read_exif_batch(photos)
    by_path = {e.path: e for e in entries if e is not None}
    ref_lower = reference_model_contains.lower()
    for photo in photos:
        e = by_path.get(photo)
        if e and e.model and ref_lower in e.model.lower():
            return photo, None
    # Fall through: no reference-camera photo in this folder.
    return photos[0], (
        f"no reference-camera photo in {folder.name!r}; "
        f"using {photos[0].name} (uncalibrated camera) for the date"
    )


def generate_plan_skeleton_from_per_day(
    per_day_root: Path,
    *,
    reference_model_contains: Optional[str] = "iPhone",
    home_tz_offset: Optional[float] = None,
    reference_offset_hours: float = 0.0,
) -> SkeletonResult:
    """Build a plan-text skeleton from the per-day folder layout.

    ``reference_model_contains`` (default ``"iPhone"`` — Nelson uses
    iPhones as the reference clock) filters which photo we sample
    per day for date extraction. Match is substring-against-Model
    (case-insensitive). Set to ``None`` to disable filtering and use
    any photo found.

    ``home_tz_offset`` is emitted on Day 1 as ``[TZ:+X.XX]``. The
    wizard parser inherits TZ for subsequent days so a single Day-1
    declaration covers the trip unless it crosses TZ boundaries
    (which the user can mark manually after the fact).

    ``reference_offset_hours`` is added to every sampled photo's
    timestamp before its calendar date is extracted — this puts plan
    dates in **trip-local** terms when the reference camera was
    configured to a different TZ than the trip's. Typically computed
    by the caller as ``trip_tz − reference.configured_tz``; defaults
    to 0 (the legacy behavior, correct when reference is a phone or
    already on trip TZ). See ``project_reconcile_reference_tz.md``
    in memory for the design context.
    """
    folders = discover_day_folders(per_day_root)
    warnings: list[str] = []

    if not folders:
        return SkeletonResult(plan_text="", day_dates={}, warnings=[
            f"no 'Dia N - LOC' subfolders found under {per_day_root}",
        ])

    # Group folders by Dia number so duplicates merge.
    by_day_num: dict[int, list[DayFolder]] = {}
    for f in folders:
        by_day_num.setdefault(f.day_number, []).append(f)

    # For each day, sample a photo + read its capture date.
    # We collect per-day-folder samples to also detect "Dia X has
    # multiple folders with different dates" (rare but possible).
    # ``day_photo_samples`` separately collects ALL reference-camera
    # photos under each day's folders — used by the Describe Day
    # dialog's thumbnail grid (independent of the date-sample logic).
    day_to_date: dict[int, date] = {}
    day_photo_samples: dict[int, list[Path]] = {}
    sample_paths: list[Path] = []
    sample_to_day: dict[Path, int] = {}
    for day_num, day_folders in by_day_num.items():
        for df in day_folders:
            sample, warning = _pick_sample_photo(
                df.folder,
                reference_model_contains=reference_model_contains,
            )
            if warning:
                warnings.append(warning)
            if sample is None:
                warnings.append(
                    f"no photos found in {df.folder.name!r}; "
                    f"day {day_num} has no date"
                )
                continue
            sample_paths.append(sample)
            sample_to_day[sample] = day_num
            day_photo_samples.setdefault(day_num, []).extend(
                _list_reference_photos(
                    df.folder,
                    reference_model_contains=reference_model_contains,
                )
            )

    # Single batch EXIF read for all samples we picked.
    mtime_fallback = 0
    if sample_paths:
        entries = read_exif_batch(sample_paths)
        by_path = {e.path: e for e in entries if e is not None}
        for path, day_num in sample_to_day.items():
            e = by_path.get(path)
            ts, source = _resolve_photo_timestamp(path, e)
            if ts is None:
                warnings.append(
                    f"sample photo {path.name} has no EXIF and no "
                    f"file timestamp; day {day_num} skipped"
                )
                continue
            if source == "mtime":
                mtime_fallback += 1
            # Shift the sampled timestamp by the reference camera's
            # offset to the trip TZ before extracting the calendar
            # date — yields plan dates in trip-local terms even when
            # the reference was configured to a non-trip TZ (e.g. G9
            # MKII set to Dubai +4 on a Nepal +5:45 trip).
            shifted = (
                ts + timedelta(hours=reference_offset_hours)
                if reference_offset_hours
                else ts
            )
            d = shifted.date()
            existing = day_to_date.get(day_num)
            if existing is None or d < existing:
                # Take the EARLIEST date when multiple folders share
                # a day number — duplicates are typically same-day
                # split into morning/afternoon.
                day_to_date[day_num] = d
    if mtime_fallback:
        warnings.append(
            f"{mtime_fallback} day(s) used file modification time "
            f"because the sampled photo had no EXIF timestamp"
        )

    # Assemble lines. Pre-populate the description with the folder's
    # location string so a folder named "Katmandu" comes through as
    # ``Dia 1 - Katmandu (26/10) ...`` instead of forcing the user
    # to retype it. When two folders share a Dia number ("As
    # montanhas..." + "Trilha Khunde"), the descriptions are joined
    # with " + " so neither activity is lost. ``folder_hints`` is
    # kept on the result for backwards compatibility with the UI's
    # existing context-column wiring; it now mirrors the
    # description rather than complementing it, so the editor can
    # decide whether to show the column or drop it.
    lines: list[str] = []
    folder_hints: dict[int, str] = {}
    last_tz_emitted = False
    for day_num in sorted(by_day_num):
        the_date = day_to_date.get(day_num)
        if the_date is None:
            date_str = "??/??"
        else:
            date_str = the_date.strftime("%d/%m")

        # Merge location strings across same-day-number folders.
        locs = [df.location for df in by_day_num[day_num]]
        merged_loc = " + ".join(locs) if locs else ""
        if merged_loc:
            folder_hints[day_num] = merged_loc

        bracket_tags = ""
        if not last_tz_emitted and home_tz_offset is not None:
            tz_str = (
                f"{int(home_tz_offset):+d}"
                if home_tz_offset == int(home_tz_offset)
                else f"{home_tz_offset:+g}"
            )
            bracket_tags = f" [TZ:{tz_str}]"
            last_tz_emitted = True

        if merged_loc:
            lines.append(
                f"Dia {day_num} - {merged_loc} ({date_str}){bracket_tags}"
            )
        else:
            lines.append(f"Dia {day_num} - ({date_str}){bracket_tags}")

    return SkeletonResult(
        plan_text="\n".join(lines) + "\n",
        day_dates=day_to_date,
        folder_hints=folder_hints,
        day_photo_samples=day_photo_samples,
        warnings=warnings,
    )


def generate_plan_skeleton_from_per_camera(
    reference_camera_folder: Path,
    *,
    home_tz_offset: Optional[float] = None,
    reference_offset_hours: float = 0.0,
) -> SkeletonResult:
    """Fallback skeleton when the user hasn't pre-organized
    ``Dia N - LOC`` per-day folders yet.

    Walks the reference camera's subfolder under per_camera_source
    (e.g. ``per_camera/iPhone Aida/``), reads EXIF DateTimeOriginal
    for every photo, clusters by calendar date, and emits one
    ``Dia N - (DD/MM)`` line per unique date with NO description —
    the user fills descriptions manually using the Describe Day
    dialog (which gets ``day_photo_samples`` populated here).

    Day numbering is sequential starting at 1 — the user can renumber
    or split rows in the editor if their narrative differs from
    pure calendar (e.g. a redeye flight day they want to call Dia 7
    + Dia 8). No ``folder_hints`` populated (no folder names exist
    in this flow).

    ``reference_offset_hours`` is added to every photo timestamp
    before its calendar date is extracted — keeps plan dates in
    trip-local terms when the reference camera was configured to a
    different TZ than the trip. Same semantics as the per-day
    skeleton variant.
    """
    if not reference_camera_folder.is_dir():
        return SkeletonResult(plan_text="", warnings=[
            f"reference camera folder not found: {reference_camera_folder}",
        ])

    photos = sorted(
        f for f in reference_camera_folder.rglob("*")
        if f.is_file() and f.suffix.lower() in _PHOTO_EXTS
    )
    if not photos:
        return SkeletonResult(plan_text="", warnings=[
            f"no photos found under {reference_camera_folder}",
        ])

    entries = read_exif_batch(photos)
    by_path = {e.path: e for e in entries if e is not None}
    by_date: dict[date, list[Path]] = {}
    exif_count = 0
    mtime_count = 0
    skipped = 0
    for path in photos:
        ts, source = _resolve_photo_timestamp(path, by_path.get(path))
        if ts is None:
            skipped += 1
            continue
        # Shift to trip-local before clustering — see same logic in
        # ``generate_plan_skeleton_from_per_day``.
        shifted = (
            ts + timedelta(hours=reference_offset_hours)
            if reference_offset_hours
            else ts
        )
        by_date.setdefault(shifted.date(), []).append(path)
        if source == "exif":
            exif_count += 1
        elif source == "mtime":
            mtime_count += 1

    warnings: list[str] = []
    if mtime_count:
        warnings.append(
            f"{mtime_count} photo(s) had no EXIF timestamp; used file "
            f"modification time as fallback (good for day-precision "
            f"clustering but not for the EXIF rewrite at commit). "
            f"{exif_count} photo(s) used proper EXIF dates."
        )
    if skipped:
        warnings.append(
            f"{skipped} photo(s) under {reference_camera_folder.name} "
            f"had neither EXIF nor a readable file timestamp and were "
            f"skipped"
        )
    if not by_date:
        return SkeletonResult(plan_text="", warnings=warnings + [
            f"no readable timestamps in {reference_camera_folder}; "
            f"can't derive day skeleton",
        ])

    sorted_dates = sorted(by_date)
    day_dates: dict[int, date] = {}
    day_photo_samples: dict[int, list[Path]] = {}
    lines: list[str] = []
    last_tz_emitted = False
    for i, the_date in enumerate(sorted_dates, start=1):
        day_dates[i] = the_date
        day_photo_samples[i] = sorted(by_date[the_date])
        date_str = the_date.strftime("%d/%m")
        bracket_tags = ""
        if not last_tz_emitted and home_tz_offset is not None:
            tz_str = (
                f"{int(home_tz_offset):+d}"
                if home_tz_offset == int(home_tz_offset)
                else f"{home_tz_offset:+g}"
            )
            bracket_tags = f" [TZ:{tz_str}]"
            last_tz_emitted = True
        lines.append(f"Dia {i} - ({date_str}){bracket_tags}")

    return SkeletonResult(
        plan_text="\n".join(lines) + "\n",
        day_dates=day_dates,
        day_photo_samples=day_photo_samples,
        warnings=warnings,
    )


def generate_plan_skeleton_from_items(
    items: list[tuple[Path, Optional[datetime]]],
    *,
    home_tz_offset: Optional[float] = None,
    reference_offset_hours: float = 0.0,
    source_label: str = "reference camera",
) -> SkeletonResult:
    """Same shape as :func:`generate_plan_skeleton_from_per_camera`,
    but consumes a pre-walked list of ``(path, timestamp)`` pairs
    instead of walking a folder + invoking exiftool. Used by the
    EXIF-scan-first flow (Nelson 2026-05-21,
    ``core.source_index.SourceIndex``): the scan has already
    happened, the timestamps are already in memory, and we just need
    to cluster them by calendar date.

    ``source_label`` is used in warning text only — pass the
    camera_id of the reference camera so the user sees e.g.
    "no readable timestamps in DC-G9" rather than a generic message.
    """
    if not items:
        return SkeletonResult(plan_text="", warnings=[
            f"no items in {source_label}; can't derive day skeleton",
        ])

    by_date: dict[date, list[Path]] = {}
    exif_count = 0
    skipped = 0
    for path, ts in items:
        if ts is None:
            skipped += 1
            continue
        shifted = (
            ts + timedelta(hours=reference_offset_hours)
            if reference_offset_hours
            else ts
        )
        by_date.setdefault(shifted.date(), []).append(path)
        exif_count += 1

    warnings: list[str] = []
    if skipped:
        warnings.append(
            f"{skipped} photo(s) in {source_label} had no readable "
            f"EXIF timestamp and were skipped (still importable — "
            f"they'll be quarantined under _no_timestamp at "
            f"reconcile time)"
        )
    if not by_date:
        return SkeletonResult(plan_text="", warnings=warnings + [
            f"no readable timestamps in {source_label}; "
            f"can't derive day skeleton",
        ])

    sorted_dates = sorted(by_date)
    day_dates: dict[int, date] = {}
    day_photo_samples: dict[int, list[Path]] = {}
    lines: list[str] = []
    last_tz_emitted = False
    for i, the_date in enumerate(sorted_dates, start=1):
        day_dates[i] = the_date
        day_photo_samples[i] = sorted(by_date[the_date])
        date_str = the_date.strftime("%d/%m")
        bracket_tags = ""
        if not last_tz_emitted and home_tz_offset is not None:
            tz_str = (
                f"{int(home_tz_offset):+d}"
                if home_tz_offset == int(home_tz_offset)
                else f"{home_tz_offset:+g}"
            )
            bracket_tags = f" [TZ:{tz_str}]"
            last_tz_emitted = True
        lines.append(f"Dia {i} - ({date_str}){bracket_tags}")

    return SkeletonResult(
        plan_text="\n".join(lines) + "\n",
        day_dates=day_dates,
        day_photo_samples=day_photo_samples,
        warnings=warnings,
    )


def days_to_plan_text(
    days: list,
    home_tz_offset: Optional[float] = None,
) -> str:
    """Serialise a ``list[TripDay]`` back into the canonical plan
    text format the parser accepts. Inverse of ``parse_trip_plan``.

    Used by surfaces that let the user edit a TripDay table and need
    to hand the result to ``reconcile_commit`` (which re-parses
    internally for description validation).

    Emits ``Dia N - DESC (DD/MM/YYYY) [LOC:..] [TZ:..]``. ``[TZ:..]``
    is emitted on the first day (matching the skeleton's
    "first-row-anchor" convention) and on any subsequent row whose
    ``tz_offset`` differs from the previously-emitted one.
    """
    if not days:
        return ""
    lines: list[str] = []
    last_tz: Optional[float] = None
    for d in days:
        date_str = d.date.strftime("%d/%m/%Y") if d.date else "??/??"
        desc = (d.description or "").strip()
        loc = (d.location or "").strip()
        bracket_tags = ""
        # TZ: first day always emits (the anchor); later days only
        # when the value actually changes.
        tz_value: Optional[float] = (
            d.tz_offset if d.tz_offset is not None else home_tz_offset
        )
        if tz_value is not None and (last_tz is None or tz_value != last_tz):
            tz_str = (
                f"{int(tz_value):+d}"
                if tz_value == int(tz_value)
                else f"{tz_value:+g}"
            )
            bracket_tags += f" [TZ:{tz_str}]"
            last_tz = tz_value
        if loc:
            bracket_tags += f" [LOC:{loc}]"
        if desc:
            lines.append(
                f"Dia {d.day_number} - {desc} ({date_str}){bracket_tags}"
            )
        else:
            lines.append(
                f"Dia {d.day_number} - ({date_str}){bracket_tags}"
            )
    return "\n".join(lines) + "\n"
