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
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
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

# Join words (spec/90 §3.2). The dropdown widget that picks between them
# lands in Phase 4c; Phase 4a renders the default ``or`` between any two
# chips so the sentence reads correctly.
JOIN_OR = "or"
JOIN_AND = "and"
JOIN_BUT_NOT = "but not in"

# Mapping the join word ↔ the spec/81 resolver operator. The picker
# widget in Phase 4c will emit join words; the source-sentence encoder
# translates here.
_JOIN_TO_OP = {JOIN_OR: "+", JOIN_AND: "&", JOIN_BUT_NOT: "-"}


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
    """One operand the user can drop into a Source / Rule predicate.

    spec/90 §3.1 alphabet: base universes, DCs, Cuts, Event Collections,
    Date ranges, Persons, hardware vocabulary, item vocabulary. Each
    chip in the dialog renders one of these. Phase 4a admits the source-
    section operands; Phase 4c grows the set to cover rule predicates."""

    name: str               # display string, e.g. '#exported'
    count: int = 0          # live count beside the name (spec/90 §3.4)
    kind: str = "base"      # 'base' | 'dc' | 'cut' | 'event_collection'
    id: Optional[str] = None
    tag: Optional[str] = None  # canonical tag without '#'; falls back to ``name``


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
    # DCs, Cuts ship in Phase 4a; Event Collections + Persons join the
    # picker in Phase 4b/4c via the same shape.
    available_pools: List[OperandOption] = field(default_factory=list)

    # Filter vocabularies (spec/90 §4). Each is the list of distinct
    # values the picker offers; the user multi-selects.
    available_styles: List[str] = field(default_factory=list)
    available_cameras: List[str] = field(default_factory=list)
    available_lenses: List[str] = field(default_factory=list)

    # Initial selections — empty for a fresh Recipe; populated when
    # loading a saved Recipe (Phase 4e).
    selected_source: List[Tuple[str, OperandOption]] = field(default_factory=list)
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


class _OperandPickerPopover(QFrame):
    """Sectioned popover for picking an operand to add (spec/90 §3.4).

    Floats over the dialog body as a small modal frame (anchored under the
    ``+`` button). Sectioned by chip type — base universes first, then
    DCs, then Cuts (Event Collections appear only when
    ``show_event_collections=True`` — Collection-face Scope picker; Phase
    4b wires that). A search line input at the top narrows by name; live
    counts ride beside every entry from :attr:`OperandOption.count`.

    "Save as DC…" sits at the bottom as a placeholder button — Phase 4e
    wires the modal that fires :attr:`NewRecipeDialog.save_as_dc_requested`."""

    chosen = pyqtSignal(object)            # OperandOption
    save_as_dc_requested = pyqtSignal()

    def __init__(
        self,
        pools: Sequence[OperandOption],
        *,
        show_event_collections: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        # Popup frame: borderless, modal-feeling, dismisses on outside click.
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("OperandPickerPopover")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(280)
        self._pools = list(pools)
        self._show_event_collections = show_event_collections
        self._rows: List[Tuple[OperandOption, QPushButton]] = []

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

        outer.addWidget(_divider())
        self._save_btn = ghost_button(tr("Save as DC…"))
        self._save_btn.setObjectName("OperandPickerSaveAsDc")
        self._save_btn.setToolTip(tr(
            "Save the current source as a Dynamic Collection — Phase 4e."))
        self._save_btn.clicked.connect(self._on_save_as_dc)
        outer.addWidget(self._save_btn)

    def _populate_sections(self) -> None:
        """Group the pool inventory by kind, render a section header per
        group, then one row per operand. spec/90 §3.4 order: Base
        universes · DCs · Cuts · Event Collections."""
        order = [
            ("base", tr("Base universes")),
            ("dc", tr("Dynamic Collections")),
            ("cut", tr("Cuts")),
        ]
        if self._show_event_collections:
            order.append(("event_collection", tr("Event Collections")))

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
        """Phase 4b placeholder. Cross-event Scope sentence — events +
        Event Collections + date ranges (spec/90 §3.1)."""
        host = QWidget()
        host.setObjectName("ScopeSection")
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        v.addWidget(_micro(tr("Scope")))
        v.addWidget(_placeholder(tr("Scope: (Phase 4b)")))
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
        chip with its preceding join-word label, then the trailing ``+``
        button. Phase 4a renders join words as inline labels (the
        dropdown widget lands in Phase 4c)."""
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
                join_lbl = QLabel(join)
                join_lbl.setObjectName("PoolFormulaOp")
                self._source_row.addWidget(join_lbl)
            chip = _SourceChip(operand.name, operand.count, self._source_box)
            chip.removed.connect(
                lambda i=index: self._remove_source_chip(i))
            self._source_row.addWidget(chip)

        self._source_row.addWidget(self._build_add_operand_button())
        self._source_row.addStretch()
        self._refresh_source_summary()

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
        host = QWidget()
        host.setObjectName("RulesSection")
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        v.addWidget(_micro(tr("Rules")))
        v.addWidget(_placeholder(tr("Rules: (Phase 4c)")))
        return host

    def _build_otherwise_section(self) -> QWidget:
        host = QWidget()
        host.setObjectName("OtherwiseSection")
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        v.addWidget(_micro(tr("Otherwise")))
        v.addWidget(_placeholder(tr("Otherwise: (Phase 4c)")))
        return host

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

    @staticmethod
    def _encode_operand(operand: OperandOption) -> Any:
        """Operand encoding mirroring
        :meth:`mira.ui.pages.new_cut_dialog.NewCutDialog._operand_for_name`.
        Base universes stay bare strings; named kinds become typed refs."""
        tag = operand.tag or operand.name.lstrip("#")
        if operand.kind == "base":
            return tag
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
    "NewRecipeContext",
    "NewRecipeDialog",
    "OperandOption",
]
