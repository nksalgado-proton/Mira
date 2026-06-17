"""spec/81 Phase 2 — cross-event DC resolution against ``global_items``.

Drives :mod:`mira.gateway.cross_event_resolver` against a hand-seeded
``global_items`` set + ``saved_filter`` rows. The Phase-1 resolver itself
(``core.collection_resolver``) is unchanged — Phase 2 only swaps the
accessors, so these tests prove the cross-event SEAM (ladder rungs, filter
catalogue, saved_filter operand lookup, key encoding) without re-testing the
algebra.
"""
from __future__ import annotations

import json

import pytest

from core import collection_resolver as cr
from mira.gateway import cross_event_resolver as cev
from mira.user_store import models as um
from mira.user_store.repo import UserStore


# --------------------------------------------------------------------------- #
# Fixtures — a 2-event, 6-item universe spanning the ladder + every facet
# --------------------------------------------------------------------------- #


NOW = "2026-06-16T00:00:00+00:00"


def _open_user_store(tmp_path) -> UserStore:
    return UserStore.create(
        tmp_path / "mira.db",
        app_version="test",
        created_at=NOW,
    )


def _seed(store: UserStore) -> None:
    """Two events. Event A is a Costa Rica trip; Event B is a Nepal trek.
    Together they exercise every spec/32 §2 facet + every ladder rung."""
    rows = [
        # Event A — Costa Rica, CR.
        # Collected only (no decision, no export); macro; ISO 400.
        um.GlobalItem(
            event_uuid="A", item_id="a1", synced_at=NOW,
            event_name="Costa Rica",
            capture_time="2026-04-01T10:00:00",
            kind="photo", classification="macro",
            iso=400, aperture_f=2.8, shutter_speed_s=0.004,
            focal_length_mm=45.0, flash_fired=0,
            lens_model="LEICA 45mm", camera_id="Pana+G9M2",
            country="Costa Rica", country_code="CR", day_city="La Fortuna",
            stars=3, has_export=False,
        ),
        # Picked but not edited. Wildlife. High ISO.
        um.GlobalItem(
            event_uuid="A", item_id="a2", synced_at=NOW,
            event_name="Costa Rica",
            capture_time="2026-04-02T08:00:00",
            kind="photo", classification="wildlife",
            iso=1600, aperture_f=4.0, shutter_speed_s=0.001,
            focal_length_mm=200.0, flash_fired=0,
            lens_model="LUMIX 100-300", camera_id="Pana+G9M2",
            country="Costa Rica", country_code="CR", day_city="Monteverde",
            stars=4, color_label="green",
            pick_state="picked", has_export=False,
        ),
        # Edited but not exported. Macro. Mid ISO. Flash.
        um.GlobalItem(
            event_uuid="A", item_id="a3", synced_at=NOW,
            event_name="Costa Rica",
            capture_time="2026-04-03T20:00:00",
            kind="photo", classification="macro",
            iso=800, aperture_f=8.0, shutter_speed_s=0.01,
            focal_length_mm=45.0, flash_fired=1,
            lens_model="LEICA 45mm", camera_id="Pana+G9M2",
            country="Costa Rica", country_code="CR", day_city="Tortuguero",
            stars=5, flag=1,
            pick_state="picked", edit_state="picked", has_export=False,
        ),
        # Exported. Video. Wide-open.
        um.GlobalItem(
            event_uuid="A", item_id="a4", synced_at=NOW,
            event_name="Costa Rica",
            capture_time="2026-04-04T15:00:00",
            kind="video", classification="landscape",
            iso=200, aperture_f=1.8, shutter_speed_s=0.04,
            focal_length_mm=24.0, flash_fired=0,
            lens_model="LUMIX 24-105", camera_id="Pana+S5",
            country="Costa Rica", country_code="CR", day_city="Manuel Antonio",
            stars=5, has_export=True,
            duration_ms=60_000,
            pick_state="picked", edit_state="picked",
        ),
        # Event B — Nepal, NP.
        # Picked + edited + exported. Portrait. Long lens.
        um.GlobalItem(
            event_uuid="B", item_id="b1", synced_at=NOW,
            event_name="Nepal trek",
            capture_time="2025-10-15T07:30:00",
            kind="photo", classification="portrait",
            iso=200, aperture_f=2.0, shutter_speed_s=0.002,
            focal_length_mm=85.0, flash_fired=0,
            lens_model="Lumix 42.5", camera_id="Pana+G9M2",
            country="Nepal", country_code="NP", day_city="Namche Bazaar",
            stars=5, color_label="red",
            pick_state="picked", edit_state="picked", has_export=True,
        ),
        # Collected only. Landscape. High focal. Low light.
        um.GlobalItem(
            event_uuid="B", item_id="b2", synced_at=NOW,
            event_name="Nepal trek",
            capture_time="2025-10-16T17:30:00",
            kind="photo", classification="landscape",
            iso=3200, aperture_f=11.0, shutter_speed_s=1.0,
            focal_length_mm=24.0, flash_fired=0,
            lens_model="LUMIX 24-105", camera_id="Pana+G9M2",
            country="Nepal", country_code="NP", day_city="Everest Base Camp",
            stars=2,
        ),
    ]
    for r in rows:
        store.upsert(r)


