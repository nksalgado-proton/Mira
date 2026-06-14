# 04 — First-Run Wizard Question Bank

> **Status: Phase 1 draft.** The wizard is the centerpiece of v1: the mechanism by which user knowledge becomes machine rules. This document specifies, genre by genre, the EXIF-grounded questions the wizard asks. Four genres (Wildlife, Macro, Landscape, Portrait) are drafted in detail; six more are stubbed. Phrasing is targeted — these go directly into the UI in v1's two locales (En + Pt) once stabilized.

> **Frozen requirement — 2026-05-17 (Nelson): "Preferred burst genre" question.** The wizard must ask the user their preferred action genre for **bursts** (e.g. Wildlife / Sports / Motorsport / Aviation / …). It writes a single value, **`preferred_burst_genre`** (settings-backed). It is the **bucket-level style tie-breaker** for Burst buckets when EXIF is ambiguous (per the frozen tie-breaker table in **docs/18 §"Bucket cull surfaces — base/derived consolidation"**). Until the wizard is built, a settings default of **Wildlife** stands in; when the wizard ships it simply writes the same key — zero rework downstream.

## Why this document matters

Per Phase 0, the wizard converts a user's *shooting habits* into a *machine-readable classification ruleset*. The output of the wizard is the user's personal **scenario library** — a set of EXIF-pattern profiles plus reference-card content. Every later journey reads from this library:

- J3 (scenario editing) — surface and refine these.
- J4 (event prep) — pick which scenarios apply to this trip.
- J5 (daily cull) — auto-classify imported photos using these patterns.
- J6 (curate) — group by scenario for narrative selection.
- J8 (reference card export) — print/install these per scenario.

Time invested in this question bank pays off across every load-bearing journey.

---

## Guiding principles

### 1. EXIF-grounded only

Every question must map to an EXIF field (or a small set) the camera writes for each photo. The point of asking is to teach the classifier what *pattern* of EXIF distinguishes one genre from another in this user's hands.

Forbidden question types:
- "What's your favorite subject?" (not EXIF — belongs in genre selection, not the EXIF questionnaire).
- "How long have you been shooting macro?" (not EXIF).
- "Do you like flat or contrasty rendering?" (not directly EXIF unless mapped through Photo Style).
- Subjective preferences not grounded in a camera setting.

Permitted question types:
- "AF mode for wildlife — AF-S, AF-C, or manual?" → EXIF `FocusMode`.
- "Aperture range for macro — wide open / mid / stopped down / mixed?" → EXIF `FNumber` distribution.
- "Photo Style for landscapes — your camera's Scenery / Vivid / Standard / custom?" → EXIF `PhotoStyle` (or brand equivalent).
- "Drive mode for sports — single, burst-low, burst-high?" → EXIF `DriveMode` / `ContinuousDrive`.
- "Do you use a tripod for landscapes — always / sometimes / never?" → indirectly inferable from shutter-speed distribution + ISO; useful for the reference card even if classifier is uncertain.

### 2. Multiple choice, "I don't know" always available

No free-text in v1. Every question has 3–5 short options plus an "I'm not sure / skip" option. Missing answers produce broader scenario rules — less precise classification, still functional.

**Pre-filled defaults (2026-05-13 refinement).** Each block opens with the *most common/expected* answer per question already selected — so the user sees Mira's reference setup for the genre at a glance and only has to change what doesn't match. Skip remains a clickable option that overrides the default to "no constraint, broader scenario." Rationale: users who accept defaults still produce a classifiable scenario (instead of a hollow one from clicking through), and the wizard becomes educational — showing the prototype's calibration baseline alongside the question. The per-question defaults live in `ui/wizard/step_<genre>_block.py` as `DEFAULT_<GENRE>_ANSWERS`.

### 3. Brand-neutral phrasing, brand-detected glossary

The wizard asks brand+model early (step 3 of the overall flow). From that point, brand-specific terminology is localized:

| Concept | Panasonic | Sony | Fujifilm | Canon | Nikon | Olympus/OM | Pentax | Leica |
|---|---|---|---|---|---|---|---|---|
| Color preset | Photo Style | Creative Look | Film Simulation | Picture Style | Picture Control | Picture Mode | Custom Image | Film Style |
| Continuous AF | AF-C | Continuous AF (AF-C) | AF-C | AI Servo | AF-C | C-AF | AF.C | AFc |
| Single AF | AF-S | Single-shot AF (AF-S) | AF-S | One Shot | AF-S | S-AF | AF.S | AFs |
| Custom mode slot | C1, C2, C3-x | M1, M2, M3 | C1–C7 | C1, C2, C3 | U1, U2, U3 | C1–C5 | U1–U5 | (none / FN) |
| Continuous shoot | Burst (H/M/L) | Cont. Hi/Lo | CH/CL | High-Speed Continuous | CH/CL | Sequential H/L | Hi/Lo | Continuous H/L |

