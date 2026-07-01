"""spec/90 Phase 4a — Source section tests.

Picking an operand from the popover adds a chip; chip removal works; the
join-word reads as ``or`` between every pair (the dropdown widget lands
in 4c); the emitted ``composition['source']`` shape matches what
:mod:`core.recipe_resolver` expects.
"""
from __future__ import annotations

import pytest

from mira.ui.pages.new_cut_dialog import (
    SCOPE_EVENT,
    INVENTORY_EVENT,
    JOIN_OR,
    NewRecipeContext,
    NewCutDialog,
    OperandOption,
    _OperandPickerPopover,
)


def _make_ctx(**over) -> NewRecipeContext:
    pools = over.pop("available_pools", [
        OperandOption(name="#exported", count=12, kind="base"),
        OperandOption(name="#long", count=200, kind="cut", tag="long",
                      id="cut-long"),
        OperandOption(name="#best", count=42, kind="dc", tag="best",
                      id="dc-best"),
    ])
    kw = dict(event_name="Evt", available_styles=["macro"])
    kw.update(over)
    return NewRecipeContext(available_pools=pools, **kw)


def _dialog(qapp, *, ctx=None, **over) -> NewCutDialog:
    kw = dict(
        scope=SCOPE_EVENT,
        show_scope=False,
        show_hardware=False,
        inventory_scope=INVENTORY_EVENT,
        ctx=ctx or _make_ctx(),
    )
    kw.update(over)
    return NewCutDialog(**kw)


# --------------------------------------------------------------------------- #
# Operand picker — populates from ctx, search filters, choice fires
# --------------------------------------------------------------------------- #


def test_picker_lists_pools_by_kind(qapp):
    ctx = _make_ctx()
    picker = _OperandPickerPopover(ctx.available_pools)
    rows_text = [btn.text() for _pool, btn in picker._rows]
    # All three pools surfaced.
    assert any("#exported" in t for t in rows_text)
    assert any("#long" in t for t in rows_text)
    assert any("#best" in t for t in rows_text)


def test_picker_search_narrows_rows(qapp):
    ctx = _make_ctx()
    picker = _OperandPickerPopover(ctx.available_pools)
    picker._search.setText("best")
    # Only the matching row stays visible. Use ``not isHidden()`` rather
    # than ``isVisible()`` — the popover hasn't been shown yet, so
    # ``isVisible()`` returns False for every child unconditionally; what
    # we actually care about is whether the row's own visibility flag
    # was flipped by the filter.
    visible = [pool.name for pool, btn in picker._rows if not btn.isHidden()]
    assert visible == ["#best"]
    # Clearing the search re-shows everything.
    picker._search.setText("")
    visible_all = [pool.name for pool, btn in picker._rows if not btn.isHidden()]
    assert set(visible_all) == {"#exported", "#long", "#best"}


def test_picker_emits_chosen_for_clicked_row(qapp):
    ctx = _make_ctx()
    picker = _OperandPickerPopover(ctx.available_pools)
    seen = []
    picker.chosen.connect(seen.append)
    target_pool, target_btn = next(
        (p, b) for p, b in picker._rows if p.name == "#long")
    target_btn.click()
    assert len(seen) == 1
    assert seen[0].name == "#long"


def test_source_picker_hides_save_as_dc_entry(qapp):
    """spec/90 §5.5 — the Source-target popover no longer carries its
    own Save as DC entry; the band-header button on "Which items?" is
    the canonical path so the picker stays focused on operand pick."""
    from mira.ui.pages.new_cut_dialog import PICKER_TARGET_SOURCE
    ctx = _make_ctx()
    picker = _OperandPickerPopover(
        ctx.available_pools, target=PICKER_TARGET_SOURCE)
    assert picker._save_btn is None


def test_rule_predicate_picker_save_as_dc_emits_signal(qapp):
    """The popover Save as DC entry stays on the rule-predicate target —
    spec/90 §5.5 — that's the only entry point for saving a predicate
    as a reusable DC."""
    from mira.ui.pages.new_cut_dialog import PICKER_TARGET_RULE_PREDICATE
    ctx = _make_ctx()
    picker = _OperandPickerPopover(
        ctx.available_pools, target=PICKER_TARGET_RULE_PREDICATE)
    fired = []
    picker.save_as_dc_requested.connect(lambda: fired.append(True))
    picker._save_btn.click()
    assert fired == [True]


# --------------------------------------------------------------------------- #
# Source row — chips render and remove
# --------------------------------------------------------------------------- #


