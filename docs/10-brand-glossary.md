# 10 — Brand Terminology Glossary

> **Status: Phase 1 deliverable.** Maps brand-specific camera terminology to the normalized enums used by the classifier and the wizard. The new project's wizard *speaks the user's brand's language* — a Fuji user sees "Film Simulation", a Panasonic user sees "Photo Style"; both store the equivalent normalized value internally. This document is the v1 source of truth for that mapping.
>
> v1 brands the glossary commits to: **Panasonic, Sony, Fujifilm, Canon, Nikon, Olympus/OM System, Pentax, Leica.** Plus normalized internal enums + RAW file extensions for each. Other brands (Sigma, Hasselblad, Phase One, etc.) earn glossary entries in patch releases.

## Why this glossary exists

Per Phase 0's brand-agnostic-by-construction architecture (see `architecture_exif_pattern_scenarios` memory), classification works on normalized EXIF values. But the *user-facing* terminology is brand-specific — that's how serious amateurs already know their cameras. The wizard asks "Film Simulation — Velvia, Astia, …" if the user shoots Fuji, and "Photo Style — Vivid, Standard, …" if they shoot Panasonic. Internally both store the same normalized `color_preset` field.

The flow is:
1. **At wizard time:** user identifies brand+model → wizard loads the brand-specific glossary entries → uses brand terminology in question phrasing → stores normalized values in the scenario JSON.
2. **At classification time:** the brand profile (a separate `assets/brand_profiles/{brand}.json` per `v2_design.md` §8) translates the brand-specific EXIF values to normalized values before the refinement-rules engine sees them. Classification is brand-agnostic.
3. **At reference-card render time:** the user's brand glossary is used again to translate normalized values back to brand-specific terms so the card reads naturally next to the camera.

## Normalized enums (what gets stored internally)

| Domain | Normalized values | Notes |
|---|---|---|
| `color_preset` | `standard`, `vivid`, `natural`, `portrait`, `landscape`, `monochrome`, `custom`, `unknown` | One of these per scenario; brand profiles map their native terms. |
| `focus_mode` | `single`, `continuous`, `manual`, `unknown` | "Continuous" includes brand variants like AI Servo (Canon) or AF-C (most). |
| `af_area_mode` | `single_point`, `zone`, `wide`, `tracking`, `subject_detection`, `unknown` | Sub-types collapse into these five. |
| `drive_mode` | `single`, `burst_low`, `burst_high`, `self_timer`, `interval`, `bracket`, `unknown` | "Bracket" used by the bracket detector before reaching refinement rules. |
| `white_balance` | `auto`, `daylight`, `cloudy`, `shade`, `tungsten`, `fluorescent`, `flash`, `custom`, `unknown` | |
| `image_format` | `raw`, `jpeg`, `raw+jpeg`, `heic`, `video`, `unknown` | |
| `stabilization` | `off`, `on`, `boost`, `panning`, `unknown` | "Boost" used by Panasonic, OM Boost IS; collapses with any vendor "high" mode. |
| `metering_mode` | `multi`, `center_weighted`, `spot`, `partial`, `highlight`, `unknown` | |
| `flash_mode` | `off`, `auto`, `on`, `red_eye`, `slow_sync`, `rear_curtain`, `unknown` | |

## Brand-by-brand glossary

### Panasonic (Lumix G / S / GH / LX series)

