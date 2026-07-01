# 162 — New Cut composition surface redesign

**Status: DESIGN AGREED (Nelson 2026-07-01, design-mode session). The
current `NewRecipeDialog` presents every composition control on one
scrollable panel — Source, Filters, Rules, Otherwise, Runtime, Metrics
— behind two implicit bands ("Which items?" and "What to do with
them?"). Users experience it as an overloaded surface with no visible
sequence. This spec replaces that dialog with a **two-stage** design:
Stage A (Compose) lives inside a single dialog structured as a strict
accordion with two clearly-numbered sections — ① Collection and ②
Format — wrapped in a **Recipe container** that visually delimits what
a saved Recipe captures. Stage B (Plate) is the pick/skip work on
`CutSessionPage`, unchanged from today, reached by clicking a **▶ Freeze
and Pick** primary button on the dialog's launch pad. The redesign also
retires `Save as Collection…` / `Load Collection…` (one savable
formula from now on: the Recipe), collapses `ShareCutsPage` and
`LibraryPage` to a mirrored one-list-plus-Base-Collection-card shape,
and unifies event-scope and cross-event-scope Cut composition into one
dialog widget parameterized by scope. Ancestor: the shelved spec/160,
whose vocabulary rename was pulled back but whose two structural ideas
(three-step story, cross-scope uniformity) survive as this redesign's
frame. No schema migrations. No new specs referenced from the outside;
this is a surface reshape.**

Ancestor + neighbours:
- **[spec/160](160-media-pool-format-cut.md)** — shelved. Its rename
  didn't ship; its structural ideas do.
- **[spec/90](90-cut-recipes-and-collections.md)** — the current dialog
  grammar (Recipe as bundle, DC/Collection as saveable pool). This spec
  retires the DC-save half.
- **[spec/92](92-widget-consolidation.md)** — role catalog. §2 (per-widget
  standard) is binding; this spec adds new roles listed in §12.
- **[spec/93](93-recipe-collection-storage-and-placement.md)** — the
  auto-placement rule survives for Recipes; Collections are retired
  from the user-saveable set (system Collections like `#exported` are
  unaffected).
- **[spec/159](159-exported-collection-review-and-classify.md)** —
  ratings + FilterBar shipped this week; this spec consumes both.

---

## 0. Vocabulary

Locked, unchanged from today's UI:

- **Collection** — a saved pool composition (Source + Filters). After
  this spec, **not saveable by the user any more**; the only remaining
  Collection surfaced to users is the **Base Collection** — the system
  Collection whose members are `#exported`.
- **Base Collection** — the `#exported` universe. Scoped to one event
  at event scope; scoped to the whole library at cross-event scope.
- **Recipe** (or informally *Cut Recipe*) — a saved bundle capturing
  the whole of Stage A (Section 1's filters + Section 2's Format
  choices, and the Section 1 source composition at cross-event scope).
  The **only user-saveable composition artefact after this spec.**
- **Format** — the presentation half of a Recipe: aspect, timing,
  music, transitions, overlays, separators, budget. Section 2 of the
  new dialog.
- **Cut** — a frozen artefact produced from a Recipe by pinning a pool
  and running a picking session.

Move #1 from the design session (finish the stalled `DynamicCollection`
→ `Collection` internal rename) is orthogonal and is NOT scoped by
this spec; it can land before, alongside, or after.

---

## 1. The frame

**Two stages, not three steps.**

- **Stage A — Compose (inside the dialog).** The user composes ①
  Collection and ② Format. Anything the user does here can be saved as
  a Recipe.
- **Stage B — Plate (on `CutSessionPage`).** The user makes their
  individual pick/skip decisions on the composed pool. Per-Cut, never
  saved to a Recipe.

The dialog is Stage A + the transition to Stage B. When the user
clicks the primary button, the dialog closes and `CutSessionPage`
opens with the composed draft — the transition mechanics are
unchanged from today (`start_requested` → `CutSession.from_draft` →
`_start_session`).

The **launch pad** at the bottom of the dialog holds the per-Cut
levers (Name-this-Cut, Rules, Otherwise, start-state toggle) plus the
primary button. Its contents are NOT captured by "Save as Recipe…".

---

## 2. What retires

Landed together as one PR family; nothing here retires in isolation.

