"""The gateway — the hard interface (spec/08, charter §2 + §4 step 6).

The **only** way UI code touches data. The UI holds a :class:`Gateway`, asks it for
what it renders, and tells it what the user did; it never sees SQLite. Public surface:

- :class:`Gateway`        — the umbrella: events list, ``photos_base_path`` anchor,
  ``open_event``, ``materialise_event`` (restore + create-from-document).
- :class:`EventGateway`   — the per-event facade over one open ``event.db`` (queries
  + mutators).
- :class:`EventsIndex`    — the cross-event thin pointer (``events_index.json``).
- :func:`make_entry`      — the one path re-anchoring rule (charter §5.9).
"""

from mira.gateway.event_gateway import EventGateway
from mira.gateway.gateway import EventsListing, EventsQuery, Gateway
from mira.gateway.index import EventsIndex, make_entry

__all__ = [
    "Gateway", "EventGateway", "EventsIndex", "EventsListing", "EventsQuery",
    "make_entry",
]
