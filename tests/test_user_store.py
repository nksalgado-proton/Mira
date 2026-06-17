"""Tests for the user-level data store — spec/53.

Logic-only (no Qt). Covers: schema init + pragmas + version (typed
``schema_info``), generic typed CRUD, the ``query_by`` SQL-WHERE primitive,
transactions (commit + rollback), and the SCHEMA_VERSION + MIGRATIONS
invariant.

Mirrors :mod:`tests.test_store` for the per-event store — same pattern, same
discipline.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from mira.user_store import models as m, schema
from mira.user_store.repo import UserStore


def _make_store(tmp_path) -> UserStore:
    return UserStore.create(
        tmp_path / "mira.db",
        app_version="test",
        created_at="2026-06-08T00:00:00+00:00",
    )


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #


def test_schema_version_self_consistent():
    """SCHEMA_VERSION and MIGRATIONS stay in lockstep — every bump appends one
    migration step. v1 (greenfield) has zero migration entries."""
    assert schema.SCHEMA_VERSION >= 1
    assert len(schema.MIGRATIONS) == schema.SCHEMA_VERSION - 1


def test_create_sets_version_and_schema_info(tmp_path):
    store = _make_store(tmp_path)
    assert schema.get_version(store.conn) == schema.SCHEMA_VERSION
    info = schema.get_schema_info(store.conn)
    assert info["app_version"] == "test"
    assert info["created_at"] == "2026-06-08T00:00:00+00:00"
    assert info["updated_at"] == "2026-06-08T00:00:00+00:00"
    store.close()


def test_pragmas_applied(tmp_path):
    store = _make_store(tmp_path)
    assert store.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert store.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    store.close()


def test_initialize_twice_raises(tmp_path):
    store = _make_store(tmp_path)
    with pytest.raises(RuntimeError):
        schema.initialize(store.conn)
    store.close()


def test_open_uninitialised_raises(tmp_path):
    sqlite3.connect(tmp_path / "empty.db").close()
    with pytest.raises(RuntimeError):
        UserStore.open(tmp_path / "empty.db")


def test_open_existing_roundtrips(tmp_path):
    path = tmp_path / "mira.db"
    UserStore.create(path, app_version="x").close()
    store = UserStore.open(path)
    assert schema.get_version(store.conn) == schema.SCHEMA_VERSION
    store.close()


def test_migrate_future_version_raises(tmp_path):
    store = _make_store(tmp_path)
    store.conn.execute(
        "UPDATE schema_info SET schema_version = ? WHERE id = 1",
        (schema.SCHEMA_VERSION + 1,),
    )
    with pytest.raises(RuntimeError):
        schema.migrate(store.conn)
    store.close()


def test_integrity_check_returns_ok_for_fresh_db(tmp_path):
    """A freshly-initialised database passes ``PRAGMA integrity_check`` —
    the user_store open path uses this as the corruption-detection seam
    (spec/53 §3.1)."""
    store = _make_store(tmp_path)
    assert schema.integrity_check(store.conn) == "ok"
    store.close()


def test_ddl_creates_every_spec53_table(tmp_path):
    """Lock the table set — adding/removing tables surfaces here, paired with
    a SCHEMA_VERSION bump and a migration entry."""
    store = _make_store(tmp_path)
    names = {
        r["name"] for r in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    # spec/61 (schema v2): the user-level "cut" table retired — event Cuts
    # live in event.db; only templates are user-level.
    # spec/81 Phase 2 (schema v3): cross-event surface arrived — global_items
    # (cross-event projection) + saved_filter (cross-event DC home).
    assert names == {
        "schema_info",
        "installation_profile",
        "setting",
        "wizard_answer",
        "event_index",
        "cut_template",
        "global_items",
        "saved_filter",
        "person",
        "user_camera",
        "gear_profile",
        "feature_flag",
    }
    store.close()


def test_gear_profile_check_rejects_invalid_kind(tmp_path):
    """spec/85 §4 — the DDL CHECK pins ``kind`` to {camera, lens}."""
    store = _make_store(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO gear_profile (kind, key, is_active, updated_at) "
            "VALUES ('body', 'X', 1, 't')"
        )
    store.close()


def test_gear_profile_preferred_genres_must_be_valid_json(tmp_path):
    """``preferred_genres`` is either NULL or a JSON envelope — same
    json_valid rule as every other extras column."""
    store = _make_store(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO gear_profile "
            "(kind, key, is_active, preferred_genres, updated_at) "
            "VALUES ('lens', 'X', 0, '{not json', 't')"
        )
    # NULL is fine — explicit "no preferred genres".
    store.conn.execute(
        "INSERT INTO gear_profile "
        "(kind, key, is_active, preferred_genres, updated_at) "
        "VALUES ('lens', 'OK', 0, NULL, 't')"
    )
    store.close()


def test_extras_json_validates_malformed_input(tmp_path):
    """The ``json_valid`` CHECK rejects malformed JSON in any extras_json
    column. Same escape-hatch + safety contract as event.db."""
    store = _make_store(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO user_camera (camera_id, make, model, created_at, extras_json) "
            "VALUES ('X', 'A', 'B', 't', '{not json')"
        )
    store.close()


def test_installation_profile_rejects_unknown_name(tmp_path):
    """The CHECK constraint pins the closed enum (XMC / MC / custom)."""
    store = _make_store(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO installation_profile (id, name, created_at) "
            "VALUES (1, 'PRO', 't')"
        )
    store.close()


def test_feature_flag_source_check(tmp_path):
    """``source`` is the closed enum default / install_profile / user."""
    store = _make_store(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO feature_flag (key, enabled, source, set_at) "
            "VALUES ('feature.x', 1, 'invented', 't')"
        )
    store.close()


def test_cut_template_type_filter_check(tmp_path):
    """spec/61 (schema v2): ``type_filter`` is the closed enum
    both / photo / video. (The old cut.scope_kind pin retired with the
    user-level cut table.)"""
    store = _make_store(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO cut_template (id, name, type_filter, created_at) "
            "VALUES ('t1', 'X', 'audio', 't')"
        )
    store.close()


# --------------------------------------------------------------------------- #
# Generic CRUD + transactions
# --------------------------------------------------------------------------- #


def test_upsert_get_all_delete_setting(tmp_path):
    """The canonical CRUD shape — exercised on the Setting table since it's the
    workhorse (every legacy settings.rebuild.json key lands here)."""
    store = _make_store(tmp_path)
    store.upsert(m.Setting(
        key="photos_base_path",
        value_json=json.dumps("D:/Photos/_mira"),
        updated_at="2026-06-08T00:00:00+00:00",
    ))
    store.upsert(m.Setting(
        key="theme",
        value_json=json.dumps("dark"),
        updated_at="2026-06-08T00:00:00+00:00",
    ))

    got = store.get(m.Setting, "photos_base_path")
    assert got is not None
    assert json.loads(got.value_json) == "D:/Photos/_mira"
    assert {s.key for s in store.all(m.Setting)} == {"photos_base_path", "theme"}

    # Upsert overwrites by PK.
    store.upsert(m.Setting(
        key="theme",
        value_json=json.dumps("light"),
        updated_at="2026-06-09T00:00:00+00:00",
    ))
    assert json.loads(store.get(m.Setting, "theme").value_json) == "light"

    store.delete(m.Setting, "theme")
    assert store.get(m.Setting, "theme") is None
    assert {s.key for s in store.all(m.Setting)} == {"photos_base_path"}
    store.close()


def test_query_by_uses_sql_where(tmp_path):
    """Filtered queries use SQL WHERE, exercised on feature_flag (a real
    consumer — read all flags from an install profile)."""
    store = _make_store(tmp_path)
    NOW = "2026-06-08T00:00:00+00:00"
    for key, src in [
        ("feature.cross_event_cuts", "install_profile"),
        ("feature.tz_correction", "install_profile"),
        ("feature.quick_sweep", "user"),
    ]:
        store.upsert(m.FeatureFlag(key=key, enabled=True, source=src, set_at=NOW))

    profile_flags = store.query_by(m.FeatureFlag, source="install_profile")
    assert {f.key for f in profile_flags} == {
        "feature.cross_event_cuts", "feature.tz_correction",
    }
    user_flags = store.query_by(m.FeatureFlag, source="user")
    assert {f.key for f in user_flags} == {"feature.quick_sweep"}
    # With no filters, query_by degenerates to all.
    assert len(store.query_by(m.FeatureFlag)) == 3
    store.close()


def test_bool_roundtrips_through_columns(tmp_path):
    """Booleans coerce back from INTEGER 0/1 (same contract as the per-event
    store). Exercised on UserCamera.is_phone — a real load-bearing field."""
    store = _make_store(tmp_path)
    NOW = "2026-06-08T00:00:00+00:00"
    store.upsert(m.UserCamera(
        camera_id="iPhone 15",
        make="Apple", model="iPhone 15",
        created_at=NOW, is_phone=True,
    ))
    store.upsert(m.UserCamera(
        camera_id="DC-G9M2",
        make="Panasonic", model="DC-G9M2",
        created_at=NOW, is_phone=False,
    ))
    phone = store.get(m.UserCamera, "iPhone 15")
    cam = store.get(m.UserCamera, "DC-G9M2")
    assert phone.is_phone is True
    assert cam.is_phone is False
    store.close()


def test_transaction_rolls_back_on_error(tmp_path):
    """An exception inside the transaction context rolls back every write."""
    store = _make_store(tmp_path)
    NOW = "2026-06-08T00:00:00+00:00"
    with pytest.raises(ValueError):
        with store.transaction() as conn:
            conn.execute(
                "INSERT INTO setting (key, value_json, updated_at) "
                "VALUES ('k1', '\"v1\"', ?)", (NOW,),
            )
            raise ValueError("boom")
    assert store.get(m.Setting, "k1") is None
    store.close()


# --------------------------------------------------------------------------- #
# Per-table round-trips (every dataclass survives upsert → get with bool
# coercion and default-value preservation)
# --------------------------------------------------------------------------- #


def _now() -> str:
    return "2026-06-08T00:00:00+00:00"


def test_installation_profile_roundtrip(tmp_path):
    store = _make_store(tmp_path)
    store.upsert(m.InstallationProfile(name="XMC", created_at=_now()))
    got = store.get(m.InstallationProfile, 1)
    assert got is not None
    assert got.name == "XMC" and got.id == 1
    assert got.extras_json == '{}'
    store.close()


def test_wizard_answer_roundtrip(tmp_path):
    store = _make_store(tmp_path)
    store.upsert(m.WizardAnswer(
        question_id="home_tz_hours",
        answer_json=json.dumps(-3.0),
        answered_at=_now(),
    ))
    got = store.get(m.WizardAnswer, "home_tz_hours")
    assert json.loads(got.answer_json) == -3.0
    store.close()


def test_event_index_roundtrip(tmp_path):
    """The replacement for events_index.json — relpath_to_base is the
    load-bearing field, abs_path is the cross-volume fallback (charter §5.9)."""
    store = _make_store(tmp_path)
    store.upsert(m.EventIndex(
        event_uuid="evt-1",
        relpath_to_base="2026 - Costa Rica",
        name_cached="Costa Rica 2026",
        type_cached="trip",
        country_cached="CR",
        start_date_cached="2026-04-01",
        end_date_cached="2026-04-14",
    ))
    got = store.get(m.EventIndex, "evt-1")
    assert got.relpath_to_base == "2026 - Costa Rica"
    assert got.abs_path is None             # normal case
    assert got.is_closed_cached is False    # bool coercion through default
    assert got.name_cached == "Costa Rica 2026"

    # Cross-volume fallback: relpath empty (PRIMARY KEY allows any string),
    # abs_path set. spec/53 §2.3 — "normally NULL".
    store.upsert(m.EventIndex(
        event_uuid="evt-2",
        relpath_to_base="",
        abs_path="E:/elsewhere/Trip",
    ))
    cross = store.get(m.EventIndex, "evt-2")
    assert cross.abs_path == "E:/elsewhere/Trip"
    store.close()


def test_cut_template_roundtrip(tmp_path):
    """spec/61 (schema v2): a template is the New Cut dialog's RECIPE —
    pool expression + filters + session default + times + music."""
    store = _make_store(tmp_path)
    store.upsert(m.CutTemplate(
        id="tpl:best-macro",
        name="best_macro_shots",
        pool_expr_json=json.dumps([["+", "exported"], ["-", "short_version"]]),
        style_filter_json=json.dumps(["macro"]),
        type_filter="photo",
        default_state="skipped",
        target_s=600,
        max_s=720,
        photo_s=5.0,
        music_category="happy",
        created_at=_now(),
    ))
    got = store.get(m.CutTemplate, "tpl:best-macro")
    assert json.loads(got.pool_expr_json) == [["+", "exported"], ["-", "short_version"]]
    assert json.loads(got.style_filter_json) == ["macro"]
    assert got.type_filter == "photo" and got.photo_s == 5.0
    assert got.music_category == "happy"
    store.close()


def test_migrate_v1_to_v2_reshapes_cut_tables(tmp_path):
    """v1→v2 (spec/61): the user-level cut table drops; cut_template
    reshapes to the recipe. Neither ever had a writer — no data moves."""
    store = _make_store(tmp_path)
    conn = store.conn
    # Reconstruct the v1 shape: old tables present, recipe shape absent,
    # later-arrived tables absent. ``gear_profile`` arrived at v5 (spec/85)
    # and must drop here too so the chain re-creates it.
    conn.execute("DROP TABLE cut_template")
    conn.execute("DROP TABLE global_items")
    conn.execute("DROP TABLE saved_filter")
    conn.execute("DROP TABLE gear_profile")
    conn.execute("CREATE TABLE cut (id TEXT PRIMARY KEY, name TEXT)")
    conn.execute(
        "CREATE TABLE cut_template (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "target_s INTEGER, max_s INTEGER, slide_dur_s REAL, "
        "videos_allowed INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, "
        "extras_json TEXT NOT NULL DEFAULT '{}')")
    conn.execute("UPDATE schema_info SET schema_version = 1 WHERE id = 1")

    schema.migrate(conn)

    assert schema.get_version(conn) == schema.SCHEMA_VERSION
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "cut" not in names and "cut_template" in names
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(cut_template)")}
    assert {"pool_expr_json", "style_filter_json", "photo_s",
            "music_category"} <= cols
    store.close()


def test_person_roundtrip(tmp_path):
    store = _make_store(tmp_path)
    store.upsert(m.Person(
        id="person-1",
        display_name="Maria",
        reference_photo_relpath="person-1.jpg",
        embedding_json=json.dumps([0.1, 0.2, 0.3]),
        created_at=_now(),
        updated_at=_now(),
    ))
    got = store.get(m.Person, "person-1")
    assert got.display_name == "Maria"
    assert got.reference_photo_relpath == "person-1.jpg"
    assert json.loads(got.embedding_json) == [0.1, 0.2, 0.3]
    store.close()


def test_feature_flag_roundtrip_and_index_on_source(tmp_path):
    store = _make_store(tmp_path)
    NOW = _now()
    store.upsert(m.FeatureFlag(
        key="feature.cross_event_cuts",
        enabled=True, source="install_profile", set_at=NOW,
    ))
    got = store.get(m.FeatureFlag, "feature.cross_event_cuts")
    assert got.enabled is True
    assert got.source == "install_profile"
    store.close()


# --------------------------------------------------------------------------- #
# spec/81 Phase 2 / spec/32 §3-§4 — the cross-event surface (schema v3)
# --------------------------------------------------------------------------- #


def test_global_item_roundtrip_with_composite_pk(tmp_path):
    """A ``GlobalItem`` round-trips: every field survives, the composite PK
    ``(event_uuid, item_id)`` keys upserts, and the bool ``has_export``
    coerces back from the 0/1 column."""
    store = _make_store(tmp_path)
    NOW = _now()
    store.upsert(m.GlobalItem(
        event_uuid="evt-1", item_id="i-100", synced_at=NOW,
        event_name="Costa Rica 2026",
        origin_relpath="Original Media/IMG_0001.jpg",
        capture_time="2026-04-02T10:00:00+00:00",
        kind="photo", provenance="captured",
        classification="macro",
        iso=400, aperture_f=2.8, shutter_speed_s=0.004,
        focal_length_mm=45.0, flash_fired=0,
        lens_model="LEICA DG MACRO-ELMARIT 45/F2.8",
        camera_id="Panasonic+DC-G9M2",
        pick_state="picked", edit_state="picked",
        has_export=True,
        country="Costa Rica", country_code="CR",
        day_city="Monteverde", day_sublocation="Cloud Forest",
        stars=5, color_label="green", flag=1,
    ))
    got = store.get(m.GlobalItem, "evt-1", "i-100")
    assert got is not None
    assert got.event_name == "Costa Rica 2026"
    assert got.classification == "macro"
    assert got.iso == 400 and got.aperture_f == 2.8
    assert got.has_export is True              # bool coercion (INTEGER 0/1)
    assert got.country_code == "CR"
    assert got.stars == 5 and got.color_label == "green"
    # Upsert overwrites by PK.
    store.upsert(m.GlobalItem(
        event_uuid="evt-1", item_id="i-100", synced_at=NOW,
        classification="wildlife", stars=4, has_export=False,
    ))
    got = store.get(m.GlobalItem, "evt-1", "i-100")
    assert got.classification == "wildlife" and got.stars == 4
    assert got.has_export is False
    store.close()


def test_global_items_query_by_event_uuid(tmp_path):
    """Multi-event projection: ``query_by(event_uuid=…)`` scopes to one
    event via the indexed event_uuid column (the cross-event resolver's
    base read for the ``#collected`` rung)."""
    store = _make_store(tmp_path)
    NOW = _now()
    for ev in ("evt-1", "evt-2"):
        for n in range(3):
            store.upsert(m.GlobalItem(
                event_uuid=ev, item_id=f"i-{n}", synced_at=NOW,
                classification="macro" if n == 0 else "wildlife",
                has_export=(n == 0),
            ))
    evt1 = store.query_by(m.GlobalItem, event_uuid="evt-1")
    assert {g.item_id for g in evt1} == {"i-0", "i-1", "i-2"}
    macros = store.query_by(m.GlobalItem, classification="macro")
    assert len(macros) == 2 and {m_.event_uuid for m_ in macros} == {"evt-1", "evt-2"}
    store.close()


def test_global_items_stars_check_rejects_out_of_range(tmp_path):
    """``stars`` CHECK enforces the 1-5 user-facing rating range
    (spec/32 §2a)."""
    store = _make_store(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO global_items (event_uuid, item_id, synced_at, stars) "
            "VALUES ('evt-1', 'i-1', 't', 6)"
        )
    store.close()


def test_global_items_has_export_check_rejects_non_boolean(tmp_path):
    """``has_export`` is INTEGER 0/1 — no other value lands."""
    store = _make_store(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO global_items (event_uuid, item_id, synced_at, has_export) "
            "VALUES ('evt-1', 'i-1', 't', 2)"
        )
    store.close()


def test_saved_filter_roundtrip(tmp_path):
    """A cross-event DC is a ``saved_filter`` row (spec/81 §2.1 + spec/32 §4).
    Same typed-ref shape as event.db ``dynamic_collection`` — the model is
    scope-agnostic; cross-event differs only in operand range + filter breadth."""
    store = _make_store(tmp_path)
    NOW = _now()
    store.upsert(m.SavedFilter(
        id="sf-1", tag="best_macro",
        description="Macro picks, 5-star, any year",
        expr_json=json.dumps([["+", "exported"]]),
        filters_json=json.dumps({
            "styles": ["macro"], "media_type": "photo",
            "iso_max": 800, "stars_min": 5,
            "country_codes": ["CR", "NP"],
        }),
        created_at=NOW, updated_at=NOW,
    ))
    got = store.get(m.SavedFilter, "sf-1")
    assert got is not None
    assert got.tag == "best_macro"
    assert json.loads(got.filters_json)["stars_min"] == 5
    store.close()


def test_saved_filter_tag_is_unique_case_blind(tmp_path):
    """``tag`` carries ``COLLATE NOCASE UNIQUE`` — same as the event-scope
    DC namespace. Cross-event tags collide case-blind."""
    store = _make_store(tmp_path)
    NOW = _now()
    store.upsert(m.SavedFilter(
        id="sf-1", tag="best_macro", created_at=NOW, updated_at=NOW))
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert(m.SavedFilter(
            id="sf-2", tag="BEST_MACRO", created_at=NOW, updated_at=NOW))
    store.close()


def test_saved_filter_empty_tag_rejected(tmp_path):
    """Empty tag is rejected (CHECK ``tag <> ''``) — same shape as event-scope DC."""
    store = _make_store(tmp_path)
    NOW = _now()
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert(m.SavedFilter(
            id="sf-1", tag="", created_at=NOW, updated_at=NOW))
    store.close()


def test_migrate_v2_to_v3_adds_cross_event_tables(tmp_path):
    """v2→v3 (spec/81 Phase 2) creates ``global_items`` + ``saved_filter``
    as NEW tables — full CHECK + index complement intact (unlike ALTER
    ADD COLUMN). Existing rows are untouched."""
    store = _make_store(tmp_path)
    conn = store.conn
    # Reconstruct the v2 shape: drop later-arrived tables, pin the version
    # to 2. ``gear_profile`` arrived at v5 (spec/85); the chain re-creates
    # it after v3.
    conn.execute("DROP TABLE global_items")
    conn.execute("DROP TABLE saved_filter")
    conn.execute("DROP TABLE gear_profile")
    conn.execute("UPDATE schema_info SET schema_version = 2 WHERE id = 1")
    # Seed a row that should survive the migration.
    conn.execute(
        "INSERT INTO setting (key, value_json, updated_at) "
        "VALUES ('marker', '\"v2-pre\"', '2026-06-16T00:00:00+00:00')"
    )

    schema.migrate(conn)

    assert schema.get_version(conn) == schema.SCHEMA_VERSION
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"global_items", "saved_filter"} <= names
    # CHECK survived: stars=6 is rejected on the migrated table.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO global_items (event_uuid, item_id, synced_at, stars) "
            "VALUES ('e', 'i', 't', 6)")
    # Indexes landed.
    idx = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='global_items'")}
    assert "ix_global_items_event" in idx
    assert "ix_global_items_has_export" in idx
    # Pre-existing row untouched.
    pre = conn.execute("SELECT value_json FROM setting WHERE key='marker'").fetchone()
    assert pre is not None and pre["value_json"] == '"v2-pre"'
    store.close()


