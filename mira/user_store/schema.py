"""SQLite schema for `mira.db` — the user-level data store (spec/53).

Single physical definition of the user store: the DDL, the owned
:data:`SCHEMA_VERSION`, the migration list, and connection setup
(WAL + foreign keys + ``NORMAL`` sync). Mirrors the per-event
:mod:`mira.store.schema` layer field for field; the two stores
share the same operational discipline (`feedback_schema_evolution_policy`).

v1 established the spec/53 §2 shape; **v2 (spec/61 slice 10)** retired the
user-level ``cut`` table (event Cuts live in event.db — file-based membership)
and reshaped ``cut_template`` to the New Cut dialog's RECIPE. **v3 (spec/81
Phase 2)** adds the cross-event surface: ``global_items`` (spec/32 §3 — a
cross-event projection of every event's items so cross-event queries hit ONE
SQLite file) + ``saved_filter`` (spec/32 §4 — the cross-event DC home; same
typed-ref shape as event.db ``dynamic_collection``, the spec/32 §4 predicate
tree reconciled to spec/81 §2's ``expr_json`` + ``filters_json``). Both arrive
as new tables — no ALTER, no CHECK loss. **v7 (spec/90 Phase 1)** adds the
three saved-noun substrates the rule-list Recipe model needs: ``recipe`` (the
saved Cut/Collection configuration, the unified noun §5.1), ``event_collection``
(saved event sets — the cross-event analogue of a DC at the event level, §5.3),
and ``representative_face_id`` on the existing ``person`` table (pointer to a
``face`` row in event.db, §5.2). All four tables stay empty in this slice; the
resolver / dialog / UI work lands in later phases. Existing files migrate in
place on open; fresh files are created at the current version.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

log = logging.getLogger(__name__)

#: Schema version owned by us. Bump together with an entry appended to MIGRATIONS.
SCHEMA_VERSION = 7

# --------------------------------------------------------------------------- #
# DDL — spec/53 §2, statement-for-statement. All durable tables — there is no
# derived/cache stratum at v1 (the cache concept is event.db-specific). Every
# table that might grow gets an ``extras_json`` column per spec/53 §1.1.
# --------------------------------------------------------------------------- #

DDL = r"""
-- ===== schema_info (D) — typed singleton; mirrors event.db pattern =========
CREATE TABLE schema_info (
  id              INTEGER PRIMARY KEY CHECK (id = 1),
  schema_version  INTEGER NOT NULL,
  app_version     TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

-- ===== installation_profile (D) — names the feature-set this install runs ==
-- 'XMC' = full enthusiast bundle; 'MC' = streamlined Persona-1 bundle;
-- 'custom' = user-mixed (rare). Drives the per-key DEFAULTS in feature_flag
-- via core.feature_flags (the code-side defaults map).
CREATE TABLE installation_profile (
  id           INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton, enforced
  name         TEXT NOT NULL CHECK (name IN ('XMC','MC','custom')),
  created_at   TEXT NOT NULL,
  extras_json  TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json))
);

-- ===== setting (D) — flat key-value store; value is JSON so any shape fits =
-- Replaces ``settings.rebuild.json`` on the first-launch import. Top-level
-- keys from the legacy file become rows; the value is JSON-encoded so the
-- shape of any individual setting (scalar / list / object) round-trips.
CREATE TABLE setting (
  key         TEXT PRIMARY KEY,
  value_json  TEXT NOT NULL CHECK (json_valid(value_json)),
  updated_at  TEXT NOT NULL
);

-- ===== wizard_answer (D) — wizard responses, separated from setting ========
-- The wizard previously folded its answers into ``settings.rebuild.json``;
-- a dedicated table avoids naming collisions with regular preferences and
-- lets the wizard read/write its own concern without the load-bearing
-- protection contract on the surrounding settings.
CREATE TABLE wizard_answer (
  question_id  TEXT PRIMARY KEY,
  answer_json  TEXT NOT NULL CHECK (json_valid(answer_json)),
  answered_at  TEXT NOT NULL
);

-- ===== event_index (D) — registry of all events known to this install ======
-- Replaces ``events_index.json``. ``relpath_to_base`` is the load-bearing
-- field per `feedback_relative_paths_from_user_default` — the single absolute
-- anchor is the ``photos_base_path`` setting row. ``abs_path`` is the
-- cross-volume fallback (event on a different drive than the base); normally
-- NULL. Cached fields are projections of per-event data — refreshed when an
-- event is closed (or whenever per-day data changes, per spec/52).
CREATE TABLE event_index (
  event_uuid          TEXT PRIMARY KEY,
  relpath_to_base     TEXT NOT NULL,
  abs_path            TEXT,
  name_cached         TEXT NOT NULL DEFAULT '',
  type_cached         TEXT,
  country_cached      TEXT,
  start_date_cached   TEXT,
  end_date_cached     TEXT,
  is_closed_cached    INTEGER NOT NULL DEFAULT 0 CHECK (is_closed_cached IN (0,1)),
  last_opened_at      TEXT,
  extras_json         TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json))
);
CREATE INDEX ix_event_index_last_opened ON event_index(last_opened_at);
CREATE INDEX ix_event_index_country     ON event_index(country_cached);

