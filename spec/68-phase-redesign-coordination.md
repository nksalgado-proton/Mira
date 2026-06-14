# spec/68 — Coordinating the phase-model revision with the redesign fidelity pass

**Authored 2026-06-14 (Nelson + Claude). A coordination layer, not a new design.**

Two UI programs are in flight at once and they collide on specific surfaces:

- **spec/65 — Redesign fidelity pass.** The 2026-06-13 migration ported all 13
  surfaces onto the redesign, but shallowly ("port + recolor"). spec/65 is the
  outstanding punch list to build the design's *voice*, not just its palette.
- **spec/66 — Collect / Pick / Edit / Export** (handoff in **spec/67**). The
  phase-model revision: Edit de-cluttered, a **new Export surface**, Share
  demoted to a closed-event Cuts state, the `Exported Media/` tier.

This spec exists because **spec/67 was written without reference to spec/65 or
the design system.** A coding agent following spec/67 alone will build the
brand-new Export surface (slice 5) in whatever style is closest to hand — which
is exactly the "port + recolor" failure spec/65 was written to correct, on the
one surface that has *no mockup to recover fidelity from later*. Read this
spec alongside spec/67; where they differ on slice 5, **this spec governs.**

---

## 1. The branch reality (VERIFIED 2026-06-14, resolves the fork worry)

