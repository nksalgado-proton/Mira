"""Atomic file ↔ event.db switch for spec/93 §5.

When a definition's classification flips between **GLOBAL** /
**BOUND(E)** / **CROSS-BOUND** during an edit — typically because the
user added or removed a single-event Cut operand — the definition
needs to migrate between the recipe library (a JSON file under
``<library_root>/Collections/`` or ``Recipes/``) and the bound event's
``event.db``. The contract per spec/93 §5 last paragraph:

* Atomic: write the new location BEFORE deleting the old. A crash
  mid-migration leaves both copies (harmless — the next scan picks the
  newer mtime and the duplicate-id detection in :mod:`definition_library`
  surfaces it for cleanup).
* Idempotent: re-running on an already-migrated definition is a no-op.
* Never two homes at once: only the old location is deleted after the
  new write succeeds.

The migration is the **save-time** seam — the dialog's "Save" button
delegates to :func:`place_definition` with the freshly-classified
placement. The mass-import migration of pre-spec/93 ``saved_filter`` /
``mira.db.recipe`` rows into JSON files is a separate concern (a
later sub-block); this module is the per-write switch.

Pure logic + callbacks. No Qt. The callbacks are the seam to the
database writers (the gateway provides them).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from core.definition_files import (
    DefinitionFile,
    KIND_COLLECTION,
    KIND_RECIPE,
    file_path_for,
    new_definition_id,
    write_definition,
)
from core.placement_classifier import (
    BoundPlacement,
    PLACEMENT_CROSS_BOUND,
    PLACEMENT_GLOBAL,
    Placement,
    placement_is_file,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredLocation:
    """Where a definition currently lives.

    ``kind`` is ``"file"`` (its on-disk JSON path is in ``path``) or
    ``"event_db"`` (it lives as a row in ``event_id``'s event.db).
    ``"unsaved"`` is the third case — a brand-new definition the user
    hasn't pressed Save on yet.
    """
    kind: str           # 'file' | 'event_db' | 'unsaved'
    event_id: str = ""  # for 'event_db'
    path: str = ""      # for 'file' (as str so the dataclass stays hashable)


LOC_FILE = "file"
LOC_EVENT_DB = "event_db"
LOC_UNSAVED = "unsaved"


@dataclass(frozen=True)
class PlacementCallbacks:
    """Database-side writers the migration needs.

    The gateway wires these to its event.db / library transaction
    layer; the migration stays pure-logic so it can be unit-tested
    without the DB.
    """
    #: Persist a Recipe-flavour DefinitionFile into ``<event_id>``'s
    #: event.db ``recipe`` table. Returns ``True`` on success.
    write_to_event_db: Callable[[str, DefinitionFile], bool]
    #: Remove a Recipe / DC row by id from ``<event_id>``'s event.db.
    #: Returns ``True`` when a row was deleted; ``False`` when it
    #: wasn't there (idempotent).
    delete_from_event_db: Callable[[str, str], bool]


def place_definition(
    df: DefinitionFile,
    *,
    placement: Placement,
    previous_location: StoredLocation,
    collections_root: Optional[object] = None,
    recipes_root: Optional[object] = None,
    callbacks: PlacementCallbacks,
) -> StoredLocation:
    """Write ``df`` to the location dictated by ``placement``, then
    delete it from ``previous_location`` if it changed.

    ``collections_root`` / ``recipes_root`` are the on-disk folder roots
    (``Path`` instances, typed as ``object`` here so the module stays
    framework-free). The caller picks the right one based on ``df.kind``;
    a single-kind library (e.g. only Recipes for a Cut-Recipe save) may
    pass ``None`` for the other.

    The contract is **write-then-delete**: a failure mid-flight leaves
    the OLD copy in place, so the user never loses the definition.

    Returns the new :class:`StoredLocation`. The caller stores this on
    the dialog state so the NEXT save can again compute the diff.
    """
    if not df.id:
        df.id = new_definition_id()

    # ── compute the NEW target ──────────────────────────────────────
    if placement_is_file(placement):
        root = collections_root if df.kind == KIND_COLLECTION else recipes_root
        if root is None:
            raise ValueError(
                f"place_definition: missing root for kind={df.kind!r} "
                f"with placement={placement!r}"
            )
        from pathlib import Path
        if not isinstance(root, Path):
            root = Path(str(root))
        # File-resident definitions always live at the root of the
        # tree by default; subfolder organisation is a user move via
        # the file manager, not a save-time choice (spec/93 §4).
        df.path = file_path_for(root, df.name)
        new_loc = StoredLocation(kind=LOC_FILE, path=str(df.path))
    elif isinstance(placement, BoundPlacement):
        if df.kind != KIND_RECIPE:
            # spec/93 §3: bound DCs already use event.db's existing
            # dynamic_collection table; the file-based store is for
            # global ones. The migration helper only handles RECIPE
            # writes into event.db — a bound Collection should be
            # routed through the existing DC gateway.
            raise ValueError(
                f"place_definition: BoundPlacement only handles "
                f"kind={KIND_RECIPE!r}; got {df.kind!r}"
            )
        # The bound write doesn't carry a Path — event.db is opaque.
        df.path = None
        new_loc = StoredLocation(
            kind=LOC_EVENT_DB, event_id=placement.event_id)
    else:
        raise ValueError(f"place_definition: unknown placement {placement!r}")

    # ── write to the NEW target ─────────────────────────────────────
    if new_loc.kind == LOC_FILE:
        write_definition(df)
    else:  # LOC_EVENT_DB
        ok = callbacks.write_to_event_db(new_loc.event_id, df)
        if not ok:
            raise OSError(
                f"place_definition: write_to_event_db failed for event "
                f"{new_loc.event_id} / definition {df.id}")

    # ── delete from the OLD location IFF it differs ─────────────────
    if previous_location.kind != new_loc.kind or (
        previous_location.kind == LOC_FILE
        and previous_location.path != new_loc.path
    ) or (
        previous_location.kind == LOC_EVENT_DB
        and previous_location.event_id != new_loc.event_id
    ):
        _delete_from(previous_location, df.id, callbacks)

    return new_loc


def _delete_from(
    loc: StoredLocation, definition_id: str,
    callbacks: PlacementCallbacks,
) -> None:
    """Best-effort delete from a previous location. Errors are logged
    but never raised — the spec contract is "never two homes", not
    "fail loudly". A leftover row is detected on next scan
    (definition_library's duplicate-id warning) and the user can clean
    it up; a leftover JSON file is similarly visible."""
    if loc.kind == LOC_UNSAVED:
        return
    if loc.kind == LOC_FILE:
        import os
        if not loc.path:
            return
        try:
            os.remove(loc.path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning(
                "place_definition: failed to delete old file %s: %s",
                loc.path, exc,
            )
        return
    if loc.kind == LOC_EVENT_DB:
        try:
            callbacks.delete_from_event_db(loc.event_id, definition_id)
        except Exception as exc:                            # noqa: BLE001
            log.warning(
                "place_definition: failed to delete %s from event %s: %s",
                definition_id, loc.event_id, exc,
            )
        return


__all__ = [
    "LOC_EVENT_DB",
    "LOC_FILE",
    "LOC_UNSAVED",
    "PlacementCallbacks",
    "StoredLocation",
    "place_definition",
]
