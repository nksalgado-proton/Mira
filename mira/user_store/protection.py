"""User-store corruption protection (spec/53 §3.1).

Three layers, all built on top of :mod:`mira.protect`'s SHA-256 helpers:

* **SHA-256 sidecar** — recomputed on every clean close (after a WAL
  checkpoint flushes the journal into the DB), verified on open. Mismatch
  warns visibly; we do NOT auto-restore (tamper is rare for a personal tool,
  but visible is the point).
* **Rolling backups** — on every clean close, the live ``mira.db`` is
  copied to ``mira.db.bak.<N>`` for ``N ∈ 1..MAX_ROLLING_BACKUPS``;
  newest is ``.bak.1``, oldest is ``.bak.<MAX>``, anything beyond rotates
  out. The copy is made with SQLite's **online backup API and verified with
  ``integrity_check`` before it is kept** — never a raw ``shutil.copy`` of
  the live WAL file (a torn copy is what corrupted ``global_items`` on
  2026-06-18). A copy that fails verification is discarded, so the rolling
  set — and therefore the restore path — can only ever contain clean DBs.
* **PRAGMA integrity_check** — runs once on open before any read. The result
  string is surfaced; ``'ok'`` is healthy, anything else is a corruption
  signal that callers act on.

The threat model is disk corruption + crash-mid-write + the user opening the
file "to take a quick look" in a text editor. NOT crypto-level tamper-proofing
(spec/53 §3.1).
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from mira import protect

log = logging.getLogger(__name__)

#: Maximum number of rolling-backup files kept alongside ``mira.db``.
#: Newest is ``.bak.1``, oldest is ``.bak.MAX_ROLLING_BACKUPS``.
MAX_ROLLING_BACKUPS = 5


# --------------------------------------------------------------------------- #
# Sidecar verify on open
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class VerifyOutcome:
    """Result of sidecar verification at open time.

    * ``ok=True, sidecar_missing=False`` — sidecar present, hashes match.
    * ``ok=True, sidecar_missing=True`` — no sidecar exists yet (fresh DB or
      first launch under the new protection contract). Not corruption.
    * ``ok=False, sidecar_missing=False`` — sidecar present, hashes mismatch.
      The DB may have been edited outside Mira; surface this to the
      user (we do NOT auto-restore).
    """

    ok: bool
    sidecar_missing: bool
    actual_sha256: str
    expected_sha256: str


def verify_sidecar(path: Path) -> VerifyOutcome:
    """Verify ``mira.db`` against its ``.sha256`` sidecar without
    opening the SQLite connection. Wraps :func:`mira.protect.verify`
    with this module's typed outcome shape."""
    outcome = protect.verify(path)
    return VerifyOutcome(
        ok=outcome.valid or outcome.sidecar_missing,
        sidecar_missing=outcome.sidecar_missing,
        actual_sha256=outcome.actual_sha256,
        expected_sha256=outcome.expected_sha256,
    )


# --------------------------------------------------------------------------- #
# Sidecar recompute on close
# --------------------------------------------------------------------------- #


def recompute_sidecar(path: Path) -> str:
    """Recompute SHA-256 over the live ``mira.db`` and write the
    sidecar. Called after a clean close (after the WAL checkpoint flushes the
    journal into the main DB file, so the SHA captures every committed
    transaction)."""
    sha = protect._read_file_sha256(path)            # noqa: SLF001 — module-internal helper
    protect._write_sidecar(path, sha)                # noqa: SLF001 — module-internal helper
    return sha


# --------------------------------------------------------------------------- #
# Rolling backups
# --------------------------------------------------------------------------- #


def _backup_path(path: Path, n: int) -> Path:
    return path.with_suffix(path.suffix + f".bak.{n}")


def roll_backup(path: Path, *, max_backups: int = MAX_ROLLING_BACKUPS) -> Optional[Path]:
    """Rotate the rolling backups and copy the live DB to ``.bak.1``.

    Order of operations (newest-first naming, so ``.bak.1`` is always the most
    recent):

    1. Delete ``.bak.<max>`` if it exists (the about-to-be-overwritten oldest).
    2. Rename ``.bak.<i>`` → ``.bak.<i+1>`` for ``i`` from ``max-1`` down to 1.
    3. Copy the live DB to ``.bak.1``.

    Returns the new ``.bak.1`` path, or ``None`` if no copy was made (e.g.
    the live file doesn't exist yet on a fresh-create call).
    """
    if not path.is_file():
        return None

    # Step 1: drop the about-to-be-overwritten oldest.
    oldest = _backup_path(path, max_backups)
    if oldest.exists():
        try:
            oldest.unlink()
        except OSError as exc:
            log.warning("could not remove %s: %s", oldest, exc)

    # Step 2: shift every existing slot one position older.
    for i in range(max_backups - 1, 0, -1):
        src = _backup_path(path, i)
        dst = _backup_path(path, i + 1)
        if src.exists():
            try:
                os.replace(str(src), str(dst))
            except OSError as exc:
                log.warning("could not rotate %s -> %s: %s", src, dst, exc)

    # Step 3: copy the live DB into the freshly-vacated .bak.1 slot — via
    # the online backup API + integrity verify, NEVER a raw file copy
    # (2026-06-18 global_items corruption: a `shutil.copy2` of a live WAL
    # db with a second opener captured a torn image, which the auto-restore
    # then promoted to the live file).
    newest = _backup_path(path, 1)
    if not _backup_db(path, newest):
        return None
    return newest