-- The spec/53-era user-level ``cut`` table retired with spec/61 (schema v2)
-- before any build wrote a row: event Cuts live in event.db — definitions +
-- FILE-based membership (cut + cut_member → lineage). Cross-event Cuts get
-- their own design session (spec/61 §8). Only TEMPLATES are user-level.

-- ===== cut_template (D) — user-saved Cut RECIPES (spec/61, schema v2) ======
-- A template is the New Cut dialog's recipe: pool expression + filters +
-- session default + times + music. Replaying re-evaluates the pool against
-- the target event's Cuts by TAG (names are the cross-event glue, spec/61
-- §1.5). NO pre-shipped templates ship (spec/61 §10 #4) — every row here is
-- the user's own.
CREATE TABLE cut_template (
  id                TEXT PRIMARY KEY,
  name              TEXT NOT NULL,
  pool_expr_json    TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(pool_expr_json)),
  style_filter_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(style_filter_json)),
  type_filter       TEXT NOT NULL DEFAULT 'both' CHECK (type_filter IN ('both','photo','video')),
  default_state     TEXT NOT NULL DEFAULT 'skipped' CHECK (default_state IN ('picked','skipped')),
  target_s          INTEGER CHECK (target_s IS NULL OR target_s > 0),
  max_s             INTEGER CHECK (max_s IS NULL OR max_s > 0),
  photo_s           REAL NOT NULL DEFAULT 6.0 CHECK (photo_s > 0),
  music_category    TEXT,
  created_at        TEXT NOT NULL,
  extras_json       TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json))
);
CREATE INDEX ix_cut_template_name ON cut_template(name);

-- ===== person (D) — the user-level people catalog =========================
-- Reference photo BYTES live at ``%LOCALAPPDATA%\\Mira\\people\\<id>.{jpg,png}``
-- (a sibling folder to mira.db). Per-photo links in event.db
-- ``photo_person`` reference ``person.id`` here. Simplest tier per spec/51
-- §3.13: one reference photo per person, embedding computed and cached at
-- upload; face-matching runs at filter time only.
--
-- spec/90 §5.2 (schema v7): ``representative_face_id`` is the pointer to a
-- ``face`` row in event.db (one event's worth of detected boxes). Opaque TEXT
-- because no FK can span stores — the same shape as ``photo_person.person_id``
-- but in the opposite direction. NULL = no representative face has been
-- chosen yet (the default for legacy rows and for People created before any
-- recognition pass runs). spec/90 also names this column ``name`` and a
-- ``name UNIQUE`` constraint; the existing ``display_name`` (NOT UNIQUE)
-- column is kept verbatim — two People may legitimately carry the same
-- display name (different ``id``), and Phase 1 has no lookup-by-name path
-- that would benefit from a UNIQUE.
CREATE TABLE person (
  id                       TEXT PRIMARY KEY,
  display_name             TEXT NOT NULL,
  reference_photo_relpath  TEXT,
  embedding_json           TEXT,
  representative_face_id   TEXT,
  created_at               TEXT NOT NULL,
  updated_at               TEXT NOT NULL,
  extras_json              TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json))
);
CREATE INDEX ix_person_display_name ON person(display_name);

-- ===== user_camera (D) — cameras the user owns ============================
-- ``camera_id`` cross-references ``event.db.camera.camera_id`` via the same
-- Make+Model business key. The catalog is populated automatically as cameras
-- are discovered during ingest; the user can edit display labels or add
-- hardware manually.
CREATE TABLE user_camera (
  camera_id     TEXT PRIMARY KEY,
  make          TEXT NOT NULL,
  model         TEXT NOT NULL,
  is_phone      INTEGER NOT NULL DEFAULT 0 CHECK (is_phone IN (0,1)),
  owned_since   TEXT,
  created_at    TEXT NOT NULL,
  extras_json   TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json))
);

