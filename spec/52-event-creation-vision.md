# spec/52 — Event Creation (Unified)

**Status:** Vision (approved 2026-06-08, Nelson). Supersedes the event-creation
portions of [spec/12-new-event](12-new-event.md) and [spec/13-capture](13-capture.md).
Captures the locked design from the 2026-06-08 design session.

> **⚠ Amended by [spec/57](57-folders-and-roundtrip.md) (2026-06-10, locked):**
> spec/57 governs wherever the two disagree. The deltas: the plan is now also a
> **product of Collecting** (an event may start empty; each Collect run creates
> its day(s), with multi-date auto-split + confirm and per-day manual metadata
> when no phone is present); a late phone Collect **reconciles** against the
> day-built plan (prompting only on mismatch); the plan editor gains a
> **post-ingest single-day TZ unlock**; the create-from-files path becomes the
> **backfill wizard** with three landing levels (ready-at-Pick / -Edit /
> -Share, from-Edited = one folder treated as both); and the on-disk folder
> names are fixed-English `Original Media` / `Edited Media` / `Cuts` /
> `Picked Media` (numbered prefixes retired).

> **⚠ Amended by [spec/64](64-event-header-and-days-table.md) (2026-06-13,
> locked):** spec/64 governs wherever the two disagree. The deltas: the
> single unified dialog splits into two — **Event Header** (identity, the
> §14 qualifier set) and **Event Days Table** (the per-day schedule); the
> three §14 qualifiers Scope / Mood / Transport retire and are replaced by
> **Context** / **Experience Type** / **Creative Focus** (the latter
> multi-select); the per-unit Duration cap is removed (X is a free integer
> > 0); the silent home-country / TZ autofill on phone-without-GPS days is
> replaced by a per-location-group prompt; and Cancel during
> create-from-media rolls back the whole flow rather than leaving an
> orphan. §14's qualifier table is **STALE** below; spec/64 §3 + §6 carry
> the live model.

This document is durable. It governs every UI/code decision in the event-creation
flow and feeds future user documentation.

---

## 0. Why

Today's event creation isn't a consistent experience. It carries vestiges of:

- An older model with a separate **Plan phase** where events were created before any
  photos existed (`new_event_page.py` — the empty-event-then-add-photos path).
