# spec/48 — The 4-phase pivot

**Authored 2026-06-06 (Nelson). Supersedes spec/46's activity-centric framing.**

Reverses the activity-centric paradigm of spec/46 in favour of a simplified 4-phase model. Locks the next-step implementation plan. Read this before any new surface or routing work after 2026-06-06.

---

## ⛔ SUPREME RULE (unchanged from spec/00 §0)

**PORT legacy verbatim. Change ONLY data-access calls. Manifest-first per surface, no exceptions.** spec/46's own §0 supreme rule + §9 Preserved Assets Registry + §10 Translation Table **still govern every surface internal** — what changes here is only the navigation paradigm and the phase count.

The lessons preserved in [[feedback_reuse_legacy_ui_dont_recreate]] and [[feedback_exactly_like_before_means_dont_ask]] both apply: PORT, don't reinvent; "exactly like today" means everything not explicitly named is unchanged.

---

## 1. What changed

Nelson 2026-06-06: rolled back the activity-centric framing. The "7 activities × 4 stages" model retires. **Phases come back**, but **simplified to 4**:

| # | Phase | Description |
|---|---|---|
| 1 | **Collect** | Exactly as today's Capture surface. Planning activities embedded; event creation embedded; cell-phone EXIF assists time/location entry. **Quick Sweep** (the renamed Fast Culler) is the Collect-time fast-discard pass: the user sweeps through fresh card imports and Discards the obvious garbage (test shots, accidentals, blurry mistakes). Default-Keep — Discards are active choices. Same behaviour as today's Fast Culler. |
| 2 | **Select** | The OLD Cull surface, applied to ALL captured content at once — all cameras + photos + videos, days list with per-day chronological interleaving (photos, clusters, videos mixed). **Pick / Discard** decision. Default-Discard. Focus peaking + AF point overlay + sharpness rating all on (the photo-evaluation helpers carry over from old Cull). Videos: Pick / Discard + clip + snapshot creation. Per-camera Cull pass is GONE. Separate Select phase is GONE. Both merge into this one phase. |
| 3 | **Edit** | Exactly as today's Process surface (`BaseProcessSurface` chassis). |
| 4 | **Share** | To be revised separately. Existing Curate code stays in place under the new label until the revision spec lands. |

### 1.1 Vocabulary lock — one name per phase, applied at every layer

**Decision (Nelson 2026-06-06):** rename across the codebase. **The earlier "internal stays `cull`" lock is reversed.** Single name per phase, applied uniformly at every layer — UI label, module directories, file names, classes, schema `phase_state.key` values, schema `phase_state.state` values, settings keys, QSS roles, test names, spec text. The fresh-start window (events wiped before Slice B testing) makes the migration free.

Rationale: keeping duplicate names would cost cognitive translation forever in every spec / commit / grep / code review. Renaming once, now, removes the photographer jargon from the codebase permanently — aligned with the V1 / Persona 1 goal of making the tool legible to non-photographers.

**The full active vocabulary:**

| Concept | New name | Renamed from |
|---|---|---|
| Phase 1 | **Collect** | Capture / `capture` |
| Phase 2 | **Select** | Cull / `cull` |
| Phase 3 | **Edit** | Process / `process` |
| Phase 4 | **Share** | Curate / `curate` |
| Decision verbs | **Pick / Discard** | Keep / Discard |
| Internal state value | `'picked'` | `'kept'` |
| Pick hotkey | **P** | K |
| Discard hotkey | **D** (unchanged) | D |
| Collect-time triage tool | **Quick Sweep** | Fast Culler |

**Cascade reach** — wherever the old vocabulary appears, the new vocabulary takes its place. Non-exhaustive audit:

