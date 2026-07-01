# spec/160 vocabulary migration audit

> Deliverable of [spec/161](../spec/161-vocabulary-migration-audit.md).
> Read-only map of every touchpoint the [spec/160](../spec/160-media-pool-format-cut.md)
> vocabulary shift moves. **No code changes land until this doc is
> complete + Nelson signs off** (spec/160 §9, spec/161 §5).

Target vocabulary (spec/160 §7):

| Retiring UI noun | Replaced by |
|---|---|
| Collection / Dynamic Collection / DC (as live query) | **Media Pool** |
| Collection (as frozen cross-event output) | **Cut** (library-scope) |
| Cut Recipe / Collection Recipe / Recipe (as UI noun) | Split → **Media Pool** + **Format** |
| Save/Load DC | Save/Load **Media Pool** |
| Save/Load Recipe | Split → Save/Load **Media Pool** + **Format** |
| Cut Template | Doesn't exist |
| pin-mode pill (`pick_in` / `weed_out` / `keep_all`) | "Start all picked" / "Start all skipped" toggle |

Schema tables, model classes, and internal symbol names stay unchanged
(spec/93 §4). Only user-facing strings + public gateway APIs move.

---

## 0. Summary

### The story

spec/160 defines one flow:

> **Compose a Media Pool → apply a Format → pin the pool into a Cut.**

Same three steps at event scope and library scope. §6.2 controls
which widgets show at each scope. Every widget in the target UI
already exists in the canonical spec/92 role catalog — the migration
is a **rename + regroup**, never a "new widget."

Two saveable templates fall out of the flow:

- **Media Pool template** — a saved Source + Filters + Rules
- **Format template** — a saved set of presentation choices

The bundled "Recipe" template retires. No "Cut template" exists (a
Cut is a composition event, not a template).

### Hits per subsection

| Subsection | Scope | Hits reconciled |
|---|---|---|
| §1.1 User-facing strings | `mira/ui/` `tr()` + widget setters | **114 sites across 13 files** (of ~2,500 raw vocab occurrences; the rest are variable names / comments / method calls handled by §1.2–§1.6) |
| §1.2 Gateway APIs | `mira/gateway/` public surface | **~40 public methods** + **~4 facade properties** + **~6 private helpers**; **~217 call sites** ride the shim strategy |
| §1.3 Dialogs / surfaces | UI surfaces that render the flow | **13 major surfaces** mapped as one flow-story; component vocabulary is entirely spec/92 canonical |
| §1.4 Schema tables + columns | `mira/store/` + `mira/user_store/` | **1 substantive table decision** (`recipe` → narrow to `flavour='format'`, option A); all other tables keep their names per spec/93 §4 + spec/160 §2.5 |
| §1.5 Tests | `tests/` | ~120 files reference retiring vocab; **~36 need phase-1 edits**, the rest ride the gateway shim; `test_collection_vocabulary.py` is the guard that flips |
| §1.6 Docs + specs | `spec/`, `docs/`, `CLAUDE.md` | ~35 live specs need updates; 3 need new "superseded by spec/160" banners; handovers stay historical |

### Breaking-change candidates (direct-rename tag)

Zero rename lands as a direct hard break — every gateway method uses
`shim + deprecation window` per §1.2.0. The one place a hard break is
recommended is the `Recipe` bundle store operations: **there is no
`save_recipe(...)` after phase 2 — callers must use
`save_media_pool(...)` + `save_format(...)`.** That's one PR.

### Docs that need a "revised by / superseded by spec/160" banner

Three new banners:

1. `spec/80-cut-construction-model.md` — superseded by spec/81 +
   spec/160.
2. `spec/90-cut-recipes-and-collections.md` — noun model retired by
   spec/160; engine + grammar survive.
3. `spec/94-collections-recipes-cuts-roadmap.md` — roadmap superseded
   by spec/160 §9.

### Surprises — proposed spec/160 amendments

The audit turned up three edits spec/160 should absorb before phase-1
implementation begins. Detail in §7.

1. **§7 tone-Recipe carve-out.** Q5 answer: tone-Recipes (spec/54
   §8's `recipe_json`) survive as a separate noun. spec/160 §7 must
   say this explicitly to prevent an over-eager sweep.
2. **§6.1 acknowledges two current composers.** Today
   `NewRecipeDialog` (two-flavour) + standalone `NewCrossEventDcDialog`
   both compose Media Pools. spec/160 §6.1 says "one dialog"; the
   audit surfaces that this is a genuine consolidation task, not a
   trivial reshape. Deferrable to §9.2 surface plan.
3. **§4.3 pin_mode collapse is heavier than the spec reads.** Four
   engine paths collapse to a boolean toggle plus a Rule-verdict
   retirement. spec/160 §9.3 phase 4 should call this out as the
   highest-risk phase.

### Ambiguity list for Nelson

See §7 — six open items, all deferrable to spec/160 amendment or to
the §9.2 surface plan. None block phase 1 vocabulary sweep.

### What Nelson does next

1. Read §1.3 (the flow map) — this is the "target UI" picture.
2. Read §7 — six open items. Answer or defer each.
3. Confirm §1.4 option A (keep `recipe` table, narrow flavour to
   `'format'`).
4. Confirm §1.2's shim + deprecation strategy (~40 gateway methods
   move as their owning surface migrates).
5. Sign off — either in a commit message on this doc or a note at
   the top.

Post-sign-off, spec/160 §9.3 phase 1 (user-facing string sweep) can
begin. The audit doc becomes the checklist implementation walks
through; migrated items get ticked ✓ per spec/161 §6.

---

## §1.1 User-facing strings

Every string the user reads that mentions a retiring noun. Structured
as **pattern rules** (the replacement contract) + a **per-file
checklist** (where the strings live).

**Scope note.** The raw `git grep -in` count against retiring vocab in
`mira/ui/` is ~2,500 hits. **Most of those are variable names, comments,
docstrings, method calls, or non-`tr()` string literals** — captured
elsewhere (§1.2 gateway, §1.4 schema, §1.6 docs). The **user-facing
string surface** — `tr(…)` calls + `setWindowTitle` / `setText` /
`setToolTip` / `setTitle` with a literal string — comes out to **~114
sites across ~13 files**.

### §1.1.1 Pattern rules — the replacement contract

Every user-facing string categorises into exactly one of these rows.
The implementation phase walks each site, matches it to a row, and
applies the target. A site that matches none of these gets the
`unresolved — Nelson decides` flag (see §1.1.3).

| # | Current pattern | Target | Confidence | Where it lives (typical) |
|---|---|---|---|---|
| P1 | `"Collection"` / `"Collections"` (as UI noun, Dynamic Collection sense) | `"Media Pool"` / `"Media Pools"` | certain | Tab labels, band titles, dialog window titles, list-empty text |
| P2 | `"Dynamic Collection"` / `"DC"` (abbreviation) | `"Media Pool"` (never abbreviate as "Pool" in user copy — full noun always, spec/160 §7) | certain | Hints, tooltips, secondary explanations |
| P3 | `"Recipe"` (as UI noun, Cut/Collection Recipe sense) | Split: `"Media Pool"` if the string is about the pool half, `"Format"` if about presentation; **rewrite** if the string references the bundled thing (per Q5, verify each hit is not tone-Recipe first) | needs review | Toolbar labels, Save/Load buttons, list bands |
| P4 | `"Cut Recipe"` / `"Collection Recipe"` | Split → `"Media Pool"` + `"Format"` per spec/160 §7; sentence usually needs rewrite | needs review | Descriptive hints and error messages |
| P5 | `"Save as Recipe"` / `"Save as Recipe…"` | Split into two independent affordances: `"Save as Media Pool"` + `"Save as Format"` (spec/160 §6.3) | certain | Toolbar/button labels |
| P6 | `"Load Recipe"` / `"Load Recipe…"` | Split into two independent affordances: `"Load Media Pool"` + `"Load Format"` (spec/160 §6.4) | certain | Toolbar/button labels |
| P7 | `"Save as Collection"` / `"Save as DC"` | `"Save as Media Pool"` | certain | Save-modal button labels |
| P8 | `"Load Collection"` / `"Load DC"` | `"Load Media Pool"` | certain | Load-modal button labels |
| P9 | `"New Collection"` (window title) | `"New Cut"` at library scope (per Q1 answer — the file renames but the surface still composes a Cut at library scope; §6.1 says one dialog title at both scopes) | certain — post-Q1 | Dialog window titles |
| P10 | `"Cross-event Cut"` / `"Cross-event Cuts"` (surface chrome) | `"Cut"` / `"Cuts"` — scope is carried by the Media Pool per spec/160 §5.2 | certain | Dialog titles, list surface headers |
| P11 | `"Cut Template"` | Doesn't exist per spec/160 §7 — retire the string; if the surrounding sentence needs rewriting because the concept vanishes, rewrite | certain | Unclear where used in `mira/ui/` — grep may find 0 |
| P12 | `pin_mode` pill labels (`"Keep all"` / `"Weed out"` / `"Pick in"` / `"Rule-based"`) | Replaced by single toggle: `"Start all picked"` / `"Start all skipped"` (spec/160 §4.3) — the four-way pill retires | certain | Cut dialog's Otherwise section |
| P13 | `"Recipe"` in `mira/ui/edited/` — tone-Recipe (spec/54) | **Keep** — Q5 answered, tone-Recipes survive spec/160 | certain | `adjustment_surface.py`, `_lineage.py`, `photo_viewport.py` |
| P14 | `"Recipe"` referring to a **`recipe_json` archival snapshot** (spec/54 §8) | **Keep** — same as P13 territory. Verify at each site. | needs review | Any surface exposing lineage's recipe_json field to the user |

