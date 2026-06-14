# 09 — Built-in Starter Scenarios

> **Status: Phase 1 deliverable.** Concrete v1 release content. Eleven starter scenarios (one per genre + a "General" universal fallback) representing the **broadest reasonable defaults** the wizard generates when the user skips every question for that genre. The classifier evaluates these in the order specified below (first-match-wins per v2_design.md §11.5). User-customized scenarios override these on a per-user basis but the built-ins guarantee a classification always exists.

## Why these exist

- **Reliability floor.** If a user skips every wizard question, the system still classifies their photos meaningfully — into the genres they selected, with reasonable confidence. Without the built-ins, "skip everything" produces no scenarios at all and the classifier has nothing to match against.
- **Onboarding sample.** The built-ins let a brand-new user with zero answers actually *see* the system work on their own photos within minutes, then refine via the wizard once they understand what scenarios are.
- **Schema validation.** Concretely populating the schema (`docs/07`) catches gaps in the design before any code is written.
- **Translation seeds.** The reference-card content in these scenarios is the first translation work for v1 (En + Pt). Native-speaker review in success criterion C3 starts here.

## Classifier evaluation order

Per §11.5 (first-match-wins), the built-ins must be ordered from most-specific to least-specific. The recommended order:

1. **Video** — file-type-driven; trivial match.
2. **Astro / Night** — very long shutter is uniquely distinctive.
3. **Macro** — macro-lens or magnification-driven; second-most distinctive.
4. **Wildlife** — long focal length + AF-C + burst. Overlaps with Sports; user-selected genre context resolves.
5. **Sports / Action** — long focal length + AF-C + burst, *but* user explicitly selected Sports. Without genre context, indistinguishable from Wildlife.
6. **Landscape** — wide focal length + small aperture.
7. **Portrait** — short-tele focal length + wide aperture + face detection.
8. **Family / Events** — face detection + flash + indoor signal.
9. **Street / Documentary** — normal focal length + AF-S + single shot.
10. **Travel / General** — catch-all for medium focal length and mixed settings.
11. **General (fallback)** — universal match. Must be last.

In the JSON files below, the `id` field is the ordering key; runtime evaluation order is the array index of the scenario in `assets/scenarios/builtins.json`.

---

## Wildlife

```json
{
  "schema_version": 1,
  "id": "builtin-wildlife",
  "name": "Wildlife",
  "name_localized": {
    "en": "Wildlife",
    "pt": "Vida Selvagem"
  },
  "genre": "wildlife",
  "kind": "final",
  "description": "Long-lens photography of animals and birds.",
  "description_localized": {
    "en": "Long-lens photography of animals and birds.",
    "pt": "Fotografia de animais e pássaros com lente teleobjetiva."
  },
  "created_by": "builtin",
  "wizard_version": null,

  "exif_expectations": {
    "focal_35mm": { "gte": 200 },
    "focus_mode": { "in": ["continuous", "single"] }
  },
  "exif_expectations_optional": {
    "drive_mode": { "in": ["burst_low", "burst_high"] },
    "subject_detection": { "in": ["animal", "bird"] }
  },

  "reference_card": {
    "physical_setup": {
      "lens": "Telephoto (200mm+ equivalent)",
      "tripod": "Monopod or handheld with stabilization"
    },
    "software_settings": {
      "mode": "Aperture priority or manual with Auto ISO",
      "aperture": "f/5.6 to f/8",
      "iso": "Auto, with high cap",
      "shutter": "1/2000s for birds in flight, 1/500s for static",
      "focus_mode": "AF-C (continuous)",
      "af_area": "Tracking or subject detection",
      "drive_mode": "Burst",
      "color_preset": "Standard"
    },
    "rationale": "Wildlife rewards fast reactions and long reach. Shutter speed is non-negotiable.",
    "common_mistakes": [
      "Shutter too slow for the subject's motion",
      "ISO cap too low — losing the shot to motion blur is worse than mild noise"
    ]
  },
  "confidence_baseline": 0.70
}
```

---

## Macro

