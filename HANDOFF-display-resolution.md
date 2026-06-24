# Handoff — adaptive display resolution (spec/95)

**Goal:** the normal viewing tier (non-F10) looks a bit soft on
large / HiDPI displays. Implement **spec/95**
(`spec/95-adaptive-display-resolution.md`) — read it in full before
coding; it governs.

Branch: **main** (the repo's only branch; CLAUDE.md's "XMC" is
conceptual, not a git branch). Photo engine = spec/63 (§5 tiers, §7
build order); this
change revises §5/§7 without touching the LOCKED keymap (§4) or the
charter invariants.

> Note: no code has been written yet — only the spec. You implement,
> test, and commit/push.

## HARD CONSTRAINT — do not bring back the lag

This proxy/cache pipeline (spec/62 audit → spec/63) exists because
switching photos was sluggish. The proxy stays the **navigation tier**:
every press/hold paints the proxy (or thumb) instantly. The heavier
original decode in §B is **settle-only** — it runs after navigation
STOPS, on the worker, lowest priority, and is dropped/superseded the
moment the user moves on (spec/63 §3.1 coalescing / skip-ahead).
Held-arrow speed must equal today's. **There is no "Maximum / native"
option** — that would reintroduce the full 24–45 MP decode spec/62
removed.

## What to do (A + B + setting)

**A. Honor `devicePixelRatio` (always).**
`mira/ui/media/photo_viewport.py`:
- `_target_size()` (~line 1699): multiply the viewport size by
  `self.devicePixelRatioF()` BEFORE the 512-px quantisation.
- In the display path (`_fit`/`_display`, `base.scaled(inner, …)`,
  ~line 1510): render at physical pixels and tag the displayed pixmap
  with `setDevicePixelRatio(dpr)` so Qt paints it 1:1.
- Never upscale beyond native: target long edge =
  `min(physical_target, native, ceiling)` (ceiling from the setting).

**B. Decode the ORIGINAL when the target exceeds the proxy edge — SETTLE ONLY.**
`mira/ui/media/photo_cache.py`, the worker's `scaled` branch (the
`_resolve_proxy` block, ~lines 277–304):
- `max(target_w, target_h) <= PROXY_MAX_EDGE` → serve from the proxy
  (today's fast path; the laptop's small targets always stay here).
- `max(target_w, target_h) > PROXY_MAX_EDGE` → `load_qimage(job.path,
  QSize(target_w, target_h))` (decode the original DOWN to target,
  never full native) instead of upscaling the proxy.
- **ANTI-LAG RULE (critical):** this original decode is **settle-only**.
  Every press/hold paints the proxy/placeholder immediately (navigation
  tier untouched); the upgrade is enqueued only when the user STOPS, at
  lowest priority, and is dropped if they move on (coalescing /
  skip-ahead, spec/63 §3.1). Held-arrow must stay at today's speed.
- Keep reporting native dims (header probe) — 1:1 / box-zoom unaffected.
- **No change to the on-disk proxy size** (`PROXY_MAX_EDGE` unchanged;
  the laptop's cost is unchanged and its small target never triggers
  the upgrade).

**C. `display_quality` setting (Balanced / High, default Balanced).**
Sets the long-edge ceiling of the normal tier (DPR from §A always on
top). **No "Maximum / native"** — the ceiling bounds the settle decode
so it never becomes the 24–45 MP decode that caused the lag (spec/62).
- `balanced` (default) → 3840 px (4K)
- `high` → 5120 px (5K)

Effective target long edge = `min(physical_target, native, ceiling)`.

**RESOLVED (2026-06-22): `display_quality` is machine-local, NOT in the
roaming `Settings`.** Investigation confirmed `Settings` persist inside
the library root (`settings.rebuild.json` under `<library_root>/.mira/`,
which roams per spec/76 §B.4), so putting the key there = last-writer-
wins between the desktop and laptop on a shared library. See spec/95 §C
(RESOLVED note).