def _make_sf(store: UserStore, **kw) -> um.SavedFilter:
    sf = um.SavedFilter(
        id=kw.pop("id"), tag=kw.pop("tag"),
        created_at=NOW, updated_at=NOW,
        expr_json=json.dumps(kw.pop("expr", [])),
        filters_json=json.dumps(kw.pop("filters", {})),
        **kw,
    )
    store.upsert(sf)
    return sf


# --------------------------------------------------------------------------- #
# Key encoding
# --------------------------------------------------------------------------- #


def test_pack_key_roundtrips():
    """``pack_key`` / ``unpack_key`` are inverse."""
    key = cev.pack_key("evt-1", "i-100")
    assert cev.unpack_key(key) == ("evt-1", "i-100")


def test_unpack_key_tolerates_malformed():
    """An unpackable string yields ``("", key)`` — never raises."""
    assert cev.unpack_key("not-a-key") == ("", "not-a-key")


# --------------------------------------------------------------------------- #
# Ladder rungs — every spec/81 §2.1 base token maps to a column predicate
# --------------------------------------------------------------------------- #


def test_collected_rung_returns_every_row(tmp_path):
    """``#collected`` = every projected item, regardless of decision state."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_COLLECTED]])
    assert {cev.unpack_key(k) for k in keys} == {
        ("A", "a1"), ("A", "a2"), ("A", "a3"), ("A", "a4"),
        ("B", "b1"), ("B", "b2"),
    }
    store.close()


def test_picked_rung_filters_by_pick_state(tmp_path):
    """``#picked`` = ``pick_state = 'picked'`` — only items that survived
    the Pick phase decision."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_PICKED]])
    assert {cev.unpack_key(k) for k in keys} == {
        ("A", "a2"), ("A", "a3"), ("A", "a4"), ("B", "b1"),
    }
    store.close()


def test_edited_rung_filters_by_edit_state(tmp_path):
    """``#edited`` = ``edit_state = 'picked'`` (spec/61 §1.1, edited ≠
    exported). Items in Edit but not yet exported still count."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_EDITED]])
    assert {cev.unpack_key(k) for k in keys} == {
        ("A", "a3"), ("A", "a4"), ("B", "b1"),
    }
    store.close()


def test_exported_rung_filters_by_has_export(tmp_path):
    """``#exported`` = lineage-backed items (``has_export = 1``). The Phase-1
    base universe with cross-event spelling."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_EXPORTED]])
    assert {cev.unpack_key(k) for k in keys} == {("A", "a4"), ("B", "b1")}
    store.close()


def test_unknown_base_token_returns_empty(tmp_path):
    """An unknown token is treated as a deleted operand (graceful shrink —
    same rule the resolver uses for a missing DC ref)."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(store, [["+", "not-a-rung"]])
    assert keys == []
    store.close()


# --------------------------------------------------------------------------- #
# Set algebra across rungs — the same engine, cross-event
# --------------------------------------------------------------------------- #


def test_difference_across_rungs(tmp_path):
    """``#picked - #exported`` = items in flight (picked + maybe edited)
    that never shipped — the spec/61 §8 "what didn't finish" pitch."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store,
        [["+", cr.BASE_PICKED], ["-", cr.BASE_EXPORTED]])
    assert {cev.unpack_key(k) for k in keys} == {
        ("A", "a2"), ("A", "a3"),
    }
    store.close()


