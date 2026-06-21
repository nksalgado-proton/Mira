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
from mira.ui.design.buttons import ghost_button
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

        # Shared Back button — sits just left of the theme toggle so it's in
        # the same place on every surface (Nelson 2026). Hidden by default;
        # the host shows it only for pages that opt in (uses_titlebar_back) and
        # routes its click to the current page's back action.
        self.back_button = ghost_button("‹ Back")
        self.back_button.setVisible(False)
        h.addWidget(self.back_button)

        # Shared Help button — the ONE help entry point, on every surface, in
        # the same spot (Nelson 2026-06-21). The "?" glyph is the universal
        # help icon; the label carries its F1 shortcut. The host routes its
        # click (and F1) to the current surface's help, falling back to the
        # global shortcuts list.
        self.help_button = ghost_button("?  F1")
        self.help_button.setToolTip("Help & keyboard shortcuts  (F1)")
        h.addWidget(self.help_button)

        self.theme_toggle = ThemeToggle()
        h.addWidget(self.theme_toggle)
