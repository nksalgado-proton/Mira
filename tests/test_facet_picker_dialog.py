"""spec/83 §4 — :class:`FacetPickerDialog` tests.

Drives the picker with hand-built inventories + an optional
:class:`GearProfileSnapshot`; asserts the partition decisions, the
selection flow, search/select-all/clear, and the OK/Cancel commit shape.
Pure UI — no LibraryGateway in the loop.
"""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QDialog

from mira.ui.pages.facet_picker_dialog import (
    FacetPickerDialog,
    GearProfileSnapshot,
    OCCASIONAL_CUTOFF,
)


# --------------------------------------------------------------------------- #
# Partition decision — pure function, no Qt required
# --------------------------------------------------------------------------- #


def test_partition_active_gear_is_main():
    """spec/85 §5: a tagged-active camera ALWAYS lands in the main list,
    even if its count is below the occasional cutoff."""
    assert not FacetPickerDialog._belongs_in_occasional(
        "Pana+G9M2", count=2,
        active=frozenset({"Pana+G9M2"}),
        occasional=frozenset())


def test_partition_inactive_gear_is_occasional():
    """A tagged-inactive (gear_profile.is_active = 0) row ALWAYS lands in
    occasional, even if its count is well above the cutoff."""
    assert FacetPickerDialog._belongs_in_occasional(
        "Pana+G9M2", count=5000,
        active=frozenset(),
        occasional=frozenset({"Pana+G9M2"}))


def test_partition_untagged_uses_count_heuristic():
    """Untagged gear (no profile row) falls back to the < cutoff rule."""
    assert FacetPickerDialog._belongs_in_occasional(
        "x", count=OCCASIONAL_CUTOFF - 1,
        active=frozenset(), occasional=frozenset())
    assert not FacetPickerDialog._belongs_in_occasional(
        "x", count=OCCASIONAL_CUTOFF,
        active=frozenset(), occasional=frozenset())
    assert not FacetPickerDialog._belongs_in_occasional(
        "x", count=OCCASIONAL_CUTOFF + 1,
        active=frozenset(), occasional=frozenset())


# --------------------------------------------------------------------------- #
# GearProfileSnapshot.for_facet
# --------------------------------------------------------------------------- #


def test_gear_snapshot_dispatches_by_facet_key():
    snap = GearProfileSnapshot(
        cameras_active=frozenset({"Pana+G9M2"}),
        cameras_occasional=frozenset({"Pana+S5"}),
        lenses_active=frozenset({"LEICA 45mm"}),
        lenses_occasional=frozenset({"LUMIX 100-300"}),
    )
    assert snap.for_facet("camera_ids") == (
        frozenset({"Pana+G9M2"}), frozenset({"Pana+S5"}))
    assert snap.for_facet("lens_models") == (
        frozenset({"LEICA 45mm"}), frozenset({"LUMIX 100-300"}))
    # Non-gear facets: empty pair so the picker falls through to count.
    assert snap.for_facet("cities") == (frozenset(), frozenset())
    assert snap.for_facet("country_codes") == (frozenset(), frozenset())


# --------------------------------------------------------------------------- #
# Dialog construction + initial state
# --------------------------------------------------------------------------- #


def _open(qapp, *, facet_key="camera_ids", inventory=(),
          initially_selected=(), gear=None):
    """Build a picker; tests `.deleteLater()` after the assertions."""
    return FacetPickerDialog(
        facet_key=facet_key,
        facet_label="Camera",
        inventory=list(inventory),
        initially_selected=list(initially_selected),
        gear=gear,
    )


def test_dialog_partitions_main_vs_occasional(qapp):
    """A 6-row inventory mixed with gear flags lands the right rows in
    each bucket. Count above the cutoff with no gear flag → main;
    inactive gear → occasional even at high count."""
    inv = [
        ("Pana+G9M2", 5000),       # active gear → main
        ("Pana+S5", 50),           # no gear, count >= cutoff → main
        ("Sony A7", 200),          # inactive gear → occasional
        ("Lumix S5II", 4),         # untagged + count < cutoff → occasional
        ("Pana+GH6", 4000),        # active gear → main
        ("Borrowed", 1),           # untagged + count < cutoff → occasional
    ]
    snap = GearProfileSnapshot(
        cameras_active=frozenset({"Pana+G9M2", "Pana+GH6"}),
        cameras_occasional=frozenset({"Sony A7"}),
    )
    d = _open(qapp, inventory=inv, gear=snap)
    main_values = {v for v, _ in d._main_rows}
    occ_values = {v for v, _ in d._occasional_rows}
    assert main_values == {"Pana+G9M2", "Pana+S5", "Pana+GH6"}
    assert occ_values == {"Sony A7", "Lumix S5II", "Borrowed"}
    d.deleteLater()


def test_dialog_opens_with_initial_selection_checked(qapp):
    inv = [("a", 100), ("b", 50), ("c", 25)]
    d = _open(qapp, inventory=inv, initially_selected=["b"])
    checked = [v for v, cb in d._main_checks if cb.isChecked()]
    assert checked == ["b"]
    d.deleteLater()


