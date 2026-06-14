# 30 — Relational Schema Redesign (recommended)

Status: **APPROVED (Nelson 2026-05-31).** The recommended design below is accepted, including the
recommended answers to all six §8 open questions (resolved inline). This is the schema the
relational-core rebuild builds (schema-first: DDL → models → gateway → ported surfaces). It
amends `spec/03-schema.md`; the *why* lives in [`spec/31-relational-vision.md`](31-relational-vision.md).
Synthesis of three candidate
schemas + three adversarial critiques into one recommended design.

Greenfield: there are **no real events to migrate**, so this is "design the best schema," not
"smallest diff from v3."

> **⚠ Amendment — schema v4 (2026-06-10, [spec/56](56-video-workshop.md), the
> video workshop):** the clip model in this document is superseded where it
> contradicts spec/56. `clip_span` (freeform in/out + label + `is_full_span`)
> RETIRED in favour of the **marker-partition model**: `video_marker` rows are
> the user's cut points (start/end implicit, never stored); consecutive markers
> define segments that tile the timeline; a segment is still ONE `item`
> (provenance `'clip'`, child of its source — the one-node spine survives
> intact) with a 1:1 `video_segment` satellite carrying only its **order
> identity** (`seg_index`); geometry is DERIVED at read time
> (`core/video_segments.py`). Snapshots carry a 1:1 `video_snapshot` point
> satellite and auto-Pick at creation. Whole-video keep is the original single
> segment, picked (`is_full_span` retired with the special case);
> `clip_span.label` → `item.subject`; `video_adjustment` lost the trim deltas
> (markers ARE the trim) and now keys to segment items. Clips/snapshots are
> authored in the **Edit workshop**, not Pick, and materialise only at Export
> (`'pick'` left the `materialized_phase` enum). §3.9, the §4 concept-#3
> sample, and the §7 video ops are rewritten below; older prose mentioning
> `clip_span` elsewhere is historical record. The earlier spec/52 retirements
> (participants / checklist / distribution / share layer) likewise remain in
> the prose as record — `mira/store/schema.py` is the live source of
> truth.
>
> *(2026-06-10, later: with every dev event deleted, the v1→v4 migration
> chain was folded into the fresh-database DDL and `SCHEMA_VERSION`
> restarted at **1** — the second greenfield reset. "v4" in this document
> names the spec/56 design generation, not a live migration target.)*

---

## 1. Context & mandate

### Why we are re-deriving the schema

The current `event.db` (schema v3) was built using SQLite as a **JSON store with a relational
veneer**. The data-model audit found the recurring anti-patterns the user named explicitly:

- **JSON-blob columns carrying queryable structure** — `params_json`, `crop_norm_json`
  (×2: `adjustment` *and* `video_override`), `calibration_json`, `devices_json`. The crop
  rectangle is *split* — `crop_norm_json` (rect) beside first-class `crop_angle`/`rotation`/
  `aspect_label` columns — a half-normalised shape that is the worst of both worlds.
- **A key-value `meta(k, v)` table** holding four typed install-time scalars as stringly-typed
  rows.
- **Singleton-as-table** (`trip_budget` with `CHECK(id=1)`) and a **de-facto singleton not
  enforced** (`event` — the gateway issues `UPDATE event SET …` with **no WHERE clause** and
  trusts that exactly one row exists).
- **Tables joined only by naming convention, no FK** — the bucket triplet (`bucket`,
  `bucket_cache`, `bucket_member` share `(bucket_key, phase)` with no FK; membership delete is
  a hand-written correlated subquery because there is no cascade to lean on); `day_key` as free
  TEXT with a magic `'undated'` sentinel and no FK to `trip_day`; `subset.base` overloading one
  TEXT column to mean *either* a literal tier *or* a self-id with no discriminator and no FK;
  `lineage.bucket_key` pointing at a recomputed string that is no table's PK by construction.
