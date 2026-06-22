# 99 — Quick Sweep cold path: a fast pre-ingest decode tier

**Status: §A + §B SHIPPED (Nelson 2026-06-22). §C — the oversized-source
nudge — is HELD: the existing new-event Quick Sweep flow already routes
per-day exclusively via the DaysListsPage → DaysGridPage → QuickSweepPage
stack (spec/97); there is no flat all-items entry to nudge away from
today. Re-open if a flat-all entry is later introduced. Makes the
pre-ingest Quick Sweep usable on an oversized source (a whole multi-day
trip) without touching the post-ingest pipeline that the Picker relies
on. Revises nothing in spec/63 §4 (the LOCKED keymap is untouched) or
the charter invariants; it adds a Quick-Sweep-only decode profile
layered on the existing `PhotoViewport` / `PhotoCache` engine. Touches
`mira/ui/pages/quick_sweep_page.py` and `mira/ui/media/photo_viewport.py`
(a per-instance profile), with no change to the shared decode worker's
contract. Sibling to spec/97 (the same new-event Quick Sweep flow).**

> **Quick Sweep runs BEFORE the caches exist.** This is the root fact,
> and it is accepted, not fought. The thumb tier (256px, sha256-keyed,
> pre-warmed at the end of `run_ingest`) and the proxy tier (2560px,
> sha256-keyed, filled by `ProxyBuilder` and seeded via
> `set_event_context` / `seed_proxies`) are both **post-ingest**. The
> Picker, Days Grid, Editor and Cut pages seed them; Quick Sweep — by
> design — sweeps raw `SourceItem` paths off the card before any
> `event.db` exists, so none of those warm tiers are reachable. The
> goal here is NOT to build those caches early; it is to make the
> cache-cold path itself cheaper and better-hidden.

## 1. Problem

`QuickSweepPage` (capture-flow "Pick before copying", and the standalone
source-folder sweep) loads raw `SourceItem`s and never calls
`photo_cache().set_event_context()` / `seed_proxies()` — verified: those
calls live in `picker_page.py`, `days_grid_page.py`, `editor_page.py`
and the Cut pages, but not in `quick_sweep_page.py` or `capture_flow.py`.
So on every navigation:

1. `get_thumb_pixmap_sync()` → `None` (no sha256 / no event root) → no
   instant placeholder; the canvas holds the *previous* frame.
2. `_resolve_proxy_for()` bails (no sha256) → the proxy fast path
   (20–40ms class) is unavailable.
3. The request falls through to a full-resolution **original** decode of
   the source file, served one-at-a-time by the single `_DecodeWorker`
   thread, at the viewport's normal display target (3840 / 5120 per
   `_DISPLAY_QUALITY_CEILINGS`).

RAW is **not** the bottleneck: `image_loader.load_qimage` already pulls
the embedded ~1620px JPEG thumb for RAW (fast). The pain is **big
JPEGs** (G9-class 24–45 MP / ~6 MB): a cold read off the card plus a
large-target DCT decode, ×1 thread, ×hundreds-to-thousands of frames.
On a 10-day trip this stalls badly enough to abandon the sweep (Nelson,
observed 2026-06-22).

The intended scale is **one day's card**, where the cold path is already
tolerable. This spec hardens the oversized case so a misuse degrades
gracefully instead of becoming unusable.

## 2. Design — a Quick-Sweep decode profile (A + B), plus a guardrail (C)

All three are opt-in per `PhotoViewport` instance, set by
`QuickSweepPage` only. Every other surface keeps today's behaviour
exactly.

### A. A lower "sweep" decode ceiling

Quick Sweep is keep/skip triage, not pixel-peeping — F10 (the truth key,
spec/63 §4) still decodes the real original on demand for the one frame
the user wants to inspect. So the *browse* tier does not need a
4K/5K-class frame.