def _backup_db(src_path: Path, dst_path: Path) -> bool:
    """Make a **consistent, verified** copy of a SQLite DB.

    The ONLY safe way to copy a WAL-mode database (which may still be open by
    a second connection — the ingest worker) is SQLite's online backup API:
    it serialises against concurrent writers and checkpoints. A plain
    ``shutil.copy`` of the main ``.db`` file can capture a half-checkpointed,
    internally inconsistent image — the 2026-06-18 ``global_items`` "Rowid
    out of order" corruption.

    Writes to a temp sibling, runs ``integrity_check`` on it, and only then
    atomically renames it into place. Returns ``True`` on a verified-clean
    copy; on ANY failure — including a copy that does not pass
    ``integrity_check`` — it leaves no partial file and returns ``False``, so
    a torn backup can never enter the rolling set (and so the restore path
    can never promote one back onto the live DB).
    """
    tmp = dst_path.with_suffix(dst_path.suffix + ".tmp")
    try:
        tmp.unlink()
    except OSError:
        pass
    src = dst = None
    try:
        src = sqlite3.connect(f"file:{src_path.as_posix()}?mode=ro", uri=True,
                              timeout=5)
        dst = sqlite3.connect(str(tmp))
        src.backup(dst)
    except sqlite3.Error as exc:
        log.warning("roll_backup: online backup of %s failed: %s",
                    src_path, exc)
        _quiet_close(dst, src)
        _quiet_unlink(tmp)
        return False
    finally:
        _quiet_close(dst, src)
    if not integrity_ok(tmp):
        log.error("roll_backup: fresh backup of %s did NOT pass "
                  "integrity_check — discarding (refusing to store a corrupt "
                  "backup)", src_path)
        _quiet_unlink(tmp)
        return False
    try:
        os.replace(str(tmp), str(dst_path))      # atomic finalise
    except OSError as exc:
        log.warning("roll_backup: could not finalise %s: %s", dst_path, exc)
        _quiet_unlink(tmp)
        return False
    return True


def _quiet_close(*conns) -> None:
    for c in conns:
        if c is not None:
            try:
                c.close()
            except sqlite3.Error:
                pass


def _quiet_unlink(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def list_backups(path: Path, *, max_backups: int = MAX_ROLLING_BACKUPS) -> list[Path]:
    """Newest-first list of existing rolling backups for ``path``. Used by
    the restore-from-backup path (spec/53 §3.4 / future slice) — the most
    recent good backup is the natural restore point on a failed open."""
    out: list[Path] = []
    for i in range(1, max_backups + 1):
        bak = _backup_path(path, i)
        if bak.is_file():
            out.append(bak)
    return out


# --------------------------------------------------------------------------- #
# Auto-restore on a failed integrity_check (2026-06-17 corruption incident)
# --------------------------------------------------------------------------- #


def integrity_ok(path: Path) -> bool:
    """``True`` if ``path`` opens read-only and passes ``PRAGMA
    integrity_check``. Opened with ``immutable=1`` so the probe never writes
    a ``-wal``/``-shm`` sidecar against a backup we're only inspecting."""
    if not path.is_file():
        return False
    try:
        uri = f"file:{path.as_posix()}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error:
        return False
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return bool(row) and str(row[0]).strip().lower() == "ok"
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def restore_from_backup(
    path: Path, *, max_backups: int = MAX_ROLLING_BACKUPS,
) -> Optional[Path]:
    """Restore ``path`` from the newest rolling backup that passes
    ``integrity_check``.

    The corrupt live DB is moved aside to ``<path>.corrupt-<ts>`` (kept for
    forensics, never deleted), its stale ``-wal``/``-shm`` sidecars are
    removed (they belong to the corrupt file and would re-corrupt the
    restored copy on next open), the clean backup is copied into place, and
    the now-mismatched ``.sha256`` sidecar is dropped so the next clean
    close recomputes it.

    Returns the backup used, or ``None`` when no clean backup exists (the
    caller then has to decide whether to open the corrupt file or bail)."""
    for bak in list_backups(path, max_backups=max_backups):  # newest-first
        if not integrity_ok(bak):
            log.warning("restore: backup %s also fails integrity_check; "
                        "trying older", bak.name)
            continue
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        corrupt = path.with_suffix(path.suffix + f".corrupt-{ts}")
        try:
            if path.exists():
                os.replace(str(path), str(corrupt))
                log.warning("restore: moved corrupt DB aside to %s",
                            corrupt.name)
        except OSError as exc:
            log.error("restore: could not move corrupt DB aside: %s", exc)
            return None
        # Drop the corrupt file's WAL/SHM sidecars — applying them to the
        # restored copy on next open would re-introduce the corruption.
        for side in (f"{path}-wal", f"{path}-shm"):
            try:
                Path(side).unlink()
            except OSError:
                pass
        try:
            shutil.copy2(str(bak), str(path))
        except OSError as exc:
            log.error("restore: copy of %s -> %s failed: %s", bak, path, exc)
            return None
        # The hash sidecar no longer matches; remove it so open() doesn't
        # warn and the next clean close recomputes it.
        try:
            protect._sidecar_path(path).unlink()        # noqa: SLF001
        except OSError:
            pass
        log.warning("restore: %s restored from backup %s", path.name, bak.name)
        return bak
    log.error("restore: no clean rolling backup found for %s", path.name)
    return None
