"""Parse a day-by-day trip itinerary into TripDay objects.

Supported formats::

    Dia 1 - Chegada em San José
    Dia 2 - Chegada em San José [TZ:-6]
    Dia 3 - La Fortuna [LOC:La Fortuna]
    Dia 4 - Drive to Monteverde [LOC:Monteverde] [TZ:-6]
    Day 1 - Arrival in San José
    1 - Chegada em San José
    1. Chegada em San José

    # Explicit dates are also accepted (and emitted by the skeleton
    # generator). When present they OVERRIDE the sequential
    # ``start_date + (day - 1)`` calculation, which lets a plan
    # have non-sequential day numbers (gap days for long flights,
    # multiple folders sharing one date, narrative numbers that
    # don't match calendar days):
    Dia 1 - Katmandu (26/10) [TZ:+5.75]
    Dia 2 - Lukla (29/10)              # 3-day gap from Dia 1
    Dia 7 - EBC Flight (03/11)
    Dia 8 - Lukla a Kathmandu (03/11)  # same date as Dia 7

Bracket tags
------------
Optional ``[TZ:...]`` and ``[LOC:...]`` tags can appear anywhere
inside the description, in any order. Each is parsed and stripped
from the description before storage.

* ``[TZ:-6]``   → UTC-6 (Costa Rica)
* ``[TZ:+1]``   → UTC+1 (Portugal)
* ``[TZ:-3.5]`` → UTC-3:30 (fractional offsets)
* ``[TZ:+5.75]`` → UTC+5:45 (Nepal)
* No ``[TZ:..]`` → inherit from previous day; first day defaults
  to ``home_timezone`` (or system local).

* ``[LOC:La Fortuna]`` → drives the Curate workflow's Medium /
  Short bucket subdivision. Anything in the brackets is stored
  verbatim (multi-word, accents, punctuation all OK except
  the closing ``]``). Days without ``[LOC:..]`` get ``location=None``.

Date hint
---------
``(DD/MM)``, ``(DD/MM/YYYY)``, ``(DD-MM)`` and ``(DD-MM-YYYY)`` are
parsed as explicit dates. When the year is omitted, it's inherited
from (in priority order):

1. ``start_date.year`` if the caller passed one.
2. The first dated entry that DID specify a year.
3. The current calendar year as a friendly default.

Dates without a year falling on the year boundary of a multi-year
trip is unsupported — use the full ``(DD/MM/YYYY)`` form when
ambiguity matters.
"""

import re
import time
from datetime import date, timedelta
from typing import Optional

from core.models import TripDay

# Day-line skeleton: "Dia N - rest of line". The body is captured
# loosely so we can pull bracket tags + date hint out in second pass.
_LINE_PATTERN = re.compile(
    r"^\s*(?:dia|day)?\s*(\d+)\s*[-–—.]\s*(.+?)\s*$",
    re.IGNORECASE,
)
_TZ_TAG = re.compile(r"\s*\[\s*TZ\s*:\s*([+-]?\d+(?:\.\d+)?)\s*\]\s*", re.IGNORECASE)
# LOC accepts anything except ']' so embedded spaces / accents work.
# Inner ``*?`` matches zero-or-more so ``[LOC:]`` parses to an empty
# location (which we then treat as None) instead of leaving the
# tag stuck in the description.
_LOC_TAG = re.compile(r"\s*\[\s*LOC\s*:\s*([^\]]*?)\s*\]\s*", re.IGNORECASE)
# Date hint: (DD/MM), (DD/MM/YYYY), (DD-MM), (DD-MM-YYYY).
# Accepts both / and - as separators and the year is optional. We
# match only at the END of the description body so it doesn't fight
# parenthetical text that happens to be in the description proper.
_DATE_HINT = re.compile(
    r"\s*\(\s*(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\s*\)\s*$"
)


