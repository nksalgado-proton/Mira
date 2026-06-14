"""FlowLayout — a wrapping horizontal layout (Nelson 2026-06-01, width-reflow pass).

A row of controls laid out with ``FlowLayout`` flows left-to-right and **wraps** to the
next line when the available width runs out, the way a toolbar of buttons should. Crucially
its ``minimumSize().width()`` is only the *widest single child* (everything else can wrap
below), so a dense toolbar no longer imposes a huge horizontal floor on its window — the
fix for the cull video surface freezing the whole window at ~2100 px wide (spec/05 §4c:
surfaces reflow down to the 1280×720 floor, never impose a hard min wider than that).

Adapted from the canonical Qt ``FlowLayout`` example. Stretch/spacer items are not
supported (wrapping makes them meaningless) — add only widgets.
"""
from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QLayout, QLayoutItem, QWidget

_DEFAULT_SPACING = 6


class FlowLayout(QLayout):
    def __init__(self, parent: Optional[QWidget] = None, *,
                 margin: int = 0, spacing: int = _DEFAULT_SPACING) -> None:
        super().__init__(parent)
        self._items: List[QLayoutItem] = []
        self._spacing = spacing
        self.setContentsMargins(QMargins(margin, margin, margin, margin))

    # ── QLayout plumbing ────────────────────────────────────────────────
    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> Optional[QLayoutItem]:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> Optional[QLayoutItem]:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    # ── the wrap ────────────────────────────────────────────────────────
    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_height = eff.x(), eff.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._spacing
            if next_x - self._spacing > eff.right() and line_height > 0:
                x = eff.x()
                y = y + line_height + self._spacing
                next_x = x + hint.width() + self._spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + m.bottom()