def test_migrate_v4_to_v5_adds_gear_profile(tmp_path):
    """v4→v5 (spec/85) creates ``gear_profile`` as a NEW table — full CHECK
    + partial index complement intact. Existing rows untouched. The
    ``json_valid`` CHECK on ``preferred_genres`` survives the migration so
    callers can't slip non-JSON in."""
    store = _make_store(tmp_path)
    conn = store.conn
    # Reconstruct the v4 shape: drop gear_profile, pin the version to 4. The
    # v6 (spec/86) event-qualifier columns also retire here so the v5→v6
    # ALTER ADD COLUMN re-creates them on the way back up. The partial
    # indexes have to drop first — SQLite refuses to drop a column an
    # index still references.
    conn.execute("DROP TABLE gear_profile")
    for idx in (
        "ix_global_items_event_type",
        "ix_global_items_event_subtype",
        "ix_global_items_experience_type",
        "ix_global_items_event_start",
        "ix_global_items_event_end",
    ):
        conn.execute(f"DROP INDEX IF EXISTS {idx}")
    for col in (
        "event_type", "event_subtype", "experience_type",
        "participants", "event_start", "event_end",
    ):
        conn.execute(f"ALTER TABLE global_items DROP COLUMN {col}")
    conn.execute("UPDATE schema_info SET schema_version = 4 WHERE id = 1")
    # Seed a row that should survive the migration.
    conn.execute(
        "INSERT INTO setting (key, value_json, updated_at) "
        "VALUES ('marker', '\"v4-pre\"', '2026-06-17T00:00:00+00:00')"
    )

    schema.migrate(conn)

    assert schema.get_version(conn) == schema.SCHEMA_VERSION
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "gear_profile" in names
    # CHECK survived: invalid kind is rejected on the migrated table.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO gear_profile (kind, key, is_active, updated_at) "
            "VALUES ('body', 'X', 1, 't')")
    # Partial index landed.
    idx = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='gear_profile'")}
    assert "ix_gear_profile_active" in idx
    # Pre-existing row untouched.
    pre = conn.execute("SELECT value_json FROM setting WHERE key='marker'").fetchone()
    assert pre is not None and pre["value_json"] == '"v4-pre"'
    store.close()


