# spec/53 — User-level data store (`mira.db`)

**Status:** Vision (approved 2026-06-08, Nelson). Captures the unified user-level
state architecture. Supersedes the per-file state model (`settings.rebuild.json`
+ `events_index.json`) on first launch via a one-shot import.

This document is durable. It governs every decision about where user-level
(non-per-event) state lives.

---

## 0. Why

User-level state is fragmented today across loose JSON files:

| Today | What it holds |
|---|---|
| `%LOCALAPPDATA%\Mira\settings.rebuild.json` | App preferences (theme, language, photos_base_path, the wizard answers folded in) |
| `%LOCALAPPDATA%\Mira\events_index.json` | The registry of events (uuid + relative path + cached display fields) |

Multiple new concerns need a home outside the per-event `event.db`:

- **Cuts** (definitions + user-saved templates) — cross-event by nature
  (spec/51 §3.3); cannot live in any single `event.db`.
- **People catalog** (one reference photo per person — spec/51 §3.13 + spec/52
  §9 / spec/30 `photo_person`).
- **User hardware registry** (cameras owned, lenses, phone identifiers — useful
  cross-event context).
- **Feature flags / installation profile** — the "lego assembly" that lets one
  codebase serve XMC vs MC vs profile-driven streamlining of unified MC.

The right answer isn't another loose JSON file per concern — it's **one
relational store with ACID semantics, schema migrations, and corruption
protection**.

---

## 1. The decision

**One SQLite file at user level: `%LOCALAPPDATA%\Mira\mira.db`.**

This file replaces both `settings.rebuild.json` and `events_index.json` (one-shot
import on first launch) and holds everything user-level we've discussed or will
need.

### 1.1 Why SQLite, not more JSON

- **ACID.** Settings + cuts + flags + people changes can be transactional when
  they need to be (backup restore, schema migration). JSON has no atomic
  multi-file write.
- **Corruption resistance.** SQLite WAL + atomic commit beats JSON's "rewrite the
  whole file and pray". `protect.py`'s atomic-write-then-rename pattern is the
  current JSON safeguard; SQLite does that natively + with finer granularity.
- **Schema discipline.** `SCHEMA_VERSION` + migrations list — same pattern as
  `event.db` — gives versioned, audited schema evolution. JSON files mutate
  freely with no record of what changed.
- **Future fields.** Every table that might grow gets an `extras_json` column
  (the established escape-hatch convention from `event.db`). The "lego
  assembly" specifically can use JSON columns when still evolving.

### 1.2 Why one file, not several

- **Cross-concern transactions become possible.** E.g., "restore backup"
  atomically replaces settings + cuts + people in one txn.
- **One file to back up, one to protect, one to checksum, one
  `schema_version` to track.**
- **New concerns** (audio library index, export presets, share destinations…)
  join as new tables without spawning new files.
- The cost is conceptual: more tables in one file. Worth it.

### 1.3 Why SQLite, not a service-managed store

MC is offline-first, single-user, desktop. No external service should sit
between the app and its user-level state. SQLite is a single-file, zero-process,
zero-network store that matches that model.

---

## 2. Tables

### 2.1 Singleton + housekeeping

```sql
-- schema_info (C) — typed singleton; mirrors event.db pattern
CREATE TABLE schema_info (
  id              INTEGER PRIMARY KEY CHECK (id = 1),
  schema_version  INTEGER NOT NULL,
  app_version     TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

-- installation_profile (D) — which feature-set this install runs
CREATE TABLE installation_profile (
  id          INTEGER PRIMARY KEY CHECK (id = 1),     -- singleton, enforced
  name        TEXT NOT NULL,                          -- 'XMC' | 'MC' | 'custom'
  created_at  TEXT NOT NULL,
  extras_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json))
);
```

### 2.2 Preferences and wizard

```sql
-- setting (D) — flat key-value store; value is JSON so any shape fits
CREATE TABLE setting (
  key        TEXT PRIMARY KEY,
  value_json TEXT NOT NULL CHECK (json_valid(value_json)),
  updated_at TEXT NOT NULL
);

-- wizard_answer (D) — wizard responses
CREATE TABLE wizard_answer (
  question_id  TEXT PRIMARY KEY,
  answer_json  TEXT NOT NULL CHECK (json_valid(answer_json)),
  answered_at  TEXT NOT NULL
);
```

