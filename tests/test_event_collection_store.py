"""spec/90 Phase 1 — EventCollection round-trip + schema invariants.

Logic-only (no Qt). An Event Collection (spec/90 §5.3) is the cross-event
analogue of a Dynamic Collection — same set-algebra shape, but the universe
is events instead of items. Phase 1 is substrate only — no resolver, no
dialog — so this exercises storage shape only:

* every dataclass field round-trips through upsert → get / all;
* the ``tag`` UNIQUE + COLLATE NOCASE + non-empty CHECK match the existing
  ``dynamic_collection`` / ``saved_filter`` pattern;
* ``expr_json`` / ``filters_json`` ``json_valid`` CHECKs reject malformed input.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from mira.user_store import models as m
from mira.user_store.repo import UserStore


NOW = "2026-06-20T12:00:00+00:00"


def _make_store(tmp_path) -> UserStore:
    return UserStore.create(
        tmp_path / "mira.db",
        app_version="test",
        created_at=NOW,
    )


# --------------------------------------------------------------------------- #
# Round-trips
# --------------------------------------------------------------------------- #


def test_event_collection_roundtrip(tmp_path):
    """Every column round-trips. The expression admits typed-ref operands
    (events by uuid, other Event Collections) and base tokens — the
    spec/81 §2 set-algebra envelope, reused here."""
    store = _make_store(tmp_path)
    expr = [
        ["+", {"kind": "event", "uuid": "evt-alaska"}],
        ["+", {"kind": "event", "uuid": "evt-costa-rica"}],
        ["-", {"kind": "event_collection", "tag": "weekend_trips"}],
    ]
    filters = {"date_range": {"start": "2024-01-01", "end": "2026-12-31"}}
    store.upsert(m.EventCollection(
        id="ec-1",
        tag="adventure_events",
        expr_json=json.dumps(expr),
        filters_json=json.dumps(filters),
        created_at=NOW,
        updated_at=NOW,
    ))
    got = store.get(m.EventCollection, "ec-1")
    assert got is not None
    assert got.tag == "adventure_events"
    assert json.loads(got.expr_json) == expr
    assert json.loads(got.filters_json) == filters
    assert got.extras_json == '{}'
    store.close()


def test_event_collection_query_by_tag(tmp_path):
    """``query_by(tag=…)`` keys on the UNIQUE column for the gateway's
    lookup-by-name path."""
    store = _make_store(tmp_path)
    for tag in ("adventure_events", "wildlife_trips", "2018_2020_travel"):
        store.upsert(m.EventCollection(
            id=f"ec-{tag}", tag=tag,
            expr_json='[]',
            created_at=NOW, updated_at=NOW,
        ))
    rows = store.query_by(m.EventCollection, tag="wildlife_trips")
    assert [r.tag for r in rows] == ["wildlife_trips"]
    assert len(store.all(m.EventCollection)) == 3
    store.close()


# --------------------------------------------------------------------------- #
# Tag CHECK + UNIQUE
# --------------------------------------------------------------------------- #


def test_event_collection_tag_is_unique_case_blind(tmp_path):
    """``tag`` carries ``COLLATE NOCASE UNIQUE`` — same as ``dynamic_collection``
    / ``saved_filter``. The cross-event Recipe grammar's named-operand
    resolution is case-blind."""
    store = _make_store(tmp_path)
    store.upsert(m.EventCollection(
        id="ec-1", tag="adventure_events",
        expr_json='[]',
        created_at=NOW, updated_at=NOW,
    ))
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert(m.EventCollection(
            id="ec-2", tag="ADVENTURE_EVENTS",
            expr_json='[]',
            created_at=NOW, updated_at=NOW,
        ))
    store.close()


def test_event_collection_empty_tag_rejected(tmp_path):
    """``tag <> ''`` CHECK — same shape as the other tagged nouns."""
    store = _make_store(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert(m.EventCollection(
            id="ec-1", tag="",
            expr_json='[]',
            created_at=NOW, updated_at=NOW,
        ))
    store.close()


def test_event_collection_expr_json_must_be_valid_json(tmp_path):
    """The ``expr_json`` ``json_valid`` CHECK keeps malformed input out."""
    store = _make_store(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO event_collection (id, tag, expr_json, "
            "filters_json, created_at, updated_at) "
            "VALUES ('ec-1', 'X', '[not json', '{}', 't', 't')"
        )
    store.close()


def test_event_collection_filters_json_must_be_valid_json(tmp_path):
    """The ``filters_json`` ``json_valid`` CHECK applies symmetrically."""
    store = _make_store(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO event_collection (id, tag, expr_json, "
            "filters_json, created_at, updated_at) "
            "VALUES ('ec-1', 'X', '[]', '{not json', 't', 't')"
        )
    store.close()


# --------------------------------------------------------------------------- #
# EventCollectionStore CRUD service (spec/90 §7 Phase 4b)
# --------------------------------------------------------------------------- #


def _ec_service(tmp_path):
    """Build an EventCollectionStore over a fresh user-store with a
    deterministic now() and id counter."""
    import itertools as _it
    from mira.shared.event_collection_store import EventCollectionStore
    counter = _it.count(1)
    user_store = _make_store(tmp_path)
    svc = EventCollectionStore(
        user_store,
        now=lambda: NOW,
        new_id=lambda: f"ec-{next(counter):03d}",
    )
    return svc, user_store


_BASIC_EXPR = [["+", {"kind": "event", "uuid": "evt-alaska"}]]


def test_event_collection_store_create_persists_row(tmp_path):
    svc, us = _ec_service(tmp_path)
    try:
        ec = svc.create("adventure_events", _BASIC_EXPR)
        assert ec.id == "ec-001"
        assert ec.tag == "adventure_events"
        assert ec.created_at == NOW and ec.updated_at == NOW
        # The expr decodes back to the list the caller passed.
        assert svc.expr(ec) == _BASIC_EXPR
        # And persistence round-trips.
        same = svc.get(ec.id)
        assert same is not None and same.tag == "adventure_events"
    finally:
        us.close()


def test_event_collection_store_create_with_filters_dict(tmp_path):
    """``filters`` is an optional dict; the store JSON-encodes it for the
    user. The 2026-06-20 spec/90 §5.3 design has only a ``date_range``
    family today but the encoder takes any dict shape."""
    svc, us = _ec_service(tmp_path)
    try:
        ec = svc.create("adventure_events", _BASIC_EXPR,
                        filters={"date_range": {"start": "2018-01-01",
                                                "end": "2020-12-31"}})
        assert svc.filters(ec) == {
            "date_range": {"start": "2018-01-01", "end": "2020-12-31"}}
    finally:
        us.close()


def test_event_collection_store_create_no_filters_yields_empty_dict(tmp_path):
    svc, us = _ec_service(tmp_path)
    try:
        ec = svc.create("adventure_events", _BASIC_EXPR)
        assert svc.filters(ec) == {}
    finally:
        us.close()


def test_event_collection_store_create_rejects_empty_tag(tmp_path):
    svc, us = _ec_service(tmp_path)
    try:
        with pytest.raises(ValueError, match="non-empty"):
            svc.create("", _BASIC_EXPR)
        with pytest.raises(ValueError, match="non-empty"):
            svc.create("   ", _BASIC_EXPR)
    finally:
        us.close()


def test_event_collection_store_create_strips_whitespace(tmp_path):
    svc, us = _ec_service(tmp_path)
    try:
        ec = svc.create("  adventure_events  ", _BASIC_EXPR)
        assert ec.tag == "adventure_events"
    finally:
        us.close()


def test_event_collection_store_create_typed_error_on_collision(tmp_path):
    """``tag`` is ``COLLATE NOCASE UNIQUE`` — the store surfaces a typed
    :class:`EventCollectionTagTakenError` instead of a raw IntegrityError."""
    from mira.shared.event_collection_store import EventCollectionTagTakenError
    svc, us = _ec_service(tmp_path)
    try:
        svc.create("wildlife", _BASIC_EXPR)
        with pytest.raises(EventCollectionTagTakenError) as exc:
            svc.create("wildlife", _BASIC_EXPR)
        assert exc.value.tag == "wildlife"
    finally:
        us.close()


def test_event_collection_store_create_collision_is_case_blind(tmp_path):
    """The DDL's ``COLLATE NOCASE`` means ``WILDLIFE`` collides with
    ``wildlife`` — the dialog can show one canonical name."""
    from mira.shared.event_collection_store import EventCollectionTagTakenError
    svc, us = _ec_service(tmp_path)
    try:
        svc.create("wildlife", _BASIC_EXPR)
        with pytest.raises(EventCollectionTagTakenError):
            svc.create("WILDLIFE", _BASIC_EXPR)
    finally:
        us.close()


def test_event_collection_store_update_partial_touches_updated_at(tmp_path):
    """``updated_at`` advances; ``created_at`` stays put."""
    svc, us = _ec_service(tmp_path)
    try:
        ec = svc.create("adventure_events", _BASIC_EXPR)
        original_created = ec.created_at

        # Move the clock and update.
        svc._now = lambda: "2026-06-21T08:00:00+00:00"
        next_expr = [["+", {"kind": "event", "uuid": "evt-bali"}]]
        svc.update(ec.id, expr=next_expr)

        refreshed = svc.get(ec.id)
        assert refreshed.created_at == original_created
        assert refreshed.updated_at == "2026-06-21T08:00:00+00:00"
        assert svc.expr(refreshed) == next_expr
    finally:
        us.close()


def test_event_collection_store_update_rename_works(tmp_path):
    svc, us = _ec_service(tmp_path)
    try:
        ec = svc.create("wildlife", _BASIC_EXPR)
        svc.update(ec.id, tag="wildlife_trips")
        refreshed = svc.get(ec.id)
        assert refreshed.tag == "wildlife_trips"
        assert svc.by_tag("wildlife") is None
        assert svc.by_tag("wildlife_trips") is not None
    finally:
        us.close()


def test_event_collection_store_update_rename_collision_raises(tmp_path):
    from mira.shared.event_collection_store import EventCollectionTagTakenError
    svc, us = _ec_service(tmp_path)
    try:
        svc.create("wildlife", _BASIC_EXPR)
        other = svc.create("travel", _BASIC_EXPR)
        with pytest.raises(EventCollectionTagTakenError):
            svc.update(other.id, tag="wildlife")
    finally:
        us.close()


def test_event_collection_store_update_same_tag_is_noop(tmp_path):
    """Re-saving the same row with its own tag (or a case-different
    variant) shouldn't trip the uniqueness pre-check."""
    svc, us = _ec_service(tmp_path)
    try:
        ec = svc.create("wildlife", _BASIC_EXPR)
        # Same tag verbatim.
        svc.update(ec.id, tag="wildlife")
        # Case variant — store normalises but the row is the same id.
        svc.update(ec.id, tag="WILDLIFE")
        refreshed = svc.get(ec.id)
        assert refreshed.tag == "WILDLIFE"
    finally:
        us.close()


