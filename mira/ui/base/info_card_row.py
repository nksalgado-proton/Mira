"""InfoCardRow — generic list-row container (title banner + two-column body).

Ported into ``mira/ui/`` from the legacy ``ui/base/info_card_row.py`` (charter §5.2 —
the legacy UI is *reassembled*, copied in + rewired, never imported across). This widget
has no data tendril (PyQt + logging only), so the port is verbatim; the Cull navigator
(`mira/ui/culler/bucket_navigator.py`) is the first consumer here.

* **Title banner (full width, top).** The row's identifier, with an optional right-aligned
  action slot.
* **Left column (flexible).** Hosts any caller-supplied widget that expands horizontally +
  fixes height to content (the reference impl is :class:`mira.ui.base.status_breakdown.
  StatusBreakdown`).
* **Right column (fixed).** Plain-text supporting metadata lines, right-aligned.
* **Whole-card click** emits :attr:`clicked` (pointing-hand cursor + QSS hover signal it).
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger(__name__)

_DEFAULT_METADATA_WIDTH = 140
_TITLE_OBJECT_NAME = "InfoCardRowTitle"
_METADATA_OBJECT_NAME = "InfoCardRowMetadata"

VARIANT_DEFAULT = "default"
VARIANT_CAMERA = "camera"
VARIANT_DAY = "day"
VARIANT_BUCKET = "bucket"
_VALID_VARIANTS = (VARIANT_DEFAULT, VARIANT_CAMERA, VARIANT_DAY, VARIANT_BUCKET)


class InfoCardRow(QFrame):
    """Generic list-row container — title banner + two-column body."""

    clicked = pyqtSignal()

    def __init__(
        self,
        title: str = "",
        content_widget: Optional[QWidget] = None,
        metadata_lines: Optional[list[str]] = None,
        *,
        title_actions: Optional[Iterable[QWidget]] = None,
        right_column_width: int = _DEFAULT_METADATA_WIDTH,
        variant: str = VARIANT_DEFAULT,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("InfoCardRow")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._right_column_width = int(right_column_width)
        self._content_widget: Optional[QWidget] = None
        self._action_widgets: list[QWidget] = []
        self.setProperty(
            "variant", variant if variant in _VALID_VARIANTS else VARIANT_DEFAULT,
        )
        self._build_chrome()
        self.set_title(title)
        if content_widget is not None:
            self.set_content_widget(content_widget)
        self.set_metadata_lines(metadata_lines or [])
        if title_actions:
            self.set_title_actions(list(title_actions))

    # ── Construction ─────────────────────────────────────────

    def _build_chrome(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        self._title_label = QLabel()
        self._title_label.setObjectName(_TITLE_OBJECT_NAME)
        self._title_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        self._title_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed,
        )
        title_row.addWidget(self._title_label, stretch=1)
        self._actions_slot = QWidget()
        self._actions_slot_layout = QHBoxLayout(self._actions_slot)
        self._actions_slot_layout.setContentsMargins(0, 0, 0, 0)
        self._actions_slot_layout.setSpacing(6)
        self._actions_slot.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed,
        )
        self._actions_slot.setVisible(False)
        title_row.addWidget(self._actions_slot)
        outer.addLayout(title_row)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)

        self._left_slot = QWidget()
        self._left_slot.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed,
        )
        self._left_slot_layout = QVBoxLayout(self._left_slot)
        self._left_slot_layout.setContentsMargins(0, 0, 0, 0)
        self._left_slot_layout.setSpacing(0)
        body.addWidget(self._left_slot, stretch=1)

        self._right_column = QWidget()
        self._right_column.setObjectName(_METADATA_OBJECT_NAME)
        self._right_column.setFixedWidth(self._right_column_width)
        self._right_column.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed,
        )
        self._right_layout = QVBoxLayout(self._right_column)
        self._right_layout.setContentsMargins(0, 0, 0, 0)
        self._right_layout.setSpacing(2)
        self._right_layout.addStretch(1)
        body.addWidget(self._right_column)

        outer.addLayout(body)

    # ── Public API ───────────────────────────────────────────

    def set_title(self, text: str) -> None:
        text = str(text or "")
        self._title_label.setText(text)
        self._title_label.setVisible(bool(text))

    def set_content_widget(self, widget: QWidget) -> None:
        if self._content_widget is not None:
            self._left_slot_layout.removeWidget(self._content_widget)
            self._content_widget.setParent(None)
            self._content_widget.deleteLater()
        self._content_widget = widget
        if widget is not None:
            self._left_slot_layout.addWidget(widget)

    def set_metadata_lines(self, lines: list[str]) -> None:
        while self._right_layout.count() > 1:
            item = self._right_layout.takeAt(0)
            if item is None:
                continue
            child_widget = item.widget()
            if child_widget is not None:
                child_widget.setParent(None)
                child_widget.deleteLater()

        clean = [str(s) for s in (lines or []) if s]
        for i, text in enumerate(clean):
            label = QLabel(text)
            label.setObjectName("InfoCardRowMetadataLine")
            label.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )
            label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._right_layout.insertWidget(i, label)
        self._right_column.setVisible(bool(clean))

    def content_widget(self) -> Optional[QWidget]:
        return self._content_widget

    def set_title_actions(self, widgets: Iterable[QWidget]) -> None:
        for w in self._action_widgets:
            self._actions_slot_layout.removeWidget(w)
            w.setParent(None)
            w.deleteLater()
        self._action_widgets = []
        widgets = [w for w in (widgets or []) if w is not None]
        for w in widgets:
            self._actions_slot_layout.addWidget(w)
            self._action_widgets.append(w)
        self._actions_slot.setVisible(bool(widgets))

    def title_actions(self) -> tuple[QWidget, ...]:
        return tuple(self._action_widgets)

    def click(self) -> None:
        """Test/driver convenience — fire :attr:`clicked` as if left-clicked."""
        self.clicked.emit()

    def set_variant(self, variant: str) -> None:
        self.setProperty(
            "variant", variant if variant in _VALID_VARIANTS else VARIANT_DEFAULT,
        )
        self.style().unpolish(self)
        self.style().polish(self)

    def variant(self) -> str:
        return str(self.property("variant") or VARIANT_DEFAULT)

    # ── Click forwarding ─────────────────────────────────────

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            in_actions = child is not None and self._actions_slot.isAncestorOf(child)
            if not in_actions:
                self.clicked.emit()
        super().mousePressEvent(event)
