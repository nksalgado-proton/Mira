"""Sports genre block — 4 habit questions, each with a Skip option.

Same axes as Wildlife with human/vehicle subject detection
replacing animal/bird, and defaults tuned for court/field action:
short-tele 70-200mm, human subject detection, burst high, very
fast shutter. See step_macro_block.py for the contract.
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
    DRIVE_BURST_HIGH,
    DRIVE_BURST_LOW,
    DRIVE_MIXED,
    FOCAL_LONG_TELE,
    FOCAL_MIXED,
    FOCAL_NORMAL,
    FOCAL_SHORT_TELE,
    FOCAL_VERY_LONG,
    SHUTTER_FAST,
    SHUTTER_MIXED,
    SHUTTER_MODERATE,
    SHUTTER_VERY_FAST,
    SPORTS_AF_HUMAN_DETECT,
    SPORTS_AF_KEY,
    SPORTS_AF_MIXED,
    SPORTS_AF_SINGLE_POINT,
    SPORTS_AF_TRACKING,
    SPORTS_AF_VEHICLE_DETECT,
    SPORTS_DRIVE_KEY,
    SPORTS_FOCAL_KEY,
    SPORTS_SHUTTER_KEY,
)
from mira.ui.i18n import tr


DEFAULT_SPORTS_ANSWERS: dict[str, str] = {
    SPORTS_FOCAL_KEY:   FOCAL_SHORT_TELE,
    SPORTS_AF_KEY:      SPORTS_AF_HUMAN_DETECT,
    SPORTS_DRIVE_KEY:   DRIVE_BURST_HIGH,
    SPORTS_SHUTTER_KEY: SHUTTER_VERY_FAST,
}


_QUESTIONS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        SPORTS_FOCAL_KEY,
        "What focal length range do you usually shoot sports at?",
        [
            (FOCAL_NORMAL, "Normal (35 – 70mm) for close-quarter / indoor"),
            (FOCAL_SHORT_TELE, "Short tele (70 – 200mm)"),
            (FOCAL_LONG_TELE, "Long tele (200 – 400mm)"),
            (FOCAL_VERY_LONG, "Very long (400mm and beyond)"),
            (FOCAL_MIXED, "Mixed — depends on the sport"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        SPORTS_AF_KEY,
        "How does your camera lock onto the action?",
        [
            (SPORTS_AF_HUMAN_DETECT, "Human subject detection (AF-C)"),
            (SPORTS_AF_VEHICLE_DETECT, "Vehicle subject detection (motorsport)"),
            (SPORTS_AF_TRACKING, "Subject tracking (no specific subject mode)"),
            (SPORTS_AF_SINGLE_POINT, "Single-point AF-C"),
            (SPORTS_AF_MIXED, "Mixed — depends on the situation"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        SPORTS_DRIVE_KEY,
        "What drive mode do you favor?",
        [
            (DRIVE_BURST_HIGH, "Burst high (12fps and up)"),
            (DRIVE_BURST_LOW, "Burst low"),
            (DRIVE_MIXED, "Mixed — single between bursts"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        SPORTS_SHUTTER_KEY,
        "What shutter speed range do you typically target?",
        [
            (SHUTTER_VERY_FAST, "Very fast (1/2000s+) to freeze action"),
            (SHUTTER_FAST, "Fast (1/500s – 1/2000s)"),
            (SHUTTER_MODERATE, "Moderate (1/250s – 1/500s) for panning"),
            (SHUTTER_MIXED, "Mixed — varies with sport"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
]


class StepSportsBlock(QWidget):
    """The Sports genre block. See StepMacroBlock for the contract."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._groups: dict[str, QButtonGroup] = {}
        self._radios: dict[str, dict[str, QRadioButton]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 32, 48, 32)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        heading = QLabel(tr("Sports"))
        heading.setObjectName("PageHeading")
        layout.addWidget(heading)

        hint = QLabel(tr(
            "The pre-selected options describe Mira's expected "
            "sports setup. Change whatever doesn't match your habits, "
            "or pick \"I'm not sure / skip\" to broaden that dimension."
        ))
        hint.setObjectName("WizardHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addSpacing(8)

        for question_key, prompt, options in _QUESTIONS:
            self._add_question(layout, question_key, prompt, options)

        for question_key, default_value in DEFAULT_SPORTS_ANSWERS.items():
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
