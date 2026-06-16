# spec/65 — Redesign fidelity pass (handover)

The 2026-06-13 redesign migration shipped 13 surface ports + Dialog
templates onto `XMC-redesign` (28 commits, branch tip `f5766b7`). When
Nelson reviewed it end-to-end, his read was:

> Many, many nice things about the new UI were just left behind. We have
> just ported the old surfaces with new colors.

That's correct. The migration treated each surface as **port + recolor**
instead of **build the new design's voice**. This spec captures the gap,
the per-surface punch list a follow-up "fidelity pass" should attack, and
the technical-debt backlog that's parallel-but-separate from the visual
work.

> **Update 2026-06-16 — read spec/74 first.** Two of the
> three "heavy UI" gaps (§3.8 crop drag handles; §2.3 / §3.8 Look-preset
> previews) are confirmed already built — the punch list above was stale.
> §0.1 ("Mira brand entirely absent") is now ~90% addressed: brand widgets
> + title-bar logo + About-Mira dialog with the tagline all ship.
> See [`spec/74-ui-fidelity-handoff.md`](74-ui-fidelity-handoff.md) for the
> per-item handoff and verification record.

**This punch list is partial.** When Nelson asked "did you write about all
that was left behind?" — the honest answer was no, and a 60-second skim
of just 3 of the 18 `MiraCrafter Redesign/*.html` mockup files surfaced
items not anywhere in §3 (see §0 below). The migration session never
opened ANY of the HTML mockups; the entire port leaned on the `.md` spec
files. A real audit against the HTMLs will surface more than what's
captured here — read §6 methodology before §3, then audit each surface's
.html and add to the punch list as you go.

A fresh session starting from this spec should:

1. Read `spec/00-charter.md` § principles.
2. Read this spec, especially **§0 (what's not in this punch list)**.
3. Re-read each surface's `.html` mockup file on Nelson's Desktop at
   `MiraCrafter Redesign/surface-NN-*.html` — **not the .md spec.** The
   `.md` says what to build; the `.html` says how it should feel. The
   first pass leaned on the `.md` and missed the visual intent.
4. Pick a surface from §3 and attack the punch list, ADDING to it as
   the HTML audit surfaces more.

---

## §0. What's not in this punch list (the partial-audit confession)

The session that wrote this spec never opened any of the 18 mockup HTMLs
during the migration. §1–§5 below capture what reflexive review +
Nelson's feedback surfaced. A real audit of the HTMLs will find more.
Examples found in a 60-second skim of 3 of the 18 files, AFTER §1–§5
were already written:

### §0.1 The Mira brand is entirely absent

`MiraCrafter Redesign/mira-logo.html` is a whole **logo kit** the
migration session never knew existed:

- App icon mark — 24×24 SVG (`viewBox="0 0 24 24"`, stroke="#fff",
  stroke-width 1.8/1.9/2.1/2.4 across sizes 128/64/32/16).
- Wordmark **`M✦ıra`** — the `i` is styled as a `✦` star plus a thin
  `ı`, a unique brand mark. Wordmark is the headline lockup with the
  mark in a square tile to the left.
- Tagline **"See the keepers."** — the brand line that sits under the
  lockup.

Nothing of this is in the app. No splash, no About box, no MainWindow
title-bar logo, no installer icon override (installer.iss still uses
`assets\icons\app.ico`). The app currently has no visible identity. A
fidelity pass should:

- ~~Extract the SVG mark from mira-logo.html (it's embedded inline; copy
  the path data) into `assets/icons/mira-mark.svg`.~~ **DONE** —
  `assets/icons/mira-mark.svg` ships.
- ~~Build a `MiraLogo(QWidget)` design component that renders the wordmark
  `M✦ıra` at any size (mark + wordmark side-by-side).~~ **DONE** —
  `mira/ui/design/brand.py` (`MiraMark` + `_Wordmark` + `MiraLogo`).
- ~~Drop it into MainWindow's title bar slot (see §0.2).~~ **DONE** —
  `mira/ui/design/title_bar.py` hosts `MiraLogo(tile_size=24)`.
- ~~Use the tagline somewhere — splash, About, README, wizard welcome.~~
  **DONE (spec/74 §3, 2026-06-16):** Help → About Mira surfaces
  `MiraLogo(tile_size=48, tagline=True)` via
  `mira/ui/design/about_dialog.py`. (Splash + wizard welcome remain
  optional next-steps if Nelson wants more saturation.)
- Replace `assets/icons/app.ico` with a generated icon from the new mark
  + bump the installer artifact.

### §0.2 The TitleBar component was never built

`MiraCrafter Redesign/surface-01-initial-app.html` shows:

```html
<div class="titlebar">
  <div class="logo"><span class="dot">✦</span> Mira</div>
  <div class="menu"><span>App</span><span>Event</span><span>Pick</span>
                    <span>Edit</span><span>Help</span></div>
  <div class="spacer"></div>
  <div class="theme-toggle">…</div>
</div>
```

00-design-system.md §3 also calls out a TitleBar component explicitly —
*"top strip: app logo (gradient rounded square), menu labels in ink_soft,
right-aligned ThemeToggle pill"*. The migration session never built it.
MainWindow still uses Qt's plain QMenuBar with no logo lockup and no
design-system ThemeToggle.

Fix: a `mira/ui/design/title_bar.py:TitleBar(QWidget)` that hosts a
MiraLogo (left) + QMenuBar items rendered as horizontal ink_soft labels
(center) + ThemeToggle pill (right). Drop into MainWindow's central widget
above the PageStack.

### §0.3 Inline SVG icons in search fields / filter chips

surface-01-initial-app.html's filter row uses inline `<svg>` magnifier
glyphs inside the search field:

```html
<svg width="17" height="17" viewBox="0 0 24 24" fill="none"
     stroke="currentColor" stroke-width="2">
  <circle cx="11" cy="11" r="7"/>
  <path d="m21 21-4.3-4.3"/>
</svg>
```

The migration used Unicode `🔍` in `mira/ui/design/inputs.py:_SearchFieldWrap`.
The mockup also uses the same magnifier inside the Cross-Event Cuts band
field. Replace with the SVG (extracted into `assets/icons/glyphs/search.svg`).

The Cross-Event Cuts band's accent icon tile also has explicit `<path>`
data in surface-01.html for the "stacked frames + magnifier" composite —
already flagged in §3.1, but the path data is RIGHT THERE in the mockup,
ready to extract.

### §0.4 Visual tuning the .md never captured

3-class-attribute drift between `_CategoryTile` and the mockup `.cat-emoji`:

| | Migration | Mockup |
|---|---|---|
| size | 50×50 | 46×46 |
| icon | 28×28 | 26×26 |
| bg | `accent_soft` (#211f3a) | `card2` (#1e222d) |
| fg | `accent` | `accent` |
| stroke | `1.8` | inherits, sometimes `1.9` |
| border | `1px accent` | none |

The mockup tile is QUIETER than the migration's. The accent backdrop in
the migration makes every event row's category tile read as a CTA-style
chip. The mockup uses card2 bg + accent fg with no border so the tile
recedes into the card. Subtle but significant for the "dashboard feel"
gap in §2.4.

Likely there's similar drift across every component class. A pass that
opens each component's `.html` and aligns size/bg/border/fg/stroke would
catch a lot.

### §0.5 What's behind in the other 15 HTMLs

Not audited. The migration session opened ZERO of the surface HTMLs.

Pattern: just from §0.1–§0.4, the .html mockups carry:

- Brand identity that's nowhere in the app yet (logo kit, wordmark,
  tagline).
- Component-level tuning the `.md` specs glossed over (sizing, bg
  choices, border presence, exact stroke widths).
- Inline SVG path data ready to extract into assets (search glyph,
  cross-event glyph, dialog-template icons, etc.).
- Layout / typography / spacing details that a `.md` summary can't carry.

A real audit should:

1. Open each HTML in a browser (it'll render with the in-file CSS).
2. Side-by-side with the matching surface's running smoke screenshot.
3. List the drift. Add to §3.x of this spec.
4. Then attack.

The migration session estimated 2-3 hours per surface for the fidelity
pass; the HTML audit step alone is probably 30 minutes per surface and
will surface more work than current §3 estimates.

---

## §1. State at handover

Branch: `XMC-redesign` @ `f5766b7`. Fallback: `git switch XMC` → `0dd029e`
(the pre-redesign MiraCrafter-named build).

| Surface | Built | Live in MainWindow | Legacy retired |
|---|---|---|---|
| 01 — Events list | ✅ | ✅ | ✅ |
| 02 — Event Header dialog | ✅ | ✅ | ✅ |
| 03 — Phases | ✅ | ✅ | ✅ |
| 04 — Event Days Table | ✅ (in-place chrome refactor) | n/a | n/a |
| 05 — Days Lists | ✅ | needs new entry from Phases.Pick tile | n/a |
| 06 — Days Grid | ✅ | gateway items() + photo cache port pending | |
| 07 — Picker | ✅ | locked keyboard + PhotoCache + pick gateway pending | |
| 08 — Editor | ✅ | adjustment pipeline + crop materialise + batch export pending | |
| 09 — Share / Cuts | ✅ | ✅ | ✅ (CutsListPage class only) |
| 10 — Full Resolution | ✅ | F10 wire from Picker/Editor + drag-pan pending | |
| 11 — Video Picker | ✅ | QMediaPlayer + poster extract pending | |
| 12 — Video Editor | ✅ | QMediaPlayer + spec/56 markers + segment export pending | |
| 13 — New Cut dialog | ✅ | constructor adapter to legacy `_dialog_kwargs` pending | |
| Dialog templates | ✅ | available; not yet retrofitted into QMessageBox callers | |

Foundation: `mira/ui/palette.py` + `assets/themes/redesign.qss` +
`mira/ui/theme.py` layered over the legacy `dark.qss`/`light.qss` so the
indigo palette resolves cleanly across both new + legacy roles.
Component catalog: `mira/ui/design/{cards,headers,buttons,inputs,chips,toolbar,progress,carousel,media_nav,stable_stage,thumbs,donut,dialogs}.py`
all available; SVG cluster + category icons at
`assets/icons/clusters/badge/` + `assets/icons/categories/`.

---

## §2. The honest gap — patterns left behind

These cut across multiple surfaces. The per-surface §3 list calls out
where each one bites worst.

### §2.1 Unicode glyph placeholders everywhere

Every "icon tile" — header bars, dialog corners, action buttons, status
chips — is a Unicode codepoint, not the line-icon family the design
system asks for. Specific offenders:

- **`✎`** in `mira/ui/pages/event_header_dialog.py:_build_header_bar`
- **`📅`** in `mira/ui/pages/event_days_table_dialog.py:_build_header_bar`
- **`✂`** in `mira/ui/pages/new_cut_dialog.py:_build_header_bar`
- **`🌐`** in `mira/ui/pages/share_cuts_page.py:_PoolCard`
- **`❖`** in `mira/ui/pages/_cross_event_band.py` ← the spec is loud:
  "a custom glyph = stacked frames + magnifier (search across groups)"
- **`◉`** for visited eye everywhere (PickerStage / EditorStage / VideoStage / Thumb)
- **`▶`** / `🔇` / `🗑` / `i` / `✓` / `▲` / `✕` / `?` across dialogs

Fix: draw the missing SVGs in the line-icon family (24×24 `viewBox`,
`stroke="currentColor"`, `stroke-width:1.8`, round caps), drop into
`assets/icons/<category>/`, tint via the `QSvgRenderer + QImage SourceIn`
pattern that `_event_card_redesign.py:_CategoryTile.paintEvent` already
uses.

The 4 cluster icons and 9 category icons that DO exist
(`assets/icons/clusters/badge/`, `assets/icons/categories/`) are
underused — only `_CategoryTile` and the Surface 02 Creative Focus pills
consume them. They should also land on:
- Surface 06 Days Grid cluster covers (Thumb already has a slot but
  the smoke uses Unicode `⬡` in cluster_type_text)
- Surface 07 Picker EXIF chip when the item is a cluster cover (spec
  says cluster badge replaces EXIF chip — `PickerStage._refresh_overlays`
  has the branch but renders Unicode `⬡`)

### §2.2 Visual conviction is too quiet

The indigo accent is in the palette but used sparingly. Most surfaces
read as 85% neutral. The mockups push accent harder.

- **PageTitle isn't living up to 30/800.** `mira/ui/design/headers.py:PageHeader`
  sets `setLetterSpacing(-0.6)` and `setPointSizeF(max(18))` but most
  hosts (EventsPage, PhasesPage, DaysListsPage, ShareCutsPage) embed it
  in a tight outer layout that compresses the visible weight. Check
  each surface against the mockup — the title should DOMINATE the
  header strip.
- **Card shadows are tuned conservatively.** `cards.py:Card.refresh_shadow`
  uses the palette's `shadow_alpha` (110 dark / 28 light) on a 30px
  blur with 10px y-offset. The mockups show more depth — likely either
  a higher alpha in dark mode or a second tinted shadow layer.
- **Primary buttons could push more.** `redesign.qss` has them at
  `padding: 9px 18px`; mockups have hero CTAs that feel more substantial.

### §2.3 New design patterns never put on stage

These got SCAFFOLDED but no real surface exercises them with conviction:

- **Blurred-fill thumbnails.** `Thumb._blurred_backdrop` is implemented
  and PickerStage/EditorStage/VideoStage all paint it. But every smoke
  uses gradient placeholders so the pattern's intent — "letterbox area
  becomes an extension of the image, no black/white bars" — is invisible.
  Need: real test images at varied aspect ratios in a Surface 06 smoke
  + a Picker smoke so the backdrop's value becomes obvious.
- **Mixed-cluster yellow border + `3✓·2✗` split chip.** Thumb supports
  it (constructor `cluster_split=(picked, skipped)`), exercised once
  in the batch B catalog smoke, NEVER landed in a real Days Grid
  context. Surface 06 mock smoke (`_smoke_surface_06.py` in the
  deleted artifacts) used `mixed` with split but the real grid never
  rendered against a fake event with actual mixed clusters.
- **Stable media stage** has the scaffold. No demo shows the
  photo→video transition where the canvas stays anchored — the whole
  point of the pattern. A 2-mode smoke that toggles a `StableMediaStage`
  between photo and video children would prove it.
- **Visited eye + dim-unvisited** treatment per design-system §5b:
  "unvisited thumbnails may also sit slightly recessed (img opacity
  ~.62, mild desaturation) so visited photos visibly light up." Thumb
  paints the eye chip but doesn't dim unvisited. Either add the dim
  treatment or commit to eye-chip-only — the design spec offers both.

### §2.4 Information density never increased

Surfaces are ported with the legacy's information density. The redesign
wants a more analytic-dashboard feel.

- **Surface 01 EventsPage** is a stack of event cards. No aggregate
  bar at the top ("3 events open · 2 behind on Pick this week").
  No comparative metric per card (capture rate, days since last
  activity, projected completion).
- **Surface 03 PhasesPage** has 4 donuts in a 2×2. No hero metric
  banner above ("Pick 38% · Edit 44% · Share 0% · 7 days
  remaining"). No legend below the Collect donut listing the cameras
  with their slice colors (Z9 · OM-1 · GoPro · Phone).
- **Surface 05 DaysListsPage** rows are pick/skip bars + count text.
  The "where do I start" dashboard wants visual decision-making aids:
  small per-hour capture spark, golden-hour count, day-relative
  effort score.

### §2.5 Pool algebra (Surface 13) doesn't feel central

The spec/61 algebra `#exported + #cut_1 − #cut_2` should feel like the
formula you're composing. The current dialog has +/− steppers and small
chips next to each available pool. The mockup wants:

- A large composed-formula display ("**`#exported × 1`** + **`#best_macro × 1`**")
- Live "matches N files" preview as you add/subtract
- The pool box itself should feel like a canvas

### §2.6 Cross-Event Cuts band doesn't feel like the designated entry

Surface 01 spec is loud: "It is the designated entry point." Today it's
a single Card-like band with a stub Search button. Compare to the
mockup — the band has:

- A custom glyph (stacked frames + magnifier)
- A `Preview` tag that signals "not yet wired"
- A subtle accent wash gradient
- The search field is the focal control of the band

I implemented the band but it reads as one card among many, not THE
entry point. The Preview tag is there but the band doesn't have visual
weight.

---

## §3. Per-surface punch list

For each, the §3.x heading says what was built. Bullets say what the
fidelity pass should add / replace / rework.

### §3.1 Surface 01 — Events list + Cross-Event Cuts entry

Built: `mira/ui/pages/events_page.py:EventsPage` + `_cross_event_band.py`
+ `_event_card_redesign.py`. Live in MainWindow.

- **Cross-Event Cuts band needs visual weight.** Custom SVG glyph
  (stacked frames + magnifier). Stronger accent wash. The band should
  feel like a hero entry, not a header card.
- **Open-card pipeline could be denser.** Today 4 StageProgress bars
  with percentages. Spec mockup shows a smaller spark / micro-chart that
  feels like a dashboard tile.
- **EventCardRedesign category tile.** Uses the SVG family (good!)
  but the icon at 28×28 reads as quiet. Mockup has more pop — try
  larger icon + tighter tile or a 2-layer accent halo.
- **No aggregate header bar.** "7 events · 6 open · 1 closed" sits as
  a sub-line. Mockup has it broken into 3 small stat tiles above the
  Cross-Event band ("Open · 6" / "Closed · 1" / "Captures this week · N").
- **No empty state polish.** When filtered to 0 events, the placeholder
  is a single Faint label. Mockup has an empty-state illustration.
- **Sort persistence is missing.** Legacy persisted
  `events_dashboard_sort`; mine doesn't. Already in the deferred list.
- **Classify-all banner deferred.** `classify_all_requested` signal is
  a dormant stub.

### §3.2 Surface 02 — Event Header dialog

Built: `mira/ui/pages/event_header_dialog.py:EventHeaderDialog`. Live.

- **Pencil icon tile is Unicode.** Need a real SVG (edit / form glyph).
- **Section headers feel quiet.** Identity / Logistics / Tags micro-headers
  use 10/800 accent. Mockup has a thin accent rule beneath each header
  + slightly more breathing space.
- **Required asterisks could be more prominent.** Currently inline rich-
  text `<span color>*</span>` after the Micro label. Mockup makes the
  asterisk substantial.
- **Subtype editable combo styling drift.** QComboBox(editable=True) has
  a QLineEdit child that styles slightly off from the Type combo. Equalise.
- **Per-option dropdown styling.** Context + Experience Type combos use
  Qt's native dropdown. Mockup shows a custom design-system dropdown
  with description tooltips inline.

### §3.3 Surface 03 — Phases

Built: `mira/ui/pages/phases_page.py:PhasesPage` + `Donut` widget. Live.

- **No hero summary metric.** Mockup has a banner at the top of the grid:
  "Picked 412 / 1,084 · Edited 180 / 412 · Exported 0 · 7 of 10 days
  reviewed". The dashboard wants this synthesis.
- **Collect donut has no legend.** Per-camera contribution is shown as
  slices; the user can't tell which color is which camera. Add a small
  legend below the donut (color square + camera name + duration / count).
- **Pick/Edit/Share donuts could show a center _delta_** — "8 / 35
  reviewed today" or "+22% this week".
- **Phase status chip language.** Today: done / prog / idle / skipped.
  Could be more action-oriented: "ready for pick" / "62% picked" / "no
  decisions yet" / "skipped".
- **`_apply_closed_card_state` is a stub.** Legacy disabled modification
  tiles; PhasesPage doesn't mute them visually.
- **Per-camera capture data is gateway-real** (set_event wires up
  `overview_stats.captured_per_camera_time_share`). Good. Just needs
  legend wiring.

### §3.4 Surface 04 — Event Days Table

Built: in-place chrome refactor on `event_days_table_dialog.py`. Live.

- **The table itself is unchanged QTableWidget.** Header row, cell
  borders, hover states all use Qt's default Look. The redesign wants
  themed table chrome (uppercase Micro labels in header row, accent
  hover wash on row, 3px accent left-edge on selected row, custom
  accent checkbox).
- **Checkbox column is Qt-native.** Mockup specifies "custom accent
  check (themed, not native Qt tick)" — replace `QCheckBox` instances in
  `_make_include_cell` with a styled widget.
- **Browse/Country/TZ cell renderers all use legacy chrome.** Country
  picker + TZ picker have their own ObjectNames; they need redesign
  styling.
- **Footer info "N days · N included" works.** Could add a small
  visualization (day-strip with included green, excluded faint).

### §3.5 Surface 05 — Days Lists

Built: `mira/ui/pages/days_lists_page.py:DaysListsPage` (no route swap).

- **DayRow is a stack of bars + meta.** Mockup wants more analytic per-
  day data: per-hour capture spark, golden-hour count, capture-density
  micro-chart, day-relative effort indicator.
- **Per-day Pick all / Skip all could surface as quieter icons** rather
  than ghost text buttons — too noisy across many rows.
- **Day badge is 46×46 accent tile.** Mockup uses a thinner pill or
  a small "DAY N" label so the badge doesn't dominate.
- **No live gateway wiring** — `setEventForPreview` is the only feed.
  Live path needs `gateway.phase_day_progress()` + bucket count.
- **No MainWindow entry point.** Today Surface 03's Pick tile click
  routes straight to `PickPage`. Need to insert DaysListsPage between
  them: `PhasesPage.phase_tile_activated('pick') →
  DaysListsPage.setEventForPreview(...)`, then `DayRow.activated(day_n)
  → PickPage(event_id, day_n)`. The DaysListsPage signals
  (`back_requested`, `new_pass_requested`, `day_activated(int)`) are
  ready for this.

### §3.6 Surface 06 — Days Grid

Built: `mira/ui/pages/days_grid_page.py:DaysGridPage` + `Thumb` cells
(no route swap).

- **Blurred-fill never shines** — smokes use gradient placeholders.
  Need real images in a smoke (load 12 actual exported jpgs from
  `D:\Photos\_mira_events\Inseto na Varanda\Edited Media\...`).
- **Cluster covers never put in real context.** The Thumb cluster pile
  + badge + count rendering works in catalog smoke. Days Grid never
  rendered with actual cluster data.
- **Day navigator pill could surface more day context** — small spark
  showing day's pick/skip ratio, time-of-day micro-chart.
- **Legend strip is text-heavy.** Mockup uses smaller swatches +
  one-line reminder.
- **Bulk operations toolbar.** Today: Pick all, Skip all, New pass.
  Mockup has more compositional actions (compare selected, group by
  cluster, etc).
- **Keyboard handling is stub.** Locked map (P/X/Space/C) is documented
  but no keyPressEvent on DaysGridPage. Per-cell focus + selection +
  bulk-action keyboard not wired.
- **Click handlers** route Thumb.clicked to `item_activated(str)`.
  Cluster cover click should expand into the cluster's frames; single-
  photo click should route to Picker. Today everything fires one signal.

### §3.7 Surface 07 — Picker

Built: `mira/ui/pages/picker_page.py:PickerPage` + `PickerStage` (no
route swap).

- **Big-picture identity gap:** the existing PickPhotoSurface has the
  locked P/X/Space/C/Tab/Enter/F10/F11/Esc keymap fully wired against
  the focus shell. PickerPage stubs `keyPressEvent` but isn't proven
  to honor the locked map across the page lifecycle.
- **PhotoCache integration missing.** PickerPage takes pre-loaded
  pixmaps. The real surface needs the PhotoCache predecode timer
  (memory: `design_rule_photo_cache_architecture`).
- **Cluster cover EXIF replacement.** PickerStage._refresh_overlays
  branches on `cluster_type` and shows `⬡ Burst ×12` Unicode glyph.
  Should use the cluster SVG family (already bundled in
  `assets/icons/clusters/badge/`).
- **Visited dim treatment.** Per design-system §5b unvisited photos
  may also sit slightly recessed; today's Picker shows only the
  visited eye chip.
- **Mixed-cluster yellow border** rendered correctly when state =
  "mixed". Never tested in PickerPage context.
- **Advance-after-pick.** The existing surface advances to the next
  item after a pick (configurable). PickerPage doesn't.
- **EXIF chip text** is a single string. Mockup has structured fields:
  body, lens, focal, shutter, aperture, ISO with intentional spacing.

### §3.8 Surface 08 — Editor

Built: `mira/ui/pages/editor_page.py:EditorPage` + `EditorStage` (no
route swap).

- ~~**Crop overlay is paint-only.** No drag handles, no aspect-lock
  enforcement during user drag. Real editor needs draggable corner
  handles + edge handles + aspect snap.~~ **DONE (verified spec/74
  §1, 2026-06-16):** `mira/ui/edited/crop_overlay.py` ships a fully
  draggable rectangle with corner + edge handle hit radii, aspect lock
  via `set_aspect_ratio()`, and a rotation handle.
- **Look / Strength / Style / Filter all emit signals only.** The
  rendered pixmap doesn't change. Real wiring goes through
  `core.adjustment_pipeline` + `mira.ui.edited.adjustment_surface`.
- **Crop materialization on Export** missing.
- **Reset all** emits a signal but doesn't reset the dialog state
  back to defaults.
- **Stage backdrop is the same blur as Picker.** Mockup hints at a
  stronger blur on Editor (the photo is the canvas, the backdrop is
  pure context).
- ~~**Look segmented preset previews.** Today text-only pills. Mockup
  has small icon preview of each preset (a tiny version of the photo
  with the look applied).~~ **DONE (verified spec/74 §2, 2026-06-16):**
  the intent landed as the Look grid (`mira/ui/edited/look_grid.py`,
  key **G**) — a 2×2 of *this* photo rendered through the real engine
  under Original/Natural/Brighten/Deeper. The inline toolbar pills + L
  / Shift+L cycle stay text-only by design; the grid is the richer
  surface.

### §3.9 Surface 09 — Share / Cuts

Built: `mira/ui/pages/share_cuts_page.py:ShareCutsPage`. **LIVE** (route
swap landed at `f5766b7`).

- **#exported pool card globe is Unicode.** Need the custom
  "cross-event search" glyph (stacked frames + magnifier).
- **Pool sub-line is plain text.** Mockup has a small stat row (file
  count + total size + duration estimate).
- **CutRow meta line is bold-key text** ("N items · M:SS · description
  · exported DATE"). Mockup adds a small per-cut stat tile (largest
  category, dominant camera, etc).
- **Cover thumb is single image.** Mockup hints at a layered or
  multi-image cover (3 stacked thumbnails).
- **Open / Adjust / Rename / Delete cluster** — 4 ghost buttons reads
  as crowded. Mockup uses a kebab menu (`⋮`) with the rare actions
  hidden.

### §3.10 Surface 10 — Full Resolution — **RETIRED (2026-06-14)**

`mira/ui/pages/full_resolution_page.py:FullResolutionPage` was deleted;
spec/63 §4's locked F10 contract (the modal `_InspectView` inspection
lens — full-resolution, true 1:1, honest peaking, AF, F11 = pure look,
Esc steps down one level) already covers and exceeds what the page
offered. The only thing the page added was a multi-photo filmstrip
in-place, which conflicts with the lens-as-parenthesis model the
locked keymap settled on ("the app waits until the lens closes" —
navigation between photos stays at the host layer where ←/→ already
work).

- The `full_resolution_requested` signals + `Full resolution F10`
  ghost buttons on `picker_page.py` / `editor_page.py` were dropped.
  When those shells reconcile with `PhotoViewport` in spec/70 Phase 3,
  the viewport intercepts F10 itself via `_open_inspect_view()` — no
  page-level signal needed.
- The Surface-10 line drops out of the spec/70 §2 state table and
  the §4 Phase-2 list.
- §5.1 "Cheap" no longer carries a Surface-10 entry.

### §3.11 Surface 11 — Video Picker

Built: `mira/ui/pages/video_picker_page.py:VideoPickerPage` + `VideoStage`
+ `TransportBar` (no route swap).

- **No QMediaPlayer wiring.** Stage paints the poster only; play
  button is decorative.
- **Poster extraction.** Real videos need frame extraction at load —
  use the existing `core/video_extract` pipeline.
- **Spec/56 marker partitions.** Today's surface treats each clip
  as a single decision. Spec/56 wants per-marker partition pick state.
- **Transport bar scrubber is a QSlider.** Mockup has a richer
  scrubber with marker positions visible on the track.
- **Volume control is small.** Mockup gives volume more presence.
- **Frame-step icons.** Today `◀|` / `|▶`. Mockup uses cleaner icon
  buttons.

### §3.12 Surface 12 — Video Editor

Built: `mira/ui/pages/video_editor_page.py:VideoEditorPage` (no route
swap).

- **All the photo Editor gaps apply** — adjustments are signal-only.
- **Timeline is paint-only.** No draggable trim handles. Mockup has
  full drag-and-drop interaction.
- **Multi-segment composer.** Today supports the segments list display
  only; can't add/split segments via UI.
- **Tools row and Transport row chrome.** Lots of buttons; could be
  reorganized for clarity.
- **Audio fade + Stabilise** are select / slider; no preview of
  intensity. Mockup hints at waveform display for audio fade region.

### §3.13 Surface 13 — New Cut dialog

Built: `mira/ui/pages/new_cut_dialog.py:NewCutDialog` (no route swap).

- **Pool algebra display is bland.** The selected-pool chip row uses
  tag chips with `+` / `−` prefix multipliers. Mockup wants the
  composition to read as a formula: `#exported × 1 + #best_macro × 1
  − #all_time_best_macro × 1`. Maybe a dedicated formula widget at
  the top of the pool box.
- **Scissors icon is Unicode `✂`.** Need a real cut SVG (scissors or
  film-strip-cut).
- **Custom accent checkboxes.** Photos / Videos use QCheckBox native.
  Mockup specifies accent-check custom widget.
- **Custom accent radios.** Slide cards + Start as use PillToggle in a
  ButtonGroup — looks fine. Could match the spec "accent Radio group"
  shape more closely.
- **Timing & music four-column layout** — Per-photo stepper uses a
  ×0.1s suffix because QSpinBox is int-only. Should be QDoubleSpinBox
  for proper 6.00 reading.
- **Music select hint.** Spec hints at a "≈ 99 photo slides fit"
  preview line that recomputes from target / per-photo. Today shows
  a static hint label.
- **MainWindow route swap not done.** Legacy CutsShellPage still
  imports `mira.ui.shared.new_cut_dialog.NewCutDialog`. Adapter
  needed (legacy's 7-key constructor → NewCutContext + result-shape
  translation).

### §3.14 Dialog templates

Built: `mira/ui/design/dialogs.py:MessageDialog` + `ProgressDialog`. No
retrofit yet.

- **Icon tile glyphs are Unicode** (i ✓ ▲ ✕ ? 🗑). Need design-system
  line-icon SVGs.
- **Buttons** read as standard primary/ghost. Mockup hints at footer
  styling — a separator line above the buttons, more breathing space.
- **Progress dialog marquee is a QTimer pulse** (back-and-forth value).
  Could be replaced with a true Qt marquee animation if the StageProgress
  paintEvent gains an indeterminate mode.
- **Retrofitting QMessageBox call sites.** A handful of `QMessageBox.warning`
  / `QMessageBox.information` callers remain (search the codebase). Each
  should be replaced with the corresponding MessageDialog factory + the
  legacy `QMessageBox.Icon.NoIcon` workaround can drop per memory
  `feedback_qmessagebox_chrome_disliked`.

---

## §4. Foundation / cross-cutting

These are not surface-specific but the fidelity pass should also touch
them.

- **PageHeader needs more weight.** `mira/ui/design/headers.py:PageHeader`
  builds a title block, but PageTitle's 30/800 letter-spacing −0.6
  doesn't translate to enough visual presence on most surfaces. Either
  bump the size, or break titles into a hero pattern with more breathing
  space.
- **Card shadow alpha is conservative.** Bump in dark mode + consider a
  second tinted shadow layer.
- **Theme toggle button is plain.** Mockup shows ☀/🌙 pill with smoother
  transition (not animatable in Qt but the static state should be richer).
- **Scrollbar styling.** redesign.qss styles it but inconsistencies
  remain in surfaces that use QScrollArea.
- **Loading states across all gateway-fed pages** are missing. Pages
  flash blank while gateway opens; design wants a quiet skeleton.

---

## §5. Technical-debt backlog (parallel to the fidelity pass)

These are not visual but should be tracked alongside.

### §5.1 Route swaps remaining

Sorted by cost.

**Cheap** (each ~50 lines of MainWindow adapter):

- ~~**Surface 10 wire-up.**~~ **RETIRED** — see §3.10. The spec/63 §4 F10
  inspection lens already covers and exceeds the page; FullResolutionPage
  was deleted, the dangling `full_resolution_requested` signals removed.
- **Surface 13 adapter.** Map legacy `_dialog_kwargs` (existing_cuts +
  exported_count + style_options + music_categories + music_hint +
  pool_probe + totals_probe) into a `NewCutContext` constructor and
  translate `cut_info()` → legacy draft shape. Both `_on_new_cut` +
  `_on_adjust_cut` call sites in `mira/ui/shared/cuts_shell.py`.
- ~~**Surface 05 wire-up.**~~ **DONE 2026-06-14** — `_open_days_lists_for`
  in `mira/ui/shell/main_window.py` builds DaySnapshot[] from
  `phase_day_progress()` + `cached_buckets()` + a per-day capture-hour
  rollup; PhasesPage Pick tile → DaysListsPage → PickPage opens at the
  selected day via `_open_day(day_n)`.

**Medium** (QMediaPlayer integration, ~200 lines each):

- **Surface 11.** QMediaPlayer + QVideoWidget inside VideoStage; poster
  frame extraction from each clip via `core/video_extract`; live
  position / duration / volume signal binding; replace the existing
  `mira/ui/picked/video_pick_page.py` (likely retire the legacy file).
- **Surface 12.** Same QMediaPlayer integration + draggable trim
  handles on the timeline + multi-segment composer + spec/56
  marker-partition wiring; replace `mira/ui/edited/edit_video_page.py`.

**Heavy** (~500-1000 lines each):

- **Surface 06 — Days Grid.** Gateway items() with cluster grouping;
  PhotoCache predecode integration; locked keyboard map P/X/Space/C;
  bulk Pick-all / Skip-all routing; day-nav state; replace
  `mira/ui/base/day_grid_view.py` (571 lines) + `day_grid_cell.py`
  (402 lines) + parts of `mira/ui/picked/grid_view.py` (711 lines).
- **Surface 07 — Picker.** Locked keyboard map fully wired against
  focus shell; PhotoCache predecode timer; decision persistence;
  visited stamping; advance-after-pick; cluster cover expansion;
  replace `mira/ui/picked/pick_photo_surface.py` (800+ lines) +
  `mira/ui/picked/pick_page.py`.
- **Surface 08 — Editor.** core.adjustment_pipeline integration for
  Look/Strength/Filter; crop materialisation on Export; batch export
  through `mira.ui.edited.export_job`; replace
  `mira/ui/edited/edit_page.py` (1700 lines) + `edit_host_page.py`
  + parts of `adjustment_surface.py`.

### §5.2 Test coverage

Surfaces 01, 02, 03, 09 ROUTED but never re-tested. Their legacy tests
were deleted in retirement commits. Priorities for new tests:

- `EventsPage._apply_filter` over the 4 selects + search + sort
  combinations.
- `EventHeaderDialog.header_info()` round-trip — input dict via
  existing_info, then header_info() should return the same shape +
  None-exclusion rule on Creative Focus.
- `PhasesPage.set_event(event_id)` against a fake gateway —
  PhaseSnapshot list shape + status assignments.
- `ShareCutsPage.refresh_from_gateway(eg)` (added during the swap) —
  pool count + cut snapshots.

### §5.3 Settings dialog persistence bug

Open chip: `task_0d5aeda0` ("Investigate: Settings dialog shows
photos_base_path but doesn't persist"). Repro is documented in the
spawn_task prompt. Nelson hit this on 2026-06-13; quick fix was a
direct SettingsRepo().save() to write photos_base_path. Real fix is
in the Settings dialog save handler.

### §5.4 `tr()` consistency

Surface 02 + 04 use `tr()` consistently. Surfaces 01, 03, 05+ are
mostly hardcoded English. A sweep pass would wrap every user-facing
string in `tr()`.

### §5.5 Keyboard map verification

The LOCKED keyboard map (P/X/Space/C/Tab/Enter/F10/F11/Esc) is the
project's critical invariant for the pick / edit surfaces. Picker /
Editor / Video pages declare it in keyPressEvent but the integration
with MainWindow's focus shell isn't tested. Need: a smoke that
constructs a real Picker via MainWindow, sends synthetic key events
through `QApplication.postEvent`, and asserts the corresponding state
changes.

### §5.6 Memory updates needed

These memory files reference patterns / files that the migration
changed; check them for staleness:

- `feedback_qmessagebox_chrome_disliked` — Dialog templates exist now;
  this guidance can soften.
- `reference_new_app_config_isolation` — updated 2026-06-13. Fresh.
- `feedback_phase_default_state_is_wired` — applies to new pages too;
  no change.
- `feedback_clean_up_after_each_step` — added 2026-06-13. The fidelity
  pass should honor it.
- `feedback_no_qmessagebox` style guidance is now superseded by
  `mira.ui.design.dialogs`.

---

## §6. How to attack the fidelity pass

Suggested approach for a fresh session:

1. **Pick one surface from §3** — preferably one that's both LIVE and
   high-traffic (Surface 01, 02, 03, 04, or 09 currently).
2. **Open the matching `.html` mockup** on Nelson's Desktop. Read it
   like a real user. What does it FEEL like?
3. **Open the matching surface's `.py` file** in the project. Compare.
4. **Make a short fidelity list** specific to that surface (one row
   per gap from §3.x or from your own reading).
5. **Attack 3-5 items.** Keep changes scoped — no scope creep across
   surfaces.
6. **Render a screenshot smoke** (always send via `SendUserFile`,
   never auto-open with Start-Process per
   [[feedback_clean_up_after_each_step]]).
7. **Commit. Clean up any artifacts. Move on or pause for Nelson's
   eyeball.**

Don't try to do all 13 surfaces in one session. The migration tried that
and produced "port + recolor." A fidelity pass needs more
contemplation per surface.

**Trap to avoid:** confusing "I added more chrome" with "I built the
design's voice." If a change makes the surface more BUSY without making
it more useful or more clearly intentional, back it out.

**Trap to embrace:** building something the legacy didn't have at all.
The new design system suggests information density and analytic feel
the legacy never had. Where you see that opportunity, take it. The fact
that no legacy surface has a "per-day capture spark" doesn't mean DayRow
shouldn't have one.
