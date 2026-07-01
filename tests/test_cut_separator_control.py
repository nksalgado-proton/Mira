"""spec/143 — restore the separator (on/off + card style) control in
the Cut dialog.

The Cut-creation dialog dropped its separators control during the
spec/90 Phase 4 sweep, so the user could no longer choose whether
day-separator / opener cards are inserted, or their style, per Cut;
it silently fell back to the global ``use_separators`` setting. The
data already exists on the Cut row (``Cut.separators`` + ``card_style``
in ``extras_json``) and is honoured by export + play — the dialog
just lost the control.

Six contracts:

* The control sets ``separators`` + ``card_style`` into
  ``composition()["presentation"]`` so :func:`recipe_to_cut_draft`
  carries the per-Cut choice into the draft.
* Prefill (Edit-mode) pre-selects the saved separators on/off + the
  saved card style.
* Off disables the card-style combo (saved pick survives the flip).
* End-to-end :meth:`EventGateway.create_cut` persists the separator
  on/off + card-style choice and reads it back via
  :meth:`EventGateway.cut_card_style`.
* The cross-event path is identical under
  ``INVENTORY_LIBRARY`` — same dialog, library gateway persistence.
* The pre-spec/143 composition schema gains exactly two keys; the
  existing ``photo_s`` / ``aspect`` / ``music_category`` keys stay
  in place so other dialog tests + the recipe adapter keep reading
  the same shape.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.gateway.library_gateway import LibraryGateway
from mira.store.repo import EventStore
from mira.ui.pages.new_cut_dialog import (
    SCOPE_EVENT,
    INVENTORY_EVENT,
    INVENTORY_LIBRARY,
    NewRecipeContext,
    NewCutDialog,
    OperandOption,
)
from mira.ui.pages.share_cuts_page import ShareCutsPage
from mira.user_store.repo import UserStore


NOW = "2026-06-25T00:00:00+00:00"


# --------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------- #


def _pools():
    return [OperandOption(name="#exported", count=200, kind="base")]


def _dialog(
    qapp,
    *,
    separators: bool = True,
    card_style: str = "black",
    inventory: str = INVENTORY_EVENT,
):
    ctx = NewRecipeContext(
        available_pools=_pools(),
        available_styles=["macro"],
        separators=separators,
        card_style=card_style,
    )
    return NewCutDialog(
        scope=SCOPE_EVENT,
        show_scope=False,
        show_hardware=False,
        inventory_scope=inventory,
        ctx=ctx,
    )


# --------------------------------------------------------------------- #
# 1. The control sets separators + card_style into composition()
# --------------------------------------------------------------------- #


def test_control_emits_separators_and_card_style_in_presentation(qapp):
    """spec/143 — separators on/off + card_style ride
    ``composition()["presentation"]`` so the adapter + downstream
    create_cut persist the per-Cut choice."""
    dlg = _dialog(qapp)
    try:
        # Default new-cut state: separators on, black cards.
        pres = dlg.composition()["presentation"]
        assert pres["separators"] is True
        assert pres["card_style"] == "black"

        # Pick a different style — the choice round-trips.
        dlg._card_style_combo.setCurrentIndex(
            dlg._card_style_combo.findData("multi"))
        pres = dlg.composition()["presentation"]
        assert pres["separators"] is True
        assert pres["card_style"] == "multi"

        # Flip separators Off — the value lands in the composition;
        # the style key persists too so a flip-back keeps the pick.
        dlg._separators_combo.setCurrentIndex(0)
        pres = dlg.composition()["presentation"]
        assert pres["separators"] is False
        assert pres["card_style"] == "multi"
    finally:
        dlg.deleteLater()


def test_off_disables_card_style_combo_keeping_the_pick(qapp):
    """Off → the style picker greys out (no effect today's behaviour),
    but the saved choice survives so flipping back restores the pick
    instantly — mirrors the overlay mode / fields gesture."""
    dlg = _dialog(qapp)
    try:
        dlg._card_style_combo.setCurrentIndex(
            dlg._card_style_combo.findData("single"))
        assert dlg._card_style_combo.isEnabled() is True

        # Off → combo disabled, but currentData still says "single".
        dlg._separators_combo.setCurrentIndex(0)
        assert dlg._card_style_combo.isEnabled() is False
        assert dlg._card_style_combo.currentData() == "single"
        # Composition stays honest — separators=False, style preserved.
        pres = dlg.composition()["presentation"]
        assert pres["separators"] is False
        assert pres["card_style"] == "single"

        # Flip back → re-enabled, still single, composition reflects it.
        dlg._separators_combo.setCurrentIndex(1)
        assert dlg._card_style_combo.isEnabled() is True
        pres = dlg.composition()["presentation"]
        assert pres["separators"] is True
        assert pres["card_style"] == "single"
    finally:
        dlg.deleteLater()


# --------------------------------------------------------------------- #
# 2. Prefill — opening Edit on an existing Cut pre-selects the choice
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("saved_style", ["black", "single", "multi"])
def test_prefill_seeds_card_style_from_ctx(qapp, saved_style):
    """spec/143 — the host prefills ``NewRecipeContext.card_style`` from
    ``eg.cut_card_style(cut)``; the dialog opens on that exact pick."""
    dlg = _dialog(qapp, card_style=saved_style)
    try:
        assert dlg._card_style == saved_style
        assert dlg._card_style_combo.currentData() == saved_style
        pres = dlg.composition()["presentation"]
        assert pres["card_style"] == saved_style
    finally:
        dlg.deleteLater()


@pytest.mark.parametrize("saved_separators", [True, False])
def test_prefill_seeds_separators_from_ctx(qapp, saved_separators):
    """spec/143 — the host prefills ``NewRecipeContext.separators`` from
    ``cut.separators``; the dialog's combo opens on that pick."""
    dlg = _dialog(qapp, separators=saved_separators)
    try:
        assert dlg._separators is saved_separators
        assert dlg._separators_combo.currentData() is saved_separators
        # Off → style combo disabled out of the gate.
        assert dlg._card_style_combo.isEnabled() is saved_separators
        pres = dlg.composition()["presentation"]
        assert pres["separators"] is saved_separators
    finally:
        dlg.deleteLater()


def test_prefill_drops_unknown_card_style(qapp):
    """A legacy / typo'd card_style reads as the canonical default —
    the dialog never surfaces a value the renderer can't honour."""
    dlg = _dialog(qapp, card_style="cinemascope")
    try:
        assert dlg._card_style == "black"
        assert dlg._card_style_combo.currentData() == "black"
    finally:
        dlg.deleteLater()


# --------------------------------------------------------------------- #
# 3. ShareCutsPage._apply_recipe_prefill threads the Cut row through
# --------------------------------------------------------------------- #


def test_share_cuts_prefill_seeds_ctx_separators_and_style():
    """spec/143 — ``_on_adjust_cut`` packs ``separators`` +
    ``card_style`` onto the prefill ``SimpleNamespace`` from the Cut
    row; ``_apply_recipe_prefill`` lands both on the ctx so the
    dialog opens on the user's per-Cut choice."""
    ctx = NewRecipeContext(
        available_pools=[OperandOption(name="#exported",
                                       count=10, kind="base")],
        available_styles=[],
    )
    # Baseline defaults: ctx.separators=True, ctx.card_style="black".
    assert ctx.separators is True
    assert ctx.card_style == "black"

    prefill = SimpleNamespace(separators=False, card_style="multi")
    ShareCutsPage._apply_recipe_prefill(None, ctx, prefill, {})
    assert ctx.separators is False
    assert ctx.card_style == "multi"


def test_share_cuts_prefill_drops_unknown_card_style():
    """An unknown card_style on the prefill leaves the context at
    its default — the dialog never opens on a value the renderer
    can't honour."""
    ctx = NewRecipeContext(
        available_pools=[OperandOption(name="#exported",
                                       count=10, kind="base")],
        available_styles=[],
    )
    prefill = SimpleNamespace(separators=True, card_style="cinemascope")
    ShareCutsPage._apply_recipe_prefill(None, ctx, prefill, {})
    assert ctx.card_style == "black"


def test_share_cuts_prefill_absent_separators_keeps_default():
    """A prefill that doesn't carry ``separators`` (legacy callers)
    leaves the ctx at its current value."""
    ctx = NewRecipeContext(
        available_pools=[OperandOption(name="#exported",
                                       count=10, kind="base")],
        available_styles=[],
        separators=True,
    )
    prefill = SimpleNamespace()                    # no .separators
    ShareCutsPage._apply_recipe_prefill(None, ctx, prefill, {})
    assert ctx.separators is True


# --------------------------------------------------------------------- #
# 4. End-to-end — create_cut persists separators + card_style
# --------------------------------------------------------------------- #


def _make_event_gw(tmp_path) -> EventGateway:
    """A minimal event.db + one Exported Media file so the cut-create
    path has somewhere to land. Mirrors the test_new_recipe_overlay
    fixture pattern."""
    from tests.test_gateway_cuts import _doc, _now
    store = EventStore.create(tmp_path / "event.db", event_id="evt-s")
    store.save_document(_doc())
    p = tmp_path / "Exported Media" / "e1.jpg"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"FILE:e1.jpg")
    gw = EventGateway(store, event_root=tmp_path, now=_now)
    return gw


