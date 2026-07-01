# Handover — 2026-06-30 (afternoon) — spec/159 §4–§6+ complete on DCDetailPage

Everything below is **committed + pushed to `main`**. The 83-test
spec/159 suite is green (cycles synchronously, no live machine
state).

## Recent commits (chronological)

| Commit | Topic |
| --- | --- |
| `484ea1a` | spec/159 §6+ — preferred-version surface + cluster + Compare reuse |
| `c6ba3b0` | spec/159 §4.5 — Filters as a group-box bar (reusable) |

Handovers-of-handover context: the morning session (Session A + review
dialog polish) landed as `cb05ce4` → `efa4ba6`. This afternoon
picked up from `HANDOVER-2026-06-30-spec159-exported-collection.md`.

## What this session landed

### Chrome / dialog rebuild

- **New custom-painted widgets** in `mira/ui/exported/rating_widgets.py`:
  `StarRow`, `ColorLabelRow`, `ColorLabelMultiRow`, `FlagToggle`,
  `DeleteToggle`, `PreferredToggle`, `StylePicker`. Theme-aware ink
  via the project palette; zero `setStyleSheet` (QSS guard clean).
- **ReviewMediaDialog** (`mira/ui/exported/review_dialog.py`)
  rebuilt on top of the new widgets. Adds a `← Back` button, a
  Style dropdown (per-item; propagates across versions), and the
  new PreferredToggle (hidden on single-version cells).
- **First-show viewport race fix** — `set_items` now deferred to
  first `showEvent` so the photo lands filling the canvas on open.

### Grid surfacing (§4.4)

- **Versions clusters** on the Exported Collection grid per
  spec/89 §6: `_compute_cells` groups by `source_item_id`, counts
  the virtual Mira intent as a +1 ship intent (via
  `items_with_mira_intent`), builds `_GridCell(kind="cluster")`
  covers with `×N` count chip + optional "N/M to delete" sub-chip.
- **Drill-in / drill-out** with title-bar Back handling
  (`on_titlebar_back` pops the cluster first). Mode + cluster state
  reset on `open_pool` / `close_event`.
