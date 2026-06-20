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
