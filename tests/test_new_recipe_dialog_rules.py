"""spec/90 Phase 4c — Rules section tests.

* Adding a rule creates a row with empty predicate, default verdict
  (``skip``), and the placeholder match count.
* Picking an operand adds it to that rule's predicate sentence.
* Multiple rules render with index numbers (1., 2., 3., …).
* Deleting a rule removes the row and re-numbers the rest.
* :meth:`_reorder_rule` rearranges the list (the testable drag-reorder
  seam).
* :meth:`rules_expression` emits the spec/90 §5.1 / resolver-expected
  shape.
* The rule-predicate picker shows Faces only when ``show_hardware=True``
  AND ``ctx.available_people`` non-empty.
"""
from __future__ import annotations

import pytest

from mira.ui.pages.new_cut_dialog import (
    SCOPE_CROSS_EVENT,
    SCOPE_EVENT,
    INVENTORY_EVENT,
    INVENTORY_LIBRARY,
    JOIN_AND,
    JOIN_OR,
    NewRecipeContext,
    NewCutDialog,
    OperandOption,
    PICKER_TARGET_RULE_PREDICATE,
    VERDICT_PICK,
    VERDICT_SKIP,
    _OperandPickerPopover,
    _RuleRow,
)


def _pools():
    return [
        OperandOption(name="#exported", count=12, kind="base"),
        OperandOption(name="#blurry", count=18, kind="cut", tag="blurry",
                      id="cut-blur"),
        OperandOption(name="#best_wildlife", count=7, kind="cut",
                      tag="best_wildlife", id="cut-bw"),
        OperandOption(name="#best_landscapes", count=10, kind="cut",
                      tag="best_landscapes", id="cut-bl"),
    ]


def _people():
    return [
        OperandOption(name="[Pedro]", kind="person", id="person-pedro"),
        OperandOption(name="[Maria]", kind="person", id="person-maria"),
    ]


def _cut_dialog(qapp, **over) -> NewCutDialog:
    ctx = NewRecipeContext(
        available_pools=_pools(),
        available_styles=["macro"],
    )
    kw = dict(
        scope=SCOPE_EVENT,
        show_scope=False,
        show_hardware=False,
        inventory_scope=INVENTORY_EVENT,
        ctx=ctx,
    )
    kw.update(over)
    return NewCutDialog(**kw)


def _collection_dialog(qapp, *, people=None, **over) -> NewCutDialog:
    ctx = NewRecipeContext(
        available_pools=_pools(),
        available_styles=["macro"],
        available_people=list(people) if people is not None else _people(),
    )
    kw = dict(
        scope=SCOPE_CROSS_EVENT,
        show_scope=True,
        show_hardware=True,
        inventory_scope=INVENTORY_LIBRARY,
        ctx=ctx,
    )
    kw.update(over)
    return NewCutDialog(**kw)


# --------------------------------------------------------------------------- #
# Add / delete rules
# --------------------------------------------------------------------------- #


def test_initial_rules_state_is_empty(qapp):
    dlg = _cut_dialog(qapp)
    assert dlg._rules == []
    assert dlg.rules_expression() == []
    assert len(dlg._rule_rows) == 0


def test_add_rule_creates_row_with_empty_predicate_default_skip(qapp):
    dlg = _cut_dialog(qapp)
    dlg._on_add_rule_clicked()
    assert len(dlg._rules) == 1
    predicate, verdict = dlg._rules[0]
    assert predicate == []
    assert verdict == VERDICT_SKIP
    assert len(dlg._rule_rows) == 1


def test_add_rule_row_renders_index_one(qapp):
    dlg = _cut_dialog(qapp)
    dlg._on_add_rule_clicked()
    assert dlg._rule_rows[0]._index_label.text() == "1."


def test_add_rule_row_renders_match_count_placeholder(qapp):
    """Phase 4c renders the placeholder ``(— match)``; Phase 4d wires
    the live number."""
    dlg = _cut_dialog(qapp)
    dlg._on_add_rule_clicked()
    txt = dlg._rule_rows[0]._match_label.text()
    assert "match" in txt
    # The em-dash placeholder marks "not computed yet".
    assert "—" in txt


