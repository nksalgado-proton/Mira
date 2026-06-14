"""Tests for core.exposure_fusion.fuse_exposures.

The Combined-preview kernel (docs/18 §"Bracket surfaces") — a
decision aid, never an output. Covers the contract that matters for
a preview that must NEVER crash the culler: graceful degradation on
0 / 1 frame and no-cv2, size normalisation across a stray mismatch,
and that a real 3-frame bracket fuses to a sane single image.
"""

from __future__ import annotations

import numpy as np
import pytest

from PyQt6.QtGui import QColor, QImage, QPixmap

from core import exposure_fusion
from core.exposure_fusion import fuse_exposures


def _solid(value: int, w: int = 64, h: int = 48) -> QPixmap:
    """A solid grey QPixmap at ``value`` (0-255)."""
    img = QImage(w, h, QImage.Format.Format_RGB888)
    img.fill(QColor(int(value), int(value), int(value)))
    return QPixmap.fromImage(img)


def _mean(pm: QPixmap) -> float:
    img = pm.toImage().convertToFormat(QImage.Format.Format_RGB888)
    bpl = img.bytesPerLine()
    ptr = img.bits()
    ptr.setsize(bpl * img.height())
    raw = np.frombuffer(bytes(ptr), dtype=np.uint8).reshape(
        (img.height(), bpl)
    )
    return float(raw[:, : img.width() * 3].mean())


def test_empty_returns_null(qapp):
    assert fuse_exposures([]).isNull()


def test_single_frame_returned_unchanged(qapp):
    one = _solid(120)
    assert fuse_exposures([one]) is one


def test_no_cv2_falls_back_to_first_frame(qapp, monkeypatch):
    monkeypatch.setattr(exposure_fusion, "cv2", None)
    a, b = _solid(40), _solid(200)
    assert fuse_exposures([a, b]) is a


def test_three_frame_bracket_fuses_to_sane_image(qapp):
    dark, mid, bright = _solid(40), _solid(128), _solid(220)
    out = fuse_exposures([dark, mid, bright])
    assert not out.isNull()
    assert out.size() == dark.size()             # first frame's size
    # Fusion blends — the result sits between the extremes, never
    # outside them (a tonemapped look, not a copy of one frame).
    m = _mean(out)
    assert _mean(dark) < m < _mean(bright)


def test_size_mismatch_is_normalised_not_fatal(qapp):
    big = _solid(90, 100, 80)
    small = _solid(180, 50, 40)
    out = fuse_exposures([big, small])
    assert not out.isNull()
    assert out.size() == big.size()              # resized to frame[0]


def test_never_raises_on_garbage(qapp, monkeypatch):
    """Any pipeline blow-up degrades to the first frame, never
    propagates (a preview must not kill the culler)."""
    first = _solid(70)

    class _Boom:
        def process(self, *_a, **_k):
            raise RuntimeError("synthetic merge failure")

    monkeypatch.setattr(
        exposure_fusion.cv2, "createMergeMertens",
        lambda *a, **k: _Boom(),
    )
    assert fuse_exposures([first, _solid(150)]) is first