def test_migrate_v5_to_v6_adds_event_qualifier_columns(tmp_path):
    """v5→v6 (spec/86) extends ``global_items`` with six event-qualifier
    columns + their partial indexes. ALTER ADD COLUMN — every existing row
    lands NULL on the new columns, then the next sync repopulates.
    Pre-existing data must survive the migration verbatim."""
    store = _make_store(tmp_path)
    conn = store.conn
    # Drop the v6 event columns + indexes; pin the version to 5. Partial
    # indexes have to drop before their columns can.
    for idx in (
        "ix_global_items_event_type",
        "ix_global_items_event_subtype",
        "ix_global_items_experience_type",
        "ix_global_items_event_start",
        "ix_global_items_event_end",
    ):
        conn.execute(f"DROP INDEX IF EXISTS {idx}")
    for col in (
        "event_type", "event_subtype", "experience_type",
        "participants", "event_start", "event_end",
    ):
        conn.execute(f"ALTER TABLE global_items DROP COLUMN {col}")
    conn.execute("UPDATE schema_info SET schema_version = 5 WHERE id = 1")
    # Seed a pre-migration global_items row that should survive.
    conn.execute(
        "INSERT INTO global_items (event_uuid, item_id, synced_at, "
        "                          classification, stars) "
        "VALUES ('e1', 'i1', 't', 'macro', 5)")

    schema.migrate(conn)

    assert schema.get_version(conn) == schema.SCHEMA_VERSION
    cols = {r["name"] for r in conn.execute(
        "PRAGMA table_info(global_items)")}
    assert {
        "event_type", "event_subtype", "experience_type",
        "participants", "event_start", "event_end",
    } <= cols
    # The pre-existing row survived, with NULL on the new columns.
    row = conn.execute(
        "SELECT classification, stars, event_type, event_subtype, "
        "       experience_type, participants, event_start, event_end "
        "FROM global_items WHERE event_uuid = 'e1' AND item_id = 'i1'"
    ).fetchone()
    assert row["classification"] == "macro"
    assert row["stars"] == 5
    for k in ("event_type", "event_subtype", "experience_type",
              "participants", "event_start", "event_end"):
        assert row[k] is None
    # Partial indexes landed.
    idx = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='global_items'")}
    assert {
        "ix_global_items_event_type",
        "ix_global_items_event_subtype",
        "ix_global_items_experience_type",
        "ix_global_items_event_start",
        "ix_global_items_event_end",
    } <= idx
    store.close()
