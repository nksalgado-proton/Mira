"""``CountryPicker`` — multi-select country chooser with type-ahead search.

Used by :class:`~mira.ui.base.classification_panel.ClassificationPanel`
for the ``countries`` extras key on Trip events. Storage is ISO 3166-1
alpha-2 codes (matches the schema's ``country_code`` convention); display
shows the country name with the code in parens.

Data source: ``assets/countries.json`` — generated from the ``babel``
package's CLDR territory data at build time and committed to the repo
(see the build step that produced the file). Build-time generation keeps
the package offline-first while still tracking the canonical ISO list.

UI shape:
* A row of chips above the picker — one per selected country, click any
  chip's ✕ to remove.
* An editable :class:`QComboBox` populated with every country; a
  :class:`QCompleter` filters as the user types (case-insensitive,
  substring match). Pressing Enter or picking from the popup adds the
  country as a chip.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QComboBox,
    QCompleter,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mira.ui.base.flow_layout import FlowLayout
from mira.ui.i18n import tr

log = logging.getLogger(__name__)


def _countries_json_path() -> Path:
    """Locate ``assets/countries.json`` (project root) from this package's depth."""
    return Path(__file__).resolve().parents[3] / "assets" / "countries.json"


@lru_cache(maxsize=1)
def load_countries() -> Tuple[Tuple[str, str], ...]:
    """All known ISO 3166-1 alpha-2 entries as ``((code, name), …)``, sorted
    by display name. Read once + cached for the life of the process."""
    path = _countries_json_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.exception("CountryPicker: could not load %s", path)
        return ()
    out: List[Tuple[str, str]] = []
    for entry in data:
        code = (entry.get("code") or "").upper()
        name = entry.get("name") or ""
        if len(code) == 2 and name:
            out.append((code, name))
    out.sort(key=lambda t: t[1].lower())
    return tuple(out)


@lru_cache(maxsize=1)
def _code_to_name() -> Dict[str, str]:
    return {code: name for code, name in load_countries()}


def make_single_country_combo(initial_code: Optional[str] = None) -> QComboBox:
    """A single-select QComboBox listing every ISO 3166-1 country (Nelson
    2026-06-06 — shared helper so PlanEditorDialog + PreingestPlanConfirmDialog
    render the country picker identically). Editable + completer-backed for
    type-to-search. ``userData`` on each entry is the alpha-2 code; an
    initial blank entry lets the user clear the field. Pre-selects
    ``initial_code`` when provided (case-insensitive)."""
    from PyQt6.QtWidgets import QComboBox  # local — keep module-level imports stable
    combo = QComboBox()
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    combo.addItem("", "")  # blank entry
    countries = load_countries()
    for cc, name in countries:
        combo.addItem(f"{name} ({cc})", cc)
    if countries:
        completer = QCompleter(
            [f"{name} ({cc})" for cc, name in countries], combo,
        )
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        combo.setCompleter(completer)
    if initial_code:
        idx = combo.findData(initial_code.upper())
        if idx >= 0:
            combo.setCurrentIndex(idx)
    return combo


def country_code_from_combo(combo) -> Optional[str]:
    """Read an alpha-2 code from a combo built by
    :func:`make_single_country_combo`. Falls back to a case-insensitive
    name match so typed entries round-trip even without picking from the
    dropdown. Returns ``None`` for blank / unknown text."""
    data = combo.currentData()
    if data:
        return str(data).upper()
    text = (combo.currentText() or "").strip()
    if not text:
        return None
    norm = text.lower()
    for cc, name in load_countries():
        if name.lower() == norm or cc.lower() == norm:
            return cc.upper()
    return None


def display_label_for_code(code: str) -> str:
    """``"Brazil (BR)"`` for a known code; the raw code (uppercased) when
    unknown so a stale value still renders something."""
    code = (code or "").upper()
    name = _code_to_name().get(code)
    return f"{name} ({code})" if name else code


