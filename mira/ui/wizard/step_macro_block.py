"""Macro genre block — 4 habit questions, each with a Skip option.

Per the wizard-derives-from-prototype-rules constraint: each question
maps to a parameter the macro classification rules read from EXIF.
Lens identity is NOT asked — the prototype rules detect macro lenses
via EXIF substring matching, keeping the wizard hardware-independent.

Mapping (question → rule parameter):
  • Focus approach        → focus_mode
  • Aperture range        → aperture (range gte+lte)
  • Focus stacking usage  → expects_focus_brackets (scenario flag)
  • Flash usage           → flash_fired

Skip ("I'm not sure") is available on every question per docs/04 §2;
a skipped question becomes an absent clause in the resulting scenario
(broader match — the user's preferences haven't narrowed anything yet
in that dimension).
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
    MACRO_APERTURE_KEY,
    MACRO_APERTURE_MIXED,
    MACRO_APERTURE_MODERATE,
    MACRO_APERTURE_STOPPED,
    MACRO_APERTURE_VERY_SMALL,
    MACRO_APERTURE_WIDE,
    MACRO_BRACKETING_ALWAYS,
    MACRO_BRACKETING_KEY,
    MACRO_BRACKETING_NEVER,
    MACRO_BRACKETING_SOMETIMES,
    MACRO_FLASH_KEY,
    MACRO_FLASH_NO,
    MACRO_FLASH_YES,
    MACRO_FOCUS_AF,
    MACRO_FOCUS_KEY,
    MACRO_FOCUS_MANUAL,
    MACRO_FOCUS_MIXED,
)
from mira.ui.i18n import tr


# Default answer per question — pre-selected on the radios so the
# user sees Mira's expected setup for the genre at a glance.
# They can change anything that doesn't match their habits, or pick
# Skip to override to "no constraint, broader scenario."
#
# Per Nelson (2026-05-13): the defaults represent the prototype's
# calibration baseline — the most-common macro habit pattern the
# rules were tuned against. A user accepting all defaults gets a
# scenario that mirrors the reference setup; deviations personalise.
DEFAULT_MACRO_ANSWERS: dict[str, str] = {
    MACRO_FOCUS_KEY:      MACRO_FOCUS_MANUAL,
    MACRO_APERTURE_KEY:   MACRO_APERTURE_STOPPED,
    MACRO_BRACKETING_KEY: MACRO_BRACKETING_SOMETIMES,
    MACRO_FLASH_KEY:      MACRO_FLASH_YES,
}


# Each question is described declaratively below, then rendered by
# a small helper. Adding a question = adding an entry to this list.
# Removing or reordering is a one-line edit.

_QUESTIONS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        MACRO_FOCUS_KEY,
        "How do you focus when shooting macro?",
        [
            (MACRO_FOCUS_MANUAL, "Manual focus (with magnification assist)"),
            (MACRO_FOCUS_AF, "Autofocus (single or continuous)"),
            (MACRO_FOCUS_MIXED, "Mixed — depends on the subject"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        MACRO_APERTURE_KEY,
        "What aperture range do you typically use for macro?",
        [
            (MACRO_APERTURE_WIDE, "Wide (f/2.8 – f/4) for shallow DOF"),
            (MACRO_APERTURE_MODERATE, "Moderate (f/4 – f/8)"),
            (MACRO_APERTURE_STOPPED, "Stopped (f/8 – f/16) for deeper DOF"),
            (MACRO_APERTURE_VERY_SMALL, "Very small (f/16+), accepting diffraction"),
            (MACRO_APERTURE_MIXED, "Mixed — varies with subject"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        MACRO_BRACKETING_KEY,
        "Do you use focus stacking (focus-bracket sequences)?",
        [
            (MACRO_BRACKETING_ALWAYS, "Always — every macro shot is bracketed"),
            (MACRO_BRACKETING_SOMETIMES, "Sometimes — when DOF demands it"),
            (MACRO_BRACKETING_NEVER, "Never — single shots only"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        MACRO_FLASH_KEY,
        "Do you use flash for macro?",
        [
            (MACRO_FLASH_YES, "Yes — macro / ring flash or diffused speedlight"),
            (MACRO_FLASH_NO, "No — available light only"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
]


class StepMacroBlock(QWidget):
    """The Macro genre block — 4 habit questions, all required.

    The host WizardWindow enforces "every question answered" by
    checking that ``collect_answers()`` returns one value per question
    key before advancing.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._groups: dict[str, QButtonGroup] = {}
        self._radios: dict[str, dict[str, QRadioButton]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 32, 48, 32)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        heading = QLabel(tr("Macro"))
        heading.setObjectName("PageHeading")
        layout.addWidget(heading)

        hint = QLabel(tr(
            "The pre-selected options describe Mira's expected "
            "macro setup. Change whatever doesn't match your habits, "
            "or pick \"I'm not sure / skip\" to broaden that dimension."
        ))
        hint.setObjectName("WizardHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addSpacing(8)

        for question_key, prompt, options in _QUESTIONS:
            self._add_question(layout, question_key, prompt, options)

        # Pre-select the default radio for each question. Done after
        # all groups are built so we don't fight Qt's exclusivity rules
        # mid-construction.
        for question_key, default_value in DEFAULT_MACRO_ANSWERS.items():
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
        """Render one question = label + radio group."""
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
        """Return whatever's been picked so far (partial OK).

        This is the persistence-facing API — a mid-wizard cancel
        captures the partial state so a resume continues with it.
        Completeness is a separate check via :meth:`is_complete`,
        which the host uses to decide whether to allow Next.
        """
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
        """True when every macro question has an answer (any value,
        including ``ANSWER_SKIP``). The host calls this before
        advancing past the macro block."""
        for radios in self._radios.values():
            if not any(radio.isChecked() for radio in radios.values()):
                return False
        return True

    def restore_answers(self, answers: dict[str, str]) -> None:
        """Re-select previously saved radios when resuming."""
        for question_key, radios in self._radios.items():
            saved = answers.get(question_key)
            if saved and saved in radios:
                radios[saved].setChecked(True)
