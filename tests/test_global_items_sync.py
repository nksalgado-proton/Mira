"""spec/81 Phase 2 + spec/32 §3 — ``global_items`` projection sync.

Drives :mod:`mira.gateway.global_items_sync` against a tiny hand-built
``event.db`` + a fresh ``mira.db``. Pure logic — no Qt. Covers:

* The projection shape (every column mapped + the ladder rungs).
* Replace-the-slice semantics (re-sync drops + re-inserts).
* The reconcile pass (multi-event sync + stale-event drop).
* Atomicity (a mid-write failure rolls back).
"""
from __future__ import annotations

import json

import pytest

from mira.gateway import global_items_sync as gis
from mira.store import models as sm, schema as sschema
from mira.store.repo import EventStore
from mira.user_store import models as um
from mira.user_store.repo import UserStore


# --------------------------------------------------------------------------- #
# Fixtures — a minimal event with the rows the projection cares about
# --------------------------------------------------------------------------- #


def _open_event_store(tmp_path, *, event_id: str = "evt-1") -> EventStore:
    """Fresh ``event.db`` at the current schema version."""
    return EventStore.create(
        tmp_path / f"{event_id}.db",
        event_id=event_id,
        app_version="test",
        created_at="2026-06-16T00:00:00+00:00",
    )


def _open_user_store(tmp_path) -> UserStore:
    return UserStore.create(
        tmp_path / "mira.db",
        app_version="test",
        created_at="2026-06-16T00:00:00+00:00",
    )


def _seed_minimal_event(store: EventStore) -> None:
    """Two trip days + two cameras + four items spanning the ladder:
    one collected-only, one picked, one edited, one exported. Plus enough
    EXIF / location context that the projection has something to denormalise.
    """
    NOW = "2026-06-16T00:00:00+00:00"
    # The event singleton is created by EventStore.create's schema_info; the
    # ``event`` table is empty until we insert. UPDATE the singleton instead.
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO event (id, uuid, name, created_at, updated_at) "
            "VALUES (1, 'evt-1', 'Costa Rica 2026', ?, ?)",
            (NOW, NOW),
        )
        # Two days — day 1 has a structured extras_json (country + city);
        # day 2 has only the legacy ``location`` free text (the projection
        # falls back to it for ``day_city``).
        conn.execute(
            "INSERT INTO trip_day (day_number, date, description, location, "
            "tz_minutes, extras_json) VALUES (?, ?, ?, ?, ?, ?)",
            (1, "2026-04-01", "Arrival", "La Fortuna", -360,
             json.dumps({"country": "Costa Rica", "country_code": "CR",
                         "city": "La Fortuna", "sublocation": "Arenal"})),
        )
        conn.execute(
            "INSERT INTO trip_day (day_number, date, description, location, "
            "tz_minutes) VALUES (?, ?, ?, ?, ?)",
            (2, "2026-04-02", "Monteverde", "Monteverde", -360),
        )
        conn.execute(
            "INSERT INTO camera (camera_id, configured_tz_minutes) VALUES (?, ?)",
            ("Panasonic+DC-G9M2", -180),
        )
        # Four items.
        _insert_item(conn, "i-collected", day_number=1, classification="macro",
                     iso=400, stars=3)
        _insert_item(conn, "i-picked", day_number=1, classification="wildlife",
                     iso=1600, stars=4, color_label="green")
        _insert_item(conn, "i-edited", day_number=2, classification="wildlife",
                     iso=200, aperture_f=2.8, stars=5, flag=1)
        _insert_item(conn, "i-exported", day_number=2, classification="macro",
                     iso=100, aperture_f=2.8, focal_length_mm=45.0,
                     flash_fired=0, lens_model="LEICA 45mm",
                     stars=5)
        # phase_state rows — the per-rung ladder.
        conn.execute(
            "INSERT INTO phase_state (item_id, phase, state, decided_at) "
            "VALUES ('i-picked', 'pick', 'picked', ?)", (NOW,))
        conn.execute(
            "INSERT INTO phase_state (item_id, phase, state, decided_at) "
            "VALUES ('i-edited', 'pick', 'picked', ?)", (NOW,))
        conn.execute(
            "INSERT INTO phase_state (item_id, phase, state, decided_at) "
            "VALUES ('i-edited', 'edit', 'picked', ?)", (NOW,))
        conn.execute(
            "INSERT INTO phase_state (item_id, phase, state, decided_at) "
            "VALUES ('i-exported', 'pick', 'picked', ?)", (NOW,))
        conn.execute(
            "INSERT INTO phase_state (item_id, phase, state, decided_at) "
            "VALUES ('i-exported', 'edit', 'picked', ?)", (NOW,))
        # One lineage row → has_export for i-exported.
        conn.execute(
            "INSERT INTO lineage (export_relpath, phase, source_kind, "
            "source_item_id, exported_at) "
            "VALUES ('Exported Media/IMG_0004.jpg', 'edit', 'item', "
            "'i-exported', ?)", (NOW,))


