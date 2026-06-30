"""SQLite schema for a Mira ``event.db`` — the **relational-core** rebuild.

Single physical definition of the event store: the DDL, the owned ``SCHEMA_VERSION``,
the migration list, and connection setup (WAL + foreign keys + ``NORMAL`` sync).

This is the schema of `spec/30-relational-schema-redesign.md` (APPROVED 2026-05-31) — a
properly relational model, **not** the legacy "SQLite-as-a-JSON-store" shape. The *why* is
`spec/31-relational-vision.md`. Greenfield: there are **no real events to migrate**, so this
is schema **v1** with an empty migration list — design the best schema, not the smallest diff.
While pre-release, new fields are folded into the DDL and dev events recreated (not migrated):
the 2026-06-10 reset (Nelson: all events deleted, "we can start fresh") folded the v2–v4
chain — spec/54 Look-choice tone columns + lineage export snapshots, the v3 'repeat'
bucket kind, and the spec/56 marker-partition video tables — into this base DDL.

**Vocabulary lock (spec/48 + spec/52):** phase enum values are ``('pick','edit')`` —
the four-phase pivot (Collect/Pick/Edit/Share) collapsed legacy Cull + Select into one
``'pick'`` phase, then spec/52 dropped ``'share'`` from phase_state / bucket / cache
enums entirely (Cut walks are item-by-item filtered per spec/51, not bucketed; Skip
is local to one Cut, so there is no global Share state). ``'share'`` survives only on
``lineage.phase`` (Cut exports still produce a tracked hardlink lineage). State enum
value is ``'picked'``. Greenfield-while-pre-release means we wipe events instead of
migrating across these renames — see SCHEMA_VERSION + MIGRATIONS.

**spec/52 retirements baked into the DDL:**
* Event-level fields ``tags_json``, ``notes``, ``google_album_name``,
  ``google_album_link``, ``whatsapp_message`` — all dropped.
* ``camera.is_reference`` + its unique index — dropped (phone EXIF is the
  reference for TZ calibration when present).
* Tables ``participant``, ``participant_device``, ``checklist_item``,
  ``distribution_action``, ``share_tag``, ``subset``, ``subset_member``,
  ``share_map`` — all dropped.
* New table ``photo_person`` (per-photo link to user-level people catalog,
  set up now for the upcoming people-tagging feature). Its spec/52 sibling
  ``photo_tag`` (ITEM-based Cut membership, the spec/51 plan) shipped and
  retired unused: spec/61 locked Cut membership as FILE-based, so schema v3
  replaced it with ``cut`` + ``cut_member`` (membership rows reference
  ``lineage`` — the exported finals — not items).
* ``item.tz_source`` enum realigned to ``('phone_auto','user_declared','pair_picker','none')``
  to match ``camera_day_tz.source``.

Load-bearing model decisions baked into the DDL:

* **One node per clip.** A clip/snapshot is ONE ``item`` across its whole life (a child of its
  source video via ``parent_item_id``). There is no ``video_moment`` / ``video_override`` /
  ``produced_item_id`` — its K/D is its own ``phase_state`` row, so nothing can desync.
* **The marker-partition model (spec/56).** Markers are first-class rows
  (``video_marker``) — the user's cut points on a source video. Consecutive markers
  define segments that tile the timeline (no gaps, no overlaps; the start/end
  markers are implicit and never stored). A segment is a clip item with a 1:1
  ``video_segment`` satellite holding only its ``seg_index`` — its identity is its
  POSITION in the marker order, never milliseconds, so moving a marker re-times a
  segment without touching its state or adjustments. Geometry is derived at read
  time (``core.video_segments``). Snapshots carry a 1:1 ``video_snapshot`` point
  satellite and auto-Pick at creation. The earlier ``clip_span`` (freeform in/out +
  label + full-span flag) retired with spec/56; labels live on ``item.subject``.
* **"Virtual" = nullable file identity.** A virtual clip is an ``item`` with
  ``origin_relpath IS NULL`` (zero bytes), guarded by an all-or-nothing CHECK. Export
  *materialises* it by filling the file columns in one UPDATE — nothing commits bytes
  before Export (spec/56 §1).
* **Whole-video export is not a special case**: it is the original single segment
  (zero markers → one segment), picked. ``is_full_span`` retired with it.
* Capture time is *virtual* (``capture_time_raw`` never mutated; corrected = derived). All
  stored paths are *relative to event root*; ``event_root`` itself is never stored (it is the
  DB's own folder). Real FKs + CHECK/UNIQUE constraints do the integrity work.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

log = logging.getLogger(__name__)

#: Schema version owned by us. Bump together with an entry appended to MIGRATIONS.
SCHEMA_VERSION = 25

# --------------------------------------------------------------------------- #
# Shared enum domains (spec/30 §3 + spec/52 cleanup). SQLite cannot DRY a CHECK
# across columns, so the domains are documented here and the identical CHECK
# is repeated per column.
#   PHASE ∈ ('pick','edit')                — Collect has no phase_state rows;
#                                            Share dropped from phase enums per
#                                            spec/52 (Cut walks per spec/51 are
#                                            item-by-item filtered, not bucketed;
#                                            Skip is local to one Cut, so there
#                                            is no global Share phase_state).
#                                            'share' survives only on lineage.phase
#                                            (Cut exports still materialize via
#                                            hardlinks; that lineage is tracked).
#   STATE ∈ ('skipped','candidate','picked')
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# DDL — spec/30 §3, statement-for-statement. Two strata, separated by table
# identity so the backup rule is trivial ("C" tables are never serialised):
#   D = durable (system-of-record, in the JSON backup)
#   C = derived/cache (regenerable, excluded from backup, droppable+rebuildable)
# --------------------------------------------------------------------------- #

DDL = r"""
-- ===== schema_info (C) — typed singleton; replaces the KV meta(k,v) ========
CREATE TABLE schema_info (
  id             INTEGER PRIMARY KEY CHECK (id = 1),
  schema_version INTEGER NOT NULL,
  app_version    TEXT NOT NULL,
  event_id       TEXT NOT NULL,
  created_at     TEXT NOT NULL
);

-- ===== event (D) — enforced singleton; trip_budget folded in ===============
CREATE TABLE event (
  id                INTEGER PRIMARY KEY CHECK (id = 1),     -- one row, enforced
  uuid              TEXT NOT NULL UNIQUE,                   -- stable external id
  name              TEXT NOT NULL,
  -- classification (spec/44): closed enum drives dashboard filter + EventCard badge + per-type
  -- extras editor rows. CHECK pinned in DDL for fresh databases.
  event_type        TEXT NOT NULL DEFAULT 'unclassified'
                          CHECK (event_type IN ('trip','session','occasion','project','unclassified')),
  event_subtype     TEXT,                                   -- free-text; UI offers curated presets per type
  description       TEXT NOT NULL DEFAULT '',                -- one-paragraph; shown on EventCard tooltip + indexed by search
  start_date        TEXT,
  end_date          TEXT,
  is_closed         INTEGER NOT NULL DEFAULT 0 CHECK (is_closed IN (0,1)),
  event_root_abs    TEXT,                                  -- cross-volume fallback ONLY; normally NULL
  budget_short_target_s INTEGER,
  budget_short_max_s    INTEGER,
  budget_long_target_s  INTEGER,
  budget_long_max_s     INTEGER,
  budget_video_share    REAL CHECK (budget_video_share IS NULL OR
                                    (budget_video_share >= 0 AND budget_video_share <= 1)),
  -- Structured event qualifiers (spec/64 — supersedes the spec/52
  -- Scope/Mood/Transport vocabulary). Duration + participants survive;
  -- the three retired axes are replaced by Context (baseline environment)
  -- / Experience Type (vibe/intent) / Creative Focus (photographic subjects,
  -- multi-select). The dashboard filter rail queries on each in plain SQL
  -- (Context + Experience Type via column equality; Creative Focus via
  -- json_each over the array). The per-unit duration cap retired with
  -- spec/64 — duration_value is now just a free integer > 0.
  duration_value    INTEGER CHECK (duration_value IS NULL OR duration_value > 0),
  duration_unit     TEXT CHECK (duration_unit IS NULL OR
                                duration_unit IN ('hours','days','weeks','months','years')),
  -- participants is a JSON array of category strings; multi-select chips
  -- per Nelson 2026-06-08. Free-form add (typed custom chip) is a future
  -- UI enhancement; the curated set is locked for now.
  participants      TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(participants)),
  -- Context: the baseline environment of the event (single-select). Closed
  -- enum app-side; the DDL keeps the column open so future values land
  -- without a migration. NULL = unset.
  context           TEXT,
  -- Experience Type: the primary vibe / intent / creative energy
  -- (single-select). Same shape as context.
  experience_type   TEXT,
  -- Creative Focus: photographic subjects (multi-select); JSON array of
  -- option keys. Empty array = blank; ["none"] = explicit "not a photo
  -- event"; the mutual-exclusion rule (none vs subjects) lives UI-side.
  creative_focus    TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(creative_focus)),
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  -- classification extras (per event_type) — spec/44 §1.6 + spec/52. No location keys
  -- at event level (location is per-day; aggregate at render time). No people keys
  -- (people-tagging moved to photo_person table; reference catalog is user-level).
  --   trip      → {}                            (no surviving keys; duration/scope/etc. are columns now)
  --   session   → {"target_subject":…}
  --   occasion  → {"host":…}
  --   project   → {"goal":…, "subject":…, "target_artifact":…}
  extras_json       TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json)),
  map_image_path    TEXT
);
-- spec/155: event.map_image_path is the event-level map slot, relative to
-- event_root (e.g. 'Maps/event.jpg'). NULL = no map attached.
CREATE INDEX ix_event_type            ON event(event_type);
CREATE INDEX ix_event_subtype         ON event(event_subtype);
CREATE INDEX ix_event_context         ON event(context)         WHERE context IS NOT NULL;
CREATE INDEX ix_event_experience_type ON event(experience_type) WHERE experience_type IS NOT NULL;

-- ===== trip_day (D) ========================================================
CREATE TABLE trip_day (
  day_number  INTEGER PRIMARY KEY,        -- the FK target everywhere; NULL day_number ⇒ "undated"
  date        TEXT,                        -- ISO date, nullable; NOT unique (smallest-day-number tie-break)
  description TEXT NOT NULL DEFAULT '',
  location    TEXT,                        -- free-text legacy field; kept for backward compat
  tz_minutes  INTEGER,
  hidden      INTEGER NOT NULL DEFAULT 0 CHECK (hidden IN (0,1)),  -- soft-hide: items derive visibility
  -- per-day country + country_code drive dashboard chrome (flag emoji), filter-by-country,
  -- and the events_index country aggregation. The user-facing location string lives in
  -- the dedicated `location` column above; this bag is structured machine-readable data only.
  -- expected extras_json keys: {"country":..., "country_code":...} (ISO 3166-1 alpha-2)
  extras_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json)),
  map_image_path TEXT
);
-- spec/155: trip_day.map_image_path is the per-day map slot, relative to
-- event_root (e.g. 'Maps/day-02.jpg'). NULL = no map attached.
CREATE INDEX ix_trip_day_date ON trip_day(date);

-- ===== camera (D) ==========================================================
-- spec/52 retired the reference-camera concept (phone EXIF is the reference
-- when present; pair-pick TZ calibration uses phone+camera photo pairs).
-- spec/123 — offset columns are integer SECONDS (×60 conversion at v16→v17)
-- so source 3 (measured pair raw delta) stays lossless.
CREATE TABLE camera (
  camera_id              TEXT PRIMARY KEY,           -- 'Make+Model' business key
  is_phone               INTEGER NOT NULL DEFAULT 0 CHECK (is_phone IN (0,1)),
  -- spec/127 — the canonical per-(camera, trip-TZ-segment) correction
  -- lives in camera_tz_correction. These columns persist as the
  -- single-segment summary (mirrored on save) so older read paths keep
  -- working; for a multi-segment trip they mirror the row for the
  -- predominant trip TZ.
  configured_tz_seconds  INTEGER,
  applied_offset_seconds INTEGER,
  applied_at             TEXT
);

