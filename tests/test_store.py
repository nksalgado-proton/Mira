"""Tests for the relational-core event store — spec/30 / charter §4 step G1.

Logic-only (no Qt). Covers: schema init + pragmas + version (typed ``schema_info``),
generic typed CRUD, the ``query_by`` SQL-WHERE primitive, transactions (commit +
rollback), the phase-counts query, JSON round-trip (``to_json`` ⇄ ``from_json``), and
the load-bearing gate: ``store → json → store`` equality (restore == migration ==
fixture, one reader). The fixture exercises the relational model's load-bearing shapes:
the spec/56 marker-partition video (a marker + two segment items + a snapshot, all
virtual / NULL file identity), the split adjustment / video_adjustment, calibration
pairs, and discriminated subset/lineage bases.
"""
from __future__ import annotations

import sqlite3

import pytest

from mira.store import json_dump, models as m, schema
from mira.store.repo import EventStore


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _make_store(tmp_path) -> EventStore:
    return EventStore.create(
        tmp_path / "event.db",
        event_id="evt-1",
        app_version="test",
        created_at="2026-05-30T00:00:00+00:00",
    )


def _rich_document() -> m.EventDocument:
    """An EventDocument exercising every table and every nesting path (new schema).

    spec/52 + spec/51 cleanup: participants / participant_devices / checklist /
    distribution / share_tags / subsets / subset_members / share_maps all dropped
    from the document. spec/61 (schema v3): photo_tags retired unused — cuts +
    cut_members (file-based membership → lineage) + photo_persons carry the
    Share layer.
    Event fields tags_json / notes / google_album_* / whatsapp_message dropped.
    Camera.is_reference dropped. item.tz_source enum aligned to camera_day_tz.
    """
    doc = m.EventDocument(
        event=m.Event(
            uuid="evt-1",
            name="Costa Rica 2026",
            created_at="2026-05-30T00:00:00+00:00",
            updated_at="2026-05-30T00:00:00+00:00",
            start_date="2026-04-01",
            end_date="2026-04-14",
            is_closed=False,
            event_type="trip",
            description="ñ accents é preserved",
            budget_short_target_s=300, budget_short_max_s=420,
            budget_long_target_s=1200, budget_long_max_s=1800, budget_video_share=0.4,
        ),
        trip_days=[
            m.TripDay(day_number=1, date="2026-04-01", description="Arenal", location="La Fortuna", tz_minutes=-360),
            m.TripDay(day_number=2, date="2026-04-02"),
        ],
        cameras=[
            m.Camera(camera_id="G9M2", configured_tz_seconds=-180 * 60, applied_offset_seconds=-540 * 60),
            m.Camera(camera_id="iPhone", is_phone=True),
        ],
    )

    # captured photo with phase states, adjustment (promoted crop columns),
    # photo tags (M:N — item in multiple Cuts), and a photo-person link.
    doc.items.append(m.Item(
        id="i-photo", kind="photo", created_at="2026-05-30T00:00:00+00:00", provenance="captured",
        origin_relpath="00 - Captured/Day01/P1000001.RW2", sha256="a" * 64, byte_size=25_000_000,
        materialized_at="2026-05-30T00:00:00+00:00", materialized_phase="ingest",
        camera_id="G9M2", day_number=1,
        capture_time_raw="2026-04-01T08:00:00", capture_time_corrected="2026-04-01T02:00:00",
        tz_offset_seconds=-360 * 60, tz_source="pair_picker",
        classification="wildlife", classification_source="auto", classification_rules_version="2026.1",
        sharpness_score=0.82, sharpness_metric="laplacian",
    ))
    # Post-Slice-0 (2026-06-06): cull + select collapsed into one 'pick' phase.
    # The original fixture had two phase_states for i-photo (phase='cull', 'kept';
    # phase='pick', 'candidate'); after collapse they would PK-collide. Keeping
    # the picked one (the original cull→kept that carried forward through Select).
    doc.phase_states += [
        m.PhaseState(item_id="i-photo", phase="pick", state="picked", decided_at="2026-04-15T00:00:00+00:00"),
    ]
    doc.adjustments.append(m.Adjustment(
        item_id="i-photo", style="wildlife", look="brighter",
        creative_filter="bw",
        crop_x=0.0, crop_y=0.0, crop_w=1.0, crop_h=1.0,
        crop_angle=1.5, rotation=90, aspect_label="3:2", edit_exported=True,
    ))
    # dynamic_collection + cut + cut_member (spec/81): the DC is the live
    # formula; the Cut is frozen and made from it (source_dc_id +
    # expr_snapshot_json). Membership is FILE-based — the member row references
    # the exported final in doc.lineage (added further down), not the item.
    doc.dynamic_collections.append(m.DynamicCollection(
        id="dc-1", tag="best_macro_shots",
        created_at="2026-04-16T00:00:00+00:00", updated_at="2026-04-16T00:00:00+00:00",
        expr_json='[["+", "exported"]]',
        filters_json='{"styles": ["macro"], "media_type": "both"}',
    ))
    doc.cuts.append(m.Cut(
        id="cut-1", tag="best_macro_shots",
        created_at="2026-04-16T00:00:00+00:00", updated_at="2026-04-16T00:00:00+00:00",
        source_dc_id="dc-1",
        expr_snapshot_json='[["+", "exported"]]',
        target_s=600, max_s=720, photo_s=6.0,
        default_state="skipped",
        music_category="happy",
        overlay_fields_json='["when", "where"]', overlay_mode="embedded",
    ))
    doc.cut_members.append(m.CutMember(
        cut_id="cut-1", export_relpath="03 - Processed/Day01/P1000001.jpg",
        added_at="2026-04-16T00:00:00+00:00",
    ))
    # photo_person — i-photo features one person from the user-level catalog
    doc.photo_persons.append(m.PhotoPerson(
        item_id="i-photo", person_id="person-1", source="user",
        tagged_at="2026-04-16T00:00:00+00:00",
    ))

    # captured video
    doc.items.append(m.Item(
        id="i-video", kind="video", created_at="2026-05-30T00:00:00+00:00", provenance="captured",
        origin_relpath="00 - Captured/Day02/P1000123.MP4", sha256="b" * 64, byte_size=500_000_000,
        materialized_at="2026-05-30T00:00:00+00:00", materialized_phase="ingest",
        camera_id="G9M2", day_number=2,
        capture_time_raw="2026-04-02T09:00:00", capture_time_corrected="2026-04-02T03:00:00",
        duration_ms=125_000,
    ))

    # spec/56 marker-partition shape: ONE user marker partitions the video into
    # TWO segments (virtual items, NULL file identity). Geometry is never
    # stored — it derives from marker order; the satellites carry only the
    # order-identity (seg_index). Segment 0 is picked + adjusted, segment 1
    # keeps the explicit default-Skip row segments are born with.
    doc.video_markers.append(m.VideoMarker(
        id="mk-1", video_item_id="i-video", at_ms=4000,
        created_at="2026-05-30T00:00:00+00:00"))
    doc.items.append(m.Item(id="i-seg0", kind="video", created_at="2026-05-30T00:00:00+00:00",
                            provenance="clip", parent_item_id="i-video"))
    doc.video_segments.append(m.VideoSegment(
        item_id="i-seg0", video_item_id="i-video", seg_index=0,
        created_at="2026-05-30T00:00:00+00:00"))
    doc.phase_states.append(m.PhaseState(item_id="i-seg0", phase="edit", state="picked"))
    doc.video_adjustments.append(m.VideoAdjustment(
        item_id="i-seg0", look="deeper", speed=0.5, include_audio=False,
        rotation_degrees=180,
    ))
    doc.items.append(m.Item(id="i-seg1", kind="video", created_at="2026-05-30T00:00:00+00:00",
                            provenance="clip", parent_item_id="i-video"))
    doc.video_segments.append(m.VideoSegment(
        item_id="i-seg1", video_item_id="i-video", seg_index=1,
        created_at="2026-05-30T00:00:00+00:00"))
    doc.phase_states.append(m.PhaseState(item_id="i-seg1", phase="edit", state="skipped"))

    # VIRTUAL snapshot child anchored at a point — auto-Picked at creation (spec/56)
    doc.items.append(m.Item(id="i-snap", kind="photo", created_at="2026-05-30T00:00:00+00:00",
                            provenance="snapshot", parent_item_id="i-video"))
    doc.video_snapshots.append(m.VideoSnapshot(
        item_id="i-snap", video_item_id="i-video", at_ms=3000,
        created_at="2026-05-30T00:00:00+00:00"))
    doc.phase_states.append(m.PhaseState(item_id="i-snap", phase="edit", state="picked"))

    # materialised stack output item (provenance='stack_output', real bytes)
    doc.items.append(m.Item(id="i-stk", kind="photo", created_at="2026-05-30T00:00:00+00:00",
                            provenance="stack_output", origin_relpath="03 - Processed/Day01/stack1.tif",
                            sha256="c" * 64, byte_size=40_000_000, materialized_at="2026-05-30T00:00:00+00:00",
                            materialized_phase="edit", day_number=1))

    doc.camera_calibration_pairs.append(m.CameraCalibrationPair(
        id="cal1", camera_id="G9M2", ref_time="2026-04-01T08:00:00", camera_time="2026-04-01T08:09:00",
        offset_minutes=-540, created_at="2026-05-30T00:00:00+00:00", ref_item_id="i-photo", subject_item_id="i-photo",
    ))

    doc.buckets += [
        m.Bucket(bucket_key="G9M2/01/sunrise", phase="pick", default_state="picked", reviewed=True, browsed=True, current_index=4, nudge_dismissed=True),
        m.Bucket(bucket_key="hash-abc123", phase="pick"),
    ]

    doc.stacks.append(m.StackBracket(bracket_id="brk1", kind="focus", action="stacked", picked_index=2, output_item_id="i-stk", day_number=1))
    doc.stack_members.append(m.StackMember(bracket_id="brk1", item_id="i-photo", ordinal=0))

    # subsets / subset_members / share_maps retired per spec/52 + spec/51 —
    # subset concept absorbed into Cuts (a "subset" is just a Cut with seed_tag);
    # share_map retires as maps become items with provenance='authored'.

    doc.lineage += [
        m.Lineage(export_relpath="03 - Processed/Day01/P1000001.jpg", phase="edit", source_kind="item", source_item_id="i-photo"),
        m.Lineage(export_relpath="03 - Processed/Day01/stack1.tif", phase="edit", source_kind="bracket", source_bracket_id="brk1"),
    ]
    return doc


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #


def test_create_sets_version_and_schema_info(tmp_path):
    store = _make_store(tmp_path)
    assert schema.get_version(store.conn) == schema.SCHEMA_VERSION
    info = schema.get_schema_info(store.conn)
    assert info["event_id"] == "evt-1"
    assert info["created_at"] == "2026-05-30T00:00:00+00:00"
    store.close()


def test_pragmas_applied(tmp_path):
    store = _make_store(tmp_path)
    assert store.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert store.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    store.close()


def test_initialize_twice_raises(tmp_path):
    store = _make_store(tmp_path)
    with pytest.raises(RuntimeError):
        schema.initialize(store.conn, event_id="evt-1")
    store.close()


def test_open_uninitialised_raises(tmp_path):
    sqlite3.connect(tmp_path / "empty.db").close()
    with pytest.raises(RuntimeError):
        EventStore.open(tmp_path / "empty.db")


def test_open_existing_roundtrips(tmp_path):
    path = tmp_path / "event.db"
    EventStore.create(path, event_id="evt-1", app_version="x").close()
    store = EventStore.open(path)
    assert schema.get_version(store.conn) == schema.SCHEMA_VERSION
    store.close()


def test_migrate_future_version_raises(tmp_path):
    store = _make_store(tmp_path)
    store.conn.execute("UPDATE schema_info SET schema_version = ? WHERE id = 1", (schema.SCHEMA_VERSION + 1,))
    with pytest.raises(RuntimeError):
        schema.migrate(store.conn)
    store.close()


