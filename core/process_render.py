"""Rendering pipeline for the Process Culler.

Loads a photo at full quality, applies the user's auto-exposure +
crop choices, and writes a JPEG to the per-day ``processed/`` folder.
Pure (Qt-free) so the same code runs in tests, batch tools, and
eventually a CLI.

Pipeline
--------
1. **Load** — ``load_photo_full(path)`` returns a PIL Image with EXIF
   orientation applied:
   * JPEG / HEIC / TIFF / PNG: opened by PIL directly
   * RAW: the embedded preview is extracted via rawpy (full demosaic
     would be 1–3 s per shot, untenable for a 1000-photo trip; the
     embedded preview is 1600–2400 px JPEG which is plenty for a
     slideshow / share output)

2. **Auto-exposure** — when enabled, ``core.exposure_engine.auto_exposure``
   stretches the histogram percentiles.

3. **Crop** — when an aspect ratio is set, the rect (normalized 0–1
   coords) is applied to the post-orientation image. ``None`` means
   "use the maximal centered crop for the requested ratio".

4. **Save** — JPEG, quality 95, EXIF *not* preserved (the source
   organism keeps the original; ``processed/`` is a clean export).

Output naming follows ``HHMMSS_<orig_stem>.jpg`` so files sort
chronologically per day even when the underlying scenarios are mixed.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageEnhance, ImageOps

from core.aspect_ratio import AspectRatio, get_aspect_ratio
from core.exposure_engine import auto_exposure

log = logging.getLogger(__name__)


# Extensions handled by PIL directly. RAW formats fall through to
# rawpy.extract_thumb() in load_photo_full().
_PIL_NATIVE_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".heic", ".heif", ".tif", ".tiff", ".png"}
)


JPEG_OUTPUT_QUALITY = 95


# Camera baseline simulation — applied AFTER the bare rawpy demosaic
# and BEFORE per-scenario auto-exposure corrections. Bridges the gap
# between LibRaw's neutral demosaic and the heavily-processed JPEG
# that the camera embeds in the .RW2 (PhotoStyle "Standard" + auto-
# tone + sharpening + colour science). Targets a "reasonable JPEG
# straight out of camera" look so per-scenario corrections operate
# from a similar starting point to LRC's auto-tone.
#
# Only applied to the RAW path of ``load_photo_full`` — JPEG / HEIC
# inputs already come cooked by their respective devices. Setting
# any constant to 1.0 disables that step.
#
# Costa Rica calibration 2026-05-01.
# Calibrated against ``tools/calibrate_camera_baseline.py`` over
# the test set in ``D:/exposição``. The pure data-derived ratios
# (brightness 2.15, saturation 1.86, contrast 1.81) caused
# hue distortions when applied as PIL ImageEnhance multipliers:
# linear extrapolation past factor 1 saturates one channel while
# pulling others down, shifting yellows toward red, beige toward
# yellow, and skin toward orange-red. The Panasonic embedded
# preview achieves the same luma / chroma stats via hue-aware
# colour science we can't replicate with uniform PIL filters.
#
# Compromise: keep BRIGHTNESS lower + add a GAMMA curve to lift
# midtones (uniform on luma so it can't shift hues), and
# moderate SATURATION / CONTRAST below the data targets to stay
# in PIL's safe extrapolation band.
CAMERA_BASELINE_GAMMA = 0.82       # < 1 lifts midtones, 1.0 = identity
CAMERA_BASELINE_BRIGHTNESS = 1.60  # multiplicative on top of gamma
CAMERA_BASELINE_SATURATION = 1.20  # modest pop, no hue extrapolation
CAMERA_BASELINE_CONTRAST = 1.20    # modest punch


def _apply_camera_baseline(img: Image.Image) -> Image.Image:
    """Push a freshly-demosaiced RAW frame toward the look of the
    camera's embedded JPEG (PhotoStyle Standard equivalent).

    Gamma runs first because it operates on the entire 0-255 range
    via a power curve — lifts midtones substantially without
    clipping any channel. Brightness / Saturation / Contrast then
    refine in PIL's standard ImageEnhance order. Setting any
    constant to its identity (1.0 for the multipliers, 1.0 for
    gamma) disables that step.
    """
    if CAMERA_BASELINE_GAMMA != 1.0:
        arr = np.asarray(img, dtype=np.float32) / 255.0
        arr = np.power(arr, CAMERA_BASELINE_GAMMA)
        arr = np.clip(arr * 255.0, 0.0, 255.0).astype(np.uint8)
        img = Image.fromarray(arr, mode="RGB")
    if CAMERA_BASELINE_BRIGHTNESS != 1.0:
        img = ImageEnhance.Brightness(img).enhance(CAMERA_BASELINE_BRIGHTNESS)
    if CAMERA_BASELINE_SATURATION != 1.0:
        img = ImageEnhance.Color(img).enhance(CAMERA_BASELINE_SATURATION)
    if CAMERA_BASELINE_CONTRAST != 1.0:
        img = ImageEnhance.Contrast(img).enhance(CAMERA_BASELINE_CONTRAST)
    return img


def load_photo_preview(path: Path) -> Image.Image:
    """Load a fast preview-quality copy of ``path``.

    For RAW files this returns the camera's embedded JPEG thumbnail
    (typically 1920×1440 on a G9 II), which decodes in ~50 ms versus
    1–2 s for a full demosaic. Plenty of pixels for the live editor —
    the user is comparing global tone curves and scrubbing crops, not
    pixel-peeping. JPEG/HEIC inputs return at full resolution because
    decoding them is already cheap.

    EXIF orientation is baked in so callers can treat the pixels as
    upright without rechecking. Mode is forced to RGB.

    Use ``load_photo_full`` instead when actually exporting.
    """
    suffix = path.suffix.lower()
    if suffix in _PIL_NATIVE_EXTENSIONS:
        # pillow-heif registers the .heic/.heif handlers globally when
        # ui.culler.viewer is imported in normal app flow; tests of
        # this module can register it explicitly if they need HEIC.
        img = Image.open(path)
    else:
        # Lazy import — rawpy pulls in libraw which is large; we only
        # want to pay for it when actually decoding a RAW file.
        import rawpy

        with rawpy.imread(str(path)) as raw:
            thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                img = Image.open(io.BytesIO(thumb.data))
            else:
                img = Image.fromarray(thumb.data)

    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    return img


def load_photo_full(path: Path) -> Image.Image:
    """Load a photo at the camera's native resolution.

    For RAW: runs ``rawpy.postprocess`` to do a full demosaic (5776×4336
    on a G9 II vs the embedded thumb's 1920×1440). Slower than
    ``load_photo_preview`` (1–2 s vs ~50 ms) but produces the actual
    sensor-resolution image. Use this for the export step where pixel
    quality matters; use ``load_photo_preview`` for the live editor
    where speed matters.

    Demosaic flags are tuned to give us a neutral starting point so
    our auto-exposure has full control:
      * ``no_auto_bright=True`` disables rawpy's own histogram stretch.
      * ``use_camera_wb=True`` honours the camera's white balance.
      * ``output_color=sRGB`` and ``output_bps=8`` produce the same
        colour space and bit depth that JPEG can store, so the
        downstream auto-exposure LUT operates in the right range.

    Costa Rica re-test 2026-05-01: Nelson noticed our exports were
    ~3 MP vs LRC's 25 MP — root cause was load_photo_full silently
    using the embedded preview.
    """
    suffix = path.suffix.lower()
    if suffix in _PIL_NATIVE_EXTENSIONS:
        img = Image.open(path)
    else:
        import rawpy
        import numpy as np

        with rawpy.imread(str(path)) as raw:
            arr = raw.postprocess(
                output_bps=8,
                use_camera_wb=True,
                no_auto_bright=True,
                output_color=rawpy.ColorSpace.sRGB,
                # 2026-05-01 demosaic-quality round (Costa Rica
                # calibration): produce a bright, detailed RAW
                # conversion BEFORE any auto-exposure correction
                # layer runs. ``exp_shift=0.5`` (~+½ stop) AND
                # ``bright=1.3`` (~+0.4 stop) stacked ≈ +0.9 stop
                # above LibRaw default — brings the baseline closer
                # to where Adobe Camera Raw's auto-tone tends to
                # land, so per-scenario corrections (when they're
                # re-introduced) start from a comparable ground.
                #
                # Tried ``demosaic_algorithm=AMAZE`` for sharper
                # detail recovery; it requires the rawpy GPL3
                # demosaic pack which would contaminate the project
                # licence. Sticking with the default LGPL algorithm.
                exp_shift=0.5,
                bright=1.3,
            )
            # postprocess returns a numpy array (H, W, 3) uint8
            img = Image.fromarray(np.asarray(arr, dtype="uint8"))
        # Push the bare rawpy demosaic toward the look of the camera's
        # embedded JPEG (PhotoStyle Standard equivalent). Only the RAW
        # path applies this — JPEG/HEIC sources already come cooked.
        img = _apply_camera_baseline(img)

    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    return img


def compute_default_crop(
    img_w: int,
    img_h: int,
    ratio: AspectRatio,
) -> Optional[tuple[float, float, float, float]]:
    """Return the maximal centered crop matching ``ratio`` as
    normalized ``(x, y, w, h)`` in [0, 1].

    Returns ``None`` for the Original ratio (no crop), so callers can
    distinguish "user picked Original" from "user hasn't picked a
    ratio yet" via the same falsy check.
    """
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


def apply_crop(
    img: Image.Image,
    crop_norm: tuple[float, float, float, float],
) -> Image.Image:
    """Crop ``img`` by a normalized ``(x, y, w, h)`` rectangle. Values
    are clamped to [0, 1] so a slightly out-of-bounds rect (numerical
    drift, weird user drag) doesn't raise."""
    x, y, w, h = crop_norm
    x = max(0.0, min(1.0, x))
    y = max(0.0, min(1.0, y))
    w = max(0.0, min(1.0 - x, w))
    h = max(0.0, min(1.0 - y, h))
    if w <= 0 or h <= 0:
        return img.copy()
    iw, ih = img.size
    left = int(round(x * iw))
    top = int(round(y * ih))
    right = int(round((x + w) * iw))
    bottom = int(round((y + h) * ih))
    return img.crop((left, top, right, bottom))


def apply_rotation(img: Image.Image, rotation: int) -> Image.Image:
    """Rotate by ``rotation`` degrees (0/90/180/270, clockwise).

    PIL's ``Image.rotate`` is counter-clockwise, so we negate the
    angle to give callers the conventional clockwise sign. ``expand``
    is on so a 90° rotation actually swaps width and height instead
    of clipping the corners off the original frame.
    """
    angle = rotation % 360
    if angle == 0:
        return img
    return img.rotate(-angle, expand=True)


def apply_crop_tilt(img: Image.Image, angle_degrees: float) -> Image.Image:
    """Rotate ``img`` by a small free angle (task #117 — rotated crop).

    Distinct from :func:`apply_rotation` (which handles bulk 0/90/
    180/270 with ``expand=True``): this is the tilt step that runs
    just before the crop, with ``expand=False`` so the canvas stays
    the same size — corners that swing outside the original frame
    become black (PIL default fill). Bicubic resampling because the
    user usually tilts only a few degrees and the result is the
    final exported pixels.

    Sign convention matches :func:`apply_rotation`: positive angle
    rotates clockwise (PIL's native ``rotate`` is counter-clockwise,
    so we negate). Near-zero angles short-circuit to the identity to
    avoid touching every pixel of a 24 MP file for ~0.0001° of
    floating-point drift.
    """
    if abs(angle_degrees) < 1e-3:
        return img
    return img.rotate(
        -float(angle_degrees), resample=Image.Resampling.BICUBIC,
        expand=False,
    )


def render_processed(
    img: Image.Image,
    *,
    rotation: int = 0,
    auto_exposure_on: bool,
    aspect_ratio_label: str,
    crop_norm: Optional[tuple[float, float, float, float]] = None,
    crop_angle: float = 0.0,
    strength: float = 0.85,
    highlight_recovery: bool = True,
    dark_percentile: float = 1.0,
    light_percentile: float = 99.0,
    highlight_knee: int = 235,
    contrast_strength: float = 0.0,
    shadows: float = 0.0,
    highlights: float = 0.0,
    saturation: float = 0.0,
    vibrance: float = 0.0,
) -> Image.Image:
    """Apply the user's choices to a loaded image and return the result.

    Order: 90°-rotation → auto-exposure → free-angle tilt → crop.

    The 90° rotation (``rotation`` ∈ {0, 90, 180, 270}) must come
    first because ``crop_norm`` is normalized over the *post-90°*
    image. Auto-exposure is orientation-agnostic and runs before the
    tilt so the histogram math sees the largest pixel set (the tilt
    blacks-out the corners). The free-angle ``crop_angle`` tilt (task
    #117, ``expand=False``) sits just before the crop so the user's
    rect is sampled from the tilted image — corner-cut by the
    tilt's black fill if the rect extends past the rotated content.

    ``crop_norm`` overrides the default centered crop. Pass ``None``
    to let this function compute the maximal centered crop from the
    aspect ratio (the typical case for the first frame of every shot
    before the user moves anything).

    ``crop_angle`` defaults to 0.0 (no tilt) so old callers /
    journals render unchanged. Range expected within ±15°; the
    session-level setter clamps to ±45° as a sanity bound.

    ``highlight_recovery`` softens the top of the tone curve so
    specular highlights don't slam to 255. Off by intent for night /
    long-exposure shots where stars and point lights *should* clip
    cleanly to white.
    """
    out = img
    if rotation:
        out = apply_rotation(out, rotation)
    if auto_exposure_on:
        out = auto_exposure(
            out,
            strength=strength,
            highlight_recovery=highlight_recovery,
            dark_percentile=dark_percentile,
            light_percentile=light_percentile,
            highlight_knee=highlight_knee,
            contrast_strength=contrast_strength,
            shadows=shadows,
            highlights=highlights,
            saturation=saturation,
            vibrance=vibrance,
        )

    if crop_angle:
        out = apply_crop_tilt(out, crop_angle)

    ratio = get_aspect_ratio(aspect_ratio_label)
    if not ratio.is_original:
        rect = crop_norm if crop_norm is not None else compute_default_crop(
            out.size[0], out.size[1], ratio,
        )
        if rect is not None:
            out = apply_crop(out, rect)
    return out


def output_filename(timestamp: datetime, source: Path) -> str:
    """Build the per-day output filename: ``HHMMSS_<orig_stem>.jpg``.

    Uses the EXIF capture timestamp (passed in by the session) so the
    chronological prefix matches when the photo was taken, not when it
    was processed. Stem comes from the source so the original asset is
    easy to locate from its export.
    """
    return f"{timestamp.strftime('%H%M%S')}_{source.stem}.jpg"


def save_jpeg(
    img: Image.Image, dest: Path, *, quality: Optional[int] = None,
) -> Path:
    """Persist ``img`` to ``dest`` as a JPEG. ``quality=None`` reads
    the user-tunable ``jpeg_export_quality`` Setting (Nelson 2026-06-09
    audit promotion); explicit ``quality=`` overrides per-call.

    Falls back to the module constant ``JPEG_OUTPUT_QUALITY`` if
    Settings can't be read."""
    if quality is None:
        try:
            from mira.settings.repo import SettingsRepo
            quality = int(SettingsRepo().load().jpeg_export_quality)
        except Exception:                                       # noqa: BLE001
            quality = JPEG_OUTPUT_QUALITY
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(dest), format="JPEG", quality=int(quality), optimize=True)
    return dest
