"""Discovery for the Process Culler — find photos ready for exposure/crop.

Walks an event's on-disk layout (``<event_root>/<Dia N>/<scenario>/...``)
and produces a chronologically-sorted list of items eligible for the
Process Culler. Eligibility:

* The day folder matches a ``TripDay`` of the event (via ``day_folder_name``)
* The scenario folder matches one of ``ELIGIBLE_SCENARIOS`` (no brackets,
  no video — those workflows run separately)
* The file extension is a still photo

Bracketed sequences and video go through other tools (focus stacking,
video trim) and are intentionally skipped here. The reserved
``processed/`` output folder at the event root is also skipped so the
Process Culler doesn't try to re-process its own output.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core.models import Event, TripDay
from core.path_builder import (
    PROCESSED_DIR_NAME as PROCESSED_FOLDER_NAME,
    SELECTED_DIR_NAME,
    day_folder_name,
    find_day_folders_root,
    process_source_dir,
)
from core.vocabulary import Scenario

log = logging.getLogger(__name__)


# Re-exported for backward-compat with callers that imported this
# module's name. Defined canonically in ``core.path_builder``.
__all__ = ["PROCESSED_FOLDER_NAME", "PHOTO_EXTENSIONS", "ELIGIBLE_SCENARIOS"]


# Stills only — videos and audio go to dedicated workflows. Same set the
# bucket scanner / folder scanner use for "photo" input, minus video
# extensions.
PHOTO_EXTENSIONS: frozenset[str] = frozenset(
    {
        # Camera RAW
        ".rw2", ".orf", ".arw", ".srf", ".sr2",
        ".cr2", ".cr3", ".crw", ".nef", ".nrw",
        ".raf", ".pef", ".rwl", ".ori", ".dng",
        # Compressed stills
        ".jpg", ".jpeg", ".heic", ".heif",
        ".tif", ".tiff",
    }
)


def source_dir_stats(source_root: Path) -> tuple[int, int]:
    """Walk ``source_root`` recursively and return ``(photo_count,
    total_bytes_all_media)``.

    Photo count uses ``PHOTO_EXTENSIONS`` only (no videos) so the
    Source picker can cleanly compare ``02 Selected`` vs
    ``02b LRC Treated`` for completeness — both should have one photo
    entry per RAW, and a deficit means LRC's publish hasn't finished.
    Total bytes covers photos + videos so the disk-usage estimate
    matches what the user sees in Explorer.

    Returns ``(0, 0)`` when ``source_root`` doesn't exist. Walks lazily
    via ``rglob`` — fine for the typical 2000-file trip; if discovery
    grows beyond that, switch to a depth-limited walk.
    """
    # Local import to avoid circular: video_discovery imports from
    # process_discovery already (PROCESSED_DIR_NAME, PHOTO_EXTENSIONS).
    from core.video_discovery import VIDEO_EXTENSIONS

    if not source_root.exists() or not source_root.is_dir():
        return 0, 0

    media_extensions = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS
    photo_count = 0
    total_bytes = 0
    for path in source_root.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in media_extensions:
            continue
        try:
            total_bytes += path.stat().st_size
        except OSError:
            pass
        if suffix in PHOTO_EXTENSIONS:
            photo_count += 1
    return photo_count, total_bytes


# Scenarios that flow through the Process Culler. Brackets are
# intermediate (merged into stacks first), video has its own pipeline,
# selfie is included so phone shots get the same treatment as camera ones.
ELIGIBLE_SCENARIOS: tuple[Scenario, ...] = (
    Scenario.LANDSCAPE,
    Scenario.PORTRAIT,
    Scenario.SELFIE,
    Scenario.MACRO,
    Scenario.WILDLIFE,
    Scenario.GENERAL,
    Scenario.NIGHT_LONG_EXPOSURE,
)


def _normalize_folder_name(name: str) -> str:
    """Canonical form for matching scenario folder names.

    Lowercase, strip outer whitespace, and collapse runs of spaces /
    underscores / hyphens into a single underscore. So ``"Landscape"``,
    ``"landscape"``, ``"Night Long Exposure"``, ``"night-long-exposure"``,
    and ``"night_long_exposure"`` all map onto the same key.
    """
    out = name.strip().lower()
    # Collapse common separators to underscore so multi-word
    # scenario names round-trip: "Night Long Exposure" → "night_long_exposure".
    for ch in (" ", "-"):
        out = out.replace(ch, "_")
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_")


# Aliases (pt-BR + en + common synonyms) so legacy folders curated by
# hand in FastStone / Lightroom / Finder don't get silently dropped.
# The user owns their folder names; this map adapts to them. Folders
# that don't match any alias still get processed — they fall through
# to ``Scenario.GENERAL`` so nothing is invisible.
SCENARIO_ALIASES: dict[str, Scenario] = {
    # Landscape — any wide outdoor view
    "landscape": Scenario.LANDSCAPE,
    "landscapes": Scenario.LANDSCAPE,
    "paisagem": Scenario.LANDSCAPE,
    "paisagens": Scenario.LANDSCAPE,
    "scenery": Scenario.LANDSCAPE,
    "vista": Scenario.LANDSCAPE,
    "vistas": Scenario.LANDSCAPE,
    "panorama": Scenario.LANDSCAPE,
    "panoramas": Scenario.LANDSCAPE,

    # Portrait — people-centered shots
    "portrait": Scenario.PORTRAIT,
    "portraits": Scenario.PORTRAIT,
    "people": Scenario.PORTRAIT,
    "person": Scenario.PORTRAIT,
    "pessoas": Scenario.PORTRAIT,
    "pessoa": Scenario.PORTRAIT,
    "pessoal": Scenario.PORTRAIT,
    "retrato": Scenario.PORTRAIT,
    "retratos": Scenario.PORTRAIT,
    "family": Scenario.PORTRAIT,
    "familia": Scenario.PORTRAIT,
    "família": Scenario.PORTRAIT,
    "friends": Scenario.PORTRAIT,
    "amigos": Scenario.PORTRAIT,

    # Selfie — explicit self-portraits
    "selfie": Scenario.SELFIE,
    "selfies": Scenario.SELFIE,
    "autorretrato": Scenario.SELFIE,
    "autorretratos": Scenario.SELFIE,

    # Macro — close-up details
    "macro": Scenario.MACRO,
    "macros": Scenario.MACRO,
    "closeup": Scenario.MACRO,
    "close_up": Scenario.MACRO,

    # Wildlife — animals + birds, wild and pets. The legacy v1.x
    # classifier subdivided this into action / static / perched; we
    # collapse them all back to plain Wildlife on the principle that
    # the action-vs-static distinction belongs at the photo level
    # (shutter speed, EXIF), not the folder level.
    "wildlife": Scenario.WILDLIFE,
    "wildlife_action": Scenario.WILDLIFE,
    "wildlife_static": Scenario.WILDLIFE,
    "wildlife_perched": Scenario.WILDLIFE,
    "animal": Scenario.WILDLIFE,
    "animals": Scenario.WILDLIFE,
    "animais": Scenario.WILDLIFE,
    "fauna": Scenario.WILDLIFE,
    "birds": Scenario.WILDLIFE,
    "bird": Scenario.WILDLIFE,
    "aves": Scenario.WILDLIFE,
    "ave": Scenario.WILDLIFE,
    "passaros": Scenario.WILDLIFE,
    "pássaros": Scenario.WILDLIFE,
    "pets": Scenario.WILDLIFE,
    "pet": Scenario.WILDLIFE,
    "mascotes": Scenario.WILDLIFE,

    # Night / long exposure
    "night": Scenario.NIGHT_LONG_EXPOSURE,
    "night_long_exposure": Scenario.NIGHT_LONG_EXPOSURE,
    "longexposure": Scenario.NIGHT_LONG_EXPOSURE,
    "long_exposure": Scenario.NIGHT_LONG_EXPOSURE,
    "noite": Scenario.NIGHT_LONG_EXPOSURE,
    "noturna": Scenario.NIGHT_LONG_EXPOSURE,
    "noturnas": Scenario.NIGHT_LONG_EXPOSURE,
    "exposicao_longa": Scenario.NIGHT_LONG_EXPOSURE,
    "exposição_longa": Scenario.NIGHT_LONG_EXPOSURE,
    "longa_exposicao": Scenario.NIGHT_LONG_EXPOSURE,
    "longa_exposição": Scenario.NIGHT_LONG_EXPOSURE,

    # General catch-all aliases
    "general": Scenario.GENERAL,
    "general_scene": Scenario.GENERAL,
    "geral": Scenario.GENERAL,
    "misc": Scenario.GENERAL,
    "miscellaneous": Scenario.GENERAL,
    "outros": Scenario.GENERAL,
    "other": Scenario.GENERAL,
    "diversos": Scenario.GENERAL,
}


# Folder names that should never produce ProcessableItems even when
# they happen to contain photos: the bracketed sequences are owned by
# the focus-stack workflow, video has its own pipeline. Stored
# normalized (lowercase, single underscores) so the lookup uses the
# same key as the alias table.
_SKIPPABLE_FOLDERS: frozenset[str] = frozenset({
    "focus_bracket",
    "exposure_bracket",
    "video",
    "videos",
    "stack",
    "stacks",
    "stacked",
    "cinemagraph",
    "cinemagraphs",
})


def _resolve_scenario(folder_name: str) -> Scenario:
    """Pick the closest enum match for an arbitrary folder name.

    Tries the alias table first; falls back to ``Scenario.GENERAL``
    so an unknown folder never prevents its photos from being
    processed. Logs a debug line on fallback so we can spot
    naming patterns worth adding to the alias table later.
    """
    key = _normalize_folder_name(folder_name)
    if key in SCENARIO_ALIASES:
        return SCENARIO_ALIASES[key]
    log.debug(
        "discovery: folder %r → GENERAL (no alias match)", folder_name,
    )
    return Scenario.GENERAL


@dataclass(frozen=True)
class ProcessableItem:
    """One photo found by ``discover_processable``.

    ``timestamp`` is the EXIF DateTimeOriginal when available; falls back
    to the file's mtime so items with broken/missing EXIF still get a
    deterministic order. Discovery sorts by this field, so an mtime
    fallback that's slightly off won't break the chronology of a normal
    trip — it only matters for files whose EXIF reader failed entirely.

    ``source_folder`` is the on-disk folder name verbatim (e.g.
    "Pessoas", "Birds", "Landscape") so the UI can group / display
    the user's original taxonomy even when ``scenario`` rolled it
    into a canonical enum or fell back to GENERAL. Two folders that
    map to the same scenario but have different names stay distinct
    on the overview — the user picks whether to consolidate them
    by renaming on disk.
    """

    path: Path
    day: TripDay
    scenario: Scenario
    timestamp: datetime
    source_folder: str = ""


def discover_processable(event: Event) -> list[ProcessableItem]:
    """Walk ``event.photos_base_path`` and return items ready to process.

    Returns:
        A list of ``ProcessableItem`` sorted by timestamp ascending — so
        the Process Culler advances through the trip in the order shots
        were taken (mixed across scenarios within a day, which is how
        the user wanted the slideshow to read).

        Empty list if the event has no base path, the path doesn't exist,
        the trip has no days, or no eligible photos were found.

    Notes:
        Reads file mtimes only — does NOT batch-read EXIF here. Reading
        EXIF for thousands of photos is slow and blocks the UI; the
        Process Culler reads each photo's tone curve / metadata lazily
        as it navigates, so cold discovery stays fast.
    """
    if not event.photos_base_path:
        return []
    event_root = Path(event.photos_base_path)
    if not event_root.exists() or not event_root.is_dir():
        return []
    if not event.trip_days:
        return []

    # Pre-build day-folder → TripDay lookup. Folder names are produced by
    # ``day_folder_name`` so this match is exact (sanitization is the
    # same on both sides).
    day_by_folder_name: dict[str, TripDay] = {
        day_folder_name(d): d for d in event.trip_days
    }

    # Folder-name → Scenario resolution goes through the alias table
    # so hand-curated folder names from FastStone / Lightroom (in any
    # of pt-BR / en / mixed-case) all map onto our enums. Unknown
    # names fall through to GENERAL — never silently dropped.
    #
    # Source dir is configurable per-event via
    # ``event.event_settings["process_source_dir"]`` so the user can
    # point at "02b LRC Treated" (post-LRC TIFFs) instead of the
    # default "02 Selected" (post-cull originals). Discovery just
    # walks ``<event_root>/<source_dir_name>/<day>/<scenario>/`` —
    # the name is data, not code.
    source_dir_name = (
        event.event_settings.get("process_source_dir")
        or SELECTED_DIR_NAME
    )
    source_root = process_source_dir(event_root, source_dir_name)
    if not source_root.exists():
        return []
    # Descend through any single intermediate directory until we
    # find the day folders. Lightroom Classic's jf Folder Publisher
    # inserts the catalog source dir name (e.g. "02 Selected")
    # between the publish-tree root and the day folders, so when
    # the user points at "02b LRC Treated/" the actual days live at
    # "02b LRC Treated/02 Selected/Dia N - .../". This silently
    # adapts.
    source_root = find_day_folders_root(
        source_root, set(day_by_folder_name.keys()),
    )
    items: list[ProcessableItem] = []
    for day_dir in source_root.iterdir():
        if not day_dir.is_dir():
            continue
        day = day_by_folder_name.get(day_dir.name)
        if day is None:
            log.debug("discovery: skipping unrecognized day folder %s", day_dir.name)
            continue

        for scen_dir in day_dir.iterdir():
            if not scen_dir.is_dir():
                continue
            # Skip bracket / video sub-folders that the v2.0 culler
            # writes — they have separate workflows.
            if _normalize_folder_name(scen_dir.name) in _SKIPPABLE_FOLDERS:
                continue
            scenario = _resolve_scenario(scen_dir.name)

            source_folder = scen_dir.name
            for photo_path in scen_dir.iterdir():
                if not photo_path.is_file():
                    continue
                if photo_path.suffix.lower() not in PHOTO_EXTENSIONS:
                    continue
                ts = _photo_timestamp(photo_path)
                items.append(
                    ProcessableItem(
                        path=photo_path,
                        day=day,
                        scenario=scenario,
                        timestamp=ts,
                        source_folder=source_folder,
                    )
                )

    items.sort(key=lambda it: it.timestamp)
    log.info(
        "process discovery: found %d eligible photos across %d day folders",
        len(items),
        len({it.day.day_number for it in items}),
    )
    return items


def _photo_timestamp(path: Path) -> datetime:
    """Best-effort timestamp for sorting. Uses mtime — EXIF would be
    more accurate but reading it here would multiply discovery latency
    by ~100×. The Process Culler can re-read EXIF when it needs the
    canonical capture time (e.g., to name the output file)."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        # Practically impossible since we already confirmed is_file(),
        # but if a file vanished between the iterdir() and the stat(),
        # epoch-zero parks it at the start of the list rather than
        # crashing the whole discovery.
        return datetime.fromtimestamp(0)


