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
    """One creative filter's transform (spec/55 §2 anatomy + spec/116
    additions).

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
    * ``spotlight``  — radial subject-pop in [0, 1] (spec/116). Anchors
      at the ``center`` kwarg of :func:`apply_filter` (the photo's AF
      point) — inside the radius gets a local-contrast + slight
      exposure lift; outside is darkened + desaturated. 0 = no-op.
    * ``spotlight_radius`` — inner radius (0..1, default ~0.6) of the
      Spotlight mask, in units of the half-diagonal. Tunes how much of
      the frame is "the subject" before background falloff begins.
    * ``dehaze``     — local contrast + saturation weighted to flat
      regions, plus a black-point pull, in [-1, 1]. +0.5 cuts a hazy
      frame; -0.5 adds atmosphere. 0 = no-op.
    * ``deglare``    — specular-hotspot tamer in [0, 1] (spec/118
      component). Soft glare mask = high-luminance AND low-saturation
      (the optical signature of specular reflections), gaussian-
      smoothed. Inside the mask, scaled by ``deglare``: luminance is
      pulled down, chroma is re-injected from the surrounding non-
      glare ring (so a blown forehead gets skin tone back, not a grey
      blob), and low-frequency texture is borrowed back. Fully clipped
      255 regions are unrecoverable — this stage softens them, never
      reconstructs detail. 0 = no-op.
    * ``deglare_subject_only`` — when True (default), the glare mask
      is multiplied by the §2 radial subject mask anchored at the
      ``center`` kwarg with the recipe's ``spotlight_radius`` so only
      the subject's glare is touched (preserves rim lights / window
      sparkles in the background). False = frame-wide tamer.
    * ``glow``       — Orton-style screen-blended bloom in [0, 1].
      Brightened-and-blurred copy laid over the frame at this strength.
      0 = no-op.
    * ``grain``      — luminance-masked monochrome gaussian noise in
      [0, 1]. Strongest in mid-tones; clean highlights/shadows. 0 =
      no-op.
    """

    params: Params = Params()
    bw_mix: Optional[tuple[float, float, float]] = None
    tint: tuple[float, float, float] = (1.0, 1.0, 1.0)
    split_shadows: tuple[float, float, float] = (1.0, 1.0, 1.0)
    split_highlights: tuple[float, float, float] = (1.0, 1.0, 1.0)
    fade: float = 0.0
    clarity: float = 0.0
    vignette: float = 0.0
    spotlight: float = 0.0
    spotlight_radius: float = 0.6
    dehaze: float = 0.0
    deglare: float = 0.0
    deglare_subject_only: bool = True
    glow: float = 0.0
    grain: float = 0.0

    @property
    def is_identity(self) -> bool:
        # ``spotlight_radius`` doesn't count toward identity on its own —
        # only the *strength* (``spotlight``) controls whether the stage
        # runs. A default-radius / zero-strength filter is identity.
        # ``deglare_subject_only`` is a mode flag; only ``deglare > 0``
        # decides whether the stage runs.
        return (
            self.params.is_identity and self.bw_mix is None
            and self.tint == (1.0, 1.0, 1.0)
            and self.split_shadows == (1.0, 1.0, 1.0)
            and self.split_highlights == (1.0, 1.0, 1.0)
            and self.fade == 0.0 and self.clarity == 0.0
            and self.vignette == 0.0
            and self.spotlight == 0.0
            and self.dehaze == 0.0
            and self.deglare == 0.0
            and self.glow == 0.0
            and self.grain == 0.0
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
            "spotlight", "spotlight_radius",
            "dehaze", "deglare", "deglare_subject_only",
            "glow", "grain",
        }
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown FilterRecipe keys: {sorted(unknown)}")
        for k in ("bw_mix", "tint", "split_shadows", "split_highlights"):
            if k in d and d[k] is not None:
                d[k] = tuple(float(v) for v in d[k])
        if "deglare_subject_only" in d:
            d["deglare_subject_only"] = bool(d["deglare_subject_only"])
        return cls(params=params, **d)

    def to_dict(self) -> dict:
        """The inverse of :meth:`from_dict` — emit a plain-dict form
        ready for re-hydration. Identity-valued components are
        omitted to keep the serialised shape lean (a default
        FilterRecipe round-trips to ``{}``)."""
        out: dict = {}
        if not self.params.is_identity:
            # Params dataclass; round-trip via its dataclass fields so
            # we don't ship the full default vector.
            from dataclasses import fields as _fields
            p_default = Params()
            p_diff = {
                f.name: getattr(self.params, f.name)
                for f in _fields(Params)
                if getattr(self.params, f.name) != getattr(p_default, f.name)
            }
            if p_diff:
                out["params"] = p_diff
        if self.bw_mix is not None:
            out["bw_mix"] = tuple(float(v) for v in self.bw_mix)
        if self.tint != (1.0, 1.0, 1.0):
            out["tint"] = tuple(float(v) for v in self.tint)
        if self.split_shadows != (1.0, 1.0, 1.0):
            out["split_shadows"] = tuple(float(v) for v in self.split_shadows)
        if self.split_highlights != (1.0, 1.0, 1.0):
            out["split_highlights"] = tuple(
                float(v) for v in self.split_highlights)
        if self.fade != 0.0:
            out["fade"] = float(self.fade)
        if self.clarity != 0.0:
            out["clarity"] = float(self.clarity)
        if self.vignette != 0.0:
            out["vignette"] = float(self.vignette)
        if self.spotlight != 0.0:
            out["spotlight"] = float(self.spotlight)
            if self.spotlight_radius != 0.6:
                out["spotlight_radius"] = float(self.spotlight_radius)
        if self.dehaze != 0.0:
            out["dehaze"] = float(self.dehaze)
        if self.deglare != 0.0:
            out["deglare"] = float(self.deglare)
            if not self.deglare_subject_only:
                out["deglare_subject_only"] = False
        if self.glow != 0.0:
            out["glow"] = float(self.glow)
        if self.grain != 0.0:
            out["grain"] = float(self.grain)
        return out