def test_dialog_selected_values_round_trip(qapp):
    inv = [("a", 100), ("b", 50), ("c", 25)]
    d = _open(qapp, inventory=inv, initially_selected=["c", "a"])
    # selected_values returns in catalogue (= inventory) order.
    assert d.selected_values() == ["a", "c"]
    d.deleteLater()


def test_dialog_toggling_a_row_updates_selection(qapp):
    inv = [("a", 100), ("b", 50)]
    d = _open(qapp, inventory=inv)
    main_checks = dict(d._main_checks)
    main_checks["b"].setChecked(True)
    assert d.selected_values() == ["b"]
    main_checks["b"].setChecked(False)
    assert d.selected_values() == []
    d.deleteLater()


# --------------------------------------------------------------------------- #
# Search + Select all + Clear
# --------------------------------------------------------------------------- #


def test_search_filters_rows(qapp):
    inv = [("Apple", 100), ("Banana", 50), ("Cherry", 25)]
    d = _open(qapp, inventory=inv)
    cb_by = dict(d._main_checks)
    d._search.setText("apple")
    # ``isHidden`` reads the explicit setVisible state without needing the
    # dialog to actually be shown (cheap for headless tests).
    assert not cb_by["Apple"].isHidden()
    assert cb_by["Banana"].isHidden()
    assert cb_by["Cherry"].isHidden()
    d._search.setText("")              # clearing restores all rows
    assert all(not cb.isHidden() for _, cb in d._main_checks)
    d.deleteLater()


def test_search_is_case_insensitive_and_substring(qapp):
    inv = [("ApplePear", 100), ("PEACH", 50), ("Banana", 25)]
    d = _open(qapp, inventory=inv)
    d._search.setText("eA")
    visible = [v for v, cb in d._main_checks if not cb.isHidden()]
    # "ea" matches ApplePear (...lePear...) and PEACH (PEAch).
    assert set(visible) == {"ApplePear", "PEACH"}
    d.deleteLater()


def test_search_only_in_occasional_auto_expands(qapp):
    """When the search matches no main rows but matches occasional rows,
    the picker auto-expands the Occasional section so the user sees the
    matches instead of an apparently-empty list."""
    inv = [
        ("Apple", 50),     # main (count >= cutoff)
        ("Borrowed", 1),   # occasional
    ]
    d = _open(qapp, inventory=inv)
    # Collapsed by default — _occ_container has been setVisible(False).
    assert d._occ_container.isHidden()
    d._search.setText("borrowed")
    assert not d._occ_container.isHidden()
    assert d._occ_toggle.isChecked()
    d.deleteLater()


def test_select_all_picks_only_visible_rows(qapp):
    """Search + Select all = "select the matches"."""
    inv = [("Apple", 100), ("Banana", 50), ("Cherry", 25)]
    d = _open(qapp, inventory=inv)
    d._search.setText("a")
    d._on_select_all()
    assert set(d.selected_values()) == {"Apple", "Banana"}    # Cherry hidden
    d.deleteLater()


def test_clear_removes_every_selection_regardless_of_search(qapp):
    inv = [("Apple", 100), ("Banana", 50), ("Cherry", 25)]
    d = _open(qapp, inventory=inv, initially_selected=["Apple", "Cherry"])
    d._search.setText("apple")
    d._on_clear()
    assert d.selected_values() == []
    d.deleteLater()


# --------------------------------------------------------------------------- #
# Occasional section toggle
# --------------------------------------------------------------------------- #


def test_occasional_section_collapsed_by_default(qapp):
    inv = [("Pana+G9M2", 5000), ("Borrowed", 1)]
    d = _open(qapp, inventory=inv)
    assert d._occ_container is not None
    assert d._occ_container.isHidden()
    d._occ_toggle.setChecked(True)
    assert not d._occ_container.isHidden()
    d.deleteLater()


def test_no_occasional_section_when_all_rows_are_main(qapp):
    inv = [("a", 100), ("b", 50)]
    d = _open(qapp, inventory=inv)
    assert d._occ_container is None
    assert d._occ_toggle is None
    d.deleteLater()


def test_no_main_section_widget_when_all_rows_are_occasional(qapp):
    inv = [("Borrowed", 1), ("Borrowed2", 2)]
    d = _open(qapp, inventory=inv)
    assert d._main_rows == []
    assert d._occ_container is not None
    d.deleteLater()


# --------------------------------------------------------------------------- #
# Accept / cancel commit
# --------------------------------------------------------------------------- #


def test_accept_emits_selection_signal(qapp):
    inv = [("a", 100), ("b", 50)]
    d = _open(qapp, inventory=inv, initially_selected=["a"])
    fired = []
    d.accepted_with_selection.connect(lambda vals: fired.append(vals))
    d._on_accept()
    assert fired == [["a"]]
    d.deleteLater()
