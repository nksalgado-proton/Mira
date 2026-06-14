"""Card surfaces — Card / Card2 / StatTile.

Every panel, list row, dialog section, stat tile in the redesign sits on a
card. QSS handles bg / border / radius via setObjectName("Card") /
setObjectName("Card2") / setObjectName("StatTile") (rules in
``assets/themes/redesign.qss``). The drop-shadow lives here because QSS has
no box-shadow equivalent in Qt — the design system specifies
``QGraphicsDropShadowEffect(blurRadius=30, yOffset=10, color=rgba(0,0,0,110|28))``
on each Card; alpha is theme-dependent so Card reads ``QApplication.property("theme")``.
"""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QLabel,
    QVBoxLayout,
)

from mira.ui.palette import PALETTE


class Card(QFrame):
    """Standard card surface (#Card).

    Auto-applies ``setObjectName("Card")`` + a theme-aware soft drop-shadow
    + (optional) padded inner VBox layout. Sub-classes / direct callers can
    skip the layout (``padded=False``) when they want full control of the
    inner geometry (e.g. grid-fill thumbnails).

    Call ``refresh_shadow()`` if the theme toggles at runtime — the shadow
    alpha differs between dark and light. (Surface-by-surface migration
    will eventually pipe this through a signal; for now Cards are typically
    rebuilt when their host page reopens.)
    """

    def __init__(self, parent=None, *, padded: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._padded = padded
        if padded:
            v = QVBoxLayout(self)
            v.setContentsMargins(18, 16, 18, 16)
            v.setSpacing(10)
        self.refresh_shadow()

    def refresh_shadow(self) -> None:
        eff = QGraphicsDropShadowEffect(self)
        eff.setBlurRadius(30)
        eff.setOffset(0, 10)
        app = QApplication.instance()
        mode = (app.property("theme") if app else None) or "dark"
        try:
            alpha = int(PALETTE[mode]["shadow_alpha"])
        except (KeyError, ValueError):
            alpha = 90
        eff.setColor(QColor(0, 0, 0, alpha))
        self.setGraphicsEffect(eff)


class Card2(QFrame):
    """Nested-surface card (#Card2) — lighter than Card, used for inputs,
    stat tiles, sub-sections inside a hero Card."""

    def __init__(self, parent=None, *, padded: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("Card2")
        if padded:
            v = QVBoxLayout(self)
            v.setContentsMargins(14, 12, 14, 12)
            v.setSpacing(8)


class StatTile(QFrame):
    """Mini stat card (#StatTile): micro uppercase label + big number in a
    semantic color, optional `· %` suffix.

    The value color is set inline (semantic — green/amber/red/blue/accent)
    because QSS can't drive per-instance color from a property without an
    explicit role rule per state. Build many of these in a row inside a
    parent Card.
    """

    def __init__(
        self,
        label: str,
        value: str,
        *,
        value_color: str | None = None,
        suffix: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("StatTile")
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(4)
        lab = QLabel(label.upper())
        lab.setObjectName("Micro")
        v.addWidget(lab)
        from PyQt6.QtWidgets import QHBoxLayout
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        big = QLabel(value)
        big.setObjectName("StatValue")
        if value_color:
            big.setStyleSheet(
                f"font-size: 24px; font-weight: 800; color: {value_color};"
            )
        else:
            big.setStyleSheet("font-size: 24px; font-weight: 800;")
        row.addWidget(big)
        if suffix:
            suf = QLabel(suffix)
            suf.setObjectName("Sub")
            suf.setAlignment(
                Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft
            )
            row.addWidget(suf)
        row.addStretch()
        v.addLayout(row)
        self.setMinimumSize(QSize(110, 70))
