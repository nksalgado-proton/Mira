"""spec/90 Phase 4e — :class:`NewRecipeDialog` Start button wiring.

* Start is disabled with an empty Source.
* Start is disabled when the probe raises :class:`RecipeResolutionError`.
* Start is disabled when the probe returns an empty pool.
* Start is enabled when the probe returns a non-empty pool with no errors.
* Clicking Start builds a :class:`CutDraft` via
  :func:`recipe_to_cut_draft` and emits :attr:`start_requested`; the
  picker session opens with the resolved seed pre-applied.
* Collection-flavour Start raises :class:`NotImplementedError` until
  cross-event Collection Start lands in a future phase.
"""
from __future__ import annotations

import pytest

from core.recipe_resolver import (
    RecipeResolution,
    RecipeResolutionError,
)
from mira.shared.cut_draft import CutDraft, PIN_PICK_IN
from mira.ui.pages.new_recipe_dialog import (
    FLAVOUR_COLLECTION,
    FLAVOUR_CUT,
    INVENTORY_EVENT,
    INVENTORY_LIBRARY,
    JOIN_OR,
    NewRecipeContext,
    NewRecipeDialog,
    OperandOption,
    VERDICT_SKIP,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _pools():
    return [
        OperandOption(name="#exported", count=42, kind="base",
                      tag="exported"),
        OperandOption(name="#bests", count=8, kind="cut",
                      tag="bests", id="cut-b"),
    ]


def _ctx(*, with_source: bool = False) -> NewRecipeContext:
    selected = []
    if with_source:
        selected = [(JOIN_OR, OperandOption(
            name="#exported", count=42, kind="base", tag="exported"))]
    return NewRecipeContext(
        event_name="Costa Rica 2026",
        available_pools=_pools(),
        available_styles=["macro"],
        selected_source=selected,
    )


def _dialog(qapp, *, recipe_probe=None, ctx=None,
            flavour=FLAVOUR_CUT, show_scope=False,
            show_hardware=False,
            inventory_scope=INVENTORY_EVENT) -> NewRecipeDialog:
    return NewRecipeDialog(
        flavour=flavour,
        show_scope=show_scope,
        show_hardware=show_hardware,
        inventory_scope=inventory_scope,
        ctx=ctx or _ctx(),
        recipe_probe=recipe_probe,
    )


def _resolution(pool_size: int, picked: int = 0) -> RecipeResolution:
    pool = [f"key-{i}" for i in range(pool_size)]
    seed = {f"key-{i}": (i < picked) for i in range(pool_size)}
    return RecipeResolution(pool=pool, seed=seed)


# --------------------------------------------------------------------------- #
# Start gate
# --------------------------------------------------------------------------- #


def test_start_disabled_with_empty_source(qapp):
    """Empty source → Start can never enable (the picker needs a pool)."""
    dlg = _dialog(qapp, ctx=_ctx(with_source=False),
                  recipe_probe=lambda _comp: _resolution(10, picked=5))
    dlg._run_probe()
    assert dlg._start_btn.isEnabled() is False


def test_start_disabled_when_probe_raises_resolution_error(qapp):
    """A missing named operand surfaces as :class:`RecipeResolutionError`
    — Start stays disabled until the user fixes the reference."""
    def probe(_comp):
        raise RecipeResolutionError("best_wildlife", kind="cut")
    dlg = _dialog(qapp, ctx=_ctx(with_source=True), recipe_probe=probe)
    dlg._run_probe()
    assert dlg._start_btn.isEnabled() is False
    # The error banner surfaces the missing operand.
    assert "best_wildlife" in dlg._metrics_banner.text()


def test_start_disabled_when_probe_returns_empty_pool(qapp):
    """A composition that resolves to an empty pool can't open the
    picker — Start stays disabled."""
    dlg = _dialog(qapp, ctx=_ctx(with_source=True),
                  recipe_probe=lambda _comp: _resolution(0))
    dlg._run_probe()
    assert dlg._start_btn.isEnabled() is False


def test_start_enabled_with_non_empty_pool(qapp):
    """A successful probe with at least one pool member enables Start."""
    dlg = _dialog(qapp, ctx=_ctx(with_source=True),
                  recipe_probe=lambda _comp: _resolution(5, picked=2))
    dlg._run_probe()
    assert dlg._start_btn.isEnabled() is True


def test_start_enabled_without_probe(qapp):
    """Without a wired probe (smokes / unit tests), Start enables as
    long as Source is non-empty — the picker layer is what validates
    pool-emptiness in that path."""
    dlg = _dialog(qapp, ctx=_ctx(with_source=True), recipe_probe=None)
    assert dlg._start_btn.isEnabled() is True


# --------------------------------------------------------------------------- #
# Start click
# --------------------------------------------------------------------------- #


def test_start_emits_cut_draft_via_adapter(qapp):
    """Clicking Start builds a :class:`CutDraft` via
    :func:`recipe_to_cut_draft` and emits it on
    :attr:`start_requested`. The composition's Source becomes the
    draft's ``expr``."""
    dlg = _dialog(qapp, ctx=_ctx(with_source=True),
                  recipe_probe=lambda _comp: _resolution(5, picked=3))
    dlg._run_probe()
    dlg._name_edit.setText("short")
    drafts: list = []
    dlg.start_requested.connect(drafts.append)
    dlg._on_start_clicked()
    assert len(drafts) == 1
    draft = drafts[0]
    assert isinstance(draft, CutDraft)
    assert draft.name == "short"
    assert draft.tag == "short"
    # Source has one operand (#exported) → expr is one (+, "exported") pair.
    assert ("+", "exported") in draft.expr
    # No rules + Otherwise=skip → pick-in (spec/90 §1.5 sugar).
    assert draft.pin_mode == PIN_PICK_IN


def test_start_accepts_the_dialog(qapp):
    """Start emits then accepts() — the host's exec() returns Accepted."""
    dlg = _dialog(qapp, ctx=_ctx(with_source=True),
                  recipe_probe=lambda _comp: _resolution(5))
    dlg._run_probe()
    accepted = []
    dlg.accepted.connect(lambda: accepted.append(True))
    dlg._on_start_clicked()
    assert accepted == [True]


def test_collection_flavour_start_raises_not_implemented(qapp):
    """Cross-event Collection Start is out of scope for v1 — clicking
    Start raises :class:`NotImplementedError` with a clear message."""
    ctx = NewRecipeContext(
        event_name="Library",
        available_pools=[OperandOption(name="#exported", count=42,
                                       kind="base", tag="exported")],
        selected_source=[(JOIN_OR, OperandOption(
            name="#exported", count=42, kind="base", tag="exported"))],
    )
    dlg = _dialog(
        qapp,
        ctx=ctx,
        flavour=FLAVOUR_COLLECTION,
        show_scope=True,
        show_hardware=True,
        inventory_scope=INVENTORY_LIBRARY,
        recipe_probe=lambda _comp: _resolution(5, picked=2),
    )
    dlg._run_probe()
    with pytest.raises(NotImplementedError, match="Collection"):
        dlg._on_start_clicked()