-- ===== camera_tz_correction (D) — per-(camera, trip-TZ-segment), spec/127 =====
-- A trip-TZ segment = the set of plan days sharing one ``trip_day.tz_minutes``
-- value. A normal trip = one segment; a TZ-crossing trip (e.g. Nepal +5:45 with
-- a day at India +5:30) = two. ``configured_tz_seconds`` set => base came from
-- a declared zone (spec/123 source 1); NULL => base came from a measured pair /
-- manual offset (spec/123 source 3 — the spec/125 discriminator, now per
-- segment). ``nudge_seconds`` is the fine ±MM:SS adjustment on top.
-- ``applied_offset_seconds`` = base + nudge, denormalized for quick recompute.
-- FK to camera cascades — drop the camera, drop its corrections.
CREATE TABLE camera_tz_correction (
  camera_id              TEXT NOT NULL REFERENCES camera(camera_id) ON DELETE CASCADE,
  trip_tz_seconds        INTEGER NOT NULL,
  configured_tz_seconds  INTEGER,
  nudge_seconds          INTEGER NOT NULL DEFAULT 0,
  applied_offset_seconds INTEGER NOT NULL DEFAULT 0,
  applied_at             TEXT,
  PRIMARY KEY (camera_id, trip_tz_seconds)
);
CREATE INDEX ix_camera_tz_correction_tz ON camera_tz_correction(trip_tz_seconds);

-- ===== item (D) — the spine; ONE node per clip; file identity nullable ======
CREATE TABLE item (
  id                     TEXT PRIMARY KEY,
  kind                   TEXT NOT NULL CHECK (kind IN ('photo','video')),
  provenance             TEXT NOT NULL DEFAULT 'captured'
                              CHECK (provenance IN ('captured','snapshot','clip','stack_output','authored')),
  -- FILE IDENTITY — nullable iff the node is still virtual --------------------
  origin_relpath         TEXT UNIQUE,           -- NULL while virtual
  sha256                 TEXT,
  byte_size              INTEGER CHECK (byte_size IS NULL OR byte_size >= 0),
  materialized_at        TEXT,                  -- when bytes were written (NULL = virtual)
  -- 'pick' retired from this enum with spec/56 (bytes never commit during
  -- deciding; clips/snapshots materialise at Export, under the Edit phase).
  materialized_phase     TEXT CHECK (materialized_phase IN
                              ('ingest','edit') OR materialized_phase IS NULL),
  -- identity / placement ------------------------------------------------------
  camera_id              TEXT REFERENCES camera(camera_id) ON DELETE RESTRICT,
  day_number             INTEGER REFERENCES trip_day(day_number) ON DELETE SET NULL,
  parent_item_id         TEXT REFERENCES item(id) ON DELETE CASCADE,   -- child → source video
  capture_time_raw       TEXT,                  -- virtual EXIF, NEVER mutated
  capture_time_corrected TEXT,                  -- derived = raw + offset; the sort key
  tz_offset_seconds      INTEGER NOT NULL DEFAULT 0,  -- spec/123: integer seconds (lossless)
  tz_source              TEXT NOT NULL DEFAULT 'none' CHECK (tz_source IN ('phone_auto','user_declared','pair_picker','none')),
  classification         TEXT,
  classification_source  TEXT CHECK (classification_source IN ('auto','user') OR classification_source IS NULL),
  classification_rules_version TEXT,
  classification_needs_review  INTEGER NOT NULL DEFAULT 0 CHECK (classification_needs_review IN (0,1)),  -- 1 = auto-classified but flagged uncertain; Select nudge reads this
  classification_confidence    REAL,             -- classifier score 0..1 (spec/58) — the Edit Style button's red→green ramp reads it; NULL = never auto-scored
  sharpness_score        REAL,                  -- lazy cache (recoverable by recompute)
  sharpness_metric       TEXT,
  duration_ms            INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),  -- video length (NULL = still / un-probed)
  -- per-item Subject (Nelson 2026-06-08) — free-text user annotation: bird
  -- species, plant name, person, location landmark, anything the user wants
  -- to research later (e-bird / iNaturalist / Wikipedia). Applies to both
  -- photos and clips (item.kind covers both). UI surface TBD; for now this
  -- is just the storage column.
  subject                TEXT,
  extras_json            TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json)),  -- sanctioned escape hatch for future per-item fields; DEFAULT '{}' so json_set always works
  -- EXIF technical facets — captured at ingest from the SAME exiftool pass; NULL = unknown/video ----
  iso                    INTEGER CHECK (iso IS NULL OR iso > 0),          -- sensor sensitivity (exploration: high-ISO filter)
  aperture_f             REAL    CHECK (aperture_f IS NULL OR aperture_f > 0),   -- f-number (exploration: wide-open filter)
  shutter_speed_s        REAL    CHECK (shutter_speed_s IS NULL OR shutter_speed_s > 0), -- seconds (exploration: long-exposure filter)
  focal_length_mm        REAL    CHECK (focal_length_mm IS NULL OR focal_length_mm > 0), -- actual focal length, not 35mm-equivalent
  flash_fired            INTEGER CHECK (flash_fired IN (0,1)),            -- 1 = flash fired (exploration: flash filter)
  lens_model             TEXT,                                            -- lens string (exploration: per-lens collection)
  -- bracket detection — populated by ingest bracket detector; NULL = not a bracket ----------------
  bracket_group_id       TEXT,                    -- shared across all frames of one bracket set
  bracket_role           TEXT CHECK (bracket_role IN ('leader','member') OR bracket_role IS NULL),
  quarantine_status      TEXT NOT NULL DEFAULT 'ok'
                              CHECK (quarantine_status IN ('ok','no_timestamp','recovered')),
  recovered_from_filename INTEGER NOT NULL DEFAULT 0 CHECK (recovered_from_filename IN (0,1)),
  -- spec/159 §6+ (Nelson 2026-06-30 eyeball pivot) — preferred-version
  -- flag for the VIRTUAL Mira-render intent. When the user picks the
  -- Mira-pending tile in Compare on a cluster whose Mira render hasn't
  -- materialised yet, this column flips to 1; downstream Cuts compose
  -- reads it as "use Mira when it ships." Mutually exclusive with any
  -- ``lineage.is_preferred = 1`` row for the same item — the gateway's
  -- setters clear each other's flag inside one transaction. When a
  -- Mira render later materialises (a fresh ``mira_render`` lineage
  -- row), the next preferred-resolution pass can promote that row +
  -- clear this column.
  preferred_virtual_mira INTEGER NOT NULL DEFAULT 0 CHECK (preferred_virtual_mira IN (0,1)),
  created_at             TEXT NOT NULL,
  -- VIRTUAL/MATERIALISED invariant: bytes present iff materialized -----------
  CHECK ( (origin_relpath IS NULL  AND sha256 IS NULL  AND byte_size IS NULL  AND materialized_at IS NULL)
       OR (origin_relpath IS NOT NULL AND sha256 IS NOT NULL AND byte_size IS NOT NULL AND materialized_at IS NOT NULL) ),
  -- only derived kinds may be virtual; captured/stack_output/authored always have bytes
  -- (authored items are rendered to PNG/JPG at authoring time — spec/52 §4)
  CHECK ( origin_relpath IS NOT NULL OR provenance IN ('snapshot','clip') ),
  -- captured items carry a camera + raw time (authored items don't — they have no camera):
  CHECK ( provenance <> 'captured' OR (camera_id IS NOT NULL AND capture_time_raw IS NOT NULL) ),
  -- the parent triangle: captured/stack_output/authored are roots; snapshot/clip have a parent
  CHECK ( (provenance IN ('snapshot','clip')) = (parent_item_id IS NOT NULL) ),
  -- kind/provenance coherence: a clip IS a video, a snapshot IS a photo
  CHECK ( provenance <> 'clip'     OR kind = 'video' ),
  CHECK ( provenance <> 'snapshot' OR kind = 'photo' )
);
CREATE INDEX ix_item_parent         ON item(parent_item_id);                                  -- video children
CREATE INDEX ix_item_nav            ON item(provenance, day_number, capture_time_corrected);  -- navigator hot path
CREATE INDEX ix_item_camera         ON item(camera_id);                                       -- TZ recompute
CREATE INDEX ix_item_classification ON item(classification);                                  -- subset resolution
CREATE INDEX ix_item_time           ON item(capture_time_corrected);                          -- chronological merges
CREATE INDEX ix_item_stars          ON item(json_extract(extras_json, '$.stars'))
    WHERE json_extract(extras_json, '$.stars') IS NOT NULL;                                   -- share: star rating (1-5)
CREATE INDEX ix_item_color_label    ON item(json_extract(extras_json, '$.color_label'))
    WHERE json_extract(extras_json, '$.color_label') IS NOT NULL;                             -- share: color label (LRC-compatible)
CREATE INDEX ix_item_iso            ON item(iso)             WHERE iso IS NOT NULL;            -- exploration: high-ISO filter
CREATE INDEX ix_item_aperture       ON item(aperture_f)      WHERE aperture_f IS NOT NULL;     -- exploration: wide-open filter
CREATE INDEX ix_item_shutter        ON item(shutter_speed_s) WHERE shutter_speed_s IS NOT NULL;-- exploration: long-exposure filter
CREATE INDEX ix_item_focal          ON item(focal_length_mm) WHERE focal_length_mm IS NOT NULL;-- exploration: focal-length filter
CREATE INDEX ix_item_flash          ON item(flash_fired)     WHERE flash_fired = 1;            -- exploration: flash filter
CREATE INDEX ix_item_lens           ON item(lens_model)      WHERE lens_model IS NOT NULL;     -- exploration: per-lens collection
CREATE INDEX ix_item_bracket        ON item(bracket_group_id) WHERE bracket_group_id IS NOT NULL; -- bracket/stack queries

-- ===== visible_item (view) — soft-hide projection ==========================
-- An item is *visible* unless its trip day is hidden. Items with NULL day_number
-- (undated) have no day to hide them, so they are always visible. Phase-facing reads +
-- completion metrics select through this view so a hidden day is disregarded everywhere;
-- backup/restore (store.all → save/load_document) reads the base `item` table so hidden
-- content still round-trips. Centralises the rule (spec/14 §5C.1).
CREATE VIEW visible_item AS
  SELECT item.* FROM item
  LEFT JOIN trip_day ON item.day_number = trip_day.day_number
  WHERE trip_day.day_number IS NULL OR trip_day.hidden = 0;

-- ===== camera_calibration_pair (D) — replaces calibration_json =============
CREATE TABLE camera_calibration_pair (
  id              TEXT PRIMARY KEY,
  camera_id       TEXT NOT NULL REFERENCES camera(camera_id) ON DELETE CASCADE,
  ref_item_id     TEXT REFERENCES item(id) ON DELETE SET NULL,    -- the reference photo used
  subject_item_id TEXT REFERENCES item(id) ON DELETE SET NULL,    -- this camera's photo
  ref_time        TEXT NOT NULL,
  camera_time     TEXT NOT NULL,
  offset_minutes  INTEGER NOT NULL,
  created_at      TEXT NOT NULL
);
CREATE INDEX ix_calib_camera ON camera_calibration_pair(camera_id);

-- ===== phase_state (D) — the ONE K/D table for EVERYTHING ==================
-- Per the Slice 0 vocabulary rename: phase enum collapses cull+select into 'pick';
-- state enum's 'picked' becomes 'picked'. Collect has no phase_state rows.
CREATE TABLE phase_state (
  item_id       TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  phase         TEXT NOT NULL CHECK (phase IN ('pick','edit')),
  state         TEXT NOT NULL DEFAULT 'skipped' CHECK (state IN ('skipped','candidate','picked')),
  derived_dirty INTEGER NOT NULL DEFAULT 0 CHECK (derived_dirty IN (0,1)),
  decided_at    TEXT,
  committed_at  TEXT,
  PRIMARY KEY (item_id, phase)
);
CREATE INDEX ix_phase_state_hist      ON phase_state(phase, state);
CREATE INDEX ix_phase_state_item      ON phase_state(item_id, phase);
CREATE INDEX ix_phase_dirty           ON phase_state(phase, derived_dirty);
CREATE INDEX ix_phase_state_committed ON phase_state(phase, committed_at);

-- ===== video_marker (D) — user cut points on a source video (spec/56) =======
-- The marker-partition model: every video is born with two IMPLICIT markers
-- (start + end) that are never stored; rows here are the USER's cut points
-- only (zero rows = the video is one segment). Consecutive markers define
-- segments that tile the timeline — overlapping clips are impossible by
-- construction. Trimming IS moving a marker (the spec/56 §4 trim deltas
-- retired with this table's arrival). 0 < at_ms (a marker at 0 would shadow
-- the implicit start); the at_ms < duration bound is gateway-enforced (a
-- CHECK cannot reach item.duration_ms). UNIQUE forbids zero-length segments.
CREATE TABLE video_marker (
  id            TEXT PRIMARY KEY,
  video_item_id TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,  -- the SOURCE video
  at_ms         INTEGER NOT NULL CHECK (at_ms > 0),
  created_at    TEXT NOT NULL,
  UNIQUE (video_item_id, at_ms)
);

