"""spec/135 — reduced-size JPEG decode via ``target_long_edge``.

Pins:
* ``decode_image(path, target_long_edge=T)`` returns pixels whose long
  edge is ``≥ T`` (never below — would soften the proxy) and ``≤ full``.
* A small source (< T) is decoded full-size — no reduction, no
  upscaling.
* Pillow's JPEG ``draft`` chooses the largest divisor that keeps the
  result ≥ T (the optimal reduced scale per spec/135 §2).
* The reduced decode preserves EXIF orientation (the post-decode
  ``exif_transpose`` runs unchanged).
* RAW: ``half_size`` is promoted only when the sensor long edge is
  ≥ ~2×T (we mock rawpy so the test has no RAW fixture dep).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from core import photo_decoder
from core.photo_decoder import decode_image


def _save_jpeg(path: Path, w: int, h: int, *, quality: int = 95) -> None:
    """Synthetic JPEG with a directional gradient so any DCT-scaled
    decode still produces predictable content (no flat color)."""
    rng = np.random.default_rng(seed=42 ^ w ^ h)
    rgb = rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8)
    Image.fromarray(rgb).save(str(path), "JPEG", quality=quality)


# ── Basic invariants ───────────────────────────────────────────────────


def test_target_long_edge_none_is_full_decode(tmp_path):
    """No target → no reduction. Backwards-compatible default."""
    src = tmp_path / "big.jpg"
    _save_jpeg(src, 6000, 4000)
    out = decode_image(src)
    assert out.shape[:2] == (4000, 6000)


def test_target_long_edge_zero_is_full_decode(tmp_path):
    """A zero target is "no bound" — pass-through to full decode.
    Mirrors the convention ``_downscale_if_huge`` uses."""
    src = tmp_path / "big.jpg"
    _save_jpeg(src, 6000, 4000)
    out = decode_image(src, target_long_edge=0)
    assert out.shape[:2] == (4000, 6000)


def test_small_source_is_unreduced(tmp_path):
    """Source already smaller than the target — no reduction, no
    upscaling: the decoder returns the full-size image."""
    src = tmp_path / "small.jpg"
    _save_jpeg(src, 800, 600)
    out = decode_image(src, target_long_edge=2560)
    assert out.shape[:2] == (600, 800)


# ── Reduced-decode size invariants ─────────────────────────────────────


@pytest.mark.parametrize(
    "src_long, target, expected_long",
    [
        # 6000-px source, T=2560 → divisor 2 → 3000-px result.
        (6000, 2560, 3000),
        # 8000-px source, T=2560 → divisor 2 (÷4=2000<T).
        (8000, 2560, 4000),
        # 4096-px source, T=600 → divisor 4 (÷8=512<T, ÷4=1024≥T).
        (4096, 600, 1024),
        # 4096-px source, T=512 → divisor 8 (÷8=512=T, exact match).
        (4096, 512, 512),
        # Exactly 2× target → divisor 2 → exactly T.
        (5120, 2560, 2560),
        # 5119-px source, T=2560 → divisor 1 (5119//2=2559 < target).
        (5119, 2560, 5119),
    ],
    ids=[
        "6000@T2560-divides-2",
        "8000@T2560-divides-2-not-4",
        "4096@T600-divides-4-not-8",
        "4096@T512-divides-8-exact",
        "exact-2x-target",
        "below-2x-stays-full",
    ],
)
def test_reduced_jpeg_decode_long_edge_at_or_above_target(
    tmp_path, src_long, target, expected_long,
):
    """Pillow draft picks the LARGEST divisor whose result ≥ target.
    Pin the exact divisor selection for the common cases.

    Aspect 3:2 → height = 2/3 × width, so a (6000, 4000) ÷2 = (3000, 2000).
    Test the long edge; the short edge follows by aspect."""
    h = int(src_long * 2 / 3)
    src = tmp_path / "src.jpg"
    _save_jpeg(src, src_long, h)
    out = decode_image(src, target_long_edge=target)
    out_long = max(out.shape[0], out.shape[1])
    assert out_long == expected_long, (
        f"src_long={src_long} target={target} expected={expected_long} "
        f"got {out_long} (shape={out.shape})")


def test_reduced_decode_long_edge_never_below_target(tmp_path):
    """Acceptance — for any plausible large source, the decoded long
    edge is ≥ target. spec/135 §2: a sub-target decode would soften
    the proxy; ``qualifies_as_proxy`` would then refuse to persist.

    Sweep keeps source dimensions under Pillow's decompression-bomb
    guard (default ~178 M pixels), but exercises every divisor branch
    via the smaller-target case below."""
    # Big-T sweep — never goes below T=2560 for plausible camera sizes.
    target = 2560
    for src_long in (3000, 4000, 5120, 5121, 6000, 8000, 12000):
        h = int(src_long * 2 / 3)
        src = tmp_path / f"src_{src_long}.jpg"
        _save_jpeg(src, src_long, h)
        out = decode_image(src, target_long_edge=target)
        out_long = max(out.shape[0], out.shape[1])
        assert out_long >= target, (
            f"src_long={src_long} → out_long={out_long} < target={target}")
    # Small-T sweep exercises the ÷8 branch within bomb-safe sizes.
    target = 400
    for src_long in (400, 800, 1600, 3200, 4000, 6400):
        h = int(src_long * 2 / 3)
        src = tmp_path / f"src_T400_{src_long}.jpg"
        _save_jpeg(src, src_long, h)
        out = decode_image(src, target_long_edge=target)
        out_long = max(out.shape[0], out.shape[1])
        assert out_long >= target, (
            f"src_long={src_long} → out_long={out_long} < target={target}")


def test_reduced_decode_long_edge_never_above_full(tmp_path):
    """A reduced decode is never larger than full. (No accidental
    upscaling — the draft path never grows.)"""
    target = 2560
    for src_long in (1000, 2400, 4000, 8000):
        h = int(src_long * 2 / 3)
        src = tmp_path / f"src_{src_long}.jpg"
        _save_jpeg(src, src_long, h)
        out = decode_image(src, target_long_edge=target)
        out_long = max(out.shape[0], out.shape[1])
        assert out_long <= src_long


# ── EXIF orientation survives the reduced path ─────────────────────────


def test_reduced_decode_preserves_exif_orientation(tmp_path):
    """The orientation transpose runs AFTER the draft/load — a portrait
    EXIF tag on a landscape source still produces an upright image.
    Pin the dims-swap so the spec/135 path doesn't accidentally bypass
    ``exif_transpose``."""
    src = tmp_path / "rot.jpg"
    img = Image.new("RGB", (6000, 4000), (200, 50, 50))
    # Orientation 6 = "Rotate 90 CW" → decoder swaps to portrait on load.
    exif = img.getexif()
    exif[0x0112] = 6
    img.save(str(src), "JPEG", quality=95, exif=exif)
    out = decode_image(src, target_long_edge=2560)
    # After orientation, height > width (portrait). The reduced decode
    # divides by 2 → ~3000 long edge.
    assert out.shape[0] > out.shape[1], (
        f"orientation 6 should land portrait; got shape {out.shape}")
    out_long = max(out.shape[0], out.shape[1])
    assert out_long >= 2560
    assert out_long <= 6000


# ── PNG / TIFF ignore the hint cleanly (no draft) ──────────────────────


def test_png_ignores_target_long_edge(tmp_path):
    """PNG has no DCT; draft is a no-op. The decoder still returns a
    valid full-size array — the spec/135 hint must be defence in
    depth, not a hard contract."""
    src = tmp_path / "big.png"
    rng = np.random.default_rng(seed=0)
    rgb = rng.integers(0, 256, size=(800, 1000, 3), dtype=np.uint8)
    Image.fromarray(rgb).save(str(src), "PNG")
    out = decode_image(src, target_long_edge=256)
    # PNG decodes full-size (no draft DCT to scale).
    assert out.shape[:2] == (800, 1000)


# ── RAW half-size promotion (mocked rawpy) ─────────────────────────────


class _FakeRaw:
    """Minimal rawpy stand-in: records the ``half_size`` arg passed
    to ``postprocess`` so the test can assert the spec/135 promotion."""

    def __init__(self, full_long: int = 6000):
        self.sizes = SimpleNamespace(
            width=full_long, height=int(full_long * 2 / 3))
        self.calls: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def postprocess(self, *, use_camera_wb, no_auto_bright, output_bps,
                    gamma, half_size):
        self.calls.append({
            "half_size": bool(half_size),
            "use_camera_wb": use_camera_wb,
            "no_auto_bright": no_auto_bright,
            "output_bps": output_bps,
            "gamma": gamma,
        })
        # Return a small valid uint8 RGB array — content doesn't matter
        # for these tests; only the half_size kwarg does.
        return np.zeros((4, 6, 3), dtype=np.uint8)


def _stub_rawpy(monkeypatch, full_long: int) -> _FakeRaw:
    """Patch ``rawpy.imread`` so :func:`decode_image` runs the RAW path
    without needing a real .RW2/.NEF/.CR2 on disk."""
    fake = _FakeRaw(full_long=full_long)
    fake_module = SimpleNamespace(imread=lambda _p: fake)
    monkeypatch.setattr(
        "rawpy.imread", fake_module.imread, raising=False)
    return fake


def test_raw_promotes_to_half_size_when_full_is_at_least_2x_target(
    tmp_path, monkeypatch,
):
    """spec/135 §2 — RAW sensor ≥ 2× target → half_size demosaic."""
    fake = _stub_rawpy(monkeypatch, full_long=6000)
    src = tmp_path / "photo.rw2"
    src.write_bytes(b"\x00")            # path.exists() is the only check
    decode_image(src, target_long_edge=2560)
    assert fake.calls and fake.calls[0]["half_size"] is True


def test_raw_stays_full_when_below_2x_target(tmp_path, monkeypatch):
    """Sensor 4000 wide, target 2560 → 2×T=5120, sensor < 5120 →
    half_size would land at 2000 (below target). Stay full."""
    fake = _stub_rawpy(monkeypatch, full_long=4000)
    src = tmp_path / "photo.rw2"
    src.write_bytes(b"\x00")
    decode_image(src, target_long_edge=2560)
    assert fake.calls and fake.calls[0]["half_size"] is False


def test_raw_caller_half_size_wins(tmp_path, monkeypatch):
    """Caller's explicit ``raw_half_size=True`` is respected even when
    the sensor wouldn't qualify for promotion — the spec/135 path
    *promotes*, it never demotes."""
    fake = _stub_rawpy(monkeypatch, full_long=4000)
    src = tmp_path / "photo.rw2"
    src.write_bytes(b"\x00")
    decode_image(src, raw_half_size=True, target_long_edge=2560)
    assert fake.calls and fake.calls[0]["half_size"] is True


def test_raw_no_target_no_promotion(tmp_path, monkeypatch):
    """Without a target, the RAW path is exactly today's behaviour —
    half_size mirrors the caller's flag, sensor probe is skipped."""
    fake = _stub_rawpy(monkeypatch, full_long=6000)
    src = tmp_path / "photo.rw2"
    src.write_bytes(b"\x00")
    decode_image(src)
    assert fake.calls and fake.calls[0]["half_size"] is False


def test_raw_sensor_probe_failure_falls_through_to_full(
    tmp_path, monkeypatch,
):
    """If ``raw.sizes`` raises, the decoder logs + falls through to
    full demosaic — correctness wins on the probe failure."""
    class _BadSizesRaw(_FakeRaw):
        def __init__(self):
            self.calls = []
        @property
        def sizes(self):
            raise RuntimeError("simulated sizes probe failure")
    fake = _BadSizesRaw()
    monkeypatch.setattr(
        "rawpy.imread", lambda _p: fake, raising=False)
    src = tmp_path / "photo.rw2"
    src.write_bytes(b"\x00")
    decode_image(src, target_long_edge=2560)
    assert fake.calls and fake.calls[0]["half_size"] is False
