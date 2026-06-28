"""Spec/94 Phase 1b — end-to-end behaviour of the Gateway's wired
:class:`LibraryGateway` (JSON tree as the single live source).

``Gateway.library_gateway()`` constructs a LibraryGateway whose DC
methods route through ``collections_library`` instead of
``mira.db.saved_filter``. These tests pin the contract: writes land
as JSON files, reads see them, the SQL table stays empty, and there
are no duplicates."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mira.gateway.gateway import Gateway
from mira.settings.repo import SettingsRepo
from mira.gateway.index import EventsIndex
from mira.user_store import models as um


@pytest.fixture
def isolated_gateway(tmp_path: Path, monkeypatch):
    """A Gateway pointing at ``tmp_path`` for everything — settings,
    index, user_store, and library subtrees."""
    # All cross-event data lives under tmp_path via MIRA_DATA_DIR so
    # the lazy properties + the library subtrees land where the test
    # can inspect them.
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))

    settings = SettingsRepo(path=tmp_path / "settings.json")
    index = EventsIndex(path=tmp_path / "events_index.json")
    g = Gateway(
        settings=settings,
        index=index,
        user_store_path=tmp_path / "mira.db",
    )
    yield g, tmp_path
    g.close()


def test_create_dc_lands_as_json_file(isolated_gateway):
    """``library_gateway().create_dc(...)`` writes a JSON file under
    ``<library_root>/Collections/`` — the legacy SQL table never sees
    the row."""
    g, root = isolated_gateway
    lg = g.library_gateway()
    sf = lg.create_dc(
        "best_wildlife",
        expr=[["+", "exported"]],
        filters={"styles": ["wildlife"]},
        description="hand-picked wildlife",
    )
    assert sf.id
    # JSON file landed.
    files = list((root / "Collections").rglob("*.json"))
    assert len(files) == 1
    blob = json.loads(files[0].read_text(encoding="utf-8"))
    assert blob["id"] == sf.id
    assert blob["kind"] == "collection"
    assert blob["payload"]["expr"] == [["+", "exported"]]
    assert blob["payload"]["filters"] == {"styles": ["wildlife"]}
    assert blob["payload"]["description"] == "hand-picked wildlife"
    # The SQL table stays empty.
    rows = g.user_store.query_raw(um.SavedFilter, "SELECT * FROM saved_filter")
    assert rows == []


def test_dynamic_collections_reads_json_tree(isolated_gateway):
    """The read path returns the JSON-backed definitions, projected
    into ``SavedFilter`` dataclasses so existing callers keep working."""
    g, root = isolated_gateway
    lg = g.library_gateway()
    lg.create_dc("alpha", expr=[["+", "exported"]])
    lg.create_dc("beta", expr=[["+", "picked"]])
    rows = lg.dynamic_collections()
    tags = sorted(r.tag for r in rows)
    assert tags == ["alpha", "beta"]


def test_update_dc_round_trip(isolated_gateway):
    """update_dc rewrites the JSON; subsequent reads see the new
    expr."""
    g, _root = isolated_gateway
    lg = g.library_gateway()
    sf = lg.create_dc("changing", expr=[["+", "exported"]])
    lg.update_dc(sf.id, expr=[["+", "exported"], ["-", "blurry"]])
    refreshed = lg.dynamic_collection(sf.id)
    assert refreshed is not None
    assert json.loads(refreshed.expr_json) == [["+", "exported"], ["-", "blurry"]]


def test_rename_dc_round_trip(isolated_gateway):
    """rename_dc renames the JSON file; lookup by new tag finds it."""
    g, root = isolated_gateway
    lg = g.library_gateway()
    sf = lg.create_dc("original", expr=[["+", "exported"]])
    lg.rename_dc(sf.id, "renamed")
    found = lg.dc_by_tag("renamed")
    assert found is not None
    assert found.id == sf.id
    assert lg.dc_by_tag("original") is None
    # The file on disk now carries the new name.
    files = list((root / "Collections").rglob("*.json"))
    assert any(p.stem == "renamed" for p in files)


def test_delete_dc_removes_file(isolated_gateway):
    g, root = isolated_gateway
    lg = g.library_gateway()
    sf = lg.create_dc("doomed", expr=[["+", "exported"]])
    lg.delete_dc(sf.id)
    assert lg.dynamic_collection(sf.id) is None
    assert list((root / "Collections").rglob("*.json")) == []


def test_no_duplicates_between_homes(isolated_gateway):
    """Spec/94 1b's hard invariant: the same definition never appears
    in two homes. A direct SQL insert + a JSON write of the same id
    would be a duplicate state — but the live app's write path goes
    through the gateway, which writes JSON only."""
    g, _root = isolated_gateway
    lg = g.library_gateway()
    sf = lg.create_dc("solo", expr=[["+", "exported"]])
    # SQL table is empty.
    sql_rows = g.user_store.query_raw(um.SavedFilter, "SELECT * FROM saved_filter")
    assert sql_rows == []
    # JSON tree has exactly one.
    rows = lg.dynamic_collections()
    assert len(rows) == 1
    assert rows[0].id == sf.id


def test_dual_home_migration_runs_at_first_access(
        isolated_gateway, tmp_path):
    """When mira.db.saved_filter has pre-existing rows (legacy install),
    the first access to ``library_gateway`` migrates them into JSON +
    deletes the SQL rows."""
    g, root = isolated_gateway

    # Bypass library_gateway() and write directly to SQL (simulating
    # legacy state — what an existing user's mira.db carries).
    legacy = um.SavedFilter(
        id="legacy-1", tag="legacy_dc",
        created_at="2026-06-20T00:00:00Z",
        updated_at="2026-06-20T00:00:00Z",
        expr_json="[[\"+\", \"exported\"]]",
        filters_json="{\"styles\": [\"macro\"]}",
    )
    g.user_store.upsert(legacy)
    # Sanity: row is in SQL.
    assert len(g.user_store.query_raw(um.SavedFilter, "SELECT * FROM saved_filter")) == 1

    # Access library_gateway() — the lazy property builds the
    # libraries + runs the dual-home migration on first touch.
    lg = g.library_gateway()
    rows = lg.dynamic_collections()
    # The row is now visible via the library_gateway (it migrated to JSON).
    assert any(r.id == "legacy-1" for r in rows)
    # SQL is empty.
    assert g.user_store.query_raw(um.SavedFilter, "SELECT * FROM saved_filter") == []
    # Marker file written.
    assert (root / ".mira" / "dual_home_migration.json").is_file()


def test_recipe_store_writes_into_recipes_tree(isolated_gateway):
    """``Gateway.recipe_store()`` writes Recipes as JSON files; the
    SQL ``recipe`` table stays empty."""
    g, root = isolated_gateway
    rs = g.recipe_store()
    r = rs.create("short cut", "cut", {"source": [["+", "exported"]]})
    files = list((root / "Recipes").rglob("*.json"))
    assert len(files) == 1
    blob = json.loads(files[0].read_text(encoding="utf-8"))
    assert blob["id"] == r.id
    assert blob["payload"]["flavour"] == "cut"
    # SQL table empty.
    assert g.user_store.query_raw(um.Recipe, "SELECT * FROM recipe") == []


def test_recipe_store_list_filters_by_flavour(isolated_gateway):
    g, _root = isolated_gateway
    rs = g.recipe_store()
    rs.create("alpha", "cut", {"source": [["+", "exported"]]})
    rs.create("alpha-coll", "collection", {"source": [["+", "exported"]]})
    cuts = rs.list(flavour="cut")
    assert [r.name for r in cuts] == ["alpha"]
    collections = rs.list(flavour="collection")
    assert [r.name for r in collections] == ["alpha-coll"]


def test_recipe_store_unique_collision_raises(isolated_gateway):
    """Two Cut Recipes with the same name → typed
    :class:`RecipeNameTakenError` on the second create."""
    from mira.shared.recipe_store import RecipeNameTakenError
    g, _root = isolated_gateway
    rs = g.recipe_store()
    rs.create("name", "cut", {"source": [["+", "exported"]]})
    with pytest.raises(RecipeNameTakenError):
        rs.create("name", "cut", {"source": [["+", "exported"]]})
    # Same name, OTHER flavour, is fine — spec/90 §5.5 splits the
    # namespace by flavour.
    rs.create("name", "collection", {"source": [["+", "exported"]]})


def test_dual_home_migration_runs_once_per_session(
        isolated_gateway):
    """A second access to ``library_gateway`` doesn't re-run the
    migration (the marker file gates it). Adding a SQL row AFTER
    the first access stays in SQL — the live app's write path is
    through library_gateway, so this scenario is theoretical, but
    the test pins the contract."""
    g, root = isolated_gateway
    # First access — marker written.
    g.library_gateway()
    assert (root / ".mira" / "dual_home_migration.json").is_file()
    # Now drop a fresh SQL row in.
    g.user_store.upsert(um.SavedFilter(
        id="late-1", tag="late",
        created_at="2026-06-21T00:00:00Z",
        updated_at="2026-06-21T00:00:00Z",
    ))
    # Second access — marker short-circuits the migration, the
    # row stays in SQL untouched.
    g.library_gateway()
    assert len(g.user_store.query_raw(um.SavedFilter, "SELECT * FROM saved_filter")) == 1


def _seed_two_items(g):
    """One wildlife + one macro exported item across two events."""
    g.user_store.upsert(um.GlobalItem(
        event_uuid="A", item_id="a1", synced_at="2026-06-21T00:00:00Z",
        event_name="Alpha", kind="photo", classification="wildlife",
        has_export=True, export_relpath="A/a1.jpg",
        capture_time="2026-04-01T10:00:00"))
    g.user_store.upsert(um.GlobalItem(
        event_uuid="B", item_id="b1", synced_at="2026-06-21T00:00:00Z",
        event_name="Beta", kind="photo", classification="macro",
        has_export=True, export_relpath="B/b1.jpg",
        capture_time="2026-04-02T10:00:00"))


def test_dc_operand_resolves_from_json_tree(isolated_gateway):
    """Regression (spec/94 Phase 1b): a Collection referenced as a *source
    operand* must resolve through the JSON-tree library, not the empty
    ``saved_filter`` table. Before the fix the resolver's ``dc_by_ref`` read
    the SQL table and a DC operand resolved to the empty set — so pinning a
    Collection reported "zero items"."""
    g, _root = isolated_gateway
    lg = g.library_gateway()
    _seed_two_items(g)
    sf = lg.create_dc(
        "wild", expr=[["+", "exported"]], filters={"styles": ["wildlife"]})
    # SQL table is empty — the Collection lives only in the JSON tree.
    assert g.user_store.query_raw(
        um.SavedFilter, "SELECT * FROM saved_filter") == []

    by_id = lg.resolve_dc_keys([["+", {"kind": "dc", "id": sf.id}]], {})
    by_tag = lg.resolve_dc_keys([["+", {"kind": "dc", "tag": sf.tag}]], {})
    direct = lg.resolve_dc_keys(lg.dc_expr(sf), lg.dc_filters(sf))
    # The operand applies the Collection's own style=wildlife filter → just A.
    assert by_id == by_tag == direct
    assert {k.split("::", 1)[0] for k in by_id} == {"A"}


def test_missing_dc_operand_shrinks_gracefully(isolated_gateway):
    """A reference to a deleted Collection contributes the empty set rather
    than raising — same graceful-shrink contract as before the JSON-tree
    swap."""
    g, _root = isolated_gateway
    lg = g.library_gateway()
    _seed_two_items(g)
    assert lg.resolve_dc_keys([["+", {"kind": "dc", "id": "gone"}]], {}) == []
