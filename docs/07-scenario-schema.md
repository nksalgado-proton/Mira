# 07 — Scenario JSON Schema + Cross-Walk Against Refinement Rules

> **Status: Phase 1 deliverable.** Specifies the JSON shape the wizard produces and the user library stores. Confirms that the wizard's questions are expressible in v2_design.md §11's refinement-rules engine — the engine that runs the actual classifier. Format-stable for v1; field additions are non-breaking; field semantics are versioned.

## Why this document exists

The wizard (`docs/04`) asks the user genre-by-genre questions. The classifier (v2_design.md §11) runs declarative rules with first-match-wins. **This document is the contract between them.** The wizard outputs scenarios in the schema below; the classifier consumes the same schema (transformed at load time into refinement-rule entries). One stable shape, two consumers.

A second consumer is **J8 reference-card export** — it reads the same scenarios to build the printable / installable cards.

## Schema (v1)

```json
{
  "schema_version": 1,
  "id": "wildlife-default",
  "name": "Wildlife",
  "name_localized": {
    "en": "Wildlife",
    "pt": "Vida Selvagem"
  },
  "genre": "wildlife",
  "kind": "final",
  "description": "Birds and mammals, typically with a long lens at fast shutter speeds.",
  "description_localized": {
    "en": "Birds and mammals, typically with a long lens at fast shutter speeds.",
    "pt": "Pássaros e mamíferos, tipicamente com lente teleobjetiva e velocidades altas."
  },

  "created_by": "wizard",
  "created_at": "2026-05-12T10:30:00Z",
  "modified_at": "2026-05-12T10:30:00Z",
  "wizard_version": "1.0",

  "lens_assignments": [
    "Leica DG Vario-Elmar 100-400mm f/4-6.3 II ASPH",
    "Lumix G Vario 35-100mm f/2.8 II"
  ],

  "exif_expectations": {
    "lens_models": {
      "in": [
        "Leica DG Vario-Elmar 100-400mm f/4-6.3 II ASPH",
        "Lumix G Vario 35-100mm f/2.8 II"
      ]
    },
    "focal_35mm": { "gte": 200, "lte": 800 },
    "aperture": { "gte": 4.0, "lte": 8.0 },
    "iso": { "gte": 200, "lte": 6400 },
    "shutter_speed": { "gte": 0.0005, "lte": 0.005 },
    "focus_mode": { "eq": "continuous" },
    "af_area_mode": { "in": ["tracking", "subject_detection", "zone"] },
    "drive_mode": { "in": ["burst_low", "burst_high"] },
    "color_preset": { "eq": "standard" },
    "subject_detection": { "in": ["animal", "bird"] }
  },

  "exif_expectations_optional": {
    "stabilization": { "in": ["on", "boost"] }
  },

  "reference_card": {
    "physical_setup": {
      "lens": "100-400mm or longer",
      "tripod": "Monopod or handheld with stabilization",
      "accessories": ["Lens hood"],
      "filter": null,
      "flash": null
    },
    "software_settings": {
      "mode": "Aperture priority or manual with Auto ISO",
      "aperture": "f/5.6 to f/8",
      "iso": "Auto, max 6400",
      "shutter": "1/2000s minimum for birds in flight, 1/500s for static",
      "focus_mode": "AF-C (continuous)",
      "af_area": "Tracking or subject detection",
      "drive_mode": "Burst high",
      "color_preset": "Standard",
      "white_balance": "Auto",
      "format": "RAW"
    },
    "field_adjustments": [
      "If shutter drops below 1/1000s, raise ISO cap",
      "Switch to AF-S for static subjects to save battery",
      "Lower drive to single shot when shooting perched birds"
    ],
    "rationale": "Wildlife rewards fast reactions and long reach. Shutter speed is non-negotiable. Burst captures the decisive moment.",
    "common_mistakes": [
      "Shutter too slow for the subject's motion",
      "Forgetting to switch back to AF-C when subject starts moving",
      "ISO cap too low — losing the shot to motion blur is worse than mild noise"
    ]
  },

  "custom_mode_slot": "C3-1",

  "confidence_baseline": 0.85,

  "tags": ["birds", "wildlife", "longtele"]
}
```