def test_intersection_across_rungs(tmp_path):
    """``#edited ∩ #exported`` = the both-edited-AND-shipped subset."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store,
        [["+", cr.BASE_EDITED], ["&", cr.BASE_EXPORTED]])
    assert {cev.unpack_key(k) for k in keys} == {
        ("A", "a4"), ("B", "b1"),
    }
    store.close()


# --------------------------------------------------------------------------- #
# Filter catalogue — spec/32 §2
# --------------------------------------------------------------------------- #


def test_styles_filter_combinable(tmp_path):
    """Multiple styles narrow as ``IN`` — combinable per spec/61 §2 step 3."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_COLLECTED]],
        {"styles": ["macro", "wildlife"]})
    assert {cev.unpack_key(k) for k in keys} == {
        ("A", "a1"), ("A", "a2"), ("A", "a3"),
    }
    store.close()


def test_media_type_photo_excludes_video(tmp_path):
    """``media_type='photo'`` drops the lone video (a4)."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_EXPORTED]],
        {"media_type": "photo"})
    assert {cev.unpack_key(k) for k in keys} == {("B", "b1")}
    store.close()


def test_iso_range_narrows_to_high_iso(tmp_path):
    """``iso_min`` realises the spec/32 §2d high-ISO query — the "all my
    shots taken with flash, ISO ≥ 1600" filter."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_COLLECTED]], {"iso_min": 1600})
    assert {cev.unpack_key(k) for k in keys} == {("A", "a2"), ("B", "b2")}
    store.close()


def test_aperture_max_narrows_to_wide_open(tmp_path):
    """``aperture_max`` realises "wide-open glass" — apertures ≤ 2.0."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_COLLECTED]], {"aperture_max": 2.0})
    assert {cev.unpack_key(k) for k in keys} == {("A", "a4"), ("B", "b1")}
    store.close()


def test_shutter_min_narrows_to_long_exposure(tmp_path):
    """``shutter_min`` realises "long exposures (≥ 1s)" — the spec/32 §2d
    "long exposure" query."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_COLLECTED]], {"shutter_min": 1.0})
    assert {cev.unpack_key(k) for k in keys} == {("B", "b2")}
    store.close()


def test_focal_range_narrows_to_macro_range(tmp_path):
    """``focal_min`` + ``focal_max`` together fence a focal-length range —
    spec/32 §2d "macro range 90-110mm" example, here 40-50mm."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_COLLECTED]],
        {"focal_min": 40, "focal_max": 50})
    assert {cev.unpack_key(k) for k in keys} == {
        ("A", "a1"), ("A", "a3"),
    }
    store.close()


def test_flash_fired_narrows_to_flash_shots(tmp_path):
    """``flash_fired=True`` narrows to actual flash captures."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_COLLECTED]], {"flash_fired": True})
    assert {cev.unpack_key(k) for k in keys} == {("A", "a3")}
    store.close()


def test_lens_models_narrows_to_lens(tmp_path):
    """``lens_models`` realises the "per-lens collection" facet."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_COLLECTED]],
        {"lens_models": ["LEICA 45mm"]})
    assert {cev.unpack_key(k) for k in keys} == {
        ("A", "a1"), ("A", "a3"),
    }
    store.close()


def test_camera_ids_narrows_to_specific_body(tmp_path):
    """``camera_ids`` realises "shots from this body" — the deprecated
    Phase-1 camera filter resurrected for cross-event."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_COLLECTED]],
        {"camera_ids": ["Pana+S5"]})
    assert {cev.unpack_key(k) for k in keys} == {("A", "a4")}
    store.close()


