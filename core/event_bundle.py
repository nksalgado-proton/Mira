"""Event migration bundle — export half (spec/82 Part B).

Composes the two existing primitives:

* :func:`core.db_backup.snapshot` — the consistent DB snapshot via
  SQLite's online backup API. **Never copy the live WAL ``event.db``
  file directly** — see spec/79 §2.
* :func:`core.event_backup_card._hash_and_copy` — the atomic
  streaming copy + SHA-256 that the SD-card offload already trusts
  on RAWs in the tens of MB.

Result: a **self-contained directory bundle** at
``<dest>/<event-folder>/`` carrying every byte of the event tree
(``Original Media/``, ``Edited Media/``, ``Exported Media/``,
``Cuts/``, ``.cache/``), with the *snapshotted* DB in place of the
live one and a top-level ``mira-event.json`` manifest. Caches travel
so the destination installation browses instantly without rebuild
(spec/82 §B "Bundle contains everything, verbatim").

Atomic finalisation. The bundle is built in
``<dest>/<event-folder>.partial/``; only after verify passes does
it become ``<dest>/<event-folder>/`` via ``os.replace``. An
interrupted copy is never mistaken for a complete bundle (invariant
#6).

Pure logic + filesystem. No Qt; no network. The source event tree is
read-only on this side of the operation (invariant #7); the
destination tree is fresh.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from core import db_backup
from core.event_backup_card import _hash_and_copy, hash_file

log = logging.getLogger(__name__)


# ── Manifest shape ────────────────────────────────────────────────


# Manifest file name. Sits at the bundle root so a quick glance
# tells you "this is a Mira migration bundle".
MANIFEST_FILENAME = "mira-event.json"
# Folder name suffix while the bundle is mid-build; replaced atomically
# on verify-pass with the bare name. An interrupted run leaves
# ``<event>.partial/`` behind — never confused with a finished bundle.
PARTIAL_SUFFIX = ".partial"
# Manifest format version. Bumped if the manifest shape changes in a
# breaking way (e.g., a new required field). Importers refuse a
# manifest with a higher version than they understand.
MANIFEST_VERSION = 1


@dataclass(frozen=True)
class ManifestFile:
    """One row in the per-file SHA-256 list.

    ``relpath`` is POSIX-style relative to the bundle root (Windows
    backslashes are converted) so a Linux installation reading a
    Windows-exported bundle matches paths cleanly.
    """
    relpath: str
    sha256: str
    byte_size: int


@dataclass(frozen=True)
class BundleManifest:
    """The bundle's identity + every file's SHA-256.

    ``event_uuid`` is the cross-installation identity (spec/82 §B.3
    step 4 — the import's identity gate keys off this). ``event_name``
    is informational. ``schema_version`` lets the importer apply the
    version gate. ``created_at`` is when the export ran (UTC ISO).
    """
    manifest_version: int
    event_uuid: str
    event_name: str
    app_version: str
    schema_version: int
    created_at: str
    total_file_count: int
    total_bytes: int
    files: List[ManifestFile]

    def to_json(self) -> str:
        """Pretty JSON for the manifest sidecar."""
        return json.dumps({
            "manifest_version": self.manifest_version,
            "event_uuid": self.event_uuid,
            "event_name": self.event_name,
            "app_version": self.app_version,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "total_file_count": self.total_file_count,
            "total_bytes": self.total_bytes,
            "files": [asdict(f) for f in self.files],
        }, indent=2, ensure_ascii=False)


@dataclass(frozen=True)
class BundleResult:
    """Outcome of :func:`export_event` on success."""
    bundle_dir: Path
    manifest: BundleManifest


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of :func:`verify_bundle`.

    ``ok`` is False on any mismatch / missing file / unreadable
    manifest. ``missing`` and ``mismatch`` carry the per-file
    detail so the UI can surface "which one" rather than a binary
    pass/fail.
    """
    ok: bool
    missing: List[str] = field(default_factory=list)
    mismatch: List[str] = field(default_factory=list)
    error: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_event_identity(db_path: Path) -> Tuple[str, str]:
    """Return ``(uuid, name)`` from an event.db's ``event`` row, or
    ``("", "")`` if the row is missing.

    Both fields are informational for the manifest but the importer
    keys off ``uuid`` for the identity gate (spec/82 §B.3 step 4),
    so a missing row leaves the bundle obviously broken — caught by
    the verify pass.
    """
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return ("", "")
    try:
        try:
            row = conn.execute(
                "SELECT uuid, name FROM event WHERE id = 1"
            ).fetchone()
        except sqlite3.OperationalError:
            return ("", "")
        if row is None:
            return ("", "")
        return (str(row[0] or ""), str(row[1] or ""))
    finally:
        conn.close()


def _read_schema_version(db_path: Path) -> int:
    """Reuse ``db_backup``'s schema_version probe — the bundle ships
    a schema_version so the importer can apply the spec/82 §B.3
    step-3 version gate."""
    from core.db_backup import _read_schema_version as _probe
    sv = _probe(db_path)
    return int(sv) if sv is not None else 0


def _to_posix(rel: Path) -> str:
    """POSIX-style separator so a manifest written on Windows reads
    cleanly on Linux."""
    return rel.as_posix()


# SQLite's WAL sidecar files. These are transient (re-created on the
# next open) and CHANGE during a snapshot — the very thing we're
# trying to avoid copying as-is. Skip them on the walk so the bundle
# carries only the consistent online-backup ``event.db`` and the
# destination re-creates the sidecars on first open.
_WAL_SIDECAR_NAMES = {
    "event.db-shm",
    "event.db-wal",
    "event.db-journal",
}


def _walk_tree(root: Path) -> List[Path]:
    """Every regular file under ``root``, returned sorted by relative
    path. ``Path.rglob('*')`` yields directories too — filter them out
    explicitly so the manifest only contains real files. SQLite's
    WAL/SHM sidecars are skipped (see :data:`_WAL_SIDECAR_NAMES`).
    """
    out: List[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.name in _WAL_SIDECAR_NAMES:
            continue
        out.append(p)
    return out


# ── export_event ──────────────────────────────────────────────────


ProgressCallback = Callable[[str, int, int], None]


def _emit(progress: Optional[ProgressCallback], msg: str,
          cur: int = 0, tot: int = 0) -> None:
    if progress is not None:
        try:
            progress(msg, cur, tot)
        except Exception:                                  # noqa: BLE001
            pass


def export_event(
    event_root: Path,
    event_db_path: Path,
    dest_dir: Path,
    *,
    app_version: str = "",
    progress: Optional[ProgressCallback] = None,
    created_at: Optional[datetime] = None,
    verify_after_copy: bool = True,
) -> BundleResult:
    """Export ``event_root`` (+ a consistent snapshot of
    ``event_db_path``) as a self-contained migration bundle under
    ``dest_dir``.

    Steps (spec/82 §B.2):

    1. Build the partial dir ``<dest>/<event-folder>.partial/``.
    2. Stream every file from ``event_root`` into the partial dir,
       computing SHA-256 in the same pass (one read of every byte).
    3. Take a Part-A snapshot of ``event_db_path`` via
       :func:`db_backup.snapshot`; copy the snapshot bytes over the
       ``event.db`` we just copied in step 2 so the bundle carries a
       **consistent** db, never the live WAL state. Re-hash the
       replaced ``event.db`` so the manifest matches the bundle's
       on-disk bytes (not the source's WAL-state bytes).
    4. Write ``mira-event.json`` at the bundle root.
    5. (Optional) Re-hash every file against the manifest to confirm
       the copy survived. ``verify_after_copy=False`` skips this for
       very large events where the user accepts the risk.
    6. ``os.replace`` the ``.partial`` dir → final name — invariant
       #6's atomic finalisation. An interrupted copy is never
       mistaken for a complete bundle.

    The source ``event_root`` is read-only throughout (invariant #7).
    Raises on permission / IO failure / verify mismatch; never
    leaves a partial dir behind silently — leftover ``.partial/``
    after a raise tells the user the bundle didn't finish.
    """
    event_root = Path(event_root)
    event_db_path = Path(event_db_path)
    dest_dir = Path(dest_dir)
    when = created_at or datetime.now(timezone.utc)

    if not event_root.is_dir():
        raise FileNotFoundError(f"event_root {event_root} is not a directory")
    if not event_db_path.exists():
        raise FileNotFoundError(f"event_db_path {event_db_path} not found")

    dest_dir.mkdir(parents=True, exist_ok=True)
    bundle_name = event_root.name
    partial_dir = dest_dir / f"{bundle_name}{PARTIAL_SUFFIX}"
    final_dir = dest_dir / bundle_name

    if final_dir.exists():
        raise FileExistsError(
            f"refusing to overwrite an existing bundle at {final_dir}; "
            "remove or rename it first")
    if partial_dir.exists():
        log.warning(
            "event_bundle: stale partial dir at %s — wiping it first",
            partial_dir)
        shutil.rmtree(partial_dir)

    # Step 1-2: copy the tree verbatim, hashing as we go.
    sources = _walk_tree(event_root)
    file_records: List[ManifestFile] = []
    total_bytes = 0
    _emit(progress, "Copying files", 0, len(sources))
    for i, src in enumerate(sources):
        rel = src.relative_to(event_root)
        target = partial_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        sha, size = _hash_and_copy(src, target)
        file_records.append(ManifestFile(
            relpath=_to_posix(rel), sha256=sha, byte_size=size))
        total_bytes += size
        _emit(progress, f"Copying {rel}", i + 1, len(sources))

    # Step 3: replace the copied event.db with a consistent snapshot.
    # The verbatim copy in step 2 grabbed whatever bytes happened to be
    # in event.db at copy time — if the user had it open in WAL mode
    # that's potentially inconsistent. The snapshot via the online
    # backup API is the safe replacement.
    bundle_db = partial_dir / "event.db"
    if bundle_db.exists():
        bundle_db.unlink()
    # ``db_backup.snapshot`` writes ``<UTC-ts>.db`` into a directory; we
    # want a single named file at bundle_db. Run the snapshot through a
    # scratch dir inside the bundle, then move it into place.
    scratch_dir = partial_dir / ".snapshot-scratch"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    try:
        snap_path = db_backup.snapshot(
            event_db_path, scratch_dir,
            reason=db_backup.REASON_MILESTONE,
            app_version=app_version,
            created_at=when,
        )
        os.replace(str(snap_path), str(bundle_db))
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)
    # Re-hash the swapped-in event.db so the manifest matches the
    # bundle's bytes (not the source's WAL state). Replace the existing
    # record in-place; preserve copy order.
    bundle_sha, bundle_size = hash_file(bundle_db)
    rewritten = []
    saw_db = False
    for r in file_records:
        if r.relpath == "event.db":
            rewritten.append(ManifestFile(
                relpath="event.db",
                sha256=bundle_sha, byte_size=bundle_size))
            saw_db = True
        else:
            rewritten.append(r)
    if not saw_db:
        # An event_root without event.db at the time of walk — the
        # snapshot replaced it from outside the tree, so add the row.
        rewritten.append(ManifestFile(
            relpath="event.db", sha256=bundle_sha, byte_size=bundle_size))
    file_records = rewritten
    # total_bytes already accounted for event.db at its source size;
    # adjust to the snapshot's size.
    total_bytes = sum(r.byte_size for r in file_records)

    # Step 4: write the manifest.
    event_uuid, event_name = _read_event_identity(bundle_db)
    schema_version = _read_schema_version(bundle_db)
    manifest = BundleManifest(
        manifest_version=MANIFEST_VERSION,
        event_uuid=event_uuid,
        event_name=event_name,
        app_version=app_version,
        schema_version=schema_version,
        created_at=when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        total_file_count=len(file_records),
        total_bytes=total_bytes,
        files=file_records,
    )
    manifest_path = partial_dir / MANIFEST_FILENAME
    _write_manifest_atomically(manifest, manifest_path)

    # Step 5: verify.
    if verify_after_copy:
        _emit(progress, "Verifying copy")
        vr = verify_bundle(partial_dir)
        if not vr.ok:
            raise RuntimeError(
                f"bundle verify failed at {partial_dir}: "
                f"missing={vr.missing!r} mismatch={vr.mismatch!r} "
                f"error={vr.error!r}")

    # Step 6: atomic finalisation.
    os.replace(str(partial_dir), str(final_dir))
    _emit(progress, "Done")
    return BundleResult(bundle_dir=final_dir, manifest=manifest)


def _write_manifest_atomically(manifest: BundleManifest, path: Path) -> None:
    """Write ``mira-event.json`` via the project's atomic pattern
    (tmp file → ``os.replace``)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    blob = manifest.to_json().encode("utf-8")
    with open(tmp, "wb") as f:
        f.write(blob)
        f.flush()
        try:
            os.fsync(f.fileno())
        except (OSError, AttributeError):
            pass
    os.replace(str(tmp), str(path))


# ── read_manifest / verify_bundle ─────────────────────────────────


def read_manifest(bundle_dir: Path) -> BundleManifest:
    """Parse ``mira-event.json`` from ``bundle_dir``. Raises
    ``FileNotFoundError`` if the manifest is missing,
    ``ValueError`` if the payload doesn't match the expected shape
    (the importer must always see a fully-typed object)."""
    manifest_path = Path(bundle_dir) / MANIFEST_FILENAME
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"bundle manifest missing at {manifest_path}")
    blob = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(blob, dict):
        raise ValueError(
            f"bundle manifest at {manifest_path} is not a JSON object")
    try:
        files = [
            ManifestFile(
                relpath=str(f["relpath"]),
                sha256=str(f["sha256"]),
                byte_size=int(f["byte_size"]),
            )
            for f in blob.get("files", [])
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"bundle manifest at {manifest_path} has malformed files: "
            f"{exc}") from exc
    return BundleManifest(
        manifest_version=int(blob.get("manifest_version", 1)),
        event_uuid=str(blob.get("event_uuid", "")),
        event_name=str(blob.get("event_name", "")),
        app_version=str(blob.get("app_version", "")),
        schema_version=int(blob.get("schema_version", 0)),
        created_at=str(blob.get("created_at", "")),
        total_file_count=int(blob.get("total_file_count", len(files))),
        total_bytes=int(blob.get("total_bytes", 0)),
        files=files,
    )


def verify_bundle(bundle_dir: Path) -> VerifyResult:
    """Re-hash every file in ``bundle_dir`` against the manifest's
    SHA-256 list. Returns ``ok=True`` only when every file is present
    and matches. Missing files and SHA mismatches are surfaced
    separately so the UI can present "which file" rather than a bare
    pass/fail.

    Also runs :func:`db_backup.quick_check` against the bundled
    ``event.db`` — a manifest-clean bundle whose db is corrupt is
    still a bad bundle.
    """
    bundle_dir = Path(bundle_dir)
    try:
        manifest = read_manifest(bundle_dir)
    except (FileNotFoundError, ValueError) as exc:
        return VerifyResult(ok=False, error=str(exc))
    missing: List[str] = []
    mismatch: List[str] = []
    for f in manifest.files:
        p = bundle_dir / f.relpath
        if not p.exists():
            missing.append(f.relpath)
            continue
        try:
            actual_sha, actual_size = hash_file(p)
        except OSError as exc:
            return VerifyResult(
                ok=False, error=f"hash failed for {f.relpath}: {exc}")
        if actual_sha != f.sha256 or actual_size != f.byte_size:
            mismatch.append(f.relpath)
    if missing or mismatch:
        return VerifyResult(
            ok=False, missing=missing, mismatch=mismatch)
    # quick_check the bundled db — manifest can be clean while
    # SQLite says the page bytes don't add up to a valid db.
    bundle_db = bundle_dir / "event.db"
    if bundle_db.exists() and not db_backup.quick_check(bundle_db):
        return VerifyResult(
            ok=False, error=f"quick_check failed on bundled event.db")
    return VerifyResult(ok=True)


# ── Import (spec/82 §B.3) ─────────────────────────────────────────


# Version-gate verdicts surfaced by :func:`inspect_bundle`.
VERSION_OK = "ok"
VERSION_NEWER_THAN_LOCAL = "newer"     # bundle schema > local → refuse
VERSION_OLDER_THAN_LOCAL = "older"     # bundle schema < local → ok, copy + warn


@dataclass(frozen=True)
class ImportPlan:
    """Pre-flight read of a bundle: did the integrity gate pass,
    and how does its schema compare to this build's target?

    The caller — the Gateway — decides next steps from here:

    * ``integrity.ok=False`` → refuse (the bundle is tampered or
      corrupt).
    * ``version_status == "newer"`` → refuse (spec/82 §B.3 step 3
      — "Update Mira on this PC first").
    * ``version_status == "older"`` → install + warn (the next
      ``UserStore.open`` migrates).
    * ``version_status == "ok"`` → install.

    The identity gate (does this event_uuid already live in the
    library?) sits in the Gateway, not here — the library index
    lives in ``mira/`` and cannot be imported from ``core/``.
    """
    bundle_dir: Path
    manifest: BundleManifest
    integrity: VerifyResult
    version_status: str

    @property
    def can_proceed(self) -> bool:
        return self.integrity.ok and self.version_status != VERSION_NEWER_THAN_LOCAL


def inspect_bundle(
    bundle_dir: Path,
    *,
    target_schema_version: int,
) -> ImportPlan:
    """Read + verify a bundle, classify its schema version against
    the local target. No side effects — never writes a byte to
    disk. Used by the importer to gate the destructive step.

    Raises ``FileNotFoundError`` only when ``bundle_dir`` is not a
    directory at all; manifest-missing / SHA mismatches surface
    through the returned :class:`ImportPlan` so the UI can show
    "this isn't a Mira bundle" vs "the bundle is damaged" with the
    same code path.
    """
    bundle_dir = Path(bundle_dir)
    if not bundle_dir.is_dir():
        raise FileNotFoundError(f"bundle_dir {bundle_dir} not a directory")

    integrity = verify_bundle(bundle_dir)
    # We need the manifest regardless of integrity — even a bundle
    # whose files don't hash clean has a manifest the caller may
    # want to display (event_uuid for the dialog title, etc.). If
    # the manifest itself is missing / malformed, surface as an
    # ``empty`` manifest and let the caller key off
    # ``integrity.ok=False``.
    try:
        manifest = read_manifest(bundle_dir)
    except (FileNotFoundError, ValueError):
        manifest = BundleManifest(
            manifest_version=MANIFEST_VERSION,
            event_uuid="", event_name="",
            app_version="", schema_version=0,
            created_at="",
            total_file_count=0, total_bytes=0,
            files=[],
        )

    if manifest.schema_version > target_schema_version:
        version_status = VERSION_NEWER_THAN_LOCAL
    elif manifest.schema_version < target_schema_version:
        version_status = VERSION_OLDER_THAN_LOCAL
    else:
        version_status = VERSION_OK

    return ImportPlan(
        bundle_dir=bundle_dir,
        manifest=manifest,
        integrity=integrity,
        version_status=version_status,
    )


def install_bundle(
    plan: ImportPlan,
    library_base: Path,
    *,
    target_event_root: Optional[Path] = None,
) -> Path:
    """Copy ``plan.bundle_dir`` into the local library under
    ``library_base``. Returns the final event_root path.

    By default the destination directory name matches the bundle's
    folder name so a round-trip preserves the original event-folder
    name (spec/57). Pass ``target_event_root`` explicitly for the
    Replace path where the existing event may live at a different
    name than the bundle's.

    Refuses when :attr:`ImportPlan.can_proceed` is False — the caller
    must handle the rejection upstream. **Does not** consult the
    library index or register the new event: identity gate +
    register are the Gateway's job (the library index lives in
    ``mira/``, not ``core/``).

    Atomic finalisation: the copy lands at
    ``<library_base>/<name>.partial/`` and only becomes
    ``<target_event_root>`` on success via ``os.replace``. An
    interrupted copy leaves ``.partial/`` behind — never a half-
    finished event_root.
    """
    if not plan.can_proceed:
        raise RuntimeError(
            "refusing to install a bundle that failed pre-flight: "
            f"integrity_ok={plan.integrity.ok} "
            f"version_status={plan.version_status!r}")

    library_base = Path(library_base)
    library_base.mkdir(parents=True, exist_ok=True)

    final_dir = (
        Path(target_event_root)
        if target_event_root is not None
        else library_base / plan.bundle_dir.name
    )
    partial_dir = final_dir.with_name(f"{final_dir.name}{PARTIAL_SUFFIX}")

    if partial_dir.exists():
        log.warning(
            "event_bundle: stale partial dir at %s — wiping it first",
            partial_dir)
        shutil.rmtree(partial_dir)

    # Copy every file (the manifest already lists them) through the
    # same _hash_and_copy primitive the export uses. Streaming +
    # atomic per file; if any single file fails, the partial dir is
    # left behind for the user to retry.
    for f in plan.manifest.files:
        src = plan.bundle_dir / f.relpath
        target = partial_dir / f.relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        _hash_and_copy(src, target)

    # Also copy the manifest itself so the destination event_root
    # carries the provenance trail (event_uuid + bundle creation
    # time, schema_version at export). Useful for debugging future
    # version-gate misses.
    shutil.copy2(
        str(plan.bundle_dir / MANIFEST_FILENAME),
        str(partial_dir / MANIFEST_FILENAME))

    # If the target already exists (Replace path), it must be moved
    # aside FIRST — the caller is responsible for snapshotting it
    # before calling install_bundle. Wipe the leftover.
    if final_dir.exists():
        shutil.rmtree(final_dir)
    os.replace(str(partial_dir), str(final_dir))
    return final_dir


__all__ = [
    "BundleManifest",
    "BundleResult",
    "ImportPlan",
    "MANIFEST_FILENAME",
    "MANIFEST_VERSION",
    "ManifestFile",
    "PARTIAL_SUFFIX",
    "VERSION_NEWER_THAN_LOCAL",
    "VERSION_OK",
    "VERSION_OLDER_THAN_LOCAL",
    "VerifyResult",
    "export_event",
    "inspect_bundle",
    "install_bundle",
    "read_manifest",
    "verify_bundle",
]
