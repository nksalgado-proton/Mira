# 132 — Closed-event carousel: exported photos only

**Status: PROPOSED (Nelson 2026-06-23). The closed-event tile's PhotoCycler
(`_sample_pixmap_paths` in `mira/ui/pages/_event_card_data.py`) sources its
photos with a 3-tier fallback: (1) exported keepers, (2) **picked** photos,
(3) **any capture**. So a closed event with no exports (or a transient
`exported_files()` failure) cycles photos the user never chose to export —
defeating the point of a closed event's "highlight reel." A closed event's
carousel must show **only exported photos**. Drop the picked + any-capture
fallbacks; when there are no exported photos, show a neutral placeholder, not
someone's un-chosen captures. Performance is a non-issue: render via the
already-populated export thumb cache, and if full frames must load, just
cycle at a slower pace — never substitute a faster non-exported source.
One-function change. No data-model change.**

## 1. The bug

`_sample_pixmap_paths` returns the first non-empty of:
1. `eg.exported_files()` — the `Exported Media/` survivors (correct).
2. picked photos (green but not exported).
3. any captured photo.

Tiers 2–3 mean the closed card can display frames the user **explicitly did
not export** — the opposite of what a closed/Share view should show. The
fallback was added so a freshly-closed event with no decisions still had
*something* to cycle, but that trade is wrong: better to show nothing than to
parade un-chosen photos.

## 2. The fix

- For the closed tile, `_sample_pixmap_paths` returns **only** the exported
  set (tier 1). Remove the picked (tier 2) and any-capture (tier 3)
  fallbacks.
- **Empty exported → return empty**, and the closed tile renders a neutral
  static placeholder / cover (no cycling) instead of falling back to
  captures. (A closed event with zero exports is an edge case — surfacing
  "nothing exported" honestly is better than showing the wrong photos.)
- **Performance:** prefer the **export thumb cache**
  (`core.photo_thumb_cache`, already populated via `queue_export_thumb` at
  export time) so cycling stays cheap. If a frame must decode from the full
  `Exported Media/` JPEG, accept a **slower cycle interval** rather than
  reaching for a faster non-exported source — correctness over speed, per the
  user's call.

## 3. Acceptance

- A closed event cycles **only** its exported photos; no picked-but-not-
  exported or arbitrary captured frame ever appears.
- A closed event with no exports shows a neutral placeholder, not captures.
- Cycling uses cached export thumbs where available; a heavier full-frame
  load slows the pace but never swaps in a non-exported image.
- The open-event tile (donut grid) is unaffected.

## 4. Tests

- `tests/test_closed_carousel_exported_only.py` — with exports present,
  `_sample_pixmap_paths` returns exactly the `exported_files()` set; with
  picked-but-not-exported photos and **no** exports, it returns **empty**
  (not the picked set); with only captures, **empty**; an `exported_files()`
  exception returns empty (never falls through to captures).
- Regress the closed-tile render with an empty source → placeholder, not a
  crash.
