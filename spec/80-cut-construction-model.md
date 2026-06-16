# spec/80 — Cut construction model & New Cut dialog (foundation for B2)

> **REVISED 2026-06-16 by [spec/81](81-dynamic-collection-and-cut.md).** The
> "live Cut vs pinned Cut" split this doc introduced is **superseded**: the live
> formula is now its own noun, the **Dynamic Collection (DC)**, and a **Cut is
> always frozen**. spec/81 governs. The sections below have been reconciled to
> that model; "pool" reads as "DC," "live Cut" reads as "DC," and the
> live/pinned Build-mode badge is gone. This doc stays as the construction-
> session record + the New Cut dialog implementation notes. **Read spec/81
> first.**

**Status:** design **agreed** with Nelson 2026-06-16 (design-mode session),
**reconciled to spec/81 same day**. Clarifies and extends the **locked** Cut
model in `spec/61`. **Implementation gated:** the real test is assembling real
Cuts (`#long`, `#medium`, `all-time-best`, …) from the production events after
the 30-year import — validate against that before building, and expect to
refine. This doc is also the **foundation for B2** (cross-event DCs+Cuts /
spec/61 §8): cross-event is the *same* model applied library-wide.

Read with: `spec/81` (the governing two-noun/two-verb model), `spec/61`
(event-Cut surfaces, set algebra, live `#exported`), `spec/32` (Dynamic
Collections — saved cross-library queries), and the current
`mira/ui/pages/new_cut_dialog.py`.

---

## 1. The construction model (one rule)

> **A Dynamic Collection = a formula (set algebra over operands + filters). A
> Cut = a DC, pinned — optionally trimmed by a budget pass.** (spec/81 §1–§4.)

### 1.1 The DC formula — set algebra over operands
The DC is **a set expression** that resolves live, and it can become the whole
Cut on its own. Operations:

- **union (`+`)**, **difference (`−`)**, **intersection (`∩`)** — all three
  available to the user from the start. Evaluated **strictly left-to-right**;
  **grouping is done by nesting a DC as an operand** (a sub-DC stands in for
  parentheses), so no precedence rules or bracket UI are needed. No
  symmetric-difference / complement operator — complement is just `#exported − X`
  (spec/81 §2).

**Operands** are either:
- a **base universe** — per-event: `#exported`; cross-event (B2): any rung of the
  ladder `#collected / #picked / #edited / #exported`; and
- **any existing DC or Cut** — a first-class operand. (`all-time-best =
  all-time-best-macro + all-time-best-wildlife`.) A single term alone is a valid
  one-term DC — *"use `#long` as the source."*

### 1.2 Pin — the optional pick/skip pass on top
After the DC resolves, the user may run a budget-driven **pin** (pick/skip
session — trim rejects, or pick keepers) to produce the Cut. The pin's trimming
is **optional**; pinning itself always happens (it is what freezes a Cut).

### 1.3 DC (live) vs Cut (frozen) — the consequence
- **A DC is LIVE.** Its membership re-evaluates from its operands; if an operand
  changes, the DC's resolution changes. (`all-time-best = macro + wildlife` grows
  when `macro` does.) A DC is only a definition — not playable, not exportable.
- **A Cut is FROZEN.** Pinning a DC snapshots its members; the Cut never
  re-queries its DC live. A Cut is the only thing you can play or export.
- **No trimming during pin → the Cut is the DC's resolution captured one-to-one**
  at pin time (still frozen, just not narrowed).

### 1.4 Worked examples (Nelson's, the acceptance set)
- `all-time-best = all-time-best-macro + all-time-best-wildlife` — pure union →
  a **live DC**; pin it (no trim) to get a playable Cut.
- `#medium` from `#long` — DC = `#long`; pin with a budget pass skipping slides
  until the duration fits → **frozen Cut**, a subset (`#medium ⊆ #long`).
- `event-cut = #exported − rejects` — DC algebra + a pin trim pass → **frozen
  Cut**.

