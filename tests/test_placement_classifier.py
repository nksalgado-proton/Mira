"""Tests for ``core.placement_classifier`` — spec/93 §5 rule.

The classifier is pure logic + two lookup callbacks. Each test wires
the callbacks to a small in-memory dict so the rule's branches —
``GLOBAL`` / ``BoundPlacement(E)`` / ``CROSS-BOUND`` — are easy to
exercise without a database."""
from __future__ import annotations

from typing import Dict, Mapping, Optional

import pytest

from core.placement_classifier import (
    PLACEMENT_CROSS_BOUND,
    PLACEMENT_GLOBAL,
    BoundPlacement,
    OperandClosureContext,
    classify_placement,
    placement_badge_text,
    placement_is_file,
)


def _ctx(
    dcs: Optional[Dict[str, Mapping]] = None,
    cuts_to_event: Optional[Dict[str, Optional[str]]] = None,
) -> OperandClosureContext:
    """Build a context from two simple dicts. ``cuts_to_event[id]``
    is the event_id for single-event Cuts, or ``None`` for cross-
    event."""
    dcs = dcs or {}
    cuts_to_event = cuts_to_event or {}

    def _dc(op: Mapping) -> Optional[Mapping]:
        return dcs.get(op.get("id"))

    def _cut(op: Mapping) -> Optional[str]:
        cid = op.get("id") or ""
        if cid not in cuts_to_event:
            return None
        return cuts_to_event[cid]

    return OperandClosureContext(
        dc_composition_by_ref=_dc,
        cut_event_by_ref=_cut,
    )


# ── Global ────────────────────────────────────────────────────────


def test_universal_only_source_is_global():
    """A Source built from a base universe (`exported`) and no event
    references → GLOBAL."""
    comp = {"source": [["+", "exported"]], "otherwise": "skip"}
    out = classify_placement(comp, _ctx())
    assert out == PLACEMENT_GLOBAL


def test_vocabulary_filters_are_global():
    """Style/Media/Camera/Lens filters are universal — they don't bind."""
    comp = {
        "source": [["+", "exported"]],
        "filters": {"styles": ["macro"], "camera_ids": ["G9"]},
        "otherwise": "skip",
    }
    out = classify_placement(comp, _ctx())
    assert out == PLACEMENT_GLOBAL


def test_nested_global_dc_stays_global():
    """A DC that itself is global (built from universal pieces) doesn't
    introduce a binding."""
    nested = {"source": [["+", "exported"]], "otherwise": "skip"}
    comp = {
        "source": [["+", {"kind": "dc", "id": "global-dc"}]],
        "otherwise": "skip",
    }
    out = classify_placement(comp, _ctx(dcs={"global-dc": nested}))
    assert out == PLACEMENT_GLOBAL


# ── Bound ────────────────────────────────────────────────────────


def test_single_event_cut_operand_binds():
    """One single-event Cut operand → BoundPlacement(event)."""
    comp = {
        "source": [["+", {"kind": "cut", "id": "cut-1"}]],
        "otherwise": "skip",
    }
    out = classify_placement(comp, _ctx(cuts_to_event={"cut-1": "evt-A"}))
    assert isinstance(out, BoundPlacement)
    assert out.event_id == "evt-A"


def test_rule_predicate_cut_binds():
    """A Cut operand inside a rule predicate also binds (the whole
    operand closure is walked, not just Source)."""
    comp = {
        "source": [["+", "exported"]],
        "rules": [
            {
                "predicate": [["+", {"kind": "cut", "id": "cut-1"}]],
                "verdict": "pick",
            }
        ],
        "otherwise": "skip",
    }
    out = classify_placement(comp, _ctx(cuts_to_event={"cut-1": "evt-A"}))
    assert isinstance(out, BoundPlacement)
    assert out.event_id == "evt-A"


def test_nested_dc_that_pins_a_cut_binds():
    """A DC operand that itself references a single-event Cut binds
    the parent — recursion through the DC closure."""
    nested = {
        "source": [["+", {"kind": "cut", "id": "cut-1"}]],
        "otherwise": "skip",
    }
    comp = {
        "source": [["+", {"kind": "dc", "id": "wrapper-dc"}]],
        "otherwise": "skip",
    }
    ctx = _ctx(
        dcs={"wrapper-dc": nested},
        cuts_to_event={"cut-1": "evt-A"},
    )
    out = classify_placement(comp, ctx)
    assert isinstance(out, BoundPlacement)
    assert out.event_id == "evt-A"


