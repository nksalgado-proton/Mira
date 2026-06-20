# spec/90 — Cut Recipes and cross-event Collections

**Status:** design **agreed** with Nelson 2026-06-20 (design-mode session). This
doc consolidates the conversation that started from a small symptom — *"can I
make a #short Cut from #long with #best_wildlife and #best_landscapes pre-picked?"*
— into a unified model for how the user composes Cuts (event-scope) and
Collections (cross-event scope) from the same engine. **Implementation gated:**
this is design only; coding agents wait for Nelson's word.

Reads on top of (does not replace):
- [`spec/81`](81-dynamic-collection-and-cut.md) — the **two-nouns / two-verbs**
  engine (Dynamic Collection + Cut, pin + export). spec/90 adds a third saved
  noun — the **Recipe** — and a richer dialog grammar; it does not change spec/81's
  set algebra or the DC↔Cut relationship.
- [`spec/61`](61-share-event-cuts.md) — event-Cut surfaces (Cuts list, Picker
  session, flat grid, separators, audio, export). All still apply; spec/90
  changes only the **New Cut dialog** described in spec/61 §2.
- [`spec/80`](80-cut-construction-model.md) — the construction-session record
  this dialog redesign **supersedes**. The 3-way Build mode pill
  (`keep_all / weed_out / pick_in`) is retired; its three states fall out of
  the rule-list model as syntactic sugar (§3.5).

What this doc adds, in one sentence: **the New Cut dialog becomes a rule-list
recipe editor with two flavours (Cut / Collection) sharing one widget and one
storage format.**

---

## 0. The north star

> **One engine, one grammar, two faces, one saved Recipe noun.**

- **One engine.** The set-algebra DC resolver from spec/81 §2 runs everything
  — Scope, Source, Filter narrowing, each rule predicate. No new evaluation
  primitives.
- **One grammar.** Every place the user composes something is the same
  **chip + join-word sentence**: an operand chip, an `or` / `and` / `but not in`
  join, another operand chip, repeat. The user learns this vocabulary once and
  uses it for Scope, Source, every Rule predicate, and Filters.
- **Two faces.** The same widget runs in two configurations: an
  **event-Cut dialog** (audience-facing compilation, narrow controls) and a
  **cross-event Collection dialog** (personal curation / search, full controls).
- **One Recipe.** The saved configuration — everything the user composes except
  the actual hand-pick / skip decisions made in the Picker session — is a
  **Recipe**, persisted at the library level, applicable across events.

---

## 1. The unified model

### 1.1 The five sections

Both dialog faces render the same five sections, top to bottom:

| Section | What it expresses | Set algebra over |
|---|---|---|
| **Scope** | Which events' items can be reached | Events / Event Collections / date ranges |
| **Source** | Which items in scope enter the pool | DCs / Cuts / base universes (`#exported`, ladder rungs) |
| **Filters** | Narrow the pool by item metadata | Bounded item vocabularies (Style, Media, Camera, Lens, Faces) |
| **Rules** | Which pool items start *picked* vs *skipped* | Ordered list of (predicate, verdict) — first match wins |
| **Otherwise** | Default verdict for items no rule matched | One verb: `pick` or `skip` |

Each section is **optional except Source and Otherwise**: an empty Scope
defaults to "this event" (Cut flavour) or "no scope chosen yet" (Collection
flavour); an empty Filters block applies no filter; an empty Rules list means
everything in the pool falls through to Otherwise. Source must be non-empty (the
pool must be defined) and Otherwise always has a verdict.

### 1.2 The chip + join-word sentence

A sentence is one or more operand chips joined by `or` / `and` / `but not in`,
evaluated **strictly left-to-right** (spec/81 §2). Grouping is by **nesting a
named operand** — a saved DC stands in for parentheses. There are no precedence
rules and no bracket UI.

Example sentences:

> **Source:** Start from `[#long]`
> **Source:** Start from `[#exported]` but not in `[#rejects]`
> **Rule 2 predicate:** If items are in `[#best_wildlife]` or `[#best_landscapes]`

Each chip carries its operand's live count (`#best_wildlife (7)`). The
`+ or / and / but not in…` affordance at the end of every sentence adds another
operand. Each join word is a small dropdown showing the three options with their
plain-language meanings ("in either set", "in both sets", "exclude these").

### 1.3 Rules and verdicts

A **Rule** is a sentence (the predicate) plus a **verdict** (`pick` / `skip`).
Rules are ordered. **First match wins** — the first rule whose predicate
matches an item dictates its initial state. Items matching no rule fall through
to **Otherwise**.

The user re-orders rules by drag. Each rule shows a live match count next to
its verdict, including the "of which N already covered by earlier rules"
nuance when overlap exists.

