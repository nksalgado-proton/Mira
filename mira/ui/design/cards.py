"""Card surfaces — Card / Card2 / StatTile.

Every panel, list row, dialog section, stat tile in the redesign sits on a
card. QSS handles bg / border / radius via:
  * Card  → setObjectName("Card")                    (default level-1 surface)
  * Card2 → setObjectName("Card") + level="2"        (#Card[level="2"], spec/92)
  * StatTile → setObjectName("Tile") + tone="stat"   (#Tile[tone], spec/92)
(rules in ``assets/themes/redesign.qss``). The drop-shadow lives here because
QSS has no box-shadow equivalent in Qt — the design system specifies
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
    """Nested-surface card (#Card[level="2"]) — lighter than Card, used for
    inputs, stat tiles, sub-sections inside a hero Card. spec/92 §2.3
    collapsed the legacy #Card2 role onto #Card + level="2"."""

    def __init__(self, parent=None, *, padded: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setProperty("level", "2")
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
        # spec/92 §2.3 → #Tile[tone="stat"] (collapsed from the legacy
        # #StatTile role). Same visual treatment, one base role.
        self.setObjectName("Tile")
        self.setProperty("tone", "stat")
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(4)
        lab = QLabel(label.upper())
        lab.setObjectName("Micro")
        v.addWidget(lab)
        # Single rich-text QLabel for value + suffix — matches the mockup's
        # `<div class="v">43 <small>· 57%</small></div>` inline pattern.
        # Two side-by-side QLabels in a QHBoxLayout (the prior shape)
        # produced uneven vertical baselines + visible background rect
        # artifacts at the tile widths these stats render at; HTML
        # baseline-aligns the small element automatically.
        app = QApplication.instance()
        mode = (app.property("theme") if app else None) or "dark"
        ink_soft = PALETTE[mode].get("ink_soft", "#8b94a7")
        big = QLabel()
        big.setObjectName("StatValue")
        big.setTextFormat(Qt.TextFormat.RichText)
        big.setStyleSheet(  # pragma: no-qss — value colour is data-driven
            "background: transparent; font-size: 24px; font-weight: 800;"
            f" color: {value_color};" if value_color else
            "background: transparent; font-size: 24px; font-weight: 800;"
        )
        if suffix:
            big.setText(
                f"{value} <span style='font-size:13px;font-weight:600;"
                f"color:{ink_soft};'>{suffix}</span>"
            )
        else:
            big.setText(value)
        v.addWidget(big)
        self.setMinimumSize(QSize(110, 70))
