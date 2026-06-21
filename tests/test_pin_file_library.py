"""spec/94 Phase 2 — pin a Cut sourced from a file-based Collection.

The dialog → adapter → :class:`CutSession.from_draft` → ``commit`` path
already exists; Phase 2 makes it work when the Source is a global
Collection living as a JSON file under ``<library_root>/Collections/``.

Pin contract (spec/81 §3 + §5):

* ``Cut.expr_snapshot_json`` is the formula frozen verbatim at pin time.
* ``Cut.source_dc_id`` is the source Collection's id.
* ``Cut.source_dc_kind`` is ``'event'`` (bound DC) or ``'user'`` (file
  library) — auto-inferred by :meth:`EventGateway.create_cut` when the
  caller doesn't pass one.
* ``cut_member`` rows are the resolved set frozen at pin time; the Cut
  lives in event.db (event-scope, Phase 2 boundary).
"""
from __future__ import annotations

import itertools
import json
from typing import Dict, Tuple

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.shared.cut_draft import (
    CutDraft,
    PIN_PICK_IN,
    PIN_WEED_OUT,
)
from mira.shared.cut_session import CutSession
from mira.store import models as m
from mira.store.repo import EventStore

from tests.test_gateway_cuts import _doc, _now


def _payload(expr, filters=None):
    return {"expr": list(expr), "filters": dict(filters or {})}


def _make_factory(by_id, by_name=None):
    by_name = by_name or {}
    state = {"calls": 0}

    def _factory() -> Tuple[Dict[str, dict], Dict[str, dict]]:
        state["calls"] += 1
        return dict(by_id), dict(by_name)

    return _factory, state


def _draft(**over) -> CutDraft:
    """Mirrors tests.test_cut_session._draft so the fixture parity holds."""
    kw = dict(
        name="My Cut", tag="my_cut",
        expr=(),
        styles=(), media_type="both",
        pin_mode=PIN_WEED_OUT,
        target_s=600, max_s=720, photo_s=6.0,
        music_category="happy",
    )
    kw.update(over)
    return CutDraft(**kw)


