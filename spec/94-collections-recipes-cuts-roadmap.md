# spec/94 — Collections · Recipes · Cuts: implementation roadmap

**Status:** plan **agreed** with Nelson 2026-06-21. The sequencing for building
the Collections / Recipes / Cuts feature out of the design specs. **Phase 1 is
un-gated and assigned**; Phases 2–5 are the durable plan, not yet started.

> **Vocabulary:** the nouns are **Collection · Recipe · Cut** — never "Dynamic
> Collection" or "DC" in UI or new code. The older specs say "Dynamic
> Collection / DC"; read them as "Collection." The existing `DynamicCollection`
> model / `dynamic_collection` table keep their internal names (no schema
> rename).

Design specs this sequences: [`spec/76`](76-home-library-and-cut-publishing.md)
(library root + publish), [`spec/81`](81-dynamic-collection-and-cut.md) (the
engine), [`spec/90`](90-cut-recipes-and-collections.md) (Recipe + dialog),
[`spec/93`](93-recipe-collection-storage-and-placement.md) (storage & placement),
[`spec/32`](32-dynamic-collections.md) (filter dimensions),
[`spec/61`](61-share-event-cuts.md) (the event-Cut surfaces).

**Ground rules for every phase:** target branch `main` (trunk; XMC == main).
Each phase ends green on `verify.bat` with new tests, and leaves the app fully
usable — no phase ships a half-wired surface. Charter invariants are binding
(offline-first, no network, atomic write-then-rename, no hardcoded paths,
one-way `ui → gateway/core` deps, `tr()` for strings, no inline QSS).

---

## Phase 1 — Foundations: library root + define / store / browse / save
*(un-gated; assigned)*

- **Library-root relocation** (spec/76 §B.4): user-defined root, hidden `.mira/`,
  bootstrap pointer, Create / Open first-run doors, one-shot migration, paths
  relative to the root, reinstall recovery.
- **Collections / Recipes as JSON files** (spec/93 §4): name-as-identity,
  per-kind global uniqueness, rename-updates-referrers, atomic writes under the
  lock, cached tree-scan.
- **Auto-placement classifier** (spec/93 §5) + file ↔ `event.db` migration.
- **Cascading folder menus** mirroring the tree (any depth).
- **Compose / save dialog** (spec/90 five-section rule-list editor), speaking the
  ingredient / recipe / dish metaphor.
- **Binding badge** (Global vs Event X) + migration note.
- **Reuses** the legacy pin → session → play / export back half.

**Exit:** author, save, organise, and browse Collections and Recipes; placement
is automatic and correct.

## Phase 2 — Resolve + pin (make definitions real, event-scope)  *(M–L)*

- Complete the live **set-algebra resolver** (spec/81 §2) over operands + the
  filters available today.
- The **pin** verb: a Collection / Recipe → a frozen Cut (`expr_snapshot_json`,
  source link + kind), **event-scope first**. Replace the legacy pin path.

**Exit:** define → resolve live → pin into a real event Cut end-to-end, with the
existing session / play / export still doing the back half.

## Phase 3 — The Cut construction session (replace the legacy back half)  *(L)*

- The proper **Picker-session-on-a-Cut** (spec/61 + spec/90 Rules / Otherwise):
  the rule list seeds initial pick / skip verdicts, the user hand-refines, with
  the flat grid, day separators, the time budget (target / max seconds), audio.
- Finish the Cut-detail and Cut-session surfaces; retire the legacy widgets
  Phase 1 reused.

**Exit:** a Recipe produces a hand-finishable Cut you can play (rehearsal) and
export per event.

## Phase 4 — Cross-event: scope, resolution, Cuts, Home/Library surface  *(L, multi-session)*

- The **cross-event power face** (spec/90 Scope = events / event-collections /
  date ranges; the full spec/32 §2 filter catalogue) + cross-event resolution.
- **Cross-event Cuts** (`cut_member.event_id` across events; bytes stay per
  event).
- The **Home / Library surface** (spec/76, spec/93 §9) that lists, plays, and
  exports cross-event Cuts.
- **Depends on the indexing track** (below) for the richer filters.

**Exit:** "best wildlife across every trip, 2010–2025" resolves, pins, plays,
exports.

## Phase 5 — Publishing + multi-device (spec/76 §A / §B)  *(M)*

- Harden the **single-writer lock** + **read-only library mode** (§B.1) for the
  NAS / multi-PC model.
- **Cut publish target + manifest** (§B.3) for the home-media-server / TV
  handoff; NAS validation (§B.2).

**Exit:** the library lives on a NAS, one writer; Cuts publish as files a TV
media server streams.

---

## Cross-cutting track — Metadata indexing & filters
*(spec/32 §2, [`spec/86`](86-event-data-filters.md), [`spec/91`](91-face-recognition.md))*

The EXIF / metadata index that makes the full filter catalogue (camera, lens,
focal length, aperture / shutter / ISO, dates, location) queryable cross-event —
and later **face recognition** (spec/91) as another dimension feeding the same
filter layer. **Gates Phase 4's richer filters**; can run in parallel from after
Phase 1. Treat as its own track, not a UI phase.

## Sequencing

1 → 2 → 3 deliver the **event-scope** feature fully (the common case). **4**
unlocks cross-event and needs the indexing track landed first. **5** is the
home / NAS endgame, schedulable any time after 1 (mostly the lock + publish
convention). So: **five phases + one parallel indexing track.**
