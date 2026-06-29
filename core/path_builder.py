"""Single source of truth for all folder/path naming patterns.

Every part of the app that constructs folder names must use these
functions — no inline f-strings for day/event/stage folder names.

Layout (spec/57, LOCKED Nelson 2026-06-10 — bytes at the two ends,
the database in the middle; folder names are FIXED ENGLISH on disk
regardless of app language):

    <event_root>/
        Original Media/   ← the captured tree — sacred, byte-pristine
            _cameras|_phones|_other/<day>/<cam>/   (the SD-wipe
                                safety mirror; never EXIF-rewritten)
            Merged/       ← ADOPTED externally-merged stack masters
                            (the one sanctioned additive carve-out)
        Picked Media/     ← DERIVED links projection of Pick state —
            <bracket>/        the external tools' doorway (spec/57 §2);
                              built on entering Edit, regenerable*
        Edited Media/     ← Edit's export target; external editors
                            return their work into a subdir here
        Cuts/<cut name>/  ← Share handoffs (hardlinks for PTE)
        event.db (+ backup) · internal caches

    * …except external-tool outputs at its root awaiting ingest —
      rebuilds must always preserve real files.

There are NO intermediate phase dirs: decisions live in the
database (phase_state), never in folders. The numbered stage
layout (00 - Captured … 05 - Distributed, 2026-05-19 freeze)
retired with spec/57; its names survive below ONLY for the
legacy-era core modules that still reference them, pending their
own retirement sweep.
"""

import re
from pathlib import Path

from core.models import Event, TripDay


# ── Event directory names (spec/57 §1) ───────────────────────────

# The captured tree — sacred, byte-pristine (CLAUDE.md invariant #7);
# the SD-wipe safety mirror. Three capture-source subfolders —
# underscore-prefixed so they sort at the top — live ONLY here.
ORIGINAL_MEDIA_DIR_NAME = "Original Media"
CAPTURED_CAMERAS_SUBDIR = "_cameras"
# Anglicised 2026-05-17 (Nelson) — were "_celulares" / "_outros".
CAPTURED_PHONES_SUBDIR = "_phones"
CAPTURED_OTHER_SUBDIR = "_other"
# Reconcile-only quarantine: photos with no readable EXIF timestamp
# land here instead of being routed by mtime (mtime can collapse to
# the copy date and place photos in the wrong day folder, which is
# more harmful than helpful). Filenames are mtime-prefixed so that
# sorting alphabetically inside this folder approximates chronology
# even though the actual day-bucketing has to be done manually.
CAPTURED_NO_TIMESTAMP_SUBDIR = "_no_timestamp"
CAPTURED_SUBDIRS: tuple[str, ...] = (
    CAPTURED_CAMERAS_SUBDIR,
    CAPTURED_PHONES_SUBDIR,
    CAPTURED_OTHER_SUBDIR,
)

# Adopted externally-merged stack masters (spec/57 §2.3) — the ONE
# sanctioned addition to the captured tree. Additive-only; the
# card-derived subtrees beside it stay untouchable.
MERGED_SUBDIR_NAME = "Merged"

# Edit candidates — external editors (LRC-class) return their work
# into a subdir here (spec/57 §3.1). spec/66 §1.2 (2026-06-14) made
# this the **inbox** for third-party returns; the shipped set lives
# in `Exported Media/` below.
EDITED_MEDIA_DIR_NAME = "Edited Media"

# spec/66 §1.2 — the SHIPPED set. The Export phase materialises
# green-selected finals here (Mira-rendered in-place; third-party
# returns hardlinked from Edited Media/). The folder is exactly the
# `#exported` Cut universe (spec/61 §1.1) and the PTE hand-off folder.
# Lineage rows produced by the Export run carry `export_relpath` under
# this tier; Edited-Media-only relpaths denote mere edit candidates.
EXPORTED_MEDIA_DIR_NAME = "Exported Media"

# The DERIVED links projection of Pick state — the external tools'
# doorway (spec/57 §2): flat root of day+camera-prefixed links plus
# one subdir per focus/exposure bracket. Built on entering Edit;
# regenerable except for tool outputs at its root awaiting ingest.
PICKED_MEDIA_DIR_NAME = "Picked Media"

