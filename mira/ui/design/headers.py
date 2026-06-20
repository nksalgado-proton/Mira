"""PageHeader + ThemeToggle + SurfaceIdentityHeader.

PageHeader is the title strip at the top of overview surfaces (Events list,
Phases, Days Grid, Cuts). Title is a PageTitle (30/800), sub line in
ink_soft, optional right-side primary action.

SurfaceIdentityHeader (spec/71) is the identity strip every decision
surface carries — Quick Sweep, Picker, Editor, Export. It pairs a
phase-identity rail + name badge with a purpose line and the surface's
§5a legend. The phase chrome (rail + badge) lives in the four phase
tokens (collect=blue / pick=accent / edit=amber / export=green); the
§5a state colours stay on the cell borders only (never repurposed). The
combination of name + colour + purpose answers "where am I" — the shared
Days List / Days Grid reads its host phase's colour automatically.

ThemeToggle is the sun/moon pill that lives at the right edge of the
title bar. Emits ``themeChanged(str)`` so the host can call
``apply_theme`` and rebuild visible surfaces.
"""
from __future__ import annotations

from typing import Optional, Sequence

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mira.ui.palette import PALETTE


class PageHeader(QWidget):
    """Page-level title row.

    Composition: title (PageTitle) + sub (Sub) stacked on the left; optional
    action button on the right. Layout: 22px gap top, 14px between title and
    sub, action right-aligned + centered vertically. The action stays
    flexible — pass any QPushButton (typically built via
    ``mira.ui.design.primary_button``).
    """

    def __init__(
        self,
        title: str,
        sub: str | None = None,
        action: QPushButton | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(6)
        t = QLabel(title)
        t.setObjectName("PageTitle")
        # QSS can't drive letter-spacing on QLabel; design-system asks for
        # -0.6px so we apply it via QFont here.
        f = QFont(t.font())
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -0.6)
        f.setPointSizeF(max(f.pointSizeF(), 18.0))
        t.setFont(f)
        text.addWidget(t)
        if sub:
            s = QLabel(sub)
            s.setObjectName("Sub")
            text.addWidget(s)
        outer.addLayout(text, 1)

        if action is not None:
            wrap = QVBoxLayout()
            wrap.setContentsMargins(0, 0, 0, 0)
            wrap.addStretch()
            wrap.addWidget(action)
            wrap.addStretch()
            outer.addLayout(wrap)


# ── SurfaceIdentityHeader (spec/71) ──────────────────────────────────────

# Same dict the events-card pipeline + closed-card stat tiles + 2x2 donuts
# read (spec/66 §1). Reusing these tokens means the surface chrome matches
# the donut the user just clicked.
_PHASE_COLOR_TOKEN = {
    "collect": "blue",
    "pick": "accent",
    "edit": "amber",
    "export": "green",
}


def _state_swatch(token: str, label: str) -> QWidget:
    """One §5a state-colour chip — a 3px-bordered square + a Sub label.

    Reads the FIXED dark-palette §5a values (green=picked, red=skipped,
    orange=compare, yellow=mixed) so the swatches stay visually identical
    across themes. Mirrors the helper in
    :mod:`mira.ui.pages.days_grid_page` /
    :mod:`mira.ui.exported.export_page` so every surface's legend speaks
    one grammar."""
    host = QWidget()
    h = QHBoxLayout(host)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(6)
    color = PALETTE["dark"][token]
    swatch = QLabel()
    swatch.setFixedSize(18, 14)
    swatch.setStyleSheet(  # pragma: no-qss — legend swatch border colour is data-driven
        f"background: transparent; border: 3px solid {color};"
        f" border-radius: 5px;"
    )
    h.addWidget(swatch)
    txt = QLabel(label)
    txt.setObjectName("Sub")
    h.addWidget(txt)
    return host


