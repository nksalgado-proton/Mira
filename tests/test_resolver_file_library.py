"""spec/94 Phase 2 — resolver falls through to the file-based Collection
library (spec/93 §4 / §6) when an operand isn't a bound DC.

The set-algebra resolver lives in :mod:`core.collection_resolver`; the
EventGateway wires its DC-by-ref accessor through ``_operand_dc`` which,
in Phase 2, checks event.db FIRST and then falls back to a callable
``collections_library_factory``. The factory is invoked LAZILY and its
result is cached on the EventGateway instance for the lifetime of one
``open_event()`` so a single resolution pass walks the JSON tree at most
once even when many operands point at library files.
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


def _make_factory(payloads_by_id, payloads_by_name=None):
    """Build a library snapshot factory that returns ``(by_id, by_name)``.

    Tracks the call count so the per-``open_event()`` cache contract
    can be asserted directly.
    """
    payloads_by_name = payloads_by_name or {}
    state = {"calls": 0}

    def _factory() -> Tuple[Dict[str, dict], Dict[str, dict]]:
        state["calls"] += 1
        return dict(payloads_by_id), dict(payloads_by_name)

    return _factory, state


@pytest.fixture
def gw_no_library(tmp_path):
    """EventGateway constructed without a library factory — the legacy
    surface (event.db-only). Exists to prove the fallback path stays
    inert until a factory is wired."""
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(store, now=_now, new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


@pytest.fixture
def gw_with_library(tmp_path):
    """Factory that returns a single library Collection — ``wildlife``.

    Its expression resolves to ``#exported − the short_version Cut`` so a
    resolver run against this library picks the same set the bound
    fixture would have returned (e2 / e3a / e3b / v1)."""
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    counter = itertools.count(1)
    by_id = {
        "wildlife-id": _payload(
            [["+", "exported"],
             ["-", {"kind": "cut", "tag": "short_version"}]],
            filters={},
        ),
    }
    by_name = {"wildlife": by_id["wildlife-id"]}
    factory, state = _make_factory(by_id, by_name)
    g = EventGateway(
        store, now=_now, new_id=lambda: f"id-{next(counter)}",
        collections_library_factory=factory,
    )
    yield g, state
    g.close()


# ── No factory: legacy surface untouched ──────────────────────────


def test_no_factory_event_db_only_behaviour(gw_no_library):
    """A library-style operand resolves to empty when no factory is
    wired — the existing event.db-only behaviour is preserved."""
    rows = gw_no_library.resolve_dc(
        [["+", {"kind": "dc", "id": "wildlife-id"}]])
    assert rows == []


# ── Factory wiring + cache contract ───────────────────────────────


def test_library_operand_resolves_via_factory(gw_with_library):
    """A Collection operand whose id is in the library resolves to that
    file's expr, walked by the same set-algebra engine."""
    gw, _state = gw_with_library
    rows = gw.resolve_dc([["+", {"kind": "dc", "id": "wildlife-id"}]])
    rels = [ln.export_relpath for ln in rows]
    assert rels == [
        "Exported Media/e2.jpg", "Exported Media/e3a.jpg",
        "Exported Media/e3b.jpg", "Exported Media/v1.mp4",
    ]


def test_factory_called_once_per_open_event(gw_with_library):
    """spec/94 Phase 2 — the snapshot factory is invoked LAZILY at first
    operand lookup and its result is cached for the lifetime of the
    EventGateway. Two consecutive resolves walk the library snapshot
    exactly once."""
    gw, state = gw_with_library
    gw.resolve_dc([["+", {"kind": "dc", "id": "wildlife-id"}]])
    gw.resolve_dc([["+", {"kind": "dc", "id": "wildlife-id"}]])
    gw.dc_probe([["+", {"kind": "dc", "id": "wildlife-id"}]])
    assert state["calls"] == 1


def test_factory_not_invoked_when_event_db_satisfies(gw_with_library):
    """An operand satisfied by the event.db dynamic_collection table
    should NEVER touch the library — the cache stays unwarmed."""
    gw, state = gw_with_library
    # Seed an event.db DC.
    bound = gw.create_dc("bound_one", expr=[["+", "exported"]])
    gw.resolve_dc([["+", {"kind": "dc", "id": bound.id}]])
    assert state["calls"] == 0


# ── Resolution by name fallback (hand-authored files) ────────────


def test_name_fallback_for_hand_authored_file(tmp_path):
    """spec/93 §4 — references are ``{id, name}`` resolved by id with
    name as a fallback. A hand-authored file may carry the wrong (or
    no) id; the library indexes it under its display name too."""
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    counter = itertools.count(1)
    by_id = {
        # The id the saver writes for hand-authored files when no id
        # is present — bypassed entirely if the caller passed only a
        # tag/name.
        "real-id-aaaa": _payload([["+", "exported"]]),
    }
    by_name = {"my_curation": by_id["real-id-aaaa"]}
    factory, state = _make_factory(by_id, by_name)
    g = EventGateway(
        store, now=_now, new_id=lambda: f"id-{next(counter)}",
        collections_library_factory=factory,
    )
    try:
        rows = g.resolve_dc(
            [["+", {"kind": "dc", "id": "stale-pointer",
                    "tag": "my_curation"}]])
        rels = [ln.export_relpath for ln in rows]
        assert rels == [
            "Exported Media/e1.jpg", "Exported Media/e2.jpg",
            "Exported Media/e3a.jpg", "Exported Media/e3b.jpg",
            "Exported Media/v1.mp4",
        ]
    finally:
        g.close()


