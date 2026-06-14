"""Shared subtitle composer for per-event surfaces (spec/46 Slice 2+3).

The header subtitle on per-event pages — "Type · Subtype · N days · date-range
· UTC-3 · La Fortuna" — was written once on :class:`EventPlanPage` and is now
needed on :class:`ActivityDashboardPage` too. Lifted here verbatim from the
EventPlanPage module so both surfaces stay byte-identical (and EventPlanPage's
retirement in Slice 2+3 doesn't take the only copy with it).

Pure helpers — no Qt dependency beyond ``tr()`` for the day-count phrase.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from core.location_syntax import parse_location
from mira import event_classification
from mira.ui.i18n import tr


def compose_subtitle(
    event,
    event_type: str = event_classification.EVENT_TYPE_UNCLASSIFIED,
    event_subtype: Optional[str] = None,
) -> str:
    """Build the per-event header subtitle.

    Type · Subtype · trip duration + date range + dominant TZ + first location.
    Classification chips first so the user's eye lands on the typing they did
    via Edit info; everything else stays the order the legacy page used.

    ``event`` is duck-typed — only ``trip_days`` (list with ``.location``,
    ``.tz_offset``, ``.date``) and ``start_date`` are read. Works against the
    legacy ``core.models.Event`` (which is what the gateway adapter returns
    today) without coupling this module to it.
    """
    parts: list[str] = []
    if event_type and event_type != event_classification.EVENT_TYPE_UNCLASSIFIED:
        parts.append(event_classification.display_label_for_type(event_type))
    if event_subtype:
        parts.append(event_subtype)

    day_count = len(event.trip_days or [])
    if day_count:
        parts.append(tr("{n} day(s)").replace("{n}", str(day_count)))

    start = event.start_date
    end = last_day_date(event) or start
    if start and end:
        if start == end:
            parts.append(start.isoformat())
        else:
            parts.append(f"{start.isoformat()} → {end.isoformat()}")

    tz_str = format_tz(event)
    if tz_str:
        parts.append(tz_str)

    days = event.trip_days or []
    if days:
        first_loc = parse_location((days[0].location or "").strip()).display.strip()
        if first_loc:
            parts.append(first_loc)
    return "    ·    ".join(parts)


def last_day_date(event) -> Optional[date]:
    """The last dated day's date, or ``None`` when no day carries one."""
    days = event.trip_days or []
    if not days:
        return None
    dated = [d.date for d in days if d.date is not None]
    if not dated:
        return None
    return max(dated)


def dominant_tz_offset(event) -> Optional[float]:
    """Most-common ``tz_offset`` across the event's trip days as a raw float."""
    offsets = [
        d.tz_offset for d in (event.trip_days or [])
        if d.tz_offset is not None
    ]
    if not offsets:
        return None
    seen: dict[float, int] = {}
    for o in offsets:
        seen[o] = seen.get(o, 0) + 1
    return max(seen.items(), key=lambda kv: kv[1])[0]


def format_tz(event) -> str:
    """``UTC-3:00`` style string — most-common tz_offset across the plan."""
    main_tz = dominant_tz_offset(event)
    if main_tz is None:
        return ""
    sign = "−" if main_tz < 0 else "+"
    hh = int(abs(main_tz))
    mm = int(round((abs(main_tz) - hh) * 60))
    return f"UTC{sign}{hh}:{mm:02d}"
