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
