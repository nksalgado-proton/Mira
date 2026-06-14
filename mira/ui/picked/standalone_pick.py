"""Standalone Quick Sweep — sidebar entry (Nelson 2026-06-01).

Pick a source folder + a destination, triage with the Quick Sweep (the bucketed cull mode),
then copy the keepers to the destination. Reuses ``read_source_items`` (folder → SourceItems),
``QuickSweepPage``, and ``core.standalone_cull_copy.copy_kept`` (flat layout — no
classification at triage time, matching the Quick Sweep's premise).

A **lean** source/destination dialog replaces the legacy's larger inline picker (Nelson
2026-06-01 — the legacy was "much larger than required"). The **full Picker standalone is
deferred** (it runs only on an event in the rebuild; it stays testable via an event's Cull
tile) — its sidebar entry shows a short notice.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Set

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.fresh_source import read_source_items
from core.standalone_cull_copy import CopyItem, copy_kept
from mira.ui.base.progress import run_with_progress
from mira.ui.i18n import tr

log = logging.getLogger(__name__)


class StandaloneCullSetupDialog(QDialog):
    """Lean source + destination picker for standalone culling."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Quick Sweep — source & destination"))
        self.setModal(True)
        self._source = ""
        self._dest = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)
        intro = QLabel(tr(
            "Choose a folder of photos/videos to cull, and where to copy the ones you keep."))
        intro.setWordWrap(True)
        outer.addWidget(intro)

        self._source_edit = self._add_row(outer, tr("Source folder:"), self._browse_source)
        self._dest_edit = self._add_row(outer, tr("Copy picked to:"), self._browse_dest)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton(tr("Cancel"))
        cancel.clicked.connect(self.reject)
        self._ok = QPushButton(tr("Start picking"))
        self._ok.setObjectName("PrimaryButton")
        self._ok.setDefault(True)
        self._ok.setEnabled(False)
        self._ok.clicked.connect(self._accept)
        for b in (cancel, self._ok):
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        row.addWidget(cancel)
        row.addWidget(self._ok)
        outer.addLayout(row)

    def _add_row(self, outer: QVBoxLayout, label: str, on_browse) -> QLineEdit:
        row = QHBoxLayout()
        lab = QLabel(label)
        lab.setMinimumWidth(110)
        row.addWidget(lab)
        edit = QLineEdit()
        edit.setReadOnly(True)
        edit.setMinimumWidth(360)
        row.addWidget(edit, stretch=1)
        btn = QPushButton(tr("Browse…"))
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn.clicked.connect(on_browse)
        row.addWidget(btn)
        outer.addLayout(row)
        return edit

    def _browse_source(self) -> None:
        d = QFileDialog.getExistingDirectory(self, tr("Select the source folder"))
        if d:
            self._source = d
            self._source_edit.setText(d)
            self._sync_ok()

    def _browse_dest(self) -> None:
        d = QFileDialog.getExistingDirectory(self, tr("Select the destination folder"))
        if d:
            self._dest = d
            self._dest_edit.setText(d)
            self._sync_ok()

    def _sync_ok(self) -> None:
        self._ok.setEnabled(
            bool(self._source) and bool(self._dest) and self._source != self._dest)

    def _accept(self) -> None:
        if not self._source or not self._dest:
            return
        if self._source == self._dest:
            QMessageBox.warning(
                self, tr("Quick Sweep"),
                tr("Source and destination must be different folders."))
            return
        self.accept()

    def source_path(self) -> Path:
        return Path(self._source)

    def dest_path(self) -> Path:
        return Path(self._dest)


def run_standalone_fast_cull(parent: QWidget) -> None:
    """The whole standalone Quick Sweep flow: pick → scan → triage → copy keepers → summary."""
    dlg = StandaloneCullSetupDialog(parent)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return
    source, dest = dlg.source_path(), dlg.dest_path()

    ok, items = run_with_progress(
        parent, tr("Quick Sweep"),
        lambda report: read_source_items(source),
        label=tr("Reading {p}…").replace("{p}", source.name))
    if not ok:
        QMessageBox.warning(
            parent, tr("Quick Sweep"), tr("Could not read that folder (see log)."))
        return
    if not items:
        QMessageBox.information(
            parent, tr("Quick Sweep"), tr("No photos or videos found in that folder."))
        return

    kept = _host_fast_culler(parent, items)
    if kept is None:
        return                                  # cancelled
    if not kept:
        QMessageBox.information(
            parent, tr("Quick Sweep"), tr("Nothing picked — nothing copied."))
        return

    copy_items = [CopyItem(source=p, style="", rel_dest=Path(p.name)) for p in kept]
    ok, result = run_with_progress(
        parent, tr("Quick Sweep"),
        lambda report: copy_kept(
            copy_items, dest,
            progress=lambda msg, cur, tot: report(cur, tot, msg)),
        label=tr("Copying picked files…"))
    if not ok:
        QMessageBox.warning(parent, tr("Quick Sweep"), tr("Copy failed (see log)."))
        return
    QMessageBox.information(
        parent, tr("Quick Sweep — done"),
        tr("Copied {n} picked file(s) to:\n{dest}")
        .replace("{n}", str(result.ok_count)).replace("{dest}", str(dest)))


def _host_fast_culler(parent: QWidget, items) -> Optional[Set[Path]]:
    """Show the Quick Sweep over ``items`` in a modal; return the kept paths, or ``None``
    if cancelled."""
    from mira.ui.picked.quick_sweep_page import QuickSweepPage

    host = QDialog(parent)
    host.setWindowTitle(tr("Quick Sweep"))
    host.setModal(True)
    host.resize(1100, 740)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    page = QuickSweepPage()
    lay.addWidget(page)

    result: dict = {"picked": None}
    page.saved.connect(
        lambda kept: (result.__setitem__("picked", set(kept)), host.accept()))
    page.cancelled.connect(
        lambda: (result.__setitem__("picked", None), host.reject()))
    if not page.load(items):
        return None
    page.setFocus()
    host.exec()
    return result["picked"]
