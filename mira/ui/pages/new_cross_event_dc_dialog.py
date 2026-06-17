"""The cross-event "New Collection" dialog (spec/81 Phase 2 — Item 5).

The cross-event counterpart of :mod:`mira.ui.pages.new_cut_dialog`. Builds a
cross-event Dynamic Collection — a ``saved_filter`` row in ``mira.db`` — by
combining one origin (the ladder rung the user wants to reach) with the full
spec/32 §2 facet catalogue. The result is the cross-event DC the
:class:`LibraryGateway` then materialises into Cuts.

Where it differs from the event-scope dialog (spec/81 §2.1 surface widening):

* **Origin is a radio over four rungs** (``#collected`` / ``#picked`` /
  ``#edited`` / ``#exported``), not just ``#exported``. The user can reach
  what didn't finish, not just what did (spec/61 §8).
* **Filters span the full spec/32 §2 catalogue** — curatorial (style /
  media / rating / flag / color), EXIF/hardware (camera / lens / flash),
  settings (ISO / aperture / shutter / focal length, each with min/max),
  temporal (capture from/to), location (country / city). Every facet
  is optional; empty = no narrowing.
* **The live count refreshes on every facet change** via an injected
  ``dc_probe(expr, filters) -> int`` callable. The host wires it to
  :meth:`LibraryGateway.dc_probe`; tests pass a stub.

Pure UI — no LibraryGateway import. The dialog is built around inventories
(``available_classifications`` / ``available_cameras`` / …) the host passes
in at construction time, and emits a ``CrossEventDcInfo`` value on commit.
The host adapter (Item 5 wiring) turns the info into a
:meth:`LibraryGateway.create_dc` call.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core import collection_resolver, cut_names
from mira.ui.base.flow_layout import FlowLayout
from mira.ui.design import line_input, primary_button, ghost_button
from mira.ui.i18n import tr
from mira.ui.pages._filter_family import (
    FilterDimension,
    GROUP_ORDER,
    _ActiveFilterRow,
    build_cross_event_catalogue,
    group_label as _group_label,
)
from mira.ui.pages.facet_picker_dialog import (
    FacetPickerDialog,
    GearProfileSnapshot,
)


# spec/83 §3 — multi-select editor switches mode at this option count.
# Below it, every option is a checkbox laid out in a wrapping
# ``FlowLayout`` (Style, color label, country in small collections render
# this way). Above it, the editor collapses to a summary + Choose… button
# that opens the spec/83 §4 ``FacetPickerDialog`` (slice 5). Tunable as a
# build-time constant; the open-questions list in the brief asks Nelson
# to confirm the value.
INLINE_PICKER_THRESHOLD = 12


# --------------------------------------------------------------------------- #
# Data exchanged with the host
# --------------------------------------------------------------------------- #


@dataclass
class CrossEventDcInfo:
    """Everything the host needs to turn the dialog's state into a
    :meth:`LibraryGateway.create_dc` call. Carries the ``expr`` (the origin
    operand as a single-term typed-ref expression) + ``filters`` (the spec/32
    §2 catalogue dict) + the user-typed name + optional description."""

    name: str
    description: str = ""
    expr: list = field(default_factory=list)
    filters: dict = field(default_factory=dict)


FacetInventoryResolver = Callable[[str], Sequence[tuple]]
"""Per-facet inventory callable — given a ``filters_json`` key, return
``[(value, photo_count), …]`` most-used-first (spec/83 §5). The dialog uses
this lazily so a high-cardinality read (camera / lens / city / country) only
runs when the user actually adds that filter — the rest of the catalogue
never touches SQLite at dialog open."""


@dataclass(frozen=True)
class CrossEventInventories:
    """The facet inventory seam the dialog speaks through (spec/83 §5).

    Wraps a single :data:`FacetInventoryResolver` callable; tests pass a
    static dict via :meth:`from_dict`, the host wires
    :meth:`LibraryGateway.facet_inventory`. Reads return ``(value, count)``
    pairs because the picker (spec/83 §4) shows counts and the
    main-vs-occasional split (spec/85) is count-driven for untagged gear.
    """

    facet_inventory: Optional[FacetInventoryResolver] = None

    def for_key(self, facet_key: str) -> Sequence[tuple]:
        """``(value, count)`` pairs for the facet, or ``[]`` if no resolver
        is wired (the dialog tolerates the absent case so tests stay tiny)."""
        if self.facet_inventory is None:
            return ()
        return self.facet_inventory(facet_key)

    @classmethod
    def from_dict(cls,
                  by_key: Dict[str, Sequence[tuple]]) -> "CrossEventInventories":
        """Test helper — build inventories from a literal ``{key: [(v, n)]}``
        mapping. Unknown keys return ``[]``."""
        return cls(facet_inventory=lambda k: by_key.get(k, ()))


# --------------------------------------------------------------------------- #
# Facet widgets — uniform interface (value() / set_value() / changed)
# --------------------------------------------------------------------------- #


class _Facet(QWidget):
    """Base interface for one filter facet. Subclasses implement
    :meth:`value` (returns the dict fragment merged into ``filters_json``),
    :meth:`set_value` (rehydrates from a fragment), and emit ``changed``
    whenever the live count needs to refresh."""

    changed = pyqtSignal()

    def value(self) -> Dict[str, Any]:
        raise NotImplementedError

    def set_value(self, fragment: Dict[str, Any]) -> None:
        raise NotImplementedError


class _MultiSelectFacet(_Facet):
    """Adaptive multi-select editor (spec/83 §3).

    Mode is decided by the vocabulary size at construction:

    * **≤ :data:`INLINE_PICKER_THRESHOLD` options** → one checkbox per
      option laid out in a wrapping :class:`FlowLayout`. Small facets
      (style, color label, country in modest collections) wrap to the
      next line instead of forcing the dialog wide — the spec/05 §4c
      "surfaces reflow down to the 1280×720 floor" rule.
    * **above the threshold** → a summary line plus a Choose… button.
      The button emits :attr:`choose_requested` carrying the filter
      key so the slice-5 :class:`FacetPickerDialog` can open. Selected
      values live in :attr:`_selected` — :meth:`set_selected_values`
      is the cross-cutting setter used by both the picker and tests.

    ``key`` is the ``filters_json`` key (e.g. ``"styles"``,
    ``"camera_ids"``). Empty selection drops the key from the value
    fragment (forward-compat-friendly)."""

    #: Emitted by the > threshold editor when the user clicks Choose…
    #: Slice 5 connects the slot.
    choose_requested = pyqtSignal(str)

    def __init__(self, key: str, options: Sequence[str],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._key = key
        self._options: List[str] = list(options)
        self._selected: List[str] = []
        # Mode-specific widgets — only one is populated.
        self._boxes: List[QCheckBox] = []
        self._summary_label: Optional[QLabel] = None
        self._choose_btn: Optional[QPushButton] = None

        if len(self._options) <= INLINE_PICKER_THRESHOLD:
            self._build_inline()
        else:
            self._build_picker_shell()

    # ----- mode A: inline FlowLayout of checkboxes ---------------------- #

    def _build_inline(self) -> None:
        layout = FlowLayout(self, margin=0, spacing=8)
        for opt in self._options:
            cb = QCheckBox(opt, self)
            cb.toggled.connect(self._on_inline_toggled)
            self._boxes.append(cb)
            layout.addWidget(cb)

    def _on_inline_toggled(self, _checked: bool = False) -> None:
        self._selected = [cb.text() for cb in self._boxes if cb.isChecked()]
        self.changed.emit()

    # ----- mode B: summary + Choose… picker shell ----------------------- #

    def _build_picker_shell(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._summary_label = QLabel("", self)
        self._summary_label.setObjectName("CrossEventDcFacetSummary")
        self._summary_label.setWordWrap(True)
        layout.addWidget(self._summary_label, 1)
        self._choose_btn = ghost_button(tr("Choose…"))
        self._choose_btn.setObjectName("CrossEventDcFacetChoose")
        self._choose_btn.setToolTip(tr(
            "Open the {n}-item picker").format(n=len(self._options)))
        self._choose_btn.clicked.connect(
            lambda: self.choose_requested.emit(self._key))
        layout.addWidget(self._choose_btn)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        if self._summary_label is None:
            return
        if not self._selected:
            text = tr("No selection — {n} options").format(
                n=len(self._options))
        elif len(self._selected) <= 3:
            text = tr("{n} selected: {labels}").format(
                n=len(self._selected),
                labels=", ".join(self._selected))
        else:
            text = tr("{n} selected").format(n=len(self._selected))
        self._summary_label.setText(text)

    # ----- _Facet interface --------------------------------------------- #

    def value(self) -> Dict[str, Any]:
        return {self._key: list(self._selected)} if self._selected else {}

    def set_value(self, fragment: Dict[str, Any]) -> None:
        picked = list(fragment.get(self._key, []) or [])
        self.set_selected_values(picked)

    def set_selected_values(self, values: Sequence[str]) -> None:
        """Replace the selected set (both modes). The picker calls this
        when it commits; rehydrate calls it through :meth:`set_value`.
        Preserves the catalogue's order so deterministic round-trips
        survive."""
        target = set(values)
        self._selected = [opt for opt in self._options if opt in target]
        if self._boxes:
            for cb in self._boxes:
                cb.blockSignals(True)
                cb.setChecked(cb.text() in target)
                cb.blockSignals(False)
        self._refresh_summary()
        self.changed.emit()

    def options(self) -> Sequence[str]:
        """The full vocabulary, in catalogue order. The picker (slice 5)
        reads this to render its row list without re-querying."""
        return tuple(self._options)

    def is_picker_mode(self) -> bool:
        """``True`` when the editor uses the summary + Choose… shell
        (above the threshold), ``False`` for the inline FlowLayout."""
        return self._choose_btn is not None


class _SingleSelectFacet(_Facet):
    """Radio-style single-select. Each option carries a ``label`` (display)
    and a ``value`` (what lands in ``filters_json``). The first option is
    the "no-narrowing" default — its value is dropped from the fragment so
    empty filters dicts stay small."""

    def __init__(self, key: str, options: Sequence[tuple],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._key = key
        self._group = QButtonGroup(self)
        self._buttons: List[tuple] = []                 # (button, value)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for i, (label, value) in enumerate(options):
            rb = QRadioButton(label, self)
            if i == 0:
                rb.setChecked(True)
            self._group.addButton(rb, i)
            self._buttons.append((rb, value))
            rb.toggled.connect(lambda _=False: self.changed.emit())
            layout.addWidget(rb)
        layout.addStretch()
        # First option's value is the "no narrowing" sentinel.
        self._default_value = options[0][1] if options else None

    def value(self) -> Dict[str, Any]:
        for btn, value in self._buttons:
            if btn.isChecked():
                if value == self._default_value:
                    return {}
                return {self._key: value}
        return {}

    def set_value(self, fragment: Dict[str, Any]) -> None:
        target = fragment.get(self._key, self._default_value)
        for btn, value in self._buttons:
            btn.blockSignals(True)
            btn.setChecked(value == target)
            btn.blockSignals(False)
        self.changed.emit()


class _NumberRangeFacet(_Facet):
    """Min/max range over a numeric facet. Two spin boxes; either end is
    optional (blank = "no constraint" for that end). ``min_key`` and
    ``max_key`` are the ``filters_json`` keys."""

    def __init__(self, min_key: str, max_key: str,
                 *, integer: bool = True,
                 lo: float = 0, hi: float = 1_000_000,
                 step: float = 1.0, decimals: int = 0,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._min_key = min_key
        self._max_key = max_key
        self._integer = integer

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._enable_min = QCheckBox(tr("min"), self)
        self._enable_max = QCheckBox(tr("max"), self)
        if integer:
            self._lo = QSpinBox(self)
            self._hi = QSpinBox(self)
            self._lo.setRange(int(lo), int(hi))
            self._hi.setRange(int(lo), int(hi))
            self._lo.setSingleStep(int(step))
            self._hi.setSingleStep(int(step))
        else:
            self._lo = QDoubleSpinBox(self)
            self._hi = QDoubleSpinBox(self)
            self._lo.setDecimals(decimals)
            self._hi.setDecimals(decimals)
            self._lo.setRange(lo, hi)
            self._hi.setRange(lo, hi)
            self._lo.setSingleStep(step)
            self._hi.setSingleStep(step)

        self._lo.setEnabled(False)
        self._hi.setEnabled(False)
        self._enable_min.toggled.connect(self._lo.setEnabled)
        self._enable_max.toggled.connect(self._hi.setEnabled)
        for w in (self._enable_min, self._enable_max, self._lo, self._hi):
            try:
                w.toggled.connect(lambda _=False: self.changed.emit())
            except AttributeError:
                w.valueChanged.connect(lambda _=0: self.changed.emit())

        layout.addWidget(self._enable_min)
        layout.addWidget(self._lo)
        layout.addSpacing(12)
        layout.addWidget(self._enable_max)
        layout.addWidget(self._hi)
        layout.addStretch()

    def value(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if self._enable_min.isChecked():
            v = self._lo.value()
            out[self._min_key] = int(v) if self._integer else float(v)
        if self._enable_max.isChecked():
            v = self._hi.value()
            out[self._max_key] = int(v) if self._integer else float(v)
        return out

    def set_value(self, fragment: Dict[str, Any]) -> None:
        lo = fragment.get(self._min_key)
        hi = fragment.get(self._max_key)
        self._enable_min.blockSignals(True)
        self._lo.blockSignals(True)
        self._enable_max.blockSignals(True)
        self._hi.blockSignals(True)
        if lo is not None:
            self._enable_min.setChecked(True)
            self._lo.setEnabled(True)
            self._lo.setValue(int(lo) if self._integer else float(lo))
        else:
            self._enable_min.setChecked(False)
            self._lo.setEnabled(False)
        if hi is not None:
            self._enable_max.setChecked(True)
            self._hi.setEnabled(True)
            self._hi.setValue(int(hi) if self._integer else float(hi))
        else:
            self._enable_max.setChecked(False)
            self._hi.setEnabled(False)
        self._enable_min.blockSignals(False)
        self._lo.blockSignals(False)
        self._enable_max.blockSignals(False)
        self._hi.blockSignals(False)
        self.changed.emit()


class _StarsMinFacet(_Facet):
    """Star rating ≥ N (spec/32 §1 "5-star photos"). Range 1-5; an
    explicit "Any" button leaves the fragment empty."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._group = QButtonGroup(self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._buttons: List[tuple] = []
        # First button = "Any" (no narrowing).
        for i, (label, value) in enumerate([
            (tr("Any"), None),
            (tr("≥ 1"), 1), (tr("≥ 2"), 2), (tr("≥ 3"), 3),
            (tr("≥ 4"), 4), (tr("≥ 5"), 5),
        ]):
            rb = QRadioButton(label, self)
            if i == 0:
                rb.setChecked(True)
            self._group.addButton(rb, i)
            self._buttons.append((rb, value))
            rb.toggled.connect(lambda _=False: self.changed.emit())
            layout.addWidget(rb)
        layout.addStretch()

    def value(self) -> Dict[str, Any]:
        for btn, val in self._buttons:
            if btn.isChecked() and val is not None:
                return {"stars_min": val}
        return {}

    def set_value(self, fragment: Dict[str, Any]) -> None:
        target = fragment.get("stars_min")
        for btn, val in self._buttons:
            btn.blockSignals(True)
            btn.setChecked(val == target)
            btn.blockSignals(False)
        if all(not btn.isChecked() for btn, _ in self._buttons):
            self._buttons[0][0].setChecked(True)
        self.changed.emit()