-- ===== video_segment (D) — 1:1 satellite for segment items (spec/56) ========
-- A segment is an item (kind='video', provenance='clip', child of its source
-- video) whose identity is its POSITION in the marker order: seg_index k spans
-- boundary k → boundary k+1 over (implicit start, markers…, implicit end).
-- Geometry is deliberately NOT stored — it derives from video_marker at read
-- time (core.video_segments.segment_bounds), so moving a marker re-times a
-- segment without touching its row, its phase_state or its video_adjustment
-- (the spec/56 locked identity rule). The gateway maintains the invariant
-- count(segments) = count(markers) + 1, seg_index dense from 0; rows
-- materialise lazily on first workshop touch. video_item_id mirrors
-- item.parent_item_id (the same acknowledged denormalization clip_span
-- carried) so SQLite can host UNIQUE(video_item_id, seg_index).
CREATE TABLE video_segment (
  item_id       TEXT PRIMARY KEY REFERENCES item(id) ON DELETE CASCADE,
  video_item_id TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  seg_index     INTEGER NOT NULL CHECK (seg_index >= 0),
  created_at    TEXT NOT NULL,
  UNIQUE (video_item_id, seg_index)
);

-- ===== video_snapshot (D) — 1:1 satellite for snapshot items (spec/56) ======
-- A snapshot is an item (kind='photo', provenance='snapshot') anchored at a
-- point on the source timeline. Creating one auto-Picks it (phase_state
-- edit/picked — placing a snapshot IS the intent). No uniqueness on
-- (video, at_ms): two snapshots of one frame with different crops are
-- legitimate. At_ms ≤ duration is gateway-enforced when duration is known.
CREATE TABLE video_snapshot (
  item_id       TEXT PRIMARY KEY REFERENCES item(id) ON DELETE CASCADE,
  video_item_id TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  at_ms         INTEGER NOT NULL CHECK (at_ms >= 0),
  created_at    TEXT NOT NULL
);
CREATE INDEX ix_video_snapshot_video ON video_snapshot(video_item_id, at_ms);

-- ===== adjustment (D) — photo Edit edits; crop promoted, tone = D4 blob ==
CREATE TABLE adjustment (
  item_id      TEXT PRIMARY KEY REFERENCES item(id) ON DELETE CASCADE,
  -- Tone payload = the Look CHOICE (spec/54 §6, zero-sliders lock),
  -- recomputed to Params deterministically at render/export. ``look`` is
  -- app-enforced against core.photo_auto.available_looks() — no CHECK,
  -- so future Looks don't need a table rebuild. ``style`` NULL = use the
  -- item's classification.
  style        TEXT,
  -- Baseline Look is Original = identity / no processing (Nelson
  -- 2026-06-18). Natural is a deliberate Look choice, not the default.
  look         TEXT NOT NULL DEFAULT 'original',
  creative_filter TEXT,        -- spec/54 §8: Mira filter key; NULL = none
  crop_x       REAL CHECK (crop_x IS NULL OR (crop_x >= 0 AND crop_x <= 1)),
  crop_y       REAL CHECK (crop_y IS NULL OR (crop_y >= 0 AND crop_y <= 1)),
  crop_w       REAL CHECK (crop_w IS NULL OR (crop_w >  0 AND crop_w <= 1)),
  crop_h       REAL CHECK (crop_h IS NULL OR (crop_h >  0 AND crop_h <= 1)),
  crop_angle   REAL NOT NULL DEFAULT 0,
  rotation     INTEGER NOT NULL DEFAULT 0 CHECK (rotation IN (0,90,180,270)),
  aspect_label TEXT,
  -- spec/54 §3.2 + Nelson 2026-06-13 strength slider: 0..2 multiplier on
  -- the whole-Look Params (the .scaled(s) call at the engine seam).
  -- 1.0 = the Look exactly as it ships; 0.0 = identity (effectively
  -- Original); 2.0 = exaggerated. Stored per photo so a strength edit
  -- survives session ends.
  look_strength REAL NOT NULL DEFAULT 1.0
                CHECK (look_strength >= 0 AND look_strength <= 2),
  -- spec/115 — per-image USER exposure (EV), added on top of the Look's
  -- already-strength-scaled exposure. ±2 EV swing covers a one-stop
  -- recovery in either direction; the slider's double-click resets to 0.
  user_exposure REAL NOT NULL DEFAULT 0.0
                CHECK (user_exposure >= -2 AND user_exposure <= 2),
  -- spec/156 — per-image creative-filter STRENGTH (−2..+2 graduation in
  -- the Edit filter group). Scales the filter's blend amount: +2 = the
  -- shipped recipe, 0 (default) ≈ 70 %, −2 ≈ 40 %. Inert when
  -- ``creative_filter`` is NULL.
  filter_strength REAL NOT NULL DEFAULT 0.0
                CHECK (filter_strength >= -2 AND filter_strength <= 2),
  edit_exported INTEGER NOT NULL DEFAULT 0 CHECK (edit_exported IN (0,1)),
  CHECK ( (crop_x IS NULL) = (crop_y IS NULL)
      AND (crop_x IS NULL) = (crop_w IS NULL)
      AND (crop_x IS NULL) = (crop_h IS NULL) )            -- crop rect all-or-nothing
);

-- ===== video_adjustment (D) — segment Edit refinements (spec/56) ==========
-- Keyed to the SEGMENT item (a segment is its own item, provenance='clip').
-- Per-segment video extras (audio, speed, stabilise, fade) stay per segment
-- per spec/56 §1; the trim deltas retired (markers ARE the trim). Snapshots
-- take the photo ``adjustment`` table instead — full photo treatment.
CREATE TABLE video_adjustment (
  item_id      TEXT PRIMARY KEY REFERENCES item(id) ON DELETE CASCADE,   -- the segment item
  -- Same Look-choice tone payload as ``adjustment`` (spec/54 §6 + §7 #1:
  -- Looks on video, uncalibrated — photo-fitted constants on the rep frame).
  look         TEXT NOT NULL DEFAULT 'natural',
  creative_filter TEXT,        -- spec/54 §8: Mira filter key; NULL = none
  crop_x REAL CHECK (crop_x IS NULL OR (crop_x >= 0 AND crop_x <= 1)),
  crop_y REAL CHECK (crop_y IS NULL OR (crop_y >= 0 AND crop_y <= 1)),
  crop_w REAL CHECK (crop_w IS NULL OR (crop_w >  0 AND crop_w <= 1)),
  crop_h REAL CHECK (crop_h IS NULL OR (crop_h >  0 AND crop_h <= 1)),
  box_angle    REAL NOT NULL DEFAULT 0,
  aspect_ratio_label TEXT,
  style        TEXT,
  rep_frame_ms INTEGER CHECK (rep_frame_ms IS NULL OR rep_frame_ms >= 0),
  include_audio       INTEGER NOT NULL DEFAULT 1 CHECK (include_audio IN (0,1)),
  rotation_degrees    INTEGER NOT NULL DEFAULT 0 CHECK (rotation_degrees IN (0,90,180,270)),
  audio_volume        REAL NOT NULL DEFAULT 1.0 CHECK (audio_volume >= 0),
  audio_fade_ms       INTEGER NOT NULL DEFAULT 0 CHECK (audio_fade_ms >= 0),
  speed               REAL NOT NULL DEFAULT 1.0 CHECK (speed > 0),
  stabilise           REAL NOT NULL DEFAULT 0 CHECK (stabilise >= 0 AND stabilise <= 1),
  -- spec/156 — per-segment creative-filter STRENGTH (−2..+2), the video
  -- twin of ``adjustment.filter_strength``.
  filter_strength     REAL NOT NULL DEFAULT 0.0
                      CHECK (filter_strength >= -2 AND filter_strength <= 2),
  CHECK ( (crop_x IS NULL) = (crop_y IS NULL)
      AND (crop_x IS NULL) = (crop_w IS NULL)
      AND (crop_x IS NULL) = (crop_h IS NULL) )
);

-- ===== stack_bracket (D) + stack_member (D) ================================
-- ``producer`` (spec/109 §5) — who fused this bracket's master:
--   'external' — adopted from an external stacker (Helicon, Zerene, LRC)
--                via the spec/57 round trip (default — see migration v13).
--   'mira'     — fused in-app by ``core.exposure_fusion`` on the batch
--                engine (the spec/109 lane; exposure brackets only).
-- Drives the spec/89 origin wordmark on the consolidation badge: 'mira'
-- → ``Mira``, 'external' → ``ext`` (post-spec/108 flatten).
CREATE TABLE stack_bracket (
  bracket_id     TEXT PRIMARY KEY,
  kind           TEXT NOT NULL CHECK (kind IN ('focus','exposure')),
  action         TEXT CHECK (action IN ('stacked','picked','skipped') OR action IS NULL),
  picked_index   INTEGER NOT NULL DEFAULT -1,
  output_item_id TEXT REFERENCES item(id) ON DELETE SET NULL,   -- the merged result, an item
  day_number     INTEGER REFERENCES trip_day(day_number) ON DELETE SET NULL,
  producer       TEXT NOT NULL DEFAULT 'external' CHECK (producer IN ('mira','external'))
);
CREATE INDEX ix_stack_day ON stack_bracket(day_number);

CREATE TABLE stack_member (
  bracket_id TEXT NOT NULL REFERENCES stack_bracket(bracket_id) ON DELETE CASCADE,
  item_id    TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  ordinal    INTEGER NOT NULL CHECK (ordinal >= 0),
  PRIMARY KEY (bracket_id, item_id),
  UNIQUE (bracket_id, ordinal)                                      -- ordered frames, no collisions
);
CREATE INDEX ix_stack_member_item ON stack_member(item_id);

-- ===== dynamic_collection (D) — the DC: a formula, resolved live (spec/81) ===
-- The DC is the live-query NOUN (spec/81 §2): set algebra over operands +
-- filters, resolved on demand — never a stored member set. ``tag`` is the
-- canonical lowercase slug WITHOUT the '#' (core.cut_names emits lowercase;
-- COLLATE NOCASE is belt-and-braces). DC and Cut have SEPARATE tag namespaces
-- (Nelson 2026-06-16): a DC and a Cut may share a #name — operands are typed
-- by ``kind`` so there is no ambiguity. The built-in #exported is NOT a row;
-- it is the base-universe token ``"exported"`` an operand names.
--   expr_json    — ordered left-to-right pairs [[<op>, <operand>], …] where
--                  <op> ∈ '+' union / '-' difference / '&' intersection
--                  (display ∩) and <operand> is the base token "exported" OR a
--                  typed ref {"kind":"dc"|"cut","id":…,"tag":…}. No precedence;
--                  grouping is nesting a sub-DC operand (spec/81 §2).
--   filters_json — event scope = {"styles":[…],"media_type":"both"}; readers
--                  tolerate missing keys (the spec/32 Phase-2 catalogue extends
--                  this object without a rewrite).
CREATE TABLE dynamic_collection (
  id           TEXT PRIMARY KEY,
  tag          TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK (tag <> ''),
  expr_json    TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(expr_json)),
  filters_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(filters_json)),
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  extras_json  TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json))
);

