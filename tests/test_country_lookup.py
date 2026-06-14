"""spec/45 Slice TZ-2 — GPS → ISO alpha-2 country code lookup.

Pinpoint tests on well-known landmarks so a future Natural Earth update
that shifts a border very slightly still passes (we test the country
contains the centroid of a major city, not the border itself).
"""
from __future__ import annotations

import pytest

from core.country_lookup import country_code_for, is_data_available


def test_data_is_available():
    assert is_data_available()


# ── Landmark cities ───────────────────────────────────────────────────────


@pytest.mark.parametrize("lat,lon,expected", [
    # Famous capital coordinates — each lands well inside its country's
    # 110m-scale polygon (we round the input to match the ~4-decimal asset
    # precision; that's still ~10 m resolution, well inside cities).
    (41.9000, 12.4833, "IT"),    # Rome
    (-23.5500, -46.6333, "BR"),   # São Paulo
    (40.7128, -74.0060, "US"),    # New York
    (35.6895, 139.6917, "JP"),    # Tokyo
    (-33.8688, 151.2093, "AU"),    # Sydney
    (51.5074, -0.1278, "GB"),     # London
    (52.5200, 13.4050, "DE"),      # Berlin
    (28.6139, 77.2090, "IN"),       # New Delhi
    (-1.2921, 36.8219, "KE"),       # Nairobi
])
def test_country_code_for_landmark_city(lat, lon, expected):
    assert country_code_for(lat, lon) == expected


# ── Open ocean / Antarctic gap ────────────────────────────────────────────


def test_country_code_for_mid_pacific_is_none():
    # Roughly 1000 km southeast of Hawaii — open ocean.
    assert country_code_for(15.0, -160.0) is None


def test_country_code_for_mid_atlantic_is_none():
    # Mid-Atlantic, equator-ish — open ocean.
    assert country_code_for(0.0, -30.0) is None


def test_country_code_for_south_pole_resolves_antarctica():
    # Natural Earth treats Antarctica as a country polygon (ISO 'AQ' is
    # the official code). Useful regression — confirms the dataset's
    # antarctic coverage is intact rather than relying on a None.
    assert country_code_for(-89.99, 0.0) == "AQ"


# ── Boundary edge cases ───────────────────────────────────────────────────


def test_country_code_for_known_island_country():
    # Reykjavik, Iceland — an island that needs its own polygon entry.
    assert country_code_for(64.1466, -21.9426) == "IS"


def test_country_code_for_madagascar_island():
    # Antananarivo, Madagascar — large island, separate polygon from
    # the African continent.
    assert country_code_for(-18.8792, 47.5079) == "MG"
