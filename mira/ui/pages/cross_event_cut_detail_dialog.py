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
        close_btn = ghost_button(tr("Close"))
        close_btn.clicked.connect(self.accept)
        top.addWidget(close_btn)
        root.addLayout(top)

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

    def _fetch_member_groups(self) -> list:
        """Return ``[(event_id, [rows])]`` for the cut. Rows carry kind /
        relpath / added_at."""
        from mira.store.repo import EventStore
        anchor_entry = self._gw.index.get(self._cut_row.anchor_event_id)
        if anchor_entry is None:
            return []
        anchor_root = self._gw.index.resolve_root(
            anchor_entry, self._gw.photos_base_path())
        if anchor_root is None or not (anchor_root / "event.db").exists():
            return []
        store = EventStore.open(anchor_root / "event.db")
        try:
            rows = store.conn.execute(
                "SELECT kind, export_relpath, origin_relpath, event_id "
                "FROM cut_member WHERE cut_id = ? "
                "ORDER BY event_id IS NULL DESC, event_id, added_at",
                (self._cut_row.cut_id,),
            ).fetchall()
        finally:
            store.close()
        groups: list = []
        current_id: object = object()
        current_list: list = []
        for r in rows:
            eid = r["event_id"]
            if eid != current_id:
                current_id = eid
                current_list = []
                groups.append((eid, current_list))
            current_list.append({
                "kind": r["kind"],
                "export_relpath": r["export_relpath"],
                "origin_relpath": r["origin_relpath"],
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
