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
# RecipeStore CRUD service (spec/90 §7 Phase 3)
# --------------------------------------------------------------------------- #


def _store(tmp_path):
    """Build a RecipeStore over a fresh user-store with a stable now()."""
    from mira.shared.recipe_store import RecipeStore
    import itertools as _it
    counter = _it.count(1)
    user_store = _make_store(tmp_path)
    rs = RecipeStore(
        user_store,
        now=lambda: NOW,
        new_id=lambda: f"rcp-{next(counter):03d}",
    )
    return rs, user_store


_BASIC_COMPOSITION = {
    "source": [["+", "exported"]],
    "filters": {"styles": ["macro"], "media_type": "photo"},
    "otherwise": "skip",
    "presentation": {"target_s": 90, "photo_s": 6.0, "card_style": "black"},
}


def test_recipe_store_create_returns_persisted_row(tmp_path):
    rs, us = _store(tmp_path)
    try:
        recipe = rs.create("short", "cut", _BASIC_COMPOSITION)
        assert recipe.id == "rcp-001"
        assert recipe.name == "short"
        assert recipe.flavour == "cut"
        assert recipe.created_at == NOW
        assert recipe.updated_at == NOW
        # The composition decodes back to the dict the caller passed.
        assert rs.composition(recipe) == _BASIC_COMPOSITION
        # And the row is actually persisted under that id.
        persisted = rs.get("rcp-001")
        assert persisted is not None
        assert persisted.name == "short"
    finally:
        us.close()


def test_recipe_store_create_serializes_composition_to_json(tmp_path):
    """The store owns JSON encoding; the user-store row carries the string."""
    rs, us = _store(tmp_path)
    try:
        recipe = rs.create("short", "cut", _BASIC_COMPOSITION)
        # The dataclass field is the JSON string (storage shape).
        assert isinstance(recipe.composition_json, str)
        # And the round-trip via json.loads matches the input dict.
        assert json.loads(recipe.composition_json) == _BASIC_COMPOSITION
    finally:
        us.close()


def test_recipe_store_create_rejects_empty_name(tmp_path):
    rs, us = _store(tmp_path)
    try:
        with pytest.raises(ValueError, match="name"):
            rs.create("", "cut", _BASIC_COMPOSITION)
        with pytest.raises(ValueError, match="name"):
            rs.create("   ", "cut", _BASIC_COMPOSITION)
    finally:
        us.close()


def test_recipe_store_create_rejects_unknown_flavour(tmp_path):
    rs, us = _store(tmp_path)
    try:
        with pytest.raises(ValueError, match="flavour"):
            rs.create("short", "mix", _BASIC_COMPOSITION)
    finally:
        us.close()


def test_recipe_store_create_raises_typed_error_on_collision(tmp_path):
    """``UNIQUE (flavour, name)`` surfaces as :class:`RecipeNameTakenError`
    — not a raw ``sqlite3.IntegrityError`` (spec/90 §7 Phase 3)."""
    from mira.shared.recipe_store import RecipeNameTakenError
    rs, us = _store(tmp_path)
    try:
        rs.create("short", "cut", _BASIC_COMPOSITION)
        with pytest.raises(RecipeNameTakenError) as exc:
            rs.create("short", "cut", _BASIC_COMPOSITION)
        assert exc.value.flavour == "cut"
        assert exc.value.name == "short"
    finally:
        us.close()


def test_recipe_store_create_same_name_other_flavour_allowed(tmp_path):
    """spec/90 §5.5 splits the namespace by flavour. A Cut Recipe + a
    Collection Recipe may share a name."""
    rs, us = _store(tmp_path)
    try:
        cut_r = rs.create("highlights", "cut", _BASIC_COMPOSITION)
        col_r = rs.create("highlights", "collection", _BASIC_COMPOSITION)
        assert cut_r.id != col_r.id
        assert rs.by_name("cut", "highlights").id == cut_r.id
        assert rs.by_name("collection", "highlights").id == col_r.id
    finally:
        us.close()


def test_recipe_store_update_partial_touches_updated_at(tmp_path):
    """``updated_at`` advances; ``created_at`` stays put. The store's
    injected ``now`` callable gives the test a deterministic clock."""
    from mira.shared.recipe_store import RecipeStore
    rs, us = _store(tmp_path)
    try:
        recipe = rs.create("short", "cut", _BASIC_COMPOSITION)
        original_created = recipe.created_at

        # Swap the clock to a later instant; update; assert updated_at
        # moved while created_at stayed.
        rs._now = lambda: "2026-06-21T08:00:00+00:00"
        next_composition = dict(_BASIC_COMPOSITION)
        next_composition["otherwise"] = "pick"
        rs.update(recipe.id, composition=next_composition)

        refreshed = rs.get(recipe.id)
        assert refreshed.created_at == original_created
        assert refreshed.updated_at == "2026-06-21T08:00:00+00:00"
        assert rs.composition(refreshed)["otherwise"] == "pick"
    finally:
        us.close()