def test_multiple_rules_have_sequential_indexes(qapp):
    dlg = _cut_dialog(qapp)
    dlg._on_add_rule_clicked()
    dlg._on_add_rule_clicked()
    dlg._on_add_rule_clicked()
    indexes = [r._index_label.text() for r in dlg._rule_rows]
    assert indexes == ["1.", "2.", "3."]


def test_deleting_rule_renumbers_remaining(qapp):
    dlg = _cut_dialog(qapp)
    for _ in range(3):
        dlg._on_add_rule_clicked()
    # Distinguish the rules by setting a unique verdict on the middle one.
    dlg._rule_rows[1].set_verdict(VERDICT_PICK)
    assert dlg._rules[1][1] == VERDICT_PICK

    dlg._delete_rule(dlg._rule_rows[0])     # delete the first one
    assert len(dlg._rules) == 2
    # Indices renumbered.
    assert [r._index_label.text() for r in dlg._rule_rows] == ["1.", "2."]
    # The previously-middle rule is now first AND keeps its verdict.
    assert dlg._rules[0][1] == VERDICT_PICK


# --------------------------------------------------------------------------- #
# Predicate flow
# --------------------------------------------------------------------------- #


def test_append_operand_via_row_adds_chip(qapp):
    dlg = _cut_dialog(qapp)
    dlg._on_add_rule_clicked()
    row = dlg._rule_rows[0]
    row.append_operand(_pools()[1])  # #blurry
    predicate = row.predicate()
    assert len(predicate) == 1
    assert predicate[0][0] == JOIN_OR
    assert predicate[0][1].tag == "blurry"


def test_dialog_routes_predicate_chip_via_picker_callback(qapp):
    """The picker fires ``chosen`` → dialog routes the operand to the
    right row via :meth:`_on_predicate_operand_chosen`."""
    dlg = _cut_dialog(qapp)
    dlg._on_add_rule_clicked()
    row = dlg._rule_rows[0]
    dlg._on_predicate_operand_chosen(row, _pools()[2])  # #best_wildlife
    assert dlg._rules[0][0][0][1].tag == "best_wildlife"


def test_remove_operand_from_predicate(qapp):
    dlg = _cut_dialog(qapp)
    dlg._on_add_rule_clicked()
    row = dlg._rule_rows[0]
    row.append_operand(_pools()[1])
    row.append_operand(_pools()[2])
    row.remove_operand(0)
    predicate = row.predicate()
    assert len(predicate) == 1
    assert predicate[0][1].tag == "best_wildlife"


def test_set_join_in_predicate_swaps_operator(qapp):
    dlg = _cut_dialog(qapp)
    dlg._on_add_rule_clicked()
    row = dlg._rule_rows[0]
    row.append_operand(_pools()[2])
    row.append_operand(_pools()[3])
    row.set_join(1, JOIN_AND)
    assert row.predicate()[1][0] == JOIN_AND


# --------------------------------------------------------------------------- #
# Verdict pill
# --------------------------------------------------------------------------- #


def test_set_verdict_via_row_updates_state(qapp):
    dlg = _cut_dialog(qapp)
    dlg._on_add_rule_clicked()
    row = dlg._rule_rows[0]
    row.set_verdict(VERDICT_PICK)
    assert row.verdict() == VERDICT_PICK
    assert dlg._rules[0][1] == VERDICT_PICK


def test_verdict_pill_object_name_flips_with_verdict(qapp):
    """spec/90 §3.3 — pick = green pill, skip = red. QSS hooks via the
    object name."""
    dlg = _cut_dialog(qapp)
    dlg._on_add_rule_clicked()
    row = dlg._rule_rows[0]
    assert row._verdict_pill.objectName() == "VerdictSkipPill"
    row.set_verdict(VERDICT_PICK)
    assert row._verdict_pill.objectName() == "VerdictPickPill"


# --------------------------------------------------------------------------- #
# Drag-to-reorder (testable seam: _reorder_rule)
# --------------------------------------------------------------------------- #


