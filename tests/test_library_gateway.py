"""spec/81 Phase 2 — :class:`LibraryGateway` (the cross-event facade).

Drives :class:`mira.gateway.library_gateway.LibraryGateway` against a
hand-seeded ``mira.db`` + a real per-event ``event.db`` for the sync hooks.
Mirrors :mod:`tests.test_gateway_cuts` (the event-scope DC surface) one
method at a time so the two facades stay in lockstep behaviour.
"""
from __future__ import annotations

import json

import pytest

from core import collection_resolver as cr
from mira.gateway.library_gateway import LibraryGateway
from mira.store.repo import EventStore
from mira.user_store import models as um
from mira.user_store.repo import UserStore


NOW = "2026-06-16T00:00:00+00:00"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _open_user_store(tmp_path) -> UserStore:
    return UserStore.create(
        tmp_path / "mira.db",
        app_version="test",
        created_at=NOW,
    )


def _open_library(tmp_path, *, ids=("dc-1", "dc-2", "dc-3", "dc-4")):
    """Deterministic ids so each test names rows predictably."""
    store = _open_user_store(tmp_path)
    id_iter = iter(ids)
    return LibraryGateway(
        store, now=lambda: NOW, new_id=lambda: next(id_iter)), store


def _seed_global_items(store: UserStore) -> None:
    """A 4-event, 6-item universe — same shape as
    test_cross_event_resolver.py but with extra facet diversity so the
    inventory methods have something to dedupe."""
    rows = [
        um.GlobalItem(
            event_uuid="A", item_id="a1", synced_at=NOW,
            event_name="Costa Rica",
            capture_time="2026-04-01T10:00:00",
            kind="photo", classification="macro",
            iso=400, aperture_f=2.8, focal_length_mm=45.0,
            lens_model="LEICA 45mm", camera_id="Pana+G9M2",
            country="Costa Rica", country_code="CR", day_city="La Fortuna",
            stars=3,
        ),
        um.GlobalItem(
            event_uuid="A", item_id="a2", synced_at=NOW,
            event_name="Costa Rica",
            capture_time="2026-04-02T08:00:00",
            kind="photo", classification="wildlife",
            iso=1600, lens_model="LUMIX 100-300", camera_id="Pana+G9M2",
            country="Costa Rica", country_code="CR", day_city="Monteverde",
            stars=4, color_label="green",
            pick_state="picked",
        ),
        um.GlobalItem(
            event_uuid="A", item_id="a3", synced_at=NOW,
            event_name="Costa Rica",
            capture_time="2026-04-03T20:00:00",
            kind="video", duration_ms=60_000,
            classification="landscape",
            iso=200, lens_model="LUMIX 24-105", camera_id="Pana+S5",
            country="Costa Rica", country_code="CR", day_city="Manuel Antonio",
            stars=5,
            pick_state="picked", edit_state="picked", has_export=True,
        ),
        um.GlobalItem(
            event_uuid="B", item_id="b1", synced_at=NOW,
            event_name="Nepal trek",
            capture_time="2025-10-15T07:30:00",
            kind="photo", classification="portrait",
            iso=200, lens_model="Lumix 42.5", camera_id="Pana+G9M2",
            country="Nepal", country_code="NP", day_city="Namche Bazaar",
            stars=5, color_label="red",
            pick_state="picked", edit_state="picked", has_export=True,
        ),
        um.GlobalItem(
            event_uuid="B", item_id="b2", synced_at=NOW,
            event_name="Nepal trek",
            capture_time="2025-10-16T17:30:00",
            kind="photo", classification="landscape",
            iso=3200, camera_id="Pana+G9M2",
            country="Nepal", country_code="NP", day_city="Everest Base Camp",
            stars=2,
        ),
        # Edge-case row with no facets — should not appear in any inventory.
        um.GlobalItem(
            event_uuid="C", item_id="c1", synced_at=NOW,
        ),
    ]
    for r in rows:
        store.upsert(r)


# --------------------------------------------------------------------------- #
# Lifecycle — the gateway does NOT own the user_store
# --------------------------------------------------------------------------- #


