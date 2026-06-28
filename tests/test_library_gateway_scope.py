"""spec/94 Phase 4a — Scope-narrowing seam on :class:`LibraryGateway`.

The Collection face (spec/90 §1.1, §3.1) composes a Scope sentence —
Events, Event Collections, date ranges joined with `or` / `and` / `but
not in`. Phase 4a wires that sentence end-to-end so the cross-event
session honours it: the dialog resolves chips → uuids via
:meth:`LibraryGateway.resolve_scope`, then the resolver narrows
``global_items`` to those uuids via
:meth:`LibraryGateway.resolve_dc_keys`'s new ``scope=`` parameter.

These tests pin the seam contract:

* ``resolve_scope`` returns ``None`` for an empty expression (the
  "library-wide" sentinel the resolver treats as "no narrowing").
* ``resolve_scope`` resolves event / event_collection / date_range
  chip operands to the right uuid set, including nested Event
  Collections and the spec/81 §2 left-to-right set algebra.
* ``resolve_dc_keys(..., scope=...)`` narrows the returned keys.
* ``resolve_dc(..., scope=...)`` + ``dc_probe(..., scope=...)`` honour
  the same ``scope`` shape.

No Qt, no event.db — the projection (``global_items``) +
``event_index`` + ``event_collection`` rows are the universe.
"""
from __future__ import annotations

import json

import pytest

from core import collection_resolver as cr
from mira.gateway.library_gateway import LibraryGateway
from mira.user_store import models as um
from mira.user_store.repo import UserStore


NOW = "2026-06-21T00:00:00+00:00"


# --------------------------------------------------------------------------- #
# Fixtures — three events: A (Costa Rica 2026-04), B (Nepal 2025-10),
# C (Patagonia 2024-12). All exported items so the ladder rung isn't the
# variable under test.
# --------------------------------------------------------------------------- #


def _open_user_store(tmp_path) -> UserStore:
    return UserStore.create(
        tmp_path / "mira.db",
        app_version="test",
        created_at=NOW,
    )


def _open_library(tmp_path):
    store = _open_user_store(tmp_path)
    return LibraryGateway(store, now=lambda: NOW), store


def _seed(store: UserStore) -> None:
    """Three events with a known event_index date range + a global_items
    population that lets us narrow visibly."""
    events = [
        um.EventIndex(
            event_uuid="A", relpath_to_base="A",
            name_cached="Costa Rica",
            start_date_cached="2026-04-01", end_date_cached="2026-04-07",
        ),
        um.EventIndex(
            event_uuid="B", relpath_to_base="B",
            name_cached="Nepal",
            start_date_cached="2025-10-10", end_date_cached="2025-10-20",
        ),
        um.EventIndex(
            event_uuid="C", relpath_to_base="C",
            name_cached="Patagonia",
            start_date_cached="2024-12-01", end_date_cached="2024-12-15",
        ),
    ]
    for e in events:
        store.upsert(e)

    rows = [
        um.GlobalItem(
            event_uuid="A", item_id="a1", synced_at=NOW,
            event_name="Costa Rica",
            event_start="2026-04-01", event_end="2026-04-07",
            capture_time="2026-04-02T10:00:00",
            kind="photo", classification="macro",
            has_export=True, export_relpath="A/a1.jpg",
        ),
        um.GlobalItem(
            event_uuid="A", item_id="a2", synced_at=NOW,
            event_name="Costa Rica",
            event_start="2026-04-01", event_end="2026-04-07",
            capture_time="2026-04-03T14:00:00",
            kind="photo", classification="wildlife",
            has_export=True, export_relpath="A/a2.jpg",
        ),
        um.GlobalItem(
            event_uuid="B", item_id="b1", synced_at=NOW,
            event_name="Nepal",
            event_start="2025-10-10", event_end="2025-10-20",
            capture_time="2025-10-15T08:00:00",
            kind="photo", classification="landscape",
            has_export=True, export_relpath="B/b1.jpg",
        ),
        um.GlobalItem(
            event_uuid="C", item_id="c1", synced_at=NOW,
            event_name="Patagonia",
            event_start="2024-12-01", event_end="2024-12-15",
            capture_time="2024-12-10T16:00:00",
            kind="photo", classification="landscape",
            has_export=True, export_relpath="C/c1.jpg",
        ),
    ]
    for r in rows:
        store.upsert(r)