-- ===== cut (D) — a Cut: a FROZEN materialisation of a DC (spec/81 §3) ========
-- The only thing playable / exportable. ``tag`` is the canonical lowercase
-- slug WITHOUT the '#' (display prepends it; the export folder is Cuts/<tag>/).
-- A Cut is always frozen (spec/81 §1): it holds frozen members (cut_member) +
-- the formula resolved at pin time (``expr_snapshot_json``) and NEVER re-queries
-- its DC live. target_s/max_s NULL = no time limit. Style + media filters live
-- on the DC's filters_json, NOT here (Nelson 2026-06-16) — the Cut is
-- self-describing via expr_snapshot_json + its frozen members.
-- No target-path column (spec/81 §5; charter invariant #2). Overlays
-- (spec/81 §3.1): overlay_fields_json = selected provenance fields
-- ('when'/'where'/'how1'/'how2'; [] = off); overlay_mode = 'embedded'|'burn_in'
-- (NULL = inherit the settings default). separators (spec/61 §4, default ON).
--
-- spec/81 Phase 2 (schema v8): ``source_dc_id`` is now OPAQUE — the FK to
-- ``dynamic_collection(id)`` is dropped because a cross-event Cut's source DC
-- lives in mira.db (``saved_filter``), not event.db. ``source_dc_kind``
-- discriminates ('event' / 'user'); NULL = legacy / event-scope.
-- The freeze invariant (spec/81 §5) is preserved at the gateway level — a
-- delete-DC operation explicitly NULLs source_dc_id on any Cut that pointed
-- at it (the FK ON DELETE SET NULL behaviour is replaced by gateway logic).
CREATE TABLE cut (
  id                  TEXT PRIMARY KEY,
  tag                 TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK (tag <> ''),
  source_dc_id        TEXT,
  source_dc_kind      TEXT CHECK (source_dc_kind IN ('event','user') OR source_dc_kind IS NULL),
  expr_snapshot_json  TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(expr_snapshot_json)),
  target_s            INTEGER CHECK (target_s IS NULL OR target_s > 0),
  max_s               INTEGER CHECK (max_s IS NULL OR max_s > 0),
  photo_s             REAL NOT NULL DEFAULT 6.0 CHECK (photo_s > 0),
  -- spec/152 §3 — per-Cut crossfade transition between consecutive
  -- slides, in ms. NULL = fall back to Settings.default_transition_ms
  -- at read time (the New / Adjust dialog seeds the field with the
  -- global default but persists the user's actual choice; a NULL
  -- column means the user never overrode the global).
  transition_ms       INTEGER CHECK (transition_ms IS NULL OR transition_ms >= 0),
  default_state       TEXT NOT NULL DEFAULT 'skipped' CHECK (default_state IN ('picked','skipped')),
  music_category      TEXT,
  separators          INTEGER NOT NULL DEFAULT 1 CHECK (separators IN (0,1)),
  overlay_fields_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(overlay_fields_json)),
  overlay_mode        TEXT CHECK (overlay_mode IN ('embedded','burn_in') OR overlay_mode IS NULL),
  last_exported_at    TEXT,
  -- spec/111 — slideshow canvas aspect (16:9 / 4:3 / 3:2 / 1:1). Drives
  -- the separator/opener card dimensions on export AND the PTE [Main]
  -- AspectRatio override (spec/107). Default '16:9' so existing rows
  -- and fresh installs both behave like the pre-spec/111 setting.
  aspect              TEXT NOT NULL DEFAULT '16:9'
                       CHECK (aspect IN ('16:9','4:3','3:2','1:1')),
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL,
  -- the sanctioned escape hatch (house pattern); holds e.g. card_style
  -- ('black'|'single'|'multi' — the separator/opener colour choice,
  -- Nelson 2026-06-12). Rendered, never queried.
  extras_json         TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json))
);

-- ===== cut_member (D) — membership = source FILES (spec/61 §1.2 + §8) =======
-- One row per (cut, member). Deleting a Cut cascades its membership away
-- (cut_id FK ON DELETE CASCADE). Cuts are zero-byte until export
-- materializes links (spec/61 §1.3).
--
-- spec/81 Phase 2 v8 reshaped: dropped FK on export_relpath (cross-event
-- members reference other events' lineage), added nullable event_id (the
-- source event's UUID; NULL = legacy event-scope).
--
-- spec/81 Phase 2 v9 (Item 6, spec/61 §6 + §8): GRAB-ORIGINALS. A cross-
-- event Cut may pull in items that are still ``#collected`` / ``#picked`` /
-- ``#edited`` (no lineage row, no shipped JPEG). The export pipeline grabs
-- the ORIGINAL bytes (the source event's ``Original Media/<...>``) instead.
-- ``kind`` discriminates ('export' = pre-exported lineage member, 'grab' =
-- pull-from-original). ``export_relpath`` is set for 'export'; ``origin_relpath``
-- is set for 'grab'. PK becomes ``(cut_id, member_id)`` with ``member_id`` =
-- the content-stable path (export_relpath OR origin_relpath) so the same
-- item can't appear twice as both an export AND a grab in one Cut.
CREATE TABLE cut_member (
  cut_id         TEXT NOT NULL REFERENCES cut(id) ON DELETE CASCADE,
  member_id      TEXT NOT NULL,
  kind           TEXT NOT NULL DEFAULT 'export' CHECK (kind IN ('export','grab')),
  export_relpath TEXT,
  origin_relpath TEXT,
  event_id       TEXT,
  added_at       TEXT NOT NULL,
  PRIMARY KEY (cut_id, member_id),
  CHECK ( (kind = 'export' AND export_relpath IS NOT NULL AND origin_relpath IS NULL)
       OR (kind = 'grab'   AND export_relpath IS NULL     AND origin_relpath IS NOT NULL) )
);
CREATE INDEX ix_cut_member_file   ON cut_member(export_relpath) WHERE export_relpath IS NOT NULL;
CREATE INDEX ix_cut_member_origin ON cut_member(origin_relpath) WHERE origin_relpath IS NOT NULL;
CREATE INDEX ix_cut_member_event  ON cut_member(event_id)       WHERE event_id IS NOT NULL;
CREATE INDEX ix_cut_member_kind   ON cut_member(kind);

-- ===== photo_person (D) — per-photo links to user-level people catalog =====
-- The people catalog (one reference photo per person) lives outside event.db
-- at the user level (designed but not yet implemented). This table links
-- items in this event to person ids in that catalog; set up now so the
-- people-tagging feature lands without a migration.
--   source='user' — explicit user tag; confidence is unused (NULL).
--   source='auto' — face-match suggestion; confidence is the matcher score.
CREATE TABLE photo_person (
  item_id    TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  person_id  TEXT NOT NULL,
  source     TEXT NOT NULL CHECK (source IN ('user','auto')),
  confidence REAL,
  tagged_at  TEXT NOT NULL,
  PRIMARY KEY (item_id, person_id)
);
CREATE INDEX ix_photo_person_person ON photo_person(person_id);

-- ===== face (D) — per-item detected face boxes (spec/90 §5.2) ================
-- Forward-compatible substrate for face recognition. Empty in v1 — the
-- detection pipeline is a separate sprint (spec/90 §7 Phase 6) — so Person
-- chips in a Recipe resolve to "no matches" leniently until rows arrive.
--
-- ``person_id`` is nullable: an unrecognized face stays NULL (the
-- ``#unrecognized_faces`` operand in spec/90 §4.3). The id references the
-- library-level ``person`` table in mira.db; no FK can span stores, so the
-- reference is opaque TEXT — same pattern as ``photo_person.person_id``.
-- Bounding box is normalised 0..1 over the item's pixel space so it stays
-- correct across thumb / proxy / original tiers (spec/63).
CREATE TABLE face (
  id           TEXT PRIMARY KEY,
  item_id      TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  person_id    TEXT,
  bbox_x       REAL CHECK (bbox_x IS NULL OR (bbox_x >= 0 AND bbox_x <= 1)),
  bbox_y       REAL CHECK (bbox_y IS NULL OR (bbox_y >= 0 AND bbox_y <= 1)),
  bbox_w       REAL CHECK (bbox_w IS NULL OR (bbox_w >  0 AND bbox_w <= 1)),
  bbox_h       REAL CHECK (bbox_h IS NULL OR (bbox_h >  0 AND bbox_h <= 1)),
  confidence   REAL,
  detected_at  TEXT NOT NULL
);
CREATE INDEX ix_face_item   ON face(item_id);
CREATE INDEX ix_face_person ON face(person_id) WHERE person_id IS NOT NULL;

-- ===== lineage (D) — real FKs both directions; discriminated ===============
CREATE TABLE lineage (
  export_relpath    TEXT PRIMARY KEY,
  phase             TEXT NOT NULL CHECK (phase IN ('edit','share')),
  source_kind       TEXT NOT NULL CHECK (source_kind IN ('item','bracket')),
  source_item_id    TEXT REFERENCES item(id) ON DELETE CASCADE,            -- 1→1 source
  source_bracket_id TEXT REFERENCES stack_bracket(bracket_id) ON DELETE CASCADE, -- N→1 stack source
  -- spec/54 §8 versions-as-exports: archival snapshot of the recipe
  -- (style/look/intensity/vibrance/creative_filter/crop) AND the resolved
  -- Params this export rendered with. Append-only — never re-read for
  -- rendering; the live adjustment row stays choice-only. exported_at
  -- orders a photo's versions in the Cut picker.
  recipe_json       TEXT,
  exported_at       TEXT,
  -- spec/72 §1 / spec/89 §1.4 Model B — origin signal. 'mira_render'
  -- = Mira's spec/60 batch produced this file; 'third_party' = the
  -- return scanner hardlinked it from Edited Media/ (LRC, Helicon,
  -- Capture One, etc.). The badge wordmark (LRC vs Helicon vs CO vs
  -- generic ext) is filename-inferred at the badge layer, not stored.
  provenance        TEXT NOT NULL DEFAULT 'mira_render'
                    CHECK (provenance IN ('mira_render', 'third_party')),
  -- spec/89 §1.2 / Block 1 D2.B — per-version intent for items with
  -- 2+ shipped rows (the versions cluster, Slice 5). Members enter
  -- the cluster in 'compare' so the cover paints Compare orange
  -- until the user decides each one to 'picked' (keep) or 'skipped'
  -- (drop on the next Export run). Single-version flat cells ignore
  -- this column — their intent rides phase_state(edit).
  intent_state      TEXT NOT NULL DEFAULT 'picked'
                    CHECK (intent_state IN ('compare', 'picked', 'skipped')),
  -- spec/144 — the TRUE on-disk duration of a clip-segment export, in ms.
  -- Recorded at clip-render time as ``(out_ms - in_ms) / speed`` so it
  -- reflects what the player actually sees (the time-stretched output,
  -- not the marker-bounds delta). NULL for photos and for legacy
  -- pre-migration video lineage rows; the surface readers fall back to
  -- ffprobing the file on disk when this column is NULL. NEVER reuse
  -- the source item's ``duration_ms`` for a clip — that's the WHOLE
  -- source video, not the segment.
  duration_ms       INTEGER,
  -- spec/159 — per-version ratings on the Exported Collection review
  -- surface. ``stars`` 1-5 or NULL (unrated); ``color_label`` one of the
  -- five LRC values or NULL; ``flag`` is the portfolio-keep toggle;
  -- ``to_delete`` marks the file for batch deletion via the
  -- "⌫ Delete N marked…" toolbar action (the unlink does NOT happen
  -- here — only at commit time). All four default to "untouched" so
  -- existing lineage rows read as unrated, unflagged, not-marked.
  stars             INTEGER CHECK (stars IS NULL OR (stars BETWEEN 1 AND 5)),
  color_label       TEXT    CHECK (color_label IS NULL OR color_label IN ('red','yellow','green','blue','purple')),
  flag              INTEGER NOT NULL DEFAULT 0 CHECK (flag IN (0,1)),
  to_delete         INTEGER NOT NULL DEFAULT 0 CHECK (to_delete IN (0,1)),
  -- spec/159 §6+ — at most one preferred version per ``source_item_id``.
  -- The gateway's ``set_lineage_preferred`` clears any sibling row's
  -- flag before setting a new one (single transaction) so downstream
  -- surfaces (Cuts compose) read a deterministic "which version to
  -- include." Single-version cells are implicitly preferred and
  -- don't need this flag.
  is_preferred      INTEGER NOT NULL DEFAULT 0 CHECK (is_preferred IN (0,1)),
  CHECK ( (source_kind='item'    AND source_item_id IS NOT NULL AND source_bracket_id IS NULL)
       OR (source_kind='bracket' AND source_bracket_id IS NOT NULL AND source_item_id IS NULL) )
);
CREATE INDEX ix_lineage_item    ON lineage(source_item_id);
CREATE INDEX ix_lineage_bracket ON lineage(source_bracket_id);
CREATE INDEX ix_lineage_phase   ON lineage(phase);
-- spec/159 — partial indexes for the Exported Collection filter (min
-- stars / colour label / flagged) + the batch-delete toolbar count.
CREATE INDEX ix_lineage_stars       ON lineage(stars)       WHERE stars IS NOT NULL;
CREATE INDEX ix_lineage_color_label ON lineage(color_label) WHERE color_label IS NOT NULL;
CREATE INDEX ix_lineage_flag        ON lineage(flag)        WHERE flag = 1;
CREATE INDEX ix_lineage_to_delete   ON lineage(to_delete)   WHERE to_delete = 1;
-- spec/159 §6+ — "find the preferred version of this source" is the
-- hot lookup; partial index keeps the table walk tiny.
CREATE INDEX ix_lineage_preferred   ON lineage(source_item_id) WHERE is_preferred = 1;

-- ===== bucket (D) — durable soft-state ONLY ================================
-- bucket_key is FK-less BY DESIGN: it is a content-stable recomputed id, and
-- soft-state must survive a membership-preserving cache recompute (spec/30 §5).
CREATE TABLE bucket (
  bucket_key      TEXT NOT NULL,                  -- content-stable {day|kind|content_key}
  phase           TEXT NOT NULL CHECK (phase IN ('pick','edit')),
  default_state   TEXT NOT NULL DEFAULT 'skipped' CHECK (default_state IN ('skipped','picked')),
  reviewed        INTEGER NOT NULL DEFAULT 0 CHECK (reviewed IN (0,1)),
  browsed         INTEGER NOT NULL DEFAULT 0 CHECK (browsed IN (0,1)),
  nudge_dismissed INTEGER NOT NULL DEFAULT 0 CHECK (nudge_dismissed IN (0,1)),
  current_index   INTEGER NOT NULL DEFAULT 0 CHECK (current_index >= 0),
  PRIMARY KEY (bucket_key, phase)
);
CREATE INDEX ix_bucket_phase_reviewed ON bucket(phase, reviewed);

-- ===== Derived cache layer (C) — excluded from backup; drop+rebuild safe ====
CREATE TABLE bucket_cache (
  bucket_key       TEXT NOT NULL,
  phase            TEXT NOT NULL CHECK (phase IN ('pick','edit')),
  day_number       INTEGER REFERENCES trip_day(day_number) ON DELETE CASCADE,  -- NULL = undated (real FK)
  kind             TEXT NOT NULL CHECK (kind IN
                       ('focus_bracket','exposure_bracket','burst','repeat',
                        'moment','individual','video','video_moment')),
  title            TEXT NOT NULL DEFAULT '',
  detection_source TEXT NOT NULL DEFAULT '',
  camera           TEXT NOT NULL DEFAULT '',
  ordinal          INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (bucket_key, phase)
);
CREATE INDEX ix_bucket_cache_day ON bucket_cache(phase, day_number);

CREATE TABLE bucket_member (
  bucket_key TEXT NOT NULL,
  phase      TEXT NOT NULL CHECK (phase IN ('pick','edit')),
  item_id    TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  ordinal    INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (bucket_key, phase, item_id),
  FOREIGN KEY (bucket_key, phase) REFERENCES bucket_cache(bucket_key, phase) ON DELETE CASCADE
);
CREATE INDEX ix_bucket_member ON bucket_member(phase, bucket_key);

CREATE TABLE clustering (
  phase       TEXT NOT NULL CHECK (phase IN ('pick','edit')),
  day_number  INTEGER REFERENCES trip_day(day_number) ON DELETE CASCADE,   -- NULL = undated
  fingerprint TEXT NOT NULL,
  computed_at TEXT NOT NULL,
  PRIMARY KEY (phase, day_number)
);

-- ===== day_resume (D) — Day Grid cell cursor (spec/32 §8.5) ================
-- Per-(phase, day) "last viewed cell" for the new flat Day Grid. Replaces the
-- per-bucket ``bucket.current_index`` resume for the layer above clusters:
-- with no bucket layer in the user-facing nav, the cursor sits on the day's
-- cell sequence directly. Cluster-internal cursors keep using ``bucket``.
CREATE TABLE day_resume (
  phase       TEXT NOT NULL CHECK (phase IN ('pick','edit')),
  day_number  INTEGER REFERENCES trip_day(day_number) ON DELETE CASCADE,   -- NULL = undated
  cell_index  INTEGER NOT NULL DEFAULT 0 CHECK (cell_index >= 0),
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (phase, day_number)
);

-- ===== camera_day_tz (D) — per-(camera, day) declared TZ; spec/45 ===========
-- The TZ the camera was set to on a given day, used by the bake step to
-- compute the discrete EXIF offset (target_tz − declared_tz). ``source``
-- tracks provenance so we can audit phone-auto-filled rows vs user-declared
-- vs legacy pair-picker fallback. Foreign keys cascade-delete: dropping a
-- camera or a day drops its TZ rows automatically.
CREATE TABLE camera_day_tz (
  camera_id           TEXT NOT NULL REFERENCES camera(camera_id) ON DELETE CASCADE,
  day_number          INTEGER NOT NULL REFERENCES trip_day(day_number) ON DELETE CASCADE,
  declared_tz_minutes INTEGER NOT NULL,
  source              TEXT NOT NULL CHECK (source IN ('phone_auto','user_declared','pair_picker')),
  declared_at         TEXT NOT NULL,
  PRIMARY KEY (camera_id, day_number)
);
CREATE INDEX ix_camera_day_tz_day ON camera_day_tz(day_number);

-- ===== item_visit (D) — Day Grid visited tick (spec/32 §2.10, §8.6) =========
-- Per-(item, phase) "I drilled in here" bit, sibling of bucket.browsed for the
-- non-cluster cells. Absence of a row = not visited (correct default).  FK to
-- item cascades deletes; writes go through gateway.set_item_visited which uses
-- ON CONFLICT DO UPDATE so an upsert does NOT cascade-delete child rows.
CREATE TABLE item_visit (
  item_id     TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  phase       TEXT NOT NULL CHECK (phase IN ('pick','edit')),
  visited     INTEGER NOT NULL DEFAULT 0 CHECK (visited IN (0,1)),
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (item_id, phase)
);
CREATE INDEX ix_item_visit_phase_visited ON item_visit(phase, visited);

-- ===== recipe (D) — spec/93 §5 BOUND recipes (spec/94 Phase 1) =============
-- A definition whose operand closure pins exactly one event's Cut (or a DC
-- already bound to that event) lives HERE rather than in the user's recipe
-- library (spec/93 §3). The shape mirrors the library-level ``recipe`` in
-- mira.db (spec/90 §5.1) MINUS the ``UNIQUE (flavour, name)`` — bound recipes
-- only need to be unique within ONE event, and bound recipes are necessarily
-- Cut-flavoured (a Collection is cross-event by intent and so can never be
-- bound).
--
-- Empty on every fresh event; populated only when the user saves a Recipe
-- whose composition references a single-event Cut from THIS event. If a later
-- edit removes that operand the classifier flips it to GLOBAL and the row
-- migrates out to ``<library_root>/Recipes/<name>.json`` atomically (spec/93
-- §5 last paragraph).
CREATE TABLE recipe (
  id                TEXT PRIMARY KEY,
  name              TEXT NOT NULL,
  composition_json  TEXT NOT NULL CHECK (json_valid(composition_json)),
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  extras_json       TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json)),
  UNIQUE (name)
);
"""

# Names of the derived/cache tables — the backup layer (json_dump / repo) excludes
# these from the JSON document; they are droppable + rebuildable from a re-scan.
CACHE_TABLES: tuple[str, ...] = ("bucket_cache", "bucket_member", "clustering")

# --------------------------------------------------------------------------- #
# Connection setup
# --------------------------------------------------------------------------- #


def connect(path: Union[str, Path]) -> sqlite3.Connection:
    """Open a connection with the spec/30 §3 pragmas applied.

    WAL journal, foreign-key enforcement, ``NORMAL`` sync. Rows come back as
    :class:`sqlite3.Row` (key access). ``path`` may be ``":memory:"``.
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    # Autocommit: we drive BEGIN/COMMIT explicitly (repo.transaction). Without
    # this the module opens an implicit transaction before DML, which then
    # collides with our explicit BEGIN.
    conn.isolation_level = None
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    # Wait up to 5s for a competing writer instead of raising "database is
    # locked" immediately — a momentary overlap between the foreground
    # gateway and the async ingest/export worker on the same event.db should
    # queue rather than crash (2026-06-17 corruption/lock incident).
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
    """Return the whole ``schema_info`` row (version + app/event id + created_at), or ``None``."""
    if not _schema_info_exists(conn):
        return None
    return conn.execute("SELECT * FROM schema_info WHERE id = 1").fetchone()


