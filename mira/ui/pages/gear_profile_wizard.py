"""spec/85 — on-demand gear-profile wizard.

A small modal that lets the user tag which cameras and lenses they
actively use (the "I use this" flag) and optionally pin a preferred
genre per gear item. Read by two downstream systems:

* The spec/83 §4 :class:`FacetPickerDialog` — ``is_active`` decides the
  main / occasional split for the camera and lens facets, beating the
  count heuristic when present.
* The slice-7 classifier user-gear-hint tier (spec/85 §5) — items shot
  with a tagged camera or lens auto-classify to its preferred genres,
  just above the generic unknown-lens fallback.

Launched on demand (not first-run) — the wizard NEEDS data, so it can't
run before the first import. Entry points: the cross-event DC dialog's
"Manage my gear…" button and (when wired) the Settings dialog. Same
wizard, both paths.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mira.gateway.library_gateway import LibraryGateway
from mira.ui.base.flow_layout import FlowLayout
from mira.ui.design import ghost_button, primary_button
from mira.ui.i18n import tr


# spec/85 §3 — pre-fill rule. Rows with count ≥ this many photos open
# with "I use this" pre-ticked. Module constant per the brief's open-
# questions list; same shape as spec/83 §4 OCCASIONAL_CUTOFF — high-count
# gear is auto-suggested as main.
GEAR_PRE_TICK_THRESHOLD = 10

# spec/85 §3 — the genre set the wizard offers. Mirrors the first-run
# wizard's 10 genre blocks (``core.wizard.GENRE_BLOCK_STEP``) so users
# see the same labels in both surfaces. The classifier (slice 7) reads
# these keys verbatim — they are :class:`core.vocabulary.Scenario` values.
WIZARD_GENRES: Tuple[str, ...] = (
    "macro", "wildlife", "sports", "landscape", "astro",
    "portrait", "family", "street", "travel", "video",
)


class _GenrePicker(QWidget):
    """Wrapping row of genre-toggle checkboxes (spec/85 §3).

    Uses :class:`FlowLayout` so the 10 genres wrap to a second line on
    narrow displays instead of forcing the row wide — same spec/05 §4c
    reflow discipline as the slice-4 inline multi-select."""

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._boxes: List[QCheckBox] = []
        layout = FlowLayout(self, margin=0, spacing=6)
        for opt in WIZARD_GENRES:
            cb = QCheckBox(opt, self)
            cb.toggled.connect(lambda _=False: self.changed.emit())
            self._boxes.append(cb)
            layout.addWidget(cb)

    def selected_values(self) -> List[str]:
        return [cb.text() for cb in self._boxes if cb.isChecked()]

    def set_selected_values(self, values) -> None:
        target = set(values or ())
        for cb in self._boxes:
            cb.blockSignals(True)
            cb.setChecked(cb.text() in target)
            cb.blockSignals(False)


class _GearRow(QFrame):
    """One camera or lens row in the wizard's review list."""

    def __init__(self, *, key: str, count: int, kind: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = kind                    # 'camera' | 'lens'
        self._key = key
        self._count = count
        self.setObjectName("GearWizardRow")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(10)
        title = QLabel(tr("{key} — {n}").format(key=key, n=count), self)
        title.setObjectName("GearWizardRowTitle")
        f = title.font(); f.setBold(True); title.setFont(f)
        top.addWidget(title, 1)
        self._use_box = QCheckBox(tr("I use this"), self)
        self._use_box.setObjectName("GearWizardRowUse")
        top.addWidget(self._use_box)
        outer.addLayout(top)

        genres_label = QLabel(tr("Preferred genres (optional):"), self)
        genres_label.setObjectName("GearWizardRowGenresLabel")
        outer.addWidget(genres_label)
        self._genre_picker = _GenrePicker(self)
        outer.addWidget(self._genre_picker)

    # ----- read / write -------------------------------------------------- #

    @property
    def key(self) -> str:
        return self._key

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def count(self) -> int:
        return self._count

    def set_active(self, is_active: bool) -> None:
        self._use_box.blockSignals(True)
        self._use_box.setChecked(bool(is_active))
        self._use_box.blockSignals(False)

    def set_genres(self, genres) -> None:
        self._genre_picker.set_selected_values(genres)

    def is_active(self) -> bool:
        return self._use_box.isChecked()

    def selected_genres(self) -> List[str]:
        return self._genre_picker.selected_values()


class GearProfileWizard(QDialog):
    """spec/85 — manage my gear (cameras + lenses + per-gear genre tag).

    On open, runs the spec/83 §5 camera + lens inventories behind a short
    "Gathering your gear…" placeholder, then renders two review lists.
    Each row has "I use this" + a wrapping genre multi-select. Pre-fill:
    rows whose count is ≥ :data:`GEAR_PRE_TICK_THRESHOLD` open ticked,
    unless an existing :class:`GearProfile` row overrides them.

    On Save, the dialog writes through :meth:`LibraryGateway.set_gear_active`
    + :meth:`set_gear_genres` for every row; the slice-7 classifier reads
    the survivors via :meth:`LibraryGateway.gear_profile_for`."""

    saved = pyqtSignal()                # fired on successful commit

    def __init__(self, library_gateway: LibraryGateway,
                 *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Manage my gear"))
        self.setMinimumWidth(640)
        self.setMinimumHeight(520)
        self._lg = library_gateway
        self._camera_rows: List[_GearRow] = []
        self._lens_rows: List[_GearRow] = []

        self._build()
        self._populate()

    # ----- layout -------------------------------------------------------- #

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        intro = QLabel(tr(
            "Pick which cameras and lenses you actively use. The picker "
            "in the New Collection dialog reads these flags to lead with "
            "your main gear; the classifier reads the preferred genres to "
            "auto-tag photos shot with each piece of kit."))
        intro.setObjectName("GearWizardIntro")
        intro.setWordWrap(True)
        root.addWidget(intro)

        # Placeholder swap target — visible only while _populate runs.
        self._gathering_label = QLabel(tr("Gathering your gear…"))
        self._gathering_label.setObjectName("GearWizardLoading")
        self._gathering_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._gathering_label)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setObjectName("GearWizardBody")
        self._scroll.setVisible(False)
        body = QWidget(self._scroll)
        body_l = QVBoxLayout(body)
        body_l.setContentsMargins(0, 0, 0, 0)
        body_l.setSpacing(14)

        cam_header = QLabel(tr("Cameras"))
        cam_header.setObjectName("GearWizardSectionHeader")
        f = cam_header.font(); f.setBold(True); cam_header.setFont(f)
        body_l.addWidget(cam_header)
        self._cameras_container = QVBoxLayout()
        self._cameras_container.setSpacing(4)
        body_l.addLayout(self._cameras_container)

        lens_header = QLabel(tr("Lenses"))
        lens_header.setObjectName("GearWizardSectionHeader")
        f = lens_header.font(); f.setBold(True); lens_header.setFont(f)
        body_l.addWidget(lens_header)
        self._lenses_container = QVBoxLayout()
        self._lenses_container.setSpacing(4)
        body_l.addLayout(self._lenses_container)

        body_l.addStretch()
        self._scroll.setWidget(body)
        root.addWidget(self._scroll, 1)

        # Footer
        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addStretch()
        self._cancel = ghost_button(tr("Cancel"))
        self._cancel.clicked.connect(self.reject)
        footer.addWidget(self._cancel)
        self._save = primary_button(tr("Save"))
        self._save.clicked.connect(self._on_save)
        footer.addWidget(self._save)
        root.addLayout(footer)

    # ----- populate ------------------------------------------------------ #

    def _populate(self) -> None:
        """Read the inventory + existing profile rows, render the review
        lists, swap the loading label out."""
        cameras = list(self._lg.available_cameras())
        lenses = list(self._lg.available_lenses())
        existing: Dict[Tuple[str, str], object] = {
            (row.kind, row.key): row
            for row in self._lg.get_gear_profile()
        }

        for key, count in cameras:
            row = _GearRow(key=key, count=count, kind="camera", parent=self)
            self._configure_row(row, existing.get(("camera", key)), count)
            self._camera_rows.append(row)
            self._cameras_container.addWidget(row)
        if not cameras:
            self._cameras_container.addWidget(QLabel(tr(
                "No cameras in the projection yet — sync an event first.")))

        for key, count in lenses:
            row = _GearRow(key=key, count=count, kind="lens", parent=self)
            self._configure_row(row, existing.get(("lens", key)), count)
            self._lens_rows.append(row)
            self._lenses_container.addWidget(row)
        if not lenses:
            self._lenses_container.addWidget(QLabel(tr(
                "No lenses in the projection yet.")))

        self._gathering_label.setVisible(False)
        self._scroll.setVisible(True)

    def _configure_row(self, row: _GearRow,
                       existing: Optional[object], count: int) -> None:
        if existing is not None:
            row.set_active(bool(existing.is_active))
            row.set_genres(LibraryGateway.gear_preferred_genres(existing))
        else:
            row.set_active(count >= GEAR_PRE_TICK_THRESHOLD)
            row.set_genres([])

    # ----- commit -------------------------------------------------------- #

    def _on_save(self) -> None:
        """Persist every row through the slice-2 repo. The two write seams
        (set_gear_active + set_gear_genres) are independent — preserving
        the user's existing choice on the other axis."""
        for row in self._camera_rows + self._lens_rows:
            self._lg.set_gear_active(row.kind, row.key, row.is_active())
            self._lg.set_gear_genres(row.kind, row.key, row.selected_genres())
        self.saved.emit()
        self.accept()

    # ----- public read --------------------------------------------------- #

    def rows(self) -> List[_GearRow]:
        """Every row in display order. Tests use this; the future Settings
        tab launch can read it for confirmation summaries."""
        return list(self._camera_rows) + list(self._lens_rows)

    def camera_rows(self) -> List[_GearRow]:
        return list(self._camera_rows)

    def lens_rows(self) -> List[_GearRow]:
        return list(self._lens_rows)


__all__ = [
    "GearProfileWizard",
    "GEAR_PRE_TICK_THRESHOLD",
    "WIZARD_GENRES",
]