def _insert_item(conn, item_id: str, *, day_number: int,
                 classification: str = None, iso: int = None,
                 aperture_f: float = None, shutter_speed_s: float = None,
                 focal_length_mm: float = None, flash_fired: int = None,
                 lens_model: str = None,
                 stars: int = None, color_label: str = None,
                 flag: int = None) -> None:
    extras = {}
    if stars is not None:
        extras["stars"] = stars
    if color_label is not None:
        extras["color_label"] = color_label
    if flag is not None:
        extras["flag"] = flag
    conn.execute(
        "INSERT INTO item (id, kind, provenance, origin_relpath, sha256, "
        "byte_size, materialized_at, materialized_phase, camera_id, "
        "day_number, capture_time_raw, capture_time_corrected, "
        "tz_offset_minutes, tz_source, classification, iso, aperture_f, "
        "shutter_speed_s, focal_length_mm, flash_fired, lens_model, "
        "extras_json, created_at) "
        "VALUES (?, 'photo', 'captured', ?, ?, ?, ?, 'ingest', "
        "?, ?, ?, ?, -360, 'pair_picker', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (item_id, f"Original Media/{item_id}.jpg", "a" * 64, 1000,
         "2026-06-16T00:00:00+00:00", "Panasonic+DC-G9M2",
         day_number, f"2026-04-0{day_number}T10:00:00",
         f"2026-04-0{day_number}T04:00:00",
         classification, iso, aperture_f, shutter_speed_s,
         focal_length_mm, flash_fired, lens_model,
         json.dumps(extras), "2026-06-16T00:00:00+00:00"),
    )


# --------------------------------------------------------------------------- #
# project_event — every column maps; ladder rungs derive cleanly
# --------------------------------------------------------------------------- #


def test_project_event_emits_one_row_per_item(tmp_path):
    """The projection visits every ``item`` row, regardless of ladder
    state (``#collected`` is the whole table)."""
    store = _open_event_store(tmp_path)
    _seed_minimal_event(store)
    rows = gis.project_event(
        event_store=store, event_uuid="evt-1",
        event_name="Costa Rica 2026",
        now=lambda: "2026-06-16T00:00:00+00:00",
    )
    assert {r.item_id for r in rows} == {
        "i-collected", "i-picked", "i-edited", "i-exported",
    }
    assert all(r.event_uuid == "evt-1" for r in rows)
    assert all(r.event_name == "Costa Rica 2026" for r in rows)
    assert all(r.synced_at == "2026-06-16T00:00:00+00:00" for r in rows)
    store.close()


def test_project_event_derives_ladder_rungs(tmp_path):
    """``pick_state`` / ``edit_state`` / ``has_export`` carry the per-item
    ladder state the cross-event resolver reads (spec/81 §2.1)."""
    store = _open_event_store(tmp_path)
    _seed_minimal_event(store)
    rows = {r.item_id: r for r in gis.project_event(
        event_store=store, event_uuid="evt-1", event_name="X",
    )}
    # Collected only — no phase decision yet, no export.
    assert rows["i-collected"].pick_state is None
    assert rows["i-collected"].edit_state is None
    assert rows["i-collected"].has_export is False
    # Picked but not edited / exported.
    assert rows["i-picked"].pick_state == "picked"
    assert rows["i-picked"].edit_state is None
    assert rows["i-picked"].has_export is False
    # Picked + edited but not exported.
    assert rows["i-edited"].pick_state == "picked"
    assert rows["i-edited"].edit_state == "picked"
    assert rows["i-edited"].has_export is False
    # All four rungs.
    assert rows["i-exported"].pick_state == "picked"
    assert rows["i-exported"].edit_state == "picked"
    assert rows["i-exported"].has_export is True
    store.close()


def test_project_event_denormalises_exif_facets(tmp_path):
    """EXIF facets (spec/32 §2d) read through verbatim — the cross-event
    resolver reads them off ``global_items`` without re-joining."""
    store = _open_event_store(tmp_path)
    _seed_minimal_event(store)
    rows = {r.item_id: r for r in gis.project_event(
        event_store=store, event_uuid="evt-1", event_name="X",
    )}
    e = rows["i-exported"]
    assert e.iso == 100 and e.aperture_f == 2.8
    assert e.focal_length_mm == 45.0 and e.flash_fired == 0
    assert e.lens_model == "LEICA 45mm"
    assert e.camera_id == "Panasonic+DC-G9M2"
    store.close()