| Concept | Panasonic term | Normalized value |
|---|---|---|
| Color preset | Photo Style: Standard | `standard` |
| Color preset | Photo Style: Vivid | `vivid` |
| Color preset | Photo Style: Natural | `natural` |
| Color preset | Photo Style: Scenery | `landscape` |
| Color preset | Photo Style: Portrait | `portrait` |
| Color preset | Photo Style: Monochrome / L.Monochrome / L.Monochrome D | `monochrome` |
| Color preset | Photo Style: Cinelike D2 / V2 / Like709 / V-Log L | `custom` |
| Color preset | Photo Style: My Photo Style 1–4 | `custom` |
| Focus mode | AF-S (Single) | `single` |
| Focus mode | AF-C (Continuous) | `continuous` |
| Focus mode | MF (Manual) | `manual` |
| Focus mode | AF-F (Flexible) | `continuous` |
| AF area | 1-Area (single point) | `single_point` |
| AF area | 9-Area / Custom Multi 1-3 | `zone` |
| AF area | 49-Area / Pinpoint | `zone` |
| AF area | Tracking | `tracking` |
| AF area | Face / Eye / Body Detection | `subject_detection` |
| Drive mode | Single (●) | `single` |
| Drive mode | Burst H / SH | `burst_high` |
| Drive mode | Burst M / L | `burst_low` |
| Drive mode | 2-sec / 10-sec self-timer | `self_timer` |
| Drive mode | Time Lapse / Stop Motion | `interval` |
| Drive mode | Bracket (Exposure / Focus / Aperture / WB) | `bracket` |
| Stabilization | Body I.S. (OFF / ON / BOOST I.S.) | `off` / `on` / `boost` |
| Stabilization | Dual I.S. / Dual I.S.2 | `on` |
| Custom mode slots | C1, C2, C3-1, C3-2, C3-3 (G9 series); C1, C2, C3 (other) | informational only |
| RAW extension | `.RW2` | — |

### Sony (Alpha α / ZV series)

| Concept | Sony term | Normalized value |
|---|---|---|
| Color preset | Creative Look: ST (Standard) | `standard` |
| Color preset | Creative Look: VV / VV2 (Vivid) | `vivid` |
| Color preset | Creative Look: PT (Portrait) | `portrait` |
| Color preset | Creative Look: LD (Landscape) | `landscape` |
| Color preset | Creative Look: SH (Shadow) / IN (Instant) / FL (Film) / NT (Neutral) | `custom` |
| Color preset | Creative Look: BW (B&W) / SE (Sepia) | `monochrome` |
| Color preset | Picture Profile PP1–PP11 (S-Log2, S-Log3, HLG, etc.) | `custom` |
| Focus mode | AF-S (Single-shot AF) | `single` |
| Focus mode | AF-C (Continuous AF) | `continuous` |
| Focus mode | AF-A (Automatic AF) | `continuous` |
| Focus mode | DMF (Direct Manual Focus) | `single` |
| Focus mode | MF (Manual Focus) | `manual` |
| AF area | Spot / Center | `single_point` |
| AF area | Zone | `zone` |
| AF area | Wide | `wide` |
| AF area | Tracking (Lock-on AF) | `tracking` |
| AF area | Real-time Eye AF / Face / Animal / Bird | `subject_detection` |
| Drive mode | Single Shooting | `single` |
| Drive mode | Continuous Hi+ / Hi | `burst_high` |
| Drive mode | Continuous Mid / Lo | `burst_low` |
| Drive mode | Self-timer Single / Cont. (2 / 5 / 10 sec) | `self_timer` |
| Drive mode | Interval Shooting | `interval` |
| Drive mode | Bracketing (Continuous Bracket / Single Bracket / WB Bracket / DRO Bracket) | `bracket` |
| Stabilization | SteadyShot OFF / ON | `off` / `on` |
| Stabilization | SteadyShot Active | `boost` |
| Custom mode slots | M1, M2, M3 (memory recall) | informational only |
| RAW extension | `.ARW` | — |

### Fujifilm (X / GFX series)