def test_recipe_store_update_rename_is_supported(tmp_path):
    rs, us = _store(tmp_path)
    try:
        recipe = rs.create("short", "cut", _BASIC_COMPOSITION)
        rs.update(recipe.id, name="shorter")
        refreshed = rs.get(recipe.id)
        assert refreshed.name == "shorter"
        # Same id, but the (flavour, name) key now points to "shorter".
        assert rs.by_name("cut", "shorter") is not None
        assert rs.by_name("cut", "short") is None
    finally:
        us.close()


def test_recipe_store_update_rename_collision_raises(tmp_path):
    from mira.shared.recipe_store import RecipeNameTakenError
    rs, us = _store(tmp_path)
    try:
        rs.create("short", "cut", _BASIC_COMPOSITION)
        other = rs.create("medium", "cut", _BASIC_COMPOSITION)
        with pytest.raises(RecipeNameTakenError):
            rs.update(other.id, name="short")
    finally:
        us.close()


def test_recipe_store_update_same_name_is_a_noop(tmp_path):
    """Re-saving the same row with its own name doesn't trip the uniqueness
    pre-check (the row excludes itself from the collision scan)."""
    rs, us = _store(tmp_path)
    try:
        recipe = rs.create("short", "cut", _BASIC_COMPOSITION)
        # Same name + a fresh composition: should succeed.
        rs.update(recipe.id, name="short", composition={
            "source": [["+", "exported"]],
            "otherwise": "pick",
        })
        refreshed = rs.get(recipe.id)
        assert refreshed.name == "short"
        assert rs.composition(refreshed)["otherwise"] == "pick"
    finally:
        us.close()


def test_recipe_store_update_unknown_id_raises_keyerror(tmp_path):
    rs, us = _store(tmp_path)
    try:
        with pytest.raises(KeyError):
            rs.update("rcp-nope", name="x")
    finally:
        us.close()


def test_recipe_store_delete_is_idempotent(tmp_path):
    rs, us = _store(tmp_path)
    try:
        recipe = rs.create("short", "cut", _BASIC_COMPOSITION)
        rs.delete(recipe.id)
        assert rs.get(recipe.id) is None
        rs.delete(recipe.id)             # second delete is a no-op
    finally:
        us.close()


def test_recipe_store_by_name_returns_none_when_missing(tmp_path):
    rs, us = _store(tmp_path)
    try:
        assert rs.by_name("cut", "nope") is None
    finally:
        us.close()


def test_recipe_store_list_filter_by_flavour(tmp_path):
    rs, us = _store(tmp_path)
    try:
        rs.create("short", "cut", _BASIC_COMPOSITION)
        rs.create("long", "cut", _BASIC_COMPOSITION)
        rs.create("highlights", "collection", _BASIC_COMPOSITION)

        cuts = rs.list(flavour="cut")
        assert [r.name for r in cuts] == ["long", "short"]
        collections = rs.list(flavour="collection")
        assert [r.name for r in collections] == ["highlights"]
    finally:
        us.close()


def test_recipe_store_list_include_other_appends_cross_flavour(tmp_path):
    """spec/90 §5.5 — the dialog's opt-in "show Collection Recipes here too"
    setting surfaces cross-flavour entries AFTER the requested flavour."""
    rs, us = _store(tmp_path)
    try:
        rs.create("short", "cut", _BASIC_COMPOSITION)
        rs.create("highlights", "collection", _BASIC_COMPOSITION)
        rs.create("travel", "collection", _BASIC_COMPOSITION)

        mixed = rs.list(flavour="cut", include_other=True)
        assert [r.name for r in mixed] == ["short", "highlights", "travel"]
        assert [r.flavour for r in mixed] == ["cut", "collection", "collection"]

        # include_other=False (default) filters out the cross-flavour set.
        cuts_only = rs.list(flavour="cut", include_other=False)
        assert [r.name for r in cuts_only] == ["short"]
    finally:
        us.close()


def test_recipe_store_list_no_flavour_returns_all_sorted(tmp_path):
    rs, us = _store(tmp_path)
    try:
        rs.create("short", "cut", _BASIC_COMPOSITION)
        rs.create("highlights", "collection", _BASIC_COMPOSITION)
        rs.create("long", "cut", _BASIC_COMPOSITION)

        every = rs.list()
        # ORDER BY flavour, name — collections (h) first, then cuts (l, s).
        assert [r.flavour for r in every] == [
            "collection", "cut", "cut"]
        assert [r.name for r in every] == ["highlights", "long", "short"]
    finally:
        us.close()