def test_context_manager_does_not_close_user_store(tmp_path):
    """LibraryGateway is a facade — the caller owns ``UserStore`` lifecycle,
    same contract as :class:`EventGateway` vs :class:`EventStore`."""
    lg, store = _open_library(tmp_path)
    with lg:
        pass
    # Still usable after exit.
    assert store.all(um.SavedFilter) == []
    store.close()


# --------------------------------------------------------------------------- #
# DC CRUD — slugify + reserved + cycle, mirrored on EventGateway
# --------------------------------------------------------------------------- #


def test_create_dc_slugifies_and_writes_row(tmp_path):
    """The user types anything; the gateway slugifies, validates, writes."""
    lg, store = _open_library(tmp_path)
    dc = lg.create_dc(
        "Best Macro Shots — 5 stars",
        expr=[["+", cr.BASE_COLLECTED]],
        filters={"styles": ["macro"], "stars_min": 5},
        description="The hero macro set",
    )
    assert dc.tag == "best_macro_shots_5_stars"
    assert dc.description == "The hero macro set"
    # Round-trip through query_raw.
    rows = lg.dynamic_collections()
    assert len(rows) == 1 and rows[0].id == dc.id
    assert json.loads(rows[0].expr_json) == [["+", "collected"]]
    assert json.loads(rows[0].filters_json) == {
        "styles": ["macro"], "stars_min": 5,
    }
    store.close()


def test_create_dc_rejects_reserved_tag(tmp_path):
    """Reserved tags (the four ladder rungs) are refused — the cross-event
    DC namespace must not shadow a base universe."""
    lg, store = _open_library(tmp_path)
    with pytest.raises(ValueError) as exc:
        lg.create_dc("Exported")           # slug → 'exported' (reserved)
    assert "reserved" in str(exc.value)
    store.close()


def test_create_dc_rejects_taken_tag(tmp_path):
    """Two cross-event DCs can't share a tag (case-blind by construction —
    slugify lowercases)."""
    lg, store = _open_library(tmp_path)
    lg.create_dc("Best macro")
    with pytest.raises(ValueError) as exc:
        lg.create_dc("BEST MACRO")
    assert "taken" in str(exc.value)
    store.close()


def test_create_dc_rejects_empty_name(tmp_path):
    """A name that slugifies to empty is rejected."""
    lg, store = _open_library(tmp_path)
    with pytest.raises(ValueError) as exc:
        lg.create_dc("   ")
    assert "empty" in str(exc.value)
    store.close()


def test_create_dc_rejects_self_referential_cycle(tmp_path):
    """A DC whose expr names itself is rejected by the write seam — the
    cycle guard runs before the row lands."""
    lg, store = _open_library(tmp_path,
                              ids=("dc-self",))
    with pytest.raises(ValueError) as exc:
        lg.create_dc(
            "loop",
            expr=[["+", {"kind": "dc", "id": "dc-self"}]])
    assert "cycle" in str(exc.value)
    store.close()


def test_create_dc_rejects_indirect_cycle(tmp_path):
    """A→B already exists; creating B→A creates the cycle and the write
    seam rejects it."""
    lg, store = _open_library(tmp_path, ids=("A", "B"))
    lg.create_dc("a", expr=[])
    # Plant the A→B edge by updating A after B exists.
    lg.create_dc("b",
                 expr=[["+", {"kind": "dc", "id": "A"}]])
    with pytest.raises(ValueError) as exc:
        lg.update_dc("A",
                     expr=[["+", {"kind": "dc", "id": "B"}]])
    assert "cycle" in str(exc.value)
    store.close()


def test_update_dc_replaces_filters_wholesale(tmp_path):
    """``filters`` REPLACES (not merges) — the cross-event catalogue's
    open-ended key set makes per-key merge brittle. Callers pass full
    next-state."""
    lg, store = _open_library(tmp_path)
    dc = lg.create_dc("x", filters={"styles": ["macro"], "iso_min": 800})
    lg.update_dc(dc.id, filters={"country_codes": ["NP"]})
    refreshed = lg.dynamic_collection(dc.id)
    assert json.loads(refreshed.filters_json) == {"country_codes": ["NP"]}
    store.close()