```json
{
  "schema_version": 1,
  "id": "builtin-macro",
  "name": "Macro",
  "name_localized": { "en": "Macro", "pt": "Macro" },
  "genre": "macro",
  "kind": "final",
  "description": "Close-up photography of small subjects.",
  "description_localized": {
    "en": "Close-up photography of small subjects.",
    "pt": "Fotografia de aproximação de pequenos sujeitos."
  },
  "created_by": "builtin",

  "exif_expectations": {
    "lens_keywords": { "in": ["macro", "MACRO", "Macro"] }
  },
  "exif_expectations_optional": {
    "focus_mode": { "eq": "manual" },
    "aperture": { "gte": 5.6 }
  },

  "reference_card": {
    "physical_setup": {
      "lens": "Dedicated macro lens",
      "tripod": "Often used for focus-bracket sequences",
      "flash": "Optional — ring flash, off-camera with diffuser, or natural light"
    },
    "software_settings": {
      "mode": "Manual or aperture priority",
      "aperture": "f/5.6 to f/16 depending on subject and DOF needs",
      "iso": "Low (100-800) on tripod; higher handheld",
      "shutter": "Sync speed (1/200s) with flash; faster handheld",
      "focus_mode": "Manual focus, often with magnification assist",
      "drive_mode": "Single, or focus-bracket sequence"
    },
    "rationale": "Macro DOF is razor-thin. Focus discipline and stable shooting matter more than fast shutter.",
    "common_mistakes": [
      "Aperture too wide — DOF disappears at high magnification",
      "Forgetting to switch to manual focus when AF hunts on close subjects"
    ]
  },
  "confidence_baseline": 0.75
}
```

**Note on `lens_keywords`:** this is a substring match on the normalized `LensModel` string, matching any of the listed substrings. It is a small extension to §11's operator vocabulary (a `contains_any` operator). If we don't want to extend §11, an alternative is matching against a curated `lens_models` list maintained by the user; the keyword approach is more user-friendly for v1 since it works with unknown lenses. Phase 3 decides.

---

## Landscape

```json
{
  "schema_version": 1,
  "id": "builtin-landscape",
  "name": "Landscape",
  "name_localized": { "en": "Landscape", "pt": "Paisagem" },
  "genre": "landscape",
  "kind": "final",
  "description": "Scenic photography, typically wide angle and stopped down.",
  "description_localized": {
    "en": "Scenic photography, typically wide angle and stopped down.",
    "pt": "Fotografia de paisagens, geralmente grande angular com diafragma fechado."
  },
  "created_by": "builtin",

  "exif_expectations": {
    "focal_35mm": { "lte": 50 },
    "aperture": { "gte": 5.6 }
  },
  "exif_expectations_optional": {
    "iso": { "lte": 400 },
    "color_preset": { "in": ["landscape", "scenery"] }
  },

  "reference_card": {
    "physical_setup": {
      "lens": "Wide angle (24-35mm equivalent) or normal zoom",
      "tripod": "Recommended for low-light and long exposure"
    },
    "software_settings": {
      "mode": "Aperture priority or manual",
      "aperture": "f/8 to f/11 for maximum DOF",
      "iso": "Base ISO (100-200) when possible",
      "shutter": "Whatever results from base ISO + chosen aperture",
      "focus_mode": "Single AF or manual hyperfocal"
    },
    "rationale": "DOF and detail across the frame matter most. Tripod enables base ISO and slow shutters when conditions get dim.",
    "common_mistakes": [
      "Aperture too small (past f/16) — diffraction softens detail",
      "Hand-held in dim light — shutter falls below sharpness threshold"
    ]
  },
  "confidence_baseline": 0.70
}
```

---

## Portrait

```json
{
  "schema_version": 1,
  "id": "builtin-portrait",
  "name": "Portrait",
  "name_localized": { "en": "Portrait", "pt": "Retrato" },
  "genre": "portrait",
  "kind": "final",
  "description": "People photography with subject separation.",
  "description_localized": {
    "en": "People photography with subject separation.",
    "pt": "Fotografia de pessoas com separação do sujeito."
  },
  "created_by": "builtin",

  "exif_expectations": {
    "focal_35mm": { "gte": 50, "lte": 200 },
    "aperture": { "lte": 5.6 }
  },
  "exif_expectations_optional": {
    "subject_detection": { "in": ["human", "face", "eye"] },
    "color_preset": { "eq": "portrait" }
  },

  "reference_card": {
    "physical_setup": {
      "lens": "Short telephoto (85-135mm equivalent ideal) or normal prime"
    },
    "software_settings": {
      "mode": "Aperture priority",
      "aperture": "f/1.8 to f/4 for subject separation",
      "iso": "Auto with sensible cap",
      "shutter": "1/(focal length) minimum to avoid blur",
      "focus_mode": "AF-S with eye/face detection if available"
    },
    "rationale": "The subject's eyes should be sharp; everything else can blur out of the way.",
    "common_mistakes": [
      "Aperture too wide — only one eye in focus",
      "Background too busy — distracting context kills the portrait"
    ]
  },
  "confidence_baseline": 0.65
}
```