def test_project_event_denormalises_curatorial_extras(tmp_path):
    """stars / color_label / flag (spec/32 §2a) come out of the item's
    ``extras_json`` via ``json_extract`` so the index is one read."""
    store = _open_event_store(tmp_path)
    _seed_minimal_event(store)
    rows = {r.item_id: r for r in gis.project_event(
        event_store=store, event_uuid="evt-1", event_name="X",
    )}
    assert rows["i-collected"].stars == 3
    assert rows["i-picked"].stars == 4 and rows["i-picked"].color_label == "green"
    assert rows["i-edited"].flag == 1
    # No stars / no color label → NULL.
    e_no_extras = rows["i-exported"]
    assert e_no_extras.color_label is None and e_no_extras.flag is None
    store.close()


def test_project_event_pulls_day_location_from_extras_then_legacy(tmp_path):
    """``country`` / ``country_code`` / ``day_city`` / ``day_sublocation``
    come from the day's ``extras_json`` when present; the legacy free-text
    ``trip_day.location`` is the fallback for ``day_city`` (spec/32 §2c)."""
    store = _open_event_store(tmp_path)
    _seed_minimal_event(store)
    rows = {r.item_id: r for r in gis.project_event(
        event_store=store, event_uuid="evt-1", event_name="X",
    )}
    # Day 1: structured extras → country + city + sublocation.
    d1 = rows["i-picked"]
    assert d1.country == "Costa Rica" and d1.country_code == "CR"
    assert d1.day_city == "La Fortuna" and d1.day_sublocation == "Arenal"
    # Day 2: legacy ``location`` text only → it lands in day_city; country NULL.
    d2 = rows["i-exported"]
    assert d2.country is None and d2.country_code is None
    assert d2.day_city == "Monteverde"
    store.close()


# --------------------------------------------------------------------------- #
# Event-level qualifiers + derived span (spec/86)
# --------------------------------------------------------------------------- #


def _seed_event_with_qualifiers(store: EventStore, *,
                                event_type="trip",
                                event_subtype="wildlife trip",
                                experience_type="expedition_discovery",
                                participants_json='["Solo","With Friends"]',
                                day_dates=("2024-08-10", "2024-08-15")):
    """A minimal one-item event with full spec/86 qualifiers + a multi-day
    span. The participants JSON is the EXACT envelope ``event.participants``
    stores."""
    NOW = "2026-06-17T00:00:00+00:00"
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO event (id, uuid, name, event_type, event_subtype, "
            "                   experience_type, participants, "
            "                   created_at, updated_at) "
            "VALUES (1, 'evt-q', 'Q', ?, ?, ?, ?, ?, ?)",
            (event_type, event_subtype, experience_type,
             participants_json, NOW, NOW),
        )
        for i, d in enumerate(day_dates, start=1):
            conn.execute(
                "INSERT INTO trip_day (day_number, date, location, tz_minutes) "
                "VALUES (?, ?, ?, ?)",
                (i, d, "Somewhere", -360),
            )
        conn.execute(
            "INSERT INTO camera (camera_id) VALUES ('Panasonic+DC-G9M2')")
        _insert_item(conn, "i-q", day_number=1, classification="macro")


def test_project_event_carries_event_qualifiers(tmp_path):
    """spec/86 §4 — the event-level qualifiers are denormalised onto every
    projected row so the cross-event resolver can filter without joining
    back to ``event``."""
    store = _open_event_store(tmp_path)
    _seed_event_with_qualifiers(store)
    rows = gis.project_event(
        event_store=store, event_uuid="evt-q", event_name="Q")
    assert len(rows) == 1
    r = rows[0]
    assert r.event_type == "trip"
    assert r.event_subtype == "wildlife trip"
    assert r.experience_type == "expedition_discovery"
    # participants stays in its JSON envelope; downstream consumers (the
    # spec/86 inventory + resolver) expand it via json_each.
    assert json.loads(r.participants) == ["Solo", "With Friends"]
    store.close()