-- ===== global_items (D) — cross-event projection of item facts (spec/32 §3) =
-- The cross-event surface (spec/81 §2.1 + Phase 2) lets ONE SQLite query reach
-- every event's items without fanning out across N event.db files. Each row is
-- a denormalised snapshot of one item plus its enclosing event/day context.
-- Reconciled column names: ``pick_state`` was ``cull_state`` (the locked verb
-- pair is Pick/Skip; the column carries the ``phase='pick'`` decision); ``flag``
-- was ``pick`` (the portfolio flag is distinct from the decision verb).
-- Synced on event close + startup reconcile (the gateway-level sync runs the
-- whole-event projection inside one ``UserStore.transaction``).
-- The ladder rungs (#collected / #picked / #edited / #exported, spec/81 §2.1
-- + spec/32 §2e) map to: #collected = every row; #picked = ``pick_state =
-- 'picked'``; #edited = ``edit_state = 'picked'`` (the Edit-phase commit;
-- spec/61 §1.1 — edited ≠ exported); #exported = ``has_export = 1``.
CREATE TABLE global_items (
  event_uuid        TEXT NOT NULL,
  event_name        TEXT NOT NULL DEFAULT '',
  item_id           TEXT NOT NULL,
  origin_relpath    TEXT,                    -- relative to event_root (NULL = virtual)
  export_relpath    TEXT,                    -- the LATEST exported relpath (NULL if not exported); cross-event Cut commit reads this
  capture_time      TEXT,                    -- corrected; the chronological sort key
  kind              TEXT,                    -- 'photo' | 'video'
  provenance        TEXT,                    -- 'captured'|'snapshot'|'clip'|'stack_output'|'authored'
  classification    TEXT,
  iso               INTEGER,
  aperture_f        REAL,
  shutter_speed_s   REAL,
  focal_length_mm   REAL,
  flash_fired       INTEGER CHECK (flash_fired IS NULL OR flash_fired IN (0,1)),
  lens_model        TEXT,
  camera_id         TEXT,
  duration_ms       INTEGER,                 -- video length; NULL = still
  -- ladder state (spec/32 §2e, locked vocabulary)
  pick_state        TEXT,                    -- phase_state(phase='pick').state; NULL = never decided
  edit_state        TEXT,                    -- phase_state(phase='edit').state; NULL = never decided
  has_export        INTEGER NOT NULL DEFAULT 0 CHECK (has_export IN (0,1)),
  -- enclosing event/day context (denormalised so cross-event filters are one SELECT)
  country           TEXT,
  country_code      TEXT,                    -- ISO 3166-1 alpha-2
  day_city          TEXT,
  day_sublocation   TEXT,
  -- curatorial (from item.extras_json — spec/32 §2a)
  stars             INTEGER CHECK (stars IS NULL OR (stars >= 1 AND stars <= 5)),
  color_label       TEXT,
  flag              INTEGER CHECK (flag IS NULL OR flag IN (0,1)),
  -- event-level qualifiers (spec/86) — denormalised onto every item of the
  -- event so cross-event resolvers can filter on the event's own attributes
  -- without fanning out to N event.db files. ``event_type`` is the closed
  -- spec/52 enum (trip/session/occasion/project/unclassified); the others
  -- echo the corresponding ``event`` table columns verbatim.
  -- ``event_start``/``event_end`` are DERIVED at sync time = min/max of the
  -- event's trip_day.date values (spec/86 §5), so the date-range overlap
  -- filter prunes whole events without joining trip_day.
  event_type        TEXT,
  event_subtype     TEXT,
  experience_type   TEXT,
  participants      TEXT,                     -- JSON array; nullable
  event_start       TEXT,                     -- ISO date or NULL
  event_end         TEXT,                     -- ISO date or NULL
  -- bookkeeping
  synced_at         TEXT NOT NULL,
  PRIMARY KEY (event_uuid, item_id)
);
CREATE INDEX ix_global_items_event       ON global_items(event_uuid);
CREATE INDEX ix_global_items_time        ON global_items(capture_time);
CREATE INDEX ix_global_items_kind        ON global_items(kind);
CREATE INDEX ix_global_items_class       ON global_items(classification)  WHERE classification IS NOT NULL;
CREATE INDEX ix_global_items_iso         ON global_items(iso)             WHERE iso IS NOT NULL;
CREATE INDEX ix_global_items_aperture    ON global_items(aperture_f)      WHERE aperture_f IS NOT NULL;
CREATE INDEX ix_global_items_shutter     ON global_items(shutter_speed_s) WHERE shutter_speed_s IS NOT NULL;
CREATE INDEX ix_global_items_focal       ON global_items(focal_length_mm) WHERE focal_length_mm IS NOT NULL;
CREATE INDEX ix_global_items_flash       ON global_items(flash_fired)     WHERE flash_fired = 1;
CREATE INDEX ix_global_items_lens        ON global_items(lens_model)      WHERE lens_model IS NOT NULL;
CREATE INDEX ix_global_items_camera      ON global_items(camera_id)       WHERE camera_id IS NOT NULL;
CREATE INDEX ix_global_items_pick        ON global_items(pick_state)      WHERE pick_state IS NOT NULL;
CREATE INDEX ix_global_items_edit        ON global_items(edit_state)      WHERE edit_state IS NOT NULL;
CREATE INDEX ix_global_items_has_export  ON global_items(has_export)      WHERE has_export = 1;
CREATE INDEX ix_global_items_country     ON global_items(country_code)    WHERE country_code IS NOT NULL;
CREATE INDEX ix_global_items_city        ON global_items(day_city)        WHERE day_city IS NOT NULL;
CREATE INDEX ix_global_items_stars       ON global_items(stars)           WHERE stars IS NOT NULL;
CREATE INDEX ix_global_items_color_label ON global_items(color_label)     WHERE color_label IS NOT NULL;
CREATE INDEX ix_global_items_flag        ON global_items(flag)            WHERE flag = 1;
-- spec/86 event-qualifier indexes — every cross-event filter that
-- partitions by event predicate hits these. Participants is JSON; queried
-- via json_each (no partial index possible).
CREATE INDEX ix_global_items_event_type      ON global_items(event_type)      WHERE event_type IS NOT NULL;
CREATE INDEX ix_global_items_event_subtype   ON global_items(event_subtype)   WHERE event_subtype IS NOT NULL;
CREATE INDEX ix_global_items_experience_type ON global_items(experience_type) WHERE experience_type IS NOT NULL;
CREATE INDEX ix_global_items_event_start     ON global_items(event_start)     WHERE event_start IS NOT NULL;
CREATE INDEX ix_global_items_event_end       ON global_items(event_end)       WHERE event_end IS NOT NULL;

