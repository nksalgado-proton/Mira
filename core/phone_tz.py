"""Phone-driven TZ + GPS aggregation — Slice TZ-1 of spec/45.

Three pure-logic helpers consumed by the ingest / pre-ingest pipeline:

* :func:`is_phone_source` — heuristic phone detector. A source is a "phone"
  iff a substantial fraction of its items carry ``OffsetTimeOriginal``
  (i.e. ``tz_offset_minutes is not None``). Modern phones write that field
  consistently; dedicated cameras virtually never do (per design memory
  [[project_phone_exif_tz_drives_correction]]).
* :func:`phone_day_tz` — per-day timezone offset derived by majority vote
  over the phone items captured that day. Returns ``None`` for days with
  no phone data (the manual calibration path picks those up).
* :func:`phone_day_centroid` — per-day GPS centroid for use by Slice TZ-2's
  country lookup. Mean of every phone GPS point that day.

Qt-free, store-free; tested in isolation. The capture flow drives these
functions from the freshly-scanned ``SourceItem`` list before the
pre-ingest dialog opens.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Sequence, Tuple

from core.fresh_source import SourceItem


# Tunable thresholds — phones write OffsetTimeOriginal on virtually every
# photo when GPS+location services are on, and even with location off the
# offset usually still rides on the system clock. Cameras almost never
# write it. A 50% fill rate is plenty of signal.
_PHONE_OFFSET_FILL_THRESHOLD = 0.5


def is_phone_source(items: Sequence[SourceItem]) -> bool:
    """``True`` iff at least :data:`_PHONE_OFFSET_FILL_THRESHOLD` of the items
    carry a non-``None`` ``tz_offset_minutes``.

    Empty input → ``False`` (no signal). Single-item input → ``True`` when
    that item has an offset (one offset is one phone — defensive default,
    matches user expectation when a single phone photo lands on a card).
    """
    if not items:
        return False
    with_offset = sum(1 for it in items if it.tz_offset_minutes is not None)
    return (with_offset / len(items)) >= _PHONE_OFFSET_FILL_THRESHOLD


def _group_by_day_for_phone(
    items: Sequence[SourceItem],
    day_for: Mapping[object, Optional[int]],
) -> Dict[int, list]:
    """``{day_number: [SourceItem, …]}`` for every phone-shape item.
    ``day_for`` maps either a ``Path`` or a ``SourceItem`` key — the caller
    supplies whatever it has — to the day_number that item's timestamp
    landed on. Items the caller couldn't assign (``None``) are skipped."""
    out: Dict[int, list] = {}
    for it in items:
        if it.tz_offset_minutes is None:
            continue
        # Try both key shapes (path and item itself) so callers can use
        # whichever is convenient.
        day = day_for.get(it.path)
        if day is None:
            day = day_for.get(it)
        if day is None:
            continue
        out.setdefault(day, []).append(it)
    return out


def phone_day_tz(
    items: Sequence[SourceItem],
    day_for: Mapping[object, Optional[int]],
) -> Dict[int, int]:
    """``{day_number: tz_offset_minutes}`` for every day where the phone
    captured at least one item. Majority vote when multiple items disagree
    (timezone-cross day; we keep the dominant offset). Skip days with no
    phone items entirely so the caller can fall back to the manual flow."""
    grouped = _group_by_day_for_phone(items, day_for)
    out: Dict[int, int] = {}
    for day, day_items in grouped.items():
        offsets = Counter(it.tz_offset_minutes for it in day_items)
        # Counter.most_common preserves insertion order on ties; that's
        # fine — tie-break stability isn't load-bearing (a tie means the
        # phone genuinely sat on two TZs that day in equal counts).
        winner, _votes = offsets.most_common(1)[0]
        out[day] = winner
    return out


