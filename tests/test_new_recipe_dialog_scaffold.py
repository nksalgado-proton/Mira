"""spec/90 Phase 4a — :class:`NewRecipeDialog` scaffold visibility tests.

Pins the four constructor flags (``flavour`` / ``show_scope`` /
``show_hardware`` / ``inventory_scope``) drive the section visibility per
spec/90 §2 — the Cut face hides Scope + Camera/Lens/Faces; the Collection
face shows all five sections. Placeholder rows (Rules / Otherwise /
Metrics) render their stubs so the in-progress state reads honestly.
"""
from __future__ import annotations

import pytest

from PyQt6.QtWidgets import QLabel

from mira.ui.pages.new_recipe_dialog import (
    FLAVOUR_COLLECTION,
    FLAVOUR_CUT,
    INVENTORY_EVENT,
    INVENTORY_LIBRARY,
    NewRecipeContext,
    NewRecipeDialog,
    OperandOption,
)


def _ctx(
    *,
    event_name: str = "Costa Rica 2026",
    styles=("macro", "wildlife"),
    cameras=(),
    lenses=(),
) -> NewRecipeContext:
    return NewRecipeContext(
        event_name=event_name,
        available_pools=[
            OperandOption(name="#exported", count=12, kind="base"),
            OperandOption(name="#long", count=200, kind="cut", tag="long"),
        ],
        available_styles=list(styles),
        available_cameras=list(cameras),
        available_lenses=list(lenses),
    )


def _cut_dialog(qapp, **over) -> NewRecipeDialog:
    kw = dict(
        flavour=FLAVOUR_CUT,
        show_scope=False,
        show_hardware=False,
        inventory_scope=INVENTORY_EVENT,
        ctx=_ctx(),
    )
    kw.update(over)
    return NewRecipeDialog(**kw)


def _collection_dialog(qapp, **over) -> NewRecipeDialog:
    kw = dict(
        flavour=FLAVOUR_COLLECTION,
        show_scope=True,
        show_hardware=True,
        inventory_scope=INVENTORY_LIBRARY,
        ctx=_ctx(
            cameras=("Pana+G9M2", "Sony+A7R5"),
            lenses=("100-500mm", "24-70mm"),
        ),
    )
    kw.update(over)
    return NewRecipeDialog(**kw)


# --------------------------------------------------------------------------- #
# Flavour rejection
# --------------------------------------------------------------------------- #


def test_invalid_flavour_raises(qapp):
    with pytest.raises(ValueError, match="flavour"):
        NewRecipeDialog(
            flavour="mix", show_scope=False, show_hardware=False,
            inventory_scope=INVENTORY_EVENT, ctx=_ctx(),
        )


def test_invalid_inventory_scope_raises(qapp):
    with pytest.raises(ValueError, match="inventory_scope"):
        NewRecipeDialog(
            flavour=FLAVOUR_CUT, show_scope=False, show_hardware=False,
            inventory_scope="elsewhere", ctx=_ctx(),
        )


# --------------------------------------------------------------------------- #
# Section visibility — Cut face hides Scope + hardware
# --------------------------------------------------------------------------- #


def test_cut_dialog_hides_scope_section(qapp):
    """spec/90 §2.1 — Scope is the current event and hidden on the Cut
    face. The placeholder row exists only when ``show_scope=True``."""
    dlg = _cut_dialog(qapp)
    scope = dlg.findChild(object, "ScopeSection")
    assert scope is None


def test_cut_dialog_hides_camera_lens_and_faces(qapp):
    """spec/90 §2.1 / §4.2 — Camera + Lens are Collection-only; the Cut
    face hides them entirely. Faces stay hidden by default (§4.3 — opt-in
    via setting, which Phase 4a doesn't expose)."""
    dlg = _cut_dialog(qapp)
    # No Camera / Lens chips ever populate the dict.
    assert dlg._camera_chips == {}
    assert dlg._lens_chips == {}


