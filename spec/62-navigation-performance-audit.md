# 62 — Item-Navigation Performance Audit

**Status: AUDIT — findings + options, awaiting Nelson's direction.
No code changed.** (2026-06-12 bug session; Nelson: "the app feels
sluggish moving item-to-item… we probably need a full audit before
doing anything.")

The bar, named by Nelson: **hold the arrow key and photos flip fast**
— FastStone-style. Quick Sweep feels best today; Picker and Edit feel
sluggish.

---

## 1. Measured reality (probe on the real library, 2026-06-12)

`_probe_nav_costs.py` (repo root, re-runnable) timed every pipeline
stage exactly as the app calls it, on real files: 25 MP G9M2 JPEGs
(~10 MB) and G9 RW2s (~23 MB). Warm-run numbers:

| Stage | ms | Used by |
|---|---:|---|
| `load_pixmap(JPEG)` full decode (5776×3248) | **145** | Picker worker, Quick Sweep (UI thread!) |
| `load_pixmap(JPEG, 2560×1440)` DCT-scaled | 93 | nobody (implemented, unused) |
| `load_pixmap(RAW)` embedded thumb (1920×1440) | 17 | Picker worker |
| native pixmap `.scaled(fit, Smooth)` | **2** | display path (cheap — NOT a problem) |
| QPixmap→numpy native conversion | 14 | Picker sharpness first-view (UI thread) |
| `sharpness_score` native array | 9 | same |
| `decode_image(JPEG)` (Edit) | **130–146** | Edit, UI thread |
| `decode_image(RAW)` full demosaic (Edit) | **~615** | Edit, UI thread |
| `_downsample(full, 1280)` (Edit) | **111** | Edit, UI thread — as expensive as the JPEG decode! |
| `compute_auto_params(preview)` | 15 | Edit, UI thread |
| tone render (`apply_params` on 1280 preview) | 10 | Edit, UI thread |
| rawpy half-res demosaic | ~233 | peaking / thumb-less RAW fallback |

And on real **exported** files (Cut surfaces; 3618×2710, ~1–3 MB —
`_probe_cut_nav.py`):

| Stage | ms | Used by |
|---|---:|---|
| `load_pixmap(export)` full decode | **75–83** | Cut single view, UI thread, per step |
| `load_pixmap(export, 280×280)` grid thumb | **~24** | Cut grid fill, UI thread, ×4 per 20 ms tick |
| `load_pixmap(export, 2560×1440)` | 46 | candidate decode-to-target |

Windows key auto-repeat delivers ~30 events/second. FastStone feel
needs an effective **<50 ms per item**; today's effective costs are
~290 ms (Edit JPEG), ~770 ms (Edit RAW), ~145 ms decode-bound
elsewhere.

### 1.1 AFTER the perf tiers (slices 7+8 landed, re-probed 2026-06-12)

`_probe_after_tiers.py` (repo root, re-runnable; caches to a temp
root — the library untouched) re-timed the same stages on the SAME
real files through the new tiers:

| Stage | before | **after** | |
|---|---:|---:|---|
| JPEG browse decode @2560 (Picker/QS/Cut single) | 93–145 | **18–30** | proxy serve — under the 33 ms key-repeat interval: the FastStone bar is MET for browsing |
| RAW browse decode | 17 (embedded-thumb extract) | **12.5** | proxy serve; also skips rawpy container parsing |
| Cut grid cell fill | ~24 | **0.4–0.5** | export-thumb serve — better than the predicted ~2 ms |
| JPEG proxy build (background, one-time) | — | 232–282 | ~0.5–1.2 MB each on these files |
| RAW proxy build (background, one-time) | — | 21–116 | embedded-preview re-encode |
| export thumb build (background, at export) | — | ~25 | |

Sidecar native dims verified on the wire: the proxy serve reports the
ORIGINAL's 5776×3248, not the proxy's. **Edit's numbers move with
spec/63 6b** (its pixel model is the remaining unmigrated surface);
the spec/63 §7.10 probe line is satisfied for the browse + grid
paths.

## 2. Why each surface feels the way it does

**Edit (worst).** The whole pipeline is synchronous on the UI thread
per keypress: `decode_image` (130–615 ms) + `_downsample` (111 ms) +
auto-params + tone + two-to-three gateway reads + a gateway write
(`edit_page.py:499`, `_load_and_render_item`). No PhotoCache, no
prefetch, no worker. Held arrow: the `_busy_flag` gate silently drops
repeats, so the UI freezes ~300–800 ms per step and skips. The wait
cursor is honest, but it's honesty about a freeze.

**Picker (sluggish despite the right architecture).** Async worker +
256 px thumb placeholder + 150 ms-settle predecode of N+1/N+2/N−1 all
exist (`photo_cache.py`, `pick_photo_surface.py`). Three defects:

1. **Priority-0 flood.** Every nav queues a never-dropped priority-0
   native decode on the SINGLE worker (`photo_cache.py:159` drops
   only priority>0). Holding the arrow for 1 s queues ~30 × 145 ms ≈
   4 s of backlog — and the queue is FIFO within priority, so the
   photo you're actually looking at decodes LAST. You stare at a
   256 px blur until the backlog drains.
2. **Native-res decode always.** The DCT-scaled path (35 % faster,
   and far more with smaller targets) sits unused because
   display-size decode broke the box-zoom 1:1 indicator
   (recorded at `photo_cache.py:162–168`). Any fix must carry native
   dimensions alongside a scaled pixmap, not lose them.
3. **First-view sharpness on the UI thread** (~23 ms) — minor cost,
   but it scores `canvas._source_pixmap` at whatever resolution
   happens to be displayed, so fast nav can persist a sharpness score
   computed on the 256 px THUMB. Perf footnote, **data-quality bug**
   (cull ranking pollution).

Per-nav extras (3–4 QSS repolishes, EXIF cache hits, overlays) are
1–2 ms each — real but not the story.

**Cut session picker (Share, audited 2026-06-12 on Nelson's ask).**
The youngest surface has the least engine integration — none at all
(`cut_session_page.py`):

- **Single view** (`_SingleView.show_file`, line 262):
  `load_pixmap(abs_path)` synchronous on the UI thread, **no target
  size, no cache of any kind, no placeholder, no prefetch, no
  coalescing**. ~75–83 ms frozen per arrow step on real exports — and
  stepping BACK to a photo just viewed re-decodes it from scratch
  (worse than Quick Sweep, which at least keeps a session dict). The
  grid thumb already in `self._thumbs` is not even used as a
  placeholder. Videos cost nothing (text stand-in — the known
  playback gap).
- **Day grid fill**: thumbs decode fresh from the full exported JPEGs
  on the UI thread, 4 per 20 ms tick (~24 ms each = **~96 ms of work
  per 20 ms tick**) — the grid visibly jams while filling, precisely
  when the user starts mousing over it. Root cause: exported files
  have no disk-thumb tier — the Pick grids read 256 px thumbs from
  `.cache/thumbs/photos/` (~2 ms), but that cache is keyed off
  ORIGINALS; Cut surfaces re-derive thumbs per page instance,
  in-memory only (`self._thumbs`, page-lifetime).
- **Decisions are fine.** Pick/Skip/undo land on the in-memory
  session ledger (commit-once model), budget-strip refresh walks
  in-memory totals, repolish is change-guarded. Pure navigation and
  grid fill are the costs, not the ledger.
- Same surface family, same fixes: PhotoCache routing (thumb
  placeholder + async full decode + prefetch + coalescing) and a
  disk-thumb tier for exported files (or admit exports into the
  existing thumb cache keyed by their own sha256).

**Quick Sweep (best, still decode-bound).** Stripped chrome and zero
per-nav DB work, but `_load_pixmap(path)` runs synchronously on the
UI thread with NO target size (`quick_sweep_page.py:1191`) — ~145 ms
per new JPEG. It feels best because it shows the SHARP image when it
lands (no blur phase) and revisits are instant (session dict). Two
flags: the docstring's "no decode lag" claim is not true today, and
`_thumb_pixmap_cache` is misnamed — it stores **unbounded native-res
pixmaps** (~75 MB each; a long sweep can grow to GBs).

## 3. The fix space

### Stage 1 — engine fixes (no new disk state, no new UX)

1. **Coalesce navigation.** While a held key repeats, only the LAST
   requested photo deserves a full decode. Cancel/supersede stale
   priority-0 jobs (track "current wanted path"; worker skips jobs
   whose path is no longer wanted — same generation trick already
   used for predecodes). Kills the Picker blur-backlog.
2. **Decode-to-display-size with native dims carried.** Decode at the
   canvas target (~2560 px → 93 ms, smaller targets hit DCT /4 and
   drop further); store `(scaled pixmap, native QSize)` so box-zoom
   1:1 indicators keep true dimensions. Full-res stays a lazy path
   for zoom Phase B (already is).
3. **Edit goes async + cached.** Route Edit through PhotoCache (warm
   case: pixmap→numpy at 14 ms instead of 130–615 ms re-decode);
   decode misses on the worker with the thumb-then-sharp pattern the
   Picker already has; prefetch N±1. Fix `_downsample` (111 ms is a
   numpy-implementation problem; PIL/cv2-class resampling is ~10 ms).
   Tone tail (auto+render, ~25 ms) stays UI-thread — fine.
4. **Quick Sweep:** pass a target size (instant 1.5×+), bound the
   cache, optionally adopt the same async worker.
5. **Sharpness honesty:** compute off the decoded native array on the
   worker (or defer until full-res arrives); never score the thumb.
6. **Cut surfaces join the engine:** single view routes through
   PhotoCache (grid thumb as instant placeholder + async full
   decode + N±1 prefetch + the shared coalescing); exported files get
   a disk-thumb tier so Cut grids fill at ~2 ms/cell like the Pick
   grids do, instead of ~24 ms UI-thread decodes.

Expected outcome: every surface effectively ~90–145 ms/item worst
case today's files, with instant thumb placeholders and no freezes —
"fast flip with a beat of softness", clearly better, possibly not yet
FastStone.

### Stage 2 — the proxy tier (Nelson's idea, if Stage 1 isn't enough)

Pre-baked ~2560 px JPEG proxies (~400–800 KB) decode in ~20–40 ms —
genuinely FastStone territory when combined with Stage 1's async +
prefetch. Design sketch, honoring the charter:

- Proxies are **derived data** → live beside the existing thumb cache
  (`<event>/.cache/proxies/<sha256>.jpg`), NEVER in the captured
  tree (invariant 7). Zero-byte until generated; safe to delete.
- Generated in the background by the spec/60 batch-engine pattern
  (worker process, hardware ladder, zero foreground lag), seeded at
  Collect/ingest and on demand.
- Surfaces display proxies by default; **F10 = full-screen,
  full-resolution** of the current item (Nelson's quality-check
  idea — feeds the scheduled app-wide keyboard review); compare
  views load real pixels.
- Open design questions: proxy size (screen-driven?), RAW proxies
  from embedded thumb vs half-res demosaic, invalidation on external
  edit (folder round-trip, spec/57), disk budget.

### What neither stage solves by itself

Edit on RAW needs the real demosaic for honest tone work: 615 ms full
/ 233 ms half-res. Even async, the SHARP working image lags ~¼–⅔ s
behind the keypress on RAWs. If Edit-on-RAW must also flip fast, that
is a separate design conversation (e.g. develop on half-res until
Preview/Export).

## 4. Recommendation

Stage 1 first — it fixes architectural defects that proxies would
only paper over (Edit's synchronous pipeline would still freeze on
proxy files; the Picker backlog would still queue stale decodes).
Re-measure with `_probe_nav_costs.py` + hands-on feel; if held-arrow
still isn't FastStone, Stage 2 rides on infrastructure that mostly
exists (thumb-cache pattern + spec/60 worker). Both stages need a
short design pass with Nelson before code.
