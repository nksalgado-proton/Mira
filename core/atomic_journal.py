"""Journal protection — Model 3 v2 companion (Nelson 2026-05-22).

Three-layer protection for the JSON journals that now act as the
source of truth for cull / select / curate decisions (docs/14
§"Journal protection"):

* **Layer 1** — atomic write-then-rename + last-N history rotation.
  Every save writes ``<path>.tmp``, fsyncs, ``os.replace``\\s onto
  the destination, AND copies the previous version into
  ``<path's parent>/.history/<stem>.<ISO-timestamp><suffix>``.
  History is kept to the most recent ``max_history`` versions
  (default 20). Most-common failure mode — "I did something
  destructive and want to roll back" — is handled here.

* **Layer 2** — SHA256 sidecar. Alongside ``<path>`` we maintain
  ``<path>.sha256`` carrying the hex digest of the file's bytes
  at the moment we wrote it. ``verify_sidecar(path)`` reads both
  and reports mismatch. Detects silent corruption (bit rot,
  accidental external edit).

* **Layer 3** — rebuild from filesystem. This module doesn't
  implement Layer 3 because it's journal-specific: the cull /
  select / curate engines own their own "walk the projection and
  reconstruct" logic, plumbed into the consistency-audit tool
  (docs/14 §"Housekeeping operations").

Qt-free. Pure stdlib. The helper is **opt-in per journal**: each
journal writer chooses to use it. Callers that don't migrate keep
their existing write logic.
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


# History rotation cap. Trade-off: more = safer rollback window;
# fewer = less disk + cleaner ``.history/`` listing. 20 covers a
# week of heavy cull edits without growing past a few hundred KB
# per phase. Easy to tune — the helper takes ``max_history`` as a
# parameter so future per-journal tuning is local.
DEFAULT_MAX_HISTORY = 20

# Sidecar suffix. Conventional `.sha256` is the industry standard
# for SHA256-of-file integrity sidecars; using the standard means
# external tools (e.g. ``sha256sum -c``) can verify too.
SHA256_SIDECAR_SUFFIX = ".sha256"

# History dir name. Hidden (leading dot on POSIX; Windows doesn't
# hide automatically but the convention reads as "internal").
HISTORY_DIR_NAME = ".history"


# ── Sha256 ─────────────────────────────────────────────────────


def _sha256_hex(data: bytes) -> str:
    """Hex digest of ``data`` — exactly the format the standard
    ``sha256sum`` tool emits, so a sidecar Mira writes can be
    verified by external tools (and vice-versa)."""
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _read_file_sha256(path: Path) -> str:
    """SHA256 of the file at ``path``, streamed in 1MB chunks.
    Used to verify the live file against its sidecar."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _write_sidecar(path: Path, sha_hex: str) -> None:
    """Sidecar file lives next to ``path`` and carries
    ``<sha256>  <basename>\\n`` — the standard ``sha256sum`` line
    format. Use the standard format so external tools can verify."""
    sidecar = path.with_suffix(path.suffix + SHA256_SIDECAR_SUFFIX)
    line = f"{sha_hex}  {path.name}\n"
    tmp = sidecar.with_suffix(sidecar.suffix + ".tmp")
    tmp.write_text(line, encoding="utf-8")
    os.replace(str(tmp), str(sidecar))


# ── History ────────────────────────────────────────────────────


def _history_dir_for(path: Path) -> Path:
    """The hidden history dir under ``path``'s parent. Created on
    demand; the parent must exist (caller-guaranteed)."""
    return path.parent / HISTORY_DIR_NAME


def _save_history_copy(
    path: Path,
    *,
    max_history: int = DEFAULT_MAX_HISTORY,
) -> Optional[Path]:
    """Copy the CURRENT contents of ``path`` (the pre-overwrite
    version) into the history dir, with a timestamped name. Prunes
    excess entries to keep only the most recent ``max_history``
    versions of THIS file. Returns the new history path, or
    ``None`` when there was nothing to archive (e.g. first write).
    """
    if not path.is_file():
        return None
    hist_dir = _history_dir_for(path)
    hist_dir.mkdir(parents=True, exist_ok=True)
    # ISO timestamp with microsecond precision so concurrent or
    # near-simultaneous writes get distinct history filenames.
    ts = datetime.now().strftime("%Y%m%dT%H%M%S_%f")
    hist_name = f"{path.stem}.{ts}{path.suffix}"
    hist_path = hist_dir / hist_name
    try:
        hist_path.write_bytes(path.read_bytes())
    except OSError as exc:
        log.warning(
            "history copy failed for %s → %s: %s",
            path, hist_path, exc,
        )
        return None
    _prune_history(path, max_history=max_history)
    return hist_path


def _prune_history(
    path: Path,
    *,
    max_history: int = DEFAULT_MAX_HISTORY,
) -> None:
    """Trim ``<path>``'s history dir to the most recent
    ``max_history`` entries (sorted by filename, which embeds the
    timestamp so lexicographic = chronological)."""
    hist_dir = _history_dir_for(path)
    if not hist_dir.is_dir():
        return
    prefix = f"{path.stem}."
    candidates = sorted(
        (p for p in hist_dir.iterdir() if p.name.startswith(prefix)),
        key=lambda p: p.name,
    )
    overflow = len(candidates) - max_history
    if overflow <= 0:
        return
    for old in candidates[:overflow]:
        try:
            old.unlink()
        except OSError as exc:
            log.warning(
                "history prune failed for %s: %s", old, exc,
            )


