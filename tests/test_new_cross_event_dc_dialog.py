"""spec/81 Phase 2 — :class:`NewCrossEventDcDialog` UI tests.

Drives the dialog with hand-supplied inventories + a stub probe; asserts the
emitted :class:`CrossEventDcInfo` shape against each facet's input. Pure UI
— no LibraryGateway in the loop; the host adapter (a separate test) wires
the dialog to the live gateway.
"""
from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QCheckBox, QRadioButton

from core import collection_resolver as cr
from mira.ui.pages.new_cross_event_dc_dialog import (
    CrossEventDcInfo,
    CrossEventInventories,
    NewCrossEventDcDialog,
)


# spec/83 §5: inventories carry ``(value, count)`` pairs sourced lazily by
# filters_json key. The dialog still iterates each facet at construction time
# in slice 1; slice 3 (two-tier shell) is what flips it to true on-demand.
_INVENTORIES = CrossEventInventories.from_dict({
    "styles": [
        ("landscape", 2), ("macro", 1), ("portrait", 1), ("wildlife", 1),
    ],
    "camera_ids":    [("Pana+G9M2", 4), ("Pana+S5", 1)],
    "lens_models":   [
        ("LEICA 45mm", 1), ("LUMIX 100-300", 1),
        ("LUMIX 24-105", 1), ("Lumix 42.5", 1),
    ],
    "country_codes": [("CR", 3), ("NP", 2)],
    "cities": [
        ("La Fortuna", 1), ("Monteverde", 1), ("Namche Bazaar", 1),
    ],
    "color_labels":  [("green", 1), ("red", 1)],
})


def _make_dialog(qapp, *, existing=None, existing_tags=(), probe=None):
    return NewCrossEventDcDialog(
        inventories=_INVENTORIES,
        dc_probe=probe or (lambda _e, _f: 42),
        existing=existing,
        existing_tags=existing_tags,
    )


def _check(box: QCheckBox, on: bool) -> None:
    box.setChecked(on)


def _multi_check(dialog, key: str, *labels: str) -> None:
    """Find the multi-select facet for ``key`` and check the named boxes."""
    from mira.ui.pages.new_cross_event_dc_dialog import _MultiSelectFacet
    for facet in dialog._facets:
        if isinstance(facet, _MultiSelectFacet) and facet._key == key:
            for cb in facet._boxes:
                if cb.text() in labels:
                    cb.setChecked(True)
            return
    raise KeyError(f"no multi-select facet for {key}")


# --------------------------------------------------------------------------- #
# Identity — name slug preview, accept gating
# --------------------------------------------------------------------------- #


def test_tag_preview_slugifies_live(qapp):
    """Typing in the name field updates the tag preview through
    ``cut_names.slugify``."""
    d = _make_dialog(qapp)
    d._name.setText("Best Macro Shots")
    assert d._tag_preview.text() == "tag: #best_macro_shots"
    d.deleteLater()


def test_tag_preview_warns_on_reserved(qapp):
    """Reserved tags (the four ladder rungs) warn in the preview — same
    rule the gateway enforces at create_dc time."""
    d = _make_dialog(qapp)
    d._name.setText("Exported")
    assert "reserved" in d._tag_preview.text()
    d.deleteLater()


def test_tag_preview_warns_on_taken(qapp):
    """A slug already in use warns; the dialog refuses to accept."""
    d = _make_dialog(qapp, existing_tags=("best_macro",))
    d._name.setText("Best Macro")
    assert "in use" in d._tag_preview.text()
    d.deleteLater()


def test_accept_gated_on_empty_name(qapp):
    """Empty name → accept does nothing."""
    d = _make_dialog(qapp)
    d._name.setText("   ")
    fired = []
    d.saved.connect(lambda info: fired.append(info))
    d._on_accept()
    assert fired == []
    d.deleteLater()


def test_accept_emits_info_and_closes(qapp):
    """Non-empty name + valid slug → ``saved`` fires + dialog accepts."""
    d = _make_dialog(qapp)
    d._name.setText("Hero Set")
    fired = []
    d.saved.connect(lambda info: fired.append(info))
    d._on_accept()
    assert len(fired) == 1
    info = fired[0]
    assert isinstance(info, CrossEventDcInfo)
    assert info.name == "Hero Set"
    d.deleteLater()


# --------------------------------------------------------------------------- #
# Origin radio — emits ladder tokens
# --------------------------------------------------------------------------- #


def test_origin_default_is_exported(qapp):
    """The dialog defaults to ``#exported`` — the spec/81 §2.1 event-scope
    parity. The user opts in to broader rungs."""
    d = _make_dialog(qapp)
    d._name.setText("x")
    info = d.info()
    assert info.expr == [["+", cr.BASE_EXPORTED]]
    d.deleteLater()


def test_origin_radio_switches_token(qapp):
    """Picking a ladder rung lands the right base operand."""
    d = _make_dialog(qapp)
    d._name.setText("x")
    for label, token in zip(
            ("#collected", "#picked", "#edited", "#exported"),
            (cr.BASE_COLLECTED, cr.BASE_PICKED, cr.BASE_EDITED, cr.BASE_EXPORTED)):
        d._origin.set_token(token)
        assert d.info().expr == [["+", token]]
    d.deleteLater()


