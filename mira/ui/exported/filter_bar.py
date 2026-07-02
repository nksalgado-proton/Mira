"""spec/159 §4.5 — Exported Collection filter, the Mira-style bar.

Supersedes the toolbar :class:`FilterPopupButton` (Nelson 2026-06-30
eyeball pivot: a popup with checkmark-style items reads as non-
standard; the Mira way is named group boxes + proper dropdowns).

The :class:`FilterBar` widget:

  * Outer ``#ProcessGroupBox`` titled "Filters" — same QSS role the
    Edit phase's adjustment groups use, so the visual language is
    consistent.
  * Inner group boxes, one per knob:
      - Min stars   — :class:`StarRow` — click Nth = ≥N stars, click
                       already-Nth = clear (Any). Same LRC convention
                       :class:`StarRow` uses as a rating input.
      - Colour      — :class:`ColorLabelMultiRow` (5 swatches, multi-
                       select; clicked swatch gets a white halo + ✓)
      - Flag        — :class:`FlagToggle` — off = Any, on = only
                       flagged. The tri-state predicate still supports
                       ``flag="no"`` for programmatic callers.
      - Deletion    — QComboBox  (Any / Show only / Hide)
  * A right-aligned "Showing N of M" indicator + a "Clear" ghost
    button.

Cross-event scope (spec/162 §8 Round 3) adds four more knobs on the
same row: Cameras (multi-select combo), Lenses (multi-select combo),
Dates (from / to :class:`QDateEdit` pair), Places (deferred — a
disabled placeholder chip until Mira attributes a place per lineage
row). The scope toggle is a public property (``FilterBar.scope`` /
``setScope``) — the host writes ``scope="cross-event"`` on the
library-scope :class:`DCDetailPage` variant and on Section 1 of the
cross-event :class:`NewCutDialog`.

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

from datetime import date
from typing import Iterable, Optional

from PyQt6.QtCore import QDate, Qt, pyqtSignal
from PyQt6.QtGui import QCursor, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mira.ui.design import ghost_button
from mira.ui.exported.filter_popup import LineageFilter
from mira.ui.exported.rating_widgets import (
    ColorLabelMultiRow,
    FlagToggle,
    StarRow,
)
from mira.ui.i18n import tr

SCOPE_EVENT = "event"
SCOPE_CROSS_EVENT = "cross-event"


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


class _MultiSelectCombo(QComboBox):
    """Multi-select drop-down combo for Camera / Lens dimension knobs.

    Presents a checkable list; the display text reflects the current
    selection. Emits :attr:`value_changed` with a ``set[str]`` after
    each user tick. Suppresses signals during programmatic
    :meth:`set_values` / :meth:`set_options` so the host round-trip
    is quiet."""

    value_changed = pyqtSignal(set)

    def __init__(self, placeholder: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("ProcessStyleCombo")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._placeholder = placeholder
        self._suppress = False
        self._model = QStandardItemModel(self)
        self.setModel(self._model)
        view = QListView(self)
        view.setObjectName("MultiSelectView")
        self.setView(view)
        self._model.itemChanged.connect(self._on_item_changed)
        self._refresh_display()

    def set_options(self, options: Iterable[str]) -> None:
        """Programmatic — replace the ticked list. Preserves the
        current selection where the option survives."""
        self._suppress = True
        try:
            current = self.values()
            self._model.clear()
            for opt in options:
                it = QStandardItem(opt)
                it.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsSelectable)
                it.setData(
                    Qt.CheckState.Checked if opt in current
                    else Qt.CheckState.Unchecked,
                    Qt.ItemDataRole.CheckStateRole,
                )
                self._model.appendRow(it)
        finally:
            self._suppress = False
        self._refresh_display()

    def set_values(self, values: Iterable[str]) -> None:
        """Programmatic — tick the given options (untick everything
        else). Fires no signal."""
        wanted = set(values or ())
        self._suppress = True
        try:
            for i in range(self._model.rowCount()):
                it = self._model.item(i)
                if it is None:
                    continue
                want = it.text() in wanted
                it.setData(
                    Qt.CheckState.Checked if want else Qt.CheckState.Unchecked,
                    Qt.ItemDataRole.CheckStateRole,
                )
        finally:
            self._suppress = False
        self._refresh_display()

    def values(self) -> set[str]:
        out: set[str] = set()
        for i in range(self._model.rowCount()):
            it = self._model.item(i)
            if it is None:
                continue
            if it.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked:
                out.add(it.text())
        return out

    def _on_item_changed(self, _item) -> None:
        if self._suppress:
            return
        self._refresh_display()
        self.value_changed.emit(self.values())

    def _refresh_display(self) -> None:
        vals = self.values()
        if not vals:
            text = self._placeholder
        elif len(vals) == 1:
            text = next(iter(vals))
        else:
            text = tr("{n} selected").replace("{n}", str(len(vals)))
        self.setEditable(True)
        edit = self.lineEdit()
        if edit is not None:
            edit.setReadOnly(True)
            edit.setText(text)
            edit.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            edit.setFocusPolicy(Qt.FocusPolicy.NoFocus)


class FilterBar(QWidget):
    """spec/159 §4.5 (redesigned) — the Exported Collection filter
    bar. Hosts the four event-scope knobs + a Showing-N-of-M indicator
    + a Clear button, plus the four cross-event-scope knobs
    (spec/162 §8) that light up when the host flips ``scope`` to
    ``"cross-event"``."""

    #: Fresh :class:`LineageFilter` snapshot — emitted after each user
    #: edit. Programmatic :meth:`set_filter` calls suppress the emit.
    filter_changed = pyqtSignal(LineageFilter)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        scope: str = SCOPE_EVENT,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("FilterBar")
        self._filter = LineageFilter()
        self._suppress_emit = False
        self._scope = SCOPE_EVENT if scope != SCOPE_CROSS_EVENT else SCOPE_CROSS_EVENT
        self._build_ui()
        self._apply_scope()
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

        # — Min stars click row ——————————————————————————————
        # spec/159 (Nelson 2026-07-01) — clickable stars replace the
        # old QComboBox. Click Nth → filter to items with ≥ N stars
        # (paints 1..N filled). Click the already-filled Nth → clear
        # (Any). Same LRC convention as :class:`StarRow` uses for
        # rating input elsewhere.
        star_box, star_col = _group(tr("Min stars"))
        self._star_row = StarRow()
        self._star_row.setToolTip(tr(
            "Click a star to filter items rated that many stars or "
            "more. Click the same star again to clear."))
        self._star_row.value_changed.connect(self._on_stars_picked)
        star_col.addWidget(self._star_row)
        row.addWidget(star_box)

        # — Colour label swatches ——————————————————————————————
        colour_box, colour_col = _group(tr("Colour label"))
        self._colour_row = ColorLabelMultiRow()
        self._colour_row.value_changed.connect(self._on_colours_changed)
        colour_col.addWidget(self._colour_row)
        row.addWidget(colour_box)

        # — Flag click toggle ——————————————————————————————
        # spec/159 (Nelson 2026-07-01) — clickable flag toggle
        # replaces the old QComboBox. Off = don't filter (Any); On =
        # show only flagged items. The predicate still supports the
        # "no" (only-unflagged) case for programmatic callers, but
        # the two-state toggle intentionally exposes only Any / Yes
        # — the common triage moves.
        flag_box, flag_col = _group(tr("Flag"))
        self._flag_toggle = FlagToggle()
        self._flag_toggle.setToolTip(tr(
            "Click to filter to items with the portfolio flag raised. "
            "Click again to clear."))
        self._flag_toggle.toggled.connect(self._on_flag_toggled)
        flag_col.addWidget(self._flag_toggle)
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

        # spec/162 §8 — cross-event dimension widgets. Built here so
        # the layout stays in one place; ``_apply_scope`` hides / shows
        # them based on ``self._scope``. Options are host-provided via
        # :meth:`set_available_cameras` / :meth:`set_available_lenses`
        # (spec/162 §8 — the cross-event grid knows the actual camera /
        # lens inventory; the bar is a dumb widget).
        self._camera_box, camera_col = _group(tr("Camera"))
        self._camera_combo = _MultiSelectCombo(
            tr("Any camera"), parent=self._camera_box)
        self._camera_combo.value_changed.connect(self._on_cameras_changed)
        camera_col.addWidget(self._camera_combo)
        row.addWidget(self._camera_box)

        self._lens_box, lens_col = _group(tr("Lens"))
        self._lens_combo = _MultiSelectCombo(
            tr("Any lens"), parent=self._lens_box)
        self._lens_combo.value_changed.connect(self._on_lenses_changed)
        lens_col.addWidget(self._lens_combo)
        row.addWidget(self._lens_box)

        self._date_box, date_col = _group(tr("Dates"))
        date_pair = QHBoxLayout()
        date_pair.setSpacing(6)
        self._date_from = QDateEdit()
        self._date_from.setObjectName("ProcessDateEdit")
        self._date_from.setCalendarPopup(True)
        self._date_from.setDisplayFormat("yyyy-MM-dd")
        self._date_from.setSpecialValueText(tr("Any"))
        self._date_from.setMinimumDate(QDate(1970, 1, 1))
        self._date_from.setDate(self._date_from.minimumDate())
        self._date_from.dateChanged.connect(self._on_date_from_changed)
        self._date_to = QDateEdit()
        self._date_to.setObjectName("ProcessDateEdit")
        self._date_to.setCalendarPopup(True)
        self._date_to.setDisplayFormat("yyyy-MM-dd")
        self._date_to.setSpecialValueText(tr("Any"))
        self._date_to.setMinimumDate(QDate(1970, 1, 1))
        self._date_to.setDate(self._date_to.minimumDate())
        self._date_to.dateChanged.connect(self._on_date_to_changed)
        date_pair.addWidget(self._date_from)
        date_pair.addWidget(QLabel("–"))
        date_pair.addWidget(self._date_to)
        date_col.addLayout(date_pair)
        row.addWidget(self._date_box)

        # spec/162 §8 — Places widget is intentionally omitted until
        # Mira attaches a per-lineage place attribution (production
        # rows carry camera / lens on the source item but not a
        # place). The predicate side already accepts a ``places`` set;
        # the widget lands the day the enriched row shape gains a
        # ``place`` field. See commit note on spec/162 Round 3a.

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

    @property
    def scope(self) -> str:
        return self._scope

    def setScope(self, value: str) -> None:  # noqa: N802 — Qt-like
        """Public scope switch (spec/162 §8). ``"event"`` hides the
        four cross-event knobs; ``"cross-event"`` reveals them."""
        target = SCOPE_EVENT if value != SCOPE_CROSS_EVENT else SCOPE_CROSS_EVENT
        if target == self._scope:
            return
        self._scope = target
        self._apply_scope()

    def set_available_cameras(self, cameras: Iterable[str]) -> None:
        """Host hook — populate the Camera multi-select. Cross-event
        surfaces call this after resolving the union of camera models
        across every event's exports."""
        self._camera_combo.set_options(sorted({c for c in cameras if c}))

    def set_available_lenses(self, lenses: Iterable[str]) -> None:
        """Host hook — populate the Lens multi-select."""
        self._lens_combo.set_options(sorted({l for l in lenses if l}))

    def _apply_scope(self) -> None:
        cross = self._scope == SCOPE_CROSS_EVENT
        self._camera_box.setVisible(cross)
        self._lens_box.setVisible(cross)
        self._date_box.setVisible(cross)

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
                cameras=set(value.cameras),
                lenses=set(value.lenses),
                date_from=value.date_from,
                date_to=value.date_to,
                places=set(value.places),
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

    def _on_flag_toggled(self, on: bool) -> None:
        """Two-state UI handler. On → filter for flagged items only;
        off → clear the filter (Any). Keeps :meth:`_on_flag_picked`
        alive for callers that push the tri-state value (tests,
        programmatic set_filter with ``flag="no"``)."""
        self._on_flag_picked("yes" if on else "any")

    def _on_delete_picked(self, value: str) -> None:
        if value not in ("any", "only", "hide"):
            value = "any"
        self._filter.to_delete = value
        self._after_change()

    def _on_cameras_changed(self, value) -> None:
        self._filter.cameras = set(value or ())
        self._after_change()

    def _on_lenses_changed(self, value) -> None:
        self._filter.lenses = set(value or ())
        self._after_change()

    def _on_date_from_changed(self, qdate: QDate) -> None:
        self._filter.date_from = self._qdate_to_date(qdate)
        self._after_change()

    def _on_date_to_changed(self, qdate: QDate) -> None:
        self._filter.date_to = self._qdate_to_date(qdate)
        self._after_change()

    # ── helpers ─────────────────────────────────────────────────────

    def _after_change(self) -> None:
        if self._suppress_emit:
            return
        self.filter_changed.emit(self._filter)

    @staticmethod
    def _qdate_to_date(qdate: QDate) -> Optional[date]:
        """``QDateEdit`` reads the min-value as "Any" (special text).
        Turn it into ``None`` for the predicate."""
        if not qdate.isValid():
            return None
        # The min-value / 1970-01-01 sentinel reads as "unbounded".
        py = date(qdate.year(), qdate.month(), qdate.day())
        if py.year == 1970 and py.month == 1 and py.day == 1:
            return None
        return py

    def _sync_controls(self) -> None:
        """Re-tick / re-select every control to match
        ``self._filter``. Uses :meth:`QComboBox.blockSignals` (or
        :meth:`StarRow.setValue`, which never emits) so the re-sync
        doesn't re-enter :meth:`_after_change`."""
        # Stars click row — StarRow.setValue is programmatic (no signal).
        self._star_row.setValue(self._filter.min_stars)
        # Colour swatches.
        self._colour_row.setValue(self._filter.colour_labels)
        # Flag toggle — treat both "any" and the legacy "no" case as
        # off (the toggle can't express "only unflagged"); "yes" is on.
        self._flag_toggle.blockSignals(True)
        try:
            self._flag_toggle.setValue(self._filter.flag == "yes")
        finally:
            self._flag_toggle.blockSignals(False)
        # Deletion combo.
        idx = max(0, self._del_combo.findData(self._filter.to_delete))
        self._del_combo.blockSignals(True)
        try:
            self._del_combo.setCurrentIndex(idx)
        finally:
            self._del_combo.blockSignals(False)
        # Cross-event knobs.
        self._camera_combo.set_values(self._filter.cameras)
        self._lens_combo.set_values(self._filter.lenses)
        self._sync_date_edit(self._date_from, self._filter.date_from)
        self._sync_date_edit(self._date_to, self._filter.date_to)

    @staticmethod
    def _sync_date_edit(edit: QDateEdit, value: Optional[date]) -> None:
        edit.blockSignals(True)
        try:
            if value is None:
                edit.setDate(edit.minimumDate())
            else:
                edit.setDate(QDate(value.year, value.month, value.day))
        finally:
            edit.blockSignals(False)


__all__ = ["FilterBar", "SCOPE_EVENT", "SCOPE_CROSS_EVENT"]
