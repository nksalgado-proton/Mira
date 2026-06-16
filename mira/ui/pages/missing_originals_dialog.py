"""Surface 03 — Missing-originals dialog (Lightroom-style locate/relink).

The user-facing half of the locate/relink flow over the captured tree
(charter §7). Reads the verdict from :meth:`Gateway.check_originals` and
presents the right action surface for each state:

* ``STORAGE_OFFLINE``: a non-destructive alert ("reconnect your
  storage") — no action surfaces, the only button closes the dialog.
* ``ORIGINALS_MOVED``: a Locate-and-relink primary (opens
  ``QFileDialog.getExistingDirectory`` → caller calls
  :meth:`Gateway.relink_event`) plus a "Not now" ghost. A muted
  "These files are gone for good" link opens a destructive-confirm
  sub-dialog that, on confirm, returns the ``PRUNE`` outcome —
  the only path from a missing file to a deleted item, always
  through an explicit confirmation (plan §"Hard rules").

The dialog is dumb: it presents the choice and reports the user's
answer through :attr:`outcome` (+ :attr:`chosen_path` for the relink
case). The caller — :class:`MainWindow` — owns the gateway calls.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mira.gateway import OriginalsCheck, OriginalsHealth
from mira.ui.design import (
    GLYPH_CROSS,
    danger_ghost_button,
    ghost_button,
    primary_button,
    tinted_svg_pixmap,
)
from mira.ui.design.dialogs import MessageDialog
from mira.ui.i18n import tr
from mira.ui.palette import PALETTE


OUTCOME_KEPT = "kept"
OUTCOME_RELINK = "relink"
OUTCOME_PRUNE = "prune"


def _palette_mode() -> str:
    app = QApplication.instance()
    return (app.property("theme") if app else None) or "dark"


class MissingOriginalsDialog(QDialog):
    """Three-mode modal driven by an :class:`OriginalsCheck` verdict.

    After ``exec()``:
      * :attr:`outcome` is one of ``"kept" | "relink" | "prune"``
      * :attr:`chosen_path` is the user-picked folder when outcome is
        ``"relink"``, else ``None``
    """

    def __init__(
        self,
        *,
        check: OriginalsCheck,
        event_name: str = "",
        missing_count: Optional[int] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("MissingOriginalsDialog")
        self.setModal(True)
        self.setWindowTitle(tr("Locate missing files"))
        self.resize(520, 280)

        self._check = check
        self._event_name = event_name or tr("this event")
        self._missing_count = missing_count
        self._outcome = OUTCOME_KEPT
        self._chosen_path: Optional[Path] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header_bar())
        outer.addWidget(self._divider())
        outer.addWidget(self._build_body(), 1)
        outer.addWidget(self._divider())
        outer.addWidget(self._build_footer())

    # ── public API ──────────────────────────────────────────────────────

    @property
    def outcome(self) -> str:
        """``"kept"`` (close / not now), ``"relink"`` (user picked a
        folder — read :attr:`chosen_path`), or ``"prune"`` (user
        confirmed the destructive branch)."""
        return self._outcome

    @property
    def chosen_path(self) -> Optional[Path]:
        return self._chosen_path

    # ── frame ───────────────────────────────────────────────────────────

    @staticmethod
    def _divider() -> QFrame:
        d = QFrame()
        d.setFrameShape(QFrame.Shape.HLine)
        d.setObjectName("DialogDivider")
        line = PALETTE[_palette_mode()]["line"]
        d.setStyleSheet(
            f"background: {line}; max-height: 1px; min-height: 1px;"
        )
        return d

    def _build_header_bar(self) -> QWidget:
        host = QWidget()
        h = QHBoxLayout(host)
        h.setContentsMargins(18, 14, 14, 14)
        h.setSpacing(12)
        p = PALETTE[_palette_mode()]

        # Intent-tinted icon tile. OFFLINE = warning amber, MOVED = info
        # blue. Same shape as event_header_dialog's accent tile so the
        # surfaces feel related; the colour swap is the only signal.
        intent = (
            "warning"
            if self._check.state == OriginalsHealth.STORAGE_OFFLINE
            else "info"
        )
        accent = "#fbbf24" if intent == "warning" else "#5b8def"
        tile = QLabel("!" if intent == "warning" else "i")
        tile.setFixedSize(32, 32)
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setStyleSheet(
            f"background: {p['accent_soft']}; color: {accent};"
            f" border: 1px solid {accent}; border-radius: 9px;"
            f" font-size: 16px; font-weight: 800;"
        )
        h.addWidget(tile)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(0)
        title = QLabel(self._title_for_state())
        title.setObjectName("CardTitle")
        text_col.addWidget(title)
        if self._event_name:
            sub = QLabel(self._event_name)
            sub.setObjectName("Sub")
            text_col.addWidget(sub)
        h.addLayout(text_col)
        h.addStretch()

        # Close X — same line-icon family as the rest of the dialogs.
        close = QPushButton()
        close.setObjectName("DialogClose")
        close.setFixedSize(30, 30)
        close.setIcon(QIcon(
            tinted_svg_pixmap(GLYPH_CROSS, 14, QColor(p["ink_soft"]))
        ))
        close.setIconSize(QSize(14, 14))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setToolTip(tr("Close"))
        close.setStyleSheet(
            "QPushButton#DialogClose {"
            f" background: transparent;"
            f" border: 1px solid {p['line']}; border-radius: 9px;"
            "}"
            "QPushButton#DialogClose:hover {"
            f" border-color: {p['accent']};"
            "}"
        )
        close.clicked.connect(self._on_close_x)
        h.addWidget(close)
        return host

    def _build_body(self) -> QWidget:
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(10)

        msg = QLabel(self._body_text())
        msg.setObjectName("Sub")
        msg.setWordWrap(True)
        msg.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        v.addWidget(msg)

        # Path hint — show the last-known path so the user knows what to
        # look for. Muted so it doesn't compete with the body sentence.
        path_text = self._path_hint_text()
        if path_text:
            p = PALETTE[_palette_mode()]
            hint = QLabel(path_text)
            hint.setObjectName("Faint")
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color: {p['ink_soft']};")
            hint.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            v.addWidget(hint)

        v.addStretch()
        return host

    def _build_footer(self) -> QWidget:
        host = QWidget()
        f = QHBoxLayout(host)
        f.setContentsMargins(22, 14, 22, 14)
        f.setSpacing(8)

        if self._check.state == OriginalsHealth.ORIGINALS_MOVED:
            # The destructive "gone for good" sits on the LEFT — a
            # danger-ghost so the resting state stays calm; the red
            # hover signals what the click costs. Only the
            # destructive confirm sub-dialog actually fires the prune.
            prune_btn = danger_ghost_button(tr("These files are gone…"))
            prune_btn.setToolTip(tr(
                "Permanently delete the missing items and everything "
                "decided about them (picks, edits, markers, snapshots)."
            ))
            prune_btn.clicked.connect(self._on_prune_clicked)
            f.addWidget(prune_btn)
            f.addStretch()
            cancel = ghost_button(tr("Not now"))
            cancel.clicked.connect(self._on_cancel)
            f.addWidget(cancel)
            locate = primary_button(tr("Locate…"))
            locate.setDefault(True)
            locate.setAutoDefault(True)
            locate.clicked.connect(self._on_locate_clicked)
            f.addWidget(locate)
        else:
            # OFFLINE — the only action is to close. Single primary so
            # the keyboard default works.
            f.addStretch()
            ok = primary_button(tr("Close"))
            ok.setDefault(True)
            ok.setAutoDefault(True)
            ok.clicked.connect(self._on_cancel)
            f.addWidget(ok)
        return host

    # ── copy ────────────────────────────────────────────────────────────

    def _title_for_state(self) -> str:
        if self._check.state == OriginalsHealth.STORAGE_OFFLINE:
            return tr("Your photo storage isn't available")
        return tr("Files moved")

    def _body_text(self) -> str:
        if self._check.state == OriginalsHealth.STORAGE_OFFLINE:
            return tr(
                "Reconnect the drive (or remount the network share) "
                "that holds your originals, then reopen the event. "
                "Nothing has been changed."
            )
        if self._missing_count:
            return tr(
                "{n} originals couldn't be found where they're supposed to be. "
                "If you moved the event folder, pick the new location and "
                "Mira will relink."
            ).format(n=self._missing_count)
        return tr(
            "The originals for this event aren't where they're supposed to be. "
            "If you moved the event folder, pick the new location and "
            "Mira will relink."
        )

    def _path_hint_text(self) -> str:
        path = self._check.event_root or self._check.originals_dir
        if path is None:
            return ""
        return tr("Last known: {path}").format(path=str(path))

    # ── handlers ────────────────────────────────────────────────────────

    def _on_close_x(self) -> None:
        self._outcome = OUTCOME_KEPT
        self.reject()

    def _on_cancel(self) -> None:
        self._outcome = OUTCOME_KEPT
        self.reject()

    def _on_locate_clicked(self) -> None:
        start_dir = ""
        if self._check.event_root is not None:
            # Start the folder picker near the missing path so the user
            # navigates only one level — most relinks are "I moved it
            # to the folder right next to where it was".
            parent_dir = self._check.event_root.parent
            if parent_dir.exists():
                start_dir = str(parent_dir)
            elif self._check.base_path and self._check.base_path.exists():
                start_dir = str(self._check.base_path)
        picked = QFileDialog.getExistingDirectory(
            self, tr("Locate the event folder"), start_dir,
        )
        if not picked:
            return  # cancelled the picker — stay in the dialog
        self._chosen_path = Path(picked)
        self._outcome = OUTCOME_RELINK
        self.accept()

    def _on_prune_clicked(self) -> None:
        body = tr(
            "This permanently deletes the missing items along with "
            "every decision about them — picks, edits, markers, "
            "snapshots. Mira can't get them back."
        )
        if self._missing_count:
            body = tr(
                "{n} items will be permanently deleted, along with "
                "every decision about them — picks, edits, markers, "
                "snapshots. Mira can't get them back."
            ).format(n=self._missing_count)
        dlg = MessageDialog.destructive(
            tr("Delete missing items?"),
            body,
            parent=self,
            primary_text=tr("Delete"),
            ghost_text=tr("Cancel"),
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if dlg.result_kind() != "primary":
            return
        self._outcome = OUTCOME_PRUNE
        self.accept()
