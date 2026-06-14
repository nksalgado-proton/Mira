# spec/41 — Mira X · Completion Sprint

**The 2-3 week sprint plan to ship Mira X 1.0 — Nelson's personal-use tool.**
Authored 2026-06-04. Locks the scope of the work between *here* and *X 1.0 shipped*.

> **This is a scope-locked sprint, not an open-ended development plan.** Every item
> below is in scope; nothing else is. "While we're at it" creep is the named risk.
> Backlog everything else.

---

## 0. THE GOAL

A tagged, installable, working Mira X 1.0:

- Built with **Nuitka** → single Windows exe
- Packaged with **Inno Setup** → `.exe` installer
- Installed on **Nelson's desktop AND laptop** for the next trip
- End-to-end working against a real event (a real trip's worth of photos)
- Real bugs found during that test get fixed; non-bugs get backlogged
- Released as `v1.0.0` on the `rebuild/relational-core` branch (or a new
  `release/x-1.0` branch — pick at sprint start)

This product is **Mira X** — the enthusiast variant for Persona 2 (Nelson).
It is **not** Mira V1 (Effortless craft / Persona 1 / public free product).
For V1, see [spec/40 — V1 Effortless Craft](40-v1-effortless-craft.md).

---

## 1. WHY THIS IS THE PRAGMATIC PLAY

Per [[project_two_product_strategy]] (2026-06-04):

- The current `rebuild/relational-core` branch is ~80% done as an enthusiast tool. Most
  of what's shipped (full AdjustmentSurface, per-style AUTO, wizard, classification
  engine, multi-camera reconciliation, three named Culler surfaces) serves Persona 2
  directly. Finishing it for personal use is a 2-3 week effort.
- **Nothing shipped gets thrown away.** All of it lives on as Mira X.
- **V1 starts on a clean foundation, not a refactor.** Building V1 fresh from spec/40,
  reusing the shared core, is much cleaner than gutting an enthusiast tree.
- **Architecture gets validated** by shipping X end-to-end before V1 borrows from it.
- **Nelson gets a working tool now** — desktop + laptop, real trips, this month.
- **The X-as-future-product option** comes for free as a side effect.

---

## 2. THE SCOPE — 7 work items