- **LRC border-click no longer marks for deletion** (Nelson: "too
  easy accidentally"). Any click on a flat cell opens the review
  viewer; marking happens only inside the dialog (`D` key /
  DeleteToggle) or via the toolbar batch action.
- **Colour label moves from cell top strip to cell border**;
  cluster covers paint a `✓ <origin>` chip when a member is
  preferred. Grid flag glyph reshaped to a pennant with amber pole
  so it reads at cell scale.

### Compare reuse (§6)

- **C-key marks flat cells**; `⇄ Compare (N)` toolbar button shows
  when ≥2 marked.
- **Cluster sub-grid**: button always visible, opens every member
  including the virtual Mira-pending tile.
- **Per-tile provenance caption** (`LRC` / `Mira (pending)` / …)
  via a new `show_titles` flag on `CompareVersionsDialog` — kept
  off for the day-grid reuse (border still carries state there).
- **Per-tile "✓ Use this" action** via `show_use_this` + a new
  `use_this_requested(item_id)` signal. Host writes through
  `set_lineage_preferred` or `set_item_preferred_virtual_mira`.

### Preferred-version surface (§6+)

Two rating datasets in play, mutually exclusive per source item:

| Storage | Column | Semantic |
| --- | --- | --- |
| `lineage.is_preferred` | 0/1, one per row | A real shipped version is preferred |
| `item.preferred_virtual_mira` | 0/1, one per source item | A planned Mira render is preferred (no file yet) |

Gateway writes clear each other's flag inside one transaction:

- `EventGateway.set_lineage_preferred(export_relpath, preferred)`
  clears siblings for the same `source_item_id` + clears the item's
  virtual flag.
- `EventGateway.set_item_preferred_virtual_mira(item_id, preferred)`
  clears every `lineage.is_preferred` for that source.
- `EventGateway.preferred_for_item(source_item_id)` returns the
  preferred lineage row (real) or `None` — downstream Cuts-compose
  reads this to default the included version.
- `LineageRatings` NamedTuple grows `is_preferred: bool`.

The Mira-pending tile in Compare **can be preferred** (Nelson pivot:
the initial "only real rows" rule locked users out in practice —
every Alaska cluster is LRC + virtual Mira). The virtual flag lives
on `item.preferred_virtual_mira`; a future Export commit should
migrate this to the freshly-created lineage row's `is_preferred`
and clear the column (see "Still open" below).

### Filter (§4.5, redesigned)

- **`LineageFilter` predicate** (`mira/ui/exported/filter_popup.py`) —
  the dataclass that carries `min_stars` / `colour_labels` /
  `flag` / `to_delete`. Duck-typed `matches(row)`; used
  everywhere.
- **`FilterBar` widget** (`mira/ui/exported/filter_bar.py`, NEW) —
  the Mira-style group-box bar. Outer `#ProcessGroupBox` "Filters",
  four inner group boxes (Min stars combo, Colour label
  swatches, Flag combo, Marked-for-deletion combo), a
  right-aligned "Showing N of M" indicator + a Clear button.
  Left/right padding 12 px matches the grid's `flow_margin` so
  everything lines up. Reusable: takes / emits `LineageFilter`;
  host owns the predicate.
- **Retired `FilterPopupButton`** — the QToolButton + QMenu popup
  was replaced (Nelson: "the thick marks on the menu items look
  terrible; colours should draw colours, not names"). Module kept
  for `LineageFilter` imports.
- **Tri-state deletion knob** — "Any / ⌫ Show only marked / Hide
  marked" (Nelson: "we need show only marked").
- **DCDetailPage rebuild**: broken "exported500 exported file(s)"
  line dropped; count moves into the FilterBar's indicator. Filter
  is session-local per §4.6 (reset on `open_pool` / `close_event`).

## Schema — v22 → v25 in this session arc

All migrations purely additive; existing rows read as unrated /
unflagged / not-marked / not-preferred.

| Version | Migration | Adds |
| --- | --- | --- |
| v22 → v23 | `_migrate_v22_to_v23` | Session A (spec/159): `lineage.stars` / `color_label` / `flag` / `to_delete` + partial indexes |
| v23 → v24 | `_migrate_v23_to_v24` | `lineage.is_preferred` + partial index |
| v24 → v25 | `_migrate_v24_to_v25` | `item.preferred_virtual_mira` |

`SCHEMA_VERSION` is 25; migration order is stamped in
`MIGRATIONS` (mira/store/schema.py:1739).

## Still open

### spec/159 open items (small)

1. **Promote-on-materialise handoff.** When the next Export run
   produces a Mira JPEG for a source that has
   `item.preferred_virtual_mira = 1`, the freshly-created lineage
   row should pick up `is_preferred = True` and the item column
   should clear. Without this the cover briefly loses its
   `✓ Mira` chip after Export. Lives in the Export commit path
   (see `mira/ui/exported/batch.py`); small.
2. **`docs/round-trip-contract.md`.** Owed from spec/108 §2 —
   never written. Documents the LRC/external-editor return rule
   in user-facing language. Writing only, no code.

### Cut-surface filter extension (large — a scope conversation)

See `AGENT-PROMPT-B-cut-filters.md` in the repo root for the
self-contained prompt to hand to a new agent. Summary:

- **Phase B**: extend `new_recipe_dialog.py` Filters section
  with a `FilterBar` (lineage-rating flavour). Route through
  Cut-recipe JSON so filters round-trip on save/load. Save
  as event DC.
- **Phase C** (deferred): revamp `new_cross_event_dc_dialog.py`
  filter UI to the same group-box look (using its existing
  spec/32 facets — two rating datasets coexist).
- **Phase D** (deferred): close spec/159 §8 by extending
  `global_items_sync.py` to carry lineage ratings, so
  cross-event DCs can filter on per-version ratings.

Key design questions for whoever picks up Phase B:

1. **Storage scope** for the FilterBar state on a Cut:
   session-local, saved on Cut recipe JSON (round-trips on
   save/load), savable as an event DC, or all of the above?
2. **Rating dataset**: use spec/159 lineage-level (my
   recommendation for closed-event Cut compose) or spec/32
   item-level (already used by the cross-event surface)?
3. **FilterBar reuse shape**: single generalised widget wired
   to a "filter spec" descriptor, or a sibling widget for the
   spec/32 shape?

### Nelson polish items

- FilterBar padding: 12 px left/right, matches grid `flow_margin`
  (Nelson eyeball 2026-06-30 — "perfect").
- Every rating widget is theme-aware; QSS guard baseline = 0.

## File map for the next agent

| File | Role |
| --- | --- |
| `mira/store/schema.py` | SCHEMA_VERSION=25; migrations v23/v24/v25 for the new columns |
| `mira/store/models.py` | `Lineage.is_preferred`, `Item.preferred_virtual_mira` |
| `mira/gateway/event_gateway.py` | `set_lineage_preferred`, `set_item_preferred_virtual_mira`, `preferred_for_item`, `LineageRatings.is_preferred` |
| `mira/ui/exported/rating_widgets.py` | Every custom-painted rating widget; `ColorLabelMultiRow` for filter multi-select |
| `mira/ui/exported/filter_popup.py` | `LineageFilter` predicate (only) — popup retired |
| `mira/ui/exported/filter_bar.py` | The reusable group-box FilterBar widget |
| `mira/ui/exported/review_dialog.py` | ReviewMediaDialog + Back button + Style picker + PreferredToggle |
| `mira/ui/exported/compare_dialog.py` | `show_titles`, `show_use_this`, `use_this_requested` — Compare tile actions |
| `mira/ui/shared/dc_detail_page.py` | Cluster surfacing, drill-in/out, FilterBar wiring, preferred-flag chrome |
| `tests/test_spec159_lineage_ratings.py` | 40 gateway tests (added preferred coverage) |
| `tests/test_spec159_dc_detail_page.py` | 29 surface tests |
| `tests/test_spec159_filter.py` | 17 tests — LineageFilter predicate + FilterBar |

## Quick eyeball protocol

1. Restart Mira; open Alaska → closed → Cut page → Open on `#exported`.
2. **FilterBar bar** sits below the title row: `Filters` group box
   with Min stars / Colour label swatches / Flag / Marked-for-del.
3. Click any 2-version cluster → drill-in → `⇄ Compare versions`
   → per-tile "✓ Use this" toggles; `LRC` / `Mira (pending)`
   captions.
4. `✓ Use this` on either tile → close → cluster cover gets a
   `✓ LRC` (or `✓ Mira`) chip in top-left.
5. Back → resets to flat, filter persists in-session, resets on
   Back-to-Cuts + re-enter.

## Test surface

The 83 spec/159 tests run in ~2 s locally. Split across three
files (see file map). Full-suite hasn't been re-run since the
session start; the local suite is representative.
