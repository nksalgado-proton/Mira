"""Offline reverse-geocoding for GPS coords → nearest city + admin region.

Companion to :mod:`core.country_lookup` (which only returns the country code).
Used by the past-photos and capture flows to pre-fill the per-day **description**
field with a human-readable place name (Nelson 2026-06-06: "while you fill
country, also put more detailed info like Salta in the description").

Backed by the ``reverse_geocoder`` package, which bundles GeoNames cities1000.txt
(~150k cities, pop ≥ 1000) and does a k-d tree nearest-neighbour search. Fully
offline; no network calls (CLAUDE.md invariant #3).
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

log = logging.getLogger(__name__)

_RG = None  # lazy-loaded reverse_geocoder module


def _lazy_load():
    """Import + warm reverse_geocoder on first use.

    First call costs ~1-3 seconds (it deserialises the k-d tree). Subsequent
    calls are sub-millisecond. We delay the import so the rebuild app's startup
    isn't gated on the library.
    """
    global _RG
    if _RG is None:
        try:
            import reverse_geocoder as rg
            _RG = rg
        except ImportError:
            log.warning(
                "reverse_geocoder not installed — place auto-fill disabled. "
                "pip install reverse_geocoder")
            _RG = False
    return _RG


def place_for(lat: float, lon: float) -> Optional[Tuple[str, str, str]]:
    """Return ``(city_name, admin1, country_code)`` for the nearest city, or
    ``None`` if the lookup can't run (library missing) or the coords are
    nonsensical.

    ``admin1`` is the first-level administrative region (state / province /
    region — e.g. ``"Salta"`` for Argentina, ``"São Paulo"`` for Brazil).
    """
    if lat is None or lon is None:
        return None
    rg = _lazy_load()
    if not rg:
        return None
    try:
        results = rg.search([(float(lat), float(lon))], mode=1, verbose=False)
    except Exception:  # noqa: BLE001
        log.exception("place_for(%s, %s) failed", lat, lon)
        return None
    if not results:
        return None
    r = results[0]
    return (r.get("name", ""), r.get("admin1", ""), r.get("cc", ""))


def describe(lat: float, lon: float) -> Optional[str]:
    """Convenience: human-friendly one-line description of where the coords are.

    Returns ``"<city>, <admin1>"`` when both are non-empty, otherwise whichever
    one is non-empty, otherwise ``None``. Suitable for pre-filling a TripDay's
    free-text description field (the user can edit it after).
    """
    place = place_for(lat, lon)
    if place is None:
        return None
    city, admin1, _cc = place
    city = (city or "").strip()
    admin1 = (admin1 or "").strip()
    if city and admin1 and city.lower() != admin1.lower():
        return f"{city}, {admin1}"
    return city or admin1 or None
