# 95 — Adaptive display resolution (HiDPI + display-quality setting)

**Status: SHIPPED (Nelson accepted 2026-06-22). Revises spec/63 §5
(pixel tiers) and §7 (build order) — the proxy tier stays; what
changes is how the *normal* (non-F10) display tier chooses its decode
target. Does not touch the LOCKED keyboard map (§4) or the charter
invariants. Final byte budget for the scaled LRU = 512 MB
(`_SCALED_CACHE_BUDGET_MB`). Implementation landed in commit
[cb0934e](https://github.com/nksalgado-proton/Mira/commit/cb0934e).**

> **Anti-lag is the hard constraint.** This whole proxy/cache pipeline
> (spec/62 audit → spec/63) exists because switching photos was
> sluggish. Nothing here may bring that back. The proxy stays the
> **navigation tier**: every press/hold paints the proxy (or thumb)
> instantly. Any heavier work (the original-decode in §2.B) is
> **settle-only** — it runs after navigation STOPS, on the worker, and
> is dropped/superseded the instant the user moves on (the spec/63 §3.1
> coalescing / skip-ahead rule). Held-arrow speed must equal today's.
> Because of this constraint there is **no "Maximum / native" option** —
> decoding a full 24–45 MP original is exactly the cost spec/62
> removed; the ceiling (§2.C) is always bounded.

## 1. Problem

The normal viewing tier looks soft on big / high-DPI displays:

1. **DPR-blind.** `PhotoViewport._target_size()` rounds the viewport in
   **logical** pixels and `_fit()` scales the base in logical pixels;
   the displayed `QPixmap` carries `devicePixelRatio = 1`. On a scaled
   display (laptop at 125–150 %, or a 4K panel at 1.5–2×) the photo is
   rendered at ~1/DPR of the physical pixels and Qt upscales → soft.
2. **Hard proxy ceiling.** The scaled tier prefers the on-disk proxy
   (`PROXY_MAX_EDGE = 2560`, spec/63 §5). Once a proxy exists it is
   always served, scaled to target — so even when the viewport could
   show more (4K fullscreen), the image is upscaled from 2560.

Nelson runs a large monitor at home and a small-screen laptop while
travelling. A single fixed ceiling is wrong for one of them: too low for
the big monitor, wasteful for the laptop.

## 2. Design — three parts (A + B + setting)

### A. Honor `devicePixelRatio` (always, every display)

- `_target_size()` requests the scaled tier in **physical** pixels:
  `logical_size × devicePixelRatioF()` (then the existing 512-px
  quantisation). 
- The displayed pixmap is rendered at physical size and tagged with
  `setDevicePixelRatio(dpr)` so Qt paints it 1:1.
- **Never upscale beyond native:** the requested long edge is
  `min(physical_target_long, native_long, ceiling)` (ceiling from the
  setting, §C). A photo smaller than the viewport is shown at its true
  size, not stretched.

Zero disk cost. Fixes the most common "soft" case (scaled displays).

### B. Decode the ORIGINAL when the target exceeds the proxy edge — SETTLE ONLY

In the `PhotoCache` decode worker's scaled branch (the `_resolve_proxy`
hit path, `mira/ui/media/photo_cache.py`):

- If `max(target_w, target_h) <= proxy_long_edge` → serve from the proxy
  (today's fast path; the laptop's small targets stay here, always).
- If `max(target_w, target_h) > proxy_long_edge` → **decode the original
  down to the target** (DCT-domain downscale, bounded by the §2.C
  ceiling — never full native) instead of upscaling the proxy.

**This original decode runs on SETTLE only and never blocks
navigation:**

- First paint on every press/hold is ALWAYS the proxy (or thumb) — the
  navigation tier is untouched. Held-arrow stays at proxy speed.
- The high-res request is the lowest-priority settle job, subject to the
  spec/63 §3.1 coalescing / skip-ahead rule: if the user moves on before
  it finishes, it is dropped (never decoded for a flown-past item).
- Only when the user STOPS on an item, on a display whose target exceeds
  the proxy edge, does the worker upgrade that one item to a true-pixel
  decode.