def test_cut_dialog_shows_style_and_media(qapp):
    """Style + Media are always visible (spec/90 §4.1, both dialogs)."""
    dlg = _cut_dialog(qapp)
    assert set(dlg._style_chips) == {"macro", "wildlife"}
    assert dlg._photos_cb is not None and dlg._photos_cb.isChecked()
    assert dlg._videos_cb is not None and dlg._videos_cb.isChecked()


# --------------------------------------------------------------------------- #
# Section visibility — Collection face shows everything
# --------------------------------------------------------------------------- #


def test_collection_dialog_shows_scope_section(qapp):
    dlg = _collection_dialog(qapp)
    scope = dlg.findChild(object, "ScopeSection")
    assert scope is not None


def test_collection_dialog_shows_camera_and_lens_chips(qapp):
    dlg = _collection_dialog(qapp)
    assert set(dlg._camera_chips) == {"Pana+G9M2", "Sony+A7R5"}
    assert set(dlg._lens_chips) == {"100-500mm", "24-70mm"}


def test_collection_dialog_shows_style_and_media(qapp):
    dlg = _collection_dialog(qapp)
    assert set(dlg._style_chips) == {"macro", "wildlife"}
    assert dlg._photos_cb is not None
    assert dlg._videos_cb is not None


# --------------------------------------------------------------------------- #
# Placeholder sections render their stubs
# --------------------------------------------------------------------------- #


def _find_placeholder(dlg: NewRecipeDialog, text_marker: str) -> bool:
    """True when at least one QLabel in the dialog carries ``text_marker``
    in its text. Placeholder rows are plain QLabels with "Faint"
    object name."""
    for lbl in dlg.findChildren(QLabel):
        if text_marker in (lbl.text() or ""):
            return True
    return False


def test_rules_section_renders(qapp):
    """Phase 4c shipped the real Rules section; the placeholder retired."""
    dlg = _cut_dialog(qapp)
    assert dlg.findChild(object, "RulesSection") is not None


def test_otherwise_section_renders_phase_4c_placeholder(qapp):
    dlg = _cut_dialog(qapp)
    assert dlg.findChild(object, "OtherwiseSection") is not None


def test_metrics_section_renders_phase_4d_placeholder(qapp):
    dlg = _cut_dialog(qapp)
    assert dlg.findChild(object, "MetricsSection") is not None
    assert _find_placeholder(dlg, "Phase 4d")


def test_faces_placeholder_only_when_hardware_visible(qapp):
    """Faces is a Phase 4c placeholder and rides under the hardware row.
    A Cut face hides it (no hardware); a Collection face shows it."""
    cut = _cut_dialog(qapp)
    assert not _find_placeholder(cut, "Faces:")
    collection = _collection_dialog(qapp)
    assert _find_placeholder(collection, "Faces:")


# --------------------------------------------------------------------------- #
# Header + footer
# --------------------------------------------------------------------------- #


def test_window_title_matches_flavour(qapp):
    cut = _cut_dialog(qapp)
    assert cut.windowTitle() == "New Cut"
    coll = _collection_dialog(qapp)
    assert coll.windowTitle() == "New Collection"


def test_start_button_disabled_in_phase_4a(qapp):
    """Phase 4a hasn't wired the rule list yet, so Start stays disabled
    (spec/90 §1.3 — rules + Otherwise are required to seed the picker)."""
    dlg = _cut_dialog(qapp)
    assert dlg._start_btn.isEnabled() is False


def test_load_recipe_button_disabled_in_phase_4a(qapp):
    """Load Recipe… opens the saved-Recipe picker in Phase 4e."""
    dlg = _cut_dialog(qapp)
    assert dlg._load_btn.isEnabled() is False


def test_save_recipe_button_disabled_in_phase_4a(qapp):
    dlg = _cut_dialog(qapp)
    assert dlg._save_recipe_btn.isEnabled() is False
