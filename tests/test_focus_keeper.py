"""Tests for the app-wide focus guard (``mira.ui.base.focus_keeper``).

The guard's job is to stop focus from following the mouse: when a
tooltip / popup deactivates + reactivates the window and Qt restores
focus to whatever input widget is under the pointer, the guard
recognises the non-user :class:`Qt.FocusReason` (ActiveWindow / Popup /
Other / NoReason) and reverts focus to the last widget the user
*actually* focused (Mouse / Tab / Backtab / Shortcut).

These tests build a dialog with two ``QLineEdit``s in the same
top-level window and drive :class:`Qt.FocusReason` values through
``setFocus`` — the same pathway Qt would generate at runtime, so the
filter sees the same ``FocusIn`` events with the same reasons.
"""
from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLineEdit

from mira.ui.base.focus_keeper import _FocusKeeperFilter


@pytest.fixture
def keeper(qapp):
    """A fresh _FocusKeeperFilter per test, attached for the test only.

    Bypasses the module-level singleton so each test sees clean state
    even if some earlier test installed the production singleton via
    :func:`mira.ui.theme.apply_theme`."""
    f = _FocusKeeperFilter(qapp)
    qapp.installEventFilter(f)
    yield f
    qapp.removeEventFilter(f)


def _build_two_lineedit_dialog():
    """Visible QDialog containing two QLineEdits side-by-side. Visible
    so setFocus actually moves focus in the headless test runner. Qt
    auto-focuses the first QLineEdit on ``show()``; we ``clearFocus``
    so subsequent ``setFocus`` calls actually fire ``FocusIn`` events
    (the filter records on FocusIn — a no-op setFocus on an
    already-focused widget produces no event and the test would never
    populate the recorded state).

    ``activateWindow`` is explicit because a previous test's deferred
    teardown can leave the platform window-manager state pointing at a
    dying dialog; without an explicit activation, ``setFocus`` on the
    new dialog's child silently fails."""
    dlg = QDialog()
    lay = QHBoxLayout(dlg)
    a = QLineEdit()
    b = QLineEdit()
    lay.addWidget(a)
    lay.addWidget(b)
    dlg.show()
    dlg.activateWindow()
    QApplication.processEvents()
    fw = QApplication.focusWidget()
    if fw is not None:
        fw.clearFocus()
    QApplication.processEvents()
    return dlg, a, b


def _teardown_dialog(dlg):
    """Close + flush the dialog completely before the next test runs.
    ``deleteLater`` alone leaves the old dialog alive long enough that
    the next dialog's activation can land on the wrong window."""
    dlg.hide()
    dlg.close()
    dlg.deleteLater()
    QApplication.processEvents()
    QApplication.processEvents()


def _drain():
    """Run two passes — one to deliver the FocusIn, one for the 0-ms
    QTimer scheduled by the guard's revert path."""
    QApplication.processEvents()
    QApplication.processEvents()


@pytest.mark.parametrize("churn_reason", [
    Qt.FocusReason.ActiveWindowFocusReason,
    Qt.FocusReason.OtherFocusReason,
    Qt.FocusReason.PopupFocusReason,
    # ``NoFocusReason`` is intentionally omitted — Qt's setFocus with
    # that reason moves focus silently without firing a FocusIn the
    # event filter can see (verified on PyQt6), so the guard never has
    # a chance to react. The filter still treats it as a churn reason
    # for the rare case Qt does deliver it, but we can't synthesise
    # that path in a test.
])
def test_churn_focus_steal_is_reverted(qapp, keeper, churn_reason):
    """Click field A, then synthesise a churn-reason FocusIn on B —
    focus reverts to A on the next tick."""
    dlg, a, b = _build_two_lineedit_dialog()
    try:
        a.setFocus(Qt.FocusReason.MouseFocusReason)
        _drain()
        assert qapp.focusWidget() is a, (
            "precondition: user-click recorded A as the focused field"
        )

        b.setFocus(churn_reason)
        _drain()

        assert qapp.focusWidget() is a, (
            f"churn reason {churn_reason!r} should have been reverted "
            f"to the recorded field A"
        )
    finally:
        _teardown_dialog(dlg)


@pytest.mark.parametrize("legit_reason", [
    Qt.FocusReason.MouseFocusReason,
    Qt.FocusReason.TabFocusReason,
    Qt.FocusReason.BacktabFocusReason,
    Qt.FocusReason.ShortcutFocusReason,
])
def test_legitimate_focus_change_sticks(qapp, keeper, legit_reason):
    """Click field A, then deliver a legit-reason FocusIn on B — focus
    stays on B; the guard does not fight."""
    dlg, a, b = _build_two_lineedit_dialog()
    try:
        a.setFocus(Qt.FocusReason.MouseFocusReason)
        _drain()
        assert qapp.focusWidget() is a

        b.setFocus(legit_reason)
        _drain()

        assert qapp.focusWidget() is b, (
            f"legitimate reason {legit_reason!r} should have stuck"
        )
    finally:
        _teardown_dialog(dlg)


def test_programmatic_open_left_alone(qapp, keeper):
    """No recorded user-focus yet on this top-level → a churn-reason
    FocusIn is left alone (the dialog-open programmatic ``setFocus``
    case). Errs toward not fighting legitimate moves."""
    dlg, _a, b = _build_two_lineedit_dialog()
    try:
        # No prior MouseFocusReason on a: the dict is empty for this top-level.
        b.setFocus(Qt.FocusReason.OtherFocusReason)
        _drain()

        assert qapp.focusWidget() is b, (
            "with no recorded user-focus the guard must NOT revert — "
            "the dialog-open path needs setFocus to land"
        )
    finally:
        _teardown_dialog(dlg)


def test_revert_to_same_widget_is_noop(qapp, keeper):
    """A churn-reason FocusIn that lands on the *same* widget the user
    last focused is left alone (no spurious re-focus)."""
    dlg, a, _b = _build_two_lineedit_dialog()
    try:
        a.setFocus(Qt.FocusReason.MouseFocusReason)
        _drain()
        assert qapp.focusWidget() is a

        a.setFocus(Qt.FocusReason.OtherFocusReason)
        _drain()

        assert qapp.focusWidget() is a
    finally:
        _teardown_dialog(dlg)