The wizard uses the user's brand-specific term in the question and stores a brand-neutral identifier in the scenario JSON. Example: a Fuji user sees "Film Simulation — Velvia / Astia / Provia / Eterna / Classic Chrome / Acros / other?"; a Panasonic user sees "Photo Style — Vivid / Standard / Scenery / Portrait / Monochrome / custom?". Both store the equivalent normalized `color_preset` field in their scenario.

For brands the author cannot QA against, the glossary table above is the v1 commitment. Brand-specific terminology that proves wrong gets corrected in patch releases.

### 4. Range questions, not point questions

Real shooting habits are distributions, not exact values. The wizard asks for *ranges* (and lets the user pick "mixed" / "varies") rather than exact values.

- "Aperture range" → wide-open (f/1.4–f/2.8), moderate (f/2.8–f/5.6), stopped (f/5.6–f/11), small (f/11–f/22), mixed.
- "ISO range" → low only (100–400), moderate (400–1600), high tolerance (1600–6400), auto-everything, mixed.
- "Focal length" → wide (under 35mm equiv), normal (35–85), short tele (85–200), long tele (200–600+), mixed, single prime.

### 5. Short, skippable, re-enterable

A genre's questionnaire targets ≤ 10 questions, ≤ 3 minutes for a fluent answer. Total wizard time for 5 selected genres: ≤ 20 minutes. Re-entry from Settings to *add a single genre* or *refine one genre* is always available.

### 6. Two outputs per question

Every answer feeds two artifacts:
- **EXIF expectations** (machine-readable ruleset for the classifier).
- **Reference-card content** (human-readable card content for J8).

Sometimes both come from the same answer; sometimes a question only contributes to one. The question bank below labels each.

---

## Genre catalog for v1

v1 wizard supports these 10 genres. Multi-select on the genre picker; user can re-enter from Settings to add more later.

1. **Wildlife** (birds, mammals, BIF)
2. **Macro** (insects, water drops, products, focus stacking)
3. **Landscape** (scenic, golden hour, long exposure)
4. **Portrait** (people, headshots, environmental)
5. **Street / Documentary**
6. **Sports / Action** (organized sport, kids action)
7. **Travel / General**
8. **Astro / Night** (Milky Way, moon, urban night)
9. **Family / Events** (gatherings, birthdays, weddings as a guest)
10. **Video** (where the user explicitly shoots video as a recurring practice)

Four are drafted in detail below. Six are stubbed and will be filled in over the next Phase 1 iterations.

---

## Wildlife (detailed)

**Goal of this scenario in classification:** photos where the user was after birds / mammals, typically with a longer lens, continuous AF, often higher ISO, frequently burst mode. EXIF signature is usually distinct from any other genre on the same camera.

**Questions:**

1. **Lenses used.** Multi-select from the user's lens list.
   - *Feeds:* EXIF `LensModel` matchers (any-of).
   - *Card:* lens choice listed in "physical setup."

2. **Focal length range you actually shoot at.** Wide / Normal / Short tele (85–200mm) / Long tele (200–400mm) / Very long (400mm+) / Mixed.
   - *Feeds:* EXIF `FocalLengthIn35mmFormat` range matcher.
   - *Card:* focal-length guidance under "field adjustments."

3. **AF mode you use most.** Continuous AF (AF-C / AI Servo / equivalent) / Single AF / Manual / Mixed.
   - *Feeds:* EXIF `FocusMode` matcher.
   - *Card:* AF mode on the card.

4. **AF area mode you favor.** Single-point / Zone / Wide / Tracking / Subject detection / Mixed.
   - *Feeds:* EXIF `AFAreaMode` (brand-quirky — store as normalized enum) matcher.
   - *Card:* AF area on the card.

5. **Aperture range.** Wide open (f/4–f/5.6) / Moderate (f/5.6–f/8) / Stopped (f/8–f/11) / Mixed.
   - *Feeds:* EXIF `FNumber` range matcher.
   - *Card:* aperture guidance on the card.

6. **ISO range you tolerate.** Low only (≤ 400) / Moderate (400–1600) / High (1600–6400) / Very high (6400+) / Auto / Mixed.
   - *Feeds:* EXIF `ISO` range matcher.
   - *Card:* ISO guidance.

7. **Shutter speed you target.** Very fast (1/2000+) for BIF / Fast (1/500–1/2000) / Moderate (1/250–1/500) / Slow with stabilization (1/60–1/250) / Mixed.
   - *Feeds:* EXIF `ExposureTime` range matcher.
   - *Card:* shutter guidance.

8. **Drive mode.** Single shot / Burst low / Burst high / Mixed.
   - *Feeds:* EXIF `DriveMode` / `ContinuousDrive` (normalized).
   - *Card:* drive mode on the card.

9. **Color preset (brand-localized term).** "Standard" / "Vivid" / "Natural-rendering" / custom / unsure.
   - *Feeds:* EXIF `PhotoStyle` / `PictureControl` / etc. matcher.
   - *Card:* color preset on the card.