**Save/Load Collection scaffolding — all of it, together:**
- `_SaveAsDcNameDialog` (class in `new_recipe_dialog.py`).
- `_LoadDcDialog` (class in `new_recipe_dialog.py`).
- `Save as Collection…` / `Load Collection…` band-header buttons.
- The **Collections tab** on `ShareCutsPage`.
- `mira/ui/pages/cross_event_dcs_dialog.py` (`CrossEventDcsDialog`).
- The **Manage Collections…** entry on `LibraryPage`.
- The `Collection` operand type in the operand picker (cross-event
  Source composition still works — Events, Cuts, Base Collections,
  date ranges remain as operand types).
- Gateway save/load-DC methods retire from the public surface (their
  read-side counterparts survive for the system Base Collection).

**Where user-saved Collections actually live at the data layer.** The
user-saved Collection is stored as a row in the `recipe` table with
`flavour='collection'` (per `mira/shared/recipe_store.py`; the
`FLAVOURS` enum today is `{'cut', 'collection'}`). The `dynamic_
collection` table holds the system Base Collections (`#exported` per
event) — it is **not** touched by this retirement. The data-cleanup
targets are:
- Delete every `recipe` row where `flavour='collection'` (all
  user-saved Collections retire).
- Leave `dynamic_collection` untouched.
- After the delete, every surviving `recipe` row has `flavour='cut'`.
  The `flavour` column can either **stay** (cheap, reversible — the
  CHECK constraint still admits `'collection'` even if no rows carry
  it) or **narrow** to a single value / be dropped as cleanup polish.
  Not required by this spec; a Slice 5 implementation decision.

**Two current cross-event dialog widgets retire (superseded, not just
edited):**
- `mira/ui/pages/cross_event_cuts_dialog.py` (`CrossEventCutsDialog`)
  — modal Cut browser. Superseded by the reshaped `LibraryPage`.
- `mira/ui/pages/cross_event_dcs_dialog.py` (already listed above).

**One current per-event tab retires:**
- The Collections tab on `ShareCutsPage`. The Base Collection card
  keeps its top-of-page spot.

**LibraryPage bands that retire:**
- The Collections band + its Manage-Collections button.
- The Recipes band (Recipes are dialog-only for now — the browsable
  Recipe library is punted to a later spec; see §11).

**One dialog renames:**
- `NewRecipeDialog` (widget file `new_recipe_dialog.py`) → the new
  dialog file. Rename target open; suggestion: `new_cut_dialog.py`.
  The two current constructor flavours (`FLAVOUR_CUT`,
  `FLAVOUR_COLLECTION`) collapse to a single dialog that takes a
  scope argument (`SCOPE_EVENT`, `SCOPE_CROSS_EVENT`). Round 1
  (2026-07-01) added `scope` + `mode` params alongside the legacy
  `flavour` param as an alias during transition; the alias retires
  with the Save/Load Collection retirement.

