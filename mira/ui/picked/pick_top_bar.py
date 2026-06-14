"""The unified cull top line (spec/11 §6 step 4) — pure chrome.

Reassembled from the legacy ``ui/culler/pick_top_bar.py`` (charter §5.2).
Trimmed for the minimal cull loop: Back · bucket-type chip · Pick All ·
Skip All · Help. (The legacy Export button is omitted — Cull has no
user-visible export; silent-sync on phase exit handles the projection.
The legacy genre readout retired 2026-06-13 — photographic
classification only surfaces in the Edit phase.)

The PAGE owns the keyboard — every button is ``NoFocus``.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from mira.ui.base.surface import back_button, help_button
from mira.ui.i18n import tr


class PickTopBar(QWidget):
    back_requested = pyqtSignal()
    keep_all_requested = pyqtSignal()
    discard_all_requested = pyqtSignal()
    help_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PickTopBar")
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 4)
        row.setSpacing(8)

        self.back_button = back_button()
        self.back_button.setToolTip(tr("Back to the bucket list  (Esc)"))
        self.back_button.clicked.connect(self.back_requested.emit)

        self.type_label = QLabel("")
        self.type_label.setObjectName("PositionLabel")
        self.type_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.info_label = QLabel("")
        self.info_label.setObjectName("SelectBucketInfo")
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.keep_all_button = QPushButton(tr("Pick All"))
        self.keep_all_button.setToolTip(tr("Mark every item in this bucket Pick"))
        self.keep_all_button.clicked.connect(self.keep_all_requested.emit)
        self.skip_all_button = QPushButton(tr("Skip All"))
        self.skip_all_button.setToolTip(tr("Mark every item in this bucket Skip"))
        self.skip_all_button.clicked.connect(self.discard_all_requested.emit)

        # Plain HelpButton role (the earlier ReclassifyButton borrow
        # painted it as if it were a genre-edit control — Nelson
        # 2026-06-12 UI round catches the misrouted role).
        self.help_button = help_button()
        self.help_button.setToolTip(tr("Keyboard shortcuts  (F1)"))
        self.help_button.clicked.connect(self.help_requested.emit)

        row.addWidget(self.back_button)
        row.addWidget(self.type_label)
        row.addWidget(self.info_label, stretch=1)
        row.addWidget(self.keep_all_button)
        row.addWidget(self.skip_all_button)
        row.addWidget(self.help_button)

        for w in (
            self.back_button, self.keep_all_button,
            self.skip_all_button, self.help_button,
        ):
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            sp = w.sizePolicy()
            sp.setVerticalPolicy(QSizePolicy.Policy.Preferred)
            w.setSizePolicy(sp)

    def set_bucket_type(self, text: str) -> None:
        self.type_label.setText(text or "")
        self.type_label.setVisible(bool(text))

    def set_info(self, text: str) -> None:
        self.info_label.setText(text or "")
