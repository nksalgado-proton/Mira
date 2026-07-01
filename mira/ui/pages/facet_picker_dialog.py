"""High-cardinality facet picker dialog (spec/83 §4).

Modal per-facet picker that used to open from the cross-event DC dialog
(spec/162 Round 3e retired ``NewCrossEventDcDialog`` along with the
``INLINE_PICKER_THRESHOLD`` constant it lived beside). The dialog stays
because its shape may be adopted by a future cross-event source picker.
Shows every option as
``value — N`` (count to disambiguate near-identical labels and to hint at
heavy hitters), supports search filtering, has Select all / Clear, and
splits its rows into **Main** vs a collapsed **Occasional (N)** section so
the borrowed-camera / one-off-city long tail doesn't crowd the user's main
gear.

The split (spec/83 §4 + spec/85 §5) reads the user's gear profile first:

* Camera / lens with ``gear_profile.is_active = True`` → main list.
* Camera / lens with a profile but ``is_active = False`` → occasional.
* Untagged gear (no profile row), and every non-gear facet → count
  heuristic: ``count < OCCASIONAL_CUTOFF`` → occasional, otherwise main.

Pure UI — no LibraryGateway import. The caller (the cross-event DC
dialog) hands in the already-fetched inventory and an optional
:class:`GearProfileSnapshot`; tests pass canned data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mira.ui.design import ghost_button, line_input, primary_button
from mira.ui.i18n import tr


# Untagged gear / non-gear facets: rows below this count fall into the
# collapsed Occasional section by default. spec/85 §1 + spec/83 §4 — the
# gear-profile flag overrides this heuristic when present. Tunable as a
# module constant; the brief's open-questions list flags it for Nelson.
OCCASIONAL_CUTOFF = 10


@dataclass(frozen=True)
class GearProfileSnapshot:
    """Snapshot the picker reads when deciding main vs occasional rows for
    the camera and lens facets (spec/85 §5).

    Built once at dialog-open time from
    :meth:`LibraryGateway.get_gear_profile`. Each set holds the matching
    ``global_items`` key (``camera_id`` or ``lens_model``). The picker
    asks via :meth:`for_facet` so the routing is per-facet — non-gear
    facets get the empty pair and fall through to the count heuristic."""

    cameras_active: FrozenSet[str] = field(default_factory=frozenset)
    cameras_occasional: FrozenSet[str] = field(default_factory=frozenset)
    lenses_active: FrozenSet[str] = field(default_factory=frozenset)
    lenses_occasional: FrozenSet[str] = field(default_factory=frozenset)

    def for_facet(
        self, facet_key: str,
    ) -> Tuple[FrozenSet[str], FrozenSet[str]]:
        """Return ``(active, occasional)`` for a facet, or two empty sets
        for non-gear facets (cities, countries, …)."""
        if facet_key == "camera_ids":
            return self.cameras_active, self.cameras_occasional
        if facet_key == "lens_models":
            return self.lenses_active, self.lenses_occasional
        return frozenset(), frozenset()


class FacetPickerDialog(QDialog):
    """Modal per-facet picker (spec/83 §4).

    Constructor:

    * ``facet_key`` — the ``filters_json`` key the picker writes back to
      (``"camera_ids"`` / ``"lens_models"`` / ``"cities"`` /
      ``"country_codes"`` today; future tags / people slot in for free).
    * ``facet_label`` — display label (``"Camera"`` etc.) for the title.
    * ``inventory`` — already-fetched ``(value, count)`` pairs,
      most-used-first (the spec/83 §5 inventory returns them that way).
    * ``initially_selected`` — the dimension's current selection, so the
      picker opens with those rows pre-checked.
    * ``gear`` — optional :class:`GearProfileSnapshot`; ``None`` (or
      empty) falls back to the count heuristic alone.

    Result: :meth:`selected_values` returns the survivors after the user
    hits OK; ``accepted_with_selection`` signal carries the same list.
    """

    accepted_with_selection = pyqtSignal(list)        # carries ``List[str]``

    def __init__(
        self,
        *,
        facet_key: str,
        facet_label: str,
        inventory: Sequence[Tuple[str, int]],
        initially_selected: Iterable[str] = (),
        gear: Optional[GearProfileSnapshot] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(
            tr("Choose {facet}").format(facet=facet_label))
        self.setMinimumWidth(420)
        self.setMinimumHeight(420)
        self._facet_key = facet_key
        self._inventory: List[Tuple[str, int]] = [
            (str(v), int(n)) for v, n in inventory]
        self._selected: Set[str] = set(initially_selected)
        active, occasional = (
            gear.for_facet(facet_key) if gear else (frozenset(), frozenset())
        )
        # Per-row partition computed once; the search filter hides rows
        # without re-partitioning, so the main / occasional headers stay
        # stable as the user types.
        self._main_rows: List[Tuple[str, int]] = []
        self._occasional_rows: List[Tuple[str, int]] = []
        for value, count in self._inventory:
            if self._belongs_in_occasional(value, count, active, occasional):
                self._occasional_rows.append((value, count))
            else:
                self._main_rows.append((value, count))

        # Checkboxes are populated by _build; the rows lists above carry
        # only data so the partition is testable headlessly.
        self._main_checks: List[Tuple[str, QCheckBox]] = []
        self._occasional_checks: List[Tuple[str, QCheckBox]] = []
        self._build()

    # ----- partition decision (testable in isolation) ------------------- #

    @staticmethod
    def _belongs_in_occasional(
        value: str, count: int,
        active: FrozenSet[str], occasional: FrozenSet[str],
    ) -> bool:
        """Main / occasional decision for one row (spec/83 §4 + spec/85 §5).
        ``gear_profile.is_active`` wins first; untagged falls through to the
        count heuristic."""
        if value in active:
            return False
        if value in occasional:
            return True
        return count < OCCASIONAL_CUTOFF

    # ----- layout ------------------------------------------------------- #

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        # Search
        self._search = line_input(tr("Search…"))
        self._search.setObjectName("FacetPickerSearch")
        self._search.textChanged.connect(self._on_search_changed)
        root.addWidget(self._search)

        # Select all / Clear strip
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._select_all_btn = ghost_button(tr("Select all"))
        self._select_all_btn.setObjectName("FacetPickerSelectAll")
        self._select_all_btn.clicked.connect(self._on_select_all)
        actions.addWidget(self._select_all_btn)
        self._clear_btn = ghost_button(tr("Clear"))
        self._clear_btn.setObjectName("FacetPickerClear")
        self._clear_btn.clicked.connect(self._on_clear)
        actions.addWidget(self._clear_btn)
        actions.addStretch()
        root.addLayout(actions)

        # Body: scroll over main + collapsed occasional
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setObjectName("FacetPickerBody")
        body = QWidget(scroll)
        body_l = QVBoxLayout(body)
        body_l.setContentsMargins(0, 0, 0, 0)
        body_l.setSpacing(4)

        if self._main_rows:
            for value, count in self._main_rows:
                cb = self._make_row_checkbox(value, count)
                self._main_checks.append((value, cb))
                body_l.addWidget(cb)

        if self._occasional_rows:
            self._occ_toggle = QToolButton(self)
            self._occ_toggle.setObjectName("FacetPickerOccasionalToggle")
            self._occ_toggle.setText(self._occasional_header_text())
            self._occ_toggle.setCheckable(True)
            self._occ_toggle.setChecked(False)
            self._occ_toggle.setArrowType(Qt.ArrowType.RightArrow)
            self._occ_toggle.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self._occ_toggle.toggled.connect(self._on_occasional_toggled)
            body_l.addWidget(self._occ_toggle)

            self._occ_container = QFrame(self)
            self._occ_container.setObjectName("FacetPickerOccasional")
            occ_l = QVBoxLayout(self._occ_container)
            occ_l.setContentsMargins(12, 0, 0, 0)
            occ_l.setSpacing(4)
            for value, count in self._occasional_rows:
                cb = self._make_row_checkbox(value, count)
                self._occasional_checks.append((value, cb))
                occ_l.addWidget(cb)
            self._occ_container.setVisible(False)
            body_l.addWidget(self._occ_container)
        else:
            self._occ_toggle = None
            self._occ_container = None

        body_l.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # Footer
        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addStretch()
        self._cancel_btn = ghost_button(tr("Cancel"))
        self._cancel_btn.clicked.connect(self.reject)
        footer.addWidget(self._cancel_btn)
        self._ok_btn = primary_button(tr("OK"))
        self._ok_btn.clicked.connect(self._on_accept)
        footer.addWidget(self._ok_btn)
        root.addLayout(footer)

    def _make_row_checkbox(self, value: str, count: int) -> QCheckBox:
        cb = QCheckBox(self._row_label(value, count), self)
        cb.setObjectName("FacetPickerRow")
        cb.setChecked(value in self._selected)
        cb.toggled.connect(
            lambda checked, v=value: self._toggle_value(v, bool(checked)))
        return cb

    @staticmethod
    def _row_label(value: str, count: int) -> str:
        return tr("{value} — {n}").format(value=value, n=count)

    def _occasional_header_text(self) -> str:
        return tr("Occasional ({n})").format(n=len(self._occasional_rows))

    # ----- selection state --------------------------------------------- #

    def _toggle_value(self, value: str, checked: bool) -> None:
        if checked:
            self._selected.add(value)
        else:
            self._selected.discard(value)

    def selected_values(self) -> List[str]:
        """The values currently checked, in catalogue order so the
        round-trip through ``filters_json`` stays deterministic."""
        order = {v: i for i, (v, _) in enumerate(self._inventory)}
        return sorted(self._selected, key=lambda v: order.get(v, 1_000_000))

    # ----- handlers ---------------------------------------------------- #

    def _on_search_changed(self, text: str) -> None:
        needle = text.strip().lower()
        for value, cb in self._main_checks + self._occasional_checks:
            visible = (not needle) or (needle in value.lower())
            cb.setVisible(visible)
        # If the user searches with the Occasional section collapsed, auto-
        # expand it when ALL matches are in the long tail — otherwise the
        # search produces an empty main list and looks broken. We probe
        # ``isHidden`` (the explicit setVisible state) instead of
        # ``isVisible`` so the check works whether or not the dialog itself
        # is currently shown.
        if needle and self._occ_toggle is not None \
                and self._occ_container is not None:
            main_visible = any(not cb.isHidden()
                               for _, cb in self._main_checks)
            occ_visible = any(not cb.isHidden()
                              for _, cb in self._occasional_checks)
            if occ_visible and not main_visible \
                    and not self._occ_toggle.isChecked():
                self._occ_toggle.setChecked(True)

    def _on_select_all(self) -> None:
        """Select every currently-visible row — search restricts the scope
        so "Select all" reads "select the matches"."""
        for value, cb in self._main_checks + self._occasional_checks:
            if not cb.isHidden() and not cb.isChecked():
                cb.setChecked(True)

    def _on_clear(self) -> None:
        """Clear every selection (regardless of search)."""
        for _value, cb in self._main_checks + self._occasional_checks:
            if cb.isChecked():
                cb.setChecked(False)

    def _on_occasional_toggled(self, checked: bool) -> None:
        if self._occ_container is None or self._occ_toggle is None:
            return
        self._occ_container.setVisible(checked)
        self._occ_toggle.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)

    def _on_accept(self) -> None:
        self.accepted_with_selection.emit(self.selected_values())
        self.accept()


__all__ = [
    "FacetPickerDialog",
    "GearProfileSnapshot",
    "OCCASIONAL_CUTOFF",
]
