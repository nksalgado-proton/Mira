"""Folder scanning helper — walks a directory, filters image files,
and produces RawExifEntry records ready for the classification pipeline.

Used by both the diagnostic tool (``diagnose_photos.py``) and the
ad-hoc folder classification entry point (``classify_folder`` in
``core.import_pipeline``). Centralized to avoid duplicating the
walk + filter + batch-EXIF-read logic.

The module does not modify any files on disk — it's strictly read-only.
"""

import logging
from pathlib import Path
from typing import Iterable, Optional

from core.import_pipeline import RawExifEntry
from core.logging_setup import log_activity

log = logging.getLogger(__name__)


# Extensions we recognize as photo files. Raw formats + common JPEGs.
# Video extensions are intentionally excluded — they go through a
# different pipeline (deferred to Phase G+).
PHOTO_EXTENSIONS: frozenset[str] = frozenset(
    {
        # Panasonic / Olympus RAW
        ".rw2", ".orf",
        # Sony RAW
        ".arw", ".srf", ".sr2",
        # Canon RAW
        ".cr2", ".cr3", ".crw",
        # Nikon RAW
        ".nef", ".nrw",
        # Fuji RAW
        ".raf",
        # Pentax RAW
        ".pef",
        # Leica / Panasonic DMC
        ".rwl",
        # Olympus OM System
        ".ori",
        # DNG (Adobe / generic)
        ".dng",
        # JPEG / HEIF (compressed out-of-camera or edited exports)
        ".jpg", ".jpeg", ".heic", ".heif",
        # TIFF (rare but valid)
        ".tif", ".tiff",
    }
)


def walk_photo_paths(
    root: Path,
    *,
    recursive: bool = True,
    extensions: Optional[Iterable[str]] = None,
) -> list[Path]:
    """Walk a folder and return a sorted list of photo file paths.

    Args:
        root: directory to scan. Must exist.
        recursive: if True, scan subdirectories too. Default True.
        extensions: optional override of the default PHOTO_EXTENSIONS set.
            Pass a custom set to limit or extend the filter.

    Returns:
        A list of absolute Path objects, sorted by full path for
        deterministic order.

    Raises:
        FileNotFoundError: if ``root`` does not exist
        NotADirectoryError: if ``root`` is not a directory
    """
    if not root.exists():
        raise FileNotFoundError(f"Folder does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {root}")

    allowed = (
        {ext.lower() for ext in extensions}
        if extensions is not None
        else PHOTO_EXTENSIONS
    )

    pattern = "**/*" if recursive else "*"
    paths: list[Path] = []
    for entry in root.glob(pattern):
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in allowed:
            continue
        paths.append(entry.resolve())

    paths.sort()
    return paths


def scan_folder(
    root: Path,
    *,
    recursive: bool = True,
    extensions: Optional[Iterable[str]] = None,
    progress_fn=None,
    chunk_size: int = 250,
) -> list[RawExifEntry]:
    """Walk a folder and return RawExifEntry records ready for classification.

    Reads EXIF in batched ExifTool subprocess calls. Splitting into chunks
    keeps memory bounded, lets the UI report determinate progress, and
    keeps the per-batch latency short enough that ``processEvents`` calls
    between chunks don't visibly stall.

    Args:
        root: directory to scan
        recursive: if True, scan subdirectories
        extensions: optional photo-extension filter override
        progress_fn: optional callback ``(done: int, total: int)`` invoked
            after each chunk (and once at the very start with done=0).
            Exceptions raised here are swallowed so the scan never fails
            because the UI handler did. Pass None to disable.
        chunk_size: photos per ExifTool call. 250 hits a sweet spot for
            session sizes up to a few thousand: short enough for fluid
            progress (~3-5s per chunk on SSD), large enough that
            subprocess overhead doesn't dominate.

    Returns:
        A list of RawExifEntry with .path and .exif populated, ready to
        feed into ``classify_imported_batch()`` or any other consumer.

    Raises:
        FileNotFoundError / NotADirectoryError: via ``walk_photo_paths``
    """
    with log_activity(log, f"scanning folder {root}"):
        paths = walk_photo_paths(root, recursive=recursive, extensions=extensions)
        return scan_paths(
            paths,
            progress_fn=progress_fn,
            chunk_size=chunk_size,
            log_label=f"folder {root}",
        )


def scan_paths(
    paths: Iterable[Path],
    *,
    progress_fn=None,
    chunk_size: int = 250,
    log_label: str = "paths",
) -> list[RawExifEntry]:
    """Read EXIF for an explicit list of paths and return RawExifEntry
    records ready for classification.

    Sibling to ``scan_folder`` — same EXIF reading + chunking + progress
    semantics, but for callers that already have the path list (e.g.,
    SD-card import where the upstream scan picked which files belong to
    which day, or any flow that wants to scan a curated subset of a
    folder rather than the whole tree).

    Same arguments as scan_folder's ``progress_fn`` / ``chunk_size``.
    Files that ExifTool cannot parse are dropped with a logged warning
    (mirroring scan_folder's behavior).
    """
    def _emit(done: int, total: int) -> None:
        if progress_fn is None:
            return
        try:
            progress_fn(done, total)
        except Exception as exc:  # noqa: BLE001 — UI errors must not abort scan
            log.debug("progress_fn raised: %s (continuing)", exc)

    paths = list(paths)
    if not paths:
        log.info("scan_paths: no paths to scan (%s)", log_label)
        _emit(0, 0)
        return []

    total = len(paths)
    log.info("Scanning %d photo path(s) [%s]", total, log_label)
    _emit(0, total)

    from core.exif_reader import read_exif_batch

    photos = []
    try:
        for i in range(0, total, chunk_size):
            chunk = paths[i:i + chunk_size]
            photos.extend(read_exif_batch(chunk))
            _emit(min(i + len(chunk), total), total)
    except Exception as exc:  # noqa: BLE001 — defensive boundary
        log.error("Batch EXIF read failed [%s]: %s", log_label, exc, exc_info=True)
        return []

    # Build a path → raw EXIF dict lookup. ExifTool may report the
    # SourceFile with different path separators or casing, so we
    # normalize via pathlib on both sides.
    exif_by_resolved: dict[str, dict] = {}
    for photo in photos:
        source = photo.raw.get("SourceFile", str(photo.path))
        try:
            key = str(Path(source).resolve())
        except OSError:
            key = str(source)
        exif_by_resolved[key] = dict(photo.raw)

    entries: list[RawExifEntry] = []
    missing_count = 0
    for path in paths:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        raw = exif_by_resolved.get(key)
        if raw is None:
            # Fallback: match by basename only
            for stored_key, stored_raw in exif_by_resolved.items():
                if Path(stored_key).name == path.name:
                    raw = stored_raw
                    break
        if raw is None:
            missing_count += 1
            log.warning("No EXIF data returned for %s", path.name)
            continue
        entries.append(RawExifEntry(path=path, exif=raw))

    if missing_count:
        log.info(
            "Scanned %d path(s); %d had no readable EXIF (dropped) [%s]",
            len(paths), missing_count, log_label,
        )
    else:
        log.info(
            "Scanned %d path(s), all with EXIF [%s]",
            len(entries), log_label,
        )
    return entries
