"""EventCard — the large per-event dashboard card (charter §4 step 7).

**Reused from the legacy ``ui/base/event_card.py``** (Nelson 2026-05-30: the events-list
card must be the legacy one, not a reinvention). The *rendering* is verbatim — the phase ×
normalised-day heatmap (``_GridCell`` painting, ``EventCardGrid`` layout, the 20-cell
normalisation via the pure ``core.event_card_grid``), the title/date/TZ left column, the
clickable card, the QSS roles. The **only change is the data seam** (charter §5.2): instead
of reaching into ``core.phase_progress`` (a cache) + filesystem walks + ``core.event_stats``,
the card is handed a plain :class:`EventCardData` the dashboard computes from the gateway
(``phase_day_progress`` is the gateway query behind it).

Deferred: the closed-event recap (TimezoneMap + cover photo + slideshow/genre stats). It
draws on Curate/Distribute data that no event has yet in the new store; it comes back —
reused the same way — when those surfaces land. Until then a closed event renders the same
heatmap with the distinct closed styling + "✓ Closed" pill.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QPainter
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.event_card_grid import (  # pure aggregation, reused as-is
    STATUS_DONE,
    STATUS_IN_PROGRESS,
    STATUS_NOT_STARTED,
    STATUS_READY,
    STATUS_UNAVAILABLE,
    aggregate_to_cells,
    cell_day_range,
)
from mira import event_classification
from mira.ui.base.flow_layout import FlowLayout
from mira.ui.i18n import tr

log = logging.getLogger(__name__)

# ── Layout constants (verbatim from legacy) ───────────────────────
N_CELLS = 20
LEFT_COL_WIDTH = 200
CELL_HEIGHT = 18
CELL_GAP = 1
ROW_PERCENT_WIDTH = 42
ROW_LABEL_WIDTH = 70

_CELL_COLORS: dict[str, QColor] = {
    STATUS_NOT_STARTED: QColor("#E0E0E0"),
    STATUS_READY:       QColor("#BBD8F0"),
    STATUS_IN_PROGRESS: QColor("#FBBF24"),
    STATUS_DONE:        QColor("#4ADE80"),
    STATUS_UNAVAILABLE: QColor("#F5F5F5"),
}

# Phase row keys — re-exported from event_classification (spec/44 §1.7) so the
# seam owns the single source of truth.
from mira.event_classification import (
    PHASE_COLLECT,
    PHASE_PICK,
    PHASE_EDIT,
    PHASE_SHARE,
)


@dataclass(frozen=True)
class _PhaseRow:
    key: str
    label: str


# Display labels for the heatmap row headers. The row SET is derived per
# event from event_classification.phases_for_type(event_type) at construction
# time; this dict just maps known phase keys to their compact label.
_PHASE_DISPLAY_LABELS: dict[str, str] = {
    "collect": "Collect",
    "pick":    "Pick",
    "edit":    "Edit",
    "export":  "Export",
    # spec/66: "share" survives only as the closed-event Cuts state; if
    # a legacy caller still iterates past it, label it for parity.
    "share":   "Share",
}


def _phase_rows_for(event_type: Optional[str]) -> tuple[_PhaseRow, ...]:
    """Heatmap row tuple for an event of the given type. Slice A returns the
    full pipeline for every type — the seam (spec/44 §1.7) is the single
    point of change when phases vary per type in the next sprint."""
    from mira import event_classification
    et = event_type or event_classification.EVENT_TYPE_UNCLASSIFIED
    return tuple(
        _PhaseRow(phase, _PHASE_DISPLAY_LABELS.get(phase, phase.title()))
        for phase in event_classification.phases_for_type(et)
    )


# Backward-compat alias: the full pipeline rows. Existing tests that import
# ``_PHASE_ROWS`` to assert on row keys keep working — Slice A still returns
# this exact list for every event_type.
_PHASE_ROWS: tuple[_PhaseRow, ...] = _phase_rows_for(None)


@dataclass
class EventCardData:
    """Everything one card renders — computed by the dashboard from the gateway, so the
    card has no data dependency of its own (charter §5.2)."""

    event_id: str
    name: str
    start_date: Optional[date]
    end_date: Optional[date]
    is_closed: bool
    total_days: int
    tz_display: str = ""
    # phase_key → {day_number: STATUS_*} (the grid input the dashboard derives).
    status_by_phase: Dict[str, Dict[int, str]] = field(default_factory=dict)
    # spec/44 — event_type drives the heatmap's row set through the
    # event_classification seam. Slice A: every type still resolves to the
    # full 6-phase pipeline.
    event_type: str = "unclassified"
    event_subtype: Optional[str] = None
    description: str = ""
    tags: list[str] = field(default_factory=list)
    # spec/64 §2.4 — closed-tile body data. Three side-by-side widgets
    # (Nelson 2026-06-13 v3): phase bar chart (counts cascade from
    # Collected → … → Exported as % of Collected) · classification
    # donut · legend. ``classification_counts`` maps each per-photo
    # classification (Scenario value) to its count across the event;
    # the donut + legend filter to non-zero slices.
    collected_count: int = 0
    picked_count: int = 0
    edited_count: int = 0
    exported_count: int = 0
    classification_counts: Dict[str, int] = field(default_factory=dict)
    # Surface 01 redesign (Nelson 2026-06-13): a small sample of the
    # event's exported finals' absolute paths feeds the closed-event card's
    # Carousel. Empty list = no exports yet (carousel renders its placeholder).
    # The legacy EventCard ignores this field; only EventCardRedesign uses it.
    sample_pixmap_paths: list = field(default_factory=list)
    # Spec/77 §4–§5 — the v2 EventTile donut grid needs these per-event
    # aggregates regardless of open/closed state. Populated for EVERY
    # event in _event_card_data.card_data (the legacy 'only closed
    # carries them' rule was dropped when the donuts moved onto the
    # open tile). All start at 0; a brand-new event with no captures
    # reads as four track-only donuts (the "nothing yet" look).
    decided_count: int = 0          # Pick — any explicit pick / skip decision
    developed_count: int = 0        # Edit — keepers with a user adjustment row
    days_with_captures: int = 0     # Collect — days the user actually shot on


# ── The grid widget (rendering verbatim; data injected) ───────────


class _GridCell(QWidget):
    """One small painted status rectangle (verbatim from legacy)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(CELL_HEIGHT)
        self.setMinimumWidth(4)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._status = STATUS_NOT_STARTED

    def set_status(self, status: str) -> None:
        if status != self._status:
            self._status = status
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        try:
            p.fillRect(self.rect(), _CELL_COLORS.get(self._status, _CELL_COLORS[STATUS_NOT_STARTED]))
        finally:
            p.end()


