"""App-wide wheel guard for input widgets that change value on scroll.

Symptom: hovering the mouse over an unfocused ``QSpinBox`` /
``QDoubleSpinBox`` / ``QComboBox`` and rolling the wheel silently
changes its value — accidental edits when the user meant to scroll
the page. Same gesture also grabs focus (those widgets ship with
``Qt.FocusPolicy.WheelFocus`` by default).

Rule established 2026-06-14: focus on input widgets transfers ONLY
via left-click, Tab, Backtab, or Shortcut. Wheel-over-unfocused
input must change neither value nor focus.

Fix — app-wide event filter:
* On a ``QWheelEvent`` targeting an unfocused widget of a guarded
  type, **consume** the event (so the widget's wheelEvent never
  fires — no value change, no ``WheelFocus`` grab) and **forward**
  the event to the nearest ``QAbstractScrollArea`` ancestor so the
  surrounding form/list still scrolls.
* On a focused widget, the wheel works normally (the user
  explicitly focused it — changing the value is intentional).

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
from typing import Tuple, Type

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
)

log = logging.getLogger(__name__)


GUARDED_TYPES: Tuple[Type[QObject], ...] = (
    QComboBox,
    QAbstractSpinBox,
)


class _WheelGuardFilter(QObject):
    """The shared app-wide wheel guard (singleton, installed on
    ``QApplication``)."""

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:  # noqa: N802
        if ev.type() != QEvent.Type.Wheel:
            return False
        if not isinstance(obj, GUARDED_TYPES):
            return False
        try:
            if obj.hasFocus():
                return False                                    # honour the user
        except RuntimeError:                                    # C++ gone
            return False
        # Forward to the nearest scrollable ancestor so the
        # surrounding form / scroll area still scrolls instead of
        # being silently blocked.
        try:
            ancestor = obj.parentWidget()
        except RuntimeError:
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
        "Installed wheel guard for: %s",
        [t.__name__ for t in GUARDED_TYPES],
    )
