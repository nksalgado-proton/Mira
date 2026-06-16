# Mira — Development Backlog (to v1 complete)

**Compiled 2026-06-15.** Combines Nelson's product backlog with the in-flight
closeout captured in `spec/73`, `spec/72`, and the 06-14 handovers. Two layers:
**Part A** finishes the build that's already in flight; **Part B** is net-new
feature development. Sizing is rough (S ≤ 1 session · M = 2–4 · L = multi-session
+ design). Items that already have a governing spec are flagged so nothing gets
re-litigated.

---

## Part A — Finish the current build (closeout)

These are loose ends in the migration that's underway, not new product. Order
follows `spec/73`'s suggested closeout: Tier 1 bugs → Tier 2 legacy retirement →
Tier 3 tests → Tier 4 QSS → confirm Tier 5.

### A1. Phase 5 closeout — *governed by `spec/73`*  (M)
- **Tier 1 functional bugs:** chronological placement of clips/snapshots in Cuts;
  New Cut dialog live pool/match counts (real probe binding); wire Load/Save
  template buttons; Days Lists bulk actions (currently log-only stubs).
- **Tier 2 legacy retirement (DoD blocker):** drop live imports of the legacy grid
  modules under Share/Cuts; retire `shared/new_cut_dialog.py` (clean swap off the
  adapter); clear remaining `mira/ui/picked/` live imports.
- **Tier 3 test holes:** Editor write-path persistence test; Days Grid Pick-mode +
  locked-keymap suite; Days Lists tests (currently zero); Picker keymap smoke;
  Cuts chronology + live-filter-count tests; Export menu→export-mode integration.
- **Tier 4 QSS/housekeeping:** move inline `setStyleSheet` to theme roles
  (`_PoolCard`, New Cut dialog); dead-import + "cull" docstring scrub; fix stale
  `spec/70` Days-Lists row.
- **Tier 5 confirmations (not bugs):** per-day-only export trigger; Editor
  video-workshop F10 lens-only preview.

### A2. Quick Sweep — *net-new, no UI yet*  (L)
The largest genuinely-unbuilt piece. Carry forward from the handovers:
per-event QS write-back design (what `saved` means — separate
`phase='quick_sweep'` ledger vs. direct pick marks); wizard QS modal tests;
port `scripts/smoke_surface_quick_sweep.py` onto the production
`_qs_build_*` helpers so the smoke is load-bearing; revisit the 33 skipped
`test_quick_sweep_clusters.py` tests.

### A3. Surface 12 — confirm final state  (S)
In flight per `spec/73`; verify it's done and folded into the audit.

### A4. WhatsApp filename-parser fallback  (S)
Optional. `^WhatsApp Image YYYY-MM-DD at HH\.MM\.SS(?: \(\d+\))?\.jpeg$` parser
before the mtime fallback, lifting the seven undated WhatsApp images out of the
`_no_timestamp` bucket. Left as an explicit "want me to?" in the handover.

---

## Part B — Remaining feature development (the roadmap)

Nelson's eight items, deduped against the specs and fleshed out.

### B1. Third-party round-trip correction + user support/help  (M–L)
- *Design is done — `spec/72` governs; implementation NOT yet scheduled.* Two
  distinct round trips that must not be conflated: external-editor returns
  (Model B — hardlinked straight into `Exported Media/`, keep-or-delete, never
  re-enters the creative Editor) and stack consolidation (new master adopted into
  `Original Media/Merged/`, flows Pick→Edit→Export). Build the return scan,
  provenance badges, and the lineage/delete semantics per spec/72.
- **User support / help** is the unspecced half: in-app help surface, the
  round-trip explainer, and onboarding for the external-editor workflow. Needs a
  help-system decision (see open question below).

### B2. Cross-event Cuts = library-wide search  (L) — *extends `spec/61` Cuts + `spec/32` dynamic collections*
A search/filter layer over the whole library whose results are saved as Cuts
(grid-visualizable media collections). Components:
- **Events filter:** open/closed, year, any event-header property.
- **Phase polls:** Collected / Picked / Edited / Exported counts as filters.
- **Global Cuts as filters:** compose library queries from existing Cuts (pool
  algebra, same model as `spec/61`'s `#exported − #cut_1 + #cut_2`).
- **Media filter** on any photo/video field: style (Macro, Wildlife…), hardware
  (camera, lens, flash), capture settings (focal length, aperture, ISO, shutter),
  etc. — implies EXIF/metadata is indexed and queryable.
- **Results saved as Cuts**, viewable as a grid. This generalizes the current
  per-event Cut into a cross-event "smart Cut."
- *Decisions needed:* whether cross-event Cuts share the Cut schema or get their
  own; how the search ledger relates to the per-event decision ledger.

### B3. Templates — clarify the concept (local + global)  (M) — *partially exists*
The New Cut dialog already carries a template store (Load/Save template — being
wired in A1), so **Cut templates** exist at the event level. Open work is the
**concept**: what a template captures (Cut recipe? export preset? tone/look?
event scaffold?) and the **local vs. global** distinction (per-event vs.
library-wide reusable). Write the governing spec before building.

### B4. Face recognition as a filter — research  (research → L)
Evaluate offline-capable Python options (charter invariant #3: **no network
calls**, so cloud APIs are out). Candidates to assess: `face_recognition`
(dlib), InsightFace, ONNX-runtime face embedding models. Deliverable: a research
note on accuracy/footprint/licensing/offline-bundling, then a person/face field
that feeds the B2 media filter.

### B5. Maps for slideshows — research/support  (research → M)
What support Mira can offer for map generation from geotagged media. Offline
constraint matters (no live tile servers without a network exception).
Investigate offline tile/static-map options and how a map slide would hand off to
PTE. Research note first.

### B6. Collages from Cuts — research/support  (research → M)
Generate collages from a Cut's exported files. Define scope: in-app composition
vs. export to an external tool, layout engine, output format. Research +
mini-spec.

### B7. Database protections + redundancy strategies  (M) — *extends charter invariant #6*
Beyond the existing atomic write-then-rename: backup strategy for `event.db` and
the user-data store, integrity checks / corruption recovery, redundancy
(snapshots, copies), and a restore path. Offline-first, local-only. Needs a
design spec.

### B8. Always prepend the year in the events title list  (S) — *quick win* — *touches `spec/64` event header / `spec/71`*
Today the year is prepended only for trips, not for general events. Make it
uniform across the events list. Smallest item on the list — good warm-up.

---

## Suggested sequencing

1. **Part A** first — close out the migration (A1 → A2 → A3 → A4) so the tree
   hits its definition-of-done before new surfaces pile on.
2. **B8** anytime — trivial, independent.
3. **B1** next — design already locked in `spec/72`; just needs building + the
   help surface.
4. **B3** (templates) — clarify before B2, since cross-event Cuts may lean on the
   template concept.
5. **B2** — the big one; depends on EXIF/metadata indexing, which also unblocks
   B4's filter integration.
6. **Research items B4/B5/B6** — can run as parallel research notes anytime; build
   after their specs land.
7. **B7** — schedule before v1 ship; it's a safety net, not a feature.

## Open questions to resolve
- **Help system:** what mechanism for in-app help/support (B1)? Offline, so likely
  bundled content — needs a decision.
- **Cross-event Cut schema (B2):** reuse the Cut schema or a new entity?
- **Template scope (B3):** what exactly does a template capture, and where does
  the local/global line sit?
- **Network exceptions:** B5 (maps) may want tile data — confirm whether any
  allow-listed network use is acceptable, or strictly offline assets only.