10. **Stabilization usage.** IBIS on / OIS on / both on / off when on tripod / always on / unsure.
    - *Feeds:* not directly EXIF in most brands; informational.
    - *Card:* stabilization note on the card.

**Optional / depth questions** (shown if the user opted into a "deeper questions" expansion):

- Teleconverter usage?
- Crop modes used in-camera?
- Exposure compensation defaults?
- Image format (RAW / JPEG / RAW+JPEG)?

**Default if all questions skipped:** broad scenario matching any photo with focal length ≥ 200mm equiv + AF-C. Classifies a meaningful subset accurately; over-claims will be visible during cull and can be reclassified by hand.

---

## Macro (detailed)

**Goal of this scenario in classification:** photos where the user was after small subjects at close distance, typically with a macro lens, often manual focus, often with flash, often single-shot or focus-bracket sequences. Distinct shutter / aperture / focal-length signature.

**Questions:**

1. **Macro lens(es) used.** Multi-select from the user's lens list.
   - *Feeds:* `LensModel` matcher.
   - *Card:* lens choice on the card.

2. **Focusing approach.** Always manual / mostly manual with AF assist / autofocus with focus tweak / mixed.
   - *Feeds:* `FocusMode` matcher (manual-bias).
   - *Card:* focus mode + technique notes.

3. **Aperture range.** Wide for shallow DOF (f/2.8–f/4) / Moderate (f/4–f/8) / Stopped for DOF (f/8–f/16) / Very small with diffraction risk (f/16+) / Mixed depending on subject.
   - *Feeds:* `FNumber` range matcher.
   - *Card:* aperture guidance.

4. **Focus bracketing / stacking usage.** Always tripod + bracketed / sometimes / never / unsure what this means.
   - *Feeds:* set a flag on the scenario for the bracket detector (`expects_focus_brackets: true/false`); influences J5 bracket grouping confidence.
   - *Card:* technique note.

5. **Working magnification.** Below 0.5x / 0.5x–1x / 1x and above / mixed.
   - *Feeds:* lens-specific minimum-focus-distance metadata + focal length helps infer; not directly EXIF.
   - *Card:* magnification guidance.

6. **Flash usage.** No flash / on-camera flash / off-camera flash with trigger / ring or macro flash / mixed.
   - *Feeds:* EXIF `Flash` (fired / mode) matcher.
   - *Card:* flash setup detail.

7. **Tripod usage.** Always tripod / handheld with stabilization / mixed depending on subject.
   - *Feeds:* informational (shutter / ISO distribution gives weak signal).
   - *Card:* tripod note.

8. **Shutter speed.** Fast to freeze movement (1/250+) / Sync speed with flash (1/100–1/250) / Slow on tripod (1/30–1/100) / Very slow on tripod (longer than 1/30) / Mixed.
   - *Feeds:* `ExposureTime` range matcher.
   - *Card:* shutter guidance.

9. **ISO range.** Low only (≤ 400) / Moderate (400–1600) / High when handheld (1600+) / Auto / Mixed.
   - *Feeds:* `ISO` range matcher.
   - *Card:* ISO guidance.

10. **Color preset (brand-localized term).** "Standard" / "Natural" / "Vivid" / custom / unsure.
    - *Feeds:* color-preset matcher.
    - *Card:* color preset on card.

**Optional / depth questions:**

- Diopter or extension tube usage?
- Live composite or live ND techniques?
- Image format (RAW / JPEG / RAW+JPEG)?

**Default if all questions skipped:** broad scenario matching any photo with a known macro lens. Classification accuracy bounded by lens-as-signal alone — sufficient if the user owns one macro lens and uses it only for macro.

---

## Landscape (detailed)

**Goal of this scenario in classification:** photos where the user was after scenic compositions, typically wide-to-normal focal length, smaller apertures for DOF, often on a tripod, often at base ISO, sometimes long exposure with ND filter.

**Questions:**

1. **Lenses used.** Multi-select.
   - *Feeds:* `LensModel` matcher.
   - *Card:* lens choice.

2. **Focal-length range.** Ultra-wide (< 24mm equiv) / Wide (24–35mm) / Normal (35–70mm) / Short tele (70–135mm) / Mixed.
   - *Feeds:* `FocalLengthIn35mmFormat` range matcher.
   - *Card:* focal-length guidance.

3. **Aperture range.** Wider for separation (f/2.8–f/5.6) / Standard for DOF (f/5.6–f/11) / Stopped for max DOF (f/11–f/16) / Mixed.
   - *Feeds:* `FNumber` range matcher.
   - *Card:* aperture guidance.

4. **Tripod usage.** Always tripod / Tripod for low light only / Mostly handheld / Mixed.
   - *Feeds:* informational; shutter-speed distribution helps verify.
   - *Card:* tripod note.

5. **ND or graduated ND filter usage.** Often / Sometimes / Never / Unsure.
   - *Feeds:* not directly EXIF; informational + shutter-speed signal.
   - *Card:* filter note.

