# spec/91 — Face recognition

**Status:** design **agreed** with Nelson 2026-06-20 (design-mode session,
immediately after [spec/90](90-cut-recipes-and-collections.md)). This doc
consolidates the conversation that worked through *"how should Mira recognize
faces well enough to support `[Pedro]` chips in Recipes, and how do we keep it
simple?"* into a deployable plan. **Implementation gated:** design only;
coding agents wait for Nelson's word.

Reads on top of:
- [`spec/90`](90-cut-recipes-and-collections.md) — Recipe + Person chip
  vocabulary in the New Cut / New Collection dialogs. spec/91 defines the
  detection + recognition substrate that makes `[Pedro]` actually resolve.
- [`spec/63`](63-photo-viewport.md) — PhotoViewport + locked Pick/Skip
  keymap. spec/91 reuses both for the candidate review session.
- [`spec/61`](61-share-event-cuts.md) §2 — Picker session UX pattern. The
  Person review session inherits its shape.
- [`spec/60`](60-batch-export-engine.md) — background batch job posture
  (worker process, no foreground lag). Detection runs through the same
  queue.

What this doc adds, in one sentence: **face recognition is a quality-gated
multi-reference recognizer wrapped in a human-in-the-loop confirmation
surface, scoped library-wide and serving the Person chip in spec/90.**

---

## 0. The north star

> **Detect every face. Embed only the ones big enough to recognize. Let the
> user confirm Mira's guesses, never assert them.**

Three principles that the rest of the doc unpacks:

- **Prominence is the recognition gate, not a filter.** Mira embeds only
  faces above a quality threshold (~5% of image area / ~150 pixels). Faces
  below get a detection row but no embedding. The `[Pedro]` chip
  *automatically* returns only "clearly seen" matches — no `· main subject`
  modifier, no threshold slider, no hidden UI complexity.
- **Identity is a set, not a prototype.** A Person's identity is the
  *collection* of reference embeddings — initial enrollment + every
  user-confirmed match over time. This handles age (Pedro at 5 vs. 30),
  lighting, angle, and hair without any temporal model. Multi-reference
  enrollment is the default, not a power-user feature.
- **The user always confirms; Mira never asserts.** Auto-detection assigns
  faces to `person_id=NULL`. Recognition produces *candidates*. Identity
  only attaches to a face after the user clicks confirm. False positives
  never propagate into Recipes.

---

## 1. The model

### 1.1 Detection vs identification — two thresholds, one pipeline

Mira's pipeline runs in two stages on each photo:

| Stage | Cost | Output |
|---|---|---|
| **Detection** | ~10-30 ms per image (CPU, HOG or fast DNN) | Bounding boxes for every face Mira can find |
| **Embedding** | ~30-100 ms per face (CPU) | A fixed-length numeric vector per face |

The **embedding stage runs only on faces above a prominence threshold** —
default ~5% of image area, configurable in settings. Faces below get a `face`
row with bbox and crowd context, but their `embedding` column stays NULL.

This is the load-bearing simplification. It:

- Saves significant compute on the initial library pass (small faces are
  the majority in many photos — group shots, distant subjects).
- Avoids storing noisy embeddings that produce false positives.
- Implicitly defines what `[Pedro]` returns: Pedro identified, which can
  only happen when his face was prominent enough to embed.
- Lets the user think in one concept ("Pedro appears") instead of two
  ("Pedro is present" vs "Pedro is the subject").

### 1.2 Multi-reference enrollment

A Person's identity is a **set of reference embeddings**, not a single
averaged prototype. Why:

- A 30-year span (Pedro from birth to age 30) is biologically impossible
  to capture in one embedding.
- Lighting, hair, glasses, angle drift naturally across photos.
- New face matching uses the *closest* reference, not an average — so the
  set covers a manifold instead of collapsing to a centroid.

Enrollment seeds the reference set with 5-15 photos the user picks. Every
subsequent **confirmation** of a candidate adds that face's embedding to
the set. The set grows monotonically as the user reviews matches; it
shrinks only when the user retracts a confirmation.

### 1.3 The recognition loop

Recognition is **iterative and human-paced**:

1. **Initial pass** runs at Person creation. Walks every embed-able face
   in the library; computes distance to each of the Person's references;
   keeps the best (closest) reference per face. Faces below threshold
   distance become candidates.