# --------------------------------------------------------------------------- #
# Facet vocabulary surfaces
# --------------------------------------------------------------------------- #


def test_styles_facet_lists_inventory(qapp):
    """The styles multi-select shows every classification the inventory
    supplied. The user's selection lands as ``styles`` in filters_json."""
    d = _make_dialog(qapp)
    d._name.setText("x")
    _multi_check(d, "styles", "macro", "wildlife")
    assert d.info().filters == {"styles": ["macro", "wildlife"]}
    d.deleteLater()


def test_media_type_single_select(qapp):
    """``media_type`` defaults to 'both' (no narrowing); picking 'photo' or
    'video' lands the value in filters."""
    d = _make_dialog(qapp)
    d._name.setText("x")
    # Default is "both" → nothing in filters.
    assert d.info().filters == {}
    # Switch to photo.
    from mira.ui.pages.new_cross_event_dc_dialog import _SingleSelectFacet
    for facet in d._facets:
        if isinstance(facet, _SingleSelectFacet) and facet._key == "media_type":
            facet._buttons[1][0].setChecked(True)        # 'Photos only'
    assert d.info().filters == {"media_type": "photo"}
    d.deleteLater()


def test_stars_min_facet(qapp):
    """The stars-min radio lands a ``stars_min`` int when above "Any"."""
    d = _make_dialog(qapp)
    d._name.setText("x")
    from mira.ui.pages.new_cross_event_dc_dialog import _StarsMinFacet
    for facet in d._facets:
        if isinstance(facet, _StarsMinFacet):
            # "≥ 5" is the last radio.
            facet._buttons[5][0].setChecked(True)
    assert d.info().filters == {"stars_min": 5}
    d.deleteLater()


def test_flag_tri_state(qapp):
    """Portfolio flag tri-state: Any (default, dropped) / Flagged (True) /
    Not flagged (False)."""
    d = _make_dialog(qapp)
    d._name.setText("x")
    from mira.ui.pages.new_cross_event_dc_dialog import _SingleSelectFacet
    for facet in d._facets:
        if isinstance(facet, _SingleSelectFacet) and facet._key == "flag":
            facet._buttons[1][0].setChecked(True)        # 'Flagged'
    assert d.info().filters == {"flag": True}
    d.deleteLater()


def test_camera_and_lens_facets(qapp):
    """Camera + lens are both multi-selects sourced from the inventories."""
    d = _make_dialog(qapp)
    d._name.setText("x")
    _multi_check(d, "camera_ids", "Pana+G9M2")
    _multi_check(d, "lens_models", "LEICA 45mm", "Lumix 42.5")
    f = d.info().filters
    assert f["camera_ids"] == ["Pana+G9M2"]
    assert f["lens_models"] == ["LEICA 45mm", "Lumix 42.5"]
    d.deleteLater()


def test_flash_tri_state(qapp):
    d = _make_dialog(qapp)
    d._name.setText("x")
    from mira.ui.pages.new_cross_event_dc_dialog import _SingleSelectFacet
    for facet in d._facets:
        if isinstance(facet, _SingleSelectFacet) and facet._key == "flash_fired":
            facet._buttons[2][0].setChecked(True)        # 'No flash'
    assert d.info().filters == {"flash_fired": False}
    d.deleteLater()


def test_iso_range_facet(qapp):
    """ISO min/max enable independently."""
    d = _make_dialog(qapp)
    d._name.setText("x")
    from mira.ui.pages.new_cross_event_dc_dialog import _NumberRangeFacet
    for facet in d._facets:
        if isinstance(facet, _NumberRangeFacet) and facet._min_key == "iso_min":
            facet._enable_min.setChecked(True)
            facet._lo.setValue(1600)
            facet._enable_max.setChecked(True)
            facet._hi.setValue(6400)
    assert d.info().filters == {"iso_min": 1600, "iso_max": 6400}
    d.deleteLater()


def test_aperture_range_facet_is_float(qapp):
    """Aperture is a float range (f/2.8 etc.)."""
    d = _make_dialog(qapp)
    d._name.setText("x")
    from mira.ui.pages.new_cross_event_dc_dialog import _NumberRangeFacet
    for facet in d._facets:
        if isinstance(facet, _NumberRangeFacet) \
                and facet._min_key == "aperture_min":
            facet._enable_max.setChecked(True)
            facet._hi.setValue(2.8)
    assert d.info().filters == {"aperture_max": 2.8}
    d.deleteLater()


def test_date_range_facet(qapp):
    """``capture_from`` / ``capture_to`` come through verbatim — the SQL
    layer's ``BETWEEN`` does the real validation."""
    d = _make_dialog(qapp)
    d._name.setText("x")
    from mira.ui.pages.new_cross_event_dc_dialog import _DateRangeFacet
    for facet in d._facets:
        if isinstance(facet, _DateRangeFacet):
            facet._from.setText("2025-01-01")
            facet._to.setText("2025-12-31")
    f = d.info().filters
    assert f["capture_from"] == "2025-01-01"
    assert f["capture_to"] == "2025-12-31"
    d.deleteLater()