def _luminance_f32(work: np.ndarray) -> np.ndarray:
    """Rec. 709 luminance of a float32 RGB array (same weights as the
    Vibrance/Saturation stages)."""
    return (0.2126 * work[..., 0] + 0.7152 * work[..., 1]
            + 0.0722 * work[..., 2])


def _clarity_delta(work: np.ndarray, strength: float) -> np.ndarray:
    """The spec/55 clarity primitive: per-luminance unsharp delta at a
    frame-proportional radius. Returned as a ``(H, W)`` float32 delta
    the caller adds to its RGB channels — spec/116 reuses this inside
    Spotlight (local-contrast pop) without re-deriving the math."""
    h, w_px = work.shape[:2]
    sigma = max(2.0, 0.015 * min(h, w_px))
    lum = _luminance_f32(work)
    blurred = gaussian_filter(lum, sigma=sigma)
    return (lum - blurred) * float(strength)


def _radial_mask(
    h: int, w_px: int, *,
    center: tuple[float, float], radius: float,
) -> np.ndarray:
    """A smooth radial mask in [0, 1] centred at ``center`` (normalised
    image coords, 0..1, origin top-left) with inner-plateau radius
    ``radius`` (in units of the half-diagonal of the frame). Falloff is
    a smoothstep (3t² − 2t³) for an aesthetic edge, going to 0 by the
    outer edge of the frame. Used by the Spotlight stage; could be
    re-used by future radial vignettes."""
    yy = (np.arange(h, dtype=np.float32) / max(h - 1, 1) - center[1])
    xx = (np.arange(w_px, dtype=np.float32) / max(w_px - 1, 1)
          - center[0])
    # Aspect-correct radius: the frame's half-diagonal is the unit.
    yy = yy * (h / max(min(h, w_px), 1))
    xx = xx * (w_px / max(min(h, w_px), 1))
    r = np.sqrt(yy[:, np.newaxis] ** 2 + xx[np.newaxis, :] ** 2)
    # Map distance to mask: 1 inside ``radius``, smoothstep to 0 by 1.0.
    inner = max(0.0, float(radius))
    outer = max(inner + 1e-3, 1.0)
    t = np.clip((r - inner) / (outer - inner), 0.0, 1.0)
    return 1.0 - (3.0 * t * t - 2.0 * t * t * t)


