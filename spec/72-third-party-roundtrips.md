# spec/72 — Third-party round trips: external edits (Model B) and stack consolidation

**Decided 2026-06-14 (Nelson + Claude), design-discussion session. Implementation
NOT yet scheduled.** Supersedes [spec/66](66-collect-pick-edit-export.md) §1.1–§1.2
on the *third-party-return* framing; refines [spec/57](57-folders-and-roundtrip.md)
§2.3 / §3 on the stacker trip.

## 0. Two round trips, deliberately different

There are **two** external round trips and they must not be conflated:

| | External editor (LRC / Helicon-class) | Stack consolidation (focus/exposure) |
|---|---|---|
| What it produces | a **finished edited version** of one photo | a **new master** from a bracket of frames |
| Layer | export / ship set | a captured-like Item that re-enters the pipeline |
| Goes through the Editor? | **never** (it's already finished) | yes — flows Pick → Edit → Export like a capture |
| On disk | shipped bytes in `Exported Media/` | adopted into `Original Media/Merged/` |

---

## 1. External editor returns — Model B (pre-committed, keep-or-delete)

The Editor (`Edited Media/` the **folder**) is just where LRC physically writes;
it is **not** Mira's creative Edit **surface**. A finished LRC/Helicon photo is a
done output — it **never appears in the creative Editor**.

**Model B (chosen over spec/66's candidate model):**

- On the return scan, the third-party file is **hardlinked straight into
  `Exported Media/`** — it enters the **ship set immediately** (hardlink is
  ~zero-cost; the bytes already exist). It is **not** held as an `Edited Media/`
  "candidate."
- It shows **only on the Export surface**, already-shipped, carrying a distinct
  **provenance badge** (§3) so the user knows it came from a third party.
- The decision is **keep or delete**, identical to any other exported file:
  deleting removes the `Exported Media/` hardlink **+ its lineage row** — and
  **never** touches the source under `Edited Media/` (the user's LRC work, the
  additive inbox) or `Original Media/`. (Un-exporting also drops it from any Cut,
  spec/61 §1.4.)
- "Choices happen only at Export" still holds — the choice is the opt-out
  (delete), at Export.

**Mira's own renders are unchanged:** still commit-on-export (green at the Export
pass), because rendering is expensive and the bytes don't exist until asked for.
Model B applies to third-party returns only — they're free to materialise eagerly.

**Why B:** one uniform "an export is an export" concept (provenance is just a
badge); uniform delete; and it removes the `Edited Media` vs `Exported Media`
*candidate* split — the exact complexity that caused the 2026-06-14 export-
recognition bug.

**Implementation note:** provenance (Mira-rendered vs third-party) must be an
**unambiguous lineage signal** — a dedicated flag, not inferred from
`recipe_json`-null (the hardlink path may stamp a Mira recipe onto a return,
muddying that inference).

This supersedes spec/66 §1.1–§1.2's "`Edited Media/` candidate → hardlink to
`Exported Media/` on green" framing for third-party returns.

---

## 2. Stack consolidation round trip (bracket → master)

Stacking never happens in Mira (spec/57 §2.3) — always external. The trip:

1. In the Picker, the user picks a subset of a bracket's frames; on Edit entry
   Mira builds `Picked Media/<bracket>/` holding links to **only the picked
   members**.
2. The user runs the external stacker (Helicon etc., possibly different
   algorithms) on that subdir and saves the merged result to the **root of
   `Picked Media/`**.
3. The return scan finds a **new real file at the picked root** → a stacker
   output (location is the discriminator; a file under `Edited Media/` is an
   editor return instead).

### 2.1 The confirm step ("Review merged results")

Fires **only** on the scan when new real files exist at the picked root — no
files, no dialog. One **batched** dialog, one row per new file:

- thumbnail + filename of the merged file;
- a **"belongs to" dropdown** of this event's **unmerged** brackets, pre-set to
  Mira's best guess, showing the candidate bracket's cover + a label ("Burst · 5
  frames · 14:32") for a visual check;
- an **"Ignore / not a merge"** option.

**Best-guess ranking:** (1) filename prefix matches a bracket member's
deterministic prefix (`D03_G9M2_…`); else (2) EXIF capture-time falls inside a
bracket's span; else (3) "unassigned" — the user picks. Common case = a one-click
**Confirm**; the dropdown is the safety net. Anything left unassigned goes to the
**unmatched-returns report** — never silently mis-filed (spec/57 §3.2 rule).

### 2.2 On confirm-adopt

- Create the master `Item` (`provenance='stack_output'`) in
  `Original Media/Merged/`.
- Set `StackBracket.output_item_id` + `StackBracket.action='stacked'`.
- **Auto-Skip the member frames** (ordinary `phase_state`) — reversible; the
  frames stay byte-pristine in `Original Media/`, re-pickable if a single frame
  is ever wanted.

### 2.3 Collapse to the master

Downstream (Pick / Edit / Export) the bracket **renders as its merged master** —
only the consolidated photo is seen as individual media; the original frames are
the (skipped) archive, reachable but not browsed by default. A run that produces
**multiple** merges from one bracket → they become **versions** of that bracket's
output.

---

## 3. Badges (two layers, never re-mixed)

- **Exported icon (corner badge), one mark everywhere** — retire the legacy
  diagonal watermark, including the leftover in the single-photo viewport
  (`photo_viewport.ExportedWatermark`); use the redesign's corner icon on every
  surface. The icon means "this shipped."
- **Export-provenance variant** on that icon: **M** (Mira-rendered) · **ext**
  (third-party editor) · **M+ext** (both versions exist) — answers "from which
  edit." (A single keeper can hold a Mira export *and* an LRC export at once,
  spec/54 §8 / spec/61 §1.2.)
- **Consolidation badge — item-level, separate** — driven by
  `Item.provenance=='stack_output'`; means "this master is a stack merge." It is
  a *different layer* from the export-provenance variant and can **coexist** with
  it (a merged master later exported via LRC carries both). Keep them distinct
  glyphs.

---

## 4. No schema change required

The model already carries every hook (spec/57 §2.3's "existing receiving end"):

- `Item.provenance == 'stack_output'` → the consolidation badge + the master's
  origin.
- `StackBracket.output_item_id` + `StackBracket.action='stacked'` → the
  bracket→master link and the bracket's disposition (drives the collapse).
- `phase_state` → the auto-Skip of member frames.

The one *new* thing worth adding is **not** a new attribute but making
export-provenance an explicit lineage flag (§1), so the M/ext badge and the
provenance-aware delete are reliable.

---

## 5. Related / supersedes

- [spec/66 §1.1–§1.2](66-collect-pick-edit-export.md) — third-party-return
  framing **superseded** by §1 (Model B). Mira's own-render commit-on-export
  unchanged.
- [spec/57 §2.3 / §3](57-folders-and-roundtrip.md) — the stacker trip, **refined**
  by §2 (the confirm step + collapse + auto-Skip).
- [spec/68 §3](68-phase-redesign-coordination.md) — the Export surface (un-export
  / delete) this builds on.
- [spec/61 §1.2 / §1.4](61-share-event-cuts.md) — versions-as-files; delete drops
  from Cuts.
- [spec/54 §8](54-user-data-store.md) — Mira-edit and LRC-edit coexist as versions.