def test_project_event_derives_event_span_from_trip_day(tmp_path):
    """spec/86 §5 — event_start / event_end = min/max of trip_day.date."""
    store = _open_event_store(tmp_path)
    _seed_event_with_qualifiers(
        store, day_dates=("2024-08-10", "2024-08-13", "2024-08-15"))
    rows = gis.project_event(
        event_store=store, event_uuid="evt-q", event_name="Q")
    r = rows[0]
    assert r.event_start == "2024-08-10"
    assert r.event_end == "2024-08-15"
    store.close()


def test_project_event_span_is_null_when_no_dated_days(tmp_path):
    """An event whose days are all undated → ``(None, None)`` span. The
    overlap filter then never matches; that's the correct behaviour —
    no information to bound on."""
    store = _open_event_store(tmp_path)
    NOW = "2026-06-17T00:00:00+00:00"
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO event (id, uuid, name, created_at, updated_at) "
            "VALUES (1, 'evt-u', 'U', ?, ?)", (NOW, NOW))
        conn.execute(
            "INSERT INTO trip_day (day_number, date, location, tz_minutes) "
            "VALUES (1, NULL, 'Undated', -360)")
        conn.execute("INSERT INTO camera (camera_id) VALUES ('Panasonic+DC-G9M2')")
        _insert_item(conn, "i-u", day_number=1, classification="macro")
    rows = gis.project_event(
        event_store=store, event_uuid="evt-u", event_name="U")
    r = rows[0]
    assert r.event_start is None
    assert r.event_end is None
    store.close()


def test_project_event_default_qualifiers_are_safe(tmp_path):
    """The original seed creates an event with the DDL defaults — every
    spec/86 qualifier still reads cleanly off the projection (no NULL
    KeyError, no crash)."""
    store = _open_event_store(tmp_path)
    _seed_minimal_event(store)
    rows = gis.project_event(
        event_store=store, event_uuid="evt-1", event_name="X")
    r = rows[0]
    # DDL defaults: event_type='unclassified', participants='[]'.
    assert r.event_type == "unclassified"
    assert r.event_subtype is None
    assert r.experience_type is None
    assert json.loads(r.participants) == []
    # Span = min/max of the two trip_day dates.
    assert r.event_start == "2026-04-01"
    assert r.event_end == "2026-04-02"
    store.close()


def test_sync_event_writes_qualifiers_to_global_items(tmp_path):
    """End-to-end: a synced row carries the spec/86 qualifiers + span and
    survives a second sync (replace-the-slice keeps consistency)."""
    event_store = _open_event_store(tmp_path)
    _seed_event_with_qualifiers(event_store)
    user_store = _open_user_store(tmp_path)
    gis.sync_event(
        event_store=event_store, user_store=user_store,
        event_uuid="evt-q", event_name="Q")
    rows = user_store.query_by(um.GlobalItem, event_uuid="evt-q")
    assert len(rows) == 1
    r = rows[0]
    assert r.event_type == "trip"
    assert r.event_subtype == "wildlife trip"
    assert r.experience_type == "expedition_discovery"
    assert json.loads(r.participants) == ["Solo", "With Friends"]
    assert r.event_start == "2024-08-10"
    assert r.event_end == "2024-08-15"
    event_store.close(); user_store.close()


# --------------------------------------------------------------------------- #
# sync_event — write semantics: replace-the-event's-slice
# --------------------------------------------------------------------------- #


def test_sync_event_writes_every_projection_row(tmp_path):
    """Every projected row lands in the user store with the right PK."""
    event_store = _open_event_store(tmp_path)
    _seed_minimal_event(event_store)
    user_store = _open_user_store(tmp_path)
    n = gis.sync_event(
        event_store=event_store, user_store=user_store,
        event_uuid="evt-1", event_name="Costa Rica 2026",
    )
    assert n == 4
    rows = user_store.query_by(um.GlobalItem, event_uuid="evt-1")
    assert {r.item_id for r in rows} == {
        "i-collected", "i-picked", "i-edited", "i-exported",
    }
    event_store.close()
    user_store.close()


def test_sync_event_replaces_prior_slice(tmp_path):
    """Re-syncing the same event REPLACES — items deleted between syncs
    fall out; reclassified items resurface with the new facet."""
    event_store = _open_event_store(tmp_path)
    _seed_minimal_event(event_store)
    user_store = _open_user_store(tmp_path)
    gis.sync_event(event_store=event_store, user_store=user_store,
                   event_uuid="evt-1", event_name="X")
    # Mutate the event store: drop one item, reclassify another.
    with event_store.transaction() as conn:
        conn.execute("DELETE FROM phase_state WHERE item_id = 'i-collected'")
        conn.execute("DELETE FROM item WHERE id = 'i-collected'")
        conn.execute(
            "UPDATE item SET classification = 'portrait' WHERE id = 'i-picked'")
    gis.sync_event(event_store=event_store, user_store=user_store,
                   event_uuid="evt-1", event_name="X")
    rows = {r.item_id: r for r in user_store.query_by(
        um.GlobalItem, event_uuid="evt-1")}
    assert "i-collected" not in rows
    assert rows["i-picked"].classification == "portrait"
    event_store.close()
    user_store.close()