def test_update_dc_partial_updates_keep_other_fields(tmp_path):
    """Updating only one field leaves the rest untouched."""
    lg, store = _open_library(tmp_path)
    dc = lg.create_dc("x", expr=[["+", "collected"]],
                     filters={"styles": ["macro"]},
                     description="d1")
    lg.update_dc(dc.id, description="d2")
    refreshed = lg.dynamic_collection(dc.id)
    assert refreshed.description == "d2"
    assert json.loads(refreshed.expr_json) == [["+", "collected"]]
    assert json.loads(refreshed.filters_json) == {"styles": ["macro"]}
    store.close()


def test_update_dc_missing_raises_keyerror(tmp_path):
    lg, store = _open_library(tmp_path)
    with pytest.raises(KeyError):
        lg.update_dc("nope", description="x")
    store.close()


def test_rename_dc_slugifies_and_keeps_id(tmp_path):
    lg, store = _open_library(tmp_path)
    dc = lg.create_dc("Original")
    new = lg.rename_dc(dc.id, "Renamed — Better")
    assert new.id == dc.id and new.tag == "renamed_better"
    assert lg.dc_by_tag("renamed_better").id == dc.id
    assert lg.dc_by_tag("original") is None
    store.close()


def test_rename_dc_rejects_taken_tag(tmp_path):
    lg, store = _open_library(tmp_path)
    lg.create_dc("Alpha")
    beta = lg.create_dc("Beta")
    with pytest.raises(ValueError) as exc:
        lg.rename_dc(beta.id, "Alpha")
    assert "taken" in str(exc.value)
    store.close()


def test_delete_dc_drops_row(tmp_path):
    lg, store = _open_library(tmp_path)
    dc = lg.create_dc("doomed")
    lg.delete_dc(dc.id)
    assert lg.dynamic_collection(dc.id) is None
    assert lg.dc_by_tag("doomed") is None
    store.close()


def test_dc_by_tag_is_case_blind(tmp_path):
    """``tag`` carries ``COLLATE NOCASE`` — case-blind lookup is the
    cross-event glue (spec/61 §1.5 + spec/61 §8)."""
    lg, store = _open_library(tmp_path)
    lg.create_dc("MyDc")
    assert lg.dc_by_tag("mydc") is not None
    assert lg.dc_by_tag("MYDC") is not None
    store.close()


# --------------------------------------------------------------------------- #
# Resolution + probes
# --------------------------------------------------------------------------- #


def test_resolve_dc_returns_tuple_pairs(tmp_path):
    lg, store = _open_library(tmp_path)
    _seed_global_items(store)
    pairs = lg.resolve_dc([["+", cr.BASE_EXPORTED]])
    assert set(pairs) == {("A", "a3"), ("B", "b1")}
    store.close()


def test_resolve_dc_keys_returns_packed_strings(tmp_path):
    """The packed-key variant is what callers use when feeding the result
    into another resolver pass without losing the encoding."""
    lg, store = _open_library(tmp_path)
    _seed_global_items(store)
    keys = lg.resolve_dc_keys([["+", cr.BASE_EXPORTED]])
    assert set(keys) == {"A::a3", "B::b1"}
    store.close()


def test_dc_probe_counts_resolved_set(tmp_path):
    lg, store = _open_library(tmp_path)
    _seed_global_items(store)
    # Picked = a2, a3, b1 (three items survived the Pick decision).
    assert lg.dc_probe([["+", cr.BASE_PICKED]]) == 3
    # Photos only drops the video (a3) → a2 + b1.
    assert lg.dc_probe([["+", cr.BASE_PICKED]], {"media_type": "photo"}) == 2
    store.close()


def test_dc_show_totals_counts_photos_videos_and_days(tmp_path):
    """One photo + one video → 1/1; two distinct days → 2 separators."""
    lg, store = _open_library(tmp_path)
    _seed_global_items(store)
    totals = lg.dc_show_totals([["+", cr.BASE_EXPORTED]])
    assert totals.photo_count == 1                # b1
    assert totals.video_count == 1                # a3
    assert totals.video_ms_total == 60_000        # a3's duration
    assert totals.separator_count == 2            # 2026-04-03 + 2025-10-15
    store.close()


