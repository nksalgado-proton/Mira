"""spec/90 Phase 4c — Verb + Join-word popover tests.

* Verb popover renders both options with plain-language descriptions
  per §3.3; the currently-selected option carries a ``selected="true"``
  Qt property.
* Join-word popover renders all three options with descriptions per
  §1.2 / §3.2.
* Clicking a join-word option in a Source row swaps the join and the
  next :meth:`source_expression` reflects the new operator.
* Same for Scope rows.
* Verb popover uses ``Qt.WindowType.Popup`` (dismiss-on-blur framework
  parity).
"""
from __future__ import annotations

import pytest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton

from mira.ui.pages.new_recipe_dialog import (
    FLAVOUR_COLLECTION,
    FLAVOUR_CUT,
    INVENTORY_EVENT,
    INVENTORY_LIBRARY,
    JOIN_AND,
    JOIN_BUT_NOT,
    JOIN_OR,
    JOIN_WORD_OPTIONS,
    NewRecipeContext,
    NewRecipeDialog,
    OperandOption,
    VERDICT_PICK,
    VERDICT_SKIP,
    VERB_OPTIONS,
    _JoinChevron,
    _JoinWordPopover,
    _VerbPopover,
    _VerdictPill,
)


def _pools():
    return [
        OperandOption(name="#exported", count=12, kind="base"),
        OperandOption(name="#long", count=200, kind="cut", tag="long",
                      id="cut-long"),
        OperandOption(name="#best", count=42, kind="dc", tag="best",
                      id="dc-best"),
    ]


def _events():
    return [
        OperandOption(name="[Alaska]", count=120, kind="event",
                      uuid="evt-alaska"),
        OperandOption(name="[Bali]", count=80, kind="event",
                      uuid="evt-bali"),
    ]


def _cut_dialog(qapp) -> NewRecipeDialog:
    return NewRecipeDialog(
        flavour=FLAVOUR_CUT, show_scope=False, show_hardware=False,
        inventory_scope=INVENTORY_EVENT,
        ctx=NewRecipeContext(
            available_pools=_pools(),
            available_styles=["macro"],
        ),
    )


def _collection_dialog(qapp) -> NewRecipeDialog:
    return NewRecipeDialog(
        flavour=FLAVOUR_COLLECTION, show_scope=True, show_hardware=True,
        inventory_scope=INVENTORY_LIBRARY,
        ctx=NewRecipeContext(
            available_pools=_pools(),
            available_events=_events(),
            available_styles=["macro"],
        ),
    )


# --------------------------------------------------------------------------- #
# Verb popover content + selected highlighting
# --------------------------------------------------------------------------- #


def test_verb_popover_renders_both_options(qapp):
    popover = _VerbPopover(selected=VERDICT_SKIP)
    keys = list(popover._rows.keys())
    assert keys == [VERDICT_PICK, VERDICT_SKIP]


def test_verb_popover_rows_include_plain_language_description(qapp):
    popover = _VerbPopover(selected=VERDICT_SKIP)
    for verdict, description in VERB_OPTIONS:
        txt = popover._rows[verdict].text() or ""
        # Description is in the second line of the row's HTML label.
        assert description in txt


def test_verb_popover_marks_selected(qapp):
    popover = _VerbPopover(selected=VERDICT_PICK)
    assert popover._rows[VERDICT_PICK].property("selected") == "true"
    assert popover._rows[VERDICT_SKIP].property("selected") == "false"


def test_verb_popover_emits_chosen_signal(qapp):
    popover = _VerbPopover(selected=VERDICT_SKIP)
    seen = []
    popover.chosen.connect(seen.append)
    popover._on_chosen(VERDICT_PICK)
    assert seen == [VERDICT_PICK]


def test_verb_popover_uses_popup_window_flag(qapp):
    """``Qt.WindowType.Popup`` gives the Qt-managed dismiss-on-outside-
    click behaviour we want for verb + join popovers — the framework's
    own blur handling, no manual install."""
    popover = _VerbPopover()
    assert bool(popover.windowFlags() & Qt.WindowType.Popup)


# --------------------------------------------------------------------------- #
# Join-word popover content + selected highlighting
# --------------------------------------------------------------------------- #


def test_join_word_popover_renders_all_three_options(qapp):
    popover = _JoinWordPopover(selected=JOIN_OR)
    keys = list(popover._rows.keys())
    assert keys == [JOIN_OR, JOIN_AND, JOIN_BUT_NOT]


def test_join_word_popover_rows_include_plain_language_description(qapp):
    popover = _JoinWordPopover()
    for join, description in JOIN_WORD_OPTIONS:
        txt = popover._rows[join].text() or ""
        assert description in txt


def test_join_word_popover_marks_selected(qapp):
    popover = _JoinWordPopover(selected=JOIN_AND)
    assert popover._rows[JOIN_AND].property("selected") == "true"
    assert popover._rows[JOIN_OR].property("selected") == "false"
    assert popover._rows[JOIN_BUT_NOT].property("selected") == "false"


def test_join_word_popover_uses_popup_window_flag(qapp):
    popover = _JoinWordPopover()
    assert bool(popover.windowFlags() & Qt.WindowType.Popup)


