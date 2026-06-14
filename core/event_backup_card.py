"""Card-offload engine — Stage D core (frozen 2026-05-19 scope
expansion; the mechanism that backs CLAUDE.md invariant #9 — the
SD-card-wipe safety gate).

Walks a source directory (an SD card, an SSD pile, a phone DCIM,
anywhere on disk), copies every photo/video **byte-untouched** into
the event's ``00 - Captured/`` pristine mirror with a per-session
subdirectory and a side-car manifest carrying a SHA-256 content hash
for every copy. The manifest is the integrity-verify gate: a downstream
``verify_offload(manifest)`` re-hashes the destination and returns a
pass/fail report. Only after pass does any consumer (the live-trip
"Back up this card" surface) offer the destructive wipe.

This engine has two real callers:

1. **"Back up this card"** (task #9) — live-trip SD-card → 00 Captured.
   Followed by ``verify_offload`` + (only if source is a removable
   drive, task #6) the wipe-offer dialog.
2. **"Create from Past Photos"** (task #13) — retroactive import from
   a folder of photos already off the card. Followed by event creation
   from the manifest. No wipe ever offered.

Both paths go through the same code; the *whether-to-wipe* decision
is a UI concern based on source-removability, never an engine concern.

Per-session subdirectory naming — ``offload_YYYYmmdd-HHMMSS`` — so
multiple offloads of the same camera on the same Day stay distinct
(two cards filled, both offloaded under Dia 5 → two sibling subdirs
under ``00 - Captured/_cameras/Dia 5 - .../G9_mkII/``, originals never
mixed). Lexicographically sortable, human-scannable.

EXIF-driven Day routing reuses ``CameraCalibration`` from
``core.clock_calibration`` (same machinery as reconcile, identical
contract). When the event has no plan yet (early-trip card back-up
before the user has written day descriptions), pass ``day_by_number=
None`` and the engine writes a flat per-session layout under
``<bucket>/<camera_id>/offload_<TS>/`` — no Day folders; the user can
reorganise later.

Files with no readable EXIF timestamp quarantine to
``00 - Captured/_no_timestamp/<camera_id>/offload_<TS>/`` (same
contract as reconcile_pipeline).

Qt-free. Pure dataclasses + filesystem.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

from core.clock_calibration import CameraCalibration
from core.cull_export import CollisionPolicy
from core.exif_reader import read_exif_batch
from core.models import TripDay
from core.path_builder import (
    CAPTURED_CAMERAS_SUBDIR,
    CAPTURED_NO_TIMESTAMP_SUBDIR,
    CAPTURED_OTHER_SUBDIR,
    CAPTURED_PHONES_SUBDIR,
    captured_dir,
    day_folder_name,
)

log = logging.getLogger(__name__)


# Same extension set reconcile_pipeline uses — photos + videos.
# Anything outside this set is ignored (the user's accidental .txt
# notes file on the card doesn't end up in 00-Captured).
_MEDIA_EXTENSIONS: frozenset[str] = frozenset({
    ".rw2", ".raf", ".arw", ".nef", ".cr2", ".cr3", ".dng", ".orf", ".pef",
    ".jpg", ".jpeg",
    ".heic", ".heif",
    ".tif", ".tiff",
    ".mp4", ".mov", ".m4v",
})

# SHA-256 chunk size for the streaming hash-while-copy. 1 MiB balances
# Python-loop overhead against memory footprint; a 50 MB RAW takes ~50
# iterations.
_HASH_CHUNK = 1024 * 1024

# Schema version for the side-car manifest — bumped when the on-disk
# shape changes incompatibly. Readers tolerate equal or higher within
# the same major.
MANIFEST_SCHEMA_VERSION = 1

# Manifest sidecar filename — lives at the root of the per-session
# subdirectory. ``.offload-manifest.json`` (leading dot so jf Folder
# Publisher and LRC skip it; not on the photo extension list).
MANIFEST_FILENAME = ".offload-manifest.json"


# Progress callback shape — same contract as reconcile_pipeline's:
# (short message, current, total). ``total == 0`` means indeterminate.
ProgressCallback = Callable[[str, int, int], None]


# ── Public dataclasses ──────────────────────────────────────────


@dataclass
class OffloadConfig:
    """One offload run's inputs.

    ``source_dir`` is anywhere on disk — the engine doesn't care if
    it's a removable drive (that's task #6's job, consumed by the UI).

    ``event_root`` is the on-disk event root (``…/trips/<event>/``);
    the engine writes under its ``00 - Captured/``.

    ``camera_id`` is the user's identifier for the source device
    (``G9_mkII``, ``iPhone_13``, ``Hero11_Black``). It becomes a path
    segment, so the user's choice of name shapes the on-disk layout.

    ``bucket`` is one of the three ``CAPTURED_*_SUBDIR`` constants —
    routes phone vs camera vs other into the canonical sub-bucket.

    ``calibration`` — optional clock-correction (same machinery as
    reconcile). ``None`` = pass-through (raw camera time used for Day
    routing; correct for phones and for cameras with no calibration).

    ``day_by_number`` — maps the parsed plan's ``day_number → TripDay``
    for Day-folder routing. Pass ``None`` for the early-trip case
    where no plan exists yet; the engine writes a flat per-session
    layout under ``<bucket>/<camera_id>/offload_<TS>/``.

    ``collision`` — per-file collision policy (frozen 2026-05-19).
    ``UNIQUE`` is the safe default for offload (a previous backup of
    the same card-fill is preserved under " (2)"); ``OVERRIDE`` is the
    explicit user-confirmed re-offload of the same card.

    ``ran_at`` — when this offload started; becomes the session subdir
    name and lands in the manifest. Pass ``datetime.now()`` from the UI;
    tests pass a fixed value for reproducibility.
    """
    source_dir: Path
    event_root: Path
    camera_id: str
    bucket: str
    calibration: Optional[CameraCalibration] = None
    day_by_number: Optional[dict[int, TripDay]] = None
    collision: CollisionPolicy = CollisionPolicy.UNIQUE
    ran_at: datetime = field(default_factory=datetime.now)
    # Task #84 — Mode B (pre-cull during ingest): the Capture
    # orchestration script computes the kept-set via the standalone
    # culler and passes their filenames here. Only files whose
    # ``Path.name`` is in this set are copied; everything else is
    # skipped silently (the user already discarded them — they don't
    # belong in 00 - Captured). ``None`` (the default) preserves
    # legacy behaviour: copy everything found under ``source_dir``.
    included_names: Optional[frozenset[str]] = None


@dataclass
class OffloadFileRecord:
    """One file's manifest entry — what was copied and what hash it
    has. Serialised verbatim into ``.offload-manifest.json``; the
    integrity-verify pass re-hashes the dest and compares against
    ``sha256``.

    ``day_number == 0`` means the file was quarantined (no readable
    EXIF timestamp). ``day_number is None`` means Day routing was
    disabled (no plan yet) — the file landed in a flat per-session
    layout.
    """
    src: str            # absolute, for the user's audit trail
    dest: str           # absolute path inside 00-Captured
    sha256: str         # hex digest of the dest bytes
    bytes: int          # dest file size (== src size; byte-untouched)
    day_number: Optional[int] = None
    capture_time_raw: Optional[str] = None       # EXIF ISO string or None
    capture_time_corrected: Optional[str] = None  # calibrated, ISO or None


@dataclass
class OffloadManifest:
    """Side-car JSON for one offload session. Lives at
    ``<session_subdir>/.offload-manifest.json``. ``verify_offload``
    consumes this; downstream "Create from Past Photos" reads it to
    skip re-walking the source."""
    schema_version: int
    source_dir: str
    event_root: str
    camera_id: str
    bucket: str
    ran_at: str          # ISO 8601
    session_subdir_name: str
    files: list[OffloadFileRecord] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(f.bytes for f in self.files)

    @property
    def file_count(self) -> int:
        return len(self.files)


@dataclass
class OffloadWarning:
    severity: str        # "info" | "warning" | "error"
    message: str
    path: Optional[Path] = None


@dataclass
class OffloadResult:
    manifest: OffloadManifest
    session_subdir: Path        # the offload_<TS> root (one per bucket)
    quarantine_subdir: Optional[Path] = None  # if any file quarantined
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)
    warnings: list[OffloadWarning] = field(default_factory=list)

    @property
    def written_count(self) -> int:
        return len(self.manifest.files)


@dataclass
class VerifyResult:
    """Output of ``verify_offload``. ``ok`` is the per-file pass/fail;
    ``missing`` is files the manifest expected but the disk doesn't
    have (cosmic-ray case — manifest written, dest deleted). ``mismatch``
    is files whose re-hashed digest doesn't match the manifest. ``passed``
    is the gate the wipe-offer reads: only true when every file
    re-hashed cleanly."""
    ok: list[Path] = field(default_factory=list)
    missing: list[Path] = field(default_factory=list)
    mismatch: list[tuple[Path, str, str]] = field(default_factory=list)
    # (path, expected_sha256, actual_sha256)

    @property
    def passed(self) -> bool:
        return not self.missing and not self.mismatch


# ── Hashing + atomic copy ───────────────────────────────────────


def _hash_and_copy(src: Path, target: Path) -> tuple[str, int]:
    """Stream ``src`` → ``target`` while computing the SHA-256 digest
    of the bytes written, atomically. Mirrors ``cull_export._atomic_copy``
    (temp file in target dir → ``os.replace``) but folds the hash into
    the single read pass — a 50 MB RAW gets read once, not twice.

    Returns ``(sha256_hex, byte_count)``. ``mtime`` is preserved
    explicitly after the rename (we drop ``shutil.copy2`` here to keep
    the streaming loop simple; ``os.utime`` restores the source mtime
    on the dest)."""
    tmp = target.with_name(f".{target.name}.part-{os.getpid()}")
    h = hashlib.sha256()
    total = 0
    try:
        with src.open("rb") as fin, tmp.open("wb") as fout:
            while True:
                chunk = fin.read(_HASH_CHUNK)
                if not chunk:
                    break
                fout.write(chunk)
                h.update(chunk)
                total += len(chunk)
        os.replace(str(tmp), str(target))
        # Preserve source mtime so downstream tools (jf Folder
        # Publisher's "newest first" view) see what the camera wrote,
        # not the offload time. EXIF DateTimeOriginal is the SoT for
        # ordering — this is courtesy.
        try:
            st = src.stat()
            os.utime(str(target), (st.st_atime, st.st_mtime))
        except OSError:
            pass  # best-effort; the bytes are intact regardless
        return h.hexdigest(), total
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def hash_file(path: Path) -> tuple[str, int]:
    """Compute the SHA-256 + byte count of an existing file (no
    copy). Used by ``verify_offload`` to re-hash the destination side
    and compare against the manifest. Streaming — never loads the
    whole file."""
    h = hashlib.sha256()
    total = 0
    with path.open("rb") as f:
        while True:
            chunk = f.read(_HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
            total += len(chunk)
    return h.hexdigest(), total


def _unique_target(dest_dir: Path, name: str) -> Path:
    """Same shape as ``cull_export._unique_target``: first free
    ``stem (n).ext`` in ``dest_dir`` starting at n=2."""
    cand = dest_dir / name
    if not cand.exists():
        return cand
    p = Path(name)
    stem, suffix = p.stem, p.suffix
    n = 2
    while True:
        cand = dest_dir / f"{stem} ({n}){suffix}"
        if not cand.exists():
            return cand
        n += 1


# ── Walk + EXIF + day-pick helpers ──────────────────────────────


def _walk_media(source_dir: Path) -> list[Path]:
    """Recursively gather media files from ``source_dir``. Matches
    reconcile's extension set; dotfiles and unknown extensions are
    silently ignored."""
    return sorted(
        p for p in source_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in _MEDIA_EXTENSIONS
    )


def _pick_day_for_date(
    cal_date: date,
    day_by_number: dict[int, TripDay],
) -> Optional[int]:
    """Return the smallest ``day_number`` whose ``TripDay.date`` equals
    ``cal_date``, or ``None`` if no day matches. Same convention as
    reconcile's same-date duplicate handling."""
    candidates = sorted(
        n for n, d in day_by_number.items() if d.date == cal_date
    )
    return candidates[0] if candidates else None


