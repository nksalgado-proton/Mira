"""The cross-event "New Cut" dialog (spec/81 Phase 2 polish — Item 4 UI).

Pins a cross-event Dynamic Collection into a Cut (spec/81 §4 — the pin verb).
The engine — :class:`mira.shared.cross_event_cut_session.CrossEventCutSession`
— shipped with Phase 2 Item 4; this dialog is the surface that configures
the pin + drives the commit.

Surfaces:

* **Name** — free text + live tag preview (slug, reserved/taken warning).
* **Source DC** — pick from cross-event saved_filter rows (the user's
  previously-saved Dynamic Collections).
* **Anchor event** — which event.db hosts the cut row. Defaults to the event
  contributing the most members; user can override.
* **Pin mode** — keep-all (pin the whole DC 1:1, no per-item review) /
  weed-out (start all-in, skip rejects) / pick-in (start all-out, pick
  keepers). For this first cut, only keep-all is wired through to a
  commit — the Picker UI for weed-out / pick-in is substantial and
  deferred to its own session. The radios surface them so the user knows
  they're coming.
* **Budget** — target / max minutes + seconds per photo.
* **Music category** — from the user's audio library subdirectories.
* **Separators** + **Overlays** — cross-event defaults (OFF + ON) per
  spec/81 §3.1.

Pure UI — the host builds the inventories from
:class:`LibraryGateway.dynamic_collections` + the events index, drives the
commit via :class:`CrossEventCutSession.from_draft`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Sequence, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core import cut_names
from mira.shared.cut_draft import (
    PIN_KEEP_ALL,
    PIN_PICK_IN,
    PIN_WEED_OUT,
)
from mira.ui.design import ghost_button, line_input, primary_button, select
from mira.ui.i18n import tr


# --------------------------------------------------------------------------- #
# Data exchanged with the host
# --------------------------------------------------------------------------- #


@dataclass
class CrossEventCutInfo:
    """Everything the host needs to drive
    :class:`CrossEventCutSession.from_draft` + commit. Carries the user-typed
    name + the pin choice + the budget + the attachments + the references to
    the source DC and anchor event."""

    name: str
    source_dc_id: Optional[str] = None
    anchor_event_id: Optional[str] = None
    pin_mode: str = PIN_KEEP_ALL                  # only keep-all wired for now
    target_s: Optional[int] = None
    max_s: Optional[int] = None
    photo_s: float = 6.0
    music_category: Optional[str] = None
    separators: bool = False                       # cross-event default OFF
    overlay_fields: tuple = ()
    overlay_mode: Optional[str] = None             # 'embedded' | 'burn_in' | None


@dataclass(frozen=True)
class CrossEventCutInventories:
    """Inventories the dialog displays — host pulls from gateways +
    settings + library."""

    # (id, label) per cross-event DC the user can pin.
    dynamic_collections: Sequence[Tuple[str, str]] = field(default_factory=tuple)
    # (event_id, label) per anchorable event.
    events: Sequence[Tuple[str, str]] = field(default_factory=tuple)
    # Audio category subdirs (or [] when audio_library_path unset).
    music_categories: Sequence[str] = field(default_factory=tuple)


# --------------------------------------------------------------------------- #
# Overlay fields widget — same shape as event-scope
# --------------------------------------------------------------------------- #


_OVERLAY_FIELDS = (
    ("when",  tr("When (date/time)")),
    ("where", tr("Where (event/location)")),
    ("how1",  tr("How¹ (lens/camera/flash)")),
    ("how2",  tr("How² (aperture/shutter/ISO/focal)")),
)


class _OverlayFieldChecks(QWidget):
    """Four checkboxes — which provenance fields to draw on each frame."""

    changed = pyqtSignal()

    def __init__(self, *, defaults: Sequence[str] = (),
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._boxes: List[Tuple[QCheckBox, str]] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        defaults_set = set(defaults)
        for key, label in _OVERLAY_FIELDS:
            cb = QCheckBox(label, self)
            cb.setChecked(key in defaults_set)
            cb.toggled.connect(lambda _=False: self.changed.emit())
            self._boxes.append((cb, key))
            layout.addWidget(cb)
        layout.addStretch()

    def selected(self) -> List[str]:
        return [k for cb, k in self._boxes if cb.isChecked()]

    def set_selected(self, keys: Sequence[str]) -> None:
        target = set(keys)
        for cb, k in self._boxes:
            cb.blockSignals(True)
            cb.setChecked(k in target)
            cb.blockSignals(False)
        self.changed.emit()


# --------------------------------------------------------------------------- #
# The dialog
# --------------------------------------------------------------------------- #


class NewCrossEventCutDialog(QDialog):
    """Configure a cross-event Cut → emit :class:`CrossEventCutInfo` on
    accept. The host turns the info into a
    :class:`mira.shared.cut_draft.CrossEventCutDraft` + drives
    :meth:`CrossEventCutSession.from_draft` + commit."""

    saved = pyqtSignal(CrossEventCutInfo)

    def __init__(
        self,
        *,
        inventories: CrossEventCutInventories,
        existing_tags: Sequence[str] = (),
        default_anchor_event_id: Optional[str] = None,
        default_dc_id: Optional[str] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("New cross-event Cut"))
        self.setMinimumWidth(560)
        self._inventories = inventories
        self._existing_tags = list(existing_tags)
        self._default_anchor = default_anchor_event_id
        self._default_dc = default_dc_id
        self._build_layout()
        self._refresh_tag_preview()

    # ----- layout --------------------------------------------------------- #

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        identity = self._build_identity()
        root.addWidget(identity)

        # DC + anchor row.
        source = self._build_source()
        root.addWidget(source)

        # Pin mode + budget.
        pin_box = self._build_pin_mode()
        root.addWidget(pin_box)

        budget_box = self._build_budget()
        root.addWidget(budget_box)

        # Music + attachments.
        attach_box = self._build_attachments()
        root.addWidget(attach_box)

        # Footer.
        footer = QHBoxLayout()
        footer.addStretch()
        self._cancel = ghost_button(tr("Cancel"))
        self._cancel.clicked.connect(self.reject)
        footer.addWidget(self._cancel)
        self._create = primary_button(tr("Create"))
        self._create.clicked.connect(self._on_accept)
        footer.addWidget(self._create)
        root.addLayout(footer)

    def _build_identity(self) -> QWidget:
        box = QFrame()
        box.setObjectName("CrossEventCutIdentity")
        grid = QGridLayout(box)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(8)
        grid.addWidget(QLabel(tr("Name")), 0, 0)
        self._name = line_input(tr("e.g. Nepal highlights 2025"))
        self._name.textChanged.connect(self._refresh_tag_preview)
        grid.addWidget(self._name, 0, 1)
        self._tag_preview = QLabel("")
        self._tag_preview.setObjectName("CrossEventCutTagPreview")
        grid.addWidget(self._tag_preview, 1, 1)
        return box

    def _build_source(self) -> QWidget:
        box = QFrame()
        box.setObjectName("CrossEventCutSource")
        grid = QGridLayout(box)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(8)
        grid.addWidget(QLabel(tr("Source DC")), 0, 0)
        self._dc_combo = QComboBox()
        for dc_id, label in self._inventories.dynamic_collections:
            self._dc_combo.addItem(label, userData=dc_id)
        if self._default_dc is not None:
            for i in range(self._dc_combo.count()):
                if self._dc_combo.itemData(i) == self._default_dc:
                    self._dc_combo.setCurrentIndex(i)
                    break
        grid.addWidget(self._dc_combo, 0, 1)
        grid.addWidget(QLabel(tr("Anchor event")), 1, 0)
        self._anchor_combo = QComboBox()
        for ev_id, label in self._inventories.events:
            self._anchor_combo.addItem(label, userData=ev_id)
        if self._default_anchor is not None:
            for i in range(self._anchor_combo.count()):
                if self._anchor_combo.itemData(i) == self._default_anchor:
                    self._anchor_combo.setCurrentIndex(i)
                    break
        grid.addWidget(self._anchor_combo, 1, 1)
        hint = QLabel(tr(
            "The anchor event hosts the Cut row. Members from other events "
            "are linked to their source — the export resolves bytes "
            "per-event."))
        hint.setObjectName("CrossEventCutHint")
        hint.setWordWrap(True)
        grid.addWidget(hint, 2, 1)
        return box

    def _build_pin_mode(self) -> QWidget:
        box = QFrame()
        box.setObjectName("CrossEventCutPinMode")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        layout.addWidget(QLabel(tr("Pin mode")))
        self._pin_group = QButtonGroup(self)
        self._pin_buttons: List[Tuple[QRadioButton, str]] = []
        for value, label in (
            (PIN_KEEP_ALL,
             tr("Keep all (pin the DC 1:1 — every resolved item becomes a member)")),
            (PIN_WEED_OUT,
             tr("Weed out (start all-in, skip rejects in the Picker)")),
            (PIN_PICK_IN,
             tr("Pick in (start all-out, pick keepers in the Picker)")),
        ):
            rb = QRadioButton(label)
            if value == PIN_KEEP_ALL:
                rb.setChecked(True)
            self._pin_group.addButton(rb)
            self._pin_buttons.append((rb, value))
            layout.addWidget(rb)
        return box

    def _build_budget(self) -> QWidget:
        box = QFrame()
        box.setObjectName("CrossEventCutBudget")
        layout = QHBoxLayout(box)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        self._target_enable = QCheckBox(tr("Target (min)"))
        self._target_spin = QSpinBox()
        self._target_spin.setRange(1, 120)
        self._target_spin.setValue(5)
        self._target_spin.setEnabled(False)
        self._target_enable.toggled.connect(self._target_spin.setEnabled)
        layout.addWidget(self._target_enable)
        layout.addWidget(self._target_spin)

        self._max_enable = QCheckBox(tr("Max (min)"))
        self._max_spin = QSpinBox()
        self._max_spin.setRange(1, 240)
        self._max_spin.setValue(10)
        self._max_spin.setEnabled(False)
        self._max_enable.toggled.connect(self._max_spin.setEnabled)
        layout.addWidget(self._max_enable)
        layout.addWidget(self._max_spin)

        layout.addSpacing(12)
        layout.addWidget(QLabel(tr("Per-photo (s)")))
        self._photo_s_spin = QDoubleSpinBox()
        self._photo_s_spin.setRange(0.5, 60.0)
        self._photo_s_spin.setDecimals(1)
        self._photo_s_spin.setSingleStep(0.5)
        self._photo_s_spin.setValue(6.0)
        layout.addWidget(self._photo_s_spin)
        layout.addStretch()
        return box

    def _build_attachments(self) -> QWidget:
        box = QFrame()
        box.setObjectName("CrossEventCutAttachments")
        grid = QGridLayout(box)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(8)

        grid.addWidget(QLabel(tr("Music category")), 0, 0)
        cats = list(self._inventories.music_categories)
        if cats:
            self._music_combo = select([tr("(none)"), *cats])
        else:
            self._music_combo = select([tr("(no audio library configured)")])
            self._music_combo.setEnabled(False)
        grid.addWidget(self._music_combo, 0, 1)

        self._separators = QCheckBox(tr("Day separator slides"))
        self._separators.setChecked(False)              # cross-event default OFF
        grid.addWidget(self._separators, 1, 0, 1, 2)

        grid.addWidget(QLabel(tr("Overlay fields")), 2, 0)
        # spec/81 §3.1 cross-event default: overlays ON, all four fields.
        self._overlay_fields = _OverlayFieldChecks(
            defaults=("when", "where", "how1", "how2"))
        grid.addWidget(self._overlay_fields, 2, 1)

        grid.addWidget(QLabel(tr("Overlay mode")), 3, 0)
        self._overlay_mode_group = QButtonGroup(self)
        mode_row = QHBoxLayout()
        for value, label in (
            (None, tr("Default")),
            ("embedded", tr("Embedded EXIF/IPTC (PTE renders)")),
            ("burn_in", tr("Burn-in (Mira renders)")),
        ):
            rb = QRadioButton(label)
            if value is None:
                rb.setChecked(True)
            self._overlay_mode_group.addButton(rb)
            self._overlay_mode_group.setId(rb, 0 if value is None else
                                              (1 if value == "embedded" else 2))
            mode_row.addWidget(rb)
        mode_row.addStretch()
        mode_widget = QWidget()
        mode_widget.setLayout(mode_row)
        grid.addWidget(mode_widget, 3, 1)
        return box

    # ----- live updates --------------------------------------------------- #

    def _refresh_tag_preview(self) -> None:
        name = self._name.text()
        slug = cut_names.slugify(name)
        if not slug:
            self._tag_preview.setText("")
            return
        err = cut_names.check_tag(slug, self._existing_tags)
        if err == "reserved":
            self._tag_preview.setText(
                tr("tag: #{slug} — reserved name").format(slug=slug))
        elif err == "taken":
            self._tag_preview.setText(
                tr("tag: #{slug} — already in use").format(slug=slug))
        else:
            self._tag_preview.setText(tr("tag: #{slug}").format(slug=slug))

    # ----- composition --------------------------------------------------- #

    def _pin_mode(self) -> str:
        for btn, value in self._pin_buttons:
            if btn.isChecked():
                return value
        return PIN_KEEP_ALL

    def _overlay_mode_value(self) -> Optional[str]:
        btn = self._overlay_mode_group.checkedButton()
        if btn is None:
            return None
        idx = self._overlay_mode_group.id(btn)
        if idx == 1:
            return "embedded"
        if idx == 2:
            return "burn_in"
        return None

    def info(self) -> CrossEventCutInfo:
        return CrossEventCutInfo(
            name=self._name.text().strip(),
            source_dc_id=self._dc_combo.currentData(),
            anchor_event_id=self._anchor_combo.currentData(),
            pin_mode=self._pin_mode(),
            target_s=(self._target_spin.value() * 60
                      if self._target_enable.isChecked() else None),
            max_s=(self._max_spin.value() * 60
                   if self._max_enable.isChecked() else None),
            photo_s=float(self._photo_s_spin.value()),
            music_category=(
                self._music_combo.currentText()
                if (self._music_combo.isEnabled()
                    and self._music_combo.currentIndex() > 0)
                else None),
            separators=self._separators.isChecked(),
            overlay_fields=tuple(self._overlay_fields.selected()),
            overlay_mode=self._overlay_mode_value(),
        )

    # ----- accept gating + commit ---------------------------------------- #

    def _on_accept(self) -> None:
        if not self._name.text().strip():
            return
        slug = cut_names.slugify(self._name.text())
        if cut_names.check_tag(slug, self._existing_tags):
            return
        if self._dc_combo.currentData() is None:
            return                                          # no DC picked
        if self._anchor_combo.currentData() is None:
            return                                          # no anchor picked
        self.saved.emit(self.info())
        self.accept()


__all__ = [
    "CrossEventCutInfo",
    "CrossEventCutInventories",
    "NewCrossEventCutDialog",
]
