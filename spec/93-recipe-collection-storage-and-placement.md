# spec/93 — Recipe / Collection storage & automatic placement

> **Vocabulary (Nelson 2026-06-21):** the nouns are **Collection · Recipe · Cut**.
> Where this doc says "Dynamic Collection" or "DC", read **Collection** — all new
> code and every user-facing string use "Collection" only. (The existing
> `DynamicCollection` model / `dynamic_collection` table keep their internal
> names; no schema rename.)

**Status:** design **agreed** with Nelson 2026-06-21 (design-mode session).
Captures where Dynamic Collections, Recipes, and Cuts live, and the
deterministic rule that decides it **automatically** so the user never chooses.
Extended the same day with the **filesystem recipe library** (§4) and the
**user-defined library root** it sits under (the root + relocation + recovery
are specced in [`spec/76`](76-home-library-and-cut-publishing.md)). **Refined the
same day:** a definition's identity is a stable internal **`id`**, not its
filename, so moving *or* renaming files in the OS file manager is always safe
(§4, §8) — an OS rename we cannot prevent must never dangle a reference.
**Implementation gated:** design only — coding agents wait for Nelson's word
(same gate as spec/81 / spec/90).

Reads on top of (revises the storage half of):
- [`spec/81`](81-dynamic-collection-and-cut.md) — the two-nouns / two-verbs
  engine (Dynamic Collection + Cut, pin + export). spec/93 adds *where each noun
  lives* and *how that is decided*; it changes neither the set algebra nor the
  DC↔Cut relationship. It **does** supersede §2's "cross-event DCs live in
  `mira.db`'s `saved_filter`": global definitions are now **JSON files** (§4).
- [`spec/90`](90-cut-recipes-and-collections.md) — the **Recipe** noun + the
  rule-list dialog. spec/93 is the storage half of spec/90's "Recipe… persisted
  at the library level, applicable across events," and adds the
  ingredient / recipe / dish framing the dialog should speak (§1).
- [`spec/32`](32-dynamic-collections.md) — the DC query dimensions. The
  dimensions still apply; the `saved_filter` storage home is superseded by §4.
- [`spec/76`](76-home-library-and-cut-publishing.md) — the **library root** the
  recipe trees and `.mira/` machinery live under, and the first-run / reinstall
  recovery flow that re-points to it.

---

## 1. The three nouns — one sentence each

| Noun | What it is | Lives… | State |
|---|---|---|---|
| **Dynamic Collection (DC)** | a reusable *ingredient* — a live set ("which files"): set algebra + filters, resolved on demand (spec/81 §2) | a JSON file in `Collections/` (§4) | **live** |
| **Recipe** | a reusable *procedure* — a DC-based pool **plus** pick/skip rules + budget (spec/90): "how to build a Cut" | a JSON file in `Recipes/` (§4) | **live** |
| **Cut** | a *dish* — the frozen materialisation of a DC/Recipe at one moment, optionally hand-finished in a Picker session (spec/81 §3) | the database, with its bytes (§3) | **frozen** |

Pipeline: **ingredient → recipe → dish.** A DC is the *Source* slice of a
Recipe; a Recipe cooked (pinned, optionally hand-picked) yields a Cut. DCs and
Cuts compose back in as operands, so it is a graph, not a line.

**Speak the metaphor in the UI.** The New Cut / save dialog (spec/90) should use
this language directly — the Source section is "your ingredients," the saved
configuration is a "Recipe," the result is a "Cut" (dish) — so the model lands
at the moment the user is composing.

## 2. Binding is the Cut — a definition has no "scope" attribute

A definition (DC or Recipe) is **always live and always relative.** It carries
no bound/free flag and no stored scope. The *only* thing that freezes a
selection — **including the events it spans** — is **pinning it into a Cut.**
The Cut records the freeze (`expr_snapshot_json`) and the scope it was frozen
against (`source_dc_kind`).

Consequence: "apply this recipe to a new event" is not a mode — it is just
cooking the same live recipe in a new context. **Same recipe, many dishes;**
each dish independent and frozen.

## 3. Storage principle — definitions are files, dishes are in the database

**The four universes (`#collected / #picked / #edited / #exported`) are a
*relative address scheme present in every event*, not an event-local asset.**
"`#picked`" means "the picked set of whatever event this is applied to." So any
definition built only from the universes + the portable filter dimensions
(spec/32 §2 — style, stars, EXIF, faces, date, location) is **portable:** it
resolves wherever it is applied, tied to no event.

