"""Per-day autofill engine — spec/52 §3.

Two autofill sources, each producing the four fields the Plan dialog shows
per day (country / TZ / location / description):

* **Phone-EXIF autofill (§3.1)** — when a day has phone photos in the scan,
  each field is pulled from the **first** phone photo that carries that
  signal. Phones write ``OffsetTimeOriginal`` consistently + GPS when the
  user hasn't disabled it; the autofill engine surfaces what's there and
  silently skips what isn't (a phone in airplane mode won't yield GPS, but
  its TZ + Make/Model still classify it as a phone, so the TZ autofill
  still fires).

* **Subdir-name autofill (§3.2)** — when ALL of a day's photos come from a
  single immediate subdirectory under the source root (common in past-
  event ingest where the user organized the dir per day — "Day 1 -
  Lisbon", "2024-07-12 Sintra hike", etc.), the subdir name autofills the
  **description** field for that day. Strict detection: any cross-subdir
  spread skips the autofill.

Conflict resolution (§3.3):

* **Subdir name beats phone-derived default description.** The subdir is a
  deliberate user signal (he organized the dir for a reason); phone-
  location is automation. Both stay editable.
* **Phone EXIF location beats absence-of-data.** No conflict because GPS
  reverse-geocode is the only source for the location string.

Pure logic, no Qt. Input is a flat list of :class:`PhotoExif` (the existing
exiftool batch output) for one day's photos; output is a
:class:`DayAutofill` carrying the autofilled values + a per-field
provenance flag so the Plan dialog can render the "from phone" badge.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, List, Optional, Sequence, Tuple

from core import phone_detector
from core.path_builder import RESERVED_DIR_NAMES

if TYPE_CHECKING:
    from core.exif_reader import PhotoExif

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Output shape
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DayAutofill:
    """The autofilled per-day fields. Each value is ``None`` when no source
    was available (no phone photo for that field; no consistent subdir for
    description). The ``*_source`` fields carry provenance for the UI badge
    rendering ("from phone EXIF" / "from subdir name")."""

    country_code: Optional[str] = None        # ISO 3166-1 alpha-2
    tz_minutes: Optional[int] = None           # minutes east-of-UTC
    location: Optional[str] = None             # human-readable, e.g. "Sintra, Lisbon"
    description: Optional[str] = None          # initially = location; subdir overrides

    country_source: str = ""                   # "phone_exif" | ""
    tz_source: str = ""                        # "phone_exif" | ""
    location_source: str = ""                  # "phone_exif" | ""
    description_source: str = ""               # "phone_exif" | "subdir" | ""

    def is_empty(self) -> bool:
        """True when nothing was autofilled (no phone photos AND no
        consistent subdir). Callers use this to decide whether to bother
        rendering the autofill summary in the plan dialog."""
        return (
            self.country_code is None
            and self.tz_minutes is None
            and self.location is None
            and self.description is None
        )


# --------------------------------------------------------------------------- #
# Phone identification — PhotoExif → bool
# --------------------------------------------------------------------------- #


def _make_of(photo: "PhotoExif") -> Optional[str]:
    """Pull the EXIF ``Make`` tag out of a PhotoExif. The PhotoExif dataclass
    exposes ``model`` directly but stashes ``Make`` in the catchall ``raw``
    dict — phone-detection needs both."""
    raw = getattr(photo, "raw", None) or {}
    val = raw.get("Make")
    if val is None:
        return None
    val = str(val).strip()
    return val or None


def is_phone_photo(photo: "PhotoExif") -> bool:
    """Phone classification for one already-read photo. Wraps
    :func:`core.phone_detector.is_phone` so the autofill engine has a single
    seam — tests can monkeypatch this without going through the full
    Make/Model API."""
    return phone_detector.is_phone(_make_of(photo), photo.model)


# --------------------------------------------------------------------------- #
# Phone-EXIF autofill (§3.1)
# --------------------------------------------------------------------------- #


def _first_phone_gps(
    photos: Sequence["PhotoExif"],
) -> Optional[Tuple[float, float]]:
    """The first phone photo's GPS coords, or ``None``. The "first phone with
    a signal" rule is per-field (spec/52 §3.1): a phone in airplane mode has
    TZ but no GPS, so we walk independently for each signal."""
    for p in photos:
        if not is_phone_photo(p):
            continue
        if p.gps_lat is None or p.gps_lon is None:
            continue
        return float(p.gps_lat), float(p.gps_lon)
    return None


def _first_phone_tz(photos: Sequence["PhotoExif"]) -> Optional[int]:
    """The first phone photo's TZ offset in minutes, or ``None``."""
    for p in photos:
        if not is_phone_photo(p):
            continue
        if p.tz_offset_minutes is None:
            continue
        return int(p.tz_offset_minutes)
    return None