def test_dc_show_totals_same_day_across_events_count_as_two(tmp_path):
    """Day buckets are per-event — same calendar day in two events still
    earns two separators because separators orient ONE event's timeline
    (spec/81 §3.1)."""
    lg, store = _open_library(tmp_path)
    # Two rows, same calendar day, different events.
    for ev in ("A", "B"):
        store.upsert(um.GlobalItem(
            event_uuid=ev, item_id="x", synced_at=NOW,
            capture_time="2026-04-01T10:00:00", kind="photo",
            has_export=True,
        ))
    totals = lg.dc_show_totals([["+", cr.BASE_EXPORTED]])
    assert totals.separator_count == 2
    store.close()


def test_dc_show_totals_zero_capture_contributes_no_day(tmp_path):
    """An item with no ``capture_time`` doesn't fabricate a separator."""
    lg, store = _open_library(tmp_path)
    store.upsert(um.GlobalItem(
        event_uuid="A", item_id="x", synced_at=NOW,
        kind="photo", has_export=True,
    ))
    totals = lg.dc_show_totals([["+", cr.BASE_EXPORTED]])
    assert totals.photo_count == 1 and totals.separator_count == 0
    store.close()


def test_dc_show_totals_empty_returns_zero_totals(tmp_path):
    lg, store = _open_library(tmp_path)
    totals = lg.dc_show_totals([["+", cr.BASE_EXPORTED]])
    assert totals.photo_count == 0 and totals.separator_count == 0
    store.close()


# --------------------------------------------------------------------------- #
# Operand + facet inventories
# --------------------------------------------------------------------------- #


def test_dc_operand_inventory_starts_with_four_ladder_rungs(tmp_path):
    """The cross-event dialog offers FOUR base operands (the ladder)
    where event scope offers ONE — the spec/81 §2.1 surface widening."""
    lg, store = _open_library(tmp_path)
    inv = lg.dc_operand_inventory()
    base = [e for e in inv if e["kind"] == "base"]
    assert [e["tag"] for e in base] == [
        "collected", "picked", "edited", "exported",
    ]
    store.close()


def test_dc_operand_inventory_lists_saved_filters_after_ladder(tmp_path):
    """Existing cross-event DCs land after the ladder, oldest first."""
    lg, store = _open_library(tmp_path, ids=("alpha", "beta"))
    lg.create_dc("Alpha set")
    lg.create_dc("Beta set")
    inv = lg.dc_operand_inventory()
    dcs = [e for e in inv if e["kind"] == "dc"]
    assert [e["tag"] for e in dcs] == ["alpha_set", "beta_set"]
    # Each DC operand has the typed-ref shape the dialog drops into expr_json.
    assert dcs[0]["operand"] == {"kind": "dc", "id": "alpha", "tag": "alpha_set"}
    store.close()


def test_available_classifications_returns_value_count_pairs(tmp_path):
    """spec/83 §5 — each inventory returns ``(value, count)`` pairs ordered
    most-used-first. ``landscape`` has two items; macro/portrait/wildlife
    one each (tie-break by value alphabetically)."""
    lg, store = _open_library(tmp_path)
    _seed_global_items(store)
    assert lg.available_classifications() == [
        ("landscape", 2), ("macro", 1), ("portrait", 1), ("wildlife", 1),
    ]
    store.close()


def test_available_cameras_ordered_by_use(tmp_path):
    """``Pana+G9M2`` appears on four items, ``Pana+S5`` on one — the heavy
    hitter leads the list so the picker's main / occasional split (spec/83
    §4) reads off the tail."""
    lg, store = _open_library(tmp_path)
    _seed_global_items(store)
    assert lg.available_cameras() == [("Pana+G9M2", 4), ("Pana+S5", 1)]
    store.close()


def test_available_lenses_ordered_by_use(tmp_path):
    """All four lenses have one photo each — tied counts fall back to value
    alphabetic order so the result is deterministic."""
    lg, store = _open_library(tmp_path)
    _seed_global_items(store)
    assert lg.available_lenses() == [
        ("LEICA 45mm", 1), ("LUMIX 100-300", 1),
        ("LUMIX 24-105", 1), ("Lumix 42.5", 1),
    ]
    store.close()