### 1.4 Strict reference resolution

A Recipe references named operands (DCs, Cuts, Event Collections, People). At
load / instantiate time the engine resolves every chip:

- **Operand exists** → resolve to its current member set.
- **Operand exists in some events but not others** in scope → resolve to the
  union of what does exist; the dialog shows a small badge ("found in 3 of 5
  scoped events").
- **Operand is missing** (renamed, deleted) → the Recipe **refuses to load**
  with a clear error: *"This Recipe references `#best_wildlife` (deleted on
  2026-06-15). Edit the Recipe or restore the operand."*

The strict rule applies to **named references** — DCs, Cuts, Event Collections,
People. **Vocabulary-based filters** (Style, Media, Camera, Lens) resolve
leniently to empty if no items match — the vocabulary itself exists library-wide.
A Recipe `Camera: [G9]` against a Bali trip with no G9 shots resolves to "0 in
pool" and loads fine; the user adjusts.

### 1.5 Verdicts and the missing pin-mode question

spec/80's `keep_all / weed_out / pick_in` pill group is gone. Its three states
are now syntactic sugar over rules + Otherwise:

| Old pin mode | New expression |
|---|---|
| `pick_in` (start all-out, pick the keepers) | No rules · **Otherwise → skip** |
| `weed_out` (start all-in, skip the rejects) | No rules · **Otherwise → pick** |
| `keep_all` (pin the DC 1:1, no curation) | No rules · **Otherwise → pick** + Picker session skipped (§4.4) |

The new model is **strictly more expressive** — anything the old pin modes did,
the rule list does with zero or one rule; anything beyond that (the
`#short` scenario, two-sided rules) becomes expressible for the first time.

---

## 2. The two dialog faces

### 2.1 Event Cut dialog (audience-facing)

**Mental model:** *"A compilation of the best photos and videos from this
event, to share with other people."* The viewer cares about content, not gear.

The dialog renders **Source + Filters (Style, Media only) + Rules + Otherwise +
runtime presentation settings**. Scope is fixed to the current event and
hidden. Hardware filters (Camera, Lens) are hidden. Face filters are hidden by
default (§4.3 — opt-in via user setting).

Inventory shows operands available **within the current event**: `#exported` as
base universe, plus every DC and Cut that lives in this event.

Entry point: today's *"New Cut"* button in the Cuts list page (spec/61 §2).

### 2.2 Cross-event Collection dialog (curation-facing)

**Mental model:** *"A curated set of media across my library, for personal
study or recurring sharing."* Examples: `#all_time_best_macro`,
`#wildlife_with_the_100_500`, `#shots_of_pedro_at_the_beach`.

The dialog renders **all five sections**, with the Scope sentence at the top
(events + Event Collections + date-range chips) and all filter dimensions in
the Filters block (Style, Media, Camera, Lens, Faces, plus future).

Inventory shows operands available **across the selected scope**: the four
ladder rungs as base universes
(`#collected / #picked / #edited / #exported`), plus every cross-event DC and
every event-scope DC / Cut from the selected events.

Entry point: a separate library-level *"New Collection"* action (spec/61 §8 —
cross-event Cuts, here renamed Collections for clarity, see §6).

### 2.3 One widget, two configurations

The two faces are the same Qt widget with two configuration flags:

```python
NewRecipeDialog(
    show_scope=False,        # Cut: scope = current event
    show_hardware=False,     # Cut: no camera / lens / face filters
    inventory_scope="event", # Cut: operands from this event only
    recipe_pool="cut",       # Cut: list of Cut-flavour Recipes
)

NewRecipeDialog(
    show_scope=True,                 # Collection: composed scope
    show_hardware=True,              # Collection: full filters
    inventory_scope="library",       # Collection: operands library-wide
    recipe_pool="collection",        # Collection: list of Collection Recipes
)
```

No duplicated widget code. Adding a future filter dimension lights up in both
faces with one feature flag flip.

### 2.4 Vocabulary

| Concept | Event-scope name | Cross-event name |
|---|---|---|
| The frozen output | **Cut** | **Collection** |
| The dialog | New Cut | New Collection |
| The saved Recipe | Cut Recipe | Collection Recipe |
| The list-page surface | Cuts (per event) | Collections (library) |

The split is intentional: a *Cut* is for sharing one trip; a *Collection* is
for curating a theme. They share the same underlying schema (§5) and the same
engine, but they read differently to the user.

---

## 3. The chip + join-word grammar in detail

### 3.1 Operand types

Every chip is one of:

| Chip type | Where it can appear | Resolves to |
|---|---|---|
| **Base universe** (`#exported`, ladder rungs) | Source | All items at that rung in scope |
| **DC** (`#all_time_best_macro`) | Source · Rule predicate | The DC's current live resolution |
| **Cut** (`#long`) | Source · Rule predicate | The Cut's frozen membership |
| **Event** (`[Alaska]`) | Scope | That event's items |
| **Event Collection** (`#adventure_events`) | Scope | The collection's events |
| **Date range** (`[2018 — 2020]`) | Scope | Events whose date falls in range |
| **Person** (`[Pedro]`) | Filters · Rule predicate (advanced) | Items where this person is detected (§4.3) |
| **Hardware vocabulary** (`[G9]`, `[100–500mm]`) | Filters | Items with this camera / lens (§4.2) |
| **Item vocabulary** (`[macro]`, `[Photos]`) | Filters | Items matching this Style / Media kind (§4.1) |

Operands are pickable from a single popover the user opens by clicking the chip
or the `+ add operand` affordance. The popover is sectioned by chip type,
search-filterable, and shows live counts.

### 3.2 The three join words

Between two chips, the user picks one of:

| Word | Semantics | Verb (set algebra) |
|---|---|---|
| `or` | items in either set | union (`+` / `∪`) |
| `and` | items in both sets | intersection (`∩`) |
| `but not in` | items in the left, excluded from the right | difference (`−`) |

The join word renders as small text with a chevron, opens a popover showing all
three with their meaning. **One-click swap.** Evaluation is left-to-right;
nesting via saved DCs supplies parentheses.

### 3.3 The verb popover (Rules only)

Each Rule and the Otherwise row carry a **verdict** — `pick` or `skip` — shown
as a coloured pill (`pick` green, `skip` red). Clicking the pill opens a small
two-row popover with plain-language descriptions:

> **pick** — items matched by this rule start picked. *(green)*
> **skip** — items matched by this rule start skipped. *(red)*

### 3.4 The operand picker popover

Clicking a chip (or the `+ add operand` button) opens an operand picker:

- **Sectioned** by type, in order: Base universes · DCs · Cuts · Event
  Collections · Date ranges (Collection dialog only).
- **Search-filterable** by name.
- **Live counts** beside each entry.
- **"Save as DC…" affordance** at the bottom — opens the new-DC sub-dialog
  with the current Source's chips pre-filled. The user names the new DC, saves,
  and the new chip lands in the picker (and, optionally, in the active
  sentence).

### 3.5 Equivalences (cheat sheet)

