# 106 — Restore the music / audio picker in the Cut composition dialog

**Status: SHIPPED (Nelson 2026-06-22) — commit
[ae2b2b9](https://github.com/nksalgado-proton/Mira/commit/ae2b2b9). The
music / soundtrack picker, dropped from `NewRecipeDialog` in the spec/90
rework, is restored: a `RuntimeMusicCombo` in the Runtime row (populated
from `music_categories`, a "No music" opt-out at the top, disabled-with-hint
when no audio library, seeded from the cut's `music_category` prefill).
`_on_music_changed` tracks the value and `presentation_payload` now emits
`music_category`, so the dialog ↔ Cut round trip is exact and `export_cut`
builds the `audio/` playlist for it. The overlay / `card_style` /
`separators` Phase-4 gaps are NOT part of this — overlays moved to their
own spec (see spec/114). Original proposal follows.**

## 1. What was broken

During the spec/90 dialog rework, `music_category` was tagged an
"unimplemented Phase 4 field" and **dropped from the dialog UI**. The data
path was intact end-to-end (`create_cut`/`update_cut` persist it,
`share_cuts_page` feeds `music_categories`/`music_hint` + the prefill,
`export_cut` builds the playlist) — only the picker widget was missing, and
`presentation_payload()` deliberately omitted the field so even the
prefilled value never came back out.

## 2. The fix (as shipped)

- A **music-category combo** in the presentation/runtime row, populated
  from `music_categories`, with a top **"No music"** opt-out; disabled +
  hint when no audio library; seeded from the cut's current
  `music_category`.
- `presentation_payload()` now **emits** `music_category`; `composition()`
  carries it through; `recipe_to_cut_draft` + `share_cuts_page` thread it
  into `create_cut`/`update_cut`.

## 3. Acceptance / tests (met)

- The dialog shows the combo; selecting a category persists it; editing
  pre-selects; empty library disables with the hint; "No music" = no
  soundtrack. Covered by `tests/test_new_recipe_music.py`.