# Share handoffs — one subfolder per Cut, hardlinks for PTE
# (spec/51 vocabulary + spec/57 placement; was "04 - Cuts").
CUTS_DIR_NAME = "Cuts"

# spec/155 — per-day and per-event map images live under this tier.
# Filenames are slot-named: ``event.<ext>`` and ``day-NN.<ext>``
# (zero-padded to 2 digits). The user supplies the image (JPEG/PNG);
# Mira never fetches map tiles (charter rule #3 — strict offline-first).
MAPS_DIR_NAME = "Maps"

# Subfolder under each day folder where Process Videos drops
# extracted frames (snapped JPEGs picked up by Process Photos).
EXTRACTED_FRAMES_FOLDER_NAME = "extracted"


# ── RETIRED stage names (spec/57 §6) — legacy modules only ────────
# The numbered pipeline layout retired 2026-06-10: nothing in the
# rebuilt pipeline reads or writes these. The constants survive so
# the legacy-era core modules (cull_export / cull_phase_sync /
# reconcile_pipeline / event_stats / …) stay importable until their
# own retirement sweep. The two RENAMED dirs alias to the live names
# so any legacy reader that does run resolves to the real tree.

CAPTURED_DIR_NAME = ORIGINAL_MEDIA_DIR_NAME          # renamed by spec/57
PROCESSED_DIR_NAME = EDITED_MEDIA_DIR_NAME           # renamed by spec/57
CULLED_DIR_NAME = "01 - Culled"                      # retired — no successor
SELECTED_DIR_NAME = "02 - Selected"                  # retired — no successor
TPS_PROCESSED_DIR_NAME = "03b - Third Party Software Processed"  # retired — Picked Media is the doorway now
CURATED_DIR_NAME = "04 - Curated"                    # retired with spec/51
DISTRIBUTED_DIR_NAME = "05 - Distributed"            # retired — Cuts/ is the handoff


# ── Sanitisation / generic helpers ───────────────────────────────

def sanitize_folder_name(name: str) -> str:
    """Remove characters that are invalid in Windows folder names."""
    invalid = '<>:"/\\|?*'
    for ch in invalid:
        name = name.replace(ch, '-')
    return name.strip('. ')


# ── Event / day folders ──────────────────────────────────────────

def event_folder_name(event: Event) -> str:
    """Standard event folder name: '{year} - {name}'."""
    return event.display_name


def event_root_path(base_path: str, event: Event) -> Path:
    """Full path to the event root folder — **always** ``Event.photos_base_path``.

    Nelson 2026-05-22: under the cleaned model,
    ``Event.photos_base_path`` is the absolute event root (the
    directory that contains ``00 - Captured/``, ``01 - Culled/``,
    etc.). No derivation, no ``/trips/`` insertion, no event-folder
    suffix appended. The user's directory structure is theirs to
    decide; Mira uses the absolute path verbatim.

    The legacy ``base_path`` argument is **vestigial** — callers
    still pass it but the function ignores it (the per-event field
    is the only source of truth). The argument is preserved for
    signature compatibility while the 22-file call-site cleanup
    happens incrementally. New code should pass ``event.photos_base_path``
    explicitly or drop the first argument once all callers are
    updated.

    Legacy events (which stored only the global base ``D:\\Photos``
    and relied on this function to append ``trips/<event>/``) are
    migrated in-place at load time by
    :func:`data.event_store._migrate_legacy_event_root` — by the
    time this function is reached, ``event.photos_base_path`` is
    always the absolute event root.

    New-event creation should set
    ``event.photos_base_path = <global_base>/<event_folder_name>``
    by default (NO ``/trips/`` insertion); the user can override the
    absolute path in the new-event dialog.
    """
    # base_path intentionally unused — see docstring.
    del base_path  # silence linters about the unused argument
    return Path(event.photos_base_path)


