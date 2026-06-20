"""Surface 13 successor — :class:`NewRecipeDialog` (spec/90 Phase 4).

Two faces, one widget (spec/90 §2.3):

* **Cut flavour** (``flavour="cut"``, ``show_scope=False``,
  ``show_hardware=False``, ``inventory_scope="event"``) — the audience-facing
  event Cut dialog. Renders Source + Filters (Style + Media only) + Rules +
  Otherwise + presentation. Scope is the current event and hidden;
  Camera / Lens / Faces are hidden by default (spec/90 §2.1, §4).
* **Collection flavour** (``flavour="collection"``, ``show_scope=True``,
  ``show_hardware=True``, ``inventory_scope="library"``) — the
  cross-event curation dialog. Renders all five sections; Scope sits at the
  top with event / Event-Collection / date-range chips; Camera / Lens /
  Faces join the Filters block (spec/90 §2.2, §4).

This module ships **Phase 4a only** — the widget skeleton plus the Source
section (chip + join-word sentence per spec/90 §3.1) and the Filters section
(Style + Media everywhere; Camera + Lens when ``show_hardware=True``).
Placeholder rows stand in for the not-yet-built sections so the visual
structure reads correctly and the layout settles where Phase 4b-e will fill
in. The legacy :mod:`mira.ui.pages.new_cut_dialog` stays in place; Phase 4e
swaps the entry points.

The widget's public surface matches the spec/90 §2.3 contract — four
boolean / enum flags pin the visible sections, the inventory + facets ride
the :class:`NewRecipeContext` dataclass, and live probes connect to the
gateway (``pool_probe`` / ``totals_probe``) for the metrics row Phase 4d
turns on. No template / save-as-Recipe wiring yet — those land in 4e.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from PyQt6.QtCore import QDate, QPoint, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QMouseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDateEdit,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mira.ui.design import (
    GLYPH_CROSS,
    GLYPH_CROSS_EVENT,
    GLYPH_CUT,
    ghost_button,
    line_input,
    pill_toggle,
    primary_button,
    tinted_svg_pixmap,
)
from mira.ui.i18n import tr
from mira.ui.palette import PALETTE

log = logging.getLogger(__name__)


# Flavour constants (mirror :data:`mira.shared.recipe_store.FLAVOUR_*`).
FLAVOUR_CUT = "cut"
FLAVOUR_COLLECTION = "collection"

# Inventory scopes — event-only operands vs the library-wide catalogue.
INVENTORY_EVENT = "event"
INVENTORY_LIBRARY = "library"

# Join words (spec/90 §3.2). Phase 4c hooks each rendered join word to
# :class:`_JoinWordPopover` so the user can swap one-click between the
# three set-algebra meanings.
JOIN_OR = "or"
JOIN_AND = "and"
JOIN_BUT_NOT = "but not in"

# Mapping the join word ↔ the spec/81 resolver operator. The picker
# widget emits join words; the source / scope / rule-predicate encoders
# translate here.
_JOIN_TO_OP = {JOIN_OR: "+", JOIN_AND: "&", JOIN_BUT_NOT: "-"}

#: Ordered (join, plain-language description) pairs the join-word popover
#: renders (spec/90 §1.2 + §3.2). Order pins the user-facing presentation.
JOIN_WORD_OPTIONS: Tuple[Tuple[str, str], ...] = (
    (JOIN_OR, "items in either set"),
    (JOIN_AND, "items in both sets"),
    (JOIN_BUT_NOT, "exclude these"),
)

# Verdicts (spec/90 §1.3). pick = items matched start picked; skip = items
# matched start skipped. The Otherwise row carries the default-when-no-rule-
# matched verdict; every Rule carries a per-rule verdict.
VERDICT_PICK = "pick"
VERDICT_SKIP = "skip"
VERDICTS: Tuple[str, ...] = (VERDICT_PICK, VERDICT_SKIP)

#: Ordered (verdict, plain-language description) pairs the verb popover
#: renders (spec/90 §3.3).
VERB_OPTIONS: Tuple[Tuple[str, str], ...] = (
    (VERDICT_PICK, "items matched by this rule start picked"),
    (VERDICT_SKIP, "items matched by this rule start skipped"),
)


def _palette_mode() -> str:
    app = QApplication.instance()
    return (app.property("theme") if app else None) or "dark"


def _micro(text: str) -> QLabel:
    """Micro section header — Faint, all-caps. Same role as
    :func:`mira.ui.pages.new_cut_dialog._micro`."""
    lbl = QLabel(text.upper())
    lbl.setObjectName("Micro")
    return lbl


def _divider() -> QFrame:
    d = QFrame()
    d.setObjectName("DialogDivider")
    return d


def _placeholder(text: str, *, object_name: str = "Faint") -> QLabel:
    """A grey label standing in for a section Phase 4b-d will build."""
    lbl = QLabel(text)
    lbl.setObjectName(object_name)
    return lbl


# --------------------------------------------------------------------------- #
# Context
# --------------------------------------------------------------------------- #


@dataclass
class OperandOption:
    """One operand the user can drop into a Source / Scope / Rule predicate.

    spec/90 §3.1 alphabet: base universes, DCs, Cuts, Event Collections,
    Events, Date ranges, Persons, hardware vocabulary, item vocabulary.
    Each chip in the dialog renders one of these.

    Optional fields support the kinds Phase 4b adds for Scope:

    * ``uuid`` — set for ``kind='event'`` operands; the encoded operand
      becomes ``{"kind": "event", "uuid": ...}``.
    * ``start`` / ``end`` — set for ``kind='date_range'`` operands; the
      encoded operand becomes ``{"kind": "date_range", "start": ...,
      "end": ...}``. Date strings are ISO-8601 (``YYYY-MM-DD``).
    """

    name: str               # display string, e.g. '#exported' or '[Alaska]'
    count: int = 0          # live count beside the name (spec/90 §3.4)
    # 'base' | 'dc' | 'cut' | 'event_collection' | 'event' | 'date_range'
    kind: str = "base"
    id: Optional[str] = None
    tag: Optional[str] = None  # canonical tag without '#'; falls back to ``name``
    uuid: Optional[str] = None       # event operand identity
    start: Optional[str] = None      # date_range start (YYYY-MM-DD)
    end: Optional[str] = None        # date_range end (YYYY-MM-DD)


@dataclass
class NewRecipeContext:
    """Prefill + inventory data the dialog reads on first paint.

    Phase 4a fields only — the resolver / Recipe-load wiring lands in
    Phase 4b-e and grows this class with rule list + scope chips + the
    cross-flavour filter facets. Keeping the field set narrow now makes
    the swap visible (a missing field surfaces at the dialog's edge,
    not deep in a panel)."""

    event_name: str = ""
    name: str = ""

    # Source operand inventory (spec/90 §3.4 picker). Base universes,
    # DCs, Cuts. Persons join the picker in Phase 4c via the same shape
    # (Person operand in rule predicates).
    available_pools: List[OperandOption] = field(default_factory=list)

    # Scope operand inventory (spec/90 §3.1 — Collection-face only).
    # Events list comes from :meth:`LibraryGateway.list_events_for_scope`;
    # Event Collections from :meth:`EventCollectionStore.list`.
    available_events: List[OperandOption] = field(default_factory=list)
    available_event_collections: List[OperandOption] = field(
        default_factory=list)

    # People catalogue for the Faces section of the rule-predicate picker
    # (spec/90 §4.3 — Person chips inside rule predicates). Empty until
    # face recognition (spec/91) ships, but the picker section lights up as
    # soon as the People-page wiring populates this list.
    available_people: List[OperandOption] = field(default_factory=list)

    # Filter vocabularies (spec/90 §4). Each is the list of distinct
    # values the picker offers; the user multi-selects.
    available_styles: List[str] = field(default_factory=list)
    available_cameras: List[str] = field(default_factory=list)
    available_lenses: List[str] = field(default_factory=list)

    # Initial selections — empty for a fresh Recipe; populated when
    # loading a saved Recipe (Phase 4e).
    selected_source: List[Tuple[str, OperandOption]] = field(default_factory=list)
    selected_scope: List[Tuple[str, OperandOption]] = field(default_factory=list)
    # Each rule is a (predicate, verdict) pair. predicate uses the same
    # (join, operand) tuple list as source / scope; verdict is one of
    # :data:`VERDICTS`.
    selected_rules: List[Tuple[List[Tuple[str, OperandOption]], str]] = field(
        default_factory=list)
    otherwise: str = VERDICT_SKIP   # spec/90 §1.3 default-when-no-rule-matches
    selected_styles: List[str] = field(default_factory=list)
    selected_cameras: List[str] = field(default_factory=list)
    selected_lenses: List[str] = field(default_factory=list)
    include_photos: bool = True
    include_videos: bool = True


# --------------------------------------------------------------------------- #
# Source-section chips + picker popover
# --------------------------------------------------------------------------- #


class _SourceChip(QFrame):
    """Selected-operand chip in the Source sentence.

    Visually mirrors :class:`mira.ui.pages.new_cut_dialog._PoolChip` so the
    Phase 4e swap doesn't shift any pixels. Uses the global
    ``QFrame#PoolChipHost`` QSS rule (card2 bg + line border + 14px
    radius); :attr:`Qt.WidgetAttribute.WA_StyledBackground` keeps the
    cascade reaching nested under the scroll area.

    Phase 4a affordance: one ``×`` button removes the chip. The +/− / ∩
    steppers from the legacy ``_PoolChip`` retire — the rule-list grammar
    handles those via the join-word dropdown that Phase 4c adds between
    chips, so the chips themselves stay simple."""

    removed = pyqtSignal()

    def __init__(self, label: str, count: int,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("PoolChipHost")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumHeight(30)
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 4, 6, 4)
        h.setSpacing(6)
        name_lbl = QLabel(label, self)
        name_lbl.setObjectName("PoolChipName")
        h.addWidget(name_lbl)
        if count:
            count_lbl = QLabel(f"({count})", self)
            count_lbl.setObjectName("PoolChipCount")
            h.addWidget(count_lbl)
        close = QPushButton("×", self)
        close.setObjectName("PoolStepperBtn")
        close.setFixedSize(22, 22)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setToolTip(tr("Remove this operand from the source."))
        close.clicked.connect(self.removed.emit)
        h.addWidget(close)


#: Target identifiers for :class:`_OperandPickerPopover`. The Source picker
#: surfaces item-set operands (Base / DCs / Cuts); the Scope picker surfaces
#: event-set operands (Events / Event Collections / Date ranges).
PICKER_TARGET_SOURCE = "source"
PICKER_TARGET_SCOPE = "scope"
PICKER_TARGET_RULE_PREDICATE = "rule_predicate"


# --------------------------------------------------------------------------- #
# Join-word + verb popovers (spec/90 §3.2 + §3.3)
# --------------------------------------------------------------------------- #


class _ChoicePopover(QFrame):
    """Shared base for the small two-/three-option popovers Phase 4c adds.

    Each option renders as a left-aligned button with the choice token in
    bold + a faint plain-language description per spec/90 §1.2 / §3.3.
    The currently-selected option carries a ``selected="true"`` Qt property
    so the QSS cascade can mark it (the rule itself lives in the project's
    theme stylesheets; the widget only sets the property).

    Subclasses pass the option list, the currently-selected key, and a
    ``role`` object name that QSS targets ("JoinWordPopover" / "VerbPopover")."""

    chosen = pyqtSignal(str)

    def __init__(
        self,
        options: Sequence[Tuple[str, str]],
        *,
        selected: Optional[str] = None,
        role: str = "ChoicePopover",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName(role)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(220)
        self._rows: Dict[str, QPushButton] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(2)

        for key, description in options:
            btn = QPushButton(self)
            btn.setObjectName(f"{role}Row")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("text-align: left; padding: 6px 10px;")
            # Two-line label: the key in bold + a faint plain-language
            # description (§1.2). HTML keeps the layout flexible without
            # nesting widgets.
            btn.setText(f"<b>{key}</b><br/><span style='color:#888'>"
                        f"{tr(description)}</span>")
            btn.setProperty("_key", key)
            btn.setProperty("selected", "true" if key == selected else "false")
            btn.clicked.connect(
                lambda _checked=False, k=key: self._on_chosen(k))
            self._rows[key] = btn
            outer.addWidget(btn)

    def _on_chosen(self, key: str) -> None:
        self.chosen.emit(key)
        self.close()


class _JoinWordPopover(_ChoicePopover):
    """spec/90 §3.2 — pick between ``or`` / ``and`` / ``but not in``.

    Opens on click of a :class:`_JoinChevron` between two chips."""

    def __init__(
        self,
        *,
        selected: str = JOIN_OR,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(
            JOIN_WORD_OPTIONS,
            selected=selected,
            role="JoinWordPopover",
            parent=parent,
        )


class _VerbPopover(_ChoicePopover):
    """spec/90 §3.3 — pick between ``pick`` / ``skip``.

    Opens on click of a :class:`_VerdictPill` (Rules row or Otherwise row)."""

    def __init__(
        self,
        *,
        selected: str = VERDICT_SKIP,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(
            VERB_OPTIONS,
            selected=selected,
            role="VerbPopover",
            parent=parent,
        )


class _JoinChevron(QPushButton):
    """The clickable join-word affordance between two chips (spec/90 §3.2).

    Renders as ``or ⌄`` (or the currently-selected word + the chevron) and
    opens :class:`_JoinWordPopover` anchored below it on click. Emits
    :attr:`chosen` with the new join word when the user picks one.

    Visually it's small inline text — uses the existing ``PoolFormulaOp``
    QSS role for the colour treatment so Phases 4a/4b's static labels
    swap in pixel-for-pixel."""

    chosen = pyqtSignal(str)

    def __init__(self, join: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("PoolFormulaOp")
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tr("Swap the join word between or / and / but not in."))
        self._join = join
        self._render()
        self.clicked.connect(self._open_popover)

    def join_word(self) -> str:
        return self._join

    def set_join_word(self, join: str) -> None:
        self._join = join
        self._render()

    def _render(self) -> None:
        # Trailing chevron ⌄ marks the affordance; the join word itself is
        # the leading text per the spec's "small text with a chevron".
        self.setText(f"{self._join} ⌄")

    def _open_popover(self) -> None:
        popover = _JoinWordPopover(selected=self._join, parent=self)
        popover.chosen.connect(self._on_chosen)
        pos = self.mapToGlobal(self.rect().bottomLeft())
        popover.move(pos)
        popover.show()
        self._popover = popover                            # keep-alive

    def _on_chosen(self, join: str) -> None:
        self.set_join_word(join)
        self.chosen.emit(join)


class _VerdictPill(QPushButton):
    """The clickable verdict-pill affordance (spec/90 §3.3).

    Renders as a green pill for ``pick`` or a red pill for ``skip`` — the
    object name flips between ``VerdictPickPill`` and ``VerdictSkipPill``
    so the QSS cascade colours each appropriately. Opens
    :class:`_VerbPopover` anchored below on click; emits :attr:`chosen`
    with the new verdict when the user picks one."""

    chosen = pyqtSignal(str)

    def __init__(
        self, verdict: str = VERDICT_SKIP,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tr("Click to swap between pick (green) and skip (red)."))
        self._verdict = verdict if verdict in VERDICTS else VERDICT_SKIP
        self._render()
        self.clicked.connect(self._open_popover)

    def verdict(self) -> str:
        return self._verdict

    def set_verdict(self, verdict: str) -> None:
        if verdict not in VERDICTS:
            return
        self._verdict = verdict
        self._render()

    def _render(self) -> None:
        self.setText(tr(self._verdict))
        self.setObjectName(
            "VerdictPickPill" if self._verdict == VERDICT_PICK
            else "VerdictSkipPill")
        # Re-polish so the QSS cascade picks up the object name change.
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)

    def _open_popover(self) -> None:
        popover = _VerbPopover(selected=self._verdict, parent=self)
        popover.chosen.connect(self._on_chosen)
        pos = self.mapToGlobal(self.rect().bottomLeft())
        popover.move(pos)
        popover.show()
        self._popover = popover                            # keep-alive

    def _on_chosen(self, verdict: str) -> None:
        self.set_verdict(verdict)
        self.chosen.emit(verdict)


class _OperandPickerPopover(QFrame):
    """Sectioned popover for picking an operand to add (spec/90 §3.4).

    Floats over the dialog body as a small modal frame (anchored under the
    ``+`` button). The picker has two **targets** (one widget, two
    inventories):

    * ``PICKER_TARGET_SOURCE`` — Source sentence picker. Sections are
      Base universes · Dynamic Collections · Cuts. The "Save as DC…"
      affordance sits at the bottom (spec/90 §3.4 — opens the new-DC
      sub-dialog with the current Source's chips pre-filled).
    * ``PICKER_TARGET_SCOPE`` — Scope sentence picker (Collection face
      only). Sections are Events · Event Collections · Date ranges
      (spec/90 §3.1, §3.4). The Date ranges section is a single
      ``+ Add date range…`` button that opens
      :class:`_DateRangePickerPopover`. "Save as DC…" is hidden — Scope
      doesn't compose into a Dynamic Collection.

    A search line input at the top narrows by name; live counts ride
    beside every entry from :attr:`OperandOption.count`. Empty sections
    silently disappear so the picker stays compact when the user has no
    DCs / Cuts / Event Collections."""

    chosen = pyqtSignal(object)              # OperandOption
    save_as_dc_requested = pyqtSignal()
    add_date_range_requested = pyqtSignal()

    def __init__(
        self,
        pools: Sequence[OperandOption] = (),
        *,
        target: str = PICKER_TARGET_SOURCE,
        events: Sequence[OperandOption] = (),
        event_collections: Sequence[OperandOption] = (),
        people: Sequence[OperandOption] = (),
        show_faces: bool = False,
        # Legacy alias from Phase 4a — accepted for back-compat. Maps
        # to ``target=PICKER_TARGET_SCOPE`` when True.
        show_event_collections: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        # Popup frame: borderless, modal-feeling, dismisses on outside click.
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("OperandPickerPopover")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(280)
        if show_event_collections and target == PICKER_TARGET_SOURCE:
            # Phase 4a callers passed show_event_collections=True to opt
            # into Event Collections in the picker. Phase 4b's Scope target
            # is the proper home for that vocabulary.
            target = PICKER_TARGET_SCOPE
        if target not in (
            PICKER_TARGET_SOURCE,
            PICKER_TARGET_SCOPE,
            PICKER_TARGET_RULE_PREDICATE,
        ):
            raise ValueError(
                f"picker target must be 'source' / 'scope' / "
                f"'rule_predicate', got {target!r}")
        self._target = target
        self._pools = list(pools)
        self._events = list(events)
        self._event_collections = list(event_collections)
        self._people = list(people)
        self._show_faces = bool(show_faces)
        self._rows: List[Tuple[OperandOption, QPushButton]] = []
        self._date_range_row: Optional[QPushButton] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        self._search = line_input(tr("Search operands…"))
        self._search.setObjectName("OperandPickerSearch")
        self._search.textChanged.connect(self._refilter)
        outer.addWidget(self._search)

        self._list_host = QWidget()
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(2)
        outer.addWidget(self._list_host)

        self._populate_sections()

        # "Save as DC…" only applies to the Source picker — Scope's
        # output is event sets, not item sets, and a rule predicate's
        # output is a per-item match set the resolver consumes once. The
        # rule-predicate picker also hides it.
        if self._target == PICKER_TARGET_SOURCE:
            outer.addWidget(_divider())
            self._save_btn = ghost_button(tr("Save as DC…"))
            self._save_btn.setObjectName("OperandPickerSaveAsDc")
            self._save_btn.setToolTip(tr(
                "Save the current source as a Dynamic Collection — Phase 4e."))
            self._save_btn.clicked.connect(self._on_save_as_dc)
            outer.addWidget(self._save_btn)
        else:
            self._save_btn = None

    def _populate_sections(self) -> None:
        """Render the section headers + rows for the active target.

        Source target (spec/90 §3.4): Base universes · Dynamic
        Collections · Cuts — pulled from ``pools`` grouped by kind.

        Scope target (spec/90 §3.4 — Collection dialog only): Events ·
        Event Collections · Date ranges. The Date ranges section has
        no inventory; instead it shows one ``+ Add date range…`` button
        that opens :class:`_DateRangePickerPopover`.

        Rule-predicate target (spec/90 §3.5, §4.3): same item-set
        alphabet as Source (Base · DCs · Cuts) plus a **Faces** section
        when the dialog enables hardware filters AND the people catalog
        has entries. Person chips in rule predicates are the §4.3
        advanced affordance."""
        if self._target == PICKER_TARGET_SCOPE:
            self._populate_scope_sections()
            return

        order = [
            ("base", tr("Base universes")),
            ("dc", tr("Dynamic Collections")),
            ("cut", tr("Cuts")),
        ]
        for kind, label in order:
            in_kind = [p for p in self._pools if p.kind == kind]
            if not in_kind:
                continue
            header = _micro(label)
            self._list_layout.addWidget(header)
            for pool in in_kind:
                row = self._make_row(pool)
                self._list_layout.addWidget(row)
                self._rows.append((pool, row))

        # Faces ride the rule-predicate target only (spec/90 §4.3) — Source
        # items have no Person-membership concept; rule predicates are the
        # one path Person chips enter.
        if (
            self._target == PICKER_TARGET_RULE_PREDICATE
            and self._show_faces
            and self._people
        ):
            self._list_layout.addWidget(_micro(tr("Faces")))
            for person in self._people:
                row = self._make_row(person)
                self._list_layout.addWidget(row)
                self._rows.append((person, row))

    def _populate_scope_sections(self) -> None:
        """The Scope picker's three sections (spec/90 §3.1)."""
        if self._events:
            self._list_layout.addWidget(_micro(tr("Events")))
            for ev in self._events:
                row = self._make_row(ev)
                self._list_layout.addWidget(row)
                self._rows.append((ev, row))
        if self._event_collections:
            self._list_layout.addWidget(_micro(tr("Event Collections")))
            for ec in self._event_collections:
                row = self._make_row(ec)
                self._list_layout.addWidget(row)
                self._rows.append((ec, row))

        # Date ranges section — single "+ Add date range…" button that
        # opens the date-range picker.
        self._list_layout.addWidget(_micro(tr("Date ranges")))
        btn = QPushButton(tr("+ Add date range…"), self)
        btn.setObjectName("OperandPickerAddDateRange")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet("text-align: left; padding: 6px 8px;")
        btn.clicked.connect(self._on_add_date_range)
        self._list_layout.addWidget(btn)
        self._date_range_row = btn

    def _make_row(self, pool: OperandOption) -> QPushButton:
        """One entry row — the name on the left, the live count on the
        right, in one tappable button. Mirrors the legacy add-row chip
        shape so the visual idiom carries over."""
        text = f"{pool.name}    ({pool.count})" if pool.count else pool.name
        btn = QPushButton(text, self)
        btn.setObjectName("OperandPickerRow")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet("text-align: left; padding: 6px 8px;")
        btn.clicked.connect(lambda _checked=False, p=pool: self._on_chosen(p))
        return btn

    def _refilter(self, text: str) -> None:
        needle = (text or "").strip().lower()
        for pool, row in self._rows:
            row.setVisible(not needle or needle in pool.name.lower())

    def _on_chosen(self, pool: OperandOption) -> None:
        self.chosen.emit(pool)
        self.close()

    def _on_save_as_dc(self) -> None:
        self.save_as_dc_requested.emit()
        self.close()

    def _on_add_date_range(self) -> None:
        """Click on the Scope picker's ``+ Add date range…`` row.

        Doesn't ship the chip itself — the dialog owns the date-range
        picker (it's bound to the dialog's parent + the current QDate
        defaults). The picker emits :attr:`add_date_range_requested`
        and closes; the dialog opens :class:`_DateRangePickerPopover`
        and adds the resulting chip on confirm."""
        self.add_date_range_requested.emit()
        self.close()


# --------------------------------------------------------------------------- #
# Date-range picker (spec/90 §3.1 — Scope's "date range" operand)
# --------------------------------------------------------------------------- #


def _today() -> date:
    """Wall-clock today, injected as a callable so tests can freeze it.
    The class consults the module-level reference so a monkeypatch hits
    every instance in the same process."""
    return datetime.now(timezone.utc).date()


def _iso(d: date) -> str:
    return d.isoformat()


@dataclass(frozen=True)
class DateRangeQuickSelect:
    """One quick-select preset in :class:`_DateRangePickerPopover`."""

    label: str
    years: Optional[int]   # None = all-time (no lower bound — opens 1900-01-01)


_DEFAULT_QUICK_SELECTS: Tuple[DateRangeQuickSelect, ...] = (
    DateRangeQuickSelect(label="Last 12 months", years=1),
    DateRangeQuickSelect(label="Last 3 years", years=3),
    DateRangeQuickSelect(label="Last 5 years", years=5),
    DateRangeQuickSelect(label="All time", years=None),
)
#: Lower bound for "All time" — spec/90 §3.1's date-range operand is
#: bounded both sides; the resolver treats the start as inclusive.
_ALL_TIME_START = date(1900, 1, 1)


class _DateRangePickerPopover(QDialog):
    """Modal-ish dialog for picking a date-range operand (spec/90 §3.1 —
    Scope only). Two :class:`QDateEdit`'s + a row of quick-selects + OK /
    Cancel.

    Doesn't enforce ``start <= end`` — the dialog re-orders on confirm so
    the emitted operand always carries the lower date as ``start``."""

    range_chosen = pyqtSignal(str, str)   # (start_iso, end_iso)

    def __init__(
        self,
        *,
        start: Optional[date] = None,
        end: Optional[date] = None,
        quick_selects: Sequence[DateRangeQuickSelect] = _DEFAULT_QUICK_SELECTS,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("DateRangePicker")
        self.setWindowTitle(tr("Add date range"))
        self.setModal(True)
        self.setMinimumWidth(360)

        today = _today()
        start = start or date(today.year - 1, today.month, today.day)
        end = end or today

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(12)

        # Two QDateEdits in a labelled grid-ish row.
        fields = QHBoxLayout()
        fields.setSpacing(12)
        start_col = QVBoxLayout()
        start_col.addWidget(_micro(tr("Start")))
        self._start_edit = QDateEdit()
        self._start_edit.setObjectName("DateRangeStart")
        self._start_edit.setCalendarPopup(True)
        self._start_edit.setDisplayFormat("yyyy-MM-dd")
        self._start_edit.setDate(QDate(start.year, start.month, start.day))
        start_col.addWidget(self._start_edit)
        end_col = QVBoxLayout()
        end_col.addWidget(_micro(tr("End")))
        self._end_edit = QDateEdit()
        self._end_edit.setObjectName("DateRangeEnd")
        self._end_edit.setCalendarPopup(True)
        self._end_edit.setDisplayFormat("yyyy-MM-dd")
        self._end_edit.setDate(QDate(end.year, end.month, end.day))
        end_col.addWidget(self._end_edit)
        fields.addLayout(start_col, 1)
        fields.addLayout(end_col, 1)
        outer.addLayout(fields)

        # Quick-select chips. Buttons rather than pill_toggles because
        # they fire-and-fill rather than carry state.
        self._quick_buttons: List[QPushButton] = []
        outer.addWidget(_micro(tr("Quick selects")))
        quick_row = QHBoxLayout()
        quick_row.setSpacing(6)
        for preset in quick_selects:
            btn = ghost_button(tr(preset.label))
            btn.setObjectName("DateRangeQuickSelect")
            btn.clicked.connect(
                lambda _checked=False, p=preset: self._apply_quick_select(p))
            quick_row.addWidget(btn)
            self._quick_buttons.append(btn)
        quick_row.addStretch()
        outer.addLayout(quick_row)

        # OK / Cancel.
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = ghost_button(tr("Cancel"))
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        self._ok_btn = primary_button(tr("OK"))
        self._ok_btn.setObjectName("DateRangeOk")
        self._ok_btn.clicked.connect(self._on_ok)
        actions.addWidget(self._ok_btn)
        outer.addLayout(actions)

    def _apply_quick_select(self, preset: DateRangeQuickSelect) -> None:
        """Quick-select math: ``years=N`` means "last N years from
        today" — start is N years ago, end is today. ``years=None`` is
        all-time, anchored at :data:`_ALL_TIME_START`."""
        today = _today()
        end = today
        if preset.years is None:
            start = _ALL_TIME_START
        else:
            try:
                start = today.replace(year=today.year - preset.years)
            except ValueError:
                # Feb 29 in a non-leap target year. Slide to Feb 28.
                start = today.replace(year=today.year - preset.years, day=28)
        self._start_edit.setDate(QDate(start.year, start.month, start.day))
        self._end_edit.setDate(QDate(end.year, end.month, end.day))

    def _on_ok(self) -> None:
        start_q = self._start_edit.date()
        end_q = self._end_edit.date()
        start_d = date(start_q.year(), start_q.month(), start_q.day())
        end_d = date(end_q.year(), end_q.month(), end_q.day())
        if start_d > end_d:
            start_d, end_d = end_d, start_d
        self.range_chosen.emit(_iso(start_d), _iso(end_d))
        self.accept()


# --------------------------------------------------------------------------- #
# Rules section — :class:`_RuleRow` + the dialog-side wiring
# --------------------------------------------------------------------------- #


class _RuleDragHandle(QPushButton):
    """The ``≡`` grip on a rule row (spec/90 §1.3 — "user re-orders rules
    by drag"). Captures mouse press, fires :attr:`drag_pressed` so the
    parent :class:`_RuleRow` can route the gesture to the dialog's
    reorder seam."""

    drag_pressed = pyqtSignal(QPoint)       # global mouse position
    drag_released = pyqtSignal(QPoint)      # global mouse position

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("≡", parent)             # ≡
        self.setObjectName("RuleDragHandle")
        self.setFlat(True)
        self.setFixedSize(24, 28)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(tr("Drag to reorder this rule."))
        self._dragging = False

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.drag_pressed.emit(event.globalPosition().toPoint())
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.drag_released.emit(event.globalPosition().toPoint())
        super().mouseReleaseEvent(event)


class _RuleRow(QFrame):
    """One Rule's row (spec/90 §1.3).

    Layout, left → right:

    * ``≡`` drag handle (:class:`_RuleDragHandle`) for reorder.
    * Index number (``1.`` / ``2.`` / …).
    * Predicate sentence — same chip + join-word grammar as the Source
      and Scope rows, but a per-rule chip list. The trailing ``+``
      opens the operand picker with ``target='rule_predicate'``.
    * Verdict pill (:class:`_VerdictPill`) with the verb popover.
    * Match-count placeholder (``"(— match)"``); Phase 4d wires the
      live numbers.
    * Delete ``×`` button.

    Owns the per-rule state (predicate chips + verdict); the parent
    dialog provides callbacks for the operand picker and signals on
    state change so the dialog can re-emit :meth:`rules_expression`.
    """

    changed = pyqtSignal()                  # any state mutation
    delete_requested = pyqtSignal()
    add_operand_requested = pyqtSignal(object)  # the QPushButton anchor
    drag_pressed = pyqtSignal(int, QPoint)      # row index, global pos
    drag_released = pyqtSignal(int, QPoint)

    def __init__(
        self,
        index: int,
        predicate: Sequence[Tuple[str, OperandOption]] = (),
        verdict: str = VERDICT_SKIP,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("RuleRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._index = index
        self._predicate: List[Tuple[str, OperandOption]] = list(predicate)
        self._verdict = verdict if verdict in VERDICTS else VERDICT_SKIP

        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(8)

        # Drag handle.
        self._handle = _RuleDragHandle(self)
        self._handle.drag_pressed.connect(
            lambda pos: self.drag_pressed.emit(self._index, pos))
        self._handle.drag_released.connect(
            lambda pos: self.drag_released.emit(self._index, pos))
        outer.addWidget(self._handle)

        # Index label.
        self._index_label = QLabel(self)
        self._index_label.setObjectName("RuleIndex")
        self._index_label.setMinimumWidth(20)
        outer.addWidget(self._index_label)

        # "If items are in" lead + predicate sentence.
        lead = QLabel(tr("If items are in"))
        lead.setObjectName("RuleLead")
        outer.addWidget(lead)
        self._predicate_box = QWidget(self)
        self._predicate_row = QHBoxLayout(self._predicate_box)
        self._predicate_row.setContentsMargins(0, 0, 0, 0)
        self._predicate_row.setSpacing(6)
        outer.addWidget(self._predicate_box, 1)

        # Verdict pill.
        self._verdict_pill = _VerdictPill(self._verdict, self)
        self._verdict_pill.chosen.connect(self._on_verdict_chosen)
        outer.addWidget(self._verdict_pill)

        # Match-count placeholder (Phase 4d will wire the live number).
        self._match_label = QLabel(tr("(— match)"))
        self._match_label.setObjectName("RuleMatchCount")
        outer.addWidget(self._match_label)

        # Delete.
        delete = QPushButton("×", self)
        delete.setObjectName("PoolStepperBtn")
        delete.setFixedSize(22, 22)
        delete.setCursor(Qt.CursorShape.PointingHandCursor)
        delete.setToolTip(tr("Remove this rule."))
        delete.clicked.connect(self.delete_requested.emit)
        outer.addWidget(delete)

        self._refresh_index()
        self._refresh_predicate_row()

    # ----- public API ----------------------------------------------- #

    def set_index(self, index: int) -> None:
        self._index = index
        self._refresh_index()

    def predicate(self) -> List[Tuple[str, OperandOption]]:
        return list(self._predicate)

    def verdict(self) -> str:
        return self._verdict

    def set_verdict(self, verdict: str) -> None:
        if verdict in VERDICTS and verdict != self._verdict:
            self._verdict = verdict
            self._verdict_pill.set_verdict(verdict)
            self.changed.emit()

    def append_operand(self, operand: OperandOption) -> None:
        """Push a new operand onto the predicate; default join is ``or``."""
        self._predicate.append((JOIN_OR, operand))
        self._refresh_predicate_row()
        self.changed.emit()

    def remove_operand(self, chip_index: int) -> None:
        if 0 <= chip_index < len(self._predicate):
            self._predicate.pop(chip_index)
            self._refresh_predicate_row()
            self.changed.emit()

    def set_join(self, chip_index: int, join: str) -> None:
        if 0 < chip_index < len(self._predicate):
            _old_join, operand = self._predicate[chip_index]
            self._predicate[chip_index] = (join, operand)
            self._refresh_predicate_row()
            self.changed.emit()

    # ----- internal ------------------------------------------------- #

    def _refresh_index(self) -> None:
        self._index_label.setText(f"{self._index + 1}.")

    def _refresh_predicate_row(self) -> None:
        while self._predicate_row.count():
            item = self._predicate_row.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.deleteLater()

        for i, (join, operand) in enumerate(self._predicate):
            if i != 0:
                chevron = _JoinChevron(join, self._predicate_box)
                chevron.chosen.connect(
                    lambda new_join, idx=i: self.set_join(idx, new_join))
                self._predicate_row.addWidget(chevron)
            chip = _SourceChip(
                operand.name, operand.count, self._predicate_box)
            chip.removed.connect(
                lambda idx=i: self.remove_operand(idx))
            self._predicate_row.addWidget(chip)

        add = QPushButton("+", self._predicate_box)
        add.setObjectName("PoolStepperBtn")
        add.setFixedSize(24, 24)
        add.setCursor(Qt.CursorShape.PointingHandCursor)
        add.setToolTip(tr("Add an operand to this rule's predicate."))
        add.clicked.connect(
            lambda _=False: self.add_operand_requested.emit(add))
        self._predicate_row.addWidget(add)
        self._predicate_row.addStretch()

    def _on_verdict_chosen(self, verdict: str) -> None:
        # _VerdictPill already updated its own state; reflect in our model.
        if verdict in VERDICTS:
            self._verdict = verdict
            self.changed.emit()


# --------------------------------------------------------------------------- #
# The dialog
# --------------------------------------------------------------------------- #


class NewRecipeDialog(QDialog):
    """The New Cut / New Collection dialog (spec/90 §2 — two faces).

    Phase 4a ships the scaffold + Source + Filters. Rules / Otherwise /
    Metrics / Save-Load are placeholder rows that the next sub-phases
    fill in. The widget's section visibility is pinned by the four
    constructor flags so the same module renders both faces without
    a separate file per flavour (spec/90 §2.3)."""

    # spec/90 §3.4 — emitted when the picker's "Save as DC…" button is
    # clicked. The host wires the modal in Phase 4e; Phase 4a uses a
    # toast-shaped placeholder so the no-op is honest.
    save_as_dc_requested = pyqtSignal()

    def __init__(
        self,
        *,
        flavour: str,
        show_scope: bool,
        show_hardware: bool,
        inventory_scope: str,
        ctx: NewRecipeContext,
        pool_probe: Optional[Callable[[list], int]] = None,
        totals_probe: Optional[Callable] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        if flavour not in (FLAVOUR_CUT, FLAVOUR_COLLECTION):
            raise ValueError(
                f"flavour must be 'cut' or 'collection', got {flavour!r}")
        if inventory_scope not in (INVENTORY_EVENT, INVENTORY_LIBRARY):
            raise ValueError(
                f"inventory_scope must be 'event' or 'library', got "
                f"{inventory_scope!r}")

        self._flavour = flavour
        self._show_scope = bool(show_scope)
        self._show_hardware = bool(show_hardware)
        self._inventory_scope = inventory_scope
        self._ctx = ctx
        self._pool_probe = pool_probe
        self._totals_probe = totals_probe

        # Source state — the ordered chip list. Each entry is
        # ``(join, operand)`` where ``join`` is one of the spec/90 §3.2
        # words ('or' / 'and' / 'but not in'); the FIRST entry's join is
        # always treated as 'or' (the empty-accumulator union case) so
        # the first chip reads as the implicit "Start from …".
        self._source_chips: List[Tuple[str, OperandOption]] = list(
            ctx.selected_source or [])

        # Scope state — same shape, populated only when ``show_scope=True``
        # (Collection face). Empty for Cut-face Recipes (the Scope is
        # implicit = "this event"; spec/90 §1.1).
        self._scope_chips: List[Tuple[str, OperandOption]] = list(
            ctx.selected_scope or [])

        # Rules + Otherwise state (spec/90 §1.3). Each rule is
        # ``(predicate_chips, verdict)`` where ``predicate_chips`` is the
        # same ``(join, OperandOption)`` tuple list source / scope use.
        self._rules: List[Tuple[List[Tuple[str, OperandOption]], str]] = [
            (list(predicate), verdict)
            for predicate, verdict in (ctx.selected_rules or [])
        ]
        self._otherwise: str = (
            ctx.otherwise if ctx.otherwise in VERDICTS else VERDICT_SKIP
        )
        # Live row widgets — kept in lockstep with :attr:`_rules` so a
        # drag-reorder can shuffle pure data and then re-render once.
        self._rule_rows: List[_RuleRow] = []
        # Outstanding drag — populated by :class:`_RuleDragHandle`'s
        # press signal; consumed on release.
        self._dragging_rule_index: Optional[int] = None

        # Filter state. Pill-toggle chips + checkboxes drive these.
        self._style_chips: Dict[str, QPushButton] = {}
        self._camera_chips: Dict[str, QPushButton] = {}
        self._lens_chips: Dict[str, QPushButton] = {}
        self._photos_cb: Optional[QCheckBox] = None
        self._videos_cb: Optional[QCheckBox] = None

        # Window chrome — same title scheme as the legacy dialog.
        is_collection = flavour == FLAVOUR_COLLECTION
        self.setWindowTitle(
            tr("New Collection") if is_collection else tr("New Cut"))
        self.setModal(True)
        self.resize(660, 880)

        self._build_ui()

        if self._ctx.name:
            self._name_edit.setText(self._ctx.name)

        self._refresh_source_row()
        if self._show_scope:
            self._refresh_scope_row()
        self._refresh_rules_rows()

    # ------------------------------------------------------------------ #
    # Build
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header_bar())
        outer.addWidget(_divider())
        outer.addWidget(self._build_body(), 1)
        outer.addWidget(_divider())
        outer.addWidget(self._build_footer())

    def _build_header_bar(self) -> QWidget:
        host = QWidget()
        h = QHBoxLayout(host)
        h.setContentsMargins(18, 14, 14, 14)
        h.setSpacing(12)
        p = PALETTE[_palette_mode()]

        # Header icon: cut for the event face, cross-event for the
        # Collection face (spec/90 §2.1 vs §2.2 — different audiences).
        glyph = (
            GLYPH_CROSS_EVENT if self._flavour == FLAVOUR_COLLECTION
            else GLYPH_CUT
        )
        tile = QLabel()
        tile.setObjectName("CutHeaderTile")
        tile.setFixedSize(32, 32)
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setPixmap(tinted_svg_pixmap(glyph, 18, QColor(p["accent"])))
        h.addWidget(tile)

        block = QHBoxLayout()
        block.setSpacing(8)
        title = QLabel(
            tr("New Collection") if self._flavour == FLAVOUR_COLLECTION
            else tr("New Cut"))
        title.setObjectName("CardTitle")
        block.addWidget(title)
        if self._ctx.event_name:
            sub = QLabel(f"· {self._ctx.event_name}")
            sub.setObjectName("Sub")
            block.addWidget(sub)
        block.addStretch()
        h.addLayout(block, 1)

        # Load Recipe… — placeholder for Phase 4e.
        self._load_btn = ghost_button(tr("Load Recipe…"))
        self._load_btn.setEnabled(False)
        self._load_btn.setToolTip(tr(
            "Pre-fill every section from a saved Recipe — Phase 4e."))
        h.addWidget(self._load_btn)

        # Close X — same line-icon as the legacy dialog.
        close = QPushButton()
        close.setObjectName("DialogClose")
        close.setFixedSize(30, 30)
        close.setIcon(QIcon(
            tinted_svg_pixmap(GLYPH_CROSS, 14, QColor(p["ink_soft"]))))
        close.setIconSize(QSize(14, 14))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self.reject)
        h.addWidget(close)
        return host

    def _build_body(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(14)

        v.addWidget(self._build_name_section())
        if self._show_scope:
            v.addWidget(self._build_scope_section())
        v.addWidget(self._build_source_section())
        v.addWidget(self._build_filters_section())
        v.addWidget(self._build_rules_section())
        v.addWidget(self._build_otherwise_section())
        v.addWidget(self._build_metrics_section())

        v.addStretch()
        scroll.setWidget(inner)
        return scroll

    def _build_name_section(self) -> QWidget:
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        v.addWidget(_micro(tr("Name")))
        self._name_edit = line_input(tr("Type a name to see its tag."))
        v.addWidget(self._name_edit)
        self._name_tag_hint = QLabel("(tag will preview here)")
        self._name_tag_hint.setObjectName("Faint")
        self._name_edit.textChanged.connect(self._on_name_changed)
        v.addWidget(self._name_tag_hint)
        return host

    def _build_scope_section(self) -> QWidget:
        """The Scope sentence (spec/90 §1.1, §3.1) — Collection face only.

        Renders as ``Events: [chip] or [chip] …`` with a ``+`` button at
        the end that opens the Scope-target operand picker (Events ·
        Event Collections · Date ranges). Mirrors the Source section
        layout so the two sentences read the same.

        The Cut face hides this section entirely — Scope is implicit
        ("this event"); the resolver substitutes the current event for
        an empty composition.scope per spec/90 §1.1."""
        host = QWidget()
        host.setObjectName("ScopeSection")
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        v.addWidget(_micro(tr("Scope")))

        self._scope_box = QWidget()
        self._scope_row = QHBoxLayout(self._scope_box)
        self._scope_row.setContentsMargins(0, 0, 0, 0)
        self._scope_row.setSpacing(8)
        v.addWidget(self._scope_box)

        # Live summary — count of events the scope expression resolves
        # to. Phase 4b doesn't wire scope resolution yet (the resolver
        # takes pre-resolved uuids; the dialog-level scope evaluator
        # lands later), so this stays as a chip-count hint.
        self._scope_summary = QLabel("scope: 0 events")
        self._scope_summary.setObjectName("PoolSummary")
        v.addWidget(self._scope_summary)
        return host

    # -------- Source ------------------------------------------------- #

    def _build_source_section(self) -> QWidget:
        """The Source sentence (spec/90 §1.1, §3.1, §3.2). Renders as
        ``Start from [chip] or [chip] …`` with a ``+`` affordance at
        the end that opens the operand picker popover. Phase 4a does not
        render the join-word dropdown UI — every chip is joined with
        ``or`` by default; the picker / swap UI lands in Phase 4c."""
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        v.addWidget(_micro(tr("Source")))

        # The sentence row hosts the "Start from" lead label, chips +
        # join-word labels, and the trailing ``+`` button. Rebuilt on
        # every change so order changes are immediate.
        self._source_box = QWidget()
        self._source_row = QHBoxLayout(self._source_box)
        self._source_row.setContentsMargins(0, 0, 0, 0)
        self._source_row.setSpacing(8)
        v.addWidget(self._source_box)

        # Live summary — count of files this expression resolves to.
        # Probe is called on every chip add / remove (Phase 4d wires
        # the totals layer above).
        self._source_summary = QLabel("source: 0 files")
        self._source_summary.setObjectName("PoolSummary")
        v.addWidget(self._source_summary)
        return host

    def _refresh_source_row(self) -> None:
        """Rebuild the Source sentence — the ``Start from`` lead, each
        chip with its preceding join-word affordance, then the trailing
        ``+`` button. Phase 4c hooks each between-chip join word to
        :class:`_JoinChevron`, so the user can swap one-click between
        ``or`` / ``and`` / ``but not in`` (spec/90 §3.2)."""
        # Clear the row.
        while self._source_row.count():
            item = self._source_row.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.deleteLater()

        if not self._source_chips:
            lead = QLabel(tr("Start from"))
            lead.setObjectName("PoolAddLabel")
            self._source_row.addWidget(lead)
            self._source_row.addWidget(self._build_add_operand_button())
            self._source_row.addStretch()
            self._refresh_source_summary()
            return

        for index, (join, operand) in enumerate(self._source_chips):
            if index == 0:
                lead = QLabel(tr("Start from"))
                lead.setObjectName("PoolAddLabel")
                self._source_row.addWidget(lead)
            else:
                chevron = _JoinChevron(join, self._source_box)
                chevron.chosen.connect(
                    lambda new_join, i=index:
                    self._set_source_join(i, new_join))
                self._source_row.addWidget(chevron)
            chip = _SourceChip(operand.name, operand.count, self._source_box)
            chip.removed.connect(
                lambda i=index: self._remove_source_chip(i))
            self._source_row.addWidget(chip)

        self._source_row.addWidget(self._build_add_operand_button())
        self._source_row.addStretch()
        self._refresh_source_summary()

    def _set_source_join(self, index: int, join: str) -> None:
        """Swap one Source chip's preceding join word. ``index`` is the
        chip's position; the first chip's join is always treated as
        union by the encoder and the popover is never offered for it."""
        if 0 < index < len(self._source_chips):
            _old_join, operand = self._source_chips[index]
            self._source_chips[index] = (join, operand)
            self._refresh_source_row()

    def _build_add_operand_button(self) -> QPushButton:
        """The ``+`` affordance that opens the operand picker (spec/90 §3.4)."""
        btn = QPushButton("+", self._source_box)
        btn.setObjectName("PoolStepperBtn")
        btn.setFixedSize(26, 26)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tr("Add an operand — DC, Cut, or base universe."))
        btn.clicked.connect(lambda: self._open_source_picker(btn))
        return btn

    def _open_source_picker(self, anchor: QWidget) -> None:
        """Open the operand picker popover, anchored under the ``+`` button.

        Event Collections are admitted only on the Collection-face Scope
        section — Phase 4a's source picker (event or library) only
        offers Base + DC + Cut operands. The library-face Source DOES
        admit DC + Cut + Base from the library inventory; the picker
        re-reads ``ctx.available_pools`` so the caller is responsible
        for filtering to the right scope."""
        popover = _OperandPickerPopover(
            self._ctx.available_pools,
            show_event_collections=False,
            parent=self,
        )
        popover.chosen.connect(self._add_source_chip)
        popover.save_as_dc_requested.connect(self._on_save_as_dc_clicked)
        # Anchor: just below the button's bottom-left corner.
        pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
        popover.move(pos)
        popover.show()
        self._picker_popover = popover  # kept-alive while open

    def _add_source_chip(self, operand: OperandOption) -> None:
        """Append an operand to the Source sentence. The first chip's
        join is ``or`` by definition; later chips default to ``or`` too
        (the join-word dropdown in Phase 4c lets the user change it)."""
        join = JOIN_OR if not self._source_chips else JOIN_OR
        self._source_chips.append((join, operand))
        self._refresh_source_row()

    def _remove_source_chip(self, index: int) -> None:
        if 0 <= index < len(self._source_chips):
            self._source_chips.pop(index)
            self._refresh_source_row()

    def _refresh_source_summary(self) -> None:
        """Call ``pool_probe`` with the current expression; fall back to
        the chip count when no probe is wired (smoke tests)."""
        expr = self.source_expression()
        if self._pool_probe is not None:
            try:
                pool_n = int(self._pool_probe(expr))
            except Exception:                            # noqa: BLE001
                log.exception("pool_probe raised — using chip-count fallback")
                pool_n = sum(p.count for _, p in self._source_chips)
        else:
            pool_n = sum(p.count for _, p in self._source_chips)
        self._source_summary.setText(f"source: {pool_n} files")

    def _on_save_as_dc_clicked(self) -> None:
        """Placeholder — emits :attr:`save_as_dc_requested` so Phase 4e
        wires the actual save modal. For now, surface the no-op honestly
        via the existing toast / log path."""
        log.info("save_as_dc_requested — Phase 4e will wire this")
        self.save_as_dc_requested.emit()

    # -------- Scope (Collection face only) --------------------------- #

    def _refresh_scope_row(self) -> None:
        """Rebuild the Scope sentence — ``Events:`` lead label, each
        chip with its preceding join-word label, then the trailing
        ``+`` button. Same shape as :meth:`_refresh_source_row` so the
        two sentences read the same."""
        if not hasattr(self, "_scope_row"):
            return                                        # Cut face — no Scope
        while self._scope_row.count():
            item = self._scope_row.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.deleteLater()

        if not self._scope_chips:
            lead = QLabel(tr("Events:"))
            lead.setObjectName("PoolAddLabel")
            self._scope_row.addWidget(lead)
            self._scope_row.addWidget(self._build_add_scope_button())
            self._scope_row.addStretch()
            self._refresh_scope_summary()
            return

        for index, (join, operand) in enumerate(self._scope_chips):
            if index == 0:
                lead = QLabel(tr("Events:"))
                lead.setObjectName("PoolAddLabel")
                self._scope_row.addWidget(lead)
            else:
                chevron = _JoinChevron(join, self._scope_box)
                chevron.chosen.connect(
                    lambda new_join, i=index:
                    self._set_scope_join(i, new_join))
                self._scope_row.addWidget(chevron)
            chip = _SourceChip(operand.name, operand.count, self._scope_box)
            chip.removed.connect(
                lambda i=index: self._remove_scope_chip(i))
            self._scope_row.addWidget(chip)

        self._scope_row.addWidget(self._build_add_scope_button())
        self._scope_row.addStretch()
        self._refresh_scope_summary()

    def _build_add_scope_button(self) -> QPushButton:
        btn = QPushButton("+", self._scope_box)
        btn.setObjectName("PoolStepperBtn")
        btn.setFixedSize(26, 26)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tr(
            "Add an event, Event Collection, or date range."))
        btn.clicked.connect(lambda: self._open_scope_picker(btn))
        return btn

    def _open_scope_picker(self, anchor: QWidget) -> None:
        """Open the operand picker in Scope mode. Inventory shows Events,
        Event Collections, and a ``+ Add date range…`` row (spec/90 §3.4)."""
        popover = _OperandPickerPopover(
            target=PICKER_TARGET_SCOPE,
            events=self._ctx.available_events,
            event_collections=self._ctx.available_event_collections,
            parent=self,
        )
        popover.chosen.connect(self._add_scope_chip)
        popover.add_date_range_requested.connect(
            self._open_date_range_picker)
        pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
        popover.move(pos)
        popover.show()
        self._picker_popover = popover

    def _open_date_range_picker(self) -> None:
        """Open :class:`_DateRangePickerPopover` and add the chosen range
        as a Scope chip on confirm."""
        picker = _DateRangePickerPopover(parent=self)
        picker.range_chosen.connect(self._add_date_range_chip)
        # Dialog rather than popover — exec so the modal blocks until
        # the user confirms or cancels.
        picker.exec()

    def _add_scope_chip(self, operand: OperandOption) -> None:
        """Append an Event or Event Collection operand to the Scope
        sentence. Default join is ``or`` (spec/90 §3.2)."""
        join = JOIN_OR
        self._scope_chips.append((join, operand))
        self._refresh_scope_row()

    def _add_date_range_chip(self, start_iso: str, end_iso: str) -> None:
        """Turn a confirmed date range into a Scope chip. The chip's
        display name is the compact ``[YYYY-MM-DD — YYYY-MM-DD]``
        shape per spec/90 §3.1."""
        operand = OperandOption(
            name=f"[{start_iso} — {end_iso}]",
            kind="date_range",
            start=start_iso,
            end=end_iso,
        )
        self._scope_chips.append((JOIN_OR, operand))
        self._refresh_scope_row()

    def _remove_scope_chip(self, index: int) -> None:
        if 0 <= index < len(self._scope_chips):
            self._scope_chips.pop(index)
            self._refresh_scope_row()

    def _set_scope_join(self, index: int, join: str) -> None:
        """Swap one Scope chip's preceding join word (spec/90 §3.2). The
        first chip's join is always treated as union by the encoder."""
        if 0 < index < len(self._scope_chips):
            _old_join, operand = self._scope_chips[index]
            self._scope_chips[index] = (join, operand)
            self._refresh_scope_row()

    def _refresh_scope_summary(self) -> None:
        """Live-count hint for the Scope sentence. Phase 4b counts the
        chips' declared event counts as a stand-in until the dialog-level
        scope evaluator lands (the resolver currently takes pre-resolved
        scope uuids; turning a date_range chip into a uuid set is a
        future-phase job)."""
        if not hasattr(self, "_scope_summary"):
            return
        total = 0
        for _join, op in self._scope_chips:
            if op.kind in ("event", "event_collection"):
                total += op.count
        self._scope_summary.setText(f"scope: {total} events")

    # -------- Filters ------------------------------------------------ #

    def _build_filters_section(self) -> QWidget:
        """The Filters block (spec/90 §4) — Style + Media everywhere;
        Camera + Lens + Faces only when ``show_hardware=True``."""
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        v.addWidget(_micro(tr("Filters")))

        v.addLayout(self._build_style_row())
        v.addLayout(self._build_media_row())

        if self._show_hardware:
            # Per spec/90 §4.2 — the dialog adapts to the user's inventory.
            # A single-camera photographer doesn't need a Camera row at
            # all; same for lens.
            if len(self._ctx.available_cameras) >= 2:
                v.addLayout(self._build_camera_row())
            if len(self._ctx.available_lenses) >= 2:
                v.addLayout(self._build_lens_row())
            v.addWidget(_placeholder(tr("Faces: (Phase 4c)")))
        return host

    def _build_style_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel(tr("Style"))
        label.setObjectName("Faint")
        label.setMinimumWidth(64)
        row.addWidget(label)
        for style in self._ctx.available_styles:
            chip = pill_toggle(
                style, checked=(style in self._ctx.selected_styles))
            chip.setObjectName("StyleChip")
            self._style_chips[style] = chip
            row.addWidget(chip)
        row.addStretch()
        return row

    def _build_media_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel(tr("Media"))
        label.setObjectName("Faint")
        label.setMinimumWidth(64)
        row.addWidget(label)
        self._photos_cb = QCheckBox(tr("Photos"))
        self._photos_cb.setObjectName("DaysTableCheck")
        self._photos_cb.setChecked(self._ctx.include_photos)
        row.addWidget(self._photos_cb)
        self._videos_cb = QCheckBox(tr("Videos"))
        self._videos_cb.setObjectName("DaysTableCheck")
        self._videos_cb.setChecked(self._ctx.include_videos)
        row.addWidget(self._videos_cb)
        row.addStretch()
        return row

    def _build_camera_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel(tr("Camera"))
        label.setObjectName("Faint")
        label.setMinimumWidth(64)
        row.addWidget(label)
        for cam in self._ctx.available_cameras:
            chip = pill_toggle(
                cam, checked=(cam in self._ctx.selected_cameras))
            chip.setObjectName("CameraChip")
            self._camera_chips[cam] = chip
            row.addWidget(chip)
        row.addStretch()
        return row

    def _build_lens_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel(tr("Lens"))
        label.setObjectName("Faint")
        label.setMinimumWidth(64)
        row.addWidget(label)
        for lens in self._ctx.available_lenses:
            chip = pill_toggle(
                lens, checked=(lens in self._ctx.selected_lenses))
            chip.setObjectName("LensChip")
            self._lens_chips[lens] = chip
            row.addWidget(chip)
        row.addStretch()
        return row

    # -------- Placeholder sections (Phase 4b/4c/4d) ------------------- #

    def _build_rules_section(self) -> QWidget:
        """The Rules block (spec/90 §1.3). Each rule is a row in a
        :class:`QVBoxLayout`; the user adds rules via the ``+ add rule``
        button at the bottom. Drag-to-reorder lives on the per-row
        :class:`_RuleDragHandle`; the dialog computes the target index
        on release using the cursor's global y vs each row's geometry.

        Drag-to-reorder choice: custom mouse handling on the drag handle
        rather than ``QListWidget``. The rule row is a composed widget
        (drag handle + index + predicate sentence + verdict pill + match
        count + delete) — a ``QListWidget`` with custom item delegates
        would have to re-implement the predicate sentence's join-word
        popovers + chip removal flow. The custom-handle approach reuses
        the per-row widget verbatim; the reorder seam is one method
        (:meth:`_reorder_rule`) the tests can call directly without
        synthesising native drag events."""
        host = QWidget()
        host.setObjectName("RulesSection")
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        v.addWidget(_micro(tr("Rules")))

        self._rules_container = QWidget(host)
        self._rules_layout = QVBoxLayout(self._rules_container)
        self._rules_layout.setContentsMargins(0, 0, 0, 0)
        self._rules_layout.setSpacing(4)
        v.addWidget(self._rules_container)

        add_btn = ghost_button(tr("+ Add rule"))
        add_btn.setObjectName("AddRuleButton")
        add_btn.clicked.connect(self._on_add_rule_clicked)
        v.addWidget(add_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        return host

    def _build_otherwise_section(self) -> QWidget:
        """The Otherwise row (spec/90 §1.1, §1.3). Just the leading word
        + a verdict pill. Always present; default verdict is ``skip``
        (matches the most common pick-in shape per spec/90 §3.5)."""
        host = QWidget()
        host.setObjectName("OtherwiseSection")
        v = QHBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        lead = QLabel(tr("Otherwise"))
        lead.setObjectName("OtherwiseLead")
        v.addWidget(lead)
        self._otherwise_pill = _VerdictPill(self._otherwise, host)
        self._otherwise_pill.chosen.connect(self._on_otherwise_chosen)
        v.addWidget(self._otherwise_pill)
        v.addStretch()
        return host

    # ---- rules wiring ---------------------------------------------- #

    def _refresh_rules_rows(self) -> None:
        """Tear down the per-row widget list and rebuild from
        :attr:`_rules`. The cheaper diff (insert / remove / move) would
        be fiddly; rules lists are tiny in practice."""
        while self._rules_layout.count():
            item = self._rules_layout.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.deleteLater()
        self._rule_rows = []

        for index, (predicate, verdict) in enumerate(self._rules):
            row = _RuleRow(index, predicate, verdict, self._rules_container)
            row.delete_requested.connect(
                lambda r=row: self._delete_rule(r))
            row.add_operand_requested.connect(
                lambda anchor, r=row: self._open_rule_predicate_picker(r, anchor))
            row.changed.connect(
                lambda r=row: self._on_rule_row_changed(r))
            row.drag_pressed.connect(self._on_rule_drag_pressed)
            row.drag_released.connect(self._on_rule_drag_released)
            self._rules_layout.addWidget(row)
            self._rule_rows.append(row)

    def _on_add_rule_clicked(self) -> None:
        """Append a fresh rule with empty predicate + default ``skip``
        verdict. spec/90 §3.5 — pick-in is the most common shape."""
        self._rules.append(([], VERDICT_SKIP))
        self._refresh_rules_rows()

    def _delete_rule(self, row: "_RuleRow") -> None:
        if row in self._rule_rows:
            idx = self._rule_rows.index(row)
            del self._rules[idx]
            self._refresh_rules_rows()

    def _on_rule_row_changed(self, row: "_RuleRow") -> None:
        """A rule's predicate or verdict changed in-row — mirror to the
        dialog's data store. Tests subscribe to widget signals via the
        rule row directly; this routes the state."""
        if row in self._rule_rows:
            idx = self._rule_rows.index(row)
            self._rules[idx] = (row.predicate(), row.verdict())

    def _open_rule_predicate_picker(
        self, row: "_RuleRow", anchor: QWidget,
    ) -> None:
        """Open the operand picker with ``target='rule_predicate'`` for
        a specific :class:`_RuleRow`. Faces appear when the dialog
        enables hardware filters AND the People catalog has entries."""
        popover = _OperandPickerPopover(
            self._ctx.available_pools,
            target=PICKER_TARGET_RULE_PREDICATE,
            people=self._ctx.available_people,
            show_faces=self._show_hardware,
            parent=self,
        )
        popover.chosen.connect(
            lambda operand, r=row: self._on_predicate_operand_chosen(r, operand))
        pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
        popover.move(pos)
        popover.show()
        self._picker_popover = popover

    def _on_predicate_operand_chosen(
        self, row: "_RuleRow", operand: OperandOption,
    ) -> None:
        """Route the picker's chosen operand back into the right rule
        row. The row owns its predicate list; we just relay."""
        row.append_operand(operand)

    def _on_otherwise_chosen(self, verdict: str) -> None:
        """Verb popover -> Otherwise pill. The pill already updated
        itself; mirror to the model."""
        if verdict in VERDICTS:
            self._otherwise = verdict

    # ---- drag-to-reorder ------------------------------------------- #

    def _on_rule_drag_pressed(self, index: int, _global_pos: QPoint) -> None:
        self._dragging_rule_index = index

    def _on_rule_drag_released(
        self, _index: int, global_pos: QPoint,
    ) -> None:
        """Compute the drop target from the cursor's global y vs each
        row's geometry; reorder the data list; rebuild the rows."""
        from_idx = self._dragging_rule_index
        self._dragging_rule_index = None
        if from_idx is None:
            return
        # Find the target index: walk the live rows, take the row whose
        # vertical centre the cursor passed over.
        target = from_idx
        for row in self._rule_rows:
            top_left = row.mapToGlobal(QPoint(0, 0))
            bottom = top_left.y() + row.height()
            if global_pos.y() < (top_left.y() + bottom) // 2:
                target = self._rule_rows.index(row)
                break
        else:
            target = len(self._rules) - 1
        self._reorder_rule(from_idx, target)

    def _reorder_rule(self, from_idx: int, to_idx: int) -> None:
        """Move :attr:`_rules` entry ``from_idx`` to ``to_idx``. Idempotent
        when ``from_idx == to_idx``. The testable seam — tests call this
        directly rather than synthesising mouse drag events."""
        if from_idx == to_idx:
            return
        if not (0 <= from_idx < len(self._rules)):
            return
        if not (0 <= to_idx < len(self._rules)):
            return
        rule = self._rules.pop(from_idx)
        self._rules.insert(to_idx, rule)
        self._refresh_rules_rows()

    def _build_metrics_section(self) -> QWidget:
        host = QWidget()
        host.setObjectName("MetricsSection")
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        v.addWidget(_micro(tr("Metrics")))
        v.addWidget(_placeholder(tr("Metrics: (Phase 4d)")))
        return host

    # -------- Footer ------------------------------------------------- #

    def _build_footer(self) -> QWidget:
        host = QWidget()
        h = QHBoxLayout(host)
        h.setContentsMargins(22, 14, 22, 14)
        h.setSpacing(10)
        # Save as Recipe… — Phase 4e.
        self._save_recipe_btn = ghost_button(tr("Save as Recipe…"))
        self._save_recipe_btn.setEnabled(False)
        self._save_recipe_btn.setToolTip(tr(
            "Save these choices as a Recipe — Phase 4e."))
        h.addWidget(self._save_recipe_btn)
        h.addStretch()
        cancel = ghost_button(tr("Cancel"))
        cancel.clicked.connect(self.reject)
        h.addWidget(cancel)
        # Start — disabled in Phase 4a (the picker session wiring lands
        # in Phase 4e along with the Save flow).
        self._start_btn = primary_button(tr("▶ Start"))
        self._start_btn.setEnabled(False)
        self._start_btn.setToolTip(tr(
            "Pick the rules + Otherwise verdict — Phase 4c — to enable Start."))
        h.addWidget(self._start_btn)
        return host

    # ------------------------------------------------------------------ #
    # Name preview
    # ------------------------------------------------------------------ #

    def _on_name_changed(self, text: str) -> None:
        cleaned = text.strip().lower().replace(" ", "_")
        if cleaned:
            self._name_tag_hint.setText(f"#{cleaned}")
        else:
            self._name_tag_hint.setText("(tag will preview here)")

    # ------------------------------------------------------------------ #
    # Public output — spec/90 §5.1 composition shape (read-only in 4a)
    # ------------------------------------------------------------------ #

    def source_expression(self) -> list:
        """The Source sentence encoded into the spec/81 / spec/90 expr
        shape — ``[[op, operand], ...]``. Each chip's preceding join
        translates to the ASCII operator via :data:`_JOIN_TO_OP`; the
        first chip uses ``+`` (the empty-accumulator union case).
        Operands are the typed-ref Mapping spec/81 expects: base
        universes stay bare strings, DCs/Cuts become typed dicts."""
        out: List[List[Any]] = []
        for index, (join, operand) in enumerate(self._source_chips):
            op = "+" if index == 0 else _JOIN_TO_OP.get(join, "+")
            out.append([op, self._encode_operand(operand)])
        return out

    def scope_expression(self) -> list:
        """The Scope sentence encoded into the spec/90 §5.1 expr shape.

        Empty list when the user composed nothing — the resolver
        substitutes "this event" for Cut-flavour Recipes and refuses to
        evaluate a Collection Recipe with no Scope (spec/90 §1.1)."""
        if not self._show_scope:
            return []
        out: List[List[Any]] = []
        for index, (join, operand) in enumerate(self._scope_chips):
            op = "+" if index == 0 else _JOIN_TO_OP.get(join, "+")
            out.append([op, self._encode_operand(operand)])
        return out

    def rules_expression(self) -> list:
        """The Rules list encoded into the spec/90 §5.1 shape.

        Each rule is ``{"predicate": [[op, operand], ...], "verdict":
        "pick" | "skip"}``. Empty predicates are dropped silently —
        :mod:`core.recipe_resolver` would drop them at resolve time
        anyway (a no-op rule). The first chip's join encodes as
        union (``+``); later chips use :data:`_JOIN_TO_OP` translation."""
        out: List[Dict[str, Any]] = []
        for predicate, verdict in self._rules:
            if not predicate:
                continue
            encoded: List[List[Any]] = []
            for index, (join, operand) in enumerate(predicate):
                op = "+" if index == 0 else _JOIN_TO_OP.get(join, "+")
                encoded.append([op, self._encode_operand(operand)])
            out.append({"predicate": encoded, "verdict": verdict})
        return out

    def otherwise_verdict(self) -> str:
        """The Otherwise row's current verdict (spec/90 §1.1, §1.3).
        Always returns one of :data:`VERDICTS` — empty state is not
        representable; the dialog defaults to ``skip``."""
        return self._otherwise

    def composition(self) -> dict:
        """Aggregate the section payloads into one ``composition`` dict
        matching the spec/90 §5.1 schema. Convenient for tests + the
        Phase 4e save-recipe / start-picker wiring."""
        comp: Dict[str, Any] = {
            "source": self.source_expression(),
            "filters": self.filters_payload(),
            "rules": self.rules_expression(),
            "otherwise": self.otherwise_verdict(),
        }
        if self._show_scope:
            comp["scope"] = self.scope_expression()
        return comp

    @staticmethod
    def _encode_operand(operand: OperandOption) -> Any:
        """Operand encoding mirroring
        :meth:`mira.ui.pages.new_cut_dialog.NewCutDialog._operand_for_name`.

        * Base universes stay bare strings.
        * DC / Cut / Event Collection refs become ``{"kind": …, "tag":
          …, "id": …}`` dicts.
        * Event refs become ``{"kind": "event", "uuid": …}`` per spec/90
          §3.1; spec/90 §5.1's strict-ref check reads ``uuid``.
        * Date-range refs become ``{"kind": "date_range", "start": …,
          "end": …}`` — the one operand kind not derived from a named
          inventory (spec/90 §3.1, §1.1).
        """
        if operand.kind == "base":
            return operand.tag or operand.name.lstrip("#")
        if operand.kind == "event":
            return {"kind": "event", "uuid": operand.uuid or ""}
        if operand.kind == "date_range":
            return {
                "kind": "date_range",
                "start": operand.start or "",
                "end": operand.end or "",
            }
        if operand.kind == "person":
            # Person refs (spec/90 §4.3) carry an ``id`` (the user-store
            # ``person.id``). The strict-ref check on the resolver side
            # uses ``id``; the dialog renders ``name``.
            ref: Dict[str, Any] = {"kind": "person"}
            if operand.id:
                ref["id"] = operand.id
            return ref
        # DC / Cut / Event Collection
        tag = operand.tag or operand.name.lstrip("#")
        ref: Dict[str, Any] = {"kind": operand.kind, "tag": tag}
        if operand.id:
            ref["id"] = operand.id
        return ref

    def filters_payload(self) -> dict:
        """Read the current filter selections into the
        ``composition['filters']`` shape spec/90 §5.1 documents."""
        out: Dict[str, Any] = {
            "styles": self._selected_styles(),
            "media_type": self._media_type(),
        }
        if self._show_hardware:
            cams = self._selected_cameras()
            if cams:
                out["camera_ids"] = cams
            lenses = self._selected_lenses()
            if lenses:
                out["lens_models"] = lenses
        return out

    def _selected_styles(self) -> List[str]:
        return [s for s, chip in self._style_chips.items() if chip.isChecked()]

    def _selected_cameras(self) -> List[str]:
        return [c for c, chip in self._camera_chips.items() if chip.isChecked()]

    def _selected_lenses(self) -> List[str]:
        return [l for l, chip in self._lens_chips.items() if chip.isChecked()]

    def _media_type(self) -> str:
        ph = self._photos_cb.isChecked() if self._photos_cb else True
        vi = self._videos_cb.isChecked() if self._videos_cb else True
        if ph and vi:
            return "both"
        if ph:
            return "photo"
        if vi:
            return "video"
        return "both"  # neither checked: treat as "no filter" for now


__all__ = [
    "FLAVOUR_CUT",
    "FLAVOUR_COLLECTION",
    "INVENTORY_EVENT",
    "INVENTORY_LIBRARY",
    "JOIN_OR",
    "JOIN_AND",
    "JOIN_BUT_NOT",
    "JOIN_WORD_OPTIONS",
    "VERDICT_PICK",
    "VERDICT_SKIP",
    "VERDICTS",
    "VERB_OPTIONS",
    "PICKER_TARGET_SOURCE",
    "PICKER_TARGET_SCOPE",
    "PICKER_TARGET_RULE_PREDICATE",
    "DateRangeQuickSelect",
    "NewRecipeContext",
    "NewRecipeDialog",
    "OperandOption",
]
