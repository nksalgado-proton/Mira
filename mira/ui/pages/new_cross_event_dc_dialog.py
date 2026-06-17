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
from typing import Any, Callable, Dict, List, Optional, Sequence

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
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core import collection_resolver, cut_names
from mira.ui.design import line_input, primary_button, ghost_button
from mira.ui.i18n import tr


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
    """A row of pill-style checkboxes (multi-select). ``key`` is the
    ``filters_json`` key (e.g. ``"styles"``, ``"camera_ids"``). Empty
    selection means "no narrowing" — the value is dropped from the
    fragment (forward-compat-friendly)."""

    def __init__(self, key: str, options: Sequence[str],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._key = key
        self._boxes: List[QCheckBox] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for opt in options:
            cb = QCheckBox(opt, self)
            cb.toggled.connect(lambda _=False: self.changed.emit())
            self._boxes.append(cb)
            layout.addWidget(cb)
        layout.addStretch()

    def value(self) -> Dict[str, Any]:
        picked = [cb.text() for cb in self._boxes if cb.isChecked()]
        return {self._key: picked} if picked else {}

    def set_value(self, fragment: Dict[str, Any]) -> None:
        picked = set(fragment.get(self._key, []) or [])
        for cb in self._boxes:
            cb.blockSignals(True)
            cb.setChecked(cb.text() in picked)
            cb.blockSignals(False)
        self.changed.emit()


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
    """Capture date range — two ISO-date text inputs. Empty = no constraint
    on that end. Validation is light (any non-empty string passes through;
    the SQL layer's ``BETWEEN`` does the real work)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
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
            out["capture_from"] = f
        if t:
            out["capture_to"] = t
        return out

    def set_value(self, fragment: Dict[str, Any]) -> None:
        self._from.blockSignals(True)
        self._to.blockSignals(True)
        self._from.setText(fragment.get("capture_from", "") or "")
        self._to.setText(fragment.get("capture_to", "") or "")
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
    """Build a cross-event Dynamic Collection (spec/81 §2.1 + spec/32 §2).

    Constructor takes the inventories (host pulls from :class:`LibraryGateway`)
    and an optional ``dc_probe`` callable for the live count. Tests can pass
    a stub probe; the host wires it to :meth:`LibraryGateway.dc_probe`.

    Public surface:
        * :meth:`info` → :class:`CrossEventDcInfo` (the host's commit input).
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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("New cross-event collection"))
        self.setMinimumWidth(640)
        self._inventories = inventories
        self._dc_probe = dc_probe or (lambda _expr, _filters: 0)
        self._existing_tags = list(existing_tags)
        self._facets: List[_Facet] = []

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

        # Scrollable body: origin + filter facets.
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

        # Curatorial group
        body_l.addWidget(self._build_section(
            tr("Style"), self._make_multi("styles")))
        body_l.addWidget(self._build_section(
            tr("Media type"),
            self._make_single("media_type", [
                (tr("Both"), "both"),
                (tr("Photos only"), "photo"),
                (tr("Videos only"), "video"),
            ])))
        body_l.addWidget(self._build_section(
            tr("Rating (stars)"), self._make_stars_min()))
        body_l.addWidget(self._build_section(
            tr("Color label"), self._make_multi("color_labels")))
        body_l.addWidget(self._build_section(
            tr("Portfolio flag"),
            self._make_single("flag", [
                (tr("Any"), None),
                (tr("Flagged"), True),
                (tr("Not flagged"), False),
            ])))

        # Hardware
        body_l.addWidget(self._build_section(
            tr("Camera"), self._make_multi("camera_ids")))
        body_l.addWidget(self._build_section(
            tr("Lens"), self._make_multi("lens_models")))
        body_l.addWidget(self._build_section(
            tr("Flash"),
            self._make_single("flash_fired", [
                (tr("Any"), None),
                (tr("Flash fired"), True),
                (tr("No flash"), False),
            ])))

        # Settings (exposure triangle + focal)
        body_l.addWidget(self._build_section(
            tr("ISO"),
            self._make_range("iso_min", "iso_max",
                             integer=True, lo=50, hi=409600, step=100)))
        body_l.addWidget(self._build_section(
            tr("Aperture (f/)"),
            self._make_range("aperture_min", "aperture_max",
                             integer=False, lo=0.7, hi=64.0,
                             step=0.1, decimals=1)))
        body_l.addWidget(self._build_section(
            tr("Shutter (s)"),
            self._make_range("shutter_min", "shutter_max",
                             integer=False, lo=0.00001, hi=3600.0,
                             step=0.1, decimals=3)))
        body_l.addWidget(self._build_section(
            tr("Focal length (mm)"),
            self._make_range("focal_min", "focal_max",
                             integer=False, lo=4.0, hi=2000.0,
                             step=1.0, decimals=1)))

        # Temporal + location
        body_l.addWidget(self._build_section(
            tr("Capture date"), self._make_date_range()))
        body_l.addWidget(self._build_section(
            tr("Country"), self._make_multi("country_codes")))
        body_l.addWidget(self._build_section(
            tr("City"), self._make_multi("cities")))

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

    # ----- facet factories — register each so refresh_count walks them ---- #

    def _make_multi(self, key: str) -> _MultiSelectFacet:
        """Build a multi-select facet by pulling its vocabulary lazily from
        :attr:`_inventories` (spec/83 §5). The current dialog still iterates
        every facet at construction; slice 3 will defer this until the user
        adds the filter."""
        pairs = self._inventories.for_key(key)
        options = [str(v) for v, _ in pairs]
        w = _MultiSelectFacet(key, options)
        w.changed.connect(self._refresh_count)
        self._facets.append(w)
        return w

    def _make_single(self, key: str,
                     options: Sequence[tuple]) -> _SingleSelectFacet:
        w = _SingleSelectFacet(key, list(options))
        w.changed.connect(self._refresh_count)
        self._facets.append(w)
        return w

    def _make_range(self, min_key: str, max_key: str, *,
                    integer: bool, lo: float, hi: float,
                    step: float, decimals: int = 0) -> _NumberRangeFacet:
        w = _NumberRangeFacet(min_key, max_key,
                              integer=integer, lo=lo, hi=hi,
                              step=step, decimals=decimals)
        w.changed.connect(self._refresh_count)
        self._facets.append(w)
        return w

    def _make_stars_min(self) -> _StarsMinFacet:
        w = _StarsMinFacet()
        w.changed.connect(self._refresh_count)
        self._facets.append(w)
        return w

    def _make_date_range(self) -> _DateRangeFacet:
        w = _DateRangeFacet()
        w.changed.connect(self._refresh_count)
        self._facets.append(w)
        return w

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
        else:
            self._count_label.setText(
                tr("{n} items match").format(n=n))

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
        """Pre-fill the dialog from an existing DC (Edit flow)."""
        self._name.setText(info.name)
        self._description.setText(info.description)
        # origin from expr[0][1] if available
        try:
            tok = info.expr[0][1]
            if isinstance(tok, str):
                self._origin.set_token(tok)
        except (IndexError, KeyError, TypeError):
            pass
        for facet in self._facets:
            facet.set_value(info.filters or {})

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
