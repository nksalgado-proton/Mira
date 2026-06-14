"""Reusable aspect-ratio picker widget.

Used by the Process toolbar (per-photo override) and the future
wizard / event-plan editor (event-wide default). Single source so the
choices stay in sync and every surface offering an aspect-ratio
picker shows the same list.

Selecting an item emits ``label_changed(str)`` with the new label —
the host wires it to whatever needs to react (re-render preview,
update overlay, persist to settings, etc.).
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QWidget

from core.aspect_ratio import (
    aspect_ratio_labels,
    get_aspect_ratio,
)

from mira.ui.i18n import tr


class AspectRatioCombo(QComboBox):
    """QComboBox preloaded with the project aspect ratios.

    Display and data are split (Nelson 2026-06-11): the no-crop entry
    SHOWS "No Crop" while its persisted label stays "Original" — every
    stored ``aspect_label``, ``get_aspect_ratio`` lookup and
    ``label_changed`` payload keeps the canonical label vocabulary.

    Use :attr:`selected_label` to read the current choice (always one
    of the labels from :func:`aspect_ratio_labels`) and
    :meth:`set_selected_label` to set it; unknown / missing values
    fall back to "Original" so legacy events or settings that
    predate this feature stay safe.
    """

    label_changed = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        for label in aspect_ratio_labels():
            display = (tr("No Crop")
                       if get_aspect_ratio(label).is_original else label)
            self.addItem(display, label)
        self.setMinimumWidth(110)
        # Re-emit the canonical LABEL (the item data, not the display
        # text) so callers don't deal with QComboBox index plumbing.
        self.currentIndexChanged.connect(self._emit_label)

    def _emit_label(self, idx: int) -> None:
        label = self.itemData(idx)
        if label is not None:
            self.label_changed.emit(label)

    @property
    def selected_label(self) -> str:
        return self.currentData() or "Original"

    def set_selected_label(self, label: str) -> None:
        """Set the current item. Unknown labels fall back to
        "Original" (matches :func:`get_aspect_ratio`'s guard)."""
        ar = get_aspect_ratio(label)
        idx = self.findData(ar.label)
        # blockSignals so callers can pre-seed the combo without
        # triggering a render. Hosts that DO want the side effect
        # connect to ``label_changed`` and call this method themselves.
        prev = self.blockSignals(True)
        try:
            self.setCurrentIndex(idx if idx >= 0 else 0)
        finally:
            self.blockSignals(prev)
