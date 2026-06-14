"""Ingest-cull session persistence — D1 resume/recovery support.

The Mode A culler (camera card / phone export → ``01 Captured/_<source>/``)
needs its own session journal — separate from
``core.cull_session._culler_session.json``, which lives inside the
destination day folder. The ingest cull happens BEFORE the user has
committed to a destination, so the journal can't live where the
existing one does.

**Storage layout (B-017, 2026-05-25 — supersedes the hashed
``<user_data_dir>/ingest_sessions/<key>.json`` layout):**

    <event_root>/.cull/<camera_id_safe>/<bucket_id>/ingest_journal.json

The journal lives inside the directory the caller passes as
``source_root``. That directory is conventionally the per-bucket
journal scope assembled by ``BucketCullShell._bucket_root()``
(``<event>/.cull/<safe-cam>/<bucket-id>/``). Putting the journal on
disk where the silent-sync engine and the cull dashboard already
look for it (both walk ``event_root/.cull/<cam>/**/ingest_journal.json``
via ``rglob``) closes a load-bearing architectural gap: before this
fix, per-day cull marks landed in ``user_data_dir`` and the dashboard
never found them, so the camera row stayed "Not culled" forever and
silent-sync hardlinked nothing into ``01 - Culled/``. Only the
"Keep all" path worked because it bypasses journals entirely.

The Standalone-Cull surface (``core/standalone_cull_copy.py``) passes
its own ``journal_root`` for the per-source scope; the layout rule is
the same — journal lives inside whatever directory you hand the API.

On commit, ``commit_ingest_cull`` in :mod:`core.cull_ingest` calls
:func:`mark_committed` to stamp ``committed_at`` for audit-trail use.

Pure-logic module — no Qt, no exiftool. Tests cover round-trip,
hash stability (the optional debug ``session_key`` field), and the
single-source resume API.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

# Re-exported so callers have one canonical place to import all
# journal-manipulation helpers from when they're working on an
# ingest session. The functions operate on the ``marks`` dict —
# they're storage-location-agnostic.
from core.cull_session import (
    current_index,
    is_kept,
    kept_count,
    mark_kept,
    set_current_index,
    unmark,
)
# 3-state model (E1, docs/18). The new culler imports the 3-state API
# from here so there is one canonical import surface for an ingest
# session — same convention as the 2-state re-exports above. The
# legacy 2-state helpers above stay until the legacy page is retired
# (E11); the new culler uses the 3-state names.
from core.cull_state import (
    STATE_CANDIDATE,
    STATE_DISCARDED,
    STATE_KEPT,
    cycle_state,
    default_state,
    get_state,
    kept_filenames,
    set_default_state,
    set_state,
    state_counts,
)


log = logging.getLogger(__name__)


# Journal file name inside whatever directory the caller treats as
# the journal scope. The cull dashboard + silent-sync engines walk
# ``<event_root>/.cull/<cam>/**/<this filename>`` so any change here
# must update both consumers (``core.cull_dashboard``,
# ``core.cull_phase_sync``) in the same commit.
INGEST_JOURNAL_FILENAME = "ingest_journal.json"

# Journal schema version — bump when the shape changes incompatibly.
INGEST_JOURNAL_VERSION = 1


# ── Path helpers ─────────────────────────────────────────────────


def _session_key(source_root: Path) -> str:
    """Stable hash from the source's absolute path.

    Kept as a debug aid stamped into the journal — the on-disk
    location is now derived directly from ``source_root`` (B-017),
    but the hashed key is still useful when grepping logs for "which
    cull session was this?". Lower-cases Windows drive letters so
    ``D:/foo`` and ``d:/foo`` hash the same.
    """
    abs_path = str(Path(source_root).resolve())
    if len(abs_path) >= 2 and abs_path[1] == ":":
        abs_path = abs_path[0].lower() + abs_path[1:]
    h = hashlib.sha1(abs_path.encode("utf-8")).hexdigest()
    return h[:16]


def journal_path_for(source_root: Path) -> Path:
    """Return the journal file path for a given source root.

    The journal lives **inside** ``source_root`` (B-017). The caller
    decides what that root is — the bucket-cull shell uses
    ``<event>/.cull/<safe-cam>/<bucket-id>/``, the standalone-cull
    surface uses its own per-source path. Either way, the silent-sync
    engine and the cull dashboard find the journal via the same
    ``rglob("ingest_journal.json")`` walk.
    """
    return Path(source_root) / INGEST_JOURNAL_FILENAME


# ── Journal lifecycle ────────────────────────────────────────────


def _empty_journal(source_root: Path) -> dict:
    """Skeleton journal — used when load() finds no file or a
    corrupted file."""
    return {
        "version": INGEST_JOURNAL_VERSION,
        "session_key": _session_key(source_root),
        "source_root": str(Path(source_root).resolve()),
        "marks": {},
        "current_index": 0,
        "committed_at": None,
        # Microsecond precision so two journals started in quick
        # succession sort deterministically.
        "created_at": datetime.now().isoformat(timespec="microseconds"),
    }


def load_ingest_journal(source_root: Path) -> dict:
    """Read the journal for an ingest source, returning a fresh
    skeleton when the file doesn't exist.

    Defensive: a malformed journal file is treated as "no journal";
    we log a warning and hand back a fresh skeleton rather than
    crashing the resume flow. Same recovery philosophy as
    ``core.settings.load_settings``.
    """
    path = journal_path_for(source_root)
    if not path.exists():
        return _empty_journal(source_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning(
            "Could not read ingest journal %s (%s); using fresh",
            path, exc,
        )
        return _empty_journal(source_root)
    if not isinstance(data, dict):
        log.warning(
            "Ingest journal %s is not a JSON object; using fresh", path,
        )
        return _empty_journal(source_root)
    # Fill in any missing keys from the skeleton
    skeleton = _empty_journal(source_root)
    for k, v in skeleton.items():
        data.setdefault(k, v)
    return data


def save_ingest_journal(source_root: Path, journal: dict) -> None:
    """Write an ingest journal with the full three-layer protection
    (B-009, 2026-05-25): atomic write + last-20 history rotation +
    SHA256 sidecar. Routes through
    ``core.atomic_journal.write_with_protection`` — same engine as
    cull + curate journals.

    Creates ``source_root`` if it does not exist. The bucket-cull
    shell hands us paths like ``<event>/.cull/<cam>/<bucket>/`` which
    are conjured on demand; the journal must materialise the
    directory before writing.
    """
    from core.atomic_journal import write_with_protection
    Path(source_root).mkdir(parents=True, exist_ok=True)
    path = journal_path_for(source_root)
    write_with_protection(path, journal)


def discard_ingest_journal(source_root: Path) -> bool:
    """Delete an uncommitted journal (user chose Discard on resume
    prompt). Never raises; returns True iff a file was removed."""
    path = journal_path_for(source_root)
    if not path.exists():
        return False
    try:
        path.unlink()
    except OSError as exc:
        log.warning(
            "Could not discard ingest journal %s: %s", path, exc,
        )
        return False
    log.info("Discarded ingest journal for %s", source_root)
    return True


def mark_committed(source_root: Path, journal: dict) -> dict:
    """Stamp ``committed_at`` on the journal and persist it.

    Called by ``core.cull_ingest.commit_ingest_cull`` after a
    successful commit. The journal stays on disk after commit so the
    audit trail isn't lost — the resume-discovery API filters it out
    via the ``committed_at`` field.

    Returns the (mutated) journal for chaining.
    """
    journal["committed_at"] = datetime.now().isoformat(
        timespec="microseconds",
    )
    save_ingest_journal(source_root, journal)
    return journal


# ── Resume discovery ─────────────────────────────────────────────


def has_pending_ingest(source_root: Path) -> bool:
    """True when an uncommitted journal exists for ``source_root``.

    Single-source check — caller already knows which source it cares
    about (the bucket-cull shell holds the journal_root for the
    bucket; the standalone-cull surface holds its own source path).

    The global "scan every event for pending journals" function that
    used to live here (``pending_ingest_sessions``) was removed in
    B-017 along with the hashed user_data_dir layout — journals now
    live distributed across each event's ``.cull/`` tree, and no
    production surface needs a cross-event scan.
    """
    journal = load_ingest_journal(source_root)
    return bool(journal.get("marks")) and not journal.get("committed_at")


__all__ = [
    "INGEST_JOURNAL_VERSION",
    "INGEST_JOURNAL_FILENAME",
    # Path helpers
    "journal_path_for",
    # Lifecycle
    "load_ingest_journal",
    "save_ingest_journal",
    "discard_ingest_journal",
    "mark_committed",
    # Resume discovery
    "has_pending_ingest",
    # Re-exports — legacy 2-state API (core.cull_session)
    "current_index",
    "is_kept",
    "kept_count",
    "mark_kept",
    "set_current_index",
    "unmark",
    # Re-exports — 3-state model (core.cull_state, E1)
    "STATE_DISCARDED",
    "STATE_CANDIDATE",
    "STATE_KEPT",
    "default_state",
    "set_default_state",
    "get_state",
    "set_state",
    "cycle_state",
    "state_counts",
    "kept_filenames",
]