**Where the standalone "compose a Collection" surface actually
lives.** Discovered during Round 1 review — the `+ New Collection`
UI is INSIDE `mira/ui/pages/cross_event_dcs_dialog.py` (line 267),
NOT on `events_page.py`. The `events_page.py:715` site that opens
the old dialog with `flavour=FLAVOUR_COLLECTION` is
`_pin_cross_event_dc` — a **cross-event Cut composer** (Pin → New
Cut flow) that just happens to use the Collection *face* of the
old dialog as its shape today. It migrates to `scope=
SCOPE_CROSS_EVENT` (mode=`new`) in Round 3, NOT retires. The real
"+ New Collection" button retires when `CrossEventDcsDialog` itself
retires (Round 2's wholesale delete of the file).

**Action button rename:** on Cut rows across every surface, the
`Adjust` ghost button becomes `Edit Cut`. Handler names
(`_on_adjust_cut`) are internal and can stay.

---

## 3. The three surface shapes

### 3.1 ShareCutsPage (event-scope Cuts)

Post-redesign shape (Nelson: mirror at cross-event scope).

```
┌ [pink identity rail — SurfaceHeaderRail[phase="share"]]
│
│                                            [+ New Cut]
│
│ [🌐 #exported · Base Collection
│     N exported files — the universe every cut starts from     [Open]]
│
│ Cuts · 3
│   ▸ Sunday best        (24 items · 3 min · exported)
│   ▸ Client preview     (12 items · 90 s)
│   ▸ Wildlife highlights (18 items · exported)
```

Retired from today: the `QTabWidget` (`ShareTabs`), the `Collections`
tab, the `#ShareTabPane` accent-bordered pane wrapping the Cuts tab
content. Cuts sit as a flat list under the Base Collection card. The
`+ New Cut` primary button stays in its header-row position.

### 3.2 LibraryPage (cross-event Cuts)

Identical shape to §3.1, mirrored:

```
┌ [pink identity rail — same colour, cross-event scope]
│
│                                            [+ New Cut]
│
│ [🌐 #exported · Base Collection · library
│     N exported files across M events                          [Open]]
│
│ Cuts · 2
│   ▸ Wildlife best of all time  (32 items · exported)
│   ▸ Portraits across trips     (12 items)
```

Retired from today's `LibraryPage`: the Cross-event Cuts band header
(now the flat header of §3.1), the Collections band + its
`Manage Collections…` button, the Recipes band. Collapses from three
`SurfaceBand`s to the one-card-plus-list shape.

The **Base Collection · library** card's `Open` button navigates to a
new library-scope `DCDetailPage` (or a scope-parameterized version of
today's `DCDetailPage`) — a flat grid over every `Exported Media/`
file across every event. Ratings + FilterBar (spec/159) work the same;
the FilterBar's dimension set is the extended cross-event set (see
§7.2).

### 3.3 New Cut / Edit Cut dialog

The core of this spec. Full anatomy in §4.

---

## 4. The dialog: anatomy

Reference sketch (New Cut, event scope, freshly opened):

```
┌─ New Cut ─────────────────────────────────────────────  ×
│
│  ┌────────────── R E C I P E ─────────────────────────┐
│  │  Recipe: (new · unsaved)   [Load Recipe…]  [Save as Recipe…]
│  │  ─────────────────────────────────────────────────
│  │
│  │  ▾ ① Collection · 137 files
│  │      Starting from your Base Collection
│  │      Filters: Stars · Colours · Flags · Style · Media
│  │      Showing 137 of 137 · Clear all
│  │
│  │  ▸ ② Format · 16:9 · 3 min · ambient · 2 overlays
│  └────────────────────────────────────────────────────┘
│
│  ── per-Cut choices (not part of the Recipe) ─────────
│  Name this Cut:  [_________________________________]
│  Rules · 0                                       [+ Add rule]
│  Starts all:  ( ● Picked  |    Skipped )
│
│  137 files · 5 min 20s · budget: OK · 0 warnings
│                                ┌ Cancel ┐  ┌ ▶ Freeze and Pick ┐
└──────────────────────────────────────────────────────
```

### 4.1 Header bar

Migrates from today's `_build_header_bar` with two changes:

- **Icon tile** — one glyph across both scopes and both modes (New /
  Edit). No scope-conditional glyph swap (today's dialog swaps
  `GLYPH_CROSS_EVENT` vs `GLYPH_CUT` for the two flavours; that
  distinction retires with the flavour collapse).
- **Title text** — `New Cut` on new-mode; `Edit Cut · [Cut Name]` on
  edit-mode.
- Close X, unchanged.

### 4.2 The Recipe container

**New QSS role**: `#RecipeContainer`. Visual frame wrapping the entire
Stage A — the Recipe toolbar row + the two accordion sections — with
a visible boundary so the user reads at a glance *"everything inside
this frame is what a saved Recipe captures."*

- Header row inside the frame: `Recipe: [name or "(new · unsaved)"]`
  on the left; `Load Recipe…` and `Save as Recipe…` ghost buttons on
  the right. The Recipe name is inline-editable (click to rename
  without opening the Save-as dialog).
- Below the header, a divider hairline separates the header from the
  accordion body.
- Below the divider: the accordion (§4.3, §4.4).

Container's visual tone must read as clearly-bounded but not as a
modal-within-a-modal. Suggested treatment: a soft accent-tinted
outline + a very subtle wash (a lighter shade of `SectionBox`). Exact
values live in spec/92 Appendix B once the role is written up there.

### 4.3 Section 1 · Collection (accordion)

The `▾ ① Collection · N files` accordion header expands to reveal:

**Event scope** (`_scope == SCOPE_EVENT`):
```
   Starting from your Base Collection
   Filters:  Stars · Colours · Flags · Style · Media
   Showing 137 of 137 · Clear all
```

- **"Starting from" line** — non-editable subhead caption at event
  scope. Purely mental-model reinforcement; there's no operand
  picker here.
- **Filter dimensions** at event scope: **Stars, Colours, Flags,
  Style, Media**. Five knobs. No Camera / Lens / Dates / Places.
- **FilterBar** — reuse `mira/ui/exported/filter_bar.py` verbatim.
  The `LineageFilter` predicate covers Stars / Colours / Flags
  already; Style and Media extend the predicate.

**Cross-event scope** (`_scope == SCOPE_CROSS_EVENT`):
```
   Starting from ▸  [Event: Zambia 2024] or [Event: Botswana 2023]
                    or [Base Collection · Namibia 2022]     [+ or…]
   Filters:  Stars · Colours · Flags · Style · Media ·
             Camera · Lens · Dates · Places
   Showing 412 of 1,847 · Clear all
```

Two differences from event scope:
1. **"Starting from"** becomes a composable source sentence — a chip
   stack of operands joined by `or` / `and` / `minus`, with a
   trailing `[+ or…]` affordance opening the existing operand picker
   popover (migrates verbatim from today's dialog).
2. **FilterBar dimensions** extend with **Camera, Lens, Dates,
   Places** — four extra knobs. The predicate widens accordingly.

Section 1 accordion header shows a live summary chip: `Collection · N
files` where N is the live-resolved count. Updates on every predicate
change.

**No header buttons on Section 1** (Load/Save Collection is retired).

### 4.4 Section 2 · Format (accordion)

Flat layout when expanded (§4.4 confirmed: not nested). Three rows in
a 2-column grid pattern — Nelson's existing spec/152 grouping,
consolidated one row shorter than today's four-row grid:

```
▾ ② Format · 16:9 · 3 min · ambient · 2 overlays

    ☐ Set a runtime budget
    Budget          [Target (min): 3 ]      [Max (min): 5 ]
    Timing          [Per photo: 3.0 s]      [Transition: 0.5 s]
    Presentation    [Aspect: 16:9    ▾]     [Music: Ambient  ▾]
                    [Overlays: When·Where]  [Separators: on · cards]
```

**Three rows** (previously four in the grid; the "Show" / "Format"
row Nelson called out as name-clashing with Section 2's title merges
into Presentation):

1. **Budget** — the two spinners `Target (min)` and `Max (min)`,
   plus the `☐ Set a runtime budget` checkbox above the row. When
   unchecked, **the whole Budget row hides** (not merely disables).
2. **Timing** — `Per photo (s)` + `Transition (s)`, unchanged
   controls, unchanged spinner shape.
3. **Presentation** — 2×2 sub-grid:
   - Top: `Aspect` combo + `Music` combo.
   - Bottom: `Overlays` box + `Separators` box.
   - The Overlays box carries the four caption-field checkboxes
     `When · Where · Camera · Exposure` (both scopes), plus the extra
     `Source label per slide` checkbox at cross-event scope only
     (spec/154 — unchanged from today).

Row order: **natural** — Budget → Timing → Presentation (as it reads
in code today).

Section 2's collapsed summary chip surfaces the significant Format
choices — see §7.3 for defaults, which drive the summary text.

### 4.5 Accordion behaviour

**Strict.** Exactly one section may be expanded at a time. Clicking
Section 2's header collapses Section 1 and expands Section 2 (and
vice versa). The header of the currently-expanded section shows the
▾ chevron; the collapsed one shows ▸. When the dialog opens fresh,
Section 1 is the expanded one.

Header summary chips (§4.3, §4.4) remain visible in both collapsed
and expanded states — the user always sees the current pool count and
the current Format condensation.

### 4.6 Launch pad

**New QSS role**: `#LaunchPad`. Visually distinct from
`#RecipeContainer` — no border, tighter background (a soft ink-tinted
strip that reads as "outside the Recipe frame"). Contents:

```
── per-Cut choices (not part of the Recipe) ──────────────
   Name this Cut:  [_________________________________]

   Rules · N                                       [+ Add rule]
     ⋮ 1  If  [predicate chip stack]   →  Pick   [×]     (if N > 0)
     ⋮ 2  If  [predicate chip stack]   →  Skip   [×]
   [Starts all: | Otherwise:]  ( ● Picked  |    Skipped )

   137 files · 5 min 20s · budget: OK · 0 warnings
                              ┌ Cancel ┐  ┌ ▶ Freeze and Pick ┐
```

**Name this Cut** — a single-line text input. Prefilled empty on New
Cut; prefilled with the Cut's current name on Edit Cut.

**Rules** — the existing ordered list of `(predicate, verdict)` rows,
migrating verbatim (`_RuleRow`, `_RuleDragHandle`, `_VerdictPill`).
Drag-to-reorder unchanged.

**Otherwise / Starts all** — the same underlying `_VerdictPill`
control, but its leading **label flexes** based on the rule count:

- Rules count **== 0** → leading label reads **"Starts all:"**
- Rules count **>= 1** → leading label reads **"Otherwise:"**

Underlying value + storage unchanged.

**Summary strip** — a one-line ready-state readout, above the button
row. Composition:

- Pool size (`N files`).
- Estimated total duration (`5 min 20s`).
- Budget status (`budget: OK` / `budget: over by 40s` /
  `budget: none`, depending on the Budget row + composition).
- Warning count (`0 warnings` / `⚠ 2 warnings` — the latter styled
  with a warning tone; details expand on click or hover).

**Buttons** — the button-row layout differs per mode (§5).

### 4.7 The transition to Stage B

Unchanged from today. On primary-button click:

1. Build a `CutDraft` from the composition (existing
   `_on_start_clicked` logic; the launch pad's per-Cut levers flow
   into the draft the same way they do today).
2. `start_requested.emit(draft)`.
3. Dialog `accept()`s.
4. Parent (`ShareCutsPage._on_new_cut` /
   `ShareCutsPage._on_adjust_cut` / their `LibraryPage`
   counterparts) receives the draft, constructs a
   `CutSession.from_draft`, swaps `_stack` to the `CutSessionPage`.

No new signals; no new draft payload fields.

---

## 5. Two invocation modes

Same dialog widget, two entry paths.

### 5.1 New Cut mode

- Opened via `+ New Cut` on `ShareCutsPage` (event scope) or
  `LibraryPage` (cross-event scope).
- Header title: `New Cut`.
- Sections + launch pad all start empty / defaults.
- Primary button label: **`▶ Freeze and Pick`**.
- Cancel button label: **`Cancel`**.

### 5.2 Edit Cut mode

- Opened via the `Edit Cut` ghost button on a Cut row.
- Header title: `Edit Cut · [Cut Name]`.
- Everything prefilled from the Cut's frozen state:
  - Recipe header: `Recipe: [origin name if any, else "(bespoke)"]`.
  - Section 1 filters / source composition from the Cut.
  - Section 2 Format values from the Cut.
  - Launch pad: Name from `Cut.name`, Rules from `Cut.rules`,
    Otherwise from `Cut.otherwise_verdict`.
- Primary button label: **`▶ Save Changes and Pick`**.
- Cancel button label: **`Discard Changes`**.
- On primary click: same `start_requested` path; the parent knows the
  target Cut id and routes to `CutSession.for_cut_with_draft` rather
  than `CutSession.from_draft` (existing behaviour, unchanged).

---

## 6. Recipe scope-portability rule

Recipes carry a `scope` field ∈ {`event`, `cross-event`}. Rule:

1. **Event-scope Recipes are reusable across events.** They store
   filter values + Format defaults + (an abstract "the current event's
   Base Collection" reference — NOT a specific event's Base
   Collection). Loading into any event's New Cut dialog resolves the
   reference to that event's Base Collection.
2. **Cross-event Recipes stay cross-event.** They store the full
   Source composition (specific event / Cut / Base Collection
   operands, join words, date ranges) + filters + Format defaults.
   Loadable only inside cross-event New Cut dialogs.

**Load Recipe picker filters by current dialog scope.** From an
event-scope dialog the picker shows only event-scope Recipes; from
cross-event, only cross-event ones. **The user never sees a Recipe
that doesn't fit where they are** — no mismatch warnings, no
cross-scope portability, no need for a "originally cross-event" note.

**Load Recipe leaves the launch pad untouched.** Loading a Recipe
overwrites Stage A (Section 1 filters + Section 2 Format) only. The
Cut name, Rules, Otherwise on the launch pad persist across the load —
they are per-Cut, not per-Recipe.

**Save as Recipe captures Stage A only.** The launch pad's contents
are never written to a Recipe.

---

## 7. Cross-event vs event differences (consolidated)

Same dialog widget, one scope parameter drives the deltas.

### 7.1 Section 1

| | Event scope | Cross-event scope |
|---|---|---|
| Starting-from line | Non-editable caption `Starting from your Base Collection` | Composable source sentence + operand picker |
| Filter dimensions | Stars · Colours · Flags · Style · Media (5) | Above five plus Camera · Lens · Dates · Places (9) |

### 7.2 Base Collection card (surface, not dialog)

| | Event Cuts surface (ShareCutsPage) | Cross-event Cuts surface (LibraryPage) |
|---|---|---|
| Card title | `#exported · Base Collection` | `#exported · Base Collection · library` |
| Subtitle | `N exported files — the universe every cut starts from.` | `N exported files across M events` |
| Open target | Per-event `DCDetailPage` (today) | Scope-parameterized `DCDetailPage` (library-wide flat grid) |

### 7.3 Section 2 (Format) defaults

Cross-event Cuts are search-result-first, not presentation-first: the
user's primary interest is the grid of results, not playing the Cut
as a slideshow. Defaults reflect that:

| Row | Event Cut default | Cross-event Cut default |
|---|---|---|
| Budget row | Shown · Target 3 min · Max 5 min | **Hidden** (checkbox off; row collapses away) |
| Timing / Per photo | 3.0 s | 3.0 s |
| Timing / Transition | 0.5 s | **0.0 s (hard cuts)** |
| Presentation / Aspect | 16:9 | 16:9 |
| Presentation / Music | Ambient category default | **No music** |
| Presentation / Overlays | `When` on; others off | `When` on · `Where` on · `Source label per slide` **on** |
| Presentation / Separators | On · standard cards | **Off** |

Section 2's collapsed summary chip reads accordingly:

- Event: `Format · 16:9 · 3 min · ambient · 2 overlays`
- Cross-event: `Format · 16:9 · no budget · 4 overlays · search defaults`

The user can open Format at cross-event scope and change any of these
— nothing is disabled. The defaults simply respect the primary use
case.

---

## 8. FilterBar reuse

`mira/ui/exported/filter_bar.py` + `mira/ui/exported/filter_popup.py::
LineageFilter` (spec/159 §4.5, shipped 2026-06-30) are the reusable
substrate. This spec consumes them as-is at event scope. Extensions
needed for cross-event scope:

- `LineageFilter` gains four fields: `cameras: set[str]`,
  `lenses: set[str]`, `date_from: Optional[date]`, `date_to:
  Optional[date]`, `places: set[str]`. Each defaults to a
  match-anything state; `matches` is extended accordingly.
- `FilterBar` gains four dimension widgets (Camera / Lens / Dates /
  Places pickers). They're feature-gated by a `scope` property on the
  bar; when `scope == "event"` the four widgets are not created.

The `Showing N of M` indicator and `Clear all` action carry through
unchanged. FilterBar remains a standalone reusable widget — three
callers after this spec: `DCDetailPage` (event scope, event Base
Collection detail), the new library `DCDetailPage` variant
(cross-event scope, library-wide flat grid), and Section 1 of the new
Cut dialog.

---

## 9. What a Recipe stores

Structurally unchanged from today's `RecipeStore` (the `recipe` table
schema in `mira/user_store/`), with one added column:

- `name` — user-provided (existing).
- `flavour` — existing; stays as `'cut'` on every surviving row after
  Slice 5's cleanup. See §2 for the storage-layer picture: user-saved
  Collections are `flavour='collection'` rows in this same table and
  they retire in Slice 5.
- `scope` — **new column**: `TEXT NOT NULL CHECK (scope IN ('event',
  'cross-event'))`. Required so §6's picker-filtering can key on it.
  Backfill: every existing row → `scope='event'` (confirmed safe:
  cross-event Cut composition today lives in `NewCrossEventDcDialog`,
  which has no Save-as-Recipe path — no cross-event Recipe rows
  exist).
- `filters_json` — the `LineageFilter` snapshot (extended fields
  serialize to `null` at event scope).
- `source_expr_json` — the operand composition at cross-event; a
  placeholder token (`"@current_event_base_collection"` or similar)
  at event scope. Existing storage shape survives.
- `presentation_json` — Section 2's values: budget-on flag, target,
  max, per-photo, transition, aspect, music category, overlay field
  set, source-label flag, separators-on flag, separators style.

Explicitly **not stored**: Cut name, Rules, Otherwise verdict. Those
are per-Cut.

---

## 10. Files affected

### 10.1 New files (or major rewrites)

- `mira/ui/pages/new_cut_dialog.py` — the redesigned dialog. Replaces
  `new_recipe_dialog.py` (rename + heavy internal rewrite).
