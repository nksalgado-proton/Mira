"""Capture & Timezones — calibration-mode picker (task #96 / #1d).

Three options the user picks once here (with full education) and
can change later in Settings:

  * **Prompt** (default; safest) — every ingest pops a per-camera
    calibration dialog (pair-picker / CameraClockDialog) that
    derives the timezone offset from a reference photo against a
    known-correct clock (typically a phone).

  * **Saved** — power-user setting. Look up each camera's offset
    from a persisted ``saved_camera_offsets`` table and only prompt
    for cameras never calibrated before. Best after the first trip
    seeds the offsets.

  * **Reference photo** — minimal-UI alternative. At every ingest,
    the user just points at one reference photo (with correct EXIF)
    per camera; the offset is derived against the photo's wall
    clock. Lighter than Prompt for users who carry a reliably-
    clocked phone.

The wizard answer maps 1:1 to ``settings.calibration_mode`` (see
``core.wizard.apply_capture_settings_to_settings``).
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
    CAPTURE_CALIBRATION_KEY,
    CAPTURE_CALIBRATION_PROMPT,
    CAPTURE_CALIBRATION_REFERENCE_PHOTO,
    CAPTURE_CALIBRATION_SAVED,
)
from mira.ui.i18n import tr


# Default = Prompt (matches ``settings.calibration_mode`` default
# and the "safest for first-trip users" guidance in docs/14).
DEFAULT_CAPTURE_CALIBRATION = CAPTURE_CALIBRATION_PROMPT


# (value, label, explanation) — one tuple per radio. Walking a list
# keeps the rendering boring and adding a fourth option (if one
# ever lands) a one-line edit.
_OPTIONS: list[tuple[str, str, str]] = [
    (
        CAPTURE_CALIBRATION_PROMPT,
        "Prompt every ingest (default — safest)",
        "Pops a per-camera calibration dialog at every ingest. You "
        "pick a reference photo (typically from a phone with the "
        "correct local time) and Mira derives the offset from "
        "the pair. Best for first-time users and travel where time "
        "zones change between trips.",
    ),
    (
        CAPTURE_CALIBRATION_SAVED,
        "Use saved per-camera offsets (skip the prompt for known cameras)",
        "After Mira has calibrated a camera once, the offset "
        "is remembered. New ingests for the same camera skip the "
        "calibration step entirely — unknown cameras still prompt. "
        "Recommended after your first trip seeds the saved offsets "
        "(usually 2 - 3 cameras + 1 phone).",
    ),
    (
        CAPTURE_CALIBRATION_REFERENCE_PHOTO,
        "Pick a reference photo each ingest (lighter than Prompt)",
        "Minimal UI: at every ingest, you point at one photo per "
        "camera that has the correct EXIF timestamp; Mira "
        "derives the offset from that photo's wall clock. No pair-"
        "picker. Good middle ground for users who reliably carry a "
        "phone with correct local time.",
    ),
]


class StepCaptureCalibration(QWidget):
    """Single-question picker for the per-ingest calibration mode."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 32, 40, 24)
        layout.setSpacing(14)

        title = QLabel(tr("How should Mira find each camera's timezone?"))
        title.setObjectName("WelcomeTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignLeft)
        title.setWordWrap(True)
        layout.addWidget(title)

        explain = QLabel(tr(
            "Cameras drift off the correct wall clock all the time — "
            "and the wrong timezone puts photos in the wrong day "
            "folder. Pick how Mira should figure out the offset "
            "when you bring photos in. Change this any time in "
            "Settings → Collect."
        ))
        explain.setObjectName("BodyText")
        explain.setWordWrap(True)
        layout.addWidget(explain)

        self._group = QButtonGroup(self)
        self._buttons: dict[str, QRadioButton] = {}
        for value, label, hint in _OPTIONS:
            radio = QRadioButton(tr(label))
            radio.setObjectName("WizardRadio")
            radio.setToolTip(tr(hint))
            self._group.addButton(radio)
            self._buttons[value] = radio
            layout.addWidget(radio)

            hint_lbl = QLabel(tr(hint))
            hint_lbl.setObjectName("WizardRadioHint")
            hint_lbl.setWordWrap(True)
            hint_lbl.setContentsMargins(28, 0, 0, 8)
            layout.addWidget(hint_lbl)

        # Pre-select the default so a user who just hits Next still
        # writes a valid answer.
        self._buttons[DEFAULT_CAPTURE_CALIBRATION].setChecked(True)

        layout.addStretch(1)

    # ── Wizard contract ────────────────────────────────────────────

    def collect_answers(self) -> dict[str, str]:
        for value, radio in self._buttons.items():
            if radio.isChecked():
                return {CAPTURE_CALIBRATION_KEY: value}
        # Defensive — Qt shouldn't let zero radios be checked once
        # one was pre-selected, but the default still applies.
        return {CAPTURE_CALIBRATION_KEY: DEFAULT_CAPTURE_CALIBRATION}

    def restore_answers(self, answers: dict[str, str]) -> None:
        saved = answers.get(CAPTURE_CALIBRATION_KEY)
        if saved in self._buttons:
            self._buttons[saved].setChecked(True)

    def is_complete(self) -> bool:
        """Always complete — every radio set is valid (default
        guarantees one is checked). Mirrors the genre-block
        contract so the host wizard can call this uniformly."""
        return True
