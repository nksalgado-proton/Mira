"""App-wide wheel guard for input widgets that change value on scroll.

Symptom: hovering the mouse over a ``QSpinBox`` / ``QDoubleSpinBox``
/ ``QComboBox`` and rolling the wheel silently changes its value —
accidental edits when the user meant to scroll the page. Same
gesture also grabs focus (those widgets ship with
``Qt.FocusPolicy.WheelFocus`` by default).

Rule established 2026-06-14, **tightened 2026-06-27**: focus on
input widgets transfers ONLY via left-click, Tab, Backtab, or
Shortcut. Wheel events over input widgets must **never** change
their value — regardless of focus. The wheel is for scrolling the
page; values are typed in, clicked on, or arrow-keyed. A user with
the keyboard caret already in a spinbox who then rolls the wheel
to scroll the page should see the page scroll, not the spinbox
silently increment ten times under the cursor.

Fix — app-wide event filter:
* On a ``QWheelEvent`` whose receiver is *inside the widget tree of*
  a guarded type, **always consume** the event (so the widget's
  wheelEvent never fires — no value change, no ``WheelFocus`` grab)
  and **forward** the event to the nearest ``QAbstractScrollArea``
  ancestor so the surrounding form/list still scrolls.
* The receiver may be the guarded widget itself OR the editable
  combo's internal ``QLineEdit`` (Qt delivers the wheel to whichever
  child is under the cursor; the inner-QLineEdit case is exactly the
  Days Table Country picker symptom that motivated the ancestor walk).

Pairs with :mod:`mira.ui.base.focus_keeper`: the focus-keeper guards
against tooltip-churn focus drift; this guard ensures wheel scrolling
neither grants focus nor mutates value. Installed at the same
startup point as :func:`install_clickable_cursor_filter` (see
``mira/ui/theme.py``).

Scope is ``QAbstractSpinBox`` (QSpinBox / QDoubleSpinBox / others)
and ``QComboBox`` — the two stock widgets whose wheel handler
mutates the value. ``QLineEdit`` doesn't react to wheel at all;
``QTextEdit`` / ``QPlainTextEdit`` scroll their own content (a
non-destructive op the user may genuinely want), so they're
intentionally NOT guarded.
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple, Type

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QWidget,
)

log = logging.getLogger(__name__)


GUARDED_TYPES: Tuple[Type[QObject], ...] = (
    QComboBox,
    QAbstractSpinBox,
)


class _WheelGuardFilter(QObject):
    """The shared app-wide wheel guard (singleton, installed on
    ``QApplication``)."""

    @staticmethod
    def _find_guarded(obj: QObject) -> Optional[QWidget]:
        """Return the nearest guarded widget at or above ``obj`` in the
        widget tree, or None. Catches the editable-``QComboBox`` case
        where the wheel event is delivered to the combo's internal
        ``QLineEdit`` (and Qt would propagate up to the combo's
        ``wheelEvent``, mutating the value)."""
        if not isinstance(obj, QWidget):
            return None
        cur: Optional[QWidget] = obj
        while cur is not None:
            if isinstance(cur, GUARDED_TYPES):
                return cur
            try:
                cur = cur.parentWidget()
            except RuntimeError:                                # C++ gone
                return None
        return None

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:  # noqa: N802
        if ev.type() != QEvent.Type.Wheel:
            return False
        guarded = self._find_guarded(obj)
        if guarded is None:
            return False
        # Wheel-over-input is ALWAYS consumed and forwarded to the
        # nearest scrollable ancestor — no focus exemption (the
        # 2026-06-27 tightening). The pre-tightening rule honoured
        # the wheel when the field had focus, but the user reported
        # that even a momentarily-focused field would silently grab
        # ten wheel ticks while the user just meant to scroll the
        # page. Values are typed, clicked, or arrow-keyed; the wheel
        # is exclusively for page scroll.
        try:
            ancestor = guarded.parentWidget()
        except RuntimeError:                                    # C++ gone
            return True
        while ancestor is not None:
            if isinstance(ancestor, QAbstractScrollArea):
                try:
                    QApplication.sendEvent(ancestor.viewport(), ev)
                except RuntimeError:
                    pass
                break
            try:
                ancestor = ancestor.parentWidget()
            except RuntimeError:
                break
        return True                                             # consume on target


_FILTER_SINGLETON: "_WheelGuardFilter | None" = None


def install_wheel_guard(app: QApplication) -> None:
    """Install the app-wide wheel guard. Idempotent."""
    global _FILTER_SINGLETON
    if _FILTER_SINGLETON is not None:
        return
    _FILTER_SINGLETON = _WheelGuardFilter(app)
    app.installEventFilter(_FILTER_SINGLETON)
    log.debug(
        "wheel_guard installed for: %s",
        [t.__name__ for t in GUARDED_TYPES],
    )
