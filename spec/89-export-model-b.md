# spec/89 — Export surface rebuild (Model B, versions, the full design pass)

**Authored 2026-06-19 (Nelson + Claude). All 10 slices shipped
2026-06-19 + two eyeball-bug fixes (amber-border override, scanner
naming-prefix mismatch) + the Mira-intent-counts-as-version cluster
trigger refinement. §11 carries the live handoff for whoever picks
up the polish surface next.**

This spec consolidates the design pass for the **Export phase**:
- Implements spec/72 Model B (third-party returns hardlinked into
  `Exported Media/` on scan).
- Introduces the **versions** model for multi-edit photos.
- Rebuilds the **Days List**, **Days Grid**, and **preview viewer** to
  speak the new vocabulary.
- Adds the **scan chip**, **provenance badges**, **single-item Export**,
  and the **destructive-cue watermark**.

Parent specs that still govern: [spec/66](66-collect-pick-edit-export.md)
(phases), [spec/72](72-third-party-roundtrips.md) (Model B), [spec/68
§3](68-phase-redesign-coordination.md) (Export uses the Days
Lists/Grid spine).

There is **no migration step**: this is greenfield development; no
production events carry legacy `Edited Media/` lineage rows.

---

## 1. Core concepts

### 1.1 A "ship intent" = one decision the next Export pass will commit

A **ship intent** is anything the Export run on the next batch will
either render to disk or hardlink in place. There are two kinds:

1. **One lineage row** under `Exported Media/` — a Mira-rendered
   JPEG or a third-party return the scanner hardlinked at scan time
   (per §1.5).
2. **A Mira-render intent** — the source item carries a non-default
   `adjustment` row (look / crop / filter / rotation per
   [`core.edit_status.EDITED_SQL`](../core/edit_status.py)). The
   next Export run will render this to `Exported Media/`. This
   counts as a virtual version even before the JPEG exists.

The cluster threshold reads the **sum of both kinds**:

| Source item's ship-intent count | Day-grid cell looks like… | Default cell border |
|---|---|---|
| 0 | flat cell, source-photo thumb | **red** (Set aside — no intent to export) |
| 1 | flat cell, the intent's thumb + provenance badge | **green** (Will export) |
| ≥2 | **versions cluster cover** + `×N` count chip; drill in to compare + decide per intent | **orange** (Undecided — needs your attention) |

The same three states drive the Days-List three-slice bar (§4.1)
under the user-locked labels: **Will export** (green) · **Undecided**
(orange) · **Set aside** (red). Mixed cluster decisions
(some-picked-some-skipped) fold into Undecided for the bar; the cell
itself still paints the cover state machine's yellow.

The cluster sub-grid surfaces every intent: a virtual **Mira
member** (item_id `mira:<source_id>`, badged "Mira", state read from
`phase_state(edit, source)`) when the source has Mira-edit intent,
plus one cell per lineage row (item_id = `export_relpath`, badged
by §1.4 inference, state read from `lineage.intent_state`).

Border colour = **intent only** (will it be exported on the next
pass); badge = **on-disk state** (Mira / LRC / Helicon / Capture One
/ generic "ext"). The two axes are orthogonal.

Nelson eyeball 2026-06-19 — the cluster was originally specified as
"≥2 lineage rows on disk." That definition kept Mira-only edits
invisible until the user explicitly ran Export, which defeated the
"two intents, compare and choose" mental model the surface is for.
The current ship-intent definition makes "I edited this in Mira AND
in LRC" a real two-member cluster from the moment the LRC return
lands.

### 1.2 Versions cluster — state machine

Members of a freshly-discovered ≥2-intent cluster enter in Compare
orange (intent semantically: "needs your attention"). Compare is
**cluster-only** — single-intent cells never use it.

