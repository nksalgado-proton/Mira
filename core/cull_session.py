"""Cull-session journal management — walking-skeleton step 8.

The journal lives at ``<event_root>/02 Selected/Dia N/_culler_session.json``
and was initialised by ``core.photo_import._ensure_culler_journal`` at
import time. Step 8 mutates it (per-photo Keep marks) and then
*commits* it (move non-kept photos to a discards subfolder, archive
the journal, update the event status).

Schema::

    {
        "version": 1,
        "day_number": 1,
        "buckets": ["Individual"],
        "marks": {
            "DSC_0001.RW2": "kept",
            "DSC_0003.RW2": "kept"
        },
        "current_index": 12,    // photo position the user was on
        "committed_at": null    // ISO datetime once commit_cull runs
    }

The schema is intentionally small. The two-pass Candidate → Kept
model (docs/15 §6.1) lands in Phase 5; for the skeleton only the
``kept`` state matters. ``current_index`` is the resume position
written by the culler on every navigation — step 9 reads it back
to restore view-state precisely (per docs/12 Principle 7).

The journal is the single source of truth for per-photo state inside
a bucket. Anything we render in the UI must derive from it.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core.models import Event, EventStatus, TripDay
from core.path_builder import culled_day_path, event_root_path
from core.photo_import import (
    BUCKET_INDIVIDUAL,
    CULLER_JOURNAL_NAME,
    CULLER_JOURNAL_VERSION,
    day_bucket_dir,
)


log = logging.getLogger(__name__)


# Subfolder created inside each bucket on commit. Discarded files
# land here; the user can review and permanently delete later.
DISCARDS_SUBDIR = "_discards"

# Settings key on Event.event_settings holding the list of day
# numbers whose cull has been committed. Used to advance event
# status from PLANNED to LAUNCHED.
CULLED_DAY_NUMBERS_KEY = "culled_day_numbers"


@dataclass
class CommitResult:
    """Outcome of a commit_cull call.

    ``kept`` is the number of files that survived in the bucket.
    ``discarded`` is the number moved to ``_discards/``. ``archive_path``
    is where the journal ended up.
    """

    kept: int
    discarded: int
    archive_path: Path


# ── Path helpers ─────────────────────────────────────────────────


def day_dir(event: Event, day: TripDay) -> Path:
    """``<event_root>/01 - Culled/Dia N[ - desc]/`` — the skeleton
    cull session lives where ``photo_import`` lands the day
    (Cull-phase tree, 2026-05-19 taxonomy; both retire at Stage E)."""
    root = event_root_path(event.photos_base_path, event)
    return culled_day_path(root, day)


def journal_path(event: Event, day: TripDay) -> Path:
    return day_dir(event, day) / CULLER_JOURNAL_NAME


# ── Journal read / write ─────────────────────────────────────────


def load_journal(event: Event, day: TripDay) -> dict:
    """Read the journal, returning a fresh skeleton when the file
    doesn't exist (e.g. importer hasn't run for this day yet)."""
    path = journal_path(event, day)
    if not path.exists():
        return _empty_journal(day.day_number)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read journal %s: %s; using fresh", path, exc)
        return _empty_journal(day.day_number)
    if not isinstance(data, dict):
        return _empty_journal(day.day_number)
    data.setdefault("version", CULLER_JOURNAL_VERSION)
    data.setdefault("day_number", day.day_number)
    data.setdefault("buckets", [BUCKET_INDIVIDUAL])
    data.setdefault("marks", {})
    data.setdefault("current_index", 0)
    data.setdefault("committed_at", None)
    return data


def save_journal(event: Event, day: TripDay, journal: dict) -> None:
    """Atomic write-then-rename of the journal.

    Nelson 2026-05-22 (Model 3 v2): writes through
    :func:`core.atomic_journal.write_with_protection` so each save
    rotates the previous version into ``.history/`` (keeping the
    last 20) AND writes a SHA256 sidecar. The journal IS the source
    of truth for cull decisions under Model 3 v2, so the extra
    safety matters."""
    from core.atomic_journal import write_with_protection

    path = journal_path(event, day)
    write_with_protection(path, journal)


def _empty_journal(day_number: int) -> dict:
    return {
        "version": CULLER_JOURNAL_VERSION,
        "day_number": day_number,
        "buckets": [BUCKET_INDIVIDUAL],
        "marks": {},
        "current_index": 0,
        "committed_at": None,
    }


def set_current_index(journal: dict, index: int) -> None:
    """Record the photo position the user is on. Called from the
    culler on every navigation so a crash mid-cull resumes precisely.
    """
    journal["current_index"] = max(0, int(index))


def current_index(journal: dict) -> int:
    """Return the saved photo position (0 if missing / malformed)."""
    value = journal.get("current_index", 0)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


# ── Mark mutations ───────────────────────────────────────────────


def mark_kept(journal: dict, filename: str) -> None:
    """Idempotent: marking an already-kept file is a no-op."""
    journal.setdefault("marks", {})[filename] = "kept"


def unmark(journal: dict, filename: str) -> None:
    """Idempotent: removing a non-existent mark is a no-op."""
    marks = journal.get("marks") or {}
    marks.pop(filename, None)
    journal["marks"] = marks


def is_kept(journal: dict, filename: str) -> bool:
    return (journal.get("marks") or {}).get(filename) == "kept"


def kept_count(journal: dict) -> int:
    return sum(
        1 for v in (journal.get("marks") or {}).values() if v == "kept"
    )


# ── Commit ───────────────────────────────────────────────────────


def commit_cull(
    event: Event,
    day: TripDay,
    journal: dict,
    *,
    bucket: str = BUCKET_INDIVIDUAL,
) -> CommitResult:
    """Move non-kept files to ``<bucket>/_discards/``, archive the
    journal, advance event status.

    Idempotent on the journal-archive side: if the file is already
    archived, this raises ``RuntimeError`` rather than silently
    re-committing (the caller shouldn't be in a state where it
    thinks an archived session is still open).

    The caller persists the event via ``save_event`` after this call;
    we mutate it in place but don't save here so the trip-dashboard
    refresh can batch with the cull result.
    """
    if journal.get("committed_at"):
        raise RuntimeError(
            f"cull already committed at {journal['committed_at']}; "
            "refusing to re-commit"
        )

    bucket_path = day_bucket_dir(event, day, bucket)
    if not bucket_path.exists():
        raise RuntimeError(
            f"bucket directory missing: {bucket_path}"
        )

    discards = bucket_path / DISCARDS_SUBDIR
    discards.mkdir(parents=True, exist_ok=True)

    kept = 0
    discarded = 0
    for entry in sorted(bucket_path.iterdir()):
        if not entry.is_file():
            continue  # skip nested dirs (e.g. _discards itself)
        if is_kept(journal, entry.name):
            kept += 1
            continue
        dest = discards / entry.name
        # Don't clobber an existing same-named file in _discards/
        # — append a numeric suffix if needed so re-cull doesn't lose
        # history.
        if dest.exists():
            dest = _unique_destination(dest)
        try:
            shutil.move(str(entry), str(dest))
            discarded += 1
        except OSError as exc:
            log.error(
                "Failed to move %s → %s: %s", entry, dest, exc,
            )

    # Microsecond precision so repeat commits on the same day don't
    # collide on the archive filename.
    journal["committed_at"] = datetime.now().isoformat(timespec="microseconds")
    save_journal(event, day, journal)

    archive_path = _archive_journal(event, day, journal["committed_at"])
    log.info(
        "Cull committed: %d kept, %d discarded; journal archived at %s",
        kept, discarded, archive_path,
    )

    _record_culled_day(event, day.day_number)
    if event.status == EventStatus.PLANNED:
        event.status = EventStatus.LAUNCHED

    return CommitResult(kept=kept, discarded=discarded, archive_path=archive_path)


def _archive_journal(
    event: Event, day: TripDay, committed_at: str,
) -> Path:
    """Rename the active journal to a timestamped archive name so
    step 9's Resume-prompt logic only fires for uncommitted sessions."""
    active = journal_path(event, day)
    if not active.exists():
        # Defensive: commit ran but the journal disappeared mid-flight.
        # Re-create the archive name and write a minimal stub there
        # so the audit trail isn't lost.
        archive = active.with_name(
            f"_culler_session.{_safe_ts(committed_at)}.archived.json"
        )
        archive.write_text(
            json.dumps({"committed_at": committed_at}, indent=2),
            encoding="utf-8",
        )
        return archive
    archive = active.with_name(
        f"_culler_session.{_safe_ts(committed_at)}.archived.json"
    )
    # Belt + braces: even with microsecond precision, give the path a
    # numeric suffix on the (vanishingly rare) collision rather than
    # raise FileExistsError.
    if archive.exists():
        archive = _unique_destination(archive)
    active.rename(archive)
    return archive


