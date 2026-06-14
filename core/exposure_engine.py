"""Auto-exposure engine for the Process Culler.

Histogram-based percentile stretching: find the 1st and 99th brightness
percentiles in the image, map them to (0, 255) so shadows open up and
highlights don't blow out. A small highlight-recovery pass compresses
the top of the curve to keep specular highlights from clipping hard.

This is *not* a Lightroom Auto Tone replacement — it's the cheap trick
that gets you 95% of the way for typical travel/landscape/people work.
The user toggles it on/off per photo from the Process Culler toolbar.

API:
    auto_exposure(img: PIL.Image, strength: float = 0.85) -> PIL.Image

The function is pure and Qt-free so it can be unit-tested with synthetic
images and reused later by batch tooling (e.g., a Nuitka-compiled CLI).
"""

from __future__ import annotations

import logging
import numpy as np
from PIL import Image, ImageEnhance

log = logging.getLogger(__name__)

# Default percentiles used to anchor the tone curve. p1=1 means "the
# darkest 1% of pixels become pure black"; p99 means "the brightest
# 1% become pure white". Per-scenario profiles override these via
# ``ExposureProfile.dark_percentile`` / ``light_percentile`` —
# preserving deep shadows for macro, lifting for wildlife, etc.
_DEFAULT_DARK_PERCENTILE = 1.0
_DEFAULT_LIGHT_PERCENTILE = 99.0

# Default highlight-recovery knee. Per-scenario profiles override via
# ``ExposureProfile.highlight_knee``. Lower values catch flash specular
# earlier (macro/portrait), higher values are more permissive.
_DEFAULT_HIGHLIGHT_KNEE = 235  # 0..255
_HIGHLIGHT_CEILING = 250


def auto_exposure(
    img: Image.Image,
    strength: float = 0.85,
    *,
    highlight_recovery: bool = True,
    dark_percentile: float = _DEFAULT_DARK_PERCENTILE,
    light_percentile: float = _DEFAULT_LIGHT_PERCENTILE,
    highlight_knee: int = _DEFAULT_HIGHLIGHT_KNEE,
    contrast_strength: float = 0.0,
    shadows: float = 0.0,
    highlights: float = 0.0,
    saturation: float = 0.0,
    vibrance: float = 0.0,
) -> Image.Image:
    """Apply a histogram-percentile tone curve to ``img``.

    Args:
        img: PIL Image (RGB or RGBA — alpha is preserved unmodified).
        strength: 0.0–1.0 blend factor. 0.0 returns the original image
            untouched; 1.0 applies the full computed curve. The default
            0.85 leaves a tiny bit of original character so the result
            doesn't look "edited".
        highlight_recovery: when True, soften pixels above
            ``highlight_knee`` so specular highlights don't slam into
            255. Disable for night/long-exposure where point lights are
            supposed to clip clean white.
        dark_percentile: which luminance percentile maps to pure black.
        light_percentile: which luminance percentile maps to pure white.
        highlight_knee: 0-255 threshold above which soft-compression
            engages when ``highlight_recovery=True``.
        contrast_strength: -1.0..+1.0 S-curve. Positive bends the tone
            curve into an S (more punch); negative inverts it (flatter,
            soft look). Effective contrast is scaled by ``strength`` so
            the slider feels gradual.
        shadows: -1.0 (crush dark detail) … +1.0 (lift shadows).
            Shapes the LUT in the lower 0..128 range only — pure
            highlights are unaffected.
        highlights: -1.0 (recover, push brights down) … +1.0 (boost,
            push brights up). Shapes the LUT in the upper 128..255
            range only.
        saturation: -1.0 (grayscale) … +1.0 (2× saturation). Linear
            scale on chroma channels.
        vibrance: -1.0 … +1.0. Like saturation but boosts low-sat
            pixels more than already-saturated ones, so the effect
            is smoother and less likely to clip vivid colours.

    Returns:
        A new PIL Image, same size and mode as the input.

    Notes:
        Tone curve runs on luminance and applies uniformly to R/G/B
        so saturation and colour balance stay where the camera put
        them. Shadows / Highlights ride the same LUT. Saturation /
        Vibrance act in chroma space AFTER the tone curve.
    """
    strength = max(0.0, min(strength, 1.0))
    has_tone = (
        strength > 0.0
        or abs(shadows) > 0.001
        or abs(highlights) > 0.001
    )
    has_chroma = abs(saturation) > 0.001 or abs(vibrance) > 0.001

    if not has_tone and not has_chroma:
        return img.copy()

    has_alpha = img.mode == "RGBA"
    rgb = img.convert("RGB")

    if has_tone:
        arr = np.asarray(rgb, dtype=np.uint8)
        luma = (
            0.2126 * arr[..., 0]
            + 0.7152 * arr[..., 1]
            + 0.0722 * arr[..., 2]
        ).astype(np.uint8)

        p_dark = float(np.percentile(luma, dark_percentile))
        p_light = float(np.percentile(luma, light_percentile))

        if p_light - p_dark < 1.0:
            log.debug(
                "auto_exposure: flat image (p%.1f=%.1f p%.1f=%.1f),"
                " skipping tone curve",
                dark_percentile, p_dark, light_percentile, p_light,
            )
        else:
            lut = _build_lut(
                p_dark, p_light, strength, highlight_recovery, highlight_knee,
                contrast_strength=contrast_strength,
                shadows=shadows,
                highlights=highlights,
            )
            out_arr = lut[arr]
            rgb = Image.fromarray(out_arr, mode="RGB")

    if has_chroma:
        if abs(saturation) > 0.001:
            # PIL's Color enhancer: 0 = grayscale, 1 = identity, 2 = double.
            # Map our -1..+1 onto 0..2 so -1 = grayscale, 0 = identity,
            # +1 = 2× saturation.
            factor = 1.0 + max(-1.0, min(1.0, saturation))
            rgb = ImageEnhance.Color(rgb).enhance(factor)
        if abs(vibrance) > 0.001:
            rgb = _apply_vibrance(rgb, vibrance)

    if has_alpha:
        rgb = rgb.convert("RGBA")
        rgb.putalpha(img.getchannel("A"))
    return rgb