| The user wants | Source | Rules | Otherwise |
|---|---|---|---|
| Pin `#long` as-is (today's `keep_all`) | `#long` | — | pick |
| Trim `#long` to a budget (today's `weed_out`) | `#long` | — | pick (and skip in Picker) |
| Build a Cut by handpicking (today's `pick_in`) | `#long` | — | skip |
| Pre-pick the bests, the rest skipped (`#short`) | `#long` | If in `#best_wildlife` or `#best_landscapes` → pick | skip |
| Pre-skip the rejects, the rest picked | `#long` | If in `#rejects` → skip | pick |
| Two-sided: skip rejects, pre-pick bests | `#long` | If in `#rejects` → skip · If in `#bests` → pick | skip |

---

## 4. Filter dimensions

Filters narrow the resolved pool — they **do not** change verdicts. They live
in their own section between Source and Rules so the user can scan "what's the
universe" before reading "how do I curate it".

### 4.1 Style + Media (both dialogs)

- **Style** chips: the user's classification vocabulary
  (`[macro] [wildlife] [landscape] …`). Multi-select; chips join implicitly
  with `or`. Same column / same data path as today's dialog.
- **Media** checkboxes: `[✓ Photos] [✓ Videos]`. Same as today.

The 2026-06-19 fix that lets unclassified videos pass through Style filters
stays in force — Style narrows the photo population only (commit `64df266`).

### 4.2 Camera + Lens (Collection dialog only)

- **Camera** chips: the user's library-wide camera bodies. Multi-select.
  Source: `item.camera_id`.
- **Lens** chips: the user's library-wide lens vocabulary. Multi-select.
  Source: `item.lens_model`.

The dialog adapts to the user's inventory — a single-camera photographer never
sees the Camera row at all.

Hidden from the event-Cut dialog. A user who wants gear-aware narrowing on an
event Cut uses a DC (e.g. `#g9_macro = #exported ∩ camera:G9 ∩ style:macro`)
and drops it into Source — keeping the event-Cut dialog visually clean while
allowing escape into full power.

### 4.3 Faces (Collection dialog, opt-in for Cuts)

A Person chip (`[Pedro]`) resolves to "items where Pedro is detected" via the
new `face` table (§5.2). Multi-select; same join grammar:

> `Faces: [Pedro] or [Maria]` — appears together or apart
> `Faces: [Pedro] and [Maria]` — both in the same shot
> `Faces: [Pedro] but not [Maria]` — Pedro yes, Maria no

Special operands:
- **`[#unrecognized_faces]`** — items with detected faces but no Person
  assignment. Useful for "what's left to label?" recipes.
- **Confidence threshold** is a user setting, not a Recipe field. The
  threshold floats; Recipe semantics stay portable.

Always available in the Collection dialog. **Opt-in via user setting** for the
event-Cut dialog (a *Travel — kids highlights* recipe is a legitimate share
use, but not the default).

### 4.4 What stays a DC, not a filter row

Continuous-valued metadata — aperture, ISO, focal length, shutter speed,
weather, location-radius, capture-hour — does **not** earn a filter row. The
user creates a named DC (`#wide_open = aperture ≤ f/2.8`) once and uses it as
a chip anywhere. This keeps the rule grammar uniform ("every predicate is
items in `<set>`") and rewards investment in reusable named queries.

The filter row holds dimensions the user reaches for **casually**, every
recipe. Anything they reach for **occasionally**, even if frequently, becomes a
named DC.

---

## 5. New entities

### 5.0 Save as DC vs Save as Recipe (two seams, two payloads)

The dialog exposes two separate "save" affordances and they save different
things:

- **Save as DC** lives in the operand picker popover (spec/90 §3.4) and is
  reachable from any picker whose output is an item-set — the Source picker
  and the per-rule predicate picker. The saved payload is the **expression
  the picker just composed** plus, for Source-target saves, the dialog's
  current Filters block. Rule-predicate saves carry an empty filters block
  (predicates don't compose with the dialog-level Filters row). The Scope
  picker hides this affordance — events don't compose into DCs; that's the
  Event Collection track (§5.3).
- **Save as Recipe…** lives in the dialog footer and saves the **whole
  workflow** — Source + Scope + Filters + Rules + Otherwise + presentation
  — under the dialog's flavour (Cut / Collection). It is the "share the
  decision-making procedure" seam (§5.1).

Both are reachable from the same dialog at different points; mixing them
is intentional — the source-level set becomes a reusable named DC, the
whole workflow becomes a reusable named Recipe. The Save as DC affordance
fires the host's `dc_creator(name, expr, filters)` callable and, on
success, drops the returned operand into the dialog's local operand
inventory so it's pickable as a chip immediately.

### 5.1 Recipe (the saved decision-making procedure)

A **Recipe** is the saved Cut / Collection configuration. It holds **everything
the user composed except the Picker session's hand decisions**:

- Scope sentence (Cut: implicit; Collection: composed)
- Source sentence
- Filter selections (Style, Media, Camera, Lens, Faces)
- Rules list (predicate sentences + verdicts)
- Otherwise verdict
- Presentation settings (slide cards style, target / max minutes, per-photo
  seconds, music category)

Recipes are **library-level**, applicable across events. The Recipe schema is
**flavoured**: a Recipe is either a `cut` flavour (no Scope, no hardware /
face filters) or a `collection` flavour (full sections). Cross-pollination is
explicit (§5.5).

A Recipe **does not** hold:
- The Cut / Collection's name (every instance has its own).
- The Picker session's per-file decisions.
- The Recipe's resolved set at any past time (it's always evaluated against
  current data at instantiate time, modulo the strict-rule guard for missing
  references).

**Phase 1 schema (mira.db, schema v7):**

```sql
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
```

`composition_json` is one opaque blob carrying Scope / Source / Filters / Rules
(predicates + verdicts) / Otherwise / presentation. The shape is dialog-defined
so the storage schema doesn't lock the dialog in. `extras_json` joins as the
house escape hatch (spec/53 §1.1 — every table that might grow gets one).
`UNIQUE (flavour, name)` splits the namespace by flavour so a Cut Recipe and a
Collection Recipe may share a name (§5.5).

**Phase 2 composition_json shape** (landed alongside the resolver). The
resolver reads these keys; any others (and any unknown nested keys) round-trip
unchanged. Every section except `source` and `otherwise` is optional.

```jsonc
{
  // Scope sentence (Collection face only — empty/missing for Cut). Same chip
  // + join-word grammar as Source. Operand kinds: "event" / "event_collection".
  "scope": [
    ["+", {"kind": "event", "uuid": "evt-alaska"}],
    ["+", {"kind": "event_collection", "tag": "adventure_events"}]
  ],

  // Source sentence — REQUIRED, non-empty. Standard spec/81 expression.
  // Operand kinds: base token "exported" (or a ladder rung for Collection),
  // "dc" (typed ref), "cut" (typed ref).
  "source": [["+", "exported"]],

  // Filter section — narrows the resolved pool. All keys optional.
  //   styles, media_type    — Style + Media (spec/90 §4.1; both dialogs)
  //   camera_ids, lens_models — Camera + Lens (§4.2; Collection only, but
  //                             the storage shape is the same)
  //   person_ids            — Person multi-select (§4.3; advanced for Cut)
  //   plus the spec/32 §2 catalogue for the Collection face
  "filters": {
    "styles": ["macro"],
    "media_type": "both",
    "camera_ids": ["G9"],
    "lens_models": ["100-500mm"],
    "person_ids": ["person-pedro"]
  },

  // Rules — ordered list; first match wins (§1.3). Each rule's predicate
  // is a sentence resolved via the same set-algebra engine; the verdict
  // ("pick" / "skip") is applied to items the predicate set contains.
  // Operand kinds in a predicate: dc, cut, person.
  "rules": [
    {
      "predicate": [["+", {"kind": "cut", "tag": "blurry"}]],
      "verdict": "skip"
    },
    {
      "predicate": [
        ["+", {"kind": "cut", "tag": "best_wildlife"}],
        ["+", {"kind": "cut", "tag": "best_landscapes"}]
      ],
      "verdict": "pick"
    }
  ],

  // Otherwise — REQUIRED. Verdict for items matching no rule.
  "otherwise": "skip",

  // Presentation — non-resolver state for the Picker session + export
  // pipeline. Resolver ignores; the dialog reads. `target_s` / `max_s`
  // are nullable: when both are `null` the Cut carries no runtime
  // budget (the dialog's "Set a runtime budget" checkbox emits both as
  // null when unchecked, and the picker shows "no limit"). `photo_s`
  // is slide-rate, not a budget — always present.
  "presentation": {
    "target_s": 90,
    "max_s": 300,
    "photo_s": 6.0,
    "music_category": "happy",
    "card_style": "multi"
  }
}
```

The resolver returns a `RecipeResolution(pool, seed)` where `pool` is the
ordered list of member keys (export relpaths for event scope; cross-event
packed keys for cross-event), and `seed[key] = True/False` is the initial
picked-state the Picker session opens against. Missing named operands raise
`RecipeResolutionError(missing_operand, kind)`; vocabulary filter misses
resolve leniently to empty (§1.4).

The dialog exposes a `has_budget` toggle (`NewRecipeContext.has_budget`,
default `true` for new Cuts). When `false` the Target / Max spinners go
disabled and the emitted `presentation.target_s` / `max_s` are both
`null`; the [`recipe_to_cut_draft`](../mira/shared/recipe_draft_adapter.py)
adapter propagates the nulls into the resulting `CutDraft`, and the
[picker session](../mira/ui/shared/cut_session_page.py) renders "no
limit" from there. The Adjust prefill derives `has_budget` from the
existing Cut's bounds — a Cut saved with `target_s = max_s = NULL`
re-opens with the checkbox unchecked.

### 5.1.1 Phase 3 — Recipe ↔ `CutDraft` adapter

The dialog will eventually consume the Recipe via the existing
[`CutDraft`](../mira/shared/cut_draft.py) handoff value (the dialog →
pin-session contract the spec/61 picker and spec/81 commit path already
read). Phase 3 lands the adapter so a Cut-flavoured Recipe ↔ `CutDraft`
round-trip works before Phase 4 builds the new widget.

**The pin-mode extension.** `CutDraft.pin_mode` gains a fourth value —
`'rule-based'` — for Recipes that compose a non-trivial rule list. The
three legacy modes (`keep-all` / `weed-out` / `pick-in`) remain expressible
verbatim and round-trip via the §1.5 sugar. The picker reads
`pin_mode == 'rule-based'` as "walk `rules` first-match-wins, fall back
to `otherwise`"; the existing legacy paths keep working unchanged. Two
new fields ride alongside: `rules: tuple[CutDraftRule, ...]` and
`otherwise: 'pick' | 'skip'`. The §1.5 sugar collapse runs both ways:

| Recipe composition shape       | CutDraft pin_mode |
|---|---|
| Non-empty `rules`              | `'rule-based'` (rules + otherwise carried verbatim) |
| No rules + `otherwise = skip`  | `'pick-in'` |
| No rules + `otherwise = pick`  | `'weed-out'` (keep-all collapses here too — see below) |

**The keep-all collapse.** spec/90 §1.5 names a third sugar case:
"keep-all = no rules + Otherwise → pick + Picker session skipped". The
"Picker session skipped" hint isn't expressible in `CutDraft` today; the
adapter treats it as `weed-out` and lets the dialog layer a `skip_picker`
extras flag on top in Phase 4 if it wants the keep-all UX back. Recipes
themselves don't carry the hint — a Recipe replay always opens the
picker so the user can still curate.

**Source DC inference.** When the composition's source is exactly
`[("+", {"kind": "dc", "id": X})]`, the adapter populates the legacy
`CutDraft.source_dc_id` field. Anything more composed leaves it `None`
and the picker reads `expr` as the authoritative source.

The CRUD service [`RecipeStore`](../mira/shared/recipe_store.py) owns the
JSON encoding — callers pass and receive Python dicts for `composition`,
never raw JSON strings. The library's `UNIQUE (flavour, name)` surfaces as
a typed `RecipeNameTakenError` so the dialog can pattern-match without
touching `sqlite3`.

### 5.2 Person + Face (face recognition substrate)

Forward-compatible model for face recognition. **Not implemented in v1 of this
spec** — the schema spec/90 needs is small and additive, so future face
work doesn't reshape the dialog grammar.

| Entity | Scope | Where it lives | Columns landed |
|---|---|---|---|
| `person` | library | `mira.db` (schema v7 — extended) | `id`, `display_name`, `reference_photo_relpath`, `embedding_json`, **`representative_face_id`** (new), `created_at`, `updated_at`, `extras_json` |
| `face` | per item | `event.db` (schema v12 — new) | `id`, `item_id`, `person_id` (nullable for unrecognized), `bbox_x` / `bbox_y` / `bbox_w` / `bbox_h` (normalised 0..1), `confidence`, `detected_at` |

The Person table predates spec/90 (spec/53 §2.5 — the simplest face-recognition
tier) and is **extended** in Phase 1, not recreated: the only new column is
`representative_face_id`, an opaque pointer to a `face` row in event.db (no FK
spans stores; same shape as `photo_person.person_id`). `display_name`
substitutes for the spec's `name` field (kept verbatim; legacy callers and the
spec/53 dialog draft already write to it). No `UNIQUE` on `display_name` —
Phase 1 has no lookup-by-name path that benefits, and two People may
legitimately carry the same display name (different `id`).

```sql
CREATE TABLE face (
  id           TEXT PRIMARY KEY,
  item_id      TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  person_id    TEXT,                                   -- NULL = unrecognized
  bbox_x       REAL CHECK (bbox_x IS NULL OR (bbox_x >= 0 AND bbox_x <= 1)),
  bbox_y       REAL CHECK (bbox_y IS NULL OR (bbox_y >= 0 AND bbox_y <= 1)),
  bbox_w       REAL CHECK (bbox_w IS NULL OR (bbox_w >  0 AND bbox_w <= 1)),
  bbox_h       REAL CHECK (bbox_h IS NULL OR (bbox_h >  0 AND bbox_h <= 1)),
  confidence   REAL,
  detected_at  TEXT NOT NULL
);
CREATE INDEX ix_face_item   ON face(item_id);
CREATE INDEX ix_face_person ON face(person_id) WHERE person_id IS NOT NULL;
```

Person chips in the Recipe resolve via `SELECT item_id FROM face WHERE
person_id = ?`. The library-level scope means a Recipe's Person reference works
across events (a Person known in Alaska is known in Bali too).

### 5.3 Event Collections (saved event sets)

The cross-event analogue of a DC, at the event level. Same shape (formula +
operands + live resolution), different universe (events instead of items).

`#adventure_events`, `#wildlife_trips`, `#2018_2020_travel` are Event
Collections. They appear as chips in the Scope sentence of the Collection
dialog.

Implementing them is small: a new `event_collection` table with the same
expression-JSON shape as DCs, and an event-universe resolver. Tagging an
event is then "add it to a tag-named Event Collection" — no new column on the
event table needed (`spec/86` already catalogued the event-filter dimensions
this collection model unifies).

**Phase 1 schema (mira.db, schema v7):**

```sql
CREATE TABLE event_collection (
  id           TEXT PRIMARY KEY,
  tag          TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK (tag <> ''),
  expr_json    TEXT NOT NULL CHECK (json_valid(expr_json)),
  filters_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(filters_json)),
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  extras_json  TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extras_json))
);
```

`tag` carries `COLLATE NOCASE UNIQUE CHECK (tag <> '')` matching the existing
`dynamic_collection` / `saved_filter` pattern — the cross-event Recipe
grammar's named-operand resolution is case-blind. `filters_json` defaults to
`'{}'` so callers that only set an expression don't have to thread a literal.
`extras_json` joins as the standard escape hatch.

### 5.4 People Collections (future, parked)

When users routinely manage > 5–6 People per Recipe, named groupings appear
useful (`#the_kids = Pedro + Maria + Tom`). Same shape as Event Collections,
at the people level. **Not in scope for v1 of this spec.** Mentioned so the
dialog grammar leaves room.

### 5.5 Recipe portability across the Cut / Collection boundary

| Direction | Behaviour |
|---|---|
| **Cut Recipe applied to Collection** | Safe. No hidden filters; the Collection dialog opens with the Recipe's sections and the user can add Scope / hardware / Person rows. |
| **Collection Recipe applied to Cut** | Cut dialog can't display the Scope / Camera / Lens / Faces sections. The Recipe is **filtered out of the Cut dialog's Recipe pool by default**, with an opt-in setting *"show Collection Recipes here too"*. When the user opts in, a banner reads *"This Recipe filters by Camera = R5 + Faces = Pedro — not editable here. [Edit as Collection] [Apply anyway]"*. Strict-style honesty. |

**Phase 3 — the store-level seam.** `RecipeStore.list(flavour, include_other)`
implements the visibility policy. With `flavour='cut'` + `include_other=False`
(the default) the dialog sees only Cut Recipes; with `include_other=True` the
Collection Recipes append after, alphabetical within each flavour. The
service surfaces the data; the dialog applies the "show Collection Recipes
here too" toggle and the banner copy above. The adapter
([`recipe_to_cut_draft`](../mira/shared/recipe_draft_adapter.py)) refuses to
adapt a Collection-flavoured Recipe into a `CutDraft` — the cross-pollination
check is the dialog's policy (Phase 4) but the adapter fails loudly on
misuse so a wrong-shaped draft never reaches the picker.

---

## 6. Naming + entry points

The split between **Cut** (event-scope, share-facing) and **Collection**
(cross-event, curation-facing) earns its own noun. Both nouns refer to the same
underlying entity — a frozen, exportable set of items — but the audience and
the dialog face differ enough that one word would muddle.

| | Event scope | Cross-event scope |
|---|---|---|
| **Frozen output** | Cut | Collection |
| **Dialog** | New Cut | New Collection |
| **Saved Recipe** | Cut Recipe | Collection Recipe |
| **List surface** | Per-event Cuts (spec/61 §2) | Library Collections (new) |
| **Built-in source** | `#exported` (this event) | `#collected / #picked / #edited / #exported` (ladder rungs, in scope) |

The library Home screen gains a *Collections* tab (or section) next to
Events; spec/76 (home-library and Cut publishing) is the natural place to
detail that surface in a follow-up.

---

## 7. Implementation strategy

Sequencing is **strictly bottom-up** so each layer is verifiable in isolation.

### Phase 1 — data layer additions
- Schema: new `recipe` table (id, name, flavour, expression_json, created_at,
  updated_at). One row per saved Recipe.
- Schema: new `event_collection` table — same shape as `dynamic_collection`,
  events as the universe.
- Schema: new `person` table + `face` table (face stays empty in v1; the table
  exists so Person chips resolve to "no matches" leniently before recognition
  ships).
- Migration: existing Cuts gain no new columns; the rule-list model is
  read-only for legacy Cuts in v1 (legacy Cuts stay editable in the old
  dialog until backfilled).

### Phase 2 — resolver
- Extend `EventGateway.resolve_dc` (and a new `LibraryGateway.resolve_dc_cross_event`)
  to accept the Rule list and return a per-relpath `picked / skipped` map. New
  signature: `(source_expr, filters, rules, otherwise, scope) -> (pool, seed)`.
- Person chip resolves via the `face` table (empty in v1 → empty result, no
  error).
- Strict-reference guard: a missing named operand raises a typed error the
  dialog catches and reports.

### Phase 3 — Recipe persistence
- CRUD on `recipe` rows from a Recipe-pool service.
- Recipe → CutDraft / CollectionDraft adapter — translates the JSON to the
  draft shape the dialog instantiates from.
- The 2026-06-19 export-orphan healer (`scan_for_returns` Leg D, commit
  `64df266`) extends naturally to Recipe-driven re-exports.

### Phase 4 — the dialog widget
- Single `NewRecipeDialog` widget, configured by the four flags in §2.3.
- Reuses today's chip rendering (the redesigned `_PoolChip` in
  `new_cut_dialog.py`) for operands.
- Adds the rule-row layout (drag handle, index, predicate sentence, verdict
  pill, match count, delete) and the Otherwise row.
- Adds the verb popover and the join-word popover with plain-language meanings.
- Live metrics row at the bottom: in-pool count, initially-picked count,
  estimated runtime.

### Phase 5 — entry points
- Event-Cut dialog launches from today's *New Cut* button with the Cut
  configuration.
- Collection-flavour Start: **works as of Phase 4f**. The Collection face
  reaches the cross-event picker session via the existing
  [`CrossEventCutSession`](../mira/shared/cross_event_cut_session.py)
  + [`CrossEventPickerDialog`](../mira/ui/pages/cross_event_picker_dialog.py)
  through the new [`recipe_to_cross_event_cut_draft`](../mira/shared/recipe_draft_adapter.py)
  adapter. The dialog opens today from
  [`EventsPage._pin_cross_event_dc`](../mira/ui/pages/events_page.py) (the
  Pin → Cut button on a cross-event DC row), pre-seeded with that DC as
  the Source. The cross-event session resolves library-wide; scope chips
  in the composition are an accepted-but-not-yet-enforced hint, and rules
  collapse to `pin_mode` via the §1.5 sugar — a future phase will extend
  the cross-event session to honour scope-narrowing + rule-based seeding.
- **Still deferred** (the piece spec/90's earlier text conflated with
  "Collection Start works"): a **library-level New Collection action** —
  a discoverable surface in the home / library UI that opens the
  Collection face with no prefill. The dialog + engine are ready; only
  the entry point needs design (spec/76 update).
- *Save as Recipe…* in the dialog footer (both flavours).
- *Load Recipe…* in the dialog header (pool filtered by flavour, see §5.5).

### Phase 6 — face recognition (separate sprint)
- Detection pipeline (out of scope for spec/90).
- Person management UI.
- Recognition confidence threshold setting.

---

## 8. What this supersedes / reframes

- **spec/80 §2.4 — Build mode pill group.** Retired. The three pin modes
  fall out of rules + Otherwise as syntactic sugar (§1.5).
- **spec/80 §2.5 — start_as / default_state column.** Retired. Initial state
  is computed per-file by the resolver; no uniform default-state column needed.
  Old Cuts derive verdict from their stored `default_state` column for
  back-compat (§7 Phase 1).
- **spec/61 §2 — New Cut dialog.** Replaced by the two-flavour rule-list
  widget. The Picker session, flat grid, separators, audio, and export
  surfaces are unchanged.
- **spec/61 §8 — Cross-event Cuts (parked).** Unparked, renamed
  Collections, designed here.
- **spec/32 — Dynamic Collections.** Untouched. DCs remain the live item
  query and the operand of choice for "named, reusable predicate" needs.

---

## 9. Open questions parked for kickoff

These were deliberately not settled in the design session — they're
implementation details with low bearing on the model.

1. **Mode switching mid-edit.** Allow the user to flip a Cut Recipe into a
   Collection Recipe (or vice versa) without closing the dialog? Useful for
   "I realised I want this cross-event," surprising if the user fat-fingers it.
2. **Recipe versioning.** Should editing a Recipe spawn a new version (Git-style)
   or overwrite in place? In place is simpler; versioning helps users who share
   Recipes (out of scope for v1 anyway).
3. **The "save as DC" sub-flow.** When the user composes a multi-chip Source
   and clicks *Save as DC*, the sub-dialog needs to capture the DC name and
   any filters. Lightweight or full sub-form? Probably the former; defer.
4. **Collection list-page surface.** Where in the library does a user browse
   their Collections? spec/76 (home library) is the natural canvas; design
   needed.
5. **Re-base** (spec/80 §1.5 parked). When a referenced DC changes, a pinned
   Cut/Collection can offer a "re-pin against current data" action. Same
   parked status — not v1.
6. **Recipe sharing / export.** Recipes are library-level today but locked to
   the library. Sharing across libraries (export as JSON, import from JSON)
   is a future spec.

---

## 10. The worked example

To anchor the model, the original session's `#short` scenario, end-to-end in
the new dialog:

> **Name:** `short`
>
> **Source:** Start from `[#long (386)]`
>
> **Filters:** *(none)*
>
> **Rules:**
> 1. If items are in `[#blurry (18)]` → **skip** *(7 match)*
> 2. If items are in `[#best_wildlife (7)]` or `[#best_landscapes (10)]` → **pick** *(11 match)*
>
> **Otherwise** → **skip** *(368 fall through)*
>
> **Live metrics:** 386 in pool · 11 initially picked · 1:30 of 5:00 target

Pressing **Start** opens the Picker session pre-seeded with 11 items picked
and 375 items skipped. The user adjusts; on Save, the Cut is frozen with
whatever the user landed on. If they hit *Save as Recipe…*, the whole
configuration above (except the per-file decisions) lands in their Recipe
library, ready for the next `#short` they make on Bali, Costa Rica, or anywhere
else `#long`-style DCs and `#best_*` Cuts exist.

That's the model.