def _safe_ts(iso: str) -> str:
    """ISO timestamps contain ``:`` and ``.`` which complicate
    glob-matching. Strip the unfriendly characters while keeping the
    timestamp readable."""
    return (
        iso.replace(":", "")
        .replace("-", "")
        .replace("T", "_")
        .replace(".", "_")
    )


def _unique_destination(dest: Path) -> Path:
    """Return ``dest`` with a numeric suffix that doesn't exist yet."""
    stem = dest.stem
    suffix = dest.suffix
    parent = dest.parent
    i = 1
    while True:
        candidate = parent / f"{stem}__{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def _record_culled_day(event: Event, day_number: int) -> None:
    settings = event.event_settings or {}
    culled = settings.get(CULLED_DAY_NUMBERS_KEY)
    if not isinstance(culled, list):
        culled = []
    if day_number not in culled:
        culled.append(day_number)
        culled.sort()
    settings[CULLED_DAY_NUMBERS_KEY] = culled
    event.event_settings = settings


# ── Public read helpers ──────────────────────────────────────────


def culled_day_numbers(event: Event) -> list[int]:
    """Read-only view of the per-day cull-completion state."""
    settings = event.event_settings or {}
    value = settings.get(CULLED_DAY_NUMBERS_KEY)
    if isinstance(value, list):
        return [int(n) for n in value if isinstance(n, (int, float))]
    return []