The proxy stays a fast, modest first-paint tier — **no change to the
on-disk proxy size** (`PROXY_MAX_EDGE` unchanged), so the laptop's
`.cache/proxies/` cost is unchanged and its small targets never trigger
the upgrade. Big-screen sharpness comes from an on-demand, settle-only
original decode (the file is already present); the cost — a slightly
slower settle (decode bounded by the ceiling, on the worker, off the GUI
thread) and a larger in-RAM scaled entry — is paid only while a large
display is actually showing that item.

Native dims are still reported from the header probe (spec/63 §5), so
1:1 / box-zoom / crop math is unaffected.

### C. `display_quality` setting — Balanced / High

A single enum that sets the **display-target ceiling** (max long edge
the normal tier will decode to, on settle). DPR (§A) always applies on
top. **There is deliberately no "native / unbounded" option** — the
ceiling caps the settle decode so it can never become the full-frame
24–45 MP decode that spec/62 found to be the source of the lag.

| Value | Ceiling (long edge) | For |
|---|---|---|
| `balanced` (default) | 3840 px (4K-class) | sharp on a 4K monitor, cheap on a laptop (its target never reaches the ceiling, so the §2.B upgrade never fires) |
| `high` | 5120 px (5K-class) | 5K / 6K panels on a powerful desktop; opt-in, heavier settle decode |

Effective target long edge = `min(physical_target, native, ceiling)`.

**Why a setting + why per-machine.** Mira's `Settings` are stored
per-install (core/settings.py — NOT in the library root; the
implementer MUST confirm this, see §5), so the home desktop can sit on
`high` and the travelling laptop on `balanced` **without any
conflict and without touching the shared library**. That is the
"customised for both" answer: the same library roams; each machine keeps
its own display ceiling. Default `balanced` is already good on both — a
laptop never reaches 3840, and a 4K monitor gets true 4K.