The wizard previously folded its answers into settings.rebuild.json. Separating
them into a dedicated table lets the wizard read/write its own concern cleanly
(no naming collisions with regular settings, no risk of one corrupting the
other).

### 2.3 Events index (replaces `events_index.json`)

```sql
-- event_index (D) — registry of all events known to this install
CREATE TABLE event_index (
  event_uuid          TEXT PRIMARY KEY,
  relpath_to_base     TEXT NOT NULL,        -- relative to setting 'photos_base_path'
  abs_path            TEXT,                  -- cross-volume fallback only; normally NULL
  name_cached         TEXT NOT NULL DEFAULT '',
  type_cached         TEXT,
  country_cached      TEXT,                  -- ISO 3166-1 alpha-2; derived from per-day data
  start_date_cached   TEXT,
  end_date_cached     TEXT,
  is_closed_cached    INTEGER NOT NULL DEFAULT 0 CHECK (is_closed_cached IN (0,1)),
  last_opened_at      TEXT,
  extras_json         TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json))
);
CREATE INDEX ix_event_index_last_opened ON event_index(last_opened_at);
CREATE INDEX ix_event_index_country     ON event_index(country_cached);
```

Cached fields are **projections** of per-event data — refreshed when an event is
closed (or whenever per-day data changes, per spec/52). The load-bearing field
is `relpath_to_base` per [`feedback_relative_paths_from_user_default`] — the
single absolute anchor is the `photos_base_path` setting.

### 2.4 Cuts and templates

