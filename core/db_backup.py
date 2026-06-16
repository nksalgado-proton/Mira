"""SQLite backup, integrity check & restore (spec/79 §7 — the
pre-freeze minimal subset).

Three primitives:

* :func:`snapshot` — point-in-time backup of an open / closed WAL
  database via SQLite's **online backup API** (a plain ``shutil.copy``
  of an open WAL db is unsafe), atomic write-then-rename
  (invariant #6), JSON side-car with ``app_version`` +
  ``schema_version`` + ``sha256`` + ``created_at``, and last-N
  rotation (suggest ``keep=5``).
* :func:`quick_check` / :func:`verify` — fast ``PRAGMA quick_check``
  on opening a db, and a fuller ``sha256`` + ``quick_check`` check on
  a snapshot before restore.
* :func:`restore` — atomic swap-in of a snapshot, after backing up
  the current (possibly corrupt) file so the restore is itself
  reversible.

Build-on, not reinvent: the atomic write-then-rename pattern is the
project's standard (:mod:`core.atomic_journal`); the SHA-256 +
verify pattern matches :mod:`core.event_backup_card`. Local-only,
no network (invariant #3), no Qt — `core/` reusable.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)


# Filename convention: ``<UTC-timestamp>.db`` where the timestamp is
# ``YYYYMMDDTHHMMSSfffZ`` (milliseconds, sortable lexicographically).
# Side-car: ``<UTC-timestamp>.json`` alongside.
SIDECAR_SUFFIX = ".json"
CORRUPT_PREFIX = "corrupt-"

# Filename regex; the millisecond field is optional so legacy / hand-
# placed snapshots without it still list cleanly.
_TS_RE = re.compile(r"^\d{8}T\d{6}(\d{3})?Z$")


@dataclass(frozen=True)
class SnapshotInfo:
    """Metadata for one snapshot. ``db_path`` and ``sidecar_path``
    are siblings under the same backups directory.

    The sha256 / created_at / version fields come from the side-car
    JSON; ``mtime`` is the filesystem mtime of the db (a tiebreaker
    when two snapshots share a sidecar timestamp, which shouldn't
    happen but is cheap to handle)."""
    db_path: Path
    sidecar_path: Path
    app_version: str
    schema_version: int
    sha256: str
    created_at: str

    @property
    def name(self) -> str:
        return self.db_path.stem


# ── Helpers ────────────────────────────────────────────────────────


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _format_ts(when: datetime) -> str:
    """``YYYYMMDDTHHMMSSfffZ`` — millisecond precision so two
    snapshots in the same second sort distinctly."""
    return when.strftime("%Y%m%dT%H%M%S") + f"{when.microsecond // 1000:03d}Z"


def _format_iso(when: datetime) -> str:
    """ISO-8601 UTC string for the side-car ``created_at``."""
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_replace(src: Path, dst: Path) -> None:
    """Move ``src`` over ``dst`` atomically — wraps ``os.replace``
    (works across filesystems on the same volume; the caller is
    responsible for choosing a same-volume temp path)."""
    os.replace(str(src), str(dst))


def _read_schema_version(db_path: Path) -> Optional[int]:
    """Read ``schema_info.schema_version`` (the Mira convention) if
    the table exists. Returns ``None`` for non-Mira sqlite files."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return None
    try:
        try:
            row = conn.execute(
                "SELECT schema_version FROM schema_info WHERE id = 1"
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        return int(row[0]) if row else None
    finally:
        conn.close()


# ── snapshot ───────────────────────────────────────────────────────


def snapshot(
    db_path: Path,
    backups_dir: Path,
    *,
    keep: int = 5,
    app_version: str = "",
    schema_version: Optional[int] = None,
    created_at: Optional[datetime] = None,
) -> Path:
    """Write a consistent point-in-time backup of ``db_path`` into
    ``backups_dir``. Returns the path to the new snapshot db.

    * Uses SQLite's online backup API — safe on an open WAL database.
    * Atomic write — the snapshot lands at its final name via
      ``os.replace`` from a sibling temp file (invariant #6).
    * Side-car JSON carries ``app_version`` / ``schema_version`` /
      ``sha256`` / ``created_at``.
    * After writing, prunes the oldest snapshots in ``backups_dir``
      so at most ``keep`` remain (1 = "always keep just the latest"
      = the "free space" pole; v1 defaults to 5 per spec/79 §2).

    ``schema_version`` is read from the source db's ``schema_info``
    table when not passed. ``app_version`` defaults to an empty
    string; the gateway passes the live build version.
    """
    db_path = Path(db_path)
    backups_dir = Path(backups_dir)
    backups_dir.mkdir(parents=True, exist_ok=True)

    when = created_at or _now_utc()
    ts = _format_ts(when)

    if schema_version is None:
        sv = _read_schema_version(db_path)
        schema_version = sv if sv is not None else 0

    final_db = backups_dir / f"{ts}.db"
    final_sidecar = backups_dir / f"{ts}{SIDECAR_SUFFIX}"
    tmp_db = backups_dir / f"{ts}.db.tmp"
    tmp_sidecar = backups_dir / f"{ts}{SIDECAR_SUFFIX}.tmp"

    # Online backup → temp file, then atomic rename.
    if tmp_db.exists():
        tmp_db.unlink()
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(tmp_db))
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    _atomic_replace(tmp_db, final_db)

    # SHA + sidecar AFTER the db is in place so the digest matches
    # what readers will see at the final path.
    sha = _sha256_file(final_db)
    payload = {
        "app_version": app_version,
        "schema_version": int(schema_version),
        "sha256": sha,
        "created_at": _format_iso(when),
    }
    blob = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    with open(tmp_sidecar, "wb") as f:
        f.write(blob)
        f.flush()
        try:
            os.fsync(f.fileno())
        except (OSError, AttributeError):
            pass
    _atomic_replace(tmp_sidecar, final_sidecar)

    _prune(backups_dir, keep=keep)
    return final_db