From that, the homes:

- **Global definitions (DC, Recipe) → JSON files in the recipe library (§4)** —
  the `Collections/` and `Recipes/` folder trees under the user's library root
  (spec/76). They speak only the universal vocabulary, belong to no single
  event, and live where every event can see them. The event a definition is
  *authored in* is only its live preview, never its home.
- **Dishes (Cuts) → the database, where their bytes are.** An event's own Cut is
  a frozen set of *that event's* files → its `event.db` (`cut` / `cut_member`).
  A cross-event Cut spans events → the user store (`mira.db`), with
  `cut_member.event_id` pointing back into each source event (bytes never move;
  only references cross stores, and no FK can span stores).
- **The one exception — a definition that pins a concrete, single-event
  operand** (a reference to one event's Cut, or to a definition already bound to
  one event). It only resolves for that event, so it lives in **that
  `event.db`** rather than the global tree. Everything composed purely from the
  universal vocabulary stays a global file.

Why files for definitions, not the database: definitions proliferate with use
and the user needs to *organise* them. A folder tree the user shapes in their
own file manager is the organising mechanism — no in-app category store, no
management UI, arbitrary depth, human-readable and portable. The database keeps
what needs integrity and bytes — the dishes.

## 4. The recipe library on disk — folders, names, menus

```
<library_root>/                 (spec/76 — user-defined)
  Collections/                  one JSON file per Collection
    Wildlife/
      best-wildlife.json
    best-of-2024.json
  Recipes/                      one JSON file per Recipe
    Slideshows/
      short-highlights.json
      long-cut.json
```

- **One file per definition.** A Collection is a JSON file under `Collections/`;
  a Recipe under `Recipes/`. The file content is the definition (spec/81 expr +
  filters; spec/90 rule-list for recipes) plus a stable internal **`id`**. Two
  roots, not one typed tree, so the picker is unambiguous about what kind it is
  offering.
- **Identity is the internal `id`, not the filename.** Each file carries a stable
  `id` (a UUID); that is what references point at. The filename is the
  human-readable **display name**. So the folder *and* the filename are both
  presentation — the user can move, rename, or reshuffle files in their own file
  manager and nothing breaks, because the `id` does the holding. (This reverses
  the first draft's "filename = identity"; an OS rename we cannot prevent must
  never be able to dangle a reference.)
- **References are `{id, name}`, resolved by id, name as fallback.** A nested
  Collection operand and a Cut's source link carry both. Resolution uses the id;
  if the id is absent — e.g. a file a power user hand-authored by typing only a
  name — it falls back to the name and the app backfills the id on save, so
  hand-editing stays possible and the JSON stays readable.
- **Move and rename in the file manager are both safe.** On the next tree-scan
  the app reconciles: an `id` whose filename changed → adopt the new filename as
  the display name (the rename "takes," as the user expects) and refresh the name
  hints in referrers; an `id` no longer present → a deleted definition, surfaced
  as a graceful "missing ingredient" (§8). **Delete is the only unrecoverable
  act.**
- **Display names stay unique for menu clarity** — a soft scan-on-save warns on a
  duplicate name — but the `id` is the load-bearing key, so a duplicate name is
  non-fatal (references resolve by id).
- **Listing is a cached tree-scan**, invalidated on change. Writes are atomic
  write-then-rename (invariant #6) under the spec/76 single-writer lock.

**Presentation — menus mirror folders.** Anywhere a DC or Recipe is offered
(load a recipe, pick a DC as an operand), the list is a **cascading menu whose
sub-menus are the sub-folders**, of any depth, leaves are the definitions. The
user navigates their own structure to the one they want. No separate management
surface — the OS file manager is the management surface (create / move / delete
folders and files); Mira reads the tree. (A flat search/filter over the tree is
a reasonable enhancement for large libraries.)

## 5. Automatic placement — the classifier (no user choice, no ML)

The user never answers "where should this live?" Placement is **computed** from
what they composed, deterministically.

**What introduces a binding.** Walk the definition's operand closure (recursively
through nested DCs). The *only* operand that binds is a reference to **a single
event's Cut** (or to a definition already bound to one event). The universes are
relative (no binding); the portable filters carry no binding; a **cross-event**
Cut operand is a fixed frozen set (no *single*-event binding).

**The rule:**

