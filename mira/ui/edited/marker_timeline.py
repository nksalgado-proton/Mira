"""Marker timeline widget for the Edit video workshop (spec/56 §1 +
spec/59 §5).

Lifted out of the retired ``edit_video_page.py`` so the redesigned
:class:`~mira.ui.pages.editor_page.EditorPage` can host the workshop
inline when a video lands in its bucket sweep. Pure widget — no data
calls, no gateway: the host feeds it geometry + states and listens to
its signals.

The timeline paints:

* **Clip bands** between consecutive markers, washed green/red from each
  segment's ``phase_state`` (Pick/Skip — spec/59 §5: "what's green is
  what the next phase sees"; red = not marked for export).
* **Marker handles** as draggable cut points (the may-not-cross rule is
  enforced live during a drag; the gateway is the data-layer backstop).
* **Permanent endpoint marks** at start + end (the implicit markers from
  spec/56 §1).
* **Snapshot glyphs** as small squares below the bar, state-coloured.
* **Playhead** on top.

Signals:

* :attr:`seek_requested(int)` — ms; click on a band or drag-without-marker.
* :attr:`segment_clicked(int)` — ``seg_index`` under the click.
* :attr:`marker_selected(str)` — marker id (``""`` clears selection).
* :attr:`marker_moved(str, int)` — id + new ``at_ms`` (drag commit).
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPointF, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QWidget

from mira.ui.palette import PALETTE


# The locked design-system §5a status colours — never re-derived, never
# muted. The timeline reads them from PALETTE so a theme toggle
# re-tints the bands without an asset edit, and so the workshop's
# green/red match the photo grids' green/red exactly (cluster covers,
# day grid, Picker badge — Nelson 2026-06-15 "use the same green and
# red used elsewhere").
def _picked_colour() -> QColor:
    app = QApplication.instance()
    mode = (app.property("theme") if app else None) or "dark"
    return QColor(PALETTE[mode]["picked"])


def _skipped_colour() -> QColor:
    app = QApplication.instance()
    mode = (app.property("theme") if app else None) or "dark"
    return QColor(PALETTE[mode]["skipped"])


_C_BASE = QColor(0x4A, 0x52, 0x5C)
_C_MARKER = QColor(0xE8, 0xC5, 0x4A)            # cut-handle accent
_C_PLAYHEAD = QColor(0xFF, 0xFF, 0xFF)

#: Half-width of the marker handle hit zone (px).
_MARKER_GRAB_PX = 6


class MarkerTimeline(QWidget):
    """The workshop timeline. See module docstring for the visual model
    + signal contract."""

    seek_requested = pyqtSignal(int)            # ms
    segment_clicked = pyqtSignal(int)           # seg_index
    marker_selected = pyqtSignal(str)           # marker id ("" = cleared)
    marker_moved = pyqtSignal(str, int)         # id, new at_ms (drag commit)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoScrub")
        self.setMinimumHeight(34)
        self._lo = 0
        self._hi = 0
        self._pos = 0
        self._markers: list[tuple[str, int]] = []       # (id, at_ms) ascending
        self._bounds: list[tuple[int, int]] = []        # per seg_index
        self._states: list[str] = []                    # per seg_index
        self._snapshots: list[tuple[int, str]] = []     # (at_ms, state)
        self._selected_seg = -1
        self._selected_marker = ""
        self._min_gap = 1
        self._drag_marker = ""                          # id while dragging
        self._drag_ms: Optional[int] = None             # live drag position

    # ── model in ──────────────────────────────────────────────────────

    def setRange(self, lo: int, hi: int) -> None:       # noqa: N802
        self._lo, self._hi = int(lo), int(hi)
        self.update()

    def setValue(self, ms: int) -> None:                # noqa: N802
        self._pos = int(ms)
        self.update()

    def value(self) -> int:
        return self._pos

    def set_min_gap(self, ms: int) -> None:
        self._min_gap = max(1, int(ms))

    def set_model(
        self,
        markers: list[tuple[str, int]],
        bounds: list[tuple[int, int]],
        states: list[str],
        selected_seg: int,
        selected_marker: str = "",
        snapshots: list[tuple[int, str]] = (),
    ) -> None:
        self._markers = list(markers)
        self._bounds = list(bounds)
        self._states = list(states)
        self._snapshots = list(snapshots)
        self._selected_seg = int(selected_seg)
        self._selected_marker = selected_marker
        if self._drag_marker and self._drag_marker not in {
                mid for mid, _ in self._markers}:
            self._drag_marker = ""
            self._drag_ms = None
        self.update()

    # ── geometry helpers ──────────────────────────────────────────────

    def _x(self, ms: int) -> int:
        span = max(1, self._hi - self._lo)
        frac = min(1.0, max(0.0, (ms - self._lo) / span))
        return int(round(frac * max(1, self.width() - 1)))

    def _ms_at(self, x: float) -> int:
        frac = min(1.0, max(0.0, x / max(1, self.width())))
        return int(round(self._lo + frac * (self._hi - self._lo)))

    def _marker_at(self, x: float) -> str:
        """Marker id whose handle covers pixel ``x`` (nearest wins)."""
        best, best_d = "", _MARKER_GRAB_PX + 1
        for mid, ms in self._markers:
            d = abs(self._x(ms) - x)
            if d <= _MARKER_GRAB_PX and d < best_d:
                best, best_d = mid, d
        return best

    def _drag_bounds(self, marker_id: str) -> tuple[int, int]:
        """Legal ``at_ms`` window for a marker drag: strictly between its
        neighbours (and the implicit ends), one ``min_gap`` apart — the
        UI half of the gateway's may-not-cross rule."""
        ids = [mid for mid, _ in self._markers]
        i = ids.index(marker_id)
        lo = self._markers[i - 1][1] if i > 0 else self._lo
        hi = self._markers[i + 1][1] if i + 1 < len(self._markers) else self._hi
        return lo + self._min_gap, hi - self._min_gap

    # ── mouse ─────────────────────────────────────────────────────────

    def mousePressEvent(self, ev):                      # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton and self._hi > self._lo:
            x = ev.position().x()
            mid = self._marker_at(x)
            if mid:
                self._drag_marker = mid
                self._drag_ms = None
                self.marker_selected.emit(mid)
            else:
                if self._selected_marker:
                    self.marker_selected.emit("")
                ms = self._ms_at(x)
                self.seek_requested.emit(ms)
                if self._bounds:
                    idx = max(0, min(
                        len(self._bounds) - 1,
                        sum(1 for b in self._bounds if b[0] <= ms) - 1))
                    self.segment_clicked.emit(idx)
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):                       # noqa: N802
        if ev.buttons() & Qt.MouseButton.LeftButton and self._hi > self._lo:
            x = ev.position().x()
            if self._drag_marker:
                lo, hi = self._drag_bounds(self._drag_marker)
                if lo <= hi:
                    self._drag_ms = max(lo, min(self._ms_at(x), hi))
                    self.update()
            else:
                self.seek_requested.emit(self._ms_at(x))
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):                    # noqa: N802
        if self._drag_marker and self._drag_ms is not None:
            self.marker_moved.emit(self._drag_marker, int(self._drag_ms))
        self._drag_marker = ""
        self._drag_ms = None
        super().mouseReleaseEvent(ev)

    # ── paint ─────────────────────────────────────────────────────────

    def _marker_paint_ms(self, mid: str, ms: int) -> int:
        if mid == self._drag_marker and self._drag_ms is not None:
            return int(self._drag_ms)
        return ms

    def paintEvent(self, ev):                           # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            w, h = self.width(), self.height()
            bar_t, bar_b = 8, h - 12
            p.fillRect(0, bar_t, w, bar_b - bar_t, _C_BASE)
            if self._hi <= self._lo:
                return
            # Segment bands — live drag shifts the shared boundary too.
            shift = {mid: self._marker_paint_ms(mid, ms)
                     for mid, ms in self._markers}
            edges = [self._lo, *shift.values(), self._hi]
            picked_c = _picked_colour()
            skipped_c = _skipped_colour()
            for idx, state in enumerate(self._states[:max(0, len(edges) - 1)]):
                x0, x1 = self._x(edges[idx]), self._x(edges[idx + 1])
                colour = QColor(picked_c if state == "picked" else skipped_c)
                colour.setAlpha(120)
                p.fillRect(x0, bar_t, max(1, x1 - x0), bar_b - bar_t, colour)
                if idx == self._selected_seg:
                    sel = QPen(_C_PLAYHEAD)
                    sel.setWidth(2)
                    p.setPen(sel)
                    p.drawRect(QRect(x0 + 1, bar_t + 1,
                                     max(2, x1 - x0 - 2), bar_b - bar_t - 2))
            # Snapshot glyphs — small squares below the bar, state-
            # coloured (spec/59 §5: snapshots are stops with their own
            # graphical representation on the timeline).
            for s_ms, s_state in self._snapshots:
                if not (self._lo <= s_ms <= self._hi):
                    continue
                sx = self._x(s_ms)
                p.setPen(QPen(QColor(0, 0, 0, 220), 1))
                p.setBrush(QColor(
                    picked_c if s_state == "picked" else skipped_c))
                p.drawRect(sx - 4, bar_b + 1, 9, 9)
            # The permanent endpoint markers (auto start + end).
            for e_ms in (self._lo, self._hi):
                ex = self._x(e_ms)
                pen = QPen(QColor(_C_MARKER))
                pen.setWidth(2)
                p.setPen(pen)
                p.drawLine(ex, bar_t - 4, ex, bar_b + 4)
            # Marker handles (the user's draggable cut points).
            for mid, ms in self._markers:
                mx = self._x(self._marker_paint_ms(mid, ms))
                accent = QColor(_C_PLAYHEAD) if mid == self._selected_marker \
                    else QColor(_C_MARKER)
                pen = QPen(accent)
                pen.setWidth(3 if mid == self._selected_marker else 2)
                p.setPen(pen)
                p.drawLine(mx, bar_t - 4, mx, bar_b + 4)
                p.setBrush(accent)
                p.setPen(QPen(QColor(0, 0, 0, 200), 1))
                p.drawPolygon(
                    QPointF(mx - 5, bar_t - 4), QPointF(mx + 5, bar_t - 4),
                    QPointF(mx, bar_t + 3))
            # Playhead.
            px = self._x(self._pos)
            halo = QPen(QColor(0, 0, 0, 200))
            halo.setWidth(4)
            p.setPen(halo)
            p.drawLine(px, 0, px, h)
            core = QPen(_C_PLAYHEAD)
            core.setWidth(2)
            p.setPen(core)
            p.drawLine(px, 0, px, h)
        finally:
            p.end()


__all__ = ["MarkerTimeline"]