### §1.1.2 Per-file checklist

Sorted by user-facing string count (descending). Numbers are the count
of `tr(…)` + `setWindowTitle(…)` / `setText(…)` / `setToolTip(…)` /
`setTitle(…)` sites with a literal string containing retiring vocab.

| File | tr() sites | setText/Title/Tooltip sites | Total | Dominant patterns | Notes |
|---|---:|---:|---:|---|---|
| `mira/ui/pages/new_recipe_dialog.py` | 40 | 13 | 53 | P3, P5, P6, P7, P8, P9, P12 | The load-bearing surface. Recipe toolbar label ("RECIPE"), Save/Load Recipe + Save/Load Collection sub-dialogs, dialog title ("New Cut" / "New Collection"), Otherwise section verdict pill. See §1.3 step 4 for the modal sub-dialogs. |
| `mira/ui/pages/share_cuts_page.py` | 8 | 1 | 9 | P1, P10 | Collections tab label, "Delete Collection" modal title, "Delete Cut" title stays, "Rename Cut" title stays. |
| `mira/ui/pages/library_page.py` | 8 | 0 | 8 | P1, P3 | Bands: "Collections" → "Media Pools", "Recipes" → "Formats" (per Q3). |
| `mira/ui/pages/cross_event_dcs_dialog.py` | 6 | 1 | 7 | P1, P9 | Window title "Collections" → "Media Pools"; row row-count text. |
| `mira/ui/pages/new_cross_event_dc_dialog.py` | 2 | 1 | 3 | P1, P9 | Window title "New Collection" → "New Media Pool" (or the dialog retires per §7 Q1). |
| `mira/ui/shared/dc_detail_page.py` | 2 | 0 | 2 | P1 | Body strings; the surface's object name is already `PoolDetailPage`. |
| `mira/ui/exported/staleness.py` | 2 | 0 | 2 | P1 or P3 | Verify pattern per hit — likely P1 ("Exported Collection" chrome per spec/159 that must retitle). |
| `mira/ui/pages/cross_event_cuts_dialog.py` | 1 | 0 | 1 | P10 | Window title "Cross-event Cuts" → "Cuts". |
| `mira/ui/pages/cross_event_picker_dialog.py` | 1 | 0 | 1 | P10 | Title "Pin cross-event Cut — {name}" → "Pin Cut — {name}". |
| `mira/ui/wizard/first_run_library.py` | 1 | 0 | 1 | P1 or P3 | First-run onboarding copy. |
| `mira/ui/pages/gear_profile_wizard.py` | 1 | 0 | 1 | P1 | Verify — likely a stray hit. |
| `mira/ui/shell/main_window.py` | 1 | 0 | 1 | P1 or P3 | Menu label / status message. |
| `mira/ui/pages/events_page.py` | 1 | 0 | 1 | P1 | Toolbar affordance. |
| **Subtotal** | **98** | **16** | **114** | | |

Plus low-count files without `tr()` hits but potentially carrying
literal strings elsewhere (checked separately in the implementation
phase via a strict per-file grep):

| File | Retiring vocab? | Likely patterns |
|---|---|---|
| `mira/ui/exported/filter_bar.py` | Yes (3 Collection hits, 1 DC) | P1, P2 — hints/tooltips |
| `mira/ui/exported/review_dialog.py` | Yes (2 Collection hits) | P1 |
| `mira/ui/exported/preview_dialog.py` | Yes (6 Recipe hits) | **Verify at each site** — likely P13 (tone-Recipe) not P3. Was in earlier ambiguity Q5. |
| `mira/ui/pages/_filter_family.py` | Yes | P1 |
| `mira/ui/pages/_cross_event_band.py` | Yes | P10 |
| `mira/ui/pages/facet_picker_dialog.py` | Yes | P1 |
| `mira/ui/pages/past_photos_dialog.py` | Yes | P1 |
| `mira/ui/edited/adjustment_surface.py`, `mira/ui/edited/_lineage.py`, `mira/ui/edited/photo_viewport.py` | Yes (Recipe hits) | **P13 (tone-Recipe) — keep** per Q5 |
| `mira/ui/shell/sidebar.py`, `mira/ui/base/binding_badge.py`, `mira/ui/base/cascading_tree_menu.py`, `mira/ui/base/settings_dialog.py`, `mira/ui/read_only.py` | Small counts | Mostly P1 / P2 in menu labels / tooltips |
| `mira/ui/design/thumbs.py`, `mira/ui/design/thumb_grid.py`, `mira/ui/design/blurred_backdrop.py`, `mira/ui/design/blurred_photo_canvas.py` | 1–3 hits each | Likely comments — verify not user-facing |
| `mira/ui/media/photo_viewport.py`, `mira/ui/media/photo_cache.py` | 1–2 hits each | Likely comments |
| `mira/ui/exported/batch.py` (31 Recipe hits), `mira/ui/exported/__init__.py`, `mira/ui/exported/staleness.py`, `mira/ui/exported/collision_dialog.py` | Recipe hits | **Verify each site.** Batch export references the archival `recipe_json` (P14) — keep. Any user-visible "Recipe" copy retires per P3. |

### §1.1.3 Ambiguous strings — Nelson decides

Sites that don't match a P1–P14 rule cleanly. These get the
`unresolved — Nelson decides` flag in the working audit; the resolution
lands in spec/160 §7 as an amendment or in the surface plan (§9.2).

- **The `pin_mode` sub-labels beyond the four pill values.** If any UI
  copy names a specific pin_mode elsewhere (helper text, error
  messages), it needs the same collapse — but the target wording is a
  copywriting decision, not a mechanical rename. Deferrable to §9.2.
- **The "Show {other} Recipes too" checkbox** on `LoadRecipeDialog`
  (`new_recipe_dialog.py` line 1296). Under spec/160 the checkbox
  itself retires (no cross-flavour concept once Recipe splits). Verify
  no code depends on the option.
- **Any string that mentions "Recipe" without specifying which of the
  three Recipe meanings** (Cut/Collection Recipe → retire; tone-Recipe
  → keep; `recipe_json` archival → keep). Every such site gets a
  disambiguation pass in phase 1 rather than a blind rename.

### §1.1.4 Reconciliation

The `git grep -in` counts (case-insensitive) that this section covers:

- `tr(…)` + widget-setter sites in `mira/ui/`: **114 user-facing sites**
  reconciled here (§1.1.2 table).
- Remaining raw vocabulary hits in `mira/ui/`: **~2,400** — non-user-
  facing (variable names, comments, docstrings, method calls). Not
  §1.1's scope; captured by §1.2 (gateway calls), §1.4 (schema/model
  imports), or §1.6 (docstrings that read like tutorials).
- **Tone-Recipe territory** (`mira/ui/edited/*`): **~30 Recipe hits**
  under P13, marked `keep` and dropped from the retirement bucket per
  Q5.

Reconciled 1:1 per spec/161 §5.1.

---

## §1.2 Gateway APIs

Public gateway methods / signals / dataclasses carrying the retiring
vocabulary. The seam between UI (moves to spec/160 vocabulary) and
storage (keeps names per spec/93 §4). Per spec/160 §7 the API side of
the seam moves.

Strategy legend: `direct rename` (all callers migrate in one PR) ·
`shim + deprecation window` (old name kept as a thin wrapper for one
phase) · `keep name, rename user-facing only` (schema / model classes).

### §1.2.0 Scale check

`git grep` over `mira/` + `tests/` for the DC method surface alone
(`dc_probe`, `resolve_dc`, `dynamic_collections`, `create_dc`,
`update_dc`, `delete_dc`, `rename_dc`, `dc_by_tag`, `dc_expr`,
`dc_filters`, `dc_operand_inventory`, `dc_show_totals`) finds **~217
call sites across ~26 files** (93 in `mira/`, 124 in `tests/`). Direct
rename in one PR is impractical at that scale. **The whole gateway
migration should use `shim + deprecation window`** so the vocabulary
sweep (spec/160 §9.3 phase 1) can land as a strings-and-facade-only
change; caller sites migrate as their owning surface moves.

