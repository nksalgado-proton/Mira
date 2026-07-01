"""spec/90 §5 — :class:`NewCutDialog` Save as DC wiring.

The picker's "Save as DC…" affordance opens a small naming sub-dialog and
calls the host's :attr:`NewCutDialog.dc_creator` callable with
``(name, expr, filters)``. The host returns the freshly-created DC as an
:class:`OperandOption` ready to drop into the operand inventory:

* Source-target saves carry the dialog's full Filters block — DCs are
  expression + filters and saving the source alone would lose the
  Style / Media / Camera / Lens narrowing the user has set.
* Rule-predicate saves carry an empty Filters block — predicates don't
  carry filters; the dialog's Filters apply at the pool level, not per-rule.
* Scope-target hides the affordance entirely — events don't compose into
  DCs (that's the Event Collection track).
* :class:`ValueError` from the dc_creator (typed-code from the gateway —
  ``'taken'`` / ``'reserved'`` / ``'empty'`` / ``'cycle'``) keeps the
  naming sub-dialog open with an inline error so the user retries
  without retyping.
"""
from __future__ import annotations

import pytest

# spec/162 Round 2a — the accordion body rebuild retires the
# ``Which items?`` band. The band-header Save as DC button (part of
# the Save/Load Collection surface) is gone from the dialog; Round 2b
# deletes the corresponding gateway method, this test file included.
# Every test here is skipped until then.
pytestmark = pytest.mark.skip(
    reason="spec/162 Round 2b — Save/Load Collection surface retires; "
           "this file is deleted with the retirement PR")

from mira.ui.pages.new_cut_dialog import (
    FLAVOUR_CUT,
    INVENTORY_EVENT,
    JOIN_OR,
    NewRecipeContext,
    NewCutDialog,
    OperandOption,
    PICKER_TARGET_RULE_PREDICATE,
    PICKER_TARGET_SCOPE,
    PICKER_TARGET_SOURCE,
    VERDICT_SKIP,
    _OperandPickerPopover,
    _SaveAsDcNameDialog,
)


def _pools():
    return [
        OperandOption(name="#exported", count=12, kind="base", tag="exported"),
        OperandOption(name="#rejects", count=4, kind="cut",
                      tag="rejects", id="cut-rej"),
    ]


def _ctx(**over) -> NewRecipeContext:
    kw = dict(
        event_name="Costa Rica 2026",
        available_pools=_pools(),
        available_styles=["macro", "wildlife"],
    )
    kw.update(over)
    return NewRecipeContext(**kw)


def _dialog(qapp, *, dc_creator=None, ctx=None, **over) -> NewCutDialog:
    kw = dict(
        flavour=FLAVOUR_CUT,
        show_scope=False,
        show_hardware=False,
        inventory_scope=INVENTORY_EVENT,
        ctx=ctx or _ctx(),
        dc_creator=dc_creator,
    )
    kw.update(over)
    return NewCutDialog(**kw)


# --------------------------------------------------------------------------- #
# Picker affordance — visibility by target
# --------------------------------------------------------------------------- #


def test_source_picker_hides_save_as_dc_button(qapp):
    """spec/90 §5.5 — Source-level Save as DC moved to the "Which items?"
    band header; the popover no longer carries its own entry so there's
    one canonical path."""
    picker = _OperandPickerPopover(
        _pools(), target=PICKER_TARGET_SOURCE,
    )
    assert picker._save_btn is None


def test_rule_predicate_picker_shows_save_as_dc_button(qapp):
    """Rule-predicate target keeps the popover entry — that's the only
    entry point for saving a predicate as a reusable DC (spec/90 §5.5)."""
    picker = _OperandPickerPopover(
        _pools(), target=PICKER_TARGET_RULE_PREDICATE,
    )
    assert picker._save_btn is not None


