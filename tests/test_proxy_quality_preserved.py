"""spec/135 — the reduced-decode path produces proxy-grade output.

Two contracts:
* A proxy built via :func:`ensure_photo_proxy` (which already uses
  ``Image.draft`` internally) satisfies :func:`qualifies_as_proxy`
  for the native dims — the long edge reaches
  ``min(native_long, PROXY_MAX_EDGE)``.
* Decoding directly via ``decode_image(path, target_long_edge=T)``
  then downsampling to exactly T produces visually equivalent pixels
  to the full-decode + downsample baseline (within a small per-pixel
  tolerance — JPEG DCT round-trip is the dominant source of error,
  and the reduced decode + LANCZOS-to-target is on the same order as
  the full decode + LANCZOS-to-target).
* ``develop_photo_array`` against the spec/135 path produces the
  same look + same bound as before (regress the pipeline).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from core.photo_decoder import decode_image
from core.photo_proxy_cache import (
    PROXY_MAX_EDGE,
    ensure_photo_proxy,
    qualifies_as_proxy,
    resolve_proxy,
)
from core.preview_render import develop_photo_array


def _save_jpeg(path: Path, w: int, h: int, *, quality: int = 95) -> None:
    """Synthetic JPEG with a known-content gradient so DCT round-trip
    error stays bounded + measurable."""
    rng = np.random.default_rng(seed=12345 ^ w ^ h)
    rgb = rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8)
    Image.fromarray(rgb).save(str(path), "JPEG", quality=quality)


def _save_smooth_jpeg(path: Path, w: int, h: int) -> None:
    """A smooth gradient JPEG — DCT round-trip error is much lower than
    on random noise, so the tolerance budget for the equivalence test
    is honest about real-world photo behaviour."""
    y = np.linspace(0, 255, h, dtype=np.float32)[:, None]
    x = np.linspace(0, 255, w, dtype=np.float32)[None, :]
    r = np.clip(x, 0, 255).astype(np.uint8)
    g = np.clip(y, 0, 255).astype(np.uint8)
    b = np.clip((x + y) / 2, 0, 255).astype(np.uint8)
    rgb = np.stack(
        [np.broadcast_to(r, (h, w)), np.broadcast_to(g, (h, w)),
         np.broadcast_to(b, (h, w))], axis=-1)
    Image.fromarray(rgb).save(str(path), "JPEG", quality=95)


# ── Proxy builder still produces proxy-grade output ────────────────────


def test_ensure_photo_proxy_satisfies_qualifies_as_proxy(tmp_path):
    """Acceptance — a proxy built via the existing reduced-decode path
    satisfies :func:`qualifies_as_proxy`. spec/135 is about NOT
    regressing this when the same draft hint propagates."""
    src = tmp_path / "big.jpg"
    _save_jpeg(src, 6000, 4000)
    event_root = tmp_path / "event"
    sha = "a" * 64
    ok = ensure_photo_proxy(event_root, src, sha)
    assert ok is True
    hit = resolve_proxy(event_root, sha, src)
    assert hit is not None
    # Native dims must be the ORIGINAL's post-orientation dims.
    assert (hit.native_w, hit.native_h) == (6000, 4000)
    # The proxy on disk must clear the qualifies_as_proxy bar.
    with Image.open(hit.path) as proxy_im:
        proxy_w, proxy_h = proxy_im.size
    assert qualifies_as_proxy(
        proxy_w, proxy_h, hit.native_w, hit.native_h), (
            f"proxy ({proxy_w}×{proxy_h}) fails qualifies_as_proxy "
            f"against native ({hit.native_w}×{hit.native_h})")


def test_proxy_long_edge_caps_at_proxy_max_edge(tmp_path):
    """The proxy on disk doesn't exceed PROXY_MAX_EDGE on the long
    edge (the bound is the whole point of the cache)."""
    src = tmp_path / "big.jpg"
    _save_jpeg(src, 6000, 4000)
    event_root = tmp_path / "event"
    sha = "b" * 64
    assert ensure_photo_proxy(event_root, src, sha)
    hit = resolve_proxy(event_root, sha, src)
    with Image.open(hit.path) as proxy_im:
        proxy_long = max(proxy_im.size)
    assert proxy_long <= PROXY_MAX_EDGE


# ── decode_image(target_long_edge=T) matches full-decode within tol ───


def test_reduced_then_downsample_matches_full_then_downsample(tmp_path):
    """Decode reduced + downsample-to-T should land within a small
    per-pixel tolerance of decode-full + downsample-to-T. The reduced
    path takes a different code branch through libjpeg so byte-for-
    byte equality isn't expected; visual equivalence is."""
    target = 2560
    src = tmp_path / "smooth.jpg"
    _save_smooth_jpeg(src, 6000, 4000)

    full = decode_image(src)                            # full-res decode
    reduced = decode_image(src, target_long_edge=target)

    # The reduced result lands ≥ target on the long edge.
    assert max(reduced.shape[:2]) >= target

    # Resize both to the same target via PIL LANCZOS — the comparable
    # point that any caller would arrive at.
    def _resize(arr: np.ndarray, T: int) -> np.ndarray:
        h, w = arr.shape[:2]
        scale = T / float(max(h, w))
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        return np.asarray(
            Image.fromarray(arr).resize(
                (new_w, new_h), Image.LANCZOS))

    full_proxy = _resize(full, target)
    reduced_proxy = _resize(reduced, target)
    # Sizes must match (both at target).
    assert full_proxy.shape == reduced_proxy.shape

    # Per-pixel absolute difference on uint8 — smooth gradient + JPEG
    # round-trip + two LANCZOS passes. 6 / 255 mean (~2.4%) is well
    # above the noise floor; tighten the bound if a future libjpeg
    # tweak narrows the gap.
    diff = np.abs(
        full_proxy.astype(np.int16) - reduced_proxy.astype(np.int16))
    mean_diff = float(diff.mean())
    assert mean_diff < 6.0, (
        f"reduced-path proxy differs by {mean_diff:.2f} mean per "
        "channel — expected close-to-equivalent (tolerance 6 / 255)")