def _session_subdir_name(ran_at: datetime) -> str:
    """``offload_20260520-143052`` — lexicographically sortable per
    Nelson's per-session-subdir spec."""
    return "offload_" + ran_at.strftime("%Y%m%d-%H%M%S")


# ── Manifest I/O ────────────────────────────────────────────────


def write_manifest(manifest: OffloadManifest, path: Path) -> None:
    """Persist a manifest. Atomic write-then-rename (CLAUDE.md
    invariant #7)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.part-{os.getpid()}")
    data = asdict(manifest)
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(str(tmp), str(path))


def read_manifest(path: Path) -> OffloadManifest:
    """Load a manifest from disk. Raises ``FileNotFoundError`` if the
    sidecar is missing, ``ValueError`` if the schema_version is from
    the future (engine doesn't know how to interpret it)."""
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    schema = int(data.get("schema_version", 0))
    if schema > MANIFEST_SCHEMA_VERSION:
        raise ValueError(
            f"manifest schema_version {schema} > engine's "
            f"{MANIFEST_SCHEMA_VERSION}; upgrade the app to read it"
        )
    files = [OffloadFileRecord(**r) for r in data.get("files", [])]
    return OffloadManifest(
        schema_version=schema,
        source_dir=data["source_dir"],
        event_root=data["event_root"],
        camera_id=data["camera_id"],
        bucket=data["bucket"],
        ran_at=data["ran_at"],
        session_subdir_name=data["session_subdir_name"],
        files=files,
    )


# ── Main entry: offload ─────────────────────────────────────────


def offload_to_captured(
    config: OffloadConfig,
    *,
    progress: Optional[ProgressCallback] = None,
) -> OffloadResult:
    """Execute one offload run.

    Walks ``config.source_dir``, EXIF-reads each file for Day routing,
    copies byte-untouched into the canonical ``00 - Captured/``
    layout with a per-session subdirectory, computes a SHA-256 digest
    per file (single-pass streaming hash-while-copy), and writes the
    side-car manifest.

    Returns an ``OffloadResult`` with the manifest, the session-subdir
    path, and per-file skip/error lists. The caller's next step is
    ``verify_offload(result.manifest)`` (the integrity-gate); after
    that returns ``VerifyResult(passed=True)`` it's safe to surface
    the wipe offer (only for removable sources — task #6).

    Sources are NEVER touched.
    """

    def _emit(msg: str, cur: int = 0, tot: int = 0) -> None:
        if progress is not None:
            progress(msg, cur, tot)

    _emit("Scanning source...")

    source = Path(config.source_dir)
    if not source.is_dir():
        raise FileNotFoundError(f"source_dir not found: {source}")

    media_files = _walk_media(source)
    # Task #84 — Mode B filter: keep only files whose basename is in
    # the included-names set. None means "no filter" (legacy default).
    if config.included_names is not None:
        before = len(media_files)
        media_files = [
            p for p in media_files if p.name in config.included_names
        ]
        _emit(
            f"Mode B filter: {len(media_files)} of {before} file(s) "
            f"kept by the user"
        )

    session_name = _session_subdir_name(config.ran_at)
    cap_root = captured_dir(Path(config.event_root))
    cap_root.mkdir(parents=True, exist_ok=True)

    manifest = OffloadManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        source_dir=str(source),
        event_root=str(config.event_root),
        camera_id=config.camera_id,
        bucket=config.bucket,
        ran_at=config.ran_at.isoformat(timespec="seconds"),
        session_subdir_name=session_name,
        files=[],
    )

    # The session_subdir on the result is the *bucket-level* session
    # root (one per offload), not the per-day descent — the UI uses
    # it to point at the just-written batch. Per-day descent lives
    # underneath when Day routing is on.
    bucket_session_root = (
        cap_root / config.bucket / config.camera_id / session_name
    )

    result = OffloadResult(
        manifest=manifest,
        session_subdir=bucket_session_root,
    )

    if not media_files:
        result.warnings.append(OffloadWarning(
            severity="info",
            message=f"no media files found under {source}",
        ))
        return result

    _emit(f"Reading EXIF for {len(media_files)} file(s)...")
    exif_entries = read_exif_batch(media_files)
    # Map path → timestamp (None if EXIF didn't surface one).
    time_by_path: dict[Path, Optional[datetime]] = {
        e.path: e.timestamp for e in exif_entries if e is not None
    }

    cal = config.calibration
    days = config.day_by_number  # None ⇒ flat layout

    total = len(media_files)
    _emit(f"Copying {total} file(s)...", 0, total)

    for idx, photo in enumerate(media_files, start=1):
        # Throttle progress emits — flooding the GUI signal queue
        # adds no info.
        if idx % 10 == 0 or idx == total:
            _emit(
                f"Copying {photo.name} ({idx}/{total})",
                idx, total,
            )

        cam_t = time_by_path.get(photo)
        corrected_t: Optional[datetime] = None
        day_num: Optional[int] = None

        # Quarantine if no EXIF timestamp — same contract as reconcile.
        if cam_t is None:
            quarantine_root = (
                cap_root
                / CAPTURED_NO_TIMESTAMP_SUBDIR
                / config.camera_id
                / session_name
            )
            quarantine_root.mkdir(parents=True, exist_ok=True)
            result.quarantine_subdir = quarantine_root
            # Mtime-prefix so name-sort approximates chronology even
            # though Day-bucketing is manual from here.
            try:
                mtime_dt = datetime.fromtimestamp(photo.stat().st_mtime)
                prefix = mtime_dt.strftime("%Y-%m-%d_%H-%M-%S")
                dest_name = f"{prefix}__{photo.name}"
            except OSError:
                dest_name = photo.name
            target = quarantine_root / dest_name
            if target.exists() and config.collision is CollisionPolicy.UNIQUE:
                target = _unique_target(quarantine_root, dest_name)
            try:
                sha256, n = _hash_and_copy(photo, target)
            except OSError as exc:
                log.warning("quarantine copy failed for %s: %s", photo, exc)
                result.errors.append((photo, str(exc)))
                continue
            manifest.files.append(OffloadFileRecord(
                src=str(photo),
                dest=str(target),
                sha256=sha256,
                bytes=n,
                day_number=0,                  # 0 == quarantined sentinel
                capture_time_raw=None,
                capture_time_corrected=None,
            ))
            continue

        # Apply calibration if we have one — shifts cam_t to trip-local
        # corrected time. ``has_any_source`` guard mirrors reconcile.
        if cal is not None and cal.has_any_source:
            corrected_t = cam_t + cal.offset_at(cam_t)
        else:
            corrected_t = cam_t

        # Decide the target directory: flat-session-layout (no plan)
        # vs Day-routed.
        if days is None:
            target_dir = bucket_session_root
        else:
            day_num = _pick_day_for_date(corrected_t.date(), days)
            if day_num is None:
                # Calibrated date doesn't match any Dia in the plan;
                # treat as quarantine-equivalent — drop into a
                # ``_unmatched`` sibling next to the per-day folders.
                target_dir = (
                    cap_root
                    / config.bucket
                    / "_unmatched"
                    / config.camera_id
                    / session_name
                )
                result.warnings.append(OffloadWarning(
                    severity="warning",
                    message=(
                        f"calibrated date {corrected_t.date()} doesn't "
                        f"match any plan day; routed to _unmatched"
                    ),
                    path=photo,
                ))
            else:
                day_folder = day_folder_name(days[day_num])
                target_dir = (
                    cap_root
                    / config.bucket
                    / day_folder
                    / config.camera_id
                    / session_name
                )

        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / photo.name
        if target.exists() and config.collision is CollisionPolicy.UNIQUE:
            target = _unique_target(target_dir, photo.name)

        try:
            sha256, n = _hash_and_copy(photo, target)
        except OSError as exc:
            log.warning("offload copy failed for %s: %s", photo, exc)
            result.errors.append((photo, str(exc)))
            continue

        manifest.files.append(OffloadFileRecord(
            src=str(photo),
            dest=str(target),
            sha256=sha256,
            bytes=n,
            day_number=day_num,
            capture_time_raw=cam_t.isoformat(timespec="seconds"),
            capture_time_corrected=(
                corrected_t.isoformat(timespec="seconds")
                if corrected_t is not None else None
            ),
        ))

    # Persist the manifest at the bucket-session root. (Even when all
    # files quarantined, write the manifest — empty ``files`` is a
    # valid result the UI can show.)
    bucket_session_root.mkdir(parents=True, exist_ok=True)
    write_manifest(manifest, bucket_session_root / MANIFEST_FILENAME)

    log.info(
        "offload complete: %d files, %d bytes, source=%s, session=%s",
        manifest.file_count, manifest.total_bytes, source, session_name,
    )

    return result


