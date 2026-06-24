# 118 — "Edited since export" badge + overwrite-vs-keep-both choice at export (LRC parity)

**Status: PROPOSED (Nelson 2026-06-23). The model for re-shipping an item
that was already exported (and may be in a Cut) after you re-edit it. Two
locked decisions: (1) Export stays **deliberate** — editing never
auto-exports; instead the Export surface shows the item with a **very
distinctive "edited since export" badge** so the user sees, at a glance,
that the on-disk export no longer matches the current edit. (2) At export
time, when a Mira render already exists for that item, Mira **asks
overwrite vs. keep-both** — exactly Lightroom Classic's behaviour. Overwrite
= replace in place (same path / same `lineage` row), so a Cut referencing
it just sees the fresh pixels, untouched. Keep-both = a new `(2)` version
alongside (today's `UNIQUE` default). Both naming policies already exist
(`core.cull_export.CollisionPolicy.OVERRIDE` / `.UNIQUE`); the staleness
predicate already exists (live `recipe_for_item` vs the render's
`lineage.recipe_json`, used today only by the preview dialog's
"Adjustments changed" chip). This spec lifts staleness onto the **grid
cell** and turns the export-time re-render dialog into the
overwrite/keep-both choice. Touches `mira/ui/pages/days_grid_page.py`,
`mira/ui/exported/batch.py` + the export run dialogs, and the QSS for the
new badge. No data-model change.**

## 1. What the system does NOT do (locked)

Editing an exported file does **not** trigger an export. Export is a
deliberate phase (spec/66, spec/89 §5) — the `↑ Export now` batch or the
single-item `Export this`. Editing writes a non-default `adjustment` row (a
render *intent*); the existing exported JPEG is now **stale** but stays on
disk until the user runs an export. "The user knows what he's doing" — we
don't block or auto-act; we make the state **loud** and give the choice at
ship time.

## 2. The "edited since export" badge (Export grid)

- **Predicate** — an item is *edited-since-export* when it has an on-disk
  Mira render whose `lineage.recipe_json` no longer matches the item's live
  resolved recipe (`recipe_for_item`). This is the exact diff
  `DaysGridPage._is_preview_item_stale` already computes for the preview
  dialog's "Adjustments changed — Export to refresh" chip — promote it to a
  per-cell signal. Third-party returns have no recipe → never stale.
- **Visual** — a **distinctive badge / cue on the grid cell** (not the soft
  provenance wordmark; a loud, unmistakable mark — e.g. an amber "edited"
  tag or a corner flag), separate from the ship-intent border and the
  provenance badge. It reads: *this cell's export is out of date with your
  current edit.* Mirror it on the versions-cluster cover when any member is
  stale.
- **Edit-view consistency** — the Edit-phase exported badge should respect
  the same truth: once an exported item is re-edited, its Edit-view
  "exported" badge reflects "edited since export" (cleared / changed), not
  a clean "exported" state. (Keeps the two surfaces honest with each
  other.)

## 3. Overwrite vs. keep-both at export (the LRC ask)

Replace spec/89 §5.2 D6.C's yes/no re-render-ask with a **three-way**
choice whenever an export run would re-render an item that **already has a
Mira render on disk**:

- **Overwrite** → `CollisionPolicy.OVERRIDE`: atomic replace at the **same
  `export_relpath`**, reusing the existing `lineage` row (refresh
  `recipe_json` + `exported_at`). The file's identity is unchanged, so any
  Cut whose `#exported` query includes it (spec/61) just sees new pixels —
  no new version, no membership change, no re-pick. This is the path that
  satisfies "the Cut doesn't need to be touched."
- **Keep both** → `CollisionPolicy.UNIQUE`: write `stem (2).jpg` with its
  **own** `lineage` row (today's default; `_lineage.py` "renamed" bucket).
  Both versions live in `Exported Media/`; the item becomes a versions
  cluster (spec/89 §1) and the Cut now sees **both** unless the user
  re-picks — surface that consequence in the dialog copy.
- **Cancel** → no render.

Surfacing:

- **Single-item `Export this`** — the dialog offers Overwrite / Keep both /
  Cancel (was Re-render / Cancel).
- **Batch `↑ Export now`** — when the run includes ≥1 edited-since-export
  item, the confirm modal ("Render N · Delete M") gains a **collision
  choice for the whole run** (Overwrite all / Keep both), defaulting to
  the user's last choice. (Per-item override is a possible v2; the batch
  default matches LRC's export-dialog single switch.)

## 4. Acceptance

- Re-editing an already-exported item makes its Export-grid cell show the
  loud "edited since export" badge; a clean item shows none; third-party
  returns never show it.
- `Export this` on such an item asks Overwrite / Keep both / Cancel.
  Overwrite replaces the file in place and the cell goes clean (recipe now
  matches); Keep both produces a 2-version cluster.
- After Overwrite, a Cut containing that frame shows the **new** pixels with
  no change to its membership or order. After Keep both, the Cut shows both
  versions until re-picked.
- Batch export with ≥1 stale item asks the run-level Overwrite/Keep-both
  choice; a run with none is unchanged.
- Export remains deliberate — no path auto-exports on edit.

## 5. Tests

- `tests/test_edited_since_export.py` — the stale predicate flags an item
  whose live recipe diverges from its render's `recipe_json`, clears after
  an OVERRIDE re-export, never flags a third-party return; the grid cell +
  cluster cover expose the badge flag.
- `tests/test_export_overwrite_choice.py` — `Export this` returns
  Overwrite/Keep-both/Cancel; OVERRIDE reuses the same `export_relpath` +
  lineage row (Cut membership stable) and refreshes `recipe_json` /
  `exported_at`; UNIQUE adds a `(2)` row (cluster forms); the batch confirm
  modal threads the run-level policy into `submit_export_batch`.
- Regress the preview-dialog staleness chip + the existing
  `CollisionPolicy` engine tests.

## 6. Implementation plan (commit order)

1. **Stale predicate → cell flag.** Lift `_is_preview_item_stale` to a
   reusable gateway/helper and expose it per grid cell + cluster cover.
2. **Badge QSS + paint.** Add the distinctive "edited since export" role
   and paint it on stale cells; mirror Edit-view badge consistency (§2).
3. **Overwrite/keep-both dialog.** Turn the single-item re-render-ask into
   the three-way choice; thread `CollisionPolicy` through `Export this`.
4. **Batch run-level choice.** Add the collision switch to the `↑ Export
   now` confirm modal and pass it into `submit_export_batch`.
