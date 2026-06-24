# 116 — New creative filters: Subject Spotlight (AF-anchored), Dehaze, Glow, Grain, De-glare

**Status: PROPOSED (Nelson 2026-06-22; De-glare added 2026-06-23). Adds five
new components to the Editor's creative-filter engine
(`core/photo_render.FilterRecipe` + `apply_filter`), each
identity-by-default so existing filters are unchanged: a **Subject
Spotlight** (a radial "pop" mask **anchored at the photo's AF point** — Mira
already extracts it via `core.brand_profile.AfPoint`), **Dehaze**, **Glow**
(Orton bloom), **Grain**, and **De-glare** (softens flash-glare specular
hotspots on the subject). New named recipes in
`core/photo_looks_data.FILTER_RECIPES` surface them in the Editor's filter
picker. The Spotlight is the headline — it answers "make the subject pop,"
which a uniform filter can't, by using *where the camera focused* as the
subject anchor (no AI). Touches `core/photo_render.py`,
`core/photo_looks_data.py`, and the render call sites (Editor / preview /
export) to thread the AF centre. Pure numpy/scipy, no Qt.**

## 1. Engine: four new `FilterRecipe` components (identity by default)

Add to `FilterRecipe` (all default 0.0 → `FilterRecipe()` stays identity;
`is_identity` + `from_dict` key-set updated):

- **`spotlight: float`** (0..1 strength) + `spotlight_radius: float`
  (0..1, default ~0.6). A radial subject-pop.
- **`dehaze: float`** (-1..1). Haze removal / add.
- **`glow: float`** (0..1). Orton-style dreamy bloom.
- **`grain: float`** (0..1). Film grain.
- **`deglare: float`** (0..1 strength) + `deglare_subject_only: bool`
  (default `True`). Softens flash-glare specular hotspots.

`apply_filter(img, recipe, amount=1.0, *, center=(0.5, 0.5))` — new
`center` kwarg (the AF anchor for the spotlight; §2). All four scale by
`amount` like the existing components (half a filter = half the effect).

**Pipeline order** (extends today's params → bw_mix → tint → split-tone →
clarity → vignette):
`params → bw_mix → tint → split_tone → dehaze → deglare → fade → clarity →
glow → spotlight → vignette → grain`. Tonal/repair first (de-glare runs
early, before clarity/glow re-touch highlights); spatial (spotlight,
vignette) then; texture (grain) last.

**Math (v1 sketches — numpy/scipy, matching the existing stages):**

- **Subject Spotlight** — build a radial mask `M` centred at `center`,
  inner radius from `spotlight_radius`, smooth (squared/cosine) falloff.
  - *Inside* (`M` high): local-contrast boost (the existing `clarity`
    primitive) + a small exposure lift.
  - *Outside* (`1-M`): darken (`× (1 - k·bg_darken·(1-M))`) and desaturate
    (lerp toward luminance by `k·bg_desat·(1-M)`), where `k = spotlight`.
  - Tuned `bg_darken`/`bg_desat`/inner-boost are baked constants scaled by
    `spotlight`; only **strength** + **radius** are exposed.
- **Dehaze** — pull the black point + add local contrast (unsharp on
  luminance) + saturation, weighted toward low-contrast (hazy) regions;
  negative `dehaze` adds atmosphere. (Honest: an approximation, not a
  physical dark-channel model — fine for a creative filter.)
- **Glow** — `G = gaussian_blur(brighten(img))`; screen-blend `img` with
  `G` at `glow`. Dreamy bloom that protects highlights and adds subject
  separation.
- **Grain** — monochrome gaussian noise, luminance-masked (strongest in
  mid-tones), added at `grain`.
- **De-glare** — softens flash-glare specular hotspots. Build a soft
  **glare mask** from the specular signature: pixels that are both **high
  luminance** (top ~5-10%, V/L threshold) **and low saturation**
  (near-white) — diffuse bright skin keeps chroma, a blown hotspot does
  not. Gaussian-smooth the mask (no hard edge). Inside it, scaled by
  `deglare`: (a) **pull luminance down** (compress the hotspot), (b)
  **re-inject chroma** sampled from the surrounding non-glare neighbourhood
  (a dilated ring) so the white patch returns toward local skin tone, (c)
  optionally borrow low-frequency texture from the neighbourhood to break
  up the flat blob. **Honest limit:** fully clipped (255) regions carry no
  recoverable detail — De-glare *softens* harshness and restores tone, it
  does not reconstruct erased texture; mild/moderate glare responds best.
  When `deglare_subject_only` (default), multiply the glare mask by the
  subject mask (§2's radial mask at `center`/`spotlight_radius`) so only the
  subject's glare is touched — intentional highlights (eye catchlights,
  bright sky, rim light) are left alone. `False` applies frame-wide.