def _build_lut(
    p_dark: float,
    p_light: float,
    strength: float,
    highlight_recovery: bool,
    highlight_knee: int = _DEFAULT_HIGHLIGHT_KNEE,
    *,
    contrast_strength: float = 0.0,
    shadows: float = 0.0,
    highlights: float = 0.0,
) -> np.ndarray:
    """Build a 256-entry uint8 LUT that maps input luminance to output.

    Splitting this out makes the algorithm easy to unit-test against
    known anchor points: ``lut[round(p_dark)]`` should be ~0,
    ``lut[round(p_light)]`` should be ~255, and the curve should be
    monotonically non-decreasing in between.

    Pipeline order: percentile stretch → strength blend with identity →
    S-curve (scaled by strength) → shadows / highlights bumps →
    highlight soft-compress.

    Shadows and highlights are independent of ``strength`` because the
    user is correcting localised tonal regions, not just dialling
    auto-exposure intensity. They're shaped to peak at one end of the
    luminance range and decay to zero at the midpoint, so they never
    fight with each other in the mid-tones.
    """
    x = np.arange(256, dtype=np.float32)

    span = p_light - p_dark
    stretched = np.clip((x - p_dark) * (255.0 / span), 0.0, 255.0)

    # Blend with original by ``strength`` so the user can dial it back.
    # 0.0 = identity, 1.0 = full stretch.
    blended = (1.0 - strength) * x + strength * stretched

    # ``contrast_strength`` is bipolar ([-1, +1]): positive bends the
    # tone curve into an S (more punch), negative bends it the other
    # way (flatter, soft look). Scaled by ``strength`` so contrast
    # follows auto-exp intensity — a contrast change with auto-exp off
    # would be confusing.
    effective_contrast = contrast_strength * strength
    if abs(effective_contrast) > 0.001:
        blended = _apply_s_curve(blended, effective_contrast)

    # Shadows / highlights bumps. ``response`` peaks at one end and
    # decays smoothly toward the opposite end. Round 5 (2026-05-01)
    # widened the reach (denominator 128 → 160) and made the falloff
    # sub-quadratic (power 2 → 1.5) so the lift carries further into
    # mid-tones. Amplitude doubled (60 → 90 levels at the extremes)
    # so the slider matches LRC ±100 strength range. Earlier rounds
    # produced visible-but-subtle deltas; round 5 should push the
    # change clearly past the perception threshold.
    if abs(shadows) > 0.001 or abs(highlights) > 0.001:
        s = max(-1.0, min(1.0, shadows))
        h = max(-1.0, min(1.0, highlights))
        shadow_response = np.maximum(0.0, 1.0 - x / 160.0) ** 1.5
        highlight_response = np.maximum(0.0, (x - 96.0) / 159.0) ** 1.5
        blended = blended + s * 90.0 * shadow_response + h * 90.0 * highlight_response

    if highlight_recovery:
        blended = _soft_compress_highlights(blended, highlight_knee)

    return np.clip(blended, 0.0, 255.0).astype(np.uint8)