@pytest.fixture
def gw_with_library(tmp_path):
    """EventGateway whose library factory exposes a ``wildlife``
    Collection ≡ ``#exported − short_version Cut`` → {e2,e3a,e3b,v1}."""
    store = EventStore.create(tmp_path / "event.db", event_id="evt-c")
    store.save_document(_doc())
    counter = itertools.count(1)
    by_id = {
        "wildlife-id": _payload(
            [["+", "exported"],
             ["-", {"kind": "cut", "tag": "short_version"}]],
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


# ── Inline expr from the adapter (the dialog → draft path) ──────


def test_pin_via_inline_expr_from_library_collection(gw_with_library):
    """The dialog adapter ships ``source_dc_id`` AND the inline ``expr``
    derived from the loaded Collection. The session resolves to the
    library's members and commit writes Cut + CutMember rows."""
    gw, _state = gw_with_library
    draft = _draft(
        source_dc_id="wildlife-id",
        expr=(("+", {"kind": "dc", "id": "wildlife-id"}),),
        pin_mode=PIN_WEED_OUT,
    )
    session = CutSession.from_draft(gw, draft)
    cut = session.commit(gw)

    # Cut row landed in event.db.
    fetched = gw.cut(cut.id)
    assert fetched is not None
    assert fetched.tag == "my_cut"
    # Source link by {id, name} resolved by id (spec/93 §4).
    assert fetched.source_dc_id == "wildlife-id"
    # Auto-inferred kind: the id isn't in event.db; it's in the library.
    assert fetched.source_dc_kind == "user"
    # The frozen formula is exactly the draft's expr.
    assert json.loads(fetched.expr_snapshot_json) == [
        ["+", {"kind": "dc", "id": "wildlife-id"}],
    ]
    # CutMember rows match the library Collection's resolution.
    rels = [r.export_relpath for r in gw.cut_member_files(cut.id)]
    assert rels == [
        "Exported Media/e2.jpg", "Exported Media/e3a.jpg",
        "Exported Media/e3b.jpg", "Exported Media/v1.mp4",
    ]


# ── Draft with only source_dc_id (no inline expr) ──────────────


def test_draft_fallback_resolves_via_library(gw_with_library):
    """A draft that ships ONLY ``source_dc_id`` (no inline expr) makes
    ``CutSession._draft_expr_filters`` fall through to the library
    lookup — the existing event.db-only path was the legacy gap Phase 2
    fixes."""
    gw, _state = gw_with_library
    draft = _draft(
        source_dc_id="wildlife-id",
        expr=(),                                            # no inline copy
        pin_mode=PIN_WEED_OUT,
    )
    session = CutSession.from_draft(gw, draft)
    # The session resolved against the library Collection.
    assert [f.export_relpath for f in session.files] == [
        "Exported Media/e2.jpg", "Exported Media/e3a.jpg",
        "Exported Media/e3b.jpg", "Exported Media/v1.mp4",
    ]


# ── Auto-infer for bound DC (no regression) ─────────────────────


def test_pin_via_bound_dc_still_lands_event_kind(gw_with_library):
    """A Cut sourced from an event.db DC keeps ``source_dc_kind = 'event'``
    — Phase 2 is additive, no regression on the bound path."""
    gw, _state = gw_with_library
    bound = gw.create_dc("birds", expr=[["+", "exported"]])
    draft = _draft(
        source_dc_id=bound.id,
        expr=(("+", "exported"),),
        pin_mode=PIN_WEED_OUT,
    )
    session = CutSession.from_draft(gw, draft)
    cut = session.commit(gw)

    fetched = gw.cut(cut.id)
    assert fetched.source_dc_id == bound.id
    assert fetched.source_dc_kind == "event"


def test_ad_hoc_pin_has_no_source_link(gw_with_library):
    """Ad-hoc Cut (Source = bare ``#exported``, no Collection picked) has
    no source link: ``source_dc_id = None`` + ``source_dc_kind = None``."""
    gw, _state = gw_with_library
    draft = _draft(
        source_dc_id=None,
        expr=(("+", "exported"),),
        pin_mode=PIN_WEED_OUT,
    )
    session = CutSession.from_draft(gw, draft)
    cut = session.commit(gw)
    fetched = gw.cut(cut.id)
    assert fetched.source_dc_id is None
    assert fetched.source_dc_kind is None


# ── Snapshot is frozen verbatim ────────────────────────────────


def test_snapshot_frozen_from_draft_expr(gw_with_library):
    """``expr_snapshot_json`` is the draft's ``expr`` literally, JSON-
    serialized. The dialog's adapter feeds the same Collection ref the
    user composed; the Cut records that ref."""
    gw, _state = gw_with_library
    draft = _draft(
        source_dc_id="wildlife-id",
        expr=(
            ("+", "exported"),
            ("-", {"kind": "dc", "id": "wildlife-id"}),
        ),
        pin_mode=PIN_PICK_IN,
    )
    session = CutSession.from_draft(gw, draft)
    cut = session.commit(gw)
    fetched = gw.cut(cut.id)
    assert json.loads(fetched.expr_snapshot_json) == [
        ["+", "exported"],
        ["-", {"kind": "dc", "id": "wildlife-id"}],
    ]


# ── Cut lives in event.db (event-scope) ─────────────────────────


def test_cut_lives_in_event_db(gw_with_library):
    """spec/94 Phase 2 boundary — event-scope first. The pinned Cut is
    a row in ``event.db.cut`` (not in mira.db or in a JSON file)."""
    gw, _state = gw_with_library
    draft = _draft(
        source_dc_id="wildlife-id",
        expr=(("+", {"kind": "dc", "id": "wildlife-id"}),),
    )
    cut = CutSession.from_draft(gw, draft).commit(gw)
    rows = gw.store.query_raw(
        m.Cut, "SELECT * FROM cut WHERE id = ?", (cut.id,))
    assert len(rows) == 1
    assert rows[0].id == cut.id