2. **Review session** surfaces candidates ranked by confidence. The user
   confirms or rejects each — Mira's existing Pick/Skip semantics (§2.3).
3. **Reference set grows.** Each confirmed face's embedding joins the
   Person's reference list.
4. **Subsequent passes** compare each still-unidentified face against the
   *new* references added since the last pass (not the whole set — the
   already-checked pairs are cached, §1.5).
5. **Plateau.** Pass after pass returns fewer candidates. When *Find more
   matches* surfaces less than ~5, the user can stop.

### 1.4 The best-match cache

Don't re-scan from scratch on every pass. Two columns on the `face` table:

| Column | Holds |
|---|---|
| `best_match_person_id TEXT` | Person whose reference is currently closest to this face's embedding |
| `best_match_distance REAL` | The distance value |

Updated incrementally:

- New Person created → for each face, compute distance to the new Person's
  references; update if better than current.
- Reference added to existing Person → for each unassigned face, compute
  distance to the *one* new reference; update if better.
- Person deleted → all faces with that `best_match_person_id` re-compute
  against remaining Persons (or set to NULL if no Persons remain).

The candidate query becomes a SQL `WHERE`, not a Python loop:

```sql
SELECT face.* FROM face
WHERE face.best_match_person_id = ?
  AND face.best_match_distance < ?
  AND face.person_id IS NULL
ORDER BY face.best_match_distance
LIMIT 50
```

Result: *Find more matches* lands the next batch on screen in milliseconds.

### 1.5 Confidence threshold

One global threshold per embedding model. Default tuned per model
(face_recognition / dlib: ~0.55 cosine; ArcFace: ~0.45). Surfaced in
settings, not per Recipe. V2 may expose per-Recipe overrides; V1 doesn't.

The threshold gates the **candidate query**, not the embedding storage. A
distance just above threshold today may dip below later, after the
reference set grows. Embeddings stay; the gate floats.

---

## 2. The user experience

### 2.1 Enrollment — the wizard

Triggered from *People page → + New Person* (§2.5) or from anywhere a face
crop is rendered (Picker, Edit, eventually) via a right-click action.

Three steps:

1. **Name.** Single text field. *e.g.* "Pedro".
2. **Pick reference photos.** Multi-select from library — events, day
   folders, current view. Drag-drop also accepted. 5-15 photos is the
   sweet spot. For Persons with long age spans, the user should pick
   across the span (Mira hints: *"covering more life stages helps Mira
   recognize across age changes"*).
3. **Confirm faces.** For each selected photo, Mira shows the detected
   face crops with bounding boxes overlaid on a thumbnail of the photo.
   The user clicks the right one. Photos with no detected face are
   flagged and skipped.

On finish: Person is created with N references; library-wide recognition
queues as a background job.

### 2.2 The library-wide recognition pass

Background batch job, runs through Mira's spec/60 queue. Steps:

1. Compute distance from every embed-able face to each of the Person's
   references; track the best.
2. Update `face.best_match_person_id` and `face.best_match_distance` where
   the new Person's closest reference beats the prior best.
3. Stream progress to the BatchProgressLine: *"Reviewing 1247 of 8432
   faces for Pedro…"*

Same UI patterns as ingest / batch export: non-blocking, cancellable,
deferred until idle.

### 2.3 The review session

Opens from *Person page → Find more matches* or from the badge nudge
(§2.4). Reuses **PhotoViewport with Pick/Skip semantics** (spec/63):

- Each candidate face crop is one ViewportItem.
- `P` confirms the candidate (becomes a reference; identity attaches).
- `X` rejects (face stays detected but identity withheld; the face is
  marked "user-rejected for Person X" so it doesn't re-surface for the
  same Person).
- `Space` toggles. `C` cycles (degenerates to toggle here).
- Esc / back returns to the People page.

Each item displays:

- **The face crop.** Cropped to bbox + ~20% margin.
- **The full photo.** Behind / beside the crop so the user reads context.
- **The matched reference.** Side-by-side so *"why does Mira think this?"*
  is visible.
- **Confidence bar.** Colored 0-100 readout; not a number, not a slider.

Progress through the candidates is ranked by confidence — best first. The
user can stop at any point; remaining candidates stay queued for the
next session.

### 2.4 Surfacing new matches — pull + badge

Two postures Mira uses, no aggressive notifications:

- **Pull.** The People page always has a *Find more matches* button per
  Person. Click → review session opens.
- **Badge.** When new events ingest (or the user adds confirmations to
  another Person, indirectly affecting this one), Mira recomputes the
  best-match cache. Persons whose candidate count grew get a small badge
  on their tile: *"7 new candidates."*

That's it. No modal nudges, no email, no toast. Library upkeep is patient.

### 2.5 The People page

Library-level surface. Sits in the home navigation next to **Events** and
(per spec/90) **Collections**.

Lists every Person as a tile:

```
┌─────────────────────────────────┐
│ [face thumb]  Pedro              │
│               247 photos · 12 new│
└─────────────────────────────────┘
```

Per-Person actions (right-click or hover): *Rename*, *Find more matches*,
*Review confirmations*, *Delete*.

Library-level *+ New Person* button (top of the page).

No event-level People surface in V1 — Persons are library-wide, surfaced
in event Cuts only via the `[Pedro]` chip in the cross-event Collection
dialog and (when opted in, per spec/90 §4.3) the event Cut dialog.

### 2.6 Deleting a Person

User confirms; cascade:

- Every `face.person_id = pedro_id` → set to NULL (detection survives;
  identity is gone).
- Every `person_reference` row referencing Pedro → delete.
- Every `face.best_match_person_id = pedro_id` → recompute or set NULL.
- Recipes that reference `[Pedro]` are unaffected at storage; at
  *load time* they trip the strict-resolution rule (spec/90 §1.4) and
  refuse to load until edited. The user chose to delete; the side effect
  is honest.

V1 does **not** support undo. V2 may.

---

## 3. Integration with Recipes (spec/90)

### 3.1 The `[Pedro]` chip

Resolves via:

```sql
SELECT face.item_id FROM face
WHERE face.person_id = pedro_id
```

Multiplicity: items with `face.person_id = pedro_id` across all of the
recipe's scope. A photo with Pedro detected three times (Pedro looking
at himself in mirrors, say) appears once — the `item_id` join.

