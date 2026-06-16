# spec/64 — Event Header and Event Days Table (the events-information split)

**Status:** design **LOCKED 2026-06-13**, Nelson (design-mode session).
Supersedes the unified `PlanDialog` model and the Scope / Mood / Transport
vocabulary from [spec/52](52-event-creation-vision.md). The
create-from-scratch + create-from-media flow shapes of spec/52 survive — the
change is what's inside each step, plus the dialog split.
**Implementation NOT scheduled — Nelson's word required.**

---

## 0. The mistake being corrected

Two different pieces of truth about an event live inside one dialog today.
One is **identity** — what was the event, who came, what was the nature of
the time. The other is the **schedule** — the per-day rows with country,
time zone, location, description. The shared host already pretends to
separate them: six `PlanDialog` call sites in `MainWindow` open it with
`with_event_info=True, with_plan=False` (or the inverse) to show one half
or the other. The toggle is an admission; the dialog is doing two jobs.

The split makes the two surfaces real, retires the toggle host, and takes
the Header field set through a vocabulary refresh that Scope / Mood /
Transport was reaching at but missing.

---

## 1. The split

Two real dialogs replace `PlanDialog`:

- **Event Header** — the identity surface. Opened on its own, never bundled.
- **Event Days Table** — the schedule surface. Opened on its own, never
  bundled.

`PlanDialog` and its `with_event_info` / `with_plan` toggles retire. The
six MainWindow call sites collapse to "open Header" or "open Days Table".

---

## 2. Entry points

Two paths to each surface.

### 2.1 Menu

The Event Phases menu (top bar, with the open event in scope) carries two
items:

- **Event Header**
- **Event Days Table**

### 2.2 The event tile

Four doors. The Title, Left side, and Status badge land at the same
surfaces regardless of event state; the Body changes by state.

**Open event:**

| Where you click | Where it lands |
|---|---|
| Title line | Event Header |
| Left side of tile | Event Days Table |
| Body of tile | Phases dashboard (the "work the event" path — unchanged) |
| Status badge | Toggles Open ↔ Closed (§2.3) |

**Closed event:**

| Where you click | Where it lands |
|---|---|
| Title line | Event Header |
| Left side of tile | Event Days Table |
| Body of tile | Cuts list (§2.4) |
| Status badge | Toggles Open ↔ Closed (§2.3) |

### 2.3 The status badge

A small badge on every event tile shows the event's current state —
**Open** or **Closed** — and toggles between them on click. The toggle is
**instant** (no confirm): the status flag is metadata; flipping it does
not destroy anything; the tile's body content and visual treatment update
accordingly. Distinct from the Header badge in §5 — different role,
different trigger; both can coexist on the same tile.

(The `event.is_closed` column already exists on the schema; the badge
reads and writes it. No schema change for this door.)

### 2.4 The closed-tile body

When an event is Closed, the tile's body area replaces the open-event
phases dashboard with **three side-by-side widgets** (Nelson
2026-06-13, three-stage redesign — slice 6c shipped the "Cuts inside"
hint; v2 swapped to a bar chart + photo carousel; v3 retired the
carousel for the donut + legend after the carousel read too small in
the fixed 180-px tile height):

- **A horizontal phase bar chart** (left, ~4⁄9 of body width). Four
  rows — **Collected** (always 100 %, the universe), **Picked**,
  **Edited**, **Exported** — each coloured to read as a left-to-right
  progression through the workflow (slate → blue → amber → emerald).
  The count + the percent of Collected ride on the right of each bar.
- **A classification donut** (centre, ~2⁄9). Pie slices proportional
  to each per-photo classification's count across the event
  (the workflow scenario tag the background classification pass
  writes on event open — Macro / Wildlife / Landscape / etc.). The
  arc starts at 12 o'clock and walks clockwise, dominant
  classification leading; a transparent centre punches the disc into
  a ring. Labels live in the legend, not on the donut itself.
- **A legend** (right, ~3⁄9). One row per classification with at
  least one photo: a colour swatch + the display label + the count.
  Sorted descending by count to match the donut's arc; capped at six
  rows with the tail folding into "+ N more" so the legend never
  crowds the body.

