"""spec/54 §3.2 Look Strength slider (Nelson 2026-06-13) — Commit 1
foundation: schema v5 migration, the Adjustment.look_strength column,
and the strength= parameter on the Look engine.

The UI half lands in Commit 2; the render-pipeline integration in
Commit 3. This file pins the data + math contracts the other two
build on.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pytest

from core.photo_auto import (
    compute_auto_params,
    compute_look_params,
    look_params_from_natural,
)
from core.photo_render import Params
from mira.store import models as m
from mira.store import schema
from mira.store.repo import EventStore


# ── schema v5 ────────────────────────────────────────────────────────


def test_v5_migration_landed_in_chain():
    """v4→v5 is the look_strength migration; the broader chain runs to
    whatever ``SCHEMA_VERSION`` is today (spec/64 bumped it past 5)."""
    assert schema.SCHEMA_VERSION >= 5


def test_fresh_install_has_look_strength_column(tmp_path):
    store = EventStore.create(tmp_path / "e.db", event_id="evt-1")
    with store.transaction() as conn:
        cols = {row[1]: row for row in conn.execute(
            "PRAGMA table_info(adjustment)")}
    assert "look_strength" in cols
    notnull = cols["look_strength"][3]
    default = cols["look_strength"][4]
    assert notnull == 1
    assert float(default) == 1.0


def test_v4_event_db_migrates_clean(tmp_path):
    """The fresh DDL ships at the current ``SCHEMA_VERSION``; old
    event.db files at v4 must migrate cleanly through the whole chain.
    For the v4→v5 step specifically (this file's focus): look_strength
    lands with default 1.0 for every existing Adjustment row so
    pre-migration photos render IDENTICALLY. The later v5→v6 step
    (spec/64) needs an event table to operate on, so the v4 fixture
    seeds one in the v4 shape (with the retired scope/mood/transport
    columns, which v5→v6 drops)."""
    db = tmp_path / "old.db"
    # Build a v4 schema by hand: schema_info pinned to 4 + adjustment
    # table WITHOUT look_strength + the v4 event table shape (so the
    # v5→v6 step has something to ALTER). The migration runner reads
    # schema_info and applies v4→v5→v6→…
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE schema_info (
            id             INTEGER PRIMARY KEY CHECK (id = 1),
            schema_version INTEGER NOT NULL,
            app_version    TEXT NOT NULL,
            event_id       TEXT NOT NULL,
            created_at     TEXT NOT NULL
        );
        INSERT INTO schema_info VALUES (1, 4, '', 'evt-x', '2026-01-01');
        CREATE TABLE adjustment (
            item_id      TEXT PRIMARY KEY,
            style        TEXT,
            look         TEXT NOT NULL DEFAULT 'natural',
            creative_filter TEXT,
            crop_x       REAL, crop_y REAL, crop_w REAL, crop_h REAL,
            crop_angle   REAL NOT NULL DEFAULT 0,
            rotation     INTEGER NOT NULL DEFAULT 0,
            aspect_label TEXT,
            edit_exported INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO adjustment (item_id, look, edit_exported)
            VALUES ('it-1', 'punch', 0);
        CREATE TABLE event (
            id             INTEGER PRIMARY KEY CHECK (id = 1),
            uuid           TEXT NOT NULL UNIQUE,
            name           TEXT NOT NULL,
            event_type     TEXT NOT NULL DEFAULT 'unclassified',
            event_subtype  TEXT,
            description    TEXT NOT NULL DEFAULT '',
            start_date     TEXT, end_date TEXT,
            is_closed      INTEGER NOT NULL DEFAULT 0,
            event_root_abs TEXT,
            budget_short_target_s INTEGER, budget_short_max_s INTEGER,
            budget_long_target_s INTEGER, budget_long_max_s INTEGER,
            budget_video_share REAL,
            duration_value INTEGER, duration_unit TEXT,
            scope          TEXT,
            participants   TEXT NOT NULL DEFAULT '[]',
            mood           TEXT, transport TEXT,
            created_at     TEXT NOT NULL, updated_at TEXT NOT NULL,
            extras_json    TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX ix_event_scope ON event(scope) WHERE scope IS NOT NULL;
        CREATE INDEX ix_event_mood  ON event(mood)  WHERE mood  IS NOT NULL;
    """)
    conn.commit()
    conn.close()

    # Reopening the store runs the pending migrations.
    store = EventStore.open(db)
    with store.transaction() as conn:
        v = conn.execute(
            "SELECT schema_version FROM schema_info").fetchone()[0]
        assert v == schema.SCHEMA_VERSION
        row = conn.execute(
            "SELECT look_strength FROM adjustment "
            "WHERE item_id = 'it-1'").fetchone()
    # Existing rows keep their pre-migration semantics: look_strength
    # defaults to 1.0 = exactly what they rendered yesterday.
    assert float(row[0]) == 1.0


