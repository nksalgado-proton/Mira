# spec/59 — The Edit Surface: top grid, the Stop model, modeless development

**Status:** design LOCKED, Nelson 2026-06-11 (the Edit Surface design
parenthesis, closed live, question by question). Implementation lands in
passes the same day. Supersedes spec/56's **surface** description —
layout, selection model, Adjust mode — wherever they conflict;
spec/56's marker-partition **data model** (markers, derived segments,
snapshots, left-survives, bytes-only-at-Export) stands unchanged.

---

## 0. The mistake being corrected

The workshop accreted modes and scattered controls: an Adjust mode with
enter/exit/adopt/reopen buttons, a selection model separate from the
playhead, a free choice of adjustment frame per clip, and "cut"
vocabulary diverging from the model's markers. Every one of those is
removed; **the cursor is the selection and navigation is everything.**

## 1. Concepts (locked vocabulary)

- **Marker** — the clip delimiter. "Cut" is dead vocabulary, everywhere.
  Every video automatically carries a permanent marker at its start and
  its end. **A marker's status IS the status of the clip that starts at
  it** and extends to the next marker — Pick paints/borders that span
  green on the timeline, Skip red. The end marker starts no clip.
- **Snapshot** — unchanged (spec/56): a photo child at a position,
  auto-Picked at creation, full photo treatment.
- **Stop** — a marker or a snapshot. The generic navigation unit.
  **No two stops may share a position.**
- **Development anchor** — a clip is adjusted ON the frame at its
  initial marker, always. The adjustment-frame concept is dead: no Adj
  Frame stops, no frame choice, no "reopen at saved frame", no
  replace-frame dialog. This composes with left-survives: deleting a
  marker merges its clip away leftward, development included —
  "keep only the first adj frame" falls out for free.

## 2. The top

Implemented 2026-06-11 (layout frozen pending the visibility eyeball):

- **Line 1:** ONE outer named box **"Style, Look & Filter"** holding
  [Look (always the widest) | Style | Filter].
