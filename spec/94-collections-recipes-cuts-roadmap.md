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
*(**complete** 2026-06-21, 12 commits on `main`, `verify.bat` green —
substrate + host-wiring + cleanups all landed)*

- **Library-root relocation** (spec/76 §B.4): user-defined root, hidden `.mira/`,
  bootstrap pointer, Create / Open first-run doors, one-shot migration, paths
  relative to the root, reinstall recovery.
- **Collections / Recipes as JSON files** (spec/93 §4): a stable internal **`id`**
  is the identity (the filename is just the display name), references are
  `{id, name}` resolved by id with a name fallback, so **move and rename in the
  file manager are both safe** (the app adopts an OS-rename's new name on scan;
  delete is the only unrecoverable act). Soft display-name uniqueness, atomic
  writes under the lock, cached tree-scan.
- **Auto-placement classifier** (spec/93 §5) + file ↔ `event.db` migration.
- **Cascading folder menus** mirroring the tree (any depth).
- **Compose / save dialog** (spec/90 five-section rule-list editor), speaking the
  ingredient / recipe / dish metaphor.
- **Binding badge** (Global vs Event X) + migration note.
- **Reuses** the legacy pin → session → play / export back half.

**Exit:** author, save, organise, and browse Collections and Recipes; placement
is automatic and correct.

**Landed (1a):** library-root resolution, first-run wizard, lock relocated to
`<root>/.mira/writer.lock`, the binding-badge + migration-note + Collection
vocabulary in `NewRecipeDialog`. `DefinitionLibrary`, the placement classifier +
atomic file↔`event.db` switch, the cascading-menu widget, and the definitions
gateway facade as substrate.

**Landed (1b):** slug-collision disambiguation (case-folded — defends against
NTFS / APFS-default) + reconcile-on-scan in `DefinitionLibrary`; one-shot
`mira.db.saved_filter` + `mira.db.recipe` → JSON-file migration
(`core/dual_home_migrate.py`, idempotent + marker-gated); `Gateway` wires
`collections_library` + `recipes_library` + the facades + the migration on
first access; `LibraryGateway` + `RecipeStore` route through the JSON tree as
the single live source (legacy SQL paths fall back only on unit-test direct
constructions); both `NewRecipeDialog` launch sites (Cut + Collection) pass
`classify_placement` + `event_name_for_id`, and Load Recipe mounts the
`CascadingTreeMenu` against the gateway facade — `tree_for_event` returns
GLOBAL ∪ BOUND-to-E per spec/93 §6.

## Phase 2 — Resolve + pin (make definitions real, event-scope)
*(**complete** 2026-06-21, 5 commits on `main`, `verify.bat` green)*

- Complete the live **set-algebra resolver** (spec/81 §2) over operands + the
  filters available today.
- The **pin** verb: a Collection / Recipe → a frozen Cut (`expr_snapshot_json`,
  source link + kind), **event-scope first**. Replace the legacy pin path.

**Exit:** define → resolve live → pin into a real event Cut end-to-end, with the
existing session / play / export still doing the back half.

**Landed:** `EventGateway` accepts a `collections_library_factory` (built by
`Gateway.open_event` from the file-based Collection library). The factory is
invoked LAZILY on first operand lookup that misses event.db; the snapshot is
cached on the EventGateway for the lifetime of one `open_event()`. The
resolver's `_operand_dc`, the Recipe strict-walk, the recursive
`_recipe_dc_expr_by_ref`, and `dc_probe` all fall through to the library;
`dc_operand_inventory` returns the spec/93 §6 load set (base + bound DCs +
global Collections + Cuts) with bound winning on id collisions. The pin
path threads the same fallback through `CutSession._draft_expr_filters` and
`create_cut` auto-infers `source_dc_kind`: id in event.db → `'event'`; id in
the library → `'user'` (the value's semantic shifted in Phase 1b — no DDL
change). Freeze invariant holds across edit/rename/delete of the source
file; the Cut's `expr_snapshot_json` + members are the authoritative record.

## Phase 3 — The Cut construction session (replace the legacy back half)
*(**complete** 2026-06-21, 3 commits on `main`, `verify.bat` green)*

- The proper **Picker-session-on-a-Cut** (spec/61 + spec/90 Rules / Otherwise):
  the rule list seeds initial pick / skip verdicts, the user hand-refines, with
  the flat grid, day separators, the time budget (target / max seconds), audio.
- Finish the Cut-detail and Cut-session surfaces; retire the legacy widgets
  Phase 1 reused.

**Exit:** a Recipe produces a hand-finishable Cut you can play (rehearsal) and
export per event.

