"""spec/90 Phase 4a — Filters section tests.

* Style chip selection populates ``composition['filters']['styles']``.
* Media checkboxes drive ``composition['filters']['media_type']``.
* Camera + Lens rows hide when the user has zero / one entry (§4.2 —
  "the dialog adapts to the user's inventory").
* The Camera + Lens rows are hidden entirely when ``show_hardware=False``.
"""
from __future__ import annotations

import pytest

from mira.ui.pages.new_cut_dialog import (
    SCOPE_CROSS_EVENT,
    SCOPE_EVENT,
    INVENTORY_EVENT,
    INVENTORY_LIBRARY,
    NewRecipeContext,
    NewCutDialog,
)


def _cut_dialog(
    qapp, *,
    styles=("macro", "wildlife"),
    cameras=(),
    lenses=(),
    include_photos=True,
    include_videos=True,
) -> NewCutDialog:
    ctx = NewRecipeContext(
        event_name="Evt",
        available_styles=list(styles),
        available_cameras=list(cameras),
        available_lenses=list(lenses),
        include_photos=include_photos,
        include_videos=include_videos,
    )
    return NewCutDialog(
        scope=SCOPE_EVENT,
        show_scope=False,
        show_hardware=False,
        inventory_scope=INVENTORY_EVENT,
        ctx=ctx,
    )


def _collection_dialog(
    qapp, *,
    cameras=("Pana+G9M2", "Sony+A7R5"),
    lenses=("100-500mm", "24-70mm"),
    selected_cameras=(),
    selected_lenses=(),
) -> NewCutDialog:
    ctx = NewRecipeContext(
        event_name="",
        available_styles=["macro"],
        available_cameras=list(cameras),
        available_lenses=list(lenses),
        selected_cameras=list(selected_cameras),
        selected_lenses=list(selected_lenses),
    )
    return NewCutDialog(
        scope=SCOPE_CROSS_EVENT,
        show_scope=True,
        show_hardware=True,
        inventory_scope=INVENTORY_LIBRARY,
        ctx=ctx,
    )


# --------------------------------------------------------------------------- #
# Style chips
# --------------------------------------------------------------------------- #


def test_no_styles_selected_emits_empty_styles(qapp):
    dlg = _cut_dialog(qapp)
    payload = dlg.filters_payload()
    assert payload["styles"] == []


def test_selected_style_chips_drive_payload(qapp):
    dlg = _cut_dialog(qapp)
    dlg._style_chips["macro"].setChecked(True)
    payload = dlg.filters_payload()
    assert payload["styles"] == ["macro"]
    dlg._style_chips["wildlife"].setChecked(True)
    payload = dlg.filters_payload()
    assert set(payload["styles"]) == {"macro", "wildlife"}


def test_initial_selected_styles_round_trip(qapp):
    """spec/90 §1.4 lenient filters: the initial selection round-trips
    on first paint (Recipe-load path)."""
    ctx = NewRecipeContext(
        available_styles=["macro", "wildlife"],
        selected_styles=["macro"],
    )
    dlg = NewCutDialog(
        scope=SCOPE_EVENT, show_scope=False, show_hardware=False,
        inventory_scope=INVENTORY_EVENT, ctx=ctx,
    )
    assert dlg._style_chips["macro"].isChecked()
    assert not dlg._style_chips["wildlife"].isChecked()


# --------------------------------------------------------------------------- #
# Media checkboxes
# --------------------------------------------------------------------------- #


def test_media_default_is_both(qapp):
    dlg = _cut_dialog(qapp)
    payload = dlg.filters_payload()
    assert payload["media_type"] == "both"


def test_media_photo_only(qapp):
    dlg = _cut_dialog(qapp)
    dlg._videos_cb.setChecked(False)
    assert dlg.filters_payload()["media_type"] == "photo"


def test_media_video_only(qapp):
    dlg = _cut_dialog(qapp)
    dlg._photos_cb.setChecked(False)
    assert dlg.filters_payload()["media_type"] == "video"


def test_initial_media_selection_respected(qapp):
    """The ctx flags drive the initial checkbox state."""
    dlg = _cut_dialog(qapp, include_photos=False, include_videos=True)
    assert dlg._photos_cb.isChecked() is False
    assert dlg._videos_cb.isChecked() is True
    assert dlg.filters_payload()["media_type"] == "video"


# --------------------------------------------------------------------------- #
# Camera + Lens rows — hide when show_hardware=False
# --------------------------------------------------------------------------- #


def test_cut_face_omits_camera_and_lens_keys(qapp):
    """Cut face hides Camera + Lens entirely (spec/90 §2.1). The keys
    don't appear in the filters payload — the resolver still tolerates
    missing keys, but the dialog's contract is that absence here means
    "the user can't filter by gear on a Cut Recipe" rather than "user
    chose nothing"."""
    dlg = _cut_dialog(qapp, cameras=("Pana+G9M2", "Sony+A7R5"))
    payload = dlg.filters_payload()
    assert "camera_ids" not in payload
    assert "lens_models" not in payload


# --------------------------------------------------------------------------- #
# Camera + Lens chip selection — Collection face
# --------------------------------------------------------------------------- #


def test_collection_face_camera_chip_selection(qapp):
    dlg = _collection_dialog(qapp)
    dlg._camera_chips["Pana+G9M2"].setChecked(True)
    payload = dlg.filters_payload()
    assert payload["camera_ids"] == ["Pana+G9M2"]


def test_collection_face_lens_chip_selection(qapp):
    dlg = _collection_dialog(qapp)
    dlg._lens_chips["100-500mm"].setChecked(True)
    payload = dlg.filters_payload()
    assert payload["lens_models"] == ["100-500mm"]


def test_collection_face_no_camera_selection_omits_key(qapp):
    """When the Camera row is visible but nothing's selected, the key
    drops out — same semantics the resolver wants for "no filter"."""
    dlg = _collection_dialog(qapp)
    payload = dlg.filters_payload()
    assert "camera_ids" not in payload
    assert "lens_models" not in payload


def test_collection_face_initial_camera_selection_respected(qapp):
    dlg = _collection_dialog(qapp, selected_cameras=("Sony+A7R5",))
    assert dlg._camera_chips["Sony+A7R5"].isChecked()
    assert not dlg._camera_chips["Pana+G9M2"].isChecked()


# --------------------------------------------------------------------------- #
# Adapt-to-inventory rule (spec/90 §4.2)
# --------------------------------------------------------------------------- #


def test_camera_row_hidden_when_only_one_camera(qapp):
    """spec/90 §4.2: a single-camera photographer never sees the Camera
    row at all. ``available_cameras`` of length 1 hides the row."""
    dlg = _collection_dialog(qapp, cameras=("Pana+G9M2",))
    # No camera chips registered = no row built.
    assert dlg._camera_chips == {}


def test_camera_row_hidden_when_zero_cameras(qapp):
    dlg = _collection_dialog(qapp, cameras=())
    assert dlg._camera_chips == {}


def test_lens_row_hidden_when_only_one_lens(qapp):
    dlg = _collection_dialog(qapp, lenses=("100-500mm",))
    assert dlg._lens_chips == {}


def test_lens_row_hidden_when_zero_lenses(qapp):
    dlg = _collection_dialog(qapp, lenses=())
    assert dlg._lens_chips == {}


def test_camera_row_shown_when_two_or_more_cameras(qapp):
    dlg = _collection_dialog(qapp, cameras=("A", "B", "C"))
    assert set(dlg._camera_chips) == {"A", "B", "C"}