def test_temporal_range_narrows_to_year(tmp_path):
    """``capture_from`` + ``capture_to`` realise the spec/32 §2b
    "BETWEEN 2010-01-01 AND 2025-12-31" filter."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_COLLECTED]],
        {"capture_from": "2026-01-01", "capture_to": "2026-12-31"})
    assert {cev.unpack_key(k) for k in keys} == {
        ("A", "a1"), ("A", "a2"), ("A", "a3"), ("A", "a4"),
    }
    store.close()


def test_country_codes_narrows_to_destination(tmp_path):
    """``country_codes`` realises "best shots from Nepal" (spec/32 §1)."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_COLLECTED]],
        {"country_codes": ["NP"]})
    assert {cev.unpack_key(k) for k in keys} == {
        ("B", "b1"), ("B", "b2"),
    }
    store.close()


def test_stars_min_narrows_to_top_rated(tmp_path):
    """``stars_min`` realises "5-star photos" — the spec/32 §1 example."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_COLLECTED]],
        {"stars_min": 5})
    assert {cev.unpack_key(k) for k in keys} == {
        ("A", "a3"), ("A", "a4"), ("B", "b1"),
    }
    store.close()


def test_color_labels_narrows_to_labelled(tmp_path):
    """``color_labels`` realises the LRC-compatible color-label facet."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_COLLECTED]],
        {"color_labels": ["green"]})
    assert {cev.unpack_key(k) for k in keys} == {("A", "a2")}
    store.close()


def test_flag_narrows_to_portfolio(tmp_path):
    """``flag=True`` narrows to portfolio-flagged items (the locked name —
    spec/32 §2a, was ``pick``)."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_COLLECTED]], {"flag": True})
    assert {cev.unpack_key(k) for k in keys} == {("A", "a3")}
    store.close()


def test_filters_compose_spec32_acceptance_query(tmp_path):
    """The spec/32 §1 "best photos from Nepal — wide-open glass, no flash,
    rated ≥ 4" query, in cross-event filter shape."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_COLLECTED]],
        {"country_codes": ["NP"], "aperture_max": 2.8,
         "flash_fired": False, "stars_min": 4})
    assert {cev.unpack_key(k) for k in keys} == {("B", "b1")}
    store.close()


def test_filters_tolerate_unknown_keys(tmp_path):
    """Unknown filter keys are ignored (forward-compat — a future facet
    added to filters_json doesn't break older readers)."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_EXPORTED]],
        {"face_set_id": "future-feature"})        # not yet implemented
    assert {cev.unpack_key(k) for k in keys} == {("A", "a4"), ("B", "b1")}
    store.close()


def test_filters_tolerate_malformed_lists(tmp_path):
    """A scalar where a list is expected is treated as "no constraint"."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_EXPORTED]],
        {"country_codes": "NP"})                   # str, not list
    assert {cev.unpack_key(k) for k in keys} == {("A", "a4"), ("B", "b1")}
    store.close()


# --------------------------------------------------------------------------- #
# Chronological ordering (spec/61 §5.1 show order — capture_time then id)
# --------------------------------------------------------------------------- #


