# spec/160 — Media Pool · Format · Cut (vocabulary simplification)

> **Status: DESIGN AGREED with Nelson 2026-06-30 (afternoon design-mode
> session). Implementation gated on the code audit + surface-plan work
> described in §9.**

Reads with (and revises):

- [`spec/81`](81-dynamic-collection-and-cut.md) — the two-nouns / two-verbs
  engine (Dynamic Collection + Cut, pin + export). spec/160 renames the
  first noun and splits the "third saved noun" spec/90 introduced.
- [`spec/90`](90-cut-recipes-and-collections.md) — the rule-list Recipe
  editor. spec/160 **retires the Recipe noun** and replaces the
  Cut/Collection dialog-flavour split with a single unified widget +
  two orthogonal saveable templates.
- [`spec/93`](93-recipe-collection-storage-and-placement.md) — automatic
  storage placement. spec/160 keeps the placement rule; the noun renaming
  and split apply to what gets placed.
- [`spec/32`](32-dynamic-collections.md) — Dynamic Collections + query
  dimensions. spec/160 replaces the "Dynamic Collection" UI label with
  **Media Pool**; the query dimensions in §2 are unchanged and still apply.
- [`spec/61`](61-share-event-cuts.md) — event-Cut surfaces. The Cut noun
  survives spec/160 unchanged; only its neighbours rename.
- [`spec/159`](159-exported-collection-review-and-classify.md) —
  spec/160 grew out of this session's design conversation about how the
  `FilterBar` widget should reuse into Cut-compose + cross-event
  surfaces. spec/159 lands unchanged; the FilterBar's contract as a
  Media Pool component is captured here.

---

## 0. Why this spec exists

