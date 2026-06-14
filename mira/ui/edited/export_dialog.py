"""The Export dialog — destination, file type, collision policy.

Ported 2026-06-10 from the ancestor's ``ui/culler/cull_export_dialog.py``
(both Edit export paths were reaching into the legacy tree with a lazy
import that only fired on click — Nelson's first export crashed with
``ModuleNotFoundError: ui``), then reshaped to the house form grammar
the same evening (Nelson):

* form inputs live in **titled QGroupBoxes** (``FormFieldGroup`` role),
  never label-beside-input; every interactive control carries a hint;
* the file type is **JPEG | TIFF — the rendered, edited photo**.
  Exporting the ORIGINAL file is not offered here: grabbing originals
  belongs to Share (Nelson 2026-06-10). The engine's byte-copy path
  survives untouched for that.

The collision section appears **only when the chosen destination
actually contains colliding files** (a ``collision_probe(dest) -> int``
is injected; the non-destructive *Add as new* is the default).

Decoupled + testable: drive the widgets and read the accessors — no
``exec()`` needed in tests (snapshot on accept).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.cull_export import CollisionPolicy, ExportFileType
from mira.ui.i18n import tr

_JPEG_DEFAULT_Q = 90


@dataclass(frozen=True)
class ExportChoice:
    destination: Path
    file_type: ExportFileType
    jpeg_quality: int
    collision: CollisionPolicy


class ExportDialog(QDialog):
    """``ExportDialog.ask(default_dir, collision_probe=…)`` →
    :class:`ExportChoice` on Export, or ``None`` on Cancel."""

    def __init__(
        self,
        default_dir: Path,
        *,
        default_file_type: ExportFileType = ExportFileType.JPEG,
        collision_probe: Optional[Callable[[Path], int]] = None,
        scope_label: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ExportDialog")
        self.setWindowTitle(tr("Export"))
        self.setModal(True)
        # Nelson eyeball 2026-05-20 v6 (ancestor): the dialog rendered too
        # narrow on Windows and truncated the destination path. The
        # explicit min width comfortably shows a full event path.
        self.setMinimumWidth(640)
        self._probe = collision_probe
        self._snapshot: Optional[ExportChoice] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)

        heading = QLabel(
            tr("Export {what}").replace(
                "{what}", scope_label or tr("the picked photos"))
            if scope_label else tr("Export the picked photos"))
        heading.setObjectName("PageHeading")
        outer.addWidget(heading)

        # ── Destination ────────────────────────────────────────────
        dest_group = QGroupBox(tr("Destination"))
        dest_group.setObjectName("FormFieldGroup")
        dest_row = QHBoxLayout(dest_group)
        self._dest_edit = QLineEdit(str(default_dir or ""))
        self._dest_edit.setToolTip(tr(
            "Folder the exported photos are written into, in per-day "
            "sub-folders. Sources are never touched."
        ))
        self._dest_edit.textChanged.connect(self._on_dest_changed)
        dest_row.addWidget(self._dest_edit, stretch=1)
        browse = QPushButton(tr("Browse…"))
        browse.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        browse.setToolTip(tr("Pick the destination folder."))
        browse.clicked.connect(self._on_browse)
        dest_row.addWidget(browse)
        outer.addWidget(dest_group)

        # ── File type — the rendered, edited photo ─────────────────
        ft_group = QGroupBox(tr("File type"))
        ft_group.setObjectName("FormFieldGroup")
        ft_row = QHBoxLayout(ft_group)
        self._ft_group = QButtonGroup(self)
        self._ft_buttons: dict[ExportFileType, QRadioButton] = {}
        self._q_spin = QSpinBox()
        self._q_spin.setRange(50, 100)
        self._q_spin.setValue(_JPEG_DEFAULT_Q)
        self._q_spin.setPrefix(tr("q "))
        self._q_spin.setToolTip(tr("JPEG quality (higher = bigger)."))
        for ft, label, hint in (
            (ExportFileType.JPEG, tr("JPEG"), tr(
                "The edited photo rendered to JPEG — your develop "
                "choices baked in. The everyday export.")),
            (ExportFileType.TIFF, tr("TIFF"), tr(
                "The edited photo rendered to TIFF — lossless and "
                "large; for print or further editing elsewhere.")),
        ):
            rb = QRadioButton(label)
            rb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            rb.setToolTip(hint)
            self._ft_group.addButton(rb)
            self._ft_buttons[ft] = rb
            ft_row.addWidget(rb)
            if ft is ExportFileType.JPEG:
                ft_row.addWidget(self._q_spin)
        ft_row.addStretch(1)
        default_rb = self._ft_buttons.get(
            default_file_type, self._ft_buttons[ExportFileType.JPEG])
        default_rb.setChecked(True)
        for rb in self._ft_buttons.values():
            rb.toggled.connect(self._sync_quality_enabled)
        outer.addWidget(ft_group)
        self._sync_quality_enabled()

        # ── Collisions (hidden until a real collision) ─────────────
        self._coll_box = QGroupBox(tr("Name collisions"))
        self._coll_box.setObjectName("FormFieldGroup")
        cb = QVBoxLayout(self._coll_box)
        self._coll_label = QLabel("")
        self._coll_label.setObjectName("PageHint")
        self._coll_label.setWordWrap(True)
        cb.addWidget(self._coll_label)
        crow = QHBoxLayout()
        self._coll_group = QButtonGroup(self)
        self._rb_unique = QRadioButton(tr("Add as new (keep both)"))
        self._rb_unique.setToolTip(tr(
            "Non-destructive: existing files keep their names; the new "
            "exports arrive under \"name (2)\"-style names."))
        self._rb_override = QRadioButton(tr("Replace the existing files"))
        self._rb_override.setToolTip(tr(
            "Overwrite the colliding files with these exports."))
        for rb in (self._rb_unique, self._rb_override):
            rb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self._coll_group.addButton(rb)
            crow.addWidget(rb)
        self._rb_unique.setChecked(True)     # non-destructive default
        crow.addStretch(1)
        cb.addLayout(crow)
        self._coll_box.setVisible(False)
        outer.addWidget(self._coll_box)

        outer.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok,
            parent=self,
        )
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if self._ok is not None:
            self._ok.setObjectName("Primary")
            self._ok.setText(tr("Export"))
            self._ok.setDefault(True)
            self._ok.setToolTip(tr("Run the export with these choices."))
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel is not None:
            cancel.setToolTip(tr("Close without exporting."))
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self._refresh_collisions()
        self._refresh_ok()

    # ── slots ────────────────────────────────────────────────────────

    def _on_browse(self) -> None:
        start = self._dest_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, tr("Export destination"), start,
            QFileDialog.Option.DontUseNativeDialog
            | QFileDialog.Option.ShowDirsOnly,
        )
        if chosen:
            self._dest_edit.setText(chosen)

    def _on_dest_changed(self) -> None:
        self._refresh_collisions()
        self._refresh_ok()

    def _sync_quality_enabled(self) -> None:
        jpeg = self._ft_buttons.get(ExportFileType.JPEG)
        self._q_spin.setEnabled(jpeg is not None and jpeg.isChecked())

    def _refresh_ok(self) -> None:
        if self._ok is not None:
            self._ok.setEnabled(bool(self._dest_edit.text().strip()))

    def _refresh_collisions(self) -> None:
        dest = self._dest_edit.text().strip()
        n = 0
        if dest and self._probe is not None:
            try:
                n = int(self._probe(Path(dest)))
            except Exception:  # noqa: BLE001 — probe is best-effort
                n = 0
        if n > 0:
            self._coll_label.setText(tr(
                "{n} file(s) with the same name already exist there. "
                "Add the new ones alongside, or replace them?"
            ).replace("{n}", str(n)))
            self._coll_box.setVisible(True)
        else:
            self._coll_box.setVisible(False)

    # ── results ──────────────────────────────────────────────────────

    def _file_type(self) -> ExportFileType:
        for ft, rb in self._ft_buttons.items():
            if rb.isChecked():
                return ft
        return ExportFileType.JPEG

    def _collision(self) -> CollisionPolicy:
        return (
            CollisionPolicy.OVERRIDE
            if self._rb_override.isChecked()
            else CollisionPolicy.UNIQUE
        )

    def choice(self) -> ExportChoice:
        if self._snapshot is not None:
            return self._snapshot
        return ExportChoice(
            destination=Path(self._dest_edit.text().strip()),
            file_type=self._file_type(),
            jpeg_quality=int(self._q_spin.value()),
            collision=self._collision(),
        )

    def _on_accept(self) -> None:
        if not self._dest_edit.text().strip():
            return
        self._snapshot = self.choice()
        self.accept()

    @staticmethod
    def ask(
        default_dir: Path,
        *,
        default_file_type: ExportFileType = ExportFileType.JPEG,
        collision_probe: Optional[Callable[[Path], int]] = None,
        scope_label: str = "",
        parent: QWidget | None = None,
    ) -> Optional[ExportChoice]:
        dlg = ExportDialog(
            default_dir, default_file_type=default_file_type,
            collision_probe=collision_probe, scope_label=scope_label,
            parent=parent,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.choice()
        return None
