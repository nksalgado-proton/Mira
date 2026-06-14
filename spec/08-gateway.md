# spec/08 — The gateway (the hard interface)

**Build-sequence step 6 (charter §4).** The single query/mutator facade that is the
**only** way any UI code touches data. The UI never knows a database exists; it asks
the gateway for what it renders and tells the gateway what the user did. This spec
enumerates that facade **from final use** (charter §5.1) — the queries every surface
reads + the mutators every action produces — and records what is built now vs.
enumerated for the downstream UI-reassembly sessions that will fill it in.

Sits on the foundations already built: `mira/store/` (G1, the per-event SQLite
`EventStore` + `EventDocument` round-trip), `mira/settings/` (G2, Domain 5),
`mira/paths.py`, `mira/protect.py`.

---

## 1. Shape — an umbrella over per-domain repositories (spec/02 §1)

```
Gateway                       ← the one object the UI holds
├── settings: SettingsRepo    Domain 5 (built, G2)
├── index:    EventsIndex     cross-event events list + path anchoring (charter §5.9)
└── open_event(id) -> EventGateway   one open event.db, the per-event facade
        (knowledge / rules / tone-corpus repos plug in here later — spec/02 D2/3/4)
```

Two tiers of access:

- **Cross-event** (no event open): the events list, settings, the `photos_base_path`
  anchor, materialising an `event.db` from `event.json` (the migration/restore batch).
  Lives on `Gateway` + `EventsIndex`.
- **Per-event** (one `event.db` open): everything that renders or mutates one event —
  the item tree, phase state, buckets, moments, adjustments, curate, lineage. Lives on
  `EventGateway`, which wraps an `EventStore` and **is the only place that opens one**.

`EventGateway` is a context manager (opens the DB, closes on exit). The UI gets one per
event-editing session and never sees `sqlite3` or `EventStore`.

## 2. Charter invariants this layer enforces

- **One-way dependency.** UI → gateway → store. Nothing here imports from `ui/`.
- **Phase progress is a QUERY** over `phase_state`, never a stored cache (spec/03 D, §3.5).
- **Relative paths from the base** (charter §5.9). `EventsIndex` resolves
  `event_relpath` against `photos_base_path`; `event_root_abs` is the cross-volume
  fallback. In-event paths stay relative to the resolved `event_root`. The gateway
  exposes a resolved absolute `event_root` to callers; nothing persists absolutes
  except the flagged fallback.
- **Atomic + protected writes** for the JSON domains (`EventsIndex`, settings) via
  `mira/protect.py`. SQLite writes go through `EventStore.transaction()`.
- **Tolerant reads, model-honest writes** (charter §5.3): never throw on a legitimate
  empty/missing state; when a crash reveals a *missing concept*, fix the model
  (spec/03), don't paper the facade. Every guard answers: invariant or model-gap?

---

## 3. Cross-event facade — `EventsIndex` + `Gateway`

### 3.1 `EventsIndex` — `<user_data_dir>/events_index.json` (spec/03 §5)

The thin pointer so the events list renders without opening every `event.db`. One row
per event; the file carries `schema_version` + a mirror of `photos_base_path`. Under
the §1 protection contract.

| operation | kind | meaning |
|---|---|---|
| `load()` | query | parse the index (tolerant: missing file → empty; corrupt → backup + empty). |
| `entries()` | query | the raw rows (id, name, dates, is_closed, event_relpath, event_root_abs). |
| `list_events(base)` | query | rows with `event_root` **resolved** (`base + relpath`, or the abs fallback). The events-list render. |
| `resolve_root(entry, base)` | query | one row's absolute `event_root` (fallback-aware). |
| `upsert(entry)` | mutator | add or replace a row by id; protected write. |
| `remove(event_id)` | mutator | drop a row; protected write. |
| `set_base(path)` | mutator | rewrite the mirrored `photos_base_path` (relocate = one edit). |

`entry` is the M1 `index_entry()` shape (`mira/migrate/extract.py`) — the
extractor already emits one row; the index *collects* them. **`make_entry(...)`** (the
re-anchoring rule: relpath when under base, abs fallback when cross-volume) is shared
with M1 so there is one re-anchoring implementation.

### 3.2 `Gateway` — the umbrella