def test_available_country_codes_ordered_by_use(tmp_path):
    """CR three items, NP two."""
    lg, store = _open_library(tmp_path)
    _seed_global_items(store)
    assert lg.available_country_codes() == [("CR", 3), ("NP", 2)]
    store.close()


def test_available_cities_ordered_by_use(tmp_path):
    lg, store = _open_library(tmp_path)
    _seed_global_items(store)
    assert lg.available_cities() == [
        ("Everest Base Camp", 1), ("La Fortuna", 1), ("Manuel Antonio", 1),
        ("Monteverde", 1), ("Namche Bazaar", 1),
    ]
    store.close()


def test_available_color_labels_ordered_by_use(tmp_path):
    lg, store = _open_library(tmp_path)
    _seed_global_items(store)
    assert lg.available_color_labels() == [("green", 1), ("red", 1)]
    store.close()


def test_available_cameras_counts_repeats(tmp_path):
    """A camera used many times reports the right ``COUNT(*)`` (not
    ``DISTINCT`` — that would always be 1)."""
    lg, store = _open_library(tmp_path)
    for i in range(7):
        store.upsert(um.GlobalItem(
            event_uuid="E", item_id=f"i{i}", synced_at=NOW,
            camera_id="Pana+S5"))
    store.upsert(um.GlobalItem(
        event_uuid="E", item_id="x", synced_at=NOW,
        camera_id="Pana+G9M2"))
    assert lg.available_cameras() == [("Pana+S5", 7), ("Pana+G9M2", 1)]
    store.close()


def test_facet_inventory_dispatches_by_filter_key(tmp_path):
    """spec/83 §5 — the dialog's lazy seam: each ``filters_json`` key maps
    to its inventory. Unknown keys (future tags / people) return ``[]``."""
    lg, store = _open_library(tmp_path)
    _seed_global_items(store)
    assert lg.facet_inventory("styles") == lg.available_classifications()
    assert lg.facet_inventory("camera_ids") == lg.available_cameras()
    assert lg.facet_inventory("lens_models") == lg.available_lenses()
    assert lg.facet_inventory("country_codes") == lg.available_country_codes()
    assert lg.facet_inventory("cities") == lg.available_cities()
    assert lg.facet_inventory("color_labels") == lg.available_color_labels()
    # Unknown key — forward-compat for spec/32 tags / people roadmap.
    assert lg.facet_inventory("tags") == []
    store.close()


def test_event_uuids_in_projection(tmp_path):
    lg, store = _open_library(tmp_path)
    _seed_global_items(store)
    assert lg.event_uuids_in_projection() == ["A", "B", "C"]
    store.close()


# --------------------------------------------------------------------------- #
# Slice 7 — gear fingerprint + classifier hint
# --------------------------------------------------------------------------- #


def test_gear_fingerprint_changes_when_gear_changes(tmp_path):
    """spec/85 §5 — bumping any row's is_active / preferred_genres flips
    the fingerprint so the classifier's persisted rules_version stamp
    rolls and untouched items re-classify on the next pass."""
    lg, store = _open_library(tmp_path)
    empty = lg.gear_fingerprint()
    lg.set_gear_active("camera", "Pana+G9M2", True)
    after_active = lg.gear_fingerprint()
    assert after_active != empty
    lg.set_gear_genres("camera", "Pana+G9M2", ["wildlife"])
    after_genres = lg.gear_fingerprint()
    assert after_genres != after_active
    # Setting the same value again is a no-op for the fingerprint.
    lg.set_gear_genres("camera", "Pana+G9M2", ["wildlife"])
    assert lg.gear_fingerprint() == after_genres
    store.close()


