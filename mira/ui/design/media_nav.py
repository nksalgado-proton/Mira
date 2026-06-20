"""MediaNav primitives — floating prev/next arrows + filmstrip.

The redesign's spec/63 photo-viewport mandate: every single-item viewing
surface (Picker / Editor / Full Resolution / Video Picker / Video Editor)
uses the SAME pair — floating ‹/› arrows overlaid on the stage + a thumbnail
filmstrip in the lower bar. No text Previous/Next buttons; consistency wins.

This module ships TWO primitives — not a turnkey "MediaNav container" —
because surfaces vary in how the stage is composed (Picker's blurred-fill
canvas vs. Video Editor's timeline stack) and forcing them into one shape
leaks complexity. The host composes:

    arrow_left = nav_arrow("left", parent=stage)
    arrow_left.move(20, stage.height() // 2 - 22)   # or in stage.resizeEvent
    arrow_left.clicked.connect(self._go_prev)

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


def nav_arrow(
    direction: str = "left", parent: QWidget | None = None
) -> QPushButton:
    """Circular floating ‹/› button (#MediaNavArrow). The host positions it
    via ``button.move(...)`` inside the stage's ``resizeEvent``.

    Pointing-hand cursor + Qt::WA_TranslucentBackground so the rounded edge
    paints over the photo without leaving a square hit-target shadow.
    """
    if direction not in ("left", "right"):
        raise ValueError(f"direction must be 'left' or 'right', got {direction!r}")
    btn = QPushButton("‹" if direction == "left" else "›", parent)
    btn.setObjectName("MediaNavArrow")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFixedSize(QSize(44, 44))
    btn.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
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
        # ObjectName drives the QSS state-border rule; Neutral when state is None
        role = {
            "picked": "StatePicked",
            "skipped": "StateSkipped",
            "compare": "StateCompare",
            "mixed": "StateMixed",
        }.get(state or "", "StateNeutral")
        self.setObjectName(role)
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
