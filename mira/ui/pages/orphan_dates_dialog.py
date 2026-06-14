"""Capture-time plan-disk consistency dialog (task #109,
Nelson 2026-05-23).

Shown by the Capture surfaces (past-photos ingest, card offload —
follow-up) before any files land in ``Original Media/``. Surfaces
the dates on the source whose timestamps fall on days not in the
event's current plan, and asks the user what to do.

Three outcomes:

* :attr:`Result.ADD_TO_PLAN` — the user wants the orphan dates
  added to the plan so the photos land in their natural days. The
  caller stretches the plan and proceeds.
* :attr:`Result.SKIP_FILES` — the user wants those photos left at
  the source. Source files are never touched; the Capture
  proceeds with only the in-plan files.
* :attr:`Result.CANCEL` — the user wants to bail out entirely. No
  files copied.

Default button is **Add to plan** (most users meant to capture
everything; the dialog is really an "are you sure?" gate against
silently dropping photos).
"""

from __future__ import annotations

from datetime import date as date_cls
from enum import Enum
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mira.ui.i18n import tr


class Result(Enum):
    """User's choice from the dialog."""

    ADD_TO_PLAN = "add"
    SKIP_FILES = "skip"
    CANCEL = "cancel"


class OrphanDatesDialog(QDialog):
    """Modal that lists orphan capture dates + counts and lets the
    user pick how to handle them.

    Construct with::

        dlg = OrphanDatesDialog(
            orphans=[(date(2025,11,12), 247), (date(2025,11,13), 89)],
            event_name="2025 - Nepal",
            parent=window,
        )
        dlg.exec()
        choice = dlg.result_choice
    """

    def __init__(
        self,
        orphans: list[tuple[date_cls, int]],
        event_name: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._orphans = list(orphans)
        self._result_choice: Result = Result.CANCEL
        self.setWindowTitle(tr("Photos outside your plan"))
        self.setModal(True)
        self.setMinimumWidth(560)
        self._build_ui(event_name)

    def _build_ui(self, event_name: str) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)

        heading = QLabel(tr("Photos outside your plan"))
        heading.setObjectName("PageHeading")
        outer.addWidget(heading)

        total = sum(count for _, count in self._orphans)
        intro_text = tr(
            "Some photos on the source fall on dates that aren't "
            "in <b>{name}</b>'s current plan:"
        ).replace("{name}", event_name or tr("this event"))
        intro = QLabel(intro_text)
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setWordWrap(True)
        outer.addWidget(intro)

        # The orphan-date table.
        table = QTableWidget(len(self._orphans), 2, self)
        table.setHorizontalHeaderLabels([
            tr("Date"), tr("Photo(s)"),
        ])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(
            QTableWidget.SelectionMode.NoSelection)
        # All columns user-draggable; last stretches (app-wide standard, spec/05 §4b).
        from mira.ui.base.tables import make_columns_resizable
        make_columns_resizable(table, widths=(220,))
        for r, (d, count) in enumerate(self._orphans):
            d_item = QTableWidgetItem(d.isoformat())
            table.setItem(r, 0, d_item)
            c_item = QTableWidgetItem(str(count))
            c_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(r, 1, c_item)
        outer.addWidget(table, stretch=1)

        explainer = QLabel(tr(
            "Total: <b>{n}</b> photo(s) across <b>{d}</b> day(s).<br><br>"
            "Pick one of:<br>"
            "<ul>"
            "<li><b>Add these as new plan days</b> — extend the plan "
            "with these dates (placeholder descriptions you can "
            "fill in afterwards) and capture every photo.</li>"
            "<li><b>Skip these photos</b> — capture only the photos "
            "whose dates are already in the plan. The skipped "
            "photos stay on the source untouched.</li>"
            "<li><b>Cancel Capture</b> — bail out, no photos "
            "copied. Re-open the plan editor, adjust, and try "
            "Capture again.</li>"
            "</ul>"
        ).replace("{n}", str(total))
         .replace("{d}", str(len(self._orphans))))
        explainer.setObjectName("PageHint")
        explainer.setTextFormat(Qt.TextFormat.RichText)
        explainer.setWordWrap(True)
        outer.addWidget(explainer)

        # Buttons. AcceptRole = Add (default), DestructiveRole =
        # Skip, RejectRole = Cancel. Default button (Enter key) is
        # Add since it's the "capture everything you have" path.
        self._buttons = QDialogButtonBox(parent=self)
        add_btn = QPushButton(tr("Add these as new plan days"))
        add_btn.setDefault(True)
        add_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        add_btn.clicked.connect(self._on_add)
        self._buttons.addButton(
            add_btn, QDialogButtonBox.ButtonRole.AcceptRole,
        )

        skip_btn = QPushButton(tr("Skip these photos"))
        skip_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        skip_btn.clicked.connect(self._on_skip)
        self._buttons.addButton(
            skip_btn, QDialogButtonBox.ButtonRole.DestructiveRole,
        )

        cancel_btn = QPushButton(tr("Cancel Capture"))
        cancel_btn.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        cancel_btn.clicked.connect(self._on_cancel)
        self._buttons.addButton(
            cancel_btn, QDialogButtonBox.ButtonRole.RejectRole,
        )
        outer.addWidget(self._buttons)

    # ── Action handlers ─────────────────────────────────────────

    def _on_add(self) -> None:
        self._result_choice = Result.ADD_TO_PLAN
        self.accept()

    def _on_skip(self) -> None:
        self._result_choice = Result.SKIP_FILES
        self.accept()

    def _on_cancel(self) -> None:
        self._result_choice = Result.CANCEL
        self.reject()

    # ── Read API ────────────────────────────────────────────────

    @property
    def result_choice(self) -> Result:
        return self._result_choice