class CountryPicker(QWidget):
    """Multi-select country chooser. Type to search, click a result to add."""

    values_changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._selected: List[str] = []
        self._build_ui()

    # ── Build ───────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        # Chips row (selected countries). FlowLayout wraps so a many-country
        # trip doesn't blow the form's width.
        self._chip_host = QWidget()
        self._chip_layout = FlowLayout(self._chip_host, spacing=4)
        self._chip_layout.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._chip_host)

        # Picker row (the search combo).
        picker_row = QHBoxLayout()
        picker_row.setContentsMargins(0, 0, 0, 0)
        picker_row.setSpacing(6)
        self._combo = QComboBox()
        self._combo.setEditable(True)
        self._combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        countries = load_countries()
        if not countries:
            # Fall back to a notice + leave the combo empty so the user knows
            # something's off; the panel above still saves whatever's typed.
            self._combo.setEnabled(False)
            self._combo.lineEdit().setPlaceholderText(tr(
                "Country data unavailable — install babel + rebuild assets."))
        else:
            for code, name in countries:
                # Item text = "Brazil (BR)"; userData = "BR" so we don't have
                # to re-parse the display text when the user picks an entry.
                self._combo.addItem(f"{name} ({code})", code)
            self._combo.setCurrentIndex(-1)
            self._combo.lineEdit().setPlaceholderText(tr(
                "Type to search countries — Enter to add"))
            # QCompleter wraps the combo's items for substring-anywhere
            # filtering (case-insensitive). Pre-built from the combo's
            # contents so we don't double-bind a list.
            completer = QCompleter(
                [f"{name} ({code})" for code, name in countries], self,
            )
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            self._combo.setCompleter(completer)
        # `activated(int)` fires only on user interaction (not on
        # programmatic setCurrentIndex), which is what we want — picking
        # from the popup or pressing Enter on a completer match.
        self._combo.activated.connect(self._on_combo_activated)
        picker_row.addWidget(self._combo, stretch=1)
        outer.addLayout(picker_row)

    # ── Public API ──────────────────────────────────────────────────────────

    def set_codes(self, codes: List[str]) -> None:
        """Reset the selection to ``codes`` (ISO 3166-1 alpha-2). Unknown
        codes are preserved verbatim so a stale value doesn't silently
        disappear — the chip just renders as the raw code."""
        seen = set()
        self._selected = []
        for raw in (codes or []):
            code = (raw or "").upper()
            if code and code not in seen:
                seen.add(code)
                self._selected.append(code)
        self._refresh_chips()

    def codes(self) -> List[str]:
        """The current selection, ordered as the user added them."""
        return list(self._selected)

    # ── Internals ───────────────────────────────────────────────────────────

    def _on_combo_activated(self, index: int) -> None:
        if index < 0:
            return
        code = self._combo.itemData(index)
        if not code or code in self._selected:
            self._reset_combo()
            return
        self._selected.append(code)
        self._refresh_chips()
        self._reset_combo()
        self.values_changed.emit()

    def _reset_combo(self) -> None:
        self._combo.setCurrentIndex(-1)
        self._combo.lineEdit().clear()

    def _refresh_chips(self) -> None:
        while self._chip_layout.count() > 0:
            item = self._chip_layout.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.deleteLater()
        for code in self._selected:
            self._chip_layout.addWidget(self._make_chip(code))

    def _make_chip(self, code: str) -> QWidget:
        """One selected-country chip: "Brazil (BR)  ✕" — clicking removes."""
        chip = QPushButton(f"{display_label_for_code(code)}   ✕")
        chip.setObjectName("CountryPickerChip")
        chip.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        chip.setFlat(True)
        chip.setToolTip(tr("Remove this country"))
        chip.clicked.connect(lambda _checked=False, c=code: self._remove(c))
        return chip

    def _remove(self, code: str) -> None:
        if code in self._selected:
            self._selected.remove(code)
            self._refresh_chips()
            self.values_changed.emit()
