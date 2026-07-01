"""spec/90 Phase 4a — :class:`NewCutDialog` scaffold visibility tests.

Pins the four constructor flags (``flavour`` / ``show_scope`` /
``show_hardware`` / ``inventory_scope``) drive the section visibility per
spec/90 §2 — the Cut face hides Scope + Camera/Lens/Faces; the Collection
face shows all five sections. Placeholder rows (Rules / Otherwise /
Metrics) render their stubs so the in-progress state reads honestly.
"""
from __future__ import annotations

import pytest

from PyQt6.QtWidgets import QLabel

from mira.ui.pages.new_cut_dialog import (
    FLAVOUR_COLLECTION,
    FLAVOUR_CUT,
    INVENTORY_EVENT,
    INVENTORY_LIBRARY,
    MODE_EDIT,
    MODE_NEW,
    NewRecipeContext,
    NewCutDialog,
    OperandOption,
    SCOPE_CROSS_EVENT,
    SCOPE_EVENT,
)


def _section(parent, name: str):
    """Find the dialog #SectionBox child whose `section` property == ``name``.

    spec/92 §2.3 collapsed the 12 dialog frame roles (NameBox, ScopeBox,
    SourceSection, FiltersSection, RulesSectionCard, OtherwiseSectionCard,
    RuntimeSectionCard, MetricsSectionCard, WhichItemsBand, WhatToDoBand,
    RecipeToolbar, SectionCard) onto one #SectionBox role; the legacy
    semantic identity rides on a ``section`` Qt dynamic property. This
    helper preserves the test API that used to be
    ``parent.findChild(object, "<RoleName>")``.
    """
    from PyQt6.QtWidgets import QFrame
    for w in parent.findChildren(QFrame):
        if w.objectName() == "SectionBox" and w.property("section") == name:
            return w
    return None


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


def _cut_dialog(qapp, **over) -> NewCutDialog:
    kw = dict(
        flavour=FLAVOUR_CUT,
        show_scope=False,
        show_hardware=False,
        inventory_scope=INVENTORY_EVENT,
        ctx=_ctx(),
    )
    kw.update(over)
    return NewCutDialog(**kw)


def _collection_dialog(qapp, **over) -> NewCutDialog:
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
    return NewCutDialog(**kw)


# --------------------------------------------------------------------------- #
# Flavour rejection
# --------------------------------------------------------------------------- #


def test_invalid_flavour_raises(qapp):
    with pytest.raises(ValueError, match="flavour"):
        NewCutDialog(
            flavour="mix", show_scope=False, show_hardware=False,
            inventory_scope=INVENTORY_EVENT, ctx=_ctx(),
        )