def _prune(backups_dir: Path, *, keep: int) -> None:
    """Drop oldest snapshots in ``backups_dir`` so at most ``keep``
    remain. Each snapshot = a ``.db`` file with a sortable
    timestamp stem; the matching sidecar is removed too. Files that
    don't match the snapshot naming convention are left alone."""
    if keep < 1:
        keep = 1
    snapshots = list_snapshots(backups_dir)
    # ``list_snapshots`` returns newest-first; keep the head, drop
    # the tail.
    for old in snapshots[keep:]:
        try:
            old.db_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("db_backup: prune failed for %s: %s", old.db_path, exc)
        try:
            old.sidecar_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning(
                "db_backup: prune failed for %s: %s", old.sidecar_path, exc)


# ── list / latest ──────────────────────────────────────────────────


def list_snapshots(backups_dir: Path) -> List[SnapshotInfo]:
    """Return the snapshots in ``backups_dir``, **newest first**.

    Skips orphan files — a ``.db`` without its sidecar, or a sidecar
    without its db — both are logged. Files outside the
    ``<UTC-timestamp>.db`` naming convention (e.g. ``corrupt-*.db``
    written by :func:`restore`) are ignored."""
    backups_dir = Path(backups_dir)
    if not backups_dir.exists():
        return []
    out: List[SnapshotInfo] = []
    for db_path in sorted(backups_dir.glob("*.db"), reverse=True):
        stem = db_path.stem
        if not _TS_RE.match(stem):
            continue
        sidecar_path = backups_dir / f"{stem}{SIDECAR_SUFFIX}"
        if not sidecar_path.exists():
            log.warning(
                "db_backup: snapshot %s has no sidecar; skipping.", db_path,
            )
            continue
        try:
            blob = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning(
                "db_backup: sidecar %s unreadable (%s); skipping.",
                sidecar_path, exc,
            )
            continue
        if not isinstance(blob, dict):
            log.warning(
                "db_backup: sidecar %s is not a JSON object; skipping.",
                sidecar_path,
            )
            continue
        try:
            out.append(SnapshotInfo(
                db_path=db_path,
                sidecar_path=sidecar_path,
                app_version=str(blob.get("app_version", "")),
                schema_version=int(blob.get("schema_version", 0)),
                sha256=str(blob.get("sha256", "")),
                created_at=str(blob.get("created_at", "")),
            ))
        except (TypeError, ValueError) as exc:
            log.warning(
                "db_backup: sidecar %s malformed (%s); skipping.",
                sidecar_path, exc,
            )
    return out


