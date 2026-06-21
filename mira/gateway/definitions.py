"""Definitions facade — spec/93 §6 GLOBAL ∪ BOUND-to-E load set.

The Collection / Recipe dialog asks "what can I offer when composing
in event E?" — and the answer is **every global definition in the
recipe library + every definition bound to E**, never bindings to a
different event.

This facade unions:

* The **file-based library** (one :class:`DefinitionLibrary` per kind
  — Collections + Recipes — backed by JSON files under the user's
  library root, spec/93 §4).
* The **event.db side** — for Collections, the existing
  ``dynamic_collection`` rows; for Recipes, the v13 ``recipe`` rows
  added by spec/94 Phase 1.

The facade is intentionally thin — id-based lookup, name fallback for
the spec/93 §4 graceful-recovery contract, and a TreeNode builder that
appends a "Bound to this event" pseudo-folder beneath the file tree
so the cascading menu (Block 5) renders both homes in one widget.

No SQL is written here directly — the gateway provides callables that
read its tables; this module owns only the union + the resolution
order.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from core.definition_files import (
    DefinitionFile,
    DefinitionRef,
    KIND_COLLECTION,
    KIND_RECIPE,
)
from mira.shared.definition_library import DefinitionLibrary, TreeNode

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BoundDefinitionRow:
    """A definition row from an event's ``event.db``.

    Mirrors the dataclass shape the gateway returns from
    ``dynamic_collection`` (Collections) and ``recipe`` (Recipes).
    The minimal columns the facade needs to render a leaf in the
    cascading menu are ``id`` + ``name``; ``composition`` is the
    payload the dialog reads on resolve.
    """
    id: str
    name: str
    kind: str
    composition: Dict


@dataclass(frozen=True)
class DefinitionResolution:
    """The result of resolving a :class:`DefinitionRef`.

    Carries the composition + a ``source`` discriminator so the
    dialog knows whether it loaded from a file or from an event.db
    row (the binding badge reads this).
    """
    id: str
    name: str
    kind: str
    composition: Dict
    source: str                     # 'file' | 'event_db'
    event_id: str = ""              # set when source == 'event_db'


#: Source-discriminator constants for :class:`DefinitionResolution`.
SOURCE_FILE = "file"
SOURCE_EVENT_DB = "event_db"

#: Pseudo-folder name appended to the cascading menu when the current
#: event has bound definitions — keeps the "this event's recipes" set
#: discoverable without losing the global tree's structure.
BOUND_FOLDER_NAME = "Bound to this event"


class DefinitionsGateway:
    """Read-only facade over the file library + event.db bound rows.

    Construct one per kind (collections + recipes), wired to the
    matching :class:`DefinitionLibrary` and the gateway's
    event-db readers.
    """

    def __init__(
        self,
        kind: str,
        *,
        library: DefinitionLibrary,
        event_db_rows: Callable[[str], Sequence[BoundDefinitionRow]],
    ) -> None:
        if library.kind != kind:
            raise ValueError(
                f"DefinitionsGateway({kind!r}) wired to library of kind "
                f"{library.kind!r}; mismatched."
            )
        self._kind = kind
        self._library = library
        self._read_event_rows = event_db_rows

    # ── basic queries ─────────────────────────────────────────────

    @property
    def kind(self) -> str:
        return self._kind

    def visible_in_event(self, event_id: str) -> List[DefinitionRef]:
        """Spec/93 §6 load set — every global definition plus the
        bound ones belonging to ``event_id``.

        File-resident definitions are returned first (the natural
        browse order); bound rows append after. The dialog renders
        these as separate sections via :meth:`tree_for_event`.
        """
        refs: List[DefinitionRef] = []
        for df in self._library.all_definitions():
            refs.append(DefinitionRef(id=df.id, name=df.name, kind=self._kind))
        for row in self._read_event_rows(event_id):
            refs.append(DefinitionRef(id=row.id, name=row.name, kind=self._kind))
        return refs

    def tree_for_event(self, event_id: str) -> TreeNode:
        """A :class:`TreeNode` mirroring the file library, with a
        pseudo-folder "Bound to this event" appended when ``event_id``
        has any bound rows.

        Returned tree is a SHALLOW copy of the library's cached tree
        — we don't mutate the library's cache. The cascading menu
        (Block 5) walks this directly.
        """
        base = self._library.list_tree()
        # Shallow copy so the bound rows don't leak into the library's
        # cache for OTHER events.
        root = TreeNode(
            name=base.name, path=base.path,
            folders=list(base.folders),
            leaves=list(base.leaves),
        )
        bound = list(self._read_event_rows(event_id))
        if bound:
            bound.sort(key=lambda r: r.name.lower())
            bound_folder = TreeNode(
                name=BOUND_FOLDER_NAME,
                path=base.path / BOUND_FOLDER_NAME,
                folders=[],
                leaves=[
                    DefinitionRef(id=r.id, name=r.name, kind=self._kind)
                    for r in bound
                ],
            )
            root.folders.append(bound_folder)
        return root

    # ── resolution ────────────────────────────────────────────────

    def resolve(
        self,
        ref: DefinitionRef,
        *,
        event_id: str = "",
    ) -> Optional[DefinitionResolution]:
        """Spec/93 §4 resolution: id first, name fallback.

        Searches the file library by id; if a hit, returns it.
        Otherwise searches ``event_id``'s bound rows by id. Falls
        back to a name lookup in both stores in the same order. A
        ``ref`` whose id resolves to one event but the caller is
        querying a DIFFERENT event will miss the bound store —
        which matches spec/93 §6 (a recipe bound to event A is
        invisible in event B).
        """
        # File-by-id.
        df = self._library.by_id(ref.id) if ref.id else None
        if df is not None:
            return self._resolution_from_file(df)
        # Event-DB-by-id.
        if event_id:
            for row in self._read_event_rows(event_id):
                if row.id == ref.id:
                    return DefinitionResolution(
                        id=row.id, name=row.name, kind=self._kind,
                        composition=row.composition,
                        source=SOURCE_EVENT_DB, event_id=event_id,
                    )
        # Name fallback (file).
        if ref.name:
            df = self._library.by_name(ref.name)
            if df is not None:
                return self._resolution_from_file(df)
            # Name fallback (event-db).
            if event_id:
                for row in self._read_event_rows(event_id):
                    if row.name == ref.name:
                        return DefinitionResolution(
                            id=row.id, name=row.name, kind=self._kind,
                            composition=row.composition,
                            source=SOURCE_EVENT_DB, event_id=event_id,
                        )
        return None

    def _resolution_from_file(self, df: DefinitionFile) -> DefinitionResolution:
        return DefinitionResolution(
            id=df.id, name=df.name, kind=self._kind,
            composition=dict(df.payload), source=SOURCE_FILE,
        )


__all__ = [
    "BOUND_FOLDER_NAME",
    "BoundDefinitionRow",
    "DefinitionResolution",
    "DefinitionsGateway",
    "SOURCE_EVENT_DB",
    "SOURCE_FILE",
]
