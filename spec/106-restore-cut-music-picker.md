# 106 — Restore the music / audio picker in the Cut composition dialog

**Status: PROPOSED (Nelson 2026-06-22). Fixes a regression: the Cut
composition dialog (`NewRecipeDialog`) no longer exposes a music / audio
control, so the user can't choose a soundtrack category for a Cut. The
data path is fully intact end-to-end — only the picker widget is missing.
Touches `mira/ui/pages/new_recipe_dialog.py` (add the control +
`presentation_payload`), and a verify pass on the recipe→cut-draft adapter
and `share_cuts_page` wiring. No keymap / charter-invariant impact.**

## 1. What's broken

During the spec/90 dialog rework, `music_category` (and `card_style` /
`overlay_fields` / `separators`) were tagged "unimplemented Phase 4 fields"
and **dropped from the dialog UI**, parked on a "spec/61 §3.1 settings
surface" that was never wired. Result: there is **no way in the UI to set
a Cut's music** anymore.

Everything *around* the control still works:

- `EventGateway.create_cut(..., music_category=...)` and `update_cut`
  accept and persist it.
- `share_cuts_page` already builds `music_categories` (from
  `audio_library.list_moods(audio_library_path)`) + a `music_hint` for
  empty states and passes them into the dialog via `_dialog_kwargs`; and
  it seeds the prefill with the cut's current `music_category`
  (share_cuts_page ~1797).
- `export_cut` builds the `audio/` playlist from `cut.music_category`
  (spec/105 §4).
- `NewRecipeDialog.presentation_payload()` **deliberately omits**
  `music_category` (its docstring says so), so even the value that's
  passed in never comes back out.

So the prefilled music value round-trips invisibly and any new Cut gets no
soundtrack. The fix is to re-add the picker and stop dropping the field.

## 2. The fix

### A. Add the picker to `NewRecipeDialog`

In the presentation section (next to the runtime spinners), add a
**music-category combo**:

- Populate from the existing `music_categories` kwarg (already wired into
  `_dialog_kwargs`). Include a blank / "No music" entry at the top so a
  Cut can opt out.
- When `music_categories` is empty, show the existing `music_hint` (either
  "No category folders found in {path}…" or "Set the audio library folder
  in Settings to enable music.") and disable the combo — same empty-state
  copy the page already computes.
- **Seed it from the prefill** — the cut's current `music_category` when
  editing (the value already arrives via the prefill context); default to
  "No music" for a brand-new Cut.

### B. Stop dropping the field

In `presentation_payload()`, **include** `music_category` (the combo's
selected value, or `None`/`""` for "No music"). Remove it from the
"Phase 4 gaps left out" exclusion in the docstring. `composition()` then
carries it through the `presentation` block automatically.

### C. Verify the carry-through (no new plumbing expected)

- Confirm `recipe_to_cut_draft` (the presentation → Cut-draft adapter)
  reads `presentation["music_category"]` into the draft; add the one-line
  mapping if it's currently tolerantly defaulting it away.
- Confirm `share_cuts_page`'s new-Cut and edit-Cut handlers pass the
  draft's `music_category` into `create_cut` / `update_cut`. (create_cut
  already takes the arg, so this should be a no-op once §B emits it.)

## 3. Acceptance

- With an audio library configured, the Cut composition dialog shows a
  music-category combo listing the mood folders; picking one and saving
  produces a Cut whose `music_category` is set, and exporting it fills the
  `audio/` playlist.
- Editing an existing Cut shows its current music category pre-selected
  and lets the user change or clear it.
- With no audio library / no category folders, the combo is disabled and
  the existing hint explains how to enable music — no crash, no silent
  empty control.
- "No music" produces a Cut with no soundtrack (empty `audio/`),
  unchanged from a Cut that never had music.

## 4. Tests

- `tests/test_new_recipe_music.py` — given `music_categories=["calm",
  "happy"]`, the dialog renders the combo; selecting "happy" makes
  `composition()["presentation"]["music_category"] == "happy"`; a prefill
  of "calm" pre-selects it; empty `music_categories` disables the combo
  and surfaces the hint.
- An end-to-end test: dialog → `create_cut` persists `music_category`;
  `export_cut` then builds a non-empty playlist for that category.
- Regress the existing `NewRecipeDialog` composition/presentation tests
  (they assert the schema — update for the added field).

## 5. Note — the other Phase-4 gaps

`card_style`, `overlay_fields`, and `separators` were dropped in the same
sweep and are also unreachable from this dialog. They're out of scope here
(music is the reported regression), but worth a follow-up ticket: the same
"parked on a settings surface that was never wired" gap applies to all
four.
