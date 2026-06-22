# 63 — The Photo Viewport (one engine, every surface)

**Status: DESIGN LOCKED with Nelson 2026-06-12 (design-mode session).
Governs all photo/video display surfaces. spec/62 is the audit record
that motivated this; spec/05 §keys and the per-surface key handling it
described are superseded by §4 below. Build order in §7.**

The audit (spec/62) found four surfaces answering "how does a photo
get on screen" four different ways — one frozen (Edit), one queue-sick
(Picker), two disconnected (Quick Sweep, Cut session) — and measured
that the image pipeline, not chrome/DB/scaling, is the entire cost.
This spec replaces the four answers with one.

## 1. The split

**Surfaces own chrome and decisions. The viewport owns pixels.**
A single `PhotoViewport` component is embedded by every surface that
shows a current item (Picker, Edit, Quick Sweep, Cut session single
view, future surfaces). It alone decides how an item appears, what
shows while pixels are coming, what a held key does, what is
remembered, and how much memory the whole app spends on images.
Surfaces never decode, scale, or cache pixels again.

## 2. The experience contract (uniform, non-negotiable)

- **Press** → something appears instantly (thumb/poster, already in
  hand); the sharp version lands within a beat.
- **Hold** → items fly at key-repeat speed on placeholders; on
  landing, the landed item sharpens FIRST (skip-ahead: flown-past
  items are never fully decoded).
- **Step back** → instant: one shared, capped, app-wide LRU replaces
  today's three private caches (one of them unbounded).
