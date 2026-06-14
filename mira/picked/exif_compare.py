"""Two-photo EXIF comparison for the cull compare grid (M2.5, approved improvement).

Nelson 2026-06-01: when the photo compare grid shows **exactly two photos**, highlight the
EXIF parameter(s) that differ — so the eye lands on what changed between two near-identical
shots (the controlled A/B: bracketed shutter speed or aperture). Guarded so it only fires
for a genuine comparison, never for two unrelated photos.

Pure logic (no Qt) over ``core.exif_reader.ExifData`` — unit-tested in isolation; the grid
UI consumes :func:`exposure_diff` to decide which tile values to emphasise.

The rule (exactly as Nelson framed it):

* **Compare set** = shutter speed, aperture, ISO, focal length.
* **Suppress all highlighting** (return ``None``) when EITHER the two photos are a
  **different style** (both carry a classification and they differ — they're different
  photos, not an A/B), OR **more than two** of the compare-set params differ (too different
  to be a controlled comparison).
* **Otherwise** return the list of differing params (0–2) to highlight. An empty list means
  "comparable, but identical" — nothing to emphasise, which is distinct from ``None``
  ("suppressed").
"""
from __future__ import annotations

from typing import Any, List, Optional

# The compare set — ``ExifData`` attribute names, in display order. Shutter speed first
# (it's the most common bracketing axis and the one the cull readout was missing).
COMPARE_PARAMS: tuple[str, ...] = ("shutter_speed", "aperture", "iso", "focal_length")

# Per-param normalisation for the equality test — round away float noise so 6.3 == 6.30001.
_ROUND = {"shutter_speed": 6, "aperture": 2, "focal_length": 1, "iso": 0}

# Above this many differing params, the two photos aren't a controlled comparison.
_MAX_DIFFS = 2

# Show the exposure overlay on grid tiles only for a SMALL comparison grid (Nelson
# 2026-06-01): a contact sheet of many photos stays clean; ≤ this many gets the readout.
GRID_CAPTION_MAX = 4

# Accent for the emphasised (differing) value — the Gulf orange used across the app.
_HIGHLIGHT = "#F37021"


def fmt_param(param: str, value) -> str:
    """Human-readable compare-param value for an exposure caption. Empty string for
    absent/zero (the EXIF reader's 'unknown')."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return ""
    if f == 0.0:
        return ""
    if param == "shutter_speed":
        return f"1/{int(round(1.0 / f))}s" if 0 < f < 1.0 else f"{f:g}s"
    if param == "aperture":
        return f"f/{f:g}"
    if param == "iso":
        return f"ISO {int(f)}"
    if param == "focal_length":
        return f"{f:g}mm"
    return ""


def exposure_text(exif: Any) -> str:
    """Plain (non-HTML) exposure readout — ``shutter · aperture · ISO · focal`` — for a
    single-photo info line (any object exposing the COMPARE_PARAMS attrs, incl. SourceItem).
    Empty when ``exif`` is missing or has no usable values."""
    if exif is None:
        return ""
    return "  ·  ".join(
        t for t in (fmt_param(p, getattr(exif, p, None)) for p in COMPARE_PARAMS) if t)


def caption_html(exif: Any, highlight: Optional[List[str]] = None) -> str:
    """One tile's exposure caption (rich text). ``highlight`` = the params to emphasise (the
    differing ones in a 2-photo compare), or ``None`` for a plain readout (no emphasis).
    Empty string when ``exif`` is missing or has no usable values."""
    if exif is None:
        return ""
    parts: List[str] = []
    for p in COMPARE_PARAMS:
        text = fmt_param(p, getattr(exif, p, None))
        if not text:
            continue
        if highlight and p in highlight:
            parts.append(f"<b style='color:{_HIGHLIGHT}'>{text}</b>")
        else:
            parts.append(text)
    return "  ·  ".join(parts)


def _norm(param: str, value: Any) -> Optional[float]:
    """A comparable scalar for one param, or ``None`` when absent/zero (the EXIF reader
    uses 0/0.0 for 'unknown'). ``None`` never equals a real value, so a missing field on
    one side reads as a difference — which is the honest signal."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f == 0.0:
        return None
    return round(f, _ROUND.get(param, 2))


def _same_style(class_a: Optional[str], class_b: Optional[str]) -> bool:
    """Two photos count as the *same* style unless BOTH are classified and the
    classifications differ (an unclassified side can't assert 'different photo')."""
    return not (class_a and class_b and class_a != class_b)


def exposure_diff(
    exif_a: Any,
    exif_b: Any,
    class_a: Optional[str] = None,
    class_b: Optional[str] = None,
) -> Optional[List[str]]:
    """Which compare-set params to highlight for a 2-photo comparison, or ``None`` to
    suppress all highlighting (different style, or more than two params differ).

    ``exif_a``/``exif_b`` are ``ExifData`` (or anything exposing the COMPARE_PARAMS
    attributes); ``class_a``/``class_b`` are the photos' classifications (genres)."""
    if exif_a is None or exif_b is None:
        return None
    if not _same_style(class_a, class_b):
        return None
    diffs = [
        p for p in COMPARE_PARAMS
        if _norm(p, getattr(exif_a, p, None)) != _norm(p, getattr(exif_b, p, None))
    ]
    if len(diffs) > _MAX_DIFFS:
        return None
    return diffs