The Recipe method surface (`resolve_recipe`, `recipe_store`,
`collections_library`, `recipes_library`, and friends) is much smaller:
**~21 call sites across ~11 files**. Direct rename is feasible here.

### §1.2.1 Cluster A — DC methods (event + library, mirrored)

Symmetric API on `EventGateway` (`mira/gateway/event_gateway.py`,
class starts line 96) and `LibraryGateway` (`mira/gateway/library_gateway.py`,
class starts line 97). Same method names, one operates on
`dynamic_collection`, the other on `saved_filter`. Both are Media Pool
substrate — the whole cluster is the Media Pool CRUD + resolve surface.

| Current name (both gateways) | Target name | Strategy |
|---|---|---|
| `dynamic_collections()` | `media_pools()` | `shim + deprecation window` |
| `dynamic_collection(id)` | `media_pool(id)` | `shim + deprecation window` |
| `dc_by_tag(tag)` | `pool_by_tag(tag)` | `shim + deprecation window` |
| `dc_expr(dc)` (staticmethod) | `pool_expr(pool)` | `shim + deprecation window` |
| `dc_filters(dc)` (staticmethod) | `pool_filters(pool)` | `shim + deprecation window` |
| `create_dc(...)` | `create_pool(...)` | `shim + deprecation window` |
| `update_dc(...)` | `update_pool(...)` | `shim + deprecation window` |
| `rename_dc(...)` | `rename_pool(...)` | `shim + deprecation window` |
| `delete_dc(...)` | `delete_pool(...)` | `shim + deprecation window` |
| `resolve_dc(...)` | `resolve_pool(...)` | `shim + deprecation window` |
| `resolve_dc_keys(...)` (library only) | `resolve_pool_keys(...)` | `shim + deprecation window` |
| `dc_probe(expr, ...)` | `pool_probe(expr, ...)` | `shim + deprecation window` |
| `dc_operand_inventory()` | `pool_operand_inventory()` | `shim + deprecation window` |
| `dc_show_totals(...)` | `pool_show_totals(...)` | `shim + deprecation window` |
| `_check_dc_cycle(...)` (private) | `_check_pool_cycle(...)` | Rename with caller sweep — private so no external shim needed. |
| `_operand_dc(ref)` (private, event only) | `_operand_pool(ref)` | Same as above. |
| `_apply_dc_filters(...)` (private, event only) | `_apply_pool_filters(...)` | Same. |

**Caller distribution** (grep count against `mira/` + `tests/`):
`share_cuts_page.py` 22 · `events_page.py` 21 · `cross_event_dcs_dialog.py`
11 · shared/`cut_session.py` 5 · `library_page.py` 1 · engine sites in
gateway itself and cross-event helpers. Test callers concentrate in
`test_library_gateway.py` (33), `test_gateway_cuts.py` (28),
`test_gateway_definition_library_swap.py` (17), `test_resolver_file_library.py`
(12).

**Migration order** (matches spec/160 §9.3 phasing):
1. Land the shim: add `pool_*` wrappers that delegate to the existing
   `dc_*` methods on both gateways. Zero behaviour change.
2. Migrate the UI surfaces in one PR per surface. Each PR renames its
   own call sites from `dc_*` to `pool_*`.
3. Migrate test files in the same PR as their protected surface.
4. When zero `dc_*` call sites remain in `mira/` + `tests/`, drop the
   shims. This is the compatibility-shim-retirement phase (spec/160
   §9.3 phase 5).

### §1.2.2 Cluster B — Recipe methods