# --------------------------------------------------------------------------- #
# resolve_scope — sentinel + operand kinds + algebra
# --------------------------------------------------------------------------- #


def test_resolve_scope_empty_is_library_wide_sentinel(tmp_path):
    """An empty Scope expression returns ``None`` — the caller treats that
    as "no narrowing" (spec/90 §1.1 — empty Scope on Collection face)."""
    lg, store = _open_library(tmp_path)
    try:
        assert lg.resolve_scope([]) is None
        assert lg.resolve_scope(()) is None
    finally:
        store.close()


def test_resolve_scope_event_operand(tmp_path):
    """A single ``{"kind":"event","uuid":…}`` chip → that one event."""
    lg, store = _open_library(tmp_path)
    _seed(store)
    try:
        out = lg.resolve_scope([["+", {"kind": "event", "uuid": "A"}]])
        assert out == frozenset({"A"})
    finally:
        store.close()


def test_resolve_scope_multiple_event_operands_union(tmp_path):
    """Two ``or``-joined Event chips → union of both events."""
    lg, store = _open_library(tmp_path)
    _seed(store)
    try:
        out = lg.resolve_scope([
            ["+", {"kind": "event", "uuid": "A"}],
            ["+", {"kind": "event", "uuid": "B"}],
        ])
        assert out == frozenset({"A", "B"})
    finally:
        store.close()


def test_resolve_scope_date_range_overlap(tmp_path):
    """A date-range chip matches every event whose cached date range
    overlaps the requested window. ``[2025-01-01, 2026-12-31]`` covers
    Nepal (2025-10) and Costa Rica (2026-04) but not Patagonia (2024-12)."""
    lg, store = _open_library(tmp_path)
    _seed(store)
    try:
        out = lg.resolve_scope([
            ["+", {"kind": "date_range",
                   "start": "2025-01-01", "end": "2026-12-31"}],
        ])
        assert out == frozenset({"A", "B"})
    finally:
        store.close()


def test_resolve_scope_date_range_open_ended(tmp_path):
    """Half-open ranges work: only ``end`` set narrows to events whose
    range starts before the bound. ``end='2024-12-31'`` matches just
    Patagonia (the only event ending in 2024)."""
    lg, store = _open_library(tmp_path)
    _seed(store)
    try:
        out = lg.resolve_scope([
            ["+", {"kind": "date_range", "end": "2024-12-31"}],
        ])
        assert out == frozenset({"C"})
    finally:
        store.close()


def test_resolve_scope_event_collection_resolves_nested_events(tmp_path):
    """An Event Collection chip resolves through its saved
    ``expr_json``: an EC whose expr lists events A + B yields {A, B}."""
    lg, store = _open_library(tmp_path)
    _seed(store)
    ec = um.EventCollection(
        id="ec-1", tag="recent_trips",
        created_at=NOW, updated_at=NOW,
        expr_json=json.dumps([
            ["+", {"kind": "event", "uuid": "A"}],
            ["+", {"kind": "event", "uuid": "B"}],
        ]),
    )
    store.upsert(ec)
    try:
        out = lg.resolve_scope([
            ["+", {"kind": "event_collection", "id": "ec-1"}],
        ])
        assert out == frozenset({"A", "B"})
    finally:
        store.close()


def test_resolve_scope_missing_event_collection_graceful_shrink(tmp_path):
    """A reference to a deleted Event Collection contributes nothing
    (graceful shrink, same as a missing DC operand). The expression as a
    whole resolves to the empty set, not ``None`` — empty = "narrow to
    nothing"; ``None`` = "library-wide" (no narrowing)."""
    lg, store = _open_library(tmp_path)
    _seed(store)
    try:
        out = lg.resolve_scope([
            ["+", {"kind": "event_collection", "id": "ec-gone"}],
        ])
        assert out == frozenset()
    finally:
        store.close()