class EventCardGrid(QWidget):
    """Phase × normalised-day heatmap. Rendering verbatim from legacy; fed a
    ``{phase_key: {day_number: STATUS}}`` map + the day count (was: a legacy Event +
    ``core.phase_progress`` calls)."""

    def __init__(
        self,
        status_by_phase: Dict[str, Dict[int, str]],
        total_days: int,
        event_type: Optional[str] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._status_by_phase = status_by_phase
        self._days = max(0, int(total_days))
        # spec/44 §1.7 seam — Slice A returns the full pipeline for every type,
        # so behaviour is unchanged. Pass-through preserves the next-sprint
        # change without a separate refactor here.
        self._phase_rows = _phase_rows_for(event_type)
        self._cells_by_phase: dict[str, list[_GridCell]] = {}
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(CELL_GAP)
        layout.setVerticalSpacing(2)

        # Day-range header retired (Nelson 2026-06-06 eyeball): the labels
        # wrapped to two rows on narrow cards and the empty cols 0+1 of row 0
        # rendered as a wide vertical gap. Day info still surfaces in each
        # cell's tooltip on hover.

        self._percent_labels: dict[str, QLabel] = {}
        for row_index, phase in enumerate(self._phase_rows, start=0):
            phase_lbl = QLabel(tr(phase.label))
            phase_lbl.setObjectName("EventCardPhaseLabel")
            phase_lbl.setMinimumWidth(ROW_LABEL_WIDTH)
            layout.addWidget(phase_lbl, row_index, 0)

            pct_lbl = QLabel("—")
            pct_lbl.setObjectName("EventCardPercent")
            pct_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            pct_lbl.setMinimumWidth(ROW_PERCENT_WIDTH)
            layout.addWidget(pct_lbl, row_index, 1)
            self._percent_labels[phase.key] = pct_lbl

            cells: list[_GridCell] = []
            for col in range(N_CELLS):
                cell = _GridCell()
                layout.addWidget(cell, row_index, col + 2)
                cells.append(cell)
            self._cells_by_phase[phase.key] = cells

        for col in range(2, N_CELLS + 2):
            layout.setColumnStretch(col, 1)

    def refresh(self) -> None:
        n_days = self._days
        for phase in self._phase_rows:
            per_day = self._status_by_phase.get(phase.key, {})
            cells = aggregate_to_cells(per_day, total_days=n_days, n_cells=N_CELLS)
            widgets = self._cells_by_phase.get(phase.key, [])
            done = partial = 0
            for i, widget in enumerate(widgets):
                status = cells[i] if i < len(cells) else STATUS_NOT_STARTED
                widget.set_status(status)
                if status == STATUS_DONE:
                    done += 1
                elif status == STATUS_IN_PROGRESS:
                    partial += 1
                first, last = cell_day_range(i, N_CELLS, n_days)
                day_str = f"Day {first}" if first == last else f"Days {first}–{last}"
                widget.setToolTip(f"{tr(phase.label)} · {day_str}: {status.replace('_', ' ')}")
            pct_lbl = self._percent_labels.get(phase.key)
            if pct_lbl is not None:
                total = len(widgets) or 1
                pct = round((done + partial * 0.5) / total * 100)
                pct_lbl.setText(f"{pct}%")
                pct_lbl.setToolTip(
                    f"{tr(phase.label)} progress: {done} done, {partial} in progress, "
                    f"{total - done - partial} not started ({pct}% weighted)")


# ── The card (rendering verbatim; data from EventCardData) ─────────


class _ClickableZone(QFrame):
    """Hover-tinted clickable area on the event card. Each of the 3 zones is one
    of these — the QSS `:hover` selector paints the active zone so the user sees
    which area their cursor will activate. Emits ``clicked(event_id)`` on mouse
    release."""

    clicked = pyqtSignal(str)

    def __init__(self, role: str, event_id: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._event_id = event_id
        self.setObjectName(role)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._event_id:
            self.clicked.emit(self._event_id)
        super().mousePressEvent(event)


class _StatusBadge(QLabel):
    """The Open / Closed status badge (spec/64 §2.3). One on every tile,
    instant toggle on click — eats its own mouse press so the parent zone
    (Header dialog door) doesn't open underneath."""

    clicked = pyqtSignal(str)

    def __init__(self, event_id: str, is_closed: bool,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._event_id = event_id
        self.setObjectName("EventCardStatusBadge")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.set_closed(is_closed)
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def set_closed(self, is_closed: bool) -> None:
        # Plain text; colour (via the QSS [state=…] selector) carries the
        # visual cue (Nelson 2026-06-13 eyeball: no glyphs).
        self.setText(tr("Closed") if is_closed else tr("Open"))
        self.setProperty("state", "closed" if is_closed else "open")
        self.setToolTip(
            tr("Closed — click to re-open this event.") if is_closed
            else tr("Open — click to mark this event Closed."),
        )
        # Repolish so the QSS [state=…] selector picks up the new value
        # (memory `reference_qss_descendant_property_repolish`).
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._event_id:
            event.accept()
            self.clicked.emit(self._event_id)
            return
        super().mousePressEvent(event)


class _PhaseStatsChart(QWidget):
    """The closed-tile bar chart (spec/64 §2.4, Nelson 2026-06-13).

    Four horizontal rows — Collected / Picked / Edited / Exported — each
    coloured to read as a left-to-right progression through the
    workflow. The Collected bar is always 100 % wide (it's the universe
    every other phase is a subset of); the others fill proportionally.
    All four rows render even when zero so the layout doesn't jitter
    between cards.
    """

    # Bar colours read as the progression Collected → … → Exported.
    # Slate (raw stock) → blue (chosen) → amber (worked on) → emerald
    # (shipped). Picked at calibration time per the eyeball-loop
    # pattern; the bars are the eyeball target.
    _COLORS = {
        "collected": "#6B7280",
        "picked":    "#3B82F6",
        "edited":    "#F59E0B",
        "exported":  "#10B981",
    }
    _MIN_BAR_HEIGHT = 10
    _MAX_BAR_HEIGHT = 26
    _LABEL_WIDTH = 70
    _COUNT_WIDTH = 64

    def __init__(
        self,
        *,
        collected: int,
        picked: int,
        edited: int,
        exported: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("EventCardClosedBodyChart")
        self._rows = (
            ("collected", tr("Collected"), int(collected)),
            ("picked",    tr("Picked"),    int(picked)),
            ("edited",    tr("Edited"),    int(edited)),
            ("exported",  tr("Exported"),  int(exported)),
        )
        self._collected = max(0, int(collected))
        # Nelson 2026-06-13: the chart fills the heatmap's slot — same
        # vertical extent as the open-tile heatmap so closed and open
        # tiles share the same height. Bar height + gap auto-scale to
        # the available height (see paintEvent).
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(4 * self._MIN_BAR_HEIGHT + 12)

    def paintEvent(self, _event) -> None:  # noqa: N802
        from PyQt6.QtGui import QBrush, QColor, QFont, QPainter
        from PyQt6.QtCore import QRectF

        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            w = self.width()
            h = self.height()
            # Auto-scale: row slot = full / 4 (no extra gap accounting),
            # bar = 70 % of slot, clamped between min/max. Centres
            # vertically when the chart has extra room.
            slot = max(self._MIN_BAR_HEIGHT, h // 4)
            bar_h = max(self._MIN_BAR_HEIGHT,
                        min(self._MAX_BAR_HEIGHT, int(slot * 0.65)))
            total_rows_h = slot * 4
            top = max(0, (h - total_rows_h) // 2)
            font = QFont(p.font())
            font.setPixelSize(max(10, min(13, slot // 2)))
            p.setFont(font)
            bar_left = self._LABEL_WIDTH + 6
            count_left = w - self._COUNT_WIDTH
            bar_right = count_left - 8
            track_w = max(20, bar_right - bar_left)
            divisor = max(1, self._collected)
            for i, (key, label, count) in enumerate(self._rows):
                slot_y = top + i * slot
                # Bar y is the slot's vertical centre line for the bar.
                bar_y = slot_y + (slot - bar_h) // 2
                # Row label (left), vertically aligned to the bar.
                p.setPen(self.palette().windowText().color())
                p.drawText(
                    QRectF(0, slot_y, self._LABEL_WIDTH, slot),
                    int(Qt.AlignmentFlag.AlignVCenter
                        | Qt.AlignmentFlag.AlignRight),
                    label,
                )
                # Bar track (subtle background).
                track = QRectF(bar_left, bar_y, track_w, bar_h)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(QColor(0, 0, 0, 28)))
                p.drawRoundedRect(track, 3, 3)
                # Bar fill (the count proportion of Collected; Collected
                # itself always reads 100 %).
                fill_w = (track_w if key == "collected"
                          else track_w * count / divisor)
                fill_w = max(0.0, min(track_w, fill_w))
                if fill_w > 0:
                    p.setBrush(QBrush(QColor(self._COLORS[key])))
                    p.drawRoundedRect(
                        QRectF(bar_left, bar_y, fill_w, bar_h), 3, 3)
                # Count + percent on the right.
                if self._collected > 0 and key != "collected":
                    pct = round(count / self._collected * 100)
                    text = f"{count}  ·  {pct}%"
                else:
                    text = str(count)
                p.setPen(self.palette().windowText().color())
                p.drawText(
                    QRectF(
                        count_left - 4, slot_y,
                        self._COUNT_WIDTH + 4, slot),
                    int(Qt.AlignmentFlag.AlignVCenter
                        | Qt.AlignmentFlag.AlignRight),
                    text,
                )
        finally:
            p.end()


# Per-classification colour palette for the donut + legend. Stable
# across renders so the same classification always reads as the same
# colour. Anything outside this dict falls back to a neutral slate
# (defensive — future Scenario values just slot in here when added).
_CLASSIFICATION_COLORS = {
    "macro":               "#EC4899",
    "wildlife":            "#F59E0B",
    "birds":               "#06B6D4",
    "portrait":            "#8B5CF6",
    "selfie":              "#A78BFA",
    "landscape":           "#10B981",
    "night_long_exposure": "#4F46E5",
    "sports":              "#DC2626",
    "street":              "#84CC16",
    "travel":              "#F97316",
    "family":              "#FBBF24",
    "astro":               "#1E40AF",
    "general":             "#6B7280",
    "urban_street":        "#84CC16",  # alias spec/64 §3.4 vocabulary
}


def _classification_color(key: str) -> str:
    return _CLASSIFICATION_COLORS.get(key, "#9CA3AF")


def _classification_label(key: str) -> str:
    """Display label for a classification — Title Case, underscores → spaces."""
    return key.replace("_", " ").title()


class _ClassificationDonut(QWidget):
    """The closed-tile classification donut (spec/64 §2.4, Nelson
    2026-06-13 v3).

    Slices are proportional to each classification's count across the
    event's photos; labels live in the sibling legend, not on the
    donut itself. A donut with no data renders an empty ring (no
    spurious "0 photos" message — the legend handles the absence).
    """

    _OUTER_FRAC = 0.92    # of min(w, h)
    _INNER_FRAC = 0.55    # hole diameter as a fraction of outer

    def __init__(
        self,
        counts,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("EventCardClosedBodyDonut")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(60, 60)
        # Sorted-descending so the dominant classification leads the
        # arc (12 o'clock); ties break alphabetically for stability.
        self._slices = sorted(
            ((k, int(v)) for k, v in (counts or {}).items()
             if int(v) > 0),
            key=lambda kv: (-kv[1], kv[0]),
        )
        self._total = sum(v for _, v in self._slices)

    def paintEvent(self, _event) -> None:  # noqa: N802
        from PyQt6.QtGui import QBrush, QColor, QPainter, QPen
        from PyQt6.QtCore import QRectF

        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            w, h = self.width(), self.height()
            size = min(w, h)
            outer_d = size * self._OUTER_FRAC
            inner_d = outer_d * self._INNER_FRAC
            cx = w / 2
            cy = h / 2
            outer_rect = QRectF(
                cx - outer_d / 2, cy - outer_d / 2, outer_d, outer_d)
            inner_rect = QRectF(
                cx - inner_d / 2, cy - inner_d / 2, inner_d, inner_d)
            # Track ring (faint backdrop — shows even on no-data).
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(0, 0, 0, 24)))
            p.drawEllipse(outer_rect)
            # Pie slices, starting at 12 o'clock and walking clockwise
            # (Qt angles are 1/16ths of a degree, 0 = 3 o'clock,
            # positive = counter-clockwise — so we flip + offset).
            if self._total > 0:
                start_angle = 90 * 16
                for key, count in self._slices:
                    span = -int(round(count / self._total * 360 * 16))
                    p.setBrush(QBrush(QColor(_classification_color(key))))
                    p.drawPie(outer_rect, start_angle, span)
                    start_angle += span
            # Punch the hole — paint over the centre with the parent's
            # background so the ring becomes a donut. Using a transparent
            # fill won't work (QPainter doesn't subtract); use the
            # widget's actual background, which inherits from the card.
            p.setBrush(self.palette().window())
            p.drawEllipse(inner_rect)
        finally:
            p.end()


class _ClassificationLegend(QWidget):
    """The donut's right-side legend (spec/64 §2.4, Nelson 2026-06-13 v3).

    One row per classification with at least one photo: a coloured
    swatch + the display label + the count. Sorted descending by
    count to match the donut's arc order. Caps at six rows so the
    legend never crowds the body (any tail folds into "+ N more").
    """

    _MAX_ROWS = 6
    _SWATCH = 10
    _ROW_GAP = 4

    def __init__(
        self,
        counts,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("EventCardClosedBodyLegend")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(80, 60)
        self._slices = sorted(
            ((k, int(v)) for k, v in (counts or {}).items()
             if int(v) > 0),
            key=lambda kv: (-kv[1], kv[0]),
        )

    def paintEvent(self, _event) -> None:  # noqa: N802
        from PyQt6.QtGui import QBrush, QColor, QFont, QPainter
        from PyQt6.QtCore import QRectF

        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            w, h = self.width(), self.height()
            visible = list(self._slices[:self._MAX_ROWS])
            hidden = len(self._slices) - len(visible)
            rows = list(visible)
            if hidden > 0:
                rows.append(("__more__", hidden))
            if not rows:
                p.setPen(self.palette().mid().color())
                font = QFont(p.font())
                font.setPixelSize(11)
                font.setItalic(True)
                p.setFont(font)
                p.drawText(
                    self.rect(),
                    int(Qt.AlignmentFlag.AlignCenter),
                    tr("no classifications yet"),
                )
                return
            slot = max(self._SWATCH + 2,
                       (h - (len(rows) - 1) * self._ROW_GAP) // len(rows))
            slot = min(slot, 22)
            total_h = len(rows) * slot + (len(rows) - 1) * self._ROW_GAP
            top = max(0, (h - total_h) // 2)
            font = QFont(p.font())
            font.setPixelSize(11)
            p.setFont(font)
            for i, (key, count) in enumerate(rows):
                y = top + i * (slot + self._ROW_GAP)
                # Colour swatch (a tiny rounded square).
                swatch_y = y + (slot - self._SWATCH) // 2
                if key == "__more__":
                    color = QColor("#9CA3AF")
                    label = tr("+ {n} more").replace("{n}", str(count))
                    count_text = ""
                else:
                    color = QColor(_classification_color(key))
                    label = tr(_classification_label(key))
                    count_text = str(count)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(color))
                p.drawRoundedRect(
                    QRectF(0, swatch_y, self._SWATCH, self._SWATCH),
                    2, 2)
                # Label (left of count).
                count_text_w = (
                    self.fontMetrics().horizontalAdvance(count_text)
                    if count_text else 0)
                label_left = self._SWATCH + 6
                label_right = w - (count_text_w + 6 if count_text else 0)
                p.setPen(self.palette().windowText().color())
                p.drawText(
                    QRectF(label_left, y, max(20, label_right - label_left),
                           slot),
                    int(Qt.AlignmentFlag.AlignVCenter
                        | Qt.AlignmentFlag.AlignLeft),
                    label,
                )
                if count_text:
                    p.drawText(
                        QRectF(w - count_text_w - 4, y,
                               count_text_w + 4, slot),
                        int(Qt.AlignmentFlag.AlignVCenter
                            | Qt.AlignmentFlag.AlignRight),
                        count_text,
                    )
        finally:
            p.end()


# Re-export both as a single closed-body builder so existing callers /
# tests can `from event_card import _ClosedBodyContent` and find a
# widget regardless of which variant it composes internally.
class _ClosedBodyContent(QWidget):
    """Closed-tile body (spec/64 §2.4). The bar chart on the left, the
    photo carousel on the right; both clickable (clicks bubble to the
    surrounding zone → routes to the event's Cuts list)."""

    def __init__(
        self,
        *,
        collected: int = 0,
        picked: int = 0,
        edited: int = 0,
        exported: int = 0,
        classification_counts=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("EventCardClosedBody")
        row = QHBoxLayout(self)
        # Three columns — chart · donut · legend — all filling the
        # right-zone slot the heatmap occupies on open tiles. Equal
        # stretch by default; the donut prefers a 1:1 aspect via its
        # own paintEvent so the slices stay round at any width.
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self._chart = _PhaseStatsChart(
            collected=collected, picked=picked,
            edited=edited, exported=exported,
        )
        row.addWidget(self._chart, stretch=4)
        counts = dict(classification_counts or {})
        self._donut = _ClassificationDonut(counts)
        row.addWidget(self._donut, stretch=2)
        self._legend = _ClassificationLegend(counts)
        row.addWidget(self._legend, stretch=3)


class EventCard(QFrame):
    """One event as a large dashboard card with 3 independently-clickable zones
    (Nelson 2026-06-06):

    * **Top zone** — tags + type badge + title + state/days badge → emits
      :attr:`title_clicked`. Opens the Event Info dialog.
    * **Left zone** — dates + TZ → emits :attr:`info_clicked`. Opens the plan
      editor.
    * **Right zone** — phase × day heatmap → emits :attr:`heatmap_clicked`.
      Opens the phases dashboard.

    Each zone hover-tints independently so the user sees the active click
    target. Backward-compat ``clicked`` signal kept as an alias for
    :attr:`heatmap_clicked` (the most "open-the-event"-like of the three) — old
    callers that connect to ``clicked`` still get the activity dashboard."""

    title_clicked = pyqtSignal(str)    # event_id → Event Header dialog
    info_clicked = pyqtSignal(str)     # event_id → Event Days Table dialog
    heatmap_clicked = pyqtSignal(str)  # event_id → Phases dashboard (open) / Cuts list (closed)
    status_badge_clicked = pyqtSignal(str)  # event_id → toggle Open↔Closed (spec/64 §2.3)
    clicked = pyqtSignal(str)          # back-compat alias for heatmap_clicked

    def __init__(self, data: EventCardData, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data = data
        self.setObjectName("EventCardClosed" if data.is_closed else "EventCard")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Fixed height in BOTH states (Nelson 2026-06-13 second eyeball:
        # the closed-tile body's Expanding chart + carousel had no upper
        # bound and pushed the closed card taller than the open one).
        # 180 was the open-tile minimum; promoting it to a fix locks
        # the two states to the same vertical extent. The legacy 220
        # closed bias dated from a recap surface that never landed.
        self.setFixedHeight(180)
        self.setToolTip(self._format_tooltip())
        self._build_ui()
        # Back-compat: anything connected to .clicked still gets the activity
        # dashboard (the most "open the event" of the three).
        self.heatmap_clicked.connect(self.clicked.emit)

    def _build_ui(self) -> None:
        data = self._data
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Top zone — tags + title + badges → Event Info dialog ──────
        top_zone = _ClickableZone("EventCardTopZone", data.event_id)
        top_zone.clicked.connect(self.title_clicked.emit)
        top_layout = QVBoxLayout(top_zone)
        top_layout.setContentsMargins(16, 12, 16, 10)
        top_layout.setSpacing(8)

        # Tags chip row (moved from bottom — they're Event-Info data).
        if data.tags:
            chip_host = QWidget()
            chip_layout = FlowLayout(chip_host, spacing=4)
            chip_layout.setContentsMargins(0, 0, 0, 0)
            visible = data.tags[: self._MAX_VISIBLE_TAGS]
            overflow = data.tags[self._MAX_VISIBLE_TAGS:]
            for tag in visible:
                chip = QLabel(f"#{tag}")
                chip.setObjectName("EventCardTagChip")
                chip.setToolTip(tag)
                chip_layout.addWidget(chip)
            if overflow:
                more = QLabel(f"+{len(overflow)} more")
                more.setObjectName("EventCardTagChipOverflow")
                more.setToolTip(", ".join(f"#{t}" for t in overflow))
                chip_layout.addWidget(more)
            top_layout.addWidget(chip_host)

        # Title row — type badge + title + Header / Status / days badges.
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        type_badge = QLabel(self._format_type_badge_text())
        type_badge.setObjectName("EventCardTypeBadge")
        type_badge.setProperty("type", data.event_type or "unclassified")
        type_badge.setToolTip(self._format_type_badge_tooltip())
        title_row.addWidget(type_badge)
        title = QLabel(self._format_title())
        title.setObjectName("EventCardTitle")
        title_row.addWidget(title, stretch=1)
        # spec/64 §2.3 — the clickable Open / Closed status badge. Replaces
        # the legacy read-only ``EventCardClosedBadge`` "✓ Closed" pill: now
        # every tile carries a badge and a click toggles state instantly.
        self._status_badge = _StatusBadge(data.event_id, data.is_closed)
        self._status_badge.clicked.connect(self.status_badge_clicked.emit)
        title_row.addWidget(self._status_badge)
        days_badge = QLabel(self._format_days_badge())
        days_badge.setObjectName("EventCardDaysBadge")
        days_badge.setAlignment(Qt.AlignmentFlag.AlignRight)
        title_row.addWidget(days_badge)
        top_layout.addLayout(title_row)

        # Subtype line (single line elide; hidden when empty).
        if data.event_subtype:
            subtype_lbl = QLabel(data.event_subtype)
            subtype_lbl.setObjectName("EventCardSubtype")
            subtype_lbl.setToolTip(data.event_subtype)
            top_layout.addWidget(subtype_lbl)

        outer.addWidget(top_zone)

        # ── Body: left zone (info) + right zone (heatmap) ─────────────
        body_row = QHBoxLayout()
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(0)

        # Left zone — dates + TZ → Plan editor.
        left_zone = _ClickableZone("EventCardLeftZone", data.event_id)
        left_zone.clicked.connect(self.info_clicked.emit)
        left = QVBoxLayout(left_zone)
        left.setSpacing(2)
        left.setContentsMargins(16, 8, 16, 14)
        from_label = QLabel(tr("FROM"))
        from_label.setObjectName("EventCardFromTo")
        left.addWidget(from_label)
        start_str = QLabel(self._format_date(data.start_date))
        start_str.setObjectName("EventCardDate")
        left.addWidget(start_str)
        left.addSpacing(6)
        tz_lbl = QLabel(data.tz_display)
        tz_lbl.setObjectName("EventCardTz")
        tz_lbl.setWordWrap(True)
        left.addWidget(tz_lbl)
        left.addStretch(1)
        to_label = QLabel(tr("TO"))
        to_label.setObjectName("EventCardFromTo")
        left.addWidget(to_label)
        end_str = QLabel(self._format_date(data.end_date))
        end_str.setObjectName("EventCardDate")
        left.addWidget(end_str)
        left_zone.setFixedWidth(LEFT_COL_WIDTH + 32)  # +32 = left+right padding
        body_row.addWidget(left_zone)

        # Right zone — heatmap (open) / Cuts hint (closed) → routes via
        # heatmap_clicked. On open events the host wires it to the activity
        # dashboard; on closed events to the Cuts list (spec/64 §2.4).
        right_zone = _ClickableZone("EventCardRightZone", data.event_id)
        right_zone.clicked.connect(self.heatmap_clicked.emit)
        right = QVBoxLayout(right_zone)
        right.setContentsMargins(8, 8, 16, 14)
        if data.is_closed:
            # spec/64 §2.4 (Nelson 2026-06-13 v3): three columns — phase
            # bar chart · classification donut · legend. Clicks anywhere
            # in the body bubble to the surrounding right zone → routes
            # to the event's Cuts list via heatmap_clicked.
            self._closed_body = _ClosedBodyContent(
                collected=data.collected_count,
                picked=data.picked_count,
                edited=data.edited_count,
                exported=data.exported_count,
                classification_counts=dict(data.classification_counts),
            )
            right.addWidget(self._closed_body, stretch=1)
        elif data.total_days <= 0:
            placeholder = QLabel(tr("No plan yet — click to set one up."))
            placeholder.setObjectName("EventCardEmpty")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setWordWrap(True)
            right.addWidget(placeholder, stretch=1)
        else:
            self._grid = EventCardGrid(
                data.status_by_phase, data.total_days, event_type=data.event_type,
            )
            right.addWidget(self._grid, stretch=1)
        body_row.addWidget(right_zone, stretch=1)

        outer.addLayout(body_row)

    _MAX_VISIBLE_TAGS = 4

    # ── Formatters ─────────────────────────────────────────────────

    def _format_title(self) -> str:
        name = self._data.name or tr("(unnamed event)")
        if self._data.start_date is not None:
            return f"{self._data.start_date.year} — {name}"
        return name

    def _format_type_badge_text(self) -> str:
        et = self._data.event_type or event_classification.EVENT_TYPE_UNCLASSIFIED
        return tr(event_classification.display_label_for_type(et))

    def _format_type_badge_tooltip(self) -> str:
        et = self._data.event_type or event_classification.EVENT_TYPE_UNCLASSIFIED
        return tr("Event type: {label}").replace(
            "{label}", event_classification.display_label_for_type(et),
        )

    _TOOLTIP_DESCRIPTION_CAP = 280

    def _format_tooltip(self) -> str:
        desc = (self._data.description or "").strip()
        if not desc:
            return tr("Open this event")
        if len(desc) > self._TOOLTIP_DESCRIPTION_CAP:
            desc = desc[: self._TOOLTIP_DESCRIPTION_CAP].rstrip() + "…"
        return desc

    def _format_days_badge(self) -> str:
        n = self._data.total_days
        if n == 0:
            return tr("no plan yet")
        if n == 1:
            return tr("1 day")
        return tr("{n} days").replace("{n}", str(n))

    def _format_date(self, d: Optional[date]) -> str:
        if d is None:
            return tr("(no date)")
        return d.strftime("%a %d %b %Y")
