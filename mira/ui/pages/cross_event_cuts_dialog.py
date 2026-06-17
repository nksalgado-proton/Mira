"""The cross-event Cuts browser (spec/81 Phase 2 polish — Item 4 UI).

Lists every cross-event Cut in the library (cut rows with
``source_dc_kind = 'user'`` across all event.db files). Each row shows the
tag, anchor event, member count, export status, and surfaces actions to
Open (placeholder — flat grid deferred), Export (cross-event export pipeline)
and Delete.

Pure view — :class:`mira.gateway.gateway.Gateway` owns the multi-event
walk via :meth:`Gateway.cross_event_cuts`; this module just renders.
"""
from __future__ import annotations

import logging
from typing import Callable, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core import cut_names
from mira.gateway.gateway import CrossEventCutRow
from mira.ui.design import ghost_button, primary_button
from mira.ui.i18n import tr

log = logging.getLogger(__name__)


class _CutRow(QFrame):
    """One row — display + actions.

    Signals:
        delete_requested(CrossEventCutRow)
        export_requested(CrossEventCutRow)
        open_requested(CrossEventCutRow)
    """

    delete_requested = pyqtSignal(CrossEventCutRow)
    export_requested = pyqtSignal(CrossEventCutRow)
    open_requested = pyqtSignal(CrossEventCutRow)

    def __init__(self, row: CrossEventCutRow,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._row = row
        self.setObjectName("CrossEventCutRow")
        self._build()

    def _build(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(2)

        tag_label = QLabel(cut_names.display_tag(self._row.tag))
        tag_label.setObjectName("CrossEventCutTag")
        f = tag_label.font(); f.setBold(True)
        tag_label.setFont(f)
        left.addWidget(tag_label)

        meta = QLabel(tr("anchor: {anchor} · {n} members").format(
            anchor=self._row.anchor_event_name,
            n=self._row.member_count))
        meta.setObjectName("CrossEventCutMeta")
        left.addWidget(meta)

        if self._row.last_exported_at:
            stamp = QLabel(tr("last exported: {ts}").format(
                ts=self._row.last_exported_at))
        else:
            stamp = QLabel(tr("not yet exported"))
        stamp.setObjectName("CrossEventCutStamp")
        left.addWidget(stamp)

        outer.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(6)
        export_btn = primary_button(tr("Export"))
        export_btn.clicked.connect(
            lambda: self.export_requested.emit(self._row))
        right.addWidget(export_btn)
        kebab = ghost_button(tr("⋯"))
        kebab.clicked.connect(self._show_menu)
        self._kebab = kebab
        right.addWidget(kebab)
        outer.addLayout(right)

    def _show_menu(self) -> None:
        menu = QMenu(self)
        open_action = menu.addAction(tr("Open…"))
        open_action.triggered.connect(
            lambda: self.open_requested.emit(self._row))
        delete_action = menu.addAction(tr("Delete"))
        delete_action.triggered.connect(
            lambda: self.delete_requested.emit(self._row))
        menu.exec(self._kebab.mapToGlobal(self._kebab.rect().bottomLeft()))


class CrossEventCutsDialog(QDialog):
    """List of cross-event Cuts.

    Signals:
        export_requested(CrossEventCutRow)   — host wires the export pipeline.
        open_requested(CrossEventCutRow)     — flat-grid view (deferred).
    """

    export_requested = pyqtSignal(CrossEventCutRow)
    open_requested = pyqtSignal(CrossEventCutRow)

    def __init__(
        self,
        umbrella_gateway,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Cross-event Cuts"))
        self.setMinimumSize(720, 480)
        self.setObjectName("CrossEventCutsDialog")
        self._gw = umbrella_gateway
        self._rows_layout: Optional[QVBoxLayout] = None
        self._empty_label: Optional[QLabel] = None
        self._build_layout()
        self.refresh()

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        top = QHBoxLayout()
        title = QLabel(tr("Cross-event Cuts"))
        title.setObjectName("CrossEventCutsTitle")
        f = title.font(); f.setBold(True); f.setPointSize(f.pointSize() + 2)
        title.setFont(f)
        top.addWidget(title)
        top.addStretch()
        close_btn = ghost_button(tr("Close"))
        close_btn.clicked.connect(self.accept)
        top.addWidget(close_btn)
        root.addLayout(top)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        host = QWidget(scroll)
        rows = QVBoxLayout(host)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(8)
        self._empty_label = QLabel(tr(
            "No cross-event Cuts yet. Build a cross-event Dynamic Collection "
            "and pin it into a Cut to populate this list."))
        self._empty_label.setObjectName("CrossEventCutsEmpty")
        self._empty_label.setWordWrap(True)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rows.addWidget(self._empty_label)
        rows.addStretch()
        self._rows_layout = rows
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

    def refresh(self) -> None:
        """Re-walk the library + rebuild rows."""
        if self._rows_layout is None:
            return
        for i in reversed(range(self._rows_layout.count())):
            w = self._rows_layout.itemAt(i).widget()
            if isinstance(w, _CutRow):
                w.setParent(None)
                w.deleteLater()
        cuts = self._gw.cross_event_cuts()
        if not cuts:
            if self._empty_label is not None:
                self._empty_label.setVisible(True)
            return
        if self._empty_label is not None:
            self._empty_label.setVisible(False)
        for row in cuts:
            w = _CutRow(row, parent=self)
            w.delete_requested.connect(self._on_delete)
            w.export_requested.connect(self.export_requested.emit)
            w.open_requested.connect(self.open_requested.emit)
            self._rows_layout.insertWidget(0, w)

    def _on_delete(self, row: CrossEventCutRow) -> None:
        ok = QMessageBox.question(
            self,
            tr("Delete cross-event Cut"),
            tr("Delete {tag}? Members + any anchored snapshots will be "
               "removed; already-exported folders on disk stay where they "
               "are.").format(tag=cut_names.display_tag(row.tag)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            self._gw.delete_cross_event_cut(row.anchor_event_id, row.cut_id)
        except Exception as exc:                           # noqa: BLE001
            QMessageBox.warning(
                self, tr("Delete failed"),
                tr("Could not delete: {err}").format(err=str(exc)))
            return
        self.refresh()


__all__ = ["CrossEventCutsDialog"]
