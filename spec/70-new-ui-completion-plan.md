# spec/70 — Completion plan: every surface running the new UI

**Authored 2026-06-14 (Nelson + Claude). The execution plan to finish the
redesign — the ordered program that takes [spec/65](65-redesign-fidelity-pass.md)
from punch list to "done."**

**Goal:** every surface the user touches in the running app is the redesigned
one, carrying the design's voice (not "port + recolor"), and every legacy
surface module is retired. The definition of done is §6.

---

## 0. Design source (read this first — the folder names are misleading)

The authoritative visual target is the **Mira redesign mockups**: the HTML+MD
set in the Desktop folder currently named **`MiraCrafter Redesign/`**. The folder
name is **stale** — its files were rebranded to *Mira* and the sandbox couldn't
rename the folder. This is the indigo, dark-default, component-based design that
the in-code `mira/ui/design/` catalog + `redesign.qss` already implement.

**Do NOT use the in-repo `Mira Surfaces/` PNGs as the target.** Despite the
"Mira" name, that set is **older**: the renders still say "MiraCrafter" in the
title bar, use the lighter legacy look, and show a "Share" 4th phase — they
predate this redesign and the spec/66 phase model, and would contradict the work
already in the code.

Where the mockups still read "MiraCrafter" or "Share": the brand is **Mira**
(the `M✦ıra` wordmark) and the 4th phase is **Export** (spec/66). Suggest
renaming the Desktop folder to `Mira Redesign/` to end the confusion.

---

## 1. Strategy (LOCKED) — redesign shell + the one engine, then retire legacy

The heavy surfaces exist twice today: a **functional legacy surface** that is
live and engine-backed, and a **redesign shell** that looks right but isn't
wired (verified — e.g. `pages/picker_page.py` takes pre-loaded pixmaps, while
the live `picked/pick_photo_surface.py` carries the gateway + the spec/63
PhotoViewport). "All surfaces on the new UI" = reconciling these. The rule for
every reconciliation:

> The redesign shell **embeds `PhotoViewport`** (spec/63's "one engine, every
> surface" thesis) and **absorbs the gateway/engine wiring** from its live
> legacy twin; then the legacy module is **retired**. Engines are reused, never
> rewritten (view-over-engine): `PhotoViewport` / PhotoCache (spec/63), the
> adjustment pipeline, the batch export engine (spec/60). spec/63 already
> migrated the legacy surfaces onto the viewport — that work is **ported, not
> discarded.**

This is not a free choice — it's what spec/63 already mandates. The redesign
shells that take "pre-loaded pixmaps" are simply not yet conformant; this plan
makes them conformant.

## 2. Current state (verified 2026-06-14, this working tree)

Grounded in `mira/ui/shell/main_window.py`, **not** the spec/65 §1 table (which
is stale — it lists Share/Cuts as route-swapped, but this tree wires the legacy
`CutsShellPage`).

| Surface | Redesign page exists | Live in MainWindow now | Legacy still wired |
|---|---|---|---|
| 01 Events | ✅ `events_page.py` | ✅ `EventsPage` | — |
| 02 Event Header dialog | ✅ | ✅ `EventHeaderDialog` | — |
| 03 Phases | ✅ `phases_page.py` | ✅ `PhasesPage` | — |
| 04 Event Days Table dialog | ✅ | ✅ `EventDaysTableDialog` | — |
| 05 Days Lists | ✅ `days_lists_page.py` | ❌ no entry point | (Pick tile → PickPage directly) |
| 06 Days Grid | ✅ `days_grid_page.py` | ❌ | `base/day_grid_view.py`, `day_grid_cell.py`, `picked/grid_view.py` |
| 07 Picker | ✅ `picker_page.py` (shell) | ❌ | `picked/pick_page.py` → `pick_photo_surface.py` |
| 08 Editor | ✅ `editor_page.py` (shell) | ❌ | `edited/edit_host_page.py`, `edit_page.py`, `edit_video_page.py` |
| 09 Share / Cuts | ✅ `share_cuts_page.py` | ❌ (legacy live) | `shared/cuts_shell.py` `CutsShellPage` |
| ~~10 Full Resolution~~ | **RETIRED 2026-06-14** — spec/63 §4 F10 lens supersedes | — | — |
| 11 Video Picker | ✅ `video_picker_page.py` (shell) | ❌ | `picked/video_pick_page.py` |
| 12 Video Editor | ✅ `video_editor_page.py` (shell) | ❌ | `edited/edit_video_page.py` |
| 13 New Cut dialog | ✅ `new_cut_dialog.py` | ❌ | `shared/new_cut_dialog.py` (legacy) |
| Export (new phase) | being built (spec/66 slice 5) | (in progress) | n/a — net new |
| Quick Sweep (Collect) | ❌ none | ❌ — **"coming next" placeholder** (`main_window._coming_next`) | logic only: `mira/picked/quick_sweep_buckets.py` (no UI page) |

**First action of every session: re-verify the row you're attacking against
`main_window.py`.** The table above will drift as the phase spine and earlier
phases land.

## 3. Preconditions (must be committed before Phase 1)

1. **Phase spine — spec/66/67 slices 4–6.** Creates the Export surface and
   de-clutters Edit; surface 08 (Editor) reconciliation builds on the
   de-cluttered Edit, and Share/Cuts (09) depends on `Exported Media/` from
   slice 6.
2. **Icon sweep — [spec/69](69-icon-wiring-fidelity.md).** Runs after the spine;
   fixes the shared widgets (`Thumb`, eye/tick) every later surface inherits.

---

## 4. The plan (phases, in order)

Each phase is several sessions; **one surface per session** (§5). Don't batch —
batching is what produced "port + recolor" the first time (spec/65 §6).

### Phase 1 — Fidelity on the already-live surfaces (lowest risk, build momentum)

Surfaces **01, 02, 03, 04** are wired but read as recolored. Pure visual work
off spec/65 §3.1–§3.4 + the cross-cutting §2/§4. No route swaps, no engine work.
Good first phase: it proves the per-session cadence and the real-asset
screenshot loop before the risky surfaces.

### Phase 2 — Cheap route-swaps / wire-ups (spec/65 §5.1 "Cheap", ~50 lines each)

- ✅ **05 Days Lists** (2026-06-14) — `DaysListsPage` lives between Phases and
  Pick: `PhasesPage.phase_tile_activated('pick') → DaysListsPage → DayRow.
  activated → PickPage._open_day(day_n)`. Gateway-fed from `phase_day_progress()`
  + `cached_buckets()` + a per-day capture-hour rollup driving the analytic
  spark. (Becomes the redesigned Days Grid target in Phase 3.)
- ~~**10 Full Resolution**~~ — **RETIRED 2026-06-14.** The verify-then-decide
  pass found that spec/63 §4's F10 inspection lens already covers and exceeds
  the page (honest peaking + AF + F11 pure look + modal aspect-locked window).
  The page's only addition was an in-place multi-photo filmstrip, which
  conflicts with the lens-as-parenthesis model the locked keymap settled on.
  FullResolutionPage was deleted; the dangling `full_resolution_requested`
  wiring on the picker/editor redesign shells was removed. See spec/65 §3.10.
- **13 New Cut dialog** — adapter mapping the legacy 7-key constructor →
  `NewCutContext`, both call sites in `shared/cuts_shell.py`.

### Phase 3 — Heavy reconciliations (one surface per session, §1 strategy)

Order chosen by dependency + risk:

1. **06 Days Grid** — `DaysGridPage` (already composes `Thumb` + `FlowLayout`)
   gets gateway `items()` + cluster grouping, PhotoCache predecode, the locked
   keymap, bulk Pick-all/Skip-all, day-nav. Retire `base/day_grid_view.py`,
   `day_grid_cell.py`, `picked/grid_view.py`. (Lands the §2.3 patterns —
   blurred-fill, mixed-cluster yellow + split chip — in a real grid.)
2. **07 Picker** — `PickerPage` shell **embeds `PhotoViewport`** and absorbs the
   wiring spec/63 §8 (5d) already built into `pick_photo_surface.py`: decision
   persistence, sharpness honesty, visited stamping, advance-after-pick, cluster
   cover expansion, sweep-with-peaking, F10 lens. Retire `picked/pick_page.py` +
   `pick_photo_surface.py`.
3. **08 Editor** — after slice 4. `EditorPage` shell binds the adjustment
   pipeline (`core.adjustment_pipeline` / `adjustment_surface`), crop overlay
   with drag handles, embeds the viewport (spec/63 §6/6b). **Export is no longer
   here** (it moved to the Export surface in the phase spine). Retire
   `edited/edit_page.py` + `edit_host_page.py` (fold what survives).
4. **Quick Sweep (Collect)** — **net-new build, not a reconciliation.** Today
   the menu items route to a `_coming_next` placeholder; there is no UI page and
   **no mockup** (like the Export surface, it needs a deliberate design against
   the catalog first). Build it as its own surface that **embeds the same
   `PhotoViewport`** the Picker uses (spec/63), over the existing
   `mira/picked/quick_sweep_buckets.py` logic; wire the "Standalone Quick Sweep"
   and "Quick Sweep this event" menu entries to it (replacing `_coming_next`).
   Placed after the Picker because it reuses the Picker's single-photo viewport
   machinery.
5. **09 Share / Cuts** — route-swap `CutsShellPage` → `ShareCutsPage`, **gated to
   closed events** (spec/66/68). *Note:* the redesigned page's deep Cut
   functionality is the **spec/61** program (separate, not yet scheduled); this
   step is the route-swap + closed-event gating + fidelity (spec/65 §3.9), with
   the full Cuts build tracked under spec/61.

### Phase 4 — Video surfaces (heaviest; QMediaPlayer + spec/56)

- **11 Video Picker** — `VideoPickerPage` rides the viewport's arm-on-landing
  video path (spec/63 §3, slice 5e already proved it); poster extract; transport
  bar with marker positions. Retire `picked/video_pick_page.py`.
- **12 Video Editor** — same player integration + spec/56 marker partitions +
  draggable trim/segment composer; segment export via the spec/60 engine. Retire
  `edited/edit_video_page.py`.

### Phase 5 — Foundation + closeout (the "done" gate)

- **Cross-cutting (spec/65 §4):** PageHeader weight, card shadow depth, ThemeToggle
  polish, scrollbar consistency, loading skeletons on gateway-fed pages.
- **Brand (spec/65 §0.1–§0.2):** the `MiraLogo` component + `TitleBar`, the
  `M✦ıra` wordmark, the "See the keepers." tagline, app/installer icon.
- **Tech-debt (spec/65 §5):** new tests for the routed-but-untested surfaces
  (§5.2), the settings-persistence bug (§5.3), the `tr()` sweep (§5.4), the
  locked-keymap verification smoke (§5.5), memory-file updates (§5.6).
- **Retire all legacy modules**; full-suite sweep; re-run the spec/62 probes.

---

## 5. Per-session recipe (every session, spec/65 §6)

1. Re-verify the surface's current state against `main_window.py`.
2. Open the surface's **`.html` mockup** on the Desktop
   (`MiraCrafter Redesign/surface-NN-*.html`) — the *feel*, not the `.md`.
3. Compare to the running page; write a short surface-specific punch list.
4. For a reconciliation: embed the viewport, absorb the legacy wiring, swap the
   MainWindow route, retire the legacy module.
5. Attack 3–5 items; keep scope to this one surface.
6. Verify with a **real-asset** screenshot smoke (placeholder smokes won't show
   icons/blurred-fill); run `verify.bat`.
7. Commit. Pause for Nelson's eyeball. Move on.

## 6. Definition of done

- `main_window.py`'s page stack instantiates **only redesigned pages**; no live
  imports from `mira/ui/picked/`, `mira/ui/edited/` (page shells),
  `mira/ui/shared/cuts_shell.py`, `mira/ui/base/day_grid_*`, `picked/grid_view.py`.
- Every surface eyeballed against its HTML mockup and signed off.
- Full suite green, including new tests for the routed surfaces and the
  keymap-verification smoke; `tr()` coverage swept; the settings bug fixed.
- The Mira brand is visible (logo, title bar, tagline, app icon).
- The spec/62 nav probes re-run and recorded.

## 7. Guardrails

- **Don't batch surfaces.** One per session; the migration batched and produced
  port + recolor.
- **Don't rewrite engines** to restyle a surface — wire to PhotoViewport /
  PhotoCache / adjustment pipeline / the spec/60 batch engine.
- **The spec/63 keymap is LOCKED** — P/X/Space/C/Tab/Enter/F10/F11/Esc unchanged.
- **Verify with real assets**, not placeholder smokes.
- **Scope creep is the enemy** — "more chrome" ≠ "the design's voice" (spec/65 §6).
- **Every decision surface carries the identity header** ([spec/71](71-surface-identity-header.md)):
  phase-colour chrome + phase name + purpose line + the surface's legend; §5a
  state colours stay on the cell borders only. Build new surfaces with it from
  the start.

## 8. Rough size

13 surfaces + brand + tech-debt. Fidelity-only surfaces ≈ 2–3 h each (spec/65's
estimate); the four heavy reconciliations (06, 07, 08, 12) are days each. This is
a multi-week program — the phasing exists so each lands shippable on its own.

## 9. Related

- [spec/65](65-redesign-fidelity-pass.md) — the punch list this orders into a plan.
- [spec/63](63-photo-viewport.md) — the one viewport engine every shell embeds.
- [spec/66](66-collect-pick-edit-export.md) / [spec/67](67-implementation-handoff.md) /
  [spec/68](68-phase-redesign-coordination.md) — the phase spine (precondition).
- [spec/69](69-icon-wiring-fidelity.md) — the icon sweep (precondition).
- [spec/61](61-share-event-cuts.md) — the Share/Cuts functional program (surface 09 depth).
- [spec/60](60-batch-export-engine.md) — the batch engine surfaces wire to, never rewrite.
