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

#: Per-param attribute-name fallbacks: the gateway's store Item
#: (`mira/store/models.py`) writes the suffix-aliased forms
#: (``shutter_speed_s`` / ``aperture_f`` / ``focal_length_mm``) while
#: the EXIF reader and ``SourceItem`` use the bare canonical names.
#: :func:`_resolve_value` walks this tuple per param and returns the
#: first attribute that's set, so callers can pass EITHER shape (live
#: EXIF, SourceItem, store Item) without caring which.
_PARAM_ALIASES: dict = {
    "shutter_speed": ("shutter_speed", "shutter_speed_s"),
    "aperture":      ("aperture", "aperture_f"),
    "focal_length":  ("focal_length", "focal_length_mm"),
    "iso":           ("iso",),
}

# Per-param normalisation for the equality test — round away float noise so 6.3 == 6.30001.
_ROUND = {"shutter_speed": 6, "aperture": 2, "focal_length": 1, "iso": 0}


def _resolve_value(obj: Any, param: str) -> Any:
    """Read ``param`` off ``obj``, trying every alias in
    :data:`_PARAM_ALIASES`. Returns the first non-None / non-zero
    value it finds, or ``None`` when none of the aliases is set —
    so the readout drops cleanly on a missing field regardless of
    which object shape the caller passed in.

    A literal zero is treated as "missing" (the EXIF reader uses
    0/0.0 for unknown), so a populated alias on the same object
    still wins. This makes a partially-extracted live ``PhotoExif``
    (model set, exposure zeroed) fall back to the store ``Item``
    where ingest already wrote the values.
    """
    for name in _PARAM_ALIASES.get(param, (param,)):
        value = getattr(obj, name, None)
        if value is None:
            continue
        # 0 / 0.0 is the EXIF reader's "unknown" sentinel — keep
        # walking the aliases so a populated suffixed alias wins.
        try:
            if float(value) == 0.0:
                continue
        except (TypeError, ValueError):
            continue
        return value
    return None

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
    single-photo info line. Accepts any object exposing the
    :data:`COMPARE_PARAMS` attrs OR their store-Item aliases
    (``shutter_speed_s`` / ``aperture_f`` / ``focal_length_mm``); the
    Picker, Quick Sweep, and the cull tile readouts all converge here.
    Empty when ``exif`` is missing or has no usable values."""
    if exif is None:
        return ""
    return "  ·  ".join(
        t for t in (fmt_param(p, _resolve_value(exif, p))
                    for p in COMPARE_PARAMS) if t)


def caption_html(exif: Any, highlight: Optional[List[str]] = None) -> str:
    """One tile's exposure caption (rich text). ``highlight`` = the params to emphasise (the
    differing ones in a 2-photo compare), or ``None`` for a plain readout (no emphasis).
    Empty string when ``exif`` is missing or has no usable values.

    Reads through :func:`_resolve_value` so a partially-extracted live
    EXIF (model set, exposure zeroed) can be paired with a store-shaped
    fallback object — see :func:`exposure_for_chip` for the
    Picker's two-source merge. Direct callers (Quick Sweep
    ``SourceItem``, EXIF compare-grid) keep their canonical attribute
    names unchanged.
    """
    if exif is None:
        return ""
    parts: List[str] = []
    for p in COMPARE_PARAMS:
        text = fmt_param(p, _resolve_value(exif, p))
        if not text:
            continue
        if highlight and p in highlight:
            parts.append(f"<b style='color:{_HIGHLIGHT}'>{text}</b>")
        else:
            parts.append(text)
    return "  ·  ".join(parts)


def exposure_for_chip(primary: Any, fallback: Any = None) -> str:
    """Compose the chip's exposure segment, preferring populated
    values from ``primary`` and falling back to ``fallback`` per
    param (spec/96 §2 — the Picker's live EXIF can return model
    without exposure for some camera bodies; the gateway store Item
    always has the post-ingest values).

    Returns the rich-text exposure block (the same shape
    :func:`caption_html` produces). Empty when neither source
    carries any of the four params.
    """
    if primary is None and fallback is None:
        return ""
    parts: List[str] = []
    for p in COMPARE_PARAMS:
        value = _resolve_value(primary, p) if primary is not None else None
        if value is None and fallback is not None:
            value = _resolve_value(fallback, p)
        text = fmt_param(p, value)
        if text:
            parts.append(text)
    return "  ·  ".join(parts)


# --------------------------------------------------------------------------- #
# Source-chip helpers (spec/96 §2) — camera + file type + file size
#
# Pure logic: the call sites (Picker / Quick Sweep) compute the strings
# (camera name from the item, ``os.stat`` for size, suffix for type) and
# pass them in. Keeping this module **filesystem-free** is the contract
# the spec explicitly calls out.
# --------------------------------------------------------------------------- #


#: Suffixes the chip labels as RAW. Lowercase, includes the leading
#: dot. The set covers the common camera brands Mira sees today.
_RAW_SUFFIXES: frozenset = frozenset({
    ".cr2", ".cr3",                       # Canon
    ".nef", ".nrw",                       # Nikon
    ".arw", ".srf", ".sr2",               # Sony
    ".raf",                               # Fujifilm
    ".rw2",                               # Panasonic
    ".orf",                               # Olympus
    ".pef",                               # Pentax
    ".dng",                               # Adobe / generic / Leica / others
    ".raw",                               # generic
    ".rwl",                               # Leica
    ".srw",                               # Samsung
    ".x3f",                               # Sigma
})


def file_type_label(suffix: str) -> str:
    """Short label for a file extension (spec/96 §2).

    ``suffix`` is the path suffix including the dot (``Path.suffix``).
    Output rules:

    * RAW family (camera-specific raw formats) → ``"RAW"``.
    * ``.jpg`` / ``.jpeg`` → ``"JPEG"``.
    * ``.heic`` / ``.heif`` → ``"HEIF"``.
    * anything else → uppercased extension without the dot
      (``".tif"`` → ``"TIF"``).
    * empty / missing suffix → empty string.
    """
    if not suffix:
        return ""
    s = suffix.lower()
    if s in _RAW_SUFFIXES:
        return "RAW"
    if s in (".jpg", ".jpeg"):
        return "JPEG"
    if s in (".heic", ".heif"):
        return "HEIF"
    return s.lstrip(".").upper()


def file_size_text(byte_size: Any) -> str:
    """Human-readable file size for the chip (spec/96 §2).

    ``byte_size`` is the ``os.stat(path).st_size`` result; ``None`` /
    ``0`` / non-numeric collapse to an empty string so the chip drops
    the segment cleanly when the file is missing or the caller hasn't
    provided a size. Megabytes for files ≥ 1 MiB (``"{:.1f} MB"``);
    KB for smaller ones (``"{:d} KB"``).
    """
    try:
        n = int(byte_size)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    mb_threshold = 1024 * 1024
    if n >= mb_threshold:
        return f"{n / mb_threshold:.1f} MB"
    kb = max(1, (n + 1023) // 1024)
    return f"{kb} KB"


def source_chip_html(
    camera: Optional[str],
    type_label: str,
    size_text: str,
    exposure_html: str = "",
) -> str:
    """Compose the spec/96 §2 exposure chip body.

    Final shape:

    ::

        <Camera>  ·  1/250s · f/2.8 · ISO 400 · 85mm  ·  RAW · 24.3 MB

    Each segment is optional — empty strings drop cleanly so the chip
    stays tidy when EXIF is missing, the file isn't on disk, or the
    item carries no camera id. ``exposure_html`` is the existing
    :func:`caption_html` output (already rich-text) so the differing-
    param highlighting still composes when the caller supplies it.
    """
    cam = (camera or "").strip()
    typ = (type_label or "").strip()
    size = (size_text or "").strip()
    body = (exposure_html or "").strip()
    tail = "  ·  ".join(t for t in (typ, size) if t)
    return "  ·  ".join(s for s in (cam, body, tail) if s)


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
        if _norm(p, _resolve_value(exif_a, p))
        != _norm(p, _resolve_value(exif_b, p))
    ]
    if len(diffs) > _MAX_DIFFS:
        return None
    return diffs
