"""spec/83 §2 — :mod:`mira.ui.pages._filter_family` shared components.

Slice 8 extracted the dimension catalogue + the ``_ActiveFilterRow``
container so the cross-event DC dialog and the event-scope Cut dialog
can speak the same Add-filter grammar (spec/81 §2.1). These tests pin:

* the cross-event catalogue covers every spec/32 §2 filter key,
* the event-scope catalogue is the thin Style + media type subset,
* :class:`_ActiveFilterRow` emits ``remove_requested`` on ✕,
* group labels are translated to user-visible strings.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from mira.ui.pages._filter_family import (
    CROSS_EVENT_DIM_IDS,
    CROSS_EVENT_PHASE4A_DIM_IDS,
    EVENT_SCOPE_DIM_IDS,
    GROUP_CAMERA_LENS,
    GROUP_CURATORIAL,
    GROUP_EVENT,
    GROUP_ORDER,
    GROUP_SETTINGS,
    GROUP_WHEN_WHERE,
    INDEXING_GATED_DIM_IDS,
    FilterDimension,
    _ActiveFilterRow,
    build_catalogue_subset,
    build_cross_event_catalogue,
    build_cross_event_phase4a_catalogue,
    build_event_scope_catalogue,
    group_label,
)


class _StubHost:
    """Minimal host the catalogue lambdas can call against — we never
    invoke the factories in this suite, but the catalogue builders need
    the methods to exist (the lambdas only close over them)."""

    def _register_facet(self, w):
        return w

    def _make_multi(self, key):
        return SimpleNamespace(_key=key)

    def _make_single(self, key, options):
        return SimpleNamespace(_key=key, _opts=options)

    def _make_range(self, *args, **kwargs):
        return SimpleNamespace(_min_key=kwargs.get("min_key", args[0]))

    def _make_stars_min(self):
        return SimpleNamespace(_key="stars_min")

    def _make_date_range(self):
        return SimpleNamespace()


# --------------------------------------------------------------------------- #
# Catalogue shapes
# --------------------------------------------------------------------------- #


def test_cross_event_catalogue_has_20_dimensions_in_order(qapp):
    """spec/83 §2 + spec/86 — 15 base dimensions + 5 event-level
    (event_type / event_subtype / scope / participants / event_date)."""
    cat = build_cross_event_catalogue(_StubHost())
    assert tuple(cat.keys()) == CROSS_EVENT_DIM_IDS
    assert len(cat) == 20


def test_cross_event_catalogue_covers_every_filter_key(qapp):
    """Every ``filters_json`` key the DC dialog reads has exactly one
    owning dimension — round-trip safety for rehydrate."""
    cat = build_cross_event_catalogue(_StubHost())
    keys_to_dim = {}
    for dim_id, dim in cat.items():
        for k in dim.filter_keys:
            assert k not in keys_to_dim, f"{k} owned by two dims"
            keys_to_dim[k] = dim_id
    assert set(keys_to_dim) == {
        "styles", "media_type", "stars_min", "color_labels", "flag",
        "camera_ids", "lens_models", "flash_fired",
        "iso_min", "iso_max",
        "aperture_min", "aperture_max",
        "shutter_min", "shutter_max",
        "focal_min", "focal_max",
        "capture_from", "capture_to",
        "country_codes", "cities",
        # spec/86 — event-level qualifiers + derived span.
        "event_types", "event_subtypes", "experience_types",
        "participants",
        "event_from", "event_to",
    }


def test_event_group_lands_between_curatorial_and_camera_lens(qapp):
    """spec/86 §6 — Event group slots between Curatorial and Camera & lens
    in the menu order so the event predicate appears as a natural narrowing
    pass before the EXIF / hardware facets."""
    from mira.ui.pages._filter_family import GROUP_EVENT
    cat = build_cross_event_catalogue(_StubHost())
    event_dims = [d for d in cat.values() if d.group == GROUP_EVENT]
    assert {d.dim_id for d in event_dims} == {
        "event_type", "event_subtype", "scope",
        "participants", "event_date",
    }
    # GROUP_ORDER places Event after Curatorial, before Camera & lens.
    assert GROUP_ORDER.index(GROUP_EVENT) == GROUP_ORDER.index(GROUP_CURATORIAL) + 1
    assert GROUP_ORDER.index(GROUP_EVENT) < GROUP_ORDER.index(GROUP_CAMERA_LENS)


def test_event_scope_catalogue_stays_thin(qapp):
    """spec/86 §2 — the Event group is added to the cross-event catalogue
    ONLY. The event-scope dialog stays at Style + media type (spec/81 §2.1)."""
    cat = build_event_scope_catalogue(_StubHost())
    assert set(cat.keys()) == {"styles", "media_type"}


def test_event_scope_catalogue_is_thin(qapp):
    """spec/81 §2.1 — the event-scope dialog keeps only Style + media
    type. Camera / lens / city / country / ISO / etc. are deliberately
    absent."""
    cat = build_event_scope_catalogue(_StubHost())
    assert tuple(cat.keys()) == EVENT_SCOPE_DIM_IDS
    assert set(cat.keys()) == {"styles", "media_type"}
    # Same FilterDimension class — host can reuse the same Add-filter
    # shell, the only difference is which dims the menu lists.
    for dim in cat.values():
        assert isinstance(dim, FilterDimension)


def test_build_catalogue_subset_skips_unknown_ids(qapp):
    """The generic subset builder tolerates unknown dim ids — useful for
    forward-compat (future tag / people dimensions slot in without
    crashing existing callers)."""
    cat = build_catalogue_subset(_StubHost(),
                                 ["styles", "doesnt_exist", "cities"])
    assert set(cat.keys()) == {"styles", "cities"}


# --------------------------------------------------------------------------- #
# spec/94 Phase 4a — indexing-gated filter dims hidden in the Collection face
# --------------------------------------------------------------------------- #


def test_indexing_gated_dim_ids_match_gear_exif_groups(qapp):
    """The Phase 4a gate is the union of Camera & lens + Settings dims
    (Faces joins when the catalogue carries it). Keeping the constant
    in lockstep with the group definitions prevents drift if a new
    gear / EXIF dim lands."""
    full = build_cross_event_catalogue(_StubHost())
    expected = {
        dim_id for dim_id, dim in full.items()
        if dim.group in (GROUP_CAMERA_LENS, GROUP_SETTINGS)
    }
    assert set(INDEXING_GATED_DIM_IDS) == expected


def test_phase4a_catalogue_excludes_every_indexing_gated_dim(qapp):
    """The Phase 4a builder hides gear / EXIF dims; only Curatorial /
    Event / When & where survive (the brief: "the cross-event filter UI
    must not offer them yet")."""
    cat = build_cross_event_phase4a_catalogue(_StubHost())
    for gated in INDEXING_GATED_DIM_IDS:
        assert gated not in cat, \
            f"{gated} leaked into the Phase 4a catalogue"


def test_phase4a_catalogue_keeps_curatorial_event_and_when_where(qapp):
    """The Phase 4a Collection face still gets Style, Media, Stars,
    Color label, Flag, the spec/86 event-level qualifiers, Capture
    date, Country, City — everything that doesn't require the indexing
    track."""
    cat = build_cross_event_phase4a_catalogue(_StubHost())
    assert set(cat.keys()) == {
        "styles", "media_type", "stars", "color_labels", "flag",
        "event_type", "event_subtype", "scope",
        "participants", "event_date",
        "capture_date", "country_codes", "cities",
    }


def test_phase4a_catalogue_preserves_display_order(qapp):
    """Dim order under the gate is the same display order the full
    catalogue uses — Phase 4a doesn't re-shuffle, just trims."""
    full = list(build_cross_event_catalogue(_StubHost()).keys())
    phase4a = list(build_cross_event_phase4a_catalogue(_StubHost()).keys())
    expected = [d for d in full if d not in INDEXING_GATED_DIM_IDS]
    assert phase4a == expected
    assert phase4a == list(CROSS_EVENT_PHASE4A_DIM_IDS)


def test_phase4a_catalogue_dims_keep_filter_dimension_type(qapp):
    """Same factory shape as the full builder so the Add-filter shell
    works without changes."""
    cat = build_cross_event_phase4a_catalogue(_StubHost())
    for dim in cat.values():
        assert isinstance(dim, FilterDimension)


# --------------------------------------------------------------------------- #
# Group labels
# --------------------------------------------------------------------------- #


def test_group_order_has_five_buckets(qapp):
    """spec/86 §6 — Event group sits between Curatorial and Camera & lens."""
    from mira.ui.pages._filter_family import GROUP_EVENT
    assert GROUP_ORDER == (
        GROUP_CURATORIAL, GROUP_EVENT, GROUP_CAMERA_LENS,
        GROUP_SETTINGS, GROUP_WHEN_WHERE,
    )


def test_group_label_returns_translated_strings(qapp):
    """Every group has a user-visible label; unknown ids round-trip
    verbatim (defensive — never explode on a typo)."""
    assert group_label(GROUP_CURATORIAL) == "Curatorial"
    assert group_label(GROUP_CAMERA_LENS) == "Camera & lens"
    assert group_label(GROUP_SETTINGS) == "Settings"
    assert group_label(GROUP_WHEN_WHERE) == "When & where"
    assert group_label("unknown_group") == "unknown_group"


def test_every_dim_belongs_to_a_known_group(qapp):
    cat = build_cross_event_catalogue(_StubHost())
    for dim_id, dim in cat.items():
        assert dim.group in GROUP_ORDER, \
            f"{dim_id} has unknown group {dim.group!r}"


# --------------------------------------------------------------------------- #
# _ActiveFilterRow
# --------------------------------------------------------------------------- #


def test_active_filter_row_emits_remove_requested(qapp):
    from PyQt6.QtWidgets import QLabel
    dim = FilterDimension(
        dim_id="x", label="X", group=GROUP_CURATORIAL,
        filter_keys=("x",), factory=lambda: QLabel("placeholder"))
    facet = QLabel("editor")
    row = _ActiveFilterRow(dim, facet)
    fired = []
    row.remove_requested.connect(lambda did: fired.append(did))
    row._remove_btn.click()
    assert fired == ["x"]
    row.deleteLater()


def test_active_filter_row_dim_id_accessor(qapp):
    """``dim_id()`` is the public read; tests + the dialog use it to
    look up the row's catalogue entry."""
    from PyQt6.QtWidgets import QLabel
    dim = FilterDimension(
        dim_id="abc", label="A", group=GROUP_CURATORIAL,
        filter_keys=("abc",), factory=lambda: QLabel())
    row = _ActiveFilterRow(dim, QLabel())
    assert row.dim_id() == "abc"
    row.deleteLater()
