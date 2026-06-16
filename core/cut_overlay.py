"""Cut overlays — provenance text composition (spec/81 §3.1).

Overlays draw provenance text on each frame — **when** (date/time), **where**
(event/location), **how¹** (hardware: lens / camera / flash), **how²**
(settings: aperture / shutter / ISO, focal length). They **cost no budget** and
**change no membership** (spec/81 §3.1). This module owns the one shared
**field-composition** (which fields → what text) so the three consumers agree:

  * **in-app Play** draws them live on the frame (non-destructive),
  * **embedded** export writes only the *where* IPTC into the file (the
    technical fields already live in the JPEG's EXIF — PTE renders them),
  * **burn-in** export draws them into rendered copies.

Pure logic, no Qt (charter invariant 8). The selected fields are a subset of
``OVERLAY_FIELDS``; ``[]`` = overlays off. The IPTC field set Mira writes for
**where** is City / Sub-location / Country (spec/32 §2c — Mira's location model
is already IPTC-shaped).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: The four overlay field keys (spec/81 §3.1). A Cut selects a subset.
FIELD_WHEN = "when"
FIELD_WHERE = "where"
FIELD_HOW1 = "how1"      # hardware: lens / camera / flash
FIELD_HOW2 = "how2"      # settings: aperture / shutter / ISO, focal length
OVERLAY_FIELDS = (FIELD_WHEN, FIELD_WHERE, FIELD_HOW1, FIELD_HOW2)

#: The IPTC tags Mira writes for the *where* field (spec/32 §2c). Only *where*
#: needs writing at export — when / how¹ / how² are already in the camera EXIF
#: of every exported JPEG, which PTE renders for free.
IPTC_CITY = "IPTC:City"
IPTC_SUBLOCATION = "IPTC:Sub-location"
IPTC_COUNTRY = "IPTC:Country-PrimaryLocationName"


@dataclass(frozen=True)
class FrameProvenance:
    """The provenance facts one frame can carry — whatever Mira already holds
    for the source item (spec/81 §3.1: a multi-select over existing fields).
    Every field is optional; a missing one is simply omitted from the text."""

    when: Optional[str] = None            # human date/time
    event_name: Optional[str] = None
    city: Optional[str] = None
    sublocation: Optional[str] = None
    country: Optional[str] = None
    lens_model: Optional[str] = None
    camera: Optional[str] = None
    flash_fired: Optional[bool] = None
    aperture_f: Optional[float] = None
    shutter_speed_s: Optional[float] = None
    iso: Optional[int] = None
    focal_length_mm: Optional[float] = None


def _where_text(p: FrameProvenance) -> Optional[str]:
    parts = [x for x in (p.event_name, p.sublocation, p.city, p.country) if x]
    return ", ".join(parts) if parts else None


def _how1_text(p: FrameProvenance) -> Optional[str]:
    parts: List[str] = []
    if p.camera:
        parts.append(p.camera)
    if p.lens_model:
        parts.append(p.lens_model)
    if p.flash_fired is not None:
        parts.append("flash" if p.flash_fired else "no flash")
    return " · ".join(parts) if parts else None


def _shutter_text(shutter_s: float) -> str:
    """A shutter speed as a photographer reads it: ``1/500`` under a second,
    seconds otherwise."""
    if shutter_s <= 0:
        return ""
    if shutter_s < 1:
        return f"1/{round(1.0 / shutter_s)}"
    # Trim a trailing .0 so 2.0 → "2"
    return f"{shutter_s:g}s"


def _how2_text(p: FrameProvenance) -> Optional[str]:
    parts: List[str] = []
    if p.focal_length_mm:
        parts.append(f"{p.focal_length_mm:g}mm")
    if p.aperture_f:
        parts.append(f"f/{p.aperture_f:g}")
    if p.shutter_speed_s:
        st = _shutter_text(p.shutter_speed_s)
        if st:
            parts.append(st)
    if p.iso:
        parts.append(f"ISO {p.iso}")
    return " · ".join(parts) if parts else None


_FIELD_FORMATTERS = {
    FIELD_WHEN: lambda p: p.when or None,
    FIELD_WHERE: _where_text,
    FIELD_HOW1: _how1_text,
    FIELD_HOW2: _how2_text,
}


def compose_overlay_lines(
    fields: Sequence[str],
    provenance: FrameProvenance,
) -> List[str]:
    """The one shared formatter (spec/81 §3.1): selected ``fields`` → ordered
    text lines for one frame. A field with no data for this frame is omitted
    (no blank line). ``[]`` fields → ``[]``. Field order follows
    :data:`OVERLAY_FIELDS`, not the selection order, so frames read uniformly."""
    if not fields:
        return []
    chosen = set(fields)
    out: List[str] = []
    for key in OVERLAY_FIELDS:
        if key not in chosen:
            continue
        text = _FIELD_FORMATTERS[key](provenance)
        if text:
            out.append(text)
    return out


def where_iptc_tags(provenance: FrameProvenance) -> Dict[str, str]:
    """The IPTC tag→value map to embed *where* into an exported file (spec/81
    §3.1, embedded mode). Only non-empty fields are written; an empty map means
    the file needs no IPTC write (so it stays a pure hardlink — the technical
    EXIF is already in it). City / Sub-location / Country only."""
    tags: Dict[str, str] = {}
    if provenance.city:
        tags[IPTC_CITY] = provenance.city
    if provenance.sublocation:
        tags[IPTC_SUBLOCATION] = provenance.sublocation
    if provenance.country:
        tags[IPTC_COUNTRY] = provenance.country
    return tags


def needs_embedded_write(fields: Sequence[str], provenance: FrameProvenance) -> bool:
    """True iff embedded mode must write IPTC for this frame — i.e. *where* is
    selected AND there are where-tags to write. When False the file stays a
    pure hardlink (when / how¹ / how² ride the existing EXIF)."""
    if FIELD_WHERE not in set(fields):
        return False
    return bool(where_iptc_tags(provenance))


__all__ = [
    "FIELD_WHEN", "FIELD_WHERE", "FIELD_HOW1", "FIELD_HOW2", "OVERLAY_FIELDS",
    "IPTC_CITY", "IPTC_SUBLOCATION", "IPTC_COUNTRY",
    "FrameProvenance",
    "compose_overlay_lines", "where_iptc_tags", "needs_embedded_write",
]
