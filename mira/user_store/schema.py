"""SQLite schema for `mira.db` — the user-level data store (spec/53).

Single physical definition of the user store: the DDL, the owned
:data:`SCHEMA_VERSION`, the migration list, and connection setup
(WAL + foreign keys + ``NORMAL`` sync). Mirrors the per-event
:mod:`mira.store.schema` layer field for field; the two stores
share the same operational discipline (`feedback_schema_evolution_policy`).

v1 established the spec/53 §2 shape; **v2 (spec/61 slice 10)** retired the
user-level ``cut`` table (event Cuts live in event.db — file-based membership)
and reshaped ``cut_template`` to the New Cut dialog's RECIPE. Existing files
migrate in place on open; fresh files are created at the current version.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

log = logging.getLogger(__name__)

#: Schema version owned by us. Bump together with an entry appended to MIGRATIONS.
SCHEMA_VERSION = 2

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
CREATE TABLE person (
  id                       TEXT PRIMARY KEY,
  display_name             TEXT NOT NULL,
  reference_photo_relpath  TEXT,
  embedding_json           TEXT,
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
    conn.execute("PRAGMA synchronous = NORMAL")
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


MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [_migrate_v1_to_v2]


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
