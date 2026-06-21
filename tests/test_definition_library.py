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


# ── spec/94 Phase 1b: slug-collision disambiguation ──────────────


def test_two_definitions_with_same_name_get_distinct_files(lib):
    """Two distinct definitions with the SAME display name don't
    overwrite each other on disk — the second gets a short
    id-fragment suffix appended to its filename."""
    a = DefinitionFile(
        id="aaaaaa-rest-of-uuid-a", name="Best Wildlife",
        kind=KIND_COLLECTION,
        payload={"expr": [["+", "exported"]]},
    )
    b = DefinitionFile(
        id="bbbbbb-rest-of-uuid-b", name="Best Wildlife",
        kind=KIND_COLLECTION,
        payload={"expr": [["+", "exported"]]},
    )
    saved_a = lib.save(a)
    saved_b = lib.save(b)
    assert saved_a.path != saved_b.path
    assert saved_a.path.exists()
    assert saved_b.path.exists()
    # Both round-trip by id (the load-bearing key).
    assert lib.by_id("aaaaaa-rest-of-uuid-a") is not None
    assert lib.by_id("bbbbbb-rest-of-uuid-b") is not None


def test_collision_suffix_uses_short_id_fragment(lib):
    """The suffix on the colliding file reads as a short id fragment
    in parentheses — keeps the slug human-readable and unmistakable."""
    a = DefinitionFile(
        id="0123456789abcdef" * 2, name="Best Wildlife",
        kind=KIND_COLLECTION, payload={},
    )
    b = DefinitionFile(
        id="fedcba9876543210" * 2, name="Best Wildlife",
        kind=KIND_COLLECTION, payload={},
    )
    lib.save(a)
    saved_b = lib.save(b)
    # The second file's stem contains the saving id's first 6 chars
    # parenthesised.
    assert "(fedcba)" in saved_b.path.stem


def test_case_only_collision_disambiguated(lib, collections_root):
    """``Best Wildlife.json`` and ``best wildlife.json`` collide on
    NTFS / APFS-default; the disambiguator catches it case-folded."""
    a = DefinitionFile(
        id="case-a-id-aaaaaaaa", name="Best Wildlife",
        kind=KIND_COLLECTION, payload={},
    )
    b = DefinitionFile(
        id="case-b-id-bbbbbbbb", name="best wildlife",
        kind=KIND_COLLECTION, payload={},
    )
    saved_a = lib.save(a)
    saved_b = lib.save(b)
    # The two stems must differ (regardless of case sensitivity of the
    # filesystem).
    assert saved_a.path.name.lower() != saved_b.path.name.lower()


def test_resave_owner_keeps_existing_path(lib, collections_root):
    """A re-save of the SAME definition uses the existing file's path
    (no spurious suffix), even when the in-memory ``df.path`` was
    cleared. This is how the dialog's Save-again flow works without
    creating ghost duplicates."""
    df_id = new_definition_id()
    first = DefinitionFile(
        id=df_id, name="Best Wildlife", kind=KIND_COLLECTION,
        payload={"expr": [["+", "exported"]]},
    )
    saved_first = lib.save(first)

    # Simulate a re-save: same id, same name, but fresh in-memory
    # object with path=None (the dialog rebuilds the DefinitionFile).
    second = DefinitionFile(
        id=df_id, name="Best Wildlife", kind=KIND_COLLECTION,
        payload={"expr": [["+", "exported"], ["-", "blurry"]]},
    )
    saved_second = lib.save(second)
    assert saved_second.path == saved_first.path
    # And only one file on disk.
    matching = list(collections_root.glob("Best Wildlife*.json"))
    assert len(matching) == 1


def test_rename_disambiguates_against_existing_target(
        lib, collections_root):
    """Renaming definition A to a name already owned by B → A's new
    file gets the id-fragment suffix; B's bare filename is preserved.
    """
    a_id = "renaming-id-aaaaaa"
    b_id = "stationary-id-bbb"
    a = DefinitionFile(
        id=a_id, name="Original A", kind=KIND_COLLECTION, payload={},
    )
    b = DefinitionFile(
        id=b_id, name="Target B", kind=KIND_COLLECTION, payload={},
    )
    lib.save(a)
    lib.save(b)
    # A renames to B's name.
    renamed_a = lib.rename(a_id, "Target B")
    assert renamed_a.id == a_id
    # B keeps its bare slug.
    b_after = lib.by_id(b_id)
    assert b_after is not None
    assert b_after.path.name == "Target B.json"
    # A's new file carries the suffix.
    assert "(" in renamed_a.path.stem


# ── spec/94 Phase 1b: reconcile-on-scan ─────────────────────────


def test_os_rename_picked_up_on_next_read(lib, collections_root):
    """After ``list_tree`` populates the cache, an out-of-band OS
    rename is picked up on the NEXT public read — no explicit
    ``refresh()`` call required."""
    given_id = new_definition_id()
    p = _make_file(collections_root, "Before", id_=given_id)
    # First read caches the tree.
    lib.list_tree()
    assert lib.by_id(given_id).name == "Before"

    # User renames the file in their file manager (between reads).
    new_p = collections_root / "After.json"
    os.replace(str(p), str(new_p))

    # The very next public read auto-reconciles — no manual refresh.
    df = lib.by_id(given_id)
    assert df is not None
    assert df.name == "After"
    assert df.path == new_p


def test_new_file_dropped_in_picked_up_on_next_read(lib, collections_root):
    """A hand-authored JSON file dropped into the folder between
    reads is picked up automatically."""
    _make_file(collections_root, "Existing")
    lib.list_tree()
    # User drops a new file in.
    _make_file(collections_root, "Hand-Authored")
    tree = lib.list_tree()
    names = {l.name for l in tree.leaves}
    assert {"Existing", "Hand-Authored"} <= names


def test_no_changes_skips_refresh(lib, collections_root):
    """When the folder signature is unchanged between reads, the
    cached tree is reused (we don't re-walk for free reads)."""
    _make_file(collections_root, "Stable")
    first_tree = lib.list_tree()
    # The second read should be the SAME object instance — no
    # re-scan happened.
    second_tree = lib.list_tree()
    assert first_tree is second_tree
