# 121 — Two Cut fixes: aspect not pre-filled on edit · PTE file should be named after the Cut

**Status: PROPOSED (Nelson 2026-06-23). Two independent, small bugs.
(1) A Cut saved with a non-default aspect (e.g. 4:3) exports correctly, but
re-opening the Edit dialog shows **16:9** — the edit prefill never carries
`aspect`, so the dialog falls back to the `NewRecipeContext` default.
(2) The generated `.pte` is always named **`slideshow.pte`** regardless of
the Cut; it should take the **Cut's name**. Touches
`mira/ui/pages/share_cuts_page.py` (prefill) and
`mira/shared/pte_project.py` (output naming) + the one caller. No
data-model change — both values already exist on the Cut.**

## 1. Aspect not pre-filled on edit

### The bug
`_on_edit_cut`'s prefill `SimpleNamespace` (share_cuts_page.py ~line 1834)
forwards name / pool / styles / type / state / durations / music / overlay
— but **not `aspect`**. `_apply_recipe_prefill` (~1364) likewise never
touches `ctx.aspect`. So the `NewRecipeContext` keeps its field default
`aspect = "16:9"` (new_recipe_dialog.py:267), and the aspect combo seeds to
16:9 for every edit. The Cut's real `aspect` is on disk (the export honoured
it), it just isn't read back into the dialog. The cross-event edit prefill
(~line 2685) has the same omission.

### The fix
- Add `aspect=cut.aspect` to the edit prefill `SimpleNamespace` (both the
  per-event ~1834 and cross-event ~2685 builders).
- In `_apply_recipe_prefill`, read it and seed the context:
  `aspect = getattr(prefill, "aspect", None)`; if set,
  `ctx.aspect = core.cut_aspect.normalise(aspect)`. (The dialog already
  seeds `self._aspect` + the combo index from `ctx.aspect` at construction,
  new_recipe_dialog.py:1703 / 3019 — so seeding the context is all that's
  needed.)

### Acceptance
- Opening Edit on a Cut saved as 4:3 (or 3:2 / 1:1) shows that aspect
  pre-selected; saving without touching it keeps it; per-event and
  cross-event both correct.

## 2. PTE file should be named after the Cut

### The bug
`pte_project.slideshow_target` hardcodes
`DEFAULT_OUTPUT_NAME = "slideshow.pte"` (and `"slideshow (N).pte"` on
collision). Every Cut's project lands as `slideshow.pte`, so a folder of
exported Cuts is a wall of identically-named projects.

### The fix
- Give `slideshow_target` and `generate_into_folder` a `stem` parameter
  (default `"slideshow"` for back-compat): output `"<stem>.pte"`, collision
  `"<stem> (N).pte"`.
- The caller `share_cuts_page._generate_pte_into_folder` passes the Cut's
  name as the stem — `cut.tag` (the same slug the export folder already
  uses, so it's filesystem-safe), falling back to `"slideshow"` when empty.
  Sanitise via `core.cut_names.slugify_*` if the display name is ever used
  instead of the tag.
- `project_path=target` already flows into `[Main] ProjectFilePath`, so the
  `.pte`'s internal path stays consistent with the new filename for free.

### Acceptance
- A Cut named e.g. `iceland-highlights` exports `iceland-highlights.pte`;
  two different Cuts in one folder get distinct project filenames; the
  collision disambiguator uses the Cut stem; an empty/missing name still
  yields `slideshow.pte`.

## 3. Tests

- `tests/test_new_recipe_aspect_prefill.py` (or extend the spec/111 dialog
  test) — an edit prefill carrying `aspect="4:3"` seeds the combo to 4:3;
  absent aspect falls back to 16:9; cross-event path identical.
- Extend `tests/test_pte_project.py` — `slideshow_target(folder,
  stem="my-cut")` → `my-cut.pte`, collision → `my-cut (2).pte`, default
  stem unchanged; `generate_into_folder(..., stem=…)` writes the named
  file; `_generate_pte_into_folder` threads `cut.tag`.
