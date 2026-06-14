"""Welcome step — first page of the first-run wizard."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from mira.ui.i18n import tr


class StepWelcome(QWidget):
    """Welcome screen. No user input — just text + a Next button
    (provided by the host WizardWindow)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 48, 48, 48)
        layout.setSpacing(20)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel(tr("Welcome to Mira"))
        title.setObjectName("WelcomeTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        body = QLabel(tr(
            "This setup wizard learns how you shoot — genre by genre, "
            "in EXIF-grounded terms — and generates the rules Mira "
            "uses to organize your photos.\n\n"
            "Everything stays on your machine. The app never reaches the "
            "internet.\n\n"
            "Click Next to begin."
        ))
        body.setObjectName("WelcomeSubtitle")
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setWordWrap(True)
        body.setMinimumWidth(480)
        layout.addWidget(body)

    # The wizard infrastructure asks every step for any answers it
    # captured before advancing. Welcome captures none.

    def collect_answers(self) -> dict[str, str]:
        return {}

    def restore_answers(self, answers: dict[str, str]) -> None:
        return  # nothing to restore
