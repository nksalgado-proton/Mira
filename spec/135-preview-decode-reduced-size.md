# 135 — Speed up preview/proxy generation: decode high-def JPEGs at reduced size

**Status: PROPOSED (Nelson 2026-06-23, perf — not a correctness bug). The
background preview/proxy generator (`ProxyBuilder` → `ensure_photo_proxy`,
and the develop preview `core.preview_render.develop_photo_array`) feels slow
(~1–2/sec) with high-definition photos in the mix. Cause: `decode_image`
decodes JPEGs at **full resolution** and only then downsamples to the
proxy/preview target (`PROXY_MAX_EDGE = 2560`, `_PREVIEW_MAX_LONG_EDGE` for
develop). A 6000-px JPEG pays full-res decode to make a 2560-px proxy — most
of that work is wasted. Fix: **decode at a reduced size** that still lands ≥
the target, then finish the resize. RAW already has `half_size`; extend the
idea to JPEG (cv2 `IMREAD_REDUCED_COLOR_2/4/8` or PIL `draft`). Optional
secondary: a small idle-time worker pool for the builder. Touches
`core/photo_decoder.py`, `core/photo_proxy_cache.py`,
`core/preview_render.py`. Correctness unchanged — proxies stay proxy-grade;
the F10 truth view still decodes real full-res pixels.**

## 1. Cause

- `decode_image(path, raw_half_size=False)` (photo_decoder.py) decodes JPEG
  full-res; only RAW has a reduced (`half_size`) path.
- `ensure_photo_proxy` then downsamples to ≤ 2560; `develop_photo_array`
  decodes full-res then `_downscale_if_huge` to `_PREVIEW_MAX_LONG_EDGE`.
- `ProxyBuilder` is a **single polite daemon thread** (spec/63 §7.7) that
  yields to foreground browsing — fine by design, but it means a big
  high-def seed processes serially at the decode rate.
- Net: the full-res decode of large JPEGs dominates → ~1–2/sec.

## 2. Primary fix — reduced-size decode

Add an optional **`target_long_edge`** to `decode_image`. When set and the
source is larger, decode at the **largest reduced scale whose result long
edge is still ≥ `target_long_edge`**, then let the caller finish the exact
resize:

- **JPEG:** cv2 `IMREAD_REDUCED_COLOR_2 / _4 / _8` (libjpeg DCT scaling — far
  cheaper than full decode), or PIL `Image.draft("RGB", size)`. Pick ÷2/÷4/÷8
  so the decoded long edge stays ≥ target (never below — a sub-target decode
  would soften the proxy; the existing `qualifies_as_proxy` guard already
  refuses to persist under-target pixels, so honour it).
- **RAW:** choose `half_size` when full-res long edge ≥ ~2× target; the
  embedded preview path stays as is.
- Callers pass their target: `ensure_photo_proxy` → `PROXY_MAX_EDGE`;
  `develop_photo_array` → `max_long_edge`. A 6000-px JPEG for a 2560 proxy
  decodes at ÷2 (3000 px) instead of 6000 — roughly 2–4× faster, and the
  final downsample to 2560 is unchanged in quality.

This is the bulk of the win and is safe: proxies/previews are bounded
outputs, so a reduced decode that stays ≥ target produces identical-grade
results.

## 3. Optional secondary — modest builder parallelism

If §2 alone isn't enough on very large high-def seeds, let `ProxyBuilder`
run a **small pool (2–3 workers)** while the foreground is idle, still
checking `is_busy()` between/within jobs so browsing keeps priority (the
politeness contract is preserved — it just uses spare idle cores). Keep it
conservative; do **not** regress foreground responsiveness. Flag as optional:
ship §2 first, measure, add this only if needed.

## 4. Acceptance

- Generating proxies/previews for high-def photos is materially faster
  (decode at reduced scale); measure before/after on a folder with several
  6000-px+ JPEGs.
- Proxy/preview output is visually identical (still downsampled to the same
  target; `qualifies_as_proxy` still holds — no under-target persisted).
- Foreground browsing stays as responsive as today (builder still yields).
- The F10 full-resolution truth view is unchanged (decodes real pixels).

## 5. Tests

- `tests/test_decode_reduced.py` — `decode_image(path, target_long_edge=T)`
  returns pixels whose long edge is ≥ T (never below) and ≤ full; a small
  source is decoded normally (no reduction); RAW picks half-size only when
  full ≥ ~2×T.
- `tests/test_proxy_quality_preserved.py` — a proxy built via the reduced
  path still satisfies `qualifies_as_proxy` (≥ min(native, PROXY_MAX_EDGE))
  and matches the full-decode proxy within a small tolerance.
- (If §3) a builder-parallelism test that foreground `is_busy()` still
  pauses the workers.
- Regress `develop_photo_array` output (same target, same look) within
  tolerance.