The **Cut** noun works. Users grasp it instantly ("the cut of the film
I'll share").

The **Collection** noun is overloaded — it names both a live-resolving
saved query (the ingredient set) AND the frozen cross-event Cut output.
Two meanings, one word.

The **Recipe** noun conflates two orthogonal things — the media-pool
choices (Source + Filters + Rules) AND the presentation choices (aspect
ratio, duration, transitions, audio, overlays, separators). Users can't
save "just the pool" or "just the presentation" as a first-class thing;
they must save one bundled Recipe.

The Cut / Collection **dialog flavour split** (spec/90 §2.4) exists only
because Recipe is a bundled noun. Once Recipe splits into two orthogonal
templates, the flavour split disappears — one dialog widget serves both
scopes.

This spec locks the simpler model that fell out of the 2026-06-30 design
conversation.

---

## 1. The whole model

> **Three nouns.** Two saveable templates + one composition event.

| Noun | Role | Scope-aware? | State |
|---|---|---|---|
| **Media Pool** | Which items belong. Source + Filters + Rules. | **Yes** — event or library | **Live** — resolves against current data every time |
| **Format** | How the items are presented. Aspect ratio, duration, timing, transitions, audio, overlays, separators. | **No** — scope-free | **Reusable settings** — saved verbatim |
| **Cut** | The composition event: a Media Pool + a Format + the user's picked/skipped decisions from the Picker session. | Inherits Media Pool's scope | **Frozen** — snapshotted at pin time |

Everything below is detail on those three things.

### 1.1 Two faces, one narrower on purpose

A Cut is a Cut at both scopes — same three nouns, same engine, same
composition event. But the two jobs the dialog does are different, and
the surface adapts:

- **Event-scope Cut** — the everyday job. The user just came back from
  a trip, culled, edited, and shipped. They **know** the content. What
  they need from the dialog: pick which of the shipped photos go in,
  choose a Format, freeze. The mental question is *"which of my
  shipped shots make this share?"*
- **Library-scope Cut** — the power job. The user is searching a
  lifetime archive for a theme. The mental question is *"where are all
  the wildlife shots I love, across everything I've ever shot?"* The
  full spec/32 §2 facet catalogue (camera, lens, flash, ISO, focal
  length, temporal, location, faces) earns its keep here — those are
  the axes of the search.

**Principle.** The event face **deliberately hides** every widget that
doesn't help the shipping job — not to lock the user out but to keep
the common flow small and fast. Every hidden widget on the event face
is one fewer decision on the way to the Picker.

The library face carries the full catalogue. Same widget, same engine,
wider chrome.

This is not an implementation shortcut — it's a load-bearing design
choice that reflects how the two jobs genuinely differ. Full
field-by-field visibility matrix is in §6.2.

---

## 2. Media Pool (retires "Collection" as a UI noun)

A **Media Pool** is a **live-resolving named query** over media files.
Same engine as today's Dynamic Collection — spec/81 §2's set algebra
over operands, plus the spec/32 §2 filter catalogue, plus Rules (§4).
Only the UI label changes.

### 2.1 What a Media Pool holds

- **Source** — set-algebra sentence over operands: base universes
  (`#exported` / `#collected` / `#picked` / `#edited`), other Media Pools,
  and Cuts. Union (`or`) · difference (`but not in`) · intersection (`and`).
- **Filters** — single-facet narrowing over the spec/32 §2 catalogue
  (Style, Media, Camera, Lens, Faces, curatorial ratings, EXIF settings,
  temporal, location).
- **Rules** — compound predicate narrowing over the same operand
  vocabulary. Rules go *further* than Filters (§4).

**Rule-of-thumb difference:** Filters are one-facet-at-a-time
(`Style: macro`); Rules are set-algebra expressions (`in #best_wildlife
and rated ≥ 4`).

### 2.2 What a Media Pool does NOT hold

- **No pick/skip verdicts.** Rules are narrowing-only (§4). A Media Pool
  is "which items belong," never "which items start picked." That's the
  Cut's freeze.
- **No presentation choices.** Aspect ratio, timing, audio, overlays,
  separators — all live on the Format (§3), never on the Pool.
- **No frozen membership.** The pool is a live query; its resolution
  changes as data changes.

### 2.3 Scope — event vs library

A Media Pool lives at exactly one scope:

- **Event-scope pool** — operates against one `event.db`. Operands are
  the event's `#exported` (base) + other event-scope pools + Cuts.
  Placed in `event.db` (spec/93 §4).
- **Library-scope pool** — operates against the four-rung ladder
  (`#collected` / `#picked` / `#edited` / `#exported`) across scoped
  events. Operands span the library. Placed in `mira.db` (spec/93 §4).

Scope is **computed automatically** per spec/93's operand-closure rule:
if every operand pins to one event, the pool is event-scope; otherwise
library-scope. Users never pick this manually.

### 2.4 Composability

A Media Pool can be an **operand inside another Media Pool** — the
spec/81 §2 "saved DC stands in for parentheses" rule survives verbatim.
`all-time-best = best-wildlife or best-landscapes` is a Pool made of
two Pools.

### 2.5 Migration from "Collection" / "Dynamic Collection"

- `dynamic_collection` table (event scope) and `saved_filter` /
  `global_items` (library scope) keep their internal names — no schema
  rename.
- Every user-facing string ("Collection", "Dynamic Collection", "DC")
  becomes **Media Pool**.
- The `#tag` display convention survives: `#best_wildlife` reads the
  same whether the referent is a Media Pool or a Cut.

---

## 3. Format (retires the presentation half of "Recipe")

A **Format** is a **named preset of presentation choices** — everything
about *how* a Cut is delivered, independent of *which* media is in it.

### 3.1 What a Format holds

- **Aspect ratio** (spec/111) — 16:9 / 4:3 / square / vertical / …
- **Duration budget** — target minutes, max minutes (spec/61 §2).
- **Per-item timing** — seconds per photo; video handled per spec/144
  (its true on-disk duration).
- **Transitions** — style, per-transition duration (spec/152).
- **Separators** — day-boundary slides on/off + style (spec/61 §4;
  spec/143).
- **Audio** — playlist category selection (spec/61 §5.3;
  spec/112 cross-event parity).
- **Overlays** — which provenance fields draw on each frame
  (spec/81 §3.1 · spec/153 · spec/154).
- **Export defaults** — overwrite behaviour (spec/148), export-only-new
  policy (spec/158).

Any per-Cut settings the current codebase treats as recipe fields belong
here.

### 3.2 What a Format does NOT hold

- **No media identity.** A Format never names specific photos, clips, or
  pools. It's purely about *how*, never *which*.
- **No scope.** A Format works identically at any scope. There's no
  "event Format" vs "library Format" distinction.
- **No pick/skip verdicts.** Same reason as the Pool — that's the Cut's
  freeze.

### 3.3 Scope-free

A Format has no scope. A `10-min portfolio` Format applies equally to
an event Cut of Alaska or a library Cut across every wildlife trip.
Placement is always library-level (`mira.db`) per spec/93.

### 3.4 Migration from "Recipe"

- The existing `recipe` table survives internally — Format is stored
  there with `flavour = 'format'` (or the table's split-off equivalent
  after the audit in §9). Presentation columns come across verbatim;
  media-pool columns disappear.
- The Recipe noun retires from every user-facing string. "Cut Recipe" /
  "Collection Recipe" / "Save as Recipe" all rename to Format.

---

## 4. Rules — narrowing only, on the Media Pool

Nelson locked (2026-06-30): **Rules narrow the pool. They do NOT set
pick/skip state.** That's the Picker's job during the freeze.

This is a deliberate re-scoping from spec/90 §1.3, which paired every
Rule with a `pick` or `skip` **verdict**. spec/160 removes the verdict.

### 4.1 What a Rule is

A **Rule** is a set-algebra sentence over operands — same chip +
join-word grammar as Source (spec/90 §1.2, §3.1–§3.4). It resolves to a
set of items; that set narrows the pool.

Example: `Rule: in [#best_wildlife] or [#best_landscapes]` shrinks the
pool to items matching either of those Pools.

Multiple rules **compose** — every rule applies (implicit `and`); the
final pool is `Source ∩ Filters ∩ (Rule 1) ∩ (Rule 2) ∩ …`.

### 4.2 Filters vs Rules

Filters are the simple-facet version; Rules are the power-user version.
Both narrow. Neither freezes anything. A user who never touches Rules
still has Filters for everyday narrowing.

### 4.3 What the retired verdicts become

The old `pick_in` / `weed_out` / `keep_all` pin modes fell out of spec/90
§1.5 as syntactic sugar over rules + Otherwise. In spec/160:

- **`weed_out` / `keep_all`** (start everything picked, optionally
  skip a rule-matched subset) → the pool's default freeze state is
  **all-picked**, and the user drops specific items in the Picker.
  No Rule needed.
- **`pick_in`** (start everything skipped, pick the keepers) → the pool's
  default freeze state is **all-skipped**, and the user picks specific
  items in the Picker. No Rule needed.

The pin mode is now a **single toggle in the Cut dialog**: "start all
picked" vs "start all skipped." Rules narrow the pool; the toggle sets
the initial state. Clean separation.

---

## 5. Cut (unchanged noun, sharpened definition)

A **Cut is one specific slideshow arrangement.** A chosen list of photos
and videos + a Format + your picked/skipped decisions from the Picker,
frozen and zero-byte on disk until you export.

### 5.1 Composition — what a Cut binds together

Every Cut carries:

- **A Media Pool** — either named (loaded from a saved Pool) or
  composed ad hoc in the Cut dialog.
- **A Format** — either named (loaded from a saved Format) or
  composed ad hoc.
- **The Picker freeze** — for every item in the resolved pool, one
  of {picked, skipped, undecided}. Undecided rarely appears — most
  Cuts drive this to 0 through the Picker session, but the schema
  admits it (spec/61 §2's "skip the pin" case).

### 5.2 What a Cut is NOT

- Not a slideshow file. PTE renders the actual slideshow video from
  the Cut's exported directory.
- Not a template. There is no "Cut Template" — never needed. What's
  reusable is the Pool or the Format, not the composition event.
- Not scope-specific by name. `#best_alaska` and `#all_time_best` are
  both Cuts; the scope difference lives in their Pool.

### 5.3 The two verbs (unchanged from spec/81 §4)

- **pin** — Media Pool → Cut (freeze the resolution + record the Picker
  decisions).
- **export** — Cut → directory of links.

Separators, audio, overlays remain attachments (spec/81 §3.1) — now
described by the Format rather than the Cut.

---

## 6. One dialog, both scopes

spec/90 §2 committed to "one widget, two configurations" but described
them as the **Cut dialog** (event) and **Collection dialog**
(cross-event). spec/160 removes the flavour split — it's now literally
one dialog whose only difference is which chrome shows.

### 6.1 Sections

Every Cut dialog renders the same three sections:

1. **Media Pool** — Source + Filters + Rules, expressed in the spec/90
   chip + join-word grammar.
2. **Format** — presentation choices (§3.1).
3. **Freeze** — either the "start all picked / start all skipped" toggle
   + the Picker session (§4.3), or an "already picked / already skipped"
   pass loaded from a saved Cut.

### 6.2 Field-by-field visibility matrix

Per the §1.1 principle, the event face hides every widget that doesn't
help the shipping job. The library face carries the full catalogue.
Every field in the dialog belongs to exactly one row of this table —
the audit + surface-plan work in §9 must not introduce a fourth
state (partial visibility, conditional visibility, etc.). Two faces,
one matrix.

| Section | Field | Event face | Library face | Rationale |
|---|---|---|---|---|
| **Scope** | Selector (Events / Event Collections / date ranges) | Hidden (fixed to current event) | Visible | Event Cut = this trip, nothing to choose |
| **Media Pool → Source** | `#exported` (base universe) | Visible + default | Visible | The shipping set |
| **Media Pool → Source** | `#collected` / `#picked` / `#edited` (lower ladder rungs) | **Hidden** | Visible | Non-shipped items don't belong in a share; but do belong in a lifetime search |
| **Media Pool → Source** | Other Pools / Cuts as operands | This event's only | Library-wide | Operand inventory auto-filters (spec/90 §3.4) |
| **Media Pool → Filters** | Style (macro / portrait / landscape / …) | Visible | Visible | The one filter that matters for audience-facing shipping |
| **Media Pool → Filters** | Media type (photo / video) | Visible | Visible | Common everywhere |
| **Media Pool → Filters** | Curatorial ratings (stars / colour / flag) | Visible (spec/159 lineage-level) | Visible (spec/32 item-level; add lineage per §8 when the projection lands) | Ratings drive the "portfolio-only" lens at every scope |
| **Media Pool → Filters** | Camera | **Hidden** | Visible | An event is (mostly) one gear pool; filtering by camera adds no signal there |
| **Media Pool → Filters** | Lens | **Hidden** | Visible | Same reasoning |
| **Media Pool → Filters** | Flash on/off | **Hidden** | Visible | Same reasoning |
| **Media Pool → Filters** | ISO / Aperture / Shutter / Focal length (min-max) | **Hidden** | Visible | Technical facets — search-tool territory, not share-composition |
| **Media Pool → Filters** | Temporal (capture from / to) | **Hidden** | Visible | Event has its own date range implicitly |
| **Media Pool → Filters** | Location (country / city) | **Hidden** | Visible | Event has its own location implicitly |
| **Media Pool → Filters** | Faces (person multi-select) | **Hidden** (opt-in via user setting) | Visible (opt-in via user setting) | Face surface behind its own flag (spec/94 Phase 4b); scope-agnostic when on |
| **Media Pool → Rules** | Chip + join-word predicate composition | Visible; operand vocabulary = this event's Pools + Cuts + `#exported` | Visible; operand vocabulary = full library | Rules exist at both scopes; the operand pool is what differs |
| **Format** | *(every field — aspect ratio, duration, timing, transitions, audio, overlays, separators)* | **Identical** at both scopes | **Identical** at both scopes | Format is scope-free by definition (§3.3) |
| **Freeze** | "Start all picked / start all skipped" toggle + Picker session | **Identical** at both scopes | **Identical** at both scopes | Freeze is scope-free |

**Reading the matrix.** Roughly half the Media Pool filters are hidden
on the event face. Everything on the Format + Freeze sections is the
same. The dialog *feels* dramatically smaller on the event face — and
that's the point.

If a future spec proposes exposing a currently-hidden field on the
event face, it must argue against §1.1: what shipping job does that
widget help? "Nice to have" is not an answer.

### 6.3 Save-as actions

The dialog surfaces two independent save actions:

- **Save as Media Pool** — captures Source + Filters + Rules from
  wherever the dialog is now. Names + placement determined per spec/93.
- **Save as Format** — captures the Presentation section. Named +
  library-placed always.

There is no "Save as Cut Template" — clicking Save simply commits the
Cut itself, with its own name.

### 6.4 Load actions

Symmetric to §6.3:

- **Load Media Pool** — prefills Source + Filters + Rules. Existing
  Presentation choices survive.
- **Load Format** — prefills Presentation. Existing Media Pool
  survives.

The user can load one, both, or neither, in any order.

---

## 7. Vocabulary retirements

| Retired UI noun | Replaced by | Where used today |
|---|---|---|
| "Collection" (Dynamic Collection) | **Media Pool** | Event + library `#tag` operands, DC editor |
| "Collection" (frozen cross-event output) | **Cut** (library-scope) | Cross-event assembly UI |
| "Cut Recipe" / "Collection Recipe" | Split → **Media Pool** + **Format** | spec/90's flavoured Recipe schema |
| "Recipe" (as UI noun) | Doesn't exist | Every "Save as Recipe" / "Load Recipe" affordance |
| "Cut Template" (any usage) | Doesn't exist | N/A — was never a first-class noun anyway |
| pin-mode pill (`pick_in` / `weed_out` / `keep_all`) | Single toggle + narrowing rules | spec/90 §1.5 |

Schema tables (`dynamic_collection`, `saved_filter`, `recipe`, `cut`,
`cut_member`) keep their internal names. Only user-facing strings and
public gateway API names change.

---

## 8. Sentences at the counter (target user-facing copy)

Every noun does one job. These sentences read cleanly:

> *"I'll make a **Cut** for Alaska using my `best-wildlife` **Media
> Pool** and my `10-min portfolio` **Format**, then run the Picker to
> freeze which shots make it in."*

> *"My `all-time-best` **Media Pool** is built from `best-wildlife or
> best-landscapes`. It resolves to 240 items right now — the Portfolio
> **Format** trims that to 10 minutes."*

> *"Save this Media Pool → I can reuse it in any future Cut."*

> *"Save this Format → next time I make a Cut for a new event, I'll load
> it and only pick the Media Pool."*

> *"There's no such thing as a Cut Template — a Cut is one specific
> composition event. What's saveable are its parts."*

---

## 9. Implementation gating — the audit + surface-plan step

**Implementation is gated on this two-step planning work.** No code
changes land until both are done and Nelson has signed off.

### 9.1 Code audit

Walk the code with the new vocabulary in mind and produce a map:

- Every user-facing string mentioning "Collection", "Recipe", "DC",
  "Dynamic Collection" — categorised by which new noun it becomes.
- Every gateway API name mentioning those terms — categorised the
  same way. Note breaking-change candidates.
- Every dialog + surface that renders any of the retiring nouns —
  list of files, entry points, current behaviour, target behaviour.
- Every schema table + column mentioning the old nouns — decide which
  keep internal names (recommended: all) and which need cascading
  compatibility shims.

Deliverable: a single audit doc listing every touchpoint + its
new-vocabulary target.

### 9.2 Surface plan

Design the target surfaces before touching code:

- **The unified New Cut dialog** — layout mock, feature-flag matrix
  for the two scope configurations, chrome sections.
- **Media Pool editor** — reusing the FilterBar (spec/159 §4.5) for the
  Filters section; how Rules render in the chip + join-word grammar.
- **Format editor** — where every presentation setting lives; how
  overlays / audio / transitions each get their own group box.
- **Cuts list** — how Cuts, Media Pools, and Formats coexist in the
  navigation.
- **Load / Save flows** — the two independent Load Media Pool / Load
  Format buttons; the two independent Save-as actions.

Deliverable: mockups or spec-doc mocks for every surface, cross-linked
with the audit doc.

### 9.3 Phased implementation

Once §9.1 and §9.2 land and Nelson signs off, break implementation into
phases that keep the app buildable at every step. Suggested shape
(subject to audit findings):

1. **Vocabulary sweep** — user-facing strings only. Zero behavioural
   change. Ships a rename PR.
2. **Format split** — carve the presentation columns out of `recipe`;
   introduce Format save/load actions; keep Recipe API alive as a
   compatibility shim.
3. **Unified dialog** — collapse the two-flavour dialog into one widget
   + feature flags.
4. **Rules re-scoping** — retire verdict-carrying rules; introduce the
   "start all picked / start all skipped" toggle.
5. **Compatibility shim retirement** — drop the Recipe API once every
   caller is migrated.

Each phase is its own spec-sized commit + eyeball.

---

## 10. What stays open

- **Naming polish.** "Format" is locked as the second saveable template
  noun. "Media Pool" is locked as the first. If in-app eyeball reveals
  either reads wrong in a specific UI slot, the noun stays; only the
  local label wording adjusts.
- **The "start all picked / start all skipped" toggle** — is this a
  Cut-level attribute or a Format-level one? Argument either way. My
  gut: **Format** — it's part of the audience-tuning ("family = start
  all picked, edit later"; "portfolio = start all skipped, curate").
  Confirm at audit time.
- **Migration of legacy Cuts with retired pin modes.** Every existing
  Cut that used `pick_in` / `weed_out` / `keep_all` needs to map onto
  the new toggle + rule model. Additive-only migration recommended;
  design at audit time.
- **Cross-event scope for Rules with an event-scoped operand.** If a
  Rule references `#best_alaska` (event-scope) inside a library-scope
  Pool, does the Rule resolve empty for non-Alaska events, or does the
  Pool refuse to load? spec/90 §1.4 chose the strict rule for named
  references; spec/160 defaults to the same and asks the audit to
  confirm.
- **spec/159 §8 (cross-event projection of lineage ratings)** — still
  open. spec/160's Media Pool + Rules model gives cross-event pools a
  richer expression vocabulary, which makes the missing lineage-rating
  projection more painful. Worth closing in the same overhaul.

---

Nelson 2026-06-30 — design session captured. Spec lands; code follows
the audit + surface plan (§9).