6. **Long exposure habits.** Frequent (more than 1 second exposures) / Occasional / Never / Unsure.
   - *Feeds:* `ExposureTime` range matcher (long-tail).
   - *Card:* long-exposure note.

7. **Shutter range.** Fast for handheld (1/125+) / Moderate (1/30–1/125) / Slow on tripod (1/30 to 1 sec) / Long exposure (1 sec to 30 sec) / Very long (30 sec to bulb) / Mixed.
   - *Feeds:* `ExposureTime` range matcher.
   - *Card:* shutter guidance.

8. **ISO range.** Base ISO only / Low (100–400) / Auto with low cap / Mixed.
   - *Feeds:* `ISO` range matcher.
   - *Card:* ISO guidance.

9. **AF approach.** Single-point AF / Hyperfocal / Manual focus / Mixed.
   - *Feeds:* `FocusMode` matcher.
   - *Card:* focus technique.

10. **Color preset (brand-localized term).** "Scenery" / "Landscape" / "Vivid" / "Standard" / custom / unsure.
    - *Feeds:* color-preset matcher.
    - *Card:* color preset on card.

**Optional / depth questions:**

- Exposure bracketing for HDR? (Feeds the bracket detector.)
- Focus stacking for ultra-deep DOF? (Feeds the bracket detector.)
- Panorama shooting habits?
- Polarizer usage?

**Default if all questions skipped:** broad scenario matching wide-angle focal length + small aperture + low ISO. Misclassification likely if the user shoots landscapes handheld at large apertures (rare but possible) — fixable in J5 by manual reclassification.

---

## Portrait (detailed)

**Goal of this scenario in classification:** photos where the user was after people, typically with short telephoto or fast normal primes, wider apertures for separation, AF-S on the subject's eye, often a "Portrait" color preset or a custom one tuned for skin tones.

**Questions:**

1. **Lenses used.** Multi-select.
   - *Feeds:* `LensModel` matcher.
   - *Card:* lens choice.

2. **Focal-length range.** Wide environmental (24–35mm) / Normal (35–70mm) / Short tele (70–135mm) / Longer tele (135–200mm) / Mixed.
   - *Feeds:* `FocalLengthIn35mmFormat` range matcher.
   - *Card:* focal-length guidance.

3. **Aperture range.** Very wide for separation (f/1.4–f/2.8) / Moderate (f/2.8–f/5.6) / Stopped for group shots (f/5.6–f/11) / Mixed.
   - *Feeds:* `FNumber` range matcher.
   - *Card:* aperture guidance.

4. **AF approach.** Eye AF / Face detection / Single-point AF / Manual / Mixed.
   - *Feeds:* `FocusMode` + `AFAreaMode` matchers (brand-quirky — store normalized).
   - *Card:* AF technique.

5. **ISO range.** Low only (≤ 400) / Moderate (400–1600) / High for indoor/event (1600+) / Auto / Mixed.
   - *Feeds:* `ISO` range matcher.
   - *Card:* ISO guidance.

6. **Lighting context.** Natural light / Continuous artificial / Studio strobe / Speedlight / Mixed.
   - *Feeds:* `Flash` matcher when applicable.
   - *Card:* lighting setup.

7. **Color preset (brand-localized term).** "Portrait" / "Standard" / "Natural" / custom skin-tone preset / unsure.
   - *Feeds:* color-preset matcher.
   - *Card:* color preset.

8. **Drive mode.** Single shot / Burst (low) for expressions / Mixed.
   - *Feeds:* `DriveMode` matcher.
   - *Card:* drive mode.

9. **Shutter speed.** Fast for handheld (1/200+) / Moderate (1/60–1/200) / Slow for ambient (1/30–1/60) / Mixed.
   - *Feeds:* `ExposureTime` range matcher.
   - *Card:* shutter guidance.

10. **Format and bit-depth.** RAW only / RAW+JPEG / JPEG only / Mixed.
    - *Feeds:* `FileType` matcher.
    - *Card:* format note.

**Optional / depth questions:**

- Strobe trigger / HSS habits?
- Background-separation technique preferences (lens choice / distance / aperture)?
- Reflector / diffuser usage?

**Default if all questions skipped:** broad scenario matching focal length 50–200mm equiv + aperture wider than f/4 + AF-S. Classification will sometimes overlap with "Wildlife" on long-lens portraits — user can reclassify in J5.

---

## Street / Documentary (detailed)

**Goal of this scenario in classification:** photos shot walking around in public spaces, typically with a normal or wide prime, AF-S or zone focus, often higher ISO for available light, mostly single-shot drive mode, sometimes monochrome color preset. Distinct lens + drive-mode + ISO signature.

**Questions:**

1. **Lenses used.** Multi-select.
   - *Feeds:* `LensModel` matcher.
   - *Card:* lens choice.

