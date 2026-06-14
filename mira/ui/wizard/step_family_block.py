"""Family genre block — 4 habit questions.

Defaults: normal 35-70 / face detection / moderate aperture /
available light. Differs from Portrait by including continuous-AF as
a distinct option (for kids moving) and a flash question that
distinguishes on-camera vs off-camera.
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
    FAMILY_AF_CONTINUOUS,
    FAMILY_AF_FACE_EYE,
    FAMILY_AF_KEY,
    FAMILY_AF_MIXED,
    FAMILY_AF_SINGLE_POINT,
    FAMILY_APERTURE_KEY,
    FAMILY_APERTURE_MIXED,
    FAMILY_APERTURE_MODERATE,
    FAMILY_APERTURE_SMALLER,
    FAMILY_APERTURE_WIDE,
    FAMILY_FLASH_AVAILABLE,
    FAMILY_FLASH_KEY,
    FAMILY_FLASH_MIXED,
    FAMILY_FLASH_OFF_CAMERA,
    FAMILY_FLASH_ON_CAMERA,
    FAMILY_FOCAL_KEY,
    FOCAL_MIXED,
    FOCAL_NORMAL,
    FOCAL_SHORT_TELE,
    FOCAL_WIDE,
)
from mira.ui.i18n import tr


DEFAULT_FAMILY_ANSWERS: dict[str, str] = {
    FAMILY_FOCAL_KEY:    FOCAL_NORMAL,
    FAMILY_AF_KEY:       FAMILY_AF_FACE_EYE,
    FAMILY_APERTURE_KEY: FAMILY_APERTURE_MODERATE,
    FAMILY_FLASH_KEY:    FAMILY_FLASH_AVAILABLE,
}


_QUESTIONS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        FAMILY_FOCAL_KEY,
        "What focal length range do you favor for family / events?",
        [
            (FOCAL_WIDE, "Wide for group shots (24 – 35mm)"),
            (FOCAL_NORMAL, "Normal (35 – 70mm) — versatile"),
            (FOCAL_SHORT_TELE, "Short tele (70 – 200mm)"),
            (FOCAL_MIXED, "Mixed — varies with situation"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        FAMILY_AF_KEY,
        "How does your camera focus on the people?",
        [
            (FAMILY_AF_FACE_EYE, "Face detection AF"),
            (FAMILY_AF_SINGLE_POINT, "Single-point AF-S"),
            (FAMILY_AF_CONTINUOUS, "Continuous AF (for kids in motion)"),
            (FAMILY_AF_MIXED, "Mixed — depends on the subject"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        FAMILY_APERTURE_KEY,
        "What aperture range do you typically use?",
        [
            (FAMILY_APERTURE_WIDE, "Wide (f/1.8 – f/2.8) for low light"),
            (FAMILY_APERTURE_MODERATE, "Moderate (f/2.8 – f/5.6)"),
            (FAMILY_APERTURE_SMALLER, "Smaller (f/5.6 – f/8) for group shots"),
            (FAMILY_APERTURE_MIXED, "Mixed — depends on the scene"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        FAMILY_FLASH_KEY,
        "Do you use flash for family / event shots?",
        [
            (FAMILY_FLASH_AVAILABLE, "Available light only"),
            (FAMILY_FLASH_ON_CAMERA, "On-camera flash (bounced when possible)"),
            (FAMILY_FLASH_OFF_CAMERA, "Off-camera flash with trigger"),
            (FAMILY_FLASH_MIXED, "Mixed — flash when ambient is too low"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
]


class StepFamilyBlock(QWidget):
    """The Family genre block. See StepMacroBlock for the contract."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._groups: dict[str, QButtonGroup] = {}
        self._radios: dict[str, dict[str, QRadioButton]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 32, 48, 32)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        heading = QLabel(tr("Events"))
        heading.setObjectName("PageHeading")
        layout.addWidget(heading)

        hint = QLabel(tr(
            "The pre-selected options describe Mira's expected "
            "events setup (gatherings, kids, parties). Change whatever "
            "doesn't match your habits, or pick \"I'm not sure / skip\" "
            "to broaden that dimension."
        ))
        hint.setObjectName("WizardHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addSpacing(8)

        for question_key, prompt, options in _QUESTIONS:
            self._add_question(layout, question_key, prompt, options)

        for question_key, default_value in DEFAULT_FAMILY_ANSWERS.items():
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
