"""The one JSON-domain protection contract (spec/02 §1, spec/04 §3).

Ported clean from the one genuinely good part of the legacy persistence
(``core/atomic_journal.py``) into the ``mira/`` namespace so it survives the
legacy archive (charter §4 step 8). Three layers, shared by every JSON-backed
domain repo (settings now; user-knowledge / rules / tone-corpus later):

* **Layer 1 — history rotation.** Before overwrite, copy the current file into
  ``<parent>/.history/<stem>.<ISO-ts><suffix>``; prune to the most recent
  ``max_history`` (default 20).
* **Layer 2 — atomic write-then-rename.** Write ``<path>.tmp``, fsync, ``os.replace``.
* **Layer 3 — SHA-256 sidecar.** ``<path>.sha256`` in the standard ``sha256sum`` line
  format; :func:`verify` reports match / mismatch / missing.

Pure stdlib, no Qt. Recovery *policy* (what to do on a mismatch) is the caller's —
this module only reports.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

DEFAULT_MAX_HISTORY = 20
SHA256_SIDECAR_SUFFIX = ".sha256"
HISTORY_DIR_NAME = ".history"


# --------------------------------------------------------------------------- #
# SHA-256
# --------------------------------------------------------------------------- #


def _sha256_hex(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _read_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _sidecar_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + SHA256_SIDECAR_SUFFIX)


def _write_sidecar(path: Path, sha_hex: str) -> None:
    """``<sha256>  <basename>`` — standard ``sha256sum`` format, so external
    tools can verify a sidecar Mira wrote (and vice-versa)."""
    sidecar = _sidecar_path(path)
    line = f"{sha_hex}  {path.name}\n"
    tmp = sidecar.with_suffix(sidecar.suffix + ".tmp")
    tmp.write_text(line, encoding="utf-8")
    os.replace(str(tmp), str(sidecar))


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #


def _history_dir_for(path: Path) -> Path:
    return path.parent / HISTORY_DIR_NAME


def _save_history_copy(path: Path, *, max_history: int) -> Optional[Path]:
    """Copy the pre-overwrite version of ``path`` into the history dir with a
    timestamped name; prune to the most recent ``max_history``. ``None`` on the
    first write (nothing to archive)."""
    if not path.is_file():
        return None
    hist_dir = _history_dir_for(path)
    hist_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S_%f")
    hist_path = hist_dir / f"{path.stem}.{ts}{path.suffix}"
    try:
        hist_path.write_bytes(path.read_bytes())
    except OSError as exc:
        log.warning("history copy failed for %s -> %s: %s", path, hist_path, exc)
        return None
    _prune_history(path, max_history=max_history)
    return hist_path


def _prune_history(path: Path, *, max_history: int) -> None:
    hist_dir = _history_dir_for(path)
    if not hist_dir.is_dir():
        return
    prefix = f"{path.stem}."
    candidates = sorted(
        (p for p in hist_dir.iterdir() if p.name.startswith(prefix)),
        key=lambda p: p.name,
    )
    overflow = len(candidates) - max_history
    for old in candidates[: max(0, overflow)]:
        try:
            old.unlink()
        except OSError as exc:
            log.warning("history prune failed for %s: %s", old, exc)


def list_history(path: Path) -> list[Path]:
    """Most-recent-first list of preserved versions of ``path``."""
    hist_dir = _history_dir_for(Path(path))
    if not hist_dir.is_dir():
        return []
    prefix = f"{Path(path).stem}."
    return sorted(
        (p for p in hist_dir.iterdir() if p.name.startswith(prefix)),
        key=lambda p: p.name,
        reverse=True,
    )


# --------------------------------------------------------------------------- #
# Write
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WriteOutcome:
    path: Path
    sha256: str
    history_path: Optional[Path]


def write_protected(
    path: Path,
    data: Any,
    *,
    max_history: int = DEFAULT_MAX_HISTORY,
    indent: Optional[int] = 2,
) -> WriteOutcome:
    """Write JSON-serialisable ``data`` to ``path`` with all three layers. The
    history copy happens before the new write so a crash mid-write cannot lose the
    prior good state; the sidecar is written last (its failure leaves a valid but
    momentarily-unverifiable file)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    history_path = _save_history_copy(path, max_history=max_history)

    payload = json.dumps(data, indent=indent, ensure_ascii=False).encode("utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(payload)
        f.flush()
        try:
            os.fsync(f.fileno())
        except (OSError, AttributeError):
            pass  # not every filesystem supports fsync; os.replace is still atomic
    os.replace(str(tmp), str(path))

    sha_hex = _sha256_hex(payload)
    try:
        _write_sidecar(path, sha_hex)
    except OSError as exc:
        log.warning("sidecar write failed for %s: %s (file is still valid)", path, exc)

    return WriteOutcome(path=path, sha256=sha_hex, history_path=history_path)


# --------------------------------------------------------------------------- #
# Verify
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class VerifyOutcome:
    valid: bool
    sidecar_missing: bool
    actual_sha256: str
    expected_sha256: str


def verify(path: Path) -> VerifyOutcome:
    """Compare the live file at ``path`` against its ``.sha256`` sidecar. A missing
    sidecar is a "can't tell" state (``sidecar_missing=True``), not corruption."""
    path = Path(path)
    sidecar = _sidecar_path(path)
    if not sidecar.is_file():
        return VerifyOutcome(False, True, "", "")
    try:
        expected = sidecar.read_text(encoding="utf-8").strip().split(maxsplit=1)[0]
    except (OSError, IndexError) as exc:
        log.warning("unparseable sidecar %s: %s", sidecar, exc)
        return VerifyOutcome(False, True, "", "")
    if not path.is_file():
        return VerifyOutcome(False, False, "", expected)
    actual = _read_file_sha256(path)
    return VerifyOutcome(actual == expected, False, actual, expected)