- `mira/ui/design/accordion.py` (proposed) — the reusable accordion
  container, since none exists today. `#AccordionHeader` +
  `#AccordionSection` roles.
- `mira/ui/design/recipe_container.py` (proposed) — the
  `#RecipeContainer` frame widget (§4.2). May land inline in
  `new_cut_dialog.py` if a full module is overkill.

### 10.2 Files to substantially rewrite

- `mira/ui/pages/share_cuts_page.py` — retire the `QTabWidget` +
  Collections tab; simplify to flat Cuts list under the Base
  Collection card.
- `mira/ui/pages/library_page.py` — collapse from three-band shape to
  the ShareCutsPage-mirror shape (§3.2).
- `mira/ui/shared/dc_detail_page.py` — accept a scope parameter so a
  single class serves both event-scope and library-scope drill-down.
  Alternatively, keep two subclasses.
- `mira/ui/exported/filter_bar.py` + `filter_popup.py` — extend for
  cross-event dimensions (§8).

### 10.3 Files to retire

- `mira/ui/pages/cross_event_cuts_dialog.py` (whole file).
- `mira/ui/pages/cross_event_dcs_dialog.py` (whole file).
- Inside `new_cut_dialog.py`: the `_SaveAsDcNameDialog` and
  `_LoadDcDialog` classes retire (no external users after the
  Collections retirement).

