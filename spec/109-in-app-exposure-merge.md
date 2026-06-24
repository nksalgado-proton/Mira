# 109 — In-app exposure merge (Mertens) as a bracket-consolidation step

**Status: SHIPPED (Nelson 2026-06-22) across four commits — core kernel
([c7fad93](https://github.com/nksalgado-proton/Mira/commit/c7fad93)) →
merge engine ([c6a9309](https://github.com/nksalgado-proton/Mira/commit/c6a9309))
→ adopt + producer + badge ([d3182e5](https://github.com/nksalgado-proton/Mira/commit/d3182e5))
→ Edit-entry action ([07858f5](https://github.com/nksalgado-proton/Mira/commit/07858f5)).
What landed: `core/exposure_fusion.py` Qt-free `fuse_exposure_arrays(arrays,
*, align=True)` (the `QPixmap` wrapper delegates — inv. #8 preserved);
`core/exposure_merge.py` + `mira/picked/exposure_merge_job.py` (JPEG/RAW
decode → Mertens, AlignMTB on → scratch **TIFF** → `adopt_stack_output(
producer='mira')`, on the existing `IngestJob`/`BatchJobQueue`); schema
**v13→v14** adds `stack_bracket.producer` (`'mira'`|`'external'`, default
`'external'`); `adopt_stack_output` takes `producer` and **rejects
`'mira'` for focus brackets** (focus has no in-app producer);
`core/export_provenance.stack_output_origin_label` + a `days_grid_page`
fallback resolve the `Mira` vs `ext` wordmark;
`external_returns.ReturnsReport.unmerged_exposure_bracket_keys` surfaces
the mergeable set, and the Edit-entry returns box gains "Merge exposure
brackets in Mira" (enqueues the job with progress/cancel on the batch
line). The §6 decisions landed as specced (scratch TIFF, AlignMTB on).
`verify.bat` green except a pre-existing, unrelated teardown error in
`test_editor_page_video_workshop.py` (FFmpeg on a bad fixture `v1.mp4`;
passes in isolation). Original proposal follows.**

**Status: PROPOSED (Nelson 2026-06-22). Makes Mira a native *producer* for
exposure-bracket consolidation: an explicit "Merge in Mira" action that
fuses a picked exposure bracket (Mertens, OpenCV — the algorithm already
behind the Picker's Combined preview) into a single master, materialized
the **same way an external stacker's output is** — into
`Original Media/Merged/` via `EventGateway.adopt_stack_output`, as a
picked `stack_output` item that still flows into Mira's Edit. It is on the
**bracket-consolidation round trip (source side), NOT the processing round
trip (export side)** — it never touches `Exported Media/`. Touches
`core/exposure_fusion.py` (add a Qt-free array core), a new merge job in
`mira/` on the batch engine, the Edit-phase bracket-resolution surface,
and a small provenance/producer tag. Relates to spec/57 + spec/72 (the
round trips), spec/89/108 (the `Mira` vs `ext` badge), and reuses
`adopt_stack_output` wholesale. Charter invariant #7: writes only to the
sanctioned additive `Original Media/Merged/`.**

## 1. Where it sits — the bracket-consolidation lane (source side)

Two round trips land on **opposite sides of Edit** (clarified 2026-06-22):

| | Processing return (LR/LRC/C1…) | Bracket consolidation |
|---|---|---|
| Lands in | `Exported Media/` (ship set) | `Original Media/Merged/` (source) |
| Provenance | `third_party` | `stack_output` |
| Vs Edit | **output side** — replaces Mira's Edit | **input side** — feeds Mira's Edit |

In-app Mertens is the **second lane**: it produces a new *original* master
(picked-by-construction) that the user can then develop in Mira's Edit, or
leave untouched (in which case it simply exports as-is at Export — so
"source-side" subsumes "skip Edit"; no separate bypass path is needed). It
is just an **in-app producer** plugged into the *exact same* artifact +
adoption an external stacker uses today. Focus brackets keep using the
external producer (no built-in focus stacker); exposure brackets gain a
Mira producer. One adoption path, two producers.

## 2. Trigger & phase — an explicit step at Edit

- **Phase: Edit** (the develop phase). Brackets are detected at ingest and
  travel as a unit through Pick; consolidation runs on a *picked* bracket.
- Mira already derives the reminder "picked focus/exposure brackets with
  **no merged result**" (`external_returns.ReturnsReport.unmerged_bracket_count`,
  surfaced on entering Edit/Export). Use that surface: an **unresolved
  exposure bracket** offers, as an explicit user action, **"Merge in
  Mira (Mertens)"** alongside the existing "send to external tool"
  (round-trip) and "keep frames" options. **Never automatic** — Nelson's
  "separate step" requirement.
- Offer it both per-bracket and as a batch "resolve all unmerged exposure
  brackets in Mira" at the Pick→Edit handoff.

## 3. The engine — Qt-free core + a background job

- **Refactor `core/exposure_fusion.py`** to expose a **Qt-free** numpy
  core: `fuse_exposure_arrays(arrays: list[np.ndarray], *, align: bool)
  -> np.ndarray` (BGR uint8 in/out), wrapping `cv2.createMergeMertens`
  with an optional `cv2.AlignMTB` pre-align pass. The existing
  `fuse_exposures(list[QPixmap]) -> QPixmap` Qt wrapper (the Combined
  preview) becomes a thin adapter over this core, so preview and merge
  share one implementation (charter inv. 8 — the core stays Qt-free).
- **A new merge job** (`mira/` layer, on the batch engine — `IngestJob` /
  `BatchJobQueue`, spec/60/84, since full-res multi-frame fusion is heavy
  and must not block the UI): decode each bracket member's full-res frame
  to a BGR array (reuse the existing decode path; handles JPEG **and**
  RAW), call `fuse_exposure_arrays(..., align=True)`, write the result to a
  scratch file.
- Progress + cancel ride the batch line like export/ingest.

## 4. Materialize + adopt (reuse `adopt_stack_output`)

After the job produces the fused scratch file:

- Call **`gateway.adopt_stack_output(scratch_path, bracket_key=…,
  bracket_kind="exposure_bracket", member_item_ids=[…])`** — the existing
  method moves it into `Original Media/Merged/` (copy → sha-verify →
  delete source), writes the `stack_bracket` / `stack_member` rows + the
  `provenance='stack_output'` item on the bracket's day, sets
  `phase_state('pick','picked')`, and the caller re-runs the links
  rebuild so the master appears at the projection root. **No new
  materialization code** — the in-app merge is just a different way to
  produce the `scratch_path`.
- **Reversible:** the bracket frames stay untouched in the captured tree,
  so "un-merge" = drop the master + its `stack_bracket` rows; the frames
  reappear (mirror the external-return undo if one exists).

## 5. Producer tag → the `Mira` badge

`adopt_stack_output` records `provenance='stack_output'` regardless of who
produced the pixels. To badge an in-app merge **`Mira`** vs an external
one **`ext`** (spec/108), record the **producer** on the stack output —
e.g. a `producer` value (`'mira'` | `'external'`) on the `stack_bracket`
row (or an `output_origin` tag the badge resolver reads). The origin
wordmark surface (spec/89/108) shows `Mira` for `producer='mira'`.

## 6. Decisions to lock

1. **Output format.** 16-bit TIFF preserves the most latitude for the
   subsequent Edit develop, but `createMergeMertens` yields float→8-bit
   today and TIFF is large. Proposed: **high-quality TIFF** (8-bit to
   start; 16-bit a later option) so the master is a clean develop source,
   not a re-compressed JPEG. Confirm.
2. **Alignment default.** Default **AlignMTB on** (safe for handheld;
   costs a little time); an opt-out for known-tripod sets. Confirm.
3. **Naming.** The merged master's filename follows the existing
   `adopt_stack_output` convention (on the bracket's day, beside its
   siblings) — no new naming rule.

## 7. Acceptance

- A picked exposure bracket shows an explicit "Merge in Mira" action in
  Edit; running it produces, on the batch line, a single master in
  `Original Media/Merged/`, picked-by-construction, badged `Mira`, that
  appears at the projection root and is developable in Edit.
- The master is byte-verified on adopt; the bracket frames remain in the
  captured tree (reversible).
- An un-developed merged master exports as-is at Export (source-side
  subsumes skip-Edit). A developed one exports its `mira_render`.
- Focus brackets are unaffected (still external-only). The processing
  round trip (`Exported Media/`) is untouched.
- The Combined preview still works (now sharing the refactored core).

## 8. Tests

- `tests/test_exposure_fusion_core.py` — `fuse_exposure_arrays` fuses a
  synthetic 3-exposure bracket to a mid-toned result; `align=True` runs
  AlignMTB without error; single/empty inputs degrade gracefully; the Qt
  `fuse_exposures` wrapper delegates to it.
- `tests/test_in_app_merge_adopt.py` — the merge job's scratch output,
  passed to `adopt_stack_output`, yields a `stack_output` item under
  `Original Media/Merged/`, `picked`, `producer='mira'`, with
  `stack_bracket`/`stack_member` rows; bracket frames untouched; undo
  restores them.
- Badge test: a `producer='mira'` stack output renders `Mira`; an external
  one renders `ext`.

## 9. Dependencies / order

- Independent of the PTE specs (105/107) and the music picker (106).
- Pairs naturally with **spec/108** (the `Mira`/`ext` badge flatten +
  the round-trip doc) — land 108's badge model with, or just before, the
  producer tag here.
