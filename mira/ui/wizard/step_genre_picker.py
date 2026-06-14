"""Genre picker — multi-select the photo types the user cares about.

Per Nelson's spec (2026-05-13): the wizard is split into blocks, one
per photo type. The user picks which blocks to fill out here; the
host WizardWindow then conditionally shows each selected block.

Each genre carries a short description focusing on what makes that
style unique — so the user can decide which blocks to open without
guessing what "Travel" or "Astro" mean to mira. Greyed-out
entries are genres whose wizard block hasn't been implemented yet;
they show with the description so the user knows what's coming.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from core.wizard import (
    ALL_GENRES,
    GENRE_ASTRO,
    GENRE_FAMILY,
    GENRE_LANDSCAPE,
    GENRE_MACRO,
    GENRE_PICKER_KEY,
    GENRE_PORTRAIT,
    GENRE_SPORTS,
    GENRE_STREET,
    GENRE_TRAVEL,
    GENRE_VIDEO,
    GENRE_WILDLIFE,
    IMPLEMENTED_GENRES,
)
from mira.ui.i18n import tr


# (genre_key, display_label, what-makes-it-unique description). The
# description is a one-line summary focused on the EXIF signature /
# habits that distinguish the genre from its neighbours — so the user
# decides which blocks to open by matching their own habits, not by
# guessing what each label means.
_GENRE_LABELS: list[tuple[str, str, str]] = [
    (
        GENRE_MACRO, "Macro",
        "Close-up of small subjects at high magnification — razor-thin "
        "depth of field, often manual focus, sometimes focus stacking.",
    ),
    (
        GENRE_WILDLIFE, "Wildlife",
        "Birds and mammals at distance — long telephoto, AF-C with "
        "animal / bird subject detection, fast shutter.",
    ),
    (
        GENRE_LANDSCAPE, "Landscape",
        "Scenic compositions — wide-to-normal lens, stopped aperture "
        "for deep DOF, often tripod and ND filter for long exposures.",
    ),
    (
        GENRE_PORTRAIT, "Portrait",
        "People as subject — short tele with wide aperture for "
        "background separation, eye / face detection AF.",
    ),
    (
        GENRE_ASTRO, "Astro / Night",
        "Stars, moon, city after dark — long shutter on tripod, manual "
        "focus, aperture from wide open (Milky Way) to stopped (moon).",
    ),
    (
        GENRE_SPORTS, "Sports / Action",
        "Action freezing on court / field / track — long tele, very "
        "fast shutter, high-fps burst, human / vehicle subject tracking.",
    ),
    (
        GENRE_STREET, "Street / Documentary",
        "Walking in public spaces, candid moments — normal-wide prime, "
        "single AF or zone, often monochrome rendering.",
    ),
    (
        GENRE_TRAVEL, "Travel / General",
        "The everyday catch-all — versatile zoom, single AF, moderate "
        "aperture. Whatever doesn't fit another scenario lands here.",
    ),
    (
        GENRE_FAMILY, "Events",
        "Gatherings, kids, indoor events — face detection AF, available "
        "light first with bounce flash when ambient is too low.",
    ),
    (
        GENRE_VIDEO, "Video",
        "Moving image — characterised by frame rate, codec, and picture "
        "profile (Log / HLG / Cinelike) instead of stills EXIF.",
    ),
]


class StepGenrePicker(QWidget):
    """Multi-select checkbox list of the 10 supported photo types.

    The user can pick any subset (including none — in which case no
    scenarios get generated and everything classifies as General).
    Greyed-out entries are genres whose wizard block hasn't been
    implemented yet; they show up so the user knows what's coming.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._checkboxes: dict[str, QCheckBox] = {}
        self._descriptions: dict[str, QLabel] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 32, 48, 32)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        question = QLabel(tr("Which photo types do you shoot?"))
        question.setObjectName("WizardQuestion")
        layout.addWidget(question)

        hint = QLabel(tr(
            "Pick any that apply — you can come back later to add more, "
            "remove some, or refine your answers. Anything you skip will "
            "classify as General."
        ))
        hint.setWordWrap(True)
        hint.setObjectName("WizardHint")
        layout.addWidget(hint)

        layout.addSpacing(4)

        for genre_key, label, description in _GENRE_LABELS:
            self._add_genre_row(layout, genre_key, label, description)

        layout.addStretch(1)

    def _add_genre_row(
        self,
        layout: QVBoxLayout,
        genre_key: str,
        label: str,
        description: str,
    ) -> None:
        """Render one genre as a checkbox + indented description line."""
        checkbox = QCheckBox(tr(label))
        checkbox.setProperty("genre_key", genre_key)
        if genre_key not in IMPLEMENTED_GENRES:
            checkbox.setEnabled(False)
            checkbox.setText(tr(label) + tr("   (coming soon)"))
        self._checkboxes[genre_key] = checkbox
        layout.addWidget(checkbox)

        desc_label = QLabel(tr(description))
        desc_label.setObjectName("WizardHint")
        desc_label.setWordWrap(True)
        # Indent under the checkbox text. Qt's checkbox indicator is
        # ~22px; 28px gives a clean alignment with a tiny visual
        # breath. setIndent works on QLabel; alternatives (margin via
        # QSS, ContentsMargins) work too but setIndent is the most
        # direct API.
        desc_label.setIndent(28)
        self._descriptions[genre_key] = desc_label
        layout.addWidget(desc_label)

        # Visual breathing room between genre rows.
        layout.addSpacing(4)

    # ── Wizard contract ──────────────────────────────────────────────

    def collect_answers(self) -> dict[str, str]:
        """Return the selected genres as a comma-separated string.

        Empty selection is a valid answer (the user is allowed to skip
        every genre — they'll see no genre blocks and get no scenarios).
        Genres that aren't implemented yet are filtered out defensively
        even if somehow checked.
        """
        selected = [
            key for key, cb in self._checkboxes.items()
            if cb.isChecked() and key in IMPLEMENTED_GENRES
        ]
        # Preserve the canonical order from ALL_GENRES so the on-disk
        # comma-separated value is stable regardless of UI ordering.
        ordered = [g for g in ALL_GENRES if g in selected]
        return {GENRE_PICKER_KEY: ",".join(ordered)}

    def restore_answers(self, answers: dict[str, str]) -> None:
        """Pre-check the boxes the user previously selected."""
        raw = answers.get(GENRE_PICKER_KEY, "")
        if not isinstance(raw, str):
            return
        previously = set(raw.split(","))
        for key, checkbox in self._checkboxes.items():
            if key in previously and key in IMPLEMENTED_GENRES:
                checkbox.setChecked(True)
