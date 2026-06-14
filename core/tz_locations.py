"""Shared named-location → UTC-offset table (P4, docs/14 §"TZ
named-location picker", docs/18 §"Phase 4").

The plan editor's per-day **TZ** cell and the culler's
**camera-clock** dialog both used to take the offset as a raw
number. That is error-prone: Nelson typed ``+5.45`` for Kathmandu,
but Nepal is UTC+5:45 = **+5.75** decimal hours (the 45-minute
zone is three-quarters of an hour, not 0.45). A shared picker of
well-known photography-trip locations — *Kathmandu (Nepal) —
UTC+05:45* → value ``5.75`` — removes that whole class of mistake.

This module is **pure** (no Qt): the location data and the
offset↔label formatting live here so the plan editor and the
camera-clock dialog stay consistent from one source of truth. The
value is always **float hours**, exactly what
``core.clock_calibration`` / ``core.fresh_source.build_tz_calibrations``
already consume — no IANA database, no event-schema change.

DST is deliberately ignored: the table lists each zone's standard
offset (a documented v1 simplification, matching the prototype and
docs/14 "predominant zone" stance). The rare exact-offset case is
covered by the picker's "Other offset…" escape hatch, which still
feeds a *constrained* numeric spinner — never free text.
"""

from __future__ import annotations

from typing import Optional

# (place label, UTC offset in float hours). Ordered by offset so a
# picker built from this reads west→east. Every inhabited offset
# (whole + the :30/:45 fractional zones) has a representative the
# user is likely to recognise; this is a deliberate superset of the
# "~20" estimate in docs/14 so *every* real value is a named pick
# (no raw fallback needed in practice). Proper-noun place names are
# the same in En/Pt — only the composed connective is translated.
TZ_LOCATIONS: list[tuple[str, float]] = [
    ("Pago Pago, Samoa", -11.0),
    ("Honolulu, Hawaii", -10.0),
    ("Anchorage, Alaska", -9.0),
    ("Los Angeles / Vancouver", -8.0),
    ("Denver / Phoenix", -7.0),
    ("Mexico City / San José (Costa Rica)", -6.0),
    ("New York / Lima / Bogotá", -5.0),
    ("Santiago / La Paz / Halifax", -4.0),
    ("St. John's, Newfoundland", -3.5),
    ("São Paulo / Buenos Aires", -3.0),
    ("Fernando de Noronha", -2.0),
    ("Azores / Cape Verde", -1.0),
    ("London / Lisbon (UTC)", 0.0),
    ("Paris / Berlin / Madrid / Rome", 1.0),
    ("Cairo / Athens / Johannesburg", 2.0),
    ("Moscow / Istanbul / Nairobi", 3.0),
    ("Tehran", 3.5),
    ("Dubai / Baku", 4.0),
    ("Kabul", 4.5),
    ("Karachi / Tashkent", 5.0),
    ("Delhi / Mumbai (India)", 5.5),
    ("Kathmandu (Nepal)", 5.75),
    ("Dhaka / Almaty", 6.0),
    ("Yangon", 6.5),
    ("Bangkok / Jakarta / Hanoi", 7.0),
    ("Singapore / Beijing / Hong Kong / Perth", 8.0),
    ("Eucla", 8.75),
    ("Tokyo / Seoul", 9.0),
    ("Adelaide / Darwin", 9.5),
    ("Sydney / Brisbane / Port Moresby", 10.0),
    ("Lord Howe Island", 10.5),
    ("Nouméa / Solomon Islands", 11.0),
    ("Auckland / Fiji", 12.0),
    ("Chatham Islands", 12.75),
    ("Apia / Tonga", 13.0),
    ("Kiritimati (Line Islands)", 14.0),
]


def format_utc_offset(hours: float) -> str:
    """``5.75`` → ``"UTC+05:45"``; ``-3.0`` → ``"UTC-03:00"``;
    ``0.0`` → ``"UTC+00:00"``. The single offset→text formatter
    (was ``camera_clock_dialog._fmt_offset``)."""
    sign = "-" if hours < 0 else "+"
    total_min = int(round(abs(hours) * 60))
    return f"UTC{sign}{total_min // 60:02d}:{total_min % 60:02d}"


def offset_label(place: str, hours: float) -> str:
    """The picker row text, e.g. ``"Kathmandu (Nepal) — UTC+05:45"``.
    Kept here (not in the widget) so plan editor and camera-clock
    dialog render identical labels."""
    return f"{place} — {format_utc_offset(hours)}"


def nearest_location(hours: float) -> tuple[str, float]:
    """The ``(place, offset)`` entry whose offset is closest to
    ``hours`` (exact wins; ties resolve to the westmost — list
    order). Used to pre-select the picker from a stored value."""
    best: Optional[tuple[str, float]] = None
    best_d: Optional[float] = None
    for place, off in TZ_LOCATIONS:
        d = abs(off - hours)
        if best_d is None or d < best_d:
            best, best_d = (place, off), d
    assert best is not None  # TZ_LOCATIONS is never empty
    return best


def has_exact(hours: float) -> bool:
    """True when some location maps to *exactly* ``hours`` — lets a
    caller decide whether a stored value needs a transient 'custom'
    row instead of an exact named pick."""
    return any(off == hours for _place, off in TZ_LOCATIONS)


def picker_label_for(hours: float) -> str:
    """Return the exact string the :class:`ui.base.tz_picker.TzPicker`
    would show for ``hours``.

    * Exact match → ``"<place> — UTC±HH:MM"`` (the named-location row).
    * No exact match → ``"Custom — UTC±HH:MM"`` (matches the
      picker's transient custom row label).

    Centralised here so any surface that wants to render a TZ "the
    way the picker shows it" — the Plan PhaseButton caption, future
    summary tooltips, etc. — gets the same string without
    duplicating the lookup.
    """
    for place, off in TZ_LOCATIONS:
        if off == hours:
            return offset_label(place, off)
    return f"Custom — {format_utc_offset(hours)}"
