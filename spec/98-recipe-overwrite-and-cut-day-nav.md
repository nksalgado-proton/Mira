# 98 — Recipe overwrite + Cut-session day navigation

**Status: PROPOSED (Nelson 2026-06-22). Two independent fixes in the
Recipe / Cut workflow: (1) saving a **Recipe, Collection, OR Cut** under
an existing name offers no overwrite — all three only say "pick another";
(2) the Cut picking session strands the user in the first day's grid
because the route back to the days panel has no visible control. Touches
`mira/ui/pages/new_recipe_dialog.py`, `mira/ui/pages/events_page.py`
(dc/cut replace callbacks), the cut-commit path, and
`mira/ui/shared/cut_session_page.py`. No charter-invariant impact.**

---

## Part 1 — Overwrite on a name collision (Recipe / Collection / Cut)

### Today

All three "definition" saves reject a duplicate name and offer **no
overwrite**, even though each already has the lookup + update API to
support one:

| Type | Save raises on collision | Lookup | Update |
|---|---|---|---|
| **Recipe** | `RecipeStore.create` → `RecipeNameTakenError` | `RecipeStore.by_name(name, flavour)` | `RecipeStore.update(id, *, composition=…)` |
| **Collection (DC)** | `create_dc` → `check_tag` → dc_creator raises `ValueError("taken")` | `dc_by_tag(slug)` | `update_dc(dc_id, expr, filters)` / `rename_dc` |
| **Cut** | `create_cut` / `create_cross_event_cut` re-validate the name (reject) | `cut_by_tag(slug)` / cross-event equiv | `update_cut_settings(...)` + `set members`; `rename_cut` |

So a user who saved a wrong Recipe/Collection/Cut and recreates it under
the same name is forced to invent a new name.

### Change — consistent "Replace existing?" across all three

When a save hits a name-taken collision, offer **Replace** / **Cancel**
(Cancel keeps today's "pick another" path; the inline message stays as
the fallback).

- **Recipe** (`new_recipe_dialog.py::_on_save_recipe_clicked`, the
  `except RecipeNameTakenError` branch): on Replace →
  `existing = recipe_store.by_name(name, flavour)`;
  `recipe_store.update(existing.id, composition=self.composition())`;
  `recipe_saved.emit(updated)`; toast "Recipe '{name}' updated."
- **Collection** (`_on_save_as_dc_clicked`, the `code == "taken"`
  branch): on Replace → resolve + overwrite the existing DC. The dialog
  reaches the store through the host `dc_creator` callback, so add a
  sibling host callback `dc_replacer(name, expr, filters)` (wired in
  `events_page.py` next to `_dc_creator`) that does
  `sf = library_gateway.dc_by_tag(slugify(name)); library_gateway.update_dc(sf.id, expr=expr, filters=filters)`
  and returns the operand. Dialog calls `dc_replacer` on Replace.
- **Cut** (the commit path — `CrossEventPickerDialog._on_commit` /
  `cut_session_page` commit, where `create_cut` /
  `create_cross_event_cut` raises name-taken): on Replace → **adopt the
  existing cut's id and update it** rather than creating a new one. i.e.
  look up `cut_by_tag(slug)` (or the cross-event equivalent), set
  `session.cut_id = existing.id`, and re-run `commit` — which then takes
  the existing **update** branch (`update_cut_settings` +
  `set_*_members`), overwriting settings + membership in place. (This
  reuses the re-entered-session path already in `commit`.)

Guard every lookup miss (returns None) defensively — fall back to the
inline "pick another" message rather than crashing.

> Implementation note: keep the three confirms worded the same ("A
> {Recipe|Collection|Cut} named '{name}' already exists. Replace it?") so
> the overwrite UX is uniform.

---

## Part 2 — Cut session: behave like the other four phases (days list first, then grid with a Back button)

### Decision (Nelson 2026-06-22)

Make the Cut picking session match the navigation of every other phase:
**open on the day list, then open a day into its grid, with a visible
Back button on the grid** to return to the list. No jumping straight
into day 1.

### Today

`cut_session_page.py` is a 3-level stack: days panel (0) → day grid (1)
→ single view (2). On start it **jumps straight to the first day's grid**
(`if self._groups: self._open_day(0)`, ~line 554 — the old "land on
photos" eyeball). The only routes back to the days panel are the **Esc**
key or the app **title-bar Back** — there is **no visible Back control in
the day-grid chrome** (`grid_chrome` holds only the day header + a
stretch). So the days panel, and every other day, look inaccessible.

### Change

1. **Open on the day list** (stack index 0) — the same "choose a day
   first" entry every other phase uses. (Keep `_refresh_days()` so the
   panel is populated on entry.) **Single exception:** when there is
   exactly **one** day, skip the list and open that day's grid directly
   (a one-row list is pointless) — i.e. `_open_day(0)` only when
   `len(self._groups) == 1`, otherwise stay on the panel.
2. **Visible Back on the grid.** Add a ghost **"‹ Back"** (or "‹ Days")
   button to the day-grid chrome (`grid_chrome` `QHBoxLayout`, left of
   `_grid_header`), wired to `self._back_to_days`, tooltip "Back to the
   day list (Esc)". This mirrors the day-grid Back the other phases
   carry. Esc and `on_titlebar_back` keep working as the same path.

Net: identical shape to Collect/Pick/Edit/Export — pick a day from the
list, work its grid, Back to the list, repeat.

---

## Tests

- Recipe: Replace → `RecipeStore.update` called with the new composition
  + `recipe_saved` emitted (stub store); Cancel → no update, dialog stays
  open; `by_name` miss → inline fallback, no crash.
- Collection: Replace → `dc_replacer` resolves the existing DC and calls
  `update_dc`; Cancel → today's behaviour.
- Cut: a name-taken commit → Replace adopts `cut_by_tag(slug).id` onto
  the session and re-commits via the update branch (asserts
  `update_cut_settings` + members replace, not a second create).
- Cut session: with ≥2 days, on entry the stack is at index 0 (days
  panel), NOT a day grid; opening a day → grid (index 1); the grid Back
  returns to index 0. With exactly 1 day, entry opens the grid directly
  (index 1). Simple widget-exists + index checks.

## Acceptance (Nelson eyeball)

- Save a Recipe / Collection / Cut, then save another with the same name
  → each prompts to Replace; replacing overwrites the first (no
  duplicate, no forced rename).
- Start picking a Cut → land on the **day list** (like every other
  phase); open a day → its grid, with a visible **Back** that returns to
  the list; every day is listed and openable.
