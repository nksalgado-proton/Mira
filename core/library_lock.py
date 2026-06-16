"""Library single-writer lock (spec/76 §A).

Exactly one Mira process holds the library read-write; everyone else
opens read-only. The lock is filesystem-only — no sockets, no server,
no network calls — so it preserves charter invariant #3
(offline-first).

The advisory file lives at ``<library_root>/.mira-writer.lock`` and
carries a JSON payload with the holder's identity + a heartbeat. The
lock is library-wide, not per-``event.db``: one writer owns the whole
library for the session.

Why advisory + heartbeat instead of OS file locks: ``flock`` /
``LockFileEx`` semantics are unreliable over SMB / NFS, which is
exactly where the library runs in the multi-device home model
(spec/76 §A.2). An advisory file with a heartbeat is the portable
primitive that survives both local and network filesystems.

Clock-skew defence: staleness is judged against the lock file's
filesystem mtime — a single NAS clock shared by every reader — not
the in-file ``heartbeat_at`` string, which can disagree across hosts.

Pure logic + filesystem. No Qt imports.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

LOCK_FILENAME = ".mira-writer.lock"

# Heartbeat cadence and staleness window are intentionally generous
# — the lock has to ride out brief NAS hiccups without prematurely
# concluding the writer died. The values below match spec/76 §A.2.
HEARTBEAT_INTERVAL_SECONDS = 30
STALENESS_TIMEOUT_SECONDS = 5 * 60


@dataclass(frozen=True)
class LockInfo:
    """The lock file's identity, as seen by a reader.

    ``mtime`` is the filesystem mtime of the lock file itself — the
    one timestamp that doesn't lie when machines on a LAN disagree on
    wall-clock time. Staleness is judged against it.
    """
    hostname: str
    pid: int
    app_version: str
    acquired_at: str       # ISO-8601 UTC, writer's clock — informational
    heartbeat_at: str      # ISO-8601 UTC, writer's clock — informational
    mtime: float           # filesystem mtime (seconds since epoch)


@dataclass(frozen=True)
class LockResult:
    """Outcome of an :func:`acquire` call.

    ``acquired=True`` → caller owns the writer lock and should proceed
    read-write. ``holder`` is the just-written identity.

    ``acquired=False`` → another live writer owns it. ``holder``
    carries that writer's identity for the conflict UI (spec/76 §A.4).
    ``holder`` is ``None`` only when the write itself failed; the
    caller should not open read-write either way.
    """
    acquired: bool
    holder: Optional[LockInfo]


def _lock_path(root: Path) -> Path:
    return Path(root) / LOCK_FILENAME


def _now_unix() -> float:
    return time.time()


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _this_app_version() -> str:
    """Same metadata read About Mira uses; falls back to ``"dev"``
    when the package isn't installed."""
    try:
        return metadata.version("mira")
    except metadata.PackageNotFoundError:
        return "dev"


def _local_payload(*, acquired_at: Optional[str] = None) -> dict:
    """Identity payload for THIS process. Pass ``acquired_at`` through
    on heartbeat so the original acquisition time is preserved."""
    return {
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "app_version": _this_app_version(),
        "acquired_at": acquired_at or _now_utc_iso(),
        "heartbeat_at": _now_utc_iso(),
    }


