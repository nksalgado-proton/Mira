"""Toolbar composer — a thin layout helper for the sticky toolbar above grid /
picker / editor surfaces (Back · day navigator · ✓ Pick all · ✗ Skip all ·
+ Start a new pass · review progress).

Not a widget class — most surfaces want full control over the toolbar contents
and a generic Toolbar(QWidget) class becomes a leaky abstraction. Instead this
returns a configured QHBoxLayout with the standard spacing + a right-stretch
strategy so action items group naturally.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QWidget


def toolbar_row(parent: QWidget | None = None) -> tuple[QFrame, QHBoxLayout]:
    """Return ``(container_frame, layout)`` for the standard sticky toolbar.

    Container is a #Card[level="2"]-styled frame so it sits on the page like a
    pill (spec/92 §2.3 — collapsed from the legacy #Card2 role); layout is an
    HBox with 10px spacing and 8px vertical padding. Caller addWidget()s in
    order: left-cluster controls, then ``layout.addStretch()``, then
    right-cluster controls.
    """
    frame = QFrame(parent)
    frame.setObjectName("Card")
    frame.setProperty("level", "2")
    h = QHBoxLayout(frame)
    h.setContentsMargins(10, 8, 10, 8)
    h.setSpacing(10)
    h.setAlignment(Qt.AlignmentFlag.AlignVCenter)
    return frame, h