2. **Focal-length range.** Wide (24–28mm) / Normal-wide (28–40mm) / Normal (40–60mm) / Short tele (60–105mm) / Mixed.
   - *Feeds:* `FocalLengthIn35mmFormat` range matcher.
   - *Card:* focal-length guidance.

3. **AF approach.** Single AF (subject-by-subject) / Zone focus pre-set / Manual hyperfocal / Mixed.
   - *Feeds:* `FocusMode` matcher (single / manual bias).
   - *Card:* AF technique on card.

4. **Aperture range.** Wide for separation (f/1.4–f/2.8) / Moderate (f/2.8–f/5.6) / Stopped for DOF (f/5.6–f/11) / Mixed.
   - *Feeds:* `FNumber` range matcher.
   - *Card:* aperture guidance.

5. **ISO range.** Low only (≤ 400) / Moderate (400–1600) / High (1600–6400) / Very high for low light (6400+) / Auto / Mixed.
   - *Feeds:* `ISO` range matcher.
   - *Card:* ISO guidance.

6. **Color rendering preference.** "Standard" / "Vivid" / "Monochrome" or "Acros" / Custom / Mixed.
   - *Feeds:* color-preset matcher.
   - *Card:* color preset.

7. **Drive mode.** Single shot only / Single with occasional burst / Mixed.
   - *Feeds:* `DriveMode` matcher (single-bias).
   - *Card:* drive mode.

8. **Indoor vs. outdoor split.** Mostly outdoor / Mixed / Mostly indoor (museums, transit, etc.).
   - *Feeds:* ISO range; informational.
   - *Card:* lighting note.

9. **Shutter speed.** Fast (1/250+) for moving subjects / Moderate (1/60–1/250) / Slow with stabilization (1/15–1/60) / Mixed.
   - *Feeds:* `ExposureTime` range matcher.
   - *Card:* shutter guidance.

10. **Image format.** RAW only / RAW+JPEG / JPEG only (for fast review) / Mixed.
    - *Feeds:* `FileType` matcher.
    - *Card:* format note.

**Optional / depth questions:**

- Flash usage (typically off for street etiquette)?
- Tilt-screen for hip-level shooting?

**Default if all questions skipped:** broad scenario matching focal length 28–60mm equiv + drive mode single. Likely overlaps with Travel/General — expected.

---

## Sports / Action (detailed)

**Goal of this scenario in classification:** photos of moving subjects in organized or unorganized action, typically with long-tele, AF-C tracking, very fast shutter, high burst drive mode, often higher ISO. Significant overlap risk with Wildlife — the disambiguator is usually subject matter (humans vs. animals), which the wizard's *user-selected genre* tag resolves cleanly.

**Questions:**

1. **Lenses used.** Multi-select.
   - *Feeds:* `LensModel` matcher.
   - *Card:* lens choice.

2. **Focal-length range.** Normal (35–70mm) / Short tele (70–200mm) / Long tele (200–400mm) / Very long (400mm+) / Mixed.
   - *Feeds:* `FocalLengthIn35mmFormat` range matcher.
   - *Card:* focal-length guidance.

3. **AF mode.** Continuous AF (AF-C / AI Servo / equivalent) almost always / AF-C with single AF for static moments / Mixed.
   - *Feeds:* `FocusMode` matcher (continuous-bias).
   - *Card:* AF mode on card.

4. **AF area mode.** Tracking / Subject detection (when available) / Zone / Single point / Mixed.
   - *Feeds:* `AFAreaMode` matcher.
   - *Card:* AF area on card.

5. **Shutter speed.** Very fast (1/2000+) for freezing action / Fast (1/500–1/2000) / Moderate (1/250–1/500) for panning / Mixed.
   - *Feeds:* `ExposureTime` range matcher.
   - *Card:* shutter guidance.

6. **Aperture range.** Wide open (f/1.4–f/2.8) for light / Wide-moderate (f/2.8–f/5.6) / Stopped (f/5.6–f/8) for DOF on multiple subjects / Mixed.
   - *Feeds:* `FNumber` range matcher.
   - *Card:* aperture guidance.

7. **ISO range.** Low only (≤ 400) / Moderate (400–1600) / High tolerance (1600–6400) / Very high (6400+) / Auto with high cap / Mixed.
   - *Feeds:* `ISO` range matcher.
   - *Card:* ISO guidance.

8. **Drive mode.** Burst high / Burst low / Mixed (single between actions, burst on action).
   - *Feeds:* `DriveMode` matcher.
   - *Card:* drive mode.

9. **Color preset.** "Standard" / Custom for skin tones in mixed lighting / Mixed.
   - *Feeds:* color-preset matcher.
   - *Card:* color preset.

10. **Stabilization usage.** Always on / Off for panning / Mixed.
    - *Feeds:* informational.
    - *Card:* stabilization note.

**Optional / depth questions:**

- Crop modes used (in-camera teleconverter for extra reach)?
- Pre-capture / pre-burst features (Lumix Pre-Burst, Sony preburst)?