### 10.4 Gateway retirements

- `Gateway.save_dc(...)` / `Gateway.load_dc(...)` — the user-facing
  save/load DC methods retire. Read-side methods survive for the
  system `#exported` Base Collection.
- `Gateway.list_user_collections(...)` (or equivalent) — retires.
- Cross-event DC list method — retires.

### 10.5 Test file impact

- `tests/test_new_recipe_dialog.py` — retargets to the new dialog
  file; the two-flavour tests collapse to one-scope-parameterized
  tests.
- Tests hitting `_SaveAsDcNameDialog` / `_LoadDcDialog` — retire.
- Tests on `CrossEventCutsDialog` / `CrossEventDcsDialog` — retire.
- Tests on `ShareCutsPage.Collections tab` — retire.
- New tests: scope-based Recipe picker filtering; strict-accordion
  behaviour; contextual Otherwise/Starts-all label; Budget-row
  hide-on-uncheck; cross-event FilterBar dimension extension.

---

## 11. Deliberate deferrals

Not blockers for shipping this redesign; captured so a later spec can
pick them up.

- **Recipe library surface.** Recipes are dialog-only after this spec
  (Load Recipe inside the dialog is the only user path to browse
  them). A dedicated surface for renaming / deleting / previewing
  saved Recipes is deferred. Follow-up spec candidate: a `Recipes`
  page reached from the app menubar.
