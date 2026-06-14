"""Portrait genre block — 4 habit questions.

Defaults: short tele 70-200 / eye-face detection / very wide aperture
/ natural light. See step_macro_block.py for the contract.
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
    FOCAL_MIXED,
    FOCAL_NORMAL,
    FOCAL_SHORT_TELE,
    FOCAL_WIDE,
    PORTRAIT_AF_FACE_EYE,
    PORTRAIT_AF_KEY,
    PORTRAIT_AF_MANUAL,
    PORTRAIT_AF_MIXED,
    PORTRAIT_AF_SINGLE_POINT,
    PORTRAIT_APERTURE_KEY,
    PORTRAIT_APERTURE_MIXED,
    PORTRAIT_APERTURE_MODERATE,
    PORTRAIT_APERTURE_STOPPED,
    PORTRAIT_APERTURE_VERY_WIDE,
    PORTRAIT_FOCAL_KEY,
    PORTRAIT_LIGHTING_KEY,
    PORTRAIT_LIGHTING_MIXED,
    PORTRAIT_LIGHTING_NATURAL,
    PORTRAIT_LIGHTING_SPEEDLIGHT,
    PORTRAIT_LIGHTING_STROBE,
)
from mira.ui.i18n import tr


DEFAULT_PORTRAIT_ANSWERS: dict[str, str] = {
    PORTRAIT_FOCAL_KEY:    FOCAL_SHORT_TELE,
    PORTRAIT_AF_KEY:       PORTRAIT_AF_FACE_EYE,
    PORTRAIT_APERTURE_KEY: PORTRAIT_APERTURE_VERY_WIDE,
    PORTRAIT_LIGHTING_KEY: PORTRAIT_LIGHTING_NATURAL,
}


_QUESTIONS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        PORTRAIT_FOCAL_KEY,
        "What focal length range do you favor for portraits?",
        [
            (FOCAL_WIDE, "Wide environmental (24 – 35mm)"),
            (FOCAL_NORMAL, "Normal (35 – 70mm)"),
            (FOCAL_SHORT_TELE, "Short tele (70 – 200mm) — classic portrait"),
            (FOCAL_MIXED, "Mixed — depends on the subject"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        PORTRAIT_AF_KEY,
        "How do you focus on the subject?",
        [
            (PORTRAIT_AF_FACE_EYE, "Eye / face detection AF"),
            (PORTRAIT_AF_SINGLE_POINT, "Single-point AF-S on the eye"),
            (PORTRAIT_AF_MANUAL, "Manual focus"),
            (PORTRAIT_AF_MIXED, "Mixed — depends on the situation"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        PORTRAIT_APERTURE_KEY,
        "What aperture range do you typically use?",
        [
            (PORTRAIT_APERTURE_VERY_WIDE, "Very wide (f/1.4 – f/2.8) for separation"),
            (PORTRAIT_APERTURE_MODERATE, "Moderate (f/2.8 – f/5.6)"),
            (PORTRAIT_APERTURE_STOPPED, "Stopped (f/5.6 – f/11) for groups"),
            (PORTRAIT_APERTURE_MIXED, "Mixed — depends on the scene"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        PORTRAIT_LIGHTING_KEY,
        "What lighting context do you usually shoot in?",
        [
            (PORTRAIT_LIGHTING_NATURAL, "Natural light"),
            (PORTRAIT_LIGHTING_SPEEDLIGHT, "Speedlight (on- or off-camera)"),
            (PORTRAIT_LIGHTING_STROBE, "Studio strobe"),
            (PORTRAIT_LIGHTING_MIXED, "Mixed — natural with fill flash"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
]


class StepPortraitBlock(QWidget):
    """The Portrait genre block. See StepMacroBlock for the contract."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._groups: dict[str, QButtonGroup] = {}
        self._radios: dict[str, dict[str, QRadioButton]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 32, 48, 32)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        heading = QLabel(tr("Portrait"))
        heading.setObjectName("PageHeading")
        layout.addWidget(heading)

        hint = QLabel(tr(
            "The pre-selected options describe Mira's expected "
            "portrait setup. Change whatever doesn't match your habits, "
            "or pick \"I'm not sure / skip\" to broaden that dimension."
        ))
        hint.setObjectName("WizardHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addSpacing(8)

        for question_key, prompt, options in _QUESTIONS:
            self._add_question(layout, question_key, prompt, options)

        for question_key, default_value in DEFAULT_PORTRAIT_ANSWERS.items():
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
