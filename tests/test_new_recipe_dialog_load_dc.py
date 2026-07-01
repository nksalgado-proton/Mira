"""spec/90 §5 — :class:`NewCutDialog` Load DC wiring.

The Which items? band carries a *Load DC…* button next to *Save as DC…*.
Clicking it opens a small picker over the available DCs in the operand
inventory; selecting one resolves to ``(expr, filters)`` via the host's
``dc_loader`` callable and replaces the dialog's Source + Filters with
the loaded payload. Rules / Otherwise / Runtime / Scope / Name stay put
— Load DC is the items-layer mirror of Load Recipe (Recipe loads
*everything*; DC loads *just the items layer*).
"""
from __future__ import annotations

import pytest

from PyQt6.QtWidgets import QDialog, QMessageBox

from mira.ui.pages.new_cut_dialog import (
    FLAVOUR_CUT,
    INVENTORY_EVENT,
    JOIN_OR,
    NewRecipeContext,
    NewCutDialog,
    OperandOption,
    VERDICT_SKIP,
    _LoadDcDialog,
)


def _pools():
    return [
        OperandOption(name="#exported", count=12, kind="base", tag="exported"),
        OperandOption(name="#rejects", count=4, kind="cut",
                      tag="rejects", id="cut-rej"),
        OperandOption(name="#clean_exports", count=8, kind="dc",
                      tag="clean_exports", id="dc-clean"),
        OperandOption(name="#bests", count=10, kind="dc",
                      tag="bests", id="dc-bests"),
    ]


def _ctx(**over) -> NewRecipeContext:
    kw = dict(
        event_name="Costa Rica 2026",
        available_pools=_pools(),
        available_styles=["macro", "wildlife"],
    )
    kw.update(over)
    return NewRecipeContext(**kw)


def _dialog(qapp, *, dc_loader=None, ctx=None, **over) -> NewCutDialog:
    kw = dict(
        flavour=FLAVOUR_CUT,
        show_scope=False,
        show_hardware=False,
        inventory_scope=INVENTORY_EVENT,
        ctx=ctx or _ctx(),
        dc_loader=dc_loader,
    )
    kw.update(over)
    return NewCutDialog(**kw)


# --------------------------------------------------------------------------- #
# Button enablement
# --------------------------------------------------------------------------- #


def test_load_dc_button_enabled_when_loader_and_dcs_present(qapp):
    """The Load DC… button enables when both a dc_loader is wired AND
    the operand inventory carries at least one DC."""
    dlg = _dialog(qapp, dc_loader=lambda op: ([], {}))
    assert dlg._load_dc_btn.isEnabled() is True


def test_load_dc_button_disabled_without_loader(qapp):
    """No dc_loader wired (smokes / unit tests without persistence) →
    button stays disabled even when the inventory carries DCs."""
    dlg = _dialog(qapp, dc_loader=None)
    assert dlg._load_dc_btn.isEnabled() is False


def test_load_dc_button_disabled_when_inventory_has_no_dcs(qapp):
    """A library with no DCs can't load any; the button greys out
    even with a loader wired."""
    ctx = NewRecipeContext(
        event_name="Empty",
        available_pools=[OperandOption(
            name="#exported", count=4, kind="base", tag="exported")],
        available_styles=[],
    )
    dlg = _dialog(qapp, ctx=ctx, dc_loader=lambda op: ([], {}))
    assert dlg._load_dc_btn.isEnabled() is False


# --------------------------------------------------------------------------- #
# Picker dialog (_LoadDcDialog)
# --------------------------------------------------------------------------- #


def test_picker_lists_only_dc_operands(qapp):
    """The DC picker filters the operand inventory to ``kind == 'dc'`` —
    base universes + Cut operands stay out (they're not DCs)."""
    picker = _LoadDcDialog(_pools())
    labels = [picker._list.item(i).text()
              for i in range(picker._list.count())]
    assert any("#clean_exports" in l for l in labels)
    assert any("#bests" in l for l in labels)
    assert not any("#exported" in l for l in labels)
    assert not any("#rejects" in l for l in labels)


def test_picker_empty_renders_friendly_hint(qapp):
    """An empty DC list shows a friendly hint instead of a silent
    empty box."""
    from PyQt6.QtWidgets import QLabel
    picker = _LoadDcDialog([])
    labels = [lbl.text() for lbl in picker.findChildren(QLabel)]
    # Vocabulary rename to "Collection" landed in spec/94 Phase 1.
    assert any("No Collections" in l for l in labels)


def test_picker_emits_chosen_signal_on_accept(qapp):
    """Selecting a DC and clicking OK emits ``dc_chosen`` with the
    selected :class:`OperandOption`."""
    picker = _LoadDcDialog(_pools())
    seen = []
    picker.dc_chosen.connect(seen.append)
    picker._list.setCurrentRow(0)
    picker._on_accept()
    assert len(seen) == 1
    assert seen[0].kind == "dc"


# --------------------------------------------------------------------------- #
# Replace flow — confirm gate + apply to items layer
# --------------------------------------------------------------------------- #