def test_foreign_keys_enforced(tmp_path):
    store = _make_store(tmp_path)
    # phase_state -> item FK should reject an orphan outside a deferred-FK txn
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO phase_state (item_id, phase, state) VALUES ('ghost', 'pick', 'picked')"
        )
    store.close()


def test_virtual_vs_materialized_check_enforced(tmp_path):
    """A half-materialised item (relpath set, sha/size NULL) is rejected by the DDL CHECK."""
    store = _make_store(tmp_path)
    store.upsert(m.Event(uuid="evt-1", name="A", created_at="t", updated_at="t"))
    store.upsert(m.Camera(camera_id="G9"))
    store.upsert(m.Item(id="v", kind="video", created_at="t", provenance="captured",
                        origin_relpath="00/v.mp4", sha256="x", byte_size=1, materialized_at="t",
                        camera_id="G9", capture_time_raw="2026-01-01T00:00:00"))
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO item (id, kind, created_at, provenance, parent_item_id, origin_relpath) "
            "VALUES ('bad', 'video', 't', 'clip', 'v', 'half.mp4')"  # relpath without sha/size
        )
    store.close()


def test_item_extras_json_roundtrips_and_validates(tmp_path):
    """The sanctioned per-item JSON escape hatch round-trips, and the DDL's json_valid
    CHECK rejects malformed JSON (Nelson 2026-06-01 — chose this over blank spare columns)."""
    import json

    store = _make_store(tmp_path)
    store.upsert(m.Event(uuid="evt-1", name="A", created_at="t", updated_at="t"))
    store.upsert(m.Camera(camera_id="G9"))
    store.upsert(m.Item(id="i1", kind="photo", created_at="t", provenance="captured",
                        origin_relpath="p.jpg", sha256="s", byte_size=1, materialized_at="t",
                        materialized_phase="ingest", camera_id="G9",
                        capture_time_raw="2026-01-01T00:00:00",
                        extras_json=json.dumps({"k": "v", "n": 3})))
    assert json.loads(store.get(m.Item, "i1").extras_json) == {"k": "v", "n": 3}
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO item (id, kind, created_at, provenance, origin_relpath, sha256, "
            "byte_size, materialized_at, camera_id, capture_time_raw, extras_json) "
            "VALUES ('i2','photo','t','captured','q.jpg','s2',1,'t','G9',"
            "'2026-01-01T00:00:00','{not json')"
        )
    store.close()


# --------------------------------------------------------------------------- #
# Generic CRUD + transactions
# --------------------------------------------------------------------------- #


def test_upsert_get_all_delete(tmp_path):
    store = _make_store(tmp_path)
    store.upsert(m.Event(uuid="evt-1", name="A", created_at="t", updated_at="t"))
    store.upsert(m.TripDay(day_number=1, description="Day one"))
    store.upsert(m.TripDay(day_number=2, description="Day two"))

    got = store.get(m.TripDay, 1)
    assert got is not None and got.description == "Day one"
    assert [d.day_number for d in store.all(m.TripDay)] == [1, 2]

    # replace semantics
    store.upsert(m.TripDay(day_number=1, description="Day one edited"))
    assert store.get(m.TripDay, 1).description == "Day one edited"

    store.delete(m.TripDay, 1)
    assert store.get(m.TripDay, 1) is None
    assert [d.day_number for d in store.all(m.TripDay)] == [2]
    store.close()