def _atomic_write_lock(path: Path, payload: dict) -> None:
    """Write ``payload`` as JSON to ``path`` atomically (write-then-
    rename — invariant #6). Readers never see a half-written lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())
        except (OSError, AttributeError):
            # fsync isn't supported on every filesystem; failure here
            # is non-fatal because os.replace is still atomic at the
            # rename level.
            pass
    os.replace(str(tmp), str(path))


def read_holder(root: Path) -> Optional[LockInfo]:
    """Return the current lock holder's :class:`LockInfo`, or ``None``
    when no lock file exists or the file is corrupt / half-written.

    Corrupt files are logged and treated as absent — :func:`acquire`
    will then write a fresh lock over them, which matches the
    "treated as stale, not a crash" guarantee in spec/76 §A.5.
    """
    p = _lock_path(root)
    try:
        st = p.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.warning("library_lock: stat %s failed: %s", p, exc)
        return None
    try:
        text = p.read_text(encoding="utf-8")
        blob = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning(
            "library_lock: %s unreadable (%s); treating as absent.", p, exc,
        )
        return None
    if not isinstance(blob, dict):
        log.warning(
            "library_lock: %s payload is not an object; treating as absent.",
            p,
        )
        return None
    try:
        return LockInfo(
            hostname=str(blob.get("hostname", "")),
            pid=int(blob.get("pid", 0)),
            app_version=str(blob.get("app_version", "")),
            acquired_at=str(blob.get("acquired_at", "")),
            heartbeat_at=str(blob.get("heartbeat_at", "")),
            mtime=float(st.st_mtime),
        )
    except (TypeError, ValueError) as exc:
        log.warning(
            "library_lock: %s payload malformed (%s); treating as absent.",
            p, exc,
        )
        return None


def is_stale(
    info: LockInfo,
    *,
    now: Optional[float] = None,
    timeout: int = STALENESS_TIMEOUT_SECONDS,
) -> bool:
    """A lock is stale when its filesystem mtime is older than
    ``timeout`` seconds ago. Filesystem mtime is preferred over the
    in-file ``heartbeat_at`` because LAN machines may disagree on
    wall-clock time but they all see the same NAS clock for file
    metadata (spec/76 §A.2)."""
    current = now if now is not None else _now_unix()
    age = current - info.mtime
    return age > timeout


def acquire(root: Path) -> LockResult:
    """Try to acquire the library writer lock for THIS process.

    Outcomes:

    * ``acquired=True`` — no lock file existed, or the previous one
      was stale (writer crashed) and we took it over. Caller proceeds
      read-write.
    * ``acquired=False`` — another live writer owns it. ``holder``
      carries that writer's identity. Caller opens read-only
      (spec/76 §A.4) — never silently overrides.

    There is no fully race-free acquire over SMB/NFS — the spec
    accepts this and pushes the real defence to the conflict UI
    (the user is told which machine owns the lock).
    """
    p = _lock_path(root)
    holder = read_holder(p.parent)
    if holder is not None and not is_stale(holder):
        return LockResult(acquired=False, holder=holder)
    # Either no lock file at all, or stale → take it.
    try:
        _atomic_write_lock(p, _local_payload())
    except OSError as exc:
        log.warning("library_lock: failed to write lock at %s: %s", p, exc)
        return LockResult(acquired=False, holder=None)
    fresh = read_holder(p.parent)
    if fresh is None:
        log.warning("library_lock: wrote lock but couldn't re-read it.")
        return LockResult(acquired=False, holder=None)
    return LockResult(acquired=True, holder=fresh)


def refresh(root: Path) -> bool:
    """Update the heartbeat for the existing lock — call every
    ``HEARTBEAT_INTERVAL_SECONDS`` while holding.

    Returns ``False`` if the lock file no longer exists or has been
    taken over by another host / pid. The caller should drop back to
    read-only when ``refresh()`` returns ``False`` — the writer half
    of the lock has been lost.
    """
    p = _lock_path(root)
    current = read_holder(p.parent)
    if current is None:
        log.warning("library_lock: refresh found no lock file at %s.", p)
        return False
    if current.hostname != socket.gethostname() or current.pid != os.getpid():
        log.warning(
            "library_lock: refresh at %s — lock now held by %s pid %d "
            "(we are %s pid %d); dropping out.",
            p, current.hostname, current.pid,
            socket.gethostname(), os.getpid(),
        )
        return False
    try:
        _atomic_write_lock(p, _local_payload(acquired_at=current.acquired_at))
    except OSError as exc:
        log.warning("library_lock: refresh write failed at %s: %s", p, exc)
        return False
    return True


def release(root: Path) -> bool:
    """Delete the lock file on clean shutdown. Returns ``True`` when
    a file was removed; ``False`` when there was nothing to remove or
    the lock had already been taken over (in which case we leave it
    in place so the new owner isn't silently kicked out).
    """
    p = _lock_path(root)
    current = read_holder(p.parent)
    if current is None:
        return False
    if current.hostname != socket.gethostname() or current.pid != os.getpid():
        log.warning(
            "library_lock: release skipped at %s — lock now held by "
            "%s pid %d.", p, current.hostname, current.pid,
        )
        return False
    try:
        os.remove(str(p))
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.warning("library_lock: release failed at %s: %s", p, exc)
        return False


__all__ = [
    "HEARTBEAT_INTERVAL_SECONDS",
    "LOCK_FILENAME",
    "LockInfo",
    "LockResult",
    "STALENESS_TIMEOUT_SECONDS",
    "acquire",
    "is_stale",
    "read_holder",
    "refresh",
    "release",
]
