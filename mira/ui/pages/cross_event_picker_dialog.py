"""The cross-event Picker (spec/81 §4 / spec/61 §2 — weed-out / pick-in).

The cross-event sibling of :mod:`mira.ui.shared.cut_session_page`. Drives a
:class:`CrossEventCutSession` in weed-out (start all-in, skip rejects) or
pick-in (start all-out, pick keepers) mode, presented as a **Picker grid** —
the same green/red state-border thumbnails + Pick/Skip grammar as the
event-Cut picker (maximum reuse: the grid is :class:`ThumbGrid`, identical to
the one ``CutSessionPage`` drives). Each candidate is a packed key — a
(event_uuid, item_id) pair the resolver returned — spanning events, so there
is no single timeline (separators default OFF, spec/81 §3.1) and the grid is
flat-chronological rather than day-grouped.

Thumbnails span events, so the host supplies a ``thumb_resolver`` that maps a
candidate to its (cached) export thumb — the Picker stays gateway-agnostic
(it only knows the session + the resolver + a ``commit_callback``). The
resolver returns only ALREADY-CACHED thumbs; it never triggers synchronous
generation (the known first-open freeze), so an un-visited event's frames
render as neutral placeholders rather than stalling the grid.
"""
from __future__ import annotations

import logging
from typing import Callable, List, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QWidget,
    QVBoxLayout,
)

from core import cut_budget, cut_names
from mira.shared.cross_event_cut_session import (
    CrossEventCutSession,
    CrossEventSessionFile,
)
from mira.shared.cut_draft import PIN_PICK_IN, PIN_WEED_OUT
from mira.ui.design import ThumbGrid, ThumbGridItem, ghost_button, primary_button
from mira.ui.i18n import tr

log = logging.getLogger(__name__)

#: A picked candidate paints the green state border; a skipped one the red —
#: the exact tokens the event-Cut grid uses (Thumb's ``state`` contract).
_STATE_PICKED = "picked"
_STATE_SKIPPED = "skipped"

#: Resolver type: candidate → its cached export-thumb pixmap, or ``None`` when
#: no cached thumb exists (render a neutral placeholder, never block).
ThumbResolver = Callable[[CrossEventSessionFile], Optional[QPixmap]]


