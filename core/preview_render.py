"""Live develop preview for spec/89 §11.3 — render a source photo
through Mira's full develop pipeline so the Export preview viewer can
show "what would the next Export run produce?" for 0-version cells and
virtual Mira cluster members.

Pure-logic (no Qt). Mirrors the photo branch of
:meth:`mira.ui.pages.editor_page.EditorPage._develop_array_for_lens`
so the preview viewer ships the same pixels the spec/60 batch would,
modulo a small target downscale (the preview is bounded by the
dialog's max size, not full Export resolution).

Pipeline (matches the AdjustmentSurface render order):
    rotation → tone (look_params via photo_auto) → creative filter →
    crop / straighten.

For a duck-typed :class:`~mira.store.models.Adjustment`-ish row;
missing fields fall back to baseline values. Returns ``None`` on any
decode / pipeline failure so the caller can fall back to the
source-photo raw read.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import numpy as np

log = logging.getLogger(__name__)


_PREVIEW_MAX_LONG_EDGE = 2400


def _downscale_if_huge(arr: np.ndarray, max_long_edge: int) -> np.ndarray:
    """Bound the long edge so the develop pipeline doesn't pay
    full-resolution cost on a preview that the dialog will paint at
    ~2400 px anyway. The bound is non-aggressive — small photos stay
    intact; only ones past the threshold get scaled down."""
    if max_long_edge <= 0:
        return arr
    h, w = arr.shape[:2]
    long_edge = max(h, w)
    if long_edge <= max_long_edge:
        return arr
    try:
        from PIL import Image
    except Exception:                                              # noqa: BLE001
        log.debug(
            "preview-render: PIL unavailable; skipping downscale")
        return arr
    scale = max_long_edge / float(long_edge)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    img = Image.fromarray(arr)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    return np.asarray(img)


def develop_photo_array(
    source_path: Path,
    adjustment: Optional[Any],
    style_fallback: str = "general",
    *,
    max_long_edge: int = _PREVIEW_MAX_LONG_EDGE,
) -> Optional[np.ndarray]:
    """Decode ``source_path`` and run it through the Mira develop
    pipeline using ``adjustment``'s settings. Returns the developed
    ``np.ndarray`` (uint8 H×W×3) or ``None`` on any failure (the
    caller is expected to fall back to a raw source read).

    ``adjustment`` is duck-typed: a None value or a baseline-default
    row yields the identity-developed source (still scaled). The
    ``style_fallback`` is used when ``adjustment.style`` is empty.
    """
    try:
        from core.photo_auto import (
            compute_auto_params,
            creative_filter_amount,
            look_params_from_natural,
            resolve_filter_recipe,
        )
        from core.photo_decoder import decode_image
        from core.photo_render import (
            FilterRecipe,
            apply_crop_norm,
            apply_filter,
            apply_params,
            apply_rotation,
            extract_rotated_crop,
        )
    except Exception:                                              # noqa: BLE001
        log.exception("preview-render: import failed")
        return None

    try:
        arr = decode_image(Path(source_path))
    except Exception:                                              # noqa: BLE001
        log.warning(
            "preview-render: decode failed for %s", source_path,
            exc_info=True)
        return None

    # Bound the long edge so the pipeline isn't full-res for a small
    # dialog. Skipped when the source is already small enough.
    arr = _downscale_if_huge(arr, max_long_edge)

    look_key = (
        (getattr(adjustment, "look", None) or "original").strip()
        or "original")
    style_key = (
        getattr(adjustment, "style", None) or style_fallback)
    creative_filter = getattr(adjustment, "creative_filter", None)
    look_strength = float(getattr(adjustment, "look_strength", 1.0) or 1.0)
    rotation = int(getattr(adjustment, "rotation", 0) or 0)
    crop = None
    if all(getattr(adjustment, k, None) is not None
           for k in ("crop_x", "crop_y", "crop_w", "crop_h")):
        crop = (
            float(adjustment.crop_x), float(adjustment.crop_y),
            float(adjustment.crop_w), float(adjustment.crop_h),
        )
    box_angle = float(getattr(adjustment, "crop_angle", 0.0) or 0.0)

    try:
        out = arr
        if rotation:
            out = apply_rotation(out, rotation)
        natural_params = compute_auto_params(out, style=style_key)
        params = look_params_from_natural(
            natural_params, look_key, strength=look_strength)
        if not params.is_identity:
            out = apply_params(out, params)
        if creative_filter:
            recipe = resolve_filter_recipe(creative_filter, style_key)
            if recipe is not None:
                out = apply_filter(
                    out, FilterRecipe.from_dict(recipe),
                    creative_filter_amount(creative_filter))
        if crop is not None:
            if box_angle:
                out = extract_rotated_crop(out, crop, box_angle)
            else:
                out = np.ascontiguousarray(apply_crop_norm(out, crop))
        return out
    except Exception:                                              # noqa: BLE001
        log.exception(
            "preview-render: pipeline failed for %s", source_path)
        return None


__all__ = ["develop_photo_array"]
