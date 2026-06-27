# 156 — Creative-filter strength graduation

**Status: DONE (Nelson 2026-06-27).** Adds a per-image STRENGTH control to
the Edit page's creative filters so the user can dial a filter's effect up
or down instead of getting only its full, shipped intensity. Applies to
photos AND video segments, baked consistently through every render path
(preview, F10 lens, photo/batch export, video export).

## The control

A **dropdown** (not a slider) sits directly **below the filter combo in
the same Filter group box** of the Edit adjustment surface. Five steps:

| Step | Label | Multiplier on the filter's blend amount |
|---|---|---|
| +2 | full | 1.00 — **today's shipped recipe** (the maximum) |
| +1 | | 0.85 |
| **0** | **medium (default)** | **0.70** |
| −1 | | 0.55 |
| −2 | subtle | 0.40 (never fully off — a selected filter always shows) |

The curve is **gentle linear**: `scale = 0.7 + 0.15 · strength`, clamped to
the ±2 stops. The shipped recipes read a touch strong at full, so the new
**default of 0 eases every filter back to ~70 %** out of the box; the user
bumps to +2 for the old look or down for a whisper. The dropdown is greyed
out when no filter is chosen (nothing to scale).

## Why a default of 0 (not +2)

Existing recipes were authored at "full". Defaulting new + legacy rows to 0
means a re-export of an already-filtered photo dials the effect back from
the previous full-strength bake — the deliberate behaviour (the filters
were a little heavy). +2 reproduces the pre-156 look exactly.

## Data model

- `Adjustment.filter_strength` (photos) and `VideoAdjustment.filter_strength`
  (segments): `REAL NOT NULL DEFAULT 0.0 CHECK (BETWEEN -2 AND 2)`. Schema
  **v20 → v21** (`_migrate_v20_to_v21`, additive ALTER on both tables).
  Inert when `creative_filter` is NULL.
- The clamp lives at the gateway/persist seam (migrated rows skip the CHECK,
  same belt-and-braces as `user_exposure` / `look_strength`).

## The one composition point + the consumers

`core.photo_auto.filter_strength_scale(strength)` is the **single source of
truth** for the graduation. The final `apply_filter` amount everywhere is:

```
creative_filter_amount(key)  ×  filter_strength_scale(strength)
```

Render paths, all reading `filter_strength` from the row / CHOICE and
multiplying — so the Edit preview equals the export:

1. `AdjustmentSurface.render_now` + `render_full_pixmap` — live Edit preview
   (`self._filter_strength`).
2. `EditorPage._open_processed_lens` — the F10 truth-render (adj/vadj).
3. `core.preview_render` — grid / thumbnail previews (the `Adjustment`).
4. `core.process_export_engine._render_one` — the photo + snapshot export
   bake (`look_choice["filter_strength"]`, fed by
   `mira.ui.exported.batch.recipe_for_item`).
5. `mira.ui.exported.batch._VideoOverrideShim` — folds the scale into
   `ExportPlan.filter_amount`, so `core.video_export_run` bakes the segment
   clip at the right strength with no change of its own.

Retune the look in one place (`filter_strength_scale`) and every surface
follows.

## Out of scope / notes

- The legacy journal-based Process-Culler export
  (`process_export_engine` via `get_process_look`) does not store per-photo
  strength; it defaults to 0 (medium). The live spec/89 Edit→Export path is
  Adjustment-backed and carries the real value.
