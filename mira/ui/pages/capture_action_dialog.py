"""Capture-action dialog — the two-ingest-modes gate (spec/13 §3).

PORTED verbatim from legacy ``ui/pages/capture_action_dialog.py`` (charter §0/§5.2 —
pure UI, no data tendril; only the ``tr`` import is rebased to ``mira.ui``).

The user clicks the Capture phase button. Before anything else
happens, this dialog asks how they want to ingest the source:

  * **Copy entire contents (Mode A)** — verbatim mirror. Every file
    on the source lands in ``00 - Captured/``. The regular Cull
    phase later walks them at the user's pace. Right for short
    events where storage is trivial and reversibility matters.
  * **Cull photos before copying (Mode B)** — runs the standalone
    cull surface on the source first; only files the user keeps
    move into ``00 - Captured/``. Right for burst-heavy trips
    where most shots are obviously unkeepable. Discards are
    *permanent* once the SD card is wiped.

The dialog is pure UI — no I/O, no engine calls. The caller
(:func:`ui.main_window.MainWindow._on_capture_phase`) reads
:meth:`choice` after :meth:`exec` returns to decide what to do
next. The orchestration script does the actual work — see
docs/18 §"Two ingest modes — design freeze".
"""

from __future__ import annotations

import logging
from enum import Enum, auto
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mira.ui.i18n import tr


log = logging.getLogger(__name__)


class CaptureMode(Enum):
    """The three possible outcomes of the dialog."""
    COPY_ALL = auto()
    CULL_THEN_COPY = auto()
    CANCEL = auto()


class CaptureActionDialog(QDialog):
    """Modal that asks the user which capture mode to use.

    Usage::

        dlg = CaptureActionDialog(parent=self)
        dlg.exec()
        if dlg.choice() is CaptureMode.COPY_ALL:
            ...
        elif dlg.choice() is CaptureMode.CULL_THEN_COPY:
            ...
        else:  # CANCEL
            return
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._choice: CaptureMode = CaptureMode.CANCEL
        self.setWindowTitle(tr("Capture — how do you want to ingest?"))
        self.setModal(True)
        self.setMinimumWidth(560)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(14)

        heading = QLabel(tr("How do you want to handle this source?"))
        heading.setObjectName("PageHeading")
        outer.addWidget(heading)

        hint = QLabel(tr(
            "Pick once per ingest. Both options copy the kept files into "
            "the event's 00 - Captured/ folder; the difference is whether "
            "you cull first or copy everything."
        ))
        hint.setObjectName("PageHint")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        # ── Big choice buttons ─────────────────────────────────────
        # Each button is a title in bold + a longer description below.
        # Stacked one above the other (Nelson 2026-05-31 — better dialog
        # shape than the side-by-side layout).

        choices_row = QVBoxLayout()
        choices_row.setSpacing(12)

        self._copy_button = self._make_choice_button(
            tr("Copy Entire Contents"),
            tr(
                "Mirror everything from the source into the event tree. "
                "Use the regular Cull phase later, at your own pace, with "
                "the safety net of a full local copy."
            ),
        )
        self._copy_button.clicked.connect(self._on_copy_all)
        choices_row.addWidget(self._copy_button)

        self._cull_button = self._make_choice_button(
            tr("Pick Before Copying"),
            tr(
                "Quickly mark obvious garbage to discard now. Only the "
                "files you keep land in the event tree. Discards are "
                "permanent once the SD card is wiped. Right for "
                "burst-heavy trips where most shots are obviously bad."
            ),
        )
        self._cull_button.clicked.connect(self._on_cull_then_copy)
        choices_row.addWidget(self._cull_button)

        outer.addLayout(choices_row)

        # ── Cancel row ─────────────────────────────────────────────
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        cancel_btn = button_box.button(QDialogButtonBox.StandardButton.Cancel)
        cancel_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        button_box.rejected.connect(self._on_cancel)
        outer.addWidget(button_box)

    # ── Public API ────────────────────────────────────────────────

    def choice(self) -> CaptureMode:
        """The user's selection. ``CANCEL`` if the dialog was
        dismissed (Esc, X, Cancel button)."""
        return self._choice

    # ── Internals ─────────────────────────────────────────────────

    @staticmethod
    def _make_choice_button(title: str, body: str) -> QPushButton:
        """A clean short-label button (Nelson 2026-05-31 — the long description was too
        much on the button face). The description becomes the tooltip (spec/05 — a hint on
        every interactive widget), so the guidance is still one hover away."""
        btn = QPushButton(title)
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn.setToolTip(body)
        btn.setMinimumHeight(48)
        return btn

    def _on_copy_all(self) -> None:
        self._choice = CaptureMode.COPY_ALL
        log.info("CaptureActionDialog: user chose COPY_ALL")
        self.accept()

    def _on_cull_then_copy(self) -> None:
        self._choice = CaptureMode.CULL_THEN_COPY
        log.info("CaptureActionDialog: user chose CULL_THEN_COPY")
        self.accept()

    def _on_cancel(self) -> None:
        self._choice = CaptureMode.CANCEL
        log.info("CaptureActionDialog: user cancelled")
        self.reject()
