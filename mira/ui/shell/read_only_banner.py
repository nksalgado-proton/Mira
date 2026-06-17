"""The persistent read-only banner (spec/76 §A.4 + §B.1).

A thin strip directly below the menubar that names the editing
machine when the library is opened read-only. Visible only when
:func:`mira.session.is_read_only` returns True; otherwise hidden
and zero-height so the rest of the chrome doesn't jump on launch.

The banner is the user's standing reminder that decision verbs,
Edit writes, Export, and event-header / plan / day-management
saves are no-ops in this session — paired with the quieter
per-control tooltip hint added on the gated surfaces themselves.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

from mira.ui.i18n import tr


class ReadOnlyBanner(QWidget):
    """Sits below the menubar; shows the writer's hostname + acquire
    time. Hidden unless :func:`mira.session.is_read_only` is True at
    construction.

    A no-op when the app holds the writer lock — no widget paint, no
    layout cost. Styled via the ``ReadOnlyBanner`` QSS role (light +
    dark) so the visual treatment stays in QSS per spec/05.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("ReadOnlyBanner")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 3, 10, 3)
        row.setSpacing(8)
        self._label = QLabel("")
        self._label.setObjectName("ReadOnlyBannerLabel")
        self._label.setWordWrap(False)
        row.addWidget(self._label, stretch=1)
        self.setVisible(False)
        self._refresh_from_session()

    def _refresh_from_session(self) -> None:
        from mira.session import is_read_only, read_only_holder
        if not is_read_only():
            self.setVisible(False)
            return
        holder = read_only_holder()
        if holder is None:
            text = tr(
                "Read-only: another Mira holds the writer lock.")
        else:
            text = (tr(
                "Read-only — this library is open for editing on "
                "{host} (since {since}). Mutations are disabled in "
                "this window."
            ).replace("{host}", holder.hostname)
             .replace("{since}", holder.acquired_at))
        self._label.setText(text)
        self.setVisible(True)


__all__ = ["ReadOnlyBanner"]