> **Status (2026-06-07, post-audit):**
> Item 1 — Share port: **PARTIAL** (shell + navigator + overview + review shipped; 7 surfaces + gateway additions remain).
> Item 2 — Pick surface verification: **DONE** via spec/48 Slice B + the deep rename pass; eyeball walkthrough has been running for days.
> Item 3 — Distribute / Backup-Restore / Audit: **NOT YET AUDITED**.
> Item 4 — Wizard auto-open: **SHIPPED**.
> Item 5 — Build retarget: **NOT STARTED** (build.bat still targets `pythonw -m ui.app`).
> Item 6 — End-to-end test: **IN PROGRESS** (Nelson's daily walkthrough has been generating commits).
> Item 7 — Ship: pending the above.
>
> The vocabulary has hardened since this spec was authored:
> **Curate → Share**, **Process → Edit**, **Cull/Select → Pick** at every layer.
> Item 1 below was written as "Curate"; substitute "Share" throughout. A fresh
> Share manifest will replace the archived `_archive/43-curate-port-manifest.md`.

In rough sequence. Some can parallelize.

### Item 1 — Share port from legacy → rebuild

**The big item.** ~5-7 days.

- Per memory `project_curate_redesign_resume_2026_05_29`: the LEGACY Curate is
  code-complete (R1–R8, 00.142–00.169, HEAD `d00d086`). Bases + subsets + Collections
  page all work. Only a manual end-to-end pass remains on the legacy.
- The REBUILD has no Curate phase ported. This is the work.
- Approach (per the Supreme Rule, charter §0): port verbatim from `ui/curate/` →
  `mira/ui/curate/`. Swap `ui.*` → `mira.ui.*` imports. Change ONLY the
  data-access calls to the gateway. Nothing else moves.
- **Manifest-first.** Open legacy `ui.curate.curate_page`, list every dialog/widget +
  every data-access call inside, present manifest, get Nelson's OK, port.
- Gateway additions likely needed:
  - `curate_tag` per item (the curate-side rating / colour / flag)
  - Subset persistence (subset definition + items)
  - Base persistence (portfolio bases)
  - Cross-event queries (for the Collections page)
- Cell-colour rule for the Curate Day Grid (analog to `cell_color_for_process_item` —
  this is one of the reusable lessons from the Process port).
- The touched-set back-refresh pattern (`[[feedback_back_refresh_track_touched_items]]`)
  MUST be replicated on any Curate Day-Grid host page.
- `phase_day_progress` needs a Curate override (reads `curate_tag` instead of
  `phase_state`, like Process reads `Adjustment.process_exported`).

### Item 2 — Unified Select surface verification

~1-2 days. **Revised 2026-06-06** per [spec/48 — The 4-phase pivot](48-four-phase-pivot.md): the per-camera Cull pass and the separate Select phase merge into one unified Select phase. The "three named Cull surfaces" model is retired.

- Verify the **video Cull page** (rebuild's `video_cull_page`) is wired against the
  relational store. Per memory `project_cull_m2_scope_corrections`, it was flagged as
  "stale pre-relational JSON-model port, unwired" and porting it onto the
  clip-as-item gateway API was a REQUIRED M2 deliverable.
- If unwired: port it now.
- Verify the two surviving surfaces work end-to-end on a real event:
  - **Fast Culler** (SD-card triage during Capture) — unchanged by the pivot.
  - **Unified Select surface** (all cameras + photos + videos, days list with per-day chronological interleaving, default-Discard, photos K/D, videos K/D + clip + snapshot creation) — the spec/48 Slice B deliverable. Verify both per-camera Cull-style internals and cross-camera Final-Cull-style behaviour land on this one surface.
- Confirm the 2-photo compare-grid EXIF-diff highlight is shipped (per the same memory).

**Dependency:** spec/48 Slice A (dashboard rewire to 4 phase tiles) + Slice B (unified Select mode on `BaseCullSurface`) must complete before this verification item can run end-to-end. Item 1 (Curate port) is independent and can parallelize.

### Item 3 — Distribute + Backup/Restore + Audit

~2-3 days.

- Audit what's shipped vs what's missing.
- Port any missing surfaces from legacy.
- For Distribute: PTE bundle export is the main legacy surface; verify it works in the
  rebuild.
- For Backup/Restore: the round-trip through JSON is foundational; verify event.db
  ↔ JSON ↔ event.db is byte-faithful (per the charter's restore = migration code path).
- For Audit: the consistency-audit engine; verify it produces sensible reports on a
  rebuild-shaped event.

### Item 4 — Wizard auto-open + first-run flow

~0.5-1 day.

- The wizard is shipped (memory `project_wizard_shipped`). Verify it auto-opens on
  first run of the rebuild. Verify it writes user classification rules to the rebuild's
  settings location, not the legacy's.
- First-run flow: wizard → events dashboard → "create your first event" prompt.

### Item 5 — Build pipeline retarget

~1 day.

- Current `build.bat` targets the legacy `ui/` entry point (`pythonw -m ui.app`).
- Retarget to `mira/` namespace (`python -m mira.ui`).
- Test Nuitka build produces a working exe.
- Test Inno Setup installer:
  - Installs to `Program Files\Mira X\`
  - Creates desktop shortcut (`Mira X.lnk`)
  - Creates Start menu entry
  - Configures `%LOCALAPPDATA%\Mira X\` for settings + logs
  - Handles upgrade-in-place (settings preserved across versions)
  - Adds uninstaller
- Naming inside the installer: `Mira X` (not just `Mira`) to distinguish
  from V1 when V1 ships later.

### Item 6 — End-to-end real-event test

~3-5 days (calendar; not all dev time — depends on Nelson's next trip).

- Nelson takes a trip (or selects a past trip's SD card).
- Runs the packaged exe end-to-end:
  - Ingest from SD card
  - Cull
  - Select
  - Process (photos + videos)
  - Curate
  - Distribute (PTE bundle if used)
- Bug-fixes what surfaces during the test.
- **Non-bug improvements get backlogged**, not fixed in this sprint.
- Failure modes to anticipate:
  - Settings location issues (legacy vs rebuild file confusion)
  - Path resolution issues (relative paths, base-path config)
  - Performance regressions (the Nepal materialize perf optimization was in legacy code;
    verify rebuild inherits)
  - Wizard first-run behaviour
  - Curate page back-refresh (the new pattern)

### Item 7 — Ship X 1.0

~0.5 day.

- Tag the release on the chosen branch
- Build final installer artifact
- Personal install on desktop AND laptop
- Write release notes (internal — for Nelson's reference, not public-facing yet)
- Update `spec/PROGRESS.md` to mark X 1.0 SHIPPED

---

## 3. OUT OF SCOPE — explicitly named

These are tempting to do "while we're at it." They are backlogged.

- **Anything described in spec/40** as V1-specific (Effortless craft 3-phase pipeline,
  style alternatives strip, scene cards, collages, time-budget Share, etc.). All V1.
- **Shared-core extraction** (the seam between X-core and V1-only-UI). Happens at V1
  kickoff, not now.
- **Repo restructuring** (monorepo with `mira_core/` + `mira_x/` +
  `mira/`). Happens at V1 kickoff.
- **Website / branding / distribution channel work.** Happens during V1 development.
- **Public release of X.** X 1.0 is Nelson's personal-use tool. Public-release
  decision is deferred to post-V1-success per the product-ladder option.
- **macOS / Linux builds.** Windows only.
- **New language localizations.** Existing En + Pt only.
- **Cull surface UX redesign.** Whatever's shipped, stays shipped.
- **AdjustmentSurface UX redesign.** Whatever's shipped, stays shipped.
- **Any feature flagged "v1.1+" or "v2" or "later" in legacy docs.**
- **Any architectural cleanup** that doesn't directly unblock an Item 1-7 deliverable.
- **Lock down or auto-update telemetry.** No telemetry. Period.

---

## 4. THE DISCIPLINE

This sprint succeeds or fails on scope discipline. Three rules:

1. **Every item closes before the next opens** (sequential pressure on Item 1, since
   it's the big one; parallelization is allowed where independent).
2. **Bugs found during Item 6 get fixed; non-bugs get backlogged.** A non-bug = "X
   could be better" without "X is broken."
3. **No new feature ideas** enter this sprint. They get written down for V1 or X 1.1.

If the sprint runs long (4 weeks instead of 2-3), the failure mode is almost certainly
scope creep, not bad estimation. Mitigate by going back to the scope list and pruning.

---

## 5. WHAT TRIGGERS V1 KICKOFF

X 1.0 shipped + a one-day pause to let it settle on Nelson's machines + a deliberate
"V1 kickoff" session that:

- Re-reads spec/40 with fresh eyes
- Decides repo strategy (monorepo vs separate)
- Maps shared-core extraction boundary
- Authors V1's first work-item spec (probably Capture)
- Updates `spec/PROGRESS.md` to mark X 1.0 SHIPPED + V1 kickoff started

V1 development can then run alongside X bug-fix / minor-feature releases as needed.

---

## 6. RELATED

- [spec/40 — V1 Effortless Craft](40-v1-effortless-craft.md) — the V1 product spec that
  comes after X 1.0 ships.
- [spec/00 — Charter](00-charter.md) — the Supreme Rule that governs the X port work
  (PORT legacy verbatim, change only data-access calls).
- [spec/PROGRESS.md](PROGRESS.md) — live handoff; updated after every session.
- Memory entries: [[project_two_product_strategy]] (the strategy this sprint
  implements), [[project_curate_redesign_resume_2026_05_29]] (legacy Curate is
  code-complete; rebuild port is the work), [[project_cull_m2_scope_corrections]]
  (video Cull port status gap to verify), [[feedback_clear_marks_button_pattern]] +
  [[feedback_back_refresh_track_touched_items]] (reusable patterns for the Curate port).