# ── Set algebra + grouping-by-nesting across the boundary ────────


def test_left_to_right_with_library_operand(tmp_path):
    """spec/81 §2 — set algebra evaluates left-to-right; the library
    operand drops into the chain like any other operand. Source =
    ``#exported − wildlife`` should leave e1 only."""
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    counter = itertools.count(1)
    by_id = {
        "wildlife-id": _payload(
            [["+", {"kind": "cut", "tag": "short_version"}]],
            # The library Collection wraps "the short_version Cut" so
            # the outer expression below is ``#exported − that Cut``.
            # The Cut's frozen members are {e1}, so the result is the
            # complement: e2/e3a/e3b/v1.
            filters={},
        ),
    }
    factory, _state = _make_factory(by_id)
    g = EventGateway(
        store, now=_now, new_id=lambda: f"id-{next(counter)}",
        collections_library_factory=factory,
    )
    try:
        rows = g.resolve_dc([
            ["+", "exported"],
            ["-", {"kind": "dc", "id": "wildlife-id"}],
        ])
        rels = [ln.export_relpath for ln in rows]
        assert rels == [
            "Exported Media/e2.jpg", "Exported Media/e3a.jpg",
            "Exported Media/e3b.jpg", "Exported Media/v1.mp4",
        ]
    finally:
        g.close()


def test_nested_library_collection_acts_as_parens(tmp_path):
    """spec/81 §2 — "grouping is done by nesting a DC as an operand".
    A library Collection used inside another expression behaves like
    a parenthesised sub-expression."""
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    counter = itertools.count(1)
    by_id = {
        # parens_inner = #exported − short_version → e2,e3a,e3b,v1
        "parens-inner": _payload(
            [["+", "exported"],
             ["-", {"kind": "cut", "tag": "short_version"}]],
            filters={},
        ),
    }
    factory, _state = _make_factory(by_id)
    g = EventGateway(
        store, now=_now, new_id=lambda: f"id-{next(counter)}",
        collections_library_factory=factory,
    )
    try:
        # Outer = #exported ∩ parens_inner → e2,e3a,e3b,v1 (the
        # intersection collapses to the inner set since it's already a
        # subset of #exported).
        rows = g.resolve_dc([
            ["+", "exported"],
            ["&", {"kind": "dc", "id": "parens-inner"}],
        ])
        rels = [ln.export_relpath for ln in rows]
        assert rels == [
            "Exported Media/e2.jpg", "Exported Media/e3a.jpg",
            "Exported Media/e3b.jpg", "Exported Media/v1.mp4",
        ]
    finally:
        g.close()


# ── Missing operand: graceful empty (no crash) ───────────────────


def test_missing_library_operand_resolves_to_empty(gw_with_library):
    """A DC ref whose id/name isn't in the library is silently empty —
    the same graceful-shrink contract as missing event.db DCs (spec/93
    §8)."""
    gw, _state = gw_with_library
    rows = gw.resolve_dc(
        [["+", {"kind": "dc", "id": "no-such-id", "tag": "never"}]])
    assert rows == []


# ── Live count probe ─────────────────────────────────────────────


def test_dc_probe_counts_via_library(gw_with_library):
    """``dc_probe`` is the dialog's metrics-row count; it must see the
    library too."""
    gw, _state = gw_with_library
    n = gw.dc_probe([["+", {"kind": "dc", "id": "wildlife-id"}]])
    assert n == 4


# ── Recipe resolver: strict walk + predicates fall through ───────


def test_resolve_recipe_strict_walk_accepts_library_dc(gw_with_library):
    """A Recipe whose source references a library Collection passes
    the strict-reference walk (spec/90 §1.4) and resolves correctly."""
    gw, _state = gw_with_library
    composition = {
        "source": [["+", {"kind": "dc", "id": "wildlife-id"}]],
        "otherwise": "skip",
    }
    resolution = gw.resolve_recipe(composition)
    assert resolution.pool == [
        "Exported Media/e2.jpg", "Exported Media/e3a.jpg",
        "Exported Media/e3b.jpg", "Exported Media/v1.mp4",
    ]


def test_resolve_recipe_rule_predicate_resolves_library_dc(gw_with_library):
    """A rule predicate that picks up a library Collection works too —
    the Recipe resolver delegates to ``collection_resolver`` for each
    predicate, with the same DC accessor."""
    gw, _state = gw_with_library
    composition = {
        "source": [["+", "exported"]],
        "rules": [{
            "predicate": [["+", {"kind": "dc", "id": "wildlife-id"}]],
            "verdict": "pick",
        }],
        "otherwise": "skip",
    }
    resolution = gw.resolve_recipe(composition)
    # Pool = #exported = all 5 lineage rows.
    assert len(resolution.pool) == 5
    # The library-Collection rule marks e2/e3a/e3b/v1 as picked.
    picked = {k for k, v in resolution.seed.items() if v}
    assert picked == {
        "Exported Media/e2.jpg", "Exported Media/e3a.jpg",
        "Exported Media/e3b.jpg", "Exported Media/v1.mp4",
    }