| Concept | Fuji term | Normalized value |
|---|---|---|
| Color preset | Film Simulation: PROVIA / Standard | `standard` |
| Color preset | Film Simulation: Velvia / Vivid | `vivid` |
| Color preset | Film Simulation: ASTIA / Soft | `natural` |
| Color preset | Film Simulation: Classic Chrome | `custom` |
| Color preset | Film Simulation: PRO Neg. Hi / Std | `portrait` |
| Color preset | Film Simulation: Classic Neg. | `custom` |
| Color preset | Film Simulation: Nostalgic Neg. | `custom` |
| Color preset | Film Simulation: ETERNA / Cinema | `custom` |
| Color preset | Film Simulation: ETERNA Bleach Bypass | `custom` |
| Color preset | Film Simulation: ACROS / ACROS+R / +Y / +G | `monochrome` |
| Color preset | Film Simulation: Monochrome / Mono+R / +Y / +G | `monochrome` |
| Color preset | Film Simulation: Sepia | `monochrome` |
| Focus mode | AF-S | `single` |
| Focus mode | AF-C | `continuous` |
| Focus mode | MF | `manual` |
| AF area | Single Point | `single_point` |
| AF area | Zone | `zone` |
| AF area | Wide / Tracking | `tracking` |
| AF area | Face / Eye Detection | `subject_detection` |
| Drive mode | S (Single) | `single` |
| Drive mode | CH (Continuous High) | `burst_high` |
| Drive mode | CL (Continuous Low) | `burst_low` |
| Drive mode | Self Timer | `self_timer` |
| Drive mode | Interval Timer | `interval` |
| Drive mode | AE Bracketing / Film Simulation Bracketing / DR Bracketing / ISO Bracketing / Focus Bracketing | `bracket` |
| Stabilization | IBIS OFF / ON | `off` / `on` |
| Custom mode slots | C1, C2, C3, C4, C5, C6, C7 (varies by body) | informational only |
| RAW extension | `.RAF` | — |

### Canon (EOS R / DSLR)

| Concept | Canon term | Normalized value |
|---|---|---|
| Color preset | Picture Style: Standard | `standard` |
| Color preset | Picture Style: Portrait | `portrait` |
| Color preset | Picture Style: Landscape | `landscape` |
| Color preset | Picture Style: Fine Detail | `vivid` |
| Color preset | Picture Style: Neutral | `natural` |
| Color preset | Picture Style: Faithful | `natural` |
| Color preset | Picture Style: Monochrome | `monochrome` |
| Color preset | Picture Style: Auto | `standard` |
| Color preset | Picture Style: User Def. 1, 2, 3 | `custom` |
| Focus mode | One Shot AF | `single` |
| Focus mode | AI Servo AF (DSLR) / Servo AF (R-series) | `continuous` |
| Focus mode | AI Focus AF (legacy) | `continuous` |
| Focus mode | MF (Manual Focus) | `manual` |
| AF area | Single point AF / Spot AF | `single_point` |
| AF area | Zone AF | `zone` |
| AF area | Large Zone AF / Whole-area AF | `wide` |
| AF area | Tracking AF / Subject Tracking | `tracking` |
| AF area | Face+Tracking / Eye Detection AF | `subject_detection` |
| Drive mode | Single Shooting | `single` |
| Drive mode | High-speed Continuous (H+, H) | `burst_high` |
| Drive mode | Low-speed Continuous (L) | `burst_low` |
| Drive mode | Self-timer (2 / 10 sec) | `self_timer` |
| Drive mode | Interval Timer | `interval` |
| Drive mode | AEB (Auto Exposure Bracketing) / Focus Bracketing | `bracket` |
| Stabilization | IS OFF / ON (lens-based) | `off` / `on` |
| Stabilization | IBIS (R-series) | `on` |
| Custom mode slots | C1, C2, C3 (most R-series bodies) | informational only |
| RAW extension | `.CR2` (DSLR) / `.CR3` (newer R / mirrorless) | — |

### Nikon (Z / DSLR)

| Concept | Nikon term | Normalized value |
|---|---|---|
| Color preset | Picture Control: Standard (SD) | `standard` |
| Color preset | Picture Control: Neutral (NL) | `natural` |
| Color preset | Picture Control: Vivid (VI) | `vivid` |
| Color preset | Picture Control: Monochrome (MC) | `monochrome` |
| Color preset | Picture Control: Portrait (PT) | `portrait` |
| Color preset | Picture Control: Landscape (LS) | `landscape` |
| Color preset | Picture Control: Flat (FL) | `custom` |
| Color preset | Picture Control: Creative Picture Controls (Dream / Morning / Pop / Sunday / Bleached / Charcoal / etc.) | `custom` |
| Color preset | Picture Control: Custom (C1–C9) | `custom` |
| Focus mode | AF-S (Single AF) | `single` |
| Focus mode | AF-C (Continuous AF) | `continuous` |
| Focus mode | AF-F (Full-time AF — video) | `continuous` |
| Focus mode | AF-A (Auto-area AF) | `continuous` |
| Focus mode | MF (Manual Focus) | `manual` |
| AF area | Single-point AF | `single_point` |
| AF area | Dynamic-area AF (9 / 21 / 51 points) | `zone` |
| AF area | Wide-area AF (S / L) | `wide` |
| AF area | Auto-area AF | `wide` |
| AF area | 3D-tracking | `tracking` |
| AF area | Subject Detection (Eye / Face / Animal / Vehicle / Plane / Bird) | `subject_detection` |
| Drive mode | S (Single Frame) | `single` |
| Drive mode | CH (Continuous High) / CH+ | `burst_high` |
| Drive mode | CL (Continuous Low) | `burst_low` |
| Drive mode | Quiet Shutter / Self-Timer | `self_timer` |
| Drive mode | Interval Timer | `interval` |
| Drive mode | Bracketing (AE / WB / ADL) | `bracket` |
| Stabilization | VR (Vibration Reduction) OFF / ON | `off` / `on` |
| Stabilization | VR Sport mode | `panning` |
| Custom mode slots | U1, U2, U3 (Z bodies); also "Memory recall" | informational only |
| RAW extension | `.NEF` | — |