- **UI strings**: phase tiles, `&Event` menu entries, Settings dialog tabs (and their internal keys), status indicators, funnel labels, donut captions, progress strings, tooltips, dialog titles, empty-state messages, window titles.
- **Modules + files**: `mira/cull/` → `mira/select/`; `mira/ui/culler/` → `mira/ui/picker/` (or similar — Slice 0 manifest picks); `cull_*.py` → `select_*.py`; `fast_culler_page.py` → `quick_pick_pass_page.py`.
- **Classes**: `BaseCullSurface` → `BasePickerSurface` (or `BaseSelectSurface` — Slice 0 picks); `CullPhotoSurface` → `PhotoPickerSurface`; `VideoCullPage` → `VideoPickerPage`; `FastCullerPage` → `QuickSweepPage`; `KeptRatioDonut` → `PickedRatioDonut`; etc.
- **Schema**: `phase_state.key` enum values `'capture' / 'cull' / 'process' / 'curate'` → `'collect' / 'select' / 'edit' / 'share'`; `phase_state.state` value `'kept'` → `'picked'`.
- **Settings**: `capture_default_state` → `collect_default_state`; `cull_default_state` → `select_default_state`; `process_default_state` → `edit_default_state`; `curate_default_state` → `share_default_state` (where present).
- **QSS roles**: `#CullState_*` → `#SelectState_*` (or `#PickerState_*`); `#KeptRatio*` → `#PickedRatio*`; etc.
- **Tests**: `test_cull_*.py` → `test_select_*.py`; function names follow.
- **Specs + docs**: spec/*.md and docs/*.md references to old names get cleaned up alongside or just after Slice 0.

**Collisions to resolve in Slice 0:** the old separate-Select code has `select_model.py`, `SelectPage`, `SELECT_CONFIG`, `select_pool_ids` — these collide with the cull-renamed-to-select names. Slice 0 + Slice B together absorb the old Select code into the renamed unified surface; the manifest picks the survivor for each colliding identifier.

---

## 2. Conceptual decisions locked

From the 2026-06-06 alignment turns:

1. **Plan = only inside Collect + event creation, exactly as today.** No "Plan" phase tile on the dashboard.
2. **Fast Culler kept exactly as today.** It is a Collect-time tool; not affected by the Select unification.
3. **The Select surface shows the days list, and within each day, photos / clusters / videos chronologically interleaved — exactly as today.** No flat-stream alternative.
4. **Default state for Select = Discard.**
5. **Dashboard chassis kept.** spec/46 Slice 2+3's `ActivityDashboardPage` is rewired to show 4 phase tiles (Collect / Select / Edit / Share), not retired.
6. **Share parked.** The existing Curate code (legacy + spec/43 Slice A rebuild) stays under the new "Share" label; its revision is a separate future spec.
7. **All photo-evaluation helpers stay on in the unified Select.** Focus peaking + AF point overlay + sharpness rating — all available, same as old Cull. (Old Select had stripped them; the unified Select gets them back since it is now the only decision pass.)
8. **Progressive filtering rule — load-bearing, applies to ALL phase transitions.** Content discarded in one phase does NOT appear in the next phase. Period. Quick Sweep discards filter out of Select's pool; Select discards filter out of Edit's pool; Edit discards filter out of Share's pool. The user's recovery path for "un-discarding" something later is to return to the earlier phase's surface and flip it back there — not next-phase silently re-showing the discard. See [[principle_progressive_filtering_no_leakage]].
9. **One name per phase, applied at every layer** (see §1.1). Reverses the earlier "internal stays cull" lock. Modules, classes, schema values, settings keys, QSS roles, tests, specs — all rename to Collect / Select / Edit / Share. The fresh start makes the migration free.
10. **Decision verbs become Pick / Discard everywhere.** Replaces Keep / Discard. Internal state value renames `'kept'` → `'picked'`. Hotkey K becomes P. "Pick" reinforces across the vocabulary (Picker / Pick / picked items).
11. **Quick Sweep replaces Fast Culler** as the Collect-time triage tool. The user actively discards the obvious garbage in this pass; the name describes that action plainly. Same behaviour as today's Fast Culler.
12. **Fresh start — no events to preserve.** Nelson will wipe all existing events before Slice B testing. No schema migration, no `phase_state('select')` cleanup, no carry-over UX for mid-flight events. The rename pass (Slice 0) and the unified Select implementation (Slice B) can both pick the cleanest path.

---

## 3. What spec/46 is preserved vs. retired

### Preserved (in active use)

- **`ActivityDashboardPage` chassis** (spec/46 Slice 2+3, shipped 2026-06-06) — keeps its shape; rewired to show 4 PHASE tiles instead of activity cards.
- **`LibraryHomePage`** (spec/46 Slice 1, partially shipped) — keeps current shape. The LibrarySidebar that was killed mid-Slice-1 stays killed.
- **Every preserved asset listed in spec/46 §9** — base surfaces, navigation rules, data-layer disciplines, visual standards, classification, TZ work — all unchanged.
- **Translation table in spec/46 §10** — the asset mappings still describe where each EventPlanPage / EventsDashboardPage piece lives. The mapping is structurally correct; only the *naming framework* changes (activity card → phase tile).

### Retired

- The **activity-centric user-facing language** — "Selection / Transformation / Classification / Compilation" as labels. Replaced by phase names: Capture / Select / Process / Curate.
- The **4-stage strip** (Get in · Refine · Mark · Deliver). The dashboard renders 4 phase tiles, no stage indicator above them.
- **spec/46 Slice 4 (Compilation card surface)** — folded into the Curate phase, which is parked for separate revision.
- **spec/46 Slice 5 (V1 strip-down)** — V1 design needs rethinking from scratch when V1 kicks off; this slice retires here. spec/40 (V1 Effortless Craft) is not affected by *this* spec; V1 design will be revised post-X-1.0.
- **spec/46 §4** ("V1 = chronological lens only / X = all three lenses") — that paragraph no longer governs. V1 design TBD.

### spec/46-shipped code that is affected

Three pieces of code shipped against the activity-centric framing now need conceptual remapping:

- `ActivityDashboardPage` — chassis kept; rewires from 5 activity cards → 4 phase tiles.
- `MainWindow._on_activity_activated` (the routing that drove the activity dashboard) — becomes `_on_phase_activated` style routing, but the routing target for "Select" is the unified Cull-style surface, not the existing Cull-only path.
- `Event` menu (former EventPlanPage actions) — unchanged. This is purely chrome relocation; same handlers.

This resolves [[project_selection_fusion_open_question]]: with unified Select feeding Process directly, the Cull / Select cross-phase mirror question collapses — there is one decision phase between Capture and Process.

---

## 4. Implementation plan

Four slices, each with a reuse manifest in chat before code lands (per the proven spec/43, spec/44, spec/46 Slice 1 / 2+3 pattern).

### Slice 0 — Vocabulary rename (mechanical, runs first)

Mechanical rename across the codebase to lock the new vocabulary before Slice A and B touch any behaviour. Pure rename — no behaviour changes. The fresh-start window (events wiped) makes this free.

Scope per the §1.1 cascade reach list: modules, files, classes, schema enum values (`phase_state.key` and `.state`), settings keys, QSS roles, tests, plus a sweep of spec/*.md and docs/*.md references.

**Verb cascade (Pick / Discard):** `kept` → `picked` everywhere it appears as a state value, count, ratio, or widget name (`kept_count` → `picked_count`, `KeptRatioDonut` → `PickedRatioDonut`, `phase_kept_count` → `phase_picked_count`, etc.). Hotkey K wiring becomes P; Discard hotkey D unchanged.

**Collision resolution:** the existing `select_model.py` / `SelectPage` / `SELECT_CONFIG` / `select_pool_ids` (from the old separate Select phase) collide with the cull-renamed-to-select identifiers. Slice 0's manifest names the survivor for each collision. Likely path: the OLD Select code retires (it was reading Cull-Kept-only, which the unified Select replaces); the renamed-from-Cull code becomes the canonical Select module.

**Schema migration:** since events are being wiped, this is a clean `DROP TABLE phase_state` + recreate with the new enum values, no data preservation. (Or, equivalently, increment `SCHEMA_VERSION` and run a fresh creation script.)

**Manifest must list:** every module / file / class rename; every collision and its survivor; the schema migration approach (drop-and-recreate vs version bump + ALTER); the hotkey rebind for K→P; the Settings keys' rename + the corresponding `settings.rebuild.json` re-write.

### Slice A — Dashboard rewire (5 activity cards → 4 phase tiles)

- Open `mira/ui/pages/activity_dashboard_page.py`; identify the 5 activity cards rendered today (per `spec/46-slice-2-3-combined-manifest.md`).
- Collapse to 4 phase tiles in this order: **Capture · Select · Process · Curate**.
- Card content (chart widget, CTA, kept-ratio donut, etc.) carries forward from the corresponding old PhaseButton per spec/46 §10.2:
  - Capture tile: `TimezoneMapWidget` + `CategoryPieWidget` (or whichever the spec/46-shipped Capture card uses today).
  - Select tile: `KeptRatioDonut`.
  - Process tile: `KeptRatioDonut`.
  - Curate tile: whichever the current Curate card / former-Curate-phase tile renders.
- Routing changes:
  - Capture tile → existing `capture_flow.run_capture`. **No change.**
  - Select tile → **unified Cull-style surface (Slice B).** All cameras, photos + videos, days list. NOT today's `CullDashboardPage` per-camera picker.
  - Process tile → existing `ProcessHostPage`. **No change.**
  - Curate tile → existing `CurateShell`. **No change.**
- Retire the stage strip (Get in / Refine / Mark / Deliver) from the chassis.
- Retire / repurpose the Classification + Compilation activity cards (they fold into Curate per §3).

**Manifest must list:** every activity card being merged or retired; chart widget reuse confirmations; routing changes; the stage-strip retirement.

### Slice B — Unified Select mode

- The Cull surface (`BaseCullSurface` chassis → `CullPhotoSurface` + `VideoCullPage`) already operates in `mode='cull'` (per-camera) and `mode='select'` (cross-camera over Cull-Kept).
- The unified Select needs a third mode (or a re-purpose of one of the two existing modes) that:
  - Operates over **ALL captured items**, not Cull-Kept-only and not per-camera-filtered.
  - Renders the days list with per-day chronological interleaving of photos, clusters, videos.
  - Photos: K/D. Videos: K/D + clip + snapshot creation (the F-029 / docs/24 design — preserved unchanged).
  - Default state: Discard.
  - Silent-sync on exit (output folder structure: open implementation question — see §5).
- Per-camera `CullDashboardPage` picker: retire its launch role for the unified Select. Audit other entry points before deletion.
- Existing `SelectPage` (per [[project_select_surface_shipped]]) and its model (`mira/cull/select_model.py`): repurpose or absorb. The Cull-Kept-only pool query (`select_pool_ids`) no longer fits — unified Select reads ALL captured items.
- The Select-style nudge dialog (`SelectNudgeDialog`, per [[project_select_design_frozen]]) — keep as a feature of the unified Select (still useful on the cross-camera kept set).

**Manifest must list:** the chosen mode strategy on `BaseCullSurface`; the fate of `SelectPage` + `select_model.py`; the fate of `CullDashboardPage`; the silent-sync output folder (or a parked decision pointer); the data-layer choice (see §5 Q1).

### Slice C — Spec + memory cleanup

- Mark spec/46 SUPERSEDED for the activity-centric framing (chassis preserved). ✅ done in this revision pass.
- Update spec/41 Item 2 ("Cull surface verification") to verify the unified Select instead of the three named Cull surfaces. ✅ done in this revision pass.
- Update spec/PROGRESS.md banner with the pivot context. ✅ done in this revision pass.
- Update memories: spec_46_supreme_rule, project_selection_fusion_open_question, project_select_design_frozen, project_select_surface_shipped, project_cull_entry_and_phasebutton_port_gaps, project_cull_m2_scope_corrections. ✅ done in this revision pass.
- Curate revision spec: park for separate session.

---

## 5. Implementation questions — settled and remaining

The Nelson Q&A pass on 2026-06-06 resolved most of the open questions; one remains.

### 5.1 Settled

- **Data layer key for the unified phase:** renames to `phase_state('select')` as part of Slice 0 (reverses the earlier "stays cull" lock — see §1.1 vocabulary lock). Internal name now matches user-facing.
- **Existing events' state:** moot. Nelson will wipe events before Slice B testing — no migration concern.
- **Photo-evaluation helpers in Select:** peaking + AF overlay + sharpness all on. Old `select_mode=True` "strip helpers" flag retires.
- **Quick Sweep → Select handoff:** Quick Sweep discards **filter out** of Select's item pool (per the progressive filtering rule — §2 #8). NOT carried forward as "already Discarded" with re-examine UI.
- **Nudge dialog firing:** keep; fires on bucket exit over the picked set. Behaviour preserved (renamed widget per §1.1).
- **Slice order:** Slice 0 (vocabulary rename) → Slice A (dashboard rewire) → Slice B (unified Select). Sequential; Slice A and B manifests can be drafted in parallel while Slice 0 executes.
- **`CullDashboardPage` (per-camera picker):** delete as part of Slice B. Audit other entry points first.

### 5.2 Still open

- **`02 - Selected/` folder structure for Edit / LRC / Helicon handoff.** Deferred — Nelson 2026-06-06: "We still have to design the process to insert the third-party into the mix. Let's wait a bit more. It will not be rocket science." Will be designed in a focused sub-call before any third-party integration work starts. Tracked in [[backlog_select_to_process_silent_sync]].

---

## 6. Related

- [spec/00 — Charter](00-charter.md) — Supreme Rule (unchanged).
- [spec/41 — Mira X completion sprint](41-xmc-completion.md) — Item 2 revised per this spec.
- [spec/42 — Surface unification](42-surface-unification.md) — `BaseCullSurface` + `BaseProcessSurface` foundation (unchanged; provides the chassis Slice B targets).
- [spec/46 — Activity-centric surface redesign](46-activity-centric-surface.md) — SUPERSEDED for activity-centric framing; §9 Preserved Assets Registry + §10 Translation Table still govern asset reuse.
- [spec/PROGRESS.md](PROGRESS.md) — live handoff; banner updated.
- Memories:
  - [[project_four_phase_pivot_2026_06_06]] — this pivot in memory form.
  - [[feedback_reuse_legacy_ui_dont_recreate]] — supreme rule (still governs).
  - [[feedback_exactly_like_before_means_dont_ask]] — the rule that emerged from this alignment turn.
  - [[project_selection_fusion_open_question]] — RESOLVED by unified Select.
  - [[project_cull_entry_and_phasebutton_port_gaps]] — per-camera picker retires for Select launch (audit other uses).
  - [[project_select_design_frozen]] — superseded by §1 unified Select description.
  - [[project_select_surface_shipped]] — needs follow-up; `SelectPage` + `select_model.py` repurpose or absorb decided in Slice B manifest.
  - [[backlog_select_to_process_silent_sync]] — still open; this pivot does not decide it.

---

## 7. Status epilogue — what actually shipped (added 2026-06-07 audit pass)

Spec/48 as written used "Select" as the phase 2 user label + internal name.
**Post-write reality:** the rename pass continued past Slice 0 — phase 2 is
now **Pick** at every layer (user label, modules, classes, schema enum,
hotkey). The conceptual locks (one phase, all cameras, all photos + videos,
default-Skip, helpers on, progressive filtering) all hold. Vocabulary:

| Spec/48 § | What it said | What shipped |
|-----------|--------------|--------------|
| Phase 2 label | Select | **Pick** |
| Decision verbs | Pick / Discard | **Pick / Skip** |
| State value | `'picked'` | `'picked'` (unchanged) |
| Skip state value | `'discarded'` (implied) | `'skipped'` |
| Skip hotkey | D | D (unchanged) |
| Old phase enum | `'cull'`/`'select'` | `'pick'` |
| Module dir | `mira/picked/` planned | `mira/picked/` shipped |
| Base class | `BaseSelectSurface` planned | `BaseCullSurface` retained (renames-only refactor would have meant gratuitous churn for tests; the chassis name is internal-only and Slice B's value was the routing change, not the class name) |
| Slice status | 0 / A / B sequential | **All three shipped**, archived as as-built manifests |

The two open items the pivot named are still open:
- `02 - Selected/` folder structure for third-party hand-off
  (`[[backlog_select_to_process_silent_sync]]`)
- Share revision (this pivot parked it; legacy Curate code is being ported
  verbatim per Supreme Rule into `mira/ui/shared/` — Share manifest
  in flight per `spec/PROGRESS.md`)