def day_folder_name(day: TripDay) -> str:
    """Canonical day folder name (Nelson 2026-05-23, task #107):
    ``Dia {N} - YYYY-MM-DD - {description}``. The ISO date is
    embedded so the on-disk folder carries the actual day it
    represents — useful when reviewing the tree in Explorer
    months later, and unambiguous across machines / time zones.

    Falls back gracefully when fields are missing:
      * No description → ``Dia {N} - YYYY-MM-DD``
      * No date (rare; only mid-edit transient state) →
        ``Dia {N} - {description}`` (legacy shape)
      * Neither → ``Dia {N}``

    Recognising LEGACY folder names (created before this format
    landed) is handled by :func:`day_number_from_folder` — the
    name parser, used by the plan↔disk reconciler to detect
    folders that need renaming without losing their identity."""
    safe_desc = sanitize_folder_name(day.description or "")
    parts = [f"Dia {day.day_number}"]
    if day.date is not None:
        parts.append(day.date.isoformat())   # YYYY-MM-DD
    if safe_desc:
        parts.append(safe_desc)
    return " - ".join(parts)


# Folder-name parser. Matches BOTH the new
# ``Dia N - YYYY-MM-DD - desc`` shape AND the legacy
# ``Dia N - desc`` shape so the reconciler can identify the day
# number regardless of when the folder was created.
_DAY_FOLDER_RE = re.compile(
    r"^Dia\s+(?P<day>\d+)(?:\s*-\s*.*)?$"
)


def day_number_from_folder(folder_name: str) -> int | None:
    """Parse the day number out of a folder name regardless of
    whether the folder uses the new (with-date) or the legacy
    (description-only) shape. Returns ``None`` when the folder
    doesn't match either pattern (e.g. ``_no_timestamp``,
    ``_out_of_day_range``, junk dirs)."""
    m = _DAY_FOLDER_RE.match(folder_name.strip())
    if not m:
        return None
    try:
        return int(m.group("day"))
    except (TypeError, ValueError):
        return None


def day_folder_path(event_root: Path, day: TripDay) -> Path:
    """Full path to a day folder in the **Select** tree
    (``02 - Selected/``) — what Process reads. Callers pass the
    event root; this inserts the stage prefix. (For the cull bank
    day folder use :func:`culled_day_path`.)"""
    return selected_dir(event_root) / day_folder_name(day)


def culled_day_path(event_root: Path, day: TripDay) -> Path:
    """Full path to a day folder in the **Cull** bank
    (``01 - Culled/Dia N - …/``) — the Cull-phase Export target."""
    return culled_dir(event_root) / day_folder_name(day)


# ── Event-dir helpers (spec/57) ──────────────────────────────────

def original_media_dir(event_root: Path) -> Path:
    """``Original Media/`` — the captured tree: sacred, byte-pristine
    (the SD-wipe safety mirror; never EXIF-rewritten)."""
    return event_root / ORIGINAL_MEDIA_DIR_NAME


def merged_dir(event_root: Path) -> Path:
    """``Original Media/Merged/`` — adopted externally-merged stack
    masters (spec/57 §2.3). Additive-only; created on first adoption,
    not pre-created."""
    return original_media_dir(event_root) / MERGED_SUBDIR_NAME


def edited_media_dir(event_root: Path) -> Path:
    """``Edited Media/`` — the third-party-return inbox / edit
    candidates tier (spec/57 §3.1, spec/66 §1.2). Mira's own
    development is non-destructive params in the DB; this tier carries
    only externally-edited returns (LRC / Helicon / stacker outputs)."""
    return event_root / EDITED_MEDIA_DIR_NAME


def exported_media_dir(event_root: Path) -> Path:
    """``Exported Media/`` — the shipped set (spec/66 §1.2). Holds
    exactly the green selections from the Export phase: Mira-rendered
    finals + hardlinks to third-party returns. Equivalent to the
    ``#exported`` Cut universe and the PTE hand-off folder."""
    return event_root / EXPORTED_MEDIA_DIR_NAME


def picked_media_dir(event_root: Path) -> Path:
    """``Picked Media/`` — the derived links projection of Pick state
    (the external tools' doorway, spec/57 §2). Built on entering
    Edit; not pre-created."""
    return event_root / PICKED_MEDIA_DIR_NAME