def already_processed_paths(
    event: Event, items: list[ProcessableItem] | None = None,
) -> set[Path]:
    """Return the set of source paths whose Process output already exists.

    Walks the items list and asks "does the JPEG that ``ProcessSession``
    *would* write for this item exist on disk?". Each item maps to a
    deterministic output path
    ``<event>/03 Processed/<Dia N>/<HHMMSS>_<orig_stem>.jpg`` so we can
    answer this without re-walking the processed folder.

    Costa Rica re-test 2026-05-01: the prior implementation built a
    ``{orig_stem: source_path}`` dict and reverse-mapped JPEGs back to
    sources. When two items shared a stem (different days, or phone +
    camera with the same ``IMG_5765`` filename) the dict collapsed to
    one entry and the others fell out of the count. Walking from
    items ensures every distinct item has its own check.

    Pass ``items`` to avoid an extra ``discover_processable`` walk
    when the caller already has the list cached.
    """
    if not event.photos_base_path:
        return set()
    processed_root = Path(event.photos_base_path) / PROCESSED_FOLDER_NAME
    if not processed_root.exists():
        return set()

    if items is None:
        items = discover_processable(event)

    # Local import keeps ``process_render`` out of the module-level
    # graph — circular import otherwise (process_render → models →
    # vocabulary → process_discovery via Scenario aliases).
    from core.path_builder import day_folder_name
    from core.process_render import output_filename

    done: set[Path] = set()
    for it in items:
        expected = (
            processed_root
            / day_folder_name(it.day)
            / output_filename(it.timestamp, it.path)
        )
        if expected.exists():
            done.add(it.path)
    return done
