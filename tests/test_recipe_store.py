"""spec/90 Phase 1 — Recipe round-trip + schema invariants.

Logic-only (no Qt). The Recipe table is the saved Cut/Collection configuration
that lives at the library level (spec/90 §5.1). Phase 1 is substrate only —
no resolver, no dialog, no UI — so this exercises the storage shape only:

* every dataclass field round-trips through save_document → load_document;
* the ``flavour`` CHECK enum is pinned to {cut, collection};
* the ``composition_json`` ``json_valid`` CHECK rejects malformed JSON;
* the ``UNIQUE (flavour, name)`` constraint splits the namespace by flavour
  (Cut and Collection Recipes may share a name);
* the ``person`` table's new ``representative_face_id`` column round-trips
  (spec/90 §5.2 — the cross-store pointer is opaque TEXT).
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from mira.user_store import models as m
from mira.user_store.repo import UserStore


NOW = "2026-06-20T12:00:00+00:00"


def _make_store(tmp_path) -> UserStore:
    return UserStore.create(
        tmp_path / "mira.db",
        app_version="test",
        created_at=NOW,
    )


# --------------------------------------------------------------------------- #
# Round-trips
# --------------------------------------------------------------------------- #


def test_recipe_cut_flavour_roundtrip(tmp_path):
    """A Cut Recipe round-trips: every field survives upsert → get."""
    store = _make_store(tmp_path)
    composition = {
        "source": [["+", "exported"]],
        "rules": [
            {"predicate": [["+", {"kind": "cut", "tag": "best_wildlife"}]],
             "verdict": "pick"},
        ],
        "otherwise": "skip",
        "presentation": {"target_s": 90, "max_s": 300, "photo_s": 6.0,
                         "music_category": "happy", "card_style": "multi"},
    }
    store.upsert(m.Recipe(
        id="rcp-short",
        name="short",
        flavour="cut",
        composition_json=json.dumps(composition),
        created_at=NOW,
        updated_at=NOW,
    ))
    got = store.get(m.Recipe, "rcp-short")
    assert got is not None
    assert got.name == "short"
    assert got.flavour == "cut"
    assert json.loads(got.composition_json) == composition
    assert got.extras_json == '{}'
    store.close()


def test_recipe_collection_flavour_roundtrip(tmp_path):
    """A Collection Recipe round-trips: the full-section composition fits in
    the same opaque JSON envelope."""
    store = _make_store(tmp_path)
    composition = {
        "scope": [["+", {"kind": "event_collection", "tag": "adventure_events"}]],
        "source": [["+", "exported"]],
        "filters": {"styles": ["macro"], "camera_ids": ["Panasonic+DC-G9M2"],
                    "person_ids": ["person-1"]},
        "rules": [],
        "otherwise": "pick",
        "presentation": {"target_s": 600},
    }
    store.upsert(m.Recipe(
        id="rcp-curated",
        name="all_time_best_macro",
        flavour="collection",
        composition_json=json.dumps(composition),
        created_at=NOW,
        updated_at=NOW,
    ))
    got = store.get(m.Recipe, "rcp-curated")
    assert got.flavour == "collection"
    assert json.loads(got.composition_json)["filters"]["person_ids"] == ["person-1"]
    store.close()


def test_recipe_listed_in_order(tmp_path):
    """``UserStore.all`` orders rows by the registry ``order_by`` so the
    UI's "Load Recipe…" list is deterministic. The Recipe registry sorts
    by (flavour, name, id)."""
    store = _make_store(tmp_path)
    for ident, name, flavour in [
        ("r3", "highlights", "collection"),
        ("r1", "short", "cut"),
        ("r2", "long", "cut"),
    ]:
        store.upsert(m.Recipe(
            id=ident, name=name, flavour=flavour,
            composition_json='{}',
            created_at=NOW, updated_at=NOW,
        ))
    listed = [(r.flavour, r.name) for r in store.all(m.Recipe)]
    assert listed == [
        ("collection", "highlights"),
        ("cut", "long"),
        ("cut", "short"),
    ]
    store.close()


# --------------------------------------------------------------------------- #
# CHECK + UNIQUE invariants
# --------------------------------------------------------------------------- #


def test_recipe_flavour_check_rejects_unknown(tmp_path):
    """``flavour`` CHECK pins the closed enum {cut, collection}."""
    store = _make_store(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO recipe (id, name, flavour, composition_json, "
            "created_at, updated_at) "
            "VALUES ('r1', 'X', 'mix', '{}', 't', 't')"
        )
    store.close()


def test_recipe_composition_json_must_be_valid_json(tmp_path):
    """``composition_json`` is the only payload column; ``json_valid`` CHECK
    keeps malformed input out at the SQL layer."""
    store = _make_store(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO recipe (id, name, flavour, composition_json, "
            "created_at, updated_at) "
            "VALUES ('r1', 'X', 'cut', '{not json', 't', 't')"
        )
    store.close()


def test_recipe_name_unique_within_flavour(tmp_path):
    """``UNIQUE (flavour, name)`` — same name in the same flavour collides;
    same name across flavours is fine (spec/90 §5.5 splits the namespace)."""
    store = _make_store(tmp_path)
    store.upsert(m.Recipe(
        id="r1", name="short", flavour="cut",
        composition_json='{}',
        created_at=NOW, updated_at=NOW,
    ))
    # Same name, same flavour, different id ⇒ UNIQUE violation.
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO recipe (id, name, flavour, composition_json, "
            "created_at, updated_at) "
            "VALUES ('r2', 'short', 'cut', '{}', 't', 't')"
        )
    # Same name, OTHER flavour ⇒ allowed (Cut + Collection share the name).
    store.upsert(m.Recipe(
        id="r3", name="short", flavour="collection",
        composition_json='{}',
        created_at=NOW, updated_at=NOW,
    ))
    flavours = {r.flavour for r in store.all(m.Recipe) if r.name == "short"}
    assert flavours == {"cut", "collection"}
    store.close()


# --------------------------------------------------------------------------- #
# Person — the spec/90 §5.2 column addition
# --------------------------------------------------------------------------- #


def test_person_representative_face_id_roundtrips(tmp_path):
    """spec/90 §5.2 — Person gains ``representative_face_id``, an opaque
    pointer to a ``face`` row in event.db. The cross-store reference
    carries no FK; round-trip preserves the string verbatim."""
    store = _make_store(tmp_path)
    store.upsert(m.Person(
        id="person-1",
        display_name="Pedro",
        representative_face_id="face-abc-123",
        created_at=NOW,
        updated_at=NOW,
    ))
    got = store.get(m.Person, "person-1")
    assert got.representative_face_id == "face-abc-123"
    # Default stays None for Persons created before recognition runs.
    store.upsert(m.Person(
        id="person-2",
        display_name="Maria",
        created_at=NOW,
        updated_at=NOW,
    ))
    assert store.get(m.Person, "person-2").representative_face_id is None
    store.close()


# --------------------------------------------------------------------------- #
# Migration v6 -> v7
# --------------------------------------------------------------------------- #


def test_migrate_v6_to_v7_adds_recipe_event_collection_and_person_column(tmp_path):
    """v6→v7 (spec/90 Phase 1) creates ``recipe`` + ``event_collection`` as
    NEW tables (full CHECK + index complement intact, unlike ALTER) and
    ALTERs ``person`` to add ``representative_face_id``. Pre-existing rows
    survive verbatim."""
    from mira.user_store import schema

    store = _make_store(tmp_path)
    conn = store.conn
    # Reconstruct the v6 shape: drop the new tables + column, pin the version.
    conn.execute("DROP TABLE recipe")
    conn.execute("DROP TABLE event_collection")
    conn.execute("ALTER TABLE person DROP COLUMN representative_face_id")
    conn.execute("UPDATE schema_info SET schema_version = 6 WHERE id = 1")
    # Seed a Person row that should survive the migration.
    conn.execute(
        "INSERT INTO person (id, display_name, created_at, updated_at) "
        "VALUES ('p1', 'Pedro', ?, ?)", (NOW, NOW))

    schema.migrate(conn)

    assert schema.get_version(conn) == schema.SCHEMA_VERSION
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "recipe" in names and "event_collection" in names
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(person)")}
    assert "representative_face_id" in cols
    # CHECK survived: invalid flavour rejected on the migrated table.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO recipe (id, name, flavour, composition_json, "
            "created_at, updated_at) "
            "VALUES ('r1', 'X', 'mix', '{}', 't', 't')")
    # Pre-existing Person row survived with NULL on the new column.
    row = conn.execute(
        "SELECT display_name, representative_face_id FROM person WHERE id = 'p1'"
    ).fetchone()
    assert row["display_name"] == "Pedro"
    assert row["representative_face_id"] is None
    store.close()