def test_query_by_uses_sql_where(tmp_path):
    store = _make_store(tmp_path)
    store.save_document(_rich_document())
    # children of the source video via an indexed WHERE (not a Python scan)
    kids = store.query_by(m.Item, parent_item_id="i-video")
    assert {k.id for k in kids} == {"i-seg0", "i-seg1", "i-snap"}
    # phase filter
    assert {ps.item_id for ps in store.query_by(m.PhaseState, phase="edit")} == {
        "i-seg0", "i-seg1", "i-snap"}
    # segment satellites come back in seg_index order (the registry order_by)
    assert [s.item_id for s in store.query_by(m.VideoSegment, video_item_id="i-video")] == [
        "i-seg0", "i-seg1"]
    store.close()


def test_bool_roundtrips_through_columns(tmp_path):
    store = _make_store(tmp_path)
    store.upsert(m.Event(uuid="evt-1", name="A", created_at="t", updated_at="t"))
    # spec/52: Camera.is_reference retired (no more reference-camera concept).
    # is_phone remains as the phone-detection cache.
    store.upsert(m.Camera(camera_id="G9", is_phone=False))
    cam = store.get(m.Camera, "G9")
    assert cam.is_phone is False
    store.close()


def test_transaction_rolls_back_on_error(tmp_path):
    store = _make_store(tmp_path)
    store.upsert(m.Event(uuid="evt-1", name="A", created_at="t", updated_at="t"))
    with pytest.raises(ValueError):
        with store.transaction() as conn:
            conn.execute("INSERT INTO trip_day (day_number, description) VALUES (9, 'pending')")
            raise ValueError("boom")
    assert store.get(m.TripDay, 9) is None
    store.close()


def test_phase_counts_query(tmp_path):
    store = _make_store(tmp_path)
    store.save_document(_rich_document())
    # Post-Slice-0: cull + select collapsed into one 'pick' phase. The
    # original two assertions (cull-kept + select-candidate) merge: per
    # PRIMARY KEY (item_id, phase), only one row survives per item. Slice B
    # will re-do this test under the unified Select model.
    assert store.phase_counts("pick") == {"picked": 1}
    # picked segment + picked snapshot; segment 1 carries its explicit
    # default-Skip row (spec/56 — segments are born with one).
    assert store.phase_counts("edit") == {"picked": 2, "skipped": 1}
    store.close()


# --------------------------------------------------------------------------- #
# JSON round-trip + the store->json->store gate
# --------------------------------------------------------------------------- #


def test_json_roundtrip_pure(tmp_path):
    doc = _rich_document()
    assert json_dump.from_json(json_dump.to_json(doc)) == doc


def test_json_top_level_shape(tmp_path):
    data = json_dump.to_json(_rich_document())
    assert data["schema_version"] == schema.SCHEMA_VERSION
    # spec/52 cleanup: participants / participant_devices / checklist / distribution /
    # share_tag / subsets / share_maps retired. spec/61: photo_tags retired too —
    # cuts + cut_members (file-based membership) + photo_persons carry Share.
    for key in ("event", "trip_days", "cameras", "camera_calibration_pairs",
                "items", "buckets", "stacks", "dynamic_collections", "cuts",
                "cut_members", "photo_persons", "lineage"):
        assert key in data
    assert "photo_tags" not in data
    # the DC carries the live formula; the Cut is frozen from it (spec/81)
    assert [d["tag"] for d in data["dynamic_collections"]] == ["best_macro_shots"]
    assert data["cuts"][0]["source_dc_id"] == "dc-1"
    assert data["cuts"][0]["expr_snapshot_json"] == '[["+", "exported"]]'
    # an item nests its satellites (cuts are flat at the top level)
    photo = next(i for i in data["items"] if i["id"] == "i-photo")
    assert set(photo["phase_state"]) == {"pick"}
    assert photo["adjustment"]["rotation"] == 90 and photo["adjustment"]["crop_w"] == 1.0
    assert photo["video_segment"] is None and photo["video_adjustment"] is None
    assert photo["video_markers"] == []
    # cut + membership at the top level — membership references the exported
    # FILE (lineage PK), never the item (spec/61 §1.2)
    assert [c["tag"] for c in data["cuts"]] == ["best_macro_shots"]
    members = [cm for cm in data["cut_members"] if cm["cut_id"] == "cut-1"]
    assert [cm["export_relpath"] for cm in members] == ["03 - Processed/Day01/P1000001.jpg"]
    # the source video carries its markers (spec/56 — segments derive from them)
    video = next(i for i in data["items"] if i["id"] == "i-video")
    assert [mk["at_ms"] for mk in video["video_markers"]] == [4000]
    # a segment is its OWN item carrying its order-identity satellite + its
    # video_adjustment (not nested in the video); geometry is nowhere in the dump
    seg = next(i for i in data["items"] if i["id"] == "i-seg0")
    assert seg["provenance"] == "clip" and seg["parent_item_id"] == "i-video"
    assert seg["origin_relpath"] is None                       # virtual
    assert seg["video_segment"]["seg_index"] == 0
    assert seg["video_adjustment"]["speed"] == 0.5
    # a snapshot nests its point satellite
    snap = next(i for i in data["items"] if i["id"] == "i-snap")
    assert snap["video_snapshot"]["at_ms"] == 3000
    # the event carries the folded-in budget
    assert data["event"]["budget_short_target_s"] == 300 and data["event"]["uuid"] == "evt-1"