def test_load_dc_with_empty_items_layer_skips_confirm(qapp, monkeypatch):
    """When Source + Filters are both empty (nothing to lose), Load DC
    skips the confirm and applies straight away."""
    loaded = []

    def _dc_loader(op):
        loaded.append(op)
        return ([["+", "exported"]], {"styles": ["macro"], "media_type": "both"})

    dlg = _dialog(qapp, dc_loader=_dc_loader)
    # Ensure the items layer starts clean.
    dlg._source_chips = []
    dlg._refresh_source_row()
    for chip in dlg._style_chips.values():
        chip.setChecked(False)

    confirms = []
    monkeypatch.setattr(
        NewCutDialog, "_confirm_replace_items",
        lambda self: confirms.append(True) or True)

    chosen_op = next(p for p in dlg._ctx.available_pools if p.kind == "dc")
    monkeypatch.setattr(
        _LoadDcDialog, "exec",
        lambda self: (self._list.setCurrentRow(0)
                      or self._on_accept()
                      or QDialog.DialogCode.Accepted))

    dlg._on_load_dc_clicked()

    # No confirm asked.
    assert confirms == []
    # dc_loader was called with the picked DC operand.
    assert len(loaded) == 1
    assert loaded[0].kind == "dc"
    # Source + Filters updated.
    assert dlg._source_chips
    assert dlg._style_chips["macro"].isChecked()


def test_load_dc_with_existing_state_asks_to_confirm(qapp, monkeypatch):
    """When Source has chips (or a Filter chip is toggled on), Load DC
    asks for confirmation before clobbering the existing items layer."""
    def _dc_loader(op):
        return ([["+", "exported"]],
                {"styles": ["wildlife"], "media_type": "both"})

    dlg = _dialog(qapp, dc_loader=_dc_loader)
    # Seed a non-empty source so the confirm path triggers.
    dlg._source_chips = [(JOIN_OR, dlg._ctx.available_pools[0])]
    dlg._refresh_source_row()

    confirms = []
    monkeypatch.setattr(
        NewCutDialog, "_confirm_replace_items",
        lambda self: confirms.append(True) or True)
    monkeypatch.setattr(
        _LoadDcDialog, "exec",
        lambda self: (self._list.setCurrentRow(0)
                      or self._on_accept()
                      or QDialog.DialogCode.Accepted))

    dlg._on_load_dc_clicked()
    assert confirms == [True]
    # Items layer replaced (wildlife now checked).
    assert dlg._style_chips["wildlife"].isChecked()


def test_load_dc_cancel_in_confirm_keeps_existing_state(qapp, monkeypatch):
    """When the user cancels the confirm prompt, the existing items
    layer stays put — no replacement happens."""
    def _dc_loader(op):
        return ([["+", "exported"]], {})

    dlg = _dialog(qapp, dc_loader=_dc_loader)
    a = dlg._ctx.available_pools[0]
    dlg._source_chips = [(JOIN_OR, a)]
    dlg._refresh_source_row()

    monkeypatch.setattr(
        NewCutDialog, "_confirm_replace_items", lambda self: False)
    monkeypatch.setattr(
        _LoadDcDialog, "exec",
        lambda self: (self._list.setCurrentRow(0)
                      or self._on_accept()
                      or QDialog.DialogCode.Accepted))

    dlg._on_load_dc_clicked()
    # Source untouched.
    assert dlg._source_chips == [(JOIN_OR, a)]


def test_load_dc_does_not_touch_rules_or_otherwise(qapp, monkeypatch):
    """Spec/90 §5 — Load DC replaces ONLY the items layer; Rules,
    Otherwise, and Runtime stay as they were."""
    def _dc_loader(op):
        return ([["+", "exported"]], {})

    dlg = _dialog(qapp, dc_loader=_dc_loader)
    # Seed a rule + flip Otherwise to pick + change the runtime.
    dlg._on_add_rule_clicked()
    dlg._rule_rows[0].append_operand(dlg._ctx.available_pools[1])  # #rejects
    dlg._otherwise = "pick"
    dlg._target_minutes = 30

    monkeypatch.setattr(
        NewCutDialog, "_confirm_replace_items", lambda self: True)
    monkeypatch.setattr(
        _LoadDcDialog, "exec",
        lambda self: (self._list.setCurrentRow(0)
                      or self._on_accept()
                      or QDialog.DialogCode.Accepted))

    dlg._on_load_dc_clicked()

    assert len(dlg._rules) == 1
    assert dlg._otherwise == "pick"
    assert dlg._target_minutes == 30


def test_load_dc_cancel_in_picker_does_nothing(qapp, monkeypatch):
    """Cancelling the DC picker (Reject) skips the loader entirely."""
    calls = []

    def _dc_loader(op):
        calls.append(op)
        return ([], {})

    dlg = _dialog(qapp, dc_loader=_dc_loader)
    monkeypatch.setattr(
        _LoadDcDialog, "exec",
        lambda self: QDialog.DialogCode.Rejected)
    dlg._on_load_dc_clicked()
    assert calls == []