### Olympus / OM System (OM-D, PEN, OM-1)

| Concept | Olympus/OM term | Normalized value |
|---|---|---|
| Color preset | Picture Mode: Natural | `natural` |
| Color preset | Picture Mode: Vivid | `vivid` |
| Color preset | Picture Mode: Portrait | `portrait` |
| Color preset | Picture Mode: Flat | `custom` |
| Color preset | Picture Mode: Muted | `natural` |
| Color preset | Picture Mode: Monotone | `monochrome` |
| Color preset | Picture Mode: i-Enhance | `vivid` |
| Color preset | Picture Mode: Art Filter (Pop Art, Soft Focus, Pale & Light Color, etc.) | `custom` |
| Focus mode | S-AF (Single AF) | `single` |
| Focus mode | C-AF (Continuous AF) | `continuous` |
| Focus mode | C-AF+TR (Tracking) | `continuous` |
| Focus mode | MF (Manual Focus) | `manual` |
| AF area | Single Target | `single_point` |
| AF area | Group Target (5-point / 9-point / 25-point) | `zone` |
| AF area | All Target | `wide` |
| AF area | Subject Detection (Face / Eye / Bird / Animal / Train / Plane / Helicopter / Auto) | `subject_detection` |
| Drive mode | Single | `single` |
| Drive mode | Sequential H (H+) | `burst_high` |
| Drive mode | Sequential L | `burst_low` |
| Drive mode | Pro Capture H / L | `burst_high` / `burst_low` |
| Drive mode | Self-Timer | `self_timer` |
| Drive mode | Interval Shooting | `interval` |
| Drive mode | HDR / Focus Bracketing / AE Bracketing | `bracket` |
| Stabilization | IBIS OFF / ON / S-IS Auto | `off` / `on` |
| Stabilization | Sync IS (lens+body) | `boost` |
| Custom mode slots | C1, C2, C3, C4, C5 (varies) | informational only |
| RAW extension | `.ORF` (Olympus) / `.ORF` (OM System) | — |

### Pentax (K-series)

| Concept | Pentax term | Normalized value |
|---|---|---|
| Color preset | Custom Image: Auto Select | `standard` |
| Color preset | Custom Image: Bright | `vivid` |
| Color preset | Custom Image: Natural | `natural` |
| Color preset | Custom Image: Portrait | `portrait` |
| Color preset | Custom Image: Landscape | `landscape` |
| Color preset | Custom Image: Vibrant | `vivid` |
| Color preset | Custom Image: Muted | `natural` |
| Color preset | Custom Image: Reversal Film | `custom` |
| Color preset | Custom Image: Bleach Bypass | `custom` |
| Color preset | Custom Image: Monochrome | `monochrome` |
| Focus mode | AF.S (Single) | `single` |
| Focus mode | AF.C (Continuous) | `continuous` |
| Focus mode | MF (Manual) | `manual` |
| AF area | Spot | `single_point` |
| AF area | Select | `single_point` |
| AF area | Zone Select | `zone` |
| AF area | Expanded Area | `zone` |
| AF area | Auto (Tracking) | `tracking` |
| Drive mode | Single Frame | `single` |
| Drive mode | Continuous Hi | `burst_high` |
| Drive mode | Continuous Lo | `burst_low` |
| Drive mode | Self-Timer | `self_timer` |
| Drive mode | Interval Shooting / Interval Composite | `interval` |
| Drive mode | Auto Bracket (AE / WB / Saturation / Hue / Contrast / Sharpness) | `bracket` |
| Stabilization | Shake Reduction OFF / ON | `off` / `on` |
| Custom mode slots | U1, U2, U3, U4, U5 (USER modes on dial) | informational only |
| RAW extension | `.PEF` (Pentax) / `.DNG` (also supported) | — |