def list_history(path: Path) -> list[Path]:
    """Most-recent-first list of all preserved versions of
    ``path``. Used by the consistency-audit tool's "restore from
    history" dialog."""
    hist_dir = _history_dir_for(path)
    if not hist_dir.is_dir():
        return []
    prefix = f"{path.stem}."
    return sorted(
        (p for p in hist_dir.iterdir() if p.name.startswith(prefix)),
        key=lambda p: p.name,
        reverse=True,
    )


# ── Top-level write ────────────────────────────────────────────


@dataclass(frozen=True)
class WriteOutcome:
    """Audit data returned by :func:`write_with_protection`. Tests
    can assert on the fields; production code can pass it to a
    progress callback or log line."""
    path: Path
    sha256: str
    history_path: Optional[Path]


def write_with_protection(
    path: Path,
    data: Any,
    *,
    max_history: int = DEFAULT_MAX_HISTORY,
    indent: Optional[int] = 2,
) -> WriteOutcome:
    """Write ``data`` (any JSON-serialisable value) to ``path`` with
    all three protections:

    1. Save a history copy of the EXISTING ``path`` (if any) into
       ``<parent>/.history/<stem>.<ISO-timestamp><suffix>``;
       prune to the most recent ``max_history`` entries.
    2. Write ``data`` as JSON to ``<path>.tmp``, fsync, then
       ``os.replace`` onto ``path`` — atomic.
    3. Compute SHA256 of the new bytes and write the sidecar at
       ``<path>.sha256`` in the standard ``sha256sum`` format.

    The history copy happens BEFORE the new write so a crash during
    write doesn't lose the prior good state. The sidecar write is
    last — if it fails, the journal itself is still valid (just
    unverifiable until the next successful write).

    Returns a :class:`WriteOutcome` carrying the SHA256 + the
    history path (or ``None`` if this was the first write).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Layer 1a — preserve the existing version.
    history_path = _save_history_copy(path, max_history=max_history)

    # Layer 1b — atomic write of the new bytes.
    payload = json.dumps(data, indent=indent, ensure_ascii=False).encode("utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(payload)
        f.flush()
        try:
            os.fsync(f.fileno())
        except (OSError, AttributeError):
            # fsync isn't supported on every filesystem; failure
            # here is non-fatal — the os.replace below is still
            # atomic at the rename level.
            pass
    os.replace(str(tmp), str(path))

    # Layer 2 — sidecar carrying the digest of what we just wrote.
    sha_hex = _sha256_hex(payload)
    try:
        _write_sidecar(path, sha_hex)
    except OSError as exc:
        log.warning(
            "sidecar write failed for %s: %s (journal is still valid)",
            path, exc,
        )

    return WriteOutcome(
        path=path,
        sha256=sha_hex,
        history_path=history_path,
    )


# ── Verification ───────────────────────────────────────────────


@dataclass(frozen=True)
class VerifyOutcome:
    """Result of :func:`verify_sidecar`.

    ``valid``: True when the sidecar exists AND matches the
        live file's hash.
    ``sidecar_missing``: True when the sidecar isn't there at all
        (this is a "we can't tell" state, NOT a corruption signal —
        legacy journals from before this helper was used have no
        sidecar).
    ``actual_sha256``: hash of the live file.
    ``expected_sha256``: the value from the sidecar (empty when
        ``sidecar_missing``).
    """
    valid: bool
    sidecar_missing: bool
    actual_sha256: str
    expected_sha256: str


def verify_sidecar(path: Path) -> VerifyOutcome:
    """Compare the live file at ``path`` against its
    ``<path>.sha256`` sidecar. Used on every journal load — a
    mismatch surfaces a recovery dialog (the consistency-audit
    tool's offer-of-history). A missing sidecar is NOT an error —
    legacy journals (pre-Model-3-v2) have none.

    Cheap: streams the file in 1MB chunks; for a typical
    sub-megabyte journal the verification is <10ms.
    """
    path = Path(path)
    sidecar = path.with_suffix(path.suffix + SHA256_SIDECAR_SUFFIX)
    if not sidecar.is_file():
        return VerifyOutcome(
            valid=False, sidecar_missing=True,
            actual_sha256="", expected_sha256="",
        )
    try:
        sidecar_text = sidecar.read_text(encoding="utf-8").strip()
        # sha256sum format: "<hex>  <basename>". Tolerate just <hex>
        # too in case some other tool wrote it.
        expected = sidecar_text.split(maxsplit=1)[0]
    except (OSError, IndexError) as exc:
        log.warning(
            "Unparseable sidecar %s: %s", sidecar, exc,
        )
        return VerifyOutcome(
            valid=False, sidecar_missing=True,
            actual_sha256="", expected_sha256="",
        )
    if not path.is_file():
        return VerifyOutcome(
            valid=False, sidecar_missing=False,
            actual_sha256="", expected_sha256=expected,
        )
    actual = _read_file_sha256(path)
    return VerifyOutcome(
        valid=(actual == expected),
        sidecar_missing=False,
        actual_sha256=actual,
        expected_sha256=expected,
    )