def test_reorder_rule_moves_entry(qapp):
    """Tests interact with the data-level reorder seam; the actual mouse
    drag chain pulls through this method on release."""
    dlg = _cut_dialog(qapp)
    dlg._on_add_rule_clicked()
    dlg._on_add_rule_clicked()
    dlg._on_add_rule_clicked()
    # Mark the rules to verify identity after move.
    dlg._rule_rows[0].set_verdict(VERDICT_PICK)
    dlg._rule_rows[1].set_verdict(VERDICT_SKIP)
    dlg._rule_rows[2].set_verdict(VERDICT_PICK)
    # Move the first rule to position 2.
    dlg._reorder_rule(0, 2)
    verdicts = [r[1] for r in dlg._rules]
    assert verdicts == [VERDICT_SKIP, VERDICT_PICK, VERDICT_PICK]
    # The widget rows rebuilt; indices reflect the new order.
    assert [r._index_label.text() for r in dlg._rule_rows] == ["1.", "2.", "3."]


def test_reorder_rule_noop_when_indices_match(qapp):
    dlg = _cut_dialog(qapp)
    dlg._on_add_rule_clicked()
    before = list(dlg._rules)
    dlg._reorder_rule(0, 0)
    assert dlg._rules == before


def test_reorder_rule_ignores_out_of_range(qapp):
    dlg = _cut_dialog(qapp)
    dlg._on_add_rule_clicked()
    dlg._reorder_rule(0, 99)
    assert len(dlg._rules) == 1
    dlg._reorder_rule(-1, 0)
    assert len(dlg._rules) == 1


# --------------------------------------------------------------------------- #
# rules_expression — the resolver-expected shape
# --------------------------------------------------------------------------- #


def test_rules_expression_empty_when_no_rules(qapp):
    dlg = _cut_dialog(qapp)
    assert dlg.rules_expression() == []


def test_rules_expression_skips_empty_predicate(qapp):
    """The resolver drops empty predicates; the dialog drops them at
    emit time so the JSON stays compact."""
    dlg = _cut_dialog(qapp)
    dlg._on_add_rule_clicked()
    # No predicate; verdict defaults to skip.
    assert dlg.rules_expression() == []


def test_rules_expression_emits_resolver_shape(qapp):
    """spec/90 §10 worked example shape: predicate ``[["+", {"kind":
    "cut", "tag": "blurry"}]]`` with verdict ``"skip"``."""
    dlg = _cut_dialog(qapp)
    dlg._on_add_rule_clicked()
    dlg._rule_rows[0].append_operand(_pools()[1])  # #blurry
    dlg._rule_rows[0].set_verdict(VERDICT_SKIP)
    expr = dlg.rules_expression()
    assert expr == [{
        "predicate": [["+", {"kind": "cut", "tag": "blurry",
                             "id": "cut-blur"}]],
        "verdict": "skip",
    }]


def test_rules_expression_short_scenario_worked_example(qapp):
    """spec/90 §10 — two rules, blurry skip + best_wildlife/landscapes
    pick. The dialog's emit must round-trip through the resolver."""
    dlg = _cut_dialog(qapp)
    # Rule 1: skip blurry.
    dlg._on_add_rule_clicked()
    dlg._rule_rows[0].append_operand(_pools()[1])
    dlg._rule_rows[0].set_verdict(VERDICT_SKIP)
    # Rule 2: pick best_wildlife or best_landscapes.
    dlg._on_add_rule_clicked()
    dlg._rule_rows[1].append_operand(_pools()[2])
    dlg._rule_rows[1].append_operand(_pools()[3])
    dlg._rule_rows[1].set_verdict(VERDICT_PICK)

    expr = dlg.rules_expression()
    assert len(expr) == 2
    assert expr[0]["verdict"] == "skip"
    assert expr[1]["verdict"] == "pick"
    assert len(expr[1]["predicate"]) == 2
    assert expr[1]["predicate"][0][0] == "+"
    assert expr[1]["predicate"][1][0] == "+"


def test_rules_expression_reflects_reorder(qapp):
    """After :meth:`_reorder_rule`, the emit order matches the new
    rule order."""
    dlg = _cut_dialog(qapp)
    dlg._on_add_rule_clicked()
    dlg._rule_rows[0].append_operand(_pools()[1])  # #blurry → skip
    dlg._on_add_rule_clicked()
    dlg._rule_rows[1].append_operand(_pools()[2])  # #best_wildlife → skip
    dlg._reorder_rule(0, 1)
    expr = dlg.rules_expression()
    assert expr[0]["predicate"][0][1]["tag"] == "best_wildlife"
    assert expr[1]["predicate"][0][1]["tag"] == "blurry"


# --------------------------------------------------------------------------- #
# Rule-predicate picker — Faces appears only with hardware + people
# --------------------------------------------------------------------------- #


