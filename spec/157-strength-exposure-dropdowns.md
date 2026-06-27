# 157 — Strength + Exposure as −5..+5 dropdowns

**Status: DONE (Nelson 2026-06-27).** Replaces the Edit surface's two
continuous tone sliders (Look **Strength** and per-image **Exposure**)
with two **side-by-side −5..+5 graduation dropdowns** (0 = default),
matching the spec/156 filter-strength dropdown idiom but at higher
resolution (11 steps).

## Why

The sliders allowed any continuous value by dragging; the user preferred
discrete dropdowns (consistent with the filter control) with a clear
0/default and finer graduation than the filter's ±2.

## The controls

Both live in the Look group, laid out side by side (caption above each
combo). Eleven steps, the middle one is 0 / default:

| | −5 | 0 (default) | +5 |
|---|---|---|---|
| **Strength** (`_look_strength`, 0..2) | 0.0 (identity) | **1.0** (Look as authored) | 2.0 (2×) |
| **Exposure** (`_user_exposure`, EV) | −2 EV | **0** (no nudge) | +2 EV |

Step → value is linear: strength `1.0 + 0.2·step`, exposure `0.4·step`.
The item's combo DATA is the underlying continuous value; the label is
the signed step (`-5` … `0` … `+5`).

## What did NOT change

The persisted columns (`adjustment.look_strength` 0..2,
`adjustment.user_exposure` ±2 EV, + the video twins), their clamps, and
every render/export path are **unchanged** — only the control. So no
schema migration. The ranges are exactly the slider ranges, just sampled
at 11 discrete steps; a legacy slider value that sits between steps loads
fine (the surface keeps the exact value for rendering and snaps only the
combo's DISPLAY to the nearest step).

## Behaviour

- A combo pick is a settled change → render + persist immediately
  (`changed("tone")`), no slider drag/debounce machinery.
- Strength greys out on the Original look (inert there), as before;
  Exposure stays enabled on any photo.
- Reset-all returns both to 0/default (strength 1.0, exposure 0 EV).

## Notes

- The slider-era render-on-release / debounce state machine
  (`_render_timer`, `_on_drag_*`) is now unused by these controls; left
  inert in `AdjustmentSurface` as a low-priority cleanup candidate.
- Combos reuse the Process-combo QSS roles (`LookStrengthCombo` /
  `UserExposureCombo`) for look + dense-tier parity.
