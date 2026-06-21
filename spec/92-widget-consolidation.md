# spec/92 — Widget consolidation (one role per widget type, one stylesheet)

**Authored 2026-06-20 (Nelson + Claude). A UI standard + migration plan.
Child of [spec/05](05-ui-standards.md) (UI grammar), informed by the
`MiraCrafter Redesign/` mockups (the widget *voice*) and
[spec/65](65-redesign-fidelity-pass.md) / [spec/70](70-new-ui-completion-plan.md)
(the redesign program). Extends spec/05 §5.1; does not contradict it.**

> **Read this before touching QSS.** The headline fear is "consolidation will
> break the code." It will not, if the staging here is followed. QSS +
> `setObjectName` changes are **visual only** — they do not touch signal/slot
> wiring, gateway calls, or data flow. Functionality lives in Python and is
> never edited by a role swap. The two places where a mistake *could* change
> appearance (a widget losing a role it leaned on, or a property-collapse whose
> value doesn't match the new selector) are exactly what the before/after
> smoke-render gate (§7) catches. Every stage is independently shippable and
> independently revertible.

---

## §0. The problem (measured, 2026-06-20)

Many agents built many surfaces; each invented its own object names and, too
often, its own inline styles. The result is role explosion with no shared
abstraction, plus a forbidden-but-unenforced inline-style leak. Concrete
numbers from the current tree:

**Role proliferation** (distinct `#Role` selectors per stylesheet):

| File | `#Role` selectors |
|---|---|
| `assets/themes/redesign.qss` | **98** |
| `assets/themes/dark.qss` | **189** |
| `assets/themes/light.qss` | **190** |

**Inline `setStyleSheet` in `mira/ui/`** (CLAUDE.md + spec/05 §5.1 forbid these):
**58 calls across 26 files.** Of those, ~22 embed **hardcoded hex / rgba**
(`#1e222d`, `#211f3a`, `#262b38`, `#c0c0c0`, `#6b4d00`/`#fff4d6`/`#d6b96e`,
`#9ca3af`, `rgba(...)`) that **do not theme-switch** — they stay dark in light
mode. These are the visible "broken on theme flip" bugs.

**Container-role sprawl.** "A box that holds stuff" is implemented as ~18
independent `QFrame` roles in `redesign.qss` alone (`Card`, `Card2`, `TileCard`,
`StatTile`, `SectionCard`, `SourceSection`, `FiltersSection`, `RulesSectionCard`,
`OtherwiseSectionCard`, `RuntimeSectionCard`, `MetricsSectionCard`, `NameBox`,
`ScopeBox`, `WhichItemsBand`, `WhatToDoBand`, `CrossEventBand`, `RecipeToolbar`,
`ShareTabPane`), plus 5 state-border roles (`StatePicked/Skipped/Compare/
Mixed/Neutral`) that differ only in border colour. None vary from a shared base
— they are all independent definitions.

### §0.1 What is NOT broken (and changes the plan)

The audit's framing of "legacy dark/light are drifting duplicates" is only half
right. The real architecture is healthier than it looks, and the consolidation
target **already exists in embryo**:

1. **There is already a single token source.** `mira/ui/palette.py` holds one
   `PALETTE` dict (dark + light) of ~21 colour tokens + a `RADIUS` scale
   (`sm/md/lg/xl`). `mira/ui/theme.py::resolve_theme_colors()` flattens it,
   adds legacy aliases (`primary→accent`, `text→ink`, `window→bg`, …) and
   computed hover/pressed variants.
2. **`redesign.qss` is already the single-source model.** It is **one**
   theme-agnostic template using `{token}` placeholders; `palette.py::
   build_redesign_qss(theme)` substitutes per theme. One file, both themes. This
   is the destination — it is proven and in production.
3. **Dynamic-property styling is already the house pattern.** ~20 attribute
   selectors are live in QSS (`[phase=…]`, `[state=…]`, `[status=…]`,
   `[active=…]`, `[zone=…]`, `[muted=…]`, …) driven by **48 `setProperty`
   calls** + **32 `unpolish/polish` pairs** in `mira/ui/`. Collapsing roles via
   properties is not a new technique to introduce — it is an existing technique
   to apply consistently.
4. **The blessed primitive library already exists.** `mira/ui/design/` exports
   (via `__all__`) the canonical widgets and factories: `primary_button /
   ghost_button / danger_ghost_button`, `line_input / search_field / select`,
   `Card / Card2 / StatTile`, `chip_* / tag / pill_toggle`, `StageProgress`,
   `Donut`, `Thumb / ThumbGrid`, `PageHeader / SurfaceIdentityHeader /
   ThemeToggle`, `MiraMark / MiraLogo`, `MessageDialog / ProgressDialog /
   confirm / show_*`. The problem is **adoption** — surfaces hand-roll instead
   of calling these — not absence.

So the legacy `dark.qss` / `light.qss` are two parallel **template** files
(structurally mirrored, token-filled). They are still a real cost — every rule
edited twice, every drift a theme-specific bug, 202 roles each — but the fix is
to **migrate their roles into the single `redesign.qss` model and retire them**,
not to rebuild a token system that already works.

### §0.2 The 13-vs-65 reality (why this is widget-level, not surface-level)

The `MiraCrafter Redesign/` mockups cover **13 surfaces**. The app now has
**~50 page/dialog modules and ~65–70 distinct surfaces** (cross-event dialogs,
recipes, calibration, backup, wizard steps — none mockup'd). Therefore:

> **The standard is defined per widget type, derived from the design primitives
> and the mockups' widget *voice* — then applied uniformly across all surfaces.
> We do NOT audit surface-by-surface against 13 HTML files.** The mockups tell
> us how a Card / Button / Chip / Input should feel; the code tells us the full
> surface inventory those roles must serve.

### §0.3 Grounded in the rendered surfaces (2026-06-20, pixel pass)

The first audit read QSS + Python only. A pass over the actual rendered surfaces
(the live app's initial surface + the `scripts/smoke_*.py` renders, light + dark)
confirms the diagnosis and sharpens the scope: **the surfaces are right; the
widgets are not uniform.**

**Already consistent — this is the canonical look to protect, not change:**

- **Buttons.** Primary (accent) vs Ghost pairing reads identically on every
  surface — `Search` / `+ New Event` / `Save event` / `Start` / `Apply days` /
  `Export green` / `+ Start a new pass…` (Primary) and `Back` / `Cancel` /
  `Filters` / `+ Collection` / `Pick all` / `Skip all` / `Adjust` (Ghost).
- **Cards / frames, phase-identity badges** (QUICK SWEEP blue · PICK accent ·
  EDIT amber · EXPORT green), and the **§5a state-legend chips** are uniform.

**Inconsistent — the widget-level work this spec targets:**

0. **The `QGroupBox` — the spark for this whole review.** The named, titled group
   box is Mira's canonical way to present input (spec/05 §3b), and it renders
   **inconsistently across surfaces**. The **New Cut** surface
   (`Desktop/New cuts surface.png`) is the reference: every field sits inside a
   titled box (`NAME`, `SOURCE`, `FILTERS`, `RUNTIME`, `METRICS`), and boxes
   **nest** to group related fields (`Which items?` → `SOURCE` + `FILTERS`).
   Event Header (smoke 02) instead shows **bare `NAME *` / `TYPE *` labels above
   unframed inputs** — the group box is missing entirely. Where group boxes *do*
   appear, their frame weight / title treatment / padding drift. Unifying the
   `QGroupBox` look and using it on **every** input is the centre of gravity of
   this spec, not a footnote.
1. **Spinboxes diverge from text fields and dropdowns.** Event Header's
   `DURATION` spinbox renders a lighter outline + stepper chrome unlike its
   sibling line-inputs/combos; New Cut's `PER PHOTO` / `TARGET` / `MAX` steppers
   differ again from the plain `MUSIC` field. (`QSpinBox` lacks a unified role —
   §2.2 `DesignSpin`.)
2. **Section-header rule is applied unevenly.** Event Header draws an accent
   hairline under `IDENTITY` / `LOGISTICS`; New Cut's `POOL` / `STYLE` eyebrows
   have none. (One `SectionEyebrow` treatment, rule included.)
3. **Utility buttons clip.** Event Days Table's `Browse..` renders as
   `3rowse..` — a non-canonical small-button size. (Fold into `Ghost`, size to
   content per spec/05 §4c.)
4. **Inline table-cell inputs** (Days Table) carry their own border treatment
   distinct from dialog inputs. (Generalise into the `DesignInput` family with a
   `[context="cell"]` property only if geometry truly differs.)
5. **Theme-breaking local overrides** (the §0 hardcoded-hex inline styles) stay
   dark in light mode.

**The standard is reaffirmed, not relaxed.** **spec/05 §3b is correct and
load-bearing: every input field is presented inside a named, titled `QGroupBox`
— never a label to the left of the input, never a bare label floating above it.**
Group boxes serve two jobs, both canonical: (a) wrap a **single** field (the
title is the field name), and (b) **group related fields** (the title names the
group), and they **nest** (New Cut). The work here is to define **one** group-box
look and bring every input onto it — including the surfaces that currently skip
the box (Event Header). Bringing a non-conforming input onto the group-box
standard is widget consistency, not a surface redesign; the *layout* of those
surfaces is untouched.

---

## §1. Principles

0. **Consolidation is appearance-preserving by definition — for conforming
   surfaces.** The goal is "one definition behind an unchanged look," not a
   restyle. A surface that already shows the canonical widget (the initial app
   surface; the cards; the button pairing) must render **pixel-identical** after
   migration — any visible change there is a bug, caught by the §7 render gate.
   The surfaces that *do* change are only the ones currently deviating, and they
   change **toward** the canonical widget, never away from it.

0b. **Surfaces are out of scope; widgets are in scope.** This spec never touches
   layout, composition, spacing structure, or which widget sits where. It
   touches only how a given widget *type* is styled, removes local overrides so
   every instance draws from one role, and — for inputs — ensures each sits in
   the canonical titled `QGroupBox` (§2.3.1). Reference/"golden" surfaces define
   the canonical look and are protected baselines in the smoke set: the live
   **initial app surface**, **New Cut** (`Desktop/New cuts surface.png` — the
   group-box reference), **Phases**, and **Share/Cuts**. Note: **Event Header is
   a *violator*, not a reference** — it will *gain* group boxes to match the
   standard; that is the intended widget change, not a surface redesign.


1. **One canonical role per widget purpose.** For every widget *job* (primary
   action, secondary action, body container, nested container, stat tile, form
   field, search field, status chip, section band, …) there is exactly **one**
   role name and **one** QSS definition. Variation is expressed by **Qt dynamic
   properties** on that role, never by a new sibling role.
2. **One stylesheet source.** All roles live in the single token-substituted
   `redesign.qss` model. Legacy `dark.qss` / `light.qss` are emptied of roles
   and retired. There is one place to edit a rule, and it themes both modes.
3. **One token vocabulary.** Every colour and radius is a `palette.py` token.
   No hex/rgba literal appears in QSS or in widget code (the documented
   slideshow-canvas exception in §6 aside).
4. **Style is data-driven, not code-driven.** A widget declares *what it is*
   (`setObjectName`) and *what state it is in* (`setProperty` + repolish). It
   never declares *how it looks* (`setStyleSheet`).
5. **Primitives over re-rolling.** A surface composes `mira/ui/design`
   primitives. New visual treatment is added to a primitive (so every caller
   inherits it), not to a surface.
6. **Locked semantics are untouchable.** The §5a photo-state colours
   (`picked`=green, `skipped`=red, `compare`=orange, `mixed`=yellow) keep their
   fixed meaning and are never re-mapped or themed away (spec/63, design-system
   §5a). The locked keyboard map (spec/63 §4) is irrelevant to styling and
   unaffected.
7. **Every stage ships green and reverts clean.** No stage depends on a later
   one. Consolidation is a sequence of small, verified commits, not a rewrite.

---

## §2. The canonical widget standard (THE role per type)

This is the destination catalog. Each entry names the **one** role for that job,
its **blessed primitive** in `mira/ui/design/`, and the **dynamic property** that
expresses variants. Roles not listed here are slated for collapse into one of
these (mapping in §4 / Appendix A). **The exact pixel values for every role —
casing, border style/width/colour-token, radius-token, padding, bg/text tokens,
and per-state behaviour — are pinned in Appendix B; that table is the
enforceable standard.**

### §2.0 Global typography & casing rule (applies to all widgets)

Casing is part of the standard, not left to whoever writes the string:

- **UPPERCASE** (with `letter-spacing: 0.4–0.5px`): micro-eyebrows / **group-box
  titles** / table column headers / phase-identity badges. These are the
  "labelling chrome" tier.
- **Sentence/Mixed case**: page & card titles (`PageTitle`, `EventTitle`,
  `CardTitle`), body (`Sub`, `Label`, `Faint`), **button labels** ("Save event",
  "Load Recipe…"), input text, status chips (`Open`/`Closed`), and lowercase
  content tags (`macro`, `wildlife`).
- **i18n discipline (CLAUDE.md #5):** uppercasing is applied by the QSS
  `text-transform` where the widget honours it (e.g. `QHeaderView::section`) or
  by the primitive's factory at set-time for `QLabel` (Qt does not apply
  `text-transform` to `QLabel`). The `tr()` source string is **always written in
  normal case** so translators never hand-type caps. Never bake casing into the
  literal.

Font family is `"Segoe UI", "Inter", sans-serif` everywhere (one declaration on
`#RedesignRoot`); no surface overrides the family. Sizes/weights come only from
the Appendix B ladder.

### §2.1 Buttons — `QPushButton`

| Role | Job | Primitive | Variants (property) |
|---|---|---|---|
| `Primary` | the one accent CTA | `primary_button()` | — |
| `Ghost` | every secondary / toolbar action | `ghost_button()` | `[active="true"]` (toggled-on) |
| `DangerGhost` | destructive secondary | `danger_ghost_button()` | — |
| `PillToggle` | segmented / on-off choice | `pill_toggle()` | `:checked` |
| `IconButton` | bare glyph button (kebab `⋮`, close ✕, nav arrows) | `nav_arrow()` / factory | `[shape="kebab\|close\|arrow"]` |
| `ThemeToggle` | the theme pill | `ThemeToggle` | — |

`TileMore`, `DialogClose`, `MediaNavArrow`, `CarouselArrow`, `DayPillNav`,
`PoolStepperBtn` collapse into `IconButton[shape=…]`. The legacy button zoo
(`BatchOpButton`, `ListButton*`, `FilterChip`, `SubfolderChip*`, `FeatureToggle`,
`PlanBrowseCell`, …) maps to `Ghost` (+ `[active]` where it was a toggle).

### §2.2 Inputs — `QLineEdit` / `QComboBox` / `QSpinBox` / `QPlainTextEdit`

| Role | Job | Primitive |
|---|---|---|
| `DesignInput` | text field | `line_input()` |
| `DesignSelect` | dropdown | `select()` |
| `DesignSpin` | numeric stepper | factory |
| `DesignText` | multi-line | factory |
| `SearchField` | search with leading glyph | `search_field()` |

All five must share **identical chrome** (card2 bg, line border, `{radius_md}`,
same height) — the rendered offender today is `DesignSpin`, whose stepper box
diverges from `DesignInput`/`DesignSelect` (§0.3.1). Bringing the spinbox onto
the shared input chrome (styling `::up-button`/`::down-button` in the one role)
is the single highest-value input fix.

**Form layout: every input lives in a named, titled `QGroupBox`** (spec/05 §3b,
reaffirmed — see §2.3.1). No label-to-the-left, no bare label-above-input. A
single field is wrapped in its own titled box; related fields are wrapped in a
shared titled box; boxes nest (New Cut). The five input roles above always sit
*inside* such a box.

Legacy `SliderValueField`, `AdjustmentValue`, `ProcessStyleCombo`,
`ProcessFilterCombo`, `ProcessAspectCombo`, `VideoExtraCombo`, `VideoSpeed`,
`DaysCellInput`, `DaysCellSelect` collapse into the five roles above (+ a
`[context="cell"]` property only where geometry genuinely differs, e.g. the
Days-Table inline cells of §0.3.4).

### §2.3 Containers — `QFrame` / `QWidget`

The big collapse. Three base container roles + property modifiers replace ~18:

| Role | Job | Primitive | Variants (property) |
|---|---|---|---|
| `Card` | primary surface panel (xl radius, soft shadow) | `Card` | `[level="2"]` → nested (card2 bg, lg radius); `[flat="true"]` → no shadow |
| `Tile` | small stat / cover tile | `StatTile` / `Thumb` | `[tone="stat\|cover"]` |
| `SectionBox` | a bordered grouping band inside a dialog/form | (new factory) | `[tone="plain\|accent\|metrics\|runtime"]` |
| `Divider` | 1px hairline separator | (factory) | `[axis="h\|v"]` |
| `StateBorder` | the §5a decision-state ring on a cell | `Thumb` border | `[state="picked\|skipped\|compare\|mixed\|neutral"]` |

Collapse map:
`Card2` → `Card[level="2"]`; `TileCard`, `StatTile` → `Tile[tone=…]`;
`SectionCard`, `SourceSection`, `FiltersSection`, `RulesSectionCard`,
`OtherwiseSectionCard`, `RuntimeSectionCard`, `MetricsSectionCard`, `NameBox`,
`ScopeBox`, `WhichItemsBand`, `WhatToDoBand`, `RecipeToolbar` → `SectionBox[tone=…]`;
`StatePicked/Skipped/Compare/Mixed/Neutral` → `StateBorder[state=…]` (this one
exactly mirrors the `GridTile[state=…]` / `DayGridCell[status=…]` precedent
already in the legacy QSS).

`CrossEventBand` and `ShareTabPane` are genuinely distinctive surfaces — they
may keep dedicated roles **if** they are documented in Appendix A as deliberate
exceptions; otherwise they fold into `Card`/`SectionBox`.

#### §2.3.1 The `QGroupBox` — Mira's input-presentation primitive (the spark)

This is the most important role in the catalog, because its inconsistency is what
prompted the review. **One canonical titled-group-box look, used for every
input.** Exactly two roles (already shipped per spec/05 §3b), both `QGroupBox`:

| Role | Job | Notes |
|---|---|---|
| `FormFieldGroup` | the primary titled box around a field (single) or a set of related fields | title = field/group name; input(s) inside the frame; **nests** |
| `FilterRailGroup` | the compact titled box for narrow filter/chip rails | same look, tighter padding |

**Root cause (the values that drift).** "Named box" is currently implemented two
different ways: the legacy **real `QGroupBox#FormFieldGroup`** (border
`{border_subtle}`, radius 6, title *notched into the border*, weight 500) and the
redesign **`QFrame#NameBox`/`#SourceSection`/…** (a frame, border `{line}`, radius
`{radius_sm}`, with a *separate* eyebrow `QLabel` weight 700 caps sitting inside).
Same concept, two looks — and that is the drift. The standard collapses both onto
**one real `QGroupBox`**.

**Canonical look (locked 2026-06-20 — Nelson: "my call, keep it consistent"):**
- **Real `QGroupBox` with the title embedded in the top border line**
  (`::title` subcontrol, `subcontrol-origin: margin; subcontrol-position: top
  left`). Not a separate floating eyebrow label.
- **Title** = UPPERCASE micro-caps, `{ink_soft}`, `font-weight: 700`,
  `letter-spacing: 0.5px` (the §2.0 "labelling chrome" tier; uppercase applied by
  the factory, `tr()` source stays normal case). Required-field `*` is a property
  on the box, rendered in `{accent}`, never a separate label.
- **Frame** = `1px solid {line}`, `border-radius: {radius_md}px`, `background:
  transparent` — **identical at every nesting level. No quieter inner frames.**
  Hierarchy is carried by the titles + spacing alone (honouring the existing
  `redesign.qss` decision, Nelson 2026-06-20). A group box always looks like a
  group box.
- **Every input field is inside one of these.** No `QLineEdit`/`QComboBox`/
  `QSpinBox`/`QPlainTextEdit` is presented bare, side-labelled, or over-labelled.
- **`FilterRailGroup`** is the same look with compact padding only (no other
  difference).

Exact values in Appendix B. Reference render: `Desktop/New cuts surface.png`
(golden) — the canonical `QGroupBox` is tuned to read like it.

### §2.4 Type scale — `QLabel`

One semantic ladder; everything else maps onto it:

| Role | Size / weight | Job |
|---|---|---|
| `PageTitle` | 30 / 800 | page H1 |
| `EventTitle` | 24 / 800 | event/section H1 |
| `CardTitle` | 18 / 700 | card/panel heading |
| `Sub` | 13 / ink_soft | subtitle / secondary line |
| `Label` | 13 / 600 | field labels, captions |
| `Micro` | 11 / 700 caps | eyebrow / micro-heading |
| `Faint` | ink_faint | hints, empty/0% state |

Phase-coloured percentages stay as `Pct` + `[phase="collect\|pick\|edit\|export"]`
(already the pattern). The ~70 legacy bespoke label roles (`WelcomeTitle`,
`PageHeading`, `EventCard*`, `Counter*`, `Shortcut*`, …) map onto this ladder;
where one needs a colour accent, that becomes a property, not a new role.

**`SectionEyebrow`** — the in-dialog section header (`IDENTITY`, `LOGISTICS`,
`POOL`, `STYLE`): `Micro` accent caps **with the accent hairline rule beneath,
always**. This is one role so the rule can never again be present on one dialog
and absent on another (§0.3.2).

### §2.5 Chips, tags, pills — `QLabel` / `QPushButton`

| Role | Job | Primitive | Variants |
|---|---|---|---|
| `Chip` | status pill | `chip_*()` | `[tone="open\|closed\|done\|prog\|idle"]` |
| `Tag` | metadata tag | `tag()` | — |
| `OverlayChip` | translucent on-photo chip | factory | `[kind="exported\|count\|badge"]` |

`ChipOpen/Closed/Done/Prog/Idle` collapse into `Chip[tone=…]`; `ExportedBadge`
folds into `OverlayChip[kind="exported"]`.

### §2.6 Progress, tables, headers

- **`QProgressBar` → `StageBar`** + `[state="done\|prog\|skip"]` (primitive
  `StageProgress`). Legacy `SharpnessBar`, `CutBudgetBar` map here.
- **Data tables → `DataTable`** (generalise `EventDaysTable`): one themed table
  chrome (header `Micro` caps, line gridlines from `{line}`, accent-soft
  selection). `past_photos_cameras` gridline hex bug fixed by adopting this.
- **Headers** stay as the spec/71 trio: `PageHeader`, `SurfaceIdentityHeader`
  (the phase rail + badge + purpose line), `TitleBar` (brand + menu + theme
  toggle). Phase identity is property-driven (`[phase=…]`) and **chrome-only** —
  never mixed with §5a state colour (spec/71 one-rule, restated).

### §2.7 The token + radius vocabulary (locked source: `palette.py`)

Colour: `bg card card2 ink ink_soft ink_faint line card_border accent
accent_soft green amber red pink blue track bg_glow picked skipped compare
mixed`. Radius: `sm=8 md=11 lg=14 xl=18`. Shadow: `shadow_alpha`.

No new token is added without a line in this section. No literal colour appears
outside this dict.

---

## §3. The single-stylesheet target

End state:

1. `redesign.qss` is the **only** role-bearing stylesheet, holding the full
   canonical catalog (§2 + Appendix A), all `{token}`-substituted.
2. `dark.qss` / `light.qss` are deleted (their unique roles migrated in;
   their legacy aliases already live in `theme.py::resolve_theme_colors()` and
   stay there as the compatibility shim until callers are renamed).
3. `theme.py::apply_theme()` applies the single substituted sheet. The
   legacy-template `.format_map()` branch is removed once the legacy files are
   empty.
4. Painted widgets (`Donut`, `StageProgress`, `Thumb`, `MiraMark`) keep reading
   `PALETTE` live in `paintEvent` — unchanged; they already theme correctly.

This is the same mechanism `redesign.qss` uses today, scaled to cover everything.

---

## §4. Migration — staged, safest first

Each stage is one or a few commits, ends green, and is revertible alone. Ordering
is cheapest-and-safest → structural. **Nothing in stages 1–2 changes a role
name**, so they cannot break a working surface; they only move existing styling
into existing/new roles.

### Stage 0 — Freeze + tooling (no visual change)
- Add the **CI guard** (§7) rejecting new inline `setStyleSheet(` in `mira/ui/`.
- Render the **baseline smoke set** (`scripts/smoke_*.py`, light + dark) and
  archive the PNGs as the before-reference.
- Land this spec. Snapshot the current role catalog into Appendix A.

### Stage 1 — Kill the theme-breaking inline hex (highest user-visible payoff)
Replace the ~22 hardcoded-colour `setStyleSheet` calls with token-based roles.
These are the "looks broken in light mode" bugs. Concrete targets:
`_event_card_redesign.py` / `_event_tile.py` (`#1e222d`),
`_cross_event_band.py` / `share_cuts_page.py` (`#211f3a`, `#7c6cff`),
`dialogs.py` divider+steps (`#262b38`, `#34d399`, `#7c6cff`),
`sync_pair_picker.py` / `collect_photo_picker.py` preview pane (`#c0c0c0`),
`camera_clock_dialog.py` warning banner (`#6b4d00/#fff4d6/#d6b96e`),
`past_photos_cameras.py` gridline (`#9ca3af`), `carousel.py` dots host.
One commit per pattern. Some land on roles that already exist (`DialogDivider`,
`DialogClose`, `CrossEventBand`); the rest need 3–4 small new roles
(`PreviewPane`, `MessageTile[intent]`, `WarningBanner`, `StepLabel[status]`).

### Stage 2 — Move the remaining (token-using) inline styles into roles
The other ~36 inline calls already use tokens, so they are not theme-broken, but
they still violate the rule and re-implement shared patterns. Convert to roles /
existing primitives. After this stage the CI guard runs clean and `setStyleSheet`
survives only in the documented exceptions (§6).

**Also resolve the rendered widget-uniformity offenders found in §0.3** (these
are role-definition gaps, not just inline styles):
- **`QGroupBox` (the headline).** Define the one canonical `FormFieldGroup` /
  `FilterRailGroup` look in QSS (border, radius, embedded title, padding) and
  verify the golden New Cut surface renders identically. [§0.3.0, §2.3.1]
- **Spinbox chrome** → unify `DesignSpin` onto the `DesignInput` chrome
  (`::up-button`/`::down-button` styled in the one role). [§0.3.1]
- **Section eyebrow** → introduce `SectionEyebrow` (caps + rule, always) and
  adopt it on every dialog. [§0.3.2]
- **`Browse..` clip** → route to `Ghost`, size to content. [§0.3.3]
- **Days-Table inline cells** → `DesignInput[context="cell"]`. [§0.3.4]
Each is gated on a before/after render of the affected dialog.

### Stage 2b — Adopt the group box on every input that skips it
Wrap the inputs on non-conforming surfaces (Event Header first; then any other
bare-label form) in the canonical titled `QGroupBox`. This edits `.py` to add the
box around existing fields — it changes the *widget*, not the surface layout or
field order. Gated hardest on before/after renders (the surface's other widgets
must stay pixel-identical; only the input framing appears). One surface per
commit, `verify.bat` green each time.

### Stage 3 — Collapse container + chip + state roles via properties
Apply §2.3 / §2.5. For each family: add the base role + property selectors to
`redesign.qss`, switch call-sites from the old role name to the base role +
`setProperty(...)` + repolish, then delete the retired role definitions. This is
the first stage that **renames roles**, so it is gated hardest on smoke renders.
Do one family per commit (state-borders first — smallest and already
property-shaped; then chips; then section boxes; then cards).

### Stage 4 — Fold legacy `dark.qss` / `light.qss` into `redesign.qss`
Migrate each still-referenced legacy role into the single template (most map onto
§2 canonical roles; a residue of truly surface-specific roles moves verbatim).
When a legacy file has no remaining referenced roles, delete it and remove its
branch from `theme.py`. Keep the `resolve_theme_colors()` legacy aliases as the
shim. This is structural but mechanical; smoke renders gate every surface.

### Stage 5 — Reconcile docs
Update **spec/05 §5.1** to point at this catalog as the canonical role list;
fold the redundant role lists. Update CLAUDE.md's QSS section to name the single
stylesheet. Mark superseded role names. Re-run the full smoke set as the
after-reference and diff against Stage 0.

> Stages 1–2 are pure cleanup with near-zero structural risk and immediate
> visible payoff (theme bugs gone). They can ship before any decision on the
> aggressive stages 3–4. If appetite changes, stopping after Stage 2 still
> leaves the codebase materially better and fully consistent on tokens.

---

## §5. What each stage may and may not touch

- **May:** `assets/themes/*.qss`; `setObjectName` / `setProperty` calls;
  `mira/ui/design/` primitives (to add shared treatment); deletion of retired
  role definitions and, in Stage 4, of the legacy QSS files.
- **May NOT:** any signal/slot connection, gateway call, engine logic, layout
  *structure* (which widget is parented where), the keyboard map, the §5a
  colour semantics, or the captured-tree invariants. If a change would touch
  Python behaviour, it is out of scope for this spec.

This boundary is the answer to "will it break functionality": the migration is
confined to the presentation layer, and the presentation layer has a render-based
safety net.

---

## §6. Documented exceptions (intentional, keep)

- **`shared/cut_play.py` slideshow canvas.** The show surface is deliberately
  black/translucent in any theme (it is the slideshow, not app chrome). The
  existing in-file comment stays; the CI guard whitelists this file via an
  explicit `# pragma: no-qss` marker per line.
- **Painted widgets reading `PALETTE` in `paintEvent`** (`Donut`,
  `StageProgress`, `Thumb`, `MiraMark`, brand) are not `setStyleSheet` and are
  the correct pattern for canvas-drawn elements.
- **Dynamic colour composed from data** (e.g. a brand swatch whose colour comes
  from a `Brand` record) may set that one property inline **only** when the
  colour is genuinely data-derived and not a token; prefer a `[swatch]` role
  with the colour passed as a property where feasible.

---

## §7. Verification gates (the safety net — mandated)

Every stage must pass, in order:

1. **CI inline-style guard.** A pre-commit / CI check fails on any new inline
   style in `mira/ui/`:
   ```
   grep -rEn 'setStyleSheet\(' mira/ui --include='*.py' \
     | grep -v '# pragma: no-qss'
   ```
   (exit non-zero ⇒ build fails). Lands in Stage 0; protects every later stage.
2. **Before/after smoke renders.** Run the `scripts/smoke_*.py` set (light +
   dark) before and after the stage; eyeball the PNG pairs for every covered
   surface. Any unintended visual delta blocks the commit. This is the primary
   net for stages 3–4 (the role-renaming ones).
3. **`verify.bat` green** for any stage that edits `.py` (stages 1–4). QSS-only
   edits still run the suite to catch role-name assertions in tests.
4. **Floor check** at 1280×720 (spec/05 §4c) for any surface whose container
   role changed.

**Coverage gap to close first:** the smoke set (16 scripts) covers the main
decision surfaces but **not** most dialogs, the wizard, or the cross-event
flows. Before Stage 3 touches a surface lacking a smoke script, add a minimal
smoke render for it, or sign it off by manual screenshot. Track coverage so no
role-collapse lands on an unrendered surface.

---

## §8. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| A widget loses a role it relied on for sizing/border | low | smoke render diff (§7.2); revert-per-stage |
| Property-collapse: `setProperty` value doesn't match new selector ⇒ unstyled widget | medium (stage 3) | one family per commit; render every affected surface; keep old + new roles co-resident until call-sites switched, then delete |
| A surface with no smoke script regresses unseen | medium | §7 coverage-gap rule: add smoke or manual signoff before touching it |
| Legacy alias removed too early ⇒ unresolved `{token}` | low | keep `resolve_theme_colors()` aliases until Stage 5; substitution failure is loud (literal `{token}` visible) |
| Functionality regression | very low | §5 boundary forbids touching Python behaviour; `verify.bat` gate |
| Scope creep into a full restyle | medium | stages 1–2 are the floor; 3–4 are opt-in; this spec is consolidation, not redesign |

---

## §9. Definition of done

1. `redesign.qss` is the only role-bearing stylesheet; `dark.qss`/`light.qss`
   deleted; `theme.py` applies one substituted sheet.
2. Zero inline `setStyleSheet` in `mira/ui/` except the §6 pragma-marked
   exceptions; CI guard enforces it.
3. Every widget job maps to exactly one canonical role (§2 + Appendix A);
   variants are properties, not sibling roles.
3b. **Every input field is presented inside the one canonical titled `QGroupBox`**
   (`FormFieldGroup` / `FilterRailGroup`, §2.3.1) — no bare or side/over labels
   remain on any surface; the group-box look is identical everywhere (verified
   against the New Cut golden render).
4. No colour/radius literal outside `palette.py`.
5. Full smoke set renders identically (modulo intended changes) before vs after,
   light + dark; `verify.bat` green.
6. spec/05 §5.1 and CLAUDE.md updated to reference this catalog.

---

## §10. Open decisions for Nelson

1. **Aggressiveness checkpoint.** Stages 1–2 (cleanup) are unambiguous wins.
   Stages 3–4 (role collapse + legacy-file retirement) are the structural part.
   Confirm we run the whole program, or pause after Stage 2 and reassess.
2. **Keep-or-fold the distinctive surfaces.** `CrossEventBand` and
   `ShareTabPane` — collapse into `Card`/`SectionBox`, or keep as documented
   dedicated roles? (They are visually deliberate; either is defensible.)
3. **Smoke-coverage investment.** How far to extend the smoke harness to dialogs
   / wizard before Stage 3 — full coverage (slower, safest) vs. main surfaces +
   manual signoff for the long tail.

---

## Appendix A — canonical role catalog (snapshot 2026-06-20)

The canonical role per widget purpose, after Stages 3a–3d and Stage 4a. The
**Variants** column lists the Qt dynamic properties that select sub-styles
(empty when the role has none); **Retires** lists the legacy sibling roles
the canonical role replaces (deleted from `redesign.qss` per the migration
commits in the brackets). Roles defined only in legacy `dark.qss`/`light.qss`
that survive the Stage 4a dead-role purge but have not yet migrated to
`redesign.qss` are listed at the end (legacy-migration history per §A.9).

### A.1 Buttons (`QPushButton`)

| Canonical role | Primitive (in `mira/ui/design`) | Variants | Retires |
|---|---|---|---|
| `#Primary` | `primary_button()` | — | — |
| `#Ghost` | `ghost_button()` | `[active="true"]` (toggled-on) | `BatchOpButton`, `ListButton*`, `FilterChip`, `SubfolderChip*`, `FeatureToggle`, `PlanBrowseCell` (legacy aliases retired in Stage 1-2 / 4a) |
| `#DangerGhost` | `danger_ghost_button()` | — | — |
| `#DangerPrimary` | factory | — | `DangerButton` (Stage 1) |
| `#PillToggle` | `pill_toggle()` | `:checked` | — |
| `#ThemeToggle` | `ThemeToggle` | — | — |
| `#TileMore` / `#DialogClose` / `#MediaNavArrow` / `#CarouselArrow` / `#DayPillNav` / `#CutRowKebab` / `#PoolStepperBtn` | various | shape-specific | (kept as named primitives; the §2.1 `IconButton[shape]` collapse is deferred to a later sweep) |

### A.2 Inputs (`QLineEdit` / `QComboBox` / `QSpinBox` / `QPlainTextEdit`)

| Canonical role | Primitive | Variants | Retires |
|---|---|---|---|
| `#DesignInput` | `line_input()` | — | `SliderValueField`, `AdjustmentValue` (legacy) |
| `#DesignSelect` | `select()` | — | `ProcessStyleCombo`, `ProcessFilterCombo`, `ProcessAspectCombo`, `VideoExtraCombo` (legacy) |
| `#DesignSpin` | factory | — | (chrome unified onto the shared input look in Stage 1-2 — `::up-button`/`::down-button` styled inside the role) |
| `#DesignText` | factory | — | — |
| `#SearchField` | `search_field()` | — | — |
| `#DaysCellInput` / `#DaysCellSelect` | factory | — | (Surface 04 inline-cell variants; `[context="cell"]` collapse is the §2.2 future state) |

**Every input lives inside a `#FormFieldGroup`** per §2.3.1 (Event Header still pending — spec/92 Stage 2b).

### A.3 Containers (`QFrame` / `QGroupBox`)

| Canonical role | Variants | Retires (collapsed in this program) |
|---|---|---|
| `#Card` (level-1 primary surface) | — | — |
| `#Card[level="2"]` | `level="2"` | `Card2` ([9f49dbf]) |
| `#Tile` | `[tone="stat\|cover"]` | `StatTile` → `[tone="stat"]`, `TileCard` → `[tone="cover"]` ([9f49dbf]) |
| `#StateBorder` (the §5a photo-state ring) | `[state="picked\|skipped\|compare\|mixed\|neutral"]` | `StatePicked` / `StateSkipped` / `StateCompare` / `StateMixed` / `StateNeutral` ([8eb665a]) |
| `#SectionBox` (dialog section family) | semantic identity on the `section` Qt property | `NameBox` / `ScopeBox` / `SourceSection` / `FiltersSection` / `RulesSectionCard` / `OtherwiseSectionCard` / `RuntimeSectionCard` / `MetricsSectionCard` / `WhichItemsBand` / `WhatToDoBand` / `RecipeToolbar` / `SectionCard` ([de2a556]) |
| `#FormFieldGroup` (`QGroupBox`) — the canonical titled input wrapper | `[required="true"]` (future) | (the input-presentation primitive; legacy `FormFieldGroup` in dark/light.qss is shadowed by the redesign rule) |
| `#FilterRailGroup` (`QGroupBox`) | — | (compact-padding variant of FormFieldGroup) |
| `#IconTile` (small rounded glyph holder) | `[tone="accent"]`, `[bordered="true"]` | category tiles, cross-event glyph, share globe (unified in Stage 1) |
| `#ShareListRow` / `#ShareTabPane` / `#CrossEventBand` | — | (kept as distinctive Share-surface roles per §2.3 deliberate-exception clause) |
| `#PreviewPane` | — | hardcoded preview-pane inline styles (Stage 1) |
| `#TzSuggestionBanner` | — | hardcoded amber-banner inline styles (Stage 1) |
| `#PoolChipHost` / `#PoolChipName` / `#PoolChipCount` | — | (recipe-dialog pool chip surface) |
| `#CutHeaderTile` / `#DialogDivider` | — | (dialog scaffolding) |

### A.4 Type scale (`QLabel`)

| Canonical role | Size / weight | Job |
|---|---|---|
| `#PageTitle` | 30 / 800 | page H1 |
| `#EventTitle` | 24 / 800 | event/section H1 |
| `#CardTitle` | 18 / 700 | card/panel heading |
| `#Sub` / `#Label` | 13 | secondary line / captions |
| `#Micro` | 11 / 700 caps | eyebrow / micro-heading |
| `#Faint` | inherit | hints, empty states |
| `#SectionEyebrow` | 10 / 800 caps + rule | dialog section header (Stage 1) |
| `#StepLabel` | `[status="done\|now"]` | ProgressDialog stepper (Stage 1) |
| `#Pct[Collect\|Pick\|Edit\|Export\|Zero]` | phase token | pipeline percentages |

### A.5 Chips / tags / badges

| Canonical role | Variants | Retires |
|---|---|---|
| `#Chip` | `[tone="open\|closed\|done\|prog\|idle"]` | `ChipOpen` / `ChipClosed` / `ChipDone` / `ChipProg` / `ChipIdle` ([95d0b11]) |
| `#Tag` | — | — |
| `#OverlayChip` / `#ExportedBadge` | — | — |
| `#SurfaceHeaderBadge` | `[phase="collect\|pick\|edit\|export\|share"]` | (per-phase identity badge) |

### A.6 Progress, tables, headers

| Canonical role | Variants | Notes |
|---|---|---|
| `#StageBar` (`QProgressBar`) | `[state="done\|prog\|skip"]` | (`StageProgress` primitive) |
| `#EventDaysTable` (`QTableWidget`) | — | Surface 04 themed chrome — generalises into spec/92 §2.6 `DataTable` |
| `#PastPhotosTable` | — | Stage 1 token-driven gridline; folds into `DataTable` next |
| `#DaysTableCheck` (`QCheckBox`) | — | generalises into `DesignCheck` |
| `#SurfaceHeaderRail` (`QFrame`) | `[phase="collect\|pick\|edit\|export\|share"]` | spec/71 phase rail |
| `#SurfaceHeaderPurpose` / `#SurfaceHeaderReminder` (`QLabel`) | — | spec/71 |
| `#TitleBar` (`QWidget`) + `#TitleMenuBar` (`QMenuBar`) + `#Wordmark` | — | shell chrome |
| `#ShareTabs` (`QTabWidget`) | — | borderless documentMode + accent underline |

### A.7 Painted (no QSS — read `PALETTE` in `paintEvent`)

`Donut`, `StageProgress` (paint), `Thumb`, `MiraMark`, brand swatches.
These are NOT `setStyleSheet` and are the correct pattern for canvas-drawn
elements (spec/92 §6 documented exception).

### A.8 Tokens (the only colours allowed) — `palette.py`

`bg card card2 ink ink_soft ink_faint line card_border accent accent_soft
green amber red pink blue track bg_glow picked skipped compare mixed` ·
radius `sm=8 md=11 lg=14 xl=18` · `shadow_alpha`. The handful of
`rgba(...)` literals in chip tints and translucent overlays are the only
sanctioned non-token colours.

### A.9 Legacy role migration — DONE (Stage 4b/c/d completed 2026-06-21)

Stage 4a removed the 73 truly-dead legacy roles. Stages 4b/c/d then
folded the 100 still-referenced legacy roles into `redesign.qss`:

- **Stage 4b** (commit `472d1b4`): extended
  `palette.py::build_redesign_qss` to accept a tokens dict, and wired
  `theme.py::apply_theme` to pass the full `resolve_theme_colors()`
  output so the migrated rules could keep using legacy aliases
  (`{window}`, `{text}`, `{primary_hover}`, …) without translation.
- **Stage 4c slice 1** (commit `0edbb40`): mechanical migration of
  233 rules whose bodies were identical between dark.qss / light.qss
  (only the `{token}` substitution layer differed per theme). The
  helper `scripts/_migrate_legacy_qss.py` walks both files in tandem
  and migrates matching pairs verbatim (the only edit is unescaping
  `{{` / `}}` → single `{` / `}`).
- **Stage 4c slice 2** (commit `09c9071`): the 28 holdouts — 19
  divergent-body rules + 4 dark-only + 5 light-only. The divergent
  ones got per-theme aliases added to `resolve_theme_colors()`
  (`primary_disabled_text`, `statusbreakdown_bg`, `status_open_*`,
  `type_default_bg + type_{trip,session,occasion,project}`,
  `info_{bucket,camera,day}{_hover}`); the single-theme ones moved
  unconditionally. dark.qss + light.qss shrank to 7-line deprecation
  stubs.
- **Stage 4d** (commit `4f921af`): the irreversible step.
  Deleted dark.qss + light.qss; retired the `_load_qss_template()`
  helper and `.format_map()` branch in `theme.py::apply_theme`. The
  `resolve_theme_colors()` legacy-aliases shim stays in place — any
  caller (or any migrated rule) that still references
  `{window}` / `{text}` / `{primary_hover}` resolves as before.

`redesign.qss` is now the only role-bearing stylesheet
(spec/92 §3 #1, §9 #1, §9 #6 all green).

---

## Appendix B — the canonical widget style standard (pixel-precise)

Every value below is a token from `mira/ui/palette.py` (so both themes resolve
from one source). `md=11 lg=14 sm=8 xl=18` are radius tokens. Values are taken
from the current `redesign.qss` where a canonical definition already exists, and
pinned where it does not. **This table is the enforceable standard; any widget
that deviates is a bug.**

### B.1 Containers (`QFrame` / `QGroupBox`)

| Role | Casing | Background | Border | Radius | Notes |
|---|---|---|---|---|---|
| `Card` | — | `{card}` | `1px solid {line}` | `xl` (18) | soft shadow via `QGraphicsDropShadowEffect` (`shadow_alpha`) |
| `Card[level="2"]` (was `Card2`/`StatTile`) | — | `{card2}` | `1px solid {line}` | `lg` (14) | nested surface, stat tile |
| `Tile` (cover/event) | — | `{card}` | painted 2px stroke (Thumb pattern) | per painter | border painted in `paintEvent`, not QSS |
| **`FormFieldGroup`** (`QGroupBox`) | **title UPPERCASE**, 11px/700, `0.5px`, `{ink_soft}` | `transparent` | `1px solid {line}` | `md` (11) | title notched in top border (`::title` top-left, `left:10px; padding:0 6px`); **same weight at all nesting levels**; `[required="true"]` → `*` in `{accent}` |
| `FilterRailGroup` (`QGroupBox`) | as above | `transparent` | `1px solid {line}` | `md` (11) | compact padding only |
| `Divider` | — | `{line}` | none | 0 | `max/min-height: 1px` (h) or width (v) |
| `StateBorder[state]` | — | transparent | `3px solid {picked\|skipped\|compare\|mixed\|line}` | `md` (11) | §5a colours locked; `state=neutral`→`{line}` |

### B.2 Buttons (`QPushButton`)

| Role | Casing | Background | Text | Border | Radius | Padding | Weight | States |
|---|---|---|---|---|---|---|---|---|
| `Primary` | Sentence | `{accent}` | `#ffffff` | none | `md` | `11px 18px` | 700 | hover: `{accent}` lightened (add real hover); disabled: dim |
| `Ghost` | Sentence | transparent | `{ink}` | `1px solid {line}` | `md` | `8px 14px` | 600 | hover: bg `{card2}` + border `{accent}`; pressed: `{card2}`; `[active="true"]`: border+text `{accent}`; disabled: `{ink_faint}` |
| `DangerGhost` | Sentence | transparent | `{ink}` | `1px solid {line}` | `md` | `8px 14px` | 600 | hover: border+text `{red}` |
| `PillToggle` | Sentence | `{card2}` | `{ink}` | `1px solid {line}` | 16 (pill) | `6px 14px` | 600 | `:checked`: `{accent_soft}` + border+text `{accent}` |
| `ThemeToggle` | Sentence | `{card2}` | `{ink}` | `1px solid {line}` | 14 | `4px 12px`, min-w 56 | 600 | hover: border `{accent}` |
| `IconButton` (folds `TileMore`/`DialogClose`/`CutRowKebab`/`MediaNavArrow`/`CarouselArrow`/`DayPillNav`/`PoolStepperBtn`) | glyph | transparent (or translucent overlay variant) | `{ink_soft}` | none or `1px solid {line}` | 6–11 | `0`–`0 6px`, square min (22–44) | 700–800 | hover: `{accent_soft}` bg or `{accent}` border; `[shape]` selects the variant |

### B.3 Inputs (`QLineEdit` / `QComboBox` / `QSpinBox` / `QPlainTextEdit`)

| Role | Background | Text | Border | Radius | Padding | min-h | Focus |
|---|---|---|---|---|---|---|---|
| `DesignInput` / `DesignSelect` / `DesignSpin` / `DesignText` | `{card2}` | `{ink}` | `1px solid {line}` | `md` (11) | `8px 12px` | 20 | `1px solid {accent}` |
| `SearchField` | `{card2}` | `{ink}` | `1px solid {line}` | `md` | `8px 12px 8px 34px` | 22 | `1px solid {accent}` |
| `DesignInput[context="cell"]` (Days-Table) | `{card2}` | `{ink}` | `1px solid {line}` | `sm` (8) | `6px 9px` | 18 | border `{accent}` |

Spinbox `::up-button`/`::down-button` and combo `::drop-down`(`width:22px`,no
border)+`::down-arrow`(`chevron_down.svg`,12px) are styled **inside these roles**
so a number field, a dropdown and a text field read identically except for their
affordance glyph. Combo popup `QAbstractItemView`: `{card}` bg, `1px {line}`,
`md` radius, item hover/selected `{accent_soft}`/`{accent}`. **All inputs sit
inside a `FormFieldGroup` (B.1).**

### B.4 Type scale (`QLabel`)

| Role | Casing | Size / weight | Colour | Notes |
|---|---|---|---|---|
| `PageTitle` | Mixed | 30 / 800 | `{ink}` | |
| `EventTitle` | Mixed | 24 / 800 | `{ink}` | |
| `CardTitle` | Mixed | 18 / 700 | `{ink}` | `padding-bottom:4px` (descenders) |
| `Sub` / `Label` | Mixed | 13 / 400–600 | `{ink_soft}` | |
| `Micro` / `SectionEyebrow` | **UPPERCASE** | 11 / 700, `0.5px` | `{ink_soft}` | `SectionEyebrow` adds the accent hairline rule beneath |
| `Faint` | Mixed | inherit | `{ink_faint}` | hints, 0%/empty |
| `Pct[phase]` | — | inherit / 700 | phase token (`blue`/`accent`/`amber`/`green`), `{ink_faint}` at 0% | |

### B.5 Chips / tags / badges

| Role | Casing | Background | Text | Radius | Padding | Weight |
|---|---|---|---|---|---|---|
| `Chip[tone="open"]` | Mixed | `rgba(green,.14)` | `{green}` | 13 | `5px 11px` | 700 |
| `Chip[tone="closed"]` | Mixed | `rgba(pink,.16)` | `{pink}` | 13 | `5px 11px` | 700 |
| `Chip[tone="done"]`/`["prog"]` | Mixed | `rgba(green,.14)` / `rgba(amber,.18)` | `{green}`/`{amber}` | 13 | `5px 11px` | 700 |
| `Chip[tone="idle"]` | Mixed | `{card2}` | `{ink_soft}` | 13 | `5px 11px` | 600 |
| `Tag` | lowercase content | `{accent_soft}` | `{accent}` | `sm` (8) | `3px 8px` | 700 |
| `SurfaceHeaderBadge[phase]` | **UPPERCASE** | `rgba(phase,.16)` | phase token | 10 | `3px 11px` | 800 |
| `OverlayChip` | Mixed | `rgba(8,10,16,.74)` | `#ffffff` | 10 | `3px 8px` | 600 |
| `ExportedBadge` | Mixed | `{accent}` | `#ffffff` | 10 | `3px 9px` | 700 |

### B.6 Progress, tables, headers, checkbox

- **`StageBar` (`QProgressBar`)**: track `{track}`, no border, radius 6, height
  11, chunk `{accent}` (radius 6); `[state="done\|prog\|skip"]` → chunk
  `{green}`/`{amber}`/`{red}`.
- **`DataTable` (`QTableWidget`, generalises `EventDaysTable`)**: `{card}` bg,
  `1px {line}`, `md` radius, gridline transparent, selection `{accent_soft}`;
  `::item` `6px 10px` + `1px {line}` bottom; header `QHeaderView::section`
  `{card2}` bg, `{ink_soft}`, 11/700, `0.4px`, **`text-transform: uppercase`**,
  `10px 12px`.
- **Headers (spec/71)**: `SurfaceHeaderRail` 3px, `background` = phase token via
  `[phase]`; `SurfaceHeaderPurpose` 13/600 `{ink}`; `SurfaceHeaderReminder`
  12 italic `{ink_soft}`. Phase chrome on rail+badge **only**; §5a colours on
  cell borders **only** — never mixed.
- **`QCheckBox` (`DaysTableCheck` look, generalise to `DesignCheck`)**: indicator
  18×18, `1px {line}`, `{card2}`, radius 6; hover border `{accent}`; checked
  `{accent}` fill + tick SVG.

### B.7 Tokens (the only colours allowed) — `palette.py`

`bg card card2 ink ink_soft ink_faint line card_border accent accent_soft green
amber red pink blue track bg_glow picked skipped compare mixed` · radius
`sm/md/lg/xl` · `shadow_alpha`. The handful of `rgba(...)` literals above
(chip tints, translucent overlays) are the **only** sanctioned non-token colours;
they should become alpha-of-token helpers during Stage 2 where practical.
