"""core.focus_peaking — the full-resolution BINARY mask primitives
(spec/63 F10 lens, 2026-06-12 round 5).

The lens computes ONE binary mask at the honest source's resolution and
derives every view from it: the fit overview by dilate+downscale (thin
pixel-level edges must SURVIVE the downscale — the dilation pin below
is the whole point), the 1:1 zoom by a straight slice.
"""
from __future__ import annotations

import numpy as np
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap

from core.focus_peaking import (
    compute_peaking_binary,
    overlay_from_binary,
    scale_binary_mask,
)


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _checkerboard_pixmap(w=400, h=300, cell=2):
    """Half flat grey, half fine checkerboard — pixel-scale detail the
    Laplacian must light up, beside a region it must leave dark."""
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(128, 128, 128))
    p = QPainter(img)
    for y in range(0, h, cell):
        for x in range(w // 2, w, cell):
            if ((x // cell) + (y // cell)) % 2 == 0:
                p.fillRect(x, y, cell, cell, QColor(230, 230, 230))
    p.end()
    return QPixmap.fromImage(img)


def test_binary_mask_is_source_resolution_and_localised(qapp):
    pm = _checkerboard_pixmap(400, 300)
    mask = compute_peaking_binary(pm, sensitivity=60)
    assert mask is not None
    assert mask.shape == (300, 400)              # the SOURCE's resolution
    assert mask.dtype == np.uint8
    left = mask[:, : 180]                        # flat grey → dark
    right = mask[:, 220:]                        # fine detail → lit
    assert np.count_nonzero(left) == 0
    assert np.count_nonzero(right) > right.size * 0.05


def test_binary_pre_blur_rejects_sensor_noise(qapp):
    """The Nelson 2026-06-12 root cause: an un-denoised RAW demosaic is
    a field of single-pixel noise spikes that exceed ANY threshold over
    10-20% of the frame. The full-res path pre-blurs (3×3) before the
    Laplacian — real optical edges span several source pixels and
    survive; single-pixel noise dies. A pure noise field must read as
    NOT sharp at the strict end AND at the default."""
    rng = np.random.default_rng(20260612)
    noise = rng.normal(128.0, 12.0, size=(300, 400)).clip(0, 255)
    arr = noise.astype(np.uint8)
    rgb = np.repeat(arr[..., None], 3, axis=2).copy()
    img = QImage(rgb.tobytes(), 400, 300, 1200, QImage.Format.Format_RGB888)
    pm = QPixmap.fromImage(img.copy())

    strict = compute_peaking_binary(pm, sensitivity=0)
    default = compute_peaking_binary(pm, sensitivity=50)
    assert strict is not None and default is not None
    assert np.count_nonzero(strict) / strict.size < 0.005
    # At the default the residual noise tail is SCATTERED single
    # pixels (a few percent at full res — honest per-pixel truth for
    # the 1:1 view)…
    assert np.count_nonzero(default) / default.size < 0.10
    # …and the user-facing claim: the FIT overview of a pure noise
    # field reads (near-)dark — a few stray specks where blur-paired
    # noise clusters, camera-peaking-like, but NEVER an area wash.
    # This is the property that was broken on 2026-06-12 ("the whole
    # photo gets peaked"). Final speckle calibration is the eyeball
    # loop on real RAWs (cutoff/σ are the one-number knobs).
    fit = scale_binary_mask(default, 100, 75)
    assert np.count_nonzero(fit) / fit.size < 0.03


def test_binary_mask_sensitivity_is_monotonic(qapp):
    pm = _checkerboard_pixmap()
    strict = compute_peaking_binary(pm, sensitivity=10)
    permissive = compute_peaking_binary(pm, sensitivity=90)
    assert np.count_nonzero(permissive) >= np.count_nonzero(strict)


def test_binary_mask_none_on_null_source(qapp):
    assert compute_peaking_binary(QPixmap()) is None


def test_scale_keeps_true_edges_visible_on_downscale(qapp):
    """The survival pins (post noise-calibration, cutoff ≈24%): a 1-px
    TRUE edge survives a 4× fit (25% of its block), a 2-px edge an 8×
    fit — beyond that the overview is a density map and 1:1 carries
    the per-pixel truth."""
    mask = np.zeros((400, 400), dtype=np.uint8)
    mask[200, :] = 255                           # 1-px line at 4×
    scaled = scale_binary_mask(mask, 100, 100)
    assert scaled.shape == (100, 100)
    assert set(np.unique(scaled)) <= {0, 255}    # binary stays binary
    assert np.count_nonzero(scaled) >= 90

    mask = np.zeros((800, 800), dtype=np.uint8)
    mask[400:402, :] = 255                       # 2-px line at 8×
    scaled = scale_binary_mask(mask, 100, 100)
    assert np.count_nonzero(scaled) >= 90


def test_scale_keeps_scattered_noise_dark(qapp):
    """The Nelson 2026-06-12 regression ("even with 0 sensitivity the
    whole photo gets peaked"): the first build DILATED by the scale
    ratio — a k² coverage amplifier that turned ~3% noise speckle into
    a fully-lit frame. The density path keeps sparse speckle dark while
    the thin-line pin above still passes."""
    mask = np.zeros((800, 800), dtype=np.uint8)
    flat = mask.reshape(-1)
    flat[::33] = 255                             # ~3% scattered speckle
    assert 0.02 < np.count_nonzero(mask) / mask.size < 0.04
    scaled = scale_binary_mask(mask, 100, 100)
    assert np.count_nonzero(scaled) == 0         # the overview stays dark


def test_scale_upscales_without_dilation_artifacts(qapp):
    mask = np.zeros((50, 50), dtype=np.uint8)
    mask[10:12, 10:12] = 255
    scaled = scale_binary_mask(mask, 200, 200)
    assert scaled.shape == (200, 200)
    # 2×2 lit block at 4× nearest → ~8×8 lit; nothing exploded.
    lit = np.count_nonzero(scaled)
    assert 32 <= lit <= 128


def test_overlay_from_binary_colours_lit_pixels_only(qapp):
    mask = np.zeros((20, 30), dtype=np.uint8)
    mask[5, 7] = 255
    overlay = overlay_from_binary(
        mask, color=(255, 0, 255), opacity=0.7)
    assert overlay.width() == 30 and overlay.height() == 20
    img = overlay.toImage()
    lit = img.pixelColor(7, 5)
    dark = img.pixelColor(0, 0)
    assert (lit.red(), lit.green(), lit.blue()) == (255, 0, 255)
    assert lit.alpha() == int(round(0.7 * 255))
    assert dark.alpha() == 0