### Leica (M, SL, Q, CL series)

Leica is the smallest market in v1's target list and has the most variable terminology across body lines. The wizard handles Leica with conservative defaults; mappings below capture the most common terms.

| Concept | Leica term | Normalized value |
|---|---|---|
| Color preset | Film Style: Standard | `standard` |
| Color preset | Film Style: Vivid | `vivid` |
| Color preset | Film Style: Natural | `natural` |
| Color preset | Film Style: B&W Natural / B&W High Contrast | `monochrome` |
| Color preset | Film Style: Eternal | `custom` |
| Focus mode | AFs (Single AF — SL, Q) | `single` |
| Focus mode | AFc (Continuous AF — SL, Q) | `continuous` |
| Focus mode | MF (Manual — M-series default, others optional) | `manual` |
| AF area | Single Field (Q, SL) | `single_point` |
| AF area | Multi-Field | `zone` |
| AF area | Subject Tracking | `tracking` |
| AF area | Face / Eye Detection | `subject_detection` |
| Drive mode | Single | `single` |
| Drive mode | Continuous H | `burst_high` |
| Drive mode | Continuous L | `burst_low` |
| Drive mode | Self-Timer | `self_timer` |
| Drive mode | Bracketing | `bracket` |
| Stabilization | OIS (lens) / IBIS (SL2-S, SL3) OFF / ON | `off` / `on` |
| Custom mode slots | FN buttons configurable; no traditional C1/C2/C3 dial slots on most bodies | informational only |
| RAW extension | `.DNG` | — |

---

## Other terminology cross-walks

### White balance (consistent across brands)

Most brands share the white-balance preset names. v1 normalizes to: `auto / daylight / cloudy / shade / tungsten / fluorescent / flash / custom / unknown`. Brand-specific quirks:

- **Panasonic** has additional Custom WB slots (Custom 1-4) — all map to `custom`.
- **Fuji** has Underwater preset (rare) — maps to `custom`.
- **Sony** has "Color Temperature" as a separate value (K-numbers) — exposed by the brand profile but normalized as `custom`.

### Metering modes

- **Panasonic:** Multiple (matrix) / Center Weighted / Spot → `multi / center_weighted / spot`
- **Sony:** Multi / Center / Spot / Average / Highlight → `multi / center_weighted / spot / multi / highlight`
- **Fujifilm:** Multi / Spot / Average / Center Weighted → `multi / spot / multi / center_weighted`
- **Canon:** Evaluative / Partial / Spot / Center-weighted Average → `multi / partial / spot / center_weighted`
- **Nikon:** Matrix / Center-weighted / Spot / Highlight-weighted → `multi / center_weighted / spot / highlight`
- **Olympus:** ESP / Spot / Center-weighted Average → `multi / spot / center_weighted`
- **Pentax:** Multi-Segment / Center-Weighted / Spot → `multi / center_weighted / spot`
- **Leica:** Multi-field / Center-weighted / Spot → `multi / center_weighted / spot`

### Flash modes

The flash-mode names are reasonably consistent across brands. v1 normalizes to: `off / auto / on / red_eye / slow_sync / rear_curtain / unknown`. Brand-specific quirks live in the brand profile, not the user-facing glossary.

### RAW file extensions

Already in the brand tables above. Summary:

| Brand | RAW extension |
|---|---|
| Panasonic | `.RW2` |
| Sony | `.ARW` |
| Fujifilm | `.RAF` |
| Canon | `.CR2` (DSLR) / `.CR3` (mirrorless) |
| Nikon | `.NEF` |
| Olympus / OM | `.ORF` |
| Pentax | `.PEF` / `.DNG` |
| Leica | `.DNG` |

