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
    EVENT_SCOPE_DIM_IDS,
    GROUP_CAMERA_LENS,
    GROUP_CURATORIAL,
    GROUP_ORDER,
    GROUP_SETTINGS,
    GROUP_WHEN_WHERE,
    FilterDimension,
    _ActiveFilterRow,
    build_catalogue_subset,
    build_cross_event_catalogue,
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


def test_cross_event_catalogue_has_15_dimensions_in_order(qapp):
    cat = build_cross_event_catalogue(_StubHost())
    assert tuple(cat.keys()) == CROSS_EVENT_DIM_IDS
    assert len(cat) == 15


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
    }


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
# Group labels
# --------------------------------------------------------------------------- #


def test_group_order_has_four_buckets(qapp):
    assert GROUP_ORDER == (
        GROUP_CURATORIAL, GROUP_CAMERA_LENS,
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
