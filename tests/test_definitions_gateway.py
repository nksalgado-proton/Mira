"""Tests for ``mira.gateway.definitions`` — spec/93 §6 load-set facade."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Sequence

import pytest

from core.definition_files import (
    KIND_COLLECTION,
    KIND_RECIPE,
    DefinitionFile,
    DefinitionRef,
    new_definition_id,
    write_definition,
)
from mira.gateway.definitions import (
    BOUND_FOLDER_NAME,
    SOURCE_EVENT_DB,
    SOURCE_FILE,
    BoundDefinitionRow,
    DefinitionsGateway,
)
from mira.shared.definition_library import DefinitionLibrary


@pytest.fixture
def collections_root(tmp_path):
    p = tmp_path / "Collections"
    p.mkdir()
    return p


@pytest.fixture
def library(collections_root):
    return DefinitionLibrary(collections_root, KIND_COLLECTION)


def _file_def(root: Path, name: str, *, id_: str | None = None) -> str:
    given_id = id_ or new_definition_id()
    df = DefinitionFile(
        id=given_id, name=name, kind=KIND_COLLECTION,
        payload={"expr": [["+", "exported"]]},
        path=root / f"{name}.json",
    )
    write_definition(df)
    return given_id


def _gateway(library, *, rows_by_event: Dict[str, List[BoundDefinitionRow]] | None = None):
    rows_by_event = rows_by_event or {}

    def _reader(event_id: str) -> Sequence[BoundDefinitionRow]:
        return rows_by_event.get(event_id, [])

    return DefinitionsGateway(
        KIND_COLLECTION, library=library, event_db_rows=_reader,
    )


# ── Construction guards ──────────────────────────────────────────


def test_kind_mismatch_raises(tmp_path):
    """Wiring a recipe-kind gateway to a collection-kind library is a
    programming error."""
    lib = DefinitionLibrary(tmp_path / "x", KIND_COLLECTION)
    with pytest.raises(ValueError):
        DefinitionsGateway(KIND_RECIPE, library=lib, event_db_rows=lambda _: [])


# ── visible_in_event ─────────────────────────────────────────────


def test_visible_unions_global_and_bound(library, collections_root):
    """spec/93 §6: GLOBAL ∪ BOUND-to-E."""
    a = _file_def(collections_root, "Alpha")
    b = _file_def(collections_root, "Beta")
    gw = _gateway(library, rows_by_event={
        "evt-A": [
            BoundDefinitionRow(
                id="bound-1", name="Bound One",
                kind=KIND_COLLECTION, composition={"expr": []},
            ),
        ],
    })
    refs = gw.visible_in_event("evt-A")
    names = {r.name for r in refs}
    assert names == {"Alpha", "Beta", "Bound One"}


def test_visible_excludes_bound_to_other_events(library, collections_root):
    """Definitions bound to a DIFFERENT event don't appear when we
    query event A."""
    _file_def(collections_root, "Alpha")
    gw = _gateway(library, rows_by_event={
        "evt-A": [],
        "evt-B": [
            BoundDefinitionRow(
                id="bound-only-b", name="B-Only",
                kind=KIND_COLLECTION, composition={"expr": []},
            ),
        ],
    })
    names = {r.name for r in gw.visible_in_event("evt-A")}
    assert names == {"Alpha"}


# ── tree_for_event ───────────────────────────────────────────────


def test_tree_appends_bound_pseudo_folder(library, collections_root):
    """When the event has bound rows, the tree gets a ``Bound to this
    event`` pseudo-folder at the end."""
    _file_def(collections_root, "Global Thing")
    gw = _gateway(library, rows_by_event={
        "evt-A": [
            BoundDefinitionRow(
                id="b1", name="Bound Macro", kind=KIND_COLLECTION,
                composition={"expr": []},
            ),
        ],
    })
    tree = gw.tree_for_event("evt-A")
    assert any(f.name == BOUND_FOLDER_NAME for f in tree.folders)
    bound = next(f for f in tree.folders if f.name == BOUND_FOLDER_NAME)
    assert [l.name for l in bound.leaves] == ["Bound Macro"]


def test_tree_omits_pseudo_folder_when_no_bound_rows(library, collections_root):
    """No bound rows → tree is just the file library, no spurious
    folder."""
    _file_def(collections_root, "Global Thing")
    gw = _gateway(library, rows_by_event={"evt-A": []})
    tree = gw.tree_for_event("evt-A")
    assert BOUND_FOLDER_NAME not in [f.name for f in tree.folders]


def test_tree_for_event_does_not_mutate_library_cache(library, collections_root):
    """Calling ``tree_for_event`` for event A then event B yields
    different bound rows — the library's cached tree must NOT carry
    event A's bound folder."""
    _file_def(collections_root, "Global Thing")
    gw = _gateway(library, rows_by_event={
        "evt-A": [
            BoundDefinitionRow(
                id="b1", name="A-Bound", kind=KIND_COLLECTION,
                composition={"expr": []},
            ),
        ],
        "evt-B": [
            BoundDefinitionRow(
                id="b2", name="B-Bound", kind=KIND_COLLECTION,
                composition={"expr": []},
            ),
        ],
    })
    a_tree = gw.tree_for_event("evt-A")
    b_tree = gw.tree_for_event("evt-B")
    # Each tree carries ONLY its own event's bound row.
    a_bound = next(
        (f for f in a_tree.folders if f.name == BOUND_FOLDER_NAME), None)
    b_bound = next(
        (f for f in b_tree.folders if f.name == BOUND_FOLDER_NAME), None)
    assert a_bound is not None and [l.name for l in a_bound.leaves] == ["A-Bound"]
    assert b_bound is not None and [l.name for l in b_bound.leaves] == ["B-Bound"]


