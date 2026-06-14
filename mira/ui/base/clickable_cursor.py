"""Application-level pointing-hand cursor for clickable widgets (spec/05).

PyQt6's QSS ``cursor`` property is not honoured reliably on Windows, so the affordance
is applied in code via a single app-wide event filter on the one-shot ``Polish`` event.
One registration at app startup covers every existing and future widget — including
those built dynamically inside dialogs. Adding a clickable type = one entry in
``CLICKABLE_TYPES``.

Ported verbatim from the legacy ``ui/base/clickable_cursor.py`` (charter §4 step 7 —
reuse the part, rewire nothing here: it has no data dependency).
"""
from __future__ import annotations

import logging
from typing import Tuple, Type

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QPushButton,
    QRadioButton,
    QSlider,
    QToolButton,
)

log = logging.getLogger(__name__)

# Widget types that always show a pointing-hand cursor on hover. QListWidget-style
# rows set a list-wide cursor themselves (the rail uses QPushButton rows, so it is
# covered here automatically).
CLICKABLE_TYPES: Tuple[Type[QObject], ...] = (
    QPushButton,
    QToolButton,
    QComboBox,
    QCheckBox,
    QRadioButton,
    QDateEdit,
    QSlider,
)


class _ClickableCursorFilter(QObject):
    """Applies the pointing-hand cursor on a clickable widget's Polish event."""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Polish and isinstance(obj, CLICKABLE_TYPES):
            try:
                obj.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            except Exception:  # noqa: BLE001
                log.debug("Could not set pointing-hand cursor on %s", type(obj).__name__)
        return False  # never consume the event


_FILTER_SINGLETON: "_ClickableCursorFilter | None" = None


def install_clickable_cursor_filter(app: QApplication) -> None:
    """Install the app-wide clickable-cursor filter. Idempotent."""
    global _FILTER_SINGLETON
    if _FILTER_SINGLETON is not None:
        return
    _FILTER_SINGLETON = _ClickableCursorFilter(app)
    app.installEventFilter(_FILTER_SINGLETON)
    log.debug("Installed clickable-cursor filter for: %s", [t.__name__ for t in CLICKABLE_TYPES])
