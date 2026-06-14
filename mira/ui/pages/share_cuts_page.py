"""Surface 09 — Share / Cuts (cuts list + #exported pool).

The Share phase landing. Top of the page is a special accent-framed
`#exported` pool card (the universe every Cut starts from); below is the
list of user Cuts as cards with cover thumbnail · meta line · action
cluster.

Composition (design-system §Surface 09):
    Header:        ghost Back · 'Cuts' PageTitle · primary + New Cut
                   right.
    Pool card:     CrossEventBand-styled (accent border + wash) hosting
                   the globe icon + `#exported` title + Pool tag +
                   sub-line + ghost Open pool.
    Section label: 'Cuts · N'.
    Cut rows:      Card per cut with cover Thumb + info block (name in
                   bold, meta line in ink_soft) + Open primary / Adjust
                   ghost / Rename ghost / Delete danger-ghost.

Live gateway wiring (gateway.cuts() + gateway.exported_files() + the New
Cut dialog → Surface 13) lands in the route-swap commit. setForPreview
populates from mock data for the smoke + tests.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mira.gateway import Gateway
from mira.ui.design import (
    Card,
    Thumb,
    danger_ghost_button,
    ghost_button,
    primary_button,
    tag,
)

log = logging.getLogger(__name__)


@dataclass
class CutSnapshot:
    """One user Cut row's data."""

    cut_id: str
    name: str
    item_count: int = 0
    duration_seconds: int = 0
    description: str = ""
    exported_date: str = ""
    cover_pixmap: QPixmap | None = None


@dataclass
class PoolSnapshot:
    """The built-in #exported pool — the universe every Cut starts from."""

    exported_count: int = 0
    sub_line: str = ""


def _fmt_duration(s: int) -> str:
    m, sec = divmod(max(0, int(s)), 60)
    return f"{m}:{sec:02d}"


class _PoolCard(QFrame):
    """Accent-framed pool card mirroring the CrossEventCutsBand chrome."""

    open_requested = pyqtSignal()

    def __init__(self, pool: PoolSnapshot) -> None:
        super().__init__()
        self.setObjectName("CrossEventBand")
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 14, 18, 14)
        h.setSpacing(14)
        # Globe icon tile
        tile = QLabel("🌐")
        tile.setFixedSize(50, 50)
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setStyleSheet(
            "background: #211f3a; color: #7c6cff;"
            " border: 1px solid #7c6cff; border-radius: 14px;"
            " font-size: 22px;"
        )
        h.addWidget(tile)
        # Label block
        block = QVBoxLayout()
        block.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        t = QLabel("#exported")
        t.setObjectName("CardTitle")
        title_row.addWidget(t)
        title_row.addWidget(tag("Pool"))
        title_row.addStretch()
        block.addLayout(title_row)
        sub_text = pool.sub_line or (
            f"{pool.exported_count} exported files — the universe"
            f" every cut starts from."
        )
        sub = QLabel(sub_text)
        sub.setObjectName("Sub")
        block.addWidget(sub)
        h.addLayout(block, 1)
        # Open pool ghost button
        btn = ghost_button("Open pool")
        btn.clicked.connect(self.open_requested.emit)
        h.addWidget(btn)


class CutRow(Card):
    """One user Cut card row."""

    open_requested = pyqtSignal(str)
    adjust_requested = pyqtSignal(str)
    rename_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)

    def __init__(
        self,
        snapshot: CutSnapshot,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, padded=True)
        self._snapshot = snapshot
        self.setMinimumHeight(120)
        self.layout().setContentsMargins(16, 14, 16, 14)
        self.layout().setSpacing(10)

        row = QHBoxLayout()
        row.setSpacing(14)
        # Cover thumb (no state border — Cuts are post-pick)
        cover = Thumb(
            snapshot.cover_pixmap,
            state=None, size=QSize(140, 100),
        )
        row.addWidget(cover)

        # Info block
        info = QVBoxLayout()
        info.setSpacing(4)
        name_label = QLabel(snapshot.name)
        name_label.setObjectName("CardTitle")
        info.addWidget(name_label)
        meta_bits = [
            f"<b>{snapshot.item_count}</b> items",
            f"<b>{_fmt_duration(snapshot.duration_seconds)}</b>",
        ]
        if snapshot.description:
            meta_bits.append(snapshot.description)
        if snapshot.exported_date:
            meta_bits.append(f"exported <b>{snapshot.exported_date}</b>")
        meta = QLabel(" · ".join(meta_bits))
        meta.setObjectName("Sub")
        meta.setTextFormat(Qt.TextFormat.RichText)
        meta.setWordWrap(True)
        info.addWidget(meta)
        info.addStretch()
        info_wrap = QWidget()
        info_wrap.setLayout(info)
        row.addWidget(info_wrap, 1)

        # Action cluster
        actions = QHBoxLayout()
        actions.setSpacing(8)
        open_btn = primary_button("Open")
        open_btn.clicked.connect(
            lambda: self.open_requested.emit(snapshot.cut_id)
        )
        actions.addWidget(open_btn)
        adjust_btn = ghost_button("Adjust")
        adjust_btn.clicked.connect(
            lambda: self.adjust_requested.emit(snapshot.cut_id)
        )
        actions.addWidget(adjust_btn)
        rename_btn = ghost_button("Rename")
        rename_btn.clicked.connect(
            lambda: self.rename_requested.emit(snapshot.cut_id)
        )
        actions.addWidget(rename_btn)
        delete_btn = danger_ghost_button("Delete")
        delete_btn.clicked.connect(
            lambda: self.delete_requested.emit(snapshot.cut_id)
        )
        actions.addWidget(delete_btn)
        actions_wrap = QWidget()
        actions_wrap.setLayout(actions)
        row.addWidget(actions_wrap)

        self.layout().addLayout(row)


