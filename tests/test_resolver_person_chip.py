"""spec/90 §4.3 + §5.2 — Person chip resolution.

The face substrate ships empty in Phase 1; a Person chip resolves to an
EMPTY set when no detection has run yet — and that's the correct behaviour,
NOT an error (spec/90 §4.3 + Phase 2 spec §3).

A synthetic ``face`` row that ties a Person id to an item proves the
positive path: the Person chip resolves to the matching item.

Headless logic only — no Qt.
"""
from __future__ import annotations

import itertools

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.store import models as m
from mira.store.repo import EventStore

FIXED_NOW = "2026-06-20T12:00:00+00:00"


def _now() -> str:
    return FIXED_NOW


def _photo(item_id, t, classification=None):
    return m.Item(
        id=item_id, kind="photo", created_at=FIXED_NOW, provenance="captured",
        origin_relpath=f"Original Media/{item_id}.jpg", sha256="a" * 64,
        byte_size=1000, materialized_at=FIXED_NOW, materialized_phase="ingest",
        camera_id="G9", day_number=1,
        capture_time_raw=t, capture_time_corrected=t,
        classification=classification,
    )


def _doc() -> m.EventDocument:
    """Three exported photos — fixture for the empty / partial-detection
    cases below."""
    doc = m.EventDocument(event=m.Event(
        uuid="evt-p", name="Person fixture",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    doc.items = [
        _photo("p1", "2026-04-01T08:00:00"),
        _photo("p2", "2026-04-01T09:00:00"),
        _photo("p3", "2026-04-01T10:00:00"),
    ]
    doc.lineage = [
        m.Lineage(export_relpath="Exported Media/p1.jpg", phase="edit",
                  source_kind="item", source_item_id="p1", exported_at="t1"),
        m.Lineage(export_relpath="Exported Media/p2.jpg", phase="edit",
                  source_kind="item", source_item_id="p2", exported_at="t2"),
        m.Lineage(export_relpath="Exported Media/p3.jpg", phase="edit",
                  source_kind="item", source_item_id="p3", exported_at="t3"),
    ]
    return doc


@pytest.fixture
def gw(tmp_path):
    store = EventStore.create(tmp_path / "event.db", event_id="evt-p")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(store, now=_now, new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


def _seed_person_via_photo_person(gw, person_id: str) -> None:
    """Drop a ``photo_person`` row so the gateway's existence gate fires
    (the user-tagged path). No face row yet → the Person resolves to an
    empty set, lenient — but the strict-ref guard passes because the id
    is known. This is the Phase 1 "Person exists, recognition not yet run"
    state spec/90 §4.3 calls out."""
    # photo_person FKs to item, so attach to an arbitrary item.
    gw.store.upsert(m.PhotoPerson(
        item_id="p1", person_id=person_id, source="user",
        tagged_at=FIXED_NOW))


def _seed_face(gw, *, face_id: str, item_id: str, person_id: str) -> None:
    """Seed a face row tying ``item_id`` to ``person_id``. Bbox + confidence
    are valid but irrelevant for resolution — the resolver only joins on
    person_id."""
    gw.store.upsert(m.Face(
        id=face_id, item_id=item_id, person_id=person_id,
        bbox_x=0.10, bbox_y=0.20, bbox_w=0.30, bbox_h=0.40,
        confidence=0.92, detected_at=FIXED_NOW,
    ))


# --------------------------------------------------------------------------- #
# Filter-row Person ids (spec/90 §4.3)
# --------------------------------------------------------------------------- #


def test_known_person_with_empty_face_table_lenient_empty_pool(gw):
    """A Person id known via ``photo_person`` (catalog existence) but with
    no ``face`` rows resolves to an empty set in the filter step. No
    exception — the substrate is intentionally empty in Phase 1."""
    _seed_person_via_photo_person(gw, "person-1")
    result = gw.resolve_recipe({
        "source": [["+", "exported"]],
        "filters": {"person_ids": ["person-1"]},
        "otherwise": "pick",
    })
    assert result.pool == []
    assert result.seed == {}


def test_seeded_face_row_makes_person_filter_resolve(gw):
    """One ``face`` row tying ``p1`` to Pedro: filtering by Pedro returns
    the single matching exported relpath."""
    _seed_face(gw, face_id="f1", item_id="p1", person_id="person-pedro")
    result = gw.resolve_recipe({
        "source": [["+", "exported"]],
        "filters": {"person_ids": ["person-pedro"]},
        "otherwise": "pick",
    })
    assert result.pool == ["Exported Media/p1.jpg"]
    assert result.seed == {"Exported Media/p1.jpg": True}


def test_two_faces_one_person_dedupes(gw):
    """Two ``face`` rows on the same item for the same person (a side+front
    detection, say) still surface the item ONCE — the SELECT DISTINCT in
    the resolver folds the duplicates."""
    _seed_face(gw, face_id="f1", item_id="p1", person_id="person-pedro")
    _seed_face(gw, face_id="f2", item_id="p1", person_id="person-pedro")
    result = gw.resolve_recipe({
        "source": [["+", "exported"]],
        "filters": {"person_ids": ["person-pedro"]},
        "otherwise": "pick",
    })
    assert result.pool == ["Exported Media/p1.jpg"]


def test_two_person_ids_union(gw):
    """Multi-select Person filter (spec/90 §4.3) unions the per-Person
    sets. Pedro on p1, Maria on p2; selecting both surfaces both items."""
    _seed_face(gw, face_id="f1", item_id="p1", person_id="person-pedro")
    _seed_face(gw, face_id="f2", item_id="p2", person_id="person-maria")
    result = gw.resolve_recipe({
        "source": [["+", "exported"]],
        "filters": {"person_ids": ["person-pedro", "person-maria"]},
        "otherwise": "pick",
    })
    assert set(result.pool) == {
        "Exported Media/p1.jpg",
        "Exported Media/p2.jpg",
    }


def test_unrecognized_face_doesnt_attribute_to_any_person(gw):
    """A ``face`` row with ``person_id=NULL`` represents an unrecognized
    face (spec/90 §4.3). It contributes nothing to a per-person filter."""
    _seed_face(gw, face_id="f1", item_id="p1", person_id="person-pedro")
    gw.store.upsert(m.Face(
        id="f-unknown", item_id="p2", person_id=None,
        bbox_x=0.10, bbox_y=0.20, bbox_w=0.30, bbox_h=0.40,
        confidence=0.55, detected_at=FIXED_NOW,
    ))
    result = gw.resolve_recipe({
        "source": [["+", "exported"]],
        "filters": {"person_ids": ["person-pedro"]},
        "otherwise": "pick",
    })
    assert result.pool == ["Exported Media/p1.jpg"]


# --------------------------------------------------------------------------- #
# Person operand inside rule predicates (spec/90 §4.3 advanced)
# --------------------------------------------------------------------------- #


def test_person_chip_inside_rule_predicate_resolves(gw):
    """spec/90 §4.3 — Person chips can appear in rule predicates. The
    resolver dispatches via the ``extra_operand`` hook on
    :mod:`core.collection_resolver`."""
    _seed_face(gw, face_id="f1", item_id="p1", person_id="person-pedro")
    result = gw.resolve_recipe({
        "source": [["+", "exported"]],
        "rules": [
            {
                "predicate": [["+", {"kind": "person", "id": "person-pedro"}]],
                "verdict": "pick",
            }
        ],
        "otherwise": "skip",
    })
    # p1 has Pedro → pick; p2, p3 have nothing → skip (Otherwise).
    assert result.seed["Exported Media/p1.jpg"] is True
    assert result.seed["Exported Media/p2.jpg"] is False
    assert result.seed["Exported Media/p3.jpg"] is False


def test_person_chip_predicate_with_empty_face_table_yields_no_match(gw):
    """A Person referenced via the catalog (photo_person) but with no
    face rows: the predicate set is empty, so no item matches the rule
    — everything falls through to Otherwise. No exception."""
    _seed_person_via_photo_person(gw, "person-empty")
    result = gw.resolve_recipe({
        "source": [["+", "exported"]],
        "rules": [
            {
                "predicate": [["+", {"kind": "person", "id": "person-empty"}]],
                "verdict": "pick",
            }
        ],
        "otherwise": "skip",
    })
    # Pool intact; everything got the Otherwise verdict.
    assert len(result.pool) == 3
    assert all(picked is False for picked in result.seed.values())