-- ===== saved_filter (D) — cross-event DC home (spec/32 §4 + spec/81 §2.1) ===
-- The cross-event DC IS a ``saved_filter`` row (no separate user-level
-- ``dynamic_collection``). Shape is intentionally identical to event.db
-- ``dynamic_collection`` — the spec/81 §2 model is scope-agnostic; the only
-- difference is what operands ``expr_json`` admits (the full ladder rungs
-- ``collected`` / ``picked`` / ``edited`` / ``exported``, vs the single
-- ``exported`` event scope offers) and the breadth of ``filters_json`` (the
-- full spec/32 §2 catalogue, vs event scope's Style + media type pair). The
-- spec/32 §4 "predicate tree" framing reconciles to the typed-ref encoding
-- here (the predicate fields become ``filters_json`` keys; the set algebra
-- moves to ``expr_json``). Tag namespace is global at the user level
-- (cross-event Cuts later collide-check against it).
CREATE TABLE saved_filter (
  id           TEXT PRIMARY KEY,
  tag          TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK (tag <> ''),
  description  TEXT,                       -- free-text one-liner; UI only
  expr_json    TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(expr_json)),
  filters_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(filters_json)),
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  extras_json  TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json))
);

-- ===== recipe (D) — the saved Cut/Collection configuration (spec/90 §5.1) ==
-- One row per saved Recipe — everything the user composed in the New Cut /
-- New Collection dialog except the Picker session's per-file hand decisions.
-- The Recipe schema is **flavoured** (spec/90 §5.1): 'cut' (no Scope, no
-- hardware / face filters — the event-Cut audience-facing face) or
-- 'collection' (full sections — the cross-event curation face). Composition
-- (Scope sentence, Source sentence, Filter selections, Rules list +
-- verdicts, Otherwise verdict, presentation settings) is one opaque JSON
-- blob; the shape is determined by the dialog code, not the schema (Phase
-- 1 is substrate only).
--
-- UNIQUE(flavour, name): the user can have a Cut Recipe and a Collection
-- Recipe sharing a #name (different audiences, different surfaces, separate
-- pools) — the namespaces are split by flavour (spec/90 §5.5). Within one
-- flavour, names are exclusive.
--
-- Lives alongside ``cut_template`` — that table is the legacy New Cut dialog
-- recipe (spec/61 §2). spec/90 supersedes ``cut_template`` once the dialog
-- rewrite lands (§7 Phase 4); Phase 1 leaves both tables present so the
-- existing dialog keeps working.
CREATE TABLE recipe (
  id                TEXT PRIMARY KEY,
  name              TEXT NOT NULL,
  flavour           TEXT NOT NULL CHECK (flavour IN ('cut','collection')),
  composition_json  TEXT NOT NULL CHECK (json_valid(composition_json)),
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  extras_json       TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json)),
  UNIQUE (flavour, name)
);
CREATE INDEX ix_recipe_flavour ON recipe(flavour);

-- ===== event_collection (D) — saved event sets (spec/90 §5.3) ==============
-- Cross-event analogue of a DC, at the EVENT level (vs spec/81's DC at the
-- ITEM level). Same shape as ``dynamic_collection`` / ``saved_filter`` —
-- set algebra over operands + filters, resolved live — but the universe is
-- events, not items. Operands the resolver admits are events (by uuid) and
-- other Event Collections (nested grouping); ``filters_json`` holds the
-- date-range predicate today and grows to the broader event-metadata
-- catalogue from spec/86 as needed. Tag namespace is global at the user
-- level, COLLATE NOCASE + non-empty check matching the other Recipe-grammar
-- nouns (DC, Cut, saved_filter). ``#adventure_events``, ``#wildlife_trips``
-- are Event Collections. Stays empty in Phase 1.
CREATE TABLE event_collection (
  id           TEXT PRIMARY KEY,
  tag          TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK (tag <> ''),
  expr_json    TEXT NOT NULL CHECK (json_valid(expr_json)),
  filters_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(filters_json)),
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  extras_json  TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json))
);

