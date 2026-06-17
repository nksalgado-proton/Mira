# Build brief — collection filters (two-tier + picker) + gear-profile wizard

**For:** a full-access coding agent. **Authored:** 2026-06-17.
**Governing specs:** [`spec/83`](spec/83-facet-picker-audit.md) (opt-in two-tier
filters + high-cardinality picker) and [`spec/85`](spec/85-gear-profile-wizard.md)
(the gear-profile wizard) — **read both in full.** They are **one task**: shared
camera/lens inventory layer, the gear "I use this" flag makes the picker's
main-vs-occasional split correct, and the wizard launches from the DC dialog.
Also: `spec/81 §2.1` (cross-event surface), `spec/32 §2` (facet catalogue),
`spec/58` (classification rules chain), `spec/05` (UI grammar).

---

## Current state (audit before you build)

- **Dialog:** `mira/ui/pages/new_cross_event_dc_dialog.py`. Today it shows **all
  ~15 facets at once**; `_MultiSelectFacet` lays options in a single
  `QHBoxLayout` → the endless-row bug for camera/lens/city/country. This dialog
  gets rebuilt around the two-tier model.
- **Host wiring:** `mira/ui/pages/cross_event_dcs_dialog.py` — `_build_inventories`
  (line ~427) builds `CrossEventInventories`; `dc_probe` is `self._lg.dc_probe`.
- **Data:** the six `available_*` methods in `mira/gateway/library_gateway.py`
  return `SELECT DISTINCT <col> … ORDER BY <col>` — **no counts**.
- **Reusable:** `FlowLayout` already exists at `mira/ui/base/flow_layout.py`.
  Classifier ruleset assembly is `core/classifier_v2.py` (`classify`,
  `_parse_ruleset`, the user-scenario load path ~line 593). User-store schema +
  migrations live in `mira/store/schema.py` (the backup work just added a
  user-store migration — same pattern for the new table).
- **New (does not exist):** the `gear_profile` table, the two-tier shell, the
  `FacetPickerDialog`, the gear wizard, the gear-hint classifier tier.

---

## Hard rules

- `core/` stays Qt-free (the picker/wizard live in `mira/ui/`; the classifier
  tier + inventory queries are core/gateway).
- No network; no hardcoded paths; atomic write-then-rename; `tr()` every string.
- **View-over-engine:** reuse `FlowLayout`, the existing facet widgets, and the
  classifier's ruleset loader — don't rewrite them.
- Run `verify.bat` after **each** slice; commit per slice.

---

## Slices (in order, each its own commit)

### SLICE 1 — Inventory counts (shared data layer)
Convert the six `available_*` queries in `library_gateway.py` from
`DISTINCT … ORDER BY name` to **`<col>, COUNT(*) … GROUP BY <col> ORDER BY
COUNT(*) DESC`**, returning `(value, count)` pairs. Update `CrossEventInventories`
+ `_build_inventories` to carry counts and to **fetch on demand** (per filter
added), not all up front. **Verify** whether `global_items.camera_id` is a
human-readable model string; if opaque, the picker/wizard must show a display
label, not the raw id. Tests.

### SLICE 2 — `gear_profile` table (shared data layer)
New **user-store** table (cross-event; same home as `saved_filter`):
`gear_profile(kind TEXT, key TEXT, is_active INT DEFAULT 0, preferred_genres
TEXT, updated_at TEXT, PRIMARY KEY(kind,key))` — `kind ∈ {camera, lens}`. Add a
schema migration (mirror the backup slice-8 user-store migration) + a small
repo: `get_gear_profile()`, `set_gear_active(kind, key, bool)`,
`set_gear_genres(kind, key, list)`. Tests.

### SLICE 3 — Two-tier dialog shell (spec/83 §2)
Rebuild `new_cross_event_dc_dialog.py`: opens with **name + origin + live count
+ “+ Add filter”** and **nothing else**. Add-filter opens a **grouped** menu
(Curatorial / Camera & lens / Settings / When & where, per spec/32 §2). Picking
a dimension adds an **active-filter row** (using the existing facet widgets as
editors for now) with an ✕ to remove. Empty state reads *“No filters — matches
everything in #exported (N items).”* Inventory for a facet is fetched only when
its filter is added (slice 1). Rehydrate/edit-flow + `dc_probe` count still work.

### SLICE 4 — Adaptive inline editor (spec/83 §3)
Multi-select facet picks its editor by option count (threshold **12**, a module
constant): **≤ threshold →** inline **`FlowLayout`** (wraps, never overflows);
**> threshold →** summary line + **Choose…** button (slice 5). Tests.

### SLICE 5 — `FacetPickerDialog` (spec/83 §4, reads gear flags)
A small per-facet dialog: **search**, **value + photo count**, **most-used-first**,
**Select all / Clear**, and a **collapsed “Occasional (N)” section**. The
main-vs-occasional split reads **`gear_profile.is_active` first** (active = main
list; inactive = occasional), **falling back to the count heuristic
(< 10 photos)** for untagged gear (spec/85 §5). Wire camera/lens/city/country to
it. Tests.

### SLICE 6 — Gear-profile wizard (spec/85 §3)
A second wizard (reuse the `mira/ui/wizard/` pattern), launched from the **DC
dialog (“Manage my gear…”)** and **Settings**. On launch it runs the slice-1
inventory query behind a short “gathering your gear…” wait, then shows two
review lists (**Cameras**, **Lenses**), each row: name · count · **[ I use this ]**
toggle · optional **preferred genre(s)** multi-select. Pre-tick high-count gear.
Writes via the slice-2 repo. Tests.

### SLICE 7 — Classifier gear-hint tier (spec/85 §5, spec/58 §3)
Add a **user-gear-hint tier** to the merged ruleset in `classifier_v2`: if an
item's camera or lens has `preferred_genres` and no higher-priority rule matched,
classify to it (confidence above the generic unknown-lens fallback, below
explicit user scenarios). A gear-profile change **bumps
`classification_rules_version`** (re-classifies **untouched** items only;
`classification_source='user'` is never overwritten — spec/58 §3). Resolve the
lens-vs-camera conflict (lean: lens wins) and the tier-placement question
(spec/85 §6). Tests.

### SLICE 8 — Family consistency (spec/83 §2 decision)
Apply the Add-filter shell to the **event-scope** and **cross-event Cut**
dialogs via shared components; the **event-scope dialog keeps its deliberately
thin facet set** (spec/81 §2.1: `#exported` + Style + media type). Tests.

---

## Per-slice loop
`verify.bat` after each slice; launch + eyeball the dialog (add/remove filters,
open the picker on a high-cardinality facet, run the gear wizard, confirm a
gear-flagged camera lands in the main list). Commit per slice.

## Definition of done
`verify.bat` green; the DC dialog opens empty (default = no filters) and grows
via Add-filter; high-cardinality facets open the picker with counts + gear-driven
occasional split; the gear wizard sets `is_active`/genres and those flags drive
both the picker and the classifier; event-scope stays thin; everything
local-only + atomic + `tr()`'d.

## Open questions (raise to Nelson — don't guess)
- **Inline-vs-picker threshold** (12) and **occasional cutoff** (< 10) — module
  constants for v1; confirm.
- **Country display** — full names vs ISO codes (store code, show name — lean).
- **Gear-hint tier placement** + **camera-vs-lens conflict** (spec/85 §6).
- **Auto-suggest active gear** by recent use vs lifetime count (spec/85 §6).