def test_invalid_inventory_scope_raises(qapp):
    with pytest.raises(ValueError, match="inventory_scope"):
        NewCutDialog(
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


def _find_placeholder(dlg: NewCutDialog, text_marker: str) -> bool:
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


def test_metrics_section_renders(qapp):
    """Phase 4d shipped the live metrics row; the placeholder retired.
    The section host is still findable; tests for the actual metrics
    behaviour live in test_new_recipe_dialog_metrics.py."""
    dlg = _cut_dialog(qapp)
    assert dlg.findChild(object, "MetricsSection") is not None


def test_faces_placeholder_only_when_show_faces_is_true(qapp):
    """spec/94 Phase 4b (2026-06-21) decoupled the Faces placeholder
    from ``show_hardware``. A Cut face hides it (both flags False);
    a Collection face hides it BY DEFAULT (``show_faces=False`` —
    spec/91 deferred) and reveals it only when the caller explicitly
    opts in (``show_faces=True`` — a future spec/91 caller)."""
    cut = _cut_dialog(qapp)
    assert not _find_placeholder(cut, "Faces:")
    collection_default = _collection_dialog(qapp)
    assert not _find_placeholder(collection_default, "Faces:")
    collection_with_faces = _collection_dialog(qapp, show_faces=True)
    assert _find_placeholder(collection_with_faces, "Faces:")


# --------------------------------------------------------------------------- #
# Header + footer
# --------------------------------------------------------------------------- #


def test_window_title_matches_flavour(qapp):
    cut = _cut_dialog(qapp)
    assert cut.windowTitle() == "New Cut"
    coll = _collection_dialog(qapp)
    assert coll.windowTitle() == "New Collection"


def test_start_button_disabled_with_empty_source(qapp):
    """Phase 4e — Start is gated on a non-empty source + a successful probe.
    A fresh dialog has no source chips, so Start stays disabled."""
    dlg = _cut_dialog(qapp)
    assert dlg._start_btn.isEnabled() is False


def test_load_recipe_button_disabled_without_store(qapp):
    """Load Recipe… enables once a :class:`RecipeStore` is wired (Phase
    4e). A scaffold dialog has none, so the button stays disabled."""
    dlg = _cut_dialog(qapp)
    assert dlg._load_btn.isEnabled() is False


def test_save_recipe_button_disabled_without_store(qapp):
    """Same gating as Load — no store wired → no save path. Also gated
    on a non-empty Source + Name per spec/90 §5.5 (the band-header
    button's new gating)."""
    dlg = _cut_dialog(qapp)
    assert dlg._save_recipe_btn.isEnabled() is False


# --------------------------------------------------------------------------- #
# Band layout — spec/90 §5.5 reorganization
# --------------------------------------------------------------------------- #


def test_both_band_headers_render(qapp):
    """spec/90 §5.5 — the dialog body groups into two visible bands
    between Name and Metrics."""
    dlg = _cut_dialog(qapp)
    assert _section(dlg, "WhichItemsBand") is not None
    assert _section(dlg, "WhatToDoBand") is not None


def test_band_headers_carry_their_save_buttons(qapp):
    """spec/90 §5 — the Recipe toolbar hosts Load Recipe + Save as
    Recipe (the recipe-layer save), and the "Which items?" band hosts
    Load DC + Save as DC (the items-layer save). The "What to do?"
    band has no header buttons (Recipe is the only save that captures
    that layer)."""
    from PyQt6.QtWidgets import QPushButton
    dlg = _cut_dialog(qapp)
    toolbar = _section(dlg, "RecipeToolbar")
    which = _section(dlg, "WhichItemsBand")
    what = _section(dlg, "WhatToDoBand")
    # Recipe toolbar carries the Recipe-layer buttons.
    assert dlg._save_recipe_btn.parent() is toolbar
    assert dlg._load_btn.parent() is toolbar
    # Which items? band carries the DC-layer buttons.
    assert dlg._save_dc_btn.parent() is which
    assert dlg._load_dc_btn.parent() is which
    # What to do? band has no header buttons.
    what_buttons = [
        b for b in what.findChildren(QPushButton)
        if b.objectName() in (
            "BandSaveAsRecipe", "ToolbarSaveAsRecipe", "BandLoadDc",
            "BandSaveAsDc",
        )
    ]
    assert what_buttons == []


def test_band_header_question_labels(qapp):
    """The band headers carry Q4-style human prose, not micro / uppercase
    section labels (spec/90 §5.5)."""
    dlg = _cut_dialog(qapp)
    which = _section(dlg, "WhichItemsBand")
    what = _section(dlg, "WhatToDoBand")
    which_labels = [
        lbl.text() for lbl in which.findChildren(QLabel)
        if lbl.objectName() == "BandQuestion"
    ]
    what_labels = [
        lbl.text() for lbl in what.findChildren(QLabel)
        if lbl.objectName() == "BandQuestion"
    ]
    assert "Which items?" in which_labels
    assert "What to do with them?" in what_labels


def test_which_items_band_hint_only_when_scope_visible(qapp):
    """The "(across the events above)" hint sits next to the Which
    items? question only when ``show_scope=True`` (Collection face);
    the Cut face suppresses it."""
    cut = _cut_dialog(qapp)
    cut_hints = [
        lbl.text()
        for lbl in _section(cut, "WhichItemsBand").findChildren(QLabel)
        if lbl.objectName() == "BandHint"
    ]
    assert cut_hints == []

    coll = _collection_dialog(qapp)
    coll_hints = [
        lbl.text()
        for lbl in _section(coll, "WhichItemsBand").findChildren(QLabel)
        if lbl.objectName() == "BandHint"
    ]
    assert any("events above" in h for h in coll_hints)


def test_scope_section_carries_inline_hint(qapp):
    """The Scope row's section label is followed by a small italic
    "events to look in" hint (spec/90 §5.5)."""
    dlg = _collection_dialog(qapp)
    scope = dlg.findChild(object, "ScopeSection")
    hints = [
        lbl.text() for lbl in scope.findChildren(QLabel)
        if lbl.objectName() == "BandHint"
    ]
    assert any("events to look in" in h for h in hints)


def test_footer_contains_only_cancel_and_start(qapp):
    """spec/90 §5 / spec/162 §5 — the footer / launch pad hosts Cancel +
    the mode-specific primary CTA; saves live with their data (Recipe
    toolbar / Which items? band). spec/162 Round 1b renamed the primary
    button per mode:

      New Cut mode  → ``▶ Freeze and Pick``
      Edit Cut mode → ``▶ Save Changes and Pick``  (covered by a
                       separate edit-mode test)
    """
    from PyQt6.QtWidgets import QPushButton
    dlg = _cut_dialog(qapp)
    # Walk the dialog's children to find the footer host (the bottom
    # widget that hosts the primary button — post-Round 1b it wears
    # the #LaunchPad role).
    footer = dlg._start_btn.parent()
    button_texts = sorted(
        (b.text() or "") for b in footer.findChildren(QPushButton)
        # ignore close-X buttons / hidden helpers
        if b.text() and b is not None
    )
    assert "Cancel" in button_texts
    assert any("Freeze and Pick" in t for t in button_texts)
    assert not any("Save as Recipe" in t for t in button_texts)
    # Vocabulary rename to "Collection" landed in spec/94 Phase 1.
    assert not any("Save as Collection" in t for t in button_texts)
    assert not any("Load Recipe" in t for t in button_texts)
    assert not any("Load Collection" in t for t in button_texts)


def test_recipe_toolbar_present_with_both_recipe_buttons(qapp):
    """spec/90 §5 — top-of-body Recipe toolbar carries Load Recipe… and
    Save as Recipe…; the dialog header bar no longer hosts them."""
    from PyQt6.QtWidgets import QPushButton
    dlg = _cut_dialog(qapp)
    toolbar = _section(dlg, "RecipeToolbar")
    assert toolbar is not None
    texts = sorted(
        (b.text() or "") for b in toolbar.findChildren(QPushButton)
        if b.text()
    )
    assert any("Load Recipe" in t for t in texts)
    assert any("Save as Recipe" in t for t in texts)


def test_load_dc_button_lives_on_which_items_band(qapp):
    """spec/90 §5 — Load DC… sits next to Save as DC… on the Which
    items? header (the items-layer mirror of Load Recipe)."""
    dlg = _cut_dialog(qapp)
    which = _section(dlg, "WhichItemsBand")
    assert dlg._load_dc_btn.parent() is which


def test_inner_section_cards_render_for_each_inner_box(qapp):
    """spec/90 §5 — every inner section (Source / Filters / Rules /
    Otherwise / Runtime / Metrics) is wrapped in a card-style frame so
    the visual hierarchy reads cleanly inside the band groups."""
    dlg = _collection_dialog(qapp)
    for name in (
        "SourceSection", "FiltersSection",
        "RulesSectionCard", "OtherwiseSectionCard",
        "RuntimeSectionCard", "MetricsSectionCard",
    ):
        assert _section(dlg, name) is not None, name


def test_initial_resize_accommodates_widest_header_row(qapp):
    """spec/90 §5 — the dialog opens wide enough that the Recipe-toolbar
    and Which items? header buttons don't fall off the right edge."""
    dlg = _collection_dialog(qapp)
    # Initial resize was set in __init__ via max(sizeHint(), 660). The
    # sizeHint reflects the laid-out band-group + toolbar widths, so
    # the dialog's current width must cover the layout's minimum.
    layout = dlg.layout()
    assert layout is not None
    min_w = layout.minimumSize().width() if layout else 660
    assert dlg.width() >= min_w


def test_name_and_scope_wrapped_as_lightboxes(qapp):
    """Name and (Collection) Scope are wrapped as light-secondary-surface
    containers — the same visual tier as the Recipe toolbar and band
    groups — so the dialog's top tier reads as a uniform family."""
    dlg = _collection_dialog(qapp)
    assert _section(dlg, "NameBox") is not None
    assert _section(dlg, "ScopeBox") is not None


# --------------------------------------------------------------------------- #
# spec/162 Round 1b — scope / mode / contextual labels / budget hide
# --------------------------------------------------------------------------- #


def test_scope_event_maps_to_flavour_cut(qapp):
    """spec/162 §2 — `scope=SCOPE_EVENT` resolves to the same behaviour
    as the legacy `flavour=FLAVOUR_CUT` code path."""
    dlg = NewCutDialog(
        scope=SCOPE_EVENT, show_scope=False, show_hardware=False,
        inventory_scope=INVENTORY_EVENT, ctx=_ctx(),
    )
    assert dlg._scope == SCOPE_EVENT
    assert dlg._flavour == FLAVOUR_CUT


def test_scope_cross_event_maps_to_flavour_collection(qapp):
    """spec/162 §2 — `scope=SCOPE_CROSS_EVENT` resolves to the legacy
    `flavour=FLAVOUR_COLLECTION` code path (until Round 2 retires the
    Collection flavour surface)."""
    dlg = NewCutDialog(
        scope=SCOPE_CROSS_EVENT, show_scope=True, show_hardware=True,
        inventory_scope=INVENTORY_LIBRARY,
        ctx=_ctx(cameras=("Pana+G9M2",), lenses=("100-500mm",)),
    )
    assert dlg._scope == SCOPE_CROSS_EVENT
    assert dlg._flavour == FLAVOUR_COLLECTION


def test_scope_and_flavour_together_when_disagreeing_raises(qapp):
    with pytest.raises(ValueError, match="disagree"):
        NewCutDialog(
            scope=SCOPE_EVENT, flavour=FLAVOUR_COLLECTION,
            show_scope=False, show_hardware=False,
            inventory_scope=INVENTORY_EVENT, ctx=_ctx(),
        )


def test_neither_scope_nor_flavour_raises(qapp):
    with pytest.raises(ValueError, match="scope|flavour"):
        NewCutDialog(
            show_scope=False, show_hardware=False,
            inventory_scope=INVENTORY_EVENT, ctx=_ctx(),
        )


def test_invalid_mode_raises(qapp):
    with pytest.raises(ValueError, match="mode"):
        NewCutDialog(
            scope=SCOPE_EVENT, mode="rewrite",
            show_scope=False, show_hardware=False,
            inventory_scope=INVENTORY_EVENT, ctx=_ctx(),
        )


def test_new_mode_primary_button_is_freeze_and_pick(qapp):
    """spec/162 §5.1 — New Cut mode primary CTA reads ‘Freeze and Pick’."""
    dlg = _cut_dialog(qapp)
    assert "Freeze and Pick" in dlg._start_btn.text()


def test_edit_mode_primary_button_is_save_changes_and_pick(qapp):
    """spec/162 §5.2 — Edit Cut mode primary CTA reads
    ‘Save Changes and Pick’."""
    ctx = _ctx()
    ctx.name = "Sunday best"
    ctx.is_editing = True
    dlg = NewCutDialog(
        scope=SCOPE_EVENT, mode=MODE_EDIT,
        show_scope=False, show_hardware=False,
        inventory_scope=INVENTORY_EVENT, ctx=ctx,
    )
    assert "Save Changes and Pick" in dlg._start_btn.text()


def test_edit_mode_window_title_shows_cut_name(qapp):
    """spec/162 §5.2 / §4.1 — the window title is ‘Edit Cut · <name>’ in
    edit mode."""
    ctx = _ctx()
    ctx.name = "Sunday best"
    ctx.is_editing = True
    dlg = NewCutDialog(
        scope=SCOPE_EVENT, mode=MODE_EDIT,
        show_scope=False, show_hardware=False,
        inventory_scope=INVENTORY_EVENT, ctx=ctx,
    )
    assert dlg.windowTitle() == "Edit Cut · Sunday best"


def test_new_mode_window_title_is_new_cut(qapp):
    """spec/162 §5.1 — the window title is just ‘New Cut’ in new mode."""
    dlg = _cut_dialog(qapp)
    assert dlg.windowTitle() == "New Cut"


def test_edit_mode_cancel_reads_discard_changes(qapp):
    """spec/162 §5.2 — the cancel button reads ‘Discard Changes’ in
    edit mode."""
    from PyQt6.QtWidgets import QPushButton
    ctx = _ctx()
    ctx.name = "Sunday best"
    ctx.is_editing = True
    dlg = NewCutDialog(
        scope=SCOPE_EVENT, mode=MODE_EDIT,
        show_scope=False, show_hardware=False,
        inventory_scope=INVENTORY_EVENT, ctx=ctx,
    )
    footer = dlg._start_btn.parent()
    texts = [b.text() for b in footer.findChildren(QPushButton) if b.text()]
    assert "Discard Changes" in texts
    assert "Cancel" not in texts


def test_footer_wears_launch_pad_role(qapp):
    """spec/162 §4.6 — the button row lives in a widget wearing the
    #LaunchPad role so Slice 1's ink-tinted QSS strip paints."""
    dlg = _cut_dialog(qapp)
    footer = dlg._start_btn.parent()
    assert footer.objectName() == "LaunchPad"


def test_otherwise_lead_reads_starts_all_when_no_rules(qapp):
    """spec/162 §4.6 — the leading label reads ‘Starts all:’ when no
    rule has been added yet."""
    dlg = _cut_dialog(qapp)
    assert dlg._otherwise_lead.text() == "Starts all:"


def test_otherwise_lead_reads_otherwise_after_a_rule_is_added(qapp):
    """spec/162 §4.6 — the leading label flexes to ‘Otherwise:’ once at
    least one rule has been added."""
    dlg = _cut_dialog(qapp)
    dlg._on_add_rule_clicked()
    assert dlg._otherwise_lead.text() == "Otherwise:"


def test_otherwise_lead_flexes_back_when_last_rule_is_deleted(qapp):
    """spec/162 §4.6 — the leading label flexes back to ‘Starts all:’ if
    the user deletes the last rule."""
    dlg = _cut_dialog(qapp)
    dlg._on_add_rule_clicked()
    assert dlg._otherwise_lead.text() == "Otherwise:"
    dlg._delete_rule(dlg._rule_rows[0])
    assert dlg._otherwise_lead.text() == "Starts all:"


def test_budget_row_hides_when_budget_check_unticks(qapp):
    """spec/162 §4.4 — the WHOLE Budget row hides when the checkbox is
    unchecked (not merely disables)."""
    ctx = _ctx()
    ctx.has_budget = True
    dlg = NewCutDialog(
        scope=SCOPE_EVENT, show_scope=False, show_hardware=False,
        inventory_scope=INVENTORY_EVENT, ctx=ctx,
    )
    # Force layout so isVisibleTo returns a stable answer.
    dlg.show()
    assert dlg._target_box.isVisibleTo(dlg) is True
    assert dlg._max_box.isVisibleTo(dlg) is True

    dlg._budget_check.setChecked(False)
    assert dlg._target_box.isVisibleTo(dlg) is False
    assert dlg._max_box.isVisibleTo(dlg) is False

    dlg._budget_check.setChecked(True)
    assert dlg._target_box.isVisibleTo(dlg) is True
    assert dlg._max_box.isVisibleTo(dlg) is True


def test_edit_mode_coerces_ctx_is_editing_true(qapp):
    """spec/162 Round 1b — if the caller passes mode=edit but a
    lagging ctx.is_editing=False, the dialog coerces the copy so
    downstream code (e.g. _refresh_start_enabled) reads the edit-mode
    branch."""
    ctx = _ctx()
    ctx.is_editing = False  # lag
    dlg = NewCutDialog(
        scope=SCOPE_EVENT, mode=MODE_EDIT,
        show_scope=False, show_hardware=False,
        inventory_scope=INVENTORY_EVENT, ctx=ctx,
    )
    assert dlg._is_editing is True