| operation | kind | meaning |
|---|---|---|
| `list_events()` | query | resolved events list (uses settings' `photos_base_path` as the anchor). |
| `photos_base_path()` | query | the single absolute anchor (from settings; index mirrors it). |
| `set_photos_base_path(p)` | mutator | write to settings **and** the index mirror together. |
| `open_event(event_id)` | query | resolve the root, open the `event.db`, return an `EventGateway`. |
| `materialise_event(event_json, entry)` | mutator | create `event.db` from a backup/migration dump (shared restore+migration path, charter §4 step 5), `upsert` the index row. The charter §4 step 4–5 batch runs this per extracted event. |

`create_event(...)` (a brand-new empty event from the plan wizard) is **enumerated,
built at the ingest surface** — it composes `materialise_event` with an empty document.

---

## 4. Per-event facade — `EventGateway`

Wraps one open `EventStore`. `event_root` (resolved absolute) is available for callers
that must touch the projected tree; **all stored paths remain relative to it**.

### 4.1 Queries (what surfaces read to render)

**Event-level**
- `event()` → the `Event` row · `trip_days()` · `cameras()` · `participants()` ·
  `checklist()` · `distribution()` — the plan/dashboard/header renders.

**Item spine** (the cull/select/process/curate item lists)
- `item(item_id)` → one `Item`.
- `items(*, phase=None, state=None, day=None, kind=None, camera_id=None, bucket_key=None)`
  → filtered, time-ordered `Item` list. The one query behind every phase's item list;
  `phase`+`state` join `phase_state`, the rest filter `item` columns.
- `children(item_id)` → snapshot/clip items produced from a source video (`parent_item_id`).
- `day_tree()` → day → counts (photos/videos), the navigator's durable day axis (the
  bucket layer is recomputed on top by the scanner at browse time — see Buckets below).

**Phase state & progress** (always a query, never a cache)
- `phase_state(item_id, phase)` → the mark (or the bucket default if no row).
- `phase_progress(phase)` → `{counts: {state: n}, total, reviewed_buckets, dirty}`
  — the heatmap/funnel/dashboard summary. Composed from `phase_counts` + `bucket`.

**Buckets** — *transient grouping, by design.* A bucket is a browsing convenience the
scanner **recomputes** each time; item→bucket membership is **never persisted**. The
store owns only each bucket's **soft-state**, keyed by `bucket_key`, so "reviewed" +
resume cursor survive a re-scan. The gateway never owns the grouping — it answers
soft-state lookups when the Cull surface (which owns the clustering) hands it a key.
- `buckets(phase)` · `bucket(bucket_key, phase)` — soft state (reviewed/browsed/
  current_index/nudge_dismissed/default_state) for resume + the reviewed pills.
- `day_tree()` groups by the stored `day_number` (the durable timeline axis), not by
  bucket; the bucket layer is layered on top at browse time by the scanner.

**Video moments**
- `moments(source_item_id)` · `kept_moments(...)` — clips/snapshots as first-class rows.
- `moment(source_item_id, moment_id)` · `video_override(source_item_id, moment_id)`.

**Process**
- `adjustment(item_id)` → per-item photo edits (tone/crop/rotation/auto).

**Curate / Distribute**
- `curate_tag(item_id)` · `curate_tags()` — tier/theme/solo/is_discarded.
- `subsets()` · `subset_members(subset_id)` · `resolve_subset(subset_id)`
  (base ∩ genre_filter − excluded, resolved on demand — census §H) · `trip_budget()`.
- `stacks()` · `stack_members(bracket_id)`.
- `lineage()` — export traceability (replaces stem-matching).

### 4.2 Mutators (what user actions produce)

Every mutator runs in an `EventStore.transaction()` and stamps the event's
`updated_at`. Timestamps come from an injected `now()` (deterministic in tests).

**Phase decisions**
- `set_phase_state(item_id, phase, state)` — the K/D/Candidate mark; stamps `decided_at`,
  clears `derived_dirty`.
- `commit_phase(phase)` — stamp `committed_at` on the phase's decided rows (phase-exit).
- `mark_derived_dirty(phase, item_ids)` — **the re-entry fix** (spec/03 D, fixes S1/S2):
  an upstream change flags downstream marks stale instead of silently going wrong.

