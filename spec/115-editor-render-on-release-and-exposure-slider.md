# 115 — Editor: render on slider release + an independent Exposure slider

**Status: PROPOSED (Nelson 2026-06-22). Two Editor (`AdjustmentSurface`)
improvements: (1) fix the sluggish sliders — the tone render fires on
nearly every drag tick (`RENDER_DEBOUNCE_MS = 40`) and runs **synchronously
on the UI thread** (`render_now` over the whole frame, behind a wait
cursor), so a drag is a stream of blocking renders; render on slider
**release** instead. (2) add an **Exposure** slider beside Strength — the
render pipeline already has the knob (`core.photo_render.Params.exposure`,
EV stops), it just isn't directly user-reachable. Touches
`mira/ui/edited/adjustment_surface.py`, `mira/ui/edited/adjustment_grid.py`,
`mira/ui/pages/editor_page.py` + the `Adjustment` row (a `user_exposure`
column + migration). No render-math change for #1; #2 reuses the existing
exposure stage.**

## 1. Render on release, not on every tick (the sluggishness fix)

Today: the Strength `QSlider` and the `AdjustmentGrid` tone sliders emit
`valueChanged` continuously; the surface restarts a **40 ms** timer that
calls `render_now`, which does whole-frame tone math **on the UI thread**
(the code raises a `WaitCursor` because it "can lag visibly"). A slow drag
therefore triggers repeated blocking renders.

Fix — only render when the slider has **stopped**:

- **Live, cheap, every tick:** update the numeric value label only (no
  render).
- **Drag end:** render once on `QSlider.sliderReleased`.
- **Keyboard / field / programmatic** (no `sliderReleased` fires): a debounce
  catches them — **raise `RENDER_DEBOUNCE_MS` from 40 to ~150 ms** so it
  means "settled," not "blinked."
- State machine to avoid mid-drag renders + double-renders:
  - `sliderPressed` → `_dragging = True`.
  - `valueChanged` → update label; **if not `_dragging`**, (re)start the
    debounce (the keyboard path).
  - `sliderReleased` → `_dragging = False`; stop the debounce; `render_now`.
  - debounce timeout → `render_now`.
- Apply to **both** the direct sliders in `AdjustmentSurface` (Strength,
  new Exposure) and the `AdjustmentGrid` tone sliders. Give `AdjustmentGrid`
  a `valueCommitted(key, value)` signal (fired on `sliderReleased`, field
  `editingFinished`, and reset); the surface renders on `valueCommitted`
  and uses `valueChanged` only for the live label. `render_now` should
  early-out when the resolved params are unchanged (cheap guard against a
  redundant post-release debounce render).
- **Out of scope but noted:** moving `render_now` off the UI thread (a
  worker, like the Picker decode thread) would remove the on-release freeze
  too — a worthwhile follow-up, but release-gating alone fixes the felt
  sluggishness.

## 2. Independent Exposure slider

The tone pipeline already applies exposure as linear-light gain
(`Params.exposure`, range −4..+4 EV; today only set inside a Look and
scaled by Strength). Add a **direct, per-image** exposure control:

- A second `QSlider` **beside Strength** in the surface's strength row,
  labelled **Exposure**, range **−2..+2 EV** (e.g. slider ints −200..200,
  `value/100.0`), default 0, tick at 0, double-click → reset to 0.
- The value is a **user exposure** that is **added to** the resolved
  `Params.exposure` **after** the Look's strength scaling — so it is
  independent of the Look and NOT scaled by Strength (a clean per-image EV
  nudge on top of whatever the Look does). Apply in `_params_for_look`
  (where the surface builds the render `Params`).
- **Persist** it: add `user_exposure` to the `Adjustment` row (sibling to
  `look_strength`), with a schema migration (event.db; default `0.0`,
  clamp/normalise on read). Thread it through the same set/load paths
  `editor_page` already uses for `look_strength` (~1097 / 1291 / 1364).
- Renders **on release** per §1.

## 3. Acceptance

- Dragging Strength or any tone slider no longer stutters: the label tracks
  live, the image re-renders once when the slider is released (and after a
  ~150 ms pause for keyboard nudges). No wait-cursor storm mid-drag.
- An Exposure slider sits beside Strength; moving it brightens/darkens the
  frame by the EV amount, independent of the Look and of Strength; persists
  per item across reload/export; double-click resets to 0.
- Existing Look/Strength/tone behaviour and the developed-export pipeline
  are otherwise unchanged.

## 4. Tests

- `tests/test_adjustment_render_gating.py` — a simulated drag
  (`sliderPressed` → N× `valueChanged` → `sliderReleased`) calls
  `render_now` **once** (on release), not per tick; a keyboard step (no
  release) renders after the debounce; `AdjustmentGrid.valueCommitted`
  fires on release/field/reset, `valueChanged` does not trigger a render.
- `tests/test_user_exposure.py` — the Exposure slider adds `user_exposure`
  to `Params.exposure` independent of Strength; persists to / loads from
  the `Adjustment` row (migration); double-click resets to 0; a +1 EV
  user-exposure ≈ 2× linear gain in the rendered output.
- Regress the `AdjustmentSurface` render/params tests.