# --------------------------------------------------------------------------- #
# JoinChevron — clicking an option flips the chevron's word
# --------------------------------------------------------------------------- #


def test_join_chevron_renders_word_and_chevron(qapp):
    chevron = _JoinChevron(JOIN_OR)
    assert chevron.join_word() == JOIN_OR
    assert "or" in chevron.text()
    assert "⌄" in chevron.text()


def test_join_chevron_flips_after_chosen(qapp):
    chevron = _JoinChevron(JOIN_OR)
    seen = []
    chevron.chosen.connect(seen.append)
    chevron._on_chosen(JOIN_AND)
    assert chevron.join_word() == JOIN_AND
    assert seen == [JOIN_AND]
    assert "and" in chevron.text()


# --------------------------------------------------------------------------- #
# Source row: swapping the join word changes the emitted operator
# --------------------------------------------------------------------------- #


def test_source_row_join_chevron_swaps_to_and(qapp):
    dlg = _cut_dialog(qapp)
    dlg._add_source_chip(dlg._ctx.available_pools[0])   # exported
    dlg._add_source_chip(dlg._ctx.available_pools[1])   # long
    # Find the join chevron in the row.
    chevron = None
    for i in range(dlg._source_row.count()):
        w = dlg._source_row.itemAt(i).widget()
        if isinstance(w, _JoinChevron):
            chevron = w
            break
    assert chevron is not None
    # Initially union → "+".
    assert dlg.source_expression()[1][0] == "+"
    # Swap to "and" via the popover signal path.
    chevron._on_chosen(JOIN_AND)
    assert dlg.source_expression()[1][0] == "&"


def test_source_row_join_chevron_swaps_to_but_not_in(qapp):
    dlg = _cut_dialog(qapp)
    dlg._add_source_chip(dlg._ctx.available_pools[0])
    dlg._add_source_chip(dlg._ctx.available_pools[1])
    # Pull a fresh chevron — _refresh_source_row rebuilt the widgets
    # after _add_source_chip ran.
    chevron = next(
        w for i in range(dlg._source_row.count())
        for w in [dlg._source_row.itemAt(i).widget()]
        if isinstance(w, _JoinChevron)
    )
    chevron._on_chosen(JOIN_BUT_NOT)
    assert dlg.source_expression()[1][0] == "-"


def test_source_first_chip_has_no_chevron(qapp):
    """The first chip's join is always ``+`` (empty-accumulator union).
    No chevron is rendered before it."""
    dlg = _cut_dialog(qapp)
    dlg._add_source_chip(dlg._ctx.available_pools[0])
    chevrons = [
        w for i in range(dlg._source_row.count())
        for w in [dlg._source_row.itemAt(i).widget()]
        if isinstance(w, _JoinChevron)
    ]
    assert chevrons == []


# --------------------------------------------------------------------------- #
# Scope row: swapping the join word changes the emitted operator
# --------------------------------------------------------------------------- #


def test_scope_row_join_chevron_swaps_to_and(qapp):
    dlg = _collection_dialog(qapp)
    dlg._add_scope_chip(_events()[0])
    dlg._add_scope_chip(_events()[1])
    chevron = next(
        w for i in range(dlg._scope_row.count())
        for w in [dlg._scope_row.itemAt(i).widget()]
        if isinstance(w, _JoinChevron)
    )
    assert dlg.scope_expression()[1][0] == "+"
    chevron._on_chosen(JOIN_AND)
    assert dlg.scope_expression()[1][0] == "&"


def test_scope_row_join_chevron_swaps_to_but_not_in(qapp):
    dlg = _collection_dialog(qapp)
    dlg._add_scope_chip(_events()[0])
    dlg._add_scope_chip(_events()[1])
    chevron = next(
        w for i in range(dlg._scope_row.count())
        for w in [dlg._scope_row.itemAt(i).widget()]
        if isinstance(w, _JoinChevron)
    )
    chevron._on_chosen(JOIN_BUT_NOT)
    assert dlg.scope_expression()[1][0] == "-"


# --------------------------------------------------------------------------- #
# VerdictPill — opens VerbPopover; swapping fires chosen + flips state
# --------------------------------------------------------------------------- #


def test_verdict_pill_renders_initial_verdict(qapp):
    pill = _VerdictPill(VERDICT_PICK)
    assert pill.verdict() == VERDICT_PICK
    assert pill.objectName() == "VerdictPickPill"
    pill2 = _VerdictPill(VERDICT_SKIP)
    assert pill2.objectName() == "VerdictSkipPill"


def test_verdict_pill_chosen_signal_swaps_state(qapp):
    pill = _VerdictPill(VERDICT_SKIP)
    seen = []
    pill.chosen.connect(seen.append)
    pill._on_chosen(VERDICT_PICK)
    assert pill.verdict() == VERDICT_PICK
    assert seen == [VERDICT_PICK]
    assert pill.objectName() == "VerdictPickPill"


def test_verdict_pill_ignores_unknown_verdict(qapp):
    pill = _VerdictPill(VERDICT_SKIP)
    pill.set_verdict("maybe")
    assert pill.verdict() == VERDICT_SKIP