The concern was that spec/66's slices 1–3 might have landed on a line that
*doesn't* include the redesign (`XMC-redesign` @ `f5766b7`), leaving two
divergent UI histories to merge. **They did not.** The current working tree is
already post-redesign — verified by direct filesystem inspection, not git
(the repo's `.git/config` was unreadable from the inspecting environment):

- The redesign **component catalog is present**: `mira/ui/design/` carries
  `brand.py`, `buttons.py`, `cards.py` (`Card` / `Card2` / `StatTile`),
  `carousel.py`, `chips.py`, `dialogs.py`, `donut.py` (`Donut` / `DonutSlice`),
  `headers.py` (`PageHeader` / `ThemeToggle`), `inputs.py`, `media_nav.py`
  (`Filmstrip`), `progress.py` (`StageProgress`), `stable_stage.py`
  (`StableMediaStage`), `thumbs.py` (`Thumb`), `title_bar.py`, `toolbar.py`.
- The **redesigned pages are present**: `events_page.py`, `phases_page.py`,
  `share_cuts_page.py`, `days_lists_page.py`, `days_grid_page.py`,
  `picker_page.py`, `editor_page.py`, `_cross_event_band.py`,
  `_event_card_redesign.py`.
- The **theme foundation is present**: `mira/ui/palette.py`, `mira/ui/theme.py`,
  `assets/themes/redesign.qss` (layered over `dark.qss` / `light.qss`).

So spec/66's phase work is building **on top of** the redesign. There is one
bookkeeping item to confirm, not a fork to repair:

> **Confirm before slices 4–6:** the branch the phase work commits to descends
> from `XMC-redesign` (`f5766b7`), so slices 4–6 land on the redesigned surfaces
> rather than re-diverging. (`spec/65 §1` names `XMC-redesign` @ `f5766b7` as the
> redesign tip and `XMC` @ `0dd029e` as the pre-redesign fallback. The working
> tree contents above confirm the redesign is checked out; just verify the
> committing branch's lineage.)

## 2. The two programs are sequential, not parallel — and must not fork

**spec/66 (the phase spine) goes first; spec/65 (fidelity across the existing
surfaces) follows.** Reasons:

- spec/66 changes *which surfaces exist* (Export is new; Share moves to a
  closed-event state). Running the fidelity pass on surfaces the phase work is
  about to add or re-home would be wasted motion.
- spec/66 slice 6 creates `Exported Media/` = `#exported`, which is the data
  foundation the Share/Cuts surface (and its fidelity pass) depends on.

The non-negotiable: **the surfaces spec/66 newly builds or re-homes must be born
fidelity-correct** (§3), so they never join the spec/65 backlog. Everything
spec/65 already lists for the *pre-existing* ported surfaces (Picker, Editor,
Days Grid, Events, etc.) stays spec/65's job, after the spine lands.

---

## 3. Amendment to spec/67 slice 5 — build Export from the design catalog

spec/67 slice 5 says "reuse the Thumb widget + FlowLayout." That is necessary
but **not sufficient**. The Export surface must be composed from the redesign
component catalog and carry the design's voice from its first commit. Concretely:

- **Grid:** mirror `mira/ui/pages/days_grid_page.py` — it already composes
  `mira.ui.design.Thumb` cells in a `mira.ui.base.flow_layout.FlowLayout`
  (responsive ~180 px tiles), with a themed toolbar, a `PageHeader`, and the
  palette. The Export surface is the same shape with a different decision
  semantic. Do not hand-roll a grid.
- **State colors (§5a):** reuse the fixed photo-state coloring the `Thumb`
  already renders — here **green = will export (the default, opt-out), red =
  dropped**. Never remap these colors; they are the app's one decision grammar
  (same green/red the Picker uses for keep/skip).
- **Keymap:** the locked spec/63 map, enforced in the viewport, not re-declared
  per page — **P = green (export), X = red (drop), Space = toggle, C degrades to
  toggle** on this binary ledger (the cut-session precedent in spec/63 §4).
- **Header / chrome:** `PageHeader` (give the title real weight — spec/65 §2.2 /
  §4 flag that titles read too quiet), themed toolbar, `StageProgress` for the
  batch progress line (§4 below), design-system buttons — no Unicode-glyph
  placeholders (spec/65 §2.1), no QMessageBox chrome (use
  `mira.ui.design.dialogs`).
- **Traps to avoid (spec/65 §6):** "port + recolor" (cloning a legacy layout and
  swapping the palette); and "more chrome ≠ the design's voice" — if a flourish
  makes the surface busier without making the export decision clearer, cut it.

> **"Fidelity-first" here means structure and voice, not drawing icons.** Build
> Export's layout, components, §5a colors, and keymap from the catalog now. Do
> **not** draw new glyph SVGs during slice 5 — Export reuses the shared `Thumb`
> widget and inherits whatever it renders, so the remaining Unicode placeholders
> (eye / tick / split chip) ride the dedicated icon sweep in
> [spec/69](69-icon-wiring-fidelity.md), which runs **after** the phase spine
> and fixes every surface — Export included — in one pass. Don't block slice 5
> on icons that don't exist yet.

## 4. The batch trigger + progress line moved out of Edit (slices 4 → 5)

Slice 4 removes the export buttons + batch-queue UI from Edit; slice 5 hosts
them. Keep the **engine** untouched (view-over-engine, charter / spec/67 hard
rule): the consumer contract from **spec/59 §8** (the `BatchExportQueue`, strictly
one job at a time, the single progress line below the menubar — label · per-file
progress · "+N waiting" · Cancel, hidden when idle, no completion popups) and the
**spec/60** worker engine are **locked and unchanged**. Slice 5 only re-parents
the *trigger* and renders the progress line with `StageProgress` in the new
surface's chrome. As-you-go single-item export stays on its immediate path
(spec/60 §8), never through the queue.

## 5. Surface mapping under the new phase model

The mockup set predates spec/66's "Export is a phase" decision, so the names
don't line up one-to-one. The mapping the build must honor:

| Phase-model concept (spec/66) | Surface / mockup |
|---|---|
| **Export phase surface** (new, slice 5) | **No mockup exists.** Derive its voice from the Days Grid surface (06) + the §5a state colors + the **`Mira Surfaces/Tick marks and watermark for exports.png`** mockup for the export-mark / watermark visuals. |
| **Share = closed-event Cuts state** (spec/66 §1, spec/61) | The existing **Share / Cuts** surface (09, `share_cuts_page.py`) — already live. It becomes reachable *only on closed events* (slice 6 gates the Share menu). Its fidelity work stays spec/65 §3.9. |
| **Export phase tile** on Phases (03) | `phases_page.py` already shows Collect/Pick/Edit/Export with phase-identity colors (slices 1–3). Slice 5 wires the Export tile click → the new Export surface. |
| Export marking / "Exported" watermark | `Tick marks and watermark for exports.png`; the watermark is lineage-driven (spec/59 §8), not the border. |

The HTML mockups (the source of *feel*, per spec/65) live on Nelson's Desktop at
`MiraCrafter Redesign/surface-NN-*.html`; the PNG renders are in-repo at
`Mira Surfaces/`. There is no `surface-*-export.html` — the Export surface is a
genuine design gap, so it gets a deliberate design pass against the catalog
rather than a port.

## 6. What this changes / does not change

- **Changes:** spec/67 slice 5 is amended by §3–§5 above — Export is built from
  the design catalog, fidelity-first, with the moved batch trigger/progress line
  rendered in design-system chrome. spec/67 gains a pointer to this spec.
- **Does not change:** spec/66's phase model and cascade; the spec/59 §8 /
  spec/60 engine contracts; the spec/63 locked keymap; spec/65's punch list for
  the *pre-existing* ported surfaces (it runs after the spine, §2); slices 4 and
  6 as written in spec/67 (only their fidelity expectations are clarified —
  no Unicode placeholders, design-system dialogs, real chrome).

## 7. Related

- [spec/65 — Redesign fidelity pass](65-redesign-fidelity-pass.md) — the punch
  list this coordinates with; runs after the phase spine.
- [spec/66 — Collect / Pick / Edit / Export](66-collect-pick-edit-export.md) —
  the phase model being implemented.
- [spec/67 — Implementation handoff (slices 4–6)](67-implementation-handoff.md) —
  the build brief; slice 5 is amended here.
- [spec/59 §8](59-edit-surface.md) / [spec/60](60-batch-export-engine.md) — the
  batch queue + engine contracts (locked, unchanged).
- [spec/61](61-share-event-cuts.md) — the Cut model = the Share closed-event state.
- [spec/63](63-photo-viewport.md) — the locked keyboard map.
