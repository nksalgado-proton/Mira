"""Smoke tests for :class:`mira.ui.pages.landing_level_dialog.LandingLevelDialog`.

spec/57 §4.3 slice 5a — the backfill wizard's landing-level question.
Covers: the three options render with hints, the default is "collected",
levels outside ``AVAILABLE_LEVELS`` render disabled (slices 5b/5c flip
them on), ``level()`` reflects the checked radio, and the OK / Cancel
transitions.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QDialogButtonBox

from mira.ui.pages.landing_level_dialog import (
    AVAILABLE_LEVELS,
    LEVEL_COLLECTED,
    LEVEL_EDITED,
    LEVEL_PICKED,
    LandingLevelDialog,
)


def test_three_levels_render(qapp):
    dlg = LandingLevelDialog()
    assert set(dlg._radio_by_level) == {
        LEVEL_COLLECTED, LEVEL_PICKED, LEVEL_EDITED,
    }


def test_default_level_is_collected(qapp):
    dlg = LandingLevelDialog()
    assert dlg.level() == LEVEL_COLLECTED


def test_enabled_states_match_available_levels(qapp):
    dlg = LandingLevelDialog()
    for level, radio in dlg._radio_by_level.items():
        assert radio.isEnabled() == (level in AVAILABLE_LEVELS), level


def test_all_three_levels_live():
    """Slice 5 complete: every landing level serves."""
    assert AVAILABLE_LEVELS == {LEVEL_COLLECTED, LEVEL_PICKED, LEVEL_EDITED}


def test_level_reflects_checked_radio(qapp):
    dlg = LandingLevelDialog()
    dlg._radio_by_level[LEVEL_PICKED].setChecked(True)
    assert dlg.level() == LEVEL_PICKED


def test_every_control_carries_a_hint(qapp):
    """spec/05 — every interactive widget gets a tooltip via tr()."""
    dlg = LandingLevelDialog()
    for level, radio in dlg._radio_by_level.items():
        assert radio.toolTip(), level
    box = dlg.findChild(QDialogButtonBox)
    assert box is not None
    for std in (QDialogButtonBox.StandardButton.Ok,
                QDialogButtonBox.StandardButton.Cancel):
        btn = box.button(std)
        assert btn is not None and btn.toolTip()


def test_ok_accepts(qapp):
    dlg = LandingLevelDialog()
    box = dlg.findChild(QDialogButtonBox)
    box.button(QDialogButtonBox.StandardButton.Ok).click()
    assert dlg.result() == QDialog.DialogCode.Accepted


def test_cancel_rejects(qapp):
    dlg = LandingLevelDialog()
    box = dlg.findChild(QDialogButtonBox)
    box.button(QDialogButtonBox.StandardButton.Cancel).click()
    assert dlg.result() == QDialog.DialogCode.Rejected