# ── develop_photo_array regress: same look, same bound ─────────────────


def test_develop_photo_array_respects_max_long_edge(tmp_path):
    """Regress — the preview-render output is still bounded to
    ``max_long_edge`` after spec/135 (the explicit downscale post-
    decode keeps the bound exact, regardless of what the reduced
    decode landed at)."""
    src = tmp_path / "src.jpg"
    _save_smooth_jpeg(src, 6000, 4000)
    out = develop_photo_array(src, adjustment=None, max_long_edge=1200)
    assert out is not None
    assert max(out.shape[:2]) <= 1200


def test_develop_photo_array_unchanged_on_small_source(tmp_path):
    """A small source (< max_long_edge) decodes full and is returned
    intact through the pipeline (modulo any look — None here)."""
    src = tmp_path / "small.jpg"
    _save_smooth_jpeg(src, 800, 600)
    out = develop_photo_array(src, adjustment=None, max_long_edge=1200)
    assert out is not None
    assert out.shape[:2] == (600, 800)


def test_develop_photo_array_reduced_and_full_paths_equivalent(tmp_path):
    """Develop the same source twice — once via the spec/135 default
    (reduced decode) and once via the full-decode path (forced by
    passing 0 for max_long_edge then resizing). The pipeline output
    should be near-equivalent (small JPEG round-trip + interpolation
    difference)."""
    src = tmp_path / "smooth.jpg"
    _save_smooth_jpeg(src, 6000, 4000)
    target = 2400

    # spec/135 path — reduced decode, bounded to target.
    spec135 = develop_photo_array(
        src, adjustment=None, max_long_edge=target)
    assert spec135 is not None
    assert max(spec135.shape[:2]) <= target

    # Full-decode baseline — pass max_long_edge=0 to skip both the
    # reduced-decode and the downscale, then resize ourselves.
    baseline_full = develop_photo_array(
        src, adjustment=None, max_long_edge=0)
    assert baseline_full is not None
    h, w = baseline_full.shape[:2]
    scale = target / float(max(h, w))
    baseline_proxy = np.asarray(
        Image.fromarray(baseline_full).resize(
            (max(1, int(round(w * scale))),
             max(1, int(round(h * scale)))),
            Image.LANCZOS))

    assert spec135.shape == baseline_proxy.shape
    diff = np.abs(
        spec135.astype(np.int16) - baseline_proxy.astype(np.int16))
    mean_diff = float(diff.mean())
    assert mean_diff < 6.0, (
        f"develop output via reduced decode differs by {mean_diff:.2f} "
        "mean per channel from the full-decode baseline (tolerance 6)")
