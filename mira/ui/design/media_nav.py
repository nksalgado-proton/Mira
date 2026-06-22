"""MediaNav primitives — inline prev/next ghost buttons + filmstrip.

Spec/63's photo-viewport mandate (Nelson 2026-06-22 revision): every
single-item viewing surface (Picker / Editor / Quick Sweep / Full
Resolution / Video Picker / Video Editor) uses the SAME pair — inline
ghost-styled **"‹ Prev"** / **"Next ›"** buttons sitting in the bottom
control row + a thumbnail filmstrip. The original spec called for
floating circular arrows; that broke down because ``#MediaNavArrow`` had
no QSS rule, so the Quick Sweep viewer rendered raw native OS buttons
next to the ghost-styled ones. Inline labelled ghost buttons keep
consistency without depending on a custom QSS rule for the chrome.

This module ships TWO primitives — not a turnkey "MediaNav container" —
because surfaces vary in how the stage is composed (Picker's blurred-fill
canvas vs. Video Editor's timeline stack) and forcing them into one shape
leaks complexity. The host composes:

    btn_prev = nav_button("left")
    btn_prev.clicked.connect(self._go_prev)
    row.addWidget(btn_prev)

    filmstrip = Filmstrip()
    filmstrip.setItems(neighbour_thumbs)
    bottom_bar.addWidget(filmstrip)
    filmstrip.thumbClicked.connect(self._jump_to)
"""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QWidget,
)

from mira.ui.design.buttons import ghost_button


def nav_button(
    direction: str = "left", parent: QWidget | None = None
) -> QPushButton:
    """Inline ghost-styled **"‹ Prev"** / **"Next ›"** button (spec/63
    MediaNav). The host places it in the bottom control row alongside
    the other ghost buttons; the chevron sits on the natural-language
    side of the label so left/right reads at a glance.

    Returns a :func:`ghost_button` (#Ghost role + redesign.qss styling
    + pointing-hand cursor + visible hover/pressed/disabled states).
    """
    if direction not in ("left", "right"):
        raise ValueError(
            f"direction must be 'left' or 'right', got {direction!r}")
    label = "‹ Prev" if direction == "left" else "Next ›"
    btn = ghost_button(label, parent)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


class _FilmstripThumb(QFrame):
    """Single tile in the filmstrip. Holds a pixmap + state border color
    derived from the design-system palette (picked / skipped / compare /
    neutral). Clicking emits the parent Filmstrip's thumbClicked(index).
    """

    def __init__(
        self,
        pixmap: QPixmap | None,
        state: str | None = None,
        *,
        size: QSize = QSize(86, 64),
        current: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFixedSize(size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # ObjectName + `state` property drive the QSS #StateBorder[state] rule
        # (spec/92 §2.3). Property values mirror §5a; unknown / None → "neutral".
        state_value = state if state in ("picked", "skipped", "compare", "mixed") else "neutral"
        self.setObjectName("StateBorder")
        self.setProperty("state", state_value)
        # Dim non-current items (design-system §3 MediaNav filmstrip rule)
        if not current:
            self.setStyleSheet(  # pragma: no-qss — runtime hover opacity toggle
                self.styleSheet() + " QFrame { opacity: 0.62; }"
            )
        v = QHBoxLayout(self)
        v.setContentsMargins(2, 2, 2, 2)
        v.setSpacing(0)
        if pixmap is not None and not pixmap.isNull():
            inner = QLabel()
            inner.setAlignment(Qt.AlignmentFlag.AlignCenter)
            inner.setPixmap(
                pixmap.scaled(
                    size - QSize(6, 6),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            v.addWidget(inner)


class Filmstrip(QScrollArea):
    """Horizontally scrolling neighbor-thumbnail bar.

    Wires onto Picker / Editor / etc. via:

        strip = Filmstrip()
        strip.setItems(
            [(pixmap, "picked"), (pixmap, "skipped"), (pixmap, None), ...],
            current_index=2,
        )
        strip.thumbClicked.connect(self._jump_to_index)
    """

    thumbClicked = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedHeight(78)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._inner = QWidget(self)
        self._row = QHBoxLayout(self._inner)
        self._row.setContentsMargins(8, 6, 8, 6)
        self._row.setSpacing(8)
        self.setWidget(self._inner)
        self._thumbs: list[_FilmstripThumb] = []

    def setItems(
        self,
        items: list[tuple[QPixmap | None, str | None]],
        *,
        current_index: int = 0,
    ) -> None:
        # Clear existing
        while self._row.count():
            it = self._row.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        self._thumbs.clear()
        for idx, (pm, state) in enumerate(items):
            t = _FilmstripThumb(pm, state, current=(idx == current_index))
            t.mousePressEvent = (
                lambda _evt, i=idx: self.thumbClicked.emit(i)
            )
            self._row.addWidget(t)
            self._thumbs.append(t)
        self._row.addStretch()