def test_gear_fingerprint_empty_when_no_rows(tmp_path):
    """The fingerprint is deterministic across runs — empty profile
    always hashes to the same value."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    lg1, store1 = _open_library(tmp_path / "a")
    lg2, store2 = _open_library(tmp_path / "b")
    assert lg1.gear_fingerprint() == lg2.gear_fingerprint()
    store1.close(); store2.close()


def test_make_gear_hint_lens_beats_camera(tmp_path):
    """spec/85 §6 lean — when both the camera and lens have preferred
    genres, the lens wins (it's the more specific optic)."""
    lg, store = _open_library(tmp_path)
    lg.set_gear_genres("camera", "Pana+G9M2", ["wildlife"])
    lg.set_gear_genres("lens", "LEICA 45mm", ["macro"])
    hint = lg.make_gear_hint(camera_id="Pana+G9M2", lens_model="LEICA 45mm")
    result = hint(object())
    assert result is not None
    scenario, confidence = result
    from core.vocabulary import Scenario
    assert scenario == Scenario.MACRO
    assert confidence == lg.USER_GEAR_HINT_CONFIDENCE
    store.close()


def test_make_gear_hint_camera_only_falls_through_to_camera(tmp_path):
    """No lens row → the camera's preferred genres take over."""
    lg, store = _open_library(tmp_path)
    lg.set_gear_genres("camera", "Pana+G9M2", ["wildlife"])
    hint = lg.make_gear_hint(
        camera_id="Pana+G9M2", lens_model="Unknown-Lens")
    from core.vocabulary import Scenario
    assert hint(object())[0] == Scenario.WILDLIFE
    store.close()


def test_make_gear_hint_returns_none_when_no_match(tmp_path):
    """Untagged gear → hint silent; classifier falls through to GENERAL."""
    lg, store = _open_library(tmp_path)
    hint = lg.make_gear_hint(
        camera_id="Pana+G9M2", lens_model="LEICA 45mm")
    assert hint(object()) is None
    store.close()


def test_make_gear_hint_skips_unknown_genre_strings(tmp_path):
    """A genre string that doesn't map to a :class:`Scenario` value is
    logged and skipped; the hint falls through to the next candidate."""
    lg, store = _open_library(tmp_path)
    lg.set_gear_genres("lens", "LEICA 45mm", ["bogus_genre", "macro"])
    from core.vocabulary import Scenario
    hint = lg.make_gear_hint(camera_id=None, lens_model="LEICA 45mm")
    assert hint(object())[0] == Scenario.MACRO
    store.close()


def test_make_gear_hint_only_if_active_or_inactive_with_genres(tmp_path):
    """Setting "I use this" without preferred genres ≠ a hint — the user
    can flag gear active for the picker without giving the classifier any
    signal at all (the two axes are independent, spec/85 §3)."""
    lg, store = _open_library(tmp_path)
    lg.set_gear_active("camera", "Pana+G9M2", True)   # no genres set
    hint = lg.make_gear_hint(
        camera_id="Pana+G9M2", lens_model=None)
    assert hint(object()) is None
    store.close()


# --------------------------------------------------------------------------- #
# Sync triggers — delegate to global_items_sync, return its row counts
# --------------------------------------------------------------------------- #


def _seed_minimal_event(store: EventStore) -> None:
    """The smallest event the projection knows how to consume (one item,
    no decisions)."""
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO event (id, uuid, name, created_at, updated_at) "
            "VALUES (1, 'evt-X', 'Test event', ?, ?)", (NOW, NOW))
        conn.execute(
            "INSERT INTO trip_day (day_number, date, location) "
            "VALUES (1, '2026-04-01', 'La Fortuna')")
        conn.execute(
            "INSERT INTO camera (camera_id) VALUES ('cam')")
        conn.execute(
            "INSERT INTO item (id, kind, provenance, origin_relpath, sha256, "
            "byte_size, materialized_at, materialized_phase, camera_id, "
            "day_number, capture_time_raw, capture_time_corrected, "
            "tz_offset_minutes, tz_source, extras_json, created_at) "
            "VALUES ('it-1', 'photo', 'captured', 'p.jpg', ?, 1, ?, 'ingest', "
            "'cam', 1, ?, ?, -360, 'pair_picker', '{}', ?)",
            ("a" * 64, NOW, "2026-04-01T10:00:00", "2026-04-01T04:00:00", NOW))


