"""Button factory helpers — primary / ghost / danger-ghost.

Three variants from the design system. Factory functions (not classes) because
a button is a stateless wrapper: the QSS rules in ``redesign.qss`` carry the
look; the function just applies the right ObjectName + pointing-hand cursor.

For a checkable selection chip (Creative Focus, Participants), use
``mira.ui.design.pill_toggle`` instead.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton, QWidget


def _make(text: str, role: str, parent: QWidget | None = None) -> QPushButton:
    b = QPushButton(text, parent)
    b.setObjectName(role)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def primary_button(text: str, parent: QWidget | None = None) -> QPushButton:
    """Accent-filled CTA. Use for the single main action per surface
    ('+ New Event', 'Search', 'Start a new pass…', 'Export'). Avoid more
    than one primary on screen — that's the design-system rule."""
    return _make(text, "Primary", parent)


def ghost_button(text: str, parent: QWidget | None = None) -> QPushButton:
    """Transparent button with a line border — Back, secondary actions,
    toolbar items."""
    return _make(text, "Ghost", parent)


def danger_ghost_button(
    text: str, parent: QWidget | None = None
) -> QPushButton:
    """Ghost variant that turns red on hover — Skip all, Delete, destructive
    secondaries. The destructive intent is in the hover, not the resting state,
    so the surface stays calm."""
    return _make(text, "DangerGhost", parent)
