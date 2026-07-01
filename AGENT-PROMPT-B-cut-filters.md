# Agent prompt — spec/159 Plan B: FilterBar in event-Cut compose + Cut-recipe persistence

Paste the block below to a fresh agent. Read CLAUDE.md and the
handover doc named below first — everything you need to catch up is
in those two + this brief.

---

## Context

Session ending 2026-06-30 finished spec/159 §4–§6+ on the closed-
event Exported Collection surface (`DCDetailPage`). The two commits
that landed:

- [`484ea1a`](https://github.com/nksalgado-proton/Mira/commit/484ea1a)
  spec/159 §6+: preferred-version surface + cluster + Compare reuse
- [`c6ba3b0`](https://github.com/nksalgado-proton/Mira/commit/c6ba3b0)
  spec/159 §4.5: Filters as a group-box bar (reusable)

Read `HANDOVER-2026-06-30-spec159-filters-and-preferred.md` for the
full session summary + schema state (v22 → v25 in that session arc)
+ the full file map.

The FilterBar Nelson just signed off on lives in
`mira/ui/exported/filter_bar.py`. It's a reusable group-box widget:
takes / emits a `LineageFilter` predicate
(`mira/ui/exported/filter_popup.py::LineageFilter`); host owns the
predicate. Design goal was ALREADY to reuse it on the Cut-compose
surfaces (Nelson: *"we should make this reusable to be applied,
either in the creation of DCs or in the new cuts"*) — this task is
the first payoff.

## The task

Extend **`mira/ui/pages/new_recipe_dialog.py`** — the event-Cut
composition dialog — to embed the FilterBar in its Filters section
so per-version lineage ratings drive Cut composition. The chosen
filter has to persist:

1. **Session state** — while the dialog is open, the filter narrows
   the pool of shipped items the recipe pulls from.
2. **On save**, the filter state rides along inside the Cut's
   `composition_json`. Reopening the Cut re-hydrates the FilterBar.
3. **Optional but recommended**: expose "Save as Dynamic Collection"
   so a user who dialled in `5★ · green-labelled · flagged` can
   save that lens as an event DC + re-load it in future Cuts.

## Read this before you code

Load these files (skim, don't reread every line):

- `HANDOVER-2026-06-30-spec159-filters-and-preferred.md` — session
  arc + file map + design questions.
- `spec/159-exported-collection-review-and-classify.md` — the spec.
  Especially §4.5 / §4.6 (session-local filter) and §8 (lineage vs
  spec/32 rating datasets).
- `spec/61-share-event-cuts.md` — the Cut model; how
  `composition_json` is used.
- `spec/32-dc-facets.md` (or nearest neighbour) — the existing
  facet vocabulary the Cut / cross-event DC surfaces speak. This
  matters because you're introducing a NEW filter dimension (spec/
  159 lineage ratings) alongside the existing spec/32 item-level
  filters. They coexist; don't collapse them.
- `mira/ui/exported/filter_bar.py` — the widget you're embedding.
- `mira/ui/exported/filter_popup.py::LineageFilter` — the predicate.
- `mira/ui/pages/new_recipe_dialog.py::_build_filters_section` — where
  you insert the FilterBar (line ~2695). Also
  `_build_which_items_group` for the outer context.
- `mira/gateway/event_gateway.py::exported_files_all` +
  `exported_files` — the gateway query the Cut composition draws
  from. Filtering happens in-memory over these results.

## Design decisions to lock down BEFORE writing code

Ask Nelson explicitly if any of these are unclear — he'll be quick
to answer.

**D1. Storage scope on save.** The filter is either:
- (a) Persisted on the Cut's `composition_json` so reopening a
  saved Cut re-hydrates the FilterBar. Recommended default.
- (b) Session-only — same as DCDetailPage §4.6. Simpler but the
  user's "5★ portfolio Cut" wouldn't recompose if they reopen it.

Recommendation: (a). It's what the user expects on a Cut recipe.

**D2. Filter dataset.** The new filter should operate on **spec/
159 lineage ratings** (per-version, closed-event-native), NOT
spec/32 item-level ratings. Rationale: the event-Cut dialog is
composing from the shipped pool, and the shipped pool is
lineage-keyed (each version can have different ratings). Existing
spec/32 filter chips in the dialog stay unchanged — the two filter
groups coexist.

**D3. FilterBar reuse shape.** Two options:
- (a) Embed the FilterBar as-is; it's already generic on
  `LineageFilter`. Simplest.
- (b) Generalise FilterBar to accept a "filter shape" descriptor
  so a spec/32 sibling can reuse it. Defer this — do the simplest
  embedding now, generalise later when the cross-event revamp
  happens.

Recommendation: (a). Widget already reusable.

**D4. Save-as-DC scope.** Two options:
- (a) Ship a "Save as Dynamic Collection" button that captures the
  current filter + writes to `dynamic_collection`. Full loop.
- (b) Just persist on the Cut composition_json; DCs come later.

Recommendation: (a) — small addition; closes the loop Nelson
mentioned ("so it can be saved as an event collection for reuse").

## Sequenced implementation plan

**Phase 1 — Wire the FilterBar into the dialog.**
1. Locate `_build_filters_section` (line ~2695). Add the FilterBar
   as a new sub-block below the existing chip rows (Style + Media
   + optional hardware).
2. Add `self._lineage_filter: LineageFilter = LineageFilter()` to
   the dialog. Wire `filter_bar.filter_changed →
   self._on_lineage_filter_changed` to store it.
3. Route the filter into the composition-preview pipeline. The
   dialog's live-count / preview pathway (search for
   `dc_probe` or `_refresh_count` — the recipe surfaces a live
   candidate count) needs to apply `LineageFilter.matches` over
   `exported_files_all` results before counting.

**Phase 2 — Round-trip via composition_json.**
1. Find where the dialog serialises to `composition_json` on save
   — likely a `_to_composition_json` or similar. Add a
   `"lineage_filter": {…}` entry that carries `min_stars`,
   `colour_labels` (as a list), `flag`, `to_delete`. Skip when
   default (compact JSON).
2. In the dialog's rehydrate path (search for `_from_composition
   _json` or `load_composition`), read the entry back into a
   `LineageFilter` + push it onto the bar via
   `filter_bar.set_filter(...)`.
3. When the Cut runs (spec/61 §1 — cut_member expansion), the
   filter should ALSO narrow the pool. Find where cut_member is
   populated from the composition; apply the LineageFilter there
   too.

**Phase 3 — Save as event DC.**
1. Add a "Save as Dynamic Collection" ghost button next to the
   FilterBar (or in the dialog's action row). Opens a small "Name
   this collection" prompt.
2. Write a new `dynamic_collection` row (via `EventGateway.save_dc`
   or the nearest neighbour — audit the existing DC-save path).
   The DC's filter shape carries the lineage filter under a
   dedicated key so it doesn't collide with spec/32 facets.
3. In the dialog's "Load Collection" path (already exists — see
   `_build_dc_loader`), teach it to re-hydrate the lineage
   filter too when the loaded DC carries one.

**Phase 4 — Tests.**
1. `tests/test_spec159_cut_filter.py` (new):
   - `LineageFilter` on `composition_json` round-trips.
   - Cut composition query respects the filter.
   - "Save as DC" writes the filter into `dynamic_collection`.
2. Extend `tests/test_new_recipe_dialog.py` (or its nearest
   neighbour) so the dialog's live-count reacts to filter changes.

## Non-goals for this session

- **Do NOT** touch `new_cross_event_dc_dialog.py` — cross-event
  revamp is a separate scope conversation (Phase C).
- **Do NOT** extend `global_items_sync.py` — that's Phase D
  (closing spec/159 §8).
- **Do NOT** unify spec/32 item-level and spec/159 lineage-level
  ratings. They stay independent.

## Verification

- QSS guard clean (`python scripts/qss_guard.py`).
- New tests pass. Existing 83-test spec/159 suite still green
  (`python -m pytest tests/test_spec159_*.py`).
- Eyeball: open new_recipe_dialog on a closed event, tick some
  ratings on a few Exported Collection cells first, come back to
  the dialog, dial in a filter, watch the preview count narrow.
  Save the Cut, reopen from the Cut list, filter is re-applied.
  Try "Save as DC", find it in the DC list, load it into a fresh
  new_recipe_dialog session — filter comes back.

## Style expectations

- Follow the CLAUDE.md project instructions: no inline
  `setStyleSheet` (use QSS roles), one-way dependency
  (`mira/ui/` may import from `mira/gateway/` but not the reverse),
  strict offline-first (no network calls anywhere in the touched
  paths).
- Match the codebase's docstring / comment style — see how
  `dc_detail_page.py` documents each new method; carry that
  discipline into the new dialog code.
- After Phase 1 works end-to-end, request an eyeball from Nelson
  BEFORE Phase 3. The visual + interaction of the composition
  preview is worth reviewing incrementally.

## When you're done

Commit + push. Write a short handover doc naming the phases that
landed + anything you deferred. Add follow-up prompts for Phase C /
D if the design conversation moves.
