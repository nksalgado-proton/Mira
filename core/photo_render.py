"""Photo render pipeline — apply LRC-vocabulary adjustments to an image.

Pure numpy + scipy. Input is a ``(H, W, 3)`` uint8 RGB array; output
is the same shape after applying a :class:`Params` set. No Qt, no
file I/O — :mod:`core.photo_decoder` reads the file, this module
processes the buffer, :mod:`core.photo_auto` infers Params from
the same buffer.

Pipeline order (matches LRC's tonemapping order, which is the order
the LRC AUTO pairs land in):

  1. Exposure       — linear-light gain (EV stops)
  2. Whites + Blacks — output-level stretch (clipping points)
  3. Shadows         — lift dark tones with soft mask
  4. Highlights      — pull bright tones with soft mask
  5. Contrast        — sigmoid curve around midpoint
  6. Saturation      — luminance-preserving color gain
  6b. Vibrance       — saturation favouring muted colours, skin-safe
  7. Sharpness       — unsharp mask (radius 1.0)

Geometry (rotation 90°, crop) is applied OUTSIDE this tone pipeline
— see :func:`apply_rotation` + :func:`apply_crop_norm` below. The
host applies rotation → tone → crop in that order (docs/25 §12).

All stages are no-ops at default values (``Params()`` returns the
input image essentially unchanged — sub-rounding-error differences
from the float32 round-trip). This makes the AUTO toggle a clean
identity transform when AUTO Strength is 0.

Float32 [0, 1] internal representation; clip + uint8 at the end.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

from core.aspect_ratio import AspectRatio

log = logging.getLogger(__name__)


# ── Parameter set ──────────────────────────────────────────────


@dataclass(frozen=True)
class Params:
    """LRC-vocabulary adjustment parameters. All defaults = no change.

    Ranges match the LRC UI (and match the slider configuration in
    :mod:`ui.culler.ingest_process_page`):

    * ``exposure``   in stops (EV), range ``-4..+4``
    * ``contrast``   in arbitrary units, range ``-100..+100``
    * ``highlights`` negative = pull down clipping, range ``-100..+100``
    * ``shadows``    positive = lift dark regions, range ``-100..+100``
    * ``whites``     positive = brighter white point, range ``-100..+100``
    * ``blacks``     positive = darker black point, range ``-100..+100``
    * ``sharpness``  ``0..100`` (sane upper bound; LRC goes to 150)
    * ``saturation`` ``-100..+100``
    * ``vibrance``   ``-100..+100`` — smart saturation: lifts muted
      colours more than vivid ones and protects skin tones. The one
      manual colour control Process exposes (docs/25 §3); AUTO never
      sets it.
    """

    exposure:   float = 0.0
    contrast:   float = 0.0
    highlights: float = 0.0
    shadows:    float = 0.0
    whites:     float = 0.0
    blacks:     float = 0.0
    sharpness:  float = 0.0
    saturation: float = 0.0
    vibrance:   float = 0.0

    @property
    def is_identity(self) -> bool:
        """True iff applying these params is a no-op."""
        return (
            self.exposure == 0.0 and self.contrast == 0.0
            and self.highlights == 0.0 and self.shadows == 0.0
            and self.whites == 0.0 and self.blacks == 0.0
            and self.sharpness == 0.0 and self.saturation == 0.0
            and self.vibrance == 0.0
        )

    def scaled(self, strength: float) -> "Params":
        """Return a copy with every parameter multiplied by
        ``strength`` (the AUTO Strength slider, 0.0..2.0).
        ``strength=1.0`` is the unmodified Params; ``0.0`` is a
        no-op; ``2.0`` is exaggerated (rare but useful when AUTO is
        "almost right but underwhelming")."""
        return Params(
            exposure=self.exposure * strength,
            contrast=self.contrast * strength,
            highlights=self.highlights * strength,
            shadows=self.shadows * strength,
            whites=self.whites * strength,
            blacks=self.blacks * strength,
            sharpness=self.sharpness * strength,
            saturation=self.saturation * strength,
            vibrance=self.vibrance * strength,
        )


# ── Apply ──────────────────────────────────────────────────────


def _tone_curve(work: np.ndarray, params: Params) -> np.ndarray:
    """The per-channel tone stages of :func:`apply_params` (exposure →
    whites/blacks → shadows → highlights → contrast) applied to a float32
    array ``work`` in [0, 1]. Pure pointwise — no cross-pixel ops — so
    :func:`apply_params` runs it once on a 256-entry ramp to build a LUT
    rather than on every pixel. Returns the toned float32 array (NOT yet
    clipped to [0, 1] — over/under-shoot is carried so the final clip in
    apply_params resolves it, exactly as the original inline code did)."""
    # 1. Exposure — linear-light gain. 1 EV stop = 2× brightness.
    if params.exposure != 0.0:
        work = work * (2.0 ** params.exposure)

    # 2. Whites + Blacks — output-level stretch (LRC "+ = brighter":
    #    +Whites lowers the input white point; +Blacks lifts the black
    #    point above zero). The 200 divisor caps the swing.
    if params.whites != 0.0 or params.blacks != 0.0:
        white_pt = 1.0 - params.whites / 200.0
        black_pt = -params.blacks / 200.0
        span = max(white_pt - black_pt, 1e-6)
        work = (work - black_pt) / span

    # 3. Shadows — lift dark tones with a smooth squared mask (confined
    #    to the lower third; +100 adds ≤0.3 to the darkest pixels).
    if params.shadows != 0.0:
        shadow_mask = np.clip(1.0 - 2.0 * work, 0.0, 1.0) ** 2
        amount = params.shadows / 100.0 * 0.3
        work = work + shadow_mask * amount

    # 4. Highlights — pull bright tones with the inverse mask.
    if params.highlights != 0.0:
        highlight_mask = np.clip(2.0 * work - 1.0, 0.0, 1.0) ** 2
        amount = params.highlights / 100.0 * 0.3
        work = work + highlight_mask * amount

    # 5. Contrast — endpoint-preserving S-curve ``x^p / (x^p + (1-x)^p)``
    #    for k>0 (p in (1, 2.5]); flatten-toward-midpoint for k<0.
    #    Clip before np.power (negative base ^ non-integer = NaN); the
    #    blend keeps pre-clip values so over/under-shoot survives to the
    #    final clip in apply_params.
    if params.contrast != 0.0:
        k = params.contrast / 100.0
        if k > 0.0:
            clipped = np.clip(work, 0.0, 1.0)
            p = 1.0 + k * 1.5
            num = np.power(clipped, p)
            den = num + np.power(1.0 - clipped, p)
            s_curved = num / np.maximum(den, 1e-12)
            work = clipped + (s_curved - clipped) * k
        else:
            work = 0.5 + (work - 0.5) * (1.0 + k * 0.5)
    return work


def apply_params(
    img: np.ndarray, params: Params,
) -> np.ndarray:
    """Apply ``params`` to a uint8 RGB image. Returns a new uint8
    array. No-op (returns a copy) when ``params.is_identity``."""
    if params.is_identity:
        return img.copy()
    if img.dtype != np.uint8:
        raise ValueError(f"expected uint8 RGB array, got {img.dtype}")
    if img.ndim != 3 or img.shape[2] != 3:
        raise ValueError(f"expected (H, W, 3), got {img.shape}")

    # Tone (stages 1-5: exposure → whites/blacks → shadows → highlights →
    # contrast) is a pure per-channel function of the original 0-255 value,
    # so collapse it into a 256-entry LUT computed once and gather, instead
    # of running the float math (notably contrast's two np.power curves) on
    # every pixel. This is BIT-IDENTICAL to the per-pixel computation — the
    # same elementwise float32 ops on the same 256 input values, just indexed
    # — but ~200× cheaper on big frames (it matters because video export runs
    # apply_params on every full-resolution frame; see core/video_export_run).
    tone_lut = _tone_curve(np.arange(256, dtype=np.float32) / 255.0, params)
    work = tone_lut[img]                          # (H, W, 3) float32, exact

    # 6. Saturation — luminance-preserving color gain.
    # Mix toward the per-pixel luminance to desaturate; mix away to
    # saturate. ``saturation=+100`` doubles the color distance from
    # the luminance line; ``-100`` collapses to grayscale.
    if params.saturation != 0.0:
        lum = (
            0.2126 * work[..., 0]
            + 0.7152 * work[..., 1]
            + 0.0722 * work[..., 2]
        )[..., np.newaxis]
        factor = 1.0 + params.saturation / 100.0
        work = lum + (work - lum) * factor

    # 6b. Vibrance — non-linear saturation that favours muted colours
    # and protects skin tones. Positive: boost low-saturation pixels
    # more than already-vivid ones, damped where the pixel reads as
    # skin (so faces don't go orange). Negative: uniform desaturation
    # toward luminance (like the Saturation control). This is the only
    # colour control Process exposes; AUTO never sets it.
    if params.vibrance != 0.0:
        amount = params.vibrance / 100.0
        vlum = (
            0.2126 * work[..., 0]
            + 0.7152 * work[..., 1]
            + 0.0722 * work[..., 2]
        )
        # HSV-style saturation from the (clipped) working pixels.
        clipped = np.clip(work, 0.0, 1.0)
        mx = clipped.max(axis=2)
        mn = clipped.min(axis=2)
        sat = (mx - mn) / np.maximum(mx, 1e-6)
        if amount > 0.0:
            weight = 1.0 - sat                     # muted colours weigh more
            weight = weight * (1.0 - 0.7 * _skin_weight(clipped))
        else:
            weight = np.ones_like(sat)             # uniform desaturate
        factor = (1.0 + amount * weight)[..., np.newaxis]
        lum3 = vlum[..., np.newaxis]
        work = lum3 + (work - lum3) * factor

    # 7. Sharpness — unsharp mask (Gaussian blur subtract).
    # Radius 1.0 px ≈ "edge sharpness only". ``sharpness=+100``
    # boosts the high-frequency signal by 1.5× — strong but not
    # obnoxious on a typical photo.
    if params.sharpness != 0.0:
        blurred = np.empty_like(work)
        for c in range(3):
            blurred[..., c] = gaussian_filter(work[..., c], sigma=1.0)
        amount = params.sharpness / 100.0 * 1.5
        work = work + (work - blurred) * amount

    # Final clip + uint8 conversion. ``np.clip`` handles both
    # positive over-exposure and negative shadow lift.
    work = np.clip(work, 0.0, 1.0)
    return (work * 255.0 + 0.5).astype(np.uint8)


def _skin_weight(rgb: np.ndarray) -> np.ndarray:
    """Heuristic skin-tone likelihood ∈ [0, 1] per pixel for an RGB
    float array in [0, 1]. Skin reads as ``R ≥ G ≥ B`` with a warm,
    moderate spread; the product of the two positive gaps, scaled and
    clamped, gives a soft mask. Used to damp Vibrance on skin so faces
    don't go orange.

    Deliberately simple — calibration against real portraits belongs
    to the AUTO-tuning workstream (docs/25 §3), not this primitive. A
    wrong-but-mild guard beats none; the worst case is slightly under-
    or over-protected skin, never a crash or a colour blow-out."""
    r = rgb[..., 0]
    g = rgb[..., 1]
    b = rgb[..., 2]
    warm = np.clip(r - g, 0.0, None) * np.clip(g - b, 0.0, None)
    return np.clip(warm * 6.0, 0.0, 1.0)


# ── Rotation helper ────────────────────────────────────────────
#
# Rotation 90° (docs/25 §4) is geometry, not tone — applied outside
# :func:`apply_params`. The host applies rotation FIRST (so the crop
# rect, which is normalised against the displayed/rotated frame, lands
# correctly), then tone, then crop. Rotating resets the crop because
# the frame's width/height swap on a 90/270 turn.


def apply_rotation(img: np.ndarray, degrees: int) -> np.ndarray:
    """Rotate a uint8 RGB array by a multiple of 90° **clockwise**.

    ``degrees`` is normalised to one of ``{0, 90, 180, 270}`` (rounded
    to the nearest 90 and wrapped). ``0`` returns the input unchanged
    (no copy). Positive = clockwise — matches the ⟳ button and the
    EXIF-orientation convention the canvas displays."""
    deg = (int(round(degrees / 90.0)) * 90) % 360
    if deg == 0:
        return img
    # np.rot90 turns counter-clockwise for k > 0, so map a clockwise
    # request to the equivalent negative k.
    k = {90: -1, 180: 2, 270: 1}[deg]
    return np.ascontiguousarray(np.rot90(img, k))


# ── Crop helpers ───────────────────────────────────────────────
#
# Ported from the prototype's ``core.process_render`` (the prototype's
# crop was field-tested through Costa Rica + Pantanal trips and
# worked flawlessly per Nelson 2026-05-21). Two simple helpers:
#
# * :func:`compute_default_crop` — the centered maximal-area rectangle
#   matching a given aspect ratio. Returns ``None`` for Original.
# * :func:`apply_crop_norm` — crops a uint8 RGB array by a normalised
#   ``(x, y, w, h)`` rect in [0, 1].
#
# The normalised representation is the *single* serialisation form
# used by the journal and overlay widget — keeping rects independent
# of which preview size or pixmap they were drawn against.


def compute_default_crop(
    img_w: int,
    img_h: int,
    ratio: AspectRatio,
) -> Optional[tuple[float, float, float, float]]:
    """Return the centred maximal rect matching ``ratio`` as normalised
    ``(x, y, w, h)`` in ``[0, 1]``. Returns ``None`` for the Original
    ratio (no crop) so callers can distinguish "user picked Original"
    from "user hasn't picked a ratio yet" via the same falsy check."""
    if ratio.is_original or img_w <= 0 or img_h <= 0:
        return None

    target = ratio.value
    src = img_w / img_h
    if target > src:
        # Target is wider than the source — crop top/bottom slabs.
        crop_w = 1.0
        crop_h = src / target
    else:
        # Target is narrower — crop left/right slabs.
        crop_w = target / src
        crop_h = 1.0
    x = (1.0 - crop_w) / 2.0
    y = (1.0 - crop_h) / 2.0
    return (x, y, crop_w, crop_h)


def apply_crop_norm(
    img: np.ndarray,
    rect_norm: tuple[float, float, float, float],
) -> np.ndarray:
    """Crop a uint8 RGB array by a normalised ``(x, y, w, h)`` rect.
    Values are clamped to ``[0, 1]`` so a slightly out-of-bounds rect
    (numerical drift, weird user drag) doesn't raise. Returns a view
    into the array — caller copies if it wants an independent buffer.
    A degenerate rect (w or h ≤ 0 after clamping) returns the input
    unchanged."""
    x, y, w, h = rect_norm
    x = max(0.0, min(1.0, x))
    y = max(0.0, min(1.0, y))
    w = max(0.0, min(1.0 - x, w))
    h = max(0.0, min(1.0 - y, h))
    if w <= 0 or h <= 0:
        return img
    ih, iw = img.shape[:2]
    left = int(round(x * iw))
    top = int(round(y * ih))
    right = int(round((x + w) * iw))
    bottom = int(round((y + h) * ih))
    # Clamp the integer coords too — guard against off-by-one when
    # x + w == 1 lands at iw + 1 after rounding.
    right = min(iw, max(left + 1, right))
    bottom = min(ih, max(top + 1, bottom))
    return img[top:bottom, left:right]


# ── Creative-filter stage (spec/55) ───────────────────────────
#
# A filter is a FIXED transform applied AFTER the Look's tone Params
# (pipeline: A-correction → mood bias → filter → crop; spec/54 §8).
# Recipes ship as plain dicts in the generated
# ``core.photo_looks_data`` module; :class:`FilterRecipe` is the
# typed form, :func:`apply_filter` the pixel stage. All components
# default to no-ops, so ``FilterRecipe()`` is the identity — same
# contract as ``Params()``.


@dataclass(frozen=True)
class FilterRecipe:
    """One creative filter's transform (spec/55 §2 anatomy).

    * ``params``     — tonal/color components reusing the existing
      engine vocabulary (contrast, saturation, vibrance, …).
    * ``bw_mix``     — channel weights for mono conversion (e.g. a
      red-heavy mix darkens skies, the classic dramatic-mono trick).
      ``None`` = stay in color.
    * ``tint``       — global RGB gains (warm/cool casts; sepia's
      brown over a bw_mix).
    * ``split_shadows`` / ``split_highlights`` — RGB gains applied in
      the dark / bright ends via soft luminance masks (teal–orange).
    * ``fade``       — output black-point lift in [0, 0.25] (matte).
    * ``clarity``    — large-radius local contrast in [-1, 1]
      (cloud drama, feather/chitin texture). Luminance-only, so no
      edge color fringing.
    * ``vignette``   — corner darkening in [0, 1] (subject-drawing).
    """

    params: Params = Params()
    bw_mix: Optional[tuple[float, float, float]] = None
    tint: tuple[float, float, float] = (1.0, 1.0, 1.0)
    split_shadows: tuple[float, float, float] = (1.0, 1.0, 1.0)
    split_highlights: tuple[float, float, float] = (1.0, 1.0, 1.0)
    fade: float = 0.0
    clarity: float = 0.0
    vignette: float = 0.0

    @property
    def is_identity(self) -> bool:
        return (
            self.params.is_identity and self.bw_mix is None
            and self.tint == (1.0, 1.0, 1.0)
            and self.split_shadows == (1.0, 1.0, 1.0)
            and self.split_highlights == (1.0, 1.0, 1.0)
            and self.fade == 0.0 and self.clarity == 0.0
            and self.vignette == 0.0
        )

    @classmethod
    def from_dict(cls, d: dict) -> "FilterRecipe":
        """Hydrate from the generated data module's plain-dict form.
        Unknown keys are rejected loudly (a broken regeneration must
        not silently render wrong)."""
        d = dict(d)
        params = Params(**d.pop("params", {}))
        known = {
            "bw_mix", "tint", "split_shadows", "split_highlights",
            "fade", "clarity", "vignette",
        }
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown FilterRecipe keys: {sorted(unknown)}")
        for k in ("bw_mix", "tint", "split_shadows", "split_highlights"):
            if k in d and d[k] is not None:
                d[k] = tuple(float(v) for v in d[k])
        return cls(params=params, **d)


def _luminance_f32(work: np.ndarray) -> np.ndarray:
    """Rec. 709 luminance of a float32 RGB array (same weights as the
    Vibrance/Saturation stages)."""
    return (0.2126 * work[..., 0] + 0.7152 * work[..., 1]
            + 0.0722 * work[..., 2])


def apply_filter(
    img: np.ndarray, recipe: FilterRecipe, amount: float = 1.0,
) -> np.ndarray:
    """Apply a creative filter to a uint8 RGB image (the spec/54 §8
    pipeline stage AFTER the Look's Params). Returns a new uint8
    array; no-op (copy) for the identity recipe or ``amount == 0``.

    Stage order: params → bw_mix → tint → split-tone → fade →
    clarity → vignette. Tonal components first so the color stages
    operate on the filter's intended base; fade near the end so the
    matte lift survives the contrast moves; vignette last (spatial,
    multiplicative).

    ``amount`` is the spec/54 §4.1 calibration trim mapped to 0..2:
    the result is BLENDED between the input (0) and the full recipe
    (1), and extrapolated past it above 1 — every component scales
    uniformly (half a B&W is a semi-desaturation, half a vignette is
    a lighter vignette)."""
    amount = float(amount)
    if recipe.is_identity or amount == 0.0:
        return img.copy()
    if img.dtype != np.uint8 or img.ndim != 3 or img.shape[2] != 3:
        raise ValueError(
            f"expected uint8 (H, W, 3), got {img.dtype} {img.shape}")
    base = img

    if not recipe.params.is_identity:
        img = apply_params(img, recipe.params)
    work = img.astype(np.float32) / 255.0

    if recipe.bw_mix is not None:
        w = np.array(recipe.bw_mix, dtype=np.float32)
        w_sum = float(w.sum())
        if abs(w_sum) > 1e-6:
            w = w / w_sum
        mono = (work * w).sum(axis=2)
        work = np.repeat(mono[..., np.newaxis], 3, axis=2)

    if recipe.tint != (1.0, 1.0, 1.0):
        work = work * np.array(recipe.tint, dtype=np.float32)

    if (recipe.split_shadows != (1.0, 1.0, 1.0)
            or recipe.split_highlights != (1.0, 1.0, 1.0)):
        lum = np.clip(_luminance_f32(work), 0.0, 1.0)
        sh_mask = ((1.0 - lum) ** 2)[..., np.newaxis]
        hi_mask = (lum ** 2)[..., np.newaxis]
        sh = np.array(recipe.split_shadows, dtype=np.float32)
        hi = np.array(recipe.split_highlights, dtype=np.float32)
        work = work * (1.0 + (sh - 1.0) * sh_mask) \
                    * (1.0 + (hi - 1.0) * hi_mask)

    if recipe.fade != 0.0:
        f = float(np.clip(recipe.fade, 0.0, 0.25))
        work = f + work * (1.0 - f)

    if recipe.clarity != 0.0:
        # Local contrast on LUMINANCE at a radius proportional to the
        # frame (texture/cloud scale) — per-channel unsharp at this
        # radius would fringe colors. Sigma floors at 2 px so tiny
        # thumbnails still respond.
        h, w_px = work.shape[:2]
        sigma = max(2.0, 0.015 * min(h, w_px))
        lum = _luminance_f32(work)
        blurred = gaussian_filter(lum, sigma=sigma)
        delta = (lum - blurred) * float(recipe.clarity)
        work = work + delta[..., np.newaxis]

    if recipe.vignette != 0.0:
        h, w_px = work.shape[:2]
        yy = (np.arange(h, dtype=np.float32) - (h - 1) / 2.0) / (h / 2.0)
        xx = (np.arange(w_px, dtype=np.float32) - (w_px - 1) / 2.0) \
            / (w_px / 2.0)
        r = np.sqrt(yy[:, np.newaxis] ** 2 + xx[np.newaxis, :] ** 2)
        # Flat centre, smooth falloff from ~55% radius outward.
        t = np.clip((r - 0.55) / 0.65, 0.0, 1.0)
        mask = 1.0 - float(np.clip(recipe.vignette, 0.0, 1.0)) * (t * t)
        work = work * mask[..., np.newaxis]

    if amount != 1.0:
        # Calibration blend: lerp from the unfiltered input (0) through
        # the full recipe (1), extrapolating beyond (clamped by the
        # final clip). Uniform across every component by construction.
        base_f = base.astype(np.float32) / 255.0
        work = base_f + (work - base_f) * amount

    work = np.clip(work, 0.0, 1.0)
    return (work * 255.0 + 0.5).astype(np.uint8)


def extract_rotated_crop(
    img: np.ndarray,
    rect_norm: tuple[float, float, float, float],
    angle_degrees: float,
) -> np.ndarray:
    """Extract the crop rect ``rect_norm`` ROTATED about its OWN centre
    by ``angle_degrees`` (clockwise positive), rectified to upright
    (docs/25 §4 "Box Rotation"): the box spins over a static photo
    keeping its size + centre, and the output is the box's content made
    upright — exactly what the user framed.

    Implementation: rotate the whole image about the box centre so the
    box becomes axis-aligned, then crop the box's pixel rect at that
    centre. ``angle_degrees == 0`` short-circuits to a plain crop. Out-
    of-frame corners (rotated box reaching past the image edge) come
    out black. Returns a uint8 RGB array."""
    if abs(angle_degrees) < 1e-3:
        return np.ascontiguousarray(apply_crop_norm(img, rect_norm))
    ih, iw = img.shape[:2]
    x, y, w, h = rect_norm
    cx = (x + w / 2.0) * iw
    cy = (y + h / 2.0) * ih
    bw = max(1, int(round(w * iw)))
    bh = max(1, int(round(h * ih)))
    pil = Image.fromarray(img)
    # PIL rotate() is counter-clockwise for positive angles; rotating
    # the image CCW by the box's CW angle brings the box upright.
    rotated = pil.rotate(
        float(angle_degrees),
        resample=Image.Resampling.BICUBIC,
        center=(cx, cy),
        expand=False,
    )
    left = int(round(cx - bw / 2.0))
    top = int(round(cy - bh / 2.0))
    crop = rotated.crop((left, top, left + bw, top + bh))
    return np.asarray(crop)
