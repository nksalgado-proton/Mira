"""Tests for ``core.placement_migrate`` — the atomic file ↔ event.db
switch (spec/93 §5 last paragraph)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

import pytest

from core.definition_files import (
    KIND_COLLECTION,
    KIND_RECIPE,
    DefinitionFile,
    new_definition_id,
    read_definition,
)
from core.placement_classifier import (
    BoundPlacement,
    PLACEMENT_CROSS_BOUND,
    PLACEMENT_GLOBAL,
)
from core.placement_migrate import (
    LOC_EVENT_DB,
    LOC_FILE,
    LOC_UNSAVED,
    PlacementCallbacks,
    StoredLocation,
    place_definition,
)


class _FakeEventDB:
    """In-memory stand-in for the event.db ``recipe`` table.

    Holds rows keyed by ``(event_id, id)`` so the tests can assert
    "which events ended up holding the definition" without spinning a
    real SQLite. Real wiring lives in the gateway (Block 5)."""

    def __init__(self) -> None:
        self.rows: Dict[tuple, dict] = {}

    def write(self, event_id: str, df: DefinitionFile) -> bool:
        self.rows[(event_id, df.id)] = {
            "id": df.id,
            "name": df.name,
            "kind": df.kind,
            "payload": dict(df.payload),
        }
        return True

    def delete(self, event_id: str, definition_id: str) -> bool:
        key = (event_id, definition_id)
        if key in self.rows:
            del self.rows[key]
            return True
        return False

    def callbacks(self) -> PlacementCallbacks:
        return PlacementCallbacks(
            write_to_event_db=self.write,
            delete_from_event_db=self.delete,
        )


@pytest.fixture
def collections_root(tmp_path):
    p = tmp_path / "Collections"
    p.mkdir()
    return p


@pytest.fixture
def recipes_root(tmp_path):
    p = tmp_path / "Recipes"
    p.mkdir()
    return p


@pytest.fixture
def db():
    return _FakeEventDB()


# ── Fresh saves ───────────────────────────────────────────────────


def test_global_save_writes_file(collections_root, recipes_root, db):
    """Unsaved Collection → GLOBAL → JSON file in Collections/."""
    df = DefinitionFile(
        id=new_definition_id(), name="Best Wildlife",
        kind=KIND_COLLECTION, payload={"expr": [["+", "exported"]]},
    )
    out = place_definition(
        df, placement=PLACEMENT_GLOBAL,
        previous_location=StoredLocation(kind=LOC_UNSAVED),
        collections_root=collections_root,
        recipes_root=recipes_root,
        callbacks=db.callbacks(),
    )
    assert out.kind == LOC_FILE
    assert (collections_root / "Best Wildlife.json").is_file()


def test_cross_bound_save_writes_file(collections_root, recipes_root, db):
    """Cross-bound Recipe → JSON file in Recipes/ (spec/93 §5)."""
    df = DefinitionFile(
        id=new_definition_id(), name="multi-event mix",
        kind=KIND_RECIPE, payload={"source": [["+", "exported"]]},
    )
    out = place_definition(
        df, placement=PLACEMENT_CROSS_BOUND,
        previous_location=StoredLocation(kind=LOC_UNSAVED),
        collections_root=collections_root,
        recipes_root=recipes_root,
        callbacks=db.callbacks(),
    )
    assert out.kind == LOC_FILE
    assert (recipes_root / "multi-event mix.json").is_file()


def test_bound_save_writes_to_event_db(collections_root, recipes_root, db):
    """BoundPlacement → event.db (no file written)."""
    df = DefinitionFile(
        id=new_definition_id(), name="bound mix",
        kind=KIND_RECIPE, payload={"source": [["+", "exported"]]},
    )
    placement = BoundPlacement(event_id="evt-A")
    out = place_definition(
        df, placement=placement,
        previous_location=StoredLocation(kind=LOC_UNSAVED),
        collections_root=collections_root,
        recipes_root=recipes_root,
        callbacks=db.callbacks(),
    )
    assert out.kind == LOC_EVENT_DB
    assert out.event_id == "evt-A"
    assert ("evt-A", df.id) in db.rows
    # No file was written.
    assert not any(recipes_root.rglob("*.json"))


def test_bound_collection_rejected(collections_root, recipes_root, db):
    """A BoundPlacement for a Collection is a programming error — the
    bound DC path uses the existing event.db.dynamic_collection table,
    not this helper (spec/93 §3)."""
    df = DefinitionFile(
        id=new_definition_id(), name="bound dc",
        kind=KIND_COLLECTION, payload={},
    )
    with pytest.raises(ValueError):
        place_definition(
            df, placement=BoundPlacement(event_id="evt-A"),
            previous_location=StoredLocation(kind=LOC_UNSAVED),
            collections_root=collections_root,
            recipes_root=recipes_root,
            callbacks=db.callbacks(),
        )


# ── Migrations (placement flipped between saves) ──────────────────


def test_global_to_bound_deletes_old_file(
        collections_root, recipes_root, db):
    """Recipe was a file → now bound. New row written, old file
    deleted (spec/93 §5: 'never two homes')."""
    df = DefinitionFile(
        id=new_definition_id(), name="r1", kind=KIND_RECIPE,
        payload={"source": [["+", "exported"]]},
    )
    # Save once as GLOBAL.
    first = place_definition(
        df, placement=PLACEMENT_GLOBAL,
        previous_location=StoredLocation(kind=LOC_UNSAVED),
        collections_root=collections_root, recipes_root=recipes_root,
        callbacks=db.callbacks(),
    )
    assert (recipes_root / "r1.json").is_file()

    # User edits the Recipe so it now pins a single-event Cut. Save
    # again with BoundPlacement, passing the previous file location.
    second = place_definition(
        df, placement=BoundPlacement(event_id="evt-A"),
        previous_location=first,
        collections_root=collections_root, recipes_root=recipes_root,
        callbacks=db.callbacks(),
    )
    assert second.kind == LOC_EVENT_DB
    assert ("evt-A", df.id) in db.rows
    # The OLD file is gone.
    assert not (recipes_root / "r1.json").exists()


def test_bound_to_global_deletes_old_row(
        collections_root, recipes_root, db):
    """The inverse: Recipe was bound → now global. File written, old
    event.db row deleted."""
    df = DefinitionFile(
        id=new_definition_id(), name="r2", kind=KIND_RECIPE,
        payload={"source": [["+", "exported"]]},
    )
    # Save once as BOUND.
    first = place_definition(
        df, placement=BoundPlacement(event_id="evt-B"),
        previous_location=StoredLocation(kind=LOC_UNSAVED),
        collections_root=collections_root, recipes_root=recipes_root,
        callbacks=db.callbacks(),
    )
    assert ("evt-B", df.id) in db.rows

    # Now save as GLOBAL.
    second = place_definition(
        df, placement=PLACEMENT_GLOBAL,
        previous_location=first,
        collections_root=collections_root, recipes_root=recipes_root,
        callbacks=db.callbacks(),
    )
    assert second.kind == LOC_FILE
    assert (recipes_root / "r2.json").is_file()
    # The OLD row is gone.
    assert ("evt-B", df.id) not in db.rows


def test_bound_to_other_bound_swaps_events(
        collections_root, recipes_root, db):
    """A definition pinned to event A → now pinned to event B. The new
    row goes into event B; the old row in event A is deleted."""
    df = DefinitionFile(
        id=new_definition_id(), name="r3", kind=KIND_RECIPE,
        payload={"source": [["+", "exported"]]},
    )
    first = place_definition(
        df, placement=BoundPlacement(event_id="evt-A"),
        previous_location=StoredLocation(kind=LOC_UNSAVED),
        collections_root=collections_root, recipes_root=recipes_root,
        callbacks=db.callbacks(),
    )
    second = place_definition(
        df, placement=BoundPlacement(event_id="evt-B"),
        previous_location=first,
        collections_root=collections_root, recipes_root=recipes_root,
        callbacks=db.callbacks(),
    )
    assert ("evt-A", df.id) not in db.rows
    assert ("evt-B", df.id) in db.rows
    assert second.event_id == "evt-B"


def test_same_placement_repeat_save_is_safe(
        collections_root, recipes_root, db):
    """Saving the same placement twice (no flip) just rewrites the
    file / row; no spurious delete."""
    df = DefinitionFile(
        id=new_definition_id(), name="r4", kind=KIND_RECIPE,
        payload={"source": [["+", "exported"]]},
    )
    first = place_definition(
        df, placement=PLACEMENT_GLOBAL,
        previous_location=StoredLocation(kind=LOC_UNSAVED),
        collections_root=collections_root, recipes_root=recipes_root,
        callbacks=db.callbacks(),
    )
    # Save again with the same placement — file path unchanged.
    second = place_definition(
        df, placement=PLACEMENT_GLOBAL,
        previous_location=first,
        collections_root=collections_root, recipes_root=recipes_root,
        callbacks=db.callbacks(),
    )
    assert second == first
    assert (recipes_root / "r4.json").is_file()


def test_id_minted_when_missing(collections_root, recipes_root, db):
    """A first save with df.id="" mints a fresh id; the file carries
    it on read-back."""
    df = DefinitionFile(
        id="", name="anonymous", kind=KIND_RECIPE,
        payload={"source": [["+", "exported"]]},
    )
    out = place_definition(
        df, placement=PLACEMENT_GLOBAL,
        previous_location=StoredLocation(kind=LOC_UNSAVED),
        collections_root=collections_root, recipes_root=recipes_root,
        callbacks=db.callbacks(),
    )
    assert df.id, "id should have been minted"
    back = read_definition(Path(out.path))
    assert back.id == df.id


# ── Failure modes ────────────────────────────────────────────────


def test_missing_recipes_root_for_global_raises(collections_root, db):
    """No recipes_root given for a GLOBAL Recipe save — the helper
    refuses rather than silently writing nowhere."""
    df = DefinitionFile(
        id=new_definition_id(), name="x", kind=KIND_RECIPE, payload={},
    )
    with pytest.raises(ValueError):
        place_definition(
            df, placement=PLACEMENT_GLOBAL,
            previous_location=StoredLocation(kind=LOC_UNSAVED),
            collections_root=collections_root,
            recipes_root=None,                              # missing
            callbacks=db.callbacks(),
        )


def test_delete_from_old_is_best_effort(
        collections_root, recipes_root, db):
    """A failure deleting the OLD location must not block the migration
    — the new write already succeeded, so the definition is safe.
    Surface the leftover via the duplicate-id scan, not by raising."""
    df = DefinitionFile(
        id=new_definition_id(), name="r5", kind=KIND_RECIPE,
        payload={"source": [["+", "exported"]]},
    )
    first = place_definition(
        df, placement=PLACEMENT_GLOBAL,
        previous_location=StoredLocation(kind=LOC_UNSAVED),
        collections_root=collections_root, recipes_root=recipes_root,
        callbacks=db.callbacks(),
    )
    # Pretend the OLD file is gone (manual delete by the user). The
    # next migration must not crash on the missing-source delete.
    Path(first.path).unlink()
    second = place_definition(
        df, placement=BoundPlacement(event_id="evt-A"),
        previous_location=first,
        collections_root=collections_root, recipes_root=recipes_root,
        callbacks=db.callbacks(),
    )
    assert second.kind == LOC_EVENT_DB