class CrossEventPickerDialog(QDialog):
    """Drive a :class:`CrossEventCutSession` to commit, via a Picker grid.

    The constructor takes the session + a ``commit_callback`` that the host
    wires to commit through the library gateway, plus an optional
    ``thumb_resolver`` for the grid thumbnails. The Picker only knows about
    the session.
    """

    #: Fires after a successful commit with the same session. Host uses it
    #: to refresh upstream lists.
    committed = pyqtSignal(CrossEventCutSession)

    def __init__(
        self,
        session: CrossEventCutSession,
        *,
        commit_callback: Callable[[CrossEventCutSession], None],
        thumb_resolver: Optional[ThumbResolver] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session = session
        self._commit_callback = commit_callback
        self._thumb_resolver = thumb_resolver
        self.setWindowTitle(tr("Pin cross-event Cut — {name}").format(
            name=cut_names.display_tag(cut_names.slugify(session.name or ""))))
        self.setMinimumSize(820, 600)
        self.setObjectName("CrossEventPickerDialog")
        self._build_layout()
        self._grid.set_items(self._build_items())
        self._refresh_budget()

    # ----- layout --------------------------------------------------------- #

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        root.addWidget(self._build_header())

        # The grid — the same widget the event-Cut picker drives. Single-zone
        # click toggles Pick/Skip (no single-view drill-in yet; a follow-up
        # can add center-zone open to match the event grid exactly).
        self._grid = ThumbGrid(two_zone_clicks=False)
        self._grid.cell_activated.connect(self._on_cell_activated)
        root.addWidget(self._grid, 1)

        root.addWidget(self._build_footer())

    def _build_header(self) -> QWidget:
        box = QFrame()
        box.setObjectName("CrossEventPickerHeader")
        layout = QHBoxLayout(box)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        info = QVBoxLayout()
        info.setSpacing(2)
        mode_text = {
            PIN_WEED_OUT: tr("Weed out — start all picked, skip rejects"),
            PIN_PICK_IN: tr("Pick in — start all skipped, pick keepers"),
        }.get(self._session.pin_mode, self._session.pin_mode)
        title = QLabel(
            tr("{n} candidates · {mode}").format(
                n=len(self._session.files), mode=mode_text))
        title.setObjectName("CrossEventPickerTitle")
        f = title.font(); f.setBold(True)
        title.setFont(f)
        info.addWidget(title)
        sub = QLabel(tr("Click a tile to Pick (green) / Skip (red)."))
        sub.setObjectName("CrossEventPickerSub")
        info.addWidget(sub)
        layout.addLayout(info, 1)

        # Batch controls — the "pick/skip in batch" ask.
        self._pick_all_btn = ghost_button(tr("Pick all"))
        self._pick_all_btn.setToolTip(tr("Mark every candidate Pick."))
        self._pick_all_btn.clicked.connect(lambda: self._set_all(True))
        layout.addWidget(self._pick_all_btn)
        self._skip_all_btn = ghost_button(tr("Skip all"))
        self._skip_all_btn.setToolTip(tr("Mark every candidate Skip."))
        self._skip_all_btn.clicked.connect(lambda: self._set_all(False))
        layout.addWidget(self._skip_all_btn)
        return box

    def _build_footer(self) -> QWidget:
        box = QFrame()
        box.setObjectName("CrossEventPickerFooter")
        layout = QHBoxLayout(box)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        self._budget_label = QLabel("")
        self._budget_label.setObjectName("CrossEventPickerBudget")
        layout.addWidget(self._budget_label, 1)

        self._cancel = ghost_button(tr("Cancel"))
        self._cancel.clicked.connect(self.reject)
        layout.addWidget(self._cancel)

        self._commit = primary_button(tr("Pin Cut"))
        self._commit.clicked.connect(self._on_commit)
        layout.addWidget(self._commit)
        return box

    # ----- grid items ---------------------------------------------------- #

    def _build_items(self) -> List[ThumbGridItem]:
        return [self._item_for(sf) for sf in self._session.files]

    def _item_for(self, sf: CrossEventSessionFile) -> ThumbGridItem:
        picked = self._session.is_picked(sf.key)
        pixmap: Optional[QPixmap] = None
        if self._thumb_resolver is not None:
            try:
                pixmap = self._thumb_resolver(sf)
            except Exception:                                  # noqa: BLE001
                log.exception("cross-event thumb resolve failed for %s", sf.key)
                pixmap = None
        return ThumbGridItem(
            pixmap=pixmap,
            state=_STATE_PICKED if picked else _STATE_SKIPPED,
            payload=sf.key,
            tooltip=self._tooltip(sf),
        )

    @staticmethod
    def _tooltip(sf: CrossEventSessionFile) -> str:
        bits: List[str] = []
        rel = sf.export_relpath or sf.origin_relpath or ""
        if rel:
            bits.append(rel)
        if sf.member_kind == "grab":
            bits.append(tr("grab"))
        if sf.kind == "video":
            bits.append(tr("video {s:.1f}s").format(s=sf.duration_ms / 1000.0))
        if sf.capture_time:
            bits.append(sf.capture_time[:10])
        return " · ".join(bits)

    # ----- callbacks ----------------------------------------------------- #

    def _on_cell_activated(self, index: int) -> None:
        files = self._session.files
        if not (0 <= index < len(files)):
            return
        sf = files[index]
        new_state = not self._session.is_picked(sf.key)
        self._session.set_state(sf.key, new_state)
        self._grid.update_item(index, self._item_for(sf))
        self._refresh_budget()

    def _set_all(self, picked: bool) -> None:
        for sf in self._session.files:
            self._session.set_state(sf.key, picked)
        # One rebuild — cheaper + simpler than N update_item calls, and the
        # thumbs are already cached on the items so it doesn't re-resolve cost.
        self._grid.set_items(self._build_items())
        self._refresh_budget()

    def _refresh_budget(self) -> None:
        totals = self._session.totals()
        seconds = totals.seconds(self._session.photo_s)
        zone = cut_budget.zone(seconds,
                               self._session.target_s,
                               self._session.max_s)
        picked = self._session.picked_count()
        total = len(self._session.files)
        mins = seconds / 60.0
        zone_text = {
            cut_budget.ZONE_GREEN: tr("green"),
            cut_budget.ZONE_AMBER: tr("amber"),
            cut_budget.ZONE_RED: tr("red"),
            cut_budget.ZONE_NONE: tr("no limit"),
        }.get(zone, zone)
        self._budget_label.setText(
            tr("{picked}/{total} picked · {mins:.1f} min · {zone}").format(
                picked=picked, total=total, mins=mins, zone=zone_text))
        # Reflect zone on the label's object name so QSS can style it.
        self._budget_label.setProperty("zone", zone)
        self._budget_label.style().unpolish(self._budget_label)
        self._budget_label.style().polish(self._budget_label)

    def _on_commit(self) -> None:
        try:
            self._commit_callback(self._session)
        except Exception as exc:                            # noqa: BLE001
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, tr("Commit failed"), self._commit_error_text(exc))
            return
        self.committed.emit(self._session)
        self.accept()

    @staticmethod
    def _commit_error_text(exc: Exception) -> str:
        """Map the gateway's tag validation codes to a human-readable
        message; anything else surfaces verbatim. ``'taken'`` is the common
        one — the Cut name collides with an existing Cut OR Collection (one
        global tag namespace)."""
        code = str(exc)
        if code == "taken":
            return tr(
                "A Cut or Collection with that name already exists. "
                "Choose a different name.")
        if code == "reserved":
            return tr("That name is reserved. Choose a different name.")
        if code == "empty":
            return tr("Enter a name for the Cut.")
        return tr("Could not pin Cut: {err}").format(err=code)


__all__ = ["CrossEventPickerDialog"]
