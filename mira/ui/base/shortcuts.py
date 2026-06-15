"""The shared keyboard-shortcuts dialog (Nelson 2026-06-12 UI round).

Every photo/video surface now opens a modal dialog with a uniform
two-column table — monospace ``KEY`` on the left, action on the
right. The QSS roles (``ShortcutsDialog`` / ``ShortcutKey`` /
``ShortcutAction``) match what the unified PickerPage video reveal uses; the
other surfaces had ad-hoc ``QMessageBox.information`` strings — those
move to this dialog at the same time.

Use ``show_shortcuts(parent, title, rows)``. Each row is a
``(key_text, action_text)`` tuple. Section dividers ride as a row
whose key is the empty string — the action label is treated as a
section heading.
"""
from __future__ import annotations

from typing import Iterable, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from mira.ui.i18n import tr

ShortcutRow = Tuple[str, str]


def show_shortcuts(parent: QWidget, title: str,
                   rows: Iterable[ShortcutRow]) -> None:
    """Open the modal shortcuts dialog. ``rows`` is a list of
    ``(key, action)`` pairs; a row with empty ``key`` is rendered as
    a section heading (used by surfaces with many bindings to keep
    the table readable)."""
    dlg = QDialog(parent)
    dlg.setObjectName("ShortcutsDialog")
    dlg.setWindowTitle(tr("Keyboard shortcuts"))
    v = QVBoxLayout(dlg)
    v.setContentsMargins(20, 18, 20, 16)
    v.setSpacing(12)

    heading = QLabel(title)
    heading.setObjectName("ShortcutsHeading")
    v.addWidget(heading)

    grid = QGridLayout()
    grid.setHorizontalSpacing(28)
    grid.setVerticalSpacing(6)
    grid.setColumnStretch(1, 1)
    mono = QFont("Consolas")
    mono.setStyleHint(QFont.StyleHint.Monospace)

    for r, (key, action) in enumerate(rows):
        if not key:
            # Section heading row — span both columns, no key label.
            sec = QLabel(action)
            sec.setObjectName("ShortcutSection")
            grid.addWidget(sec, r, 0, 1, 2)
            continue
        kl = QLabel(key)
        kl.setObjectName("ShortcutKey")
        kl.setFont(mono)
        kl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        al = QLabel(action)
        al.setObjectName("ShortcutAction")
        al.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        al.setWordWrap(True)
        grid.addWidget(kl, r, 0)
        grid.addWidget(al, r, 1)
    v.addLayout(grid)

    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    bb.rejected.connect(dlg.reject)
    bb.accepted.connect(dlg.accept)
    v.addWidget(bb)
    dlg.exec()


__all__ = ["ShortcutRow", "show_shortcuts"]