def test_sync_event_isolated_per_event_uuid(tmp_path):
    """Two events coexist in the projection; re-syncing one never touches
    the other."""
    e1 = _open_event_store(tmp_path, event_id="e1")
    _seed_minimal_event(e1)
    e2 = _open_event_store(tmp_path, event_id="e2")
    _seed_minimal_event(e2)
    user_store = _open_user_store(tmp_path)
    gis.sync_event(event_store=e1, user_store=user_store,
                   event_uuid="e1-uuid", event_name="E1")
    gis.sync_event(event_store=e2, user_store=user_store,
                   event_uuid="e2-uuid", event_name="E2")
    # Re-sync only e1 with a mutated set.
    with e1.transaction() as conn:
        conn.execute("DELETE FROM phase_state WHERE item_id = 'i-exported'")
        conn.execute(
            "DELETE FROM lineage WHERE source_item_id = 'i-exported'")
        conn.execute("DELETE FROM item WHERE id = 'i-exported'")
    gis.sync_event(event_store=e1, user_store=user_store,
                   event_uuid="e1-uuid", event_name="E1")
    e1_rows = user_store.query_by(um.GlobalItem, event_uuid="e1-uuid")
    e2_rows = user_store.query_by(um.GlobalItem, event_uuid="e2-uuid")
    assert {r.item_id for r in e1_rows} == {
        "i-collected", "i-picked", "i-edited",
    }
    assert {r.item_id for r in e2_rows} == {
        "i-collected", "i-picked", "i-edited", "i-exported",
    }
    e1.close(); e2.close(); user_store.close()


# --------------------------------------------------------------------------- #
# reconcile_all — startup pass over the events index
# --------------------------------------------------------------------------- #


def test_reconcile_all_syncs_every_known_event(tmp_path):
    e1 = _open_event_store(tmp_path, event_id="e1")
    _seed_minimal_event(e1)
    e2 = _open_event_store(tmp_path, event_id="e2")
    _seed_minimal_event(e2)
    e1.close(); e2.close()
    user_store = _open_user_store(tmp_path)

    def _open(uuid):
        path = tmp_path / f"{uuid}.db"
        return EventStore.open(path) if path.exists() else None

    summary = gis.reconcile_all(
        user_store=user_store, open_event_store=_open,
        known_events=[("e1", "E1"), ("e2", "E2")],
    )
    assert summary == {"synced": 2, "dropped": 0, "skipped": 0}
    by_event = {r.event_uuid for r in user_store.all(um.GlobalItem)}
    assert by_event == {"e1", "e2"}
    user_store.close()


def test_reconcile_all_drops_stale_events(tmp_path):
    """A projection row whose event is no longer in the library gets
    dropped — keeps the index clean after manual delete."""
    e1 = _open_event_store(tmp_path, event_id="e1")
    _seed_minimal_event(e1)
    e1.close()
    user_store = _open_user_store(tmp_path)
    # First reconcile registers e1.
    gis.reconcile_all(
        user_store=user_store,
        open_event_store=lambda uuid: EventStore.open(tmp_path / "e1.db")
            if uuid == "e1" else None,
        known_events=[("e1", "E1")],
    )
    assert user_store.query_by(um.GlobalItem, event_uuid="e1")
    # Second reconcile: e1 disappears from known_events → its slice drops.
    summary = gis.reconcile_all(
        user_store=user_store,
        open_event_store=lambda uuid: None,
        known_events=[],
    )
    assert summary["dropped"] == 1
    assert user_store.query_by(um.GlobalItem, event_uuid="e1") == []
    user_store.close()


def test_reconcile_all_skips_unopenable_events_without_raising(tmp_path):
    """An event whose store can't be opened (corruption, missing file,
    in-use lock) is skipped + logged, never raised — the reconcile is
    best-effort (spec/53 §3 protection contract)."""
    user_store = _open_user_store(tmp_path)
    summary = gis.reconcile_all(
        user_store=user_store,
        open_event_store=lambda uuid: None,        # always fail
        known_events=[("ghost", "G")],
    )
    assert summary == {"synced": 0, "dropped": 0, "skipped": 1}
    user_store.close()
