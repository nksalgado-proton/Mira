"""First-run library picker (spec/76 §B.4) — two doors.

Shown at startup when :func:`mira.paths.library_root` returns ``None``
(no bootstrap pointer, no env override). Blocks the launch until the
user picks one of:

* **Create a new library** — choose an (empty) folder, scaffold
  ``.mira/`` + ``Collections/`` + ``Recipes/``, migrate the legacy
  ``%LOCALAPPDATA%\\Mira`` user-data into ``<root>/.mira/``, and write
  the bootstrap pointer.
* **Open an existing library** — choose a folder that is already a
  Mira library (has a ``.mira/`` machinery folder), and write the
  bootstrap pointer at it.

After either path returns successfully the bootstrap pointer is on
disk and every subsequent call to :func:`mira.paths.library_root` /
:func:`mira.paths.user_data_dir` follows it. Cancel rejects the dialog;
the caller exits.

The dialog deliberately stays small — no logo, no welcome copy, just
the choice + a one-line hint. The setup wizard (the existing
:class:`mira.ui.wizard.wizard_window.WizardWindow`) opens AFTER the
library root exists; until it does, Mira can't even decide where to
write its log files.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from core import library_root as _library_root
from mira.ui.design.buttons import ghost_button, primary_button
from mira.ui.i18n import tr

log = logging.getLogger(__name__)


class FirstRunLibraryDialog(QDialog):
    """Two-door modal dialog. Drives the spec/76 §B.4 first-run flow."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setObjectName("FirstRunLibraryDialog")
        self.setWindowTitle(tr("Choose your Mira library"))
        self.resize(540, 280)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QWidget()
        b = QVBoxLayout(body)
        b.setContentsMargins(28, 28, 28, 28)
        b.setSpacing(14)

        title = QLabel(tr("Where should Mira keep your library?"))
        title.setObjectName("CardTitle")
        b.addWidget(title)

        explain = QLabel(tr(
            "Your library is one folder holding every event, every "
            "collection, every recipe, and Mira's own machinery. You "
            "can keep it on a local disk or a NAS share, and move it "
            "later — Mira only needs the location once.\n\n"
            "Choose Create to start a fresh library, or Open to point "
            "Mira at an existing one."
        ))
        explain.setObjectName("Sub")
        explain.setWordWrap(True)
        b.addWidget(explain)

        b.addStretch()

        outer.addWidget(body, 1)

        footer_host = QWidget()
        footer = QHBoxLayout(footer_host)
        footer.setContentsMargins(22, 14, 22, 14)
        footer.setSpacing(8)

        cancel = ghost_button(tr("Cancel"))
        cancel.clicked.connect(self._on_cancel)
        footer.addWidget(cancel)
        footer.addStretch()

        self._open_btn = ghost_button(tr("Open existing library…"))
        self._open_btn.clicked.connect(self._on_open_clicked)
        footer.addWidget(self._open_btn)

        self._create_btn = primary_button(tr("Create new library…"))
        self._create_btn.clicked.connect(self._on_create_clicked)
        self._create_btn.setDefault(True)
        self._create_btn.setAutoDefault(True)
        footer.addWidget(self._create_btn)

        outer.addWidget(footer_host)

        self._chosen_root: Optional[Path] = None
        self._migrated: bool = False

    # ── public state read by the caller after exec() returns ─────────

    def chosen_root(self) -> Optional[Path]:
        """The library root the user committed to, or ``None`` if they
        cancelled. Defined only after ``exec()`` returns
        ``Accepted``."""
        return self._chosen_root

    def did_migrate_legacy(self) -> bool:
        """``True`` if a legacy ``%LOCALAPPDATA%\\Mira`` migration ran
        during the Create flow. Lets the caller surface a one-time
        note in the log."""
        return self._migrated

    # ── slots ────────────────────────────────────────────────────────

    def _on_cancel(self) -> None:
        log.info("first_run_library: user cancelled")
        self.reject()

    def _on_create_clicked(self) -> None:
        path = self._pick_folder(
            tr("Choose a folder for your new Mira library"))
        if path is None:
            return
        # Empty-ish folder check: a non-empty folder that ISN'T already
        # a Mira library is ambiguous; the spec/76 §B.4 contract says
        # "pick an empty location". We tolerate hidden entries because
        # explorer/macOS scatter ``.DS_Store`` and Thumbs.db without
        # consent, and refuse anything else.
        if not self._is_safe_create_target(path):
            QMessageBox.warning(
                self,
                tr("Folder isn't empty"),
                tr(
                    "Mira can only Create a new library in an empty "
                    "folder. Pick another folder, or use Open to point "
                    "Mira at an existing library."
                ),
            )
            return
        try:
            _library_root.scaffold_library(path)
            self._migrated = _library_root.migrate_legacy_data_dir(path)
            _library_root.write_pointer(path)
        except OSError as exc:
            log.warning("first_run_library: scaffold/pointer write failed: %s",
                        exc)
            QMessageBox.critical(
                self,
                tr("Couldn't create library"),
                tr(
                    "Mira couldn't write the library at {path}: {err}.\n"
                    "Check the folder permissions and try again."
                ).format(path=str(path), err=str(exc)),
            )
            return
        self._chosen_root = path
        log.info("first_run_library: created library at %s (migrated=%s)",
                 path, self._migrated)
        self.accept()

    def _on_open_clicked(self) -> None:
        path = self._pick_folder(
            tr("Open an existing Mira library"))
        if path is None:
            return
        if not _library_root.is_library_shape(path):
            QMessageBox.warning(
                self,
                tr("Not a Mira library"),
                tr(
                    "{path} doesn't look like a Mira library — there's "
                    "no .mira/ folder inside. Use Create new library to "
                    "set one up here, or pick a different folder."
                ).format(path=str(path)),
            )
            return
        try:
            _library_root.write_pointer(path)
        except OSError as exc:
            log.warning("first_run_library: pointer write failed: %s", exc)
            QMessageBox.critical(
                self,
                tr("Couldn't open library"),
                tr(
                    "Mira couldn't remember the library location: {err}.\n"
                    "Check the system-config folder permissions and try "
                    "again."
                ).format(err=str(exc)),
            )
            return
        self._chosen_root = path
        log.info("first_run_library: opened existing library at %s", path)
        self.accept()

    # ── helpers ──────────────────────────────────────────────────────

    def _pick_folder(self, caption: str) -> Optional[Path]:
        chosen = QFileDialog.getExistingDirectory(
            self, caption, str(Path.home()),
            QFileDialog.Option.ShowDirsOnly,
        )
        if not chosen:
            return None
        return Path(chosen)

    @staticmethod
    def _is_safe_create_target(path: Path) -> bool:
        """A folder is OK to Create into when:

        * It doesn't exist yet (we create it), OR
        * It exists and holds only ``.DS_Store`` / ``Thumbs.db``
          (filesystem cruft the OS adds without asking).

        A folder with real content is rejected — Create would either
        clash with existing files (Collections / Recipes) or quietly
        shadow them.
        """
        if not path.exists():
            return True
        if not path.is_dir():
            return False
        try:
            entries = list(path.iterdir())
        except OSError:
            return False
        cruft = {".DS_Store", "Thumbs.db", "desktop.ini"}
        return all(e.name in cruft for e in entries)


__all__ = ["FirstRunLibraryDialog"]