> **RESOLVED (Nelson 2026-06-22): `display_quality` is machine-local,
> NOT in the roaming `Settings`.** Investigation confirmed `Settings`
> persist inside the library root — `mira/settings/repo.py`
> (`settings.rebuild.json` under `user_data_dir()`) → `mira/paths.py`
> resolves `user_data_dir()` to `<library_root>/.mira/`, which roams
> with the library (spec/76 §B.4). Putting `display_quality` there would
> mean last-writer-wins between the desktop and the laptop on a shared
> NAS library — the exact conflict this setting must avoid.
>
> Therefore `display_quality` lives in a tiny **per-install** override
> file at the OS-local config dir (the same place the library-root
> bootstrap pointer lives — `%LOCALAPPDATA%\Mira\` on Windows /
> `~/.config/mira/` elsewhere), NEVER inside the library root, and never
> under `MIRA_DATA_DIR`. Shape:
>
> - New Qt-free module `core/machine_settings.py` (respects charter
>   inv. 8 — no Qt in `core/`): `read_display_quality() -> "balanced" |
>   "high"` and `write_display_quality(value)`, atomic write-then-rename
>   (inv. 6), JSON envelope `{"display_quality": "balanced"}` in a
>   sibling file to the bootstrap pointer (e.g. `machine.json`).
> - Missing / corrupt / pre-first-read → default `"balanced"`. A
>   reinstall wipes it cleanly.
> - The Settings dialog Display tab reads/writes via these helpers, NOT
>   via `SettingsRepo`; the rest of `Settings` is untouched.
> - The photo viewport reads the ceiling via `read_display_quality()`
>   when computing the target.
>
> Net: desktop on `high`, laptop on `balanced`, both pointed at one
> roaming library, with no conflict.

## 3. Memory budget (LRU)

`photo_cache.py` caps the scaled-pixmap LRU at 32 entries sized for a
~2560 target (~17 MB each ≈ 0.5 GB). At `high` on a 4K+ display an
entry can be ~30–38 MB; 32 of them ≈ 1.2 GB.

Change the scaled LRU to a **byte budget** (cap total MB, evict oldest
until under budget) rather than a fixed entry count, so the memory
ceiling is stable regardless of the display-quality setting. Suggested
budget ≈ 512 MB (tunable); document it in Settings disk/memory honesty
like the proxy disk number (spec/63 §5).

## 4. What does NOT change

- Proxy on-disk size + quality (`PROXY_MAX_EDGE`, `PROXY_QUALITY`) — the
  proxy stays the fast first-paint tier. (An optional modest bump to
  ~2880 for nicer first-paint on the big monitor is allowed but
  separate; not required by this spec.)
- The F10 inspection lens (already true-pixel / native).
- The LOCKED keyboard map (spec/63 §4) and charter invariants
  (originals never mutated; proxies remain derived/regenerable).
- 1:1 / box-zoom / crop math (native dims still reported).

## 5. Build order (one slice = one eyeball)

1. **Machine-local setting (NOT roaming `Settings`).** Per the §C
   RESOLVED note: add `core/machine_settings.py` (Qt-free) with
   `read_display_quality()` / `write_display_quality()`, atomic
   write-then-rename, stored at the OS-local config dir beside the
   library-root bootstrap pointer (`machine.json`), default `"balanced"`.
   Expose it in `mira/ui/base/settings_dialog.py` as a `combo` (options
   `balanced`/`high`) in the Display/Viewing tab near `peaking_color` /
   `peaking_sensitivity`, reading/writing via those helpers — do NOT add
   the key to `mira/settings/model.py` / `SettingsRepo`.
2. **A — DPR.** `PhotoViewport._target_size()` × `devicePixelRatioF()`;
   tag the displayed pixmap with `setDevicePixelRatio`; clamp to native.
   Eyeball on a scaled display: normal view sharpens.
3. **B — settle-only original decode.** Worker serves proxy when
   `target ≤ proxy_edge`, else decodes the original down to target. Wire
   the ceiling from `display_quality` into the viewport's target
   computation. **Verify the upgrade is settle-only / coalesced** (held
   navigation still paints the proxy and never waits — spec/63 §3.1).
4. **LRU byte budget.** Convert the scaled LRU to an MB cap.
5. **Settings honesty.** Surface the chosen ceiling + the scaled-cache
   budget in the Settings disk/memory readout.

## 6. Tests

- `_target_size()` multiplies by DPR (fake `devicePixelRatioF` 1.0 / 1.5
  / 2.0) and clamps to native; never exceeds the `display_quality`
  ceiling.
- Worker scaled branch: `target ≤ proxy_edge` → proxy path;
  `target > proxy_edge` → original-decode path (assert the served
  image's long edge tracks the target, not 2560).
- **Navigation never waits:** a held-arrow run (rapid navigate) issues
  only proxy/placeholder paints; the original-decode upgrade is enqueued
  only on settle and is dropped when superseded (assert no original
  decode fires for a flown-past item).
- `balanced` caps at 3840, `high` at 5120; there is no native/unbounded
  option (the settle decode is always ceiling-bounded).
- Displayed pixmap carries the expected `devicePixelRatio`.
- Scaled LRU evicts by byte budget; a run of large (4K-ceiling) entries
  doesn't blow the cap.
- A sub-viewport-sized photo is shown at native size, never upscaled.
- Regression: 1:1 / box-zoom still reads native dims with B engaged.

## 7. Acceptance (Nelson eyeball)

- Big monitor, fullscreen, `balanced`: a 24 MP photo reads crisp (true
  4K), not soft.
- **No new lag:** held-arrow navigation on the big monitor is as fast as
  today (proxy paints; the original upgrade only fires after you stop).
- Laptop, `balanced`: same speed + cache footprint as today (proxy
  path), no regression.
- Switching the laptop to `balanced` and the desktop to `high`
  needs no library change and they don't fight each other.

## 8. Relationship to F10 (Full Resolution) — unchanged

F10 (the inspection lens, spec/63 §4 truth key / §5 Original tier) is
**not touched** by this spec. It still decodes the **full native**
original (RAW → honest half-res demosaic), with true 1:1 zoom, pan, and
focus peaking. spec/95 only raises the *fit-to-screen normal view*
ceiling (≤3840/5120, on settle).

Consequence: on a ≤4K monitor the normal settled view and F10 will look
essentially identical **at fit-to-screen** (both now come from the
original's pixels at fit size). F10's distinct value remains **zooming
to 1:1** — at 100 % you see real native detail (e.g. 6000 px of a 24 MP
frame) by panning, plus peaking — which the fit-bounded normal view
never shows. So F10 stays meaningful for pixel-level inspection; it just
stops being the only way to escape a soft 2560 fit.