def test_store_to_json_to_store_equality(tmp_path):
    """The load-bearing gate: restore == migration == fixture (one reader)."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    src = _make_store(tmp_path / "a")
    src.save_document(_rich_document())
    dumped = json_dump.to_json(src.load_document())

    dst = EventStore.create(tmp_path / "b" / "event.db", event_id="evt-1", created_at="t")
    dst.save_document(json_dump.from_json(dumped))

    assert dst.load_document() == src.load_document()
    src.close()
    dst.close()


def test_save_document_is_idempotent(tmp_path):
    """A second identical save must not duplicate rows or diverge from one save."""
    (tmp_path / "once").mkdir()
    (tmp_path / "twice").mkdir()
    doc = _rich_document()

    once = _make_store(tmp_path / "once")
    once.save_document(doc)

    twice = _make_store(tmp_path / "twice")
    twice.save_document(doc)
    twice.save_document(doc)  # second write must not duplicate or error

    reloaded = twice.load_document()
    assert len(reloaded.items) == 6
    assert len(reloaded.phase_states) == 4   # photo pick + two segments + snapshot
    assert len(reloaded.video_markers) == 1
    assert len(reloaded.video_segments) == 2
    assert len(reloaded.video_snapshots) == 1
    assert reloaded == once.load_document()
    once.close()
    twice.close()


# --------------------------------------------------------------------------- #
# Migrations. The 2026-06-10 spec/56 RESET restarted SCHEMA_VERSION at 1;
# v1→v2 (classification_confidence) is additive-only and covered by the
# machinery tests above; v2→v3 (spec/61 cut tables) gets a per-step test here.
# --------------------------------------------------------------------------- #


def test_migrate_v2_to_v3_replaces_photo_tag_with_cuts(tmp_path):
    """v2→v3 (spec/61): photo_tag (item-based membership, never written by any
    user flow) drops; cut + cut_member (file-based membership → lineage)
    arrive, and the migrated DB accepts the rich document."""
    store = _make_store(tmp_path)
    conn = store.conn
    # Reconstruct the v2 shape: cut tables absent, photo_tag present.
    conn.execute("DROP TABLE IF EXISTS dynamic_collection")
    conn.execute("DROP TABLE cut_member")
    conn.execute("DROP TABLE cut")
    conn.execute(
        "CREATE TABLE photo_tag (item_id TEXT NOT NULL, tag TEXT NOT NULL, "
        "tagged_at TEXT NOT NULL, PRIMARY KEY (item_id, tag))")
    # Strip the v5 column from adjustment so the v4→v5 ADD COLUMN
    # step doesn't collide on the way back up (Nelson 2026-06-13).
    conn.execute("ALTER TABLE adjustment DROP COLUMN look_strength")
    # spec/115 — strip the v16 user_exposure column so the v15→v16
    # ADD COLUMN doesn't collide on the way back up.
    conn.execute("ALTER TABLE adjustment DROP COLUMN user_exposure")
    # Reverse the v5→v6 event qualifier swap so v5→v6 finds the v5
    # shape it expects (spec/64). The fresh store ships at the current
    # SCHEMA_VERSION; rolling back to v2 means restoring scope / mood /
    # transport and dropping context / experience_type / creative_focus.
    conn.execute("DROP INDEX IF EXISTS ix_event_context")
    conn.execute("DROP INDEX IF EXISTS ix_event_experience_type")
    conn.execute("ALTER TABLE event DROP COLUMN context")
    conn.execute("ALTER TABLE event DROP COLUMN experience_type")
    conn.execute("ALTER TABLE event DROP COLUMN creative_focus")
    conn.execute("ALTER TABLE event ADD COLUMN scope TEXT")
    conn.execute("ALTER TABLE event ADD COLUMN mood TEXT")
    conn.execute("ALTER TABLE event ADD COLUMN transport TEXT")
    conn.execute(
        "CREATE INDEX ix_event_scope ON event(scope) WHERE scope IS NOT NULL")
    conn.execute(
        "CREATE INDEX ix_event_mood ON event(mood) WHERE mood IS NOT NULL")
    # Strip the post-v2 lineage columns so the ADD COLUMN migrations
    # on the way back up don't collide (spec/89 added 'provenance' and
    # 'intent_state'; spec/144 added 'duration_ms').
    conn.execute("ALTER TABLE lineage DROP COLUMN duration_ms")
    conn.execute("ALTER TABLE lineage DROP COLUMN intent_state")
    conn.execute("ALTER TABLE lineage DROP COLUMN provenance")
    # Strip the v12 face table so the v11→v12 CREATE TABLE doesn't
    # collide on the way back up (spec/90 Phase 1). Also drop the v13
    # recipe table for the same reason (spec/94 Phase 1).
    conn.execute("DROP TABLE face")
    conn.execute("DROP TABLE IF EXISTS recipe")
    # Strip the v14 stack_bracket.producer column so the v13→v14 ADD
    # COLUMN migration doesn't collide on the way back up (spec/109).
    conn.execute("ALTER TABLE stack_bracket DROP COLUMN producer")
    # NB: cut table was dropped above, so the spec/111 v14→v15 cut.aspect
    # add doesn't collide here — v2→v3 will re-create the table sans
    # aspect; v14→v15 will then add the column on top of it.
    # spec/123 v16→v17 — rename the *_seconds columns back to *_minutes
    # so the migration on the way back up has the v16 shape to work
    # with (and divide the seeded values back down).
    conn.execute(
        "ALTER TABLE camera RENAME COLUMN applied_offset_seconds "
        "TO applied_offset_minutes")
    conn.execute(
        "ALTER TABLE camera RENAME COLUMN configured_tz_seconds "
        "TO configured_tz_minutes")
    conn.execute(
        "ALTER TABLE item RENAME COLUMN tz_offset_seconds "
        "TO tz_offset_minutes")
    # spec/127 v17→v18 — drop the per-(camera, trip-TZ) correction table
    # so the migration on the way back up creates it fresh.
    conn.execute("DROP INDEX IF EXISTS ix_camera_tz_correction_tz")
    conn.execute("DROP TABLE IF EXISTS camera_tz_correction")
    conn.execute("UPDATE schema_info SET schema_version = 2 WHERE id = 1")

    schema.migrate(conn)

    assert schema.get_version(conn) == schema.SCHEMA_VERSION
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "cut" in names and "cut_member" in names
    assert "photo_tag" not in names
    # The migrated DB is fully usable: the rich document round-trips.
    store.save_document(_rich_document())
    assert [c.tag for c in store.all(m.Cut)] == ["best_macro_shots"]
    assert [cm.export_relpath for cm in store.all(m.CutMember)] == [
        "03 - Processed/Day01/P1000001.jpg"]
    store.close()


# --------------------------------------------------------------------------- #
# v6 -> v7 (spec/81 — Dynamic Collection / Cut)
# --------------------------------------------------------------------------- #


def _strip_post_v6_lineage_cols(conn) -> None:
    """The fresh DDL ships at the current SCHEMA_VERSION, so the
    ``lineage`` table already carries every post-v6 column and the
    ``face`` table (spec/90 v11→v12) already exists. Strip them so the
    post-v6 ADD COLUMN / CREATE TABLE migrations on the way back up
    don't collide (spec/89 v9→v10 added ``provenance``; v10→v11 added
    ``intent_state``; spec/90 v11→v12 created ``face``; spec/94 v12→v13
    created ``recipe``; spec/109 v13→v14 added stack_bracket.producer).
    NB: the spec/111 v14→v15 ``cut.aspect`` column does NOT need
    stripping here — callers run :func:`_rebuild_v6_cut_tables` first,
    which recreates ``cut`` at the v6 shape (no aspect column); v14→v15
    will then add it on the way back up."""
    # spec/144 v18→v19 added lineage.duration_ms; strip here too so
    # the ALTER on the way back up doesn't collide.
    conn.execute("ALTER TABLE lineage DROP COLUMN duration_ms")
    conn.execute("ALTER TABLE lineage DROP COLUMN intent_state")
    conn.execute("ALTER TABLE lineage DROP COLUMN provenance")
    conn.execute("DROP TABLE face")
    conn.execute("DROP TABLE IF EXISTS recipe")
    conn.execute("ALTER TABLE stack_bracket DROP COLUMN producer")
    # spec/115 — strip v15→v16 adjustment.user_exposure too so the
    # ADD COLUMN on the way back up doesn't collide.
    conn.execute("ALTER TABLE adjustment DROP COLUMN user_exposure")
    # spec/123 v16→v17 — rename the *_seconds columns back to *_minutes
    # so the rename + ×60 migration has the v16 shape to work with.
    conn.execute(
        "ALTER TABLE camera RENAME COLUMN applied_offset_seconds "
        "TO applied_offset_minutes")
    conn.execute(
        "ALTER TABLE camera RENAME COLUMN configured_tz_seconds "
        "TO configured_tz_minutes")
    conn.execute(
        "ALTER TABLE item RENAME COLUMN tz_offset_seconds "
        "TO tz_offset_minutes")
    # spec/127 v17→v18 — drop camera_tz_correction so the v17→v18
    # CREATE TABLE on the way back up doesn't collide.
    conn.execute("DROP INDEX IF EXISTS ix_camera_tz_correction_tz")
    conn.execute("DROP TABLE IF EXISTS camera_tz_correction")


def _rebuild_v6_cut_tables(conn) -> None:
    """Recreate the v6 cut shape (pool_expr_json + style/type filters, no DC),
    so the v6->v7 migration has something real to fold."""
    conn.execute("DROP TABLE IF EXISTS dynamic_collection")
    conn.execute("DROP TABLE cut_member")
    conn.execute("DROP TABLE cut")
    conn.execute("""