Plumbing:
- New Qt-free module `core/machine_settings.py` (respects charter inv. 8):
  `read_display_quality() -> "balanced"|"high"` and
  `write_display_quality(value)`, atomic write-then-rename (inv. 6), JSON
  envelope `{"display_quality": "balanced"}` in a per-install file
  (`machine.json`) at the OS-local config dir beside the library-root
  bootstrap pointer (`%LOCALAPPDATA%\Mira\` on Windows /
  `~/.config/mira/` elsewhere). NEVER inside the library root, never
  under `MIRA_DATA_DIR`. Missing/corrupt → default `"balanced"`.
- `mira/ui/base/settings_dialog.py`: a `combo` entry with options
  `[("balanced","Balanced"), ("high","High")]` in the Display/Viewing
  tab near `peaking_color` / `peaking_sensitivity` (~line 634),
  reading/writing via the `core/machine_settings.py` helpers (NOT via
  `SettingsRepo`). `restart_required: False`. (No "Maximum".) The rest
  of `Settings` is untouched.
- The viewport reads the ceiling via `read_display_quality()` (map
  string→px) when computing the target.

**D. Scaled-LRU byte budget.**
`mira/ui/media/photo_cache.py`: the scaled LRU is currently entry-count
(32 entries ~17 MB @2560 ≈ 0.5 GB). At `high` on 4K+ each entry can be
~30–38 MB. Switch to a **byte budget** (cap total MB, evict oldest until
under budget). Suggested ~512 MB, tunable.

## Relationship to F10 (Full Resolution) — UNCHANGED

F10 (the inspection lens, spec/63 §4 truth key / §5 Original tier) is
NOT touched. It still decodes the full **native** original (RAW → honest
half-res demosaic), with true 1:1 zoom, pan, and focus peaking. spec/95
only raises the fit-to-screen normal ceiling (≤3840/5120, on settle).
Consequence: on a ≤4K monitor the normal settled view and F10 look
essentially identical at fit-to-screen; F10's distinct value remains
zooming to 1:1 (real native detail + peaking).

## Do NOT change

- `PROXY_MAX_EDGE` / `PROXY_QUALITY` (the proxy stays the fast
  first-paint tier). An optional modest bump (~2880) is separate and not
  required.
- F10 (already native).
- LOCKED keymap (spec/63 §4) and invariants (originals never mutated;
  proxies derived/regenerable).

## Tests to add

- `_target_size()` multiplies by DPR (fake `devicePixelRatioF` 1.0 / 1.5
  / 2.0) and clamps to native; never exceeds the `display_quality`
  ceiling.
- Worker: `target ≤ proxy_edge` → proxy path; `target > proxy_edge` →
  original-decode path (served long edge tracks the target, not 2560).
- **Navigation never waits:** a held-arrow run issues only
  proxy/placeholder paints; the original-decode upgrade is enqueued only
  on settle and is dropped when superseded (no original decode fires for
  a flown-past item).
- Ceilings: `balanced` = 3840, `high` = 5120; no native/unbounded option.
- The displayed pixmap carries the expected `devicePixelRatio`.
- Scaled LRU evicts by byte budget; a run of large (4K-ceiling) entries
  doesn't blow the cap.
- A sub-viewport-sized photo is shown at native size, never upscaled.
- Regression: 1:1 / box-zoom still read native dims with B engaged.
- `core/machine_settings.py`: round-trips `display_quality`; missing /
  corrupt file → default `"balanced"`; the value does NOT land in the
  roaming `Settings` / `settings.rebuild.json` (assert the library-root
  settings file is untouched).

Existing suites to run:
```
verify.bat tests\test_photo_proxy_cache.py
verify.bat tests\test_photo_cache.py    (if present; check the name)
```
plus any viewport/photo suite, then the full `verify.bat`.

## Acceptance (Nelson eyeball)

- Big monitor, fullscreen, `balanced`: a 24 MP photo reads crisp (true
  4K), not soft.
- No new lag: held-arrow navigation on the big monitor is as fast as
  today (proxy paints; the original upgrade fires only after you stop).
- Laptop, `balanced`: same speed + cache footprint as today (proxy path).
- Laptop on `balanced` + desktop on `high` need no library change and
  don't fight each other.

## Spec + commit

- The spec is at `spec/95-adaptive-display-resolution.md`. If you tune
  any numbers (ceilings, RAM budget), update the spec with the code
  (CLAUDE.md: spec before/with the code). Move status from PROPOSED to
  implemented once Nelson approves the eyeball.
- Suggested commit:

```
feat: adaptive display resolution — DPR-aware view + display_quality (spec/95)

- viewport: request scaled tier in physical px (devicePixelRatio) and
  tag the displayed pixmap so the normal view paints 1:1 on scaled /
  HiDPI displays; never upscale beyond native.
- photo_cache: when the display target exceeds the proxy edge, decode
  the original down to target (settle-only, coalesced — navigation still
  paints the proxy and never waits) instead of upscaling the 2560 proxy.
  Proxy stays the fast first-paint tier; on-disk proxy size unchanged.
- settings: display_quality (balanced/high, default balanced) caps the
  normal-view long edge; per-machine so a roaming library serves a big
  desktop and a small laptop from one setting each. No native/unbounded
  option (keeps the spec/62 anti-lag guarantee). F10 unchanged.
- photo_cache: scaled LRU is now a byte budget (stable RAM ceiling
  across display-quality levels).
```

Then: `git push` on branch `main`.
