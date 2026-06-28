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
from mira.shared.cut_draft import (
    CrossEventCutDraft,
    CutDraft,
    PIN_PICK_IN,
    PIN_WEED_OUT,
)
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


# --------------------------------------------------------------------------- #
# path A — the Pin → Cut flow drops the inline filter wall
# --------------------------------------------------------------------------- #


def test_show_filters_false_drops_the_filter_section(qapp):
    """The cross-event Pin → Cut flow passes show_filters=False: the dialog
    builds no Style / Camera / Lens / Media widgets, and composition carries
    empty filters (the pinned Collection filters via the source operand)."""
    dlg = NewRecipeDialog(
        flavour=FLAVOUR_COLLECTION, show_scope=True, show_hardware=True,
        show_filters=False, inventory_scope=INVENTORY_LIBRARY,
        ctx=_ctx(with_source=True))
    assert dlg._style_chips == {}
    assert dlg._camera_chips == {}
    assert dlg._lens_chips == {}
    assert dlg._photos_cb is None and dlg._videos_cb is None
    comp = dlg.composition()
    assert comp["filters"].get("styles") == []
    assert comp["filters"].get("media_type") == "both"


def test_show_filters_true_keeps_the_filter_section(qapp):
    """The default (event-scope Cut + inline Collection authoring) keeps the
    thin Style + Media filters."""
    dlg = _dialog(qapp, ctx=_ctx(with_source=True))
    assert "macro" in dlg._style_chips
    assert dlg._photos_cb is not None and dlg._videos_cb is not None


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


def test_collection_flavour_start_emits_cross_event_cut_draft(qapp):
    """spec/90 Phase 4f — Collection-flavour Start no longer raises.
    Clicking Start translates the composition via
    :func:`recipe_to_cross_event_cut_draft` and emits a
    :class:`CrossEventCutDraft` on :attr:`start_requested`. The host
    wires :class:`CrossEventCutSession.from_draft` + the cross-event
    picker."""
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
    dlg._name_edit.setText("curated_macro")
    drafts: list = []
    dlg.start_requested.connect(drafts.append)
    dlg._on_start_clicked()
    assert len(drafts) == 1
    draft = drafts[0]
    assert isinstance(draft, CrossEventCutDraft)
    assert draft.name == "curated_macro"
    assert draft.tag == "curated_macro"
    assert ("+", "exported") in draft.expr
    # Otherwise default (skip) → pin_mode = PIN_PICK_IN (spec/90 §1.5 sugar).
    assert draft.pin_mode == PIN_PICK_IN


def test_collection_flavour_start_uses_pin_mode_from_otherwise(qapp):
    """spec/90 §1.5 — no rules + Otherwise=pick collapses to
    :data:`PIN_WEED_OUT` on the cross-event draft."""
    ctx = NewRecipeContext(
        available_pools=[OperandOption(name="#exported", count=42,
                                       kind="base", tag="exported")],
        selected_source=[(JOIN_OR, OperandOption(
            name="#exported", count=42, kind="base", tag="exported"))],
        otherwise="pick",
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
    drafts: list = []
    dlg.start_requested.connect(drafts.append)
    dlg._on_start_clicked()
    assert drafts[0].pin_mode == PIN_WEED_OUT


def test_editing_mode_start_enabled_even_with_empty_pool(qapp):
    """spec/90 Phase 4e edit note (Nelson 2026-06-20): when the dialog
    opens via Adjust on an existing Cut (``is_editing=True``), Start
    enables as long as Source is non-empty — even if the current
    resolution is an empty pool. The user may be clearing the budget
    on a Cut whose source's exports were deleted, and that metadata
    change should still save."""
    ctx = NewRecipeContext(
        available_pools=_pools(),
        selected_source=[(JOIN_OR, _pools()[0])],
        is_editing=True,
    )
    dlg = _dialog(qapp, ctx=ctx,
                  recipe_probe=lambda _c: _resolution(0))  # empty pool
    dlg._run_probe()
    assert dlg._start_btn.isEnabled() is True


def test_editing_mode_start_enabled_even_with_probe_error(qapp):
    """When the source references a missing operand, Adjust still lets
    the user save (e.g., to clear the budget) — the picker handles
    the empty-resolution case gracefully."""
    def probe(_comp):
        raise RecipeResolutionError("missing", kind="cut")
    ctx = NewRecipeContext(
        available_pools=_pools(),
        selected_source=[(JOIN_OR, _pools()[0])],
        is_editing=True,
    )
    dlg = _dialog(qapp, ctx=ctx, recipe_probe=probe)
    dlg._run_probe()
    assert dlg._start_btn.isEnabled() is True


def test_new_cut_mode_start_still_disabled_on_empty_pool(qapp):
    """Regression guard: with ``is_editing=False`` (New Cut path) the
    old gate still protects accidental empty new Cuts."""
    ctx = NewRecipeContext(
        available_pools=_pools(),
        selected_source=[(JOIN_OR, _pools()[0])],
        is_editing=False,
    )
    dlg = _dialog(qapp, ctx=ctx,
                  recipe_probe=lambda _c: _resolution(0))
    dlg._run_probe()
    assert dlg._start_btn.isEnabled() is False


def test_collection_flavour_start_accepts_dialog(qapp):
    """Collection Start emits then accepts() — same lifecycle as the
    Cut-flavour path."""
    ctx = NewRecipeContext(
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
        recipe_probe=lambda _comp: _resolution(5),
    )
    dlg._run_probe()
    accepted = []
    dlg.accepted.connect(lambda: accepted.append(True))
    dlg._on_start_clicked()
    assert accepted == [True]
