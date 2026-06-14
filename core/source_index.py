"""EXIF-driven source index for the photos-import flow.

Nelson 2026-05-21: the original photos-import flow required the user
to pre-sort their archive into ``Root/<CameraName>/…`` subfolders so
:func:`core.reconcile_pipeline.reconcile_commit` could walk one
camera at a time. That's friction (a per-trip pre-sort task) AND
brittle (one misnamed folder breaks the import).

This module replaces the pre-sort with a single recursive EXIF scan.
The scan groups every photo + video under a chosen root by the
canonical ``camera_id`` (EXIF ``Model``, fallback ``Make`` —
:func:`core.fresh_source.camera_id_for`). The :class:`SourceIndex`
that comes out is the input to the rest of the past-photos flow
(camera-config dialog + reconcile_commit).

The same scanning machinery (``fresh_source.read_source_items``) is
reused — one batched exiftool call for the whole tree, no per-file
subprocess overhead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

# Strong-signal substrings that identify a phone Model. The list is
# kept small and conservative — false positives cost the user a
# checkbox flip in the camera-config step; false negatives cost the
# same. Used by :func:`looks_like_phone` to *default* the is_phone
# flag in step 3; the user can override per-camera if needed.
#
# Why the substrings (not the brand `Make`): some bodies write only
# ``Model`` and not ``Make`` (Nelson's HERO12 on video), so we key
# off Model — and Model carries the brand for phones (``iPhone 15``,
# ``Pixel 8``).
_PHONE_TOKENS = (
    "iphone", "ipad", "pixel", "galaxy", "samsung",
    "redmi", "xiaomi", "huawei", "motorola",
    "oneplus", "nokia",
)


# Sentinel camera_id used to bucket files whose EXIF has no readable
# ``Make`` / ``Model`` — they're still scanned, indexed, and
# importable, just under one "unknown camera" group the user can
# flip to a real id (or accept as-is) in step 3.
UNIDENTIFIED_CAMERA_ID = "_unidentified"


def _serial_for(raw) -> Optional[str]:
    """The most-specific body serial reachable in ``raw`` (an
    exiftool-JSON dict), or ``None`` when no serial is recorded.

    Read priority — most-specific to least:
      1. ``InternalSerialNumber`` (MakerNotes; Panasonic, Sony,
         Canon, Nikon — most reliable; survives copies)
      2. ``BodySerialNumber`` (newer standard EXIF)
      3. ``SerialNumber`` (original standard EXIF)

    Returns ``None`` for empty values, ``"0"``, ``"n/a"``, etc. so
    cameras that fill the tag with a placeholder don't accidentally
    collide. Phones (iPhone, most Android) write none of these and
    correctly return ``None``.
    """
    if not raw:
        return None
    for key in ("InternalSerialNumber", "BodySerialNumber", "SerialNumber"):
        v = raw.get(key)
        if v is None:
            continue
        s = str(v).strip()
        if not s:
            continue
        # Defensive against placeholder values some cameras write.
        if s.lower() in ("0", "n/a", "none", "null", "unknown"):
            continue
        return s
    return None


def looks_like_phone(camera_id: str) -> bool:
    """True iff ``camera_id`` (an EXIF ``Model``) looks like a phone.

    Substring check against :data:`_PHONE_TOKENS`. Case-insensitive.
    Empty / unidentified ids return False (we don't want to default-
    classify "no clue" as a phone — phones have very recognisable
    Model names)."""
    s = (camera_id or "").lower()
    if not s or s == UNIDENTIFIED_CAMERA_ID:
        return False
    return any(t in s for t in _PHONE_TOKENS)


@dataclass
class ScannedCamera:
    """One distinct camera body found during a source scan.

    ``camera_id`` is the EXIF-derived identity (Model alone, fallback
    Make). ``paths`` lists every photo + video that this body shot,
    sorted. ``timestamps`` is the per-file capture time as read from
    EXIF (None when no readable timestamp — those files still belong
    to the camera but will be quarantined under
    ``_no_timestamp`` at reconcile time).

    ``date_range`` is the (earliest, latest) calendar date over the
    camera's files that *had* a timestamp — None when every file in
    this group lacked one. The UI uses this to show users a date hint
    per camera ("iPhone — 1,247 photos, Oct 24 – Nov 9") so they can
    sanity-check at step 3 that they're pointing at the right trip's
    archive before kicking off the import.
    """
    camera_id: str
    is_phone: bool
    file_count: int
    date_range: Optional[tuple[date, date]]
    paths: list[Path]
    timestamps: dict[Path, Optional[datetime]]


@dataclass
class SourceIndex:
    """Result of :func:`scan_source_tree` — a complete inventory of
    every camera + media file under ``root``.

    Empty ``cameras`` means the root was empty or unreadable; the
    caller surfaces the right "no photos found" message in that
    case. The UI flow halts (a fresh root is needed).

    ``items`` is the flat list of :class:`core.fresh_source.SourceItem`
    behind the grouping. Kept so the existing cull-time consumers
    (``cameras_in``, ``cull_shell_page.load_fresh_items``) that
    expect a flat list don't have to be rewritten — they take
    ``index.items``. The grouped ``cameras`` dict is the new
    detector/UX surface."""

    root: Path
    cameras: dict[str, ScannedCamera] = field(default_factory=dict)
    total_files: int = 0
    items: list = field(default_factory=list)  # list[SourceItem]

    @property
    def is_empty(self) -> bool:
        return self.total_files == 0

    def cameras_sorted(self) -> list[ScannedCamera]:
        """Cameras most-files-first (then by camera_id) — the order
        the step-3 dialog displays them. Most-files-first surfaces
        the trip's main body at the top, which is almost always the
        right reference candidate when no phone is present."""
        return sorted(
            self.cameras.values(),
            key=lambda c: (-c.file_count, c.camera_id),
        )


def scan_source_tree(
    root: Path,
    progress: Optional[Callable[[str, int, int], None]] = None,
) -> SourceIndex:
    """Walk ``root`` recursively, read EXIF on every media file once,
    and group by camera_id.

    Reuses :func:`core.fresh_source.read_source_items` for the heavy
    lift — single batched exiftool call across the whole tree, so a
    Nepal-sized archive (~3000 files) returns in seconds, not the
    minutes a per-file invocation would cost. The function is safe
    to call off the GUI thread; it never touches Qt.

    ``progress`` is the usual ``(msg, current, total)`` callback shape
    used elsewhere in the codebase. It fires at four named stages
    (Nelson 2026-05-21 — the user reported an empty "Please wait"
    dialog because the legacy single-emit-before-blocking-call
    pattern gave Qt no chance to paint a label before the slow
    exiftool subprocess froze the GUI thread):

      1. ``"Walking folder tree..."`` — before the recursive walk.
      2. ``"Reading EXIF on N file(s)..."`` — right before the slow
         batched exiftool call (the long-pole). Fires AFTER the walk
         so ``N`` is the real file count.
      3. ``"Grouping N file(s) by camera..."`` — after exiftool
         returns, while grouping is in progress.
      4. ``"Found K camera(s), N file(s)."`` — final summary.

    Files whose EXIF has no readable ``Make`` / ``Model`` are grouped
    under :data:`UNIDENTIFIED_CAMERA_ID` rather than dropped — they
    still belong to the trip and the user can categorise them in
    step 3.
    """
    from core.exif_reader import read_exif_batch
    from core.fresh_source import SourceItem, camera_id_for
    from core.folder_scanner import walk_photo_paths
    from core.video_discovery import VIDEO_EXTENSIONS

    def _emit(msg: str, cur: int = 0, tot: int = 0) -> None:
        if progress is not None:
            progress(msg, cur, tot)

    root_path = Path(root)
    # Stage 1: walk the file tree. Fast (filesystem-bound, not
    # subprocess-bound), but on cold cache for a Nepal-sized archive
    # it can take a couple of seconds, so emit a label first.
    _emit("Walking folder tree...", 0, 0)
    try:
        photos = list(walk_photo_paths(root_path))
    except (FileNotFoundError, NotADirectoryError):
        photos = []
    vids = (
        [
            p for p in root_path.rglob("*")
            if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
        ]
        if root_path.exists() else []
    )
    files = photos + vids
    if not files:
        log.info(
            "source_index: %s had no readable media files", root_path)
        _emit("Found 0 camera(s), 0 file(s).", 0, 0)
        return SourceIndex(root=root_path, total_files=0)

    # Stage 2: batched EXIF read — the long-pole. Use an indeterminate
    # bar (tot=0 → Qt bouncing animation) because read_exif_batch is a
    # single blocking exiftool call with no per-file feedback; showing
    # 0/N looks frozen. The label tells the user what is happening.
    n_files = len(files)
    _emit(
        f"Reading EXIF on {n_files} file(s)...",
        0, 0,
    )
    pe_entries = read_exif_batch(files)
    items: list[SourceItem] = []
    # Path → body serial (or None when EXIF has no serial — i.e.
    # iPhones and most Androids, which deliberately strip serial
    # numbers for privacy). Used by Stage 3b to split two
    # physically-distinct same-model cameras (Nelson 2026-05-23:
    # two G9 MkIIs in the family).
    serial_for_path: dict[Path, Optional[str]] = {}
    # B-002 (2026-05-24): when EXIF carries no usable timestamp —
    # the WhatsApp case is the canonical example: WhatsApp strips
    # DateTimeOriginal/CreateDate AND Make/Model but leaves the
    # capture date in the filename (``WhatsApp Image YYYY-MM-DD at
    # HH.MM.SS_xxx.jpg``) — fall back to the same filename-recovery
    # helper task #122 wired into reconcile/quarantine. Without
    # this, those files reach the plan-builder with
    # ``timestamp=None`` and get silently filtered out, their dates
    # never reach the trip plan. Counter is for log telemetry only.
    from core.filename_timestamp import parse_timestamp_from_filename
    recovered_from_filename = 0
    for pe in pe_entries:
        ts = pe.timestamp
        if ts is None:
            pt = parse_timestamp_from_filename(pe.path.name)
            if pt is not None:
                ts = pt.dt
                recovered_from_filename += 1
        items.append(SourceItem(
            path=pe.path, timestamp=ts,
            camera_id=camera_id_for(pe.raw or {}),
            # spec/45 — propagate phone-TZ + GPS so the downstream phone_tz
            # aggregator can read them without revisiting the EXIF batch.
            tz_offset_minutes=pe.tz_offset_minutes,
            gps_lat=pe.gps_lat,
            gps_lon=pe.gps_lon,
        ))
        serial_for_path[pe.path] = _serial_for(pe.raw or {})
    if recovered_from_filename:
        log.info(
            "source_index: recovered timestamp from filename for "
            "%d file(s) with no usable EXIF date",
            recovered_from_filename,
        )

    # Stage 3: group by EXIF-derived camera_id. ``camera_id_for``
    # returns "" when both Make and Model are empty — bucket those
    # under UNIDENTIFIED_CAMERA_ID so the user still sees them.
    _emit(
        f"Grouping {n_files} file(s) by camera...",
        n_files, n_files,
    )
    by_cam: dict[str, list] = {}
    for it in items:
        cid = it.camera_id or UNIDENTIFIED_CAMERA_ID
        by_cam.setdefault(cid, []).append(it)

    # Stage 3b — serial-aware split (Nelson 2026-05-23). For each
    # Model bucket that contains photos with TWO OR MORE distinct
    # body-serial numbers, split into per-serial sub-buckets so two
    # physically-distinct bodies of the same model (e.g. two G9 MkIIs)
    # don't get merged. Buckets with zero or one distinct serial are
    # untouched — the common single-body case stays as bare
    # ``<Model>`` (no serial suffix appended). Phones merge as-is
    # (they write no serial; the split simply doesn't fire).
    split_by_cam: dict[str, list] = {}
    for base_cid, group in by_cam.items():
        serials_present = {
            serial_for_path.get(it.path) for it in group
        }
        serials_present.discard(None)
        if len(serials_present) <= 1:
            split_by_cam[base_cid] = group
            continue
        # Multi-body bucket — split. Photos with a serial → new
        # camera_id ``<Model> #<short>``; photos without a serial
        # (rare in this branch but defensive) → bare base_cid.
        for it in group:
            s = serial_for_path.get(it.path)
            if s is None:
                new_cid = base_cid
            else:
                short = s[-4:] if len(s) > 4 else s
                new_cid = f"{base_cid} #{short}"
            split_by_cam.setdefault(new_cid, []).append(
                SourceItem(
                    path=it.path, timestamp=it.timestamp,
                    camera_id=new_cid,
                    # spec/45 — preserve the EXIF-derived TZ + GPS across the
                    # serial-split rebuild (otherwise the per-camera phone
                    # heuristic would see all-None for items in a split bucket).
                    tz_offset_minutes=it.tz_offset_minutes,
                    gps_lat=it.gps_lat,
                    gps_lon=it.gps_lon,
                )
            )
    by_cam = split_by_cam
    # Rebuild ``items`` from the (possibly split) buckets so the
    # flat list returned in SourceIndex.items reflects the final
    # camera_ids the rest of the pipeline will see.
    items = [it for grp in by_cam.values() for it in grp]

    cameras: dict[str, ScannedCamera] = {}
    for cid, group in by_cam.items():
        paths = sorted(g.path for g in group)
        timestamps = {g.path: g.timestamp for g in group}
        dates = sorted({g.timestamp.date() for g in group if g.timestamp})
        date_range: Optional[tuple[date, date]] = (
            (dates[0], dates[-1]) if dates else None
        )
        cameras[cid] = ScannedCamera(
            camera_id=cid,
            is_phone=(
                cid != UNIDENTIFIED_CAMERA_ID and looks_like_phone(cid)
            ),
            file_count=len(group),
            date_range=date_range,
            paths=paths,
            timestamps=timestamps,
        )

    _emit(
        f"Found {len(cameras)} camera(s), {len(items)} file(s).",
        len(items), len(items),
    )
    # Flat items list (for cull-time consumers that still take
    # ``list[SourceItem]``). Populated below after the SourceIndex
    # is constructed so the field is always in sync.
    log.info(
        "source_index: %s → %d camera(s), %d file(s) — %s",
        root_path, len(cameras), len(items),
        ", ".join(
            f"{c.camera_id}({c.file_count})"
            for c in sorted(
                cameras.values(),
                key=lambda c: (-c.file_count, c.camera_id),
            )
        ),
    )
    return SourceIndex(
        root=root_path,
        cameras=cameras,
        total_files=len(items),
        items=items,
    )
