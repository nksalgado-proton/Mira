"""Landscape genre block — 4 habit questions.

Defaults: Wide (24-35mm) / Standard aperture (f/5.6-f/11) /
Long-exposure occasional / Single-point AF-S. Mirrors the established
block pattern (see step_macro_block.py for the contract description).
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
    FOCAL_ULTRA_WIDE,
    FOCAL_WIDE,
    LANDSCAPE_AF_KEY,
    LANDSCAPE_AF_MANUAL_HYPERFOCAL,
    LANDSCAPE_AF_MIXED,
    LANDSCAPE_AF_SINGLE_POINT,
    LANDSCAPE_APERTURE_KEY,
    LANDSCAPE_APERTURE_MIXED,
    LANDSCAPE_APERTURE_STANDARD,
    LANDSCAPE_APERTURE_STOPPED,
    LANDSCAPE_APERTURE_WIDER,
    LANDSCAPE_FOCAL_KEY,
    LANDSCAPE_LONG_EXPOSURE_FREQUENT,
    LANDSCAPE_LONG_EXPOSURE_KEY,
    LANDSCAPE_LONG_EXPOSURE_NEVER,
    LANDSCAPE_LONG_EXPOSURE_OCCASIONAL,
)
from mira.ui.i18n import tr


DEFAULT_LANDSCAPE_ANSWERS: dict[str, str] = {
    LANDSCAPE_FOCAL_KEY:         FOCAL_WIDE,
    LANDSCAPE_APERTURE_KEY:      LANDSCAPE_APERTURE_STANDARD,
    LANDSCAPE_LONG_EXPOSURE_KEY: LANDSCAPE_LONG_EXPOSURE_OCCASIONAL,
    LANDSCAPE_AF_KEY:            LANDSCAPE_AF_SINGLE_POINT,
}


_QUESTIONS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        LANDSCAPE_FOCAL_KEY,
        "What focal length range do you favor for landscapes?",
        [
            (FOCAL_ULTRA_WIDE, "Ultra-wide (under 24mm)"),
            (FOCAL_WIDE, "Wide (24 – 35mm)"),
            (FOCAL_NORMAL, "Normal (35 – 70mm)"),
            (FOCAL_SHORT_TELE, "Short tele (70 – 200mm)"),
            (FOCAL_MIXED, "Mixed — depends on the scene"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        LANDSCAPE_APERTURE_KEY,
        "What aperture range do you usually shoot at?",
        [
            (LANDSCAPE_APERTURE_WIDER, "Wider (f/2.8 – f/5.6) for separation"),
            (LANDSCAPE_APERTURE_STANDARD, "Standard (f/5.6 – f/11) for DOF"),
            (LANDSCAPE_APERTURE_STOPPED, "Stopped (f/11 – f/16) for maximum DOF"),
            (LANDSCAPE_APERTURE_MIXED, "Mixed — depends on the scene"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        LANDSCAPE_LONG_EXPOSURE_KEY,
        "How often do you shoot long exposures (over 1 second)?",
        [
            (LANDSCAPE_LONG_EXPOSURE_FREQUENT, "Frequent — tripod + ND filter regular"),
            (LANDSCAPE_LONG_EXPOSURE_OCCASIONAL, "Occasional — when the scene calls for it"),
            (LANDSCAPE_LONG_EXPOSURE_NEVER, "Never — handheld or tripod-fast only"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        LANDSCAPE_AF_KEY,
        "How do you focus for landscapes?",
        [
            (LANDSCAPE_AF_SINGLE_POINT, "AF-S, single-point on a foreground anchor"),
            (LANDSCAPE_AF_MANUAL_HYPERFOCAL, "Manual focus at hyperfocal distance"),
            (LANDSCAPE_AF_MIXED, "Mixed — depends on the scene"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
]


class StepLandscapeBlock(QWidget):
    """The Landscape genre block. See StepMacroBlock for the contract."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._groups: dict[str, QButtonGroup] = {}
        self._radios: dict[str, dict[str, QRadioButton]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 32, 48, 32)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        heading = QLabel(tr("Landscape"))
        heading.setObjectName("PageHeading")
        layout.addWidget(heading)

        hint = QLabel(tr(
            "The pre-selected options describe Mira's expected "
            "landscape setup. Change whatever doesn't match your habits, "
            "or pick \"I'm not sure / skip\" to broaden that dimension."
        ))
        hint.setObjectName("WizardHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addSpacing(8)

        for question_key, prompt, options in _QUESTIONS:
            self._add_question(layout, question_key, prompt, options)

        for question_key, default_value in DEFAULT_LANDSCAPE_ANSWERS.items():
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