def cuts_dir(event_root: Path) -> Path:
    """``Cuts/`` — Share handoffs, one subfolder per Cut."""
    return event_root / CUTS_DIR_NAME


def maps_dir(event_root: Path) -> Path:
    """``Maps/`` — per-day and per-event map images (spec/155). User-supplied
    JPEG/PNG only; strict offline-first holds (no tile fetch). Filenames are
    slot-named (``event.<ext>``, ``day-NN.<ext>``); replacement overwrites
    the slot atomically (write-then-rename) and deletes any stale sibling
    with a different extension."""
    return event_root / MAPS_DIR_NAME


# ── Map-slot filename helpers (spec/155) ─────────────────────────

# Accepted source extensions for map images. Saved with the source's
# extension — no re-encoding. The picker rejects anything else.
MAP_IMAGE_EXTENSIONS: tuple[str, ...] = (".jpg", ".jpeg", ".png")


def event_map_slot_basename() -> str:
    """The event-level map slot's base filename (without extension)."""
    return "event"


def day_map_slot_basename(day_number: int) -> str:
    """The per-day map slot's base filename (without extension), e.g.
    ``day-02`` for day 2. Two-digit zero pad — widen to 3 here if any
    real event ever holds >99 days."""
    return f"day-{day_number:02d}"


# ── Stage helpers (legacy-era; see the RETIRED block above) ──────

def captured_dir(event_root: Path) -> Path:
    """Legacy alias of :func:`original_media_dir` (renamed by
    spec/57). Prefer the new helper in live code."""
    return original_media_dir(event_root)


def culled_dir(event_root: Path) -> Path:
    """RETIRED (spec/57): ``01 - Culled/`` — legacy Cull-phase output.
    Kept only for the legacy-era modules pending their sweep."""
    return event_root / CULLED_DIR_NAME


def selected_dir(event_root: Path) -> Path:
    """RETIRED (spec/57): ``02 - Selected/`` — legacy Select-phase
    output. Kept only for the legacy-era modules pending their sweep."""
    return event_root / SELECTED_DIR_NAME


def tps_processed_dir(event_root: Path) -> Path:
    """``03b - Third Party Software Processed/`` — the external-tool
    PEER of ``03 - Processed`` (Curate reads either). Created by the
    user / the tool's publisher when an external processor is used."""
    return event_root / TPS_PROCESSED_DIR_NAME


def process_source_dir(event_root: Path, source_dir_name: str) -> Path:
    """Resolve the parent of the per-day folders that Process Photos
    should read from. ``source_dir_name`` is typically
    ``SELECTED_DIR_NAME`` (default) or ``TPS_PROCESSED_DIR_NAME``,
    but the user can point at any folder name — discovery just walks
    ``<event_root>/<source_dir_name>/<day>/<scenario>/``.
    """
    return event_root / source_dir_name


def find_day_folders_root(
    start: Path,
    day_folder_names: set[str],
    max_descend: int = 2,
) -> Path:
    """Find the directory whose direct children include at least one
    matching day folder, descending up to ``max_descend`` levels.

    A third-party folder-publisher (e.g. Lightroom's jf Folder
    Publisher) rebuilds the catalog's relative paths under its
    Publish Tree Root, which inserts the original source dir name
    (e.g. ``02 - Selected``) between the user-picked publish root and
    the day folders. So when Process Photos points at
    ``03b - Third Party Software Processed/`` the actual day folders
    are one level deeper at
    ``03b - Third Party Software Processed/02 - Selected/Dia N - …/``.
    The user can't change LRC's behavior, so we transparently descend
    into a single intermediate dir when no day folders are direct
    children.

    Returns the original ``start`` if it doesn't exist (callers handle
    missing roots themselves) or if no day folders are found within
    ``max_descend`` levels — keeping discovery's "no items" path
    intact instead of silently returning a wrong directory.

    Doesn't descend through more than one subdir per level: if the
    intermediate level has multiple sibling dirs (i.e. ambiguous
    structure), we stop and return ``start`` so the caller's normal
    iteration still runs.
    """
    if not start.exists() or not start.is_dir():
        return start
    candidate = start
    for _ in range(max_descend + 1):
        try:
            children = list(candidate.iterdir())
        except OSError:
            return start
        # Direct day-folder match wins immediately — never descend
        # further once we've found the actual root.
        for child in children:
            if child.is_dir() and child.name in day_folder_names:
                return candidate
        # No matching day at this level. Descend if (and only if)
        # there's exactly one subdir to descend into. Multiple
        # subdirs is ambiguous — bail out.
        subdirs = [c for c in children if c.is_dir()]
        if len(subdirs) != 1:
            return start
        candidate = subdirs[0]
    return start