# ── resolve ──────────────────────────────────────────────────────


def test_resolve_by_id_finds_file_definition(library, collections_root):
    """Spec/93 §4: resolution by id wins; source='file'."""
    given_id = _file_def(collections_root, "Best Wildlife")
    gw = _gateway(library)
    ref = DefinitionRef(id=given_id, name="ignored", kind=KIND_COLLECTION)
    out = gw.resolve(ref)
    assert out is not None
    assert out.source == SOURCE_FILE
    assert out.name == "Best Wildlife"


def test_resolve_by_id_finds_event_db_definition(library):
    """Bound rows resolve too — by id, with source='event_db' so the
    binding badge can report it."""
    row = BoundDefinitionRow(
        id="bound-id", name="Bound Mix", kind=KIND_COLLECTION,
        composition={"expr": [["+", "exported"]]},
    )
    gw = _gateway(library, rows_by_event={"evt-A": [row]})
    ref = DefinitionRef(id="bound-id", name="ignored", kind=KIND_COLLECTION)
    out = gw.resolve(ref, event_id="evt-A")
    assert out is not None
    assert out.source == SOURCE_EVENT_DB
    assert out.event_id == "evt-A"


def test_resolve_name_fallback_to_file(library, collections_root):
    """An id that no longer resolves (file deleted out-of-band, then
    re-created by the user with same display name) falls back to the
    name — spec/93 §4 graceful-recovery contract."""
    _file_def(collections_root, "Best Wildlife")
    gw = _gateway(library)
    ref = DefinitionRef(
        id="stale-id-from-cut-frozen-source",
        name="Best Wildlife",
        kind=KIND_COLLECTION,
    )
    out = gw.resolve(ref)
    assert out is not None
    assert out.source == SOURCE_FILE


def test_resolve_returns_none_when_nothing_found(library):
    """An id + name that neither store knows → None (the dialog
    surfaces a 'missing ingredient' note per spec/93 §8)."""
    gw = _gateway(library)
    ref = DefinitionRef(
        id="never-existed", name="Never Existed", kind=KIND_COLLECTION)
    assert gw.resolve(ref, event_id="evt-A") is None
