# Session handover — 2026-06-14 (evening, QS wire-up completion)

Follow-up to [HANDOVER-2026-06-14.md](HANDOVER-2026-06-14.md). That doc
captured the QS pivot (`bf3e5e9`) shipping without final Nelson eyeball;
this session closed the loops Nelson found, ran the standalone +
wizard QS flows end-to-end on real assets, and got both signed off.

## Commits in this session

| SHA       | Title                                                                       | Eyeball |
|-----------|-----------------------------------------------------------------------------|---------|
| `1a0951c` | Quick Sweep: all-green default + real thumbnails in the days grid           | ✅      |
| `d222fe9` | Wizard QS: route through DaysList → DaysGrid → viewer                       | ✅      |
| `f2f2a8e` | Quick Sweep day grid: clusters + chronological order + Clusters label       | ✅      |

All three shipped on `main`. The branch is now 37 commits ahead of
`origin/main` (the prior handover left it at 34).

## What this session fixed — by symptom

### 1 · The grid was empty (`1a0951c`)

`DaysGridPage._decode_thumbnail` short-circuited on `self._eg is None`,
so the paths-mode standalone QS never reached a decoder — the user got
gradient placeholders. Fix: the gateway-backed branches now run only
when an event is open; the paths-mode tail uses
`load_pixmap(path, _TILE_SIZE)` so JPEGs DCT-downscale at the tile size
and a 24-MP source thumbs in ~10–20 ms. Videos in paths mode return
`None` (no event `.cache/` to materialise a frame thumb; the `Thumb`
widget keeps painting its placeholder).

### 2 · "All-green by default" wasn't rendering (`1a0951c`)

The DaysList bars + DaysGrid cell borders ignored
`quick_sweep_default_state`. Two new MainWindow helpers
(`_qs_default_legacy_state`, `_qs_default_phase_state`) read
`default_state_for(settings, "quick_sweep")` once — mirroring how Edit
pulls its default via `default_state_for(settings, "edit")` — and
translate it into either the `core.cull_state` legacy values the
standalone ledger uses (`kept`/`discarded`) or the `mira.picked.status`
wire values the gateway-fed grid expects (`picked`/`skipped`).

- **Standalone path**: `_qs_build_day_snapshots` counts picked/skipped
  from the session ledger (Compare folds into picked, matching the QS
  save-time contract). `_qs_build_grid_items` falls back to the
  resolved default for items the user hasn't decided yet.
- **Per-event path**: `_qs_apply_default_to_snapshots` folds each day's
  undecided count (`items − picked − skipped`) into the QS-default
  side AFTER the gateway-fed snapshot build so the bars read all-green
  on entry to a fresh QS session over an event with no pick decisions
  yet. The QS default is also plumbed through to
  `DaysGridPage.open_for_day` via a new `default_state` parameter so
  the cell borders use the QS default for undecided items instead of
  the pick-phase default.

### 3 · Wizard QS skipped the day list / day grid (`d222fe9`)

The Collect ingest-mode gate's "Quick Sweep first…" button (and the
backfill wizard's pre-import QS) loaded SourceItems straight into a
single flat `QuickSweepPage` modal. A multi-day scan walked every
photo linearly. The prior handover flagged this as a known follow-up.

`_run_quick_sweep_first` now hosts the same 3-page route the
standalone flow uses, inside a modal QDialog so the wizard's
synchronous return contract holds (`None` = cancel, `set` = kept
paths):

- `QStackedWidget` cycles `DaysListsPage → DaysGridPage →
  QuickSweepPage` (paths mode — fresh instances, no gateway, no
  main-window page-stack involvement; local closures own navigation).
- The shared QS session dict is borrowed for the modal's lifetime
  (`mode = "wizard"`) so the same helpers produce the all-green-by-
  default bars + cells as standalone. Prior session restored in
  `finally`.
