"""spec/90 Phase 1 — Face round-trip + schema invariants.

Logic-only (no Qt). A ``face`` row (spec/90 §5.2) is one detected face on an
item, with a normalised bounding box and a nullable pointer to the library
``person`` table. The table stays empty in Phase 1 (the detection pipeline is
a separate sprint, spec/90 §7 Phase 6); the substrate exists so Person chips
in a Recipe resolve to "no matches" leniently before recognition ships.

This exercises:

* every column survives save_document → load_document;
* the ``json_dump`` backup path round-trips faces alongside photo_persons;
* the bbox ``0..1`` CHECKs reject out-of-range coordinates;
* ``ON DELETE CASCADE`` from the item drops the face too (the parent
  triangle from spec/30 §3).
"""
from __future__ import annotations

import sqlite3

import pytest

from mira.store import json_dump, models as m
from mira.store.repo import EventStore


NOW = "2026-06-20T12:00:00+00:00"


def _make_store(tmp_path) -> EventStore:
    return EventStore.create(
        tmp_path / "event.db",
        event_id="evt-90",
        app_version="test",
        created_at=NOW,
    )


def _photo_item(item_id: str = "p1") -> m.Item:
    return m.Item(
        id=item_id, kind="photo", created_at=NOW, provenance="captured",
        origin_relpath=f"Original Media/{item_id}.jpg", sha256="a" * 64,
        byte_size=1000, materialized_at=NOW, materialized_phase="ingest",
        camera_id="G9", day_number=1,
        capture_time_raw="2026-04-01T08:00:00",
        capture_time_corrected="2026-04-01T08:00:00",
    )


def _doc_with_faces() -> m.EventDocument:
    """A minimal EventDocument carrying two faces — one recognized (linked to
    a library Person id), one unrecognized (person_id NULL)."""
    doc = m.EventDocument(event=m.Event(
        uuid="evt-90", name="Face fixture",
        created_at=NOW, updated_at=NOW,
    ))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    doc.items = [_photo_item("p1"), _photo_item("p2")]
    doc.faces = [
        m.Face(
            id="face-recognized", item_id="p1",
            person_id="person-pedro",
            bbox_x=0.10, bbox_y=0.20, bbox_w=0.30, bbox_h=0.40,
            confidence=0.92, detected_at=NOW,
        ),
        m.Face(
            id="face-unknown", item_id="p2",
            person_id=None,           # unrecognized — spec/90 §4.3
            bbox_x=0.50, bbox_y=0.55, bbox_w=0.20, bbox_h=0.25,
            confidence=0.81, detected_at=NOW,
        ),
    ]
    return doc


# --------------------------------------------------------------------------- #
# Round-trips
# --------------------------------------------------------------------------- #


def test_face_save_load_document_roundtrip(tmp_path):
    """save_document → load_document preserves every Face column."""
    store = _make_store(tmp_path)
    store.save_document(_doc_with_faces())
    loaded = store.load_document()
    by_id = {f.id: f for f in loaded.faces}
    assert set(by_id) == {"face-recognized", "face-unknown"}

    recognized = by_id["face-recognized"]
    assert recognized.item_id == "p1"
    assert recognized.person_id == "person-pedro"
    assert recognized.bbox_x == 0.10 and recognized.bbox_y == 0.20
    assert recognized.bbox_w == 0.30 and recognized.bbox_h == 0.40
    assert recognized.confidence == 0.92
    assert recognized.detected_at == NOW

    unknown = by_id["face-unknown"]
    assert unknown.person_id is None
    assert unknown.bbox_x == 0.50
    store.close()


def test_face_query_by_item_id(tmp_path):
    """``query_by(item_id=…)`` uses the indexed item column — the Person chip
    resolver's hot path."""
    store = _make_store(tmp_path)
    store.save_document(_doc_with_faces())
    on_p1 = store.query_by(m.Face, item_id="p1")
    assert [f.id for f in on_p1] == ["face-recognized"]
    on_p2 = store.query_by(m.Face, item_id="p2")
    assert [f.id for f in on_p2] == ["face-unknown"]
    store.close()


