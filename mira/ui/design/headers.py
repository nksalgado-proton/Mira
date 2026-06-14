"""PageHeader + ThemeToggle.

PageHeader is the title strip at the top of every detail surface (Events
list, Phases, Days Grid, Picker, Editor, Cuts, Full Resolution). Title is a
PageTitle (30/800), sub line in ink_soft, optional right-side primary action.

ThemeToggle is the ☀️/🌙 pill that lives at the right edge of the title
bar. Emits ``themeChanged(str)`` so the host can call ``apply_theme`` and
rebuild visible surfaces.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class PageHeader(QWidget):
    """Page-level title row.

    Composition: title (PageTitle) + sub (Sub) stacked on the left; optional
    action button on the right. Layout: 22px gap top, 14px between title and
    sub, action right-aligned + centered vertically. The action stays
    flexible — pass any QPushButton (typically built via
    ``mira.ui.design.primary_button``).
    """

    def __init__(
        self,
        title: str,
        sub: str | None = None,
        action: QPushButton | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(6)
        t = QLabel(title)
        t.setObjectName("PageTitle")
        # QSS can't drive letter-spacing on QLabel; design-system asks for
        # -0.6px so we apply it via QFont here.
        f = QFont(t.font())
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -0.6)
        f.setPointSizeF(max(f.pointSizeF(), 18.0))
        t.setFont(f)
        text.addWidget(t)
        if sub:
            s = QLabel(sub)
            s.setObjectName("Sub")
            text.addWidget(s)
        outer.addLayout(text, 1)

        if action is not None:
            wrap = QVBoxLayout()
            wrap.setContentsMargins(0, 0, 0, 0)
            wrap.addStretch()
            wrap.addWidget(action)
            wrap.addStretch()
            outer.addLayout(wrap)


class ThemeToggle(QPushButton):
    """Sun/moon pill (#ThemeToggle). Click flips between ``light`` and
    ``dark`` and emits ``themeChanged(str)``. The host (typically the main
    window) is expected to call :func:`mira.ui.theme.apply_theme` in response.

    Default initial state reads ``QApplication.property("theme")``
    (set by ``apply_theme`` itself). Falls back to ``"dark"`` if the property
    is unset (cold boot before any theme applies).
    """

    themeChanged = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ThemeToggle")
        self.setCheckable(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        app = QApplication.instance()
        initial = (app.property("theme") if app else None) or "dark"
        self._mode = "dark" if initial == "dark" else "light"
        self._refresh_label()
        self.clicked.connect(self._toggle)

    def _refresh_label(self) -> None:
        self.setText("☼ Light" if self._mode == "dark" else "☾ Dark")
        self.setToolTip(
            "Switch to light theme" if self._mode == "dark"
            else "Switch to dark theme"
        )

    def _toggle(self) -> None:
        self._mode = "light" if self._mode == "dark" else "dark"
        self._refresh_label()
        self.themeChanged.emit(self._mode)
