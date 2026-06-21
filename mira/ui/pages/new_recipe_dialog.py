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

Phase 4a–e build the widget incrementally: scaffold (4a), Scope (4b),
Rules + verb + join-word popovers (4c), live metrics + resolver probe
(4d), and the Save / Load Recipe footer + Start-button wiring (4e —
which also retires the legacy ``new_cut_dialog`` module). The widget is
now the production New Cut surface; :class:`mira.ui.pages.share_cuts_page.ShareCutsPage`
opens it directly.

The widget's public surface matches the spec/90 §2.3 contract — four
boolean / enum flags pin the visible sections, the inventory + facets
ride the :class:`NewRecipeContext` dataclass, live probes connect to
the gateway (``pool_probe`` / ``totals_probe`` / ``recipe_probe``), and
the optional :class:`RecipeStore` enables the Save as Recipe… +
Load Recipe… buttons.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from PyQt6.QtCore import QDate, QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QMouseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core import recipe_resolver as _recipe_resolver
from core.placement_classifier import (
    PLACEMENT_GLOBAL,
    BoundPlacement,
    Placement,
)
from mira.shared.recipe_store import (
    FLAVOUR_COLLECTION as _STORE_FLAVOUR_COLLECTION,
    FLAVOUR_CUT as _STORE_FLAVOUR_CUT,
    RecipeNameTakenError,
    RecipeStore,
)
from mira.ui.base.binding_badge import BindingBadge
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
    """Micro section header — Faint, all-caps."""
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

    # Runtime presentation (Phase 4d) — the metrics row's projection.
    # ``target_minutes`` / ``max_minutes`` define the budget zones in
    # :mod:`core.cut_budget`; ``per_photo_seconds`` is the seconds-per-
    # slide cost the runtime math multiplies by. Defaults match the
    # legacy New Cut dialog's spec/61 §2 step 5 numbers.
    #
    # ``has_budget`` (spec/90 §5.1 presentation block): when False the
    # dialog renders the Target / Max spinners greyed and emits
    # ``presentation.target_s = max_s = None`` so the picker session
    # honours the "no time budget" state (cut_session_page already
    # renders "no limit" for NULL bounds). True by default for new
    # Cuts; the Adjust prefill flips it to False when the existing Cut
    # has both NULL bounds.
    target_minutes: int = 10
    max_minutes: int = 12
    per_photo_seconds: float = 6.0
    has_budget: bool = True

    # ``is_editing`` flips the Start-button gate to a permissive mode
    # (spec/90 Phase 4e — Nelson 2026-06-20): when True, Start enables
    # as long as Source is non-empty, regardless of probe state. Use
    # for the Adjust flow on an existing Cut, where the user may be
    # editing metadata (budget, etc.) and the source's current
    # resolution might be empty (deleted exports) or errored (missing
    # operand) — neither of which should block saving the metadata
    # change. New-Cut flow keeps False so the old non-empty-pool gate
    # still protects accidental empty Cuts.
    is_editing: bool = False


# --------------------------------------------------------------------------- #
# Source-section chips + picker popover
# --------------------------------------------------------------------------- #


class _SourceChip(QFrame):
    """Selected-operand chip in the Source sentence.

    Uses the global ``QFrame#PoolChipHost`` QSS rule (card2 bg + line
    border + 14px radius); :attr:`Qt.WidgetAttribute.WA_StyledBackground`
    keeps the cascade reaching nested under the scroll area.

    One ``×`` button removes the chip. The +/− / ∩ steppers from the
    retired ``_PoolChip`` are gone — the rule-list grammar handles the
    set-algebra via the join-word dropdown between chips, so the chips
    themselves stay simple."""

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

    Each option renders as a two-line button — the choice token in bold on
    top, a faint plain-language description below — per spec/90 §1.2 /
    §3.3. The two lines are real :class:`QLabel`'s inside the button's
    layout (Qt's ``QPushButton.setText`` renders HTML markup literally —
    a ``<b>pick</b>`` would show up as the raw tags). The currently-
    selected option carries a ``selected="true"`` Qt property so the QSS
    cascade can mark it; the row stays a :class:`QPushButton` so click /
    hover / keyboard semantics come for free.

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
            # Two-line layout inside the button: real QLabel widgets so
            # the markup never reaches Qt's text rendering.
            inner = QVBoxLayout(btn)
            inner.setContentsMargins(10, 6, 10, 6)
            inner.setSpacing(1)
            key_lbl = QLabel(key, btn)
            key_lbl.setObjectName(f"{role}Key")
            key_lbl.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            inner.addWidget(key_lbl)
            desc_lbl = QLabel(tr(description), btn)
            desc_lbl.setObjectName(f"{role}Desc")
            desc_lbl.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            desc_lbl.setWordWrap(True)
            inner.addWidget(desc_lbl)
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

        # "Save as DC…" in the popover is the per-rule-predicate entry
        # point only (spec/90 §5.5). Source-level Save as DC is the
        # canonical band-header button on the "Which items?" band, so the
        # Source popover hides this affordance to avoid two paths for the
        # same intent. Scope hides it too — events don't compose into DCs
        # (Event Collection track).
        if self._target == PICKER_TARGET_RULE_PREDICATE:
            outer.addWidget(_divider())
            self._save_btn = ghost_button(tr("Save as Collection…"))
            self._save_btn.setObjectName("OperandPickerSaveAsDc")
            self._save_btn.setToolTip(tr(
                "Save this predicate as a reusable Collection."))
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
            ("dc", tr("Collections")),
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
        btn.setStyleSheet("text-align: left; padding: 6px 8px;")  # pragma: no-qss — layout-only
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
        btn.setStyleSheet("text-align: left; padding: 6px 8px;")  # pragma: no-qss — layout-only
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


def _format_mm_ss(seconds: float) -> str:
    """Format ``seconds`` as ``MM:SS`` for the metrics row (spec/90 §10).
    Negative or NaN values clamp to ``0:00``."""
    try:
        total = max(0, int(round(float(seconds))))
    except (TypeError, ValueError):
        return "0:00"
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"


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
# Save-as-Recipe + Load-Recipe sub-dialogs (spec/90 §7 Phase 5)
# --------------------------------------------------------------------------- #


class _SaveRecipeNameDialog(QDialog):
    """One-line name dialog the footer "Save as Recipe…" button opens.

    Defaults to the parent dialog's Name field when present. On OK, the
    parent reads :meth:`recipe_name` and calls :meth:`RecipeStore.create`;
    a :class:`RecipeNameTakenError` is surfaced via :meth:`show_error`
    which keeps the dialog open with an inline message so the user can
    pick a different name without retyping.
    """

    def __init__(
        self,
        *,
        default: str = "",
        flavour: str = FLAVOUR_CUT,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SaveRecipeNameDialog")
        self.setWindowTitle(tr("Save as Recipe"))
        self.setModal(True)
        self.setMinimumWidth(420)
        self._flavour = flavour

        box = QVBoxLayout(self)
        group = QGroupBox(tr("Recipe name"))
        group.setObjectName("FormFieldGroup")
        gbox = QVBoxLayout(group)
        self._edit = QLineEdit(default)
        self._edit.setObjectName("SaveRecipeNameEdit")
        self._edit.setToolTip(tr(
            "How this Recipe appears in the Load Recipe… list."))
        self._edit.textChanged.connect(self._refresh)
        gbox.addWidget(self._edit)
        self._error = QLabel("")
        self._error.setObjectName("SaveRecipeNameError")
        self._error.setWordWrap(True)
        self._error.setVisible(False)
        gbox.addWidget(self._error)
        box.addWidget(group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok, parent=self)
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if self._ok is not None:
            self._ok.setObjectName("Primary")
            self._ok.setText(tr("Save"))
            self._ok.setToolTip(tr("Save the Recipe under this name."))
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel is not None:
            cancel.setToolTip(tr("Don't save a Recipe."))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)
        self._refresh()

    def recipe_name(self) -> str:
        return self._edit.text().strip()

    def show_error(self, message: str) -> None:
        """Display ``message`` inline; called by the parent when
        :meth:`RecipeStore.create` raised :class:`RecipeNameTakenError`."""
        self._error.setText(message)
        self._error.setVisible(True)

    def _refresh(self, _text: str = "") -> None:
        # Typing clears a stale error so the user isn't told their
        # in-progress name is taken. ``isHidden()`` rather than
        # ``isVisible()`` so we don't gate on the dialog being on-screen
        # (it isn't during tests).
        if not self._error.isHidden():
            self._error.setVisible(False)
            self._error.setText("")
        if self._ok is not None:
            self._ok.setEnabled(bool(self._edit.text().strip()))


class _SaveAsDcNameDialog(QDialog):
    """One-line name dialog the picker's "Save as DC…" button opens.

    Mirrors :class:`_SaveRecipeNameDialog`: defaults to empty so the user
    types fresh, a leading ``#`` preview shows beneath the input, and a
    conflict from the host's dc_creator surfaces via :meth:`show_error`
    inline so the user retries without retyping.
    """

    def __init__(
        self,
        *,
        default: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SaveAsDcNameDialog")
        self.setWindowTitle(tr("Save as Collection"))
        self.setModal(True)
        self.setMinimumWidth(420)

        box = QVBoxLayout(self)
        group = QGroupBox(tr("Collection name"))
        group.setObjectName("FormFieldGroup")
        gbox = QVBoxLayout(group)
        self._edit = QLineEdit(default)
        self._edit.setObjectName("SaveAsDcNameEdit")
        self._edit.setToolTip(tr(
            "How this Collection appears in operand pickers — # is added for you."))
        self._edit.textChanged.connect(self._refresh)
        gbox.addWidget(self._edit)
        self._preview = QLabel("")
        self._preview.setObjectName("Faint")
        gbox.addWidget(self._preview)
        self._error = QLabel("")
        self._error.setObjectName("SaveAsDcNameError")
        self._error.setWordWrap(True)
        self._error.setVisible(False)
        gbox.addWidget(self._error)
        box.addWidget(group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok, parent=self)
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if self._ok is not None:
            self._ok.setObjectName("Primary")
            self._ok.setText(tr("Save"))
            self._ok.setToolTip(tr("Save as a Collection."))
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel is not None:
            cancel.setToolTip(tr("Don't save a Collection."))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)
        self._refresh()

    def dc_name(self) -> str:
        return self._edit.text().strip()

    def show_error(self, message: str) -> None:
        """Display ``message`` inline; called by the parent when the
        host's dc_creator raised on the name."""
        self._error.setText(message)
        self._error.setVisible(True)

    def _refresh(self, _text: str = "") -> None:
        text = self._edit.text().strip()
        if text:
            cleaned = text.lower().replace(" ", "_")
            self._preview.setText(f"#{cleaned}")
        else:
            self._preview.setText("(tag will preview here)")
        if not self._error.isHidden():
            self._error.setVisible(False)
            self._error.setText("")
        if self._ok is not None:
            self._ok.setEnabled(bool(text))