---

## Street / Documentary

```json
{
  "schema_version": 1,
  "id": "builtin-street",
  "name": "Street",
  "name_localized": { "en": "Street", "pt": "Rua" },
  "genre": "street",
  "kind": "final",
  "description": "Documentary photography in public spaces.",
  "description_localized": {
    "en": "Documentary photography in public spaces.",
    "pt": "Fotografia documental em espaços públicos."
  },
  "created_by": "builtin",

  "exif_expectations": {
    "focal_35mm": { "gte": 24, "lte": 70 },
    "drive_mode": { "eq": "single" }
  },
  "exif_expectations_optional": {
    "focus_mode": { "in": ["single", "manual"] },
    "color_preset": { "in": ["standard", "monochrome"] }
  },

  "reference_card": {
    "physical_setup": {
      "lens": "Normal prime (35-50mm equivalent ideal) or compact zoom"
    },
    "software_settings": {
      "mode": "Aperture priority or manual with Auto ISO",
      "aperture": "f/4 to f/8 for working depth of field",
      "iso": "Auto with high cap for low-light scenes",
      "shutter": "1/250s or faster for moving subjects",
      "focus_mode": "AF-S with single point, or pre-set hyperfocal manual",
      "drive_mode": "Single shot"
    },
    "rationale": "Speed and discretion. Composition matters more than technical perfection.",
    "common_mistakes": [
      "Hesitating to shoot — the moment passes",
      "Flash in candid scenes — ruins the mood and draws attention"
    ]
  },
  "confidence_baseline": 0.55
}
```

---

## Sports / Action

```json
{
  "schema_version": 1,
  "id": "builtin-sports",
  "name": "Sports",
  "name_localized": { "en": "Sports", "pt": "Esportes" },
  "genre": "sports",
  "kind": "final",
  "description": "Fast-moving subjects in organized action.",
  "description_localized": {
    "en": "Fast-moving subjects in organized action.",
    "pt": "Sujeitos em movimento rápido em ações organizadas."
  },
  "created_by": "builtin",

  "exif_expectations": {
    "focal_35mm": { "gte": 100 },
    "focus_mode": { "eq": "continuous" },
    "shutter_speed": { "lte": 0.002 }
  },
  "exif_expectations_optional": {
    "drive_mode": { "in": ["burst_low", "burst_high"] },
    "subject_detection": { "in": ["human"] }
  },

  "reference_card": {
    "physical_setup": {
      "lens": "Telephoto zoom (70-200mm or 100-400mm equivalent)"
    },
    "software_settings": {
      "mode": "Shutter priority or manual with Auto ISO",
      "shutter": "1/1000s minimum, 1/2000s for fast action",
      "aperture": "Whatever the shutter speed demands; wider is faster",
      "iso": "Auto with high cap",
      "focus_mode": "AF-C with tracking or subject detection",
      "drive_mode": "Burst high"
    },
    "rationale": "Freeze the peak action moment. Light is usually the constraint.",
    "common_mistakes": [
      "Focal length too short — missing the decisive face",
      "AF-S for moving subjects — guaranteed soft eyes"
    ]
  },
  "confidence_baseline": 0.55
}
```

---

## Travel / General

```json
{
  "schema_version": 1,
  "id": "builtin-travel",
  "name": "Travel",
  "name_localized": { "en": "Travel", "pt": "Viagem" },
  "genre": "travel",
  "kind": "final",
  "description": "General photography while traveling.",
  "description_localized": {
    "en": "General photography while traveling.",
    "pt": "Fotografia geral durante viagens."
  },
  "created_by": "builtin",

  "exif_expectations": {
    "focal_35mm": { "gte": 24, "lte": 200 }
  },
  "exif_expectations_optional": {},

  "reference_card": {
    "physical_setup": {
      "lens": "Versatile zoom (24-70, 24-105, 24-200 equivalent)"
    },
    "software_settings": {
      "mode": "Aperture priority for control, or program for speed",
      "aperture": "f/5.6 to f/8 typical",
      "iso": "Auto with sensible cap",
      "shutter": "Auto",
      "focus_mode": "AF-S with face detection"
    },
    "rationale": "Be ready for anything. Versatile gear and conservative settings get you usable shots in most situations.",
    "common_mistakes": [
      "Over-thinking — you walked past the shot while changing settings",
      "Flat compositions — travel scenes need foreground interest"
    ]
  },
  "confidence_baseline": 0.50
}
```