def test_empty_source_renders_lead_and_add_button(qapp):
    dlg = _dialog(qapp)
    assert dlg._source_chips == []
    # The "Start from" lead label is there, and the trailing + button is
    # findable.
    texts = []
    for i in range(dlg._source_row.count()):
        w = dlg._source_row.itemAt(i).widget()
        if w is not None:
            texts.append((type(w).__name__, w.objectName(),
                          getattr(w, "text", lambda: "")()))
    # At least one PoolAddLabel ("Start from") + one PoolStepperBtn ("+").
    assert any("PoolAddLabel" == t[1] for t in texts)
    assert any(t[2] == "+" for t in texts)


def test_adding_one_operand_renders_one_chip(qapp):
    dlg = _dialog(qapp)
    pool = dlg._ctx.available_pools[1]   # #long
    dlg._add_source_chip(pool)
    assert dlg._source_chips == [(JOIN_OR, pool)]
    # The chip widget actually exists in the row.
    from mira.ui.pages.new_cut_dialog import _SourceChip
    chips = [
        dlg._source_row.itemAt(i).widget()
        for i in range(dlg._source_row.count())
        if dlg._source_row.itemAt(i).widget() is not None
    ]
    assert any(isinstance(c, _SourceChip) for c in chips)


def test_adding_two_operands_inserts_a_join_chevron(qapp):
    """spec/90 §3.2 — two chips share a clickable join word between
    them. Phase 4c hooks each join word to :class:`_JoinChevron`, so
    the rendered label is ``"or ⌄"`` (the word + the chevron
    affordance). The chevron's ``join_word()`` returns the bare
    word."""
    dlg = _dialog(qapp)
    a, b = dlg._ctx.available_pools[0], dlg._ctx.available_pools[1]
    dlg._add_source_chip(a)
    dlg._add_source_chip(b)
    from mira.ui.pages.new_cut_dialog import _JoinChevron
    join_chevrons = []
    for i in range(dlg._source_row.count()):
        w = dlg._source_row.itemAt(i).widget()
        if isinstance(w, _JoinChevron):
            join_chevrons.append(w)
    assert len(join_chevrons) == 1
    assert join_chevrons[0].join_word() == JOIN_OR
    assert "or" in join_chevrons[0].text()


def test_remove_chip_drops_it_from_row(qapp):
    dlg = _dialog(qapp)
    a, b = dlg._ctx.available_pools[0], dlg._ctx.available_pools[1]
    dlg._add_source_chip(a)
    dlg._add_source_chip(b)
    dlg._remove_source_chip(0)
    assert len(dlg._source_chips) == 1
    assert dlg._source_chips[0][1].name == "#long"


# --------------------------------------------------------------------------- #
# Source expression encoding
# --------------------------------------------------------------------------- #


def test_source_expression_empty(qapp):
    dlg = _dialog(qapp)
    assert dlg.source_expression() == []


def test_source_expression_single_base(qapp):
    """spec/81 §2 — base token stays a bare string."""
    dlg = _dialog(qapp)
    dlg._add_source_chip(dlg._ctx.available_pools[0])   # #exported (base)
    assert dlg.source_expression() == [["+", "exported"]]


def test_source_expression_single_cut(qapp):
    """spec/81 §2 — named ref becomes a typed dict."""
    dlg = _dialog(qapp)
    dlg._add_source_chip(dlg._ctx.available_pools[1])   # #long (cut)
    assert dlg.source_expression() == [
        ["+", {"kind": "cut", "tag": "long", "id": "cut-long"}]
    ]


def test_source_expression_single_dc(qapp):
    dlg = _dialog(qapp)
    dlg._add_source_chip(dlg._ctx.available_pools[2])   # #best (dc)
    assert dlg.source_expression() == [
        ["+", {"kind": "dc", "tag": "best", "id": "dc-best"}]
    ]


def test_source_expression_two_chips_default_to_union(qapp):
    """Phase 4a defaults every join to ``or`` (union, ``+``). The first
    chip's operator is always ``+`` (the empty-accumulator union case
    spec/81 §2 documents)."""
    dlg = _dialog(qapp)
    dlg._add_source_chip(dlg._ctx.available_pools[0])   # exported
    dlg._add_source_chip(dlg._ctx.available_pools[1])   # long
    expr = dlg.source_expression()
    assert expr == [
        ["+", "exported"],
        ["+", {"kind": "cut", "tag": "long", "id": "cut-long"}],
    ]


# --------------------------------------------------------------------------- #
# Save-as-DC placeholder signal — Phase 4e hook
# --------------------------------------------------------------------------- #


@pytest.mark.skip(
    reason="spec/162 Round 2d.C — _on_save_as_dc_clicked retires with "
           "the Save-as-DC dialog surface (dead code from Round 2a's "
           "body rebuild).")
def test_save_as_dc_placeholder_emits_dialog_signal(qapp):
    """Retired — see the skip reason above."""
    ...
