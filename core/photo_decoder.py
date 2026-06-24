"""Photo file decoder — read any supported camera format into a
uint8 RGB numpy array.

Format triage by extension:

* **RAW** (.RW2, .NEF, .CR2/.CR3, .ARW, .DNG, .RAF, etc.) → ``rawpy``
  demosaic with camera white-balance, sRGB-ish gamma, 8-bit output.
* **HEIC** (.HEIC/.HEIF) → ``pillow-heif`` → Pillow conversion.
* **JPEG / TIFF / PNG** → ``Pillow`` directly.

Output is always a ``(H, W, 3)`` uint8 RGB array — the shape and
dtype :mod:`core.photo_render` expects. Decoding strips EXIF
orientation by applying it (so portrait phone shots arrive
upright); the photo bytes on disk are NOT modified.

Pure-Python, no Qt; safe off the GUI thread. RAW decode is the
expensive step (~200-500 ms per file on typical hardware) — callers
should cache the result for the duration of the user's edit
session.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageOps

log = logging.getLogger(__name__)


# ── Format detection ───────────────────────────────────────────

# RAW formats handled by rawpy (libraw). Comprehensive list copied
# from libraw's supported-cameras manifest, filtered to the formats
# Mira's target bodies actually produce.
RAW_EXTENSIONS: frozenset[str] = frozenset({
    ".rw2",                                       # Panasonic Lumix
    ".nef", ".nrw",                               # Nikon
    ".cr2", ".cr3", ".crw",                       # Canon
    ".arw", ".srf", ".sr2",                       # Sony
    ".raf",                                       # Fujifilm
    ".orf", ".ori",                               # Olympus / OM System
    ".pef", ".ptx",                               # Pentax
    ".rwl",                                       # Leica
    ".dng",                                       # Adobe / generic
})

HEIC_EXTENSIONS: frozenset[str] = frozenset({
    ".heic", ".heif",                             # iPhone / Android
})

# Pillow-natively decodes JPEG / PNG / TIFF / BMP / WebP and more;
# we whitelist the photo-relevant set so a stray PDF or GIF errors
# cleanly.
PILLOW_EXTENSIONS: frozenset[str] = frozenset({
    ".jpg", ".jpeg", ".jpe",
    ".png",
    ".tif", ".tiff",
    ".webp",
    ".bmp",
})


def is_supported(path: Path) -> bool:
    """True iff this file's extension is one we know how to decode."""
    ext = path.suffix.lower()
    return (
        ext in RAW_EXTENSIONS
        or ext in HEIC_EXTENSIONS
        or ext in PILLOW_EXTENSIONS
    )


# ── Decode ─────────────────────────────────────────────────────


def decode_image(
    path: Path, *,
    raw_half_size: bool = False,
    target_long_edge: Optional[int] = None,
) -> np.ndarray:
    """Decode ``path`` to a uint8 RGB array ``(H, W, 3)``. Raises
    ``ValueError`` for unsupported extensions, ``FileNotFoundError``
    for missing files, and the underlying library's exception for
    any decode failure (so callers can inspect the cause).

    EXIF orientation is **applied** (so a phone portrait shot comes
    back rotated upright). The source file is never modified.

    ``raw_half_size`` (spec/63 §6, Nelson's Q2 ruling 2026-06-12):
    RAW decodes at libraw's half-size (~233 ms vs ~615 ms) — the Edit
    working copy. Tone choices and the 1280-px preview are
    resolution-insensitive, and export re-decodes full independently;
    only the RAW path changes, JPEG/HEIC are unaffected.

    ``target_long_edge`` (spec/135 — preview/proxy speedup): when set,
    decode at the **largest reduced scale whose long edge stays ≥
    target**. Skips wasted full-res decode work when the caller will
    downsample to a known bound anyway (proxy = 2560, preview ~2400).
    The decoded image is always ≥ ``target_long_edge`` on the long
    edge (the caller's final resize is responsible for the exact
    bound; honours :func:`qualifies_as_proxy`'s "no under-target
    pixels" rule). A small source is decoded full-size unchanged.

    * **JPEG / TIFF / etc. (Pillow):** uses ``Image.draft`` so libjpeg
      runs its DCT-domain downscale (~2–4× faster than full decode on
      6000-px+ JPEGs). Non-JPEG formats ignore the hint cleanly.
    * **RAW:** promotes to ``half_size`` when the sensor's long edge
      is ≥ ~2× ``target_long_edge``. The embedded-preview path used by
      ``ensure_photo_proxy`` is independent and unchanged.
    * **HEIC:** routed through Pillow; pillow-heif's plugin honours
      the draft hint when its backend supports DCT scaling, otherwise
      decodes full-res (same as today).
    """
    if not path.exists():
        raise FileNotFoundError(path)
    ext = path.suffix.lower()

    if ext in RAW_EXTENSIONS:
        return _decode_raw(
            path, half_size=raw_half_size,
            target_long_edge=target_long_edge)
    if ext in HEIC_EXTENSIONS:
        return _decode_heic(path, target_long_edge=target_long_edge)
    if ext in PILLOW_EXTENSIONS:
        return _decode_pillow(path, target_long_edge=target_long_edge)
    raise ValueError(
        f"unsupported file extension {ext!r} for {path.name}"
    )


# ── Per-format decoders ────────────────────────────────────────


