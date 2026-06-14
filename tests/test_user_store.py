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
    assert names == {
        "schema_info",
        "installation_profile",
        "setting",
        "wizard_answer",
        "event_index",
        "cut_template",
        "person",
        "user_camera",
        "feature_flag",
    }
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
    # Reconstruct the v1 shape: old tables present, recipe shape absent.
    conn.execute("DROP TABLE cut_template")
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
