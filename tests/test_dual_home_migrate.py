"""Tests for ``core.dual_home_migrate`` — spec/94 Phase 1b one-shot
move of mira.db.saved_filter + mira.db.recipe rows into the JSON
tree."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

import pytest

from core.definition_files import (
    KIND_COLLECTION,
    KIND_RECIPE,
    DefinitionFile,
    new_definition_id,
)
from core.dual_home_migrate import (
    MARKER_FILENAME,
    DualHomeMigrationReport,
    marker_path,
    migrate_dual_homes,
)
from mira.shared.definition_library import DefinitionLibrary


# ── fixtures: row dataclasses + a fake SQL store ─────────────────


@dataclass
class FakeSavedFilter:
    id: str
    tag: str
    expr_json: str = "[]"
    filters_json: str = "{}"
    description: Optional[str] = None
    created_at: str = "2026-06-21T00:00:00Z"
    updated_at: str = "2026-06-21T00:00:00Z"


@dataclass
class FakeRecipe:
    id: str
    name: str
    flavour: str
    composition_json: str = "{}"
    created_at: str = "2026-06-21T00:00:00Z"
    updated_at: str = "2026-06-21T00:00:00Z"


class _FakeSqlStore:
    """An in-memory stand-in for the user store's saved_filter +
    recipe tables. Lets the migration tests exercise the delete
    callbacks without spinning a real SQLite."""

    def __init__(
        self, saved_filters: List[FakeSavedFilter],
        recipes: List[FakeRecipe],
    ) -> None:
        self._dcs = {sf.id: sf for sf in saved_filters}
        self._recipes = {r.id: r for r in recipes}

    def saved_filters(self) -> List[FakeSavedFilter]:
        return list(self._dcs.values())

    def recipes(self) -> List[FakeRecipe]:
        return list(self._recipes.values())

    def delete_saved_filter(self, dc_id: str) -> None:
        self._dcs.pop(dc_id, None)

    def delete_recipe(self, recipe_id: str) -> None:
        self._recipes.pop(recipe_id, None)


@pytest.fixture
def library_root(tmp_path):
    (tmp_path / ".mira").mkdir(parents=True)
    (tmp_path / "Collections").mkdir(parents=True)
    (tmp_path / "Recipes").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def collections_lib(library_root):
    return DefinitionLibrary(library_root / "Collections", KIND_COLLECTION)


@pytest.fixture
def recipes_lib(library_root):
    return DefinitionLibrary(library_root / "Recipes", KIND_RECIPE)


def _run(library_root, *, sql, collections_lib, recipes_lib):
    return migrate_dual_homes(
        library_root,
        saved_filter_rows=sql.saved_filters(),
        recipe_rows=sql.recipes(),
        collections_library=collections_lib,
        recipes_library=recipes_lib,
        delete_saved_filter=sql.delete_saved_filter,
        delete_recipe=sql.delete_recipe,
    )


# ── happy path ────────────────────────────────────────────────────


def test_migrates_saved_filter_into_collections_tree(
        library_root, collections_lib, recipes_lib):
    """A saved_filter row lands as a JSON file with the same id +
    tag-as-display-name. The SQL row is deleted."""
    sf = FakeSavedFilter(
        id="dc-1", tag="best_macro",
        expr_json="[[\"+\", \"exported\"]]",
        filters_json="{\"styles\": [\"macro\"]}",
    )
    sql = _FakeSqlStore(saved_filters=[sf], recipes=[])

    report = _run(library_root, sql=sql,
                  collections_lib=collections_lib, recipes_lib=recipes_lib)
    assert report.skipped is False
    assert report.migrated_collections == 1
    assert report.migrated_recipes == 0

    # JSON written.
    df = collections_lib.by_id("dc-1")
    assert df is not None
    assert df.name == "best_macro"
    assert df.payload["expr"] == [["+", "exported"]]
    assert df.payload["filters"] == {"styles": ["macro"]}
    # SQL row gone.
    assert sql.saved_filters() == []


def test_migrates_recipe_with_flavour_in_payload(
        library_root, collections_lib, recipes_lib):
    """A recipe row's ``flavour`` rides on the JSON payload so the
    library-level RecipeStore can filter by it (spec/90 §5.5)."""
    r = FakeRecipe(
        id="r-1", name="short cut",
        flavour="cut",
        composition_json=json.dumps({"source": [["+", "exported"]]}),
    )
    sql = _FakeSqlStore(saved_filters=[], recipes=[r])
    _run(library_root, sql=sql,
         collections_lib=collections_lib, recipes_lib=recipes_lib)

    df = recipes_lib.by_id("r-1")
    assert df is not None
    assert df.payload["flavour"] == "cut"
    assert df.payload["source"] == [["+", "exported"]]
    assert sql.recipes() == []


def test_migrates_both_tables_together(
        library_root, collections_lib, recipes_lib):
    sql = _FakeSqlStore(
        saved_filters=[
            FakeSavedFilter(id="dc-1", tag="a"),
            FakeSavedFilter(id="dc-2", tag="b"),
        ],
        recipes=[
            FakeRecipe(id="r-1", name="x", flavour="cut"),
        ],
    )
    report = _run(library_root, sql=sql,
                  collections_lib=collections_lib, recipes_lib=recipes_lib)
    assert report.migrated_collections == 2
    assert report.migrated_recipes == 1
    assert len(list((library_root / "Collections").glob("*.json"))) == 2
    assert len(list((library_root / "Recipes").glob("*.json"))) == 1


# ── marker / idempotency ──────────────────────────────────────────


def test_marker_written_after_successful_run(
        library_root, collections_lib, recipes_lib):
    """The marker file lives at ``<root>/.mira/dual_home_migration.json``
    after the first successful run."""
    sql = _FakeSqlStore(saved_filters=[], recipes=[])
    _run(library_root, sql=sql,
         collections_lib=collections_lib, recipes_lib=recipes_lib)
    marker = marker_path(library_root)
    assert marker.is_file()
    blob = json.loads(marker.read_text(encoding="utf-8"))
    assert blob["schema_version"] == 1
    assert "migrated_at" in blob


def test_marker_skips_subsequent_runs(
        library_root, collections_lib, recipes_lib):
    """Second invocation sees the marker → no-op, reports skipped=True
    and leaves freshly-added SQL rows alone (the user can manually
    drop the marker if they want to re-run)."""
    sql = _FakeSqlStore(saved_filters=[FakeSavedFilter(id="dc-1", tag="a")],
                        recipes=[])
    _run(library_root, sql=sql,
         collections_lib=collections_lib, recipes_lib=recipes_lib)
    assert sql.saved_filters() == []

    # Drop a fresh SQL row in (the kind of thing that can't happen in
    # production once the live writes route through the JSON tree, but
    # it sharpens the contract — marker means "we're done; ignore SQL").
    sql._dcs["dc-late"] = FakeSavedFilter(id="dc-late", tag="late")

    second = _run(library_root, sql=sql,
                  collections_lib=collections_lib, recipes_lib=recipes_lib)
    assert second.skipped is True
    # The new row was NOT migrated (the marker short-circuited).
    assert "dc-late" in {sf.id for sf in sql.saved_filters()}


def test_empty_tables_still_write_marker(
        library_root, collections_lib, recipes_lib):
    """A first run with NOTHING to migrate still writes the marker —
    subsequent installs never re-walk the empty tables."""
    sql = _FakeSqlStore(saved_filters=[], recipes=[])
    report = _run(library_root, sql=sql,
                  collections_lib=collections_lib, recipes_lib=recipes_lib)
    assert report.skipped is False
    assert marker_path(library_root).is_file()


# ── round-trip + no duplicates ────────────────────────────────────


def test_no_duplicates_after_migration(
        library_root, collections_lib, recipes_lib):
    """After migration, the JSON tree has each id exactly once — no
    accidental file-vs-row double home."""
    sf = FakeSavedFilter(id="dc-1", tag="only_one",
                          expr_json="[[\"+\", \"exported\"]]")
    sql = _FakeSqlStore(saved_filters=[sf], recipes=[])
    _run(library_root, sql=sql,
         collections_lib=collections_lib, recipes_lib=recipes_lib)
    # One JSON file, no SQL row.
    files = list((library_root / "Collections").rglob("*.json"))
    assert len(files) == 1
    assert sql.saved_filters() == []


def test_round_trip_preserves_id_and_payload(
        library_root, collections_lib, recipes_lib):
    """The migrated definition resolves by its original id (so any
    Cuts already pinned from it keep their source_link valid)."""
    original_id = "stable-id-aaaaaa"
    sf = FakeSavedFilter(
        id=original_id, tag="wildlife",
        expr_json="[[\"+\", \"exported\"], [\"-\", \"blurry\"]]",
        filters_json="{\"styles\": [\"wildlife\"]}",
    )
    sql = _FakeSqlStore(saved_filters=[sf], recipes=[])
    _run(library_root, sql=sql,
         collections_lib=collections_lib, recipes_lib=recipes_lib)

    df = collections_lib.by_id(original_id)
    assert df is not None
    assert df.payload["expr"] == [["+", "exported"], ["-", "blurry"]]
    assert df.payload["filters"]["styles"] == ["wildlife"]


# ── failure modes ────────────────────────────────────────────────


def test_json_write_failure_keeps_sql_row(
        library_root, collections_lib, recipes_lib, monkeypatch):
    """If the JSON write fails, the SQL row is NOT deleted — leave the
    legacy home in place so a retry has the data."""
    sf = FakeSavedFilter(id="dc-1", tag="x")
    sql = _FakeSqlStore(saved_filters=[sf], recipes=[])

    def _explode(_df, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(collections_lib, "save", _explode)

    report = _run(library_root, sql=sql,
                  collections_lib=collections_lib, recipes_lib=recipes_lib)
    assert report.migrated_collections == 0
    # SQL row preserved.
    assert {sf.id for sf in sql.saved_filters()} == {"dc-1"}
    # Marker still written — the migration ran, just empty.
    assert marker_path(library_root).is_file()


def test_recipe_with_malformed_composition_logs_and_continues(
        library_root, collections_lib, recipes_lib):
    """A recipe with non-JSON composition gets logged + skipped; the
    surrounding rows still migrate."""
    good = FakeRecipe(id="r-good", name="ok", flavour="cut",
                       composition_json="{\"source\": [[\"+\", \"exported\"]]}")
    bad = FakeRecipe(id="r-bad", name="garbled", flavour="cut",
                      composition_json="not valid json")
    sql = _FakeSqlStore(saved_filters=[], recipes=[good, bad])
    report = _run(library_root, sql=sql,
                  collections_lib=collections_lib, recipes_lib=recipes_lib)
    # Even the malformed payload migrates (parse falls back to {} and
    # we don't lose the row).
    assert report.migrated_recipes == 2
    # The good one round-trips with its expr.
    assert recipes_lib.by_id("r-good").payload["source"] == \
        [["+", "exported"]]
    # The bad one's payload defaulted to {"flavour": "cut"}.
    assert recipes_lib.by_id("r-bad").payload == {"flavour": "cut"}


def test_partial_failure_safety_on_recipe_writes(
        library_root, collections_lib, recipes_lib, monkeypatch):
    """If one recipe write fails mid-batch, the others still migrate
    and the SQL row for the failing one is preserved."""
    a = FakeRecipe(id="r-a", name="alpha", flavour="cut")
    b = FakeRecipe(id="r-b", name="beta", flavour="cut")
    sql = _FakeSqlStore(saved_filters=[], recipes=[a, b])

    original_save = recipes_lib.save
    state = {"saw_a": False}

    def _flaky(df, **kw):
        if df.id == "r-a" and not state["saw_a"]:
            state["saw_a"] = True
            raise OSError("transient")
        return original_save(df, **kw)

    monkeypatch.setattr(recipes_lib, "save", _flaky)

    report = _run(library_root, sql=sql,
                  collections_lib=collections_lib, recipes_lib=recipes_lib)
    assert report.migrated_recipes == 1                # only b made it
    assert {r.id for r in sql.recipes()} == {"r-a"}    # a survived