Filtering by **confidence at attach time** is not exposed in V1 — every
confirmed face is treated as authoritative. The user confirmed; Mira
trusts.

### 3.2 The `[#unrecognized_faces]` operand

Surfaces faces detected but never assigned (`face.person_id IS NULL`).
Useful for housekeeping recipes (*"what's left to label?"*). Library-wide
chip; behaves the same as a Person chip in the grammar.

### 3.3 Cross-event semantics

Persons are library-level; faces are per-event. A Person's reference set
spans events. Recognition uses every reference regardless of which event
it lives in.

When a Recipe runs across N scoped events, Mira opens each event's
`event.db` to read face rows. The same Person id resolves consistently
because it's library-level.

### 3.4 The "and" / "or" join words on Person chips

Already covered by spec/90 §3.2. Faces have cardinality 0..N per item, so
"`[Pedro] and [Maria]`" means *both detected in the same photo*. The
algebra is unchanged from any other operand.

---

## 4. Data model

### 4.1 Schema additions (post-Phase 1 of spec/90)

spec/90 Phase 1 establishes empty `person` and `face` tables. spec/91 adds
the columns + tables that make recognition actually work.

#### `face` (event.db) — additions

| Column | Type | Notes |
|---|---|---|
| `embedding` | BLOB | Fixed-length numeric vector (model-dependent size). NULL for sub-threshold faces. |
| `embedding_model` | TEXT | Model identity (e.g. `"dlib_128"`, `"arcface_512"`). Cross-model comparison is blocked at the distance layer. |
| `image_area_ratio` | REAL | `(bbox_w * bbox_h) / (image_w * image_h)`. The prominence gate uses this. |
| `face_count_in_image` | INTEGER | Total detected faces in this image. Useful for V2 crowd-size modifiers. |
| `best_match_person_id` | TEXT | The Person whose reference is currently closest (the cache, §1.4). |
| `best_match_distance` | REAL | Distance to that Person's closest reference. |
| `user_rejected_persons_json` | TEXT | JSON array of Person ids the user has explicitly rejected for this face. Stops them re-surfacing. Default `'[]'`. |
| `detection_model` | TEXT | Detector identity. Tracked for the same swap-safety as embeddings. |

Indexes: `(best_match_person_id, best_match_distance)` for the candidate
query; `(person_id)` for Recipe resolution.

#### `person_reference` (mira.db) — new table