**Buckets**
- `set_bucket_reviewed(key, phase, value)` · `set_bucket_browsed(...)` ·
  `set_bucket_current_index(...)` · `dismiss_nudge(key, phase)` ·
  `set_bucket_default_state(...)`. (`reviewed` is user-declared, never inferred.)

**Classification** (FS→own — never folder names again)
- `set_classification(item_id, value, source)` — `source='user'` overrides; the
  auto-classifier writes `source='auto'` + `rules_version`.

**Process**
- `save_adjustment(adjustment)` · `set_process_exported(item_id, bool)`.

**Video**
- `create_moment(...)` / `update_moment(...)` / `set_moment_state(...)` /
  `delete_moment(...)` (clip trim / snapshot / per-moment K/D) ·
  `save_video_override(override)` · `set_moment_produced(... , produced_item_id)`
  (the materialise link, D3 — Process owns byte materialisation).

**Curate / Distribute**
- `set_curate_tag(tag)` · `save_subset(subset)` / `set_subset_excluded(...)` ·
  `save_trip_budget(budget)` · `record_distribution(action)`.

**Stacks / lineage / event**
- `save_stack(bracket, members)` · `set_stack_action(...)`.
- `record_lineage(entry)` / `clear_lineage(phase)` (rebuilt on re-export).
- `set_closed(bool)` — the Open/Closed bit (the only lifecycle bit, D6).
- `save_item(item)` / `add_items(items)` — ingest populates the spine.

### 4.3 Item creation at materialise (D3)
Snapshot/clip items the Process phase produces are real `item` rows with
`provenance` + `parent_item_id`; `set_moment_produced` links the moment to its produced
item. The gateway does not itself render frames — it records the produced item the
Process engine materialises.

---

## 5. Built now vs. enumerated (charter: the gateway grows as surfaces reassemble)

**Built + tested this session (the foundation the first surfaces need):**
- `EventsIndex` — full (load/entries/list_events/resolve_root/upsert/remove/set_base,
  shared `make_entry` re-anchoring).
- `Gateway` — list_events, photos_base_path get/set, open_event, materialise_event.
- `EventGateway` — all §4.1 read queries; the load-bearing mutators: `set_phase_state`,
  `commit_phase`, `mark_derived_dirty`, the bucket mutators, `set_classification`,
  `save_adjustment`, `set_closed`, `record_distribution`, `save_item`/`add_items`,
  `set_curate_tag`, the moment + override + subset + stack + lineage mutators.

These are thin wrappers over the store's generic CRUD, so building the set whole is
cheap and locks the interface. They are **not yet exercised by a UI** — the parity test
against the oracle (old vs new on a real event) lands with the **first UI surface
reassembly** (charter §4 step 7), not here.

**Enumerated, built at the surface that needs it:** `create_event` (ingest plan),
per-bucket aspect defaults, the curate map slides (deferred, spec/03 §4 note), and any
query a reassembled surface turns out to need that isn't above — *added to the gateway
when the surface needs it* (charter §2), fixing the model if it's a model gap (§5.3).

## 6. Gate (this session)

`tests/test_gateway.py`, logic-only:
- `EventsIndex` round-trips through `protect`; relpath vs abs-fallback resolution;
  tolerant load (missing → empty, corrupt → backup + empty); upsert/remove.
- `Gateway.materialise_event` over the `test_store` `_rich_document` JSON → an
  `event.db` that `load_document()`-equals the source, and an index row resolvable back
  to its root.
- `EventGateway` queries over a materialised rich event: filtered `items(...)`,
  `phase_progress`, `day_tree`, `moments`, `resolve_subset`.
- `EventGateway` mutators: `set_phase_state` stamps + clears dirty; `mark_derived_dirty`
  flags; bucket reviewed/browsed; `set_classification` user-override; `set_closed`
  reflects in the index on the next `materialise`/`upsert`.
- Cross-check the M1–M4 migration fixtures flow through `materialise_event` (restore ==
  migration, one reader — charter §4 steps 2–5).

## 7. What this drives next

After the gateway: **reassemble the UI downstream from ingest** (charter §4 step 7).
Each surface takes the legacy UI parts, severs their data tendrils (they call journal
modules directly today — charter §5.2), binds them to **this gateway only**, and ships
with a parity test against the oracle. The gateway grows one method at a time as each
surface is wired; the discipline (§5.3: model gap vs. invariant) governs every addition.