- **Line 2**, aligned under line 1: [**Crop** under Look, same width,
  controls on one horizontal row — the aspect dropdown displays
  **"No Crop"** while persisting the canonical "Original" label |
  **Audio** (video-only; today's Fade controls) | **Vibrations**
  (video-only; today's Stabilise controls)].
- **Line 3:** the action row, untouched.
- **Mixed-case titles, always, app-wide.** Dropdown height = button
  height.

### 2.1 Visibility (cursor-driven; video surface)

a) Cursor **not** on a Picked stop → the top controls are **completely
   hidden**, with their space fully preserved — the window and canvas
   never shift.
b) Cursor on a **Skipped** stop → controls visible, **all greyed**
   (the video extras' export-settings exemption dies — they grey too).
c) Cursor on a **Picked** stop → controls live, bound to that stop:
   the snapshot, or the clip starting at that marker (on its initial
   frame).

Photos: the top is always visible.

## 3. Modeless development

The Adjust mode dies — ✎ Adjust, Reopen-at-saved-frame, Adopt-and-back,
and the mode-driven transport hiding all retire. **Landing the cursor
on a Picked stop IS entering development:** the media area swaps to the
development canvas showing that stop's frame; stepping off swaps back
to the player. Development engages when paused; during playback the top
stays hidden. Frame extraction shows the wait cursor (the busy rule)
and caches per (target, position).

## 4. The bottom — two lines (video)

**Middle line:**
`[Marker] [Snapshot] [Remove] [Toggle Status] [Reset ▾]` …
`[snapshot strip · Mute · Vol · Speed]` (the tenants stay to the right
for now; space is judged at the eyeball).

- The creators grey while the cursor sits on any stop.
- **Remove** removes the stop under the cursor; greys off-stop and at
  the permanent endpoints.
- **Toggle Status** works anywhere (the old culler rule): on a snapshot
  it toggles the snapshot; otherwise it toggles the marker owning the
  position (= the clip you're inside). Markers and snapshots only.
- **Reset** menu: Reset everything / Clear markers only / Clear
  snapshots only (NoIcon confirms). Reset everything returns the single
  surviving clip to Skip.

**Navigation line:**
`[Previous] [Start] [◀ Stop] [◀ Frame] [▶ Play/⏸] [Frame ▶] [Stop ▶] [End] [Next]`

- ◀/▶ Stop walk markers ∪ snapshots (endpoints included), with the
  ancestor culler's fallback to start/end, one frame of tolerance, and
  the end parking one frame short.
- **Photos show only Previous / Next**; the photo top is always
  visible and the photo bottom carries only what makes sense.

## 5. The timeline

Clips painted green/red from their markers' statuses; markers as
draggable handles (the may-not-cross rule stands); snapshots get their
own glyph on the timeline; the permanent endpoints are visible. Click =
seek (the cursor model); clicking a marker handle seeks to it. The
rep-frame glyph dies.

## 6. What this retires

- The Adjust mode + its four buttons; the selection model
  (`sel_kind` / snapshot-hold) — the cursor is the selection.
- The adjustment-frame concept (`rep_frame_ms` remains as a stored
  anchor, written = the clip's start).
- "Cut" vocabulary; the 2026-06-11 temporary third line; the nav-row
  workshop buttons; the handle-selection Remove path; the
  `excludeFromToolsEnable` exemption.

## 7. Implementation slices

1. **Lines** — the navigation line + Stop nav + the middle line +
   cursor-driven enable/grey + the vocabulary sweep + temp-line death.
2. **Modeless development** — the §2.1/§3 visibility + media mechanics
   + the §6 retirements.

Targeted tests per slice; Nelson eyeballs the assembled surface.

## 8. Export status + the batch queue (designed + landed 2026-06-11)

Two workflows, one grammar (Nelson, closed live):

- **The border returns as a status marker at Edit: marked for export.**
  Green = marked (and **what's green is what the next phase sees** —
  progressive filtering applied at Edit), red = not. Click the border
  to toggle — on the Edit surfaces AND on the grids (supersedes the Q4
  2026-06-08 "border-click no-op at Process" rule). Under the hood it
  is the ``edit`` phase state the workshop's clips/snapshots already
  use — photos joined the same model.
- **Default: a setting, shipped born-green** (``edit_default_state``,
  already routed through ``default_state_for``). It governs photos AND
  segment lazy-birth — **superseding spec/56's fixed default-Skip for
  clips** (snapshots stay auto-Pick; split halves still inherit).
  *Flagged for Nelson's veto — decided coherently while he was
  wrapping; spec/56's lock predated this design.*
- **As-you-go exporting IS marking:** a per-item export flips its
  status green automatically (an exported photo must be visible in the
  next phase).
- **Video cells on grids aggregate** their clips + snapshots with the
  picker's cluster grammar: green all-marked · red none · **yellow
  partial**. Border-clicking a video/cluster cell paints ALL members
  (all-green → all red, anything else → all green); inside a cluster
  sub-grid a border-click flips the one member.
- **Batch export = a queue.** Day-scope and event-scope exports
  collect the GREEN set and run app-level, **strictly one at a time**
  (launch as many as you like — they wait in line), detached from the
  page: the user keeps working anywhere, dashboard included. **One
  progress line below the menubar** (label · per-file progress ·
  "+N waiting" · Cancel), hidden when idle. No completion popups — the
  strip going idle and the cells turning are the signal. A commit lost
  to a closed event self-heals via the Edited Media return scan
  (spec/57 §3).
- **The already-exported WATERMARK (landed 2026-06-11):** a diagonal
  "Exported" over the image in grid + individual views — system-set
  (never togglable), driven by lineage (in-app exports AND third-party
  associations; photos), with a setting to hide it entirely
  (``show_exported_watermark``, Edit tab, default on). The border
  carries no exported meaning anymore. The driver is deliberately NOT
  ``Adjustment.edit_exported`` — that flag is freshness (reset on
  every adjustment change) and keeps its chip; the watermark means
  "an exported version exists", which only lineage records uniformly
  across all four writers (as-you-go, batch, return scan, backfill).
- **The batch ENGINE is its own design (queued, next session):**
  maximise the hardware — GPU encode, frame-parallel clip rendering
  across cores — while the foreground app stays lag-free (process
  isolation, yield-to-foreground). The queue + line above are its
  consumer and do not change.

## 9. The Edit metric — *edited ÷ picked* (Nelson 2026-06-18)

The Edit-phase progress number is **edited ÷ picked**, and "edited" has a
strict meaning: a picked photo is **edited** the moment it moves **off the
unedited baseline**. Until then every picked photo reads as *Original* —
unprocessed, so a day full of keepers nobody has touched reads **0%
edited**, not 100%.

**The predicate (the one definition).** A photo counts as edited when its
adjustment carries any of three INDEPENDENT reasons:

- **Look** — a look other than ``original``,
- **Filter** — a creative filter, or
- **Crop** — an explicit crop box, a non-``Original`` aspect, a straighten
  angle, or a rotation.

This is **not** "an adjustment row exists." A row written but left at the
baseline (Original look, no filter, no crop) is **not** edited. The
denominator is the day's (or event's) **picked** keepers — "among what you
kept, how much have you actually worked."

**The baseline is Original only (Nelson 2026-06-18).** ``original`` is the
sole unedited look — identity, no processing. ``natural`` is **a deliberate
Look choice** ("the default one"), so a photo at Natural is edited. To make
this real the **editor now defaults new adjustments to Original** (the
model/schema default, the AdjustmentSurface entry look, and Reset all flip
from Natural → Original): an unedited photo loads RAW until the user picks a
Look, applies a filter, or crops. ``core.photo_auto`` renders
``look="original"`` as ``Params()`` (identity), so this is a real,
renderable state.

**One source of truth in code.** ``core/edit_status.py`` owns it three
ways, kept in lock-step: ``edit_reasons(adj)`` (the ordered tuple of active
reasons — ``look``/``filter``/``crop``), ``is_adjustment_edited`` (=
``bool(edit_reasons)``), and ``EDITED_SQL`` (the GROUP-BY twin). The
per-object test, the badge, and the bulk count cannot drift. Consumers:

- **Events-tile Edit donut** — ``edited_count / picked_count`` via the
  gateway's ``edited_count()`` (replacing the old bare ``len(adjustments())``
  "developed-row" count; ``developed_count`` survives only as the
  row-exists number, not a progress signal).
- **Days-Lists Edit row** — ``phase_day_progress()['edit']`` now counts
  ``EDITED_SQL`` rows / picked. The shared Days Lists, under the **Edit**
  identity, swaps the Pick/Skip read for the two **halves of the picked
  keepers**: **As shot (green** — ``picked − edited``, still at the
  unedited baseline) + **Edited (amber** — off the baseline). Both are
  taken **over picked**, so **As shot + Edited always sum to 100%** — the
  As-shot percentage is derived as ``100 − Edited%`` so rounding can never
  break the complement. The per-row *Pick all / Skip all* and the header
  *Pick/Skip all days* verbs are **hidden** under Edit (no day-level Skip
  there).
- **Days Grid (Edit phase)** — two signals per photo cell. The **border**
  encodes edited: **green = unedited / amber = edited** (repurposing the
  Edit grid's free border — decision state is stripped there per BUGS.md
  B-010). The **reason badge** is one bottom-left **amber pill** carrying a
  small dark glyph per active reason (Look · Filter · Crop, in order), so
  the colour says *whether* it's edited and the pill says *why* / which
  template(s). It stacks above the Exported badge when both apply; the full
  reason names ride the cell tooltip. Driven by ``adjustments_for_day`` +
  ``edit_reasons`` (Edit grid only; Pick has nothing edited, Export shares
  the edit storage and keeps showing it).