- Add a per-instance `sweep_ceiling` on `PhotoViewport` (default
  `None` = today's `_DISPLAY_QUALITY_CEILINGS` behaviour). When set,
  `_target_size()` / `_nav_target_size()` clamp the long edge to it.
- `QuickSweepPage` sets `sweep_ceiling ≈ 2048` (a 1/4-class `scale_denom`
  off a 24 MP JPEG; `QImageReader.setScaledSize` already gives the
  DCT-domain downscale — measured 3–4× faster than the large-target
  decode on 24 MP, per `load_qimage`'s own note).
- **Never upscale beyond native** — same `min(target, native, ceiling)`
  rule spec/95 §A established; a small source shows at true size.
- **The cap is the WINDOWED-browse ceiling only — both fuller views stay
  first-class.** The reduced ceiling buys fast cold navigation in the
  small (~1100×740) capture-flow modal; it must not bleed into the two
  larger viewing affordances the surface already offers (both present in
  `quick_sweep_page.py` today):
  - **Full Resolution (F10)** — the inspection lens, unchanged: it
    decodes the **true original** on demand (the spec/63 §4 truth key),
    never the sweep tier. The cap never touches it. The lens already
    carries the full focus-judgement toolkit on real pixels — **1:1
    box-zoom (Z) + pan** and **honest focus peaking (F)** with the
    colour/sensitivity controls (`_InspectView` in `photo_viewport.py`).
    So the sweep tier never has to be sharp enough to *judge* focus: a
    soft browse frame is only a triage proxy, and the moment the user
    actually wants to verify critical focus they press F10 and dive in to
    true 1:1 with peaking. This is precisely why a reduced browse ceiling
    costs the user nothing real.
  - **Full Screen (F11)** — when the user enters full screen, the browse
    tier lifts from `sweep_ceiling` back to the real display ceiling
    (`_DISPLAY_QUALITY_CEILINGS` per spec/95) and re-requests the current
    frame at that target (a settle-class upgrade, dropped/superseded by
    navigation per spec/63 §3.1), so a 4K panel shows a sharp frame
    rather than an upscaled 2048. Leaving full screen restores the sweep
    ceiling. So the cap is a property of *how big the view is*, not a
    global quality drop.

Effect: each cold frame decodes substantially faster in the fast windowed
pass — for free, no disk writes, no new cache — while full screen and
full resolution both remain available at full quality on demand.

### B. Deeper forward read-ahead (the sweep is linear)

A sweep is a strictly forward pass, the ideal case for prefetch — but
the viewport today prefetches only `_PREFETCH_OFFSETS = (1, 2, -1)`, and
each is a full-target original decode competing with the on-screen frame
on one worker thread.

- Add a per-instance prefetch plan. `QuickSweepPage` uses a
  forward-biased, deeper set (e.g. `(1, 2, 3, 4, -1)`) at the **sweep
  ceiling** target from §A (cheap entries, so a deeper window is
  affordable).
- The spec/63 §3.1 generation-drop already protects this: a held-arrow
  burst drops fly-by prefetches automatically, so deepening the window
  cannot reintroduce the held-arrow backlog spec/63 slice 0 removed.

Effect: on a steady forward cadence the worker stays ahead of the user,
so landed frames are warm and the placeholder→sharp beat shrinks toward
invisible.

### C. Guardrail — keep people on the fast path (oversized-source nudge)

§A+§B soften the worst case; the cleanest win is to make it rare.

- When the scanned source crosses a threshold (proposed: **> ~600 items
  OR spanning > 2 day-buckets**), the new-event Quick Sweep entry offers
  to **sweep per day** rather than one flat pass — the Days-Grid level
  already partitions by day (spec/97's stack), so this is a default
  selection, not new UI.
- Pure nudge: the user can still choose the flat all-items sweep. No
  hard cap, no blocked flow.

## 3. Non-goals / what this deliberately does NOT do

- **No early proxy/thumb build for Quick Sweep.** Building sha256-keyed
  caches before ingest would duplicate the ingest pipeline and write
  derived data for files the user is about to *not* copy. Rejected.
- **No worker-thread pool.** A 2–3 worker `_DecodeWorker` pool would lift
  read-ahead throughput further, but it touches the shared decode engine
  (every surface) and widens the test surface. Held in reserve as a
  later, separately-specced change if §A+§B prove insufficient on the
  slowest machines.
- **No keymap, no charter-invariant impact.** F10 still decodes the true
  original; the captured tree is never mutated; no network, no
  telemetry.

## 4. Acceptance

- A flat Quick Sweep over a large all-JPEG source (≈1–2k frames, 24 MP)
  is navigable at a steady forward cadence without the canvas stalling on
  the previous frame for seconds — the §62 "switching photos is
  sluggish" bar, applied to the cold path.
- F10 on any swept frame still opens the honest full-resolution lens
  (real original pixels), unchanged.
- F11 full screen on a swept frame shows it at the display ceiling (sharp
  on a 4K panel), not an upscaled sweep-tier frame; leaving full screen
  returns to the fast windowed cadence.
- Every non-Quick-Sweep surface decodes at exactly today's target
  (regression guard: the profile defaults to `None`).
- The oversized-source nudge (§C) fires on a multi-day source and is
  absent on a single day's card.

## 5. Tuning (resolved-but-flagged — live knobs to revisit on the laptop)

The §A + §B shipped values are the proposed starts; each remains a live
knob — bump only if the laptop reads it as a problem.

1. **Sweep ceiling value — shipped at 2048.** Bump to 2560 (the proxy
   edge) if 2048 reads soft on the laptop screen for keep/skip judgement.
   F10 covers true inspection either way, so this only affects the
   browse-tier eyeball pass.
2. **Prefetch depth — shipped at `(1, 2, 3, 4, -1)`.** A deeper forward
   window only helps while the single decode worker can stay ahead of
   the user. If the laptop falls behind on a held arrow, drop back to
   `(1, 2, 3, -1)` before adding worker threads (the worker-pool change
   is held in §3 / "Non-goals").
3. **§C threshold (when revisited) — start at >600 items OR >2 day-buckets.**
   The numbers are tuned to separate "a day's card" from "a whole trip";
   re-tune on the real card sizes the user brings back from trips.
   §C itself is HELD — see status banner.