-- ===== gear_profile (D) — the user's camera/lens kit tag (spec/85) =========
-- ``kind`` discriminates cameras from lenses; ``key`` matches the matching
-- ``global_items.camera_id`` (Make+Model business key) or
-- ``global_items.lens_model`` so the spec/83 §4 picker and the spec/85 §5
-- classifier user-gear-hint tier can join on it. ``is_active`` is the user's
-- "I currently use this" flag; in the picker (spec/83 §4) it beats the
-- photo-count heuristic that would otherwise misclassify a borrowed camera
-- with 300 frames as "main". ``preferred_genres`` is an optional JSON array
-- of Scenario keys (spec/85 §3 — multi-select; empty/NULL = unset); the
-- classifier reads it as a tier just above the generic unknown-lens
-- fallback (spec/85 §5).
--
-- User-level by purpose: a camera spans events. Same home as
-- ``saved_filter`` (the cross-event DC namespace), same operational
-- discipline (atomic-write-then-rename via the user_store transaction).
CREATE TABLE gear_profile (
  kind             TEXT NOT NULL CHECK (kind IN ('camera','lens')),
  key              TEXT NOT NULL,
  is_active        INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0,1)),
  preferred_genres TEXT CHECK (preferred_genres IS NULL OR json_valid(preferred_genres)),
  updated_at       TEXT NOT NULL,
  PRIMARY KEY (kind, key)
);
CREATE INDEX ix_gear_profile_active ON gear_profile(kind, is_active) WHERE is_active = 1;

-- ===== feature_flag (D) — runtime feature gating ==========================
-- Flag KEYS are app-code constants (see ``core/feature_flags.py``); new flags
-- ship in code, never invented at runtime. Unknown keys at read time fold to
-- the per-profile DEFAULT (the code-side ``DEFAULTS_BY_PROFILE`` map).
--
-- ``source`` semantics:
--   'default'         — coded default applied (no row exists; reads through
--                       feature_flags.py)
--   'install_profile' — set by ``installation_profile.name`` at install time
--                       (XMC turns on the full set; MC turns on the
--                       streamlined subset)
--   'user'            — user toggled explicitly via Settings (rare;
--                       spec/53 §3.3 — restart required to take effect)
CREATE TABLE feature_flag (
  key      TEXT PRIMARY KEY,
  enabled  INTEGER NOT NULL CHECK (enabled IN (0,1)),
  source   TEXT NOT NULL CHECK (source IN ('default','install_profile','user')),
  set_at   TEXT NOT NULL
);
"""


# --------------------------------------------------------------------------- #
# Connection setup
# --------------------------------------------------------------------------- #


def connect(path: Union[str, Path]) -> sqlite3.Connection:
    """Open a connection with the spec/53 §3.1 pragmas applied.

    WAL journal, foreign-key enforcement, ``NORMAL`` sync. Rows come back as
    :class:`sqlite3.Row` (key access). ``path`` may be ``":memory:"``.
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    # Autocommit: we drive BEGIN/COMMIT explicitly via UserStore.transaction.
    # Without this the module opens an implicit transaction before DML, which
    # then collides with our explicit BEGIN.
    conn.isolation_level = None
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    # FULL (not NORMAL) for the user store: it is small and written
    # infrequently, so the extra fsync cost is negligible, and it tightens
    # the durability window around a checkpoint interrupted by an abrupt
    # process kill — the 2026-06-18 ``global_items`` corruption (the app
    # never closed cleanly; checkpoints raced teardown). The real fix is the
    # clean-close path now wired at exit; this is defence-in-depth.
    conn.execute("PRAGMA synchronous = FULL")
    # Wait up to 5s for a competing writer to release its lock instead of
    # raising "database is locked" immediately. A momentary overlap (the
    # async ingest worker + the foreground store touching mira.db) should
    # queue, not error out — an unhandled OperationalError here crashes the
    # app (2026-06-17 incident).
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _schema_info_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_info'"
    ).fetchone()
    return row is not None


def get_version(conn: sqlite3.Connection) -> Optional[int]:
    """Return the schema version recorded in ``schema_info``, or ``None`` if uninitialised."""
    if not _schema_info_exists(conn):
        return None
    row = conn.execute("SELECT schema_version FROM schema_info WHERE id = 1").fetchone()
    return int(row["schema_version"]) if row else None