class _LoadDcDialog(QDialog):
    """Saved-DC picker the Which items? band's "Load DC…" button opens.

    Lists the DC operands from the dialog's local inventory
    (``ctx.available_pools`` filtered to ``kind == 'dc'``). Single-select;
    double-click or OK loads. The picker does NOT itself replace state —
    it emits :attr:`dc_chosen` with the picked :class:`OperandOption` and
    closes; the parent dialog decides whether to confirm-replace or load
    directly. spec/90 §5 — Load DC mirrors Load Recipe but only touches
    the *items* layer (Source + Filters).
    """

    dc_chosen = pyqtSignal(object)             # OperandOption

    def __init__(
        self,
        dcs: Sequence["OperandOption"],
        *,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("LoadDcDialog")
        self.setWindowTitle(tr("Load Collection"))
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setMinimumHeight(340)
        self._dcs = [d for d in (dcs or ()) if getattr(d, "kind", "") == "dc"]
        self._rows: List[Tuple[OperandOption, QListWidgetItem]] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        hint = QLabel(tr(
            "Pick a Collection to replace the Source + Filters with. Rules, "
            "Otherwise, and Runtime stay as they are."))
        hint.setObjectName("PageHint")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        self._list = QListWidget(self)
        self._list.setObjectName("LoadDcList")
        self._list.itemDoubleClicked.connect(self._on_double_click)
        outer.addWidget(self._list, 1)

        for dc in self._dcs:
            label = self._format_label(dc)
            item = QListWidgetItem(label, self._list)
            self._rows.append((dc, item))

        empty = (not self._dcs)
        if empty:
            empty_lbl = QLabel(tr(
                "No Collections in this library yet. Compose a Source and "
                "click Save as Collection to make one."))
            empty_lbl.setObjectName("Faint")
            empty_lbl.setWordWrap(True)
            outer.addWidget(empty_lbl)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok, parent=self)
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if self._ok is not None:
            self._ok.setObjectName("Primary")
            self._ok.setText(tr("Load"))
            self._ok.setToolTip(tr(
                "Load the selected Collection into Source + Filters."))
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self._list.itemSelectionChanged.connect(self._refresh_ok)
        self._refresh_ok()

    def selected_dc(self) -> Optional["OperandOption"]:
        items = self._list.selectedItems()
        if not items:
            return None
        for dc, item in self._rows:
            if item is items[0]:
                return dc
        return None

    @staticmethod
    def _format_label(dc: "OperandOption") -> str:
        # Show "#tag    (count)" for visual parity with the operand
        # picker rows the dialog already uses.
        name = getattr(dc, "name", "") or ""
        count = getattr(dc, "count", 0) or 0
        return f"{name}    ({count})" if count else name

    def _refresh_ok(self) -> None:
        if self._ok is not None:
            self._ok.setEnabled(bool(self._list.selectedItems()))

    def _on_double_click(self, _item) -> None:
        if self.selected_dc() is not None:
            self._on_accept()

    def _on_accept(self) -> None:
        dc = self.selected_dc()
        if dc is None:
            return
        self.dc_chosen.emit(dc)
        self.accept()