def latest_snapshot(backups_dir: Path) -> Optional[SnapshotInfo]:
    """The newest snapshot in ``backups_dir``, or ``None``."""
    snaps = list_snapshots(backups_dir)
    return snaps[0] if snaps else None


# ── verify / quick_check ───────────────────────────────────────────


def quick_check(db_path: Path) -> bool:
    """Run ``PRAGMA quick_check`` on ``db_path``.

    True iff the result is ``"ok"``. Used on open (cheap) to catch
    corruption before the gateway touches the db. The fuller
    ``PRAGMA integrity_check`` is a "verify library" action — out of
    the pre-freeze subset."""
    db_path = Path(db_path)
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.OperationalError as exc:
        log.warning("db_backup: quick_check open failed for %s: %s",
                    db_path, exc)
        return False
    try:
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
        except sqlite3.DatabaseError as exc:
            log.warning("db_backup: quick_check raised %s on %s", exc, db_path)
            return False
        return bool(row) and row[0] == "ok"
    finally:
        conn.close()


def verify(snapshot_info: SnapshotInfo) -> bool:
    """Re-hash ``snapshot_info.db_path`` and compare to the sidecar's
    ``sha256``; also run :func:`quick_check`. True iff both pass.

    Returns False on missing files, hash mismatch, or a failed
    integrity check — never raises for these cases."""
    if not snapshot_info.db_path.exists():
        log.warning(
            "db_backup: verify: snapshot db missing at %s",
            snapshot_info.db_path,
        )
        return False
    try:
        actual = _sha256_file(snapshot_info.db_path)
    except OSError as exc:
        log.warning(
            "db_backup: verify: hash read failed for %s: %s",
            snapshot_info.db_path, exc,
        )
        return False
    if actual != snapshot_info.sha256:
        log.warning(
            "db_backup: verify: sha mismatch for %s (expected %s, got %s)",
            snapshot_info.db_path, snapshot_info.sha256, actual,
        )
        return False
    return quick_check(snapshot_info.db_path)


# ── restore ────────────────────────────────────────────────────────


def restore(
    snapshot_info: SnapshotInfo,
    db_path: Path,
    *,
    corrupt_dir: Optional[Path] = None,
    created_at: Optional[datetime] = None,
) -> Optional[Path]:
    """Atomically swap ``snapshot_info``'s db into ``db_path``.

    The current ``db_path`` (the possibly-corrupt original) is
    renamed to ``<corrupt_dir>/corrupt-<ts>.db`` first so a restore
    is itself reversible. ``corrupt_dir`` defaults to the snapshot's
    backups directory.

    Validates the snapshot first (sha + quick_check) — raises
    :class:`ValueError` if validation fails. We never restore a
    known-bad snapshot over a possibly-recoverable original.

    Returns the path of the saved corrupt backup, or ``None`` if
    there was no existing file at ``db_path``.
    """
    db_path = Path(db_path)
    if not verify(snapshot_info):
        raise ValueError(
            f"refusing to restore unverified snapshot "
            f"{snapshot_info.db_path}"
        )

    corrupt_dir = Path(corrupt_dir) if corrupt_dir is not None \
        else snapshot_info.db_path.parent
    corrupt_dir.mkdir(parents=True, exist_ok=True)

    saved: Optional[Path] = None
    if db_path.exists():
        when = created_at or _now_utc()
        corrupt_path = corrupt_dir / f"{CORRUPT_PREFIX}{_format_ts(when)}.db"
        # Move (not copy) the corrupt original so the swap below is a
        # clean replace, not an overwrite-while-readers-attached race.
        shutil.move(str(db_path), str(corrupt_path))
        saved = corrupt_path

    # Copy the snapshot to a temp file beside the destination, then
    # os.replace it onto the final name — atomic. We copy rather than
    # move so the snapshot itself stays in the backups dir.
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(db_path.suffix + ".tmp")
    shutil.copyfile(str(snapshot_info.db_path), str(tmp))
    _atomic_replace(tmp, db_path)

    return saved


__all__ = [
    "CORRUPT_PREFIX",
    "SIDECAR_SUFFIX",
    "SnapshotInfo",
    "latest_snapshot",
    "list_snapshots",
    "quick_check",
    "restore",
    "snapshot",
    "verify",
]
