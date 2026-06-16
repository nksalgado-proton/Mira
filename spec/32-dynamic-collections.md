# spec/32 — Dynamic Collections & Exploration App

> **REVISED 2026-06-16 by [spec/81](81-dynamic-collection-and-cut.md).** The
> **Dynamic Collection (DC)** described here is now the **canonical live-query
> noun** of the whole app — generalised by spec/81 to **set algebra over
> operands** (base universes *and other DCs/Cuts*) plus the filters catalogued
> below, and made **scope-agnostic** (the same DC engine serves event and
> cross-event; only the operands in range and the storage home differ). A DC is
> only a definition — to play or export, it is **pinned into a Cut** (spec/81
> §3–§4). Legacy pipeline vocabulary in this doc (**cull / curate / select /
> kept**) is reconciled below to the locked terms (charter): the phases are
> **Collect / Pick / Edit / Export**, the verbs **Pick / Skip**, the surviving
> state value `'picked'`, and the universes the ladder rungs
> `#collected / #picked / #edited / #exported`. Read spec/81 first.

**Status:** Design registered. Foundation built (2026-06-02). Implementation
deferred until **post-Pick** (cross-event DCs+Cuts are spec/81's phase 2). No
behaviour changes to the core pipeline — this spec describes what the database
already supports and where to go next.

---

## 1. Vision

A **Dynamic Collection** is a named, saved query — a **formula of set algebra
over operands plus filters** — that resolves live to a set of media files across
all events. The user never hand-assembles the collection: they declare the
formula and the system delivers. It is **reusable** (an operand inside other
DCs) and **only a definition** — pinned into a Cut when the user wants to play
or export it (spec/81).

**Example queries that motivated this spec (Nelson 2026-06-02):**

> "All macro photos of insects with stacking and rating ≥ 4 stars."

> "Best photos of my son Pedro from birth to age 30, not posed
> (subject distance > X?), rated ≥ 3 stars."

> "All my shots taken with flash, ISO ≥ 1600, international trips, 2010–2025."

> "All 5-star photos — generate a hardlinked directory for slideshow."

The system should answer these queries across the entire lifetime archive, regardless of
how many events exist.

---

## 2. Dimensions — what we can query

The database (as of 2026-06-02) supports the following dimensions:

### 2a. Subjective / curatorial (per item, in `item.extras_json`)

| Key | Type | Notes |
|---|---|---|
| `stars` | int 1–5 | Star rating — Pick/Edit-phase curatorial input |
| `color_label` | str | `red`/`yellow`/`green`/`blue`/`purple` (LRC-compatible) |
| `flag` | bool | Portfolio flag — distinct from stars. **Renamed from `pick`** to avoid colliding with the locked Pick/Skip decision verbs (charter); the old key name is reconciled at implementation. |
| `caption` | str | Slide caption for PTE AV Studio bundles |

Accessed via `json_extract(extras_json, '$.stars')`. Partial indexes already exist
(`ix_item_stars`, `ix_item_color_label`).

### 2b. Temporal (per item, indexed columns)

| Column | Type | Query example |
|---|---|---|
| `capture_time_corrected` | TEXT (ISO) | `BETWEEN '2010-01-01' AND '2025-12-31'` |
| `day_number` → `trip_day.date` | via JOIN | filter by specific day |

### 2c. Location — without GPS (per event and per day)

Location is **user-supplied context**, not GPS. Two levels:

- `event.extras_json`: `{"country": "Nepal", "country_code": "NP", "city": "Kathmandu"}`
- `trip_day.extras_json`: `{"city": "Namche Bazaar", "sublocation": "Everest Base Camp"}`

The day-level overrides the event-level (more specific). IPTC Core flat field names are
used for LRC interoperability:

| Key | Meaning |
|---|---|
| `country` | Full country name |
| `country_code` | ISO 3166-1 alpha-2 (e.g. `"NP"`, `"BR"`, `"IT"`) |
| `state` | Province / state |
| `city` | City |
| `sublocation` | Landmark, street, specific location |
| `region` | Free-text regional grouping (e.g. `"South America"`, `"International"`) |

These IPTC-shaped location fields double as the **overlay "where" source**: at
export Mira writes them into the file's IPTC so PTE renders them natively
(spec/81 §3.1). The technical EXIF (§2d) is already in the file; only location
needs writing.

### 2d. Technical / EXIF (per item, indexed columns)

| Column | Type | Query example |
|---|---|---|
| `iso` | INTEGER | `iso >= 1600` → high-ISO / low-light |
| `aperture_f` | REAL | `aperture_f <= 2.8` → wide-open glass |
| `shutter_speed_s` | REAL | `shutter_speed_s >= 0.5` → long exposure |
| `focal_length_mm` | REAL | `focal_length_mm BETWEEN 90 AND 110` → macro range |
| `flash_fired` | 0/1 | `flash_fired = 1` → with flash |
| `lens_model` | TEXT | `lens_model = 'LEICA DG MACRO-ELMARIT 45/F2.8'` |
| `kind` | TEXT | `kind = 'video'` → videos only |
| `duration_ms` | INTEGER | `duration_ms >= 60000` → clips > 1 min |

Indexes: `ix_item_iso`, `ix_item_aperture`, `ix_item_shutter`, `ix_item_focal`,
`ix_item_flash`, `ix_item_lens`.

### 2e. Pipeline state (per item, via `phase_state`)

| Criterion | How |
|---|---|
| Picked (survived the Pick pass) | `phase_state.state = 'picked' AND phase = 'pick'` — the `#picked` universe |
| Edited | `#edited` rung (carries edits; spec/61 §1.1 notes edited ≠ exported) |
| Exported | `#exported` rung (lineage-backed; the base universe for event Cuts) |
| Classification / genre | `item.classification = 'macro'` |
| Is a stack output | `item.provenance = 'stack_output'` |
| Is a clip | `item.provenance = 'clip'` |

(Legacy `phase = 'cull' / 'select'` and `state = 'kept'` are retired in favour
of the locked Collect/Pick/Edit/Export ladder and the `'picked'` state.)

### 2f. Bracket / stack detection (per item, columns — deferred)

| Column | Meaning |
|---|---|
| `bracket_group_id` | Shared ID across all frames of one bracket set |
| `bracket_role` | `'leader'` / `'member'` |

Schema columns are in place. Detection algorithm (brand drive-mode EXIF + EV delta +
timestamp proximity) to be implemented at ingest in a future milestone.

### 2g. Facial recognition (deferred — future ML batch)

Via `item.extras_json → {"face_set_id": "abc123"}` pointing to a `face_set` table.
Run offline using a local DNN (InsightFace / DeepFace — no cloud, offline-first).
Only runs on the picked subset (post-Pick), not on the full raw archive.

### 2h. Tags / keywords (deferred — future `item_tag` table)

Hierarchical tags via `tag` + `tag_tree` (closure table) + `item_tag` junction.
digiKam pattern. Enables: "all wildlife shots", "all family shots", nested hierarchies.
`item.extras_json` can hold a `{"tag_ids": [...]}` pointer until the table is built.

---

## 3. Cross-event architecture

Each event has its own `event.db` (isolation, portability, backup). The exploration app
needs to search across all events. Two-tier approach:

### Tier 1 — `app.db` global summary index (to build)

A `global_items` table in `app.db` (the central settings database) containing a
denormalized subset of item fields from all events:

```sql
CREATE TABLE global_items (
  event_uuid        TEXT NOT NULL,
  event_name        TEXT NOT NULL,
  item_id           TEXT NOT NULL,
  origin_relpath    TEXT,          -- relative to event root
  capture_time      TEXT,
  classification    TEXT,
  kind              TEXT,
  provenance        TEXT,
  iso               INTEGER,
  aperture_f        REAL,
  shutter_speed_s   REAL,
  focal_length_mm   REAL,
  flash_fired       INTEGER,
  lens_model        TEXT,
  camera_id         TEXT,
  pick_state        TEXT,          -- latest phase_state.state (was cull_state)
  -- from event.extras_json:
  country           TEXT,
  country_code      TEXT,
  -- from trip_day.extras_json:
  day_city          TEXT,
  day_sublocation   TEXT,
  -- from item.extras_json (synced at save):
  stars             INTEGER,
  color_label       TEXT,
  flag              INTEGER,       -- portfolio flag (was pick)
  PRIMARY KEY (event_uuid, item_id)
);
```

Sync trigger: on event close / on app startup. Each sync opens the event.db, queries
the projection, and upserts into `global_items`.

### Tier 2 — per-event drill-down

After `global_items` returns a result set, a drill-down into the specific event.db
fetches the full item detail (EXIF overlay, adjustments, phase history, etc.).

---

## 4. Saved filters (smart collections)

A `saved_filter` table in `app.db`:

```sql
CREATE TABLE saved_filter (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  description TEXT,
  query_json  TEXT NOT NULL,  -- serialized predicate tree (AND/OR)
  sort_json   TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
```

The predicate tree is a JSON structure:
```json
{
  "op": "AND",
  "children": [
    {"field": "classification", "op": "eq", "value": "macro"},
    {"field": "stars",          "op": "gte", "value": 4},
    {"field": "iso",            "op": "lte", "value": 800},
    {"field": "country_code",   "op": "in",  "value": ["NP","IT","NZ"]}
  ]
}
```

Pre-shipped presets (examples):
- "High-ISO shots" — `iso >= 1600`
- "Long exposures" — `shutter_speed_s >= 0.5`
- "Wide-open glass" — `aperture_f <= 2.0`
- "Macro picks" — `classification = 'macro' AND pick_state = 'picked'`
- "5-star flags" — `stars = 5 AND flag = 1`
- "Flash portraits" — `flash_fired = 1 AND classification = 'portrait'`

---

## 5. Batch operations on dynamic collections

Once a collection is resolved to a set of `(event_uuid, item_id, origin_relpath)`:

| Operation | How |
|---|---|
| Generate hardlinked directory | iterate → `os.link(event_root/relpath, dest/filename)` |
| Export to PTE bundle | same + copy caption from `extras_json.caption` |
| Set rating in bulk | `UPDATE item SET extras_json = json_set(extras_json, '$.stars', N)` per event |
| Generate slideshow | ordered by `capture_time`, output JSON for PTE AV Studio |
| Face recognition run | batch over resolved file paths → insert `face_set` records |

---

## 6. Build order

1. ✅ **Schema foundation** — EXIF columns on `item`; `extras_json` on `event` + `trip_day`;
   partial indexes; existing databases backfilled (2026-06-02).
2. **Pick phase** — star rating + color label UI (keyboard 1–5 in the Picker);
   `set_stars` / `set_color_label` gateway mutators.
3. **Location entry** — plan editor UI for `event.extras_json` location fields
   (City, Country, CountryCode, Region); day-level override in ManageDaysDialog.
4. **`app.db` + `global_items`** — central index + sync job.
5. **Exploration UI** — facet panel + result grid + slideshow launcher.
6. **Saved filters** — builder UI + preset library.
7. **Batch jobs** — hardlink export, rating bulk-set, PTE bundle generator.
8. **Facial recognition** (future) — local DNN batch, `face_set` table, person facet.
9. **Tags** (future) — hierarchical `item_tag` table, tag browser.

---

## 8. Website / distribution pitch (Nelson 2026-06-02)

Dynamic Collections are the **centrepiece demo for the Mira website** — the single
most compelling argument for importing all your past and future events into the system.

The pitch (free product, serious photographer audience):

> "15 years of photos. Every trip, every camera, every moment.
> Ask anything — get an answer in seconds."
> *Show: query running live, hardlinked slideshow folder appearing.*

Why it works as a teaser:
- The **return on investment is proportional to the archive size** — the more events
  you import, the more powerful the queries. This creates a natural pull to migrate
  everything, not just new events.
- It demonstrates something **no other free tool does**: cross-event, multi-dimensional,
  offline-first, SQL-powered search over a lifetime archive.
- The examples are emotionally resonant: "best photos of my son Pedro from birth to
  age 30" is not a features list — it's a promise.
- The demo can be **interactive on the website**: type a query template, see a simulated
  result set with real photo metadata, feel the potential before installing.

Suggested demo queries for the website:
- "All 5-star macro photos of insects with focus stacking, any year"
- "Best shots from Nepal — wide-open glass, no flash, rated ≥ 4"
- "Family portraits, 2010–2025, international trips, with flash"
- "Long exposures (≥ 1s) at ISO ≤ 400 — any landscape, any camera"

The implicit message: **Mira is the system that makes your archive worth having.**
Every photo you ever took becomes findable, collectable, shareable — without a
subscription, without a cloud, without giving anyone your data.

---

## 7. Design principles

- **Offline-first always** — no cloud, no ML unless local model.
- **Query, don't label** — EXIF facets are free (no user effort); user effort goes into
  rating + location + caption, not into duplicating what the camera already wrote.
- **Cross-event is the point** — the single-event view is a drill-down, not the goal.
- **Saved filters are assets** — a well-tuned filter is as valuable as an album;
  export/import/share as JSON.
- **Lazy sync** — `global_items` syncs on demand; the per-event databases are always
  the source of truth. No orphan risk.