def test_event_collection_store_update_unknown_id_raises_keyerror(tmp_path):
    svc, us = _ec_service(tmp_path)
    try:
        with pytest.raises(KeyError):
            svc.update("ec-nope", tag="x")
    finally:
        us.close()


def test_event_collection_store_delete_is_idempotent(tmp_path):
    svc, us = _ec_service(tmp_path)
    try:
        ec = svc.create("wildlife", _BASIC_EXPR)
        svc.delete(ec.id)
        assert svc.get(ec.id) is None
        svc.delete(ec.id)                # second delete: no-op
    finally:
        us.close()


def test_event_collection_store_by_tag_is_case_blind(tmp_path):
    """The lookup matches the ``COLLATE NOCASE UNIQUE`` constraint."""
    svc, us = _ec_service(tmp_path)
    try:
        svc.create("wildlife", _BASIC_EXPR)
        assert svc.by_tag("wildlife") is not None
        assert svc.by_tag("WILDLIFE") is not None
        assert svc.by_tag("Wildlife") is not None
        assert svc.by_tag("ghost") is None
    finally:
        us.close()


def test_event_collection_store_list_sorted_by_tag(tmp_path):
    svc, us = _ec_service(tmp_path)
    try:
        svc.create("wildlife_trips", _BASIC_EXPR)
        svc.create("2018_2020_travel", _BASIC_EXPR)
        svc.create("adventure_events", _BASIC_EXPR)
        # Alphabetical by tag (the order the Scope picker renders).
        listed = [ec.tag for ec in svc.list()]
        assert listed == [
            "2018_2020_travel",
            "adventure_events",
            "wildlife_trips",
        ]
    finally:
        us.close()


def test_event_collection_store_expr_and_filters_are_tolerant(tmp_path):
    """A row with malformed JSON shouldn't crash readers — helpers fall
    back to ``[]`` / ``{}``. Matches the resolver's posture."""
    svc, us = _ec_service(tmp_path)
    try:
        ec = svc.create("wildlife", _BASIC_EXPR)
        # Force malformed-shape JSON via raw SQL (the CHECK still
        # validates JSON, so we write a well-formed but wrong-shape blob).
        with us.transaction() as conn:
            conn.execute(
                "UPDATE event_collection SET expr_json = '\"not a list\"', "
                "filters_json = '\"not a dict\"' WHERE id = ?",
                (ec.id,))
        refreshed = svc.get(ec.id)
        assert svc.expr(refreshed) == []
        assert svc.filters(refreshed) == {}
    finally:
        us.close()