---

## Astro / Night

```json
{
  "schema_version": 1,
  "id": "builtin-astro",
  "name": "Astro / Night",
  "name_localized": { "en": "Astro / Night", "pt": "Astro / Noturna" },
  "genre": "astro",
  "kind": "final",
  "description": "Low-light photography including stars, moon, and urban night.",
  "description_localized": {
    "en": "Low-light photography including stars, moon, and urban night.",
    "pt": "Fotografia de baixa luz incluindo estrelas, lua e cenas urbanas noturnas."
  },
  "created_by": "builtin",

  "exif_expectations": {
    "shutter_speed": { "gte": 1.0 }
  },
  "exif_expectations_optional": {
    "iso": { "gte": 1600 },
    "focus_mode": { "eq": "manual" }
  },

  "reference_card": {
    "physical_setup": {
      "lens": "Wide aperture lens (f/1.4-f/2.8 for stars; any for cityscapes)",
      "tripod": "Mandatory — long shutters demand stability"
    },
    "software_settings": {
      "mode": "Manual",
      "aperture": "Wide open for Milky Way; f/8-f/11 for moon",
      "iso": "1600-3200 for Milky Way; base ISO for moon",
      "shutter": "10-25s for Milky Way; 1/250s or faster for moon",
      "focus_mode": "Manual focus with live-view magnification"
    },
    "rationale": "Light is scarce. Stable mount + manual focus + deliberate exposure.",
    "common_mistakes": [
      "Auto white balance — gives a flat blue cast; use daylight or cooler",
      "Forgetting long-exposure NR — leaves hot pixels in dark areas"
    ]
  },
  "confidence_baseline": 0.90
}
```

The very-long-shutter signature is uniquely distinctive, hence the high baseline confidence.

---

## Family / Events

```json
{
  "schema_version": 1,
  "id": "builtin-family",
  "name": "Family / Events",
  "name_localized": { "en": "Family / Events", "pt": "Família / Eventos" },
  "genre": "family",
  "kind": "final",
  "description": "Family gatherings and casual social events.",
  "description_localized": {
    "en": "Family gatherings and casual social events.",
    "pt": "Reuniões familiares e eventos sociais casuais."
  },
  "created_by": "builtin",

  "exif_expectations": {
    "focal_35mm": { "gte": 24, "lte": 135 },
    "flash_fired": { "eq": true }
  },
  "exif_expectations_optional": {
    "iso": { "gte": 800 },
    "subject_detection": { "in": ["face"] }
  },

  "reference_card": {
    "physical_setup": {
      "lens": "Normal zoom or fast prime",
      "flash": "On-camera with bounce or off-camera with diffuser"
    },
    "software_settings": {
      "mode": "Aperture priority or program",
      "aperture": "f/2.8 to f/5.6",
      "iso": "Auto with high cap for indoor",
      "shutter": "1/100s minimum (sync speed if flash)",
      "focus_mode": "AF-C with face detection",
      "drive_mode": "Single shot, occasional burst"
    },
    "rationale": "Mixed lighting, multiple subjects, expressions matter. Flash is often necessary.",
    "common_mistakes": [
      "Direct on-camera flash — flat, unflattering light",
      "Aperture too wide for group shots — only one person in focus"
    ]
  },
  "confidence_baseline": 0.50
}
```

---

## Video