# --------------------------------------------------------------------------- #
# Migrations — restarted at v1 after the spec/56 RESET (2026-06-10, the second
# greenfield reset; the first was Slice 0, 2026-06-06). Nelson deleted every
# event for the marker-partition schema change, so the v1→v4 chain (spec/54
# Look columns + lineage snapshots, the 'repeat' bucket kind, the spec/56
# video tables/retirements) was folded into the initial DDL above and the
# history discarded. Pre-release rule stands: new fields fold into the DDL and
# dev events are recreated, not migrated.
# Once real events exist: existing event.db files are migrated in place on
# open — NEVER require recreation. SQLite ALTER TABLE ADD COLUMN does not
# support CHECK constraints; those are in the DDL for new databases only, and
# application code enforces valid values on migrated ones.
#
# SHIP-TIME RESET (Nelson 2026-06-12): when Mira ships, the whole
# development migration chain folds into the base DDL ONE last time and the
# counter restarts at v1 — the mark between building and maintaining. No
# resets before then, none after (released users only ever migrate forward).
# --------------------------------------------------------------------------- #


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """spec/58 slice 1 — persist the classifier's confidence score (the
    Edit Style button's red→green ramp reads it). Additive only; existing
    rows read NULL = never auto-scored."""
    conn.execute(
        "ALTER TABLE item ADD COLUMN classification_confidence REAL")


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """spec/61 (Share event Cuts) — Cut membership is FILE-based, not
    item-based. ``photo_tag`` (the spec/51 item-membership plan) drops —
    it was never written by any user flow, so no data moves. ``cut`` +
    ``cut_member`` arrive; membership rows reference ``lineage`` (the
    exported finals)."""
    conn.execute("DROP TABLE IF EXISTS photo_tag")
    conn.execute("""
CREATE TABLE cut (
  id                TEXT PRIMARY KEY,
  tag               TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK (tag <> ''),
  target_s          INTEGER CHECK (target_s IS NULL OR target_s > 0),
  max_s             INTEGER CHECK (max_s IS NULL OR max_s > 0),
  photo_s           REAL NOT NULL DEFAULT 6.0 CHECK (photo_s > 0),
  pool_expr_json    TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(pool_expr_json)),
  style_filter_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(style_filter_json)),
  type_filter       TEXT NOT NULL DEFAULT 'both' CHECK (type_filter IN ('both','photo','video')),
  default_state     TEXT NOT NULL DEFAULT 'skipped' CHECK (default_state IN ('picked','skipped')),
  music_category    TEXT,
  last_exported_at  TEXT,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
)""")
    conn.execute("""
CREATE TABLE cut_member (
  cut_id         TEXT NOT NULL REFERENCES cut(id) ON DELETE CASCADE,
  export_relpath TEXT NOT NULL REFERENCES lineage(export_relpath) ON DELETE CASCADE,
  added_at       TEXT NOT NULL,
  PRIMARY KEY (cut_id, export_relpath)
)""")
    conn.execute("CREATE INDEX ix_cut_member_file ON cut_member(export_relpath)")


def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    """spec/61 round 3 — the ``cut`` table gets the house ``extras_json``
    escape hatch (omitted at v3), first tenant: ``card_style`` (the
    separator/opener colour choice). Additive only."""
    conn.execute(
        "ALTER TABLE cut ADD COLUMN extras_json TEXT NOT NULL DEFAULT '{}'")


def _migrate_v4_to_v5(conn: sqlite3.Connection) -> None:
    """Nelson 2026-06-13 Look Strength slider — the new ``look_strength``
    column on ``adjustment``. 0..2 multiplier on the resolved Look
    Params; defaults to 1.0 so existing rows render IDENTICALLY to
    the pre-migration result. Additive only.

    SQLite can't add a CHECK constraint via ALTER TABLE — the new
    column ships without one; the validation lives at the gateway
    seam (clamped to [0, 2] on save). Fresh installs get the full
    CHECK in the DDL."""
    conn.execute(
        "ALTER TABLE adjustment ADD COLUMN look_strength REAL NOT NULL "
        "DEFAULT 1.0")