def _section_headers(picker: _OperandPickerPopover):
    out = []
    for i in range(picker._list_layout.count()):
        w = picker._list_layout.itemAt(i).widget()
        if w is None:
            continue
        if w.objectName() == "Micro":
            out.append((w.text() or "").lower())
    return out


def test_rule_predicate_picker_shows_base_dc_cut(qapp):
    picker = _OperandPickerPopover(
        _pools(), target=PICKER_TARGET_RULE_PREDICATE,
    )
    headers = _section_headers(picker)
    assert "base universes" in headers
    assert "cuts" in headers


def test_rule_predicate_picker_no_faces_when_hardware_off(qapp):
    picker = _OperandPickerPopover(
        _pools(), target=PICKER_TARGET_RULE_PREDICATE,
        people=_people(), show_faces=False,
    )
    headers = _section_headers(picker)
    assert "faces" not in headers


def test_rule_predicate_picker_no_faces_when_no_people(qapp):
    picker = _OperandPickerPopover(
        _pools(), target=PICKER_TARGET_RULE_PREDICATE,
        people=[], show_faces=True,
    )
    headers = _section_headers(picker)
    assert "faces" not in headers


def test_rule_predicate_picker_shows_faces_when_hardware_and_people(qapp):
    """spec/90 §4.3 — Person chips in rule predicates are the advanced
    affordance gated on both flags."""
    picker = _OperandPickerPopover(
        _pools(), target=PICKER_TARGET_RULE_PREDICATE,
        people=_people(), show_faces=True,
    )
    headers = _section_headers(picker)
    assert "faces" in headers
    # The Person rows are clickable.
    person_rows = [pool for pool, _btn in picker._rows
                   if pool.kind == "person"]
    assert {p.name for p in person_rows} == {"[Pedro]", "[Maria]"}


def test_dialog_opens_rule_predicate_picker_with_faces(qapp):
    """spec/94 Phase 4b (2026-06-21) decoupled ``show_faces`` from
    ``show_hardware``. Faces light up only when the caller explicitly
    opts in (a future spec/91 caller); the test demonstrates the
    flag by passing ``show_faces=True``."""
    dlg = _collection_dialog(qapp, show_faces=True)
    dlg._on_add_rule_clicked()
    row = dlg._rule_rows[0]
    from mira.ui.pages.new_cut_dialog import _OperandPickerPopover as P
    dlg._open_rule_predicate_picker(row, anchor=row)
    assert isinstance(dlg._picker_popover, P)
    headers = _section_headers(dlg._picker_popover)
    assert "faces" in headers


def test_dialog_collection_face_omits_faces_by_default(qapp):
    """spec/94 Phase 4b — the Collection face's EXIF/gear filters
    are wired (``show_hardware=True`` in production), but the face
    surface stays behind ``show_faces`` (default False) until spec/91
    lands. So the Collection-face popover doesn't list faces unless
    the caller opts in."""
    dlg = _collection_dialog(qapp)  # show_faces defaults to False
    dlg._on_add_rule_clicked()
    row = dlg._rule_rows[0]
    dlg._open_rule_predicate_picker(row, anchor=row)
    headers = _section_headers(dlg._picker_popover)
    assert "faces" not in headers


def test_dialog_omits_faces_for_cut_face(qapp):
    """Cut face hides hardware filters AND faces — both flags
    default False on the event-scope Cut face."""
    dlg = _cut_dialog(qapp)
    dlg._on_add_rule_clicked()
    row = dlg._rule_rows[0]
    dlg._open_rule_predicate_picker(row, anchor=row)
    headers = _section_headers(dlg._picker_popover)
    assert "faces" not in headers


# --------------------------------------------------------------------------- #
# composition() aggregates everything
# --------------------------------------------------------------------------- #


def test_composition_includes_rules_and_otherwise(qapp):
    dlg = _cut_dialog(qapp)
    dlg._on_add_rule_clicked()
    dlg._rule_rows[0].append_operand(_pools()[1])
    dlg._rule_rows[0].set_verdict(VERDICT_SKIP)
    comp = dlg.composition()
    assert "rules" in comp
    assert "otherwise" in comp
    assert len(comp["rules"]) == 1
    assert comp["otherwise"] == VERDICT_SKIP