```json
{
  "schema_version": 1,
  "id": "builtin-video",
  "name": "Video",
  "name_localized": { "en": "Video", "pt": "Vídeo" },
  "genre": "video",
  "kind": "final",
  "description": "Video clips of any subject.",
  "description_localized": {
    "en": "Video clips of any subject.",
    "pt": "Clipes de vídeo de qualquer tema."
  },
  "created_by": "builtin",

  "exif_expectations": {
    "file_type": { "in": ["mp4", "mov", "m4v", "avi", "mkv"] }
  },
  "exif_expectations_optional": {},

  "reference_card": {
    "physical_setup": {
      "lens": "Variable — depends on subject",
      "tripod": "Often used for stable footage"
    },
    "software_settings": {
      "mode": "Movie mode",
      "frame_rate": "30p for general, 24p for cinematic, 60p+ for slow motion",
      "resolution": "4K for archival, FHD for smaller files",
      "focus_mode": "Continuous AF with tracking"
    },
    "rationale": "File-type-driven classification — v1 does not deeply classify video into sub-genres."
  },
  "confidence_baseline": 0.95
}
```

The file-type signature is unique. Video clips classify immediately on this rule.

---

## General (fallback — must be last)

```json
{
  "schema_version": 1,
  "id": "builtin-general",
  "name": "General",
  "name_localized": { "en": "General", "pt": "Geral" },
  "genre": "general",
  "kind": "final",
  "description": "Photos that don't match any specific scenario.",
  "description_localized": {
    "en": "Photos that don't match any specific scenario.",
    "pt": "Fotos que não correspondem a nenhum cenário específico."
  },
  "created_by": "builtin",

  "exif_expectations": {},
  "exif_expectations_optional": {},

  "reference_card": {
    "rationale": "If a photo lands here, none of your defined scenarios matched. Review the photo and consider whether a new scenario would help, or whether your existing scenarios need refining."
  },
  "confidence_baseline": 0.30
}
```

The General scenario matches everything. The classifier evaluates it last and uses it only when nothing else matched. Low confidence baseline is intentional — every General-classified photo should prompt the user to review and consider refining their scenario library.

---

## Implementation notes

- **Storage:** built-ins ship under `assets/scenarios/builtins.json` as a single ordered array. The order in that file *is* the classifier evaluation order. Per `docs/07`, user scenarios under `%LOCALAPPDATA%/{AppName}/scenarios/` override built-ins by `id`.
- **Genre filtering at load time:** the wizard only emits built-ins for genres the user selected. A user who picked "Wildlife + Macro + General" gets three built-ins loaded, not all eleven. Saves classifier work and avoids overclaiming on genres the user does not shoot.
- **Localization:** the `name_localized` and `description_localized` fields ship in En and Pt for v1; Es is added in v1.1 by extending the same JSON. `reference_card` content is *not* shipped with localization in v1 — the wizard's English question bank generates English card content; for non-English locales, the wizard's localized question bank generates localized card content; the *built-ins* (which the user gets by skipping all questions) have English-only card content in v1, with a flag for v1.1 to add Pt card content.

Actually — this last point is a real gap. **In v1, if a Portuguese-locale user skips every wizard question, they get scenarios with Portuguese names and descriptions but English reference-card content.** That is sub-optimal. Two fixes possible:
- (a) Localize the built-in reference-card content to Pt as part of v1's release-process translation pass.
- (b) Skip the built-in reference card content and only generate card content when the user answers wizard questions.

**Recommend (a)** — content is small (11 short reference cards × 2 locales = manageable). Add as a release-process item: translate the built-in reference cards to Pt before v1 release.

---

## What this set does not cover (and why that's fine)

- **No intermediate scenarios in the built-ins.** Focus brackets and exposure brackets are detected by the bracket detector (§12 of v2_design) before refinement rules run, so they never reach the scenario-matching step. No built-in needed.
- **No sub-genre tags.** A wildlife photo could in principle carry tag `birds` vs `mammals`; v1's built-ins emit only the genre, leaving sub-classification to user-generated scenarios via wizard answers.
- **No exposure-priority-specific scenarios** (e.g., "long exposure" as separate from "astro"). Collapse into Astro for v1 — long-exposure photos that are *not* astro (waterfalls, light trails) still classify as Astro by the long-shutter signature; the user reclassifies in J5/J6 if they want a separate genre.

---

## Update to v1 release-process (added by this document)

The release-process gates need a localization addition:

- **Built-in reference-card content localized to Pt.** Eleven short reference cards × Pt translation. Native-speaker reviewed. Ships in `assets/scenarios/builtins.json`.

(This should be added to `03-v1-scope.md` "Release process / acceptance gates" section in a follow-up edit. Flagging here rather than editing in-line so the v1-scope changes are made consciously.)
