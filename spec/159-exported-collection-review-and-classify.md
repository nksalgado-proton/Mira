# 159 — Exported Collection review and classify

**Status: PROPOSED (Nelson 2026-06-30). After Pick / Edit / Export, the
photos that *shipped* are the survivors of the full pipeline — the
content the user actually cares about. Today the closed-event Cut page
exposes an Exported Collection grid where clicking a tile only marks
it for deletion; there is no way to look at a shipped photo in full
res, no way to attach a rating, and no way to compare alternate
versions of the same shot side-by-side. This spec turns the Exported
Collection into the canonical place to *judge* the survivors:
per-version star rating, LRC-style colour label, portfolio flag, plus
a separate "marked for deletion" badge with batched commit. Versions
of the same source item cluster together (reusing spec/89 Slice 5's
`versions:` cluster) so two renders of one shot sit next to each
other in the grid. Center-click on a cell opens the existing Editor
in a new `review_mode` that hides the creative chrome (develop / crop
/ look / marker timeline) and surfaces the classification widgets in
the slot the Style classifier already occupies. The Editor loads the
actual exported bytes from `Exported Media/` so the user is judging
what shipped, not what the develop pipeline would re-emit. Touches
`mira/store/schema.py` (one migration adding four columns on
`lineage`), `core/path_builder.py` (nothing new), `mira/gateway/
event_gateway.py` (six new mutator + reader pairs), `mira/ui/pages/
share_cuts_page.py` (the Exported Collection grid surface + the new
border-click semantics + filters), `mira/ui/edited/editor_page.py`
(`review_mode` flag + chrome gating + classification header
extension), `mira/ui/design/thumbs.py` (three new cell visual
slots: colour-label top strip, star chip, flag glyph, plus the "To
be Deleted" bottom strip), and `mira/ui/exported/` (Delete-confirm
dialog + filter dropdown widget). Reuses spec/89 Slice 5's
versions-cluster infrastructure verbatim. No new dependency.**

## 0. Vocabulary

- **Exported Collection** — the live set of every file that exists
  under `<event_root>/Exported Media/` for the event, surfaced as a
  grid on the closed-event Cut page (already half-built; this spec
  takes it the rest of the way).
- **Version** — one row in the `lineage` table. Each version is one
  shipped file under `Exported Media/`. A single source item can
  produce ≥1 versions (a Mira render PLUS a Lightroom return PLUS a
  second Mira pass with a different look — three lineage rows on one
  item). Spec/89 calls this the lineage stack.
- **Versions cluster** — spec/89 Slice 5 cluster bucket
  `versions:<item_id>`. Synthetic; exists whenever a source item
  carries ≥2 lineage rows. Already implemented for the Export-mode
  sub-grid; this spec surfaces it on the Exported Collection grid.
- **Review mode** — a new flag on `EditorPage` that opens the editor
  against a *lineage row's exported file* (not the source item's
  develop output) and hides every creative-edit control while
  surfacing the classification header.
- **To be Deleted** — a new boolean flag on `lineage` that marks the
  exported file for batch deletion. Independent of pick/skip intent.

## 1. The user-visible flow

1. Event is closed. User opens the Cut page → sees the existing
   Exported Collection entry → clicks Open.
2. Grid appears. Cells show shipped photos / clips at thumb
   resolution. Versions of one source item are grouped into a
   versions cluster (a single cover cell with a "×N" chip); the user
   can click into the cluster to see the individual versions or
   center-click the cover to drill in.
3. On any cell:
   - **Border-click** — toggles the "To be Deleted" badge on the
     lineage row.
   - **Center-click** — opens the editor in `review_mode` against
     that lineage row.
4. In the review editor:
   - Pixels shown = the exported file's bytes (Mira render or
     third-party return — whatever is under
     `Exported Media/<export_relpath>`).
   - Top header: Style classifier (editable, per-item) + Stars 1–5
     (per-version) + Colour label (per-version) + Portfolio flag
     (per-version).
   - Below: the photo, full-canvas, with the spec/63 proxy/original
     tier engine.
   - Keys: 1–5 stars, Shift+1..5 colour label, K flag toggle, D
     toggle "To be Deleted", ←/→ prev/next version, F10 truth key,
     F / F11 fullscreen, Esc closes.
   - Bottom: a thin status strip with version count ("3 of 12") and
     small chips echoing the current ratings.
5. Back on the grid: ratings render on the cell (colour-label top
   strip, star chip bottom-right, flag glyph top-left); deletion
   badge across the bottom 10 %.