def get_schema_info(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    """Return the whole ``schema_info`` row (version + app_version + timestamps), or ``None``."""
    if not _schema_info_exists(conn):
        return None
    return conn.execute("SELECT * FROM schema_info WHERE id = 1").fetchone()


def integrity_check(conn: sqlite3.Connection) -> str:
    """Run ``PRAGMA integrity_check`` and return the first row's result string.

    Healthy databases return ``'ok'``; anything else is a corruption signal that
    callers (the user_store open path) surface to the user before any read.
    Multi-row results (multiple errors) collapse to the first line — the
    presence of *any* problem is what callers act on.
    """
    row = conn.execute("PRAGMA integrity_check").fetchone()
    return str(row[0]) if row else ""


# --------------------------------------------------------------------------- #
# Migrations. Existing mira.db files are migrated in place on open —
# NEVER require recreation (`feedback_schema_evolution_policy`). SQLite ALTER
# TABLE ADD COLUMN does not support CHECK constraints; those are in the DDL
# for new databases only. Application code enforces valid values on migrated
# columns.
#
# SHIP-TIME RESET (Nelson 2026-06-12): at ship, the dev migration chain folds
# into the base DDL one last time and the counter restarts at v1 (same rule
# as the event store). No resets before then, none after.
# --------------------------------------------------------------------------- #


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """spec/61 (Share event Cuts) — the spec/53-era user-level ``cut``
    table retires (event Cuts live in event.db; cross-event gets its own
    session) and ``cut_template`` reshapes to the RECIPE (pool expression
    + filters + defaults + times + music). Neither table ever had a
    writer, so no data moves."""
    conn.execute("DROP TABLE IF EXISTS cut")
    conn.execute("DROP TABLE IF EXISTS cut_template")
    conn.execute("""
CREATE TABLE cut_template (
  id                TEXT PRIMARY KEY,
  name              TEXT NOT NULL,
  pool_expr_json    TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(pool_expr_json)),
  style_filter_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(style_filter_json)),
  type_filter       TEXT NOT NULL DEFAULT 'both' CHECK (type_filter IN ('both','photo','video')),
  default_state     TEXT NOT NULL DEFAULT 'skipped' CHECK (default_state IN ('picked','skipped')),
  target_s          INTEGER CHECK (target_s IS NULL OR target_s > 0),
  max_s             INTEGER CHECK (max_s IS NULL OR max_s > 0),
  photo_s           REAL NOT NULL DEFAULT 6.0 CHECK (photo_s > 0),
  music_category    TEXT,
  created_at        TEXT NOT NULL,
  extras_json       TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json))
)""")
    conn.execute("CREATE INDEX ix_cut_template_name ON cut_template(name)")


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """spec/81 Phase 2 — the cross-event surface arrives.

    Two new tables, neither touches existing rows:

    * ``global_items`` (spec/32 §3) — the cross-event projection of every
      event's items, populated by the gateway sync on event close + startup
      reconcile. Reconciled names: ``pick_state`` (was ``cull_state``),
      ``flag`` (was ``pick`` — the portfolio bit is distinct from the
      locked Pick/Skip decision verb).
    * ``saved_filter`` (spec/32 §4) — the cross-event DC home. Same
      typed-ref shape as event.db ``dynamic_collection`` (spec/81 §2 is
      scope-agnostic); the spec/32 §4 predicate-tree framing reconciles
      to ``expr_json`` (set algebra) + ``filters_json`` (the catalogue).

    Both arrive as CREATE TABLE, so the full CHECK + index complement
    survives (unlike ALTER TABLE ADD COLUMN). No existing row is touched.

    Uses individual ``conn.execute`` calls (not ``executescript``) so the
    wrapper's explicit BEGIN/COMMIT stays intact — ``executescript`` would
    auto-commit between statements and break the rollback path."""
    conn.execute("""
CREATE TABLE global_items (
  event_uuid        TEXT NOT NULL,
  event_name        TEXT NOT NULL DEFAULT '',
  item_id           TEXT NOT NULL,
  origin_relpath    TEXT,
  capture_time      TEXT,
  kind              TEXT,
  provenance        TEXT,
  classification    TEXT,
  iso               INTEGER,
  aperture_f        REAL,
  shutter_speed_s   REAL,
  focal_length_mm   REAL,
  flash_fired       INTEGER CHECK (flash_fired IS NULL OR flash_fired IN (0,1)),
  lens_model        TEXT,
  camera_id         TEXT,
  duration_ms       INTEGER,
  pick_state        TEXT,
  edit_state        TEXT,
  has_export        INTEGER NOT NULL DEFAULT 0 CHECK (has_export IN (0,1)),
  country           TEXT,
  country_code      TEXT,
  day_city          TEXT,
  day_sublocation   TEXT,
  stars             INTEGER CHECK (stars IS NULL OR (stars >= 1 AND stars <= 5)),
  color_label       TEXT,
  flag              INTEGER CHECK (flag IS NULL OR flag IN (0,1)),
  synced_at         TEXT NOT NULL,
  PRIMARY KEY (event_uuid, item_id)
)""")
    for sql in (
        "CREATE INDEX ix_global_items_event       ON global_items(event_uuid)",
        "CREATE INDEX ix_global_items_time        ON global_items(capture_time)",
        "CREATE INDEX ix_global_items_kind        ON global_items(kind)",
        "CREATE INDEX ix_global_items_class       ON global_items(classification)  WHERE classification IS NOT NULL",
        "CREATE INDEX ix_global_items_iso         ON global_items(iso)             WHERE iso IS NOT NULL",
        "CREATE INDEX ix_global_items_aperture    ON global_items(aperture_f)      WHERE aperture_f IS NOT NULL",
        "CREATE INDEX ix_global_items_shutter     ON global_items(shutter_speed_s) WHERE shutter_speed_s IS NOT NULL",
        "CREATE INDEX ix_global_items_focal       ON global_items(focal_length_mm) WHERE focal_length_mm IS NOT NULL",
        "CREATE INDEX ix_global_items_flash       ON global_items(flash_fired)     WHERE flash_fired = 1",
        "CREATE INDEX ix_global_items_lens        ON global_items(lens_model)      WHERE lens_model IS NOT NULL",
        "CREATE INDEX ix_global_items_camera      ON global_items(camera_id)       WHERE camera_id IS NOT NULL",
        "CREATE INDEX ix_global_items_pick        ON global_items(pick_state)      WHERE pick_state IS NOT NULL",
        "CREATE INDEX ix_global_items_edit        ON global_items(edit_state)      WHERE edit_state IS NOT NULL",
        "CREATE INDEX ix_global_items_has_export  ON global_items(has_export)      WHERE has_export = 1",
        "CREATE INDEX ix_global_items_country     ON global_items(country_code)    WHERE country_code IS NOT NULL",
        "CREATE INDEX ix_global_items_city        ON global_items(day_city)        WHERE day_city IS NOT NULL",
        "CREATE INDEX ix_global_items_stars       ON global_items(stars)           WHERE stars IS NOT NULL",
        "CREATE INDEX ix_global_items_color_label ON global_items(color_label)     WHERE color_label IS NOT NULL",
        "CREATE INDEX ix_global_items_flag        ON global_items(flag)            WHERE flag = 1",
    ):
        conn.execute(sql)
    conn.execute("""
CREATE TABLE saved_filter (
  id           TEXT PRIMARY KEY,
  tag          TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK (tag <> ''),
  description  TEXT,
  expr_json    TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(expr_json)),
  filters_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(filters_json)),
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  extras_json  TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json))
)""")


def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    """spec/81 Phase 2 Item 4 — cross-event Cut commit needs to route each
    member key to its source event's exported file without fanning out across
    every event.db. Stash the LATEST exported relpath per item in
    ``global_items`` so :class:`CrossEventCutSession` resolves members from
    one read. NULL = not yet exported (the ``#collected`` / ``#picked`` /
    ``#edited`` rungs).

    Additive — ALTER TABLE ADD COLUMN. The next gateway sync repopulates;
    existing rows show NULL until then (the cross-event commit path
    falls back to the fanout for un-synced events)."""
    conn.execute("ALTER TABLE global_items ADD COLUMN export_relpath TEXT")


def _migrate_v4_to_v5(conn: sqlite3.Connection) -> None:
    """spec/85 — the gear-profile arrives. One new table, no existing rows
    move (the photographer has never declared their kit before this slice).

    The shape matches the spec/85 §4 SQL: ``kind`` ∈ {camera, lens} +
    ``key`` (the matching ``global_items.camera_id`` / ``.lens_model``) +
    ``is_active`` + JSON ``preferred_genres`` + timestamp. The partial index
    on active rows keeps the spec/83 §4 picker's "main list" lookup cheap
    on big collections (one row per gear item is small, but the partial
    skips the inactive tail). Fresh installs get the full CHECK complement
    via the DDL; the CREATE TABLE here mirrors it so migrated stores carry
    the same constraints — unlike ALTER ADD COLUMN."""
    conn.execute("""
CREATE TABLE gear_profile (
  kind             TEXT NOT NULL CHECK (kind IN ('camera','lens')),
  key              TEXT NOT NULL,
  is_active        INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0,1)),
  preferred_genres TEXT CHECK (preferred_genres IS NULL OR json_valid(preferred_genres)),
  updated_at       TEXT NOT NULL,
  PRIMARY KEY (kind, key)
)""")
    conn.execute(
        "CREATE INDEX ix_gear_profile_active ON gear_profile(kind, is_active) "
        "WHERE is_active = 1")


def _migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    """spec/86 — push the event-level qualifiers into ``global_items`` so the
    cross-event filter system can prune by event predicate without joining
    back to N event.db files. Adds six columns (event_type, event_subtype,
    experience_type, participants, event_start, event_end) and the partial
    indexes the resolver uses.

    Additive — ALTER TABLE ADD COLUMN. Every existing row lands NULL; the
    next ``sync_event`` / startup reconcile populates the columns from the
    event row + the derived span (min/max of ``trip_day.date``). No data
    moves at migration time."""
    for col in (
        "event_type", "event_subtype", "experience_type",
        "participants",        # JSON array; nullable
        "event_start", "event_end",
    ):
        conn.execute(f"ALTER TABLE global_items ADD COLUMN {col} TEXT")
    # Partial indexes mirror the fresh-install DDL.
    for sql in (
        "CREATE INDEX ix_global_items_event_type      ON global_items(event_type)      WHERE event_type IS NOT NULL",
        "CREATE INDEX ix_global_items_event_subtype   ON global_items(event_subtype)   WHERE event_subtype IS NOT NULL",
        "CREATE INDEX ix_global_items_experience_type ON global_items(experience_type) WHERE experience_type IS NOT NULL",
        "CREATE INDEX ix_global_items_event_start     ON global_items(event_start)     WHERE event_start IS NOT NULL",
        "CREATE INDEX ix_global_items_event_end       ON global_items(event_end)       WHERE event_end IS NOT NULL",
    ):
        conn.execute(sql)


def _migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
    """spec/90 Phase 1 — schema additions for Recipe, Event Collection, Person.

    Three additions, all empty until later phases:

    * ``recipe`` (spec/90 §5.1) — the saved Cut/Collection configuration,
      flavoured cut/collection, composition_json carries Scope/Source/Filters/
      Rules/Otherwise/presentation. UNIQUE(flavour, name) lets a Cut Recipe
      and a Collection Recipe share a name across the flavour boundary.
    * ``event_collection`` (spec/90 §5.3) — saved event sets, same shape as
      ``dynamic_collection`` / ``saved_filter`` but the universe is events.
    * ``person.representative_face_id`` (spec/90 §5.2) — opaque pointer to a
      ``face`` row in event.db. ALTER TABLE ADD COLUMN — existing rows land
      NULL (no representative face chosen).

    The recipe + event_collection tables arrive as CREATE TABLE so the full
    CHECK + index complement survives. The person column is the only ALTER —
    its NULL default is the right legacy semantics. Uses individual
    ``conn.execute`` calls (not ``executescript``) so the wrapper's explicit
    BEGIN/COMMIT stays intact."""
    conn.execute("""
CREATE TABLE recipe (
  id                TEXT PRIMARY KEY,
  name              TEXT NOT NULL,
  flavour           TEXT NOT NULL CHECK (flavour IN ('cut','collection')),
  composition_json  TEXT NOT NULL CHECK (json_valid(composition_json)),
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  extras_json       TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json)),
  UNIQUE (flavour, name)
)""")
    conn.execute("CREATE INDEX ix_recipe_flavour ON recipe(flavour)")
    conn.execute("""
CREATE TABLE event_collection (
  id           TEXT PRIMARY KEY,
  tag          TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK (tag <> ''),
  expr_json    TEXT NOT NULL CHECK (json_valid(expr_json)),
  filters_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(filters_json)),
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  extras_json  TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json))
)""")
    conn.execute(
        "ALTER TABLE person ADD COLUMN representative_face_id TEXT")


MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [
    _migrate_v1_to_v2,
    _migrate_v2_to_v3,
    _migrate_v3_to_v4,
    _migrate_v4_to_v5,
    _migrate_v5_to_v6,
    _migrate_v6_to_v7,
]


def initialize(
    conn: sqlite3.Connection,
    *,
    app_version: str = "",
    created_at: Optional[str] = None,
) -> None:
    """Create a fresh schema at :data:`SCHEMA_VERSION` and stamp ``schema_info``.

    Must be called on an empty database. Idempotency is *not* assumed — callers
    use :func:`get_version` to decide between :func:`initialize` and
    :func:`migrate`.
    """
    if _schema_info_exists(conn):
        raise RuntimeError("initialize() called on an already-initialised database")
    stamp = created_at or _utc_now_iso()
    conn.executescript(DDL)  # autocommit: each DDL statement commits on its own
    conn.execute(
        "INSERT INTO schema_info (id, schema_version, app_version, created_at, updated_at) "
        "VALUES (1, ?, ?, ?, ?)",
        (SCHEMA_VERSION, app_version, stamp, stamp),
    )
    log.info("initialised mira.db schema v%s", SCHEMA_VERSION)


def migrate(conn: sqlite3.Connection) -> None:
    """Apply any pending migrations to reach :data:`SCHEMA_VERSION`.

    Raises if the DB is uninitialised or newer than this code understands
    ("upgrade Mira").
    """
    current = get_version(conn)
    if current is None:
        raise RuntimeError("migrate() called on an uninitialised database")
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"mira.db is schema v{current} but this build only understands "
            f"v{SCHEMA_VERSION}; upgrade Mira to open it"
        )
    while current < SCHEMA_VERSION:
        step = MIGRATIONS[current - 1]  # version N -> N+1
        conn.execute("BEGIN")
        try:
            step(conn)
            conn.execute(
                "UPDATE schema_info SET schema_version = ?, updated_at = ? WHERE id = 1",
                (current + 1, _utc_now_iso()),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        current += 1
    log.info("mira.db migrated to schema v%s", SCHEMA_VERSION)
