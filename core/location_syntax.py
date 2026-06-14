"""Location-field syntax — task #110 (Nelson 2026-05-23).

The plan editor's *Location* column is free text — by design — but
Nelson asked for a light syntax so a single string can carry the
three things a curate map slide needs to know:

  1. **Where the day starts** (origin)
  2. **Where the day ends**   (destination, if it's a travel day)
  3. **How the user moved**   (transport mode, when relevant)

Conventions:

  * ``>``  separates origin from destination. Presence of ``>``
    marks the day as a *travel day*.
  * ``#``  prefixes the transport mode (free text after the ``#``).
    Can appear on either a stay day (``Kathmandu # walking``) or a
    travel day (``Kathmandu > Pokhara # bus``).
  * Whitespace around the separators is optional and stripped.

Examples::

    "San José"                  → stay,  origin=San José
    "San José > La Fortuna"     → travel, origin→dest
    "Kathmandu # plane"         → stay,  transport=plane (rare)
    "Kathmandu > Pokhara # bus" → travel + transport

Pure logic — no Qt, no I/O. The plan-editor UI, the curate map-slide
engine, and the EventCard travel-day badge all read this module.

Backwards compatible: a plain location string without ``>`` or ``#``
parses to ``origin=text, destination=None, transport=None``, which is
the legacy meaning. No migration needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


_TRAVEL_SEP = ">"
_TRANSPORT_SEP = "#"


@dataclass(frozen=True)
class LocationParts:
    """One day's parsed location.

    ``origin``       — the first segment (never empty when the input
                       had any non-separator text; empty string only
                       for fully-empty input).
    ``destination``  — the segment after ``>`` when present; ``None``
                       on a stay day.
    ``transport``    — the segment after ``#`` when present; ``None``
                       when not specified.
    """

    origin: str
    destination: Optional[str] = None
    transport: Optional[str] = None

    @property
    def is_travel(self) -> bool:
        """True when the day moves between locations (``>`` was used).
        ``Curate`` reads this to surface a travel-day map slide."""
        return self.destination is not None

    @property
    def display(self) -> str:
        """Human-readable single-line render. Uses ``→`` instead of
        the raw ``>`` for readability, and parens for the transport.
        Empty string on fully-empty input."""
        if not self.origin and not self.destination:
            return ""
        if self.destination:
            base = f"{self.origin} → {self.destination}"
        else:
            base = self.origin
        if self.transport:
            return f"{base} ({self.transport})"
        return base

    @property
    def folder_safe(self) -> str:
        """A version safe for inclusion in a folder name (no ``>``
        or ``#`` — Windows tolerates them but they read as URL/CLI
        artifacts in Explorer). The travel arrow becomes a plain
        ``to`` so the folder still tells the story. Empty input
        returns an empty string."""
        if not self.origin and not self.destination:
            return ""
        if self.destination:
            base = f"{self.origin} to {self.destination}"
        else:
            base = self.origin
        if self.transport:
            return f"{base} - {self.transport}"
        return base


def parse_location(text: str) -> LocationParts:
    """Parse a free-text location string into its structured parts.

    Robust to user formatting: surrounding whitespace is stripped,
    separator spacing is flexible (``A>B`` and ``A > B`` both work),
    a missing destination after ``>`` (``"A >"``) collapses to a stay
    day with ``origin="A"`` (the user probably hadn't finished
    typing), and a stray ``#`` with no text after it collapses
    similarly to ``transport=None``.

    Multiple ``>`` separators are not supported — only the FIRST one
    counts; everything after the second ``>`` is included in the
    destination string verbatim. (Multi-leg days are a rare enough
    case that they belong in the description column or a future
    explicit feature, not silent special-casing here.)
    """
    s = (text or "").strip()
    if not s:
        return LocationParts(origin="")

    # Split off the transport part first. Rule: any ``#`` preceded
    # by at least one space is the transport separator. A ``#`` glued
    # to a name (``Restaurant#3``) stays in the origin — the syntax
    # is opt-in, so users who use a space + ``#`` accept it as the
    # transport prefix. Simpler than scanning for known transport
    # words; predictable; easy to document.
    transport: Optional[str] = None
    if _TRANSPORT_SEP in s:
        idx = s.rfind(" " + _TRANSPORT_SEP)
        if idx == -1 and s.startswith(_TRANSPORT_SEP):
            # Whole string starts with #; honour as a leading-
            # transport pattern (pathological but unambiguous).
            transport = s[1:].strip() or None
            s = ""
        elif idx != -1:
            transport = s[idx + 2:].strip() or None
            s = s[:idx].strip()

    # Now split origin from destination on the FIRST ``>``.
    if _TRAVEL_SEP in s:
        left, _, right = s.partition(_TRAVEL_SEP)
        origin = left.strip()
        destination_str = right.strip() or None
        return LocationParts(
            origin=origin,
            destination=destination_str,
            transport=transport,
        )

    return LocationParts(
        origin=s,
        destination=None,
        transport=transport,
    )


__all__ = ["LocationParts", "parse_location"]