def test_create_cut_persists_separators_and_card_style(tmp_path):
    """spec/143 — the dialog → create_cut path persists the per-Cut
    separator on/off + card-style choice. The style round-trips via
    ``cut_card_style`` (which reads the ``extras_json`` column)."""
    gw = _make_event_gw(tmp_path)
    try:
        cut = gw.create_cut(
            "show", separators=False, card_style="multi",
        )
        loaded = gw.cut(cut.id)
        assert loaded is not None
        assert loaded.separators is False
        assert gw.cut_card_style(loaded) == "multi"
    finally:
        gw.close()


# --------------------------------------------------------------------- #
# 5. Cross-event side — same dialog, library gateway persistence
# --------------------------------------------------------------------- #


def test_cross_event_dialog_emits_separators_in_composition(qapp):
    """spec/143 §4 — the cross-event surface uses the same
    NewCutDialog under ``INVENTORY_LIBRARY``. The contract is
    identical: pick on/off + style, composition carries both."""
    dlg = _dialog(qapp, separators=False, inventory=INVENTORY_LIBRARY)
    try:
        # Cross-event default is Off — the combo opens there.
        assert dlg._separators_combo.currentData() is False
        # Flip on + pick the multi style.
        dlg._separators_combo.setCurrentIndex(1)
        dlg._card_style_combo.setCurrentIndex(
            dlg._card_style_combo.findData("multi"))
        pres = dlg.composition()["presentation"]
        assert pres["separators"] is True
        assert pres["card_style"] == "multi"
    finally:
        dlg.deleteLater()


