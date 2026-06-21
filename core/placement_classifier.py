"""Auto-placement classifier (spec/93 §5).

A definition's home (a JSON file in the recipe library, or a row in
one event's ``event.db``) is **computed** from its operand closure,
deterministically, never user-chosen.

The rule, verbatim from spec/93 §5:

    bound_events = ⋃ over the closure of
                   { the owning event of each single-event Cut/DC operand }

    |bound_events| == 0  → GLOBAL          → a JSON file in the recipe library
    |bound_events| == 1  → BOUND(event E)  → that event's event.db
    |bound_events| >= 2  → CROSS-BOUND     → a JSON file in the recipe library

The classifier walks the composition's ``scope`` + ``source`` + every
rule predicate, recursing through nested ``dc`` operands (memoised so
a cycle terminates). Base universes (the ladder rungs), vocabulary
filters, cross-event Cuts, Event Collections, and date ranges
contribute no binding. The only operand that BINDS is a single-event
``cut`` reference (or a ``dc`` that recursively pins one).

Pure logic + callbacks — no Qt, no gateway, no SQL. The caller
provides two lookups so the classifier can recurse through
references without knowing the database:

* ``dc_composition_by_ref`` — given a ``{"kind": "dc", "id": …}``
  operand, return that DC's composition (so we can walk INTO it) or
  ``None`` if it's missing.
* ``cut_event_by_ref`` — given a ``{"kind": "cut", "id": …}``
  operand, return the event_id it's bound to, or ``None`` if it's
  cross-event / unknown.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Optional, Sequence, Set, Union

log = logging.getLogger(__name__)


#: Sentinel returned when the composition's operand closure references
#: no single-event Cut/DC.
PLACEMENT_GLOBAL = "global"

#: Sentinel returned when the closure references ≥ 2 distinct events.
PLACEMENT_CROSS_BOUND = "cross_bound"


@dataclass(frozen=True)
class BoundPlacement:
    """Returned when exactly one event is referenced in the closure —
    the definition lives in *that* event's ``event.db``."""
    event_id: str


#: The classifier's return type. A string sentinel for the two file
#: outcomes; a :class:`BoundPlacement` for the single-event case.
Placement = Union[str, BoundPlacement]


@dataclass(frozen=True)
class OperandClosureContext:
    """Lookups the classifier needs to walk through references.

    Both callbacks accept the operand dict as recorded in the
    composition (``{"kind": "dc", "id": …, "name": …}`` etc.). They
    may return ``None`` to signal a missing operand — the classifier
    treats missing operands as "contributes no binding", which is the
    graceful failure spec/93 §8 mandates (a missing operand is reported
    elsewhere as "missing ingredient" — but it must never derail the
    classifier).

    ``dc_composition_by_ref`` returns ``{"source": [...], "scope": [...],
    "rules": [...], ...}``. ``cut_event_by_ref`` returns the
    ``event_id`` string for single-event Cuts, ``None`` for
    cross-event Cuts (no single-event binding).
    """
    dc_composition_by_ref: Callable[[Mapping], Optional[Mapping]]
    cut_event_by_ref: Callable[[Mapping], Optional[str]]


def classify_placement(
    composition: Mapping,
    ctx: OperandClosureContext,
) -> Placement:
    """Walk the operand closure and return the spec/93 §5 placement.

    Cycle-safe — a ``dc`` operand whose closure transitively references
    itself is visited at most once. Memoised by id within the walk.
    The returned :class:`BoundPlacement` carries the event_id (the
    "bound to event X" badge text reads from this).
    """
    bound_events: Set[str] = set()
    seen_dc_ids: Set[str] = set()

    def _walk_expr(expr: Optional[Sequence]) -> None:
        if expr is None:
            return
        if not isinstance(expr, (list, tuple)):
            return
        for term in expr:
            if not isinstance(term, (list, tuple)) or len(term) < 2:
                continue
            _walk_operand(term[1])

    def _walk_operand(operand) -> None:
        if isinstance(operand, str):
            # Base universe / vocabulary token — relative, no binding.
            return
        if not isinstance(operand, Mapping):
            return
        kind = operand.get("kind")
        if kind == "dc":
            dc_id = operand.get("id") or ""
            if not isinstance(dc_id, str) or not dc_id:
                # Hand-authored / name-only reference — without an id
                # the recursion can't proceed safely (the name fallback
                # may resolve at the gateway layer but the classifier
                # walks raw refs).
                return
            if dc_id in seen_dc_ids:
                return
            seen_dc_ids.add(dc_id)
            sub = ctx.dc_composition_by_ref(operand)
            if sub is None:
                # Missing operand — spec/93 §8: graceful skip. The dialog
                # surfaces the "missing ingredient" warning separately.
                return
            _walk_composition(sub)
        elif kind == "cut":
            event_id = ctx.cut_event_by_ref(operand)
            if event_id:
                bound_events.add(event_id)
        # Anything else (event, event_collection, date_range, person,
        # vocabulary chip) — no single-event binding.

    def _walk_composition(c: Mapping) -> None:
        _walk_expr(c.get("source"))
        _walk_expr(c.get("scope"))
        for r in c.get("rules") or ():
            if isinstance(r, Mapping):
                _walk_expr(r.get("predicate"))

    _walk_composition(composition)

    n = len(bound_events)
    if n == 0:
        return PLACEMENT_GLOBAL
    if n == 1:
        return BoundPlacement(event_id=next(iter(bound_events)))
    return PLACEMENT_CROSS_BOUND


def placement_badge_text(placement: Placement, *, event_name: str = "") -> str:
    """Return the human-readable badge text for the spec/93 §7 binding
    badge ("Global" / "Event A" / "Spans N events").

    ``event_name`` is the BOUND event's display name when known — the
    caller resolves the event_id to a name via the gateway and passes
    it through. Falls back to a short id stub when the name is unknown.
    """
    if placement == PLACEMENT_GLOBAL:
        return "Global"
    if placement == PLACEMENT_CROSS_BOUND:
        return "Cross-event"
    if isinstance(placement, BoundPlacement):
        if event_name:
            return f"Event: {event_name}"
        return f"Event: {placement.event_id[:8]}"
    return "?"


def placement_is_file(placement: Placement) -> bool:
    """True when the placement says "live as a JSON file in the
    recipe library" (GLOBAL or CROSS-BOUND). False when it's a BOUND
    placement (lives in an event's ``event.db``)."""
    return placement in (PLACEMENT_GLOBAL, PLACEMENT_CROSS_BOUND)


__all__ = [
    "BoundPlacement",
    "OperandClosureContext",
    "PLACEMENT_CROSS_BOUND",
    "PLACEMENT_GLOBAL",
    "Placement",
    "classify_placement",
    "placement_badge_text",
    "placement_is_file",
]
