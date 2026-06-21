"""Tests for ``mira.shared.definition_library`` — the cached
file-tree library for spec/93 §4 Collections / Recipes."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core.definition_files import (
    JSON_SCHEMA_VERSION,
    KIND_COLLECTION,
    KIND_RECIPE,
    DefinitionFile,
    new_definition_id,
    write_definition,
)
from mira.shared.definition_library import DefinitionLibrary, TreeNode


@pytest.fixture
def collections_root(tmp_path: Path) -> Path:
    p = tmp_path / "Collections"
    p.mkdir()
    return p


@pytest.fixture
def lib(collections_root: Path) -> DefinitionLibrary:
    return DefinitionLibrary(collections_root, KIND_COLLECTION)


def _make_file(root: Path, name: str, *, subfolder: str = "",
               payload: dict | None = None, id_: str | None = None) -> Path:
    folder = root if not subfolder else root / subfolder
    folder.mkdir(parents=True, exist_ok=True)
    df = DefinitionFile(
        id=id_ or new_definition_id(),
        name=name,
        kind=KIND_COLLECTION,
        payload=payload or {"expr": [["+", "exported"]]},
        path=folder / f"{name}.json",
    )
    write_definition(df)
    return df.path


# ── construction ─────────────────────────────────────────────────


def test_kind_must_be_known(tmp_path):
    with pytest.raises(ValueError):
        DefinitionLibrary(tmp_path, "bogus")


def test_kind_round_trips(collections_root):
    lib = DefinitionLibrary(collections_root, KIND_COLLECTION)
    assert lib.kind == KIND_COLLECTION
    assert lib.root == collections_root


def test_empty_root_returns_empty_tree(collections_root):
    """A fresh install with no JSON files yet — tree has no leaves."""
    lib = DefinitionLibrary(collections_root, KIND_COLLECTION)
    tree = lib.list_tree()
    assert isinstance(tree, TreeNode)
    assert tree.leaves == []
    assert tree.folders == []


def test_missing_root_doesnt_crash(tmp_path):
    """Library against a folder that doesn't exist yet (the scaffold
    didn't run) — empty tree, no error."""
    lib = DefinitionLibrary(tmp_path / "never", KIND_COLLECTION)
    tree = lib.list_tree()
    assert tree.leaves == []


# ── tree-scan ────────────────────────────────────────────────────


def test_tree_mirrors_folder_structure(lib, collections_root):
    _make_file(collections_root, "Top Level")
    _make_file(collections_root, "Best Wildlife", subfolder="Wildlife")
    _make_file(collections_root, "Best Tropical", subfolder="Wildlife/Tropical")

    tree = lib.list_tree()
    assert {n.name for n in tree.folders} == {"Wildlife"}
    assert {l.name for l in tree.leaves} == {"Top Level"}

    wildlife = next(n for n in tree.folders if n.name == "Wildlife")
    assert {l.name for l in wildlife.leaves} == {"Best Wildlife"}
    assert {n.name for n in wildlife.folders} == {"Tropical"}


def test_tree_sorts_alphabetically_case_insensitive(lib, collections_root):
    """Folders and leaves sort alphabetically, case-blind, so the
    cascading menu reads predictably."""
    _make_file(collections_root, "zebra")
    _make_file(collections_root, "Antelope")
    _make_file(collections_root, "macaque")
    tree = lib.list_tree()
    assert [l.name for l in tree.leaves] == ["Antelope", "macaque", "zebra"]


# ── id lookup + name fallback ────────────────────────────────────


def test_by_id_finds_definition(lib, collections_root):
    given_id = new_definition_id()
    _make_file(collections_root, "Best Wildlife", id_=given_id)
    df = lib.by_id(given_id)
    assert df is not None
    assert df.name == "Best Wildlife"


def test_by_id_returns_none_for_unknown(lib):
    assert lib.by_id("never") is None


def test_by_name_finds_definition(lib, collections_root):
    _make_file(collections_root, "Best Wildlife")
    df = lib.by_name("Best Wildlife")
    assert df is not None


def test_resolve_prefers_id_over_name(lib, collections_root):
    """When id is set + matches, name is not consulted."""
    given_id = new_definition_id()
    _make_file(collections_root, "Real Name", id_=given_id)
    out = lib.resolve(definition_id=given_id, display_name="ignored")
    assert out is not None
    assert out.name == "Real Name"


def test_resolve_falls_back_to_name_when_id_unknown(lib, collections_root):
    """An id from a since-recreated file doesn't resolve; the name
    fallback finds the live file (spec/93 §4 contract)."""
    _make_file(collections_root, "Best Wildlife")
    out = lib.resolve(
        definition_id="stale-id-that-no-longer-exists",
        display_name="Best Wildlife")
    assert out is not None


# ── id backfill for hand-authored files ──────────────────────────


