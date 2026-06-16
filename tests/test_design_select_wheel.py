"""spec/75 §3.3 — the design-system ``select()`` factory ignores the
mouse wheel unless the user has explicitly engaged the combo.

Root cause this pins: Qt's ``WheelFocus`` + window-activation churn can
mark a combo focused on mere hover, so the app-wide wheel guard
(``mira.ui.base.wheel_guard``) lets the wheel through and the value
silently changes. The fix is the ``_user_engaged`` pattern carried into
the factory itself — the same pattern the Days Table's TZ and Country
pickers already use.
"""
from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QFocusEvent, QMouseEvent, QWheelEvent

from mira.ui.design.inputs import select


def _post_wheel(widget, *, delta: int = -120) -> None:
    """Synthesise a wheel event over ``widget``. ``delta=-120`` is one
    notch down (the value Windows posts for a single scroll click)."""
    event = QWheelEvent(
        QPointF(20.0, 20.0),                              # local pos
        QPointF(widget.mapToGlobal(QPoint(20, 20))),       # global pos
        QPoint(0, 0),                                      # pixelDelta
        QPoint(0, delta),                                  # angleDelta
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    widget.wheelEvent(event)


def _post_left_click(widget) -> None:
    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(10.0, 10.0),
        QPointF(widget.mapToGlobal(QPoint(10, 10))),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mousePressEvent(press)


def _post_focus_in(widget, reason: Qt.FocusReason) -> None:
    event = QFocusEvent(QFocusEvent.Type.FocusIn, reason)
    widget.focusInEvent(event)


def test_unfocused_combo_ignores_wheel(qapp):
    """A combo that the user has not engaged with should NOT change on
    a wheel notch — this is the bug-1 regression."""
    combo = select(["A", "B", "C", "D"])
    combo.setCurrentIndex(1)
    _post_wheel(combo)
    assert combo.currentIndex() == 1


def test_combo_engaged_by_left_click_accepts_wheel(qapp):
    """After a deliberate left-click the user IS engaged, so the wheel
    should change the value normally."""
    combo = select(["A", "B", "C", "D"])
    combo.setCurrentIndex(1)
    _post_left_click(combo)
    _post_wheel(combo)
    assert combo.currentIndex() != 1


def test_tab_focus_engages_combo(qapp):
    """Tab traversal is real user intent — the wheel should land after
    a Tab focus."""
    combo = select(["A", "B", "C", "D"])
    combo.setCurrentIndex(1)
    _post_focus_in(combo, Qt.FocusReason.TabFocusReason)
    _post_wheel(combo)
    assert combo.currentIndex() != 1


@pytest.mark.parametrize("reason", [
    Qt.FocusReason.MouseFocusReason,
    Qt.FocusReason.ActiveWindowFocusReason,
    Qt.FocusReason.OtherFocusReason,
])
def test_hover_or_window_focus_does_not_engage(qapp, reason):
    """Wheel-on-hover, ActiveWindowFocusReason, OtherFocusReason all
    fire on mere window activation / pointer churn; they must NOT
    engage the combo or the original bug returns."""
    combo = select(["A", "B", "C", "D"])
    combo.setCurrentIndex(1)
    _post_focus_in(combo, reason)
    _post_wheel(combo)
    assert combo.currentIndex() == 1


def test_focus_out_disengages_combo(qapp):
    """Once the user clicks away (non-popup focus-out), the engagement
    flag clears and subsequent wheels are ignored again."""
    combo = select(["A", "B", "C", "D"])
    combo.setCurrentIndex(1)
    _post_left_click(combo)
    out = QFocusEvent(
        QFocusEvent.Type.FocusOut, Qt.FocusReason.MouseFocusReason
    )
    combo.focusOutEvent(out)
    _post_wheel(combo)
    assert combo.currentIndex() == 1


def test_popup_focus_out_keeps_engagement(qapp):
    """When the user clicks the combo to open the popup, Qt fires
    focusOut with ``PopupFocusReason``. Treating that as a disengage
    would break wheel-inside-popup; the implementation must keep the
    flag set."""
    combo = select(["A", "B", "C", "D"])
    combo.setCurrentIndex(1)
    _post_left_click(combo)
    out = QFocusEvent(
        QFocusEvent.Type.FocusOut, Qt.FocusReason.PopupFocusReason
    )
    combo.focusOutEvent(out)
    # Engaged — wheel works.
    _post_wheel(combo)
    assert combo.currentIndex() != 1
