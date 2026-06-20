"""Chip / Tag / PillToggle helpers.

QLabel-based chips for state pills (open/closed/done/in-progress/idle) and
tags (uppercase micro accents). QPushButton-based pill_toggle for selectable
options (Creative Focus, Participants in the Event Header dialog).

QSS rules in ``redesign.qss`` carry the look (#Chip[tone=…] / #Tag /
#PillToggle); these helpers wire the ObjectName + tone property + cursor
and return ready-to-add widgets. Spec/92 §2.5 collapsed the five sibling
#Chip* roles onto one #Chip + a `tone` property.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPushButton, QWidget


def _chip(tone: str, text: str, parent: QWidget | None = None) -> QLabel:
    lab = QLabel(text, parent)
    lab.setObjectName("Chip")
    lab.setProperty("tone", tone)
    lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return lab


def chip_open(text: str = "Open", parent: QWidget | None = None) -> QLabel:
    """Green-tinted pill for open/active events."""
    return _chip("open", text, parent)


def chip_closed(text: str = "Closed", parent: QWidget | None = None) -> QLabel:
    """Pink-tinted pill for closed events / finished sessions."""
    return _chip("closed", text, parent)


def chip_done(text: str = "Done", parent: QWidget | None = None) -> QLabel:
    """Green-tinted pill for completed phases / 100% stages."""
    return _chip("done", text, parent)


def chip_prog(text: str = "62%", parent: QWidget | None = None) -> QLabel:
    """Amber-tinted pill for in-progress phases / partial completion."""
    return _chip("prog", text, parent)


def chip_idle(
    text: str = "Not started", parent: QWidget | None = None
) -> QLabel:
    """Neutral pill for not-started / 0%-state. Card2 fill, ink_faint text."""
    return _chip("idle", text, parent)


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
