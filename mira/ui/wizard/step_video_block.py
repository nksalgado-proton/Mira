"""Video genre block — 4 habit questions.

Defaults: Standard recording mode / 4K 30p / Mixed focal / Travel
B-roll subject. Per docs/04 v1 doesn't try to classify video into
specific scenarios — this block is primarily reference-card content
for the user's typical video setup. See step_macro_block.py for the
contract.
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
    FOCAL_LONG_TELE,
    FOCAL_MIXED,
    FOCAL_NORMAL,
    FOCAL_SHORT_TELE,
    FOCAL_WIDE,
    VIDEO_FOCAL_KEY,
    VIDEO_RECORDING_CINELIKE,
    VIDEO_RECORDING_HLG,
    VIDEO_RECORDING_KEY,
    VIDEO_RECORDING_MIXED,
    VIDEO_RECORDING_PHOTO_STYLE,
    VIDEO_RECORDING_STANDARD,
    VIDEO_RECORDING_V_LOG,
    VIDEO_RESOLUTION_4K_24,
    VIDEO_RESOLUTION_4K_30,
    VIDEO_RESOLUTION_4K_60,
    VIDEO_RESOLUTION_FHD_30,
    VIDEO_RESOLUTION_FHD_60,
    VIDEO_RESOLUTION_KEY,
    VIDEO_RESOLUTION_MIXED,
    VIDEO_SUBJECT_FAMILY,
    VIDEO_SUBJECT_KEY,
    VIDEO_SUBJECT_MACRO_BEHAVIOR,
    VIDEO_SUBJECT_MIXED,
    VIDEO_SUBJECT_OTHER,
    VIDEO_SUBJECT_TRAVEL_BROLL,
    VIDEO_SUBJECT_WILDLIFE_BEHAVIOR,
)
from mira.ui.i18n import tr


DEFAULT_VIDEO_ANSWERS: dict[str, str] = {
    VIDEO_RECORDING_KEY:  VIDEO_RECORDING_STANDARD,
    VIDEO_RESOLUTION_KEY: VIDEO_RESOLUTION_4K_30,
    VIDEO_FOCAL_KEY:      FOCAL_MIXED,
    VIDEO_SUBJECT_KEY:    VIDEO_SUBJECT_TRAVEL_BROLL,
}


_QUESTIONS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        VIDEO_RECORDING_KEY,
        "What recording mode do you typically use?",
        [
            (VIDEO_RECORDING_STANDARD, "Standard (in-camera basic)"),
            (VIDEO_RECORDING_PHOTO_STYLE, "Photo Style passthrough"),
            (VIDEO_RECORDING_CINELIKE, "Cinelike (D / V)"),
            (VIDEO_RECORDING_V_LOG, "V-Log L / S-Log (graded in post)"),
            (VIDEO_RECORDING_HLG, "HLG (Hybrid Log Gamma)"),
            (VIDEO_RECORDING_MIXED, "Mixed — depends on the project"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        VIDEO_RESOLUTION_KEY,
        "What resolution and frame rate?",
        [
            (VIDEO_RESOLUTION_4K_30, "4K at 30p"),
            (VIDEO_RESOLUTION_4K_60, "4K at 60p"),
            (VIDEO_RESOLUTION_4K_24, "4K at 24p (cinematic)"),
            (VIDEO_RESOLUTION_FHD_60, "FHD at 60p"),
            (VIDEO_RESOLUTION_FHD_30, "FHD at 30p"),
            (VIDEO_RESOLUTION_MIXED, "Mixed — depends on the project"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        VIDEO_FOCAL_KEY,
        "What focal length range do you favor for video?",
        [
            (FOCAL_WIDE, "Wide (24 – 35mm)"),
            (FOCAL_NORMAL, "Normal (35 – 70mm)"),
            (FOCAL_SHORT_TELE, "Short tele (70 – 200mm)"),
            (FOCAL_LONG_TELE, "Long tele (200mm and beyond)"),
            (FOCAL_MIXED, "Mixed — depends on the subject"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
    (
        VIDEO_SUBJECT_KEY,
        "What's the main subject of most of your clips?",
        [
            (VIDEO_SUBJECT_WILDLIFE_BEHAVIOR, "Wildlife behavior"),
            (VIDEO_SUBJECT_TRAVEL_BROLL, "Travel B-roll"),
            (VIDEO_SUBJECT_FAMILY, "Family events / kids"),
            (VIDEO_SUBJECT_MACRO_BEHAVIOR, "Macro behavior (insects, water drops)"),
            (VIDEO_SUBJECT_OTHER, "Other recurring subject"),
            (VIDEO_SUBJECT_MIXED, "Mixed — varies"),
            (ANSWER_SKIP, "I'm not sure / skip"),
        ],
    ),
]


class StepVideoBlock(QWidget):
    """The Video genre block. See StepMacroBlock for the contract."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._groups: dict[str, QButtonGroup] = {}
        self._radios: dict[str, dict[str, QRadioButton]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 32, 48, 32)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        heading = QLabel(tr("Video"))
        heading.setObjectName("PageHeading")
        layout.addWidget(heading)

        hint = QLabel(tr(
            "Video uses different EXIF fields than stills — frame rate, "
            "codec, picture profile. In v1 your clips bucket as 'video' "
            "and trim externally; the questions here mainly populate "
            "your reference card."
        ))
        hint.setObjectName("WizardHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addSpacing(8)

        for question_key, prompt, options in _QUESTIONS:
            self._add_question(layout, question_key, prompt, options)

        for question_key, default_value in DEFAULT_VIDEO_ANSWERS.items():
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
