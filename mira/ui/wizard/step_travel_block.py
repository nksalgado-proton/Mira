"""Travel / General genre block — 4 habit questions.

The fallback / catch-all genre. Defaults reflect "versatile carry-it-
everywhere" setup: mixed focal (zoom range), moderate aperture, single
AF, single drive. See step_macro_block.py for the contract.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from core.wizard import (
    ANSWER_SKIP,
    DRIVE_BURST_LOW,
    DRIVE_MIXED,
    DRIVE_SINGLE,
    FOCAL_MIXED,
    FOCAL_NORMAL,
    FOCAL_SHORT_TELE,
    FOCAL_WIDE,
    TRAVEL_AF_CONTINUOUS,
    TRAVEL_AF_KEY,
    TRAVEL_AF_MIXED,
    TRAVEL_AF_SINGLE,
    TRAVEL_APERTURE_KEY,
    TRAVEL_APERTURE_MIXED,
    TRAVEL_APERTURE_MODERATE,
    TRAVEL_APERTURE_STOPPED,
    TRAVEL_APERTURE_WIDE,
    TRAVEL_DRIVE_KEY,
    TRAVEL_FOCAL_KEY,
)
from mira.ui.i18n import tr


DEFAULT_TRAVEL_ANSWERS: dict[str, str] = {
    TRAVEL_FOCAL_KEY:    FOCAL_MIXED,
    TRAVEL_APERTURE_KEY: TRAVEL_APERTURE_MODERATE,
    TRAVEL_AF_KEY:       TRAVEL_AF_SINGLE,
    TRAVEL_DRIVE_KEY:    DRIVE_SINGLE,
}


_QUESTIONS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        TRAVEL_FOCAL_KEY,
        "What focal length range do you favor on travel days?",
        [
            (FOCAL_WIDE, "Mostly wide (24 – 35mm)"),
            (FOCAL_NORMAL, "Mostly normal (35 – 70mm)"),
            (FOCAL_SHORT_TELE, "Mostly short tele (70 – 200mm)"),
            (FOCAL_MIXED, "Mixed — zoom covering most of these"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        TRAVEL_APERTURE_KEY,
        "What aperture range do you typically use?",
        [
            (TRAVEL_APERTURE_WIDE, "Wider (f/2.8 – f/4) for separation"),
            (TRAVEL_APERTURE_MODERATE, "Moderate (f/4 – f/8) — versatile"),
            (TRAVEL_APERTURE_STOPPED, "Stopped (f/8 – f/11) for DOF"),
            (TRAVEL_APERTURE_MIXED, "Mixed — depends on subject"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        TRAVEL_AF_KEY,
        "What AF mode do you use most often?",
        [
            (TRAVEL_AF_SINGLE, "Single AF (deliberate)"),
            (TRAVEL_AF_CONTINUOUS, "Continuous AF (subjects in motion)"),
            (TRAVEL_AF_MIXED, "Mixed — depends on subject"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        TRAVEL_DRIVE_KEY,
        "What drive mode do you usually shoot in?",
        [
            (DRIVE_SINGLE, "Single shot"),
            (DRIVE_BURST_LOW, "Single with occasional burst"),
            (DRIVE_MIXED, "Mixed — depends on subject"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
]


class StepTravelBlock(QWidget):
    """The Travel / General genre block. See StepMacroBlock for the contract."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._groups: dict[str, QButtonGroup] = {}
        self._radios: dict[str, dict[str, QRadioButton]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 32, 48, 32)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        heading = QLabel(tr("Travel / General"))
        heading.setObjectName("PageHeading")
        layout.addWidget(heading)

        hint = QLabel(tr(
            "Travel is the everyday catch-all — what you set when "
            "you're not in a specific genre. The pre-selected options "
            "describe a versatile default; change what doesn't match "
            "your habits, or pick \"I'm not sure / skip\" to broaden."
        ))
        hint.setObjectName("WizardHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addSpacing(8)

        for question_key, prompt, options in _QUESTIONS:
            self._add_question(layout, question_key, prompt, options)

        for question_key, default_value in DEFAULT_TRAVEL_ANSWERS.items():
            radios = self._radios.get(question_key, {})
            default_radio = radios.get(default_value)
            if default_radio is not None:
                default_radio.setChecked(True)

        layout.addStretch(1)

    def _add_question(
        self,
        layout: QVBoxLayout,
        question_key: str,
        prompt: str,
        options: list[tuple[str, str]],
    ) -> None:
        prompt_label = QLabel(tr(prompt))
        prompt_label.setObjectName("WizardQuestion")
        prompt_label.setWordWrap(True)
        layout.addSpacing(8)
        layout.addWidget(prompt_label)

        group = QButtonGroup(self)
        self._groups[question_key] = group
        self._radios[question_key] = {}

        for value, display in options:
            radio = QRadioButton(tr(display))
            radio.setProperty("answer_value", value)
            group.addButton(radio)
            self._radios[question_key][value] = radio
            layout.addWidget(radio)

    # ── Wizard contract ──────────────────────────────────────────────

    def collect_answers(self) -> dict[str, str]:
        answers: dict[str, str] = {}
        for question_key, radios in self._radios.items():
            chosen = next(
                (value for value, radio in radios.items() if radio.isChecked()),
                None,
            )
            if chosen is not None:
                answers[question_key] = chosen
        return answers

    def is_complete(self) -> bool:
        for radios in self._radios.values():
            if not any(radio.isChecked() for radio in radios.values()):
                return False
        return True

    def restore_answers(self, answers: dict[str, str]) -> None:
        for question_key, radios in self._radios.items():
            saved = answers.get(question_key)
            if saved and saved in radios:
                radios[saved].setChecked(True)