All three widgets bubble clicks to the surrounding right zone, which
routes via the same `heatmap_clicked` signal the open tile uses;
MainWindow forks on `event.is_closed` and lands on the **Cuts list**
([spec/61](61-share-event-cuts.md) §6 — `CutsListPage` with the
#exported built-in row + user Cuts).

The card itself uses `setFixedHeight(180)` (was `setMinimumHeight`),
so the closed body's Expanding widgets fill the same vertical slot
the heatmap occupies on open tiles — open and closed tiles share the
same vertical size by construction.

---

## 3. Event Header

### 3.1 The fields

| Field | Form | Required at create | Notes |
|---|---|---|---|
| **Name** | Free text | ✓ | The event identity. |
| **Type + subtype** | Two-level chooser | ✓ | Files the event under its umbrella. |
| **Description** | Free text | — | What this event was. |
| **Duration** | Integer X (free, > 0) + unit selector | — | Per-unit caps removed (use 7 days instead of being forced to 1 week). |
| **Context** | Single-select | — | The baseline environment (§3.2). |
| **Experience Type** | Single-select | — | The vibe / intent / creative energy (§3.3). |
| **Creative Focus** | Multi-select | — | Photographic subjects (§3.4). |
| **Participants** | Multi-select chips | — | Solo / Couple / With Family / etc. (unchanged from spec/52 §14). |

**Out:** Scope, Mood, Transport. Replaced by Context + Experience Type +
Creative Focus.

### 3.2 Context (single-select)

Tracks the baseline environment of the event.

- **Leisure** — pure personal time, vacations, family life.
- **Professional Trip** — business travel, board meetings, work events.
- **Home / Routine** — activities anchored at the primary residence or
  local neighborhood.

### 3.3 Experience Type (single-select)

Tracks the primary vibe, intent, or creative energy of the experience.

- **Expedition & Discovery** — active exploration, tracking wildlife,
  birding, nature travel, or heavy outdoor photography.
- **Studio & Craft** — highly deliberate, technical, or staged creative
  projects at home (e.g., complex macro rigs, focus-stacking setups,
  waterdrop experiments).
- **The Slow Down** — retreats, quiet weekend getaways, cabins, or anywhere
  the explicit goal was rest and disconnecting.
- **Urban & Culture** — city breaks, architecture walks, museum visits,
  dining experiences, theater.
- **Milestones & Traditions** — birthdays, anniversaries, weddings, family
  holiday gatherings, major life markers.

### 3.4 Creative Focus (multi-select)

Tracks the artistic or photographic subjects of the event. Leave blank if
it wasn't a photography-centric event.

- **Macro**
- **Birds**
- **Wildlife**
- **Landscape**
- **Urban / Street**
- **None** — the explicit "this was not a photo event" answer. Selecting
  None clears the subjects; selecting any subject clears None.

### 3.5 Rich descriptions ride as hints

The prose under each Context / Experience Type / Creative Focus option
above is the **tooltip** in the dialog — hover teaches; the field itself
stays clean. Honors the every-control-has-a-hint rule (spec/05; memory
`ui_editable_fields_need_hints`).

### 3.6 The required floor at create

The dialog accepts OK with **Name + Type + subtype** set. Everything else
is optional. The badge (§5) carries the nudge to fill the rest later.

### 3.7 When the Header opens

Header is **the first moment of any event birth**. Two paths:

- **Just create an event** (menu / button). Header dialog opens. OK →
  event row exists with zero days. Days Table fills later when the user
  runs Collect.
- **Create from media.** Header dialog opens at the start of the flow,
  before any further SD scan progress. OK → Collect proceeds
  automatically; the Days Table fills as photos resolve.

### 3.8 Cancel

- **Create-from-scratch cancel.** No event created. Clean.
- **Create-from-media cancel.** **Rolls back the whole flow.** SD scan
  stops, no event exists, the user starts over.

(Editing the Header on an existing event — never destructive; Cancel just
dismisses changes.)

---

## 4. Event Days Table

### 4.1 The shape

Per-day rows. Each row carries:

- **Country** (dropdown)
- **Time zone** (dropdown)
- **Location** (free text, never required)
- **Description** (free text, never required)

The table is created and extended by **Collect** (spec/57 §4) — that flow
stays. Day rows arrive as Collect resolves capture dates; the user fills /
edits country, TZ, location, description.

### 4.2 Focus stops following the mouse pointer

Today, the cell under the mouse pointer steals focus when the pointer
moves — Copy / Paste lands in the cell the user did not intend. The fix:
focus stays where the user explicitly placed it (click, Tab) and does
**not** migrate just because the mouse moved over a different cell.

### 4.3 Country / TZ propagate-down with confirm

Changing the country (or TZ) for 20 days, one row at a time, is the
dominant pain on this surface today. The move:

1. The user changes the country (or TZ) in row N.
2. The system prompts (plain yes / no): "Apply the new value to the rows
   below, stopping at the first one you've already touched?"
3. On **Yes**: rows N+1, N+2, … take the new value, walling at the first
   **user-touched** row (or running to the end if none). On **No**: only
   row N changes.

**"User-touched"** = the user has changed that cell from whatever Collect
left there. A cell Collect filled and the user never opened is in the
cascade; a cell the user has edited is a wall.

Same lever for Time Zone, independently.

### 4.4 Phone-without-GPS prompts replace silent home-fill

Today, when phone shots on a day carry no GPS / no TZ, Collect silently
fills country and TZ with the user's home defaults. **It should ask
instead.**

The prompt fires **per location-group**: Collect groups consecutive
GPS-less days into a single stretch and asks once per stretch — "Days 3–5:
country / TZ?". When phone GPS reappears, the stretch closes.

**"GPS-less day" definition.** A day is GPS-less when **no phone shot on
that day carries usable location info**. Cameras typically don't tag GPS;
their absence does not make a day GPS-less. The test is the phone.

When a stretch is a single day, the prompt is effectively per-day, which
is the right behavior anyway.

### 4.5 What does not change

- The Days Table is built and extended by Collect as today.
- Location and Description are free text, never required.
- Existing day rows survive — the migration touches Header columns only.

---

## 5. The badge (RETIRED 2026-06-13 — Nelson eyeball)

**Status: retired post-build.** Slice 6 shipped the Header first-touch
badge as designed; Nelson's first eyeball ruled it redundant — the title
line on every tile already opens the Event Header dialog (§2.2), so the
badge was a second nudge to the same door. The badge widget, the sticky
``extras_json["header_touched"]`` bit, and the supporting
``event_classification.header_unset()`` rollup all retired in the same
session. Old events still open with blank Context / Experience Type /
Creative Focus — the user fills them via the title click whenever they
want; there is no longer a tile-level nudge.

(Original design — for the record: a small "Header" badge would have
ridden on the tile while any of the three were unset, clearing on first
touch and staying cleared via the sticky bit even if the user later
wiped the field.)

---

## 6. Migration

### 6.1 The schema move

- `event.scope` — **dropped**.
- `event.mood` — **dropped**.
- `event.transport` — **dropped**.
- `event.context` — **added** (TEXT enum, nullable: `leisure` /
  `professional_trip` / `home_routine`).
- `event.experience_type` — **added** (TEXT enum, nullable:
  `expedition_discovery` / `studio_craft` / `slow_down` / `urban_culture`
  / `milestones_traditions`).
- `event.creative_focus` — **added** (TEXT JSON array, nullable; values:
  `macro` / `birds` / `wildlife` / `landscape` / `urban_street` / `none`).
- `event.duration_value` — **preserved**.
- `event.duration_unit` — **preserved** (the cap-per-unit removal is
  UI-side; the unit selector stays).

Per `feedback_schema_evolution_policy` — every column change is a real
ALTER TABLE migration; events are preserved.

### 6.2 User-facing

Existing events survive. Old Scope / Mood / Transport values do **not**
migrate — they retire with the columns; no leftovers. The new three
dimensions open as blanks; the tile carries the badge (§5) until the user
touches at least one.

### 6.3 Code surface

- `PlanDialog` and its `with_event_info` / `with_plan` flags retire.
- The six `MainWindow` call sites collapse to two — an "open Event Header"
  call and an "open Event Days Table" call (working names).
- Spec/52 §14's qualifier table (Scope / Mood / Transport rows) is
  **STALE** vs spec/64 — annotated in place.
- Spec/03 (schema.md) event table needs to follow the §6.1 column moves
  when implementation lands.

---

## 7. What this retires / changes

- `PlanDialog` and its `with_event_info` / `with_plan` toggle host.
- The Scope / Mood / Transport vocabulary.
- The per-unit Duration cap (X capped at unit-specific max).
- The silent home-country / TZ autofill in Collect when phone GPS is
  missing.
- Mouse-pointer-follows-focus behavior on the per-day table.
- One-by-one Country / TZ editing as the only path for cross-day changes.

---

## 8. Parked

- **Closed-tile body content.** Stats charts vs random Picked photos vs
  alternating (§2.4) — pick at build and eyeball.
- **pt-BR vocabulary.** Translation strings for Context / Experience Type
  / Creative Focus and their option labels, plus the Open / Closed status
  badge text.
- **Dialog field layout and grouping.** Which fields share a
  `FormFieldGroup`, the order, the column count — rides the build and
  eyeball, with the `feedback_titled_groupbox_over_label` rule in force.
- **Tile badge visuals — Header badge (§5) and Status badge (§2.3).**
  Position on the tile, color, exact text — both ride the build.

---

## 9. Acceptance criteria (the eyeball)

1. Two real dialogs. Opening one never shows fields that belong to the
   other.
2. Four doors on the event tile land where §2.2 says, for both Open and
   Closed states.
3. Header at create accepts OK with Name + Type + subtype alone.
4. Cancel during create-from-media leaves no event and aborts the SD
   scan.
5. Hovering an option in Context / Experience Type / Creative Focus shows
   its rich description as a tooltip.
6. Days Table: clicking a cell, moving the mouse off, typing — the typed
   text lands in the cell the user clicked.
7. Days Table: changing country in row N prompts plain yes / no; Yes
   propagates to rows below until the first user-touched row (or end).
8. Collect on a day where the phone shot nothing with GPS does not
   silently fill home defaults; a per-location-group prompt asks instead.
9. Old events open with blanks for the new three dimensions; the user
   fills them via the title-click Event Header dialog at their own pace
   (the original tile-level Header nudge badge retired 2026-06-13).
10. Cap-per-unit gone: typing `7` in Duration with unit `days` is
    accepted; typing `100` with unit `days` is also accepted.
11. Clicking the Status badge toggles Open ↔ Closed instantly; the tile's
    body content and visual treatment update on the spot.
12. On a Closed tile, the body area shows the phase bar chart (left)
    + the classification donut (centre) + the legend (right) — not
    the phases dashboard — and clicking any of the three lands on
    the Cuts list. Open and closed tiles render at the same fixed
    height (the closed body fills the same slot the heatmap does on
    open tiles).

---

## 10. Implementation record

**Slice 1 — schema + model + gateway (LANDED 2026-06-13).** The data
foundation; no new UI.

- `mira/store/schema.py` — `SCHEMA_VERSION` bumped to 6. DDL
  swap on `event`: `scope` / `mood` / `transport` columns (and their
  partial indexes `ix_event_scope` / `ix_event_mood`) retire; `context`
  (TEXT, nullable) + `experience_type` (TEXT, nullable) +
  `creative_focus` (TEXT JSON array, NOT NULL DEFAULT `'[]'`, with
  `json_valid` CHECK) arrive, alongside partial indexes
  `ix_event_context` + `ix_event_experience_type`. Migration
  `_migrate_v5_to_v6`: DROP INDEX first (SQLite refuses `ALTER TABLE
  DROP COLUMN` while an index references the column), then DROP
  COLUMN × 3, then ADD COLUMN × 3, then CREATE INDEX × 2. Per §6.2,
  old Scope / Mood / Transport values do NOT carry over — the new
  columns land empty on existing events; the Header badge (slice 6)
  nudges the user to fill at leisure. SQLite still can't add CHECKs
  via ALTER, so migrated rows get the `json_valid` CHECK only on
  fresh installs; the gateway seam validates on write either way.
- `mira/store/models.py` — `Event` dataclass: `scope` / `mood`
  / `transport` fields removed; `context` / `experience_type` /
  `creative_focus` added with sensible defaults. Per-unit duration
  cap comment retired (the value is now just a free integer > 0 in
  the chosen unit).
- `mira/event_classification.py` — `SCOPE_OPTIONS` /
  `MOOD_OPTIONS` / `TRANSPORT_OPTIONS` / `DURATION_UNIT_MAX` retired
  (callers fail loudly on missing symbol). Three new vocabularies,
  each shipping its enum tuple, its display-label map, and (for the
  two single-selects) its rich-description map drawn straight from
  §3.2 / §3.3 — slice 2's `EventHeaderDialog` will bind these as
  hover tooltips. `is_known_context` / `is_known_experience_type` /
  `is_known_creative_focus` helpers added for boundary validation.
- `mira/gateway/gateway.py` — `set_classification` signature
  swap: `scope` / `mood` / `transport` kwargs gone; `context` /
  `experience_type` (single-select TEXT) + `creative_focus`
  (list-of-strings, JSON-encoded at the seam) added. Closed-enum
  validation per §3 fires before any UPDATE runs (`ValueError` is
  the contract; empty string clears the single-selects to NULL,
  empty list clears the multi-select to `'[]'`).
- `mira/ui/pages/plan_dialog.py` — minimal patch so the
  dialog construct-smokes after the columns retire. The three
  Scope / Mood / Transport widgets (combos + group boxes + grid
  placements) are gone; the trip-only Transport visibility logic
  retired (no per-type fields remain); `_apply_existing_event_info`
  and `event_info()` no longer touch those keys; the duration
  spinbox's per-unit clamp on `DURATION_UNIT_MAX` is replaced by a
  generous fixed `9999` ceiling (the §3.1 "free integer > 0" rule);
  the unused `_make_open_combo` / `_read_open_combo` helpers
  deleted. PlanDialog stays alive — slice 4 retires it after the
  two replacement dialogs (slice 2 / slice 3) land.
- `mira/ui/shell/main_window.py` — four call sites updated:
  the `_create_event_from_plan` `Event(...)` constructor (line
  ~1206), the Collect-path `set_classification` call (line ~2412),
  and the two `existing_info` dict builders (lines ~1820 + ~3136)
  no longer reference the retired columns or pass the retired
  kwargs.
- `tests/test_event_header_migration.py` NEW — 12 pins: schema
  version, fresh-install column shape, fresh-install index shape,
  fresh-install `creative_focus` default `'[]'`, the v5→v6
  migration on a real v5 fixture (columns dropped clean, new
  columns blank, indexes rebuilt, survivor columns untouched), the
  `Event` dataclass field shape, and the vocabulary surface
  (`is_known_*`, labels, descriptions, retired-symbol absence).
- `tests/test_gateway.py` — Scope / Mood / Transport tests replaced
  with Context / Experience Type / Creative Focus equivalents
  (round-trip, enum validation, the `["none"]` explicit-no-photo
  case, the falsy-value clear path). Same coverage shape, new
  vocabulary.
- `tests/test_look_strength_foundation.py` — the v4→v5 migration
  fixture seeds the v4 event table too (with retired scope / mood
  / transport columns) so the v5→v6 step has something to ALTER as
  the chain runs through; the `schema_version == 5` pin loosened
  to `>= 5` (the broader chain runs to whatever the current
  `SCHEMA_VERSION` is).
- `tests/test_store.py` — the v2→v3 migration test's roll-back-to-v2
  setup extended to reverse the v5→v6 event qualifier swap (drop
  context / experience_type / creative_focus + their indexes; add
  scope / mood / transport + their indexes back) so the v5→v6 step
  finds the v5 shape it expects when the chain runs forward.

**Verified:** `verify.bat` 2797 passed / 0 failed (main) + 20 passed
/ 0 failed (quarantine) at slice 1 HEAD. MainWindow construct-smoke
OK. 12 new tests across the migration / vocabulary surface; the
existing schema / gateway / dashboard / dialog suites green.

**Slice 2 — `EventHeaderDialog` (LANDED 2026-06-13).** The new identity
dialog; the schedule half retires in slice 3.

- `mira/ui/pages/event_header_dialog.py` NEW — eight fields
  per §3.1: Name / Type+subtype (required floor, §3.6) / Description
  / Duration (free integer > 0 + unit, no cap) / Context / Experience
  Type / Creative Focus (multi-select with §3.4 None-vs-subjects
  mutual exclusion) / Participants. Every form input rides a titled
  `FormFieldGroup` group box (memory
  `feedback_titled_groupbox_over_label`). Context / Experience Type
  bind their rich descriptions from §3.2 / §3.3 as per-item Qt
  ToolTipRole tooltips on the dropdowns (the §3.5 "hover teaches"
  rule). OK is gated on the required floor; Cancel always available.
  Returns `header_info() -> dict` keyed for the gateway (matches the
  legacy `PlanDialog.event_info()` for the surviving fields so slice
  4 can swap the call sites cleanly); accepts `existing_info` for
  edit-existing flows.
- `tests/test_event_header_dialog.py` NEW — 13 pins: construct
  shape, required-floor gating (name, type, subtype, all three
  paths), `header_info()` round-trip for filled + empty values, the
  retired-keys-do-not-leak guard, `existing_info` pre-population
  (full + partial), the Creative Focus mutual-exclusion rule (None
  clears subjects, subject clears None), the per-option tooltip
  coverage on both single selects, the every-control-has-a-hint
  pin.

**Verified:** 13 new pins green; the slice 1 / 2 neighbourhood (117
across `test_event_header_dialog` + `test_event_header_migration`
+ `test_plan_dialog` + `test_event_info_dialog` + `test_gateway`)
green; MainWindow construct-smoke OK. PlanDialog stays alive — the
new dialog is unreachable from the running app until slice 4 wires
it into the creation flows.

**Slice 3 — `EventDaysTableDialog` (LANDED 2026-06-13).** The new
schedule dialog; the §4 fixes in one focused surface.

- `mira/ui/pages/event_days_table_dialog.py` NEW — 5-column
  table per §4.1: Date (read-only identity) / Country (search-combo)
  / TZ (TzPicker) / Location (free text) / Description (free text).
  The Include / Browse / Override columns of the legacy PlanDialog
  per-day table are intentionally out of scope here: this dialog
  edits day metadata, so the Collect-time "pick which days to
  import" UX stays on PlanDialog until slice 4.

  Three §4 fixes:
  - **§4.2 focus stays put** — a `_NoUnfocusedWheelFilter` event
    filter installed on every cell widget swallows wheel events
    delivered to widgets that don't already have focus. Without it,
    scrolling the table over a combo / picker shifts focus to the
    widget under the cursor and changes its value (the legacy
    behaviour Nelson reported). Click + Tab focus still work
    normally.
  - **§4.3 propagate-down with confirm** — `_on_country_changed`
    and `_on_tz_changed` mark the seed row as user-touched in a
    per-cell ledger (`_touched: Set[(row_idx, field_key)]`), then
    `_maybe_propagate_*` collects the rows below up to the first
    user-touched row of the same field and opens a plain yes/no
    `QMessageBox.NoIcon` confirm (`feedback_qmessagebox_chrome_disliked`).
    On Yes the cascade runs with `_cascading=True` so the
    cascade-driven `setCurrentIndex` / `setValue` calls don't
    re-trigger the propagate logic. The Country and TZ walls are
    independent (touching TZ doesn't wall a Country cascade and
    vice versa). The confirm has a test seam,
    `set_propagate_confirm(bool | None)`, so the suite never pops
    a real modal (memory `feedback_tests_never_exec_modals`).
  - **§4.5 free-text Location / Description** — plain QLineEdit
    cells; editing marks the cell touched but never triggers a
    propagate prompt of its own.
- `tests/test_event_days_table_dialog.py` NEW — 15 pins: construct
  + row-round-trip + read-only date, country yes/no/no-rows-below/
  walls-at-touched-row, TZ same matrix, the independence pin
  (Country and TZ walls are independent), the free-text edits mark
  touched without prompting, the wheel filter swallows wheel on
  unfocused widgets and passes wheel through on focused ones, and
  per-column-header tooltip coverage.

**Verified:** 15 new pins green; `verify.bat` 2825 passed / 0 failed
(main) + 20 / 0 (quarantine) at slice 3 HEAD (up from slice 2's 2810
by the 15 new pins); MainWindow construct-smoke OK. PlanDialog stays
alive — slice 4 retires it.

**Slice 4 — PlanDialog retirement + creation-flow rewire (LANDED
2026-06-13).** Every legacy call site moves to one of the two new
dialogs; the toggle host is gone.

- `mira/ui/pages/plan_dialog.py` and
  `tests/test_plan_dialog.py` DELETED.
- `mira/ui/pages/event_days_table_dialog.py` extended — slice
  3 shipped a minimal Country / TZ / Location / Description dialog;
  slice 4 brings forward every feature the legacy `PlanDialog`
  per-day table offered: the **Include checkbox** column (with the
  ISO date as the checkbox label, matching the legacy UX), the
  **Browse-day** peek button (handler injected by the host), the
  **Override marker** column (auto-hides when no row carries a
  marker; handler injected), **CSV Save / Load** (premium-gated via
  `can_save_load_csv`; the load path skips TZ values when
  `frozen_after_ingest` is set, preserving the spec/57 §4.2
  no-shift-photos guarantee), **Delete-day** (opt-in via
  `can_delete_days`; selection-driven enable; NoIcon confirm),
  **`frozen_after_ingest`** + **`tz_editable_when_frozen`** flags
  (TZ disable for ingested days; the §4.2 single-day TZ unlock lets
  the picker stay live with the host gating the actual write via a
  re-time confirmation). The slice 3 fixes stay intact: the
  `_NoUnfocusedWheelFilter` event filter (focus-stays-put), the
  Country / TZ propagate-down with plain yes/no confirm walling at
  user-touched rows, the free-text touched ledger. A `_loading`
  re-entrancy guard prevents CSV-load bulk writes from triggering
  propagate prompts.
- `mira/ui/shell/main_window.py` — six legacy call sites
  collapse to two: `_open_event_header_dialog(event_id)` opens the
  Header (identity edit), `_open_event_days_table_dialog(event_id)`
  opens the Days Table (schedule edit). Tile click routing per
  §2.2: `_open_event_info_dialog` (title click) → Header,
  `_open_event_plan_from_card` (left click) → Days Table; the body
  click stays on the existing `event_activated` → activity dashboard
  wiring. `_open_edit_info` (Event menu) → Header.
  `_open_edit_plan_for_event` (Collect → Edit plan) → Days Table
  with `can_save_load_csv` (premium flag), `can_delete_days=True`,
  `frozen_after_ingest` derived from whether any day holds photos,
  `tz_editable_when_frozen=True`; the spec/57 §4.2 re-time
  confirmation moves into a new `_handle_retime_and_save` helper.
  `_open_new_event_info_only` (Just-create) → Header only (no
  Days Table; event row exists with zero days, fills via Collect
  later — spec/64 §3.7). `_open_new_event_flow` (Create from
  existing media) → Header dialog → **Days Table dialog with all
  scan_rows + Include checkbox + browse_handler + CSV gate** →
  `included = [r for r in edited if r.checked]` filter → create
  event → auto-Collect. Cancel on either dialog rolls the whole
  flow back. `_open_collect` (incremental Collect for an existing
  event) → multi-date split → **Days Table dialog with merged_rows
  + Include + browse_handler + CSV gate** → ingest gate. The
  Header isn't shown in Collect (identity edits route through the
  tile's title-zone door). `_open_existing_event_info` retired (the
  unified-host method); the lingering scope/mood/transport call
  the slice 1 sweep missed at line ~3196 dies with it.
