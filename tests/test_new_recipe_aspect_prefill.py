"""spec/121 §1 — aspect ratio pre-fills correctly on Cut edit.

Re-opening the Edit dialog on a Cut saved as ``4:3`` / ``3:2`` /
``1:1`` must surface that aspect pre-selected. Pre-fix, the edit
prefill SimpleNamespace omitted ``aspect`` and ``_apply_recipe_prefill``
never seeded ``ctx.aspect``, so the dialog fell back to the
NewRecipeContext default (``16:9``) for every edit.

Three pins:

* :meth:`ShareCutsPage._apply_recipe_prefill` reads ``prefill.aspect`` and
  seeds ``ctx.aspect`` via :func:`core.cut_aspect.normalise`.
* An absent / blank / unknown aspect leaves the context at its
  ``"16:9"`` default (back-compat for any callers that don't pass
  the field).
* The dialog construction path reads ``ctx.aspect`` and sets both
  ``self._aspect`` AND the combo's index — so seeding the context is
  enough to pre-select the right entry.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.cut_aspect import (
    ASPECT_1_1, ASPECT_3_2, ASPECT_4_3, ASPECT_16_9,
)
from mira.ui.pages.new_cut_dialog import (
    FLAVOUR_CUT, INVENTORY_EVENT,
    NewRecipeContext, NewCutDialog, OperandOption,
)
from mira.ui.pages.share_cuts_page import ShareCutsPage


def _ctx() -> NewRecipeContext:
    """A bare NewRecipeContext — defaults give ``aspect="16:9"``."""
    return NewRecipeContext(
        available_pools=[OperandOption(name="#exported",
                                       count=10, kind="base")],
        available_styles=[],
    )


def _dialog(qapp, ctx: NewRecipeContext) -> NewCutDialog:
    return NewCutDialog(
        flavour=FLAVOUR_CUT,
        show_scope=False,
        show_hardware=False,
        inventory_scope=INVENTORY_EVENT,
        ctx=ctx,
    )


# --------------------------------------------------------------------- #
# _apply_recipe_prefill seeds ctx.aspect from prefill.aspect
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("saved_aspect", [
    ASPECT_4_3, ASPECT_3_2, ASPECT_1_1,
])
def test_prefill_aspect_seeds_context(saved_aspect):
    """spec/121 — a Cut saved 4:3 / 3:2 / 1:1 pre-fills back to that
    aspect via :meth:`ShareCutsPage._apply_recipe_prefill`."""
    ctx = _ctx()
    assert ctx.aspect == ASPECT_16_9               # baseline default
    prefill = SimpleNamespace(aspect=saved_aspect)
    ShareCutsPage._apply_recipe_prefill(None, ctx, prefill, {})
    assert ctx.aspect == saved_aspect


def test_prefill_aspect_unknown_falls_back_to_default():
    """An unknown aspect normalises to the default 16:9 — the dialog
    never gets a value the renderer can't honour."""
    ctx = _ctx()
    prefill = SimpleNamespace(aspect="cinemascope")
    ShareCutsPage._apply_recipe_prefill(None, ctx, prefill, {})
    assert ctx.aspect == ASPECT_16_9


def test_prefill_aspect_absent_leaves_context_default():
    """A prefill that doesn't carry ``aspect`` at all (legacy callers,
    DC-based new-from-pin path) keeps the context at its 16:9
    default."""
    ctx = _ctx()
    prefill = SimpleNamespace()                    # no .aspect at all
    ShareCutsPage._apply_recipe_prefill(None, ctx, prefill, {})
    assert ctx.aspect == ASPECT_16_9


def test_prefill_aspect_none_leaves_context_default():
    """An explicit ``aspect=None`` (the cross-event-pin path's value)
    also leaves the context default — the seeding only fires for a
    truthy value, mirroring the gateway's None-sentinel semantics."""
    ctx = _ctx()
    prefill = SimpleNamespace(aspect=None)
    ShareCutsPage._apply_recipe_prefill(None, ctx, prefill, {})
    assert ctx.aspect == ASPECT_16_9


# --------------------------------------------------------------------- #
# Dialog seeds self._aspect + the combo's selected index from ctx.aspect
# --------------------------------------------------------------------- #


def test_dialog_combo_pre_selects_seeded_aspect(qapp):
    """spec/121 — the dialog reads ``ctx.aspect`` at construction and
    seeds BOTH ``self._aspect`` and the combo's current index. Seeding
    the context (per the _apply_recipe_prefill fix above) is therefore
    enough to land the user on their saved aspect."""
    ctx = _ctx()
    prefill = SimpleNamespace(aspect=ASPECT_4_3)
    ShareCutsPage._apply_recipe_prefill(None, ctx, prefill, {})
    dlg = _dialog(qapp, ctx)
    try:
        assert dlg._aspect == ASPECT_4_3
        assert dlg._aspect_combo.currentData() == ASPECT_4_3
    finally:
        dlg.deleteLater()


def test_dialog_combo_defaults_to_16_9_when_prefill_absent(qapp):
    """Sanity-check the back-compat path: no aspect in the prefill →
    the dialog opens on 16:9 (the prior shipping behaviour)."""
    ctx = _ctx()
    ShareCutsPage._apply_recipe_prefill(None, ctx, SimpleNamespace(), {})
    dlg = _dialog(qapp, ctx)
    try:
        assert dlg._aspect == ASPECT_16_9
        assert dlg._aspect_combo.currentData() == ASPECT_16_9
    finally:
        dlg.deleteLater()
