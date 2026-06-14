"""``ClassificationPanel`` — reusable Type / Subtype / Description / Tags /
per-type extras editor (spec/44).

Used by every surface that creates or edits an event's classification:

* :class:`mira.ui.pages.new_event_page.NewEventPage` — Slice B.
* :class:`mira.ui.pages.preingest_dialog.PreingestDialog` — Slice C
  (the create-from-photos day-list header).
* The Edit-info dialog opened from EventPlanPage — Slice D.

The panel is a passive QWidget: it doesn't know the gateway, doesn't load or
save anything on its own. Callers populate it via :meth:`set_values` and
read it via :meth:`values` — the dialog/page glue then drives
``Gateway.set_classification(...)``.

Hoisted into ``ui.base`` from day one (review finding #7 — verbatim widget
copies were a recent smell; this widget is reuse-ready so the three surfaces
above all share one form layout + one vocabulary mapping).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from mira import event_classification
from mira.ui.base.country_picker import CountryPicker
from mira.ui.base.flow_layout import FlowLayout
from mira.ui.i18n import tr

log = logging.getLogger(__name__)


# Soft cap on description length — matches EventCard's tooltip truncation
# point, so what the user types is what they see on the card.
DESCRIPTION_MAX = 280

# Keys whose value is a list of strings (rendered as a comma-separated text
# input; the read pipeline splits on commas and strips empties).
_LIST_VALUED_EXTRAS = frozenset({"countries", "people"})


@dataclass(frozen=True)
class ClassificationValues:
    """Snapshot of the panel's current state.

    ``extras`` carries only the classification-namespace keys (people,
    countries, target_subject, …). IPTC location keys live in the same
    ``event.extras_json`` blob but are owned by a different editor —
    :meth:`Gateway.set_classification` shallow-merges so the two namespaces
    don't collide.
    """
    event_type: str
    event_subtype: Optional[str]
    description: str
    tags: List[str] = field(default_factory=list)
    extras: Dict[str, Any] = field(default_factory=dict)


def _parse_list_field(text: str) -> List[str]:
    return [t.strip() for t in (text or "").split(",") if t.strip()]


def _format_list_field(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if value is None:
        return ""
    return str(value)


def _humanise_key(key: str) -> str:
    return key.replace("_", " ").capitalize()


class ClassificationPanel(QWidget):
    """Self-contained editor: Type radios + Subtype combo + Description +
    Tags + per-type extras rows."""

    values_changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._type_buttons: Dict[str, QRadioButton] = {}
        self._extras_widgets: Dict[str, QLineEdit] = {}
        self._signals_blocked = False
        self._build_ui()
        # Initialise to defaults so callers can read values() right away.
        self.set_values()

    # ── Build ───────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        self._form = QFormLayout()
        self._form.setSpacing(8)
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Type — one radio button per enum value (closed set).
        self._type_group = QButtonGroup(self)
        type_row = QHBoxLayout()
        type_row.setSpacing(8)
        for et in event_classification.ALL_EVENT_TYPES:
            rb = QRadioButton(tr(event_classification.display_label_for_type(et)))
            rb.setProperty("event_type", et)
            self._type_group.addButton(rb)
            self._type_buttons[et] = rb
            type_row.addWidget(rb)
        type_row.addStretch(1)
        # Qt's buttonClicked carries the QAbstractButton; we ignore it and
        # read the current type via _current_type().
        self._type_group.buttonClicked.connect(lambda _b: self._on_type_changed())
        type_wrap = QWidget()
        type_wrap.setLayout(type_row)
        self._form.addRow(tr("Type") + ":", type_wrap)

        # Subtype — editable combo (presets + arbitrary user-typed values).
        self._subtype = QComboBox()
        self._subtype.setEditable(True)
        self._subtype.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._subtype.lineEdit().setPlaceholderText(tr(
            "Pick a preset or type a custom subtype"))
        self._subtype.lineEdit().textChanged.connect(lambda _t: self._emit_changed())
        self._form.addRow(tr("Subtype") + ":", self._subtype)

        # Description — short paragraph; placeholder names the surface.
        self._description = QPlainTextEdit()
        self._description.setPlaceholderText(tr(
            "Short paragraph shown on the dashboard card tooltip"))
        self._description.setMaximumHeight(80)
        self._description.textChanged.connect(self._emit_changed)
        self._form.addRow(tr("Description") + ":", self._description)

        # Tags — single text line, comma-separated. Lowercase convention noted
        # in placeholder; not enforced (the panel respects what the user types).
        self._tags = QLineEdit()
        self._tags.setPlaceholderText(tr(
            "Comma-separated, lowercase: wildlife, candid, blue-hour"))
        self._tags.textChanged.connect(lambda _t: self._emit_changed())
        self._form.addRow(tr("Tags") + ":", self._tags)

        # Tag-suggestion row (Nelson 2026-06-06): users were lost on what to
        # put in Tags. Show a helper line + a row of clickable example chips
        # so the *why* is visible. Click any chip to append it; the chips are
        # examples, not a constraint — typing your own still works.
        tag_help = QLabel(tr(
            "Tags help you find photos later by mood, theme, or subject. "
            "Click an example to add it, or type your own."
        ))
        tag_help.setObjectName("PageHint")
        tag_help.setWordWrap(True)
        self._form.addRow("", tag_help)

        self._tag_suggestions_host = QWidget()
        self._tag_suggestions_layout = FlowLayout(
            self._tag_suggestions_host, spacing=4,
        )
        self._tag_suggestions_layout.setContentsMargins(0, 0, 0, 0)
        self._form.addRow("", self._tag_suggestions_host)

        outer.addLayout(self._form)

        # Per-type extras section — rebuilds on type change.
        self._extras_label = QLabel(tr("More details"))
        self._extras_label.setObjectName("PageHint")
        outer.addWidget(self._extras_label)
        self._extras_form = QFormLayout()
        self._extras_form.setSpacing(8)
        self._extras_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        outer.addLayout(self._extras_form)

    # ── Public API ──────────────────────────────────────────────────────────

    def set_values(
        self,
        *,
        event_type: str = event_classification.EVENT_TYPE_UNCLASSIFIED,
        event_subtype: Optional[str] = None,
        description: str = "",
        tags: Optional[List[str]] = None,
        extras: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Reset the panel to these values. No ``values_changed`` signals fire
        during the repopulate — callers don't want a load-cycle to look like a
        user edit."""
        self._signals_blocked = True
        try:
            event_type = event_classification.normalize_type(event_type)
            for et, rb in self._type_buttons.items():
                rb.setChecked(et == event_type)
            self._populate_subtype_combo(event_type)
            self._subtype.setCurrentText(event_subtype or "")
            self._description.setPlainText(description or "")
            self._tags.setText(", ".join(tags or []))
            self._rebuild_tag_suggestions(event_type)
            self._rebuild_extras(event_type, extras or {})
        finally:
            self._signals_blocked = False

    def values(self) -> ClassificationValues:
        """Snapshot the panel's current state. Description is truncated to
        :data:`DESCRIPTION_MAX` so callers don't need to defend separately."""
        et = self._current_type()
        subtype_text = self._subtype.currentText().strip()
        subtype = subtype_text or None
        description = self._description.toPlainText().strip()
        if len(description) > DESCRIPTION_MAX:
            description = description[:DESCRIPTION_MAX].rstrip()
        tags = _parse_list_field(self._tags.text())
        extras = self._read_extras(et)
        return ClassificationValues(
            event_type=et,
            event_subtype=subtype,
            description=description,
            tags=tags,
            extras=extras,
        )

    # ── Internals ───────────────────────────────────────────────────────────

    def _current_type(self) -> str:
        for et, rb in self._type_buttons.items():
            if rb.isChecked():
                return et
        return event_classification.EVENT_TYPE_UNCLASSIFIED

    def _on_type_changed(self) -> None:
        if self._signals_blocked:
            return
        et = self._current_type()
        # Re-seed subtype presets for the new type, preserving any user-typed
        # value so the user doesn't lose a half-typed custom subtype just
        # because they switched a radio.
        previous_subtype = self._subtype.currentText()
        carried_extras = self._read_extras_raw()
        self._signals_blocked = True
        try:
            self._populate_subtype_combo(et)
            self._subtype.setCurrentText(previous_subtype)
            self._rebuild_tag_suggestions(et)
            self._rebuild_extras(et, carried_extras)
        finally:
            self._signals_blocked = False
        self._emit_changed()

    def _rebuild_tag_suggestions(self, event_type: str) -> None:
        """Recreate the row of clickable tag-suggestion chips for ``event_type``."""
        while self._tag_suggestions_layout.count() > 0:
            item = self._tag_suggestions_layout.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.deleteLater()
        for tag in event_classification.tag_suggestions_for(event_type):
            chip = QPushButton(f"+ {tag}")
            chip.setObjectName("ClassificationTagSuggestion")
            chip.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            chip.setFlat(True)
            chip.setToolTip(tr("Add this tag"))
            chip.clicked.connect(
                lambda _checked=False, t=tag: self._append_tag(t),
            )
            self._tag_suggestions_layout.addWidget(chip)

    def _append_tag(self, tag: str) -> None:
        """Append ``tag`` to the tags input, skipping duplicates so a user
        clicking the same chip twice doesn't end up with two copies."""
        current = _parse_list_field(self._tags.text())
        if tag in current:
            return
        current.append(tag)
        self._tags.setText(", ".join(current))

    def _populate_subtype_combo(self, event_type: str) -> None:
        self._subtype.clear()
        presets = event_classification.subtype_presets_for(event_type)
        if presets:
            self._subtype.addItem("")  # leading blank = "no subtype"
            for p in presets:
                self._subtype.addItem(p)

    def _rebuild_extras(
        self, event_type: str, current_values: Dict[str, Any],
    ) -> None:
        # Clear existing rows
        while self._extras_form.rowCount() > 0:
            self._extras_form.removeRow(0)
        self._extras_widgets.clear()
        keys = event_classification.extras_keys_for(event_type)
        self._extras_label.setVisible(bool(keys))
        for key in keys:
            label = QLabel(tr(_humanise_key(key)) + ":")
            label.setObjectName("ClassificationExtrasLabel")
            widget = self._build_extras_widget(key, current_values.get(key))
            self._extras_widgets[key] = widget
            self._extras_form.addRow(label, widget)

    def _build_extras_widget(self, key: str, value: Any) -> QWidget:
        """One per-key dispatch — the place to swap a plain QLineEdit for a
        richer widget when the schema warrants. ``countries`` uses the
        searchable :class:`CountryPicker`; other list-valued keys stay as
        comma-separated free text (e.g. ``people`` — names aren't a closed list)."""
        if key == "countries":
            picker = CountryPicker()
            picker.set_codes(value if isinstance(value, list) else [])
            picker.values_changed.connect(self._emit_changed)
            return picker
        edit = QLineEdit()
        if key in _LIST_VALUED_EXTRAS:
            edit.setPlaceholderText(tr("Comma-separated"))
            edit.setText(_format_list_field(value))
        else:
            edit.setText(str(value or ""))
        edit.textChanged.connect(lambda _t: self._emit_changed())
        return edit

    def _read_extras_raw(self) -> Dict[str, Any]:
        """All non-blank extras currently displayed, regardless of type."""
        out: Dict[str, Any] = {}
        for key, w in self._extras_widgets.items():
            if isinstance(w, CountryPicker):
                codes = w.codes()
                if codes:
                    out[key] = codes
                continue
            if isinstance(w, QLineEdit):
                text = w.text().strip()
                if not text:
                    continue
                if key in _LIST_VALUED_EXTRAS:
                    out[key] = _parse_list_field(text)
                else:
                    out[key] = text
        return out

    def _read_extras(self, event_type: str) -> Dict[str, Any]:
        # Drop extras that aren't valid for the current type — useful when a
        # caller polls values() right after a type flip while a stale field
        # still sits in self._extras_widgets between rebuilds.
        keys_for_type = set(event_classification.extras_keys_for(event_type))
        return {
            k: v for k, v in self._read_extras_raw().items() if k in keys_for_type
        }

    def _emit_changed(self) -> None:
        if not self._signals_blocked:
            self.values_changed.emit()
