"""spec/94 Phase 2 — operand inventory expands to GLOBAL ∪ BOUND-to-E.

When the user composes a Cut in event E, the operand picker should offer
both bound DCs (live in event.db.dynamic_collection) and global
Collections (live as JSON files in the library). spec/93 §6 names this
the "load set"; ``EventGateway.dc_operand_inventory`` is the gateway
seam the dialog reads.
"""
from __future__ import annotations

import itertools
from typing import Dict, Tuple

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.store.repo import EventStore

from tests.test_gateway_cuts import _doc, _now


def _payload(expr, filters=None):
    return {"expr": list(expr), "filters": dict(filters or {})}


def _make_factory(by_id, by_name):
    state = {"calls": 0}

    def _factory() -> Tuple[Dict[str, dict], Dict[str, dict]]:
        state["calls"] += 1
        return dict(by_id), dict(by_name)

    return _factory, state


def _gateway(tmp_path, *, by_id=None, by_name=None):
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    counter = itertools.count(1)
    factory = None
    state = None
    if by_id is not None:
        factory, state = _make_factory(by_id, by_name or {})
    g = EventGateway(
        store, now=_now, new_id=lambda: f"id-{next(counter)}",
        collections_library_factory=factory,
    )
    return g, state


# ── No library wired: legacy behaviour ───────────────────────────


def test_no_library_only_bound_dcs_and_cuts(tmp_path):
    """Without a wired library, the inventory matches the pre-Phase-2
    contract: base + bound DCs + Cuts."""
    g, _state = _gateway(tmp_path)
    try:
        g.create_dc("birds", expr=[["+", "exported"]])
        inv = g.dc_operand_inventory()
        tags = [(row["kind"], row["tag"]) for row in inv]
        assert ("base", "exported") in tags
        assert ("dc", "birds") in tags
        assert ("cut", "short_version") in tags
    finally:
        g.close()


# ── Library wired: GLOBAL ∪ BOUND-to-E ───────────────────────────


def test_inventory_unions_bound_and_global(tmp_path):
    """Bound DCs come first, then global Collections from the library,
    then Cuts (spec/93 §6 load set ordering)."""
    by_id = {
        "alpha-id": _payload([["+", "exported"]]),
        "beta-id": _payload([["+", "exported"]]),
    }
    by_name = {
        "alpha": by_id["alpha-id"],
        "beta": by_id["beta-id"],
    }
    g, _state = _gateway(tmp_path, by_id=by_id, by_name=by_name)
    try:
        g.create_dc("bound_one", expr=[["+", "exported"]])
        inv = g.dc_operand_inventory()
        dcs = [r for r in inv if r["kind"] == "dc"]
        # Bound DC first, then library Collections (alphabetical by id
        # insertion order — the by_id dict is preserved).
        tags = [r["tag"] for r in dcs]
        assert tags == ["bound_one", "alpha", "beta"]


    finally:
        g.close()


def test_id_in_both_stores_appears_once_as_bound(tmp_path):
    """If a definition's id appears in BOTH event.db and the library
    (e.g. the dual-home migration ran but the user hand-edited a stale
    JSON copy), the bound row wins so the resolver and the dialog agree
    on a single identity."""
    fixed_id = "shared-id-aaaaaa"
    counter = itertools.count(1)
    by_id = {fixed_id: _payload([["+", "exported"]])}
    by_name = {"global_copy": by_id[fixed_id]}
    factory, _state = _make_factory(by_id, by_name)

    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    # Force ``create_dc`` to mint the colliding id so both stores carry
    # the same identity.
    pending_ids = iter([fixed_id])

    def _next_id():
        try:
            return next(pending_ids)
        except StopIteration:
            return f"id-{next(counter)}"

    g = EventGateway(
        store, now=_now, new_id=_next_id,
        collections_library_factory=factory,
    )
    try:
        g.create_dc("bound_with_same_id", expr=[["+", "exported"]])
        inv = g.dc_operand_inventory()
        dc_ids = [r["operand"]["id"] for r in inv if r["kind"] == "dc"]
        assert dc_ids.count(fixed_id) == 1
    finally:
        g.close()


def test_library_chip_uses_display_name_tag(tmp_path):
    """The operand chip for a library Collection carries its display
    name (the filename stem), not the id."""
    by_id = {"long-uuid-here": _payload([["+", "exported"]])}
    by_name = {"Best Wildlife": by_id["long-uuid-here"]}
    g, _state = _gateway(tmp_path, by_id=by_id, by_name=by_name)
    try:
        inv = g.dc_operand_inventory()
        library_chips = [r for r in inv if r["kind"] == "dc"]
        assert len(library_chips) == 1
        row = library_chips[0]
        assert row["tag"] == "Best Wildlife"
        assert row["operand"]["id"] == "long-uuid-here"
        assert row["operand"]["tag"] == "Best Wildlife"
    finally:
        g.close()


# ── Cache contract: inventory uses the same snapshot as resolver ─


def test_inventory_does_not_double_scan_with_resolver(tmp_path):
    """The library snapshot is built ONCE per open_event() — both
    ``dc_operand_inventory`` and ``resolve_dc`` share the same cache."""
    by_id = {"x-id": _payload([["+", "exported"]])}
    by_name = {"x": by_id["x-id"]}
    g, state = _gateway(tmp_path, by_id=by_id, by_name=by_name)
    try:
        g.resolve_dc([["+", {"kind": "dc", "id": "x-id"}]])
        g.dc_operand_inventory()
        g.dc_operand_inventory()
        g.resolve_dc([["+", {"kind": "dc", "id": "x-id"}]])
        assert state["calls"] == 1
    finally:
        g.close()
