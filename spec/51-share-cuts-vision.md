# spec/51 — Share redesign vision (Cuts)

> **⚠ SUPERSEDED for the event-Cut model + surfaces by
> [spec/61-share-event-cuts.md](61-share-event-cuts.md) (design locked
> 2026-06-11).** This doc remains the record of the 2026-06-08 brainstorm.
> Where the two disagree — built-in #exported vs zero-Cuts, typed names vs
> internal tags, Picker session vs one-by-one walk, pool algebra vs seed
> filter, file-membership vs item-membership, generated separators vs
> authored maps/collages — **spec/61 governs.** spec/61 §7 carries the full
> delta table.

**Status:** vision approved 2026-06-08, Nelson. **§6 open questions all closed
2026-06-08** (same session) — see §6 for resolutions and §10 for the storage
architecture that emerged. Implementation slicing can proceed against this
document. When the slicing manifest lands it will explicitly supersede
spec/49 (the verbatim port plan) for the surfaces in scope here.

This doc supersedes nothing on its own except the spec/49 port plan
(implicitly). It's the durable record of a brainstorm + closure, so the
design doesn't have to be rebuilt from memory next time.

---

## 0. Why redesign

The legacy Curate surface (today's Share, parked per spec/48 §2 #6) is
functional but jumps the user straight to a results-style view (Overview,
pass cards, Collections) with no clear "what do I do here?" path. The
classification activity — the pass-walking — is buried below Overview and
reads like another results button. Nelson on first run: *"I am not sure
anymore [this surface] is the best starting point ... we have now combined
classify+curate into share, but we are missing the classify activity."*

The redesign brings the activity back to the front and unifies what's
today three surfaces (passes navigator + subset editor + Collections page)
into one creation flow. It also restructures the artifact model around
tags so cross-event compositions and per-event compositions share the
same machinery.

---

## 1. The north-star principle

**A Cut is NOT a final slideshow.** It's a time-budgeted, ordered set of
items the user assembles in mira and hands off to an external
slideshow tool (PTE AV Studio etc.) for finishing. Mira's job is to
deliver a high-quality starting point — not to render or render-prepare
the show itself.

Two corollaries:

1. **Order is chronological only**; if the user wants to reorder, that
   happens in PTE.
2. **No render UX in mira** — no transitions, no music synchronization,
   no per-slide effects. Those belong in PTE.

This frame should govern every design decision in Share.

---

## 2. Vocabulary

The artifact is a **Cut** (locked 2026-06-08). Fits the Collect / Pick /
Edit / Share verb cadence without photographer jargon.

| Concept | Term |
|---|---|
| The artifact | **Cut** |
| Plural | **Cuts** |
| The list surface | **Cuts** (replaces "Collections") |
| Creating one | **New Cut** dialog |
| Walking-and-tagging | *Pick into the Cut* / *Skip from the Cut* |
| Saved profile | **Cut template** |
| Cross-event flavor | **Cross-event Cut** |
| Folder (if materialized) | `Cuts/<cut name>/` *(was `04 - Cuts/`; numbered prefixes retired by [spec/57](57-folders-and-roundtrip.md) — Cuts is a fixed-English top-level event dir)* |

The legacy terms **Compilation**, **Collection**, **Subset**, **Curate**
retire from user-facing vocabulary. The `04 - Curated/` folder retires too.
"Pass" retires as a system concept (see §3.2).

---

## 3. Locked decisions

### 3.1 A Cut IS a tag (one concept, not two)

- A tag in the DB *is* a Cut's identity. Items carrying the tag are the
  Cut's members.
- The "first pass" / per-item "all-time-best property" / "All-Time Best
  compilation" are not three things — they are one thing: items tagged
  `#all-time-best` are the All-Time Best Cut.
- Tags become **internal** — workflow-derived, not user-typed. The user
  never types a tag name. They see a Cut's display name and the system
  manages the underlying tag id.

### 3.2 All Cuts are user-created — no system-framed first pass

- Open Share on a fresh event → **zero Cuts exist.** No system-defined
  "first pass" the user must do.
- The user creates Cuts on demand, including All-Time Best.
- The "All-Time Best Cut" is just one of many templates — equal status
  with Short, Long, user-saved templates.
- The verb "pass" retires as a system concept. What we used to call
  "passes" are just **Cut walks**.

### 3.3 Same machinery for in-event, cross-event, and All-Time Best

The New Cut dialog → walk → tag flow is **the only flow.** Three personas
collapse into one:

- *All-Time Best on a fresh event* = New Cut dialog with the All-Time Best
  template (no time limit, scope=this event, empty seed).
- *Wedding Highlights* = New Cut dialog with a custom or cloned template
  (target=10min, video_share=40%, scope=this event, seed=`#all-time-best`).
- *Birds of 2026* = New Cut dialog with scope=multiple events,
  filter=genre=wildlife.

Same dialog, same walk surface, same persistence, same export. The only
difference is which dialog fields the user fills in.

### 3.4 The New Cut dialog — the single composition point

The user enters everything about the Cut up front, then walks. Dialog
fields:

- **Template / name** — pick a template (All-Time Best, Short, Long, user-
  saved) or start blank. Name defaults from template, user can override.
- **Target time + max time + slide duration** — with live keep-rate
  guidance ("starting time = N × d", "keep ~1 in K"). Math lives in
  `core/curate_budget.py` (the legacy Curate budget helpers travel
  unchanged under the new vocabulary).
- **Videos toggle** — Allow / Don't allow. Default per template.
- **Seed filter** — "Start by including everything in [another Cut]"
  (subtractive walk; see §3.5).
- **Auto-exclusion** — items already in the Cut being built are never
  shown twice in the walk. Implicit, not a user-facing toggle.
- **Events filter** — `[this event]` or `[any group of events]`. ← the
  cross-event lever.
- **Genre filter** — Macro / Wildlife / etc. (from `item.classification`).
- **(Future) People filter** — face recognition, see §5.

### 3.5 The walk surface

- Items shown one-by-one, filtered per the dialog's predicates.
- Verbs: **Pick** (in this Cut) / **Skip** (not in this Cut).
- Skip is local to this Cut — it does NOT propagate to other Cuts or
  affect the item in any way outside this Cut. The legacy per-event
  Discard does not exist.
- **Seed-filter items start PRE-PICKED.** When the user seeds from
  `#all-time-best`, those items are already in the Cut when the walk
  begins; the user can Skip any that don't earn their slot. Subtractive
  walk, same mental model as the legacy subset editor.
- **Live time-left counter** — three zones:
  - green — at/under target
  - amber — between target and max
  - red — over max
  The counter shows real video time + photo slide-time against the Cut's
  target/max. `video_share` is planning-only, not enforced.
- Ctrl+Z undo, same as today.

### 3.6 Closed events

- Cuts are a **separate layer** from event content.
- A cross-event Cut that pulls from a closed event does NOT count as
  modifying that event. The closed-event "no mutations" invariant holds.
- Conversely, deleting an event whose items are in a Cut: see open
  question H.

### 3.7 Video in Cuts

- Videos are allowed in any Cut. The user decides per-Cut via the
  videos toggle in the dialog. Saved per template:
  - `#short` template default: videos OFF
  - `#long` template default: videos ON
  - `#all-time-best` template default: TBD (probably ON — no time limit
    so video duration doesn't constrain)
- A clip's duration counts against the time-left counter; photos count
  as slide-duration.

### 3.8 Templates

- Pre-shipped: `#all-time-best`, `#short`, `#long`.
- User-savable: from the dialog, after configuring fields, "Save as
  template…" stores the profile for reuse.
- Storage location: **open question C** — leaning per-user
  (`settings.rebuild.json`); schema table is the alternative.

### 3.9 The Cuts list (replaces Collections)

When the user enters Share for an event (or the cross-event entry):

- Header: **Cuts** + "New Cut →" action.
- List of existing Cuts in scope (per-event or cross-event depending on
  entry point).
- Per row: name · item count · duration · last-touched · export status.
- Row click: open the Cut. From there: browse · continue walking · edit
  the Cut's settings · export.

### 3.10 Export

- Separate verb from creation.
- "Export Cut →" materializes the Cut as a folder of hardlinks (no byte
  copies for the photos themselves; the originals stay in `00 - Captured/`).
- Per-event Cut destination: `<event_root>/04 - Cuts/<cut name>/`.
- Cross-event Cut destination: user-chosen folder (no single event
  root applies).
- Export is a snapshot in time; the Cut stays live and editable.

### 3.11 Audio at export — mood-based selection

A **bonus feature** Nelson called out as historically painful in slideshow
prep:

- **No bundled library.** User-owned audio only.
- Layout (proposed, simple): `<audio_root>/<mood>/<file>.mp3` — one mood
  per directory, files inside.
- New setting: `audio_library_path` in `settings.rebuild.json`. Empty
  setting or missing path → the export's audio feature is unavailable
  (graceful skip).
- At export time, the user picks a mood (e.g. happy, reflective,
  energetic). The system selects N files from that mood subdir to cover
  the Cut's projected duration.
- The matching algorithm (which files, in what order, how close to the
  duration target) is its own sub-design — see §6 open list.

### 3.12 Maps + collages — sidebar features (per event)

- These are *authored artefacts* the user creates **for an event** — one
  map slide; one collage.
- They live alongside photos/videos as items but are user-authored
  (not captured).
- Per-Cut inclusion: the dialog or walk lets the user opt these in or
  out per Cut.
- Authoring entry point: **open question F** — inside Share or a
  separate per-event authoring surface.

### 3.13 People filter (future, behind research)

- A filter on the New Cut dialog: "Include only Cuts of [these people]".
- Seeded by the user uploading 1–N sample photos per person of interest.
- Cross-event scope multiplies the value: "Cuts where my son appears,
  across the last 5 years."
- Research required before committing to an implementation — see open
  question G.

---

## 4. The user's mental walk-through (illustrative)

Per-event, fresh event:

1. Opens event → clicks Share tile.
2. Sees: "Cuts" header, "New Cut →" button, empty list, hint
   ("Try All-Time Best to start").
3. Clicks "New Cut" → dialog opens.
4. Template picker: chooses "All-Time Best".
5. Dialog auto-fills: name "All-Time Best", no time limit, videos ON, no
   seed, scope=this event, no genre.
6. Clicks Start → walk begins.
7. For each item: Pick (it's in) or Skip (not). Repeats.
8. Done → Cut appears in the Cuts list.
9. Later, creates a "Short" Cut: New Cut → Short template →
   seed-filter = "All-Time Best" (so the highlights are auto-in) →
   target 3 min, slide 4s, videos OFF.
10. Walks the remaining items (auto-excluding the all-time-best ones
    that are already picked). Skips most.
11. Exports → picks a mood (happy) → folder lands on disk with hardlinked
    photos + audio files.

Cross-event:

1. From the events view, clicks "Cross-event Cut".
2. New Cut dialog opens with scope picker prominent.
3. Picks 5 events (e.g. all 2026 trips).
4. Filter: genre=wildlife, people=[son's sample photo].
5. Walks → exports → handed to PTE.

---

## 5. What's explicitly out of scope

- **Reordering inside mira.** Order is chronological; user reorders
  in PTE.
- **Final slideshow rendering.** PTE renders.
- **Transitions / effects / per-slide settings.** PTE handles.
- **Video editing in Cuts.** Use Edit phase for that; the clip is
  already final by the time it reaches Share.
- **User-typed event tags.** Retired. All tags are workflow-derived.
- **Auto-creation of cross-event Cuts by the system.** The system never
  creates Cuts on its own — the user always initiates.
- **Implementation slicing.** A separate manifest (spec/52? TBD) will
  define the port-vs-rewrite breakdown.

---

## 6. Open questions — ALL CLOSED 2026-06-08

### B. Cross-event entry point location — CLOSED

**Resolution:** New top-level **"Cuts"** menu on the menu bar (sibling to File,
Events, Plan, Pick, Edit, Share). Two entries:

- *New cross-event Cut…* → opens the New Cut dialog with `scope=multi` pre-set.
- *Cross-event Cuts…* → opens the Cuts list filtered to cross-event Cuts.

Per-event Cuts continue via the in-event Share tile — no change there.

Why menu bar: matches [[feedback_maximize_canvas_space]] (sidebar/dashboard
chrome retired in favour of menu entries); always reachable from every page.

(There's a separate pending design session on the menu bar's first-level
structure + dynamic context-aware option population. Flagged 2026-06-08.)

### C. Template storage — CLOSED

**Resolution:** **In `mira.db`** (the user-level data store, [spec/53](53-user-data-store.md)),
in a dedicated `cut_template` table.

- **Pre-shipped templates** (`#all-time-best`, `#short`, `#long`) are Python
  constants in `core/cut_templates.py`, NOT rows. Stable across versions,
  owned by the app code, can't be accidentally deleted.
- **User-saved templates** are rows in `cut_template`. Created via the New Cut
  dialog's "Save as template…" affordance.

Why this over per-user `settings.rebuild.json` (the original lean): templates
are user-saved data, not app config; separating them keeps `setting`-like
state focused on app preferences. The user-level DB already exists for other
reasons (Cuts themselves, people catalog, feature flags); one more table
costs nothing.

### D. Cut rename — when allowed — CLOSED

**Resolution:** **Always renameable. No lifecycle restriction.**

- `cut.id` (UUID) is stable internal identity; `cut.name` is mutable display
  metadata. Identity vs label distinction.
- Export is a snapshot in time (§3.10); the on-disk export folder
  (`04 - Cuts/<name>/`) was named at export-time and stays named that way.
  Renaming the cut later doesn't (and shouldn't) touch already-exported
  folders. Subsequent exports use the new name → fresh folder.
- No unique constraint on name. Two Cuts can share a name; distinguishable by
  id internally. Export-time folder collision handled with a disambiguator
  suffix (`(2)`, timestamp). Implementation detail.

UI consequence: the Cut detail surface always shows an editable name field. No
special "rename mode" or unlock affordance.

### F. Maps + collages authoring entry point — CLOSED

**Resolution:** **Separate per-event authoring page** reached from the event
dashboard (NOT inside Share, NOT inside a Cut walk).

Schema model: maps + collages are **items with `provenance='authored'`**. Their
config lives in `item.extras_json`:

```
authored items:
  map     → {"day_number":N, "center":[lat,lon], "zoom":N, "markers":[...], "rendered_at":...}
  collage → {"source_item_ids":[...], "layout":"grid|mosaic|...", "rendered_at":...}
```

Rendered bytes land at `<event_root>/_authored/<item_id>.{jpg,png}` — a sibling
to `00 - Captured/`. Hardlinks into Cut export folders work identically to
captured items.

Per-Cut inclusion happens via tagging (`photo_tag`) — same mechanism as any
other item.

Why this and not "inside Share" or "from a Cut walk":

- Authoring is its own concept, separable from Cut creation. Build a map once,
  use it in many Cuts via tagging — same mechanism as a photo.
- Cut walks stay pure (Pick/Skip only — no authoring tools clutter).
- Standalone authoring works (no Cut required, no Cut intended).

The old `share_map` table (separately keyed JSON-blob for maps) **retires** —
maps as items + extras_json replace it.

### G. People filter v1 ambition — CLOSED

**Resolution:** **Simplest tier** — user uploads N sample photos per person to
the catalog; system runs face match per item at filter time only.

- Schema already supports all tiers (`photo_person.source ∈ {'user','auto'}` +
  `confidence`). No schema regret if v2 adds clustering or continuous
  indexing.
- User opts in: face matching runs only when the user applies the People filter
  on a New Cut dialog. No background processing eating CPU on normal ingest.
- Smallest scope to ship in v1.

People catalog itself lives in `mira.db.person` (one row per person, one
reference photo embedded for similarity comparison at filter time). See
[spec/53 §2.5](53-user-data-store.md).

Out of v1: background face indexing, auto-suggested clusters, multi-face-per-
photo refinement, an Apple-Photos-style "People" browse tab.

### H. Cut-with-deleted-source-event — CLOSED

**Resolution:** **Graceful shrink with a soft visual indicator.**

The storage architecture (§10) makes this natural — `photo_tag` rows live in
each `event.db`. Deleting an event deletes its `photo_tag` rows; the Cut's
computed membership shrinks.

- Cut's `scope_event_uuids_json` keeps the deleted uuid (frozen record of
  intent).
- When computing membership, missing scope uuids are skipped silently.
- The Cut detail surface shows a small line: *"2 of 3 events available — Costa
  Rica 2024 was deleted"* — so the user knows membership shrank if they
  wonder.

No new schema needed. Behaviour falls out of the per-event `photo_tag` design
+ scope list being a record of intent rather than enforced FKs.

### I. Empty-state guidance — CLOSED

**Resolution:** **Sentence hint, context-aware copy, no onboarding modal.**

Concrete copy (iterate later):

- **Per-event Cuts**, empty: *"No Cuts yet. Try **All-Time Best** to start —
  your favourites from this event with no time constraint."*
- **Cross-event Cuts**, empty: *"No cross-event Cuts yet. New cross-event Cut
  lets you combine items across multiple events — try filtering by genre or
  people."*

Hint disappears as soon as ≥1 Cut exists. Button is always there.

No modal, no tutorial overlay, no first-launch checklist.

### Audio matching algorithm — CLOSED

**Resolution:** **Random pick to cover the Cut's projected duration. Not clever.**

1. List audio files in the mood subdir.
2. Read each file's duration via `mutagen` (already a dep — fast, no decode).
3. Shuffle randomly.
4. Sum lengths until total ≥ Cut's projected duration.
5. **Include the file that crosses the threshold** (so the user has trim room
   in PTE).
6. Copy chosen files into the export folder, prefixed `01_`, `02_`, etc., for
   play order.

Aligned with §1 north-star: *"MC's job is to deliver a high-quality starting
point — not to render or render-prepare the show itself."* Trim/fade of the
final track is exactly what PTE does well.

Edge cases:

- **Mood subdir empty**: export proceeds without audio; small inline notice.
- **Total subdir audio < Cut duration**: copy all of it; notice that user
  should add more music or use a different mood.

Out of scope: BPM matching to scene type, fade-in/out at file boundaries (PTE
does this), energy-curve re-ordering, deduplication across mood subdirs.

---

## 10. Storage architecture (closed 2026-06-08)

A Cut has two things: (1) its **definition** — name, target/max time, slide
duration, videos toggle, seed tag, scope, filters. (2) its **membership** —
which items are in it.

The split:

- **Cut definitions live in `mira.db.cut`** (the user-level data store
  per [spec/53](53-user-data-store.md)). Same table holds per-event AND
  cross-event Cuts; `scope_kind ∈ {'single','multi'}` + `scope_event_uuids_json`
  tells them apart.
- **Membership lives in `event.db.photo_tag(item_id, tag, tagged_at)`** — the
  M:N replacement for the legacy `share_tag` table per [spec/30 cleanup](30-relational-schema-redesign.md)
  + [spec/52 §11](52-event-creation-vision.md). `photo_tag.tag` references
  `cut.id`.

How operations work at runtime:

| Operation | Mechanism |
|---|---|
| Open a Cut | Read row from `mira.db.cut` → for each `event_uuid` in `scope_event_uuids_json`, open that event.db → `SELECT item_id FROM photo_tag WHERE tag = <cut.id>` → union results = membership. |
| Walk a Cut (Pick) | Insert `photo_tag(item_id, tag=cut.id, tagged_at=now)` in the relevant `event.db`. |
| Walk a Cut (Skip) | No row written. Skip is local to the cut (§3.5) — no global state. |
| Cross-event Cut | Same as per-event but the union spans N `event.db` files. |
| Deleted event | Its `event.db` is gone → its `photo_tag` rows go with it → Cut's computed membership shrinks (graceful, §6 H). |
| Export | Read membership union → hardlink each item's `origin_relpath` into `<event_root>/04 - Cuts/<cut name>/` (per-event) or `<user-chosen folder>/<cut name>/` (cross-event). |

**Why split this way:**

- **One home for Cut definitions** = no per-event-vs-cross-event branching in
  the data layer.
- **Templates in `mira.db.cut_template`, not `setting`** = templates are
  user-saved DATA, not app config; separating keeps settings focused on
  preferences.
- **Pre-shipped templates as code constants** = versioned with the app, can't be
  accidentally deleted.
- **Membership stays in `event.db`** = item ids are scoped per event;
  cascade-delete on item deletion is already there via `ON DELETE CASCADE`;
  no cross-db FK problems.
- **`event.db.photo_tag` is M:N** = one photo can be in many Cuts. The legacy
  `share_tag` 1:1 model couldn't express this.

**Why one `mira.db` instead of a dedicated `cuts.db`:**

The same architecture decision in [spec/53](53-user-data-store.md) — Cuts are
one concern of several at the user level (settings, wizard answers, events
index, people catalog, hardware, feature flags). One file, one ACID boundary,
one backup story.

---

## 7. Related docs + code

- **[spec/48-four-phase-pivot](48-four-phase-pivot.md)** — the 4-phase pivot
  (parks Share for separate revision — this doc IS that revision).
- **[spec/52-event-creation-vision](52-event-creation-vision.md)** — the
  event-creation redesign that retires tags / people / long-observation at the
  event level, freeing the surface room this design needs.
- **[spec/53-user-data-store](53-user-data-store.md)** — the user-level
  `mira.db` that holds `cut`, `cut_template`, `person` (the People
  catalog feeds the §3.13 filter), and the feature-flag scaffold.
- **[spec/30-relational-schema-redesign](30-relational-schema-redesign.md)** —
  the per-event schema; `event.db.photo_tag` (membership), `photo_person`
  (people link), and `item.provenance='authored'` (maps + collages) all live
  there.
- **`core/curate_budget.py`** — the keep-rate / time-budget / collection-
  stats helpers reuse cleanly under the new vocabulary.
- Historical references: the legacy Curate spec + checklist
  (`docs/27` + `docs/28` in Mira, dropped here) contain the dialog
  precedents and the math derivations. Consult them in the ancestor
  repo if their derivation rationale is ever needed.
- **`mira/ui/shared/`** (post Slice A) — the navigator + review
  page + overview page in this folder are salvage material. Most of the
  per-pass + per-tier framing retires; the walk-page mechanics + the
  budget label / position label survive in reorganized form.
- **memory `[[project_marketing_lifetime_catalogue_promise]]`** — the
  "lifetime catalogue" V2 promise this vision begins to enable
  (cross-event Cuts + tag-driven library).

---

## 8. What this vision retires from earlier specs

- **Per-tier slideshow tiers as fixed concepts** — Short, Medium, Long
  become templates, not tiers. The user can create as many or as few
  Cuts as they want, with any duration.
- **Compilation Setup dialog as a separate "shown once after Portfolio"
  step** — folds into the New Cut dialog. Every Cut gets its own setup
  fields.
- **The Collections page as a distinct surface** — renamed to the Cuts
  list and becomes the Share landing page.
- **Per-event Discard at the curate layer** — retires. Skip is local
  to one Cut now.
- **Subsets as a distinct concept** — retires. A "subset" is just
  another Cut seeded from another Cut.
- **The "first pass = mandatory All-Time Best" model** from the legacy
  Curate pass-flow — retires. No system-framed sequence.