CREATE TABLE cut (
  id                TEXT PRIMARY KEY,
  tag               TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK (tag <> ''),
  target_s          INTEGER,
  max_s             INTEGER,
  photo_s           REAL NOT NULL DEFAULT 6.0,
  pool_expr_json    TEXT NOT NULL DEFAULT '[]',
  style_filter_json TEXT NOT NULL DEFAULT '[]',
  type_filter       TEXT NOT NULL DEFAULT 'both',
  default_state     TEXT NOT NULL DEFAULT 'skipped',
  music_category    TEXT,
  last_exported_at  TEXT,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  extras_json       TEXT NOT NULL DEFAULT '{}'
)""")
    conn.execute("""
CREATE TABLE cut_member (
  cut_id         TEXT NOT NULL REFERENCES cut(id) ON DELETE CASCADE,
  export_relpath TEXT NOT NULL REFERENCES lineage(export_relpath) ON DELETE CASCADE,
  added_at       TEXT NOT NULL,
  PRIMARY KEY (cut_id, export_relpath)
)""")
    conn.execute("CREATE INDEX ix_cut_member_file ON cut_member(export_relpath)")


def test_migrate_v6_to_v7_synthesizes_dc_and_freezes_cut(tmp_path):
    """v6->v7 (spec/81): a v6 cut with a pool_expr + filters synthesizes a DC
    (reusing the cut's OWN tag), folds the filters into filters_json, points
    source_dc_id at it, freezes expr_snapshot_json, and drops the old columns."""
    store = _make_store(tmp_path)
    conn = store.conn
    _rebuild_v6_cut_tables(conn)
    _strip_post_v6_lineage_cols(conn)
    conn.execute(
        "INSERT INTO cut (id, tag, target_s, photo_s, pool_expr_json, "
        "style_filter_json, type_filter, default_state, created_at, updated_at) "
        "VALUES ('c1', 'best_macro', 600, 6.0, "
        "'[[\"+\", \"exported\"], [\"-\", \"drafts\"]]', "
        "'[\"macro\"]', 'photo', 'skipped', 't0', 't0')")
    conn.execute("UPDATE schema_info SET schema_version = 6 WHERE id = 1")

    schema.migrate(conn)

    assert schema.get_version(conn) == schema.SCHEMA_VERSION
    assert schema.SCHEMA_VERSION >= 7
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "dynamic_collection" in names
    dc = conn.execute("SELECT * FROM dynamic_collection").fetchone()
    assert dc["tag"] == "best_macro"
    import json as _json
    assert _json.loads(dc["expr_json"]) == [
        ["+", "exported"], ["-", {"kind": "cut", "id": None, "tag": "drafts"}]]
    assert _json.loads(dc["filters_json"]) == {
        "styles": ["macro"], "media_type": "photo"}
    cut = conn.execute("SELECT * FROM cut").fetchone()
    assert cut["source_dc_id"] == dc["id"]
    assert _json.loads(cut["expr_snapshot_json"]) == _json.loads(dc["expr_json"])
    cols = {r[1] for r in conn.execute("PRAGMA table_info(cut)")}
    assert "pool_expr_json" not in cols
    assert "style_filter_json" not in cols
    assert "type_filter" not in cols
    assert {"source_dc_id", "expr_snapshot_json", "separators",
            "overlay_fields_json", "overlay_mode"} <= cols
    store.close()


def test_migrate_v6_to_v7_is_idempotent(tmp_path):
    """Re-running migrate() after reaching v7 is a no-op (no second DC)."""
    store = _make_store(tmp_path)
    conn = store.conn
    _rebuild_v6_cut_tables(conn)
    _strip_post_v6_lineage_cols(conn)
    conn.execute(
        "INSERT INTO cut (id, tag, pool_expr_json, created_at, updated_at) "
        "VALUES ('c1', 'best_macro', '[[\"+\", \"exported\"]]', 't0', 't0')")
    conn.execute("UPDATE schema_info SET schema_version = 6 WHERE id = 1")
    schema.migrate(conn)
    n1 = conn.execute("SELECT COUNT(*) FROM dynamic_collection").fetchone()[0]
    schema.migrate(conn)
    n2 = conn.execute("SELECT COUNT(*) FROM dynamic_collection").fetchone()[0]
    assert n1 == n2 == 1
    store.close()


def test_dc_crud_round_trips_through_document(tmp_path):
    """A DC + a frozen Cut made from it survive store -> json -> store."""
    (tmp_path / "a").mkdir()
    src = _make_store(tmp_path / "a")
    src.save_document(_rich_document())
    reloaded = src.load_document()
    assert [d.tag for d in reloaded.dynamic_collections] == ["best_macro_shots"]
    dc = reloaded.dynamic_collections[0]
    assert dc.expr_json == '[["+", "exported"]]'
    cut = next(c for c in reloaded.cuts if c.id == "cut-1")
    assert cut.source_dc_id == "dc-1"
    assert cut.overlay_fields_json == '["when", "where"]'
    assert cut.overlay_mode == "embedded"
    assert json_dump.from_json(json_dump.to_json(reloaded)) == reloaded
    src.close()


def test_cut_dc_freeze_invariant_at_gateway_level(tmp_path):
    """spec/81 §5 (freeze invariant). Schema v8 (spec/81 Phase 2) DROPPED the
    FK that used to carry ON DELETE SET NULL on ``cut.source_dc_id``; the
    equivalent guarantee now lives in :meth:`EventGateway.delete_dc`. So a
    raw SQL ``DELETE FROM dynamic_collection`` no longer touches the Cut
    (the source_dc_id stays as a dangling opaque id), but the gateway path
    NULLs it correctly. The Cut + members survive either way."""
    store = _make_store(tmp_path)
    store.save_document(_rich_document())
    # Raw SQL delete — no cascade now; the source_dc_id remains for audit.
    store.conn.execute("DELETE FROM dynamic_collection WHERE id = 'dc-1'")
    cut = store.get(m.Cut, "cut-1")
    assert cut is not None
    assert cut.source_dc_id == "dc-1"          # dangling, gateway would NULL
    assert [cm.export_relpath for cm in store.all(m.CutMember)] == [
        "03 - Processed/Day01/P1000001.jpg"]
    store.close()


def test_cut_member_lineage_cascade_dropped_in_v8(tmp_path):
    """spec/81 Phase 2 (schema v8) DROPPED the FK on
    ``cut_member.export_relpath`` so cross-event members can reference
    lineage in other event.db files. The cut-side cascade (cut delete →
    members cascade) stays via the ``cut_id`` FK; integrity for the
    lineage relationship moves to the gateway sweep."""
    store = _make_store(tmp_path)
    store.save_document(_rich_document())
    # Lineage delete no longer cascades — the cut_member survives as a
    # dangling reference; a gateway sweep is the new integrity seam.
    store.conn.execute(
        "DELETE FROM lineage WHERE export_relpath = "
        "'03 - Processed/Day01/P1000001.jpg'")
    assert len(store.all(m.CutMember)) == 1
    # Cut delete still cascades (cut_id FK ON DELETE CASCADE survives).
    store.conn.execute("DELETE FROM cut WHERE id = 'cut-1'")
    assert store.all(m.CutMember) == []
    store.close()


# --------------------------------------------------------------------------- #
# v16 -> v17 (spec/123 — *_minutes -> *_seconds, ×60 conversion)
# --------------------------------------------------------------------------- #


def test_migrate_v16_to_v17_renames_and_scales_minute_columns(tmp_path):
    """v16→v17 (spec/123): camera.applied_offset_minutes and
    configured_tz_minutes, and item.tz_offset_minutes, become *_seconds
    with values ×60. Lossless on whole-minute values."""
    store = _make_store(tmp_path)
    conn = store.conn
    # Rebuild the v16 column shape (the fresh DDL is at v17 with the
    # renamed columns), then seed values in MINUTES, then migrate.
    conn.execute(
        "ALTER TABLE camera RENAME COLUMN applied_offset_seconds "
        "TO applied_offset_minutes")
    conn.execute(
        "ALTER TABLE camera RENAME COLUMN configured_tz_seconds "
        "TO configured_tz_minutes")
    conn.execute(
        "ALTER TABLE item RENAME COLUMN tz_offset_seconds "
        "TO tz_offset_minutes")
    # spec/127 v17→v18 — drop camera_tz_correction so the v17→v18
    # migration's CREATE TABLE doesn't collide on the way back up.
    conn.execute("DROP INDEX IF EXISTS ix_camera_tz_correction_tz")
    conn.execute("DROP TABLE IF EXISTS camera_tz_correction")
    # spec/144 v18→v19 — strip lineage.duration_ms so the ALTER on
    # the way back up doesn't collide on the column.
    conn.execute("ALTER TABLE lineage DROP COLUMN duration_ms")
    conn.execute(
        "INSERT INTO camera (camera_id, applied_offset_minutes, "
        "configured_tz_minutes) VALUES "
        "('GoPro', 525, -180), "        # +8:45 minutes, -3:00 minutes
        "('phone', NULL, NULL)")
    conn.execute(
        "INSERT INTO item (id, kind, provenance, origin_relpath, sha256, "
        "byte_size, materialized_at, materialized_phase, camera_id, "
        "capture_time_raw, capture_time_corrected, tz_offset_minutes, "
        "tz_source, created_at) VALUES "
        "('a', 'photo', 'captured', 'a.jpg', 'sha', 1, "
        "'2026-03-10T00:00:00', 'ingest', 'GoPro', '2026-03-09T23:30:00', "
        "'2026-03-10T08:15:00', 525, 'user_declared', "
        "'2026-03-10T00:00:00')")
    conn.execute("UPDATE schema_info SET schema_version = 16 WHERE id = 1")

    schema.migrate(conn)

    assert schema.get_version(conn) == schema.SCHEMA_VERSION
    # Columns now named *_seconds.
    cam_cols = {r[1] for r in conn.execute("PRAGMA table_info(camera)")}
    assert "applied_offset_seconds" in cam_cols
    assert "configured_tz_seconds" in cam_cols
    assert "applied_offset_minutes" not in cam_cols
    assert "configured_tz_minutes" not in cam_cols
    item_cols = {r[1] for r in conn.execute("PRAGMA table_info(item)")}
    assert "tz_offset_seconds" in item_cols
    assert "tz_offset_minutes" not in item_cols
    # Values multiplied by 60.
    gopro = conn.execute(
        "SELECT applied_offset_seconds, configured_tz_seconds "
        "FROM camera WHERE camera_id = 'GoPro'").fetchone()
    assert gopro["applied_offset_seconds"] == 525 * 60      # 31 500
    assert gopro["configured_tz_seconds"] == -180 * 60      # -10 800
    # NULLs stay NULL on cameras with no recorded offset.
    phone = conn.execute(
        "SELECT applied_offset_seconds, configured_tz_seconds "
        "FROM camera WHERE camera_id = 'phone'").fetchone()
    assert phone["applied_offset_seconds"] is None
    assert phone["configured_tz_seconds"] is None
    item = conn.execute(
        "SELECT tz_offset_seconds FROM item WHERE id = 'a'").fetchone()
    assert item["tz_offset_seconds"] == 525 * 60
    store.close()
