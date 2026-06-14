"""``ComparePage`` — dedicated compare-grid surface for the Picker
(Nelson 2026-06-09).

The PickPage's day grid and cluster sub-grid both expose a ``Compare``
button (on the top bar) when their current cell set has 2+ flat
Compare-state photos. Clicking it pushes ``ComparePage`` onto PickPage's
stack with those exact photos, rendered side-by-side via the existing
:class:`mira.ui.picked.grid_view.GridView` (the same comparison-
aware widget the legacy single-photo surface embedded for its grid
mode).

This page replaces the old grid-mode toggle inside
:class:`PickPhotoSurface`. The split is intentional:

* The day grid / cluster sub-grid are **navigation surfaces** — centre-
  click drills into a photo / cluster.
* :class:`ComparePage` is a **decision surface** — the only thing the
  user does here is finalise the K/D choice for each candidate photo
  by clicking its border ring.

The two responsibilities lived in one widget before this redesign; the
new shape is cleaner and surfaces the Compare action as a first-class
top-bar affordance instead of a hidden toggle.

State semantics inside the compare grid:

* The border-ring cycle is **K ↔ D only** (no cycle back into Compare).
  The user opened the compare grid because they want to finalise — not
  to re-mark as Compare. ``STATE_CANDIDATE`` is the entry state and the
  first click moves it to ``STATE_PICKED``.
* State writes go to the gateway via
  :meth:`EventGateway.set_phase_state` — same source of truth the
  navigation grid reads. When the user clicks Quit Comparison, the
  host (PickPage) reprojects the originating grid's cells so the new
  Pick / Skip states show as the user's expected colours.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mira.picked.model import CullItem
from mira.picked.status import (
    STATE_CANDIDATE,
    STATE_PICKED,
    STATE_SKIPPED,
)
from mira.ui.base.surface import back_button
from mira.ui.i18n import tr
from mira.ui.picked.grid_view import GridItem, GridView

log = logging.getLogger(__name__)


class ComparePage(QWidget):
    """Side-by-side compare grid for the Picker's Compare-state photos.

    Loaded via :meth:`load`; the host (PickPage) gathers the originating
    grid's Compare-state items + their current ``phase_state`` and pushes
    them in. Per-tile clicks cycle the journal directly through the
    gateway; the host listens to :attr:`state_changed` so it can refresh
    its own cell projections when the user Quits.

    Emits :attr:`quit_requested` when the user clicks Quit Comparison.
    The host swaps the page stack back to the originating grid and
    reprojects its cells (states may have changed)."""

    quit_requested = pyqtSignal()
    # Fired whenever a tile's state moves. ``(item_id, new_state)`` —
    # the host can either listen and refresh per-event, or batch-refresh
    # on ``quit_requested``. Both work; current PickPage uses the latter.
    state_changed = pyqtSignal(str, str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("ComparePage")
        self._eg = None
        self._phase = "pick"
        self._items: List[CullItem] = []
        self._states: Dict[str, str] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Top bar: Quit + caption ─────────────────────────────────
        top = QWidget(self)
        top.setObjectName("CompareTopBar")
        top_lay = QHBoxLayout(top)
        top_lay.setContentsMargins(16, 10, 16, 10)
        top_lay.setSpacing(12)

        # Compare's leave button — plain Back via the factory; the
        # CompareQuitButton secondary objectName is kept for any QSS
        # override slot, but the role is the uniform BackButton.
        self._quit_btn = back_button()
        self._quit_btn.setObjectName("CompareQuitButton")
        self._quit_btn.setToolTip(tr(
            "Return to the previous grid.  (Esc or C)"))
        self._quit_btn.clicked.connect(self.quit_requested.emit)
        top_lay.addWidget(self._quit_btn)

        self._caption = QLabel("")
        self._caption.setObjectName("CompareCaption")
        self._caption.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        top_lay.addWidget(self._caption, stretch=1)

        outer.addWidget(top)

        # ── Body: the comparison grid ───────────────────────────────
        self._grid = GridView(self)
        self._grid.state_cycle_requested.connect(self._on_cycle)
        outer.addWidget(self._grid, stretch=1)

    # ── Public API ────────────────────────────────────────────────────

    def load(
        self,
        eg,
        phase: str,
        items: Sequence[CullItem],
        phase_states: Dict[str, object],
    ) -> None:
        """Populate the compare grid with ``items`` (the originating
        grid's Compare-state photos). ``phase_states`` is the map the
        host already has — :meth:`EventGateway.phase_states` output —
        keyed by ``item_id``; used to render the per-tile starting
        border colour. Items without a row default to Compare."""
        self._eg = eg
        self._phase = phase
        self._items = list(items)
        self._states = {}
        grid_items: List[GridItem] = []
        for idx, ci in enumerate(self._items):
            ps = phase_states.get(ci.item_id)
            state = getattr(ps, "state", None) or STATE_CANDIDATE
            self._states[ci.item_id] = state
            grid_items.append(GridItem(
                index=idx,
                path=ci.path,
                state=state,
                rating=None,
                kind=ci.kind,
            ))
        self._grid.set_items(grid_items)
        self._caption.setText(tr(
            "Comparing {n} photo(s) — click a border to mark Pick / Skip."
        ).replace("{n}", str(len(self._items))))

    # ── Cycle handler ─────────────────────────────────────────────────

    def _on_cycle(self, index: int) -> None:
        """Border-ring click on a tile → cycle that item's state. Inside
        the compare grid the cycle is **K ↔ D only** (no return to
        Compare). Entry state ``candidate`` moves to ``picked`` on the
        first click (the user is here to finalise, not to leave items
        in limbo).

        Persistence is the HOST's job: a gateway-backed host (PickPage)
        passes ``eg`` and the write lands here; an in-memory host (Quick
        Sweep, pre-ingest) passes ``eg=None`` and persists by listening
        to :attr:`state_changed`. Either way the grid updates + the
        signal fires."""
        if not (0 <= index < len(self._items)):
            return
        item = self._items[index]
        cur = self._states.get(item.item_id, STATE_CANDIDATE)
        nxt = STATE_SKIPPED if cur == STATE_PICKED else STATE_PICKED
        if self._eg is not None:
            try:
                self._eg.set_phase_state(item.item_id, self._phase, nxt)
            except Exception:                                   # noqa: BLE001
                log.exception(
                    "ComparePage: set_phase_state failed for %s",
                    item.item_id)
                return
        self._states[item.item_id] = nxt
        self._grid.update_tile_state(index, nxt)
        self.state_changed.emit(item.item_id, nxt)

    # ── Keyboard ──────────────────────────────────────────────────────

    def keyPressEvent(self, event) -> None:                     # noqa: N802
        """Esc and C both quit. The host's global ``C`` shortcut also
        reaches this surface; routing it here is harmless (clicking the
        Quit button is the same path)."""
        key = event.key()
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_C):
            self.quit_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)