def test_scope_picker_hides_save_as_dc_button(qapp):
    """Scope target hides it — events don't compose into DCs."""
    picker = _OperandPickerPopover(
        target=PICKER_TARGET_SCOPE,
        events=[OperandOption(name="[Trip]", kind="event", uuid="evt-1")],
    )
    assert picker._save_btn is None


def test_band_header_save_as_dc_button_routes_through_same_code_path(
        qapp, monkeypatch):
    """spec/90 §5.5 — the band-header Save as DC button and the popover
    Save as DC button both end up in :meth:`_on_save_as_dc_clicked`; the
    band button sets the source context explicitly so the dc_creator
    receives the source expression + filters payload."""
    seen = {}

    def _dc_creator(name, expr, filters):
        seen["name"] = name
        seen["expr"] = expr
        seen["filters"] = filters
        return OperandOption(
            name=f"#{name}", count=1, kind="dc", tag=name, id=f"id-{name}")

    # Construct with dc_creator so the band button enables once Source
    # has chips; the click below is only a no-op on a disabled button.
    dlg = _dialog(qapp, dc_creator=_dc_creator)
    dlg._source_chips = [(JOIN_OR, dlg._ctx.available_pools[0])]
    dlg._refresh_source_row()
    assert dlg._save_dc_btn.isEnabled() is True

    from PyQt6.QtWidgets import QDialog, QMessageBox
    monkeypatch.setattr(QMessageBox, "exec", lambda self: None)
    monkeypatch.setattr(
        _SaveAsDcNameDialog, "exec",
        lambda self: (self._edit.setText("from_band")
                      or QDialog.DialogCode.Accepted))

    # Click the band-header Save as DC button (the canonical Source path).
    dlg._save_dc_btn.click()
    assert seen["name"] == "from_band"
    assert seen["expr"] == [["+", "exported"]]
    assert "styles" in seen["filters"]


# --------------------------------------------------------------------------- #
# No dc_creator wired → button stays visible-but-inert (Phase 4a behaviour)
# --------------------------------------------------------------------------- #


def test_save_as_dc_with_no_creator_still_emits_signal(qapp):
    """Smokes / unit tests can pass ``dc_creator=None`` to keep the
    placeholder behaviour. The signal still fires; the sub-dialog isn't
    opened."""
    dlg = _dialog(qapp)
    seen = []
    dlg.save_as_dc_requested.connect(lambda: seen.append(True))
    dlg._on_save_as_dc_clicked()
    assert seen == [True]


# --------------------------------------------------------------------------- #
# Naming sub-dialog
# --------------------------------------------------------------------------- #


def test_save_as_dc_name_dialog_gates_ok_on_text(qapp):
    dlg = _SaveAsDcNameDialog(default="")
    assert not dlg._ok.isEnabled()
    dlg._edit.setText("Clean exports")
    assert dlg._ok.isEnabled()
    assert dlg.dc_name() == "Clean exports"


def test_save_as_dc_name_dialog_previews_tag(qapp):
    dlg = _SaveAsDcNameDialog()
    dlg._edit.setText("Best Macros")
    assert "#best_macros" in dlg._preview.text()


def test_save_as_dc_name_dialog_show_error_inline(qapp):
    """Inline error stays visible until the user keeps typing."""
    dlg = _SaveAsDcNameDialog(default="taken_name")
    dlg.show_error("A DC named 'taken_name' already exists. Pick another.")
    assert not dlg._error.isHidden()
    assert "already exists" in dlg._error.text()
    # Typing clears the error so the user isn't told their in-progress
    # name is the conflict.
    dlg._edit.setText("taken_name_v2")
    assert dlg._error.isHidden()


# --------------------------------------------------------------------------- #
# Source-target Save as DC → dc_creator receives source expression + filters
# --------------------------------------------------------------------------- #


