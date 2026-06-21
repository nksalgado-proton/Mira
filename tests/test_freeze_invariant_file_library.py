"""spec/94 Phase 2 — the freeze invariant (spec/81 §5) holds when the
source Collection lives in the file-based library.

Pin freezes the Cut's formula (``expr_snapshot_json``) and its members.
Subsequent mutations of the source — editing the Collection's expr,
renaming the JSON file (display name change only — the id is
unchanged), or deleting the file — must NEVER alter the Cut. The
``source_dc_id`` link may dangle after a delete, which is correct;
the snapshot + members are the authoritative record.

Plus a small parity test on event.db-backed sources to confirm the
existing freeze contract still holds after the Phase 2 changes.
"""
from __future__ import annotations

import itertools
import json
from typing import Dict, Tuple

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.shared.cut_draft import CutDraft, PIN_WEED_OUT
from mira.shared.cut_session import CutSession
from mira.store.repo import EventStore

from tests.test_gateway_cuts import _doc, _now


def _payload(expr, filters=None):
    return {"expr": list(expr), "filters": dict(filters or {})}


def _draft(**over) -> CutDraft:
    kw = dict(
        name="Frozen Cut", tag="frozen_cut",
        expr=(),
        styles=(), media_type="both",
        pin_mode=PIN_WEED_OUT,
        target_s=600, max_s=720, photo_s=6.0,
        music_category="happy",
    )
    kw.update(over)
    return CutDraft(**kw)


def _make_mutable_factory(by_id, by_name):
    """Build a library factory that re-reads its backing dicts on each
    call. Tests mutate the dicts to simulate external edits between
    EventGateway sessions."""
    state = {"calls": 0}

    def _factory() -> Tuple[Dict[str, dict], Dict[str, dict]]:
        state["calls"] += 1
        return dict(by_id), dict(by_name)

    return _factory, state


def _build_gateway(tmp_path, *, by_id, by_name):
    """Open a fresh EventGateway against the fixture event.db. Each
    open_event() lifetime caches the library snapshot independently."""
    store = EventStore.open(tmp_path / "event.db")
    counter = itertools.count(1000)
    factory, state = _make_mutable_factory(by_id, by_name)
    g = EventGateway(
        store, now=_now, new_id=lambda: f"id-{next(counter)}",
        collections_library_factory=factory,
    )
    return g, state


@pytest.fixture
def event_path(tmp_path):
    """A pristine event.db with the cuts fixture document loaded — the
    tests open and close EventGateways against it freely."""
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    store.close()
    return tmp_path / "event.db"


def _open_gw(event_path, *, by_id, by_name):
    store = EventStore.open(event_path)
    counter = itertools.count(1000)
    factory, state = _make_mutable_factory(by_id, by_name)
    g = EventGateway(
        store, now=_now, new_id=lambda: f"id-{next(counter)}",
        collections_library_factory=factory,
    )
    return g, state


def _pin_from_library(gw, dc_id="wildlife-id"):
    """Pin a fresh Cut sourced from a library Collection. Returns the
    Cut row."""
    draft = _draft(
        source_dc_id=dc_id,
        expr=(("+", {"kind": "dc", "id": dc_id}),),
        pin_mode=PIN_WEED_OUT,
    )
    return CutSession.from_draft(gw, draft).commit(gw)


def _snapshot_cut(gw, cut_id):
    """Return (expr_snapshot_json, sorted member relpaths) for a Cut."""
    cut = gw.cut(cut_id)
    members = [r.export_relpath for r in gw.cut_member_files(cut_id)]
    return cut.expr_snapshot_json, sorted(members), cut.source_dc_id, cut.source_dc_kind


# ── Edit the source Collection — Cut unchanged ────────────────


def test_edit_library_collection_does_not_alter_cut(event_path):
    """Mutating the library Collection's expression after pin doesn't
    propagate to the frozen Cut (spec/81 §5)."""
    # Session 1: pin a Cut.
    by_id = {"wildlife-id": _payload(
        [["+", "exported"],
         ["-", {"kind": "cut", "tag": "short_version"}]])}
    by_name = {"wildlife": by_id["wildlife-id"]}
    g, _ = _open_gw(event_path, by_id=by_id, by_name=by_name)
    try:
        cut = _pin_from_library(g)
        before = _snapshot_cut(g, cut.id)
    finally:
        g.close()

    # Edit the Collection's expression (cross-session — the dialog
    # surface persists the new expr to disk; we simulate by mutating
    # the dict the factory reads from).
    by_id["wildlife-id"] = _payload([["+", "exported"]])
    by_name["wildlife"] = by_id["wildlife-id"]

    g2, _ = _open_gw(event_path, by_id=by_id, by_name=by_name)
    try:
        after = _snapshot_cut(g2, cut.id)
        assert before == after
    finally:
        g2.close()


