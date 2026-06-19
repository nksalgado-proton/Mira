# spec/66 — Collect / Pick / Edit / Export (Share becomes a closed-event state)

**Authored 2026-06-14 (Nelson). Revises the 4th phase of spec/48.**

spec/48 locked a 4-phase model — Collect / Pick / Edit / **Share** — but
explicitly *parked* Share ("to be revised separately"; spec/48 §1 row 4, §2
#6, §5.2, §7). This spec is that revision. It governs the phase model from
here; spec/48's phase **count** and its Collect/Pick vocabulary still hold,
only the 4th phase changes.

---

## 1. The decision

**The four working phases are Collect → Pick → Edit → Export.**

**Share is not a phase.** Share is a *permanent state* of a **closed** event:
the place where the files that survived the whole pipeline are assembled into
**Cuts** (spec/61) for hand-off. It has no progress bar and no pipeline tile;
it is reached through a closed event, not stepped through like a phase.

| # | Phase | What it is | Progress metric (per day) |
|---|---|---|---|
| 1 | **Collect** | SD-card / past-photos ingest, day plan, Quick Sweep | captured items present (day done once it has captures) |
| 2 | **Pick** | One unified decision pass across all captured content (default-Skip) | **decided / captured** — review completeness |
| 3 | **Edit** | Fix classification, tone, crop — *or leave as-is*; a standard-correction baseline is applied to every keeper automatically | **reviewed / picked** — keepers cleared through the Edit pass |
| 4 | **Export** | A green/red survival pass over the keepers; materialise the survivors to processed JPEGs | **exported / picked** — keepers materialised to a file |
| — | *Share* | *(state, not a phase)* assemble Cuts from exported files on a **closed** event | *none — not a progress bar* |

Notes on the metrics (Nelson 2026-06-14):

- **Pick** measures *review completeness* (how much of the day you've decided),
  not how many you kept. "Picked" = the keepers, the subset that feeds Edit.
- **Edit** progress = keepers you've passed through the Edit pass ÷ picked. It
  is **not** a pixel-change count: leaving a file as-is (it already carries the
  standard-correction baseline) still counts as cleared. Manual tone/crop is
  optional polish, not a gate.
- **Export** progress = **exported ÷ picked** = *actual materialised files* —
  the real artifact that survives the pipeline and that Share later draws on.
- **Bars encode phase identity, not state (Nelson 2026-06-14).** Phases advance
  freely (per-day, non-linear: a user may run one day all the way to Export, or
  sweep one phase across every day), so "in progress" / "closed" is not a real
  per-phase status. The events-card pipeline bars are therefore coloured by
  **phase** — Collect blue · Pick accent · Edit amber · Export green (the
  closed-card stat palette) — with **length = %**. No done/in-progress/zero
  state colour. (Same applies to the PhasesPage donuts in slice 3.)

### 1.1 The Edit and Export surfaces are separate (Nelson 2026-06-14)

Edit and Export are **two distinct surfaces**, deliberately de-cluttered:

- **Edit** is purely creative: correct classification, adjust tone, crop —
  *and nothing else*. **No export buttons, no batch queue here.** Every keeper
  arrives with a standard-correction baseline already applied; the user
  fine-tunes or leaves it as-is. (The export status / batch-queue UI that
  spec/59 placed inside Edit **moves out** to the Export surface.)
- **Export** is a deliberate "what ships" decision. Its pool is **all picked
  keepers** (each carrying its Edit baseline, touched or not). It reuses the
  app's one decision grammar — the **§5a green/red photo-state toggle** and the
  **locked P/X keymap** (spec/63): **green = export (the default — opt-out),
  red = drop**. Materialising the green set to JPEGs is the existing batch
  export engine (spec/60), now triggered from here instead of from Edit.

> **Superseded by [spec/72](72-third-party-roundtrips.md) §1 (2026-06-14):**
> third-party returns are no longer `Edited Media/` *candidates* promoted on
> green — they hardlink straight into `Exported Media/` on scan (Model B), show
> only on Export with a provenance badge, and are keep-or-delete like any export.
> Mira's own renders stay commit-on-export. The paragraph below is the original
> framing.

**External edits count as edited (LRC / Helicon, spec/57).** A keeper can be
developed *outside* Mira via the round trip — projected out, edited in
Lightroom Classic / Helicon / etc., then scanned back under `Edited Media` and
adopted. Such returns arrive **already finished**: the adopted file is itself a
rendered output. They enter Export already edited, default **green**, and
"exporting" one means **adopting / linking the external file**, not re-rendering
it through the spec/60 engine. The Export pool is still *all picked keepers* —
whether each keeper's output came from Mira's in-app develop or an external
return.

This keeps one decision grammar across the whole pipeline (Pick chooses
keepers green/red; Export chooses what ships green/red) and makes both
surfaces single-purpose.

### 1.2 On-disk: a dedicated `Exported Media/` (resolves spec/48 §5.2)

> **Implementation framing: see [`spec/89`](89-export-model-b.md).** spec/89
> §1.5 + Slice 1 amend the third-party-return path below: returns hardlink
> from `Edited Media/` into `Exported Media/` at **scan time** (Model B),
> not on Export. By the time a batch reaches the spec/60 engine every cell
> is a Mira-render target. The rest of this section's folder rules + ship-
> set invariant still hold.

Three byte tiers under the event root, two-tier output (Nelson 2026-06-14):

| Folder | Holds | Mutability |
|---|---|---|
| `Original Media/` (`_cameras`/`_phones`/`_other`) | the captured tree | **immutable** (SD-wipe gate only) |
| `Edited Media/` | third-party returns adopted via the round trip (spec/57) — the **edit candidates / inbox**. (Mira's own develop is non-destructive *params* in the DB, not files.) | additive |
| `Exported Media/` | **only the green-selected finals** — the ship set | additive |

Rules:

- **`Exported Media/` == `#exported` == the PTE hand-off folder.** The folder is
  exactly the green set, nothing more. This is the invariant Share/Cuts
  (spec/61) lean on.
- On Export, Mira **renders** green in-app develops into `Exported Media/`
  (spec/60 engine). Green **third-party returns are hardlinked** from
  `Edited Media/` into `Exported Media/` **at scan time** under Model B
  (spec/89 §1.5) — the older "hardlinked on Export" framing is retired.
- **Provenance** (Mira-rendered vs external) is **lineage metadata**, not a
  separate folder. The signal is the `lineage.provenance` enum
  (`'mira_render'` / `'third_party'`); spec/89 §1.4 specifies the badge
  wordmark inferred from the relpath.
- **`lineage.export_relpath` is repointed** from `Edited Media/…` (today's
  conflation) to `Exported Media/…`. Edited-Media returns keep their own
  relpath as edit candidates; only promotion to the ship set writes an
  `Exported Media/` artifact.
- Nothing is ever written into `Original Media/` — the immutable-capture
  invariant holds.

---

## 2. Why — and what was wrong before

The pre-revision events-list pipeline rendered four bars labelled
Collect / Pick / **Edit** / **Share**, but:

1. **The "Edit" bar wasn't editing.** `event_gateway.phase_day_progress()`
   builds per-phase counts from `phase_state`, then **overrides** the `edit`
   bucket with a count of `adjustment.edit_exported = 1` — i.e. the "Edit" bar
   was actually fed by **exported files**, not by editing.
2. **The "Share" bar was dead.** The same method emits **no `share` bucket**
   (comment: "callers that read pdp['share'] should expect KeyError now"), so
   the 4th bar resolved to 0% always.
3. **Edit looked identical to Pick.** The card reduces each bar to
   done/in-progress per day; when picked days and exported days coincide,
   "Edit" (really export) and "Pick" collapse to the same pattern → same number
   and (after the Surface 01 fidelity pass) same colour. This was the reported
   bug.
4. **Stray tuple.** `event_gateway.py` line 55 reads
   `_PHASES = ("pick", "pick", "edit", "share")` — a duplicated `"pick"` and a
   now-defunct `"share"`.

The good news: **no data is missing.** The store already distinguishes the
three signals this model needs —

| Phase | Source of truth (today) |
|---|---|
| Pick | `phase_state` rows with `phase='pick'` (decided / picked) |
| Edit | `adjustment` rows exist for the item = **developed** |
| Export | `adjustment.edit_exported = 1` = **exported** |

So the revision is mostly **relabelling + splitting** the collapsed Edit/Export
bar and **demoting** the dead Share bar — not new data plumbing.

---

## 3. Vocabulary delta (from spec/48 §1.1 / §7)

| Concept | Was (spec/48) | Now (spec/66) |
|---|---|---|
| Phase 4 (user label) | Share | **Export** |
| Phase 4 internal key | `'share'` | `'export'` |
| Phase 3 (`'edit'`) metric | exported-file count (mislabelled) | **developed** (has a develop adjustment) |
| "Share" | the 4th phase | a **state** of closed events (Cuts live here) |
| Decision verbs / keymap | Pick / Skip; P/X/Space/C… | **unchanged** (spec/63 keymap stays locked) |
| Internal state value `'picked'` | unchanged | unchanged |

Share keeps its place in the Cut model (spec/61) — only its *status* changes
from "phase" to "closed-event state." The `mira/ui/shared/` module, the
`#exported` pool, and the Cuts surfaces are unaffected in substance.

---

## 4. Surface impact (the cascade, for the implementation pass)

This spec is the design; code lands in a follow-up pass after Nelson's review.
Touch points:

- **`mira/gateway/event_gateway.py`**
  - `phase_day_progress()`: emit distinct `pick`, `edit`, `export` buckets.
    `edit` = days×items with an `adjustment` row (developed); `export` = the
    existing `edit_exported` count (rename the bucket from `edit` to `export`).
    Drop the dead `share` handling.
  - Fix the `_PHASES = ("pick", "pick", "edit", "share")` tuple (line 55) →
    the correct phase set.
- **`mira/ui/pages/_event_card_data.py`**
  - `_PHASES = ("pick", "edit", "share")` → `("pick", "edit", "export")`.
  - `_status_by_phase`: compute Edit% = developed ÷ picked and Export% =
    exported ÷ picked (the "among picked" denominators), per day.
- **`mira/ui/pages/_event_card_redesign.py`**
  - Open-card pipeline labels/order: Collect · Pick · Edit · **Export**.
  - (Closed cards already render the Share/Cuts summary — stat grid + carousel;
    no pipeline bars. That stays.)
- **Edit surface (`mira/ui/edited/…`, spec/59)**
  - **Remove the export buttons + batch-queue UI** — Edit keeps only
    classification / tone / crop. The standard-correction baseline stays
    applied to every keeper on entry. Edit no longer triggers export.
- **New Export surface (new module under `mira/ui/…`)**
  - A green/red decision grid over **all picked keepers** (default **green**;
    §5a state colours + the locked **P/X** keymap from spec/63). Hosts the
    batch-export trigger + status/queue **moved out of Edit**, and materialises
    the green set via the spec/60 engine. Needs its own page, a route, and a
    phase tile (Surface 03) + menu entry.
- **`mira/ui/pages/phases_page.py`** (Surface 03)
  - The 2×2 phase donuts become Collect / Pick / Edit / Export. Share is no
    longer a phase tile.
- **Menu bar (`mira/ui/shell/main_window.py`)**
  - Add a top-level **Export** menu ("Open Export phase") alongside
    Collect / Pick / Edit. Keep the **Share** menu (New Cut, etc.) but gate it
    to **closed events only** — Share is a closed-event state, not a phase you
    open on an active event. (Drop the "Open Share phase" wording; Share is
    reached by opening a closed event.)
- **On-disk / data (`core/path_builder.py`, spec/60 engine, lineage)**
  - Add the `Exported Media/` tier; the batch engine renders the green set
    there. Repoint `lineage.export_relpath` from `Edited Media/…` to
    `Exported Media/…`; hardlink green third-party returns from `Edited Media/`.
  - `exported_item_ids()` / `exported_files()` (today: lineage `WHERE
    phase='edit'`) need to distinguish the **exported** (shipped) set from mere
    edit candidates — likely a new `phase='export'` lineage/marker or an
    `Exported Media/` relpath test.
- **Vocabulary sweep** — `'share'` as a *phase key* retires; `'share'` as a
  *state word* (closed-event Cuts) stays. Audit strings/tests for the phase-key
  sense only.
- **Cross-refs** — spec/48 gets a "Share superseded by spec/66" pointer;
  spec/61 (Cuts) gains a note that it is the Share *state*; `CLAUDE.md` four-
  phase table + Cut section updated.

---

## 5. What does NOT change

- The locked keyboard map (spec/63) — untouched.
- Decision verbs Pick / Skip and the `'picked'` state value.
- The Cut model itself (spec/61): pool algebra, `#exported`, Picker-on-Cut,
  flat-grid Play/Export. Only its framing as "the Share *state*" is clarified.
- Collect and Pick phase *behaviour*. (Edit *does* change: the export step
  leaves it — see §1.1 — but classification / tone / crop are untouched.)
- The closed-event card body (carousel + stat grid) — already the Share view.

---

## 6. Open questions for Nelson

**Resolved 2026-06-14:**
- *Export phase entry* — Export **is its own surface** (§1.1), not the batch
  action inside Edit. Edit is de-cluttered; the batch trigger/queue moves to
  Export.
- *Export pool & gate* — pool is **all picked keepers**; the **green/red**
  toggle (default green) is the survival decision.
- *On-disk layout* — dedicated **`Exported Media/`** = the green ship set =
  `#exported` = PTE hand-off; `Edited Media/` stays the third-party returns
  inbox (§1.2). Resolves spec/48 §5.2.

- *Menu model* — add a top-level **Export** menu (Open Export phase) beside
  Collect/Pick/Edit; keep the **Share** menu but **enabled only on closed
  events** (Share is a state, not an openable phase). §4 updated.

**Still open (minor, settle during implementation):**
1. **Collect metric.** Keep "day has captures = done", or make Collect% reflect
   planned-vs-ingested days?
2. **Edit progress signal.** "Reviewed ÷ picked" needs a concrete "cleared in
   Edit" signal (visited? an explicit advance?). Settle during the Edit
   surface pass.

---

## 7. Related

- [spec/48 — The 4-phase pivot](48-four-phase-pivot.md) — phase **count** +
  Collect/Pick vocabulary still govern; its **Share** framing is superseded
  here.
- [spec/61 — Share event Cuts](61-share-event-cuts.md) — the Cut model; this
  spec reframes it as the **Share state** of closed events.
- [spec/59 — Edit surface](59-edit-surface.md) / [spec/60 — Batch export
  engine](60-batch-export-engine.md) — relevant to §6 Q2 (is Export a phase
  surface or the batch action inside Edit).
- [spec/57 — Folders and round trip](57-folders-and-roundtrip.md) — the
  `Original Media/` + `Edited Media/` model + external round trip; this spec
  adds the `Exported Media/` tier (§1.2) and resolves spec/48 §5.2.
- [spec/63 — Photo viewport](63-photo-viewport.md) — locked keymap, unchanged.
