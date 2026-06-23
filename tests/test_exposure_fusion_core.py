"""Tests for the Qt-free exposure-fusion kernel (spec/109 §3).

The Qt adapter (``fuse_exposures``) keeps its own coverage in
``test_exposure_fusion.py``; this file is the kernel's contract — the
shape the merge job will feed and read at full res, plus the
``align=True`` AlignMTB pre-pass and the adapter→kernel delegation."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from core import exposure_fusion
from core.exposure_fusion import fuse_exposure_arrays, fuse_exposures


def _solid(value: int, w: int = 64, h: int = 48) -> np.ndarray:
    """A solid BGR uint8 frame filled with ``value``."""
    return np.full((h, w, 3), int(value), dtype=np.uint8)


def _varied(seed: int, w: int = 64, h: int = 48) -> np.ndarray:
    """A non-flat BGR uint8 frame — AlignMTB needs pixel variation to
    compute a median-threshold map."""
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, (h, w, 3), dtype=np.uint8)


# ── fuse_exposure_arrays — degradation contract ──────────────────


def test_empty_returns_empty_array():
    out = fuse_exposure_arrays([])
    assert isinstance(out, np.ndarray)
    assert out.shape == (0, 0, 3)
    assert out.dtype == np.uint8


def test_single_frame_returns_input_unchanged():
    one = _solid(120)
    assert fuse_exposure_arrays([one]) is one


def test_no_cv2_falls_back_to_first_frame(monkeypatch):
    monkeypatch.setattr(exposure_fusion, "cv2", None)
    a, b = _solid(40), _solid(200)
    assert fuse_exposure_arrays([a, b]) is a


def test_size_mismatch_is_normalised_to_first_frame():
    big = _solid(90, 100, 80)
    small = _solid(180, 50, 40)
    out = fuse_exposure_arrays([big, small], align=False)
    # Fused output adopts frame[0]'s (H, W) — a stray-mismatched
    # bracket normalises rather than crashing.
    assert out.shape[:2] == big.shape[:2]


def test_pipeline_failure_degrades_to_first_frame(monkeypatch):
    """Mertens blowing up returns the first frame, never raises."""
    first = _solid(70)

    class _Boom:
        def process(self, *_a, **_k):
            raise RuntimeError("synthetic merge failure")

    monkeypatch.setattr(
        exposure_fusion.cv2, "createMergeMertens", lambda *a, **k: _Boom(),
    )
    assert fuse_exposure_arrays([first, _solid(150)], align=False) is first


# ── fuse_exposure_arrays — Mertens output ────────────────────────


def test_three_frame_bracket_fuses_to_mid_toned_image():
    """A dark/mid/bright bracket fuses to a value strictly between the
    extremes — a tonemapped blend, never a copy of one frame."""
    dark, mid, bright = _solid(40), _solid(128), _solid(220)
    out = fuse_exposure_arrays([dark, mid, bright], align=False)
    assert out.shape == dark.shape
    assert out.dtype == np.uint8
    m = float(out.mean())
    assert float(dark.mean()) < m < float(bright.mean())


def test_align_true_runs_without_error():
    """``align=True`` puts the bracket through ``cv2.AlignMTB`` first.
    Solid frames are degenerate input for the median-threshold
    bitmap, so we use varied pixels — and assert only that the call
    succeeds + returns a valid BGR uint8 frame at the first-frame
    size, not the specific alignment offset (that's cv2's
    contract)."""
    a, b, c = _varied(1), _varied(2), _varied(3)
    out = fuse_exposure_arrays([a, b, c], align=True)
    assert out.shape == a.shape
    assert out.dtype == np.uint8


def test_align_failure_falls_through_to_unaligned(monkeypatch):
    """A failing ``createAlignMTB`` must not abort the fusion — the
    kernel logs and falls back to the unaligned bracket so the merge
    still produces a master."""
    a, b, c = _solid(40), _solid(128), _solid(220)

    def _broken_aligner():
        raise RuntimeError("synthetic align failure")

    monkeypatch.setattr(
        exposure_fusion.cv2, "createAlignMTB", _broken_aligner,
    )
    out = fuse_exposure_arrays([a, b, c], align=True)
    assert out.shape == a.shape
    # Unaligned bracket of dark/mid/bright still fuses to a mid tone
    # (alignment is the no-op for matched-framing input anyway).
    m = float(out.mean())
    assert float(a.mean()) < m < float(c.mean())


# ── Qt adapter delegates to the kernel ───────────────────────────


def test_qt_wrapper_delegates_to_core(qapp):
    """``fuse_exposures(list[QPixmap])`` is a thin adapter — it must
    call into :func:`fuse_exposure_arrays` with ``align=False`` (the
    preview stays snappy; the merge job is the one that aligns)."""
    from PyQt6.QtGui import QColor, QImage, QPixmap

    def _pixmap(value: int, w: int = 32, h: int = 24) -> QPixmap:
        img = QImage(w, h, QImage.Format.Format_RGB888)
        img.fill(QColor(value, value, value))
        return QPixmap.fromImage(img)

    captured = {}
    real_kernel = exposure_fusion.fuse_exposure_arrays

    def _spy(arrays, *, align=True):
        captured["align"] = align
        captured["n_frames"] = len(arrays)
        captured["shapes"] = [a.shape for a in arrays]
        return real_kernel(arrays, align=align)

    with patch.object(exposure_fusion, "fuse_exposure_arrays", _spy):
        out = fuse_exposures([_pixmap(40), _pixmap(128), _pixmap(220)])

    assert not out.isNull()
    assert captured["align"] is False
    assert captured["n_frames"] == 3
    # The kernel got BGR uint8 frames at the first pixmap's size.
    assert all(s == (24, 32, 3) for s in captured["shapes"])
