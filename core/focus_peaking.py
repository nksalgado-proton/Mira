"""Focus-peaking overlay computation — Laplacian implementation.

Focus peaking highlights in-focus areas of a photo with a coloured
overlay — a visual aid for the cull's second pass where the user
picks the sharpest of similar shots.

**Algorithm (port from `PhotosWorkflow&DataManager/culler/focus_tools.py`,
Nelson 2026-06-06).** The legacy/rebuild's prior Sobel-based pipeline
(with Gaussian pre-blur and NMS ridge-thinning) over-suppressed exactly
the fine detail that signals focus on textured surfaces (skin, hair,
feathers, fabric). The prototype's simpler Laplacian — same kernel PIL
uses in `ImageFilter.FIND_EDGES`, applied without any pre-blur — caught
those cases. We restore it:

1. Convert to grayscale.
2. Apply the 8-neighbour Laplacian kernel
   ``[[-1,-1,-1],[-1,8,-1],[-1,-1,-1]]`` via ``cv2.filter2D`` with
   ``CV_8U`` output. saturate_cast clamps negative responses to 0 (PIL
   ``FIND_EDGES`` parity) and >255 to 255 — the visible signal is
   positive-going edges.
3. Threshold: pixels whose response exceeds ``threshold`` are "in
   focus".
4. Build the overlay RGBA pixmap: lit pixels carry the user's chosen
   colour at the configured opacity (default 0.7).

The Laplacian responds to second-derivative content — fine textures,
isolated bright features, dense detail — not just to first-derivative
edges. That is why it catches focus that doesn't sit on a hard boundary.

Two public entry points share the same algorithm and differ only in how
they parametrise the threshold:

* :func:`compute_peaking_mask` — direct ``threshold`` parameter (used
  by the focus-bracket "stack-film" overlay where the host has already
  computed the threshold from the slider).
* :func:`compute_peaking_absolute` — accepts a ``sensitivity`` in
  0-100 and maps it to a threshold (sensitivity 50 = prototype default
  threshold 30; higher = more lit).

Pure logic (Qt-free except for QPixmap/QImage marshalling). Never
raises — callers can always composite without try/except wrappers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

try:  # pragma: no cover — opencv is a hard dep; keep import guarded so
      # trimmed unit-test environments can still import the rest of the
      # module without crashing at import time.
    import cv2  # type: ignore
except ImportError:    # pragma: no cover
    cv2 = None        # type: ignore

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QPixmap


log = logging.getLogger(__name__)


# Fixed palette per docs/16. Stored as (R, G, B) tuples; the caller
# picks one and converts to QColor when needed.
PEAKING_COLOR_MAGENTA: tuple[int, int, int] = (255, 0, 255)
PEAKING_COLOR_YELLOW:  tuple[int, int, int] = (255, 255, 0)
PEAKING_COLOR_RED:     tuple[int, int, int] = (255, 48, 48)
PEAKING_COLOR_CYAN:    tuple[int, int, int] = (0, 255, 255)

PEAKING_COLORS: dict[str, tuple[int, int, int]] = {
    "magenta": PEAKING_COLOR_MAGENTA,
    "yellow":  PEAKING_COLOR_YELLOW,
    "red":     PEAKING_COLOR_RED,
    "cyan":    PEAKING_COLOR_CYAN,
}

# Default colour name used when the host hasn't picked one yet.
PEAKING_COLOR_DEFAULT = "magenta"

# Default Laplacian-response threshold (0-255). Ported from the
# prototype's ``apply_peaking(threshold=30)``: low enough that a wide
# range of in-focus content lights up, high enough to suppress sensor
# noise on a flat region.
PEAKING_THRESHOLD_DEFAULT = 30

# Slider middle (Settings → "Default state for untouched items" /
# culler chrome slider). 50 = prototype's default visual feel.
PEAKING_SENSITIVITY_DEFAULT = 50

# Overlay opacity (0.0-1.0). 0.7 (prototype default) keeps the peaking
# colour vivid while still letting the photo read through; full opacity
# blots out the underlying detail and hurts the at-a-glance check.
PEAKING_OPACITY_DEFAULT = 0.7


# 8-neighbour Laplacian kernel — byte-identical to PIL's
# ``ImageFilter.FIND_EDGES``. Sums to zero so a flat region produces a
# flat zero response (no spurious "edges" on plain backgrounds).
_LAPLACIAN_KERNEL_8 = np.array(
    [[-1, -1, -1],
     [-1,  8, -1],
     [-1, -1, -1]],
    dtype=np.float32,
)


# Sensitivity → threshold mapping. Anchored so the slider midpoint
# (sens=50) lands at the prototype's default threshold 30, the strict
# end (sens=0) at 60 (only the strongest features), and the permissive
# end (sens=100) at 5 (almost everything responsive lights). Piecewise
# linear so the midpoint anchor is exact.
_SENS_THRESH_AT_ZERO = 60.0
_SENS_THRESH_AT_MID  = 30.0
_SENS_THRESH_AT_FULL = 5.0


def threshold_from_sensitivity(sensitivity: float) -> int:
    """Map slider sensitivity (0-100) to a Laplacian-response threshold
    (0-255). Slider centre lands at the prototype's default threshold,
    so the slider has intuitive teeth across its full range."""
    s = max(0.0, min(100.0, float(sensitivity)))
    if s <= 50.0:
        # 0 → 60, 50 → 30 (linear).
        t = _SENS_THRESH_AT_ZERO - (s / 50.0) * (
            _SENS_THRESH_AT_ZERO - _SENS_THRESH_AT_MID
        )
    else:
        # 50 → 30, 100 → 5 (linear).
        t = _SENS_THRESH_AT_MID - ((s - 50.0) / 50.0) * (
            _SENS_THRESH_AT_MID - _SENS_THRESH_AT_FULL
        )
    return int(round(t))


@dataclass
class PeakingResult:
    """Outcome of computing a peaking overlay.

    ``mask`` — the QPixmap to composite on top of the source. Same size
    as the source; transparent everywhere except on detected in-focus
    pixels, which carry the peaking colour at the configured opacity.
    ``coverage`` — fraction of pixels lit by the mask (0.0-1.0). Useful
    for tests and future telemetry.
    """

    mask: QPixmap
    coverage: float


def compute_peaking_mask(
    source: QPixmap,
    *,
    color: tuple[int, int, int] = PEAKING_COLOR_MAGENTA,
    threshold: int = PEAKING_THRESHOLD_DEFAULT,
    opacity: Optional[float] = None,
) -> PeakingResult:
    """Laplacian focus peaking — direct threshold parametrisation.

    Returns a ``PeakingResult`` whose ``mask`` is the same size as
    ``source`` with the peaking colour painted at ``opacity`` on
    pixels whose 8-neighbour Laplacian response exceeds ``threshold``.
    Non-lit pixels are fully transparent so compositing preserves the
    original photo.

    Threshold is in the 0-255 range of the (saturate_cast'd) Laplacian
    response. Lower = more pixels light up (more sensitive).

    Returns an empty (transparent) mask if cv2 is unavailable, the
    source is null, or anything in the pipeline fails — never raises,
    so the caller can always composite without error handling.
    """
    if source is None or source.isNull():
        return PeakingResult(mask=QPixmap(), coverage=0.0)
    if cv2 is None:
        log.warning("cv2 unavailable — focus peaking returns empty mask")
        return PeakingResult(
            mask=_empty_mask(source.width(), source.height()),
            coverage=0.0,
        )

    # opacity=None reads the user-tunable Setting (Nelson 2026-06-09
    # audit promotion); fall back to PEAKING_OPACITY_DEFAULT.
    if opacity is None:
        try:
            from mira.settings.repo import SettingsRepo
            opacity = float(SettingsRepo().load().focus_peaking_opacity)
        except Exception:                                       # noqa: BLE001
            opacity = PEAKING_OPACITY_DEFAULT

    try:
        bgr = _pixmap_to_array(source)
        mask = _laplacian_mask(bgr, int(threshold))
        rgba = _mask_to_rgba(mask, color, opacity)
        coverage = float(np.count_nonzero(mask)) / float(mask.size)
        return PeakingResult(mask=rgba, coverage=coverage)
    except Exception as exc:    # noqa: BLE001 — must not crash UI
        log.warning("Focus-peaking computation failed: %s", exc)
        return PeakingResult(
            mask=_empty_mask(source.width(), source.height()),
            coverage=0.0,
        )


def compute_peaking_absolute(
    source: QPixmap,
    *,
    color: tuple[int, int, int] = PEAKING_COLOR_MAGENTA,
    sensitivity: float = PEAKING_SENSITIVITY_DEFAULT,
) -> PeakingResult:
    """Sensitivity-parametrised variant of :func:`compute_peaking_mask`.

    Same Laplacian algorithm; ``sensitivity`` (0-100) maps to the
    underlying threshold via :func:`threshold_from_sensitivity` so the
    slider has intuitive feel across its full range (centre = prototype
    default, ends behave as you would expect).
    """
    return compute_peaking_mask(
        source,
        color=color,
        threshold=threshold_from_sensitivity(sensitivity),
    )


# ── Full-resolution binary peaking (the F10 lens, 2026-06-12 round 5) ─
# The lens computes the mask ONCE on the honest source pixels and
# derives every view from it: the fit overview by DENSITY downscale
# (a display pixel lights when enough of its source block is lit —
# thin true edges survive, scattered noise does not, and display
# smoothing can no longer invent edges that aren't sharp at pixel
# scale), the 1:1 zoom by a straight slice — so the overview and the
# zoom finally agree about what is sharp.


def compute_peaking_binary(
    source: QPixmap,
    *,
    sensitivity: float = PEAKING_SENSITIVITY_DEFAULT,
) -> Optional[np.ndarray]:
    """The BINARY peaking mask (uint8 0/255, shape H×W) at the
    source's own resolution. ``None`` when cv2 is unavailable, the
    source is null, or the pipeline fails — never raises."""
    if source is None or source.isNull() or cv2 is None:
        return None
    try:
        bgr = _pixmap_to_array(source)
        # FULL-RES sources take a light pre-blur before the Laplacian.
        # At source resolution real optical detail spans several pixels
        # (lens PSF + AA filter + demosaic) while SINGLE-pixel spikes
        # are sensor/demosaic noise — the exact opposite of the
        # display-scale finding that banned pre-blur for the fast paths
        # (where true detail IS single-pixel). Without this, an
        # un-denoised RAW half-res demosaic lights 10-20% of its pixels
        # at ANY threshold and the overview reads "everything is sharp"
        # (Nelson 2026-06-12, "even with 0 sensitivity").
        bgr = cv2.GaussianBlur(bgr, (3, 3), 0)
        return _laplacian_mask(
            bgr, threshold_from_sensitivity(sensitivity))
    except Exception as exc:    # noqa: BLE001 — must not crash UI
        log.warning("binary focus-peaking failed: %s", exc)
        return None


# Fit-view density cutoff: a display pixel lights when at least this
# fraction of its source block is lit (≈24% → 60/255) — "a quarter of
# this block is sharp" is what a fit pixel can honestly claim. Lower
# cutoffs amplify scattered noise (at a 4× fit, 10% meant TWO noisy
# pixels lit a block); the first build dilated by the scale ratio — a
# k² amplifier that lit whole photos at any sensitivity (Nelson
# 2026-06-12). Thin TRUE edges: a 1-px line survives up to ~4× fits,
# a 2-px line up to ~8× — beyond that the overview is a density map
# and the 1:1 view carries the per-pixel truth.
_FIT_DENSITY_CUTOFF = 60


def scale_binary_mask(
    mask: np.ndarray, width: int, height: int,
) -> np.ndarray:
    """Fit a binary mask to a display size, honestly.

    Downscale = a DENSITY measure: AREA-average the 0/255 mask (each
    display pixel becomes the fraction of its source block that is
    lit), then threshold at ``_FIT_DENSITY_CUTOFF``. Thin connected
    edges survive; isolated noise speckle does not — the overview
    answers "where is sharpness DENSE at pixel scale", which is what
    a fit view can truthfully say. Upscale = plain nearest (binary
    stays binary)."""
    if cv2 is None:
        return mask
    h, w = mask.shape[:2]
    width = max(1, int(width))
    height = max(1, int(height))
    if width < w:
        density = cv2.resize(
            mask, (width, height), interpolation=cv2.INTER_AREA)
        return np.where(
            density >= _FIT_DENSITY_CUTOFF, np.uint8(255), np.uint8(0))
    return cv2.resize(
        mask, (width, height), interpolation=cv2.INTER_NEAREST)


def overlay_from_binary(
    mask: np.ndarray,
    *,
    color: tuple[int, int, int] = PEAKING_COLOR_MAGENTA,
    opacity: Optional[float] = None,
) -> QPixmap:
    """Coloured RGBA overlay pixmap from a binary mask — the public
    sibling of the internal RGBA build. ``opacity=None`` reads the
    user-tunable setting exactly like :func:`compute_peaking_mask`."""
    if opacity is None:
        try:
            from mira.settings.repo import SettingsRepo
            opacity = float(SettingsRepo().load().focus_peaking_opacity)
        except Exception:                                       # noqa: BLE001
            opacity = PEAKING_OPACITY_DEFAULT
    return _mask_to_rgba(mask, color, opacity)


# ── internals ────────────────────────────────────────────────────


def _pixmap_to_array(pixmap: QPixmap) -> np.ndarray:
    """Convert QPixmap → BGR numpy array for cv2 consumption."""
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB888)
    width = image.width()
    height = image.height()
    bytes_per_line = image.bytesPerLine()
    ptr = image.bits()
    ptr.setsize(bytes_per_line * height)
    # QImage rows are padded to a multiple of 4 bytes — slice each row
    # back to width*3 before reshape, otherwise the BGR conversion sees
    # stride bytes as image content.
    raw = np.frombuffer(bytes(ptr), dtype=np.uint8).reshape(
        (height, bytes_per_line)
    )
    rgb = raw[:, : width * 3].reshape((height, width, 3))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _laplacian_mask(image_bgr: np.ndarray, threshold: int) -> np.ndarray:
    """Grayscale → 8-Laplacian via filter2D (CV_8U output, saturate_cast
    clamps negatives to 0 and >255 to 255 — PIL FIND_EDGES parity)
    → binary mask uint8 (0 or 255).

    No pre-blur. The prototype proved (and the rebuild's prior
    Gaussian-pre-blur Sobel pipeline confirmed by being worse) that
    smoothing before edge detection suppresses exactly the fine-texture
    signal that distinguishes in-focus from out-of-focus regions on
    natural subjects.
    """
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    response = cv2.filter2D(gray, cv2.CV_8U, _LAPLACIAN_KERNEL_8)
    return np.where(response > threshold, np.uint8(255), np.uint8(0))


def _mask_to_rgba(
    mask: np.ndarray,
    color: tuple[int, int, int],
    opacity: float,
) -> QPixmap:
    """Build an RGBA QPixmap from the binary mask. Lit pixels carry
    the peaking colour at ``opacity`` (0-1.0 → alpha 0-255); non-lit
    pixels are fully transparent."""
    height, width = mask.shape
    alpha_lit = int(round(max(0.0, min(1.0, opacity)) * 255))
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    r, g, b = color
    rgba[..., 0] = r
    rgba[..., 1] = g
    rgba[..., 2] = b
    # mask is 0 or 255; reduce to {0, alpha_lit} via a boolean compare.
    rgba[..., 3] = np.where(mask > 0, np.uint8(alpha_lit), np.uint8(0))
    qimage = QImage(
        rgba.tobytes(),
        width,
        height,
        4 * width,
        QImage.Format.Format_RGBA8888,
    )
    # Copy detaches from the numpy buffer (which goes out of scope).
    return QPixmap.fromImage(qimage.copy())


def _empty_mask(width: int, height: int) -> QPixmap:
    """A fully-transparent QPixmap of the requested size (used as the
    safe degraded-mode return when cv2 is missing or decode fails)."""
    pixmap = QPixmap(max(width, 1), max(height, 1))
    pixmap.fill(Qt.GlobalColor.transparent)
    return pixmap


# ── Public-colour helpers ────────────────────────────────────────


def color_tuple_for_name(name: str) -> tuple[int, int, int]:
    """Look up a peaking colour by name; fall back to magenta on an
    unknown value. Lower-cases for case-insensitive matching so the
    Settings dialog round-trip is forgiving."""
    return PEAKING_COLORS.get(
        (name or "").strip().lower(), PEAKING_COLOR_MAGENTA,
    )


def color_qcolor_for_name(name: str) -> QColor:
    """Same as :func:`color_tuple_for_name` but returning a QColor —
    convenient for QPen / QBrush callers."""
    r, g, b = color_tuple_for_name(name)
    return QColor(r, g, b)
