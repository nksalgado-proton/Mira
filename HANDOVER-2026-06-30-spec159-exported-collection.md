# Handover — 2026-06-30 — spec/159 Exported Collection (review + classify)

Everything below is **committed + pushed to `main`**. The 41-test
spec/159 suite is green (cycles synchronously, no live machine
state).

## Recent commits (chronological)

| Commit | Topic |
| --- | --- |
| `cb05ce4` | spec/159 design captured — [spec/159-exported-collection-review-and-classify.md](spec/159-exported-collection-review-and-classify.md) |
| `83b33da` | spec/159 **Session A** — schema + gateway + Thumb chrome + grid rebuild + 41 tests |
| `d3daa57` | spec/159 follow-up — pill-shaped "Marked for deletion" badge + ReviewMediaDialog wired to center-click |
| `ec60289` | Review dialog: ★/☆ glyphs + loud active states on every control |

Earlier in the same session (left in here as context for the next agent):

| Commit | Topic |
| --- | --- |
| `eb871e9`, `a15e4ef`, `317bce1`, `8c7d842`, `10e7a18` | Day-grid Compare button — fires on ≥2 cells in Compare state; grid layout in the dialog; tiles strip title + state chip |
| `938d3da` | Video play-triangle badge restored on uncloistered day-grid cells |
| `3fba237`, `380a018`, `35975b4`, `2f50da5`, `684a480`, `a123ff9`, `4f53b0c` | spec/155 polish — pastel separator palette, per-day seed for video sep bg, video map not baked into card JPG, grid paints video first-frame + caption overlay, etc. |

## What spec/159 Session A landed

Captured in [spec/159-exported-collection-review-and-classify.md](spec/159-exported-collection-review-and-classify.md);
implementation arc:

**Schema (v22 → v23)** — four new columns on `lineage`:
- `stars` (1..5 / NULL)
- `color_label` (LRC red/yellow/green/blue/purple / NULL)
- `flag` (0/1)
- `to_delete` (0/1)

Four partial indexes; migration is purely additive; the
`m.Lineage` dataclass picks them up with safe defaults.

**Gateway** — six new entry points:
- `set_lineage_stars` / `set_lineage_color_label` /
  `set_lineage_flag` / `set_lineage_to_delete` (mutators with
  input validation, funnel through `_touch()`).
- `lineage_ratings(rel)` → one-query `LineageRatings` NamedTuple.
- `exported_marked_for_deletion()` → list of every
  `Exported Media/` row with `to_delete=1`.
- `delete_marked_exported_files()` → batch commit; delegates each
  row to `delete_exported_file_by_relpath` so the file unlink +
  lineage drop + `edit_exported` flip + cut_member cascade stays
  in one path. Returns count deleted.

**Thumb chrome** (`mira/ui/design/thumbs.py`):
- Constructor kwargs + setters: `stars`, `color_label`, `flag`,
  `to_delete`.
- Four paint methods:
  - `_paint_color_label_strip` — 4 px top edge, LRC hue.
  - `_paint_star_chip` — "★N" bottom-right; suppressed on cluster
    covers + when `to_delete` is on.
  - `_paint_flag_glyph` — painted amber flag top-left.
  - `_paint_to_delete_badge` — **pill-shaped** (round corners,
    inset from cell edges), dark-red bg, white "DELETE" text. The
    v1 full-width strip was retired (`d3daa57`) after Nelson
    eyeball.
- `ThumbGridItem` carries the same four fields; `_GridCell`
  threads them through both `__init__` + `apply_item`.

**Exported Collection grid** (`mira/ui/shared/dc_detail_page.py`):
- Selection state moved from in-memory `_selected: set[str]` to
  the persistent `lineage.to_delete` column. Survives Back +
  reopen.
- Two-zone click grammar:
  - **Border-click** toggles `to_delete` via
    `set_lineage_to_delete`.
  - **Center-click** emits `review_requested` AND opens the
    new ReviewMediaDialog at the clicked index.
- Toolbar: "⌫ Delete N marked…" (visible when N ≥ 1) +
  "Clear marks". Delete commits via
  `delete_marked_exported_files`; Clear releases `to_delete` on
  every visible row.
- Cell chrome reads ratings off the `Lineage` row so the grid
  paints the full spec/159 visual catalogue at refresh time.

**ReviewMediaDialog**
(`mira/ui/exported/review_dialog.py` — NEW):
- Wraps the spec/63 `PhotoViewport` so the user sees the actual
  exported file bytes at full quality.
- Top chrome row: star buttons (★/☆ glyph swap, amber when
  filled), 5 colour-label dots (3 px white ring + ✓ on the
  active), flag toggle (amber bg when ON, labelled "⚑ Flagged"),
  "Mark for deletion" toggle (red bg when ON, labelled
  "⌫ Marked for deletion").
- Each change writes through the matching gateway mutator and
  updates the cached `Lineage` row + the grid chrome on close.
- Keyboard map: `1..5` stars, `Shift+1..5` colour, `0` clear
  stars, `Shift+0` clear colour, `K` flag, `D`/`Delete` mark,
  `←/→` prev/next, `F`/`F11` fullscreen, `Esc` close.

