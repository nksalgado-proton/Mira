"""Country code from GPS coordinates — Slice TZ-2 of spec/45.

Reads ``assets/country_boundaries.json`` (built from Natural Earth 110m
admin-0; see the asset's commit message for the regeneration script) and
exposes a single pure function :func:`country_code_for` that returns the
ISO 3166-1 alpha-2 code containing a (lat, lon) point, or ``None`` for
open ocean / Antarctica gap / coordinates outside every shipped polygon.

The implementation is bounding-box pre-filter + ray-casting
point-in-polygon, both in pure Python (no shapely / no C dep). Lookups are
<5 ms on a typical centroid; the JSON parse happens once per process
(``lru_cache`` on the loader).

Used by the ingest engine to write ``trip_day.extras_json.country_code``
per day from :func:`core.phone_tz.phone_day_centroid` output. Offline-first
([[project_persona1_market_findings]] §"phone-first reality") — the asset
ships with the app; no network reverse-geocode at runtime.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


def _boundaries_path() -> Path:
    """Locate ``assets/country_boundaries.json`` (project root) from this
    module's depth."""
    return Path(__file__).resolve().parents[1] / "assets" / "country_boundaries.json"


@lru_cache(maxsize=1)
def _load_boundaries() -> Dict[str, dict]:
    """Read the JSON asset once; return ``{alpha2: {bbox, polygons}}``.
    Missing / unreadable file → empty dict (lookups all return ``None``;
    a warning is logged once)."""
    path = _boundaries_path()
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        log.exception(
            "country_lookup: could not load %s — country_code_for will "
            "always return None", path,
        )
        return {}


def _bbox_contains(bbox: List[float], lat: float, lon: float) -> bool:
    """``bbox`` is ``[min_lon, min_lat, max_lon, max_lat]`` (GeoJSON order)."""
    return bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]


def _ring_contains_point(
    ring: List[Tuple[float, float]], lat: float, lon: float,
) -> bool:
    """Ray-casting point-in-polygon. ``ring`` is a list of ``(lon, lat)``
    pairs (GeoJSON order). The ring's first and last points are typically
    equal in GeoJSON; the algorithm treats consecutive pairs as edges so
    that closure is implicit.

    Casts a horizontal ray east from the test point; counts edge crossings;
    odd → inside, even → outside. Edge cases at the test latitude itself
    are nudged via the strict-greater / strict-less inequalities to avoid
    double-counting vertices that sit exactly on the ray."""
    n = len(ring)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        # Does the edge straddle the test latitude? (Strict on one side,
        # non-strict on the other — the standard half-open trick to avoid
        # counting both vertices when the ray grazes a flat segment.)
        if (yi > lat) != (yj > lat):
            # X coord where the edge crosses the test latitude.
            slope = (xj - xi) * (lat - yi) / (yj - yi) if (yj - yi) else 0.0
            x_cross = xi + slope
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def _polygon_contains_point(
    polygon: List[List[Tuple[float, float]]], lat: float, lon: float,
) -> bool:
    """GeoJSON polygon = exterior ring + optional inner rings (holes).
    Point is inside iff it lies in the exterior ring AND not in any hole."""
    if not polygon:
        return False
    if not _ring_contains_point(polygon[0], lat, lon):
        return False
    for hole in polygon[1:]:
        if _ring_contains_point(hole, lat, lon):
            return False
    return True


def country_code_for(lat: float, lon: float) -> Optional[str]:
    """ISO 3166-1 alpha-2 code for the country containing ``(lat, lon)``,
    or ``None`` when the point falls outside every shipped polygon (open
    ocean, the Antarctic gap, the boundary asset's known unmapped pockets).

    Country boundaries don't overlap by design at the source dataset's
    scale; whichever candidate's polygon contains the point wins. When
    multiple candidates pass bbox pre-filter, both are tested — but in
    practice the first hit is the answer.
    """
    boundaries = _load_boundaries()
    if not boundaries:
        return None
    candidates = [
        (code, entry) for code, entry in boundaries.items()
        if _bbox_contains(entry.get("bbox") or [0, 0, 0, 0], lat, lon)
    ]
    for code, entry in candidates:
        for polygon in entry.get("polygons") or ():
            if _polygon_contains_point(polygon, lat, lon):
                return code
    return None


def is_data_available() -> bool:
    """``True`` iff the boundaries asset was loaded successfully — useful
    for callers that want to surface a "country auto-fill disabled because
    the data file is missing" hint rather than silently failing."""
    return bool(_load_boundaries())