**Landed:** `CutDraft.seed` carries `(export_relpath, picked)` pairs computed
by `recipe_resolver` at Start time; `CutSession.__post_init__` overlays them
on top of the `pin_mode` default so a rule-based Recipe opens the picker
pre-curated. `CutSession.from_draft` derives a seed for rule-based drafts
that arrive without one (defensive backstop). `cut_draft_to_recipe_composition`
drops the seed — it's a runtime artefact, not part of the saved Recipe.
`CutSessionPage` rebuilt to the redesign standard — flush
`#SurfaceHeaderRail[phase="share"]`, content in two `#SurfaceBand` boxes,
the standard 28/18/28/22 margins; the page-level Back retired in favour of
the shared title bar (`uses_titlebar_back = True`, `back_requested` signal,
three-level `on_titlebar_back` dispatcher: single → grid → days → leave).
`CutDetailPage` finished the SurfaceBand wrap + the dispatcher contract on
the same shape. Play (`CutPlayerDialog`) + Export (`cut_export.py`) reused
intact per the brief. 27 new tests; 17 quarantined-or-rebuilt existing
suites still green; no inline QSS introduced; render smoke in both themes.

## Phase 4 — Cross-event: scope, resolution, Cuts, Home/Library surface

### Phase 4a — today's filters only *(complete 2026-06-21, 3 commits on `main`, `verify.bat` green)*

The cross-event surface lands end-to-end using only the filters
available today (Style / Media + Stars / Color label / Flag + the
spec/86 event-level qualifiers + Capture date + Country / City).
The richer filters (camera, lens, aperture / shutter / ISO, faces)
stay gated on the **indexing track** below.

- The **cross-event power face** (spec/90 Scope = events /
  event-collections / date ranges) + cross-event resolution. UI
  catalogue limited to Curatorial + Event + When/Where for now.
- **Cross-event Cuts** in `mira.db` (spec/93 §3): one row per Cut +
  `cut_member.event_id` per row; bytes stay per source event; no FK
  spans stores.
- The **Library page** (spec/76 §B.4, spec/93 §9): top-level
  destination, three SurfaceBand sections (cross-event Cuts list with
  per-row Play / Export / Open / Delete; Collections entry; Recipes
  entry). Retires the events-page cross-event band.
- Cross-event **Play** + **Export** gather members from each source
  event's `Exported Media/`.

**Exit met:** a Collection composed over selected events with date /
style filters → pins to a cross-event Cut → appears in the Library
page → plays + exports across events.

**Landed:**

- **(i)** [`36edbc6`](https://github.com/nksalgado-proton/Mira/commit/36edbc6)
  — scope wiring (`LibraryGateway.resolve_scope` walks chip operands;
  `resolve_dc_keys / dc_probe` accept `scope=`; `CrossEventCutSession.from_draft`
  threads `scope_event_uuids`); filter gating
  (`build_cross_event_phase4a_catalogue` hides Camera & lens + Settings
  groups behind `INDEXING_GATED_DIM_IDS`; `NewRecipeDialog` Collection
  face flipped to `show_hardware=False`); vocabulary sweep ("Collection"
  user-facing across cross_event_* + share_cuts_page + dc_detail_page;
  internal table names untouched per spec/93 §4); `test_collection_vocabulary.py`
  smoke gates regressions.
- **(ii)** [`affe261`](https://github.com/nksalgado-proton/Mira/commit/affe261)
  — schema v8 (cross-event `cut` + `cut_member` in `mira.db`,
  `event_id NOT NULL`, no FK across stores); full CRUD on `LibraryGateway`;
  `CrossEventCutSession.commit(library_gateway)` flips storage target;
  export pipeline + UI dialogs + sweeps re-pointed at `mira.db`;
  `core/cross_event_cut_migrate.py` one-shot with the **membership-shape
  discriminator** (Nelson 2026-06-21 — a Cut migrates iff at least one
  member has a non-NULL `event_id`; `source_dc_kind='user'` alone is
  insufficient) and **copy-verify-delete safety** (mira.db insert +
  verify inside one transaction; event.db DELETE only after the
  COMMIT + verify pass; a crash leaves both stores intact + marker
  absent). Tests: discriminator behaviour, copy-verify-delete ordering
  on a forced verify failure, idempotency + partial-recovery.
- **(iii)** [`86a3220`](https://github.com/nksalgado-proton/Mira/commit/86a3220)
  — `mira/ui/pages/library_page.py` (flush `#SurfaceHeaderRail[phase="share"]`
  + three `#SurfaceBand` sections; `uses_titlebar_back` + `on_titlebar_back`
  per Phase 3 contract; defensive failure handling); cross-event Play
  (`mira/shared/cross_event_cut_play.py` walks members + projection,
  builds entries chronologically across events; `CutPlayerDialog` gains
  `resolve_path=` callable for per-member root resolution; event-scope
  Play unchanged); MainWindow wiring + App-menu "Cross-event Cuts…"
  entry; the events-page `CrossEventCutsBand` retires entirely.

### Phase 4b — richer filters *(unlocked when the indexing track lands)*

The dialog's hardware / EXIF / face groups light up by flipping
`NewCrossEventDcDialog` + `NewRecipeDialog`'s Collection face back
to the full `build_cross_event_catalogue` / `show_hardware=True`. No
new dialog work; just the gate.

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