def test_apply_filters_orders_chronologically(tmp_path):
    """Results are returned in capture-time order (then item_id tie-break) —
    the cross-event flat grid reads this directly."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_COLLECTED]])
    times = [cev.unpack_key(k) for k in keys]
    assert times == [
        ("B", "b1"), ("B", "b2"),                  # 2025-10-15, 2025-10-16
        ("A", "a1"), ("A", "a2"), ("A", "a3"), ("A", "a4"),  # 2026-04-01..04
    ]
    store.close()


# --------------------------------------------------------------------------- #
# SavedFilter operand — the cross-event DC home
# --------------------------------------------------------------------------- #


def test_saved_filter_operand_by_id(tmp_path):
    """A DC operand pointing at a saved_filter row by id resolves through
    its own expr + filters."""
    store = _open_user_store(tmp_path)
    _seed(store)
    _make_sf(store, id="sf-1", tag="best_macro",
             expr=[["+", cr.BASE_COLLECTED]],
             filters={"styles": ["macro"], "stars_min": 5})
    keys = cev.resolve_cross_event(
        store,
        [["+", {"kind": "dc", "id": "sf-1"}]])
    assert {cev.unpack_key(k) for k in keys} == {("A", "a3")}
    store.close()


def test_saved_filter_operand_by_tag_fallback(tmp_path):
    """When id is None / unknown, fall back to tag lookup (the cross-event
    tag is the public handle)."""
    store = _open_user_store(tmp_path)
    _seed(store)
    _make_sf(store, id="sf-2", tag="five_star_picks",
             expr=[["+", cr.BASE_COLLECTED]],
             filters={"stars_min": 5})
    keys = cev.resolve_cross_event(
        store,
        [["+", {"kind": "dc", "tag": "five_star_picks"}]])
    assert {cev.unpack_key(k) for k in keys} == {
        ("A", "a3"), ("A", "a4"), ("B", "b1"),
    }
    store.close()


def test_saved_filter_operand_missing_contributes_nothing(tmp_path):
    """A DC operand for a deleted / never-existed saved_filter contributes
    the empty set (graceful shrink, same rule event-scope applies)."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store,
        [["+", cr.BASE_EXPORTED],
         ["-", {"kind": "dc", "id": "gone"}]])
    # The minus over an empty set is a no-op; the result is just #exported.
    assert {cev.unpack_key(k) for k in keys} == {("A", "a4"), ("B", "b1")}
    store.close()


def test_nested_saved_filter_grouping(tmp_path):
    """A saved_filter operand IS grouping (spec/81 §2 "no parentheses,
    nest a DC to group"). The nested DC's own filters apply before it
    composes upward."""
    store = _open_user_store(tmp_path)
    _seed(store)
    _make_sf(store, id="sf-macro", tag="macro_set",
             expr=[["+", cr.BASE_COLLECTED]],
             filters={"styles": ["macro"]})
    # outer = macro_set ∩ #picked → the macro frames that were Picked
    keys = cev.resolve_cross_event(
        store,
        [["+", {"kind": "dc", "id": "sf-macro"}],
         ["&", cr.BASE_PICKED]])
    assert {cev.unpack_key(k) for k in keys} == {("A", "a3")}
    store.close()


def test_cross_event_cycle_guard(tmp_path):
    """A→B→A in saved_filter operands raises CycleError — the resolver's
    own guard applies cross-event, no change needed."""
    store = _open_user_store(tmp_path)
    _seed(store)
    _make_sf(store, id="A", tag="dc_a",
             expr=[["+", {"kind": "dc", "id": "B"}]])
    _make_sf(store, id="B", tag="dc_b",
             expr=[["+", {"kind": "dc", "id": "A"}]])
    with pytest.raises(cr.CycleError):
        cev.resolve_cross_event(
            store, [["+", {"kind": "dc", "id": "A"}]])
    store.close()


# --------------------------------------------------------------------------- #
# Cut operand — deferred to Item 4, empty for now
# --------------------------------------------------------------------------- #


def test_cut_operand_returns_empty_until_item_4(tmp_path):
    """Cross-event Cut operands are deferred to Item 4; the resolver treats
    the empty set as "deleted, contributes nothing" — correct behaviour
    until cross-event Cuts exist."""
    store = _open_user_store(tmp_path)
    _seed(store)
    keys = cev.resolve_cross_event(
        store,
        [["+", cr.BASE_EXPORTED],
         ["-", {"kind": "cut", "id": "future-cut"}]])
    assert {cev.unpack_key(k) for k in keys} == {("A", "a4"), ("B", "b1")}
    store.close()


# --------------------------------------------------------------------------- #
# spec/86 — event-level qualifier filters
# --------------------------------------------------------------------------- #