6. Toolbar: **"⌫ Delete N marked…"** primary action (count-aware,
   confirm dialog before disk action). **Compare** button (reuses
   the spec/63 §4 follow-up day-grid Compare). **Filter** dropdown.

## 2. Schema additions

### 2.1 `lineage` — four new columns

```sql
ALTER TABLE lineage ADD COLUMN stars        INTEGER;     -- 1..5 or NULL
ALTER TABLE lineage ADD COLUMN color_label  TEXT;        -- 'red'|'yellow'|'green'|'blue'|'purple' or NULL
ALTER TABLE lineage ADD COLUMN flag         INTEGER NOT NULL DEFAULT 0
                                  CHECK (flag IN (0,1)); -- portfolio flag
ALTER TABLE lineage ADD COLUMN to_delete    INTEGER NOT NULL DEFAULT 0
                                  CHECK (to_delete IN (0,1)); -- "marked for deletion"

CREATE INDEX ix_lineage_stars
  ON lineage(stars) WHERE stars IS NOT NULL;
CREATE INDEX ix_lineage_color_label
  ON lineage(color_label) WHERE color_label IS NOT NULL;
CREATE INDEX ix_lineage_flag
  ON lineage(flag) WHERE flag = 1;
CREATE INDEX ix_lineage_to_delete
  ON lineage(to_delete) WHERE to_delete = 1;
```

Bump `SCHEMA_VERSION`. New migration adds the four columns + four
partial indexes. All four default to NULL / 0 so existing rows
read as "unrated, unflagged, not marked for deletion".

