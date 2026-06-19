# spec/89 — Export surface rebuild (Model B, versions, the full design pass)

**Authored 2026-06-19 (Nelson + Claude). Implementation in progress —
see §7 for slice status (Slice 1 shipped 2026-06-19).**

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

### 1.1 A "version" = one shipped lineage row

A **version** of a source item is one row in `lineage` whose
`export_relpath` lives under `Exported Media/`. Any source item can
carry 0, 1, or many versions.

| Source item has… | Day-grid cell looks like… | Default intent |
|---|---|---|
| 0 versions | flat cell, Mira-render placeholder thumb | **red** (no intent to export) |
| 1 version (Mira or third-party) | flat cell, the version's thumb + provenance badge | **green** (a file exists, intent matches) |
| ≥2 versions | **versions cluster cover** + "N versions" count chip; drill in to compare + decide per version | members enter in **Compare orange** |

Border colour = **intent only** (will it be exported on the next pass);
badge = **on-disk state** (Mira / LRC / Helicon / Capture One / generic
"ext"). The two axes are orthogonal.

### 1.2 Versions cluster — state machine

Members of a freshly-discovered ≥2-version cluster enter in Compare
orange (intent semantically: "needs your attention"). Compare is
**cluster-only** — single-version cells never use it.

| Cluster member states | Cover border |
|---|---|
| Any member still in Compare (fresh / undecided) | **Compare orange** |
| All members decided **green** | green |
| All members decided **red** | red |
| All decided, mix of green + red | **yellow** (distinct from Edit's amber) |

A new external version added later to an already-decided cluster
enters as Compare → cover reverts to orange. Same behaviour as "you
have new versions to look at."

### 1.3 Versions cluster — drill-in (sub-grid)

- Order: **newest export time first**.
- Per-cell label: thumbnail only — the provenance badge (§2) carries
  identification.
- Click semantics (per §3): border = toggle, center = preview viewer.

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
| 2 | **Days List Export branch.** `exported` + `undecided` (Compare count) on `DaySnapshot`; three-slice bar in `DayRow`; `Export all / Drop all` labels with respect-decisions semantics; scan chip mirror. | pending |
| 3 | **Days Grid legend + pool filter.** Pool = picked ∪ shipped + "skipped in Pick" indicator chip; legend swatches + reminder + keymap hint per §4.2; border state machine for flat cells. | pending |
| 4 | **Provenance badges + scan chip.** Wordmark strip under each cell; chip in legend + Days List mirror; per-source breakdown wording on change. | pending |
| 5 | **Versions cluster reshape + sub-grid.** ≥2-version items become a synthetic cluster (`bucket_key = "versions:<item_id>"`); sub-grid surface; member Compare orange default; cover state machine; `"N versions"` count chip. | pending |
| 6 | **Preview viewer.** Center click → read-only viewer; P/X decide; Esc back; arrow stepping (within current surface); `Open in Editor` + `Export this` buttons; viewer content per §3.2 (read from disk for Mira renders). | pending |
| 7 | **Watermark repurpose.** Diagonal stamp = "this flip will delete a real file." | pending |
| 8 | **Export run triggers.** `Export now` batch button on both toolbars + confirm modal; single-item `Export this` re-render-ask dialog. | pending |
| 9 | **Video cluster updates.** New cover state machine (no Compare); hide empty videos; show only workshop-greened segments / snapshots inside. | pending |
| 10 | **Cleanup.** Drop dead code paths, update CLAUDE.md four-phase table + Cut section to reference the new model, retire `edit_candidate_*` tests, update spec/66 §1.2 / spec/72 §1 to point here. | pending |

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
