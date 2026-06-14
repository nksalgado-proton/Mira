"""User-store corruption protection (spec/53 §3.1).

Three layers, all built on top of :mod:`mira.protect`'s SHA-256 helpers:

* **SHA-256 sidecar** — recomputed on every clean close (after a WAL
  checkpoint flushes the journal into the DB), verified on open. Mismatch
  warns visibly; we do NOT auto-restore (tamper is rare for a personal tool,
  but visible is the point).
* **Rolling backups** — on every clean close, the live ``mira.db`` is
  copied to ``mira.db.bak.<N>`` for ``N ∈ 1..MAX_ROLLING_BACKUPS``;
  newest is ``.bak.1``, oldest is ``.bak.<MAX>``, anything beyond rotates out.
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
from dataclasses import dataclass
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

    # Step 3: copy the live DB into the freshly-vacated .bak.1 slot.
    newest = _backup_path(path, 1)
    try:
        shutil.copy2(str(path), str(newest))
    except OSError as exc:
        log.warning("could not write %s: %s", newest, exc)
        return None
    return newest


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
