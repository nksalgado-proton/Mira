# spec/03 — Event schema (SQLite DDL + JSON backup shape)

**Build-sequence step 2.** Mechanically derives the `event.db` schema and its JSON
backup shape from the `spec/01` field catalog. This spec drives the first real
`mira/` code — `mira/store/schema.py` (DDL below, verbatim-ish) and
`mira/store/models.py` (typed dataclasses mirroring these tables). Paths are
**relative to event root**; capture time is **virtual** (raw never mutated). One
`event.db` per event (decision D2, confirmed).

> **⚠ Spec drift notice (2026-06-08):** the CREATE TABLE blocks below
> document the early shape and still mention legacy fields (`notes`,
> `google_album_*`, `whatsapp_message`, `participant` / `checklist_item` /
> `distribution_action` tables) that spec/52 + the 2026-06-08 schema lock
> retired or replaced. The **live source of truth** is
> `mira/store/schema.py`. Updates landed since this doc was last
> written:
>
> * `event` table — added structured qualifier columns: `duration_value`
>   INTEGER, `duration_unit` TEXT (CHECK enum), `scope` TEXT (CHECK enum),
>   `participants` TEXT NOT NULL DEFAULT '[]' (CHECK json_valid),
>   `mood` TEXT (CHECK enum), `transport` TEXT (CHECK enum). See
>   [spec/52 §14](52-event-creation-vision.md) for the full vocabulary
>   and UI shape. Indexes: `ix_event_scope`, `ix_event_mood`.
> * `event_subtype` — semantics changed to **activity-only** with
>   curated-but-editable presets per type (so users can still type
>   custom subtypes the presets don't cover).
> * `item` table — added `subject` TEXT (nullable, free-text). User-
>   provided annotation: bird species, plant name, landmark, etc. UI
>   surface deferred; applies to both photos and clips.
> * `event.tags_json`, `event.notes`, `event.google_album_*`,
>   `event.whatsapp_message`, `participant`, `participant_device`,
>   `checklist_item`, `distribution_action`, `share_tag`, `subset`,
>   `subset_member`, `share_map` tables — **retired**.
> * **spec/56 — the video workshop (2026-06-10):** the marker-partition
>   model replaced freeform clip spans. New tables `video_marker` (user
>   cut points per source video; start/end stay implicit),
>   `video_segment` (1:1 satellite for segment items — only `seg_index`;
>   geometry is DERIVED from marker order at read time via
>   `core/video_segments.py`), `video_snapshot` (1:1 point satellite;
>   creation auto-Picks). **Retired:** `clip_span` (in/out + label +
>   `is_full_span` — labels live on `item.subject`; whole-video export is
>   the original single segment, picked),
>   `video_adjustment.trim_start_delta_ms`/`trim_end_delta_ms` (markers
>   ARE the trim), and `'pick'` from the `item.materialized_phase` enum
>   (bytes never commit during deciding). The `video_moment` /
>   `video_override` blocks below (§3) are two generations stale —
>   first replaced by `clip_span`, then by markers.
> * **Version RESET to v1 (2026-06-10, later):** Nelson deleted every
>   event for the spec/56 change, so the v1→v4 migration chain (spec/54
>   Look columns + lineage snapshots, the 'repeat' bucket kind, the
>   spec/56 tables/retirements) was folded into the fresh-database DDL
>   and `SCHEMA_VERSION` restarted at **1** with an empty migration
>   list — the second greenfield reset (first: Slice 0, 2026-06-06).
>   Mentions of "schema v2/v3/v4" in specs name design generations,
>   not live migration targets.
> * **v3 — spec/61 Share event Cuts (2026-06-11):** `photo_tag` (the
>   spec/51 item-based membership plan) retired UNUSED — spec/61 locked
>   Cut membership as FILE-based. New tables `cut` (definitions: tag
>   slug unique per event, target/max seconds, photo seconds, pool
>   expression, style/type filters, default state, music category,
>   last_exported_at) and `cut_member` (cut_id + export_relpath →
>   `lineage` PK, both FKs cascade). The built-in #exported is a live
>   query over `lineage WHERE phase='edit'`, never a row.
>
> A future cleanup sprint will rewrite the DDL blocks below to match the
> live schema; until then, treat the live `schema.py` as authoritative.

## Decisions resolved here (my calls, per Nelson's "make your calls")

- **D1 — Event-doc home: THIN POINTER.** The full event lives in `event.db`. A small
  app-level index (`events_index.json` in user-data-dir, §4) holds one row per event
  (id, name, dates, is_closed, **`event_relpath` — the event_root relative to the
  `photos_base_path` setting**) so the events *list* renders without opening every DB.
  *(Amended 2026-05-30, charter §5.9: the row stores a relpath, not an absolute path.
  The single absolute anchor is the `photos_base_path` setting, not per-event roots —
  relocating the whole library is a one-setting edit. Absolute `event_root` is kept
  only as the cross-volume fallback, flagged.)*
- **D4 — Adjustment params: JSON BLOB** (`params_json`) until we must query by a param.
  *(SUPERSEDED 2026-06-10 by spec/54 — the tone payload is now the Look CHOICE in
  real columns: `style`, `look`, `look_intensity`, `vibrance`. Resolved params are
  recomputed deterministically at render/export; no tone blob remains. Folded into
  the base DDL by the 2026-06-10 v1 reset.)*
- **D5 — Bucket identity: one `bucket_key`** — a structural key (`camera/day/bucket`)
  for stable buckets, or a content-hash for transient Moment clusters (re-cluster
  stability). One column, documented semantics.
- **D6 — Drop `EventStatus`.** The UI doesn't read it; `is_closed` is the only lifecycle
  bit. Not in the schema.
- **D7 — Persist `nudge_dismissed`** as a small per-(bucket,phase) flag (removes the
  re-entry annoyance). Video scrub/playhead/markers stay transient (markers become
  `video_moment` rows on creation).
- **Phase progress / completion is a QUERY** over `phase_state`, never a stored cache.

---

## 1. Pragmas & meta

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous  = NORMAL;

CREATE TABLE meta (           -- schema_version, app_version, event_id, created_at
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);
```

`meta.schema_version` is owned by us; `mira/store/schema.py` carries the version
constant and an explicit, ordered migration list (we own migrations now — unlike the
legacy schema-less JSON).

## 2. Event-level tables

```sql
CREATE TABLE event (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  start_date  TEXT,                       -- ISO date
  end_date    TEXT,
  is_closed   INTEGER NOT NULL DEFAULT 0,
  notes       TEXT NOT NULL DEFAULT '',
  google_album_name TEXT NOT NULL DEFAULT '',
  google_album_link TEXT NOT NULL DEFAULT '',
  whatsapp_message  TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);   -- event_root is NOT stored: it is the DB's own location; all paths relative to it.

CREATE TABLE trip_day (
  day_number  INTEGER PRIMARY KEY,
  date        TEXT,                        -- ISO date
  description TEXT NOT NULL DEFAULT '',
  location    TEXT,
  tz_minutes  INTEGER                      -- nullable: inherits plan default
);

CREATE TABLE participant (
  id   TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  role TEXT,
  devices_json TEXT                        -- small list; blob is fine
);

CREATE TABLE checklist_item (
  id      TEXT PRIMARY KEY,
  label   TEXT NOT NULL,
  checked INTEGER NOT NULL DEFAULT 0,
  notes   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE camera (                      -- unifies legacy camera_clocks + camera_timezone_offsets + day overrides
  camera_id              TEXT PRIMARY KEY,
  is_reference           INTEGER NOT NULL DEFAULT 0,
  is_phone               INTEGER NOT NULL DEFAULT 0,
  configured_tz_minutes  INTEGER,          -- camera's declared tz
  applied_offset_minutes INTEGER,          -- correction currently applied
  applied_at             TEXT,
  calibration_json       TEXT              -- pairs[], rejected[], per-day overrides, reversible
);

CREATE TABLE distribution_action (
  id         TEXT PRIMARY KEY,
  timestamp  TEXT NOT NULL,
  channel    TEXT NOT NULL,
  item_count INTEGER NOT NULL DEFAULT 0,
  share_url  TEXT,
  notes      TEXT NOT NULL DEFAULT ''
);
```

## 3. The item spine and its satellites

```sql
CREATE TABLE item (
  id            TEXT PRIMARY KEY,
  kind          TEXT NOT NULL CHECK (kind IN ('photo','video')),
  origin_relpath TEXT NOT NULL UNIQUE,     -- relative to event root, under 00 - Captured
  sha256        TEXT NOT NULL,
  byte_size     INTEGER NOT NULL,
  camera_id     TEXT NOT NULL REFERENCES camera(camera_id),
  capture_time_raw       TEXT NOT NULL,    -- virtual EXIF: camera's recorded time, never mutated
  capture_time_corrected TEXT NOT NULL,    -- raw + applied offset; the app's sort key
  tz_offset_minutes      INTEGER NOT NULL DEFAULT 0,
  tz_source     TEXT NOT NULL DEFAULT 'none' CHECK (tz_source IN ('pair','tz','manual','none')),
  classification TEXT,                      -- scenario as DATA (null = unclassified)
  classification_source TEXT CHECK (classification_source IN ('auto','user') OR classification_source IS NULL),
  classification_rules_version TEXT,
  sharpness_score REAL,
  sharpness_metric TEXT,
  provenance    TEXT NOT NULL DEFAULT 'captured' CHECK (provenance IN ('captured','snapshot','clip')),
  parent_item_id TEXT REFERENCES item(id), -- snapshot/clip -> source video (N->1 lineage)
  day_number    INTEGER REFERENCES trip_day(day_number),
  quarantine_status TEXT NOT NULL DEFAULT 'ok' CHECK (quarantine_status IN ('ok','no_timestamp','recovered')),
  recovered_from_filename INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL
);
CREATE INDEX ix_item_day    ON item(day_number);
CREATE INDEX ix_item_camera ON item(camera_id);
CREATE INDEX ix_item_time   ON item(capture_time_corrected);
CREATE INDEX ix_item_parent ON item(parent_item_id);

CREATE TABLE phase_state (                 -- one model, all phases (replaces every per-phase journal)
  item_id   TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  phase     TEXT NOT NULL CHECK (phase IN ('cull','select','process','curate')),
  state     TEXT NOT NULL DEFAULT 'discarded' CHECK (state IN ('discarded','candidate','kept')),
  derived_dirty INTEGER NOT NULL DEFAULT 0,-- upstream change invalidated this (fixes re-entry S1/S2)
  decided_at  TEXT,
  committed_at TEXT,
  PRIMARY KEY (item_id, phase)
);
CREATE INDEX ix_phase_state ON phase_state(phase, state);
CREATE INDEX ix_phase_dirty ON phase_state(phase, derived_dirty);

CREATE TABLE bucket (                      -- per-bucket soft state (D5: structural key OR content-hash)
  bucket_key   TEXT NOT NULL,
  phase        TEXT NOT NULL CHECK (phase IN ('cull','select','process','curate')),
  default_state TEXT NOT NULL DEFAULT 'discarded' CHECK (default_state IN ('discarded','kept')),
  reviewed     INTEGER NOT NULL DEFAULT 0,
  browsed      INTEGER NOT NULL DEFAULT 0,
  current_index INTEGER NOT NULL DEFAULT 0,
  nudge_dismissed INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (bucket_key, phase)
);

CREATE TABLE video_moment (                -- clips & snapshots as first-class rows (never synthesised)
  source_item_id TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  id        TEXT NOT NULL,                 -- c1, s1 ... stable across re-trim; unique within source
  kind      TEXT NOT NULL CHECK (kind IN ('clip','snapshot')),
  in_ms     INTEGER, out_ms INTEGER,       -- clip range
  at_ms     INTEGER,                       -- snapshot position
  state     TEXT NOT NULL DEFAULT 'discarded' CHECK (state IN ('kept','discarded')),
  label     TEXT NOT NULL DEFAULT '',
  source_duration_ms INTEGER NOT NULL DEFAULT 0,
  produced_item_id TEXT REFERENCES item(id),-- materialised snapshot-photo / exported clip
  created_at TEXT NOT NULL,
  PRIMARY KEY (source_item_id, id)
);

CREATE TABLE adjustment (                  -- per-item Edit photo state
  item_id      TEXT PRIMARY KEY REFERENCES item(id) ON DELETE CASCADE,
  -- spec/54 tone payload = the Look CHOICE (recomputed to Params at
  -- render/export; `look` app-enforced against available_looks()):
  style        TEXT,                        -- NULL = item's classification
  look         TEXT NOT NULL DEFAULT 'natural',
  creative_filter TEXT,                     -- spec/54 §8 filter key; NULL = none
  crop_norm_json TEXT,                      -- [x,y,w,h] in [0,1]
  crop_angle   REAL NOT NULL DEFAULT 0,
  rotation     INTEGER NOT NULL DEFAULT 0 CHECK (rotation IN (0,90,180,270)),
  aspect_label TEXT,
  process_exported INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE video_override (              -- per-clip Process refinements
  source_item_id TEXT NOT NULL,
  moment_id   TEXT NOT NULL,
  params_json TEXT, crop_norm_json TEXT, box_angle REAL NOT NULL DEFAULT 0,
  aspect_ratio_label TEXT, auto_on INTEGER NOT NULL DEFAULT 1, style TEXT, rep_frame_ms INTEGER,
  include_audio INTEGER NOT NULL DEFAULT 1, rotation_degrees INTEGER NOT NULL DEFAULT 0,
  trim_start_delta_ms INTEGER NOT NULL DEFAULT 0, trim_end_delta_ms INTEGER NOT NULL DEFAULT 0,
  audio_volume REAL NOT NULL DEFAULT 1.0, audio_fade_ms INTEGER NOT NULL DEFAULT 0,
  speed REAL NOT NULL DEFAULT 1.0, stabilise REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (source_item_id, moment_id),
  FOREIGN KEY (source_item_id, moment_id) REFERENCES video_moment(source_item_id, id) ON DELETE CASCADE
);

CREATE TABLE stack_bracket (              -- focus/exposure brackets (id+kind were folder-name today)
  bracket_id   TEXT PRIMARY KEY,
  kind         TEXT NOT NULL CHECK (kind IN ('focus','exposure')),
  action       TEXT CHECK (action IN ('stacked','picked','skipped') OR action IS NULL),
  picked_index INTEGER NOT NULL DEFAULT -1,
  output_relpath TEXT,
  day_number   INTEGER REFERENCES trip_day(day_number)
);
CREATE TABLE stack_member (
  bracket_id TEXT NOT NULL REFERENCES stack_bracket(bracket_id) ON DELETE CASCADE,
  item_id    TEXT NOT NULL REFERENCES item(id),
  ordinal    INTEGER NOT NULL,
  PRIMARY KEY (bracket_id, item_id)
);
```

## 4. Curate, lineage

```sql
CREATE TABLE curate_tag (
  item_id TEXT PRIMARY KEY REFERENCES item(id) ON DELETE CASCADE,
  level   TEXT CHECK (level IN ('best','short','long','composition','collage_only') OR level IS NULL),
  theme   TEXT,                            -- portfolio genre tag
  solo    INTEGER NOT NULL DEFAULT 0,
  is_discarded INTEGER NOT NULL DEFAULT 0,
  tag_set_at TEXT
);

CREATE TABLE subset (
  id    TEXT PRIMARY KEY,
  name  TEXT NOT NULL,
  base  TEXT NOT NULL,                     -- 'short' | 'long' | <subset-id> (chaining; not FK-able)
  genre_filter TEXT,
  target_s INTEGER, max_s INTEGER
);
CREATE TABLE subset_member (
  subset_id TEXT NOT NULL REFERENCES subset(id) ON DELETE CASCADE,
  item_id   TEXT NOT NULL REFERENCES item(id),
  excluded  INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (subset_id, item_id)
);

CREATE TABLE trip_budget (                 -- single row
  id INTEGER PRIMARY KEY CHECK (id = 1),
  short_target_s INTEGER, short_max_s INTEGER,
  long_target_s  INTEGER, long_max_s  INTEGER,
  video_share REAL
);

CREATE TABLE lineage (                     -- export traceability (replaces stem-matching)
  export_relpath TEXT PRIMARY KEY,
  item_id    TEXT REFERENCES item(id),     -- null for N->1 bucket outputs
  bucket_key TEXT,                         -- for stacks/brackets (N->1)
  phase      TEXT NOT NULL CHECK (phase IN ('process','curate'))
);
```

`curate_map` (kind=MAP slides) is deferred — note only; add when the maps feature lands.

## 5. The app-level events index (D1, NOT in event.db)

`<user_data_dir>/events_index.json` — the thin pointer, under the §1 protection
contract, one entry per event so the events list renders without opening any `event.db`:

```json
{ "schema_version": 1,
  "photos_base_path": "D:/Photos/_mira",
  "events": [
    { "id": "...", "name": "Costa Rica 2026", "start_date": "2026-04-01",
      "end_date": "2026-04-14", "is_closed": false,
      "event_relpath": "2026 - Costa Rica",
      "event_root_abs": null } ] }
```

**The single absolute anchor is the `photos_base_path` setting** (charter §5.9), not
per-event roots. Each event stores `event_relpath` — its root relative to
`photos_base_path` — and resolves at load as `base + event_relpath`. `event_root_abs`
is the **cross-volume fallback only**: non-null (and used in preference) when the event
lives on a different drive than the base, where Windows has no relative path. Relocating
the whole library rewrites *one* value (`photos_base_path`); everything inside `event.db`
is relative to the resolved `event_root`. (`photos_base_path` is mirrored here for a
self-contained index, but the settings store owns it.)

## 6. JSON backup shape (the same model serialised)

`event.json` (the backup dump + migration intermediate + test fixture) is `event.db`
serialised: a top-level object with `schema_version`, `event`, `trip_days[]`,
`cameras[]`, `participants[]`, `checklist[]`, `distribution[]`, then `items[]` where
each item nests its `phase_state{cull,select,process,curate}`, `adjustment{}`,
`moments[]` (+ each moment's `override{}`), and `curate_tag{}`; plus top-level
`buckets[]`, `stacks[]` (bracket + members), `subsets[]` (+ members), `trip_budget{}`,
`lineage[]`. `mira/store/json_dump.py` reads this → store and writes store → this;
**restore and migration share the reader** (charter §4 steps 2–5). It carries its own
`schema_version` because it is the durable, human-readable backup format.

## 7. What this drives next (code)

- `mira/store/schema.py` — these DDL statements + `SCHEMA_VERSION` + ordered
  migrations + WAL/FK connection setup.
- `mira/store/models.py` — typed dataclasses, one per table, field-for-field.
- `mira/store/json_dump.py` — store ⇄ `event.json` (§6).
- then `mira/store/repo.py` — the EventStore repository (CRUD + transactions),
  the substrate-hiding API the gateway is built on.
