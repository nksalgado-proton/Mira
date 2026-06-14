"""LandingLevelDialog — the backfill wizard's first question (spec/57 §4.3).

"New event from existing media…" is ONE flow with three landing levels:
media that was merely *collected* lands the user at Pick; already-*picked*
keepers land at Edit; already-*edited* finals land at Share. The system
works backwards from the answer — media copies into ``Original Media/``
and database rows are written as if the earlier phases had run in order.

This dialog asks where the media stands; the flow tail (MainWindow's
``_open_new_event_flow``) applies the level's state writes + landing.
Levels outside :data:`AVAILABLE_LEVELS` render disabled — slices 5b/5c
flip them on as their state-write engines land.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
)

from mira.ui.i18n import tr

LEVEL_COLLECTED = "collected"
LEVEL_PICKED = "picked"
LEVEL_EDITED = "edited"

# All three landing levels serve (slice 5 complete). The dialog
# disables anything not listed here — kept as the gating seam in case
# a level ever needs to be pulled.
AVAILABLE_LEVELS = frozenset({LEVEL_COLLECTED, LEVEL_PICKED, LEVEL_EDITED})


class LandingLevelDialog(QDialog):
    """Ask where the media stands — the wizard's three landing levels."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("LandingLevelDialog")
        self.setWindowTitle(tr("New event from existing media"))

        outer = QVBoxLayout(self)
        intro = QLabel(tr(
            "Mira copies this media into the new event and prepares "
            "it as if the earlier phases had already run — you continue "
            "from where the media stands."
        ))
        intro.setWordWrap(True)
        outer.addWidget(intro)

        group = QGroupBox(tr("Where does this media stand?"))
        group.setObjectName("FormFieldGroup")
        group.setToolTip(tr(
            "Pick the level this folder is at — it decides which phase "
            "the new event opens on."
        ))
        group_layout = QVBoxLayout(group)

        self._collected = QRadioButton(tr(
            "Just collected — I still need to choose the keepers"))
        self._collected.setToolTip(tr(
            "The whole shoot, unfiltered. The event opens at Pick so you "
            "can decide what to keep."
        ))
        self._picked = QRadioButton(tr(
            "Already picked — these are my keepers"))
        self._picked.setToolTip(tr(
            "Only the keepers. Every file arrives already picked; the "
            "event opens at Edit."
        ))
        self._edited = QRadioButton(tr(
            "Already edited — these are my finished versions"))
        self._edited.setToolTip(tr(
            "Finished output. The one folder counts as both the originals "
            "and the edited results; the event opens ready to Share."
        ))

        self._radio_by_level = {
            LEVEL_COLLECTED: self._collected,
            LEVEL_PICKED: self._picked,
            LEVEL_EDITED: self._edited,
        }
        for level, radio in self._radio_by_level.items():
            if level not in AVAILABLE_LEVELS:
                radio.setEnabled(False)
                radio.setToolTip(
                    radio.toolTip() + "\n" + tr("Coming in the next build step."))
            group_layout.addWidget(radio)
        self._collected.setChecked(True)

        outer.addWidget(group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setToolTip(tr("Continue to the source-folder pick."))
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn is not None:
            cancel_btn.setToolTip(tr("Close without creating anything."))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self.resize(460, 0)

    def level(self) -> str:
        """The chosen landing level — one of the ``LEVEL_*`` constants."""
        for level, radio in self._radio_by_level.items():
            if radio.isChecked():
                return level
        return LEVEL_COLLECTED