| Cluster member states | Cover border |
|---|---|
| Any member still in Compare (fresh / undecided) | **Compare orange** |
| All members decided **green** | green |
| All members decided **red** | red |
| All decided, mix of green + red | **yellow** (distinct from Edit's amber) |

A new lineage row added later to an already-decided cluster enters
as Compare → cover reverts to orange. Same behaviour as "you have
new versions to look at."

The lineage member's Compare wire value is `lineage.intent_state =
'compare'`; the Mira member's Compare wire value is `phase_state(edit,
source).state = 'candidate'` (or no row at all, which the renderer
also reads as Compare). The cover state derivation folds both into
"compare" for the colour choice.

### 1.3 Versions cluster — drill-in (sub-grid)

- Order: **newest export time first**.
- Per-cell label: thumbnail only — the provenance badge (§2) carries
  identification.
- Click semantics (per §3): border = toggle, center = preview viewer.
- **Compare button** on the sub-grid toolbar (spec/89 §11.3 polish,
  shipped 2026-06-19) — opens
  [`CompareVersionsDialog`](mira/ui/exported/compare_dialog.py)
  side-by-side: every version at full-definition pixels (file from
  disk OR live Mira-develop pipeline for virtual Mira members), each
  with the state border + provenance caption.
  - **Click a tile's border** → cycle picked ↔ skipped.
  - **spec/63 locked keymap** inside the dialog: `P` Will export ·
    `X` Set aside · `Space` toggle (all act on the focused tile);
    `← →` step focus; `Esc` closes.
  - The first tile is focused on open so the keyboard works
    immediately; mouse-clicking a tile also moves focus to it
    before firing the toggle.
  - Routes back through the existing per-version verb path
    (`set_lineage_intent` for lineage members,
    `phase_state(edit, source)` for the virtual Mira member); the
    sub-grid borders re-paint on close.

### 1.4 Provenance — the binary signal + the inferred badge label

Per spec/72 §1, provenance is an **unambiguous lineage signal** stored
on the row:

- `lineage.provenance` enum: `'mira_render'` | `'third_party'`.
- The displayed badge wordmark (Mira / LRC / Helicon / CO / ext) is
  inferred from the filename at render time — `mira_render` always
  reads as **"Mira"**; `third_party` rows are parsed for editor-tool
  hints (LRC, Helicon, Capture One, fallback generic "ext").

### 1.5 Scanner behaviour (Model B)

Runs **on every Export entry** (the scan is cheap in normal cases;
mtime gates are an implementation optimisation if needed at >50k files).

For each unknown file under `Edited Media/`:
1. **Hardlink** into `Exported Media/<filename>` (flat — `Exported Media/`
   is the ship set, not a mirror of `Edited Media/`'s subdir layout).
2. Write a `lineage` row with `export_relpath = "Exported Media/<filename>"`,
   `phase = "edit"`, `source_kind = "item"`, `source_item_id = <match>`,
   `provenance = "third_party"`, `recipe_json = NULL`.
3. The `Edited Media/<file>` original stays untouched (it is LRC's
   inbox, additive).

Idempotent by `export_relpath` PK. Name collisions in
`Exported Media/<filename>` skip with a logged warning (caller decides).

---

## 2. Provenance badges + scan chip

### 2.1 Badge

- **Placement:** thin strip **under the thumb** (not a corner chip).
- **Style:** small wordmark — `Mira`, `LRC`, `Helicon`, `CO`, `ext`.
- **On a versions cluster cover:** show only a `"N versions"` count
  chip — origin breakdown reveals on drill-in.
- **On a 0-version flat cell:** no badge (nothing on disk).

### 2.2 Scan chip (status indicator)

- **Persistent**, sits inline with the legend strip on the Days Grid.
- **Mirrored** at the top of the Days List (same wording, same shape).
- Wording examples:
  - No changes: *"External edits: up to date"*
  - Changes found: *"2 new external edits · 2 LRC · 1 Helicon"* (D5c.B
    per-source breakdown).
- Hover reveals last-scan timestamp; clickable to dismiss.

---

## 3. Click semantics + the preview viewer

### 3.1 Day-grid cells

| Cell type | Border click | Center click |
|---|---|---|
| Flat (1-version or 0-version) | **toggle ship/drop** | **open preview viewer** |
| Versions cluster cover | drill-in to sub-grid | drill-in to sub-grid |
| Versions cluster sub-grid member | toggle ship/drop | open preview viewer |
| Video cluster cover | drill-in | drill-in |
| Inside video cluster (segment / snapshot) | toggle ship/drop | open preview viewer |

### 3.2 Preview viewer

A read-only viewer that shows **the would-be or already-is shipped
pixels** — the actual image the user is committing to:

| Cell type | What the viewer shows |
|---|---|
| 0-version flat cell | Source photo run through Mira's develop pipeline at current adjustments — what would ship if the cell were greened. |
| 1-version Mira-rendered cell | The actual JPEG on disk in `Exported Media/`. **Read from disk** (option D1a.A) — fast and honest; if adjustments have changed since the last render, a small chip can later be added to surface staleness (deferred). |
| 1-version third-party return | The actual third-party file on disk, untouched. |
| Cluster sub-grid member | Each version's actual file. |

Viewer controls:
- **P / X** decide on the focused cell.
- **Esc** backs out.
- **Arrow keys step to neighbours** (D1b.A — stepping stays within the
  current surface; flat-cell view steps to flat-cell siblings,
  cluster sub-grid view steps to other versions of the same cluster).
- **"Open in Editor"** button (D4.C) — opens the Editor for last-minute
  tone / crop tweaks before committing.
- **"Export this"** button (single-item run — see §5.2).

---

## 4. Days List + Days Grid surfaces

### 4.1 Days List per-day cards (Export phase)

- **Three-slice bar** (D1.C): `Shipped` green + `Undecided` Compare
  orange + `Dropped` red, summing over picked keepers.
- **Per-day buttons:** **"Export all" / "Drop all"** (D2.A labels).
- **Bulk behaviour (D2a.B):** respect explicit P/X decisions — only
  commit Compare members + default-state cells; never override a cell
  the user has touched.
- **Counter unit (Block 6 D4.A):** **source level**. One source video
  = one keeper unit, shipped if at least one of its segments /
  snapshots shipped.
- **Scan chip (D3.B):** mirror of the grid chip at top of the page.

### 4.2 Days Grid — Export mode

#### Legend strip (Block 4 locks)

- **Three swatches** (D1.B): *"Will export"* (green) · *"Dropped"*
  (red) · *"Undecided"* (Compare orange). Mixed yellow learned by
  example, not legended.
- **Keymap hint** (D2.A): *"P Export · X Drop · Space toggle"*.
- **Reminder** (D3.A): *"border = decision · wordmark = origin · count
  chip = versions"*.

#### Pool inclusion (Block 7 D1.B)

The Export grid shows: **picked keepers ∪ any item with a file in
`Exported Media/`**. The union covers the edge case where a user
skipped an item in Pick but later dropped an LRC export for it
(scanner hardlinks it → it appears in Export so the user can either
delete the file or re-Pick).

#### Skipped-but-shipped indicator (Block 7 D2.B)

Items that are Pick-skipped but have a shipped file: shown with **red
border** + a small **"skipped in Pick"** indicator chip so the user
knows why this is here.

#### Exported watermark — repurposed (Block 7 D3.B)

The existing diagonal "Exported" stamp **flips meaning** on the Export
surface: it lights up on cells where a green→red flip **would unlink
an actual file** — a visual "this is destructive" cue. Purely
informational on other surfaces.

---

## 5. Run triggers — batch + single-item

### 5.1 Batch "Export now"

- **Label** (D1.A): **"Export now"**.
- **Confirmation** (D2.B): brief modal — *"Render N · Delete M files.
  Proceed?"* with Cancel / Run.
- **Placement** (D3.B): on **both** the Days List toolbar (all-days
  scope) and the Days Grid toolbar (current-day scope).

The run:
1. Renders every green-intent item that has 0 versions via the spec/60
   batch engine — writes Exported Media file + lineage row +
   `provenance='mira_render'`.
2. Deletes Exported Media files for red-intent cells that still have
   on-disk files; drops their lineage rows; cascades Cut membership
   (spec/61 §1.4).
3. Third-party returns: nothing to do — already hardlinked at scan
   time.

### 5.2 Single-item "Export this"

- **Placement** (D4.A): **preview viewer only** — alongside the "Open
  in Editor" button.
- **Label + state coupling** (D5.A): button text **"Export this"**;
  **disabled when the cell is red**. User must P (green) first to
  activate the button.
- **Re-render behaviour** (D6.C): if the item already has a shipped
  Mira render, **ask** — *"An export already exists. Re-render with
  current settings?"* with Cancel / Re-render. Third-party returns
  are never re-rendered (no recipe).

---

## 6. Videos — segments and snapshots

### 6.1 Video as a structural cluster

A picked source video appears in Export as a **structural cluster
cover** (current `_reshape_for_export` shape):
- Drill-in shows segments + snapshots as members.
- **Only segments/snapshots the user greened in the workshop** are
  shown — workshop-skipped ones don't appear in Export at all.
- **Source video with no picked segments AND no snapshots: hidden
  from Export entirely** (D3.B in Block 6).

### 6.2 Inside the video cluster

Members are **flat green/red cells** — no versions concept (D1.C in
Block 6). The surfacing rule IS the keeper rule, so:
- **Default state: green.** Showing it means it's in the pool.
- No Compare orange (no versions = no need for the "compare" state).

### 6.3 Video cluster cover state machine

Same machine as versions clusters but without the Compare leg, since
members can never be Compare:
- All members green → cover green.
- All members red → cover red.
- Mixed → cover yellow.

### 6.4 Asymmetry with photos (deliberate)

Picked **photos** with 0 versions default **red** (must opt in to a
Mira render). Picked **video segments** with 0 versions default
**green** (workshop pick = enough commitment). This is intentional —
videos are simpler — but worth flagging so a future pass doesn't
accidentally normalise it.

---

## 7. Slicing — implementation order

Each slice its own commit, runnable, gated by `verify.bat`. Visible
improvement on every slice.

| # | Slice | Status |
|---|---|---|
| 1 | **Schema + scanner foundation.** Add `lineage.provenance` column (NOT NULL DEFAULT `'mira_render'`, CHECK enum); schema v9→v10 migration; scanner hardlinks `Edited Media/` returns to `Exported Media/` and stamps `provenance='third_party'`; retire `edit_candidate_*` gateway calls and the batch.py partition / `_hardlink_third_party_returns` (now dead — scanner does the work). | **shipped 2026-06-19** |
| 2 | **Days List Export branch.** `exported` + `undecided` (Compare count) on `DaySnapshot`; three-slice bar in `DayRow`; `Export all / Drop all` labels with respect-decisions semantics; scan chip mirror. | **shipped 2026-06-19** |
| 3 | **Days Grid legend + pool filter.** Pool = picked ∪ shipped + "skipped in Pick" indicator chip; legend swatches + reminder + keymap hint per §4.2; flat-cell intent inference (0-version red / 1-version green) per Block 1 D1.C. | **shipped 2026-06-19** |
| 4 | **Provenance badges + scan chip.** Wordmark strip under each cell; chip in legend + Days List mirror; per-source breakdown wording on change. | **shipped 2026-06-19** |
| 5 | **Versions cluster reshape + sub-grid.** ≥2-version items become a synthetic cluster (`bucket_key = "versions:<item_id>"`); sub-grid surface; member Compare orange default; cover state machine; `"N versions"` count chip. | **shipped 2026-06-19** |
| 6 | **Preview viewer.** Center click → read-only viewer; P/X decide; Esc back; arrow stepping (within current surface); `Open in Editor` + `Export this` buttons; viewer content per §3.2 (read from disk for Mira renders). | **shipped 2026-06-19** |
| 7 | **Watermark repurpose.** Diagonal stamp = "this flip will delete a real file." | **shipped 2026-06-19** |
| 8 | **Export run triggers.** `Export now` batch button on both toolbars + confirm modal; single-item `Export this` re-render-ask dialog. | **shipped 2026-06-19** |
| 9 | **Video cluster updates.** New cover state machine (no Compare); hide empty videos; show only workshop-greened segments / snapshots inside. | **shipped 2026-06-19** |
| 10 | **Cleanup.** Drop dead code paths, update CLAUDE.md four-phase table + Cut section to reference the new model, retire `edit_candidate_*` tests, update spec/66 §1.2 / spec/72 §1 to point here. | **shipped 2026-06-19** |
| — | **Post-eyeball corrections.** (a) Restrict the Edit-grid amber `border_token` override to pure Edit (Export was inheriting it). (b) Relax scanner matcher to accept LRC's default bare-filename export naming + surface `unmatched` in the chip + log a WARNING when nothing matched. (c) Mira-edit intent counts as a virtual ship version so a Mira-edit + a third-party return for the same source now forms a cluster (§1.1 refined). | **shipped 2026-06-19** |

---

## 8. What does NOT change

- The locked keyboard map (spec/63) — untouched.
- Decision verbs Pick / Skip; the `'picked'` state value.
- The Cut model (spec/61) — `#exported` is still "everything in
  `Exported Media/`"; Model B just changes what enters that set.
- The closed-event card body (carousel + stat grid) — still the Share
  view.

---

## 9. Open follow-ups (out of scope for this spec)

- **Editor-app glyphs.** Slice 4 ships text wordmarks (`LRC`,
  `Helicon`, etc.). App-specific icons can replace them in a later
  visual polish pass.
- **Adjustments-changed staleness chip in preview viewer.** D1a.A
  ("read from disk") is honest but stale-tolerant. A "Adjustments
  changed — Export to refresh" chip is deferred until the surface
  ships.
- **Snapshot multi-version nesting.** A snapshot IS a photo and could
  technically have 2+ external versions. Slice 9 keeps the video
  cluster's interior flat (no nested versions cluster); the nested
  case is a deliberate v2 enhancement.

---

## 10. Related

- [spec/66 — Collect / Pick / Edit / Export](66-collect-pick-edit-export.md)
- [spec/68 — Phase redesign coordination](68-phase-redesign-coordination.md)
- [spec/72 — Third-party round trips](72-third-party-roundtrips.md) — the
  Model B parent spec; this spec is the implementation framing.
- [spec/57 — Folders and round trip](57-folders-and-roundtrip.md)
- [spec/60 — Batch export engine](60-batch-export-engine.md)
- [spec/61 — Share event Cuts](61-share-event-cuts.md)
- [spec/63 — Photo viewport / locked keymap](63-photo-viewport.md)

---

## 11. Handoff notes (post-eyeball, polish surface)

**Pick up here in a fresh session.** Working tree is clean at
[`4cd7241`](https://github.com/nksalgado-proton/Mira/commit/4cd7241);
all 10 slices shipped, plus the eyeball-pass corrections (see
§11.1). The remaining surface is **polish + nice-to-haves** — see
§11.3 below — not a functional gate.

### 11.1 Post-slice eyeball-pass fixes (2026-06-19)

The first live walkthrough on Nelson's Alaska event surfaced three
real bugs that landed after the original 10 slices. They are all
shipped; flagged here so a fresh session knows what the commits
mean and doesn't accidentally revert them.

- **Amber-border override.**
  [`_items_from_cells`](mira/ui/pages/days_grid_page.py) gated the
  Edit-grid amber/green `border_token` on `self._phase == "edit"`
  alone. Export shares the `'edit'` phase storage (spec/66 §1.1), so
  every cell read amber-edited / green-unedited, completely
  drowning the green / red ship-intent border. Gate is now
  `self._phase == "edit" and not self._export_mode`. Commit
  [`11616e8`](https://github.com/nksalgado-proton/Mira/commit/11616e8).
- **Scanner filename-prefix mismatch.** The matcher only knew the
  full Picked-Media link stem (`D{day}_{cam}_{originalname}`).
  Lightroom Classic's default export preset emits files keyed off
  the ORIGINAL filename (`IMG_1234-Edit.jpg`) and rejected every
  return. `_all_item_stems` now registers BOTH the full link stem
  and the bare origin filename stem; the longest-prefix-wins rule
  keeps the strict match preferred when both are available. The
  scan chip now also surfaces `unmatched` (chip used to read "up to
  date" while 31 files sat un-linked); the scan log carries a
  WARNING with the first five rejected names when the matcher
  rejects everything. Same commit.
- **Mira-edit-intent → virtual cluster member.** A source with a
  Mira-edit intent AND a third-party return on disk now forms a
  cluster (was: only ≥2 lineage rows). New gateway helper
  [`items_with_mira_intent`](mira/gateway/event_gateway.py); the
  reshape in
  [`_reshape_for_versions`](mira/ui/pages/days_grid_page.py) counts
  Mira intent as a virtual version and inserts a synthetic Mira
  member (`item_id = "mira:<source_id>"`) when drilling into the
  cluster. P/X on the Mira member writes
  `phase_state(edit, source)`; P/X on a lineage member still writes
  `lineage.intent_state` (existing). Commit
  [`4cd7241`](https://github.com/nksalgado-proton/Mira/commit/4cd7241).

### 11.2 Slice rollup (everything shipped 2026-06-19)

**Slice 8 — Export run triggers** (shipped 2026-06-19). Days Grid
button is `↑ Export now` (D1.A); confirm modal carries
"Render N · Delete M files. Proceed?" with Cancel / Run (D2.B). The
all-days variant lives on the Days List header
([`DaysListsPage._export_now_btn`](mira/ui/pages/days_lists_page.py),
visible only under the Export identity; MainWindow handler
`_on_days_lists_export_now` walks every day, sums N+M, asks once,
then runs delete + per-day batch submits). M counts versions cluster
lineage rows with `intent_state='skipped'` whose file still exists
on disk (the Slice-5 deferred deletes land here, plus the legacy
flat-cell auto-delete on X). Single-item path
[`DaysGridPage._on_preview_export_this`](mira/ui/pages/days_grid_page.py)
now submits a one-cell batch and shows the D6.C re-render-ask when a
Mira-render version already exists (third-party-only history goes
straight through — a fresh Mira render lands as a new version
alongside per spec/54 §8). The "Export this" button on the preview
viewer is disabled unless `state == 'picked'` (D5.A); that contract
remains enforced in `ExportPreviewDialog`.

**Slice 9 — Video cluster updates** (shipped 2026-06-19).
[`_video_cluster_grid_item`](mira/ui/pages/days_grid_page.py) now
filters segments + snapshots to ``phase_state(edit) == 'picked'``
(workshop-greened only; Block 6 D1.C). The new
[`_video_cover_color`](mira/ui/pages/days_grid_page.py) static
helper paints the cover with no Compare leg (Block 6 §6.3 — all
picked → green, all skipped → red, mixed → yellow). The pre-Slice-9
"keep flat when no children" fallback in `_reshape_for_export` is
gone (Block 6 D3.B): a video with no workshop-greened segments AND
no workshop-greened snapshots drops out of the Export grid entirely.
The user has to return to the Workshop and green something to bring
it back.

**Slice 10 — Cleanup** (shipped 2026-06-19). (a) The retired
partition / `to_render` alias in
[`submit_export_batch`](mira/ui/exported/batch.py) is gone, and the
module-top docstring no longer describes a hardlink fork through
this helper (spec/89 §1.5 — scanner does it at scan time). The
`_strip_post_v6_lineage_cols` test fixture **stays** — it's still
needed for the v6→v7 migration tests
([`tests/test_store.py`](tests/test_store.py)) that start from a v6
shape and need the post-v6 ADD COLUMN migrations to not collide.
(b) CLAUDE.md's four-phase table now points at spec/89 from the
Export row; the Cut section flags Model B's expanded `#exported`
set (Mira renders + hardlinked third-party returns). (c) spec/66
§1.2 and spec/72 §1 each carry the "implementation framing: see
spec/89" pointer at the top. (d) **Eyeball check** (open question
for the next session): badge strip readability, scan-chip wording,
cluster cover thumbnail. The live newest-version cover preview is
still the deferred polish in §9 first/second bullets.

### 11.3 Polish surface

Most of the polish landed 2026-06-19; the deferred items are flagged
with explicit reasons so a future session knows what's left.

- ~~**Live Mira-develop preview in the viewer.**~~ **Shipped
  2026-06-19** — new [`core.preview_render.develop_photo_array`](core/preview_render.py)
  runs the source through the full pipeline (rotation → tone →
  filter → crop) using the live `Adjustment` row, capped at the
  dialog's max long-edge so a 6000-px source doesn't pay full-res
  cost. [`ExportPreviewDialog`](mira/ui/exported/preview_dialog.py)
  dispatches via `_load_pixmap_for`; the host
  ([`DaysGridPage._preview_develop_kwargs`](mira/ui/pages/days_grid_page.py))
  enables it for 0-version cells + virtual Mira cluster members
  (`item_id` starts with `mira:`) and skips it when `path` already
  points at an `Exported Media/` file. Pipeline failures fall back
  to a raw source-bytes read so the user always sees SOMETHING.
- ~~**Cluster cover thumbnail = newest version.**~~ **Shipped
  2026-06-19** — [`_versions_cluster_grid_item`](mira/ui/pages/days_grid_page.py)
  picks `rows[0]` from `versions_for_item` (newest-first per
  Slice 5) as the cover's `_path`, clears the source's `_sha256`
  so the in-memory pixmap cache keys on the version's file
  instead of mis-serving the source thumb. The initial paint
  shows the source thumb as a brief placeholder until the async
  decoder swaps in the version's pixels. Mira-intent-only
  clusters (no on-disk version yet) still use the source thumb
  as the cover — there's nothing else to show.
- **App-specific badge icons.** *Deferred — needs design assets.*
  Slice 4 ships text wordmarks (`Mira`, `LRC`, `Helicon`, `CO`,
  `ext`). App-specific icon glyphs would replace them, but the
  asset audit (2026-06-19) only found `assets/icons/mira-mark.svg`
  + `mira.ico` + `mira.png` — no third-party glyphs. Authoring
  LRC/Helicon/Capture-One-look-alike SVGs (and clearing them for
  trademark) is a design-pass concern that needs Nelson, not
  headless work. The text wordmarks are unambiguous and ship-ready
  in the meantime.
- ~~**Adjustments-changed staleness chip.**~~ **Shipped 2026-06-19**
  — [`ExportPreviewDialog`](mira/ui/exported/preview_dialog.py)
  carries an `Adjustments changed — Export to refresh` chip that
  fires when the focused cell's live `Adjustment` (via
  `recipe_for_item`) no longer matches the on-disk Mira render's
  `lineage.recipe_json`. The host populates `PreviewItem.is_stale`
  per cell ([`DaysGridPage._is_preview_item_stale`](mira/ui/pages/days_grid_page.py));
  third-party returns short-circuit to False (no recipe to diff).
- **Snapshot multi-version nesting.** *Deferred — explicit v2.*
  A video snapshot is a photo and could carry 2+ external
  versions. Slice 9 kept the video cluster's interior flat (no
  nested versions cluster) and §9 already flagged this as a
  deliberate v2 enhancement. The nested case widens the cell
  taxonomy meaningfully (cluster-inside-cluster) and deserves its
  own design pass before code.
- ~~**Days List bar accuracy under the new ship-intent rule.**~~
  **Shipped 2026-06-19** — the `phase_day_progress` export bucket
  is now **per source** (one tally per picked keeper) with the
  user-locked default-state rule (Nelson 2026-06-19):
  **0 intents → Set aside** (override with explicit
  `phase_state(edit)`); **1 intent → Will export** (Mira-edit only
  OR one third-party return); **≥2 intents → Undecided** (cluster).
  Cluster member decisions roll up through the cover state machine
  in [`EventGateway._export_source_state`](mira/gateway/event_gateway.py)
  — Mixed picked + skipped folds into Undecided for the bar
  (Mixed is not a bar bucket, only a cell colour). The DayRow bar
  carries the locked labels **Will export** / **Undecided** / **Set
  aside** (was Shipped / Dropped); the verb buttons stay **Export
  all** / **Drop all**. *Note: this supersedes the earlier
  intent-level interpretation also shipped 2026-06-19; only one
  rule lives on `main`.* The
  [`DayRow`](mira/ui/pages/days_lists_page.py) Export branch's
  denominator is `shipped + undecided + dropped` (instead of the
  clamped `picked` it used pre-polish). Backwards-compat keeps the
  `decided / committed / picked` legacy fields populated.
- **Eyeball the scan chip wording end-to-end.** *Deferred —
  needs Nelson in the loop.* The chip is unit-tested via
  `test_scan_chip_text` but the visual check (badge readability,
  mixed match/unmatched runs, the "31 files in Edited Media/"
  failure path side-by-side with the real surface) needs a live
  walkthrough on a real event. Out of scope for headless work.

