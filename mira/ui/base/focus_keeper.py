"""Application-level focus-keeper: stop focus from following the mouse.

Symptom (cross-cutting): hovering or scrolling over an editable field
(``QLineEdit``, ``QComboBox``, the loc/description fields in the Event
Days Table dialog, ``QAbstractSpinBox``, …) steals focus with **no
click**, so pasted text lands in the wrong field.

Root cause — already triaged in
``mira/ui/base/plan_editor_dialog.py`` (``_PlanFocusKeeper`` +
comments): per-field tooltips' ``QTipLabel`` show/hide deactivates +
reactivates the dialog window; Qt then restores focus to whatever
input widget is under the pointer. The ``FocusIn`` carries a
:class:`Qt.FocusReason` of ``ActiveWindow`` / ``Popup`` / ``Other`` /
``NoReason`` — never one of the legitimate ones (``Mouse``, ``Tab``,
``Backtab``, ``Shortcut``).

Fix — **reason-targeted, not blind**:

1. Track the last input widget focused with a *legitimate* reason,
   per top-level window.
2. On a ``FocusIn`` with a *churn* reason that lands on a **different**
   input widget in the **same** top-level, revert focus (next
   event-loop tick) to the recorded widget.
3. Err toward **not** reverting when unsure — programmatic
   ``setFocus`` on dialog open (no recorded user-focus yet for this
   top-level), a recorded widget that belongs to a different window,
   or a target / recorded widget that has been torn down all bail
   silently. This guard is riskier than the wheel one; preferred
   behaviour is "stay out of the way" over "fight a legitimate move".

Scope: only input widgets (``QLineEdit``, ``QTextEdit``,
``QPlainTextEdit``, ``QComboBox``, ``QAbstractSpinBox``). Buttons,
lists, table cells, and the table host itself pass through unchanged.

Generalisation of ``mira.ui.base.plan_editor_dialog._PlanFocusKeeper``
(which was scoped to a single ``QTableWidget``) — lifted to one
app-wide event filter, installed at the same startup point as
``mira.ui.base.clickable_cursor.install_clickable_cursor_filter``
(see ``mira/ui/theme.py``).
"""
from __future__ import annotations

import logging
from typing import Dict, FrozenSet, Optional, Tuple, Type

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

log = logging.getLogger(__name__)


INPUT_TYPES: Tuple[Type[QObject], ...] = (
    QLineEdit,
    QTextEdit,
    QPlainTextEdit,
    QComboBox,
    QAbstractSpinBox,
)

# The only focus reasons that count as user intent. Anything else is
# treated as churn and reverted when it differs from the recorded
# widget.
_LEGIT: FrozenSet[Qt.FocusReason] = frozenset({
    Qt.FocusReason.MouseFocusReason,
    Qt.FocusReason.TabFocusReason,
    Qt.FocusReason.BacktabFocusReason,
    Qt.FocusReason.ShortcutFocusReason,
})


class _FocusKeeperFilter(QObject):
    """The shared app-wide focus guard (singleton, installed on
    ``QApplication``)."""

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        # Per-top-level: the input widget the user last focused with a
        # legitimate reason. Keyed on the top-level QWidget itself —
        # entries become stale when a dialog closes but reads are
        # defensive (try / except RuntimeError on every Qt call).
        self._user_focus: Dict[QWidget, QWidget] = {}
        # One-shot log so a real run can confirm the guard fired —
        # mirrors the diagnostic-and-fix pattern of the original
        # _PlanFocusKeeper.
        self._logged: bool = False

    @staticmethod
    def _is_input(w: object) -> bool:
        return isinstance(w, INPUT_TYPES)

    @staticmethod
    def _top_level(w: QWidget) -> Optional[QWidget]:
        try:
            return w.window()
        except RuntimeError:                                    # C++ gone
            return None

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:  # noqa: N802
        if ev.type() != QEvent.Type.FocusIn or not self._is_input(obj):
            return False
        target = obj                                            # freshly focused
        top = self._top_level(target)
        if top is None:
            return False
        reason = ev.reason()
        if reason in _LEGIT:
            # Real user intent — record and let it stand.
            self._user_focus[top] = target
            return False
        # Churn reason. Revert only if we have a recorded user-focus
        # in THIS top-level AND the target is a different input widget
        # AND the recorded widget is still alive in THIS top-level.
        recorded = self._user_focus.get(top)
        if recorded is None or recorded is target:
            return False
        try:
            recorded_top = recorded.window()
            recorded_alive = recorded.isVisible()
        except RuntimeError:
            # Recorded widget is gone — drop the entry and bail.
            self._user_focus.pop(top, None)
            return False
        if recorded_top is not top or not recorded_alive:
            return False
        if not self._logged:
            log.info(
                "focus-keeper: caught non-user focus steal "
                "(reason=%s) → reverting to last user-focused field",
                reason,
            )
            self._logged = True

        def _restore() -> None:
            try:
                # Re-check: a legitimate move scheduled meanwhile must
                # win (the user clicked another field while we waited).
                if (self._user_focus.get(top) is recorded
                        and recorded.isVisible()):
                    recorded.setFocus(Qt.FocusReason.OtherFocusReason)
            except RuntimeError:
                pass

        QTimer.singleShot(0, _restore)
        return False                                            # never consume


_FILTER_SINGLETON: "_FocusKeeperFilter | None" = None


def install_focus_keeper(app: QApplication) -> None:
    """Install the app-wide focus-keeper. Idempotent."""
    global _FILTER_SINGLETON
    if _FILTER_SINGLETON is not None:
        return
    _FILTER_SINGLETON = _FocusKeeperFilter(app)
    app.installEventFilter(_FILTER_SINGLETON)
    log.debug(
        "Installed focus-keeper for: %s",
        [t.__name__ for t in INPUT_TYPES],
    )
