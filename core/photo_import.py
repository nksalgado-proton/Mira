"""Photo import — folder → event day bucket.

Walking-skeleton step 6: copy image files from a user-picked source
folder into ``<event_root>/02 Selected/Dia N/<bucket>/``. No EXIF
classification yet — every file lands in the single bucket
``Individual``. After copy, an empty ``_culler_session.json`` journal
is written into the day folder so step 8's culler has something to
read on first open.

Pure-logic module (no Qt). The trip-dashboard page is the sole UI
consumer.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from core.folder_scanner import walk_photo_paths
from core.models import Event, TripDay
from core.path_builder import culled_day_path, culled_dir, event_root_path
from core.settings import user_data_dir


log = logging.getLogger(__name__)


# Bucket name used by the walking-skeleton importer that hasn't run
# the bucket scanner yet — every photo goes here. Real time/hardware-
# driven buckets (Bursts, Moments, Brackets, Individual, Live Photo,
# Video — see core/bucket_scanner.py) land in Phase 5. Note: "bucket"
# in the culler always means a time/hardware grouping, NOT a photo-
# genre grouping. Per-photo scene classification (macro/portrait/...)
# happens inside a bucket and drives kept-photo destination only.
BUCKET_INDIVIDUAL = "Individual"

# Journal filename inside each day folder. Step 8 fleshes the
# schema out; step 6 just writes a minimal version so the cull
# page never opens against a missing file.
CULLER_JOURNAL_NAME = "_culler_session.json"
CULLER_JOURNAL_VERSION = 1


@dataclass
class ImportResult:
    """Outcome of a single import-folder call.

    ``copied`` is the number of files newly written to the bucket.
    ``skipped`` covers files whose target name already exists at the
    same size (idempotent re-import of the same source). ``destination``
    is the bucket directory, useful for the UI to surface in a status
    message.
    """

    copied: int
    skipped: int
    destination: Path


# ── Path helpers ─────────────────────────────────────────────────


def default_photos_base_path() -> Path:
    """Where event photos go when the user hasn't picked a path.

    Lives inside ``user_data_dir()`` so a fresh install Just Works
    without an onboarding step. Users can still override this per
    event by setting ``event.photos_base_path`` directly (the
    onboarding flow that does so lands in Phase 5).
    """
    base = user_data_dir() / "photos"
    base.mkdir(parents=True, exist_ok=True)
    return base


def ensure_event_root(event: Event) -> Path:
    """Return the event root path, creating directories and assigning
    ``event.photos_base_path`` if it was empty.

    The caller is responsible for persisting the event after this call
    when ``photos_base_path`` was newly filled in — we don't save here
    so importers can batch the save with the rest of their mutation.
    """
    if not event.photos_base_path:
        event.photos_base_path = str(default_photos_base_path())
    root = event_root_path(event.photos_base_path, event)
    root.mkdir(parents=True, exist_ok=True)
    # Skeleton importer lands keepers in the Cull-phase tree
    # (01 - Culled) per the 2026-05-19 taxonomy — the real cull
    # Export is the production writer; this stand-in is consistent
    # with it (and is slated for Stage-E retirement).
    culled_dir(root).mkdir(parents=True, exist_ok=True)
    return root


def day_bucket_dir(
    event: Event,
    day: TripDay,
    bucket: str = BUCKET_INDIVIDUAL,
) -> Path:
    """Return ``<event_root>/01 - Culled/Dia N/<bucket>/``.

    Caller is responsible for ``ensure_event_root`` if directories
    haven't been created yet. This helper only computes the path.
    """
    root = event_root_path(event.photos_base_path, event)
    return culled_day_path(root, day) / bucket


# ── Bucket inspection ────────────────────────────────────────────


def count_bucket(
    event: Event,
    day: TripDay,
    bucket: str = BUCKET_INDIVIDUAL,
) -> int:
    """Count image files currently in a day's bucket directory.

    Returns 0 when the directory doesn't exist (event never imported,
    or day was just added). Walks one level deep — buckets are flat.
    """
    if not event.photos_base_path:
        return 0
    bucket_path = day_bucket_dir(event, day, bucket)
    if not bucket_path.exists() or not bucket_path.is_dir():
        return 0
    try:
        return sum(1 for p in bucket_path.iterdir() if p.is_file())
    except OSError as exc:
        log.warning("count_bucket failed for %s: %s", bucket_path, exc)
        return 0


# ── Import ───────────────────────────────────────────────────────


def import_folder_to_day(
    event: Event,
    day: TripDay,
    source: Path,
    *,
    bucket: str = BUCKET_INDIVIDUAL,
    recursive: bool = True,
) -> ImportResult:
    """Copy image files from ``source`` into the day's bucket.

    No EXIF reading or classification — every image goes to the same
    bucket. Files whose target name already exists at the same size
    are skipped (idempotent). A minimal ``_culler_session.json`` is
    written into the day folder if absent.

    Returns counts plus the destination path for UI display. The
    event is mutated in place (``photos_base_path`` may be filled in)
    but is NOT saved — the caller persists the event after the import
    completes so unrelated state changes can be batched.

    Raises ``FileNotFoundError`` / ``NotADirectoryError`` from the
    folder scanner if ``source`` is missing or not a directory.
    """
    source = Path(source)
    ensure_event_root(event)
    bucket_path = day_bucket_dir(event, day, bucket)
    bucket_path.mkdir(parents=True, exist_ok=True)

    paths = walk_photo_paths(source, recursive=recursive)
    log.info(
        "Importing %d photo(s) from %s into %s",
        len(paths), source, bucket_path,
    )

    copied = 0
    skipped = 0
    for src in paths:
        dest = bucket_path / src.name
        if _already_imported(src, dest):
            skipped += 1
            continue
        try:
            shutil.copy2(str(src), str(dest))
            copied += 1
        except OSError as exc:
            log.error("Failed to copy %s → %s: %s", src, dest, exc)

    _ensure_culler_journal(bucket_path.parent, day.day_number, bucket)

    return ImportResult(copied=copied, skipped=skipped, destination=bucket_path)


def _already_imported(src: Path, dest: Path) -> bool:
    """Same-name + same-size heuristic for idempotent re-imports."""
    if not dest.exists():
        return False
    try:
        return src.stat().st_size == dest.stat().st_size
    except OSError:
        return False


def _ensure_culler_journal(
    day_dir: Path,
    day_number: int,
    bucket: str,
) -> None:
    """Initialize ``_culler_session.json`` if it doesn't exist.

    Atomic write-then-rename. Step 8 expands the schema; for now we
    just record the version, day, and the empty mark-map so the
    culler can open the file unconditionally.
    """
    path = day_dir / CULLER_JOURNAL_NAME
    if path.exists():
        return
    journal = {
        "version": CULLER_JOURNAL_VERSION,
        "day_number": day_number,
        "buckets": [bucket],
        "marks": {},
    }
    # B-009 (2026-05-25): write with the full three-layer protection
    # (atomic + history rotation + SHA256 sidecar) via the shared
    # ``core.atomic_journal`` engine. ``write_with_protection``
    # creates parent directories itself, so the explicit
    # ``day_dir.mkdir(...)`` is no longer required (the day_dir IS
    # the path's parent here).
    from core.atomic_journal import write_with_protection
    write_with_protection(path, journal)
    log.debug("Initialized culler journal at %s", path)
