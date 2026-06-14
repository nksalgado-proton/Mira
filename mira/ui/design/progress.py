"""StageProgress — the redesign's progress-bar widget with optional dark-mode glow.

Used for: Collect/Pick/Edit/Share pipeline stages on the events list, per-day
Picked/Skipped bars on Days Lists, the review-progress mini bar on the Days
Grid toolbar. Four states:

    None   accent (indigo) — default / generic in-progress
    done   green  — phase 100% (Collect → 1284/1284 captures decoded, etc.)
    prog   amber  — phase partial
    skip   red    — phase deferred / zero-state with explicit skip

The dark-mode glow ("+ soft glow in dark" per design-system §3 ProgressBar)
isn't expressible in QSS — we paint a small wider rounded rect with alpha
behind the chunk so the fill reads as soft-glowing. In light mode the glow
is skipped (the chrome is already high-contrast).

This widget paints from ``mira.ui.palette.PALETTE`` directly so the colors
follow theme toggles without any QSS resync.
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter
from PyQt6.QtWidgets import QApplication, QSizePolicy, QWidget

from mira.ui.palette import PALETTE

_BAR_HEIGHT = 11
_RADIUS = 5.5
_VALID_STATES = (None, "done", "prog", "skip")


class StageProgress(QWidget):
    """Painted progress bar — call ``setValue(int)`` and ``setState(str|None)``."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = 0
        self._state: str | None = None
        self._color_token: str | None = None
        self.setMinimumHeight(_BAR_HEIGHT + 4)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

    def setValue(self, v: int) -> None:
        self._value = max(0, min(100, int(v)))
        self.update()

    def value(self) -> int:
        return self._value

    def setState(self, state: str | None) -> None:
        if state not in _VALID_STATES:
            raise ValueError(
                f"unknown state {state!r}; expected one of {_VALID_STATES}"
            )
        self._state = state
        self.update()

    def state(self) -> str | None:
        return self._state

    def setColorToken(self, token: str | None) -> None:
        """Force the fill to a fixed PALETTE colour token (e.g. ``'accent'``,
        ``'amber'``, ``'green'``, ``'blue'``), resolved live per theme so it
        follows theme toggles. Overrides the state-based colour; pass ``None``
        to revert to state colouring. Used for phase-identity bars (spec/66 —
        bars encode phase, length encodes progress, not a done/in-progress
        state)."""
        self._color_token = token
        self.update()

    def paintEvent(self, _evt) -> None:  # noqa: N802 — Qt override
        app = QApplication.instance()
        mode = (app.property("theme") if app else None) or "dark"
        p = PALETTE[mode]
        track = QColor(p["track"])
        if self._color_token:
            chunk = QColor(p.get(self._color_token, p["accent"]))
        else:
            chunk = QColor(
                p["green"] if self._state == "done"
                else p["amber"] if self._state == "prog"
                else p["red"] if self._state == "skip"
                else p["accent"]
            )

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        y = (self.height() - _BAR_HEIGHT) / 2
        track_rect = QRectF(0, y, self.width(), _BAR_HEIGHT)
        painter.setBrush(track)
        painter.drawRoundedRect(track_rect, _RADIUS, _RADIUS)

        if self._value > 0:
            chunk_w = self.width() * (self._value / 100.0)
            chunk_rect = QRectF(0, y, chunk_w, _BAR_HEIGHT)

            # Dark-mode glow — soft halo under the chunk. Two passes of
            # decreasing alpha to fake a blur without QGraphicsBlurEffect.
            if mode == "dark":
                for spread, alpha in ((3, 40), (1.5, 80)):
                    glow = QColor(chunk)
                    glow.setAlpha(alpha)
                    painter.setBrush(glow)
                    painter.drawRoundedRect(
                        chunk_rect.adjusted(-spread, -spread, spread, spread),
                        _RADIUS + spread, _RADIUS + spread,
                    )

            # Horizontal gradient fill (base → slightly darker) to match the
            # mockup's `done`/`prog` gradient bars. Theme-safe: the darker stop
            # is derived from the chunk colour so it works in light + dark.
            grad = QLinearGradient(
                chunk_rect.left(), 0.0, chunk_rect.right(), 0.0
            )
            grad.setColorAt(0.0, chunk)
            grad.setColorAt(1.0, chunk.darker(118))
            painter.setBrush(grad)
            painter.drawRoundedRect(chunk_rect, _RADIUS, _RADIUS)

        painter.end()
