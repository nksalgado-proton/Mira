"""spec/159 §4.5 — Exported Collection filter, the Mira-style bar.

Supersedes the toolbar :class:`FilterPopupButton` (Nelson 2026-06-30
eyeball pivot: a popup with checkmark-style items reads as non-
standard; the Mira way is named group boxes + proper dropdowns).

The :class:`FilterBar` widget:

  * Outer ``#ProcessGroupBox`` titled "Filters" — same QSS role the
    Edit phase's adjustment groups use, so the visual language is
    consistent.
  * Four inner group boxes, one per knob:
      - Min stars   — QComboBox  (Any / ★1+ / ★2+ / ★3+ / ★4+ / ★5)
      - Colour      — :class:`ColorLabelMultiRow` (5 swatches, multi-
                       select; clicked swatch gets a white halo + ✓)
      - Flag        — QComboBox  (Any / Flagged / Unflagged)
      - Deletion    — QComboBox  (Any / Show only / Hide)
  * A right-aligned "Showing N of M" indicator + a "Clear" ghost
    button.

Reusable contract: takes / emits a :class:`LineageFilter` (the
predicate :mod:`mira.ui.exported.filter_popup` already defines). The
host pushes ``set_filter`` on first show + after a "Clear filter"
elsewhere; the bar fires :data:`filter_changed` after every user
edit. ``setRenderedCount(n, total)`` repaints the indicator without
touching the predicate.

Suitable for re-use on a future Dynamic Collection editor + the new-
Cut compose surface (Nelson 2026-06-30 — "make it reusable").
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mira.ui.design import ghost_button
from mira.ui.exported.filter_popup import LineageFilter
from mira.ui.exported.rating_widgets import ColorLabelMultiRow
from mira.ui.i18n import tr


def _group(title: str) -> tuple[QFrame, QVBoxLayout]:
    """Mira's standard inner group box — same shape the Edit phase's
    adjustment surface uses (``#ProcessGroupBox`` + ``#ProcessGroupTitle``)
    so the visual language stays consistent across surfaces."""
    box = QFrame()
    box.setObjectName("ProcessGroupBox")
    col = QVBoxLayout(box)
    col.setContentsMargins(10, 4, 10, 6)
    col.setSpacing(4)
    lbl = QLabel(title)
    lbl.setObjectName("ProcessGroupTitle")
    col.addWidget(lbl)
    return box, col


class FilterBar(QWidget):
    """spec/159 §4.5 (redesigned) — the Exported Collection filter
    bar. Hosts the four knobs + a Showing-N-of-M indicator + a Clear
    button."""

    #: Fresh :class:`LineageFilter` snapshot — emitted after each user
    #: edit. Programmatic :meth:`set_filter` calls suppress the emit.
    filter_changed = pyqtSignal(LineageFilter)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("FilterBar")
        self._filter = LineageFilter()
        self._suppress_emit = False
        self._build_ui()
        self._sync_controls()

    # ── UI ──────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer_v = QVBoxLayout(self)
        # 12 px left/right matches the grid's flow margin + the
        # surface's title/chrome rows (Nelson 2026-06-30 — "use the
        # position of the grid tiles as a reference"). The small
        # vertical breathing room keeps the bar from kissing the
        # title row above and the section header below.
        outer_v.setContentsMargins(12, 4, 12, 4)
        outer_v.setSpacing(0)

        # Outer "Filters" group box — same #ProcessGroupBox role.
        outer_box, outer_col = _group(tr("Filters"))
        outer_col.setContentsMargins(10, 4, 10, 8)
        row = QHBoxLayout()
        row.setSpacing(8)
        outer_col.addLayout(row)
        outer_v.addWidget(outer_box)

        # — Min stars dropdown ——————————————————————————————
        star_box, star_col = _group(tr("Min stars"))
        self._star_combo = QComboBox()
        self._star_combo.setObjectName("ProcessStyleCombo")
        self._star_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._star_combo.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        for label, value in (
            (tr("Any"), None),
            ("★ 1+", 1), ("★ 2+", 2), ("★ 3+", 3),
            ("★ 4+", 4), ("★ 5", 5),
        ):
            self._star_combo.addItem(label, value)
        self._star_combo.activated.connect(
            lambda _i: self._on_stars_picked(
                self._star_combo.currentData()))
        star_col.addWidget(self._star_combo)
        row.addWidget(star_box)

        # — Colour label swatches ——————————————————————————————
        colour_box, colour_col = _group(tr("Colour label"))
        self._colour_row = ColorLabelMultiRow()
        self._colour_row.value_changed.connect(self._on_colours_changed)
        colour_col.addWidget(self._colour_row)
        row.addWidget(colour_box)

        # — Flag dropdown ——————————————————————————————
        flag_box, flag_col = _group(tr("Flag"))
        self._flag_combo = QComboBox()
        self._flag_combo.setObjectName("ProcessStyleCombo")
        self._flag_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._flag_combo.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        for label, value in (
            (tr("Any"), "any"),
            (tr("⚑ Flagged"), "yes"),
            (tr("Unflagged"), "no"),
        ):
            self._flag_combo.addItem(label, value)
        self._flag_combo.activated.connect(
            lambda _i: self._on_flag_picked(
                self._flag_combo.currentData()))
        flag_col.addWidget(self._flag_combo)
        row.addWidget(flag_box)

        # — Marked for deletion dropdown ——————————————————————
        del_box, del_col = _group(tr("Marked for deletion"))
        self._del_combo = QComboBox()
        self._del_combo.setObjectName("ProcessStyleCombo")
        self._del_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._del_combo.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        for label, value in (
            (tr("Any"), "any"),
            (tr("⌫ Show only marked"), "only"),
            (tr("Hide marked"), "hide"),
        ):
            self._del_combo.addItem(label, value)
        self._del_combo.activated.connect(
            lambda _i: self._on_delete_picked(
                self._del_combo.currentData()))
        del_col.addWidget(self._del_combo)
        row.addWidget(del_box)

        row.addStretch(1)

        # — Showing N of M indicator + Clear button ————————————
        indicator_col = QVBoxLayout()
        indicator_col.setSpacing(2)
        indicator_col.addStretch(1)
        self._count_lbl = QLabel("")
        self._count_lbl.setObjectName("Sub")
        self._count_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._count_lbl.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        indicator_col.addWidget(self._count_lbl)
        self._clear_btn = ghost_button(tr("Clear filter"))
        self._clear_btn.clicked.connect(self.reset)
        indicator_col.addWidget(self._clear_btn)
        indicator_col.addStretch(1)
        row.addLayout(indicator_col)

    # ── public API ──────────────────────────────────────────────────

    def filter(self) -> LineageFilter:
        return self._filter

    def set_filter(self, value: LineageFilter) -> None:
        """Programmatic write — re-syncs every control. Does NOT
        emit :data:`filter_changed`."""
        self._suppress_emit = True
        try:
            self._filter = LineageFilter(
                min_stars=value.min_stars,
                colour_labels=set(value.colour_labels),
                flag=value.flag,
                to_delete=value.to_delete,
            )
            self._sync_controls()
        finally:
            self._suppress_emit = False

    def reset(self) -> None:
        """Reset every knob to default + emit one change."""
        self._filter = LineageFilter()
        self._sync_controls()
        if not self._suppress_emit:
            self.filter_changed.emit(self._filter)

    def setRenderedCount(  # noqa: N802 — Qt-like
        self, shown: int, total: int,
    ) -> None:
        """Repaint the right-aligned indicator. Hosts call this on
        every grid rebuild so the user sees how many cells the filter
        is hiding."""
        if total <= 0:
            self._count_lbl.setText(tr("No exported files"))
            return
        if shown == total:
            self._count_lbl.setText(
                tr("Showing {n} exported file(s)").replace(
                    "{n}", str(total)))
        else:
            self._count_lbl.setText(
                tr("Showing {s} of {t}").replace(
                    "{s}", str(shown)).replace("{t}", str(total)))

    # ── user-input handlers ────────────────────────────────────────

    def _on_stars_picked(self, value) -> None:
        self._filter.min_stars = value
        self._after_change()

    def _on_colours_changed(self, value) -> None:
        # ``value`` arrives as a set from ColorLabelMultiRow.
        self._filter.colour_labels = set(value or ())
        self._after_change()

    def _on_flag_picked(self, value: str) -> None:
        if value not in ("any", "yes", "no"):
            value = "any"
        self._filter.flag = value
        self._after_change()

    def _on_delete_picked(self, value: str) -> None:
        if value not in ("any", "only", "hide"):
            value = "any"
        self._filter.to_delete = value
        self._after_change()

    # ── helpers ─────────────────────────────────────────────────────

    def _after_change(self) -> None:
        if self._suppress_emit:
            return
        self.filter_changed.emit(self._filter)

    def _sync_controls(self) -> None:
        """Re-tick / re-select every control to match
        ``self._filter``. Uses :meth:`QComboBox.blockSignals` so the
        re-sync doesn't re-enter :meth:`_after_change`."""
        # Stars combo.
        idx = max(0, self._star_combo.findData(self._filter.min_stars))
        self._star_combo.blockSignals(True)
        try:
            self._star_combo.setCurrentIndex(idx)
        finally:
            self._star_combo.blockSignals(False)
        # Colour swatches.
        self._colour_row.setValue(self._filter.colour_labels)
        # Flag combo.
        idx = max(0, self._flag_combo.findData(self._filter.flag))
        self._flag_combo.blockSignals(True)
        try:
            self._flag_combo.setCurrentIndex(idx)
        finally:
            self._flag_combo.blockSignals(False)
        # Deletion combo.
        idx = max(0, self._del_combo.findData(self._filter.to_delete))
        self._del_combo.blockSignals(True)
        try:
            self._del_combo.setCurrentIndex(idx)
        finally:
            self._del_combo.blockSignals(False)


__all__ = ["FilterBar"]