def is_day_culled(event: Event, day: TripDay) -> bool:
    return day.day_number in culled_day_numbers(event)


def has_pending_session(event: Event, day: TripDay) -> bool:
    """True when an uncommitted ``_culler_session.json`` exists for
    this day — the signal step 9's Resume-prompt logic will key off."""
    path = journal_path(event, day)
    if not path.exists():
        return False
    try:
        journal = load_journal(event, day)
    except Exception:  # noqa: BLE001
        return False
    return not journal.get("committed_at")


def discard_journal(event: Event, day: TripDay) -> bool:
    """Delete an uncommitted journal — the user chose "Discard" on
    the resume prompt. Photos in the bucket are untouched; a fresh
    culler session can be started over them whenever the user wants.

    Returns True if a file was actually removed (False when there
    was no journal to discard). Never raises.
    """
    path = journal_path(event, day)
    if not path.exists():
        return False
    try:
        path.unlink()
    except OSError as exc:
        log.warning("Could not discard journal %s: %s", path, exc)
        return False
    log.info("Discarded journal for day %d", day.day_number)
    return True


# Re-export so callers don't reach into core.photo_import for the
# bucket name constant.
__all__ = [
    "BUCKET_INDIVIDUAL",
    "CULLED_DAY_NUMBERS_KEY",
    "CommitResult",
    "DISCARDS_SUBDIR",
    "commit_cull",
    "culled_day_numbers",
    "current_index",
    "day_dir",
    "discard_journal",
    "has_pending_session",
    "is_day_culled",
    "is_kept",
    "journal_path",
    "kept_count",
    "load_journal",
    "mark_kept",
    "save_journal",
    "set_current_index",
    "unmark",
]


