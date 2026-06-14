"""Collect & Timezones — Quick Sweep mode picker (task #96 / #1d;
rewritten 2026-06-07 for the 4-phase pivot).

Two options the user picks once here (with full education) and
can change later in Settings:

  * **Verbatim** (default; safest) — copy EVERY photo from source
    to ``Original Media/``; Pick / Skip decisions happen later in
    the Pick phase where they're fully reversible. Best for first-
    time users and anything where storage isn't the gating concern.

  * **Quick Sweep** — review each photo during the copy; obvious
    garbage is Skipped NOW and never lands in ``Original Media/``.
    Saves disk on burst-heavy trips, but the Skips are permanent
    (the SD-card safety net is gone once you accept the wipe
    gate).

The wizard answer maps 1:1 to ``settings.default_quick_sweep_mode``
(see ``core.wizard.apply_capture_settings_to_settings``). The per-
ingest UX still pops a consequence-disclosure dialog the first
time Quick Sweep would actually run, regardless of this default —
this picker just sets the dialog's starting state.
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
    CAPTURE_PRECULL_KEY,
    CAPTURE_PRECULL_PRECULL,
    CAPTURE_PRECULL_VERBATIM,
)
from mira.ui.i18n import tr


DEFAULT_CAPTURE_PRECULL = CAPTURE_PRECULL_VERBATIM


_OPTIONS: list[tuple[str, str, str]] = [
    (
        CAPTURE_PRECULL_VERBATIM,
        "Copy everything, decide later (default — safest)",
        "Every file on the card goes into Original Media. Decisions "
        "happen in the Pick phase, where Skip is reversible and "
        "the originals live in Original Media until you choose to "
        "delete them. Recommended whenever disk space isn't the "
        "limiting factor.",
    ),
    (
        CAPTURE_PRECULL_PRECULL,
        "Quick Sweep while copying (skip obvious garbage at ingest)",
        "Mid-copy, Mira shows each photo and you mark Pick / "
        "Skip. Skips are NEVER copied — they only exist on the "
        "source card. After the wipe gate, that means they're "
        "gone for good. Saves space on burst-heavy shoots; use "
        "with care.",
    ),
]


class StepCapturePrecull(QWidget):
    """Single-question picker for the default pre-cull mode."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 32, 40, 24)
        layout.setSpacing(14)

        title = QLabel(tr(
            "Should ingest copy everything or let you Quick Sweep "
            "as it goes?"
        ))
        title.setObjectName("WelcomeTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignLeft)
        title.setWordWrap(True)
        layout.addWidget(title)

        explain = QLabel(tr(
            "Pick the default for new ingests. You can flip the "
            "choice per-ingest in the offload dialog's "
            "<i>Change for this ingest</i> expander, and you can "
            "change the default any time in Settings → Collect."
        ))
        explain.setObjectName("BodyText")
        explain.setTextFormat(Qt.TextFormat.RichText)
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

        # Surface the consequence loudly — the wizard isn't the
        # place to hide the trade-off behind a tooltip.
        warning = QLabel(tr(
            "⚠ <b>Quick Sweep skips are permanent.</b> Whatever "
            "you mark Skip at ingest is never written to "
            "<code>Original Media/</code> — and after the SD-card "
            "wipe gate, the source is gone too. The Pick phase "
            "(later) lets you change your mind; Quick Sweep "
            "doesn't."
        ))
        warning.setObjectName("WizardWarning")
        warning.setTextFormat(Qt.TextFormat.RichText)
        warning.setWordWrap(True)
        layout.addWidget(warning)

        self._buttons[DEFAULT_CAPTURE_PRECULL].setChecked(True)

        layout.addStretch(1)

    # ── Wizard contract ────────────────────────────────────────────

    def collect_answers(self) -> dict[str, str]:
        for value, radio in self._buttons.items():
            if radio.isChecked():
                return {CAPTURE_PRECULL_KEY: value}
        return {CAPTURE_PRECULL_KEY: DEFAULT_CAPTURE_PRECULL}

    def restore_answers(self, answers: dict[str, str]) -> None:
        saved = answers.get(CAPTURE_PRECULL_KEY)
        if saved in self._buttons:
            self._buttons[saved].setChecked(True)

    def is_complete(self) -> bool:
        return True