> **✔ REVISED 2026-06-12 (spec/61 slice 10, user-store schema v2).** The DDL
> below documents the ORIGINAL spec/51-era shape and is retained as record.
> The live shape (see `mira/user_store/schema.py`): the user-level
> `cut` table is **gone** — event Cuts live in `event.db` (`cut` +
> `cut_member` → `lineage`, file-based membership per spec/61 §1.4); only
> **`cut_template`** remains here, reshaped to the RECIPE (pool_expr_json /
> style_filter_json / type_filter / default_state / target_s / max_s /
> photo_s / music_category). No pre-shipped templates (spec/61 §10 #4) —
> `core/cut_templates.py` was never created. v1→v2 migration in place.

```sql
-- cut (D) — all Cut definitions (per-event AND cross-event); spec/51
CREATE TABLE cut (
  id                       TEXT PRIMARY KEY,            -- internal tag id; used as photo_tag.tag in event.db
  name                     TEXT NOT NULL,
  template_id              TEXT,                         -- which template it was derived from (NULL = scratch)
  target_s                 INTEGER,
  max_s                    INTEGER,
  slide_dur_s              REAL,
  videos_allowed           INTEGER NOT NULL DEFAULT 1 CHECK (videos_allowed IN (0,1)),
  seed_tag                 TEXT,                         -- other cut.id used as seed filter (subtractive walk)
  genre_filter             TEXT,                         -- item.classification value
  people_filter_json       TEXT NOT NULL DEFAULT '[]'    -- list of person.id values
                              CHECK (json_valid(people_filter_json)),
  scope_kind               TEXT NOT NULL CHECK (scope_kind IN ('single','multi')),
  scope_event_uuids_json   TEXT NOT NULL
                              CHECK (json_valid(scope_event_uuids_json)),  -- list of event_uuid
  last_walked_at           TEXT,
  last_exported_at         TEXT,
  created_at               TEXT NOT NULL,
  updated_at               TEXT NOT NULL,
  extras_json              TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json))
);
CREATE INDEX ix_cut_scope_kind ON cut(scope_kind);
CREATE INDEX ix_cut_updated    ON cut(updated_at);

-- cut_template (D) — user-saved templates (pre-shipped templates are app constants)
CREATE TABLE cut_template (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  target_s        INTEGER,
  max_s           INTEGER,
  slide_dur_s     REAL,
  videos_allowed  INTEGER NOT NULL DEFAULT 1 CHECK (videos_allowed IN (0,1)),
  created_at      TEXT NOT NULL,
  extras_json     TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json))
);
```

**Pre-shipped templates** (`#all-time-best`, `#short`, `#long`) are **Python
constants** in `core/cut_templates.py`, not rows. Stable across versions, owned
by the app code, can't be accidentally deleted by the user. When the user
creates a Cut from a pre-shipped template, the resulting `cut` row gets a new
UUID for `id` and references the template's stable key in `template_id`.

**Cut membership** lives in `event.db.photo_tag(item_id, tag)` per spec/30 /
spec/52. `photo_tag.tag = cut.id`. Cross-event Cuts gather members by querying
each event.db in scope.

### 2.5 People catalog

```sql
-- person (D) — the user-level catalog of people
CREATE TABLE person (
  id                       TEXT PRIMARY KEY,
  display_name             TEXT NOT NULL,
  reference_photo_relpath  TEXT,                         -- relative to %LOCALAPPDATA%\Mira\people\
  embedding_json           TEXT,                          -- face-rec embedding cached (simplest tier; spec/51 §3.13)
  created_at               TEXT NOT NULL,
  updated_at               TEXT NOT NULL,
  extras_json              TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json))
);
CREATE INDEX ix_person_display_name ON person(display_name);
```

The reference photo bytes live at `%LOCALAPPDATA%\Mira\people\<id>.{jpg,png}`
(a sibling folder to `mira.db`). Per-photo links in event.db
`photo_person` reference `person.id` here.

Simplest tier per spec/51 §3.13: one reference photo per person, embedding
computed and cached at upload; face-matching runs at filter time only.

### 2.6 User hardware registry

```sql
-- user_camera (D) — cameras the user owns
CREATE TABLE user_camera (
  camera_id     TEXT PRIMARY KEY,        -- 'Make+Model' business key; matches event.db.camera.camera_id
  make          TEXT NOT NULL,
  model         TEXT NOT NULL,
  is_phone      INTEGER NOT NULL DEFAULT 0 CHECK (is_phone IN (0,1)),
  owned_since   TEXT,
  created_at    TEXT NOT NULL,
  extras_json   TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json))
);
```

Cross-references `event.db.camera.camera_id` via the same business key. The
catalog is populated automatically as cameras are discovered during ingest;
the user can edit display labels or add hardware-owned-but-not-yet-used
manually.

### 2.7 Feature flags and the "lego assembly"

```sql
-- feature_flag (D) — runtime feature gating
CREATE TABLE feature_flag (
  key      TEXT PRIMARY KEY,
  enabled  INTEGER NOT NULL CHECK (enabled IN (0,1)),
  source   TEXT NOT NULL CHECK (source IN ('default','install_profile','user')),
  set_at   TEXT NOT NULL
);
```

**Flag keys are app-code constants.** New flags ship in code, never invented at
runtime. The DDL doesn't enumerate them (open-ended), but the application
defines the set in `core/feature_flags.py` (the source of truth for "what
flags exist"). Unknown keys at read time are treated as `default`.

### 2.7.1 The pre-defined v1 flag set

These flag keys exist in `core/feature_flags.py` from v1 and gate the
"Premium-vs-Basic" feature axis (XMC = full / MC = streamlined). For each, the
XMC installation_profile sets `enabled=1`; the future MC profile sets
`enabled=0` or omits the row (defaulting to disabled — see below for the
default-policy decision).

**Implementation discipline (Nelson 2026-06-08):** when a surface listed below
is built or revised, the FIRST thing built is the flag gate — read at startup,
checked at construct time, branch hides menu entries / tiles / dialog fields
when off. No "implement now, gate later".

| Flag key | What it gates | XMC default | MC default |
|---|---|---|---|
| `feature.cross_event_cuts` | Top-level "Cuts" menu's cross-event entries + the New Cut dialog's scope picker | on | off |
| `feature.tz_correction` | The conditional TZ-calibration ask + the pair-pick surface (spec/52 §8.2-8.3). Note: the underlying day-boundary derivation from corrected-time-at-read still runs; what this gates is the user-driven calibration FLOW | on | off |
| `feature.quick_sweep` | The Quick Sweep on-card pre-filter surface inside Collect | on | off |
| `feature.video_clips_snapshots` | Clip + snapshot creation on video items (the cull-time materialization workflow) | on | off |
| `feature.third_party_roundtrip` | LRC / Helicon export + reingest hooks | on | off |
| `feature.audio_export` | Mood-picker on Cut export + the audio matching algorithm (spec/51 §3.11 + closed in §6) | on | off |
| `feature.maps` | The per-event Maps authoring surface + items with `provenance='authored'` of map shape (spec/51 §3.12 + closed in §6 F) | on | off |
| `feature.collages` | Same as Maps but for collages | on | off |
| `feature.people_tagging` | The People catalog management page + the people filter on the New Cut dialog (spec/51 §3.13 + closed in §6 G) | on | off |
| `feature.bracket_detection` | Ingest-time focus/exposure bracket + burst cluster detection (runs in `core/bracket_detector.py`). When off, items land as individuals — no cluster grouping in the Day Grid | on | off |
| `feature.bracket_stacking` | The downstream merge of detected brackets (focus stack, exposure HDR merge). Independent of detection — you can detect without merging; you can't merge without detecting | on | off |
| `feature.wizard_custom_rules` | The wizard's classification-rule-customization screens. Wizard still runs in MC for the bits it captures regardless (home TZ, photos folder, basic preferences); the rule-tuning surfaces hide | on | off |
| `feature.advanced_pick_overlays` | Pro photo overlays during Pick: focus peaking, AF point overlay, per-photo sharpness scores. The photo + the Pick/Skip decision stay; the diagnostic overlays hide | on | off |
| `feature.plan_save_load_csv` | The `;`-CSV save/load buttons on the plan dialog (spec/52 §5.5). Power-user escape hatch for offline editing; not exposed in MC | on | off |
| `feature.advanced_edit_controls` | The full slider-driven Edit phase (exposure / contrast / clarity / per-style AUTO variants / detailed crop+rotate+aspect controls). When OFF, MC gets a simplified Edit experience — design TBD (separate redesign session pending; see PROGRESS) | on | off |
| `feature.event_lifecycle_close` | The "Close event" affordance + closed-event chrome (badges, the Closed Curate-access path). When off, events just exist; no formal closed state | on | off |
| `feature.detailed_event_types` | The full `event_type` enum (`trip`/`session`/`occasion`/`project`/`unclassified`) + `event_subtype` + per-type extras_json keys. When off, MC collapses to a simpler shape — exact reduction TBD when MC profile is designed | on | off |

**Default-when-no-row policy:** unknown / missing flag rows read as `default`,
and the code-side `core/feature_flags.py` carries the **per-profile default**
for each key. So an MC install that has no `feature_flag` rows for these keys
still gets the correct "off" behaviour via the code-side default; XMC gets
"on" the same way. Explicit user toggles (rare; restart-required per §3.3)
write a row with `source='user'` that overrides the profile default.

**`source` semantics:**

| source | Meaning |
|---|---|
| `default` | Coded default applied because no override exists |
| `install_profile` | Set by `installation_profile.name` at install time (e.g. XMC turns on the full feature set, MC turns on the streamlined subset) |
| `user` | User toggled it explicitly (rare; spec/53 §3.3 — restart to take effect) |

The "lego assembly" is the combination of:

- `installation_profile.name` — names the bundle (`'XMC'`, `'MC'`, `'custom'`).
- `core/feature_flags.py` — code-side: maps profile name → default flag values
  for the bundle.
- `feature_flag` table — runtime authoritative state; defaults applied if a row
  doesn't exist, profile-driven values written on first launch, user overrides
  (if any) layered on top.

---

## 3. Behaviour

### 3.1 Protection

| Mechanism | What |
|---|---|
| **WAL journal mode** | `PRAGMA journal_mode = WAL` — survives partial writes |
| **Foreign keys + CHECK constraints** | `PRAGMA foreign_keys = ON` — catches integrity bugs |
| **`PRAGMA integrity_check` on open** | Detect corruption before any read; log + warn if non-`ok`; backup is loaded |
| **SHA-256 sidecar** | `mira.db.sha256` recomputed after every WAL checkpoint / clean close; verified on open; mismatch warns the user (not auto-restored — tamper is rare for a personal tool, but visible) |
| **Rolling backups** | On every clean close (or daily, whichever is sooner), `mira.db` is copied to `mira.db.bak.<N>` for `N ∈ 1..5` (oldest rotated out). If the live DB fails `integrity_check` on open, the most recent good backup is offered as the restore point |

This is *corruption*-resistant + *accidental-edit*-resistant. It is NOT
crypto-level tamper-proof. Personal-use single-user tool; the real threat model
is disk corruption, app crash mid-write, and the user opening the file in a
text editor "to take a quick look".

### 3.2 Schema migrations

Same pattern as `event.db`:

- `SCHEMA_VERSION` is a module constant in the schema definition (e.g.
  `core/user_store/schema.py::SCHEMA_VERSION`).
- `MIGRATIONS: list[Callable[[sqlite3.Connection], None]]` is the ordered list.
  Index N applies version `N+1` (i.e. migrates from N to N+1).
- On open, if recorded `schema_version` < code's `SCHEMA_VERSION`, apply
  pending migrations in order, each in its own transaction, stamping
  `schema_info` as it goes. If recorded > code's, refuse to open ("upgrade
  Mira").
- v1 is greenfield: the initial DDL above is the v1 shape; `MIGRATIONS` starts
  empty.

### 3.3 Feature flag lifecycle

Flags are **read at startup** and applied per surface as it constructs. Changes
require a restart to take effect.

Why: simpler than per-access checking, faster (constant-folded after startup),
predictable (no flag-flipping-mid-session weirdness). Matches Nelson's stated
preference: flags are defined at installation or rare license-class events —
they don't need to flip during normal use.

Implementation shape (for the build phase, not part of this design):

- `core/feature_flags.py` exposes a frozen `Flags` object at module import
  (after first read).
- Every UI surface that's flag-gated reads `Flags.x` at its construct site;
  the value is constant within the process lifetime.
- A "Reload flags" path exists for explicit user toggling (Settings dialog → a
  flag toggle → "Restart to apply"), but it's a restart prompt, not a hot
  reload.

### 3.4 Backups and restore

The rolling backups (§3.1) are local. They cover crash-recovery scenarios. They
are NOT a substitute for user-controlled backups (which the user does
themselves to their own cloud / external drive — out of scope for MC's job per
the offline-first invariant).

The legacy "restore from backup" path (the Events menu entry) currently restores
a per-event backup (event.db + media). With mira.db it ALSO needs to
restore user-level state if the user is doing a full machine migration. That
flow is a separate slice (covered by event-creation + backup work, not this
spec).

---

## 4. One-shot import on first launch

On the first launch where `mira.db` doesn't exist:

1. Create `mira.db` at v1 (apply DDL, stamp `schema_info`).
2. Apply the **installation profile** (read from a side channel — installer
   wrote it, or default to `'XMC'` for source-run dev). Populate
   `installation_profile` + the corresponding default `feature_flag` rows.
3. **Import legacy state, if present:**
   - `%LOCALAPPDATA%\Mira\settings.rebuild.json` → split into `setting`
     rows (top-level keys become rows; `value_json` carries the JSON-encoded
     value) and `wizard_answer` rows (the wizard sub-tree is the source for
     those).
   - `%LOCALAPPDATA%\Mira\events_index.json` → one `event_index` row per
     entry, with `relpath_to_base` preserved and cached fields copied.
4. **Retire the legacy files** by renaming them with a `.imported-<timestamp>`
   suffix. They stay on disk as a safety net for one or two app versions, then
   get deleted by a later cleanup pass.

After step 4, the app reads exclusively from `mira.db` for user-level
state. No two-source-of-truth period.

If legacy files don't exist (truly fresh install), step 3 is skipped and the
wizard fires per the usual first-run logic.

---

## 5. Cross-references

- [spec/30-relational-schema-redesign](30-relational-schema-redesign.md) — the
  per-event `event.db` schema. `photo_tag.tag` references `cut.id` here.
  `photo_person.person_id` references `person.id` here. `camera.camera_id`
  business key matches `user_camera.camera_id` here.
- [spec/51-share-cuts-vision](51-share-cuts-vision.md) — the Cuts design that
  drives the `cut`/`cut_template` tables.
- [spec/52-event-creation-vision](52-event-creation-vision.md) — the event-
  creation flow that uses `event_index` (replaces `events_index.json`) and
  reads home-TZ from `setting`/`wizard_answer` for the TZ-calibration trigger.
- [spec/04-settings](04-settings.md) — the settings model (table-of-contents
  for what keys appear in `setting`).

---

## 6. What this spec leaves open

- **The audio library index** (mentioned in spec/51 §3.11 — `audio_library_path`
  setting, plus a future cached index of available files + durations). When
  implemented, it lands as a new table here; no change to this spec.
- **Cross-installation sync** (the user moves to a new machine). Cited in
  passing for the rolling backups (§3.4) but not designed. Future work.
- **A "history" / audit table** for actions taken (e.g., "this Cut exported at
  timestamp T"). If the user wants such a log, it lands as a new table here
  without changing existing shape.
- **External-system bridges** (LRC, Helicon, etc.). If their per-user config
  needs persistent state, it lands in `setting` rows or a dedicated table.