- **DaysList Back → finalize confirm** ("Import and finish" vs "Stay
  in Quick Sweep") → return the kept set; the wizard's ingest pipeline
  uses it as the keep-only filter (`_run_collect_copy_all`).
- **DaysGrid Back** → re-render the days list (reads updated bars from
  the ledger). **Viewer Save / Cancel** → re-render the day grid with
  updated cell colours.
- **Esc** on the host dialog → `QDialog.reject()` → kept stays `None`
  → caller treats as cancel.

### 4 · "Buckets" was stale terminology + count was off (`f2f2a8e`)

`DayRow`'s meta column on `DaysListsPage` renamed Buckets → Clusters.
Both snapshot builders (gateway + paths) now count only
`REAL_CLUSTER_KINDS` (`burst` / `focus_bracket` / `exposure_bracket` /
`repeat`) so the number matches what the day grid renders as cluster
covers. Individual / moment / video buckets flatten to per-item cells,
so they were never "clusters" in the user-visible sense.

### 5 · No cluster covers in the paths-mode day grid (`f2f2a8e`)

The standalone + wizard grid showed a flat list with no cluster
icons / count chips. Refactor:

- `_qs_build_grid_items(items)` → `_qs_build_grid_items(day_number)`.
  Walks the session's `PickDay.buckets`, collapses each
  `REAL_CLUSTER_KINDS` bucket to a single cluster-cover `GridItem`
  carrying `cluster_type` (burst/focus/exposure/repeated icons),
  `cluster_count`, mixed-cluster `cluster_split` chip, and a
  `_cull_cluster` reference so `DaysGridPage`'s existing drill-in
  walks the members.
- `DaysGridPage` gets two new paths-mode callbacks
  (`set_paths_mode_callbacks(state_lookup, day_rebuild)`):
  `state_lookup` colours sub-grid member cells from the QS ledger;
  `day_rebuild` is called by `_close_cluster` so cluster covers
  repaint with their fresh aggregate state after the user marks
  members inside.
- `_open_cluster` paths-mode branch: looks up each member's thumb
  state via the host callback; the bucket-browsed mark is a no-op
  (nothing to write).

### 6 · Grid order didn't match viewer order (`f2f2a8e`)

The viewer sorted items by timestamp internally; the grid walked them
in scan order. Click the first grid cell → viewer opened on a
different item. Fix:

- `items_by_day` is now sorted chronologically at build time
  (standalone + wizard) using the same ISO sort key the viewer uses
  (`(timestamp is None, timestamp.isoformat(), path.name)`).
- The grid sorts cluster covers + flat cells together by the cell's
  anchor capture time (clusters anchor on their earliest member).
  Same ISO-string sort key. Grid and viewer walk the day in the same
  order — no more silent re-shuffle on entry.

## Verified on real assets

Tested end-to-end with Nelson on the live app:

1. **Standalone QS** — folder picker → days list (all-green Picked bar,
   "Clusters · N" badge) → click a day → grid with real photo
   thumbnails + green borders → click a cell → viewer at the right
   item → P/X cycles state → Back to grid → Back to days list →
   finalize → `copy_kept` to dest.
2. **Wizard QS** — backfill / Collect gate's "Quick Sweep first…" →
   same DaysList → DaysGrid → viewer route inside the modal →
   finalize → kept set returned to ingest pipeline → real copy ran.
   254 photos + 7 WhatsApp images recorded; classify pass cleared all
   256 (210 classified, 46 inherited).

Per-event QS standalone path NOT eyeball-tested in this session
(per-event write-back is still deferred per the prior handover; the
bars now correctly fold the QS default but no decisions persist).

## Tests + verification

- `verify.bat` baseline (prior handover): 2796 / 314 skipped + 20 / 1.
- After this session: **2796 / 314 skipped + 20 / 1.** Unchanged.
- No new test files added or removed.

## Smokes (`scripts/smoke_quick_sweep_*.png`)

Updated in `1a0951c` + `f2f2a8e`:

- `smoke_quick_sweep_days_lists_{dark,light}.png` — now reads
  "Clusters · 3 · Items · 54" (smoke source has all-moment buckets so
  it labels 3; real-app code correctly reports 0 real clusters for
  the same source — the smoke just doesn't go through
  `_qs_build_day_snapshots`).
- `smoke_quick_sweep_days_grid_{dark,light}.png` — file size jumped
  ~50 KB → ~1 MB confirming real photo thumbnails painted (vs the
  gradient placeholders the pre-`1a0951c` smoke captured).
- `smoke_quick_sweep_viewer_{dark,light}.png` — unchanged structure;
  refresh capture sized up because the viewer's surface now decodes
  the source.

The smoke script (`scripts/smoke_surface_quick_sweep.py`) was NOT
updated to exercise the cluster path or the chronological sort —
those live in `_qs_build_grid_items`, which the smoke bypasses with
its own `build_grid_items` closure. A follow-up should port the smoke
onto the production helpers so the screenshot reflects the real app.

## Observations + notes (not bugs, but worth flagging)

### WhatsApp images get "fallback identity" at Collect

The post-QS Collect copy logged seven lines of the shape:

```
Collect: recording WhatsApp Image 2026-01-31 at 17.44.56.jpeg with
  fallback identity (camera=_unknown, time=2026-04-07T22:12:21.772331)
```

The mechanism — [main_window.py:3578-3614](mira/ui/shell/main_window.py:3578)
— is the existing **EXIF-less first-class** fallback (Nelson
2026-06-11, the renamed bird clips):

- No EXIF camera → `camera_id = "_unknown"` (a sentinel camera row
  gets upserted once per Collect).
- No `DateTimeOriginal` → `raw_dt = dest.stat().st_mtime`,
  `tz_source = "none"`, day_number stays `None` (lands in the
  undated bucket).

WhatsApp strips EXIF on download, so these all fell back. The
timestamps cluster around `2026-04-07T22:12:21` because that's when
those files first landed on disk (mtime preserved through the copy);
not "now". The filenames embed the REAL capture date
(`2026-01-31 at 17.44.56`) but the fallback doesn't parse that.

**Optional follow-up** (Nelson left this as a "want me to?"): add a
WhatsApp-aware filename-parser fallback that runs before the mtime
fallback. Pattern is well-defined: `^WhatsApp Image YYYY-MM-DD at
HH\.MM\.SS(?: \(\d+\))?\.jpeg$`. The camera_id would still fall to
`_unknown` (or a new `_whatsapp` sentinel), but the timestamp +
day grouping would be honest. NOT taken in this session.

### Per-event QS write-back still deferred

The prior handover flagged this; nothing changed here. Per-event QS
borrows the pick-phase ledger; user marks land in the in-memory QS
session dict, not in `phase_state`. `_on_quick_sweep_saved` logs the
kept count and that's it. Design + implementation is its own session.

### Smoke ↔ real-app divergence

The standalone smoke (`scripts/smoke_surface_quick_sweep.py`) has its
own paths-mode page builders that diverged from the real helpers:

- Its `build_snapshots` hardcodes `picked = items_count, skipped = 0`
  instead of counting from the ledger.
- Its `build_grid_items` walks `day_items` in scan order, doesn't
  emit cluster covers.
- Its `items_by_day` isn't sorted chronologically.

The screenshots still LOOK right because the smoke's all-green +
flat-grid + scan-order happen to match what `_qs_build_grid_items`
produces on a no-cluster source. But the smoke isn't load-bearing.
Worth porting onto the production helpers next pass.

### Lock-file question from the prior handover

The `settings.rebuild.json` empty-paths investigation flagged at the
end of the previous handover did NOT recur this session. Nothing
new to add — the forensics pointers in `HANDOVER-2026-06-14.md`
remain the right starting point if it shows up again.

## Files touched this session

```
M  mira/ui/pages/days_grid_page.py                  (+126/−63 across 3 commits)
M  mira/ui/pages/days_lists_page.py                 (+6/−6, label rename)
M  mira/ui/shell/main_window.py                     (+529/−95 across 3 commits)
M  scripts/smoke_quick_sweep_days_grid_{dark,light}.png   (real photos painted)
M  scripts/smoke_quick_sweep_days_lists_{dark,light}.png  (Clusters label)
M  scripts/smoke_quick_sweep_viewer_{dark,light}.png      (resized capture)
A  HANDOVER-2026-06-14-evening.md                   (this file)
```

Still unstaged (Nelson's pre-session edits, untouched here):

```
M  spec/66-collect-pick-edit-export.md
M  spec/70-new-ui-completion-plan.md
?? scripts/smoke_icons_{dark,light}.png
```

## Recommended next steps

1. **WhatsApp filename-parser fallback** — if Nelson wants it (it was
   left as an explicit "want me to?" at the end of the chat). Lifts the
   seven undated WhatsApp images out of the `_no_timestamp` bucket and
   into the day Nelson actually photographed them on.
2. **Per-event QS write-back** — still the next QS milestone. Design
   what `saved` means in the per-event mode (a separate
   `phase='quick_sweep'` ledger? Marking pick decisions directly?).
3. **Port `scripts/smoke_surface_quick_sweep.py`** onto the production
   `_qs_build_day_snapshots` / `_qs_build_grid_items` / `items_by_day`
   sort so the screenshot reflects the real app (and exercises the
   cluster cover path on a source that has bursts).
4. **Smoke + tests for the wizard QS modal** — none exist yet. The
   construction was sanity-checked inline this session but there's no
   permanent assertion that the 3-page modal wires up. A small test
   that fakes `QDialog.exec` to return immediately + asserts the
   session dict is set + torn down would be cheap insurance.
5. **`test_quick_sweep_clusters.py`** still has 33 skipped tests
   (deferred at `bf3e5e9`). Now that cluster covers + drill-in work in
   paths mode, several of those might be portable to a
   `test_days_grid_page_paths_mode.py` suite.

— Claude (Opus 4.7, 1M)
