# Handoff — Recipe overwrite + Cut-session day navigation (spec/98)

Branch: **main**. Read CLAUDE.md and spec/98. Two small, independent
fixes; keep verification modest (a couple of targeted tests + an
eyeball — no need for the whole suite).

## Part 1 — Overwrite on name collision: Recipe, Collection, AND Cut

All three reject a duplicate name with "pick another" and offer no
overwrite, though each has the lookup + update API. Add a consistent
"Replace existing?" (Replace / Cancel; Cancel keeps today's path).

**Recipe** — `new_recipe_dialog.py::_on_save_recipe_clicked` (~3197), the
`except RecipeNameTakenError` branch. On Replace:
`existing = self._recipe_store.by_name(name, self._flavour)`; if not
None → `updated = self._recipe_store.update(existing.id,
composition=self.composition())`; `recipe_saved.emit(updated)`; toast;
return. None → inline fallback + `continue`.

**Collection** — `new_recipe_dialog.py::_on_save_as_dc_clicked` (~2326),
the `code == "taken"` branch. The dialog reaches the store via the host
`dc_creator` callback, so add a sibling callback `dc_replacer(name, expr,
filters)` wired in `events_page.py` next to `_dc_creator`:
`sf = library_gateway.dc_by_tag(slugify(name)); library_gateway.update_dc(sf.id, expr=expr, filters=filters)` → return the operand.
On Replace the dialog calls `dc_replacer` instead of `dc_creator`. Thread
`dc_replacer` through the `NewRecipeDialog` constructor like `dc_creator`.

**Cut** — the commit path where `create_cut` / `create_cross_event_cut`
raises name-taken (`CrossEventPickerDialog._on_commit` and the
`cut_session_page` commit). On Replace, **adopt the existing cut's id and
re-commit** rather than creating a second one: look up `cut_by_tag(slug)`
(or the cross-event equivalent), set `session.cut_id = existing.id`, and
call `session.commit(...)` again — it then takes the existing update
branch (`update_cut_settings` + members replace). This reuses the
re-entered-session path already in `commit`.

Guard every lookup miss (None) → fall back to the inline "pick another"
message, no crash. Keep the three confirms worded the same: "A
{Recipe|Collection|Cut} named '{name}' already exists. Replace it?"

## Part 2 — Cut session: days list first, then grid with a Back button

Make it behave like every other phase (Collect/Pick/Edit/Export): land on
the **day list**, open a day into its **grid**, with a visible **Back** to
return to the list.

File: `mira/ui/shared/cut_session_page.py`.

1. **Open on the day list.** Today the constructor jumps straight to the
   first day's grid: `if self._groups: self._open_day(0)` (~line 554).
   Change so it stays on the days panel (stack index 0) on entry —
   **except when there is exactly one day**, where you skip the list and
   open that day directly:
   ```python
   if len(self._groups) == 1:
       self._open_day(0)
   # else: stay on the days panel (index 0); _refresh_days() already ran
   ```
2. **Visible Back on the grid.** The day-grid chrome is the `grid_chrome`
   `QHBoxLayout` (~line 516) holding `self._grid_header` + a stretch. Add
   a ghost **"‹ Back"** button left of `_grid_header`, wired to
   `self._back_to_days` (already exists ~line 599 — sets the stack to
   index 0 and refreshes the panel). Tooltip "Back to the day list (Esc)."
   Esc and `on_titlebar_back` keep working as the same path.

## Tests (modest)

- Recipe: stub `RecipeStore` raises `RecipeNameTakenError` on `create`;
  Replace → `update(existing.id, composition=…)` called + `recipe_saved`
  emitted; Cancel → no update, dialog stays open; `by_name` None → inline
  fallback, no crash.
- Collection: Replace → `dc_replacer` resolves the DC and calls
  `update_dc`; Cancel → unchanged.
- Cut: name-taken commit → Replace adopts `cut_by_tag(slug).id` and
  re-commits via the update branch (assert `update_cut_settings` + member
  replace, not a second create).
- Cut session: with ≥2 days, entry leaves the stack at index 0 (days
  panel) and the grid has a visible Back that returns to 0; with exactly
  1 day, entry opens the grid (index 1). Widget-exists + index checks.

Run only the directly-related test files (recipe dialog / cut session),
then a quick launch eyeball. No full `verify.bat` needed for this.

## Commit + push (on main)

```
fix: Recipe/Collection/Cut overwrite on name collision + visible "Days" back in Cut session (spec/98)

- new_recipe_dialog + events_page: saving a Recipe, Collection, or Cut
  under an existing name now offers Replace instead of only forcing a new
  name. Recipe → by_name + update; Collection → new dc_replacer
  (dc_by_tag + update_dc); Cut → adopt cut_by_tag id and re-commit via the
  update branch.
- cut_session_page: open on the day list (like the other phases), then a
  day's grid with a visible "‹ Back". Single exception: jump straight to
  the grid when there's only one day. (Was: always jumped to day 1 with
  no visible way back to the list.)
```

Then `git push` on `main`.