def parse_trip_plan(
    text: str,
    start_date: Optional[date] = None,
    home_timezone: Optional[float] = None,
) -> list[TripDay]:
    """Parse multiline itinerary text into a list of TripDay.

    Each non-empty line is tested against the day pattern. Lines
    that don't match are silently skipped (so header lines like
    "Viagem: Costa Rica" don't blow up the parse). Comments
    starting with ``#`` are also stripped before parsing.

    ``[TZ:..]``, ``[LOC:..]``, and the trailing ``(DD/MM)`` date
    hint are extracted from the body. Tag order doesn't matter;
    each can be omitted independently.

    ``start_date`` resolution order, in priority:

    1. Caller-provided ``start_date``.
    2. The earliest explicit date found in the plan body.
    3. ``ValueError`` if neither is available.

    Per-day date resolution:

    1. The explicit ``(DD/MM[/YYYY])`` if present.
    2. ``start_date + (day_number - 1)`` as legacy fallback.

    Days are sorted by day_number. Timezone offsets are resolved
    via inheritance from the previous day, defaulting to
    ``home_timezone`` (or system local) for the first day.
    """
    parsed_lines: list[tuple[int, str, Optional[float], Optional[str], Optional[date]]] = []
    # First pass: extract everything except final dates; we may need
    # all explicit dates to determine start_date for the legacy fallback.
    for raw_line in text.splitlines():
        # Strip the ``#`` line-comment, but only when ``#`` is
        # OUTSIDE any ``[...]`` bracket — inside, ``#`` is the LOC
        # transport-mode marker (task #110, e.g. ``[LOC:A > B # bus]``).
        # Tracks bracket depth in a single forward pass; the first
        # ``#`` seen at depth=0 starts the comment.
        line = _strip_line_comment(raw_line).strip()
        if not line:
            continue
        m = _LINE_PATTERN.match(line)
        if not m:
            continue
        day_num = int(m.group(1))
        body = m.group(2)

        tz_offset, body = _extract_tz(body)
        location, body = _extract_location(body)
        explicit_date, body = _extract_date_hint(body)
        description = body.strip()

        parsed_lines.append((day_num, description, tz_offset, location, explicit_date))

    if not parsed_lines:
        return []

    # Year inference for explicit dates without a year. Priority:
    #  1. start_date.year if the caller provided one
    #  2. The first line whose ``(DD/MM/YYYY)`` actually included a year
    #  3. Current calendar year (the friendly default — a plan that
    #     types ``(12/04)`` and no start_date is overwhelmingly likely
    #     to mean "this year"; we'd rather pick today's year than
    #     keep the year=1 sentinel and watch QDateEdit clamp to 2000)
    inferred_year: Optional[int] = (
        start_date.year if start_date is not None else None
    )
    if inferred_year is None:
        # Look for the first explicit date that included a year. A
        # sentinel (year=1) means "no year was specified" — skip it.
        for _, _, _, _, ed in parsed_lines:
            if ed is not None and ed.year != 1:
                inferred_year = ed.year
                break
    if inferred_year is None:
        inferred_year = date.today().year

    # Second-pass resolve: dates without year get the inferred year.
    resolved: list[tuple[int, str, Optional[float], Optional[str], Optional[date]]] = []
    for day_num, description, tz_offset, location, explicit_date in parsed_lines:
        if explicit_date is not None and inferred_year is not None:
            # Re-stamp year on dates that came in as YearMarker(0).
            # Marker convention: year=1 in the parsed date means
            # "no year was specified, use inferred". We use 1 because
            # date(0,...) is invalid.
            if explicit_date.year == 1 and inferred_year:
                explicit_date = date(inferred_year, explicit_date.month, explicit_date.day)
        resolved.append((day_num, description, tz_offset, location, explicit_date))

    # Determine effective start_date for legacy fallback.
    effective_start = start_date
    if effective_start is None:
        explicit_dates = [d for _, _, _, _, d in resolved if d is not None]
        if not explicit_dates:
            # No start_date AND no explicit dates → can't compute the calendar.
            raise ValueError(
                "trip plan has no start_date and no explicit (DD/MM) dates; "
                "cannot infer the calendar"
            )
        effective_start = min(explicit_dates)

    days: list[TripDay] = []
    for day_num, description, tz_offset, location, explicit_date in resolved:
        if explicit_date is not None:
            day_date = explicit_date
        else:
            day_date = effective_start + timedelta(days=day_num - 1)
        days.append(TripDay(
            day_number=day_num, date=day_date,
            description=description, tz_offset=tz_offset,
            location=location,
        ))

    days.sort(key=lambda d: (d.day_number, d.date))
    _resolve_tz_inheritance(days, home_timezone)
    return days


def _strip_line_comment(line: str) -> str:
    """Return ``line`` with any ``#``-prefixed comment removed.

    Bracket-aware: a ``#`` inside ``[...]`` (the LOC transport-mode
    marker, task #110) is preserved; only a ``#`` at bracket
    depth=0 starts a comment. Cheap O(len) scan; no regex needed.

    Examples::

        "# whole line is a comment"          → ""
        "Dia 2 - x (29/10) # trailing"        → "Dia 2 - x (29/10) "
        "Dia 2 - x [LOC:A > B # bus]"         → "Dia 2 - x [LOC:A > B # bus]"
        "Dia 2 - x [LOC:A > B # bus] # tail"  → "Dia 2 - x [LOC:A > B # bus] "
    """
    depth = 0
    for i, ch in enumerate(line):
        if ch == "[":
            depth += 1
        elif ch == "]" and depth > 0:
            depth -= 1
        elif ch == "#" and depth == 0:
            return line[:i]
    return line