- **Zoom / F10** → true pixels on demand only.
- **Video** → poster behaves exactly like a photo placeholder; the
  player ARMS ONLY ON LANDING (a settle beat after navigation stops),
  and the poster→live flip happens only when real frames flow for the
  item still on screen. This keeps the spec/59 no-black-frame
  guarantee AND kills the per-keypress flicker (2026-06-12 bug #1).
- **MediaNav chrome (Nelson 2026-06-22):** prev/next on every viewing
  surface (Picker / Editor / Quick Sweep / Full Resolution / Video
  Picker / Video Editor) is an **inline ghost-styled "‹ Prev" /
  "Next ›"** pair via :func:`mira.ui.design.media_nav.nav_button`,
  sitting in the bottom control row alongside the other ghost buttons.
  This **overrides** the earlier "floating circular ‹/› arrows, no
  text Previous/Next" rule: the floating-arrow QSS role
  (``#MediaNavArrow``) never propagated cleanly, so the Quick Sweep
  viewer was rendering raw native OS buttons next to the ghost-styled
  Pick / Skip / Compare cluster. Inline labelled ghost buttons keep
  consistency without depending on a custom chrome role.

## 3. Engine evolution (PhotoCache v2)

1. **Coalescing / skip-ahead.** Navigation requests supersede: only
   the current wanted path deserves a priority-0 decode; stale
   priority-0 jobs are dropped exactly like stale predecodes are
   today (the audit's queue disease).
2. **Decode-to-display-size, native dims carried.** The cache stores
   `(pixmap at target size, native QSize)` so box-zoom 1:1 and crop
   indicators keep true dimensions (the recorded 2026-06-09
   regression reason for native-only decode). Full-res remains a
   lazy, explicit tier (zoom Phase B, F10).
3. **Prefetch** (N+1, N+2, N−1 on settle) becomes a viewport
   behavior — every surface inherits it.
4. **One memory budget** for pixmaps app-wide; thumb tier unchanged.
5. **Sharpness honesty** (Picker): scored off the decoded native
   array on the worker, never off whatever the canvas happens to
   show (the audit's score-the-thumb bug).

## 4. The keyboard map (LOCKED — closes the scheduled review)

Universal on every photo surface; enforced once, in the viewport:

| Key | Meaning |
|---|---|
| ← ↑ / → ↓, wheel | previous / next |
| **P** | Pick (set) |
| **X** | Skip (set) |
| **Space** | simple toggle Pick ⇄ Skip |
| **C** | full cycle Pick → Skip → Compare → Pick |
| **Tab** | play/pause (clips; inert on stills — focus traversal stays disabled) |
| **Enter** | play/pause the cluster sweep |
| **F / F11** | fullscreen |
| **F10** | the INSPECTION LENS: full-resolution in a MODAL, RESIZABLE, ASPECT-LOCKED window (amended Nelson 2026-06-12 UI round — best pixels without the screen takeover; the picture fills the view edge to edge; the app waits until the lens closes) carrying the zoom + peaking CONTROL BAR (Peaking · Colour · Sensitivity · Zoom 1:1; colour/sens collapse until Peaking is on); house-themed (the photo bed + a region card — no inline styling); HONEST peaking + true 1:1 zoom + AF; **F11 inside the lens = the PURE look** (truly fullscreen, bar hidden, peaking + zoom OFF); Esc one level at a time (zoom → fullscreen → close); in Edit = the developed Preview |
| **Esc** | one level back |
| **Ctrl+Z** | undo last decision |

Rulings recorded (Nelson 2026-06-12): no context-dependent keys —
transport (Tab/Enter) and decisions (P/X/Space/C) never share a key;
two toggles are deliberate (Space for fast binary sweeps, C for
serious triage); on binary-ledger surfaces (Cut session) C degrades
to Space's behavior (fewer states, same intent). Legacy evictions:
Picker P-sweep → Enter; Edit P-Preview → F10 (and the dead P-export
branch dies). D stays retired. Surface-specific extras (Picker R)
live in the surface hint line and may never collide with this table.

## 5. The pixel tiers

| Tier | What | Where | Made |
|---|---|---|---|
| Thumb | 256 px JPEG | `<event>/.cache/thumbs/photos/` | ingest (exists) — **extended to exported files (slice 8 ✓ 2026-06-12**, at 280 px under `.cache/thumbs/exports/`, relpath-digest keyed**)** so Cut grids fill at ~2 ms/cell |
| **Proxy** | ~2560 px JPEG, quality ~85 | `<event>/.cache/proxies/` | **landed (slice 7 v1, 2026-06-12):** write-on-decode (the worker persists proxy-grade decodes it already holds) + a polite in-process builder thread (`core/photo_proxy_cache.ProxyBuilder`), seeded at Pick event-open (whole event) and per-bucket registration; the spec/60 worker-process promotion stays a future option |
| Original | the real file | captured tree / Edited Media | never mutated (charter inv. 7) |

The viewport prefers proxy when present, falls back to original —
surfaces never know. Proxies are derived data: deletable, regenerable,
excluded from any backup story. Invalidation: source mtime/size
mismatch → rebuild (covers the spec/57 external round-trip).
Disk honesty: ~3 GB per 5 000 photos, visible in settings.
Audit-measured payoff: 20–40 ms decodes — held-arrow at key-repeat
speed (the FastStone bar) on top of §3.

## 6. Edit's development model

Browse on the viewport like everywhere else (proxy-sharp). The
working copy (tone math input) is prepared back-of-house: half-size
demosaic for RAW (~233 ms, off-thread), full decode for JPEG; the
1280 px preview pipeline stays as today (~25 ms, UI). **Full-res
development happens only at F10 (Preview) and Export** — the moments
the user asks for truth. The audit's 111 ms `_downsample` flaw is
fixed in passing. Per-nav gateway reads/writes stay (measured cheap).

### 6.1 — 6b execution map (LOCKED 2026-06-12 — Nelson's checkpoint:
**Q1 instant landing accepted** (undeveloped flash, tools greyed,
develop-in-place ~¼–⅓ s) · **Q2 RAW F10 preview from the half-size
working copy** (the lens's honest-RAW definition; export untouched) ·
**Q3 develop-only-on-settle confirmed** (the Picker cadence) · Q4
nothing raised. **EXECUTED same day** — the swap landed as mapped;
the net passed UNEDITED across it; deviations + the crash-hunt
verdict in the landed notes at the end of this section)

**LANDED NOTES (2026-06-12).** Executed as written below, plus:
`edit_prep.py` is a process-wide SINGLETON relay (the PhotoCache
shape — the worker emits only to the long-lived relay, pages get
same-thread delivery; signal-to-signal chaining, never `.emit` as a
slot, which loses the receiver and runs downstream slots on the
worker thread); the worker thread is self-terminating (exists only
while a job is pending). EditPage gained `shutdown()` (quiesce: stop
the settle timer, leave the prep fan-out, drop viewport items) which
ALSO runs automatically on DeferredDelete — a defined lifecycle end.
`_downsample`: 111 ms LANCZOS → integer-box `reduce` + BILINEAR
finish = **37 ms measured**, and it rides the worker anyway (UI cost
zero). The mid-verification 0xC0000409 process crashes were bisected
to a LATENT PRE-EXISTING bug (reproduced 4/4 at `4eb4d69`, before
any of today's work): any pytest process constructing bare
AdjustmentSurfaces fail-fasts in Qt6Core during whichever suite runs
next — verify.bat now runs those two suites in their own process
(quarantine) AND propagates pytest's exit code (it used to end with
`type`, masking crashes as green). The deep fix is its own session;
spec/PROGRESS.md carries the reproducer + the event-log signature.

**Anatomy as analysed (the organism, 2026-06-12).** EditPage `_show`
→ `_load_and_render_item` runs the WHOLE pipeline on the UI thread
under a wait cursor: `decode_image` (130–146 ms JPEG / ~615 ms RAW
full demosaic) → gateway adjustment read (cheap) →
`surface.load_image` (= `_downsample` 111 ms + `compute_auto_params`
15 ms) → `set_state` → `render_now` (tone on the 1280 preview,
~25–50 ms). Felt cost ~290 ms JPEG / ~770 ms RAW per navigation, and
arrows queue. Display is a PUSHED pixmap
(`canvas.set_preview_pixmap`); MediaCanvas contributes the photo bed,
`photo_area_widget()` (the crop overlay's parent) and
`photo_geometry_changed` (the overlay sync). EditVideoPage SHARES the
surface and detaches/reattaches `surface.canvas()` around clip
development (the phantom-window lesson). F10 already feeds the
standard lens from `render_full_pixmap()` (full array in hand, pure
read). Export is INDEPENDENT (`core/process_export_engine` decodes
from file) — export fidelity is untouched by anything here.
`decode_image` is documented Qt-free/thread-safe — the off-thread
move is sanctioned by its own docstring.

**The target landing sequence (per navigation):**

1. The viewport shows the photo INSTANTLY — thumb placeholder →
   proxy-sharp within a beat (the slice-7 path, 18–30 ms decodes).
   This is the *undeveloped* image.
2. Tools grey (the existing `set_tools_enabled` affordance — the
   spec/59 greyed-on-Skipped precedent, a look the user knows). The
   exported chip / state border update immediately (cheap reads).
3. On SETTLE (the Picker's 150 ms cadence — a held arrow never
   queues prep work; generation-dropped like slice 0), the **prep
   worker** decodes the working copy off-thread: JPEG full decode →
   fast downsample (PIL-class, ~10 ms — the 111 ms numpy flaw dies);
   RAW **half-size demosaic** (~233 ms). `compute_auto_params` rides
   the worker (it only needs the 1280 preview).
4. The worker delivers → `load_image`-equivalent state lands →
   `render_now` on the UI thread (~25–50 ms) → the canvas flips
   undeveloped → DEVELOPED working view; tools un-grey. Total
   sharp-to-developed gap ≈ ¼ s JPEG / ⅓ s RAW, app responsive
   throughout.
5. Same-path renavigation keeps today's cache fast-path (the worker
   caches the last working copy by path).

**The display-engine swap.** MediaCanvas leaves the surface;
`PhotoViewport` becomes the one display engine (the §1 thesis).
Additive viewport APIs Edit needs (each small + testable):

* `set_rendered_pixmap(pm)` / clear-on-nav — the developed working
  view and the Toggle-Crop preview are HOST-RENDERED pixels shown
  for the current item (the `set_preview_pixmap` contract, viewport
  edition; kin of the loose-slide display path).
* `photo_display_rect()` + a `photo_geometry_changed`-equivalent
  signal — the crop overlay parents onto the viewport's photo area
  and syncs exactly as it does on MediaCanvas today.
* `set_truth_internal(False)` + the host's `truth_requested` →
  EditPage's processed-lens handler (the §7.9 note; the
  `_truth_internal` flag already exists in the viewport). F10 keeps
  meaning THE DEVELOPED full-res preview on this surface.
* `display_widget()` exposure for EditVideoPage's detach/reattach
  dance (today it reparents `surface.canvas()`).

The viewport ALSO carries Edit's browse keys (arrows/wheel,
Home/End), the 6a decision verbs (P/X/Space/C → mark-for-export
handlers), F/F11, and the unsupported-file/video poster path —
EditPage's own keyPressEvent shrinks to its page-specific keys
(L/G/[ ]/\/R), the 6a keymap pins stay green.

**The prep worker.** A small Edit-owned single-thread worker (the
`_DecodeWorker` pattern: priority heap unnecessary — one job at a
time, newest wins, stale results dropped by generation). It is NOT
the PhotoCache worker: display pixels and tone-math inputs are
different tiers with different lifetimes; the PhotoCache stays
display-only. Output: `(path, full_array, preview_array,
natural_params)` delivered queued to the GUI thread.

**RAW policy (the §6 rule, applied):** the working copy is the
HALF-SIZE demosaic — tone choices and the 1280 preview are
resolution-insensitive (auto-params read the 1280 preview either
way; export re-decodes full independently). F10's developed preview
on RAW develops the half-size copy — consistent with the lens's
existing "honest RAW = half-res sensor decode" definition — with
the lens Z-zoom keeping its on-demand TRUE-full-res path. (The
alternative — a one-time ~615 ms full demosaic on first F10 per
photo — is the checkpoint's Q2.)

**What does NOT change (the insurance list):** the tone model
(Style/Look/Filter, the A-routed Natural), the crop overlay
interaction + box rotation, Toggle-Crop (button-driven, full-res
computed, canvas-fit), Compare, the 6a keymap, adjustment
persistence + the changed-signal contract, the export pipeline and
its dialog, the video workshop's development semantics (it keeps
pushing extracted-frame arrays through the same surface API), the
exported watermark, the classification badge.

**The net (tests-first, the 5d recipe).** Edit's suites are
slice-B-skipped — no net exists. New module
`tests/test_edit_pixel_model.py` (name dodges the skip list),
committed at clean HEAD BEFORE the swap, pinning behavior that must
survive: load→developed-preview-reaches-display (through a display
seam, flagged as the one pin the swap rewires, like 5d's Tab pin);
same-path renav skips re-decode; unsupported file degrades
gracefully; set_state/get_state round-trip + persistence emissions;
crop-overlay geometry after load + resize; `render_full_pixmap` =
full-res crop-baked dims + pure-read; Toggle-Crop on/off restores
the working view; Compare shows the original; the video page's
frame-development render path. Then the atomic swap
(AdjustmentSurface display side + EditPage `_show` pipeline), net
green unedited.

**Checkpoint questions (Nelson rules, then code starts):**

* **Q1 — the landing moment.** Photo instantly sharp but
  *undeveloped*, tools greyed, developed look replaces it ~¼–⅓ s
  later. Accept the undeveloped flash? (Alternative: hold the
  PREVIOUS photo until the new one is developed — no flash, but
  landing feels slower; against the never-blank/never-stale grammar
  everywhere else.)
* **Q2 — RAW truth at F10.** Develop the half-size copy (instant,
  the lens's existing honest-RAW definition) or pay a one-time
  ~0.6 s full demosaic per photo for true-full-res development in
  the lens? (JPEG is always truly full — no question there.)
* **Q3 — browse semantics.** Held-arrow in Edit = pure browse
  (proxy-sharp, no development prep until settle), the Picker's
  cadence. Confirm.
* **Q4 — anything else in the Edit feel** to fold in while the
  organism is open (cheap moment for small reshapes).

## 7. Build order (one slice = one eyeball)

**Reordered 2026-06-12 (Nelson):** video moved from last to slice 3.
Discovered migrating Quick Sweep — the video-bearing surfaces route
navigation + keys THROUGH the widget that shows both photos and
video, so video in the viewport is a PREREQUISITE for migrating them,
not a follow-on. Building it next avoids throwaway hybrid glue and
closes the flicker (bug #1) + the Play-freeze suspect (#7) before the
surfaces that depend on it.

0. PhotoCache v2 (coalescing, decode-to-target + native dims) — tests. ✓
1. `PhotoViewport` widget (photos: placeholder→sharp, prefetch,
   memory budget, key grammar) — tests. ✓
2. **Cut session + detail** migrate (single views over the viewport;
   grid thumbs decode ASYNC via the scaled tier at priority 1 — the
   UI-thread jam dies now; their on-disk materialisation rides the
   slice-8 builder) — eyeball. ✓
3. **Video in the viewport — arm-on-landing** (was slice 6; Nelson's
   own design, confirmed identical): a video shows its poster like a
   photo placeholder while flying past; the QMediaPlayer/QVideoWidget
   ARMS only on settle (the landed beat), and the poster→live flip
   fires only when real frames flow for the item still on screen.
   Stacked-sibling, not overlay (the Windows compositor rule). Keeps
   the no-black-frame guarantee, kills the per-keypress flicker.
   Exposes a timeline API (position/duration/seek/play-pause) for the
   surfaces' scrubbers. The Cut surfaces' blank-video interim heals
   for free — eyeball with real clips (bug #1 closes here).
4. **Quick Sweep** migrates — eyeball.
5. **Picker** migrates — Nelson chose FULL ABSORB, then shaped it into
   a THREE-MODE peaking model (2026-06-12), peaking in two of them:
   - **Browse** (everyday stills): clean + fast, NO peaking/zoom.
   - **Sweep** (play a focus cluster, Enter): peaking ON — FAST
     stack-film peaking (compute on the quick display pixels so frames
     keep flipping; a per-frame half-res demosaic would stutter the
     playback) so the user watches focus travel through the burst.
   - **Inspect** (F10): full-screen full-res + HONEST peaking (real
     half-res sensor data, one frame) + zoom + AF.
   Sub-slices:
   - 5a AF overlay ✓ · 5b focus peaking (+ honest RAW half-res, +
     the fast stack-film flag) ✓ — these STAY in the viewport: the
     Sweep uses the viewport's peaking (stack-film); F10 uses the
     honest path. (The everyday browse simply never toggles it on.)
   - 5c box-zoom (Phase A/B, pan, full-res RAW, region) — built
     INSIDE the F10 InspectView ONLY (the everyday view sheds in-view
     zoom entirely — the real simplification from the reframe).
   - InspectView shell: F10 opens a fullscreen full-res inspector
     carrying honest peaking (F) + zoom (Z) + AF + pan; Esc/F10 closes.
   - Sweep-with-peaking: the cluster slideshow (Picker drives
     show_index at the film cadence) with the viewport's stack-film
     peaking on — lands with 5d.
   - 5d Picker rides the viewport; sharpness honesty; P-sweep → Enter;
     the Sweep-with-peaking; retire its MediaCanvas — eyeball. ✓
     (2026-06-12, tests-first: the 28-pin net landed at clean HEAD,
     the atomic swap on top of it, net green unedited — §8.)
   - 5e VideoPickPage migrates; retire PosterStack (closes bug #1). ✓
     (2026-06-12: the page-owned player/PosterStack gone — the
     viewport's arm-on-landing IS the no-black-frame guarantee; the
     Day-Grid poster bridges in as a host-supplied ViewportItem
     pixmap; decisions leave as `decision_verb_requested` verbs and
     PickPage degrades C→toggle, the binary-ledger rule; Tab became
     TRANSPORT — its legacy cycles-state pin rewritten as a §4 pin;
     F/F11 fullscreen landed on the video surface; viewport grew the
     `video_error` pass-through + the F10-inert-on-poster guard.)
6. **Edit** migrates (§6 model, P-Preview → F10) — eyeball.
   **6a (the key-map half) LANDED 2026-06-12:** both Edit surfaces
   speak §4 — EditPage P/X set the export mark, Space/C toggle, F10 =
   the developed Preview (the legacy P binding evicted; the DEAD
   second Key_P export branch deleted); EditVideoPage Tab=transport,
   Space/C=toggle-at-cursor (the Space-plays binding evicted), F/F11;
   stale "(P / D)" texts fixed. `tests/test_edit_page_keymap.py`.
   **6b (the pixel model) PARKED for its own design-checkpoint
   session:** viewport browse + off-thread working copy + the 111 ms
   `_downsample` fix need a §8-style turnkey analysis first — the
   canvas, crop overlay and render pipeline are one organism inside
   AdjustmentSurface, and Edit's suites are slice-B-skipped (no net
   exists yet). Do it like 5d: analysis → net → atomic swap.
   **✓ LANDED 2026-06-12:** analysis → §6.1 map → Nelson's checkpoint
   (Q1–Q3 as recommended) → the net at clean HEAD → the atomic swap,
   net green UNEDITED. Every photo surface now rides the ONE engine.
7. Proxy tier (builder + `.cache/proxies/` + prefer-proxy +
   invalidation) — eyeball + disk honesty. ✓ **LANDED 2026-06-12
   (v1 as parked-noted):** `core/photo_proxy_cache.py` (Qt-free) —
   sha256-keyed `<sha>.jpg` + `<sha>.json` sidecar (source mtime_ns +
   size = the invalidation key; **the ORIGINAL's post-orientation
   native dims** = what the scaled tier keeps reporting, so
   `sharp_pixmap_info` consumers never learn proxies exist; sidecar
   written LAST = the commit marker — a crash between writes can't
   serve unverified pixels). ONLY the scaled tier prefers proxies:
   `request_pixmap` (Compare) and the F10 lens decode originals by
   construction. Corrupt proxy → drop pair, decode original, rebuild
   (self-heal). Write-on-decode persists AFTER the emit (sharp
   latency never pays) and only proxy-grade decodes (long edge ≥
   min(native, 2560) − 2 px — small-window decodes would serve soft
   later; the builder fills those). `ProxyBuilder` daemon thread
   yields while the decode worker has queued jobs; seeded by
   `set_event_context` (per bucket) + `PickPage.open_event` (whole
   event, one SQL pass); cross-root seed drops the stale queue (the
   cache deliberately does NOT clear the builder on context switch —
   the whole-event seed lands BEFORE the first bucket registration).
   Disk honesty: Settings → Advanced "Screen copies" info row
   (count · size for the open event) + NoIcon-confirmed Clear…, via
   the new schema `info` widget kind (host-injected providers).
   RAW proxies = the embedded-preview decode (what browsing shows;
   native dims = that decode's dims, the tier's existing RAW
   contract); sharpness/peaking honest paths untouched (half-res
   demosaic). **Measured (probe rig, noisy 24 MP JPEG @2560 target):
   original 140 ms → proxy 24 ms (5.9×, inside the predicted
   20–40 ms class); proxy 0.61 MB (~3 GB / 5 000 — the §5 number);
   build 309 ms/photo in the background.** Tests:
   `tests/test_photo_proxy_cache.py` (21) +
   `tests/test_settings_info_row.py` (4).
8. Export-file thumb tier (Cut grids fill at ~2 ms/cell on disk).
   ✓ **LANDED 2026-06-12 (with 7):** `core/photo_thumb_cache.py`
   export functions — `<event>/.cache/thumbs/exports/<relpath-digest>
   .jpg` (exports aren't Items; the lineage `export_relpath` IS the
   identity, so its digest is the key). **280 px, not 256** — the
   Cut grids request at the Day Grid's MAX_CELL_SIZE (280); a 256
   thumb would upscale at the slider's top end. Staleness is
   make-style (`thumb.mtime ≥ source.mtime`) — a re-export
   overwriting the file invalidates; hardlinked backfill sources keep
   old mtimes, which the later-written thumb always beats. The four
   lineage writers (`ui/edited/_lineage` ×2 entry points, the return
   scan, the spec/57 backfill) **QUEUE thumbs onto a background
   builder** (the slice-7 `ProxyBuilder` with the thumb ensure
   injected) — never inline: a 200-file batch must not stall the
   foreground. Engine: scaled requests at ≤280 targets for NON-item
   paths (no sha) serve the thumb (native dims via the original's
   header probe); bigger targets (Cut single views) bypass; item
   paths never get export thumbs (they have proxies). Files exported
   BEFORE this slice self-heal via the same write-on-decode hook the
   proxies use. Cut session + detail pages register the event root
   (`set_event_context(root, {})`) — a straight-to-Share flow never
   passes a Pick surface. Clip exports (.mp4) skip by suffix —
   their posters ride the video thumb cache. Tests:
   `tests/test_export_thumbs.py` (13).
9. ~~F10 truth key~~ **landed early in the viewport**. AMENDED in the
   2026-06-12 UI round (Nelson's first final-app eyeball): the lens
   opens WINDOWED (resizable, image-aspect inside ~88% of the screen,
   title = name + honest pixel dims) with the zoom/peaking control
   bar; F11 inside it = the pure fullscreen look (bar + helpers off);
   Esc steps down one level. ~~Remaining after 6a~~ **done with 6b
   (2026-06-12)**: Edit's `set_truth_internal(False)` +
   `truth_requested` wiring landed — the viewport's F10 routes to the
   page's processed-lens handler; the app-wide map sweep is DONE (Picker
   5d, video Pick 5e, both Edit surfaces 6a, Cut surfaces at slice 2;
   cut_play keeps Space-pause deliberately — a pure player with no
   decision keys to collide).
10. Spec/docs sync, full-suite sweep, re-run the spec/62 probes and
    record the after numbers. (Sweep run 2026-06-12 with 6a; **probe
    re-run DONE 2026-06-12 with 7/8** — spec/62 §1.1 carries the
    after table: browse 93–145 → 18–30 ms, Cut grid cell 24 →
    0.4 ms; Edit's numbers move with 6b.)

Deferred deliberately: compare surfaces keep using true pixels (no
proxy) — confirmed direction, details with the compare-view design;
settle-delay tuning is implementation, calibrated by eyeball.

---

## 8. 5d execution map (EXECUTED 2026-06-12 — tests-first, then the
atomic swap; the net below passed UNEDITED across the migration)

**Landed record.** The 28-pin net committed first at clean HEAD
(`tests/test_pick_photo_surface.py`), the ~1,500→~1,000-line rewrite on
top, the net green unedited, + 9 new-key-map pins written with the
swap. Execution deviations worth knowing (all in the surface's
docstrings too): PickPage's ``photo.setFocus()`` reaches the viewport
via ``setFocusProxy`` (PickPage untouched); the Combined preview shows
as a viewport LOOSE SLIDE (payload-None card) so per-frame nav is
genuinely locked and restores at the cursor; the surface's small
``keyPressEvent`` (R/Home/End/F1) also carries stray-focus fallbacks
routing to the SAME verb handlers (never a dead key on the cull
surface); the canvas's bucket-colour flip died with the canvas (the
BasePickSurface state border is the visual cue); the long-dead
``_exif_line`` / ``_caption_html`` helpers were dropped. The original
turnkey map follows as written (the record of the analysis):

`PickPhotoSurface` (`mira/ui/picked/pick_photo_surface.py`,
~1504 lines) migrates from `MediaCanvas` to the now-rich
`PhotoViewport`. It WORKS today (got the queue-cure + QImage hardening
for free), so this is the architectural finish, not a repair. Atomic
(no safe half-commit) → net it first.

**Tests first (the safety net it never had).** Build like
`test_cut_session_page`: `EventStore.create(db)` + `save_document` +
`EventGateway(store, event_root=tmp)`; a `CullBucket` of small real
JPEGs whose `item_id`s exist in the doc (so `set_phase_state` /
`set_sharpness` FKs resolve). Cover the PRESERVED behaviours (survive
the migration): `_go` advances `_index`; `_cycle` →
`eg.set_phase_state` persists + `_effective` reads back; sharpness
computes once + persists via `eg.set_sharpness`; `_toggle_film` /
`_film_step` walk playable frames. Then the NEW key map.

**The swap (construction):** `self.canvas = MediaCanvas()` →
`self.viewport = PhotoViewport()`; `set_media(self.viewport)`.
Re-parent the exposure overlay to the viewport (was
`canvas.photo_area_widget()` + `photo_geometry_changed`; now reposition
on the viewport's resize). Retire the canvas signal wiring (346–360).

**Nav:** `load()` → `viewport.set_items([ViewportItem(path, kind,
payload=ci) for ci in items], index)`. Wire `viewport.current_changed`
→ a new `_on_current_changed(index)` that sets `_index` then runs the
CHROME half of today's `_show_current` (exposure, `_sync_state_pill` →
`surface.set_media_state`, sharpness, genre, position, AF feed).
`_go` → `viewport.show_index`; wire `viewport.edge_reached` →
`_on_edge` (the navigate_at_edge path). `_film_step`/`_toggle_film`
set `viewport.show_index(nxt)` (not `_index=…; _show_current()`).

**Keys → the locked map (the alignment):** wire viewport verbs:
`pick_requested`→set PICKED, `skip_requested`→set SKIPPED,
`toggle_requested`(Space)→binary Pick⇄Skip, `cycle_requested`(C)→
`_cycle` (K→D→C), `sweep_requested`(Enter)→`_toggle_film`,
`fullscreen_requested`(F/F11)→`_toggle_fullscreen`,
`back_requested`(Esc)→back. P STOPS being the sweep (→ Enter). The
surface keeps a small `keyPressEvent` for R (reclassify) + Home/End
(propagate up from the viewport). Combined stays a BUTTON (drop the
Shift+P key, or rehome later).

**Sweep-with-peaking (Nelson 2026-06-12):** on `_toggle_film` start →
`viewport.set_peaking_enabled(True)` + `set_stack_film_peaking(True)`
(FAST peaking on the quick display pixels, so frames keep flipping and
the user watches focus travel); on stop/pause → `set_peaking_enabled
(False)`. Load already sets stack-film for focus brackets.

**Sharpness HONESTY (the audit bug):** `_compute_sharpness` scored
`canvas._source_pixmap` (could be the 256-px thumb mid-nav). New: use
`viewport.sharp_pixmap_info()` — score the decoded NATIVE pixmap, or
skip until it lands; never the thumb. (No zoomed-region scoring —
zoom is F10 now, so always whole-frame.)

**AF:** feed `viewport.set_af_point(self._resolve_af(path))` per nav
(the viewport stores it for F10's overlay). No in-view AF toggle.

**REMOVE (now in F10 — the inspection lens):** the TOOLS-row-2 zoom
cluster (`_zoom_toggle/_zoom_fac_±/_zoom_phase/_zoom_reset` + handlers
`_on_zoom_toggle/_on_zoom_phase`), the AF toggle (`_af_toggle`), the
peaking cluster (`_peak_colour/_peak_sens/_peak_toggle` + `_on_peak_*`),
their Z/F key bindings, and the `_refresh_clusters` branches that
gate them. KEEP: Play (`_film_btn`), Combined (`_combined_btn` +
`_toggle_combined`), Reclassify, position, genre, exposure overlay.
Then drop the `MediaCanvas` import.

**Verify:** construct-smoke headed (the memory rule — constructor
touched); a real-flow headed smoke driving a gateway + real photos
(nav, P/X/Space/C, Enter-sweep-with-peaking, F10, sharpness persists);
Nelson eyeball on the live cull. **5e** (VideoPickPage → viewport,
delete `poster_stack.py`) follows.