def test_recipe_store_composition_is_tolerant(tmp_path):
    """A row with malformed JSON shouldn't crash readers — the helper
    coerces to ``{}``. Matches the spec/81 / spec/90 resolver's posture."""
    from mira.shared.recipe_store import RecipeStore
    rs, us = _store(tmp_path)
    try:
        # Bypass create() to write a row with intentionally-broken JSON
        # via the raw underlying upsert. We can't UNIQUE-collide because
        # the SQL CHECK constraint validates the JSON — so the row stays
        # well-formed under SQL but the helper still handles the edge.
        recipe = rs.create("short", "cut", _BASIC_COMPOSITION)
        # Force a malformed (but JSON-valid empty-array) blob to test
        # the tolerant fallback to {}.
        with us.transaction() as conn:
            conn.execute(
                "UPDATE recipe SET composition_json = '[]' WHERE id = ?",
                (recipe.id,))
        refreshed = rs.get(recipe.id)
        assert rs.composition(refreshed) == {}  # not a dict → {}
    finally:
        us.close()


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
    # v8 (spec/94 Phase 4a-ii) added the cross-event cut tables; drop
    # them so the migration chain re-creates them on the way back up.
    conn.execute("DROP TABLE cut_member")
    conn.execute("DROP TABLE cut")
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


# --------------------------------------------------------------------------- #
# spec/162 §9 Round 2c — recipe.scope column
# --------------------------------------------------------------------------- #


def test_recipe_default_scope_is_event(tmp_path):
    """spec/162 §9 — fresh recipes get scope='event' when the caller
    doesn't say otherwise. Pre-Round-3 callers save event-scope
    Recipes by default."""
    rs, us = _store(tmp_path)
    try:
        recipe = rs.create("event-only", "cut", _BASIC_COMPOSITION)
        assert recipe.scope == "event"
    finally:
        us.close()


def test_recipe_create_accepts_explicit_scope(tmp_path):
    """The caller can pin scope explicitly. Round 3's cross-event
    dialog surface will pass scope='cross-event'."""
    rs, us = _store(tmp_path)
    try:
        recipe = rs.create(
            "cross-event-one", "collection", _BASIC_COMPOSITION,
            scope="cross-event",
        )
        assert recipe.scope == "cross-event"
    finally:
        us.close()


def test_recipe_create_rejects_unknown_scope(tmp_path):
    rs, us = _store(tmp_path)
    try:
        with pytest.raises(ValueError, match="scope"):
            rs.create(
                "bad-scope", "cut", _BASIC_COMPOSITION,
                scope="everywhere",
            )
    finally:
        us.close()


def test_recipe_list_scope_filter(tmp_path):
    """spec/162 §6 — the Load Recipe picker filters by the current
    dialog scope. RecipeStore.list(scope=...) drives that."""
    rs, us = _store(tmp_path)
    try:
        rs.create("event-a", "cut", _BASIC_COMPOSITION)
        rs.create("event-b", "cut", _BASIC_COMPOSITION)
        rs.create(
            "cross-a", "collection", _BASIC_COMPOSITION,
            scope="cross-event",
        )
        rs.create(
            "cross-b", "collection", _BASIC_COMPOSITION,
            scope="cross-event",
        )

        event_only = rs.list(scope="event")
        assert [r.name for r in event_only] == ["event-a", "event-b"]

        cross_only = rs.list(scope="cross-event")
        assert [r.name for r in cross_only] == ["cross-a", "cross-b"]

        # Unfiltered returns everything.
        assert len(rs.list()) == 4
    finally:
        us.close()


def test_recipe_list_scope_rejects_unknown(tmp_path):
    rs, us = _store(tmp_path)
    try:
        with pytest.raises(ValueError, match="scope"):
            rs.list(scope="everywhere")
    finally:
        us.close()