def _migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    """spec/64 (Nelson 2026-06-13) — events-information split. The
    Scope / Mood / Transport vocabulary retires; Context / Experience
    Type / Creative Focus replaces it.

    Existing events survive (spec/64 §6.2) — the new columns land NULL
    (and '[]' for the multi-select array) so Brazil 2023 opens with
    blanks for the new dimensions and Nelson fills them at his leisure
    via the EventHeaderDialog. Old Scope / Mood / Transport values do
    NOT map over — Nelson's explicit "drop clean, no leftovers" call.

    SQLite refuses ``ALTER TABLE DROP COLUMN`` while an index references
    the column, so the partial indexes on ``scope`` / ``mood`` come down
    first. Adds are real ``ADD COLUMN`` with the same SQLite limitation
    as v4→v5 — no CHECK can be added via ALTER, so the validation lives
    at the gateway seam on migrated rows. Fresh installs get the full
    CHECK on ``creative_focus`` in the DDL."""
    conn.execute("DROP INDEX IF EXISTS ix_event_scope")
    conn.execute("DROP INDEX IF EXISTS ix_event_mood")
    conn.execute("ALTER TABLE event DROP COLUMN scope")
    conn.execute("ALTER TABLE event DROP COLUMN mood")
    conn.execute("ALTER TABLE event DROP COLUMN transport")
    conn.execute("ALTER TABLE event ADD COLUMN context TEXT")
    conn.execute("ALTER TABLE event ADD COLUMN experience_type TEXT")
    conn.execute(
        "ALTER TABLE event ADD COLUMN creative_focus TEXT NOT NULL "
        "DEFAULT '[]'")
    conn.execute(
        "CREATE INDEX ix_event_context ON event(context) "
        "WHERE context IS NOT NULL")
    conn.execute(
        "CREATE INDEX ix_event_experience_type ON event(experience_type) "
        "WHERE experience_type IS NOT NULL")


def _migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
    """spec/81 (Nelson 2026-06-16) — the Dynamic Collection / Cut model.

    spec/80 modelled the live formula as a *mode on the Cut*
    (``pool_expr_json`` + a live/pinned split). spec/81 makes the
    formula a first-class noun — the **Dynamic Collection (DC)** — and
    makes a **Cut always frozen**. The schema follows:

    * ``dynamic_collection`` arrives — the live-query recipe (set algebra
      over operands + filters), resolved on demand, never a stored member
      set. Separate tag namespace from ``cut`` (operands are typed by
      ``kind``).
    * ``cut`` gains ``source_dc_id`` (the DC it was pinned from; ON DELETE
      SET NULL — the freeze invariant, the Cut survives a DC delete),
      ``expr_snapshot_json`` (the formula frozen at pin), the overlay
      attachment columns (``overlay_fields_json`` / ``overlay_mode``,
      spec/81 §3.1), and the ``separators`` flag (spec/61 §4, default ON).
    * The recipe + filter columns fold into the synthesized DC, then drop:
      each existing ``cut`` synthesizes a DC reusing the cut's OWN tag (no
      prefix — separate namespaces), translating ``pool_expr_json`` into
      ``expr_json`` (the bare token ``"exported"`` stays bare; a user tag
      becomes a typed ``{"kind":"cut",...}`` ref) and folding
      ``style_filter_json`` + ``type_filter`` into ``filters_json``. The
      cut's ``source_dc_id`` + ``expr_snapshot_json`` then point at it.
    * ``pool_expr_json`` / ``style_filter_json`` / ``type_filter`` drop.

    Additive ``ALTER TABLE ADD COLUMN`` can't carry a CHECK or a FK that
    SQLite back-validates existing rows against — the added ``source_dc_id``
    FK is enforced on NEW writes only (Nelson decision #6, accepted: no
    table rebuild). Fresh installs get the full DDL above. ``cut_member``
    is untouched (FILE-based, lineage-backed, cascade both ways)."""
    import json as _json

    # 1. The DC table.
    conn.execute("""
CREATE TABLE dynamic_collection (
  id           TEXT PRIMARY KEY,
  tag          TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK (tag <> ''),
  expr_json    TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(expr_json)),
  filters_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(filters_json)),
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  extras_json  TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json))
)""")

    # 2-6. The new cut columns (no CHECK via ALTER — validated at the seam).
    conn.execute("ALTER TABLE cut ADD COLUMN source_dc_id TEXT "
                 "REFERENCES dynamic_collection(id) ON DELETE SET NULL")
    conn.execute("ALTER TABLE cut ADD COLUMN expr_snapshot_json TEXT "
                 "NOT NULL DEFAULT '[]'")
    conn.execute("ALTER TABLE cut ADD COLUMN separators INTEGER "
                 "NOT NULL DEFAULT 1")
    conn.execute("ALTER TABLE cut ADD COLUMN overlay_fields_json TEXT "
                 "NOT NULL DEFAULT '[]'")
    conn.execute("ALTER TABLE cut ADD COLUMN overlay_mode TEXT")

    # 7. Backfill: synthesize one DC per existing cut, reusing the cut's
    #    own tag (separate namespace -> no collision). Translate the recipe;
    #    fold the two filter columns into filters_json.
    rows = conn.execute(
        "SELECT id, tag, pool_expr_json, style_filter_json, type_filter, "
        "created_at, updated_at FROM cut").fetchall()
    for r in rows:
        try:
            old_expr = _json.loads(r["pool_expr_json"] or "[]")
        except (ValueError, TypeError):
            old_expr = []
        new_expr = []
        for pair in old_expr:
            try:
                op, tag = pair[0], pair[1]
            except (IndexError, TypeError):
                continue
            if tag == "exported":
                new_expr.append([op, "exported"])
            else:
                new_expr.append([op, {"kind": "cut", "id": None, "tag": tag}])
        try:
            styles = _json.loads(r["style_filter_json"] or "[]")
        except (ValueError, TypeError):
            styles = []
        filters = {"styles": styles, "media_type": r["type_filter"] or "both"}
        dc_id = _new_uuid_for_migration()
        conn.execute(
            "INSERT INTO dynamic_collection "
            "(id, tag, expr_json, filters_json, created_at, updated_at, extras_json) "
            "VALUES (?, ?, ?, ?, ?, ?, '{}')",
            (dc_id, r["tag"], _json.dumps(new_expr), _json.dumps(filters),
             r["created_at"], r["updated_at"]))
        conn.execute(
            "UPDATE cut SET source_dc_id = ?, expr_snapshot_json = ? WHERE id = ?",
            (dc_id, _json.dumps(new_expr), r["id"]))

    # 8. Drop the folded-in columns.
    conn.execute("ALTER TABLE cut DROP COLUMN pool_expr_json")
    conn.execute("ALTER TABLE cut DROP COLUMN style_filter_json")
    conn.execute("ALTER TABLE cut DROP COLUMN type_filter")


def _new_uuid_for_migration() -> str:
    """A fresh id for migration-synthesized rows. Kept module-local so the
    migration is self-contained (the gateway's injected ``new_id`` is a
    runtime concern; a migration runs once at open with no gateway)."""
    import uuid as _uuid
    return _uuid.uuid4().hex


def _migrate_v7_to_v8(conn: sqlite3.Connection) -> None:
    """spec/81 Phase 2 (Nelson 2026-06-16) — the cross-event surface needs
    to make ``cut`` + ``cut_member`` cross-event-capable.

    Two reshapes, both required by the cross-event model:

    * ``cut.source_dc_id`` — Phase 1's FK to ``dynamic_collection(id)`` ON
      DELETE SET NULL is dropped. A cross-event Cut's source DC lives in
      ``mira.db`` (``saved_filter``), not event.db; an FK can't span stores.
      The id stays as opaque TEXT, and a new ``source_dc_kind`` column
      ('event' / 'user') discriminates which store to look in. The freeze
      invariant (spec/81 §5) moves to the gateway: deleting a DC explicitly
      NULLs the ids in any Cut that referenced it.
    * ``cut_member.export_relpath`` — Phase 1's FK to ``lineage(export_relpath)``
      ON DELETE CASCADE is dropped for the same reason: a cross-event Cut's
      members can reference exports in OTHER events' lineage tables. The new
      ``event_id`` column is nullable: NULL = legacy event-scope (the member
      is from THIS event's lineage by convention), non-NULL = the source
      event's UUID for cross-event lookup. The ``cut_id`` FK stays — deleting
      a Cut still cascades its membership.

    Implementation: SQLite can't DROP FK via ALTER, so the standard
    table-rebuild dance applies. ``PRAGMA defer_foreign_keys = 1`` defers the
    enforcement to COMMIT, so the intermediate ``DROP TABLE`` + ``RENAME``
    states don't fail the kept FKs (``cut_member.cut_id`` references the new
    ``cut`` after rename). All data preserves verbatim — ``source_dc_kind``
    + ``event_id`` land NULL on existing rows (legacy semantics).
    """
    # Defer FK checks to COMMIT — the rebuild needs intermediate states where
    # cut_member.cut_id references a dropped-then-renamed cut table.
    conn.execute("PRAGMA defer_foreign_keys = 1")

    # ---- Rebuild ``cut`` (drop source_dc_id FK, add source_dc_kind) ------ #
    conn.execute("""
CREATE TABLE cut_v8 (
  id                  TEXT PRIMARY KEY,
  tag                 TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK (tag <> ''),
  source_dc_id        TEXT,
  source_dc_kind      TEXT CHECK (source_dc_kind IN ('event','user') OR source_dc_kind IS NULL),
  expr_snapshot_json  TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(expr_snapshot_json)),
  target_s            INTEGER CHECK (target_s IS NULL OR target_s > 0),
  max_s               INTEGER CHECK (max_s IS NULL OR max_s > 0),
  photo_s             REAL NOT NULL DEFAULT 6.0 CHECK (photo_s > 0),
  default_state       TEXT NOT NULL DEFAULT 'skipped' CHECK (default_state IN ('picked','skipped')),
  music_category      TEXT,
  separators          INTEGER NOT NULL DEFAULT 1 CHECK (separators IN (0,1)),
  overlay_fields_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(overlay_fields_json)),
  overlay_mode        TEXT CHECK (overlay_mode IN ('embedded','burn_in') OR overlay_mode IS NULL),
  last_exported_at    TEXT,
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL,
  extras_json         TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json))
)""")
    # Copy every existing column; source_dc_kind lands NULL (legacy = unset,
    # treated as 'event' by gateway readers for back-compat).
    conn.execute("""
INSERT INTO cut_v8 (
  id, tag, source_dc_id, source_dc_kind, expr_snapshot_json,
  target_s, max_s, photo_s, default_state, music_category, separators,
  overlay_fields_json, overlay_mode, last_exported_at,
  created_at, updated_at, extras_json
) SELECT
  id, tag, source_dc_id, NULL, expr_snapshot_json,
  target_s, max_s, photo_s, default_state, music_category, separators,
  overlay_fields_json, overlay_mode, last_exported_at,
  created_at, updated_at, extras_json
FROM cut""")
    conn.execute("DROP TABLE cut")
    conn.execute("ALTER TABLE cut_v8 RENAME TO cut")

    # ---- Rebuild ``cut_member`` (drop export_relpath FK, add event_id) --- #
    conn.execute("""
CREATE TABLE cut_member_v8 (
  cut_id         TEXT NOT NULL REFERENCES cut(id) ON DELETE CASCADE,
  export_relpath TEXT NOT NULL,
  event_id       TEXT,
  added_at       TEXT NOT NULL,
  PRIMARY KEY (cut_id, export_relpath)
)""")
    conn.execute("""
INSERT INTO cut_member_v8 (cut_id, export_relpath, event_id, added_at)
SELECT cut_id, export_relpath, NULL, added_at FROM cut_member""")
    conn.execute("DROP TABLE cut_member")
    conn.execute("ALTER TABLE cut_member_v8 RENAME TO cut_member")

    # Recreate the indexes (DROP TABLE took the old ones with it).
    conn.execute("CREATE INDEX ix_cut_member_file ON cut_member(export_relpath)")
    conn.execute("CREATE INDEX ix_cut_member_event ON cut_member(event_id) "
                 "WHERE event_id IS NOT NULL")