class ShareCutsPage(QWidget):
    """Surface 09 — Share / Cuts list page.

    Signal names match the legacy CutsListPage contract so this is a
    drop-in replacement inside :class:`CutsShellPage` (the list↔detail
    stack chassis stays; only the list visual layer changes).
    """

    back_requested = pyqtSignal()
    new_cut_requested = pyqtSignal()
    pool_open_requested = pyqtSignal()
    open_requested = pyqtSignal(str)
    adjust_requested = pyqtSignal(str)
    rename_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)

    def __init__(
        self,
        gateway: Optional[Gateway] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.gateway = gateway
        self._pool = PoolSnapshot()
        self._cuts: list[CutSnapshot] = []
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 24, 32, 24)
        outer.setSpacing(18)

        head = QHBoxLayout()
        head.setSpacing(12)
        self._back = ghost_button("‹ Back")
        self._back.clicked.connect(self.back_requested.emit)
        head.addWidget(self._back)
        title = QLabel("Cuts")
        title.setObjectName("PageTitle")
        head.addWidget(title)
        head.addStretch()
        new_btn = primary_button("+ New Cut")
        new_btn.clicked.connect(self.new_cut_requested.emit)
        head.addWidget(new_btn)
        outer.addLayout(head)

        # Pool card slot
        self._pool_slot = QVBoxLayout()
        self._pool_slot.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self._pool_slot)

        # Section label
        self._section_label = QLabel("Cuts · 0")
        self._section_label.setObjectName("Micro")
        outer.addWidget(self._section_label)

        # Cuts list scroll
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        inner = QWidget()
        self._cuts_layout = QVBoxLayout(inner)
        self._cuts_layout.setContentsMargins(0, 0, 0, 0)
        self._cuts_layout.setSpacing(12)
        self._cuts_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(inner)
        self._scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        outer.addWidget(self._scroll, 1)

    # ── data API ────────────────────────────────────────────────────────

    def setForPreview(
        self,
        pool: PoolSnapshot,
        cuts: list[CutSnapshot],
    ) -> None:
        self._pool = pool
        self._cuts = list(cuts)
        self._render()

    def _render(self) -> None:
        # Rebuild pool card
        while self._pool_slot.count():
            it = self._pool_slot.takeAt(0)
            w = it.widget() if it else None
            if w is not None:
                w.deleteLater()
        pool_card = _PoolCard(self._pool)
        pool_card.open_requested.connect(self.pool_open_requested.emit)
        self._pool_slot.addWidget(pool_card)

        # Section label
        self._section_label.setText(f"Cuts · {len(self._cuts)}")

        # Cuts list
        while self._cuts_layout.count():
            it = self._cuts_layout.takeAt(0)
            w = it.widget() if it else None
            if w is not None:
                w.deleteLater()
        for snap in self._cuts:
            row = CutRow(snap)
            row.open_requested.connect(self.open_requested.emit)
            row.adjust_requested.connect(self.adjust_requested.emit)
            row.rename_requested.connect(self.rename_requested.emit)
            row.delete_requested.connect(self.delete_requested.emit)
            self._cuts_layout.addWidget(row)

    # ── gateway feed (live path) ───────────────────────────────────────

    def refresh_from_gateway(self, eg) -> None:
        """Pull pool + cuts from an opened :class:`EventGateway` and
        re-render. Called by :class:`~mira.ui.shared.cuts_shell.CutsShellPage`
        when the host opens an event."""
        exported = []
        try:
            exported = list(eg.exported_files())
        except Exception:                                          # noqa: BLE001
            log.exception("share-cuts: exported_files() failed")
        pool = PoolSnapshot(
            exported_count=len(exported),
            sub_line=(
                f"{len(exported)} exported file"
                + ("" if len(exported) == 1 else "s")
                + " — the universe every cut starts from."
            ),
        )
        cuts: list[CutSnapshot] = []
        try:
            cut_rows = list(eg.cuts())
        except Exception:                                          # noqa: BLE001
            log.exception("share-cuts: cuts() failed")
            cut_rows = []
        for c in cut_rows:
            cuts.append(CutSnapshot(
                cut_id=str(getattr(c, "id", "") or getattr(c, "tag", "")),
                name=getattr(c, "tag", None) or getattr(c, "name", "") or "",
                item_count=int(getattr(c, "item_count", 0) or 0),
                duration_seconds=int(getattr(c, "duration_seconds", 0) or 0),
                description=str(getattr(c, "description", "") or ""),
                exported_date=str(getattr(c, "created_at", "") or "")[:10],
            ))
        self.setForPreview(pool, cuts)