- The pre-cell-phone-EXIF era, when location / TZ / country had to be filled manually
  because no automated source was available (the per-camera classification surface
  in `past_photos_cameras.py`, the multi-path `capture_action_dialog.py` "how do you
  want to ingest?" picker, etc.).

The redesign collapses all of this into **one creation path that starts from the
photos** — the photos are the source of truth, the system extracts everything it can
from EXIF, and the user fills in only what's actually missing.

---

## 1. North-star principles

These govern every decision in the flow:

1. **One entry point.** Event creation always begins by pointing at a source of
   photos (an SD card or a directory). There is no other path.
2. **The phone is ground truth.** When phone photos are present in a scan, their
   EXIF provides authoritative country / TZ / location / description. Manual entry
   is the fallback, never the front door.
3. **One mental model for create AND incremental ingest.** The same flow that
   creates an event also adds photos to an existing one. No second surface for
   "later additions".
4. **TZ correction is correction-on-read.** Captured EXIF is never mutated. Per-
   `(camera, day)` offset records project corrected times at read time.
5. **Everything is per-day.** No event-level country / TZ / location fields. Per-
   day data is authoritative; event-level chrome (dashboard flag emoji, etc.) is
   derived at render.
6. **Defer is the exception.** Plans can be partial at create time, but Pick is
   gated on plan-complete.

---

## 2. The flow

### 2.0 Two creation entries (Nelson 2026-06-08 second thought)

The original §11 collapse of "New event" + "Create from photos" to one
menu entry was wrong. They're two distinct workflows the user picks
between deliberately:

**File → New event** — *plan-first* (no photos yet). The user is
planning a future trip / starting a session / blocking time on a
project. Opens the **event info dialog only** (Name / Type / Subtype /
Description / Duration / Scope / Mood / Transport / Participants — the
§14 structured qualifiers). OK creates the event row with **zero
trip_days**. The user lands on the new event's activity dashboard,
empty. The plan grows as the user adds photos via **Collect** (which
re-enters the source-pick → scan flow against the now-existing event).

**File → New event from photos** — *photos-first*. The user has photos
on disk or on a card and wants to organise them into an event. Opens
the source-pick → scan → unified info+plan dialog (§§2.1-2.4). OK
materialises the event with the first batch's trip_days baked in.

Both paths arrive at the same place — an event with metadata and a
(possibly empty) day plan. The difference is whether the plan is
seeded at create time or grown later.

### 2.1 Source pick

The user starts event creation (or re-enters Collect on an existing event). The
first surface asks for a **source**: an SD card or a directory anywhere on disk.

A source is **one source per pass**. The source's nature determines whether it
carries one camera or many:

- **SD card** → naturally one camera. Trip-incremental flow = one card per pass,
  repeated nightly.
- **Directory** (past-event ingest, "upload everything at once") → naturally many
  cameras. The scan discovers all cameras within the source.

Multi-camera events are built up via repeated single-source passes. The phone
typically arrives as its own source on a later pass — the override-ask handles
the consistency check naturally (§6.2).

### 2.2 Day list

The code scans the source and derives a **list of days**. Days come from
per-photo EXIF DateTimeOriginal, grouped by **corrected-time-at-read** (§8.5).

The day list is the working surface for the rest of the creation flow. Each
day-row holds:

| Cell | What |
|---|---|
| Checkbox | Include this day in the import (yes/no) |
| Browse | Quick peek at a subset of that day's photos (§5.6) |
| Country | Per-day country (autofilled or manual) |
| TZ | Per-day timezone (autofilled or manual) |
| Location | User-facing location string (autofilled or manual) |
| Description | Free-text narrative for the day (optional — §4) |
| Override marker | Inline badge when an incremental ingest brought new phone data that differs from the existing per-day values (§6.2) |

### 2.3 Plan dialog

All day-list interaction happens in **one dialog**. It replaces today's two-tab
plan dialog (the tabs retire — there's enough horizontal + vertical room for
everything on one surface, because the Share redesign retired tags, people, and
the long observation field per spec/51).

See §5 for dialog-shape details.

### 2.4 Event info — same dialog as the plan, presented once

**Amended 2026-06-08, Nelson eyeball:** the §2.3 plan dialog and the
event-info collection are ONE surface presented at once, not two dialogs
in sequence. The dialog has an "Event info" section at the top + the
per-day plan table below. OK on this single dialog is the spec/52 §2.4
commit point.

The event info section captures (see §14 for the storage shape):

- Event **name** *
- Event **type** * (closed enum: trip / session / occasion / project /
  unclassified)
- Event **subtype** * (curated activity-only presets per type — Trip:
  City / Beach / Nature / Adventure / Wildlife / Cultural / Road; etc.
  The combo is **editable** so a user can type a custom subtype not in
  the presets.)
- Event **description** (optional short paragraph)
- **Duration** (value + unit — hours / days / weeks / months / years,
  with per-unit caps)
- **Scope** (international / domestic / null)
- **Participants** (multi-select chips — Solo / Couple / With Family /
  With Kids / With Friends / With Colleagues / Client)
- **Mood / vibe** (relaxed / active / cultural / professional / null)
- **Transport** (Trip-only — flight / car / train / cruise / motorhome /
  mixed / null; hidden on non-Trip types)

The event is created when the unified dialog confirms (a single OK).
**Abandoning at any stage is a clean no-op** — no orphaned event records
exist before that OK.

**Photo ingest is not part of this dialog.** OK creates the event row + the
trip-day plan only; the user lands on the new event's activity dashboard
with zero items. Photos are added on a second pass through the **Collect**
entry (which re-enters the same source-pick → scan → plan flow against the
already-created event).

There are NO event-level country / TZ / location fields. Anywhere chrome needs
"the event country" (events dashboard flag emoji, filters, etc.) it's derived
from the per-day data at render time (majority country, or first-day for
tie-break). The `event_index` table in `mira.db` (per [spec/53](53-user-data-store.md))
caches the projection for cold-list rendering, invalidated whenever day data
changes.

---

## 3. Autofill

### 3.1 Phone-EXIF autofill

When the scan finds **phone photos** for a day (see §9 for the detection rule),
each per-day field is autofilled from the phone's EXIF:

| Field | Source |
|---|---|
| Country | Reverse-geocode of GPSLatitude / GPSLongitude (ISO 3166-1 alpha-2 also stored in `trip_day.extras_json.country_code`) |
| TZ | EXIF `OffsetTimeOriginal` |
| Location | Reverse-geocode of GPS to a human-readable string ("Sintra, Portugal") |
| Description | Initially populated equal to location text, user-editable (§4) |

Autofill is **never silent** for a re-scan (§6.2 — override-ask). On the first
scan of a fresh source where the user hasn't yet filled anything, autofill
populates the fields without ceremony.

### 3.2 Subdir-name autofill (past-event scenario)

When the user points at a **directory** organized into per-day subdirectories
(common in past-event ingest — "Day 1 - Lisbon", "2024-07-12 Sintra hike", etc.),
the subdirectory name autofills the **description** field for that day.

Detection threshold per day: **strict — all of a day's photos must come from a
single subdirectory**. Mixed cases (a day's photos spread across multiple
subdirs, or split with a stray DCIM file in a different folder) skip the
autofill. Rationale: strict detection means we never guess wrong from noisy
structure.

Source scope: any source (in practice dir-only, since SD cards rarely have this
structure).

### 3.3 Conflict resolution

When multiple autofill sources apply to the same field:

- **Subdir name beats phone-derived default description.** Subdir is a deliberate
  user signal (he organized the dir for a reason); phone-location is automation.
  Both stay editable.
- **Phone EXIF location beats absence-of-data.** No conflict because there's only
  one source for country / TZ / GPS-derived location.
- **Verbatim copy.** Subdir names like `"2024-07-12 Sintra"` are copied verbatim
  into description — no date-prefix stripping (the user can clean up in two
  seconds; heuristic stripping risks chewing useful tokens).

---

## 4. Description as optional

The **description** field is **never required**. Autofill still happens (location-
text default + subdir-name override + phone-arrives-late propagate-if-untouched
heuristic), but the Pick gate (§10) does not require it.

Rationale:

- Description is narrative, not structured data. Country / TZ / location drive
  correction-on-read, dashboard chrome, the calibration trigger; description
  doesn't gate any phase.
- Required-but-trivial-typing produces "x" and "Day 1" entries — worse than
  empty. Optional invites either a real narrative or nothing.
- Autofill still populates the field for most users (location text + subdir
  name), so it's typically not empty by the time the user looks at it.
- Matches the Share-phase precedent (long observation field retired per
  spec/51).

Behavior on a subsequent ingest with phone override accepted: if `description ==
location` text verbatim (a cheap "untouched" signal), description follows along
to the new location value. If description has been edited away from location,
it stays — user owns it.

The location field is also editable. Phone-derived GPS labels can be wrong
(mall-mapped to wrong street; rural areas with no reverse-geocode hit); user
overrides.

---

## 5. Plan dialog

### 5.1 Single surface, no tabs

The two-tab chrome of today's plan dialog retires. All per-day data fits on one
scrollable surface. This is possible because the Share-phase redesign retires
tags / people / long-observation fields (spec/51), freeing vertical space.

### 5.2 Scrollable, 14 rows visible

The day-list region is vertical-scroll only with 14 day-rows visible at a time.
Surrounding chrome (header, footer with save/load + OK/Cancel) stays pinned.

### 5.3 Per-row columns

Per row, in display order: checkbox · Browse · country · TZ · location ·
description (optional) · override marker (when applicable). Standard control
heights (~36px row).

### 5.4 Sizing

**Width:** ~1280-1380px. Fits all columns at usable widths without truncation
games. Sized for 1920×1080 @ 125% Windows scaling per spec/05 with margin.

**Height:** ~700-800px (14 day-rows × ~36px + header/footer chrome).

Laptops at 100% scaling or sub-1280px displays will see horizontal scroll —
acceptable trade-off; MC is a desktop tool with a 1920 floor per spec/05.

### 5.5 Save / Load CSV

Two buttons on the dialog footer: **Save plan to file…** / **Load plan from
file…**.

- Format: `;`-separated CSV (semicolon, not comma — preserves regional locale
  decimal handling).
- Columns: `date;country;tz;location;description`.
- Round-trip key: `date`. Import matches each CSV row to the scan day with the
  same date; overwrites that day's four fields. CSV rows whose date isn't in
  the scan are ignored with a notice. Scan days with no matching CSV row are
  left as-is (partial CSV loads are non-destructive to days the user didn't
  include in the file).
- Use case: escape hatch for filling many per-day rows offline (Excel, etc.).
  Most users never touch it.

The "checked for import" state is scan-level, not plan content — stays out of
the CSV. Event-level fields (name / type / subtype) live in the event info
dialog, not in the plan CSV.

### 5.6 Browse peek

Clicking the Browse button on a day-row opens a **read-only peek dialog** showing
a sample of that day's photos so the user can decide whether to check the import
box.

- **Target ~20 photos per day.** Not exhaustive.
- **Time-spread**: pick photos across the day's time range so the peek shows an
  arc (morning / midday / evening), not 20 photos clustered at breakfast.
- **Skip videos** (heavy to preview) and huge files (slow to decode).
- **Thumbnail source preference**: JPEG when available. If only RAW, decode the
  embedded JPEG preview (RAW formats carry one for the camera screen — fast,
  milliseconds).
- **RAW + JPEG sibling pairs**: dedup by stem; one slot per moment.
- **<20 photos in the filtered set**: show all available; don't pad.
- **Day with ONLY videos or unloadable files**: empty peek with a short label
  ("(no preview-able photos — N videos, M RAWs)"). Don't fall back to video frame
  extraction — fast is the point.

UI: modal popover, ~6×4 thumbnail grid, click a thumbnail to zoom to full-size,
Esc to close. No selection action wired — pure peek. Reuses the same thumbnail-
grid widget as the pair-pick TZ-calibration flow (§8.3).

---

## 6. Incremental ingest + override-ask

### 6.1 Same flow re-entered

After the initial event creation, the user can come back each end-of-day (during
a trip) or whenever (past-event additions), open the existing event, click
**Collect**. The same source-pick + day-list flow re-runs.

The card or directory may contain days that have already been imported. Those
days are shown in the day list as usual — the user picks which to import (or
re-import additions to). For days that already exist in the plan AND have new
photos in the source, the new content is added to the existing day.

### 6.2 Override-ask UI

When a re-scan brings **new phone photos for a day that already has plan data
(country / TZ / location set by a prior ingest or manual entry)**, the system
**asks** the user before overriding.

The override marker is an inline badge at the start of the affected day's row.
Click → open the override-decide UI showing the existing values side-by-side
with the phone-EXIF-derived new values:

```
                Existing            New (from phone EXIF)
Country         Portugal            Spain
TZ              +00:00              +01:00
Location        Lisbon, Portugal    Madrid, Spain
Description     (kept; see §4)      (kept; see §4)
                [Keep existing]     [Override with new]
```

The user picks per-row (or accepts all). Description follows the propagate-if-
untouched heuristic (§4).

### 6.3 Side-by-side comparison

Always show old + new explicitly. Never silently overwrite. Never auto-decide
based on majority or recency.

---

## 7. Late phone ingest

A common pattern: cameras get ingested daily during a trip (one SD card per
night), but the phone photos arrive in **one pass after the trip ends**. The
phone ingest happens against an event that's already been built up entirely
from camera data.

This is just §6 with N existing days and one new source. The flow handles it
without special-casing:

- Phone source pointed at, scan runs.
- Day list shows all days the phone has photos for (overlapping with existing
  event days).
- For days where the phone contributes new data that differs from existing
  per-day values, the override marker appears.
- User accepts overrides per day (typically all — the phone is authoritative
  for the trip he just took).

---

## 8. TZ correction

### 8.1 Mechanism — correction-on-read

The captured tree is **never mutated** after ingest (CLAUDE.md invariant #7).
TZ correction is purely a projection at read time.

The data layer is `camera_day_tz` — a per-`(camera, day)` declared TZ record
(already exists in the schema). The fallback chain when computing a photo's
corrected time:

```
camera_day_tz row (if exists)
  → camera.applied_offset_minutes (if set)
  → 0 (raw EXIF time, no correction)
```

When the user accepts a phone override (§6.2), the system writes/updates the
`camera_day_tz` row for that day. The corrected-time projection picks up the new
value on the next render — no re-bake, no file mutation.

Cached projections that depend on corrected time (per-day thumbnail cache, time-
sorted indexes, events_index cached fields) invalidate when an offset changes.
Known surface area; not a new problem.

### 8.2 Conditional ask trigger

TZ calibration **is not automatic**. It is offered when both conditions hold:

1. A day's location-derived TZ ≠ the user's home TZ (captured by the wizard,
   stored in user settings — see [spec/53](53-user-data-store.md)).
2. Camera photos (non-phone) are present on that day.

If only phones contributed, no calibration is needed — phones carry their own
TZ in EXIF (`OffsetTimeOriginal`).

When both conditions hold, the system asks: "Calibrate camera TZ now, or skip?"
The user opts in or defers.

### 8.3 Calibration paths

If the user calibrates, a per-camera dialog appears (phones not shown — they
don't need calibration). For each camera, two paths:

**Path A — known TZ.** The user types in the TZ the camera was set to on that
day. Direct entry.

**Path B — pair-pick.** The user picks one **camera photo** + one **phone photo**
of approximately the same moment. The system derives the offset from the time
difference. Reuses existing day-list + thumbnail-grid surfaces:

- Click "select cell phone photo" → opens a day-list of phone-photo-bearing days
  → click a day → grid of phone thumbnails with exact capture times → user
  picks one.
- Same for "select camera photo" → camera-photo-bearing days → grid → pick.
- After both photos picked, the existing TZ-derivation behaviour computes the
  offset and writes the `camera_day_tz` row.

If **no phone photos exist** on any day, Path A is the only option (pair-pick
needs both halves).

### 8.4 Per-day with different TZ

If the trip spans days in different TZs (e.g., crossed a border), the
calibration flow runs **per-day-needing-it**. Same camera may need different
offsets on different days.

### 8.5 Day-boundary derivation

Day grouping is derived from per-photo **corrected-time at read**, not frozen
at scan time. Same TZ-3 fallback chain (§8.1).

Pre-calibration (no offsets set yet): camera photos with wrong-TZ EXIF land in
approximately-right days (off by hours, sometimes pushing midnight photos to
the wrong side). Phone photos for the same day group correctly (their TZ is
known).

Post-calibration: the offset for `(camera, day)` is stored. Next render of the
day list re-groups using corrected times. Mis-grouped camera photos shift to
their correct day. Everything aligns with phone-derived day boundaries.

No re-scan needed. No frozen-at-scan state to invalidate.

**UX preservation on re-grouping:** when the day list re-renders post-calibration
and photos shift across day boundaries, preserve the user's per-day "checked for
import" decisions **by DATE** (the checked-date, in corrected-time). Photos that
moved across midnight inherit their new day's checked state. If a new date
appears (because some photos shifted into a previously-empty calendar day),
default it to **unchecked** — let the user opt in.

---

## 9. Phone detection rule

A photo is "a phone photo" when its EXIF `Make`/`Model` matches a **maintained
list of phone makers and model patterns** (Apple, Samsung, Google, Huawei,
Xiaomi, OnePlus, Sony Xperia by model name, etc.). The list lives as data (a
small config file the app can extend over time), not hardcoded.

**Why Make/Model specifically and not "has GPS":**

- Some modern cameras have built-in GPS too (Sony A7R V, some Nikon Z bodies).
  GPS-presence alone would mis-classify those as phones.
- Make/Model is the cleanest, most direct signal.

**WhatsApp / stripped-EXIF photos (the corner case):**

EXIF can be destroyed in transit (WhatsApp share, web download, screenshot). The
app cannot recover phone-detection from a stripped file. These photos are
degraded inputs:

- They can't trigger phone autofill (no Make/Model).
- They can't contribute GPS to location autofill.
- If `DateTimeOriginal` is missing too, they may not group into a day reliably
  (file mtime is unreliable — file copy resets it).

Handled honestly: degraded inputs land in whichever day their available
timestamp puts them, don't contribute phone signals, and the user edits/moves
them manually if needed. No heuristic guessing.

---

## 10. Pick gate

The user cannot enter the **Pick phase** for an event until its plan is
**complete**.

Plan-complete is defined as:

- Every **checked-for-import** day has `country`, `tz`, and `location` all
  non-empty.
- The event has `name`, `event_type`, and `event_subtype` set (the latter via
  the event info dialog).

Description is **not** required (§4).

**Affordance:** Pick action is **disabled** (greyed) on the event card and menu
until the gate is satisfied. A tooltip explains why: "Pick is locked until each
day has country, timezone and location."

No error dialog, no modal block — visible disabled state is the affordance. The
user knows what to fix because the plan dialog is one click away.

---

## 11. Retired surfaces and code

The following are retired by this design and removed from the codebase:

| What | Why |
|---|---|
| `mira/ui/pages/new_event_page.py` | No more pre-Plan path; events are born from photos. |
| `mira/ui/pages/capture_action_dialog.py` | One ingest path; no multi-path picker. |
| `mira/ui/pages/past_photos_cameras.py` (per-camera classification) | Phone auto-detected from EXIF (§9); no reference-camera concept; TZ mode is per-day not per-camera. |
| Two-tab chrome on the plan dialog | One scrollable surface now (§5.1). |
| Standalone "Plan phase" as a separately-navigable concept | Folded into the plan dialog opened at create + Collect. |
| `ENTRY_PLAN_TEMPLATE` menu entry ("Download plan template") | Save/load is two buttons on the plan dialog (§5.5); no menu-bar entry. |
| `core/trip_plan_parser.py` (parses today's plan-file format) | Replaced by `;`-CSV (§5.5). |
| Old TZ-calibration trigger model | Replaced by conditional ask (§8.2). |

**Amended 2026-06-08, Nelson eyeball:** the menu-bar collapse line that
previously sat here is **reverted**. The two creation entries are real
distinct workflows (see §2.0) — File → New event opens the info-only
dialog; File → New event from photos opens the source-pick + scan +
unified info+plan dialog. Keep both.

Schema-level retirements (the corresponding tables/columns dropped from
`event.db`) are documented in [spec/30-relational-schema-redesign](30-relational-schema-redesign.md)'s
spec/52 cleanup section.

---

## 12. What stays

Naming what we're NOT retiring, so future readers know:

- `mira/ui/pages/event_info_dialog.py` — opens after day triage; name +
  type/subtype.
- `mira/ui/pages/past_photos_dialog.py` — the SHELL of the past-photos
  creation flow. The new single-creation-path IS this surface, restructured.
- TZ-3 infrastructure (`mira/ui/pages/discrete_tz_dialog.py`,
  `core/tz_locations.py`, the per-`(camera, day)` offset records). The data
  layer that correction-on-read uses.
- Day-grid + bucket-navigator + pick-photo-surface — reused for pair-pick TZ
  calibration (§8.3) and the Browse peek thumbnail grid (§5.6).
- **Quick Sweep** — distinct concept (per-photo Pick decisions within a day,
  inside Collect phase). Stays.
- Wizard's home-TZ capture in user settings — feeds the new TZ-calibration
  trigger condition (§8.2).

---

## 14. Structured event qualifiers (Nelson 2026-06-08 schema lock)

> **⚠ STALE vs [spec/64](64-event-header-and-days-table.md) (2026-06-13):**
> the `scope` / `mood` / `transport` rows in the table below retire; the
> `duration_value` / `duration_unit` cap-per-unit retires (X is a free
> integer > 0; the unit selector stays). Replacements live on `event` as
> `context` (single-select enum), `experience_type` (single-select enum),
> and `creative_focus` (multi-select; JSON array). `event_subtype` and
> `participants` stay as described. spec/64 §3 + §6 carry the live model.

The legacy `event_subtype` field was a free-text bucket that quietly mixed
three orthogonal axes — duration ("One week"), scope ("International"), and
activity ("Roadtrip"). One filter dropdown couldn't sort them; the dashboard
couldn't say "show me all relaxed motorhome trips with kids."

Subtype is now **activity-only**. The other axes become first-class columns
on `event` so the dashboard filter rail can query them in plain SQL.

| Axis | Column | Type | Values | UI shape |
|---|---|---|---|---|
| Activity | `event_subtype` | TEXT (any) | curated presets per type, **plus free-text fallback** | editable QComboBox |
| Duration | `duration_value` + `duration_unit` | INTEGER + TEXT (enum) | unit ∈ `hours, days, weeks, months, years`; value 1..cap; cap per unit (23 / 6 / 3 / 11 / 50) | spinbox + unit combo |
| Scope | `scope` | TEXT (enum) | `international, domestic` | dropdown |
| Participants | `participants` | TEXT (JSON array) | `Solo, Couple, With Family, With Kids, With Friends, With Colleagues, Client` | multi-select chips |
| Mood | `mood` | TEXT (enum) | `relaxed, active, cultural, professional` | dropdown |
| Transport | `transport` | TEXT (enum) | `flight, car, train, cruise, motorhome, mixed` | dropdown (**Trip-only**; hidden on other types) |

Empty / `0` / `[]` on any of these clears the field back to NULL (or `'[]'`
for participants). The gateway `set_classification` accepts every column as
a kwarg and validates each enum value — bad input raises `ValueError`, not a
silent coerce.

### Per-item Subject — separate concern

The per-item `item.subject` column (TEXT, nullable) is a free-text
annotation the user attaches to a photo or clip (bird species, plant name,
landmark — anything to look up later in e-bird / iNaturalist / Wikipedia).
Storage only at this stage; the UI surface for editing it is deferred to a
later slice. Applies to both photos and clips since they share `item.kind`.

---

## 13. Related specs

- [spec/00-charter](00-charter.md) — the constitution.
- [spec/30-relational-schema-redesign](30-relational-schema-redesign.md) — the
  per-event schema (with spec/52 cleanups: dropped tables/columns, new
  `photo_tag`, new `photo_person`, `item.tz_source` aligned to camera_day_tz,
  `'share'` dropped from phase enums).
- [spec/48-four-phase-pivot](48-four-phase-pivot.md) — locked vocabulary
  (Collect / Pick / Edit / Share) this spec inherits.
- [spec/51-share-cuts-vision](51-share-cuts-vision.md) — Share-phase redesign
  that drives the field retirements (tags / people / long-observation) which
  free space for the one-surface plan dialog.
- [spec/53-user-data-store](53-user-data-store.md) — the user-level
  `mira.db` that holds settings, wizard answers, event_index, etc.
  (the cached event-level country / TZ projection lives here).