def test_save_as_dc_from_source_calls_creator_with_expr_and_filters(
        qapp, monkeypatch):
    """OK in the naming sub-dialog calls dc_creator(name, expr, filters)
    with the dialog's source expression + filters payload."""
    dlg = _dialog(qapp)
    # Compose a Source: #exported but not in #rejects (the spec/90 §10
    # worked example shape, trimmed to two chips).
    dlg._source_chips = [
        (JOIN_OR, dlg._ctx.available_pools[0]),     # #exported
    ]
    dlg._refresh_source_row()
    # Add a Style filter so the DC carries narrowing.
    dlg._style_chips["macro"].setChecked(True)

    seen = {}

    def _dc_creator(name, expr, filters):
        seen["name"] = name
        seen["expr"] = expr
        seen["filters"] = filters
        return OperandOption(name=f"#{name.lower().replace(' ', '_')}",
                             count=5, kind="dc",
                             tag=name.lower().replace(' ', '_'),
                             id=f"id-{name}")

    dlg._dc_creator = _dc_creator
    dlg._save_as_dc_context = ("source", None)

    # Drive the sub-dialog: stub its exec so it returns Accepted with
    # the chosen name in the line edit.
    from PyQt6.QtWidgets import QDialog

    def _fake_exec(self):
        self._edit.setText("clean_exports")
        return QDialog.DialogCode.Accepted

    # Stub the toast (QMessageBox) so it doesn't park on the desktop.
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "exec", lambda self: None)
    monkeypatch.setattr(_SaveAsDcNameDialog, "exec", _fake_exec)

    dlg._on_save_as_dc_clicked()

    assert seen["name"] == "clean_exports"
    assert seen["expr"] == [["+", "exported"]]
    assert seen["filters"]["styles"] == ["macro"]
    assert seen["filters"]["media_type"] == "both"


def test_save_as_dc_appends_returned_operand_to_inventory(qapp, monkeypatch):
    """The OperandOption the dc_creator returns lands in
    ``ctx.available_pools`` so the next picker open lists it."""
    dlg = _dialog(qapp)
    dlg._source_chips = [(JOIN_OR, dlg._ctx.available_pools[0])]
    dlg._refresh_source_row()

    new_op = OperandOption(
        name="#clean_exports", count=7, kind="dc",
        tag="clean_exports", id="dc-new")

    def _dc_creator(name, expr, filters):
        return new_op

    dlg._dc_creator = _dc_creator
    dlg._save_as_dc_context = ("source", None)

    from PyQt6.QtWidgets import QDialog, QMessageBox
    monkeypatch.setattr(QMessageBox, "exec", lambda self: None)
    monkeypatch.setattr(
        _SaveAsDcNameDialog, "exec",
        lambda self: (self._edit.setText("clean_exports")
                      or QDialog.DialogCode.Accepted))

    before_n = len(dlg._ctx.available_pools)
    dlg._on_save_as_dc_clicked()
    assert len(dlg._ctx.available_pools) == before_n + 1
    assert dlg._ctx.available_pools[-1] is new_op

    # The same inventory drives the next picker — constructing a picker
    # straight off the (now-updated) ctx lists the new DC. The dialog's
    # ``_open_source_picker`` reads the same list, so this proves the
    # next user-facing picker open will surface the new chip.
    picker = _OperandPickerPopover(dlg._ctx.available_pools)
    names = [p.name for p, _btn in picker._rows]
    assert "#clean_exports" in names


# --------------------------------------------------------------------------- #
# Rule-predicate Save as DC → empty filters block
# --------------------------------------------------------------------------- #