### 1.5 Structure & the parked wrinkle
DCs form a **derivation graph** (`#medium`'s DC hangs off `#long`;
`all-time-best` off `macro`+`wildlife`). DC→DC composition stays live; a Cut's
members are frozen at pin. **Parked:** when a *source* DC changes (e.g. a shot
added to `#long`'s DC later), an already-pinned Cut can't silently absorb it. v1
leaves pinned Cuts **frozen**; a later **"re-base"** action ("re-pin against the
updated DC") is an explicit, optional feature — not v1. (spec/81 §5.)

---

## 2. How the New Cut dialog captures this

> **Implementation note — reconciled to spec/81 (2026-06-16).** The dialog as
> first built carried a 3-way **Build mode** (keep_all / weed_out / pick_in) +
> a live/pinned consequence hint in `mira/ui/pages/new_cut_dialog.py`
> (`_build_mode_group`, `_on_mode_changed`); `cut_info()` emitted
> `build_mode` + `live`. **Under spec/81 this collapses:** there is no
> live *Cut* — "keep all → live" is now "make/choose a DC and pin it without
> trimming." The dialog's job is (1) compose/choose a **DC**, then (2) an
> optional **pin** (the budget pick/skip pass: weed-out = start all-in,
> pick-in = start all-out). The live/pinned **badge is removed** (a Cut is
> always frozen; the DC is the live thing, and DCs are managed/shown
> separately). **Backend to build (gated):** the **DC entity + live-resolution
> engine** (spec/81 §2; see agent task A/B) and the **pin** that snapshots a
> DC's resolution into `cut_member`. Implement against the real-event
> acceptance test (§4).

The reconciled dialog flow:

- **Source section = the DC.** The chips + `+`/`−` steppers compose the DC's
  set algebra. The operand menu lists **`#exported` *and every existing DC/Cut`***
  (per-event); for B2 it also lists the ladder universes and DCs/Cuts across the
  selected events. A live **"source: N files"** readout updates as terms change.
  (Using one DC/Cut as the source = add it as the single term — no special
  control.) The composed DC can be **named and saved** for reuse as an operand
  (spec/81 §2).
- **Pin (replaces "Build mode") — the one optional trim choice.** Two modes,
  *both produce a frozen Cut*:
  1. **Keep all** → pin the DC's resolution one-to-one; no pick session.
     (`all-time-best`.)
  2. **Weed out** → start all-in, skip the rejects in a budget session.
     (`#medium` from `#long`.)
  3. **Pick in** → start all-out, pick the keepers.

  There is no fourth "live Cut" option — a live result is simply a saved DC you
  haven't pinned yet.
- **Duration drives the pin.** The Target / Max budget (already in the dialog)
  guides the weed-out / pick-in session: "skip until it fits" — exactly the
  `#long → #medium` flow.

Everything else in the dialog (Name + tag, Style/Media filters, Timing & Music,
Slide cards, Load/Save template, live match count) stays per the current build +
spec/61 §10.

---

## 3. B2 (cross-event) sits directly on this

Cross-event Cuts (spec/61 §8) are the **same construction model, library-wide**:
- Operands' base universes expand to `#collected / #picked / #edited / #exported`
  **across selected events**; existing Cuts across events are operands too.
- A pure-composition cross-event source is a **Dynamic Collection** (spec/32):
  a user-level saved query, computed membership, **pinned** into a Cut for the
  PTE hand-off (spec/81 — same two-noun model, library-wide).
- Entry point = the **cross-event band** on the events screen (already built,
  spec/75 §2).
- Still-open B2 specifics (own session): the **storage** of a cross-event DC/Cut
  (user-level, not in one `event.db` — likely the spec/32 saved-query entity, not
  the per-event `cut` table), the **media/EXIF filter index** that makes
  rule-based selection possible, and the **filter surface**. The construction
  *logic* here is settled; those are the remaining cross-event pieces.

---

## 4. Open / to validate (the gate)
- **Default pin mode** — does pinning a freshly-composed DC default to **Keep
  all** (no trim) or to a weed-out/pick-in pass? Settle while making real Cuts.
- **Re-base** for pinned Cuts when a source DC changes — parked (§1.5; spec/81 §5).
- **The acceptance test:** build `#long`, `#medium`, and an `all-time-best`-style
  composed DC from real production events; pin each to a Cut; confirm the dialog
  + the DC→pin→Cut behaviour feel right. Refine this spec from that, then
  implement.