def autofill_phone_for_day(photos: Sequence["PhotoExif"]) -> DayAutofill:
    """Pull country / TZ / location / description from the day's phone photos.

    Returns an empty :class:`DayAutofill` when no phone photos are present
    OR when phone photos have none of the relevant EXIF signals (a fully-
    stripped WhatsApp share, per spec/52 §9). Reverse-geocode failures
    (lat/lon falls outside any country polygon — open ocean, the Antarctic
    gap) are treated as "no country" — the location string may still fire if
    the city-level lookup succeeds.
    """
    if not photos:
        return DayAutofill()

    # Walk each signal independently — different phone photos may have
    # different gaps.
    tz = _first_phone_tz(photos)
    gps = _first_phone_gps(photos)

    country: Optional[str] = None
    location: Optional[str] = None
    if gps is not None:
        # Local imports — these geocode helpers carry a heavy first-call
        # cost (loads boundary data / a k-d tree) that we want to defer
        # until somebody actually needs them.
        from core.country_lookup import country_code_for
        from core.place_lookup import describe

        try:
            country = country_code_for(gps[0], gps[1])
        except Exception:                                # noqa: BLE001
            log.exception("country_code_for(%s, %s) failed", *gps)
        try:
            location = describe(gps[0], gps[1])
        except Exception:                                # noqa: BLE001
            log.exception("place_lookup.describe(%s, %s) failed", *gps)

    # Description starts equal to location (spec/52 §3.1 — "initially
    # populated equal to location text, user-editable"). The subdir-name
    # autofill overrides this if it fires for the same day.
    description = location

    return DayAutofill(
        country_code=country,
        tz_minutes=tz,
        location=location,
        description=description,
        country_source="phone_exif" if country is not None else "",
        tz_source="phone_exif" if tz is not None else "",
        location_source="phone_exif" if location is not None else "",
        description_source="phone_exif" if description is not None else "",
    )


# --------------------------------------------------------------------------- #
# Subdir-name autofill (§3.2)
# --------------------------------------------------------------------------- #


def common_immediate_subdir(
    paths: Iterable[Path],
    source_root: Path,
) -> Optional[str]:
    """The single immediate subdirectory under ``source_root`` shared by
    EVERY path in ``paths``, or ``None``.

    Strict detection per spec/52 §3.2 — any mixed-subdir spread (a day's
    photos split across two folders, or one stray DCIM file in a different
    subdir) yields ``None``. Photos directly in ``source_root`` (no
    intervening folder) also yield ``None`` — there's no per-day subdir to
    use.

    Verbatim — no date-prefix stripping (the user can clean up in two
    seconds; heuristic stripping risks chewing useful tokens, §3.3).
    """
    source_root = Path(source_root).resolve()
    seen: set[str] = set()
    has_any = False
    for raw in paths:
        has_any = True
        path = Path(raw).resolve()
        try:
            rel = path.relative_to(source_root)
        except ValueError:
            return None                                  # outside the source
        parts = rel.parts
        if len(parts) < 2:
            return None                                  # photo in root, no subdir
        seen.add(parts[0])
        if len(seen) > 1:
            return None                                  # early exit on mixed
    if not has_any:
        return None
    name = seen.pop()
    # Never surface Mira's own internal bucket folders as a day
    # description. An import whose source already carries a captured tree
    # (``_cameras`` / ``_phones`` / ``_other`` / ``_no_timestamp``, the
    # legacy ``_outros`` / ``_celulares``, or any phase folder like
    # ``Original Media``) would otherwise leak a token such as "_outros"
    # straight into the day title. A real user subdir never starts with
    # "_", so this is a safe guard; the day falls back to "Day N".
    if name.startswith("_") or name in RESERVED_DIR_NAMES:
        return None
    return name


def autofill_description_from_subdir(
    photos: Sequence["PhotoExif"],
    source_root: Path,
) -> Optional[str]:
    """The subdir-derived description for one day's photos, or ``None``.

    Thin wrapper over :func:`common_immediate_subdir` that pulls the path
    field out of :class:`PhotoExif`. Kept as a separate seam so the UI
    layer can call it without importing the path helper directly."""
    paths = [p.path for p in photos]
    return common_immediate_subdir(paths, source_root)


# --------------------------------------------------------------------------- #
# Combined — the single entry point the Plan dialog uses
# --------------------------------------------------------------------------- #


def autofill_for_day(
    photos: Sequence["PhotoExif"],
    *,
    source_root: Optional[Path] = None,
    home_country: Optional[str] = None,
    home_tz_minutes: Optional[int] = None,
) -> DayAutofill:
    """The combined autofill — phone-EXIF for country / TZ / location, then
    the subdir-name override for description per the spec/52 §3.3 conflict
    rule ("subdir name beats phone-derived default description").

    ``source_root=None`` skips the subdir-name pass — used when the source
    is an SD card (cards rarely have the organized-by-day layout) or when
    the caller has no source root (synthesised tests, etc.).

    ``home_country`` / ``home_tz_minutes`` are the user's home settings
    (Nelson 2026-06-08). When the day's phone autofill leaves a field
    blank (no phone photos, or phone in airplane mode with GPS off), the
    home value fills in and the corresponding source flag becomes
    ``"home_default"`` so the UI can flag the pre-fill for the user. Pass
    ``None`` to skip the fallback (the legacy behaviour).
    """
    base = autofill_phone_for_day(photos)

    # Apply home-default fallbacks BEFORE the subdir override (subdir is
    # description-only, doesn't interact with country / TZ).
    country_code = base.country_code
    country_source = base.country_source
    if country_code is None and home_country:
        country_code = home_country
        country_source = "home_default"
    tz_minutes = base.tz_minutes
    tz_source = base.tz_source
    if tz_minutes is None and home_tz_minutes is not None:
        tz_minutes = home_tz_minutes
        tz_source = "home_default"

    enriched = DayAutofill(
        country_code=country_code,
        tz_minutes=tz_minutes,
        location=base.location,
        description=base.description,
        country_source=country_source,
        tz_source=tz_source,
        location_source=base.location_source,
        description_source=base.description_source,
    )

    if source_root is None:
        return enriched

    subdir = autofill_description_from_subdir(photos, source_root)
    if subdir is None:
        return enriched

    # Subdir overrides description; everything else stays from enriched.
    return DayAutofill(
        country_code=enriched.country_code,
        tz_minutes=enriched.tz_minutes,
        location=enriched.location,
        description=subdir,
        country_source=enriched.country_source,
        tz_source=enriched.tz_source,
        location_source=enriched.location_source,
        description_source="subdir",
    )