**Default if all questions skipped:** broad scenario matching focal length ≥ 100mm equiv + AF-C + drive-mode burst. Likely overlaps with Wildlife; user's chosen genre tag at import is the disambiguator.

---

## Travel / General (detailed)

**Goal of this scenario in classification:** the "everything else" bucket — photos shot while traveling, walking around, capturing whatever shows up. Mixed everything by definition. The wizard asks what the user *typically* sets when not in a specific genre, which informs the broad fallback rules.

**Questions:**

1. **Lenses used.** Multi-select. (Typically zooms or kit lenses.)
   - *Feeds:* `LensModel` matcher (broad).
   - *Card:* lens choice.

2. **Focal-length range.** Mostly wide (under 35mm) / Mostly normal (35–85mm) / Mostly short tele (85–200mm) / Zoom range covering most of these / Mixed.
   - *Feeds:* `FocalLengthIn35mmFormat` range matcher (broad).
   - *Card:* focal-length guidance.

3. **Default shooting mode.** Aperture priority / Shutter priority / Manual with auto ISO / Full auto / Mixed.
   - *Feeds:* `ExposureProgram` matcher (where exposed).
   - *Card:* mode guidance.

4. **Aperture range.** Wider for separation (f/2.8–f/4) / Moderate (f/4–f/8) / Stopped (f/8–f/11) / Auto / Mixed.
   - *Feeds:* `FNumber` range matcher (broad).
   - *Card:* aperture guidance.

5. **ISO range.** Auto with low cap (≤ 1600) / Auto with moderate cap (≤ 3200) / Auto with high cap (≤ 6400) / Manual / Mixed.
   - *Feeds:* `ISO` range matcher (broad).
   - *Card:* ISO guidance.

6. **AF mode.** Single AF with face detection / Continuous AF / Mixed depending on subject.
   - *Feeds:* `FocusMode` matcher.
   - *Card:* AF mode.

7. **Color preset.** "Standard" / "Vivid" / Custom / Mixed.
   - *Feeds:* color-preset matcher (broad).
   - *Card:* color preset.

8. **Drive mode.** Single shot / Single with occasional burst / Mixed.
   - *Feeds:* `DriveMode` matcher (single-bias).
   - *Card:* drive mode.

9. **Shutter speed.** Auto / Moderate (1/125–1/500) / Mixed.
   - *Feeds:* `ExposureTime` range matcher (broad).
   - *Card:* shutter guidance.

10. **Image format.** RAW only / RAW+JPEG / JPEG only / Mixed.
    - *Feeds:* `FileType` matcher.
    - *Card:* format note.

**Optional / depth questions:**

- HDR or panorama habits?
- In-camera crop modes?

**Default if all questions skipped:** This *is* the fallback scenario. By design, broad matches. Captures whatever doesn't match other scenarios more strongly.

---

## Astro / Night (detailed)

**Goal of this scenario in classification:** photos of stars / Milky Way / moon / urban night scenes. Very distinctive EXIF signature — usually very long shutter + manual focus + wide aperture (for Milky Way) or moderate aperture + faster shutter (for moon). Strong, easy-to-classify pattern.

**Note:** this genre splits naturally into sub-types (Milky Way, moon, urban night, meteors, light painting). The wizard supports them as variants; the classifier may emit `astro` + a tag.

**Questions:**

1. **Sub-types you shoot.** Milky Way / Moon / Urban night / Light trails / Meteors / Star trails / Mixed.
   - *Feeds:* shapes the rest of the wizard's question path.
   - *Card:* subtype identification.

2. **Lenses used.** Multi-select.
   - *Feeds:* `LensModel` matcher.
   - *Card:* lens choice.

3. **Focal-length range.** Ultra-wide (< 24mm) for sky / Wide (24–35mm) / Normal (35–85mm) for urban night / Long tele (200mm+) for moon / Mixed.
   - *Feeds:* `FocalLengthIn35mmFormat` range matcher.
   - *Card:* focal-length guidance.

4. **Focus approach.** Manual focus always / Manual with magnification assist / Live-view AF in bright moonlight / Mixed.
   - *Feeds:* `FocusMode` matcher (manual-bias).
   - *Card:* focus technique.

5. **Aperture range.** Wide open (f/1.4–f/2.8) for Milky Way / Moderate (f/4–f/5.6) for urban / Smaller (f/8–f/11) for moon / Mixed.
   - *Feeds:* `FNumber` range matcher.
   - *Card:* aperture guidance.

6. **Shutter speed.** Very long (10–30s) for Milky Way / Long (1–10s) for cityscapes / Moderate (1/30–1s) for handheld night / Fast (1/250+) for moon / Mixed.
   - *Feeds:* `ExposureTime` range matcher.
   - *Card:* shutter guidance.

7. **ISO range.** Very high (3200+) for Milky Way / Moderate (400–1600) for urban / Low (100–400) for stacked or moon / Mixed.
   - *Feeds:* `ISO` range matcher.
   - *Card:* ISO guidance.

