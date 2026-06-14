"""Astro / Night genre block — 4 habit questions.

Defaults: Milky Way / Ultra-wide / Wide-open aperture / Very long
shutter. Sub-type drives reference-card content and a tag but no
EXIF clause directly (the aperture/shutter/focal answers do that).
See step_macro_block.py for the contract.
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
    ASTRO_APERTURE_KEY,
    ASTRO_APERTURE_MIXED,
    ASTRO_APERTURE_MODERATE,
    ASTRO_APERTURE_STOPPED,
    ASTRO_APERTURE_WIDE_OPEN,
    ASTRO_FOCAL_KEY,
    ASTRO_SHUTTER_FAST,
    ASTRO_SHUTTER_KEY,
    ASTRO_SHUTTER_LONG,
    ASTRO_SHUTTER_MIXED,
    ASTRO_SHUTTER_MODERATE,
    ASTRO_SHUTTER_VERY_LONG,
    ASTRO_SUBTYPE_KEY,
    ASTRO_SUBTYPE_MILKY_WAY,
    ASTRO_SUBTYPE_MIXED,
    ASTRO_SUBTYPE_MOON,
    ASTRO_SUBTYPE_STAR_TRAILS,
    ASTRO_SUBTYPE_URBAN_NIGHT,
    FOCAL_LONG_TELE,
    FOCAL_MIXED,
    FOCAL_NORMAL,
    FOCAL_ULTRA_WIDE,
    FOCAL_WIDE,
)
from mira.ui.i18n import tr


DEFAULT_ASTRO_ANSWERS: dict[str, str] = {
    ASTRO_SUBTYPE_KEY:  ASTRO_SUBTYPE_MILKY_WAY,
    ASTRO_FOCAL_KEY:    FOCAL_ULTRA_WIDE,
    ASTRO_APERTURE_KEY: ASTRO_APERTURE_WIDE_OPEN,
    ASTRO_SHUTTER_KEY:  ASTRO_SHUTTER_VERY_LONG,
}


_QUESTIONS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        ASTRO_SUBTYPE_KEY,
        "Which astro / night subject do you shoot most?",
        [
            (ASTRO_SUBTYPE_MILKY_WAY, "Milky Way / starry skies"),
            (ASTRO_SUBTYPE_MOON, "Moon"),
            (ASTRO_SUBTYPE_URBAN_NIGHT, "Urban night / cityscapes"),
            (ASTRO_SUBTYPE_STAR_TRAILS, "Star trails (stacked exposures)"),
            (ASTRO_SUBTYPE_MIXED, "Mixed — depends on the night"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        ASTRO_FOCAL_KEY,
        "What focal length do you usually shoot at?",
        [
            (FOCAL_ULTRA_WIDE, "Ultra-wide (under 24mm) for sky coverage"),
            (FOCAL_WIDE, "Wide (24 – 35mm)"),
            (FOCAL_NORMAL, "Normal (35 – 70mm) for urban night"),
            (FOCAL_LONG_TELE, "Long tele (200mm+) for the moon"),
            (FOCAL_MIXED, "Mixed — depends on the subject"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        ASTRO_APERTURE_KEY,
        "What aperture do you typically use?",
        [
            (ASTRO_APERTURE_WIDE_OPEN, "Wide open (f/1.4 – f/2.8) for Milky Way"),
            (ASTRO_APERTURE_MODERATE, "Moderate (f/4 – f/5.6) for urban night"),
            (ASTRO_APERTURE_STOPPED, "Stopped (f/8 – f/11) for moon detail"),
            (ASTRO_APERTURE_MIXED, "Mixed — varies with subject"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        ASTRO_SHUTTER_KEY,
        "What shutter speed do you typically use?",
        [
            (ASTRO_SHUTTER_VERY_LONG, "Very long (10 – 30s) for Milky Way"),
            (ASTRO_SHUTTER_LONG, "Long (1 – 10s) for cityscapes / light trails"),
            (ASTRO_SHUTTER_MODERATE, "Moderate (1/30 – 1s) for handheld dusk"),
            (ASTRO_SHUTTER_FAST, "Fast (1/250s+) for moon"),
            (ASTRO_SHUTTER_MIXED, "Mixed — varies with subject"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
]


class StepAstroBlock(QWidget):
    """The Astro / Night genre block. See StepMacroBlock for the contract."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._groups: dict[str, QButtonGroup] = {}
        self._radios: dict[str, dict[str, QRadioButton]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 32, 48, 32)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        heading = QLabel(tr("Astro / Night"))
        heading.setObjectName("PageHeading")
        layout.addWidget(heading)

        hint = QLabel(tr(
            "The pre-selected options describe the Milky Way setup "
            "(the most common astro starting point). Change whatever "
            "doesn't match your habits — moon work, urban night, and "
            "star trails each need different settings."
        ))
        hint.setObjectName("WizardHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addSpacing(8)

        for question_key, prompt, options in _QUESTIONS:
            self._add_question(layout, question_key, prompt, options)

        for question_key, default_value in DEFAULT_ASTRO_ANSWERS.items():
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
