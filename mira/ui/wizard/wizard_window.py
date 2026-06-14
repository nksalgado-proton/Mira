"""Multi-step wizard host.

Renders the canonical step sequence (Welcome → Genre Picker → one
block per selected genre → Done) and persists state on every
transition so a mid-wizard quit can resume at the same step with the
answers so far preserved.

Per Nelson's spec:

- **block-per-genre** structure: the Genre Picker records which
  blocks the user opted into; navigation walks only through those.
- **two-mode dialog sizing** (2026-05-13): Welcome is a small intro
  dialog (information confirmation). Once the user clicks Next, the
  dialog grows to its single fixed wizard-mode size and stays there
  for every subsequent step — no per-step resize, no shrink-grow
  jumping around. The "same dialog, contents change" feeling Nelson
  asked for. Re-entry from Settings (post-completion) restarts at
  Welcome (the saved answers are still pre-filled).
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.wizard import (
    GENRE_BLOCK_STEP,
    STEP_ASTRO_BLOCK,
    STEP_CAPTURE_CALIBRATION,
    STEP_CAPTURE_OVERVIEW,
    STEP_CAPTURE_PRECULL,
    STEP_DONE,
    STEP_FAMILY_BLOCK,
    STEP_GENRE_PICKER,
    STEP_LANDSCAPE_BLOCK,
    STEP_MACRO_BLOCK,
    STEP_PORTRAIT_BLOCK,
    STEP_SPORTS_BLOCK,
    STEP_STREET_BLOCK,
    STEP_TRAVEL_BLOCK,
    STEP_VIDEO_BLOCK,
    STEP_WELCOME,
    STEP_WILDLIFE_BLOCK,
    STEPS_IN_ORDER,
    WizardState,
    apply_capture_settings_to_settings,
    generate_scenarios_from_answers,
    get_selected_genres,
    load_wizard_state,
    next_applicable_step,
    previous_applicable_step,
    save_wizard_state,
)
from mira.ui.i18n import tr
from mira.ui.wizard.step_astro_block import StepAstroBlock
from mira.ui.wizard.step_capture_calibration import StepCaptureCalibration
from mira.ui.wizard.step_capture_overview import StepCaptureOverview
from mira.ui.wizard.step_capture_precull import StepCapturePrecull
from mira.ui.wizard.step_family_block import StepFamilyBlock
from mira.ui.wizard.step_genre_picker import StepGenrePicker
from mira.ui.wizard.step_landscape_block import StepLandscapeBlock
from mira.ui.wizard.step_macro_block import StepMacroBlock
from mira.ui.wizard.step_portrait_block import StepPortraitBlock
from mira.ui.wizard.step_sports_block import StepSportsBlock
from mira.ui.wizard.step_street_block import StepStreetBlock
from mira.ui.wizard.step_travel_block import StepTravelBlock
from mira.ui.wizard.step_video_block import StepVideoBlock
from mira.ui.wizard.step_welcome import StepWelcome
from mira.ui.wizard.step_wildlife_block import StepWildlifeBlock


log = logging.getLogger(__name__)


# Two-mode dialog sizing (per Nelson 2026-05-13). Welcome is the
# information-confirmation intro; the rest of the wizard runs at one
# size so the dialog frame stays put while content changes. Sizes
# are DEFAULTS — the user can resize each mode and the new size
# applies to subsequent steps in the same mode (so if the user
# enlarges to avoid scrolling on, say, the Video block, every other
# block uses that larger size too).
#
# Each step widget is wrapped in a QScrollArea so steps that exceed
# the current dialog size scroll vertically inside the frame rather
# than pushing the dialog larger.
WELCOME_DIALOG_SIZE = QSize(540, 360)
WIZARD_DIALOG_SIZE = QSize(700, 800)

# Hard floors so the user can't drag the dialog into an unusable
# state. Below these, content disappears even with scrollbars.
WELCOME_MIN_SIZE = QSize(420, 240)
WIZARD_MIN_SIZE = QSize(520, 360)


class WizardWindow(QDialog):
    """Modal wizard hosting Welcome + Genre Picker + per-genre blocks.

    All implemented step widgets live in a QStackedWidget; navigation
    just switches the current index. The dialog has two fixed sizes
    (Welcome small, wizard large), set whenever the current step
    transitions in or out of Welcome — never per-step within the
    main wizard.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setWindowTitle(tr("Setup"))
        self.setModal(True)

        self._state: WizardState = load_wizard_state()

        # User-chosen sizes per mode. Initialized to defaults; updated
        # whenever the user drags-to-resize the dialog (see resizeEvent).
        # Persist for the lifetime of this WizardWindow instance — not
        # across re-opens; settings persistence is a future option if
        # the user wants it.
        self._welcome_size: QSize = QSize(WELCOME_DIALOG_SIZE)
        self._wizard_size: QSize = QSize(WIZARD_DIALOG_SIZE)
        # Track the previous mode so we only resize on Welcome ↔ Wizard
        # transitions — within Wizard mode, the dialog stays at the
        # user's chosen size as they step through the genre blocks.
        self._previous_mode: str | None = None

        # All implemented step widgets live in the stack — applicability
        # is decided at navigation time, not by adding/removing widgets.
        self._step_keys: list[str] = [
            STEP_WELCOME,
            STEP_CAPTURE_OVERVIEW,
            STEP_CAPTURE_CALIBRATION,
            STEP_CAPTURE_PRECULL,
            STEP_GENRE_PICKER,
            STEP_MACRO_BLOCK,
            STEP_WILDLIFE_BLOCK,
            STEP_SPORTS_BLOCK,
            STEP_LANDSCAPE_BLOCK,
            STEP_ASTRO_BLOCK,
            STEP_PORTRAIT_BLOCK,
            STEP_FAMILY_BLOCK,
            STEP_STREET_BLOCK,
            STEP_TRAVEL_BLOCK,
            STEP_VIDEO_BLOCK,
        ]
        self._step_widgets: list[QWidget] = [
            StepWelcome(self),
            StepCaptureOverview(self),
            StepCaptureCalibration(self),
            StepCapturePrecull(self),
            StepGenrePicker(self),
            StepMacroBlock(self),
            StepWildlifeBlock(self),
            StepSportsBlock(self),
            StepLandscapeBlock(self),
            StepAstroBlock(self),
            StepPortraitBlock(self),
            StepFamilyBlock(self),
            StepStreetBlock(self),
            StepTravelBlock(self),
            StepVideoBlock(self),
        ]

        self._build_ui()
        self._restore_from_state()

    # ── Construction ────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Wrap each step in QScrollArea so a step taller than the
        # fixed dialog scrolls vertically rather than forcing the
        # dialog to grow. QScrollArea has a small default minimumSize
        # which means the stack's overall minimum stays small —
        # ``setFixedSize`` then actually pins the dialog (without the
        # scroll wrap the stack's minimum is the tallest step's
        # minimum, which defeats setFixedSize on smaller steps like
        # Welcome).
        self._stack = QStackedWidget(self)
        for widget in self._step_widgets:
            scroll = QScrollArea(self)
            scroll.setWidget(widget)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
            )
            scroll.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded,
            )
            self._stack.addWidget(scroll)
        root.addWidget(self._stack, stretch=1)

        button_row = QWidget(self)
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(24, 12, 24, 16)

        self._back_button = QPushButton(tr("&Back"))
        self._back_button.clicked.connect(self._on_back)
        button_layout.addWidget(self._back_button)

        button_layout.addStretch(1)

        self._cancel_button = QPushButton(tr("Cancel"))
        self._cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(self._cancel_button)

        self._next_button = QPushButton(tr("&Next"))
        self._next_button.setObjectName("Primary")
        self._next_button.setDefault(True)
        self._next_button.clicked.connect(self._on_next)
        button_layout.addWidget(self._next_button)

        root.addWidget(button_row)

    # ── State integration ───────────────────────────────────────────

    def _restore_from_state(self) -> None:
        """Place the user where they left off; pre-fill every step
        widget with previously captured answers.

        Two cases land the user at Welcome instead of the saved step:
        (1) saved step isn't a real step widget (legacy state from
        before a wizard rewrite, or completion-state ``"done"``); and
        (2) wizard was previously completed — re-opening to edit
        feels like a fresh run, not a resume mid-flight. Answers are
        still pre-filled so the edit-flow finds them.
        """
        target = self._state.current_step
        if target not in self._step_keys or self._state.completed:
            target = STEP_WELCOME
            self._state.current_step = STEP_WELCOME
        idx = self._step_keys.index(target)
        self._stack.setCurrentIndex(idx)

        for widget in self._step_widgets:
            widget.restore_answers(self._state.answers)

        self._update_button_labels()
        self._apply_dialog_mode()

    def _apply_dialog_mode(self) -> None:
        """Two modes — Welcome (information confirmation) and Wizard
        (genre picker + all genre blocks). Resize only happens at the
        Welcome ↔ Wizard transition; within Wizard mode the dialog
        stays at whatever size the user last chose (defaults if the
        user hasn't dragged-to-resize), so navigating between genre
        blocks doesn't make the frame jump.
        """
        current_mode = (
            "welcome"
            if self._state.current_step == STEP_WELCOME
            else "wizard"
        )
        if current_mode == "welcome":
            self.setMinimumSize(WELCOME_MIN_SIZE)
            self.setMaximumSize(16777215, 16777215)  # Qt's default max
        else:
            self.setMinimumSize(WIZARD_MIN_SIZE)
            self.setMaximumSize(16777215, 16777215)

        if current_mode != self._previous_mode:
            target_size = (
                self._welcome_size if current_mode == "welcome"
                else self._wizard_size
            )
            self.resize(target_size)
            self._previous_mode = current_mode

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt API
        """Remember the user's chosen size per mode. Dragging to
        resize during a Wizard-mode step applies the new size to
        every subsequent step in that mode."""
        super().resizeEvent(event)
        if self._previous_mode == "welcome":
            self._welcome_size = self.size()
        elif self._previous_mode == "wizard":
            self._wizard_size = self.size()

    def _update_button_labels(self) -> None:
        """Show ``&Complete`` on the last applicable step's primary button."""
        upcoming = next_applicable_step(self._state)
        is_last = upcoming == STEP_DONE or upcoming is None
        self._next_button.setText(tr("&Complete") if is_last else tr("&Next"))
        self._back_button.setEnabled(
            previous_applicable_step(self._state) is not None,
        )

    # ── Navigation ──────────────────────────────────────────────────

    def _on_back(self) -> None:
        self._absorb_current_step_answers()
        previous = previous_applicable_step(self._state)
        if previous is None:
            return
        self._move_to(previous)

    def _on_next(self) -> None:
        idx = self._stack.currentIndex()
        current_widget = self._step_widgets[idx]
        current_step = self._step_keys[idx]

        # Per-step validation. Genre blocks expose ``is_complete()``
        # when they have a "must answer everything" rule. With pre-fill
        # defaults, is_complete is trivially true at construction —
        # the user can hit Next immediately. Picker has no completeness
        # check (zero genres is a legitimate "all general" outcome).
        if current_step in GENRE_BLOCK_STEP.values() and not current_widget.is_complete():
            log.info("%s: every question required before advancing", current_step)
            return

        answers = current_widget.collect_answers()
        self._state.answers.update(answers)
        upcoming = next_applicable_step(self._state)
        if upcoming is None or upcoming == STEP_DONE:
            self._complete()
            return
        self._move_to(upcoming)

    def _move_to(self, step_key: str) -> None:
        """Switch the stack to ``step_key`` and persist the new state."""
        if step_key not in self._step_keys:
            log.warning("Cannot move to unknown step: %s", step_key)
            return
        idx = self._step_keys.index(step_key)
        self._stack.setCurrentIndex(idx)
        self._state.current_step = step_key
        save_wizard_state(self._state)
        self._update_button_labels()
        self._apply_dialog_mode()

    def _absorb_current_step_answers(self) -> None:
        idx = self._stack.currentIndex()
        captured = self._step_widgets[idx].collect_answers()
        if captured:
            self._state.answers.update(captured)

    # ── Completion + cancellation ───────────────────────────────────

    def _complete(self) -> None:
        """Final-step Next click. Generate scenarios, apply the
        Collect & Timezones settings, and flip completed.

        Two side effects, in deliberate order:
          1. ``generate_scenarios_from_answers`` writes per-genre
             scenario JSONs into ``user_data_dir()/scenarios/``.
          2. ``apply_capture_settings_to_settings`` updates
             ``settings.json`` with the picks from the Collect
             section (task #96 / #1d). Runs second so a failure
             writing settings doesn't leave the user with no
             scenarios at all — they can re-open the wizard from
             Settings later to retry that half.
        """
        self._absorb_current_step_answers()
        try:
            written = generate_scenarios_from_answers(self._state.answers)
            selected = get_selected_genres(self._state)
            log.info(
                "Wizard completed; %d scenario(s) written for %d genre(s) selected",
                len(written), len(selected),
            )
        except Exception:
            log.exception("Failed to generate scenarios from wizard answers")
            raise

        # Collect-section settings — never raise; the helper logs its
        # own failures. A failure here doesn't roll back the wizard,
        # because the scenarios are already on disk; the user can
        # re-open the wizard or fix the setting in Settings → Collect.
        try:
            apply_capture_settings_to_settings(self._state.answers)
        except Exception:
            log.exception(
                "Failed to apply capture settings from wizard answers"
            )

        self._state.completed = True
        self._state.current_step = STEP_DONE
        save_wizard_state(self._state)
        self.accept()

    def reject(self) -> None:
        """Cancel mid-wizard — preserve state so a relaunch resumes."""
        self._absorb_current_step_answers()
        save_wizard_state(self._state)
        super().reject()