## Field-by-field reference

### Identity

- **`schema_version`** *(int, required)*. Currently `1`. Increments only for incompatible changes.
- **`id`** *(string, required)*. Stable identifier. Lowercase, hyphen-separated, must not change after creation (user edits the `name`, not the `id`).
- **`name`** *(string, required)*. User-facing default name. Defaults to English; gets shown if the user's locale has no entry in `name_localized`.
- **`name_localized`** *(object, optional)*. `{ "en": "...", "pt": "..." }`. v1 ships En + Pt; v1.1 adds Es.
- **`genre`** *(enum string, required)*. One of: `wildlife / macro / landscape / portrait / street / sports / travel / astro / family / video / general`. Used for grouping in UI and for cross-genre overlap detection.
- **`kind`** *(enum string, required)*. `final` (permanent, used for organization) or `intermediate` (focus_bracket, exposure_bracket — transitional, dies after stack processing). See v2_design §6.
- **`description` / `description_localized`** *(string / object, optional)*. Short user-facing description.

### Provenance

- **`created_by`** *(enum string, required)*. `wizard / user / builtin / imported`. Tracks where a scenario came from.
- **`created_at` / `modified_at`** *(ISO 8601 string, required)*.
- **`wizard_version`** *(string, optional)*. Records which wizard version produced this scenario. Used to detect "this was generated by an old wizard; offer to re-run."

### Gear

- **`lens_assignments`** *(array of strings, optional)*. Canonical lens model names this scenario expects to be used with. Feeds both `exif_expectations.lens_models` (classifier) and the reference card (J8).

### EXIF expectations (the classifier-facing payload)

**`exif_expectations`** *(object, required)* — fields that **must match** for the scenario to be a candidate. Each field uses the operator vocabulary from §11.3 (see cross-walk below). All fields are AND-ed.

**`exif_expectations_optional`** *(object, optional)* — fields that **boost confidence when matched** but are not required. Used by the classifier to disambiguate close calls. Implementation note for Phase 3: this requires a small extension to §11's match semantics (optional fields raise confidence by a per-field weight when present).

Available fields (subset of §11.4):

| Field | Type | Example operators | Notes |
|---|---|---|---|
| `lens_models` | string list | `in`, `not_in` | Lens model strings as written by the brand profile after normalization. |
| `focal_length` | float (mm) | `eq`, `gt`, `gte`, `lt`, `lte`, range | Physical focal length. |
| `focal_35mm` | float (mm equiv) | range | Computed via body crop factor. |
| `aperture` | float (f-number) | range | |
| `shutter_speed` | float (seconds) | range | `gte: 1.0` for long exposure. |
| `iso` | int | range | |
| `iso_relative_to_body` | enum | `eq`, `in` | `low / normal / high` vs body's `iso_baseline`. |
| `focus_mode` | enum | `eq`, `in` | `manual / single / continuous`. |
| `af_area_mode` | enum | `in`, `not_in` | Normalized: `single_point / zone / wide / tracking / subject_detection`. |
| `subject_detection` | enum | `in` | `animal / bird / human / face / eye / off`. Requires body capability. |
| `drive_mode` | enum | `in` | `single / burst_low / burst_high / self_timer / interval / bracket`. |
| `flash_fired` | bool | `eq` | |
| `color_preset` | enum | `eq`, `in` | Normalized: `standard / vivid / natural / portrait / landscape / monochrome / custom`. Brand profiles map their native names (Photo Style / Picture Control / Film Simulation / etc.) to these. |
| `white_balance` | enum | `in` | `auto / daylight / cloudy / shade / tungsten / fluorescent / flash / custom`. |
| `image_format` | enum | `eq`, `in` | `raw / jpeg / raw+jpeg`. |
| `stabilization` | enum | `in` | `off / on / boost / panning`. |
| `lens.primary_scenario` | string | `eq` | From lens registry. |
| `lens.fallback_scenarios` | string list | `in` | From lens registry. |
| `body.brand_id` | string | `eq` | Rare — most rules should not depend on brand. |
| `body.crop_factor` | float | `gte`, `lte` | Used for sensor-size-aware rules. |