def test_sync_event_delegates_to_global_items_sync(tmp_path):
    """The library gateway's sync delegates — counts come back, rows land
    in ``global_items`` under the right event_uuid."""
    user_store = _open_user_store(tmp_path)
    lg = LibraryGateway(user_store, now=lambda: NOW)
    event_store = EventStore.create(
        tmp_path / "evt.db", event_id="evt-X",
        app_version="test", created_at=NOW,
    )
    _seed_minimal_event(event_store)
    n = lg.sync_event(event_store=event_store,
                      event_uuid="evt-X", event_name="Test event")
    assert n == 1
    rows = user_store.query_by(um.GlobalItem, event_uuid="evt-X")
    assert len(rows) == 1 and rows[0].item_id == "it-1"
    event_store.close(); user_store.close()


def test_drop_event_removes_slice(tmp_path):
    user_store = _open_user_store(tmp_path)
    user_store.upsert(um.GlobalItem(
        event_uuid="zap", item_id="i", synced_at=NOW))
    lg = LibraryGateway(user_store, now=lambda: NOW)
    assert lg.drop_event("zap") == 1
    assert lg.drop_event("zap") == 0           # idempotent
    user_store.close()


# --------------------------------------------------------------------------- #
# Gear profile (spec/85) — slice 2 storage + repo
# --------------------------------------------------------------------------- #


def test_get_gear_profile_empty_by_default(tmp_path):
    """No rows until the user touches a camera/lens — the wizard (spec/85
    §3) is what seeds them."""
    lg, store = _open_library(tmp_path)
    assert lg.get_gear_profile() == []
    store.close()


def test_set_gear_active_upserts(tmp_path):
    """First call creates the row; subsequent calls update in place."""
    lg, store = _open_library(tmp_path)
    lg.set_gear_active("camera", "Pana+G9M2", True)
    rows = lg.get_gear_profile()
    assert len(rows) == 1
    assert rows[0].kind == "camera"
    assert rows[0].key == "Pana+G9M2"
    assert rows[0].is_active is True
    # Re-toggle the same gear → still one row, different flag.
    lg.set_gear_active("camera", "Pana+G9M2", False)
    rows = lg.get_gear_profile()
    assert len(rows) == 1 and rows[0].is_active is False
    store.close()


def test_set_gear_genres_round_trips_json(tmp_path):
    """``preferred_genres`` lands as a JSON array of strings — the slice-7
    classifier reads it through :meth:`gear_preferred_genres`."""
    lg, store = _open_library(tmp_path)
    lg.set_gear_genres("lens", "LEICA 45mm", ["macro", "portrait"])
    row = lg.gear_profile_for("lens", "LEICA 45mm")
    assert row is not None
    assert lg.gear_preferred_genres(row) == ["macro", "portrait"]
    store.close()


def test_set_gear_genres_clears_with_none(tmp_path):
    """Passing ``None`` (or ``[]``) clears the tag — the row stays around
    in case ``is_active`` is set, but its hint contribution disappears."""
    lg, store = _open_library(tmp_path)
    lg.set_gear_genres("lens", "LEICA 45mm", ["macro"])
    lg.set_gear_genres("lens", "LEICA 45mm", None)
    row = lg.gear_profile_for("lens", "LEICA 45mm")
    assert row is not None
    assert row.preferred_genres is None
    assert lg.gear_preferred_genres(row) == []
    store.close()


def test_set_gear_active_preserves_genres(tmp_path):
    """spec/85 §3 — the two toggles are independent. Toggling active must
    not wipe a genre tag the user set earlier."""
    lg, store = _open_library(tmp_path)
    lg.set_gear_genres("lens", "LEICA 45mm", ["macro"])
    lg.set_gear_active("lens", "LEICA 45mm", True)
    row = lg.gear_profile_for("lens", "LEICA 45mm")
    assert row.is_active is True
    assert lg.gear_preferred_genres(row) == ["macro"]
    store.close()