def _seed_event_filter_universe(store: UserStore) -> None:
    """Three events spanning every event-level dimension. Each event has
    one captured row + every spec/86 qualifier set explicitly so the test
    asserts what the predicate matches, not what happens to be default."""
    rows = [
        # Trip A — Costa Rica wildlife trip, 2024
        um.GlobalItem(
            event_uuid="A", item_id="a1", synced_at=NOW,
            kind="photo", classification="macro",
            event_type="trip", event_subtype="wildlife trip",
            experience_type="expedition_discovery",
            participants='["Solo","With Friends"]',
            event_start="2024-08-10", event_end="2024-08-15",
            has_export=True,
        ),
        um.GlobalItem(
            event_uuid="A", item_id="a2", synced_at=NOW,
            kind="photo", classification="wildlife",
            event_type="trip", event_subtype="wildlife trip",
            experience_type="expedition_discovery",
            participants='["Solo","With Friends"]',
            event_start="2024-08-10", event_end="2024-08-15",
            has_export=True,
        ),
        # Occasion B — wedding, 2025
        um.GlobalItem(
            event_uuid="B", item_id="b1", synced_at=NOW,
            kind="photo", classification="portrait",
            event_type="occasion", event_subtype="wedding",
            experience_type="milestones_traditions",
            participants='["With Family","With Kids"]',
            event_start="2025-05-12", event_end="2025-05-13",
            has_export=True,
        ),
        # Trip C — city break, 2020, mid date for overlap tests
        um.GlobalItem(
            event_uuid="C", item_id="c1", synced_at=NOW,
            kind="photo", classification="street",
            event_type="trip", event_subtype="city break",
            experience_type="urban_culture",
            participants='["Couple"]',
            event_start="2020-03-01", event_end="2020-03-08",
            has_export=True,
        ),
        # Project D — undated, used to confirm overlap silently skips
        # events that have no date information.
        um.GlobalItem(
            event_uuid="D", item_id="d1", synced_at=NOW,
            kind="photo", classification="macro",
            event_type="project", event_subtype=None,
            experience_type=None,
            participants='[]',
            event_start=None, event_end=None,
            has_export=True,
        ),
    ]
    for r in rows:
        store.upsert(r)


def test_event_types_filter_prunes_whole_events(tmp_path):
    """spec/86 §1 efficiency win: an event_type IN ('trip') discards every
    item from non-trip events before the rest of the chain runs."""
    store = _open_user_store(tmp_path)
    _seed_event_filter_universe(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_EXPORTED]],
        filters={"event_types": ["trip"]})
    # Trip A (a1+a2) + Trip C (c1) survive; B (occasion) + D (project)
    # are pruned at the event predicate.
    assert {cev.unpack_key(k) for k in keys} == {
        ("A", "a1"), ("A", "a2"), ("C", "c1"),
    }
    store.close()


def test_event_subtypes_filter_narrows_within_a_type(tmp_path):
    """A subtype filter complements an event_type narrowing — wildlife
    trips only."""
    store = _open_user_store(tmp_path)
    _seed_event_filter_universe(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_EXPORTED]],
        filters={"event_subtypes": ["wildlife trip"]})
    assert {cev.unpack_key(k) for k in keys} == {("A", "a1"), ("A", "a2")}
    store.close()


def test_experience_types_filter_multi_select(tmp_path):
    """Scope is multi (spec/86 §8): both expedition_discovery and
    urban_culture pulled in one query."""
    store = _open_user_store(tmp_path)
    _seed_event_filter_universe(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_EXPORTED]],
        filters={"experience_types":
                 ["expedition_discovery", "urban_culture"]})
    assert {cev.unpack_key(k) for k in keys} == {
        ("A", "a1"), ("A", "a2"), ("C", "c1"),
    }
    store.close()


def test_participants_filter_any_of_overlap(tmp_path):
    """spec/86 §8 lean — participants match is any-of: an event with
    [Solo, With Friends] matches a filter for [Solo] because the JSON
    array contains Solo."""
    store = _open_user_store(tmp_path)
    _seed_event_filter_universe(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_EXPORTED]],
        filters={"participants": ["Solo"]})
    assert {cev.unpack_key(k) for k in keys} == {("A", "a1"), ("A", "a2")}
    store.close()


def test_participants_filter_overlaps_multiple_categories(tmp_path):
    """Selecting two categories matches any item whose participants
    overlap with EITHER (any-of). Trip A (Solo + Friends) + Occasion B
    (Family + Kids) both match {With Friends, With Kids}."""
    store = _open_user_store(tmp_path)
    _seed_event_filter_universe(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_EXPORTED]],
        filters={"participants": ["With Friends", "With Kids"]})
    assert {cev.unpack_key(k) for k in keys} == {
        ("A", "a1"), ("A", "a2"), ("B", "b1"),
    }
    store.close()


