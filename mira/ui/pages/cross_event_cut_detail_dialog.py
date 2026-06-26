"""Cross-event Cut detail viewer (minimum-viable flat grid).

Opens from the Cuts list's ``Open…`` action. Reads cut_member rows from the
anchor event.db; groups members by source ``event_id`` (NULL=anchor); per
group shows each member's relpath + kind. A first-cut viewer — the full
WYSIWYG flat grid with thumbnails + per-(event, day) separators is its own
follow-up.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core import cut_names
from mira.ui.design import ghost_button
from mira.ui.i18n import tr

log = logging.getLogger(__name__)


def _event_label(gateway, event_id: Optional[str], anchor_event_id: str,
                 anchor_event_name: str) -> str:
    """Render an event label for a member group. NULL event_id means the
    member is from the anchor event by the legacy convention."""
    if event_id is None:
        return f"{anchor_event_name} (anchor)"
    entry = gateway.index.get(event_id)
    if entry is None:
        return tr("{eid} (missing)").format(eid=event_id)
    return f"{entry.get('name') or event_id}"


class CrossEventCutDetailDialog(QDialog):
    """Minimum-viable detail viewer for a cross-event Cut. Lists members
    grouped by source event so the user can see the multi-event provenance
    of a cut at a glance."""

    def __init__(self, umbrella_gateway, cut_row,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._gw = umbrella_gateway
        self._cut_row = cut_row
        self.setWindowTitle(tr("Cut — {tag}").format(
            tag=cut_names.display_tag(cut_row.tag)))
        self.setMinimumSize(640, 480)
        self.setObjectName("CrossEventCutDetailDialog")
        self._build_layout()

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        top = QHBoxLayout()
        title = QLabel(cut_names.display_tag(self._cut_row.tag))
        title.setObjectName("CrossEventCutDetailTitle")
        f = title.font(); f.setBold(True); f.setPointSize(f.pointSize() + 2)
        title.setFont(f)
        top.addWidget(title)
        top.addStretch()
        # spec/117 + spec/149 — persistent post-export actions on a
        # shipped Cut. Same gates as the per-event surface (visible when
        # ``last_exported_at`` is set); Open in PTE additionally needs
        # ``use_pte`` + ``pte_launch_available`` + folder-exists (the
        # .pte itself is no longer required — :meth:`_on_open_in_pte`
        # auto-generates when missing per spec/149 §2.B). Generate PTE
        # needs ``use_pte`` + folder-exists (the launcher path is
        # irrelevant when only writing).
        self._open_pte_btn = ghost_button(tr("Open in PTE"))
        self._open_pte_btn.setToolTip(tr(
            "Reopen this exported Cut's slideshow.pte in PTE — no "
            "re-export needed."))
        self._open_pte_btn.clicked.connect(self._on_open_in_pte)
        self._open_pte_btn.setVisible(False)
        top.addWidget(self._open_pte_btn)
        # spec/149 — standalone Generate PTE for the cross-event surface.
        self._generate_pte_btn = ghost_button(tr("Generate PTE"))
        self._generate_pte_btn.setToolTip(tr(
            "Rewrite slideshow.pte for this exported folder using the "
            "files there (no media re-export)."))
        self._generate_pte_btn.clicked.connect(self._on_generate_pte)
        self._generate_pte_btn.setVisible(False)
        top.addWidget(self._generate_pte_btn)
        self._open_folder_btn = ghost_button(tr("Open folder"))
        self._open_folder_btn.setToolTip(tr(
            "Reveal this exported Cut's bundle folder in Explorer."))
        self._open_folder_btn.clicked.connect(self._on_open_folder)
        self._open_folder_btn.setVisible(False)
        top.addWidget(self._open_folder_btn)
        close_btn = ghost_button(tr("Close"))
        close_btn.clicked.connect(self.accept)
        top.addWidget(close_btn)
        root.addLayout(top)

        self._sync_exported_actions()

        meta = QLabel(tr("anchor: {anchor} · {n} members").format(
            anchor=self._cut_row.anchor_event_name,
            n=self._cut_row.member_count))
        meta.setObjectName("CrossEventCutDetailMeta")
        root.addWidget(meta)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        host = QWidget(scroll)
        body = QVBoxLayout(host)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)

        groups = self._fetch_member_groups()
        if not groups:
            empty = QLabel(tr("No members."))
            empty.setObjectName("CrossEventCutDetailEmpty")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            body.addWidget(empty)
        else:
            for event_id, rows in groups:
                label = _event_label(
                    self._gw, event_id,
                    self._cut_row.anchor_event_id,
                    self._cut_row.anchor_event_name)
                body.addWidget(self._build_group(label, rows))
        body.addStretch()
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

    # ── spec/117 — persistent post-export actions ────────────────

    def _sync_exported_actions(self) -> None:
        """Flip the Open folder / Open in PTE / Generate PTE buttons
        based on whether the cross-event Cut shipped and the state of
        its bundle.

        spec/149 — Open in PTE no longer requires a ``.pte`` on disk;
        the handler auto-generates when missing. Generate PTE shows
        when ``use_pte`` is on AND the folder exists (independent of
        the launcher path)."""
        from mira.shared.exported_cut_actions import is_exported
        from mira.shared.pte_launch import pte_launch_available
        if not is_exported(self._cut_row):
            return
        loc = self._resolve_location()
        if loc is None:
            return
        self._open_folder_btn.setVisible(True)
        settings = self._load_settings()
        use_pte = bool(getattr(settings, "use_pte", False)) if settings else False
        pte_path_ok = pte_launch_available(
            getattr(settings, "pte_path", "") if settings else "")
        # spec/149 §2.B — Open in PTE: drop the .pte presence gate,
        # auto-generate covers it. Still need a real folder + working
        # launcher.
        pte_available = use_pte and pte_path_ok and loc.folder_exists
        self._open_pte_btn.setVisible(bool(pte_available))
        # spec/149 §2.A — Generate PTE: write into the existing folder,
        # launcher irrelevant.
        self._generate_pte_btn.setVisible(
            bool(use_pte and loc.folder_exists))

    def _resolve_location(self):
        """Resolve the bundle via the cross-event resolver, fed
        ``library_root`` + ``cuts_export_root`` from settings. Returns
        ``None`` when the library root can't be resolved (offline
        bootstrap)."""
        from mira.paths import library_root as _library_root_from_paths
        from mira.shared.exported_cut_actions import (
            resolve_cross_event_cut_location,
        )
        root = _library_root_from_paths()
        if root is None:
            return None
        settings = self._load_settings()
        cuts_export_root = (
            getattr(settings, "cuts_export_root", "") or ""
            if settings else "")
        return resolve_cross_event_cut_location(
            cut_tag=self._cut_row.tag,
            library_root=root,
            cuts_export_root=cuts_export_root or None,
        )

    def _load_settings(self):
        try:
            return self._gw.settings.load()
        except Exception:                                          # noqa: BLE001
            return None

    def _on_open_folder(self) -> None:
        from mira.shared.pte_launch import reveal_in_explorer
        loc = self._resolve_location()
        if loc is None:
            return
        try:
            reveal_in_explorer(loc.folder)
        except OSError as exc:
            log.warning("reveal_in_explorer failed: %s", exc)

    def _on_open_in_pte(self) -> None:
        """spec/149 §2.B — auto-generate the ``.pte`` when missing, then
        launch. Mirrors the per-event flow."""
        from mira.shared.pte_launch import open_in_pte
        loc = self._resolve_location()
        if loc is None:
            return
        settings = self._load_settings()
        pte_path = getattr(settings, "pte_path", "") if settings else ""
        if not pte_path:
            return
        pte_file = loc.pte_file
        if pte_file is None:
            use_pte = bool(getattr(settings, "use_pte", False)) if settings else False
            if use_pte and loc.folder_exists:
                pte_file = self._generate_pte_into_folder(loc.folder)
        if pte_file is None:
            log.warning(
                "Open in PTE: no .pte for cross-event %s",
                getattr(self._cut_row, "tag", "?"))
            return
        try:
            from pathlib import Path
            open_in_pte(Path(pte_path), pte_file)
        except OSError as exc:
            log.warning("open_in_pte failed: %s", exc)

    def _on_generate_pte(self) -> None:
        """spec/149 §2.A — write a fresh ``.pte`` into the resolved
        cross-event folder using the files already there. No media
        re-materialisation. Hidden upstream when ``use_pte`` is off
        or the folder is gone, so the handler trusts the call site."""
        from PyQt6.QtGui import QGuiApplication
        from PyQt6.QtWidgets import QMessageBox
        loc = self._resolve_location()
        if loc is None or not loc.folder_exists:
            log.warning(
                "Generate PTE: bundle folder missing for cross-event %s",
                getattr(self._cut_row, "tag", "?"))
            return
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            pte_file = self._generate_pte_into_folder(loc.folder)
        finally:
            QGuiApplication.restoreOverrideCursor()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setWindowTitle(tr("Generate PTE"))
        if pte_file is None:
            box.setText(tr(
                "Mira couldn't write the .pte — the export folder has "
                "no media to wrap in a project, or the generator failed. "
                "See the log for details."))
        else:
            box.setText(tr(
                "Wrote {file}.").replace("{file}", str(pte_file)))
        box.exec()
        # A fresh .pte may unlock the launcher button — re-sync.
        self._sync_exported_actions()

    def _generate_pte_into_folder(self, folder):
        """spec/149 — call the shared standalone generator with the
        cross-event Cut's aspect / photo_s. Returns the path written, or
        ``None`` when generation didn't run (no media / failure)."""
        from mira.paths import library_root as _library_root_from_paths
        from mira.shared.cut_pte_generation import generate_pte_for_folder
        try:
            return generate_pte_for_folder(
                folder,
                aspect=getattr(self._cut_row, "aspect", None) or "16:9",
                photo_seconds=float(
                    getattr(self._cut_row, "photo_s", 6.0) or 6.0),
                stem=(getattr(self._cut_row, "tag", None) or "").strip()
                or "slideshow",
                library_root=_library_root_from_paths(),
            )
        except Exception:                                          # noqa: BLE001
            log.exception(
                "standalone PTE generation failed for cross-event %s",
                getattr(self._cut_row, "tag", "?"))
            return None

    def _fetch_member_groups(self) -> list:
        """Return ``[(event_id, [rows])]`` for the cut. Rows carry kind /
        relpath / added_at.

        spec/94 Phase 4a-ii: members live in mira.db (spec/93 §3); the
        read is one ``LibraryGateway.cross_event_cut_members`` call,
        no event.db opens. Per-event grouping is preserved (the library
        gateway returns rows ordered by event_id, then added_at)."""
        lg = self._gw.library_gateway()
        rows = lg.cross_event_cut_members(self._cut_row.cut_id)
        groups: list = []
        current_id: object = object()
        current_list: list = []
        for r in rows:
            eid = r.event_id
            if eid != current_id:
                current_id = eid
                current_list = []
                groups.append((eid, current_list))
            current_list.append({
                "kind": r.kind,
                "export_relpath": r.export_relpath,
                "origin_relpath": r.origin_relpath,
            })
        return groups

    def _build_group(self, label: str, rows: list) -> QWidget:
        box = QFrame()
        box.setObjectName("CrossEventCutDetailGroup")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(2)
        header = QLabel(tr("{label} · {n}").format(
            label=label, n=len(rows)))
        header.setObjectName("CrossEventCutDetailGroupHeader")
        f = header.font(); f.setBold(True)
        header.setFont(f)
        layout.addWidget(header)
        for r in rows:
            kind = r["kind"]
            relpath = r["export_relpath"] or r["origin_relpath"] or ""
            line = QLabel(f"  {kind}: {relpath}")
            line.setObjectName("CrossEventCutDetailRow")
            layout.addWidget(line)
        return box


__all__ = ["CrossEventCutDetailDialog"]
