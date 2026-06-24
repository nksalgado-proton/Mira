"""spec/134 — item → :class:`~core.cut_overlay.FrameProvenance` resolver
for the Picker / Editor photo-viewer overlay.

Reuses the cut overlay vocabulary (:data:`OVERLAY_FIELDS` and
:func:`compose_overlay_lines`) so the viewer pill and the cut overlays
speak one language. The resolver builds a ``FrameProvenance`` from a
**live store** ``Item`` (the post-ingest source row — not a lineage
row) plus its day's where-context (city / country from
``TripDay.location`` + ``extras_json.country``).

Pure logic, no Qt, no SQLite — accepts plain dataclasses / dicts so the
test suite can exercise the resolver without spinning up a real
``EventGateway``. The gateway adds a thin wrapper
(:meth:`EventGateway.item_provenance`) that does the day / camera
lookups and hands us the resolved inputs.

``when`` follows the cut-overlay rule (event_gateway.py:1693): the
TZ / clock-CORRECTED capture time is preferred, falling back to the raw
EXIF only when no correction has been applied. The whole correction
pipeline exists so the *corrected* time is the one shown; raw is the
last-ditch fallback when corrected is absent.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from core.cut_overlay import FrameProvenance


def resolve_when(item: Any) -> Optional[str]:
    """Pick the *when* string for a viewer overlay. Corrected first,
    raw only when corrected is missing. ``None`` when neither is set —
    the composer omits the field rather than printing a placeholder."""
    corrected = getattr(item, "capture_time_corrected", None)
    if corrected:
        return str(corrected)
    raw = getattr(item, "capture_time_raw", None)
    if raw:
        return str(raw)
    return None


def resolve_where(day: Any) -> tuple[Optional[str], Optional[str]]:
    """Pick (city, country) for a viewer overlay from a ``TripDay``.

    ``city`` reads ``TripDay.location`` (free-text legacy field — what
    the Days editor wrote). ``country`` reads ``TripDay.extras_json``'s
    ``country`` key (the structured machine-readable side). Either may
    be ``None`` independently. Tolerant of a missing/None ``day``."""
    if day is None:
        return None, None
    city = getattr(day, "location", None) or None
    country: Optional[str] = None
    extras = getattr(day, "extras_json", None) or "{}"
    try:
        country = json.loads(extras).get("country") or None
    except (ValueError, TypeError):
        country = None
    return city, country


def item_to_frame_provenance(
    item: Any,
    *,
    camera_label: Optional[str] = None,
    day: Any = None,
) -> FrameProvenance:
    """Build a :class:`FrameProvenance` from a live store ``Item``.

    The caller resolves ``camera_label`` (typically
    ``Camera.camera_id`` — the ``'Make+Model'`` business key — looked
    up by ``item.camera_id``) and ``day`` (the ``TripDay`` for
    ``item.day_number``) so this helper stays pure logic. Missing
    fields are dropped; the cut overlay composer omits them
    gracefully.

    ``flash_fired`` keeps the tri-state contract — ``None`` ≠
    ``False`` (the composer skips the field entirely when None,
    prints ``"no flash"`` when False)."""
    if item is None:
        return FrameProvenance()
    city, country = resolve_where(day)
    flash = getattr(item, "flash_fired", None)
    return FrameProvenance(
        when=resolve_when(item),
        city=city,
        country=country,
        camera=(str(camera_label) if camera_label else None),
        lens_model=getattr(item, "lens_model", None),
        flash_fired=(None if flash is None else bool(flash)),
        aperture_f=getattr(item, "aperture_f", None),
        shutter_speed_s=getattr(item, "shutter_speed_s", None),
        iso=getattr(item, "iso", None),
        focal_length_mm=getattr(item, "focal_length_mm", None),
    )


__all__ = [
    "FrameProvenance",
    "item_to_frame_provenance",
    "resolve_when",
    "resolve_where",
]