**Tests** (41 new):
- `tests/test_spec159_lineage_ratings.py` (31) — gateway round-
  trip + validation + the batch helpers.
- `tests/test_spec159_dc_detail_page.py` (10) — border-click
  toggles `to_delete`; toolbar count + label update;
  clear-marks; center-click emits `review_requested` without
  touching `to_delete`; delete confirm fires the batch helper.

## What's still open

### From spec/159 §4 / §9 — NOT yet implemented

1. **Versions-cluster surfacing on the grid.** Spec/89 Slice 5's
   `versions:<item_id>` cluster (the synthetic bucket that groups
   the lineage rows of one source item) is NOT surfaced on the
   Exported Collection grid yet. Today the grid is a flat list
   over `exported_files_all()`. Spec/159 §4.4 calls for the
   cluster cover to appear with the cluster count chip + a
   "N/M to delete" sub-chip when any inner version is marked.
2. **Filter dropdown** (§4.5) — single QToolButton on the
   toolbar with Min Stars / Colour label (multi) / Flag /
   "Hide marked-for-deletion" toggles. Session-local.
3. **Compare button reuse** (§6) — the spec/63 §4 follow-up
   day-grid Compare button is supposed to carry over verbatim.
   Not yet wired on this surface.
4. **"To be Deleted" badge polish** — `d3daa57` swapped to a pill
   but the visual still wants Nelson's eyeball.

### spec/159 §5 — Editor reuse pivot question

The spec's preferred shape was Editor reuse with a `review_mode`
flag (`mira/ui/edited/editor_page.py`) — hide creative chrome,
surface classification widgets in the existing metadata header.

I shipped a **standalone ReviewMediaDialog** instead. Reasoning:

- Pro: fast to land, surfaces every rating control immediately,
  no Editor chrome-gating audit (every panel needs an
  `if review_mode: hide`), no impact on the Editor's
  per-photo-state machinery.
- Con: doesn't co-locate Style classification — the Editor's
  classifier chip stays in the Editor. The spec called out style-
  co-location as a virtue.

**Open call for the next session**: keep ReviewMediaDialog and
treat the spec/159 §5 Editor-reuse text as superseded, OR promote
the `review_requested` signal to open the Editor (with a real
`review_mode`) and retire ReviewMediaDialog. Either path is
viable; the gateway / schema / Thumb-chrome work is reusable
verbatim.

### Open polish items Nelson called out

- Stars / selection state v2 already landed (`ec60289`) — needs
  Nelson eyeball.
- The grid Compare button (from earlier the same session,
  `eb871e9` lineage) DOES still apply to this surface even though
  not yet wired here. Tests for it are in
  `tests/test_compare_versions_dialog.py` and
  `tests/test_days_grid_export_mode.py`.

## Sources of truth

- [spec/159-exported-collection-review-and-classify.md](spec/159-exported-collection-review-and-classify.md)
  — the spec; §11 is the locked-decisions table; §9 is the
  session split.
- [CLAUDE.md](CLAUDE.md) — invariants, vocabulary, the locked
  keyboard map.
- [spec/00-charter.md](spec/00-charter.md) + [spec/03-schema.md](spec/03-schema.md)
  — anchors.

## File map for the next agent

| File | Role in spec/159 |
|---|---|
| `mira/store/schema.py` | v23 migration + the four lineage columns + four partial indexes |
| `mira/store/models.py` | `Lineage` dataclass with the four new fields |
| `mira/gateway/event_gateway.py` | `LineageRatings` NamedTuple + the six setters/readers/batch helpers |
| `mira/ui/design/thumbs.py` | The four new paint methods + setters on `Thumb` |
| `mira/ui/design/thumb_grid.py` | `ThumbGridItem` carries the four new fields; `_GridCell` threads them |
| `mira/ui/shared/dc_detail_page.py` | The grid surface — border-click toggles `to_delete`, center-click opens ReviewMediaDialog, toolbar wires the batch delete |
| `mira/ui/exported/review_dialog.py` | The full review viewer (NEW) |
| `tests/test_spec159_lineage_ratings.py` | 31 gateway tests |
| `tests/test_spec159_dc_detail_page.py` | 10 surface tests |

## Quick eyeball protocol

1. Restart Mira. Open a closed event with shipped files → Cut
   page → "Open" on the #exported card.
2. **Grid border-click** a few cells → "Marked for deletion" pill
   appears + toolbar shows "⌫ Delete N marked…".
3. **Grid center-click** any cell → ReviewMediaDialog opens with
   the photo at full quality.
4. **Inside the dialog**: tap `3` → 3 stars; tap `Shift+3` → green
   colour label; tap `K` → flag toggles; tap `D` → "Marked for
   deletion" toggles. Both should reflect on the grid after
   closing.
5. Toolbar "⌫ Delete N marked…" → confirm dialog → files unlink
   under `Exported Media/` and the rows drop.

If anything reads wrong, the visual state is set per control by
`_refresh_chrome` in `review_dialog.py` (lines around 222-310).

## Test surface

The 41 spec/159 tests run in well under a second locally; full
suite hasn't been run since the start of the session arc. The
nearest neighbouring suite that touches the same surface
(`tests/test_pool_delete_cascade.py`) is green.
