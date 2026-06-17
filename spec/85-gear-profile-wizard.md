# spec/85 — The gear-profile wizard (which cameras/lenses I use + their genres)

**Status:** design draft, 2026-06-17. **Merged with spec/83 into one build**
(Nelson 2026-06-17) — they share the camera/lens inventory layer, the gear
flags make spec/83's main-vs-occasional split correct, and the wizard launches
from the DC dialog, so they ship as a single agent task (one combined brief).
A small second wizard that lets the user tag
their **camera/lens inventory**: which gear they *currently use* (vs. the long
tail of old / borrowed / guest-photographer cameras) and, optionally, a
*preferred genre* per camera or lens. It pays off in two existing systems at
once — the collection filters (spec/83) and classification (spec/58).

Read with: `spec/58` (classification pass + rules chain + wizard), `spec/83`
(the facet picker's main-vs-occasional split), `spec/32` (cross-event
inventories), `core/classifier_v2.py` (the existing lens→genre fallback).

---

## 1. Why this helps two things at once
- **Filtering (spec/83).** The facet picker splits "your" cameras from one-offs
  using a **photo-count heuristic** (< ~10 photos = occasional). That misclassifies
  a borrowed camera that shot 300 frames on one trip. A user **"I use this"
  flag** is a far better signal: the active set leads the list / is the default;
  everything else is the collapsed "occasional" group. Gear flag **beats** the
  count heuristic; count stays the fallback when nothing is tagged.
- **Classification (spec/58).** The rules chain is already *deterministic tier >
  user scenarios > lens fallback*, and `classifier_v2` already falls back to a
  lens's `potential_scenarios`. A user-declared **camera/lens → preferred genre**
  is a confirmed, stronger version of that fallback — e.g. frames from "my macro
  lens" pre-classify as macro with higher confidence. It slots in as a new
  **"user gear hint"** tier, just above the generic lens-registry fallback.

So one small bit of user input improves both the lists they browse and the
auto-classification they rely on.

## 2. Where it lives
- **Not first-run.** It needs data (cameras/lenses already in the library), so it
  can't run before the first import. The first-run wizard (genre prefs + scenario
  bootstrap) is unchanged.
- **Launched on demand**, primarily from the **DC creation dialog** ("Manage my
  gear…") — exactly where the long lists hurt — and also from **Settings**. Same
  wizard, two entry points.

## 3. What it shows
The cross-event camera + lens inventory (the same `global_items` distinct
values + counts spec/83's picker already needs), as two short review lists:

- **Cameras** — each row: name · photo count · **[ I use this ]** toggle ·
  optional **preferred genre(s)** (multi-select over the wizard's genre set;
  "none" is valid).
- **Lenses** — same shape.

**Pre-filled, not blank:** seed "I use this" on the high-count gear so the user
mostly confirms rather than fills. The long tail starts off. Two minutes of
review, not data entry.

## 4. Storage
A small user-level table in `mira.db` (cross-event by nature — a camera spans
events; same home as `saved_filter` / `global_items`, spec/32):

```sql
CREATE TABLE gear_profile (
  kind             TEXT NOT NULL,   -- 'camera' | 'lens'
  key              TEXT NOT NULL,   -- camera_id | lens_model
  is_active        INTEGER NOT NULL DEFAULT 0,   -- "I currently use this"
  preferred_genres TEXT,            -- JSON array of genre keys, nullable
  updated_at       TEXT NOT NULL,
  PRIMARY KEY (kind, key)
);
```

User-level, not per-event: the profile is about *the photographer's kit*, not
one event. Offline-first, atomic writes as ever.

## 5. How the two consumers read it
- **spec/83 facet picker** — when building the camera/lens list, partition by
  `is_active` first (active = main list, default-shown; inactive = collapsed
  "occasional"), falling back to the count heuristic for untagged gear. The
  preferred-genre tags could later seed quick presets ("my macro kit").
- **spec/58 classifier** — add the **user-gear-hint tier** to the merged ruleset:
  if an item's camera or lens has a `preferred_genres` entry and no
  higher-priority rule matched, classify to it (confidence above the generic
  unknown-lens fallback, below explicit user scenarios). Respect spec/58 §3:
  only re-classifies **untouched** items; `classification_source='user'` is never
  overwritten. A gear-profile change is a `classification_rules_version` bump,
  same as a wizard re-run.

## 6. Open questions
- **Genre tier placement** — user gear hint above or below the user *scenarios*
  derived from dropped photos? (Lean: scenarios are more specific → gear hint
  just below them, above the generic lens fallback.)
- **Camera vs lens conflict** — if the camera says "wildlife" but the lens says
  "macro", which wins? (Lean: lens, it's the more specific optic; confirm.)
- **Auto-suggest active gear** — pre-tick by recent use (gear seen in the last N
  months / events) rather than raw lifetime count?
- **Scope** — park for after the three committed briefs (82/83/84), or pull in?
  No hard blocker either way (see §7).

## 7. Dependency note — soft, not hard
The inventory it needs is just a query over `global_items` (distinct cameras /
lenses + counts). spec/83 *formalizes* that query, but this wizard **does not
have to wait for it**: on launch it can **run the query itself** behind a short
"gathering your gear…" wait and build its lists from that. So:
- If spec/83 has already landed → **reuse** its inventory query (no duplication).
- If not → the wizard **computes the inventory on demand** at launch.

Either path works; this is buildable standalone whenever Nelson wants it, and
simply gets tidier (shared query) once spec/83 is in.