def _extract_tz(body: str) -> tuple[Optional[float], str]:
    """Pull the first ``[TZ:..]`` token out of ``body``. Returns the
    parsed offset (or None) and the remaining body with the tag
    spliced out. Subsequent ``[TZ:..]`` tokens (which we don't
    expect on a single line) are left untouched — only the first
    is honored."""
    m = _TZ_TAG.search(body)
    if not m:
        return None, body
    try:
        offset = float(m.group(1))
    except ValueError:
        return None, body
    return offset, body[:m.start()] + body[m.end():]


def _extract_location(body: str) -> tuple[Optional[str], str]:
    """Pull the first ``[LOC:..]`` token out of ``body``. Trim the
    captured value to drop incidental whitespace; downstream
    consumers expect the location string verbatim from then on
    (no further normalization)."""
    m = _LOC_TAG.search(body)
    if not m:
        return None, body
    location = m.group(1).strip()
    if not location:
        return None, body[:m.start()] + body[m.end():]
    return location, body[:m.start()] + body[m.end():]


def _extract_date_hint(body: str) -> tuple[Optional[date], str]:
    """Pull the trailing ``(DD/MM)`` / ``(DD/MM/YYYY)`` from ``body``.

    Returns ``(date, body_without_hint)`` or ``(None, body)`` if no
    hint is present. When no year is given the returned date carries
    a sentinel year of 1 — the caller substitutes the inferred year
    in a second pass once the calendar context is known.
    """
    m = _DATE_HINT.search(body)
    if not m:
        return None, body
    try:
        day = int(m.group(1))
        month = int(m.group(2))
        year_raw = m.group(3)
        if year_raw is None:
            year = 1  # sentinel; resolved by caller
        else:
            year = int(year_raw)
            if year < 100:
                year += 2000  # 2-digit year — assume 21st century
        # Validate the date constructs (catches things like 31/02).
        the_date = date(year if year >= 1 else 1, month, day)
    except (ValueError, TypeError):
        return None, body
    return the_date, body[:m.start()] + body[m.end():]


def _resolve_tz_inheritance(
    days: list[TripDay],
    home_timezone: Optional[float] = None,
) -> None:
    """Fill None tz_offset by inheriting from previous day.

    First day with no TZ uses home_timezone (or system local if not specified).
    """
    if home_timezone is None:
        home_timezone = -time.timezone / 3600  # system local
    last_tz = home_timezone
    for day in sorted(days, key=lambda d: d.day_number):
        if day.tz_offset is None:
            day.tz_offset = last_tz
        else:
            last_tz = day.tz_offset


def format_trip_plan(days: list[TripDay]) -> str:
    """Format TripDay list back to text (for display/editing).

    Always emits the explicit ``(DD/MM)`` date hint after the
    description so round-tripping a non-sequential plan (gap days,
    duplicate day numbers) is lossless. ``[LOC:..]`` is emitted
    when the day has a non-empty location; ``[TZ:..]`` only when
    the offset differs from the previous day. Tag order in the
    output: description ``(DD/MM/YYYY)`` ``[LOC:..]`` ``[TZ:..]``.

    B-024 (Nelson 2026-05-26): the date hint includes the **year**
    so a saved plan reopened in a later calendar year doesn't
    silently re-anchor against the current year. The parser
    accepts both ``(DD/MM)`` and ``(DD/MM/YYYY)`` (the year group
    in :data:`_DATE_RE` is optional), so plans written before this
    fix still re-import without rewriting.
    """
    lines = []
    last_tz = None
    for day in sorted(days, key=lambda d: (d.day_number, d.date)):
        date_hint = f"({day.date.strftime('%d/%m/%Y')})"
        parts = [f"Dia {day.day_number} - {day.description} {date_hint}".rstrip()]
        if day.location:
            parts.append(f"[LOC:{day.location}]")
        if day.tz_offset is not None and day.tz_offset != last_tz:
            tz_val = (
                int(day.tz_offset)
                if day.tz_offset == int(day.tz_offset)
                else day.tz_offset
            )
            parts.append(f"[TZ:{tz_val:+g}]")
            last_tz = day.tz_offset
        else:
            last_tz = day.tz_offset
        lines.append(" ".join(parts))
    return "\n".join(lines)
