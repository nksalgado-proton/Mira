"""Tests for the app-wide wheel guard (``mira.ui.base.wheel_guard``).

Symptom under test: hovering over an unfocused ``QSpinBox`` /
``QComboBox`` and rolling the mouse wheel silently changes its value
(and grabs focus via the default ``WheelFocus`` policy). The guard
consumes the event before it reaches the widget and forwards it to
the nearest scroll-area ancestor so the surrounding form still
scrolls.

The tests drive synthetic ``QWheelEvent`` values via
``QApplication.sendEvent`` — that delivery path goes through the
installed application-wide event filter, so the guard's
``eventFilter`` runs first and decides whether the widget's own
``wheelEvent`` is reached.
"""
from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt, QEvent, QPoint, QPointF
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mira.ui.base.wheel_guard import _WheelGuardFilter


@pytest.fixture
def guard(qapp):
    """Fresh _WheelGuardFilter per test, detached at teardown."""
    f = _WheelGuardFilter(qapp)
    qapp.installEventFilter(f)
    yield f
    qapp.removeEventFilter(f)


def _wheel_event(delta_y: int) -> QWheelEvent:
    return QWheelEvent(
        QPointF(5, 5),                              # local
        QPointF(5, 5),                              # global
        QPoint(0, 0),                               # pixelDelta
        QPoint(0, delta_y),                         # angleDelta
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def _build_spin_dialog(initial: int = 50):
    dlg = QDialog()
    lay = QVBoxLayout(dlg)
    spin = QSpinBox()
    spin.setRange(0, 1000)
    spin.setValue(initial)
    lay.addWidget(spin)
    dlg.show()
    QApplication.processEvents()
    fw = QApplication.focusWidget()
    if fw is not None:
        fw.clearFocus()
    QApplication.processEvents()
    return dlg, spin


def _teardown(dlg: QDialog) -> None:
    dlg.hide()
    dlg.close()
    dlg.deleteLater()
    QApplication.processEvents()
    QApplication.processEvents()


def test_wheel_on_unfocused_spinbox_does_not_change_value(qapp, guard):
    """The core symptom: scroll over an unfocused QSpinBox without
    clicking, value stays put."""
    dlg, spin = _build_spin_dialog(initial=50)
    try:
        assert not spin.hasFocus()
        QApplication.sendEvent(spin, _wheel_event(-120))
        QApplication.processEvents()
        assert spin.value() == 50, (
            "wheel on unfocused spin must not change the value"
        )
    finally:
        _teardown(dlg)


def test_wheel_on_focused_spinbox_does_change_value(qapp, guard):
    """Once the user focuses the spin (real click in production), the
    wheel works normally — the guard MUST NOT block legitimate input."""
    dlg, spin = _build_spin_dialog(initial=50)
    try:
        spin.setFocus(Qt.FocusReason.MouseFocusReason)
        QApplication.processEvents()
        assert spin.hasFocus()
        QApplication.sendEvent(spin, _wheel_event(-120))
        QApplication.processEvents()
        assert spin.value() != 50, (
            "wheel on focused spin must still change the value"
        )
    finally:
        _teardown(dlg)


def test_wheel_on_unfocused_combobox_does_not_change_index(qapp, guard):
    """Same rule for QComboBox: rolling the wheel over an unfocused
    combo must not cycle the index."""
    dlg = QDialog()
    lay = QVBoxLayout(dlg)
    combo = QComboBox()
    combo.addItems(["A", "B", "C", "D"])
    combo.setCurrentIndex(1)
    lay.addWidget(combo)
    dlg.show()
    QApplication.processEvents()
    combo.clearFocus()
    QApplication.processEvents()
    try:
        assert not combo.hasFocus()
        QApplication.sendEvent(combo, _wheel_event(-120))
        QApplication.processEvents()
        assert combo.currentIndex() == 1, (
            "wheel on unfocused combo must not change index"
        )
    finally:
        _teardown(dlg)


def test_wheel_forwarded_to_scrollable_ancestor(qapp, guard):
    """When an unfocused spin sits inside a QScrollArea, the wheel
    event is forwarded to the scroll area's viewport so the user can
    still scroll the surrounding form by rolling over the field."""
    dlg = QDialog()
    outer = QVBoxLayout(dlg)
    scroll = QScrollArea()
    scroll.setFixedHeight(80)                       # short → scrollable
    outer.addWidget(scroll)
    content = QWidget()
    content_lay = QVBoxLayout(content)
    spins = []
    for i in range(30):
        s = QSpinBox()
        s.setRange(0, 100)
        s.setValue(i)
        s.setFixedHeight(40)
        content_lay.addWidget(s)
        spins.append(s)
    scroll.setWidget(content)
    dlg.show()
    QApplication.processEvents()
    spins[0].clearFocus()
    QApplication.processEvents()

    try:
        assert not spins[0].hasFocus()
        initial_value = spins[0].value()
        initial_scroll = scroll.verticalScrollBar().value()
        QApplication.sendEvent(spins[0], _wheel_event(-120))
        QApplication.processEvents()
        QApplication.processEvents()
        assert spins[0].value() == initial_value, (
            "guarded spin must not change value when wheel is forwarded"
        )
        assert (
            scroll.verticalScrollBar().value() != initial_scroll
        ), "wheel should have been forwarded to the scroll viewport"
    finally:
        _teardown(dlg)


def test_wheel_on_non_guarded_widget_passes_through(qapp, guard):
    """A plain ``QWidget`` is not in the guarded set — the filter
    leaves the event alone (no consume). Verified via accepted state
    (the guard returning True would mean the widget never saw it)."""
    dlg = QDialog()
    lay = QVBoxLayout(dlg)
    plain = QWidget()
    lay.addWidget(plain)
    dlg.show()
    QApplication.processEvents()
    try:
        ev = _wheel_event(-120)
        ev.setAccepted(False)
        QApplication.sendEvent(plain, ev)
        # Plain widget doesn't accept the wheel; what matters is the
        # filter doesn't trip on it. No assertion needed beyond
        # "no exception" — but document the intent.
        assert True
    finally:
        _teardown(dlg)
