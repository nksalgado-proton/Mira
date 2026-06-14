# spec/55 — Creative filters (the Mira set)

**Status:** SET LOCKED at nine, Nelson 2026-06-10 — Vivid, B&W,
Sepia, Faded, Golden, Cinema, Bleach, **Dramatic** (Nelson's Lumix
cloudy-sky memory: High Dynamic / Impressive Art lineage), **Crisp**
(macro + birds: texture + subject pop, per-style recipe overrides).
Recipes and names are NOT locked — they land through the established
loop: engine primitives → candidate recipes → contact sheets →
Nelson's eyeball → naming session. Inherited locks from spec/54: a
distinct Mira set, applied on top of the Look (correction →
mood → filter), one curated strength each (zero sliders),
versions-as-exports.

Resolved 2026-06-10: set size = 9 (Nelson: "keep all nine");
**vignette is in** as a subject-drawing component (Crisp) — not
nostalgia seasoning; **grain stays out of v1**; **clarity joins the
primitive list** (Dramatic's local contrast + Crisp's texture).

---

## 0. What a Mira filter is

A filter answers *"what should this photo become?"* where a Look
answers *"how should it feel?"*. Hard requirements, inherited:

1. **Distinct at grid-tile size.** If a filter isn't instantly
   recognizable on a small tile, it fails the chooser (spec/54 §3.4).
2. **Persona-1 legible.** The name + one glance = full understanding.
3. **One curated strength.** Panasonic pick-and-shoot grammar; an
   amount dial returns only as an evidence-gated per-filter exception
   (spec/54 §4.1).
4. **Photo-adaptive where it matters.** Filters ride on the A-routed
   correction, so they inherit per-photo intelligence; the filter
   itself may be a fixed transform.

## 1. Inspiration sources surveyed

- **Panasonic Creative Control** (Nelson's in-camera reference; 22
  filters): Expressive, Retro, Old Days, High Key, Low Key, Sepia,
  Monochrome, Dynamic Monochrome, Rough Monochrome, Silky Monochrome,
  Impressive Art, High Dynamic, Cross Process, Toy Effect, Toy Pop,
  Bleach Bypass, Miniature Effect, Soft Focus, Fantasy, Star Filter,
  One Point Color, Sunshine.
- **Fujifilm Film Simulations** (the gold standard of in-camera
  looks): Provia (neutral), Velvia (vivid landscape), Astia (soft),
  Classic Chrome (muted documentary), Classic Neg, Nostalgic Neg,
  Eterna (flat cinematic), Acros (tonal B&W), plain Mono, Sepia.
- **Olympus/OM Art Filters:** Pop Art, Grainy Film, Pin Hole
  (vignette), Diorama, Dramatic Tone, Cross Process, Pale & Light,
  Light Tone, Vintage, Bleach Bypass.
- **Film stocks / analog lineage:** Kodak Portra (warm, gentle skin),
  Velvia 50 (saturated), Tri-X (gritty B&W), Cinestill 800T
  (tungsten, teal-orange, halation), faded-print look (matte blacks).
- **Cinema grades:** teal–orange blockbuster, bleach bypass, day-for-
  night cool.
- **App-world:** VSCO fade presets, Instagram classics — proof that
  matte/faded + warm-golden are the most *chosen* casual looks.

## 2. Anatomy — filter families in engine vocabulary

What each family actually does, in terms of primitives:

| Family | Anatomy | Engine status |
|---|---|---|
| Vivid / pop | saturation+, vibrance+, contrast+ | **expressible today** (`Params`) |
| Bleach bypass | saturation−−, contrast+, slight exposure− | **expressible today** |
| B&W (plain → dramatic) | channel-mixed desaturation (red-filter sky darkening), tone curve | needs **`bw_mix`** primitive |
| Sepia / toned mono | B&W + warm (or selenium-cool) tone map | needs **`tint`** after bw |
| Faded / matte (Retro, Classic Chrome) | lifted black point ("fade"), saturation−, slight warm or cool cast | needs **`fade`** + **`tint`** |
| Golden / Sunshine | warm white-balance shift, soft contrast, highlight glow | needs **`tint`** (glow optional later) |
| Cinematic (Eterna / teal–orange) | split-tone (cool shadows, warm highlights), saturation−, flat curve | needs **`split_tone`** |
| Cross process | hue rotation + cast + punched contrast | tint + curve approximations |
| Vignette looks (Pin Hole, Toy) | radial darkening | needs **`vignette`** (spatial, cheap) |
| Grain looks (Grainy Film, Tri-X) | luminance noise | needs **`grain`** (spatial, cheap) |
| Optics family (Soft Focus, Miniature, Star, halation) | blur fields, tilt-shift bands, starbursts | **heavy** — out of first set |
| One Point Color | selective desaturation by hue | medium; out of first set |

