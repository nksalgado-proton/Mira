"""``QStackedWidget`` host for the top-level pages, keyed by navigation-rail entry key.

Ported from the legacy ``ui/shell/page_stack.py`` (generic, no data tendril). Pages are
added via ``add_page(key, widget)`` and shown via ``show_page(key)``; the keys are the
:mod:`mira.ui.shell.sidebar` entry keys so the wiring is symmetric.
"""
from __future__ import annotations

from typing import Dict, Optional

from PyQt6.QtWidgets import QStackedWidget, QWidget


class PageStack(QStackedWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._key_to_index: Dict[str, int] = {}

    def add_page(self, key: str, widget: QWidget) -> None:
        self._key_to_index[key] = self.addWidget(widget)

    def show_page(self, key: str) -> bool:
        """Switch to ``key``'s page; returns ``False`` if no page is registered for it
        (the host then treats the entry as an action, not a destination)."""
        idx = self._key_to_index.get(key)
        if idx is None:
            return False
        self.setCurrentIndex(idx)
        return True

    def page(self, key: str) -> Optional[QWidget]:
        idx = self._key_to_index.get(key)
        return self.widget(idx) if idx is not None else None

    @property
    def current_key(self) -> Optional[str]:
        idx = self.currentIndex()
        for key, registered in self._key_to_index.items():
            if registered == idx:
                return key
        return None
