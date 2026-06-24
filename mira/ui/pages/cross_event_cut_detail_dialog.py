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
        # spec/117 — persistent post-export actions on a shipped Cut.
        # Same gates as the per-event surface: visible when
        # ``last_exported_at`` is set; PTE further gated by ``use_pte``
        # + ``pte_launch_available`` + a ``.pte`` found on disk.
        self._open_pte_btn = ghost_button(tr("Open in PTE"))
        self._open_pte_btn.setToolTip(tr(
            "Reopen this exported Cut's slideshow.pte in PTE — no "
            "re-export needed."))
        self._open_pte_btn.clicked.connect(self._on_open_in_pte)
        self._open_pte_btn.setVisible(False)
        top.addWidget(self._open_pte_btn)
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
        """Flip the Open folder / Open in PTE buttons based on whether
        the cross-event Cut shipped and the state of its bundle."""
        from mira.shared.exported_cut_actions import is_exported
        from mira.shared.pte_launch import pte_launch_available
        if not is_exported(self._cut_row):
            return
        loc = self._resolve_location()
        if loc is None:
            return
        self._open_folder_btn.setVisible(True)
        settings = self._load_settings()
        pte_available = (
            loc.pte_available
            and getattr(settings, "use_pte", False)
            and pte_launch_available(
                getattr(settings, "pte_path", "") if settings else ""))
        self._open_pte_btn.setVisible(bool(pte_available))

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
        from mira.shared.pte_launch import open_in_pte
        loc = self._resolve_location()
        if loc is None or loc.pte_file is None:
            return
        settings = self._load_settings()
        pte_path = getattr(settings, "pte_path", "") if settings else ""
        if not pte_path:
            return
        try:
            from pathlib import Path
            open_in_pte(Path(pte_path), loc.pte_file)
        except OSError as exc:
            log.warning("open_in_pte failed: %s", exc)

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