- **A generic load-all-then-filter-in-Python access layer** — almost every aggregate gateway
  method (`items()`, `day_tree()`, `phase_day_progress()`, the navigator's `_captured_by_day`)
  calls `store.all(cls)` (a full-table hydration into dataclasses) and filters/groups in
  Python, so the indexes that *do* exist go unused on the hottest paths.
- **Two identities for one clip** — a clip/snapshot is a `video_moment` row (virtual, with its
  own *binary* `state` that diverges from the tri-state `phase_state`), and *also* an `item`
  row once Process materialises it, joined by nullable cross-FKs in both directions. The
  "videos are buckets of clips/snapshots" idea is expressed three different ways
  (`bucket_member` convention, `video_moment` rows, materialised `item` rows) — not unified.

### The mandate (the user's framing)

> Leverage the relational engine — real **FOREIGN KEYS, indexes, JOINs, CHECK/UNIQUE
> constraints, cascades** — so the **structure does the work** and the code is **faster + more
> reliable.** Fix this at the **schema level, not the query level.**

And the load-bearing design concepts (treated as hard requirements below):

1. **Videos are buckets of clips and snapshots** — one parent→child pattern underlies both a
   photo bucket and a source video.
2. **Clips/snapshots are virtual until Process** — DB rows, **zero bytes**, until Process writes
   bytes. Rows are not bytes.
3. **Full-video keep = a full-span clip** — Process iterates a uniform list of kept children
   with **no "whole video vs clips" special case anywhere.**
4. Leverage the relational engine (above).
5. Legacy UI surfaces are then **ported faithfully** onto this schema (the Supreme Rule:
   port, don't reinvent) — so the schema must serve the real access patterns of cull / select
   / process / curate / distribute.
6. Pipeline `Capture → Cull → Select → Process → Curate → Distribute`; per-item K/D per phase
   (`discarded`/`candidate`/`kept`); capture time virtual; all paths relative to event root;
   `event_root` never stored.

### Synthesis stance (what this recommendation grafts and rejects)

The three candidates and their critiques converged on a clear answer:

- **Adopt wholesale** (all three candidates agreed, critiques confirmed): the FK/discriminator/
  index/cascade fixes — enforced `event` singleton, `trip_budget` folded into `event`, typed
  `schema_info`, `day_key`→`day_number` FK, `subset.base`→discriminated self-FK, `lineage`→real
  FKs, the three missing hot-path indexes (`phase_state(item_id, phase)`,
  `item(provenance, day_number, capture_time_corrected)`, `bucket(phase, reviewed)`), blob
  columns relationalised where they carry **structure** (`devices_json`, `calibration_json`,
  crop rect), and the cache layer given a real composite FK + cascade.

- **Take the "unified spine" insight, reject its over-reach.** The `unified-item-spine`
  candidate's best idea is that a clip is **one node across its whole lifecycle**, not two
  identities. We keep that. But its critique was decisive: the `item` ⟂ `media_file` 1:1 split
  taxes the captured-item bulk (which is **never** virtual) with a mandatory join to express a
  distinction a single nullable column carries just as queryably — and its own stated rationale
  ("virtual = no media_file = pre-Process") is contradicted by snapshots materialising at Cull.
  **Rejected.** "Virtual" becomes a nullable file-identity on the one `item` row, guarded by a
  row-level CHECK.

- **Keep D4's tone-param blob.** Both `purist-normalized` and `conservative-normalize` tried to
  relationalise the adjustment-slider map; both critiques flagged it as the wrong bet —
  normalization cost on the **cold path** (no surface queries items by exposure value) for query
  power no ported surface uses, contradicting frozen decision D4. **Rejected.** `params_json`
  stays a blob, as D4 sanctions. The crop *rectangle* (genuinely structural, paired with
  promoted angle/rotation columns) **is** promoted to real `[0,1]`-checked columns.

- **Reject the `video_moment.state` removal as proposed by `conservative-normalize`.** Its
  critique proved the "state-unification win" actually *relocated* the two-records-can-disagree
  bug (source-video `phase_state` vs full-span-moment-item `phase_state`, nothing binding them).
  We solve this the clean way: a clip/snapshot **is** an `item` from creation (one node), so a
  clip's K/D is its **own** `phase_state` row — there is no second state record to disagree with.
  A kept raw video is itself a full-span clip child (see §4), so the source video carries no
  independent "kept because a clip is kept" state to desync.

- **Reject surrogate-INTEGER-keys-everywhere + reference-table-per-enum** (`purist-normalized`).
  Its critique was decisive on port ergonomics: ~35 tables, dual surrogate+business keys at
  every port site, a translation layer over the already-string-keyed gateway, and a heavier
  order-sensitive `json_dump` — rigor that over-serves a single-user, single-writer, offline,
  ~10k-item app whose atomic-write model already prevents most of the corruption it guards.
  **We keep TEXT/UUID business keys** (the gateway and `repo.py` reflection are written against
  them) and **enforce enums with per-column `CHECK` against shared, documented domains** — one
  documented domain constant reused, not 7 copies drifting, but no lookup-table ceremony.

The result: the `conservative-normalize` table shape (familiar to the port), upgraded with the
`unified-item-spine` one-node-per-clip insight done **without** the media_file split, plus every
FK/index/discriminator fix all three agreed on.

---

## 2. Conceptual ER model

Durable (**D**) = system-of-record, in the JSON backup. Derived/cache (**C**) = regenerable,
excluded from backup.

```
                                  event (D, singleton)
                                    │ 1:1  budget cols inline
        ┌───────────────┬───────────┼───────────────┬──────────────────┐
        │ 1:N           │ 1:N       │ 1:N           │ 1:N              │ 1:N
    trip_day (D)    camera (D)   participant(D)  checklist_item(D)  distribution_action(D)
        │               │  1:N        │ M:N (participant_device, D)
        │               │ camera_calibration_pair (D)   ╲___________╱
        │               │                                  (join table → camera)
        │ 1:N (day_number FK; NULL = undated)
        │               │ 1:N (camera_id FK)
        └──────┬────────┘
               ▼
            item (D)  ── the spine, one row per captured/derived unit
             │  kind ∈ {photo, video};  provenance ∈ {captured, snapshot, clip}
             │  file identity NULLABLE  ⇒  "virtual" = origin_relpath IS NULL
             │
             │ self-ref  parent_item_id  (N:1)  ── child clip/snapshot → source video
             │                                     == the videos-are-buckets edge
             │
             ├── 1:N  phase_state (D)        ── THE K/D spine, one row per (item × phase)
             ├── 1:N  video_marker (D, v4)   ── cut points on a SOURCE video (spec/56)
             ├── 1:1  video_segment (D, v4)  ── segment order-identity (provenance=clip)
             ├── 1:1  video_snapshot (D, v4) ── snapshot point        (provenance=snapshot)
             ├── 0..1 adjustment (D)         ── photo Process edits  (kind=photo)
             ├── 0..1 video_adjustment (D)   ── segment Process edits (kind=video, provenance=clip)
             ├── 0..1 curate_tag (D)
             ├── N:1  bucket   (D soft-state; membership is C)
             ├── M:N  stack_bracket (via stack_member, D)
             └── 0..1 lineage (D, by source_item_id)

   subset (D) ──self-FK base_subset_id (N:1, discriminated)── subset
        │ M:N (subset_member, D; stored = exclusions, inclusion resolved on demand)
        ▼
      item

   curate_map (D) ──N:1── trip_day

   ── DERIVED / CACHE (C; never in backup; dropped+rebuilt freely) ──
   bucket_cache (C) ──1:N (composite FK + cascade)── bucket_member (C) ──N:1── item
        │ N:1 day_number FK (NULL = undated)
   clustering (C) ── fingerprint per (phase, day_number)
   schema_info (C, typed singleton) ;  shared enum domains documented once
```

### Cardinality + durable/derived census

| Entity | Card. to parent | D/C | Note |
|---|---|---|---|
| event | singleton | D | budget cols inline (1:1 folded) |
| trip_day | event 1:N | D | `day_number` PK = the FK target everywhere |
| camera | event 1:N | D | calibration blob → `camera_calibration_pair` rows |
| camera_calibration_pair | camera 1:N | D | pair-picker provenance, real FKs to the items used |
| participant | event 1:N | D | v1-empty (kept structurally) |
| participant_device | M:N | D | replaces `devices_json` |
| checklist_item | event 1:N | D | |
| distribution_action | event 1:N | D | |
| **item** | event 1:N; self N:1 | D | the spine; file identity nullable (virtual) |
| **phase_state** | item 1:N | D | one per (item, phase); row-exists-iff-decided |
| **video_marker** (v4) | source video 1:N | D | the user's cut points; segments derive from their order (spec/56) |
| **video_segment** (v4) | item 1:1 | D | iff provenance = clip; ORDER identity only (seg_index) — geometry derived |
| **video_snapshot** (v4) | item 1:1 | D | iff provenance = snapshot; the at_ms point; auto-Picks at creation |
| adjustment | item 1:1 | D | photo + snapshot; crop promoted, tone = spec/54 Look choice |
| video_adjustment | item 1:1 | D | segment; shared crop cols + video-only fields (trims retired, v4) |
| stack_bracket | event 1:N | D | output is an item (`provenance='stack_output'`) |
| stack_member | bracket 1:N | D | M:N item↔bracket, ordered |
| curate_tag | item 1:1 | D | |
| subset | event 1:N; self N:1 | D | base discriminated self-FK |
| subset_member | subset 1:N | D | stored = exclusions; inclusion derived |
| curate_map | trip_day 1:N | D | the map-separator slides (was under-modelled) |
| lineage | — | D | real FK to item OR stack_bracket |
| bucket | event 1:N | D | **soft-state only**; content-stable key |
| bucket_cache | event 1:N | C | derived structure |
| bucket_member | bucket_cache 1:N | C | derived membership (composite FK + cascade) |
| clustering | — | C | fingerprint invalidation key |
| schema_info | singleton | C | typed; regenerated at restore |

---

## 3. The recommended schema, table by table

Conventions: `PRAGMA foreign_keys=ON`, `journal_mode=WAL`, `synchronous=NORMAL`. TEXT PKs are
UUIDv4 unless noted. Timestamps are ISO-8601 UTC TEXT. All paths are **relative to
`event_root`**; `event_root` itself is never stored (it is the DB's own folder). `event_root_abs`
is a cross-volume fallback only, normally NULL. `bool` ≡ `INTEGER NOT NULL DEFAULT 0
CHECK(col IN (0,1))`.

**Shared enum domains** (documented once; enforced by identical per-column CHECK, not copied
ad hoc):
- `PHASE ∈ ('cull','select','process','curate')`
- `KD ∈ ('discarded','candidate','kept')`

### 3.1 `schema_info` — **C** (typed; regenerated at create/restore)
```
schema_info(
  id             INTEGER PRIMARY KEY CHECK (id = 1),
  schema_version INTEGER NOT NULL,
  app_version    TEXT NOT NULL,
  event_id       TEXT NOT NULL,
  created_at     TEXT NOT NULL
)
```
Replaces the KV `meta(k,v)`. Typed columns, single enforced row. Not in backup.

### 3.2 `event` — **D** (enforced singleton; budget folded in)
```
event(
  id                INTEGER PRIMARY KEY CHECK (id = 1),     -- one row, enforced
  uuid              TEXT NOT NULL UNIQUE,                   -- stable external id
  name              TEXT NOT NULL,
  start_date        TEXT,            -- ISO date, nullable
  end_date          TEXT,
  is_closed         INTEGER NOT NULL DEFAULT 0 CHECK (is_closed IN (0,1)),
  notes             TEXT NOT NULL DEFAULT '',
  google_album_name TEXT NOT NULL DEFAULT '',
  google_album_link TEXT NOT NULL DEFAULT '',
  whatsapp_message  TEXT NOT NULL DEFAULT '',
  event_root_abs    TEXT,            -- cross-volume fallback ONLY; normally NULL
  -- trip_budget (was CHECK(id=1) singleton-as-table) folded in, 1:1 with event:
  budget_short_target_s INTEGER,
  budget_short_max_s    INTEGER,
  budget_long_target_s  INTEGER,
  budget_long_max_s     INTEGER,
  budget_video_share    REAL CHECK (budget_video_share IS NULL OR
                                    (budget_video_share >= 0 AND budget_video_share <= 1)),
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
)
```
`CHECK(id=1)` makes the gateway's no-WHERE `UPDATE event SET …` provably correct. `EventStatus`
dropped (D6); `is_closed` is the only lifecycle bit. Distribution metadata kept inline on
`event` (it is event-scoped, low-cardinality, and the legacy UI reads it as event fields — the
`purist` candidate's per-channel split was rejected as over-normalization for the port).

### 3.3 `trip_day` — **D**
```
trip_day(
  day_number  INTEGER PRIMARY KEY,     -- the FK target everywhere; NULL day_number ⇒ "undated"
  date        TEXT,                     -- ISO date, nullable; NOT unique (see open Q1)
  description TEXT NOT NULL DEFAULT '',
  location    TEXT,
  tz_minutes  INTEGER
)
CREATE INDEX ix_trip_day_date ON trip_day(date);
```
`day_number` is the real PK every `day_number` FK points at — killing the `day_key`
free-TEXT/`'undated'`-sentinel. (We deliberately do **not** add `UNIQUE(date)`: the legacy
"smallest-day-number-wins" tie-break copes with split/multi-leg days; a hard UNIQUE could
surface as a plan-editor error the legacy silently handled — see Open Q1.)

### 3.4 `camera` — **D**
```
camera(
  camera_id              TEXT PRIMARY KEY,     -- 'Make+Model' business key
  is_reference           INTEGER NOT NULL DEFAULT 0 CHECK (is_reference IN (0,1)),
  is_phone               INTEGER NOT NULL DEFAULT 0 CHECK (is_phone IN (0,1)),
  configured_tz_minutes  INTEGER,
  applied_offset_minutes INTEGER,
  applied_at             TEXT
)
CREATE UNIQUE INDEX ux_camera_one_reference ON camera(is_reference) WHERE is_reference = 1;
```
The partial unique index enforces at-most-one reference camera. `calibration_json` →
relational child:

```
camera_calibration_pair(
  id              TEXT PRIMARY KEY,
  camera_id       TEXT NOT NULL REFERENCES camera(camera_id) ON DELETE CASCADE,
  ref_item_id     TEXT REFERENCES item(id) ON DELETE SET NULL,    -- the reference photo used
  subject_item_id TEXT REFERENCES item(id) ON DELETE SET NULL,    -- this camera's photo
  ref_time        TEXT NOT NULL,
  camera_time     TEXT NOT NULL,
  offset_minutes  INTEGER NOT NULL,
  created_at      TEXT NOT NULL
)
CREATE INDEX ix_calib_camera ON camera_calibration_pair(camera_id);
```
The pair-picker history becomes queryable rows with real FKs to the actual photos. `applied_*`
on `camera` stays the resolved value. (Note: `ON DELETE SET NULL` means a pair can outlive its
anchoring items as a bare offset+timestamps — acceptable; the resolved `applied_offset_minutes`
on `camera` is the authoritative value, the pair rows are provenance.)

### 3.5 `participant` + `participant_device` — **D** (v1-empty, structurally correct)
```
participant(
  id   TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  role TEXT
)
participant_device(
  participant_id TEXT NOT NULL REFERENCES participant(id) ON DELETE CASCADE,
  camera_id      TEXT NOT NULL REFERENCES camera(camera_id) ON DELETE CASCADE,
  PRIMARY KEY (participant_id, camera_id)
)
CREATE INDEX ix_participant_device_camera ON participant_device(camera_id);
```
`devices_json` → a real join table. v1 populates zero rows (docs/03 withdrew per-person
attribution) but the relationship is correct and free.

### 3.6 `checklist_item`, `distribution_action` — **D**
```
checklist_item(
  id      TEXT PRIMARY KEY,
  label   TEXT NOT NULL,
  checked INTEGER NOT NULL DEFAULT 0 CHECK (checked IN (0,1)),
  notes   TEXT NOT NULL DEFAULT '',
  camera_id TEXT REFERENCES camera(camera_id) ON DELETE SET NULL   -- TZ-set items link a body
)
distribution_action(
  id         TEXT PRIMARY KEY,
  timestamp  TEXT NOT NULL,
  channel    TEXT NOT NULL,
  item_count INTEGER NOT NULL DEFAULT 0 CHECK (item_count >= 0),
  share_url  TEXT,
  notes      TEXT NOT NULL DEFAULT ''
)
```

### 3.7 `item` — **D** (the spine; ONE node per clip; file identity nullable = virtual)
```
item(
  id                     TEXT PRIMARY KEY,
  kind                   TEXT NOT NULL CHECK (kind IN ('photo','video')),
  provenance             TEXT NOT NULL DEFAULT 'captured'
                              CHECK (provenance IN ('captured','snapshot','clip','stack_output')),
  -- FILE IDENTITY — nullable iff the node is still virtual --------------------
  origin_relpath         TEXT UNIQUE,           -- NULL while virtual
  sha256                 TEXT,
  byte_size              INTEGER CHECK (byte_size IS NULL OR byte_size >= 0),
  materialized_at        TEXT,                  -- when bytes were written (NULL = virtual)
  materialized_phase     TEXT CHECK (materialized_phase IN
                              ('ingest','cull','select','process') OR materialized_phase IS NULL),
  -- identity / placement ------------------------------------------------------
  camera_id              TEXT REFERENCES camera(camera_id) ON DELETE RESTRICT,
  day_number             INTEGER REFERENCES trip_day(day_number) ON DELETE SET NULL,
  parent_item_id         TEXT REFERENCES item(id) ON DELETE CASCADE,   -- child → source video
  capture_time_raw       TEXT,                  -- virtual EXIF, NEVER mutated
  capture_time_corrected TEXT,                  -- derived = raw + offset; the sort key
  tz_offset_minutes      INTEGER NOT NULL DEFAULT 0,
  tz_source              TEXT NOT NULL DEFAULT 'none' CHECK (tz_source IN ('pair','tz','manual','none')),
  classification         TEXT,
  classification_source  TEXT CHECK (classification_source IN ('auto','user') OR classification_source IS NULL),
  classification_rules_version TEXT,
  sharpness_score        REAL,                  -- lazy cache (recoverable by recompute)
  sharpness_metric       TEXT,
  quarantine_status      TEXT NOT NULL DEFAULT 'ok'
                              CHECK (quarantine_status IN ('ok','no_timestamp','recovered')),
  recovered_from_filename INTEGER NOT NULL DEFAULT 0 CHECK (recovered_from_filename IN (0,1)),
  created_at             TEXT NOT NULL,

  -- VIRTUAL/MATERIALISED invariant: bytes present iff materialized -----------
  CHECK ( (origin_relpath IS NULL  AND sha256 IS NULL  AND byte_size IS NULL  AND materialized_at IS NULL)
       OR (origin_relpath IS NOT NULL AND sha256 IS NOT NULL AND byte_size IS NOT NULL AND materialized_at IS NOT NULL) ),
  -- only derived kinds may be virtual; captured/stack_output always have bytes
  CHECK ( origin_relpath IS NOT NULL OR provenance IN ('snapshot','clip') ),
  -- captured items carry a camera + raw time:
  CHECK ( provenance <> 'captured' OR (camera_id IS NOT NULL AND capture_time_raw IS NOT NULL) ),
  -- the parent triangle: captured/stack_output are roots; snapshot/clip have a parent
  CHECK ( (provenance IN ('snapshot','clip')) = (parent_item_id IS NOT NULL) ),
  -- kind/provenance coherence: a clip IS a video, a snapshot IS a photo
  CHECK ( provenance <> 'clip'     OR kind = 'video' ),
  CHECK ( provenance <> 'snapshot' OR kind = 'photo' )
)
CREATE INDEX ix_item_parent         ON item(parent_item_id);                                  -- video children
CREATE INDEX ix_item_nav            ON item(provenance, day_number, capture_time_corrected);  -- navigator hot path
CREATE INDEX ix_item_camera         ON item(camera_id);                                       -- TZ recompute
CREATE INDEX ix_item_classification ON item(classification);                                  -- subset resolution
CREATE INDEX ix_item_time           ON item(capture_time_corrected);                          -- chronological merges
```

Note: `capture_time_raw`/`capture_time_corrected`/`camera_id` are nullable at the column level
(a virtual clip has no independent capture instant of its own pre-materialise — it inherits from
its parent) but the `provenance='captured'` CHECK forces them present for real captured files.
`sharpness_score` stays on `item` (a column, lazily filled) rather than a separate cache table —
it is recoverable but cheap to colocate and every per-item read already wants it.

### 3.8 `phase_state` — **D** (the ONE K/D table, for everything: photos, videos, clips, snapshots, stack outputs)
```
phase_state(
  item_id       TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  phase         TEXT NOT NULL CHECK (phase IN ('cull','select','process','curate')),
  state         TEXT NOT NULL DEFAULT 'discarded' CHECK (state IN ('discarded','candidate','kept')),
  derived_dirty INTEGER NOT NULL DEFAULT 0 CHECK (derived_dirty IN (0,1)),
  decided_at    TEXT,
  committed_at  TEXT,
  PRIMARY KEY (item_id, phase)
)
CREATE INDEX ix_phase_state_hist      ON phase_state(phase, state);            -- global histogram (#4)
CREATE INDEX ix_phase_state_item      ON phase_state(item_id, phase);          -- per-bucket/per-item compose (#2,#5) — NEW
CREATE INDEX ix_phase_dirty           ON phase_state(phase, derived_dirty);
CREATE INDEX ix_phase_state_committed ON phase_state(phase, committed_at);     -- heatmap (#5)
```
**Row-exists-iff-decided** stays first-class (absence = undecided). There is **no** second
state record anywhere: a clip's K/D is its own `phase_state` row (because a clip is its own
`item`). The v3 `video_moment.state`-vs-`phase_state` divergence is structurally gone — there is
nothing for it to disagree with.

### 3.9 `video_marker` + `video_segment` + `video_snapshot` — **D** (the marker-partition model; schema v4, spec/56)

*(Rewritten 2026-06-10 — replaces the retired `clip_span`.)*

```
video_marker(                       -- the user's cut points on a SOURCE video
  id            TEXT PRIMARY KEY,
  video_item_id TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  at_ms         INTEGER NOT NULL CHECK (at_ms > 0),   -- 0/duration are the implicit ends
  created_at    TEXT NOT NULL,
  UNIQUE (video_item_id, at_ms)                       -- no zero-length segments
)

video_segment(                      -- 1:1 satellite for segment items: ORDER IDENTITY only
  item_id       TEXT PRIMARY KEY REFERENCES item(id) ON DELETE CASCADE,
  video_item_id TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  seg_index     INTEGER NOT NULL CHECK (seg_index >= 0),
  created_at    TEXT NOT NULL,
  UNIQUE (video_item_id, seg_index)
)

video_snapshot(                     -- 1:1 satellite for snapshot items: the point
  item_id       TEXT PRIMARY KEY REFERENCES item(id) ON DELETE CASCADE,
  video_item_id TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  at_ms         INTEGER NOT NULL CHECK (at_ms >= 0),
  created_at    TEXT NOT NULL
)
CREATE INDEX ix_video_snapshot_video ON video_snapshot(video_item_id, at_ms);
```

Every video is born with two **implicit** markers (start + end) that are never
stored — zero `video_marker` rows means ONE segment spanning the whole
timeline. Consecutive markers define segments that **tile** the timeline (no
gaps, no overlaps — overlapping clips are impossible by construction). A
segment is still one `item` (provenance `'clip'`, child of its source via
`parent_item_id` — the §1 one-node spine unchanged); its satellite stores
ONLY `seg_index` because **identity is the position in the marker order,
never milliseconds** (spec/56 locked rules): geometry derives at read time
(`core.video_segments.segment_bounds`), so moving a marker re-times a segment
without touching its row, its `phase_state` or its `video_adjustment`; a
marker inserted inside segment *k* splits it (the original row stays as the
left half, a new item at *k+1* inherits state + adjustments verbatim, later
rows shift up); deleting a marker merges (the LEFT half survives at the
merged position, the right half's item cascades away, later rows shift down).
The gateway maintains `count(segments) = count(markers) + 1` with dense
indexes; rows materialise lazily on first workshop touch, each born with an
explicit `phase_state('edit','skipped')` row (spec/56: default Skip,
deliberately immune to the settings-driven edit default). Snapshots auto-Pick
at creation and take the photo `adjustment` table (full photo treatment).

`video_segment.video_item_id` / `video_snapshot.video_item_id` mirror
`item.parent_item_id` — the same acknowledged denormalization `clip_span`
carried, hosting the per-source UNIQUE constraints; the gateway keeps the
copies in lockstep. The old `lineage_id` ('c1'/'s1') is gone: the segment
item's own id is the stable identity, and export naming is slice 4's concern.
`label` is gone: subjects live on `item.subject` (universal media-unit
annotation, spec/56 §2).

### 3.10 `adjustment` — **D** (photo Process edits; crop promoted, tone map = D4 blob)
```
adjustment(
  item_id      TEXT PRIMARY KEY REFERENCES item(id) ON DELETE CASCADE,
  params_json  TEXT,            -- D4-sanctioned tone-slider blob (exposure/contrast/.../clarity)
  crop_x       REAL CHECK (crop_x IS NULL OR (crop_x >= 0 AND crop_x <= 1)),
  crop_y       REAL CHECK (crop_y IS NULL OR (crop_y >= 0 AND crop_y <= 1)),
  crop_w       REAL CHECK (crop_w IS NULL OR (crop_w >  0 AND crop_w <= 1)),
  crop_h       REAL CHECK (crop_h IS NULL OR (crop_h >  0 AND crop_h <= 1)),
  crop_angle   REAL NOT NULL DEFAULT 0,
  rotation     INTEGER NOT NULL DEFAULT 0 CHECK (rotation IN (0,90,180,270)),
  aspect_label TEXT,
  auto_on      INTEGER NOT NULL DEFAULT 1 CHECK (auto_on IN (0,1)),
  strength     REAL NOT NULL DEFAULT 1.0 CHECK (strength >= 0 AND strength <= 2),
  process_exported INTEGER NOT NULL DEFAULT 0 CHECK (process_exported IN (0,1)),
  CHECK ( (crop_x IS NULL) = (crop_y IS NULL)
      AND (crop_x IS NULL) = (crop_w IS NULL)
      AND (crop_x IS NULL) = (crop_h IS NULL) )            -- crop rect all-or-nothing
)
```
The crop **rectangle** is promoted out of `crop_norm_json` into four `[0,1]`-CHECKed columns,
co-living with the already-promoted angle/rotation/aspect — ending the v3 half-normalised split.
Only the **tone-slider map** stays a blob, exactly as D4 sanctions ("blob until we must query by
a param" — no surface queries items by exposure value). Applies to snapshots too (a snapshot is
`kind='photo'` → same table; no separate `SnapshotOverride`, per docs/24).

### 3.11 `video_adjustment` — **D** (segment Edit refinements; keyed to the SEGMENT item)

*(v4 note: the trim deltas below RETIRED with spec/56 — markers are the trim —
and the key is the segment item; the tone payload became the spec/54 Look
choice. Live columns in `schema.py`.)*

```
video_adjustment(
  item_id      TEXT PRIMARY KEY REFERENCES item(id) ON DELETE CASCADE,   -- the segment item
  params_json  TEXT,            -- (superseded by spec/54 look/creative_filter columns)
  crop_x REAL CHECK (crop_x IS NULL OR (crop_x >= 0 AND crop_x <= 1)),
  crop_y REAL CHECK (crop_y IS NULL OR (crop_y >= 0 AND crop_y <= 1)),
  crop_w REAL CHECK (crop_w IS NULL OR (crop_w >  0 AND crop_w <= 1)),
  crop_h REAL CHECK (crop_h IS NULL OR (crop_h >  0 AND crop_h <= 1)),
  box_angle    REAL NOT NULL DEFAULT 0,
  aspect_ratio_label TEXT,
  auto_on      INTEGER NOT NULL DEFAULT 1 CHECK (auto_on IN (0,1)),
  style        TEXT,
  rep_frame_ms INTEGER CHECK (rep_frame_ms IS NULL OR rep_frame_ms >= 0),
  -- video-only fields (per-segment extras stay per segment — spec/56 §1) -----
  include_audio       INTEGER NOT NULL DEFAULT 1 CHECK (include_audio IN (0,1)),
  rotation_degrees    INTEGER NOT NULL DEFAULT 0 CHECK (rotation_degrees IN (0,90,180,270)),
  audio_volume        REAL NOT NULL DEFAULT 1.0 CHECK (audio_volume >= 0),
  audio_fade_ms       INTEGER NOT NULL DEFAULT 0 CHECK (audio_fade_ms >= 0),
  speed               REAL NOT NULL DEFAULT 1.0 CHECK (speed > 0),
  stabilise           REAL NOT NULL DEFAULT 0 CHECK (stabilise >= 0 AND stabilise <= 1)
)
```
Because a segment **is** an `item` from creation, its refinements key on `item_id` exactly like
`adjustment` — no `(source_item_id, moment_id)` composite, no separate moment identity. The
shared colour/crop columns are identical between `adjustment` and `video_adjustment` by
construction (the video surface reuses the photo `AdjustmentSurface` on the rep frame). The v3
"two near-duplicate adjustment shapes" shrinks to "one shared shape + a video-only extension."
We keep them as **two tables** (a photo and a clip are different edit surfaces; one row each)
rather than relationalising the shared params into an EAV table — that was rejected (D4, and the
`tone_param` nullable-composite-PK bug the critique found).

### 3.12 `stack_bracket` + `stack_member` — **D**
```
stack_bracket(
  bracket_id    TEXT PRIMARY KEY,
  kind          TEXT NOT NULL CHECK (kind IN ('focus','exposure')),
  action        TEXT CHECK (action IN ('stacked','picked','skipped') OR action IS NULL),
  picked_index  INTEGER NOT NULL DEFAULT -1,
  output_item_id TEXT REFERENCES item(id) ON DELETE SET NULL,   -- the merged result, an item
  day_number    INTEGER REFERENCES trip_day(day_number) ON DELETE SET NULL
)
CREATE INDEX ix_stack_day ON stack_bracket(day_number);

stack_member(
  bracket_id TEXT NOT NULL REFERENCES stack_bracket(bracket_id) ON DELETE CASCADE,
  item_id    TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,   -- NOW cascades (v3 did not)
  ordinal    INTEGER NOT NULL CHECK (ordinal >= 0),
  PRIMARY KEY (bracket_id, item_id),
  UNIQUE (bracket_id, ordinal)                                      -- ordered frames, no collisions
)
CREATE INDEX ix_stack_member_item ON stack_member(item_id);
```
The stack output is `output_item_id` → an `item` with `provenance='stack_output'` (bytes in the
item's file identity), replacing the free-text `output_relpath`. This makes the N→1 stack output
a first-class spine node, so `lineage` can FK to it cleanly.

### 3.13 `curate_tag` — **D**
```
curate_tag(
  item_id      TEXT PRIMARY KEY REFERENCES item(id) ON DELETE CASCADE,
  level        TEXT CHECK (level IN ('best','short','long','composition','collage_only') OR level IS NULL),
  theme        TEXT,
  solo         INTEGER NOT NULL DEFAULT 0 CHECK (solo IN (0,1)),
  is_discarded INTEGER NOT NULL DEFAULT 0 CHECK (is_discarded IN (0,1)),
  tag_set_at   TEXT
)
CREATE INDEX ix_curate_level ON curate_tag(level);
CREATE INDEX ix_curate_theme ON curate_tag(theme);
```

### 3.14 `subset` + `subset_member` — **D** (base = discriminated self-FK)
```
subset(
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL UNIQUE,              -- fs-safe, unique per event
  base_kind     TEXT NOT NULL CHECK (base_kind IN ('literal','subset')),
  base_literal  TEXT CHECK (base_literal IN ('short','long','all_time_best','portfolio') OR base_literal IS NULL),
  base_subset_id TEXT REFERENCES subset(id) ON DELETE CASCADE,
  genre_filter  TEXT,
  target_s      INTEGER CHECK (target_s IS NULL OR target_s >= 0),
  max_s         INTEGER CHECK (max_s    IS NULL OR max_s    >= 0),
  CHECK ( (base_kind='literal' AND base_literal IS NOT NULL AND base_subset_id IS NULL)
       OR (base_kind='subset'  AND base_subset_id IS NOT NULL AND base_literal IS NULL) )
)
CREATE INDEX ix_subset_base ON subset(base_subset_id);

subset_member(
  subset_id TEXT NOT NULL REFERENCES subset(id) ON DELETE CASCADE,
  item_id   TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,   -- NOW cascades
  excluded  INTEGER NOT NULL DEFAULT 0 CHECK (excluded IN (0,1)),
  PRIMARY KEY (subset_id, item_id)
)
CREATE INDEX ix_subset_member_item ON subset_member(item_id);
```
The overloaded `base TEXT` becomes a discriminator + a literal column + a **real self-FK**.
Base-chaining is FK-enforced and `WITH RECURSIVE`-resolvable. **Membership stays resolved on
demand** ((base ∩ genre_filter) − excluded); `subset_member` stores the exclusion/override set,
not the full inclusion set (frozen invariant 6.2). Cycle-prevention in the chain stays a gateway
invariant (SQLite cannot express acyclicity declaratively).

### 3.15 `curate_map` — **D** (the map-separator slides; was under-modelled)
```
curate_map(
  id             TEXT PRIMARY KEY,
  day_number     INTEGER NOT NULL REFERENCES trip_day(day_number) ON DELETE CASCADE,
  sequence       INTEGER NOT NULL DEFAULT 0,        -- 0 = before day, 999 = after
  source_relpath TEXT,
  caption        TEXT NOT NULL DEFAULT '',
  crop_x REAL, crop_y REAL, crop_w REAL, crop_h REAL,
  composed_relpath TEXT,
  UNIQUE (day_number, sequence)
)
```

### 3.16 `lineage` — **D** (real FKs both directions; discriminated)
```
lineage(
  export_relpath   TEXT PRIMARY KEY,
  phase            TEXT NOT NULL CHECK (phase IN ('process','curate')),
  source_kind      TEXT NOT NULL CHECK (source_kind IN ('item','bracket')),
  source_item_id   TEXT REFERENCES item(id) ON DELETE CASCADE,            -- 1→1 source
  source_bracket_id TEXT REFERENCES stack_bracket(bracket_id) ON DELETE CASCADE, -- N→1 stack source
  CHECK ( (source_kind='item'    AND source_item_id IS NOT NULL AND source_bracket_id IS NULL)
       OR (source_kind='bracket' AND source_bracket_id IS NOT NULL AND source_item_id IS NULL) )
)
CREATE INDEX ix_lineage_item    ON lineage(source_item_id);
CREATE INDEX ix_lineage_bracket ON lineage(source_bracket_id);
CREATE INDEX ix_lineage_phase   ON lineage(phase);
```
The free-text `bucket_key` (FK-impossible by construction) is gone. The only durable N→1 export
is a focus/exposure **stack** (processed ↔ bracket members — the frozen "lineage is ↔ bucket,
never ↔ a single RAW for stacks"); `stack_bracket.bracket_id` is a PK, so it gets a real FK.
Photo-bucket outputs (burst/moment) are always 1→1 to the kept item, so they use
`source_item_id`. **Open Q4** addresses whether any future non-stack N→1 output needs a more
general target.

### 3.17 Durable bucket soft-state — **D**
```
bucket(
  bucket_key      TEXT NOT NULL,                  -- content-stable {day|kind|content_key}
  phase           TEXT NOT NULL CHECK (phase IN ('cull','select','process','curate')),
  default_state   TEXT NOT NULL DEFAULT 'discarded' CHECK (default_state IN ('discarded','kept')),
  reviewed        INTEGER NOT NULL DEFAULT 0 CHECK (reviewed IN (0,1)),
  browsed         INTEGER NOT NULL DEFAULT 0 CHECK (browsed IN (0,1)),
  nudge_dismissed INTEGER NOT NULL DEFAULT 0 CHECK (nudge_dismissed IN (0,1)),
  current_index   INTEGER NOT NULL DEFAULT 0 CHECK (current_index >= 0),
  PRIMARY KEY (bucket_key, phase)
)
CREATE INDEX ix_bucket_phase_reviewed ON bucket(phase, reviewed);   -- kills full-scan reviewed count (#4)
```
`bucket_key` is FK-less **by design** — it is a content-stable recomputed id (D5/invariant
2.3), and soft-state (`reviewed`/`browsed`/`current_index`/`nudge_dismissed`) must survive a
membership-preserving cache recompute. This is the one sanctioned convention-seam (§5).

### 3.18 Derived cache layer — **C** (excluded from backup; droppable+rebuildable)
```
bucket_cache(                          -- the recomputed grouping structure
  bucket_key       TEXT NOT NULL,
  phase            TEXT NOT NULL CHECK (phase IN ('cull','select','process','curate')),
  day_number       INTEGER REFERENCES trip_day(day_number) ON DELETE CASCADE,  -- NULL = undated (real FK, no sentinel)
  kind             TEXT NOT NULL CHECK (kind IN
                       ('focus_bracket','exposure_bracket','burst','repeat','moment','individual','video','video_moment')),
                       -- 'repeat' added 2026-06-10 (store v3 migration) — the Quick Sweep
                       -- slice-B cluster kind (2026-06-09) the enum had missed.
  title            TEXT NOT NULL DEFAULT '',
  detection_source TEXT NOT NULL DEFAULT '',
  camera           TEXT NOT NULL DEFAULT '',
  ordinal          INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (bucket_key, phase)
)
CREATE INDEX ix_bucket_cache_day ON bucket_cache(phase, day_number);

bucket_member(                         -- the recomputed membership (the ONLY place item→bucket lives)
  bucket_key TEXT NOT NULL,
  phase      TEXT NOT NULL CHECK (phase IN ('cull','select','process','curate')),
  item_id    TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  ordinal    INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (bucket_key, phase, item_id),
  FOREIGN KEY (bucket_key, phase) REFERENCES bucket_cache(bucket_key, phase) ON DELETE CASCADE  -- THE missing FK
)
CREATE INDEX ix_bucket_member ON bucket_member(phase, bucket_key);

clustering(                            -- fingerprint invalidation key per (phase, day)
  phase       TEXT NOT NULL CHECK (phase IN ('cull','select','process','curate')),
  day_number  INTEGER REFERENCES trip_day(day_number) ON DELETE CASCADE,   -- NULL = undated
  fingerprint TEXT NOT NULL,
  computed_at TEXT NOT NULL,
  PRIMARY KEY (phase, day_number)
)
```
The v3 bucket triplet joined by naming convention becomes: `bucket_member → bucket_cache` with a
**real composite FK + cascade** (the hand-written correlated-subquery delete in `save_day_cache`
becomes a cascade), the `kind` CHECK that v3 lacked is added, and `day_key` free-TEXT/`'undated'`
becomes a real nullable `day_number` FK. The durable `bucket` soft-state row deliberately does
**not** FK into the cache (its lifetime is independent).

### Tables removed vs v3
| Removed | Why |
|---|---|
| `video_moment` | A clip/snapshot is now one `item` (child + 1:1 satellite — `clip_span` in v1, `video_segment`/`video_snapshot` since v4). Its two v3 identities collapse into one node; `produced_item_id` disappears (the moment *is* the item; "materialised" = file identity present). |
| `video_override` | → `video_adjustment`, keyed on the child `item_id` (the segment item since v4). |
| `video_marker` | *(v1 reasoning, superseded.)* v1 said "a kept span IS a `clip_span` child; bare markers are transient." **Schema v4 (spec/56) reverses this**: markers became the first-class rows (§3.9) and the freeform spans retired — segments derive from marker order. |
| `trip_budget` | Folded into `event` (5 nullable 1:1 scalars). |
| `meta(k,v)` | → typed `schema_info`. |

---

## 4. How the design honors each fixed concept

**Concept #1 — videos are buckets (unified parent→child).** A source video's children
(clips/snapshots) and a photo bucket's members are reconciled into **two clean layers, honestly
labelled** (the critiques were emphatic that the `purist` "reads as one, stores as two" was the
flaw to avoid):

- **The durable parent→child edge is `item.parent_item_id`** — used by clips/snapshots *and*
  stack outputs. This is the SAME self-referential edge for every derived child. A video's
  children are `item` rows under it via `parent_item_id`; traversal is `ix_item_parent`.
- **Photo-bucket membership is the derived `bucket_member` edge** — recomputed, regenerable,
  invalidated by fingerprint. Photo grouping is *clustering*, not authored structure, so it
  belongs in the cache.

The conceptual parallel is exact: "give me the children of this container" is one query
(`WHERE parent_item_id = ?` for a video; `bucket_member` lookup for a photo bucket), and the
navigator's Day→Bucket→child hierarchy drives both. We do **not** pretend a photo burst's members
are durable rows (they are a recompute) — that honesty is the fix the `purist` critique demanded.
What is unified and durable is the thing that *must* be: the clip/snapshot child node, expressed
once as an `item` with a parent.

**Concept #2 — virtual until Process (precise relational meaning).** "Virtual" = an `item` row
with **NULL file identity** (`origin_relpath IS NULL`, enforced by the all-or-nothing CHECK).
Zero bytes; rows only; disk cost zero regardless of table. Materialisation = filling
`origin_relpath`/`sha256`/`byte_size`/`materialized_at` in one UPDATE. The CHECK
`origin_relpath IS NOT NULL OR provenance IN ('snapshot','clip')` permits **only** derived kinds
to be virtual — a captured file or a stack output always has bytes. Crucially, we do **not**
claim "virtual = pre-Process": `materialized_phase` records *when* bytes were written, so a
snapshot can materialise early at Cull (docs/24: cheap lossless frame) while a clip stays virtual
until Process — same table, same one-node model, no contradiction. This is exactly the
`unified-item-spine` insight, achieved with a nullable column instead of the rejected
`media_file` 1:1 split (which would have taxed the never-virtual captured-item bulk with a
mandatory join).

**Concept #3 — whole-video keep with no special case** *(rewritten for v4 —
spec/56 made this stronger)*. A video is born as ONE segment (zero markers);
keeping the whole video is **that original segment, picked** — not even a
flagged full-span row (`is_full_span` retired with the freeform spans;
Nelson: "No difference. Beautiful."). The source video still carries no
independent "kept" state to desync (the bug the `conservative` critique
caught) — kept-ness lives on the segment children. Export iterates a single
uniform query:

```sql
SELECT child.*, vs.seg_index
FROM item child
JOIN video_segment vs ON vs.item_id = child.id
JOIN phase_state ps   ON ps.item_id = child.id AND ps.phase = 'edit'
WHERE child.parent_item_id = :video_id AND ps.state = 'picked'
ORDER BY vs.seg_index;          -- order IS timeline order; geometry derives from markers
```
No `if has_clips then iterate_clips else process_whole_video` branch exists anywhere — there is
no other shape for "a thing Export renders from a video," because a kept whole video *is* the
single segment child.

**Concept #4 — leverage the relational engine.** Every JSON blob carrying *structure* is
relationalised: `devices_json`→`participant_device`, `calibration_json`→`camera_calibration_pair`,
crop rect→`crop_x/y/w/h` columns. Every convention-join gets a real FK: `bucket_member`→
`bucket_cache` (composite + cascade), `day_key`→`day_number` FK, `subset.base`→discriminated
self-FK, `lineage`→FK to item/bracket. Every singleton is enforced (`event`/`schema_info`
`CHECK(id=1)`). The kind/provenance/parent triangle is locked by CHECKs (no orphan clip, no
captured-with-parent, no clip-that-is-a-photo). The three missing hot-path indexes are added
(§6). The **one** blob we keep is the tone-slider map (`params_json`), per frozen D4 — no surface
queries items by slider value, so relationalising it is cost on the cold path the critiques twice
flagged.

---

## 5. The derived / cache layer (clean separation, durable soft-state preserved)

Three strata, separated by **table identity** (not column flags), so the backup rule is trivial
— "**C** tables are never serialised":

1. **Durable spine + satellites + soft-state** — `item`, `clip_span`, `phase_state`,
   `adjustment`, `video_adjustment`, `curate_tag`, `subset*`, `stack*`, `curate_map`, `lineage`,
   `bucket` (soft-state), all event-level tables. System-of-record.
2. **Derived bucket/clustering cache** — `bucket_cache`, `bucket_member`, `clustering`. **C**,
   excluded from backup, regenerated by re-scan, invalidated per `(phase, day_number)`
   fingerprint (D5). Item→bucket **membership lives only here** (invariant 2.e) — never durable.
3. **The filesystem projection** (`01 - Culled/ … 04 - Curated/` hardlink tree) — outside the DB
   entirely, rebuilt from `phase_state` + item file identity on phase-exit silent-sync.

Separation is enforced two ways: (i) **FK direction** — cache tables FK *into* durable
(`bucket_member.item_id → item`, `bucket_cache.day_number → trip_day`), but **nothing durable
FKs into a cache table**, so `DELETE FROM bucket_cache; DELETE FROM bucket_member; DELETE FROM
clustering;` + re-scan is always safe and never touches a durable row; (ii) the durable `bucket`
soft-state row has **no** FK into the cache, so wiping the cache never disturbs
`reviewed`/`browsed`/`current_index`. Because `bucket.bucket_key` is content-stable, a
membership-preserving recompute re-derives the identical key and the soft-state still matches —
**this is why `bucket_key` is intentionally not an FK** (the thing it identifies is recomputed,
not stored). Completion/phase-progress is **never a table in any stratum** — it is a `GROUP BY`
over `phase_state` (invariant 2.d), made sub-millisecond by the new indexes.

---

## 6. What changes vs the current schema, and why each makes the code faster or more reliable

| Change | Faster / more reliable — and the named hot path or guarantee |
|---|---|
| **`ix_phase_state_item (item_id, phase)`** (NEW) | **Faster.** The per-bucket/per-item state composition (access-map #2 honest-status projection, #5 per-bucket surface load) currently loads the *whole phase* of `phase_state` to read one bucket's ~dozen marks. The v3 `(phase, state)` index can't serve a per-item lookup; this one does — turns the per-bucket histogram into an index seek. |
| **`ix_item_nav (provenance, day_number, capture_time_corrected)`** (NEW) | **Faster.** The navigator's `_captured_by_day` filters `provenance='captured'` and orders by corrected time on **every** Cull open/refresh (the hottest path). v3 had no `provenance` index → a 10k-row full hydrate; this makes it a covered range scan. |
| **`ix_bucket_phase_reviewed (phase, reviewed)`** (NEW) | **Faster.** `phase_progress` counts reviewed buckets by `store.all(Bucket)` + Python (#4); becomes `SELECT COUNT(*) … WHERE phase=? AND reviewed=1`. |
| **`ix_curate_level` + `ix_item_classification`** (NEW) | **Faster.** Curate subset resolution (#9 — the heaviest Curate read) intersects `curate_tag.level` with `item.classification`; both were unindexed full scans. |
| **One node per clip (drop `video_moment`/`produced_item_id`)** | **More reliable.** Eliminates the v3 dual-identity desync surface: a clip's state lived in *two* models (`video_moment.state` binary vs `phase_state` tri-state) with **no constraint** binding them. Now a clip is one `item` with one `phase_state` — nothing to disagree. Integrity guarantee: the kind/provenance/parent CHECK triangle. |
| **File identity nullable + all-or-nothing CHECK** | **More reliable.** "Virtual" is a single enforced row-state, not a cross-table LEFT-JOIN convention; impossible to have a half-materialised item (relpath set, sha NULL). |
| **`event`/`schema_info` `CHECK(id=1)`** | **More reliable.** The gateway's no-WHERE `UPDATE event SET …` (a latent multi-row-corruption hazard) is now provably correct. |
| **`bucket_member → bucket_cache` composite FK + cascade** | **Faster + more reliable.** Replaces the hand-written correlated-subquery membership delete with a cascade; removes the "join by string convention, no integrity" hole. |
| **`day_key` free-TEXT/`'undated'` → `day_number` FK** | **More reliable.** One representation of "day" (the FK; NULL = undated), no magic sentinel, no two-representations-coexisting drift. |
| **`subset.base` → discriminated self-FK** | **More reliable.** Base-chaining is FK-enforced and `WITH RECURSIVE`-resolvable; a referenced base can't vanish silently. |
| **`lineage.bucket_key` free-TEXT → FK to item/bracket** | **More reliable.** Every lineage row has a real source FK; reverse lookups (`source_item_id`/`source_bracket_id`/`phase`, all unindexed in v3 — #10) are indexed. |
| **Blobs → relations** (`devices_json`, `calibration_json`, crop rect) | **More reliable + queryable.** Structured data leaves opaque TEXT; the crop half-normalisation ends. |
| **`trip_budget` folded into `event`; `meta`→`schema_info`** | **More reliable + simpler.** One fewer join, one fewer singleton-as-table, typed install scalars. |
| **`stack_member.item_id` ON DELETE CASCADE + `UNIQUE(bracket_id, ordinal)`** | **More reliable.** No orphan members; no two frames at the same position. |
| **Keep `params_json` blob (reject param relationalization)** | **Faster port, no regression.** Avoids a 3-table hydration on the per-item editor read for query power no surface uses (D4). |

**Load-bearing caveat (all three critiques agreed):** the new indexes only pay off if the
**faithful port also rewrites the aggregate gateway methods** (`items()`, `day_tree()`,
`phase_day_progress()`, `phase_progress`'s reviewed count, `_captured_by_day`) from
`store.all()` + Python loops into SQL `JOIN`/`GROUP BY`/`WHERE` over these indexes. The schema
*enables* the fast paths; it does not *deliver* them. Because that is a change to data-access
shape (not UI/flow), it is squarely a §0-amendment "propose first" item — proposed here, and
listed in §8.

---

## 7. Port implications (per legacy surface) + gateway-method shapes

Supreme Rule: the UI is ported verbatim; **only data-access calls are rewired** to the gateway.
The schema is shaped so the gateway can present the legacy's mental model with thin methods.

**Cull (Camera/Fast/Final/Standalone — `BucketCullShell` + `IngestCullerPage` + Fast culler).**
- Navigator tree: `cull_days(phase)` → one query joining `item` (filtered
  `provenance='captured'`, `WHERE day_number`, ordered `capture_time_corrected`) over
  `ix_item_nav`, plus `bucket_cache`/`bucket_member` for cached grouping (composite-FK cascade),
  plus `bucket` soft-state by PK map. Honest status: `bucket_status(bucket_key, phase)` →
  `bucket_member ⋈ phase_state GROUP BY state` over `ix_phase_state_item`.
- Per-bucket surface: `bucket_items(bucket_key, phase)` returns items + their `phase_state` for
  *that bucket only* (not the whole phase) + `sharpness_score` inline (no per-item PK round-trip).
- Video cull *(SUPERSEDED by spec/56 — schema v4)*: Pick no longer authors clips
  at all (watch + P/D the whole video; the v1 `create_clip` / `create_snapshot` /
  `keep_whole_video` / `video_children` gateway surface retired). The Edit
  workshop owns the marker ops instead: `add_video_marker` (split + inherit) /
  `move_video_marker` (re-time, identity untouched) / `delete_video_marker`
  (merge, left survives) / `ensure_video_segments` (lazy birth) /
  `create_video_snapshot` (auto-Pick), with reads `video_markers` /
  `video_segments` / `segment_items` / `segment_bounds` / `video_snapshots`.
  Segment K/D is still plain `set_phase_state` (phase='edit').

**Select (`IngestCullerPage` mode='select').** Same surface; snapshots already flow into the
photo pool (they are `kind='photo'` items); per-source-video `video_moment` buckets are a
`bucket_cache.kind='video_moment'` grouping over the source's kept clip children. K/D via
`set_phase_state(phase='select')`. Classification nudge: `bucket.nudge_dismissed`.

**Process (ProcessPhotoPage + ProcessVideoPage).** Photos: `adjustment` by `item_id` (crop
columns + `params_json`). Videos: the uniform kept-children query in §4 (`video_children` filtered
`phase_state='kept'`), each child edited via `video_adjustment` by `item_id`. **Materialisation:**
`materialize(item_id, relpath, sha256, byte_size, phase)` fills the file identity (the single
virtual→real transition) — replaces the v3 `set_moment_produced`. Process iterates one list; no
whole-video special case. Stacks: `stack_bracket`/`stack_member`; output via `materialize` on a
`provenance='stack_output'` item, recorded in `lineage` by `source_bracket_id`.

**Curate (Curate pass + Collections page).** Tagging: `set_curate_tag(item_id, level, theme,
…)`. Subset resolve: `resolve_subset(id)` walks `base_subset_id` (recursive) → base tier items
via `ix_curate_level`, intersects `genre_filter` via `ix_item_classification`, subtracts
`subset_member.excluded`. Maps: `curate_map` by `day_number`/`sequence`. Lineage for portfolio
copies via `source_item_id`.

**Distribute.** `distribution_action` append-only log; `event` distribution metadata
(google/whatsapp fields) read as event fields (unchanged shape for the ported UI).

Cross-cutting gateway shapes implied: `set_phase_state` (creates row iff decided),
`mark_derived_dirty(item_ids, phases)` (the re-entry fix), `materialize(...)` (virtual→real),
the spec/56 marker ops (`add_video_marker`/`move_video_marker`/`delete_video_marker`/
`ensure_video_segments`/`create_video_snapshot` — superseding the v1 clip-creation trio), and the
**rewritten aggregates** (`day_tree`, `phase_progress`, `phase_day_progress`) as SQL GROUP BY.

---

## 8. Open questions for the user — **ALL RESOLVED (Nelson 2026-05-31)**

> Nelson accepted the recommended answer on every point. Resolutions:
> 1. **No** `trip_day UNIQUE(date)` — keep the smallest-day-number tie-break.
> 2. Video scrub markers **transient**; `nudge_dismissed` stays on `bucket`. (No durable
>    `video_marker` table.)
> 3. clip_span↔parent coherence enforced in the **gateway**, not by trigger.
> 4. A focus/exposure **stack is the only durable N→1 export** in v1 — `lineage.source_bracket_id`
>    FK to `stack_bracket` is sufficient.
> 5. **Yes — bundle the aggregate-gateway rewrite** with the schema landing (push predicates into
>    SQL `JOIN`/`GROUP BY`/`WHERE`); the schema is no faster without it.
> 6. **Yes** — `event`/`schema_info` use `INTEGER PRIMARY KEY CHECK(id=1)` + a stable `uuid`
>    column; every other table keeps its TEXT business PK.

The original questions + rationale are preserved below for the record.

1. **`trip_day` UNIQUE(date)?** The `conservative` candidate proposed `UNIQUE(date)` to kill the
   "smallest-day-number-wins" tie-break; its own critique warned multi-leg/timezone-split days
   might legitimately share a date and a hard constraint could surface as a plan-editor error the
   legacy silently coped with. **Recommendation: do NOT add it** (keep the tie-break). Confirm
   against the plan/manage surface before freezing.

2. **Persist video scrub markers + the classification-nudge "dismissed" flag (census D7)?**
   `nudge_dismissed` is kept on `bucket` (it's annoying to lose on re-entry). For raw *scrub*
   markers (not yet promoted to a clip/snapshot), this design treats them as **transient** (a
   kept span/point *is* a `clip_span` child; a bare marker is not yet a decision). If you want
   bare markers to survive a crash mid-session, we add a thin `video_marker(source_item_id,
   position_ms)` durable table back. **Decide: transient (recommended) or persisted.**

3. **Enforce the clip_span↔parent coherence with a trigger?** "A `clip_span` may only attach to
   an item whose parent is `kind='video'`," and "the denormalized `clip_span.parent_item_id`
   equals `item.parent_item_id`" are cross-row invariants SQLite CHECK can't express. Options:
   (a) gateway-enforced only (simplest, matches where the legacy already lived), or (b) add two
   small triggers (more declarative rigor, more surface area, ports slightly worse under the
   Supreme Rule). **Recommendation: (a) gateway-enforced**, revisit if a bug appears.

4. **Is a focus/exposure stack the only durable N→1 export?** `lineage.source_bracket_id` FKs
   only `stack_bracket`. If a future surface produces a non-stack many→one output (e.g. a
   multi-frame composite that isn't a bracket), this FK is too narrow and would need a
   generalised output target. **Confirm no v1 surface does this** (the design assumes none).

5. **Rewrite the aggregate gateway methods as part of this change?** The index wins are only
   realised if `items()`, `day_tree()`, `phase_day_progress()`, and `_captured_by_day` stop
   calling `store.all()` + Python-filtering and push predicates into SQL. This is a data-access
   reshaping (not UI/flow) and thus a §0 "propose first" item. **Approve bundling the gateway
   rewrite with the schema landing** (recommended — the schema without it is no faster), or keep
   it as a separate follow-up.

6. **`schema_info`/`event` as `INTEGER PRIMARY KEY CHECK(id=1)` vs the existing TEXT `event.id`.**
   This proposal keeps a stable `uuid` column for external reference and uses `id=1` for the
   singleton guarantee. Confirm the gateway/`repo.py` reflection is comfortable with the
   `id`/`uuid` split on `event` (every other table keeps its TEXT business PK).