## 2. AF-point anchoring (the Spotlight's subject awareness)

- The Spotlight `center` defaults to the photo's **AF point**
  (`brand_profile` → `AfPoint(cx, cy)`), falling back to frame centre
  `(0.5, 0.5)` when the camera recorded none.
- The render **call sites** pass it: the Editor (`AdjustmentSurface.render_now`
  already has the item's EXIF/AF for the F10 lens), the preview renderer,
  and the export engines (`core/preview_render.py`,
  `core/process_export_engine.py`, `core/video_export_run.py`). Compute the
  AfPoint from the item's EXIF via `brand_profile` (or read a stored one),
  pass `center=` into `apply_filter`. Default centre when absent — never an
  error.
- (Future nicety, not v1: let the user drag the Spotlight centre to
  override the AF anchor.)

## 3. Named filters (surface them in the Editor)

Add to `FILTER_RECIPES` so they appear in the Editor's filter picker:
**Subject Pop** (`spotlight ≈ 0.6`), **Dehaze** (`dehaze ≈ 0.5`),
**Dreamy Glow** (`glow ≈ 0.5` + slight `fade`), **Film Grain**
(`grain ≈ 0.5` + small contrast), **De-glare** (`deglare ≈ 0.5`,
`deglare_subject_only = True`). Names via `tr()`. They combine with the
existing Look/Strength like any filter.

## 4. Future (separate spec) — true subject matting

The Spotlight is AF-anchored, not a real subject mask. A later phase could
add **AI subject/person segmentation** to build an accurate matte (apply
pop to the subject, blur/darken the true background), leaning on the
face detection from spec/91. Flagged here as the Phase-2 evolution; out of
scope for 116.

## 5. Acceptance

- `FilterRecipe()` is still identity; all existing filters render
  byte-identically (the four new fields default off).
- A Subject Spotlight visibly pops the subject: local contrast + slight
  lift around the **AF point**, background gently darkened + desaturated;
  with no AF point it centres on the frame.
- Dehaze recovers contrast/colour in a hazy frame; Glow adds a highlight
  bloom; Grain adds luminance-masked texture — each scaling with `amount`.
- De-glare lowers and re-tones a bright, desaturated specular hotspot on the
  subject (skin shine / flash glare); with `deglare_subject_only` it leaves
  highlights outside the subject region untouched; frame-wide when `False`.
- The four named filters appear in the Editor filter picker and combine
  with Look/Strength.
- Full-res export and the preview render produce the same effect at scale.

## 6. Tests

- `tests/test_filter_components.py` — identity preserved with new fields
  off; each component changes pixels in the expected direction (spotlight
  brightens/contrasts near `center` and mutes the corners; dehaze raises
  contrast/saturation; glow raises highlight bloom; grain raises local
  variance; de-glare lowers luminance + raises saturation inside a
  synthetic bright-desaturated hotspot, and `deglare_subject_only=True`
  leaves an identical hotspot in the corner untouched); `from_dict`/
  `is_identity` updated; `amount=0.5` ≈ half effect.
- `tests/test_spotlight_center.py` — `center=(0.2,0.2)` pops the top-left,
  `center=(0.8,0.8)` the bottom-right; default centres the frame; AfPoint
  → center plumbing resolves through the Editor render path.
- Regress the existing `apply_filter` / `FILTER_RECIPES` tests.

## 7. Implementation plan (commit order)

1. **Engine primitives** — the five `FilterRecipe` fields (spotlight,
   dehaze, glow, grain, deglare + the two sub-params) + the five pixel
   stages in `apply_filter` + the `center` kwarg (`core/photo_render.py`).
   Pure numpy/scipy; unit-tested in isolation (`test_filter_components`,
   `test_spotlight_center`). No call-site changes yet (default centre).
2. **AF-point plumbing** — thread `center` from the item's `AfPoint` through
   the Editor / preview / export render call sites; default `(0.5,0.5)`.
   (De-glare's subject weighting reuses the same `center`.)
3. **Named filters** — add Subject Pop / Dehaze / Dreamy Glow / Film Grain /
   De-glare to `FILTER_RECIPES`; they appear in the Editor picker.
4. *(Future, separate spec)* AI subject-matte Phase 2 (§4) — would also
   sharpen De-glare's subject masking beyond the AF radial.
