"""Wildlife genre block — 4 habit questions, each with a Skip option.

Mirrors the Macro block structure (see step_macro_block.py) with
Wildlife-specific questions and defaults. Question → rule-parameter
mapping:

  • Focal length range      → focal_35mm range
  • AF / subject lock       → subject_detection / af_area_mode /
                              focus_mode (per option)
  • Drive mode              → drive_mode
  • Shutter speed range     → shutter_speed range

Defaults reflect the most common wildlife setup on a modern body
with subject detection: long tele 200-400mm, animal/bird subject
detection AF-C, burst low, 1/500 – 1/2000s shutter. Users with
older bodies or different habits override per question; Skip on
any question broadens that dimension.
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
    DRIVE_SINGLE,
    FOCAL_LONG_TELE,
    FOCAL_MIXED,
    FOCAL_SHORT_TELE,
    FOCAL_VERY_LONG,
    SHUTTER_FAST,
    SHUTTER_MIXED,
    SHUTTER_MODERATE,
    SHUTTER_VERY_FAST,
    WILDLIFE_AF_KEY,
    WILDLIFE_AF_MANUAL,
    WILDLIFE_AF_MIXED,
    WILDLIFE_AF_SINGLE_POINT,
    WILDLIFE_AF_SUBJECT_DETECT,
    WILDLIFE_AF_TRACKING,
    WILDLIFE_DRIVE_KEY,
    WILDLIFE_FOCAL_KEY,
    WILDLIFE_SHUTTER_KEY,
)
from mira.ui.i18n import tr


DEFAULT_WILDLIFE_ANSWERS: dict[str, str] = {
    WILDLIFE_FOCAL_KEY:   FOCAL_LONG_TELE,
    WILDLIFE_AF_KEY:      WILDLIFE_AF_SUBJECT_DETECT,
    WILDLIFE_DRIVE_KEY:   DRIVE_BURST_LOW,
    WILDLIFE_SHUTTER_KEY: SHUTTER_FAST,
}


_QUESTIONS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        WILDLIFE_FOCAL_KEY,
        "What focal length range do you usually shoot wildlife at?",
        [
            (FOCAL_SHORT_TELE, "Short tele (85 – 200mm)"),
            (FOCAL_LONG_TELE, "Long tele (200 – 400mm)"),
            (FOCAL_VERY_LONG, "Very long (400mm and beyond)"),
            (FOCAL_MIXED, "Mixed — depends on the day"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        WILDLIFE_AF_KEY,
        "How does your camera lock onto wildlife?",
        [
            (WILDLIFE_AF_SUBJECT_DETECT, "Animal / bird subject detection (AF-C)"),
            (WILDLIFE_AF_TRACKING, "Subject tracking (no specific subject mode)"),
            (WILDLIFE_AF_SINGLE_POINT, "Single-point AF-C"),
            (WILDLIFE_AF_MANUAL, "Manual focus"),
            (WILDLIFE_AF_MIXED, "Mixed — depends on the subject"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        WILDLIFE_DRIVE_KEY,
        "What drive mode do you favor?",
        [
            (DRIVE_BURST_HIGH, "Burst high (12fps and up)"),
            (DRIVE_BURST_LOW, "Burst low"),
            (DRIVE_SINGLE, "Single shot"),
            (DRIVE_MIXED, "Mixed — single between bursts"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        WILDLIFE_SHUTTER_KEY,
        "What shutter speed range do you typically target?",
        [
            (SHUTTER_VERY_FAST, "Very fast (1/2000s+) for BIF and fast action"),
            (SHUTTER_FAST, "Fast (1/500s – 1/2000s)"),
            (SHUTTER_MODERATE, "Moderate (1/250s – 1/500s) for static subjects"),
            (SHUTTER_MIXED, "Mixed — varies with subject"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
]


class StepWildlifeBlock(QWidget):
    """The Wildlife genre block. Mirrors StepMacroBlock — see that
    module for the contract description (collect_answers /
    is_complete / restore_answers)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._groups: dict[str, QButtonGroup] = {}
        self._radios: dict[str, dict[str, QRadioButton]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 32, 48, 32)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        heading = QLabel(tr("Wildlife"))
        heading.setObjectName("PageHeading")
        layout.addWidget(heading)

        hint = QLabel(tr(
            "The pre-selected options describe Mira's expected "
            "wildlife setup. Change whatever doesn't match your habits, "
            "or pick \"I'm not sure / skip\" to broaden that dimension."
        ))
        hint.setObjectName("WizardHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addSpacing(8)

        for question_key, prompt, options in _QUESTIONS:
            self._add_question(layout, question_key, prompt, options)

        for question_key, default_value in DEFAULT_WILDLIFE_ANSWERS.items():
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