def _apply_vibrance(img: Image.Image, vibrance: float) -> Image.Image:
    """Saturation-with-falloff: boost low-saturation pixels more
    than already-saturated ones.

    Implementation rides on the chroma vector (per-pixel offset from
    Rec. 709 luma). For each pixel we compute a target chroma
    magnitude that ADDS an absolute amount weighted by how much
    saturation headroom is left:

        target_mag = current_mag + v * 0.30 * (1 - current_sat)

    Then we scale the chroma vector to that target. Adding an
    absolute amount (instead of multiplying by a per-pixel factor)
    means low-sat pixels — whose starting chroma magnitude is small
    — see a much larger relative jump than already-vivid pixels,
    which is the whole point of vibrance vs plain saturation.

    Negative ``vibrance`` shrinks chroma symmetrically so dragging
    the slider left feels like the inverse of dragging it right.
    """
    v = max(-1.0, min(1.0, vibrance))
    arr = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
    max_c = np.max(arr, axis=-1)
    min_c = np.min(arr, axis=-1)
    sat_estimate = max_c - min_c  # 0..1, cheap stand-in for HSV S
    luma = (
        arr[..., 0] * 0.2126
        + arr[..., 1] * 0.7152
        + arr[..., 2] * 0.0722
    )[..., None]
    chroma = arr - luma
    chroma_mag = np.linalg.norm(chroma, axis=-1, keepdims=True)
    # Absolute magnitude delta: 0.30 chosen so vibrance=+1 adds ~0.30
    # of magnitude to a pure-grey pixel — visible boost without
    # turning skin tones lurid. Scaled by (1 - sat_estimate) so
    # already-saturated pixels barely move.
    delta = v * 0.30 * (1.0 - sat_estimate)[..., None]
    target_mag = np.maximum(chroma_mag + delta, 0.0)
    scale = np.where(
        chroma_mag > 1e-6, target_mag / np.maximum(chroma_mag, 1e-6), 1.0,
    )
    out = np.clip(luma + chroma * scale, 0.0, 1.0)
    return Image.fromarray((out * 255.0).astype(np.uint8), mode="RGB")


def _apply_s_curve(curve: np.ndarray, strength: float) -> np.ndarray:
    """Apply a contrast S-curve (or inverse-S) to a 0-255 LUT in float32.

    Bipolar: positive ``strength`` bends mid-tones into an S (darken
    shadows, lift highlights — more punch). Negative bends the other
    way (lift shadows, darken highlights — flatter / soft look).
    Identity at the endpoints (0 stays 0, 255 stays 255) and at the
    pivot 128 in both directions.

    Math: piecewise power curve around 0.5 normalized, exponent
    ``k = 1 + strength``. ``k > 1`` produces an S; ``k < 1`` inverts it.
    ``k`` is clamped to ``[0.1, 2.0]`` so the curve stays well-behaved
    (very small ``k`` would compress everything to mid-grey).
    """
    strength = float(np.clip(strength, -1.0, 1.0))
    if abs(strength) < 0.001:
        return curve
    norm = curve / 255.0
    k = float(np.clip(1.0 + strength, 0.1, 2.0))
    out = np.where(
        norm < 0.5,
        0.5 * np.power(np.clip(2.0 * norm, 0.0, 1.0), k),
        1.0 - 0.5 * np.power(np.clip(2.0 - 2.0 * norm, 0.0, 1.0), k),
    )
    return out * 255.0


def _soft_compress_highlights(
    curve: np.ndarray,
    knee: int = _DEFAULT_HIGHLIGHT_KNEE,
) -> np.ndarray:
    """Smoothly bend the top of the curve so values heading for 255+
    land near ``_HIGHLIGHT_CEILING`` instead. Below ``knee`` the curve
    passes through unchanged — so this is invisible on properly-exposed
    photos and only kicks in when the stretch pushed the highlights too
    hot. Per-scenario profiles tune ``knee`` lower for macro/portrait
    (catch flash specular earlier) and higher for landscape (permissive).
    """
    out = curve.copy()
    ceil = _HIGHLIGHT_CEILING
    above_knee = out > knee
    if not np.any(above_knee):
        return out
    # Map [knee, max(out)] → [knee, ceil] with a quadratic. Picking the
    # quadratic over linear keeps the slope at the knee continuous so
    # the transition isn't visible as a hard line.
    excess = out[above_knee] - knee
    max_excess = float(excess.max()) if excess.size else 1.0
    if max_excess < 1e-3:
        return out
    normalized = excess / max_excess
    compressed = (1.0 - (1.0 - normalized) ** 2) * (ceil - knee)
    out[above_knee] = knee + compressed
    return out