- `_create_event_from_plan` now writes Context / Experience Type /
  Creative Focus from the Header dialog into the new `Event` row.
- New helper methods on MainWindow:
  `_build_scan_rows_from_trip_days`, `_build_days_table_dialog`
  (kwargs forward), `_save_trip_day_edits`,
  `_handle_retime_and_save`, `_show_no_days_message`,
  `_exec_event_header_dialog` / `_exec_event_days_table_dialog`
  (test seams per `feedback_tests_never_exec_modals`).
- `tests/test_event_days_table_dialog.py` rewritten — 31 pins: the
  15 slice 3 pins PLUS 16 new ones for the restored features
  (Include date label + round-trip; Browse handler enabled /
  disabled + fires with the day; Override hide / show / handler;
  frozen TZ disable + the tz_editable unlock; CSV gated visibility
  + round-trip save→load; CSV-load TZ-skip when frozen; Delete-day
  gated visibility + yes / no paths).

**Restoration record (2026-06-13, mid-slice-4).** A first cut of
slice 4 stripped the legacy `PlanDialog`'s Include / Browse / CSV /
Delete / Override / frozen-after-ingest features in the structural
rewire, on the wrong reading that "PlanDialog retires" meant the
features retire too. Nelson called the regression at the spec-shape
checkpoint ("I have never asked you to remove them — all those
features are important and have to be kept") — the right reading of
spec/64 + memory `feedback_exactly_like_before_means_dont_ask` is
that the dialog splits + the new Header dimensions are spec/64's
scope; every other legacy capability survives untouched. The
EventDaysTableDialog rewrite above brought all of them forward and
the slice 3 fixes stay intact. The single commit logs the restored
shape, not the intermediate strip.

**Verified:** `verify.bat` 2811 passed / 0 failed (main) + 20 / 0
(quarantine). MainWindow construct-smoke OK. Net delta vs slice 3
(2825): −30 from `test_plan_dialog.py` deletion + 16 new
features-back pins = −14. Nelson eyeballed the end-to-end Create
Event flow on real data and confirmed it works ("All that we have
created in this session seems to have worked fine").

**Slice 5 — phone-without-GPS per-location-group prompt (LANDED
2026-06-13; the per-stretch loop is SUPERSEDED by spec/78 §A —
single ask for all no-GPS days, 2026-06-16).** Replaces today's
silent home-country / TZ autofill during Collect.

- `mira/ui/pages/phone_gps_stretch_dialog.py` NEW — the prompt
  dialog. Shows the date range covered ("Days 3–5 …"), one Country
  combo + one TZ picker that apply across the whole stretch,
  pre-filled with the user's home country / TZ as suggestions. Apply
  = use these values; Skip = leave the rows blank so the user can
  fine-tune via the Days Table dialog later. Singular / plural
  heading variants for one-day vs multi-day stretches.
- `mira/ui/shell/main_window.py` — three changes:
  - `_open_new_event_flow` + `_open_collect` now pass
    `home_country=None, home_tz_minutes=None` to `scan_source`
    (and to `build_scan_result` after the multi-date split rebuild)
    so the silent home-fill path doesn't run; GPS-less days come
    back with blanks.
  - New helper `_prompt_phone_gps_stretches` walks the row list, finds
    consecutive runs missing country OR TZ, opens one
    `PhoneGpsStretchDialog` per run, and applies the user's pick
    across the run (Skip leaves the run blank).
  - `_collect_phone_gps_stretches` is the pure-logic grouping —
    static, no Qt; tested in isolation.
  - `_exec_phone_gps_stretch_dialog` is the test seam.
- `tests/test_phone_gps_stretches.py` NEW — 11 pins: the
  grouper's no-blanks / all-blanks / middle-singleton /
  edges-only-stretches / country-only-blank / tz-only-blank cases,
  plus the dialog's construct / Apply / Skip / result-values shape
  + tooltip coverage.

Note: `TzPicker` has no None state — it defaults to 0:00 UTC when
the prompt opens without a home TZ. Apply = "use the picker's
current value" (UTC if untouched). Skip is the explicit "leave
blank" path. Acceptable; documented in the test.

**Verified:** 11 new pins green; `verify.bat` 2835 passed / 0
failed (main, up from 2812) + 20 / 0 (quarantine). MainWindow
construct-smoke OK.

**Next:** slice 6 — the tile updates (Header first-touch badge +
Status badge + closed-event body content + Cuts list door).

**Slice 6 — tile updates (LANDED 2026-06-13).** The closer of the
spec/64 arc; every door on §2.2 is live, every badge on §2.3 / §5 is
on screen, and the closed-tile body content swap (§2.4) routes to the
Cuts list.

- `mira/ui/base/event_card.py` — three new building blocks:
  - `_StatusBadge` (QLabel subclass, **clickable**, eats its own mouse
    press so the parent's title-zone door doesn't open underneath):
    the §2.3 Open ↔ Closed badge on **every** tile. Dynamic
    ``state`` Qt property (``"open"`` / ``"closed"``) drives the QSS
    colour; the badge repolishes on state change so Windows picks the
    new colour up (memory
    `reference_qss_descendant_property_repolish`). Emits
    ``clicked(event_id)``.
  - The §5 **Header first-touch badge** — a plain
    ``EventCardHeaderBadge`` QLabel slotted into the title row; clicks
    bubble naturally to the surrounding top-zone (which routes to
    ``title_clicked`` → Event Header dialog, the natural place to fill
    the nudge). Visible iff ``data.header_unset`` is True.
  - `_ClosedBodyContent` — the §2.4 closed-tile body content. Replaces
    the phase × day heatmap with a "Cuts inside" hint + Cut count
    label. First-cut interpretation of §2.4 + §8 ("pick one"): the
    count + hint, no charts / Picked-thumbs strip yet (those can ride
    a follow-up if Nelson wants them).
  - `EventCardData` extended with two new fields: ``header_unset``
    (the §5 rollup) and ``cuts_count`` (closed-tile body data).
  - New ``status_badge_clicked(event_id)`` signal on ``EventCard``.
    The existing ``heatmap_clicked`` signal is the closed-body click
    too (back-compat with the open path) — the host forks on
    ``is_closed`` to route it to the Cuts list vs. activity dashboard.
- `mira/event_classification.py` — pure-logic helper
  ``header_unset(*, context, experience_type, creative_focus_json,
  header_touched)`` returning ``True`` when none of the three new
  dimensions has been touched (incl. the sticky-bit check). The
  dashboard's ``_card_data`` calls this; the gateway is what flips
  the sticky bit.
- `mira/gateway/gateway.py` — ``set_classification`` now writes
  a sticky ``extras_json["header_touched"] = True`` whenever any of
  ``context`` / ``experience_type`` / ``creative_focus`` arrives with
  a non-empty value. Touch = "user set something"; empty-string /
  empty-list clears do NOT count. The bit shallow-merges into
  existing ``extras_json`` so spec/52 IPTC location facets survive.
  Honors §5 "first-touch counts, even if the user later wipes the
  field back to blank" — once flipped the badge stays cleared.
- `mira/ui/pages/events_dashboard_page.py` — ``_card_data``
  reads ``ev.context`` / ``ev.experience_type`` / ``ev.creative_focus``
  + ``extras_json`` off the open event row, feeds them to
  ``event_classification.header_unset`` for the ``header_unset``
  rollup, and (for closed events only) reads ``len(eg.cuts())`` into
  ``cuts_count``. New signal
  ``event_status_toggle_requested(event_id)`` plumbed through the
  card → page wiring.
- `mira/ui/shell/main_window.py` — three changes:
  - New ``event_status_toggle_requested`` connection at the dashboard
    seam.
  - ``_open_event`` forks on ``self._event_is_closed(event_id)``:
    closed → ``_open_event_cuts_list(event_id)`` (the §2.4 Cuts list
    door, same shape as the existing ``"share"`` route on the phase
    dashboard, promoted to a direct landing for closed tiles); open
    → the existing activity-dashboard path unchanged.
  - ``_on_card_status_toggle_requested`` is the §2.3 instant toggle
    handler: opens the gateway, flips ``is_closed``, refreshes the
    index entry + the events page so the tile picks up its new badge
    state + body content. If the toggled event is currently open in
    the activity dashboard, refreshes that too (keeping the Event
    menu's Close↔Re-open label in sync).
  - ``_event_is_closed`` is a thin index-cache lookup so the fork
    doesn't open ``event.db`` per click.
- `assets/themes/light.qss` + `dark.qss` — new QSS roles in BOTH
  themes: ``EventCardStatusBadge`` (with the
  ``[state="open"]`` / ``[state="closed"]`` selectors and a
  ``:hover`` border thickening that keeps the outer footprint stable),
  ``EventCardHeaderBadge`` (uses the existing
  ``warning_bg`` / ``warning_fg`` / ``warning`` palette tokens),
  ``EventCardClosedBody`` + its title + count labels. The legacy
  read-only ``EventCardClosedBadge`` block retires (replaced by the
  clickable status badge).
- `tests/test_spec64_tile_updates.py` NEW — 31 pins across five
  groups: (a) ``event_classification.header_unset`` pure-logic
  matrix (all three blank / context set / experience_type set /
  creative_focus set / ``["none"]`` explicit no-photo / sticky bit /
  empty-string-as-blank); (b) EventCard structure (status badge on
  open + closed tiles, click → signal, tooltip coverage, legacy role
  retired, header badge visible iff ``header_unset``, closed body
  replaces heatmap, zero / one / N Cut count phrasing, open tile
  keeps heatmap, heatmap signal fires on closed body); (c) dashboard
  wiring (``_card_data`` rollup for all four header_unset axes +
  cuts_count for closed/open, the new dashboard signal); (d)
  gateway sticky-touch bit (flipped on context set / creative_focus
  set, NOT flipped on empty clears, doesn't clobber existing
  extras_json keys); (e) MainWindow routing (``_event_is_closed``
  reads the cache, the status-toggle handler round-trips). File name
  dodges the conftest ``_SLICE_B_FILES`` skip list (memory
  `feedback_slice_b_skip_list_swallows_tests`).

**Verified:** ``verify.bat`` **2871 passed / 0 failed** (main, up
from slice 5's 2835 by 31 new pins + the 5 that were elsewhere in
the suite) + **20 / 0** (quarantine). MainWindow construct-smoke OK.
The full sweep is justified — gateway + dashboard + event_card +
main_window + both QSS themes all touched, genuinely cross-cutting
per memory `feedback_scope_tests_to_what_changed`.

**The spec/64 arc is COMPLETE.** Slices 1–6 all landed across one
session each (slices 1–5 on the previous session; slice 6 here),
with the eyeball steps Nelson took during slice 4 confirming the
end-to-end Create Event flow.

**Slice 6 post-build retire (LANDED 2026-06-13, same session).**
Nelson's first eyeball on slice 6 ruled two pieces of the build
overdesigned and called the change:

- **The §5 Header badge retires.** The title-line click already opens
  the Event Header dialog (§2.2) — the badge was a second nudge to the
  same door. Removed: ``_data.header_unset`` field on ``EventCardData``,
  the badge widget in ``EventCard._build_ui``, the
  ``EventCardHeaderBadge`` QSS role in both themes, the
  ``event_classification.header_unset()`` helper, the gateway's
  sticky ``extras_json["header_touched"]`` write inside
  ``set_classification``, and the dashboard's ``_card_data`` rollup
  that fed it. spec/64 §5 retires with a record of the original
  design + the retire reason; §9 acceptance item 9 rewrites.
- **The §2.3 Status badge drops its glyphs.** "✓ Closed" / "● Open"
  → "Closed" / "Open"; colour (via the QSS ``[state=…]`` selector)
  carries the visual cue. The clickable behaviour, the colours, the
  hover treatment, and the toggle wiring are all unchanged.

Tests updated in the same commit: ``test_spec64_tile_updates.py``
sheds the badge-related groups (header_unset pure logic, the badge
visible/absent pins, the gateway sticky-bit matrix) and gains
regression guards for the retire (``test_header_badge_role_retired``,
``test_status_badge_text_carries_no_glyphs``,
``test_set_classification_does_not_write_header_touched_anymore``).
19 pins remain (down from 31).

**Slice 6 closed-tile body redesign (LANDED 2026-06-13, same session).**
Nelson's second eyeball on slice 6 upgraded §2.4's body content from
the slice-6c first cut ("Cuts inside" hint + count) to the two
side-by-side widgets the spec was reaching for:

- ``_PhaseStatsChart`` — the horizontal phase bar chart. Four rows
  (Collected / Picked / Edited / Exported), distinct colours per row
  (slate → blue → amber → emerald, eyeball-tunable), counts + percent
  of Collected on each bar. Always renders all four rows so layouts
  don't jitter card-to-card.
- ``_PhotoCarousel`` — a QLabel-based slideshow that cycles cached
  photo thumbs every 2 s. Timer arms on show + stops on hide so
  off-screen tiles don't tick. Random shuffle on load; resilient to
  corrupt / missing thumbs (drops + tries the next). Falls back to
  a "no photos to show" label when the path list is empty.

``EventCardData`` grew five fields — ``collected_count`` /
``picked_count`` / ``edited_count`` / ``exported_count`` /
``carousel_thumb_paths``; ``cuts_count`` (the slice-6c hint) retires.
``events_dashboard_page._populate_closed_body_data`` is the new
helper that fills them on closed events only — counts come from the
existing gateway methods (``items(kind='photo')``, ``items(phase='pick',
state='picked')``, ``adjustments()``, ``exported_item_ids()``); the
carousel list walks ``photo_thumb_path()`` and filters to thumbs that
ALREADY exist on disk (the dashboard refresh never triggers a fresh
decode). Picked photos preferred; falls back to all photos if no
Picked yet; capped at 15 paths so 5 000-photo events don't blow the
per-card scan.

The click semantics are unchanged — both widgets bubble through the
existing right-zone ``heatmap_clicked`` signal; MainWindow's
``is_closed`` fork still routes to the Cuts list.

Tests added in the same commit: chart paints with mixed + zero counts
without zero-division; carousel arms + stops its timer on show / hide;
carousel drops unreadable paths instead of freezing; ``_card_data``
populates the new fields for closed events only.