**Primitive shopping list for the first set** (all pointwise or
trivially spatial, all cheap in the existing float pipeline of
`core/photo_render.py`): `bw_mix` (channel-weight desaturation),
`tint` (RGB gain shift, may be split shadow/highlight = `split_tone`),
`fade` (output black-point lift), `vignette`, `grain`. Filter recipes
become a new `Filter` stage applied after the tone LUT.

## 3. Feasibility tiers

- **Tier A — today:** Vivid, Bleach. (`Params` alone.)
- **Tier B — pointwise color primitives:** B&W, Sepia, Faded, Golden,
  Cinematic, Cross-process. One small engine extension unlocks six
  distinct identities.
- **Tier C — cheap spatial:** vignette + grain as *components* of the
  above (Toy, Grainy Film flavors).
- **Tier D — optics (NOT first set):** Soft Focus, Miniature, Star,
  One Point Color, halation.

## 4. Proposed starter shortlist (for Nelson's reaction — NOT locked)

Seven candidates, every one grid-distinct from the others and from
the three Looks; Vivid + Faded deliberately absorb the retired
Vibrance slider's two directions (spec/54 §4.1):

| Working key | Vibe in one phrase | Lineage | Tier |
|---|---|---|---|
| `vivid` | colors turned up, postcard pop | Expressive / Velvia | A |
| `bw` | classic punchy black & white | Dynamic Monochrome / Acros | B |
| `sepia` | warm old-photograph mono | Sepia (Panasonic) | B |
| `faded` | matte, quiet, old-print calm | Retro / Classic Chrome / VSCO | B |
| `golden` | late-afternoon warmth | Sunshine / Portra | B |
| `cinema` | teal-shadow movie grade | Eterna / teal–orange | B |
| `bleach` | gritty desaturated punch | Bleach Bypass | A |
| `dramatic` | cloudy skies turned epic | High Dynamic / Impressive Art / Dramatic Tone | B+clarity |
| `crisp` | texture + subject pop (macro: specimen-dark bg; birds: warm feather detail — per-style recipe overrides, the `_TUNING_BY_STYLE` pattern) | Nelson's macro/birds ask 2026-06-10 | B+clarity+vignette |

High Key / Low Key are deliberately **excluded**: they collide with
the Brighter/Deeper mood axis (one chooser must not fight the other).

## 5. Open questions (the design conversation)

1. Which of these directions speak to Nelson — and which Panasonic
   filters does he actually reach for in-camera? (Best signal there is.)
2. Set size: all seven? Five? The filter chooser is a dropdown (the
   hidden spec/54 slot), so the grid-tile pressure is softer than for
   Looks — but curation beats abundance.
3. Should `grain` / `vignette` season any first-set member (e.g.
   faded with grain), or stay pure-color for v1?
4. Naming + pt-BR comes last, after the contact sheets (locked habit).

## 6. The build loop — COMPLETE except naming (2026-06-10)

1. ✅ Engine primitives — `core/photo_render.py` `FilterRecipe` +
   `apply_filter` (bw_mix, tint, split-tone, fade, clarity, vignette).
2. ✅ Recipes v0 in `tools/calibrate_looks.py` (`FILTERS` +
   `FILTER_STYLE_OVERRIDES`) — **Nelson-approved on the contact
   sheets, first eyeball, 2026-06-10 ("I love it").**
3. ✅ `filters` contact-sheet subcommand + filters.html viewer.
4. ✅ `export` ships `FILTER_RECIPES` into `core/photo_looks_data.py`;
   `core/photo_auto.available_filters` / `resolve_filter_recipe`
   (style-aware); the Edit surface's Filter chooser is LIVE (preview
   renders the filter; choice persists to `creative_filter`); photo
   export applies it (correction → mood → filter → crop); video
   export applies it per frame (`ExportPlan.filter_recipe`).
   Note: clarity-bearing filters (Dramatic, Crisp) cost a per-frame
   Gaussian on video export — acceptable async, optimize if felt.
5. ✅ **Naming — EN locked, pt-BR deferred** (Nelson 2026-06-10:
   "Only English" for now). Display names = Vivid, B&W, Sepia, Faded,
   Golden, Cinema, Bleach, Dramatic, Crisp, mapped in
   `mira/ui/edited/look_grid.py::filter_display_name`.
   Portuguese rides the future i18n pass (the Looks' pt-BR from
   spec/54 §2 stays locked).