def _migrate_v8_to_v9(conn: sqlite3.Connection) -> None:
    """spec/81 Phase 2 Item 6 — grab-originals (spec/61 §6 + §8).

    Cross-event Cuts may want items that are still ``#collected`` / ``#picked``
    / ``#edited`` — un-exported, no lineage row to point at. The export
    pipeline grabs the ORIGINAL bytes from the source event's
    ``Original Media/<...>`` instead. This migration extends ``cut_member`` to
    discriminate the two member kinds:

    * ``kind = 'export'`` — the legacy v8 shape: ``export_relpath`` set,
      ``origin_relpath`` NULL, the export pipeline links from the source
      event's ``Exported Media/``.
    * ``kind = 'grab'`` — ``export_relpath`` NULL, ``origin_relpath`` set
      (the source event's ``Original Media/<...>``), the export pipeline
      copies/links from there.

    PK changes from ``(cut_id, export_relpath)`` to ``(cut_id, member_id)``
    where ``member_id`` is the content-stable path (export_relpath for exports
    OR origin_relpath for grabs). Same item can't appear twice in one Cut as
    both an export AND a grab.

    Standard table rebuild — ``PRAGMA defer_foreign_keys = 1`` carries FK
    enforcement to COMMIT so the dropped/rebuilt cut_member doesn't break
    the kept ``cut_id`` cascade. Existing rows preserve verbatim: every row
    is ``kind='export'``, ``member_id = export_relpath`` (unique within a
    cut by the old PK)."""
    conn.execute("PRAGMA defer_foreign_keys = 1")
    conn.execute("""
CREATE TABLE cut_member_v9 (
  cut_id         TEXT NOT NULL REFERENCES cut(id) ON DELETE CASCADE,
  member_id      TEXT NOT NULL,
  kind           TEXT NOT NULL DEFAULT 'export' CHECK (kind IN ('export','grab')),
  export_relpath TEXT,
  origin_relpath TEXT,
  event_id       TEXT,
  added_at       TEXT NOT NULL,
  PRIMARY KEY (cut_id, member_id),
  CHECK ( (kind = 'export' AND export_relpath IS NOT NULL AND origin_relpath IS NULL)
       OR (kind = 'grab'   AND export_relpath IS NULL     AND origin_relpath IS NOT NULL) )
)""")
    conn.execute("""
INSERT INTO cut_member_v9 (
  cut_id, member_id, kind, export_relpath, origin_relpath, event_id, added_at
) SELECT
  cut_id, export_relpath, 'export', export_relpath, NULL, event_id, added_at
FROM cut_member""")
    conn.execute("DROP TABLE cut_member")
    conn.execute("ALTER TABLE cut_member_v9 RENAME TO cut_member")

    for sql in (
        "CREATE INDEX ix_cut_member_file   ON cut_member(export_relpath) "
        "WHERE export_relpath IS NOT NULL",
        "CREATE INDEX ix_cut_member_origin ON cut_member(origin_relpath) "
        "WHERE origin_relpath IS NOT NULL",
        "CREATE INDEX ix_cut_member_event  ON cut_member(event_id)       "
        "WHERE event_id IS NOT NULL",
        "CREATE INDEX ix_cut_member_kind   ON cut_member(kind)",
    ):
        conn.execute(sql)


def _migrate_v9_to_v10(conn: sqlite3.Connection) -> None:
    """spec/72 §1 / spec/89 Model B — the unambiguous lineage signal
    for origin (Mira-rendered vs third-party return).

    Adds ``lineage.provenance`` with default ``'mira_render'``. Every
    existing row backfills to 'mira_render' — pre-Model-B installs
    only wrote Mira-rendered exports via the spec/60 batch engine;
    third-party returns lived as ``Edited Media/`` candidate rows and
    have no ``Exported Media/`` lineage row to backfill.

    SQLite can't add a CHECK constraint via ALTER TABLE — the
    validation lives at the gateway seam on migrated rows. Fresh
    installs get the full CHECK in the DDL."""
    conn.execute(
        "ALTER TABLE lineage ADD COLUMN provenance TEXT NOT NULL "
        "DEFAULT 'mira_render'")


def _migrate_v10_to_v11(conn: sqlite3.Connection) -> None:
    """spec/89 Slice 5 / Block 1 D2.B — per-version intent for the
    versions cluster.

    Adds ``lineage.intent_state``. Existing rows backfill to
    ``'picked'`` (the legacy 1-row-per-item world treated every
    shipped row as a keeper); freshly-scanned rows in a 2+ -version
    cluster will be born ``'compare'`` by the gateway mutator so the
    cluster cover reads Compare orange until the user resolves it.

    SQLite can't add a CHECK constraint via ALTER TABLE — validation
    lives at the gateway seam on migrated rows. Fresh installs get
    the full CHECK in the DDL."""
    conn.execute(
        "ALTER TABLE lineage ADD COLUMN intent_state TEXT NOT NULL "
        "DEFAULT 'picked'")


def _migrate_v11_to_v12(conn: sqlite3.Connection) -> None:
    """spec/90 §5.2 Phase 1 — the ``face`` table arrives as the per-item
    substrate for face recognition.

    Empty in v1 of spec/90 (the detection pipeline is a separate sprint —
    spec/90 §7 Phase 6); the table exists so Person chips in a Recipe
    resolve to "no matches" leniently before recognition ships. New
    table, no ALTER, full CHECK + index complement intact."""
    conn.execute("""
CREATE TABLE face (
  id           TEXT PRIMARY KEY,
  item_id      TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  person_id    TEXT,
  bbox_x       REAL CHECK (bbox_x IS NULL OR (bbox_x >= 0 AND bbox_x <= 1)),
  bbox_y       REAL CHECK (bbox_y IS NULL OR (bbox_y >= 0 AND bbox_y <= 1)),
  bbox_w       REAL CHECK (bbox_w IS NULL OR (bbox_w >  0 AND bbox_w <= 1)),
  bbox_h       REAL CHECK (bbox_h IS NULL OR (bbox_h >  0 AND bbox_h <= 1)),
  confidence   REAL,
  detected_at  TEXT NOT NULL
)""")
    conn.execute("CREATE INDEX ix_face_item   ON face(item_id)")
    conn.execute(
        "CREATE INDEX ix_face_person ON face(person_id) "
        "WHERE person_id IS NOT NULL")


def _migrate_v12_to_v13(conn: sqlite3.Connection) -> None:
    """spec/94 Phase 1 — bound recipes live in event.db (spec/93 §3).

    A definition whose operand closure pins exactly one event's Cut
    lives in THAT event's ``event.db`` rather than in the library-level
    ``Recipes/`` JSON tree. Mirrors the mira.db ``recipe`` shape
    (spec/90 §5.1) minus the cross-flavour uniqueness — bound recipes
    are necessarily Cut-flavoured. Empty after migration; populated only
    when the classifier (spec/93 §5) routes a save here."""
    conn.execute("""
CREATE TABLE recipe (
  id                TEXT PRIMARY KEY,
  name              TEXT NOT NULL,
  composition_json  TEXT NOT NULL CHECK (json_valid(composition_json)),
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  extras_json       TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json)),
  UNIQUE (name)
)""")


def _migrate_v13_to_v14(conn: sqlite3.Connection) -> None:
    """spec/109 §5 — in-app exposure-bracket merge needs a way to
    distinguish a Mira-fused master from an external stacker's output.

    Adds ``stack_bracket.producer`` with default ``'external'`` so
    every pre-spec/109 row reads as an external stacker output (the
    only way they could have been adopted). Freshly-written rows take
    the explicit producer the caller passes; the in-app merge job
    sets it to ``'mira'``.

    SQLite can't add a CHECK constraint via ALTER TABLE — validation
    lives at the gateway seam on migrated rows. Fresh installs get
    the full CHECK in the DDL."""
    conn.execute(
        "ALTER TABLE stack_bracket ADD COLUMN producer TEXT NOT NULL "
        "DEFAULT 'external'")


def _migrate_v14_to_v15(conn: sqlite3.Connection) -> None:
    """spec/111 — a Cut carries the slideshow canvas aspect (the show
    canvas shape belongs to the Cut, not the event). Adds
    ``cut.aspect`` with default ``'16:9'`` so every pre-spec/111 row
    renders separator / opener cards at the same aspect they did under
    the legacy ``settings.separator_aspect`` knob (which also defaulted
    to 16:9). Fresh installs get the full CHECK in the DDL; ALTER
    can't add it post-hoc so migrated rows are validated at the
    gateway seam."""
    conn.execute(
        "ALTER TABLE cut ADD COLUMN aspect TEXT NOT NULL DEFAULT '16:9'")


def _migrate_v15_to_v16(conn: sqlite3.Connection) -> None:
    """spec/115 — independent Exposure slider. Adds
    ``adjustment.user_exposure``: a per-image USER exposure (EV) that is
    added to the resolved Look's exposure AFTER Strength scaling, so it
    nudges brightness independently of both. Defaults to 0.0 so every
    pre-spec/115 row renders identically.

    SQLite refuses CHECK via ALTER TABLE — fresh installs get the full
    ``CHECK (user_exposure BETWEEN -2 AND 2)`` in the DDL; migrated
    rows are clamped at the gateway seam on save."""
    conn.execute(
        "ALTER TABLE adjustment ADD COLUMN user_exposure REAL NOT NULL "
        "DEFAULT 0.0")