def test_resolve_scope_intersection_and_difference(tmp_path):
    """Left-to-right set algebra: ``{A,B} ∩ {B,C}`` = ``{B}``;
    ``{A,B} − {B}`` = ``{A}``. Same engine the source resolver uses."""
    lg, store = _open_library(tmp_path)
    _seed(store)
    try:
        # A ∪ B then ∩ B → just B
        out = lg.resolve_scope([
            ["+", {"kind": "event", "uuid": "A"}],
            ["+", {"kind": "event", "uuid": "B"}],
            ["∩", {"kind": "event", "uuid": "B"}],
        ])
        assert out == frozenset({"B"})
        # A ∪ B then − B → just A
        out = lg.resolve_scope([
            ["+", {"kind": "event", "uuid": "A"}],
            ["+", {"kind": "event", "uuid": "B"}],
            ["−", {"kind": "event", "uuid": "B"}],
        ])
        assert out == frozenset({"A"})
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# resolve_dc_keys / resolve_dc / dc_probe — honour the scope parameter
# --------------------------------------------------------------------------- #


def test_resolve_dc_keys_scope_none_returns_library_wide(tmp_path):
    """``scope=None`` (the default) returns every projected ``#exported``
    key across all three events — the historical, pre-spec/94 behaviour
    stays intact."""
    lg, store = _open_library(tmp_path)
    _seed(store)
    try:
        keys = lg.resolve_dc_keys(
            [["+", cr.BASE_EXPORTED]], {}, scope=None)
        events = {k.split("::", 1)[0] for k in keys}
        assert events == {"A", "B", "C"}
    finally:
        store.close()


def test_resolve_dc_keys_scope_narrows_to_passed_uuids(tmp_path):
    """``scope={"A","B"}`` drops the C event's items from the result.
    The narrowing happens at the gateway seam, independent of the
    resolver's own filter clauses."""
    lg, store = _open_library(tmp_path)
    _seed(store)
    try:
        keys = lg.resolve_dc_keys(
            [["+", cr.BASE_EXPORTED]], {}, scope={"A", "B"})
        events = {k.split("::", 1)[0] for k in keys}
        assert events == {"A", "B"}
    finally:
        store.close()


def test_resolve_dc_keys_scope_empty_iterable_narrows_to_nothing(tmp_path):
    """An empty iterable is explicitly different from ``None``: the
    caller composed a Scope sentence and nothing resolved → the result
    is empty, not library-wide. Matches the seam's documented contract."""
    lg, store = _open_library(tmp_path)
    _seed(store)
    try:
        keys = lg.resolve_dc_keys(
            [["+", cr.BASE_EXPORTED]], {}, scope=frozenset())
        assert keys == []
    finally:
        store.close()


def test_resolve_dc_honours_scope_same_shape(tmp_path):
    """``resolve_dc`` is the unpacked-tuple variant; it shares the same
    ``scope`` contract."""
    lg, store = _open_library(tmp_path)
    _seed(store)
    try:
        out = lg.resolve_dc(
            [["+", cr.BASE_EXPORTED]], {}, scope={"A"})
        assert {e for e, _ in out} == {"A"}
    finally:
        store.close()


def test_dc_probe_honours_scope(tmp_path):
    """``dc_probe`` is the live-count probe the dialog reads on every
    state change; the scope it gets is the same Scope sentence the
    Start path will apply."""
    lg, store = _open_library(tmp_path)
    _seed(store)
    try:
        assert lg.dc_probe(
            [["+", cr.BASE_EXPORTED]], {}, scope=None) == 4
        assert lg.dc_probe(
            [["+", cr.BASE_EXPORTED]], {}, scope={"A"}) == 2
        assert lg.dc_probe(
            [["+", cr.BASE_EXPORTED]], {}, scope=frozenset()) == 0
    finally:
        store.close()