def phone_day_centroid(
    items: Sequence[SourceItem],
    day_for: Mapping[object, Optional[int]],
) -> Dict[int, Tuple[float, float]]:
    """``{day_number: (lat, lon)}`` — mean of every phone GPS point that
    day. Days where the phone captured only TZ but no GPS (location off,
    indoor photos) are absent from the output. Slice TZ-2 reads this map
    + the country-boundaries asset to write per-day country codes."""
    grouped = _group_by_day_for_phone(items, day_for)
    out: Dict[int, Tuple[float, float]] = {}
    for day, day_items in grouped.items():
        coords = [
            (it.gps_lat, it.gps_lon) for it in day_items
            if it.gps_lat is not None and it.gps_lon is not None
        ]
        if not coords:
            continue
        # Plain arithmetic mean. For most trip-day spans (a few km), this is
        # well within the precision a country-boundary lookup needs. The
        # 180° meridian wraparound case (rare — Fiji, Kiribati) would need
        # a proper spherical mean; deferred until someone reports it.
        n = len(coords)
        lat_mean = sum(c[0] for c in coords) / n
        lon_mean = sum(c[1] for c in coords) / n
        out[day] = (lat_mean, lon_mean)
    return out


@dataclass(frozen=True)
class PhoneDaySummary:
    """Compact roll-up for a single day's phone-derived signals.

    Used by the capture flow to drive Slice TZ-3's DiscreteTzDialog and
    Slice TZ-2's country derivation in one read. ``tz_minutes`` and
    ``centroid`` are ``None`` independently — a day with phone items but
    GPS off has TZ but no centroid; an indoor selfie day with location
    off has TZ but no centroid; an outdoor walk has both.
    """
    day_number: int
    tz_minutes: Optional[int]
    centroid: Optional[Tuple[float, float]]
    item_count: int


def phone_day_arrival_gps(
    items: Sequence[SourceItem],
    day_for: Mapping[object, Optional[int]],
) -> Dict[int, Tuple[float, float]]:
    """``{day_number: (lat, lon)}`` — the LAST GPS point of the day, by
    item timestamp. Distinct from :func:`phone_day_centroid`: for a travel
    day (e.g. SP → NY), the centroid lands mid-Atlantic, while this picks
    the destination — the country the user *arrived in*. Used by spec/47's
    per-day country auto-fill (Nelson 2026-06-06: "use the country he has
    arrived in").

    Items without GPS are skipped; days with no GPS at all are absent. Items
    without timestamps (``it.timestamp is None``) are also skipped — without
    a timestamp we can't order arrival.
    """
    grouped = _group_by_day_for_phone(items, day_for)
    out: Dict[int, Tuple[float, float]] = {}
    for day, day_items in grouped.items():
        with_gps = [
            it for it in day_items
            if it.gps_lat is not None and it.gps_lon is not None
            and it.timestamp is not None
        ]
        if not with_gps:
            continue
        latest = max(with_gps, key=lambda it: it.timestamp)
        out[day] = (latest.gps_lat, latest.gps_lon)
    return out


def phone_day_summaries(
    items: Sequence[SourceItem],
    day_for: Mapping[object, Optional[int]],
) -> Dict[int, PhoneDaySummary]:
    """``{day_number: PhoneDaySummary}`` — the convenience roll-up the
    capture flow consumes. Combines :func:`phone_day_tz` and
    :func:`phone_day_centroid` over one walk of the phone items."""
    grouped = _group_by_day_for_phone(items, day_for)
    out: Dict[int, PhoneDaySummary] = {}
    for day, day_items in grouped.items():
        tz_winner = Counter(
            it.tz_offset_minutes for it in day_items
        ).most_common(1)[0][0]
        coords = [
            (it.gps_lat, it.gps_lon) for it in day_items
            if it.gps_lat is not None and it.gps_lon is not None
        ]
        centroid: Optional[Tuple[float, float]] = None
        if coords:
            n = len(coords)
            centroid = (
                sum(c[0] for c in coords) / n,
                sum(c[1] for c in coords) / n,
            )
        out[day] = PhoneDaySummary(
            day_number=day,
            tz_minutes=tz_winner,
            centroid=centroid,
            item_count=len(day_items),
        )
    return out