# ── Verify ──────────────────────────────────────────────────────


def verify_offload(
    manifest: OffloadManifest,
    *,
    progress: Optional[ProgressCallback] = None,
) -> VerifyResult:
    """Re-hash every file in the manifest and compare against the
    recorded SHA-256. The gate the live-trip "Back up this card" UI
    consults BEFORE offering the destructive wipe — CLAUDE.md
    invariant #9 in mechanism form.

    Returns a ``VerifyResult``. ``passed`` is the boolean the wipe
    surface honours: only when no file is missing or mismatched is
    the source-card delete permitted.
    """
    def _emit(msg: str, cur: int = 0, tot: int = 0) -> None:
        if progress is not None:
            progress(msg, cur, tot)

    result = VerifyResult()
    total = len(manifest.files)
    _emit(f"Verifying {total} file(s)...", 0, total)
    for idx, rec in enumerate(manifest.files, start=1):
        dest = Path(rec.dest)
        if idx % 10 == 0 or idx == total:
            _emit(f"Verifying {dest.name} ({idx}/{total})", idx, total)
        if not dest.is_file():
            result.missing.append(dest)
            continue
        try:
            actual, _n = hash_file(dest)
        except OSError as exc:
            log.warning("verify read failed for %s: %s", dest, exc)
            result.missing.append(dest)
            continue
        if actual != rec.sha256:
            result.mismatch.append((dest, rec.sha256, actual))
        else:
            result.ok.append(dest)
    return result