def test_hand_authored_file_gets_id_backfilled(lib, collections_root):
    """A file written by the user without an ``id`` gets one minted on
    next scan, and the file is rewritten so subsequent reads are
    id-anchored."""
    p = collections_root / "hand.json"
    p.write_text(json.dumps({
        "kind": KIND_COLLECTION,
        "payload": {"expr": [["+", "exported"]]},
    }), encoding="utf-8")
    lib.refresh()
    df = lib.by_name("hand")
    assert df is not None
    assert df.id, "id should have been backfilled"
    # Re-read from disk — the file now carries the id.
    blob = json.loads(p.read_text(encoding="utf-8"))
    assert blob["id"] == df.id


# ── OS-rename adoption ──────────────────────────────────────────


def test_os_rename_adopted_on_scan(lib, collections_root):
    """spec/93 §4: an OS-rename takes — the new filename becomes the
    in-memory display name, the id is unchanged, every referrer keeps
    resolving by id."""
    given_id = new_definition_id()
    p = _make_file(collections_root, "Original", id_=given_id)
    # User renames the file in their file manager (we simulate via
    # os.replace which is what the file manager would do).
    new_p = collections_root / "Renamed.json"
    os.replace(str(p), str(new_p))
    lib.refresh()
    df = lib.by_id(given_id)
    assert df is not None
    assert df.name == "Renamed"
    assert df.path == new_p


def test_os_move_to_subfolder_adopted_on_scan(lib, collections_root):
    """A move between folders is also adopted — id-anchored
    resolution doesn't care which folder the file lives in."""
    given_id = new_definition_id()
    p = _make_file(collections_root, "Best Wildlife", id_=given_id)
    sub = collections_root / "Wildlife"
    sub.mkdir()
    new_p = sub / "Best Wildlife.json"
    os.replace(str(p), str(new_p))
    lib.refresh()
    df = lib.by_id(given_id)
    assert df is not None
    assert df.path == new_p


# ── save / rename / delete ───────────────────────────────────────


def test_save_writes_file_and_updates_cache(lib):
    df = DefinitionFile(
        id="", name="New One", kind=KIND_COLLECTION,
        payload={"expr": [["+", "exported"]]},
    )
    saved = lib.save(df)
    assert saved.id, "save mints an id when none is set"
    assert saved.path is not None and saved.path.exists()
    # The cache now finds it.
    assert lib.by_id(saved.id) is not None


def test_save_into_subfolder(lib, collections_root):
    df = DefinitionFile(
        id="", name="Tropical", kind=KIND_COLLECTION, payload={},
    )
    saved = lib.save(df, subfolder=Path("Wildlife"))
    assert saved.path is not None
    assert saved.path.parent == collections_root / "Wildlife"


def test_save_rejects_kind_mismatch(lib):
    df = DefinitionFile(
        id="", name="Wrong Kind", kind=KIND_RECIPE, payload={},
    )
    with pytest.raises(ValueError):
        lib.save(df)


def test_rename_updates_filename_keeps_id(lib, collections_root):
    given_id = new_definition_id()
    _make_file(collections_root, "Before", id_=given_id)
    out = lib.rename(given_id, "After")
    assert out.id == given_id
    assert out.name == "After"
    assert out.path == collections_root / "After.json"
    assert not (collections_root / "Before.json").exists()


def test_rename_unknown_id_raises(lib):
    with pytest.raises(KeyError):
        lib.rename("never", "After")


def test_delete_removes_file(lib, collections_root):
    given_id = new_definition_id()
    p = _make_file(collections_root, "Doomed", id_=given_id)
    assert lib.delete(given_id) is True
    assert not p.exists()
    assert lib.by_id(given_id) is None


def test_delete_unknown_id_returns_false(lib):
    assert lib.delete("never") is False


# ── duplicate-display-name warning ──────────────────────────────


def test_duplicate_display_names_detected(lib, collections_root):
    """Two files with the same stem (different folders) → soft
    duplicate warning surfaces (§4 / §8 ``soft scan-on-save``)."""
    _make_file(collections_root, "Best Wildlife", subfolder="Wildlife")
    _make_file(collections_root, "Best Wildlife", subfolder="Travel")
    dups = lib.duplicate_display_names()
    assert dups == {"Best Wildlife": 2}


def test_no_duplicates_returns_empty_dict(lib, collections_root):
    _make_file(collections_root, "Only One")
    assert lib.duplicate_display_names() == {}


# ── corrupt file is skipped, not fatal ───────────────────────────


def test_corrupt_file_is_skipped(lib, collections_root):
    """A malformed JSON file is logged and skipped; the rest of the
    tree still loads (spec/93 §8: graceful failure)."""
    _make_file(collections_root, "Good")
    bad = collections_root / "broken.json"
    bad.write_text("{not json", encoding="utf-8")
    tree = lib.list_tree()
    assert any(l.name == "Good" for l in tree.leaves)
    assert all(l.name != "broken" for l in tree.leaves)