def test_participants_filter_skips_null_and_empty(tmp_path):
    """Items with NULL or '[]' participants don't match any participant
    filter — the EXISTS(json_each) silently returns false."""
    store = _open_user_store(tmp_path)
    _seed_event_filter_universe(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_EXPORTED]],
        filters={"participants":
                 ["Solo", "With Family", "With Kids",
                  "With Friends", "Couple"]})
    # Every dated event has SOMETHING; only project D ('[]') stays out.
    assert {cev.unpack_key(k) for k in keys} == {
        ("A", "a1"), ("A", "a2"), ("B", "b1"), ("C", "c1"),
    }
    store.close()


def test_event_date_range_overlap_both_bounds(tmp_path):
    """spec/86 §5 overlap: an event qualifies if it intersects the
    requested window. A 2024–2025 window catches Trip A (Aug 2024) and
    Occasion B (May 2025) but not Trip C (March 2020) or undated D."""
    store = _open_user_store(tmp_path)
    _seed_event_filter_universe(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_EXPORTED]],
        filters={"event_from": "2024-01-01", "event_to": "2025-12-31"})
    assert {cev.unpack_key(k) for k in keys} == {
        ("A", "a1"), ("A", "a2"), ("B", "b1"),
    }
    store.close()


def test_event_date_range_overlap_only_lower_bound(tmp_path):
    """``event_from`` alone means "events ending on or after this date"."""
    store = _open_user_store(tmp_path)
    _seed_event_filter_universe(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_EXPORTED]],
        filters={"event_from": "2024-01-01"})
    # Trip A (2024-08) + Occasion B (2025-05); Trip C (2020) + undated D out.
    assert {cev.unpack_key(k) for k in keys} == {
        ("A", "a1"), ("A", "a2"), ("B", "b1"),
    }
    store.close()


def test_event_date_range_overlap_only_upper_bound(tmp_path):
    """``event_to`` alone means "events starting on or before this date"."""
    store = _open_user_store(tmp_path)
    _seed_event_filter_universe(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_EXPORTED]],
        filters={"event_to": "2021-12-31"})
    # Trip C only (March 2020); A + B start after 2021; undated D out.
    assert {cev.unpack_key(k) for k in keys} == {("C", "c1")}
    store.close()


def test_event_date_overlap_handles_straddling_boundary(tmp_path):
    """A trip that straddles the boundary still matches — overlap, not
    containment. Trip A runs Aug 10–15; a window of Aug 14 onward catches
    it via event_end >= event_from."""
    store = _open_user_store(tmp_path)
    _seed_event_filter_universe(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_EXPORTED]],
        filters={"event_from": "2024-08-14", "event_to": "2024-12-31"})
    assert {cev.unpack_key(k) for k in keys} == {("A", "a1"), ("A", "a2")}
    store.close()


def test_event_date_overlap_skips_undated_events(tmp_path):
    """Undated events (NULL event_start / event_end) never match an
    event-date filter — NULL comparisons are false, which is the right
    behaviour: no information to bound on."""
    store = _open_user_store(tmp_path)
    _seed_event_filter_universe(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_EXPORTED]],
        filters={"event_from": "1900-01-01", "event_to": "2100-12-31"})
    # Wide window catches every DATED event; undated D stays out.
    keys_set = {cev.unpack_key(k) for k in keys}
    assert ("D", "d1") not in keys_set
    assert ("A", "a1") in keys_set and ("C", "c1") in keys_set
    store.close()


def test_event_filter_composes_with_item_filter(tmp_path):
    """spec/86 — event-level + item-level predicates AND together. Trip A
    (event_type=trip) ∩ macro (item classification) = a1 only."""
    store = _open_user_store(tmp_path)
    _seed_event_filter_universe(store)
    keys = cev.resolve_cross_event(
        store, [["+", cr.BASE_EXPORTED]],
        filters={"event_types": ["trip"], "styles": ["macro"]})
    assert {cev.unpack_key(k) for k in keys} == {("A", "a1")}
    store.close()