def test_save_as_dc_from_rule_predicate_uses_predicate_expr(qapp, monkeypatch):
    """A predicate-level Save as DC ships the rule's predicate as the
    expression and an empty filters dict (predicates don't compose with
    the dialog-level Filters row)."""
    dlg = _dialog(qapp)
    # Build a rule with one predicate operand: #rejects.
    dlg._on_add_rule_clicked()
    row = dlg._rule_rows[0]
    row.append_operand(dlg._ctx.available_pools[1])    # #rejects
    # The dialog's own Filters are set — they MUST NOT leak into the DC.
    dlg._style_chips["macro"].setChecked(True)

    seen = {}

    def _dc_creator(name, expr, filters):
        seen["name"] = name
        seen["expr"] = expr
        seen["filters"] = filters
        return OperandOption(
            name=f"#{name}", count=2, kind="dc", tag=name, id="dc-pred")

    dlg._dc_creator = _dc_creator
    dlg._save_as_dc_context = ("rule_predicate", row)

    from PyQt6.QtWidgets import QDialog, QMessageBox
    monkeypatch.setattr(QMessageBox, "exec", lambda self: None)
    monkeypatch.setattr(
        _SaveAsDcNameDialog, "exec",
        lambda self: (self._edit.setText("rejects_alias")
                      or QDialog.DialogCode.Accepted))

    dlg._on_save_as_dc_clicked()

    assert seen["name"] == "rejects_alias"
    assert seen["expr"] == [
        ["+", {"kind": "cut", "tag": "rejects", "id": "cut-rej"}]
    ]
    assert seen["filters"] == {}


# --------------------------------------------------------------------------- #
# Name conflict → sub-dialog stays open
# --------------------------------------------------------------------------- #


def test_save_as_dc_name_conflict_keeps_dialog_open(qapp, monkeypatch):
    """A typed ``ValueError('taken')`` from the dc_creator surfaces
    inline in the sub-dialog instead of closing it. The user retries
    without retyping the conflicting name."""
    dlg = _dialog(qapp)
    dlg._source_chips = [(JOIN_OR, dlg._ctx.available_pools[0])]
    dlg._refresh_source_row()

    calls = []

    def _dc_creator(name, expr, filters):
        calls.append(name)
        if len(calls) == 1:
            raise ValueError("taken")
        return OperandOption(
            name=f"#{name}", count=1, kind="dc", tag=name, id="dc-x")

    dlg._dc_creator = _dc_creator
    dlg._save_as_dc_context = ("source", None)

    names_to_try = iter(["taken_name", "fresh_name"])
    errors_seen = []

    from PyQt6.QtWidgets import QDialog, QMessageBox
    monkeypatch.setattr(QMessageBox, "exec", lambda self: None)

    def _fake_exec(self):
        try:
            name = next(names_to_try)
        except StopIteration:
            return QDialog.DialogCode.Rejected
        # Capture any error already on the dialog from a prior loop
        # iteration BEFORE typing wipes it — the second exec entry
        # follows show_error from the first iteration's conflict.
        if name == "fresh_name" and self._error.text():
            errors_seen.append(self._error.text())
        self._edit.setText(name)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(_SaveAsDcNameDialog, "exec", _fake_exec)
    dlg._on_save_as_dc_clicked()

    assert calls == ["taken_name", "fresh_name"]
    # The conflict surfaces as an inline message before the second exec
    # cleared it (typed text wipes prior errors so the user isn't told
    # their in-progress name is taken).
    assert errors_seen and "already exists" in errors_seen[0]


# --------------------------------------------------------------------------- #
# Skip on cancel of sub-dialog
# --------------------------------------------------------------------------- #


def test_save_as_dc_cancel_does_not_call_creator(qapp, monkeypatch):
    """Cancelling the naming sub-dialog skips the dc_creator entirely."""
    dlg = _dialog(qapp)
    dlg._source_chips = [(JOIN_OR, dlg._ctx.available_pools[0])]
    dlg._refresh_source_row()

    calls = []

    def _dc_creator(name, expr, filters):
        calls.append(name)
        return OperandOption(name=f"#{name}", kind="dc", tag=name)

    dlg._dc_creator = _dc_creator
    dlg._save_as_dc_context = ("source", None)

    from PyQt6.QtWidgets import QDialog
    monkeypatch.setattr(
        _SaveAsDcNameDialog, "exec",
        lambda self: QDialog.DialogCode.Rejected)

    dlg._on_save_as_dc_clicked()
    assert calls == []