def _decode_raw(
    path: Path, *,
    half_size: bool = False,
    target_long_edge: Optional[int] = None,
) -> np.ndarray:
    """RAW → uint8 RGB. Uses rawpy's default-quality demosaic with
    camera white-balance, sRGB-ish output gamma, and **rawpy's default
    auto-bright** so the user sees the camera-default look (matches
    LRC / Windows Photos / the embedded preview JPG) when AUTO is OFF.
    Output is 8-bit; this is the working surface for Process-phase
    preview + export.

    spec/135 — when ``target_long_edge`` is set and the sensor's long
    edge is ≥ ~2× the target, promote to ``half_size=True`` so the
    demosaic runs at half resolution (~233 ms vs ~615 ms). The result
    still beats the target on the long edge (the 2× check guarantees
    it); the caller's final resize lands the exact bound.

    **Auto-bright history (Nelson 2026-06-09).** This previously passed
    ``no_auto_bright=True`` so the AUTO logic analysed the un-stretched
    sensor data.  Trade-off: AUTO OFF showed a very dark photo (raw
    sensor floor), which user-tested as "looks like the photo was
    processed somehow" — the opposite of the intent.  Flipping to
    rawpy's default auto-bright matches Nelson's mental model of
    "original = camera-default brightness".  Side-effect: ``compute_
    auto_params`` now sees an already-stretched image, so its
    suggestions are subtler (less aggressive exposure pushes).  May
    need a calibration pass — see :memory:`backlog_video_adjustment_
    calibration` and the AUTO tuning project.
    """
    import rawpy

    with rawpy.imread(str(path)) as raw:
        # spec/135 — promote to half_size when the sensor is big enough
        # to absorb the divisor without crossing the target. Skipped
        # when the caller already asked for half_size (no double
        # reduction; rawpy doesn't offer ÷4) or didn't ask for a
        # target.
        effective_half = bool(half_size)
        if (
            not effective_half
            and target_long_edge is not None
            and target_long_edge > 0
        ):
            try:
                sizes = raw.sizes
                # ``raw_width / raw_height`` are the full sensor; the
                # post-demosaic output is essentially those dims minus
                # crop margins. The 2× rule is conservative — half_size
                # halves both axes, so we need the full long edge to
                # be at least 2× target to stay safely above the bound.
                full_long = max(
                    int(getattr(sizes, "width", 0)),
                    int(getattr(sizes, "height", 0)),
                )
                if full_long >= 2 * int(target_long_edge):
                    effective_half = True
            except Exception:                                      # noqa: BLE001
                # rawpy.sizes access shouldn't fail; if it does just
                # fall through to the full demosaic (correctness wins).
                log.debug(
                    "rawpy.sizes probe failed for %s; full demosaic",
                    path, exc_info=True)

        # AHD demosaic (libraw default — good quality, reasonable
        # speed). ``use_camera_wb=True`` honours the in-camera WB
        # setting (matches what LRC does by default and what the
        # JPEG-pair sees).  ``no_auto_bright=False`` (rawpy default)
        # applies the histogram stretch so AUTO-OFF reads as
        # "camera-default brightness" (Nelson 2026-06-09).
        rgb = raw.postprocess(
            use_camera_wb=True,
            no_auto_bright=False,
            output_bps=8,
            gamma=(2.222, 4.5),
            half_size=effective_half,
        )
    if rgb.dtype != np.uint8:
        rgb = rgb.astype(np.uint8)
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise RuntimeError(
            f"rawpy returned unexpected shape {rgb.shape} for {path}"
        )
    return rgb


def _decode_heic(
    path: Path, *,
    target_long_edge: Optional[int] = None,
) -> np.ndarray:
    """HEIC → uint8 RGB. Uses pillow-heif's Pillow-compatibility
    plugin so the rest of the pipeline mirrors the JPEG path."""
    # Register the HEIF opener on first use (idempotent).
    import pillow_heif
    pillow_heif.register_heif_opener()
    return _decode_pillow(path, target_long_edge=target_long_edge)


def _decode_pillow(
    path: Path, *,
    target_long_edge: Optional[int] = None,
) -> np.ndarray:
    """JPEG / TIFF / PNG / HEIC (post-registration) → uint8 RGB.
    Applies EXIF orientation in-memory.

    spec/135 — when ``target_long_edge`` is set, computes the largest
    JPEG DCT divisor in ``{8, 4, 2, 1}`` whose result long edge stays
    ≥ target, then asks :meth:`PIL.Image.draft` for the exact post-
    divisor dims so Pillow's JPEG codec picks that scale. The
    "result long edge ≥ target" rule lines up with
    ``qualifies_as_proxy``'s no-under-target invariant — sub-target
    pixels would soften the proxy.

    Why compute the divisor ourselves: Pillow 12's
    :meth:`Image.draft` requires BOTH scaled dims ≥ the requested
    dims, so passing a symmetric ``(T, T)`` on a non-square source
    refuses to scale (the short edge falls below T). Passing the
    asymmetric per-divisor dims lets the codec pick the divisor we
    want. Non-JPEG formats ignore the hint cleanly (the try/except
    is defence in depth — only JPEG/MPO carry the DCT)."""
    with Image.open(path) as img:
        if target_long_edge is not None and target_long_edge > 0:
            try:
                ow, oh = img.size
                long_dim = max(int(ow), int(oh))
                if long_dim > int(target_long_edge):
                    divisor = 1
                    for s in (8, 4, 2):
                        if long_dim // s >= int(target_long_edge):
                            divisor = s
                            break
                    if divisor > 1:
                        img.draft(
                            "RGB",
                            (int(ow) // divisor, int(oh) // divisor),
                        )
            except Exception:                                      # noqa: BLE001
                pass    # non-JPEG formats simply ignore draft
        # ImageOps.exif_transpose rotates / flips per the EXIF
        # ``Orientation`` tag so portrait shots arrive upright. The
        # source file on disk is untouched.
        oriented = ImageOps.exif_transpose(img)
        rgb = oriented.convert("RGB")
        return np.array(rgb)
