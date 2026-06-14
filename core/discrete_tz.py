"""Discrete TZ offset vocabulary — spec/45 Slice TZ-3.

The DiscreteTzDialog and the gateway's :meth:`set_camera_day_tz` validation
both go through this module so the closed list of accepted offsets stays in
one place. Real-world TZs come in fixed increments (mostly whole hours,
occasionally 30/45/15 min); a continuous hours+minutes picker invited
wrong-minute precision errors that the new model eliminates by construction.

Pure-data + tiny helpers — no Qt, no store, no I/O. Tested in isolation.
"""
from __future__ import annotations

from typing import Optional, Tuple


# Standard TZ offsets in minutes east-of-UTC (negative = west). Covers every
# IANA-recognised offset in active use: UTC-12 through UTC+14 with the +30,
# +45, and +13 fractional / overflow cases that real zones (NPL, Chatham,
# Kiribati, etc.) sit at. Roughly 39 entries.
STANDARD_TZ_OFFSETS_MINUTES: Tuple[int, ...] = (
    -720, -660, -600, -570, -540, -480, -420, -360, -300, -240, -210, -180,
    -150, -120, -60, 0, 60, 120, 180, 210, 240, 270, 300, 330, 345, 360,
    390, 420, 480, 525, 540, 570, 600, 630, 660, 720, 765, 780, 840,
)


def is_valid_offset(minutes: int) -> bool:
    """``True`` iff ``minutes`` is in the closed enum :data:`STANDARD_TZ_OFFSETS_MINUTES`."""
    return minutes in STANDARD_TZ_OFFSETS_MINUTES


def format_offset(minutes: int) -> str:
    """``"UTC+02:00"`` form (zero-padded, no fractional hours). Used as the
    canonical short label across the dialog + log lines. Falls back to a
    bare numeric form for arbitrary values so this is never the function
    that raises on bad input."""
    sign = "-" if minutes < 0 else "+"
    abs_min = abs(int(minutes))
    hh, mm = divmod(abs_min, 60)
    return f"UTC{sign}{hh:02d}:{mm:02d}"


# Curated location hints per common offset — shown in the dropdown so the
# user can recognise their TZ by a landmark name without memorising the
# number. Best-effort; absent for offsets without an iconic location.
_OFFSET_LOCATION_HINTS: dict = {
    -720: "Baker Island",
    -660: "Samoa",
    -600: "Hawaii",
    -570: "Marquesas",
    -540: "Alaska",
    -480: "Los Angeles · Vancouver",
    -420: "Denver · Phoenix",
    -360: "Chicago · Mexico City",
    -300: "New York · Lima",
    -240: "Caracas · Halifax",
    -210: "St. John's",
    -180: "São Paulo · Buenos Aires",
    -150: "Atlantic mid",
    -120: "Mid-Atlantic",
    -60:  "Cape Verde",
    0:    "London · UTC",
    60:   "Rome · Paris · Berlin · Madrid",
    120:  "Cairo · Athens · Helsinki",
    180:  "Moscow · Riyadh · Nairobi",
    210:  "Tehran",
    240:  "Dubai · Baku",
    270:  "Kabul",
    300:  "Karachi · Tashkent",
    330:  "New Delhi · Colombo",
    345:  "Kathmandu",
    360:  "Dhaka · Almaty",
    390:  "Yangon",
    420:  "Bangkok · Jakarta · Ho Chi Minh City",
    480:  "Beijing · Singapore · Perth",
    525:  "Eucla",
    540:  "Tokyo · Seoul",
    570:  "Adelaide · Darwin",
    600:  "Sydney · Brisbane",
    630:  "Lord Howe",
    660:  "Solomon Is. · Magadan",
    720:  "Auckland · Fiji",
    765:  "Chatham",
    780:  "Tonga · Samoa (DST)",
    840:  "Kiribati",
}


def display_label_for_offset(
    minutes: int, *, with_locations: bool = True,
) -> str:
    """``"UTC+01:00 (Rome · Paris · Berlin · Madrid)"`` when a location hint
    exists; bare ``"UTC+01:00"`` otherwise or when ``with_locations=False``."""
    base = format_offset(minutes)
    if not with_locations:
        return base
    hint = _OFFSET_LOCATION_HINTS.get(minutes)
    return f"{base} ({hint})" if hint else base


def nearest_valid_offset(minutes: int) -> Optional[int]:
    """Snap an arbitrary minutes value to the closest entry in
    :data:`STANDARD_TZ_OFFSETS_MINUTES`. Returns ``None`` only when the
    enum is empty (defensive — never expected in real code).

    Used by the dialog when seeding from `camera.applied_offset_minutes`
    (a legacy continuous value) or from a phone's `OffsetTimeOriginal` that
    arrives at an unusual minute boundary."""
    if not STANDARD_TZ_OFFSETS_MINUTES:
        return None
    return min(STANDARD_TZ_OFFSETS_MINUTES, key=lambda v: abs(v - int(minutes)))