Holds the multi-reference set per Person. One row per (Person, contributing
face) pair.

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PRIMARY KEY | |
| `person_id` | TEXT NOT NULL | Library-level Person reference |
| `event_uuid` | TEXT NOT NULL | Which event's db holds the face row |
| `face_id` | TEXT NOT NULL | Face id within that event |
| `source` | TEXT NOT NULL CHECK (source IN ('enrollment', 'confirmation')) | Where the reference came from |
| `weight` | REAL NOT NULL DEFAULT 1.0 | Optional contribution weight (V2 may use; V1 leaves at 1.0) |
| `confidence_at_add` | REAL | Distance the face had to existing references when added (NULL for initial enrollment) |
| `added_at` | TEXT NOT NULL | |

Unique on `(person_id, event_uuid, face_id)`.

When Mira reads a Person's references, it joins to each `event_uuid`'s
`face` table for the embedding bytes. Cross-event open / close is short
and read-only.

#### `person` (mira.db) — schema additions

spec/90 Phase 1 set `representative_face_id TEXT`. spec/91 adds:

| Column | Type | Notes |
|---|---|---|
| `reference_count` | INTEGER NOT NULL DEFAULT 0 | Denormalised count of references — for the People-page tile, no recount per render |
| `assigned_face_count` | INTEGER NOT NULL DEFAULT 0 | Denormalised count of identified items — same |

Both updated by triggers or by the recognition pipeline; both safe to
recompute from scratch if drift is suspected.

### 4.2 Settings additions

User-store settings keys:

| Key | Default | What it controls |
|---|---|---|
| `face_recognition_enabled` | `false` | Master toggle. Off ships zero face work; on enables detection at ingest. |
| `face_embedding_model` | `"dlib_128"` | Which embedder Mira uses for new detections. |
| `face_prominence_threshold` | `0.05` | Image-area ratio below which embedding is skipped. |
| `face_match_threshold` | model-dependent | Distance gate for the candidate query. |
| `face_review_batch_size` | `50` | How many candidates surface per session. |

### 4.3 Library choice for V1

**face_recognition (dlib)** — chosen for V1:

- One pip install (plus dlib's heavier wheel, ~100MB).
- HOG detector at CPU-acceptable speed; CNN detector optional for harder
  cases.
- 128-dim embedding.
- Simple `face_recognition.face_locations`, `face_recognition.face_encodings`,
  `face_recognition.face_distance` API.

**InsightFace (ArcFace, ONNX)** — V2 candidate:

- State-of-the-art accuracy, especially across age and lighting.
- Requires onnxruntime + a downloaded model file.
- 512-dim embedding (not interchangeable with dlib).

The model-agnostic design (`face.embedding_model` column, threshold per
model in settings) means switching is a feature flag + a re-detection
pass on already-detected faces. Coexistence is supported — old dlib
embeddings stay readable, new detections use ArcFace.

---

## 5. The detection + embedding pipeline

### 5.1 When detection runs

**At ingest, in the background, opt-in via settings.** Specifically:

- A new event ingests (spec/40-collect pipeline) → after the photo lands
  on disk and Item rows are written, a *face detection* batch job
  enqueues for that event's photos.
- The user changes `face_recognition_enabled` from `false` to `true` →
  the library-wide backfill detection job enqueues.
- The user manually triggers *Re-detect this event* from the event's
  context menu → that one event re-enqueues.

Detection **does not run synchronously inside the user's foreground
flow**. Photos appear in their phases without waiting on face work. The
BatchProgressLine surfaces face work alongside other batch jobs.

### 5.2 What detection writes

For each detected face:

```python
gw.record_face(Face(
    id=new_id(),
    item_id=item.id,
    bbox_x=..., bbox_y=..., bbox_w=..., bbox_h=...,
    image_area_ratio=...,
    face_count_in_image=...,
    detection_model="dlib_hog_v1",
    detected_at=now(),
    # embedding fields filled only if area_ratio >= prominence_threshold
    embedding=encoded_vector or None,
    embedding_model="dlib_128" or None,
))
```

### 5.3 Re-detection on item changes

- **Item deleted** → ON DELETE CASCADE removes the face rows.
- **Item replaced** (user re-imports the same source with a sharper
  version) → if the sha256 changes, Mira re-runs detection.
- **Item rotated / cropped in Edit** → face bboxes invalidate (the spatial
  reference moved). V1 invalidates by setting all of the item's faces to
  detection_stale; the next batch picks them up. V2 may transform
  bboxes through the recipe.

### 5.4 Performance budget

For a typical library (50k-200k photos):

- **Initial detection pass:** 3-10 photos/sec → 5,000-50,000 seconds
  (~1.5-14 hours) of background CPU. One-time cost; runs overnight.
- **Per-event ingest:** new event of 500 photos → ~1-3 minutes,
  transparently in the background while the user phases through.
- **Embedding cost is dwarfed by detection** — embeddings are fast once
  the face is cropped.

Acceptable for an offline desktop app on Mira's hardware target.

---

## 6. The recognition + matching pipeline

### 6.1 When recognition runs

Three triggers:

- **Person created** → library-wide pass: every embed-able face compared
  against the new Person's references; best-match cache updated.
- **Reference added** (user confirms a candidate, or adds an extra
  enrollment photo) → narrower pass: every unassigned face compared
  against the one new reference; best-match cache updated if better.
- **Person deleted** → faces with `best_match_person_id = deleted_id`
  recompute against remaining Persons (or set NULL).

All three run through the spec/60 batch queue.

### 6.2 What recognition writes

For each face whose closest distance to a Person's references improves on
the current `best_match_distance`:

```sql
UPDATE face
SET best_match_person_id = ?,
    best_match_distance = ?
WHERE id = ?
```

Recognition **does not** write `face.person_id`. That column only fills on
user confirmation in a review session.

### 6.3 The candidate query

Already covered (§1.4). The People-page badge counts surface from:

```sql
SELECT COUNT(*) FROM face
WHERE best_match_person_id = ?
  AND best_match_distance < ?
  AND person_id IS NULL
  AND (user_rejected_persons_json NOT LIKE '%' || ? || '%')
```

The last clause excludes faces the user already rejected for this Person.

### 6.4 The review session writes

When the user confirms a candidate (`P` key):

- `face.person_id` ← Pedro's id.
- New `person_reference` row inserted (source: `'confirmation'`).
- `person.reference_count` and `person.assigned_face_count` increment.
- The face is removed from the candidate query (`person_id` is no longer
  NULL).
- A delayed background job adds this face's embedding to subsequent
  matching — *the loop continues*.

When the user rejects (`X`):

- `face.person_id` stays NULL.
- This person id appends to `face.user_rejected_persons_json`.
- The face is excluded from this Person's candidate query forever (until
  the user explicitly retracts in V2).
- The face stays eligible to be a candidate for **other** Persons.

---

## 7. Implementation strategy

Strictly phased so each layer ships verifiably and can be paused. spec/90's
Phase 1 must land first (the empty `person` and `face` tables).