Universal: `.DNG` (Adobe Digital Negative; some bodies write DNG natively).

---

## How the glossary is consumed

### At wizard time

```
wizard.start()
  → user picks brand+model
  → wizard.load_glossary(brand)
  → wizard.ask("color_preset", brand_terms=glossary["color_preset"])
      shows: "Film Simulation — PROVIA / Velvia / Astia / Acros / …"   [for Fuji]
      shows: "Photo Style — Standard / Vivid / Scenery / Monochrome / …"   [for Panasonic]
  → user answers
  → wizard.store(scenario.color_preset = "vivid")   # normalized value
```

### At reference-card render time

```
card.render(scenario, brand)
  → glossary = load_glossary(brand)
  → display_term = glossary["color_preset"][scenario.color_preset]
      e.g., scenario.color_preset = "vivid", brand = "Fuji"
      display_term = "Velvia"
```

### At classification time

```
classify(photo)
  → brand_profile.normalize(photo.exif)
      e.g., photo.exif.PhotoStyle = "Vivid" (Panasonic)
      normalized: photo.color_preset = "vivid"
  → refinement_rules.match(photo)
      rules see normalized values; brand-agnostic
```

---

## Open questions / gaps

1. **Body-specific deviations.** Within a brand, different bodies sometimes use different terminology. The G9 MkI and G9 MkII have slightly different Photo Style options. v1 ignores these intra-brand variations; the glossary entries above use the most common term across recent bodies. Patches add body-specific overrides if real bugs surface.

2. **Localized brand terminology.** Most brands keep terminology in English in their UI worldwide (e.g., "Photo Style" stays "Photo Style" in the Brazilian Portuguese Panasonic menu) but a few brands translate. For v1 (En + Pt), the glossary entries are English regardless of locale; v1.1 (Es) revisits.

3. **Generic terminology in i18n strings.** The wizard's question text *around* the brand term is fully localized. The brand term itself (e.g., "Film Simulation") usually stays in English even in localized UI, because the camera menu the user is reading also says "Film Simulation." Confirm this approach with the v1 Pt native-speaker review.

4. **Glossary versioning.** The glossary is built-in data in `assets/brand_glossary.json`. It is read-only at runtime. If a user's camera says something the glossary doesn't know about, the wizard falls back to "Other / Custom" and stores `custom` or `unknown` normalized. Patch releases extend the glossary.

5. **Crowd-sourcing.** Phase 0's "closed-source freeware, no PRs" means the glossary cannot be community-extended via pull requests. v1.1+ may consider a "glossary submission" out-of-band channel (email, form). For v1, the author extends the glossary based on user bug reports.

---

## What this glossary deliberately does not cover

- **AF tracking sub-modes** (e.g., Sony's specific tracking algorithms). The classifier doesn't need this granularity; the wizard's question is "AF area mode" with the five normalized values.
- **Drive-mode burst speeds** (FPS numbers). The wizard's "burst low / burst high" is a relative classification, not an FPS lookup.
- **Lens stabilization vs body stabilization** distinctions. Both collapse to `on` for the classifier; the brand profile may preserve the distinction for the reference card if relevant.
- **Custom-function-button assignments.** Brand-specific and per-user; not part of the scenario model.
- **Exposure-program names** (Manual / Aperture priority / Shutter priority / Program). Standard across all brands; no brand-specific glossary needed.

---

## Cross-references

- **`docs/04-wizard-question-bank.md`** — uses this glossary to phrase brand-specific questions.
- **`docs/07-scenario-schema.md`** — the normalized values are what the schema stores in `exif_expectations`.
- **`docs/09-starter-scenarios.md`** — the built-in scenarios use normalized values in their EXIF expectations; reference-card content uses brand-neutral phrasing (e.g., "color preset" rather than "Photo Style") because the built-ins are loaded before the user has been asked their brand.
- **`v2_design.md §8 (Brand Profiles)`** — the *classification-time* EXIF normalization; this glossary is the *user-facing-terminology* mirror.