class SurfaceIdentityHeader(QWidget):
    """The identity strip every decision surface carries (spec/71).

    Composition (top -> bottom):

    1. A 3px **phase-coloured rail** in the phase's identity colour.
    2. A title row: **phase-name badge** (tinted pill in the phase
       colour) + **purpose line** + optional right-aligned action.
    3. Optional **legend row**: §5a state swatches + reminder italic.

    The phase identity colour stays on the rail + badge (chrome only).
    The legend swatches use the locked §5a state palette unchanged
    (green=picked, red=skipped, orange=compare, yellow=mixed) — never
    repurposed by phase.

    QSS roles (rules live in ``assets/themes/redesign.qss``):
    ``#SurfaceHeader``, ``#SurfaceHeaderRail`` (with ``phase`` property),
    ``#SurfaceHeaderBadge`` (with ``phase`` property),
    ``#SurfaceHeaderPurpose``, ``#SurfaceHeaderReminder``.
    """

    VALID_PHASES = frozenset(_PHASE_COLOR_TOKEN)

    def __init__(
        self,
        phase: str,
        name: str,
        purpose: str,
        *,
        legend: Optional[Sequence[tuple[str, str]]] = None,
        reminder: Optional[str] = None,
        action: Optional[QPushButton] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        if phase not in self.VALID_PHASES:
            raise ValueError(
                f"SurfaceIdentityHeader: invalid phase {phase!r}; "
                f"expected one of {sorted(self.VALID_PHASES)}"
            )
        self._phase = phase
        self.setObjectName("SurfaceHeader")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # 1. Phase-coloured rail (full-bleed, 3px).
        self._rail = QFrame()
        self._rail.setObjectName("SurfaceHeaderRail")
        self._rail.setProperty("phase", phase)
        self._rail.setFixedHeight(3)
        outer.addWidget(self._rail)

        # 2. Title row: badge + purpose + (optional) action.
        # Content margins stay at 0 — the host owns horizontal padding
        # via its own outer margins (or a thin wrapper for the zero-margin
        # viewport surfaces). Keeps the rail flush with the badge column.
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(14)

        self._badge = QLabel(name.upper())
        self._badge.setObjectName("SurfaceHeaderBadge")
        self._badge.setProperty("phase", phase)
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Letter-spacing reads as identity; QSS can't drive it on QLabel.
        f = QFont(self._badge.font())
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6)
        self._badge.setFont(f)
        title_row.addWidget(
            self._badge, 0, Qt.AlignmentFlag.AlignVCenter)

        self._purpose = QLabel(purpose)
        self._purpose.setObjectName("SurfaceHeaderPurpose")
        self._purpose.setWordWrap(True)
        title_row.addWidget(
            self._purpose, 1, Qt.AlignmentFlag.AlignVCenter)

        if action is not None:
            title_row.addWidget(
                action, 0, Qt.AlignmentFlag.AlignVCenter)

        outer.addLayout(title_row)

        # 3. Legend row (optional). Per spec/71 the Editor passes neither
        # legend nor reminder — the row drops out entirely.
        if legend or reminder:
            legend_row = QHBoxLayout()
            legend_row.setContentsMargins(0, 0, 0, 0)
            legend_row.setSpacing(16)
            for token, label in (legend or ()):
                legend_row.addWidget(_state_swatch(token, label))
            if reminder:
                rem = QLabel(reminder)
                rem.setObjectName("SurfaceHeaderReminder")
                rem.setWordWrap(True)
                legend_row.addWidget(rem)
            legend_row.addStretch(1)
            outer.addLayout(legend_row)

    def phase(self) -> str:
        """The phase identity token this header carries."""
        return self._phase


class ThemeToggle(QPushButton):
    """Sun/moon pill (#ThemeToggle). Click flips between ``light`` and
    ``dark`` and emits ``themeChanged(str)``. The host (typically the main
    window) is expected to call :func:`mira.ui.theme.apply_theme` in response.

    Default initial state reads ``QApplication.property("theme")``
    (set by ``apply_theme`` itself). Falls back to ``"dark"`` if the property
    is unset (cold boot before any theme applies).
    """

    themeChanged = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ThemeToggle")
        self.setCheckable(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        app = QApplication.instance()
        initial = (app.property("theme") if app else None) or "dark"
        self._mode = "dark" if initial == "dark" else "light"
        self._refresh_label()
        self.clicked.connect(self._toggle)

    def _refresh_label(self) -> None:
        self.setText("☼ Light" if self._mode == "dark" else "☾ Dark")
        self.setToolTip(
            "Switch to light theme" if self._mode == "dark"
            else "Switch to dark theme"
        )

    def _toggle(self) -> None:
        self._mode = "light" if self._mode == "dark" else "dark"
        self._refresh_label()
        self.themeChanged.emit(self._mode)