class _DateRangeFacet(_Facet):
    """ISO date range — two text inputs. Empty = no constraint on that end.
    Validation is light (any non-empty string passes through; the SQL
    layer's ``BETWEEN`` / overlap clauses do the real work).

    ``min_key`` / ``max_key`` parameterize the ``filters_json`` keys so
    the same widget drives both the spec/32 §2b capture-date facet
    (``capture_from`` / ``capture_to``) and the spec/86 §5 event-date
    facet (``event_from`` / ``event_to``)."""

    def __init__(
        self,
        *,
        min_key: str = "capture_from",
        max_key: str = "capture_to",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._min_key = min_key
        self._max_key = max_key
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._from = line_input(tr("from (YYYY-MM-DD)"))
        self._to = line_input(tr("to (YYYY-MM-DD)"))
        self._from.textChanged.connect(lambda _: self.changed.emit())
        self._to.textChanged.connect(lambda _: self.changed.emit())
        layout.addWidget(self._from)
        layout.addWidget(self._to)
        layout.addStretch()

    def value(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        f = self._from.text().strip()
        t = self._to.text().strip()
        if f:
            out[self._min_key] = f
        if t:
            out[self._max_key] = t
        return out

    def set_value(self, fragment: Dict[str, Any]) -> None:
        self._from.blockSignals(True)
        self._to.blockSignals(True)
        self._from.setText(fragment.get(self._min_key, "") or "")
        self._to.setText(fragment.get(self._max_key, "") or "")
        self._from.blockSignals(False)
        self._to.blockSignals(False)
        self.changed.emit()


# --------------------------------------------------------------------------- #
# Origin radio — the four ladder rungs
# --------------------------------------------------------------------------- #


class _OriginRadio(QWidget):
    """The base-universe operand (spec/81 §2.1) — one of the four ladder
    rungs. Emits ``changed`` so the dialog can re-probe; emits the token via
    :meth:`token`."""

    changed = pyqtSignal()

    LADDER: tuple = (
        (collection_resolver.BASE_COLLECTED, "#collected — every captured frame"),
        (collection_resolver.BASE_PICKED,    "#picked — survived the Pick decision"),
        (collection_resolver.BASE_EDITED,    "#edited — has been developed"),
        (collection_resolver.BASE_EXPORTED,  "#exported — shipped to disk"),
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._group = QButtonGroup(self)
        self._buttons: List[tuple] = []
        for i, (token, label) in enumerate(self.LADDER):
            rb = QRadioButton(tr(label), self)
            if token == collection_resolver.BASE_EXPORTED:
                rb.setChecked(True)
            self._group.addButton(rb, i)
            self._buttons.append((rb, token))
            rb.toggled.connect(lambda _=False: self.changed.emit())
            layout.addWidget(rb)

    def token(self) -> str:
        for btn, tok in self._buttons:
            if btn.isChecked():
                return tok
        return collection_resolver.BASE_EXPORTED

    def set_token(self, tok: str) -> None:
        # Block ALL buttons before mutating so the QButtonGroup's exclusive
        # cascade (un-checking the previous selection) doesn't fire a
        # cross-signal through whichever radio was previously on.
        for btn, _t in self._buttons:
            btn.blockSignals(True)
        try:
            for btn, t in self._buttons:
                btn.setChecked(t == tok)
        finally:
            for btn, _t in self._buttons:
                btn.blockSignals(False)
        self.changed.emit()


# --------------------------------------------------------------------------- #
# The dialog
# --------------------------------------------------------------------------- #


class NewCrossEventDcDialog(QDialog):
    """Build a cross-event Dynamic Collection (spec/81 §2.1 + spec/32 §2 +
    spec/83 §2).

    Two-tier model (spec/83 §2): the dialog opens with **name + origin + live
    count + "+ Add filter"** and **nothing else** — most collections use two
    or three constraints, so the other dozen facets are noise until the user
    asks for them. "+ Add filter" opens a grouped menu (Curatorial / Camera &
    lens / Settings / When & where); picking a dimension adds an active
    filter row whose editor is the existing facet widget (slice 4 will make
    it adaptive). Each row carries an ✕ to remove it.

    Constructor takes the inventories (host pulls from :class:`LibraryGateway`)
    and an optional ``dc_probe`` callable for the live count. Tests can pass
    a stub probe; the host wires it to :meth:`LibraryGateway.dc_probe`.

    Public surface:
        * :meth:`info` → :class:`CrossEventDcInfo` (the host's commit input).
        * :meth:`add_filter_dimension` / :meth:`remove_filter_dimension` —
          programmatic entry points; the menu wires user clicks here, tests
          use them directly to skip the menu choreography.
        * :meth:`active_dimension_ids` — read the active set.
        * :meth:`accept` is gated on a non-empty name.
        * ``saved`` signal carries the info when the user accepts (parity
          with the event-scope dialog's signal pattern).
    """

    saved = pyqtSignal(CrossEventDcInfo)

    def __init__(
        self,
        *,
        inventories: CrossEventInventories,
        dc_probe: Optional[Callable[[list, dict], int]] = None,
        existing: Optional[CrossEventDcInfo] = None,
        existing_tags: Sequence[str] = (),
        gear: Optional[GearProfileSnapshot] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("New cross-event collection"))
        self.setMinimumWidth(640)
        self._inventories = inventories
        self._dc_probe = dc_probe or (lambda _expr, _filters: 0)
        self._existing_tags = list(existing_tags)
        # Gear snapshot drives the picker's main-vs-occasional split for
        # camera / lens facets (spec/85 §5). ``None`` falls through to the
        # count heuristic alone — tests + the event-scope dialog (slice 8)
        # use that path.
        self._gear: Optional[GearProfileSnapshot] = gear
        # ``_facets`` mirrors the order rows are added — tests walk it to
        # poke individual editors; the spec-32 ``_filters()`` aggregator
        # iterates it too.
        self._facets: List[_Facet] = []
        self._active_rows: "Dict[str, _ActiveFilterRow]" = {}
        # Catalogue built before _build_layout so the menu can read it.
        # Cross-event dialog uses the full spec/32 §2 catalogue (spec/81 §2.1
        # full ladder + every facet); the event-scope sibling uses
        # :func:`build_event_scope_catalogue` instead (spec/81 §2.1 — thin
        # surface). Slice 8 share is the catalogue itself.
        self._dimensions: Dict[str, FilterDimension] = \
            build_cross_event_catalogue(self)

        self._build_layout()
        if existing is not None:
            self._rehydrate(existing)
        self._refresh_tag_preview()
        self._refresh_count()

    # ----- layout --------------------------------------------------------- #

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # Identity row: name + description + live tag preview.
        identity = self._build_identity()
        root.addWidget(identity)

        # Scrollable body: origin + Add-filter surface + active rows.
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setObjectName("CrossEventDcBody")
        body = QWidget(scroll)
        body_l = QVBoxLayout(body)
        body_l.setContentsMargins(0, 0, 0, 0)
        body_l.setSpacing(14)

        body_l.addWidget(self._build_section(
            tr("Origin (spec/81 §2.1)"),
            self._build_origin()))

        # Active-filter stack (spec/83 §2). Empty until the user clicks Add.
        filters_frame = QFrame()
        filters_frame.setObjectName("CrossEventDcFilters")
        filters_l = QVBoxLayout(filters_frame)
        filters_l.setContentsMargins(0, 0, 0, 0)
        filters_l.setSpacing(6)
        self._filters_container = filters_l

        self._empty_state = QLabel("")
        self._empty_state.setObjectName("CrossEventDcEmptyFilters")
        self._empty_state.setWordWrap(True)
        filters_l.addWidget(self._empty_state)

        body_l.addWidget(filters_frame)

        # The Add-filter button — opens a grouped menu of dimensions.
        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        self._add_btn = ghost_button(tr("+ Add filter"))
        self._add_btn.setObjectName("CrossEventDcAddFilter")
        self._add_btn.setToolTip(tr("Add a filter dimension"))
        self._add_btn.clicked.connect(self._show_add_menu)
        add_row.addWidget(self._add_btn)
        add_row.addStretch()
        body_l.addLayout(add_row)

        body_l.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # Live-count strip.
        self._count_label = QLabel("")
        self._count_label.setObjectName("CrossEventDcCount")
        root.addWidget(self._count_label)

        # Footer
        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addStretch()
        self._cancel = ghost_button(tr("Cancel"))
        self._cancel.clicked.connect(self.reject)
        footer.addWidget(self._cancel)
        self._create = primary_button(tr("Create"))
        self._create.clicked.connect(self._on_accept)
        footer.addWidget(self._create)
        root.addLayout(footer)

    def _build_identity(self) -> QWidget:
        box = QFrame()
        box.setObjectName("CrossEventDcIdentity")
        grid = QGridLayout(box)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(8)
        grid.addWidget(QLabel(tr("Name")), 0, 0)
        self._name = line_input(tr("e.g. 5-star macro across all years"))
        self._name.textChanged.connect(self._refresh_tag_preview)
        grid.addWidget(self._name, 0, 1)
        self._tag_preview = QLabel("")
        self._tag_preview.setObjectName("CrossEventDcTagPreview")
        grid.addWidget(self._tag_preview, 1, 1)
        grid.addWidget(QLabel(tr("Description")), 2, 0)
        self._description = line_input(tr("optional one-liner"))
        grid.addWidget(self._description, 2, 1)
        return box

    def _build_origin(self) -> QWidget:
        self._origin = _OriginRadio()
        self._origin.changed.connect(self._refresh_count)
        return self._origin

    def _build_section(self, title: str, widget: QWidget) -> QWidget:
        frame = QFrame()
        frame.setObjectName("CrossEventDcSection")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        header = QLabel(title)
        header.setObjectName("CrossEventDcSectionTitle")
        layout.addWidget(header)
        layout.addWidget(widget)
        return frame

    # ----- Add-filter menu (uses the slice-8 shared catalogue) ------------ #

    def _show_add_menu(self) -> None:
        """Open the grouped Add-filter menu (spec/83 §2). Already-active
        dimensions are disabled — adding a second Style row makes no sense.
        Re-built each click so the disabled state is always current."""
        menu = QMenu(self)
        for group_id in GROUP_ORDER:
            dims = [d for d in self._dimensions.values()
                    if d.group == group_id]
            if not dims:
                continue
            sub = menu.addMenu(_group_label(group_id))
            for dim in dims:
                act = sub.addAction(dim.label)
                already = dim.dim_id in self._active_rows
                act.setEnabled(not already)
                if not already:
                    act.triggered.connect(
                        lambda checked=False, did=dim.dim_id:
                        self.add_filter_dimension(did))
        menu.exec(self._add_btn.mapToGlobal(
            self._add_btn.rect().bottomLeft()))

    def add_filter_dimension(self, dim_id: str) -> "_ActiveFilterRow":
        """Add an active filter row for ``dim_id``. Returns the row so
        tests can poke its editor; idempotent — if the dimension is already
        active, returns the existing row."""
        if dim_id in self._active_rows:
            return self._active_rows[dim_id]
        dim = self._dimensions.get(dim_id)
        if dim is None:
            raise KeyError(f"unknown filter dimension: {dim_id!r}")
        facet = dim.factory()
        # Picker-mode multi-selects (spec/83 §3, > threshold) route their
        # Choose… click here so we can open the FacetPickerDialog over the
        # right inventory + gear snapshot.
        if isinstance(facet, _MultiSelectFacet) and facet.is_picker_mode():
            facet.choose_requested.connect(self._open_facet_picker)
        row = _ActiveFilterRow(dim, facet, parent=self)
        row.remove_requested.connect(self.remove_filter_dimension)
        self._active_rows[dim_id] = row
        # Insert above the empty-state stretch (which lives at the bottom of
        # the filters container by virtue of being the last widget).
        self._filters_container.addWidget(row)
        self._refresh_empty_state()
        self._refresh_count()
        return row

    def remove_filter_dimension(self, dim_id: str) -> None:
        """Drop the row for ``dim_id`` and clean up its registered facet.
        No-op if the dimension wasn't active."""
        row = self._active_rows.pop(dim_id, None)
        if row is None:
            return
        facet = row.facet
        if facet in self._facets:
            self._facets.remove(facet)
        row.setParent(None)
        row.deleteLater()
        self._refresh_empty_state()
        self._refresh_count()

    def active_dimension_ids(self) -> List[str]:
        """The currently-active dimensions in insertion order."""
        return list(self._active_rows.keys())

    def _open_facet_picker(self, facet_key: str) -> None:
        """Open the spec/83 §4 picker for a high-cardinality facet. Called
        when the user clicks Choose… on a > threshold ``_MultiSelectFacet``;
        on OK, writes the survivors back to the facet's selected set."""
        row = self._active_rows.get(facet_key) if facet_key in self._dimensions \
            else None
        # The dimension id and the facet key happen to match for the four
        # picker-bound dimensions (cameras / lenses / cities / countries)
        # since each owns exactly one filter key. Lookup falls back to a
        # walk for forward-compat.
        if row is None:
            for d_id, r in self._active_rows.items():
                dim = self._dimensions.get(d_id)
                if dim is not None and facet_key in dim.filter_keys:
                    row = r
                    break
        if row is None:
            return
        facet = row.facet
        if not isinstance(facet, _MultiSelectFacet):
            return
        dim = self._dimensions.get(row.dim_id())
        label = dim.label if dim else facet_key
        inventory = self._inventories.for_key(facet_key)
        # Currently-selected values: round-trip through value() so we read
        # the same encoded set the dialog will commit.
        currently = facet.value().get(facet_key, [])
        picker = FacetPickerDialog(
            facet_key=facet_key,
            facet_label=label,
            inventory=inventory,
            initially_selected=currently,
            gear=self._gear,
            parent=self,
        )
        if picker.exec() == QDialog.DialogCode.Accepted:
            facet.set_selected_values(picker.selected_values())

    def _register_facet(self, w: "_Facet") -> "_Facet":
        """Hook a freshly-built facet into the dialog: wire its ``changed``
        signal to the live-count refresh and append to :attr:`_facets` so
        :meth:`_filters` and the test helpers see it."""
        w.changed.connect(self._refresh_count)
        self._facets.append(w)
        return w

    # ----- facet factory primitives — build only, do NOT register ---------- #

    def _make_multi(self, key: str) -> _MultiSelectFacet:
        """Build a multi-select facet by lazily reading
        :meth:`CrossEventInventories.for_key` (spec/83 §5). Today the editor
        is :class:`_MultiSelectFacet` regardless of cardinality; slice 4
        flips to the adaptive ``FlowLayout`` / picker split."""
        pairs = self._inventories.for_key(key)
        options = [str(v) for v, _ in pairs]
        return _MultiSelectFacet(key, options)

    def _make_single(self, key: str,
                     options: Sequence[tuple]) -> _SingleSelectFacet:
        return _SingleSelectFacet(key, list(options))

    def _make_range(self, min_key: str, max_key: str, *,
                    integer: bool, lo: float, hi: float,
                    step: float, decimals: int = 0) -> _NumberRangeFacet:
        return _NumberRangeFacet(min_key, max_key,
                                 integer=integer, lo=lo, hi=hi,
                                 step=step, decimals=decimals)

    def _make_stars_min(self) -> _StarsMinFacet:
        return _StarsMinFacet()

    def _make_date_range(
        self,
        min_key: str = "capture_from",
        max_key: str = "capture_to",
    ) -> _DateRangeFacet:
        return _DateRangeFacet(min_key=min_key, max_key=max_key)

    # ----- live updates --------------------------------------------------- #

    def _refresh_tag_preview(self) -> None:
        name = self._name.text()
        slug = cut_names.slugify(name)
        if not slug:
            self._tag_preview.setText("")
            return
        # check_tag against existing — for live preview, just show the slug
        # + a warning if reserved / taken.
        err = cut_names.check_tag(slug, self._existing_tags)
        if err == "reserved":
            self._tag_preview.setText(tr("tag: #{slug} — reserved name").format(slug=slug))
        elif err == "taken":
            self._tag_preview.setText(tr("tag: #{slug} — already in use").format(slug=slug))
        else:
            self._tag_preview.setText(tr("tag: #{slug}").format(slug=slug))

    def _refresh_count(self) -> None:
        try:
            n = self._dc_probe(self._expr(), self._filters())
        except Exception:                                          # noqa: BLE001
            n = -1
        if n < 0:
            self._count_label.setText(tr("Count: error"))
        elif not self._active_rows:
            # No filters → the count IS the empty-universe count. Spell
            # out which origin (spec/83 §2 empty-state line) so the user
            # sees what "everything" means at this rung.
            self._count_label.setText(tr(
                "No filters — matches everything in {origin} "
                "({n} items)").format(
                    origin=self._origin_label(), n=n))
        else:
            self._count_label.setText(
                tr("{n} items match").format(n=n))
        # Empty-state line under the (absent) filters echoes the same hint
        # so it's visible even when the count label scrolls off-screen on
        # narrow displays.
        self._refresh_empty_state(count=n)

    def _refresh_empty_state(self, *, count: Optional[int] = None) -> None:
        """Toggle the empty-state hint in the filters container."""
        if not hasattr(self, "_empty_state"):
            return
        if self._active_rows:
            self._empty_state.setVisible(False)
            return
        self._empty_state.setVisible(True)
        # ``count`` may be omitted when called from add/remove — read it
        # back lazily so we don't double-probe.
        if count is None:
            try:
                count = self._dc_probe(self._expr(), self._filters())
            except Exception:                                      # noqa: BLE001
                count = -1
        if count is None or count < 0:
            self._empty_state.setText(tr(
                "No filters — pick "
                "“+ Add filter” to narrow the collection."))
        else:
            self._empty_state.setText(tr(
                "No filters — matches everything in {origin} "
                "({n} items).").format(
                    origin=self._origin_label(), n=count))

    def _origin_label(self) -> str:
        """The origin radio's current token as a ``#tag`` display string —
        used by the empty-state and the no-filters count line."""
        return "#" + str(self._origin.token())

    # ----- value composition --------------------------------------------- #

    def _expr(self) -> list:
        """Origin operand as a single-term typed-ref expression."""
        return [["+", self._origin.token()]]

    def _filters(self) -> dict:
        out: dict = {}
        for facet in self._facets:
            out.update(facet.value())
        return out

    def info(self) -> CrossEventDcInfo:
        return CrossEventDcInfo(
            name=self._name.text().strip(),
            description=self._description.text().strip(),
            expr=self._expr(),
            filters=self._filters(),
        )

    # ----- rehydrate ------------------------------------------------------ #

    def _rehydrate(self, info: CrossEventDcInfo) -> None:
        """Pre-fill the dialog from an existing DC (Edit flow). Scans the
        saved ``filters`` dict and adds an active row for every dimension
        whose ``filter_keys`` intersect with the dict — so rehydrating a
        DC that pinned camera + ISO + country opens with exactly those
        three rows."""
        self._name.setText(info.name)
        self._description.setText(info.description)
        # origin from expr[0][1] if available
        try:
            tok = info.expr[0][1]
            if isinstance(tok, str):
                self._origin.set_token(tok)
        except (IndexError, KeyError, TypeError):
            pass
        filters = info.filters or {}
        # Walk the catalogue in display order so re-opened DCs feel stable.
        for dim_id, dim in self._dimensions.items():
            if not any(k in filters for k in dim.filter_keys):
                continue
            row = self.add_filter_dimension(dim_id)
            row.facet.set_value(filters)

    # ----- accept gating + commit ---------------------------------------- #

    def _on_accept(self) -> None:
        if not self._name.text().strip():
            return                                                # gated empty
        slug = cut_names.slugify(self._name.text())
        err = cut_names.check_tag(slug, self._existing_tags)
        if err:
            # The host displays the error code → tr() message; here we just
            # refuse to accept so the user sees the warning text under the
            # name field.
            return
        self.saved.emit(self.info())
        self.accept()


__all__ = [
    "CrossEventDcInfo",
    "CrossEventInventories",
    "NewCrossEventDcDialog",
]