def test_set_gear_genres_preserves_is_active(tmp_path):
    """The inverse: setting genres must not flip ``is_active`` back to 0
    on an active gear row."""
    lg, store = _open_library(tmp_path)
    lg.set_gear_active("camera", "Pana+G9M2", True)
    lg.set_gear_genres("camera", "Pana+G9M2", ["wildlife"])
    row = lg.gear_profile_for("camera", "Pana+G9M2")
    assert row.is_active is True
    assert lg.gear_preferred_genres(row) == ["wildlife"]
    store.close()


def test_gear_profile_for_unknown_returns_none(tmp_path):
    lg, store = _open_library(tmp_path)
    assert lg.gear_profile_for("camera", "missing") is None
    store.close()


def test_gear_profile_invalid_kind_raises(tmp_path):
    """``kind`` is closed to ``{camera, lens}`` — the gateway gates app-side
    so callers get a clear error before the SQL CHECK fires."""
    lg, store = _open_library(tmp_path)
    with pytest.raises(ValueError):
        lg.set_gear_active("body", "x", True)
    with pytest.raises(ValueError):
        lg.set_gear_genres("optic", "x", [])
    with pytest.raises(ValueError):
        lg.gear_profile_for("widget", "x")
    store.close()


def test_gear_profile_empty_key_raises(tmp_path):
    """Empty ``key`` is rejected — the user-store would silently accept it,
    but it can't match anything in ``global_items`` so the row is dead
    weight."""
    lg, store = _open_library(tmp_path)
    with pytest.raises(ValueError):
        lg.set_gear_active("camera", "", True)
    with pytest.raises(ValueError):
        lg.set_gear_genres("lens", "", ["macro"])
    store.close()


def test_get_gear_profile_returns_sorted(tmp_path):
    """The wizard reads the full list (spec/85 §3); sort is deterministic
    so the UI doesn't reshuffle between opens."""
    lg, store = _open_library(tmp_path)
    lg.set_gear_active("lens", "Zuiko 50mm", True)
    lg.set_gear_active("camera", "Pana+S5", True)
    lg.set_gear_active("camera", "Pana+G9M2", True)
    lg.set_gear_active("lens", "LEICA 45mm", True)
    rows = lg.get_gear_profile()
    assert [(r.kind, r.key) for r in rows] == [
        ("camera", "Pana+G9M2"), ("camera", "Pana+S5"),
        ("lens", "LEICA 45mm"), ("lens", "Zuiko 50mm"),
    ]
    store.close()


def test_gear_preferred_genres_tolerates_malformed_json(tmp_path):
    """A malformed payload (would happen only if the file is hand-edited)
    collapses to ``[]`` so the classifier (slice 7) doesn't crash the pass."""
    lg, store = _open_library(tmp_path)
    row = um.GearProfile(
        kind="lens", key="X", updated_at=NOW,
        preferred_genres='{"not": "a list"}')
    assert lg.gear_preferred_genres(row) == []
    bad = um.GearProfile(
        kind="lens", key="Y", updated_at=NOW,
        preferred_genres="not json at all")
    assert lg.gear_preferred_genres(bad) == []


def test_reconcile_all_runs_known_events_and_drops_stale(tmp_path):
    """Full reconcile: open every known event, sync it, then drop slices
    for events that aren't in known_events anymore."""
    user_store = _open_user_store(tmp_path)
    event_store = EventStore.create(
        tmp_path / "evt.db", event_id="evt-X",
        app_version="test", created_at=NOW,
    )
    _seed_minimal_event(event_store)
    event_store.close()
    # Seed a stale slice — an event that no longer exists.
    user_store.upsert(um.GlobalItem(
        event_uuid="stale", item_id="i", synced_at=NOW))
    lg = LibraryGateway(user_store, now=lambda: NOW)

    def _open(uuid):
        return EventStore.open(tmp_path / "evt.db") if uuid == "evt-X" else None

    summary = lg.reconcile_all(
        open_event_store=_open,
        known_events=[("evt-X", "Test event")],
    )
    assert summary == {"synced": 1, "dropped": 1, "skipped": 0}
    assert user_store.query_by(um.GlobalItem, event_uuid="stale") == []
    assert user_store.query_by(um.GlobalItem, event_uuid="evt-X")
    user_store.close()