def _migrate_v16_to_v17(conn: sqlite3.Connection) -> None:
    """spec/123 — clock offset columns become integer SECONDS (lossless
    ×60 conversion) so source 3 (measured pair raw delta to the nearest
    second) doesn't lose precision through a minute-resolution column.

    Renames:
      camera.applied_offset_minutes → applied_offset_seconds
      camera.configured_tz_minutes  → configured_tz_seconds
      item.tz_offset_minutes        → tz_offset_seconds

    Values are multiplied by 60 in place. ``trip_day.tz_minutes`` and
    ``camera_day_tz.declared_tz_minutes`` stay in minutes (zones are
    whole minutes; converted to seconds at read where needed).

    Tolerant of test fixtures that build only a partial v16 schema —
    each table's columns are checked first so a fixture missing the
    ``camera`` or ``item`` table doesn't error here. Real event.db
    files always have both."""
    def _table_exists(name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        return row is not None

    def _column_exists(table: str, column: str) -> bool:
        if not _table_exists(table):
            return False
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        return column in cols

    if _column_exists("camera", "applied_offset_minutes"):
        conn.execute(
            "ALTER TABLE camera RENAME COLUMN applied_offset_minutes "
            "TO applied_offset_seconds")
        conn.execute(
            "UPDATE camera SET applied_offset_seconds = "
            "applied_offset_seconds * 60 "
            "WHERE applied_offset_seconds IS NOT NULL")
    if _column_exists("camera", "configured_tz_minutes"):
        conn.execute(
            "ALTER TABLE camera RENAME COLUMN configured_tz_minutes "
            "TO configured_tz_seconds")
        conn.execute(
            "UPDATE camera SET configured_tz_seconds = "
            "configured_tz_seconds * 60 "
            "WHERE configured_tz_seconds IS NOT NULL")
    if _column_exists("item", "tz_offset_minutes"):
        conn.execute(
            "ALTER TABLE item RENAME COLUMN tz_offset_minutes "
            "TO tz_offset_seconds")
        conn.execute(
            "UPDATE item SET tz_offset_seconds = tz_offset_seconds * 60")


def _migrate_v17_to_v18(conn: sqlite3.Connection) -> None:
    """spec/127 — per-(camera, trip-TZ-segment) correction store.

    Adds ``camera_tz_correction(camera_id, trip_tz_seconds,
    configured_tz_seconds, nudge_seconds, applied_offset_seconds,
    applied_at)`` PK (camera_id, trip_tz_seconds) + populates it from
    the existing single per-camera ``applied_offset_seconds`` /
    ``configured_tz_seconds`` columns, keyed by the event's predominant
    ``trip_day.tz_minutes`` (×60). Cameras whose ``applied_offset_seconds``
    is NULL or 0 get no row — the dialog reads "Correct" for them.

    Tolerant of partial fixtures (some test stores don't build
    ``trip_day``/``camera`` at all); each table is probed first so a
    missing one doesn't error here. Real event.db files always have both.
    """
    def _table_exists(name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        return row is not None

    conn.execute(
        """
        CREATE TABLE camera_tz_correction (
          camera_id              TEXT NOT NULL REFERENCES camera(camera_id) ON DELETE CASCADE,
          trip_tz_seconds        INTEGER NOT NULL,
          configured_tz_seconds  INTEGER,
          nudge_seconds          INTEGER NOT NULL DEFAULT 0,
          applied_offset_seconds INTEGER NOT NULL DEFAULT 0,
          applied_at             TEXT,
          PRIMARY KEY (camera_id, trip_tz_seconds)
        )
        """
    )
    conn.execute(
        "CREATE INDEX ix_camera_tz_correction_tz "
        "ON camera_tz_correction(trip_tz_seconds)"
    )

    if not (_table_exists("camera") and _table_exists("trip_day")):
        return

    # Predominant trip TZ for the event = the most common non-NULL
    # ``trip_day.tz_minutes`` value. Ties broken by smallest tz_minutes
    # (deterministic across reruns; a real tie is improbable). Multi-TZ
    # trips migrate every existing camera row into the predominant
    # segment; the user fixes the second segment via the unified dialog.
    tz_rows = conn.execute(
        "SELECT tz_minutes FROM trip_day WHERE tz_minutes IS NOT NULL"
    ).fetchall()
    if not tz_rows:
        return
    from collections import Counter
    counter: Counter = Counter(int(r[0]) for r in tz_rows)
    # max() ties: pick the smallest tz for determinism.
    most_common = sorted(
        counter.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    trip_tz_seconds = int(most_common) * 60

    cam_rows = conn.execute(
        "SELECT camera_id, configured_tz_seconds, applied_offset_seconds, "
        "applied_at FROM camera WHERE applied_offset_seconds IS NOT NULL "
        "AND applied_offset_seconds <> 0"
    ).fetchall()
    for cam_id, cfg, applied, stamp in cam_rows:
        conn.execute(
            "INSERT OR IGNORE INTO camera_tz_correction "
            "(camera_id, trip_tz_seconds, configured_tz_seconds, "
            " nudge_seconds, applied_offset_seconds, applied_at) "
            "VALUES (?, ?, ?, 0, ?, ?)",
            (cam_id, trip_tz_seconds, cfg, int(applied), stamp),
        )


def _migrate_v19_to_v20(conn: sqlite3.Connection) -> None:
    """spec/152 §3 — per-Cut crossfade transition_ms.

    The Phase 1 fix gave the global setting
    (``Settings.default_transition_ms``) a single read site at the
    callers; users wanted per-Cut overrides next to ``photo_s`` in
    the New / Adjust dialog. Add a nullable ``transition_ms`` column
    so an unset Cut keeps falling back to the global default at read
    time and only Cuts the user explicitly tuned carry a non-NULL
    value. Additive only — every existing row stays NULL and
    behaves identically to its pre-152 self when the global default
    matches the legacy ``DEFAULT_TRANSITION_MS = 2000``.
    """
    conn.execute(
        "ALTER TABLE cut ADD COLUMN transition_ms INTEGER "
        "CHECK (transition_ms IS NULL OR transition_ms >= 0)"
    )


def _migrate_v21_to_v22(conn: sqlite3.Connection) -> None:
    """spec/155 — per-day and per-event map images.

    Adds a nullable ``map_image_path`` column to both ``trip_day`` (the
    per-day slot) and ``event`` (the one event-level slot). Both store
    the path relative to ``event_root`` (e.g. ``Maps/day-02.jpg``) so the
    event folder stays portable (spec/82). NULL = no map attached, which
    is the legacy state for every existing row.

    The Cut day-separator pipeline (spec/61 §4) reads ``trip_day`` and
    renders the letterboxed-map form when the column is set, falling
    back to the v1 text card when NULL. The event-level entry drives
    the Cut intro slide in the same way.

    Each ALTER is guarded by a table-existence check: hand-crafted
    legacy fixtures (e.g. the v4 seed in
    ``tests/test_look_strength_foundation.py``) skip ``trip_day``, and a
    missing table on this step would otherwise crash forward migration.
    Same pattern as ``_migrate_v20_to_v21``'s ``video_adjustment``
    guard.
    """
    def _has(name: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,)).fetchone() is not None

    if _has("trip_day"):
        conn.execute("ALTER TABLE trip_day ADD COLUMN map_image_path TEXT")
    if _has("event"):
        conn.execute("ALTER TABLE event ADD COLUMN map_image_path TEXT")


def _migrate_v22_to_v23(conn: sqlite3.Connection) -> None:
    """spec/159 — per-version ratings + delete flag on lineage.

    Adds four columns on ``lineage`` that drive the Exported Collection
    review surface:

    * ``stars``       — 1..5 or NULL (unrated). LRC-style star rating.
    * ``color_label`` — 'red'|'yellow'|'green'|'blue'|'purple' or NULL.
    * ``flag``        — 0/1 portfolio keep flag.
    * ``to_delete``   — 0/1; marked for batch deletion via the closed-
      event Cut page's "⌫ Delete N marked…" toolbar action. The unlink
      does NOT happen on the column flip — only at confirm time.

    Plus four partial indexes for the filter dropdown ("min stars",
    colour multi-select, flagged-only, hide-marked-for-deletion) +
    the toolbar count query.

    Existing rows default to NULL/0 = unrated, unflagged, not-marked,
    so the migration is purely additive — no data backfill needed.
    The ``lineage`` table is guaranteed by every event.db created
    after v13 (the spec/54 versions-as-exports migration), so no
    presence guard is needed here.
    """
    conn.execute(
        "ALTER TABLE lineage ADD COLUMN stars INTEGER "
        "CHECK (stars IS NULL OR (stars BETWEEN 1 AND 5))"
    )
    conn.execute(
        "ALTER TABLE lineage ADD COLUMN color_label TEXT "
        "CHECK (color_label IS NULL OR color_label IN "
        "('red','yellow','green','blue','purple'))"
    )
    conn.execute(
        "ALTER TABLE lineage ADD COLUMN flag INTEGER NOT NULL DEFAULT 0 "
        "CHECK (flag IN (0,1))"
    )
    conn.execute(
        "ALTER TABLE lineage ADD COLUMN to_delete INTEGER NOT NULL DEFAULT 0 "
        "CHECK (to_delete IN (0,1))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_lineage_stars "
        "ON lineage(stars) WHERE stars IS NOT NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_lineage_color_label "
        "ON lineage(color_label) WHERE color_label IS NOT NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_lineage_flag "
        "ON lineage(flag) WHERE flag = 1"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_lineage_to_delete "
        "ON lineage(to_delete) WHERE to_delete = 1"
    )


def _migrate_v24_to_v25(conn: sqlite3.Connection) -> None:
    """spec/159 §6+ Nelson eyeball pivot — virtual-Mira preferred flag
    on ``item``.

    Adds the boolean ``preferred_virtual_mira`` column on ``item``. In
    practice many clusters consist of an LRC return + the virtual Mira
    intent (no Mira render file on disk yet); the user still wants to
    pick which is preferred. ``lineage.is_preferred`` covers real
    rows; this column covers the virtual case. The gateway's
    ``set_lineage_preferred`` clears this column for the same source
    when it writes a real preferred row; ``set_item_preferred_virtual_mira``
    clears all ``lineage.is_preferred`` siblings for that source.
    """
    conn.execute(
        "ALTER TABLE item ADD COLUMN preferred_virtual_mira INTEGER NOT NULL "
        "DEFAULT 0 CHECK (preferred_virtual_mira IN (0,1))"
    )


def _migrate_v23_to_v24(conn: sqlite3.Connection) -> None:
    """spec/159 §6+ — preferred-version flag on ``lineage``.

    Adds the boolean ``is_preferred`` column + a partial index keyed
    on ``source_item_id``. The gateway enforces the at-most-one-per-
    source uniqueness rule by clearing siblings inside one
    transaction; the schema only enforces 0/1 + the index.

    Existing rows default to 0 (not preferred). The Exported
    Collection's downstream consumers (Cuts compose) fall back to a
    deterministic "most-recent ``mira_render`` first" pick when no
    preferred is set, so legacy events keep working without backfill.
    """
    conn.execute(
        "ALTER TABLE lineage ADD COLUMN is_preferred INTEGER NOT NULL "
        "DEFAULT 0 CHECK (is_preferred IN (0,1))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_lineage_preferred "
        "ON lineage(source_item_id) WHERE is_preferred = 1"
    )


def _migrate_v20_to_v21(conn: sqlite3.Connection) -> None:
    """spec/156 — per-image creative-filter STRENGTH.

    Adds ``filter_strength`` to both ``adjustment`` (photos) and
    ``video_adjustment`` (segments): the −2..+2 graduation the Edit
    filter group exposes. Existing rows default to 0.0 (medium ≈ 70 %),
    so a re-export of an already-filtered photo dials the effect back a
    touch from the previous full-strength bake — the deliberate
    spec/156 behaviour (filters read a little strong at full)."""
    conn.execute(
        "ALTER TABLE adjustment ADD COLUMN filter_strength REAL NOT NULL "
        "DEFAULT 0.0 CHECK (filter_strength >= -2 AND filter_strength <= 2)"
    )
    # ``video_adjustment`` lives only in the base DDL (no migration creates
    # it), so a genuinely old event.db that predates it may not have the
    # table. Guard the ALTER — a DB without the table simply has no
    # segments to carry the column; a fresh install gets it from the DDL.
    has_video_adjustment = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' "
        "AND name = 'video_adjustment'").fetchone()
    if has_video_adjustment:
        conn.execute(
            "ALTER TABLE video_adjustment ADD COLUMN filter_strength REAL "
            "NOT NULL DEFAULT 0.0 "
            "CHECK (filter_strength >= -2 AND filter_strength <= 2)"
        )


def _migrate_v18_to_v19(conn: sqlite3.Connection) -> None:
    """spec/144 — record the clip-segment's TRUE on-disk duration on the
    lineage row.

    Before this column existed, every consumer of a clip member's
    duration (Cut budget, cut-play scrubber, PTE timing) read the
    SOURCE video's duration_ms — i.e. the whole video, not the
    marker-partition segment. The budget undercounted (a clip-heavy
    show read 25 min for a 1 h+ render), cut-play's scrubber drew
    each clip at the source length (the player still advanced on
    EndOfMedia, so the playhead lagged), and PTE wrote ``Duration=0``
    (the spec/140 name-match always missed).

    The migration adds a nullable ``duration_ms`` column to lineage.
    Legacy rows stay NULL — readers fall back to ffprobing the
    on-disk file (`probe_video`); new clip-render writes populate
    the column directly with ``(out_ms - in_ms) / speed``.
    """
    conn.execute("ALTER TABLE lineage ADD COLUMN duration_ms INTEGER")


MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [
    _migrate_v1_to_v2,
    _migrate_v2_to_v3,
    _migrate_v3_to_v4,
    _migrate_v4_to_v5,
    _migrate_v5_to_v6,
    _migrate_v6_to_v7,
    _migrate_v7_to_v8,
    _migrate_v8_to_v9,
    _migrate_v9_to_v10,
    _migrate_v10_to_v11,
    _migrate_v11_to_v12,
    _migrate_v12_to_v13,
    _migrate_v13_to_v14,
    _migrate_v14_to_v15,
    _migrate_v15_to_v16,
    _migrate_v16_to_v17,
    _migrate_v17_to_v18,
    _migrate_v18_to_v19,
    _migrate_v19_to_v20,
    _migrate_v20_to_v21,
    _migrate_v21_to_v22,
    _migrate_v22_to_v23,
    _migrate_v23_to_v24,
    _migrate_v24_to_v25,
]


def initialize(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    app_version: str = "",
    created_at: Optional[str] = None,
) -> None:
    """Create a fresh schema at :data:`SCHEMA_VERSION` and stamp ``schema_info``.

    Must be called on an empty database. Idempotency is *not* assumed — callers
    use :func:`get_version` to decide between initialize and :func:`migrate`.
    """
    if _schema_info_exists(conn):
        raise RuntimeError("initialize() called on an already-initialised database")
    created_at = created_at or _utc_now_iso()
    conn.executescript(DDL)
    conn.execute(
        "INSERT INTO schema_info (id, schema_version, app_version, event_id, created_at) "
        "VALUES (1, ?, ?, ?, ?)",
        (SCHEMA_VERSION, app_version, event_id, created_at),
    )
    log.info("initialised event.db schema v%s for event %s", SCHEMA_VERSION, event_id)


def migrate(conn: sqlite3.Connection) -> None:
    """Apply any pending migrations to reach :data:`SCHEMA_VERSION`.

    Raises if the DB is uninitialised or newer than this code understands.
    """
    current = get_version(conn)
    if current is None:
        raise RuntimeError("migrate() called on an uninitialised database")
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"event.db is schema v{current} but this build only understands "
            f"v{SCHEMA_VERSION}; upgrade Mira to open it"
        )
    while current < SCHEMA_VERSION:
        step = MIGRATIONS[current - 1]
        conn.execute("BEGIN")
        try:
            step(conn)
            conn.execute(
                "UPDATE schema_info SET schema_version = ? WHERE id = 1", (current + 1,)
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        log.info("migrated event.db schema v%s -> v%s", current, current + 1)
        current += 1
