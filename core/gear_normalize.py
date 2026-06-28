"""Gear-string normalization for the cross-event projection.

Phone cameras have no interchangeable lens, yet every phone writes a distinct
EXIF ``LensModel`` per sensor — an iPhone 11 alone emits five
("iPhone 11 back dual wide camera 4.25mm f/1.8", "… front camera 2.71mm f/2.2",
…). Grouping the cross-event Lens facet on the raw string therefore spawns a
wall of near-duplicate "lenses" that mean nothing to the photographer (who
thinks "iPhone 11", not the sensor's focal/aperture string).

This module collapses those phone-lens strings to the phone's camera name so
the Lens facet shows one row per phone. Real-camera lenses (LEICA, LUMIX,
OLYMPUS, Canon EF, …) pass through untouched.

Pure logic, no Qt / no store imports — applied at projection-write time
(``global_items_sync.project_event``) so the inventory, the resolver's filter
clause, and the overlay caption all read the same normalized value. The raw
EXIF string stays intact in each ``event.db`` ``item`` row; the projection is a
derived cache and is allowed to carry the curated label.
"""
from __future__ import annotations

from typing import Optional

# Markers that identify a phone / tablet camera from its make+model business
# key or its EXIF LensModel. Lower-cased substring match — conservative enough
# that no real interchangeable lens (LEICA / LUMIX / OLYMPUS / Canon EF / Nikon
# / Sigma / Tamron …) collides. Extend as new phone brands turn up.
_PHONE_MARKERS = (
    "iphone", "ipad", "pixel", "galaxy", "samsung sm-", "oneplus",
    "xiaomi", "redmi", "huawei", "honor", "oppo", "vivo", "realme",
    "motorola", "moto ", "nokia", "sony xperia",
)


def is_phone_lens(lens_model: Optional[str],
                  camera_id: Optional[str] = None) -> bool:
    """True when ``lens_model`` is a phone-camera EXIF string (and so should
    collapse to the phone name). Reads the lens string first; falls back to
    the ``camera_id`` so a phone shot with a blank lens still classifies."""
    for source in (lens_model, camera_id):
        if not source:
            continue
        low = source.lower()
        if any(marker in low for marker in _PHONE_MARKERS):
            return True
    return False


# Sensor-position words that begin the noise suffix of a phone LensModel
# ("iPhone 11 *back* dual wide camera …", "… *front* camera …"). The phone
# model is everything before the first such word.
_SENSOR_WORDS = (" back ", " front ", " back", " front")


def _phone_label(lens_model: str, camera_id: Optional[str]) -> str:
    """The phone model to group a phone shot under. Derived from the lens
    string's prefix ("iPhone 6s front camera 2.65mm f/2.2" → "iPhone 6s")
    because the ``camera_id`` can be missing / "_unknown" on a shot whose
    EXIF lost the body. Falls back to a usable ``camera_id``, then the raw
    string, so the result is never null/empty."""
    low = lens_model.lower()
    cut = min((i for i in (low.find(w) for w in _SENSOR_WORDS) if i > 0),
              default=-1)
    if cut > 0:
        return lens_model[:cut].strip()
    if camera_id and camera_id.lower() not in ("_unknown", "unknown", ""):
        return camera_id
    return lens_model


def normalize_lens(lens_model: Optional[str],
                   camera_id: Optional[str] = None) -> Optional[str]:
    """Return the lens label the cross-event Lens facet should group on.

    Phone-camera ``LensModel`` strings collapse to the phone model (e.g.
    "iPhone 11") so one phone is one Lens row, not five. Real lenses return
    unchanged. ``None`` / empty in → unchanged out (the facet's
    ``WHERE lens_model IS NOT NULL`` drops it either way)."""
    if not lens_model:
        return lens_model
    if is_phone_lens(lens_model, camera_id):
        return _phone_label(lens_model, camera_id)
    return lens_model


__all__ = ["is_phone_lens", "normalize_lens"]
