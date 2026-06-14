"""Per-event metric aggregators for the dashboard PhaseButton charts.

The dashboard's PhaseButton cards each render a small chart
representing the phase's progress, but the *useful* metrics differ
phase to phase (Nelson 2026-05-21):

* **Capture** — how many photos did each camera contribute? (Pie
  per camera_id)
* **Cull** — what fraction of the original photos survived? (Kept
  ratio donut: kept / total_captured)
* **Select** — what styles dominate the Kept set? (Pie per style)
* **Process** — what fraction of the original photos came through
  Process? (Kept ratio donut: processed / total_captured)
* **Plan** — a timezone map (no counting; just the trip's TZ).
* **Curate** — same kept-ratio shape as Process for now.

All counts come from walking the on-disk event tree. Pure logic,
no Qt. Each helper accepts the resolved ``event_root`` (already
``Path``-typed) so tests can run against any tmp_path layout.

"Original number of photos" is the count under ``00 - Captured/``,
mirroring what the user sees in the file system (Nelson 2026-05-21:
"Original = number of photos in the captured or culled phase").
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.path_builder import (
    CAPTURED_SUBDIRS,
    captured_dir,
    culled_dir,
    processed_dir,
    selected_dir,
)

log = logging.getLogger(__name__)


# Extensions counted as photos. Anything else under the phase trees
# (.json journals, .tmp files mid-write, etc.) is ignored.
_PHOTO_EXTS: frozenset[str] = frozenset({
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".heif",
    ".webp", ".bmp",
    # RAW formats — every brand we currently care about
    ".rw2", ".arw", ".srf", ".sr2", ".cr2", ".cr3", ".nef", ".nrw",
    ".raf", ".orf", ".pef", ".dng", ".srw",
})


def _is_photo(path: Path) -> bool:
    """True iff ``path`` is a regular file with a photo extension.
    Defensive: a journal or temp file in the wrong tree must not
    inflate the count."""
    return (
        path.is_file()
        and path.suffix.lower() in _PHOTO_EXTS
    )


def _count_photos_recursive(root: Path) -> int:
    """Count photo files anywhere under ``root``. Empty / missing
    directories return 0 (never raises — the dashboard mustn't crash
    when a phase folder doesn't exist yet)."""
    if not root.exists() or not root.is_dir():
        return 0
    total = 0
    for child in root.rglob("*"):
        if _is_photo(child):
            total += 1
    return total


# ── Captured phase ──────────────────────────────────────────────


def captured_photos_per_camera(event_root: Path) -> dict[str, int]:
    """Walk ``00 - Captured/<bucket>/<day>/<camera_id>/`` and return
    ``{camera_id: photo_count}``. Cameras with zero photos don't
    appear in the dict.

    Camera identification is by the immediate folder name (e.g.
    ``"DC-G9M2"``, ``"iPhone 13"``, ``"HERO12 Black"``) — the
    convention enforced by :mod:`core.fresh_source`. Photos collapsed
    across all buckets and days, so a single camera that contributed
    to both ``_cameras`` and ``_other`` is counted once."""
    counts: dict[str, int] = {}
    cap_root = captured_dir(event_root)
    if not cap_root.exists():
        return counts
    for bucket in CAPTURED_SUBDIRS:
        bucket_dir = cap_root / bucket
        if not bucket_dir.is_dir():
            continue
        for day_dir in bucket_dir.iterdir():
            if not day_dir.is_dir():
                continue
            for camera_dir in day_dir.iterdir():
                if not camera_dir.is_dir():
                    continue
                n = _count_photos_recursive(camera_dir)
                if n > 0:
                    counts[camera_dir.name] = counts.get(
                        camera_dir.name, 0) + n
    return counts


def total_captured_photos(event_root: Path) -> int:
    """Sum of :func:`captured_photos_per_camera`. The "original
    number of photos" referenced by other phase metrics."""
    return sum(captured_photos_per_camera(event_root).values())


# ── Cull phase ──────────────────────────────────────────────────


def kept_in_cull_count(event_root: Path) -> int:
    """Number of photos that survived the Cull phase (= files now
    under ``01 - Culled/``). The Cull layout is per-camera under each
    bucket, so a single walk of the whole tree gives the right
    answer."""
    return _count_photos_recursive(culled_dir(event_root))


# ── Select phase ────────────────────────────────────────────────


def kept_in_select_by_style(event_root: Path) -> dict[str, int]:
    """Walk ``02 - Selected/<day>/<style>/`` and return
    ``{style_label: photo_count}``. Style is the immediate folder
    name under each day (e.g. ``"wildlife"``, ``"landscape"``,
    ``"uncategorized"``). Styles with zero photos don't appear.

    The Select layout is consolidated (one ``<style>/`` per day),
    so the same style across multiple days is collapsed into a
    single dict entry."""
    counts: dict[str, int] = {}
    sel_root = selected_dir(event_root)
    if not sel_root.exists():
        return counts
    for day_dir in sel_root.iterdir():
        if not day_dir.is_dir():
            continue
        for style_dir in day_dir.iterdir():
            if not style_dir.is_dir():
                continue
            n = _count_photos_recursive(style_dir)
            if n > 0:
                counts[style_dir.name] = counts.get(
                    style_dir.name, 0) + n
    return counts


def kept_in_select_count(event_root: Path) -> int:
    """Total photos under ``02 - Selected/``."""
    return sum(kept_in_select_by_style(event_root).values())


# ── Process phase ───────────────────────────────────────────────


def kept_in_process_count(event_root: Path) -> int:
    """Number of photos that came through Process (= files under
    ``03 - Processed/``)."""
    return _count_photos_recursive(processed_dir(event_root))


# ── Curate phase ────────────────────────────────────────────────


def kept_in_curate_count(event_root: Path) -> int:
    """Number of unique photos that made it into ANY slideshow tier
    of ``04 - Curated/`` (Nelson 2026-05-22: completes the funnel
    chart story — every non-Capture phase shows the same denominator,
    ``total_captured_photos``).

    Slideshow tiers = anything UNDER ``04 - Curated/`` *except* the
    archive buckets ``Compositions`` and ``Collage Only``. The
    cascade hardlinks the same photo into multiple bucket folders
    (e.g., a Best+Macro photo lives in ``All-Time Best/``,
    ``Macro/``, AND ``Short / Medium / Long``); we count by unique
    filename so cascade duplicates fold to one.

    Returns 0 if ``04 - Curated/`` doesn't exist yet (Curate phase
    hasn't been Exported)."""
    from core.path_builder import curated_dir
    root = curated_dir(event_root)
    if not root.is_dir():
        return 0
    EXCLUDE = {"Compositions", "Collage Only"}
    seen: set[str] = set()
    for bucket_dir in root.iterdir():
        if not bucket_dir.is_dir():
            continue
        if bucket_dir.name in EXCLUDE:
            continue
        for p in bucket_dir.rglob("*"):
            if p.is_file():
                seen.add(p.name)
    return len(seen)