def test_face_query_by_person_id_finds_recognized_only(tmp_path):
    """``query_by(person_id=…)`` returns only recognized faces — the NULL
    person_ids stay out (spec/90 §4.3's #unrecognized_faces operand has its
    own resolver path)."""
    store = _make_store(tmp_path)
    store.save_document(_doc_with_faces())
    pedro = store.query_by(m.Face, person_id="person-pedro")
    assert [f.id for f in pedro] == ["face-recognized"]
    assert store.query_by(m.Face, person_id="person-unknown-id") == []
    store.close()


def test_face_round_trips_through_json_backup(tmp_path):
    """Faces survive the ``event.json`` backup intermediate so a JSON
    restore preserves them alongside photo_persons."""
    doc = _doc_with_faces()
    data = json_dump.to_json(doc)
    assert "faces" in data
    assert len(data["faces"]) == 2
    rebuilt = json_dump.from_json(data)
    assert {f.id for f in rebuilt.faces} == {"face-recognized", "face-unknown"}
    # Equality is column-for-column; the fixture should round-trip exactly.
    assert rebuilt == doc


# --------------------------------------------------------------------------- #
# CHECK + cascade invariants
# --------------------------------------------------------------------------- #


def test_face_bbox_check_rejects_out_of_range(tmp_path):
    """The normalised-bbox CHECKs reject coordinates outside 0..1."""
    store = _make_store(tmp_path)
    store.save_document(_doc_with_faces())
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO face (id, item_id, bbox_x, detected_at) "
            "VALUES ('f-bad', 'p1', 1.5, ?)", (NOW,)
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO face (id, item_id, bbox_w, detected_at) "
            "VALUES ('f-bad', 'p1', -0.1, ?)", (NOW,)
        )
    store.close()


def test_face_cascades_when_item_deleted(tmp_path):
    """Deleting an item drops its faces — ``ON DELETE CASCADE`` (same
    discipline as photo_person)."""
    store = _make_store(tmp_path)
    store.save_document(_doc_with_faces())
    with store.transaction() as conn:
        conn.execute("DELETE FROM item WHERE id = 'p1'")
    remaining = {f.id for f in store.all(m.Face)}
    assert remaining == {"face-unknown"}
    store.close()


def test_face_table_starts_empty_on_fresh_db(tmp_path):
    """The substrate ships empty (spec/90 §7 Phase 1 — no backfill)."""
    store = _make_store(tmp_path)
    assert store.all(m.Face) == []
    store.close()


# --------------------------------------------------------------------------- #
# Migration v11 -> v12
# --------------------------------------------------------------------------- #


def test_migrate_v11_to_v12_adds_face_table(tmp_path):
    """v11→v12 (spec/90 Phase 1) creates ``face`` as a NEW table — full
    CHECK + index complement intact. Pre-existing rows are untouched."""
    from mira.store import schema

    store = _make_store(tmp_path)
    conn = store.conn
    conn.execute("DROP INDEX IF EXISTS ix_face_person")
    conn.execute("DROP INDEX IF EXISTS ix_face_item")
    conn.execute("DROP TABLE face")
    # spec/94 Phase 1 added v12→v13; drop the recipe table the up-migration
    # will recreate so the test's path from v11 doesn't collide on the way
    # back up to SCHEMA_VERSION.
    conn.execute("DROP TABLE IF EXISTS recipe")
    # spec/109 added v13→v14 stack_bracket.producer; strip the column so
    # the up-migration's ALTER ADD doesn't collide on the way back up.
    conn.execute("ALTER TABLE stack_bracket DROP COLUMN producer")
    conn.execute("UPDATE schema_info SET schema_version = 11 WHERE id = 1")

    schema.migrate(conn)

    assert schema.get_version(conn) == schema.SCHEMA_VERSION
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "face" in names
    # CHECK survived: out-of-range bbox is rejected on the migrated table.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO face (id, item_id, bbox_x, detected_at) "
            "VALUES ('f-bad', 'p1', 1.5, ?)", (NOW,))
    # Indexes landed.
    idx = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='face'")}
    assert {"ix_face_item", "ix_face_person"} <= idx
    store.close()