8. **Tripod usage.** Always / Almost always / Mixed (handheld for some urban).
   - *Feeds:* informational.
   - *Card:* tripod note.

9. **Color preset.** "Standard" / Custom (for star color rendition) / Mixed.
   - *Feeds:* color-preset matcher.
   - *Card:* color preset.

10. **Multi-shot techniques.** Star stacking / Image averaging / Single-shot only / Mixed.
    - *Feeds:* flag `expects_focus_brackets: true` or `expects_exposure_brackets: true` on the scenario; influences bracket-detector behavior.
    - *Card:* technique note.

**Optional / depth questions:**

- Long-exposure noise reduction (LENR) on?
- Light pollution filter usage?
- Tracker / star tracker mount?
- Live Composite mode (Olympus, Panasonic)?

**Default if all questions skipped:** broad scenario matching shutter ≥ 1 second. The strong shutter signature alone usually classifies astro/night correctly without further input.

---

## Family / Events (detailed)

**Goal of this scenario in classification:** photos of family gatherings, parties, kids, weddings as a guest, birthdays. Mixed lighting, often indoor, often flash, AF-C with face detection, mixed drive modes, variable lenses.

**Significant overlap risk with Portrait** — the disambiguator is multiple-subjects + event-context + drive-mode + indoor signal. The wizard's *user-selected genre* tag is the cleanest disambiguator.

**Questions:**

1. **Lenses used.** Multi-select.
   - *Feeds:* `LensModel` matcher.
   - *Card:* lens choice.

2. **Focal-length range.** Wide for group shots (24–35mm) / Normal (35–70mm) / Short tele (70–135mm) / Mixed.
   - *Feeds:* `FocalLengthIn35mmFormat` range matcher.
   - *Card:* focal-length guidance.

3. **AF mode.** Single AF with face/eye detection / Continuous AF with face / Mixed.
   - *Feeds:* `FocusMode` + `AFAreaMode` matchers.
   - *Card:* AF technique.

4. **Aperture range.** Wide (f/1.8–f/2.8) for low light / Moderate (f/2.8–f/5.6) / Smaller (f/5.6–f/8) for group shots / Mixed.
   - *Feeds:* `FNumber` range matcher.
   - *Card:* aperture guidance.

5. **ISO range.** Auto with high cap (≤ 6400) for indoor / Moderate (400–1600) / Mixed.
   - *Feeds:* `ISO` range matcher.
   - *Card:* ISO guidance.

6. **Flash usage.** On-camera bounce / Off-camera with trigger / Available light only / Mixed.
   - *Feeds:* `Flash` matcher.
   - *Card:* flash technique.

7. **Indoor vs. outdoor split.** Mostly indoor / Mixed / Mostly outdoor.
   - *Feeds:* informational; ISO range hints.
   - *Card:* lighting note.

8. **Drive mode.** Single / Burst low for expressions / Mixed.
   - *Feeds:* `DriveMode` matcher.
   - *Card:* drive mode.

9. **Color preset.** "Portrait" / "Standard" / "Vivid" / Custom / Mixed.
   - *Feeds:* color-preset matcher.
   - *Card:* color preset.

10. **Image format.** RAW for important events / JPEG for casual / RAW+JPEG / Mixed.
    - *Feeds:* `FileType` matcher.
    - *Card:* format note.

**Optional / depth questions:**

- Diffuser / bouncing techniques for flash?
- High-ISO noise tolerance specific to family/casual standards?

**Default if all questions skipped:** broad scenario matching focal length 35–135mm equiv + Flash fired OR ISO ≥ 1600. Strong overlap with Portrait expected.

---

## Video (detailed)

**Goal of this scenario in classification:** the user shoots video as a recurring practice, not just incidentally. v1's video pipeline is partial — video clips are bucketed (J5) and can be trimmed externally (v2). Classification of video clips into specific scenarios is *partial* in v1; mostly clips just bucket as "video" until the user manually scopes them.

**Note:** video metadata is very different from stills EXIF. Fields like frame rate, codec, bit depth, recording mode, picture profile (Log / HLG / Standard) are recorded; aperture/shutter/ISO may or may not be. The wizard's questions reflect this gap.

**Questions:**

1. **Bodies you shoot video on.** Multi-select from cameras + phones.
   - *Feeds:* identifies which device produced a clip; not directly classification logic, but useful for the file-tree organization.
   - *Card:* device list.

2. **Typical recording mode.** Photo style (in-camera basic) / Cinelike / V-Log / HLG / Standard / Mixed / I don't know.
   - *Feeds:* `PictureProfile` / `LogMode` matcher where exposed.
   - *Card:* recording mode.

3. **Resolution + frame rate.** 4K 30p / 4K 60p / 4K 24p (cinematic) / FHD 60p / FHD 30p / Mixed.
   - *Feeds:* `VideoResolution` + `VideoFrameRate` matchers.
   - *Card:* resolution + frame rate.

