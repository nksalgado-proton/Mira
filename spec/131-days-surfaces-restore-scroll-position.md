# 131 — Days List + Days Grid: return to where you were, not the top

**Status: PROPOSED (Nelson 2026-06-23). Diving from the Days Grid into a
photo (or from the Days List into the Grid) and coming back always lands the
surface **scrolled to the top, first item visible** — losing the user's
place. Both surfaces rebuild from scratch on return with no scroll/anchor
memory. Worse, because both intermediate levels let the user navigate while
"dived in" (the Grid has prev/next **day**, the viewer has next **item**),
the right target isn't even the dive point — it's the **last** position the
user was on. Fix: track a restore **anchor** that flows back up the stack —
the viewer reports its last `item_id`, the Grid scrolls to + selects that
cell on return and reports its last `day_number`, and the List scrolls to +
selects that day card. Touches `mira/ui/pages/days_grid_page.py`,
`mira/ui/pages/days_lists_page.py`, the shared `ThumbGrid` (scroll-to-cell
helper), the viewer's back contract, and the host wiring in
`mira/ui/shell/main_window.py`. No data-model change.**

## 1. The model — anchor flows up the dive stack

The dive is **List → Grid (one day) → viewer (one item)**. On the way back,
each level restores to the anchor the level below reports:

- **Viewer → Grid:** the viewer (Picker / preview) reports the **last
  `item_id`** it was showing when the user backs out (it may have stepped to
  other items / days). The Grid restores to that item.
- **Grid → List:** the Grid reports the **last `day_number`** it was showing
  (it may have used prev/next-day). The List restores to that day.

"Restore to the last position" (not the dive point) is the goal the user
asked for and falls out of reporting the *current* anchor on back, not the
*entry* anchor.

## 2. Days Grid restore

- `DaysGridPage` gains an optional **`anchor_item_id`** applied after the
  day's cells are built: scroll the `QScrollArea` so that cell is visible and
  **select / focus** it (visible highlight, so the eye finds it).
- Because `ThumbGrid` builds cells in chunks (`_thumb_pending`), the scroll
  must run **after the target cell exists** — defer via the build-complete
  signal, or compute the cell's row/offset from its known index and scroll
  there immediately (index → y-offset), then refine when built. Never scroll
  before the cell is laid out (that's the current top-pinned result).
- Add a `ThumbGrid` helper: `ensure_item_visible(item_id)` /
  `select_item(item_id)` (locate the cell, `ensureWidgetVisible`, set the
  selected/focus state).
- On `item_activated(item_id)`, the Grid records `item_id` as its **entry**
  anchor (fallback if the viewer reports nothing).

## 3. Days List restore

- `DaysListsPage` gains an optional **`anchor_day_number`** applied after the
  day cards are built: scroll so that `DayRow` is visible and highlight it.
- Add a helper to locate a `DayRow` by `day_number` and `ensureWidgetVisible`
  it.
- On `day_activated(day_number)`, the List records it as the entry anchor.

## 4. Reporting the last position (the "better" behaviour)

- **Viewer back contract:** the Picker / preview's `closed`/back signal
  carries the **current `item_id`** (and its `day_number` if it crossed
  days). If the viewer stepped across days, the host opens the Grid on that
  day first, then anchors the item.
- **Grid back contract:** `back_requested` carries the Grid's **current
  `day_number`** so the List can anchor it; prev/next-day already update the
  Grid's `day_number`, so the report is just the live value.
- The host (`main_window`) threads these: viewer-close → `open_for_day(…,
  anchor_item_id=…)`; grid-back → `days_lists_page` shown with
  `anchor_day_number=…`.

## 5. Acceptance

- Dive into a photo from the Grid, page through a few items (and/or to the
  next day), back out → the Grid is scrolled to and highlights the **last**
  item viewed (on the right day), not the top.
- Dive into a day from the List, navigate prev/next day in the Grid, back out
  → the List is scrolled to and highlights the **last** day, not the top.
- A fresh open of either surface (no anchor) behaves as today (top).
- Works in Pick / Edit / Export grid modes.

## 6. Tests

- `tests/test_days_grid_restore.py` — `open_for_day(anchor_item_id=X)` scrolls
  the scroll area so X's cell is visible + selected after the chunked build;
  no anchor → top; an anchor for an item not on the day → top (graceful).
- `tests/test_days_list_restore.py` — showing the List with
  `anchor_day_number=N` scrolls to + highlights that `DayRow`; no anchor →
  top.
- `tests/test_dive_return_anchor_flow.py` — viewer-close reports the current
  `item_id` (after stepping) → Grid restores to it; grid-back reports the
  current `day_number` (after prev/next-day) → List restores to it.
- `ThumbGrid.ensure_item_visible` unit test (locate + scroll a known cell).
