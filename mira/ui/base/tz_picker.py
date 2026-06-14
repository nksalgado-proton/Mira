"""``TzPicker`` — the shared named-location timezone combo (P4).

docs/14 §"TZ named-location picker"; docs/18 §"Phase 4". Replaces
the raw-number TZ entry in **both** the plan editor's per-day TZ
cell and the culler's camera-clock dialog, killing the
``+5.45``/``+5.75`` decimal trap (Kathmandu is UTC+5:45 = 5.75 h,
*not* 5.45).

A plain ``QComboBox`` of well-known locations (data = float hours)
plus one **"Other offset…"** escape hatch that opens a *constrained*
numeric spinner (``QInputDialog.getDouble``, 0.25 steps) — the rare
custom case the backlog asked to keep, but never a free-text field.

Drop-in for the old ``QDoubleSpinBox``: it exposes ``value()`` /
``setValue(float)`` and a ``valueChanged(float)`` signal with the
same emission semantics (emits on a real change unless
``blockSignals`` is set), so the plan editor's first-day-TZ
propagation wiring keeps working unchanged. A stored value with no
exact named match (legacy plans, hand-edited JSON) is shown as a
transient "Custom — UTC±HH:MM" row so nothing is ever lost.
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor, QFocusEvent, QMouseEvent, QWheelEvent
from PyQt6.QtWidgets import QComboBox, QInputDialog, QWidget

from core.tz_locations import (
    TZ_LOCATIONS,
    format_utc_offset,
    nearest_location,
)
from mira.ui.i18n import tr  # ported into mira/ui (charter §4 step 7)

log = logging.getLogger(__name__)

# userData sentinel for the "Other offset…" row (no float value).
_CUSTOM = "__custom__"


class TzPicker(QComboBox):
    """Named-location UTC-offset picker. ``value()`` /
    ``setValue()`` / ``valueChanged(float)`` mirror the
    ``QDoubleSpinBox`` it replaces."""

    valueChanged = pyqtSignal(float)

    def __init__(
        self,
        initial: Optional[float] = None,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("TzPicker")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setToolTip(tr(
            "Pick the location whose local time the clock was set to. "
            "Choosing a place avoids the +5:45 vs +5.45 decimal "
            "mistake. Use “Other offset…” only for a zone not listed."
        ))
        # Source of truth for value() even if the sentinel row is the
        # transient current item mid-interaction.
        self._value: float = 0.0
        # Index of the transient "Custom — …" row, if one exists.
        self._custom_idx: Optional[int] = None
        # Re-entrancy guard: suppress the index-change → valueChanged
        # bridge while we mutate the model from setValue / dialog.
        self._suppress = False
        # User-engagement flag for the wheel guard: True only after a
        # real left-click or Tab/Backtab/Shortcut focus. Hover-induced
        # focus (Qt's WheelFocus + window-activation churn) does NOT
        # set it. Cleared on focusOut. wheelEvent() uses it instead of
        # ``hasFocus()`` because hasFocus() is True even when focus was
        # grabbed by mouse-over before the wheel arrived.
        self._user_engaged = False

        for place, off in TZ_LOCATIONS:
            self.addItem(f"{place} — {format_utc_offset(off)}", off)
        self.addItem(tr("Other offset…"), _CUSTOM)
        self._sentinel_idx = self.count() - 1

        self.currentIndexChanged.connect(self._on_index_changed)
        self.activated.connect(self._on_activated)

        self.setValue(initial if initial is not None else 0.0)

    # ── public QDoubleSpinBox-compatible API ─────────────────────────

    def value(self) -> float:
        """Current UTC offset in float hours."""
        data = self.currentData()
        if isinstance(data, (int, float)):
            return float(data)
        return self._value      # sentinel selected mid-flight

    def setValue(self, hours: float) -> None:
        """Select the location for ``hours``. Exact match → that
        named row; otherwise a transient *Custom — UTC±HH:MM* row is
        (re)inserted so the value is never silently rounded away.
        Emits ``valueChanged`` on a real change unless signals are
        blocked (matches ``QDoubleSpinBox.setValue``)."""
        hours = float(hours)
        changed = hours != self._value
        self._value = hours
        self._suppress = True
        try:
            idx = self._exact_index(hours)
            if idx is None:
                idx = self._ensure_custom_row(hours)
            else:
                self._drop_custom_row()
            self.setCurrentIndex(idx)
        finally:
            self._suppress = False
        if changed and not self.signalsBlocked():
            self.valueChanged.emit(hours)

    # ── model helpers ────────────────────────────────────────────────

    def _exact_index(self, hours: float) -> Optional[int]:
        for i in range(self.count()):
            data = self.itemData(i)
            if isinstance(data, (int, float)) and float(data) == hours:
                return i
        return None

    def _drop_custom_row(self) -> None:
        if self._custom_idx is not None:
            self.removeItem(self._custom_idx)
            self._custom_idx = None
            self._sentinel_idx = self.count() - 1

    def _ensure_custom_row(self, hours: float) -> int:
        """(Re)create the single transient custom row at its sorted
        position and return its index."""
        self._drop_custom_row()
        pos = self.count() - 1      # default: just before the sentinel
        for i in range(self.count()):
            data = self.itemData(i)
            if isinstance(data, (int, float)) and float(data) > hours:
                pos = i
                break
        label = tr("Custom — {tz}").replace(
            "{tz}", format_utc_offset(hours))
        self.insertItem(pos, label, hours)
        self._custom_idx = pos
        self._sentinel_idx = self.count() - 1
        return pos

    # ── signal bridge ────────────────────────────────────────────────

    def _on_index_changed(self, _idx: int) -> None:
        if self._suppress:
            return
        data = self.currentData()
        if not isinstance(data, (int, float)):
            return              # the sentinel; handled by _on_activated
        new = float(data)
        if new != self._value:
            self._value = new
            if not self.signalsBlocked():
                self.valueChanged.emit(new)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._user_engaged = True
        super().mousePressEvent(event)

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        if event.reason() in (
            Qt.FocusReason.TabFocusReason,
            Qt.FocusReason.BacktabFocusReason,
            Qt.FocusReason.ShortcutFocusReason,
        ):
            self._user_engaged = True
        super().focusInEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        self._user_engaged = False
        super().focusOutEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        """Refuse wheel unless the user has actually engaged this
        picker (real left-click or Tab/Backtab/Shortcut focus). The
        2026-06-14 rule: focus (and value mutation) only on left-click
        / Tab / Backtab / Shortcut, NEVER on hover-and-scroll.

        ``hasFocus()`` alone is not enough — Qt's WheelFocus policy
        plus window-activation churn on QTableWidget cell widgets can
        leave the picker focused after a mere hover, BEFORE wheelEvent
        runs. ``_user_engaged`` tracks the actual user intent (set in
        mousePressEvent / Tab focusInEvent, cleared on focusOut)."""
        if not self._user_engaged:
            event.ignore()
            return
        super().wheelEvent(event)

    def _on_activated(self, idx: int) -> None:
        """User picked a row. Only the sentinel needs work: prompt for
        a custom offset and apply it (or revert)."""
        if self.itemData(idx) != _CUSTOM:
            return
        current = self._value
        # Positional args — PyQt6's keyword names for the bounds are
        # binding-version-sensitive; positional is stable.
        new, ok = QInputDialog.getDouble(
            self,
            tr("Other offset"),
            tr("UTC offset in hours (e.g. 5.75 for Nepal, -3.5 for "
               "Newfoundland):"),
            current,    # value
            -12.0,      # min
            14.0,       # max
            2,          # decimals
        )
        if ok:
            self.setValue(round(new * 4) / 4.0)   # snap to 15-min grid
        else:
            self.setValue(current)                # revert the sentinel
