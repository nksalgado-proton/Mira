"""Chip / Tag / PillToggle helpers.

QLabel-based chips for state pills (open/closed/done/in-progress/idle) and
tags (uppercase micro accents). QPushButton-based pill_toggle for selectable
options (Creative Focus, Participants in the Event Header dialog).

QSS rules in ``redesign.qss`` carry the look (#ChipOpen / #ChipClosed /
#ChipDone / #ChipProg / #ChipIdle / #Tag / #PillToggle); these helpers wire
the ObjectName + cursor and return ready-to-add widgets.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPushButton, QWidget


def _chip(role: str, text: str, parent: QWidget | None = None) -> QLabel:
    lab = QLabel(text, parent)
    lab.setObjectName(role)
    lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return lab


def chip_open(text: str = "Open", parent: QWidget | None = None) -> QLabel:
    """Green-tinted pill for open/active events."""
    return _chip("ChipOpen", text, parent)


def chip_closed(text: str = "Closed", parent: QWidget | None = None) -> QLabel:
    """Pink-tinted pill for closed events / finished sessions."""
    return _chip("ChipClosed", text, parent)


def chip_done(text: str = "Done", parent: QWidget | None = None) -> QLabel:
    """Green-tinted pill for completed phases / 100% stages."""
    return _chip("ChipDone", text, parent)


def chip_prog(text: str = "62%", parent: QWidget | None = None) -> QLabel:
    """Amber-tinted pill for in-progress phases / partial completion."""
    return _chip("ChipProg", text, parent)


def chip_idle(
    text: str = "Not started", parent: QWidget | None = None
) -> QLabel:
    """Neutral pill for not-started / 0%-state. Card2 fill, ink_faint text."""
    return _chip("ChipIdle", text, parent)


def tag(text: str, parent: QWidget | None = None) -> QLabel:
    """Uppercase micro accent pill — Trip / Session / Wildlife / etc.
    accent_soft bg, accent text."""
    lab = QLabel(text.upper(), parent)
    lab.setObjectName("Tag")
    return lab


def pill_toggle(
    text: str, parent: QWidget | None = None, *, checked: bool = False
) -> QPushButton:
    """Checkable pill (#PillToggle). Use rows of these for Creative Focus /
    Participants in the Event Header dialog. Checked state tints to accent."""
    b = QPushButton(text, parent)
    b.setObjectName("PillToggle")
    b.setCheckable(True)
    b.setChecked(checked)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b
