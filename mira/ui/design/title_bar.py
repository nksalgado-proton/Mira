"""TitleBar — the Surface 01 top strip: Mira logo + menu + theme toggle.

surface-01-initial-app.html opens with one strip: the app logo at the left,
the menu labels in the middle, and the ThemeToggle pill at the right. The app
already has a fully-wired native ``QMenuBar``; rather than reimplement the menu
system, this widget *hosts* that existing menu bar between the logo and the
toggle, and the host installs it via ``QMainWindow.setMenuWidget`` — so the
menu keeps every action, shortcut, and surface-aware visibility rule it had.

The strip styling (card background, bottom hairline, transparent embedded
menu) lives in ``redesign.qss`` under ``#TitleBar`` / ``#TitleMenuBar``.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout, QMenuBar, QWidget

from mira.ui.design.brand import MiraLogo
from mira.ui.design.headers import ThemeToggle


class TitleBar(QWidget):
    """Logo (left) · embedded menu bar (middle) · ThemeToggle (right).

    Expose :attr:`theme_toggle` so the host can connect ``themeChanged`` to
    :func:`mira.ui.theme.apply_theme`.
    """

    def __init__(
        self, menu_bar: QMenuBar | None = None, *, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("TitleBar")
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 6, 14, 6)
        h.setSpacing(16)

        h.addWidget(MiraLogo(tile_size=24))

        if menu_bar is not None:
            menu_bar.setObjectName("TitleMenuBar")
            h.addWidget(menu_bar)

        h.addStretch(1)

        self.theme_toggle = ThemeToggle()
        h.addWidget(self.theme_toggle)
