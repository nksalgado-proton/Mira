"""The manual "Restore from backup…" dialog (spec/79 §5 + spec/82
§A.4).

Shows the snapshot list for one event — timestamp + reason +
schema/app version — and lets the user pick one to restore. Used
from the per-event Events menu; complements the auto-restore
offered by the integrity-failure dialog (which always picks the
latest good snapshot). This dialog exists so the user can
deliberately roll back to an older milestone snapshot — the
trip-workflow "I picked the wrong cluster yesterday, take me back
to before that day's ingest" case.

After the user picks + confirms, the dialog runs
``db_backup.restore`` synchronously (small db, milliseconds) and
returns the corrupt-copy path of the just-replaced file, or
``None`` if the user cancelled / restore failed.

Tr-styled strings only; no inline ``setStyleSheet`` (spec/05).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import db_backup
from core.db_backup import SnapshotInfo
from mira.ui.i18n import tr

log = logging.getLogger(__name__)


def _format_snapshot_line(snap: SnapshotInfo) -> str:
    """One-line label for the list — newest first, classifies the
    snapshot at a glance.

    Shape: ``2026-06-17T09:30:00Z · milestone · schema 4 · v1.2.3``
    """
    reason_label = (
        tr("milestone") if snap.reason == db_backup.REASON_MILESTONE
        else tr("periodic")
    )
    schema_label = tr("schema {n}").replace("{n}", str(snap.schema_version))
    app_label = (
        f" · v{snap.app_version}" if snap.app_version else ""
    )
    return (
        f"{snap.created_at} · {reason_label} · {schema_label}{app_label}"
    )


class RestoreBackupDialog(QDialog):
    """List the event's snapshots; the user picks one and confirms.

    Caller hands in the backups dir (``Gateway.event_backups_dir``)
    and the live event.db path (``<event_root>/event.db``). The
    dialog handles the rest: rendering, verify-on-select feedback,
    the actual restore call.
    """

    def __init__(
        self,
        *,
        event_name: str,
        backups_dir: Path,
        db_path: Path,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._backups_dir = Path(backups_dir)
        self._db_path = Path(db_path)
        self._snapshots: List[SnapshotInfo] = []
        self._restored_corrupt_path: Optional[Path] = None
        self.setWindowTitle(tr("Restore from backup"))
        self.setModal(True)
        self.resize(560, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        header = QLabel(
            tr("Pick a snapshot to roll {event} back to.").replace(
                "{event}", event_name)
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        self._list = QListWidget()
        self._list.setObjectName("SnapshotList")
        layout.addWidget(self._list, stretch=1)

        self._hint = QLabel("")
        self._hint.setObjectName("DialogHint")
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self._cancel = QPushButton(tr("Cancel"))
        self._cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel.clicked.connect(self.reject)
        buttons.addWidget(self._cancel)
        self._restore = QPushButton(tr("Restore"))
        self._restore.setObjectName("PrimaryButton")
        self._restore.setCursor(Qt.CursorShape.PointingHandCursor)
        self._restore.clicked.connect(self._on_restore)
        self._restore.setEnabled(False)
        buttons.addWidget(self._restore)
        layout.addLayout(buttons)

        self._populate()
        self._list.currentRowChanged.connect(self._on_selection_changed)

    def _populate(self) -> None:
        """Load snapshots newest-first into the list. Empty-state
        message + disabled Restore when no snapshots exist."""
        self._snapshots = db_backup.list_snapshots(self._backups_dir)
        self._list.clear()
        if not self._snapshots:
            self._list.addItem(QListWidgetItem(tr(
                "No snapshots yet. Snapshots are saved automatically "
                "after each day's ingest and on clean event close.")))
            self._list.item(0).setFlags(Qt.ItemFlag.NoItemFlags)
            return
        for snap in self._snapshots:
            self._list.addItem(_format_snapshot_line(snap))

    def _on_selection_changed(self, row: int) -> None:
        if 0 <= row < len(self._snapshots):
            self._restore.setEnabled(True)
            self._hint.setText("")
        else:
            self._restore.setEnabled(False)

    def _on_restore(self) -> None:
        row = self._list.currentRow()
        if not 0 <= row < len(self._snapshots):
            return
        snap = self._snapshots[row]
        try:
            self._restored_corrupt_path = db_backup.restore(
                snap, self._db_path)
            log.info(
                "spec/82 §A.4 manual restore: %s rolled back to "
                "snapshot %s; previous file saved at %s",
                self._db_path, snap.db_path,
                self._restored_corrupt_path)
            self.accept()
        except ValueError as exc:
            log.warning(
                "spec/82 §A.4 restore failed (verify): %s", exc)
            self._hint.setText(tr(
                "This snapshot failed its integrity check and can't "
                "be used. Pick an older one."))
        except OSError as exc:
            log.warning("spec/82 §A.4 restore failed (I/O): %s", exc)
            self._hint.setText(tr(
                "Mira couldn't swap the snapshot in. Check disk space "
                "and permissions, then try again."))

    def restored_corrupt_path(self) -> Optional[Path]:
        """After ``exec()`` returns Accepted, this is the path of the
        replaced file (``corrupt-<ts>.db`` next to the snapshot)."""
        return self._restored_corrupt_path


__all__ = ["RestoreBackupDialog"]