- **Recipe rename outside Save-as-Recipe.** The inline-editable
  Recipe name in the container header (§4.2) is nice-to-have; if it
  costs too much per-widget behaviour it can defer to open-a-tiny-
  rename-dialog behaviour.
- **The "originally cross-event" scope note.** Removed by design
  (§6). If Nelson later wants cross-scope Recipe portability with
  warnings, that's a new decision, not a return to the note.
- **Cross-event Source operand picker polish.** The composable source
  sentence at cross-event scope migrates verbatim from today. Any
  UX polish on the operand picker itself is a separate spec.

---

## 12. QSS role additions

New roles this redesign needs — added to `assets/themes/redesign.qss`
and documented in spec/92 §2 + Appendix B. **No inline
`setStyleSheet` in any widget module.**

| Role | Where used | Notes |
|---|---|---|
| `#RecipeContainer` | Frame around Stage A in the dialog | Accent-tinted outline + subtle wash; header divider inside |
| `#RecipeContainerHeader` | Toolbar row inside `#RecipeContainer` | Recipe name + Load/Save Recipe buttons |
| `#AccordionSection` | Each of the two Sections (Collection, Format) | Card-style; dynamic property `expanded="true|false"` drives ▾/▸ chevron via QSS |
| `#AccordionHeader` | The clickable row that toggles expand/collapse | Hover + pressed states; summary chip embedded on right |
| `#AccordionSummaryChip` | The right-aligned summary chip in an accordion header | Tone-variant of existing `#Chip` |
| `#StartingFromRow` | The "Starting from" line at Section 1 top | Non-editable label at event scope; chip strip + `+ or…` at cross-event |
| `#StartingChip` | Anchor chip at cross-event Source composition | Tone-variant of `#Chip[tone="universe"]` |
| `#LaunchPad` | Container for per-Cut controls at bottom of dialog | Ink-tinted, no border; visually outside the `#RecipeContainer` |
| `#LaunchPadRow` | Labelled row inside `#LaunchPad` (Name / Rules / Otherwise) | Left label + right control cluster |
| `#LaunchPadSummaryStrip` | One-line ready-state readout above the button row | `tone="warn"` when warnings > 0 |
| `#WarningStrip` | Inline warning-styled readout | Reused inside `#LaunchPadSummaryStrip` when warnings present |
| `#RulesList` | The rules-container inside the launch pad | Migrates the existing rules layout |

