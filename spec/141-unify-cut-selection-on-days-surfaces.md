# 141 — Reuse Days List + Days Grid for Cut item selection (retire the bespoke session stack)

**Status: PROPOSED (Nelson 2026-06-23). Picking items for a Cut uses a
**bespoke** days-panel → day-grid → single-view stack (`CutSessionPage`),
reimplementing navigation the real **Days List** + **Days Grid** already do.
Its hand-rolled Back dispatch is buggy: from a day's grid, Back to pick the
**next day** instead **closes the Cut unsaved**. The duplication exists only
because the Cut surface shows **exported files** (`SessionFile` /
`export_relpath`, the `#exported` pool) while the Days surfaces show
**captured items** (`item_id`, schedule days, item thumbs). That's a
**data-source** difference, not a UX one. Fix: generalise the Days List +
Days Grid to a **pluggable cell source**, add a **Cut-selection mode**, and
host the Cut session on those real surfaces — which fixes the Back bug, the
scroll-restore (spec/131), and removes a large duplicated stack. Touches
`mira/ui/pages/days_grid_page.py`, `mira/ui/pages/days_lists_page.py`,
`mira/ui/shared/cut_session_page.py` (reduced to a thin host or retired),
`mira/ui/pages/share_cuts_page.py`. No data-model change — the Cut draft
ledger stays as is.**

## 1. The real difference: cell source, not navigation

- **Days surfaces (phases):** cells from `day_grid_cells(eg, day, phase)` —
  captured items, keyed by `item_id`, item thumbs, decision = `phase_state`.
- **Cut session:** cells from the Cut **pool** — exported files
  (`SessionFile` / `export_relpath`), export-folder thumbs
  (`.cache/thumbs/exports/`), decision = **in/out of the Cut** (a separate
  draft ledger; pool algebra `#exported − … + …`).

Everything else — the day panel, the grid of selectable thumbnails, the
single-view step-through, Back/Esc, scroll restore — is identical UX that
`CutSessionPage` re-built and the Days surfaces already own.

## 2. Generalise the Days surfaces to a pluggable cell source

Introduce a small **`CellSource`** seam the Days Grid + Days List consume:

- `days() -> [(day_number, label, count, picked_count)]` (drives the list).
- `cells(day_number) -> [Cell{id, thumb_key, selected, kind}]` (drives the
  grid).
- `toggle(cell_id) -> selected` / `set_selected(cell_id, bool)` (the
  decision write).
- `thumb(cell) -> pixmap/async key` (item thumbs **or** export thumbs).

Two implementors:

- **`PhaseCellSource`** — today's behaviour (captured items, `phase_state`),
  for Pick / Edit / Export. The Days surfaces wrap their current logic behind
  this so nothing regresses.
- **`CutPoolCellSource`** — the Cut pool: cells = exported files grouped by
  day, `selected` = in the Cut draft, `toggle` writes the Cut decision
  ledger, thumbs = the export-thumb tier. This replaces the bespoke
  `CutSessionPage` content.

The Days Grid's selection visual (Pick/Skip border) is reused as **in/out of
Cut**; the Days List's per-day bar shows **selected ÷ available** for the Cut.

## 3. Cut session hosted on the real surfaces

- New-Cut / Adjust opens **Days List (Cut mode)** → click a day → **Days Grid
  (Cut mode)** for that day → optional single view; selection toggles Cut
  membership in the draft.
- **Back pops one level** (grid → day list → … → the Cut dialog/Detail), the
  proven Days navigation — **never closes/discards the Cut**. The draft lives
  in the `CutSession` for the whole flow; only **Create Cut** commits and
  **Cancel** discards (explicit), exactly as today.
- Scroll restore (spec/131) and the dive-return anchor come for free.
- "Selection only in the grid" is fine (the user's note — not a firm
  restriction); the single view can still toggle, but the grid is the primary
  selection surface.

## 4. Retire / reduce the bespoke stack

- `CutSessionPage`'s days-panel / day-grid / single-view + its
  `on_titlebar_back` dispatcher are **retired** (or it becomes a thin host
  that wires the Cut-mode Days surfaces + the budget line + "Create Cut").
- The buggy Back dispatch is gone by construction.

## 5. Acceptance

- Picking Cut items uses the **same** Days List + Days Grid as the phases
  (same chrome, navigation, scroll restore); Back from a day's grid returns
  to the **day list** to pick another day — **the Cut is never closed
  unsaved**.
- Toggling an item in the grid adds/removes it from the Cut draft; the day
  list shows per-day selected counts; **Create Cut** commits, **Cancel**
  discards.
- Pick / Edit / Export grids and lists are unchanged (they run the
  `PhaseCellSource`).
- Per-event and cross-event Cut composition both use the unified flow.

## 6. Implementation plan (commit order)

1. **`CellSource` seam** — extract today's Days Grid + Days List logic behind
   `PhaseCellSource`; no behaviour change (pure refactor, green suite).
2. **`CutPoolCellSource`** — exported-file pool, export thumbs, Cut-draft
   toggle; unit-tested against a sample Cut.
3. **Cut-mode hosting** — `share_cuts_page` drives the Days surfaces in Cut
   mode for New-Cut / Adjust; Back pops levels; draft held in `CutSession`;
   Create/Cancel unchanged. Remove the bespoke `CutSessionPage` navigation.
4. **Cleanup** — delete dead bespoke widgets/tests; update spec/61 to point
   the Cut-session UI at the shared surfaces.

## 7. Tests

- `tests/test_cellsource_phase_parity.py` — `PhaseCellSource` reproduces the
  current Days Grid/List cells + decisions for Pick/Edit/Export (regression
  guard for the refactor).
- `tests/test_cut_pool_cellsource.py` — `CutPoolCellSource` yields the Cut
  pool's exported files grouped by day; `toggle` writes the Cut draft;
  selected counts roll up per day.
- `tests/test_cut_selection_back_nav.py` — from a day's grid, Back returns to
  the day list and the **Cut draft survives** (no close); only Create commits,
  Cancel discards. (This is the reported bug, pinned.)
- Regress the Days surfaces under `PhaseCellSource`.