def test_same_event_referenced_twice_stays_bound():
    """Two Cut operands from the SAME event still count as one
    bound event (set semantics, not list)."""
    comp = {
        "source": [
            ["+", {"kind": "cut", "id": "cut-1"}],
            ["+", {"kind": "cut", "id": "cut-2"}],
        ],
        "otherwise": "skip",
    }
    out = classify_placement(
        comp, _ctx(cuts_to_event={"cut-1": "evt-A", "cut-2": "evt-A"}),
    )
    assert isinstance(out, BoundPlacement)
    assert out.event_id == "evt-A"


# ── Cross-bound ──────────────────────────────────────────────────


def test_two_events_in_closure_is_cross_bound():
    """Two single-event Cut operands from DIFFERENT events → CROSS-BOUND."""
    comp = {
        "source": [
            ["+", {"kind": "cut", "id": "cut-A"}],
            ["+", {"kind": "cut", "id": "cut-B"}],
        ],
        "otherwise": "skip",
    }
    ctx = _ctx(cuts_to_event={"cut-A": "evt-A", "cut-B": "evt-B"})
    out = classify_placement(comp, ctx)
    assert out == PLACEMENT_CROSS_BOUND


def test_cross_event_cut_does_not_introduce_binding():
    """A cross-event Cut operand (event_id=None) is a fixed frozen set
    — not a single-event binding (spec/93 §5 paragraph)."""
    comp = {
        "source": [["+", {"kind": "cut", "id": "ce-cut"}]],
        "otherwise": "skip",
    }
    out = classify_placement(
        comp, _ctx(cuts_to_event={"ce-cut": None}),
    )
    assert out == PLACEMENT_GLOBAL


def test_nested_dc_referencing_two_events_is_cross_bound():
    """Recursion through a nested DC accumulates bound events; ≥2 →
    CROSS-BOUND."""
    nested_a = {
        "source": [["+", {"kind": "cut", "id": "cut-A"}]],
        "otherwise": "skip",
    }
    comp = {
        "source": [
            ["+", {"kind": "dc", "id": "uses-A"}],
            ["+", {"kind": "cut", "id": "cut-B"}],
        ],
        "otherwise": "skip",
    }
    ctx = _ctx(
        dcs={"uses-A": nested_a},
        cuts_to_event={"cut-A": "evt-A", "cut-B": "evt-B"},
    )
    out = classify_placement(comp, ctx)
    assert out == PLACEMENT_CROSS_BOUND


# ── Graceful handling of missing operands ────────────────────────


def test_missing_dc_operand_contributes_no_binding():
    """When a referenced DC is gone (deleted out-of-band), the
    classifier skips it — the missing-ingredient warning is the
    dialog's job (spec/93 §8)."""
    comp = {
        "source": [["+", {"kind": "dc", "id": "ghost"}]],
        "otherwise": "skip",
    }
    out = classify_placement(comp, _ctx())  # ghost is not in dcs
    assert out == PLACEMENT_GLOBAL


def test_dc_cycle_does_not_loop():
    """A self-referential DC closure terminates — memoised seen-ids."""
    # ``self-ref`` references itself via Source.
    self_ref = {
        "source": [["+", {"kind": "dc", "id": "self-ref"}]],
        "otherwise": "skip",
    }
    comp = {
        "source": [["+", {"kind": "dc", "id": "self-ref"}]],
        "otherwise": "skip",
    }
    out = classify_placement(comp, _ctx(dcs={"self-ref": self_ref}))
    assert out == PLACEMENT_GLOBAL


def test_handles_empty_composition():
    """An empty composition (no source, no rules) classifies as global."""
    out = classify_placement({}, _ctx())
    assert out == PLACEMENT_GLOBAL


# ── Badge text + helpers ────────────────────────────────────────


def test_badge_text_for_global():
    assert placement_badge_text(PLACEMENT_GLOBAL) == "Global"


def test_badge_text_for_cross_bound():
    assert placement_badge_text(PLACEMENT_CROSS_BOUND) == "Cross-event"


def test_badge_text_for_bound_with_known_name():
    p = BoundPlacement(event_id="evt-A")
    assert placement_badge_text(p, event_name="Costa Rica") == \
        "Event: Costa Rica"


def test_badge_text_for_bound_with_unknown_name():
    p = BoundPlacement(event_id="abcdef1234")
    # Falls back to a short id stub when the gateway can't resolve it.
    assert placement_badge_text(p) == "Event: abcdef12"


def test_placement_is_file_helper():
    assert placement_is_file(PLACEMENT_GLOBAL) is True
    assert placement_is_file(PLACEMENT_CROSS_BOUND) is True
    assert placement_is_file(BoundPlacement(event_id="x")) is False
