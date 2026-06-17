"""The cross-event DC list (spec/81 Phase 2 polish — Item 5 follow-up).

Companion to :class:`NewCrossEventDcDialog`. The new-DC dialog is the CREATE
path; this is the BROWSE path — list the user's cross-event Dynamic
Collections (saved_filter rows), see their live counts, edit one, delete one,
or pin one into a cross-event Cut.

Modal dialog opened from the cross-event band on the events screen. Reads
the SavedFilter rows via :class:`LibraryGateway` and emits its own
``pin_requested(SavedFilter)`` signal that the host wires to the cross-event
Cut session (deferred — the cross-event "New Cut" dialog is its own surface).

No DB writes outside the gateway methods (``rename_dc`` / ``update_dc`` /
``delete_dc``); the dialog stays a thin view.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, List, Optional, Sequence

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core import collection_resolver, cut_names
from mira.ui.design import ghost_button, primary_button
from mira.ui.i18n import tr
from mira.ui.pages.facet_picker_dialog import GearProfileSnapshot
from mira.ui.pages.gear_profile_wizard import GearProfileWizard
from mira.ui.pages.new_cross_event_dc_dialog import (
    CrossEventDcInfo,
    CrossEventInventories,
    NewCrossEventDcDialog,
)
from mira.user_store import models as um

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# One row per SavedFilter — display + actions
# --------------------------------------------------------------------------- #


class _DcRow(QFrame):
    """One cross-event DC, displayed as a card row.

    Layout: left column has the tag + description + expr summary +
    filter summary + live count; right column has the action buttons
    (Pin → New Cut / Edit kebab with Delete).

    Signals:
        edit_requested(SavedFilter)
        delete_requested(SavedFilter)
        pin_requested(SavedFilter)
    """

    edit_requested = pyqtSignal(um.SavedFilter)
    delete_requested = pyqtSignal(um.SavedFilter)
    pin_requested = pyqtSignal(um.SavedFilter)

    def __init__(self, dc: um.SavedFilter, *,
                 live_count: int,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dc = dc
        self.setObjectName("CrossEventDcRow")
        self._build(live_count)

    def _build(self, live_count: int) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(2)

        # Tag (display: #tag) + description on the same row.
        tag_label = QLabel(cut_names.display_tag(self._dc.tag))
        tag_label.setObjectName("CrossEventDcTag")
        font = tag_label.font()
        font.setBold(True)
        tag_label.setFont(font)
        left.addWidget(tag_label)

        if self._dc.description:
            desc = QLabel(self._dc.description)
            desc.setObjectName("CrossEventDcDesc")
            desc.setWordWrap(True)
            left.addWidget(desc)

        # Recipe summary: origin operand + applied filters in short form.
        recipe_text = _recipe_summary(self._dc)
        if recipe_text:
            recipe = QLabel(recipe_text)
            recipe.setObjectName("CrossEventDcRecipe")
            recipe.setWordWrap(True)
            left.addWidget(recipe)

        # Live count (one line, accent-colored if non-zero).
        count = QLabel(tr("{n} items match").format(n=live_count))
        count.setObjectName("CrossEventDcCount")
        left.addWidget(count)

        outer.addLayout(left, 1)

        # Right column: Pin + kebab.
        right = QVBoxLayout()
        right.setSpacing(6)
        pin_btn = primary_button(tr("Pin → Cut"))
        pin_btn.clicked.connect(
            lambda: self.pin_requested.emit(self._dc))
        right.addWidget(pin_btn)

        kebab = ghost_button(tr("⋯"))
        kebab.clicked.connect(self._show_menu)
        self._kebab = kebab
        right.addWidget(kebab)
        outer.addLayout(right)

    def _show_menu(self) -> None:
        menu = QMenu(self)
        edit = menu.addAction(tr("Edit…"))
        edit.triggered.connect(
            lambda: self.edit_requested.emit(self._dc))
        delete = menu.addAction(tr("Delete"))
        delete.triggered.connect(
            lambda: self.delete_requested.emit(self._dc))
        menu.exec(self._kebab.mapToGlobal(self._kebab.rect().bottomLeft()))


def _recipe_summary(dc: um.SavedFilter) -> str:
    """Short text summary of a DC's recipe: origin operand + key
    filter narrowings. Tolerant of malformed JSON (logs + returns empty)."""
    parts: List[str] = []
    try:
        expr = json.loads(dc.expr_json or "[]")
    except (ValueError, TypeError):
        expr = []
    try:
        filters = json.loads(dc.filters_json or "{}")
        if not isinstance(filters, dict):
            filters = {}
    except (ValueError, TypeError):
        filters = {}

    # Origin operand — the first term's operand.
    if expr:
        try:
            op, operand = expr[0][0], expr[0][1]
            if isinstance(operand, str):
                parts.append(f"#{operand}")
            elif isinstance(operand, dict) and operand.get("tag"):
                parts.append(f"{op} #{operand['tag']}")
        except (IndexError, KeyError, TypeError):
            pass

    # Surface a few key narrowings — not exhaustive.
    catalogue = [
        ("styles", "styles"),
        ("media_type", "media"),
        ("stars_min", "stars≥"),
        ("country_codes", "country"),
        ("camera_ids", "camera"),
        ("lens_models", "lens"),
        ("iso_max", "iso≤"),
        ("iso_min", "iso≥"),
        ("aperture_max", "f/≤"),
        ("aperture_min", "f/≥"),
        ("shutter_min", "shutter≥"),
        ("focal_min", "focal≥"),
        ("focal_max", "focal≤"),
    ]
    for key, label in catalogue:
        if key not in filters:
            continue
        val = filters[key]
        if isinstance(val, list):
            if not val:
                continue
            parts.append(f"{label}=[{','.join(str(v) for v in val[:3])}{'…' if len(val) > 3 else ''}]")
        else:
            parts.append(f"{label}{val}")

    return " · ".join(parts)


# --------------------------------------------------------------------------- #
# The dialog
# --------------------------------------------------------------------------- #


class CrossEventDcsDialog(QDialog):
    """Modal browser for the user's cross-event Dynamic Collections.

    The host (events screen) opens it; it reads SavedFilter rows + the live
    count via :class:`LibraryGateway`, lets the user create / edit / delete /
    pin. The pin path emits :attr:`pin_requested` for the host to drive the
    cross-event Cut session (engine in
    :mod:`mira.shared.cross_event_cut_session`; UI dialog deferred).
    """

    pin_requested = pyqtSignal(um.SavedFilter)
    #: Emitted when the user clicks ``View Cuts`` — host opens the
    #: cross-event Cuts list dialog.
    view_cuts_requested = pyqtSignal()

    def __init__(
        self,
        library_gateway,
        *,
        umbrella_gateway=None,
        inventories: Optional[CrossEventInventories] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Cross-event collections"))
        self.setMinimumSize(720, 480)
        self.setObjectName("CrossEventDcsDialog")
        self._lg = library_gateway
        # The umbrella Gateway lets the Delete action ALSO sweep cross-
        # store references (spec/81 Phase 2 polish — Item 13). Tests can
        # omit it; the dialog falls back to a LibraryGateway-only delete.
        self._umbrella = umbrella_gateway
        # Inventories default to a live read; tests pass a stub.
        self._inventories = inventories or self._build_inventories()
        self._rows_layout: Optional[QVBoxLayout] = None
        self._empty_label: Optional[QLabel] = None
        self._build_layout()
        self.refresh()

    # ----- layout --------------------------------------------------------- #

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # Top bar: title + + New collection + Close.
        top = QHBoxLayout()
        title = QLabel(tr("Cross-event collections"))
        title.setObjectName("CrossEventDcsTitle")
        f = title.font(); f.setBold(True); f.setPointSize(f.pointSize() + 2)
        title.setFont(f)
        top.addWidget(title)
        top.addStretch()
        self._manage_gear_btn = ghost_button(tr("Manage my gear…"))
        self._manage_gear_btn.setToolTip(tr(
            "Tag which cameras and lenses you actively use — the picker "
            "and the classifier read these flags"))
        self._manage_gear_btn.clicked.connect(self._on_manage_gear)
        top.addWidget(self._manage_gear_btn)
        self._view_cuts_btn = ghost_button(tr("View Cuts"))
        self._view_cuts_btn.clicked.connect(self.view_cuts_requested.emit)
        top.addWidget(self._view_cuts_btn)
        self._new_btn = primary_button(tr("+ New collection"))
        self._new_btn.clicked.connect(self._on_new)
        top.addWidget(self._new_btn)
        close_btn = ghost_button(tr("Close"))
        close_btn.clicked.connect(self.accept)
        top.addWidget(close_btn)
        root.addLayout(top)

        # Body: scrollable rows.
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        host = QWidget(scroll)
        rows = QVBoxLayout(host)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(8)
        self._empty_label = QLabel(tr(
            "No cross-event collections yet. Use + New collection to define "
            "one — pick a ladder rung + the facets you want, save it, and it "
            "becomes a reusable cross-event query you can pin into Cuts."))
        self._empty_label.setObjectName("CrossEventDcsEmpty")
        self._empty_label.setWordWrap(True)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rows.addWidget(self._empty_label)
        rows.addStretch()
        self._rows_layout = rows
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

    # ----- refresh ------------------------------------------------------- #

    def refresh(self) -> None:
        """Re-read the cross-event DCs and rebuild the row list. Called on
        open + after every Edit / Delete / Create."""
        if self._rows_layout is None:
            return
        # Clear existing widgets (skip the empty label + stretch).
        for i in reversed(range(self._rows_layout.count())):
            item = self._rows_layout.itemAt(i)
            w = item.widget()
            if isinstance(w, _DcRow):
                w.setParent(None)
                w.deleteLater()

        dcs = self._lg.dynamic_collections()
        if not dcs:
            if self._empty_label is not None:
                self._empty_label.setVisible(True)
            return
        if self._empty_label is not None:
            self._empty_label.setVisible(False)

        # Insert rows above the stretch (which sits at the bottom).
        # We re-build by removing the stretch, appending rows, re-appending.
        # Use a simpler approach: insert at the front (above the empty label
        # + stretch).
        for dc in dcs:
            try:
                count = self._lg.dc_probe(
                    self._lg.dc_expr(dc), self._lg.dc_filters(dc))
            except Exception:                              # noqa: BLE001
                count = -1
            row = _DcRow(dc, live_count=count, parent=self)
            row.edit_requested.connect(self._on_edit)
            row.delete_requested.connect(self._on_delete)
            row.pin_requested.connect(self._on_pin)
            # Insert at position 0 — above the empty label (hidden) + stretch.
            self._rows_layout.insertWidget(0, row)

    # ----- actions ------------------------------------------------------- #

    def _on_new(self) -> None:
        dialog = NewCrossEventDcDialog(
            inventories=self._inventories,
            dc_probe=self._lg.dc_probe,
            existing_tags=tuple(d.tag for d in self._lg.dynamic_collections()),
            gear=self._gear_snapshot(),
            parent=self,
        )
        dialog.saved.connect(self._create_from_info)
        dialog.exec()

    def _on_edit(self, dc: um.SavedFilter) -> None:
        existing = CrossEventDcInfo(
            name=dc.tag,
            description=dc.description or "",
            expr=self._lg.dc_expr(dc),
            filters=self._lg.dc_filters(dc),
        )
        # Exclude this DC's own tag from the "taken" check so the rename
        # path allows keeping the same tag.
        other_tags = tuple(
            d.tag for d in self._lg.dynamic_collections() if d.id != dc.id)
        dialog = NewCrossEventDcDialog(
            inventories=self._inventories,
            dc_probe=self._lg.dc_probe,
            existing=existing,
            existing_tags=other_tags,
            gear=self._gear_snapshot(),
            parent=self,
        )
        dialog.saved.connect(lambda info: self._save_edit(dc, info))
        dialog.exec()

    def _on_delete(self, dc: um.SavedFilter) -> None:
        ok = QMessageBox.question(
            self,
            tr("Delete cross-event collection"),
            tr("Delete {tag}? The collection's recipe goes; Cuts that pinned "
               "from it survive (their frozen members + snapshot are "
               "untouched).").format(tag=cut_names.display_tag(dc.tag)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            if self._umbrella is not None:
                # Cross-store cleanup: NULL source_dc_id on every cut that
                # pointed at this DC across event.db files (spec/81 Phase 2
                # polish — freeze invariant moves to the gateway).
                self._umbrella.delete_cross_event_dc(dc.id)
            else:
                self._lg.delete_dc(dc.id)
        except Exception as exc:                           # noqa: BLE001
            QMessageBox.warning(
                self, tr("Delete failed"),
                tr("Could not delete: {err}").format(err=str(exc)))
            return
        self.refresh()

    def _on_pin(self, dc: um.SavedFilter) -> None:
        """Emit the host signal. The cross-event Cut dialog (where pinning
        actually happens) is its own surface, deferred to a follow-up; until
        then the host can show a "coming soon" message."""
        self.pin_requested.emit(dc)

    def _on_manage_gear(self) -> None:
        """Launch the spec/85 gear-profile wizard. The wizard owns its
        commit path (writes directly through the gateway); on dismissal
        the picker reads the updated snapshot the next time it opens."""
        wizard = GearProfileWizard(self._lg, parent=self)
        wizard.exec()

    # ----- helpers ------------------------------------------------------- #

    def _create_from_info(self, info: CrossEventDcInfo) -> None:
        try:
            self._lg.create_dc(
                info.name, expr=info.expr,
                filters=info.filters,
                description=info.description or None,
            )
        except ValueError as exc:
            QMessageBox.warning(
                self, tr("Create failed"),
                tr("Could not create: {err}").format(err=str(exc)))
            return
        self.refresh()

    def _save_edit(self, dc: um.SavedFilter,
                   info: CrossEventDcInfo) -> None:
        try:
            # Rename if the name (slug) changed.
            new_slug = cut_names.slugify(info.name)
            if new_slug != dc.tag:
                self._lg.rename_dc(dc.id, info.name)
            self._lg.update_dc(
                dc.id, expr=info.expr,
                filters=info.filters,
                description=info.description or "")
        except ValueError as exc:
            QMessageBox.warning(
                self, tr("Edit failed"),
                tr("Could not save changes: {err}").format(err=str(exc)))
            return
        self.refresh()

    def _build_inventories(self) -> CrossEventInventories:
        """The lazy seam (spec/83 §5): pass the gateway's per-facet resolver
        through so the dialog touches SQLite only when a filter is added,
        not at dialog open. Today the dialog still iterates the catalogue
        at construction; slice 3 (two-tier shell) flips it to true lazy."""
        return CrossEventInventories(facet_inventory=self._lg.facet_inventory)

    def _gear_snapshot(self) -> GearProfileSnapshot:
        """Build a fresh :class:`GearProfileSnapshot` from the user-store at
        dialog-open time (spec/85 §5). The slice-4 picker uses it to
        partition main vs occasional for the camera and lens facets; non-
        gear facets fall through to the count heuristic regardless."""
        cameras_active: set = set()
        cameras_occasional: set = set()
        lenses_active: set = set()
        lenses_occasional: set = set()
        for row in self._lg.get_gear_profile():
            if row.kind == "camera":
                bucket = cameras_active if row.is_active else cameras_occasional
            elif row.kind == "lens":
                bucket = lenses_active if row.is_active else lenses_occasional
            else:
                continue
            bucket.add(row.key)
        return GearProfileSnapshot(
            cameras_active=frozenset(cameras_active),
            cameras_occasional=frozenset(cameras_occasional),
            lenses_active=frozenset(lenses_active),
            lenses_occasional=frozenset(lenses_occasional),
        )


__all__ = ["CrossEventDcsDialog"]
