"""Surface 02 — Event Header dialog.

The event identity surface (spec/64 §3): name, type, subtype, description,
duration, context, experience type, creative focus, participants. Opens
at every event birth and from the Event Header door on the event tile /
menu.

Composition (design-system surface-02 spec):
    Header bar:  accent icon tile · "Event Header" + event-name subtitle
                 · close ✕ · 1px divider
    Body:        scrollable stacked fields, each with a Micro uppercase
                 label above the control (required asterisks in accent).
                 Three sections — IDENTITY / LOGISTICS / TAGS — separated
                 by small accent section headers. Creative Focus pills
                 carry their category SVG icons (Macro=beetle, Birds,
                 Wildlife=paw, Landscape=sun+hills, Urban=skyline; None
                 has no icon, design spec).
    Footer:      1px top divider · ghost Cancel · primary Save event

The mutual-exclusion rule on Creative Focus (None ⇔ subjects, spec/64
§3.4) is preserved. Subtype suggestions refill on type change (uses
``event_classification.subtype_presets_for``).

Save event is gated on Name + Type + Subtype being set (§3.6). Returns
the form as a dict via :meth:`header_info`.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QIcon,
    QImage,
    QPainter,
    QPixmap,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mira import event_classification
from mira.ui.base.flow_layout import FlowLayout
from mira.ui.design import (
    GLYPH_CROSS,
    GLYPH_EVENT,
    ghost_button,
    line_input,
    pill_toggle,
    primary_button,
    select,
    tinted_svg_pixmap,
)
from mira.ui.i18n import tr
from mira.ui.palette import PALETTE

log = logging.getLogger(__name__)


_CATEGORY_DIR = (
    Path(__file__).resolve().parents[3]
    / "assets" / "icons" / "categories"
)

#: Creative-Focus option → SVG icon stem under assets/icons/categories/.
#: 'none' has no icon (the design spec is explicit: it's the exclusive
#: 'not a photo event' answer and reads as a plain pill).
_FOCUS_ICON_NAME = {
    "macro":        "macro",
    "birds":        "birds",
    "wildlife":     "wildlife",
    "landscape":    "landscape",
    "urban_street": "urban",
}


def _palette_mode() -> str:
    app = QApplication.instance()
    return (app.property("theme") if app else None) or "dark"


def _micro(text: str, *, required: bool = False) -> QLabel:
    """Uppercase micro label above a form control, with optional accent
    asterisk when the field is required. The asterisk is intentionally
    larger and bolder than the surrounding caps so the required-field
    signal actually carries (spec/65 §3.2 — the prior small-* read as
    decorative)."""
    p = PALETTE[_palette_mode()]
    accent = p["accent"]
    if required:
        lbl = QLabel(
            f"{text.upper()}"
            f"<span style='color:{accent}; font-size: 14px;"
            f" font-weight: 800; vertical-align: -1px;'>&nbsp;*</span>"
        )
        lbl.setTextFormat(Qt.TextFormat.RichText)
    else:
        lbl = QLabel(text.upper())
    lbl.setObjectName("Micro")
    return lbl


class _SectionHeader(QWidget):
    """Section divider — small uppercase accent label + a thin accent
    underline rule + a touch of breathing room above.

    Spec/65 §3.2: the bare-label section dividers read as decoration;
    the rule beneath each one is what turns them into a real visual
    cleft. The accent fades out across the row so the rule reads as a
    soft underline, not a hard divider — matches the design-system's
    quieter group rules.
    """

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 14, 0, 4)
        v.setSpacing(6)
        p = PALETTE[_palette_mode()]
        lbl = QLabel(text.upper())
        lbl.setObjectName("SectionEyebrow")  # shared section-eyebrow role (redesign.qss)
        v.addWidget(lbl)
        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet(  # pragma: no-qss — decorative accent-fade gradient (computed)
            "background: qlineargradient("
            f"x1:0, y1:0, x2:1, y2:0,"
            f" stop:0 {_with_alpha(p['accent'], 90)},"
            f" stop:0.6 {_with_alpha(p['accent'], 28)},"
            f" stop:1 {_with_alpha(p['line'], 0)});"
            " border: none;"
        )
        v.addWidget(rule)


def _with_alpha(hex_color: str, alpha255: int) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha255 / 255:.3f})"


def _section_header(text: str) -> QWidget:
    """Back-compat wrapper so the body builder stays declarative."""
    return _SectionHeader(text)


def _field(
    title: str, widget: QWidget, *, required: bool = False
) -> QGroupBox:
    """Wrap ``widget`` in the canonical ``#FormFieldGroup`` titled group
    box (spec/92 §2.3.1). The title is rendered UPPERCASE per §2.0
    ('labelling chrome' tier); required fields append a trailing ``*``
    (the accent-coloured ``[required="true"]`` selector is reserved for
    a later sweep per spec/92 Appendix A — for now the asterisk inherits
    the title's ink_soft tint, consistent with the New Cut surface).

    The group box owns the title rendering (notched into the top
    border); the inner ``QVBoxLayout`` holds the bare input widget so
    caller-side wiring (``signals``, ``setObjectName``, ``setToolTip``)
    is unchanged. spec/92 §4 Stage 2b."""
    box = QGroupBox()
    box.setObjectName("FormFieldGroup")
    raw = title.upper() + (" *" if required else "")
    box.setTitle(raw)
    inner = QVBoxLayout(box)
    inner.setContentsMargins(0, 6, 0, 0)
    inner.setSpacing(0)
    inner.addWidget(widget)
    return box


def _tinted_svg_icon(icon_stem: str, color_hex: str, size: int = 18) -> QIcon:
    """Render the named category SVG into a fixed-size QIcon tinted to
    ``color_hex``. Returns an empty QIcon if the file is missing — the
    QPushButton just renders without a leading icon."""
    path = _CATEGORY_DIR / f"{icon_stem}.svg"
    if not path.exists():
        return QIcon()
    renderer = QSvgRenderer(str(path))
    if not renderer.isValid():
        return QIcon()
    buf = QImage(size, size, QImage.Format.Format_ARGB32)
    buf.fill(0)
    p = QPainter(buf)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(buf.rect(), QColor(color_hex))
    p.end()
    return QIcon(QPixmap.fromImage(buf))


class EventHeaderDialog(QDialog):
    """The redesigned Event Header dialog.

    Public surface mirrors the legacy :class:`EventHeaderDialog` so call
    sites can swap without other changes:

        dlg = EventHeaderDialog(parent=parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            info = dlg.header_info()  # same dict shape as the legacy
    """

    _TYPE_CHOICES = (
        (event_classification.EVENT_TYPE_TRIP, "Trip"),
        (event_classification.EVENT_TYPE_SESSION, "Session"),
        (event_classification.EVENT_TYPE_OCCASION, "Occasion"),
        (event_classification.EVENT_TYPE_PROJECT, "Project"),
    )

    def __init__(
        self,
        *,
        existing_info: Optional[dict] = None,
        on_locate_originals: Optional[callable] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.resize(720, 820)
        self._was_applied = False
        self._existing_name = (
            (existing_info or {}).get("name") or ""
        ).strip()
        # Window title tracks the flow: "New Event" while creating, the
        # neutral "Event Header" while editing an existing one.
        self.setWindowTitle(
            tr("Event Header") if self._existing_name else tr("New Event")
        )
        # Optional secondary action surfaced in the footer when the
        # dialog opens for an existing event (the locate/relink entry
        # point per charter §7). Left None for the create flow.
        self._on_locate_originals = on_locate_originals
        self._creative_chips: dict[str, QPushButton] = {}
        self._participant_chips: dict[str, QPushButton] = {}

        self._build_ui()
        if existing_info:
            self._apply_existing(existing_info)
        self._refresh_save_enabled()

    # ── Build ─────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_header_bar())
        outer.addWidget(self._divider())
        outer.addWidget(self._build_body(), 1)
        outer.addWidget(self._divider())
        outer.addWidget(self._build_footer())

    @staticmethod
    def _divider() -> QFrame:
        d = QFrame()
        d.setFrameShape(QFrame.Shape.HLine)
        d.setObjectName("DialogDivider")  # themed hairline (redesign.qss)
        return d

    def _build_header_bar(self) -> QWidget:
        host = QWidget()
        h = QHBoxLayout(host)
        h.setContentsMargins(18, 14, 14, 14)
        h.setSpacing(12)
        p = PALETTE[_palette_mode()]
        # Accent icon tile — the mockup uses a calendar/event SVG (rect +
        # day-divider + tear-off tab), NOT the Unicode pencil the migration
        # used. The tile reads colour tokens from the live palette so the
        # light theme picks up accent_soft = #eceaff instead of dark's
        # #211f3a (the prior inline hex broke the tile in light mode).
        tile = QLabel()
        tile.setObjectName("CutHeaderTile")  # shared accent-soft dialog tile (redesign.qss)
        tile.setFixedSize(32, 32)
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setPixmap(
            tinted_svg_pixmap(GLYPH_EVENT, 18, QColor(p["accent"]))
        )
        h.addWidget(tile)

        # Title + subtitle. The dialog serves two flows — creating a
        # brand-new event (no name yet) and editing an existing one. Always
        # render a two-line lockup so the title never sits alone next to the
        # 32px tile (the create flow used to show a single lonely line). The
        # subtitle carries the event name when editing, or a one-line guide
        # when creating.
        is_new = not self._existing_name
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)
        title = QLabel(tr("New Event") if is_new else tr("Event Header"))
        title.setObjectName("CardTitle")
        text_col.addWidget(title)
        sub = QLabel(
            tr("Set up identity, logistics, and tags.")
            if is_new else self._existing_name
        )
        sub.setObjectName("Sub")
        text_col.addWidget(sub)
        h.addLayout(text_col)
        h.addStretch()

        # Close X — mockup uses a 30x30 rounded-square (9px radius). The
        # glyph is the line-icon family's cross.svg (spec/65 §2.1 — no
        # Unicode `✕` placeholders); rendered via QIcon so the X tints
        # correctly per theme and never falls back to a glyph the font
        # can't render.
        close = QPushButton()
        close.setObjectName("DialogClose")
        close.setFixedSize(30, 30)
        close.setIcon(QIcon(
            tinted_svg_pixmap(GLYPH_CROSS, 14, QColor(p["ink_soft"]))
        ))
        close.setIconSize(QSize(14, 14))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setToolTip(tr("Cancel and close"))  # styled by QPushButton#DialogClose (redesign.qss)
        close.clicked.connect(self.reject)
        h.addWidget(close)
        return host

    def _build_body(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(14)

        # ── Section 1: IDENTITY ─────────────────────────────────────────
        v.addWidget(_section_header("Identity"))

        # 1. Name — spec/92 §2.3.1 canonical FormFieldGroup wrap.
        self._name_edit = line_input(tr("e.g. 2026 - Nepal trek"))
        self._name_edit.setToolTip(tr("The event's identity name."))
        self._name_edit.textChanged.connect(
            lambda _t: self._refresh_save_enabled()
        )
        v.addWidget(_field("Name", self._name_edit, required=True))

        # 2. Type / Subtype side by side — each its own FormFieldGroup.
        row2 = QHBoxLayout()
        row2.setSpacing(14)
        self._type_combo = select([])
        self._type_combo.setObjectName("DesignSelect")
        self._type_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        for key, label in self._TYPE_CHOICES:
            self._type_combo.addItem(tr(label), key)
        self._type_combo.setToolTip(tr(
            "What kind of event was this — files it under the right umbrella."
        ))
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        row2.addWidget(
            _field("Type", self._type_combo, required=True), 1
        )

        self._subtype_combo = QComboBox()
        self._subtype_combo.setObjectName("DesignSelect")
        self._subtype_combo.setEditable(True)
        self._subtype_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._subtype_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._subtype_combo.setToolTip(tr(
            "Pick from the suggestions or type your own."
        ))
        self._update_subtype_combo()
        self._subtype_combo.editTextChanged.connect(
            lambda _t: self._refresh_save_enabled()
        )
        row2.addWidget(
            _field("Subtype", self._subtype_combo, required=True), 1
        )
        v.addLayout(row2)

        # 3. Description
        self._desc_edit = QPlainTextEdit()
        self._desc_edit.setObjectName("DesignText")
        self._desc_edit.setPlaceholderText(tr(
            "One short paragraph — shown on the event tile."
        ))
        self._desc_edit.setFixedHeight(72)
        self._desc_edit.setToolTip(tr("Brief description of the event."))
        v.addWidget(_field("Description", self._desc_edit))

        # ── Section 2: LOGISTICS ────────────────────────────────────────
        v.addWidget(_section_header("Logistics"))

        # BUGS.md B-012 (Nelson 2026-06-17) — From/To fields removed from
        # the dialog. The dates live in ``event.db`` but are DERIVED ONLY
        # from the trip_days table: ``gateway.recompute_event_date_range``
        # runs on every trip_day batch (event creation, Collect, plan
        # editor, ingest) and writes ``start_date = min(day.date)`` /
        # ``end_date = max(day.date)``. There is no manual override.
        # Supersedes spec/77 §5 floor; the Collect denominator now reads
        # the same (max − min + 1) range from the derived columns.

        # 5. Duration / Unit — each its own FormFieldGroup, side-by-side.
        row_dur = QHBoxLayout()
        row_dur.setSpacing(14)
        self._duration_value_spin = QSpinBox()
        self._duration_value_spin.setObjectName("DesignSpin")
        self._duration_value_spin.setMinimum(0)
        self._duration_value_spin.setMaximum(9999)
        self._duration_value_spin.setSpecialValueText(tr("—"))
        self._duration_value_spin.setToolTip(tr(
            "How long the event lasted. Leave at — to skip."
        ))
        row_dur.addWidget(_field("Duration", self._duration_value_spin), 1)

        self._duration_unit_combo = QComboBox()
        self._duration_unit_combo.setObjectName("DesignSelect")
        self._duration_unit_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._duration_unit_combo.addItem(tr("(unit)"), "")
        for unit in event_classification.DURATION_UNITS:
            self._duration_unit_combo.addItem(tr(unit), unit)
        self._duration_unit_combo.currentIndexChanged.connect(
            self._on_duration_unit_changed
        )
        row_dur.addWidget(_field("Unit", self._duration_unit_combo), 2)
        v.addLayout(row_dur)

        # 5. Context
        self._context_combo = self._make_single_select(
            event_classification.CONTEXT_OPTIONS,
            event_classification.CONTEXT_LABELS,
            event_classification.CONTEXT_DESCRIPTIONS,
            tooltip=tr(
                "The baseline environment of the event."
                " Hover an option for its definition."
            ),
        )
        v.addWidget(_field("Context", self._context_combo))

        # 6. Experience Type
        self._experience_combo = self._make_single_select(
            event_classification.EXPERIENCE_TYPE_OPTIONS,
            event_classification.EXPERIENCE_TYPE_LABELS,
            event_classification.EXPERIENCE_TYPE_DESCRIPTIONS,
            tooltip=tr(
                "The primary vibe, intent, or creative energy."
            ),
        )
        v.addWidget(_field("Experience Type", self._experience_combo))

        # ── Section 3: TAGS ─────────────────────────────────────────────
        v.addWidget(_section_header("Tags"))

        # 7. Creative Focus — multi-select pill toggles with None exclusion.
        # FlowLayout wraps when the row outgrows the dialog width.
        # Each subject pill carries its category-icon SVG; 'None' has no
        # icon (it's the exclusive 'not a photo event' answer).
        cf_host = QWidget()
        cf_flow = FlowLayout(cf_host, spacing=6)
        cf_flow.setContentsMargins(0, 0, 0, 0)
        for option in event_classification.CREATIVE_FOCUS_OPTIONS:
            label = event_classification.CREATIVE_FOCUS_LABELS.get(option, option)
            chip = pill_toggle(tr(label))
            icon_stem = _FOCUS_ICON_NAME.get(option)
            if icon_stem:
                chip.setIcon(_tinted_svg_icon(icon_stem, "#8b94a7"))
                chip.setIconSize(QSize(16, 16))
            chip.clicked.connect(
                lambda _checked=False, opt=option: self._on_creative_chip(opt)
            )
            self._creative_chips[option] = chip
            cf_flow.addWidget(chip)
        v.addWidget(_field("Creative Focus", cf_host))

        # 8. Participants — multi-select pill toggles, FlowLayout wraps.
        p_host = QWidget()
        p_flow = FlowLayout(p_host, spacing=6)
        p_flow.setContentsMargins(0, 0, 0, 0)
        for option in event_classification.PARTICIPANT_OPTIONS:
            chip = pill_toggle(tr(option))
            self._participant_chips[option] = chip
            p_flow.addWidget(chip)
        v.addWidget(_field("Participants", p_host))

        v.addStretch()
        scroll.setWidget(inner)
        return scroll

    def _build_footer(self) -> QWidget:
        host = QWidget()
        h = QHBoxLayout(host)
        h.setContentsMargins(22, 14, 22, 14)
        h.setSpacing(10)
        # Optional secondary action on the left — the locate/relink
        # entry per charter §7. Only shown when a callback was wired
        # (existing-event path, not the create flow).
        if self._on_locate_originals is not None:
            locate = ghost_button(tr("Locate originals…"))
            locate.setToolTip(tr(
                "Check whether this event's originals are reachable, "
                "and re-point Mira if you've moved them."
            ))
            locate.clicked.connect(self._on_locate_originals_clicked)
            h.addWidget(locate)
        h.addStretch()
        cancel = ghost_button(tr("Cancel"))
        cancel.clicked.connect(self.reject)
        h.addWidget(cancel)
        self._save_btn = primary_button(tr("Save event"))
        self._save_btn.clicked.connect(self._on_save)
        h.addWidget(self._save_btn)
        return host

    def _on_locate_originals_clicked(self) -> None:
        """Run the wired-in locate flow without closing this dialog.
        The user usually wants to stay in the header editor (perhaps
        they're about to rename + relink in one sitting), so we
        intentionally don't close on the secondary action."""
        if self._on_locate_originals is not None:
            self._on_locate_originals()

    @staticmethod
    def _make_single_select(
        options: tuple,
        labels: dict,
        descriptions: dict,
        *,
        tooltip: str,
    ) -> QComboBox:
        combo = QComboBox()
        combo.setObjectName("DesignSelect")
        combo.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        combo.setToolTip(tooltip)
        combo.addItem(tr("— select —"), "")
        for option in options:
            label = labels.get(option, option)
            combo.addItem(tr(label), option)
            descr = descriptions.get(option, "")
            if descr:
                combo.setItemData(
                    combo.count() - 1, tr(descr), Qt.ItemDataRole.ToolTipRole
                )
        return combo

    # ── Subtype refill on type change ─────────────────────────────────

    def _update_subtype_combo(self) -> None:
        prev = (
            self._subtype_combo.currentText()
            if self._subtype_combo.count() else ""
        )
        self._subtype_combo.blockSignals(True)
        self._subtype_combo.clear()
        self._subtype_combo.addItem(tr("— select or type —"), "")
        current_type = (
            self._type_combo.currentData()
            or event_classification.EVENT_TYPE_TRIP
        )
        for subtype in event_classification.subtype_presets_for(current_type):
            self._subtype_combo.addItem(tr(subtype), subtype)
        if prev and prev != tr("— select or type —"):
            self._subtype_combo.setCurrentText(prev)
        self._subtype_combo.blockSignals(False)

    def _on_type_changed(self, _index: int) -> None:
        self._update_subtype_combo()
        self._refresh_save_enabled()

    def _on_duration_unit_changed(self, _index: int) -> None:
        unit = self._duration_unit_combo.currentData() or ""
        if not unit:
            self._duration_value_spin.setValue(0)
            return
        if self._duration_value_spin.value() == 0:
            self._duration_value_spin.setValue(1)

    def _on_creative_chip(self, option: str) -> None:
        """spec/64 §3.4: None ⇔ subjects mutual exclusion."""
        none_chip = self._creative_chips.get(
            event_classification.CREATIVE_FOCUS_NONE
        )
        if option == event_classification.CREATIVE_FOCUS_NONE:
            if none_chip is not None and none_chip.isChecked():
                for opt, chip in self._creative_chips.items():
                    if opt != event_classification.CREATIVE_FOCUS_NONE:
                        chip.setChecked(False)
        else:
            chip = self._creative_chips.get(option)
            if chip is not None and chip.isChecked():
                if none_chip is not None:
                    none_chip.setChecked(False)

    # ── Save gating ───────────────────────────────────────────────────

    def _refresh_save_enabled(self) -> None:
        # BUGS.md B-012 (Nelson 2026-06-17) — From/To fields removed; the
        # save gate is Name + Type + Subtype. Dates derive from trip_days
        # in the gateway helper (``recompute_event_date_range``).
        name_ok = bool(self._name_edit.text().strip())
        type_ok = bool(self._type_combo.currentData())
        subtype_text = (self._subtype_combo.currentText() or "").strip()
        subtype_ok = (
            bool(subtype_text) and subtype_text != tr("— select or type —")
        )
        self._save_btn.setEnabled(name_ok and type_ok and subtype_ok)
        # spec/76 §B.1 — overrides the validity-gated enable above when
        # the library is read-only.
        from mira.ui.read_only import disable_if_read_only
        disable_if_read_only(self._save_btn)

    # ── Existing-info pre-population ─────────────────────────────────

    def _apply_existing(self, info: dict) -> None:
        if info.get("name"):
            self._name_edit.setText(info["name"])
        if info.get("event_type"):
            idx = self._type_combo.findData(info["event_type"])
            if idx >= 0:
                self._type_combo.setCurrentIndex(idx)
        if info.get("event_subtype"):
            self._subtype_combo.setCurrentText(info["event_subtype"])
        if info.get("description"):
            self._desc_edit.setPlainText(info["description"])
        # BUGS.md B-012 — start_date / end_date removed from the dialog
        # (derived from trip_days by the gateway helper). If
        # ``existing_info`` carries them, they're ignored here.
        unit = info.get("duration_unit")
        if unit:
            idx = self._duration_unit_combo.findData(unit)
            if idx >= 0:
                self._duration_unit_combo.setCurrentIndex(idx)
        if info.get("duration_value"):
            self._duration_value_spin.setValue(int(info["duration_value"]))
        context = info.get("context")
        if context:
            idx = self._context_combo.findData(context)
            if idx >= 0:
                self._context_combo.setCurrentIndex(idx)
        experience = info.get("experience_type")
        if experience:
            idx = self._experience_combo.findData(experience)
            if idx >= 0:
                self._experience_combo.setCurrentIndex(idx)
        for option, chip in self._creative_chips.items():
            chip.blockSignals(True)
            chip.setChecked(option in (info.get("creative_focus") or []))
            chip.blockSignals(False)
        for option, chip in self._participant_chips.items():
            chip.setChecked(option in (info.get("participants") or []))

    # ── Output ────────────────────────────────────────────────────────

    def header_info(self) -> dict:
        """Same dict shape as the legacy EventHeaderDialog.header_info()."""
        subtype_text = (self._subtype_combo.currentText() or "").strip()
        if subtype_text == tr("— select or type —"):
            subtype_text = ""
        duration_unit = self._duration_unit_combo.currentData() or ""
        duration_value = (
            self._duration_value_spin.value() if duration_unit else 0
        )
        selected_focus = [
            opt for opt, chip in self._creative_chips.items()
            if chip.isChecked()
        ]
        selected_participants = [
            opt for opt, chip in self._participant_chips.items()
            if chip.isChecked()
        ]
        # BUGS.md B-012 — start_date / end_date no longer come from this
        # dialog. They are derived from trip_days in the gateway by
        # ``recompute_event_date_range`` after every trip_day batch
        # (creation, Collect, plan editor, ingest). The output keys are
        # kept (``None``) so existing callers that read them keep
        # working without a code sweep.
        return {
            "name": self._name_edit.text().strip(),
            "event_type": (
                self._type_combo.currentData()
                or event_classification.EVENT_TYPE_TRIP
            ),
            "event_subtype": subtype_text,
            "description": self._desc_edit.toPlainText().strip(),
            "start_date": None,
            "end_date": None,
            "duration_value": duration_value or None,
            "duration_unit": duration_unit or None,
            "context": self._context_combo.currentData() or None,
            "experience_type": self._experience_combo.currentData() or None,
            "creative_focus": selected_focus,
            "participants": selected_participants,
        }

    def _on_save(self) -> None:
        self._was_applied = True
        self.accept()

    def was_applied(self) -> bool:
        return self._was_applied