def apply_filter(
    img: np.ndarray, recipe: FilterRecipe, amount: float = 1.0,
    *, center: tuple[float, float] = (0.5, 0.5),
) -> np.ndarray:
    """Apply a creative filter to a uint8 RGB image (the spec/54 §8
    pipeline stage AFTER the Look's Params). Returns a new uint8
    array; no-op (copy) for the identity recipe or ``amount == 0``.

    Stage order (spec/55 + spec/116 + spec/118): params → bw_mix →
    tint → split-tone → dehaze → deglare → fade → clarity → glow →
    spotlight → vignette → grain. Tonal components first so the color
    stages operate on the filter's intended base; de-glare runs EARLY
    (before clarity/glow re-touch highlights) so the hotspot's been
    softened before subsequent local-contrast / bloom stages amplify
    it again; fade near the end so the matte lift survives the
    contrast moves; spatial stages (glow, spotlight, vignette) then;
    texture (grain) last so the noise isn't blurred away by a
    subsequent stage.

    ``amount`` is the spec/54 §4.1 calibration trim mapped to 0..2:
    the result is BLENDED between the input (0) and the full recipe
    (1), and extrapolated past it above 1 — every component scales
    uniformly (half a B&W is a semi-desaturation, half a vignette is
    a lighter vignette).

    ``center`` is the Spotlight's anchor in normalised image coords
    (spec/116 §2). The render call sites pass the photo's AF point
    here; ``(0.5, 0.5)`` (frame centre) is the contractual fallback
    when no AF point is available — never an error."""
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

    if recipe.dehaze != 0.0:
        # spec/116 — dehaze approximation (NOT a physical dark-channel
        # model; honest about that). Three coordinated moves:
        #   1. Pull the black point (positive) / lift it (negative) —
        #      removes / adds the milky baseline of a hazy frame.
        #   2. Local-contrast lift (unsharp on luminance), weighted by
        #      the inverse local luminance variance so flat (hazy)
        #      regions get more help than already-detailed areas.
        #   3. Saturation bump on the same flat-region weighting so
        #      washed-out distant colour pops without over-juicing
        #      already-vivid foreground.
        k = float(np.clip(recipe.dehaze, -1.0, 1.0))
        # 1. Black point.
        black_pull = 0.08 * k
        work = (work - black_pull) / max(1.0 - black_pull, 1e-3)
        # 2. Local-contrast lift weighted by flatness.
        lum = _luminance_f32(work)
        # Use a SMALLER blur radius than clarity — dehaze targets a
        # finer micro-contrast scale.
        h, w_px = work.shape[:2]
        sigma_d = max(1.5, 0.008 * min(h, w_px))
        blurred = gaussian_filter(lum, sigma=sigma_d)
        # Flatness ≈ 1 - normalized local variance of luminance; cheap
        # proxy = the absolute residual after the blur.
        flatness = np.clip(
            1.0 - np.abs(lum - blurred) * 6.0, 0.0, 1.0)
        delta = (lum - blurred) * (0.6 * k)
        work = work + (delta * flatness)[..., np.newaxis]
        # 3. Flat-weighted saturation (positive k → boost, negative →
        # mute).
        sat_lum = _luminance_f32(work)[..., np.newaxis]
        work = sat_lum + (work - sat_lum) * (1.0 + 0.4 * k * flatness[..., np.newaxis])

    if recipe.deglare != 0.0:
        # spec/118 — specular-hotspot tamer. Fully clipped (255)
        # regions are unrecoverable; this softens them, it does not
        # reconstruct lost detail. Three coordinated moves inside the
        # mask, all scaled by the strength ``g``:
        #   1. Pull luminance down so the hotspot stops dominating the
        #      tone.
        #   2. Re-inject chroma sampled from the surrounding non-glare
        #      ring — restores skin / fabric colour to a blown patch
        #      instead of leaving a grey blob.
        #   3. Borrow low-frequency texture from the same ring so the
        #      patch isn't perfectly flat.
        # The mask is the intersection of (a) high luminance and (b)
        # low saturation (the optical signature of specular reflections
        # — a sunlit forehead reads as nearly-white, not as a colour
        # cast). Gaussian-smoothed so the boundary doesn't read as a
        # ring. When ``deglare_subject_only`` (default), multiply by
        # the §2 subject radial mask so background sparkles / rim
        # lights / window highlights are preserved.
        g = float(np.clip(recipe.deglare, 0.0, 1.0))
        h, w_px = work.shape[:2]
        rgb_max = np.max(work, axis=2)
        rgb_min = np.min(work, axis=2)
        # HSV-style saturation; 0 when grey, 1 when fully saturated.
        sat = np.where(
            rgb_max > 1e-6, (rgb_max - rgb_min) / np.maximum(rgb_max, 1e-6),
            0.0).astype(np.float32)
        # High-luminance band: smooth ramp 0..1 between 0.78 and 0.96
        # so partly-clipped patches respond at near-full strength while
        # ordinary highlights are mostly spared.
        lum = _luminance_f32(work)
        lum_mask = np.clip((lum - 0.78) / max(0.96 - 0.78, 1e-3), 0.0, 1.0)
        # Low-saturation band: smooth ramp 1..0 between 0.05 and 0.30
        # so a desaturated patch reads as glare and a colourful one is
        # left alone.
        sat_mask = np.clip(
            1.0 - (sat - 0.05) / max(0.30 - 0.05, 1e-3), 0.0, 1.0)
        glare = lum_mask * sat_mask
        # Smooth the mask edge so the recovery doesn't read as a ring.
        sigma_m = max(1.5, 0.006 * min(h, w_px))
        glare = gaussian_filter(glare, sigma=sigma_m)
        if recipe.deglare_subject_only:
            subject_mask = _radial_mask(
                h, w_px, center=center,
                radius=recipe.spotlight_radius)
            glare = glare * subject_mask
        # 1. Luminance pull-down inside the mask.
        lum_pull = 0.18 * g
        m3 = glare[..., np.newaxis]
        work = work * (1.0 - lum_pull * m3)
        # Sample the surrounding non-glare ring once for both chroma
        # and low-frequency-texture borrows. Sigma must be wider than
        # the mask so the blur reaches OUTSIDE the glare patch.
        sigma_ring = max(6.0, 0.030 * min(h, w_px))
        ring = np.empty_like(work)
        for c in range(3):
            ring[..., c] = gaussian_filter(work[..., c], sigma=sigma_ring)
        # 2. Chroma re-injection. Use the ring's chroma (its colour
        # relative to its own luminance) and add it to the in-mask
        # pixels' luminance — so the recovered patch carries the
        # local tone, not whatever cast leaked into the white.
        ring_lum = _luminance_f32(ring)
        ring_chroma = ring - ring_lum[..., np.newaxis]
        chroma_strength = 0.85 * g
        in_lum = _luminance_f32(work)[..., np.newaxis]
        chroma_target = in_lum + ring_chroma
        work = work + (chroma_target - work) * (chroma_strength * m3)
        # 3. Low-frequency texture borrow. Blend toward the ring's
        # smoothed RGB (a much milder move — keeps the patch from
        # reading perfectly flat without painting over what little
        # texture survived).
        texture_strength = 0.20 * g
        work = work + (ring - work) * (texture_strength * m3)

    if recipe.fade != 0.0:
        f = float(np.clip(recipe.fade, 0.0, 0.25))
        work = f + work * (1.0 - f)

    if recipe.clarity != 0.0:
        # Local contrast on LUMINANCE at a radius proportional to the
        # frame (texture/cloud scale) — per-channel unsharp at this
        # radius would fringe colors. Sigma floors at 2 px so tiny
        # thumbnails still respond.
        delta = _clarity_delta(work, recipe.clarity)
        work = work + delta[..., np.newaxis]

    if recipe.glow != 0.0:
        # spec/116 — Orton-style bloom. Brightened-and-blurred copy
        # screen-blended over the original at strength ``glow``.
        # Sigma is frame-proportional so a 4K frame and a thumbnail
        # produce visually-matching bloom radii.
        g = float(np.clip(recipe.glow, 0.0, 1.0))
        h, w_px = work.shape[:2]
        sigma_g = max(3.0, 0.025 * min(h, w_px))
        # Brighten the source for the bloom layer — pull dark midtones
        # up via a gentle gamma so highlights bloom more than shadows.
        bright = np.clip(work, 0.0, 1.0) ** 0.7
        blurred = np.empty_like(bright)
        for c in range(3):
            blurred[..., c] = gaussian_filter(bright[..., c], sigma=sigma_g)
        # Screen blend ``A`` over ``B``: ``1 - (1 - A)(1 - B)``.
        a = np.clip(work, 0.0, 1.0)
        screen = 1.0 - (1.0 - a) * (1.0 - blurred)
        work = work + (screen - work) * g

    if recipe.spotlight != 0.0:
        # spec/116 — radial subject-pop. ``center`` is the AF anchor;
        # the mask is high near the centre, low at the corners. Inside:
        # clarity-style local contrast + a small exposure lift. Outside:
        # darken + desaturate toward luminance.
        s = float(np.clip(recipe.spotlight, 0.0, 1.0))
        h, w_px = work.shape[:2]
        mask = _radial_mask(
            h, w_px, center=center, radius=recipe.spotlight_radius)
        m = mask[..., np.newaxis]
        # Inside: local contrast + small exposure lift.
        inner_clarity = 0.55 * s
        clarity_delta = _clarity_delta(work, inner_clarity)
        work = work + (clarity_delta * mask)[..., np.newaxis]
        exposure_lift = 0.12 * s
        work = work * (1.0 + exposure_lift * m)
        # Outside: darken + desaturate toward luminance.
        bg = (1.0 - mask)[..., np.newaxis]
        bg_darken = 0.18 * s
        work = work * (1.0 - bg_darken * bg)
        bg_desat = 0.55 * s
        lum3 = _luminance_f32(work)[..., np.newaxis]
        work = lum3 + (work - lum3) * (1.0 - bg_desat * bg)

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

    if recipe.grain != 0.0:
        # spec/116 — luminance-masked monochrome film grain. Noise is
        # strongest in the midtones (where film grain reads strongest)
        # and tapers at the clean ends. Reproducible per-render so the
        # output isn't visually unstable on re-renders — seed from
        # frame dimensions (the AF center isn't a stable seed if the
        # user crops). The noise is monochrome to read as grain rather
        # than as chroma sparkle.
        g = float(np.clip(recipe.grain, 0.0, 1.0))
        h, w_px = work.shape[:2]
        rng = np.random.default_rng(int(h * 131071 + w_px))
        noise = rng.standard_normal((h, w_px), dtype=np.float32)
        # Midtone-weighted mask: 4*L*(1-L) peaks at L=0.5.
        lum = np.clip(_luminance_f32(work), 0.0, 1.0)
        mid_mask = 4.0 * lum * (1.0 - lum)
        amplitude = 0.12 * g
        work = work + (noise * mid_mask * amplitude)[..., np.newaxis]

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
