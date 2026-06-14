"""Street / Documentary genre block — 4 habit questions.

Defaults: wide 24-35 / single-point AF / moderate aperture / standard
color. See step_macro_block.py for the contract.
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
    STREET_AF_KEY,
    STREET_AF_MANUAL_HYPERFOCAL,
    STREET_AF_MIXED,
    STREET_AF_SINGLE,
    STREET_AF_ZONE,
    STREET_APERTURE_KEY,
    STREET_APERTURE_MIXED,
    STREET_APERTURE_MODERATE,
    STREET_APERTURE_STOPPED,
    STREET_APERTURE_WIDE,
    STREET_COLOR_CUSTOM,
    STREET_COLOR_KEY,
    STREET_COLOR_MIXED,
    STREET_COLOR_MONOCHROME,
    STREET_COLOR_STANDARD,
    STREET_COLOR_VIVID,
    STREET_FOCAL_KEY,
)
from mira.ui.i18n import tr


DEFAULT_STREET_ANSWERS: dict[str, str] = {
    STREET_FOCAL_KEY:    FOCAL_WIDE,
    STREET_AF_KEY:       STREET_AF_SINGLE,
    STREET_APERTURE_KEY: STREET_APERTURE_MODERATE,
    STREET_COLOR_KEY:    STREET_COLOR_STANDARD,
}


_QUESTIONS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        STREET_FOCAL_KEY,
        "What focal length range do you favor for street?",
        [
            (FOCAL_WIDE, "Wide (24 – 35mm)"),
            (FOCAL_NORMAL, "Normal (35 – 70mm)"),
            (FOCAL_SHORT_TELE, "Short tele (70 – 200mm)"),
            (FOCAL_MIXED, "Mixed — depends on the city"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        STREET_AF_KEY,
        "How do you focus on street subjects?",
        [
            (STREET_AF_SINGLE, "Single-point AF-S, subject by subject"),
            (STREET_AF_ZONE, "Zone focus pre-set"),
            (STREET_AF_MANUAL_HYPERFOCAL, "Manual hyperfocal distance"),
            (STREET_AF_MIXED, "Mixed — depends on the scene"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        STREET_APERTURE_KEY,
        "What aperture range do you typically use?",
        [
            (STREET_APERTURE_WIDE, "Wide (f/1.4 – f/2.8) for low light / separation"),
            (STREET_APERTURE_MODERATE, "Moderate (f/2.8 – f/5.6)"),
            (STREET_APERTURE_STOPPED, "Stopped (f/5.6 – f/11) for deep DOF"),
            (STREET_APERTURE_MIXED, "Mixed — depends on the light"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        STREET_COLOR_KEY,
        "How do you render street images?",
        [
            (STREET_COLOR_STANDARD, "Standard color"),
            (STREET_COLOR_MONOCHROME, "Monochrome (Acros / Eterna / B&W)"),
            (STREET_COLOR_VIVID, "Vivid color"),
            (STREET_COLOR_CUSTOM, "Custom preset I dialed in"),
            (STREET_COLOR_MIXED, "Mixed — varies with mood"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
]


class StepStreetBlock(QWidget):
    """The Street / Documentary genre block. See StepMacroBlock for the contract."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._groups: dict[str, QButtonGroup] = {}
        self._radios: dict[str, dict[str, QRadioButton]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 32, 48, 32)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        heading = QLabel(tr("Street / Documentary"))
        heading.setObjectName("PageHeading")
        layout.addWidget(heading)

        hint = QLabel(tr(
            "The pre-selected options describe Mira's expected "
            "street / documentary setup. Change whatever doesn't match "
            "your habits, or pick \"I'm not sure / skip\" to broaden "
            "that dimension."
        ))
        hint.setObjectName("WizardHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addSpacing(8)

        for question_key, prompt, options in _QUESTIONS:
            self._add_question(layout, question_key, prompt, options)

        for question_key, default_value in DEFAULT_STREET_ANSWERS.items():
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