**Rationale for per-version**: a single source item with multiple
exports (Mira render + LR return) carries independent ratings; the
user might prefer the LR return aesthetically and rate it 5★ while
the Mira render gets 3★. Storing ratings on `item.extras_json`
(spec/32's original location for `stars` / `color_label` / `flag`)
would force one rating across all versions. **Spec/32 §3.1's item-
level ratings remain valid for Pick-phase pre-export curation; they
are NOT what this spec uses.** The two storages are independent:
spec/32 stars on `item.extras_json` describe the *shot*; spec/159
stars on `lineage` describe the *shipped rendering*.

### 2.2 No change to `item.classification`

Style classification stays on `item.classification` (the existing
column). When the user edits Style in the review editor, all
versions of that source item read the new value — semantically
correct (Style describes the subject, not the rendering).

## 3. Gateway surface

```py
EventGateway.set_lineage_stars(export_relpath: str, stars: int | None) -> None
EventGateway.set_lineage_color_label(export_relpath: str, label: str | None) -> None
EventGateway.set_lineage_flag(export_relpath: str, flag: bool) -> None
EventGateway.set_lineage_to_delete(export_relpath: str, to_delete: bool) -> None
EventGateway.lineage_ratings(export_relpath: str) -> LineageRatings  # bag read
EventGateway.exported_marked_for_deletion() -> list[Lineage]          # the "Delete N…" pool
EventGateway.delete_marked_exported_files() -> int                    # returns count actually deleted
```

`LineageRatings` is a small NamedTuple / dataclass: `(stars,
color_label, flag, to_delete)`. The four single-field setters each
funnel through `_touch()` (the existing read-only-library guard +
backup-snapshot trigger). Each setter validates: stars ∈ {1..5,
None}; color_label ∈ {'red','yellow','green','blue','purple',
None}; flag / to_delete ∈ {0, 1}.

`delete_marked_exported_files()` reuses
`EventGateway.delete_exported_file(item_id)` per row — the existing
unlink + lineage row drop + edit_exported clear + Cut membership
cascade pipeline (spec/61 §1.4). Wraps every row in one transaction
so a half-failed run leaves the database consistent.

## 4. The Exported Collection grid

### 4.1 Location

Surface lives in `mira/ui/pages/share_cuts_page.py` (the closed-
event Cut page). Currently the Exported Collection entry is a button
that opens a grid in some half-built form; this spec replaces the
grid implementation with a fully-specified one.

### 4.2 Cell content

Reuses the existing `Thumb` widget from `mira/ui/design/thumbs.py`,
with three new visual slots:

- **Colour-label strip** — 4 px tall, full cell width, along the
  TOP edge. Solid colour matching the `color_label` value (`red` →
  #D9382E, `yellow` → #E4B91F, `green` → #2DA84A, `blue` →
  #3A8DD8, `purple` → #9C4DC9). Hidden when `color_label IS NULL`.
- **Star chip** — small "★N" chip in the BOTTOM-RIGHT corner.
  Free on non-cluster cells; cluster covers keep the existing "×N"
  count chip there. Hidden when `stars IS NULL`.
- **Flag glyph** — a small flag SVG (use `assets/icons/glyphs/`
  pattern, new `flag.svg`) in the TOP-LEFT corner. Hidden when
  `flag = 0`.

Plus the existing chrome, kept selectively:

- **Visited eye chip** (top-right) — KEEP. Tells the user which
  versions they've reviewed.
- **Exported watermark** (top-left) — DROP. Everything here is
  exported; redundant.
- **Edit reasons pill** (bottom-left) — DROP. Out of scope; the
  user is no longer editing in this surface.
- **Cluster count** (bottom-right) — KEEP for versions cluster
  covers. Conflicts with the star chip on cluster covers, which is
  fine because per §6 we explicitly DON'T render stars on cluster
  covers.
- **State border** (green / red / orange) — default OFF. The
  Pick / Skip colour grammar isn't meaningful in closed-event
  review. **Compare-marked orange border STAYS** as a special case
  so the Compare button (spec/63 §4 follow-up) can find the marked
  set.

The **"To be Deleted" badge** is a full-width strip across the
bottom ~10 % of the cell — opaque dark-red bg (#A02020), white
"Marked for deletion" text (`tr("Marked for deletion")`), 4 px
internal padding. Painted in Thumb's paintEvent after the existing
overlays; reads at any tile size from the size slider's range. The
star chip in the bottom-right hides while this badge is visible
(no point showing a rating on a thing about to be deleted).

### 4.3 Click grammar (this surface specifically)

| Gesture | Cluster cover | Single-version cell |
|---|---|---|
| **Border-click** | Open the cluster (existing) | Toggle "To be Deleted" badge |
| **Center-click** | Open the cluster (existing) | Open Editor in review mode |
| **C key** | (cluster — no-op on cover) | Toggle Compare-mark |

The locked spec/63 keys (P / X / Space) DO NOT apply on this
surface — there is no Pick/Skip decision to make in closed-event
review. The Pick grid grammar lives on the Pick day grid; this
surface uses its own (smaller) grammar. C still routes through
the existing `_apply_verb_at_index(cycle)` path so Compare-mark
works the same way it does on the day grid.

### 4.4 Versions cluster cover

Per §6: no rating chrome on the cover (averaging is meaningless,
"max" or "best" can be misleading). The cover shows:

- The first-photo-version's pixmap (existing).
- The `×N` cluster count chip (bottom-right, existing).
- If any inner version carries `to_delete = 1`: a small
  **"N/M to delete"** chip in the bottom-left (e.g. `2/4` if 2 of 4
  versions are marked). New chip family; the empty case hides the
  chip entirely.

Click semantics on the cover stay consistent with existing cluster
behaviour: any click opens the cluster sub-grid.

### 4.5 Toolbar

| Widget | Purpose |
|---|---|
| `⌫ Delete N marked…` primary button | Opens a confirm dialog with the count, the total bytes-on-disk, and an "Are you sure?" + the explicit warning that the unlink also propagates to Cut membership. Count = `len(exported_marked_for_deletion())`. Visible only when count ≥ 1. |
| Compare button (existing from spec/63 §4 follow-up) | Same behaviour as the day grid: visible when ≥ 2 cells are Compare-marked, opens the grid Compare dialog with those cells. |
| Filter dropdown | Single QToolButton with a popup menu. Items: **Min stars** (radio 1/2/3/4/5/any), **Colour label** (multi-select), **Flag** (yes / no / any), **Hide marked-for-deletion** (toggle). Applies in-memory to the rendered cell list; doesn't mutate the gateway query. |

### 4.6 Filter semantics

The filter dropdown applies on top of the existing
`exported_files()` gateway query. Implementation: read the full
list, render filtered. State is session-local (not persisted) so
the user always opens a fresh, unfiltered view on a Cut page
revisit. The Compare-mark and to-delete badges survive filter
toggles (state lives on `lineage`, not the in-memory cell).

## 5. The review-mode Editor

### 5.1 The flag

`EditorPage` gains a new constructor kwarg `review_mode: bool =
False`. When True:

- The host (share_cuts_page) passes the lineage row's
  `export_relpath` instead of the source item's `item_id`.
- The editor resolves the file to
  `<event_root>/Exported Media/<export_relpath>` and renders it
  via the spec/63 photo viewport's "original" tier — the bytes on
  disk, no develop pipeline.
- The editor's `_resolve_item()` returns the underlying source
  item (looked up via `lineage.item_id`) so Style classification
  still reads / writes `item.classification`.

### 5.2 Chrome gating

Hide in `review_mode`:

- Develop section (looks picker, sliders, white-balance, exposure,
  Style strength slider — everything below the Style row).
- Crop overlay + crop toolbar.
- Look picker / recipe row.
- Marker timeline (video only).
- "Save / Apply" — there is nothing to save; ratings persist
  on key press.

Keep:

- The photo viewport (spec/63 engine).
- The top metadata header — Style classification chip (editable)
  + the new four rating widgets (stars, colour label, flag,
  to-delete toggle).
- The transport bar (for video review — Tab plays / pauses).
- The truth key F10 (already shows the on-disk file, no behaviour
  change in review mode).
- Esc / F / F11 — close / fullscreen.
- Ctrl+Z — undoes the most recent rating or to_delete change.

### 5.3 The classification header

Existing layout: Style chip + (in review mode) star row + colour
row + flag + to-delete toggle, all in a horizontal strip across
the top. Star row: five filled / empty star glyphs; clicking the
Nth fills 1..N and clears N+1..5. Clicking the already-filled Nth
clears all (LRC convention). Colour row: five colour dots + a
"clear" slot. Flag: single toggle glyph. To-delete: a small badge
toggle "✗ Mark for deletion".

### 5.4 Keyboard map in review mode

| Key | Action |
|---|---|
| `1..5` | Set stars to 1..5 |
| `0` | Clear stars |
| `Shift+1..5` | Set colour label (1=red, 2=yellow, 3=green, 4=blue, 5=purple) |
| `Shift+0` | Clear colour label |
| `K` | Toggle portfolio flag |
| `D` or `Delete` | Toggle "To be Deleted" |
| `←` `→` | Prev / next version (cycles through every version in the visible Exported Collection list, including across versions clusters) |
| `F10` | Truth key (existing) |
| `F` / `F11` | Fullscreen (existing) |
| `Esc` | Close (existing) |
| `Ctrl+Z` | Undo last rating / delete-flag change |
| `P` / `X` / `Space` / `C` | **No-op in review mode** (the locked Pick grammar isn't relevant here). |

### 5.5 Next / prev navigation

`←` and `→` cycle through the **visible** Exported Collection list
(post-filter). One stop per version: if a versions cluster has 3
versions, the nav makes 3 stops as the user arrows through. After
the last version, wraps to the first (or stops — see §7 §C). The
viewer's "version count" label reads "N of M" where M is the
filtered list length.

## 6. Compare button reuse

The spec/63 §4 follow-up day-grid Compare button (`⇄ Compare (N)`,
visible when ≥ 2 cells are Compare-marked) carries over to this
surface verbatim. Same handler, same dialog. The dialog already
treats per-tile titles + state chips as optional (spec/63 §4
follow-up's tile clean-up); titles are blank, status chips hidden,
border colour encodes Compare state. Border-click in the dialog
routes through `_apply_verb_at_index(toggle)`, which on this surface
keeps doing what it does on the day grid.

## 7. Operational details

### A. Delete confirm dialog

Reuses `mira.ui.design.dialogs.confirm` shape:

```
Delete 8 exported files?

This unlinks the files under Exported Media/, drops their lineage
rows, and removes them from any Cut that referenced them.

The source media in Original Media/ is unchanged.

[Cancel]   [Delete 8 files]
```

On click, runs `delete_marked_exported_files()` (which loops
`delete_exported_file(item_id)` per row in one transaction).
Successful deletes clear `to_delete` on the rows (now gone),
the toolbar count refreshes to 0, the grid removes the cells
(or repaints them empty if the lineage row stayed for some other
reason — there shouldn't be one).

### B. Rating persistence

Each rating change is one gateway call (`set_lineage_stars` etc.)
which lands one `UPDATE lineage SET … WHERE export_relpath = ?`.
No batching, no debouncing — the action is the persistence.
Ctrl+Z replays the previous value through the same setter; the
undo stack lives on the EditorPage instance and survives a key
sequence but not a viewer close.

### C. Edge cases

- **A version whose source item was deleted between open and
  rating** — `set_lineage_*` returns silently (the UPDATE matches
  0 rows). UI shows the change immediately; the next refresh
  drops the cell.
- **`to_delete = 1` + lineage row stays Pick (picked)** — that's
  fine; the user is saying "I want this exported file off disk
  but I'm not unwilling to re-export later". When the delete
  commits, the lineage row goes too; intent state goes with it.
- **A version in the middle of being marked for deletion when the
  user opens it for review** — works fine; the badge shows, the
  user can flip it off or rate it, neither blocks the other.
- **Two source items both pointing at the same export_relpath**
  — impossible; the `lineage.export_relpath` is UNIQUE (charter
  rule, schema invariant). The four ratings are keyed by
  export_relpath so they're always 1:1.

## 8. What this spec does NOT do

- Doesn't ship rating UI on the Pick / Edit / Export day grids
  (spec/32 §3.1's item-level ratings stay where they are; this
  spec leaves those undisturbed).
- Doesn't auto-include flagged photos in any Cut. The flag is
  curatorial; a future spec (a "Portfolio" cross-event Cut surface)
  could read it.
- Doesn't expose lineage ratings via `global_items_sync.py`. Cross-
  event Dynamic Collections can't filter on lineage ratings yet;
  that's a deliberate scope omission.
- Doesn't grandfather existing ratings from `item.extras_json` into
  the new lineage columns. Today no Pick-phase rating UI exists, so
  the question is moot; a future migration can copy them if needed.

## 9. Implementation plan — session breakdown

This spec is large enough to need ≥ 2 sessions; the natural seams:

### Session A — schema + gateway + the grid surface

1. Add the four `lineage` columns + indexes + migration.
2. Wire the six gateway mutators + readers + `LineageRatings`.
3. Implement the three new Thumb visual slots (colour-label
   strip, star chip, flag glyph) and the "To be Deleted" badge.
4. Rebuild the Exported Collection grid in share_cuts_page:
   the toolbar, the click grammar, the versions cluster
   surfacing, the filter dropdown, the Delete confirm dialog.
5. **Don't** open the review-mode editor yet — center-click is a
   no-op or a placeholder.
6. Tests: gateway mutators round-trip; the grid renders the
   chrome correctly per rating state; the click grammar lands
   on the right verb; the Delete confirm dialog runs the right
   loop.

### Session B — the review-mode Editor

1. Add the `review_mode` kwarg + the chrome-gating audit.
2. Build the four rating widgets in the classification header.
3. Wire the keyboard map (the keys block of §5.4).
4. Wire ←/→ next/prev across the visible list.
5. Re-point the pixel source to `Exported Media/<relpath>` when
   in review mode.
6. Center-click on a cell now opens this editor for that
   lineage row.
7. Tests: review-mode editor opens against a lineage row;
   creative chrome is hidden; the rating header reads / writes
   the right gateway calls; the keys map to the right setters;
   prev / next cycles correctly.

If context budget allows, both sessions can share one wrap-up:
PROGRESS.md banner + a small handover doc.

## 10. Migration / back-compat

- The four new columns default to NULL / 0; existing lineage
  rows read as unrated, unflagged, not-marked-for-deletion. No
  data migration needed.
- The SCHEMA_VERSION bump triggers the four `ALTER TABLE` +
  partial indexes on next event open. Migration is idempotent
  (existing column check).
- spec/32 §3.1's item-level ratings are not retired; they
  remain available for future use on other surfaces. This spec
  uses lineage-level columns specifically because the
  Exported Collection is per-version.

## 11. Locked decisions reference

| Decision | §  | Rationale |
|---|---|---|
| Per-version (not per-item) ratings | §2.1 | One source item, multiple shipped renders, independent quality. |
| Style stays per-item | §2.2 | Style describes the *shot*, not the rendering. |
| Separate `to_delete` flag (not reuse pick/skip intent) | §0, §2.1 | Closed-event surface; delete intent is about the bytes on disk, not the source media. |
| Batch confirm for deletion (not immediate unlink) | §7A | The Exported Collection is a closing-out activity; immediate is too fast. |
| Editor reuse with `review_mode` (not new viewer) | §5 | Co-locates Style classifier; reuses the photo viewport, F10 truth key, fullscreen. |
| Exported file bytes (not develop pipeline output) | §5.1 | Judging what shipped, not what would be re-emitted. |
| Border-click toggles `to_delete`; center-click opens viewer | §4.3 | Two-zone grammar (BUGS.md B-006); existing pattern from spec/89. |
| No P/X/Space in review mode | §5.4 | The locked Pick grammar (spec/63 §4) belongs on the Pick day grid; this surface has no Pick decision. |
| Versions cluster covers don't show ratings | §6 | Aggregates are misleading; user drills in. |
| Compare button reused verbatim | §6 | Spec/63 §4 follow-up dialog is generic. |

---

Nelson 2026-06-30 — design session captured. Spec lands; code
follows in two sessions (§9).