def test_country_and_city_facets(qapp):
    d = _make_dialog(qapp)
    d._name.setText("x")
    _multi_check(d, "country_codes", "NP")
    _multi_check(d, "cities", "Namche Bazaar")
    f = d.info().filters
    assert f["country_codes"] == ["NP"]
    assert f["cities"] == ["Namche Bazaar"]
    d.deleteLater()


def test_color_label_facet(qapp):
    d = _make_dialog(qapp)
    d._name.setText("x")
    _multi_check(d, "color_labels", "green")
    assert d.info().filters == {"color_labels": ["green"]}
    d.deleteLater()


# --------------------------------------------------------------------------- #
# Composition — multiple facets fold into one dict
# --------------------------------------------------------------------------- #


def test_filters_compose_spec32_acceptance_query(tmp_path, qapp):
    """The spec/32 §1 query — "best from Nepal — wide-open glass, no flash,
    ≥ 4 stars" — composes from the dialog's facets in one shot."""
    d = _make_dialog(qapp)
    d._name.setText("Nepal Best")
    _multi_check(d, "country_codes", "NP")
    from mira.ui.pages.new_cross_event_dc_dialog import (
        _NumberRangeFacet, _SingleSelectFacet, _StarsMinFacet,
    )
    for facet in d._facets:
        if isinstance(facet, _NumberRangeFacet) \
                and facet._min_key == "aperture_min":
            facet._enable_max.setChecked(True)
            facet._hi.setValue(2.8)
        elif isinstance(facet, _SingleSelectFacet) \
                and facet._key == "flash_fired":
            facet._buttons[2][0].setChecked(True)        # 'No flash'
        elif isinstance(facet, _StarsMinFacet):
            facet._buttons[4][0].setChecked(True)        # '≥ 4'
    f = d.info().filters
    assert f == {
        "country_codes": ["NP"],
        "aperture_max": 2.8,
        "flash_fired": False,
        "stars_min": 4,
    }
    d.deleteLater()


# --------------------------------------------------------------------------- #
# Live count — calls the probe with current expr+filters
# --------------------------------------------------------------------------- #


def test_live_count_reads_probe(qapp):
    """The count label reflects the probe's return."""
    calls = []
    def probe(expr, filters):
        calls.append((expr, filters))
        return 12
    d = _make_dialog(qapp, probe=probe)
    d._name.setText("x")
    assert "12 items match" in d._count_label.text()
    assert calls[-1] == ([["+", cr.BASE_EXPORTED]], {})
    d.deleteLater()


def test_live_count_refreshes_on_facet_change(qapp):
    """A facet change retriggers the probe."""
    counts = iter([5, 99])
    d = _make_dialog(qapp, probe=lambda _e, _f: next(counts))
    d._name.setText("x")
    # First refresh during construction; bump origin to picked.
    d._origin.set_token(cr.BASE_PICKED)
    assert "99 items match" in d._count_label.text()
    d.deleteLater()


def test_live_count_handles_probe_error(qapp):
    """A probe that raises doesn't crash the dialog — count shows 'error'."""
    def angry(_e, _f):
        raise RuntimeError("nope")
    d = _make_dialog(qapp, probe=angry)
    d._name.setText("x")
    assert "error" in d._count_label.text()
    d.deleteLater()


# --------------------------------------------------------------------------- #
# Rehydrate (edit flow)
# --------------------------------------------------------------------------- #


def test_rehydrate_from_existing(qapp):
    """An existing DC's info pre-fills name + origin + every facet."""
    existing = CrossEventDcInfo(
        name="five_star_macro",
        description="five-stars only",
        expr=[["+", cr.BASE_PICKED]],
        filters={
            "styles": ["macro"], "stars_min": 5,
            "country_codes": ["NP"],
        },
    )
    d = _make_dialog(qapp, existing=existing)
    assert d._name.text() == "five_star_macro"
    assert d._description.text() == "five-stars only"
    assert d._origin.token() == cr.BASE_PICKED
    info = d.info()
    assert info.filters["styles"] == ["macro"]
    assert info.filters["stars_min"] == 5
    assert info.filters["country_codes"] == ["NP"]
    d.deleteLater()


# --------------------------------------------------------------------------- #
# Entry point on the cross-event band
# --------------------------------------------------------------------------- #


def test_cross_event_band_emits_new_dc_requested(qapp):
    """The band's new + Collection button emits ``new_dc_requested`` —
    the events screen wires it to open this dialog."""
    from mira.ui.pages._cross_event_band import CrossEventCutsBand
    band = CrossEventCutsBand()
    fired = []
    band.new_dc_requested.connect(lambda: fired.append(1))
    band._new_dc_button.click()
    assert fired == [1]
    band.deleteLater()
