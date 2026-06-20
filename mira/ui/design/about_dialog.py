"""About Mira — the small modal that surfaces the brand lockup + version.

Reached from **Help → About Mira** (spec/74 §3). The dialog is the one
surface where the ``MiraLogo(tagline=True)`` lockup ships (everywhere else
the tagline stays hidden — the title-bar logo at ``tile_size=24`` is too
small for legible tagline type). Layout:

    ┌─────────────────────────────────┐
    │      [tile]  M✦ıra              │
    │              See the keepers.   │
    │                                 │
    │            Version 0.1.0        │
    │                                 │
    │  A Windows photography workflow │
    │  tool for serious amateurs.     │
    ├─────────────────────────────────┤
    │                       [ Close ] │
    └─────────────────────────────────┘

The label roles (``CardTitle`` / ``Sub`` / ``Faint``) all come from
``assets/themes/redesign.qss`` and exist in both light + dark builds, so no
new QSS rule is needed.
"""
from __future__ import annotations

from importlib import metadata
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from mira.ui.design.brand import MiraLogo
from mira.ui.design.buttons import primary_button
from mira.ui.i18n import tr
from mira.ui.palette import PALETTE


def _app_version() -> str:
    """Read the version from package metadata. Falls back to ``"dev"`` when
    the package isn't installed (e.g. a checkout that skipped ``pip install
    -e .``) so the dialog stays openable in any dev configuration."""
    try:
        return metadata.version("mira")
    except metadata.PackageNotFoundError:
        return "dev"


def _palette_mode() -> str:
    app = QApplication.instance()
    return (app.property("theme") if app else None) or "dark"


def _divider() -> QFrame:
    """Same hairline the rest of the design-system dialogs use; reads the
    line colour from the live palette so it follows the theme toggle."""
    d = QFrame()
    d.setFrameShape(QFrame.Shape.HLine)
    d.setObjectName("DialogDivider")  # themed hairline (redesign.qss)
    return d


class AboutDialog(QDialog):
    """Modal About box — MiraLogo (with tagline) + version + one-line
    description, with a single Close action."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(tr("About Mira"))
        self.resize(440, 320)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QWidget()
        b = QVBoxLayout(body)
        b.setContentsMargins(28, 28, 28, 24)
        b.setSpacing(16)

        # Brand lockup — centred so the tile + wordmark sit symmetrically
        # over the version / description block below them.
        lockup_row = QHBoxLayout()
        lockup_row.addStretch()
        lockup_row.addWidget(MiraLogo(tile_size=48, tagline=True))
        lockup_row.addStretch()
        b.addLayout(lockup_row)

        version = QLabel(
            tr("Version {v}").format(v=_app_version())
        )
        version.setObjectName("Faint")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        b.addWidget(version)

        desc = QLabel(
            tr("A Windows photography workflow tool for serious amateurs.")
        )
        desc.setObjectName("Sub")
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        b.addWidget(desc)

        b.addStretch(1)
        outer.addWidget(body, 1)
        outer.addWidget(_divider())

        footer_host = QWidget()
        footer = QHBoxLayout(footer_host)
        footer.setContentsMargins(22, 14, 22, 14)
        footer.setSpacing(8)
        footer.addStretch()
        close_btn = primary_button(tr("Close"))
        close_btn.clicked.connect(self.accept)
        close_btn.setDefault(True)
        close_btn.setAutoDefault(True)
        footer.addWidget(close_btn)
        outer.addWidget(footer_host)


def show_about(parent: Optional[QWidget]) -> None:
    """Shortcut: open the About dialog modally."""
    AboutDialog(parent=parent).exec()