```
bound_events = ⋃ over the closure of
               { the owning event of each single-event Cut/DC operand }

|bound_events| == 0  → GLOBAL          → a JSON file in the recipe library (§4)
|bound_events| == 1  → BOUND(event E)  → that event's event.db
|bound_events| >= 2  → CROSS-BOUND     → a JSON file in the recipe library
```

**Re-run on every save and edit.** If the classification flips — e.g. the user
removes the one concrete operand from a bound recipe — the definition **migrates
silently**: the JSON file moves into / out of the event's `event.db`,
atomically, never left in two places. The user touches no control; placement is
a *consequence* of the composition.

## 6. Load time — what's offered when creating a Cut in event E

The New Cut / load-recipe picker in event E offers exactly:

> **GLOBAL ∪ BOUND-to-E**

— the whole recipe library (its folder menus), plus the handful of definitions
specific to E — Definitions bound to a *different* event are correctly absent;
they could not resolve in E anyway. (A cross-bound definition appears in E only
if E ∈ its `bound_events`.)

## 7. Honesty without management — badges & the migration note

"Automatic" must not mean "opaque." Two **read-only** affordances:

- **A computed binding badge on every definition — *Global* vs *Event A*.**
  Derived from §5, never set by the user. It explains *why* a definition is (or
  isn't) available somewhere.
- **A quiet migration note** when an edit changes the class: "now specific to
  Event A — it'll only appear there," or the inverse "now reusable in any event."
  Not a prompt, not a blocker — just so an availability change is never a
  surprise.

The invariant that keeps this explainable: the answer to "why did this land here
/ show up there" is **always a concrete operand the user can point at** — never
a heuristic.

## 8. Integrity without a database (the cost of files, and how it's paid)

A database gives FK enforcement; the recipe files do not. The integrity story:

- **A Cut freezes its formula** (`expr_snapshot_json`, spec/81 / schema v8), so
  deleting, renaming, or editing a Collection/Recipe **never breaks a Cut**
  already made from it. This is the big one, already in the model.
- **Identity is an internal `id`, so the file manager is safe.** Move and rename
  hold every reference (the id is unchanged; the app reconciles the display name
  on scan, §4). **Delete** is the only act that can dangle a reference.
- **A missing operand fails gracefully.** If a Collection nests another and that
  operand's `id` no longer resolves (the file was deleted out-of-band), the
  resolver reports "missing ingredient: *Best wildlife*" in place — never a
  crash, never a silent empty.
- Display-name uniqueness is a **soft** scan-on-save check; the `id` is the hard
  key. Writes are atomic write-then-rename (invariant #6) under the spec/76
  single-writer lock.

## 9. Categorisation & the library surface — solved by the folder tree

Categorisation, left open in the first draft, is now answered by §4: the user's
own folder tree *is* the category system, and the cascading menu *is* the
browse/select surface. There is no separate category store and no in-app
management UI — the file manager creates and rearranges; Mira reads. What remains
to design under spec/76's Home/Library is the *surface that lists Cuts*
(the dishes, which live in the database) and the cross-event play/export over
them; the **definitions** are handled here.

## 10. Invariants (for the coding agents, when un-gated)

1. **No user-facing "where does this live" control.** Placement is computed
   (§5), never chosen.
2. A definition is **GLOBAL** unless its operand closure references a
   single-event Cut/DC; then it is **BOUND** to that event (≥2 → **CROSS-BOUND**
   → global file).
3. **Global definitions are JSON files** under `<library_root>/Collections/` and
   `/Recipes/` (spec/76); **bound** definitions live in their `event.db`;
   **dishes (Cuts)** live in the database (`event.db` or `mira.db`).
4. **One file per definition; identity is a stable internal `id`** (not the
   filename); references are `{id, name}` resolved by id with a name fallback;
   filename *and* folder are both presentation, so move/rename in the file
   manager never break a reference (an OS-rename's new name is adopted on scan).
   Display names stay unique for menu clarity (soft check). Menus mirror the
   folder tree.
5. **Re-classify on every write;** migrate the file ↔ `event.db` atomically if
   the class changed; never leave a definition in two places.
6. A Cut is **frozen** (`expr_snapshot_json`); editing/deleting/renaming the
   DC/Recipe it was pinned from never alters an existing Cut.
7. **Load set in event E = GLOBAL ∪ BOUND-to-E.** The binding badge is
   read-only, derived.
8. **Bytes never move;** only membership references (`cut_member.event_id`) span
   events. No FK spans stores. Recipe-file writes are atomic + lock-guarded.