# ── Rename the library file — id stays, Cut unchanged ───────────


def test_rename_library_file_preserves_cut_and_id_resolves(event_path):
    """Rename in the file manager touches the filename + display name
    only — the stable id is unchanged. The Cut's frozen state is
    unchanged AND the source_dc_id still resolves by id."""
    by_id = {"wildlife-id": _payload(
        [["+", "exported"],
         ["-", {"kind": "cut", "tag": "short_version"}]])}
    by_name = {"wildlife": by_id["wildlife-id"]}
    g, _ = _open_gw(event_path, by_id=by_id, by_name=by_name)
    try:
        cut = _pin_from_library(g)
        before = _snapshot_cut(g, cut.id)
    finally:
        g.close()

    # OS rename — display name changes, id unchanged.
    payload = by_name.pop("wildlife")
    by_name["best_wildlife"] = payload
    # The factory rebuilds (by_id, by_name) on next call; by_id keeps
    # the same id pointing at the same payload.

    g2, _ = _open_gw(event_path, by_id=by_id, by_name=by_name)
    try:
        after = _snapshot_cut(g2, cut.id)
        assert before == after
        # The id still resolves directly — the Cut's source link is
        # alive (no name fallback needed because id is the load-bearing
        # key, spec/93 §4).
        ref = g2._resolve_library_collection({
            "kind": "dc", "id": "wildlife-id",
        })
        assert ref is not None
        assert ref.expr == by_id["wildlife-id"]["expr"]
    finally:
        g2.close()


# ── Hand-authored file (no id) — name fallback resolves ────────


def test_hand_authored_name_fallback_for_dangling_id(event_path):
    """A Cut's source_dc_id can dangle (e.g. the user deleted the
    original JSON file and recreated one with the same display name).
    Resolution then falls back to the name — spec/93 §4 contract."""
    by_id = {}
    by_name = {"wildlife": _payload(
        [["+", "exported"],
         ["-", {"kind": "cut", "tag": "short_version"}]])}
    g, _ = _open_gw(event_path, by_id=by_id, by_name=by_name)
    try:
        ref = g._resolve_library_collection({
            "kind": "dc", "id": "stale-id-from-old-file",
            "tag": "wildlife",
        })
        assert ref is not None
        assert ref.expr == by_name["wildlife"]["expr"]
    finally:
        g.close()


# ── Delete the library file — Cut survives, link may dangle ────


def test_delete_library_file_leaves_cut_intact(event_path):
    """Deleting the source Collection's file doesn't touch the Cut. The
    ``source_dc_id`` link may now dangle (a future Cut detail surface
    can show a "missing source" badge); the Cut's members + snapshot
    are the source of truth."""
    by_id = {"wildlife-id": _payload(
        [["+", "exported"],
         ["-", {"kind": "cut", "tag": "short_version"}]])}
    by_name = {"wildlife": by_id["wildlife-id"]}
    g, _ = _open_gw(event_path, by_id=by_id, by_name=by_name)
    try:
        cut = _pin_from_library(g)
        before = _snapshot_cut(g, cut.id)
    finally:
        g.close()

    # Delete the file.
    by_id.clear()
    by_name.clear()

    g2, _ = _open_gw(event_path, by_id=by_id, by_name=by_name)
    try:
        # The Cut row still carries the dangling id; the snapshot +
        # members are unchanged.
        after = _snapshot_cut(g2, cut.id)
        assert before == after
        # And the resolver can't find it (graceful empty, spec/93 §8).
        assert g2._resolve_library_collection({
            "kind": "dc", "id": "wildlife-id"}) is None
    finally:
        g2.close()


# ── Parity: bound-DC freeze still holds (no regression) ────────


def test_bound_dc_freeze_still_holds(event_path):
    """The pre-Phase-2 freeze contract for event.db-backed Collections
    is unchanged. Mirror of
    tests.test_cut_session.test_from_saved_dc_resolves_and_freezes
    but exercised through the same fixture path as the file-library
    cases for symmetry."""
    g, _ = _open_gw(event_path, by_id={}, by_name={})
    try:
        bound = g.create_dc("birds",
                             expr=[["+", "exported"],
                                   ["-", {"kind": "cut",
                                          "tag": "short_version"}]])
        draft = _draft(
            source_dc_id=bound.id,
            expr=(("+", {"kind": "dc", "id": bound.id}),),
            pin_mode=PIN_WEED_OUT,
        )
        cut = CutSession.from_draft(g, draft).commit(g)
        before = _snapshot_cut(g, cut.id)

        # Mutate the bound DC.
        g.update_dc(bound.id, expr=[["+", "exported"]])

        after = _snapshot_cut(g, cut.id)
        assert before == after
        # Re-open to be exhaustive — the dirty session sync hook
        # is None, so this is a clean reopen.
    finally:
        g.close()