def test_cross_event_create_cut_persists_separators_and_card_style(tmp_path):
    """spec/143 — same dialog (with ``INVENTORY_LIBRARY``) drives
    ``LibraryGateway.create_cross_event_cut``; both the bool column
    and the ``extras_json``-backed card_style persist."""
    us = UserStore.create(
        tmp_path / "mira.db", app_version="t", created_at=NOW)
    try:
        lg = LibraryGateway(us, now=lambda: NOW)
        cut = lg.create_cross_event_cut(
            "share", separators=True, card_style="single",
        )
        loaded = lg.cross_event_cut(cut.id)
        assert loaded is not None
        assert loaded.separators is True
        # The cross-event reader for card_style: same shape as the
        # per-event gateway (extras_json with the canonical key).
        import json as _json
        assert _json.loads(loaded.extras_json).get("card_style") == "single"
    finally:
        us.close()


# --------------------------------------------------------------------- #
# 6. Regression — the pre-spec/143 composition schema gains exactly
# two keys; everything else stays in place
# --------------------------------------------------------------------- #


def test_composition_schema_carries_existing_keys_and_two_new_ones(qapp):
    """Pin the presentation block schema so the recipe adapter + the
    other dialog tests keep reading the same shape. spec/143 adds
    ``separators`` + ``card_style``; the pre-existing ``photo_s`` /
    ``music_category`` / ``aspect`` / ``target_s`` / ``max_s`` keys
    stay."""
    dlg = _dialog(qapp)
    try:
        pres = dlg.composition()["presentation"]
        # Pre-spec/143 keys must still be there (the regression guard).
        assert "photo_s" in pres
        assert "music_category" in pres
        assert "aspect" in pres
        assert "target_s" in pres
        assert "max_s" in pres
        # New spec/143 keys are present unconditionally.
        assert "separators" in pres
        assert "card_style" in pres
    finally:
        dlg.deleteLater()