def test_migrate_v9_to_v10_adds_scope_column_and_backfills(tmp_path):
    """spec/162 §9 — v9→v10 adds recipe.scope with a safe 'event'
    backfill. Every pre-existing row lands at scope='event' because
    pre-spec/162 cross-event composition had no Save-as-Recipe path.

    NOTE: the migration chain runs through v10→v11 too (spec/162 §2 —
    Round 2d.A) which sweeps every ``flavour='collection'`` row. To
    keep this test's assertion stable across future scope-only
    schema bumps, we only seed cut rows here — the collection-sweep
    behaviour has its own test below."""
    from mira.user_store import schema

    store = _make_store(tmp_path)
    conn = store.conn
    # Wind the store back to v9 shape: DROP the ix_recipe_scope index
    # first (SQLite refuses to drop a column referenced by an index),
    # then the column itself, then set the version.
    conn.execute("DROP INDEX IF EXISTS ix_recipe_scope")
    conn.execute("ALTER TABLE recipe DROP COLUMN scope")
    conn.execute("UPDATE schema_info SET schema_version = 9 WHERE id = 1")
    # Seed two cut rows only — the Round 2d.A migration that also runs
    # here would sweep away any collection rows we seeded.
    conn.execute(
        "INSERT INTO recipe (id, name, flavour, composition_json, "
        "created_at, updated_at) "
        "VALUES ('r1', 'legacy-cut', 'cut', '{}', ?, ?)", (NOW, NOW))
    conn.execute(
        "INSERT INTO recipe (id, name, flavour, composition_json, "
        "created_at, updated_at) "
        "VALUES ('r2', 'another-legacy-cut', 'cut', '{}', ?, ?)",
        (NOW, NOW))

    schema.migrate(conn)

    assert schema.get_version(conn) == schema.SCHEMA_VERSION
    # Every pre-v10 row backfilled to 'event'.
    rows = conn.execute(
        "SELECT id, scope FROM recipe ORDER BY id").fetchall()
    assert [dict(r) for r in rows] == [
        {"id": "r1", "scope": "event"},
        {"id": "r2", "scope": "event"},
    ]
    # The picker index landed on the migrated DB.
    idx_names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert "ix_recipe_scope" in idx_names
    store.close()


# --------------------------------------------------------------------------- #
# spec/162 §2 Round 2d.A — retire user-saved Collections
# --------------------------------------------------------------------------- #


def test_migrate_v10_to_v11_deletes_collection_flavour_rows(tmp_path):
    """spec/162 §2 — the migration sweeps every flavour='collection'
    row from the recipe table. Cut Recipes and every other table stay
    untouched."""
    from mira.user_store import schema

    store = _make_store(tmp_path)
    conn = store.conn
    # Wind back to v10 shape and seed a mixed set of rows so the DELETE
    # has both hits and non-hits to exercise.
    conn.execute("UPDATE schema_info SET schema_version = 10 WHERE id = 1")
    conn.execute(
        "INSERT INTO recipe (id, name, flavour, composition_json, "
        "scope, created_at, updated_at) "
        "VALUES ('r1', 'cut-keeper', 'cut', '{}', 'event', ?, ?)",
        (NOW, NOW))
    conn.execute(
        "INSERT INTO recipe (id, name, flavour, composition_json, "
        "scope, created_at, updated_at) "
        "VALUES ('r2', 'legacy-collection', 'collection', '{}', "
        "'event', ?, ?)",
        (NOW, NOW))
    conn.execute(
        "INSERT INTO recipe (id, name, flavour, composition_json, "
        "scope, created_at, updated_at) "
        "VALUES ('r3', 'another-collection', 'collection', '{}', "
        "'event', ?, ?)",
        (NOW, NOW))

    schema.migrate(conn)

    assert schema.get_version(conn) == schema.SCHEMA_VERSION
    surviving = [dict(r) for r in conn.execute(
        "SELECT id, flavour FROM recipe ORDER BY id").fetchall()]
    assert surviving == [{"id": "r1", "flavour": "cut"}]


def test_migrate_v10_to_v11_is_idempotent(tmp_path):
    """Second run on the migrated DB is a no-op — the DELETE finds
    nothing to remove."""
    from mira.user_store import schema

    store = _make_store(tmp_path)
    conn = store.conn
    conn.execute("UPDATE schema_info SET schema_version = 10 WHERE id = 1")
    conn.execute(
        "INSERT INTO recipe (id, name, flavour, composition_json, "
        "scope, created_at, updated_at) "
        "VALUES ('r1', 'legacy-collection', 'collection', '{}', "
        "'event', ?, ?)",
        (NOW, NOW))

    schema.migrate(conn)
    # First run cleared the collection row.
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM recipe").fetchone()["n"]
    assert count == 0
    # Second run explicitly re-invokes the migration step — must be a
    # no-op (SQLite `DELETE ... WHERE flavour='collection'` on an empty
    # or already-swept table affects zero rows and doesn't raise).
    from mira.user_store.schema import _migrate_v10_to_v11
    _migrate_v10_to_v11(conn)
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM recipe").fetchone()["n"]
    assert count == 0


# Note: ``dynamic_collection`` lives in event.db (per-event), not in
# the user_store's mira.db, so there's no user-store-side sanity test
# for its survival here. The user-store migration only touches the
# ``recipe`` table; the DC storage seam is a different code path.