class _LoadRecipeDialog(QDialog):
    """Saved-Recipe picker the header "Load Recipe…" button opens.

    Lists Recipes from :meth:`RecipeStore.list` filtered to the dialog's
    flavour by default; a "Show {other} Recipes too" checkbox flips the
    list to the cross-flavour view per spec/90 §5.5 — same-flavour first,
    other-flavour appended after with a small kind suffix so the user
    can tell them apart.

    Emits :attr:`recipe_chosen` on double-click or OK; the parent reads
    the selection and re-populates its state from the Recipe's
    composition_json.
    """

    recipe_chosen = pyqtSignal(object)             # um.Recipe

    def __init__(
        self,
        *,
        recipes_for: Callable[[bool], Sequence[Any]],
        flavour: str = FLAVOUR_CUT,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("LoadRecipeDialog")
        self.setWindowTitle(tr("Load Recipe"))
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setMinimumHeight(380)
        self._recipes_for = recipes_for
        self._flavour = flavour
        self._rows: List[Tuple[Any, QListWidgetItem]] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        hint = QLabel(tr(
            "Pick a Recipe to pre-fill every section below. The source "
            "re-evaluates against THIS event's data."))
        hint.setObjectName("PageHint")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        other = (FLAVOUR_COLLECTION if flavour == FLAVOUR_CUT
                 else FLAVOUR_CUT)
        self._include_other_cb = QCheckBox(
            tr("Show {other} Recipes too").format(other=other),
            self)
        self._include_other_cb.setObjectName("LoadRecipeIncludeOther")
        self._include_other_cb.toggled.connect(self._refresh)
        outer.addWidget(self._include_other_cb)

        self._list = QListWidget(self)
        self._list.setObjectName("LoadRecipeList")
        self._list.itemDoubleClicked.connect(self._on_double_click)
        outer.addWidget(self._list, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok, parent=self)
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if self._ok is not None:
            self._ok.setObjectName("Primary")
            self._ok.setText(tr("Load"))
            self._ok.setToolTip(tr("Load the selected Recipe."))
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self._list.itemSelectionChanged.connect(self._refresh_ok)
        self._refresh()

    def selected_recipe(self) -> Optional[Any]:
        items = self._list.selectedItems()
        if not items:
            return None
        for recipe, item in self._rows:
            if item is items[0]:
                return recipe
        return None

    def _refresh(self) -> None:
        self._list.clear()
        self._rows = []
        include_other = self._include_other_cb.isChecked()
        for recipe in self._recipes_for(include_other) or ():
            label = self._format_label(recipe)
            item = QListWidgetItem(label, self._list)
            self._rows.append((recipe, item))
        self._refresh_ok()

    def _format_label(self, recipe: Any) -> str:
        name = getattr(recipe, "name", "") or ""
        created = (getattr(recipe, "created_at", "") or "")[:10]
        flavour = getattr(recipe, "flavour", "") or ""
        if flavour and flavour != self._flavour:
            suffix = (f"  ({tr('Collection')})"
                      if flavour == FLAVOUR_COLLECTION
                      else f"  ({tr('Cut')})")
        else:
            suffix = ""
        if created:
            return f"{name}{suffix}    {created}"
        return f"{name}{suffix}"

    def _refresh_ok(self) -> None:
        if self._ok is not None:
            self._ok.setEnabled(bool(self._list.selectedItems()))

    def _on_double_click(self, _item) -> None:
        if self.selected_recipe() is not None:
            self._on_accept()

    def _on_accept(self) -> None:
        recipe = self.selected_recipe()
        if recipe is None:
            return
        self.recipe_chosen.emit(recipe)
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

    def show_match_placeholder(self) -> None:
        """Render the "(— match)" stand-in. Used before the first probe
        lands and when the probe fails."""
        self._match_label.setText(tr("(— match)"))
        self._match_label.setToolTip("")

    def show_match_count(self, predicate_match: int, new_match: int) -> None:
        """Render the live count per spec/90 §1.3:
        ``N match`` when ``predicate_match == new_match`` (no overlap)
        or ``N match · M new`` when items were already covered by an
        earlier rule (some matches don't actually take this rule's
        verdict).

        Tooltip carries the §1.3 plain-language explanation so the
        user understands why ``new`` is smaller than ``match``."""
        if predicate_match == new_match:
            self._match_label.setText(f"{predicate_match} {tr('match')}")
            self._match_label.setToolTip(
                tr("Predicate matches {n} items in the pool.").format(
                    n=predicate_match))
        else:
            self._match_label.setText(
                f"{predicate_match} {tr('match')} · "
                f"{new_match} {tr('new')}")
            self._match_label.setToolTip(
                tr(
                    "Predicate matches {n} items in the pool; {m} of those "
                    "weren't covered by an earlier rule and start picked "
                    "(or skipped) by this rule's verdict."
                ).format(n=predicate_match, m=new_match))

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
    #: spec/90 §7 Phase 5 — emitted by :meth:`_on_start_clicked` once the
    #: composition + draft are ready. Carries the :class:`CutDraft`
    #: the host hands to :meth:`CutSession.from_draft`. The dialog
    #: accepts() itself after emission; the host wires the picker session
    #: in its slot.
    start_requested = pyqtSignal(object)           # CutDraft
    #: Emitted after a successful :meth:`RecipeStore.create` so the
    #: host can show a toast (the dialog stays open).
    recipe_saved = pyqtSignal(object)              # um.Recipe

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
        recipe_probe: Optional[Callable[
            [dict], "_recipe_resolver.RecipeResolution"]] = None,
        recipe_store: Optional[RecipeStore] = None,
        dc_creator: Optional[Callable[
            [str, list, dict], "OperandOption"]] = None,
        dc_loader: Optional[Callable[
            ["OperandOption"], Tuple[list, dict]]] = None,
        classify_placement: Optional[Callable[[dict], Placement]] = None,
        event_name_for_id: Optional[Callable[[str], str]] = None,
        recipes_tree_provider: Optional[Callable[[], Any]] = None,
        recipe_resolver_by_ref: Optional[
            Callable[[Any], Optional[Any]]] = None,
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
        self._recipe_probe = recipe_probe
        self._recipe_store = recipe_store
        self._dc_creator = dc_creator
        self._dc_loader = dc_loader
        # spec/93 §5 — auto-placement classifier. The host wires this to a
        # callback that uses :func:`core.placement_classifier.classify_placement`
        # over the gateway's DC + Cut lookups; when unwired (early Phase 1
        # rollout, tests) the binding badge defaults to "Global" silently.
        self._classify_placement_callback: Optional[
            Callable[[dict], Placement]] = classify_placement
        self._event_name_for_id_callback: Optional[
            Callable[[str], str]] = event_name_for_id
        # Last classifier output — read by the badge widget + by the
        # migration-note label that fires when the placement flips.
        self._last_placement: Optional[Placement] = None
        # spec/94 Phase 1b — cascading folder menu for Load Recipe.
        # ``recipes_tree_provider`` returns a TreeNode mirroring the
        # file tree; ``recipe_resolver_by_ref`` resolves a chosen
        # DefinitionRef back to a um.Recipe-shaped object so the
        # existing :meth:`_apply_recipe` path can replay it.
        self._recipes_tree_provider = recipes_tree_provider
        self._recipe_resolver_by_ref = recipe_resolver_by_ref
        # Tracks which picker is "active" for a save_as_dc click:
        # ("source", None) for the source picker, ("rule_predicate", row)
        # for a rule row's predicate picker. Read inside
        # :meth:`_on_save_as_dc_clicked` so the dialog knows which
        # expression + filter block to ship.
        self._save_as_dc_context: Optional[Tuple[str, Optional["_RuleRow"]]] = None
        # Cross-flavour banner widget — created in :meth:`_build_metrics_section`
        # and toggled in :meth:`_show_cross_flavour_banner` /
        # :meth:`_clear_cross_flavour_banner`. spec/90 §5.5 — when a Collection
        # Recipe is loaded into a Cut dialog its hidden filters become a banner
        # above the metrics line.
        self._cross_flavour_fields: List[str] = []

        # Runtime presentation state (Phase 4d). Seeded from the ctx;
        # the spin widgets in the Runtime row mutate these on change.
        self._target_minutes: int = max(1, int(ctx.target_minutes))
        self._max_minutes: int = max(1, int(ctx.max_minutes))
        self._per_photo_seconds: float = max(0.1, float(ctx.per_photo_seconds))
        self._has_budget: bool = bool(ctx.has_budget)
        self._is_editing: bool = bool(ctx.is_editing)

        # Debounce timer — every section-state mutator calls
        # :meth:`_kick_probe`; the timer restarts on each kick and fires
        # :meth:`_run_probe` after a short quiet period. 200ms is fast
        # enough that the UI feels live, long enough that the probe
        # doesn't fire on every keystroke.
        self._probe_timer = QTimer(self)
        self._probe_timer.setInterval(200)
        self._probe_timer.setSingleShot(True)
        self._probe_timer.timeout.connect(self._run_probe)

        # Last successful resolution — read by tests and by the per-rule
        # label refresh path.
        self._last_resolution: Optional[
            "_recipe_resolver.RecipeResolution"] = None

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

        self._build_ui()

        if self._ctx.name:
            self._name_edit.setText(self._ctx.name)

        self._refresh_source_row()
        if self._show_scope:
            self._refresh_scope_row()
        self._refresh_rules_rows()
        self._refresh_save_button_states()
        # Initial size — at least wide enough that the widest header row
        # (Which items? + Load DC… + Save as DC…) doesn't clip on first
        # paint. ``sizeHint()`` reflects the contents the build pass just
        # laid out; bumping the floor to it fixes the right-edge clip a
        # naked 660-px ``resize`` produced before the toolbar arrived.
        hint = self.sizeHint()
        self.resize(max(hint.width(), 660), max(hint.height(), 880))
        # Fire one probe at end-of-init so the metrics row reflects any
        # initial selections (Recipe-load path — Phase 4e).
        self._kick_probe()

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

        # Load Recipe… moved to the Recipe toolbar at the top of the
        # body (spec/90 §5 — saves live with their data; the header bar
        # carries only title + close).

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
        """The dialog body — three visual tiers per spec/90 §5:

        * **Recipe toolbar** (top) — light secondary surface, no border,
          hosts the *Recipe* label + Load Recipe… + Save as Recipe…
          buttons. The Recipe save captures the whole composition.
        * **Name** + (Collection only) **Scope** — sit between the
          toolbar and the items group. Scope is the universe both saves
          operate within; neither captures it.
        * **Which items?** group — secondary-tint container wrapping the
          Source and Filters inner cards. The group header carries
          *Load DC…* and *Save as DC…* (the items layer becomes a
          reusable DC).
        * **What to do with them?** group — secondary-tint container
          wrapping the Rules, Otherwise, Runtime, and Metrics inner
          cards. No header buttons (Recipe is the only save that
          captures this layer).
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(14)

        v.addWidget(self._build_recipe_toolbar())
        v.addWidget(self._wrap_as_lightbox(
            self._build_name_section(), object_name="NameBox"))
        if self._show_scope:
            v.addWidget(self._wrap_as_lightbox(
                self._build_scope_section(), object_name="ScopeBox"))
        v.addWidget(self._build_which_items_group())
        v.addWidget(self._build_what_to_do_group())

        v.addStretch()
        scroll.setWidget(inner)
        return scroll

    # -------- Recipe toolbar (spec/90 §5) ---------------------------- #

    def _build_recipe_toolbar(self) -> QWidget:
        """Top-of-body toolbar — Recipe label on the left, Load Recipe…
        + Save as Recipe… buttons on the right. Light secondary surface,
        no border."""
        host = QFrame()
        # Collapsed onto the unified #SectionBox role (spec/92 §2.3);
        # the `section` property keeps the legacy "RecipeToolbar" identity
        # so tests + ad-hoc lookups still find this box.
        host.setObjectName("SectionBox")
        host.setProperty("section", "RecipeToolbar")
        host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        h = QHBoxLayout(host)
        h.setContentsMargins(14, 10, 14, 10)
        h.setSpacing(8)
        label = QLabel(tr("Recipe").upper())
        label.setObjectName("RecipeToolbarLabel")
        h.addWidget(label)

        # spec/93 §7 — read-only binding badge. Updated on every probe
        # via :meth:`_refresh_binding_badge`. Sits next to the Recipe
        # label so the user sees the placement context inline with the
        # name. Defaults to "Global" until the classifier callback is
        # wired by the host.
        self._binding_badge = BindingBadge()
        h.addWidget(self._binding_badge)

        # spec/93 §7 — migration note. A quiet inline label that lights
        # up for one beat when an edit flips the classification ("now
        # specific to Event A — it'll only appear there"). Hidden until
        # a flip is detected; cleared on the next refresh.
        self._migration_note = QLabel("")
        self._migration_note.setObjectName("MigrationNote")
        self._migration_note.setWordWrap(False)
        self._migration_note.setVisible(False)
        h.addWidget(self._migration_note)

        h.addStretch()

        # Load Recipe…
        self._load_btn = ghost_button(tr("Load Recipe…"))
        self._load_btn.setEnabled(self._recipe_store is not None)
        self._load_btn.setToolTip(
            tr("Pre-fill every section from a saved Recipe.")
            if self._recipe_store is not None
            else tr("No Recipe store wired — saving / loading disabled."))
        self._load_btn.clicked.connect(self._on_load_recipe_clicked)
        h.addWidget(self._load_btn)

        # Save as Recipe…
        self._save_recipe_btn = ghost_button(tr("Save as Recipe…"))
        self._save_recipe_btn.setObjectName("ToolbarSaveAsRecipe")
        self._save_recipe_btn.setToolTip(
            tr("Save the whole composition as a Recipe to re-instantiate later.")
            if self._recipe_store is not None
            else tr("No Recipe store wired — saving / loading disabled."))
        self._save_recipe_btn.clicked.connect(self._on_save_recipe_clicked)
        h.addWidget(self._save_recipe_btn)
        return host

    # -------- Nested-box helpers ------------------------------------- #

    def _wrap_as_section_card(
        self,
        inner: QWidget,
        *,
        object_name: str = "",
    ) -> QWidget:
        """Wrap an inner section widget in a card-style frame
        (unified ``SectionBox`` QSS role, spec/92 §2.3). The semantic
        identity passed via ``object_name`` rides on the ``section``
        property so tests + ad-hoc lookups still find each box by name."""
        host = QFrame()
        host.setObjectName("SectionBox")
        host.setProperty("section", object_name or "SectionCard")
        # WA_StyledBackground so the QSS ``background`` fills the frame
        # in addition to the painted border.
        host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        host.setProperty("kind", "section-card")
        v = QVBoxLayout(host)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)
        v.addWidget(inner)
        return host

    def _wrap_as_lightbox(
        self,
        inner: QWidget,
        *,
        object_name: str = "",
    ) -> QWidget:
        """Wrap a section in a light-secondary-surface container — the
        same visual tier as the Recipe toolbar and the band groups but
        without a header row or inner cards. Used for Name and Scope
        (spec/90 §5 — sibling siblings of the items + actions bands)."""
        host = QFrame()
        host.setObjectName("SectionBox")
        host.setProperty("section", object_name or "BandGroup")
        host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        v = QVBoxLayout(host)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(8)
        v.addWidget(inner)
        return host

    def _build_band_group(
        self,
        *,
        question: str,
        sections: Sequence[QWidget],
        header_buttons: Sequence[QPushButton] = (),
        hint: str = "",
        object_name: str = "",
    ) -> QWidget:
        """A band group — secondary-tint outer frame wrapping a header
        row + a stack of inner section cards. spec/90 §5: Q4-style
        question on the left, optional inline hint, optional header
        buttons on the right, then the inner cards below."""
        host = QFrame()
        # The band-group frames join the unified #SectionBox family
        # (spec/92 §2.3); the legacy identity (WhichItemsBand /
        # WhatToDoBand) rides on the `section` property.
        host.setObjectName("SectionBox")
        if object_name:
            host.setProperty("section", object_name)
        host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        v = QVBoxLayout(host)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)

        # Header row.
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        q = QLabel(question)
        q.setObjectName("BandQuestion")
        header.addWidget(q)
        if hint:
            hint_lbl = QLabel(hint)
            hint_lbl.setObjectName("BandHint")
            header.addWidget(hint_lbl)
        header.addStretch()
        for btn in header_buttons:
            header.addWidget(btn)
        v.addLayout(header)

        # Inner section cards.
        for section in sections:
            v.addWidget(section)
        return host

    def _build_which_items_group(self) -> QWidget:
        """The "Which items?" group — Source + Filters wrapped as inner
        cards inside a secondary-tint container. Header carries Load
        DC… and Save as DC… buttons (spec/90 §5)."""
        self._load_dc_btn = ghost_button(tr("Load Collection…"))
        self._load_dc_btn.setObjectName("BandLoadDc")
        self._load_dc_btn.setToolTip(
            tr("Replace Source + Filters with a saved Collection.")
            if self._dc_loader is not None
            else tr("No Collection loader wired — Load Collection disabled."))
        self._load_dc_btn.clicked.connect(self._on_load_dc_clicked)

        self._save_dc_btn = ghost_button(tr("Save as Collection…"))
        self._save_dc_btn.setObjectName("BandSaveAsDc")
        self._save_dc_btn.setToolTip(tr(
            "Save the current source + filters as a reusable Collection."))
        self._save_dc_btn.clicked.connect(self._on_band_save_as_dc_clicked)

        hint = tr("(across the events above)") if self._show_scope else ""
        sections = [
            self._wrap_as_section_card(
                self._build_source_section(), object_name="SourceSection"),
            self._wrap_as_section_card(
                self._build_filters_section(), object_name="FiltersSection"),
        ]
        return self._build_band_group(
            question=tr("Which items?"),
            sections=sections,
            header_buttons=[self._load_dc_btn, self._save_dc_btn],
            hint=hint,
            object_name="WhichItemsBand",
        )

    def _build_what_to_do_group(self) -> QWidget:
        """The "What to do with them?" group — Rules + Otherwise +
        Runtime + Metrics wrapped as inner cards. No header buttons
        (Save as Recipe lives in the Recipe toolbar)."""
        sections = [
            self._wrap_as_section_card(
                self._build_rules_section(), object_name="RulesSectionCard"),
            self._wrap_as_section_card(
                self._build_otherwise_section(),
                object_name="OtherwiseSectionCard"),
            self._wrap_as_section_card(
                self._build_runtime_section(),
                object_name="RuntimeSectionCard"),
            self._wrap_as_section_card(
                self._build_metrics_section(),
                object_name="MetricsSectionCard"),
        ]
        return self._build_band_group(
            question=tr("What to do with them?"),
            sections=sections,
            object_name="WhatToDoBand",
        )

    def _on_band_save_as_dc_clicked(self) -> None:
        """The band-header Save as DC click is the Source-level entry
        point. Pin the context so :meth:`_on_save_as_dc_clicked` ships
        the source expression + filters payload (and not, say, a stale
        predicate context from a previously-opened rule picker)."""
        self._save_as_dc_context = ("source", None)
        self._on_save_as_dc_clicked()

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
        label_row = QHBoxLayout()
        label_row.setContentsMargins(0, 0, 0, 0)
        label_row.setSpacing(8)
        label_row.addWidget(_micro(tr("Scope")))
        scope_hint = QLabel(tr("events to look in"))
        scope_hint.setObjectName("BandHint")
        label_row.addWidget(scope_hint)
        label_row.addStretch()
        v.addLayout(label_row)

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
            self._refresh_save_button_states()
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
        self._refresh_save_button_states()

    def _set_source_join(self, index: int, join: str) -> None:
        """Swap one Source chip's preceding join word. ``index`` is the
        chip's position; the first chip's join is always treated as
        union by the encoder and the popover is never offered for it."""
        if 0 < index < len(self._source_chips):
            _old_join, operand = self._source_chips[index]
            self._source_chips[index] = (join, operand)
            self._refresh_source_row()
            self._kick_probe()

    def _build_add_operand_button(self) -> QPushButton:
        """The ``+`` affordance that opens the operand picker (spec/90 §3.4)."""
        btn = QPushButton("+", self._source_box)
        btn.setObjectName("PoolStepperBtn")
        btn.setFixedSize(26, 26)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tr("Add an operand — Collection, Cut, or base universe."))
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
        # Source-level Save as DC lives on the band header, not in the
        # popover — see :meth:`_OperandPickerPopover._populate_sections`
        # and :meth:`_build_which_items_band`.
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
        self._kick_probe()

    def _remove_source_chip(self, index: int) -> None:
        if 0 <= index < len(self._source_chips):
            self._source_chips.pop(index)
            self._refresh_source_row()
            self._kick_probe()

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
        """Picker "Save as DC…" — open the naming sub-dialog and route
        the result through :attr:`_dc_creator`.

        Source target ships ``(name, source_expression(), filters_payload())``
        — saving the source alone would lose the Style/Media/Camera/Lens
        narrowing the user has set. Rule-predicate target ships
        ``(name, predicate_expr, {})`` — predicates don't carry filters
        (the dialog's filters apply at the pool level, not per-rule).

        With no :attr:`_dc_creator` wired the signal still fires so
        legacy tests / smokes that listen for the placeholder behaviour
        keep working — but the sub-dialog isn't opened (the creator is
        what materialises the DC)."""
        self.save_as_dc_requested.emit()
        if self._dc_creator is None:
            log.info("save_as_dc_requested — no dc_creator wired")
            return
        context = self._save_as_dc_context
        if context is None:
            # Defensive: a stray emit with no opener context. Honour
            # the source flavour so the click does *something*.
            context = ("source", None)
        target, row = context
        expr, filters = self._build_save_as_dc_payload(target, row)
        if not expr:
            log.info("save_as_dc clicked with empty expression — skipping")
            return
        self._open_save_as_dc_dialog(expr, filters)

    def _build_save_as_dc_payload(
        self,
        target: str,
        row: Optional["_RuleRow"],
    ) -> Tuple[list, dict]:
        """Encode the current expression + filters for the dc_creator.

        spec/90 §5 separates the two seams: Source-level Save as DC carries
        the dialog's filters block; predicate-level Save as DC carries an
        empty filters block (predicates don't compose with the dialog's
        Filters row)."""
        if target == "rule_predicate" and row is not None:
            return self._encode_rule_predicate_expr(row), {}
        return self.source_expression(), self.filters_payload()

    def _encode_rule_predicate_expr(self, row: "_RuleRow") -> list:
        """Translate one :class:`_RuleRow`'s predicate chip list into the
        spec/90 §5.1 ``[[op, operand], …]`` shape — same encoder
        :meth:`source_expression` uses."""
        out: List[List[Any]] = []
        for index, (join, operand) in enumerate(row.predicate()):
            op = "+" if index == 0 else _JOIN_TO_OP.get(join, "+")
            out.append([op, self._encode_operand(operand)])
        return out

    def _open_save_as_dc_dialog(
        self, expr: list, filters: dict,
    ) -> None:
        """The naming sub-dialog loop — mirrors
        :meth:`_on_save_recipe_clicked`. Keeps the sub-dialog open on
        conflict so the user retries without retyping; on success
        appends the returned :class:`OperandOption` to the inventory,
        toasts, and leaves the main dialog + the operand picker open."""
        dlg = _SaveAsDcNameDialog(parent=self)
        while True:
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            name = dlg.dc_name()
            try:
                operand = self._dc_creator(name, expr, filters)
            except ValueError as exc:
                code = str(exc)
                if code == "taken":
                    msg = tr(
                        "A Collection named '{name}' already exists. "
                        "Pick another.").format(name=name)
                elif code == "reserved":
                    msg = tr(
                        "'{name}' is a reserved name. Pick another."
                    ).format(name=name)
                elif code == "empty":
                    msg = tr(
                        "Pick a name with at least one letter or digit.")
                elif code == "cycle":
                    msg = tr(
                        "This expression refers back to itself — pick a "
                        "different combination.")
                else:
                    msg = str(exc) or tr("Could not save the Collection.")
                dlg.show_error(msg)
                continue
            except Exception as exc:                       # noqa: BLE001
                log.exception("dc_creator raised — keeping the sub-dialog open")
                dlg.show_error(str(exc) or tr("Could not save the Collection."))
                continue
            if operand is not None:
                self._append_operand_to_inventory(operand)
            self._toast(tr("Collection '{name}' saved.").format(name=name))
            return

    def _append_operand_to_inventory(self, operand: "OperandOption") -> None:
        """Drop a freshly-created DC into the dialog's local operand
        inventory so the next picker open lists it. Replaces any prior
        entry that matches the new operand's id (tag-by-tag fallback)."""
        pools = self._ctx.available_pools
        for i, existing in enumerate(pools):
            same_id = (operand.id and existing.id == operand.id)
            same_tag = (
                operand.tag and existing.tag
                and existing.kind == operand.kind
                and existing.tag == operand.tag
            )
            if same_id or same_tag:
                pools[i] = operand
                return
        pools.append(operand)

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
        self._kick_probe()

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
        self._kick_probe()

    def _remove_scope_chip(self, index: int) -> None:
        if 0 <= index < len(self._scope_chips):
            self._scope_chips.pop(index)
            self._refresh_scope_row()
            self._kick_probe()

    def _set_scope_join(self, index: int, join: str) -> None:
        """Swap one Scope chip's preceding join word (spec/90 §3.2). The
        first chip's join is always treated as union by the encoder."""
        if 0 < index < len(self._scope_chips):
            _old_join, operand = self._scope_chips[index]
            self._scope_chips[index] = (join, operand)
            self._refresh_scope_row()
            self._kick_probe()

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
            chip.toggled.connect(self._on_filter_chip_toggled)
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
        self._photos_cb.toggled.connect(self._on_filter_chip_toggled)
        row.addWidget(self._photos_cb)
        self._videos_cb = QCheckBox(tr("Videos"))
        self._videos_cb.setObjectName("DaysTableCheck")
        self._videos_cb.setChecked(self._ctx.include_videos)
        self._videos_cb.toggled.connect(self._on_filter_chip_toggled)
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
            chip.toggled.connect(self._on_filter_chip_toggled)
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
            chip.toggled.connect(self._on_filter_chip_toggled)
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
        self._kick_probe()

    def _delete_rule(self, row: "_RuleRow") -> None:
        if row in self._rule_rows:
            idx = self._rule_rows.index(row)
            del self._rules[idx]
            self._refresh_rules_rows()
            self._kick_probe()

    def _on_rule_row_changed(self, row: "_RuleRow") -> None:
        """A rule's predicate or verdict changed in-row — mirror to the
        dialog's data store. Tests subscribe to widget signals via the
        rule row directly; this routes the state."""
        if row in self._rule_rows:
            idx = self._rule_rows.index(row)
            self._rules[idx] = (row.predicate(), row.verdict())
        self._kick_probe()

    def _open_rule_predicate_picker(
        self, row: "_RuleRow", anchor: QWidget,
    ) -> None:
        """Open the operand picker with ``target='rule_predicate'`` for
        a specific :class:`_RuleRow`. Faces appear when the dialog
        enables hardware filters AND the People catalog has entries.

        The picker also surfaces "Save as DC…" for the rule's predicate
        — spec/90 §5: a rule's predicate is an item-set expression, the
        same shape as Source, so it can become a named DC (the saved
        DC carries an empty filters block; predicates don't compose
        with the dialog-level Filters row)."""
        popover = _OperandPickerPopover(
            self._ctx.available_pools,
            target=PICKER_TARGET_RULE_PREDICATE,
            people=self._ctx.available_people,
            show_faces=self._show_hardware,
            parent=self,
        )
        popover.chosen.connect(
            lambda operand, r=row: self._on_predicate_operand_chosen(r, operand))
        self._save_as_dc_context = ("rule_predicate", row)
        popover.save_as_dc_requested.connect(self._on_save_as_dc_clicked)
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
            self._kick_probe()

    def _on_filter_chip_toggled(self, _checked: bool = False) -> None:
        """Style / Media / Camera / Lens chip toggled → probe re-runs.
        The selection state itself lives on the chip widgets;
        :meth:`filters_payload` re-reads it on demand."""
        self._kick_probe()

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
        self._kick_probe()

    # -------- Runtime presentation (Phase 4d) ------------------------ #

    def _build_runtime_section(self) -> QWidget:
        """Small triple-spinner row carrying the runtime presentation
        settings (spec/61 §2 step 5 / spec/90 §10). Phase 4d only needs
        Target / Max / Per-photo to feed the metrics row's runtime math;
        Music + slide-cards still live on the spec/61 §3.1 settings
        surface and don't influence the live count.

        spec/90 §5.1 presentation block: a "Set a runtime budget"
        checkbox sits above the Target / Max spinners. When unchecked,
        the Target + Max spinners go disabled and the emitted
        ``presentation.target_s`` / ``max_s`` are ``None`` — the
        picker session renders "no limit" honestly. Per-photo is
        slide-rate (not a budget) and stays enabled regardless."""
        host = QWidget()
        host.setObjectName("RuntimeSection")
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        v.addWidget(_micro(tr("Runtime")))

        # Budget toggle — unchecked = "no budget" (target_s/max_s emit
        # as None; spinners disabled).
        self._budget_check = QCheckBox(tr("Set a runtime budget"))
        self._budget_check.setObjectName("RuntimeBudgetCheck")
        self._budget_check.setChecked(self._has_budget)
        self._budget_check.toggled.connect(self._on_budget_toggled)
        v.addWidget(self._budget_check)

        row = QHBoxLayout()
        row.setSpacing(10)

        # Target minutes.
        target_box = QWidget()
        tv = QVBoxLayout(target_box)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(2)
        tv.addWidget(QLabel(tr("Target (min)")))
        self._target_spin = QSpinBox()
        self._target_spin.setObjectName("RuntimeTargetSpin")
        self._target_spin.setRange(1, 240)
        self._target_spin.setValue(self._target_minutes)
        self._target_spin.setSuffix(" min")
        self._target_spin.setEnabled(self._has_budget)
        self._target_spin.valueChanged.connect(self._on_target_changed)
        tv.addWidget(self._target_spin)
        row.addWidget(target_box)

        # Max minutes.
        max_box = QWidget()
        mv = QVBoxLayout(max_box)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(2)
        mv.addWidget(QLabel(tr("Max (min)")))
        self._max_spin = QSpinBox()
        self._max_spin.setObjectName("RuntimeMaxSpin")
        self._max_spin.setRange(1, 480)
        self._max_spin.setValue(self._max_minutes)
        self._max_spin.setSuffix(" min")
        self._max_spin.setEnabled(self._has_budget)
        self._max_spin.valueChanged.connect(self._on_max_changed)
        mv.addWidget(self._max_spin)
        row.addWidget(max_box)

        # Per-photo seconds.
        pp_box = QWidget()
        pv = QVBoxLayout(pp_box)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(2)
        pv.addWidget(QLabel(tr("Per photo (s)")))
        self._per_photo_spin = QDoubleSpinBox()
        self._per_photo_spin.setObjectName("RuntimePerPhotoSpin")
        self._per_photo_spin.setRange(0.5, 60.0)
        self._per_photo_spin.setSingleStep(0.5)
        self._per_photo_spin.setDecimals(2)
        self._per_photo_spin.setValue(self._per_photo_seconds)
        self._per_photo_spin.setSuffix(" s")
        self._per_photo_spin.valueChanged.connect(self._on_per_photo_changed)
        pv.addWidget(self._per_photo_spin)
        row.addWidget(pp_box)

        row.addStretch()
        v.addLayout(row)
        return host

    def _on_budget_toggled(self, checked: bool) -> None:
        """Checkbox toggled — flip the budget state, grey or restore the
        Target / Max spinners, and refresh the metrics line (the suffix
        drops the "of N target" portion when no budget)."""
        self._has_budget = bool(checked)
        if hasattr(self, "_target_spin"):
            self._target_spin.setEnabled(self._has_budget)
        if hasattr(self, "_max_spin"):
            self._max_spin.setEnabled(self._has_budget)
        self._refresh_metrics_from_state()

    def _on_target_changed(self, value: int) -> None:
        self._target_minutes = int(value)
        self._refresh_metrics_from_state()

    def _on_max_changed(self, value: int) -> None:
        self._max_minutes = int(value)
        self._refresh_metrics_from_state()

    def _on_per_photo_changed(self, value: float) -> None:
        self._per_photo_seconds = float(value)
        self._refresh_metrics_from_state()

    # -------- Live metrics row (Phase 4d) ---------------------------- #

    def _build_metrics_section(self) -> QWidget:
        """The live metrics row (spec/90 §10 worked example). Renders as
        ``N in pool · M initially picked · MM:SS of MM:SS target``;
        updates after a debounced probe call.

        Two error surfaces ride alongside:

        * **Error banner** — red, shown when the probe raises
          :class:`RecipeResolutionError` (a missing named operand).
          Carries the operand label + kind so the user knows which
          named ref to fix.
        * **Soft hint** — faint, shown when the probe raises a plain
          :class:`ValueError` (author mistake: empty source, invalid
          Otherwise verdict). Phase 4d uses one shared label slot and
          flips its object name to mark the severity."""
        host = QWidget()
        host.setObjectName("MetricsSection")
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        v.addWidget(_micro(tr("Metrics")))

        # Error / hint banner — hidden until a probe surfaces something.
        self._metrics_banner = QLabel("")
        self._metrics_banner.setObjectName("MetricsBanner")
        self._metrics_banner.setWordWrap(True)
        self._metrics_banner.setVisible(False)
        v.addWidget(self._metrics_banner)

        # The metrics line itself. spec/90 §10:
        # "386 in pool · 11 initially picked · 1:30 of 5:00 target"
        self._metrics_label = QLabel(tr("(no probe wired yet)"))
        self._metrics_label.setObjectName("MetricsLine")
        v.addWidget(self._metrics_label)
        return host

    def _kick_probe(self) -> None:
        """Restart the debounce timer. Called from every section's
        change path so the probe only fires once after a quiet 200ms
        regardless of how many edits the user made."""
        if not hasattr(self, "_probe_timer"):
            return                                       # called pre-init
        self._probe_timer.start()
        # Cheap gating up-front: an empty Source instantly disables Start
        # without waiting for the probe. The post-probe call in
        # :meth:`_run_probe` re-evaluates with the resolution in hand.
        self._refresh_start_enabled()

    def _run_probe(self) -> None:
        """Build the composition + call :attr:`_recipe_probe`. Updates
        the metrics row, the banner, and per-rule labels.

        Public-ish: tests call this directly to bypass the debounce
        timer's wall-clock wait."""
        if self._recipe_probe is None:
            self._show_metrics_hint(tr("(no probe wired yet)"))
            self._refresh_start_enabled()
            return
        composition = self.composition()
        try:
            resolution = self._recipe_probe(composition)
        except _recipe_resolver.RecipeResolutionError as exc:
            self._show_metrics_error(
                tr("Missing operand: ") + (exc.missing_operand or "")
                + (f" ({exc.kind})" if exc.kind else ""))
            self._last_resolution = None
            self._clear_rule_breakdown_labels()
            self._refresh_start_enabled()
            return
        except ValueError as exc:
            self._show_metrics_hint(str(exc))
            self._last_resolution = None
            self._clear_rule_breakdown_labels()
            self._refresh_start_enabled()
            return
        except Exception as exc:                         # noqa: BLE001
            log.exception("recipe_probe raised — keeping previous metrics")
            self._show_metrics_error(tr("Probe error: ") + str(exc))
            self._refresh_start_enabled()
            return

        self._last_resolution = resolution
        self._apply_metrics(resolution)
        self._apply_rule_breakdown(resolution.rule_breakdown)
        self._clear_banner()
        self._refresh_start_enabled()
        # spec/93 §7 — refresh the binding badge + the one-shot migration
        # note. Done at the end so a probe failure earlier in the method
        # leaves the badge in its last-good state (the placement doesn't
        # depend on the probe; this is just where we already pay the cost
        # of building the composition).
        self._refresh_binding_badge(composition)

    def _refresh_binding_badge(self, composition: dict) -> None:
        """spec/93 §5 + §7 — classify the current composition and
        update the binding badge / one-shot migration note.

        Safe when the host hasn't wired the classifier callback: the
        badge stays at its default ("Global"), no migration note ever
        fires. Same when the callback raises — log + leave the last
        state in place (the badge must never become a crash surface).
        """
        if self._classify_placement_callback is None:
            return
        try:
            placement = self._classify_placement_callback(composition)
        except Exception as exc:                            # noqa: BLE001
            log.exception(
                "classify_placement raised — keeping badge as-is: %s", exc)
            return
        # Resolve the event_id to a name when possible so the badge
        # reads "Event: Costa Rica" instead of "Event: abcdef12".
        event_name = ""
        if isinstance(placement, BoundPlacement) and self._event_name_for_id_callback:
            try:
                event_name = self._event_name_for_id_callback(placement.event_id) or ""
            except Exception as exc:                        # noqa: BLE001
                log.warning(
                    "event_name_for_id raised — using id stub: %s", exc)
                event_name = ""
        self._binding_badge.set_placement(placement, event_name=event_name)
        self._maybe_show_migration_note(placement, event_name)
        self._last_placement = placement

    def _maybe_show_migration_note(
        self,
        placement: Placement,
        event_name: str,
    ) -> None:
        """One-shot inline note when the classification flips between
        saves of the same Recipe (spec/93 §7 "quiet migration note")."""
        prev = self._last_placement
        if prev is None or prev == placement:
            self._migration_note.setText("")
            self._migration_note.setVisible(False)
            return
        text = self._migration_note_text(placement, event_name)
        if not text:
            self._migration_note.setText("")
            self._migration_note.setVisible(False)
            return
        self._migration_note.setText(text)
        self._migration_note.setVisible(True)

    @staticmethod
    def _migration_note_text(
        placement: Placement,
        event_name: str,
    ) -> str:
        """The migration-note copy keyed off the new placement. Quiet
        + factual, never a prompt."""
        if placement == PLACEMENT_GLOBAL:
            return tr("Now reusable in any event.")
        if isinstance(placement, BoundPlacement):
            if event_name:
                return tr("Now specific to {event} — it'll only appear there.").replace(
                    "{event}", event_name)
            return tr("Now specific to one event — it'll only appear there.")
        # Cross-event — appears in the events its pinned operands belong to.
        return tr("Now spans several events — appears only in those.")

    def _refresh_metrics_from_state(self) -> None:
        """Re-render the metrics line using the last successful
        resolution + the current runtime settings. Doesn't re-run the
        probe — used for spin-box changes where the resolver output
        hasn't changed, only the projected runtime numbers."""
        if self._last_resolution is None:
            return
        self._apply_metrics(self._last_resolution)

    def _apply_metrics(
        self, resolution: "_recipe_resolver.RecipeResolution",
    ) -> None:
        """Render the metrics line from a fresh resolution. With a
        runtime budget set, includes the ``of MM:SS target`` suffix;
        without one (spec/90 §5.1 — has_budget=False), the line drops
        the suffix and tags the runtime as ``runtime`` so the user
        sees the projected length without an implied limit."""
        pool_size = len(resolution.pool)
        picked = sum(1 for v in resolution.seed.values() if v)
        total_s = float(picked) * float(self._per_photo_seconds)
        head = (
            f"{pool_size} {tr('in pool')} · "
            f"{picked} {tr('initially picked')} · "
        )
        if self._has_budget:
            target_s = int(self._target_minutes) * 60
            text = (
                head
                + f"{_format_mm_ss(total_s)} {tr('of')} "
                + f"{_format_mm_ss(target_s)} {tr('target')}"
            )
        else:
            text = head + f"{_format_mm_ss(total_s)} {tr('runtime')}"
        self._metrics_label.setText(text)

    def _apply_rule_breakdown(
        self,
        breakdown: Sequence["_recipe_resolver.RuleMatchInfo"],
    ) -> None:
        """Per-rule match labels (spec/90 §1.3 + §10).

        Map each ``RuleMatchInfo`` to the dialog's _rule_rows. The
        resolver only emits entries for non-empty predicates; the
        dialog's ``rules_expression()`` makes the same skip-empty
        decision so the indices line up if we iterate rows AND skip
        empty-predicate rows."""
        breakdown = list(breakdown)
        expressing_idx = 0
        for row in self._rule_rows:
            if not row.predicate():
                row.show_match_placeholder()
                continue
            if expressing_idx < len(breakdown):
                info = breakdown[expressing_idx]
                row.show_match_count(info.predicate_match, info.new_match)
            else:
                row.show_match_placeholder()
            expressing_idx += 1

    def _clear_rule_breakdown_labels(self) -> None:
        for row in self._rule_rows:
            row.show_match_placeholder()

    def _show_metrics_error(self, message: str) -> None:
        self._metrics_banner.setText(message)
        self._metrics_banner.setProperty("severity", "error")
        self._metrics_banner.setVisible(True)
        self._repolish(self._metrics_banner)

    def _show_metrics_hint(self, message: str) -> None:
        self._metrics_banner.setText(message)
        self._metrics_banner.setProperty("severity", "hint")
        self._metrics_banner.setVisible(True)
        self._repolish(self._metrics_banner)

    def _clear_banner(self) -> None:
        self._metrics_banner.setVisible(False)
        self._metrics_banner.setText("")

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        """Force QSS to re-evaluate after a property change. Without
        this the cascade keeps the previous severity colour."""
        style = widget.style()
        if style is not None:
            style.unpolish(widget)
            style.polish(widget)

    # -------- Footer ------------------------------------------------- #

    def _build_footer(self) -> QWidget:
        """The dialog footer — Cancel + Start ▶ only (spec/90 §5.5).

        Save as DC and Save as Recipe moved to the band headers so each
        save sits with the data it captures; the footer is purely about
        closing the dialog (discard or run)."""
        host = QWidget()
        h = QHBoxLayout(host)
        h.setContentsMargins(22, 14, 22, 14)
        h.setSpacing(10)
        h.addStretch()
        cancel = ghost_button(tr("Cancel"))
        cancel.clicked.connect(self.reject)
        h.addWidget(cancel)
        # Start — gated by :meth:`_refresh_start_enabled`. Disabled on
        # empty source / probe error / empty pool; enabled when the last
        # probe returned a non-empty pool with no errors.
        self._start_btn = primary_button(tr("▶ Start"))
        self._start_btn.setEnabled(False)
        self._start_btn.setToolTip(tr(
            "Compose a Source and pick a verdict to enable Start."))
        self._start_btn.clicked.connect(self._on_start_clicked)
        h.addWidget(self._start_btn)
        return host

    # ------------------------------------------------------------------ #
    # Save as Recipe…  (spec/90 §7 Phase 5)
    # ------------------------------------------------------------------ #

    def _on_save_recipe_clicked(self) -> None:
        """Footer Save as Recipe… — opens the naming dialog and writes
        through :class:`RecipeStore`. The naming dialog keeps the focus
        on retry: a :class:`RecipeNameTakenError` is surfaced inline
        without closing the dialog so the user can pick another name."""
        if self._recipe_store is None:
            return
        default = self._name_edit.text().strip()
        dlg = _SaveRecipeNameDialog(
            default=default, flavour=self._flavour, parent=self)
        while True:
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            name = dlg.recipe_name()
            try:
                recipe = self._recipe_store.create(
                    name=name,
                    flavour=self._flavour,
                    composition=self.composition(),
                )
            except RecipeNameTakenError:
                kind = (tr("Collection") if self._flavour == FLAVOUR_COLLECTION
                        else tr("Cut"))
                dlg.show_error(tr(
                    "A {kind} Recipe named '{name}' already exists. "
                    "Pick another."
                ).format(kind=kind, name=name))
                continue
            except ValueError as exc:                       # bad name
                dlg.show_error(str(exc))
                continue
            self.recipe_saved.emit(recipe)
            self._toast(tr("Recipe '{name}' saved.").format(name=name))
            return

    def _toast(self, message: str) -> None:
        """Brief acknowledgement after a Recipe save. Uses
        :class:`QMessageBox` for now — the design-system toast widget
        isn't a single seam yet; a no-icon message box reads as a quick
        ack without parking on the desktop."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setWindowTitle(tr("Save Recipe"))
        box.setText(message)
        box.exec()

    # ------------------------------------------------------------------ #
    # Load Recipe…  (spec/90 §7 Phase 5)
    # ------------------------------------------------------------------ #

    def _on_load_recipe_clicked(self) -> None:
        """Header Load Recipe… — opens the recipe picker.

        When the host wires :attr:`_recipes_tree_provider` (spec/94
        Phase 1b), the picker is the :class:`CascadingTreeMenu`
        mirroring the user's ``<library_root>/Recipes/`` folder tree
        — the surface spec/93 §4 / §9 prescribed. Otherwise falls
        back to the flat list dialog (test harnesses + legacy
        callers without a Gateway-provided tree)."""
        if self._recipes_tree_provider is not None and \
                self._recipe_resolver_by_ref is not None:
            self._open_recipe_cascading_menu()
            return
        if self._recipe_store is None:
            return
        store = self._recipe_store
        flavour = self._flavour

        def recipes_for(include_other: bool):
            return store.list(flavour=flavour, include_other=include_other)

        dlg = _LoadRecipeDialog(
            recipes_for=recipes_for, flavour=flavour, parent=self)
        dlg.recipe_chosen.connect(self._apply_recipe)
        dlg.exec()

    def _open_recipe_cascading_menu(self) -> None:
        """Show the :class:`CascadingTreeMenu` rooted at the file
        library's tree. The menu pops up under the Load Recipe button
        so it reads as a continuation of the user's gesture."""
        from mira.ui.base.cascading_tree_menu import CascadingTreeMenu
        try:
            tree = self._recipes_tree_provider()
        except Exception as exc:                          # noqa: BLE001
            log.warning("recipes_tree_provider raised: %s", exc)
            return
        if tree is None:
            return
        menu = CascadingTreeMenu(tree, title=tr("Load Recipe"), parent=self)
        menu.definition_picked.connect(self._on_recipe_picked_from_tree)
        # Pop under the Load Recipe button so the menu reads as a
        # cascading continuation of the click; fall back to the cursor
        # when the button reference isn't around (defensive).
        anchor = getattr(self, "_load_btn", None)
        if anchor is not None:
            pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
        else:
            pos = self.mapToGlobal(self.rect().center())
        menu.exec(pos)

    def _on_recipe_picked_from_tree(self, ref) -> None:
        """Resolve a :class:`DefinitionRef` chosen via the cascading
        menu back to a Recipe-shaped object and apply it."""
        if self._recipe_resolver_by_ref is None:
            return
        try:
            recipe = self._recipe_resolver_by_ref(ref)
        except Exception as exc:                          # noqa: BLE001
            log.warning("recipe_resolver_by_ref raised: %s", exc)
            return
        if recipe is None:
            return
        self._apply_recipe(recipe)

    # ------------------------------------------------------------------ #
    # Load DC…  (spec/90 §5 — items-layer mirror of Load Recipe)
    # ------------------------------------------------------------------ #

    def _on_load_dc_clicked(self) -> None:
        """Which items? band's Load DC… — open a DC picker over the
        available DCs in the operand inventory; on select, resolve the
        DC via :attr:`_dc_loader` and replace Source + Filters with the
        loaded ``(expr, filters)``.

        Spec/90 §5 — Load DC replaces ONLY the items layer (Source +
        Filters); Rules / Otherwise / Runtime / Scope / Name stay put.
        If the user has unsaved state in Source or Filters a small
        confirm dialog gates the replace so an accidental click doesn't
        nuke their composition."""
        if self._dc_loader is None:
            return
        dcs = [
            p for p in (self._ctx.available_pools or ())
            if getattr(p, "kind", "") == "dc"
        ]
        picker = _LoadDcDialog(dcs, parent=self)

        chosen: List[OperandOption] = []
        picker.dc_chosen.connect(chosen.append)
        if picker.exec() != QDialog.DialogCode.Accepted or not chosen:
            return
        dc_operand = chosen[0]

        if self._items_layer_has_state() and not self._confirm_replace_items():
            return

        try:
            expr, filters = self._dc_loader(dc_operand)
        except Exception as exc:                           # noqa: BLE001
            log.exception("dc_loader raised — keeping current items layer")
            self._show_metrics_error(tr("Load Collection failed: ") + str(exc))
            return
        self._apply_dc_to_items_layer(expr or [], filters or {})

    def _items_layer_has_state(self) -> bool:
        """True when the items layer (Source + any filter selection)
        has user content. Used by Load DC to decide whether to confirm
        the replace."""
        if self._source_chips:
            return True
        if any(chip.isChecked() for chip in self._style_chips.values()):
            return True
        if self._photos_cb is not None and not self._photos_cb.isChecked():
            return True
        if self._videos_cb is not None and not self._videos_cb.isChecked():
            return True
        if any(chip.isChecked() for chip in self._camera_chips.values()):
            return True
        if any(chip.isChecked() for chip in self._lens_chips.values()):
            return True
        return False

    def _confirm_replace_items(self) -> bool:
        """Confirm dialog before Load DC overwrites a non-empty items
        layer. Returns True when the user picked Replace."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setWindowTitle(tr("Load Collection"))
        box.setText(tr(
            "This will replace your current Source and Filters with the "
            "loaded Collection's. Continue?"))
        replace_btn = box.addButton(
            tr("Replace"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(
            tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(replace_btn)
        box.exec()
        return box.clickedButton() is replace_btn

    def _apply_dc_to_items_layer(
        self, expr: Sequence[Sequence[Any]], filters: Mapping[str, Any],
    ) -> None:
        """Replace the Source sentence + Filter chip selections with the
        loaded DC's expression + filter block. Rules / Otherwise /
        Runtime / Scope / Name stay untouched."""
        self._source_chips = self._decode_expr(expr or [])
        self._refresh_source_row()

        styles = list((filters or {}).get("styles") or [])
        for style, chip in self._style_chips.items():
            chip.setChecked(style in styles)
        media_type = (filters or {}).get("media_type") or "both"
        if self._photos_cb is not None:
            self._photos_cb.setChecked(media_type in ("both", "photo"))
        if self._videos_cb is not None:
            self._videos_cb.setChecked(media_type in ("both", "video"))
        if self._show_hardware:
            cams = (filters or {}).get("camera_ids") or []
            for cam, chip in self._camera_chips.items():
                chip.setChecked(cam in cams)
            lenses = (filters or {}).get("lens_models") or []
            for lens, chip in self._lens_chips.items():
                chip.setChecked(lens in lenses)
        self._kick_probe()

    def _apply_recipe(self, recipe: Any) -> None:
        """Pre-fill every section from a :class:`um.Recipe`. Tears down
        the in-memory chip / rule state, repopulates Source / Scope /
        Filters / Rules / Otherwise from the composition_json, then
        kicks one probe so the metrics + per-rule labels refresh.

        spec/90 §5.5 — a Collection Recipe loaded into the Cut dialog
        may carry hidden filters (Camera / Lens / Faces / Scope). Those
        filters survive in the composition but the Cut face can't edit
        them; :meth:`_apply_cross_flavour_banner` posts a warning above
        the metrics line so the mismatch is honest."""
        composition = RecipeStore.composition(recipe)
        self._apply_composition(composition)
        self._name_edit.setText(getattr(recipe, "name", "") or "")
        loaded_flavour = getattr(recipe, "flavour", self._flavour)
        if loaded_flavour != self._flavour:
            self._apply_cross_flavour_banner(loaded_flavour, composition)
        else:
            self._clear_cross_flavour_banner()
        self._kick_probe()

    def _apply_composition(self, composition: Mapping[str, Any]) -> None:
        """Tear down the source / scope / rule chip lists and rebuild
        them from ``composition``. Filter chips + Otherwise + presentation
        spinners are reset in place."""
        # Source.
        self._source_chips = self._decode_expr(
            composition.get("source") or [])
        self._refresh_source_row()

        # Scope (Collection only). When a Cut dialog loads a Collection
        # Recipe (spec/90 §5.5) the scope decodes but the Cut face has
        # no row to render it; ``_show_scope`` skips the refresh.
        if self._show_scope:
            self._scope_chips = self._decode_expr(
                composition.get("scope") or [])
            self._refresh_scope_row()

        # Rules + Otherwise.
        self._rules = self._decode_rules(composition.get("rules") or [])
        self._otherwise = (
            composition.get("otherwise")
            if composition.get("otherwise") in VERDICTS
            else VERDICT_SKIP
        )
        self._refresh_rules_rows()
        if hasattr(self, "_otherwise_pill"):
            self._otherwise_pill.set_verdict(self._otherwise)

        # Filters (Style + Media; Camera / Lens only when ``show_hardware``).
        filters = composition.get("filters") or {}
        if not isinstance(filters, Mapping):
            filters = {}
        styles = filters.get("styles") or []
        for style, chip in self._style_chips.items():
            chip.setChecked(style in styles)
        media_type = filters.get("media_type") or "both"
        if self._photos_cb is not None:
            self._photos_cb.setChecked(media_type in ("both", "photo"))
        if self._videos_cb is not None:
            self._videos_cb.setChecked(media_type in ("both", "video"))
        if self._show_hardware:
            cams = filters.get("camera_ids") or []
            for cam, chip in self._camera_chips.items():
                chip.setChecked(cam in cams)
            lenses = filters.get("lens_models") or []
            for lens, chip in self._lens_chips.items():
                chip.setChecked(lens in lenses)

        # Presentation (Runtime spinners). Reads target_s / max_s in
        # seconds; the dialog displays minutes. spec/90 §5.1: has_budget
        # derives from whether the loaded recipe carries a real bound —
        # a Recipe saved with target_s=max_s=None re-opens with the
        # checkbox unchecked.
        presentation = composition.get("presentation") or {}
        if isinstance(presentation, Mapping):
            target_s = presentation.get("target_s")
            max_s = presentation.get("max_s")
            has_target = isinstance(target_s, (int, float))
            has_max = isinstance(max_s, (int, float))
            if has_target:
                self._target_minutes = max(1, int(round(float(target_s) / 60)))
                if hasattr(self, "_target_spin"):
                    self._target_spin.setValue(self._target_minutes)
            if has_max:
                self._max_minutes = max(1, int(round(float(max_s) / 60)))
                if hasattr(self, "_max_spin"):
                    self._max_spin.setValue(self._max_minutes)
            photo_s = presentation.get("photo_s")
            if isinstance(photo_s, (int, float)):
                self._per_photo_seconds = max(0.1, float(photo_s))
                if hasattr(self, "_per_photo_spin"):
                    self._per_photo_spin.setValue(self._per_photo_seconds)
            # Sync has_budget + the checkbox / spinner-enabled state.
            self._has_budget = bool(has_target or has_max)
            if hasattr(self, "_budget_check"):
                self._budget_check.blockSignals(True)
                self._budget_check.setChecked(self._has_budget)
                self._budget_check.blockSignals(False)
            if hasattr(self, "_target_spin"):
                self._target_spin.setEnabled(self._has_budget)
            if hasattr(self, "_max_spin"):
                self._max_spin.setEnabled(self._has_budget)

    def _decode_expr(
        self, expr: Sequence[Sequence[Any]],
    ) -> List[Tuple[str, OperandOption]]:
        """Translate a composition expression back into the
        ``(join, OperandOption)`` chip-list the dialog stores. Unknown
        ops fall back to ``or`` so a malformed Recipe still loads."""
        op_to_join = {"+": JOIN_OR, "-": JOIN_BUT_NOT, "&": JOIN_AND}
        out: List[Tuple[str, OperandOption]] = []
        for index, pair in enumerate(expr or ()):
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                continue
            op, operand = pair[0], pair[1]
            join = JOIN_OR if index == 0 else op_to_join.get(op, JOIN_OR)
            out.append((join, self._operand_from_encoded(operand)))
        return out

    def _decode_rules(
        self, rules: Sequence[Any],
    ) -> List[Tuple[List[Tuple[str, OperandOption]], str]]:
        out: List[Tuple[List[Tuple[str, OperandOption]], str]] = []
        for raw in rules or ():
            if not isinstance(raw, Mapping):
                continue
            predicate = self._decode_expr(raw.get("predicate") or [])
            verdict = raw.get("verdict")
            if verdict not in VERDICTS:
                verdict = VERDICT_SKIP
            out.append((predicate, verdict))
        return out

    def _operand_from_encoded(self, operand: Any) -> OperandOption:
        """Re-hydrate an operand. Looks for a match in the ctx's
        inventories first (so the live count is current); falls back to
        a placeholder chip carrying the raw values so the Recipe still
        loads when the named operand no longer exists."""
        if isinstance(operand, str):
            tag = operand
            for opt in self._ctx.available_pools:
                if (opt.kind == "base" and
                        (opt.tag or opt.name.lstrip("#")) == tag):
                    return opt
            return OperandOption(name=f"#{tag}", kind="base", tag=tag)
        if not isinstance(operand, Mapping):
            return OperandOption(name=str(operand), kind="base")
        kind = operand.get("kind") or "base"
        if kind == "event":
            uuid = operand.get("uuid") or ""
            for opt in self._ctx.available_events:
                if opt.uuid == uuid:
                    return opt
            return OperandOption(
                name=f"[{uuid}]", kind="event", uuid=uuid)
        if kind == "date_range":
            start = operand.get("start") or ""
            end = operand.get("end") or ""
            return OperandOption(
                name=f"[{start} — {end}]", kind="date_range",
                start=start, end=end)
        if kind == "person":
            person_id = operand.get("id") or ""
            for opt in self._ctx.available_people:
                if opt.id == person_id:
                    return opt
            return OperandOption(
                name=f"[{person_id}]", kind="person", id=person_id)
        tag = operand.get("tag") or ""
        op_id = operand.get("id")
        inventory: Sequence[OperandOption]
        if kind == "event_collection":
            inventory = self._ctx.available_event_collections
        else:
            inventory = self._ctx.available_pools
        for opt in inventory:
            if (opt.kind == kind and
                    ((op_id and opt.id == op_id) or
                     (opt.tag or opt.name.lstrip("#")) == tag)):
                return opt
        return OperandOption(
            name=f"#{tag}" if tag else str(operand),
            kind=kind, tag=tag or None, id=op_id)

    def _apply_cross_flavour_banner(
        self, loaded_flavour: str, composition: Mapping[str, Any],
    ) -> None:
        """spec/90 §5.5 — when a Collection Recipe is loaded into a Cut
        dialog (or vice versa), surface the not-editable filters as a
        banner above the metrics line. The banner shares the metrics
        banner slot (mutually exclusive with the resolver error banner —
        the next probe clears it)."""
        fields: List[str] = []
        filters = composition.get("filters") or {}
        if isinstance(filters, Mapping):
            if not self._show_hardware:
                if filters.get("camera_ids"):
                    fields.append(tr("Camera"))
                if filters.get("lens_models"):
                    fields.append(tr("Lens"))
                if filters.get("person_ids"):
                    fields.append(tr("Faces"))
        if not self._show_scope and composition.get("scope"):
            fields.append(tr("Scope"))
        self._cross_flavour_fields = fields
        if not fields:
            return
        message = tr(
            "This Recipe filters by {fields} — not editable here."
        ).format(fields=" + ".join(fields))
        self._show_metrics_hint(message)

    def _clear_cross_flavour_banner(self) -> None:
        self._cross_flavour_fields = []

    # ------------------------------------------------------------------ #
    # Start — gate + draft handoff  (spec/90 §7 Phase 5)
    # ------------------------------------------------------------------ #

    def _refresh_start_enabled(self) -> None:
        """Gate the Start button.

        **New Cut** (``is_editing=False``): disabled when source is
        empty / the last probe raised :class:`RecipeResolutionError` /
        the last probe returned an empty pool. Enabled when the last
        probe returned a non-empty pool with no errors. Spec/90 Phase
        4e original gate — protects accidental empty new Cuts.

        **Adjust an existing Cut** (``is_editing=True``): enabled as
        long as Source is non-empty, regardless of probe state. The
        user may be editing metadata (budget, name, etc.) on a Cut
        whose source's current resolution is empty (deleted exports)
        or errored (missing operand) — neither should block saving the
        metadata change. The picker session handles an empty pool
        gracefully + preserves the existing cut's stray members on
        re-entry."""
        if not hasattr(self, "_start_btn"):
            return
        if not self._source_chips:
            self._start_btn.setEnabled(False)
            return
        if self._is_editing:
            # Permissive: source non-empty is enough.
            self._start_btn.setEnabled(True)
            return
        res = self._last_resolution
        if res is None:
            # Probe hasn't completed yet (or wasn't wired). With no
            # probe the dialog can still hand off to the picker as
            # long as the source is non-empty; a missing resolver is a
            # smoke / unit-test path, not a production path.
            self._start_btn.setEnabled(self._recipe_probe is None)
            return
        self._start_btn.setEnabled(bool(res.pool))

    def _on_start_clicked(self) -> None:
        """Footer ▶ Start — build the composition, adapt it to the right
        draft flavour, emit :attr:`start_requested`, and accept().

        Cut flavour → :class:`CutDraft` via :func:`recipe_to_cut_draft`;
        the host wires :class:`CutSession.from_draft` + the event-scope
        picker.

        Collection flavour (spec/90 §7 Phase 5, completed Phase 4f) →
        :class:`CrossEventCutDraft` via
        :func:`recipe_to_cross_event_cut_draft`; the host wires
        :class:`CrossEventCutSession.from_draft` + the cross-event
        picker. The downstream session resolves library-wide; scope
        chips in the composition are an accepted-but-not-yet-enforced
        hint (the cross-event session has no scope param). Rules
        collapse to the §1.5 sugar via the Otherwise verdict — the
        cross-event picker doesn't yet honour rule-based seeding."""
        from mira.shared.recipe_draft_adapter import (
            recipe_to_cross_event_cut_draft,
            recipe_to_cut_draft,
        )
        from mira.user_store import models as um
        composition = self.composition()
        name = self._name_edit.text().strip()
        recipe = um.Recipe(
            id="",
            name=name,
            flavour=self._flavour,
            composition_json=json.dumps(composition),
            created_at="",
            updated_at="",
        )
        if self._flavour == FLAVOUR_COLLECTION:
            draft = recipe_to_cross_event_cut_draft(recipe)
        else:
            draft = recipe_to_cut_draft(recipe)
            # spec/94 Phase 3 — seed the Cut session's initial verdicts from
            # the recipe resolver's verdict map. Cheap because the probe
            # already runs at every composition change; one extra call at
            # Start time is the canonical snapshot. Failure (probe absent,
            # missing operand, etc.) just leaves the draft's seed empty and
            # the session falls back to the pin_mode default.
            seed = self._compute_start_seed(composition)
            if seed:
                from dataclasses import replace as _replace
                draft = _replace(draft, seed=seed)
        self.start_requested.emit(draft)
        self.accept()

    def _compute_start_seed(
        self,
        composition: dict,
    ) -> Tuple[Tuple[str, bool], ...]:
        """Snapshot the recipe resolver's seed verdict map at Start time
        and shape it for :class:`CutDraft`.

        Returns a tuple of ``(export_relpath, picked)`` pairs so the
        draft stays frozen-friendly. An empty tuple signals "no seed"
        — the session then uses the legacy pin_mode default. We never
        let a resolver failure block Start; the probe path already
        surfaces missing-operand errors to the user."""
        probe = self._recipe_probe
        if probe is None:
            return ()
        try:
            resolution = probe(composition)
        except Exception:                                   # noqa: BLE001
            return ()
        seed = getattr(resolution, "seed", None) or {}
        return tuple(sorted(seed.items()))

    # ------------------------------------------------------------------ #
    # Name preview
    # ------------------------------------------------------------------ #

    def _on_name_changed(self, text: str) -> None:
        cleaned = text.strip().lower().replace(" ", "_")
        if cleaned:
            self._name_tag_hint.setText(f"#{cleaned}")
        else:
            self._name_tag_hint.setText("(tag will preview here)")
        # Save as Recipe gates on Name + Source; refresh both bands.
        self._refresh_save_button_states()

    # ------------------------------------------------------------------ #
    # Band-header save-button enablement  (spec/90 §5.5)
    # ------------------------------------------------------------------ #

    def _refresh_save_button_states(self) -> None:
        """Gate the items-band + recipe-toolbar buttons:

        * **Save as DC** — needs a non-empty Source (nothing to save
          otherwise) and a wired :attr:`_dc_creator` (smokes / unit
          tests without persistence pass ``None``).
        * **Save as Recipe** — needs a non-empty Source (spec/90 §1.1)
          AND a non-empty Name, plus a wired :attr:`_recipe_store`.
        * **Load DC** — needs at least one DC in the operand inventory
          and a wired :attr:`_dc_loader`.

        Called from every source / name mutator + the Recipe-load path."""
        has_source = bool(self._source_chips)
        if hasattr(self, "_save_dc_btn"):
            self._save_dc_btn.setEnabled(
                has_source and self._dc_creator is not None)
        if hasattr(self, "_save_recipe_btn"):
            has_name = bool(self._name_edit.text().strip())
            self._save_recipe_btn.setEnabled(
                has_source and has_name
                and self._recipe_store is not None)
        if hasattr(self, "_load_dc_btn"):
            has_dcs = any(
                getattr(p, "kind", "") == "dc"
                for p in (self._ctx.available_pools or ()))
            self._load_dc_btn.setEnabled(
                has_dcs and self._dc_loader is not None)

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
        Phase 4e save-recipe / start-picker wiring.

        The ``presentation`` block carries the runtime spinners through
        to :func:`recipe_to_cut_draft` (which reads ``target_s`` /
        ``max_s`` / ``photo_s`` from it). When :attr:`_has_budget` is
        False, ``target_s`` + ``max_s`` are emitted as ``None`` so the
        downstream Cut shows "no limit" (cut_session_page §131).
        Music / card_style / overlay_fields / separators are Phase 4
        UI gaps — left out of the presentation block until the dialog
        gains those controls."""
        comp: Dict[str, Any] = {
            "source": self.source_expression(),
            "filters": self.filters_payload(),
            "rules": self.rules_expression(),
            "otherwise": self.otherwise_verdict(),
            "presentation": self.presentation_payload(),
        }
        if self._show_scope:
            comp["scope"] = self.scope_expression()
        return comp

    def presentation_payload(self) -> dict:
        """The ``presentation`` block of the composition (spec/90 §5.1).
        Emits the runtime fields the dialog currently exposes; leaves
        the unimplemented Phase 4 fields (music_category, card_style,
        overlay_fields, separators) out of the dict so the adapter's
        tolerant defaults take over."""
        target_s: Optional[int] = (
            int(self._target_minutes) * 60 if self._has_budget else None
        )
        max_s: Optional[int] = (
            int(self._max_minutes) * 60 if self._has_budget else None
        )
        return {
            "target_s": target_s,
            "max_s": max_s,
            "photo_s": float(self._per_photo_seconds),
        }

    @staticmethod
    def _encode_operand(operand: OperandOption) -> Any:
        """Translate an :class:`OperandOption` into the spec/81 / spec/90
        operand encoding the resolver consumes.

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
    "_LoadDcDialog",
    "_SaveAsDcNameDialog",
]