Roles that **survive from today** (no change needed): `#SectionBox`,
`#Chip`, `#Sub`, `#Micro`, `#CardTitle`, `#DialogClose`, `#Faint`,
`#CutHeaderTile`, `#PoolSummary`, and every existing operand-picker /
rule-row / verdict-pill / join-chevron role.

Roles that **retire from today** (no user after retirement):
`#ShareTabs`, `#ShareTabPane` (both die with the Collections tab
retirement), any `#Collections*` bands on LibraryPage.

---

## 13. Implementation phasing suggestion

The redesign is large enough to want breaking. Suggested slices, in
order — each slice ships behind a feature check where practical so
partial states remain usable:

1. **Slice 1 · QSS role catalog additions.** Land the new roles in
   `redesign.qss` + spec/92 Appendix B updates. No widget code
   changes. Guardrails: `test_no_inline_qss.py` baseline stays green.
2. **Slice 2 · Reusable accordion + Recipe-container primitives.**
   Build the `#AccordionSection` + `#RecipeContainer` widgets under
   `mira/ui/design/` with their own unit tests. No caller changes
   yet.
3. **Slice 3 · New Cut dialog scaffolding.** Rename
   `new_recipe_dialog.py` → `new_cut_dialog.py`. Replace `_build_body`
   with the two-stage shape (Recipe container + launch pad). Preserve
   all existing behaviours (Source, Filters, Rules, Otherwise, all
   Format controls) behind the new visual layout. `NewRecipeDialog` →
   `NewCutDialog`. Ship green.
4. **Slice 4 · FilterBar extension for cross-event.** Add the four
   dimension widgets + `LineageFilter` field extensions. Wire the new
   dialog's Section 1 to the extended bar.
5. **Slice 5 · Retire Save/Load Collection.** Remove the two dialogs
   (`_SaveAsDcNameDialog`, `_LoadDcDialog`), the band buttons, the
   Collections tab on `ShareCutsPage`, the LibraryPage Collections
   band, the cross-event DCs dialog, the gateway save/load-DC
   methods. **Data cleanup**: one-shot migration that deletes every
   `recipe` row with `flavour='collection'` (per §2, §9). Leaves
   `dynamic_collection` untouched. This is the biggest single-slice
   change on the surface side; the data delete is small and
   irreversible — call it out in the migration's docstring.
6. **Slice 6 · Collapse LibraryPage to the ShareCutsPage-mirror
   shape.** Retire `CrossEventCutsDialog`; make LibraryPage the
   home for cross-event Cuts with the same shape as event
   ShareCutsPage.
7. **Slice 7 · Recipe scope field + picker filtering.** Add the
   `scope` column to the `recipe` table (`TEXT NOT NULL CHECK (scope
   IN ('event', 'cross-event'))`); v(N)→v(N+1) migration in
   `mira/user_store/`. Backfill: every existing row → `scope='event'`
   (confirmed safe per §9 — no cross-event Recipes exist today).
   Update `RecipeStore.list(...)` to accept a `scope` filter; update
   the `_LoadRecipeDialog` picker to pass the current dialog's scope
   when populating.
8. **Slice 8 · Edit Cut mode wiring.** Ensure the `Edit Cut` entry
   from Cut rows hits the new dialog with proper prefill; verify the
   button labels flex per mode (§5.2).
9. **Slice 9 · Adjust → Edit Cut rename sweep.** UI string sweep on
   the row action label. Small.
10. **Slice 10 · Cross-event Format defaults.** Land the scope-aware
    default table (§7.3) — the dialog seeds Section 2 differently
    per scope.

Slices 1-3 unblock every other slice. Slices 5-6 have the biggest
retirement blast radius and are the most cautious to sequence — best
landed together as a "retire the DC-save world" PR family.

---

## 14. What this spec does NOT cover

- **`CutSessionPage` (Stage B) internals.** Unchanged from today.
- **Cut playback / export.** Unchanged.
- **Move #1 (`DynamicCollection` → `Collection` internal rename).**
  Orthogonal; owns its own decision.
- **Recipes browsable surface.** Deferred (§11).
- **Base Collection ratings input flow.** Ratings are input in the
  editor (spec/159); this spec only consumes rating filters on the
  compose surface.
- **PTE overlay authoring / rendering.** Overlay control set is
  described (§7.3, spec/153/154); rendering is unchanged.