These handle the composed **Recipe** (pool + presentation). Under
spec/160 the Recipe noun retires; these methods either rename (the
"resolve the pool half" ones) or retire outright (the "compose the
whole bundle" ones).

| File · Symbol | What it does today | Target |
|---|---|---|
| `event_gateway.py::resolve_recipe(...)` (line 1610) | Resolves a Recipe (pool + narrowing rules) to a set of item IDs. | Rename to `resolve_pool_from_recipe(...)` during the migration window as a shim; then either `resolve_pool(...)` when the resolve-pool method is enough, or a fresh `resolve_cut_composition(...)` if the caller genuinely needs pool + rules + verdicts. **Deferrable to phase 2** — the audit's job is to name the touchpoint. |
| `event_gateway.py::_check_recipe_operand(...)` (line 1527) | Validates operand references inside a Recipe. | Private helper; rename to `_check_pool_operand` alongside cluster A migration. |
| `event_gateway.py::_recipe_dc_expr_by_ref(...)` (line 1568) | Recipe → DC-expression translator. | Private; either retires (Recipe bundle retires) or renames to `_pool_expr_by_ref` if the "pool-expression-from-reference" job survives. |
| `library_gateway.py::resolve_recipe(...)` (line 728) | Same job at library scope. | Same strategy. |
| `library_gateway.py::_check_recipe_operand(...)` (line 618) | Same. | Same. |
| `library_gateway.py::_recipe_dc_expr_by_ref(...)` (line 667) | Same. | Same. |
| `library_gateway.py::_cev_dc_lookup(...)` (line 681) | Cross-event-view DC lookup. | Private; rename to `_cev_pool_lookup` alongside cluster A. |
| `gateway.py::recipe_store` (property, line 529) | Returns the Recipe store facade. | During migration: keep as shim. Post-migration: rename to `format_store` (if the store's job narrows to Formats only per §1.4.1 option A) or split into `media_pool_store` + `format_store`. Load-bearing property with 21 total call sites — direct rename is feasible. |
| `gateway.py::collections_library` (property, line 389) | The library-scope Collections facade. | Rename to `media_pool_library` (or `pools_library`). |
| `gateway.py::recipes_library` (property, line 401) | The library-scope Recipes facade. | Post-migration: rename to `formats_library`. During migration: keep as shim + add a `formats_library` alias. |
| `gateway.py::collections_gateway`, `recipes_gateway` (line 412, 421) | The per-flavour gateway accessors. | Same as the library properties — rename after migration. |
| `gateway.py::_make_collections_library_factory(...)` (line 1277) | Private factory. | Rename alongside `collections_library`. |

Caller distribution for the Recipe cluster: `share_cuts_page.py` 5 ·
`gateway.py` internal 4 · `events_page.py` 2 · `new_recipe_dialog.py`
2 · `event_collection_store.py` 2 · one-hit callers scattered across
`recipe_draft_adapter.py`, `definition_library.py`, `cut_session.py`,
`library_page.py`. Small enough to sweep in one PR per property.

### §1.2.3 Cluster C — Cross-event Cut methods (LibraryGateway + Gateway facade)

| File · Symbol | Target |
|---|---|
| `library_gateway.py::create_cross_event_cut(...)` (line 1288) | `create_library_cut(...)` or drop the "cross_event" prefix (`create_cut(...)`). Scope is carried by the Cut's pool per spec/160. **unresolved — Nelson decides** whether the API drops the "cross-event" chrome or renames to "library" (§7 question). |
| `library_gateway.py::update_cross_event_cut_settings(...)` (line 1380) | Same reasoning. |
| `library_gateway.py::delete_cross_event_cut(...)` (line 1514) | Same. |
| `library_gateway.py::delete_cross_event_cut_members(...)` (line 1560) | Same. |
| `gateway.py::delete_cross_event_dc(...)` (line 1374) | Rename to `delete_library_pool(...)` (or `delete_pool(...)`). |
| `gateway.py::delete_cross_event_cut(...)` (line 1396) | Same as the library gateway methods. |

### §1.2.4 Cluster D — Internal helpers + constants

| File · Symbol | Target |
|---|---|
| `event_gateway.py::_infer_source_dc_kind(...)` (line 2927) | Rename to `_infer_source_pool_kind(...)` if `cut.source_dc_*` columns rename per §1.4 §7 question 4; else keep. |
| `event_gateway.py::_collections_library_snapshot(...)` (line 1374) | `_pools_library_snapshot(...)`. |
| `event_gateway.py::_resolve_library_collection(...)` (line 1394) | `_resolve_library_pool(...)`. |
| `library_gateway.py::_event_collection_by_ref(...)` (line 579) | **Keep** — `event_collection` is unrelated to Media Pool (spec/90 §5.3 saved event set). §1.4 flagged the naming clarity issue. |
| `cross_event_resolver.py::CrossEventAccessors.dc_by_ref(...)` (line 319) | `pool_by_ref(...)`. |

### §1.2.5 Signals + dataclasses

Zero `pyqtSignal` names in the gateway carry retiring vocabulary (grep
against `pyqtSignal.*(?:recipe|collection|dc|dynamic)` returns nothing).

Dataclasses in `gateway.py`:

| Name | Vocab? | Action |
|---|---|---|
| `CrossEventCutRow` (line 137) | "Cross-event" chrome | Drop the prefix if §1.2.3's Nelson decision goes that way; else keep. |
| `EventsQuery`, `EventsListing` | None | Keep. |
| `LineageRatings` (event_gateway.py line 77) | None | Keep. |

### §1.2.6 Summary

The gateway migration has a **shape**, not a per-method decision list:

- **~40 public methods rename** (`dc_*` → `pool_*`, `resolve_recipe` →
  `resolve_pool_from_recipe`, `create_cross_event_cut` →
  `create_library_cut`).
- **~4 facade properties rename** on the `Gateway` class
  (`collections_library` / `recipes_library` / etc.).
- **~6 private helpers rename** (`_check_dc_cycle`, `_operand_dc`, …).
- **Every rename lands as a shim first** so UI sweeps and gateway
  renames don't have to synchronise; shims retire in phase 5.
- Zero schema-facing name changes emerge from this section — those
  are §1.4.

---

## §1.3 The Cut creation flow — surface map

The whole point of spec/160 is one flow, one story:

> **Compose a Media Pool → apply a Format → pin the pool into a Cut.**
> Same three steps at event scope and at library scope. Different
> width (§6.2), same skeleton.

This section maps every current surface onto that flow. Read as one
picture; the vocabulary change (Collection / DC / Recipe → Media Pool
/ Format) is what makes the picture legible — no other structural
change is implied.

Every widget substrate below is a canonical spec/92 role — nothing new
is invented. The migration is a **rename + regroup** job over an
existing widget vocabulary.

### Step 1 — Start a Cut

**Today (event scope).** `share_cuts_page.py`'s Cuts tab exposes the
`+ New Cut` entry; opens `NewRecipeDialog(flavour=cut)`.
**Today (library scope).** Multiple entry points: `library_page.py`'s
Cuts band; `cross_event_cuts_dialog.py`; the "Pin" action on rows in
`cross_event_dcs_dialog.py` (pin a saved Collection into a cross-event
Cut) — some of these route via `NewRecipeDialog(flavour=collection)`,
others via `NewCrossEventDcDialog` + `CrossEventPickerDialog`.

**Target.** One New-Cut affordance at each scope; opens the one Cut
composition surface (spec/160 §6.1). Scope is inferred by entry point
and operand closure (spec/93). No user-visible flavour switch.

### Step 2 — Compose the Media Pool

The "Which items?" part. Source + Filters + Rules.

**Today.** Two dialogs carry pool composition:

1. Inside `NewRecipeDialog`'s "Which items?" band:
   - **Source** = pool-formula chips (operand + join-word); today's
     `Load Collection…` / `Save as Collection…` buttons ride here.
   - **Filters** = Style / Media rows always; Camera / Lens rows on
     the collection flavour only (today's rough version of §6.2's
     visibility split).
   - **Rules** = `RuleRow` list, each row with a **verdict pill**
     (pick / skip) plus a join-word popover.
2. Standalone in `NewCrossEventDcDialog` — a lighter Media Pool
   composer used from `cross_event_dcs_dialog.py`. Identity + Origin +
   Filters (via the shared `FilterBar`, spec/159 §4.5).

The live pool-detail surface at `mira/ui/shared/dc_detail_page.py`
(top-level `setObjectName` **already** `PoolDetailPage`) is where a
saved pool gets reviewed + refined; it hosts the same `FilterBar`.

**Target per spec/160 §6.1 + §6.2.**

- **One Media Pool section** on the Cut dialog. Same section at both
  scopes; §6.2 controls width.
- Source: `Load Media Pool…` / `Save as Media Pool…` buttons ride the
  section header (§6.3–§6.4).
- Filters at **event face**: Style + Media + curatorial ratings +
  Faces-if-flag-on.
- Filters at **library face**: adds Camera / Lens / Flash / EXIF
  ranges / Temporal / Location / Faces-if-flag-on.
- **Rules retire the per-rule verdict pill** (spec/160 §4) — Rules
  narrow only. The join-word grammar survives verbatim.
- The `FilterBar` primitive keeps its job as the reusable Filters
  editor — inside the Cut dialog and inside the standalone pool detail.

### Step 3 — Compose the Format

The "How is it presented?" part.

**Today.** The "Runtime" section inside `NewRecipeDialog`:
aspect / per-photo timing / target minutes / max minutes / music /
overlays / separators. No standalone Format editor exists; the
presentation choices are only reachable through the Cut dialog.

**Target per spec/160 §3 + §6.1.**

- **One Format section** on the Cut dialog. **Identical widgets at
  both scopes** (spec/160 §3.3 — Format is scope-free).
- Section header carries its own `Load Format…` / `Save as Format…`
  buttons — orthogonal to the Media Pool save/load.
- An optional standalone Format editor (edit a saved Format template
  outside a Cut) is a **surface-plan question**, not an audit one —
  flagged in §7 as something the target-UI phase decides.

### Step 4 — Save/Load templates

Two independent template kinds under spec/160.

**Today.** Four modal sub-dialogs, all inside `new_recipe_dialog.py`,
all using the canonical `FormFieldGroup`:

- `SaveRecipeNameDialog` — saves the bundled Pool + Format
- `LoadRecipeDialog` — loads the bundle; carries a "show cuts too" /
  "show collections too" cross-flavour filter
- `SaveAsDcNameDialog` — saves the Pool half only (event-scope)
- `LoadDcDialog` — loads the Pool half only (event-scope)

**Target per spec/160 §6.3 + §6.4.**

- **Save/Load Media Pool** at both scopes — one modal (roughly the
  current `SaveAsDcNameDialog` + `LoadDcDialog` shape, promoted to
  library scope).
- **Save/Load Format** at both scopes — one modal in the same shape.
- **Save/Load Recipe retires** — the bundled template is not a spec/160
  noun. Legacy Recipe rows migrate onto the split (Pool half → Media
  Pool row, Format half → Format row); design in phase 2.

### Step 5 — Pin / freeze

Freeze the resolved pool into picked/skipped decisions.

**Today.** The engine layer:

- `mira/shared/cut_session.py::CutSession` — the in-memory pin
  session, driven by a `pin_mode` field with **four values**:
  `keep-all` / `weed-out` / `pick-in` / `rule-based`.
- `mira/ui/pages/picker_page.py::PickerPage` — event-scope Picker.
- `mira/ui/pages/cross_event_picker_dialog.py::CrossEventPickerDialog`
  — library-scope Picker; window title `Pin cross-event Cut — {name}`.

Pin entry points: the Cut dialog's `▶ Start` button; the "Pin" kebab
action on saved DC rows in `share_cuts_page.py` (event) and
`cross_event_dcs_dialog.py` (library).

**Target per spec/160 §4.3 + §5.1.**

- `pin_mode` collapses to **one toggle** — "Start all picked / Start
  all skipped" — living in the Cut dialog's **Freeze section**.
  `rule-based` retires (Rules narrow only, spec/160 §4). `keep-all` +
  `weed-out` merge into "all picked"; `pick-in` becomes "all skipped".
- Picker surfaces (`PickerPage` + `CrossEventPickerDialog`) survive
  unchanged in shape; the cross-event window title drops "cross-event"
  chrome (`Pin Cut — {name}`; scope is carried by the Cut's Media
  Pool).
- Pin entry points survive: the ▶ Start button, and the row-kebab pin
  affordance (which reads the pool's default Freeze toggle).

### Step 6 — Browse existing

**Today.**

- **Cuts:** `share_cuts_page.py` (event, Cuts tab) · `library_page.py`
  (Cuts band) · `cross_event_cuts_dialog.py` (list) ·
  `cross_event_cut_detail_dialog.py` (detail).
- **Media Pools** (called Collections/DCs today): `share_cuts_page.py`
  (event, Collections tab) · `library_page.py` (Collections band) ·
  `cross_event_dcs_dialog.py` (list) · `dc_detail_page.py` (detail —
  spec/159 §4.5).
- **Recipes** (bundled Pool + Format): `library_page.py`'s Recipes
  band; the `LoadRecipeDialog` load surface.

**Target per spec/160.**

- **Cuts** — same surfaces; strings retitle where needed. The library
  Cut list drops "cross-event" chrome (`cross_event_cuts_dialog.py` /
  `cross_event_cut_detail_dialog.py`) — scope is carried by the Cut.
- **Media Pools** — same surfaces retitled from Collection.
  `dc_detail_page.py` continues as the Media Pool detail (its object
  name already reads that way).
- **Formats** — a first-class browse surface. `library_page.py` shows
  **three bands: Cuts / Media Pools / Formats** (Q3 answer confirmed:
  silent split at load; the legacy Recipes band retires cleanly, no
  transitional four-band layout).
- **Tone-Recipes** (spec/54 edit-tone looks) — **out of scope for
  spec/160** (Q5 answer confirmed). Every `Recipe` hit in
  `mira/ui/edited/` and any tone-Recipe path elsewhere gets a `keep —
  tone recipe, unrelated to spec/160` marker in §1.1 and skipped from
  the retirement bucket. spec/160 §7 needs an amendment recording the
  carve-out (see §7 below).

### Cross-scope sanity check against §6.2

The target Cut composition surface, event face vs library face:

| Section | Event face | Library face |
|---|---|---|
| Scope selector | hidden | visible |
| Media Pool → Source | `#exported` only; other Pools + Cuts as operands (this event's) | full ladder `#collected` / `#picked` / `#edited` / `#exported`; library-wide operands |
| Media Pool → Filters | Style · Media · curatorial ratings · Faces (opt-in) | + Camera · Lens · Flash · EXIF ranges · Temporal · Location |
| Media Pool → Rules | visible; operand vocabulary = this event's | visible; operand vocabulary = library-wide |
| Format | identical (aspect / timing / audio / overlays / separators / …) | identical |
| Freeze | "Start all picked / Start all skipped" toggle | identical |
| Metrics | live match count | live match count |

Every widget in either column already exists in spec/92 §2's canonical
catalog. The migration is **strings + regroup**, never "invent a new
role."

### Which files carry which step

Reverse index — where the flow is currently implemented, so the
implementation phase knows what to touch per step.

| Step | Files that touch it today |
|---|---|
| 1. Start | `share_cuts_page.py` · `library_page.py` · `cross_event_cuts_dialog.py` · `cross_event_dcs_dialog.py` |
| 2. Media Pool composition | `new_recipe_dialog.py` (Which-items band) · `new_cross_event_dc_dialog.py` (standalone) · `dc_detail_page.py` (live review via `FilterBar`) · `exported/filter_bar.py` (reusable filter primitive) |
| 3. Format composition | `new_recipe_dialog.py` (Runtime section) |
| 4. Save/Load templates | `new_recipe_dialog.py` (4 modal sub-dialogs) |
| 5. Pin / freeze | `shared/cut_session.py` · `picker_page.py` · `cross_event_picker_dialog.py` · pin trigger points in `share_cuts_page.py` + `cross_event_dcs_dialog.py` |
| 6. Browse | `share_cuts_page.py` · `library_page.py` · `cross_event_cuts_dialog.py` · `cross_event_cut_detail_dialog.py` · `cross_event_dcs_dialog.py` · `dc_detail_page.py` |

**What the map surfaces.** Three structural observations that fall
out, none of them audit decisions:

1. **Media Pool composition is currently split across two dialogs**
   (`NewRecipeDialog`'s "Which items?" band, and the standalone
   `NewCrossEventDcDialog`). Under spec/160 §6.1 the pool always
   composes inside one Cut dialog, so the standalone composer either
   retires or gets reshaped into a "quick save" flow. This is the
   implementation phase's choice; the audit's job is to name the
   surfaces.
2. **The pin engine's `pin_mode` field has four values today; spec/160
   §4.3 collapses them to a boolean toggle.** The migration is a
   store-level change with a small UI surface — one pill in the Freeze
   section replacing the Otherwise verdict pill.
3. **The Library page's Recipes band is the only surface that dies
   outright.** Cuts, Media Pools (Collections), and Formats each end
   up with a band; the bundled Recipe concept has no surface after
   phase 2.

---

## §1.4 Schema tables + columns

Default per spec/93 §4 + spec/160 §7: **keep internal names.** spec/160
§2.5 additionally makes this explicit for `dynamic_collection`,
`saved_filter`, and `global_items` — the three that hold Media Pool
substrate.

**Verdict.** Every table and column can keep its current name **except
`recipe`**, which is the one place the noun itself splits and the
storage question is real. Detail below.

### Schema-wide inventory

| Scope | Table | Retiring vocab? | Action |
|---|---|---|---|
| event.db | `dynamic_collection` | "Dynamic Collection" name | **Keep** — spec/160 §2.5 explicit. Substrate for event-scope Media Pool. |
| event.db | `cut` + `cut_member` | Cut noun survives spec/160 | **Keep.** |
| event.db | `recipe` (spec/94 Phase 1 bound recipes) | Recipe noun retires + `flavour` enum retires | **Reshape** — options in §1.4.1 below. |
| event.db | `lineage.recipe_json` column | `recipe_json` names a **tone recipe** (spec/54 §8 — archival snapshot of the edit look) | **Keep** — Q5 answered: tone-Recipes are unrelated to spec/160. Column name stays; content unchanged. |
| event.db | `cut.source_dc_kind`, `cut.source_dc_id`, `cut.source_dc_tag` | `dc` abbreviation retires as UI vocab | **Keep** (internal, spec/93 §4). Optional developer-comfort rename to `source_pool_*` is a Q — see §7. |
| event.db | `dynamic_collection`'s columns (`origin_ref`, `filters_json`, `composition_json`, etc.) | None carry retiring vocab | **Keep.** |
| mira.db | `saved_filter` | Substrate for library-scope Media Pool | **Keep** — spec/160 §2.5 explicit. |
| mira.db | `global_items` | Substrate for library-scope facet projection | **Keep** — spec/160 §2.5 explicit. |
| mira.db | `recipe` (spec/90 §5.1 Cut/Collection Recipe) | Recipe noun retires + `flavour` enum retires | **Reshape** — options in §1.4.1 below. **This is the load-bearing schema change.** |
| mira.db | `event_collection` | The word "Collection" appears — but this is a **saved set of events**, not a Media Pool. Unrelated to spec/160's retirement | **Keep.** Consider flagging in §7 whether it needs a docstring clarifying the distinction. |
| mira.db | `cut` + `cut_member` | Cut noun survives | **Keep.** |
| mira.db | `cut_template` (v2 legacy) | "Cut template" retires per spec/160 §7 | **Investigate + likely retire.** spec/94 (docstring line 310–311) already says spec/90 "supersedes cut_template once the dialog migration lands." Confirm zero live callers, then drop in a phase-2 migration. |
| mira.db | `gear_profile`, `person`, `user_camera`, `feature_flag`, `wizard_answer`, `installation_profile`, `setting`, `event_index`, `schema_info` | None | **Keep.** |

### §1.4.1 The `recipe` table — the load-bearing decision

Under spec/160 the "Recipe" noun splits into **Media Pool** (already
stored as `dynamic_collection` / `saved_filter`) and **Format** (new
storage need — the presentation half of what was a Recipe).

Three storage options, in order of divergence from today:

- **(A) Keep the `recipe` table; narrow `flavour` to `format`-only.**
  During phase 2, legacy `flavour='cut'` and `flavour='collection'` rows
  migrate: the Media Pool half moves into `dynamic_collection` /
  `saved_filter`; the presentation half stays in-place with
  `flavour='format'`. The `UNIQUE (flavour, name)` constraint survives
  degenerately (only one flavour left). Table keeps its name; docstring
  updates. **Closest to spec/160 §3.4's leaning ("survives internally").**
- **(B) Rename `recipe` to `format`.** Same content shape as (A) after
  migration; the rename is a schema-level cosmetics change. Breaks any
  external SQL introspection ("show tables") but nothing user-facing.
- **(C) Split into two new tables: `media_pool` and `format`.** Deprecate
  `recipe`. Highest divergence; largest migration; buys nothing over (A)
  because the Media Pool substrate **already** lives in
  `dynamic_collection` / `saved_filter` — a new `media_pool` table would
  be a rename of one of those, not new storage.

The audit **recommends (A)** for consistency with spec/93 §4 (keep
internal names), spec/160 §3.4 ("survives internally"), and minimum
migration cost. Explicit Nelson sign-off requested in §7.

### §1.4.2 The `Recipe.flavour` enum + `cut.source_dc_kind` enum

Both enums encode retiring vocabulary in their **values**, not just the
column name.

| Column | Current values | Under spec/160 |
|---|---|---|
| `mira.db recipe.flavour` | `'cut'` \| `'collection'` | Post-migration: `'format'` only (option A above). During migration: `'cut'` / `'collection'` legacy rows get split and either deleted or re-flavoured to `'format'`. |
| `event.db recipe.flavour` | `'cut'` only (CHECK constrains it; bound recipes are Cut-flavoured) | Rename to `'format'` after migration or drop the column entirely. |
| `event.db cut.source_dc_kind` | `'user'` (mira.db saved_filter) \| `'event'` (event.db dynamic_collection) \| `NULL` | **Keep.** Values encode which substrate table the pool lives in, not user vocabulary. |

### §1.4.3 Model classes (mira/store/models.py + mira/user_store/models.py)

Per spec/93 §4 model class names stay:

| Class | File | Action |
|---|---|---|
| `DynamicCollection` | `mira/store/models.py` | Keep. |
| `SavedFilter` | `mira/user_store/models.py` | Keep. |
| `Recipe` | `mira/user_store/models.py` | Keep the class name; the `flavour` field's allowed values change per §1.4.2. Alternative: rename to `Format` — flagged in §7 as a developer-comfort choice. |
| `CutTemplate` | `mira/user_store/models.py` | Retire along with `cut_template` table (§1.4). |
| `EventCollection` | `mira/user_store/models.py` | Keep — unrelated to Media Pool. |
| `Cut`, `CutMember` (both DBs) | | Keep. |
| `GlobalItem` | `mira/user_store/models.py` | Keep. |

### §1.4.4 No user-facing leaks

Grep confirms zero SQL introspection paths surface these table names to
the user (no debug console, no `.schema` dump in the app). Table names
appear only in code + spec docs + tests. Renames therefore have zero UI
cost and are purely developer-facing decisions.

---

## §1.5 Tests

Every test file mentioning retiring vocab. Grouped by **category
protected** (§1.1–§1.4), each with an update-kind tag.

Update-kind legend: `mechanical` (moves when its gateway/store call
gets shimmed; no logic change) · `semantic` (assertion prose or fixture
data changes shape) · `guard` (the test enforces vocabulary
compliance — its rule flips).

### §1.5.0 Scale

- **New Cut dialog test suite:** 18 files (`test_new_recipe_*.py` +
  `test_recipe_draft_adapter.py`). File names carry `recipe` because
  the dialog class is `NewRecipeDialog`; file renames are optional and
  cosmetic (a renamed dialog class → renamed file). The tests
  themselves protect §1.3 step 2–4 behaviour.
- **Collection/DC dialog tests:** ~10 files (`test_collection_*.py` +
  `test_*dc*.py` + `test_dc_detail_page.py`).
- **Cut tests:** ~45 files (`test_cut_*.py`, `test_cross_event_cut_*.py`,
  `test_cuts_shell.py`, `test_share_cuts_*.py`, `test_library_gateway_cuts.py`,
  `test_gateway_cuts.py`). Cut noun survives — these tests need
  updates ONLY where they call a renamed gateway method
  (`dc_probe`, `resolve_recipe`, etc.).
- **Store / gateway tests:** ~15 files
  (`test_library_gateway*.py`, `test_gateway_cuts.py`,
  `test_recipe_store.py`, `test_event_collection_store.py`,
  `test_resolver_file_library.py`, `test_dual_home_migrate.py`,
  `test_placement_migrate.py`, `test_freeze_invariant_file_library.py`,
  `test_pin_file_library.py`, `test_operand_inventory_file_library.py`,
  `test_gateway_definition_library_swap.py`).
- **The vocabulary guard test** (`test_collection_vocabulary.py`) —
  spec/93's rule-enforcement test. See §1.5.4 — its rule inverts under
  spec/160.

Total retiring-vocab reach across `tests/`: **~2,880 raw hits across
~120 files.** Most hits are imports of model classes (which stay per
§1.4.3) and gateway method calls (which move by shim per §1.2.0) — so
the majority is `mechanical`, riding on the gateway shim, not requiring
per-test edits during phase 1.

### §1.5.1 Category (a) — tests that protect §1.1 (user-facing strings)

| Test file | Update kind | Notes |
|---|---|---|
| `tests/test_collection_vocabulary.py` | **guard — rule inverts** | Today enforces "use Collection, not Dynamic Collection / DC / cross-event collection." Under spec/160 flips to "use Media Pool, never Collection / DC / Dynamic Collection." Rewrites completely during phase 1. This test is the phase-1 completion signal. |
| `tests/test_cross_event_dcs_dialog.py` | semantic | Asserts window title / row rendering; strings update to Media Pool vocabulary. |
| `tests/test_cross_event_cuts_list.py` | semantic | Asserts "Cross-event Cuts" title becomes "Cuts". |
| `tests/test_cross_event_cut_detail_dialog.py` | semantic | Title assertions update. |
| `tests/test_new_recipe_dialog_scaffold.py` and its 17 siblings | semantic + mechanical | Dialog title assertions ("New Cut" / "New Collection") update per P9. Toolbar label assertions update per P3. |
| `tests/test_share_cuts_phase1b_wiring.py`, `tests/test_cuts_shell.py` | semantic | Assertions on tab labels + kebab menu entries update per P1. |
| `tests/test_first_run_library.py` | semantic | Onboarding copy assertions update. |
| `tests/test_dc_detail_page.py`, `tests/test_spec159_dc_detail_page.py` | semantic | Header + breadcrumb strings update. |

### §1.5.2 Category (b) — tests that protect §1.2 (gateway APIs)

These call gateway methods with retiring vocab in their name. **Under
the shim strategy (§1.2.0) these tests keep passing during phase 1
without edits** — the old `dc_*` / `resolve_recipe` names remain as
deprecated wrappers. They migrate to the new names one file per
UI-surface PR, matching §1.5.4 below.

| Concentration | Files (top-hit) | Priority |
|---|---|---|
| DC method calls (`dc_probe`, `resolve_dc`, `create_dc`, etc.) | `test_library_gateway.py` (33), `test_gateway_cuts.py` (28), `test_gateway_definition_library_swap.py` (17), `test_resolver_file_library.py` (12) | High — the shim lets these keep passing; migrate as gateway rename lands. |
| Recipe method calls (`resolve_recipe`, `recipe_store`, `.recipes_library`, `.collections_library`) | `test_recipe_store.py` (88), `test_recipe_draft_adapter.py` (76), `test_share_cuts_phase1b_wiring.py` (29) | High — smaller scope than DC, direct rename feasible. |
| Cross-event Cut methods | `test_cross_event_cut_session.py` (11), `test_cross_event_cut_migrate.py`, `test_cross_event_cut_export.py`, `test_cross_event_cut_play.py`, `test_cross_event_cut_detail_dialog.py`, `test_library_gateway_cuts.py` | Medium — updates hinge on the §7 Q "drop cross-event prefix or keep." |

### §1.5.3 Category (c) — tests that protect §1.3 (surfaces)

The 18 `test_new_recipe_*.py` files. Each targets one section of the
Cut composition surface:

| File | Section it protects | Update-kind expected |
|---|---|---|
| `test_new_recipe_dialog_scaffold.py` | overall structure + section builders | semantic + mechanical |
| `test_new_recipe_dialog_source.py` | Media Pool → Source | semantic |
| `test_new_recipe_dialog_filters.py` | Media Pool → Filters | semantic |
| `test_new_recipe_dialog_rules.py` | Media Pool → Rules (verdict pill retires) | **semantic — behaviour change** |
| `test_new_recipe_dialog_otherwise.py` | Otherwise section | **semantic — section retires**, tests migrate to "Freeze toggle" or delete |
| `test_new_recipe_dialog_scope.py` | Scope band (library face) | semantic |
| `test_new_recipe_dialog_popovers.py` | Verb + join-word popovers | semantic (verb popover retires per §4) |
| `test_new_recipe_dialog_metrics.py` | Metrics section | semantic |
| `test_new_recipe_dialog_save_load.py`, `_save_as_dc.py`, `_load_dc.py` | Save/Load modals | **semantic — split** into 2 × 2 modals per P5–P8 |
| `test_new_recipe_dialog_start.py` | Start button + pin session handoff | semantic |
| `test_new_recipe_dialog_binding_badge.py` | Binding badge | mechanical only if wording changes |
| `test_new_recipe_music.py`, `test_new_recipe_aspect_prefill.py`, `test_new_recipe_overlay.py` | Format section (Music / Aspect / Overlay widgets) | mechanical — Format widgets survive verbatim |
| `test_new_recipe_dialog.py::test_*rules*` (in scaffold) | Rules verdict pill test cases | delete or rewrite — verdict pill retires per §4 |

**Load-bearing observation.** `test_new_recipe_dialog_otherwise.py`
and the rules-verdict tests are the ones that **verify the retired
behaviour** — they need active work in phase 4 (Rules re-scoping,
spec/160 §9.3), not phase 1 vocabulary sweep.

### §1.5.4 Category (d) — tests that protect §1.4 (schema)

| File | Update kind | Notes |
|---|---|---|
| `test_recipe_store.py` (88 Recipe hits) | mechanical | The store's public methods hide behind the same API; internal schema stays per §1.4.1 option A. Fixture data setting `flavour='cut'` / `flavour='collection'` migrates to `flavour='format'` in phase 2. |
| `test_event_collection_store.py` | keep | `event_collection` is unrelated to Media Pool (spec/90 §5.3). No changes required beyond adding a docstring clarification per §1.4 §7 point 5. |
| `test_placement_migrate.py`, `test_dual_home_migrate.py` (24 + 33 + 41 + 75 hits) | semantic | These test the storage placement of saved templates. Fixture data + assertion strings shift from Recipe (bundled) to Media Pool + Format (split) in phase 2. |
| `test_freeze_invariant_file_library.py`, `test_operand_inventory_file_library.py`, `test_resolver_file_library.py`, `test_pin_file_library.py`, `test_gateway_definition_library_swap.py` | mechanical | Ride the gateway shim; migrate with the gateway rename PR. |

### §1.5.5 Category (e) — tests protecting §1.5 itself (meta)

`test_no_inline_qss.py` — CLAUDE.md's QSS guard. Not touched by
vocabulary migration; noted here so no one edits it by accident during
phase 1.

### §1.5.6 Reconciliation

- Guard test (1 file, rule inverts): `test_collection_vocabulary.py`.
- Semantic updates (~35 files): assertion strings or fixture data
  change with the surface + schema they protect.
- Mechanical updates (~85 files): ride the gateway shim + keep working
  through phase 1; move at their owning surface's rename PR.
- **Keep as-is** (~30 files): tests that only touch tone-Recipe
  (`test_edit*.py`, `test_look_strength_foundation.py`) or Cut noun
  behaviour without vocabulary assertions.

Total files with retiring-vocab imports/references: ~120. Files that
actually **need an edit during phase 1** (before gateway shims retire):
~36 (the guard test + the ~35 semantic-update files).

---

## §1.6 Docs + specs

Two-part treatment:

- **Live specs** — the ones spec/160 says continue to govern (spec/03,
  spec/32, spec/61, spec/81, spec/93, and spec/160 itself). Per-section
  rows for the load-bearing passages that carry retiring vocabulary.
- **Superseded specs** — the ones spec/160 explicitly retires or that
  already carry a "revised by" banner. **One-row historical entry per
  doc.** Content stays as the design record; a banner is added if not
  already present, pointing at spec/160.

### §1.6.1 Inventory

| Doc kind | Files | Total retiring-vocab hits (approx) |
|---|---|---|
| **Live governing specs (spec/160 substrate)** | `spec/00-charter.md`, `spec/03-schema.md`, `spec/32-dynamic-collections.md` (16), `spec/61-share-event-cuts.md` (10), `spec/81-dynamic-collection-and-cut.md` (38), `spec/93-recipe-collection-storage-and-placement.md` (49) | ~113 |
| **Live surface / feature specs referencing retiring vocab** | `spec/54-edit-tone-looks.md` (5 — Q5 tone-Recipe; keep), `spec/55-creative-filters.md` (9), `spec/57-folders-and-roundtrip.md` (1), `spec/58-classification-and-wizard.md` (2), `spec/60-batch-export-engine.md` (3), `spec/63-photo-viewport.md` (1), `spec/72-third-party-roundtrips.md` (1), `spec/76-home-library-and-cut-publishing.md` (5), `spec/85-gear-profile-wizard.md` (3), `spec/86-event-data-filters.md` (3), `spec/89-export-model-b.md` (5), `spec/91-face-recognition.md` (23), `spec/92-widget-consolidation.md` (7), `spec/98-recipe-overwrite-and-cut-day-nav.md` (19), `spec/106-restore-cut-music-picker.md` (3), `spec/111-cut-aspect-ratio.md` (2), `spec/113-cut-active-filter-visibility.md` (2), `spec/114-restore-cut-overlay-control.md` (10), `spec/116-creative-filters-spotlight-dehaze-glow-grain.md` (7), `spec/118-edited-since-export-badge-and-overwrite-choice.md` (9), `spec/119-cut-dialog-checkboxes-not-pills.md` (3), `spec/121-cut-aspect-prefill-and-pte-filename.md` (7), `spec/143-restore-cut-separator-control.md` (3), `spec/151-video-export-passthrough-and-cut.md` (5), `spec/154-cross-event-pte-overlays.md` (1), `spec/156-filter-strength.md` (4), `spec/159-exported-collection-review-and-classify.md` (16) | ~168 |
| **Superseded — banner already present** | `spec/48-four-phase-pivot.md` (banner: Share revised by spec/66), `spec/51-share-cuts-vision.md` (7, banner: superseded by spec/61), `spec/56-video-workshop.md` (banner exists), `spec/PROGRESS.md` (rolling history) | ~7 |
| **Superseded by spec/160 (banner NEEDED)** | `spec/80-cut-construction-model.md` (44, brainstorm predecessor to spec/81), `spec/90-cut-recipes-and-collections.md` (175 — the parent Recipe/Collection spec, spec/160 explicitly retires its noun model), `spec/94-collections-recipes-cuts-roadmap.md` (43 — spec/160 explicitly replaces its roadmap) | ~262 |
| **Retirement housekeeping** | `spec/40-v1-effortless-craft.md` (1), `spec/41-xmc-completion.md` (2), `spec/52-event-creation-vision.md` (1), `spec/14-plan-manage.md` (7), `spec/09-shell-and-navigation.md` (1), `spec/05-ui-standards.md` (1), `spec/30-relational-schema-redesign.md` (1), `spec/70-new-ui-completion-plan.md` (1), `spec/83-facet-picker-audit.md` (3), `spec/87-dead-code-audit.md` (1) | ~19 |
| **`docs/`** | `docs/02-user-journeys.md` (2), `docs/20-pte-annotation-workflow.md` (1); `docs/10-brand-glossary.md` — check for retiring nouns in the glossary itself | ~5 (excluding this audit doc) |
| **HANDOVER-*.md** | `HANDOVER-2026-06-14.md`, `HANDOVER-2026-06-14-evening.md`, `HANDOVER-2026-06-26-overlays.md`, `HANDOVER-2026-06-30-day-separator-videos.md`, `HANDOVER-2026-06-30-spec159-exported-collection.md`, `HANDOVER-2026-06-30-spec159-filters-and-preferred.md`; plus 5 files under `agent-tasks/HANDOVER-phase-*.md` | Session records — see §1.6.4 |
| **CLAUDE.md** | Top-level agent instructions | ~4 (mentions "Collection" in the "One product, two branches" section; mentions Recipe indirectly through mention of "Dynamic Collection UI label") |

### §1.6.2 Live specs — per-passage rows

The load-bearing passages that need retirement-aware edits. This is not
a comprehensive line-by-line list; it's the passages that **substantively
teach** the retiring vocabulary and would confuse a future maintainer
without a rename.

| File | Section | Current phrasing | Target phrasing | Update kind |
|---|---|---|---|---|
| `spec/03-schema.md` | `dynamic_collection` / `saved_filter` sections | Names DC as "Dynamic Collection" user noun | Preface with "internal name stays; user-facing noun is **Media Pool** per spec/160" | one paragraph |
| `spec/32-dynamic-collections.md` | Whole doc | Introduces "Dynamic Collection" as UI noun with 16 references | Add spec/160 banner at top; §1 introduction rewords "Dynamic Collection" to "Media Pool"; §2 facet catalogue survives unchanged (spec/160 §2 confirms) | banner + intro rewrite |
| `spec/61-share-event-cuts.md` | §2, §3 | References DC + Recipe interchangeably | Rewrite affected paragraphs to Media Pool + Format; Cut noun stays | 3–5 paragraphs |
| `spec/81-dynamic-collection-and-cut.md` | Whole doc | The DC + Cut engine spec — 38 hits | Add spec/160 banner at top; keep the engine explanation but retitle the DC noun as Media Pool; §2 set-algebra unchanged; §4 verbs unchanged | banner + §1 heading rename |
| `spec/93-recipe-collection-storage-and-placement.md` | Whole doc | The Recipe + Collection storage placement spec — 49 hits | Add spec/160 banner; §3 rules survive verbatim; retitle "Recipe" as "Media Pool template + Format template" throughout; make explicit that the Recipe → 2-template split doesn't change placement math | banner + terminology sweep |

### §1.6.3 Live feature specs — one-paragraph updates each

For most feature specs (the ~27 files in the second row of §1.6.1),
the vocabulary appears in **cross-references** — "the Recipe editor
(spec/90)" or "the Cut Recipe carries…" — rather than as substantive
teaching. Per-file: one-paragraph update replacing the cross-reference
with spec/160 vocabulary. Total: ~30 file edits.

Special cases:

| File | Update kind | Notes |
|---|---|---|
| `spec/54-edit-tone-looks.md` | **Keep** the word "Recipe" in this file | This is the tone-Recipe home (Q5). Add an explicit sentence: *"'Recipe' here refers to a tone recipe (spec/54 §8's `recipe_json` archival snapshot), unrelated to the Cut/Collection Recipe that spec/160 retires."* Same clarification cross-referenced in spec/160 §7 amendment. |
| `spec/92-widget-consolidation.md` | one paragraph | The spec/92 §0.3 QGroupBox reference to "New Cut" surface stays. Any mention of "Recipe" chrome retitles to Media Pool + Format section headers. |
| `spec/98-recipe-overwrite-and-cut-day-nav.md` | banner + rewrite | 19 hits, load-bearing on Recipe as UI noun. Add spec/160 banner; substantive rewrite of Recipe → 2-template flow. |
| `spec/106`, `spec/111`, `spec/113`, `spec/114`, `spec/119`, `spec/121`, `spec/143`, `spec/151`, `spec/154` | one paragraph each | "Restore Cut Recipe" / "Cut Recipe field" / etc. references retitle to Format section under spec/160. |
| `spec/91-face-recognition.md` | one paragraph | 23 hits, but mostly cross-reference to Recipe chip vocabulary. Update to "Media Pool → Filters → Faces" per spec/160 §6.2. |
| `spec/159-exported-collection-review-and-classify.md` | banner + rewrite | Whole spec is about the "Exported Collection" review surface. Under spec/160, retitles to "Exported Media Pool detail" throughout. spec/159 §4.5's FilterBar contract survives. |

### §1.6.4 Superseded specs — one-row historical entries

Files that no longer teach current behaviour but stay as design record.

| File | Status | Action |
|---|---|---|
| `spec/48-four-phase-pivot.md` | Banner exists (revised by spec/66) | No changes — already flagged. |
| `spec/51-share-cuts-vision.md` | Banner exists (superseded by spec/61) | No changes. |
| `spec/56-video-workshop.md` | Banner exists | No changes. |
| `spec/80-cut-construction-model.md` | **Needs banner** | Add: *"Superseded by spec/81 (engine) and spec/160 (nouns). Retained as design record."* No content edit. |
| `spec/90-cut-recipes-and-collections.md` | **Needs banner** | Add: *"The noun model in this spec (Recipe as bundled Pool + Format) is retired by spec/160. The rule-list engine and chip grammar survive; refer to spec/160 §4 + §6.1 for the target vocabulary."* No content edit. |
| `spec/94-collections-recipes-cuts-roadmap.md` | **Needs banner** | Add: *"The roadmap in this spec is superseded by spec/160 §9. Retained as design-history."* No content edit. |
| `spec/PROGRESS.md` | Rolling history | Add a spec/160 entry to the progress log; no historical edit. |

### §1.6.5 Retirement housekeeping (~10 files, 1–7 hits each)

These carry an incidental reference to Collection / Recipe / DC in
tables of contents, historical notes, or "see also" links. Update the
cross-reference; no content edit.

| File | Update kind |
|---|---|
| `spec/00-charter.md` | Update the "Load-bearing specs" list to include spec/160 (and spec/161). |
| `spec/05-ui-standards.md`, `spec/09-shell-and-navigation.md`, `spec/14-plan-manage.md`, `spec/30-relational-schema-redesign.md`, `spec/40-v1-effortless-craft.md`, `spec/41-xmc-completion.md`, `spec/52-event-creation-vision.md`, `spec/70-new-ui-completion-plan.md`, `spec/83-facet-picker-audit.md`, `spec/87-dead-code-audit.md` | One-line update per file — usually a cross-reference in a paragraph. |

### §1.6.6 `docs/` + CLAUDE.md

| File | Update kind | Notes |
|---|---|---|
| `docs/02-user-journeys.md` | one paragraph | Update the user-journey narrative from "Collection" / "Recipe" to "Media Pool" / "Format". |
| `docs/10-brand-glossary.md` | verify | Check the glossary defines any retiring noun; update entries + add Media Pool / Format entries. |
| `docs/20-pte-annotation-workflow.md` | one paragraph | Cross-reference update. |
| `CLAUDE.md` | one section | Update the "One product, two branches" section's noun usage. Add a "spec/160 vocabulary" note pointing at spec/160 §7. |

### §1.6.7 HANDOVER-*.md files

**Session records — do not edit.** These are point-in-time notes about
what a specific session did. Editing them retroactively distorts the
history. Fresh handovers written during phase 1 use spec/160 vocabulary
naturally.

Exception: `HANDOVER-2026-06-30-spec159-*.md` — if these are still the
active handover pointing next-session work at spec/159 in-flight, add
a top-line note: *"Vocabulary in this handover predates spec/160. Read
in tandem with spec/160 §7 for the current noun map."*

### §1.6.8 Reconciliation summary

- **Live specs needing substantive updates:** ~35 files (§1.6.2 + most
  of §1.6.3 + §1.6.5).
- **Banners added:** 3 (spec/80, spec/90, spec/94).
- **Superseded specs already banner-tagged:** 4 (spec/48, spec/51,
  spec/56, PROGRESS).
- **`docs/`:** 4 files.
- **CLAUDE.md:** 1 section.
- **Handovers:** 0 edits (historical).
- **Tone-Recipe carve-out clarification:** added to spec/54 (Q5).

---

## §7. Ambiguity list — for Nelson

Cases spec/160 didn't cleanly resolve. Each item is a question, not a
recommendation. Resolutions amend spec/160 before implementation begins.

### Resolved (from earlier design pass)

- **Q3 — Library page during Recipe→(Media Pool + Format) split.**
  Nelson: three bands (Cuts / Media Pools / Formats) from day one;
  legacy Recipes split silently at load. No transitional four-band
  layout. Applied to Step 6 above.
- **Q5 — Tone-Recipes.** Nelson: spec/160 retirement covers only
  Cut/Collection Recipes. **Tone-Recipes (spec/54 edit-tone looks)
  survive as a separate noun.** Applied to Step 6 above. **This needs
  a spec/160 §7 amendment** carving the tone-Recipe path out of the
  retirement bucket — captured in the "Surprises" list below.

### Genuine open questions (design-level, not implementation)

1. **Standalone Format editor?** The current codebase has no way to
   edit a saved Format template outside a Cut composition. spec/160
   §6.1 describes Format editing as a section of the Cut dialog only.
   The Library page's new Formats band (Q3 above) implies at least a
   *view* — is opening a saved Format row from the Library:
   (a) opens the Cut dialog with that Format pre-loaded, disables the
   Media Pool + Freeze sections, and only the Format section is
   editable? (b) opens a dedicated small Format editor (new surface)?
   (c) opens the row for delete/rename only, no edit? *Deferrable to
   the target-UI phase (spec/160 §9.2).*
2. **The "start all picked / start all skipped" toggle placement.**
   spec/160 §10 already flags this as open — is it a Cut attribute or
   a Format attribute? Every Cut needs it. Every Format could carry a
   default. Nelson's own gut in §10: probably Format. Confirm at the
   end of the audit; not blocking.
3. **`recipe` table storage strategy.** §1.4.1's option (A) — keep the
   table, narrow the `flavour` enum to `'format'` — is the audit's
   recommendation. Confirm before phase-2 implementation.
4. **Developer-comfort renames.** Two internal names read awkwardly
   under spec/160 vocabulary:
   - `cut.source_dc_kind` / `.source_dc_id` / `.source_dc_tag` →
     `source_pool_*`. Migration cost: SQLite `ALTER TABLE ... RENAME
     COLUMN`, small.
   - `Recipe` dataclass → `Format`. Rename after option (A) lands.
   Both are optional. Value: reduces future-developer confusion. Cost:
   one migration + one class-name sweep. Nelson decides at audit
   sign-off.
5. **`event_collection` docstring clarification.** The word
   "Collection" appears in the table name but refers to a **saved set
   of events** (spec/90 §5.3), not a Media Pool. Worth adding a
   docstring line so a future maintainer doesn't confuse it. Not a
   spec/160 amendment; a code-comment task.
6. **`cut_template` retirement.** spec/94 already says spec/90
   supersedes it "once the dialog migration lands." Confirm zero live
   callers, then drop in phase 2. Not a Nelson decision; a
   verification task for implementation.

### Surprises the audit uncovered — proposed spec/160 amendments

These are things the audit found that spec/160 didn't say (or said
imprecisely). Each is a proposed edit to spec/160, to land BEFORE the
implementation phases begin.

1. **§7 needs a tone-Recipe carve-out row.** Per Q5. Suggested wording
   near §7: *"The 'Recipe' noun as used inside the edit-tone system
   (spec/54 tone looks) is unrelated to the Cut/Collection Recipe and
   is out of scope for this retirement."*
2. **§6.1 says "one dialog, both scopes," but two composers exist
   today** (`NewRecipeDialog` two-flavour + standalone
   `NewCrossEventDcDialog`). spec/160 as written treats this as a
   trivial reshape; the audit surfaces it as a real question — does
   the standalone Pool composer retire, or does it survive as a
   "quick save Media Pool" light path alongside the full Cut dialog?
   Deferrable to spec/160 §9.2 surface plan.
3. **§4.3's pin-mode collapse is heavier than the spec reads.** The
   engine layer (`shared/cut_session.py`) carries four `pin_mode`
   values with distinct code paths (`keep-all` / `weed-out` /
   `pick-in` / `rule-based`). The migration is a store-schema change
   plus a rule-verdict removal plus a UI collapse — three moving
   parts, not one. spec/160 §9.3 phase 4 should be explicit that this
   is the highest-risk phase.
