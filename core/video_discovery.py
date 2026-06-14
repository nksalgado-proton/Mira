"""Discovery for the Process Videos workflow — find videos to process.

Walks an event's per-day folders looking for video files. Like the
Process Photos discovery, this preserves the on-disk source folder
verbatim on each item so the UI can group cards by the user's
taxonomy (``video``, ``gopro``, ``cellphone``, etc.) without
forcing a canonical taxonomy.

Output folders the Process Videos pass writes into are skipped on
re-walks so the user doesn't see clip exports as fresh input.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.models import Event, TripDay
from core.path_builder import (
    EXTRACTED_FRAMES_FOLDER_NAME,
    PROCESSED_DIR_NAME as PROCESSED_FOLDER_NAME,
    SELECTED_DIR_NAME,
    day_folder_name,
    find_day_folders_root,
    process_source_dir,
)

log = logging.getLogger(__name__)


# Folders we never treat as a video source, even if the user
# dropped a stray ``.mp4`` in there. The stage dirs at the event
# root never appear under ``02 Selected/``, so the ``_RESERVED_FOLDERS``
# guard below is mostly historical — kept as a belt-and-suspenders
# filter for hand-arranged events.
_RESERVED_FOLDERS: frozenset[str] = frozenset({PROCESSED_FOLDER_NAME})


# Video container extensions we know how to play / extract from.
# Conservative on purpose — niche formats (.mkv, .webm, .ts) might
# work via FFmpeg but the Qt media player won't preview them, so we
# leave them out until there's a real use case.
VIDEO_EXTENSIONS: frozenset[str] = frozenset({
    ".mp4", ".mov", ".m4v", ".avi",
    ".mts", ".m2ts",  # AVCHD / consumer camcorders
})


@dataclass(frozen=True)
class VideoItem:
    """One video discovered under an event's day folder, OR a single
    standalone file opened via the Video Tool.

    ``source_folder`` is the on-disk folder name verbatim (e.g.
    ``video``, ``gopro``, ``cellphone``) so the UI can show one
    card per source the user actually has, even when the canonical
    scenario is the same.

    ``timestamp`` is the file's mtime — we don't read EXIF here
    because video EXIF is rare and we don't need accurate sub-
    minute ordering for a per-day list. The mtime is good enough
    for "first video of the day" / "last video of the day".

    ``day`` is optional so the same dataclass works for the
    standalone Video Tool, where there's no event / no trip day —
    a single picked file. Code paths that require a day (the
    Process Videos host, day grouping, the player's title bar)
    must handle ``None`` explicitly. ``VideoSession`` does the
    output-dir branching internally based on this field."""

    path: Path
    source_folder: str
    timestamp: datetime
    day: Optional[TripDay] = None
    # spec/59 black-frame guarantee — the cached Day-Grid poster JPEG
    # (``thumb_cache.poster_path_if_cached``), shown by the player
    # surface until the decoder's first real frame. None = no poster
    # cached; the player falls back to its plain load behaviour.
    poster: Optional[Path] = None


def discover_videos(event: Event) -> list[VideoItem]:
    """Walk ``event.photos_base_path`` and return video items
    sorted by timestamp ascending.

    Empty list when:
      * the event has no base path
      * the path doesn't exist
      * the trip has no days
      * no day folder contains a recognized video file
    """
    if not event.photos_base_path:
        return []
    event_root = Path(event.photos_base_path)
    if not event_root.exists() or not event_root.is_dir():
        return []
    if not event.trip_days:
        return []

    day_by_folder_name: dict[str, TripDay] = {
        day_folder_name(d): d for d in event.trip_days
    }

    # Read from the same configurable source dir Process Photos uses
    # (event_settings["process_source_dir"], default "02 Selected").
    # Lightroom's publish step copies videos through unmodified, so
    # when the user is on the LRC TIFF source dir the videos are
    # right there alongside the TIFFs — symmetry beats forking the
    # discovery logic by media type.
    source_dir_name = (
        event.event_settings.get("process_source_dir")
        or SELECTED_DIR_NAME
    )
    source_root = process_source_dir(event_root, source_dir_name)
    if not source_root.exists():
        return []
    # Descend through a single intermediate dir if needed — Lightroom
    # Classic's publish step nests day folders under the catalog
    # source dir name. See path_builder.find_day_folders_root.
    source_root = find_day_folders_root(
        source_root, set(day_by_folder_name.keys()),
    )
    items: list[VideoItem] = []
    for day_dir in source_root.iterdir():
        if not day_dir.is_dir():
            continue
        if day_dir.name in _RESERVED_FOLDERS:
            continue
        day = day_by_folder_name.get(day_dir.name)
        if day is None:
            log.debug(
                "video discovery: skipping unrecognized day %s",
                day_dir.name,
            )
            continue

        for entry in day_dir.iterdir():
            if entry.is_file():
                # Stray video at the day-folder root. Treat the day
                # itself as the source — gives the UI a card labeled
                # like the day, which is fine when there's no
                # subfolder to attribute it to.
                if entry.suffix.lower() in VIDEO_EXTENSIONS:
                    items.append(_make_item(entry, day, day.description or "(root)"))
                continue
            if not entry.is_dir():
                continue
            # Sub-folder: skip the reserved output ones, then walk for
            # videos one level deep. Deeper structures (e.g.
            # ``video/100GOPRO/``) get flattened by walking
            # recursively; the source_folder stays as the immediate
            # child of the day.
            if entry.name in _RESERVED_FOLDERS:
                continue
            if entry.name == EXTRACTED_FRAMES_FOLDER_NAME:
                # Frames written by us — never re-process as video.
                continue
            source_folder = entry.name
            for video_path in entry.rglob("*"):
                if not video_path.is_file():
                    continue
                if video_path.suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                items.append(_make_item(video_path, day, source_folder))

    items.sort(key=lambda it: it.timestamp)
    log.info(
        "video discovery: found %d video(s) across %d day folder(s)",
        len(items),
        len({it.day.day_number for it in items}),
    )
    return items


def _make_item(path: Path, day: TripDay, source_folder: str) -> VideoItem:
    try:
        ts = datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        ts = datetime.fromtimestamp(0)
    return VideoItem(
        path=path,
        day=day,
        source_folder=source_folder,
        timestamp=ts,
    )