### Phase 1 — detection substrate
- Add the `face` columns (§4.1) via migration.
- Wire dlib / face_recognition into the codebase (offline pip install
  guidance in CLAUDE.md; the library is opt-in via settings, so default
  installs don't need it).
- Implement the detection job: enqueueable, writes face rows, no
  embeddings yet.
- Tests: per-photo detection produces correct bbox rows; sub-threshold
  faces have NULL embedding; idempotent re-runs.

### Phase 2 — embedding + best-match cache
- Embed faces above prominence threshold.
- Implement the recognition job: compute distance to a Person's
  references; update best-match cache.
- Settings UI for the two thresholds.
- Tests: cache updates on Person create / reference add; SQL candidate
  query returns expected rows.

### Phase 3 — enrollment wizard + People page
- Library-level People page (list, +New Person, per-Person actions).
- Enrollment wizard (name → pick photos → confirm faces).
- *Find more matches* button.
- Badge nudges on new candidates.
- Tests: enrollment produces `person_reference` rows; recognition queues
  on Person create.

### Phase 4 — review session
- Adapt PhotoViewport for face-crop items.
- Wire Pick/Skip keys to confirm/reject semantics.
- Write the confirmation → `face.person_id` + `person_reference` path.
- Tests: confirm writes the right rows; reject updates the rejected
  set; the candidate count drops on each decision.

### Phase 5 — Recipe integration
- Implement the `[Pedro]` chip resolution in the resolver (spec/90
  Phase 2's gateway extension already supports a "Person id" operand
  type at the chip layer; this phase fills in the SQL).
- Implement `[#unrecognized_faces]` operand.
- Tests: Recipe with a Person chip returns the expected items;
  cross-event Recipes traverse `event.db` correctly.

### Phase 6 — InsightFace migration (V2 candidate)
- Add ArcFace via onnxruntime as an alternative embedding model.
- Settings entry to switch.
- Re-detection job for cross-model upgrade.

---

## 8. Dependencies, supersedes, parks

**Depends on:**
- spec/90 Phase 1 (empty Person + Face tables in place).
- spec/90 Phase 2 (resolver supporting per-operand-type chip resolution).
- spec/60 batch engine (for detection + recognition background work).

**Supersedes:**
- Nothing — this is net new functionality.

**Parks for V2:**

- **Auto-clustering unknown faces** into suggested Persons.
- **Per-Recipe confidence thresholds.**
- **Cross-Person merging** ("these two are actually the same person").
- **Drift adjustment** (auto-tightening threshold as reference set grows).
- **Bbox transformation through Edit operations** (crop/rotate change face
  coordinates; V1 invalidates and re-detects).
- **Undo for Person deletion.**
- **InsightFace / ArcFace as the default model.**

---

## 9. Open questions for kickoff

1. **Detection model selection.** dlib's HOG is fast but misses profile
   faces; the CNN detector is accurate but ~10× slower. Ship HOG and let
   the user opt into CNN per event ("re-detect with the slow accurate
   model"), or pick one for V1?
2. **Where do face crops live for the review session?** Re-extract from
   the source photo each render (small CPU cost, no storage), or
   pre-cache as JPEG thumbnails (faster review, ~50KB per face)? Probably
   thumb-cache for review-session speed.
3. **The "show the matched reference" affordance.** Static image
   side-by-side, or a hover/click reveal? Static is more honest; hover
   keeps the layout tight.
4. **Aging hints at enrollment time.** Mira could analyse the user's
   selected reference photos and suggest *"add a photo from an earlier
   era?"* if all references cluster in a narrow time window. Out of scope
   for V1; possible polish for V2.
5. **What's the right number for the prominence threshold default?** 5%
   image area is a starting point. The right number wants empirical tuning
   across a sample of real Mira libraries.
6. **The `face_recognition` package's dlib dependency on Windows.** dlib
   wheels for Windows are reasonably available but require Visual Studio
   build tools for some flavours. The opt-in posture (default settings off)
   protects users from this; the install instructions need to be honest.

---

## 10. The worked example

Pedro through the years, end to end:

1. **Settings on.** Nelson flips `face_recognition_enabled = true`. Mira
   queues the library-wide detection job. ~3 hours later, every photo
   has face rows; ~40% of those rows have embeddings (the rest are
   small / occluded).
2. **Enrollment.** Nelson opens the People page, clicks +New Person,
   names "Pedro", picks 12 photos spanning his life (baby, kindergarten,
   age 10, age 15, age 20, age 25, current). Confirms which face is
   Pedro in each. Done.
3. **Initial pass.** Mira runs library-wide recognition. ~12 seconds
   later, the best-match cache shows ~340 candidates with
   `best_match_person_id = pedro` and distance < threshold.
4. **First review.** Pedro tile shows "340 candidates". Nelson clicks
   *Find more matches*. The review session opens — face crops ranked
   by confidence. P confirms, X rejects. Over 20 minutes, Nelson
   confirms 280, rejects 60.
5. **Reference set grows to 292.** Mira incrementally recomputes the
   best-match cache against the 280 new references. ~30 seconds. The
   badge updates: "Pedro · 87 new candidates" — photos that didn't
   match the original 12 but match newer references.
6. **Second review.** Nelson clicks the badge. 87 candidates, again
   ranked. Confirms 70, rejects 17. Reference set now 362.
7. **Plateau.** Third pass surfaces 12 candidates; fourth surfaces 3.
   Nelson stops.
8. **The Recipe.** Nelson creates a Collection Recipe:
   > **Scope:** all events
   > **Source:** `[#exported]`
   > **Filters:** Faces `[Pedro]`
   > **Otherwise:** pick

9. **Run.** The Picker session opens with ~330 photos pre-picked across
   30 years of Pedro. Nelson curates down to the 40 best.
10. **Save as Recipe.** The composition saves as
    *"Pedro through the years"*. Next year, Nelson re-opens it; the
    library has grown; the Recipe surfaces the new shots; the loop
    continues.

That's the model.
