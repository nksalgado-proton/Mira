"""The Pick-phase gate — spec/52 §10.

The user cannot enter the **Pick phase** for an event until its plan is
**complete**. Plan-complete means:

* Every **checked-for-import** day has ``country``, ``tz`` and ``location``
  all non-empty. (Hidden days don't count toward the gate — they're soft-
  excluded everywhere per ``spec/14 §5C.1``.)
* The event has ``name``, ``event_type`` (non-``unclassified``), and
  ``event_subtype`` set.

Description is **not** required (spec/52 §4).

The UI affordance is a **disabled** Pick button + a static tooltip
("Pick is locked until each day has country, timezone and location.") — no
modal block (spec/52 §10). The structured :class:`PlanGateOutcome` returned
here drives both the boolean gate and the "Why?" diagnostic surface (a click
on the disabled tile can open a small panel listing the specific gaps).

This module is pure logic. The single input is a per-event
:class:`~mira.gateway.event_gateway.EventGateway`; the gateway already
exposes the typed reads (:meth:`event`, :meth:`trip_days`) so we don't reach
into SQLite directly.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Tuple

if TYPE_CHECKING:
    from mira.gateway.event_gateway import EventGateway

log = logging.getLogger(__name__)


# The static UI tooltip — spec/52 §10. tr()'d at the call site, not here, to
# keep this module free of Qt.
PICK_GATE_TOOLTIP = "Pick is locked until each day has country, timezone and location."


# --------------------------------------------------------------------------- #
# Outcome shape
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DayGap:
    """One day's missing-field set. ``day_number`` is the trip_day PK;
    ``missing`` is the human field names ('country' / 'timezone' / 'location')
    in display order."""

    day_number: int
    missing: Tuple[str, ...]


@dataclass(frozen=True)
class PlanGateOutcome:
    """Result of :func:`evaluate`. ``complete`` is the single boolean the UI
    consumes for the Pick affordance; the diagnostic fields below let a
    "Why is this locked?" panel render specifics."""

    complete: bool
    event_gaps: Tuple[str, ...] = field(default_factory=tuple)
    day_gaps: Tuple[DayGap, ...] = field(default_factory=tuple)

    def summary(self) -> str:
        """One-sentence diagnostic — the form a "Why is this locked?" panel
        renders when the user clicks the disabled Pick affordance.

        Returns ``""`` when complete. When incomplete, leads with the event-
        level gaps (they block everything) then summarises the day-level
        gaps as a count + which fields are missing across them."""
        if self.complete:
            return ""
        parts: List[str] = []
        if self.event_gaps:
            parts.append("Event needs: " + ", ".join(self.event_gaps))
        if self.day_gaps:
            # Aggregate which field names appear across all incomplete days.
            field_counts: dict[str, int] = {}
            for gap in self.day_gaps:
                for f in gap.missing:
                    field_counts[f] = field_counts.get(f, 0) + 1
            field_str = ", ".join(
                f"{name} ({n} day{'s' if n != 1 else ''})"
                for name, n in field_counts.items()
            )
            parts.append(
                f"{len(self.day_gaps)} day{'s' if len(self.day_gaps) != 1 else ''} need: {field_str}"
            )
        return ". ".join(parts) + "."


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #


def _is_empty(value) -> bool:
    """Cross-type empty check — None / "" / whitespace-only strings count as
    missing for the gate. Numeric 0 is NOT empty (tz_minutes=0 = UTC is a
    valid value)."""
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _country_code_from_extras(extras_json: str) -> str | None:
    """Pull ``country_code`` out of a trip_day's ``extras_json`` blob. The
    schema's documented expected key is ``country_code`` (ISO 3166-1
    alpha-2). Tolerant on malformed blobs — treats them as no country."""
    try:
        data = json.loads(extras_json or "{}")
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    code = data.get("country_code")
    return str(code).strip() if isinstance(code, str) and code.strip() else None


def evaluate(event_gw: "EventGateway") -> PlanGateOutcome:
    """Compute the Pick-gate state for one event.

    Reads event + trip_days through the gateway (no direct SQL). Hidden days
    are skipped — they're soft-excluded from the gate the same way they're
    excluded from phase metrics (spec/14 §5C.1)."""

    ev = event_gw.event()
    event_gaps: List[str] = []
    if _is_empty(ev.name):
        event_gaps.append("name")
    if _is_empty(ev.event_type) or ev.event_type == "unclassified":
        event_gaps.append("type")
    if _is_empty(ev.event_subtype):
        event_gaps.append("subtype")

    day_gaps: List[DayGap] = []
    for day in event_gw.trip_days():
        if day.hidden:
            continue
        missing: List[str] = []
        if _country_code_from_extras(day.extras_json) is None:
            missing.append("country")
        if day.tz_minutes is None:
            missing.append("timezone")
        if _is_empty(day.location):
            missing.append("location")
        if missing:
            day_gaps.append(DayGap(day_number=day.day_number, missing=tuple(missing)))

    complete = not event_gaps and not day_gaps
    return PlanGateOutcome(
        complete=complete,
        event_gaps=tuple(event_gaps),
        day_gaps=tuple(day_gaps),
    )
