"""A neutral placeholder for navigation destinations not yet reassembled.

Lets the shell + rail be wired and navigated end-to-end before every page exists — each
real surface replaces its placeholder as the reassembly reaches it.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from mira.ui.i18n import tr


class PlaceholderPage(QWidget):
    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        heading = QLabel(title)
        heading.setObjectName("PageHeading")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)

        note = QLabel(tr("This surface is being reassembled."))
        note.setObjectName("PageHint")
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(heading)
        layout.addWidget(note)
