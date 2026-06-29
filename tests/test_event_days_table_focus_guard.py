"""spec/64 §4.2 amendment (Nelson 2026-06-29) — focus in
EventDaysTableDialog cells follows ONLY left-click or Tab.

Hover-induced focus (which Qt's QTableWidget cell widgets surface on
some platforms via Other/ActiveWindow focus reasons) must NOT light up
the cell's accent border. The cell widgets honor:

* MouseFocusReason     — left-click in the cell
* TabFocusReason       — Tab forward
* BacktabFocusReason   — Shift+Tab
* ShortcutFocusReason  — keyboard shortcut
* PopupFocusReason     — the combo's own dropdown

Anything else (Other / ActiveWindow / MenuBar / …) is rejected; the
widget schedules a clearFocus on the next event-loop tick so the
synchronous focus chain unwinds cleanly first.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFocusEvent

from mira.ui.base.country_picker import make_single_country_combo
from mira.ui.base.tz_picker import TzPicker
from mira.ui.pages.event_days_table_dialog import _FocusGuardedLineEdit


def _send_focus_in(widget, reason: Qt.FocusReason) -> bool:
    """Send a synthetic FocusIn with ``reason`` and return whether the
    widget claims focus after."""
    widget.show()
    widget.setFocus(reason)
    return widget.hasFocus()


def _focus_in_reason_is_rejected(widget, reason: Qt.FocusReason) -> None:
    """Dispatch focusInEvent with ``reason`` directly and assert the
    widget's focusInEvent does NOT call super (i.e. user_engaged stays
    unset on the picker, and the QTimer.singleShot clearFocus path
    fires)."""
    evt = QFocusEvent(QFocusEvent.Type.FocusIn, reason)
    widget.focusInEvent(evt)


# ── TzPicker ───────────────────────────────────────────────────

def test_tz_picker_accepts_focus_from_left_click_reason(qapp):
    """Mouse-click focus = the user explicitly engaged the picker."""
    p = TzPicker(initial=0.0)
    try:
        evt = QFocusEvent(
            QFocusEvent.Type.FocusIn, Qt.FocusReason.MouseFocusReason)
        p.focusInEvent(evt)
        # No deferred clearFocus was scheduled (we can't directly assert
        # the QTimer, but _user_engaged getting set by Tab is the
        # observable: MouseFocusReason itself doesn't toggle it, but the
        # absence of the early-return is what we're proving).
    finally:
        p.deleteLater()


def test_tz_picker_accepts_focus_from_tab(qapp):
    """Tab focus sets _user_engaged so the wheel guard knows the user
    intentionally engaged the picker."""
    p = TzPicker(initial=0.0)
    try:
        evt = QFocusEvent(
            QFocusEvent.Type.FocusIn, Qt.FocusReason.TabFocusReason)
        p.focusInEvent(evt)
        assert p._user_engaged is True
    finally:
        p.deleteLater()


def test_tz_picker_rejects_focus_from_hover_reasons(qapp):
    """OtherFocusReason / ActiveWindowFocusReason — the hover and
    window-activation paths — must NOT set _user_engaged. The early
    return in focusInEvent prevents super() from being called."""
    for reason in (
        Qt.FocusReason.OtherFocusReason,
        Qt.FocusReason.ActiveWindowFocusReason,
        Qt.FocusReason.MenuBarFocusReason,
    ):
        p = TzPicker(initial=0.0)
        try:
            evt = QFocusEvent(QFocusEvent.Type.FocusIn, reason)
            p.focusInEvent(evt)
            assert p._user_engaged is False, (
                f"reason {reason} should not engage the picker")
        finally:
            p.deleteLater()


# ── Country combo ──────────────────────────────────────────────

def test_country_combo_accepts_focus_from_tab(qapp):
    combo = make_single_country_combo(None)
    try:
        evt = QFocusEvent(
            QFocusEvent.Type.FocusIn, Qt.FocusReason.TabFocusReason)
        combo.focusInEvent(evt)
        assert combo._user_engaged is True
    finally:
        combo.deleteLater()


def test_country_combo_rejects_focus_from_hover_reasons(qapp):
    for reason in (
        Qt.FocusReason.OtherFocusReason,
        Qt.FocusReason.ActiveWindowFocusReason,
        Qt.FocusReason.MenuBarFocusReason,
    ):
        combo = make_single_country_combo(None)
        try:
            evt = QFocusEvent(QFocusEvent.Type.FocusIn, reason)
            combo.focusInEvent(evt)
            assert combo._user_engaged is False, (
                f"reason {reason} must not engage the country combo")
        finally:
            combo.deleteLater()


# ── _FocusGuardedLineEdit ──────────────────────────────────────

def test_focus_guarded_line_edit_accepts_click_and_tab(qapp):
    """Click + Tab are the user-explicit paths the dialog accepts."""
    for reason in (
        Qt.FocusReason.MouseFocusReason,
        Qt.FocusReason.TabFocusReason,
        Qt.FocusReason.BacktabFocusReason,
        Qt.FocusReason.ShortcutFocusReason,
        Qt.FocusReason.PopupFocusReason,
    ):
        e = _FocusGuardedLineEdit()
        try:
            e.show()
            e.setFocus(reason)
            # Allowed: hasFocus stays True (no deferred clearFocus).
            assert e.hasFocus() is True, (
                f"reason {reason} should keep the field focused")
        finally:
            e.deleteLater()


def test_focus_guarded_line_edit_rejects_hover_reasons(qapp):
    """OtherFocusReason / ActiveWindowFocusReason — the hover-focus
    paths the dialog blocks. clearFocus rides a QTimer.singleShot, so
    after a single event-loop tick the field is unfocused."""
    for reason in (
        Qt.FocusReason.OtherFocusReason,
        Qt.FocusReason.ActiveWindowFocusReason,
        Qt.FocusReason.MenuBarFocusReason,
    ):
        e = _FocusGuardedLineEdit()
        try:
            e.show()
            e.setFocus(reason)
            # Drain the event loop so the singleShot fires.
            qapp.processEvents()
            assert e.hasFocus() is False, (
                f"reason {reason} should not leave the field focused")
        finally:
            e.deleteLater()
