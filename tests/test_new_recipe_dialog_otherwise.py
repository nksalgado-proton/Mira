"""spec/90 Phase 4c — Otherwise row tests.

* Otherwise row is always present in the dialog body.
* Default verdict is ``skip`` (matches spec/90 §3.5's pick-in shape —
  the most common starting point).
* The verdict pill flips to green / red based on the chosen value.
* :meth:`otherwise_verdict` returns the resolver-expected string.
* Initial verdict can be seeded via ``ctx.otherwise``.
"""
from __future__ import annotations

import pytest

from mira.ui.pages.new_cut_dialog import (
    SCOPE_EVENT,
    INVENTORY_EVENT,
    NewRecipeContext,
    NewCutDialog,
    OperandOption,
    VERDICT_PICK,
    VERDICT_SKIP,
    _VerdictPill,
    _VerbPopover,
)


def _dialog(qapp, *, otherwise=VERDICT_SKIP) -> NewCutDialog:
    ctx = NewRecipeContext(
        available_pools=[
            OperandOption(name="#exported", count=12, kind="base"),
        ],
        available_styles=["macro"],
        otherwise=otherwise,
    )
    return NewCutDialog(
        scope=SCOPE_EVENT,
        show_scope=False,
        show_hardware=False,
        inventory_scope=INVENTORY_EVENT,
        ctx=ctx,
    )


# --------------------------------------------------------------------------- #
# Presence + defaults
# --------------------------------------------------------------------------- #


def test_otherwise_row_is_present(qapp):
    dlg = _dialog(qapp)
    assert dlg.findChild(object, "OtherwiseSection") is not None


def test_otherwise_pill_is_a_verdict_pill(qapp):
    dlg = _dialog(qapp)
    assert isinstance(dlg._otherwise_pill, _VerdictPill)


def test_otherwise_default_is_skip(qapp):
    """spec/90 §3.5 — pick-in is the most common shape (no rules +
    Otherwise → skip). Default to skip so the dialog opens on it."""
    dlg = _dialog(qapp)
    assert dlg.otherwise_verdict() == VERDICT_SKIP
    assert dlg._otherwise_pill.verdict() == VERDICT_SKIP
    assert dlg._otherwise_pill.objectName() == "VerdictSkipPill"


def test_otherwise_initial_verdict_can_be_seeded(qapp):
    dlg = _dialog(qapp, otherwise=VERDICT_PICK)
    assert dlg.otherwise_verdict() == VERDICT_PICK
    assert dlg._otherwise_pill.verdict() == VERDICT_PICK
    assert dlg._otherwise_pill.objectName() == "VerdictPickPill"


def test_otherwise_invalid_seed_falls_back_to_skip(qapp):
    """Unknown values fall back to the safe default."""
    ctx = NewRecipeContext(
        available_pools=[
            OperandOption(name="#exported", count=12, kind="base"),
        ],
        otherwise="maybe",   # not a valid verdict
    )
    dlg = NewCutDialog(
        scope=SCOPE_EVENT, show_scope=False, show_hardware=False,
        inventory_scope=INVENTORY_EVENT, ctx=ctx,
    )
    assert dlg.otherwise_verdict() == VERDICT_SKIP


# --------------------------------------------------------------------------- #
# Verdict swap via pill + verb popover
# --------------------------------------------------------------------------- #


def test_setting_pill_emits_dialog_state_change(qapp):
    """The pill emits :attr:`chosen` after a user-picked swap;
    :meth:`_on_otherwise_chosen` mirrors to the model."""
    dlg = _dialog(qapp)
    # Simulate the popover delivering the user's choice.
    dlg._otherwise_pill._on_chosen(VERDICT_PICK)
    assert dlg.otherwise_verdict() == VERDICT_PICK
    assert dlg._otherwise_pill.objectName() == "VerdictPickPill"


def test_setting_pill_back_to_skip(qapp):
    dlg = _dialog(qapp, otherwise=VERDICT_PICK)
    dlg._otherwise_pill._on_chosen(VERDICT_SKIP)
    assert dlg.otherwise_verdict() == VERDICT_SKIP
    assert dlg._otherwise_pill.objectName() == "VerdictSkipPill"


def test_unknown_verdict_is_ignored(qapp):
    """Defensive: an invalid value from somewhere upstream doesn't
    leave the dialog in an unclear state."""
    dlg = _dialog(qapp, otherwise=VERDICT_SKIP)
    dlg._on_otherwise_chosen("maybe")
    assert dlg.otherwise_verdict() == VERDICT_SKIP


# --------------------------------------------------------------------------- #
# composition() — Otherwise is always emitted
# --------------------------------------------------------------------------- #


def test_composition_always_includes_otherwise(qapp):
    """spec/90 §1.1 — Otherwise always has a verdict; the composition
    always includes the key."""
    dlg = _dialog(qapp)
    comp = dlg.composition()
    assert comp["otherwise"] == VERDICT_SKIP

    dlg._on_otherwise_chosen(VERDICT_PICK)
    comp2 = dlg.composition()
    assert comp2["otherwise"] == VERDICT_PICK