def test_adjustment_dataclass_carries_look_strength():
    adj = m.Adjustment(item_id="it")
    assert adj.look_strength == 1.0
    adj.look_strength = 0.7
    assert adj.look_strength == 0.7


def _populated_store(tmp_path):
    """A store with one valid captured Item ready to bind an
    Adjustment to. Re-uses the gateway-cuts fixture's _doc() so the
    schema gauntlet (Item identity, camera FK, capture time)
    stays a single place to maintain."""
    from tests.test_gateway_cuts import _doc
    store = EventStore.create(tmp_path / "e.db", event_id="evt-c")
    store.save_document(_doc())
    return store


def test_adjustment_round_trip_through_store(tmp_path):
    """upsert(Adjustment) → store.get round-trips the new column."""
    store = _populated_store(tmp_path)
    a = m.Adjustment(item_id="p1", look="punch", look_strength=1.4)
    store.upsert(a)
    got = store.get(m.Adjustment, "p1")
    assert got is not None
    assert got.look == "punch"
    assert got.look_strength == 1.4


def test_fresh_install_rejects_out_of_range_strength(tmp_path):
    """The CHECK on fresh installs guards range [0, 2]. Migrated v4→v5
    DBs deliberately ship the column without the CHECK (SQLite
    constraint-add limitation — guard at the gateway seam there)."""
    store = _populated_store(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        with store.transaction() as conn:
            conn.execute(
                "INSERT INTO adjustment (item_id, look_strength) "
                "VALUES ('p1', 3.0)")


# ── math: compute_look_params(strength=...) ──────────────────────────


def _flat(grey: int = 100, size=(48, 64, 3)) -> np.ndarray:
    return np.full(size, grey, dtype=np.uint8)


def test_strength_defaults_preserve_pre_strength_behavior():
    """strength=1.0 is a NO-OP — every existing call site renders
    identically. This is the load-bearing pin for the migration."""
    img = _flat()
    a = compute_look_params(img, look="natural")
    b = compute_look_params(img, look="natural", strength=1.0)
    for f in a.__dataclass_fields__:
        assert getattr(a, f) == getattr(b, f), (
            f"strength=1.0 changed field {f}")


def test_strength_zero_returns_identity_for_natural():
    """At strength=0.0 every field is multiplied by 0 → identity =
    effectively the Original look."""
    img = _flat()
    p = compute_look_params(img, look="natural", strength=0.0)
    assert p.is_identity is True


def test_strength_two_doubles_every_field_for_natural():
    img = _flat()
    base = compute_look_params(img, look="natural", strength=1.0)
    boosted = compute_look_params(img, look="natural", strength=2.0)
    # Every non-zero field doubles. Floating-point tolerance because
    # the AUTO routing has its own numerics.
    for f in base.__dataclass_fields__:
        b, x = getattr(base, f), getattr(boosted, f)
        if abs(b) > 1e-6:
            assert abs(x - 2 * b) < 1e-4, f"field {f} did not double"


def test_strength_on_original_stays_identity():
    """Original look = no-op regardless of strength; the user expects
    'leave it alone' to keep meaning at any slider position."""
    img = _flat()
    for s in (0.0, 0.5, 1.0, 1.5, 2.0):
        p = compute_look_params(img, look="original", strength=s)
        assert p.is_identity is True


def test_strength_scales_bias_and_correction_for_brighter():
    """The Brighter look = Natural correction + bias. Strength scales
    BOTH the correction and the bias (the multiplier is on the
    composed Params). At strength=0.5, the whole effect is halved."""
    img = _flat()
    full = compute_look_params(img, look="brighter", strength=1.0)
    half = compute_look_params(img, look="brighter", strength=0.5)
    for f in full.__dataclass_fields__:
        v_full, v_half = getattr(full, f), getattr(half, f)
        if abs(v_full) > 1e-6:
            assert abs(v_half - 0.5 * v_full) < 1e-4, (
                f"field {f} not halved under strength=0.5")


def test_look_params_from_natural_strength_round_trip():
    """The cached-Natural fast path AdjustmentSurface uses must honour
    the same strength contract as compute_look_params."""
    natural = Params(exposure=0.4, contrast=10.0)
    same = look_params_from_natural(natural, "natural", strength=1.0)
    half = look_params_from_natural(natural, "natural", strength=0.5)
    zero = look_params_from_natural(natural, "natural", strength=0.0)
    assert same.exposure == 0.4 and same.contrast == 10.0
    assert abs(half.exposure - 0.2) < 1e-6
    assert abs(half.contrast - 5.0) < 1e-6
    assert zero.is_identity is True