def processed_dir(event_root: Path) -> Path:
    """Legacy alias of :func:`edited_media_dir` (renamed by spec/57).
    Prefer the new helper in live code."""
    return edited_media_dir(event_root)


def curated_dir(event_root: Path) -> Path:
    """RETIRED (spec/51 + spec/57): ``04 - Curated/``. Kept only for
    the legacy-era modules pending their sweep."""
    return event_root / CURATED_DIR_NAME


def distributed_dir(event_root: Path) -> Path:
    """RETIRED (spec/57): ``05 - Distributed/`` — ``Cuts/`` is the
    handoff now. Kept only for the legacy-era modules."""
    return event_root / DISTRIBUTED_DIR_NAME


def slideshows_dir(event_root: Path) -> Path:
    """``05 - Distributed/`` — rendered slideshow outputs (now the
    top-level Distribute stage; back-compat alias of
    :func:`distributed_dir`)."""
    return distributed_dir(event_root)


def extracted_dir(event_root: Path, day: TripDay) -> Path:
    """``02 - Selected/Dia N - …/extracted/`` — frame snaps from
    Process Videos for this day."""
    return day_folder_path(event_root, day) / EXTRACTED_FRAMES_FOLDER_NAME


def ensure_event_tree(event_root: Path) -> None:
    """Idempotently create the spec/57 + spec/66 event skeleton under an
    existing ``event_root``: ``Original Media/{_cameras,_phones,_other}``
    + ``Edited Media`` + ``Exported Media`` + ``Cuts``. The single
    tree-birthing helper — every creation/restore path calls this so an
    event always reads the same in Explorer. ``Picked Media`` (built on
    entering Edit) and ``Original Media/Merged`` (first stack adoption)
    stay lazy."""
    original = original_media_dir(event_root)
    original.mkdir(parents=True, exist_ok=True)
    for sub in CAPTURED_SUBDIRS:
        (original / sub).mkdir(exist_ok=True)
    edited_media_dir(event_root).mkdir(exist_ok=True)
    exported_media_dir(event_root).mkdir(exist_ok=True)
    cuts_dir(event_root).mkdir(exist_ok=True)
    maps_dir(event_root).mkdir(exist_ok=True)


# ── Reserved-name helpers (used to skip stage dirs during scans) ──

# Names that should NOT be treated as day folders or as scenario
# subfolders when walking the event tree. Keep this list complete
# so any code using ``Path.iterdir()`` to discover days/scenarios
# can filter against it. Carries the live spec/57 names AND the
# retired numbered names (a stray legacy tree must never read as
# day folders).
RESERVED_DIR_NAMES = frozenset({
    ORIGINAL_MEDIA_DIR_NAME,
    PICKED_MEDIA_DIR_NAME,
    EDITED_MEDIA_DIR_NAME,
    EXPORTED_MEDIA_DIR_NAME,
    CUTS_DIR_NAME,
    MAPS_DIR_NAME,
    MERGED_SUBDIR_NAME,
    "00 - Captured",
    CULLED_DIR_NAME,
    SELECTED_DIR_NAME,
    TPS_PROCESSED_DIR_NAME,
    "03 - Processed",
    CURATED_DIR_NAME,
    DISTRIBUTED_DIR_NAME,
    EXTRACTED_FRAMES_FOLDER_NAME,
})