### Reference-card content (the J8-facing payload)

**`reference_card`** *(object, optional but recommended)* — content for the per-scenario printable + installable card. Not consumed by the classifier.

- **`physical_setup`** *(object)*. Free-form keyed strings (lens, tripod, accessories list, filter, flash) — null where not applicable.
- **`software_settings`** *(object)*. Free-form keyed strings (mode, aperture, iso, shutter, focus_mode, af_area, drive_mode, color_preset, white_balance, format). Used for the "back of the card" reference text.
- **`field_adjustments`** *(string array)*. Short list of "what to tweak in the field" tips.
- **`rationale`** *(string)*. The "why this scenario" paragraph.
- **`common_mistakes`** *(string array)*. Short list of pitfalls.

All free-text reference-card content gets localized when the scenario is generated in non-English locales (the wizard's answers are localized; the generated text matches).

### Optional metadata

- **`custom_mode_slot`** *(string, optional)*. The user's chosen camera custom-mode slot reference (e.g., "C3-1", "U1", "C2"). **Informational only** — *not* a classification key. Used by the reference card to remind the user which slot to switch to.
- **`confidence_baseline`** *(float, 0.0–1.0, optional)*. The classifier's confidence floor when this scenario matches — adjusted up or down by how many EXIF expectations matched. Default 0.85.
- **`tags`** *(string array, optional)*. Free-form tags used by the user for personal organization. Not consumed by the classifier.

---

## Cross-walk: wizard questions → schema fields → §11 rule operators

This table proves that every wizard question (from `docs/04`) generates a schema field that can be expressed as a refinement-rules `when` clause. The cross-walk validates the wizard-to-classifier pipeline.

| Wizard question | Schema field(s) | §11 `when` form | Notes |
|---|---|---|---|
| "Lenses used for X?" | `lens_models` | `{ "lens_models": { "in": [...] } }` | Multi-select → array `in`. |
| "Focal-length range?" | `focal_35mm` | `{ "focal_35mm": { "gte": 200, "lte": 800 } }` | Range → `gte` + `lte`. |
| "AF mode?" | `focus_mode` | `{ "focus_mode": { "eq": "continuous" } }` or `{ "in": [...] }` | Single → `eq`. Mixed → `in`. |
| "AF area mode?" | `af_area_mode` | `{ "in": ["tracking", "subject_detection"] }` | Multi-select → `in`. |
| "Aperture range?" | `aperture` | `{ "gte": 5.6, "lte": 11 }` | Range. |
| "ISO range?" | `iso` (and/or `iso_relative_to_body`) | `{ "gte": 200, "lte": 6400 }` | Range. |
| "Shutter speed?" | `shutter_speed` | `{ "gte": 0.0005, "lte": 0.005 }` | Range in seconds (smaller number = faster shutter). |
| "Drive mode?" | `drive_mode` | `{ "in": ["burst_low", "burst_high"] }` | Multi-select → `in`. |
| "Color preset (Photo Style / Film Sim / etc.)?" | `color_preset` | `{ "eq": "standard" }` or `{ "in": [...] }` | After brand normalization. |
| "White balance?" | `white_balance` | `{ "in": [...] }` | |
| "Image format?" | `image_format` | `{ "eq": "raw" }` or `{ "in": [...] }` | |
| "Flash usage?" | `flash_fired` | `{ "eq": true }` (or `false`, or skip field for "either") | |
| "Subject detection (when available)?" | `subject_detection` | `{ "in": ["animal", "bird"] }` | Requires body capability — see below. |
| "Stabilization usage?" | `stabilization` (in `_optional`) | `{ "in": ["on", "boost"] }` | Boost-confidence-only. |
| "Tripod usage?" | (not directly EXIF — informational only) | — | Stored in reference card; classifier infers weakly from shutter-speed distribution. |
| "Indoor vs outdoor?" | (not directly EXIF — informational) | — | Stored in reference card. |
| "Sub-types (Milky Way, moon, etc.)?" | `tags` | (not in `when`; used for tag emission in `then`) | Becomes `ClassificationResult.tag`. |

**Conclusion of the cross-walk:** every wizard question maps cleanly to either a `when` predicate using v2_design.md §11's existing operator vocabulary (`eq, ne, gt, gte, lt, lte, in, not_in, exists`) OR to reference-card content that bypasses the classifier. **No extension to the §11 engine is required by v1's wizard.**

One small enhancement is worth noting (already flagged in the schema above):

- **`exif_expectations_optional`** is a small *additive* concept the new project introduces — fields that boost confidence when matched but are not required. §11 currently has all conditions as required (AND). The optional bucket would be a per-scenario "soft AND" — only relevant for tie-breaking between candidate scenarios. Phase 3 decides whether this is worth the engine extension or whether it lives in scenario-side post-processing (e.g., "for each candidate scenario, sum confidence boosts from matched optional fields").

## Wizard → rule transformation

When the wizard generates a scenario, the transformation to a refinement-rule entry is direct and mechanical:

```python
# Pseudo-code; actual implementation in Phase 4.

def scenario_to_rules(scenario: dict) -> list[dict]:
    """Transform a wizard-generated scenario into one or more refinement rules."""
    base_rule = {
        "id": f"{scenario['id']}-rule",
        "description": f"Auto-generated from wizard: {scenario['name']}",
        "when": scenario["exif_expectations"].copy(),
        "then": {
            "scenario": scenario["genre"],
            "confidence": scenario.get("confidence_baseline", 0.85),
            "reason": f"Matched wizard-defined scenario '{scenario['name']}'",
            "scenario_id": scenario["id"],
        },
    }
    
    # Required-capability inference
    if "subject_detection" in scenario["exif_expectations"]:
        base_rule["requires_capability"] = ["subject_detection"]
    
    return [base_rule]
```

Rules are written to `%LOCALAPPDATA%/{AppName}/refinement_rules.json` and re-read on each import batch (per §11.9).

## Storage

- **Built-in defaults** live under `assets/scenarios/` — one JSON file per scenario per locale. Shipped read-only. v1 ships small starter sets per genre (the "broad defaults" the wizard generates when all questions are skipped).
- **User-generated scenarios** live under `%LOCALAPPDATA%/{AppName}/scenarios/` — one JSON file per scenario, atomically written. The user can hand-edit these.
- **Generated refinement rules** live under `%LOCALAPPDATA%/{AppName}/refinement_rules.json` — derived from the scenarios at wizard-completion time. Re-derivable if lost.

If the same scenario `id` exists in both built-in and user locations, **user wins**.

## What gets re-decided in Phase 3

- Whether `exif_expectations_optional` extends the engine or lives in post-processing.
- Whether scenarios store generated reference-card content in their own JSON, or render the cards from a template at card-generation time.
- Whether brand-specific terminology in the `reference_card.software_settings` block is stored normalized (with localization at render time) or brand-localized at scenario-creation time.
- The exact `id` generation scheme (slug + suffix for uniqueness vs. UUID vs. user-chosen).

## What's deliberately *not* in the schema for v1

- **Per-event scenario overrides** — a user might want "for this specific trip, the wildlife scenario adds f/4 lower aperture bound." Out of v1; out of scope until a real use-case surfaces.
- **Scenario inheritance / composition** — "Macro on a tripod is Macro with extra constraints." YAGNI in v1; collapse into separate scenarios if needed.
- **Auto-refinement from observed photos** — "as the user culls, learn that their wildlife photos actually go to ISO 12800." Powerful but complex; v2+ feature.
- **Cross-scenario tie-breaking rules** — if two scenarios match equally well, which wins? §11.5 says "first match wins" by rule order; v1 inherits that. v2+ might introduce explicit priority or weighted scoring.