4. **Lenses used for video.** Multi-select.
   - *Feeds:* `LensModel` matcher.
   - *Card:* lens choice.

5. **Focal-length range.** Wide (24–35mm) / Normal (35–85mm) / Tele (85–200mm) / Long tele (200mm+) / Mixed.
   - *Feeds:* `FocalLengthIn35mmFormat` range matcher.
   - *Card:* focal-length guidance.

6. **Focus approach.** Continuous AF with subject tracking / Manual focus / Mixed.
   - *Feeds:* `FocusMode` matcher.
   - *Card:* focus technique.

7. **Audio.** In-camera mic / External mic on hot shoe / Wireless lavalier / Mixed.
   - *Feeds:* informational; not directly EXIF.
   - *Card:* audio setup.

8. **Stabilization.** IBIS only / IBIS + electronic stabilization / Gimbal / Tripod / Handheld with stabilizer / Mixed.
   - *Feeds:* informational.
   - *Card:* stabilization note.

9. **Subject of most video clips.** Wildlife behavior / Travel B-roll / Family / Macro behavior / Mixed / Other.
   - *Feeds:* may inform scenario assignment when classification is too uncertain.
   - *Card:* subject focus.

10. **Bitrate / codec preference.** Default JPEG-style / High bitrate (100Mbps+) / All-Intra / Long-GOP / I don't know.
    - *Feeds:* `VideoCodec` + `Bitrate` informational matchers.
    - *Card:* codec note.

**Optional / depth questions:**

- LUT application in-camera?
- Frame-rate switching for slow motion (120fps, 240fps)?
- Shooting raw video (ProRes RAW)?

**Default if all questions skipped:** all video clips bucket as "video / general." v1 does not over-attempt classification of video into specific scenarios.

**v1 scope note:** classification of video into specific scenarios beyond "video / general" is a v1 stretch goal, not a v1 mandate. The bucket scanner identifies clips correctly; further per-scenario classification is a v1.1+ feature unless a strong signal (lens + duration) makes auto-classification cheap.

---

## What the question bank reveals (initial implications)

1. **Color preset is the most brand-fragmented field.** Eight brands, eight names. The glossary table earlier in this doc is the v1 commitment. Patches will refine.

2. **AF area mode is the second-most brand-fragmented field.** Even more so than `FocusMode`. Likely stored as a normalized enum (`single_point`, `zone`, `wide`, `tracking`, `subject_detection`) with brand-specific mapping in the brand profile.

3. **Drive mode names vary almost as much as color presets.** Worth its own glossary entry.

4. **The "Mixed / Unsure / Skip" answer is load-bearing.** A user who skips every question still produces a scenario — just a very broad one that classifies many photos as "General." This is correct behavior: the wizard meets users where they are.

5. **Focus bracketing / focus stacking is a binary signal on the scenario** rather than a classification axis. It tells the bracket detector to expect grouped sequences when this scenario matches. (Cross-reference v2_design.md §10 Bracket Sequence Detector — that engine is reusable here.)

6. **Lens model is a strong matcher.** A user with one macro lens used only for macro almost doesn't need any other questions for macro to classify correctly. This is the strongest single signal in the EXIF.

7. **Some questions feed only the reference card, not classification.** Tripod usage, filter usage, lighting context. These improve J8 output even when they cannot inform J5.

---

## Open questions for Phase 1 (will be brought to user discussion)

1. **Should the wizard ship with a "broad starter" scenario per genre** that any user gets even if they skip all questions? (Lean: yes — at least one default per chosen genre, fully broad.)
2. **How many color-preset glossary entries does v1 ship with?** The 8-brand table above is the v1 commitment; lesser-known brands (Sigma, Hasselblad, Phase One) can wait for patches.
3. **Should "Mixed" be a discrete value stored in the scenario, or trigger a follow-up question** like "describe the two patterns you switch between"? (Lean: discrete value in v1; follow-up is a v1.1 polish item.)
4. **Should the wizard offer a "Sample my photos" mode** that infers some answers from existing photo folders? (Lean: nice-to-have for v1; mandatory feature for v1.1 if not v1.)
5. **What is the upper bound on the wizard's total length?** A user picking all 10 genres could face 80+ questions. Hard cap? Soft warning? Multi-session resumability? (Lean: multi-session resumability — the wizard's journal is the same crash-safe pattern as J5 cull.)

---

## Next iterations

- Fill in the six stubbed genres (Street, Sports, Travel, Astro, Family, Video).
- Annotate each question with its target EXIF field name across at least Panasonic and Sony (the bodies the author can validate).
- Specify the scenario JSON schema (rough sketch in `03-v1-scope.md`; needs a full draft here or in a `05-scenario-schema.md` companion).
- Cross-walk this question bank against `v2_design.md` §11 (Refinement Rules Engine) to confirm the rules generated by these answers can actually be expressed in that engine — or note where the engine needs to grow.
